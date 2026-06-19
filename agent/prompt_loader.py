"""Markdown prompt loader.

Prompt files are UTF-8 Markdown documents under the configured prompts
directory. The loader refuses path traversal so prompts cannot escape that root.
"""

from __future__ import annotations

from pathlib import Path


class PromptNotFoundError(FileNotFoundError):
    """Raised when a requested prompt file does not exist."""


class PromptPathError(ValueError):
    """Raised when a prompt name would resolve outside the prompts directory."""


class PromptLoader:
    """Load prompt templates from a single prompts directory."""

    def __init__(self, prompts_dir: Path | str):
        """Resolve the prompt directory once so later path checks are stable."""

        self.prompts_dir = Path(prompts_dir).expanduser().resolve()

    def load(self, name: str) -> str:
        """Load one Markdown prompt by name, with or without the .md suffix."""

        path = self._resolve_prompt_path(name)
        if not path.exists():
            raise PromptNotFoundError(f"prompt not found: {name}")
        return path.read_text(encoding="utf-8")

    def load_many(self, names: list[str]) -> dict[str, str]:
        """Load multiple prompts and return them keyed by the requested name."""

        return {name: self.load(name) for name in names}

    def available_names(self) -> list[str]:
        """Return prompt filenames currently available to the CLI."""

        if not self.prompts_dir.exists():
            return []
        return sorted(path.name for path in self.prompts_dir.glob("*.md"))

    def _resolve_prompt_path(self, name: str) -> Path:
        """Resolve a prompt path while keeping it inside prompts_dir."""

        filename = name if name.endswith(".md") else f"{name}.md"
        candidate = (self.prompts_dir / filename).resolve()
        if candidate.parent != self.prompts_dir:
            raise PromptPathError(f"prompt path escapes prompts directory: {name}")
        return candidate
