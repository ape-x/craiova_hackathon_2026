"""Flight identification and operator-reported recovery cases, without supply invention."""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
import re
import secrets
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .airport_catalog import lookup_airport
from .live_data import identifier
from .models import Airport, FlightSelection, IncidentFlightReference, Model, Mutation, RecoveryIncident
from .providers.base import ProviderError
from .solver import resource_blocked_by_incident
from .scenario_schema import MAX_AIRPORTS
from .store import DomainError, Store, audit, utcnow


def normalized_flight(value: str) -> str:
    return re.sub(r"[\s-]", "", value).upper()


class FlightLookupRequest(Model):
    flight_number: str = Field(min_length=3, max_length=16)
    date_utc: date

    @field_validator("flight_number")
    @classmethod
    def flight_identifier(cls, value):
        value = normalized_flight(value)
        if not re.fullmatch(r"[A-Z0-9]{2,3}[0-9]{1,4}[A-Z]?", value) or not any(char.isalpha() for char in value):
            raise ValueError("Use a flight number such as W64351")
        return value

    @field_validator("date_utc")
    @classmethod
    def supported_date(cls, value):
        if not date(2022, 6, 1) <= value <= utcnow().date():
            raise ValueError("Choose a UTC date from 2022-06-01 through today; FR24 has no future schedules")
        return value


class FlightContextSelection(Mutation):
    lookup_id: str = Field(min_length=8, max_length=80)
    selection_id: str = Field(min_length=8, max_length=80)


class IncidentCreate(FlightContextSelection):
    kind: Literal["destination_unavailable", "flight_disruption"]
    description: str = Field(min_length=3, max_length=2000)
    unavailable_airport_code: str | None = None
    affected_group_ids: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("description")
    @classmethod
    def description_text(cls, value):
        value = value.strip()
        if len(value) < 3 or any(ord(char) < 32 for char in value):
            raise ValueError("Provide a clear incident description without control characters")
        return value

    @field_validator("unavailable_airport_code")
    @classmethod
    def known_airport(cls, value):
        if value is None:
            return None
        airport = lookup_airport(value.strip().upper())
        if airport is None:
            raise ValueError("Choose a known airport code")
        return airport["code"]

    @field_validator("affected_group_ids")
    @classmethod
    def group_ids(cls, value):
        return sorted({identifier(item) for item in value})

    @model_validator(mode="after")
    def incident_target(self):
        if self.kind == "destination_unavailable" and self.unavailable_airport_code is None:
            raise ValueError("Identify the destination reported as unavailable")
        return self


class IncidentGroupBinding(Mutation):
    group_ids: list[str] = Field(min_length=1, max_length=500)

    @field_validator("group_ids")
    @classmethod
    def normalize_groups(cls, value):
        return sorted({identifier(item) for item in value})


