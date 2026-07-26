"""Workspace-configured Tool Hook Runtime."""

from pathlib import Path

from agent.hooks.config import HookConfigurationError
from agent.hooks.loader import load_hook_registry_mapping
from agent.hooks.runner import HookProcessRunner
from agent.hooks.runtime import ConfiguredHookRuntime
from agent.runtime_config import load_runtime_section


def create_hook_runtime(workspace: Path, config_dir: Path) -> ConfiguredHookRuntime:
    """Build the Hook Runtime from the workspace runtime configuration."""

    try:
        section = load_runtime_section(config_dir, "hooks", default={})
    except ValueError as exc:
        raise HookConfigurationError("Cannot read Hook config: config.yml") from exc
    if not isinstance(section, dict):
        raise HookConfigurationError("config.yml hooks must be a mapping")
    normalized = {
        "version": section.get("version", 1),
        "hooks": section.get("entries", []),
    } if section else None
    registry = load_hook_registry_mapping(normalized, workspace=workspace)
    return ConfiguredHookRuntime(registry, HookProcessRunner(workspace))


__all__ = ["ConfiguredHookRuntime", "HookConfigurationError", "create_hook_runtime"]
