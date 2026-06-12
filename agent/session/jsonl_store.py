"""JSONL session store.

Each session is persisted as one UTF-8 JSON object per line. This format keeps
the first-stage runtime easy to inspect by hand while still allowing append-only
conversation history.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from agent.message import Message, Role
from agent.protocols.session import SessionState, SessionSummary

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MESSAGE_FIELDS = {"role", "content", "name", "tool_call_id", "tool_calls", "metadata"}


class InvalidSessionIdError(ValueError):
    """Raised when a session id could escape the sessions directory."""


class JsonlSessionStore:
    """Persist and load Agent sessions from a local JSONL directory."""

    def __init__(self, sessions_dir: Path | str):
        self.sessions_dir = Path(sessions_dir).expanduser().resolve()

    def load(self, session_id: str) -> SessionState:
        """Load a session state from disk, or return an empty session."""

        path = self._path_for(session_id)
        if not path.exists():
            return SessionState(session_id=session_id, messages=[])

        messages: list[Message] = []
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                stripped = line.strip()
                if not stripped:
                    continue
                record = json.loads(stripped)
                messages.append(self._message_from_record(record))
        return SessionState(session_id=session_id, messages=messages)

    def append(self, session_id: str, messages: list[Message]) -> None:
        """Append messages to a session JSONL file."""

        if not messages:
            return

        path = self._path_for(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("a", encoding="utf-8", newline="\n") as file:
            for message in messages:
                record = self._record_from_message(message)
                file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                file.write("\n")

    def clear(self, session_id: str) -> None:
        """Delete the persisted JSONL file for a session if it exists."""

        path = self._path_for(session_id)
        if path.exists():
            path.unlink()

    def list_sessions(self) -> list[SessionSummary]:
        """Return stored sessions sorted by recent update time."""

        if not self.sessions_dir.exists():
            return []

        summaries: list[SessionSummary] = []
        for path in sorted(self.sessions_dir.glob("*.jsonl")):
            session_id = path.stem
            self._path_for(session_id)

            messages: list[Message] = []
            with path.open("r", encoding="utf-8") as file:
                for line in file:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    messages.append(self._message_from_record(json.loads(stripped)))

            updated_at = path.stat().st_mtime
            for message in reversed(messages):
                timestamp = message.metadata.get("timestamp")
                if isinstance(timestamp, int | float):
                    updated_at = float(timestamp)
                    break

            summaries.append(
                SessionSummary(
                    session_id=session_id,
                    preview=_session_preview(messages),
                    updated_at=updated_at,
                    message_count=len(messages),
                )
            )

        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)

    def _path_for(self, session_id: str) -> Path:
        """Resolve and validate the JSONL path for a session id."""

        if not _SESSION_ID_RE.fullmatch(session_id):
            raise InvalidSessionIdError(
                "session_id must contain only letters, numbers, underscores, and hyphens"
            )
        return self.sessions_dir / f"{session_id}.jsonl"

    @staticmethod
    def _record_from_message(message: Message) -> dict[str, Any]:
        """Convert a Message into the stable JSONL record shape."""

        metadata = dict(message.metadata)
        timestamp = metadata.pop("timestamp", time.time())
        return {
            "role": message.role,
            "content": message.content,
            "timestamp": timestamp,
            "name": message.name,
            "tool_call_id": message.tool_call_id,
            "tool_calls": message.tool_calls,
            "metadata": metadata,
        }

    @staticmethod
    def _message_from_record(record: dict[str, Any]) -> Message:
        """Convert a JSONL record into Message while ignoring unknown fields."""

        metadata = dict(record.get("metadata") or {})
        if "timestamp" in record:
            metadata.setdefault("timestamp", record["timestamp"])

        message_data = {
            key: record.get(key)
            for key in _MESSAGE_FIELDS
            if key in record and key != "metadata"
        }
        return Message(
            role=message_data.get("role", "user"),  # type: ignore[arg-type]
            content=str(message_data.get("content", "")),
            name=message_data.get("name"),
            tool_call_id=message_data.get("tool_call_id"),
            tool_calls=list(message_data.get("tool_calls") or []),
            metadata=metadata,
        )


def validate_role(role: str) -> Role:
    """Return a typed role when the persisted value is recognized."""

    if role not in {"system", "user", "assistant", "tool"}:
        raise ValueError(f"unknown message role: {role}")
    return role  # type: ignore[return-value]


def _session_preview(messages: list[Message]) -> str:
    """Build a short preview from the first user message in a session."""

    for message in messages:
        if message.role != "user":
            continue
        text = " ".join(message.content.split())
        if text:
            return text[:40] + "..." if len(text) > 40 else text

    for message in messages:
        text = " ".join(message.content.split())
        if text:
            return text[:40] + "..." if len(text) > 40 else text
    return "(empty)"
