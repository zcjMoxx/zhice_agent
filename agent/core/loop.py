"""Agent loop with optional tool-calling support."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any

from agent.console import console
from agent.core.context import ContextBuilder
from agent.core.turns import assign_turn, new_turn_id, next_turn_index
from agent.logging_utils import log_event, preview_json, preview_text
from agent.message import Message
from agent.protocols.activity import RuntimeActivityEvent, RuntimeActivitySink
from agent.protocols.auth import ActorContext, AuditEvent, AuditSink
from agent.protocols.errors import ErrorCode
from agent.protocols.llm import (
    LLMConfigurationError,
    LLMProvider,
    LLMProviderError,
    LLMResponse,
    LLMStreamChunk,
)
from agent.protocols.session import SessionStore
from agent.protocols.tool import (
    ToolConfirmationBroker,
    ToolExecutionContext,
    ToolExecutionDecision,
    ToolExecutionPolicy,
    ToolProvider,
    ToolResult,
)

ASSISTANT_ERROR_TEXT = "LLM call failed. Check the workspace configuration and retry."
TOOL_ITERATION_LIMIT_TEXT = "Tool call limit reached. Please retry with a narrower request."
TURN_CANCELLED_TEXT = "[stopped]"
TurnEventCallback = Callable[[dict[str, Any]], None]
turn_logger = logging.getLogger("zcagent.agent.turn")
llm_logger = logging.getLogger("zcagent.agent.llm")
tool_logger = logging.getLogger("zcagent.agent.tool")
session_logger = logging.getLogger("zcagent.agent.session")


@dataclass
class ParsedToolCall:
    """Decoded tool call request from an LLM response."""

    id: str
    name: str
    arguments: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    error: ToolResult | None = None


class TurnCancelledError(RuntimeError):
    """Raised when a chat turn is cancelled by its caller."""


class CancellationToken:
    """Small thread-safe cancellation token shared by Web runtime and AgentLoop."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        """Mark the turn as cancelled."""

        self._event.set()

    def is_cancelled(self) -> bool:
        """Return whether cancellation was requested."""

        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """Raise TurnCancelledError when cancellation was requested."""

        if self.is_cancelled():
            raise TurnCancelledError(TURN_CANCELLED_TEXT)


class AgentLoop:
    """Run one chat turn, execute requested tools, and persist session messages."""

    def __init__(
        self,
        llm: LLMProvider,
        sessions: SessionStore,
        context_builder: ContextBuilder,
        workspace: Path,
        tools: ToolProvider | None = None,
        max_tool_iterations: int = 25,
        tool_policy: ToolExecutionPolicy | None = None,
        confirmation_broker: ToolConfirmationBroker | None = None,
        activity_sink: RuntimeActivitySink | None = None,
        audit_sink: AuditSink | None = None,
    ):
        """Wire provider dependencies and guard the tool-iteration limit."""

        if max_tool_iterations < 0:
            raise ValueError("max_tool_iterations must be non-negative")

        self.llm = llm
        self.sessions = sessions
        self.context_builder = context_builder
        self.workspace = Path(workspace).expanduser().resolve()
        self.tools = tools
        self.max_tool_iterations = max_tool_iterations
        self.tool_policy = tool_policy
        self.confirmation_broker = confirmation_broker
        self.activity_sink = activity_sink
        self.audit_sink = audit_sink

    def run_turn(
        self,
        session_id: str,
        user_text: str,
        *,
        turn_id: str | None = None,
        on_event: TurnEventCallback | None = None,
        cancellation_token: CancellationToken | None = None,
        actor: ActorContext | None = None,
        llm_override: LLMProvider | None = None,
        sessions_override: SessionStore | None = None,
        tools_override: ToolProvider | None = None,
        workspace_override: Path | None = None,
        tool_policy: ToolExecutionPolicy | None = None,
        confirmation_broker: ToolConfirmationBroker | None = None,
        activity_sink: RuntimeActivitySink | None = None,
        audit_sink: AuditSink | None = None,
        channel: str = "",
        request_id: str = "",
    ) -> str:
        """Run one user turn, saving user, assistant, and tool messages."""

        sessions = sessions_override or self.sessions
        tools = tools_override if tools_override is not None else self.tools
        llm = llm_override or self.llm
        workspace = Path(workspace_override or self.workspace).expanduser().resolve()
        execution_policy = tool_policy or self.tool_policy
        confirmations = confirmation_broker or self.confirmation_broker
        activity = activity_sink or self.activity_sink
        audit = audit_sink or self.audit_sink
        resolved_channel = channel or (actor.channel if actor is not None else "")
        session = sessions.load(session_id)
        resolved_turn_id = turn_id or new_turn_id()
        resolved_turn_index = next_turn_index(session.messages)
        turn_started = time.perf_counter()
        user_msg = assign_turn(
            Message(role="user", content=user_text),
            turn_id=resolved_turn_id,
            turn_index=resolved_turn_index,
        )
        messages = self.context_builder.build(
            history=session.messages,
            user_message=user_msg,
            workspace=workspace,
            session_id=session_id,
        )
        log_event(
            turn_logger,
            logging.INFO,
            "turn.start",
            session_id=session_id,
            turn_id=resolved_turn_id,
            turn_index=resolved_turn_index,
            input_preview=preview_text(user_text, limit=120),
            actor_user_id=actor.user_id if actor else "",
            actor_username=actor.username if actor else "",
            channel=resolved_channel,
        )
        _record_activity(
            activity,
            RuntimeActivityEvent(
                action="chat.turn_started",
                actor=actor,
                request_id=request_id,
                channel=resolved_channel,
                session_id=session_id,
                turn_id=resolved_turn_id,
                decision="allow",
                metadata={"turn_index": resolved_turn_index},
            ),
        )

        pending_session_messages = [user_msg]
        tool_definitions = tools.definitions() if tools else None
        tool_iterations = 0

        def persist_cancelled_turn() -> str:
            assistant_msg = Message(
                role="assistant",
                content=TURN_CANCELLED_TEXT,
                metadata={"stopped": True},
            )
            _assign_turn_fields(assistant_msg, resolved_turn_id, resolved_turn_index)
            pending_session_messages.append(assistant_msg)
            save_error = _append_session_messages(
                sessions,
                session_id,
                pending_session_messages,
                workspace,
            )
            _log_session_save(save_error, session_id, resolved_turn_id, len(pending_session_messages))
            log_event(
                turn_logger,
                logging.INFO,
                "turn.stopped",
                session_id=session_id,
                turn_id=resolved_turn_id,
                turn_index=resolved_turn_index,
            )
            _record_activity(
                activity,
                RuntimeActivityEvent(
                    action="chat.turn_stopped",
                    actor=actor,
                    request_id=request_id,
                    channel=resolved_channel,
                    session_id=session_id,
                    turn_id=resolved_turn_id,
                    decision="stopped",
                    metadata={"duration_ms": _duration_ms(turn_started)},
                ),
            )
            return _with_save_error(TURN_CANCELLED_TEXT, save_error)

        while True:
            llm_started = time.perf_counter()
            try:
                _raise_if_cancelled(cancellation_token)
                log_event(
                    llm_logger,
                    logging.DEBUG,
                    "llm.call",
                    session_id=session_id,
                    turn_id=resolved_turn_id,
                    messages=len(messages),
                    tools=len(tool_definitions or []),
                    actor_user_id=actor.user_id if actor else "",
                    request_id=request_id,
                    channel=resolved_channel,
                )
                response = _call_llm(
                    llm,
                    messages=list(messages),
                    tools=tool_definitions,
                    on_event=on_event,
                    cancellation_token=cancellation_token,
                )
                _raise_if_cancelled(cancellation_token)
                log_event(
                    llm_logger,
                    logging.DEBUG,
                    "llm.done",
                    session_id=session_id,
                    turn_id=resolved_turn_id,
                    duration_ms=_duration_ms(llm_started),
                    actor_user_id=actor.user_id if actor else "",
                    request_id=request_id,
                    channel=resolved_channel,
                    endpoint=(response.metadata or {}).get("endpoint_name", ""),
                    model=(response.metadata or {}).get("model", ""),
                )
            except TurnCancelledError:
                return persist_cancelled_turn()
            except Exception as exc:  # noqa: BLE001 - the loop must persist failed turns.
                log_event(
                    llm_logger,
                    logging.ERROR,
                    "llm.error",
                    session_id=session_id,
                    turn_id=resolved_turn_id,
                    error_type=type(exc).__name__,
                    duration_ms=_duration_ms(llm_started),
                    actor_user_id=actor.user_id if actor else "",
                    request_id=request_id,
                    channel=resolved_channel,
                )
                error_text = _format_llm_error(exc, workspace)
                assistant_msg = Message(
                    role="assistant",
                    content=error_text,
                    metadata={
                        "is_error": True,
                        "error_type": type(exc).__name__,
                    },
                )
                _assign_turn_fields(assistant_msg, resolved_turn_id, resolved_turn_index)
                pending_session_messages.append(assistant_msg)
                save_error = _append_session_messages(
                    sessions,
                    session_id,
                    pending_session_messages,
                    workspace,
                )
                _log_session_save(save_error, session_id, resolved_turn_id, len(pending_session_messages))
                log_event(
                    turn_logger,
                    logging.ERROR,
                    "turn.error",
                    session_id=session_id,
                    turn_id=resolved_turn_id,
                    turn_index=resolved_turn_index,
                    error_type=type(exc).__name__,
                )
                _record_activity(
                    activity,
                    RuntimeActivityEvent(
                        action="chat.turn_error",
                        actor=actor,
                        request_id=request_id,
                        channel=resolved_channel,
                        session_id=session_id,
                        turn_id=resolved_turn_id,
                        decision="error",
                        reason_code=type(exc).__name__,
                        metadata={"duration_ms": _duration_ms(turn_started)},
                    ),
                )
                return _with_save_error(error_text, save_error)

            if response.tool_calls:
                log_event(
                    llm_logger,
                    logging.DEBUG,
                    "llm.tool_calls",
                    session_id=session_id,
                    turn_id=resolved_turn_id,
                    count=len(response.tool_calls),
                    tools=",".join(_tool_call_names(response.tool_calls)),
                )

            assistant_msg = Message(
                role="assistant",
                content=str(response.content),
                tool_calls=list(response.tool_calls or []),
                metadata=dict(response.metadata or {}),
            )
            _assign_turn_fields(assistant_msg, resolved_turn_id, resolved_turn_index)
            pending_session_messages.append(assistant_msg)
            messages.append(_message_to_llm_dict(assistant_msg))

            if not assistant_msg.tool_calls:
                save_error = _append_session_messages(
                    sessions,
                    session_id,
                    pending_session_messages,
                    workspace,
                )
                _log_session_save(save_error, session_id, resolved_turn_id, len(pending_session_messages))
                log_event(
                    turn_logger,
                    logging.INFO,
                    "turn.done",
                    session_id=session_id,
                    turn_id=resolved_turn_id,
                    turn_index=resolved_turn_index,
                    duration_ms=_duration_ms(turn_started),
                    output_preview=_answer_output_preview(assistant_msg.content),
                )
                _record_activity(
                    activity,
                    RuntimeActivityEvent(
                        action="chat.turn_done",
                        actor=actor,
                        request_id=request_id,
                        channel=resolved_channel,
                        session_id=session_id,
                        turn_id=resolved_turn_id,
                        decision="done",
                        metadata={
                            "duration_ms": _duration_ms(turn_started),
                            "actual_endpoint": assistant_msg.metadata.get("endpoint_name", ""),
                            "actual_model": assistant_msg.metadata.get("model", ""),
                            "attempted_endpoints": assistant_msg.metadata.get(
                                "attempted_endpoints", []
                            ),
                        },
                    ),
                )
                return _with_save_error(assistant_msg.content, save_error)

            if tool_iterations >= self.max_tool_iterations:
                log_event(
                    tool_logger,
                    logging.WARNING,
                    "tool.iteration_limit",
                    session_id=session_id,
                    turn_id=resolved_turn_id,
                    limit=self.max_tool_iterations,
                )
                for index, raw_call in enumerate(assistant_msg.tool_calls):
                    call = _parse_tool_call(raw_call, index)
                    result = ToolResult(
                        output=TOOL_ITERATION_LIMIT_TEXT,
                        is_error=True,
                        metadata={"code": "TOOL_ITERATION_LIMIT", "tool_name": call.name},
                    )
                    tool_msg = _tool_result_to_message(call, call.error or result)
                    _assign_turn_fields(tool_msg, resolved_turn_id, resolved_turn_index)
                    pending_session_messages.append(tool_msg)
                limit_msg = Message(
                    role="assistant",
                    content=(
                        f"{TOOL_ITERATION_LIMIT_TEXT} The limit is "
                        f"{self.max_tool_iterations} tool iteration(s)."
                    ),
                    metadata={"is_error": True, "code": "TOOL_ITERATION_LIMIT"},
                )
                _assign_turn_fields(limit_msg, resolved_turn_id, resolved_turn_index)
                pending_session_messages.append(limit_msg)
                messages.append(_message_to_llm_dict(limit_msg))
                final_text = limit_msg.content
                try:
                    log_event(
                        llm_logger,
                        logging.DEBUG,
                        "llm.limit_summary",
                        session_id=session_id,
                        turn_id=resolved_turn_id,
                    )
                    limit_response = llm.chat(messages, tools=None)
                    if limit_response.tool_calls or not str(limit_response.content).strip():
                        raise RuntimeError("LLM did not produce a final limit summary")
                    final_msg = Message(
                        role="assistant",
                        content=str(limit_response.content),
                        metadata={
                            **dict(limit_response.metadata or {}),
                            "tool_iteration_limit": self.max_tool_iterations,
                        },
                    )
                    _assign_turn_fields(final_msg, resolved_turn_id, resolved_turn_index)
                    pending_session_messages.append(final_msg)
                    final_text = final_msg.content
                except Exception as exc:  # noqa: BLE001 - retain the deterministic limit result.
                    log_event(
                        llm_logger,
                        logging.WARNING,
                        "llm.limit_summary_failed",
                        session_id=session_id,
                        turn_id=resolved_turn_id,
                        error_type=type(exc).__name__,
                    )
                save_error = _append_session_messages(sessions, session_id, pending_session_messages, workspace)
                _log_session_save(save_error, session_id, resolved_turn_id, len(pending_session_messages))
                log_event(
                    turn_logger,
                    logging.WARNING,
                    "turn.done",
                    session_id=session_id,
                    turn_id=resolved_turn_id,
                    turn_index=resolved_turn_index,
                    duration_ms=_duration_ms(turn_started),
                    output_preview=_answer_output_preview(final_text),
                )
                _record_activity(
                    activity,
                    RuntimeActivityEvent(
                        action="chat.turn_done",
                        actor=actor,
                        request_id=request_id,
                        channel=resolved_channel,
                        session_id=session_id,
                        turn_id=resolved_turn_id,
                        decision="done",
                        reason_code="TOOL_ITERATION_LIMIT",
                        metadata={"duration_ms": _duration_ms(turn_started)},
                    ),
                )
                return _with_save_error(final_text, save_error)

            tool_iterations += 1
            for index, raw_call in enumerate(assistant_msg.tool_calls):
                try:
                    _raise_if_cancelled(cancellation_token)
                except TurnCancelledError:
                    return persist_cancelled_turn()
                call = _parse_tool_call(raw_call, index)
                if call.error is not None:
                    result = call.error
                    _log_tool_result(
                        result,
                        session_id=session_id,
                        turn_id=resolved_turn_id,
                        tool_name=call.name,
                        duration_ms=0,
                    )
                else:
                    result = _dispatch_tool(
                        tools,
                        call,
                        actor=actor,
                        session_id=session_id,
                        turn_id=resolved_turn_id,
                        turn_index=resolved_turn_index,
                        channel=resolved_channel,
                        request_id=request_id,
                        policy=execution_policy,
                        confirmation_broker=confirmations,
                        audit_sink=audit,
                        activity_sink=activity,
                        on_event=on_event,
                        cancellation_token=cancellation_token,
                    )
                try:
                    _raise_if_cancelled(cancellation_token)
                except TurnCancelledError:
                    return persist_cancelled_turn()
                tool_msg = _tool_result_to_message(call, result)
                _assign_turn_fields(tool_msg, resolved_turn_id, resolved_turn_index)
                pending_session_messages.append(tool_msg)
                messages.append(_message_to_llm_dict(tool_msg))


def _call_llm(
    llm: LLMProvider,
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    on_event: TurnEventCallback | None,
    cancellation_token: CancellationToken | None,
) -> LLMResponse:
    """Call a streaming provider when available, otherwise fall back to chat()."""

    stream_chat = getattr(llm, "stream_chat", None)
    if callable(stream_chat):
        content_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        metadata: dict[str, Any] = {}
        for raw_chunk in stream_chat(messages=messages, tools=tools):
            _raise_if_cancelled(cancellation_token)
            chunk = _normalize_stream_chunk(raw_chunk)
            if chunk.content_delta:
                content_parts.append(chunk.content_delta)
                _emit_event(on_event, {"type": "text_delta", "content": chunk.content_delta})
            if chunk.tool_calls:
                tool_calls = list(chunk.tool_calls)
            if chunk.metadata:
                metadata.update(chunk.metadata)
        return LLMResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            metadata=metadata,
        )

    response = llm.chat(messages=messages, tools=tools)
    if response.content:
        _emit_event(on_event, {"type": "text_delta", "content": response.content})
    return response


def _normalize_stream_chunk(raw_chunk: LLMStreamChunk | str) -> LLMStreamChunk:
    """Convert protocol-supported stream chunk shapes to LLMStreamChunk."""

    if isinstance(raw_chunk, LLMStreamChunk):
        return raw_chunk
    if isinstance(raw_chunk, str):
        return LLMStreamChunk(content_delta=raw_chunk)
    raise TypeError(f"Unsupported LLM stream chunk type: {type(raw_chunk).__name__}")


def _emit_event(on_event: TurnEventCallback | None, event: dict[str, Any]) -> None:
    """Emit one turn event without coupling AgentLoop to a transport."""

    if on_event is not None:
        on_event(event)


def _raise_if_cancelled(cancellation_token: CancellationToken | None) -> None:
    """Raise TurnCancelledError when the optional cancellation token is set."""

    if cancellation_token is not None:
        cancellation_token.raise_if_cancelled()


def _dispatch_tool(
    tools: ToolProvider | None,
    call: ParsedToolCall,
    *,
    actor: ActorContext | None,
    session_id: str,
    turn_id: str,
    turn_index: int,
    channel: str,
    request_id: str,
    policy: ToolExecutionPolicy | None,
    confirmation_broker: ToolConfirmationBroker | None,
    activity_sink: RuntimeActivitySink | None,
    audit_sink: AuditSink | None,
    on_event: TurnEventCallback | None,
    cancellation_token: CancellationToken | None,
) -> ToolResult:
    """Apply actor-aware policy and confirmation before the existing registry dispatch."""

    record_id = "tool-record-" + uuid.uuid4().hex
    if actor is None and policy is not None:
        return ToolResult(
            output="Tool execution actor is required.",
            is_error=True,
            metadata={"code": "ACTOR_REQUIRED", "tool_name": call.name},
        )

    context = None
    if actor is not None:
        context = ToolExecutionContext(
            actor=actor,
            session_id=session_id,
            turn_id=turn_id,
            turn_index=turn_index,
            channel=channel,
            request_id=request_id,
            tool_name=call.name,
            tool_call_id=call.id,
            tool_call_record_id=record_id,
        )
    metadata = {
        "tool_name": call.name,
        "tool_call_id": call.id,
        "args_preview": _safe_tool_args_preview(call.name, call.arguments),
    }
    if call.name == "exec":
        metadata["command_preview"] = preview_text(str(call.arguments.get("command") or ""), limit=200)
        metadata["cwd"] = preview_text(str(call.arguments.get("cwd") or "."), limit=120)
        metadata["timeout_seconds"] = call.arguments.get("timeout_seconds", 30)
    _record_activity(
        activity_sink,
        RuntimeActivityEvent(
            action="tool.call_requested",
            actor=actor,
            resource_id=call.id,
            request_id=request_id,
            channel=channel,
            session_id=session_id,
            turn_id=turn_id,
            tool_call_record_id=record_id,
            metadata=metadata,
        ),
    )

    decision = ToolExecutionDecision(
        action="allow",
        code="ALLOWED",
        message="Tool execution allowed",
        permission_key="",
    )
    if policy is not None and context is not None:
        decision = policy.decide(call.name, call.arguments, context)
    if _should_audit_tool(context, decision):
        _record_audit(
            audit_sink,
            AuditEvent(
                action="tool.call_requested",
                resource_type="tool_call",
                actor=actor,
                resource_id=call.id,
                request_id=request_id,
                channel=channel,
                session_id=session_id,
                turn_id=turn_id,
                tool_call_record_id=record_id,
                decision=decision.action,
                reason_code=decision.code,
                risk_category=decision.risk_category,
                metadata={
                    **metadata,
                    "permission_key": decision.permission_key,
                    "risk_level": decision.risk_level,
                },
            ),
        )
    if decision.action == "deny":
        _record_tool_decision(
            activity_sink,
            audit_sink,
            context,
            decision,
            action="tool.call_denied",
        )
        result = ToolResult(
            output=decision.message or "Tool execution denied.",
            is_error=True,
            metadata={
                "code": decision.code or ErrorCode.AUTH_PERMISSION_DENIED,
                "tool_name": call.name,
                "permission_key": decision.permission_key,
                "risk_category": decision.risk_category,
                "permission_decision": "deny",
            },
        )
        _record_tool_result_activity(activity_sink, context, decision, result)
        return result

    if decision.action == "confirm":
        if confirmation_broker is None or context is None:
            unavailable = ToolExecutionDecision(
                action="deny",
                code=ErrorCode.TOOL_CONFIRMATION_UNAVAILABLE,
                message="Explicit confirmation is unavailable for this channel.",
                permission_key=decision.permission_key,
                risk_level=decision.risk_level,
                risk_category=decision.risk_category,
            )
            _record_tool_decision(
                activity_sink,
                audit_sink,
                context,
                unavailable,
                action="tool.call_denied",
            )
            result = ToolResult(
                output=unavailable.message,
                is_error=True,
                metadata={"code": unavailable.code, "tool_name": call.name},
            )
            _record_tool_result_activity(activity_sink, context, unavailable, result)
            return result

        def on_requested(payload: dict[str, Any]) -> None:
            event = {
                "type": "tool_confirmation_required",
                "tool_name": call.name,
                "risk_level": decision.risk_level,
                "risk_category": decision.risk_category,
                "permission_key": decision.permission_key,
                "session_id": session_id,
                "turn_id": turn_id,
                **payload,
            }
            _emit_event(on_event, event)

        _record_tool_decision(
            activity_sink,
            audit_sink,
            context,
            decision,
            action="tool.confirmation_requested",
        )
        confirmation = confirmation_broker.request(
            decision,
            context,
            call.arguments,
            on_requested=on_requested,
            is_cancelled=(cancellation_token.is_cancelled if cancellation_token else None),
        )
        confirmation_action = f"tool.confirmation_{confirmation.status}"
        _record_audit(
            audit_sink,
            AuditEvent(
                action=confirmation_action,
                resource_type="tool_confirmation",
                actor=actor,
                resource_id=confirmation.confirmation_id,
                request_id=request_id,
                channel=channel,
                session_id=session_id,
                turn_id=turn_id,
                tool_call_record_id=record_id,
                decision=confirmation.status,
                risk_category=decision.risk_category,
                metadata={"tool_name": call.name},
            ),
        )
        if confirmation.status != "approved":
            result = ToolResult(
                output=confirmation.message or f"Tool confirmation {confirmation.status}.",
                is_error=True,
                metadata={
                    "code": f"CONFIRMATION_{confirmation.status.upper()}",
                    "tool_name": call.name,
                    "confirmation_id": confirmation.confirmation_id,
                    "confirmation_status": confirmation.status,
                },
            )
            _record_tool_result_activity(activity_sink, context, decision, result)
            return result
    _record_tool_decision(
        activity_sink,
        audit_sink,
        context,
        decision,
        action="tool.call_allowed",
    )
    result = _execute_tool(
        tools,
        call,
        session_id=session_id,
        turn_id=turn_id,
        turn_index=turn_index,
        actor=actor,
        request_id=request_id,
        channel=channel,
    )
    result_metadata = {
        "tool_name": call.name,
        "permission_key": decision.permission_key,
        "output_preview": _safe_tool_output_preview(call.name, result, limit=160),
        **{
            key: result.metadata[key]
            for key in (
                "cwd",
                "exit_code",
                "duration_ms",
                "duration_seconds",
                "timeout_seconds",
                "timed_out",
                "truncated",
                "stdout_tail",
                "stderr_tail",
            )
            if key in result.metadata
        },
    }
    result_event = RuntimeActivityEvent(
        action="tool.call_error" if result.is_error else "tool.call_done",
        actor=actor,
        resource_id=call.id,
        request_id=request_id,
        channel=channel,
        session_id=session_id,
        turn_id=turn_id,
        tool_call_record_id=record_id,
        decision="error" if result.is_error else "done",
        reason_code=str(result.metadata.get("code") or ""),
        risk_category=decision.risk_category,
        metadata=result_metadata,
    )
    _record_activity(activity_sink, result_event)
    if _should_audit_tool(context, decision):
        _record_audit(
            audit_sink,
            AuditEvent(
                action=result_event.action,
                resource_type="tool_call",
                actor=actor,
                resource_id=call.id,
                request_id=request_id,
                channel=channel,
                session_id=session_id,
                turn_id=turn_id,
                tool_call_record_id=record_id,
                decision=result_event.decision,
                reason_code=result_event.reason_code,
                risk_category=result_event.risk_category,
                metadata=result_metadata,
            ),
        )
    return result


def _record_tool_decision(
    activity_sink: RuntimeActivitySink | None,
    audit_sink: AuditSink | None,
    context: ToolExecutionContext | None,
    decision: ToolExecutionDecision,
    *,
    action: str,
) -> None:
    if context is None:
        return
    metadata = {
        "tool_name": context.tool_name,
        "permission_key": decision.permission_key,
        "risk_level": decision.risk_level,
        **decision.audit_metadata,
    }
    event = RuntimeActivityEvent(
        action=action,
        actor=context.actor,
        resource_id=context.tool_call_id,
        request_id=context.request_id,
        channel=context.channel,
        session_id=context.session_id,
        turn_id=context.turn_id,
        tool_call_record_id=context.tool_call_record_id,
        decision=decision.action,
        reason_code=decision.code,
        risk_category=decision.risk_category,
        metadata=metadata,
    )
    _record_activity(activity_sink, event)
    if _should_audit_tool(context, decision) or action == "tool.call_denied":
        _record_audit(
            audit_sink,
            AuditEvent(
                action=action,
                resource_type="tool_call",
                actor=context.actor,
                resource_id=context.tool_call_id,
                request_id=context.request_id,
                channel=context.channel,
                session_id=context.session_id,
                turn_id=context.turn_id,
                tool_call_record_id=context.tool_call_record_id,
                decision=decision.action,
                reason_code=decision.code,
                risk_category=decision.risk_category,
                metadata=metadata,
            ),
        )


def _record_tool_result_activity(
    activity_sink: RuntimeActivitySink | None,
    context: ToolExecutionContext | None,
    decision: ToolExecutionDecision,
    result: ToolResult,
) -> None:
    if context is None:
        return
    _record_activity(
        activity_sink,
        RuntimeActivityEvent(
            action="tool.call_error" if result.is_error else "tool.call_done",
            actor=context.actor,
            resource_id=context.tool_call_id,
            request_id=context.request_id,
            channel=context.channel,
            session_id=context.session_id,
            turn_id=context.turn_id,
            tool_call_record_id=context.tool_call_record_id,
            decision="error" if result.is_error else "done",
            reason_code=str(result.metadata.get("code") or ""),
            risk_category=decision.risk_category,
            metadata={
                "tool_name": context.tool_name,
                "permission_key": decision.permission_key,
                "output_preview": _safe_tool_output_preview(
                    context.tool_name,
                    result,
                    limit=160,
                ),
            },
        ),
    )


def _should_audit_tool(
    context: ToolExecutionContext | None,
    decision: ToolExecutionDecision,
) -> bool:
    if context is None:
        return False
    return (
        decision.action in {"deny", "confirm"}
        or bool(decision.permission_key)
        or decision.risk_level in {"high", "critical"}
        or context.tool_name in {"memory_write", "sync_skills"}
    )


def _record_audit(audit_sink: AuditSink | None, event: AuditEvent) -> None:
    """Best-effort audit emission that never breaks the generic Agent loop."""

    if audit_sink is None:
        return
    try:
        audit_sink.record(event)
    except Exception as exc:  # noqa: BLE001 - runtime audit failures are logged, not hidden.
        log_event(
            tool_logger,
            logging.ERROR,
            "audit.write_failed",
            action=event.action,
            error_type=type(exc).__name__,
        )


def _record_activity(
    activity_sink: RuntimeActivitySink | None,
    event: RuntimeActivityEvent,
) -> None:
    """Best-effort activity emission that never breaks the generic Agent loop."""

    if activity_sink is None:
        return
    try:
        activity_sink.record(event)
    except Exception as exc:  # noqa: BLE001 - runtime indexing failures stay observable.
        log_event(
            tool_logger,
            logging.ERROR,
            "activity.write_failed",
            action=event.action,
            error_type=type(exc).__name__,
        )


def _execute_tool(
    tools: ToolProvider | None,
    call: ParsedToolCall,
    *,
    session_id: str,
    turn_id: str,
    turn_index: int,
    actor: ActorContext | None = None,
    request_id: str = "",
    channel: str = "",
) -> ToolResult:
    """Dispatch one parsed tool call through the configured tool provider."""

    log_event(
        tool_logger,
        logging.INFO,
        "tool.start",
        session_id=session_id,
        turn_id=turn_id,
        tool=call.name,
        tool_call_id=call.id,
        actor_user_id=actor.user_id if actor else "",
        actor_username=actor.username if actor else "",
        turn_index=turn_index,
        request_id=request_id,
        channel=channel,
        args_preview=_safe_tool_args_preview(call.name, call.arguments),
    )
    started = time.perf_counter()
    if tools is None:
        result = ToolResult(
            output="Tool provider is not configured.",
            is_error=True,
            metadata={"code": "TOOLS_UNAVAILABLE", "tool_name": call.name},
        )
    else:
        result = tools.execute(call.name, call.arguments)
    duration_ms = _duration_ms(started)
    result.metadata.setdefault("duration_ms", duration_ms)
    _log_tool_result(
        result,
        session_id=session_id,
        turn_id=turn_id,
        tool_name=call.name,
        duration_ms=duration_ms,
        actor_user_id=actor.user_id if actor else "",
        actor_username=actor.username if actor else "",
        turn_index=turn_index,
        request_id=request_id,
        channel=channel,
        tool_call_id=call.id,
    )
    return result


def _log_tool_result(
    result: ToolResult,
    *,
    session_id: str,
    turn_id: str,
    tool_name: str,
    duration_ms: int,
    actor_user_id: str = "",
    actor_username: str = "",
    turn_index: int | None = None,
    request_id: str = "",
    channel: str = "",
    tool_call_id: str = "",
) -> None:
    """Log a bounded tool result preview."""

    fields: dict[str, Any] = {
        "session_id": session_id,
        "turn_id": turn_id,
        "tool": tool_name,
        "ok": not result.is_error,
        "duration_ms": duration_ms,
        "output_preview": _safe_tool_output_preview(tool_name, result, limit=120),
    }
    if actor_user_id:
        fields["actor_user_id"] = actor_user_id
    if actor_username:
        fields["actor_username"] = actor_username
    if turn_index is not None:
        fields["turn_index"] = turn_index
    if request_id:
        fields["request_id"] = request_id
    if channel:
        fields["channel"] = channel
    if tool_call_id:
        fields["tool_call_id"] = tool_call_id
    code = result.metadata.get("code")
    if code:
        fields["code"] = code
    for key in ("match_count", "total", "category", "operation"):
        value = result.metadata.get(key)
        if value not in (None, ""):
            fields[key] = value
    log_event(
        tool_logger,
        logging.WARNING if result.is_error else logging.INFO,
        "tool.error" if result.is_error else "tool.done",
        **fields,
    )


def _safe_tool_args_preview(tool_name: str, args: dict[str, Any]) -> str:
    """Return a bounded preview without persisting private Memory text."""

    if tool_name == "memory_read":
        query = args.get("query", "")
        return preview_json(
            {
                "mode": args.get("mode", ""),
                "category": args.get("category", ""),
                "query_length": len(query) if isinstance(query, str) else 0,
                "session_id": args.get("session_id", ""),
                "offset": args.get("offset", 0),
                "limit": args.get("limit", 8),
            },
            limit=200,
        )
    if tool_name == "memory_write":
        content = args.get("content", "")
        normalized = content if isinstance(content, str) else ""
        return preview_json(
            {
                "operation": args.get("operation", ""),
                "category": args.get("category", ""),
                "authorization": args.get("authorization", ""),
                "content_length": len(normalized),
                "content_hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest()
                if normalized
                else "",
                "old_content_hash": hashlib.sha256(
                    str(args.get("old_content", "")).encode("utf-8")
                ).hexdigest()
                if args.get("old_content")
                else "",
            },
            limit=300,
        )
    return preview_json(args, limit=200)


def _safe_tool_output_preview(tool_name: str, result: ToolResult, *, limit: int) -> str:
    """Return operational Memory result metadata instead of private content."""

    if tool_name in {"memory_read", "memory_write"}:
        return preview_json(
            {
                "ok": not result.is_error,
                "code": result.metadata.get("code", ""),
                "operation": result.metadata.get("operation", ""),
                "category": result.metadata.get("category", ""),
                "mode": result.metadata.get("mode", ""),
                "match_count": result.metadata.get("match_count", 0),
                "total": result.metadata.get("total", 0),
            },
            limit=limit,
        )
    return preview_text(result.output, limit=limit)


def _answer_output_preview(content: str, *, limit: int = 80) -> str:
    """Return the first non-empty answer line for terminal and trace summaries."""

    first_line = next((line.strip() for line in str(content).splitlines() if line.strip()), "")
    return preview_text(first_line, limit=limit)


def _log_session_save(
    save_error: str | None,
    session_id: str,
    turn_id: str,
    messages_count: int,
) -> None:
    """Log whether pending turn messages were saved."""

    if save_error:
        log_event(
            session_logger,
            logging.ERROR,
            "session.save_failed",
            session_id=session_id,
            turn_id=turn_id,
            messages=messages_count,
            error_preview=preview_text(save_error, limit=160),
        )
    return


def _tool_call_names(tool_calls: list[dict[str, Any]]) -> list[str]:
    """Return provider tool-call names for compact logging."""

    names: list[str] = []
    for raw_call in tool_calls:
        name = ""
        if isinstance(raw_call, dict):
            function = raw_call.get("function")
            if isinstance(function, dict):
                name = str(function.get("name") or "")
            if not name:
                name = str(raw_call.get("name") or "")
        names.append(name or "unknown_tool")
    return names


def _duration_ms(started: float) -> int:
    """Return elapsed monotonic time in milliseconds."""

    return max(0, int((time.perf_counter() - started) * 1000))


def _parse_tool_call(raw_call: object, index: int) -> ParsedToolCall:
    """Decode one provider tool-call object and validate its JSON arguments."""

    generated_id = False
    if isinstance(raw_call, dict):
        raw_id = raw_call.get("id")
        call_id = raw_id if isinstance(raw_id, str) and raw_id else f"call_{index}"
        generated_id = call_id != raw_id
        function = raw_call.get("function") if isinstance(raw_call.get("function"), dict) else {}
        raw_name = function.get("name") or raw_call.get("name")
        raw_arguments = function.get("arguments") if "arguments" in function else raw_call.get("arguments", {})
    else:
        call_id = f"call_{index}"
        generated_id = True
        raw_name = None
        raw_arguments = {}

    metadata = {"generated_tool_call_id": generated_id} if generated_id else {}
    if not isinstance(raw_name, str) or not raw_name.strip():
        return ParsedToolCall(
            id=call_id,
            name="unknown_tool",
            arguments={},
            metadata=metadata,
            error=ToolResult(
                output="Tool call is missing a function name.",
                is_error=True,
                metadata={"code": "MISSING_TOOL_NAME"},
            ),
        )

    name = raw_name.strip()
    if raw_arguments in (None, ""):
        arguments: dict[str, Any] = {}
    elif isinstance(raw_arguments, str):
        try:
            decoded = json.loads(raw_arguments)
        except json.JSONDecodeError:
            return ParsedToolCall(
                id=call_id,
                name=name,
                arguments={},
                metadata=metadata,
                error=ToolResult(
                    output="Tool arguments were not valid JSON.",
                    is_error=True,
                    metadata={"code": "INVALID_ARGUMENT_JSON"},
                ),
            )
        if not isinstance(decoded, dict):
            return ParsedToolCall(
                id=call_id,
                name=name,
                arguments={},
                metadata=metadata,
                error=ToolResult(
                    output="Tool arguments must be a JSON object.",
                    is_error=True,
                    metadata={"code": "INVALID_PARAM"},
                ),
            )
        arguments = decoded
    elif isinstance(raw_arguments, dict):
        arguments = dict(raw_arguments)
    else:
        return ParsedToolCall(
            id=call_id,
            name=name,
            arguments={},
            metadata=metadata,
            error=ToolResult(
                output="Tool arguments must be a JSON object.",
                is_error=True,
                metadata={"code": "INVALID_PARAM"},
            ),
        )

    return ParsedToolCall(id=call_id, name=name, arguments=arguments, metadata=metadata)


def _tool_result_to_message(call: ParsedToolCall, result: ToolResult) -> Message:
    """Wrap a ToolResult as an OpenAI-compatible tool message for the next LLM call."""

    metadata = {
        "tool_name": call.name,
        "is_error": result.is_error,
        **call.metadata,
        **result.metadata,
    }
    content = json.dumps(
        {
            "status": "error" if result.is_error else "success",
            "output": result.output,
            "metadata": {"tool_name": call.name, **call.metadata, **result.metadata},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return Message(
        role="tool",
        content=content,
        name=call.name,
        tool_call_id=call.id,
        metadata=metadata,
    )


def _assign_turn_fields(message: Message, turn_id: str, turn_index: int) -> Message:
    """Attach the active turn fields to one pending session message."""

    return assign_turn(message, turn_id=turn_id, turn_index=turn_index)


def _message_to_llm_dict(message: Message) -> dict[str, Any]:
    """Convert one internal Message to the chat message dict shape."""

    converted: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.name:
        converted["name"] = message.name
    if message.tool_call_id:
        converted["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        converted["tool_calls"] = message.tool_calls
    return converted


def _format_llm_error(exc: Exception, workspace: Path) -> str:
    """Turn provider/config failures into actionable CLI text."""

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
    """Bound provider error text before displaying it to the user."""

    return message[:500] if message else "unknown provider error"


def _extract_missing_env_name(message: str) -> str | None:
    """Pull the missing environment variable name out of config error text."""

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
    """Persist pending messages and return a user-facing save error if needed."""

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
    """Append a session-save warning to an otherwise valid assistant response."""

    if not save_error:
        return text
    return f"{text}\n\n{save_error}"
