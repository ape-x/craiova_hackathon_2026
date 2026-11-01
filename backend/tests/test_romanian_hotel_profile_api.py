"""Authenticated saved-input reads and HTTP command → worker, without live I/O."""
from datetime import timedelta
import json

from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.providers.ai import compact_world
from app.providers.voice import VoiceProvider
from app.recovery_jobs import RecoveryJobs
from app.romanian_hotel_recovery import RomanianHotelRecoveryInputs
from test_live_commands import FakeAI, intent, interpretation
from test_recovery_jobs import setup, run_next
from test_romanian_hotel_recovery import romania, fixture_source_terms, request as package_request


URL = "/api/incidents/CASE/groups/G1/romanian-hotel-recovery-profile"


@pytest.fixture
def profile_client(romania, monkeypatch):
    _, original_jobs, ai, travel, coach, hotel, fx, _, _ = romania
    # Use the real ASGI routes; run the durable worker explicitly in the test.
    monkeypatch.setattr(RecoveryJobs, "start_worker", lambda self: None)
    app = create_app(original_jobs.settings.model_copy(update={"operator_token": "offline-operator"}))
    jobs = app.state.jobs
    jobs.ai, jobs.travel = ai, travel
    jobs.transfero_provider, jobs.hotel_provider, jobs.fx_provider = coach, hotel, fx
    with TestClient(app, headers={"Authorization": "Bearer offline-operator"}) as client:
        yield client


def save_fixture(client, env):
    store, jobs = client.app.state.store, client.app.state.jobs
    world = store.read()
    with store.connect() as conn:
        conn.execute("BEGIN")
        jobs.romanian_hotel_profiles.save_in_transaction(conn, world, world.groups[0], env[-2], "FIXTURE-JOB")


