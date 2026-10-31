"""Reusable operator inputs, saved only with a successful scoped hotel plan.

The profile is not source evidence or a saved fare. Its identity deliberately
excludes mutable planning constraints; callers revalidate those before dispatch.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


PROFILE_KEY = "hotel_recovery_profile"


class HotelRecoveryProfileError(ValueError):
    pass


class HotelRecoveryProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=1, le=1, strict=True)
    source: Literal["operator_requested"]
    scope_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    inputs: dict[str, Any]


def _data(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _scope(world, group):
    world, group = _data(world), _data(group)
    incident = next((item for item in world.get("incidents", [])
                     if item["id"] == group.get("incident_id") and group["id"] in item["group_ids"]), None)
    if world.get("mode") != "live" or incident is None:
        raise HotelRecoveryProfileError("The saved hotel configuration must belong to this live case and group")
    return {"scenario_id": world["scenario_id"], "epoch": world["epoch"],
            "incident": {key: incident.get(key) for key in ("id", "requested_flight_number", "date_utc")},
            "group": {key: group.get(key) for key in (
                "id", "size", "adult_count", "members", "source", "origin", "destination", "original_flight_id")}}


def _fingerprint(world, group):
    return hashlib.sha256(json.dumps(_scope(world, group), sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _inputs(value):
    # Local import avoids a cycle: hotel_recovery installs profiles, while the
    # existing job-input model remains the single schema for actual inputs.
    from .hotel_recovery import HotelRecoveryInputs
    return HotelRecoveryInputs.model_validate(_data(value)).model_dump(mode="json")


def build_hotel_recovery_profile(world, group, inputs) -> dict:
    """Return stable metadata; the caller owns its successful atomic persistence."""
    return HotelRecoveryProfile(schema_version=1, source="operator_requested",
                                scope_fingerprint=_fingerprint(world, group),
                                inputs=_inputs(inputs)).model_dump(mode="json")


def read_hotel_recovery_profile(world, group) -> dict:
    """Return typed, scope-bound inputs. Do not change dates or current limits."""
    try:
        raw = _data(group).get("provenance", {}).get(PROFILE_KEY)
        if raw is None:
            raise HotelRecoveryProfileError("No saved hotel configuration; complete the typed setup first")
        profile = HotelRecoveryProfile.model_validate(raw)
        if profile.scope_fingerprint != _fingerprint(world, group):
            raise HotelRecoveryProfileError("The saved hotel configuration belongs to a different party, route or case")
        return copy.deepcopy(_inputs(profile.inputs))
    except (ValidationError, TypeError, KeyError) as exc:
        raise HotelRecoveryProfileError("The saved hotel configuration is invalid; review the typed setup") from exc


def hotel_profile_summary(world, group) -> dict | None:
    """Small command context: no coordinates, names, luggage or passenger data."""
    if PROFILE_KEY not in _data(group).get("provenance", {}):
        return None
    try:
        inputs = read_hotel_recovery_profile(world, group)
    except (HotelRecoveryProfileError, ValueError):
        return {"configured": False, "requires_typed_setup": True}
    return {"configured": True, "source": "operator_requested",
            "checkin_at": inputs["checkin_at"], "checkout_at": inputs["checkout_at"],
            "pickup_at": inputs["pickup_at"], "initial_airport_code": _data(group)["origin"],
            "onward_airport_code": inputs["onward_airport_code"],
            "destination_airport_code": _data(group)["destination"],
            "minimum_total_calls": 11}
