"""Opaque auth token generation and hashing helpers."""

from __future__ import annotations

import hashlib
import secrets


def generate_token() -> str:
    """Return a URL-safe opaque token suitable for an HttpOnly cookie."""

    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """Return the one-way SHA-256 digest persisted in SQLite."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()

