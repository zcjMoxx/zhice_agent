"""Application configuration and path resolution.

The first-stage runtime derives every important directory from a workspace root.
This keeps CLI usage simple and gives later modules one place to read paths.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    """Resolved filesystem layout for a Zhice-Agent process."""

    workspace: Path
    config_dir: Path
    prompts_dir: Path
    contexts_dir: Path
    sessions_dir: Path
    skills_dir: Path
    logs_dir: Path

    def ensure_dirs(self) -> None:
        """Create runtime directories that must exist before the CLI runs."""

        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        self.contexts_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)


def load_config(workspace: str | Path | None = None) -> AppConfig:
    """Load configuration from explicit input, environment variables, and defaults."""

    workspace_path = _resolve_path(
        workspace or os.getenv("ZHICE_AGENT_WORKSPACE") or _default_workspace()
    )
    config_dir = _resolve_path(os.getenv("ZHICE_AGENT_CONFIG_DIR"), workspace_path / "config")
    prompts_dir = _resolve_path(os.getenv("ZHICE_AGENT_PROMPTS_DIR"), workspace_path / "prompts")
    contexts_dir = _resolve_path(
        os.getenv("ZHICE_AGENT_CONTEXTS_DIR"), workspace_path / "contexts"
    )
    skills_dir = _resolve_path(os.getenv("ZHICE_AGENT_SKILLS_DIR"), workspace_path / "skills")
    logs_dir = _resolve_path(os.getenv("ZHICE_AGENT_LOGS_DIR"), workspace_path / "logs")

    return AppConfig(
        workspace=workspace_path,
        config_dir=config_dir,
        prompts_dir=prompts_dir,
        contexts_dir=contexts_dir,
        sessions_dir=contexts_dir / "sessions",
        skills_dir=skills_dir,
        logs_dir=logs_dir,
    )


def _default_workspace() -> Path:
    """Return the project root inferred from this module location."""

    return Path(__file__).resolve().parents[1]


def _resolve_path(value: str | Path | None, default: Path | None = None) -> Path:
    """Resolve a path-like value, falling back to a provided default."""

    selected = Path(value) if value else default
    if selected is None:
        raise ValueError("path value is required")
    return selected.expanduser().resolve()
