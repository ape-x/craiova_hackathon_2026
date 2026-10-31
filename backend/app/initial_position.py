"""Explicit passenger pickup facts; never an inferred airport-to-point journey."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, field_validator

from .pricing import PriceModel, aware_timestamp


class PositionCoordinates(PriceModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class InitialPositionAssumption(PriceModel):
    source: Literal["operator_declared"]
    confirmed: Literal[True]
    group_id: str
    group_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    scenario_id: str
    epoch: str
    airport_context_id: str
    point_id: str
    label: str = Field(min_length=1, max_length=500)
    coordinates: PositionCoordinates
    ready_at: int = Field(ge=0)

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("The operator must explicitly confirm the initial position")
        return value

    @field_validator("epoch")
    @classmethod
    def aware_epoch(cls, value):
        aware_timestamp(value)
        return value


def initial_position_fingerprint(world, group) -> str:
    # Presentation labels and transient state do not move passengers. Changed
    # identities, constraints or source facts do invalidate an old declaration.
    evidence = {"scenario_id": world.scenario_id, "epoch": world.epoch,
        "group": group.model_dump(mode="json", exclude={"status", "token", "name"})}
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def valid_initial_position(world, resource, group) -> bool:
    try:
        raw = resource.initial_position_assumption
        assumption = InitialPositionAssumption.model_validate(raw.model_dump(mode="json") if hasattr(raw, "model_dump") else raw)
        point = next((item for item in world.airports if item.id == assumption.point_id), None)
        airport = next((item for item in world.airports if item.id == assumption.airport_context_id), None)
        return (world.mode == "live" and assumption.group_id == group.id
            and assumption.scenario_id == world.scenario_id and assumption.epoch == world.epoch
            and assumption.group_fingerprint == initial_position_fingerprint(world, group)
            and assumption.airport_context_id == group.origin and assumption.ready_at == group.ready_at
            and airport is not None and airport.kind == "airport"
            and point is not None and point.kind == "meeting_point" and point.id == resource.origin
            and point.lat == assumption.coordinates.latitude and point.lon == assumption.coordinates.longitude
            and resource.eligible_group_ids == [group.id] and resource.kind == "bus"
            and resource.transport_mode == "private_transfer")
    except (TypeError, ValueError, AttributeError):
        return False
