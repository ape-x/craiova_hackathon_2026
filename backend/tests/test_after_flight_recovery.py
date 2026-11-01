"""Automatic incoming-flight/airport-vehicle composition, offline source fixtures only."""
import asyncio
import copy
from datetime import timedelta
import json

import pytest
import pytest_asyncio
from pydantic import ValidationError

from app.after_flight_inputs import AfterFlightTransferInput
from app.airport_cab_profile import AirportCabProfileError
from app.models import Strategy
from app.models import Offer
from app.operational_reports import ArrivalReportInput, OperationalReports
from app.providers.base import ProviderError
from app.providers.bucharest_cab import SOURCE
from app.recovery_jobs import RecoveryCancellation, RecoveryJobRequest
from app.store import DomainError
from test_airport_cab_recovery_jobs import NOW, FakeFlights, change, env, manual_import, manual_quote, run_next, stable


def connection(**changes):
    return {"group_id": "G1", "via_airport": "OTP", "destination_airport": "CRA",
        "pickup_window_start": NOW+timedelta(hours=10), "pickup_window_end": NOW+timedelta(hours=18),
        "airport_processing_minutes": 30, "suitcases": 0, "handbags": 0, "max_inbound_options": 2} | changes


def request(env, **changes):
    return RecoveryJobRequest(**({"expected_version": env.store.read().version, "idempotency_key": "offline-postflight-job",
        "group_ids": ["G1"], "modes": ["flight", "bus"], "max_provider_calls": 6,
        "after_flight_transfer": connection()} | changes))


class IncomingAI:
    configured = True

    def __init__(self):
        self.plans, self.shortlists, self.decisions = [], [], []
        self.before_plan = self.before_shortlist = self.before_choose = None
        self.selected_override = None

    async def plan_recovery_queries(self, world, options, request):
        self.plans.append((world, options, request))
        if self.before_plan:
            await self.before_plan()
        return {"query_ids": [options[0]["id"]], "strategies": [Strategy(id=value, label=value, objective=value).model_dump()
            for value in ("balanced", "fastest", "lowest_cost")], "explanation": "Offline incoming query"}

    async def select_airport_transfer_flights(self, world, candidates, request):
        self.shortlists.append((world, candidates, request))
        if self.before_shortlist:
            await self.before_shortlist()
        ids = [item["resource_id"] for item in candidates[:request["max_inbound_options"]]]
        return {"flight_resource_ids": self.selected_override if self.selected_override is not None else ids,
            "explanation": "Offline exact incoming choices"}

    async def choose(self, world, candidates, request):
        self.decisions.append((world, candidates, request))
        if self.before_choose:
            await self.before_choose()
        chosen = min(candidates, key=lambda item: item["total_cost"])
        return {"plan_id": chosen["id"], "provider": "Offline Decisions", "explanation": "Exact complete source pair"}


class IncomingFlights(FakeFlights):
    count = 1
    mutate_quote = None

    async def search(self, request):
        result = await super().search(request)
        first = result["items"][0]
        for index in range(1, self.count):
            item = copy.deepcopy(first)
            item["id"] += "-"+str(index)
            item["provider_offer_id"] = item["id"]
            item["total_price"] = str(100+index*10)+".00"
            result["items"].append(item)
        if self.mutate_quote:
            for index, item in enumerate(result["items"]):
                self.mutate_quote(index, item)
        return result


@pytest_asyncio.fixture
async def setup(env):
    def prepare(world):
        world.groups[0].origin = "LHR"
        world.groups[0].ready_at = 0
        world.incidents[0].unavailable_airport_code = "CRA"
        world.incidents[0].description = "CRA air operations unavailable; explicitly ready passengers at LHR."
    change(env.store, prepare)
    env.ai = IncomingAI()
    env.flights = IncomingFlights(env.clock)
    env.jobs.ai = env.ai
    env.jobs.travel = env.flights
    return env


