"""Cross-platform single-Gateway workspace lock."""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO


class WorkspaceGatewayLockError(RuntimeError):
    """Raised when another Gateway already owns the workspace."""


class WorkspaceGatewayLock:
    """Hold one OS-backed exclusive lock for a Gateway workspace."""

    def __init__(self, workspace: Path | str):
        self.path = Path(workspace).expanduser().resolve() / "state" / "gateway.lock"
        self._file: BinaryIO | None = None

    def acquire(self) -> None:
        """Acquire the lock without waiting or fail with a stable startup error."""

        if self._file is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            _lock_file(handle)
        except OSError as exc:
            handle.close()
            raise WorkspaceGatewayLockError(
                f"workspace already has an active Gateway: {self.path.parent.parent}"
            ) from exc
        self._file = handle

    def release(self) -> None:
        """Release the OS lock; the metadata file intentionally remains reusable."""

        handle = self._file
        if handle is None:
            return
        self._file = None
        try:
            _unlock_file(handle)
        finally:
            handle.close()


def _lock_file(handle: BinaryIO) -> None:
    handle.flush()
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        if handle.read(1) == b"":
            handle.seek(0)
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        os.lseek(handle.fileno(), 0, os.SEEK_SET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle: BinaryIO) -> None:
    handle.flush()
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        os.lseek(handle.fileno(), 0, os.SEEK_SET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
