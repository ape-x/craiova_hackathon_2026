"""Versioned, salted password hashes; no plaintext passwords are persisted."""
import hashlib
import hmac
import re
import secrets


ITERATIONS = 600_000
_HASH = re.compile(r"pbkdf2_sha256\$600000\$([0-9a-f]{32})\$([0-9a-f]{64})")


def valid_password_hash(value: str) -> bool:
    return _HASH.fullmatch(value) is not None


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 1024:
        raise ValueError("Use a password between 12 and 1024 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    match = _HASH.fullmatch(encoded)
    if not match:
        return False
    salt, expected = match.groups()
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
    return hmac.compare_digest(actual, bytes.fromhex(expected))
