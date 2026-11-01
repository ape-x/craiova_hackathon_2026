"""Actual consumer quote fixture; all HTTP is mocked, no bookings or live calls."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import ValidationError

from app.providers.base import ProviderError
from app.providers.bucharest_cab import (
    AirportCabQuoteRequest, BucharestCabProvider, HTTP_REQUEST_COUNT, LOCAL_ZONE,
    MAX_BODY, MAX_OPTIONS, QUOTE_URL, SOURCE, SOURCE_AIRPORTS, SOURCE_URL, normalize_quote,
)

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures/bucharest_cab_otp_cra.json"
TOKEN = "a" * 32


def request(**changes):
    return AirportCabQuoteRequest(**({"origin_airport": "OTP", "destination_airport": "CRA",
        "pickup_at": "2026-11-02T12:00:00+02:00", "passengers": 2, "suitcases": 2, "handbags": 2} | changes))


def payload():
    return json.loads(FIXTURE.read_text())


def bootstrap(**changes):
    data = {"csrfToken": TOKEN, "ajax_get_suitable_vehicles_url": "https://www.bucharest.cab/index.php?option=com_tulipbooking&format=json&task=ajax.getSuitableVehicles&format=json"} | changes
    return '<html><script>var tulipBookingScriptData=' + json.dumps(data) + ';</script></html>'


def parsed(data=None, req=None, now=NOW):
    return normalize_quote(payload() if data is None else data, request() if req is None else req, received_at=now)


async def fetch(data=None, req=None, *, boot=None, now=NOW, handler=None, before_dispatch=None):
    calls = []
    def respond(outbound):
        calls.append(outbound)
        if handler:
            return handler(outbound)
        if outbound.url.path == "/en/book":
            return httpx.Response(200, text=bootstrap() if boot is None else boot, headers={"content-type": "text/html", "set-cookie": "anonymous=only-this-session; Path=/; Secure"})
        return httpx.Response(200, json=payload() if data is None else data, headers={"content-type": "application/json"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = BucharestCabProvider(SimpleNamespace(request_timeout_seconds=1), client=client, now=lambda: now)
        result = await provider.quote(request() if req is None else req, before_dispatch=before_dispatch)
        await provider.aclose()
        assert not client.is_closed and not provider.configured
    return result, calls


@pytest.mark.asyncio
async def test_real_quote_preserves_dated_airport_party_price_and_unknowns():
    dispatches = []
    async def before():
        dispatches.append("dispatch")
    result, calls = await fetch(before_dispatch=before)
    assert len(calls) == len(dispatches) == result["http_request_count"] == HTTP_REQUEST_COUNT == 2
    assert all(call.method == "GET" and call.url.host == "www.bucharest.cab" for call in calls)
    assert str(calls[0].url) == SOURCE_URL and calls[1].url.path == "/en/"
    query = dict(calls[1].url.params)
    assert query["task"] == "ajax.getSuitableVehicles" and query["passengers"] == "2"
    assert query["pickupDateTime"] == "2026-11-02 12:00:00" and query["tripType"] == "from-airport"
    assert query["placeIdStart"] == SOURCE_AIRPORTS["OTP"]["place_id"] and query["placeIdEnd"] == SOURCE_AIRPORTS["CRA"]["place_id"]
    assert query["csrfToken"] == TOKEN and "anonymous=only-this-session" in calls[1].headers["cookie"]
    assert "suitcases" not in query and "handbags" not in query and "authorization" not in calls[1].headers
    assert TOKEN not in json.dumps(result) and "cookie" not in json.dumps(result).lower()
    assert result["provider"] == SOURCE and result["booking_supported"] is False
    assert result["source_quoted_at"] is result["expires_at"] is None
    assert result["request"]["pickup_at"] == "2026-11-02T10:00:00+00:00"
    assert result["request"]["baggage_supplier_scoped"] is False
    assert result["resolved_route"] == {"origin": SOURCE_AIRPORTS["OTP"], "destination": SOURCE_AIRPORTS["CRA"]}
    assert result["route"] == {"distance_meters": 234466, "duration_seconds": 8787, "duration_text": "2 hours 26 mins", "duration_basis": "source_estimate_without_departure_time"}
    assert [car["price"]["amount"] for car in result["options"]] == ["188.00", "212.00", "235.00", "258.00"]
    assert [car["max_passengers"] for car in result["options"]] == [3, 4, 7, 2]
    assert result["policy"]["required_advance_hours"] == 9
    for car in result["options"]:
        assert car["remaining_fleet"] is car["provider_offer_id"] is car["supplier_valid_until"] is None
        assert car["availability_status"] == "quoted_not_reserved" and car["accessibility_confirmed"] is False
        assert car["price"]["currency"] == "EUR" and car["price_scope"] == "per_vehicle"
        assert car["fits_request"] is True and car["price_components"]["card_payment_fee"] == "3.00"
        assert car["price_components"]["payment_fee"] == car["price_components"]["extras_total"] == "0.00"


def test_supplier_formula_minimum_custom_and_flat_night_surcharge():
    data = payload()
    data["vehicle_types"][0].update(minimal_order_price="200.50", night_surcharge=True, night_surcharge_fee="3.25")
    data["vehicle_types"][1].update(custom_price="150.25", night_surcharge=True, night_surcharge_fee="4.50")
    result = parsed(data)
    assert result["options"][0]["price"]["amount"] == "203.75"
    assert result["options"][1]["price"]["amount"] == "154.75"
    assert result["options"][0]["price_components"]["distance_fare_rounded_up"] == "188.00"
    assert result["options"][1]["price_components"]["custom_price"] == "150.25"


def test_capacity_includes_cabin_bags_and_never_asserts_remaining_inventory():
    result = parsed(req=request(passengers=4, suitcases=3, handbags=4))
    assert [option["fits_request"] for option in result["options"]] == [False, False, True, False]
    assert all(option["remaining_fleet"] is None for option in result["options"])


@pytest.mark.parametrize("field,value", [
    ("origin_airport", "CRA"), ("destination_airport", "SBZ"), ("origin_airport", "OTOP"),
    ("passengers", True), ("passengers", "2"), ("passengers", 0), ("passengers", 8),
    ("suitcases", False), ("suitcases", -1), ("handbags", 8), ("handbags", "2"),
    ("pickup_at", 1793606400), ("pickup_at", True), ("pickup_at", "2026-11-02T12:00:00"),
    ("pickup_at", "2026-11-02T12:00:01+02:00"), ("pickup_at", "2026-11-02T12:00:00.001+02:00"),
    ("pickup_at", "2026-10-25T03:30:00+03:00"), ("pickup_at", "2026-10-25T03:30:00+02:00"),
    ("pickup_at", datetime(2026, 3, 29, 3, 30, tzinfo=ZoneInfo("Europe/Bucharest"))),
    ("invented_extra", 1),
])
def test_strict_request_rejects_unknown_scope_and_ambiguous_clocks(field, value):
    with pytest.raises(ValidationError):
        request(**{field: value})


@pytest.mark.asyncio
@pytest.mark.parametrize("stamp", ["2026-10-31T12:00:00+02:00", "2026-11-01T00:00:00Z", "2026-12-02T00:01:00Z"])
async def test_past_or_beyond_31_days_rejected_before_http(stamp):
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: calls.append(r))) as client:
        provider = BucharestCabProvider(SimpleNamespace(), client=client, now=lambda: NOW)
        with pytest.raises(ProviderError) as error:
            await provider.quote(request(pickup_at=stamp))
    assert error.value.code == "invalid_request" and calls == []


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(success=False),
    lambda d: d.update(timezone="Europe/London"),
    lambda d: d.update(datetime_param="2026-11-03 12:00:00"),
    lambda d: d.update(datetime="2026-11-02 12:00:00"),
    lambda d: d.update(passengers=1),
    lambda d: d.update(passengers=True),
    lambda d: d["website"].update(currency="RON"),
    lambda d: d["website"].update(name="Another supplier"),
    lambda d: d["website"].update(card_payment_fee="NaN"),
    lambda d: d["website"].update(min_advance_day_booking=True),
    lambda d: d["website"].update(min_advance_night_booking=200),
    lambda d: d["website"].update(night_restricted_start_time="27:00:00"),
    lambda d: d["start_place_data"].update(id=SOURCE_AIRPORTS["CRA"]["place_id"]),
    lambda d: d["end_place_data"].update(formattedAddress="City centre"),
    lambda d: d["end_place_data"]["location"].update(latitude=45),
    lambda d: d["end_place_data"]["location"].update(longitude=True),
    lambda d: d["start_place_data"].update(types=["locality"]),
    lambda d: d["start_place_data"].update(addressComponents=[]),
    lambda d: d["distance"].update(status="NOT_FOUND"),
    lambda d: d["distance"].update(destination_addresses=["Brașov"]),
    lambda d: d["distance"]["rows"][0]["elements"][0]["distance"].update(value=-1),
    lambda d: d["distance"]["rows"][0]["elements"][0]["duration"].update(value=True),
    lambda d: d["distance"]["rows"].append(deepcopy(d["distance"]["rows"][0])),
    lambda d: d["vehicle_types"].append(deepcopy(d["vehicle_types"][0])),
    lambda d: d["vehicle_types"][1].update(type="sEdAn"),
    lambda d: d["vehicle_types"][0].update(status=0),
    lambda d: d["vehicle_types"][0].update(passengers=None),
    lambda d: d["vehicle_types"][0].update(suitcases=True),
    lambda d: d["vehicle_types"][0].update(handbags=-1),
    lambda d: d["vehicle_types"][0].update(price_per_km=0.8),
    lambda d: d["vehicle_types"][0].update(price_per_km="1e1"),
    lambda d: d["vehicle_types"][0].update(minimal_order_price="NaN"),
    lambda d: d["vehicle_types"][0].update(custom_price="0.00"),
    lambda d: d["vehicle_types"][0].update(night_surcharge=1),
    lambda d: d["vehicle_types"][0].update(night_surcharge_fee="-3.00"),
    lambda d: d["vehicle_types"][0].update(currency="USD"),
    lambda d: d["vehicle_types"][0].update(mandatory_airport_fee="25.00"),
    lambda d: d["vehicle_types"][0].update(break_hours=[{"start": "10:00", "end": "14:00"}]),
    lambda d: d.update(vehicle_types=d["vehicle_types"] * (MAX_OPTIONS + 1)),
])
@pytest.mark.asyncio
async def test_changed_scope_and_malformed_source_fails_closed(mutate):
    data = payload(); mutate(data)
    with pytest.raises(ProviderError) as error:
        await fetch(data)
    assert error.value.code == "invalid_response"
    assert TOKEN not in str(error.value)


@pytest.mark.asyncio
async def test_empty_source_categories_are_not_synthetic_supply():
    data = payload(); data["vehicle_types"] = []
    result, _ = await fetch(data)
    assert result["options"] == []


@pytest.mark.parametrize("hours,allowed", [(8, False), (9, True)])
def test_source_advance_rules_apply_to_actual_time_without_shifting_it(hours, allowed):
    data = payload(); req = request(pickup_at=NOW + timedelta(hours=hours))
    data["datetime_param"] = req.pickup_at.astimezone(LOCAL_ZONE).strftime("%Y-%m-%d %H:%M:%S")
    data["datetime"] = req.pickup_at.strftime("%Y-%m-%d %H:%M:%S")
    if allowed:
        assert parsed(data, req)["request"]["pickup_at"] == req.pickup_at.isoformat()
    else:
        with pytest.raises(ProviderError) as error:
            parsed(data, req)
        assert error.value.code == "pickup_too_soon"


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["min_advance_day_booking", "min_advance_night_booking"])
async def test_explicit_source_advance_rejection_is_preserved(code):
    with pytest.raises(ProviderError) as error:
        await fetch({"error": code})
    assert error.value.code == "pickup_too_soon"


@pytest.mark.asyncio
async def test_source_no_vehicle_result_is_unavailable_without_substitute_supply():
    with pytest.raises(ProviderError) as error:
        await fetch({"error": "no_vehicle_types_found", "message": TOKEN})
    assert error.value.code == "no_vehicles" and TOKEN not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "https://evil.invalid/?option=com_tulipbooking&format=json&task=ajax.getSuitableVehicles",
    "https://www.bucharest.cab@evil.invalid/?option=com_tulipbooking&format=json&task=ajax.getSuitableVehicles",
    "http://www.bucharest.cab/index.php?option=com_tulipbooking&format=json&task=ajax.getSuitableVehicles",
    "https://www.bucharest.cab/index.php?option=com_tulipbooking&format=json&task=ajax.bookOrder",
    "https://www.bucharest.cab/index.php?option=com_tulipbooking&format=json&task=ajax.storePriceAjax",
    "https://www.bucharest.cab/index.php?option=com_tulipbooking&format=json&task=ajax.getSuitableVehicles&extra=1",
    "javascript:alert(1)",
])
async def test_unexpected_bootstrap_url_never_becomes_request(url):
    with pytest.raises(ProviderError) as error:
        await fetch(boot=bootstrap(ajax_get_suitable_vehicles_url=url))
    assert error.value.code == "invalid_response"


@pytest.mark.asyncio
async def test_bootstrap_is_inert_and_duplicate_configuration_is_rejected():
    with pytest.raises(ProviderError):
        await fetch(boot=bootstrap() + bootstrap())
    with pytest.raises(ProviderError):
        await fetch(boot='<script>var tulipBookingScriptData={"csrfToken":fetch("https://evil.invalid")};</script>')


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
async def test_http_failures_do_not_redirect_retry_or_echo_source_body(status):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(status, text=TOKEN, headers={"location": "https://evil.invalid/booking"})
    with pytest.raises(ProviderError) as error:
        await fetch(handler=handler)
    assert error.value.code == f"http_{status}" and len(calls) == 1
    assert TOKEN not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["length", "stream", "json_duplicate", "deep", "nonfinite", "content_type"])
async def test_bounded_bodies_and_invalid_json(kind):
    def handler(req):
        if req.url.path == "/en/book":
            return httpx.Response(200, text=bootstrap(), headers={"content-type": "text/html"})
        raw = json.dumps(payload()).encode(); headers = {"content-type": "application/json"}
        if kind == "length": headers["content-length"] = str(MAX_BODY + 1)
        if kind == "stream": raw = b" " * (MAX_BODY + 1)
        if kind == "json_duplicate": raw = b'{"success":true,"success":false}'
        if kind == "deep": raw = b'{"data":' + b'[' * 100 + b'0' + b']' * 100 + b'}'
        if kind == "nonfinite": raw = b'{"value":NaN}'
        if kind == "content_type": headers["content-type"] = "text/html"
        return httpx.Response(200, content=raw, headers=headers)
    with pytest.raises(ProviderError) as error:
        await fetch(handler=handler)
    assert error.value.code == "invalid_response"


@pytest.mark.asyncio
async def test_network_errors_and_timeout_are_sanitized_without_retry():
    for exception, code in [(httpx.ReadTimeout(TOKEN), "timeout"), (httpx.ConnectError(TOKEN), "connection")]:
        calls = []
        def handler(req):
            calls.append(req); raise exception
        with pytest.raises(ProviderError) as error:
            await fetch(handler=handler)
        assert error.value.code == code and TOKEN not in str(error.value) and len(calls) == 1
        assert error.value.__suppress_context__ is True


@pytest.mark.asyncio
async def test_total_deadline_and_callback_failure_prevent_dispatch():
    calls = []
    async def handler(req):
        calls.append(req); await asyncio.sleep(0.05)
        return httpx.Response(200, text=bootstrap(), headers={"content-type": "text/html"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BucharestCabProvider(SimpleNamespace(request_timeout_seconds=0.01), client=client, now=lambda: NOW)
        with pytest.raises(ProviderError) as error:
            await provider.quote(request())
        assert error.value.code == "timeout" and len(calls) == 1
        async def deny():
            raise RuntimeError("quota exhausted")
        with pytest.raises(RuntimeError, match="quota exhausted"):
            await provider.quote(request(), before_dispatch=deny)
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_shared_client_credentials_and_cookies_are_not_forwarded():
    calls = []
    def respond(req):
        calls.append(req)
        assert "authorization" not in req.headers and "x-api-key" not in req.headers
        assert "logged_in" not in req.headers.get("cookie", "")
        return httpx.Response(200, text=bootstrap(), headers={"content-type": "text/html"}) if req.url.path == "/en/book" else httpx.Response(200, json=payload())
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), headers={"x-api-key": "PRIVATE"}, auth=("user", "password"), cookies={"logged_in": "private"}) as client:
        await BucharestCabProvider(SimpleNamespace(), client=client, now=lambda: NOW).quote(request())
    assert len(calls) == 2


def test_receipt_and_exact_party_are_part_of_offer_identity():
    first = parsed(); second = parsed(now=NOW + timedelta(seconds=1)); different_party = parsed(req=request(passengers=3))
    assert first["options"][0]["id"] != second["options"][0]["id"] != different_party["options"][0]["id"]
    assert len({option["id"] for option in first["options"]}) == 4
