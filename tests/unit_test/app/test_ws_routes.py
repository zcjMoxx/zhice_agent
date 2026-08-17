from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agent.app.gateway import create_app
from agent.app.runtime import ChatTurnResult
from agent.applications.travel.service import TravelApplicationError
from agent.config import AppConfig
from agent.protocols.llm import LLMProviderError


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
    runtime = _WsRuntime(runtime_events=[_runtime_event("skill.started", 2)])
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
    assert runtime_frame["data"]["type"] == "skill.started"
    assert runtime_frame["data"]["skill_run_id"] == "skill-run-1"
    assert text["event"] == "channel_text"
    assert done["data"]["type"] == "done"


def test_ws_routes_child_runtime_event_to_root_session_envelope(tmp_path):
    child = _runtime_event("tool.completed", 3)
    child.update(
        {
            "session_id": "child-session",
            "turn_id": "child-turn",
            "root_session_id": "alpha",
            "root_turn_id": "root-turn",
            "agent_id": "subagent-one",
        }
    )
    runtime = _WsRuntime(runtime_events=[child])
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "message", "session_id": "alpha", "content": "hello"})
        websocket.receive_json()
        runtime_frame = websocket.receive_json()

    assert runtime_frame["session_id"] == "alpha"
    assert runtime_frame["turn_id"] == "root-turn"
    assert runtime_frame["data"]["session_id"] == "child-session"
    assert runtime_frame["data"]["root_session_id"] == "alpha"
    assert runtime_frame["data"]["agent_id"] == "subagent-one"


def test_ws_forwards_travel_planning_confirmation_before_turn_completion(tmp_path):
    event = _runtime_event("travel.planning_confirmed", 2)
    event.update(
        {
            "status": "completed",
            "display": {"title": "旅行条件已确认", "visibility": "internal"},
            "ui_metadata": {
                "detail_type": "travel_planning_confirmed",
                "detail_data": {"phase": "planning"},
            },
            "metadata": {"phase": "planning"},
        }
    )
    runtime = _WsRuntime(runtime_events=[event])
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {"type": "message", "session_id": "travel-a", "content": "确认"}
        )
        accepted = websocket.receive_json()
        confirmed = websocket.receive_json()

    assert accepted["data"]["type"] == "accepted"
    assert confirmed["event"] == "runtime_event"
    assert confirmed["data"]["type"] == "travel.planning_confirmed"
    assert confirmed["data"]["ui_metadata"]["detail_data"]["phase"] == "planning"


def test_ws_creates_travel_application_session_with_isolated_channel(tmp_path):
    runtime = _WsRuntime()
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "new_session", "application": "travel"})
        created = websocket.receive_json()

    assert created["event"] == "session_created"
    assert runtime.created_sessions == [(created["data"]["session_id"], "travel")]


def test_ws_auto_continues_a_travel_turn_until_plan_ready(tmp_path):
    runtime = _TravelWsRuntime(outcomes=["text", "plan"])
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "message", "session_id": "travel-a", "content": "plan"})
        frames = _receive_until_terminal(websocket)

    assert frames[-1]["data"]["type"] == "done"
    assert any(frame.get("event") == "runtime_event" and frame["data"]["type"] == "travel.plan_ready" for frame in frames)
    assert len(runtime.chat_calls) == 2
    assert runtime.chat_calls[1][1] == "continue travel"


def test_ws_uses_server_validated_candidate_continuation_for_first_turn(tmp_path):
    runtime = _SelectedCandidateWsRuntime(outcomes=["plan"])
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "message",
                "session_id": "travel-a",
                "content": "untrusted candidate text",
            }
        )
        frames = _receive_until_terminal(websocket)

    assert frames[-1]["data"]["type"] == "done"
    assert runtime.chat_calls[0][1] == "continue selected candidate-a"


def test_ws_returns_structured_error_when_travel_continuations_are_exhausted(tmp_path):
    runtime = _TravelWsRuntime(outcomes=["text"] * 6)
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "message", "session_id": "travel-a", "content": "plan"})
        frames = _receive_until_terminal(websocket)

    assert frames[-1]["data"]["type"] == "error"
    assert frames[-1]["data"]["error"]["code"] == "TRAVEL_PLAN_NOT_FINALIZED"
    assert len(runtime.chat_calls) == 6


