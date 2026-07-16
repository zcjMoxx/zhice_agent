"""Bounded background extraction of durable user Memory."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

from agent.core.turns import group_messages_by_turn
from agent.memory import MemoryStoreError
from agent.memory.safety import MemorySafetyPolicy
from agent.message import Message
from agent.prompt_loader import PromptLoader
from agent.protocols.llm import LLMProvider
from agent.protocols.memory import (
    MemoryContext,
    MemoryExtractionCandidate,
    MemoryExtractionResult,
    MemoryStore,
)

ALLOWED_CATEGORIES = {"profile", "preferences", "constraints"}


class MemoryExtractionService:
    """Review new user turns after a Session becomes idle."""

    def __init__(
        self,
        context: MemoryContext,
        store: MemoryStore,
        prompt_loader: PromptLoader,
        safety: MemorySafetyPolicy,
        *,
        min_user_turns: int = 3,
        max_source_turns: int = 40,
        max_source_chars: int = 40000,
    ):
        self.context = context
        self.store = store
        self.prompt_loader = prompt_loader
        self.safety = safety
        self.min_user_turns = min_user_turns
        self.max_source_turns = max_source_turns
        self.max_source_chars = max_source_chars

    def extract(
        self,
        session_id: str,
        messages: list[Message],
        llm: LLMProvider,
        *,
        should_commit: Callable[[], bool] | None = None,
        notify: bool = True,
    ) -> MemoryExtractionResult:
        groups = [
            group
            for group in group_messages_by_turn(messages)
            if isinstance(group.turn_index, int)
            and any(message.role == "user" for message in group.messages)
        ]
        if len(groups) < self.min_user_turns:
            return MemoryExtractionResult(reviewed_through_turn_index=0)

        checkpoint = self._read_checkpoint(session_id)
        new_groups = [group for group in groups if (group.turn_index or 0) > checkpoint]
        if not new_groups:
            return MemoryExtractionResult(reviewed_through_turn_index=checkpoint)

        selected = groups[-self.max_source_turns :]
        source_turns = self._source_turns(selected)
        reviewed_through = max(group.turn_index or 0 for group in new_groups)
        try:
            response = llm.chat(
                messages=[
                    {"role": "system", "content": self.prompt_loader.load("memory_extraction")},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"session_id": session_id, "user_turns": source_turns},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                ],
                tools=None,
            )
        except Exception as exc:
            raise MemoryStoreError(
                "MEMORY_EXTRACTION_PROVIDER_FAILED",
                "Background Memory provider call failed.",
            ) from exc
        candidates = _parse_candidates(response.content, source_turns)

        can_commit = should_commit or (lambda: True)
        if not can_commit():
            return MemoryExtractionResult(reviewed_through_turn_index=checkpoint)

        added = []
        for candidate in candidates:
            if not can_commit():
                return MemoryExtractionResult(
                    reviewed_through_turn_index=checkpoint,
                    added=tuple(added),
                )
            try:
                content = self.safety.validate(candidate.content)
                existing_contents = {
                    " ".join(entry.content.split()).casefold()
                    for entry in self.store.search(
                        query=content,
                        category=candidate.category,
                        limit=20,
                    )
                }
                if content.casefold() in existing_contents:
                    continue
                entry = self.store.add(candidate.category, content)
            except MemoryStoreError:
                continue
            added.append(entry)

        if not can_commit():
            return MemoryExtractionResult(
                reviewed_through_turn_index=checkpoint,
                added=tuple(added),
            )
        self._write_checkpoint(session_id, reviewed_through)
        if added and notify:
            self._append_notification([entry.content for entry in added])
        return MemoryExtractionResult(
            reviewed_through_turn_index=reviewed_through,
            added=tuple(added),
        )

    def pop_notification(self) -> tuple[str, ...]:
        """Consume the pending auto-Memory notification once."""

        return pop_memory_notification(self.context)

    def _source_turns(self, groups) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        chars = 0
        for group in groups:
            content = "\n".join(
                message.content for message in group.messages if message.role == "user"
            ).strip()
            if not content:
                continue
            content = content[:8000]
            if result and chars + len(content) > self.max_source_chars:
                break
            result.append({"turn_index": group.turn_index, "content": content})
            chars += len(content)
        return result

    def _read_checkpoint(self, session_id: str) -> int:
        path = self._checkpoint_path(session_id)
        if not path.is_file():
            return 0
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        value = raw.get("reviewed_through_turn_index", 0) if isinstance(raw, dict) else 0
        return value if isinstance(value, int) and value >= 0 else 0

    def _write_checkpoint(self, session_id: str, turn_index: int) -> None:
        _atomic_write_json(
            self._checkpoint_path(session_id),
            {"reviewed_through_turn_index": turn_index},
        )

    def _append_notification(self, contents: list[str]) -> None:
        current = list(pop_memory_notification(self.context))
        combined = list(dict.fromkeys([*current, *contents]))[-5:]
        _atomic_write_json(self._notification_path(), combined)

    def _checkpoint_path(self, session_id: str) -> Path:
        if not session_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in session_id):
            raise MemoryStoreError("MEMORY_EXTRACTION_FAILED", "Invalid extraction session id.")
        return self.context.memory_dir / "extraction_state" / f"{session_id}.json"

    def _notification_path(self) -> Path:
        return self.context.memory_dir / "extraction_state" / "pending_notification.json"


def _parse_candidates(
    text: str,
    source_turns: list[dict[str, Any]],
) -> tuple[MemoryExtractionCandidate, ...]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MemoryStoreError("MEMORY_EXTRACTION_INVALID", "Invalid extraction output.") from exc
    raw_items = payload.get("memories") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        raise MemoryStoreError("MEMORY_EXTRACTION_INVALID", "Invalid extraction output.")
    source_by_index = {
        item["turn_index"]: str(item["content"])
        for item in source_turns
        if isinstance(item.get("turn_index"), int)
    }
    result: list[MemoryExtractionCandidate] = []
    for item in raw_items[:8]:
        if not isinstance(item, dict) or item.get("confidence") != "high":
            continue
        category = item.get("category")
        content = item.get("content")
        evidence = item.get("evidence")
        if category not in ALLOWED_CATEGORIES or not isinstance(content, str):
            continue
        if not isinstance(evidence, list) or not 2 <= len(evidence) <= 3:
            continue
        indexes: list[int] = []
        valid = True
        for proof in evidence:
            if not isinstance(proof, dict):
                valid = False
                break
            turn_index = proof.get("turn_index")
            quote = proof.get("quote")
            if (
                not isinstance(turn_index, int)
                or turn_index not in source_by_index
                or not isinstance(quote, str)
                or not quote.strip()
                or quote.strip() not in source_by_index[turn_index]
            ):
                valid = False
                break
            indexes.append(turn_index)
        if not valid or len(set(indexes)) != len(indexes):
            continue
        result.append(
            MemoryExtractionCandidate(
                category=category,
                content=" ".join(content.split()),
                evidence_turn_indexes=tuple(indexes),
            )
        )
    return tuple(result)


def pop_memory_notification(context: MemoryContext) -> tuple[str, ...]:
    """Consume one actor-scoped background Memory notification."""

    path = context.memory_dir / "extraction_state" / "pending_notification.json"
    if not path.is_file():
        return ()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        path.unlink(missing_ok=True)
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(raw, list):
        return ()
    return tuple(str(item).strip() for item in raw if str(item).strip())


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            json.dump(payload, file, ensure_ascii=False, separators=(",", ":"))
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
