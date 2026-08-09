"""Tool registry implementation."""

from __future__ import annotations

import copy
import re
from typing import Any

from agent.protocols.tool import Tool, ToolExecutionContext, ToolResult

_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ToolRegistry:
    """Register tools, expose schemas, and execute tools by name."""

    def __init__(self, tools: list[Tool]):
        """Register the provided tools and reject duplicate names early."""

        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self._register(tool)

    def definitions(self) -> list[dict[str, Any]]:
        """Return OpenAI-compatible tool definitions."""

        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": copy.deepcopy(tool.parameters),
                },
            }
            for tool in self._tools.values()
        ]

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Execute a registered tool and convert dispatch errors to ToolResult."""

        if not isinstance(args, dict):
            return ToolResult(
                output="Tool arguments must be a JSON object.",
                is_error=True,
                metadata={"code": "INVALID_PARAM", "tool_name": name},
            )
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                output=f"Unknown tool: {name}",
                is_error=True,
                metadata={"code": "UNKNOWN_TOOL", "tool_name": name},
            )
        try:
            return tool.execute(args)
        except Exception as exc:  # noqa: BLE001 - custom tools must not break AgentLoop.
            return ToolResult(
                output="Tool execution failed.",
                is_error=True,
                metadata={
                    "code": "INTERNAL_ERROR",
                    "tool_name": name,
                    "error_type": type(exc).__name__,
                },
            )

    def execute_with_context(
        self,
        name: str,
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Dispatch contextual tools while retaining ordinary registry safety."""

        if not isinstance(args, dict):
            return ToolResult(
                output="Tool arguments must be a JSON object.",
                is_error=True,
                metadata={"code": "INVALID_PARAM", "tool_name": name},
            )
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                output=f"Unknown tool: {name}",
                is_error=True,
                metadata={"code": "UNKNOWN_TOOL", "tool_name": name},
            )
        try:
            contextual = getattr(tool, "execute_with_context", None)
            if callable(contextual):
                return contextual(args, context)
            return tool.execute(args)
        except Exception as exc:  # noqa: BLE001 - custom tools cannot break AgentLoop.
            return ToolResult(
                output="Tool execution failed.",
                is_error=True,
                metadata={
                    "code": "INTERNAL_ERROR",
                    "tool_name": name,
                    "error_type": type(exc).__name__,
                },
            )

    def _register(self, tool: Tool) -> None:
        """Validate and store one tool by its public name."""

        if not _TOOL_NAME_RE.fullmatch(tool.name):
            raise ValueError(f"invalid tool name: {tool.name}")
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool
