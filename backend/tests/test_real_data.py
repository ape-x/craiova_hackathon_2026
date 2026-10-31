"""Contract and isolation tests; no credentials and no live HTTP requests."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app.real_data import RealDataRequest, RealDataService, local_today
from app.providers.base import ProviderError
from app.providers.real_data import MAX_BODY, RealDataProvider, safe_url


def settings(**values):
    return SimpleNamespace(request_timeout_seconds=1, **values)


def test_hotel_search_calendar_uses_source_airport_time_zone(monkeypatch):
    import app.real_data as module
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 11, 1, 0, 30, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(module, "datetime", FixedDateTime)
    request = RealDataRequest(airport_code="LAX", destination_code="JFK", checkin="2026-10-31", checkout="2026-11-01")
    assert request.checkin.isoformat() == "2026-10-31"
    with pytest.raises(ValidationError):
        RealDataRequest(airport_code="NRT", destination_code="JFK", checkin="2026-10-31", checkout="2026-11-01")


@pytest.mark.asyncio
async def test_supplier_property_location_uses_exact_metadata_identity_and_coordinates():
    def handler(request):
        assert request.url.path == "/v3.0/data/hotel"
        assert request.url.params["hotelId"] == "property-123"
        assert request.headers["X-API-Key"] == "prod_test"
        return httpx.Response(200, json={"data": {"id": "property-123", "location": {
            "latitude": 51.47, "longitude": -0.45}}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await RealDataProvider(settings(liteapi_api_key="prod_test", liteapi_environment="live"), client=client).hotel_location("property-123")
    assert result["latitude"] == 51.47 and result["longitude"] == -0.45
    assert result["location_source"] == "Nuitee / LiteAPI"
    assert "prod_test" not in json.dumps(result)
    assert result["postal_address"]["address"] is None


@pytest.mark.asyncio
async def test_exact_property_postal_metadata_is_sourced_and_never_guessed_or_truncated():
    data={"id":"property-123","name":"Source property","address":"628 Western Avenue, London W3 0TA","country":"GB","city":"London",
        "location":{"latitude":51.53,"longitude":-.29}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={"data":data}))) as client:
        provider=RealDataProvider(settings(liteapi_api_key="prod_test",liteapi_environment="live"),client=client)
        location=await provider.hotel_location("property-123")
        postal=location["postal_address"]
        assert {key:postal[key] for key in ("name","address","city","country")}=={key:data[key] for key in ("name","address","city","country")}
        assert postal["hotel_id"]=="property-123" and postal["source"]=="Nuitee / LiteAPI"
        assert postal["retrieved_at"]==location["location_retrieved_at"]
        data["address"]="x"*2001
        assert (await provider.hotel_location("property-123"))["postal_address"]["address"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [
    {"id": "different-property", "location": {"latitude": 51.47, "longitude": -0.45}},
    {"id": "property-123", "location": {"latitude": True, "longitude": -0.45}},
    {"id": "property-123", "location": {"latitude": 51.47}},
])
async def test_supplier_property_metadata_cannot_guess_or_mix_locations(data):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": data}))) as client:
        with pytest.raises(ProviderError):
            await RealDataProvider(settings(liteapi_api_key="prod_test", liteapi_environment="live"), client=client).hotel_location("property-123")


def search(**values):
    return RealDataRequest(**({"airport_code": "CRA", "destination_code": "OTP", "checkin": local_today(), "checkout": local_today()+timedelta(days=1), "adults": 2, "rooms": 1} | values))


def osm_data():
    return {"elements": [{"type": "node", "id": 123, "lat": 44.32, "lon": 23.89, "tags": {"name": "A real provider result", "tourism": "hotel", "website": "https://example.org/hotel", "addr:city": "Craiova", "wheelchair": "yes"}}]}


def handler(request):
    assert request.url.scheme == "https"
    assert request.headers["user-agent"].startswith("AirportRecoveryLive/")
    if request.url.host == "overpass-api.de":
        assert request.method == "POST"
        assert b"around%3A20000" in request.content
        return httpx.Response(200, json=osm_data())
    assert request.url.host == "router.project-osrm.org"
    assert "/route/v1/driving/23.888599,44.3181;" in request.url.path
    return httpx.Response(200, json={"code": "Ok", "routes": [{"distance": 231234.5, "duration": 9345}]})


@pytest.mark.parametrize("override", [
    {"airport_code": "ZZZ"}, {"airport_code": "https://evil.example"}, {"destination_code": "CRA"},
    {"checkin": local_today()-timedelta(days=1)}, {"checkout": local_today()},
    {"checkout": local_today()+timedelta(days=31)}, {"rooms": 3}, {"adults": True}, {"adults": "2"},
    {"unexpected": "value"},
])
def test_search_rejects_invalid_criteria_before_io(override):
    with pytest.raises(ValidationError):
        search(**override)


@pytest.mark.asyncio
async def test_open_data_concurrent_cache_and_provenance_without_invented_inventory():
    calls = []
    def record(request):
        calls.append(request)
        return handler(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as client:
        service = RealDataService(settings(), client=client)
        results = await asyncio.gather(service.search(search()), service.search(search()))
        assert len(calls) == 2
        first, second = results
        assert first["hotels"]["state"] == "ready"
        assert first["hotels"]["source"] == "OpenStreetMap"
        assert first["hotels"]["items"][0]["id"] == "osm-node-123"
        assert first["hotels"]["items"][0]["wheelchair"] == "yes"
        assert first["hotels"]["fetched_at"] == second["hotels"]["fetched_at"]
        assert second["hotels"]["cached"] and second["transport"]["cached"]
        assert first["transport"]["items"][0]["distance_km"] == 231.2
        assert first["transport"]["items"][0]["duration_minutes"] == 155.8
        assert first["transport"]["items"][0]["traffic"] == "none"
        assert first["hotel_offers"]["state"] == "not_configured"
        assert first["hotel_offers"]["items"] == []
        assert "capacity" not in json.dumps(first) and "total_price" not in json.dumps(first)
        second["hotels"]["items"].clear()
        assert len((await service.search(search()))["hotels"]["items"]) == 1


@pytest.mark.asyncio
async def test_failure_is_visible_but_other_source_remains_available():
    def fail_places(request):
        if request.url.host == "overpass-api.de":
            return httpx.Response(429, json={"secret": "never expose me"})
        return handler(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fail_places)) as client:
        result = await RealDataService(settings(), client=client).search(search())
    assert result["hotels"]["state"] == "error" and result["hotels"]["items"] == []
    assert "429" in result["hotels"]["message"]
    assert "never expose" not in json.dumps(result)
    assert result["transport"]["state"] == "ready"


@pytest.mark.parametrize("payload", [
    {"elements": [], "remark": "query timed out"},
    {"elements": [{"type": "node", "id": 1, "lat": 101, "lon": 24, "tags": {"name": "bad"}}]},
    {"elements": [{"type": "node", "id": 1, "lat": True, "lon": 24, "tags": {"name": "bad"}}]},
    {"elements": {}},
])
@pytest.mark.asyncio
async def test_incomplete_or_malformed_osm_data_never_becomes_success(payload):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        provider = RealDataProvider(settings(), client=client)
        with pytest.raises(ProviderError, match="invalid response"):
            await provider.hotels({"latitude": 44.3, "longitude": 23.8})


@pytest.mark.asyncio
async def test_redirect_body_limit_and_total_timeout():
    for response in [httpx.Response(302, headers={"location": "https://evil.example"}), httpx.Response(200, content=b" "*(MAX_BODY+1))]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
            with pytest.raises(ProviderError):
                await RealDataProvider(settings(), client=client).hotels({"latitude": 44.3, "longitude": 23.8})
    async def slow(_):
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={"elements": []})
    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
        provider = RealDataProvider(settings(), client=client)
        provider.timeout = 0.01
        with pytest.raises(ProviderError, match="timed out"):
            await provider.hotels({"latitude": 44.3, "longitude": 23.8})


@pytest.mark.asyncio
async def test_google_uses_separate_server_key_and_never_falls_back():
    calls = []
    def google(request):
        calls.append(request)
        assert request.headers["x-goog-api-key"] == "server-only-key"
        assert request.url.host in ["places.googleapis.com", "routes.googleapis.com"]
        assert "browser-key" not in request.content.decode()
        return httpx.Response(403, json={"error": "server-only-key"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(google)) as client:
        service = RealDataService(settings(google_maps_api_key="browser-key", google_maps_server_api_key="server-only-key"), client=client)
        result = await service.search(search())
    assert len(calls) == 2
    assert result["hotels"]["source"] == "Google Places"
    assert result["transport"]["source"] == "Google Routes"
    assert result["hotels"]["state"] == result["transport"]["state"] == "error"
    assert "server-only-key" not in json.dumps(result)


@pytest.mark.asyncio
async def test_google_fields_route_and_no_room_capacity():
    def google(request):
        if request.url.host == "places.googleapis.com":
            body = json.loads(request.content)
            assert body["includedTypes"] == ["hotel"] and body["maxResultCount"] == 10
            assert request.headers["x-goog-fieldmask"] != "*"
            return httpx.Response(200, json={"places": [{"id": "place-id", "displayName": {"text": "Provider hotel"}, "location": {"latitude": 44.32, "longitude": 23.89}, "googleMapsUri": "https://maps.google.com/?cid=1", "accessibilityOptions": {"wheelchairAccessibleEntrance": False}}]})
        assert json.loads(request.content)["routingPreference"] == "TRAFFIC_AWARE"
        return httpx.Response(200, json={"routes": [{"duration": "9300s", "distanceMeters": 230000}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(google)) as client:
        result = await RealDataService(settings(google_maps_server_api_key="test-key"), client=client).search(search())
    assert result["hotels"]["items"][0]["wheelchair"] == "no"
    assert result["transport"]["items"][0]["traffic"] == "live"
    assert "capacity" not in json.dumps(result)


@pytest.mark.asyncio
async def test_google_content_is_fetched_again_and_never_stored_in_service_cache():
    calls = {"places": 0, "routes": 0}
    def google(request):
        if request.url.host == "places.googleapis.com":
            calls["places"] += 1
            return httpx.Response(200, json={"places": [{"id": "place-id", "displayName": {"text": f'Current hotel {calls["places"]}'}, "location": {"latitude": 44.32, "longitude": 23.89}, "googleMapsUri": "https://maps.google.com/?cid=1"}]})
        calls["routes"] += 1
        return httpx.Response(200, json={"routes": [{"duration": f'{calls["routes"] * 9300}s', "distanceMeters": 230000}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(google)) as client:
        service = RealDataService(settings(google_maps_server_api_key="test-key"), client=client)
        first = await service.search(search())
        assert service._cache == {}
        second = await service.search(search())
        assert service._cache == {}
    assert calls == {"places": 2, "routes": 2}
    assert first["hotels"]["items"][0]["name"] == "Current hotel 1"
    assert second["hotels"]["items"][0]["name"] == "Current hotel 2"
    assert first["transport"]["items"][0]["duration_minutes"] == 155.0
    assert second["transport"]["items"][0]["duration_minutes"] == 310.0
    for result in (first, second):
        assert result["hotels"]["cached"] is False
        assert result["transport"]["cached"] is False
        assert "BOOKING_" not in result["hotel_offers"]["message"]


@pytest.mark.asyncio
async def test_booking_search_details_join_preserves_exact_full_stay_price():
    booking_calls = []
    def booking(request):
        if request.url.host != "demandapi.booking.com":
            return handler(request)
        booking_calls.append(request)
        assert request.headers["authorization"] == "Bearer partner-only-key"
        assert request.headers["x-affiliate-id"] == "1234"
        body = json.loads(request.content)
        if request.url.path.endswith("/search"):
            assert body["rows"] == 10
            assert body["booker"] == {"country": "ro", "platform": "desktop"}
            assert body["guests"] == {"number_of_adults": 2, "number_of_rooms": 1}
            assert body["checkin"] == local_today().isoformat()
            return httpx.Response(200, content=b'{"data":[{"id":42,"currency":{"booker":"EUR","accommodation":"RON"},"price":{"total":{"booker_currency":123.4501}},"url":"https://www.booking.com/hotel/ro/example.html"}]}')
        assert request.url.path == "/3.3/accommodations/details"
        assert body == {"accommodations": [42], "languages": ["en-gb"]}
        return httpx.Response(200, json={"data": [{"id": 42, "name": {"en-gb": "Actual hotel"}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(booking)) as client:
        service = RealDataService(settings(booking_demand_api_token="partner-only-key", booking_affiliate_id="1234"), client=client)
        result = await service.search(search())
        assert (await service.search(search()))["hotel_offers"]["cached"]
    assert len(booking_calls) == 2
    offer = result["hotel_offers"]["items"][0]
    assert {key: offer[key] for key in ("id", "name", "total_price", "currency", "url")} == {"id": "42", "name": "Actual hotel", "total_price": "123.4501", "currency": "EUR", "url": "https://www.booking.com/hotel/ro/example.html"}
    assert offer["provider"] == "booking"
    assert offer["provider_offer_id"] is None and offer["occupancies"] is None
    assert offer["expires_at"] is None and offer["requires_revalidation"] is True
    assert offer["availability_status"] == "quoted_not_reserved"
    assert "partner-only-key" not in json.dumps(result)


def test_external_links_are_passive_http_only():
    for value in ["javascript:alert(1)", "file:///etc/passwd", "//evil.example", "https://u:p@example.org", "https://example.org\n", None]:
        assert safe_url(value) is None
    assert safe_url("https://example.org/path") == "https://example.org/path"


def liteapi_data(amount="321.4501", *, rooms=2):
    return {"sandbox": False, "hotels": [{"id": "lp123", "name": "Provider hotel"}], "data": [{
        "hotelId": "lp123", "roomTypes": [{
            "offerId": "unused-booking-offer", "offerRetailRate": {"amount": amount, "currency": "EUR"},
            "rates": [{"occupancyNumber": index+1, "adultCount": count, "childCount": 0, "retailRate": {"taxesAndFees": None}}
                      for index, count in enumerate([3, 2][:rooms])],
        }],
    }]}


def test_hotel_provider_selection_is_explicit_and_never_falls_back():
    both = dict(liteapi_api_key="prod_test", booking_demand_api_token="booking_test", booking_affiliate_id="1", liteapi_environment="live")
    assert RealDataProvider(settings(**both)).hotel_offer_provider == "liteapi"
    assert RealDataProvider(settings(**both, hotel_offers_provider="booking")).hotel_offer_provider == "booking"
    assert RealDataProvider(settings(hotel_offers_provider="liteapi", booking_demand_api_token="booking_test", booking_affiliate_id="1")).hotel_offer_provider == "liteapi"


@pytest.mark.asyncio
@pytest.mark.parametrize("key,environment", [("sand_test", "live"), ("sandbox_test", "live"), ("prod_test", "sandbox")])
async def test_sandbox_liteapi_never_makes_a_rate_call_or_returns_live_prices(key, environment):
    calls = []
    def capture(request):
        calls.append(request.url.host)
        return handler(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(capture)) as client:
        service = RealDataService(settings(liteapi_api_key=key, liteapi_environment=environment), client=client)
        result = await service.search(search())
        assert not service.provider.hotel_offers_configured
    assert "api.liteapi.travel" not in calls
    assert result["hotel_offers"]["source"] == "Nuitee / LiteAPI"
    assert result["hotel_offers"]["state"] == "sandbox"
    assert result["hotel_offers"]["mode"] == "sandbox"
    assert result["hotel_offers"]["items"] == []
    assert "total_price" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("include_environment_flag", [True, False])
async def test_liteapi_live_request_full_room_distribution_and_aggregate_decimal_price(include_environment_flag):
    calls = []
    def rates(request):
        if request.url.host != "api.liteapi.travel":
            return handler(request)
        calls.append(request)
        assert request.method == "POST" and request.url.path == "/v3.0/hotels/rates"
        assert request.headers["X-API-Key"] == "prod_private_test"
        body = json.loads(request.content)
        assert body["occupancies"] == [{"adults": 3, "children": []}, {"adults": 2, "children": []}]
        assert body["currency"] == "EUR" and body["guestNationality"] == "RO"
        assert body["includeHotelData"] is True and body["maxRatesPerHotel"] == 1
        assert body["limit"] == 10 and body["timeout"] <= 20
        assert not any(key in body for key in ["payment", "holder", "offerId"])
        payload = liteapi_data()
        if not include_environment_flag:
            payload.pop("sandbox")
        return httpx.Response(200, json=payload)
    async with httpx.AsyncClient(transport=httpx.MockTransport(rates)) as client:
        service = RealDataService(settings(liteapi_api_key="prod_private_test", liteapi_environment="live"), client=client)
        result = await service.search(search(adults=5, rooms=2))
        assert service.provider.hotel_offers_configured
        cached_result = (await service.search(search(adults=5, rooms=2)))["hotel_offers"]
        assert cached_result["cached"]
    assert len(calls) == 1
    assert result["hotel_offers"]["source"] == "Nuitee / LiteAPI"
    assert result["hotel_offers"]["state"] == "ready"
    offer = result["hotel_offers"]["items"][0]
    assert {key: offer[key] for key in ("id", "name", "total_price", "currency", "url")} == {"id": "lp123", "name": "Provider hotel", "total_price": "321.4501", "currency": "EUR", "url": None}
    assert offer["provider"] == "liteapi" and offer["provider_offer_id"] == "unused-booking-offer"
    assert offer["adults"] == 5 and offer["rooms"] == 2
    assert offer["occupancies"] == [{"adults": 3, "children": []}, {"adults": 2, "children": []}]
    assert offer["guest_nationality"] == "RO"
    assert offer["checkin"] == local_today().isoformat()
    assert offer["checkout"] == (local_today() + timedelta(days=1)).isoformat()
    assert offer["price_scope"] == "full_stay_all_requested_rooms"
    assert offer["availability_status"] == "quoted_not_reserved"
    assert offer["expires_at"] is None and offer["requires_revalidation"] is True
    assert offer["refundable"] is None
    assert datetime.fromisoformat(offer["quoted_at"]).tzinfo == timezone.utc
    assert cached_result["items"][0]["quoted_at"] == offer["quoted_at"]
    assert all(rate["refundable"] is None for rate in offer["room_rates"])
    assert not any(key in offer for key in ("capacity", "rooms_available", "reservation_id", "prebook_id"))
    assert "prod_private_test" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation,expected_state", [
    (lambda d: d.update(sandbox=True), "sandbox"),
    (lambda d: d.update(sandbox=None), "error"),
    (lambda d: d.update(sandbox="false"), "error"),
    (lambda d: d.update(sandbox=0), "error"),
    (lambda d: d["data"][0]["roomTypes"][0]["rates"].pop(), "error"),
    (lambda d: d["data"][0]["roomTypes"][0]["rates"][0].update(adultCount=1), "error"),
    (lambda d: d["data"][0]["roomTypes"][0]["rates"][0].update(childrenAges=[7]), "error"),
    (lambda d: d["data"][0]["roomTypes"][0].pop("offerId"), "error"),
    (lambda d: d["data"][0]["roomTypes"][0].update(offerId=""), "error"),
    (lambda d: d["data"][0]["roomTypes"][0]["offerRetailRate"].update(amount=-1), "error"),
    (lambda d: d["data"][0]["roomTypes"][0]["offerRetailRate"].update(currency="USD"), "error"),
    (lambda d: d.update(hotels=[]), "error"),
    (lambda d: d.update(error={"code": 4011, "message": "prod_private_test"}), "error"),
])
async def test_liteapi_rejects_sandbox_unverified_and_partial_quotes(mutation, expected_state):
    data = liteapi_data()
    mutation(data)
    def respond(request):
        return httpx.Response(200, json=data) if request.url.host == "api.liteapi.travel" else handler(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = RealDataService(settings(liteapi_api_key="prod_private_test", liteapi_environment="live"), client=client)
        result = await service.search(search(adults=5, rooms=2))
    assert result["hotel_offers"]["state"] == expected_state
    assert result["hotel_offers"]["items"] == []
    assert "prod_private_test" not in json.dumps(result)


@pytest.mark.asyncio
async def test_liteapi_documented_empty_availability_is_ready_and_explicit_selection_stays_selected():
    def respond(request):
        if request.url.host == "api.liteapi.travel":
            return httpx.Response(200, json={"sandbox": False, "error": {"code": 2001}})
        return handler(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = RealDataService(settings(liteapi_api_key="prod_test", liteapi_environment="live"), client=client)
        assert (await service.search(search()))["hotel_offers"]["state"] == "ready"
        selected_missing = RealDataService(settings(hotel_offers_provider="liteapi", booking_demand_api_token="booking_test", booking_affiliate_id="1"), client=client)
        result = await selected_missing.search(search())
        assert result["hotel_offers"]["source"] == "Nuitee / LiteAPI"
        assert result["hotel_offers"]["state"] == "not_configured"
        assert result["hotel_offers"]["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("aggregate", ["array", "per_room"])
async def test_liteapi_accepts_documented_price_shapes_without_multiplying_stay(aggregate):
    data = liteapi_data()
    offer = data["data"][0]["roomTypes"][0]
    if aggregate == "array":
        offer["offerRetailRate"] = [{"amount": "321.4501", "currency": "EUR"}]
    else:
        offer.pop("offerRetailRate")
        offer["rates"][0]["retailRate"] = {"total": [{"amount": "200.1201", "currency": "EUR"}]}
        offer["rates"][1]["retailRate"] = {"total": [{"amount": "121.3300", "currency": "EUR"}]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))) as client:
        provider = RealDataProvider(settings(liteapi_api_key="prod_test", liteapi_environment="live"), client=client)
        result = await provider.offers({"latitude": 44.3, "longitude": 23.8}, search(adults=5, rooms=2, checkout=local_today()+timedelta(days=3)))
    assert result[0]["total_price"] == "321.4501"


@pytest.mark.asyncio
async def test_liteapi_preserves_selected_quote_identity_terms_and_unknown_expiry():
    data = liteapi_data()
    data["hotels"][0].update(latitude=44.32, longitude=23.89)
    offer = data["data"][0]["roomTypes"][0]
    offer["offerId"] = "exact-opaque-offer-" + "x" * 1500
    first, second = offer["rates"]
    first.update(
        rateId="rate-one", name="Twin room", boardType="BI", remarks="Payment at the property.",
        cancellationPolicies={"refundableTag": "RFN", "hotelRemarks": ["Cancellation penalty applies after deadline."], "cancelPolicyInfos": [
            {"cancelTime": "2026-11-02 18:00:00", "amount": "150.1201", "currency": "EUR", "type": "amount"},
        ]},
        retailRate={"taxesAndFees": [
            {"included": True, "description": "Included tax", "amount": "20.00", "currency": "EUR"},
            {"included": False, "description": "Local tax payable at hotel", "amount": "9.7001", "currency": "RON"},
        ]},
    )
    second.update(rateId="rate-two", cancellationPolicies={"refundableTag": "NRFN", "cancelPolicyInfos": []})
    expensive = json.loads(json.dumps(offer))
    expensive["offerId"] = "more-expensive-alternative"
    expensive["offerRetailRate"]["amount"] = "999.99"
    data["data"][0]["roomTypes"].insert(0, expensive)
    # Undocumented supplier-like fields must not create a validity guarantee.
    offer["expiresAt"] = "2099-01-01T00:00:00Z"
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json=data)

    before = datetime.now(timezone.utc)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = RealDataProvider(settings(liteapi_api_key="prod_test", liteapi_environment="live"), client=client)
        quote = (await provider.offers({"latitude": 44.3, "longitude": 23.8}, search(adults=5, rooms=2, checkout=local_today()+timedelta(days=3))))[0]
    assert calls == [("POST", "/v3.0/hotels/rates")]
    assert quote["provider_offer_id"] == offer["offerId"]
    assert quote["total_price"] == "321.4501"
    assert quote["checkout"] == (local_today()+timedelta(days=3)).isoformat()
    assert before <= datetime.fromisoformat(quote["quoted_at"]) <= datetime.now(timezone.utc)
    assert quote["expires_at"] is None and quote["requires_revalidation"] is True
    assert quote["refundable"] is False
    assert quote["latitude"] == 44.32 and quote["longitude"] == 23.89
    assert quote["room_rates"][0] == {
        "rate_id": "rate-one", "occupancy_number": 1, "adults": 3, "children": [],
        "room_name": "Twin room", "board_type": "BI", "refundable": True,
        "cancellation_penalties": [{"from": "2026-11-02 18:00:00", "timezone": "GMT", "amount": "150.1201", "currency": "EUR", "type": "amount"}],
        "cancellation_remarks": ["Cancellation penalty applies after deadline."],
        "remarks": "Payment at the property.",
        "additional_fees": [{"amount": "9.7001", "currency": "RON", "description": "Local tax payable at hotel"}],
        "booking_total": None, "fees_disclosure": "itemized", "taxes_and_fees": [
            {"included": True, "amount": "20.00", "currency": "EUR", "description": "Included tax"},
            {"included": False, "amount": "9.7001", "currency": "RON", "description": "Local tax payable at hotel"},
        ],
    }
    assert quote["room_rates"][1]["rate_id"] == "rate-two"


@pytest.mark.asyncio
@pytest.mark.parametrize("terms", [
    {"cancellationPolicies": {"cancelPolicyInfos": [{"cancelTime": "2026-11-02", "amount": "NaN", "currency": "EUR"}]}},
    {"retailRate": {"taxesAndFees": [{"included": "false", "amount": 5, "currency": "EUR"}]}},
    {"remarks": "x" * 32769},
])
async def test_liteapi_rejects_unusable_terms_instead_of_dropping_them(terms):
    data = liteapi_data()
    data["data"][0]["roomTypes"][0]["rates"][0].update(terms)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))) as client:
        provider = RealDataProvider(settings(liteapi_api_key="prod_test", liteapi_environment="live"), client=client)
        with pytest.raises(ProviderError, match="invalid response"):
            await provider.offers({"latitude": 44.3, "longitude": 23.8}, search(adults=5, rooms=2))


async def _hotel_quote(data):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))) as client:
        provider = RealDataProvider(settings(liteapi_api_key="prod_test", liteapi_environment="live"), client=client)
        return (await provider.offers({"latitude": 44.3, "longitude": 23.8}, search(adults=5, rooms=2, checkout=local_today()+timedelta(days=3))))[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("fees", [None, []])
async def test_liteapi_documented_included_only_fees_preserve_exact_booking_total(fees):
    data = liteapi_data()
    for rate in data["data"][0]["roomTypes"][0]["rates"]:
        rate["retailRate"]["taxesAndFees"] = fees
    quote = await _hotel_quote(data)
    assert quote["total_price"] == "321.4501"
    assert quote["pricing"] == {
        "booking_total": {"amount": "321.4501", "currency": "EUR"}, "excluded_fee_totals": [],
        "mandatory_total": {"amount": "321.4501", "currency": "EUR"}, "status": "complete",
        "unresolved_reasons": [], "payment_timing": "unconfirmed",
    }
    assert all(rate["fees_disclosure"] == "all_included" for rate in quote["room_rates"])
    assert "due_now" not in json.dumps(quote)


@pytest.mark.asyncio
async def test_liteapi_mandatory_cost_adds_excluded_fees_once_per_occupancy_not_per_guest_or_night():
    data = liteapi_data("221.00")
    rates = data["data"][0]["roomTypes"][0]["rates"]
    for rate, amount in zip(rates, ("100.00", "121.00")):
        rate["remarks"] = "Local tax: 7 EUR payable at the property."
        rate["retailRate"] = {"total": [{"amount": amount, "currency": "EUR"}], "taxesAndFees": [
            {"included": True, "amount": "12.00", "currency": "EUR", "description": "Already in the total"},
            {"included": False, "amount": "7.00", "currency": "EUR", "description": "Local tax"},
        ]}
    quote = await _hotel_quote(data)
    assert quote["total_price"] == "221.00"
    assert quote["pricing"]["excluded_fee_totals"] == [{"amount": "14.00", "currency": "EUR"}]
    assert quote["pricing"]["mandatory_total"] == {"amount": "235.00", "currency": "EUR"}
    assert [rate["booking_total"]["amount"] for rate in quote["room_rates"]] == ["100.00", "121.00"]


@pytest.mark.asyncio
async def test_liteapi_missing_fee_disclosure_remains_visible_with_incomplete_budget_warning():
    data = liteapi_data()
    data["data"][0]["roomTypes"][0]["rates"][0]["retailRate"].pop("taxesAndFees")
    def respond(request):
        return httpx.Response(200, json=data) if request.url.host == "api.liteapi.travel" else handler(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = RealDataService(settings(liteapi_api_key="prod_test", liteapi_environment="live"), client=client)
        result = (await service.search(search(adults=5, rooms=2)))["hotel_offers"]
    assert result["state"] == "ready" and "unresolved mandatory costs" in result["message"]
    quote = result["items"][0]
    assert quote["total_price"] == "321.4501"
    assert quote["pricing"]["mandatory_total"] is None and quote["pricing"]["status"] == "incomplete"
    assert quote["pricing"]["unresolved_reasons"] == ["fees_not_disclosed"]
    assert quote["room_rates"][0]["fees_disclosure"] == "missing"


@pytest.mark.asyncio
@pytest.mark.parametrize("field,explicit_null", [("amount", False), ("amount", True), ("currency", False), ("currency", True)])
async def test_liteapi_missing_excluded_fee_component_never_becomes_zero(field, explicit_null):
    data = liteapi_data()
    fee = {"included": False, "amount": "9.70", "currency": "EUR", "description": "Property tax"}
    if explicit_null:
        fee[field] = None
    else:
        fee.pop(field)
    data["data"][0]["roomTypes"][0]["rates"][0]["retailRate"]["taxesAndFees"] = [fee]
    quote = await _hotel_quote(data)
    assert quote["pricing"]["mandatory_total"] is None
    assert quote["pricing"]["unresolved_reasons"] == [f"excluded_fee_{field}_missing"]
    assert quote["room_rates"][0]["taxes_and_fees"][0][field] is None
    assert quote["pricing"]["excluded_fee_totals"] == []


@pytest.mark.asyncio
async def test_liteapi_mixed_currency_extras_keep_exact_amounts_without_unsourced_conversion():
    data = liteapi_data()
    data["data"][0]["roomTypes"][0]["rates"][0]["retailRate"]["taxesAndFees"] = [
        {"included": False, "amount": "9.7001", "currency": "RON"},
        {"included": False, "amount": "1.2002", "currency": "EUR"},
    ]
    quote = await _hotel_quote(data)
    assert quote["pricing"]["excluded_fee_totals"] == [
        {"amount": "1.2002", "currency": "EUR"}, {"amount": "9.7001", "currency": "RON"},
    ]
    assert quote["pricing"]["mandatory_total"] is None
    assert quote["pricing"]["unresolved_reasons"] == ["unsupported_budget_currency"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fees", [
    {}, "unknown", [{"included": "false"}], [{"included": False, "amount": True, "currency": "EUR"}],
    [{"included": False, "amount": "NaN", "currency": "EUR"}],
    [{"included": False, "amount": "-1", "currency": "EUR"}],
    [{"included": False, "amount": "1", "currency": "eur"}],
])
async def test_liteapi_malformed_fee_data_is_not_treated_as_no_extra_cost(fees):
    data = liteapi_data()
    data["data"][0]["roomTypes"][0]["rates"][0]["retailRate"]["taxesAndFees"] = fees
    with pytest.raises(ProviderError, match="invalid response"):
        await _hotel_quote(data)


@pytest.mark.asyncio
async def test_liteapi_offer_total_must_reconcile_with_all_supplied_occupancy_totals():
    data = liteapi_data("100.00")
    for rate in data["data"][0]["roomTypes"][0]["rates"]:
        rate["retailRate"]["total"] = [{"amount": "60.00", "currency": "EUR"}]
    with pytest.raises(ProviderError, match="invalid response"):
        await _hotel_quote(data)


@pytest.mark.asyncio
async def test_liteapi_selects_lowest_complete_mandatory_total_instead_of_lowest_base():
    data = liteapi_data("99.00")
    offers = data["data"][0]["roomTypes"]
    unknown = offers[0]
    unknown["offerId"] = "unknown-fees"
    unknown["rates"][0]["retailRate"].pop("taxesAndFees")
    expensive_total = json.loads(json.dumps(unknown))
    expensive_total["offerId"] = "low-base-high-extras"
    expensive_total["offerRetailRate"]["amount"] = "100.00"
    expensive_total["rates"][0]["retailRate"]["taxesAndFees"] = [{"included": False, "amount": "100.00", "currency": "EUR"}]
    complete = json.loads(json.dumps(unknown))
    complete["offerId"] = "lowest-mandatory-cost"
    complete["offerRetailRate"]["amount"] = "150.00"
    complete["rates"][0]["retailRate"]["taxesAndFees"] = None
    offers.extend([expensive_total, complete])
    quote = await _hotel_quote(data)
    assert quote["provider_offer_id"] == "lowest-mandatory-cost"
    assert quote["total_price"] == "150.00"
    assert quote["pricing"]["mandatory_total"] == {"amount": "150.00", "currency": "EUR"}
