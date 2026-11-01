"""Bounded live inventory with transactionally preserved, immutable source history.

Planning helpers only modify the supplied World and its private pending snapshots.
History reads are lazy and indexed; persistence belongs exclusively to Store._save.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .models import Airport, Resource
from .scenario_schema import MAX_AIRPORTS, MAX_RESOURCES

ACTIVE = {"held", "proposed", "accepted", "confirmed"}


def _error(status, message, details=None):
    from .store import DomainError
    return DomainError(status, message, details)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class ArchiveAccess:
    """A deepcopy-safe read capability, bound to one operation's namespace."""
    def __init__(self, path, scenario_id, connection=None):
        self.path, self.scenario_id, self.connection = path, scenario_id, connection

    @contextmanager
    def connect(self):
        try:
            current = self.connection if self.connection is not None and self.connection.in_transaction else None
        except sqlite3.Error:
            current = None
        if current is not None:
            yield current
            return
        uri = "file:" + quote(str(Path(self.path).resolve()), safe="/") + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=15)
        try:
            yield conn
        finally:
            conn.close()

    def find(self, entity_type, **filters):
        clauses, args = ["a.scenario_id=?", "a.entity_type=?"], [self.scenario_id, entity_type]
        for name, value in filters.items():
            if value is None:
                continue
            if name == "group_id":
                clauses.append("EXISTS (SELECT 1 FROM inventory_archive_groups g WHERE g.scenario_id=a.scenario_id AND g.entity_type=a.entity_type AND g.entity_id=a.entity_id AND g.revision_hash=a.revision_hash AND g.group_id=?)")
            elif name in {"entity_id", "source", "external_offer_id", "kind", "origin"}:
                clauses.append(f"a.{name}=?")
            else:
                raise ValueError("Unsupported inventory history filter")
            args.append(value)
        with self.connect() as conn:
            rows = conn.execute("SELECT a.data,a.locations,a.archived_at,a.archived_version,a.reason FROM inventory_archive a WHERE " + " AND ".join(clauses) + " ORDER BY a.archived_version DESC,a.rowid DESC", args).fetchall()
        return [{"entity_type": entity_type, "data": json.loads(row[0]), "locations": json.loads(row[1]),
                 "archived_at": row[2], "archived_version": row[3], "reason": row[4]} for row in rows]


@dataclass(eq=False)
class InventoryContext:
    scenario_id: str
    reader: ArchiveAccess | None = None
    pending: list[dict] = field(default_factory=list)

    def __eq__(self, other):
        # A lazy persistence capability is not part of World's domain equality.
        # Pydantic compares private attributes, including a fresh model's None.
        return other is None or isinstance(other, InventoryContext)

    def __deepcopy__(self, memo):
        result = InventoryContext(self.scenario_id, self.reader, copy.deepcopy(self.pending, memo))
        memo[id(self)] = result
        return result


def bind_inventory(world, path, connection=None):
    world._inventory_context = InventoryContext(world.scenario_id, ArchiveAccess(path, world.scenario_id, connection))


def _context(world):
    context = world._inventory_context
    if context is None or context.scenario_id != world.scenario_id:
        reader = context.reader if context else None
        context = InventoryContext(world.scenario_id,
            ArchiveAccess(reader.path, world.scenario_id, reader.connection) if reader else None)
        world._inventory_context = context
    return context


def _records(world, entity_type, **filters):
    context = _context(world)
    def matches(record):
        data = record["data"]
        return record["entity_type"] == entity_type and all(
            value is None or (value in data.get("eligible_group_ids", []) if name == "group_id"
            else data.get("id" if name == "entity_id" else name) == value)
            for name, value in filters.items())
    pending = [copy.deepcopy(record) for record in reversed(context.pending) if matches(record)]
    return pending + (context.reader.find(entity_type, **filters) if context.reader else [])


def resource_history(world, resource_id=None, source=None, external_offer_id=None, group_id=None, kind=None, origin=None):
    """Detached active and historical revisions; supplying filters avoids broad reads."""
    filters = dict(entity_id=resource_id, source=source, external_offer_id=external_offer_id,
                   group_id=group_id, kind=kind, origin=origin)
    active = [item for item in world.resources if all(value is None or
        (value in item.eligible_group_ids if name == "group_id" else getattr(item, "id" if name == "entity_id" else name) == value)
        for name, value in filters.items())]
    result, seen = [], set()
    for item in active + [Resource.model_validate(record["data"]) for record in _records(world, "resource", **filters)]:
        identity = _json(item.model_dump(mode="json"))
        if identity not in seen:
            seen.add(identity)
            result.append(item.model_copy(deep=True))
    return result


