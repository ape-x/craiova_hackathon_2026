"""Case-local operation bans with sourced itineraries, entirely offline."""
from datetime import datetime, timedelta

import pytest

from app.airport_constraints import case_airport_operation_blocked, flight_airport_operations
from app.hotel_recovery import validate_hotel_job
from app.inventory_lifecycle import _resource_locations, protected_inventory
from app.models import CaseAirportRestriction, Resource, Strategy
from app.recovery_jobs import RecoveryJobRequest, flight_query_options
from app.scenario_schema import WeatherDefinition
from app.solver import build_candidate, resource_blocked_by_incident, resource_feasible
from app.store import DomainError, utcnow
from test_hotel_recovery_profile import hotel_command_setup
from test_live_commands import command_setup
from test_recovery_ground_paths import chain, node, service
from test_recovery_jobs import setup


def restriction(code, operation):
    return CaseAirportRestriction(airport_code=code, operation=operation, report_id="OFFLINE-REPORT",
        received_at=utcnow().isoformat(), description="Offline operator report fixture")


def set_restriction(world, code, operation):
    world.incidents[0].unavailable_airport_code = None
    world.incidents[0].airport_restrictions = [restriction(code, operation)]


def weather(*, arrivals=True, departures=True):
    return WeatherDefinition(condition="fog", visibility_m=None, wind_kts=None,
        landing_allowed=arrivals, departure_allowed=departures)


@pytest.fixture
def connected(setup):
    world = setup[1].read()
    group = world.groups[0]
    group.origin = "SBZ"
    world.incidents[0].unavailable_airport_code = None
    node(world, "CLJ", "airport")
    epoch = datetime.fromisoformat(world.epoch)
    at = lambda minute: (epoch + timedelta(minutes=group.ready_at + minute)).isoformat()
    flight = Resource(id="CONNECTED-FLIGHT", name="Offline sourced connecting itinerary", kind="flight",
        origin="SBZ", destination="OTP", departure=group.ready_at + 30, arrival=group.ready_at + 150,
        capacity=2, cost_per_person=100, eligible_group_ids=["G1", "G2"],
        source="offline-flight-fixture", valid_until=(utcnow() + timedelta(hours=1)).isoformat(),
        provenance={"segments": [
            {"origin": "SBZ", "destination": "CLJ", "departure_at": at(30), "arrival_at": at(60), "technical_stop_count": 0, "stops": []},
            {"origin": "CLJ", "destination": "OTP", "departure_at": at(90), "arrival_at": at(150), "technical_stop_count": 0, "stops": []},
        ]})
    world.resources.append(flight)
    return world, group, flight


@pytest.mark.parametrize(("code", "operation", "blocked"), [
    ("SBZ", "departures", True), ("SBZ", "arrivals", False),
    ("OTP", "arrivals", True), ("OTP", "departures", False),
    ("CLJ", "arrivals", True), ("CLJ", "departures", True),
])
def test_every_marketed_segment_obeys_its_case_operation_role(connected, code, operation, blocked):
    world, group, flight = connected
    set_restriction(world, code, operation)
    assert resource_blocked_by_incident(world, flight, group) is blocked
    assert resource_feasible(world, flight, group, group.origin, group.ready_at, set()) is not blocked
    plan = build_candidate(world, Strategy(), scope_group_ids={group.id})
    assert plan.validation["valid"]
    assert plan.recovered_passengers == (0 if blocked else group.size)


def test_intermediate_icao_alias_is_enforced_without_a_world_airport_node(connected):
    world, group, flight = connected
    world.airports = [item for item in world.airports if item.id != "CLJ"]
    flight.provenance["segments"][0]["destination"] = "LRCL"
    flight.provenance["segments"][1]["origin"] = "LRCL"
    set_restriction(world, "CLJ", "departures")
    assert case_airport_operation_blocked(world, group, "LRCL", "departures")
    assert resource_blocked_by_incident(world, flight, group)
    assert "CLJ" in _resource_locations(flight)


def test_case_ban_does_not_change_other_case_or_global_airport_state(connected):
    world, group, flight = connected
    other = world.groups[1]
    other.origin = "SBZ"
    other.incident_id = "OTHER-CASE"
    second = world.incidents[0].model_copy(deep=True, update={"id": "OTHER-CASE", "group_ids": [other.id]})
    world.incidents[0].group_ids = [group.id]
    world.incidents.append(second)
    set_restriction(world, "CLJ", "arrivals")
    airport_state = [item.model_dump() for item in world.airports]
    assert resource_blocked_by_incident(world, flight, group)
    assert not resource_blocked_by_incident(world, flight, other)
    assert resource_feasible(world, flight, other, other.origin, other.ready_at, set())
    assert [item.model_dump() for item in world.airports] == airport_state


