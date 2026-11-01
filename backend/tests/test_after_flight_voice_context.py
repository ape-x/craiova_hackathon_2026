"""Saved-input reference context through real adapters with offline transports only."""
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.after_flight_inputs import AfterFlightTransferInput
from app.after_flight_profile import AfterFlightProfiles
from app.airport_catalog import lookup_airport
from app.config import Settings
from app.main import create_app
from app.models import Airport
from app.providers import AIProvider, VoiceProvider
from app.providers.ai import compact_world
from app.providers.voice import REFERENCE_POLICY
from app.voice_context import MAX_REFERENCE_BYTES, VoiceScope, build_voice_context
from test_airport_cab_profile import NOW, setup
from test_providers import FakeVoiceSocket, completed, settings


@pytest.fixture
def references(setup):
    store, initial_profiles, _ = setup
    def change(world):
        world.groups[0].origin, world.groups[0].destination = "LHR", "CRA"
        for code in ("LHR", "CRA"):
            if not any(item.id == code for item in world.airports):
                record = lookup_airport(code)
                world.airports.append(Airport(id=code, code=code, name=record["name"], lat=record["lat"], lon=record["lon"]))
    store.mutate("offline-afterflight-reference", {}, store.read().version, change)
    profiles = AfterFlightProfiles(store)
    inputs = AfterFlightTransferInput(group_id="G1", via_airport="OTP", destination_airport="CRA",
        pickup_window_start="2030-01-04T12:00:00+02:00", pickup_window_end="2030-01-04T15:00:00+02:00",
        airport_processing_minutes=60, suitcases=0, handbags=1, max_inbound_options=2)
    world = store.read()
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        profiles.save_in_transaction(conn, world, world.groups[0], inputs, "JOB-AFTERFLIGHT-REFERENCE", now=NOW)
    return store, initial_profiles, profiles


def context(references, group_id="G1"):
    store, initial, after = references
    return build_voice_context(store.read(), VoiceScope(group_id=group_id),
        airport_cab_profiles=initial, after_flight_profiles=after)


def test_startup_distinguishes_scopes_without_sending_inputs_or_spend_authority(references):
    store, _, _ = references
    before = store.read().model_dump(mode="json", exclude={"sim_time"})
    value = context(references)
    facts = json.loads(value)
    assert facts["groups"][0]["saved_after_flight_transfer_scope_valid"] is True
    assert facts["groups"][0]["saved_airport_transfer_scope_valid"] is False
    assert len(value.encode()) <= MAX_REFERENCE_BYTES
    for private in ("PRIVATE PASSENGER", "PRIVATE-TOKEN", "scope_fingerprint", "source_job_id", "saved_at",
                    "pickup_window_start", "pickup_window_end", "airport_processing_minutes", "suitcases", "handbags",
                    "minimum_total_calls", "two_variant_total_calls", "max_inbound_options"):
        assert private not in value
    assert store.read().model_dump(mode="json", exclude={"sim_time"}) == before
    assert json.loads(context(references, "G2"))["groups"][0]["saved_after_flight_transfer_scope_valid"] is False


@pytest.mark.parametrize("change", ["party", "origin", "date", "corrupt"])
def test_stale_or_corrupt_afterflight_profile_cannot_appear_scope_valid(references, change):
    store, _, _ = references
    if change == "corrupt":
        with store.connect() as conn:
            conn.execute("UPDATE after_flight_profiles SET data=? WHERE group_id='G1'", ("PRIVATE corrupt data",))
    else:
        def mutate(world):
            if change == "party": world.groups[0].adult_count = 0
            elif change == "origin": world.groups[0].origin = "OTP"
            else: world.incidents[0].date_utc = "2030-01-05"
        store.mutate("offline-stale-afterflight-"+change, {}, store.read().version, mutate)
    result = context(references)
    assert json.loads(result)["groups"][0]["saved_after_flight_transfer_scope_valid"] is False
    assert "PRIVATE corrupt data" not in result


def test_context_scope_is_not_a_claim_of_current_route_or_quote_eligibility(references):
    store, _, _ = references
    assert store.read().incidents[0].unavailable_airport_code == "OTP"
    assert json.loads(context(references))["groups"][0]["saved_after_flight_transfer_scope_valid"] is True
    assert "checks current constraints" in REFERENCE_POLICY
    assert "not a flight or vehicle offer" in REFERENCE_POLICY


