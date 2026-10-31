"""Offline, authored contract fixtures; no retained Google route/price response."""
import copy
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.providers.public_transit import FIELD_MASK, TransitRouteRequest, build_transit_request, normalize_transit_response


NOW = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)


def request(**changes):
    return TransitRouteRequest(**({
        "origin": {"label": "Terminal entrance", "latitude": 44.5, "longitude": 26.1, "source": "operator-confirmed entrance"},
        "destination": {"label": "Property entrance", "latitude": 44.3, "longitude": 23.8, "source": "supplier property coordinates"},
        "departure_at": "2030-11-01T09:00:00+02:00", "max_walking_minutes": 10,
    } | changes))


def sample():
    # Invented stops/times exercise the official API shape, not an actual service.
    return {"routes": [{"duration": "4200s", "distanceMeters": 72000, "warnings": ["Check station access"],
        "legs": [{"steps": [
            {"travelMode": "WALK", "staticDuration": "120.25s", "distanceMeters": 100},
            {"travelMode": "TRANSIT", "staticDuration": "3600s", "transitDetails": {
                "stopDetails": {
                    "departureTime": "2030-11-01T09:05:00+02:00", "arrivalTime": "2030-11-01T10:05:00+02:00",
                    "departureStop": {"name": "Fixture A", "location": {"latLng": {"latitude": 44.5, "longitude": 26.1}}},
                    "arrivalStop": {"name": "Fixture B", "location": {"latLng": {"latitude": 44.3, "longitude": 23.8}}},
                }, "transitLine": {"nameShort": "T1", "vehicle": {"type": "HEAVY_RAIL"},
                    "agencies": [{"name": "Fixture Rail", "uri": "https://example.org/tickets"}]},
                "headsign": "Fixture B", "stopCount": 3,
            }},
            {"travelMode": "WALK", "staticDuration": "180.5s"},
        ]}], "travelAdvisory": {"transitFare": {}}}]}


def normalize(payload=None, query=None):
    return normalize_transit_response(payload if payload is not None else sample(), query or request(), received_at=NOW)


def test_explicit_request_uses_source_coordinates_and_future_offset_without_false_hard_limit():
    query = request()
    outbound = build_transit_request(query, now=NOW)
    assert outbound["origin"]["location"]["latLng"] == {"latitude": 44.5, "longitude": 26.1}
    assert outbound["departureTime"] == "2030-11-01T07:00:00+00:00"
    assert outbound["transitPreferences"] == {"routingPreference": "LESS_WALKING"}
    assert outbound["travelMode"] == "TRANSIT" and outbound["computeAlternativeRoutes"]
    assert "intermediates" not in outbound and "max_walking_minutes" not in outbound
    assert "routes.legs.steps.staticDuration" in FIELD_MASK and "routes.warnings" in FIELD_MASK
    assert "*" not in FIELD_MASK
    result = normalize(query=query)
    assert result["request"]["origin"]["source"] == "operator-confirmed entrance"
    assert result["request"]["destination"]["source"] == "supplier property coordinates"
    assert result["attribution"] == "Google Maps" and result["walking_warning"]


@pytest.mark.parametrize("changes", [
    {"departure_at": "2030-11-01T09:00:00"}, {"departure_at": 1_900_000_000},
    {"max_walking_minutes": True}, {"max_walking_minutes": "10"}, {"max_walking_minutes": -1},
    {"max_walking_minutes": float("inf")}, {"origin": {"latitude": 44.5, "longitude": 26.1}},
    {"origin": {"label": "A", "latitude": True, "longitude": 26.1, "source": "catalog"}},
    {"origin": {"label": "A", "latitude": 91, "longitude": 26.1, "source": "catalog"}},
    {"origin": {"label": "A", "latitude": 44.5, "longitude": 26.1, "source": "  "}},
    {"passengers": 4},  # The upstream API cannot price a party or report its seat availability.
])
def test_ambiguous_or_unsupported_query_is_rejected(changes):
    with pytest.raises(ValidationError):
        request(**changes)


def test_departure_must_be_future_at_dispatch_not_an_implicit_now():
    with pytest.raises(ValueError, match="future"):
        build_transit_request(request(departure_at=NOW), now=NOW)
    with pytest.raises(ValueError, match="UTC offset"):
        build_transit_request(request(), now=NOW.replace(tzinfo=None))
    values = request().model_dump()
    values.pop("max_walking_minutes")
    with pytest.raises(ValidationError):
        TransitRouteRequest.model_validate(values)


