"""Tests for the formal executable Skill runtime."""

from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from dataclasses import replace

import pytest

from agent.core.loop import CancellationToken
from agent.protocols.auth import ActorContext
from agent.protocols.skill import SkillRunRequest
from agent.skills.executor import PythonSkillExecutor
from agent.skills.loader import SkillLoader


class ProgressCollector:
    def __init__(self):
        self.items = []
        self.received = threading.Event()

    def emit(self, progress):
        self.items.append(progress)
        self.received.set()


def test_executor_streams_progress_and_validates_result(tmp_path):
    skill = _skill(
        tmp_path,
        """
import argparse, json
p=argparse.ArgumentParser(); p.add_argument('--params'); a=p.parse_args()
params=json.loads(a.params)
print(json.dumps({'type':'progress','message':'API_KEY=secret-value reading','percent':30}))
print(json.dumps({'type':'result','status':'success','code':'OK','data':params,'message':'done','error_stack':''}))
""",
    )
    collector = ProgressCollector()

    result = PythonSkillExecutor().run(
        _request(skill.qualified_name, {"city": "上海"}),
        skill,
        progress_sink=collector,
    )

    assert result.status == "success"
    assert result.data == {"city": "上海"}
    assert collector.items[0].percent == 30
    assert "secret-value" not in collector.items[0].message


def test_executor_delivers_flushed_progress_before_process_completion(tmp_path):
    skill = _skill(
        tmp_path,
        """
import json, time
print(json.dumps({'type':'progress','message':'started','percent':5}), flush=True)
time.sleep(1.5)
print(json.dumps({'type':'result','status':'success','code':'OK','data':{},'message':'done','error_stack':''}), flush=True)
""",
    )
    collector = ProgressCollector()
    results = []
    worker = threading.Thread(
        target=lambda: results.append(
            PythonSkillExecutor().run(
                _request(skill.qualified_name, {}),
                skill,
                progress_sink=collector,
            )
        )
    )

    worker.start()
    assert collector.received.wait(1.0), "flushed progress was buffered until process exit"
    assert worker.is_alive()
    worker.join(timeout=4)

    assert results[0].status == "success"


def test_executor_supports_legacy_last_line_result_without_fake_progress(tmp_path):
    skill = _skill(
        tmp_path,
        """
import json
print('bounded internal log')
print(json.dumps({'status':'success','code':'OK','data':{},'message':'done','error_stack':''}))
""",
    )
    collector = ProgressCollector()

    result = PythonSkillExecutor().run(
        _request(skill.qualified_name, {}),
        skill,
        progress_sink=collector,
    )

    assert result.status == "success"
    assert collector.items == []
    assert result.metadata["non_json_lines"] == 1


def test_executor_rejects_output_after_result_and_output_overflow(tmp_path):
    after_result = _skill(
        tmp_path / "after",
        """
import json
print(json.dumps({'type':'result','status':'success','code':'OK','data':{},'message':'done','error_stack':''}))
print('extra')
""",
    )
    overflow = _skill(tmp_path / "overflow", "print('x' * 10000)\n")

    invalid = PythonSkillExecutor().run(_request(after_result.qualified_name, {}), after_result)
    too_large = PythonSkillExecutor(max_stdout_bytes=128).run(
        _request(overflow.qualified_name, {}), overflow
    )

    assert invalid.code == "SKILL_PROTOCOL_ERROR"
    assert too_large.code == "SKILL_STDOUT_LIMIT"


@pytest.mark.parametrize(
    ("script", "executor", "expected_code"),
    [
        (
            """
import json
print(json.dumps({'type':'progress','message':'bad','percent':True}))
""",
            PythonSkillExecutor(),
            "SKILL_PROTOCOL_ERROR",
        ),
        (
            """
import json
print(json.dumps({'type':'result','status':'success','code':7,'data':{},'message':'done'}))
""",
            PythonSkillExecutor(),
            "SKILL_RESULT_INVALID",
        ),
        (
            """
import sys
sys.stderr.write('x' * 10000)
""",
            PythonSkillExecutor(max_stderr_bytes=128),
            "SKILL_STDERR_LIMIT",
        ),
        (
            """
print('one')
print('two')
print('three')
""",
            PythonSkillExecutor(max_stdout_lines=2),
            "SKILL_OUTPUT_LINE_LIMIT",
        ),
    ],
)
def test_executor_rejects_invalid_progress_result_and_bounded_output(
    tmp_path,
    script,
    executor,
    expected_code,
):
    skill = _skill(tmp_path, script)

    result = executor.run(_request(skill.qualified_name, {}), skill)

    assert result.code == expected_code


