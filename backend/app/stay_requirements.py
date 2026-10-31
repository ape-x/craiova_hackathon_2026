"""Requested accommodation is a planning constraint, never supplier evidence."""
from __future__ import annotations

import math
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .pricing import aware_timestamp


REQUIREMENT_KEY = "hotel_stay_requirement"


class StayRequirementError(ValueError):
    code = "invalid_hotel_stay_requirement"


class HotelStayRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, hide_input_in_errors=True)

    source: Literal["operator_requested"]
    checkin_at: str
    checkout_at: str
    rooms: int = Field(ge=1, le=30)
    minimum_rest_minutes: int = Field(ge=0)

    @field_validator("checkin_at", "checkout_at")
    @classmethod
    def aware_instant(cls, value):
        aware_timestamp(value)
        return value

    @model_validator(mode="after")
    def coherent_interval(self):
        start, end = aware_timestamp(self.checkin_at), aware_timestamp(self.checkout_at)
        if end <= start:
            raise ValueError("Hotel checkout must follow check-in")
        # Compare exact instants: rounding a short interval must not create rest.
        if self.minimum_rest_minutes > (end-start).total_seconds()/60:
            raise ValueError("Minimum rest exceeds the explicitly requested interval")
        return self


def read_stay_requirement(group) -> HotelStayRequirement | None:
    """Absent means no constraint; present-null or malformed input is an error."""
    if REQUIREMENT_KEY not in group.provenance:
        return None
    raw = group.provenance[REQUIREMENT_KEY]
    try:
        requirement = HotelStayRequirement.model_validate(raw.model_dump(mode="json") if isinstance(raw, HotelStayRequirement) else raw)
    except (ValidationError, TypeError, ValueError) as exc:
        raise StayRequirementError("Invalid hotel stay requirement: provide offset-bearing check-in/checkout, a positive room count and a nonnegative rest duration no longer than the requested interval.") from exc
    if requirement.rooms > group.size:
        raise StayRequirementError("Invalid hotel stay requirement: requested rooms exceed the passenger count.")
    return requirement


def _matching_stay(world, group, requirement, resource, leg=None) -> bool:
    try:
        if (resource.kind != "hotel" or resource.source != "Nuitee / LiteAPI"
                or resource.origin != resource.destination or resource.quote_rooms != requirement.rooms
                or (resource.eligible_group_ids and group.id not in resource.eligible_group_ids)):
            return False
        source = resource.provenance
        if (source.get("provider") != "liteapi" or type(source.get("rooms")) is not int
                or source["rooms"] != requirement.rooms or type(source.get("adults")) is not int
                or source["adults"] != group.size):
            return False
        location = source["property_location"]
        node = next((item for item in world.airports if item.id == resource.origin), None)
        if (node is None or node.kind != "hotel" or location.get("node_id") != node.id
                or location.get("latitude") != node.lat or location.get("longitude") != node.lon):
            return False
        start, end = aware_timestamp(source["checkin_at"]), aware_timestamp(source["checkout_at"])
        epoch = aware_timestamp(world.epoch)
        if (end <= start or math.floor((start-epoch).total_seconds()/60) != resource.departure
                or math.ceil((end-epoch).total_seconds()/60) != resource.arrival):
            return False
        if leg is not None and (leg.resource_id, leg.kind, leg.origin, leg.destination, leg.departure, leg.arrival) != (
                resource.id, resource.kind, resource.origin, resource.destination, resource.departure, resource.arrival):
            return False
        return (start <= aware_timestamp(requirement.checkin_at)
            and end >= aware_timestamp(requirement.checkout_at)
            and end-start >= timedelta(minutes=requirement.minimum_rest_minutes))
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return False


def matching_required_stay(world, group, resource, leg=None) -> bool:
    """Check a sourced stay; without a requirement this constraint is satisfied.

    Quote freshness, room capacity, budgets, policy and route continuity remain
    the responsibility of the existing source importer and allocation checker.
    """
    requirement = read_stay_requirement(group)
    return requirement is None or _matching_stay(world, group, requirement, resource, leg)


def itinerary_stay_errors(world, group, legs) -> list[str]:
    """Require one actual hotel leg; disjoint stays and airport waits do not add up."""
    try:
        requirement = read_stay_requirement(group)
    except StayRequirementError as exc:
        return [f"{group.id}: {exc}"]
    if requirement is None:
        return []
    resources = {resource.id: resource for resource in world.resources}
    if any(leg.resource_id in resources and _matching_stay(world, group, requirement, resources[leg.resource_id], leg) for leg in legs):
        return []
    return [f"{group.id}: requested hotel stay is missing or does not cover its full interval, room count and minimum rest."]
