"""Bounded authoring payloads shared by the AI parser and scenario API.

Every field is required (nullable only when explicitly declared), so the same
contracts work with strict structured output. These describe simulated inventory,
never observations from an external provider.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


HORIZON_MINUTES = 7 * 24 * 60
MAX_AIRPORTS = 32
MAX_RESOURCES = 150
MAX_GROUPS = 250
MAX_PASSENGERS = 1500
MAX_COMMAND_EVENTS = 64


class ScenarioPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class AirportDefinition(ScenarioPayload):
    name: str = Field(min_length=1, max_length=140)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    diversion_eligible: bool


class ResourceDefinition(ScenarioPayload):
    kind: Literal["bus", "flight", "hotel"]
    name: str = Field(min_length=1, max_length=140)
    origin: str = Field(min_length=1, max_length=40)
    destination: str = Field(min_length=1, max_length=40)
    departure: int = Field(ge=0, lt=HORIZON_MINUTES)
    arrival: int = Field(gt=0, le=HORIZON_MINUTES)
    capacity: int = Field(ge=0, le=10000)
    accessible: bool
    cost_per_person: float = Field(ge=0, le=100000)
    room_capacity: int = Field(ge=1, le=10)
    vehicle_id: str | None = Field(min_length=1, max_length=40)
    available: bool


class PassengerBatchDefinition(ScenarioPayload):
    name: str = Field(min_length=1, max_length=100)
    passenger_count: int = Field(ge=1, le=MAX_PASSENGERS)
    group_size: int = Field(ge=1, le=12)
    origin: str = Field(min_length=1, max_length=40)
    destination: str = Field(min_length=1, max_length=40)
    ready_at: int = Field(ge=0, lt=HORIZON_MINUTES)
    deadline: int = Field(gt=0, le=HORIZON_MINUTES)
    accessible: bool
    budget_per_person: float = Field(ge=0, le=100000)
    original_flight_id: str | None = Field(min_length=1, max_length=40)
    priority: int = Field(ge=1, le=10)


class ResourceSchedule(ScenarioPayload):
    departure: int = Field(ge=0, lt=HORIZON_MINUTES)
    arrival: int = Field(gt=0, le=HORIZON_MINUTES)


class ResourceRoute(ScenarioPayload):
    origin: str = Field(min_length=1, max_length=40)
    destination: str = Field(min_length=1, max_length=40)


STRUCTURED_PAYLOADS = {
    "airport_added": AirportDefinition,
    "resource_added": ResourceDefinition,
    "passengers_added": PassengerBatchDefinition,
    "resource_schedule": ResourceSchedule,
    "resource_route": ResourceRoute,
}
