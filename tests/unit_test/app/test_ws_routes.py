from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agent.app.gateway import create_app
from agent.app.runtime import ChatTurnResult
from agent.config import AppConfig


def test_ws_message_streams_text_and_done(tmp_path):
    runtime = _WsRuntime(chunks=["one ", "two"])
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        assert websocket.receive_json()["event"] == "connected"
        websocket.send_json({"type": "message", "session_id": "alpha", "content": "hello"})

        accepted = websocket.receive_json()
        first = websocket.receive_json()
        second = websocket.receive_json()
        done = websocket.receive_json()

    assert accepted["event"] == "channel_status"
    assert accepted["data"]["type"] == "accepted"
    turn_id = accepted["data"]["turn_id"]
    assert accepted["turn_id"] == turn_id
    assert first == {
        "event": "channel_text",
        "data": "one ",
        "session_id": "alpha",
        "turn_id": turn_id,
    }
    assert second == {
        "event": "channel_text",
        "data": "two",
        "session_id": "alpha",
        "turn_id": turn_id,
    }
    assert done["event"] == "channel_status"
    assert done["data"]["type"] == "done"
    assert done["data"]["turn_id"] == turn_id
    assert done["turn_id"] == turn_id
    assert done["data"]["assistant"]["content"] == "one two"
    assert runtime.chat_calls == [("alpha", "hello", "web", turn_id)]
    assert runtime.request_ids == [""]


def test_ws_forwards_runtime_event_envelope(tmp_path):
    runtime = _WsRuntime(runtime_events=[_runtime_event("context.started", 2)])
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        assert websocket.receive_json()["event"] == "connected"
        websocket.send_json({"type": "message", "session_id": "alpha", "content": "hello"})
        accepted = websocket.receive_json()
        runtime_frame = websocket.receive_json()
        text = websocket.receive_json()
        done = websocket.receive_json()

    turn_id = accepted["turn_id"]
    assert runtime_frame["event"] == "runtime_event"
    assert runtime_frame["session_id"] == "alpha"
    assert runtime_frame["turn_id"] == turn_id
    assert runtime_frame["data"]["type"] == "context.started"
    assert text["event"] == "channel_text"
    assert done["data"]["type"] == "done"


def test_ws_stop_frame_calls_runtime_cancel(tmp_path):
    runtime = _WsRuntime()
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "stop", "session_id": "alpha"})
        stopped = websocket.receive_json()

    assert stopped["event"] == "channel_status"
    assert stopped["data"]["type"] == "stopped"
    assert stopped["data"]["cancelled"] == 1
    assert runtime.cancelled_sessions == ["alpha"]


def test_ws_web_stop_text_is_a_message_command_not_stop_frame(tmp_path):
    runtime = _WsRuntime()
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "message", "session_id": "alpha", "content": "/stop"})
        accepted = websocket.receive_json()
        text = websocket.receive_json()
        done = websocket.receive_json()

    assert accepted["event"] == "channel_status"
    assert accepted["data"]["type"] == "accepted"
    turn_id = accepted["data"]["turn_id"]
    assert text == {"event": "channel_text", "data": "ok", "session_id": "alpha", "turn_id": turn_id}
    assert done["event"] == "channel_status"
    assert done["data"]["type"] == "done"
    assert runtime.cancelled_sessions == []
    assert runtime.chat_calls == [("alpha", "/stop", "web", turn_id)]


def test_ws_external_stop_text_uses_same_cancel_path(tmp_path):
    runtime = _WsRuntime()
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "hello", "client": "external"})
        websocket.receive_json()
        websocket.send_json({"type": "message", "session_id": "alpha", "content": "/stop"})
        stopped = websocket.receive_json()

    assert stopped["event"] == "channel_status"
    assert stopped["data"]["type"] == "stopped"
    assert runtime.cancelled_sessions == ["alpha"]
    assert runtime.chat_calls == []


def test_ws_hello_web_reports_web_capabilities(tmp_path):
    runtime = _WsRuntime()
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "hello", "client": "web"})
        hello = websocket.receive_json()

    assert hello == {
        "event": "hello",
        "data": {
            "client": "web",
            "command_profile": "web",
            "capabilities": {"history_command": False, "exit_command": False},
        },
    }


