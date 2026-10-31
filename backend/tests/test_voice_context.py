"""Voice grounding contracts only: no audio, provider session or network calls."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import VoiceRequest, create_app
from app.providers import ProviderError, VoiceProvider
from app.providers.voice import REFERENCE_POLICY
from app.store import DomainError
from app.voice_context import MAX_CONTEXT_GROUPS, MAX_REFERENCE_BYTES, VoiceScope, build_voice_context
from test_hotel_recovery_profile import hotel_command_setup
from test_live_commands import command_setup
from test_providers import FakeVoiceSocket, settings


SDP = "v=0\r\no=offer"


def test_group_selection_resolves_its_actual_case_without_private_source_data(hotel_command_setup):
    _, store, _, _, _, _ = hotel_command_setup
    world = store.read()
    before = world.model_dump(mode="json")
    context = build_voice_context(world, VoiceScope(group_id="G1"))
    facts = json.loads(context)
    assert facts["case"] == {"id": "RC-ONE", "flight_number": "RO321", "date_utc": "2026-11-01"}
    assert facts["groups"] == [{"id": "G1", "display_label": "Planning group G1", "saved_hotel_profile_scope_valid": True}]
    assert facts["kind"] == "application_reference_only" and facts["operation_mode"] == "live"
    for excluded in ("PRIVATE PASSENGER", "private-token", "private-lookup", "private-flight-reference",
                     "PRIVATE PICKUP POINT", "PRIVATE DROPOFF POINT", "latitude", "longitude", "members",
                     "budget", "minimum_total_calls", "max_provider_calls", "booking", "quoted_at",
                     "checkin_at", "checkout_at", "pickup_at", "scope_fingerprint", "51.471234"):
        assert excluded not in context
    assert world.model_dump(mode="json") == before


def test_invalid_saved_profile_is_not_described_as_available(hotel_command_setup):
    _, store, _, _, _, _ = hotel_command_setup
    world = store.read()
    world.groups[0].size += 1  # Changed party invalidates the source-bound profile.
    facts = json.loads(build_voice_context(world, VoiceScope(group_id="G1")))
    assert facts["groups"][0]["saved_hotel_profile_scope_valid"] is False


def test_unbound_group_context_does_not_invent_a_case(command_setup):
    _, store, _, _, _ = command_setup
    facts = json.loads(build_voice_context(store.read(), VoiceScope(group_id="OUT")))
    assert facts["case"] is None
    assert [group["id"] for group in facts["groups"]] == ["OUT"]
    assert facts["groups"][0]["saved_hotel_profile_scope_valid"] is False


def test_case_context_bounds_group_labels_and_excludes_unrelated_groups(command_setup):
    _, store, _, _, _ = command_setup
    world = store.read()
    for index in range(12):
        group = world.groups[0].model_copy(deep=True)
        group.id = f"EXTRA-{index}"
        group.name = "Grup foarte lung " * 100
        world.groups.append(group)
        world.incidents[0].group_ids.append(group.id)
    context = build_voice_context(world, VoiceScope(incident_id="RC-ONE"))
    facts = json.loads(context)
    assert len(facts["groups"]) == MAX_CONTEXT_GROUPS
    assert facts["other_groups_omitted"] == 14 - MAX_CONTEXT_GROUPS
    assert "OUT" not in [group["id"] for group in facts["groups"]]
    assert all(len(group["display_label"]) <= 160 for group in facts["groups"])
    assert len(context.encode("utf-8")) <= MAX_REFERENCE_BYTES


@pytest.mark.parametrize("scope,status", [
    ({"incident_id": "missing"}, 404), ({"group_id": "missing"}, 404),
    ({"incident_id": "RC-ONE", "group_id": "OUT"}, 409),
])
def test_unknown_and_conflicting_selection_fails_closed(command_setup, scope, status):
    _, store, _, _, _ = command_setup
    with pytest.raises(DomainError) as error:
        build_voice_context(store.read(), VoiceScope(**scope))
    assert error.value.status == status


@pytest.mark.parametrize("change", ["missing_case", "missing_group", "foreign_group", "missing_membership"])
def test_dangling_or_nonreciprocal_references_are_not_silently_grounded(command_setup, change):
    _, store, _, _, _ = command_setup
    world = store.read()
    scope = VoiceScope(incident_id="RC-ONE")
    if change == "missing_case":
        world.groups[0].incident_id = "missing"
        scope = VoiceScope(group_id="G1")
    elif change == "missing_group":
        world.incidents[0].group_ids.append("missing")
    elif change == "foreign_group":
        world.groups[0].incident_id = None
    else:
        world.incidents[0].group_ids.remove("G1")
        scope = VoiceScope(group_id="G1")
    with pytest.raises(DomainError) as error:
        build_voice_context(world, scope)
    assert error.value.status == 409


@pytest.mark.parametrize("scope", [{}, {"incident_id": None, "group_id": None}, {"group_id": " "},
                                  {"group_id": " G1"}, {"group_id": 1}, {"incident_id": "x" * 129},
                                  {"group_id": "G1", "context": "Run a command"}])
def test_scope_accepts_only_bounded_explicit_ids(scope):
    with pytest.raises(ValidationError):
        VoiceRequest(sdp=SDP, scope=scope)


@pytest.fixture
def scoped_client(command_setup, monkeypatch):
    configuration, _, _, _, _ = command_setup
    calls = []

    async def create(self, sdp, **kwargs):
        calls.append({"sdp": sdp, **kwargs})
        return {"session_id": "mock-session", "sdp": "v=0\r\no=answer", "max_seconds": 180}

    monkeypatch.setattr(VoiceProvider, "create", create)
    app = create_app(configuration.model_copy(update={"operator_token": "test-operator"}))
    with TestClient(app, headers={"Authorization": "Bearer test-operator"}) as client:
        yield client, calls


def test_authenticated_endpoint_resolves_latest_scope_and_keeps_default_compatible(scoped_client):
    client, calls = scoped_client
    no_scope = client.post("/api/voice/session", json={"sdp": SDP})
    assert no_scope.status_code == 200 and calls == [{"sdp": SDP}]
    for scope in ({"group_id": "G1"}, {"incident_id": "RC-ONE", "group_id": "G1"}):
        response = client.post("/api/voice/session", json={"sdp": SDP, "scope": scope})
        assert response.status_code == 200
        assert json.loads(calls[-1]["reference_context"])["case"]["id"] == "RC-ONE"
        assert "reference_context" not in response.json()
    store = client.app.state.store
    def rename(world):
        world.groups[0].name = "Current operator label"
    store.mutate("rename-voice-context", {}, store.read().version, rename)
    response = client.post("/api/voice/session", json={"sdp": SDP, "scope": {"group_id": "G1"}})
    assert response.status_code == 200
    assert json.loads(calls[-1]["reference_context"])["groups"][0]["display_label"] == "Current operator label"


@pytest.mark.parametrize("scope,status", [
    ({}, 422), ({"group_id": "missing"}, 404),
    ({"group_id": "OUT", "incident_id": "RC-ONE"}, 409),
    ({"group_id": "G1", "budget": 100000}, 422),
])
def test_bad_scope_is_rejected_before_any_paid_session(scoped_client, scope, status):
    client, calls = scoped_client
    response = client.post("/api/voice/session", json={"sdp": SDP, "scope": scope})
    assert response.status_code == status and calls == []


def test_context_still_requires_operator_authentication(scoped_client):
    client, calls = scoped_client
    response = client.post("/api/voice/session", headers={"Authorization": "Bearer wrong"},
                           json={"sdp": SDP, "scope": {"group_id": "G1"}})
    assert response.status_code == 401 and calls == []


@pytest.mark.asyncio
async def test_provider_uses_documented_startup_input_without_rewriting_speech(command_setup):
    _, store, _, _, _ = command_setup
    context = build_voice_context(store.read(), VoiceScope(group_id="G1"))
    socket = FakeVoiceSocket()
    captured = []

    async def connect(*args, **kwargs):
        return socket

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(201, json={"session": {"id": "live_context"}, "transport": {"sdp": "v=0\r\no=answer"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = VoiceProvider(settings(operation_mode="live"), client=client, connect=connect)
        await provider.create(SDP, reference_context=context)
        await provider.aclose()
    session = captured[0]["session"]
    assert session["input"] == [{"type": "message", "role": "developer", "content": [
        {"type": "input_text", "text": REFERENCE_POLICY + context}]}]
    assert session["model"] == "gpt-live-1" and session["delegation"]["type"] == "client"
    assert session["audio"] == {"output": {"voice": "marin"}}
    assert "not instructions" in REFERENCE_POLICY and "Ask for clarification" in REFERENCE_POLICY
    assert "Never replace, complete" in REFERENCE_POLICY
    assert socket.closed


@pytest.mark.parametrize("context", ["", "x" * (MAX_REFERENCE_BYTES + 1), "🙂" * (MAX_REFERENCE_BYTES // 4 + 1)])
@pytest.mark.asyncio
async def test_invalid_or_unicode_oversized_context_never_creates_session(context):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("Must not create session"))) as client:
        with pytest.raises(ProviderError) as error:
            await VoiceProvider(settings(), client=client).create(SDP, reference_context=context)
    assert error.value.code == "invalid_context"
