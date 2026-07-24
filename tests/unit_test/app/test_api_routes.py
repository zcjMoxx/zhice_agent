from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent.app.gateway import create_app
from agent.app.runtime import ChatTurnResult, ModelState
from agent.config import AppConfig
from agent.message import Message
from agent.protocols.llm import LLMConfigurationError, LLMProviderError
from agent.protocols.session import SessionState, SessionSummary
from agent.session.jsonl_store import InvalidSessionIdError


def test_weixin_binding_attempt_is_no_store_and_does_not_accept_user_id(tmp_path):
    runtime = _FakeRuntime()
    runtime.channel_weixin_binding = _FakeWeixinBinding()
    client = _client(tmp_path, runtime)

    response = client.post(
        "/api/channels/weixin/binding-attempts",
        json={"user_id": "attacker-selected"},
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {
        "attempt_id": "wxbind-safe",
        "status": "waiting_scan",
        "expires_at": "2026-07-24T10:00:00+00:00",
        "qr_data": "data:image/png;base64,safe",
        "error_code": "",
    }
    assert runtime.channel_weixin_binding.actor_user_ids == [None]


def test_sessions_api_returns_summaries(tmp_path):
    runtime = _FakeRuntime(
        summaries=[
            SessionSummary(
                session_id="alpha",
                preview="first question",
                updated_at=1_785_567_600.0,
                message_count=2,
            )
        ]
    )
    client = _client(tmp_path, runtime)

    response = client.get("/api/sessions")

    assert response.status_code == 200
    payload = response.json()
    assert payload["sessions"] == [
        {
                "session_id": "alpha",
                "preview": "first question",
                "updated_at": "2026-08-01T07:00:00+00:00",
                "message_count": 2,
                "title": "",
                "channel": "",
                "conversation_type": "",
                "continuation_mode": "writable",
            }
        ]


def test_fork_session_api_returns_private_web_summary(tmp_path):
    runtime = _FakeRuntime()
    client = _client(tmp_path, runtime)

    response = client.post("/api/sessions/qq-group/fork")

    assert response.status_code == 200
    assert response.json()["session_id"] == "session-forked"
    assert response.json()["channel"] == "web"
    assert response.json()["continuation_mode"] == "writable"
    assert runtime.forked_sessions == ["qq-group"]


def test_session_api_returns_messages(tmp_path):
    runtime = _FakeRuntime(
        states={
            "alpha": SessionState(
                session_id="alpha",
                messages=[
                    Message(
                        role="user",
                        content="hello",
                        metadata={"timestamp": 1.0},
                        turn_id="turn-1",
                        turn_index=1,
                    ),
                    Message(role="assistant", content="hi", turn_id="turn-1", turn_index=1),
                    Message(role="tool", content='{"status":"success"}', name="list_dir"),
                ],
            )
        }
    )
    client = _client(tmp_path, runtime)

    response = client.get("/api/sessions/alpha")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "alpha"
    assert payload["metadata"] == {}
    assert [(item["role"], item["content"]) for item in payload["messages"]] == [
        ("user", "hello"),
        ("assistant", "hi"),
        ("tool", '{"status":"success"}'),
    ]
    assert payload["messages"][0]["turn_id"] == "turn-1"
    assert payload["messages"][0]["turn_index"] == 1
    assert payload["messages"][2]["name"] == "list_dir"


def test_chat_api_calls_runtime_and_returns_assistant_message(tmp_path):
    runtime = _FakeRuntime(chat_result="web reply")
    client = _client(tmp_path, runtime)

    response = client.post("/api/chat", json={"session_id": "alpha", "message": "hello"})

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "alpha",
        "assistant": {
            "role": "assistant",
            "content": "web reply",
            "name": None,
            "tool_call_id": None,
            "tool_calls": [],
            "metadata": {},
            "turn_id": "turn-fake",
            "turn_index": None,
            "parent_turn_id": None,
        },
    }
    assert runtime.chat_calls == [("alpha", "hello")]


def test_chat_api_applies_selected_model_before_running_turn(tmp_path):
    runtime = _FakeRuntime(chat_result="web reply")
    client = _client(tmp_path, runtime)

    response = client.post(
        "/api/chat",
        json={"session_id": "alpha", "message": "hello", "model": "model-b"},
    )

    assert response.status_code == 200
    assert runtime.selected_models == ["model-b"]
    assert runtime.chat_calls == [("alpha", "hello")]


def test_chat_api_ignores_auto_model_placeholder(tmp_path):
    runtime = _FakeRuntime(chat_result="web reply")
    client = _client(tmp_path, runtime)

    response = client.post(
        "/api/chat",
        json={"session_id": "alpha", "message": "hello", "model": "auto"},
    )

    assert response.status_code == 200
    assert runtime.selected_models == []
    assert runtime.chat_calls == [("alpha", "hello")]