@pytest.mark.asyncio
async def test_complete_six_call_pipeline_commits_only_exact_chosen_pair(setup):
    e = setup
    before = e.store.read()
    started = e.jobs.start("CASE", request(e))
    assert started["job"]["after_flight_transfer"] == request(e).after_flight_transfer.model_dump(mode="json")
    assert not e.source.calls and not e.flights.calls
    job = await run_next(e.jobs)
    assert job["status"] == "completed", job
    assert job["calls_dispatched"] == 6
    assert [item["stage"] for item in job["steps"]] == ["planning_queries", "searching_flights", "selecting_incoming_flights", "searching_airport_transfer", "searching_airport_transfer", "deciding"]
    assert len(e.source.calls) == 2 and len(e.flights.calls) == 1
    assert e.ai.shortlists[0][2]["max_inbound_options"] == 1
    after = e.store.read()
    assert after.groups[0].origin == "LHR" and after.groups[0].ready_at == 0
    assert after.groups[1] == before.groups[1]
    assert next(item for item in after.resources if item.id == "UNTOUCHED") == before.resources[0]
    assert len(after.resources) == 3
    chosen = job["result"]["after_flight_transfer"]
    assert set(job["result"]["resource_ids"]) == {chosen["incoming_resource_id"], chosen["transfer_resource_id"]}
    cab = next(item for item in after.resources if item.source == SOURCE)
    assert cab.provenance["postflight_connection"]["incoming_resource_id"] == chosen["incoming_resource_id"]
    assert cab.provenance["airport_cab_quote"]["request"]["pickup_at"] == (NOW+timedelta(hours=15, minutes=30)).isoformat()
    assert cab.provenance["airport_cab_quote"]["request"]["suitcases"] == 0
    assert cab.provenance["direction"] == "after_flight_airport_to_airport"
    assert cab.booking_status == "unbooked" and all(item.status == "proposed" for item in after.reservations)
    assert after.offers[0].itinerary.legs[1].postflight_connection == cab.provenance["postflight_connection"]
    assert job["airport_transfer_quotes"][0]["postflight_connection"] == cab.provenance["postflight_connection"]
    with pytest.raises(AirportCabProfileError):
        e.jobs.airport_cab_profiles.read(after, after.groups[0])
    for context in (e.ai.plans[0][2]["context"], e.ai.shortlists[0][2]["context"], e.ai.decisions[0][2]["context"]):
        assert "not observed passenger arrivals" in context
        assert "unreserved" in context and "without departure-time traffic" in context and "Fleet stock" in context
    assert "b"*32 not in json.dumps(job)


@pytest.mark.asyncio
async def test_two_same_pickup_receipts_preserve_earlier_candidate_and_chosen_only_commit(setup):
    e = setup
    e.flights.count = 2
    e.jobs.start("CASE", request(e, max_provider_calls=8))
    job = await run_next(e.jobs)
    assert job["status"] == "completed", job
    assert job["calls_dispatched"] == 8 and len(e.source.calls) == 4
    assert len(job["airport_transfer_quotes"]) == 2
    first, second = job["airport_transfer_quotes"]
    assert first["quote_id"] != second["quote_id"] and first["pickup_at"] == second["pickup_at"]
    # Cheaper first incoming must survive the later receipt without a shared
    # supplier hold, fabricated fleet identity, or retirement of its branch.
    assert job["result"]["after_flight_transfer"]["incoming_resource_id"] == first["postflight_connection"]["incoming_resource_id"]
    after = e.store.read()
    assert len(after.resources) == 3
    assert next(item for item in after.resources if item.source == SOURCE).provenance["quote_id"] == first["quote_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("values", [{"max_provider_calls": 5}, {"group_ids": ["G1", "G2"]}, {"modes": ["bus"]},
    {"airport_transfers": [{"group_id": "G1", "destination_airport": "CRA", "pickup_at": NOW+timedelta(hours=10), "suitcases": 0, "handbags": 0}]}])
async def test_preflight_rejects_scope_mode_budget_or_initial_mix_without_calls(setup, values):
    e = setup
    with pytest.raises((DomainError, ValidationError)):
        e.jobs.start("CASE", request(e, **values))
    assert not e.source.calls and not e.flights.calls and not e.ai.plans
    assert e.jobs.list("CASE")["jobs"] == []


