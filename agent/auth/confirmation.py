"""Blocking CLI and Web confirmation brokers for high-risk tool calls."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from threading import Event, Lock
from typing import Any, Callable

from agent.auth.store import AuthStoreError, SQLiteAuthStore
from agent.protocols.auth import ActorContext
from agent.protocols.tool import (
    ToolConfirmationResult,
    ToolExecutionContext,
    ToolExecutionDecision,
)
from agent.tools.shell_policy import redact_secrets

DEFAULT_CONFIRMATION_TIMEOUT_SECONDS = 300


class SQLiteToolConfirmationBroker:
    """Persist confirmations and wake blocked AgentLoop worker threads on decisions."""

    def __init__(
        self,
        store: SQLiteAuthStore,
        *,
        timeout_seconds: int = DEFAULT_CONFIRMATION_TIMEOUT_SECONDS,
    ):
        self.store = store
        self.timeout_seconds = max(1, int(timeout_seconds))
        self._events: dict[str, Event] = {}
        self._lock = Lock()

    def request(
        self,
        decision: ToolExecutionDecision,
        context: ToolExecutionContext,
        args: dict[str, Any],
        *,
        on_requested=None,
        is_cancelled=None,
    ) -> ToolConfirmationResult:
        """Create, announce, and wait for one exact high-risk request."""

        if context.actor.user_id is None:
            return ToolConfirmationResult(
                status="denied",
                confirmation_id="",
                message="Database user is required for Web confirmation.",
            )
        confirmation_id = "conf-" + uuid.uuid4().hex
        args_hash = _args_hash(args)
        command_preview, confirmation_fields = _confirmation_view(args)
        expires_at = datetime.now(UTC) + timedelta(seconds=self.timeout_seconds)
        self.store.create_tool_confirmation(
            confirmation_id=confirmation_id,
            tool_call_record_id=context.tool_call_record_id,
            actor_user_id=context.actor.user_id,
            session_id=context.session_id,
            turn_id=context.turn_id,
            tool_name=context.tool_name,
            risk_level=decision.risk_level,
            command_preview=command_preview,
            args_hash=args_hash,
            expires_at=expires_at.isoformat(timespec="seconds"),
        )
        event = Event()
        with self._lock:
            self._events[confirmation_id] = event
        if on_requested is not None:
            on_requested(
                {
                    "confirmation_id": confirmation_id,
                    "command_preview": command_preview,
                    "confirmation_title": "Confirm high-risk tool",
                    "confirmation_fields": confirmation_fields,
                    "expires_at": expires_at.isoformat(timespec="seconds"),
                }
            )
        deadline = time.monotonic() + self.timeout_seconds
        try:
            while time.monotonic() < deadline:
                if is_cancelled is not None and is_cancelled():
                    status = self.store.expire_tool_confirmation(
                        confirmation_id, status="cancelled"
                    )
                    return ToolConfirmationResult(
                        status=status,
                        confirmation_id=confirmation_id,
                    )
                event.wait(timeout=0.1)
                row = self.store.get_tool_confirmation(confirmation_id)
                status = str((row or {}).get("status") or "pending")
                if status != "pending":
                    return ToolConfirmationResult(
                        status=status,  # type: ignore[arg-type]
                        confirmation_id=confirmation_id,
                    )
            status = self.store.expire_tool_confirmation(confirmation_id)
            return ToolConfirmationResult(  # type: ignore[arg-type]
                status=status,
                confirmation_id=confirmation_id,
            )
        finally:
            with self._lock:
                self._events.pop(confirmation_id, None)

    def decide(self, actor: ActorContext, confirmation_id: str, approved: bool) -> str:
        """Approve or deny one actor-visible pending confirmation."""

        if actor.user_id is None:
            raise AuthStoreError("confirmation not found")
        status = self.store.decide_tool_confirmation(
            confirmation_id,
            decision_actor_user_id=actor.user_id,
            approved=approved,
            manage_any=actor.has_permission("session.manage.any"),
        )
        with self._lock:
            event = self._events.get(confirmation_id)
        if event is not None:
            event.set()
        return status

    def list_for_actor(self, actor: ActorContext) -> list[dict[str, Any]]:
        """List pending own confirmations, or all for session managers."""

        rows = self.store.list_tool_confirmations(
            actor_user_id=None if actor.has_permission("session.manage.any") else actor.user_id,
            pending_only=True,
        )
        return [
            {
                "id": str(row["id"]),
                "session_id": str(row["session_id"]),
                "turn_id": str(row["turn_id"]),
                "tool_name": str(row["tool_name"]),
                "risk_level": str(row["risk_level"]),
                "command_preview": str(row["command_preview"]),
                "status": str(row["status"]),
                "expires_at": str(row["expires_at"]),
            }
            for row in rows
        ]


class ConsoleConfirmationBroker:
    """Block the local CLI and require typing the generated confirmation id."""

    def __init__(self, *, input_func: Callable[[str], str] = input):
        self.input_func = input_func

    def request(
        self,
        decision: ToolExecutionDecision,
        context: ToolExecutionContext,
        args: dict[str, Any],
        *,
        on_requested=None,
        is_cancelled=None,
    ) -> ToolConfirmationResult:
        confirmation_id = "conf-" + uuid.uuid4().hex[:12]
        command_preview, confirmation_fields = _confirmation_view(args)
        if on_requested is not None:
            on_requested(
                {
                    "confirmation_id": confirmation_id,
                    "command_preview": command_preview,
                    "confirmation_title": "Confirm high-risk tool",
                    "confirmation_fields": confirmation_fields,
                    "expires_at": "",
                }
            )
        details = "\n".join(
            f"{field['label']}: {field['value']}" for field in confirmation_fields
        )
        prompt = (
            "Confirm high-risk tool.\n"
            f"{details}\n"
            f"risk: {decision.risk_level} ({decision.risk_category})\n"
            f"Type {confirmation_id} to approve, or press Enter to deny: "
        )
        if is_cancelled is not None and is_cancelled():
            return ToolConfirmationResult(status="cancelled", confirmation_id=confirmation_id)
        typed = self.input_func(prompt).strip()
        if typed == confirmation_id:
            return ToolConfirmationResult(status="approved", confirmation_id=confirmation_id)
        return ToolConfirmationResult(status="denied", confirmation_id=confirmation_id)


def _args_hash(args: dict[str, Any]) -> str:
    encoded = json.dumps(args, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _confirmation_view(args: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    command_preview = redact_secrets(str(args.get("command") or ""))[:300]
    return command_preview, [{"label": "Command", "value": command_preview or "-"}]
