#!/usr/bin/env python3
"""Create/change the deployment's operator account without storing its password."""
import argparse
import getpass
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.passwords import hash_password


def configure(path: Path, username: str, password: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", username):
        raise ValueError("Username must contain 1–128 letters, numbers, _, ., @ or -")
    values = {"OPERATOR_USERNAME": username, "OPERATOR_PASSWORD_HASH": hash_password(password)}
    original = path.read_text() if path.exists() else ""
    lines = [line for line in original.splitlines()
             if not re.match(r"^\s*(?:export\s+)?(?:OPERATOR_USERNAME|OPERATOR_PASSWORD_HASH)\s*=", line)]
    # Literal single quotes prevent Compose from interpolating the hash's '$'.
    lines.extend(f"{key}='{value}'" for key, value in values.items())
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".operator-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write("\n".join(lines) + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--username", default="operator")
    parser.add_argument("--generate", action="store_true", help="Generate and display a random password once")
    args = parser.parse_args()
    password = secrets.token_urlsafe(24) if args.generate else getpass.getpass("Password (at least 12 characters): ")
    if not args.generate and password != getpass.getpass("Repeat password: "):
        parser.error("Passwords do not match")
    try:
        configure(args.env_file.resolve(), args.username, password)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Configured user {args.username} in {args.env_file}. Restart the API to apply.")
    if args.generate:
        print(f"Password (save it now): {password}")


if __name__ == "__main__":
    main()