@pytest.mark.asyncio
async def test_no_implicit_afterflight_field_changes_old_serialization(setup):
    req = RecoveryJobRequest(expected_version=setup.store.read().version, idempotency_key="offline-old-shape", group_ids=["G1"])
    assert "after_flight_transfer" not in req.model_dump()
    assert "after_flight_transfer" not in ArrivalReportInput(expected_version=1, idempotency_key="offline-arrival-shape",
        group_ids=["G1"], airport_code="LHR", ready_at=NOW, description="Reported ready").model_dump()


@pytest.mark.asyncio
@pytest.mark.parametrize("elapsed", [120, 121])
async def test_incoming_expires_during_shortlist_spends_no_vehicle_or_decision(setup, elapsed):
    e = setup
    async def expire():
        e.clock[0] += timedelta(seconds=elapsed)
    e.ai.before_shortlist = expire
    before = stable(e.store.read())
    e.jobs.start("CASE", request(e))
    job = await run_next(e.jobs)
    assert job["status"] == "blocked", job
    assert job["calls_dispatched"] == 3 and not e.source.calls and not e.ai.decisions
    assert stable(e.store.read()) == before
    assert any(item["code"] == "incoming_flight_expired" for item in job["gaps"])


@pytest.mark.asyncio
async def test_version_change_between_source_phases_stops_and_preserves_new_state(setup):
    e = setup
    e.source.after_bootstrap = lambda: change(e.store, lambda world: setattr(world.groups[1], "budget", 777))
    e.jobs.start("CASE", request(e))
    job = await run_next(e.jobs)
    assert job["status"] == "needs_review", job
    assert job["calls_dispatched"] == 4 and [item[0] for item in e.source.calls] == ["bootstrap"]
    assert e.store.read().groups[1].budget == 777
    assert len(e.store.read().resources) == 1 and not e.store.read().offers


@pytest.mark.asyncio
async def test_unknown_vehicle_outcome_prevents_second_candidate_and_decision(setup):
    e = setup
    e.flights.count = 2
    e.source.timeout_phase = "vehicles"
    e.jobs.start("CASE", request(e, max_provider_calls=8))
    job = await run_next(e.jobs)
    assert job["status"] == "unknown_outcome", job
    assert job["calls_dispatched"] == 5 and len(e.source.calls) == 2 and not e.ai.decisions
    assert job["steps"][-1]["status"] == "unknown"
    assert len(e.store.read().resources) == 1 and not e.store.read().offers


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError(), ProviderError("Offline server failure", provider=SOURCE, code="http_507")])
async def test_all_ambiguous_timeouts_and_server_failures_are_unknown(setup, error):
    e = setup
    e.flights.count = 2
    def fail(payload):
        raise error
    e.source.mutate_payload = fail
    e.jobs.start("CASE", request(e, max_provider_calls=8))
    job = await run_next(e.jobs)
    assert job["status"] == "unknown_outcome", job
    assert job["calls_dispatched"] == 5 and len(e.source.calls) == 2
    assert job["steps"][-1]["status"] == "unknown" and not e.ai.decisions
    with e.store.connect() as conn:
        assert conn.execute("SELECT status,http_requests FROM airport_cab_receipts").fetchall() == [("unknown", 2)]


@pytest.mark.asyncio
@pytest.mark.parametrize("semantic", ["no_vehicle_types_found", "min_advance_day_booking"])
async def test_known_semantic_rejection_can_use_only_other_shortlisted_flight(setup, semantic):
    e = setup
    e.flights.count = 2
    returned = 0
    def fail_once(payload):
        nonlocal returned
        returned += 1
        if returned == 1:
            payload.clear()
            payload["error"] = semantic
    e.source.mutate_payload = fail_once
    e.jobs.start("CASE", request(e, max_provider_calls=8))
    job = await run_next(e.jobs)
    assert job["status"] == "completed", job
    assert job["calls_dispatched"] == 8 and len(e.source.calls) == 4
    assert job["steps"][4]["status"] == "failed" and job["steps"][6]["status"] == "succeeded"
    assert job["result"]["after_flight_transfer"]["incoming_resource_id"] == e.ai.shortlists[0][1][1]["resource_id"]
    assert len(job["airport_transfer_quotes"]) == 1
    assert any(item["code"] == "airport_transfer_source_rejected" for item in job["gaps"])


