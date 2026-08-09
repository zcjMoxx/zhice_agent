"""User-files tool registry with a virtual shared read-only mount."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.protocols.auth import ActorContext
from agent.protocols.diagnostics import DiagnosticContext
from agent.protocols.memory import MemoryStore
from agent.protocols.skill import SkillExecutor, SkillProvider
from agent.protocols.tool import ToolResult
from agent.skills.executor import PythonSkillExecutor
from agent.skills.sync import SkillSourceSync
from agent.tools.diagnostics import DiagnoseRecentActivityTool, DiagnoseSystemActivityTool
from agent.tools.exec import ExecTool
from agent.tools.memory import MemoryReadTool, MemoryWriteTool
from agent.tools.readonly import GrepTool, ListDirTool, ReadFileTool
from agent.tools.registry import ToolRegistry
from agent.tools.skill import LoadSkillsTool, RunSkillTool, SyncSkillsTool

_READONLY_NAMES = {"list_dir", "read_file", "grep"}


class UserScopedToolProvider:
    """Expose user files as '.', plus shared read-only content as 'shared/'."""

    def __init__(
        self,
        *,
        files_dir: Path,
        shared_readonly_dir: Path,
        actor: ActorContext,
        skills: SkillProvider | None = None,
        skill_sync: SkillSourceSync | None = None,
        skill_executor: SkillExecutor | None = None,
        diagnostics=None,
        system_diagnostics=None,
        diagnostic_context: DiagnosticContext | None = None,
        memory_store: MemoryStore | None = None,
        memory_safety=None,
        extra_tools=None,
    ):
        primary_tools = [
            ListDirTool(files_dir),
            ReadFileTool(files_dir),
            GrepTool(files_dir),
            ExecTool(files_dir, allow_confirmable=True),
        ]
        if skills is not None:
            primary_tools.append(LoadSkillsTool(files_dir, skills))
            primary_tools.append(
                RunSkillTool(files_dir, skills, skill_executor or PythonSkillExecutor())
            )
        if skill_sync is not None:
            primary_tools.append(SyncSkillsTool(files_dir, skill_sync, skills))
        if diagnostics is not None:
            primary_tools.append(
                DiagnoseRecentActivityTool(
                    files_dir,
                    actor=actor,
                    diagnostics=diagnostics,
                    context=diagnostic_context,
                )
            )
        if system_diagnostics is not None and actor.has_permission("diagnostics.system.use"):
            primary_tools.append(
                DiagnoseSystemActivityTool(
                    files_dir,
                    actor=actor,
                    diagnostics=system_diagnostics,
                )
            )
        if memory_store is not None and memory_safety is not None:
            primary_tools.extend(
                [
                    MemoryReadTool(files_dir, store=memory_store),
                    MemoryWriteTool(
                        files_dir,
                        store=memory_store,
                        safety=memory_safety,
                    ),
                ]
            )
        primary_tools.extend(list(extra_tools or ()))
        self._primary = ToolRegistry(primary_tools)
        self._shared = ToolRegistry(
            [
                ListDirTool(shared_readonly_dir),
                ReadFileTool(shared_readonly_dir),
                GrepTool(shared_readonly_dir),
            ]
        )

    def definitions(self) -> list[dict[str, Any]]:
        return self._primary.definitions()

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        if name not in _READONLY_NAMES or not isinstance(args, dict):
            return self._primary.execute(name, args)
        path = str(args.get("path") or ".").replace("\\", "/")
        if path == "shared" or path.startswith("shared/"):
            shared_args = dict(args)
            shared_args["path"] = "." if path == "shared" else path.removeprefix("shared/")
            result = self._shared.execute(name, shared_args)
            metadata = dict(result.metadata)
            metadata["path"] = "shared" + (
                f"/{metadata['path']}" if metadata.get("path") not in {None, "", "."} else ""
            )
            return ToolResult(output=result.output, is_error=result.is_error, metadata=metadata)
        result = self._primary.execute(name, args)
        if name == "list_dir" and path in {"", "."} and not result.is_error:
            output = f"{result.output}\nDIR  shared" if result.output else "DIR  shared"
            return ToolResult(output=output, metadata=dict(result.metadata))
        return result

    def execute_with_context(
        self,
        name: str,
        args: dict[str, Any],
        context,
    ) -> ToolResult:
        """Preserve trusted context while retaining shared read-only routing."""

        if name not in _READONLY_NAMES or not isinstance(args, dict):
            return self._primary.execute_with_context(name, args, context)
        return self.execute(name, args)
