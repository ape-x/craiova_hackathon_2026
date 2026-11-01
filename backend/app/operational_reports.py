"""Versioned operator reports, separate from supplier facts and booking authority."""
from __future__ import annotations

from datetime import datetime, timezone
import copy
import re
import secrets
from typing import Annotated, Literal
import unicodedata

from pydantic import Field, ValidationError, field_validator, model_serializer

from .hotel_recovery import HotelRecoveryInputs, validate_hotel_job
from .romanian_hotel_recovery import RomanianHotelRecoveryInputs, validate_romanian_hotel_job
from .airport_cab_recovery import AirportCabRecoveryInput, validate_airport_transfer_job
from .after_flight_inputs import AfterFlightTransferInput
from .after_flight_recovery import validate_after_flight_job
from .confirmed_allocations import has_confirmed_groups
from .live_data import _known_airport_code, _new_airports, aware, relative_minute
from .live_service import _find, _invalidate_pending, _live
from .models import Model, Mutation, World
from .recovery_jobs import RecoveryJobRequest
from .store import DomainError, Store, audit, utcnow


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


class ArrivalRecoveryOptions(Model):
    """Explicit job allowance, without a command-interpretation charge or scope override."""
    objective: Literal["balanced", "fastest", "lowest_cost"] = "balanced"
    modes: list[Literal["flight", "hotel", "bus"]] = Field(default_factory=lambda: ["flight"], min_length=1, max_length=3)
    max_provider_calls: int = Field(default=8, ge=3, le=26, strict=True)
    hotel_recovery: HotelRecoveryInputs | None = None
    romanian_hotel_recovery: RomanianHotelRecoveryInputs | None = None
    airport_transfers: list[AirportCabRecoveryInput] = Field(default_factory=list, max_length=12)
    after_flight_transfer: AfterFlightTransferInput | None = None

    @model_serializer(mode="wrap")
    def compatible_payload(self, handler):
        result = handler(self)
        if self.romanian_hotel_recovery is None:
            result.pop("romanian_hotel_recovery", None)
        if not self.airport_transfers:
            result.pop("airport_transfers", None)
        if self.after_flight_transfer is None:
            result.pop("after_flight_transfer", None)
        return result


