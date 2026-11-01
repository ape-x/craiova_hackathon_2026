"""Two grounded reports share SQLite state, interpretation and recovery allowance.

Fake interpretation/flight adapters and MockTransport only; conftest forbids
real sockets. No voice session, live provider or application server is started.
"""
import copy
from datetime import datetime, timedelta
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app.airport_catalog import lookup_airport
from app.live_commands import LiveCommands
from app.models import Airport, Itinerary, Leg, Offer, Reservation, Resource
from app.providers import AIProvider
from app.providers.ai import Interpretation, LiveInterpretation
from app.recovery_jobs import RecoveryJobRequest, scope_fingerprint
from app.store import DomainError, utcnow
from test_live_commands import command_setup, intent, interpretation, request
from test_recovery_jobs import FakeAI as PlanningAI, FakeTravel, run_next


@pytest.fixture
def diversion_setup(command_setup):
    settings, store, service, jobs, ai = command_setup
    store.mutate("offline-diversion-budget", {}, store.read().version, lambda world: setattr(world, "budget", 1000))
    planning, travel = PlanningAI(), FakeTravel()
    jobs.ai, jobs.travel = planning, travel
    async def flight(query):
        departure = query.earliest_departure_at + timedelta(minutes=30)
        arrival = departure + timedelta(hours=1)
        return {"state": "ready", "source": "Nuitee", "items": [{
            "id": "offline-diversion-flight", "provider": "nuitee", "provider_offer_id": "offline-diversion-flight",
            "name": "Offline sourced flight", "origin": query.origin_code, "destination": query.destination_code,
            "departure_at": departure.isoformat(), "arrival_at": arrival.isoformat(),
            "total_price": "100.00", "currency": "EUR", "passengers": query.adults,
            "quoted_at": utcnow().isoformat(), "expires_at": (utcnow() + timedelta(hours=1)).isoformat(),
            "mode": "live", "accessibility_confirmed": False,
            "segments": [{"origin": query.origin_code, "destination": query.destination_code,
                "departure_at": departure.isoformat(), "arrival_at": arrival.isoformat()}]}]}
    travel.handler = flight
    return SimpleNamespace(settings=settings, store=store, service=service, jobs=jobs, ai=ai, planning=planning, travel=travel)


def bundle(language="en"):
    restriction = "CRA is closed for arrivals" if language == "en" else "CRA nu mai permite aterizarea"
    ready = "in 30 minutes" if language == "en" else "în 30 de minute"
    arrival = f"G1 at SBZ ready {ready}" if language == "en" else f"G1 la SBZ este pregătit {ready}"
    return {"type": "airport_restriction_and_arrival",
        "restriction": {"type": "airport_restriction", "incident_id": "RC-ONE", "airport_code": "CRA",
            "airport_reference": "CRA", "operations": ["arrivals"], "action": "restrict", "report_reference": restriction},
        "arrival": {"type": "arrival_report", "incident_id": "RC-ONE", "group_ids": ["G1"],
            "all_linked_groups": False, "airport_code": "SBZ", "airport_reference": "SBZ",
            "ready_at": None, "ready_at_reference": ready, "report_reference": arrival}}


def utterance(value, *, language="en", conjunction=False, followup=""):
    prefix = "For RO321: " if language == "en" else "Pentru RO321: "
    joiner = (" and " if language == "en" else " și ") if conjunction else ". "
    return prefix + value["restriction"]["report_reference"] + joiner + value["arrival"]["report_reference"] + ". " + followup


def recover(**changes):
    return intent(group_ids=["G1"], all_linked_groups=False, **changes)


def configure(env, value, recovery=None):
    env.ai.result = interpretation(operational_report=value, recovery=recovery)


def stable(store):
    return store.read().model_dump(mode="json", exclude={"sim_time"})


def counts(store):
    with store.connect() as conn:
        return {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("audit", "recovery_jobs", "idempotency")}


def assert_rejected(env, before, result):
    assert result["applied"] is False and "recovery_job" not in result, result
    assert stable(env.store) == before
    assert env.jobs.list("RC-ONE")["jobs"] == []
    assert len(env.ai.calls) == 1 and not env.planning.plans and not env.travel.calls


