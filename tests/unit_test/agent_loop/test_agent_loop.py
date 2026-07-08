"""Tests for the minimal no-tool AgentLoop."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.message import Message
from agent.protocols.session import SessionState


def test_run_turn_returns_assistant_text_and_appends_session_messages(tmp_path):
    """A successful turn should save user and assistant messages exactly once."""

    from agent.core.loop import AgentLoop
    from agent.protocols.llm import LLMResponse

    llm = FakeLLM(LLMResponse(content="hi there", metadata={"model": "fake"}))
    sessions = InMemorySessionStore()
    context_builder = FakeContextBuilder()
    loop = AgentLoop(llm=llm, sessions=sessions, context_builder=context_builder, workspace=tmp_path)

    result = loop.run_turn("default", "hello")

    assert result == "hi there"
    assert llm.calls == [{"messages": [{"role": "user", "content": "hello"}], "tools": None}]
    assert [(message.role, message.content) for message in sessions.appended["default"]] == [
        ("user", "hello"),
        ("assistant", "hi there"),
    ]
    assert sessions.appended["default"][1].metadata == {"model": "fake"}
    _assert_single_turn(sessions.appended["default"], expected_index=1)


def test_run_turn_logs_lifecycle_without_full_user_text(tmp_path, caplog):
    """Lifecycle logs should be correlated by session/turn and use short previews."""

    from agent.core.loop import AgentLoop
    from agent.protocols.llm import LLMResponse

    user_text = "OPENAI_API_KEY=sk-testsecret123456\n" + "x" * 200
    llm = FakeLLM(LLMResponse(content="hi"))
    sessions = InMemorySessionStore()
    loop = AgentLoop(llm=llm, sessions=sessions, context_builder=FakeContextBuilder(), workspace=tmp_path)
    caplog.set_level("INFO", logger="zcagent.agent")

    loop.run_turn("default", user_text, turn_id="turn-log")

    events = [record for record in caplog.records if record.name.startswith("zcagent.agent")]
    assert [record.event for record in events] == [  # type: ignore[attr-defined]
        "turn.start",
        "turn.done",
    ]
    for record in events:
        fields = record.fields  # type: ignore[attr-defined]
        assert fields["session_id"] == "default"
        assert fields["turn_id"] == "turn-log"
        assert "sk-testsecret123456" not in str(fields)
    start_fields = events[0].fields  # type: ignore[attr-defined]
    assert "input_preview" in start_fields
    assert len(start_fields["input_preview"]) <= 120


def test_run_turn_keeps_repetitive_lifecycle_logs_at_debug(tmp_path, caplog):
    """Default terminal info should stay concise while debug keeps trace detail."""

    from agent.core.loop import AgentLoop
    from agent.protocols.llm import LLMResponse

    llm = FakeLLM(LLMResponse(content="hi"))
    sessions = InMemorySessionStore()
    loop = AgentLoop(llm=llm, sessions=sessions, context_builder=FakeContextBuilder(), workspace=tmp_path)
    caplog.set_level("DEBUG", logger="zcagent.agent")

    loop.run_turn("default", "hello", turn_id="turn-debug")

    records = [record for record in caplog.records if record.name.startswith("zcagent.agent")]
    debug_events = [record.event for record in records if record.levelname == "DEBUG"]  # type: ignore[attr-defined]
    assert debug_events == ["llm.call", "llm.direct", "session.save"]


def test_run_turn_uses_external_turn_id_when_provided(tmp_path):
    """Web callers should be able to align accepted events with persisted messages."""

    from agent.core.loop import AgentLoop
    from agent.protocols.llm import LLMResponse

    llm = FakeLLM(LLMResponse(content="hi"))
    sessions = InMemorySessionStore()
    loop = AgentLoop(llm=llm, sessions=sessions, context_builder=FakeContextBuilder(), workspace=tmp_path)

    loop.run_turn("default", "hello", turn_id="turn-web")

    _assert_single_turn(
        sessions.appended["default"],
        expected_turn_id="turn-web",
        expected_index=1,
    )


def test_run_turn_passes_existing_history_to_context_builder(tmp_path):
    """The current user message should be separate from loaded session history."""

    from agent.core.loop import AgentLoop
    from agent.protocols.llm import LLMResponse

    history = [Message(role="user", content="before", turn_id="turn-before", turn_index=1)]
    sessions = InMemorySessionStore(states={"default": SessionState("default", history)})
    context_builder = FakeContextBuilder(messages=[{"role": "user", "content": "from context"}])
    loop = AgentLoop(
        llm=FakeLLM(LLMResponse(content="ok")),
        sessions=sessions,
        context_builder=context_builder,
        workspace=tmp_path,
    )

    loop.run_turn("default", "current")

    assert context_builder.calls[0]["history"] == history
    assert context_builder.calls[0]["workspace"] == tmp_path
    assert context_builder.calls[0]["session_id"] == "default"
    user_message = context_builder.calls[0]["user_message"]
    assert user_message.role == "user"
    assert user_message.content == "current"
    assert user_message.turn_id is not None
    assert user_message.turn_index == 2


def test_run_turn_records_error_marker_when_llm_raises(tmp_path):
    """LLM failures should leave a complete session turn without leaking details."""

    from agent.core.loop import AgentLoop

    llm = RaisingLLM(RuntimeError("upstream rejected secret sk-test"))
    sessions = InMemorySessionStore()
    loop = AgentLoop(
        llm=llm,
        sessions=sessions,
        context_builder=FakeContextBuilder(),
        workspace=tmp_path,
    )

    result = loop.run_turn("default", "hello")

    assert "LLM" in result
    assert "sk-test" not in result
    appended = sessions.appended["default"]
    assert [(message.role, message.content) for message in appended] == [
        ("user", "hello"),
        ("assistant", result),
    ]
    assert appended[1].metadata["is_error"] is True
    _assert_single_turn(appended, expected_index=1)


def test_run_turn_explains_missing_api_key(tmp_path):
    """Configuration errors should tell the user exactly what to set."""

    from agent.core.loop import AgentLoop
    from agent.protocols.llm import LLMConfigurationError

    llm = RaisingLLM(LLMConfigurationError("LLM API key is missing. Set api_key in llm_endpoints.json."))
    sessions = InMemorySessionStore()
    loop = AgentLoop(
        llm=llm,
        sessions=sessions,
        context_builder=FakeContextBuilder(),
        workspace=tmp_path,
    )

    result = loop.run_turn("default", "hello")

    assert "missing API key" in result
    assert "Choose one" in result
    assert "api_key" in result
    assert "${YOUR_ENV_NAME}" in result
    assert str(tmp_path / "config" / "llm_endpoints.json") in result


def test_run_turn_explains_missing_placeholder_environment_variable(tmp_path):
    """Missing ${ENV_VAR} references should tell the user what variable to define."""

    from agent.core.loop import AgentLoop
    from agent.protocols.llm import LLMConfigurationError

    llm = RaisingLLM(
        LLMConfigurationError(
            "LLM endpoint 'default' references missing environment variable "
            "'ZHICE_LLM_OPENAI_API_KEY' in field api_key"
        )
    )
    sessions = InMemorySessionStore()
    loop = AgentLoop(
        llm=llm,
        sessions=sessions,
        context_builder=FakeContextBuilder(),
        workspace=tmp_path,
    )

    result = loop.run_turn("default", "hello")

    assert "missing environment variable" in result
    assert "ZHICE_LLM_OPENAI_API_KEY" in result
    assert "config/.env" in result
    assert "api_key" in result


def test_run_turn_explains_provider_request_failure(tmp_path):
    """Provider errors should point to endpoint configuration."""

    from agent.core.loop import AgentLoop
    from agent.protocols.llm import LLMProviderError

    llm = RaisingLLM(LLMProviderError("LLM HTTP request failed with status 401: bad key"))
    sessions = InMemorySessionStore()
    loop = AgentLoop(
        llm=llm,
        sessions=sessions,
        context_builder=FakeContextBuilder(),
        workspace=tmp_path,
    )

    result = loop.run_turn("default", "hello")

    assert "llm_endpoints.json" in result
    assert "base_url" in result
    assert "api_key" in result


def test_run_turn_reports_session_save_failure(tmp_path):
    """Saving history failures should not hide the LLM/configuration message."""

    from agent.core.loop import AgentLoop
    from agent.protocols.llm import LLMResponse

    loop = AgentLoop(
        llm=FakeLLM(LLMResponse(content="ok")),
        sessions=FailingSessionStore(),
        context_builder=FakeContextBuilder(),
        workspace=tmp_path,
    )

    result = loop.run_turn("default", "hello")

    assert "ok" in result
    assert "Cannot save session history" in result
    assert "contexts" in result


def test_run_turn_emits_streaming_text_events(tmp_path):
    """Streaming providers should surface deltas before the final turn returns."""

    from agent.core.loop import AgentLoop
    from agent.protocols.llm import LLMStreamChunk

    llm = StreamingLLM([LLMStreamChunk(content_delta="hel"), LLMStreamChunk(content_delta="lo")])
    sessions = InMemorySessionStore()
    events = []
    loop = AgentLoop(llm=llm, sessions=sessions, context_builder=FakeContextBuilder(), workspace=tmp_path)

    result = loop.run_turn("default", "hello", on_event=events.append)

    assert result == "hello"
    assert events == [
        {"type": "text_delta", "content": "hel"},
        {"type": "text_delta", "content": "lo"},
    ]
    assert sessions.appended["default"][-1].content == "hello"
    _assert_single_turn(sessions.appended["default"], expected_index=1)


def test_stream_chunk_rejects_shapes_outside_protocol():
    """Streaming providers should return strings or LLMStreamChunk objects."""

    import pytest

    from agent.core.loop import _normalize_stream_chunk

    with pytest.raises(TypeError, match="Unsupported LLM stream chunk type"):
        _normalize_stream_chunk({"content_delta": "hello"})


def test_run_turn_stops_when_cancellation_token_is_set(tmp_path):
    """Cancellation should stop later deltas and persist a stopped marker."""

    from agent.core.loop import TURN_CANCELLED_TEXT, AgentLoop, CancellationToken
    from agent.protocols.llm import LLMStreamChunk

    token = CancellationToken()
    llm = StreamingLLM([LLMStreamChunk(content_delta="first"), LLMStreamChunk(content_delta="late")])
    sessions = InMemorySessionStore()
    events = []

    def on_event(event):
        events.append(event)
        token.cancel()

    loop = AgentLoop(llm=llm, sessions=sessions, context_builder=FakeContextBuilder(), workspace=tmp_path)

    result = loop.run_turn("default", "hello", on_event=on_event, cancellation_token=token)

    assert result == TURN_CANCELLED_TEXT
    assert events == [{"type": "text_delta", "content": "first"}]
    assert sessions.appended["default"][-1].content == TURN_CANCELLED_TEXT
    assert sessions.appended["default"][-1].metadata["stopped"] is True
    _assert_single_turn(sessions.appended["default"], expected_index=1)


def _assert_single_turn(
    messages: list[Message],
    *,
    expected_turn_id: str | None = None,
    expected_index: int,
) -> None:
    turn_ids = {message.turn_id for message in messages}
    turn_indices = {message.turn_index for message in messages}
    assert None not in turn_ids
    if expected_turn_id is not None:
        assert turn_ids == {expected_turn_id}
    else:
        assert len(turn_ids) == 1
        assert next(iter(turn_ids)).startswith("turn-")  # type: ignore[union-attr]
    assert turn_indices == {expected_index}


@dataclass
class InMemorySessionStore:
    states: dict[str, SessionState] = field(default_factory=dict)
    appended: dict[str, list[Message]] = field(default_factory=dict)

    def load(self, session_id: str) -> SessionState:
        return self.states.get(session_id, SessionState(session_id=session_id, messages=[]))

    def append(self, session_id: str, messages: list[Message]) -> None:
        self.appended.setdefault(session_id, []).extend(messages)


class FakeContextBuilder:
    def __init__(self, messages: list[dict[str, str]] | None = None):
        self.messages = messages
        self.calls: list[dict[str, Any]] = []

    def build(
        self,
        history: list[Message],
        user_message: Message,
        workspace: Path,
        session_id: str,
    ) -> list[dict[str, str]]:
        self.calls.append(
            {
                "history": history,
                "user_message": user_message,
                "workspace": workspace,
                "session_id": session_id,
            }
        )
        return self.messages or [{"role": user_message.role, "content": user_message.content}]


class FakeLLM:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, tools=None):
        self.calls.append({"messages": messages, "tools": tools})
        return self.response


class RaisingLLM:
    def __init__(self, error: Exception):
        self.error = error

    def chat(self, messages, tools=None):
        raise self.error


class StreamingLLM:
    def __init__(self, chunks):
        self.chunks = chunks

    def stream_chat(self, messages, tools=None):
        yield from self.chunks


class FailingSessionStore:
    def load(self, session_id: str) -> SessionState:
        return SessionState(session_id=session_id, messages=[])

    def append(self, session_id: str, messages: list[Message]) -> None:
        raise PermissionError("no write access")

