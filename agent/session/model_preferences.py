"""JSON sidecar persistence for session-scoped model preferences."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.protocols.session import SessionContext, SessionModelPreference
from agent.session.jsonl_store import validate_session_id
from agent.session.sidecar_lock import session_sidecar_lock

_ENDPOINT_FIELD = "preferred_endpoint_name"
_MODEL_FIELD = "preferred_model_name"


class JsonSessionModelPreferenceStore:
    """Read and update model fields without overwriting other session metadata."""

    def get(
        self,
        session_context: SessionContext,
        session_id: str,
    ) -> SessionModelPreference | None:
        """Return the complete saved preference or None for system default."""

        with session_sidecar_lock(self._path(session_context, session_id)):
            metadata = self._read(session_context, session_id)
        endpoint_name = str(metadata.get(_ENDPOINT_FIELD) or "").strip()
        model_name = str(metadata.get(_MODEL_FIELD) or "").strip()
        if not endpoint_name or not model_name:
            return None
        return SessionModelPreference(endpoint_name=endpoint_name, model_name=model_name)

    def set(
        self,
        session_context: SessionContext,
        session_id: str,
        preference: SessionModelPreference,
    ) -> None:
        """Persist endpoint and model fields while preserving title and future fields."""

        endpoint_name = preference.endpoint_name.strip()
        model_name = preference.model_name.strip()
        if not endpoint_name or not model_name:
            raise ValueError("endpoint_name and model_name are required")
        with session_sidecar_lock(self._path(session_context, session_id)):
            metadata = self._read(session_context, session_id)
            metadata[_ENDPOINT_FIELD] = endpoint_name
            metadata[_MODEL_FIELD] = model_name
            self._write(session_context, session_id, metadata)

    def reset(self, session_context: SessionContext, session_id: str) -> None:
        """Remove only the model fields and preserve other sidecar metadata."""

        path = self._path(session_context, session_id)
        with session_sidecar_lock(path):
            metadata = self._read(session_context, session_id)
            metadata.pop(_ENDPOINT_FIELD, None)
            metadata.pop(_MODEL_FIELD, None)
            if metadata:
                self._write(session_context, session_id, metadata)
            elif path.exists():
                path.unlink()

    def _read(self, session_context: SessionContext, session_id: str) -> dict[str, Any]:
        path = self._path(session_context, session_id)
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

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
