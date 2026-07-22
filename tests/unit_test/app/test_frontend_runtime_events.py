"""Node-backed tests for the browser RuntimeEvent state reducer."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_frontend_math_renderer_supports_common_latex_and_safe_fallback():
    script = Path("web/static/app.js").read_text(encoding="utf-8")
    styles = Path("web/static/styles.css").read_text(encoding="utf-8")
    index = Path("web/static/index.html").read_text(encoding="utf-8")

    assert 'const KATEX_VERSION = "0.16.11"' in script
    assert "cdn.jsdelivr.net/npm/katex@" in script
    assert '{ left: "$$", right: "$$", display: true }' in script
    assert '{ left: "\\\\(", right: "\\\\)", display: false }' in script
    assert 'macros: { "\\\\bm": "\\\\boldsymbol{#1}" }' in script
    assert 'ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code"]' in script
    assert "throwOnError: false" in script
    assert "trust: false" in script
    assert ".bubble.markdown .katex-display" in styles
    assert "overflow-x: auto" in styles
    assert "/static/app.js?v=20260722-math-rendering" in index
    assert "/static/styles.css?v=20260722-math-rendering" in index


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


def test_frontend_runtime_event_reducer_tracks_child_sequences_independently():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_path = Path("web/static/runtime-event-state.js").resolve()
    script = f"""
const {{ applyRuntimeEvent }} = require({json.dumps(str(module_path))});
const active = {{ sessionId: 'alpha', turnId: 'root-turn' }};
const pending = {{ runtimeStatus: '', runtimeEvents: [] }};
const child = (taskId, sequence, type, title) => ({{
  session_id: 'child-session',
  data: {{
    type, sequence, turn_id: `child-${{taskId}}`, root_session_id: 'alpha',
    root_turn_id: 'root-turn', agent_id: `agent-${{taskId}}`, task_id: taskId,
    display: {{ title }}
  }}
}});
const first = applyRuntimeEvent(active, pending, child('implementation', 3, 'tool.started', '读取代码'));
const second = applyRuntimeEvent(active, pending, child('tests', 1, 'llm.started', '运行测试'));
const done = applyRuntimeEvent(active, pending, child('implementation', 4, 'turn.completed', '已完成'));
process.stdout.write(JSON.stringify({{ first, second, done, pending }}));
"""
    completed = subprocess.run(
        [node, "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    result = json.loads(completed.stdout)

    assert result["first"] is True
    assert result["second"] is True
    assert result["done"] is True
    assert result["pending"]["subagentTasks"]["implementation"]["status"] == "completed"
    assert result["pending"]["subagentTasks"]["tests"]["status"] == "running"
    assert result["pending"]["runtimeStatus"] == "并行子任务 1/2 已完成"
