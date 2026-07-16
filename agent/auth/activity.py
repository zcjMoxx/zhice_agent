"""SQLite runtime activity sink for turn and tool query indexes."""

from __future__ import annotations

import json
from dataclasses import replace

from agent.auth.audit import sanitize_metadata
from agent.auth.store import SQLiteAuthStore
from agent.protocols.activity import RuntimeActivityEvent


class SqliteRuntimeActivitySink:
    """Persist runtime activity without adding rows to the security audit ledger."""

    def __init__(self, store: SQLiteAuthStore):
        self.store = store

    def record(self, event: RuntimeActivityEvent) -> None:
        """Update the structured runtime indexes."""

        safe_metadata = sanitize_metadata(event.metadata)
        encoded = json.dumps(safe_metadata, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > 4000:
            safe_metadata = {"preview": encoded[:3988] + "[truncated]"}
        self.store.record_activity(replace(event, metadata=safe_metadata))
