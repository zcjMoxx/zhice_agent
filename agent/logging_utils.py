"""Shared helpers for safe, structured runtime logging."""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Mapping
from typing import Any

SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "passwd",
    "token",
    "access_token",
    "refresh_token",
    "secret",
}

_ASSIGNMENT_SECRET_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)\s*=\s*([^\s]+)"
)
_BEARER_RE = re.compile(r"(?i)(Authorization\s*:\s*Bearer\s+)([^\s]+)")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_CONSOLE_DEFER_LOCK = threading.RLock()
_CONSOLE_DEFER_ACTIVE = False
_DEFERRED_CONSOLE_RECORDS: list[tuple[logging.StreamHandler, logging.LogRecord]] = []


class DeferredConsoleHandler(logging.StreamHandler):
    """Buffer console records during Gateway startup, then replay them unchanged."""

    def emit(self, record: logging.LogRecord) -> None:
        with _CONSOLE_DEFER_LOCK:
            if _CONSOLE_DEFER_ACTIVE:
                _DEFERRED_CONSOLE_RECORDS.append((self, record))
                return
        super().emit(record)


def begin_console_log_deferral() -> None:
    """Begin a fresh, process-local startup deferral window."""

    global _CONSOLE_DEFER_ACTIVE
    with _CONSOLE_DEFER_LOCK:
        _DEFERRED_CONSOLE_RECORDS.clear()
        _CONSOLE_DEFER_ACTIVE = True


def flush_deferred_console_logs() -> None:
    """Replay deferred records before allowing new console records through."""

    global _CONSOLE_DEFER_ACTIVE
    with _CONSOLE_DEFER_LOCK:
        records = tuple(_DEFERRED_CONSOLE_RECORDS)
        _DEFERRED_CONSOLE_RECORDS.clear()
        _CONSOLE_DEFER_ACTIVE = False
        for handler, record in records:
            logging.StreamHandler.emit(handler, record)


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Emit one structured log event with redacted fields."""

    if not logger.isEnabledFor(level):
        return
    logger.log(
        level,
        event,
        extra={
            "event": event,
            "fields": redact_mapping(fields),
        },
    )


def preview_text(value: Any, *, limit: int = 120) -> str:
    """Return a single-line, redacted, bounded preview for logs."""

    text = redact_text("" if value is None else str(value))
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    if limit <= 3:
        return "." * max(limit, 0)
    return text[: limit - 3].rstrip() + "..."


def preview_json(value: Any, *, limit: int = 200) -> str:
    """Return a compact JSON preview after recursive redaction."""

    try:
        text = json.dumps(redact_value(value), ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        text = str(redact_value(value))
    return preview_text(text, limit=limit)


def redact_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return a redacted copy of a mapping without mutating the input."""

    return {str(key): _redact_key_value(str(key), value) for key, value in data.items()}


def redact_value(value: Any) -> Any:
    """Recursively redact common secret shapes from log field values."""

    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(text: str) -> str:
    """Redact secret-like values from unstructured text."""

    redacted = _ASSIGNMENT_SECRET_RE.sub(r"\1=<redacted>", text)
    redacted = _BEARER_RE.sub(r"\1<redacted>", redacted)
    return _OPENAI_KEY_RE.sub("sk-<redacted>", redacted)


def _redact_key_value(key: str, value: Any) -> Any:
    """Redact a mapping value when its key is sensitive."""

    if _is_sensitive_key(key):
        return "***"
    return redact_value(value)


def _is_sensitive_key(key: str) -> bool:
    """Return whether a field name usually carries credentials."""

    normalized = key.strip().lower().replace("-", "_")
    if normalized in SENSITIVE_KEYS:
        return True
    return any(part in normalized for part in ("api_key", "token", "authorization", "password", "secret"))
