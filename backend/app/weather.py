"""Explicit synthetic airport weather constraints shared by authoring and solving.

Measurements describe the scenario. They never imply undocumented aviation
thresholds; landing/departure flags are the operational inputs to the simulator.
Airport closure and weather are independent, so clearing weather cannot reopen a
closed airport or revive a flight explicitly cancelled by the operator.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Airport, PassengerGroup, Resource, World


def landing_allowed(airport: Airport) -> bool:
    return airport.available and (airport.weather is None or airport.weather.landing_allowed)


def departure_allowed(airport: Airport) -> bool:
    return airport.available and (airport.weather is None or airport.weather.departure_allowed)


def passenger_arrival_pending(world: World, group: PassengerGroup) -> bool:
    return any(
        resource.id == group.original_flight_id
        and (resource.status == "needs_diversion" or (
            resource.status == "weather_held" and group.origin == resource.destination
        ))
        for resource in world.resources
    )


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
