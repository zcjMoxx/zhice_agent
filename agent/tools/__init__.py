"""Built-in tool registry helpers."""

from pathlib import Path

from agent.tools.readonly import GrepTool, ListDirTool, ReadFileTool
from agent.tools.registry import ToolRegistry


def create_default_tool_registry(workspace: Path | str) -> ToolRegistry:
    """Create the first-stage read-only tool registry."""

    workspace_path = Path(workspace)
    return ToolRegistry(
        [
            ListDirTool(workspace_path),
            ReadFileTool(workspace_path),
            GrepTool(workspace_path),
        ]
    )


__all__ = [
    "GrepTool",
    "ListDirTool",
    "ReadFileTool",
    "ToolRegistry",
    "create_default_tool_registry",
]
