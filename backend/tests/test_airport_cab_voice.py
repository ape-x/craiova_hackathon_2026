"""Saved-transfer spoken commands through real SQLite/worker, with no live I/O.

Interpretation is deterministic, supplier HTTP is MockTransport, and the suite's
socket guard prohibits all provider/audio/model/network calls.
"""
from datetime import timedelta, timezone
import json

from fastapi.testclient import TestClient
import pytest

import app.airport_cab_profile as profiles_module
from app.airport_cab_profile import AirportCabProfileError
from app.live_commands import LiveCommands
from app.main import create_app
from app.models import CommandRequest
from app.providers.ai import compact_world
from app.providers.bucharest_cab import SOURCE
from app.store import DomainError
from app.voice_context import VoiceScope, build_voice_context
from test_airport_cab_recovery_jobs import (
    NOW, PICKUP, change, env, request as typed_request, run_next, stable, transfer,
)
from test_live_commands import FakeAI, intent, interpretation


@pytest.fixture(autouse=True)
def profile_clock(env, monkeypatch):
    monkeypatch.setattr(profiles_module, "utcnow", lambda: env.clock[0])


def spoken(env, text=None, *, groups=None, modes=None, report=None, key="spoken-cab", total=4):
    ai = FakeAI()
    ai.result = interpretation(recovery=intent(incident_id="CASE", group_ids=groups or ["G1"],
        all_linked_groups=False, modes=modes or ["bus"], requires_input_changes=False),
        operational_report=report)
    service = env.jobs.service
    service.ai = ai
    service.live_commands = LiveCommands(env.store, env.jobs)
    text = text or f"Recover RO321 for G1 using the saved transfer with maximum {total} calls"
    body = CommandRequest(expected_version=env.store.read().version, idempotency_key=key, text=text)
    return service, ai, body


async def complete_typed(env, **changes):
    inputs = transfer(pickup_at=PICKUP.astimezone(timezone(timedelta(hours=2))), suitcases=0, handbags=0)
    body = typed_request(env, airport_transfers=[inputs], **changes)
    started = env.jobs.start("CASE", body)["job"]
    completed = await run_next(env.jobs)
    assert completed["id"] == started["id"] and completed["status"] == "completed", completed
    world = env.store.read()
    saved = env.jobs.airport_cab_profiles.read(world, world.groups[0])
    assert saved.model_dump(mode="json") == body.airport_transfers[0].model_dump(mode="json")
    return completed, saved


def assert_exact_queue(env, receipt, saved, *, source_calls=2, max_calls=3):
    assert "recovery_job" in receipt, receipt.get("explanation")
    job = receipt["recovery_job"]
    assert job["status"] == "queued" and job["calls_dispatched"] == 0
    assert job["group_ids"] == ["G1"] and job["max_provider_calls"] == max_calls
    assert receipt["command_interpretation_calls"] == 1
    assert receipt["recovery_total_call_limit"] == max_calls + 1
    private = env.jobs.ledger.get(job["id"], private=True)
    assert private["_request"]["airport_transfers"] == [saved.model_dump(mode="json")]
    assert len(env.source.calls) == source_calls
    return job


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "Recover RO321 for G1 using the saved transfer with maximum 4 calls",
    "Recuperează RO321 pentru G1 folosind transferul salvat cu maximum 4 apeluri",
    "Recuperează RO321 pentru G1 și folosește configurația transferului salvat cu maximum 4 apeluri",
    "Recuperează RO321 din 2030-01-04 pentru G1 folosind transferul salvat cu maximum 4 apeluri",
    "Recover RO321 for G1 using the saved transfer, limit to four calls, without bookings",
])
async def test_explicit_saved_transfer_queues_exact_zero_bags_offset_and_fresh_worker_quote(env, text):
    first, saved = await complete_typed(env)
    before = stable(env.store.read())
    service, ai, body = spoken(env, text)
    receipt = await service.command(body)
    queued = assert_exact_queue(env, receipt, saved)
    assert receipt["applied"] is False and stable(env.store.read()) == before
    replay = await service.command(body)
    assert replay["replayed"] and replay["recovery_job"]["id"] == queued["id"]
    assert len(ai.calls) == 1 and len(env.source.calls) == 2
    completed = await run_next(env.jobs)
    assert completed["status"] == "completed" and completed["calls_dispatched"] == 3, completed
    assert len(env.source.calls) == 4 and len(env.ai.decisions) == 2 and not env.flights.calls
    world = env.store.read()
    profile = env.jobs.airport_cab_profiles.public_for_case(world, "CASE")[0]
    assert profile["source_job_id"] == completed["id"] != first["id"]
    assert profile["inputs"] == saved.model_dump(mode="json")
    assert all(item.status != "confirmed" for item in world.reservations)
    assert all(item.booking_status != "confirmed" for item in world.resources)


