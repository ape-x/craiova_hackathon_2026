"""Atomic service+passenger reports use SQLite and offline adapters only."""
import copy
from datetime import datetime, timedelta
import json
import sqlite3

import httpx
import pytest
from pydantic import ValidationError

import app.live_service_arrival_commands as paired
from app.live_commands import LiveCommands
from app.models import Itinerary, Leg, Offer, Reservation, Resource
from app.providers import AIProvider, ProviderError
from app.providers.ai import Interpretation, LiveInterpretation
from app.recovery_jobs import RecoveryJobRequest, scope_fingerprint
from app.store import DomainError, utcnow
from test_live_commands import command_setup, intent, interpretation, request
from test_live_diversion_commands import diversion_setup, stable, counts
from test_recovery_jobs import run_next


@pytest.fixture
def service_arrival_setup(diversion_setup):
    env = diversion_setup
    def seed(world):
        expiry = (utcnow()+timedelta(hours=1)).isoformat()
        other = world.incidents[0].model_copy(deep=True)
        other.id, other.requested_flight_number, other.group_ids = "RC-OTHER", "RO999", ["OUT"]
        world.incidents.append(other)
        world.groups[2].incident_id = other.id
        keep = world.groups[0].model_copy(deep=True)
        keep.id, keep.name, keep.token = "KEEP", "Independent booked group", "offline-keep"
        keep.incident_id, keep.original_flight_id = None, "INDEPENDENT"
        world.groups.append(keep)
        for rid in ("Q1", "Q2"):
            world.resources.append(Resource(id=rid, name="Supplier service "+rid, kind="bus",
                origin="CRA", destination="OTP", departure=world.groups[0].ready_at+60,
                arrival=world.groups[0].ready_at+120, capacity=4, cost_per_person=40,
                source="offline operator source", valid_until=expiry,
                provenance={"supplier_fact": "keep exact source schedule/fare"}))
        for gid, rid, status in (("G1", "Q1", "proposed"), ("G2", "Q1", "accepted"),
                ("OUT", "Q1", "proposed"), ("KEEP", "Q2", "confirmed")):
            group = next(item for item in world.groups if item.id == gid)
            group.status = "confirmed" if status == "confirmed" else "offered"
            resource = next(item for item in world.resources if item.id == rid)
            if status == "confirmed": resource.booking_status = "confirmed"
            world.offers.append(Offer(id="O-"+gid, group_id=gid, status=status, expires_at=expiry,
                created_version=world.version+1,
                external_bookings=[{"resource_id": rid, "confirmation_reference": "offline-booking"}] if status == "confirmed" else [],
                itinerary=Itinerary(legs=[Leg(resource_id=rid, kind="bus", origin="CRA", destination="OTP",
                    departure=resource.departure, arrival=resource.arrival, cost=40)],
                    arrival=resource.arrival, total_cost=40)))
            world.reservations.append(Reservation(id="R-"+gid, group_id=gid, offer_id="O-"+gid,
                resource_id=rid, quantity=1, status=status, expires_at=expiry))
    env.store.mutate("paired-service-fixture", {}, env.store.read().version, seed)
    return env


def bundle(language="en"):
    ready = "in 30 minutes" if language == "en" else "în 30 de minute"
    arrival = "G1 at SBZ ready "+ready if language == "en" else "G1 la SBZ este pregătit "+ready
    return {"type": "resource_unavailability_and_arrival",
        "unavailability": {"type": "resource_unavailability", "resource_id": "Q1", "resource_reference": "Q1",
            "report_reference": "Q1 is unavailable" if language == "en" else "Q1 nu mai circulă"},
        "arrival": {"type": "arrival_report", "incident_id": "RC-ONE", "group_ids": ["G1"],
            "all_linked_groups": False, "airport_code": "SBZ", "airport_reference": "SBZ", "ready_at": None,
            "ready_at_reference": ready, "report_reference": arrival}}


def spoken(value, *, language="en", conjunction=False, followup="", reverse=False):
    claims = [value["unavailability"]["report_reference"], value["arrival"]["report_reference"]]
    if reverse: claims.reverse()
    join = (" and " if language == "en" else " și ") if conjunction else ". "
    return ("For RO321: " if language == "en" else "Pentru RO321: ")+join.join(claims)+". "+followup


def configure(env, value, recovery=None):
    env.ai.result = interpretation(operational_report=value, recovery=recovery)


