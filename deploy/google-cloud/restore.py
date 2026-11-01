"""Install a verified snapshot once. Existing target databases are never replaced."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

from persistence_guard import database


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def restore(source: Path, target: Path, expected_sha256: str, *, uid: int, gid: int) -> dict:
    summary = database(source, restoring=True)
    actual_sha256 = sha256(source)
    if len(expected_sha256) != 64 or actual_sha256 != expected_sha256:
        raise ValueError("Snapshot SHA-256 does not match the explicit transfer checksum.")
    if target.exists() or target.is_symlink() or any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("A target database or sidecar already exists; restore refused. No reset or overwrite is supported.")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".restore-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as incoming:
            shutil.copyfileobj(incoming, output)
            output.flush()
            os.fsync(output.fileno())
        temporary_path = Path(temporary)
        if sha256(temporary_path) != expected_sha256:
            raise ValueError("Snapshot changed during copy.")
        if database(temporary_path, restoring=True) != summary:
            raise ValueError("Snapshot verification changed during copy.")
        os.chown(temporary, uid, gid)
        os.chmod(temporary, 0o600)
        # Atomic no-clobber install even if another process races this script.
        os.link(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"restored": True, "sha256": expected_sha256, **summary}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("sha256")
    args = parser.parse_args()
    try:
        if os.geteuid() != 0 or not os.path.ismount("/mnt/airport-recovery"):
            raise ValueError("Run as root only after mounting the separate persistent data disk.")
        print(json.dumps(restore(args.snapshot, Path("/mnt/airport-recovery/data/recovery.sqlite3"), args.sha256, uid=10001, gid=10001)))
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(str(exc) if isinstance(exc, ValueError) else "Snapshot restore failed; no existing database was replaced.", file=sys.stderr)
        sys.exit(1)