def test_chat_api_handles_slash_commands_without_calling_llm(tmp_path):
    runtime = _FakeRuntime(command_result="current: model-a")
    client = _client(tmp_path, runtime)

    response = client.post("/api/chat", json={"session_id": "alpha", "message": "/model"})

    assert response.status_code == 200
    assert response.json()["assistant"]["content"] == "current: model-a"
    assert runtime.command_calls == [("alpha", "/model")]
    assert runtime.chat_calls == []


def test_chat_api_does_not_apply_request_model_to_slash_commands(tmp_path):
    runtime = _FakeRuntime(command_result="current: model-a")
    client = _client(tmp_path, runtime)

    response = client.post(
        "/api/chat",
        json={"session_id": "alpha", "message": "/model", "model": "model-b"},
    )

    assert response.status_code == 200
    assert response.json()["assistant"]["content"] == "current: model-a"
    assert runtime.selected_models == []
    assert runtime.command_calls == [("alpha", "/model")]
    assert runtime.chat_calls == []


def test_chat_stream_api_emits_sse_status_delta_and_done(tmp_path):
    runtime = _FakeRuntime(chat_result="streamed reply")
    client = _client(tmp_path, runtime)

    response = client.post(
        "/api/chat/stream",
        json={"session_id": "alpha", "message": "hello"},
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    events = _parse_sse(response.text)
    assert events[0][0] == "status"
    assert events[0][1]["phase"] == "accepted"
    turn_id = events[0][1]["turn_id"]
    assert ("delta", {"content": "streamed reply", "turn_id": turn_id}) in events
    assert events[-1] == (
        "done",
        {
            "session_id": "alpha",
            "turn_id": turn_id,
            "assistant": {
                "role": "assistant",
                "content": "streamed reply",
                "name": None,
                "tool_call_id": None,
                "tool_calls": [],
                "metadata": {},
                "turn_id": turn_id,
                "turn_index": None,
                "parent_turn_id": None,
            },
        },
    )
    assert runtime.chat_calls == [("alpha", "hello")]


def test_chat_stream_api_uses_runtime_streaming_events(tmp_path):
    runtime = _FakeRuntime(stream_chunks=["one ", "two"])
    client = _client(tmp_path, runtime)

    response = client.post(
        "/api/chat/stream",
        json={"session_id": "alpha", "message": "hello"},
    )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    turn_id = events[0][1]["turn_id"]
    assert ("delta", {"content": "one ", "turn_id": turn_id}) in events
    assert ("delta", {"content": "two", "turn_id": turn_id}) in events
    assert events[-1][0] == "done"


def test_chat_stream_api_forwards_runtime_events(tmp_path):
    runtime = _FakeRuntime(runtime_events=[_runtime_event("llm.started", 4)])
    client = _client(tmp_path, runtime)

    response = client.post(
        "/api/chat/stream",
        json={"session_id": "alpha", "message": "hello"},
    )

    events = _parse_sse(response.text)
    turn_id = events[0][1]["turn_id"]
    runtime_payload = next(payload for name, payload in events if name == "runtime")
    assert runtime_payload["type"] == "llm.started"
    assert runtime_payload["turn_id"] == turn_id
    assert runtime_payload["sequence"] == 4


def test_chat_stream_api_handles_slash_commands_without_calling_llm(tmp_path):
    runtime = _FakeRuntime(command_result="unknown command")
    client = _client(tmp_path, runtime)

    response = client.post(
        "/api/chat/stream",
        json={"session_id": "alpha", "message": "/unknown", "model": "model-b"},
    )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    turn_id = events[0][1]["turn_id"]
    assert ("delta", {"content": "unknown command", "turn_id": turn_id}) in events
    assert runtime.selected_models == []
    assert runtime.command_calls == [("alpha", "/unknown")]
    assert runtime.chat_calls == []


def test_chat_stream_api_maps_runtime_errors_to_sse_error(tmp_path):
    runtime = _FakeRuntime(chat_error=LLMProviderError("provider failed"))
    client = _client(tmp_path, runtime)

    response = client.post(
        "/api/chat/stream",
        json={"session_id": "alpha", "message": "hello"},
    )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    turn_id = events[0][1]["turn_id"]
    error_event = events[-1]
    assert error_event[0] == "error"
    assert error_event[1]["turn_id"] == turn_id
    assert error_event[1]["error"]["status"] == 502
    assert error_event[1]["error"]["code"] == "LLM_ERROR"
    assert error_event[1]["error"]["message"] == "provider failed"
    assert error_event[1]["error"]["request_id"].startswith("req-")
    assert error_event[1]["error"]["details"] == {}
    assert "Traceback" not in response.text


def test_models_api_returns_current_endpoint_models(tmp_path):
    runtime = _FakeRuntime()
    client = _client(tmp_path, runtime)

    response = client.get("/api/models")

    assert response.status_code == 200
    assert response.json() == {
        "endpoint": "default",
        "current_model": "model-a",
        "models": ["model-a", "model-b"],
    }


def test_model_preference_api_updates_runtime(tmp_path):
    runtime = _FakeRuntime()
    client = _client(tmp_path, runtime)

    response = client.post("/api/model/preference", json={"model": "model-b"})

    assert response.status_code == 200
    assert response.json() == {
        "endpoint": "default",
        "current_model": "model-b",
        "models": ["model-a", "model-b"],
    }
    assert runtime.selected_models == ["model-b"]


def test_model_preference_api_rejects_invalid_model(tmp_path):
    runtime = _FakeRuntime(model_error=ValueError("unsupported model"))
    client = _client(tmp_path, runtime)

    response = client.post("/api/model/preference", json={"model": "missing"})

    assert response.status_code == 400
    _assert_error(response, 400, "REQUEST_VALIDATION_FAILED", "unsupported model")


def test_session_rename_api_updates_runtime(tmp_path):
    runtime = _FakeRuntime()
    client = _client(tmp_path, runtime)

    response = client.patch("/api/sessions/alpha", json={"title": "New title"})

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "alpha",
        "status": "renamed",
        "title": "New title",
    }
    assert runtime.renamed_sessions == [("alpha", "New title")]


