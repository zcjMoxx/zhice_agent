"""Agent loop with optional tool-calling support."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.console import console
from agent.context import ContextBuilder
from agent.message import Message
from agent.protocols.llm import LLMConfigurationError, LLMProvider, LLMProviderError
from agent.protocols.session import SessionStore
from agent.protocols.tool import ToolProvider, ToolResult

ASSISTANT_ERROR_TEXT = "LLM call failed. Check the workspace configuration and retry."
TOOL_ITERATION_LIMIT_TEXT = "Tool call limit reached. Please retry with a narrower request."


@dataclass
class ParsedToolCall:
    """Decoded tool call request from an LLM response."""

    id: str
    name: str
    arguments: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    error: ToolResult | None = None


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

    def run_turn(self, session_id: str, user_text: str) -> str:
        """Run one user turn, saving user, assistant, and tool messages."""

        session = self.sessions.load(session_id)
        user_msg = Message(role="user", content=user_text)
        messages = self.context_builder.build(
            history=session.messages,
            user_message=user_msg,
            workspace=self.workspace,
            session_id=session_id,
        )

        pending_session_messages = [user_msg]
        tool_definitions = self.tools.definitions() if self.tools else None
        tool_iterations = 0

        while True:
            try:
                response = self.llm.chat(messages=list(messages), tools=tool_definitions)
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
                pending_session_messages.append(assistant_msg)
                save_error = _append_session_messages(
                    self.sessions,
                    session_id,
                    pending_session_messages,
                    self.workspace,
                )
                return _with_save_error(error_text, save_error)

            assistant_msg = Message(
                role="assistant",
                content=str(response.content),
                tool_calls=list(getattr(response, "tool_calls", []) or []),
                metadata=dict(getattr(response, "metadata", {}) or {}),
            )
            pending_session_messages.append(assistant_msg)
            messages.append(_message_to_llm_dict(assistant_msg))

            if not assistant_msg.tool_calls:
                save_error = _append_session_messages(
                    self.sessions,
                    session_id,
                    pending_session_messages,
                    self.workspace,
                )
                return _with_save_error(assistant_msg.content, save_error)

            if tool_iterations >= self.max_tool_iterations:
                for index, raw_call in enumerate(assistant_msg.tool_calls):
                    call = _parse_tool_call(raw_call, index)
                    result = ToolResult(
                        output=TOOL_ITERATION_LIMIT_TEXT,
                        is_error=True,
                        metadata={"code": "TOOL_ITERATION_LIMIT", "tool_name": call.name},
                    )
                    tool_msg = _tool_result_to_message(call, call.error or result)
                    pending_session_messages.append(tool_msg)
                limit_msg = Message(
                    role="assistant",
                    content=TOOL_ITERATION_LIMIT_TEXT,
                    metadata={"is_error": True, "code": "TOOL_ITERATION_LIMIT"},
                )
                pending_session_messages.append(limit_msg)
                save_error = _append_session_messages(
                    self.sessions,
                    session_id,
                    pending_session_messages,
                    self.workspace,
                )
                return _with_save_error(TOOL_ITERATION_LIMIT_TEXT, save_error)

            tool_iterations += 1
            for index, raw_call in enumerate(assistant_msg.tool_calls):
                call = _parse_tool_call(raw_call, index)
                result = call.error or _execute_tool(self.tools, call)
                tool_msg = _tool_result_to_message(call, result)
                pending_session_messages.append(tool_msg)
                messages.append(_message_to_llm_dict(tool_msg))


def _execute_tool(tools: ToolProvider | None, call: ParsedToolCall) -> ToolResult:
    """Dispatch one parsed tool call through the configured tool provider."""

    if tools is None:
        return ToolResult(
            output="Tool provider is not configured.",
            is_error=True,
            metadata={"code": "TOOLS_UNAVAILABLE", "tool_name": call.name},
        )
    return tools.execute(call.name, call.arguments)


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