def seed_flight_offer(env, group_id, status):
    def seed(world):
        group = next(item for item in world.groups if item.id == group_id)
        group.origin, group.destination = "OTP", "CRA"
        group.status = "confirmed" if status == "confirmed" else "offered"
        end = (utcnow() + timedelta(hours=1)).isoformat()
        resource = Resource(id="FL-" + group_id, kind="flight", name="Offline prior flight", origin="OTP", destination="CRA",
            departure=world.sim_time + 120, arrival=world.sim_time + 180, capacity=1, cost_per_person=50,
            source="Offline source", valid_until=end)
        world.resources.append(resource)
        world.offers.append(Offer(id="OFF-" + group_id, group_id=group_id, status=status, created_version=world.version + 1,
            expires_at=end, itinerary=Itinerary(legs=[Leg(resource_id=resource.id, kind="flight", origin="OTP", destination="CRA",
                departure=resource.departure, arrival=resource.arrival, cost=50)], arrival=resource.arrival, total_cost=50),
            external_bookings=[{"resource_id": resource.id, "confirmation_reference": "offline-confirmed"}] if status == "confirmed" else []))
        world.reservations.append(Reservation(id="RES-" + group_id, offer_id="OFF-" + group_id, group_id=group_id,
            resource_id=resource.id, quantity=1, status=status, expires_at=end))
    env.store.mutate("offline-flight-" + group_id, {}, env.store.read().version, seed)


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["en", "ro"])
@pytest.mark.parametrize("with_recovery", [False, True])
@pytest.mark.parametrize("conjunction", [False, True])
async def test_two_exact_reports_share_one_version_and_optional_post_report_job(diversion_setup, language, with_recovery, conjunction):
    env = diversion_setup
    value = bundle(language)
    configure(env, value, recover(max_total_calls=20) if with_recovery else None)
    followup = "Recover RO321 for G1 by flight with maximum 4 calls" if language == "en" else "Recalculează RO321 pentru G1 cu zbor, maximum 4 apeluri"
    text = utterance(value, language=language, conjunction=conjunction, followup=followup if with_recovery else "")
    before = env.store.read()
    req = request(env.store, text)
    result = await env.service.command(req)
    assert result["applied"] is True, result
    assert result["command_interpretation_calls"] == 1 and len(result["reports"]) == 2
    restricted, arrived = result["reports"]
    assert result["report"] == arrived and restricted["type"] == "airport_restriction" and arrived["type"] == "arrival_report"
    assert restricted["airport_code"] == "CRA" and restricted["operations"] == ["arrivals"]
    assert arrived["airport_code"] == "SBZ" and arrived["group_ids"] == ["G1"]
    assert result["focus_id"] == "SBZ"
    assert "passenger position/readiness were not changed" not in result["explanation"]
    after = env.store.read()
    assert after.version == before.version + 1
    assert after.groups[0].origin == "SBZ" and after.groups[1:] == before.groups[1:]
    assert after.groups[0].destination == before.groups[0].destination
    assert after.groups[0].original_flight_id == before.groups[0].original_flight_id
    assert after.resources == before.resources and after.offers == before.offers and after.reservations == before.reservations
    assert after.incidents[0].flight_ref == before.incidents[0].flight_ref
    assert after.airports[:len(before.airports)] == before.airports
    assert [(item.airport_code, item.operation) for item in after.incidents[0].airport_restrictions] == [("CRA", "arrivals")]
    assert len(after.groups[0].provenance["arrival_reports"]) == 1
    assert not env.planning.plans and not env.planning.decisions and not env.travel.calls
    if with_recovery:
        job = result["recovery_job"]
        assert job["source_version"] == after.version and job["group_ids"] == ["G1"]
        assert job["max_provider_calls"] == 3 and job["calls_dispatched"] == 0
        assert result["recovery_total_call_limit"] == 4
        assert restricted["recovery_group_ids"] == ["G1"]
        assert env.jobs.ledger.get(job["id"], private=True)["_scope_hash"] == scope_fingerprint(after, "RC-ONE", ["G1"])
    else:
        assert "recovery_job" not in result and restricted["recovery_group_ids"] == []
    env.service.live_commands = LiveCommands(env.store, env.jobs)
    replay = await env.service.command(req)
    assert replay["replayed"] and replay["reports"] == result["reports"] and replay["report"] == arrived
    assert len(env.ai.calls) == 1 and env.store.read().version == after.version
    assert len(env.jobs.list("RC-ONE")["jobs"]) == int(with_recovery)


