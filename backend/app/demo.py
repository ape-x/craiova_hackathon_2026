"""Fictional recovery demand evaluated only against real integrated supply."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import secrets

from pydantic import Field, field_validator, model_validator

from .airport_catalog import lookup_airport
from .models import Model, RecoveryIncident, World
from .scenario_schema import MAX_AIRPORTS, MAX_RESOURCES


class DemoCaseDefinition(Model):
    origin: str = "LHR"
    destination: str = "OTP"
    passengers: int = Field(default=2, ge=1, le=500, strict=True)
    group_size: int = Field(default=2, ge=1, le=12, strict=True)
    group_budget_eur: Decimal = Decimal("2000")
    ready_at: datetime
    deadline: datetime
    description: str = Field(default="Two fictional adults need alternative travel.", min_length=3, max_length=1800)

    @field_validator("origin", "destination")
    @classmethod
    def catalog_airport(cls, value):
        airport = lookup_airport(value.strip().upper())
        if airport is None:
            raise ValueError("Choose an airport from the reference catalog")
        return airport["code"]

    @field_validator("ready_at", "deadline", mode="before")
    @classmethod
    def utc_timestamp(cls, value):
        from .live_data import aware
        return aware(value).astimezone(timezone.utc)

    @field_validator("group_budget_eur", mode="before")
    @classmethod
    def group_budget(cls, value):
        from .live_data import amount
        return amount(value)

    @field_validator("description")
    @classmethod
    def useful_description(cls, value):
        value = value.strip()
        if len(value) < 3 or any(ord(char) < 32 for char in value):
            raise ValueError("Describe the hypothetical incident without control characters")
        return value

    @model_validator(mode="after")
    def valid_route(self):
        if self.origin == self.destination or self.deadline <= self.ready_at:
            raise ValueError("Choose distinct airports and an arrival deadline after readiness")
        return self


def build_demo_case(world: World, definition: DemoCaseDefinition) -> dict:
    """Append explicitly fictional demand; never invent or modify supply."""
    from .live_data import PassengerGenerationInput, prepare_generated_passengers
    from .store import DomainError, audit, utcnow

    if not world.demo or world.mode != "live":
        raise DomainError(409, "Fictional recovery cases require demo mode")
    definition = DemoCaseDefinition.model_validate(
        definition.model_dump(include=set(DemoCaseDefinition.model_fields)))
    if definition.ready_at <= utcnow():
        raise DomainError(422, "Demo readiness must be in the future")
    if len(world.incidents) >= 100:
        raise DomainError(422, "Recovery incident limit reached")
    new_codes = {definition.origin, definition.destination} - {item.id for item in world.airports}
    if len(world.airports) + len(new_codes) > MAX_AIRPORTS or len(world.resources) > MAX_RESOURCES:
        raise DomainError(422, "Operation inventory capacity reached; no existing supply was changed")

    # This is a case reference, deliberately not an invented airline flight.
    used_ids = {item.id for collection in (world.incidents, world.airports, world.resources, world.groups) for item in collection}
    incident_id = "DEMO-" + secrets.token_hex(8).upper()
    while incident_id in used_ids:
        incident_id = "DEMO-" + secrets.token_hex(8).upper()
    request = PassengerGenerationInput(
        expected_version=world.version, idempotency_key="demo-passenger-preparation",
        passengers=definition.passengers, group_size=definition.group_size,
        origin=definition.origin, destination=definition.destination,
        original_flight_id=incident_id, ready_at=definition.ready_at, deadline=definition.deadline,
        group_budget_eur=definition.group_budget_eur, accessible=False, priority=1, is_adult=True)
    additions = prepare_generated_passengers(world, request)
    groups = additions["groups"]
    for index, group in enumerate(groups, 1):
        group.name = f"Demo {len(world.incidents) + 1} · group {index}"
        group.members = [f"Fictional adult {index}-{number + 1}" for number in range(group.size)]
        group.incident_id = incident_id
        group.provenance.update({"source": "demo_scenario", "fictional": True,
            "incident_reference": incident_id, "age_assumption": "all_adults"})
    incident = RecoveryIncident(
        id=incident_id, created_at=utcnow().isoformat(), source="demo_scenario",
        kind="flight_disruption", description="DEMO — hypothetical incident. " + definition.description,
        requested_flight_number=incident_id, date_utc=definition.ready_at.date().isoformat(),
        flight_ref=None, flight=None, flight_context_state="not_applicable",
        group_ids=[group.id for group in groups], status="open")

    # All validation and construction finish before changing the operation.
    world.airports.extend(additions["airports"])
    world.groups.extend(groups)
    world.incidents.append(incident)
    world.budget += sum(group.budget for group in groups)
    audit(world, "demo_case_created",
        "Fictional incident and adult passenger requirements created. All travel supply must come from integrated sources.",
        incident.id, {"source": "demo_scenario", "group_ids": incident.group_ids})
    return {"incident_id": incident.id, "group_ids": incident.group_ids}
