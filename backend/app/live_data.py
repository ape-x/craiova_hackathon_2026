"""Validate real operator evidence before applying any operational mutation.

This module performs no network I/O and never creates demonstration passengers,
capacities or prices. Helpers return complete validated additions for the caller
to apply in its existing versioned transaction.
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
import re
import secrets
from collections import OrderedDict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .airport_catalog import lookup_airport
from .models import Airport, Mutation, PassengerGroup, Resource, World
from .scenario_schema import MAX_AIRPORTS, MAX_GROUPS, MAX_PASSENGERS, MAX_RESOURCES
from .planning_window import planning_horizon_end
from .store import DomainError


MAX_MANIFEST_BYTES = 500_000
MANIFEST_COLUMNS = (
    "passenger_id", "group_id", "name", "origin", "destination",
    "original_flight_id", "ready_at", "deadline", "group_budget_eur", "accessible", "priority", "is_adult",
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: Any) -> datetime:
    """Preserve the submitted instant; never guess a local timezone."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Use an ISO timestamp including its timezone offset") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware timestamp is required")
    return value


def identifier(value: str) -> str:
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,39}", value):
        raise ValueError("Identifiers require 1–40 letters, digits, underscores or hyphens")
    return value


def amount(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("A non-negative EUR amount is required")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("A non-negative EUR amount is required") from exc
    if not result.is_finite() or not 0 <= result <= 1_000_000 or result != result.quantize(Decimal("0.01")):
        raise ValueError("EUR amounts require at most two decimal places and must not exceed 1000000")
    return result


class EvidenceInput(Mutation):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, allow_inf_nan=False)
    source_name: str = Field(min_length=1, max_length=200)
    source_reference: str = Field(min_length=1, max_length=1000)
    confirmed_by: str = Field(min_length=1, max_length=200)
    observed_at: datetime

    @field_validator("source_name", "source_reference", "confirmed_by")
    @classmethod
    def useful_text(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("Evidence must include a meaningful source and confirmation reference")
        return value

    @field_validator("observed_at", mode="before")
    @classmethod
    def observed_timestamp(cls, value):
        return aware(value)


class ManifestImport(EvidenceInput):
    csv_text: str = Field(min_length=1, max_length=MAX_MANIFEST_BYTES)


class ManualResourceInput(EvidenceInput):
    id: str
    kind: Literal["bus", "flight"]
    name: str = Field(min_length=1, max_length=140)
    origin: str
    destination: str
    departure_at: datetime
    arrival_at: datetime
    capacity: int = Field(ge=0, le=10000, strict=True)
    unit_price_eur: Decimal
    accessible: bool = Field(strict=True)
    vehicle_id: str | None = None
    valid_until: datetime

    @field_validator("id", "vehicle_id")
    @classmethod
    def valid_identifier(cls, value):
        return identifier(value) if value is not None else value

    @field_validator("origin", "destination")
    @classmethod
    def airport_code(cls, value):
        # Station nodes can be supplied for real first/last-mile transfers.
        # Their existence and transport kind are checked against the world.
        return identifier(value)

    @field_validator("name")
    @classmethod
    def service_name(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("A resource name is required")
        return value

    @field_validator("departure_at", "arrival_at", "valid_until", mode="before")
    @classmethod
    def schedule_timestamp(cls, value):
        return aware(value)

    @field_validator("unit_price_eur", mode="before")
    @classmethod
    def price(cls, value):
        return amount(value)

    @model_validator(mode="after")
    def valid_service(self):
        if self.arrival_at <= self.departure_at:
            raise ValueError("Arrival or checkout must follow departure or check-in")
        if self.origin == self.destination:
            raise ValueError("Transport connects different airports")
        if self.kind == "bus" and self.vehicle_id is None:
            raise ValueError("An operator-confirmed bus requires its vehicle identifier")
        if self.valid_until <= self.observed_at:
            raise ValueError("Inventory validity must extend beyond its observation")
        return self


class PassengerGenerationInput(Mutation):
    """Explicit passenger planning assumptions; never creates transport inventory."""
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, allow_inf_nan=False)
    passengers: int = Field(ge=1, le=500, strict=True)
    group_size: int = Field(ge=1, le=12, strict=True)
    origin: str
    destination: str
    original_flight_id: str
    ready_at: datetime
    deadline: datetime
    group_budget_eur: Decimal
    accessible: bool = Field(strict=True)
    priority: int = Field(ge=1, le=10, strict=True)
    is_adult: bool = Field(strict=True)

    @field_validator("original_flight_id")
    @classmethod
    def valid_identifier(cls, value):
        return identifier(value)

    @field_validator("origin", "destination")
    @classmethod
    def airport_code(cls, value):
        return _known_airport_code(value)

    @field_validator("ready_at", "deadline", mode="before")
    @classmethod
    def schedule_timestamp(cls, value):
        return aware(value)

    @field_validator("group_budget_eur", mode="before")
    @classmethod
    def budget(cls, value):
        return amount(value)

    @model_validator(mode="after")
    def valid_route(self):
        if self.origin == self.destination or self.deadline <= self.ready_at:
            raise ValueError("Passengers require distinct airports and a deadline after readiness")
        return self


def relative_minute(world: World, timestamp: datetime, *, rounding: Literal["up", "down"]) -> int:
    epoch = aware(world.epoch)
    minutes = (aware(timestamp) - epoch).total_seconds() / 60
    result = math.ceil(minutes) if rounding == "up" else math.floor(minutes)
    if not 0 <= result <= planning_horizon_end(world):
        raise DomainError(422, "Timestamps must follow the operation epoch and be no more than seven days ahead of the current planning time")
    return result


def _evidence(value: EvidenceInput, *, digest: str | None = None) -> dict:
    now = utcnow()
    if (value.observed_at - now).total_seconds() > 300:
        raise DomainError(422, "Evidence timestamps cannot be in the future")
    result = {
        "source_name": value.source_name, "source_reference": value.source_reference,
        "confirmed_by": value.confirmed_by, "observed_at": value.observed_at.isoformat(),
        "imported_at": now.isoformat(),
    }
    if digest:
        result["sha256"] = digest
    return result


def _new_airports(world: World, codes: list[str]) -> list[Airport]:
    known = {airport.id for airport in world.airports}
    airports = []
    for code in dict.fromkeys(codes):
        if code in known:
            continue
        reference = lookup_airport(code)
        if reference is None:
            raise DomainError(422, f"Airport {code} is not present in the reference catalog")
        code = reference["code"]
        if code not in known:
            airports.append(Airport(id=code, code=code, name=reference["name"], lat=reference["lat"], lon=reference["lon"], available=False, operational_status="unknown"))
            known.add(code)
    if len(known) > MAX_AIRPORTS:
        raise DomainError(422, f"Operation limit is {MAX_AIRPORTS} airports")
    return airports


def _known_airport_code(value: str) -> str:
    code = identifier(value)
    reference = lookup_airport(code)
    if reference is None:
        raise ValueError("Airport is not present in the reference catalog")
    return reference["code"]


def _csv_bool(value: str) -> bool:
    if value not in ("true", "false"):
        raise ValueError("Use true or false for accessible")
    return value == "true"


def _live(world: World) -> None:
    if world.mode != "live":
        raise DomainError(409, "Real operational imports require live mode")


def prepare_manifest(world: World, value: ManifestImport) -> dict:
    """Parse one passenger per CSV row and preserve each supplied group intact."""
    _live(world)
    content = value.csv_text.lstrip("\ufeff")
    if len(content.encode("utf-8")) > MAX_MANIFEST_BYTES or "\x00" in content:
        raise DomainError(422, "Manifest exceeds the size limit or contains invalid characters")
    evidence = _evidence(value, digest=hashlib.sha256(content.encode("utf-8")).hexdigest())
    existing_ids = {item.id for items in (world.airports, world.resources, world.groups) for item in items}
    existing_passengers = {passenger for group in world.groups for passenger in group.provenance.get("passenger_ids", [])}
    by_group: OrderedDict[str, list[dict]] = OrderedDict()
    seen_passengers = set(existing_passengers)
    try:
        reader = csv.DictReader(io.StringIO(content, newline=""), strict=True)
        if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)) or set(reader.fieldnames) != set(MANIFEST_COLUMNS):
            raise DomainError(422, "Manifest headers must match the downloadable CSV template exactly")
        for number, raw in enumerate(reader, 2):
            if number > MAX_PASSENGERS + 1:
                raise DomainError(422, f"Manifest limit is {MAX_PASSENGERS} passengers")
            if None in raw or any(item is None for item in raw.values()):
                raise DomainError(422, f"Manifest row {number} has the wrong number of columns")
            row = {key: item.strip() for key, item in raw.items()}
            try:
                if any(any(ord(char) < 32 for char in item) for item in row.values()):
                    raise ValueError("Control character")
                passenger_id, group_id = identifier(row["passenger_id"]), identifier(row["group_id"])
                if passenger_id in seen_passengers:
                    raise ValueError("Duplicate passenger ID")
                if group_id in existing_ids:
                    raise ValueError("Existing group ID")
                if not row["name"] or len(row["name"]) > 200:
                    raise ValueError("Passenger name is required")
                origin, destination = _known_airport_code(row["origin"]), _known_airport_code(row["destination"])
                if origin == destination:
                    raise ValueError("Recovery needs distinct origin and destination")
                original_flight = identifier(row["original_flight_id"])
                original = next((resource for resource in world.resources if resource.id == original_flight), None)
                if original is not None and original.kind != "flight":
                    raise ValueError("Original flight ID refers to a non-flight resource")
                ready_stamp, deadline_stamp = aware(row["ready_at"]), aware(row["deadline"])
                ready = relative_minute(world, ready_stamp, rounding="up")
                deadline = relative_minute(world, deadline_stamp, rounding="down")
                if ready > deadline or deadline_stamp <= utcnow():
                    raise ValueError("Passenger deadline has passed or precedes readiness")
                priority = int(row["priority"])
                if not 1 <= priority <= 10 or str(priority) != row["priority"]:
                    raise ValueError("Priority must be an integer from 1 to 10")
                record = {
                    "passenger_id": passenger_id, "name": row["name"], "origin": origin, "destination": destination,
                    "original_flight_id": original_flight, "ready_at": ready, "deadline": deadline,
                    "ready_at_source": ready_stamp.isoformat(), "deadline_source": deadline_stamp.isoformat(),
                    "ready_timestamp": ready_stamp, "deadline_timestamp": deadline_stamp,
                    "budget": amount(row["group_budget_eur"]), "accessible": _csv_bool(row["accessible"]),
                    "priority": priority, "is_adult": _csv_bool(row["is_adult"]),
                }
            except (ValueError, TypeError, InvalidOperation) as exc:
                raise DomainError(422, f"Manifest row {number} has invalid or duplicate passenger, group, route, time, budget or eligibility data") from exc
            seen_passengers.add(passenger_id)
            by_group.setdefault(group_id, []).append(record)
    except csv.Error as exc:
        raise DomainError(422, "Manifest is not valid CSV") from exc
    if not by_group:
        raise DomainError(422, "Manifest contains no passengers")
    if len(world.groups) + len(by_group) > MAX_GROUPS or sum(group.size for group in world.groups) + sum(map(len, by_group.values())) > MAX_PASSENGERS:
        raise DomainError(422, "Manifest exceeds the operation's passenger or group limit")
    groups = []
    shared_fields = ("origin", "destination", "original_flight_id", "ready_timestamp", "deadline_timestamp", "budget", "priority")
    for group_id, records in by_group.items():
        first = records[0]
        if len(records) > 12 or any(any(row[key] != first[key] for key in shared_fields) for row in records):
            raise DomainError(422, f"Group {group_id} exceeds 12 members or contains conflicting group fields")
        provenance = {**evidence, "passenger_ids": [row["passenger_id"] for row in records], "ready_at_source": first["ready_at_source"], "deadline_source": first["deadline_source"], "group_budget_eur": str(first["budget"])}
        groups.append(PassengerGroup(
            id=group_id, name=group_id, size=len(records), members=[row["name"] for row in records],
            origin=first["origin"], destination=first["destination"], ready_at=first["ready_at"], deadline=first["deadline"],
            accessible=any(row["accessible"] for row in records), budget=float(first["budget"]),
            original_flight_id=first["original_flight_id"], priority=first["priority"], token=secrets.token_urlsafe(32),
            status="waiting", source="manifest_import", provenance=provenance, adult_count=sum(row["is_adult"] for row in records),
        ))
    airports = _new_airports(world, [code for group in groups for code in (group.origin, group.destination)])
    if {group.id for group in groups}.intersection(airport.id for airport in airports):
        raise DomainError(409, "A group ID cannot also be an airport ID")
    return {"airports": airports, "groups": groups, "provenance": evidence}


