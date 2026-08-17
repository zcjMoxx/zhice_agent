"""Provider-neutral contracts for bounded Subagent orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from agent.protocols.capability import CapabilityStatus
from agent.protocols.tool import ToolExecutionContext

SubagentReason = Literal[
    "parallel_independent",
    "context_isolation",
    "specialist_capability",
    "independent_verification",
    "explicit_user_request",
]
SubagentTaskStatus = Literal["completed", "failed", "timed_out", "cancelled"]
SubagentWorkspaceMode = Literal["shared_readonly", "worktree", "shared_exclusive"]
SubagentModelRole = Literal["inherit", "fast", "reasoning"]


def subagent_unavailable_payload(status: CapabilityStatus | None) -> dict[str, Any]:
    """Return the stable use-time error for an unavailable Subagent runtime."""

    resolved = status or CapabilityStatus(
        name="subagent",
        state="unavailable",
        code="SUBAGENT_STATUS_UNAVAILABLE",
        message="Subagent capability status is unavailable.",
        hint="Restart the process so startup capability checks can run.",
    )
    return {
        "code": "SUBAGENT_RUNTIME_UNAVAILABLE",
        "cause_code": resolved.code or "SUBAGENT_STATUS_UNAVAILABLE",
        "message": resolved.message or "Subagent runtime is unavailable.",
        "hint": resolved.hint,
    }


@dataclass(frozen=True)
class SubagentTask:
    """One model-selected task executed by a configured child Profile."""

    task_id: str
    task: str
    profile_name: str
    expected_output: str = ""


@dataclass(frozen=True)
class SubagentBatchRequest:
    """One bounded fan-out request from a parent Agent turn."""

    reason: SubagentReason
    tasks: tuple[SubagentTask, ...]


@dataclass(frozen=True)
class SubagentTaskResult:
    """Safe, bounded terminal result for one child task."""

    task_id: str
    status: SubagentTaskStatus
    code: str
    output: str
    subagent_id: str
    child_session_id: str
    child_turn_id: str
    duration_ms: int
    truncated: bool = False
    stage: str = ""


@dataclass(frozen=True)
class SubagentProfile:
    """Validated capability Profile selectable by the parent Agent."""

    name: str
    description: str
    tools: tuple[str, ...]
    initial_tools: tuple[str, ...] = ()
    denied_tools: tuple[str, ...] = ("delegate_tasks",)
    allowed_skills: tuple[str, ...] = ()
    preload_skills: tuple[str, ...] = ()
    workspace_mode: SubagentWorkspaceMode = "shared_readonly"
    max_tool_iterations: int = 10
    timeout_seconds: int = 180
    max_result_chars: int = 12000
    allow_model_invocation: bool = True
    model_role: SubagentModelRole = "inherit"


class SubagentProfileProvider(Protocol):
    """Read-only access to validated child capability Profiles."""

    def list_profiles(self) -> tuple[SubagentProfile, ...]:
        """Return enabled Profiles in stable configuration order."""

    def get_profile(self, name: str) -> SubagentProfile | None:
        """Return one Profile without inventing a fallback."""


class SubagentCoordinator(Protocol):
    """Execute one bounded child batch without exposing implementation details."""

    def run_batch(
        self,
        request: SubagentBatchRequest,
        context: ToolExecutionContext,
    ) -> tuple[SubagentTaskResult, ...]:
        """Run children and preserve result order from the input request."""
