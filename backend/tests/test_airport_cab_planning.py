"""Offline service/API journey using the actual normalized consumer quote.

MockTransport is the only HTTP transport. No application server, source request,
booking, model or paid routing call is started by these tests.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

import app.store as store_module
from app.airport_catalog import lookup_airport
from app.airport_cab_planning import AirportCabImport, AirportCabPlanning, AirportCabPlanningRequest, append_airport_cab_options
from app.config import Settings
from app.inventory_lifecycle import resource_history
from app.main import create_app
from app.models import Airport, Itinerary, Offer, PassengerGroup, Resource
from app.providers.bucharest_cab import BucharestCabProvider, LOCAL_ZONE, SOURCE
from app.providers.base import ProviderError
from app.store import DomainError, Store

NOW = datetime(2030, 1, 4, tzinfo=timezone.utc)
PICKUP = NOW + timedelta(hours=10)
FIXTURE = Path(__file__).parent / "fixtures/bucharest_cab_otp_cra.json"
TOKEN = "b" * 32


def airport(code):
    record = lookup_airport(code)
    return Airport(id=code, code=code, name=record["name"], lat=record["lat"], lon=record["lon"], operational_status="open")


def initialize(store):
    def populate(world):
        world.scenario_id = "CAB-SCOPED-TEST"
        world.epoch = NOW.isoformat()
        world.sim_time = 0
        world.airports = [airport("OTP"), airport("LHR")]
        world.groups = [PassengerGroup(id="G1", name="Reported adult passengers", size=2, adult_count=2,
            origin="OTP", destination="LHR", ready_at=600, deadline=1200, budget=500,
            original_flight_id="ARRIVED", token="offline-1", source="operator_input"),
            PassengerGroup(id="G2", name="Unrelated party", size=1, adult_count=1,
            origin="LHR", destination="OTP", ready_at=600, deadline=1200, budget=500,
            original_flight_id="OTHER", token="offline-2", source="operator_input")]
        world.resources = []
        world.offers = []
        world.reservations = []
        world.budget = 1000
    store.mutate("initialize-cab", {}, store.read().version, populate)


def mutate(store, action, key=None):
    version = store.read().version
    return store.mutate(key or "test-change-" + str(version), {"version": version}, version, action)


def confirm_offer(world):
    world.offers.append(Offer(id="EXTERNALLY-CONFIRMED", group_id="G1",
        itinerary=Itinerary(legs=[], arrival=800, total_cost=0), status="confirmed",
        expires_at=(NOW + timedelta(days=1)).isoformat(), created_version=world.version))


def pending_arrival(world):
    world.resources.append(Resource(id="ARRIVED", kind="flight", name="Arrival now requires diversion",
        origin="LHR", destination="OTP", departure=0, arrival=100, capacity=2, cost_per_person=0,
        source="Operator flight status", status="needs_diversion"))


class SourceHTTP:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self.after_bootstrap = None
        self.after_quote = None
        self.gate = None
        self.entered = asyncio.Event()
        self.error_status = None
        self.error_payload = None
        self.payload_mutation = None

    async def __call__(self, outgoing):
        self.calls.append(outgoing)
        if self.gate is not None:
            self.entered.set()
            await self.gate.wait()
        if self.error_status is not None:
            return httpx.Response(self.error_status, text="upstream-private-token")
        if outgoing.url.path == "/en/book":
            if self.after_bootstrap:
                self.after_bootstrap()
            config = {"csrfToken": TOKEN, "ajax_get_suitable_vehicles_url": "https://www.bucharest.cab/index.php?option=com_tulipbooking&format=json&task=ajax.getSuitableVehicles&format=json"}
            return httpx.Response(200, text="<script>var tulipBookingScriptData=" + json.dumps(config) + ";</script>", headers={"content-type": "text/html"})
        assert outgoing.url.path == "/en/" and outgoing.url.params["task"] == "ajax.getSuitableVehicles"
        payload = json.loads(FIXTURE.read_text())
        local = datetime.strptime(outgoing.url.params["pickupDateTime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_ZONE)
        payload.update(datetime_param=local.strftime("%Y-%m-%d %H:%M:%S"), datetime=local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
        if self.payload_mutation:
            self.payload_mutation(payload)
        if self.after_quote:
            self.after_quote()
        return httpx.Response(200, json=self.error_payload if self.error_payload is not None else payload)


@pytest_asyncio.fixture
async def setup(tmp_path, monkeypatch):
    clock = [NOW]
    monkeypatch.setattr(store_module, "utcnow", lambda: clock[0])
    store = Store(str(tmp_path / "cab.sqlite3"), operation_mode="live")
    initialize(store)
    source = SourceHTTP(clock)
    client = httpx.AsyncClient(transport=httpx.MockTransport(source))
    provider = BucharestCabProvider(SimpleNamespace(request_timeout_seconds=1), client=client, now=lambda: clock[0])
    service = AirportCabPlanning(SimpleNamespace(), store, provider=provider, now=lambda: clock[0])
    def request(key="cab-quote-one", **changes):
        return AirportCabPlanningRequest(**({"group_id": "G1", "destination_airport": "CRA", "pickup_at": PICKUP,
            "suitcases": 2, "handbags": 2, "expected_version": store.read().version, "idempotency_key": key} | changes))
    yield store, service, source, request, clock
    await service.aclose()
    await client.aclose()


def importing(store, quote, *, key="cab-import-one", option=0, **changes):
    return AirportCabImport(**({"option_id": quote["transfer"]["options"][option]["id"],
        "expected_version": store.read().version, "idempotency_key": key} | changes))


def receipt_rows(store):
    with store.connect() as connection:
        return connection.execute("SELECT status,http_requests FROM airport_cab_receipts ORDER BY rowid").fetchall()


def source_context(store, quote):
    with store.connect() as connection:
        row = connection.execute("SELECT data FROM airport_cab_cache WHERE id=?", (quote["quote_id"],)).fetchone()
    return json.loads(row[0])["context"]


@pytest.mark.asyncio
async def test_quote_import_native_eur_and_real_airport_node_preserves_other_state(setup):
    store, service, source, request, clock = setup
    before = store.read()
    quote = await service.quote(request())
    assert quote["state"] == "ready", quote
    assert store.read().version == before.version and len(source.calls) == 2
    assert receipt_rows(store) == [("done", 2)]
    assert all(item["importable"] for item in quote["options"])
    result = service.import_quote(quote["quote_id"], importing(store, quote))
    after = store.read()
    assert result["applied"] is True and len(after.resources) == 1
    cab = after.resources[0]
    assert cab.id == result["resource_ids"][0]
    assert cab.source == SOURCE and cab.transport_mode == "private_transfer"
    assert (cab.origin, cab.destination, cab.departure, cab.arrival) == ("OTP", "CRA", 600, 747)
    assert cab.capacity == 2 and cab.eligible_group_ids == ["G1"] and cab.quote_total_cost == 188
    assert cab.cost_per_person == 0 and cab.budget_total_cost is cab.price_evidence is None
    assert cab.initial_position_assumption is cab.airport_arrival_assumption is None
    assert cab.external_offer_id is cab.vehicle_id is cab.valid_until is None
    assert cab.quote_freshness.received_at == NOW.isoformat() and cab.quote_freshness.max_age_seconds == 120
    assert cab.provenance["estimated_arrival"] is True and cab.provenance["supplier_booked"] is False
    assert after.groups == before.groups and not after.offers and not after.reservations
    destination = next(node for node in after.airports if node.id == "CRA")
    assert destination.kind == "airport" and destination.code == "CRA"
    assert (destination.lat, destination.lon) == (lookup_airport("CRA")["lat"], lookup_airport("CRA")["lon"])
    assert destination.available is False and destination.operational_status == "unknown"
    assert not any(node.kind == "meeting_point" for node in after.airports)
    assert before.groups[0].destination == after.groups[0].destination == "LHR"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    lambda w: setattr(w.groups[0], "adult_count", None),
    lambda w: setattr(w.groups[0], "adult_count", 1),
    lambda w: setattr(w.groups[0], "size", 8),
    lambda w: setattr(w.groups[0], "status", "confirmed"),
    lambda w: setattr(w.groups[0], "accessible", True),
    lambda w: setattr(w.groups[0], "origin", "LHR"),
    lambda w: setattr(w.groups[0], "ready_at", 601),
    lambda w: setattr(w.groups[0], "deadline", 600),
    lambda w: setattr(w.airports[0], "kind", "station"),
    lambda w: setattr(w, "currency", "RON"),
    confirm_offer,
    pending_arrival,
])
async def test_ineligible_scope_sends_no_http(setup, change):
    store, service, source, request, _ = setup
    mutate(store, change)
    with pytest.raises(DomainError):
        await service.quote(request())
    assert not source.calls and receipt_rows(store) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"expected_version": 1}, {"group_id": "missing"},
    {"pickup_at": NOW - timedelta(minutes=1)}, {"pickup_at": NOW + timedelta(days=32)},
    {"pickup_at": PICKUP + timedelta(seconds=1)}, {"pickup_at": "2030-01-04T10:00:00"},
    {"pickup_at": "2030-10-27T03:30:00+03:00"},
])
async def test_bad_version_or_date_sends_no_http(setup, changes):
    store, service, source, request, _ = setup
    with pytest.raises(DomainError):
        await service.quote(request(**changes))
    assert not source.calls and receipt_rows(store) == []


@pytest.mark.asyncio
async def test_planning_horizon_is_enforced_before_source_calls(setup):
    store, service, source, request, _ = setup
    mutate(store, lambda world: setattr(world.groups[0], "deadline", 20_000))
    with pytest.raises(DomainError):
        await service.quote(request(pickup_at=NOW + timedelta(days=8)))
    assert not source.calls


@pytest.mark.asyncio
async def test_conflicting_archived_airport_identity_blocks_before_http(setup):
    store, service, source, request, _ = setup
    def retain_bad_destination(world):
        node = airport("CRA")
        node.lat = 0
        world.airports.append(node)
    mutate(store, retain_bad_destination)
    # Store preserves removal as immutable geography; it must not become a
    # source-consistent airport merely because it is outside the active array.
    mutate(store, lambda world: setattr(world, "airports", [node for node in world.airports if node.id != "CRA"]))
    with pytest.raises(DomainError):
        await service.quote(request())
    assert not source.calls and receipt_rows(store) == []


@pytest.mark.asyncio
async def test_source_duration_outside_deadline_is_visible_and_cannot_be_imported(setup):
    store, service, source, request, _ = setup
    mutate(store, lambda world: setattr(world.groups[0], "deadline", 700))
    quote = await service.quote(request())
    assert quote["state"] == "blocked" and len(source.calls) == 2
    assert all(not option["importable"] and option["blockers"] for option in quote["options"])
    with pytest.raises(DomainError):
        service.import_quote(quote["quote_id"], importing(store, quote))
    assert not store.read().resources


@pytest.mark.asyncio
async def test_exact_replay_uses_receipt_and_conflicting_key_does_not_requery(setup):
    store, service, source, request, _ = setup
    original = request()
    first = await service.quote(original)
    mutate(store, lambda world: setattr(world.groups[1], "name", "Updated unrelated label"))
    replay = await service.quote(original)
    assert replay == {**first, "replayed": True} and len(source.calls) == 2
    with pytest.raises(DomainError) as error:
        await service.quote(request(handbags=1))
    assert error.value.status == 409 and len(source.calls) == 2
    assert receipt_rows(store) == [("done", 2)]


@pytest.mark.asyncio
async def test_each_http_dispatch_consumes_fixed_allowance(setup):
    store, service, source, request, _ = setup
    actual = service.provider
    class ExcessiveProvider:
        configured = True
        validate_request = actual.validate_request
        async def quote(self, query, *, before_dispatch):
            for _ in range(3):
                await before_dispatch()
            raise AssertionError("A third supplier call must be prevented")
        async def aclose(self):
            return None
    service.provider = ExcessiveProvider()
    result = await service.quote(request())
    assert result["state"] == "blocked" and result["blockers"]
    assert receipt_rows(store) == [("done", 2)] and not source.calls


@pytest.mark.asyncio
async def test_concurrent_claim_cancel_and_unknown_outcome_never_redispatch_same_key(setup):
    store, service, source, request, clock = setup
    original = request(); source.gate = asyncio.Event()
    running = asyncio.create_task(service.quote(original))
    await asyncio.wait_for(source.entered.wait(), 1)
    with pytest.raises(DomainError) as repeated:
        await service.quote(original)
    assert repeated.value.status == 503 and repeated.value.details["in_progress"] is True
    with pytest.raises(DomainError) as overlapping:
        await service.quote(request("another-quote"))
    assert overlapping.value.status == 409
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert receipt_rows(store) == [("unknown", 1)] and len(source.calls) == 1
    with pytest.raises(DomainError) as unknown:
        await service.quote(original)
    assert unknown.value.status == 503 and unknown.value.details["in_progress"] is False
    clock[0] += timedelta(seconds=121); service.purge()
    with pytest.raises(DomainError):
        await service.quote(original)
    assert len(source.calls) == 1


@pytest.mark.asyncio
async def test_expired_inflight_lease_becomes_unknown_without_source_redispatch(setup):
    store, service, source, request, clock = setup
    original = request(); source.gate = asyncio.Event()
    running = asyncio.create_task(service.quote(original))
    await asyncio.wait_for(source.entered.wait(), 1)
    clock[0] += timedelta(seconds=121)
    service.purge()
    assert receipt_rows(store) == [("unknown", 1)]
    with pytest.raises(DomainError) as error:
        await service.quote(original)
    assert error.value.status == 503 and error.value.details["in_progress"] is False
    assert error.value.details["unknown_outcome"] is True and len(source.calls) == 1
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    lambda w: setattr(w.groups[0], "budget", 450),
    lambda w: setattr(w, "epoch", (NOW - timedelta(minutes=1)).isoformat()),
    lambda w: setattr(w, "scenario_id", "replacement-case"),
    lambda w: setattr(w.groups[0], "status", "confirmed"),
    lambda w: setattr(w.airports[0], "kind", "station"),
    lambda w: setattr(w, "currency", "RON"),
    confirm_offer,
    pending_arrival,
])
async def test_changed_eligibility_between_http_steps_blocks_second_dispatch(setup, change):
    store, service, source, request, _ = setup
    source.after_bootstrap = lambda: mutate(store, change)
    result = await service.quote(request())
    assert result["state"] == "blocked", result
    assert len(source.calls) == 1 and receipt_rows(store) == [("done", 1)]
    assert not any(resource.source == SOURCE for resource in store.read().resources)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    lambda w: setattr(w.groups[0], "ready_at", 601),
    lambda w: setattr(w, "epoch", (NOW - timedelta(minutes=1)).isoformat()),
    lambda w: setattr(w, "scenario_id", "new-operation"),
])
async def test_changed_group_epoch_or_scenario_cannot_import_old_receipt(setup, change):
    store, service, _, request, _ = setup
    quote = await service.quote(request())
    mutate(store, change)
    with pytest.raises(DomainError) as error:
        service.import_quote(quote["quote_id"], importing(store, quote))
    assert error.value.status == 409 and not store.read().resources


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [pending_arrival, confirm_offer,
    lambda world: setattr(world.airports[0], "kind", "station"),
    lambda world: setattr(world, "currency", "RON"),
])
async def test_non_group_eligibility_change_after_quote_blocks_import(setup, change):
    store, service, source, request, _ = setup
    quote = await service.quote(request())
    mutate(store, change)
    with pytest.raises(DomainError):
        service.import_quote(quote["quote_id"], importing(store, quote))
    assert not any(resource.source == SOURCE for resource in store.read().resources)
    assert len(source.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [pending_arrival,
    lambda world: setattr(world.groups[0], "ready_at", 601),
])
async def test_changed_context_while_final_http_awaits_never_returns_importable_options(setup, change):
    store, service, source, request, _ = setup
    source.after_quote = lambda: mutate(store, change)
    result = await service.quote(request())
    assert result["state"] == "blocked"
    assert not any(option["importable"] for option in result["options"])
    assert len(source.calls) == 2 and not any(resource.source == SOURCE for resource in store.read().resources)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    lambda world: setattr(world, "scenario_id", "reset-operation"),
    lambda world: setattr(world, "epoch", (NOW - timedelta(minutes=1)).isoformat()),
    lambda world: setattr(world.groups[0], "ready_at", 601),
    pending_arrival,
])
async def test_replaying_receipt_in_changed_operation_does_not_advertise_importable_old_scope(setup, change):
    store, service, source, request, _ = setup
    original = request(); await service.quote(original)
    mutate(store, change)
    try:
        replay = await service.quote(original)
    except DomainError as error:
        assert error.status in (409, 422)
    else:
        assert replay["state"] == "blocked" and replay["replayed"] is True
        assert not any(option["importable"] for option in replay["options"])
    assert len(source.calls) == 2


@pytest.mark.asyncio
async def test_unrelated_operational_change_does_not_invalidate_group_scoped_import(setup):
    store, service, source, request, _ = setup
    quote = await service.quote(request())
    mutate(store, lambda world: setattr(world.groups[1], "budget", 300))
    result = service.import_quote(quote["quote_id"], importing(store, quote))
    assert result["applied"] and store.read().groups[1].budget == 300 and len(source.calls) == 2


@pytest.mark.asyncio
async def test_expired_cache_is_purged_but_quote_and_import_idempotency_are_retained(setup):
    store, service, source, request, clock = setup
    original = request(); quote = await service.quote(original)
    original_import = importing(store, quote)
    imported = service.import_quote(quote["quote_id"], original_import)
    clock[0] += timedelta(seconds=121); service.purge()
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM airport_cab_cache").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM airport_cab_receipts").fetchone()[0] == 1
    with pytest.raises(DomainError) as error:
        await service.quote(original)
    assert error.value.status == 409 and len(source.calls) == 2
    replay = service.import_quote(quote["quote_id"], original_import)
    assert replay["replayed"] and replay["resource_ids"] == imported["resource_ids"]
    with pytest.raises(DomainError):
        service.import_quote(quote["quote_id"], importing(store, quote, key="new-import-old-expired"))
    assert len(store.read().resources) == 1


@pytest.mark.asyncio
async def test_source_advance_cutoff_shortens_receipt_window_and_expires_without_redispatch(setup):
    store, service, source, request, clock = setup
    # A valid quote arrives only 30 seconds before the source's nine-hour
    # advance requirement would cease to hold. The 120-second application
    # receipt window must not extend this source constraint.
    cutoff = PICKUP - timedelta(hours=9)
    clock[0] = cutoff - timedelta(seconds=30)
    original = request()
    quote = await service.quote(original)
    assert quote["state"] == "ready"
    assert datetime.fromisoformat(quote["usable_until"]) == cutoff
    assert datetime.fromisoformat(quote["usable_until"]) - datetime.fromisoformat(quote["created_at"]) == timedelta(seconds=30)
    assert quote["transfer"]["policy"]["required_advance_hours"] == 9
    clock[0] = cutoff - timedelta(seconds=1)
    imported = service.import_quote(quote["quote_id"], importing(store, quote))
    assert imported["applied"] is True
    clock[0] = cutoff
    service.purge()
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM airport_cab_cache").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM airport_cab_receipts").fetchone()[0] == 1
    with pytest.raises(DomainError) as expired:
        await service.quote(original)
    assert expired.value.status == 409
    with pytest.raises(DomainError) as late_import:
        service.import_quote(quote["quote_id"], importing(store, quote, key="import-after-source-cutoff"))
    assert late_import.value.status == 409
    assert len(source.calls) == 2 and len(store.read().resources) == 1


@pytest.mark.asyncio
async def test_retired_import_and_older_receipt_revision_cannot_resurrect(setup):
    store, service, source, request, clock = setup
    first = await service.quote(request())
    first_import = service.import_quote(first["quote_id"], importing(store, first))
    clock[0] += timedelta(seconds=1)
    second = await service.quote(request("newer-quote"))
    second_import = service.import_quote(second["quote_id"], importing(store, second, key="newer-import"))
    assert first_import["resource_ids"] != second_import["resource_ids"]
    assert len(source.calls) == 4
    with pytest.raises(DomainError, match="newer source revision"):
        service.import_quote(first["quote_id"], importing(store, first, key="resurrect-old"))
    second_id = second_import["resource_ids"][0]
    mutate(store, lambda world: setattr(next(resource for resource in world.resources if resource.id == second_id), "available", False))
    with pytest.raises(DomainError, match="retired"):
        service.import_quote(second["quote_id"], importing(store, second, key="resurrect-current"))
    assert all(not resource.available for resource in store.read().resources if resource.source == SOURCE)
    # Immutable older historical snapshots may retain their then-available flag;
    # they must remain outside active planning inventory.
    assert any(resource.id == first_import["resource_ids"][0] for resource in resource_history(store.read(), source=SOURCE, group_id="G1"))


@pytest.mark.asyncio
@pytest.mark.parametrize("source_error,code", [(429, "http_429"), ("no_vehicle_types_found", "no_vehicles"), ("min_advance_night_booking", "pickup_too_soon")])
async def test_source_errors_are_visible_without_inventory_or_retry(setup, source_error, code):
    store, service, source, request, _ = setup
    if isinstance(source_error, int): source.error_status = source_error
    else: source.error_payload = {"error": source_error, "message": "upstream-private-token"}
    original = request(); result = await service.quote(original)
    assert result["state"] == "blocked" and result["transfer"] is None and result["options"] == []
    assert result["blockers"][0]["code"] == code and "upstream-private-token" not in json.dumps(result)
    calls = len(source.calls); replay = await service.quote(original)
    assert replay["replayed"] and len(source.calls) == calls and not store.read().resources


@pytest.mark.asyncio
async def test_main_router_enforces_auth_body_bounds_and_scoped_import(tmp_path, monkeypatch):
    clock = [NOW]; monkeypatch.setattr(store_module, "utcnow", lambda: clock[0])
    settings = Settings(_env_file=None, environment="test", operation_mode="live", database_path=str(tmp_path / "api-cab.sqlite3"),
        operator_token="offline-operator-secret", openai_api_key="", fr24_api_key="", google_maps_api_key="", google_maps_server_api_key="",
        liteapi_api_key="", booking_demand_api_token="", duffel_access_token="", apl_api_key="", distribusion_api_key="")
    app = create_app(settings); service = app.state.airport_cab; initialize(app.state.store)
    await service.provider.aclose()
    source = SourceHTTP(clock)
    http = httpx.AsyncClient(transport=httpx.MockTransport(source))
    service.provider = BucharestCabProvider(SimpleNamespace(request_timeout_seconds=1), client=http, now=lambda: clock[0])
    service.now = lambda: clock[0]
    body = {"group_id": "G1", "destination_airport": "CRA", "pickup_at": PICKUP.isoformat(), "suitcases": 2, "handbags": 2,
        "expected_version": app.state.store.read().version, "idempotency_key": "api-cab-search"}
    headers = {"Authorization": "Bearer offline-operator-secret"}
    try:
        with TestClient(app) as client:
            assert client.get("/api/airport-transfers/status").status_code == 401
            assert client.post("/api/airport-transfers/quotes", json=body).status_code == 401
            assert client.post("/api/airport-transfers/quotes/fake/import", json={"option_id":"fake", "expected_version":2, "idempotency_key":"unauthorized"}).status_code == 401
            status = client.get("/api/airport-transfers/status", headers=headers)
            assert status.status_code == 200 and status.json()["http_requests_per_quote"] == 2
            for key, value in [("suitcases", -1), ("handbags", True), ("handbags", 8), ("destination_airport", "SBZ"), ("pickup_at", 12345)]:
                invalid = client.post("/api/airport-transfers/quotes", json={**body, key:value}, headers=headers)
                assert invalid.status_code == 422, invalid.text
            assert not source.calls
            quote_response = client.post("/api/airport-transfers/quotes", json=body, headers=headers)
            assert quote_response.status_code == 200, quote_response.text
            quote = quote_response.json(); assert quote["state"] == "ready"
            assert client.post("/api/airport-transfers/quotes", json=body, headers=headers).json()["replayed"] is True
            import_body = {"option_id":quote["transfer"]["options"][0]["id"], "expected_version":app.state.store.read().version, "idempotency_key":"api-cab-import"}
            imported = client.post(f'/api/airport-transfers/quotes/{quote["quote_id"]}/import', json=import_body, headers=headers)
            assert imported.status_code == 200, imported.text
            assert imported.json()["applied"] is True and len(source.calls) == 2
            assert app.state.store.read().resources[0].quote_total_cost == 188
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_automatic_phase_errors_propagate_after_retaining_unknown_receipt(setup):
    store, service, source, request, _ = setup
    source.error_status = 503
    phases = []
    async def runner(phase, operation):
        phases.append(phase)
        return await operation()
    original = request()
    with pytest.raises(ProviderError) as error:
        await service.quote(original, request_runner=runner, propagate_errors=True)
    assert error.value.code == "http_503" and phases == ["bootstrap"]
    assert receipt_rows(store) == [("unknown", 1)] and len(source.calls) == 1
    with store.connect() as connection:
        saved = json.loads(connection.execute("SELECT data FROM airport_cab_cache").fetchone()[0])
    assert saved["failure"]["code"] == "http_503" and saved["response"]["state"] == "blocked"
    assert "upstream-private-token" not in json.dumps(saved)
    with pytest.raises(DomainError) as replay:
        await service.quote(original, request_runner=runner, propagate_errors=True)
    assert replay.value.status == 503 and len(source.calls) == 1


@pytest.mark.asyncio
async def test_known_automatic_failure_is_rethrown_on_replay_without_more_requests(setup):
    store, service, source, request, _ = setup
    source.error_payload = {"error": "no_vehicle_types_found"}
    original = request()
    for _ in range(2):
        with pytest.raises(ProviderError) as error:
            await service.quote(original, propagate_errors=True)
        assert error.value.code == "no_vehicles"
    assert len(source.calls) == 2 and receipt_rows(store) == [("done", 2)]
    manual = await service.quote(original)
    assert manual["state"] == "blocked" and manual["replayed"] is True


@pytest.mark.asyncio
async def test_stage_receipt_categories_together_and_append_only_chosen_option(setup):
    store, service, source, request, clock = setup
    original = await service.quote(request())
    first = service.import_quote(original["quote_id"], importing(store, original))
    clock[0] += timedelta(seconds=1)
    refreshed = await service.quote(request("replacement-quote"))
    context = source_context(store, refreshed)
    option_ids = [item["id"] for item in refreshed["transfer"]["options"]]
    unchanged = store.read().model_dump(mode="json")
    staged = store.read()
    result = append_airport_cab_options(staged, context, refreshed, option_ids, now=clock[0], stage=True)
    assert len(result["resource_ids"]) == 4 and result["superseded_resource_ids"] == first["resource_ids"]
    assert {item.id for item in staged.resources if item.source == SOURCE and item.available} == set(result["resource_ids"])
    assert store.read().model_dump(mode="json") == unchanged
    chosen = store.read()
    imported = append_airport_cab_options(chosen, context, refreshed, [option_ids[2]], now=clock[0])
    assert imported["superseded_resource_ids"] == first["resource_ids"]
    assert [item.quote_total_cost for item in chosen.resources if item.source == SOURCE and item.available] == [235]
    assert chosen.groups[1].model_dump() == store.read().groups[1].model_dump()
    assert len(source.calls) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["missing_after_valid", "duplicate", "multiple_final"])
async def test_append_rejects_invalid_option_batches_without_partial_mutation(setup, selection):
    store, service, _, request, clock = setup
    quote = await service.quote(request()); context = source_context(store, quote)
    world = store.read(); before = world.model_dump(mode="json")
    ids = [item["id"] for item in quote["transfer"]["options"]]
    selected = [ids[0], "not-a-source-option"] if selection == "missing_after_valid" else [ids[0], ids[0]] if selection == "duplicate" else ids[:2]
    with pytest.raises(DomainError):
        append_airport_cab_options(world, context, quote, selected, now=clock[0], stage=selection != "multiple_final")
    assert world.model_dump(mode="json") == before


@pytest.mark.asyncio
async def test_final_append_never_compacts_unrelated_inventory_and_stage_stays_bounded(setup, monkeypatch):
    import app.inventory_lifecycle as inventory
    store, service, _, request, clock = setup
    quote = await service.quote(request()); context = source_context(store, quote)
    world = store.read()
    world.resources.append(Resource(id="UNRELATED", kind="bus", name="Retained unrelated evidence", origin="LHR", destination="OTP",
        departure=1, arrival=2, capacity=1, cost_per_person=1, available=False, source="Existing source"))
    before = world.model_dump(mode="json")
    monkeypatch.setattr(inventory, "MAX_RESOURCES", 1)
    ids = [item["id"] for item in quote["transfer"]["options"]]
    with pytest.raises(DomainError) as error:
        append_airport_cab_options(world, context, quote, ids[:1], now=clock[0])
    assert error.value.details["inventory_capacity_exhausted"] is True
    assert world.model_dump(mode="json") == before
    append_airport_cab_options(world, context, quote, ids, now=clock[0], stage=True)
    assert len(world.resources) == 5 and any(item.id == "UNRELATED" for item in world.resources)
    assert len(store.read().resources) == 0


@pytest.mark.asyncio
async def test_staging_older_or_retired_receipt_cannot_restore_source_inventory(setup):
    store, service, _, request, clock = setup
    old = await service.quote(request()); old_context = source_context(store, old)
    clock[0] += timedelta(seconds=1)
    latest = await service.quote(request("latest-receipt"))
    latest_import = service.import_quote(latest["quote_id"], importing(store, latest))
    unchanged = store.read().model_dump(mode="json")
    with pytest.raises(DomainError, match="newer source revision"):
        append_airport_cab_options(store.read(), old_context, old, [old["transfer"]["options"][0]["id"]], now=clock[0], stage=True)
    assert store.read().model_dump(mode="json") == unchanged
    mutate(store, lambda world: setattr(next(item for item in world.resources if item.id == latest_import["resource_ids"][0]), "available", False))
    with pytest.raises(DomainError, match="retired"):
        append_airport_cab_options(store.read(), source_context(store, latest), latest, [latest["transfer"]["options"][0]["id"]], now=clock[0], stage=True)


@pytest.mark.asyncio
async def test_existing_immutable_option_cannot_silently_overwrite_changed_inventory(setup):
    store, service, _, request, _ = setup
    quote = await service.quote(request())
    service.import_quote(quote["quote_id"], importing(store, quote))
    mutate(store, lambda world: setattr(world.resources[0], "quote_total_cost", 1))
    with pytest.raises(DomainError, match="immutable receipt"):
        service.import_quote(quote["quote_id"], importing(store, quote, key="different-import-key"))
    assert store.read().resources[0].quote_total_cost == 1
