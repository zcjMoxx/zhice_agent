"""Transport-neutral runtime lifecycle event contracts."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

RUNTIME_EVENT_PROTOCOL_VERSION = 1
RUNTIME_EVENT_STATUS_BY_TYPE = {
    "turn.started": "started",
    "turn.completed": "completed",
    "turn.failed": "failed",
    "turn.stopped": "stopped",
    "context.started": "started",
    "context.completed": "completed",
    "context.failed": "failed",
    "llm.started": "started",
    "llm.completed": "completed",
    "llm.failed": "failed",
    "tool.started": "started",
    "tool.completed": "completed",
    "tool.failed": "failed",
    "tool.waiting_confirmation": "waiting",
    "skill.started": "started",
    "skill.progress": "started",
    "skill.completed": "completed",
    "skill.failed": "failed",
    "travel.plan_ready": "completed",
    "travel.clarification_required": "waiting",
    "travel.candidate_review_required": "waiting",
}
RUNTIME_EVENT_TYPES = frozenset(RUNTIME_EVENT_STATUS_BY_TYPE)
RUNTIME_EVENT_STATUSES = frozenset(RUNTIME_EVENT_STATUS_BY_TYPE.values())

_DISPLAY_KEYS = frozenset({"title", "detail", "icon", "visibility"})
_DISPLAY_LIMITS = {"title": 120, "detail": 500, "icon": 40, "visibility": 20}
_UI_METADATA_KEYS = frozenset({"detail_type", "detail_data"})
RUNTIME_UI_DETAIL_TYPES = frozenset(
    {"summary", "search_results", "travel_candidates", "code_preview", "table", "map"}
)
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "argument",
    "authorization",
    "command",
    "credential",
    "memory",
    "message",
    "output",
    "password",
    "prompt",
    "secret",
    "token",
)
_UNSAFE_UI_KEY_PARTS = (*_SENSITIVE_KEY_PARTS, "html", "script", "url")
_SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)\b[A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD)\s*[:=]\s*\S+"),
    re.compile(r"(?i)authorization\s*:\s*bearer\s+\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)
_ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"(?i)(?:^|[\s\"'(=])[A-Z]:[\\/]"),
    re.compile(r"(?:^|[\s\"'(=])/(?!/)"),
)


@dataclass(frozen=True)
class RuntimeEvent:
    """One validated, safe-to-forward lifecycle fact for a single turn."""

    protocol_version: int
    event_id: str
    type: str
    status: str
    timestamp: str
    sequence: int
    session_id: str
    turn_id: str
    request_id: str = ""
    tool_call_id: str = ""
    tool_call_record_id: str = ""
    skill_run_id: str = ""
    parent_event_id: str = ""
    agent_id: str = ""
    parent_agent_id: str = ""
    root_session_id: str = ""
    root_turn_id: str = ""
    parent_session_id: str = ""
    parent_turn_id: str = ""
    batch_id: str = ""
    task_id: str = ""
    depth: int = 0
    display: dict[str, Any] = field(default_factory=dict)
    ui_metadata: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.protocol_version != RUNTIME_EVENT_PROTOCOL_VERSION:
            raise ValueError("unsupported runtime event protocol version")
        expected_status = RUNTIME_EVENT_STATUS_BY_TYPE.get(self.type)
        if expected_status is None:
            raise ValueError(f"unsupported runtime event type: {self.type}")
        if self.status != expected_status:
            raise ValueError(f"runtime event {self.type} requires status {expected_status}")
        if self.sequence < 1:
            raise ValueError("runtime event sequence must be positive")
        for name, value in (
            ("event_id", self.event_id),
            ("session_id", self.session_id),
            ("turn_id", self.turn_id),
        ):
            if not str(value).strip():
                raise ValueError(f"runtime event {name} is required")
        for name, value in (
            ("event_id", self.event_id),
            ("session_id", self.session_id),
            ("turn_id", self.turn_id),
            ("request_id", self.request_id),
            ("tool_call_id", self.tool_call_id),
            ("tool_call_record_id", self.tool_call_record_id),
            ("skill_run_id", self.skill_run_id),
            ("parent_event_id", self.parent_event_id),
            ("agent_id", self.agent_id),
            ("parent_agent_id", self.parent_agent_id),
            ("root_session_id", self.root_session_id),
            ("root_turn_id", self.root_turn_id),
            ("parent_session_id", self.parent_session_id),
            ("parent_turn_id", self.parent_turn_id),
            ("batch_id", self.batch_id),
            ("task_id", self.task_id),
        ):
            if len(str(value)) > 200:
                raise ValueError(f"runtime event {name} is too long")
        if self.depth < 0 or self.depth > 1:
            raise ValueError("runtime event depth must be between 0 and 1")
        _validate_timestamp(self.timestamp)
        _validate_display(self.display)
        _validate_ui_metadata(self.ui_metadata)
        _validate_metadata(self.metadata)
        object.__setattr__(self, "display", deepcopy(self.display))
        object.__setattr__(self, "ui_metadata", deepcopy(self.ui_metadata))
        object.__setattr__(self, "metadata", deepcopy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        """Return the stable JSON-ready event payload."""

        return {
            "protocol_version": self.protocol_version,
            "event_id": self.event_id,
            "type": self.type,
            "status": self.status,
            "timestamp": self.timestamp,
            "sequence": self.sequence,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "request_id": self.request_id,
            "tool_call_id": self.tool_call_id,
            "tool_call_record_id": self.tool_call_record_id,
            "skill_run_id": self.skill_run_id,
            "parent_event_id": self.parent_event_id,
            "agent_id": self.agent_id,
            "parent_agent_id": self.parent_agent_id,
            "root_session_id": self.root_session_id,
            "root_turn_id": self.root_turn_id,
            "parent_session_id": self.parent_session_id,
            "parent_turn_id": self.parent_turn_id,
            "batch_id": self.batch_id,
            "task_id": self.task_id,
            "depth": self.depth,
            "display": deepcopy(self.display),
            "ui_metadata": deepcopy(self.ui_metadata),
            "metadata": deepcopy(self.metadata),
        }


class RuntimeEventSink(Protocol):
    """Consume one validated RuntimeEvent without owning transport semantics."""

    def emit(self, event: RuntimeEvent) -> None: ...


class RuntimeEventPublisher(Protocol):
    """Publish one event while owning turn-local sequence allocation."""

    def emit(self, event_type: str, **kwargs: Any) -> RuntimeEvent | None: ...


def is_runtime_event_payload(value: object) -> bool:
    """Return whether a callback payload is a serialized RuntimeEvent."""

    return (
        isinstance(value, dict)
        and value.get("protocol_version") == RUNTIME_EVENT_PROTOCOL_VERSION
        and value.get("type") in RUNTIME_EVENT_TYPES
        and isinstance(value.get("sequence"), int)
    )


def validate_runtime_event_presentation(
    display: dict[str, Any] | None,
    ui_metadata: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate and copy a Hook-provided presentation patch."""

    resolved_display = dict(display or {})
    resolved_ui_metadata = dict(ui_metadata or {})
    _validate_display(resolved_display)
    _validate_ui_metadata(resolved_ui_metadata)
    return resolved_display, resolved_ui_metadata