def test_session_delete_api_updates_runtime(tmp_path):
    runtime = _FakeRuntime()
    client = _client(tmp_path, runtime)

    response = client.delete("/api/sessions/alpha")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "alpha",
        "status": "deleted",
        "title": "",
    }
    assert runtime.deleted_sessions == ["alpha"]


def test_chat_api_rejects_empty_message(tmp_path):
    client = _client(tmp_path, _FakeRuntime())

    response = client.post("/api/chat", json={"session_id": "alpha", "message": "   "})

    assert response.status_code == 400
    _assert_error(
        response,
        400,
        "REQUEST_VALIDATION_FAILED",
        "message is required",
        details={"field": "message"},
    )


def test_chat_api_maps_validation_errors_to_invalid_request(tmp_path):
    client = _client(tmp_path, _FakeRuntime())

    response = client.post("/api/chat", json={"message": "hello"})

    assert response.status_code == 400
    _assert_error(
        response,
        400,
        "REQUEST_VALIDATION_FAILED",
        "invalid request",
        details={
            "issues": [
                {
                    "field": "body.session_id",
                    "reason": "missing",
                }
            ]
        },
    )


def test_session_api_maps_invalid_session_id(tmp_path):
    runtime = _FakeRuntime(load_error=InvalidSessionIdError("bad session"))
    client = _client(tmp_path, runtime)

    response = client.get("/api/sessions/bad.session")

    assert response.status_code == 400
    _assert_error(response, 400, "REQUEST_VALIDATION_FAILED", "bad session")


def test_chat_api_maps_runtime_errors(tmp_path):
    cases = [
        (LLMConfigurationError("missing config"), 500, "CONFIG_INVALID"),
        (LLMProviderError("provider failed"), 502, "LLM_ERROR"),
        (RuntimeError("boom with stack detail"), 500, "INTERNAL_ERROR"),
    ]

    for error, expected_status, expected_code in cases:
        runtime = _FakeRuntime(chat_error=error)
        client = _client(tmp_path, runtime)

        response = client.post("/api/chat", json={"session_id": "alpha", "message": "hello"})

        assert response.status_code == expected_status
        assert response.json()["error"]["code"] == expected_code
        assert response.json()["error"]["status"] == expected_status
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
        assert "Traceback" not in response.text


def _client(tmp_path: Path, runtime: "_FakeRuntime") -> TestClient:
    static_dir = tmp_path / "static"
    static_dir.mkdir(exist_ok=True)
    static_dir.joinpath("index.html").write_text("<html>ZhiCe-Agent</html>", encoding="utf-8")
    return TestClient(
        create_app(
            config=_config(tmp_path),
            runtime=runtime,
            static_dir=static_dir,
        )
    )


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.strip().split("\n\n"):
        event = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            if line.startswith("data: "):
                data = line.removeprefix("data: ")
        if event:
            events.append((event, json.loads(data)))
    return events


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        workspace=tmp_path,
        config_dir=tmp_path / "config",
        prompts_dir=tmp_path / "prompts",
        contexts_dir=tmp_path / "contexts",
        sessions_dir=tmp_path / "contexts" / "sessions",
        extends_dir=tmp_path / "extends",
        logs_dir=tmp_path / "logs",
    )