@pytest.mark.asyncio
async def test_queued_recovery_uses_both_reports_and_original_remaining_allowance(diversion_setup):
    env = diversion_setup
    value = bundle()
    configure(env, value, recover())
    result = await env.service.command(request(env.store, utterance(value, followup="Recover RO321 for G1 by flight with maximum 4 calls")))
    assert "recovery_job" in result, result
    final = await run_next(env.jobs)
    assert final["status"] == "completed" and final["calls_dispatched"] == 3, json.dumps({key: final.get(key) for key in ("status", "stage", "message", "steps", "result", "gaps", "blocked_reasons")})
    assert len(env.ai.calls) == len(env.planning.plans) == len(env.planning.decisions) == len(env.travel.calls) == 1
    assert env.travel.calls[0].origin_code == "SBZ" and env.travel.calls[0].destination_code == "OTP"
    assert final["group_ids"] == ["G1"] and len(env.store.read().groups[0].provenance["arrival_reports"]) == 1


@pytest.mark.asyncio
async def test_relative_time_retains_interpretation_anchor_across_atomic_failure_and_replay(diversion_setup, monkeypatch):
    env = diversion_setup
    stamp = utcnow().replace(second=37, microsecond=123)
    monkeypatch.setattr("app.live_commands.utcnow", lambda: stamp)
    value = bundle(); configure(env, value, recover())
    req = request(env.store, utterance(value, followup="Recover RO321 for G1 with maximum 4 calls"))
    before, totals = stable(env.store), counts(env.store)
    start = env.jobs.start_in_transaction
    def fail(*args, **kwargs):
        start(*args, **kwargs)
        raise RuntimeError("offline failure after successor write")
    monkeypatch.setattr(env.jobs, "start_in_transaction", fail)
    with pytest.raises(RuntimeError, match="successor write"):
        await env.service.command(req)
    assert stable(env.store) == before and counts(env.store) == totals
    monkeypatch.setattr(env.jobs, "start_in_transaction", start)
    monkeypatch.setattr("app.live_commands.utcnow", lambda: stamp + timedelta(minutes=20))
    env.service.live_commands = LiveCommands(env.store, env.jobs)
    result = await env.service.command(req)
    expected = stamp.replace(second=0, microsecond=0) + timedelta(minutes=31)
    assert datetime.fromisoformat(result["report"]["ready_at"]) == expected
    basis = result["report"]["readiness_basis"]
    assert basis["command_received_at"] == stamp.isoformat() and basis["minutes"] == 30 and basis["rounding"] == "ceil_minute"
    assert len(env.ai.calls) == 1
    replay = await env.service.command(req)
    assert replay["reports"] == result["reports"] and len(env.ai.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["restriction_case", "arrival_case", "arrival_group", "recovery_group", "recovery_all", "aircraft_only"])