def test_ws_external_channel_is_passed_to_runtime(tmp_path):
    runtime = _WsRuntime(chunks=["history"])
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "hello", "client": "external"})
        hello = websocket.receive_json()
        websocket.send_json({"type": "message", "session_id": "alpha", "content": "/history"})
        accepted = websocket.receive_json()
        text = websocket.receive_json()
        done = websocket.receive_json()

    assert hello["data"]["command_profile"] == "external"
    assert hello["data"]["capabilities"] == {"history_command": True, "exit_command": True}
    assert accepted["data"]["type"] == "accepted"
    turn_id = accepted["data"]["turn_id"]
    assert text == {
        "event": "channel_text",
        "data": "history",
        "session_id": "alpha",
        "turn_id": turn_id,
    }
    assert done["data"]["type"] == "done"
    assert runtime.chat_calls == [("alpha", "/history", "external", turn_id)]


def test_ws_hello_unknown_client_is_rejected(tmp_path):
    runtime = _WsRuntime()
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "hello", "client": "mobile"})
        error = websocket.receive_json()

    assert error["event"] == "channel_status"
    assert error["data"]["type"] == "error"
    assert error["data"]["error"]["status"] == 400
    assert error["data"]["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    assert error["data"]["error"]["message"] == (
        "unknown WS client; supported clients: web, external"
    )
    assert error["data"]["error"]["request_id"].startswith("ws-")
    assert error["data"]["error"]["details"] == {}


def test_ws_external_exit_closes_current_connection(tmp_path):
    runtime = _WsRuntime()
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "hello", "client": "external"})
        websocket.receive_json()
        websocket.send_json({"type": "message", "content": "/exit"})
        closing = websocket.receive_json()

    assert closing == {
        "event": "channel_status",
        "data": {"type": "closing", "reason": "exit_command"},
    }
    assert runtime.chat_calls == []


def test_ws_mcp_elicitation_response_is_forwarded(tmp_path):
    runtime = _WsRuntime()
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "mcp_elicitation_response",
                "session_id": "alpha",
                "interaction_id": "mcp-int-1",
                "action": "accept",
                "response": {"code": "1234"},
            }
        )
        response = websocket.receive_json()

    assert response == {
        "event": "mcp_elicitation_response",
        "data": {"interaction_id": "mcp-int-1", "accepted": True},
        "session_id": "alpha",
    }
    assert runtime.mcp_interactions == [("mcp-int-1", "accept", {"code": "1234"})]


def _client(tmp_path: Path, runtime: "_WsRuntime") -> TestClient:
    static_dir = tmp_path / "static"
    static_dir.mkdir(exist_ok=True)
    static_dir.joinpath("index.html").write_text("<html>ZhiCe-Agent</html>", encoding="utf-8")
    return TestClient(create_app(config=_config(tmp_path), runtime=runtime, static_dir=static_dir))


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


class _WsRuntime:
    def __init__(self, chunks: list[str] | None = None, runtime_events: list[dict] | None = None):
        self.chunks = chunks or ["ok"]
        self.runtime_events = runtime_events or []
        self.chat_calls: list[tuple[str, str, str, str]] = []
        self.request_ids: list[str] = []
        self.cancelled_sessions: list[str] = []
        self.mcp_interactions: list[tuple[str, str, dict | None]] = []

    def run_chat_events(
        self,
        session_id: str,
        message: str,
        *,
        turn_id: str | None = None,
        on_event=None,
        command_profile: str = "web",
        request_id: str = "",
    ):
        self.chat_calls.append((session_id, message, command_profile, turn_id or ""))
        self.request_ids.append(request_id)
        for runtime_event in self.runtime_events:
            event = dict(runtime_event)
            event["session_id"] = session_id
            event["turn_id"] = turn_id or "turn-ws"
            if on_event is not None:
                on_event(event)
        for chunk in self.chunks:
            if on_event is not None:
                on_event({"type": "text_delta", "content": chunk})
        return ChatTurnResult(content="".join(self.chunks), turn_id=turn_id or "turn-ws")

    def cancel_session(self, session_id: str):
        self.cancelled_sessions.append(session_id)
        return {"session_id": session_id, "cancelled": 1}

    def submit_mcp_interaction(self, interaction_id: str, action: str, content=None):
        self.mcp_interactions.append((interaction_id, action, content))
        return True


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
        "display": {"title": "正在整理上下文"},
        "ui_metadata": {},
        "metadata": {},
    }
