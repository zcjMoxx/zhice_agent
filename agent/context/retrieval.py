"""Hybrid lexical, semantic, entity, anchor, and recency Turn ranking."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from agent.context.config import RetrievalConfig
from agent.protocols.context import RetrievedTurn, SearchHit
from agent.protocols.llm import LLMProvider

_ENTITY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,}|[\u3400-\u9fff]{2,12}")
_ANCHOR_RE = re.compile(
    r"(?:[^\s]+\.(?:py|md|json|ya?ml|txt|log|js|ts|vue))|"
    r"\b[A-Z][A-Z0-9_]{2,}\b|\b(?:[45]\d\d|[A-Za-z]+Error)\b"
)
_REWRITE_MARKERS = ("它", "这个", "那个", "上述", "刚才", "继续", "it", "that", "those")
_RRF_K = 60


def rewrite_query_once(
    query: str,
    recent_user_text: Sequence[str],
    llm: LLMProvider | None,
    prompt: str,
) -> str:
    """Perform one bounded retrieval-only rewrite for clearly elliptical queries."""

    normalized = query.strip()
    if (
        llm is None
        or not prompt
        or not query_needs_recent_context(normalized)
    ):
        return ""
    payload = {"query": normalized, "recent_user_turns": list(recent_user_text)[-2:]}
    response = llm.chat(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        tools=None,
    )
    text = str(response.content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    raw = json.loads(text)
    rewritten = str(raw.get("query") or "").strip() if isinstance(raw, dict) else ""
    return rewritten[:4000] or normalized


def query_needs_recent_context(query: str) -> bool:
    """Return whether a short elliptical query needs recent user text for retrieval."""

    normalized = query.strip().casefold()
    return bool(
        normalized
        and len(normalized) < 80
        and any(marker in normalized for marker in _REWRITE_MARKERS)
    )


def fuse_hits(
    query: str,
    hits: Sequence[SearchHit],
    config: RetrievalConfig,
    *,
    max_turn_index: int,
) -> list[RetrievedTurn]:
    """Use weighted reciprocal-rank fusion and bounded exact/recency bonuses."""

    query_folded = query.casefold()
    query_entities = {match.group(0).casefold() for match in _ENTITY_RE.finditer(query)}
    query_anchors = {match.group(0).casefold() for match in _ANCHOR_RE.finditer(query)}
    ranked = []
    for hit in hits:
        semantic_ok = (
            hit.semantic_rank is not None
            and hit.semantic_score is not None
            and hit.semantic_score >= config.min_semantic_score
        )
        lexical_ok = hit.lexical_rank is not None
        document = hit.document
        entities = {value.casefold() for value in document.entities}
        anchors = {value.casefold() for value in document.anchors}
        entity_match = bool(query_entities & entities)
        anchor_match = bool(query_anchors & anchors)
        exact_text = bool(query_folded and query_folded in document.searchable_text.casefold())
        if not (semantic_ok or lexical_ok or entity_match or anchor_match or exact_text):
            continue
        score = 0.0
        if semantic_ok and hit.semantic_rank is not None:
            score += config.semantic_weight / (_RRF_K + hit.semantic_rank)
        if lexical_ok and hit.lexical_rank is not None:
            score += config.lexical_weight / (_RRF_K + hit.lexical_rank)
        if entity_match:
            score += config.entity_weight
        if anchor_match or exact_text:
            score += config.anchor_weight
        if max_turn_index > 0:
            recency_rank = max(1, max_turn_index - document.turn_index + 1)
            score += config.recency_weight / (_RRF_K + recency_rank)
        reasons = []
        if semantic_ok:
            reasons.append("semantic")
        if lexical_ok:
            reasons.append("lexical")
        if entity_match:
            reasons.append("entity_exact")
        if anchor_match or exact_text:
            reasons.append("anchor_exact")
        ranked.append(
            RetrievedTurn(
                turn_id=document.turn_id,
                turn_index=document.turn_index,
                final_score=round(score, 8),
                semantic_rank=hit.semantic_rank if semantic_ok else None,
                lexical_rank=hit.lexical_rank,
                entity_match=entity_match,
                anchor_match=anchor_match or exact_text,
                reason="+".join(reasons),
            )
        )
    return sorted(ranked, key=lambda item: (-item.final_score, -item.turn_index))[: config.top_k]
