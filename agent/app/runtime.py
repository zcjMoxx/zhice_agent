"""Runtime dependency assembly for the local Web app."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable

from agent.config import AppConfig
from agent.core.context import DEFAULT_CONTEXT_PROMPTS, ContextBuilder
from agent.core.loop import AgentLoop, CancellationToken
from agent.core.turns import new_turn_id
from agent.llm.runtime import create_configured_llm_provider
from agent.logging_utils import log_event
from agent.prompt_loader import PromptLoader
from agent.protocols.llm import LLMEndpoint, LLMProvider
from agent.protocols.session import SessionState, SessionStore, SessionSummary
from agent.session import JsonlSessionStore
from agent.skills import SkillLoader, SkillSourceSync
from agent.skills.sync import SkillSyncError
from agent.tools import create_default_tool_registry

DEFAULT_WEB_HISTORY_MESSAGES = 12
RuntimeEventCallback = Callable[[dict[str, Any]], None]
web_logger = logging.getLogger("zcagent.agent.web")
session_logger = logging.getLogger("zcagent.agent.session")
WEB_COMMAND_PROFILE = "web"
EXTERNAL_COMMAND_PROFILE = "external"


@dataclass
class ModelState:
    """Current endpoint model state exposed to the Web UI."""

    endpoint: str
    current_model: str
    models: list[str]


@dataclass
class ChatTurnResult:
    """Final result of one Web chat turn."""

    content: str
    stopped: bool = False
    turn_id: str = ""


@dataclass
class ActiveTurn:
    """Cancellation state for one active session turn."""

    turn_id: str
    token: CancellationToken


@dataclass
class WebRuntime:
    """Dependencies needed by HTTP routes without exposing construction details."""

    config: AppConfig
    sessions: SessionStore
    agent_loop: AgentLoop
    llm: LLMProvider

    def __post_init__(self) -> None:
        self._active_turns: dict[str, ActiveTurn] = {}
        self._turns_lock = Lock()

    def list_sessions(self) -> list[SessionSummary]:
        """Return known sessions for the Web sidebar."""

        return self.sessions.list_sessions()

    def load_session(self, session_id: str) -> SessionState:
        """Load one session for Web history rendering."""

        return self.sessions.load(session_id)

    def run_chat_events(
        self,
        session_id: str,
        message: str,
        *,
        turn_id: str | None = None,
        on_event: RuntimeEventCallback | None = None,
        command_profile: str = WEB_COMMAND_PROFILE,
    ) -> ChatTurnResult:
        """Run one command-aware turn and emit text_delta events as they arrive."""

        command_text = self.handle_command(session_id, message, command_profile=command_profile)
        if command_text is not None:
            _emit_runtime_event(on_event, {"type": "text_delta", "content": command_text})
            command_name = message.strip().split(maxsplit=1)[0].lower()
            stopped = command_profile == EXTERNAL_COMMAND_PROFILE and command_name == "/stop"
            return ChatTurnResult(content=command_text, stopped=stopped, turn_id=turn_id or "")

        turn_id = turn_id or new_turn_id()
        token = CancellationToken()
        self._register_turn(session_id, ActiveTurn(turn_id=turn_id, token=token))
        log_event(web_logger, logging.DEBUG, "chat.accepted", session_id=session_id, turn_id=turn_id)
        try:
            content = self.agent_loop.run_turn(
                session_id,
                message,
                turn_id=turn_id,
                on_event=on_event,
                cancellation_token=token,
            )
            stopped = token.is_cancelled()
            if stopped:
                log_event(web_logger, logging.INFO, "chat.stopped", session_id=session_id, turn_id=turn_id)
            else:
                log_event(web_logger, logging.DEBUG, "chat.done", session_id=session_id, turn_id=turn_id)
            return ChatTurnResult(content=content, stopped=stopped, turn_id=turn_id)
        except Exception as exc:
            log_event(
                web_logger,
                logging.ERROR,
                "chat.error",
                session_id=session_id,
                turn_id=turn_id,
                error_type=type(exc).__name__,
            )
            raise
        finally:
            self._unregister_turn(session_id, turn_id)

    def handle_command(
        self,
        session_id: str,
        message: str,
        *,
        command_profile: str = WEB_COMMAND_PROFILE,
    ) -> str | None:
        """Return a Web command response, or None for ordinary chat text."""

        stripped = message.strip()
        if not stripped.startswith("/"):
            return None

        command, _, target = stripped[1:].partition(" ")
        command = command.lower()
        target = target.strip()
        if command == "help":
            return _web_help_text(command_profile)
        if command == "model":
            return _handle_model_command(self.llm, target)
        if command == "stop":
            if command_profile != EXTERNAL_COMMAND_PROFILE:
                return _command_not_supported_for_client(stripped)
            result = self.cancel_session(session_id)
            return f"Stopped current turn. Cancelled: `{result['cancelled']}`"
        if command == "reset":
            self.sessions.clear(session_id)
            return f"Session cleared: `{session_id}`"
        if command == "new":
            return "Use the New chat button to start a fresh Web session."
        if command == "sessions":
            return self._handle_sessions_command(session_id, target)
        if command == "history":
            if command_profile != EXTERNAL_COMMAND_PROFILE:
                return _command_not_supported_for_client(stripped)
            return _format_session_history(self.sessions.load(session_id).messages)
        if command == "exit":
            if command_profile != EXTERNAL_COMMAND_PROFILE:
                return _command_not_supported_for_client(stripped)
            return "Closing WebSocket connection."

        return _unsupported_command(stripped)

    def _handle_sessions_command(self, session_id: str, target: str) -> str:
        """Handle shared /sessions subcommands for Web and WS channels."""

        if not target:
            return _format_session_list(self.sessions.list_sessions(), session_id)

        subcommand, _, rest = target.partition(" ")
        subcommand = subcommand.strip().lower()
        rest = rest.strip()
        if subcommand == "rename":
            target_session_id, _, title = rest.partition(" ")
            target_session_id = target_session_id.strip()
            title = title.strip()
            if not target_session_id or not title:
                return _sessions_usage_text()
            summary = self.rename_session(target_session_id, title)
            return f"Session renamed: `{summary.session_id}`"
        if subcommand == "delete":
            target_session_id = rest or session_id
            if target_session_id == session_id:
                self.sessions.clear(session_id)
                return f"Session cleared: `{session_id}`"
            self.delete_session(target_session_id)
            return f"Session deleted: `{target_session_id}`"
        return _sessions_usage_text()

    def rename_session(self, session_id: str, title: str) -> SessionSummary:
        """Rename a session title and return the updated summary."""

        self.sessions.rename(session_id, title)
        log_event(session_logger, logging.INFO, "session.renamed", session_id=session_id)
        return _find_session_summary(self.sessions.list_sessions(), session_id)

    def delete_session(self, session_id: str) -> None:
        """Cancel then delete one Web session."""

        self.cancel_session(session_id)
        self.sessions.delete(session_id)
        log_event(session_logger, logging.INFO, "session.deleted", session_id=session_id)

    def cancel_session(self, session_id: str) -> dict[str, Any]:
        """Cancel the active turn for a session when one exists."""

        with self._turns_lock:
            active = self._active_turns.get(session_id)
        if active is None:
            return {"session_id": session_id, "cancelled": 0}
        active.token.cancel()
        log_event(
            web_logger,
            logging.INFO,
            "chat.cancel_requested",
            session_id=session_id,
            turn_id=active.turn_id,
        )
        return {"session_id": session_id, "turn_id": active.turn_id, "cancelled": 1}

    def current_model_label(self) -> str:
        """Return a compact endpoint/model label when the provider exposes it."""

        try:
            model_state = self.model_state()
        except ValueError:
            return "auto"
        return f"{model_state.endpoint}/{model_state.current_model}"

    def model_state(self) -> ModelState:
        """Return the current endpoint and selectable models for the Web UI."""

        current_endpoint = _current_endpoint(self.llm)
        configured_endpoint = _configured_endpoint(self.llm, current_endpoint.name) or current_endpoint
        models = _endpoint_model_names(configured_endpoint)
        if current_endpoint.model not in models:
            models.insert(0, current_endpoint.model)
        return ModelState(
            endpoint=current_endpoint.name,
            current_model=current_endpoint.model,
            models=models,
        )

    def set_model_preference(self, model: str) -> ModelState:
        """Set the preferred model for the current endpoint and return new state."""

        selected_model = model.strip()
        if not selected_model:
            raise ValueError("model is required")
        current_endpoint = _current_endpoint(self.llm)

        selected_endpoint, error = self.llm.match_endpoint(f"{current_endpoint.name}/{selected_model}")
        if selected_endpoint is None:
            raise ValueError(error or "model is not supported by the current endpoint")
        self.llm.set_preferred(selected_endpoint.name, selected_endpoint.model)
        return self.model_state()

    def _register_turn(self, session_id: str, active: ActiveTurn) -> None:
        """Register the active turn, cancelling any older turn for the session."""

        with self._turns_lock:
            old_turn = self._active_turns.get(session_id)
            if old_turn is not None:
                old_turn.token.cancel()
            self._active_turns[session_id] = active

    def _unregister_turn(self, session_id: str, turn_id: str) -> None:
        """Remove an active turn only if it is still the current one."""

        with self._turns_lock:
            active = self._active_turns.get(session_id)
            if active is not None and active.turn_id == turn_id:
                self._active_turns.pop(session_id, None)


def build_web_runtime(
    config: AppConfig,
    *,
    endpoint_name: str = "auto",
) -> WebRuntime:
    """Build the runtime graph used by the local FastAPI gateway."""

    config.ensure_dirs()
    skill_sync = SkillSourceSync(
        workspace=config.workspace,
        config_dir=config.config_dir,
        extends_dir=config.extends_dir,
    )
    _sync_startup_skills(skill_sync)

    prompt_loader = PromptLoader(config.prompts_dir)
    prompt_loader.load_many(DEFAULT_CONTEXT_PROMPTS)
    session_store = JsonlSessionStore(config.sessions_dir)
    skill_loader = _create_skill_loader(skill_sync)
    context_builder = ContextBuilder(
        prompt_loader,
        skills=skill_loader,
        max_history_messages=DEFAULT_WEB_HISTORY_MESSAGES,
    )
    llm = create_configured_llm_provider(config.config_dir, endpoint_name)
    tool_registry = create_default_tool_registry(
        config.workspace,
        skills=skill_loader,
        skill_sync=skill_sync,
    )
    agent_loop = AgentLoop(
        llm=llm,
        sessions=session_store,
        context_builder=context_builder,
        workspace=config.workspace,
        tools=tool_registry,
    )
    return WebRuntime(
        config=config,
        sessions=session_store,
        agent_loop=agent_loop,
        llm=llm,
    )


def _sync_startup_skills(skill_sync: SkillSourceSync) -> None:
    """Best-effort startup Skill sync for the Web process."""

    try:
        skill_sync.sync_on_startup()
    except SkillSyncError:
        return


def _emit_runtime_event(on_event: RuntimeEventCallback | None, event: dict[str, Any]) -> None:
    """Emit a runtime event when a caller provided a callback."""

    if on_event is not None:
        on_event(event)


def _find_session_summary(summaries: list[SessionSummary], session_id: str) -> SessionSummary:
    """Return the summary for a session after a metadata mutation."""

    for summary in summaries:
        if summary.session_id == session_id:
            return summary
    return SessionSummary(
        session_id=session_id,
        preview="(empty)",
        updated_at=0.0,
        message_count=0,
    )


def _create_skill_loader(skill_sync: SkillSourceSync) -> SkillLoader:
    """Create a SkillLoader without letting optional Skill config block Web chat."""

    try:
        return SkillLoader(skill_sync.skill_roots())
    except SkillSyncError:
        return SkillLoader([])


def _current_endpoint(llm: LLMProvider) -> LLMEndpoint:
    """Read the current endpoint from the configured Web provider."""

    endpoint = llm.current_endpoint()
    if not isinstance(endpoint, LLMEndpoint):
        raise ValueError("current_endpoint must return an LLMEndpoint")
    return endpoint


def _configured_endpoint(llm: LLMProvider, endpoint_name: str) -> LLMEndpoint | None:
    """Return the configured endpoint before any temporary model override."""

    for endpoint in llm.endpoints():
        if isinstance(endpoint, LLMEndpoint) and endpoint.name == endpoint_name:
            return endpoint
    return None


def _endpoint_model_names(endpoint: LLMEndpoint) -> list[str]:
    """Return endpoint default model first, followed by unique supported models."""

    models = [endpoint.model]
    for model in endpoint.supported_models:
        if model not in models:
            models.append(model)
    return models


def _web_help_text(command_profile: str = WEB_COMMAND_PROFILE) -> str:
    """Return compact Web slash-command help."""

    lines = [
        "Available Web commands:",
        "",
        "- `/help` - show commands",
        "- `/model` - show or switch the preferred model",
        "- `/reset` - clear this session history",
        "- `/sessions` - list or manage recent sessions",
    ]
    if command_profile == EXTERNAL_COMMAND_PROFILE:
        lines.extend(
            [
                "- `/stop` - stop the active turn",
                "- `/history` - show recent messages",
                "- `/exit` - close this WebSocket connection",
            ]
        )
    return "\n".join(lines)


def _command_not_supported_for_client(command_text: str) -> str:
    """Return the shared client capability rejection text."""

    return f"Command not supported for this client: `{command_text}`.\n\nUse `/help` to see available commands."


def _unsupported_command(command_text: str) -> str:
    """Return the shared unknown command text."""

    return f"Unsupported command: `{command_text}`.\n\nUse `/help` to see available commands."


def _sessions_usage_text() -> str:
    """Return compact /sessions subcommand help."""

    return "\n".join(
        [
            "Available sessions commands:",
            "",
            "- `/sessions` - list recent sessions",
            "- `/sessions rename <id> <title>` - rename a session",
            "- `/sessions delete [id]` - clear the current session when id is omitted; delete the named session otherwise",
        ]
    )


def _sessions_tip_text() -> str:
    """Return the short /sessions management tip."""

    return (
        "Tip: use `/sessions rename <id> <title>` to rename, or "
        "`/sessions delete [id]` to delete; omitting id clears the current session like `/reset`."
    )


def _handle_model_command(llm: LLMProvider, target: str) -> str:
    """Handle Web /model commands without sending them to the LLM."""

    normalized = target.strip()
    if not normalized:
        return _format_model_status(llm)
    if normalized.lower() == "list" or normalized.lower().startswith("list "):
        endpoint_name = normalized[4:].strip()
        if endpoint_name:
            return _format_endpoint_model_list(llm, endpoint_name)
        return _format_model_list(llm)
    if normalized.lower() == "reset":
        llm.reset_preferred()
        return "Model preference reset.\n\n" + _format_model_status(llm)

    endpoint, error = llm.match_endpoint(normalized)
    if endpoint is None:
        return f"{error}\n\n{_format_model_status(llm)}"

    model_override = endpoint.model if "/" in normalized else None
    llm.set_preferred(endpoint.name, model_override)
    return f"Model switched: `{endpoint.name}/{endpoint.model}`"


def _format_model_status(llm: LLMProvider) -> str:
    """Format current endpoint/model state for Web command output."""

    current = llm.current_endpoint()
    return "\n".join(
        [
            f"Current model: `{current.name}/{current.model}`",
            "",
            "Tip: use `/model list` to see available endpoints and models.",
        ]
    )


def _format_model_list(llm: LLMProvider) -> str:
    """Format all endpoints for Web /model list."""

    current = llm.current_endpoint()
    lines = [f"Current model: `{current.name}/{current.model}`", "", "Available endpoints:"]
    for endpoint in llm.endpoints():
        marker = "*" if endpoint.name == current.name else "-"
        lines.append(f"{marker} `{endpoint.name}` default model: `{endpoint.model}`")
    lines.append("")
    lines.append("Use `/model list <endpoint>` to see available models for one endpoint.")
    return "\n".join(lines)


def _format_endpoint_model_list(llm: LLMProvider, endpoint_name: str) -> str:
    """Format supported models for one endpoint."""

    for endpoint in llm.endpoints():
        if endpoint.name != endpoint_name:
            continue
        models = _endpoint_model_names(endpoint)
        lines = [f"Endpoint: `{endpoint.name}`", "", "Available models:"]
        for model in models:
            suffix = " (default)" if model == endpoint.model else ""
            lines.append(f"- `{model}`{suffix}")
        return "\n".join(lines)
    return f"Unknown endpoint: `{endpoint_name}`\n\n{_format_model_list(llm)}"


def _format_session_list(summaries: list[SessionSummary], current_session_id: str) -> str:
    """Return a compact Markdown session list."""

    if not summaries:
        return f"No recent sessions.\n\n{_sessions_tip_text()}"
    lines = ["Recent sessions:"]
    for summary in summaries[:10]:
        marker = "*" if summary.session_id == current_session_id else "-"
        preview = summary.preview or "(empty)"
        lines.append(f"{marker} `{summary.session_id}` [{summary.message_count}] {preview}")
    lines.extend(["", _sessions_tip_text()])
    return "\n".join(lines)


def _format_session_history(messages) -> str:
    """Return recent user/assistant messages for Web command output."""

    visible = [message for message in messages if message.role in {"user", "assistant"}]
    if not visible:
        return "This session has no visible history."
    lines = ["Recent history:"]
    for message in visible[-10:]:
        content = " ".join(message.content.split())
        if len(content) > 120:
            content = content[:117] + "..."
        lines.append(f"- **{message.role}**: {content}")
    return "\n".join(lines)
