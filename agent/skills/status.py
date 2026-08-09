"""Persistent, safe Skill source synchronization and load health state."""

from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class SkillSourceStatus:
    source: str
    enabled: bool = False
    sync_enabled: bool = False
    configured_target: str = ""
    materialized_root: str = ""
    current_commit: str = ""
    last_sync_started_at: str = ""
    last_sync_finished_at: str = ""
    last_success_at: str = ""
    last_status: str = "unknown"
    health: str = "unknown"
    skill_count: int = 0
    load_error_count: int = 0
    last_error_code: str = ""
    last_error_message_safe: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class SkillSourceStatusStore:
    """Atomically persist derived source state without credentials or raw stderr."""

    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.RLock()

    def record_sync_started(self, sources: list[Any]) -> None:
        now = _now()
        with self._lock:
            states = self._load()
            for source in sources:
                state = _configured_state(states.get(source.name), source)
                state.last_sync_started_at = now
                state.last_status = "syncing"
                state.health = "syncing" if source.sync else "disabled"
                states[source.name] = state
            self._save(states)

    def record_sync_result(self, source: Any, result: Any, materialized_root: Path) -> None:
        now = _now()
        with self._lock:
            states = self._load()
            state = _configured_state(states.get(source.name), source)
            state.materialized_root = f"extends/{source.name}"
            state.last_sync_finished_at = now
            state.last_status = str(result.status)
            state.skill_count = max(0, int(getattr(result, "skills", 0) or 0))
            state.current_commit = _git_commit(materialized_root)
            if result.status in {"synced", "up_to_date", "skipped"}:
                state.last_success_at = now
                state.last_error_code = ""
                state.last_error_message_safe = ""
                state.health = "healthy" if source.sync else "disabled"
            else:
                state.last_error_code = "SKILL_SYNC_FAILED"
                state.last_error_message_safe = "Skill source synchronization failed."
                state.health = "degraded"
            states[source.name] = state
            self._save(states)

    def record_unknown_source(self, source_name: str) -> None:
        with self._lock:
            states = self._load()
            state = states.get(source_name) or SkillSourceStatus(source=source_name)
            state.last_sync_started_at = _now()
            state.last_sync_finished_at = state.last_sync_started_at
            state.last_status = "failed"
            state.health = "degraded"
            state.last_error_code = "SKILL_SOURCE_NOT_CONFIGURED"
            state.last_error_message_safe = "Skill source is not configured."
            states[source_name] = state
            self._save(states)

    def record_load_state(self, loader: Any, sources: list[Any]) -> None:
        skills = loader.list_skills()
        counts: dict[str, int] = {}
        for skill in skills:
            counts[skill.source] = counts.get(skill.source, 0) + 1
        errors: dict[str, int] = {}
        source_roots = {
            str(root.root.resolve()): root.source for root in getattr(loader, "skill_roots", ())
        }
        for error in getattr(loader, "load_errors", ()):
            source = str(error.get("qualified_name") or "").partition("/")[0]
            if not source:
                path = str(error.get("path") or "")
                source = next(
                    (name for root, name in source_roots.items() if path.startswith(root)),
                    "",
                )
            if source:
                errors[source] = errors.get(source, 0) + 1
        with self._lock:
            states = self._load()
            configured_names = set()
            for source in sources:
                configured_names.add(source.name)
                state = _configured_state(states.get(source.name), source)
                state.skill_count = counts.get(source.name, 0)
                state.load_error_count = errors.get(source.name, 0)
                if state.load_error_count:
                    state.health = "degraded"
                    state.last_error_code = "SKILL_LOAD_ERROR"
                    state.last_error_message_safe = "One or more Skills could not be loaded."
                elif not source.sync:
                    state.health = "disabled"
                elif state.last_status in {"unknown", "syncing"}:
                    state.health = "healthy" if state.skill_count else "unknown"
                states[source.name] = state
            for name in list(states):
                if name not in configured_names:
                    states.pop(name, None)
            self._save(states)

    def list_statuses(self, *, skill_loader: Any, skill_sync: Any) -> list[dict[str, Any]]:
        _settings, sources = skill_sync.load()
        self.record_load_state(skill_loader, sources)
        with self._lock:
            states = self._load()
        return [states[source.name].as_dict() for source in sources if source.name in states]

    def _load(self) -> dict[str, SkillSourceStatus]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            items = payload.get("sources", {}) if isinstance(payload, dict) else {}
            if not isinstance(items, dict):
                raise ValueError("invalid source state")
            result = {}
            fields = set(SkillSourceStatus.__dataclass_fields__)
            for name, value in items.items():
                if isinstance(name, str) and isinstance(value, dict):
                    clean = {key: value[key] for key in fields if key in value}
                    clean["source"] = name
                    result[name] = SkillSourceStatus(**clean)
            return result
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            return {}

    def _save(self, states: dict[str, SkillSourceStatus]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "sources": {name: state.as_dict() for name, state in sorted(states.items())},
        }
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


def _configured_state(current: SkillSourceStatus | None, source: Any) -> SkillSourceStatus:
    state = current or SkillSourceStatus(source=source.name)
    state.enabled = bool(source.sync)
    state.sync_enabled = bool(source.sync)
    state.configured_target = str(source.target or "master")
    state.materialized_root = f"extends/{source.name}"
    if not source.sync:
        state.health = "disabled"
    return state


def _git_commit(path: Path) -> str:
    if not (path / ".git").exists():
        return ""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and len(value) <= 64 else ""


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