def archived_location(world, identity):
    records = _records(world, "location", entity_id=identity)
    return Airport.model_validate(records[0]["data"]) if records else None


def validate_location_identity(world, node):
    candidates = [item for item in world.airports if item.id == node.id]
    candidates += [Airport.model_validate(record["data"]) for record in _records(world, "location", entity_id=node.id)]
    if any((item.kind, item.lat, item.lon) != (node.kind, node.lat, node.lon) for item in candidates):
        message = "Supplier property location changed; review retained source geography" if node.kind == "hotel" else "Location identity conflicts with retained source geography; review the existing location"
        raise _error(409, message)


def _resource_locations(resource):
    ids = {resource.origin, resource.destination}
    if resource.kind == "flight":
        from .airport_constraints import flight_airport_operations
        ids.update(code for code, _ in flight_airport_operations(resource).operations)
    initial = resource.initial_position_assumption
    if initial:
        ids.update((initial.point_id, initial.airport_context_id))
    arrival = resource.airport_arrival_assumption
    if arrival:
        ids.add(arrival.airport_id)
    return ids


def resource_detail(world, resource_id):
    active = next((item for item in world.resources if item.id == resource_id), None)
    records = [] if active else _records(world, "resource", entity_id=resource_id)
    if active is None and not records:
        return None
    record = records[0] if records else None
    resource = active.model_copy(deep=True) if active else Resource.model_validate(record["data"])
    # An archived resource uses its contemporaneous geographic snapshot first.
    locations = {item["id"]: Airport.model_validate(item) for item in record["locations"]} if record else {}
    for identity in sorted(_resource_locations(resource)):
        if identity not in locations:
            node = next((item for item in world.airports if item.id == identity), None) or archived_location(world, identity)
            if node:
                locations[identity] = node.model_copy(deep=True)
    return {"resource": resource, "locations": list(locations.values()), "archived": active is None,
            "archived_at": record["archived_at"] if record else None,
            "archived_version": record["archived_version"] if record else None,
            "archive_reason": record["reason"] if record else None}


def protected_inventory(world, *, removing_resource_ids=(), keep_resource_ids=(), keep_location_ids=()):
    """Close operational references over the resources that would remain active."""
    resources = set(keep_resource_ids)
    locations = set(keep_location_ids)
    for group in world.groups:
        resources.add(group.original_flight_id)
        locations.update((group.origin, group.destination))
    for offer in world.offers:
        if offer.status in ACTIVE:
            resources.update(leg.resource_id for leg in offer.itinerary.legs)
            locations.update(code for leg in offer.itinerary.legs for code in (leg.origin, leg.destination))
        resources.update(item["resource_id"] for item in offer.external_bookings if isinstance(item.get("resource_id"), str))
    resources.update(item.resource_id for item in world.reservations if item.status in ACTIVE)
    resources.update(item.id for item in world.resources if item.booking_status == "confirmed")
    for incident in world.incidents:
        if incident.unavailable_airport_code:
            locations.add(incident.unavailable_airport_code)
            from .airport_constraints import canonical_airport_code
            canonical = canonical_airport_code(incident.unavailable_airport_code)
            if canonical:
                locations.add(canonical)
        locations.update(entry.airport_code for entry in incident.airport_restrictions)
        if incident.flight:
            for key in ("origin", "intended_destination", "actual_destination"):
                airport = getattr(incident.flight, key)
                if airport and airport.catalog_code:
                    locations.add(airport.catalog_code)
    # All airport operational/weather restrictions remain live in this first slice.
    locations.update(item.id for item in world.airports if item.kind == "airport"
        or item.operational_status != "unknown" or item.weather is not None)
    removing = set(removing_resource_ids)
    changed = True
    while changed:
        before = len(resources)
        for item in world.resources:
            if item.id not in removing or item.id in resources:
                locations.update(_resource_locations(item))
                if item.airport_arrival_assumption:
                    resources.add(item.airport_arrival_assumption.onward_resource_id)
                connection = item.provenance.get("postflight_connection")
                if isinstance(connection, dict) and isinstance(connection.get("incoming_resource_id"), str):
                    resources.add(connection["incoming_resource_id"])
        changed = len(resources) != before
    return resources, locations


