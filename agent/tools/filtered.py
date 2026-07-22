"""Schema and dispatch filtering for child Agent ToolProviders."""

from __future__ import annotations

import copy
import re
from typing import Any

from agent.protocols.auth import AuditEvent, AuditSink
from agent.protocols.tool import ToolExecutionContext, ToolProvider, ToolResult

_EXACT_TOOL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MCP_PATTERN_RE = re.compile(r"^mcp__[A-Za-z0-9_-]+__\*$")
_KERNEL_DENIED_TOOLS = ("delegate_tasks",)


class FilteredToolProvider:
    """Expose only the parent-visible Tools selected by one child Profile."""

    def __init__(
        self,
        parent: ToolProvider,
        *,
        allowed_tools: tuple[str, ...] | list[str],
        denied_tools: tuple[str, ...] | list[str] = (),
        kernel_denied_tools: tuple[str, ...] | list[str] = _KERNEL_DENIED_TOOLS,
        audit_sink: AuditSink | None = None,
    ):
        self._parent = parent
        self._allowed = _validated_patterns(allowed_tools, required=True)
        self._denied = _validated_patterns(denied_tools, required=False)
        self._kernel_denied = _validated_patterns(kernel_denied_tools, required=False)
        self._audit_sink = audit_sink
        self._effective_names = frozenset(self._select_effective_names())

    @property
    def effective_tool_names(self) -> tuple[str, ...]:
        """Return effective Tool names in the parent's schema order."""

        return tuple(self._select_effective_names())

    def definitions(self) -> list[dict[str, Any]]:
        """Return only effective parent schemas and protect parent-owned objects."""

        return [
            copy.deepcopy(definition)
            for definition in self._parent.definitions()
            if _definition_name(definition) in self._effective_names
        ]

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Re-check the effective set before ordinary parent dispatch."""

        denied = self._denied_result(name)
        if denied is not None:
            return denied
        return self._parent.execute(name, args)

    def execute_with_context(
        self,
        name: str,
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Re-check then preserve trusted context when the parent supports it."""

        denied = self._denied_result(name, context)
        if denied is not None:
            return denied
        contextual_execute = getattr(self._parent, "execute_with_context", None)
        if callable(contextual_execute):
            return contextual_execute(name, args, context)
        return self._parent.execute(name, args)

    def _denied_result(
        self,
        name: str,
        context: ToolExecutionContext | None = None,
    ) -> ToolResult | None:
        if name in self._effective_names:
            return None
        if context is not None and self._audit_sink is not None:
            try:
                self._audit_sink.record(
                    AuditEvent(
                        action="subagent.tool_denied",
                        resource_type="tool_call",
                        actor=context.actor,
                        resource_id=context.tool_call_id,
                        request_id=context.request_id,
                        channel=context.channel,
                        session_id=context.session_id,
                        turn_id=context.turn_id,
                        tool_call_record_id=context.tool_call_record_id,
                        decision="deny",
                        reason_code="SUBAGENT_TOOL_NOT_ALLOWED",
                        risk_category="subagent_capability",
                        metadata={"tool_name": name, "task_id": context.task_id},
                    )
                )
            except Exception:  # noqa: BLE001 - audit failures cannot change ToolResult.
                pass
        return ToolResult(
            output=f"Subagent is not allowed to use tool: {name}",
            is_error=True,
            metadata={"code": "SUBAGENT_TOOL_NOT_ALLOWED", "tool_name": name},
        )

    def _select_effective_names(self) -> list[str]:
        names: list[str] = []
        for definition in self._parent.definitions():
            name = _definition_name(definition)
            if not name or name in names:
                continue
            if not _matches_any(name, self._allowed):
                continue
            if _matches_any(name, self._denied) or _matches_any(name, self._kernel_denied):
                continue
            names.append(name)
        return names


def _definition_name(definition: Any) -> str:
    if not isinstance(definition, dict):
        return ""
    function = definition.get("function")
    if not isinstance(function, dict):
        return ""
    name = function.get("name")
    return name if isinstance(name, str) else ""


def _validated_patterns(value, *, required: bool) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or (required and not value):
        requirement = "a non-empty sequence" if required else "a sequence"
        raise ValueError(f"tool patterns must be {requirement}")
    patterns: list[str] = []
    for item in value:
        if not isinstance(item, str) or not (
            _EXACT_TOOL_RE.fullmatch(item) or _MCP_PATTERN_RE.fullmatch(item)
        ):
            raise ValueError(f"invalid tool pattern: {item!r}")
        if item not in patterns:
            patterns.append(item)
    return tuple(patterns)


def _matches_any(name: str, patterns: tuple[str, ...]) -> bool:
    return any(name == pattern or (pattern.endswith("__*") and name.startswith(pattern[:-1])) for pattern in patterns)