@pytest.mark.asyncio
async def test_spoken_reuse_builds_fresh_transfer_and_connected_onward_flight_within_total_cap(env):
    change(env.store, lambda world: setattr(world.groups[0], "destination", "LHR"))
    _, saved = await complete_typed(env, modes=["bus", "flight"], max_provider_calls=5)
    service, ai, body = spoken(env, modes=["bus", "flight"], total=6)
    receipt = await service.command(body)
    assert_exact_queue(env, receipt, saved, max_calls=5)
    assert len(env.flights.calls) == 1
    result = await run_next(env.jobs)
    assert result["status"] == "completed" and result["calls_dispatched"] == 5, result
    assert len(env.source.calls) == 4 and len(env.flights.calls) == 2 and len(ai.calls) == 1
    assert len(env.ai.plans) == len(env.ai.decisions) == 2
    offer = next(item for item in env.store.read().offers if item.id in result["result"]["offer_ids"])
    assert [leg.origin for leg in offer.itinerary.legs] == ["OTP", "CRA"]
    assert offer.itinerary.legs[-1].destination == "LHR"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bus_failure", "airport_restriction"])
async def test_combined_operational_report_preserves_saved_inputs_and_selected_scope(env, kind):
    _, saved = await complete_typed(env)
    before = env.store.read()
    if kind == "bus_failure":
        resource = next(item for item in before.resources if item.source == SOURCE)
        claim = resource.id + " is unavailable"
        report = {"type": "resource_unavailability", "resource_id": resource.id,
            "resource_reference": resource.id, "report_reference": claim}
    else:
        claim = "OTP is closed for departures"
        report = {"type": "airport_restriction", "incident_id": "CASE", "airport_code": "OTP",
            "airport_reference": "OTP", "operations": ["departures"], "action": "restrict",
            "report_reference": claim}
    service, ai, body = spoken(env, claim + ". Recover RO321 for G1 using the saved transfer with maximum 4 calls", report=report)
    receipt = await service.command(body)
    queued = assert_exact_queue(env, receipt, saved)
    assert receipt["applied"] and queued["source_version"] == before.version + 1
    assert env.store.read().groups[1] == before.groups[1]
    if kind == "bus_failure":
        assert next(item for item in env.store.read().resources if item.id == resource.id).available is False
    else:
        assert any(item.airport_code == "OTP" and item.operation == "departures"
            for item in env.store.read().incidents[0].airport_restrictions)
    result = await run_next(env.jobs)
    assert result["status"] == "completed" and result["calls_dispatched"] == 3, result
    assert len(env.source.calls) == 4 and len(ai.calls) == 1
    replay = await service.command(body)
    assert replay["replayed"] and replay["report"] == receipt["report"]
    assert len(env.source.calls) == 4 and len(ai.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("omitted_condition", ["OTP is closed", "OTP este închis"])
async def test_exact_cab_id_outage_cannot_mask_a_real_omitted_airport_restriction(env, omitted_condition):
    await complete_typed(env)
    before = env.store.read()
    resource = next(item for item in before.resources if item.source == SOURCE)
    claim = resource.id + " is unavailable"
    report = {"type": "resource_unavailability", "resource_id": resource.id,
        "resource_reference": resource.id, "report_reference": claim}
    service, ai, body = spoken(env, claim + ". " + omitted_condition
        + ". Recover RO321 for G1 using the saved transfer with maximum 4 calls", report=report)
    receipt = await service.command(body)
    assert receipt["applied"] is False and "recovery_job" not in receipt
    assert stable(env.store.read()) == stable(before)
    assert len(env.jobs.list("CASE")["jobs"]) == 1 and len(env.source.calls) == 2 and len(ai.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("airport,ready_minutes,valid", [("OTP", 570, True), ("OTP", 601, False), ("LHR", 570, False)])
async def test_arrival_report_resolves_saved_profile_against_post_report_position_and_readiness(env, airport, ready_minutes, valid):
    _, saved = await complete_typed(env)
    stamp = (NOW + timedelta(minutes=ready_minutes)).isoformat()
    claim = f"Assume G1 at {airport} ready at {stamp}"
    report = {"type": "arrival_report", "incident_id": "CASE", "group_ids": ["G1"],
        "all_linked_groups": False, "airport_code": airport, "airport_reference": airport,
        "ready_at": stamp, "ready_at_reference": stamp, "report_reference": claim}
    service, ai, body = spoken(env, claim + ". Recover RO321 for G1 using the saved transfer with maximum 4 calls", report=report)
    before = env.store.read()
    receipt = await service.command(body)
    assert receipt["applied"], receipt
    current = env.store.read()
    assert current.groups[0].origin == airport and current.groups[0].ready_at == ready_minutes
    assert current.groups[1] == before.groups[1] and len(ai.calls) == 1
    if valid:
        assert_exact_queue(env, receipt, saved)
        done = await run_next(env.jobs)
        assert done["status"] == "completed" and done["calls_dispatched"] == 3, done
        assert len(env.source.calls) == 4
    else:
        assert "recovery_job" not in receipt
        assert receipt["recovery_blocked"]["code"] == "airport_transfer_setup_required"
        assert len(env.jobs.list("CASE")["jobs"]) == 1 and len(env.source.calls) == 2
        assert env.jobs.ledger.claim_next() is None


@pytest.mark.asyncio
async def test_elapsed_pickup_is_rejected_without_moving_it_or_searching(env):
    _, saved = await complete_typed(env)
    env.clock[0] = PICKUP + timedelta(minutes=1)
    before = stable(env.store.read())
    service, ai, body = spoken(env)
    receipt = await service.command(body)
    assert receipt["recovery_blocked"]["code"] == "airport_transfer_setup_required"
    assert "recovery_job" not in receipt and len(env.source.calls) == 2
    assert stable(env.store.read()) == before and len(ai.calls) == 1
    current = env.store.read()
    assert env.jobs.airport_cab_profiles.read(current, current.groups[0]) == saved


@pytest.mark.asyncio
async def test_missing_profile_does_not_invent_inputs_or_queue_partial_work(env):
    service, ai, body = spoken(env)
    receipt = await service.command(body)
    assert receipt["recovery_blocked"]["code"] == "airport_transfer_setup_required"
    assert "recovery_job" not in receipt and len(ai.calls) == 1
    assert not env.jobs.list("CASE")["jobs"] and not env.source.calls


@pytest.mark.asyncio
async def test_all_requested_groups_need_profiles_without_silently_reducing_to_configured_subset(env):
    _, saved = await complete_typed(env)
    def link_second(world):
        world.groups[1].origin, world.groups[1].destination, world.groups[1].incident_id = "OTP", "CRA", "CASE"
        world.incidents[0].group_ids.append("G2")
    change(env.store, link_second)
    service, _, body = spoken(env, "Recover RO321 for G1 and G2 using the saved transfers with maximum 6 calls", groups=["G1", "G2"])
    rejected = await service.command(body)
    assert "recovery_job" not in rejected and rejected["recovery_blocked"]["code"] == "airport_transfer_setup_required"
    service, _, body = spoken(env, key="explicit-only-configured-group")
    accepted = await service.command(body)
    assert_exact_queue(env, accepted, saved)
    assert len(env.jobs.list("CASE")["jobs"]) == 2


@pytest.mark.asyncio
async def test_low_literal_total_cap_is_not_increased_for_saved_transfer(env):
    await complete_typed(env)
    service, ai, body = spoken(env, total=3)
    receipt = await service.command(body)
    assert "recovery_job" not in receipt and receipt["recovery_total_call_limit"] == 3
    assert len(ai.calls) == 1 and len(env.source.calls) == 2
    assert len(env.jobs.list("CASE")["jobs"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", ["at 14:00", "tomorrow", "with 3 suitcases", "cu 0 bagaje", "with no handbags"])
async def test_literal_pickup_or_baggage_edits_are_not_discarded_when_model_change_flag_false(env, extra):
    await complete_typed(env)
    service, ai, body = spoken(env, f"Recover RO321 for G1 using the saved transfer {extra} with maximum 4 calls")
    receipt = await service.command(body)
    assert "recovery_job" not in receipt and receipt["recovery_blocked"]["code"] == "airport_transfer_setup_required"
    assert ai.result["recovery"]["requires_input_changes"] is False
    assert len(ai.calls) == 1 and len(env.source.calls) == 2 and len(env.jobs.list("CASE")["jobs"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("text,modes,queued", [
    ("Recover RO321 for G1 by bus with maximum 4 calls", ["bus"], True),
    ("Recover RO321 for G1 by flight with maximum 4 calls", ["flight"], True),
    ("Recover RO321 for G1 using the saved transfer with maximum 4 calls", ["flight"], False),
])
async def test_profile_existence_never_implicitly_adds_transfer_or_overrides_flight_only_modes(env, text, modes, queued):
    await complete_typed(env)
    service, _, body = spoken(env, text, modes=modes)
    receipt = await service.command(body)
    if queued:
        assert "recovery_job" in receipt, receipt
        private = env.jobs.ledger.get(receipt["recovery_job"]["id"], private=True)
        assert not private["_request"].get("airport_transfers")
        assert private["_request"]["modes"] == modes
        await run_next(env.jobs)
    else:
        assert "recovery_job" not in receipt and receipt["recovery_blocked"]["code"] == "airport_transfer_setup_required"
    assert len(env.source.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "The saved transfer exists. Recover RO321 for G1 by bus with maximum 4 calls",
    "RO321 G1 has a saved transfer. Recover RO321 for G1 by bus with maximum 4 calls",
])
async def test_descriptive_saved_transfer_reference_does_not_authorize_its_reuse(env, text):
    await complete_typed(env)
    service, _, body = spoken(env, text)
    receipt = await service.command(body)
    if receipt.get("recovery_job"):
        private = env.jobs.ledger.get(receipt["recovery_job"]["id"], private=True)
        assert not private["_request"].get("airport_transfers")
    assert len(env.source.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "Recover RO321 for G1 but do not use the saved transfer with maximum 4 calls",
    "Recover RO321 for G1 using the saved transfer if it is cheaper with maximum 4 calls",
    "Recuperează RO321 pentru G1 dar nu folosi transferul salvat cu maximum 4 apeluri",
    "Recuperează RO321 pentru G1 folosind transferul salvat dacă este mai ieftin cu maximum 4 apeluri",
    "Recover RO321 for G1 using the saved transfer to SBZ with maximum 4 calls",
    "Recuperează RO321 pentru G1 folosind transferul salvat către SBZ cu maximum 4 apeluri",
    "Recover RO321 for G1 to SBZ using the saved transfer with maximum 4 calls",
    "Recuperează RO321 pentru G1 către SBZ folosind transferul salvat cu maximum 4 apeluri",
])
async def test_negated_conditional_or_changed_route_is_not_silently_replaced_by_saved_inputs(env, text):
    await complete_typed(env)
    service, ai, body = spoken(env, text)
    receipt = await service.command(body)
    assert "recovery_job" not in receipt, receipt.get("explanation")
    assert ai.result["recovery"]["requires_input_changes"] is False
    assert len(env.source.calls) == 2 and len(ai.calls) == 1
    assert len(env.jobs.list("CASE")["jobs"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", ["to SBZ", "with 1 suitcase", "if it is cheaper"])
async def test_combined_restriction_retains_valid_report_when_saved_transfer_followup_needs_setup(env, extra):
    await complete_typed(env)
    claim = "OTP is closed for departures"
    report = {"type": "airport_restriction", "incident_id": "CASE", "airport_code": "OTP",
        "airport_reference": "OTP", "operations": ["departures"], "action": "restrict",
        "report_reference": claim}
    service, ai, body = spoken(env,
        claim + f". Recover RO321 for G1 using the saved transfer {extra} with maximum 4 calls", report=report)
    before = env.store.read()
    receipt = await service.command(body)
    assert receipt["applied"] and "recovery_job" not in receipt, receipt
    assert receipt["recovery_blocked"]["code"] == "airport_transfer_setup_required"
    assert env.store.read().version == before.version + 1
    assert env.store.read().groups == before.groups
    assert len(env.source.calls) == 2 and len(ai.calls) == 1
    assert len(env.jobs.list("CASE")["jobs"]) == 1


@pytest.mark.asyncio
async def test_only_chosen_fresh_transfer_group_gets_saved_profile(env):
    def link_second(world):
        world.groups[1].origin, world.groups[1].destination, world.groups[1].incident_id = "OTP", "CRA", "CASE"
        world.incidents[0].group_ids.append("G2")
    change(env.store, link_second)
    env.jobs.start("CASE", typed_request(env, group_ids=["G1", "G2"], max_provider_calls=5,
        airport_transfers=[transfer("G1"), transfer("G2")]))
    completed = await run_next(env.jobs)
    assert completed["status"] == "completed" and completed["calls_dispatched"] == 5, completed
    world = env.store.read()
    assert len(world.offers) == 1  # Application limit: fleet count remains unknown.
    profiles = env.jobs.airport_cab_profiles.public_for_case(world, "CASE")
    selected = [item for item in profiles if item["configured"]]
    assert len(selected) == 1 and selected[0]["group_id"] == world.offers[0].group_id
    assert selected[0]["source_job_id"] == completed["id"]
    assert len([item for item in profiles if item.get("reason") == "missing"]) == 1


@pytest.mark.asyncio
async def test_uncertain_source_never_saves_profile_before_valid_chosen_commit(env):
    env.source.timeout_phase = "vehicles"
    env.jobs.start("CASE", typed_request(env))
    result = await run_next(env.jobs)
    assert result["status"] == "unknown_outcome" and result["calls_dispatched"] == 2, result
    current = env.store.read()
    with pytest.raises(AirportCabProfileError, match="No saved"):
        env.jobs.airport_cab_profiles.read(current, current.groups[0])
    assert not current.offers and not env.ai.decisions and len(env.source.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
async def test_profile_write_failure_rolls_back_plan_receipt_and_profile_in_one_transaction(env, monkeypatch, existing):
    if existing:
        await complete_typed(env)
    before = stable(env.store.read())
    with env.store.connect() as conn:
        profiles_before = [tuple(item) for item in conn.execute("SELECT * FROM airport_cab_profiles")]
    original = env.jobs.airport_cab_profiles.save_in_transaction
    def fail_after_write(*args, **kwargs):
        original(*args, **kwargs)
        raise DomainError(409, "Offline atomic profile failure after write")
    monkeypatch.setattr(env.jobs.airport_cab_profiles, "save_in_transaction", fail_after_write)
    queued = env.jobs.start("CASE", typed_request(env, idempotency_key="rollback-profile-job"))["job"]
    result = await run_next(env.jobs)
    assert result["status"] == "needs_review" and result["calls_dispatched"] == 3, result
    assert stable(env.store.read()) == before
    with env.store.connect() as conn:
        assert [tuple(item) for item in conn.execute("SELECT * FROM airport_cab_profiles")] == profiles_before
        assert not conn.execute("SELECT 1 FROM idempotency WHERE key=?", ("recovery-job-apply:" + queued["id"],)).fetchone()


@pytest.mark.asyncio
async def test_authenticated_profile_endpoint_and_scoped_voice_context_do_not_leak_private_identity(env):
    def private_names(world):
        world.groups[0].members = ["PRIVATE ONE", "PRIVATE TWO"]
    change(env.store, private_names)
    _, saved = await complete_typed(env)
    service, ai, body = spoken(env)
    await service.command(body)
    compact = compact_world(ai.calls[0][1])
    summary = compact["groups"][0]["airport_transfer_profile"]
    assert summary["configured"] and summary["requires_current_validation"]
    assert summary["pickup_at"] == saved.pickup_at.isoformat()
    assert summary["minimum_total_calls"] == 4
    assert not any(key in summary for key in ("suitcases", "handbags", "scope_fingerprint", "members", "token"))
    context = build_voice_context(env.store.read(), VoiceScope(group_id="G1"), airport_cab_profiles=env.jobs.airport_cab_profiles)
    facts = json.loads(context)
    assert len(facts["groups"]) == 1 and facts["groups"][0]["saved_airport_transfer_scope_valid"] is True
    for private in ("PRIVATE ONE", "PRIVATE TWO", "offline-one", "scope_fingerprint", "pickup_at", "suitcases", "handbags", "G2"):
        assert private not in context
    changed = env.store.read()
    changed.groups[0].origin = "LHR"
    stale = json.loads(build_voice_context(changed, VoiceScope(group_id="G1"), airport_cab_profiles=env.jobs.airport_cab_profiles))
    assert stale["groups"][0]["saved_airport_transfer_scope_valid"] is False
    env.settings.operator_token = "offline-profile-operator-secret"
    app = create_app(env.settings)
    app.state.jobs.start_worker = lambda: None
    with TestClient(app) as client:
        path = "/api/incidents/CASE/airport-transfer-profiles"
        assert client.get(path).status_code == 401
        response = client.get(path, headers={"Authorization": "Bearer offline-profile-operator-secret"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["world_version"] == env.store.read().version
        assert len(data["profiles"]) == 1 and data["profiles"][0]["inputs"] == saved.model_dump(mode="json")
        for private in ("PRIVATE ONE", "PRIVATE TWO", "offline-one", "scope_fingerprint"):
            assert private not in response.text
        assert client.get("/api/incidents/ABSENT/airport-transfer-profiles", headers={"Authorization": "Bearer offline-profile-operator-secret"}).status_code == 404
    assert len(env.source.calls) == 2 and len(ai.calls) == 1
