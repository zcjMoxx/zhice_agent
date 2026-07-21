"""Provider-neutral Tool Hook Runtime contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

HookStage = Literal["pre_tooluse", "post_tooluse"]


@dataclass(frozen=True)
class PreToolHookRequest:
    """One parsed Tool call offered to configured pre-tool Hooks."""

    tool_name: str
    arguments: dict[str, Any]
    session_id: str
    turn_id: str
    request_id: str = ""
    channel: str = ""
    actor_type: str = ""
    role_keys: tuple[str, ...] = ()
    permission_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreToolHookResult:
    """Aggregate pre-tool Hook decision returned to AgentLoop."""

    action: Literal["continue", "block", "modify"]
    arguments: dict[str, Any] = field(default_factory=dict)
    code: str = ""
    message: str = ""


@dataclass(frozen=True)
class PostToolHookRequest:
    """Final Tool result offered to configured post-tool Hooks."""

    tool_name: str
    arguments: dict[str, Any]
    output: str
    is_error: bool
    result_metadata: dict[str, Any]
    session_id: str
    turn_id: str
    request_id: str = ""
    channel: str = ""
    actor_type: str = ""
    role_keys: tuple[str, ...] = ()
    permission_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class PostToolHookResult:
    """Validated presentation patch for a final Tool RuntimeEvent."""

    display: dict[str, Any] = field(default_factory=dict)
    ui_metadata: dict[str, Any] = field(default_factory=dict)


class HookRuntime(Protocol):
    """Run configured Tool Hooks without owning Tool execution or transport."""

    def run_pre_tooluse(self, request: PreToolHookRequest) -> PreToolHookResult: ...

    def run_post_tooluse(self, request: PostToolHookRequest) -> PostToolHookResult: ...