def recover(**changes):
    return intent(group_ids=["G1"], all_linked_groups=False, **changes)


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["en", "ro"])
@pytest.mark.parametrize("conjunction,reverse", [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("with_recovery", [False, True])
async def test_pair_records_all_service_dependencies_but_only_selected_arrival_once(service_arrival_setup, language, conjunction, reverse, with_recovery):
    env = service_arrival_setup
    value = bundle(language)
    configure(env, value, recover(max_total_calls=20) if with_recovery else None)
    followup = "Recover RO321 for G1 by flight with maximum 4 calls" if language == "en" else "Recalculează RO321 pentru G1 cu zbor, maximum 4 apeluri"
    req = request(env.store, spoken(value, language=language, conjunction=conjunction, reverse=reverse,
        followup=followup if with_recovery else ""))
    before = env.store.read()
    result = await env.service.command(req)
    assert result["applied"], result
    unavailable, arrived = result["reports"]
    assert result["report"] == arrived and result["incident_id"] == "RC-ONE"
    assert unavailable["type"] == "resource_unavailability" and unavailable["resource_id"] == "Q1"
    assert unavailable["affected_offer_ids"] == ["O-G1", "O-G2", "O-OUT"]
    assert unavailable["affected_group_ids"] == ["G1", "G2", "OUT"]
    assert result["invalidated_offer_ids"] == unavailable["affected_offer_ids"]
    assert result["affected_group_ids"] == unavailable["affected_group_ids"]
    assert arrived["type"] == "arrival_report" and arrived["group_ids"] == ["G1"]
    assert result["command_interpretation_calls"] == 1 and result["focus_id"] == "SBZ"
    after = env.store.read()
    assert after.version == before.version+1 and after.groups[0].origin == "SBZ"
    for old, new in zip(before.groups[1:3], after.groups[1:3]):
        assert old.model_dump(exclude={"status"}) == new.model_dump(exclude={"status"})
    assert after.groups[3] == before.groups[3] and after.offers[3] == before.offers[3]
    assert after.reservations[3] == before.reservations[3] and after.resources[1] == before.resources[1]
    assert [item.status for item in after.offers[:3]] == ["invalidated"]*3
    assert [item.status for item in after.reservations[:3]] == ["released"]*3
    assert after.resources[0].available is False
    assert after.resources[0].model_dump(exclude={"available", "provenance"}) == before.resources[0].model_dump(exclude={"available", "provenance"})
    assert after.resources[0].provenance["supplier_fact"] == before.resources[0].provenance["supplier_fact"]
    assert after.resources[0].provenance["unavailability_reports"] == [unavailable]
    assert [event.data for event in after.events[-2:]] == [unavailable, arrived]
    assert [event.version for event in after.events[-2:]] == [after.version]*2
    if with_recovery:
        job = result["recovery_job"]
        assert job["group_ids"] == ["G1"] and job["source_version"] == after.version
        assert job["max_provider_calls"] == 3 and job["calls_dispatched"] == 0
        assert env.jobs.ledger.get(job["id"], private=True)["_scope_hash"] == scope_fingerprint(after, "RC-ONE", ["G1"])
    else: assert "recovery_job" not in result
    env.service.live_commands = LiveCommands(env.store, env.jobs)
    replay = await env.service.command(req)
    assert replay["replayed"] and replay["reports"] == result["reports"]
    assert len(env.ai.calls) == 1 and env.store.read().version == after.version
    assert not env.planning.plans and not env.travel.calls


@pytest.mark.asyncio
async def test_effect_union_keeps_distinct_service_and_arrival_dependencies(service_arrival_setup):
    env = service_arrival_setup
    def separate(world):
        other = world.resources[0].model_copy(deep=True)
        other.id, other.name = "Q3", "Separate group service"
        world.resources.append(other)
        world.offers[0].itinerary.legs[0].resource_id = "Q3"
        world.reservations[0].resource_id = "Q3"
    env.store.mutate("separate-dependent-proposals", {}, env.store.read().version, separate)
    value = bundle(); configure(env, value)
    result = await env.service.command(request(env.store, spoken(value)))
    assert result["reports"][0]["affected_offer_ids"] == ["O-G2", "O-OUT"]
    assert result["reports"][1]["affected_offer_ids"] == ["O-G1"]
    assert result["invalidated_offer_ids"] == ["O-G1", "O-G2", "O-OUT"]
    assert result["affected_group_ids"] == ["G1", "G2", "OUT"]
    assert result["report"]["group_ids"] == ["G1"]
    assert next(item for item in env.store.read().resources if item.id == "Q3").available


@pytest.mark.asyncio
@pytest.mark.parametrize("service_text", ["Q1 is unavailable at SBZ", "Q1 este indisponibil la SBZ",
    "Q1 broke and is unavailable", "Q1 s-a stricat și nu mai funcționează", "Q1 broke and Q1 is unavailable"])
async def test_service_airport_description_does_not_report_an_airport_closure(service_arrival_setup, service_text):
    env = service_arrival_setup
    value = bundle()
    value["unavailability"]["report_reference"] = service_text
    configure(env, value)
    result = await env.service.command(request(env.store, spoken(value)))
    assert result["applied"] and result["reports"][0]["description"] == service_text, result
    assert not env.store.read().incidents[0].airport_restrictions


@pytest.mark.asyncio
async def test_worker_sources_from_reported_origin_with_original_budget(service_arrival_setup):
    env = service_arrival_setup
    value = bundle(); configure(env, value, recover())
    result = await env.service.command(request(env.store, spoken(value, followup="Recover RO321 for G1 by flight with maximum 4 calls")))
    assert "recovery_job" in result, result
    completed = await run_next(env.jobs)
    assert completed["status"] == "completed", completed
    assert completed["calls_dispatched"] == 3 and len(env.ai.calls) == len(env.travel.calls) == 1
    assert env.travel.calls[0].origin_code == "SBZ" and env.travel.calls[0].destination_code == "OTP"
    assert next(item for item in env.store.read().offers if item.status == "proposed").group_id == "G1"
    assert not env.store.read().resources[0].available


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["overlap", "duplicate", "third_location", "third_outage", "restriction", "conditional", "global_condition", "negation", "hidden_recovery", "swallowed_arrival", "swallowed_outage", "wrong_groups", "wrong_case", "no_readiness", "flight_eta"])
async def test_ambiguous_or_incomplete_pairs_apply_neither_fact(service_arrival_setup, fault):
    env = service_arrival_setup
    value = bundle(); text = spoken(value)
    if fault == "overlap": value["unavailability"]["report_reference"] += ". "+value["arrival"]["report_reference"]
    elif fault == "duplicate": text += " Q1 is unavailable."
    elif fault == "third_location": text += " G2 is at OTP."
    elif fault == "third_outage": text += " Q2 is unavailable."
    elif fault == "restriction": text += " OTP is closed for arrivals."
    elif fault == "conditional": text = "If "+text
    elif fault == "global_condition": text = "If needed. "+text
    elif fault == "negation": text = "Do not report "+text
    elif fault == "hidden_recovery":
        text += " Recover RO321 for G1 with maximum 4 calls"
        value["arrival"]["report_reference"] += ". Recover RO321 for G1 with maximum 4 calls"
    elif fault == "swallowed_arrival":
        text = text.replace("Q1 is unavailable", "Q1 is unavailable and G2 is at OTP")
        value["unavailability"]["report_reference"] = "Q1 is unavailable and G2 is at OTP"
    elif fault == "swallowed_outage":
        text = text.replace("Q1 is unavailable", "Q1 is unavailable and another bus broke")
        value["unavailability"]["report_reference"] = "Q1 is unavailable and another bus broke"
    elif fault == "wrong_groups": value["arrival"]["group_ids"] = ["G2"]
    elif fault == "wrong_case": value["arrival"]["incident_id"] = "RC-OTHER"
    elif fault == "no_readiness":
        text = text.replace(value["arrival"]["report_reference"], "G1 has arrived at SBZ")
        value["arrival"]["report_reference"] = "G1 has arrived at SBZ"
    elif fault == "flight_eta":
        text = text.replace(value["arrival"]["report_reference"], "Flight RO321 arrives at SBZ in 30 minutes")
        value["arrival"]["report_reference"] = "Flight RO321 arrives at SBZ in 30 minutes"
    configure(env, value)
    before = stable(env.store)
    result = await env.service.command(request(env.store, text))
    assert not result["applied"] and "recovery_job" not in result, result
    assert stable(env.store) == before and not env.jobs.list("RC-ONE")["jobs"]
    assert len(env.ai.calls) == 1 and not env.travel.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["selected_group", "service", "other_booked_leg"])
