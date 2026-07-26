"""Provider-neutral context planning, compaction, and search contracts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from agent.protocols.session import TurnGroup


@dataclass(frozen=True)
class RetrievedTurn:
    """One old complete Turn selected by hybrid retrieval."""

    turn_id: str
    turn_index: int
    final_score: float
    semantic_rank: int | None = None
    lexical_rank: int | None = None
    entity_match: bool = False
    anchor_match: bool = False
    reason: str = ""


@dataclass(frozen=True)
class ContextPlan:
    """Explainable immutable input selected for one LLM call."""

    mode: Literal["full", "history_query", "compacted_retrieval"]
    messages: tuple[dict[str, Any], ...]
    selected_turn_ids: tuple[str, ...]
    recent_turn_ids: tuple[str, ...]
    retrieved_turns: tuple[RetrievedTurn, ...] = ()
    compaction_id: str = ""
    compacted_through_turn_index: int = 0
    estimated_input_tokens: int = 0
    reason: str = ""
    candidate_turn_count: int = 0
    degraded: tuple[str, ...] = ()


@dataclass(frozen=True)
class TurnDocument:
    """Sanitized, rebuildable search representation of one complete Turn."""

    session_id: str
    turn_id: str
    turn_index: int
    user_text: str
    assistant_text: str
    tool_text: str
    entities: tuple[str, ...]
    anchors: tuple[str, ...]
    content_hash: str

    @property
    def searchable_text(self) -> str:
        return "\n".join(
            part for part in (self.user_text, self.assistant_text, self.tool_text) if part
        )


@dataclass(frozen=True)
class SearchHit:
    """Backend search evidence before hybrid rank fusion."""

    document: TurnDocument
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    semantic_score: float | None = None


@dataclass(frozen=True)
class CompactionRecord:
    """Versioned structured compaction derived from Session JSONL."""

    compaction_id: str
    session_id: str
    source_start_turn_index: int
    source_end_turn_index: int
    source_digest: str
    data: dict[str, Any]
    schema_version: int = 1


@dataclass(frozen=True)
class HistoryQueryResult:
    """Deterministic evidence for a natural-language Session history query."""

    plan_type: str
    total_user_turns: int
    matched_turn_ids: tuple[str, ...]
    matched_turn_indexes: tuple[int, ...]
    evidence: tuple[dict[str, Any], ...]
    direct_answer: str = ""
    truncated: bool = False


class TurnSearchIndex(Protocol):
    """User-isolated, rebuildable Turn search index."""

    def upsert(self, documents: Sequence[TurnDocument]) -> None: ...

    def search(
        self,
        session_id: str,
        query: str,
        *,
        query_embedding: Sequence[float] | None = None,
        provider_identity: str = "",
        top_k: int = 20,
    ) -> list[SearchHit]: ...

    def missing_embeddings(
        self, session_id: str, provider_identity: str
    ) -> list[TurnDocument]: ...

    def upsert_embeddings(
        self,
        documents: Sequence[TurnDocument],
        vectors: Sequence[Sequence[float]],
        provider_identity: str,
    ) -> None: ...

    def delete_session(self, session_id: str) -> None: ...

    def rebuild_session(self, session_id: str, documents: Sequence[TurnDocument]) -> None: ...


class ContextCompactor(Protocol):
    """Build strict structured state from completed Turns."""

    def compact(
        self,
        session_id: str,
        turns: Sequence[TurnGroup],
        *,
        previous: CompactionRecord | None = None,
    ) -> CompactionRecord: ...


@dataclass(frozen=True)
class ContextTrace:
    """Safe trace event emitted by context components."""

    event: str
    fields: dict[str, Any] = field(default_factory=dict)


class ContextTraceSink(Protocol):
    def emit(self, trace: ContextTrace) -> None: ...
