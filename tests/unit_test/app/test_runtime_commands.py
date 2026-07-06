from __future__ import annotations

from pathlib import Path

from agent.app.runtime import (
    EXTERNAL_COMMAND_PROFILE,
    WEB_COMMAND_PROFILE,
    WebRuntime,
)
from agent.config import AppConfig
from agent.message import Message
from agent.protocols.session import SessionState, SessionSummary


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


def _runtime(tmp_path: Path) -> WebRuntime:
    return WebRuntime(
        config=AppConfig(
            workspace=tmp_path,
            config_dir=tmp_path / "config",
            prompts_dir=tmp_path / "prompts",
            contexts_dir=tmp_path / "contexts",
            sessions_dir=tmp_path / "contexts" / "sessions",
            extends_dir=tmp_path / "extends",
            logs_dir=tmp_path / "logs",
        ),
        sessions=_SessionStore(),
        agent_loop=_AgentLoop(),
        llm=_Llm(),
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
                Message(role="user", content="hello from user"),
                Message(role="assistant", content="hello from assistant"),
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


class _AgentLoop:
    def run_turn(self, *_args, **_kwargs) -> str:
        raise AssertionError("commands should not enter AgentLoop")


class _Llm:
    pass
