"""Dated Google route evidence; all transports are mocked and credentials fake."""
import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.providers.base import ProviderError
from app.providers.real_data import RealDataProvider


NOW = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
PICKUP = (NOW + timedelta(days=1, microseconds=123456)).astimezone(timezone(timedelta(hours=2)))
ORIGIN = {"latitude": 51.4715123456, "longitude": -0.4874123456}
DESTINATION = {"latitude": 51.5309123456, "longitude": -0.2861123456}


def settings(**overrides):
    return SimpleNamespace(**({"request_timeout_seconds": 1, "google_maps_server_api_key": "test-server-key"} | overrides))


def result():
    return {"routes": [{"duration": "120.000000001s", "distanceMeters": 12345,
        "legs": [{"startLocation": {"latLng": {"latitude": 51.4716, "longitude": -0.4875}},
                  "endLocation": {"latLng": {"latitude": 51.531, "longitude": -0.2862}}}],
        "warnings": ["The route includes restricted-access roads."]}]}


@pytest.mark.asyncio
async def test_exact_future_request_and_unrounded_source_evidence_are_separate_from_road_points():
    calls = []
    def respond(request):
        calls.append(request)
        assert str(request.url) == "https://routes.googleapis.com/directions/v2:computeRoutes"
        assert request.method == "POST"
        assert request.headers["x-goog-api-key"] == "test-server-key"
        mask = request.headers["x-goog-fieldmask"]
        assert "fallbackInfo" in mask and "routes.legs.startLocation" in mask and "*" not in mask
        body = json.loads(request.content)
        assert body == {"origin": {"location": {"latLng": ORIGIN}},
            "destination": {"location": {"latLng": DESTINATION}},
            "departureTime": "2026-11-01T12:00:00.123456Z", "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE", "computeAlternativeRoutes": False, "units": "METRIC"}
        return httpx.Response(200, json=result())
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = RealDataProvider(settings(), client=client, now=lambda: NOW)
        quote = await provider.dated_route(ORIGIN, DESTINATION, PICKUP)
        await provider.aclose()
        assert not client.is_closed  # Injected client remains caller-owned.
    assert len(calls) == 1
    assert quote["duration_seconds"] == "120.000000001"
    assert quote["distance_meters"] == 12345
    assert quote["request"]["origin"] == ORIGIN and quote["request"]["destination"] == DESTINATION
    assert quote["road_endpoints"]["origin"] != ORIGIN
    assert quote["road_endpoints"]["destination"] != DESTINATION
    assert quote["fetched_at"] == NOW.isoformat()
    assert quote["warnings"] == result()["routes"][0]["warnings"]
    assert quote["source"] == "Google Routes" and quote["duration_guaranteed"] is False
    assert not {"duration_minutes", "arrival_at", "capacity", "vehicle_id"}.intersection(quote)
    assert "test-server-key" not in json.dumps(quote)


@pytest.mark.asyncio
@pytest.mark.parametrize("origin,destination,pickup", [
    (ORIGIN, DESTINATION, NOW), (ORIGIN, DESTINATION, NOW-timedelta(seconds=1)),
    (ORIGIN, DESTINATION, PICKUP.replace(tzinfo=None)), (ORIGIN, DESTINATION, PICKUP.isoformat()),
    (ORIGIN, ORIGIN, PICKUP), (None, DESTINATION, PICKUP),
    ({"latitude": 91, "longitude": 0}, DESTINATION, PICKUP),
    ({"latitude": 0, "longitude": -181}, DESTINATION, PICKUP),
    ({"latitude": True, "longitude": 0}, DESTINATION, PICKUP),
    ({"latitude": float("nan"), "longitude": 0}, DESTINATION, PICKUP),
    ({"latitude": "51.5", "longitude": 0}, DESTINATION, PICKUP),
    ({"latitude": 51}, DESTINATION, PICKUP),
])
async def test_invalid_inputs_never_dispatch_a_billable_route(origin, destination, pickup):
    def unexpected(_):
        pytest.fail("Invalid inputs must not reach Google or an alternative provider")
    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        provider = RealDataProvider(settings(), client=client, now=lambda: NOW)
        with pytest.raises(ProviderError) as caught:
            await provider.dated_route(origin, destination, pickup)
    assert caught.value.code == "invalid_request"


