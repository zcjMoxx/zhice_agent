"""Tests for local runtime initialization from the CLI."""

import builtins

from agent.cli import _resolve_preferred_endpoint, main
from agent.protocols.llm import LLMEndpoint


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


def test_cli_auto_endpoint_uses_default_alias_when_configured(tmp_path):
    """Auto startup should honor a lightweight default alias."""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "llm_endpoints.json").write_text(
        '{"default":"backup","backup":{"protocol":"openai","base_url":"https://b.test/v1","api_key":"k","model":"m"}}',
        encoding="utf-8",
    )
    endpoints = [_endpoint("backup", priority=2)]

    assert _resolve_preferred_endpoint(config_dir, "auto", endpoints) == "backup"


def test_cli_auto_endpoint_uses_priority_when_no_default_exists(tmp_path):
    """Auto startup should allow configs without any default entry."""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "llm_endpoints.json").write_text(
        '{"slow":{"protocol":"openai","base_url":"https://s.test/v1","api_key":"k","model":"s"},"fast":{"protocol":"openai","base_url":"https://f.test/v1","api_key":"k","model":"f"}}',
        encoding="utf-8",
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


def _endpoint(name: str, *, priority: int = 1) -> LLMEndpoint:
    return LLMEndpoint(
        name=name,
        protocol="openai",
        base_url=f"https://{name}.test/v1",
        api_key="key",
        model=name,
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
                priority=1,
            ),
            LLMEndpoint(
                name="backup",
                protocol="openai",
                base_url="https://b.test/v1",
                api_key="key",
                model="model-b",
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
        "ZHICE_AGENT_SKILLS_DIR",
        "ZHICE_AGENT_LOGS_DIR",
    ]:
        monkeypatch.delenv(key, raising=False)
