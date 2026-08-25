"""Tests for local runtime initialization from the CLI."""

import builtins
import json
import os
from pathlib import Path

import pytest
import yaml

from agent.app.logging import GatewayLogOptions
from agent.cli import _resolve_preferred_endpoint, main
from agent.protocols.llm import LLMEndpoint


def _write_models(config_dir, chat, routing=None):
    config_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "routing": {"chat": next(iter(chat))} if routing is None else routing,
        "chat": chat,
        "embedding": {},
    }
    config_dir.joinpath("models.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_config_section(config_dir, section, value):
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "config.yml"
    root = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    root = root or {}
    root.setdefault("schema_version", 1)
    root[section] = value
    path.write_text(yaml.safe_dump(root, sort_keys=False), encoding="utf-8")


def test_cli_init_generates_runtime_files(tmp_path, capsys, monkeypatch):
    """zcagent init should create local files in the selected workspace."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = main(["init", "--workspace", str(tmp_path)])

    output = capsys.readouterr().out
    assert result == 0
    assert "created:" in output
    assert "Runtime files created" in output
    assert "Runtime files created from repository examples" in output
    assert "Edit config/models.json and config/.env before chatting" in output
    assert "Extension capabilities are optional" in output
    assert "context_window" not in output
    assert "api_key" not in output
    assert not (tmp_path / ".env").exists()
    assert (tmp_path / "config" / ".env").read_text(encoding="utf-8") == (
        Path(__file__).resolve().parents[3] / "config" / ".env.example"
    ).read_text(encoding="utf-8")
    models_path = tmp_path / "config" / "models.json"
    models_example = Path(__file__).resolve().parents[3] / "config" / "models.example.json"
    assert models_path.read_bytes() == models_example.read_bytes()
    payload = json.loads(models_path.read_text(encoding="utf-8"))
    assert "请填写端点名称" in payload["chat"]
    assert payload["chat"]["请填写端点名称"]["provider"] == ""
    assert (tmp_path / "config" / "config.yml").is_file()
    assert (tmp_path / "prompts" / "identity.md").is_file()
    assert (tmp_path / "prompts" / "diagnostics.md").is_file()
    assert (tmp_path / "prompts" / "exec.md").is_file()


def test_cli_init_preserves_existing_files_and_fills_missing(tmp_path, capsys, monkeypatch):
    """Existing user config should be preserved while missing runtime files are added."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / ".env").write_text("EXISTING=1\n", encoding="utf-8")

    result = main(["init", "--workspace", str(tmp_path), "--write-env"])

    output = capsys.readouterr().out
    assert result == 0
    assert "created:" in output
    assert (config_dir / ".env").read_text(encoding="utf-8") == "EXISTING=1\n"
    assert (tmp_path / "config" / "models.json").is_file()
    assert (tmp_path / "config" / "config.yml").is_file()
    assert (tmp_path / "prompts" / "identity.md").is_file()


def test_cli_init_write_env_is_compatibility_no_op_and_force_still_overwrites(
    tmp_path, capsys, monkeypatch
):
    _clear_zhice_env(monkeypatch)
    plain = tmp_path / "plain"
    compatibility = tmp_path / "compatibility"

    assert main(["init", "--workspace", str(plain)]) == 0
    capsys.readouterr()
    assert main(["init", "--workspace", str(compatibility), "--write-env"]) == 0
    capsys.readouterr()
    assert (plain / "config" / ".env").read_bytes() == (
        compatibility / "config" / ".env"
    ).read_bytes()

    env_path = compatibility / "config" / ".env"
    env_path.write_text("EXISTING=1\n", encoding="utf-8")
    assert main(["init", "--workspace", str(compatibility), "--write-env", "--force"]) == 0
    capsys.readouterr()
    assert "EXISTING=1" not in env_path.read_text(encoding="utf-8")


def test_cli_init_reports_when_everything_already_exists(tmp_path, capsys, monkeypatch):
    """Rerunning init should be a harmless no-op when all requested files exist."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.chdir(tmp_path)

    first = main(["init", "--workspace", str(tmp_path)])
    capsys.readouterr()
    second = main(["init", "--workspace", str(tmp_path)])

    output = capsys.readouterr().out
    assert first == 0
    assert second == 0
    assert "already exist" in output


def test_cli_init_uses_explicit_env_file_workspace(tmp_path, capsys, monkeypatch):
    """zcagent init should load an explicit env file when provided."""

    _clear_zhice_env(monkeypatch)
    workspace = tmp_path / "runtime"
    env_file = tmp_path / "project.env"
    env_file.write_text(
        f"ZHICE_AGENT_WORKSPACE={workspace}\n",
        encoding="utf-8",
    )

    result = main(["--env-file", str(env_file), "init"])

    output = capsys.readouterr().out
    assert result == 0
    assert "created:" in output
    assert not (workspace / ".env").exists()
    assert (workspace / "config" / ".env").is_file()
    assert (workspace / "config" / "models.json").is_file()
    assert (workspace / "config" / "config.yml").is_file()


def test_cli_init_uses_default_home_workspace_when_no_env_exists(
    tmp_path, capsys, monkeypatch
):
    """A clean install should initialize ~/.zhice without a bootstrap env."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("agent.config.Path.home", lambda: tmp_path)

    result = main(["--env-file", str(tmp_path / "missing.env"), "init"])

    output = capsys.readouterr().out
    assert result == 0
    assert "created:" in output
    assert (tmp_path / ".zhice" / "config" / ".env").is_file()
    assert (tmp_path / ".zhice" / "config" / "models.json").is_file()


def test_cli_gateway_check_uses_configured_workspace(tmp_path, capsys, monkeypatch):
    """zcagent gateway should be a first-class subcommand."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("ZHICE_OPS_MODE", "local_docker")

    result = main(["gateway", "--check", "--port", "19000"])

    output = capsys.readouterr().out
    assert result == 0
    assert "ZhiCe-Agent gateway check ok" in output
    assert "http://127.0.0.1:19000" in output
    assert str(tmp_path.resolve()) in output


def test_cli_gateway_check_rejects_partial_mcp_configuration(
    tmp_path,
    capsys,
    monkeypatch,
):
    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("ZHICE_OPS_MODE", "local_docker")
    _write_config_section(
        tmp_path / "config",
        "mcp",
        {
            "servers": {
                "valid": {"command": "python"},
                "invalid": {"command": "python", "cwd": "../outside"},
            }
        },
    )

    result = main(["gateway", "--check"])

    output = capsys.readouterr().out
    assert result == 1
    assert "MCP_CONFIG_PARTIAL" in output
    assert '"invalid_server_ids": [' in output
    assert "outside" not in output
    assert "ZhiCe-Agent gateway check ok" not in output


def test_cli_gateway_check_rejects_invalid_site_configuration(
    tmp_path,
    capsys,
    monkeypatch,
):
    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("ZHICE_OPS_MODE", "local_docker")
    _write_config_section(
        tmp_path / "config",
        "site",
        {
            "public_security_record": {
                "enabled": True,
                "code": "invalid",
                "label": "private-value-must-not-leak",
                "allowed_hosts": ["example.test"],
            }
        },
    )

    result = main(["gateway", "--check"])

    output = capsys.readouterr().out
    assert result == 1
    assert "Site configuration is invalid" in output
    assert "private-value-must-not-leak" not in output
    assert "ZhiCe-Agent gateway check ok" not in output


def test_cli_gateway_passes_log_options(tmp_path, capsys, monkeypatch):
    """Gateway log flags should be passed as split gateway log options."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("ZHICE_OPS_MODE", "local_docker")
    captured = {}

    def capture_gateway(config, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("agent.cli.run_gateway", capture_gateway)

    result = main(
        [
            "gateway",
            "--http-server-log-level",
            "warning",
            "--http-access-log",
            "off",
        ]
    )

    capsys.readouterr()
    assert result == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 10086
    assert captured["log_options"] == GatewayLogOptions(
        http_access_log=False,
        http_server_log_level="warning",
    )


def test_cli_gateway_rejects_removed_legacy_log_flags(capsys):
    """Local gateway should not keep old --log-level or --access-log aliases."""

    with pytest.raises(SystemExit) as exc_info:
        main(["gateway", "--log-level", "warning"])
    assert exc_info.value.code == 2

    with pytest.raises(SystemExit) as exc_info:
        main(["gateway", "--access-log", "off"])
    assert exc_info.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_cli_gateway_uses_default_home_workspace(tmp_path, capsys, monkeypatch):
    """Gateway check should resolve the same ~/.zhice default as init and chat."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("agent.config.Path.home", lambda: tmp_path)

    result = main(["--env-file", str(tmp_path / "missing.env"), "gateway", "--check"])

    output = capsys.readouterr().out
    assert result == 0
    assert str((tmp_path / ".zhice").resolve()) in output


def test_cli_workspace_hint_loads_that_workspaces_runtime_env(tmp_path, capsys, monkeypatch):
    _clear_zhice_env(monkeypatch)
    workspace = tmp_path / "selected"
    config_dir = workspace / "config"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text("WORKSPACE_RUNTIME_VALUE=loaded\n", encoding="utf-8")

    result = main(["init", "--workspace", str(workspace)])

    capsys.readouterr()
    assert result == 0
    assert os.environ["WORKSPACE_RUNTIME_VALUE"] == "loaded"


def test_cli_chat_reports_missing_runtime_prompts(tmp_path, capsys, monkeypatch):
    """Chat should ask the user to run init instead of raising a traceback."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))

    result = main([])

    output = capsys.readouterr().out
    assert result == 1
    assert "prompt not found: identity" in output
    assert "zcagent init" in output


def test_cli_chat_errors_when_llm_config_is_missing(tmp_path, capsys, monkeypatch):
    """Missing required LLM config should block chat startup with setup guidance."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)

    result = main([])

    output = capsys.readouterr().out
    assert result == 1
    assert "LLM configuration is invalid" in output
    assert "models.json" in output
    assert "zcagent init" in output


def test_cli_chat_errors_when_enabled_llm_has_no_api_key(tmp_path, capsys, monkeypatch):
    """A generated but unconfigured endpoint should be treated as not runnable."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    _write_models(
        config_dir,
        {"default": {"protocol": "openai", "base_url": "https://api.test/v1", "api_key": "", "model": "m", "context_window": 8192}},
    )

    result = main([])

    output = capsys.readouterr().out
    assert result == 1
    assert "api_key" in output
    assert "Chat cannot start" in output
    assert "models.json" in output
    assert "edit" in output
    assert "zcagent init --force" in output
    assert "run zcagent init to create" not in output


def test_cli_chat_rejects_invalid_hook_config(tmp_path, capsys, monkeypatch):
    """An explicitly configured but invalid Hook must not be silently skipped."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _write_config_section(
        config_dir,
        "hooks",
        {"version": 1, "entries": [{"name": "invalid", "stage": "unknown", "script": "hook.py"}]},
    )

    result = main([])

    output = capsys.readouterr().out
    assert result == 1
    assert "Hook configuration is invalid" in output
    assert "Unsupported Hook stage" in output


def test_cli_chat_treats_missing_skill_sources_config_as_disabled(tmp_path, capsys, monkeypatch):
    """An unconfigured optional Skill source should stay silent."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())
    monkeypatch.setattr(builtins, "input", lambda _prompt="": "/exit")

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "skills sync skipped" not in output
    assert "skill_sources.yml" not in output


def test_cli_chat_reports_one_warning_for_invalid_skill_sources_config(
    tmp_path, capsys, monkeypatch
):
    """An explicitly configured invalid Skill source should be unavailable, not disabled."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_dir.joinpath("config.yml").write_text("skills: []\n", encoding="utf-8")
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())
    monkeypatch.setattr(builtins, "input", lambda _prompt="": "/exit")

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert output.count("Skill capability unavailable") == 1
    assert "invalid config/config.yml skills section" in output
    assert "skills disabled" not in output
    assert "skills sync skipped" not in output


def test_cli_chat_defaults_to_daily_session_without_banner_noise(tmp_path, capsys, monkeypatch):
    """zcagent should use today's session without printing runtime path details."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    monkeypatch.setattr("agent.cli._default_session_id", lambda: "chat-20260621")
    echo = _EchoLLM()
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: echo)
    inputs = iter(["hello", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "zcagent - Personal AI Assistant" in output
    assert "workspace:" not in output
    assert "session:" not in output
    assert (tmp_path / "contexts" / "sessions" / "chat-20260621.jsonl").exists()
    assert not (tmp_path / "contexts" / "sessions" / "default.jsonl").exists()
    assert [item["function"]["name"] for item in echo.tools_calls[0]] == ["discover_tools"]


def test_cli_chat_and_history_render_assistant_markdown_as_plain_text(
    tmp_path, capsys, monkeypatch
):
    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    monkeypatch.setattr("agent.cli._default_session_id", lambda: "chat-markdown")
    markdown = "# 结果\n\n**总计：** 265 天\n\n- 第一项\n- 第二项"
    monkeypatch.setattr(
        "agent.cli._build_llm_provider",
        lambda *_args: _EchoLLM(markdown),
    )
    inputs = iter(["计算时间", "/history", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    session_text = (
        tmp_path / "contexts" / "sessions" / "chat-markdown.jsonl"
    ).read_text(encoding="utf-8")
    assert result == 0
    assert "结果\n\n总计： 265 天\n\n• 第一项\n• 第二项" in output
    assert "**总计：**" not in output
    assert "**总计：**" in session_text


def test_cli_chat_respects_explicit_session_id(tmp_path, capsys, monkeypatch):
    """An explicit --session should still resume the named conversation."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())
    inputs = iter(["hello", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main(["--session", "named-session"])

    capsys.readouterr()
    assert result == 0
    assert (tmp_path / "contexts" / "sessions" / "named-session.jsonl").exists()


def test_cli_runtime_status_updates_spinner_only_for_active_events():
    from agent.cli import _update_cli_runtime_status

    class _Spinner:
        labels = []

        def set_label(self, label):
            self.labels.append(label)

    spinner = _Spinner()
    base = {
        "protocol_version": 1,
        "event_id": "event-1",
        "type": "llm.started",
        "status": "started",
        "timestamp": "2026-07-20T00:00:00Z",
        "sequence": 1,
        "session_id": "session-1",
        "turn_id": "turn-1",
        "display": {"title": "正在请求模型"},
    }

    _update_cli_runtime_status(spinner, base)
    _update_cli_runtime_status(
        spinner,
        {
            **base,
            "type": "skill.progress",
            "display": {"title": "Skill 运行中", "detail": "正在生成报告"},
        },
    )
    _update_cli_runtime_status(
        spinner,
        {**base, "display": {"title": "内部包装", "visibility": "internal"}},
    )
    _update_cli_runtime_status(spinner, {**base, "type": "llm.completed", "status": "completed"})
    _update_cli_runtime_status(spinner, {"type": "text_delta", "content": "hello"})

    assert spinner.labels == ["正在请求模型", "正在生成报告"]


def test_cli_auto_endpoint_uses_default_alias_when_configured(tmp_path):
    """Auto startup should honor a lightweight default alias."""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _write_models(
        config_dir,
        {"backup": {"protocol": "openai", "base_url": "https://b.test/v1", "api_key": "k", "model": "m"}},
        {"chat": "backup"},
    )
    endpoints = [_endpoint("backup", priority=2)]

    assert _resolve_preferred_endpoint(config_dir, "auto", endpoints) == "backup"


def test_cli_auto_endpoint_uses_priority_when_no_default_exists(tmp_path):
    """Auto startup should allow configs without any default entry."""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _write_models(
        config_dir,
        {
            "slow": {"protocol": "openai", "base_url": "https://s.test/v1", "api_key": "k", "model": "s"},
            "fast": {"protocol": "openai", "base_url": "https://f.test/v1", "api_key": "k", "model": "f"},
        },
        {},
    )
    endpoints = [_endpoint("slow", priority=3), _endpoint("fast", priority=1)]

    assert _resolve_preferred_endpoint(config_dir, "auto", endpoints) is None


def test_cli_new_switches_to_a_new_session(tmp_path, capsys, monkeypatch):
    """The /new command should create and switch to a fresh session id."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())
    monkeypatch.setattr("agent.cli._new_session_id", lambda: "session-test-new")
    inputs = iter(["/new", "hello", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "new session:" in output
    assert "session-test-new" in output
    assert (tmp_path / "contexts" / "sessions" / "session-test-new.jsonl").exists()


def test_cli_clear_clears_current_session(tmp_path, capsys, monkeypatch):
    """The /clear command should clear the persisted current session."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    monkeypatch.setattr("agent.cli._default_session_id", lambda: "chat-clear-day")
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())
    inputs = iter(["hello", "/clear", "/history", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "session cleared:" in output
    assert "(empty history)" in output
    assert not (tmp_path / "contexts" / "sessions" / "chat-clear-day.jsonl").exists()


def test_cli_reset_is_rejected_without_calling_the_llm(tmp_path, capsys, monkeypatch):
    """The retired /reset command should point to /clear without entering the Agent."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    echo = _EchoLLM()
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: echo)
    inputs = iter(["/reset", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "Unsupported command: /reset. Use /clear." in output
    assert echo.chat_calls == 0


def test_cli_sessions_lists_previews(tmp_path, capsys, monkeypatch):
    """The /sessions command should show ids and first-user previews."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())

    sessions_dir = tmp_path / "contexts" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir.joinpath("alpha.jsonl").write_text(
        '{"role":"user","content":"first alpha message","timestamp":1.0,"name":null,"tool_call_id":null,"tool_calls":[],"metadata":{}}\n',
        encoding="utf-8",
    )
    sessions_dir.joinpath("beta.jsonl").write_text(
        '{"role":"user","content":"first beta message","timestamp":2.0,"name":null,"tool_call_id":null,"tool_calls":[],"metadata":{}}\n',
        encoding="utf-8",
    )
    inputs = iter(["/sessions", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main(["--session", "beta"])

    output = capsys.readouterr().out
    assert result == 0
    assert "alpha" in output
    assert "beta" in output
    assert "first alpha message" in output
    assert "first beta message" in output
    assert "Tip: use '/sessions rename <id> <title>'" in output
    assert "'/sessions delete (<id>)'" in output


def test_cli_sessions_rename_updates_title(tmp_path, capsys, monkeypatch):
    """The /sessions rename command should set a title without calling the LLM."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    echo = _EchoLLM()
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: echo)
    sessions_dir = tmp_path / "contexts" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir.joinpath("alpha.jsonl").write_text(
        '{"role":"user","content":"first alpha message","timestamp":1.0,"name":null,"tool_call_id":null,"tool_calls":[],"metadata":{}}\n',
        encoding="utf-8",
    )
    inputs = iter(["/sessions rename alpha 新标题", "/sessions", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "session renamed:" in output
    assert "新标题" in output
    assert echo.chat_calls == 0


def test_cli_sessions_delete_without_id_clears_current_session(tmp_path, capsys, monkeypatch):
    """Deleting without an id should behave like /clear for the current session."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    monkeypatch.setattr("agent.cli._default_session_id", lambda: "chat-delete-day")
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())
    inputs = iter(["hello", "/sessions delete", "/history", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "session cleared:" in output
    assert "(empty history)" in output
    assert not (tmp_path / "contexts" / "sessions" / "chat-delete-day.jsonl").exists()


def test_cli_sessions_delete_with_id_deletes_other_session(tmp_path, capsys, monkeypatch):
    """Deleting another id should remove that stored session."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())
    sessions_dir = tmp_path / "contexts" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir.joinpath("alpha.jsonl").write_text(
        '{"role":"user","content":"first alpha message","timestamp":1.0,"name":null,"tool_call_id":null,"tool_calls":[],"metadata":{}}\n',
        encoding="utf-8",
    )
    inputs = iter(["/sessions delete alpha", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main(["--session", "beta"])

    output = capsys.readouterr().out
    assert result == 0
    assert "session deleted:" in output
    assert not (sessions_dir / "alpha.jsonl").exists()


def test_cli_tools_lists_default_tools(tmp_path, capsys, monkeypatch):
    """The /tools debug command should show the default registry."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())
    inputs = iter(["/tools", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "list_dir" in output
    assert "read_file" in output
    assert "grep" in output
    assert "exec" in output
    assert "load_skills" in output
    assert "run_skill_script" not in output
    assert "sync_skills" in output
    assert "memory_read" in output
    assert "memory_write" in output


def test_cli_memory_defaults_to_showing_current_memory(tmp_path, capsys, monkeypatch):
    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())
    inputs = iter(["/memory", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main(["--session", "alpha"])

    output = capsys.readouterr().out
    assert result == 0
    assert "Memory is empty." in output


def test_cli_memory_shows_current_memory(tmp_path, capsys, monkeypatch):
    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    memory = tmp_path / "contexts" / "memory" / "MEMORY.md"
    memory.parent.mkdir(parents=True, exist_ok=True)
    memory.write_text(
        "# ZhiCe-Agent Memory\n\n<!-- zhice-memory:start -->\n\n"
        "## profile\n\n## preferences\n\n- 喜欢吃西瓜\n\n"
        "## projects\n\n## constraints\n\n## decisions\n\n"
        "<!-- zhice-memory:end -->\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())
    inputs = iter(["/memory", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "preferences:" in output
    assert "喜欢吃西瓜" in output


def test_cli_help_keeps_skill_sync_as_skills_tip(tmp_path, capsys, monkeypatch):
    """Global help should keep /skills sync under the /skills command surface."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())
    inputs = iter(["/help", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "/skills" in output
    assert "/skills sync" not in output
    assert "/subagent" in output
    assert "/subagent auto" not in output
    assert "/subagent off" not in output
    assert "/subagent once" not in output
    assert "show current Memory" in output
    assert "/memory session" not in output
    assert "/stop" not in output


def test_cli_missing_subagent_prompt_warns_but_chat_still_starts(
    tmp_path,
    capsys,
    monkeypatch,
):
    """Optional Subagent prompt failures must not block the main CLI."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    _write_config_section(
        config_dir,
        "subagents",
        {"enabled": True, "profiles": {"explorer": {"description": "inspect", "tools": ["read_file"]}}},
    )
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())
    inputs = iter(["/subagent", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "Subagent is currently unavailable:" in output
    assert "Required Subagent runtime prompt is missing: subagent.md" in output
    assert "Run zcagent init" in output
    assert "cause_code" not in output
    assert "bye" in output


def test_cli_invalid_mcp_config_warns_but_chat_still_starts(tmp_path, capsys, monkeypatch):
    """Optional MCP configuration failures must not block the main CLI."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    config_dir.joinpath("config.yml").write_text("mcp: []\n", encoding="utf-8")
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())
    inputs = iter(["/mcp", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "MCP_CONFIG_INVALID" in output
    assert "Fix the mcp section in config/config.yml" in output
    assert "bye" in output


def test_cli_skills_lists_empty_directory(tmp_path, capsys, monkeypatch):
    """The /skills command should be friendly when no local Skills exist."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())
    inputs = iter(["/skills", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "(no skills)" in output
    assert "Tip: use '/skills sync [--verbose] [source_name]'" in output
    assert "Optional args: --verbose prints details" in output


def test_cli_skills_lists_discovered_skill(tmp_path, capsys, monkeypatch):
    """The /skills command should print compact local Skill summaries."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    source = tmp_path / "official-source"
    _write_demo_skill(source)
    _write_skill_sources_config(tmp_path, source, on_startup="always")
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())
    inputs = iter(["/skills", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "official/demo" in output
    assert "category" not in output
    assert "readonly" not in output
    assert "Demo skill." in output
    assert "Tip: use '/skills sync [--verbose] [source_name]'" in output
    assert "Optional args: --verbose prints details" in output


def test_cli_skills_sync_updates_runtime_skills(tmp_path, capsys, monkeypatch):
    """The /skills sync command should sync configured sources into extends."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    source = tmp_path / "official-source"
    _write_demo_skill(source)
    _write_runtime_prompts(tmp_path)
    _write_skill_sources_config(tmp_path, source, on_startup="never")
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())
    inputs = iter(["/skills sync", "/skills", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "skills synced: official" in output
    assert "1 new" in output
    assert "official/demo" in output
    assert (tmp_path / "extends" / "official" / "skills" / "demo" / "SKILL.md").is_file()


def test_cli_startup_skill_sync_is_silent_in_chat(tmp_path, capsys, monkeypatch):
    """Automatic startup Skill sync should not add noise to normal zcagent chat."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    source = tmp_path / "official-source"
    _write_demo_skill(source)
    _write_runtime_prompts(tmp_path)
    _write_skill_sources_config(tmp_path, source, on_startup="always")
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())
    monkeypatch.setattr(builtins, "input", lambda _prompt="": "/exit")

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "skills sync:" not in output
    assert (tmp_path / "extends" / "official" / "skills" / "demo" / "SKILL.md").is_file()


def test_cli_model_shows_compact_current_status(tmp_path, capsys, monkeypatch):
    """The /model command should show compact local endpoint state."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    switchable = _SwitchableLLM()
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: switchable)
    inputs = iter(["/model", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "current: default/model-a" in output
    assert "model-a" in output
    assert "backup" not in output
    assert "available endpoints:" not in output
    assert "Tip:" in output
    assert "'/model <endpoint>'" in output
    assert "'/model <endpoint>/<model>'" in output
    assert "/model <model>" not in output
    assert "'/model list'" in output
    assert "'/model reset'" in output
    assert "protocol=" not in output
    assert "priority=" not in output
    assert switchable.chat_calls == 0


def test_cli_model_list_shows_available_endpoints_one_line(tmp_path, capsys, monkeypatch):
    """The /model list command should show endpoints and models on one line."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    switchable = _SwitchableLLM()
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: switchable)
    inputs = iter(["/model list", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "available endpoints:" in output
    assert "* default" in output
    assert "default model: model-a" in output
    assert "  backup" in output
    assert "default model: model-b" in output
    assert "Tip: use '/model list <endpoint>'" in output
    assert "protocol=" not in output
    assert "priority=" not in output
    assert switchable.chat_calls == 0


def test_cli_model_list_endpoint_shows_supported_models(tmp_path, capsys, monkeypatch):
    """The /model list endpoint command should show supported models for one endpoint."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    switchable = _SwitchableLLM()
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: switchable)
    inputs = iter(["/model list backup", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "endpoint: backup" in output
    assert "available models:" in output
    assert "* model-b (default)" in output
    assert "  model-b-plus" in output
    assert "available endpoints:" not in output
    assert switchable.chat_calls == 0


def test_cli_model_list_endpoint_reports_unknown_endpoint(tmp_path, capsys, monkeypatch):
    """The /model list endpoint command should report unknown endpoints clearly."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    switchable = _SwitchableLLM()
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: switchable)
    inputs = iter(["/model list missing", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "Unknown endpoint: missing" in output
    assert "available endpoints:" in output
    assert switchable.chat_calls == 0


def test_cli_model_switches_preferred_endpoint(tmp_path, capsys, monkeypatch):
    """The /model command should switch the provider's preferred endpoint."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    switchable = _SwitchableLLM()
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: switchable)
    inputs = iter(["/model backup", "/model", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "model switched:" in output
    assert "model-b" in output
    assert "current:" in output
    assert "backup" in output
    assert switchable.preferred_endpoint == "backup"


def test_cli_model_switches_endpoint_with_model_override(tmp_path, capsys, monkeypatch):
    """The /model endpoint/model form should override the preferred endpoint model."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    switchable = _SwitchableLLM()
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: switchable)
    inputs = iter(["/model backup/model-b-plus", "/model", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "model switched:" in output
    assert "model-b-plus" in output
    assert "current: backup/model-b-plus" in output
    assert switchable.preferred_endpoint == "backup"
    assert switchable.preferred_model == "model-b-plus"


def test_cli_model_rejects_unsupported_endpoint_model(tmp_path, capsys, monkeypatch):
    """The /model endpoint/model form should reject models not listed by the endpoint."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    switchable = _SwitchableLLM()
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: switchable)
    inputs = iter(["/model backup/model-c", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "does not list model" in output
    assert switchable.preferred_endpoint == "default"
    assert switchable.chat_calls == 0


def test_cli_model_reset_returns_to_default_order(tmp_path, capsys, monkeypatch):
    """The /model reset command should clear an explicit model preference."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    switchable = _SwitchableLLM()
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: switchable)
    inputs = iter(["/model backup", "/model reset", "/model", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "model preference reset" in output
    assert "current: default/model-a" in output
    assert switchable.preferred_endpoint == "default"


def test_gateway_start_uses_local_ops_supervisor_by_default(tmp_path, monkeypatch):
    _clear_zhice_env(monkeypatch)
    captured = {}

    class Supervisor:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self):
            return 23

    monkeypatch.setattr("agent.cli.LocalOpsSupervisor", Supervisor)

    result = main(["gateway", "--workspace", str(tmp_path), "--port", "12000"])

    assert result == 23
    assert captured["state_dir"] == tmp_path / "state"
    assert captured["child_argv"][-1] == "--ops-child"
    assert "12000" in captured["child_argv"]


def test_gateway_external_docker_mode_skips_local_supervisor(tmp_path, monkeypatch):
    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_OPS_MODE", "local_docker")
    called = []
    monkeypatch.setattr("agent.cli.run_gateway", lambda *_args, **_kwargs: called.append(True))

    result = main(["gateway", "--workspace", str(tmp_path)])

    assert result == 0
    assert called == [True]


def _write_runtime_prompts(workspace):
    prompts_dir = workspace / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "identity.md").write_text("identity prompt", encoding="utf-8")
    (prompts_dir / "tool_use_policy.md").write_text("tool policy prompt", encoding="utf-8")
    (prompts_dir / "skills_intro.md").write_text("skills intro prompt", encoding="utf-8")
    (prompts_dir / "memory_extraction.md").write_text("extract prompt", encoding="utf-8")


def _write_demo_skill(workspace):
    skill_dir = workspace / "skills" / "demo"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(
        """---
name: demo
description: Demo skill.
---

Demo body.
""",
        encoding="utf-8",
    )


def _write_skill_sources_config(workspace, source, *, on_startup):
    config_dir = workspace / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    _write_config_section(
        config_dir,
        "skills",
        {
            "sync": {
                "on_startup": on_startup,
                "background": {"enabled": False, "interval_seconds": 0},
                "log": "changes_only",
            },
            "sources": [
                {
                    "name": "official",
                    "sync": True,
                    "local_dir": source.resolve().as_posix(),
                }
            ],
        },
    )


class _EchoLLM:
    def __init__(self, content="ok"):
        self.chat_calls = 0
        self.tools_calls = []
        self.content = content

    def chat(self, messages, tools=None):
        from agent.protocols.llm import LLMResponse

        self.chat_calls += 1
        self.tools_calls.append(tools)
        return LLMResponse(content=self.content)


def _endpoint(name: str, *, priority: int = 1) -> LLMEndpoint:
    return LLMEndpoint(
        name=name,
        protocol="openai",
        base_url=f"https://{name}.test/v1",
        api_key="key",
        model=name,
        context_window=32768,
        priority=priority,
    )


class _SwitchableLLM:
    def __init__(self):
        from agent.protocols.llm import LLMEndpoint

        self._endpoints = [
            LLMEndpoint(
                name="default",
                protocol="openai",
                base_url="https://a.test/v1",
                api_key="key",
                model="model-a",
                context_window=32768,
                priority=1,
            ),
            LLMEndpoint(
                name="backup",
                protocol="openai",
                base_url="https://b.test/v1",
                api_key="key",
                model="model-b",
                context_window=32768,
                priority=2,
                supported_models=("model-b-plus",),
            ),
        ]
        self.preferred_endpoint = "default"
        self.preferred_model = ""
        self.chat_calls = 0

    def endpoints(self):
        return list(self._endpoints)

    def current_endpoint(self):
        from dataclasses import replace

        for endpoint in self._endpoints:
            if endpoint.name == self.preferred_endpoint:
                if self.preferred_model:
                    return replace(endpoint, model=self.preferred_model)
                return endpoint
        return self._endpoints[0]

    def match_endpoint(self, target):
        from dataclasses import replace

        endpoint_name, separator, model = target.partition("/")
        for endpoint in self._endpoints:
            if endpoint.name == endpoint_name:
                if separator:
                    if model != endpoint.model and model not in endpoint.supported_models:
                        return None, f"Endpoint {endpoint.name!r} does not list model {model!r} as supported."
                    return replace(endpoint, model=model), ""
                return endpoint, ""
        return None, f"Unknown endpoint: {endpoint_name}"

    def set_preferred(self, endpoint_name, model=None):
        self.preferred_endpoint = endpoint_name
        self.preferred_model = model or ""

    def reset_preferred(self):
        self.preferred_endpoint = "default"
        self.preferred_model = ""

    def chat(self, messages, tools=None):
        from agent.protocols.llm import LLMResponse

        self.chat_calls += 1
        return LLMResponse(content="ok")


def _clear_zhice_env(monkeypatch) -> None:
    for key in [
        "ZHICE_AGENT_WORKSPACE",
        "ZHICE_AGENT_CONFIG_DIR",
        "ZHICE_AGENT_PROMPTS_DIR",
        "ZHICE_AGENT_CONTEXTS_DIR",
        "ZHICE_AGENT_EXTENDS_DIR",
        "ZHICE_AGENT_SKILLS_DIR",
        "ZHICE_AGENT_LOGS_DIR",
    ]:
        monkeypatch.delenv(key, raising=False)
