"""Tools for loading and synchronizing local Skills."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from agent.logging_utils import redact_value
from agent.protocols.skill import (
    ProgressSink,
    SkillError,
    SkillExecutor,
    SkillProgress,
    SkillProvider,
    SkillResult,
    SkillRunRequest,
)
from agent.protocols.tool import ToolExecutionContext, ToolResult
from agent.skills.sync import SkillSourceSync, SkillSyncError
from agent.tools.base import (
    BaseTool,
    ToolExecutionError,
    relative_display_path,
    require_int,
    require_string,
    truncate_text,
)


class LoadSkillsTool(BaseTool):
    """Read the full SKILL.md for one local Skill."""

    name = "load_skills"
    description = "Load a local Skill instruction file by name."
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill name to load. Prefer qualified source/name.",
            },
            "source": {
                "type": "string",
                "description": "Optional configured source name for unqualified Skill names.",
            },
            "max_chars": {
                "type": "integer",
                "minimum": 1000,
                "maximum": 50000,
                "description": "Maximum characters to return.",
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    }

    def __init__(self, workspace: Path | str, skills: SkillProvider):
        """Store workspace and Skill provider."""

        super().__init__(workspace)
        self.skills = skills

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        """Load one Skill body and return bounded text."""

        name = require_string(args, "name", required=True).strip()
        source = require_string(args, "source", default="").strip() or None
        max_chars = require_int(args, "max_chars", default=20000, minimum=1000, maximum=50000)
        try:
            info = self.skills.get_skill(name, source=source)
            body = self.skills.get_skill_body(name, source=source)
        except SkillError as exc:
            raise _tool_error_from_skill(exc) from exc

        output = "\n".join(
            [
                f"skill: {info.qualified_name}",
                f"path: {relative_display_path(self.workspace, info.skill_file)}",
                "",
                body,
            ]
        )
        output, truncation = truncate_text(output, max_chars)
        metadata = {
            "skill": info.qualified_name,
            "name": info.name,
            "source": info.source,
            "path": relative_display_path(self.workspace, info.skill_file),
            "truncated": truncation["truncated"],
        }
        if truncation["truncated"]:
            metadata.update(truncation)
        return ToolResult(output=output, metadata=metadata)


class SyncSkillsTool(BaseTool):
    """Synchronize whitelisted configured Skill sources into the workspace."""

    name = "sync_skills"
    description = "Sync configured Skill sources into the runtime workspace."
    parameters = {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "Optional configured source name to sync.",
            },
        },
        "required": [],
        "additionalProperties": False,
    }

    def __init__(
        self,
        workspace: Path | str,
        skill_sync: SkillSourceSync,
        skills: SkillProvider | None = None,
    ):
        """Store the configured sync service."""

        super().__init__(workspace)
        self.skill_sync = skill_sync
        self.skills = skills

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        """Run configured Skill source sync and return a JSON summary."""

        source = require_string(args, "source", default="").strip()
        try:
            result = self.skill_sync.sync(
                source_names=[source] if source else None,
            )
        except SkillSyncError as exc:
            missing_config = "Skill source config is missing" in str(exc)
            payload = {
                "code": "SKILL_SYNC_ERROR",
                "message": (
                    "Skill source config is missing. Run zcagent init to create it."
                    if missing_config
                    else "Skill source synchronization failed."
                ),
                "errors": [
                    {"source": source or "all", "message": "Synchronization failed."}
                ],
            }
            return ToolResult(
                output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                is_error=True,
                metadata={
                    "code": "SKILL_SYNC_ERROR",
                    "error_type": type(exc).__name__,
                },
            )

        invalidate = getattr(self.skills, "invalidate", None)
        if callable(invalidate):
            invalidate(source or None)

        payload = result.as_dict()
        for item in payload["sources"]:
            if item["status"] == "failed":
                if item["error"] == "Skill source is not configured":
                    continue
                item["message"] = "Skill source synchronization failed."
                item["error"] = "Synchronization failed."
        output = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        is_error = bool(result.errors)
        metadata = {
            "code": "SKILL_SYNC_FAILED" if is_error else "OK",
            "status": payload["status"],
            **payload["counts"],
        }
        return ToolResult(output=output, is_error=is_error, metadata=metadata)


class RunSkillTool(BaseTool):
    """Execute one explicitly runnable Skill through the formal runtime boundary."""

    name = "run_skill"
    description = "Run an explicitly executable Skill by qualified source/name."
    parameters = {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "Qualified executable Skill name in source/name form.",
            },
            "params": {
                "type": "object",
                "description": "Skill parameters declared by the selected Skill.",
            },
        },
        "required": ["skill", "params"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        workspace: Path | str,
        skills: SkillProvider,
        executor: SkillExecutor,
    ):
        super().__init__(workspace)
        self.skills = skills
        self.executor = executor

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        del args
        return ToolResult(
            output="Skill execution requires trusted turn context.",
            is_error=True,
            metadata={"code": "SKILL_CONTEXT_REQUIRED", "tool_name": self.name},
        )

    def execute_with_context(
        self,
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Resolve authorization, emit lifecycle events, and run the Skill."""

        if not isinstance(args, dict):
            return _run_error("INVALID_PARAM", "Skill arguments must be an object.")
        unknown = sorted(set(args) - {"skill", "params"})
        if unknown:
            return _run_error("INVALID_PARAM", "Skill arguments contain unsupported fields.")
        qualified_name = args.get("skill")
        params = args.get("params")
        if not isinstance(qualified_name, str) or "/" not in qualified_name:
            return _run_error("INVALID_SKILL_NAME", "A qualified Skill name is required.")
        if not isinstance(params, dict):
            return _run_error("INVALID_SKILL_PARAMS", "Skill params must be an object.")
        try:
            actor_lookup = getattr(self.skills, "get_skill_for_actor", None)
            if callable(actor_lookup):
                info = actor_lookup(context.actor, qualified_name)
            else:
                info = self.skills.get_skill(qualified_name)
        except SkillError as exc:
            return ToolResult(
                output=exc.output,
                is_error=True,
                metadata={"code": exc.code, "tool_name": self.name},
            )
        if info.executable is None:
            runtime_error = info.metadata.get("runtime_error")
            code = (
                str(runtime_error.get("code"))
                if isinstance(runtime_error, dict)
                else "SKILL_NOT_EXECUTABLE"
            )
            return _run_error(code, "Skill is not executable.")

        run_id = "skill-run-" + uuid.uuid4().hex
        events = context.runtime_events
        if events is not None:
            events.emit(
                "skill.started",
                skill_run_id=run_id,
                tool_call_id=context.tool_call_id,
                tool_call_record_id=context.tool_call_record_id,
                parent_event_id=context.tool_started_event_id,
                metadata={"skill_name": info.qualified_name},
            )
        request = SkillRunRequest(
            run_id=run_id,
            qualified_name=info.qualified_name,
            params=params,
            actor_context=context.actor,
            session_id=context.session_id,
            turn_id=context.turn_id,
            request_id=context.request_id,
            cancellation_token=context.cancellation_token,
        )
        sink = _RuntimeProgressSink(context, run_id, info.qualified_name)
        try:
            result = self.executor.run(request, info, progress_sink=sink)
            if not isinstance(result, SkillResult):
                raise TypeError("SkillExecutor returned an invalid result")
        except Exception:  # noqa: BLE001 - every runtime failure must close the lifecycle.
            result = SkillResult(
                status="error",
                code="SKILL_EXECUTOR_FAILED",
                data=None,
                message="Skill execution failed safely.",
            )
        if events is not None:
            events.emit(
                "skill.completed" if result.status == "success" else "skill.failed",
                skill_run_id=run_id,
                tool_call_id=context.tool_call_id,
                tool_call_record_id=context.tool_call_record_id,
                parent_event_id=context.tool_started_event_id,
                metadata={
                    "skill_name": info.qualified_name,
                    "code": result.code,
                    "duration_ms": result.duration_ms,
                },
            )
        payload = {
            "status": result.status,
            "code": result.code,
            "data": redact_value(result.data),
            "message": result.message,
        }
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            is_error=result.status != "success",
            metadata={
                "code": result.code,
                "tool_name": self.name,
                "skill": info.qualified_name,
                "skill_run_id": run_id,
                "duration_ms": result.duration_ms,
            },
        )


class _RuntimeProgressSink(ProgressSink):
    def __init__(self, context: ToolExecutionContext, run_id: str, qualified_name: str):
        self.context = context
        self.run_id = run_id
        self.qualified_name = qualified_name

    def emit(self, progress: SkillProgress) -> None:
        events = self.context.runtime_events
        if events is None:
            return
        metadata: dict[str, Any] = {"skill_name": self.qualified_name}
        if progress.percent is not None:
            metadata["percent"] = progress.percent
        events.emit(
            "skill.progress",
            skill_run_id=self.run_id,
            tool_call_id=self.context.tool_call_id,
            tool_call_record_id=self.context.tool_call_record_id,
            parent_event_id=self.context.tool_started_event_id,
            display={"detail": progress.message},
            metadata=metadata,
        )


def _run_error(code: str, message: str) -> ToolResult:
    return ToolResult(
        output=message,
        is_error=True,
        metadata={"code": code, "tool_name": RunSkillTool.name},
    )


def _tool_error_from_skill(exc: SkillError) -> ToolExecutionError:
    """Convert SkillError into the error type understood by BaseTool."""

    payload = {"code": exc.code, "message": exc.output, **exc.metadata}
    return ToolExecutionError(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        exc.code,
        exc.metadata,
    )
