"""Official travel contracts using HTTP fixtures only; never search live inventory."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.providers.travel import TravelProvider
from app.travel import TravelSearchRequest


def query(kind="flight", **overrides):
    values = {"kind": kind, "origin_code": "CRA", "destination_code": "OTP", "departure_date": (datetime.now(timezone.utc)+timedelta(days=2)).date(), "adults": 2}
    return TravelSearchRequest(**(values | overrides))


def settings(**overrides):
    return SimpleNamespace(request_timeout_seconds=20, **overrides)


def flight_data():
    day = query().departure_date.isoformat()
    passengers = [{"id": "pax-1", "type": "adult"}, {"id": "pax-2", "type": "adult"}]
    offer = {
        "id": "off-provider", "live_mode": True, "passengers": passengers,
        "total_amount": "123.4501", "total_currency": "EUR",
        "expires_at": (datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat(),
        "owner": {"name": "Fixture airline", "iata_code": "RO"},
        "conditions": {"refund_before_departure": {"allowed": True, "penalty_amount": "25.00", "penalty_currency": "EUR"}},
        "slices": [{"segments": [{
            "id": "seg-provider", "origin": {"iata_code": "CRA", "time_zone": "Europe/Bucharest"},
            "destination": {"iata_code": "OTP", "time_zone": "Europe/Bucharest"},
            "departing_at": day+"T12:00:00", "arriving_at": day+"T13:00:00",
            "marketing_carrier_flight_number": "123",
        }]}],
    }
    return {"data": {"id": "orq-provider", "live_mode": True, "passengers": passengers, "offers": [offer]}}


def ref(kind, identity):
    return {"data": {"type": kind, "id": identity}}


def refs(kind, *identities):
    return {"data": [{"type": kind, "id": identity} for identity in identities]}


def ground_data():
    day = query().departure_date.isoformat()
    segment = {"type": "segments", "id": "segment-1", "attributes": {"index": 0, "departure_time": day+"T12:00", "arrival_time": day+"T16:00", "vehicle_number": "456"}, "relationships": {"vehicle": ref("vehicles", "vehicle-1"), "operating_carrier": ref("operating_carriers", "carrier-1"), "departure_station": ref("stations", "ROCRAAIR"), "arrival_station": ref("stations", "ROOTPAIR")}}
    fare = {"type": "fares", "id": "fare-1", "attributes": {"offer_id": "offer-provider", "offer_expires_at": (datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat(), "price": 12345, "booked_out": False, "seats_left": 4, "booking_type": "standalone", "passenger_coverage_type": "collective", "passenger_ids": ["pax-1", "pax-2"], "segment_coverage_type": "collective", "segment_ids": ["segment-1"]}, "relationships": {"fare_class": ref("fare_classes", "class-1"), "conditions": refs("fare_conditions", "condition-1")}}
    included = [segment, fare,
        {"type": "vehicles", "id": "vehicle-1", "relationships": {"vehicle_type": ref("vehicle_types", "BUS")}},
        {"type": "vehicle_types", "id": "BUS", "attributes": {"code": "BUS"}},
        {"type": "operating_carriers", "id": "carrier-1", "attributes": {"trade_name": "Fixture coach operator"}},
        {"type": "stations", "id": "ROCRAAIR", "attributes": {"name": "Fixture Craiova stop", "time_zone": "Europe/Bucharest", "latitude": 44.3, "longitude": 23.8}},
        {"type": "stations", "id": "ROOTPAIR", "attributes": {"name": "Fixture Otopeni stop", "time_zone": "Europe/Bucharest", "latitude": 44.5, "longitude": 26.1}},
        {"type": "passengers", "id": "pax-1", "attributes": {"age": 40}},
        {"type": "passengers", "id": "pax-2", "attributes": {"age": 65}},
        {"type": "fare_classes", "id": "class-1", "attributes": {"name": "Provider Standard"}},
        {"type": "fare_conditions", "id": "condition-1", "attributes": {"description": "Refund with a provider fee.", "refundable": "yes", "exchangeable": "no"}},
    ]
    return {"data": [{"type": "connections", "id": "connection-1", "attributes": {"booked_out": False}, "relationships": {"segments": refs("segments", "segment-1"), "fares": refs("fares", "fare-1")}}], "included": included, "meta": {"currency": "EUR", "locale": "en"}, "jsonapi": {"version": "1.0"}}


@pytest.mark.parametrize("values", [
    {"adults": True}, {"adults": 31}, {"origin_code": "ZZZ"}, {"destination_code": "CRA"},
    {"passenger_ages": [40]}, {"passenger_ages": [True, 40]}, {"passenger_ages": [0, 40]},
    {"departure_date": "2000-01-01"}, {"provider_url": "https://example.org"},
])
def test_travel_request_rejects_invalid_inputs(values):
    with pytest.raises(ValidationError):
        query(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,config,state", [
    ("flight", {}, "not_configured"), ("bus", {}, "not_configured"),
    ("flight", {"duffel_access_token": "duffel_test_private", "duffel_environment": "live"}, "sandbox"),
    ("flight", {"duffel_access_token": "duffel_live_private"}, "sandbox"),
    ("bus", {"distribusion_api_key": "private"}, "sandbox"),
    ("bus", {"distribusion_api_key": "private", "distribusion_environment": "live"}, "not_configured"),
])
async def test_missing_or_sandbox_credentials_make_no_requests(kind, config, state):
    def forbidden(_):
        pytest.fail("No HTTP call is allowed")
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        result = await TravelProvider(settings(**config), client=client).search(query(kind))
    assert result["state"] == state and result["items"] == []


@pytest.mark.asyncio
async def test_duffel_search_preserves_group_price_expiry_and_never_orders():
    calls = []
    data = flight_data()
    def respond(request):
        calls.append(request)
        assert request.method == "POST" and str(request.url).startswith("https://api.duffel.com/air/offer_requests?")
        assert request.headers["Authorization"] == "Bearer duffel_live_private"
        assert request.headers["Duffel-Version"] == "v2"
        assert request.url.params["return_offers"] == "true"
        body = json.loads(request.content)["data"]
        assert body["passengers"] == [{"type": "adult"}, {"type": "adult"}]
        assert body["slices"][0]["origin"] == "CRA"
        return httpx.Response(201, json=data)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = TravelProvider(settings(duffel_access_token="duffel_live_private", duffel_environment="live"), client=client)
        result = await provider.search(query())
    assert len(calls) == 1
    assert result["state"] == "ready"
    item = result["items"][0]
    assert item["provider_offer_id"] == "off-provider" and item["provider_search_id"] == "orq-provider"
    assert item["total_price"] == "123.4501" and item["passengers"] == 2
    assert item["conditions"]["refund_before_departure"]["penalty"]["amount"] == "25.00"
    assert item["expires_at"] == data["data"]["offers"][0]["expires_at"]
    assert item["mode"] == "live" and item["requires_revalidation"] is True
    assert item["accessibility_confirmed"] is False and item["reported_seats_left"] is None
    assert item["availability_status"] == "quoted_not_reserved"
    assert datetime.fromisoformat(item["departure_at"]).astimezone(ZoneInfo("Europe/Bucharest")).hour == 12
    assert datetime.fromisoformat(item["arrival_at"]) > datetime.fromisoformat(item["departure_at"])
    assert "duffel_live_private" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation,state", [
    (lambda d: d["data"].update(live_mode=False), "sandbox"),
    (lambda d: d["data"].pop("live_mode"), "error"),
    (lambda d: d["data"]["offers"][0].update(live_mode="true"), "error"),
    (lambda d: d["data"]["offers"][0]["owner"].update(iata_code="ZZ"), "sandbox"),
    (lambda d: d["data"]["offers"][0].update(total_amount="NaN"), "error"),
    (lambda d: d["data"]["offers"][0].pop("expires_at"), "error"),
    (lambda d: d["data"]["offers"][0]["passengers"].pop(), "error"),
    (lambda d: d["data"]["offers"][0]["slices"][0]["segments"][0]["destination"].update(iata_code="SBZ"), "error"),
])
async def test_duffel_rejects_fake_or_mismatched_offers(mutation, state):
    data = flight_data()
    mutation(data)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))) as client:
        result = await TravelProvider(settings(duffel_access_token="duffel_live_private", duffel_environment="live"), client=client).search(query())
    assert result["state"] == state and result["items"] == []


@pytest.mark.asyncio
async def test_duffel_sends_actual_ages_and_excludes_expired_offers():
    data = flight_data()
    for passenger, age in zip(data["data"]["passengers"], [10, 40]):
        passenger["age"] = age
    data["data"]["offers"][0]["expires_at"] = "2000-01-01T00:00:00Z"
    def respond(request):
        assert json.loads(request.content)["data"]["passengers"] == [{"age": 10}, {"age": 40}]
        return httpx.Response(200, json=data)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await TravelProvider(settings(duffel_access_token="duffel_live_private", duffel_environment="live"), client=client).search(query(passenger_ages=[10, 40]))
    assert result["state"] == "ready" and result["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ages", [None, [40, 65]])
async def test_distribusion_exact_new_offers_search_does_not_multiply_group_fare(ages):
    calls = []
    def respond(request):
        calls.append(request)
        assert request.method == "POST" and str(request.url) == "https://api.distribusion.com/retailers/v4/connections/find"
        assert request.headers["Api-Key"] == "private-ground-key"
        body = json.loads(request.content)
        assert body["currency"] == "EUR" and body["number_of_connections"] == 10
        assert body["search_bounds"][0]["departure_location"] == {"type": "iata", "value": "CRA"}
        if ages is None:
            assert body["pax"] == 2 and "passengers" not in body
        else:
            assert body["passengers"] == [{"pax": 1, "max_age": 40}, {"pax": 1, "max_age": 65}]
        return httpx.Response(200, json=ground_data())
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await TravelProvider(settings(distribusion_api_key="private-ground-key", distribusion_environment="live", distribusion_offers_enabled=True), client=client).search(query("bus", passenger_ages=ages))
    assert len(calls) == 1 and result["state"] == "ready"
    item = result["items"][0]
    assert item["total_price"] == "123.45" and item["currency"] == "EUR" and item["passengers"] == 2
    assert item["reported_seats_left"] == 4
    assert item["fare_name"] == "Provider Standard"
    assert item["provider_offer_id"] == "offer-provider"
    assert item["segments"][0]["departure_station"]["name"] == "Fixture Craiova stop"
    assert item["requires_revalidation"] is True and item["accessibility_confirmed"] is False
    assert "private-ground-key" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    lambda d: d["included"][1]["attributes"].update(booking_type="addon"),
    lambda d: d["included"][1]["attributes"].update(passenger_coverage_type="individual"),
    lambda d: d["included"][1]["attributes"].update(passenger_ids=["pax-1"]),
    lambda d: d["included"][1]["attributes"].update(segment_ids=[]),
    lambda d: d["included"][1]["attributes"].update(seats_left=1),
    lambda d: d["included"][1]["attributes"].update(booked_out=True),
    lambda d: d["included"][1]["attributes"].update(offer_expires_at="2000-01-01T00:00:00Z"),
    lambda d: d["included"][3]["attributes"].update(code="TRAIN"),
])
async def test_ground_excludes_partial_unavailable_or_non_bus_fares(mutation):
    data = ground_data()
    mutation(data)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))) as client:
        result = await TravelProvider(settings(distribusion_api_key="private", distribusion_environment="live", distribusion_offers_enabled=True), client=client).search(query("bus"))
    assert result["state"] == "ready" and result["items"] == []


@pytest.mark.asyncio
async def test_ground_missing_seat_count_is_unknown_and_http_error_hides_secrets():
    data = ground_data()
    data["included"][1]["attributes"]["seats_left"] = None
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))) as client:
        result = await TravelProvider(settings(distribusion_api_key="private", distribusion_environment="live", distribusion_offers_enabled=True), client=client).search(query("bus"))
    assert result["items"][0]["reported_seats_left"] is None
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(403, json={"error": "private-ground-key"}))) as client:
        result = await TravelProvider(settings(distribusion_api_key="private-ground-key", distribusion_environment="live", distribusion_offers_enabled=True), client=client).search(query("bus"))
    assert result["state"] == "error" and result["items"] == []
    assert "private-ground-key" not in json.dumps(result)


def test_travel_credentials_are_hidden_in_settings_repr():
    config = Settings(_env_file=None, duffel_access_token="duffel-private", distribusion_api_key="ground-private")
    assert "duffel-private" not in repr(config) and "ground-private" not in repr(config)


def nuitee_data():
    day = query().departure_date.isoformat()
    return {"data": [{"journeys": [{
        "journeyKey": "journey-provider", "parameters": {"adults": 2, "children": 0, "infants": 0},
        "segments": [{"segmentKey": "segment-provider", "originCode": "CRA", "destinationCode": "OTP", "departureTime": day+"T12:00:00", "arrivalTime": day+"T13:00:00", "direction": "OUTBOUND", "carrier": {"operatingName": "Fixture airline"}, "flight": {"marketingNumber": "123"}, "stopCount": 0}],
        "offers": [{"offerId": "opaque-provider-offer==", "expiration": (datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat(), "pricing": {"display": {"total": 234.5678, "currency": "EUR", "base": 200, "taxes": 34.5678, "fees": 0}}, "provider": {"code": "SABRE"}, "fare": {"seatsRemaining": 4}, "terms": {"refundable": False, "changeFee": {"pricing": {"display": {"amount": 25.15, "currency": "EUR"}}, "applicability": "beforeDeparture"}}, "baggage": {"hasCarryOnBag": True, "hasCheckedBag": False}, "segmentFares": [{"segmentKey": "segment-provider", "seatsRemaining": 4}]}],
    }]}]}


@pytest.mark.asyncio
async def test_nuitee_auto_selects_existing_production_key_and_preserves_total_and_conditions():
    requests = []
    def respond(request):
        requests.append(request)
        assert str(request.url) == "https://api.liteapi.travel/v3.0/flights/rates"
        assert request.headers["X-API-Key"] == "prod_private"
        body = json.loads(request.content)
        assert body["legs"] == [{"origin": "CRA", "destination": "OTP", "date": query().departure_date.isoformat()}]
        assert body["adults"] == 2 and body["children"] == body["infants"] == 0
        assert body["currency"] == "EUR"
        return httpx.Response(200, json=nuitee_data())
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = TravelProvider(settings(liteapi_api_key="prod_private", liteapi_environment="live", duffel_access_token="duffel_live_private", duffel_environment="live"), client=client)
        assert provider.flight_source == "Nuitee" and provider.flight_configured and provider.flight_environment == "live"
        result = await provider.search(query())
    assert result["state"] == "ready" and result["source"] == "Nuitee" and len(requests) == 1
    item = result["items"][0]
    assert item["provider"] == "nuitee" and item["provider_offer_id"] == "opaque-provider-offer=="
    assert item["total_price"] == "234.5678" and item["passengers"] == 2
    assert item["reported_seats_left"] == 4 and item["accessibility_confirmed"] is False
    assert item["conditions"]["changeFee"]["pricing"]["display"]["amount"] == "25.15"
    assert item["baggage"]["hasCheckedBag"] is False and item["requires_revalidation"] is True
    assert datetime.fromisoformat(item["departure_at"]).astimezone(ZoneInfo("Europe/Bucharest")).hour == 12


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation,state", [
    (lambda d: d.update(sandbox=True), "sandbox"),
    (lambda d: d["data"][0]["journeys"][0]["offers"][0]["provider"].update(code="E2E"), "sandbox"),
    (lambda d: d["data"][0]["journeys"][0]["segments"][0]["carrier"].update(operatingName="Nuitee Air"), "sandbox"),
    (lambda d: d["data"][0]["journeys"][0]["offers"][0].pop("expiration"), "error"),
    (lambda d: d["data"][0]["journeys"][0]["parameters"].update(adults=3), "error"),
    (lambda d: d["data"][0]["journeys"][0]["segments"][0].update(originCode="ZZZ"), "error"),
    (lambda d: d["data"][0]["journeys"][0]["offers"][0]["pricing"]["display"].update(currency="USD"), "error"),
    (lambda d: d["data"][0]["journeys"][0]["offers"][0]["segmentFares"][0].update(seatsRemaining=1), "ready"),
    (lambda d: d["data"][0]["journeys"][0]["offers"][0].update(expiration="2000-01-01T00:00:00Z"), "ready"),
])
async def test_nuitee_rejects_test_malformed_and_mismatched_offers(mutation, state):
    data = nuitee_data()
    mutation(data)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))) as client:
        result = await TravelProvider(settings(liteapi_api_key="prod_private", liteapi_environment="live"), client=client).search(query())
    assert result["state"] == state and result["items"] == []


@pytest.mark.asyncio
async def test_nuitee_child_composition_and_expired_or_insufficient_offers():
    data = nuitee_data()
    journey = data["data"][0]["journeys"][0]
    journey["parameters"].update(adults=1, children=1)
    journey["offers"][0]["fare"]["seatsRemaining"] = 1
    def respond(request):
        body = json.loads(request.content)
        assert body["adults"] == body["children"] == 1 and body["childrenAges"] == [10]
        return httpx.Response(200, json=data)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await TravelProvider(settings(liteapi_api_key="prod_private", liteapi_environment="live"), client=client).search(query(passenger_ages=[40, 10]))
    assert result["state"] == "ready" and result["items"] == []


@pytest.mark.asyncio
async def test_nuitee_access_denied_never_falls_back_to_duffel():
    calls = []
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(403, json={"error": "private-secret"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await TravelProvider(settings(liteapi_api_key="prod_private", liteapi_environment="live", duffel_access_token="duffel_live_private", duffel_environment="live"), client=client).search(query())
    assert len(calls) == 1 and result["state"] == "error" and result["source"] == "Nuitee"
    assert "Request production Flights API access" in result["message"]
    assert "private-secret" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("key,environment", [("sand_private", "live"), ("prod_private", "sandbox"), ("unknown_private", "live")])
async def test_nuitee_unverified_environment_never_searches(key, environment):
    def forbidden(_):
        pytest.fail("No HTTP call is allowed")
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        provider = TravelProvider(settings(liteapi_api_key=key, liteapi_environment=environment), client=client)
        assert provider.flight_environment == "sandbox" and not provider.flight_configured
        result = await provider.search(query())
    assert result["state"] == "sandbox" and result["items"] == []


def nuitee_missing_echo_data():
    """Synthetic shape regression; contains no recorded provider prices or IDs."""
    data = nuitee_data()
    journey = data["data"][0]["journeys"][0]
    journey["parameters"] = {"adults": 0, "children": 0, "infants": 0}
    offer = journey["offers"][0]
    offer.pop("provider")
    offer["pricing"]["display"] = {"total": 120.02, "currency": "EUR", "base": 100.01, "fees": 0, "taxes": 20.01, "perPassenger": {"adult": {"total": 60, "currency": "EUR", "base": 50, "fees": 0, "taxes": 10}}}
    return data


@pytest.mark.asyncio
async def test_nuitee_zero_echo_requires_component_reconciliation_and_preserves_original_total():
    data = nuitee_missing_echo_data()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))) as client:
        result = await TravelProvider(settings(liteapi_api_key="prod_private", liteapi_environment="live"), client=client).search(query())
    assert result["state"] == "ready"
    item = result["items"][0]
    assert item["total_price"] == "120.02" and item["passengers"] == 2
    assert item["content_provider"] is None
    assert item["passenger_composition_source"] == "request_reconciled_with_price_breakdown"
    assert item["reported_passenger_composition"] == {"adults": 0, "children": 0, "infants": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    lambda d: d.pop("perPassenger"),
    lambda d: d["perPassenger"]["adult"].update(currency="USD"),
    lambda d: d["perPassenger"]["adult"].update(total=120),
    lambda d: d.update(total=220.02, base=200.01),
    lambda d: d.update(total=120.05),
    lambda d: d["perPassenger"]["adult"].pop("taxes"),
])
async def test_nuitee_zero_echo_does_not_accept_unproven_group_price(mutation):
    data = nuitee_missing_echo_data()
    mutation(data["data"][0]["journeys"][0]["offers"][0]["pricing"]["display"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))) as client:
        result = await TravelProvider(settings(liteapi_api_key="prod_private", liteapi_environment="live"), client=client).search(query())
    assert result["state"] == "error" and result["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("selection,source,host", [("auto", "Nuitee", "api.liteapi.travel"), ("nuitee", "Nuitee", "api.liteapi.travel"), ("duffel", "Duffel", "api.duffel.com")])
async def test_explicit_flight_provider_selection_keeps_both_credentials(selection, source, host):
    calls = []
    def respond(request):
        calls.append(request.url.host)
        return httpx.Response(200, json=flight_data() if source == "Duffel" else nuitee_data())
    config = settings(flight_offers_provider=selection, liteapi_api_key="prod_private", liteapi_environment="live", duffel_access_token="duffel_live_private", duffel_environment="live")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = TravelProvider(config, client=client)
        assert provider.flight_source == source and provider.flight_configured and provider.flight_environment == "live"
        result = await provider.search(query())
    assert result["state"] == "ready" and result["source"] == source
    assert calls == [host]


def test_flight_provider_setting_validates_supported_values():
    assert Settings(_env_file=None, flight_offers_provider="duffel").flight_offers_provider == "duffel"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, flight_offers_provider="unknown")
