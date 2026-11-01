"""Explicit initial-vehicle inputs shared by recovery and atomic arrival reports."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, field_validator

from .airport_cab_planning import AirportCabPlanningRequest, cab_context
from .live_data import identifier
from .models import Model
from .pricing import aware_timestamp
from .providers.base import ProviderError
from .providers.bucharest_cab import SOURCE
from .solver import fixed_offers
from .store import DomainError, Store


AIRPORT_TRANSFER_CONTEXT = (
    "Bucharest.CAB resources are unreserved initial OTP-to-CRA vehicle-category quotes. "
    "Native EUR prices use cash with no optional extras. Driving arrivals are estimates "
    "without departure-time traffic; terminal or curb access is unverified. Supplier fleet "
    "stock and expiry are unknown. The application permits at most one category across "
    "the combined plan and fixed allocations and applies its own short receipt window. "
    "Do not describe these quotes as booked vehicles or guaranteed arrival times."
)


class AirportCabRecoveryInput(Model):
    group_id: str = Field(min_length=1, max_length=80)
    destination_airport: Literal["CRA"]
    pickup_at: datetime
    suitcases: int = Field(ge=0, le=7, strict=True)
    handbags: int = Field(ge=0, le=7, strict=True)

    @field_validator("group_id")
    @classmethod
    def exact_group(cls, value):
        if identifier(value) != value:
            raise ValueError("Use the exact selected passenger group identifier")
        return value

    @field_validator("pickup_at", mode="before")
    @classmethod
    def exact_pickup(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("Supply a pickup timestamp with its UTC offset")
        value = aware_timestamp(value.isoformat() if isinstance(value, datetime) else value)
        if value.second or value.microsecond or value.astimezone(timezone.utc).second:
            raise ValueError("Supply an exact whole-minute pickup timestamp")
        return value


def validate_airport_transfer_job(world, request, airport_cab):
    """No I/O and no inferred baggage, pickup time, or passenger relocation."""
    inputs = request.airport_transfers
    if not inputs:
        return
    if "bus" not in request.modes or request.hotel_recovery is not None:
        raise DomainError(422, "Airport vehicle sourcing requires Ground transport and cannot be combined with the separate hotel recovery workflow")
    ids = [item.group_id for item in inputs]
    if len(set(ids)) != len(ids) or not set(ids) <= set(request.group_ids):
        raise DomainError(422, "Select each airport transfer group once, within this recovery job's exact passenger scope")
    if request.max_provider_calls < 2 * len(inputs) + 1:
        raise DomainError(422, "Allow two source requests per declared airport transfer plus the final decision; flight searches require additional calls")
    if airport_cab is None or not airport_cab.provider.configured:
        raise DomainError(422, "The airport vehicle source is unavailable; no substitute transport was inferred")
    for item in inputs:
        try:
            cab_context(world, item, airport_cab.provider, now=airport_cab.now())
        except DomainError as exc:
            # These are known source prerequisites, not failures of an independently
            # valid arrival report. Its transaction may retain the report and explain
            # why no recovery job was queued against the projected passenger state.
            raise DomainError(422, exc.message) from exc


def _matches_transfer_request(resource, group, request):
    """Compare declared inputs, never infer bags from category capacity."""
    try:
        quoted = resource.provenance["airport_cab_quote"]["request"]
        return (resource.eligible_group_ids == [request.group_id]
            and resource.origin == "OTP" and resource.destination == request.destination_airport
            and quoted["origin_airport"] == "OTP" and quoted["destination_airport"] == request.destination_airport
            and type(quoted["passengers"]) is int and quoted["passengers"] == group.size
            and type(quoted["suitcases"]) is int and quoted["suitcases"] == request.suitcases
            and type(quoted["handbags"]) is int and quoted["handbags"] == request.handbags
            and aware_timestamp(quoted["pickup_at"]) == request.pickup_at)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def validate_airport_transfer_selection(world, plan, inputs, *, fresh_resource_ids=()):
    """Independent commit guard for this job's explicitly refreshed CAB scope.

    A current old receipt is insufficient after an explicit new source request,
    even when its original pickup and luggage happen to match. The caller passes
    only IDs built from this job's returned, validated source receipts.
    """
    requested = {item.group_id: item for value in inputs
        for item in [AirportCabRecoveryInput.model_validate(value)]}
    if not requested:
        return
    fresh = set(fresh_resource_ids)
    resources, groups = {item.id: item for item in world.resources}, {item.id: item for item in world.groups}
    for allocation in plan.allocations:
        request = requested.get(allocation.group_id)
        if request is None:
            continue
        group = groups.get(allocation.group_id)
        for leg in allocation.itinerary.legs:
            resource = resources.get(leg.resource_id)
            if resource is not None and resource.source == SOURCE and (
                    resource.id not in fresh or group is None or not _matches_transfer_request(resource, group, request)):
                raise DomainError(409, "The selected airport vehicle does not have this search's fresh exact pickup and luggage evidence",
                    {"code": "airport_transfer_request_mismatch", "group_id": allocation.group_id, "resource_id": resource.id})


def _exclude_previous_cab_quotes(projected, inputs, staged):
    """Candidate-only exclusion; the authoritative source inventory is untouched."""
    requested = {item.group_id for item in inputs}
    fresh = {identity for item in staged for identity in item["resources"]}
    affected = set()
    for resource in projected.resources:
        if (resource.source == SOURCE and resource.id not in fresh
                and len(resource.eligible_group_ids or []) == 1
                and resource.eligible_group_ids[0] in requested and resource.available):
            resource.available = False
            affected.add(resource.eligible_group_ids[0])
    return sorted(affected)


async def source_airport_transfers(jobs, job, owner, world, gaps, source_counts):
    """Stage independently quoted alternatives; each HTTP dispatch has its own receipt."""
    from .airport_cab_planning import append_airport_cab_options

    projected = world.model_copy(deep=True)
    staged, observed = [], []
    inputs = [AirportCabRecoveryInput.model_validate(item) for item in job["_request"].get("airport_transfers", [])]
    if not inputs:
        return projected, staged

    def missing(code, message, ids):
        gaps.append({"code": code, "message": message, "group_ids": list(ids)})

    def finish_projection():
        # Apply after normal source staging, so prior-revision retirement is
        # recorded normally when a new usable receipt actually exists.
        affected = _exclude_previous_cab_quotes(projected, inputs, staged)
        if affected:
            missing("airport_transfer_previous_quote_excluded",
                "This explicit airport transfer search requires a freshly returned category for the exact pickup and luggage. Earlier CAB receipts were excluded from this attempt; stored inventory was not changed by that exclusion.", affected)
        return projected, staged

    fixed_ids = {leg.resource_id for offer in fixed_offers(world, set(job["group_ids"])) for leg in offer.itinerary.legs}
    if any(resource.source == SOURCE and resource.id in fixed_ids for resource in world.resources):
        missing("airport_transfer_fleet_unverified", "A fixed allocation already uses this source. Additional vehicle availability is unverified, so no replacement vehicle was queried or assumed.", [item.group_id for item in inputs])
        return finish_projection()

    for item in inputs:
        if await jobs._cancelled(job, owner):
            break
        current = await asyncio.to_thread(jobs.ledger.get, job["id"], private=True)
        if current["max_provider_calls"] - current["calls_dispatched"] < 3:
            missing("airport_transfer_allowance_uncovered", "This quote requires two source requests while preserving the final decision. No partial quote or extra call was started.", [item.group_id])
            continue
        request = AirportCabPlanningRequest(**item.model_dump(), expected_version=job["_planning_version"],
            idempotency_key="recovery-cab-" + Store.fingerprint({"job_id": job["id"], "group_id": item.group_id}))
        context, _ = cab_context(world, request, jobs.airport_cab.provider, now=jobs.airport_cab.now())

        async def run_phase(phase, operation):
            if phase not in ("bootstrap", "vehicles"):
                raise DomainError(502, "Airport source requested an unsupported dispatch phase")
            private_result = {}

            async def execute():
                private_result["value"] = await operation()
                # Anonymous source tokens/cookies remain inside the provider and
                # this in-memory return path, never in job results or AI context.
                return {"bootstrap_received": True} if phase == "bootstrap" else private_result["value"]

            await jobs._call(job, owner, "searching_airport_transfer", SOURCE,
                {"phase": phase, **item.model_dump(mode="json")}, [item.group_id], execute)
            return private_result["value"]

        try:
            response = await jobs.airport_cab.quote(request, request_runner=run_phase, propagate_errors=True)
        except ProviderError as exc:
            # The provider also bounds the complete two-phase operation. Its
            # outer deadline can cancel an individual _call before translating
            # that cancellation to a sanitized timeout; retain the unknown result.
            current = await asyncio.to_thread(jobs.ledger.get, job["id"], private=True)
            if not current.get("_failure_status"):
                unknown = exc.code in ("timeout", "connection", "http_500", "http_502", "http_503", "http_504")
                await asyncio.to_thread(jobs.ledger.halt, job["id"], owner,
                    "unknown_outcome" if unknown else "failed", str(exc) + " No retry was issued.")
            raise

        usable = {entry["option_id"] for entry in response["options"] if entry["importable"]}
        options = (response.get("transfer") or {}).get("options", [])
        observed.append({"group_id": item.group_id, "quote_id": response["quote_id"],
            "quote_sequence": response["quote_sequence"], "pickup_at": item.pickup_at.isoformat(),
            "usable_until": response["usable_until"], "state": response["state"],
            "http_request_count": (response.get("transfer") or {}).get("http_request_count", 0),
            "arrival_estimated": True, "supplier_booked": False, "blockers": response["blockers"],
            "options": [{**{key: option[key] for key in ("id", "car_type", "price", "max_passengers", "max_suitcases", "max_handbags", "fits_request")},
                "importable": option["id"] in usable} for option in options]})
        source_counts[SOURCE] = source_counts.get(SOURCE, 0) + len(options)
        if usable:
            added = append_airport_cab_options(projected, context, response,
                [option["id"] for option in options if option["id"] in usable], now=jobs.airport_cab.now(), stage=True)
            staged.append({"context": context, "response": response,
                "resources": dict(zip(added["resource_ids"], [option["id"] for option in options if option["id"] in usable])),
                "superseded_resource_ids": added["superseded_resource_ids"]})
        else:
            missing("airport_transfer_not_usable", "The source returned no category covering the exact party, luggage, pickup and current planning window. No pickup was shifted and no vehicle was invented.", [item.group_id])
        await jobs._update(job, owner, airport_transfer_quotes=observed, source_counts=source_counts, gaps=gaps)
    return finish_projection()
