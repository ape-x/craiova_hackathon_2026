"""Small, server-resolved reference facts for a voice session's first utterance.

This context is neither a command nor authorization to act. The command path
continues to validate the unchanged transcript against current application state.
"""
from __future__ import annotations

import json

from pydantic import Field, field_validator, model_validator

from .airport_catalog import lookup_airport
from .hotel_recovery_profile import HotelRecoveryProfileError, read_hotel_recovery_profile
from .models import Model, World
from .store import DomainError


MAX_CONTEXT_GROUPS = 8
MAX_CONTEXT_AIRPORTS = 2 * MAX_CONTEXT_GROUPS + 1
MAX_REFERENCE_BYTES = 6000


class VoiceScope(Model):
    incident_id: str | None = Field(default=None, min_length=1, max_length=128, strict=True)
    group_id: str | None = Field(default=None, min_length=1, max_length=128, strict=True)

    @field_validator("incident_id", "group_id")
    @classmethod
    def exact_nonblank_id(cls, value):
        if value is not None and (value != value.strip() or not value.strip()):
            raise ValueError("Voice scope requires an exact, nonblank ID")
        return value

    @model_validator(mode="after")
    def has_selection(self):
        if self.incident_id is None and self.group_id is None:
            raise ValueError("Voice scope requires a case or group ID")
        return self


def _label(value: str, limit: int) -> str:
    # Labels are data, not instructions. Bound them without changing any ID or
    # using a truncated value to resolve a case or execute a command.
    return " ".join(value.split())[:limit]


def _airport_vocabulary(world: World, groups, incident) -> dict:
    """Catalog names for selected endpoint codes, never observed locations.

    Airport node labels and coordinates may be operator-supplied. Resolve only
    an unambiguous airport node's code against the local catalog; a hotel or an
    unresolved endpoint ID must not turn into an airport by matching its text.
    """
    nodes = {}
    ambiguous = set()
    for node in world.airports:
        previous = nodes.get(node.id)
        if previous is not None and (previous.kind, previous.code) != (node.kind, node.code):
            ambiguous.add(node.id)
        nodes[node.id] = node
    codes = []
    for group in groups:
        for endpoint in (group.origin, group.destination):
            node = nodes.get(endpoint)
            if node is not None and node.kind == "airport" and endpoint not in ambiguous:
                codes.append(node.code)
    if incident is not None and incident.unavailable_airport_code:
        codes.append(incident.unavailable_airport_code)
    if incident is not None:
        codes.extend(item.airport_code for item in incident.airport_restrictions)
    entries = []
    seen = set()
    for code in codes:
        airport = lookup_airport(code)
        if airport is None or not airport["iata"] or airport["iata"] in seen:
            continue
        seen.add(airport["iata"])
        entries.append({"iata": airport["iata"], "name": airport["name"]})
    # Keep the same byte/count bound: current case-restriction names fill only
    # unused slots after the selected passenger endpoints and legacy case code.
    return {"source": "local_airport_catalog", "entries": entries[:MAX_CONTEXT_AIRPORTS]}


def build_voice_context(world: World, scope: VoiceScope) -> str:
    """Resolve exact selections; fail before creating a paid voice session."""
    incident = next((item for item in world.incidents if item.id == scope.incident_id), None)
    group = next((item for item in world.groups if item.id == scope.group_id), None)
    if scope.incident_id is not None and incident is None:
        raise DomainError(404, "Selected voice case no longer exists; select the current case again")
    if scope.group_id is not None and group is None:
        raise DomainError(404, "Selected voice group no longer exists; select the current group again")
    if group is not None:
        if incident is None and group.incident_id is not None:
            incident = next((item for item in world.incidents if item.id == group.incident_id), None)
            if incident is None:
                raise DomainError(409, "Selected voice group has no matching current case")
        if incident is not None and (group.incident_id != incident.id or group.id not in incident.group_ids):
            raise DomainError(409, "Selected voice case and group do not belong together")
        groups = [group]
        omitted = 0
    else:
        by_id = {item.id: item for item in world.groups}
        linked = list(dict.fromkeys(incident.group_ids))
        if any(item not in by_id or by_id[item].incident_id != incident.id for item in linked):
            raise DomainError(409, "Selected voice case has inconsistent group references")
        groups = [by_id[item] for item in linked[:MAX_CONTEXT_GROUPS]]
        omitted = max(0, len(linked) - len(groups))

    facts = {
        "kind": "application_reference_only",
        "operation_mode": world.mode,
        "case": None if incident is None else {
            "id": incident.id,
            "flight_number": _label(incident.requested_flight_number, 32),
            "date_utc": _label(incident.date_utc, 32),
        },
        "groups": [],
        "other_groups_omitted": omitted,
        "airport_vocabulary": _airport_vocabulary(world, groups, incident),
    }
    for item in groups:
        try:
            read_hotel_recovery_profile(world, item)
            profile_valid = True
        except HotelRecoveryProfileError:
            profile_valid = False
        facts["groups"].append({
            "id": item.id,
            "display_label": _label(item.name, 160),
            "saved_hotel_profile_scope_valid": profile_valid,
        })
    result = json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
    if len(result.encode("utf-8")) > MAX_REFERENCE_BYTES:
        raise DomainError(409, "Selected voice reference is too large; select one group")
    return result