@pytest.mark.asyncio
async def test_generic_invalid_response_is_not_a_second_candidate_permission(setup):
    e = setup
    e.flights.count = 2
    e.source.mutate_payload = lambda payload: payload.clear()
    e.jobs.start("CASE", request(e, max_provider_calls=8))
    job = await run_next(e.jobs)
    assert job["status"] == "failed", job
    assert job["calls_dispatched"] == 5 and len(e.source.calls) == 2 and not e.ai.decisions


@pytest.mark.asyncio
async def test_cancellation_between_vehicle_phases_prevents_further_source_calls(setup):
    e = setup
    started = e.jobs.start("CASE", request(e))
    e.source.after_bootstrap = lambda: e.jobs.cancel(started["job"]["id"], RecoveryCancellation(idempotency_key="offline-cancel-postflight"))
    before = stable(e.store.read())
    job = await run_next(e.jobs)
    assert job["status"] == "cancelled", job
    assert job["calls_dispatched"] == 4 and len(e.source.calls) == 1 and not e.ai.decisions
    assert stable(e.store.read()) == before


@pytest.mark.asyncio
async def test_expiry_during_final_decision_rolls_back_chosen_flight_and_vehicle(setup):
    e = setup
    async def expire():
        e.clock[0] += timedelta(seconds=121)
    e.ai.before_choose = expire
    before = stable(e.store.read())
    e.jobs.start("CASE", request(e))
    job = await run_next(e.jobs)
    assert job["status"] == "needs_review" and job["calls_dispatched"] == 6, job
    assert stable(e.store.read()) == before
    with e.store.connect() as conn:
        assert not conn.execute("SELECT 1 FROM idempotency WHERE key LIKE 'recovery-job-apply:%'").fetchone()


@pytest.mark.asyncio
async def test_fixed_other_group_vehicle_blocks_before_any_new_call(setup):
    import app.solver as solver
    e = setup
    def relocate(world):
        world.groups[1].origin, world.groups[1].destination = "OTP", "CRA"
    change(e.store, relocate)
    quoted = await manual_quote(e, "offline-fixed-after-flight", "G2")
    manual_import(e, quoted, "offline-fixed-after-flight-import")
    def fixed(world):
        itinerary = solver.itinerary_options(world, world.groups[1])[0]
        world.offers.append(Offer(id="FIXED-OTHER", group_id="G2", itinerary=itinerary, status="proposed",
            expires_at=(NOW+timedelta(seconds=120)).isoformat(), created_version=world.version))
    change(e.store, fixed)
    before = stable(e.store.read())
    e.jobs.start("CASE", request(e))
    job = await run_next(e.jobs)
    assert job["status"] == "blocked" and job["calls_dispatched"] == 0, job
    assert len(e.source.calls) == 2 and not e.flights.calls and not e.ai.plans
    assert stable(e.store.read()) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,expected", [(3, "completed"), (2, "needs_review")])
async def test_persistent_capacity_counts_chosen_pair_not_ephemeral_category_alternatives(setup, monkeypatch, limit, expected):
    import app.inventory_lifecycle as inventory
    e = setup
    e.flights.count = 2
    monkeypatch.setattr(inventory, "MAX_RESOURCES", limit)
    before = stable(e.store.read())
    e.jobs.start("CASE", request(e, max_provider_calls=8))
    job = await run_next(e.jobs)
    assert job["status"] == expected and job["calls_dispatched"] == 8, job
    if expected == "needs_review":
        assert "capacity" in job["message"].lower()
        assert stable(e.store.read()) == before
    else:
        assert len(e.store.read().resources) == 3
        assert e.store.read().resources[0].id == "UNTOUCHED"