def test_legacy_ban_remains_both_directions_with_alias_normalization(connected):
    world, group, flight = connected
    world.incidents[0].unavailable_airport_code = "LRCL"
    assert case_airport_operation_blocked(world, group, "CLJ", "arrivals")
    assert case_airport_operation_blocked(world, group, "CLJ", "departures")
    assert resource_blocked_by_incident(world, flight, group)


@pytest.mark.parametrize(("code", "operation", "searchable"), [
    ("SBZ", "arrivals", True), ("SBZ", "departures", False),
    ("OTP", "departures", True), ("OTP", "arrivals", False),
])
def test_query_origin_uses_departures_and_destination_uses_arrivals(connected, code, operation, searchable):
    world, group, _ = connected
    set_restriction(world, code, operation)
    found, gaps = flight_query_options(world, [group])
    assert bool(found) is searchable
    if searchable:
        assert all(item["query"]["origin_code"] == "SBZ" and item["query"]["destination_code"] == "OTP" for item in found)
    else:
        assert any(item["code"] == "physical_route_missing" for item in gaps)


def test_arrival_ban_and_airfield_closure_do_not_erase_sourced_ground_suffix(chain):
    world, group = chain
    set_restriction(world, "OTP", "arrivals")
    otp = next(item for item in world.airports if item.id == "OTP")
    otp.available = False
    otp.operational_status = "closed"
    found, _ = flight_query_options(world, [group])
    assert len(found) == 1 and found[0]["query"]["destination_code"] == "CRA"
    service(world, "ALTERNATE-FLIGHT", "SBZ", "CRA", 30, 170, 100, kind="flight", source="offline-flight-fixture")
    plan = build_candidate(world, Strategy(), scope_group_ids={group.id})
    assert plan.validation["valid"] and plan.recovered_passengers == group.size
    assert [leg.kind for leg in plan.allocations[0].itinerary.legs] == ["flight", "bus", "bus"]


@pytest.mark.parametrize("stops", [None, False, [], {}, "", "   ", 0])
def test_empty_technical_stop_metadata_is_not_an_invented_stop(connected, stops):
    world, group, flight = connected
    set_restriction(world, "LHR", "arrivals")
    flight.provenance["segments"][0].update(technical_stop_count=0, stops=stops,
        incidental_terms={"currency": "RON", "description": "stopsfalse LHR"})
    assert not resource_blocked_by_incident(world, flight, group)
    assert resource_feasible(world, flight, group, group.origin, group.ready_at, set())


@pytest.mark.parametrize(("count", "stops"), [(1, None), (None, [{"opaque": "airport not normalized"}]), (0, "unstructured technical stop")])
def test_unknown_declared_technical_stops_fail_closed_only_for_active_case_constraints(connected, count, stops):
    world, group, flight = connected
    flight.provenance["segments"][0].update(technical_stop_count=count, stops=stops)
    assert flight_airport_operations(flight).unresolved_technical_stops
    assert not resource_blocked_by_incident(world, flight, group)
    set_restriction(world, "LHR", "departures")
    assert resource_blocked_by_incident(world, flight, group)
    assert not resource_feasible(world, flight, group, group.origin, group.ready_at, set())


@pytest.mark.parametrize("change", ["negative_count", "bool_count", "text_count", "unknown_airport", "disconnected", "malformed_segment", "empty_segments", "unsupported_stop_scalar"])
def test_malformed_source_route_is_unusable_even_without_operator_bans(connected, change):
    world, group, flight = connected
    segments = flight.provenance["segments"]
    if change.endswith("count"):
        segments[0]["technical_stop_count"] = {"negative_count": -1, "bool_count": True, "text_count": "1"}[change]
    elif change == "unknown_airport":
        segments[0]["destination"] = "NOT-AN-AIRPORT"
    elif change == "disconnected":
        segments[1]["origin"] = "LHR"
    elif change == "malformed_segment":
        segments[0] = "opaque route"
    elif change == "empty_segments":
        flight.provenance["segments"] = []
    else:
        segments[0]["stops"] = True
    assert not flight_airport_operations(flight).valid
    assert not resource_feasible(world, flight, group, group.origin, group.ready_at, set())


@pytest.mark.parametrize(("code", "arrivals", "departures", "allowed"), [
    ("SBZ", False, True, True), ("OTP", True, False, True),
    ("CLJ", False, True, False), ("CLJ", True, False, False),
])
def test_clearing_case_rule_preserves_known_global_weather_operation_roles(connected, code, arrivals, departures, allowed):
    world, group, flight = connected
    set_restriction(world, code, "arrivals")
    next(item for item in world.airports if item.id == code).weather = weather(arrivals=arrivals, departures=departures)
    world.incidents[0].airport_restrictions = []
    assert not resource_blocked_by_incident(world, flight, group)
    assert resource_feasible(world, flight, group, group.origin, group.ready_at, set()) is allowed