def prepare_resource(world: World, value: ManualResourceInput) -> dict:
    """Turn an operator's inventory confirmation into one bounded live resource."""
    _live(world)
    evidence = _evidence(value)
    now = utcnow()
    if value.valid_until <= now or value.departure_at < now:
        raise DomainError(422, "Inventory confirmation must be valid now and departure must not be in the past")
    if len(world.resources) >= MAX_RESOURCES:
        raise DomainError(422, f"Operation limit is {MAX_RESOURCES} resources")
    if any(item.id == value.id for items in (world.airports, world.resources, world.groups) for item in items):
        raise DomainError(409, "Resource ID already exists")
    def endpoint(code):
        known = next((node for node in world.airports if node.id == code or node.code == code), None)
        if known is not None:
            if value.kind == "flight" and getattr(known, "kind", "airport") != "airport":
                raise DomainError(422, "Flight endpoints must be airports")
            return known.id
        try:
            return _known_airport_code(code)
        except ValueError as exc:
            raise DomainError(422, "Transport endpoint is neither an existing node nor a known airport") from exc
    origin, destination = endpoint(value.origin), endpoint(value.destination)
    if origin == destination:
        raise DomainError(422, "Transport must connect different airports")
    departure = relative_minute(world, value.departure_at, rounding="down")
    arrival = relative_minute(world, value.arrival_at, rounding="up")
    if departure < world.sim_time or arrival <= departure or value.unit_price_eur > 100000:
        raise DomainError(422, "Resource schedule or price is outside the operation's supported limits")
    evidence.update({"departure_at": value.departure_at.isoformat(), "arrival_at": value.arrival_at.isoformat(), "unit_price_eur": str(value.unit_price_eur)})
    resource = Resource(
        id=value.id, name=value.name.strip(), kind=value.kind, origin=origin, destination=destination,
        departure=departure, arrival=arrival, capacity=value.capacity, cost_per_person=float(value.unit_price_eur),
        accessible=value.accessible, room_capacity=1, vehicle_id=value.vehicle_id,
        available=True, status="scheduled", source="operator_confirmed", provenance=evidence,
        valid_until=value.valid_until.isoformat(), capacity_scope="operator_confirmed",
    )
    airports = _new_airports(world, [origin, destination])
    if resource.id in {airport.id for airport in airports}:
        raise DomainError(409, "A resource ID cannot also be an airport ID")
    return {"airports": airports, "resources": [resource], "provenance": evidence}


