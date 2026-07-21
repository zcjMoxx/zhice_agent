"""Configuration structures for workspace Tool Hooks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent.protocols.hook import HookStage

DEFAULT_HOOK_TIMEOUT_SECONDS = 2.0
MAX_HOOK_TIMEOUT_SECONDS = 10.0
DEFAULT_HOOK_OUTPUT_CHARS = 16384
MIN_HOOK_OUTPUT_CHARS = 1024
MAX_HOOK_OUTPUT_CHARS = 65536
MAX_HOOK_INPUT_CHARS = 32768


class HookConfigurationError(RuntimeError):
    """Raised when an explicit workspace Hook config is invalid."""


@dataclass(frozen=True)
class HookSpec:
    """One validated local Python Hook registration."""

    name: str
    stage: HookStage
    script: Path
    tools: tuple[str, ...]
    exempt_roles: tuple[str, ...] = ()
    exempt_permissions: tuple[str, ...] = ()
    timeout_seconds: float = DEFAULT_HOOK_TIMEOUT_SECONDS
    max_output_chars: int = DEFAULT_HOOK_OUTPUT_CHARS

    def matches(self, tool_name: str) -> bool:
        return "*" in self.tools or tool_name in self.tools

    def exempted_role(self, role_keys: tuple[str, ...]) -> str:
        """Return the first configured role that exempts this invocation."""

        return next((role for role in self.exempt_roles if role in role_keys), "")

    def exempted_permission(self, permission_keys: tuple[str, ...]) -> str:
        """Return the first configured permission that exempts this invocation."""

        return next(
            (permission for permission in self.exempt_permissions if permission in permission_keys),
            "",
        )


@dataclass(frozen=True)
class HookRegistry:
    """Ordered immutable Hook registrations loaded at startup."""

    hooks: tuple[HookSpec, ...] = ()

    def select(self, stage: HookStage, tool_name: str) -> tuple[HookSpec, ...]:
        return tuple(
            hook for hook in self.hooks if hook.stage == stage and hook.matches(tool_name)
        )
