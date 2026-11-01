from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_serializer, model_validator
from .scenario_schema import STRUCTURED_PAYLOADS, WeatherDefinition
from .pricing import PriceEvidence, AplQuoteFreshness, CostEvidence
from .initial_position import InitialPositionAssumption
from .airport_arrival import AirportArrivalAssumption


def canonical_id(value: str | None) -> str | None:
    """Normalize presentation prefixes; strict lookups still reject unknown IDs."""
    if value is None:
        return None
    normalized=value.strip().upper()
    for prefix in ("RESOURCE_","RESOURCE-","BUS_","BUS-","FLIGHT_","FLIGHT-","AIRPORT_","AIRPORT-","GROUP_","GROUP-"):
        if normalized.startswith(prefix):
            return normalized[len(prefix):]
    return normalized


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)


class Airport(Model):
    id: str
    code: str
    name: str
    lat: float
    lon: float
    available: bool = True
    diversion_eligible: bool = True
    weather: WeatherDefinition | None = None
    operational_status: Literal["unknown", "open", "closed"] = "unknown"
    kind: Literal["airport", "station", "hotel", "meeting_point"] = "airport"


class Resource(Model):
    id: str
    kind: Literal["bus", "flight", "hotel"]
    name: str
    origin: str
    destination: str
    departure: int = Field(ge=0)
    arrival: int = Field(gt=0)
    capacity: int = Field(ge=0, le=10000)
    accessible: bool = True
    available: bool = True
    cost_per_person: float = Field(ge=0, le=100000)
    room_capacity: int = Field(default=2, ge=1, le=10)
    vehicle_id: str | None = None
    status: str = "scheduled"
    source: str = "synthetic"
    provenance: dict[str, Any] = Field(default_factory=dict)
    valid_until: str | None = None
    eligible_group_ids: list[str] = Field(default_factory=list)
    quote_total_cost: float | None = Field(default=None, ge=0, le=1000000)
    budget_total_cost: float | None = Field(default=None, ge=0, le=1000000)
    quote_rooms: int | None = Field(default=None, ge=1, le=30)
    requires_revalidation: bool = False
    external_offer_id: str | None = None
    capacity_scope: str = "synthetic"
    booking_status: Literal["unbooked", "confirmed"] = "unbooked"
    transport_mode: Literal["private_transfer"] | None = None
    price_evidence: PriceEvidence | None = None
    quote_freshness: AplQuoteFreshness | None = None
    initial_position_assumption: InitialPositionAssumption | None = None
    airport_arrival_assumption: AirportArrivalAssumption | None = None


class PassengerGroup(Model):
    id: str
    name: str
    size: int = Field(ge=1, le=12)
    members: list[str] = Field(default_factory=list)
    origin: str
    destination: str
    deadline: int = Field(ge=0)
    ready_at: int = Field(default=600, ge=0)
    accessible: bool = False
    budget: float = Field(ge=0)
    original_flight_id: str
    status: Literal["scheduled", "waiting", "offered", "confirmed", "affected", "exception"] = "waiting"
    token: str
    priority: int = Field(default=1, ge=1, le=10)
    source: str = "synthetic"
    provenance: dict[str, Any] = Field(default_factory=dict)
    adult_count: int | None = Field(default=None, ge=0, le=12)
    incident_id: str | None = None


class Leg(Model):
    resource_id: str
    kind: Literal["bus", "flight", "hotel"]
    origin: str
    destination: str
    departure: int
    arrival: int
    cost: float
    transport_mode: Literal["private_transfer"] | None = None
    price_evidence: PriceEvidence | None = None
    initial_position_assumption: InitialPositionAssumption | None = None
    airport_arrival_assumption: AirportArrivalAssumption | None = None
    postflight_connection: dict[str, Any] | None = None

    @model_serializer(mode="wrap")
    def omit_absent_postflight_connection(self, handler):
        result = handler(self)
        if self.postflight_connection is None:
            result.pop("postflight_connection", None)
        return result


class Itinerary(Model):
    legs: list[Leg]
    arrival: int
    total_cost: float
    cost_evidence: CostEvidence | None = None


class Offer(Model):
    id: str
    group_id: str
    itinerary: Itinerary
    status: Literal["held", "proposed", "accepted", "confirmed", "declined", "expired", "invalidated", "superseded"]
    expires_at: str
    created_version: int
    confirmed_at: str | None = None
    reason: str | None = None
    external_bookings: list[dict[str, Any]] = Field(default_factory=list)


class Reservation(Model):
    id: str
    offer_id: str
    group_id: str
    resource_id: str
    quantity: int
    status: Literal["held", "proposed", "accepted", "confirmed", "released"]
    expires_at: str


class AuditEvent(Model):
    id: str
    type: str
    timestamp: str
    version: int
    target_id: str | None = None
    description: str
    data: dict[str, Any] = Field(default_factory=dict)


