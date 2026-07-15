"""Password hashing helpers using only the Python standard library."""

from __future__ import annotations

import hashlib
import hmac
import secrets

PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16


def hash_password(password: str, *, salt_hex: str | None = None) -> tuple[str, str]:
    """Return a PBKDF2-SHA256 password hash and per-user salt."""

    _validate_password(password)
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return digest.hex(), salt.hex()


def verify_password(password: str, password_hash: str, password_salt: str) -> bool:
    """Verify a password without leaking timing differences in the digest compare."""

    if not password or not password_hash or not password_salt:
        return False
    try:
        candidate, _ = hash_password(password, salt_hex=password_salt)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, password_hash)


def _validate_password(password: str) -> None:
    """Enforce the small first-stage password boundary."""

    if not isinstance(password, str) or len(password) < 8:
        raise ValueError("password must contain at least 8 characters")
    if len(password) > 1024:
        raise ValueError("password is too long")

