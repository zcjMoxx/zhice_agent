"""Authenticated Web-user ownership and QR binding state for Weixin."""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

from agent.auth.store import AuthStoreError, SQLiteAuthStore
from agent.channels.weixin.sidecar import (
    WEIXIN_ACCOUNT_START_TIMEOUT_SECONDS,
    WEIXIN_TOKEN_STALE,
    safe_weixin_error_code,
)
from agent.logging_utils import log_event
from agent.protocols.auth import ActorContext

binding_logger = logging.getLogger("zcagent.agent.channel.weixin")


@dataclass(frozen=True)
class BindingAttempt:
    attempt_id: str
    owner_user_id: str
    status: str
    expires_at: str
    qr_data: str = ""
    error_code: str = ""


class WeixinCredentialStore:
    def __init__(self, workspace: Path):
        self.root = workspace / "config" / "channels" / "weixin" / "accounts"

    def relative_ref(self, account_key: str) -> str:
        return f"channels/weixin/accounts/{account_key}.json"

    def stage(self, account_key: str, payload: dict[str, object]) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f".{account_key}.{secrets.token_hex(6)}.tmp"
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return path

    def promote(self, staged: Path, account_key: str) -> Path:
        target = self.root / f"{account_key}.json"
        os.replace(staged, target)
        return target

    def read(self, account_key: str) -> dict[str, object]:
        return json.loads((self.root / f"{account_key}.json").read_text(encoding="utf-8"))

    def delete(self, account_key: str) -> None:
        (self.root / f"{account_key}.json").unlink(missing_ok=True)