async def test_model_cannot_change_case_group_or_infer_passenger_readiness(diversion_setup, fault):
    env = diversion_setup
    value = bundle(); text = utterance(value, followup="Recover RO321 for G1 with maximum 4 calls")
    recovery = recover()
    if fault == "restriction_case": value["restriction"]["incident_id"] = "RC-OTHER"
    elif fault == "arrival_case": value["arrival"]["incident_id"] = "RC-OTHER"
    elif fault == "arrival_group": value["arrival"]["group_ids"] = ["G1", "G2"]
    elif fault == "recovery_group": recovery["group_ids"] = ["G2"]
    elif fault == "recovery_all": recovery.update(group_ids=[], all_linked_groups=True)
    else:
        value["arrival"]["report_reference"] = "RO321 has landed at SBZ in 30 minutes"
        text = utterance(value, followup="Recover RO321 for G1 with maximum 4 calls")
    configure(env, value, recovery)
    before = stable(env.store)
    assert_rejected(env, before, await env.service.command(request(env.store, text)))


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["overlap", "duplicate", "third_airport", "third_arrival", "third_outage", "condition_prefix", "condition_between", "condition_tail", "omitted_restriction", "omitted_arrival"])
async def test_overlap_extra_assertions_and_quoted_away_conditions_apply_neither_report(diversion_setup, fault):
    env = diversion_setup
    value = bundle()
    text = utterance(value, conjunction=True)
    if fault == "overlap": value["arrival"]["report_reference"] = value["restriction"]["report_reference"] + " and " + value["arrival"]["report_reference"]
    elif fault == "duplicate": text = utterance(value) + value["restriction"]["report_reference"] + "."
    elif fault == "third_airport": text += " OTP is closed for departures."
    elif fault == "third_arrival": text += " G2 is ready at LHR in 40 minutes."
    elif fault == "third_outage": text += " Q1 is broken."
    elif fault == "condition_prefix": text = "For RO321: If " + value["restriction"]["report_reference"] + " and " + value["arrival"]["report_reference"] + "."
    elif fault == "condition_between": text = "For RO321: " + value["restriction"]["report_reference"] + " and if " + value["arrival"]["report_reference"] + "."
    elif fault == "condition_tail": text = utterance(value).rstrip(". ") + " if the flight lands."
    elif fault == "omitted_restriction": value = value["arrival"]
    else: value = value["restriction"]
    configure(env, value)
    before = stable(env.store)
    assert_rejected(env, before, await env.service.command(request(env.store, text)))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,cap,followup,code", [
    (["flight"], 3, "Recover RO321 for G1 with maximum 3 calls", "insufficient_call_allowance"),
    (["flight", "hotel", "bus"], 11, "Recover RO321 for G1 using saved hotel, both transfers and flight with maximum 11 calls", "hotel_setup_required"),
    (["flight", "bus"], 6, "Recover RO321 for G1 using the saved transfer and flight with maximum 6 calls", "airport_transfer_setup_required"),
])
async def test_known_prerequisites_preserve_both_reports_without_new_job_or_source_calls(diversion_setup, mode, cap, followup, code):
    env = diversion_setup
    value = bundle(); configure(env, value, recover(modes=mode, max_total_calls=cap))
    before = env.store.read()
    result = await env.service.command(request(env.store, utterance(value, followup=followup)))
    assert result["applied"] and result["recovery_blocked"]["code"] == code, result
    assert len(result["reports"]) == 2 and "recovery_job" not in result
    assert env.store.read().version == before.version + 1 and env.store.read().groups[0].origin == "SBZ"
    assert len(env.store.read().incidents[0].airport_restrictions) == 1
    assert not env.jobs.list("RC-ONE")["jobs"] and len(env.ai.calls) == 1 and not env.travel.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown", [False, True])
async def test_bundle_successor_waits_for_prior_call_and_cannot_bypass_unknown_drain(diversion_setup, unknown):
    env = diversion_setup
    old = env.jobs.start("RC-ONE", RecoveryJobRequest(expected_version=env.store.read().version,
        idempotency_key="offline-preceding-job", group_ids=["G1"], max_provider_calls=6))["job"]
    _, owner = env.jobs.ledger.claim_next()
    step = env.jobs.ledger.dispatch(old["id"], owner, "searching", "Offline fixture", {}, ["G1"])
    value = bundle(); configure(env, value, recover())
    result = await env.service.command(request(env.store, utterance(value, followup="Recover RO321 for G1 with maximum 4 calls")))
    successor = result["recovery_job"]
    assert successor["stage"] == "waiting_for_previous" and successor["supersedes_job_ids"] == [old["id"]]
    assert successor["max_provider_calls"] == 3 and successor["calls_dispatched"] == 0
    assert env.jobs.get(old["id"])["job"]["cancel_requested"]
    assert env.jobs.ledger.claim_next() is None
    if not unknown:
        env.jobs.ledger.outcome(old["id"], owner, step, "succeeded", "Offline known outcome", {})
    env.jobs.ledger.finish(old["id"], owner, "cancelled", "Offline drain ended")
    claim = env.jobs.ledger.claim_next()
    if unknown:
        assert claim is None and env.jobs.get(successor["id"])["job"]["status"] == "needs_review"
    else:
        assert claim[0]["id"] == successor["id"]
        assert claim[0]["_scope_hash"] == scope_fingerprint(env.store.read(), "RC-ONE", ["G1"])
    assert len(env.ai.calls) == 1 and not env.travel.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("known", [False, True])