def test_ws_retries_one_transient_llm_failure_from_persisted_travel_state(tmp_path):
    runtime = _TravelWsRuntime(outcomes=["text", "provider_error", "plan"])
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "message", "session_id": "travel-a", "content": "plan"})
        frames = _receive_until_terminal(websocket)

    assert frames[-1]["data"]["type"] == "done"
    assert any(
        frame.get("event") == "runtime_event"
        and frame["data"]["type"] == "travel.plan_ready"
        for frame in frames
    )
    assert [call[1] for call in runtime.chat_calls] == [
        "plan",
        "continue travel",
        "continue travel",
    ]


def test_ws_clarification_event_pauses_travel_without_auto_continuation(tmp_path):
    runtime = _TravelWsRuntime(outcomes=["clarification"])
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "message", "session_id": "travel-a", "content": "plan"})
        frames = _receive_until_terminal(websocket)

    clarification = next(frame for frame in frames if frame.get("event") == "runtime_event")
    assert clarification["data"]["type"] == "travel.clarification_required"
    assert frames[-1]["data"]["type"] == "done"
    assert len(runtime.chat_calls) == 1


def test_ws_rejects_unknown_session_application_without_creating_session(tmp_path):
    runtime = _WsRuntime()
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "new_session", "application": "unknown"})
        error = websocket.receive_json()

    assert error["event"] == "channel_status"
    assert error["data"]["type"] == "error"
    assert runtime.created_sessions == []


def test_ws_new_session_without_application_preserves_external_profile(tmp_path):
    runtime = _WsRuntime()
    client = _client(tmp_path, runtime)

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "hello", "client": "external"})
        websocket.receive_json()
        websocket.send_json({"type": "new_session"})
        created = websocket.receive_json()

    assert runtime.created_sessions == [(created["data"]["session_id"], "external")]


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
        self.created_sessions: list[tuple[str, str]] = []

    def create_session(self, session_id: str, channel: str = "web"):
        self.created_sessions.append((session_id, channel))

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
            event["session_id"] = event.get("session_id") or session_id
            event["turn_id"] = event.get("turn_id") or turn_id or "turn-ws"
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


class _TravelWsRuntime(_WsRuntime):
    def __init__(self, outcomes: list[str]):
        super().__init__(chunks=[])
        self.outcomes = outcomes

    def travel_continuation_message(self, session_id: str) -> str:
        if session_id != "travel-a":
            raise TravelApplicationError(
                "TRAVEL_GENERATION_NOT_FOUND", "not travel", status_code=404
            )
        return "continue travel"

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
        outcome = self.outcomes[len(self.chat_calls) - 1]
        if outcome == "provider_error":
            raise LLMProviderError("temporary provider failure", retryable=True)
        if outcome == "plan" and on_event is not None:
            on_event(_travel_runtime_event("travel.plan_ready", turn_id or "turn-ws"))
        elif outcome == "clarification" and on_event is not None:
            on_event(_travel_runtime_event("travel.clarification_required", turn_id or "turn-ws"))
        return ChatTurnResult(content=outcome, turn_id=turn_id or "turn-ws")


class _SelectedCandidateWsRuntime(_TravelWsRuntime):
    def travel_candidate_continuation_message(self, session_id: str) -> str:
        if session_id != "travel-a":
            raise TravelApplicationError(
                "TRAVEL_GENERATION_NOT_FOUND", "not travel", status_code=404
            )
        return "continue selected candidate-a"


def _receive_until_terminal(websocket):
    frames = []
    while True:
        frame = websocket.receive_json()
        frames.append(frame)
        if frame.get("event") == "channel_status" and frame.get("data", {}).get("type") in {
            "done", "error", "stopped"
        }:
            return frames


def _travel_runtime_event(event_type: str, turn_id: str) -> dict:
    event = _runtime_event(event_type, 1)
    event["session_id"] = "travel-a"
    event["turn_id"] = turn_id
    event["status"] = "completed" if event_type == "travel.plan_ready" else "waiting"
    event["metadata"] = {"plan_id": "plan-a"} if event_type == "travel.plan_ready" else {"question_count": 1}
    event["ui_metadata"] = (
        {} if event_type == "travel.plan_ready" else {"detail_type": "summary", "detail_data": {"questions": ["预算档位？"]}}
    )
    return event


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
        "skill_run_id": "skill-run-1",
        "parent_event_id": "",
        "display": {"title": "正在整理上下文"},
        "ui_metadata": {},
        "metadata": {},
    }