@pytest.mark.parametrize("global_rule", ["closed", "arrivals", "departures"])
@pytest.mark.parametrize("opaque_details", [False, True])
def test_clearing_case_cannot_bypass_global_ban_with_an_unidentified_technical_stop(connected, global_rule, opaque_details):
    world, group, flight = connected
    node(world, "LHR", "airport")
    airport = next(item for item in world.airports if item.id == "LHR")
    if global_rule == "closed":
        airport.operational_status = "closed"
    else:
        airport.weather = weather(arrivals=global_rule != "arrivals", departures=global_rule != "departures")
    flight.provenance["segments"][0].update(
        technical_stop_count=0 if opaque_details else 1,
        stops=[{"unstructured": "technical stop location unavailable"}] if opaque_details else [])
    set_restriction(world, "LHR", "arrivals")
    assert resource_blocked_by_incident(world, flight, group)
    world.incidents[0].airport_restrictions = []
    assert not resource_blocked_by_incident(world, flight, group)
    assert not resource_feasible(world, flight, group, group.origin, group.ready_at, set())
    # A route proven to have no technical stops cannot touch the unrelated ban.
    flight.provenance["segments"][0].update(technical_stop_count=0, stops=[])
    assert resource_feasible(world, flight, group, group.origin, group.ready_at, set())


@pytest.mark.parametrize("clear_weather", [False, True])
def test_catalog_only_node_is_not_a_global_ban_for_opaque_technical_stops(connected, clear_weather):
    world, group, flight = connected
    node(world, "LHR", "airport")
    airport = next(item for item in world.airports if item.id == "LHR")
    airport.available, airport.operational_status = False, "unknown"
    airport.weather = weather() if clear_weather else None
    flight.provenance["segments"][0].update(technical_stop_count=1)
    before = airport.model_dump()
    assert resource_feasible(world, flight, group, group.origin, group.ready_at, set())
    assert airport.model_dump() == before


def test_query_does_not_override_a_source_global_departure_restriction(connected):
    world, group, _ = connected
    node = next(item for item in world.airports if item.id == "SBZ")
    node.weather = weather(arrivals=True, departures=False)
    assert flight_query_options(world, [group])[0] == []


@pytest.mark.parametrize(("status", "available", "departures", "searchable"), [
    ("unknown", False, None, True), ("unknown", False, True, True),
    ("unknown", False, False, False), ("open", False, None, False),
    ("closed", True, None, False),
])
def test_catalog_discovery_is_distinct_from_sourced_flight_permission(connected, status, available, departures, searchable):
    world, group, flight = connected
    airport = next(item for item in world.airports if item.id == "SBZ")
    airport.available, airport.operational_status = available, status
    airport.weather = None if departures is None else weather(departures=departures)
    before = airport.model_dump()
    assert bool(flight_query_options(world, [group])[0]) is searchable
    assert not resource_feasible(world, flight, group, group.origin, group.ready_at, set())
    assert airport.model_dump() == before


def test_inventory_pins_new_restriction_codes_and_legacy_canonical_identity(connected):
    world, _, _ = connected
    world.incidents[0].airport_restrictions = [restriction("LHR", "arrivals")]
    world.incidents[0].unavailable_airport_code = "LRCL"
    _, locations = protected_inventory(world)
    assert {"LHR", "LRCL", "CLJ"} <= locations


@pytest.mark.parametrize(("code", "operation", "blocked"), [
    ("LHR", "arrivals", False), ("LHR", "departures", True),
    ("OTP", "departures", False), ("OTP", "arrivals", True),
])
def test_hotel_preflight_uses_only_actual_onward_flight_operation_roles(hotel_command_setup, code, operation, blocked):
    _, store, _, _, _, inputs = hotel_command_setup
    world = store.read()
    set_restriction(world, code, operation)
    request = RecoveryJobRequest(expected_version=world.version, idempotency_key="directional-hotel-fixture",
        group_ids=["G1"], modes=["flight", "hotel", "bus"], max_provider_calls=10, hotel_recovery=inputs)
    if blocked:
        with pytest.raises(DomainError) as error:
            validate_hotel_job(world, request)
        assert error.value.status == 422
    else:
        validate_hotel_job(world, request)


def test_hotel_preflight_can_source_unknown_catalog_endpoint_without_promoting_it(hotel_command_setup):
    _, store, _, _, _, inputs = hotel_command_setup
    world = store.read()
    airport = next(item for item in world.airports if item.id == inputs.onward_airport_code)
    airport.available, airport.operational_status = False, "unknown"
    before = airport.model_dump()
    request = RecoveryJobRequest(expected_version=world.version, idempotency_key="catalog-only-hotel-fixture",
        group_ids=["G1"], modes=["flight", "hotel", "bus"], max_provider_calls=10, hotel_recovery=inputs)
    validate_hotel_job(world, request)
    assert airport.model_dump() == before
