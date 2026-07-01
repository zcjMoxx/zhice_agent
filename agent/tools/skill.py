"""Tools for loading and synchronizing local Skills."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.protocols.skill import SkillError, SkillProvider
from agent.protocols.tool import ToolResult
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

    def __init__(self, workspace: Path | str, skill_sync: SkillSourceSync):
        """Store the configured sync service."""

        super().__init__(workspace)
        self.skill_sync = skill_sync

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        """Run configured Skill source sync and return a JSON summary."""

        source = require_string(args, "source", default="").strip()
        try:
            result = self.skill_sync.sync(
                source_names=[source] if source else None,
            )
        except SkillSyncError as exc:
            payload = {
                "code": "SKILL_SYNC_ERROR",
                "message": str(exc),
                "errors": [{"source": source or "all", "message": str(exc)}],
            }
            return ToolResult(
                output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                is_error=True,
                metadata={"code": "SKILL_SYNC_ERROR", "message": str(exc)},
            )

        payload = result.as_dict()
        output = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        is_error = bool(result.errors)
        metadata = {
            "code": "SKILL_SYNC_FAILED" if is_error else "OK",
            "status": payload["status"],
            **payload["counts"],
        }
        return ToolResult(output=output, is_error=is_error, metadata=metadata)


def _tool_error_from_skill(exc: SkillError) -> ToolExecutionError:
    """Convert SkillError into the error type understood by BaseTool."""

    payload = {"code": exc.code, "message": exc.output, **exc.metadata}
    return ToolExecutionError(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        exc.code,
        exc.metadata,
    )