@pytest.mark.asyncio
async def test_arrival_then_afterflight_search_is_atomic_and_same_key_replay_never_requeues(setup):
    e = setup
    change(e.store, lambda world: setattr(world.groups[0], "origin", "CRA"))
    body = ArrivalReportInput(expected_version=e.store.read().version, idempotency_key="offline-arrival-then-incoming",
        group_ids=["G1"], airport_code="LHR", ready_at=NOW+timedelta(minutes=1),
        description="Operator reports passengers ready at London Heathrow.",
        recovery={"modes": ["flight", "bus"], "max_provider_calls": 6, "after_flight_transfer": connection()})
    reports = OperationalReports(e.store, e.jobs)
    receipt = reports.arrival("CASE", body)
    assert receipt["applied"] and receipt["command_interpretation_calls"] == 0
    assert receipt["recovery_job"]["after_flight_transfer"] == AfterFlightTransferInput(**connection()).model_dump(mode="json")
    assert receipt["recovery_job"]["group_ids"] == ["G1"] and not e.source.calls
    job = await run_next(e.jobs)
    assert job["status"] == "completed", job
    assert e.store.read().groups[0].origin == "LHR" and e.store.read().groups[0].ready_at == 1
    replay = reports.arrival("CASE", body)
    assert replay["replayed"] and replay["recovery_job"]["id"] == receipt["recovery_job"]["id"]
    assert e.jobs.ledger.claim_next() is None and len(e.source.calls) == 2


@pytest.mark.asyncio
async def test_valid_arrival_preserved_when_new_location_ineligible_for_afterflight(setup):
    e = setup
    body = ArrivalReportInput(expected_version=e.store.read().version, idempotency_key="offline-arrival-not-afterflight",
        group_ids=["G1"], airport_code="OTP", ready_at=NOW+timedelta(minutes=1), description="Passengers are at OTP.",
        recovery={"modes": ["flight", "bus"], "max_provider_calls": 6, "after_flight_transfer": connection()})
    receipt = OperationalReports(e.store, e.jobs).arrival("CASE", body)
    assert receipt["applied"] and receipt["recovery_blocked"]["code"] == "airport_transfer_setup_required"
    assert "recovery_job" not in receipt and not e.jobs.list("CASE")["jobs"]
    assert e.store.read().groups[0].origin == "OTP" and not e.source.calls and not e.ai.plans