async def test_original_booking_guards_reject_whole_pair_before_invalidation(service_arrival_setup, kind):
    env = service_arrival_setup
    def protect(world):
        if kind == "selected_group": world.groups[0].status = "confirmed"
        elif kind == "service": world.resources[0].booking_status = "confirmed"
        else:
            # The failed unbooked leg's invalidation must not hide this original
            # active association to another booked resource from arrival guards.
            world.offers[0].itinerary.legs.append(world.offers[3].itinerary.legs[0].model_copy(deep=True))
    env.store.mutate("paired-original-booking", {}, env.store.read().version, protect)
    configure(env, bundle())
    before, tables = stable(env.store), counts(env.store)
    with pytest.raises(DomainError) as rejected:
        await env.service.command(request(env.store, spoken(bundle())))
    assert rejected.value.status == 409
    assert stable(env.store) == before and counts(env.store) == tables


@pytest.mark.asyncio
@pytest.mark.parametrize("modes,total,followup,code", [
    (["flight"], 3, "Recover RO321 for G1 with maximum 3 calls", "insufficient_call_allowance"),
    (["hotel", "bus", "flight"], 11, "Recover RO321 for G1 using saved hotel and transfers and flight with maximum 11 calls", "hotel_setup_required"),
    (["flight", "bus"], 6, "Recover RO321 for G1 using saved transfer and flight with maximum 6 calls", "airport_transfer_setup_required"),
])
async def test_missing_recovery_setup_preserves_both_valid_facts(service_arrival_setup, modes, total, followup, code):
    env = service_arrival_setup
    value = bundle(); configure(env, value, recover(modes=modes, max_total_calls=total))
    before = env.store.read()
    result = await env.service.command(request(env.store, spoken(value, followup=followup)))
    assert result["applied"] and result["recovery_blocked"]["code"] == code, result
    assert len(result["reports"]) == 2 and "recovery_job" not in result
    assert env.store.read().version == before.version+1 and env.store.read().groups[0].origin == "SBZ"
    assert not env.store.read().resources[0].available and not env.jobs.list("RC-ONE")["jobs"]
    assert not env.travel.calls


