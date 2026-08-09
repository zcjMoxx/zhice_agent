"""Built-in tool registry helpers."""

from pathlib import Path

from agent.protocols.memory import MemoryStore
from agent.protocols.skill import SkillExecutor, SkillProvider
from agent.protocols.tool import Tool
from agent.skills.executor import PythonSkillExecutor
from agent.skills.sync import SkillSourceSync
from agent.tools.discovery import DiscoverableToolProvider, with_tool_discovery
from agent.tools.exec import ExecTool
from agent.tools.filtered import FilteredToolProvider
from agent.tools.mcp import McpToolAdapter
from agent.tools.memory import MemoryReadTool, MemoryWriteTool
from agent.tools.readonly import GrepTool, ListDirTool, ReadFileTool
from agent.tools.registry import ToolRegistry
from agent.tools.scoped import UserScopedToolProvider
from agent.tools.skill import LoadSkillsTool, RunSkillTool, SyncSkillsTool
from agent.tools.subagent import AugmentedToolProvider, DelegateTasksTool


def create_default_tool_registry(
    workspace: Path | str,
    skills: SkillProvider | None = None,
    skill_sync: SkillSourceSync | None = None,
    skill_executor: SkillExecutor | None = None,
    *,
    allow_confirmable_exec: bool = False,
    memory_store: MemoryStore | None = None,
    memory_safety=None,
    extra_tools: list[Tool] | None = None,
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
        tools.append(RunSkillTool(workspace_path, skills, skill_executor or PythonSkillExecutor()))
    if skill_sync is not None:
        tools.append(SyncSkillsTool(workspace_path, skill_sync, skills))
    if memory_store is not None and memory_safety is not None:
        tools.extend(
            [
                MemoryReadTool(workspace_path, store=memory_store),
                MemoryWriteTool(
                    workspace_path,
                    store=memory_store,
                    safety=memory_safety,
                ),
            ]
        )
    tools.extend(extra_tools or [])
    return ToolRegistry(tools)


__all__ = [
    "ExecTool",
    "AugmentedToolProvider",
    "DelegateTasksTool",
    "DiscoverableToolProvider",
    "FilteredToolProvider",
    "GrepTool",
    "ListDirTool",
    "LoadSkillsTool",
    "MemoryReadTool",
    "MemoryWriteTool",
    "McpToolAdapter",
    "ReadFileTool",
    "RunSkillTool",
    "SyncSkillsTool",
    "ToolRegistry",
    "UserScopedToolProvider",
    "create_default_tool_registry",
    "with_tool_discovery",
]
