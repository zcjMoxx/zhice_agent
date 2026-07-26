"""Safe defaults and YAML loading for Session context engineering."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.runtime_config import load_runtime_section


@dataclass(frozen=True)
class FullHistoryConfig:
    enabled: bool = True
    turn_growth_reserve_tokens: int = 4096
    turn_growth_reserve_ratio: float = 0.15


@dataclass(frozen=True)
class HistoryQueryConfig:
    enabled: bool = True
    planner_fallback: bool = True


@dataclass(frozen=True)
class CompactionConfig:
    enabled: bool = True
    trigger_budget_ratio: float = 0.85
    recent_keep_ratio: float = 0.15
    post_compaction_max_ratio: float = 0.35
    min_recent_turns: int = 8
    background_enabled: bool = True
    background_trigger_budget_ratio: float = 0.80


@dataclass(frozen=True)
class RetrievalConfig:
    enabled: bool = True
    top_k: int = 6
    semantic_weight: float = 0.45
    lexical_weight: float = 0.30
    entity_weight: float = 0.15
    anchor_weight: float = 0.08
    recency_weight: float = 0.02
    min_semantic_score: float = 0.15


@dataclass(frozen=True)
class IndexConfig:
    backend: str = "sqlite"


@dataclass(frozen=True)
class ContextEngineeringConfig:
    full_history: FullHistoryConfig = field(default_factory=FullHistoryConfig)
    history_query: HistoryQueryConfig = field(default_factory=HistoryQueryConfig)
    compaction: CompactionConfig = field(default_factory=CompactionConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    index: IndexConfig = field(default_factory=IndexConfig)


def load_context_config(config_dir: Path | str) -> ContextEngineeringConfig:
    """Load config.yml context, using safe defaults when it is absent."""

    raw = load_runtime_section(config_dir, "context", default={})
    if not isinstance(raw, dict):
        raise ValueError("config.yml context must contain a mapping")
    if not raw:
        return ContextEngineeringConfig()
    root = raw
    return ContextEngineeringConfig(
        full_history=_section(FullHistoryConfig, root.get("full_history")),
        history_query=_section(HistoryQueryConfig, root.get("history_query")),
        compaction=_section(CompactionConfig, root.get("compaction")),
        retrieval=_section(RetrievalConfig, root.get("retrieval")),
        index=_section(IndexConfig, root.get("index")),
    )


def _section(cls, value: Any):
    if value is None:
        return cls()
    if not isinstance(value, dict):
        raise ValueError(f"config.yml context section {cls.__name__} must be a mapping")
    allowed = set(cls.__dataclass_fields__)
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown context config fields: {sorted(unknown)}")
    result = cls(**value)
    _validate(result)
    return result


def _validate(value: object) -> None:
    for name, item in vars(value).items():
        if name.endswith("ratio") or name.endswith("weight") or name == "min_semantic_score":
            if not 0 <= float(item) <= 1:
                raise ValueError(f"context config {name} must be between 0 and 1")
        if name in {"turn_growth_reserve_tokens", "min_recent_turns", "top_k"} and int(item) < 0:
            raise ValueError(f"context config {name} must be non-negative")
    if isinstance(value, IndexConfig) and value.backend != "sqlite":
        raise ValueError("only context index backend 'sqlite' is supported")
    if isinstance(value, CompactionConfig):
        if value.recent_keep_ratio >= value.trigger_budget_ratio:
            raise ValueError(
                "context compaction recent_keep_ratio must be below trigger_budget_ratio"
            )
        if value.post_compaction_max_ratio >= value.trigger_budget_ratio:
            raise ValueError(
                "context compaction post_compaction_max_ratio must be below "
                "trigger_budget_ratio"
            )
        if value.recent_keep_ratio > value.post_compaction_max_ratio:
            raise ValueError(
                "context compaction recent_keep_ratio must not exceed "
                "post_compaction_max_ratio"
            )
        if not (
            value.post_compaction_max_ratio
            < value.background_trigger_budget_ratio
            < value.trigger_budget_ratio
        ):
            raise ValueError(
                "context compaction background_trigger_budget_ratio must be between "
                "post_compaction_max_ratio and trigger_budget_ratio"
            )
