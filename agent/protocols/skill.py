"""Skill protocol and shared data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class SkillInfo:
    """Summary metadata for one local Skill package."""

    source: str
    name: str
    qualified_name: str
    description: str
    root: Path
    skill_file: Path
    scripts_dir: Path
    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)


class SkillError(RuntimeError):
    """Structured Skill failure that callers can turn into ToolResult."""

    def __init__(self, output: str, code: str, metadata: dict[str, Any] | None = None):
        """Store model-facing error text, stable code, and optional metadata."""

        super().__init__(output)
        self.output = output
        self.code = code
        self.metadata = dict(metadata or {})


class SkillProvider(Protocol):
    """Skill discovery contract consumed by context and tools."""

    def list_skills(self) -> list[SkillInfo]:
        """Return available local Skill summaries."""

    def get_skill(self, name: str, source: str | None = None) -> SkillInfo:
        """Return metadata for one Skill."""

    def get_skill_body(self, name: str, source: str | None = None) -> str:
        """Return the full SKILL.md body for one Skill."""
