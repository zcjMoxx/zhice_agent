"""Runtime dependency assembly for the local Web app."""

from __future__ import annotations

import inspect
import logging
import os
from dataclasses import asdict, dataclass
from threading import Lock
from typing import Any, Callable

from agent.app.auth import AuthService, local_operator_actor
from agent.auth.activity import SqliteRuntimeActivitySink
from agent.auth.audit import SqliteAuditSink
from agent.auth.confirmation import SQLiteToolConfirmationBroker
from agent.auth.diagnostics import RecentActivityDiagnostics
from agent.auth.session_access import SessionAccessService
from agent.auth.store import SQLiteAuthStore
from agent.auth.tool_policy import RbacToolExecutionPolicy
from agent.auth.user_context import FilesystemUserContextResolver
from agent.config import AppConfig
from agent.core.context import DEFAULT_CONTEXT_PROMPTS, ContextBuilder
from agent.core.loop import AgentLoop, CancellationToken
from agent.core.turns import new_turn_id
from agent.llm.runtime import create_configured_llm_provider
from agent.llm.selection import ConfiguredLLMProviderResolver
from agent.logging_utils import log_event
from agent.mcp import McpRuntime, load_mcp_server_specs
from agent.memory import MemoryStoreError
from agent.memory.context import build_memory_context
from agent.memory.extraction import MemoryExtractionService, pop_memory_notification
from agent.memory.markdown_store import MarkdownMemoryStore
from agent.memory.presentation import format_memory_list
from agent.memory.safety import MemorySafetyPolicy
from agent.memory.scheduler import MemoryExtractionJob, MemoryExtractionScheduler
from agent.prompt_loader import PromptLoader
from agent.protocols.auth import ActorContext, AuditEvent
from agent.protocols.diagnostics import DiagnosticContext
from agent.protocols.llm import LLMEndpoint, LLMProvider
from agent.protocols.mcp import McpInteractionResponse
from agent.protocols.session import (
    SessionModelPreference,
    SessionState,
    SessionStore,
    SessionSummary,
)
from agent.session import JsonlSessionStore, JsonSessionModelPreferenceStore
from agent.skills import SkillLoader, SkillSourceSync
from agent.skills.sync import SkillSyncError
from agent.tools import UserScopedToolProvider, create_default_tool_registry

