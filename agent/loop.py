"""Minimal no-tool Agent loop."""

from __future__ import annotations

from pathlib import Path

from agent.console import console
from agent.context import ContextBuilder
from agent.message import Message
from agent.protocols.llm import LLMConfigurationError, LLMProvider, LLMProviderError
from agent.protocols.session import SessionStore

ASSISTANT_ERROR_TEXT = "LLM call failed. Check the workspace configuration and retry."


class AgentLoop:
    """Run one no-tool chat turn and persist the resulting session messages."""

    def __init__(
        self,
        llm: LLMProvider,
        sessions: SessionStore,
        context_builder: ContextBuilder,
        workspace: Path,
    ):
        self.llm = llm
        self.sessions = sessions
        self.context_builder = context_builder
        self.workspace = Path(workspace).expanduser().resolve()

    def run_turn(self, session_id: str, user_text: str) -> str:
        """Run one user turn, saving user and assistant/error messages."""

        session = self.sessions.load(session_id)
        user_msg = Message(role="user", content=user_text)
        messages = self.context_builder.build(
            history=session.messages,
            user_message=user_msg,
            workspace=self.workspace,
            session_id=session_id,
        )

        try:
            response = self.llm.chat(messages=messages, tools=None)
        except Exception as exc:  # noqa: BLE001 - the loop must persist failed turns.
            error_text = _format_llm_error(exc, self.workspace)
            assistant_msg = Message(
                role="assistant",
                content=error_text,
                metadata={
                    "is_error": True,
                    "error_type": type(exc).__name__,
                },
            )
            save_error = _append_session_messages(
                self.sessions, session_id, [user_msg, assistant_msg], self.workspace
            )
            return _with_save_error(error_text, save_error)

        assistant_msg = Message(
            role="assistant",
            content=str(response.content),
            tool_calls=list(getattr(response, "tool_calls", []) or []),
            metadata=dict(getattr(response, "metadata", {}) or {}),
        )
        save_error = _append_session_messages(
            self.sessions, session_id, [user_msg, assistant_msg], self.workspace
        )
        return _with_save_error(assistant_msg.content, save_error)


def _format_llm_error(exc: Exception, workspace: Path) -> str:
    config_path = workspace / "config" / "llm_endpoints.json"
    message = str(exc)
    if isinstance(exc, LLMConfigurationError):
        if "Set api_key in llm_endpoints.json." in message:
            return (
                f"{console.error('LLM configuration is incomplete: missing API key.')}\n"
                "Choose one:\n"
                f"  {console.warning('Direct local value:')} set {console.command('api_key')} in {console.path(config_path)}\n"
                f"  {console.warning('Env placeholder:')} set {console.command('api_key')} to {console.command('${YOUR_ENV_NAME}')}"
                f" in {console.path(config_path)}, then define {console.command('YOUR_ENV_NAME')} in "
                f"{console.path('config/.env')} or the current PowerShell session."
            )
        missing_env = _extract_missing_env_name(message)
        if missing_env:
            return (
                f"{console.error('LLM configuration is incomplete: missing environment variable.')}\n"
                f"Referenced variable: {console.command(missing_env)}\n"
                f"Referenced by: {console.path(config_path)} field {console.command('api_key')}\n"
                f"Set {console.command(missing_env + '=...')} in {console.path('config/.env')} "
                "or the current PowerShell session, or replace "
                f"{console.command('api_key')} with a direct value."
            )
        return f"{console.error('LLM configuration is invalid:')} {_safe_error_message(message)}"
    if isinstance(exc, LLMProviderError):
        return (
            f"{console.error('LLM provider request failed:')} {_safe_error_message(message)}\n"
            f"Check endpoint config: {console.path(config_path)}\n"
            "Check base_url, model, network access, or api_key."
        )
    return (
        f"{console.error(ASSISTANT_ERROR_TEXT)}\n"
        f"Check endpoint config: {console.path(config_path)}\n"
        f"Error type: {type(exc).__name__}"
    )


def _safe_error_message(message: str) -> str:
    return message[:500] if message else "unknown provider error"


def _extract_missing_env_name(message: str) -> str | None:
    marker = "references missing environment variable "
    if marker not in message:
        return None
    tail = message.split(marker, 1)[1]
    if not tail.startswith("'"):
        return None
    return tail.split("'", 2)[1]


def _append_session_messages(
    sessions: SessionStore,
    session_id: str,
    messages: list[Message],
    workspace: Path,
) -> str | None:
    try:
        sessions.append(session_id, messages)
    except OSError as exc:
        sessions_dir = workspace / "contexts" / "sessions"
        return (
            f"Cannot save session history: {exc}\n"
            f"Check that this directory is writable: {sessions_dir}"
        )
    return None


def _with_save_error(text: str, save_error: str | None) -> str:
    if not save_error:
        return text
    return f"{text}\n\n{save_error}"
