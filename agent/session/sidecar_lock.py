"""Process-local locking shared by session sidecar metadata services."""

from __future__ import annotations

from pathlib import Path
from threading import Lock, RLock

_LOCKS_GUARD = Lock()
_LOCKS: dict[Path, RLock] = {}


def session_sidecar_lock(path: Path) -> RLock:
    """Return one stable lock for all services mutating the same sidecar path."""

    resolved = path.resolve()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(resolved, RLock())
