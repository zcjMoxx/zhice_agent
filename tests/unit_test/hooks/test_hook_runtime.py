"""Tests for configured workspace Tool Hooks."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import signal
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from agent.hooks.config import HookConfigurationError
from agent.hooks.loader import load_hook_registry
from agent.hooks.runner import HookProcessRunner, _minimal_hook_environment
from agent.hooks.runtime import ConfiguredHookRuntime
from agent.protocols.hook import PostToolHookRequest, PreToolHookRequest


def _entry(name: str, stage: str) -> dict[str, object]:
    return {
        "name": name,
        "stage": stage,
        "script": "extends/hooks/hook_fixture.py",
        "tools": ["*"],
    }


def test_missing_hook_config_returns_empty_registry(tmp_path):
    registry = load_hook_registry(tmp_path / "config" / "hooks.yml", workspace=tmp_path)

    assert registry.hooks == ()


def test_hook_loader_registers_ordered_workspace_scripts(tmp_path):
    config_path = _write_config(tmp_path, [_entry("continue", "pre_tooluse"), _entry("enrich", "post_tooluse")])

    registry = load_hook_registry(config_path, workspace=tmp_path)

    assert [hook.name for hook in registry.hooks] == ["continue", "enrich"]
    assert registry.select("pre_tooluse", "read_file")[0].name == "continue"


def test_hook_loader_accepts_exact_matchers_and_standalone_wildcard(tmp_path):
    exact = {**_entry("exact", "pre_tooluse"), "tools": ["read_file"]}
    wildcard = _entry("wildcard", "pre_tooluse")
    config_path = _write_config(tmp_path, [exact, wildcard])

    registry = load_hook_registry(config_path, workspace=tmp_path)

    assert [hook.name for hook in registry.select("pre_tooluse", "read_file")] == [
        "exact",
        "wildcard",
    ]
    assert [hook.name for hook in registry.select("pre_tooluse", "exec")] == ["wildcard"]


def test_hook_loader_parses_and_deduplicates_exempt_roles(tmp_path):
    config_path = _write_config(
        tmp_path,
        [
            {
                **_entry("scoped", "pre_tooluse"),
                "exempt_roles": ["owner", "admin", "owner"],
                "exempt_permissions": ["auth.users.manage", "auth.users.manage"],
            },
            {
                **_entry("empty-scope", "post_tooluse"),
                "exempt_roles": [],
                "exempt_permissions": [],
            },
        ],
    )

    registry = load_hook_registry(config_path, workspace=tmp_path)

    assert registry.hooks[0].exempt_roles == ("owner", "admin")
    assert registry.hooks[0].exempt_permissions == ("auth.users.manage",)
    assert registry.hooks[1].exempt_roles == ()
    assert registry.hooks[1].exempt_permissions == ()


@pytest.mark.parametrize(
    "exempt_roles",
    ["owner", ["bad*role"], [""], [None]],
)
def test_hook_loader_rejects_invalid_exempt_roles(tmp_path, exempt_roles):
    config_path = _write_config(
        tmp_path,
        [{**_entry("bad-role", "pre_tooluse"), "exempt_roles": exempt_roles}],
    )

    with pytest.raises(HookConfigurationError):
        load_hook_registry(config_path, workspace=tmp_path)


@pytest.mark.parametrize(
    "exempt_permissions",
    ["auth.users.manage", ["bad*permission"], [""], [None]],
)
def test_hook_loader_rejects_invalid_exempt_permissions(tmp_path, exempt_permissions):
    config_path = _write_config(
        tmp_path,
        [
            {
                **_entry("bad-permission", "pre_tooluse"),
                "exempt_permissions": exempt_permissions,
            }
        ],
    )

    with pytest.raises(HookConfigurationError):
        load_hook_registry(config_path, workspace=tmp_path)


@pytest.mark.parametrize("matcher", ["read_*", "foo*bar", "*read"])
def test_hook_loader_rejects_partial_tool_wildcards(tmp_path, matcher):
    config_path = _write_config(
        tmp_path,
        [{**_entry("partial", "pre_tooluse"), "tools": [matcher]}],
    )

    with pytest.raises(HookConfigurationError, match="Invalid Hook tool matcher"):
        load_hook_registry(config_path, workspace=tmp_path)


@pytest.mark.parametrize(
    "entries",
    [
        [_entry("same", "pre_tooluse"), _entry("same", "post_tooluse")],
        [_entry("bad-stage", "unknown")],
        [{**_entry("bad-limit", "pre_tooluse"), "timeout_seconds": 99}],
    ],
)
def test_hook_loader_rejects_invalid_config(tmp_path, entries):
    config_path = _write_config(tmp_path, entries)

    with pytest.raises(HookConfigurationError):
        load_hook_registry(config_path, workspace=tmp_path)


def test_hook_loader_rejects_script_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("print('{}')", encoding="utf-8")
    config_path = workspace / "config" / "hooks.yml"
    config_path.parent.mkdir()
    config_path.write_text(
        f"version: 1\nhooks:\n  - name: outside\n    stage: pre_tooluse\n    script: '{outside.as_posix()}'\n    tools: ['*']\n",
        encoding="utf-8",
    )

    with pytest.raises(HookConfigurationError, match="outside workspace"):
        load_hook_registry(config_path, workspace=workspace)


@pytest.mark.parametrize(
    ("name", "expected_action", "expected_code"),
    [
        ("continue", "continue", ""),
        ("block", "block", "BUSINESS_BLOCKED"),
        ("modify", "modify", ""),
        ("timeout", "block", "HOOK_TIMEOUT"),
        ("invalid-json", "block", "HOOK_INVALID_OUTPUT"),
        ("invalid-fields", "block", "HOOK_INVALID_OUTPUT"),
        ("oversize", "block", "HOOK_OUTPUT_LIMIT"),
        ("exception", "block", "HOOK_PROCESS_FAILED"),
    ],
)
def test_real_pre_hook_fixture_actions_and_fail_closed(tmp_path, name, expected_action, expected_code):
    config_path = _write_config(
        tmp_path,
        [
            {
                **_entry(name, "pre_tooluse"),
                "timeout_seconds": 0.1 if name == "timeout" else 2,
                "max_output_chars": 1024 if name == "oversize" else 16384,
            }
        ],
    )
    runtime = _runtime(tmp_path, config_path)

    result = runtime.run_pre_tooluse(_pre_request())

    assert result.action == expected_action
    assert result.code == expected_code
    if name == "modify":
        assert result.arguments["path"] == "allowed.txt"


def test_real_post_hook_fixture_enriches_safe_presentation(tmp_path):
    config_path = _write_config(tmp_path, [_entry("enrich", "post_tooluse")])
    runtime = _runtime(tmp_path, config_path)

    result = runtime.run_post_tooluse(_post_request())

    assert result.display["title"] == "fixture enriched"
    assert result.ui_metadata["detail_type"] == "summary"


@pytest.mark.parametrize("name", ["timeout", "invalid-json", "invalid-fields", "exception"])
def test_post_hook_failures_are_ignored(tmp_path, name):
    config_path = _write_config(
        tmp_path,
        [{**_entry(name, "post_tooluse"), "timeout_seconds": 0.1 if name == "timeout" else 2}],
    )
    runtime = _runtime(tmp_path, config_path)

    result = runtime.run_post_tooluse(_post_request())

    assert result.display == {}
    assert result.ui_metadata == {}


def test_hook_environment_excludes_business_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.test")

    environment = _minimal_hook_environment(tmp_path)

    assert environment["ZHICE_AGENT_WORKSPACE"] == str(tmp_path)
    assert "OPENAI_API_KEY" not in environment
    assert "HTTPS_PROXY" not in environment


def test_pre_hook_role_exemption_skips_only_matching_hook(tmp_path, caplog):
    config_path = _write_config(
        tmp_path,
        [
            {**_entry("owner-block", "pre_tooluse"), "exempt_roles": ["owner"]},
            _entry("always-continue", "pre_tooluse"),
        ],
    )
    runner = _RecordingRunner(
        {
            "owner-block": {
                "action": "block",
                "code": "OWNER_BLOCKED",
                "message": "owner should have skipped this Hook",
            },
            "always-continue": {"action": "continue"},
        }
    )
    runtime = ConfiguredHookRuntime(
        load_hook_registry(config_path, workspace=tmp_path),
        runner,
    )
    caplog.set_level("DEBUG", logger="zcagent.agent.hook")

    result = runtime.run_pre_tooluse(_pre_request(role_keys=("owner",)))

    assert result.action == "continue"
    assert [name for name, _payload in runner.calls] == ["always-continue"]
    assert "hook.skipped" in caplog.text
    skipped = next(record for record in caplog.records if record.message == "hook.skipped")
    assert skipped.fields["reason"] == "role_exempted"
    assert skipped.fields["matched_key"] == "owner"


@pytest.mark.parametrize("role_keys", [("admin",), ()])
def test_pre_hook_without_matching_exemption_still_runs(tmp_path, role_keys):
    config_path = _write_config(
        tmp_path,
        [{**_entry("owner-block", "pre_tooluse"), "exempt_roles": ["owner"]}],
    )
    runner = _RecordingRunner(
        {
            "owner-block": {
                "action": "block",
                "code": "BUSINESS_BLOCKED",
                "message": "not exempt",
            }
        }
    )
    runtime = ConfiguredHookRuntime(
        load_hook_registry(config_path, workspace=tmp_path),
        runner,
    )

    result = runtime.run_pre_tooluse(_pre_request(role_keys=role_keys))

    assert result.action == "block"
    assert [name for name, _payload in runner.calls] == ["owner-block"]


def test_admin_exemption_uses_effective_permissions_not_role_name(tmp_path, caplog):
    config_path = _write_config(
        tmp_path,
        [
            {
                **_entry("manage-users", "pre_tooluse"),
                "exempt_permissions": ["auth.users.manage"],
            }
        ],
    )
    runner = _RecordingRunner(
        {
            "manage-users": {
                "action": "block",
                "code": "BUSINESS_BLOCKED",
                "message": "permission required",
            }
        }
    )
    runtime = ConfiguredHookRuntime(
        load_hook_registry(config_path, workspace=tmp_path),
        runner,
    )
    caplog.set_level("DEBUG", logger="zcagent.agent.hook")

    allowed = runtime.run_pre_tooluse(
        _pre_request(
            role_keys=("admin",),
            permission_keys=("auth.users.manage",),
        )
    )
    denied = runtime.run_pre_tooluse(_pre_request(role_keys=("admin",)))

    assert allowed.action == "continue"
    assert denied.action == "block"
    assert [name for name, _payload in runner.calls] == ["manage-users"]
    skipped = next(record for record in caplog.records if record.message == "hook.skipped")
    assert skipped.fields["reason"] == "permission_exempted"
    assert skipped.fields["matched_key"] == "auth.users.manage"


def test_post_hook_uses_same_role_exemption_and_payload_context(tmp_path):
    config_path = _write_config(
        tmp_path,
        [{**_entry("enrich", "post_tooluse"), "exempt_roles": ["owner"]}],
    )
    runner = _RecordingRunner(
        {
            "enrich": {
                "action": "enrich",
                "display": {"title": "admin enrichment"},
            }
        }
    )
    runtime = ConfiguredHookRuntime(
        load_hook_registry(config_path, workspace=tmp_path),
        runner,
    )

    owner_result = runtime.run_post_tooluse(_post_request(role_keys=("owner",)))
    admin_result = runtime.run_post_tooluse(
        _post_request(
            role_keys=("admin",),
            permission_keys=("auth.users.read",),
        )
    )

    assert owner_result.display == {}
    assert admin_result.display["title"] == "admin enrichment"
    assert [name for name, _payload in runner.calls] == ["enrich"]
    assert runner.calls[0][1]["context"]["role_keys"] == ["admin"]
    assert runner.calls[0][1]["context"]["permission_keys"] == ["auth.users.read"]


def test_hook_timeout_reclaims_parent_and_spawned_child_processes(tmp_path):
    config_path = _write_config(
        tmp_path,
        [{**_entry("spawn-timeout", "pre_tooluse"), "timeout_seconds": 1}],
    )
    runtime = _runtime(tmp_path, config_path)
    pids: list[int] = []
    try:
        result = runtime.run_pre_tooluse(_pre_request())
        pid_data = json.loads((tmp_path / "hook-tree-pids.json").read_text(encoding="utf-8"))
        pids = [pid_data["parent"], pid_data["child"]]

        assert result.action == "block"
        assert result.code == "HOOK_TIMEOUT"
        assert _wait_until_processes_exit(pids)
    finally:
        for pid in pids:
            _force_kill_process(pid)


def _runtime(workspace: Path, config_path: Path) -> ConfiguredHookRuntime:
    registry = load_hook_registry(config_path, workspace=workspace)
    return ConfiguredHookRuntime(registry, HookProcessRunner(workspace))


def _pre_request(
    *,
    role_keys: tuple[str, ...] = (),
    permission_keys: tuple[str, ...] = (),
) -> PreToolHookRequest:
    return PreToolHookRequest(
        tool_name="read_file",
        arguments={"path": "before.txt"},
        session_id="session-1",
        turn_id="turn-1",
        channel="web",
        actor_type="user",
        role_keys=role_keys,
        permission_keys=permission_keys,
    )


def _post_request(
    *,
    role_keys: tuple[str, ...] = (),
    permission_keys: tuple[str, ...] = (),
) -> PostToolHookRequest:
    return PostToolHookRequest(
        tool_name="read_file",
        arguments={"path": "a.txt"},
        output="content",
        is_error=False,
        result_metadata={"path": "a.txt"},
        session_id="session-1",
        turn_id="turn-1",
        channel="web",
        actor_type="user",
        role_keys=role_keys,
        permission_keys=permission_keys,
    )


def _write_config(workspace: Path, entries: list[dict[str, object]]) -> Path:
    script_dir = workspace / "extends" / "hooks"
    script_dir.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).parent / "fixtures" / "hook_fixture.py"
    shutil.copyfile(source, script_dir / "hook_fixture.py")
    config_path = workspace / "config" / "hooks.yml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    import yaml

    config_path.write_text(
        yaml.safe_dump({"version": 1, "hooks": entries}, sort_keys=False),
        encoding="utf-8",
    )
    return config_path


class _RecordingRunner:
    def __init__(self, outputs: dict[str, dict[str, object]]):
        self.outputs = outputs
        self.calls: list[tuple[str, dict[str, object]]] = []

    def run(self, spec, payload):
        self.calls.append((spec.name, payload))
        return self.outputs[spec.name]


def _wait_until_processes_exit(pids: list[int], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(_process_exists(pid) for pid in pids):
            return True
        time.sleep(0.05)
    return not any(_process_exists(pid) for pid in pids)


def _process_exists(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    kernel32 = _windows_kernel32()
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == 259
    finally:
        kernel32.CloseHandle(handle)


def _force_kill_process(pid: int) -> None:
    if not _process_exists(pid):
        return
    if os.name != "nt":
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return
    kernel32 = _windows_kernel32()
    handle = kernel32.OpenProcess(0x0001, False, pid)
    if handle:
        try:
            kernel32.TerminateProcess(handle, 1)
        finally:
            kernel32.CloseHandle(handle)


def _windows_kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32