async def test_partial_queue_write_cannot_leave_successor_or_predecessor_cancellation(diversion_setup, monkeypatch, known):
    env = diversion_setup
    old = env.jobs.start("RC-ONE", RecoveryJobRequest(expected_version=env.store.read().version,
        idempotency_key="offline-original-job", group_ids=["G1"]))["job"]
    seed_flight_offer(env, "G2", "proposed")
    value = bundle(); configure(env, value, recover())
    before, totals = stable(env.store), counts(env.store)
    start = env.jobs.start_in_transaction
    def fail(*args, **kwargs):
        start(*args, **kwargs)
        if known: raise DomainError(429, "Offline queue rejection after partial write", {"code": "recovery_queue_full"})
        raise RuntimeError("offline enqueue failure")
    monkeypatch.setattr(env.jobs, "start_in_transaction", fail)
    req = request(env.store, utterance(value, followup="Recover RO321 for G1 with maximum 4 calls"))
    if known:
        result = await env.service.command(req)
        assert result["applied"] and len(result["reports"]) == 2 and result["recovery_blocked"]["code"] == "recovery_queue_unavailable"
        assert env.store.read().offers[0].status == "invalidated" and "recovery_job" not in result
    else:
        with pytest.raises(RuntimeError, match="enqueue failure"): await env.service.command(req)
        assert stable(env.store) == before and counts(env.store) == totals
    jobs = env.jobs.list("RC-ONE")["jobs"]
    assert len(jobs) == 1 and jobs[0]["id"] == old["id"]
    assert jobs[0]["status"] == "queued" and not jobs[0]["cancel_requested"] and "superseded_by_job_id" not in jobs[0]


@pytest.mark.asyncio
async def test_second_report_rejection_rolls_back_prior_restriction_and_pending_invalidation(diversion_setup):
    env = diversion_setup
    seed_flight_offer(env, "G2", "proposed")
    env.store.mutate("offline-confirm-selected", {}, env.store.read().version,
        lambda world: setattr(world.groups[0], "status", "confirmed"))
    value = bundle(); configure(env, value)
    before, totals = stable(env.store), counts(env.store)
    with pytest.raises(DomainError) as error:
        await env.service.command(request(env.store, utterance(value)))
    assert error.value.status == 409
    assert stable(env.store) == before and counts(env.store) == totals
    assert env.store.read().offers[0].status == "proposed" and env.store.read().reservations[0].status == "proposed"


@pytest.mark.asyncio
async def test_unselected_confirmed_conflict_survives_receipt_merge_and_blocks_whole_case(diversion_setup):
    env = diversion_setup
    seed_flight_offer(env, "G2", "confirmed")
    value = bundle(); configure(env, value, recover())
    before = env.store.read()
    result = await env.service.command(request(env.store, utterance(value, followup="Recover RO321 for G1 with maximum 4 calls")))
    assert result["applied"] and result["manual_review_required"] and result["confirmed_conflicts"], result
    assert result["reports"][0]["confirmed_conflicts"] == result["confirmed_conflicts"]
    assert result["report"] == result["reports"][1] and result["report"]["group_ids"] == ["G1"]
    assert "recovery_job" not in result and result["recovery_blocked"]
    assert env.store.read().offers == before.offers and env.store.read().reservations == before.reservations
    assert env.store.read().groups[1:] == before.groups[1:]
    assert not env.jobs.list("RC-ONE")["jobs"] and not env.travel.calls


