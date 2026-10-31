"""Versioned operator reports, separate from supplier facts and booking authority."""
from __future__ import annotations

from datetime import datetime, timezone
import copy
import re
import secrets
from typing import Annotated
import unicodedata

from pydantic import Field, field_validator

from .live_data import _known_airport_code, _new_airports, aware, relative_minute
from .live_service import _find, _invalidate_pending, _live
from .models import Mutation, World
from .store import DomainError, audit, utcnow


ReportId = Annotated[str, Field(min_length=1, max_length=128, strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]


class OperationalReportInput(Mutation):
    description: str = Field(min_length=3, max_length=2000, strict=True)

    @field_validator("description")
    @classmethod
    def meaningful_description(cls, value):
        if any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value):
            raise ValueError("Describe the operator report without control characters")
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Provide a meaningful operator report description")
        return value


class ArrivalReportInput(OperationalReportInput):
    group_ids: list[ReportId] = Field(min_length=1, max_length=500)
    airport_code: str = Field(min_length=3, max_length=4, strict=True)
    ready_at: datetime

    @field_validator("group_ids")
    @classmethod
    def distinct_groups(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Select each existing group only once")
        return sorted(value)

    @field_validator("airport_code")
    @classmethod
    def known_airport(cls, value):
        if any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value):
            raise ValueError("Use a known airport code without control characters")
        return _known_airport_code(value)

    @field_validator("ready_at", mode="before")
    @classmethod
    def precise_readiness(cls, value):
        value = aware(value)
        if value.second or value.microsecond or value.astimezone(timezone.utc).second:
            raise ValueError("Use an explicit whole-minute readiness timestamp")
        return value


class ResourceUnavailabilityInput(OperationalReportInput):
    pass


def _route_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise DomainError(422, "Use an exact existing case or resource ID")
    return value


def _history(provenance, key):
    result = provenance.get(key, [])
    if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
        raise DomainError(409, "Existing operator report history requires review")
    return result


def _confirmed_groups(world, group_ids):
    return (any(group.id in group_ids and group.status == "confirmed" for group in world.groups)
        or any(offer.group_id in group_ids and (offer.status == "confirmed" or offer.external_bookings) for offer in world.offers)
        or any(item.group_id in group_ids and item.status == "confirmed" for item in world.reservations))


def _confirmed_service(world, resource):
    return (resource.booking_status == "confirmed"
        or any(item.resource_id == resource.id and item.status == "confirmed" for item in world.reservations)
        or any((offer.status == "confirmed" and any(leg.resource_id == resource.id for leg in offer.itinerary.legs))
            or any(item.get("resource_id") == resource.id for item in offer.external_bookings)
            for offer in world.offers))


