"""Stable workflow definition and execution records."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

NODE_TYPES = frozenset({"schedule_trigger", "mcp_query", "mcp_action", "llm_transform", "template", "condition", "official_notification", "personal_email", "qq_notification"})
WORKFLOW_STATUSES = frozenset({"draft", "active", "paused", "paused_attention", "archived"})
RUN_STATUSES = frozenset({"queued", "running", "succeeded", "failed", "cancelled", "partial"})


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1
    backoff_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 5 or not 0 <= self.backoff_seconds <= 60:
            raise ValueError("invalid retry policy")


@dataclass(frozen=True)
class WorkflowNode:
    id: str
    type: str
    position: dict[str, float] = field(default_factory=lambda: {"x": 0, "y": 0})
    title: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    input_bindings: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 60.0
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    def __post_init__(self) -> None:
        if not self.id or len(self.id) > 100 or self.type not in NODE_TYPES:
            raise ValueError("invalid workflow node")
        if not 0 < self.timeout_seconds <= 3600:
            raise ValueError("invalid node timeout")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WorkflowNode":
        data = dict(value)
        data["retry_policy"] = RetryPolicy(**data.get("retry_policy", {}))
        return cls(**data)


@dataclass(frozen=True)
class WorkflowEdge:
    id: str
    source_node_id: str
    source_port: str = "output"
    target_node_id: str = ""
    target_port: str = "input"
    condition_branch: Literal["true", "false"] | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WorkflowEdge":
        return cls(**value)


@dataclass(frozen=True)
class WorkflowDefinitionV1:
    workflow_id: str
    owner_user_id: str
    name: str
    nodes: tuple[WorkflowNode, ...]
    edges: tuple[WorkflowEdge, ...]
    description: str = ""
    schema_version: int = 1
    version: int = 1
    status: str = "draft"
    timezone: str = "Asia/Shanghai"
    required_permissions: tuple[str, ...] = ()
    connection_ids: tuple[str, ...] = ()
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    published_at: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1 or not self.workflow_id or not self.owner_user_id:
            raise ValueError("invalid workflow definition identity")
        if not self.name.strip() or len(self.name) > 120 or self.status not in WORKFLOW_STATUSES:
            raise ValueError("invalid workflow definition")
        if self.version < 1:
            raise ValueError("workflow version must be positive")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["nodes"] = [asdict(node) for node in self.nodes]
        data["edges"] = [asdict(edge) for edge in self.edges]
        data["required_permissions"] = list(self.required_permissions)
        data["connection_ids"] = list(self.connection_ids)
        return data

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WorkflowDefinitionV1":
        data = dict(value)
        data["nodes"] = tuple(WorkflowNode.from_dict(node) for node in data.get("nodes", []))
        data["edges"] = tuple(WorkflowEdge.from_dict(edge) for edge in data.get("edges", []))
        data["required_permissions"] = tuple(data.get("required_permissions", []))
        data["connection_ids"] = tuple(data.get("connection_ids", []))
        return cls(**data)


@dataclass(frozen=True)
class WorkflowRun:
    id: str
    workflow_id: str
    version: int
    owner_user_id: str
    trigger_type: str
    status: str = "queued"
    scheduled_for: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error_code: str | None = None