def test_authenticated_voice_endpoint_resolves_separate_profile_before_session(references, monkeypatch):
    store, _, _ = references
    calls = []
    async def create(self, sdp, **kwargs):
        calls.append({"sdp": sdp, **kwargs})
        return {"session_id": "offline-profile-session", "sdp": "v=0\r\no=answer", "max_seconds": 180}
    monkeypatch.setattr(VoiceProvider, "create", create)
    config = Settings(_env_file=None, environment="test", operation_mode="live", database_path=store.path,
        operator_token="offline-voice-reference")
    with TestClient(create_app(config)) as client:
        body = {"sdp": "v=0\r\no=offer", "scope": {"incident_id": "CASE", "group_id": "G1"}}
        assert client.post("/api/voice/session", json=body).status_code == 401 and not calls
        headers = {"Authorization": "Bearer offline-voice-reference"}
        missing = {**body, "scope": {"group_id": "MISSING"}}
        assert client.post("/api/voice/session", json=missing, headers=headers).status_code == 404 and not calls
        response = client.post("/api/voice/session", json=body, headers=headers)
        assert response.status_code == 200 and len(calls) == 1
        group = json.loads(calls[0]["reference_context"])["groups"][0]
        assert group["saved_after_flight_transfer_scope_valid"] is True
        assert group["saved_airport_transfer_scope_valid"] is False
        assert "reference_context" not in response.json()
        assert client.app.state.jobs.list("CASE")["jobs"] == []


@pytest.mark.asyncio
async def test_responses_receives_separate_bounded_profile_summary(references):
    store, _, profiles = references
    world = store.read()
    value = world.model_dump(mode="json")
    summary = profiles.summary(world, world.groups[0])
    value["after_flight_transfer_profiles"] = {"G1": summary}
    before = copy.deepcopy(value)
    compact = compact_world(value)
    assert compact["groups"][0]["after_flight_transfer_profile"] == summary
    assert "after_flight_transfer_profile" not in compact["groups"][1]
    assert value == before
    calls = []
    answer = {"events": [], "strategies": [], "focus_id": None, "operational_report": None,
        "explanation": "Use the exact saved inputs after backend checks.", "recovery": {
            "action": "start", "incident_id": "CASE", "group_ids": ["G1"], "all_linked_groups": False,
            "objective": "balanced", "modes": ["flight", "bus"], "max_total_calls": 7,
            "call_limit_reference": "maximum 7 calls", "requires_input_changes": False}}
    def handle(request):
        calls.append(json.loads(request.content))
        assert "PRIVATE PASSENGER" not in request.content.decode() and "PRIVATE-TOKEN" not in request.content.decode()
        assert "scope_fingerprint" not in request.content.decode()
        assert '"after_flight_transfer_profile"' in request.content.decode().replace('\\"', '"')
        assert "not a promise of two results" in calls[-1]["instructions"]
        return httpx.Response(200, json=completed(answer))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        parsed = await AIProvider(settings(), client=client).interpret(
            "Recover CASE for G1 using saved transfer after the flight with maximum 7 calls", value)
    assert parsed["recovery"] == answer["recovery"] and len(calls) == 1


@pytest.mark.asyncio
async def test_voice_adapter_carries_boolean_reference_without_new_audio_or_permissions(references):
    value = context(references)
    socket, requests = FakeVoiceSocket(), []
    async def connect(*args, **kwargs): return socket
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(201, json={"session": {"id": "offline_afterflight_context"}, "transport": {"sdp": "v=0\r\no=answer"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = VoiceProvider(settings(operation_mode="live"), client=client, connect=connect)
        await provider.create("v=0\r\no=offer", reference_context=value)
        await provider.aclose()
    assert len(requests) == 1 and socket.closed
    session = requests[0]["session"]
    assert session["model"] == "gpt-live-1" and session["delegation"]["type"] == "client"
    assert session["input"][0]["content"] == [{"type": "input_text", "text": REFERENCE_POLICY + value}]
    assert "Saved initial-pickup and after-flight-transfer configurations are distinct" in REFERENCE_POLICY
    assert "Generate <|SpawnThinking|>" in session["instructions"]
    assert not any("input_audio" in item.get("type", "") for item in socket.sent)
