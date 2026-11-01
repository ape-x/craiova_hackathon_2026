"""Install one reviewed git archive without overlaying any previous release."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tarfile


MANIFEST = "RELEASE_MANIFEST.json"
MAX_FILES = 20_000
MAX_BYTES = 1024 * 1024 * 1024


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def revision_value(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{40}", value):
        raise ValueError("Expected a full reviewed git revision.")
    return value


def archive_name(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or ".." in path.parts or "\\" in value
            or path.as_posix() != value.rstrip("/") or value.rstrip("/") in {MANIFEST, "REVISION"}):
        raise ValueError("Archive contains an unsupported path.")
    return path.as_posix()


def verify_release(target: Path, revision: str) -> dict:
    revision_value(revision)
    if not target.is_dir() or target.is_symlink():
        raise ValueError("Expected a regular installed release directory.")
    marker, manifest_path = target / "REVISION", target / MANIFEST
    if (not marker.is_file() or marker.is_symlink() or marker.stat().st_size > 100
            or marker.read_text() != revision + "\n" or not manifest_path.is_file()
            or manifest_path.is_symlink() or manifest_path.stat().st_size > 4 * 1024 * 1024):
        raise ValueError("Release marker or manifest is missing or invalid.")
    manifest = json.loads(manifest_path.read_text())
    if (not isinstance(manifest, dict) or manifest.get("version") != 1 or manifest.get("revision") != revision
            or not re.fullmatch(r"[a-f0-9]{64}", str(manifest.get("archive_sha256", "")))
            or not isinstance(manifest.get("files"), dict) or not 1 <= len(manifest["files"]) <= MAX_FILES):
        raise ValueError("Release manifest is invalid.")
    actual = set()
    for directory, dirs, files in os.walk(target, followlinks=False):
        for name in dirs:
            if (Path(directory) / name).is_symlink():
                raise ValueError("Release contains an unexpected link.")
        for name in files:
            path = Path(directory) / name
            relative = path.relative_to(target).as_posix()
            if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
                raise ValueError("Release contains an unsupported file.")
            if relative not in {"REVISION", MANIFEST}:
                actual.add(relative)
    if actual != set(manifest["files"]):
        raise ValueError("Release files differ from the installed archive.")
    for name, expected in manifest["files"].items():
        path = target / archive_name(name)
        if (not isinstance(expected, dict) or digest(path) != expected.get("sha256")
                or stat.S_IMODE(path.stat().st_mode) != expected.get("mode")):
            raise ValueError("Release content or permissions changed after installation.")
    return {"revision": revision, "archive_sha256": manifest["archive_sha256"], "files": len(actual)}


def install_release(archive: Path, releases: Path, revision: str, expected_sha256: str) -> dict:
    revision_value(revision)
    if (not archive.is_file() or archive.is_symlink() or archive.stat().st_size > MAX_BYTES
            or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256) or digest(archive) != expected_sha256):
        raise ValueError("Archive is missing or its SHA-256 does not match the reviewed source archive.")
    target = releases / revision
    # Atomic exclusive directory creation prevents overlaying a prior extraction.
    target.mkdir(mode=0o755)
    try:
        manifest = {"version": 1, "revision": revision, "archive_sha256": expected_sha256, "files": {}}
        names, total = set(), 0
        with tarfile.open(archive, mode="r:") as source:
            for item in source:
                name = archive_name(item.name)
                if name in names or len(names) >= MAX_FILES or not (item.isdir() or item.isfile()):
                    raise ValueError("Archive has duplicate paths, too many entries or unsupported links/files.")
                names.add(name)
                total += item.size
                if item.size < 0 or total > MAX_BYTES:
                    raise ValueError("Archive exceeds the release size bound.")
                path = target / name
                if item.isdir():
                    path.mkdir(mode=0o755, parents=True, exist_ok=True)
                    continue
                path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                stream = source.extractfile(item)
                if stream is None:
                    raise ValueError("Archive file is unreadable.")
                with stream, path.open("xb") as output:
                    shutil.copyfileobj(stream, output)
                mode = 0o755 if item.mode & 0o111 else 0o644
                path.chmod(mode)
                manifest["files"][name] = {"sha256": digest(path), "mode": mode}
        if digest(archive) != expected_sha256:
            raise ValueError("Archive changed during installation.")
        (target / "REVISION").write_text(revision + "\n")
        (target / MANIFEST).write_text(json.dumps(manifest, sort_keys=True) + "\n")
        (target / "REVISION").chmod(0o644)
        (target / MANIFEST).chmod(0o644)
        target.chmod(0o755)
        return verify_release(target, revision)
    except BaseException:
        shutil.rmtree(target)
        raise


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "verify":
        return verify_release(Path(sys.argv[2]), sys.argv[3])
    if len(sys.argv) == 5 and sys.argv[1] == "install":
        if os.geteuid() != 0:
            raise ValueError("Run the release installation as root.")
        return install_release(Path(sys.argv[2]), Path("/opt/airport-recovery/releases"), sys.argv[3], sys.argv[4])
    raise ValueError("Usage: install_release.py install ARCHIVE REVISION SHA256 | verify DIRECTORY REVISION")


if __name__ == "__main__":
    try:
        print(json.dumps(main()))
    except (ValueError, OSError, tarfile.TarError) as exc:
        print(str(exc) if isinstance(exc, ValueError) else "Release installation/verification failed; no existing release was replaced.", file=sys.stderr)
        sys.exit(1)
