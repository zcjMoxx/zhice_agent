from __future__ import annotations

import json
from pathlib import Path

from agent.app.runtime import (
    DEFAULT_WEB_HISTORY_MESSAGES,
    EXTERNAL_COMMAND_PROFILE,
    WEB_COMMAND_PROFILE,
    WebRuntime,
)
from agent.config import AppConfig
from agent.message import Message
from agent.prompt_loader import PromptLoader
from agent.protocols.llm import LLMResponse
from agent.protocols.session import SessionState, SessionSummary


def test_web_context_history_message_guard_matches_context_builder_default():
    assert DEFAULT_WEB_HISTORY_MESSAGES == 60


def test_web_profile_rejects_history_command(tmp_path):
    runtime = _runtime(tmp_path)

    result = runtime.handle_command(
        "alpha",
        "/history",
        command_profile=WEB_COMMAND_PROFILE,
    )

    assert result == "Command not supported for this client: `/history`.\n\nUse `/help` to see available commands."


def test_web_profile_rejects_stop_command(tmp_path):
    runtime = _runtime(tmp_path)

    result = runtime.handle_command(
        "alpha",
        "/stop",
        command_profile=WEB_COMMAND_PROFILE,
    )

    assert result == "Command not supported for this client: `/stop`.\n\nUse `/help` to see available commands."


def test_external_profile_allows_history_command(tmp_path):
    runtime = _runtime(tmp_path)

    result = runtime.handle_command(
        "alpha",
        "/history",
        command_profile=EXTERNAL_COMMAND_PROFILE,
    )

    assert result is not None
    assert "Recent history:" in result
    assert "**user**: hello from user" in result
    assert "**assistant**: hello from assistant" in result


def test_web_help_hides_external_only_commands(tmp_path):
    runtime = _runtime(tmp_path)

    result = runtime.handle_command("/alpha", "/help", command_profile=WEB_COMMAND_PROFILE)

    assert result is not None
    assert "/history" not in result
    assert "/exit" not in result
    assert "/stop" not in result
    assert "- `/model` - show or switch the preferred model" in result
    assert "- `/memory` - show current Memory" in result
    assert "- `/mcp` - show available MCP capabilities" in result
    assert "/memory list" not in result
    assert "/memory summarize" not in result
    assert "- `/model list`" not in result
    assert "- `/sessions` - list or manage recent sessions" in result
    assert "- `/sessions rename" not in result
    assert "Tip:" not in result


def test_external_help_lists_external_commands(tmp_path):
    runtime = _runtime(tmp_path)

    result = runtime.handle_command("/alpha", "/help", command_profile=EXTERNAL_COMMAND_PROFILE)

    assert result is not None
    assert "- `/stop` - stop the active turn" in result
    assert "- `/history` - show recent messages" in result
    assert "- `/exit` - close this WebSocket connection" in result
    assert "- `/model list`" not in result
    assert "Tip:" not in result


def test_sessions_list_shows_subcommand_tip(tmp_path):
    runtime = _runtime(tmp_path)

    result = runtime.handle_command("alpha", "/sessions")

    assert result is not None
    assert "Recent sessions:" in result
    assert "Tip: use `/sessions rename <id> <title>`" in result
    assert "`/sessions delete [id]`" in result
    assert "omitting id clears the current session like `/reset`" in result


def test_quit_is_not_a_supported_command(tmp_path):
    runtime = _runtime(tmp_path)

    result = runtime.handle_command("alpha", "/quit", command_profile=EXTERNAL_COMMAND_PROFILE)

    assert result == "Unsupported command: `/quit`.\n\nUse `/help` to see available commands."


def test_sessions_rename_subcommand_updates_session_title(tmp_path):
    runtime = _runtime(tmp_path)

    result = runtime.handle_command("alpha", "/sessions rename alpha New title")

    assert result == "Session renamed: `alpha`"
    assert runtime.sessions.renamed_sessions == [("alpha", "New title")]


def test_sessions_delete_subcommand_deletes_named_session(tmp_path):
    runtime = _runtime(tmp_path)

    result = runtime.handle_command("alpha", "/sessions delete beta")

    assert result == "Session deleted: `beta`"
    assert runtime.sessions.deleted_sessions == ["beta"]
    assert runtime.sessions.cleared_sessions == []


