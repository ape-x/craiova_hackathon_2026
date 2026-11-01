"""Combined CAB recovery paths with SQLite, MockTransport and fake AI only.

The real consumer parser, receipt store, job ledger and solver are exercised.
The suite's socket guard prevents all actual supplier/model/runtime requests.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from pydantic import ValidationError

import app.airport_cab_evidence as evidence
import app.airport_restrictions as restrictions_module
import app.inventory_lifecycle as inventory
import app.live_travel as live_travel
import app.operational_reports as reports_module
import app.pricing as pricing
import app.recovery_jobs as jobs_module
import app.recovery_ledger as ledger_module
import app.service as service_module
import app.solver as solver
import app.store as store_module
import app.travel as travel_module
from app.airport_catalog import lookup_airport
from app.airport_cab_planning import AirportCabImport, AirportCabPlanning, AirportCabPlanningRequest, append_airport_cab_options
from app.airport_cab_recovery import validate_airport_transfer_selection
from app.airport_restrictions import AirportRestrictionReportInput, AirportRestrictions
from app.config import Settings
from app.inventory_lifecycle import resource_history
from app.models import Airport, IncidentFlightReference, Offer, PassengerGroup, RecoveryIncident, Resource, Strategy
from app.operational_reports import ArrivalRecoveryOptions, ArrivalReportInput, OperationalReports
from app.providers.bucharest_cab import BucharestCabProvider, LOCAL_ZONE, SOURCE
from app.recovery_jobs import RecoveryCancellation, RecoveryJobRequest, RecoveryJobs
from app.service import RecoveryService
from app.store import DomainError, Store


NOW = datetime(2030, 1, 4, tzinfo=timezone.utc)
PICKUP = NOW + timedelta(hours=10)
FIXTURE = Path(__file__).parent / "fixtures/bucharest_cab_otp_cra.json"


def airport(code):
    row = lookup_airport(code)
    return Airport(id=code, code=code, name=row["name"], lat=row["lat"], lon=row["lon"], operational_status="open")


class SourceHTTP:
    def __init__(self):
        self.calls = []
        self.after_bootstrap = None
        self.pause_phase = None
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.timeout_phase = None
        self.mutate_payload = None

    async def __call__(self, request):
        phase = "bootstrap" if request.url.path == "/en/book" else "vehicles"
        self.calls.append((phase, request))
        if self.pause_phase == phase:
            self.entered.set()
            await self.release.wait()
        if self.timeout_phase == phase:
            raise httpx.ReadTimeout("Offline uncertain supplier outcome", request=request)
        if phase == "bootstrap":
            if self.after_bootstrap:
                self.after_bootstrap()
            config = {"csrfToken": "b" * 32, "ajax_get_suitable_vehicles_url":
                "https://www.bucharest.cab/index.php?option=com_tulipbooking&format=json&task=ajax.getSuitableVehicles&format=json"}
            return httpx.Response(200, text="<script>var tulipBookingScriptData=" + json.dumps(config) + ";</script>",
                headers={"content-type": "text/html"})
        assert request.url.path == "/en/" and request.url.params["task"] == "ajax.getSuitableVehicles"
        payload = json.loads(FIXTURE.read_text())
        local = datetime.strptime(request.url.params["pickupDateTime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_ZONE)
        payload.update(datetime_param=local.strftime("%Y-%m-%d %H:%M:%S"),
            datetime=local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
        if self.mutate_payload:
            self.mutate_payload(payload)
        return httpx.Response(200, json=payload)


class FakeAI:
    configured = True

    def __init__(self):
        self.plans, self.decisions = [], []
        self.before_plan = None
        self.before_choose = None

    async def plan_recovery_queries(self, world, options, request):
        self.plans.append((world, options, request))
        if self.before_plan:
            await self.before_plan()
        selected = {}
        for option in options:
            if option["query"]["origin_code"] == "CRA":
                selected.setdefault(option["group_id"], option["id"])
        return {"query_ids": list(selected.values())[:request["max_search_calls"]],
            "strategies": [Strategy(id=name, label=name, objective=name).model_dump()
                for name in ("balanced", "fastest", "lowest_cost")], "explanation": "Offline bounded search selection"}

    async def choose(self, world, candidates, request):
        self.decisions.append((world, candidates, request))
        if self.before_choose:
            await self.before_choose()
        chosen = min(candidates, key=lambda item: (-item["recovered_passengers"], item["total_cost"]))
        return {"plan_id": chosen["id"], "provider": "Offline Decisions", "explanation": "Offline validated candidate"}


class FakeFlights:
    flight_configured = True
    flight_source = "Nuitee"

    def __init__(self, clock):
        self.clock, self.calls = clock, []

    async def search(self, request):
        self.calls.append(request)
        departure = max(request.earliest_departure_at, NOW + timedelta(hours=13))
        arrival = departure + timedelta(hours=2)
        identity = "offline-" + request.origin_code + request.destination_code
        return {"state": "ready", "source": "Nuitee", "items": [{"id": identity,
            "provider": "nuitee", "provider_offer_id": identity, "name": "Offline onward flight",
            "origin": request.origin_code, "destination": request.destination_code,
            "departure_at": departure.isoformat(), "arrival_at": arrival.isoformat(),
            "total_price": "100.00", "currency": "EUR", "passengers": request.adults,
            "expires_at": (self.clock[0] + timedelta(hours=1)).isoformat(), "quoted_at": self.clock[0].isoformat(),
            "mode": "live", "accessibility_confirmed": False,
            "segments": [{"origin": request.origin_code, "destination": request.destination_code,
                "departure_at": departure.isoformat(), "arrival_at": arrival.isoformat()}]}]}


def change(store, action):
    version = store.read().version
    return store.mutate("offline-change-" + str(version), {}, version, action)


def transfer(group_id="G1", **changes):
    return {"group_id": group_id, "destination_airport": "CRA", "pickup_at": PICKUP,
        "suitcases": 2, "handbags": 2} | changes


def request(env, **changes):
    return RecoveryJobRequest(**({"expected_version": env.store.read().version,
        "idempotency_key": "offline-cab-job", "group_ids": ["G1"], "modes": ["bus"],
        "max_provider_calls": 3, "airport_transfers": [transfer()]} | changes))


async def run_next(jobs):
    claimed = jobs.ledger.claim_next()
    assert claimed is not None
    await jobs.run(*claimed)
    return jobs.get(claimed[0]["id"])["job"]


def stable(world):
    return world.model_dump(mode="json", exclude={"sim_time"})


def assert_source_uncertainty_context(context):
    assert "unreserved" in context
    assert "arrivals are estimates" in context
    assert "fleet stock and expiry are unknown" in context
    assert "at most one category" in context
    assert "guaranteed arrival times" in context


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    clock = [NOW]

    class ClockMeta(type):
        def __instancecheck__(cls, value):
            return isinstance(value, datetime)

    class Clock(datetime, metaclass=ClockMeta):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

    for module in (evidence, pricing, solver, live_travel, travel_module):
        monkeypatch.setattr(module, "datetime", Clock)
    for module in (store_module, service_module, jobs_module, ledger_module, reports_module, restrictions_module):
        monkeypatch.setattr(module, "utcnow", lambda: clock[0])
    # Keep the worker lease beyond the explicit 121-second freshness advance;
    # that regression must test quote expiry, not accidental lease loss.
    settings = Settings(_env_file=None, operation_mode="live", request_timeout_seconds=120,
        database_path=str(tmp_path / "cab-jobs.sqlite3"))
    store = Store(settings.database_path, operation_mode="live")

    def populate(world):
        world.scenario_id, world.epoch, world.sim_time = "OFFLINE-CAB-JOBS", NOW.isoformat(), 0
        world.airports = [airport(code) for code in ("OTP", "CRA", "LHR")]
        world.groups = [PassengerGroup(id="G1", name="Selected adults", size=2, adult_count=2,
            origin="OTP", destination="CRA", ready_at=600, deadline=1800, budget=500,
            original_flight_id="ARRIVED", source="operator_input", token="offline-one", incident_id="CASE"),
            PassengerGroup(id="G2", name="Unrelated adults", size=2, adult_count=2,
            origin="LHR", destination="OTP", ready_at=600, deadline=1800, budget=500,
            original_flight_id="OTHER", source="operator_input", token="offline-two", incident_id="OTHER-CASE")]
        world.resources = [Resource(id="UNTOUCHED", kind="bus", name="Unrelated withdrawn evidence",
            origin="LHR", destination="OTP", departure=600, arrival=700, capacity=2, cost_per_person=40,
            source="Offline prior source", available=False, eligible_group_ids=["G2"])]
        world.offers, world.reservations = [], []
        world.budget = 2000
        world.incidents = [RecoveryIncident(id="CASE", created_at=NOW.isoformat(), kind="destination_unavailable",
            description="OTP air operations unavailable; passengers reported ready at OTP.",
            requested_flight_number="RO321", date_utc=NOW.date().isoformat(),
            flight_ref=IncidentFlightReference(lookup_id="offline-lookup", selection_id="offline-flight"),
            unavailable_airport_code="OTP", group_ids=["G1"], status="open")]

    change(store, populate)
    source = SourceHTTP()
    client = httpx.AsyncClient(transport=httpx.MockTransport(source))
    provider = BucharestCabProvider(settings, client=client, now=lambda: clock[0])
    cab = AirportCabPlanning(settings, store, provider=provider, now=lambda: clock[0])
    ai, flights = FakeAI(), FakeFlights(clock)
    service = RecoveryService(settings, store, ai)
    jobs = RecoveryJobs(settings, store, service, flights, ai, airport_cab=cab)
    yield SimpleNamespace(settings=settings, store=store, cab=cab, source=source, clock=clock,
        ai=ai, flights=flights, jobs=jobs)
    await cab.aclose()
    await client.aclose()


async def manual_quote(env, key, group_id="G1"):
    return await env.cab.quote(AirportCabPlanningRequest(expected_version=env.store.read().version,
        idempotency_key=key, **transfer(group_id)))


def manual_import(env, response, key):
    return env.cab.import_quote(response["quote_id"], AirportCabImport(
        expected_version=env.store.read().version, idempotency_key=key,
        option_id=response["transfer"]["options"][0]["id"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"modes": ["flight"]},
    {"airport_transfers": [transfer(), transfer()]},
    {"airport_transfers": [transfer("G2")]},
])
async def test_invalid_transfer_delegation_is_rejected_before_source_or_queue(env, changes):
    before = env.store.read()
    with pytest.raises(DomainError) as rejected:
        env.jobs.start("CASE", request(env, **changes))
    assert rejected.value.status == 422
    assert not env.jobs.list("CASE")["jobs"] and not env.source.calls
    assert stable(env.store.read()) == stable(before)


@pytest.mark.asyncio
async def test_two_quote_requests_require_both_http_allowances_and_final_decision(env):
    def bind_other(world):
        world.groups[1].origin, world.groups[1].destination, world.groups[1].incident_id = "OTP", "CRA", "CASE"
        world.incidents[0].group_ids.append("G2")
    change(env.store, bind_other)
    before = env.store.read()
    with pytest.raises(DomainError) as rejected:
        env.jobs.start("CASE", request(env, group_ids=["G1", "G2"], max_provider_calls=4,
            airport_transfers=[transfer("G1"), transfer("G2")]))
    assert rejected.value.status == 422
    assert not env.jobs.list("CASE")["jobs"] and not env.source.calls
    assert stable(env.store.read()) == stable(before)


@pytest.mark.asyncio
async def test_pickup_before_readiness_is_rejected_without_substituting_a_later_time(env):
    before = env.store.read()
    explicit = PICKUP - timedelta(minutes=1)
    with pytest.raises(DomainError) as rejected:
        env.jobs.start("CASE", request(env, airport_transfers=[transfer(pickup_at=explicit)]))
    assert rejected.value.status == 422
    assert not env.source.calls and not env.jobs.list("CASE")["jobs"]
    assert stable(env.store.read()) == stable(before)


def test_empty_transfer_defaults_preserve_original_payload_fingerprints():
    base = {"expected_version": 1, "idempotency_key": "offline-original-request", "group_ids": ["G1"]}
    assert "airport_transfers" not in RecoveryJobRequest(**base).model_dump(mode="json")
    assert RecoveryJobRequest(**base).model_dump(mode="json") == RecoveryJobRequest(**base,
        airport_transfers=[]).model_dump(mode="json")
    assert "airport_transfers" not in ArrivalRecoveryOptions().model_dump(mode="json")


@pytest.mark.parametrize("changes", [
    {"pickup_at": "2030-01-04T10:00:00"},
    {"pickup_at": "2030-01-04T10:00:01+00:00"},
    {"suitcases": True}, {"handbags": -1}, {"group_id": "g1"},
])
def test_transfer_evidence_requires_exact_time_scope_and_declared_integer_bags(changes):
    with pytest.raises(ValidationError):
        RecoveryJobRequest(expected_version=1, idempotency_key="offline-invalid-input",
            group_ids=["G1"], modes=["bus"], airport_transfers=[transfer(**changes)])


@pytest.mark.asyncio
async def test_bus_only_sources_then_decides_in_three_calls_without_flight_credentials(env):
    env.flights.flight_configured = False
    req = request(env)
    before = env.store.read()
    env.jobs.start("CASE", req)
    assert not env.source.calls and stable(env.store.read()) == stable(before)
    result = await run_next(env.jobs)
    assert result["status"] == "completed", result
    assert result["calls_dispatched"] == 3
    assert [step["status"] for step in result["steps"]] == ["succeeded"] * 3
    assert [phase for phase, _ in env.source.calls] == ["bootstrap", "vehicles"]
    assert not env.flights.calls and not env.ai.plans and len(env.ai.decisions) == 1
    assert_source_uncertainty_context(env.ai.decisions[0][2]["context"])
    after = env.store.read()
    assert after.version == before.version + 1 and after.groups[1] == before.groups[1]
    assert next(resource for resource in after.resources if resource.id == "UNTOUCHED") == before.resources[0]
    selected = [resource for resource in after.resources if resource.source == SOURCE]
    assert len(selected) == 1 and selected[0].quote_total_cost == 188
    assert selected[0].eligible_group_ids == ["G1"] and selected[0].booking_status == "unbooked"
    assert len(after.offers) == 1 and after.offers[0].status == "proposed"
    assert [leg.resource_id for leg in after.offers[0].itinerary.legs] == [selected[0].id]
    assert after.offers[0].itinerary.arrival == 747
    assert result["result"]["recovered_passengers"] == 2
    replay = env.jobs.start("CASE", req)
    assert replay["replayed"] and replay["job"]["status"] == "completed"
    assert env.jobs.ledger.claim_next() is None and len(env.source.calls) == 2
    with env.store.connect() as conn:
        saved = " ".join(str(row) for row in conn.execute("SELECT query,result FROM recovery_steps"))
    assert "csrfToken" not in saved and "b" * 32 not in saved


@pytest.mark.asyncio
async def test_explicit_zero_luggage_is_valid_and_exact_offset_pickup_reaches_source(env):
    pickup = "2030-01-04T12:00:00+02:00"
    env.jobs.start("CASE", request(env, airport_transfers=[transfer(pickup_at=pickup, suitcases=0, handbags=0)]))
    result = await run_next(env.jobs)
    assert result["status"] == "completed" and result["calls_dispatched"] == 3, result
    assert env.source.calls[1][1].url.params["pickupDateTime"] == "2030-01-04 12:00:00"
    resource = next(item for item in env.store.read().resources if item.source == SOURCE)
    evidence_request = resource.provenance["airport_cab_quote"]["request"]
    assert datetime.fromisoformat(evidence_request["pickup_at"]) == PICKUP
    assert evidence_request["suitcases"] == evidence_request["handbags"] == 0
    assert evidence_request["baggage_supplier_scoped"] is False


@pytest.mark.asyncio
async def test_source_lead_rejection_never_retries_with_an_optimizer_shifted_pickup(env):
    change(env.store, lambda world: setattr(world.groups[0], "ready_at", 0))
    before = env.store.read()
    explicit = NOW + timedelta(hours=8)
    env.jobs.start("CASE", request(env, airport_transfers=[transfer(pickup_at=explicit)]))
    result = await run_next(env.jobs)
    assert result["status"] == "failed", result
    assert result["calls_dispatched"] == 2 and len(env.source.calls) == 2
    assert env.source.calls[1][1].url.params["pickupDateTime"] == "2030-01-04 10:00:00"
    assert "advance" in result["message"].lower()
    assert not env.ai.plans and not env.ai.decisions and not env.flights.calls
    assert stable(env.store.read()) == stable(before)


@pytest.mark.asyncio
@pytest.mark.parametrize("resource_limit", [None, 3])
async def test_initial_cab_unlocks_only_connected_onward_flight_with_remaining_budget(env, monkeypatch, resource_limit):
    if resource_limit is not None:
        # One untouched resource plus the chosen CAB and flight fit exactly.
        # Four ephemeral vehicle categories must not consume persistent slots.
        monkeypatch.setattr(inventory, "MAX_RESOURCES", resource_limit)
    untouched = env.store.read().resources[0].model_copy(deep=True)
    change(env.store, lambda world: setattr(world.groups[0], "destination", "LHR"))
    env.jobs.start("CASE", request(env, modes=["bus", "flight"], max_provider_calls=5))
    result = await run_next(env.jobs)
    assert result["status"] == "completed", (result["message"], result.get("gaps"), result["steps"])
    assert result["calls_dispatched"] == 5 and len(env.source.calls) == 2
    assert len(env.ai.plans) == len(env.flights.calls) == len(env.ai.decisions) == 1
    assert env.ai.plans[0][2]["max_search_calls"] == 1
    assert_source_uncertainty_context(env.ai.plans[0][2]["context"])
    assert_source_uncertainty_context(env.ai.decisions[0][2]["context"])
    flight = env.flights.calls[0]
    assert (flight.origin_code, flight.destination_code) == ("CRA", "LHR")
    assert flight.earliest_departure_at >= NOW + timedelta(minutes=777)
    world = env.store.read()
    assert [leg.kind for leg in world.offers[0].itinerary.legs] == ["bus", "flight"]
    assert [leg.origin for leg in world.offers[0].itinerary.legs] == ["OTP", "CRA"]
    assert world.offers[0].itinerary.total_cost == 288
    assert len([resource for resource in world.resources if resource.source == SOURCE]) == 1
    assert len(world.resources) == 3
    assert next(resource for resource in world.resources if resource.id == "UNTOUCHED") == untouched


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry", ["receipt", "lead"])
async def test_cab_expiry_during_responses_stops_flight_dispatch_without_retry(env, expiry):
    pickup = PICKUP if expiry == "receipt" else NOW + timedelta(hours=9, minutes=1)
    def onward(world):
        world.groups[0].destination = "LHR"
        if expiry == "lead":
            world.groups[0].ready_at = 540
    change(env.store, onward)
    before = env.store.read()
    async def expire():
        env.clock[0] += timedelta(seconds=121 if expiry == "receipt" else 61)
    env.ai.before_plan = expire
    env.jobs.start("CASE", request(env, modes=["bus", "flight"], max_provider_calls=5,
        airport_transfers=[transfer(pickup_at=pickup)]))
    result = await run_next(env.jobs)
    assert result["status"] == "blocked", result
    assert result["calls_dispatched"] == 3 and len(env.source.calls) == 2
    assert len(env.ai.plans) == 1 and not env.ai.decisions and not env.flights.calls
    assert [step["status"] for step in result["steps"]] == ["succeeded"] * 3
    assert any(item["code"] == "ground_connection_expired" for item in result["gaps"])
    after = env.store.read()
    assert after.version == before.version and after.resources == before.resources
    assert after.groups == before.groups and not after.offers and not after.reservations
    assert datetime.fromisoformat(env.ai.plans[0][0]["epoch"]) == NOW


@pytest.mark.asyncio
async def test_flight_connection_recheck_accepts_a_surviving_category_without_shifting_query(env):
    change(env.store, lambda world: setattr(world.groups[0], "destination", "LHR"))
    response = await manual_quote(env, "offline-alternative-ground-witness")
    world = env.store.read()
    group = world.groups[0]
    option_ids = [item["option_id"] for item in response["options"] if item["importable"]]
    assert len(option_ids) > 1
    append_airport_cab_options(world, evidence.airport_cab_context(world, group), response,
        option_ids, now=env.clock[0], stage=True)
    options, _ = jobs_module.flight_query_options(world, [group])
    original = next(item for item in options if item["query"]["origin_code"] == "CRA")
    snapshot = json.dumps(original, sort_keys=True)
    categories = [item for item in world.resources if item.source == SOURCE]
    for resource in categories[:-1]:
        resource.available = False
    assert jobs_module.current_flight_connection(world, [group], original)
    assert json.dumps(original, sort_keys=True) == snapshot
    categories[-1].available = False
    assert not jobs_module.current_flight_connection(world, [group], original)
    assert len(env.source.calls) == 2 and not env.ai.plans and not env.flights.calls
    assert not any(item.source == SOURCE for item in env.store.read().resources)


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["cancel", "version"])
async def test_between_http_requests_cancellation_or_version_change_stops_second_dispatch(env, interruption):
    started = env.jobs.start("CASE", request(env))["job"]
    before = env.store.read()
    if interruption == "cancel":
        env.source.after_bootstrap = lambda: env.jobs.cancel(started["id"],
            RecoveryCancellation(idempotency_key="offline-cancel-between-http"))
    else:
        env.source.after_bootstrap = lambda: change(env.store, lambda world: setattr(world, "budget", 1999))
    result = await run_next(env.jobs)
    assert result["status"] == ("cancelled" if interruption == "cancel" else "needs_review"), result
    assert result["calls_dispatched"] == 1 and len(env.source.calls) == 1
    assert result["steps"][0]["status"] == "succeeded"
    assert not env.ai.plans and not env.ai.decisions and not env.flights.calls
    after = env.store.read()
    assert after.resources == before.resources and after.groups == before.groups
    assert not after.offers and not after.reservations


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry", ["receipt", "lead"])
async def test_quote_expiring_during_decisions_never_commits_or_requeries(env, expiry):
    pickup = PICKUP if expiry == "receipt" else NOW + timedelta(hours=9, minutes=1)
    if expiry == "lead":
        change(env.store, lambda world: setattr(world.groups[0], "ready_at", 540))
    before = env.store.read()
    async def expire():
        env.clock[0] += timedelta(seconds=121 if expiry == "receipt" else 61)
    env.ai.before_choose = expire
    env.jobs.start("CASE", request(env, airport_transfers=[transfer(pickup_at=pickup)]))
    result = await run_next(env.jobs)
    assert result["status"] == "needs_review", result
    assert result["calls_dispatched"] == 3 and len(env.source.calls) == 2
    after = env.store.read()
    assert after.version == before.version and after.resources == before.resources
    assert after.groups == before.groups and not after.offers and not after.reservations


@pytest.mark.asyncio
async def test_job_uses_shared_manual_receipt_order_and_old_receipt_cannot_resurrect(env):
    older = await manual_quote(env, "offline-older-manual-quote")
    env.clock[0] += timedelta(seconds=1)
    env.jobs.start("CASE", request(env))
    result = await run_next(env.jobs)
    assert result["status"] == "completed", result
    resource = next(item for item in env.store.read().resources if item.source == SOURCE)
    assert resource.provenance["quote_sequence"] > older["quote_sequence"]
    with pytest.raises(DomainError, match="newer source revision"):
        manual_import(env, older, "offline-refuse-older-after-job")
    assert len(env.source.calls) == 4
    assert [item.id for item in resource_history(env.store.read(), source=SOURCE, group_id="G1")] == [resource.id]


@pytest.mark.asyncio
async def test_new_manual_import_during_decisions_is_preserved_when_job_commit_is_stale(env):
    saved = {}
    async def newer_import():
        env.clock[0] += timedelta(seconds=1)
        quoted = await manual_quote(env, "offline-newer-manual-quote")
        saved.update(manual_import(env, quoted, "offline-newer-manual-import"))
    env.ai.before_choose = newer_import
    env.jobs.start("CASE", request(env))
    result = await run_next(env.jobs)
    assert result["status"] == "needs_review", result
    assert result["calls_dispatched"] == 3 and len(env.source.calls) == 4
    resources = [item for item in env.store.read().resources if item.source == SOURCE]
    assert [item.id for item in resources] == saved["resource_ids"]
    assert not env.store.read().offers and not env.store.read().reservations


@pytest.mark.asyncio
async def test_fixed_other_group_cab_skips_new_source_without_reservation_row(env):
    def relocate_other(world):
        world.groups[1].origin, world.groups[1].destination = "OTP", "CRA"
    change(env.store, relocate_other)
    quoted = await manual_quote(env, "offline-fixed-other-quote", "G2")
    manual_import(env, quoted, "offline-fixed-other-import")
    def fixed(world):
        itinerary = solver.itinerary_options(world, world.groups[1])[0]
        world.offers.append(Offer(id="FIXED-OTHER", group_id="G2", itinerary=itinerary, status="proposed",
            expires_at=(NOW + timedelta(seconds=120)).isoformat(), created_version=world.version))
    change(env.store, fixed)
    before = env.store.read()
    env.jobs.start("CASE", request(env))
    result = await run_next(env.jobs)
    assert result["status"] == "blocked", result
    assert result["calls_dispatched"] == 0 and len(env.source.calls) == 2
    assert any(item["code"] == "airport_transfer_fleet_unverified" for item in result["gaps"])
    assert stable(env.store.read()) == stable(before)
    assert not env.ai.decisions and not before.reservations


@pytest.mark.asyncio
async def test_two_selected_groups_never_allocate_two_unreserved_vehicles(env):
    def select_other(world):
        group = world.groups[1]
        group.origin, group.destination, group.incident_id = "OTP", "CRA", "CASE"
        world.incidents[0].group_ids.append(group.id)
    change(env.store, select_other)
    env.jobs.start("CASE", request(env, group_ids=["G1", "G2"], max_provider_calls=5,
        airport_transfers=[transfer("G1"), transfer("G2")]))
    result = await run_next(env.jobs)
    assert result["status"] == "completed", result
    assert result["calls_dispatched"] == 5 and len(env.source.calls) == 4
    assert result["result"]["recovered_passengers"] == result["result"]["exception_passengers"] == 2
    world = env.store.read()
    assert len(world.offers) == 1
    assert len([item for item in world.resources if item.source == SOURCE]) == 1
    assert not any(item.booking_status == "confirmed" for item in world.resources)


@pytest.mark.asyncio
async def test_atomic_completion_failure_rolls_back_all_staged_source_and_offer_changes(env, monkeypatch):
    before = env.store.read()
    def fail_commit(*args):
        raise DomainError(409, "Offline final receipt failure")
    monkeypatch.setattr(env.jobs.ledger, "complete_in_transaction", fail_commit)
    env.jobs.start("CASE", request(env))
    result = await run_next(env.jobs)
    assert result["status"] == "needs_review" and result["calls_dispatched"] == 3, result
    assert stable(env.store.read()) == stable(before)
    with env.store.connect() as conn:
        assert not conn.execute("SELECT 1 FROM idempotency WHERE key LIKE 'recovery-job-apply:%'").fetchone()
    assert len(env.source.calls) == 2


@pytest.mark.asyncio
async def test_capacity_does_not_archive_untouched_out_of_scope_resource(env, monkeypatch):
    before = env.store.read()
    monkeypatch.setattr(inventory, "MAX_RESOURCES", len(before.resources))
    env.jobs.start("CASE", request(env))
    result = await run_next(env.jobs)
    assert result["status"] == "needs_review", result
    assert "capacity" in result["message"].lower()
    assert result["calls_dispatched"] == 3
    assert stable(env.store.read()) == stable(before)
    assert not resource_history(env.store.read(), resource_id="UNTOUCHED")[0].available
    assert len(env.ai.decisions) == 1


@pytest.mark.asyncio
async def test_structured_arrival_queues_post_report_cab_scope_and_replays_without_calls(env):
    change(env.store, lambda world: setattr(world.groups[0], "origin", "LHR"))
    before = env.store.read()
    body = ArrivalReportInput(expected_version=before.version, idempotency_key="offline-arrival-cab",
        group_ids=["G1"], airport_code="OTP", ready_at=PICKUP,
        description="Operator reports these passengers ready at Otopeni.",
        recovery={"modes": ["bus"], "max_provider_calls": 3, "airport_transfers": [transfer()]})
    reports = OperationalReports(env.store, env.jobs)
    receipt = reports.arrival("CASE", body)
    assert receipt["applied"] and receipt["command_interpretation_calls"] == 0
    assert receipt["recovery_job"]["group_ids"] == ["G1"]
    assert receipt["recovery_job"]["calls_dispatched"] == 0 and not env.source.calls
    result = await run_next(env.jobs)
    assert result["status"] == "completed" and result["calls_dispatched"] == 3, result
    world = env.store.read()
    assert world.groups[0].origin == "OTP" and world.groups[0].ready_at == 600
    assert world.groups[1] == before.groups[1]
    resource = next(item for item in world.resources if item.source == SOURCE)
    assert resource.provenance["airport_cab_context"] == evidence.airport_cab_context(world, world.groups[0])
    replay = reports.arrival("CASE", body)
    assert replay["replayed"] and replay["recovery_job"]["id"] == receipt["recovery_job"]["id"]
    assert len(env.source.calls) == 2 and env.jobs.ledger.claim_next() is None


@pytest.mark.asyncio
async def test_arrival_with_pickup_before_new_readiness_keeps_report_but_queues_nothing(env):
    body = ArrivalReportInput(expected_version=env.store.read().version,
        idempotency_key="offline-arrival-invalidated-pickup", group_ids=["G1"], airport_code="OTP",
        ready_at=PICKUP + timedelta(minutes=1), description="Passengers will be ready one minute later.",
        recovery={"modes": ["bus"], "max_provider_calls": 3, "airport_transfers": [transfer()]})
    receipt = OperationalReports(env.store, env.jobs).arrival("CASE", body)
    assert receipt["applied"] and receipt["recovery_blocked"]["code"] == "airport_transfer_setup_required"
    assert "recovery_job" not in receipt and env.store.read().groups[0].ready_at == 601
    assert not env.jobs.list("CASE")["jobs"] and not env.source.calls


@pytest.mark.asyncio
async def test_restriction_report_queues_exact_scope_with_frozen_transfer_inputs(env):
    before = env.store.read()
    body = AirportRestrictionReportInput(expected_version=before.version,
        idempotency_key="offline-restriction-valid-cab", airport_code="SBZ", operations=["arrivals"],
        action="restrict", description="Operator restricts Sibiu arrivals for this case.",
        recovery={"group_ids": ["G1"], "modes": ["bus"], "max_provider_calls": 3,
            "airport_transfers": [transfer()]})
    reports = AirportRestrictions(env.store, env.jobs)
    receipt = reports.report("CASE", body)
    assert receipt["applied"] and receipt["command_interpretation_calls"] == 0
    job = receipt["recovery_job"]
    assert job["group_ids"] == receipt["report"]["recovery_group_ids"] == ["G1"]
    assert job["airport_transfers"] == body.recovery.model_dump(mode="json")["airport_transfers"]
    assert job["source_version"] == before.version + 1
    assert job["status"] == "queued" and job["calls_dispatched"] == 0 and job["max_provider_calls"] == 3
    after = env.store.read()
    assert after.groups == before.groups and after.resources == before.resources
    assert any(item.airport_code == "SBZ" and item.operation == "arrivals"
        for item in after.incidents[0].airport_restrictions)
    replay = reports.report("CASE", body)
    assert replay["replayed"] and replay["recovery_job"]["id"] == job["id"]
    assert len(env.jobs.list("CASE")["jobs"]) == 1 and not env.source.calls
    assert not env.ai.plans and not env.ai.decisions and not env.flights.calls


@pytest.mark.asyncio
async def test_restriction_with_ineligible_or_out_of_scope_transfer_preserves_report_without_job(env):
    reports = AirportRestrictions(env.store, env.jobs)
    for reason in ("ineligible_origin", "outside_subset"):
        change(env.store, lambda world: setattr(world.groups[0], "origin", "LHR" if reason == "ineligible_origin" else "OTP"))
        before = env.store.read()
        body = AirportRestrictionReportInput(expected_version=before.version,
            idempotency_key="offline-restriction-" + reason, airport_code="SBZ", operations=["arrivals"],
            action="restrict", description="Operator restricts Sibiu arrivals for this case.",
            recovery={"group_ids": ["G1"], "modes": ["bus"], "max_provider_calls": 3,
                "airport_transfers": [transfer("G1" if reason == "ineligible_origin" else "G2")]})
        receipt = reports.report("CASE", body)
        assert receipt["applied"] and receipt["report"]["recovery_group_ids"] == ["G1"]
        assert receipt["recovery_blocked"]["code"] == "airport_transfer_setup_required"
        assert "recovery_job" not in receipt
        after = env.store.read()
        assert after.version == before.version + 1
        assert after.groups == before.groups and after.resources == before.resources
        assert any(item.id == receipt["report"]["id"] for item in after.incidents[0].airport_restriction_reports)
        assert any(item.airport_code == "SBZ" and item.operation == "arrivals"
            for item in after.incidents[0].airport_restrictions)
        replay = reports.report("CASE", body)
        assert replay["replayed"] and replay["recovery_blocked"] == receipt["recovery_blocked"]
        assert not env.jobs.list("CASE")["jobs"] and not env.source.calls
        assert not env.ai.plans and not env.ai.decisions and not env.flights.calls


@pytest.mark.asyncio
async def test_unknown_cab_http_outcome_blocks_arrival_successor_without_duplicate_dispatch(env):
    env.source.pause_phase = env.source.timeout_phase = "vehicles"
    started = env.jobs.start("CASE", request(env))["job"]
    running = asyncio.create_task(run_next(env.jobs))
    await asyncio.wait_for(env.source.entered.wait(), 2)
    try:
        body = ArrivalReportInput(expected_version=env.store.read().version,
            idempotency_key="offline-arrival-after-uncertain-cab", group_ids=["G1"], airport_code="OTP",
            ready_at=PICKUP + timedelta(minutes=10), description="Updated reported readiness at Otopeni.",
            recovery={"modes": ["bus"], "max_provider_calls": 3,
                "airport_transfers": [transfer(pickup_at=PICKUP + timedelta(hours=1))]})
        receipt = OperationalReports(env.store, env.jobs).arrival("CASE", body)
        successor = receipt["recovery_job"]
        assert started["id"] in successor["supersedes_job_ids"]
        assert successor["calls_dispatched"] == 0
    finally:
        env.source.release.set()
    result = await running
    assert result["status"] == "unknown_outcome", result
    assert result["calls_dispatched"] == 2 and any(step["status"] == "unknown" for step in result["steps"])
    assert env.jobs.ledger.claim_next() is None
    final = env.jobs.get(successor["id"])["job"]
    assert final["status"] == "needs_review" and final["calls_dispatched"] == 0
    assert len(env.source.calls) == 2 and not env.ai.decisions and not env.flights.calls
    assert not env.store.read().offers and not env.store.read().reservations


def no_fitting_luggage(payload):
    for vehicle in payload["vehicle_types"]:
        vehicle["suitcases"] = vehicle["handbags"] = 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_inputs", [
    {}, {"suitcases": 3}, {"handbags": 3}, {"pickup_at": PICKUP + timedelta(hours=1)},
])
async def test_new_no_fit_quote_cannot_fall_back_to_existing_cab_even_for_identical_inputs(env, changed_inputs):
    old = await manual_quote(env, "offline-prior-cab-request")
    manual_import(env, old, "offline-prior-cab-import")
    before = env.store.read()
    env.source.mutate_payload = no_fitting_luggage
    env.jobs.start("CASE", request(env, airport_transfers=[transfer(**changed_inputs)]))
    result = await run_next(env.jobs)
    assert result["status"] == "blocked", result
    assert result["calls_dispatched"] == 2 and len(env.source.calls) == 4
    assert not env.ai.decisions and not env.ai.plans and not env.flights.calls
    assert any(item["code"] == "airport_transfer_not_usable" for item in result["gaps"])
    assert stable(env.store.read()) == stable(before)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unknown", "invalid_response"])
async def test_failed_fresh_source_never_allocates_or_retires_old_cab(env, failure):
    old = await manual_quote(env, "offline-before-source-error")
    manual_import(env, old, "offline-before-source-error-import")
    before = env.store.read()
    if failure == "unknown":
        env.source.timeout_phase = "vehicles"
    else:
        env.source.mutate_payload = lambda payload: payload.pop("timezone")
    env.jobs.start("CASE", request(env, airport_transfers=[transfer(suitcases=3)]))
    result = await run_next(env.jobs)
    assert result["status"] == ("unknown_outcome" if failure == "unknown" else "failed"), result
    assert result["calls_dispatched"] == 2 and len(env.source.calls) == 4
    assert not env.ai.decisions and stable(env.store.read()) == stable(before)


@pytest.mark.asyncio
async def test_no_fit_search_preserves_other_group_cab_and_authoritative_old_quote(env):
    def relocate_other(world):
        world.groups[1].origin, world.groups[1].destination = "OTP", "CRA"
    change(env.store, relocate_other)
    selected = await manual_quote(env, "offline-selected-existing-cab")
    manual_import(env, selected, "offline-selected-existing-import")
    other = await manual_quote(env, "offline-other-existing-cab", "G2")
    imported_other = manual_import(env, other, "offline-other-existing-import")
    before = env.store.read()
    env.source.mutate_payload = no_fitting_luggage
    env.jobs.start("CASE", request(env))
    result = await run_next(env.jobs)
    assert result["status"] == "blocked" and result["calls_dispatched"] == 2
    assert len(env.source.calls) == 6 and stable(env.store.read()) == stable(before)
    assert next(item for item in env.store.read().resources if item.id == imported_other["resource_ids"][0]).available
    excluded = next(item for item in result["gaps"] if item["code"] == "airport_transfer_previous_quote_excluded")
    assert excluded["group_ids"] == ["G1"]


@pytest.mark.asyncio
async def test_job_without_explicit_cab_search_can_use_current_existing_quote(env):
    old = await manual_quote(env, "offline-current-existing-cab")
    imported = manual_import(env, old, "offline-current-existing-import")
    env.jobs.start("CASE", request(env, airport_transfers=[]))
    result = await run_next(env.jobs)
    assert result["status"] == "completed" and result["calls_dispatched"] == 1, result
    assert len(env.source.calls) == 2 and len(env.ai.decisions) == 1
    assert result["result"]["resource_ids"] == imported["resource_ids"]


@pytest.mark.asyncio
async def test_successful_fresh_request_uses_new_category_and_retires_old_revision(env):
    old = await manual_quote(env, "offline-old-before-refresh")
    imported = manual_import(env, old, "offline-old-before-refresh-import")
    env.jobs.start("CASE", request(env, airport_transfers=[transfer(handbags=3)]))
    result = await run_next(env.jobs)
    assert result["status"] == "completed" and result["calls_dispatched"] == 3, result
    assert len(env.source.calls) == 4
    assert not set(imported["resource_ids"]) & set(result["result"]["resource_ids"])
    current = next(item for item in env.store.read().resources if item.source == SOURCE and item.available)
    assert current.provenance["airport_cab_quote"]["request"]["handbags"] == 3
    assert current.quote_total_cost == 212
    assert any(item.id in imported["resource_ids"] and not item.available
        for item in resource_history(env.store.read(), source=SOURCE, group_id="G1"))


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["not_fresh", "suitcases", "handbags", "pickup_at"])
async def test_independent_commit_guard_requires_fresh_category_and_exact_request(env, mismatch):
    old = await manual_quote(env, "offline-guard-cab")
    imported = manual_import(env, old, "offline-guard-cab-import")
    world = env.store.read()
    plan = solver.build_candidate(world, Strategy(id="guard", label="Guard", objective="balanced"), scope_group_ids={"G1"})
    assert plan.recovered_passengers == 2 and plan.validation["valid"]
    values = {} if mismatch == "not_fresh" else {mismatch: PICKUP + timedelta(hours=1) if mismatch == "pickup_at" else 3}
    with pytest.raises(DomainError) as error:
        validate_airport_transfer_selection(world, plan, [transfer(**values)],
            fresh_resource_ids=[] if mismatch == "not_fresh" else imported["resource_ids"])
    assert error.value.details["code"] == "airport_transfer_request_mismatch"
    assert stable(env.store.read()) == stable(world)
    # Offset spelling does not alter the exact instant; this guard receives its
    # fresh-ID allowlist from the worker, independently of source validation.
    validate_airport_transfer_selection(world, plan,
        [transfer(pickup_at="2030-01-04T12:00:00+02:00")], fresh_resource_ids=imported["resource_ids"])
