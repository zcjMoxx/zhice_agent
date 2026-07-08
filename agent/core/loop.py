"""Agent loop with optional tool-calling support."""

from __future__ import annotations

import json
import logging
import time
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
from agent.protocols.llm import (
    LLMConfigurationError,
    LLMProvider,
    LLMProviderError,
    LLMResponse,
    LLMStreamChunk,
)
from agent.protocols.session import SessionStore
from agent.protocols.tool import ToolProvider, ToolResult

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
        max_tool_iterations: int = 4,
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

    def run_turn(
        self,
        session_id: str,
        user_text: str,
        *,
        turn_id: str | None = None,
        on_event: TurnEventCallback | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> str:
        """Run one user turn, saving user, assistant, and tool messages."""

        session = self.sessions.load(session_id)
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
            workspace=self.workspace,
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
        )

        pending_session_messages = [user_msg]
        tool_definitions = self.tools.definitions() if self.tools else None
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
                self.sessions,
                session_id,
                pending_session_messages,
                self.workspace,
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
            return _with_save_error(TURN_CANCELLED_TEXT, save_error)

        while True:
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
                )
                response = _call_llm(
                    self.llm,
                    messages=list(messages),
                    tools=tool_definitions,
                    on_event=on_event,
                    cancellation_token=cancellation_token,
                )
                _raise_if_cancelled(cancellation_token)
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
                )
                error_text = _format_llm_error(exc, self.workspace)
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
                    self.sessions,
                    session_id,
                    pending_session_messages,
                    self.workspace,
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
            else:
                log_event(
                    llm_logger,
                    logging.DEBUG,
                    "llm.direct",
                    session_id=session_id,
                    turn_id=resolved_turn_id,
                    output_preview=preview_text(response.content, limit=120),
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
                    self.sessions,
                    session_id,
                    pending_session_messages,
                    self.workspace,
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
                    output_preview=preview_text(assistant_msg.content, limit=120),
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
                    content=TOOL_ITERATION_LIMIT_TEXT,
                    metadata={"is_error": True, "code": "TOOL_ITERATION_LIMIT"},
                )
                _assign_turn_fields(limit_msg, resolved_turn_id, resolved_turn_index)
                pending_session_messages.append(limit_msg)
                save_error = _append_session_messages(
                    self.sessions,
                    session_id,
                    pending_session_messages,
                    self.workspace,
                )
                _log_session_save(save_error, session_id, resolved_turn_id, len(pending_session_messages))
                log_event(
                    turn_logger,
                    logging.WARNING,
                    "turn.done",
                    session_id=session_id,
                    turn_id=resolved_turn_id,
                    turn_index=resolved_turn_index,
                    duration_ms=_duration_ms(turn_started),
                    output_preview=TOOL_ITERATION_LIMIT_TEXT,
                )
                return _with_save_error(TOOL_ITERATION_LIMIT_TEXT, save_error)

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
                    result = _execute_tool(
                        self.tools,
                        call,
                        session_id=session_id,
                        turn_id=resolved_turn_id,
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


def _execute_tool(
    tools: ToolProvider | None,
    call: ParsedToolCall,
    *,
    session_id: str,
    turn_id: str,
) -> ToolResult:
    """Dispatch one parsed tool call through the configured tool provider."""

    log_event(
        tool_logger,
        logging.INFO,
        "tool.start",
        session_id=session_id,
        turn_id=turn_id,
        tool=call.name,
    )
    log_event(
        tool_logger,
        logging.DEBUG,
        "tool.args",
        session_id=session_id,
        turn_id=turn_id,
        tool=call.name,
        args_preview=preview_json(call.arguments, limit=200),
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
    _log_tool_result(
        result,
        session_id=session_id,
        turn_id=turn_id,
        tool_name=call.name,
        duration_ms=_duration_ms(started),
    )
    return result


def _log_tool_result(
    result: ToolResult,
    *,
    session_id: str,
    turn_id: str,
    tool_name: str,
    duration_ms: int,
) -> None:
    """Log a bounded tool result preview."""

    fields: dict[str, Any] = {
        "session_id": session_id,
        "turn_id": turn_id,
        "tool": tool_name,
        "ok": not result.is_error,
        "duration_ms": duration_ms,
        "output_preview": preview_text(result.output, limit=120),
    }
    code = result.metadata.get("code")
    if code:
        fields["code"] = code
    log_event(
        tool_logger,
        logging.WARNING if result.is_error else logging.INFO,
        "tool.error" if result.is_error else "tool.done",
        **fields,
    )


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
    log_event(
        session_logger,
        logging.DEBUG,
        "session.save",
        session_id=session_id,
        turn_id=turn_id,
        messages=messages_count,
    )


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