class ArrivalReportInput(OperationalReportInput):
    group_ids: list[ReportId] = Field(min_length=1, max_length=500)
    airport_code: str = Field(min_length=3, max_length=4, strict=True)
    ready_at: datetime
    recovery: ArrivalRecoveryOptions | None = None

    @model_serializer(mode="wrap")
    def compatible_payload(self, handler):
        result = handler(self)
        # Report-only receipts created before recovery was supported retain the
        # same fingerprint, including when a client explicitly sends null.
        if self.recovery is None:
            result.pop("recovery", None)
        return result

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
    return has_confirmed_groups(world, group_ids)


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
            scenario_id=world.scenario_id, epoch=world.epoch, original_flight_id=group.original_flight_id,
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
    def __init__(self, store, jobs=None):
        self.store, self.jobs = store, jobs

    def arrival(self, incident_id: str, request: ArrivalReportInput):
        incident_id = _route_id(incident_id)
        payload = {**request.model_dump(mode="json"), "incident_id": incident_id}

        def apply(world):
            result = apply_arrival_report(world, incident_id, request)
            result["focus_id"] = request.airport_code
            return result

        def enqueue(conn, response):
            options = request.recovery
            if options is None:
                return
            response["command_interpretation_calls"] = 0
            response["recovery_provider_call_limit"] = options.max_provider_calls

            def blocked(code, explanation):
                response["recovery_blocked"] = {"code": code, "explanation": explanation}
                response["explanation"] += " Report recorded; recovery was not started. " + explanation

            if self.jobs is None:
                blocked("recovery_service_unavailable", "Recovery service is unavailable; no provider call was dispatched.")
                return
            current = self.store._load(conn)
            if "hotel" in options.modes and options.hotel_recovery is None and options.romanian_hotel_recovery is None:
                blocked("hotel_setup_required", "Supply fresh hotel and transfer inputs for the reported location and readiness. A saved hotel profile was not reused or omitted from the requested journey.")
                return
            try:
                job_request = RecoveryJobRequest(expected_version=current.version,
                    idempotency_key="arrival-report-" + Store.fingerprint({"key": request.idempotency_key, "scenario": current.scenario_id, "incident": incident_id}),
                    group_ids=request.group_ids, **options.model_dump(),
                    instructions=f"Recover exactly the reported groups from {request.airport_code} after their operator-reported readiness {request.ready_at.isoformat()}. Preserve their final destinations and current constraints.")
            except ValidationError:
                blocked("recovery_prerequisite_failed", "Recovery requires 1–24 selected groups with supported exact identifiers and valid source inputs. The report was preserved without changing its group scope.")
                return
            if job_request.group_ids != request.group_ids:
                blocked("recovery_prerequisite_failed", "The recovery identifier normalization would change the reported group scope. The report was preserved; no renamed or substituted groups were queued.")
                return
            try:
                validate_hotel_job(current, job_request)
            except DomainError as exc:
                if exc.status != 422:
                    raise
                blocked("hotel_setup_required", exc.message + " No saved hotel inputs were substituted.")
                return
            try:
                validate_romanian_hotel_job(current, job_request)
            except DomainError as exc:
                if exc.status != 422:
                    raise
                blocked("romanian_hotel_setup_required", exc.message + " No hotel, coach departure or luggage was inferred.")
                return
            try:
                validate_airport_transfer_job(current, job_request, getattr(self.jobs,"airport_cab",None))
            except DomainError as exc:
                if exc.status != 422:
                    raise
                blocked("airport_transfer_setup_required", exc.message + " Pickup and luggage were not inferred or reused.")
                return
            try:
                validate_after_flight_job(current, job_request, getattr(self.jobs,"airport_cab",None))
            except DomainError as exc:
                if exc.status != 422:
                    raise
                blocked("airport_transfer_setup_required", exc.message + " No flight arrival, pickup window or luggage was inferred.")
                return
            if not self.jobs.ai or not self.jobs.ai.configured:
                blocked("ai_not_configured", "The real recovery AI is unavailable; no scripted plan or provider call was substituted.")
                return
            # A known queue rejection preserves the independently valid report,
            # but never a partially inserted successor or cancelled predecessor.
            conn.execute("SAVEPOINT arrival_recovery")
            try:
                result = self.jobs.start_in_transaction(conn, current, incident_id, job_request, supersede_active=True)
            except DomainError as exc:
                conn.execute("ROLLBACK TO SAVEPOINT arrival_recovery")
                conn.execute("RELEASE SAVEPOINT arrival_recovery")
                code = (exc.details or {}).get("code")
                if code not in ("recovery_queue_full", "recovery_group_active", "recovery_handoff_limit"):
                    raise
                blocked("recovery_queue_unavailable", "The recovery queue cannot accept this request. Inspect existing attempts; no replacement was queued and their call allowances were not changed.")
                return
            conn.execute("RELEASE SAVEPOINT arrival_recovery")
            response["recovery_job"] = result["job"]
            response["explanation"] += (f" Recovery for exactly groups {', '.join(request.group_ids)} was queued with at most {options.max_provider_calls} provider/model calls and no interpretation call. "
                "A queued request does not confirm an itinerary or booking.")
            if result["job"].get("supersedes_job_ids"):
                response["explanation"] += " It supersedes outdated attempts for these groups and waits for already dispatched calls; an uncertain outcome requires review before further calls."

        return self.store.mutate("operator-arrival:" + request.idempotency_key, payload, request.expected_version,
            apply, on_commit=enqueue)

    def unavailable(self, resource_id: str, request: ResourceUnavailabilityInput):
        resource_id = _route_id(resource_id)
        payload = {**request.model_dump(mode="json"), "resource_id": resource_id}
        return self.store.mutate("operator-unavailability:" + request.idempotency_key, payload, request.expected_version,
            lambda world: apply_resource_unavailability(world, resource_id, request))
