"""Repository-wide pytest configuration."""

from __future__ import annotations

import pytest

from agent.auth import passwords

TEST_PBKDF2_ITERATIONS = 2_000


@pytest.fixture(autouse=True)
def _use_fast_password_hashing(monkeypatch):
    """Keep auth tests fast without weakening the production hash setting."""

    monkeypatch.setattr(passwords, "PBKDF2_ITERATIONS", TEST_PBKDF2_ITERATIONS)