def prepare_generated_passengers(world: World, value: PassengerGenerationInput) -> dict:
    _live(world)
    origin, destination = _known_airport_code(value.origin), _known_airport_code(value.destination)
    if origin == destination:
        raise DomainError(422, "Passengers require distinct origin and destination airports")
    ready = relative_minute(world, value.ready_at, rounding="up")
    deadline = relative_minute(world, value.deadline, rounding="down")
    if ready >= deadline or value.deadline <= utcnow():
        raise DomainError(422, "Passenger deadline must be in the future and after readiness")
    group_count = math.ceil(value.passengers / value.group_size)
    if len(world.groups) + group_count > MAX_GROUPS or sum(group.size for group in world.groups) + value.passengers > MAX_PASSENGERS:
        raise DomainError(422, "Passenger generation exceeds the operation's passenger or group limit")
    provenance = {"source": "operator_requested_test_passengers", "generated_at": utcnow().isoformat(), "ready_at_source": value.ready_at.isoformat(), "deadline_source": value.deadline.isoformat(), "group_budget_eur": str(value.group_budget_eur)}
    used = {item.id for items in (world.airports, world.resources, world.groups) for item in items}
    groups, remaining, passenger_index = [], value.passengers, 1
    while remaining:
        group_id = "TEST-" + secrets.token_hex(6).upper()
        while group_id in used:
            group_id = "TEST-" + secrets.token_hex(6).upper()
        used.add(group_id)
        size = min(value.group_size, remaining)
        groups.append(PassengerGroup(
            id=group_id, name=f"Planning group {len(groups)+1}", size=size,
            members=[f"Planning passenger {index}" for index in range(passenger_index, passenger_index + size)],
            origin=origin, destination=destination, ready_at=ready, deadline=deadline,
            accessible=value.accessible, budget=float(value.group_budget_eur), original_flight_id=value.original_flight_id,
            priority=value.priority, token=secrets.token_urlsafe(32), status="waiting", source="synthetic",
            adult_count=size if value.is_adult else 0, provenance=dict(provenance),
        ))
        remaining -= size
        passenger_index += size
    return {"airports": _new_airports(world, [origin, destination]), "groups": groups, "provenance": provenance}
