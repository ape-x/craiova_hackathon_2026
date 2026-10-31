"""Server-owned construction and reference checks for simulated scenario data."""
from __future__ import annotations

import math
import re
import secrets

from .airport_catalog import lookup_airport
from .models import Airport, PassengerGroup, Resource, World, canonical_id
from .scenario_schema import (
    AirportDefinition, PassengerBatchDefinition, ResourceDefinition,
    HORIZON_MINUTES, MAX_AIRPORTS, MAX_GROUPS, MAX_PASSENGERS, MAX_RESOURCES,
)
from .store import DomainError
from .weather import apply_weather_to_flight


def identifier(value: str | None) -> str:
    result=canonical_id(value)
    if not result or not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,39}",result):
        raise DomainError(422,"Entity IDs must contain 1–40 letters, digits, underscores or hyphens")
    return result


def airport_reference(world: World, value: str) -> Airport:
    target=identifier(value)
    found=next((a for a in world.airports if a.id==target or a.code==target),None)
    if found is None:
        metadata=lookup_airport(target)
        found=next((a for a in world.airports if metadata and a.id==metadata["id"]),None)
    if found is None:
        raise DomainError(404,f"Unknown scenario airport: {target}; add it before referencing it")
    return found


def unused_identifier(world: World, value: str) -> str:
    target=identifier(value)
    if any(item.id==target for items in (world.airports,world.resources,world.groups) for item in items):
        raise DomainError(409,f"Entity ID already exists: {target}")
    return target


def validate_schedule(world: World, departure: int, arrival: int, *, allow_airborne=False):
    if not 0<=departure<arrival<=HORIZON_MINUTES:
        raise DomainError(422,"Schedule must have departure before arrival within the seven-day simulation horizon")
    if arrival<=world.sim_time or (departure<world.sim_time and not allow_airborne):
        raise DomainError(422,"Schedule cannot create a completed service or a ground service in the past")


def validate_route(world: World, kind: str, origin: str, destination: str, *, available=True):
    start=airport_reference(world,origin)
    end=airport_reference(world,destination)
    if (start.id==end.id)!=(kind=="hotel"):
        raise DomainError(422,"Hotels must stay at one airport; transport must connect two different airports")
    if kind=="flight" and available and (not start.available or not end.available):
        raise DomainError(409,"An available flight cannot use a closed airport")
    return start.id,end.id


def add_airport(world: World, target: str, value: dict | None) -> Airport:
    if len(world.airports)>=MAX_AIRPORTS:
        raise DomainError(422,f"Scenario limit is {MAX_AIRPORTS} airports")
    if value is None:
        metadata=lookup_airport(identifier(target))
        if metadata is None:
            raise DomainError(422,"Airport not found in the reference catalog; provide an explicit name and coordinates for a custom simulated airport")
        code=unused_identifier(world,metadata["id"])
        airport=Airport(id=code,code=code,name=metadata["name"],lat=metadata["lat"],lon=metadata["lon"],available=True,diversion_eligible=True)
    else:
        definition=AirportDefinition.model_validate(value)
        code=unused_identifier(world,target)
        if not re.fullmatch(r"[A-Z0-9]{3,8}",code):
            raise DomainError(422,"Custom airport codes must contain 3–8 letters or digits")
        airport=Airport(id=code,code=code,**definition.model_dump(),available=True)
    world.airports.append(airport)
    return airport


def add_resource(world: World, target: str, value: dict) -> Resource:
    if len(world.resources)>=MAX_RESOURCES:
        raise DomainError(422,f"Scenario limit is {MAX_RESOURCES} resources")
    definition=ResourceDefinition.model_validate(value)
    target=unused_identifier(world,target)
    origin,destination=validate_route(world,definition.kind,definition.origin,definition.destination,available=definition.available)
    validate_schedule(world,definition.departure,definition.arrival,allow_airborne=definition.kind=="flight")
    if definition.kind=="hotel" and definition.vehicle_id is not None:
        raise DomainError(422,"A hotel cannot be assigned a transport vehicle")
    vehicle=identifier(definition.vehicle_id) if definition.vehicle_id is not None else (target if definition.kind=="bus" else None)
    resource=Resource(id=target,**{**definition.model_dump(),"origin":origin,"destination":destination,"vehicle_id":vehicle},
        source="synthetic",status="scheduled" if definition.available else "cancelled" if definition.kind=="flight" else "unavailable")
    apply_weather_to_flight(world,resource)
    world.resources.append(resource)
    return resource


def add_passengers(world: World, value: dict) -> list[PassengerGroup]:
    definition=PassengerBatchDefinition.model_validate(value)
    count=math.ceil(definition.passenger_count/definition.group_size)
    if len(world.groups)+count>MAX_GROUPS or sum(g.size for g in world.groups)+definition.passenger_count>MAX_PASSENGERS:
        raise DomainError(422,f"Scenario limit is {MAX_GROUPS} groups and {MAX_PASSENGERS} passengers")
    origin=airport_reference(world,definition.origin).id
    destination=airport_reference(world,definition.destination).id
    if not world.sim_time<=definition.ready_at<=definition.deadline:
        raise DomainError(422,"Passenger readiness must be at or after simulation time and no later than the deadline")
    flight_id=""
    flight=None
    if definition.original_flight_id is not None:
        flight_id=identifier(definition.original_flight_id)
        flight=next((r for r in world.resources if r.id==flight_id and r.kind=="flight"),None)
        if flight is None:
            raise DomainError(404,f"Unknown original flight: {flight_id}")
        if sum(g.size for g in world.groups if g.original_flight_id==flight_id)+definition.passenger_count>flight.capacity:
            raise DomainError(409,"Passenger manifest would exceed the original flight capacity")
        if flight.departure<=world.sim_time<flight.arrival and (flight.available or flight.status=="needs_diversion"):
            if origin!=flight.destination or definition.ready_at<flight.arrival+30:
                raise DomainError(422,"Airborne passengers must start at the arrival airport with at least 30 minutes after landing")
        elif origin not in (flight.origin,flight.destination):
            raise DomainError(422,"Passenger origin must match the original flight departure or arrival airport")
        elif origin==flight.destination and (flight.available or flight.status=="weather_held") and definition.ready_at<flight.arrival+30:
            raise DomainError(422,"Incoming passengers must be ready at least 30 minutes after their original flight lands")
    used={item.id for items in (world.airports,world.resources,world.groups) for item in items}
    remaining=definition.passenger_count
    created=[]
    index=1
    while remaining:
        while f"G{index:03}" in used:
            index+=1
        group_id=f"G{index:03}"
        used.add(group_id)
        size=min(remaining,definition.group_size)
        created.append(PassengerGroup(id=group_id,name=f"{definition.name} · {len(created)+1:02}",size=size,
            members=[f"Synthetic passenger {group_id}-{member+1}" for member in range(size)],
            origin=origin,destination=destination,ready_at=definition.ready_at,deadline=definition.deadline,
            accessible=definition.accessible,budget=round(size*definition.budget_per_person,2),
            original_flight_id=flight_id,priority=definition.priority,
            status="affected" if flight and flight.status in ("needs_diversion","weather_held") else "scheduled" if origin==destination else "waiting",
            token=secrets.token_urlsafe(32)))
        remaining-=size
        index+=1
    world.groups.extend(created)
    return created
