"""Subagent configuration and runtime building blocks.

The package entrypoint stays lazy so lightweight helpers such as presentation do not
load the Coordinator and re-enter ``agent.core.loop`` during Tool package imports.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "BoundedSubagentCoordinator": ("agent.subagents.coordinator", "BoundedSubagentCoordinator"),
    "ChildAgentFactory": ("agent.subagents.factory", "ChildAgentFactory"),
    "SubagentConfig": ("agent.subagents.config", "SubagentConfig"),
    "SubagentConfigurationError": ("agent.subagents.config", "SubagentConfigurationError"),
    "WorkspaceManager": ("agent.subagents.workspace", "WorkspaceManager"),
    "load_subagent_config": ("agent.subagents.config", "load_subagent_config"),
    "shared_workspace_manager": ("agent.subagents.workspace", "shared_workspace_manager"),
}

__all__ = [
    "BoundedSubagentCoordinator",
    "ChildAgentFactory",
    "SubagentConfig",
    "SubagentConfigurationError",
    "WorkspaceManager",
    "load_subagent_config",
    "shared_workspace_manager",
]


def __getattr__(name: str) -> Any:
    """Resolve public Subagent exports without eagerly importing runtime modules."""

    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value