def test_sessions_delete_without_id_clears_current_session(tmp_path):
    runtime = _runtime(tmp_path)

    result = runtime.handle_command("alpha", "/sessions delete")

    assert result == "Session cleared: `alpha`"
    assert runtime.sessions.cleared_sessions == ["alpha"]
    assert runtime.sessions.deleted_sessions == []


def test_sessions_invalid_subcommand_returns_usage(tmp_path):
    runtime = _runtime(tmp_path)

    result = runtime.handle_command("alpha", "/sessions rename alpha")

    assert result is not None
    assert "Available sessions commands:" in result
    assert "/sessions rename <id> <title>" in result
    assert "/sessions delete [id]" in result


def test_memory_command_defaults_to_showing_current_memory(tmp_path):
    runtime = _runtime(tmp_path)

    result = runtime.handle_command("alpha", "/memory")

    assert result is not None
    assert result.startswith("Memory is empty.")


def test_mcp_command_uses_shared_runtime_summary(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.mcp_runtime = _McpRuntime()

    assert runtime.handle_command("alpha", "/mcp") == "MCP summary"
    assert runtime.handle_command("alpha", "/mcp details") == "Usage: `/mcp`"


def test_memory_command_shows_scoped_memory(tmp_path):
    runtime = _runtime(tmp_path)
    memory = tmp_path / "contexts" / "memory" / "MEMORY.md"
    memory.parent.mkdir(parents=True, exist_ok=True)
    memory.write_text(
        "# ZhiCe-Agent Memory\n\n<!-- zhice-memory:start -->\n\n"
        "## profile\n\n## preferences\n\n- 喜欢吃西瓜\n\n"
        "## projects\n\n## constraints\n\n## decisions\n\n"
        "<!-- zhice-memory:end -->\n",
        encoding="utf-8",
    )

    result = runtime.handle_command("alpha", "/memory")

    assert result is not None
    assert "preferences:" in result
    assert "- 喜欢吃西瓜" in result


def test_removed_memory_subcommands_return_current_usage(tmp_path):
    runtime = _runtime(tmp_path)

    for command in (
        "/memory session",
        "/memory list",
        "/memory extract alpha",
        "/memory summarize",
    ):
        result = runtime.handle_command("alpha", command)
        assert result is not None
        assert result == "Usage: `/memory`"


def test_run_chat_events_passes_external_turn_id_to_agent_loop(tmp_path):
    agent_loop = _RecordingAgentLoop()
    runtime = _runtime(tmp_path, agent_loop=agent_loop)

    result = runtime.run_chat_events("alpha", "hello", turn_id="turn-web")

    assert result.turn_id == "turn-web"
    assert result.content == "ok"
    assert agent_loop.calls == [
        {
            "session_id": "alpha",
            "message": "hello",
            "turn_id": "turn-web",
            "has_token": True,
        }
    ]


def test_run_chat_events_logs_web_runtime_lifecycle(tmp_path, caplog):
    agent_loop = _RecordingAgentLoop()
    runtime = _runtime(tmp_path, agent_loop=agent_loop)
    caplog.set_level("DEBUG", logger="zcagent.agent")

    runtime.run_chat_events("alpha", "hello", turn_id="turn-web")

    records = [record for record in caplog.records if record.name == "zcagent.agent.web"]
    assert records == []


def test_run_chat_events_resets_idle_memory_job(tmp_path):
    scheduler = _RecordingMemoryScheduler()
    runtime = _runtime(tmp_path, agent_loop=_RecordingAgentLoop())
    runtime.memory_extraction_enabled = True
    runtime.memory_scheduler = scheduler

    runtime.run_chat_events("alpha", "first")
    runtime.run_chat_events("alpha", "second")

    assert scheduler.calls == [
        ("cancel", "workspace-operator", "alpha"),
        ("schedule", "workspace-operator", "alpha"),
        ("cancel", "workspace-operator", "alpha"),
        ("schedule", "workspace-operator", "alpha"),
    ]


def _runtime(tmp_path: Path, *, agent_loop=None, sessions=None, llm=None) -> WebRuntime:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir(exist_ok=True)
    (prompts_dir / "memory_extraction.md").write_text("extract prompt", encoding="utf-8")
    return WebRuntime(
        config=AppConfig(
            workspace=tmp_path,
            config_dir=tmp_path / "config",
            prompts_dir=prompts_dir,
            contexts_dir=tmp_path / "contexts",
            sessions_dir=tmp_path / "contexts" / "sessions",
            extends_dir=tmp_path / "extends",
            logs_dir=tmp_path / "logs",
        ),
        sessions=sessions or _SessionStore(),
        agent_loop=agent_loop or _AgentLoop(),
        llm=llm or _Llm(),
        prompt_loader=PromptLoader(prompts_dir),
    )


class _SessionStore:
    def __init__(self) -> None:
        self.cleared_sessions: list[str] = []
        self.renamed_sessions: list[tuple[str, str]] = []
        self.deleted_sessions: list[str] = []

    def load(self, session_id: str) -> SessionState:
        return SessionState(
            session_id=session_id,
            messages=[
                Message(
                    role="user",
                    content="hello from user",
                    turn_id="turn-1",
                    turn_index=1,
                ),
                Message(
                    role="assistant",
                    content="hello from assistant",
                    turn_id="turn-1",
                    turn_index=1,
                ),
            ],
        )

    def append(self, session_id: str, messages: list[Message]) -> None:
        return None

    def clear(self, session_id: str) -> None:
        self.cleared_sessions.append(session_id)

    def rename(self, session_id: str, title: str) -> None:
        self.renamed_sessions.append((session_id, title))

    def delete(self, session_id: str) -> None:
        self.deleted_sessions.append(session_id)

    def list_sessions(self) -> list[SessionSummary]:
        return [
            SessionSummary(
                session_id="alpha",
                preview="hello from user",
                updated_at=1.0,
                message_count=2,
            )
        ]


class _ThreeTurnSessionStore(_SessionStore):
    def load(self, session_id: str) -> SessionState:
        messages = []
        for index in range(1, 4):
            messages.extend(
                [
                    Message(
                        role="user",
                        content="先给结论，最多三点",
                        turn_id=f"turn-{index}",
                        turn_index=index,
                    ),
                    Message(
                        role="assistant",
                        content="好的",
                        turn_id=f"turn-{index}",
                        turn_index=index,
                    ),
                ]
            )
        return SessionState(session_id=session_id, messages=messages)


class _AgentLoop:
    def run_turn(self, *_args, **_kwargs) -> str:
        raise AssertionError("commands should not enter AgentLoop")


class _RecordingAgentLoop:
    def __init__(self) -> None:
        self.calls = []

    def run_turn(
        self,
        session_id,
        message,
        *,
        turn_id=None,
        on_event=None,
        cancellation_token=None,
    ) -> str:
        self.calls.append(
            {
                "session_id": session_id,
                "message": message,
                "turn_id": turn_id,
                "has_token": cancellation_token is not None,
            }
        )
        if on_event is not None:
            on_event({"type": "text_delta", "content": "ok"})
        return "ok"


class _RecordingMemoryScheduler:
    def __init__(self):
        self.calls = []

    def schedule(self, actor_key, actor, session_id):
        self.calls.append(("schedule", actor_key, session_id))
        return True

    def cancel(self, actor_key, session_id):
        self.calls.append(("cancel", actor_key, session_id))
        return True

    def shutdown(self):
        return None


class _Llm:
    def chat(self, messages, tools=None):
        return LLMResponse(content="ok")


class _McpRuntime:
    def format_capabilities(self):
        return "MCP summary"


class _ExtractionLlm:
    def chat(self, messages, tools=None):
        return LLMResponse(
            content=json.dumps(
                {
                    "memories": [
                        {
                            "category": "preferences",
                            "content": "回答时先给结论，最多三点。",
                            "confidence": "high",
                            "evidence": [
                                {"turn_index": 1, "quote": "先给结论，最多三点"},
                                {"turn_index": 3, "quote": "先给结论，最多三点"},
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
