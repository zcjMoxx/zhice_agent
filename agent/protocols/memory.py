"""Provider-neutral long-term Memory contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

MEMORY_CATEGORIES = ("profile", "preferences", "projects", "constraints", "decisions")


@dataclass(frozen=True)
class MemoryContext:
    """Already-authorized filesystem scope for one actor's Memory."""

    scope: Literal["workspace", "user"]
    actor_user_id: str | None
    memory_dir: Path
    durable_file: Path


@dataclass(frozen=True)
class MemoryEntry:
    """One minimal durable, user-visible Memory entry."""

    category: str
    content: str


@dataclass(frozen=True)
class MemoryExtractionCandidate:
    """One high-confidence durable fact proposed by the background extractor."""

    category: Literal["profile", "preferences", "constraints"]
    content: str
    evidence_turn_indexes: tuple[int, ...]


@dataclass(frozen=True)
class MemoryExtractionResult:
    """Outcome of one bounded background extraction pass."""

    reviewed_through_turn_index: int
    added: tuple[MemoryEntry, ...] = ()


class MemoryStore(Protocol):
    """Read and mutate one already-scoped Memory store."""

    def search(
        self,
        query: str = "",
        *,
        category: str = "",
        offset: int = 0,
        limit: int = 8,
    ) -> list[MemoryEntry]: ...

    def count(self, query: str = "", *, category: str = "") -> int: ...

    def add(
        self,
        category: str,
        content: str,
    ) -> MemoryEntry: ...

    def replace(
        self,
        category: str,
        old_content: str,
        content: str,
    ) -> MemoryEntry: ...

    def delete(self, category: str, content: str) -> bool: ...
