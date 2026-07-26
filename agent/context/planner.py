"""Budget-first full Session context planning and long-history fallback."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from agent.context.compaction import (
    CompactionStore,
    LLMContextCompactor,
    format_compaction_evidence,
    validate_compaction,
)
from agent.context.config import ContextEngineeringConfig
from agent.context.history_query import (
    LLMHistoryQueryPlanner,
    SessionHistoryQueryResolver,
    format_history_evidence,
    looks_like_history_query,
)
from agent.context.index import SQLiteTurnSearchIndex
from agent.context.retrieval import fuse_hits, query_needs_recent_context, rewrite_query_once
from agent.context.turn_document import build_turn_documents
from agent.logging_utils import log_event
from agent.protocols.context import ContextPlan, RetrievedTurn
from agent.protocols.embedding import EmbeddingProvider
from agent.protocols.llm import ContextBudget, LLMProvider
from agent.protocols.session import TurnGroup

context_logger = logging.getLogger("zcagent.agent.context")


class ContextPlanner:
    """Choose complete history when possible and derived context only when necessary."""

    def __init__(
        self,
        config: ContextEngineeringConfig | None = None,
        *,
        embedding_provider: EmbeddingProvider | None = None,
    ):
        self.config = config or ContextEngineeringConfig()
        self.embedding_provider = embedding_provider
        self.history_queries = SessionHistoryQueryResolver()

    def plan(
        self,
        *,
        session_id: str,
        turns: Sequence[TurnGroup],
        system_message: dict[str, Any],
        current_user: dict[str, Any],
        history_to_messages: Callable[[list], list[dict[str, Any]]],
        estimate_tokens: Callable[..., int],
        context_budget: ContextBudget | None,
        tool_definitions: list[dict[str, Any]] | None,
        context_root: Path | None,
        llm: LLMProvider | None,
        compaction_llm: LLMProvider | None = None,
        compaction_phase: str = "foreground",
        compaction_prompt: str = "",
        history_query_prompt: str = "",
        query_rewrite_prompt: str = "",
    ) -> ContextPlan:
        started = time.perf_counter()
        full_history = history_to_messages(
            [message for turn in turns for message in turn.messages]
        )
        full_messages = [system_message, *full_history, current_user]
        full_tokens = estimate_tokens(full_messages, tool_definitions=tool_definitions)
        limit = context_budget.input_token_limit if context_budget is not None else None
        safe_limit = _safe_limit(limit, self.config)
        history_result = (
            self.history_queries.resolve(str(current_user.get("content") or ""), turns)
            if self.config.history_query.enabled
            else None
        )
        if (
            history_result is None
            and self.config.history_query.enabled
            and self.config.history_query.planner_fallback
            and looks_like_history_query(str(current_user.get("content") or ""))
            and llm is not None
            and history_query_prompt
        ):
            try:
                query_plan = LLMHistoryQueryPlanner(llm, history_query_prompt).plan(
                    str(current_user.get("content") or "")
                )
                if query_plan is not None:
                    history_result = self.history_queries.execute_plan(query_plan, turns)
            except Exception as exc:  # noqa: BLE001 - fallback must not block chat.
                log_event(
                    context_logger,
                    logging.WARNING,
                    "context.history_query.ambiguous",
                    session_id=session_id,
                    error_type=type(exc).__name__,
                )
        if history_result is not None:
            log_event(
                context_logger,
                logging.DEBUG,
                "context.history_query.resolved",
                session_id=session_id,
                plan_type=history_result.plan_type,
                matched_turns=len(history_result.matched_turn_ids),
            )
        evidence_message = (
            {"role": "system", "content": format_history_evidence(history_result)}
            if history_result is not None
            else None
        )
        if evidence_message is not None:
            full_messages = [system_message, evidence_message, *full_history, current_user]
            full_tokens = estimate_tokens(full_messages, tool_definitions=tool_definitions)

        if self.config.full_history.enabled and (safe_limit is None or full_tokens <= safe_limit):
            mode = "history_query" if history_result is not None else "full"
            plan = ContextPlan(
                mode=mode,
                messages=tuple(full_messages),
                selected_turn_ids=tuple(turn.turn_id for turn in turns),
                recent_turn_ids=tuple(turn.turn_id for turn in turns),
                estimated_input_tokens=full_tokens,
                reason="session_history_query" if history_result else "full_history_fits",
                candidate_turn_count=len(turns),
            )
            self._trace_selection(session_id, plan, turns, limit, started)
            return plan

        plan = self._plan_long(
            session_id=session_id,
            turns=turns,
            system_message=system_message,
            current_user=current_user,
            evidence_message=evidence_message,
            history_to_messages=history_to_messages,
            estimate_tokens=estimate_tokens,
            context_budget=context_budget,
            tool_definitions=tool_definitions,
            context_root=context_root,
            llm=llm,
            compaction_llm=compaction_llm,
            compaction_phase=compaction_phase,
            compaction_prompt=compaction_prompt,
            query_rewrite_prompt=query_rewrite_prompt,
        )
        self._trace_selection(session_id, plan, turns, limit, started)
        return plan

    def _plan_long(
        self,
        *,
        session_id: str,
        turns: Sequence[TurnGroup],
        system_message: dict[str, Any],
        current_user: dict[str, Any],
        evidence_message: dict[str, Any] | None,
        history_to_messages,
        estimate_tokens,
        context_budget,
        tool_definitions,
        context_root,
        llm,
        compaction_llm,
        compaction_phase,
        compaction_prompt,
        query_rewrite_prompt,
    ) -> ContextPlan:
        degraded: list[str] = []
        if self.embedding_provider is None and self.config.retrieval.enabled:
            degraded.append("embedding_unconfigured")
        limit = context_budget.input_token_limit if context_budget is not None else 131072
        recent_budget = max(1, int(limit * self.config.compaction.recent_keep_ratio))
        post_compaction_max = max(
            1,
            int(limit * self.config.compaction.post_compaction_max_ratio),
        )
        trigger = max(1, int(limit * self.config.compaction.trigger_budget_ratio))
        fixed = [system_message, *([evidence_message] if evidence_message else []), current_user]
        fixed_tokens = estimate_tokens(fixed, tool_definitions=tool_definitions)
        compaction_available = bool(
            self.config.compaction.enabled
            and context_root
            and (compaction_llm or llm)
            and compaction_prompt
        )
        derived_messages: list[dict[str, Any]] = []
        compaction_id = ""
        compacted_through = 0
        retrieved: list[RetrievedTurn] = []

        store = CompactionStore(context_root / "compactions") if context_root else None
        record = (
            store.load(session_id)
            if store and self.config.compaction.enabled
            else None
        )
        if record is not None:
            previous_source = [
                turn
                for turn in turns
                if (turn.turn_index or 0) <= record.source_end_turn_index
            ]
            if not validate_compaction(record, previous_source):
                store.delete(session_id)
                record = None
                log_event(context_logger, logging.INFO, "context.compaction.invalidated", session_id=session_id)

        reuse_record = False
        if record is not None:
            uncompacted_tail = [
                turn
                for turn in turns
                if (turn.turn_index or 0) > record.source_end_turn_index
            ]
            tail_messages = history_to_messages(
                [message for turn in uncompacted_tail for message in turn.messages]
            )
            reuse_messages = [
                system_message,
                {"role": "system", "content": format_compaction_evidence(record)},
                *([evidence_message] if evidence_message else []),
                *tail_messages,
                current_user,
            ]
            if estimate_tokens(reuse_messages, tool_definitions=tool_definitions) <= trigger:
                reuse_record = True
                recent = uncompacted_tail
                old_turns = list(turns[: len(turns) - len(recent)])
                log_event(
                    context_logger,
                    logging.DEBUG,
                    "context.compaction.reused",
                    session_id=session_id,
                    compacted_through=record.source_end_turn_index,
                    uncompacted_turns=len(uncompacted_tail),
                    trigger_budget_ratio=self.config.compaction.trigger_budget_ratio,
                )

        if not reuse_record:
            planned_recent_budget, compaction_reserve = _planned_recent_budget(
                recent_budget=recent_budget,
                post_compaction_max=post_compaction_max,
                fixed_tokens=fixed_tokens,
                record=record,
                estimate_tokens=estimate_tokens,
            )
            recent = _select_recent_turns(
                turns,
                recent_budget=(
                    planned_recent_budget
                    if compaction_available
                    else max(1, limit - fixed_tokens)
                ),
                min_recent_turns=self.config.compaction.min_recent_turns,
                history_to_messages=history_to_messages,
                estimate_tokens=estimate_tokens,
            )
            old_turns = list(turns[: len(turns) - len(recent)])
            if compaction_available:
                log_event(
                    context_logger,
                    logging.DEBUG,
                    "context.compaction.budget_planned",
                    session_id=session_id,
                    recent_budget=planned_recent_budget,
                    summary_reserve_tokens=compaction_reserve,
                    post_compaction_max_tokens=post_compaction_max,
                )

        compactor = (
            LLMContextCompactor(
                compaction_llm or llm,
                compaction_prompt,
                phase=compaction_phase,
            )
            if self.config.compaction.enabled
            and store
            and (compaction_llm or llm)
            and compaction_prompt
            else None
        )
        if old_turns and compactor is not None:
            if (
                not reuse_record
                and (record is None or record.source_end_turn_index < (old_turns[-1].turn_index or 0))
            ):
                log_event(context_logger, logging.DEBUG, "context.compaction.start", session_id=session_id, source_turns=len(old_turns))
                try:
                    for covered_turns in _incremental_compaction_prefixes(old_turns, record):
                        record = compactor.compact(
                            session_id,
                            covered_turns,
                            previous=record,
                        )
                        store.save(record)
                    log_event(context_logger, logging.DEBUG, "context.compaction.done", session_id=session_id, compacted_through=record.source_end_turn_index)
                except Exception as exc:  # noqa: BLE001 - long chat must degrade.
                    degraded.append("compaction")
                    log_event(context_logger, logging.WARNING, "context.compaction.failed", session_id=session_id, error_type=type(exc).__name__)

        if record is not None and not reuse_record and compactor is not None:
            base_tokens = _compacted_base_tokens(
                recent,
                fixed_messages=fixed,
                record=record,
                history_to_messages=history_to_messages,
                estimate_tokens=estimate_tokens,
                tool_definitions=tool_definitions,
            )
            if base_tokens > post_compaction_max:
                log_event(
                    context_logger,
                    logging.INFO,
                    "context.compaction.low_watermark_missed",
                    session_id=session_id,
                    estimated_tokens=base_tokens,
                    target_tokens=post_compaction_max,
                    trigger_tokens=trigger,
                )

            # The low watermark is a latency/quality target, not a reason to
            # immediately pay for another LLM call. Expand compaction coverage
            # only when the just-created reusable state still exceeds the
            # trigger safety waterline.
            while base_tokens > trigger and recent:
                fitted_recent = _fit_recent_after_compaction(
                    recent,
                    fixed_messages=fixed,
                    compaction_message={
                        "role": "system",
                        "content": format_compaction_evidence(record),
                    },
                    post_compaction_max=trigger,
                    history_to_messages=history_to_messages,
                    estimate_tokens=estimate_tokens,
                    tool_definitions=tool_definitions,
                )
                if len(fitted_recent) == len(recent):
                    break
                expanded_old_turns = list(turns[: len(turns) - len(fitted_recent)])
                try:
                    for covered_turns in _incremental_compaction_prefixes(
                        expanded_old_turns,
                        record,
                    ):
                        record = compactor.compact(
                            session_id,
                            covered_turns,
                            previous=record,
                        )
                        store.save(record)
                except Exception as exc:  # noqa: BLE001 - keep the covered tail raw.
                    degraded.append("compaction")
                    log_event(
                        context_logger,
                        logging.WARNING,
                        "context.compaction.failed",
                        session_id=session_id,
                        error_type=type(exc).__name__,
                    )
                    break
                recent = fitted_recent
                old_turns = expanded_old_turns
                base_tokens = _compacted_base_tokens(
                    recent,
                    fixed_messages=fixed,
                    record=record,
                    history_to_messages=history_to_messages,
                    estimate_tokens=estimate_tokens,
                    tool_definitions=tool_definitions,
                )

        if record is not None:
            derived_messages.append(
                {"role": "system", "content": format_compaction_evidence(record)}
            )
            compaction_id = record.compaction_id
            compacted_through = record.source_end_turn_index

        index = None
        if context_root and self.config.retrieval.enabled:
            try:
                index = SQLiteTurnSearchIndex(context_root / "context_index.sqlite3")
                if index.recovered:
                    degraded.append("index_rebuilt")
                    log_event(context_logger, logging.WARNING, "context.index.rebuilt", session_id=session_id)
                documents = build_turn_documents(session_id, turns)
                index.upsert(documents)
                log_event(context_logger, logging.DEBUG, "context.index.lexical_upserted", session_id=session_id, turn_count=len(documents))
                if self.embedding_provider is not None:
                    self._backfill_embeddings(index, session_id, degraded)
                query = _retrieval_query(str(current_user.get("content") or ""), recent)
                recent_users = [
                    message.content
                    for turn in recent[-2:]
                    for message in turn.messages
                    if message.role == "user"
                ]
                try:
                    query = rewrite_query_once(
                        str(current_user.get("content") or ""),
                        recent_users,
                        llm,
                        query_rewrite_prompt,
                    ) or query
                except Exception as exc:  # noqa: BLE001 - original query remains valid.
                    degraded.append("query_rewrite")
                    log_event(context_logger, logging.WARNING, "context.retrieval.rewrite_failed", session_id=session_id, error_type=type(exc).__name__)
                query_vector = None
                if self.embedding_provider is not None:
                    try:
                        query_vector = self.embedding_provider.embed([query])[0]
                    except Exception as exc:  # noqa: BLE001
                        degraded.append("embedding_query")
                        log_event(context_logger, logging.WARNING, "context.index.embedding_failed", session_id=session_id, error_type=type(exc).__name__)
                hits = index.search(
                    session_id,
                    query,
                    query_embedding=query_vector,
                    provider_identity=(self.embedding_provider.identity if self.embedding_provider else ""),
                    top_k=max(20, self.config.retrieval.top_k * 4),
                )
                old_ids = {turn.turn_id for turn in old_turns}
                retrieved = [
                    item
                    for item in fuse_hits(
                        query,
                        hits,
                        self.config.retrieval,
                        max_turn_index=max((turn.turn_index or 0 for turn in turns), default=0),
                    )
                    if item.turn_id in old_ids
                ]
                log_event(context_logger, logging.DEBUG, "context.retrieval.done", session_id=session_id, retrieved_turns=len(retrieved), degraded=bool(degraded))
            except Exception as exc:  # noqa: BLE001 - raw recent context remains usable.
                degraded.append("index")
                log_event(context_logger, logging.WARNING, "context.index.failed", session_id=session_id, error_type=type(exc).__name__)

        turn_by_id = {turn.turn_id: turn for turn in old_turns}
        retrieved_turns = sorted(
            (turn_by_id[item.turn_id] for item in retrieved if item.turn_id in turn_by_id),
            key=lambda turn: turn.turn_index or 0,
        )
        retrieved_messages = history_to_messages(
            [message for turn in retrieved_turns for message in turn.messages]
        )
        recent_messages = history_to_messages(
            [message for turn in recent for message in turn.messages]
        )
        messages = [
            system_message,
            *derived_messages,
            *([evidence_message] if evidence_message else []),
            *retrieved_messages,
            *recent_messages,
            current_user,
        ]
        selected = [*retrieved_turns, *recent]
        return ContextPlan(
            mode="history_query" if evidence_message else "compacted_retrieval",
            messages=tuple(messages),
            selected_turn_ids=tuple(turn.turn_id for turn in selected),
            recent_turn_ids=tuple(turn.turn_id for turn in recent),
            retrieved_turns=tuple(retrieved),
            compaction_id=compaction_id,
            compacted_through_turn_index=compacted_through,
            estimated_input_tokens=estimate_tokens(messages, tool_definitions=tool_definitions),
            reason="session_history_query" if evidence_message else "history_exceeds_safe_limit",
            candidate_turn_count=len(turns),
            degraded=tuple(dict.fromkeys(degraded)),
        )

    def _backfill_embeddings(self, index, session_id: str, degraded: list[str]) -> None:
        provider = self.embedding_provider
        if provider is None:
            return
        missing = index.missing_embeddings(session_id, provider.identity)
        batch_size = max(1, provider.batch_size)
        for offset in range(0, len(missing), batch_size):
            batch = missing[offset : offset + batch_size]
            try:
                vectors = provider.embed([document.searchable_text for document in batch])
                index.upsert_embeddings(batch, vectors, provider.identity)
                log_event(context_logger, logging.DEBUG, "context.index.embedding_upserted", session_id=session_id, turn_count=len(batch))
            except Exception as exc:  # noqa: BLE001
                degraded.append("embedding_backfill")
                log_event(context_logger, logging.WARNING, "context.index.embedding_failed", session_id=session_id, error_type=type(exc).__name__)
                break

    def delete_session(self, context_root: Path | None, session_id: str) -> None:
        if context_root is None:
            return
        CompactionStore(context_root / "compactions").delete(session_id)
        try:
            SQLiteTurnSearchIndex(context_root / "context_index.sqlite3").delete_session(session_id)
        except Exception as exc:  # noqa: BLE001
            log_event(context_logger, logging.WARNING, "context.index.delete_failed", session_id=session_id, error_type=type(exc).__name__)

    def _trace_selection(self, session_id, plan, turns, limit, started) -> None:
        retrieved = ",".join(
            f"{item.turn_index}:{item.reason}:{item.final_score:.4f}"
            for item in plan.retrieved_turns
        )
        log_event(
            context_logger,
            logging.DEBUG,
            "context.selection",
            session_id=session_id,
            mode=plan.mode,
            candidate_turn_count=len(turns),
            selected_turn_indexes=",".join(
                str(turn.turn_index or 0) for turn in turns if turn.turn_id in plan.selected_turn_ids
            ),
            recent_turn_indexes=",".join(
                str(turn.turn_index or 0) for turn in turns if turn.turn_id in plan.recent_turn_ids
            ),
            retrieved=retrieved,
            compaction_id=plan.compaction_id,
            compacted_through_turn_index=plan.compacted_through_turn_index,
            estimated_input_tokens=plan.estimated_input_tokens,
            input_token_limit=limit or 0,
            compaction_trigger_ratio=self.config.compaction.trigger_budget_ratio,
            recent_keep_ratio=self.config.compaction.recent_keep_ratio,
            post_compaction_max_ratio=self.config.compaction.post_compaction_max_ratio,
            reason=plan.reason,
            degraded=",".join(plan.degraded),
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )


def context_root_for_session_store(session_store: object | None) -> Path | None:
    """Derive the isolated context directory from an already-authorized store."""

    sessions_dir = getattr(session_store, "sessions_dir", None)
    if sessions_dir is None:
        return None
    sessions_path = Path(sessions_dir).expanduser().resolve()
    return sessions_path.parent / "context"


def _safe_limit(limit: int | None, config: ContextEngineeringConfig) -> int | None:
    if limit is None:
        return None
    return max(1, int(limit * config.compaction.trigger_budget_ratio))


def _select_recent_turns(
    turns: Sequence[TurnGroup],
    *,
    recent_budget: int,
    min_recent_turns: int,
    history_to_messages,
    estimate_tokens,
) -> list[TurnGroup]:
    """Keep a bounded raw tail, treating the minimum Turn count as a preference."""

    recent: list[TurnGroup] = []
    for turn in reversed(turns):
        candidate = [turn, *recent]
        candidate_messages = history_to_messages(
            [message for item in candidate for message in item.messages]
        )
        candidate_tokens = estimate_tokens(candidate_messages)
        if candidate_tokens <= recent_budget:
            recent = candidate
        elif not recent and min_recent_turns > 0:
            # Keep one complete Turn as a best-effort continuity floor. The
            # post-compaction cap and ContextBuilder's hard budget may still
            # remove it when a single Turn itself is too large.
            recent = candidate
            break
        else:
            break
    return recent


def _planned_recent_budget(
    *,
    recent_budget: int,
    post_compaction_max: int,
    fixed_tokens: int,
    record,
    estimate_tokens,
) -> tuple[int, int]:
    """Reserve summary space before selecting the raw/compacted boundary."""

    if record is None:
        summary_reserve = recent_budget
    else:
        current_summary_tokens = estimate_tokens(
            [{"role": "system", "content": format_compaction_evidence(record)}]
        )
        summary_reserve = max(
            recent_budget,
            math.ceil(current_summary_tokens * 1.20),
        )
    available = max(0, post_compaction_max - fixed_tokens - summary_reserve)
    return min(recent_budget, available), summary_reserve


def _compacted_base_tokens(
    recent: Sequence[TurnGroup],
    *,
    fixed_messages: Sequence[dict[str, Any]],
    record,
    history_to_messages,
    estimate_tokens,
    tool_definitions,
) -> int:
    recent_messages = history_to_messages(
        [message for turn in recent for message in turn.messages]
    )
    return estimate_tokens(
        [
            fixed_messages[0],
            {"role": "system", "content": format_compaction_evidence(record)},
            *fixed_messages[1:],
            *recent_messages,
        ],
        tool_definitions=tool_definitions,
    )


def _fit_recent_after_compaction(
    recent: Sequence[TurnGroup],
    *,
    fixed_messages: Sequence[dict[str, Any]],
    compaction_message: dict[str, Any],
    post_compaction_max: int,
    history_to_messages,
    estimate_tokens,
    tool_definitions,
) -> list[TurnGroup]:
    """Shrink the raw tail until the reusable base state reaches its low waterline."""

    fitted = list(recent)
    while fitted:
        recent_messages = history_to_messages(
            [message for turn in fitted for message in turn.messages]
        )
        base_messages = [
            fixed_messages[0],
            compaction_message,
            *fixed_messages[1:],
            *recent_messages,
        ]
        if (
            estimate_tokens(base_messages, tool_definitions=tool_definitions)
            <= post_compaction_max
        ):
            break
        fitted.pop(0)
    return fitted


def _retrieval_query(query: str, recent: Sequence[TurnGroup]) -> str:
    normalized = query.strip()
    if not recent or not query_needs_recent_context(normalized):
        return normalized
    prior_users = [
        message.content
        for turn in recent[-2:]
        for message in turn.messages
        if message.role == "user"
    ]
    return "\n".join([*prior_users[-2:], normalized])[:4000]


def _incremental_compaction_prefixes(
    turns: Sequence[TurnGroup],
    previous,
) -> list[list[TurnGroup]]:
    """Return bounded growing prefixes so each LLM call receives only a small delta."""

    completed = previous.source_end_turn_index if previous is not None else 0
    start = next(
        (index for index, turn in enumerate(turns) if (turn.turn_index or 0) > completed),
        len(turns),
    )
    prefixes: list[list[TurnGroup]] = []
    cursor = start
    while cursor < len(turns):
        end = cursor
        characters = 0
        while end < len(turns) and end - cursor < 32:
            turn_chars = sum(len(message.content) for message in turns[end].messages)
            if end > cursor and characters + turn_chars > 60000:
                break
            characters += turn_chars
            end += 1
        prefixes.append(list(turns[:end]))
        cursor = end
    return prefixes
