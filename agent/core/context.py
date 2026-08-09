"""Build LLM message context for one Agent turn."""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import warnings
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent.context.compaction import (
    CompactionStore,
    format_compaction_evidence,
    validate_compaction,
)
from agent.context.config import ContextEngineeringConfig
from agent.context.index import SQLiteTurnSearchIndex
from agent.context.planner import ContextPlanner, context_root_for_session_store
from agent.context.turn_document import build_turn_documents
from agent.core.context_relevance import select_relevant_turns
from agent.core.turns import group_messages_by_turn
from agent.logging_utils import log_event
from agent.message import Message
from agent.prompt_loader import PromptLoader, PromptNotFoundError
from agent.protocols.llm import ContextBudget, LLMContextBudgetError
from agent.protocols.skill import SkillProvider

DEFAULT_CONTEXT_PROMPTS = ["identity", "tool_use_policy", "skills_intro"]
DEFAULT_ALWAYS_INCLUDE_RECENT_TURNS = 3
DEFAULT_MAX_RELEVANT_TURNS = 3

_CJK_CHAR_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_MESSAGE_TOKEN_OVERHEAD = 4
_TOOL_CONTENT_MIN_CHARS = 128
context_logger = logging.getLogger("zcagent.agent.context")


class ContextBuilder:
    """Assemble system prompt, recent history, and the current user message."""

    def __init__(
        self,
        prompt_loader: PromptLoader,
        skills: SkillProvider | None = None,
        max_history_turns: int | None = 50,
        max_relevant_turns: int = DEFAULT_MAX_RELEVANT_TURNS,
        always_include_recent_turns: int = DEFAULT_ALWAYS_INCLUDE_RECENT_TURNS,
        max_history_messages: int = 60,
        max_message_chars: int = 8000,
        max_skill_summaries: int = 50,
        max_skill_summary_chars: int = 5000,
        extra_system_prompts: tuple[str, ...] = (),
        context_config: ContextEngineeringConfig | None = None,
        embedding_provider=None,
        compaction_llm_provider=None,
    ):
        """Configure prompt source and history/message size limits."""

        if max_history_turns is not None and max_history_turns < 0:
            raise ValueError("max_history_turns must be non-negative or None")
        if max_relevant_turns < 0:
            raise ValueError("max_relevant_turns must be non-negative")
        if always_include_recent_turns < 0:
            raise ValueError("always_include_recent_turns must be non-negative")
        if max_history_messages < 0:
            raise ValueError("max_history_messages must be non-negative")
        if max_message_chars <= 0:
            raise ValueError("max_message_chars must be positive")
        if max_skill_summaries < 0:
            raise ValueError("max_skill_summaries must be non-negative")
        if max_skill_summary_chars <= 0:
            raise ValueError("max_skill_summary_chars must be positive")

        self.prompt_loader = prompt_loader
        self.skills = skills
        self.max_history_turns = max_history_turns
        self.max_relevant_turns = max_relevant_turns
        self.always_include_recent_turns = always_include_recent_turns
        self.max_history_messages = max_history_messages
        self.max_message_chars = max_message_chars
        self.max_skill_summaries = max_skill_summaries
        self.max_skill_summary_chars = max_skill_summary_chars
        self.extra_system_prompts = tuple(extra_system_prompts)
        if (
            max_history_turns != 50
            or max_relevant_turns != DEFAULT_MAX_RELEVANT_TURNS
            or always_include_recent_turns != DEFAULT_ALWAYS_INCLUDE_RECENT_TURNS
            or max_history_messages != 60
        ):
            warnings.warn(
                "Fixed history count settings are deprecated and no longer remove budget-fitting Session Turns.",
                DeprecationWarning,
                stacklevel=2,
            )
        self.context_planner = ContextPlanner(
            context_config or ContextEngineeringConfig(),
            embedding_provider=embedding_provider,
        )
        self.compaction_llm_provider = compaction_llm_provider
        self._background_lock = threading.Lock()
        self._background_futures: dict[tuple[str, str], Future] = {}
        self._background_states: dict[tuple[str, str], dict[str, Any]] = {}
        self.last_plan = None
        self.supports_context_engineering = True

    def build(
        self,
        history: list[Message],
        user_message: Message,
        workspace: Path,
        session_id: str,
        context_budget: ContextBudget | None = None,
        tool_definitions: list[dict[str, Any]] | None = None,
        session_store: object | None = None,
        llm_provider: object | None = None,
        skills_override: SkillProvider | None = None,
    ) -> list[dict[str, Any]]:
        """Return budget-first full-Session messages for one chat turn."""

        system_message = {
            "role": "system",
            "content": self._build_system_prompt(
                workspace=workspace,
                session_id=session_id,
                skills_override=skills_override,
            ),
        }
        current_user = self._message_to_llm_dict(user_message)
        if current_user is None or current_user["role"] != "user":
            raise ValueError("user_message must have role 'user'")
        try:
            compaction_prompt = self.prompt_loader.load("context_compaction").strip()
        except PromptNotFoundError:
            compaction_prompt = ""
        try:
            history_query_prompt = self.prompt_loader.load("history_query_planner").strip()
        except PromptNotFoundError:
            history_query_prompt = ""
        try:
            query_rewrite_prompt = self.prompt_loader.load("context_query_rewrite").strip()
        except PromptNotFoundError:
            query_rewrite_prompt = ""
        projected_full_tokens = estimate_llm_tokens(
            [system_message, *self._history_to_llm_dicts(history), current_user],
            tool_definitions=tool_definitions,
        )
        self._wait_for_background_if_needed(
            session_store,
            session_id,
            projected_full_tokens,
            context_budget,
        )
        self._remember_background_state(
            session_store=session_store,
            session_id=session_id,
            workspace=workspace,
            context_budget=context_budget,
            tool_definitions=tool_definitions,
            compaction_llm=self.compaction_llm_provider or llm_provider,
        )
        self.last_plan = self.context_planner.plan(
            session_id=session_id,
            turns=group_messages_by_turn(history),
            system_message=system_message,
            current_user=current_user,
            history_to_messages=self._history_to_llm_dicts,
            estimate_tokens=estimate_llm_tokens,
            context_budget=context_budget,
            tool_definitions=tool_definitions,
            context_root=context_root_for_session_store(session_store),
            llm=llm_provider,
            compaction_llm=self.compaction_llm_provider or llm_provider,
            compaction_prompt=compaction_prompt,
            history_query_prompt=history_query_prompt,
            query_rewrite_prompt=query_rewrite_prompt,
        )
        return self.fit_messages(
            list(self.last_plan.messages),
            tool_definitions=tool_definitions,
            context_budget=context_budget,
        )

    def on_turn_committed(
        self,
        session_store: object,
        session_id: str,
        messages: list[Message],
    ) -> None:
        """Synchronously make a committed Turn available to lexical retrieval."""

        context_root = context_root_for_session_store(session_store)
        if context_root is None:
            return
        documents = build_turn_documents(session_id, group_messages_by_turn(messages))
        if documents:
            SQLiteTurnSearchIndex(context_root / "context_index.sqlite3").upsert(documents)
        self._maybe_schedule_background(session_store, session_id)

    def delete_derived_session(self, session_store: object, session_id: str) -> None:
        """Invalidate compaction and index state for clear/delete lifecycle."""

        self.context_planner.delete_session(
            context_root_for_session_store(session_store),
            session_id,
        )
        key = _background_session_key(session_store, session_id)
        with self._background_lock:
            future = self._background_futures.pop(key, None)
            self._background_states.pop(key, None)
        if future is not None:
            future.cancel()

    def _remember_background_state(
        self,
        *,
        session_store,
        session_id: str,
        workspace: Path,
        context_budget: ContextBudget | None,
        tool_definitions,
        compaction_llm,
    ) -> None:
        if (
            not self.context_planner.config.compaction.background_enabled
            or context_budget is None
            or session_store is None
            or compaction_llm is None
        ):
            return
        key = _background_session_key(session_store, session_id)
        with self._background_lock:
            self._background_states[key] = {
                "session_store": session_store,
                "workspace": Path(workspace),
                "context_budget": context_budget,
                "tool_definitions": list(tool_definitions or []),
                "compaction_llm": compaction_llm,
            }

    def _wait_for_background_if_needed(
        self,
        session_store,
        session_id: str,
        projected_full_tokens: int,
        context_budget: ContextBudget | None,
    ) -> None:
        if context_budget is None or session_store is None:
            return
        trigger = int(
            context_budget.input_token_limit
            * self.context_planner.config.compaction.trigger_budget_ratio
        )
        if projected_full_tokens < trigger:
            return
        key = _background_session_key(session_store, session_id)
        with self._background_lock:
            future = self._background_futures.get(key)
        if future is None:
            return
        log_event(
            context_logger,
            logging.INFO,
            "context.compaction.background_waited",
            session_id=session_id,
        )
        try:
            future.result()
        except Exception as exc:  # noqa: BLE001 - foreground planner will safely retry.
            log_event(
                context_logger,
                logging.WARNING,
                "context.compaction.background_failed",
                session_id=session_id,
                error_type=type(exc).__name__,
            )

    def _maybe_schedule_background(self, session_store, session_id: str) -> None:
        config = self.context_planner.config
        if not config.compaction.background_enabled:
            return
        key = _background_session_key(session_store, session_id)
        with self._background_lock:
            state = self._background_states.get(key)
            existing = self._background_futures.get(key)
        if state is None or (existing is not None and not existing.done()):
            return
        messages = session_store.load(session_id).messages
        turns = group_messages_by_turn(messages)
        if not turns:
            return
        context_root = context_root_for_session_store(session_store)
        if context_root is None:
            return
        system_message = {
            "role": "system",
            "content": self._build_system_prompt(state["workspace"], session_id),
        }
        reusable_messages = [system_message, *self._history_to_llm_dicts(messages)]
        record = CompactionStore(context_root / "compactions").load(session_id)
        if record is not None:
            covered = [
                turn for turn in turns if (turn.turn_index or 0) <= record.source_end_turn_index
            ]
            if validate_compaction(record, covered):
                tail = [
                    turn
                    for turn in turns
                    if (turn.turn_index or 0) > record.source_end_turn_index
                ]
                reusable_messages = [
                    system_message,
                    {"role": "system", "content": format_compaction_evidence(record)},
                    *self._history_to_llm_dicts(
                        [message for turn in tail for message in turn.messages]
                    ),
                ]
            else:
                CompactionStore(context_root / "compactions").delete(session_id)
        reusable_tokens = estimate_llm_tokens(
            reusable_messages,
            tool_definitions=state["tool_definitions"],
        )
        background_trigger = int(
            state["context_budget"].input_token_limit
            * config.compaction.background_trigger_budget_ratio
        )
        if reusable_tokens < background_trigger:
            return
        future: Future = Future()
        with self._background_lock:
            current = self._background_futures.get(key)
            if current is not None and not current.done():
                return
            self._background_futures[key] = future
        thread = threading.Thread(
            target=self._run_background_compaction,
            args=(key, session_id, state, future),
            name="zcagent-context-precompact",
            daemon=True,
        )
        thread.start()

    def _run_background_compaction(self, key, session_id: str, state, future: Future) -> None:
        log_event(
            context_logger,
            logging.INFO,
            "context.compaction.background_started",
            session_id=session_id,
        )
        try:
            config = self.context_planner.config
            background_config = replace(
                config,
                history_query=replace(
                    config.history_query,
                    enabled=False,
                    planner_fallback=False,
                ),
                compaction=replace(
                    config.compaction,
                    trigger_budget_ratio=config.compaction.background_trigger_budget_ratio,
                    background_enabled=False,
                ),
                retrieval=replace(config.retrieval, enabled=False),
            )
            session_store = state["session_store"]
            messages = session_store.load(session_id).messages
            planner = ContextPlanner(background_config)
            plan = planner.plan(
                session_id=session_id,
                turns=group_messages_by_turn(messages),
                system_message={
                    "role": "system",
                    "content": self._build_system_prompt(state["workspace"], session_id),
                },
                current_user={"role": "user", "content": "[background precompaction]"},
                history_to_messages=self._history_to_llm_dicts,
                estimate_tokens=estimate_llm_tokens,
                context_budget=state["context_budget"],
                tool_definitions=state["tool_definitions"],
                context_root=context_root_for_session_store(session_store),
                llm=None,
                compaction_llm=state["compaction_llm"],
                compaction_phase="background",
                compaction_prompt=self.prompt_loader.load("context_compaction").strip(),
            )
            if not plan.compaction_id and "compaction" in plan.degraded:
                raise RuntimeError("background compaction degraded")
            future.set_result(plan)
            log_event(
                context_logger,
                logging.INFO,
                "context.compaction.background_done",
                session_id=session_id,
                compaction_id=plan.compaction_id,
                compacted_through=plan.compacted_through_turn_index,
            )
        except Exception as exc:  # noqa: BLE001 - background work never blocks Session truth.
            future.set_exception(exc)
            log_event(
                context_logger,
                logging.WARNING,
                "context.compaction.background_failed",
                session_id=session_id,
                error_type=type(exc).__name__,
            )
        finally:
            with self._background_lock:
                if self._background_futures.get(key) is future:
                    self._background_futures.pop(key, None)

    def fit_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        tool_definitions: list[dict[str, Any]] | None = None,
        context_budget: ContextBudget | None = None,
    ) -> list[dict[str, Any]]:
        """Fit LLM input to the configured budget without splitting Turn/tool blocks."""

        fitted = [dict(message) for message in messages]
        if context_budget is None:
            return fitted
        available_tokens = max(
            1,
            context_budget.input_token_limit
            - estimate_llm_tokens([], tool_definitions=tool_definitions),
        )

        while estimate_llm_tokens(fitted) > available_tokens:
            turn_ranges = _conversation_turn_ranges(fitted)
            if len(turn_ranges) <= 1:
                break
            start, end = turn_ranges[0]
            del fitted[start:end]

        fitted = _truncate_tool_results_to_budget(fitted, available_tokens)
        fitted = _drop_old_completed_tool_blocks(fitted, available_tokens)
        fitted = _truncate_tool_results_to_budget(fitted, available_tokens)
        if estimate_llm_tokens(fitted) > available_tokens:
            raise LLMContextBudgetError(
                "Required system/current-turn content exceeds the failover-safe "
                f"input budget ({context_budget.input_token_limit} tokens)."
            )
        return fitted

    def _select_recent_history(self, history: list[Message], *, query: str) -> list[Message]:
        """Return history selected by recent relevant user turns or message fallback."""

        if self.max_history_turns is None:
            return history[-self.max_history_messages :] if self.max_history_messages else []
        if self.max_history_turns == 0 or self.max_history_messages == 0:
            return []

        user_turns = [
            group
            for group in group_messages_by_turn(history)
            if any(message.role == "user" for message in group.messages)
        ]
        candidate_turns = user_turns[-self.max_history_turns :]
        recent_count = min(self.always_include_recent_turns, len(candidate_turns))
        if recent_count:
            recent_turns = candidate_turns[-recent_count:]
            relevance_candidates = candidate_turns[:-recent_count]
        else:
            recent_turns = []
            relevance_candidates = candidate_turns
        relevant_turns = select_relevant_turns(
            query,
            relevance_candidates,
            max_selected_turns=self.max_relevant_turns,
        )
        selected_turns = [*relevant_turns, *recent_turns]
        while len(selected_turns) > 1 and _message_count(selected_turns) > self.max_history_messages:
            selected_turns = selected_turns[1:]
        return [message for group in selected_turns for message in group.messages]

    def _build_system_prompt(
        self,
        workspace: Path,
        session_id: str,
        skills_override: SkillProvider | None = None,
    ) -> str:
        """Combine runtime prompts with workspace/session facts for the LLM."""

        prompts = self.prompt_loader.load_many(DEFAULT_CONTEXT_PROMPTS)
        parts = [
            "# Identity",
            prompts["identity"].strip(),
            "# Tool Use Policy",
            prompts["tool_use_policy"].strip(),
            "# Skill Use Policy",
            prompts["skills_intro"].strip(),
        ]
        try:
            memory_policy = self.prompt_loader.load("memory_policy").strip()
        except PromptNotFoundError:
            memory_policy = ""
        if memory_policy:
            parts.extend(["# Memory Policy", memory_policy])
        try:
            diagnostics_policy = self.prompt_loader.load("diagnostics").strip()
        except PromptNotFoundError:
            diagnostics_policy = ""
        if diagnostics_policy:
            parts.extend(["# Diagnostics Policy", diagnostics_policy])
        try:
            exec_policy = self.prompt_loader.load("exec").strip()
        except PromptNotFoundError:
            exec_policy = ""
        if exec_policy:
            parts.extend(["# Exec Policy", exec_policy])
        for prompt_name in self.extra_system_prompts:
            try:
                prompt_text = self.prompt_loader.load(prompt_name).strip()
            except PromptNotFoundError:
                continue
            if prompt_text:
                parts.extend([f"# {prompt_name.replace('_', ' ').title()}", prompt_text])
        skill_summary = self._build_available_skills_prompt(skills_override)
        if skill_summary:
            parts.extend(["# Available Skills", skill_summary])
        parts.extend(
            [
                "# Runtime",
                "Use tools only through the provided tool schemas.",
                f"workspace={workspace}",
                f"session_id={session_id}",
            ]
        )
        return "\n\n".join(parts)

    def _message_to_llm_dict(self, message: Message) -> dict[str, Any] | None:
        """Convert an internal Message to the provider-neutral chat shape."""

        if message.role not in {"system", "user", "assistant", "tool"}:
            return None

        converted: dict[str, Any] = {
            "role": message.role,
            "content": self._truncate(message.content),
        }
        if message.name:
            converted["name"] = message.name
        if message.tool_call_id:
            converted["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            converted["tool_calls"] = message.tool_calls
        return converted

    def _history_to_llm_dicts(self, history: list[Message]) -> list[dict[str, Any]]:
        """Convert history while preserving OpenAI-compatible tool call blocks."""

        converted: list[dict[str, Any]] = []
        index = 0
        while index < len(history):
            message = history[index]
            if message.role == "tool":
                index += 1
                continue

            if message.role == "assistant" and message.tool_calls:
                tool_call_ids = _tool_call_ids(message.tool_calls)
                block: list[dict[str, Any]] = []
                assistant_message = self._message_to_llm_dict(message)
                if assistant_message is not None:
                    block.append(assistant_message)

                seen_tool_call_ids: set[str] = set()
                cursor = index + 1
                while cursor < len(history) and history[cursor].role == "tool":
                    tool_message = history[cursor]
                    tool_call_id = tool_message.tool_call_id
                    if tool_call_id in tool_call_ids and tool_call_id not in seen_tool_call_ids:
                        tool_dict = self._message_to_llm_dict(tool_message)
                        if tool_dict is not None:
                            block.append(tool_dict)
                            seen_tool_call_ids.add(tool_call_id)
                    cursor += 1

                if tool_call_ids and seen_tool_call_ids == set(tool_call_ids):
                    converted.extend(block)
                index = cursor
                continue

            message_dict = self._message_to_llm_dict(message)
            if message_dict is not None:
                converted.append(message_dict)
            index += 1
        return converted

    def _truncate(self, content: str) -> str:
        """Trim overly long message content before sending it to the LLM."""

        if len(content) <= self.max_message_chars:
            return content
        marker = "[truncated]"
        return f"{content[: self.max_message_chars]}{marker}"

    def _build_available_skills_prompt(
        self,
        skills_override: SkillProvider | None = None,
    ) -> str:
        """Return a compact list of available Skills for the system prompt."""

        skills_provider = skills_override or self.skills
        if skills_provider is None or self.max_skill_summaries == 0:
            return ""
        try:
            skills = skills_provider.list_skills()
        except Exception:  # noqa: BLE001 - bad Skill metadata should not block chat startup.
            return ""
        lines = []
        for skill in skills[: self.max_skill_summaries]:
            lines.append(f"- `{skill.qualified_name}`: {skill.summary}")
        text = "\n".join(lines)
        if len(text) <= self.max_skill_summary_chars:
            return text
        marker = "[truncated]"
        return f"{text[: self.max_skill_summary_chars]}{marker}"


def _tool_call_ids(tool_calls: list[dict[str, Any]]) -> list[str]:
    """Return valid unique tool call ids, or empty when the block is unsafe."""

    ids: list[str] = []
    for tool_call in tool_calls:
        raw_id = tool_call.get("id") if isinstance(tool_call, dict) else None
        if not isinstance(raw_id, str) or not raw_id:
            return []
        if raw_id in ids:
            return []
        ids.append(raw_id)
    return ids


def _message_count(groups) -> int:
    """Return total messages across a small list of TurnGroup-like objects."""

    return sum(len(group.messages) for group in groups)


def estimate_llm_tokens(
    messages: list[dict[str, Any]],
    *,
    tool_definitions: list[dict[str, Any]] | None = None,
) -> int:
    """Return a conservative provider-neutral token estimate for LLM input."""

    total = 0
    for message in messages:
        total += _MESSAGE_TOKEN_OVERHEAD
        total += _estimate_text_tokens(str(message.get("role") or ""))
        total += _estimate_text_tokens(str(message.get("content") or ""))
        for field in ("name", "tool_call_id"):
            if message.get(field):
                total += _estimate_text_tokens(str(message[field]))
        if message.get("tool_calls"):
            total += _estimate_text_tokens(_compact_json(message["tool_calls"]))
    if tool_definitions:
        total += _estimate_text_tokens(_compact_json(tool_definitions))
    return total


def _estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    cjk_chars = len(_CJK_CHAR_RE.findall(text))
    non_cjk_chars = max(0, len(text) - cjk_chars)
    return cjk_chars + math.ceil(non_cjk_chars / 4)


def _compact_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return str(value)


def _background_session_key(session_store: object, session_id: str) -> tuple[str, str]:
    root = context_root_for_session_store(session_store)
    return (str(root or id(session_store)), session_id)


def _conversation_turn_ranges(messages: list[dict[str, Any]]) -> list[tuple[int, int]]:
    starts = [index for index, message in enumerate(messages) if message.get("role") == "user"]
    return [
        (start, starts[index + 1] if index + 1 < len(starts) else len(messages))
        for index, start in enumerate(starts)
    ]


def _drop_old_completed_tool_blocks(
    messages: list[dict[str, Any]],
    budget: int,
) -> list[dict[str, Any]]:
    fitted = list(messages)
    while estimate_llm_tokens(fitted) > budget:
        ranges = _conversation_turn_ranges(fitted)
        if not ranges:
            break
        blocks = _complete_tool_block_ranges(fitted, *ranges[-1])
        if len(blocks) <= 1:
            break
        start, end = blocks[0]
        del fitted[start:end]
    return fitted


def _complete_tool_block_ranges(
    messages: list[dict[str, Any]],
    start: int,
    end: int,
) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    index = start
    while index < end:
        message = messages[index]
        if message.get("role") != "assistant" or not message.get("tool_calls"):
            index += 1
            continue
        tool_call_ids = {
            str(call.get("id") or "")
            for call in message.get("tool_calls") or []
            if isinstance(call, dict) and call.get("id")
        }
        cursor = index + 1
        seen_ids: set[str] = set()
        while cursor < end and messages[cursor].get("role") == "tool":
            tool_call_id = str(messages[cursor].get("tool_call_id") or "")
            if tool_call_id:
                seen_ids.add(tool_call_id)
            cursor += 1
        if tool_call_ids and tool_call_ids == seen_ids:
            blocks.append((index, cursor))
        index = max(cursor, index + 1)
    return blocks


def _truncate_tool_results_to_budget(
    messages: list[dict[str, Any]],
    budget: int,
) -> list[dict[str, Any]]:
    fitted = [dict(message) for message in messages]
    while estimate_llm_tokens(fitted) > budget:
        candidates = [
            (len(str(message.get("content") or "")), index)
            for index, message in enumerate(fitted)
            if message.get("role") == "tool"
            and len(str(message.get("content") or "")) > _TOOL_CONTENT_MIN_CHARS
        ]
        if not candidates:
            break
        length, index = max(candidates)
        content = str(fitted[index].get("content") or "")
        keep = max(_TOOL_CONTENT_MIN_CHARS, length // 2)
        fitted[index]["content"] = content[:keep] + "[truncated for context budget]"
    return fitted
