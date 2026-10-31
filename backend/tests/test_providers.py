"""Contract tests with mocked HTTP only. These do NOT verify account access."""
import copy
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.providers import AIProvider, FlightProvider, ProviderError, VoiceProvider


def settings(**overrides):
    return SimpleNamespace(**({"openai_api_key": "test-key", "fr24_api_key": "test-fr24", "request_timeout_seconds": 1, "fr24_result_limit": 5} | overrides))


@pytest.fixture
def world():
    return {
        "version": 12, "epoch": "2026-10-31T00:00:00+02:00", "budget": 45000, "currency": "EUR",
        "airports": [{"id": "CRA", "available": True}, {"id": "OTP", "available": True, "diversion_eligible": True}],
        "resources": [{"id": "B12", "kind": "bus", "capacity": 12}, {"id": "F201", "kind": "flight"}],
        "groups": [{"id": "G001", "size": 4, "token": "private-passenger-access-token"}],
        "offers": [],
    }


def interpretation():
    return {"events": [{"type": "resource_unavailable", "target_id": "B12", "value": None}], "strategies": [{"id": "fast", "label": "Sosire rapidă", "objective": "fastest", "preferred_airport": None, "weights": {"recovery": 10, "arrival": 3, "cost": 1, "stability": 2}}], "explanation": "Voi compara alternativele pentru B12.", "focus_id": "B12"}


def completed(value):
    return {"status": "completed", "model": "gpt-6.1-sol", "service_tier": "ultrafast", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(value)}]}]}


@pytest.mark.asyncio
async def test_responses_payload_parsing_and_no_token_disclosure(world):
    original = copy.deepcopy(world)
    def handler(request):
        assert request.url.path == "/v1/responses"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "gpt-6.1-sol"
        assert body["service_tier"] == "ultrafast"
        assert body["store"] is False
        assert body["text"]["format"]["strict"] is True
        assert body["text"]["format"]["type"] == "json_schema"
        assert "private-passenger-access-token" not in request.content.decode()
        return httpx.Response(200, json=completed(interpretation()))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AIProvider(settings(), client=client)
        actual = await provider.interpret("Autocarul B12 s-a defectat", world)
    assert actual["events"][0]["target_id"] == "B12"
    assert provider.last_interpretation["latency_ms"] >= 0
    assert world == original


@pytest.mark.asyncio
async def test_two_constraints_normalize_only_known_presentation_ids(world):
    obj = interpretation()
    obj["events"] = [{"type": "resource_capacity", "target_id": " bus_b12 ", "value": 9}, {"type": "group_deadline", "target_id": "group_g001", "value": 1320}]
    obj["focus_id"] = " resource_b12 "
    obj["strategies"][0]["preferred_airport"] = " airport_otp "
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=completed(obj)))) as client:
        result = await AIProvider(settings(), client=client).interpret("B12 are 9 locuri; G001 trebuie să ajungă până la 22:00.", world)
    assert result["events"] == [{"type": "resource_capacity", "target_id": "B12", "value": 9}, {"type": "group_deadline", "target_id": "G001", "value": 1320}]
    assert result["focus_id"] == "B12" and result["strategies"][0]["preferred_airport"] == "OTP"


@pytest.mark.parametrize("mutate", [
    lambda obj: obj["events"][0].update(target_id="UNKNOWN"),
    lambda obj: obj["events"][0].update(type="resource_capacity", value=-1),
    lambda obj: obj["events"][0].update(type="resource_capacity", value=1.5),
    lambda obj: obj["events"][0].update(type="resource_capacity", value=True),
    lambda obj: obj.update(focus_id="unknown"),
    lambda obj: obj.update(unexpected="do not accept"),
])
@pytest.mark.asyncio
async def test_invalid_generated_commands_fail_closed(world, mutate):
    obj = interpretation()
    mutate(obj)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=completed(obj)))) as client:
        with pytest.raises(ProviderError, match="invalid command"):
            await AIProvider(settings(), client=client).interpret("update", world)


