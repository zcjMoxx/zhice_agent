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
_MESSAGE_FIELDS = {
    "role",
    "content",
    "name",
    "tool_call_id",
    "tool_calls",
    "metadata",
    "turn_id",
    "turn_index",
    "parent_turn_id",
}


class InvalidSessionIdError(ValueError):
    """Raised when a session id could escape the sessions directory."""


def validate_session_id(session_id: str) -> str:
    """Validate and return a path-safe session id."""

    if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
        raise InvalidSessionIdError(
            "session_id must contain only letters, numbers, underscores, and hyphens"
        )
    return session_id


class JsonlSessionStore:
    """Persist and load Agent sessions from a local JSONL directory."""

    def __init__(self, sessions_dir: Path | str):
        """Resolve the session directory used by all JSONL reads and writes."""

        self.sessions_dir = Path(sessions_dir).expanduser().resolve()
        self.metadata_dir = (
            self.sessions_dir.parent / "sessions_meta"
            if self.sessions_dir.name == "sessions"
            else self.sessions_dir / "sessions_meta"
        )

    def load(self, session_id: str) -> SessionState:
        """Load a session state from disk, or return an empty session."""

        path = self._path_for(session_id)
        metadata = self._read_metadata(session_id)
        if not path.exists():
            return SessionState(session_id=session_id, messages=[], metadata=metadata)

        messages: list[Message] = []
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                stripped = line.strip()
                if not stripped:
                    continue
                record = json.loads(stripped)
                messages.append(self._message_from_record(record))
        return SessionState(session_id=session_id, messages=messages, metadata=metadata)

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

    def replace(self, session_id: str, messages: list[Message]) -> None:
        """Atomically replace one session message file while preserving metadata."""

        path = self._path_for(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".jsonl.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as file:
            for message in messages:
                record = self._record_from_message(message)
                file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                file.write("\n")
        temporary.replace(path)

    def update_metadata(self, session_id: str, values: dict[str, Any]) -> None:
        """Merge bounded application metadata into the Session sidecar."""

        metadata = self._read_metadata(session_id)
        metadata.update(values)
        self._write_metadata(session_id, metadata)

    def clear(self, session_id: str) -> None:
        """Delete the persisted JSONL file for a session if it exists."""

        path = self._path_for(session_id)
        if path.exists():
            path.unlink()

    def rename(self, session_id: str, title: str) -> None:
        """Store a display title for a session without changing the JSONL id."""

        self._path_for(session_id)
        normalized_title = " ".join(title.split())
        if not normalized_title:
            raise ValueError("title is required")
        if len(normalized_title) > 120:
            normalized_title = normalized_title[:120]
        metadata = self._read_metadata(session_id)
        metadata["title"] = normalized_title
        self._write_metadata(session_id, metadata)

    def delete(self, session_id: str) -> None:
        """Delete the session messages and sidecar metadata."""

        self.clear(session_id)
        metadata_path = self._metadata_path_for(session_id)
        if metadata_path.exists():
            metadata_path.unlink()

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
                    title=str(self._read_metadata(session_id).get("title") or ""),
                )
            )

        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)

    def _path_for(self, session_id: str) -> Path:
        """Resolve and validate the JSONL path for a session id."""

        validate_session_id(session_id)
        return self.sessions_dir / f"{session_id}.jsonl"

    def _metadata_path_for(self, session_id: str) -> Path:
        """Resolve and validate the sidecar metadata path for a session id."""

        self._path_for(session_id)
        return self.metadata_dir / f"{session_id}.json"

    def _read_metadata(self, session_id: str) -> dict[str, Any]:
        """Read optional sidecar metadata, ignoring malformed non-dict content."""

        path = self._metadata_path_for(session_id)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_metadata(self, session_id: str, metadata: dict[str, Any]) -> None:
        """Write sidecar metadata as UTF-8 JSON."""

        path = self._metadata_path_for(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _record_from_message(message: Message) -> dict[str, Any]:
        """Convert a Message into the stable JSONL record shape."""

        metadata = dict(message.metadata)
        timestamp = metadata.pop("timestamp", time.time())
        return {
            "role": message.role,
            "content": message.content,
            "timestamp": timestamp,
            "turn_id": message.turn_id,
            "turn_index": message.turn_index,
            "parent_turn_id": message.parent_turn_id,
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

        turn_id = record.get("turn_id")
        if not isinstance(turn_id, str) or not turn_id:
            turn_id = None

        turn_index = record.get("turn_index")
        if not isinstance(turn_index, int):
            turn_index = None

        parent_turn_id = record.get("parent_turn_id")
        if not isinstance(parent_turn_id, str) or not parent_turn_id:
            parent_turn_id = None

        message_data = {
            key: record.get(key)
            for key in _MESSAGE_FIELDS
            if key in record and key not in {"metadata", "turn_id", "turn_index", "parent_turn_id"}
        }
        return Message(
            role=message_data.get("role", "user"),  # type: ignore[arg-type]
            content=str(message_data.get("content", "")),
            name=message_data.get("name"),
            tool_call_id=message_data.get("tool_call_id"),
            tool_calls=list(message_data.get("tool_calls") or []),
            metadata=metadata,
            turn_id=turn_id,
            turn_index=turn_index,
            parent_turn_id=parent_turn_id,
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