def _retirable(resource):
    if resource.booking_status == "confirmed":
        return False
    if not resource.available:
        return True
    from .solver import live_resource_current
    # Only sourced inventory with a declared freshness basis can age out.
    return resource.source != "synthetic" and bool(resource.valid_until or resource.requires_revalidation or resource.quote_freshness) and not live_resource_current(resource)


def _snapshot(world, entity_type, item, reason, locations=()):
    record = {"entity_type": entity_type, "data": item.model_dump(mode="json"),
              "locations": [node.model_dump(mode="json") for node in locations],
              "archived_at": datetime.now(timezone.utc).isoformat(), "archived_version": world.version + 1, "reason": reason}
    _context(world).pending.append(record)


def _queue_resource(world, resource, reason):
    locations = []
    for identity in sorted(_resource_locations(resource)):
        node = next((item for item in world.airports if item.id == identity), None) or archived_location(world, identity)
        if node:
            locations.append(node)
            _snapshot(world, "location", node, reason)
    _snapshot(world, "resource", resource, reason, locations)


def archive_resources(world, ids, reason="retired"):
    if world.mode != "live":
        return []
    requested = set(ids)
    eligible = {item.id for item in world.resources if item.id in requested and _retirable(item)}
    protected, _ = protected_inventory(world, removing_resource_ids=eligible)
    removed = eligible - protected
    for item in world.resources:
        if item.id in removed:
            _queue_resource(world, item, reason)
    world.resources = [item for item in world.resources if item.id not in removed]
    return sorted(removed)


def ensure_inventory_capacity(world, extra_resources=0, extra_locations=0, keep_resource_ids=(), keep_location_ids=()):
    if type(extra_resources) is not int or type(extra_locations) is not int or min(extra_resources, extra_locations) < 0:
        raise ValueError("Additional inventory counts must be nonnegative integers")
    if len(world.resources) + extra_resources <= MAX_RESOURCES and len(world.airports) + extra_locations <= MAX_AIRPORTS:
        return
    if world.mode != "live":
        raise _error(422, "Operation inventory capacity reached", {"inventory_capacity_exhausted": True})
    trial = world.model_copy(deep=True)
    candidates = {item.id for item in trial.resources if _retirable(item)}
    protected, _ = protected_inventory(trial, removing_resource_ids=candidates,
        keep_resource_ids=keep_resource_ids, keep_location_ids=keep_location_ids)
    archive_resources(trial, candidates - protected, reason="capacity_pressure")
    _, locations = protected_inventory(trial, keep_resource_ids=keep_resource_ids, keep_location_ids=keep_location_ids)
    if len(trial.airports) + extra_locations > MAX_AIRPORTS:
        removable = [item for item in trial.airports if item.kind != "airport" and item.id not in locations]
        need = len(trial.airports) + extra_locations - MAX_AIRPORTS
        for item in removable[:need]:
            _snapshot(trial, "location", item, "capacity_pressure")
        removed = {item.id for item in removable[:need]}
        trial.airports = [item for item in trial.airports if item.id not in removed]
    if len(trial.resources) + extra_resources > MAX_RESOURCES or len(trial.airports) + extra_locations > MAX_AIRPORTS:
        raise _error(422, "Active inventory capacity is full; protected services and locations were preserved",
                     {"inventory_capacity_exhausted": True})
    world.resources, world.airports = trial.resources, trial.airports
    world._inventory_context = trial._inventory_context


ARCHIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory_archive (
 scenario_id TEXT NOT NULL, entity_type TEXT NOT NULL CHECK(entity_type IN ('resource','location')),
 entity_id TEXT NOT NULL, revision_hash TEXT NOT NULL, archived_at TEXT NOT NULL,
 archived_version INTEGER NOT NULL, reason TEXT NOT NULL, source TEXT, kind TEXT,
 external_offer_id TEXT, origin TEXT, data TEXT NOT NULL, locations TEXT NOT NULL,
 PRIMARY KEY(scenario_id,entity_type,entity_id,revision_hash));
CREATE INDEX IF NOT EXISTS inventory_archive_supplier ON inventory_archive(scenario_id,entity_type,source,external_offer_id);
CREATE INDEX IF NOT EXISTS inventory_archive_origin ON inventory_archive(scenario_id,entity_type,origin,source);
CREATE TABLE IF NOT EXISTS inventory_archive_groups (
 scenario_id TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
 revision_hash TEXT NOT NULL, group_id TEXT NOT NULL,
 PRIMARY KEY(scenario_id,entity_type,entity_id,revision_hash,group_id));
