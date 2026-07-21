"""Tests for the transport-neutral RuntimeEvent contract."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent.protocols.runtime_event import (
    RuntimeEvent,
    is_runtime_event_payload,
    validate_runtime_event_presentation,
)


def _event(**overrides) -> RuntimeEvent:
    values = {
        "protocol_version": 1,
        "event_id": "event-1",
        "type": "llm.started",
        "status": "started",
        "timestamp": datetime.now(UTC).isoformat(),
        "sequence": 1,
        "session_id": "session-1",
        "turn_id": "turn-1",
        "display": {"title": "正在请求模型", "icon": "model"},
    }
    values.update(overrides)
    return RuntimeEvent(**values)


def test_runtime_event_serializes_stable_payload():
    event = _event(metadata={"reason": "initial"})

    payload = event.to_dict()

    assert is_runtime_event_payload(payload) is True
    assert payload["type"] == "llm.started"
    assert payload["metadata"] == {"reason": "initial"}
    assert is_runtime_event_payload({"type": "text_delta", "content": "x"}) is False


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"protocol_version": 2}, "protocol version"),
        ({"type": "skill.started"}, "unsupported runtime event type"),
        ({"status": "completed"}, "requires status started"),
        ({"sequence": 0}, "sequence must be positive"),
        ({"timestamp": "not-a-time"}, "ISO-8601"),
    ],
)
def test_runtime_event_rejects_invalid_identity(overrides, message):
    with pytest.raises(ValueError, match=message):
        _event(**overrides)


def test_runtime_event_rejects_sensitive_metadata_and_unsafe_ui():
    with pytest.raises(ValueError, match="not allowed"):
        _event(metadata={"prompt": "full prompt"})
    with pytest.raises(ValueError, match="unsafe text"):
        validate_runtime_event_presentation({"title": "API_KEY=secret-value"}, {})
    with pytest.raises(ValueError, match="unsafe UI field"):
        validate_runtime_event_presentation(
            {},
            {"detail_type": "summary", "detail_data": {"url": "https://example.test"}},
        )


def test_runtime_event_accepts_registered_bounded_presentation():
    display, ui_metadata = validate_runtime_event_presentation(
        {"title": "资料搜索完成", "icon": "search"},
        {"detail_type": "search_results", "detail_data": {"items": ["a", "b"]}},
    )

    assert display["title"] == "资料搜索完成"
    assert ui_metadata["detail_type"] == "search_results"
