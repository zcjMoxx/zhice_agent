from __future__ import annotations

import json
from pathlib import Path

from agent.operations.runtime import (
    OperationsRuntimeState,
    clear_operations_runtime_state,
    load_operations_runtime_state,
    state_from_environment,
    write_operations_runtime_state,
)


def test_runtime_state_round_trip_and_owned_cleanup(tmp_path: Path):
    state = OperationsRuntimeState(
        mode="local_process",
        target_type="process",
        target_name="zcagent-gateway",
        url="http://127.0.0.1:17681",
        instance_id="owner-a",
        supervisor_pid=__import__("os").getpid(),
    )
    write_operations_runtime_state(tmp_path, state)

    loaded = load_operations_runtime_state(tmp_path)

    assert loaded is not None
    assert loaded.mode == "local_process"
    assert loaded.url == "http://127.0.0.1:17681"
    clear_operations_runtime_state(tmp_path, instance_id="other")
    assert (tmp_path / "operations.json").is_file()
    clear_operations_runtime_state(tmp_path, instance_id="owner-a")
    assert not (tmp_path / "operations.json").exists()


def test_runtime_state_rejects_stale_or_non_loopback_local_record(tmp_path: Path):
    (tmp_path / "operations.json").write_text(
        json.dumps(
            {
                "mode": "local_process",
                "target_type": "process",
                "target_name": "zcagent-gateway",
                "url": "http://0.0.0.0:17681",
                "instance_id": "stale",
                "supervisor_pid": 99999999,
            }
        ),
        encoding="utf-8",
    )

    assert load_operations_runtime_state(tmp_path) is None


def test_environment_projection_validates_mode_target_and_scheme(monkeypatch):
    monkeypatch.setenv("ZHICE_OPS_MODE", "server_docker")
    monkeypatch.setenv("ZHICE_OPS_URL", "https://ops.example.test")
    monkeypatch.setenv("ZHICE_OPS_TARGET_TYPE", "container")
    monkeypatch.setenv("ZHICE_OPS_TARGET_NAME", "zhice-agent")

    state = state_from_environment()

    assert state is not None
    assert state.mode == "server_docker"
    assert state.target_name == "zhice-agent"
    monkeypatch.setenv("ZHICE_OPS_URL", "http://ops.example.test")
    assert state_from_environment() is None
