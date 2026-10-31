"""APL contract checks use mock HTTP only; no quotes, bookings or credentials."""
import asyncio
import copy
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.providers.apl import APLProvider, APLQuoteRequest, QUOTE_URL
from app.providers.base import ProviderError
from app.providers.real_data import MAX_BODY


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def request(**overrides):
    return APLQuoteRequest(**({
        "origin": "Heathrow Terminal 5", "destination": "W1K 1LN", "passengers": 2,
        "suitcases": 2, "pickup_at": "2030-11-02T14:00:00Z",
    } | overrides))


def settings(**overrides):
    return SimpleNamespace(**({"apl_api_key": SecretStr("test-only-apl-key"), "request_timeout_seconds": 1} | overrides))


def quote():
    # Shape observed in the registered REST response. Prices are final, not a
    # per-person amount. No duration or supplier expiry exists in this shape.
    return {
        "pickup": "LHR Airport", "dropoff": "W1K 1LN", "transfer_date": "2030-11-02",
        "transfer_time": "14:00", "passengers": 2, "recommended_car_type": "Saloon",
        "cars": [
            {"car_type": "Saloon", "price_gbp": 76, "max_passengers": 3, "max_bags": 3},
            {"car_type": "People Carrier", "price_gbp": 88, "max_passengers": 5, "max_bags": 5},
        ],
        "meeting_point": {"name": "Heathrow T5", "instructions": "Generic meeting instructions, not an assigned driver."},
    }


async def run_quote(payload, query=None):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        return await APLProvider(settings(), client=client, now=lambda: NOW).quote(query or request())


@pytest.mark.asyncio
async def test_registered_quote_sends_exact_explicit_request_and_preserves_unknowns():
    calls = []
    def respond(outbound):
        calls.append(outbound)
        assert outbound.method == "POST" and str(outbound.url) == QUOTE_URL
        assert outbound.headers["x-api-key"] == "test-only-apl-key"
        assert json.loads(outbound.content) == {
            "origin": "Heathrow Terminal 5", "destination": "W1K 1LN", "passengers": 2,
            "suitcases": 2, "transfer_date": "2030-11-02", "transfer_time": "14:00",
        }
        return httpx.Response(200, json=quote())
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = APLProvider(settings(), client=client, now=lambda: NOW)
        result = await provider.quote(request(expected_hub="LHR", expected_direction="from_hub"))
        await provider.aclose()
        assert not client.is_closed  # Caller retains ownership of injected client.
    assert len(calls) == 1 and result["received_at"] == NOW.isoformat()
    assert result["source_quoted_at"] is None and result["expires_at"] is None
    assert result["request"]["timezone"] == "Europe/London"
    assert result["resolved_route"]["hub"] is None  # An expected hub is not a supplier observation.
    assert result["resolved_route"]["direction"] is None
    assert result["options"][0]["price"] == {"amount": "76", "currency": "GBP"}
    assert result["options"][0]["price_scope"] == "per_vehicle"
    assert result["options"][0]["availability_status"] == "quoted_not_reserved"
    assert result["options"][0]["provider_offer_id"] is None
    assert result["options"][0]["duration_seconds"] is result["options"][0]["arrival_at"] is None
    assert result["options"][0]["accessibility_confirmed"] is False
    assert "test-only-apl-key" not in json.dumps(result)
    assert "meeting_point" not in result  # No implication that a driver has been assigned.


@pytest.mark.parametrize("override", [
    {"passengers": True}, {"passengers": "2"}, {"passengers": 0}, {"suitcases": -1},
    {"pickup_at": "2030-11-02T14:00:00"}, {"pickup_at": 1_900_000_000},
    {"pickup_at": "2030-11-02T14:00:01Z"},
    {"pickup_at": "2030-10-27T00:30:00Z"}, {"pickup_at": "2030-10-27T01:30:00Z"},
    {"origin": "Heathrow\nT5"}, {"origin": " a "}, {"destination": "Heathrow Terminal 5"},
    {"passenger_email": "not-collected@example.org"}, {"expected_direction": "cheapest"},
])
def test_invalid_or_ambiguous_input_rejected_before_http(override):
    with pytest.raises(ValidationError):
        request(**override)


def test_summer_pickup_uses_london_clock_and_preserves_explicit_zero_bags():
    query = request(pickup_at="2030-07-02T14:00:00Z", suitcases=0)
    assert query.supplier_payload()["transfer_time"] == "15:00"
    assert query.supplier_payload()["suitcases"] == 0


@pytest.mark.parametrize("field", ["passengers", "suitcases"])
def test_party_and_luggage_have_no_silent_defaults(field):
    values = request().model_dump()
    values.pop(field)
    with pytest.raises(ValidationError):
        APLQuoteRequest.model_validate(values)


@pytest.mark.asyncio
async def test_missing_key_or_expired_pickup_never_dispatches_or_uses_anonymous_fallback():
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: calls.append(req))) as client:
        for config, query, code in [
            (settings(apl_api_key=SecretStr("")), request(), "not_configured"),
            (settings(), request(pickup_at="2025-11-02T14:00:00Z"), "invalid_request"),
        ]:
            with pytest.raises(ProviderError) as failure:
                await APLProvider(config, client=client, now=lambda: NOW).quote(query)
            assert failure.value.code == code
    assert calls == []