@pytest.mark.asyncio
async def test_unexpected_arrival_enqueue_failure_rolls_back_report_and_job(setup, monkeypatch):
    e = setup
    before = stable(e.store.read())
    original = e.jobs.start_in_transaction
    def fail_after_create(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Offline unexpected queue failure")
    monkeypatch.setattr(e.jobs, "start_in_transaction", fail_after_create)
    body = ArrivalReportInput(expected_version=e.store.read().version, idempotency_key="offline-arrival-rollback",
        group_ids=["G1"], airport_code="LHR", ready_at=NOW+timedelta(minutes=1), description="Explicit readiness update.",
        recovery={"modes": ["flight", "bus"], "max_provider_calls": 6, "after_flight_transfer": connection()})
    with pytest.raises(RuntimeError):
        OperationalReports(e.store, e.jobs).arrival("CASE", body)
    assert stable(e.store.read()) == before and not e.jobs.list("CASE")["jobs"]
    assert not e.source.calls and not e.ai.plans


@pytest.mark.asyncio
async def test_unknown_shortlist_identity_is_not_replaced_by_a_scripted_candidate(setup):
    e = setup
    e.ai.selected_override = ["NOT-A-RETURNED-RESOURCE"]
    e.jobs.start("CASE", request(e))
    job = await run_next(e.jobs)
    assert job["status"] == "needs_review" and job["calls_dispatched"] == 3, job
    assert not e.source.calls and not e.ai.decisions


@pytest.mark.asyncio
@pytest.mark.parametrize("attribute,value", [("adult_count", 1), ("accessible", True), ("destination", "LHR"), ("origin", "OTP"), ("ready_at", 601)])
async def test_current_group_prerequisites_rejected_without_calls(setup, attribute, value):
    e = setup
    change(e.store, lambda world: setattr(world.groups[0], attribute, value))
    with pytest.raises(DomainError) as error:
        e.jobs.start("CASE", request(e))
    assert error.value.status == 422
    assert not e.source.calls and not e.ai.plans and not e.jobs.list("CASE")["jobs"]


@pytest.mark.asyncio
async def test_window_start_is_not_an_expiry_during_current_source_decision(setup):
    e = setup
    async def cross_lower_bound():
        e.clock[0] += timedelta(seconds=90)
    e.ai.before_choose = cross_lower_bound
    e.jobs.start("CASE", request(e, after_flight_transfer=connection(pickup_window_start=NOW+timedelta(minutes=1))))
    job = await run_next(e.jobs)
    assert job["status"] == "completed", job
    assert job["calls_dispatched"] == 6
    assert job["airport_transfer_quotes"][0]["pickup_at"] == (NOW+timedelta(hours=15, minutes=30)).isoformat()


@pytest.mark.asyncio
@pytest.mark.parametrize("later_start", [False, True])
async def test_derived_pickup_uses_exact_source_seconds_and_declared_window_lower_bound(setup, later_start):
    from datetime import datetime
    e = setup
    def exact_second(index, quote):
        arrival = datetime.fromisoformat(quote["arrival_at"])+timedelta(seconds=1)
        quote["arrival_at"] = quote["segments"][-1]["arrival_at"] = arrival.isoformat()
    e.flights.mutate_quote = exact_second
    start = NOW+timedelta(hours=16 if later_start else 10)
    e.jobs.start("CASE", request(e, after_flight_transfer=connection(pickup_window_start=start)))
    job = await run_next(e.jobs)
    assert job["status"] == "completed", job
    expected = start if later_start else NOW+timedelta(hours=15, minutes=31)
    evidence = job["airport_transfer_quotes"][0]["postflight_connection"]
    assert evidence["source_arrival_at"] == (NOW+timedelta(hours=15, seconds=1)).isoformat()
    assert evidence["derived_pickup_at"] == expected.isoformat()
    assert datetime.fromisoformat(job["result"]["after_flight_transfer"]["inputs"]["pickup_window_start"]) == start


@pytest.mark.asyncio
async def test_actual_source_lead_rule_does_not_shift_early_derived_pickup(setup):
    e = setup
    def early(index, quote):
        quote["departure_at"] = quote["segments"][0]["departure_at"] = (NOW+timedelta(hours=5)).isoformat()
        quote["arrival_at"] = quote["segments"][-1]["arrival_at"] = (NOW+timedelta(hours=7)).isoformat()
    e.flights.mutate_quote = early
    before = stable(e.store.read())
    e.jobs.start("CASE", request(e, after_flight_transfer=connection(pickup_window_start=NOW+timedelta(hours=1))))
    job = await run_next(e.jobs)
    assert job["status"] == "blocked", job
    assert job["calls_dispatched"] == 5 and len(e.source.calls) == 2 and not e.ai.decisions
    assert e.source.calls[1][1].url.params["pickupDateTime"] == "2030-01-04 09:30:00"
    assert any(item["code"] == "airport_transfer_source_rejected" for item in job["gaps"])
    assert stable(e.store.read()) == before


@pytest.mark.asyncio
async def test_older_revision_is_not_shortlisted_even_when_cheaper(setup):
    e = setup
    e.flights.count = 2
    def revision(index, quote):
        if index:
            quote["provider_offer_id"] = "offline-LHROTP"
            quote["quoted_at"] = (NOW+timedelta(seconds=1)).isoformat()
            e.clock[0] += timedelta(seconds=1)
    e.flights.mutate_quote = revision
    e.jobs.start("CASE", request(e, max_provider_calls=8))
    job = await run_next(e.jobs)
    assert job["status"] == "completed", job
    assert len(e.ai.shortlists[0][1]) == 1 and job["calls_dispatched"] == 6
    assert e.ai.shortlists[0][1][0]["total_cost_eur"] == 110
    chosen = next(item for item in e.store.read().resources if item.kind == "flight")
    assert chosen.provenance["quoted_at"] == (NOW+timedelta(seconds=1)).isoformat()


@pytest.mark.parametrize("group_ids", [["g1"], ["G1", "G1"], [" G1 "], ["G1", "G2"], ["G1"+"X"*40]])
def test_afterflight_scope_never_uses_legacy_group_normalization(group_ids):
    with pytest.raises(ValidationError):
        RecoveryJobRequest(expected_version=1, idempotency_key="offline-exact-postflight-scope",
            group_ids=group_ids, modes=["flight", "bus"], max_provider_calls=6, after_flight_transfer=connection())
    assert RecoveryJobRequest(expected_version=1, idempotency_key="offline-legacy-scope", group_ids=["g1", "G1"]).group_ids == ["G1"]


@pytest.mark.parametrize("malformed", [True, [], [connection()], "G1", 3, {"group_id": None}, {"group_id": ["G1"]}])
def test_malformed_afterflight_child_is_validation_error_not_attribute_error(malformed):
    with pytest.raises(ValidationError):
        RecoveryJobRequest(expected_version=1, idempotency_key="offline-malformed-postflight",
            group_ids=["G1"], modes=["flight", "bus"], max_provider_calls=6, after_flight_transfer=malformed)


@pytest.mark.asyncio
async def test_arrival_handoff_drains_running_old_phase_before_fresh_sourcing(setup):
    e = setup
    e.source.pause_phase = "bootstrap"
    old = e.jobs.start("CASE", request(e))["job"]
    claimed = e.jobs.ledger.claim_next()
    task = asyncio.create_task(e.jobs.run(*claimed))
    await asyncio.wait_for(e.source.entered.wait(), timeout=2)
    try:
        body = ArrivalReportInput(expected_version=e.store.read().version, idempotency_key="offline-postflight-handoff",
            group_ids=["G1"], airport_code="LHR", ready_at=NOW+timedelta(minutes=2), description="Updated explicit readiness at Heathrow.",
            recovery={"modes": ["flight", "bus"], "max_provider_calls": 6, "after_flight_transfer": connection()})
        receipt = OperationalReports(e.store, e.jobs).arrival("CASE", body)
        successor = receipt["recovery_job"]
        assert successor["supersedes_job_ids"] == [old["id"]]
        assert successor["calls_dispatched"] == 0 and successor["max_provider_calls"] == 6
        assert e.jobs.ledger.claim_next() is None
    finally:
        e.source.pause_phase = None
        e.source.release.set()
        await task
    previous = e.jobs.get(old["id"])["job"]
    assert previous["status"] == "cancelled" and previous["calls_dispatched"] == 4, previous
    result = await run_next(e.jobs)
    assert result["status"] == "completed" and result["calls_dispatched"] == 6, result
    assert len(e.source.calls) == 3
    assert e.store.read().groups[0].origin == "LHR" and e.store.read().groups[0].ready_at == 2
    assert len(e.store.read().resources) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_commit", [False, True])
async def test_replan_at_capacity_replaces_only_explicit_source_revisions_atomically(setup, monkeypatch, fail_commit):
    import app.inventory_lifecycle as inventory
    e = setup
    monkeypatch.setattr(inventory, "MAX_RESOURCES", 3)
    e.jobs.start("CASE", request(e))
    original = await run_next(e.jobs)
    assert original["status"] == "completed", original
    before = stable(e.store.read())
    original_ids = set(original["result"]["resource_ids"])
    e.clock[0] += timedelta(seconds=1)
    if fail_commit:
        def fail(*args):
            raise DomainError(409, "Offline final receipt failure after inventory replacement")
        monkeypatch.setattr(e.jobs.ledger, "complete_in_transaction", fail)
    e.jobs.start("CASE", request(e, idempotency_key="offline-postflight-refresh"))
    refreshed = await run_next(e.jobs)
    assert refreshed["calls_dispatched"] == 6
    if fail_commit:
        assert refreshed["status"] == "needs_review", refreshed
        assert stable(e.store.read()) == before
        assert e.store.read().offers[0].status == "proposed"
    else:
        assert refreshed["status"] == "completed", refreshed
        current = e.store.read()
        assert len(current.resources) == 3
        assert not original_ids & {item.id for item in current.resources}
        assert next(item for item in current.resources if item.id == "UNTOUCHED").model_dump(mode="json") == before["resources"][0]
        assert current.offers[0].status == "invalidated" and current.offers[-1].status == "proposed"
        for identity in original_ids:
            assert inventory.resource_history(current, resource_id=identity)