@dataclass
class _FakeRuntime:
    summaries: list[SessionSummary] | None = None
    states: dict[str, SessionState] | None = None
    chat_result: str = "ok"
    chat_error: Exception | None = None
    load_error: Exception | None = None
    model_error: Exception | None = None
    command_result: str | None = None
    stream_chunks: list[str] | None = None
    runtime_events: list[dict] | None = None

    def __post_init__(self) -> None:
        self.chat_calls: list[tuple[str, str]] = []
        self.command_calls: list[tuple[str, str]] = []
        self.selected_models: list[str] = []
        self.renamed_sessions: list[tuple[str, str]] = []
        self.deleted_sessions: list[str] = []
        self.forked_sessions: list[str] = []

    def list_sessions(self) -> list[SessionSummary]:
        return self.summaries or []

    def load_session(self, session_id: str) -> SessionState:
        if self.load_error:
            raise self.load_error
        if self.states and session_id in self.states:
            return self.states[session_id]
        return SessionState(session_id=session_id, messages=[])

    def run_chat_events(
        self,
        session_id: str,
        message: str,
        *,
        turn_id: str | None = None,
        on_event=None,
    ):
        command = self.handle_command(session_id, message)
        if command is not None:
            if on_event is not None:
                on_event({"type": "text_delta", "content": command})
            return ChatTurnResult(content=command, turn_id=turn_id or "")
        self.chat_calls.append((session_id, message))
        if self.chat_error:
            raise self.chat_error
        for runtime_event in self.runtime_events or []:
            event = dict(runtime_event)
            event["session_id"] = session_id
            event["turn_id"] = turn_id or "turn-fake"
            if on_event is not None:
                on_event(event)
        chunks = self.stream_chunks or [self.chat_result]
        for chunk in chunks:
            if on_event is not None:
                on_event({"type": "text_delta", "content": chunk})
        return ChatTurnResult(content="".join(chunks), turn_id=turn_id or "turn-fake")
    def handle_command(self, session_id: str, message: str) -> str | None:
        if not message.startswith("/"):
            return None
        self.command_calls.append((session_id, message))
        return self.command_result

    def current_model_label(self) -> str:
        return "default/model-a"

    def model_state(self) -> ModelState:
        current_model = self.selected_models[-1] if self.selected_models else "model-a"
        return ModelState(
            endpoint="default",
            current_model=current_model,
            models=["model-a", "model-b"],
        )

    def set_model_preference(self, model: str) -> ModelState:
        if self.model_error:
            raise self.model_error
        self.selected_models.append(model)
        return self.model_state()

    def rename_session(self, session_id: str, title: str):
        self.renamed_sessions.append((session_id, title))
        return SessionSummary(
            session_id=session_id,
            preview="first question",
            updated_at=1.0,
            message_count=2,
            title=title,
        )

    def delete_session(self, session_id: str) -> None:
        self.deleted_sessions.append(session_id)

    def fork_session_to_web(self, source_session_id: str):
        self.forked_sessions.append(source_session_id)
        return SessionSummary(
            session_id="session-forked",
            preview="group context",
            updated_at=1.0,
            message_count=2,
            channel="web",
        )


class _FakeWeixinBinding:
    def __init__(self):
        self.actor_user_ids = []

    def start(self, actor):
        self.actor_user_ids.append(actor.user_id)
        return SimpleNamespace(
            attempt_id="wxbind-safe",
            status="waiting_scan",
            expires_at="2026-07-24T10:00:00+00:00",
            qr_data="data:image/png;base64,safe",
            error_code="",
        )


def _runtime_event(event_type: str, sequence: int) -> dict:
    return {
        "protocol_version": 1,
        "event_id": f"event-{sequence}",
        "type": event_type,
        "status": "started",
        "timestamp": "2026-07-20T00:00:00Z",
        "sequence": sequence,
        "session_id": "",
        "turn_id": "",
        "request_id": "",
        "tool_call_id": "",
        "tool_call_record_id": "",
        "parent_event_id": "",
        "display": {"title": "正在请求模型"},
        "ui_metadata": {},
        "metadata": {},
    }


def _assert_error(response, status: int, code: str, message: str, *, details=None) -> None:
    payload = response.json()["error"]
    assert response.status_code == status
    assert payload["status"] == status
    assert payload["code"] == code
    assert payload["message"] == message
    assert payload["request_id"] == response.headers["X-Request-ID"]
    assert payload["details"] == (details or {})
