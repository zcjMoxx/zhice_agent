"""Tests for application configuration path resolution."""

from agent.config import load_config


def test_load_config_uses_explicit_workspace(tmp_path, monkeypatch):
    """An explicit workspace should be the root for all default directories."""

    _clear_zhice_env(monkeypatch)

    config = load_config(tmp_path)

    assert config.workspace == tmp_path.resolve()
    assert config.config_dir == tmp_path / "config"
    assert config.prompts_dir == tmp_path / "prompts"
    assert config.contexts_dir == tmp_path / "contexts"
    assert config.sessions_dir == tmp_path / "contexts" / "sessions"
    assert config.skills_dir == tmp_path / "skills"
    assert config.logs_dir == tmp_path / "logs"


def test_load_config_allows_environment_overrides(tmp_path, monkeypatch):
    """Directory-specific environment variables should override derived paths."""

    workspace = tmp_path / "workspace"
    custom_config = tmp_path / "custom_config"
    custom_prompts = tmp_path / "custom_prompts"
    custom_contexts = tmp_path / "custom_contexts"
    custom_skills = tmp_path / "custom_skills"
    custom_logs = tmp_path / "custom_logs"

    monkeypatch.setenv("ZHICE_AGENT_WORKSPACE", str(workspace))
    monkeypatch.setenv("ZHICE_AGENT_CONFIG_DIR", str(custom_config))
    monkeypatch.setenv("ZHICE_AGENT_PROMPTS_DIR", str(custom_prompts))
    monkeypatch.setenv("ZHICE_AGENT_CONTEXTS_DIR", str(custom_contexts))
    monkeypatch.setenv("ZHICE_AGENT_SKILLS_DIR", str(custom_skills))
    monkeypatch.setenv("ZHICE_AGENT_LOGS_DIR", str(custom_logs))

    config = load_config()

    assert config.workspace == workspace.resolve()
    assert config.config_dir == custom_config.resolve()
    assert config.prompts_dir == custom_prompts.resolve()
    assert config.contexts_dir == custom_contexts.resolve()
    assert config.sessions_dir == custom_contexts.resolve() / "sessions"
    assert config.skills_dir == custom_skills.resolve()
    assert config.logs_dir == custom_logs.resolve()


def test_ensure_dirs_creates_runtime_directories(tmp_path, monkeypatch):
    """ensure_dirs should create the directories needed by the first-stage CLI."""

    _clear_zhice_env(monkeypatch)
    config = load_config(tmp_path)

    config.ensure_dirs()

    assert config.config_dir.is_dir()
    assert config.prompts_dir.is_dir()
    assert config.contexts_dir.is_dir()
    assert config.sessions_dir.is_dir()
    assert config.skills_dir.is_dir()
    assert config.logs_dir.is_dir()


def _clear_zhice_env(monkeypatch) -> None:
    """Remove Zhice-Agent path variables so tests are deterministic."""

    for key in [
        "ZHICE_AGENT_WORKSPACE",
        "ZHICE_AGENT_CONFIG_DIR",
        "ZHICE_AGENT_PROMPTS_DIR",
        "ZHICE_AGENT_CONTEXTS_DIR",
        "ZHICE_AGENT_SKILLS_DIR",
        "ZHICE_AGENT_LOGS_DIR",
    ]:
        monkeypatch.delenv(key, raising=False)