@pytest.mark.asyncio
async def test_final_receipt_sql_failure_rolls_back_both_reports_and_recovery(diversion_setup):
    env = diversion_setup
    value = bundle(); configure(env, value, recover())
    before, totals = stable(env.store), counts(env.store)
    with env.store.connect() as conn:
        conn.execute("CREATE TRIGGER reject_bundle_receipt BEFORE INSERT ON idempotency WHEN NEW.key LIKE 'command:%' BEGIN SELECT RAISE(ABORT, 'Offline bundle receipt failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="bundle receipt"):
        await env.service.command(request(env.store, utterance(value, followup="Recover RO321 for G1 with maximum 4 calls")))
    assert stable(env.store) == before and counts(env.store) == totals
    assert len(env.ai.calls) == 1


@pytest.mark.parametrize("change", ["missing_arrival", "extra_report", "wrong_restriction_type", "wrong_arrival_type"])
def test_strict_bundle_branch_rejects_unbounded_or_wrong_nested_reports(change):
    value = bundle()
    if change == "missing_arrival": value.pop("arrival")
    elif change == "extra_report": value["another"] = copy.deepcopy(value["arrival"])
    elif change == "wrong_restriction_type": value["restriction"] = copy.deepcopy(value["arrival"])
    else: value["arrival"] = copy.deepcopy(value["restriction"])
    with pytest.raises(ValidationError): LiveInterpretation.model_validate(interpretation(operational_report=value, recovery=None), strict=True)


@pytest.mark.asyncio
async def test_mocked_responses_uses_strict_two_report_schema_without_changing_simulation(diversion_setup):
    env = diversion_setup
    env.settings.openai_api_key = "offline-placeholder"
    value = bundle()
    async def handle(outgoing):
        body = json.loads(outgoing.content)
        schema = body["text"]["format"]["schema"]
        branch = next(item for item in schema["$defs"].values()
            if item.get("properties", {}).get("type", {}).get("const") == "airport_restriction_and_arrival")
        assert set(branch["properties"]) == set(branch["required"]) == {"type", "restriction", "arrival"}
        assert branch["additionalProperties"] is False
        assert "anyOf" in schema["properties"]["operational_report"]
        assert '"oneOf"' not in json.dumps(schema) and '"discriminator"' not in json.dumps(schema)
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text",
            "text": json.dumps(interpretation(operational_report=value, recovery=None))}]}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await AIProvider(env.settings, client=client).interpret(utterance(value), env.store.read().model_dump(mode="json"))
    assert result["operational_report"] == value
    assert "operational_report" not in Interpretation.model_json_schema()["properties"]


@pytest.mark.asyncio
async def test_case_reference_inside_arrival_quote_can_bind_both_reports(diversion_setup):
    env = diversion_setup
    value = bundle()
    value["arrival"]["report_reference"] = "For RO321, " + value["arrival"]["report_reference"]
    configure(env, value)
    text = value["restriction"]["report_reference"] + ". " + value["arrival"]["report_reference"]
    result = await env.service.command(request(env.store, text))
    assert result["applied"] and len(result["reports"]) == 2, result
    assert all(report["incident_id"] == "RC-ONE" for report in result["reports"])
    assert len(env.ai.calls) == 1 and not env.jobs.list("RC-ONE")["jobs"]


@pytest.mark.asyncio
async def test_recovery_cannot_borrow_group_scope_from_arrival_assertion(diversion_setup):
    env = diversion_setup
    value = bundle(); configure(env, value, recover())
    before = stable(env.store)
    result = await env.service.command(request(env.store, utterance(value, followup="Recover RO321 with maximum 4 calls")))
    assert_rejected(env, before, result)


@pytest.mark.asyncio
@pytest.mark.parametrize("child", ["restriction", "arrival"])
async def test_conditional_inside_either_exact_quote_cannot_be_masked_away(diversion_setup, child):
    env = diversion_setup
    value = bundle()
    value[child]["report_reference"] = "If " + value[child]["report_reference"]
    configure(env, value)
    before = stable(env.store)
    assert_rejected(env, before, await env.service.command(request(env.store, utterance(value, conjunction=True))))