CREATE INDEX IF NOT EXISTS inventory_archive_group ON inventory_archive_groups(scenario_id,group_id,entity_type,entity_id,revision_hash);
CREATE TRIGGER IF NOT EXISTS inventory_archive_no_replace BEFORE INSERT ON inventory_archive
 WHEN EXISTS(SELECT 1 FROM inventory_archive WHERE scenario_id=NEW.scenario_id AND entity_type=NEW.entity_type AND entity_id=NEW.entity_id AND revision_hash=NEW.revision_hash)
 BEGIN SELECT RAISE(IGNORE); END;
CREATE TRIGGER IF NOT EXISTS inventory_archive_groups_no_replace BEFORE INSERT ON inventory_archive_groups
 WHEN EXISTS(SELECT 1 FROM inventory_archive_groups WHERE scenario_id=NEW.scenario_id AND entity_type=NEW.entity_type AND entity_id=NEW.entity_id AND revision_hash=NEW.revision_hash AND group_id=NEW.group_id)
 BEGIN SELECT RAISE(IGNORE); END;
CREATE TRIGGER IF NOT EXISTS inventory_archive_no_update BEFORE UPDATE ON inventory_archive BEGIN SELECT RAISE(ABORT,'Inventory archive is immutable'); END;
CREATE TRIGGER IF NOT EXISTS inventory_archive_no_delete BEFORE DELETE ON inventory_archive BEGIN SELECT RAISE(ABORT,'Inventory archive is immutable'); END;
CREATE TRIGGER IF NOT EXISTS inventory_archive_groups_no_update BEFORE UPDATE ON inventory_archive_groups BEGIN SELECT RAISE(ABORT,'Inventory archive is immutable'); END;
CREATE TRIGGER IF NOT EXISTS inventory_archive_groups_no_delete BEFORE DELETE ON inventory_archive_groups BEGIN SELECT RAISE(ABORT,'Inventory archive is immutable'); END;
"""


def _insert(conn, scenario_id, record, version):
    data = record["data"]
    encoded, locations = _json(data), _json(record["locations"])
    digest = hashlib.sha256((encoded + "\n" + locations).encode()).hexdigest()
    conn.execute("INSERT OR IGNORE INTO inventory_archive VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (scenario_id, record["entity_type"], data["id"], digest, record["archived_at"], version,
         record["reason"], data.get("source"), data.get("kind"), data.get("external_offer_id"), data.get("origin"), encoded, locations))
    conn.executemany("INSERT OR IGNORE INTO inventory_archive_groups VALUES(?,?,?,?,?)",
        [(scenario_id, record["entity_type"], data["id"], digest, group_id) for group_id in data.get("eligible_group_ids", [])])


def persist_inventory(conn, world):
    """Save pending snapshots and catch legacy destructive replacement paths."""
    if not conn.in_transaction:
        raise RuntimeError("Inventory archive persistence requires the world transaction")
    from .models import World
    pending = _context(world).pending if world.mode == "live" else []
    explicit = {(record["entity_type"], _json(record["data"]), _json(record["locations"])) for record in pending}
    previous = World.model_validate_json(conn.execute("SELECT data FROM world WHERE id=1").fetchone()[0])
    if previous.mode == "live":
        same = previous.scenario_id == world.scenario_id
        current_resources = {item.id: item for item in world.resources} if same else {}
        current_locations = {item.id: item for item in world.airports} if same else {}
        # Explicitly changed source snapshots and legacy removals are preserved too.
        for item in previous.resources:
            current = current_resources.get(item.id)
            if current is None or current.model_dump() != item.model_dump():
                _queue_resource(previous, item, "removed_from_active" if current is None else "resource_revision")
        for item in previous.airports:
            current = current_locations.get(item.id)
            if current is None or current.model_dump() != item.model_dump():
                _snapshot(previous, "location", item, "removed_from_active" if current is None else "location_revision")
        for record in _context(previous).pending:
            if not same or (record["entity_type"], _json(record["data"]), _json(record["locations"])) not in explicit:
                _insert(conn, previous.scenario_id, record, world.version)
    if world.mode == "live":
        for record in _context(world).pending:
            _insert(conn, world.scenario_id, record, world.version)
        _context(world).pending.clear()
