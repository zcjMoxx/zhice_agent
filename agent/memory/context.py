"""Helpers for constructing already-authorized Memory contexts."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from agent.protocols.memory import MemoryContext


def build_memory_context(
    memory_dir: Path | str,
    *,
    scope: Literal["workspace", "user"],
    actor_user_id: str | None,
) -> MemoryContext:
    """Return the fixed durable Memory path below one authorized root."""

    resolved = Path(memory_dir).expanduser().resolve()
    return MemoryContext(
        scope=scope,
        actor_user_id=actor_user_id,
        memory_dir=resolved,
        durable_file=resolved / "MEMORY.md",
    )