@pytest.mark.asyncio
async def test_refusal_and_incomplete_do_not_become_ai_success(world):
    for body in [{"status": "incomplete", "output": []}, {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "No"}]}]}]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
            with pytest.raises(ProviderError):
                await AIProvider(settings(), client=client).interpret("update", world)


@pytest.mark.asyncio
async def test_decisions_selects_only_dynamic_validated_id(world):
    candidates = [{"id": "plan-live-42", "strategy": {"objective": "fastest"}, "validation": {"valid": True}, "recovered_passengers": 19, "total_cost": 534.5, "average_arrival": 940, "exceptions": [{"group_id": "G002"}]}]
    def handler(request):
        body = json.loads(request.content)
        assert request.url.path == "/v1/decisions"
        assert body["model"] == "gpt-6-luna"
        assert body["questions"][0]["choices"][0]["value"] == "plan-live-42"
        assert body["input"][0]["content"][0]["type"] == "input_json"
        assert "private-passenger-access-token" not in request.content.decode()
        return httpx.Response(200, json={"answers": [{"type": "choice", "name": "recovery_plan", "choice": "plan-live-42", "confidence": 0.8}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AIProvider(settings(), client=client).choose(world, candidates, "Ajungeți rapid")
    assert result["plan_id"] == "plan-live-42"
    assert "534.50" in result["explanation"] and "1 grupuri" in result["explanation"]
    assert result["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_decisions_rejects_unvalidated_candidates_without_network(world):
    def forbidden(_):
        pytest.fail("unvalidated candidates must never reach the network")
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(ProviderError, match="unvalidated"):
            await AIProvider(settings(), client=client).choose(world, [{"id": "x", "validation": {"valid": False}}], "choose")


@pytest.mark.asyncio
async def test_decisions_refusal_or_unknown_choice_is_error(world):
    for answer in [{"type": "refusal", "name": "recovery_plan"}, {"type": "choice", "name": "recovery_plan", "choice": "made-up"}]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"answers": [answer]}))) as client:
            with pytest.raises(ProviderError, match="no valid candidate"):
                await AIProvider(settings(), client=client).choose(world, [{"id": "valid", "validation": {"valid": True}}], "choose")


@pytest.mark.asyncio
async def test_credentials_errors_timeouts_never_retry_or_leak(world):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"error": "secret upstream test-key"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as missing:
            await AIProvider(settings(openai_api_key=""), client=client).interpret("test", world)
        assert missing.value.code == "not_configured" and not calls
        with pytest.raises(ProviderError) as rejected:
            await AIProvider(settings(), client=client).interpret("test", world)
        assert "test-key" not in str(rejected.value) and len(calls) == 1
    def timeout(request):
        raise httpx.ReadTimeout("private details", request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(ProviderError, match="timed out"):
            await AIProvider(settings(), client=client).interpret("test", world)


FR24_ROW = {"fr24_id": "abcd", "callsign": "WZZ123", "flight": "W4123", "lat": 44.32, "lon": 23.88, "alt": 7000, "gspeed": 200, "track": 100, "timestamp": "2026-10-31T09:00:00Z", "source": "ADSB", "orig_iata": "LTN", "dest_iata": "CRA"}


class FakeVoiceSocket:
    def __init__(self):
        self.received = asyncio.Queue()
        self.sent = []
        self.closed = False

    async def recv(self):
        return await self.received.get()

    async def send(self, value):
        self.sent.append(json.loads(value))
        if self.sent[-1]["type"] == "session.close":
            await self.received.put(json.dumps({"type": "session.closed", "usage": {"duration_seconds": 1}}))

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_fr24_real_endpoint_budget_and_original_timestamp():
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.path == "/api/live/flight-positions/full"
        assert request.headers["Accept-Version"] == "v1"
        assert request.url.params["limit"] == "5"
        assert "bounds" in request.url.params
        return httpx.Response(200, json={"data": [FR24_ROW]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = FlightProvider(settings(fr24_max_requests=1), client=client)
        observation = (await provider.fetch())[0]
        assert observation["observed_at"] == FR24_ROW["timestamp"]
        assert observation["position_source"] == "ADSB" and observation["source"] == "Flightradar24"
        assert observation["mode"] == "LIVE" and "capacity" not in observation
        assert provider.estimated_credits_used == 8
        with pytest.raises(ProviderError) as fast:
            await provider.fetch()
        assert fast.value.code == "poll_interval"
        provider.last_request_at = None
        with pytest.raises(ProviderError) as exhausted:
            await provider.fetch()
        assert exhausted.value.code == "budget_exhausted" and len(calls) == 1


@pytest.mark.asyncio
async def test_fr24_sandbox_never_labeled_live_and_missing_time_rejected():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [FR24_ROW]}))) as client:
        assert (await FlightProvider(settings(fr24_environment="sandbox"), client=client).fetch())[0]["mode"] == "SANDBOX"
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [{"fr24_id": "a", "lat": 44, "lon": 24}]}))) as client:
        with pytest.raises(ProviderError, match="source timestamp"):
            await FlightProvider(settings(), client=client).fetch()


