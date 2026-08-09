"""Skill protocol and shared data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from agent.protocols.auth import ActorContext


class CancellationToken(Protocol):
    """Cancellation probe shared with long-running Skill execution."""

    def is_cancelled(self) -> bool:
        """Return whether the caller requested cancellation."""


@dataclass(frozen=True)
class ExecutableSkillInfo:
    """Validated executable metadata declared explicitly in Skill frontmatter."""

    runtime_type: Literal["python"]
    entrypoint: Path
    protocol: Literal["ndjson-v1"]
    timeout_seconds: int
    params_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class SkillRunRequest:
    """Trusted request passed from ``run_skill`` to a SkillExecutor."""

    run_id: str
    qualified_name: str
    params: dict[str, Any]
    actor_context: ActorContext
    session_id: str
    turn_id: str
    request_id: str = ""
    timeout_seconds: int | None = None
    cancellation_token: CancellationToken | None = None


@dataclass(frozen=True)
class SkillProgress:
    """One safe, bounded progress update emitted by an executable Skill."""

    run_id: str
    qualified_name: str
    message: str
    percent: int | None = None


@dataclass(frozen=True)
class SkillResult:
    """Validated terminal result returned by an executable Skill."""

    status: Literal["success", "error", "cancelled"]
    code: str
    data: Any
    message: str
    error_stack: str = ""
    duration_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class SkillRuntimeError(RuntimeError):
    """Internal structured runtime failure with a safe model-facing message."""

    def __init__(
        self,
        code: str,
        message_safe: str,
        *,
        metadata: dict[str, Any] | None = None,
    ):
        super().__init__(message_safe)
        self.code = code
        self.message_safe = message_safe
        self.metadata = dict(metadata or {})


class ProgressSink(Protocol):
    """Receive one already-sanitized Skill progress update."""

    def emit(self, progress: SkillProgress) -> None:
        """Forward progress without owning execution or transport semantics."""


class SkillExecutor(Protocol):
    """Execute one validated executable Skill without shell interpretation."""

    def run(
        self,
        request: SkillRunRequest,
        skill: SkillInfo,
        *,
        progress_sink: ProgressSink | None = None,
    ) -> SkillResult:
        """Return a structured result for every success or failure path."""


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
    executable: ExecutableSkillInfo | None = None


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
