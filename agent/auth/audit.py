"""SQLite audit sink with recursive secret redaction and size bounds."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from agent.auth.store import SQLiteAuthStore
from agent.protocols.auth import AuditEvent
from agent.tools.shell_policy import redact_secrets

_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "password",
    "password_hash",
    "password_salt",
    "secret",
    "token",
}


class SqliteAuditSink:
    """Persist bounded, redacted audit events through SQLiteAuthStore."""

    def __init__(self, store: SQLiteAuthStore):
        self.store = store

    def record(self, event: AuditEvent) -> None:
        """Redact metadata before persisting the security event."""

        safe_metadata = _sanitize(event.metadata)
        encoded = json.dumps(safe_metadata, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > 4000:
            safe_metadata = {"preview": encoded[:3988] + "[truncated]"}
        self.store.record_audit(replace(event, metadata=safe_metadata))


def _sanitize(value: Any, *, key: str = "") -> Any:
    normalized_key = key.lower().replace("-", "_")
    if any(marker in normalized_key for marker in _SENSITIVE_KEYS):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(item_key): _sanitize(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_sanitize(item, key=key) for item in value[:100]]
    if isinstance(value, str):
        return redact_secrets(value)[:1000]
    if value is None or isinstance(value, bool | int | float):
        return value
    return redact_secrets(str(value))[:500]