class Strategy(Model):
    id: str = "balanced"
    label: str = "Balanced recovery"
    objective: Literal["balanced", "fastest", "lowest_cost"] = "balanced"
    preferred_airport: str | None = None
    weights: dict[str, float] = Field(default_factory=dict)

    @field_validator("preferred_airport")
    @classmethod
    def normalize_airport(cls,value):
        return canonical_id(value)


class Allocation(Model):
    group_id: str
    itinerary: Itinerary


class Plan(Model):
    id: str
    strategy: Strategy
    allocations: list[Allocation]
    exceptions: list[dict[str, Any]]
    recovered_passengers: int
    total_cost: float
    cost_evidence: CostEvidence | None = None
    average_arrival: float
    scenario_changes: list[dict[str, Any]] = Field(default_factory=list)
    validation: dict[str, Any]
    solver: str = "bounded_multi_start_allocator"
    optimality: str = "feasible heuristic; global optimality not proven"
    latency_ms: float = 0


class Decision(Model):
    id: str
    timestamp: str
    source_version: int
    applied_version: int
    mode: Literal["ai", "solver"]
    provider: str
    request: str
    explanation: str
    selected_plan_id: str
    candidates: list[Plan]
    latency_ms: float
    timings_ms: dict[str, float] = Field(default_factory=dict)
    candidate_counts: dict[str, int] = Field(default_factory=dict)
    focus_id: str | None = None


class FlightAirportReference(Model):
    iata: str | None = None
    icao: str | None = None
    catalog_code: str | None = None


class FlightSelection(Model):
    selection_id: str
    fr24_id: str
    flight_number: str | None = None
    callsign: str | None = None
    registration: str | None = None
    aircraft_type: str | None = None
    origin: FlightAirportReference | None = None
    intended_destination: FlightAirportReference | None = None
    actual_destination: FlightAirportReference | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    takeoff_at: str | None = None
    landed_at: str | None = None
    flight_ended: bool | None = None
    status: Literal["tracking", "ended", "unknown"] = "unknown"
    freshness: Literal["current", "stale", "historical", "unknown"] = "unknown"


class IncidentFlightReference(Model):
    lookup_id: str
    selection_id: str


AirportOperation = Literal["arrivals", "departures"]
MAX_CASE_AIRPORT_RESTRICTIONS = 64


class CaseAirportRestriction(Model):
    airport_code: str = Field(min_length=3, max_length=4)
    operation: AirportOperation
    source: Literal["operator_report"] = "operator_report"
    report_id: str = Field(min_length=1, max_length=128)
    received_at: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=2000)
    inherited_from_legacy: bool = False

    @field_validator("airport_code")
    @classmethod
    def canonical_airport(cls, value):
        from .airport_catalog import lookup_airport
        airport = lookup_airport(value)
        if airport is None or airport["code"] != value:
            raise ValueError("Active restrictions require a canonical airport code")
        return value


class CaseAirportRestrictionReport(Model):
    id: str = Field(min_length=1, max_length=128)
    type: Literal["airport_restriction"] = "airport_restriction"
    source: Literal["operator_report"] = "operator_report"
    received_at: str = Field(min_length=1, max_length=64)
    airport_code: str = Field(min_length=3, max_length=4)
    operations: list[AirportOperation] = Field(min_length=1, max_length=2)
    action: Literal["restrict", "clear"]
    description: str = Field(min_length=1, max_length=2000)


class RecoveryIncident(Model):
    id: str
    created_at: str
    source: Literal["operator_report", "demo_scenario"] = "operator_report"
    kind: Literal["destination_unavailable", "flight_disruption"]
    description: str
    requested_flight_number: str
    date_utc: str
    flight_ref: IncidentFlightReference | None
    unavailable_airport_code: str | None = None
    original_unavailable_airport_code: str | None = None
    airport_restrictions: list[CaseAirportRestriction] = Field(default_factory=list, max_length=MAX_CASE_AIRPORT_RESTRICTIONS)
    airport_restriction_reports: list[CaseAirportRestrictionReport] = Field(default_factory=list, max_length=10)
    group_ids: list[str] = Field(default_factory=list)
    status: Literal["needs_passenger_data", "open"] = "needs_passenger_data"
    # Hydrated from a bounded cache; never stored in operational snapshots.
    flight: FlightSelection | None = None
    flight_context_state: Literal["available", "expired", "not_applicable"] = "expired"

    @model_validator(mode="after")
    def source_context(self):
        if self.source == "demo_scenario":
            if self.flight_ref is not None or self.flight is not None or self.flight_context_state != "not_applicable":
                raise ValueError("A fictional demo incident cannot contain sourced flight observations")
        elif self.flight_ref is None or self.flight_context_state == "not_applicable":
            raise ValueError("An operator-reported incident requires its sourced flight reference")
        return self

    @field_validator("airport_restrictions")
    @classmethod
    def unique_airport_operations(cls, entries):
        identities = {(entry.airport_code, entry.operation) for entry in entries}
        if len(identities) != len(entries):
            raise ValueError("Each case airport operation may have only one active restriction")
        return entries

    @model_serializer(mode="wrap")
    def preserve_legacy_payload(self, handler):
        result = handler(self)
        # Existing jobs/receipts compare durable scope fingerprints. Introducing
        # unused restriction fields must not change their original payloads.
        for field in ("airport_restrictions", "airport_restriction_reports", "original_unavailable_airport_code"):
            if not result.get(field):
                result.pop(field, None)
        return result


