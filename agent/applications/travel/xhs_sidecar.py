"""Gateway-owned local Xiaohongshu browser sidecar lifecycle."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from agent.logging_utils import log_event
from agent.process_tree import ManagedProcessTree
from agent.protocols.mcp import McpServerSpec

sidecar_logger = logging.getLogger("zcagent.agent.travel")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_RESTART_BACKOFF_SECONDS = (1.0, 2.0, 5.0, 10.0, 30.0)
_LOGIN_COOKIE_STABLE_SECONDS = 0.6
_LOGIN_EXIT_GRACE_SECONDS = 2.0


class LocalXhsSidecarSupervisor:
    """Own the optional browser service only for a configured local upstream."""

    def __init__(self, workspace: Path | str, spec: McpServerSpec | None):
        self.workspace = Path(workspace).expanduser().resolve()
        self.spec = spec
        self.host, self.port = _local_upstream(spec)
        self.binary = _binary_path(self.workspace)
        self.login_binary = _login_binary_path(self.workspace)
        self.data_dir = (self.workspace / "integrations" / "xhs" / "data").resolve()
        self.cookie_file = _cookie_path(self.workspace, self.data_dir, spec)
        self._cookie_signature = _file_signature(self.cookie_file)
        self._tree: ManagedProcessTree | None = None
        self._login_tree: ManagedProcessTree | None = None
        self._login_thread: threading.Thread | None = None
        self._login_syncing = False
        self._login_state = "unknown"
        self._login_code = "XHS_AUTH_NOT_CHECKED"
        self._login_message = "Login status has not been checked."
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._stdout = None
        self._stderr = None

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.port and self.binary)

    @classmethod
    def from_specs(
        cls, workspace: Path | str, specs: tuple[McpServerSpec, ...]
    ) -> LocalXhsSidecarSupervisor:
        spec = next((item for item in specs if item.server_id == "xhs-readonly"), None)
        return cls(workspace, spec)

    def start(self) -> bool:
        """Ensure one local listener before MCP adapters connect, then watch it."""

        if not self.enabled:
            return False
        if not self._port_ready() and not self._spawn_and_wait():
            return False
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._watch,
                name="zcagent-xhs-sidecar",
                daemon=True,
            )
            self._thread.start()
        return True

    def stop(self) -> None:
        """Stop only a process tree created by this supervisor."""

        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        login_thread = self._login_thread
        if login_thread is not None and login_thread is not threading.current_thread():
            login_thread.join(timeout=3.0)
        self._login_thread = None
        with self._lock:
            self._terminate_owned()
            self._terminate_login()
        log_event(sidecar_logger, logging.INFO, "travel.xhs_sidecar_stopped")

    def admin_snapshot(self) -> dict[str, object]:
        """Return credential-free local management state for the Owner UI."""

        with self._lock:
            login_in_progress = self._login_syncing
            restart_supported = self.enabled and self._tree is not None
            state = "login_pending" if login_in_progress else self._login_state
            code = "XHS_LOGIN_STARTED" if login_in_progress else self._login_code
            message = (
                "Waiting for the Xiaohongshu login to complete."
                if login_in_progress
                else self._login_message
            )
        updated_at = ""
        if self.cookie_file is not None:
            try:
                updated_at = datetime.fromtimestamp(
                    self.cookie_file.stat().st_mtime,
                    UTC,
                ).isoformat(timespec="seconds")
            except OSError:
                pass
        return {
            "enabled": self.enabled,
            "login_supported": bool(os.name == "nt" and self.login_binary),
            "login_in_progress": login_in_progress,
            "restart_supported": restart_supported,
            "cookie_updated_at": updated_at,
            "state": state,
            "code": code,
            "message": message,
        }

    def record_login_status(self, state: str, code: str, message: str) -> None:
        """Keep only the latest credential-free login check for page refreshes."""

        with self._lock:
            self._login_state = state
            self._login_code = code
            self._login_message = message

    def start_login(self) -> str:
        """Open the fixed local login helper without exposing its process details."""

        if os.name != "nt" or self.login_binary is None or self.cookie_file is None:
            return "XHS_LOGIN_UNSUPPORTED"
        login_thread = None
        with self._lock:
            if self._login_syncing or self._login_tree is not None:
                return "XHS_LOGIN_ALREADY_RUNNING"
            self.data_dir.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env["COOKIES_PATH"] = str(self.cookie_file)
            cookie_before = _file_signature(self.cookie_file)
            try:
                self._login_tree = ManagedProcessTree.spawn(
                    [str(self.login_binary)],
                    cwd=self.data_dir,
                    env=env,
                    shell=False,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
            except (OSError, ValueError) as exc:
                log_event(
                    sidecar_logger,
                    logging.ERROR,
                    "travel.xhs_login_start_failed",
                    error_type=type(exc).__name__,
                )
                return "XHS_LOGIN_START_FAILED"
            self._login_syncing = True
            self._login_state = "login_pending"
            self._login_code = "XHS_LOGIN_STARTED"
            self._login_message = "The Xiaohongshu login window is open."
            login_thread = threading.Thread(
                target=self._watch_login,
                args=(self._login_tree, cookie_before),
                name="zcagent-xhs-login",
                daemon=True,
            )
            self._login_thread = login_thread
        login_thread.start()
        log_event(sidecar_logger, logging.INFO, "travel.xhs_login_started")
        return "XHS_LOGIN_STARTED"

    def restart(self) -> str:
        """Restart only the local sidecar tree owned by this supervisor."""

        if not self.enabled:
            return "XHS_RESTART_UNAVAILABLE"
        with self._lock:
            if self._tree is None:
                return "XHS_RESTART_NOT_OWNED"
            self._terminate_owned()
        if self._spawn_and_wait():
            self.record_login_status(
                "unknown",
                "XHS_AUTH_RECHECK_PENDING",
                "The Xiaohongshu sidecar restarted; login will be checked again.",
            )
            return "XHS_RESTARTED"
        return "XHS_RESTART_FAILED"

    def _watch(self) -> None:
        attempt = 0
        while not self._stop.wait(3.0):
            if self._port_ready():
                attempt = 0
                with self._lock:
                    login_syncing = self._login_syncing
                    owned = self._tree is not None
                if not login_syncing and owned and self._cookie_changed():
                    log_event(
                        sidecar_logger,
                        logging.INFO,
                        "travel.xhs_sidecar_cookie_reloading",
                    )
                    with self._lock:
                        self._terminate_owned()
                    if self._spawn_and_wait():
                        self.record_login_status(
                            "unknown",
                            "XHS_AUTH_RECHECK_PENDING",
                            "The Xiaohongshu Cookie changed; login will be checked again.",
                        )
                    else:
                        attempt = 1
                continue
            delay = _RESTART_BACKOFF_SECONDS[min(attempt, len(_RESTART_BACKOFF_SECONDS) - 1)]
            log_event(
                sidecar_logger,
                logging.WARNING,
                "travel.xhs_sidecar_restart_scheduled",
                delay_seconds=delay,
                attempt=attempt + 1,
            )
            if self._stop.wait(delay):
                return
            if self._spawn_and_wait():
                attempt = 0
            else:
                attempt += 1

    def _spawn_and_wait(self) -> bool:
        with self._lock:
            if self._port_ready():
                return True
            self._terminate_owned()
            if self.binary is None or not self.binary.is_file():
                return False
            self.data_dir.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            if self.cookie_file is not None:
                env["COOKIES_PATH"] = str(self.cookie_file)
            try:
                self._stdout = (self.data_dir / "service.stdout.log").open("ab")
                self._stderr = (self.data_dir / "service.stderr.log").open("ab")
                self._tree = ManagedProcessTree.spawn(
                    [
                        str(self.binary),
                        "-headless=true",
                        f"-port={self.host}:{self.port}",
                    ],
                    cwd=self.data_dir,
                    stdin=subprocess.DEVNULL,
                    stdout=self._stdout,
                    stderr=self._stderr,
                    env=env,
                    shell=False,
                    **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
                )
            except (OSError, ValueError) as exc:
                self._close_logs()
                log_event(
                    sidecar_logger,
                    logging.ERROR,
                    "travel.xhs_sidecar_start_failed",
                    error_type=type(exc).__name__,
                )
                return False
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline and not self._stop.is_set():
            if self._port_ready():
                self._cookie_signature = _file_signature(self.cookie_file)
                log_event(
                    sidecar_logger,
                    logging.INFO,
                    "travel.xhs_sidecar_ready",
                    host=self.host,
                    port=self.port,
                )
                return True
            tree = self._tree
            if tree is None or tree.process.poll() is not None:
                break
            time.sleep(0.1)
        with self._lock:
            self._terminate_owned()
        log_event(
            sidecar_logger,
            logging.ERROR,
            "travel.xhs_sidecar_start_timeout",
            host=self.host,
            port=self.port,
        )
        return False

    def _cookie_changed(self) -> bool:
        current = _file_signature(self.cookie_file)
        if (
            current is None
            or current == self._cookie_signature
            or not _valid_cookie_file(self.cookie_file)
        ):
            return False
        self._cookie_signature = current
        return True

    def _watch_login(
        self,
        tree: ManagedProcessTree,
        cookie_before: tuple[int, str] | None,
    ) -> None:
        """Converge helper exit or stable Cookie update into one ready sidecar."""

        candidate: tuple[int, str] | None = None
        candidate_since = 0.0
        exit_deadline = 0.0
        cookie_after: tuple[int, str] | None = None
        while not self._stop.wait(0.2):
            now = time.monotonic()
            current = _file_signature(self.cookie_file)
            if (
                current is not None
                and current != cookie_before
                and _valid_cookie_file(self.cookie_file)
            ):
                if current != candidate:
                    candidate = current
                    candidate_since = now
                elif now - candidate_since >= _LOGIN_COOKIE_STABLE_SECONDS:
                    cookie_after = current
                    break
            if tree.process.poll() is not None:
                if not exit_deadline:
                    exit_deadline = now + _LOGIN_EXIT_GRACE_SECONDS
                elif now >= exit_deadline:
                    break
        if self._stop.is_set():
            return

        with self._lock:
            if self._login_tree is tree:
                tree.terminate(grace_seconds=0.5)
                self._login_tree = None

        reloaded = True
        if cookie_after is not None:
            log_event(
                sidecar_logger,
                logging.INFO,
                "travel.xhs_login_cookie_detected",
            )
            with self._lock:
                self._terminate_owned()
            reloaded = self._spawn_and_wait()

        with self._lock:
            self._login_syncing = False
            if self._login_thread is threading.current_thread():
                self._login_thread = None
            if cookie_after is not None and not reloaded:
                self._login_state = "unavailable"
                self._login_code = "XHS_RESTART_FAILED"
                self._login_message = "The Xiaohongshu sidecar could not load the new Cookie."
            else:
                self._login_state = "unknown"
                self._login_code = "XHS_AUTH_RECHECK_PENDING"
                self._login_message = "Login credentials changed; login will be checked again."
        log_event(
            sidecar_logger,
            logging.INFO if reloaded else logging.ERROR,
            "travel.xhs_login_sync_finished",
            cookie_updated=cookie_after is not None,
            sidecar_ready=reloaded,
        )

    def _port_ready(self) -> bool:
        if not self.host or not self.port:
            return False
        try:
            with socket.create_connection((self.host, self.port), timeout=0.5):
                return True
        except OSError:
            return False

    def _terminate_owned(self) -> None:
        tree, self._tree = self._tree, None
        if tree is not None:
            tree.terminate(grace_seconds=2.0)
        self._close_logs()

    def _close_logs(self) -> None:
        for handle_name in ("_stdout", "_stderr"):
            handle = getattr(self, handle_name)
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
                setattr(self, handle_name, None)

    def _terminate_login(self) -> None:
        tree, self._login_tree = self._login_tree, None
        if tree is not None:
            tree.terminate(grace_seconds=0.5)
        self._login_syncing = False


def _local_upstream(spec: McpServerSpec | None) -> tuple[str, int]:
    if spec is None:
        return "", 0
    value = str(spec.env.get("XHS_READONLY_UPSTREAM_URL") or "").strip()
    parsed = urlsplit(value)
    host = str(parsed.hostname or "").casefold()
    if parsed.scheme != "http" or host not in _LOOPBACK_HOSTS:
        return "", 0
    try:
        port = parsed.port or 80
    except ValueError:
        return "", 0
    if not 1 <= port <= 65535:
        return "", 0
    return "127.0.0.1", port


def _binary_path(workspace: Path) -> Path | None:
    fallback_names = {
        "win32": "xiaohongshu-mcp-windows-amd64.exe",
        "linux": "xiaohongshu-mcp-linux-amd64",
        "darwin": "xiaohongshu-mcp-darwin-arm64",
    }
    rednote_patterns = {
        "win32": "xiaohongshu-mcp-rednote-v*.exe",
        "linux": "xiaohongshu-mcp-rednote-v*-linux-amd64",
        "darwin": "xiaohongshu-mcp-rednote-v*-darwin-arm64",
    }
    fallback_name = fallback_names.get(sys.platform)
    pattern = rednote_patterns.get(sys.platform)
    if not fallback_name or not pattern:
        return None
    bin_dir = (workspace / "integrations" / "xhs" / "bin").resolve()
    rednote_candidates = sorted(
        bin_dir.glob(pattern),
        key=_rednote_version,
        reverse=True,
    )
    for candidate in [*rednote_candidates, bin_dir / fallback_name]:
        path = candidate.resolve()
        try:
            path.relative_to(workspace)
        except ValueError:
            continue
        if path.is_file():
            return path
    return None


def _rednote_version(path: Path) -> tuple[int, ...]:
    match = re.search(r"-v(\d+(?:\.\d+)*)", path.name.casefold())
    if match is None:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def _login_binary_path(workspace: Path) -> Path | None:
    if sys.platform != "win32":
        return None
    bin_dir = (workspace / "integrations" / "xhs" / "bin").resolve()
    candidates = [
        bin_dir / "xiaohongshu-login-windows-amd64.exe",
        *sorted(
            bin_dir.glob("xiaohongshu-login-rednote-v*.exe"),
            key=_rednote_version,
            reverse=True,
        ),
    ]
    for candidate in candidates:
        path = candidate.resolve()
        try:
            path.relative_to(workspace)
        except ValueError:
            continue
        if path.is_file():
            return path
    return None


def _cookie_path(
    workspace: Path,
    data_dir: Path,
    spec: McpServerSpec | None,
) -> Path | None:
    configured = str(
        (spec.env if spec is not None else {}).get("XHS_READONLY_COOKIE_FILE") or ""
    ).strip()
    if not configured:
        return None
    candidate = Path(configured).expanduser()
    if not candidate.is_absolute():
        candidate = data_dir / candidate
    path = candidate.resolve(strict=False)
    try:
        path.relative_to(workspace)
    except ValueError:
        return None
    return path


def _file_signature(path: Path | None) -> tuple[int, str] | None:
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    if not path.is_file() or stat.st_size <= 0:
        return None
    try:
        content = path.read_bytes()
    except OSError:
        return None
    return stat.st_size, hashlib.sha256(content).hexdigest()


def _valid_cookie_file(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        return False
    return isinstance(payload, list | dict)


__all__ = ["LocalXhsSidecarSupervisor"]