def test_schedules_preserve_offsets_stops_agencies_and_do_not_imply_inventory():
    result = normalize()
    route = result["options"][0]
    segment = route["steps"][1]["transit"]
    assert segment["departure_at"] == "2030-11-01T09:05:00+02:00"
    assert segment["arrival_at"] == "2030-11-01T10:05:00+02:00"
    assert segment["departure_stop"]["name"] == "Fixture A"
    assert segment["arrival_stop"]["coordinates"] == {"latitude": 44.3, "longitude": 23.8}
    assert segment["agencies"] == [{"name": "Fixture Rail", "url": "https://example.org/tickets"}]
    assert route["availability_status"] == "schedule_only" and route["booking_supported"] is False
    assert route["seat_availability"] is route["accessibility_confirmed"] is None
    assert route["estimated_fare"] is None and route["fare_status"] == "unavailable"
    assert route["walking"] == {"status": "within_limit", "duration_seconds": 300.75, "known_duration_seconds": 300.75, "limit_minutes": 10}
    assert route["warnings"] == ["Check station access"]
    assert "capacity" not in route and "arrival_at" not in route  # No invented final door arrival.


def test_all_walking_steps_across_legs_count_toward_explicit_limit():
    payload = sample()
    payload["routes"][0]["legs"].append({"steps": [{"travelMode": "WALK", "staticDuration": "300s"}]})
    walking = normalize(payload)["options"][0]["walking"]
    assert walking["duration_seconds"] == 600.75 and walking["status"] == "exceeds_limit"
    assert normalize(query=request(max_walking_minutes=0))["options"][0]["walking"]["status"] == "exceeds_limit"


@pytest.mark.parametrize("duration", [None, "", "1m", "NaNs", "-1s", 120, True])
def test_missing_or_invalid_walking_duration_is_unknown_never_zero(duration):
    payload = sample()
    payload["routes"][0]["legs"][0]["steps"][0]["staticDuration"] = duration
    route = normalize(payload)["options"][0]
    assert route["walking"]["status"] == "unknown"
    assert route["walking"]["duration_seconds"] is None
    assert route["walking"]["known_duration_seconds"] == 180.5
    assert "incomplete_walking_duration" in route["issues"]


@pytest.mark.parametrize("route", [{}, {"legs": []}, {"legs": [{}]}, {"legs": [{"steps": [{}]}]}])
def test_missing_steps_or_unknown_mode_never_become_zero_walking(route):
    output = normalize({"routes": [route]})["options"][0]
    assert output["walking"]["status"] == "unknown" and output["walking"]["duration_seconds"] is None


def test_decimal_fare_is_route_estimate_not_booking_or_party_total():
    payload = sample()
    payload["routes"][0]["travelAdvisory"]["transitFare"] = {"currencyCode": "RON", "units": "42", "nanos": 123456789}
    route = normalize(payload)["options"][0]
    assert route["estimated_fare"] == {"currency": "RON", "amount": "42.123456789", "kind": "estimated_route_fare", "party_total": None}
    assert route["fare_status"] == "complete_estimate"
    payload["routes"][0]["travelAdvisory"]["transitFare"] = {"currencyCode": "RON", "units": "0"}
    assert normalize(payload)["options"][0]["estimated_fare"]["amount"] == "0"  # Only explicit zero.


@pytest.mark.parametrize("fare", [
    {"currencyCode": "RON"}, {"units": "42"}, {"currencyCode": "RON", "units": "-1"},
    {"currencyCode": "RON", "units": "42", "nanos": -1},
    {"currencyCode": "RON", "units": "42", "nanos": 1_000_000_000},
    {"currencyCode": "RON", "units": True}, {"currencyCode": "RON", "units": "NaN"},
])
def test_partial_or_invalid_money_never_becomes_free_or_payable(fare):
    payload = sample()
    payload["routes"][0]["travelAdvisory"]["transitFare"] = fare
    route = normalize(payload)["options"][0]
    assert route["estimated_fare"] is None and route["fare_status"] == "invalid"


def test_bad_schedule_is_visible_and_unsafe_agency_links_are_removed():
    payload = sample()
    details = payload["routes"][0]["legs"][0]["steps"][1]["transitDetails"]
    details["stopDetails"]["arrivalTime"] = "2030-11-01T08:00:00+02:00"
    details["transitLine"]["agencies"][0]["uri"] = "javascript:alert(1)"
    route = normalize(payload)["options"][0]
    assert "incomplete_transit_schedule" in route["issues"]
    assert route["steps"][1]["transit"]["agencies"][0]["url"] is None
    assert route["steps"][1]["transit"]["arrival_at"] == "2030-11-01T08:00:00+02:00"  # Kept for inspection, not corrected.


def test_no_routes_is_distinct_from_provider_error_and_response_is_not_mutated():
    assert normalize({})["state"] == "no_routes"
    assert normalize({"routes": []})["options"] == []
    original = sample()
    snapshot = copy.deepcopy(original)
    normalize(original)
    assert original == snapshot
    for payload in ({"error": {"message": "denied"}}, {"routes": None}, {"routes": [sample()["routes"][0]] * 21}):
        with pytest.raises(ValueError):
            normalize(payload)
