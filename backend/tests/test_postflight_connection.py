"""Real normalized CAB fixture, fake incoming offers and MockTransport only."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

import app.airport_cab_evidence as evidence
import app.airport_cab_planning as planning
import app.live_travel as live_travel
import app.solver as solver
import app.store as store_module
from app.after_flight_inputs import AfterFlightTransferInput
from app.airport_catalog import lookup_airport
from app.airport_cab_planning import AirportCabPlanning, AirportCabPlanningRequest, append_airport_cab_options, cab_context
from app.inventory_lifecycle import protected_inventory
from app.models import Airport, PassengerGroup, RecoveryIncident, Strategy
from app.postflight_connection import derive_postflight_pickup
from app.providers.base import ProviderError
from app.providers.bucharest_cab import BucharestCabProvider, LOCAL_ZONE
from app.store import DomainError, Store
from test_bucharest_cab import bootstrap, payload

NOW = datetime(2030, 1, 4, tzinfo=timezone.utc)


def airport(code):
    ref = lookup_airport(code)
    return Airport(id=code, code=code, name=ref["name"], lat=ref["lat"], lon=ref["lon"])


@pytest_asyncio.fixture
async def setup(tmp_path, monkeypatch):
    clock = [NOW]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)
    for module in (solver, live_travel, evidence): monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setattr(store_module, "utcnow", lambda: clock[0])
    store = Store(str(tmp_path / "postflight.sqlite3"), operation_mode="live")
    def seed(world):
        world.epoch = NOW.isoformat(); world.budget = 1000
        world.airports = [airport("SBZ"), airport("CRA")]
        world.groups = [PassengerGroup(id="G1", name="Offline party", size=2, adult_count=2, origin="SBZ", destination="CRA",
            ready_at=0, deadline=1200, budget=500, source="manifest_import", token="offline", original_flight_id="RO321", incident_id="CASE")]
        world.resources = []; world.offers = []; world.reservations = []
        world.incidents = [RecoveryIncident(id="CASE", requested_flight_number="RO321", date_utc="2030-01-04", group_ids=["G1"],
            flight_ref={"lookup_id":"offline-lookup","selection_id":"offline-selection"}, created_at=NOW.isoformat(), kind="destination_unavailable", description="Offline arrival ban", unavailable_airport_code="CRA")]
    store.mutate("seed", {}, store.read().version, seed)
    value = AfterFlightTransferInput(group_id="G1", via_airport="OTP", destination_airport="CRA", pickup_window_start=NOW+timedelta(hours=9),
        pickup_window_end=NOW+timedelta(hours=12), airport_processing_minutes=60, suitcases=2, handbags=2)
    source_quote = {"id":"incoming-offer", "provider":"nuitee", "provider_offer_id":"incoming-offer", "name":"Mock sourced incoming flight",
        "origin":"SBZ", "destination":"OTP", "departure_at":(NOW+timedelta(hours=7)).isoformat(), "arrival_at":(NOW+timedelta(hours=9)).isoformat(),
        "quoted_at":NOW.isoformat(), "expires_at":(NOW+timedelta(hours=1)).isoformat(), "total_price":"100.00", "currency":"EUR", "passengers":2,
        "mode":"live", "accessibility_confirmed":False, "segments":[{"origin":"SBZ","destination":"OTP", "departure_at":(NOW+timedelta(hours=7)).isoformat(), "arrival_at":(NOW+timedelta(hours=9)).isoformat()}]}
    projected = store.read().model_copy(deep=True)
    result = live_travel.append_travel_quote(projected, source_quote, {"origin_code":"SBZ","destination_code":"OTP"}, projected.groups[0], "flight", stage=True)
    incoming = next(item for item in projected.resources if item.id == result["resource_ids"][0])
    calls, callbacks = [], {}
    async def http(outbound):
        calls.append(outbound)
        if outbound.url.path == "/en/book":
            if "bootstrap" in callbacks: callbacks["bootstrap"]()
            return httpx.Response(200, text=bootstrap(), headers={"content-type":"text/html"})
        if callbacks.get("timeout"): raise httpx.ReadTimeout("private upstream", request=outbound)
        if callbacks.get("http_status"): return httpx.Response(callbacks["http_status"], text="private upstream")
        local = datetime.strptime(outbound.url.params["pickupDateTime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_ZONE)
        data = payload(); data.update(datetime_param=local.strftime("%Y-%m-%d %H:%M:%S"), datetime=local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
        if "vehicles" in callbacks: callbacks["vehicles"]()
        return httpx.Response(200, json=data)
    client = httpx.AsyncClient(transport=httpx.MockTransport(http))
    provider = BucharestCabProvider(SimpleNamespace(request_timeout_seconds=1), client=client, now=lambda:clock[0])
    service = AirportCabPlanning(SimpleNamespace(), store, provider=provider, now=lambda:clock[0])
    def request(key="postflight-one", transfer=None, flight=None):
        transfer, flight = transfer or value, flight or incoming
        return AirportCabPlanningRequest(group_id="G1", destination_airport="CRA", pickup_at=derive_postflight_pickup(flight,transfer),
            suitcases=transfer.suitcases, handbags=transfer.handbags, expected_version=store.read().version, idempotency_key=key)
    yield SimpleNamespace(store=store, service=service, provider=provider, value=value, incoming=incoming,
        projected=projected, request=request, calls=calls, callbacks=callbacks, clock=clock)
    await service.aclose(); await client.aclose()


async def quote_and_stage(env):
    req = env.request()
    context, _ = cab_context(env.store.read(), req, env.provider, now=env.clock[0], incoming_flight=env.incoming, after_flight_transfer=env.value)
    response = await env.service.quote(req, incoming_flight=env.incoming, after_flight_transfer=env.value, propagate_errors=True)
    world = env.projected.model_copy(deep=True)
    added = append_airport_cab_options(world, context, response, [response["transfer"]["options"][0]["id"]], now=env.clock[0], stage=True)
    vehicle = next(item for item in world.resources if item.id == added["resource_ids"][0])
    return world, vehicle, response, context


@pytest.mark.asyncio
async def test_two_get_quote_and_complete_exact_connection_keep_passengers_at_reported_origin(setup):
    env = setup; before = env.store.read().model_dump(mode="json", exclude={"sim_time"})
    world, vehicle, response, context = await quote_and_stage(env)
    assert len(env.calls) == 2 and response["state"] == "ready" and response["transfer"]["http_request_count"] == 2
    assert response["transfer"]["request"]["baggage_supplier_scoped"] is False
    proof = vehicle.provenance["postflight_connection"]
    assert proof == context["postflight_connection"]
    assert proof["source_arrival_at"] == (NOW+timedelta(hours=9)).isoformat()
    assert proof["derived_pickup_at"] == (NOW+timedelta(hours=10)).isoformat()
    assert proof["arrival_basis"] == "supplier_schedule_not_observed"
    assert vehicle.quote_total_cost == 188 and vehicle.valid_until is None and vehicle.booking_status == "unbooked"
    assert evidence.valid_airport_cab_resource(world, vehicle, world.groups[0], now=NOW)
    plan = solver.build_candidate(world, Strategy())
    assert plan.validation["valid"] and plan.recovered_passengers == 2 and plan.total_cost == 288
    assert [leg.kind for leg in plan.allocations[0].itinerary.legs] == ["flight", "bus"]
    assert plan.allocations[0].itinerary.legs[1].postflight_connection == proof
    assert world.groups[0].origin == "SBZ" and world.groups[0].ready_at == 0
    assert env.store.read().model_dump(mode="json", exclude={"sim_time"}) == before
    replay = await env.service.quote(env.request(), incoming_flight=env.incoming, after_flight_transfer=env.value, propagate_errors=True)
    assert replay["replayed"] and len(env.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["wrong_flight", "arrival", "processing", "pickup", "scope", "bags", "leg_missing"])
async def test_independent_solver_rejects_modified_connection_or_leg_evidence(setup, change):
    world, vehicle, _, _ = await quote_and_stage(setup)
    plan = solver.build_candidate(world, Strategy())
    proof = vehicle.provenance["postflight_connection"]
    if change == "wrong_flight": proof["incoming_resource_id"] = "OTHER"
    elif change == "arrival": proof["source_arrival_at"] = (NOW+timedelta(hours=8)).isoformat()
    elif change == "processing": proof["airport_processing_minutes"] = 59
    elif change == "pickup": proof["derived_pickup_at"] = (NOW+timedelta(hours=11)).isoformat()
    elif change == "scope": world.groups[0].ready_at = 1
    elif change == "bags": vehicle.provenance["airport_cab_quote"]["request"]["suitcases"] = 7
    else: plan.allocations[0].itinerary.legs[1].postflight_connection = None
    assert not solver.validate_plan(world, plan)["valid"]


@pytest.mark.asyncio
async def test_bound_flight_must_be_immediately_previous_and_is_retained_for_evidence(setup):
    world, vehicle, _, _ = await quote_and_stage(setup)
    assert solver.connection_minutes(setup.incoming, vehicle) == 60
    assert solver.connection_minutes(None, vehicle) is None
    other = setup.incoming.model_copy(update={"id":"different-flight"})
    assert solver.connection_minutes(other, vehicle) is None
    assert not solver.resource_feasible(world, vehicle, world.groups[0], "OTP", vehicle.departure, set())
    protected, _ = protected_inventory(world, removing_resource_ids={setup.incoming.id})
    assert setup.incoming.id in protected


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["retired", "tampered_price", "expired", "case_arrival_ban", "weather_departure_ban"])
async def test_exact_incoming_revision_is_rechecked_before_any_source_dispatch(setup, change):
    env = setup
    def change_world(world):
        if change == "retired":
            world.resources.append(env.incoming.model_copy(update={"available":False}))
        elif change == "case_arrival_ban": world.incidents[0].unavailable_airport_code = "OTP"
        else:
            world.airports[0].operational_status = "closed"
    if change == "tampered_price": env.incoming.provenance["total_price"] = "1.00"
    elif change == "expired": env.clock[0] += timedelta(seconds=121)
    else: env.store.mutate("invalidate", {}, env.store.read().version, change_world)
    with pytest.raises(DomainError):
        await env.service.quote(env.request(), incoming_flight=env.incoming, after_flight_transfer=env.value, propagate_errors=True)
    assert not env.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["group", "flight_freshness", "airport"])
async def test_context_change_after_bootstrap_prevents_vehicle_request_and_fake_runner_dispatch(setup, change):
    env = setup; phases = []
    def changed():
        if change == "flight_freshness": env.clock[0] += timedelta(seconds=121)
        else:
            def mutate(world):
                if change == "group": world.groups[0].ready_at += 1
                else: world.airports[0].operational_status = "closed"
            env.store.mutate("mid-source", {}, env.store.read().version, mutate)
    env.callbacks["bootstrap"] = changed
    async def runner(phase, operation):
        phases.append(phase)
        return await operation()
    with pytest.raises(DomainError):
        await env.service.quote(env.request(), incoming_flight=env.incoming, after_flight_transfer=env.value, request_runner=runner, propagate_errors=True)
    assert len(env.calls) == 1 and phases == ["bootstrap"]


@pytest.mark.asyncio
async def test_unknown_vehicle_timeout_remains_unknown_and_never_redispatches(setup):
    env = setup; env.callbacks["timeout"] = True
    with pytest.raises(ProviderError) as error:
        await env.service.quote(env.request(), incoming_flight=env.incoming, after_flight_transfer=env.value, propagate_errors=True)
    assert error.value.code == "timeout" and len(env.calls) == 2
    with pytest.raises(DomainError) as repeated:
        await env.service.quote(env.request(), incoming_flight=env.incoming, after_flight_transfer=env.value, propagate_errors=True)
    assert repeated.value.details["unknown_outcome"] and len(env.calls) == 2


@pytest.mark.asyncio
async def test_two_incoming_revisions_with_identical_pickup_have_separate_receipts(setup):
    env = setup
    first = await env.service.quote(env.request(), incoming_flight=env.incoming, after_flight_transfer=env.value)
    second = env.incoming.model_copy(deep=True)
    second.provenance["provider_offer_id"] = "second-offer"
    second.external_offer_id = "second-offer"
    second.id = "Q-"+Store.fingerprint({"group_id":"G1","kind":"flight","quote":second.provenance})[:24].upper()
    another = await env.service.quote(env.request("postflight-second",flight=second), incoming_flight=second, after_flight_transfer=env.value)
    assert len(env.calls) == 4 and first["quote_id"] != another["quote_id"]
    assert first["quote_sequence"] < another["quote_sequence"]
    assert first["transfer"]["request"]["pickup_at"] == another["transfer"]["request"]["pickup_at"]
    with pytest.raises(DomainError):
        await env.service.quote(env.request(), incoming_flight=second, after_flight_transfer=env.value)
    assert len(env.calls) == 4


@pytest.mark.asyncio
async def test_source_advance_rule_never_moves_the_derived_pickup(setup):
    env = setup
    early = env.incoming.model_copy(deep=True)
    early.provenance["departure_at"] = (NOW+timedelta(hours=5)).isoformat()
    early.provenance["arrival_at"] = (NOW+timedelta(hours=7)).isoformat()
    early.provenance["segments"][0].update(departure_at=early.provenance["departure_at"],arrival_at=early.provenance["arrival_at"])
    early.departure, early.arrival = 300, 420
    early.id = "Q-"+Store.fingerprint({"group_id":"G1","kind":"flight","quote":early.provenance})[:24].upper()
    supplied = env.value.model_copy(update={"pickup_window_start":NOW+timedelta(hours=8)})
    assert derive_postflight_pickup(early,supplied) == NOW+timedelta(hours=8)
    with pytest.raises(ProviderError) as error:
        await env.service.quote(env.request(transfer=supplied,flight=early), incoming_flight=early, after_flight_transfer=supplied, propagate_errors=True)
    assert error.value.code == "pickup_too_soon" and len(env.calls) == 2
    assert env.calls[1].url.params["pickupDateTime"] == "2030-01-04 10:00:00"


def test_processing_uses_exact_source_seconds_then_ceil_and_never_exceeds_window():
    incoming = SimpleNamespace(provenance={"arrival_at":"2030-01-04T08:00:01+00:00"})
    supplied = AfterFlightTransferInput(group_id="G1",via_airport="OTP",destination_airport="CRA",
        pickup_window_start="2030-01-04T08:00:00+00:00",pickup_window_end="2030-01-04T09:01:00+00:00",
        airport_processing_minutes=60,suitcases=0,handbags=0)
    assert derive_postflight_pickup(incoming,supplied) == NOW+timedelta(hours=9,minutes=1)
    too_early = supplied.model_copy(update={"pickup_window_end":NOW+timedelta(hours=9)})
    with pytest.raises(DomainError): derive_postflight_pickup(incoming,too_early)


@pytest.mark.asyncio
async def test_newer_flight_source_observation_between_http_phases_stops_old_connection(setup):
    env = setup
    def revise():
        env.clock[0] += timedelta(seconds=1)
        newer = env.incoming.model_copy(deep=True)
        newer.provenance["quoted_at"] = env.clock[0].isoformat()
        newer.id = "Q-"+Store.fingerprint({"group_id":"G1","kind":"flight","quote":newer.provenance})[:24].upper()
        env.store.mutate("new-flight-observation", {}, env.store.read().version, lambda world: world.resources.append(newer))
    env.callbacks["bootstrap"] = revise
    with pytest.raises(DomainError):
        await env.service.quote(env.request(), incoming_flight=env.incoming, after_flight_transfer=env.value, propagate_errors=True)
    assert len(env.calls) == 1


@pytest.mark.asyncio
async def test_receipt_window_is_capped_by_exact_incoming_source_freshness(setup):
    env = setup; env.clock[0] += timedelta(seconds=100)
    response = await env.service.quote(env.request(), incoming_flight=env.incoming, after_flight_transfer=env.value)
    assert response["state"] == "ready"
    assert datetime.fromisoformat(response["usable_until"]) == NOW+timedelta(seconds=120)
    env.clock[0] += timedelta(seconds=21)
    with pytest.raises(DomainError):
        await env.service.quote(env.request(), incoming_flight=env.incoming, after_flight_transfer=env.value)
    assert len(env.calls) == 2


@pytest.mark.asyncio
async def test_source_receipt_cannot_import_standalone_or_resurrect_older_category(setup):
    env = setup
    first = await env.service.quote(env.request(), incoming_flight=env.incoming, after_flight_transfer=env.value)
    context, _ = cab_context(env.store.read(),env.request(),env.provider,now=NOW,incoming_flight=env.incoming,after_flight_transfer=env.value)
    old_option = first["transfer"]["options"][0]["id"]
    world = env.store.read().model_copy(deep=True)
    with pytest.raises(DomainError):
        append_airport_cab_options(world,context,first,[old_option],now=NOW)
    assert not world.resources and world.groups[0].origin == "SBZ"
    second = await env.service.quote(env.request("newer-receipt"),incoming_flight=env.incoming,after_flight_transfer=env.value)
    world = env.projected.model_copy(deep=True)
    append_airport_cab_options(world,context,second,[second["transfer"]["options"][0]["id"]],now=NOW)
    before = world.model_dump(mode="json")
    with pytest.raises(DomainError,match="newer source revision"):
        append_airport_cab_options(world,context,first,[old_option],now=NOW)
    assert world.model_dump(mode="json") == before


@pytest.mark.asyncio
async def test_http_507_marks_source_receipt_unknown_and_prevents_redispatch(setup):
    env = setup; env.callbacks["http_status"] = 507
    with pytest.raises(ProviderError) as error:
        await env.service.quote(env.request(), incoming_flight=env.incoming, after_flight_transfer=env.value, propagate_errors=True)
    assert error.value.code == "http_507" and len(env.calls) == 2
    with env.store.connect() as conn:
        assert conn.execute("SELECT status,http_requests FROM airport_cab_receipts").fetchone() == ("unknown", 2)
    with pytest.raises(DomainError) as replay:
        await env.service.quote(env.request(), incoming_flight=env.incoming, after_flight_transfer=env.value, propagate_errors=True)
    assert replay.value.details["unknown_outcome"] and len(env.calls) == 2
