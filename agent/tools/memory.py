"""LLM-callable tools for scoped durable Memory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.memory import MemoryStoreError
from agent.memory.safety import MemorySafetyPolicy
from agent.protocols.memory import MEMORY_CATEGORIES, MemoryEntry, MemoryStore
from agent.protocols.tool import ToolResult
from agent.tools.base import BaseTool, ToolExecutionError, require_int, require_string


class MemoryReadTool(BaseTool):
    """Read bounded entries from the bound Memory store."""

    name = "memory_read"
    description = (
        "List all current-user Memory by category or search it with concrete keywords. "
        "Use mode=list for inventory questions such as 'what do you remember about me'."
    )
    parameters = {
        "type": "object",
        "required": ["mode"],
        "properties": {
            "mode": {"type": "string", "enum": ["list", "search"]},
            "query": {"type": "string"},
            "category": {"type": "string", "enum": ["", *MEMORY_CATEGORIES]},
            "offset": {"type": "integer", "minimum": 0, "maximum": 200},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "additionalProperties": False,
    }

    def __init__(self, workspace: Path | str, *, store: MemoryStore):
        super().__init__(workspace)
        self.store = store

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        mode = require_string(args, "mode", required=True)
        if mode not in {"list", "search"}:
            raise ToolExecutionError("Invalid Memory read mode.", "INVALID_PARAM")
        category = require_string(args, "category", default="")
        offset = require_int(args, "offset", default=0, minimum=0, maximum=200)
        limit = require_int(args, "limit", default=8, minimum=1, maximum=20)
        payload: dict[str, Any] = {"mode": mode}
        returned = 0
        total = 0
        try:
            query = ""
            if mode == "search":
                query = require_string(args, "query", required=True).strip()
                if not query:
                    raise ToolExecutionError(
                        "Memory search requires concrete query text.",
                        "INVALID_PARAM",
                        {"parameter": "query"},
                    )
            entries = self.store.search(
                query,
                category=category,
                offset=offset,
                limit=limit,
            )
            total = self.store.count(query, category=category)
            entry_payloads = [_entry_payload(entry) for entry in entries]
            categories = {name: [] for name in MEMORY_CATEGORIES}
            for entry_payload in entry_payloads:
                categories[entry_payload["category"]].append(entry_payload)
            returned = len(entry_payloads)
            payload.update(
                {
                    "entries": entry_payloads,
                    "categories": categories,
                    "total": total,
                    "returned": returned,
                    "offset": offset,
                    "has_more": offset + returned < total,
                }
            )
        except MemoryStoreError as exc:
            raise ToolExecutionError(exc.message, exc.code, exc.metadata) from exc
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "mode": mode,
                "match_count": returned,
                "total": total,
                "has_more": bool(payload.get("has_more", False)),
                "category": category,
            },
        )


class MemoryWriteTool(BaseTool):
    """Add, replace, or delete one entry after conversational user authorization."""

    name = "memory_write"
    description = (
        "Write an exact change to the current user's long-term Memory only when the current "
        "user explicitly requested it or naturally agreed to the assistant's prior Memory question."
    )
    parameters = {
        "type": "object",
        "required": ["operation", "authorization"],
        "properties": {
            "operation": {"type": "string", "enum": ["add", "replace", "delete"]},
            "category": {"type": "string", "enum": list(MEMORY_CATEGORIES)},
            "content": {"type": "string"},
            "old_content": {"type": "string"},
            "authorization": {
                "type": "string",
                "enum": ["user_explicit", "user_confirmed"],
            },
        },
        "additionalProperties": False,
    }

    def __init__(
        self,
        workspace: Path | str,
        *,
        store: MemoryStore,
        safety: MemorySafetyPolicy,
    ):
        super().__init__(workspace)
        self.store = store
        self.safety = safety

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        authorization = require_string(args, "authorization", default="")
        if authorization not in {"user_explicit", "user_confirmed"}:
            raise ToolExecutionError(
                "Memory write requires conversational user authorization.",
                "MEMORY_USER_AUTHORIZATION_REQUIRED",
            )
        operation = require_string(args, "operation", required=True)
        if operation not in {"add", "replace", "delete"}:
            raise ToolExecutionError("Invalid Memory operation.", "MEMORY_INVALID_OPERATION")
        try:
            if operation == "add":
                category = require_string(args, "category", required=True)
                content = self.safety.validate(require_string(args, "content", required=True))
                entry = self.store.add(category, content)
                return _entry_result(operation, entry)
            if operation == "replace":
                category = require_string(args, "category", required=True)
                old_content = require_string(args, "old_content", required=True)
                content = self.safety.validate(require_string(args, "content", required=True))
                entry = self.store.replace(category, old_content, content)
                return _entry_result(operation, entry)
            category = require_string(args, "category", required=True)
            content = require_string(args, "content", required=True)
            deleted = self.store.delete(category, content)
            return ToolResult(
                output=json.dumps(
                    {
                        "operation": operation,
                        "category": category,
                        "content": content,
                        "deleted": deleted,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                metadata={"operation": operation, "category": category},
            )
        except MemoryStoreError as exc:
            raise ToolExecutionError(exc.message, exc.code, exc.metadata) from exc


def _entry_payload(entry: MemoryEntry) -> dict[str, Any]:
    return {
        "category": entry.category,
        "content": entry.content,
    }


def _entry_result(operation: str, entry: MemoryEntry) -> ToolResult:
    return ToolResult(
        output=json.dumps(
            {"operation": operation, "entry": _entry_payload(entry)},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        metadata={"operation": operation, "category": entry.category},
    )
