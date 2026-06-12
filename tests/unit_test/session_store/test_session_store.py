"""Tests for JSONL session persistence."""

import json

import pytest

from agent.message import Message
from agent.session.jsonl_store import InvalidSessionIdError, JsonlSessionStore


def test_load_missing_session_returns_empty_state(tmp_path):
    """A missing JSONL file should behave like an empty session."""

    store = JsonlSessionStore(tmp_path)

    state = store.load("default")

    assert state.session_id == "default"
    assert state.messages == []


def test_append_then_load_preserves_message_order(tmp_path):
    """Appended messages should be read back in the same order."""

    store = JsonlSessionStore(tmp_path)

    store.append(
        "default",
        [
            Message(role="user", content="hello", metadata={"timestamp": 1.0}),
            Message(role="assistant", content="hi", metadata={"timestamp": 2.0}),
        ],
    )

    state = store.load("default")

    assert [message.role for message in state.messages] == ["user", "assistant"]
    assert [message.content for message in state.messages] == ["hello", "hi"]
    assert [message.metadata["timestamp"] for message in state.messages] == [1.0, 2.0]


def test_append_creates_jsonl_file_with_utf8_content(tmp_path):
    """Session files should be UTF-8 JSONL and keep Chinese text readable."""

    store = JsonlSessionStore(tmp_path)

    store.append("zhice", [Message(role="user", content="你好")])

    session_file = tmp_path / "zhice.jsonl"
    text = session_file.read_text(encoding="utf-8")
    record = json.loads(text)

    assert record["content"] == "你好"
    assert record["role"] == "user"
    assert "timestamp" in record


@pytest.mark.parametrize("session_id", ["../escape", "bad/name", "bad.name", ""])
def test_invalid_session_id_is_rejected(tmp_path, session_id):
    """Session ids should not be able to address paths outside sessions_dir."""

    store = JsonlSessionStore(tmp_path)

    with pytest.raises(InvalidSessionIdError):
        store.load(session_id)


def test_unknown_json_fields_are_ignored(tmp_path):
    """Future JSONL fields should not break old readers."""

    session_file = tmp_path / "default.jsonl"
    session_file.write_text(
        json.dumps(
            {
                "role": "user",
                "content": "hello",
                "timestamp": 3.0,
                "metadata": {"source": "test"},
                "future_field": "ignored",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    state = JsonlSessionStore(tmp_path).load("default")

    assert len(state.messages) == 1
    assert state.messages[0].content == "hello"
    assert state.messages[0].metadata == {"source": "test", "timestamp": 3.0}


def test_clear_removes_persisted_session_file(tmp_path):
    """Resetting a session should remove its JSONL file."""

    store = JsonlSessionStore(tmp_path)
    store.append("default", [Message(role="user", content="hello")])

    store.clear("default")

    assert not (tmp_path / "default.jsonl").exists()
    assert store.load("default").messages == []


def test_list_sessions_returns_preview_and_recent_order(tmp_path):
    """Session listings should include a simple preview and sort by recency."""

    store = JsonlSessionStore(tmp_path)
    store.append(
        "older",
        [
            Message(role="assistant", content="preface", metadata={"timestamp": 1.0}),
            Message(role="user", content="first older question", metadata={"timestamp": 2.0}),
        ],
    )
    store.append(
        "newer",
        [Message(role="user", content="first newer question", metadata={"timestamp": 5.0})],
    )

    summaries = store.list_sessions()

    assert [summary.session_id for summary in summaries] == ["newer", "older"]
    assert summaries[0].preview == "first newer question"
    assert summaries[1].preview == "first older question"