class IncidentService:
    def __init__(self, settings, store: Store, flights):
        self.settings, self.store, self.flights = settings, store, flights

    async def lookup(self, request: FlightLookupRequest):
        request = FlightLookupRequest.model_validate(request.model_dump())
        if self.settings.operation_mode not in ("live", "demo"):
            raise DomainError(409, "Real flight lookup requires the live operation")
        payload = request.model_dump(mode="json")
        query_key = Store.fingerprint(payload)
        cached = self.store.cached_flight_lookup(query_key)
        if cached:
            return cached
        base = {**payload, "lookup_id": None, "source": "Flightradar24", "queried_at": utcnow().isoformat(),
                "expires_at": None, "matches": [], "cached": False, "message": None, "code": None}
        key = "flight-lookup:" + query_key
        try:
            claim = self.store.claim_request(key, payload, self.settings.request_timeout_seconds + 15)
        except DomainError as exc:
            return {**base, "state": "error", "code": "lookup_in_progress", "message": exc.message}
        try:
            cached = self.store.cached_flight_lookup(query_key)
            if cached:
                return cached
            try:
                matches = await self.flights.lookup(request.flight_number, request.date_utc)
            except ProviderError as exc:
                return {**base, "state": "not_configured" if exc.code == "not_configured" else "error", "code": exc.code, "message": str(exc)}
            except (TimeoutError, asyncio.TimeoutError):
                return {**base, "state": "error", "code": "timeout", "message": "Flight lookup timed out. No flight was selected; its reserved request budget is retained."}
            result = {**base, "lookup_id": "lookup_" + secrets.token_hex(16),
                      "expires_at": (utcnow() + timedelta(seconds=min(300, self.store.observation_retention_seconds))).isoformat(),
                      "matches": [self.store._freshen_flight(match) for match in matches],
                      "state": "ready" if matches else "no_match",
                      "message": "Choose the exact flight leg." if len(matches) > 1 else None if matches else "No tracked flight matched this number and UTC date. This does not establish a cancellation or future schedule."}
            self.store.save_flight_lookup(query_key, result)
            return result
        finally:
            self.store.release_request(key, claim["owner"])

    @staticmethod
    def _live(world):
        if world.mode != "live":
            raise DomainError(409, "Recovery incidents require the live operation")

    @staticmethod
    def _find(world, incident_id):
        incident = next((item for item in world.incidents if item.id == incident_id), None)
        if incident is None:
            raise DomainError(404, "Recovery incident does not exist in the current operation")
        return incident

    @staticmethod
    def _references(world, match):
        for field in ("origin", "intended_destination", "actual_destination"):
            value = match.get(field)
            code = value.get("catalog_code") if value else None
            if code and not any(item.id == code for item in world.airports):
                airport = lookup_airport(code)
                if len(world.airports) >= MAX_AIRPORTS:
                    raise DomainError(422, "Airport reference limit reached")
                world.airports.append(Airport(id=code, code=code, name=airport["name"], lat=airport["lat"], lon=airport["lon"], available=False, operational_status="unknown"))

    @staticmethod
    def _bind(world, incident, group_ids, match):
        identifiers = {normalized_flight(value) for value in (incident.requested_flight_number, match.get("flight_number"), match.get("callsign"), match.get("fr24_id")) if value}
        groups = []
        for group_id in group_ids:
            group = next((item for item in world.groups if item.id == group_id), None)
            if group is None:
                raise DomainError(404, f"Passenger group {group_id} does not exist")
            if group.incident_id not in (None, incident.id):
                raise DomainError(409, f"Passenger group {group_id} already belongs to another recovery incident")
            if group.status == "confirmed" or any(offer.group_id == group_id and offer.status == "confirmed" for offer in world.offers):
                raise DomainError(409, f"Passenger group {group_id} already has supplier-confirmed bookings. They require separate operator handling before a new recovery case; existing bookings remain unchanged.")
            if normalized_flight(group.original_flight_id) not in identifiers:
                raise DomainError(409, f"Passenger group {group_id} references a different original flight")
            groups.append(group)
        for group in groups:
            group.incident_id = incident.id
            if group.id not in incident.group_ids:
                incident.group_ids.append(group.id)
            for offer in world.offers:
                if offer.group_id != group.id or offer.status not in ("held", "proposed", "accepted"):
                    continue
                resources = {resource.id: resource for resource in world.resources}
                if any(resource_blocked_by_incident(world, resources[leg.resource_id], group) for leg in offer.itinerary.legs):
                    offer.status = "invalidated"
                    offer.reason = "An operator-reported incident blocks this proposal. Local allocations released; no supplier cancellation was issued."
                    for reservation in world.reservations:
                        if reservation.offer_id == offer.id:
                            reservation.status = "released"
                    group.status = "affected"
                    audit(world, "proposal_invalidated_by_incident", offer.reason, offer.id, {"incident_id": incident.id, "group_id": group.id})
        incident.group_ids.sort()
        incident.status = "open" if incident.group_ids else "needs_passenger_data"

    def create(self, request: IncidentCreate):
        def apply(world):
            self._live(world)
            if len(world.incidents) >= 100:
                raise DomainError(422, "Recovery incident limit reached")
            lookup, match = self.store.selected_flight(request.lookup_id, request.selection_id)
            intended = match.get("intended_destination") or {}
            if request.kind == "destination_unavailable" and request.unavailable_airport_code != intended.get("catalog_code"):
                raise DomainError(422, "The reported unavailable destination must match this flight's sourced intended destination")
            incident = RecoveryIncident(id="INC-" + secrets.token_hex(8).upper(), created_at=utcnow().isoformat(),
                kind=request.kind, description=request.description, requested_flight_number=lookup["flight_number"], date_utc=lookup["date_utc"],
                flight_ref=IncidentFlightReference(lookup_id=request.lookup_id, selection_id=request.selection_id),
                unavailable_airport_code=request.unavailable_airport_code, flight=FlightSelection.model_validate(match), flight_context_state="available")
            world.incidents.append(incident)
            self._bind(world, incident, request.affected_group_ids, match)
            self._references(world, match)
            audit(world, "incident_created", "Operator-reported recovery incident recorded; provider flight status and supplier inventory were not changed.", incident.id,
                  {"kind": incident.kind, "unavailable_airport_code": incident.unavailable_airport_code, "group_ids": incident.group_ids})
            return {"incident_id": incident.id}
        return self.store.mutate("incident-create:" + request.idempotency_key, request.model_dump(mode="json"), request.expected_version, apply)

    def bind(self, incident_id: str, request: IncidentGroupBinding):
        def apply(world):
            self._live(world)
            incident = self._find(world, incident_id)
            try:
                # Binding an existing case does not select a new flight or
                # assert fresh tracking state. Retained source aliases remain
                # useful after the initial five-minute selection window.
                if incident.flight_ref is None:
                    match = {}
                else:
                    _, match = self.store.selected_flight(incident.flight_ref.lookup_id, incident.flight_ref.selection_id, fresh=False)
            except DomainError as exc:
                if exc.status != 409:
                    raise
                # The operator's saved flight number is durable case input.
                # Expired source aliases are never reconstructed or retained.
                match = {}
            self._bind(world, incident, request.group_ids, match)
            audit(world, "incident_groups_bound", "Explicit passenger groups attached to this recovery incident; no locations or supplier bookings changed.", incident.id, {"group_ids": request.group_ids})
            return {"incident_id": incident.id}
        payload = {**request.model_dump(mode="json"), "incident_id": incident_id}
        return self.store.mutate("incident-bind:" + request.idempotency_key, payload, request.expected_version, apply)

    def refresh(self, incident_id: str, request: FlightContextSelection):
        def apply(world):
            self._live(world)
            incident = self._find(world, incident_id)
            if incident.flight_ref is None:
                raise DomainError(409, "This fictional incident has no tracked flight to refresh. Its travel alternatives are sourced separately.")
            lookup, match = self.store.selected_flight(request.lookup_id, request.selection_id)
            if request.selection_id != incident.flight_ref.selection_id or lookup["flight_number"] != incident.requested_flight_number or lookup["date_utc"] != incident.date_utc:
                raise DomainError(409, "Refresh must identify the same exact flight leg and UTC date; this incident was not changed")
            incident.flight_ref = IncidentFlightReference(lookup_id=request.lookup_id, selection_id=request.selection_id)
            incident.flight = FlightSelection.model_validate(match)
            incident.flight_context_state = "available"
            self._references(world, match)
            audit(world, "incident_flight_refreshed", "Flight context refreshed for the same flight leg; operator-reported constraints remain separate.", incident.id)
            return {"incident_id": incident.id}
        payload = {**request.model_dump(mode="json"), "incident_id": incident_id}
        return self.store.mutate("incident-refresh:" + request.idempotency_key, payload, request.expected_version, apply)
