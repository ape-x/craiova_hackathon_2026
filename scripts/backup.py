"""Back up operational SQLite state, including WAL changes, without transient feed data."""
import argparse
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def without_observations(response):
    """Match Store's cache redaction without importing application/database code."""
    result = {**response}
    if isinstance(result.get("world"), dict):
        world = {**result["world"], "observations": []}
        world["provider_status"] = {key: value for key, value in world.get("provider_status", {}).items() if key != "flights"}
        if "incidents" in world:
            world["incidents"] = [{**case, "flight": None, "flight_context_state": "expired"} for case in world["incidents"]]
        result["world"] = world
    result.pop("observations", None)
    return result


parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
parser.add_argument("destination", type=Path)
args = parser.parse_args()
if not args.source.is_file():
    parser.error("Source database does not exist")
if args.destination.exists():
    parser.error("Destination already exists; choose a new snapshot path")
args.destination.parent.mkdir(parents=True, exist_ok=True)
try:
    descriptor=os.open(args.destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
except FileExistsError:
    parser.error("Destination already exists; choose a new snapshot path")
os.close(descriptor)
try:
    with closing(sqlite3.connect(args.source.resolve().as_uri()+"?mode=ro",uri=True)) as source:
        with closing(sqlite3.connect(args.destination)) as destination:
            source.backup(destination)
            # Aircraft observations are a bounded cache, not archival scenario
            # data. Omit them so a backup cannot extend their cache lifetime.
            tables = {row[0] for row in destination.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            destination.execute("PRAGMA secure_delete=ON")
            if "observations" in tables:
                destination.execute("DELETE FROM observations")
            if "flight_lookups" in tables:
                destination.execute("DELETE FROM flight_lookups")
            # Search-result bodies are temporary. The canonical request and
            # dispatch/outcome ledger remain, but a historical backup must never
            # automatically resume work that may have run after it was captured.
            unknown_jobs=set()
            if "recovery_steps" in tables:
                destination.execute("UPDATE recovery_steps SET result=NULL")
                for step_id,job_id,raw in destination.execute("SELECT id,job_id,data FROM recovery_steps").fetchall():
                    step=json.loads(raw)
                    if step["status"]=="dispatched":
                        unknown_jobs.add(job_id)
                        step.update(status="unknown",finished_at=datetime.now(timezone.utc).isoformat(),
                            message="Dispatch was in progress when this backup was captured. Its outcome is unknown; restoration will not retry it.")
                        destination.execute("UPDATE recovery_steps SET data=? WHERE id=?",(json.dumps(step),step_id))
            if "recovery_jobs" in tables:
                for job_id,raw in destination.execute("SELECT id,data FROM recovery_jobs WHERE status IN ('queued','running')").fetchall():
                    job=json.loads(raw)
                    job.update(status="unknown_outcome" if job_id in unknown_jobs else "needs_review",stage="finished",
                        message="Historical job from a backup. No request resumes automatically after restoration; review before starting an explicit new attempt.")
                    destination.execute("UPDATE recovery_jobs SET status=?,owner=NULL,lease_until=0,data=? WHERE id=?",(job["status"],json.dumps(job),job_id))
            # Old snapshots may predate startup migration or the separate cache
            # table. Sanitize only the copy, preserving all operational fields.
            for table, identifier, column, is_response in (
                ("world", "id", "data", False),
                ("idempotency", "key", "result", True),
                ("world_archives", "id", "data", False),
            ):
                if table not in tables:
                    continue
                for key, raw in destination.execute(f"SELECT {identifier},{column} FROM {table}").fetchall():
                    value = json.loads(raw)
                    cleaned = without_observations(value) if is_response else without_observations({"world": value})["world"]
                    destination.execute(f"UPDATE {table} SET {column}=? WHERE {identifier}=?", (json.dumps(cleaned), key))
            destination.commit()
            destination.execute("VACUUM")
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")
except BaseException:
    args.destination.unlink(missing_ok=True)
    raise
print(f"Verified snapshot saved to {args.destination}")
