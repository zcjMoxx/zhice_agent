"""Node-backed tests for the browser RuntimeEvent state reducer."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_frontend_runtime_event_reducer_orders_and_clears_status():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_path = Path("web/static/runtime-event-state.js").resolve()
    script = f"""
const {{ applyRuntimeEvent }} = require({json.dumps(str(module_path))});
const active = {{ sessionId: 'alpha', turnId: 'turn-1' }};
const pending = {{ runtimeStatus: '已接收问题', runtimeSequence: 0, runtimeEvents: [] }};
const event = (type, sequence, title) => ({{
  session_id: 'alpha', turn_id: 'turn-1',
  data: {{ type, sequence, turn_id: 'turn-1', display: {{ title }} }}
}});
const applied = applyRuntimeEvent(active, pending, event('llm.started', 4, '正在请求模型'));
const stale = applyRuntimeEvent(active, pending, event('context.started', 3, '旧状态'));
const terminal = applyRuntimeEvent(active, pending, event('turn.completed', 5, '已完成'));
process.stdout.write(JSON.stringify({{ applied, stale, terminal, pending }}));
"""

    completed = subprocess.run(  # noqa: S603 - fixed local node executable and inline test code.
        [node, "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    result = json.loads(completed.stdout)

    assert result["applied"] is True
    assert result["stale"] is False
    assert result["terminal"] is True
    assert result["pending"]["runtimeSequence"] == 5
    assert result["pending"]["runtimeStatus"] == ""
    assert len(result["pending"]["runtimeEvents"]) == 2