@pytest.mark.asyncio
async def test_distinct_existing_cases_in_two_assertions_cannot_form_one_bundle(diversion_setup):
    env = diversion_setup
    def seed(world):
        other = world.incidents[0].model_copy(deep=True)
        other.id, other.requested_flight_number, other.group_ids = "RC-OTHER", "RO999", ["OUT"]
        world.incidents.append(other)
        world.groups[2].incident_id = other.id
    env.store.mutate("offline-second-case", {}, env.store.read().version, seed)
    value = bundle()
    value["restriction"]["incident_id"] = "RC-OTHER"
    value["restriction"]["report_reference"] = "For RO999, " + value["restriction"]["report_reference"]
    value["arrival"]["report_reference"] = "For RO321, " + value["arrival"]["report_reference"]
    text = value["restriction"]["report_reference"] + ". " + value["arrival"]["report_reference"]
    configure(env, value)
    before = stable(env.store)
    assert_rejected(env, before, await env.service.command(request(env.store, text)))
    assert not env.jobs.list("RC-OTHER")["jobs"]


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["If needed. ", "Dacă este nevoie. ", "Do not assume this. "])
async def test_separate_sentence_shared_qualifier_cannot_be_dropped(diversion_setup, prefix):
    env = diversion_setup
    value = bundle(); configure(env, value)
    before = stable(env.store)
    assert_rejected(env, before, await env.service.command(request(env.store, prefix + utterance(value))))


@pytest.mark.asyncio
async def test_documented_romanian_bundle_sources_from_reported_cra_and_avoids_closed_otp(diversion_setup):
    env = diversion_setup
    def seed(world):
        # This is an explicitly mocked, dated existing operator service. The
        # command must not manufacture the missing SBZ→OTP ground connection.
        world.airports = list({airport.id: airport for airport in world.airports}.values())
        for code in ("LHR", "SBZ"):
            ref = lookup_airport(code)
            world.airports.append(Airport(id=code, code=code, name=ref["name"], lat=ref["lat"], lon=ref["lon"]))
        world.groups[0].origin = "LHR"
        world.resources.append(Resource(id="OFFLINE-SBZ-OTP", kind="bus", name="Mock dated operator connection",
            origin="SBZ", destination="OTP", departure=world.sim_time + 600, arrival=world.sim_time + 690,
            capacity=1, cost_per_person=40, source="Offline operator inventory", capacity_scope="operator_inventory",
            valid_until=(utcnow() + timedelta(hours=1)).isoformat(), eligible_group_ids=["G1"]))
    env.store.mutate("offline-dated-ground-link", {}, env.store.read().version, seed)
    value = bundle("ro")
    value["restriction"].update(airport_code="OTP", airport_reference="OTP", report_reference="OTP nu mai permite aterizarea")
    value["arrival"].update(airport_code="CRA", airport_reference="CRA", report_reference="G1 este la CRA, pregătit în 30 de minute")
    configure(env, value, recover())
    text = "Pentru RO321, OTP nu mai permite aterizarea. G1 este la CRA, pregătit în 30 de minute. Recalculează zboruri pentru G1, maximum patru apeluri."
    before = env.store.read()
    result = await env.service.command(request(env.store, text))
    assert result["applied"] and result["recovery_total_call_limit"] == 4 and len(result["reports"]) == 2, result
    assert env.store.read().groups[0].origin == "CRA" and env.store.read().groups[1:] == before.groups[1:]
    assert [(entry.airport_code, entry.operation) for entry in env.store.read().incidents[0].airport_restrictions] == [("OTP", "arrivals")]
    final = await run_next(env.jobs)
    assert final["status"] == "completed" and final["calls_dispatched"] == 3, json.dumps(final.get("gaps"))
    assert len(env.ai.calls) == len(env.travel.calls) == len(env.planning.plans) == len(env.planning.decisions) == 1
    assert (env.travel.calls[0].origin_code, env.travel.calls[0].destination_code) == ("CRA", "SBZ")
    after = env.store.read()
    assert after.groups[1:] == before.groups[1:] and after.incidents[0].flight_ref == before.incidents[0].flight_ref
    offer = next(offer for offer in after.offers if offer.group_id == "G1")
    assert [(leg.kind, leg.origin, leg.destination) for leg in offer.itinerary.legs] == [("flight", "CRA", "SBZ"), ("bus", "SBZ", "OTP")]
    assert all(resource.destination != "OTP" for resource in after.resources if resource.kind == "flight")