def test_executor_accepts_internal_logs_before_typed_result(tmp_path):
    skill = _skill(
        tmp_path,
        """
import json
print('internal log only')
print(json.dumps({'type':'result','status':'success','code':'OK','data':{},'message':'done','error_stack':''}))
""",
    )

    result = PythonSkillExecutor().run(_request(skill.qualified_name, {}), skill)

    assert result.status == "success"
    assert result.metadata["non_json_lines"] == 1


def test_executor_validates_param_shape_size_depth_and_declared_schema(tmp_path):
    skill = _skill(tmp_path, "print('unused')")
    executable = replace(
        skill.executable,
        params_schema={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    )
    skill = replace(skill, executable=executable)
    too_deep = value = {}
    for _ in range(22):
        value["child"] = {}
        value = value["child"]

    wrong_schema = PythonSkillExecutor().run(_request(skill.qualified_name, {}), skill)
    unserializable = PythonSkillExecutor().run(
        _request(skill.qualified_name, {"city": {1, 2}}), skill
    )
    oversized = PythonSkillExecutor(max_params_bytes=16).run(
        _request(skill.qualified_name, {"city": "x" * 100}), skill
    )
    nested = PythonSkillExecutor().run(_request(skill.qualified_name, too_deep), skill)

    assert {wrong_schema.code, unserializable.code, oversized.code, nested.code} == {
        "INVALID_SKILL_PARAMS"
    }


def test_executor_rechecks_entrypoint_after_loader_to_block_symlink_swap(tmp_path):
    skill = _skill(tmp_path, "print('unused')")
    outside = tmp_path / "outside.py"
    outside.write_text("print('escaped')\n", encoding="utf-8")
    entrypoint = skill.executable.entrypoint
    entrypoint.unlink()
    try:
        entrypoint.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    result = PythonSkillExecutor().run(_request(skill.qualified_name, {}), skill)

    assert result.code == "INVALID_SKILL_ENTRYPOINT"


def test_executor_cleans_up_descendants_after_success(tmp_path):
    skill = _skill(
        tmp_path,
        """
import json, subprocess, sys
child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
print(json.dumps({'type':'result','status':'success','code':'OK','data':{'child_pid':child.pid},'message':'done','error_stack':''}), flush=True)
""",
    )

    result = PythonSkillExecutor().run(_request(skill.qualified_name, {}), skill)
    child_pid = result.data["child_pid"]

    deadline = time.monotonic() + 3
    while _process_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _process_exists(child_pid)


def test_executor_timeout_and_cancellation_terminate_execution(tmp_path):
    skill = _skill(tmp_path, "import time\ntime.sleep(10)\n", timeout=10)
    timed_out = PythonSkillExecutor().run(
        _request(skill.qualified_name, {}, timeout=1),
        skill,
    )
    token = CancellationToken()
    timer = threading.Timer(0.1, token.cancel)
    timer.start()
    try:
        cancelled = PythonSkillExecutor().run(
            _request(skill.qualified_name, {}, token=token),
            skill,
        )
    finally:
        timer.cancel()

    assert timed_out.code == "SKILL_TIMEOUT"
    assert cancelled.status == "cancelled"
    assert cancelled.code == "SKILL_CANCELLED"


def _skill(tmp_path, script, *, timeout=5):
    skills_dir = tmp_path / "skills"
    root = skills_dir / "demo"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "main.py").write_text(script.strip() + "\n", encoding="utf-8")
    (root / "SKILL.md").write_text(
        f"""---
name: demo
description: Demo.
runtime:
  type: python
  entrypoint: scripts/main.py
  protocol: ndjson-v1
  timeout_seconds: {timeout}
---

Demo.
""",
        encoding="utf-8",
    )
    return SkillLoader([("official", skills_dir)]).list_skills()[0]


def _request(name, params, *, timeout=None, token=None):
    return SkillRunRequest(
        run_id="run-1",
        qualified_name=name,
        params=params,
        actor_context=ActorContext(
            actor_type="user",
            user_id="owner",
            username="owner",
            display_name="Owner",
            role_keys=frozenset({"owner"}),
            permission_keys=frozenset(),
            channel="web",
        ),
        session_id="session-1",
        turn_id="turn-1",
        timeout_seconds=timeout,
        cancellation_token=token,
    )


def _process_exists(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)
