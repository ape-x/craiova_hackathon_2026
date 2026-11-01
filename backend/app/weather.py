"""Explicit synthetic airport weather constraints shared by authoring and solving.

Measurements describe the scenario. They never imply undocumented aviation
thresholds; landing/departure flags are the operational inputs to the simulator.
Airport closure and weather are independent, so clearing weather cannot reopen a
closed airport or revive a flight explicitly cancelled by the operator.
"""
from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Airport, PassengerGroup, Resource, World


def landing_allowed(airport: Airport) -> bool:
    return airport.available and (airport.weather is None or airport.weather.landing_allowed)


def departure_allowed(airport: Airport) -> bool:
    return airport.available and (airport.weather is None or airport.weather.departure_allowed)


def passenger_arrival_pending(world: World, group: PassengerGroup) -> bool:
    pending = any(
        resource.id == group.original_flight_id
        and (resource.status == "needs_diversion" or (
            resource.status == "weather_held" and group.origin == resource.destination
        ))
        for resource in world.resources
    )
    return pending and not current_operator_arrival(world, group)


def current_operator_arrival(world: World, group: PassengerGroup) -> bool:
    """Resolve a passenger-location hold, never an aircraft or airport restriction.

    Only the latest explicitly recorded operator arrival can supersede a stale
    original-flight status. It is bound to this world, case, group and readiness;
    a scheduled future flight, arbitrary origin edit, or old report cannot do so.
    """
    from .airport_catalog import lookup_airport
    from .pricing import aware_timestamp

    history = group.provenance.get("arrival_reports")
    if world.mode != "live" or not isinstance(history, list) or not history:
        return False
    try:
        if any(not isinstance(entry, dict) or not isinstance(entry.get("id"), str) for entry in history):
            return False
        if len({entry["id"] for entry in history}) != len(history):
            return False
        received = [aware_timestamp(entry.get("received_at")) for entry in history]
        if received != sorted(received):
            return False
        entry = history[-1]
        case = next((case for case in world.incidents if case.id == group.incident_id), None)
        airport = next((airport for airport in world.airports if airport.id == group.origin), None)
        known = lookup_airport(group.origin)
        return bool(
            entry.get("source") == "operator_report" and entry.get("type") == "arrival_report"
            and re.fullmatch(r"opr_[0-9a-f]{24}", entry["id"])
            and entry.get("scenario_id") == world.scenario_id and entry.get("epoch") == world.epoch
            and entry.get("original_flight_id") == group.original_flight_id
            and "original_flight_id" in entry
            and entry.get("group_id") == group.id and entry.get("incident_id") == group.incident_id
            and case is not None and group.id in case.group_ids
            and entry.get("airport_code") == group.origin
            and airport is not None and airport.kind == "airport" and airport.code == group.origin
            and known is not None and known["code"] == group.origin
            and math.ceil((aware_timestamp(entry.get("ready_at")) - aware_timestamp(world.epoch)).total_seconds()/60) == group.ready_at
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def flight_weather_blocked(world: World, resource: Resource) -> bool:
    airports = {airport.id: airport for airport in world.airports}
    origin, destination = airports[resource.origin], airports[resource.destination]
    airborne = resource.departure <= world.sim_time < resource.arrival
    return bool(
        (destination.weather is not None and not destination.weather.landing_allowed)
        or (not airborne and origin.weather is not None and not origin.weather.departure_allowed)
    )


def apply_weather_to_flight(world: World, resource: Resource) -> bool:
    """Return whether the operational state changed; preserve explicit failures."""
    if resource.kind != "flight" or resource.arrival <= world.sim_time:
        return False
    before = resource.available, resource.status
    airborne = resource.departure <= world.sim_time < resource.arrival
    if flight_weather_blocked(world, resource):
        if resource.available or resource.status in ("weather_held", "needs_diversion"):
            resource.available = False
            resource.status = "needs_diversion" if airborne else "weather_held"
    elif resource.status == "weather_held":
        airports = {airport.id: airport for airport in world.airports}
        if departure_allowed(airports[resource.origin]) and landing_allowed(airports[resource.destination]):
            resource.available = True
            resource.status = "scheduled"
    return before != (resource.available, resource.status)
