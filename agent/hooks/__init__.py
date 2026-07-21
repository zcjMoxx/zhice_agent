"""Workspace-configured Tool Hook Runtime."""

from pathlib import Path

from agent.hooks.config import HookConfigurationError
from agent.hooks.loader import load_hook_registry
from agent.hooks.runner import HookProcessRunner
from agent.hooks.runtime import ConfiguredHookRuntime


def create_hook_runtime(workspace: Path, config_dir: Path) -> ConfiguredHookRuntime:
    """Build the Hook Runtime from the workspace runtime configuration."""

    registry = load_hook_registry(Path(config_dir) / "hooks.yml", workspace=workspace)
    return ConfiguredHookRuntime(registry, HookProcessRunner(workspace))


__all__ = ["ConfiguredHookRuntime", "HookConfigurationError", "create_hook_runtime"]