@pytest.mark.asyncio
async def test_capacity_is_per_vehicle_and_luggage_is_required_to_fit():
    payload = quote()
    payload["passengers"] = 4
    result = await run_quote(payload, request(passengers=4, suitcases=6))
    assert not any(option["fits_request"] for option in result["options"])
    assert result["message"] and len(result["options"]) == 2
    result = await run_quote(payload, request(passengers=4, suitcases=5))
    assert [option["fits_request"] for option in result["options"]] == [False, True]
    assert result["options"][1]["price"]["amount"] == "88"  # Never multiply by passengers.


@pytest.mark.asyncio
async def test_directional_final_quote_does_not_choose_reverse_or_apply_adjustments_again():
    payload = quote()
    payload.pop("cars")
    payload.update({"hub": "LHR", "direction": "from_hub", "resolved_address": "W1K1LN", "pickup_terminal": "T5",
        "from_hub": [{"car_type": "Saloon", "base_price_gbp": 79, "final_price_gbp": 76, "max_passengers": 3, "max_bags": 3}],
        "to_hub": [{"car_type": "Saloon", "base_price_gbp": 76, "final_price_gbp": 73, "max_passengers": 3, "max_bags": 3}],
        "surcharges": [{"amount_gbp": -3}], "price_basis": "Final fixed price per vehicle for this pickup time."})
    result = await run_quote(payload, request(expected_hub="LHR", expected_direction="from_hub"))
    assert result["options"][0]["price"] == {"amount": "76", "currency": "GBP"}
    assert result["resolved_route"]["direction"] == "from_hub"
    for changed in [{"direction": "to_hub"}, {"direction": None}, {"hub": "LGW"}, {"from_hub": None}]:
        with pytest.raises(ProviderError) as failure:
            await run_quote(payload | changed, request(expected_hub="LHR", expected_direction="from_hub"))
        assert failure.value.code == "invalid_response"


@pytest.mark.parametrize("field,value", [
    ("price_gbp", True), ("price_gbp", -1), ("price_gbp", "NaN"), ("price_gbp", "1e99999"),
    ("price_gbp", "0e999999"), ("price_gbp", None), ("max_passengers", True), ("max_passengers", None),
    ("max_bags", "3"), ("currency", "EUR"), ("final_price_gbp", 55),
])
@pytest.mark.asyncio
async def test_malformed_or_conflicting_price_and_capacity_never_becomes_inventory(field, value):
    payload = quote()
    payload["cars"][0][field] = value
    with pytest.raises(ProviderError) as failure:
        await run_quote(payload)
    assert failure.value.code == "invalid_response"


@pytest.mark.parametrize("changed", [
    {"transfer_date": "2030-11-03"}, {"transfer_time": "15:00"}, {"passengers": True},
    {"passengers": 3}, {"suitcases": 3}, {"currency": "USD"}, {"pickup": None},
    {"cars": [None]}, {"cars": {}}, {"cars": [quote()["cars"][0]] * 2},
])
@pytest.mark.asyncio
async def test_mismatched_or_malformed_quote_is_explicit_failure(changed):
    with pytest.raises(ProviderError) as failure:
        await run_quote(quote() | copy.deepcopy(changed))
    assert failure.value.code == "invalid_response"


@pytest.mark.asyncio
async def test_decimal_price_is_not_rounded_or_converted_to_eur():
    payload = quote()
    payload["cars"][0]["price_gbp"] = "76.125"
    payload["cars"][0]["final_price_gbp"] = "76.1250"
    result = await run_quote(payload)
    assert result["options"][0]["price"] == {"amount": "76.1250", "currency": "GBP"}


@pytest.mark.parametrize("status", [302, 401, 429, 503])
@pytest.mark.asyncio
async def test_http_failures_are_sanitized_and_never_retry_or_follow_redirects(status):
    calls = []
    def respond(outbound):
        calls.append(outbound)
        return httpx.Response(status, json={"error": "test-only-apl-key"}, headers={"location": "https://untrusted.example/quote"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        with pytest.raises(ProviderError) as failure:
            await APLProvider(settings(), client=client, now=lambda: NOW).quote(request())
    assert failure.value.code == f"http_{status}" and "test-only-apl-key" not in str(failure.value)
    assert len(calls) == 1 and str(calls[0].url) == QUOTE_URL


@pytest.mark.asyncio
async def test_total_timeout_and_response_limit_are_enforced():
    async def slow(_):
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=quote())
    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
        provider = APLProvider(settings(), client=client, now=lambda: NOW)
        provider._http.timeout = 0.01
        with pytest.raises(ProviderError) as failure:
            await provider.quote(request())
        assert failure.value.code == "timeout"
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b" " * (MAX_BODY + 1)))) as client:
        with pytest.raises(ProviderError) as failure:
            await APLProvider(settings(), client=client, now=lambda: NOW).quote(request())
        assert failure.value.code == "invalid_response"


def test_real_apl_credentials_are_disabled_during_tests_and_settings_mask_key():
    assert os.environ["APL_API_KEY"] == ""
    config = Settings(_env_file=None, apl_api_key="test-only-apl-key")
    assert "test-only-apl-key" not in repr(config)
    assert "test-only-apl-key" not in config.model_dump_json()