@pytest.mark.asyncio
async def test_fr24_outside_bounds_static_sample_cannot_masquerade_as_local_live():
    sample = FR24_ROW | {"lat": 35.34722, "lon": -7.90277}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [sample]}))) as client:
        with pytest.raises(ProviderError):
            await FlightProvider(settings(), client=client).fetch()
        data = await FlightProvider(settings(fr24_environment="sandbox"), client=client).fetch()
        assert data[0]["mode"] == "SANDBOX"


@pytest.mark.asyncio
async def test_voice_real_json_webrtc_handshake():
    socket = FakeVoiceSocket()
    async def connect(url, **kwargs):
        assert url == "wss://api.openai.com/v1/live/sessions/live_keep-prefix/attach"
        assert kwargs["additional_headers"]["Authorization"] == "Bearer test-key"
        return socket
    def handler(request):
        body = json.loads(request.content)
        assert request.url.path == "/v1/live/sessions"
        assert body["session"]["model"] == "gpt-live-1"
        assert body["session"]["delegation"]["type"] == "client"
        assert body["transport"] == {"type": "webrtc", "sdp": "v=0\r\no=offer"}
        assert "audio.format" not in body["session"]
        return httpx.Response(201, json={"session": {"id": "live_keep-prefix"}, "transport": {"type": "webrtc", "sdp": "v=0\r\no=answer"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = VoiceProvider(settings(), client=client, connect=connect)
        result = await provider.create("v=0\r\no=offer")
        await provider.aclose()
    assert result == {"session_id": "live_keep-prefix", "sdp": "v=0\r\no=answer", "max_seconds": 180}
    assert "test-key" not in json.dumps(result)
    assert socket.sent == [{"type": "session.close"}]
    assert socket.closed and provider.session_status["live_keep-prefix"]["state"] == "closed"


@pytest.mark.asyncio
async def test_voice_server_watchdog_closes_when_browser_never_does():
    socket = FakeVoiceSocket()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("HTTP not expected"))) as client:
        provider = VoiceProvider(settings(), client=client)
        await provider._watch("live_timeout", socket, 0.01)
    assert socket.sent == [{"type": "session.close"}]
    assert provider.session_status["live_timeout"] == {"state": "closed", "usage": {"duration_seconds": 1}}
    assert socket.closed


@pytest.mark.asyncio
async def test_voice_watchdog_failure_is_explicit_and_never_retries():
    attempts = []
    async def connect(*args, **kwargs):
        attempts.append(1)
        raise OSError("private network details")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(201, json={"session": {"id": "live_x"}, "transport": {"sdp": "v=0\r\no=answer"}}))) as client:
        with pytest.raises(ProviderError, match="watchdog") as failure:
            await VoiceProvider(settings(), client=client, connect=connect).create("v=0\r\no=offer")
    assert failure.value.code == "watchdog_failed" and len(attempts) == 1
    assert "private" not in str(failure.value)


@pytest.mark.asyncio
async def test_voice_invalid_offer_never_connects():
    def forbidden(_):
        pytest.fail("invalid offer must not spend a session")
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(ProviderError, match="SDP"):
            await VoiceProvider(settings(), client=client).create("not-sdp")