class World(Model):
    # Lazy server-only inventory history; never serialized into API/model payloads.
    _inventory_context: Any = PrivateAttr(default=None)
    scenario_id: str
    version: int = 1
    epoch: str
    sim_time: int = 600
    mode: Literal["simulation", "live"] = "simulation"
    # Demo uses real sourced inventory and live proposal semantics; only the
    # incident and passenger requirements are explicitly fictional.
    demo: bool = False
    currency: str = "EUR"
    budget: float = 45000
    airports: list[Airport]
    resources: list[Resource]
    groups: list[PassengerGroup]
    offers: list[Offer] = Field(default_factory=list)
    reservations: list[Reservation] = Field(default_factory=list)
    incidents: list[RecoveryIncident] = Field(default_factory=list)
    events: list[AuditEvent] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    provider_status: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def demo_uses_sourced_inventory(self):
        if self.demo and self.mode != "live":
            raise ValueError("Demo operations must use the live inventory workflow")
        if not self.demo and any(incident.source == "demo_scenario" for incident in self.incidents):
            raise ValueError("Fictional demo incidents require a demo operation")
        return self


class Mutation(Model):
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=120)


EventType = Literal["resource_unavailable", "flight_cancelled", "airport_closed", "resource_capacity", "budget_changed", "group_deadline", "group_accessibility", "resource_accessibility", "flight_diverted", "group_budget", "resource_restored", "airport_added", "resource_added", "passengers_added", "resource_schedule", "resource_route", "resource_cost", "airport_restored", "airport_diversion_eligibility", "group_destination", "group_priority", "airport_weather", "scenario_reset"]


class ScenarioChange(Model):
    type: EventType
    target_id: str | None = None
    value: Any = None

    @field_validator("target_id")
    @classmethod
    def normalize_target(cls,value):
        return canonical_id(value)

    @model_validator(mode="after")
    def validate_authoring_payload(self):
        if self.type=="scenario_reset":
            if self.target_id is not None or self.value not in ("empty","seed"):
                raise ValueError("scenario_reset requires target_id null and value empty or seed")
        payload_type=STRUCTURED_PAYLOADS.get(self.type)
        if payload_type and not (self.type=="airport_added" and self.value is None):
            # Keep JSON-native values for audit records and idempotency hashes.
            value=payload_type.model_validate(self.value).model_dump(mode="json")
            object.__setattr__(self,"value",value)
        if self.type=="passengers_added":
            if self.target_id is not None:
                raise ValueError("Passenger group IDs are generated by the server; target_id must be null")
        elif self.type in STRUCTURED_PAYLOADS and not self.target_id:
            raise ValueError("This event requires target_id")
        return self


class EventRequest(Mutation, ScenarioChange):
    pass


class ReplanRequest(Mutation):
    mode: Literal["ai", "solver"] = "solver"


class CommandScope(Model):
    incident_id: str | None = Field(default=None, min_length=1, max_length=120)
    group_id: str | None = Field(default=None, min_length=1, max_length=120)
    selected_id: str | None = Field(default=None, min_length=1, max_length=120)


class CommandRequest(Mutation):
    text: str = Field(min_length=1, max_length=4000)
    context: str = Field(default="", max_length=6000)
    scope: CommandScope | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_command(self, handler):
        result = handler(self)
        if self.scope is None:
            result.pop("scope", None)
        return result


class AcceptRequest(Model):
    token: str = Field(min_length=20, max_length=200)
    idempotency_key: str = Field(min_length=8, max_length=120)


class ConstraintRequest(Model):
    idempotency_key: str = Field(min_length=8, max_length=120)
    deadline: int | None = Field(default=None, ge=0)
    accessible: bool | None = None
    budget: float | None = Field(default=None, ge=0, le=100000)


class LoginRequest(Model):
    username: str | None = Field(default=None, min_length=1, max_length=128)
    password: str | None = Field(default=None, min_length=1, max_length=1024, repr=False)
    # Compatibility for existing token-only local deployments.
    token: str | None = Field(default=None, min_length=1, max_length=512, repr=False)

    @model_validator(mode="after")
    def credentials(self):
        if self.token is not None:
            if self.username is not None or self.password is not None:
                raise ValueError("Provide one login method")
        elif self.username is None or self.password is None:
            raise ValueError("Username and password are required")
        return self
