"""Build safe search documents from complete Session Turns."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence

from agent.protocols.context import TurnDocument
from agent.protocols.session import TurnGroup

_ENTITY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,}|[\u3400-\u9fff]{2,12}")
_ANCHOR_RE = re.compile(
    r"(?:[A-Za-z]:)?[^\s]+\.(?:py|md|json|ya?ml|txt|log|js|ts|vue)|"
    r"\b[A-Z][A-Z0-9_]{2,}\b|\b(?:turn|session)-[A-Za-z0-9_-]+\b|"
    r"\b(?:[45]\d\d|[A-Za-z]+Error)\b"
)
_SECRET_RE = re.compile(
    r"(?i)(?:api[_-]?key|authorization|password|secret|token)\s*[:=]\s*\S+|"
    r"\bsk-[A-Za-z0-9_-]{8,}\b"
)
_MAX_FIELD_CHARS = 8000
_MAX_TOOL_CHARS = 1500


def build_turn_documents(session_id: str, turns: Sequence[TurnGroup]) -> list[TurnDocument]:
    """Return deterministic sanitized documents for Turns containing a user message."""

    documents: list[TurnDocument] = []
    for fallback_index, turn in enumerate(turns, start=1):
        user_parts = [_sanitize(message.content) for message in turn.messages if message.role == "user"]
        if not user_parts:
            continue
        assistant_parts = [
            _sanitize(message.content)
            for message in turn.messages
            if message.role == "assistant" and not message.metadata.get("reasoning_content")
        ]
        tool_parts = [
            _sanitize(message.content)[:_MAX_TOOL_CHARS]
            for message in turn.messages
            if message.role == "tool"
        ]
        user_text = "\n".join(user_parts)[:_MAX_FIELD_CHARS]
        assistant_text = "\n".join(assistant_parts)[:_MAX_FIELD_CHARS]
        tool_text = "\n".join(tool_parts)[:_MAX_TOOL_CHARS]
        combined = "\n".join((user_text, assistant_text, tool_text))
        entities = tuple(dict.fromkeys(match.group(0) for match in _ENTITY_RE.finditer(combined)))
        anchors = tuple(dict.fromkeys(match.group(0) for match in _ANCHOR_RE.finditer(combined)))
        content_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()
        documents.append(
            TurnDocument(
                session_id=session_id,
                turn_id=turn.turn_id,
                turn_index=turn.turn_index or fallback_index,
                user_text=user_text,
                assistant_text=assistant_text,
                tool_text=tool_text,
                entities=entities[:100],
                anchors=anchors[:100],
                content_hash=content_hash,
            )
        )
    return documents


def turn_source_digest(turns: Sequence[TurnGroup]) -> str:
    """Hash source Turn identity and message content for derived-state invalidation."""

    payload = [
        {
            "turn_id": turn.turn_id,
            "turn_index": turn.turn_index,
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                    "tool_call_id": message.tool_call_id,
                    "tool_calls": message.tool_calls,
                }
                for message in turn.messages
            ],
        }
        for turn in turns
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sanitize(text: str) -> str:
    return _SECRET_RE.sub("[redacted]", str(text or ""))
