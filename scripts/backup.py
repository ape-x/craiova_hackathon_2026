"""Back up operational SQLite state, including WAL changes, without transient feed data."""
import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path

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
            if destination.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='observations'").fetchone():
                destination.execute("PRAGMA secure_delete=ON")
                destination.execute("DELETE FROM observations")
                destination.commit()
                destination.execute("VACUUM")
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")
except BaseException:
    args.destination.unlink(missing_ok=True)
    raise
print(f"Verified snapshot saved to {args.destination}")
