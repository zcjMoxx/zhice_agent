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
    assert first == {"event": "channel_text", "data": "one ", "session_id": "alpha"}
    assert second == {"event": "channel_text", "data": "two", "session_id": "alpha"}
    assert done["event"] == "channel_status"
    assert done["data"]["type"] == "done"
    assert done["data"]["assistant"]["content"] == "one two"
    assert runtime.chat_calls == [("alpha", "hello", "web")]


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


def test_ws_stop_text_uses_same_cancel_path(tmp_path):
    runtime = _WsRuntime()
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
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
    assert text == {"event": "channel_text", "data": "history", "session_id": "alpha"}
    assert done["data"]["type"] == "done"
    assert runtime.chat_calls == [("alpha", "/history", "external")]


def test_ws_hello_unknown_client_is_rejected(tmp_path):
    runtime = _WsRuntime()
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "hello", "client": "mobile"})
        error = websocket.receive_json()

    assert error == {
        "event": "channel_status",
        "data": {
            "type": "error",
            "error": {"code": "INVALID_REQUEST", "message": "unknown WS client; supported clients: web, external"},
        },
    }


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
    def __init__(self, chunks: list[str] | None = None):
        self.chunks = chunks or ["ok"]
        self.chat_calls: list[tuple[str, str]] = []
        self.cancelled_sessions: list[str] = []

    def run_chat_events(self, session_id: str, message: str, on_event=None, *, command_profile: str = "web"):
        self.chat_calls.append((session_id, message, command_profile))
        for chunk in self.chunks:
            if on_event is not None:
                on_event({"type": "text_delta", "content": chunk})
        return ChatTurnResult(content="".join(self.chunks), turn_id="turn-ws")

    def cancel_session(self, session_id: str):
        self.cancelled_sessions.append(session_id)
        return {"session_id": session_id, "cancelled": 1}