def apply_arrival_report(world: World, incident_id: str, request: ArrivalReportInput, *, readiness_basis: dict | None = None) -> dict:
    """Apply to the supplied in-memory world; the caller owns persistence/rollback."""
    incident_id = _route_id(incident_id)
    _live(world)
    incident = _find(world.incidents, incident_id, "recovery case")
    groups = [_find(world.groups, item, "passenger group") for item in request.group_ids]
    if any(group.incident_id != incident.id or group.id not in incident.group_ids for group in groups):
        raise DomainError(409, "Every selected group must belong to the selected recovery case")
    selected = set(request.group_ids)
    if _confirmed_groups(world, selected):
        raise DomainError(409, "Selected passengers have externally confirmed bookings. Operator review is required; this report cannot relocate booked passengers or cancel services.")
    ready = relative_minute(world, request.ready_at, rounding="up")
    airports = _new_airports(world, [request.airport_code])
    existing_airport = next((item for item in world.airports if item.id == request.airport_code), None)
    if existing_airport is not None and (existing_airport.kind != "airport" or existing_airport.code != request.airport_code):
        raise DomainError(409, "The selected airport code conflicts with a current location node")
    histories = {group.id: _history(group.provenance, "arrival_reports") for group in groups}
    affected = [offer for offer in world.offers if offer.status in ("proposed", "accepted") and offer.group_id in selected]
    report = {"id": "opr_" + secrets.token_hex(12), "type": "arrival_report", "source": "operator_report",
        "received_at": utcnow().isoformat(), "description": request.description,
        "incident_id": incident.id, "group_ids": request.group_ids,
        "airport_code": request.airport_code, "ready_at": request.ready_at.isoformat(),
        "affected_offer_ids": sorted(offer.id for offer in affected),
        "affected_group_ids": sorted({offer.group_id for offer in affected})}
    if readiness_basis is not None:
        report["readiness_basis"] = copy.deepcopy(readiness_basis)
    for group in groups:
        entry = {key: copy.deepcopy(value) for key, value in report.items() if key not in ("group_ids", "affected_group_ids", "affected_offer_ids")}
        entry.update(group_id=group.id, previous_origin=group.origin, previous_ready_at=group.ready_at,
            affected_offer_ids=sorted(offer.id for offer in affected if offer.group_id == group.id))
        group.provenance = {**group.provenance, "arrival_reports": [*histories[group.id], entry]}
        group.origin, group.ready_at = request.airport_code, ready
    world.airports.extend(airports)
    explanation = "Recorded the operator-reported passenger location and readiness. Only selected pending proposals were invalidated; no landing, airport permission, supplier search or booking was inferred."
    _invalidate_pending(world, "Operator reported a new passenger location/readiness", group_ids=selected)
    audit(world, "operator_arrival_report", explanation, incident.id, report)
    return {"applied": True, "incident_id": incident.id, "report": report, "explanation": explanation}


def apply_resource_unavailability(world: World, resource_id: str, request: ResourceUnavailabilityInput) -> dict:
    """Apply a reported outage without a transaction or external side effects."""
    resource_id = _route_id(resource_id)
    _live(world)
    resource = _find(world.resources, resource_id, "service")
    if _confirmed_service(world, resource):
        raise DomainError(409, "This service has externally confirmed bookings. Operator review is required; this report cannot cancel or change those bookings.")
    history = _history(resource.provenance, "unavailability_reports")
    affected = [offer for offer in world.offers if offer.status in ("proposed", "accepted")
        and any(leg.resource_id == resource.id for leg in offer.itinerary.legs)]
    report = {"id": "opr_" + secrets.token_hex(12), "type": "resource_unavailability", "source": "operator_report",
        "received_at": utcnow().isoformat(), "description": request.description,
        "resource_id": resource.id, "previous_available": resource.available,
        "affected_offer_ids": sorted(offer.id for offer in affected),
        "affected_group_ids": sorted({offer.group_id for offer in affected})}
    resource.provenance = {**resource.provenance, "unavailability_reports": [*history, report]}
    resource.available = False
    explanation = "Recorded the operator report that this service is unavailable for planning. Only dependent pending proposals were invalidated; supplier evidence and confirmed bookings were preserved. No replacement search or booking was made."
    _invalidate_pending(world, "Operator reported this service unavailable", resource_ids={resource.id})
    audit(world, "operator_resource_unavailability", explanation, resource.id, report)
    return {"applied": True, "report": report, "explanation": explanation}


class OperationalReports:
    def __init__(self, store):
        self.store = store

    def arrival(self, incident_id: str, request: ArrivalReportInput):
        incident_id = _route_id(incident_id)
        payload = {**request.model_dump(mode="json"), "incident_id": incident_id}
        return self.store.mutate("operator-arrival:" + request.idempotency_key, payload, request.expected_version,
            lambda world: apply_arrival_report(world, incident_id, request))

    def unavailable(self, resource_id: str, request: ResourceUnavailabilityInput):
        resource_id = _route_id(resource_id)
        payload = {**request.model_dump(mode="json"), "resource_id": resource_id}
        return self.store.mutate("operator-unavailability:" + request.idempotency_key, payload, request.expected_version,
            lambda world: apply_resource_unavailability(world, resource_id, request))