@pytest.mark.asyncio
async def test_browser_key_cannot_enable_dated_routes_and_no_public_route_fallback():
    def unexpected(_):
        pytest.fail("Missing server key must not dispatch")
    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        provider = RealDataProvider(settings(google_maps_server_api_key="", google_maps_api_key="browser-only"), client=client, now=lambda: NOW)
        with pytest.raises(ProviderError) as caught:
            await provider.dated_route(ORIGIN, DESTINATION, PICKUP)
    assert caught.value.code == "not_configured"
    assert "browser-only" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    *(('duration', value) for value in [None, True, 120, "-2s", "0s", "1e3s", "NaNs", "1.0000000001s", "604801s", "1\u0662s"]),
    *(('distanceMeters', value) for value in [None, True, "12345", 0, -1, 1.2, 20_000_001]),
    ("legs", []), ("legs", [{}, {}]), ("legs", None), ("legs", [None]),
    ("warnings", "Warning"), ("warnings", [None]), ("warnings", ["x"]*21),
])
async def test_malformed_route_never_becomes_a_planning_duration(field, value):
    payload = result()
    payload["routes"][0][field] = value
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        provider = RealDataProvider(settings(), client=client, now=lambda: NOW)
        with pytest.raises(ProviderError) as caught:
            await provider.dated_route(ORIGIN, DESTINATION, PICKUP)
    assert caught.value.code == "invalid_response"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,code", [
    ({}, "no_route"), ({"routes": []}, "no_route"), ({"routes": None}, "invalid_response"),
    ({"routes": [None]}, "invalid_response"), ({"routes": result()["routes"]*2}, "invalid_response"),
    ({"error": {"message": "test-server-key"}}, "invalid_response"),
    (result() | {"fallbackInfo": {"routingMode": "FALLBACK_TRAFFIC_UNAWARE", "reason": "LATENCY_EXCEEDED"}}, "routing_fallback"),
    (result() | {"fallbackInfo": {}}, "routing_fallback"),
])
async def test_no_route_and_provider_fallback_are_explicit_failures(payload, code):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        provider = RealDataProvider(settings(), client=client, now=lambda: NOW)
        with pytest.raises(ProviderError) as caught:
            await provider.dated_route(ORIGIN, DESTINATION, PICKUP)
    assert caught.value.code == code
    assert "test-server-key" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("location", [None, {}, {"latLng": {"latitude": 91, "longitude": 0}}, {"latLng": {"latitude": 51, "longitude": True}}])
async def test_road_endpoint_evidence_is_validated(location):
    payload = copy.deepcopy(result())
    payload["routes"][0]["legs"][0]["startLocation"] = location
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        provider = RealDataProvider(settings(), client=client, now=lambda: NOW)
        with pytest.raises(ProviderError) as caught:
            await provider.dated_route(ORIGIN, DESTINATION, PICKUP)
    assert caught.value.code == "invalid_response"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 403, 429, 503])
async def test_provider_failure_does_not_retry_redirect_or_echo_response(status):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://example.com/do-not-follow"}, text="test-server-key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        provider = RealDataProvider(settings(), client=client, now=lambda: NOW)
        with pytest.raises(ProviderError) as caught:
            await provider.dated_route(ORIGIN, DESTINATION, PICKUP)
    assert len(calls) == 1 and caught.value.code == f"http_{status}"
    assert "test-server-key" not in str(caught.value)


@pytest.mark.asyncio
async def test_whole_transfer_timeout_closes_stream_without_retry():
    calls, closed = [], []
    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"routes":'
            await asyncio.sleep(1)
            yield b'[]}'
        async def aclose(self):
            closed.append(True)
    def respond(request):
        calls.append(request)
        return httpx.Response(200, stream=SlowBody())
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = RealDataProvider(settings(), client=client, now=lambda: NOW)
        provider.timeout = .01
        with pytest.raises(ProviderError) as caught:
            await provider.dated_route(ORIGIN, DESTINATION, PICKUP)
    assert caught.value.code == "timeout" and len(calls) == 1 and closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"<html>test-server-key</html>", b'{"routes":[NaN]}'])
async def test_malformed_json_is_sanitized_without_an_alternate_request(body):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, content=body)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = RealDataProvider(settings(), client=client, now=lambda: NOW)
        with pytest.raises(ProviderError) as caught:
            await provider.dated_route(ORIGIN, DESTINATION, PICKUP)
    assert caught.value.code == "invalid_response" and len(calls) == 1
    assert "test-server-key" not in str(caught.value)


@pytest.mark.asyncio
async def test_dated_route_uses_bounded_body_transport(monkeypatch):
    monkeypatch.setattr("app.providers.real_data.MAX_BODY", 64)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=result()))) as client:
        provider = RealDataProvider(settings(), client=client, now=lambda: NOW)
        with pytest.raises(ProviderError) as caught:
            await provider.dated_route(ORIGIN, DESTINATION, PICKUP)
    assert caught.value.code == "invalid_response"


@pytest.mark.asyncio
async def test_transport_failure_is_visible_and_not_retried():
    calls = []
    def respond(request):
        calls.append(request)
        raise httpx.ConnectError("test-server-key", request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = RealDataProvider(settings(), client=client, now=lambda: NOW)
        with pytest.raises(ProviderError) as caught:
            await provider.dated_route(ORIGIN, DESTINATION, PICKUP)
    assert caught.value.code == "connection" and len(calls) == 1
    assert "test-server-key" not in str(caught.value)


@pytest.mark.asyncio
async def test_pickup_that_elapsed_during_request_is_not_replaced_with_now():
    readings = iter([NOW, PICKUP+timedelta(seconds=1)])
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=result())
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = RealDataProvider(settings(), client=client, now=lambda: next(readings))
        with pytest.raises(ProviderError) as caught:
            await provider.dated_route(ORIGIN, DESTINATION, PICKUP)
    assert caught.value.code == "pickup_elapsed" and len(calls) == 1
