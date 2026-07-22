"""JSON sidecar persistence for session-scoped Subagent preferences."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.protocols.session import SessionContext
from agent.session.jsonl_store import validate_session_id
from agent.session.sidecar_lock import session_sidecar_lock

_MODE_FIELD = "subagent_mode"
_FORCE_ONCE_FIELD = "subagent_force_once"
_VALID_MODES = frozenset({"auto", "off"})


@dataclass(frozen=True)
class SessionSubagentPreference:
    """Effective Subagent preference for one session."""

    mode: str = "auto"
    force_once: bool = False


class JsonSessionSubagentPreferenceStore:
    """Mutate Subagent fields without overwriting other sidecar metadata."""

    def get(
        self,
        session_context: SessionContext,
        session_id: str,
    ) -> SessionSubagentPreference:
        """Return the saved preference, applying safe defaults to stale values."""

        with session_sidecar_lock(self._path(session_context, session_id)):
            metadata = self._read(session_context, session_id)
        mode = str(metadata.get(_MODE_FIELD) or "auto").strip().lower()
        if mode not in _VALID_MODES:
            mode = "auto"
        return SessionSubagentPreference(
            mode=mode,
            force_once=metadata.get(_FORCE_ONCE_FIELD) is True,
        )

    def set_mode(
        self,
        session_context: SessionContext,
        session_id: str,
        mode: str,
    ) -> SessionSubagentPreference:
        """Persist auto/off mode and clear any older one-shot request."""

        normalized = mode.strip().lower()
        if normalized not in _VALID_MODES:
            raise ValueError("subagent mode must be auto or off")
        with session_sidecar_lock(self._path(session_context, session_id)):
            metadata = self._read(session_context, session_id)
            metadata[_MODE_FIELD] = normalized
            metadata.pop(_FORCE_ONCE_FIELD, None)
            self._write(session_context, session_id, metadata)
        return SessionSubagentPreference(mode=normalized, force_once=False)

    def force_once(
        self,
        session_context: SessionContext,
        session_id: str,
    ) -> SessionSubagentPreference:
        """Require Subagent delegation for the next ordinary message."""

        with session_sidecar_lock(self._path(session_context, session_id)):
            metadata = self._read(session_context, session_id)
            mode = str(metadata.get(_MODE_FIELD) or "auto").strip().lower()
            if mode not in _VALID_MODES:
                mode = "auto"
            metadata[_MODE_FIELD] = mode
            metadata[_FORCE_ONCE_FIELD] = True
            self._write(session_context, session_id, metadata)
        return SessionSubagentPreference(mode=mode, force_once=True)

    def consume_force_once(
        self,
        session_context: SessionContext,
        session_id: str,
    ) -> bool:
        """Atomically consume and return the one-shot flag."""

        with session_sidecar_lock(self._path(session_context, session_id)):
            metadata = self._read(session_context, session_id)
            if metadata.get(_FORCE_ONCE_FIELD) is not True:
                return False
            metadata.pop(_FORCE_ONCE_FIELD, None)
            self._write_or_remove(session_context, session_id, metadata)
            return True

    def clear_force_once(
        self,
        session_context: SessionContext,
        session_id: str,
    ) -> None:
        """Clear only the one-shot flag while preserving mode and other fields."""

        with session_sidecar_lock(self._path(session_context, session_id)):
            metadata = self._read(session_context, session_id)
            metadata.pop(_FORCE_ONCE_FIELD, None)
            self._write_or_remove(session_context, session_id, metadata)

    def _read(self, session_context: SessionContext, session_id: str) -> dict[str, Any]:
        path = self._path(session_context, session_id)
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_or_remove(
        self,
        session_context: SessionContext,
        session_id: str,
        metadata: dict[str, Any],
    ) -> None:
        path = self._path(session_context, session_id)
        if metadata:
            self._write(session_context, session_id, metadata)
        elif path.exists():
            path.unlink()

    def _write(
        self,
        session_context: SessionContext,
        session_id: str,
        metadata: dict[str, Any],
    ) -> None:
        path = self._path(session_context, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _path(session_context: SessionContext, session_id: str) -> Path:
        validate_session_id(session_id)
        metadata_dir = Path(session_context.sessions_meta_dir).expanduser().resolve()
        path = (metadata_dir / f"{session_id}.json").resolve()
        try:
            path.relative_to(metadata_dir)
        except ValueError as exc:
            raise ValueError("session metadata path is outside the metadata directory") from exc
        return path