def _validate_timestamp(value: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("runtime event timestamp must be ISO-8601") from exc


def _validate_display(display: dict[str, Any]) -> None:
    if not isinstance(display, dict):
        raise ValueError("runtime event display must be an object")
    unknown = set(display) - _DISPLAY_KEYS
    if unknown:
        raise ValueError(f"unsupported runtime event display fields: {sorted(unknown)}")
    for key, value in display.items():
        if not isinstance(value, str):
            raise ValueError(f"runtime event display.{key} must be a string")
        if len(value) > _DISPLAY_LIMITS[key]:
            raise ValueError(f"runtime event display.{key} is too long")
        if _contains_unsafe_text(value):
            raise ValueError(f"runtime event display.{key} contains unsafe text")


def _validate_ui_metadata(metadata: dict[str, Any]) -> None:
    if not isinstance(metadata, dict):
        raise ValueError("runtime event ui_metadata must be an object")
    unknown = set(metadata) - _UI_METADATA_KEYS
    if unknown:
        raise ValueError(f"unsupported runtime event ui_metadata fields: {sorted(unknown)}")
    if metadata:
        detail_type = metadata.get("detail_type")
        if not isinstance(detail_type, str) or detail_type not in RUNTIME_UI_DETAIL_TYPES:
            raise ValueError("runtime event ui_metadata.detail_type is invalid")
        if "detail_data" not in metadata:
            raise ValueError("runtime event ui_metadata.detail_data is required")
    _validate_json_value(metadata, field_name="ui_metadata", max_bytes=32 * 1024)
    _reject_unsafe_ui_values(metadata)


def _validate_metadata(metadata: dict[str, Any]) -> None:
    if not isinstance(metadata, dict):
        raise ValueError("runtime event metadata must be an object")
    if len(metadata) > 20:
        raise ValueError("runtime event metadata has too many fields")
    for key in metadata:
        if not isinstance(key, str) or not key or len(key) > 64:
            raise ValueError("runtime event metadata keys must be short strings")
        normalized = key.lower()
        if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
            raise ValueError(f"runtime event metadata field is not allowed: {key}")
    _validate_json_value(metadata, field_name="metadata", max_bytes=4096)


def _validate_json_value(value: Any, *, field_name: str, max_bytes: int) -> None:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"runtime event {field_name} must be JSON serializable") from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError(f"runtime event {field_name} is too large")


def _reject_unsafe_ui_values(value: Any, *, path: str = "ui_metadata") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in _UNSAFE_UI_KEY_PARTS):
                raise ValueError(f"runtime event unsafe UI field is not allowed: {path}.{key}")
            _reject_unsafe_ui_values(item, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_unsafe_ui_values(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        if value.strip().lower().startswith(("http://", "https://", "javascript:")):
            raise ValueError(f"runtime event external UI value is not allowed: {path}")
        if _contains_unsafe_text(value):
            raise ValueError(f"runtime event unsafe UI value is not allowed: {path}")


def _contains_unsafe_text(value: str) -> bool:
    return any(pattern.search(value) for pattern in (*_SECRET_TEXT_PATTERNS, *_ABSOLUTE_PATH_PATTERNS))
