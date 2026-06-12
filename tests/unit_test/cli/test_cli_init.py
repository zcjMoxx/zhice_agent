"""Tests for local runtime initialization from the CLI."""

import builtins

from agent.cli import main


def test_cli_init_generates_runtime_files(tmp_path, capsys, monkeypatch):
    """zcagent init should create local files in the selected workspace."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = main(
        [
            "init",
            "--workspace",
            str(tmp_path),
            "--endpoint",
            "local",
            "--base-url",
            "https://gateway.test/v1",
            "--api-key",
            "local-json-secret",
            "--model",
            "test-model",
        ]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "created:" in output
    assert not (tmp_path / ".env").exists()
    assert (tmp_path / "config" / "llm_endpoints.json").is_file()
    assert (tmp_path / "prompts" / "identity.md").is_file()


def test_cli_init_refuses_to_overwrite_existing_env(tmp_path, capsys, monkeypatch):
    """Existing user config should be preserved unless --force is explicit."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("EXISTING=1\n", encoding="utf-8")

    result = main(["init", "--workspace", str(tmp_path), "--write-env"])

    output = capsys.readouterr().out
    assert result == 1
    assert "Refusing to overwrite existing file" in output
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "EXISTING=1\n"


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
    assert (workspace / "config" / "llm_endpoints.json").is_file()


def test_cli_reports_missing_workspace_when_no_env_exists(tmp_path, capsys, monkeypatch):
    """Missing workspace should explain how to create a local .env."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = main(["--env-file", str(tmp_path / "missing.env"), "init"])

    output = capsys.readouterr().out
    assert result == 1
    assert "ZHICE_AGENT_WORKSPACE is not set" in output
    assert "Create config/.env" in output


def test_cli_gateway_check_uses_configured_workspace(tmp_path, capsys, monkeypatch):
    """zcagent gateway should be a first-class subcommand."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))

    result = main(["gateway", "--check", "--port", "19000"])

    output = capsys.readouterr().out
    assert result == 0
    assert "ZhiCe-Agent gateway check ok" in output
    assert "http://127.0.0.1:19000" in output
    assert str(tmp_path.resolve()) in output


def test_cli_gateway_reports_missing_workspace(tmp_path, capsys, monkeypatch):
    """Gateway startup should share the same workspace setup guard as chat."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = main(["--env-file", str(tmp_path / "missing.env"), "gateway", "--check"])

    output = capsys.readouterr().out
    assert result == 1
    assert "ZHICE_AGENT_WORKSPACE is not set" in output
    assert "zcagent gateway" in output


def test_cli_chat_reports_missing_runtime_prompts(tmp_path, capsys, monkeypatch):
    """Chat should ask the user to run init instead of raising a traceback."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))

    result = main([])

    output = capsys.readouterr().out
    assert result == 1
    assert "prompt not found: identity" in output
    assert "zcagent init" in output


def test_cli_chat_defaults_to_stable_default_session(tmp_path, capsys, monkeypatch):
    """zcagent should keep using the stable default session unless changed."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())
    monkeypatch.setattr(builtins, "input", lambda _prompt="": "/exit")

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "default" in output


def test_cli_chat_respects_explicit_session_id(tmp_path, capsys, monkeypatch):
    """An explicit --session should still resume the named conversation."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())
    monkeypatch.setattr(builtins, "input", lambda _prompt="": "/exit")

    result = main(["--session", "named-session"])

    output = capsys.readouterr().out
    assert result == 0
    assert "named-session" in output


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


def test_cli_reset_clears_current_session(tmp_path, capsys, monkeypatch):
    """The /reset command should clear the persisted current session."""

    _clear_zhice_env(monkeypatch)
    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(tmp_path))
    _write_runtime_prompts(tmp_path)
    monkeypatch.setattr("agent.cli._build_llm_provider", lambda *_args: _EchoLLM())
    inputs = iter(["hello", "/reset", "/history", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(inputs))

    result = main([])

    output = capsys.readouterr().out
    assert result == 0
    assert "session cleared:" in output
    assert "(empty history)" in output
    assert not (tmp_path / "contexts" / "sessions" / "default.jsonl").exists()


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


def _write_runtime_prompts(workspace):
    prompts_dir = workspace / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "identity.md").write_text("identity prompt", encoding="utf-8")
    (prompts_dir / "tool_use_policy.md").write_text("tool policy prompt", encoding="utf-8")
    (prompts_dir / "skills_intro.md").write_text("skills intro prompt", encoding="utf-8")


class _EchoLLM:
    def chat(self, messages, tools=None):
        from agent.protocols.llm import LLMResponse

        return LLMResponse(content="ok")


def _clear_zhice_env(monkeypatch) -> None:
    for key in [
        "ZHICE_AGENT_WORKSPACE",
        "ZHICE_AGENT_CONFIG_DIR",
        "ZHICE_AGENT_PROMPTS_DIR",
        "ZHICE_AGENT_CONTEXTS_DIR",
        "ZHICE_AGENT_SKILLS_DIR",
        "ZHICE_AGENT_LOGS_DIR",
    ]:
        monkeypatch.delenv(key, raising=False)