DEFAULT_WEB_HISTORY_MESSAGES = 60
RuntimeEventCallback = Callable[[dict[str, Any]], None]
web_logger = logging.getLogger("zcagent.agent.web")
session_logger = logging.getLogger("zcagent.agent.session")
memory_logger = logging.getLogger("zcagent.agent.memory")
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
    auth: AuthService | None = None
    session_access: SessionAccessService | None = None
    model_preferences: JsonSessionModelPreferenceStore | None = None
    llm_resolver: ConfiguredLLMProviderResolver | None = None
    tool_policy: RbacToolExecutionPolicy | None = None
    confirmation_broker: SQLiteToolConfirmationBroker | None = None
    activity_sink: SqliteRuntimeActivitySink | None = None
    audit_sink: SqliteAuditSink | None = None
    diagnostics: RecentActivityDiagnostics | None = None
    skill_loader: SkillLoader | None = None
    skill_sync: SkillSourceSync | None = None
    prompt_loader: PromptLoader | None = None
    memory_extraction_enabled: bool = False
    memory_idle_seconds: float = 300.0
    memory_extraction_max_workers: int = 2
    memory_extraction_max_pending_jobs: int = 1000
    memory_scheduler: MemoryExtractionScheduler | None = None
    mcp_runtime: McpRuntime | None = None

    def __post_init__(self) -> None:
        self._active_turns: dict[tuple[str, str], ActiveTurn] = {}
        self._turns_lock = Lock()

    def list_sessions(
        self,
        actor: ActorContext | None = None,
        *,
        request_id: str = "",
    ) -> list[SessionSummary]:
        """Return known sessions for the Web sidebar."""

        if self.session_access is not None and actor is not None and actor.user_id is not None:
            return self.session_access.list_sessions(actor)
        return self.sessions.list_sessions()

    def load_session(
        self,
        actor: ActorContext | str,
        session_id: str | None = None,
        *,
        request_id: str = "",
    ) -> SessionState:
        """Load one session for Web history rendering."""

        actor, session_id = _normalize_actor_session(actor, session_id)
        if self.session_access is not None and actor.user_id is not None:
            return self.session_access.load_session(actor, session_id)
        return self.sessions.load(session_id)

    def create_session(self, actor: ActorContext, session_id: str, channel: str = "web") -> None:
        """Claim a new globally unique session id for one authenticated actor."""

        if self.session_access is not None and actor.user_id is not None:
            resolved = self.session_access.ensure_session(
                actor, session_id, channel=channel, write=True
            )
            if resolved.created:
                log_event(
                    session_logger,
                    logging.INFO,
                    "session.created",
                    actor_user_id=actor.user_id,
                    session_id=session_id,
                )

    def run_chat_events(
        self,
        actor: ActorContext | str,
        session_id: str | None = None,
        message: str | None = None,
        *,
        turn_id: str | None = None,
        on_event: RuntimeEventCallback | None = None,
        command_profile: str = WEB_COMMAND_PROFILE,
        request_id: str = "",
    ) -> ChatTurnResult:
        """Run one command-aware turn and emit text_delta events as they arrive."""

        actor, session_id, message = _normalize_actor_session_message(actor, session_id, message)
        command_text = self.handle_command(
            actor,
            session_id,
            message,
            command_profile=command_profile,
            request_id=request_id,
        )
        if command_text is not None:
            _emit_runtime_event(on_event, {"type": "text_delta", "content": command_text})
            command_name = message.strip().split(maxsplit=1)[0].lower()
            stopped = command_profile == EXTERNAL_COMMAND_PROFILE and command_name == "/stop"
            return ChatTurnResult(content=command_text, stopped=stopped, turn_id=turn_id or "")

        turn_id = turn_id or new_turn_id()
        token = CancellationToken()
        active_key = _active_turn_key(actor, session_id)
        self._cancel_memory_extraction(actor, session_id)
        self._register_turn(active_key, ActiveTurn(turn_id=turn_id, token=token))
        sessions = self.sessions
        workspace = self.config.workspace
        tools = getattr(self.agent_loop, "tools", None)
        turn_llm = self.llm
        memory_notice: tuple[str, ...] = ()
        if self.session_access is not None and actor.user_id is not None:
            resolved = self.session_access.ensure_session(
                actor,
                session_id,
                channel=("external_ws" if command_profile == EXTERNAL_COMMAND_PROFILE else "web"),
                write=True,
            )
            if resolved.created:
                log_event(
                    session_logger,
                    logging.INFO,
                    "session.created",
                    actor_user_id=actor.user_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    request_id=request_id,
                )
            sessions = resolved.store
            owner_has_workspace_scope = "owner" in actor.role_keys
            workspace = self.config.workspace if owner_has_workspace_scope else resolved.context.files_dir
            memory_context = build_memory_context(
                resolved.context.memory_dir,
                scope="workspace" if owner_has_workspace_scope else "user",
                actor_user_id=None if owner_has_workspace_scope else actor.user_id,
            )
            memory_store = MarkdownMemoryStore(memory_context)
            memory_safety = MemorySafetyPolicy()
            memory_notice = pop_memory_notification(memory_context)
            if self.model_preferences is not None and self.llm_resolver is not None:
                preference = self.model_preferences.get(resolved.model_context(), session_id)
                selection = self.llm_resolver.resolve(preference)
                turn_llm = self.llm_resolver.bind(selection)
            tools = UserScopedToolProvider(
                files_dir=workspace,
                shared_readonly_dir=resolved.context.shared_readonly_dir,
                actor=actor,
                skills=self.skill_loader,
                skill_sync=self.skill_sync,
                diagnostics=self.diagnostics,
                diagnostic_context=DiagnosticContext(
                    session_id=session_id,
                    current_turn_id=turn_id,
                    current_request_id=request_id,
                    channel=actor.channel,
                ),
                memory_store=memory_store,
                memory_safety=memory_safety,
                extra_tools=(
                    self.mcp_runtime.tools_for_actor(
                        actor,
                        workspace,
                        interaction_notifier=lambda request: _emit_runtime_event(
                            on_event,
                            {"type": "mcp_elicitation_requested", **asdict(request)},
                        ),
                    )
                    if self.mcp_runtime is not None
                    else None
                ),
            )
        try:
            notice_text = _format_memory_notice(memory_notice)
            if notice_text:
                _emit_runtime_event(on_event, {"type": "text_delta", "content": notice_text + "\n\n"})
            content = _run_agent_turn(
                self.agent_loop,
                session_id,
                message,
                turn_id=turn_id,
                on_event=on_event,
                cancellation_token=token,
                actor=actor,
                llm_override=turn_llm,
                sessions_override=sessions,
                tools_override=tools,
                workspace_override=workspace,
                tool_policy=self.tool_policy,
                confirmation_broker=self.confirmation_broker,
                activity_sink=self.activity_sink,
                audit_sink=self.audit_sink,
                channel=actor.channel,
                request_id=request_id,
            )
            if self.session_access is not None and actor.user_id is not None:
                self.session_access.refresh_index(actor, session_id)
            stopped = token.is_cancelled()
            if stopped:
                log_event(web_logger, logging.INFO, "chat.stopped", session_id=session_id, turn_id=turn_id)
            else:
                self._schedule_memory_extraction(actor, session_id)
            visible_content = f"{notice_text}\n\n{content}" if notice_text else content
            return ChatTurnResult(content=visible_content, stopped=stopped, turn_id=turn_id)
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
            self._unregister_turn(active_key, turn_id)

    def handle_command(
        self,
        actor: ActorContext | str,
        session_id: str | None = None,
        message: str | None = None,
        *,
        command_profile: str = WEB_COMMAND_PROFILE,
        request_id: str = "",
    ) -> str | None:
        """Return a Web command response, or None for ordinary chat text."""

        actor, session_id, message = _normalize_actor_session_message(actor, session_id, message)
        stripped = message.strip()
        if not stripped.startswith("/"):
            return None

        command, _, target = stripped[1:].partition(" ")
        command = command.lower()
        target = target.strip()
        if command == "help":
            return _web_help_text(command_profile)
        if command == "model":
            return self._handle_model_command(actor, session_id, target, request_id=request_id)
        if command == "memory":
            return self._handle_memory_command(actor, session_id, target)
        if command == "mcp":
            if target:
                return "Usage: `/mcp`"
            return (
                self.mcp_runtime.format_capabilities()
                if self.mcp_runtime is not None
                else "当前没有可用的 MCP Server。"
            )
        if command == "stop":
            if command_profile != EXTERNAL_COMMAND_PROFILE:
                return _command_not_supported_for_client(stripped)
            result = self.cancel_session(actor, session_id)
            return f"Stopped current turn. Cancelled: `{result['cancelled']}`"
        if command == "reset":
            self._cancel_memory_extraction(actor, session_id)
            if self.session_access is not None and actor.user_id is not None:
                self.session_access.clear_session(actor, session_id)
            else:
                self.sessions.clear(session_id)
            return f"Session cleared: `{session_id}`"
        if command == "new":
            return "Use the New chat button to start a fresh Web session."
        if command == "sessions":
            return self._handle_sessions_command(actor, session_id, target)
        if command == "history":
            if command_profile != EXTERNAL_COMMAND_PROFILE:
                return _command_not_supported_for_client(stripped)
            return _format_session_history(self.load_session(actor, session_id).messages)
        if command == "exit":
            if command_profile != EXTERNAL_COMMAND_PROFILE:
                return _command_not_supported_for_client(stripped)
            return "Closing WebSocket connection."

        return _unsupported_command(stripped)

    def _handle_memory_command(
        self,
        actor: ActorContext,
        session_id: str,
        target: str,
    ) -> str:
        """Display the current actor's scoped durable Memory."""

        if target:
            return "Usage: `/memory`"
        target_session_id = session_id
        memory_dir = self.config.local_memory_dir
        scope = "workspace"
        actor_user_id = None
        if self.session_access is not None and actor.user_id is not None:
            resolved = self.session_access.resolve_session(actor, target_session_id)
            if resolved.owner_user_id != actor.user_id:
                return "Memory is limited to your own sessions."
            is_owner = "owner" in actor.role_keys
            memory_dir = resolved.context.memory_dir
            scope = "workspace" if is_owner else "user"
            actor_user_id = None if is_owner else actor.user_id
        memory_store = MarkdownMemoryStore(
            build_memory_context(
                memory_dir,
                scope=scope,
                actor_user_id=actor_user_id,
            )
        )
        return format_memory_list(memory_store)

    def _handle_sessions_command(
        self,
        actor: ActorContext,
        session_id: str,
        target: str,
    ) -> str:
        """Handle shared /sessions subcommands for Web and WS channels."""

        if not target:
            return _format_session_list(self.list_sessions(actor), session_id)

        subcommand, _, rest = target.partition(" ")
        subcommand = subcommand.strip().lower()
        rest = rest.strip()
        if subcommand == "rename":
            target_session_id, _, title = rest.partition(" ")
            target_session_id = target_session_id.strip()
            title = title.strip()
            if not target_session_id or not title:
                return _sessions_usage_text()
            summary = self.rename_session(actor, target_session_id, title)
            return f"Session renamed: `{summary.session_id}`"
        if subcommand == "delete":
            target_session_id = rest or session_id
            if target_session_id == session_id:
                self._cancel_memory_extraction(actor, session_id)
                if self.session_access is not None and actor.user_id is not None:
                    self.session_access.clear_session(actor, session_id)
                else:
                    self.sessions.clear(session_id)
                return f"Session cleared: `{session_id}`"
            self.delete_session(actor, target_session_id)
            return f"Session deleted: `{target_session_id}`"
        return _sessions_usage_text()

    def rename_session(
        self,
        actor: ActorContext | str,
        session_id: str | None = None,
        title: str | None = None,
        *,
        request_id: str = "",
    ) -> SessionSummary:
        """Rename a session title and return the updated summary."""

        actor, session_id, title = _normalize_actor_session_message(actor, session_id, title)
        if self.session_access is not None and actor.user_id is not None:
            summary = self.session_access.rename_session(actor, session_id, title)
        else:
            self.sessions.rename(session_id, title)
            summary = _find_session_summary(self.sessions.list_sessions(), session_id)
        log_event(session_logger, logging.INFO, "session.renamed", session_id=session_id)
        return summary

    def delete_session(
        self,
        actor: ActorContext | str,
        session_id: str | None = None,
        *,
        request_id: str = "",
    ) -> None:
        """Cancel then delete one Web session."""

        actor, session_id = _normalize_actor_session(actor, session_id)
        self._cancel_memory_extraction(actor, session_id)
        self.cancel_session(actor, session_id)
        if self.session_access is not None and actor.user_id is not None:
            self.session_access.delete_session(actor, session_id)
        else:
            self.sessions.delete(session_id)
        log_event(session_logger, logging.INFO, "session.deleted", session_id=session_id)
        self._audit(
            actor,
            "session.deleted",
            "session",
            session_id=session_id,
            request_id=request_id,
        )

    def cancel_session(
        self,
        actor: ActorContext | str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Cancel the active turn for a session when one exists."""

        actor, session_id = _normalize_actor_session(actor, session_id)
        key = _active_turn_key(actor, session_id)
        with self._turns_lock:
            active = self._active_turns.get(key)
            if active is None and actor.has_permission("chat.stop.any"):
                match = next(
                    (
                        (candidate_key, candidate)
                        for candidate_key, candidate in self._active_turns.items()
                        if candidate_key[1] == session_id
                    ),
                    None,
                )
                if match is not None:
                    key, active = match
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
            if self.llm_resolver is not None:
                selection = self.llm_resolver.resolve(None)
                return f"{selection.endpoint_name}/{selection.model_name}"
            model_state = self.model_state(local_operator_actor(channel="web"), "")
        except ValueError:
            return "auto"
        return f"{model_state.endpoint}/{model_state.current_model}"

    def model_state(
        self,
        actor: ActorContext | None = None,
        session_id: str = "",
        *,
        request_id: str = "",
    ) -> ModelState:
        """Return the current endpoint and selectable models for the Web UI."""

        actor = actor or local_operator_actor(channel="web")
        if self.session_access is not None and actor.user_id is not None:
            resolved = self.session_access.ensure_session(actor, session_id, channel="web")
            if resolved.created:
                log_event(
                    session_logger,
                    logging.INFO,
                    "session.created",
                    actor_user_id=actor.user_id,
                    session_id=session_id,
                )
            preference = self.model_preferences.get(resolved.model_context(), session_id) if self.model_preferences else None
            selection = self.llm_resolver.resolve(preference) if self.llm_resolver else None
            if selection is None:
                raise ValueError("LLM resolver is not configured")
            endpoint = _find_endpoint(self.llm_resolver.endpoints(), selection.endpoint_name)
            return ModelState(
                endpoint=selection.endpoint_name,
                current_model=selection.model_name,
                models=_endpoint_model_names(endpoint),
            )
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

    def set_model_preference(
        self,
        actor: ActorContext | str,
        session_id: str | None = None,
        model: str | None = None,
        *,
        request_id: str = "",
    ) -> ModelState:
        """Set the preferred model for the current endpoint and return new state."""

        if isinstance(actor, ActorContext):
            resolved_actor = actor
            resolved_session_id = str(session_id or "")
            selected_model = str(model or "").strip()
        else:
            resolved_actor = local_operator_actor(channel="web")
            resolved_session_id = ""
            selected_model = str(actor).strip()
        if not selected_model:
            raise ValueError("model is required")
        if self.session_access is not None and resolved_actor.user_id is not None:
            resolved = self.session_access.ensure_session(
                resolved_actor, resolved_session_id, channel="web", write=True
            )
            if resolved.created:
                log_event(
                    session_logger,
                    logging.INFO,
                    "session.created",
                    actor_user_id=resolved_actor.user_id,
                    session_id=resolved_session_id,
                    request_id=request_id,
                )
            state = self.model_state(
                resolved_actor,
                resolved_session_id,
                request_id=request_id,
            )
            selection = self.llm_resolver.select(state.endpoint, selected_model)
            if (
                selection.endpoint_name == state.endpoint
                and selection.model_name == state.current_model
            ):
                return state
            self.model_preferences.set(
                resolved.model_context(),
                resolved_session_id,
                SessionModelPreference(selection.endpoint_name, selection.model_name),
            )
            log_event(
                web_logger,
                logging.INFO,
                "model.switched",
                actor_user_id=resolved_actor.user_id,
                session_id=resolved_session_id,
                request_id=request_id,
                endpoint=selection.endpoint_name,
                model=selection.model_name,
            )
            return self.model_state(
                resolved_actor,
                resolved_session_id,
                request_id=request_id,
            )
        current_endpoint = _current_endpoint(self.llm)

        selected_endpoint, error = self.llm.match_endpoint(f"{current_endpoint.name}/{selected_model}")
        if selected_endpoint is None:
            raise ValueError(error or "model is not supported by the current endpoint")
        self.llm.set_preferred(selected_endpoint.name, selected_endpoint.model)
        return self.model_state()

    def reset_model_preference(
        self,
        actor: ActorContext,
        session_id: str,
        *,
        request_id: str = "",
    ) -> ModelState:
        """Clear only one session's model fields and restore the system default."""

        if self.session_access is None or self.model_preferences is None:
            self.llm.reset_preferred()
            return self.model_state()
        resolved = self.session_access.resolve_session(actor, session_id, write=True)
        existing = self.model_preferences.get(resolved.model_context(), session_id)
        self.model_preferences.reset(resolved.model_context(), session_id)
        if existing is not None:
            log_event(
                web_logger,
                logging.INFO,
                "model.reset",
                actor_user_id=actor.user_id or "",
                session_id=session_id,
                request_id=request_id,
            )
        return self.model_state(actor, session_id, request_id=request_id)

    def list_tool_confirmations(self, actor: ActorContext) -> list[dict[str, Any]]:
        if self.confirmation_broker is None:
            return []
        return self.confirmation_broker.list_for_actor(actor)

    def decide_tool_confirmation(
        self,
        actor: ActorContext,
        confirmation_id: str,
        approved: bool,
    ) -> str:
        if self.confirmation_broker is None:
            raise ValueError("confirmation broker is not configured")
        return self.confirmation_broker.decide(actor, confirmation_id, approved)

    def _handle_model_command(
        self,
        actor: ActorContext,
        session_id: str,
        target: str,
        *,
        request_id: str = "",
    ) -> str:
        """Handle session-scoped Web /model commands."""

        normalized = target.strip()
        if not normalized:
            state = self.model_state(actor, session_id, request_id=request_id)
            return f"Current model: `{state.endpoint}/{state.current_model}`"
        if normalized.lower() == "reset":
            state = self.reset_model_preference(actor, session_id, request_id=request_id)
            return f"Model preference reset.\n\nCurrent model: `{state.endpoint}/{state.current_model}`"
        if normalized.lower() == "list" or normalized.lower().startswith("list "):
            endpoint_name = normalized[4:].strip()
            if endpoint_name:
                return _format_endpoint_model_list(
                    self.llm_resolver or self.llm,
                    endpoint_name,
                )
            return _format_model_list(self.llm_resolver or self.llm)
        if self.session_access is None or self.model_preferences is None or self.llm_resolver is None:
            return _handle_model_command(self.llm, normalized)
        endpoint_name, separator, model_name = normalized.partition("/")
        selection = self.llm_resolver.select(endpoint_name, model_name if separator else None)
        resolved = self.session_access.ensure_session(
            actor, session_id, channel="web", write=True
        )
        if resolved.created:
            log_event(
                session_logger,
                logging.INFO,
                "session.created",
                actor_user_id=actor.user_id,
                session_id=session_id,
            )
        current = self.model_state(actor, session_id, request_id=request_id)
        if selection.endpoint_name == current.endpoint and selection.model_name == current.current_model:
            return f"Current model: `{selection.endpoint_name}/{selection.model_name}`"
        self.model_preferences.set(
            resolved.model_context(),
            session_id,
            SessionModelPreference(selection.endpoint_name, selection.model_name),
        )
        log_event(
            web_logger,
            logging.INFO,
            "model.switched",
            actor_user_id=actor.user_id or "",
            session_id=session_id,
            request_id=request_id,
            endpoint=selection.endpoint_name,
            model=selection.model_name,
        )
        return f"Model switched: `{selection.endpoint_name}/{selection.model_name}`"

    def _audit(
        self,
        actor: ActorContext,
        action: str,
        resource_type: str,
        *,
        session_id: str = "",
        turn_id: str = "",
        request_id: str = "",
        decision: str = "",
        reason_code: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.audit_sink is None:
            return
        self.audit_sink.record(
            AuditEvent(
                action=action,
                resource_type=resource_type,
                actor=actor,
                session_id=session_id,
                turn_id=turn_id,
                request_id=request_id,
                channel=actor.channel,
                decision=decision,
                reason_code=reason_code,
                metadata=dict(metadata or {}),
            )
        )

    def _register_turn(self, key: tuple[str, str], active: ActiveTurn) -> None:
        """Register the active turn, cancelling any older turn for the session."""

        with self._turns_lock:
            old_turn = self._active_turns.get(key)
            if old_turn is not None:
                old_turn.token.cancel()
            self._active_turns[key] = active

    def _unregister_turn(self, key: tuple[str, str], turn_id: str) -> None:
        """Remove an active turn only if it is still the current one."""

        with self._turns_lock:
            active = self._active_turns.get(key)
            if active is not None and active.turn_id == turn_id:
                self._active_turns.pop(key, None)

    def shutdown(self) -> None:
        """Cancel pending background work before the Gateway exits."""

        if self.memory_scheduler is not None:
            self.memory_scheduler.shutdown()
        if self.mcp_runtime is not None:
            self.mcp_runtime.close()

    def submit_mcp_interaction(
        self,
        interaction_id: str,
        action: str,
        content: dict[str, Any] | None = None,
    ) -> bool:
        """Forward one Web/WS Elicitation response to the shared MCP Runtime."""

        if self.mcp_runtime is None or action not in {"accept", "decline", "cancel"}:
            return False
        return self.mcp_runtime.submit_interaction(
            interaction_id,
            McpInteractionResponse(action=action, content=content),  # type: ignore[arg-type]
        )

    def _schedule_memory_extraction(self, actor: ActorContext, session_id: str) -> None:
        if not self.memory_extraction_enabled or self.prompt_loader is None:
            return
        scheduler = self._ensure_memory_scheduler()
        scheduler.schedule(
            _memory_extraction_actor_key(actor),
            actor,
            session_id,
        )

    def _cancel_memory_extraction(self, actor: ActorContext, session_id: str) -> None:
        if self.memory_scheduler is not None:
            self.memory_scheduler.cancel(_memory_extraction_actor_key(actor), session_id)

    def _run_memory_extraction(self, job: MemoryExtractionJob) -> None:
        if self.prompt_loader is None:
            return
        actor = job.actor
        session_id = job.session_id
        sessions = self.sessions
        memory_dir = self.config.local_memory_dir
        scope = "workspace"
        actor_user_id = None
        llm = self.llm
        if self.session_access is not None and actor.user_id is not None:
            resolved = self.session_access.resolve_session(actor, session_id)
            sessions = resolved.store
            is_owner = "owner" in actor.role_keys
            memory_dir = resolved.context.memory_dir
            scope = "workspace" if is_owner else "user"
            actor_user_id = None if is_owner else actor.user_id
            if self.model_preferences is not None and self.llm_resolver is not None:
                preference = self.model_preferences.get(resolved.model_context(), session_id)
                llm = self.llm_resolver.bind(self.llm_resolver.resolve(preference))
        if job.cancelled.is_set():
            return
        context = build_memory_context(
            memory_dir,
            scope=scope,
            actor_user_id=actor_user_id,
        )
        result = MemoryExtractionService(
            context,
            MarkdownMemoryStore(context),
            self.prompt_loader,
            MemorySafetyPolicy(),
        ).extract(
            session_id,
            sessions.load(session_id).messages,
            llm,
            should_commit=lambda: not job.cancelled.is_set(),
        )
        if job.cancelled.is_set():
            return
        log_event(
            memory_logger,
            logging.INFO if result.added else logging.DEBUG,
            "memory.extraction.done",
            actor_user_id=actor.user_id or "",
            session_id=session_id,
            reviewed_through_turn_index=result.reviewed_through_turn_index,
            added_count=len(result.added),
        )

    def _ensure_memory_scheduler(self) -> MemoryExtractionScheduler:
        if self.memory_scheduler is None:
            self.memory_scheduler = MemoryExtractionScheduler(
                self._run_memory_extraction,
                idle_seconds=self.memory_idle_seconds,
                max_workers=self.memory_extraction_max_workers,
                max_pending_jobs=self.memory_extraction_max_pending_jobs,
                retryable=_is_retryable_memory_extraction_error,
            )
        return self.memory_scheduler


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
    default_endpoint = _current_endpoint(llm).name
    llm_resolver = ConfiguredLLMProviderResolver(
        list(llm.endpoints()),
        default_endpoint=default_endpoint,
    )
    auth_store = SQLiteAuthStore(config.auth_db_path)
    auth_store.initialize_schema()
    audit_sink = SqliteAuditSink(auth_store)
    activity_sink = SqliteRuntimeActivitySink(auth_store)
    auth = AuthService(
        auth_store,
        audit_sink=audit_sink,
        setup_token=os.getenv("ZHICE_AGENT_SETUP_TOKEN", ""),
    )
    user_contexts = FilesystemUserContextResolver(
        config.contexts_dir,
        workspace_dir=config.workspace,
    )
    session_access = SessionAccessService(auth_store, user_contexts)
    model_preferences = JsonSessionModelPreferenceStore()
    confirmation_broker = SQLiteToolConfirmationBroker(auth_store)
    diagnostics = RecentActivityDiagnostics(auth_store, config.logs_dir)
    tool_policy = RbacToolExecutionPolicy()
    mcp_runtime = McpRuntime(
        load_mcp_server_specs(config.config_dir),
        workspace=config.workspace,
        activity_sink=activity_sink,
        audit_sink=audit_sink,
    )
    operator = local_operator_actor(channel="web")
    tool_registry = create_default_tool_registry(
        config.workspace,
        skills=skill_loader,
        skill_sync=skill_sync,
        extra_tools=mcp_runtime.tools_for_actor(operator, config.workspace),
    )
    agent_loop = AgentLoop(
        llm=llm,
        sessions=session_store,
        context_builder=context_builder,
        workspace=config.workspace,
        tools=tool_registry,
        tool_policy=tool_policy,
        confirmation_broker=confirmation_broker,
        activity_sink=activity_sink,
        audit_sink=audit_sink,
    )
    return WebRuntime(
        config=config,
        sessions=session_store,
        agent_loop=agent_loop,
        llm=llm,
        auth=auth,
        session_access=session_access,
        model_preferences=model_preferences,
        llm_resolver=llm_resolver,
        tool_policy=tool_policy,
        confirmation_broker=confirmation_broker,
        activity_sink=activity_sink,
        audit_sink=audit_sink,
        diagnostics=diagnostics,
        skill_loader=skill_loader,
        skill_sync=skill_sync,
        prompt_loader=prompt_loader,
        memory_extraction_enabled=True,
        mcp_runtime=mcp_runtime,
    )


def _sync_startup_skills(skill_sync: SkillSourceSync) -> None:
    """Best-effort startup Skill sync for the Web process."""

    try:
        skill_sync.sync_on_startup()
    except SkillSyncError:
        return


def _run_agent_turn(agent_loop, session_id: str, message: str, **kwargs) -> str:
    """Pass only kwargs supported by the concrete or fake AgentLoop."""

    method = agent_loop.run_turn
    parameters = inspect.signature(method).parameters
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )
    filtered = kwargs if accepts_kwargs else {key: value for key, value in kwargs.items() if key in parameters}
    return method(session_id, message, **filtered)


def _normalize_actor_session(
    actor: ActorContext | str,
    session_id: str | None,
) -> tuple[ActorContext, str]:
    """Normalize new actor-aware and legacy direct runtime call forms."""

    if isinstance(actor, ActorContext):
        resolved_session_id = str(session_id or "").strip()
        if not resolved_session_id:
            raise ValueError("session_id is required")
        return actor, resolved_session_id
    resolved_session_id = str(actor).strip()
    if not resolved_session_id:
        raise ValueError("session_id is required")
    return local_operator_actor(channel="web"), resolved_session_id


def _normalize_actor_session_message(
    actor: ActorContext | str,
    session_id: str | None,
    message: str | None,
) -> tuple[ActorContext, str, str]:
    """Normalize actor-aware calls and the former (session_id, message) form."""

    if isinstance(actor, ActorContext):
        resolved_actor, resolved_session_id = _normalize_actor_session(actor, session_id)
        resolved_message = str(message or "")
    else:
        resolved_actor = local_operator_actor(channel="web")
        resolved_session_id = str(actor).strip()
        resolved_message = str(session_id if message is None else message)
    if not resolved_session_id:
        raise ValueError("session_id is required")
    return resolved_actor, resolved_session_id, resolved_message


def _active_turn_key(actor: ActorContext, session_id: str) -> tuple[str, str]:
    """Keep active turns isolated by stable actor identity and session id."""

    actor_key = actor.user_id or f"{actor.actor_type}:{actor.username}"
    return actor_key, session_id


def _memory_extraction_actor_key(actor: ActorContext) -> str:
    """Serialize Owner/CLI workspace Memory separately from ordinary users."""

    if "owner" in actor.role_keys or actor.actor_type == "local_operator":
        return "workspace-operator"
    return actor.user_id or f"{actor.actor_type}:{actor.username}"


def _is_retryable_memory_extraction_error(exc: BaseException) -> bool:
    return isinstance(exc, MemoryStoreError) and exc.code == "MEMORY_EXTRACTION_PROVIDER_FAILED"


def _find_endpoint(endpoints: list[LLMEndpoint], endpoint_name: str) -> LLMEndpoint:
    for endpoint in endpoints:
        if endpoint.name == endpoint_name:
            return endpoint
    raise ValueError(f"Unknown endpoint: {endpoint_name}")


def _emit_runtime_event(on_event: RuntimeEventCallback | None, event: dict[str, Any]) -> None:
    """Emit a runtime event when a caller provided a callback."""

    if on_event is not None:
        on_event(event)


def _format_memory_notice(contents: tuple[str, ...]) -> str:
    if not contents:
        return ""
    if len(contents) == 1:
        return f"💾 根据上次对话，我记住了：{contents[0]}"
    rendered = "；".join(contents[:3])
    return f"💾 根据上次对话，我记住了：{rendered}"


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
        "- `/memory` - show current Memory",
        "- `/mcp` - show available MCP capabilities",
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

    if isinstance(llm, ConfiguredLLMProviderResolver):
        selection = llm.resolve(None)
        current_name = selection.endpoint_name
        current_model = selection.model_name
    else:
        current = llm.current_endpoint()
        current_name = current.name
        current_model = current.model
    lines = [f"Current model: `{current_name}/{current_model}`", "", "Available endpoints:"]
    for endpoint in llm.endpoints():
        marker = "*" if endpoint.name == current_name else "-"
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