class WeixinBindingService:
    def __init__(self, store: SQLiteAuthStore, sidecar, workspace: Path, timeout_seconds: int = 480):
        self.store = store
        self.sidecar = sidecar
        self.workspace = workspace
        self.timeout_seconds = timeout_seconds
        self.credentials = WeixinCredentialStore(workspace)
        self._attempts: dict[str, BindingAttempt] = {}
        self._lock = threading.Lock()
        self._account_starter: Callable[[str, dict[str, object]], str] | None = None

    def set_account_starter(
        self, starter: Callable[[str, dict[str, object]], str]
    ) -> None:
        self._account_starter = starter

    def status(self, actor: ActorContext) -> dict[str, object]:
        row = self.store.get_channel_account_for_user(
            channel="weixin", owner_user_id=str(actor.user_id)
        )
        if row is None:
            return {"status": "unbound"}
        return {"status": str(row["status"]), "linked_at": str(row["linked_at"])}

    def start(self, actor: ActorContext) -> BindingAttempt:
        user_id = str(actor.user_id or "")
        if not user_id:
            raise AuthStoreError("authenticated user is required")
        if self.store.get_channel_account_for_user(channel="weixin", owner_user_id=user_id):
            raise AuthStoreError("weixin account is already bound")
        with self._lock:
            for attempt in self._attempts.values():
                if attempt.owner_user_id == user_id and attempt.status not in _TERMINAL:
                    return self._expire(attempt)
            attempt = BindingAttempt(
                attempt_id="wxbind-" + secrets.token_hex(16),
                owner_user_id=user_id,
                status="creating_qr",
                expires_at=(datetime.now(UTC) + timedelta(seconds=self.timeout_seconds)).isoformat(
                    timespec="seconds"
                ),
            )
            self._attempts[attempt.attempt_id] = attempt
        try:
            response = self.sidecar.request(
                "binding.start",
                attempt_id=attempt.attempt_id,
                timeout_seconds=min(30, self.timeout_seconds),
            )
        except Exception:
            failed = replace(
                attempt,
                status="upstream_unavailable",
                error_code="WEIXIN_BINDING_UPSTREAM_FAILED",
            )
            self._attempts[attempt.attempt_id] = failed
            return failed
        self.handle_frame(response)
        return self.get(actor, attempt.attempt_id)

    def get(self, actor: ActorContext, attempt_id: str) -> BindingAttempt:
        attempt = self._attempts.get(attempt_id)
        if attempt is None or attempt.owner_user_id != str(actor.user_id or ""):
            raise KeyError(attempt_id)
        return self._expire(attempt)

    def cancel(self, actor: ActorContext, attempt_id: str) -> BindingAttempt:
        attempt = self.get(actor, attempt_id)
        if attempt.status not in _TERMINAL:
            self.sidecar.request("binding.cancel", attempt_id=attempt_id)
            attempt = replace(attempt, status="cancelled", qr_data="")
            self._attempts[attempt_id] = attempt
        return attempt

    def unlink(self, actor: ActorContext) -> str:
        user_id = str(actor.user_id or "")
        row = self.store.get_channel_account_for_user(channel="weixin", owner_user_id=user_id)
        if row is None:
            raise KeyError(user_id)
        account_key = str(row["account_key"])
        self.store.update_channel_account_status(
            channel="weixin", account_key=account_key, status="disabled"
        )
        try:
            self.sidecar.request("account.stop", account_key=account_key)
        except Exception:  # noqa: BLE001 - disabled DB state remains authoritative.
            pass
        deleted = self.store.delete_channel_account_for_user(
            channel="weixin", owner_user_id=user_id
        )
        try:
            self.credentials.delete(account_key)
            state_dir = self.workspace / "state" / "channels" / "weixin" / account_key
            (state_dir / "sync.json").unlink(missing_ok=True)
            try:
                state_dir.rmdir()
            except OSError:
                pass
        except OSError:
            return "cleanup_pending"
        return "unbound" if deleted else "cleanup_pending"

    def reconnect(self, actor: ActorContext) -> str:
        row = self.store.get_channel_account_for_user(
            channel="weixin", owner_user_id=str(actor.user_id or "")
        )
        if row is None:
            raise KeyError(str(actor.user_id or ""))
        account_key = str(row["account_key"])
        credential = self.credentials.read(account_key)
        if self._account_starter is not None:
            status = self._account_starter(account_key, credential)
            return "reconnecting" if status == "retry_pending" else status
        try:
            response = self.sidecar.request(
                "account.start",
                timeout_seconds=WEIXIN_ACCOUNT_START_TIMEOUT_SECONDS,
                account_key=account_key,
                credential=credential,
            )
        except Exception as exc:
            code = safe_weixin_error_code(exc)
            if code == WEIXIN_TOKEN_STALE:
                self.store.update_channel_account_status(
                    channel="weixin", account_key=account_key, status="reconnect_required"
                )
                return "reconnect_required"
            _log_binding_reconnecting(account_key, code)
            return "reconnecting"
        status = str(response.get("status") or "degraded")
        code = safe_weixin_error_code(response.get("code"), "WEIXIN_ACCOUNT_START_FAILED")
        if status == "active":
            self.store.update_channel_account_status(
                channel="weixin", account_key=account_key, status="active"
            )
            return "active"
        if status == "reconnect_required" and code == WEIXIN_TOKEN_STALE:
            self.store.update_channel_account_status(
                channel="weixin", account_key=account_key, status="reconnect_required"
            )
            return "reconnect_required"
        _log_binding_reconnecting(account_key, code)
        return "reconnecting"

    def handle_frame(self, frame: dict[str, object]) -> None:
        frame_type = str(frame.get("type") or "")
        attempt_id = str(frame.get("attempt_id") or "")
        attempt = self._attempts.get(attempt_id)
        if attempt is None:
            return
        if frame_type == "binding.qr":
            self._attempts[attempt_id] = replace(
                attempt, status="waiting_scan", qr_data=str(frame.get("qr_data") or "")
            )
        elif frame_type == "binding.status":
            status = str(frame.get("status") or "waiting_scan")
            self._attempts[attempt_id] = replace(attempt, status=status)
        elif frame_type == "binding.failed":
            self._attempts[attempt_id] = replace(
                attempt,
                status=str(frame.get("status") or "upstream_unavailable"),
                error_code=str(frame.get("code") or "WEIXIN_UPSTREAM_UNAVAILABLE"),
                qr_data="",
            )
        elif frame_type == "binding.connected":
            self._finalize(attempt, frame)

    def _finalize(self, attempt: BindingAttempt, frame: dict[str, object]) -> None:
        account_key = "wx_" + secrets.token_hex(16)
        credential = frame.get("credential")
        external_account_id = str(frame.get("external_account_id") or "").strip()
        external_user_id = str(frame.get("external_user_id") or "").strip()
        if not isinstance(credential, dict) or not external_account_id or not external_user_id:
            self._attempts[attempt.attempt_id] = replace(
                attempt, status="persist_failed", error_code="WEIXIN_CREDENTIAL_MISSING"
            )
            return
        staged = self.credentials.stage(account_key, credential)
        try:
            self.store.create_channel_account(
                channel="weixin",
                account_key=account_key,
                owner_user_id=attempt.owner_user_id,
                external_account_id=external_account_id,
                external_user_id=external_user_id,
                credential_ref=self.credentials.relative_ref(account_key),
            )
            self.credentials.promote(staged, account_key)
        except Exception:
            staged.unlink(missing_ok=True)
            self.store.delete_channel_account_for_user(
                channel="weixin", owner_user_id=attempt.owner_user_id
            )
            self._attempts[attempt.attempt_id] = replace(
                attempt, status="persist_failed", error_code="WEIXIN_BIND_PERSIST_FAILED"
            )
            return
        self._attempts[attempt.attempt_id] = replace(attempt, status="connected", qr_data="")
        threading.Thread(
            target=self._start_bound_account,
            args=(account_key, credential),
            name=f"weixin-account-start-{account_key[-8:]}",
            daemon=True,
        ).start()

    def _start_bound_account(self, account_key: str, credential: dict[str, object]) -> None:
        if self._account_starter is not None:
            self._account_starter(account_key, credential)
            return
        try:
            response = self.sidecar.request(
                "account.start",
                timeout_seconds=WEIXIN_ACCOUNT_START_TIMEOUT_SECONDS,
                account_key=account_key,
                credential=credential,
            )
            status = str(response.get("status") or "degraded")
            code = safe_weixin_error_code(
                response.get("code"), "WEIXIN_ACCOUNT_START_FAILED"
            )
            if status in {"active", "degraded"}:
                return
            if status == "reconnect_required" and code == WEIXIN_TOKEN_STALE:
                self.store.update_channel_account_status(
                    channel="weixin", account_key=account_key, status="reconnect_required"
                )
                return
            _log_binding_reconnecting(account_key, code)
        except Exception as exc:  # noqa: BLE001 - persisted binding remains retryable.
            code = safe_weixin_error_code(exc)
            if code == WEIXIN_TOKEN_STALE:
                self.store.update_channel_account_status(
                    channel="weixin", account_key=account_key, status="reconnect_required"
                )
                return
            _log_binding_reconnecting(account_key, code)

    def _expire(self, attempt: BindingAttempt) -> BindingAttempt:
        if attempt.status in _TERMINAL:
            return attempt
        if attempt.expires_at <= datetime.now(UTC).isoformat(timespec="seconds"):
            attempt = replace(attempt, status="expired", qr_data="")
            self._attempts[attempt.attempt_id] = attempt
        return attempt


_TERMINAL = {
    "connected",
    "expired",
    "cancelled",
    "account_conflict",
    "verification_failed",
    "upstream_unavailable",
    "persist_failed",
}


def _log_binding_reconnecting(account_key: str, code: str) -> None:
    log_event(
        binding_logger,
        logging.WARNING,
        "channel.weixin.reconnecting",
        account_ref="wx-" + uuid.uuid5(uuid.NAMESPACE_URL, account_key).hex[:8],
        reason_code=code,
    )
