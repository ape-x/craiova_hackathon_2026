"""Fail-closed deployment checks. No application imports or provider requests."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
import sys


# Committed application schema (69b252a): losing a table is not a migration.
# It can erase an unknown dispatch, cancellation, consumed allowance or prior
# source revision. Only observations, flight_lookups, transfer_quote_cache and
# airport_cab_cache are transient and may be recreated empty.
DURABLE_TABLES = frozenset({
    "world", "idempotency", "audit", "controls", "leases", "request_claims",
    "world_archives", "inventory_archive", "inventory_archive_groups",
    "recovery_jobs", "recovery_steps", "recovery_cancellations",
    "live_command_intents", "transfer_quote_receipts", "airport_cab_receipts",
    "airport_cab_profiles", "after_flight_profiles", "romanian_hotel_profiles",
})


def database(path: Path, *, restoring: bool = False, quiescent: bool = False) -> dict:
    if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
        raise ValueError("An existing regular SQLite snapshot is required; no empty database is initialized.")
    if restoring and any(Path(str(path) + suffix).exists() and Path(str(path) + suffix).stat().st_size
                         for suffix in ("-wal", "-journal")):
        raise ValueError("Restore a standalone snapshot produced by scripts/backup.py, not a live WAL database.")
    uri = path.resolve().as_uri() + ("?mode=ro&immutable=1" if restoring else "?mode=ro")
    with sqlite3.connect(uri, uri=True, timeout=15) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("SQLite integrity check failed.")
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("SQLite foreign-key check failed.")
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not DURABLE_TABLES <= tables:
            raise ValueError("The snapshot does not contain the authoritative application and request ledgers.")
        rows = conn.execute("SELECT id,data FROM world").fetchall()
        if len(rows) != 1 or rows[0][0] != 1:
            raise ValueError("Expected exactly one authoritative world.")
        world = json.loads(rows[0][1])
        if (world.get("mode") != "live" or type(world.get("version")) is not int
                or not isinstance(world.get("groups"), list) or not isinstance(world.get("resources"), list)):
            raise ValueError("Expected an existing live world, not synthetic inventory or a blank seed.")
        active = conn.execute("SELECT count(*) FROM recovery_jobs WHERE status IN ('queued','running')").fetchone()[0]
        traffic = conn.execute("SELECT value FROM controls WHERE key='traffic_running'").fetchone()
        if (restoring or quiescent) and (active or (traffic and json.loads(traffic[0]) is not False)):
            raise ValueError("Snapshot is not quiescent: stop traffic and drain jobs before the final snapshot.")
        # A migrated deployment must not regain its original provider allowance.
        lease = conn.execute("SELECT count FROM leases WHERE name='fr24'").fetchone()
        credits = conn.execute("SELECT value FROM controls WHERE key='fr24_credits_reserved'").fetchone()
        if lease is None or credits is None:
            raise ValueError("FR24 usage records are missing; refusing an implicit budget reset.")
        credit_count = json.loads(credits[0])
        if type(lease[0]) is not int or lease[0] < 0 or type(credit_count) is not int or credit_count < 0:
            raise ValueError("Invalid retained provider counters.")
        summary = {"world_version": world["version"], "groups": len(world["groups"]),
                   "resources": len(world["resources"]), "active_jobs": active,
                   "fr24_requests": lease[0], "fr24_credits": credit_count,
                   "recovery_steps": conn.execute("SELECT count(*) FROM recovery_steps").fetchone()[0]}
        summary["durable_rows"] = {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                                   for table in sorted(DURABLE_TABLES)}
        for table in ("airport_cab_receipts", "airport_cab_profiles", "after_flight_profiles", "romanian_hotel_profiles"):
            if table in tables:
                summary[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        return summary


def environment(path: Path) -> None:
    if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError("The private backend env file must be a regular file with mode 0600 or 0400.")
    if path.stat().st_size > 100_000:
        raise ValueError("Unexpected env file size.")
    credentials = {}
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        key = key.strip()
        if separator and key in {"OPERATOR_USERNAME", "OPERATOR_PASSWORD_HASH", "OPERATOR_TOKEN"}:
            if key in credentials:
                raise ValueError("Operator credentials must have exactly one definition each.")
            credentials[key] = value.strip()
    username = credentials.get("OPERATOR_USERNAME", "")
    if username.startswith("'") and username.endswith("'"):
        username = username[1:-1]
    # Match configure_operator.py's output. Single quotes keep '$' in hashes
    # literal when Compose reads the private env file; no app imports are needed.
    if (not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", username)
            or not re.fullmatch(r"'pbkdf2_sha256\$600000\$[0-9a-f]{32}\$[0-9a-f]{64}'",
                                credentials.get("OPERATOR_PASSWORD_HASH", ""))):
        raise ValueError("Configure OPERATOR_USERNAME and a single-quoted OPERATOR_PASSWORD_HASH with backend/scripts/configure_operator.py.")
    token = credentials.get("OPERATOR_TOKEN", "")
    if token and not re.fullmatch(r"[A-Za-z0-9_-]{32,}", token):
        raise ValueError("Optional OPERATOR_TOKEN must be a literal URL-safe value of at least 32 characters; its value is never printed.")


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in {"database", "quiescent", "environment"}:
        raise ValueError("Usage: persistence_guard.py database|quiescent|environment PATH")
    result = (database(Path(sys.argv[2]), quiescent=sys.argv[1] == "quiescent")
              if sys.argv[1] != "environment" else environment(Path(sys.argv[2])))
    if result is not None:
        print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, sqlite3.Error) as exc:
        # Do not include SQLite contents, environment text or upstream errors.
        print(str(exc) if isinstance(exc, ValueError) else "Deployment persistence check failed.", file=sys.stderr)
        sys.exit(1)
