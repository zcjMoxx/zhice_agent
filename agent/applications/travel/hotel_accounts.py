"""Owner-managed lifecycle for the local read-only hotel browser account."""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

from agent.applications.travel.account_credentials import (
    CredentialStoreError,
    EnvironmentPlatformCredentialStore,
)
from agent.logging_utils import log_event
from agent.process_tree import ManagedProcessTree

hotel_account_logger = logging.getLogger("zcagent.agent.travel")
_PROVIDER = "ctrip"


class HotelAccountSupervisor:
    """Persist environment credentials and coordinate one visible login helper."""

    def __init__(
        self,
        workspace: Path | str,
        *,
        credential_store: EnvironmentPlatformCredentialStore | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.store = credential_store or EnvironmentPlatformCredentialStore(self.workspace)
        self.state_dir = (
            self.workspace / "state" / "platform_accounts" / _PROVIDER
        ).resolve()
        self.status_file = (self.state_dir / "status.json").resolve()
        self.profile_dir = (
            self.workspace / "state" / "browser_profiles" / _PROVIDER
        ).resolve()
        self._tree: ManagedProcessTree | None = None
        self._watcher: threading.Thread | None = None
        self._lock = threading.Lock()

    def admin_snapshot(self) -> dict[str, object]:
        """Return a credential-free status projection for the Owner UI."""

        status = self._read_status()
        with self._lock:
            process = self._tree.process if self._tree is not None else None
            login_in_progress = process is not None and process.poll() is None
        configured = self.store.configured(_PROVIDER)
        state = "login_pending" if login_in_progress else str(status.get("state") or "unknown")
        if not configured:
            state = "not_configured"
        code = str(status.get("code") or "HOTEL_AUTH_NOT_CHECKED")
        message = str(status.get("message") or "Hotel account login has not been checked.")
        if not configured:
            code = "HOTEL_CREDENTIALS_NOT_CONFIGURED"
            message = "Ctrip credentials have not been configured."
        return {
            "provider": _PROVIDER,
            "state": state,
            "code": code,
            "message": message,
            "credential_store_supported": self.store.available,
            "credential_configured": configured,
            "account_hint": self.store.account_hint(_PROVIDER),
            "credential_source": self.store.source(_PROVIDER),
            "credentials_updated_at": self.store.updated_at(_PROVIDER),
            "browser_supported": _playwright_available(),
            "login_in_progress": login_in_progress,
            "login_supported": bool(self.store.available and _playwright_available()),
            "login_mode": "password_with_manual_verification_fallback",
            "last_checked_at": str(status.get("updated_at") or ""),
        }

    def save_credentials(self, username: str, password: str) -> str:
        """Encrypt one Ctrip credential and reset only safe login state."""

        self.stop()
        self.store.save(_PROVIDER, username, password)
        self._write_status(
            "unknown",
            "HOTEL_AUTH_RECHECK_PENDING",
            "Credentials changed; login will be checked again.",
        )
        log_event(
            hotel_account_logger,
            logging.INFO,
            "travel.hotel_credentials_saved",
            provider=_PROVIDER,
        )
        return "HOTEL_CREDENTIALS_SAVED"

    def delete_credentials(self) -> str:
        """Delete workspace .env credentials; keep the browser profile."""

        self.stop()
        try:
            deleted = self.store.delete(_PROVIDER)
        except CredentialStoreError:
            self._write_status(
                "unknown",
                "HOTEL_CREDENTIALS_EXTERNALLY_MANAGED",
                "Ctrip credentials are managed by the deployment environment.",
            )
            return "HOTEL_CREDENTIALS_EXTERNALLY_MANAGED"
        self._write_status(
            "not_configured",
            "HOTEL_CREDENTIALS_NOT_CONFIGURED",
            "Ctrip credentials have not been configured.",
        )
        log_event(
            hotel_account_logger,
            logging.INFO,
            "travel.hotel_credentials_deleted",
            provider=_PROVIDER,
            existed=deleted,
        )
        return "HOTEL_CREDENTIALS_DELETED"

    def start_login(self) -> str:
        """Start a fixed helper that reads the environment credential itself."""

        if not self.store.available:
            return "HOTEL_CREDENTIAL_STORE_UNAVAILABLE"
        if not self.store.configured(_PROVIDER):
            return "HOTEL_CREDENTIALS_NOT_CONFIGURED"
        if not _playwright_available():
            return "HOTEL_BROWSER_DEPENDENCY_MISSING"
        with self._lock:
            if self._tree is not None and self._tree.process.poll() is None:
                return "HOTEL_LOGIN_ALREADY_RUNNING"
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self._write_status_unlocked(
                "login_pending",
                "HOTEL_LOGIN_STARTED",
                "Ctrip automatic login is running.",
            )
            try:
                self._tree = ManagedProcessTree.spawn(
                    [
                        sys.executable,
                        "-m",
                        "integrations.hotel_browser_mcp.login",
                        "--workspace",
                        str(self.workspace),
                    ],
                    cwd=self.workspace,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    **(
                        {"creationflags": subprocess.CREATE_NO_WINDOW}
                        if os.name == "nt"
                        else {}
                    ),
                )
            except (OSError, ValueError) as exc:
                self._tree = None
                self._write_status_unlocked(
                    "unavailable",
                    "HOTEL_LOGIN_START_FAILED",
                    "The Ctrip login helper could not be started.",
                )
                log_event(
                    hotel_account_logger,
                    logging.ERROR,
                    "travel.hotel_login_start_failed",
                    provider=_PROVIDER,
                    error_type=type(exc).__name__,
                )
                return "HOTEL_LOGIN_START_FAILED"
            watcher = threading.Thread(
                target=self._watch_login,
                args=(self._tree,),
                name="zcagent-hotel-login",
                daemon=True,
            )
            self._watcher = watcher
        watcher.start()
        log_event(
            hotel_account_logger,
            logging.INFO,
            "travel.hotel_login_started",
            provider=_PROVIDER,
        )
        return "HOTEL_LOGIN_STARTED"

    def stop(self) -> None:
        """Stop only the login helper process created by this supervisor."""

        with self._lock:
            tree, self._tree = self._tree, None
        if tree is not None:
            tree.terminate(grace_seconds=1.0)
        watcher = self._watcher
        if watcher is not None and watcher is not threading.current_thread():
            watcher.join(timeout=2.0)
        self._watcher = None

    def _watch_login(self, tree: ManagedProcessTree) -> None:
        tree.process.wait()
        with self._lock:
            if self._tree is tree:
                self._tree = None
            if self._watcher is threading.current_thread():
                self._watcher = None
        status = self._read_status()
        log_event(
            hotel_account_logger,
            logging.INFO
            if str(status.get("state")) == "authenticated"
            else logging.WARNING,
            "travel.hotel_login_finished",
            provider=_PROVIDER,
            state=str(status.get("state") or "unknown"),
            code=str(status.get("code") or "HOTEL_LOGIN_FAILED"),
        )

    def _read_status(self) -> dict[str, object]:
        try:
            payload = json.loads(self.status_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        allowed = {"state", "code", "message", "updated_at"}
        return {key: payload[key] for key in allowed if key in payload}

    def _write_status(self, state: str, code: str, message: str) -> None:
        with self._lock:
            self._write_status_unlocked(state, code, message)

    def _write_status_unlocked(self, state: str, code: str, message: str) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "state": state,
            "code": code,
            "message": message,
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        temporary = self.status_file.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, self.status_file)


def _playwright_available() -> bool:
    try:
        return importlib.util.find_spec("playwright.sync_api") is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


__all__ = [
    "CredentialStoreError",
    "HotelAccountSupervisor",
]
