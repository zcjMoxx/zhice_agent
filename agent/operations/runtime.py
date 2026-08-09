"""Safe, non-secret runtime projection for the active Ops endpoint."""

from __future__ import annotations

import ctypes
import json
import os
import tempfile
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

STATE_FILE_NAME = "operations.json"
VALID_MODES = {"local_process", "local_docker", "server_docker"}
VALID_TARGET_TYPES = {"process", "container"}


@dataclass(frozen=True)
class OperationsRuntimeState:
    """Validated active endpoint published by a trusted launcher."""

    mode: str
    target_type: str
    target_name: str
    url: str
    presentation: str = "both"
    instance_id: str = ""
    supervisor_pid: int = 0
    updated_at: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def runtime_state_path(state_dir: Path) -> Path:
    return state_dir / STATE_FILE_NAME


def write_operations_runtime_state(state_dir: Path, state: OperationsRuntimeState) -> None:
    """Atomically publish one launcher-owned endpoint without credentials."""

    state_dir.mkdir(parents=True, exist_ok=True)
    target = runtime_state_path(state_dir)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".operations.", dir=state_dir)
    temporary = Path(temporary_name)
    payload = state.to_dict()
    payload["updated_at"] = datetime.now(UTC).isoformat()
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=True, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def clear_operations_runtime_state(state_dir: Path, *, instance_id: str) -> None:
    """Remove only the state owned by the caller's supervisor instance."""

    target = runtime_state_path(state_dir)
    current = load_operations_runtime_state(state_dir, require_live_process=False)
    if current is not None and current.instance_id == instance_id:
        target.unlink(missing_ok=True)


def load_operations_runtime_state(
    state_dir: Path,
    *,
    require_live_process: bool = True,
) -> OperationsRuntimeState | None:
    """Load state defensively and reject stale local supervisor records."""

    path = runtime_state_path(state_dir)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        state = OperationsRuntimeState(
            mode=str(raw.get("mode", "")),
            target_type=str(raw.get("target_type", "")),
            target_name=str(raw.get("target_name", "")),
            url=str(raw.get("url", "")),
            presentation=str(raw.get("presentation", "both")),
            instance_id=str(raw.get("instance_id", "")),
            supervisor_pid=int(raw.get("supervisor_pid", 0)),
            updated_at=str(raw.get("updated_at", "")),
        )
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if state.mode not in VALID_MODES or state.target_type not in VALID_TARGET_TYPES:
        return None
    if not state.target_name or state.presentation not in {"new_tab", "embed", "both"}:
        return None
    if not _valid_runtime_url(state.url, mode=state.mode):
        return None
    if (
        require_live_process
        and state.mode == "local_process"
        and not _pid_is_current(state.supervisor_pid)
    ):
        return None
    return state


def state_from_environment() -> OperationsRuntimeState | None:
    """Read a trusted non-secret projection injected by Docker/server launchers."""

    url = os.getenv("ZHICE_OPS_URL", "").strip()
    mode = os.getenv("ZHICE_OPS_MODE", "").strip()
    target_type = os.getenv("ZHICE_OPS_TARGET_TYPE", "").strip()
    target_name = os.getenv("ZHICE_OPS_TARGET_NAME", "").strip()
    if not url and not mode and not target_type and not target_name:
        return None
    state = OperationsRuntimeState(
        mode=mode,
        target_type=target_type,
        target_name=target_name,
        url=url,
        presentation=os.getenv("ZHICE_OPS_PRESENTATION", "both").strip() or "both",
    )
    if state.mode not in VALID_MODES or state.target_type not in VALID_TARGET_TYPES:
        return None
    if not state.target_name or state.presentation not in {"new_tab", "embed", "both"}:
        return None
    return state if _valid_runtime_url(state.url, mode=state.mode) else None


def _valid_runtime_url(url: str, *, mode: str) -> bool:
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(url)
        _ = parsed.port
    except ValueError:
        return False
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
        return False
    if mode in {"local_process", "local_docker"}:
        return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    return parsed.scheme == "https"


def _pid_is_current(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_is_current(pid)
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def _windows_pid_is_current(pid: int) -> bool:
    """Query a Windows process without using os.kill(pid, 0), which terminates it."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == 259
    finally:
        kernel32.CloseHandle(handle)
