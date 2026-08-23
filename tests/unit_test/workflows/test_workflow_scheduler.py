import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent.workflows.scheduler import WorkflowScheduler
from agent.workflows.schemas import WorkflowDefinitionV1, WorkflowEdge, WorkflowNode
from agent.workflows.store import WorkflowStore


def definition() -> WorkflowDefinitionV1:
    nodes = (WorkflowNode("trigger", "schedule_trigger"), WorkflowNode("result", "template", config={"template": "ok"}))
    edges = (WorkflowEdge("edge", "trigger", target_node_id="result"),)
    return WorkflowDefinitionV1("wf", "user", "workflow", nodes, edges)


def active_store(path: Path) -> WorkflowStore:
    store = WorkflowStore(path / "workflows.sqlite3")
    store.save_draft(definition())
    store.publish(definition())
    return store


def test_scheduler_rebuilds_fixed_job_and_prevents_second_instance(tmp_path: Path):
    store = active_store(tmp_path)
    store.upsert_schedule("wf", "interval", {"minutes": 5}, "Asia/Shanghai")
    scheduler = WorkflowScheduler(store, lambda *_: None, workspace=tmp_path)
    scheduler.start()
    try:
        assert scheduler.jobs() == ("workflow:wf",)
        other = WorkflowScheduler(store, lambda *_: None, workspace=tmp_path)
        with pytest.raises(RuntimeError, match="WORKFLOW_SCHEDULER_ALREADY_RUNNING"):
            other.start()
    finally:
        scheduler.shutdown()


def test_scheduler_supports_date_interval_cron_and_restart_recovery(tmp_path: Path):
    store = active_store(tmp_path)
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    store.upsert_schedule("wf", "date", {"run_at": future}, "UTC")
    first = WorkflowScheduler(store, lambda *_: None, workspace=tmp_path)
    first.start()
    assert first.jobs() == ("workflow:wf",)
    first.shutdown()

    store.upsert_schedule("wf", "cron", {"expression": "*/5 * * * *"}, "Asia/Shanghai")
    recovered = WorkflowScheduler(store, lambda *_: None, workspace=tmp_path)
    recovered.start()
    try:
        assert recovered.jobs() == ("workflow:wf",)
        assert WorkflowScheduler._build_trigger("interval", {"seconds": 30}, "UTC")
        with pytest.raises(ValueError, match="WORKFLOW_TIMEZONE_INVALID"):
            WorkflowScheduler._build_trigger("cron", {"expression": "0 8 * * *"}, "Mars/Olympus")
    finally:
        recovered.shutdown()


@pytest.mark.skipif(os.name != "nt", reason="Windows process probing regression")
def test_scheduler_recovers_stale_windows_process_lock(tmp_path: Path):
    lock_path = tmp_path / "state" / "workflow-scheduler.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(json.dumps({"pid": 2_147_483_647}), encoding="utf-8")
    scheduler = WorkflowScheduler(active_store(tmp_path), lambda *_: None, workspace=tmp_path)

    scheduler.start()
    try:
        lock_payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert lock_payload["pid"] == os.getpid()
        assert isinstance(lock_payload["process_created_at"], int)
    finally:
        scheduler.shutdown()


@pytest.mark.skipif(os.name != "nt", reason="Windows process identity regression")
def test_scheduler_recovers_reused_windows_process_id_lock(tmp_path: Path):
    lock_path = tmp_path / "state" / "workflow-scheduler.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    os.utime(lock_path, ns=(1_000_000_000, 1_000_000_000))
    scheduler = WorkflowScheduler(active_store(tmp_path), lambda *_: None, workspace=tmp_path)

    scheduler.start()
    try:
        lock_payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert lock_payload["pid"] == os.getpid()
        assert isinstance(lock_payload["process_created_at"], int)
    finally:
        scheduler.shutdown()