def test_profile_read_requires_operator_auth(profile_client):
    response = profile_client.get(URL, headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401
    assert "profiles" not in response.json()


def test_missing_and_saved_profile_receipts_are_exact_and_read_only(profile_client, romania):
    store = profile_client.app.state.store
    before = store.read().model_dump(exclude={"sim_time"})
    missing = profile_client.get(URL)
    assert missing.status_code == 200
    assert missing.json() == {"world_version": before["version"], "incident_id": "CASE",
                              "group_id": "G1", "configured": False, "reason": "missing"}
    save_fixture(profile_client, romania)
    saved = profile_client.get(URL)
    assert saved.status_code == 200
    data = saved.json()
    assert data["configured"] and data["group_id"] == "G1" and data["incident_id"] == "CASE"
    assert data["world_version"] == before["version"] and data["source_job_id"] == "FIXTURE-JOB"
    assert data["inputs"] == RomanianHotelRecoveryInputs.model_validate(romania[-2]).model_dump(mode="json")
    for private in ("scope_fingerprint", "members", "token", "Fixture adult", "G2"):
        assert private not in saved.text
    assert store.read().model_dump(exclude={"sim_time"}) == before
    assert not romania[4].calls and not romania[5].calls and not romania[2].plans


@pytest.mark.parametrize("url", [URL.replace("CASE", "MISSING"), URL.replace("G1", "MISSING")])
def test_profile_read_does_not_select_a_replacement_scope(profile_client, url):
    assert profile_client.get(url).status_code == 404


@pytest.mark.parametrize("change", ["group", "case", "mode"])
def test_profile_read_rejects_changed_scope(profile_client, romania, change):
    save_fixture(profile_client, romania)
    store = profile_client.app.state.store
    def update(world):
        if change == "group": world.groups[0].incident_id = None
        elif change == "case": world.incidents[0].group_ids.remove("G1")
        else: world.mode = "simulation"
    store.mutate("profile-scope-change", {}, store.read().version, update)
    assert profile_client.get(URL).status_code == 409


def test_stale_identity_and_corrupt_profile_do_not_return_inputs(profile_client, romania):
    save_fixture(profile_client, romania)
    store = profile_client.app.state.store
    store.mutate("change-party", {}, store.read().version,
                 lambda world: setattr(world.groups[0], "members", ["Changed adult A", "Changed adult B"]))
    stale = profile_client.get(URL).json()
    assert not stale["configured"] and stale["reason"] == "stale" and "inputs" not in stale
    with store.connect() as conn:
        conn.execute("UPDATE romanian_hotel_profiles SET data='not-json' WHERE group_id='G1'")
    invalid = profile_client.get(URL).json()
    assert not invalid["configured"] and invalid["reason"] == "invalid" and "inputs" not in invalid


def test_voice_reference_contains_only_romanian_profile_validity(profile_client, romania, monkeypatch):
    save_fixture(profile_client, romania)
    calls = []
    async def create(self, sdp, **kwargs):
        calls.append(kwargs)
        return {"session_id": "offline-session", "sdp": "v=0\r\no=offline-answer", "max_seconds": 45}
    monkeypatch.setattr(VoiceProvider, "create", create)
    body = {"sdp": "v=0\r\no=offline-request", "scope": {"incident_id": "CASE", "group_id": "G1"}}
    assert profile_client.post("/api/voice/session", json=body).status_code == 200
    facts = json.loads(calls[-1]["reference_context"])
    group = facts["groups"][0]
    assert group["id"] == "G1" and group["saved_romanian_hotel_profile_scope_valid"] is True
    for private in ("suitcases", "handbags", "checkin_at", "scope_fingerprint", "saved_at", "FIXTURE-JOB"):
        assert private not in calls[-1]["reference_context"]
    store = profile_client.app.state.store
    store.mutate("invalidate-profile", {}, store.read().version,
                 lambda world: setattr(world.groups[0], "size", 3))
    assert profile_client.post("/api/voice/session", json=body).status_code == 200
    assert json.loads(calls[-1]["reference_context"])["groups"][0]["saved_romanian_hotel_profile_scope_valid"] is False


def test_compact_context_exposes_only_bounded_summary_fields(profile_client, romania):
    save_fixture(profile_client, romania)
    world = profile_client.app.state.store.read()
    summary = profile_client.app.state.jobs.romanian_hotel_profiles.summary(world, world.groups[0])
    summary.update(inputs={"suitcases": 2}, scope_fingerprint="PRIVATE-HASH", source_job_id="PRIVATE-JOB")
    context = world.model_dump(mode="json")
    context["romanian_hotel_profiles"] = {"G1": summary}
    compact = compact_world(context)
    supplied = compact["groups"][0]["romanian_hotel_recovery_profile"]
    assert supplied["configured"] and supplied["minimum_total_calls"] == 9
    assert supplied["checkin_at"] == RomanianHotelRecoveryInputs.model_validate(romania[-2]).model_dump(mode="json")["checkin_at"]
    assert not {"inputs", "scope_fingerprint", "source_job_id"}.intersection(supplied)
    assert "romanian_hotel_recovery_profile" not in compact["groups"][1]


@pytest.mark.asyncio
async def test_http_typed_plan_then_saved_command_sources_a_fresh_complete_journey(profile_client, romania):
    jobs, store = profile_client.app.state.jobs, profile_client.app.state.store
    typed = profile_client.post("/api/incidents/CASE/recovery-jobs", json=package_request(romania).model_dump(mode="json"))
    assert typed.status_code == 202, typed.text
    first = await run_next(jobs)
    assert first["status"] == "completed", first
    initial_profile = profile_client.get(URL).json()
    assert initial_profile["configured"] and initial_profile["source_job_id"] == first["id"]
    other_before = store.read().groups[1].model_dump()
    before_world = store.read().model_dump(exclude={"sim_time"})
    ai = FakeAI()
    ai.result = interpretation(recovery=intent(incident_id="CASE", group_ids=["G1"], all_linked_groups=False,
                                             modes=["flight", "hotel", "bus"]))
    profile_client.app.state.service.ai = ai
    body = {"expected_version": store.read().version, "idempotency_key": "http-saved-romania-command",
            "text": "Recover CASE for G1 using the saved Romanian hotel configuration with maximum 9 calls"}
    queued = profile_client.post("/api/commands", json=body)
    assert queued.status_code == 200, queued.text
    receipt = queued.json()
    assert receipt["command_interpretation_calls"] == 1 and receipt["recovery_total_call_limit"] == 9
    assert receipt["recovery_job"]["max_provider_calls"] == 8
    assert receipt["recovery_job"]["romanian_hotel_recovery"] == initial_profile["inputs"]
    assert store.read().model_dump(exclude={"sim_time"}) == before_world
    # A new observation timestamp represents new supplier evidence in this offline test.
    romania[-1]["now"] += timedelta(seconds=1)
    fresh = await run_next(jobs)
    assert fresh["status"] == "completed" and fresh["calls_dispatched"] == 8, fresh
    assert len(romania[4].calls) == len(romania[5].calls) == 4
    assert len(romania[3].calls) == len(romania[2].plans) == len(romania[2].decisions) == 2
    after = store.read()
    assert after.groups[1].model_dump() == other_before
    assert not any(item.booking_status == "confirmed" for item in after.resources)
    proposal = next(item for item in after.offers if item.id == fresh["result"]["offer_ids"][0])
    assert [leg.kind for leg in proposal.itinerary.legs] == ["bus", "hotel", "bus", "flight"]
    assert profile_client.get(URL).json()["source_job_id"] == fresh["id"]
    replay = profile_client.post("/api/commands", json=body)
    assert replay.status_code == 200 and replay.json()["replayed"]
    assert len(ai.calls) == 1 and jobs.ledger.claim_next() is None
