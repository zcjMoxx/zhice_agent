"""Synchronous Tool adapter over the process-wide MCP Runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agent.protocols.auth import ActorContext
from agent.protocols.mcp import McpInteractionNotifier, McpRuntimeFacade, McpToolDescriptor
from agent.protocols.tool import ToolResult


class McpToolAdapter:
    """Bind one discovered MCP Tool to the current actor's artifact boundary."""

    def __init__(
        self,
        descriptor: McpToolDescriptor,
        runtime: McpRuntimeFacade,
        *,
        actor: ActorContext,
        files_dir: Path,
        session_id: str = "",
        interaction_notifier: McpInteractionNotifier | None = None,
        result_observer: Callable[[str, str, ToolResult], None] | None = None,
    ) -> None:
        self.name = descriptor.local_name
        self.description = descriptor.description
        self.parameters = descriptor.input_schema
        self._descriptor = descriptor
        self._runtime = runtime
        self._actor = actor
        self._files_dir = files_dir
        self._session_id = session_id
        self._interaction_notifier = interaction_notifier
        self._result_observer = result_observer

    def execute(self, args: dict[str, Any]) -> ToolResult:
        """Call the remote Tool through the shared runtime."""

        result = self._runtime.call_tool_sync(
            self._descriptor,
            args,
            actor=self._actor,
            files_dir=self._files_dir,
            session_id=self._session_id,
            interaction_notifier=self._interaction_notifier,
        )
        if self._result_observer is not None:
            try:
                self._result_observer(self._session_id, self.name, result)
            except Exception:  # noqa: BLE001 - observation must not change Tool execution.
                pass
        return result
