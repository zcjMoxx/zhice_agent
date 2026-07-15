"""Built-in tool registry helpers."""

from pathlib import Path

from agent.protocols.skill import SkillProvider
from agent.skills.sync import SkillSourceSync
from agent.tools.exec import ExecTool
from agent.tools.readonly import GrepTool, ListDirTool, ReadFileTool
from agent.tools.registry import ToolRegistry
from agent.tools.scoped import UserScopedToolProvider
from agent.tools.skill import LoadSkillsTool, SyncSkillsTool


def create_default_tool_registry(
    workspace: Path | str,
    skills: SkillProvider | None = None,
    skill_sync: SkillSourceSync | None = None,
    *,
    allow_confirmable_exec: bool = False,
) -> ToolRegistry:
    """Create the default local workspace tool registry."""

    workspace_path = Path(workspace)
    tools = [
        ListDirTool(workspace_path),
        ReadFileTool(workspace_path),
        GrepTool(workspace_path),
        ExecTool(workspace_path, allow_confirmable=allow_confirmable_exec),
    ]
    if skills is not None:
        tools.append(LoadSkillsTool(workspace_path, skills))
    if skill_sync is not None:
        tools.append(SyncSkillsTool(workspace_path, skill_sync))
    return ToolRegistry(tools)


__all__ = [
    "ExecTool",
    "GrepTool",
    "ListDirTool",
    "LoadSkillsTool",
    "ReadFileTool",
    "SyncSkillsTool",
    "ToolRegistry",
    "UserScopedToolProvider",
    "create_default_tool_registry",
]
