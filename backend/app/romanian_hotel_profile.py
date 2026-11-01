"""Exact Romanian package inputs, persisted with a successful complete proposal.

Profiles are not fares, availability, observed passenger positions or permission
to spend. Stable identity is checked here; dispatch must revalidate current
readiness, dates, restrictions, party eligibility, budgets and call allowance.
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .pricing import aware_timestamp
from .romanian_hotel_recovery import RomanianHotelRecoveryInputs
from .store import Store, utcnow


_GROUP_IDENTITY = ("id", "size", "adult_count", "members", "source", "origin", "destination", "original_flight_id")
_REASONS = {
    "missing": "No saved Romanian hotel configuration; complete explicit typed setup first",
    "stale": "The saved Romanian hotel configuration belongs to a different party, route or case",
    "invalid": "The saved Romanian hotel configuration is invalid; review typed setup",
    "scope": "The Romanian hotel configuration requires an existing group reciprocally linked to this live case",
}


class RomanianHotelProfileError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason if reason in _REASONS else "invalid"
        super().__init__(_REASONS[self.reason])


class _Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    schema_version: int = Field(ge=1, le=1, strict=True)
    source: Literal["operator_requested"]
    scope_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$", strict=True)
    inputs: RomanianHotelRecoveryInputs
    source_job_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$", strict=True)
    saved_at: str = Field(min_length=1, max_length=80, strict=True)

    @field_validator("saved_at")
    @classmethod
    def aware_saved_at(cls, value):
        aware_timestamp(value)
        return value


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _identity(group):
    return {name: _field(group, name) for name in _GROUP_IDENTITY}


def _case(world, incident_id):
    cases = [item for item in _field(world, "incidents", []) if _field(item, "id") == incident_id]
    if _field(world, "mode") != "live" or not incident_id or len(cases) != 1:
        raise RomanianHotelProfileError("scope")
    return cases[0]


def _scope(world, group):
    group_id, incident_id = _field(group, "id"), _field(group, "incident_id")
    incident = _case(world, incident_id)
    retained = [item for item in _field(world, "groups", []) if _field(item, "id") == group_id]
    if (not group_id or _field(incident, "group_ids", []).count(group_id) != 1 or len(retained) != 1
            or _field(retained[0], "incident_id") != incident_id or _identity(retained[0]) != _identity(group)):
        raise RomanianHotelProfileError("scope")
    return {"scenario_id": _field(world, "scenario_id"), "epoch": _field(world, "epoch"),
        "incident": {name: _field(incident, name) for name in ("id", "requested_flight_number", "date_utc")},
        "group": _identity(group)}


def _fingerprint(scope):
    return hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _key(world, group):
    return _field(world, "scenario_id"), _field(group, "incident_id"), _field(group, "id")


class RomanianHotelProfiles:
    def __init__(self, store: Store):
        self.store = store
        with store.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS romanian_hotel_profiles (
                scenario_id TEXT NOT NULL, incident_id TEXT NOT NULL, group_id TEXT NOT NULL,
                data TEXT NOT NULL, PRIMARY KEY(scenario_id, incident_id, group_id))""")

    def save_in_transaction(self, conn, world, group, inputs, job_id, now=None):
        """Caller owns successful complete-plan commit; never start/commit here."""
        if not conn.in_transaction:
            raise RuntimeError("Saving Romanian hotel inputs requires an active transaction")
        scope = _scope(world, group)
        try:
            supplied = inputs.model_dump(mode="json") if hasattr(inputs, "model_dump") else inputs
            typed = RomanianHotelRecoveryInputs.model_validate(supplied)
            saved = now if now is not None else utcnow()
            if not isinstance(saved, datetime):
                raise ValueError("An explicit save timestamp is required")
            record = _Profile(schema_version=1, source="operator_requested", scope_fingerprint=_fingerprint(scope),
                inputs=typed, source_job_id=job_id, saved_at=saved.isoformat())
            encoded = record.model_dump_json()
        except (ValidationError, TypeError, ValueError, OverflowError) as exc:
            raise RomanianHotelProfileError("invalid") from exc
        conn.execute("""INSERT INTO romanian_hotel_profiles VALUES(?,?,?,?)
            ON CONFLICT(scenario_id,incident_id,group_id) DO UPDATE SET data=excluded.data""",
            (*_key(world, group), encoded))

    def _read_record(self, world, group, *, conn=None):
        scope = _scope(world, group)
        with self.store.connect() if conn is None else nullcontext(conn) as connection:
            row = connection.execute("SELECT data FROM romanian_hotel_profiles WHERE scenario_id=? AND incident_id=? AND group_id=?",
                _key(world, group)).fetchone()
        if row is None:
            raise RomanianHotelProfileError("missing")
        try:
            if not isinstance(row[0], str) or len(row[0].encode("utf-8")) > 8192:
                raise ValueError("Invalid profile encoding")
            record = _Profile.model_validate_json(row[0])
        except (ValidationError, TypeError, ValueError, UnicodeError, OverflowError) as exc:
            raise RomanianHotelProfileError("invalid") from exc
        if record.scope_fingerprint != _fingerprint(scope):
            raise RomanianHotelProfileError("stale")
        return record

    def read(self, world, group, *, conn=None) -> RomanianHotelRecoveryInputs:
        """Detached exact inputs, with no implicit date shift or supplier request."""
        return self._read_record(world, group, conn=conn).inputs.model_copy(deep=True)

    def public_for_case(self, world, incident_id, *, conn=None):
        incident = _case(world, incident_id)
        by_id = {_field(group, "id"): group for group in _field(world, "groups", [])}
        result = []
        with self.store.connect() if conn is None else nullcontext(conn) as connection:
            for group_id in sorted(set(_field(incident, "group_ids", []))):
                item = {"group_id": group_id, "configured": False}
                try:
                    group = by_id.get(group_id)
                    if group is None or _field(group, "incident_id") != incident_id:
                        raise RomanianHotelProfileError("scope")
                    record = self._read_record(world, group, conn=connection)
                    item.update(configured=True, inputs=record.inputs.model_dump(mode="json"),
                        source_job_id=record.source_job_id, saved_at=record.saved_at)
                except RomanianHotelProfileError as exc:
                    item["reason"] = exc.reason
                result.append(item)
        return result

    def summary(self, world, group, *, conn=None):
        """Reference-only command context: no bags, passenger identity or quotes."""
        try:
            record = self._read_record(world, group, conn=conn)
        except RomanianHotelProfileError as exc:
            return {"configured": False, "requires_typed_setup": True, "reason": exc.reason}
        inputs = record.inputs
        return {"configured": True, "source": "operator_requested", "hotel_id": inputs.hotel_id,
            "origin_airport": _field(group, "origin"), "onward_airport": "OTP",
            "destination_airport": _field(group, "destination"),
            "checkin_at": inputs.checkin_at.isoformat(), "checkout_at": inputs.checkout_at.isoformat(),
            "minimum_total_calls": 9, "requires_current_validation": True}