@pytest.mark.asyncio
async def test_second_report_failure_rolls_back_then_replay_uses_original_relative_anchor(service_arrival_setup, monkeypatch):
    env = service_arrival_setup
    stamp = utcnow().replace(second=37, microsecond=123)
    monkeypatch.setattr("app.live_commands.utcnow", lambda: stamp)
    value = bundle(); configure(env, value)
    req = request(env.store, spoken(value))
    before, tables = stable(env.store), counts(env.store)
    original = paired.apply_arrival_report
    def failure(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("offline after second report")
    monkeypatch.setattr(paired, "apply_arrival_report", failure)
    with pytest.raises(RuntimeError): await env.service.command(req)
    assert stable(env.store) == before and counts(env.store) == tables
    monkeypatch.setattr(paired, "apply_arrival_report", original)
    monkeypatch.setattr("app.live_commands.utcnow", lambda: stamp+timedelta(hours=5))
    result = await env.service.command(req)
    expected = stamp.replace(second=0, microsecond=0)+timedelta(minutes=31)
    assert datetime.fromisoformat(result["report"]["ready_at"]) == expected
    assert result["report"]["readiness_basis"]["command_received_at"] == stamp.isoformat()
    assert len(env.ai.calls) == 1 and len(env.store.read().resources[0].provenance["unavailability_reports"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("known", [False, True])
async def test_partial_successor_or_cancellation_rolls_back_correct_transaction_scope(service_arrival_setup, monkeypatch, known):
    env = service_arrival_setup
    old = env.jobs.start("RC-ONE", RecoveryJobRequest(expected_version=env.store.read().version,
        idempotency_key="paired-previous-job", group_ids=["G1"]))["job"]
    original = env.jobs.start_in_transaction
    def failure(*args, **kwargs):
        original(*args, **kwargs)
        if known: raise DomainError(429, "Offline queue full", {"code": "recovery_queue_full"})
        raise RuntimeError("offline after successor")
    monkeypatch.setattr(env.jobs, "start_in_transaction", failure)
    value = bundle(); configure(env, value, recover())
    before, tables = stable(env.store), counts(env.store)
    req = request(env.store, spoken(value, followup="Recover RO321 for G1 with maximum 4 calls"))
    if known:
        result = await env.service.command(req)
        assert result["applied"] and len(result["reports"]) == 2 and result["recovery_blocked"]["code"] == "recovery_queue_unavailable"
        assert env.store.read().version == before["version"]+1
    else:
        with pytest.raises(RuntimeError): await env.service.command(req)
        assert stable(env.store) == before and counts(env.store) == tables
    jobs = env.jobs.list("RC-ONE")["jobs"]
    assert len(jobs) == 1 and jobs[0]["id"] == old["id"] and jobs[0]["status"] == "queued"
    assert not jobs[0]["cancel_requested"] and "superseded_by_job_id" not in jobs[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown", [False, True])
async def test_successor_waits_for_drain_and_never_retries_unknown_source(service_arrival_setup, unknown):
    env = service_arrival_setup
    old = env.jobs.start("RC-ONE", RecoveryJobRequest(expected_version=env.store.read().version,
        idempotency_key="paired-running-job", group_ids=["G1"], max_provider_calls=8))["job"]
    _, owner = env.jobs.ledger.claim_next()
    step = env.jobs.ledger.dispatch(old["id"], owner, "searching", "Offline", {}, ["G1"])
    value = bundle(); configure(env, value, recover())
    result = await env.service.command(request(env.store, spoken(value, followup="Recover RO321 for G1 with maximum 4 calls")))
    successor = result["recovery_job"]
    assert successor["stage"] == "waiting_for_previous" and successor["supersedes_job_ids"] == [old["id"]]
    assert successor["max_provider_calls"] == 3 and not successor["calls_dispatched"]
    assert env.jobs.ledger.claim_next() is None
    if not unknown: env.jobs.ledger.outcome(old["id"], owner, step, "succeeded", "Offline result", {})
    env.jobs.ledger.finish(old["id"], owner, "cancelled", "Offline drained")
    claimed = env.jobs.ledger.claim_next()
    if unknown:
        assert claimed is None and env.jobs.get(successor["id"])["job"]["status"] == "needs_review"
    else: assert claimed[0]["id"] == successor["id"]
    assert len(env.ai.calls) == 1 and not env.travel.calls


@pytest.mark.asyncio
async def test_unknown_interpretation_is_not_retried(service_arrival_setup):
    env = service_arrival_setup
    async def failure(): raise ProviderError("Offline unknown", provider="OpenAI", code="timeout")
    env.ai.before_return = failure
    req = request(env.store, spoken(bundle()))
    before = stable(env.store)
    for status in (502, 503):
        with pytest.raises(DomainError) as rejected: await env.service.command(req)
        assert rejected.value.status == status
    assert len(env.ai.calls) == 1 and stable(env.store) == before and not env.jobs.list("RC-ONE")["jobs"]


@pytest.mark.asyncio
async def test_final_receipt_failure_rolls_back_both_facts_and_job(service_arrival_setup):
    env = service_arrival_setup
    value = bundle(); configure(env, value, recover())
    before, tables = stable(env.store), counts(env.store)
    with env.store.connect() as conn:
        conn.execute("CREATE TRIGGER reject_pair BEFORE INSERT ON idempotency WHEN NEW.key LIKE 'command:%' BEGIN SELECT RAISE(ABORT, 'offline pair receipt'); END")
    with pytest.raises(sqlite3.IntegrityError):
        await env.service.command(request(env.store, spoken(value, followup="Recover RO321 for G1 with maximum 4 calls")))
    assert stable(env.store) == before and counts(env.store) == tables


@pytest.mark.parametrize("fault", ["missing", "extra", "wrong_service", "wrong_arrival"])
def test_schema_is_exact_bounded_pair(fault):
    value = bundle()
    if fault == "missing": value.pop("arrival")
    elif fault == "extra": value["third_report"] = copy.deepcopy(value["arrival"])
    elif fault == "wrong_service": value["unavailability"] = copy.deepcopy(value["arrival"])
    else: value["arrival"] = copy.deepcopy(value["unavailability"])
    with pytest.raises(ValidationError): LiveInterpretation.model_validate(interpretation(operational_report=value), strict=True)


@pytest.mark.asyncio
async def test_mock_responses_receives_strict_nested_union_without_simulation_change(service_arrival_setup):
    env = service_arrival_setup
    env.settings.openai_api_key = "offline-placeholder"
    value = bundle()
    async def handle(outgoing):
        body = json.loads(outgoing.content)
        schema = body["text"]["format"]["schema"]
        branch = next(item for item in schema["$defs"].values()
            if item.get("properties", {}).get("type", {}).get("const") == "resource_unavailability_and_arrival")
        assert set(branch["properties"]) == set(branch["required"]) == {"type", "unavailability", "arrival"}
        assert branch["additionalProperties"] is False and "anyOf" in schema["properties"]["operational_report"]
        assert '"oneOf"' not in json.dumps(schema) and '"discriminator"' not in json.dumps(schema)
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text",
            "text": json.dumps(interpretation(operational_report=value, recovery=None))}]}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await AIProvider(env.settings, client=client).interpret(spoken(value), env.store.read().model_dump(mode="json"))
    assert result["operational_report"] == value
    assert "operational_report" not in Interpretation.model_json_schema()["properties"]
