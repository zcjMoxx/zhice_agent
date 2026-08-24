"""SQLite source of truth for workflow definitions, versions and runs."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from agent.workflows.schemas import WorkflowDefinitionV1, WorkflowRun, utc_now

_UNSET = object()
_DELIVERY_NODE_TYPES = frozenset(
    {"official_notification", "personal_email", "qq_notification", "weixin_notification"}
)


def _editable_signature(value: WorkflowDefinitionV1 | dict[str, Any]) -> str:
    payload = value.to_dict() if isinstance(value, WorkflowDefinitionV1) else dict(value)
    for key in ("version", "status", "created_at", "updated_at", "published_at"):
        payload.pop(key, None)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _summary_value(summary: str) -> Any:
    try:
        return json.loads(summary)
    except (TypeError, json.JSONDecodeError):
        return summary


def _delivery_content_summary(input_summary: str) -> str:
    value = _summary_value(input_summary)
    if not isinstance(value, dict):
        return input_summary
    content = value.get("content")
    if content not in (None, ""):
        return json.dumps(content, ensure_ascii=False) if not isinstance(content, str) else content
    source = value.get("source_ref")
    if source in (None, ""):
        return ""
    return json.dumps(source, ensure_ascii=False) if not isinstance(source, str) else source


def _run_results(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deliveries = [
        node
        for node in nodes
        if node["node_type"] in _DELIVERY_NODE_TYPES and node["status"] != "skipped"
    ]
    if deliveries:
        return [
            {
                "node_id": node["node_id"],
                "node_type": node["node_type"],
                "status": node["status"],
                "content_summary": _delivery_content_summary(node.get("input_summary", "")),
                "delivery_summary": node.get("output_summary", ""),
                "error_code": node.get("error_code"),
            }
            for node in deliveries
        ]
    candidates = [
        node
        for node in nodes
        if node["status"] == "succeeded" and node.get("output_summary")
        and node["node_type"] not in {"schedule_trigger", "condition"}
    ]
    templates = [node for node in candidates if node["node_type"] == "template"]
    selected = (templates or candidates)[-1:]  # A run without delivery has one final visible result.
    return [
        {
            "node_id": node["node_id"],
            "node_type": node["node_type"],
            "status": node["status"],
            "content_summary": node["output_summary"],
            "delivery_summary": "",
            "error_code": node.get("error_code"),
        }
        for node in selected
    ]


class WorkflowStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS workflow_definitions(
              id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, name TEXT NOT NULL,
              status TEXT NOT NULL, latest_draft_version INTEGER NOT NULL,
              active_version INTEGER, draft_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS ix_workflows_owner ON workflow_definitions(owner_user_id);
            CREATE TABLE IF NOT EXISTS workflow_versions(
              workflow_id TEXT NOT NULL, version INTEGER NOT NULL, definition_json TEXT NOT NULL,
              schema_version INTEGER NOT NULL, required_permissions_json TEXT NOT NULL,
              tool_schema_hashes_json TEXT NOT NULL, published_at TEXT NOT NULL,
              PRIMARY KEY(workflow_id,version), FOREIGN KEY(workflow_id) REFERENCES workflow_definitions(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS workflow_schedules(
              workflow_id TEXT PRIMARY KEY, trigger_type TEXT NOT NULL, trigger_json TEXT NOT NULL,
              timezone TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, last_scheduled_at TEXT,
              last_started_at TEXT, last_finished_at TEXT, next_run_at TEXT,
              misfire_grace_seconds INTEGER NOT NULL DEFAULT 900, coalesce INTEGER NOT NULL DEFAULT 1,
              FOREIGN KEY(workflow_id) REFERENCES workflow_definitions(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS workflow_runs(
              id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, version INTEGER NOT NULL,
              owner_user_id TEXT NOT NULL, trigger_type TEXT NOT NULL, scheduled_for TEXT,
              status TEXT NOT NULL, started_at TEXT, finished_at TEXT, error_code TEXT,
              FOREIGN KEY(workflow_id) REFERENCES workflow_definitions(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS workflow_node_runs(
              run_id TEXT NOT NULL, node_id TEXT NOT NULL, node_type TEXT NOT NULL, status TEXT NOT NULL,
              attempt INTEGER NOT NULL, safe_input_summary TEXT, safe_output_summary TEXT,
              started_at TEXT, finished_at TEXT, error_code TEXT,
              PRIMARY KEY(run_id,node_id,attempt), FOREIGN KEY(run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS workflow_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, event_type TEXT NOT NULL,
              payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
              FOREIGN KEY(run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS outbound_deliveries(
              id TEXT PRIMARY KEY, run_id TEXT NOT NULL, node_id TEXT NOT NULL, provider TEXT NOT NULL,
              status TEXT NOT NULL, safe_recipient_summary TEXT, external_id TEXT, created_at TEXT NOT NULL,
              FOREIGN KEY(run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE);
            """)

    def save_draft(self, definition: WorkflowDefinitionV1, *, expected_version: int | None = None) -> WorkflowDefinitionV1:
        now = utc_now()
        with self._connect() as db:
            row = db.execute(
                """SELECT owner_user_id,latest_draft_version,active_version
                FROM workflow_definitions WHERE id=?""",
                (definition.workflow_id,),
            ).fetchone()
            if row and row["owner_user_id"] != definition.owner_user_id:
                raise PermissionError("WORKFLOW_ACCESS_DENIED")
            if row and expected_version is not None and row["latest_draft_version"] != expected_version:
                raise RuntimeError("WORKFLOW_DRAFT_CONFLICT")
            normalized = definition
            active_version = int(row["active_version"]) if row and row["active_version"] is not None else None
            if row and active_version is not None and definition.version <= active_version:
                active_row = db.execute(
                    "SELECT definition_json FROM workflow_versions WHERE workflow_id=? AND version=?",
                    (definition.workflow_id, active_version),
                ).fetchone()
                active_payload = json.loads(active_row["definition_json"]) if active_row else {}
                if _editable_signature(definition) != _editable_signature(active_payload):
                    normalized = WorkflowDefinitionV1.from_dict(
                        {
                            **definition.to_dict(),
                            "version": max(int(row["latest_draft_version"]) + 1, active_version + 1),
                            "published_at": None,
                        }
                    )
            normalized = WorkflowDefinitionV1.from_dict(
                {**normalized.to_dict(), "updated_at": now}
            )
            payload = json.dumps(
                normalized.to_dict(), ensure_ascii=False, separators=(",", ":")
            )
            db.execute("""INSERT INTO workflow_definitions(id,owner_user_id,name,status,latest_draft_version,active_version,draft_json,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?, ?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,status=excluded.status,
              latest_draft_version=excluded.latest_draft_version,draft_json=excluded.draft_json,updated_at=excluded.updated_at""",
              (normalized.workflow_id, normalized.owner_user_id, normalized.name, normalized.status,
               normalized.version, None, payload, normalized.created_at, now))
        return normalized

    def workflow_state(
        self, workflow_id: str, *, owner_user_id: str | None = None
    ) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                """SELECT owner_user_id,latest_draft_version,active_version,
                       draft_json,updated_at
                FROM workflow_definitions WHERE id=?""",
                (workflow_id,),
            ).fetchone()
            active_row = None
            if row is not None and row["active_version"] is not None:
                active_row = db.execute(
                    """SELECT definition_json FROM workflow_versions
                    WHERE workflow_id=? AND version=?""",
                    (workflow_id, row["active_version"]),
                ).fetchone()
        if row is None:
            raise KeyError("WORKFLOW_NOT_FOUND")
        if owner_user_id is not None and row["owner_user_id"] != owner_user_id:
            raise PermissionError("WORKFLOW_ACCESS_DENIED")
        active_version = int(row["active_version"]) if row["active_version"] is not None else None
        draft_version = int(row["latest_draft_version"])
        content_changed = active_row is None or _editable_signature(
            json.loads(row["draft_json"])
        ) != _editable_signature(json.loads(active_row["definition_json"]))
        return {
            "active_version": active_version,
            "has_unpublished_changes": active_version != draft_version or content_changed,
            "updated_at": str(row["updated_at"]),
        }

    def get_draft(self, workflow_id: str, *, owner_user_id: str | None = None) -> WorkflowDefinitionV1 | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT d.owner_user_id,d.status,d.draft_json,v.published_at
                FROM workflow_definitions d
                LEFT JOIN workflow_versions v
                  ON v.workflow_id=d.id AND v.version=d.active_version
                WHERE d.id=?""",
                (workflow_id,),
            ).fetchone()
        if not row:
            return None
        if owner_user_id is not None and row["owner_user_id"] != owner_user_id:
            raise PermissionError("WORKFLOW_ACCESS_DENIED")
        payload = {**json.loads(row["draft_json"]), "status": row["status"]}
        if row["published_at"]:
            payload["published_at"] = row["published_at"]
        return WorkflowDefinitionV1.from_dict(payload)

    def publish(self, definition: WorkflowDefinitionV1, *, tool_schema_hashes: dict[str, str] | None = None) -> WorkflowDefinitionV1:
        from agent.workflows.catalog import validate_definition

        validate_definition(definition)
        published_at = utc_now()
        published = WorkflowDefinitionV1.from_dict({**definition.to_dict(), "status": "active", "published_at": published_at})
        payload = json.dumps(published.to_dict(), ensure_ascii=False, separators=(",", ":"))
        with self._connect() as db:
            row = db.execute(
                "SELECT owner_user_id,active_version FROM workflow_definitions WHERE id=?",
                (definition.workflow_id,),
            ).fetchone()
            if not row:
                raise KeyError("WORKFLOW_NOT_FOUND")
            if row["owner_user_id"] != definition.owner_user_id:
                raise PermissionError("WORKFLOW_ACCESS_DENIED")
            if row["active_version"] == definition.version:
                existing = db.execute(
                    "SELECT definition_json FROM workflow_versions WHERE workflow_id=? AND version=?",
                    (definition.workflow_id, definition.version),
                ).fetchone()
                if existing and _editable_signature(json.loads(existing[0])) == _editable_signature(definition):
                    return WorkflowDefinitionV1.from_dict(json.loads(existing[0]))
            try:
                db.execute("INSERT INTO workflow_versions VALUES(?,?,?,?,?,?,?)", (definition.workflow_id, definition.version, payload, 1, json.dumps(definition.required_permissions), json.dumps(tool_schema_hashes or {}), published_at))
            except sqlite3.IntegrityError as exc:
                raise RuntimeError("WORKFLOW_VERSION_IMMUTABLE") from exc
            db.execute("UPDATE workflow_definitions SET active_version=?,status='active',updated_at=? WHERE id=?", (definition.version, published_at, definition.workflow_id))
        return published

    def get_published(self, workflow_id: str, version: int | None = None, *, owner_user_id: str | None = None) -> WorkflowDefinitionV1 | None:
        with self._connect() as db:
            root = db.execute("SELECT owner_user_id,active_version FROM workflow_definitions WHERE id=?", (workflow_id,)).fetchone()
            if not root:
                return None
            if owner_user_id is not None and root["owner_user_id"] != owner_user_id:
                raise PermissionError("WORKFLOW_ACCESS_DENIED")
            resolved = version if version is not None else root["active_version"]
            if resolved is None:
                return None
            row = db.execute("SELECT definition_json FROM workflow_versions WHERE workflow_id=? AND version=?", (workflow_id, resolved)).fetchone()
        return WorkflowDefinitionV1.from_dict(json.loads(row[0])) if row else None

    def list_definitions(self, owner_user_id: str) -> list[WorkflowDefinitionV1]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT d.status,d.draft_json,v.published_at
                FROM workflow_definitions d
                LEFT JOIN workflow_versions v
                  ON v.workflow_id=d.id AND v.version=d.active_version
                WHERE d.owner_user_id=? ORDER BY d.updated_at DESC""",
                (owner_user_id,),
            ).fetchall()
        items = []
        for row in rows:
            payload = {**json.loads(row["draft_json"]), "status": row["status"]}
            if row["published_at"]:
                payload["published_at"] = row["published_at"]
            items.append(WorkflowDefinitionV1.from_dict(payload))
        return items

    def set_status(self, workflow_id: str, owner_user_id: str, status: str) -> None:
        with self._connect() as db:
            result = db.execute("UPDATE workflow_definitions SET status=?,updated_at=? WHERE id=? AND owner_user_id=?", (status, utc_now(), workflow_id, owner_user_id))
            if result.rowcount != 1:
                raise KeyError("WORKFLOW_NOT_FOUND")

    def delete(self, workflow_id: str, owner_user_id: str) -> None:
        with self._connect() as db:
            result = db.execute("DELETE FROM workflow_definitions WHERE id=? AND owner_user_id=?", (workflow_id, owner_user_id))
            if result.rowcount != 1:
                raise KeyError("WORKFLOW_NOT_FOUND")

    def create_run(self, run: WorkflowRun) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,?,?,?,?)", (run.id, run.workflow_id, run.version, run.owner_user_id, run.trigger_type, run.scheduled_for, run.status, run.started_at, run.finished_at, run.error_code))

    def update_run(self, run_id: str, status: str, *, error_code: str | None = None) -> None:
        now = utc_now()
        started = now if status == "running" else None
        finished = now if status in {"succeeded", "failed", "cancelled", "partial"} else None
        with self._connect() as db:
            db.execute("UPDATE workflow_runs SET status=?,started_at=COALESCE(started_at,?),finished_at=COALESCE(?,finished_at),error_code=? WHERE id=?", (status, started, finished, error_code, run_id))

    def record_node_run(self, run_id: str, node_id: str, node_type: str, status: str, *, attempt: int = 1, input_summary: str = "", output_summary: str = "", error_code: str | None = None) -> None:
        now = utc_now()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO workflow_node_runs VALUES(?,?,?,?,?,?,?,?,?,?)", (run_id, node_id, node_type, status, attempt, input_summary[:4096], output_summary[:4096], now, now, error_code))

    def append_event(self, run_id: str, event_type: str, payload: dict[str, Any] | None = None) -> int:
        with self._connect() as db:
            cursor = db.execute("INSERT INTO workflow_events(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)", (run_id, event_type, json.dumps(payload or {}, separators=(",", ":")), utc_now()))
            return int(cursor.lastrowid)

    def events_after(self, run_id: str, cursor: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT id,event_type,payload_json,created_at FROM workflow_events WHERE run_id=? AND id>? ORDER BY id LIMIT ?", (run_id, cursor, min(limit, 500))).fetchall()
        return [{"cursor": row[0], "type": row[1], "payload": json.loads(row[2]), "created_at": row[3]} for row in rows]

    def upsert_schedule(self, workflow_id: str, trigger_type: str, trigger: dict[str, Any], timezone: str, *, enabled: bool = True, misfire_grace_seconds: int = 900, coalesce: bool = True) -> None:
        if trigger_type not in {"date", "interval", "cron"}:
            raise ValueError("WORKFLOW_TRIGGER_INVALID")
        with self._connect() as db:
            db.execute("""INSERT INTO workflow_schedules(workflow_id,trigger_type,trigger_json,timezone,enabled,misfire_grace_seconds,coalesce)
              VALUES(?,?,?,?,?,?,?) ON CONFLICT(workflow_id) DO UPDATE SET trigger_type=excluded.trigger_type,
              trigger_json=excluded.trigger_json,timezone=excluded.timezone,enabled=excluded.enabled,
              misfire_grace_seconds=excluded.misfire_grace_seconds,coalesce=excluded.coalesce""",
              (workflow_id, trigger_type, json.dumps(trigger, separators=(",", ":")), timezone, int(enabled), max(1, int(misfire_grace_seconds)), int(coalesce)))

    def list_active_schedules(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("""SELECT s.* FROM workflow_schedules s JOIN workflow_definitions d ON d.id=s.workflow_id
              WHERE s.enabled=1 AND d.status='active' AND d.active_version IS NOT NULL ORDER BY s.workflow_id""").fetchall()
        return [{"workflow_id": row["workflow_id"], "trigger_type": row["trigger_type"], "trigger": json.loads(row["trigger_json"]),
                 "timezone": row["timezone"], "misfire_grace_seconds": row["misfire_grace_seconds"],
                 "coalesce": bool(row["coalesce"]), "next_run_at": row["next_run_at"]} for row in rows]

    def update_schedule_state(self, workflow_id: str, *, last_scheduled_at: str | None = None, last_started_at: str | None = None, last_finished_at: str | None = None, next_run_at: str | None | object = _UNSET) -> None:
        updates = {"last_scheduled_at": last_scheduled_at, "last_started_at": last_started_at, "last_finished_at": last_finished_at, "next_run_at": next_run_at}
        assignments = [f"{key}=?" for key, value in updates.items() if value is not None and value is not _UNSET or key == "next_run_at" and value is None]
        values = [value for key, value in updates.items() if value is not None and value is not _UNSET or key == "next_run_at" and value is None]
        if assignments:
            with self._connect() as db:
                db.execute(f"UPDATE workflow_schedules SET {','.join(assignments)} WHERE workflow_id=?", (*values, workflow_id))

    def disable_schedule(self, workflow_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE workflow_schedules SET enabled=0,next_run_at=NULL WHERE workflow_id=?", (workflow_id,))

    def get_schedule(self, workflow_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM workflow_schedules WHERE workflow_id=?", (workflow_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["trigger"] = json.loads(result.pop("trigger_json"))
        result["enabled"] = bool(result["enabled"])
        result["coalesce"] = bool(result["coalesce"])
        return result

    def enable_schedule(self, workflow_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE workflow_schedules SET enabled=1 WHERE workflow_id=?", (workflow_id,))

    def list_runs(self, workflow_id: str, owner_user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            owned = db.execute("SELECT 1 FROM workflow_definitions WHERE id=? AND owner_user_id=?", (workflow_id, owner_user_id)).fetchone()
            if not owned:
                raise PermissionError("WORKFLOW_ACCESS_DENIED")
            rows = db.execute("SELECT * FROM workflow_runs WHERE workflow_id=? ORDER BY rowid DESC LIMIT ?", (workflow_id, max(1, min(int(limit), 500)))).fetchall()
        return [dict(row) for row in rows]

    def get_run(self, run_id: str, owner_user_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM workflow_runs WHERE id=? AND owner_user_id=?", (run_id, owner_user_id)).fetchone()
            if not row:
                return None
            nodes = db.execute("SELECT * FROM workflow_node_runs WHERE run_id=? ORDER BY started_at,node_id,attempt", (run_id,)).fetchall()
        result = dict(row)
        result["nodes"] = []
        for item in nodes:
            node = dict(item)
            node["input_summary"] = node.pop("safe_input_summary", "")
            node["output_summary"] = node.pop("safe_output_summary", "")
            result["nodes"].append(node)
        result["results"] = _run_results(result["nodes"])
        result["events"] = self.events_after(run_id)
        return result
