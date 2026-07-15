from __future__ import annotations

import json
from datetime import datetime

from agent.auth.diagnostics import RecentActivityDiagnostics
from agent.auth.store import SQLiteAuthStore


def test_recent_activity_diagnostics_reads_only_current_user_trace(tmp_path):
    store = SQLiteAuthStore(tmp_path / "state" / "auth.sqlite3")
    owner = store.initialize_owner("owner", "Owner", "password-123")
    viewer = store.create_user("viewer", "Viewer", "viewer-password", role_keys=["viewer"])
    owner_actor = store.actor_for_user(owner.id, channel="web")
    viewer_actor = store.actor_for_user(viewer.id, channel="web")
    store.session_index_create(
        session_id="owner-session",
        owner_user_id=owner.id,
        channel="web",
    )
    store.session_index_create(
        session_id="viewer-session",
        owner_user_id=viewer.id,
        channel="web",
    )
    trace_path = tmp_path / "logs" / datetime.now().strftime("%Y-%m-%d") / "trace.log"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": datetime.now().astimezone().isoformat(),
                        "level": "ERROR",
                        "component": "agent",
                        "event": "tool.done",
                        "actor_user_id": owner.id,
                        "session_id": "owner-session",
                        "turn_id": "owner-turn",
                        "tool": "exec",
                        "ok": False,
                        "output_preview": "owner failure",
                        "args_preview": "secret owner args",
                    }
                ),
                json.dumps(
                    {
                        "ts": datetime.now().astimezone().isoformat(),
                        "level": "ERROR",
                        "component": "agent",
                        "event": "tool.done",
                        "actor_user_id": viewer.id,
                        "session_id": "viewer-session",
                        "turn_id": "viewer-turn",
                        "tool": "exec",
                        "ok": False,
                        "output_preview": "viewer failure",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    owner_report = RecentActivityDiagnostics(store, tmp_path / "logs").diagnose(
        owner_actor, {"minutes": 30}
    )
    viewer_report = RecentActivityDiagnostics(store, tmp_path / "logs").diagnose(
        viewer_actor, {"minutes": 30}
    )

    assert [event["turn_id"] for event in owner_report["trace_events"]] == ["owner-turn"]
    assert [event["turn_id"] for event in viewer_report["trace_events"]] == ["viewer-turn"]
    assert "args_preview" not in owner_report["trace_events"][0]
    assert owner_report["failure_candidates"][0]["output_preview"] == "owner failure"
