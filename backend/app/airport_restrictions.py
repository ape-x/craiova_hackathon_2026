"""Case-scoped operator restrictions, never airport permission or aircraft control."""
from __future__ import annotations

import secrets
from typing import Literal

from pydantic import Field, ValidationError, field_validator

from .airport_catalog import lookup_airport
from .hotel_recovery import validate_hotel_job
from .inventory_lifecycle import resource_history
from .live_service import _find, _live
from .models import (AirportOperation, CaseAirportRestriction, CaseAirportRestrictionReport,
                     MAX_CASE_AIRPORT_RESTRICTIONS, World)
from .operational_reports import (ArrivalRecoveryOptions, ArrivalReportInput, OperationalReportInput,
                                  ReportId, _confirmed_groups, _route_id)
from .recovery_jobs import RecoveryJobRequest
from .store import DomainError, Store, audit, utcnow


class AirportRestrictionRecoveryOptions(ArrivalRecoveryOptions):
    group_ids: list[ReportId] = Field(min_length=1, max_length=500)

    @field_validator("group_ids")
    @classmethod
    def exact_groups(cls, value):
        return ArrivalReportInput.distinct_groups(value)


class AirportRestrictionReportInput(OperationalReportInput):
    airport_code: str = Field(min_length=3, max_length=4, strict=True)
    operations: list[AirportOperation] = Field(min_length=1, max_length=2)
    action: Literal["restrict", "clear"]
    recovery: AirportRestrictionRecoveryOptions | None = None

    @field_validator("airport_code")
    @classmethod
    def known_airport(cls, value):
        return ArrivalReportInput.known_airport(value)

    @field_validator("operations")
    @classmethod
    def distinct_operations(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Specify each airport operation only once")
        return sorted(value)


def _inherited_entries(incident):
    entries = {(item.airport_code, item.operation): item.model_copy(deep=True)
               for item in incident.airport_restrictions}
    legacy = incident.unavailable_airport_code
    if legacy:
        airport = lookup_airport(legacy)
        if airport is None:
            raise DomainError(409, "The original airport restriction requires review before migration")
        for operation in ("arrivals", "departures"):
            entries.setdefault((airport["code"], operation), CaseAirportRestriction(
                airport_code=airport["code"], operation=operation, report_id=incident.id,
                received_at=incident.created_at, description=incident.description,
                inherited_from_legacy=True))
    return entries


def _restricted_resource_ids(world, group, offer, resources):
    # Import enforcement lazily: it is pure and shared with the solver, while
    # this module owns transactions and must not become a solver import cycle.
    from .airport_constraints import case_airport_operation_blocked
    from .solver import resource_blocked_by_incident
    blocked = set()
    for leg in offer.itinerary.legs:
        if leg.resource_id not in resources:
            resources[leg.resource_id] = resource_history(world, resource_id=leg.resource_id)
        versions = resources[leg.resource_id]
        if any(resource_blocked_by_incident(world, resource, group) for resource in versions):
            blocked.add(leg.resource_id)
        elif not versions and leg.kind == "flight" and (
                case_airport_operation_blocked(world, group, leg.origin, "departures")
                or case_airport_operation_blocked(world, group, leg.destination, "arrivals")):
            # The retained itinerary still establishes its explicit endpoints;
            # no unavailable supplier segments or capacity are reconstructed.
            blocked.add(leg.resource_id)
    return blocked


def apply_airport_restriction_report(world: World, incident_id: str, request: AirportRestrictionReportInput,
                                     *, recovery_group_ids: list[str] | None = None) -> dict:
    """Apply one report to the caller's world; persistence and rollback stay outside."""
    _live(world)
    incident_id = _route_id(incident_id)
    incident = _find(world.incidents, incident_id, "recovery case")
    groups = {group.id: group for group in world.groups
              if group.incident_id == incident.id and group.id in incident.group_ids}
    selected = request.recovery.group_ids if request.recovery else []
    if recovery_group_ids is not None:
        selected = ArrivalReportInput.distinct_groups(list(recovery_group_ids))
        if request.recovery is not None and selected != request.recovery.group_ids:
            raise DomainError(409, "The explicit recovery scope differs from the report request")
    entries = _inherited_entries(incident)
    legacy = incident.unavailable_airport_code
    report_id, received_at = "opr_" + secrets.token_hex(12), utcnow().isoformat()
    for operation in request.operations:
        key = (request.airport_code, operation)
        if request.action == "restrict":
            entries[key] = CaseAirportRestriction(airport_code=request.airport_code, operation=operation,
                report_id=report_id, received_at=received_at, description=request.description)
        else:
            entries.pop(key, None)
    if len(entries) > MAX_CASE_AIRPORT_RESTRICTIONS:
        raise DomainError(409, "This case has reached its active airport-operation restriction limit")

    # Dependency checks consider only this report's operations, not a different
    # old restriction. Clearing restrictions never invalidates/restores offers.
    report_case = incident.model_copy(update={"unavailable_airport_code": None,
        "airport_restrictions": [CaseAirportRestriction(airport_code=request.airport_code,
            operation=operation, report_id=report_id, received_at=received_at,
            description=request.description) for operation in request.operations]})
    scope = world.model_copy(update={"incidents": [report_case if item.id == incident.id else item for item in world.incidents]})
    current_case = incident.model_copy(update={"unavailable_airport_code": None,
        "airport_restrictions": [entries[key] for key in sorted(entries)]})
    current_scope = world.model_copy(update={"incidents": [current_case if item.id == incident.id else item for item in world.incidents]})
    resources, affected, conflicts = {}, [], {}

    def conflict(group_id, offer_id, resource_ids):
        conflicts.setdefault((group_id, offer_id), set()).update(resource_ids)

    for offer in world.offers:
        group = groups.get(offer.group_id)
        if group is None:
            continue
        confirmed_reservation = any(item.offer_id == offer.id and item.status == "confirmed" for item in world.reservations)
        if offer.status not in ("held", "proposed", "accepted", "confirmed") and not offer.external_bookings and not confirmed_reservation:
            continue
        current_blocked = _restricted_resource_ids(current_scope, group, offer, resources)
        confirmed = (offer.status == "confirmed" or bool(offer.external_bookings)
            or group.status == "confirmed" or confirmed_reservation
            or any(item.booking_status == "confirmed" for identity in current_blocked for item in resources.get(identity, [])))
        if confirmed:
            if current_blocked:
                conflict(group.id, offer.id, current_blocked)
        elif request.action == "restrict" and offer.status in ("held", "proposed", "accepted") and _restricted_resource_ids(scope, group, offer, resources):
            affected.append(offer)
    # Retained externally confirmed allocations can outlive their offer. Show
    # conflicts against all remaining case restrictions, including after clear.
    from .solver import resource_blocked_by_incident
    for reservation in world.reservations:
        group = groups.get(reservation.group_id)
        if group is None or reservation.status != "confirmed":
            continue
        if reservation.resource_id not in resources:
            resources[reservation.resource_id] = resource_history(current_scope, resource_id=reservation.resource_id)
        versions = resources[reservation.resource_id]
        if any(resource_blocked_by_incident(current_scope, resource, group) for resource in versions):
            offer_id = reservation.offer_id if any(item.id == reservation.offer_id for item in world.offers) else None
            conflict(group.id, offer_id, [reservation.resource_id])

    incident.airport_restrictions = [entries[key] for key in sorted(entries)]
    if legacy:
        if incident.original_unavailable_airport_code is None:
            incident.original_unavailable_airport_code = lookup_airport(legacy)["code"]
        incident.unavailable_airport_code = None
    affected_ids = {offer.id for offer in affected}
    for offer in affected:
        offer.status = "invalidated"
        offer.reason = "A case-scoped operator airport restriction blocks this proposal. No supplier cancellation was issued."
    for reservation in world.reservations:
        if reservation.offer_id in affected_ids and reservation.status != "confirmed":
            reservation.status = "released"
    for group_id in {offer.group_id for offer in affected}:
        group = groups[group_id]
        if group.status != "confirmed" and not any(offer.group_id == group_id and offer.status in ("held", "proposed", "accepted", "confirmed") for offer in world.offers):
            group.status = "waiting"
    confirmed_conflicts = [{"group_id": group_id, "offer_id": offer_id, "resource_ids": sorted(ids)}
        for (group_id, offer_id), ids in sorted(conflicts.items(), key=lambda item: (item[0][0], item[0][1] or ""))]
    report = {"id": report_id, "type": "airport_restriction", "source": "operator_report",
        "received_at": received_at, "incident_id": incident.id, "airport_code": request.airport_code,
        "operations": request.operations, "action": request.action, "description": request.description,
        "affected_offer_ids": sorted(affected_ids), "affected_group_ids": sorted({offer.group_id for offer in affected}),
        "recovery_group_ids": selected, "manual_review_required": bool(confirmed_conflicts),
        "confirmed_conflicts": confirmed_conflicts, "migrated_legacy_airport_code": legacy}
    summary = CaseAirportRestrictionReport.model_validate({key: report[key] for key in CaseAirportRestrictionReport.model_fields})
    incident.airport_restriction_reports = [*incident.airport_restriction_reports, summary][-10:]
    explanation = ("Recorded the operator restriction for this case only. Dependent pending proposals were invalidated; confirmed bookings were preserved."
        if request.action == "restrict" else
        "Cleared only the selected operator restriction for this case. This does not assert airport permission, override weather or source restrictions, or restore any proposal.")
    if confirmed_conflicts:
        explanation += " Confirmed travel conflicts require manual review; no supplier booking was cancelled or replaced."
    explanation += " Aircraft facts and passenger position/readiness were not changed."
    audit(world, "operator_airport_restriction", explanation, incident.id, report)
    return {"applied": True, "incident_id": incident.id, "focus_id": request.airport_code,
        "report": report, "manual_review_required": bool(confirmed_conflicts),
        "confirmed_conflicts": confirmed_conflicts, "explanation": explanation}


def enqueue_airport_restriction_recovery(conn, store, jobs, response, request, *, job_request=None,
                                         recovery_blocked=None, interpretation_calls=0, total_call_limit=None):
    """Queue within the saved report transaction; known preflight failures retain it."""
    if not conn.in_transaction:
        raise RuntimeError("Restriction recovery requires the report transaction")
    response["command_interpretation_calls"] = interpretation_calls
    if total_call_limit is not None:
        response["recovery_total_call_limit"] = total_call_limit
    selected = response["report"]["recovery_group_ids"]

    def blocked(code, explanation):
        response["recovery_blocked"] = {"code": code, "explanation": explanation}
        response["explanation"] += " Restriction report recorded; recovery was not started. " + explanation

    if recovery_blocked is not None:
        blocked(recovery_blocked["code"], recovery_blocked["explanation"])
        return
    options = request.recovery
    if job_request is None and options is None:
        if selected:
            raise RuntimeError("An explicit recovery request requires a job or a known blocked outcome")
        return
    current = store._load(conn)
    if job_request is None:
        response["recovery_provider_call_limit"] = options.max_provider_calls
        try:
            job_request = RecoveryJobRequest(expected_version=current.version,
                idempotency_key="airport-report-" + Store.fingerprint({"key": request.idempotency_key,
                    "scenario": current.scenario_id, "incident": response["incident_id"]}),
                **options.model_dump(), instructions=f"Recover exactly the selected groups under the recorded {request.action} of {', '.join(request.operations)} at {request.airport_code}. Passenger positions, readiness and final destinations remain as recorded.")
        except ValidationError:
            blocked("recovery_prerequisite_failed", "Recovery requires 1–24 groups with supported exact identifiers. The report scope was not narrowed or renamed.")
            return
    response["recovery_provider_call_limit"] = job_request.max_provider_calls
    if job_request.group_ids != selected:
        blocked("recovery_prerequisite_failed", "Recovery must use exactly the explicitly selected group IDs; no group scope was normalized or substituted.")
        return
    if total_call_limit is not None and job_request.max_provider_calls + interpretation_calls > total_call_limit:
        blocked("insufficient_call_allowance", "The requested recovery would exceed the original total call allowance; no limit was increased.")
        return
    incident = _find(current.incidents, response["incident_id"], "recovery case")
    by_id = {group.id: group for group in current.groups}
    if any(identity not in by_id or by_id[identity].incident_id != incident.id or identity not in incident.group_ids for identity in selected):
        blocked("recovery_prerequisite_failed", "Every selected group must already belong to this case. No groups were linked, inferred or dropped.")
        return
    if _confirmed_groups(current, set(selected)):
        blocked("recovery_prerequisite_failed", "Selected groups have externally confirmed travel and require manual review; their bookings were not replaced.")
        return
    if response.get("confirmed_conflicts"):
        # Fixed confirmed allocations cannot be silently changed to make room
        # for a new plan. Orphan confirmation records also require review: this
        # is an explicit case-level policy, not a claim that every such record
        # proves the solver's fixed-allocation set is infeasible.
        blocked("recovery_prerequisite_failed", "Confirmed travel in this case conflicts with its remaining airport restrictions. Operator review is required before automatic recovery for any groups in the case; no booking was cancelled or replaced.")
        return
    if "hotel" in job_request.modes and job_request.hotel_recovery is None:
        blocked("hotel_setup_required", "Explicit valid hotel and transfer inputs are required; no saved profile was silently reused and no requested mode was dropped.")
        return
    try:
        validate_hotel_job(current, job_request)
    except DomainError as exc:
        if exc.status != 422:
            raise
        blocked("hotel_setup_required", exc.message)
        return
    if jobs is None or not jobs.ai or not jobs.ai.configured:
        blocked("recovery_service_unavailable", "The real recovery service is unavailable; no source or model call was dispatched.")
        return
    conn.execute("SAVEPOINT airport_restriction_recovery")
    try:
        result = jobs.start_in_transaction(conn, current, incident.id, job_request, supersede_active=True)
    except DomainError as exc:
        conn.execute("ROLLBACK TO SAVEPOINT airport_restriction_recovery")
        conn.execute("RELEASE SAVEPOINT airport_restriction_recovery")
        if (exc.details or {}).get("code") not in ("recovery_queue_full", "recovery_group_active", "recovery_handoff_limit"):
            raise
        blocked("recovery_queue_unavailable", "The queue cannot accept the replacement. No partial successor or predecessor cancellation was retained.")
        return
    conn.execute("RELEASE SAVEPOINT airport_restriction_recovery")
    response["recovery_job"] = result["job"]
    response["explanation"] += (f" Recovery was queued for exactly groups {', '.join(selected)} with at most {job_request.max_provider_calls} provider/model calls. "
        "The receipt does not confirm a feasible itinerary or booking.")
    if result["job"].get("supersedes_job_ids"):
        response["explanation"] += " Earlier overlapping attempts must drain; unknown outcomes require review before successor calls."


class AirportRestrictions:
    def __init__(self, store, jobs=None):
        self.store, self.jobs = store, jobs

    def report(self, incident_id: str, request: AirportRestrictionReportInput):
        incident_id = _route_id(incident_id)
        payload = {**request.model_dump(mode="json"), "incident_id": incident_id}
        return self.store.mutate("operator-airport-restriction:" + request.idempotency_key,
            payload, request.expected_version,
            lambda world: apply_airport_restriction_report(world, incident_id, request),
            on_commit=lambda conn, response: enqueue_airport_restriction_recovery(conn,
                self.store, self.jobs, response, request))
