from pathlib import Path

import pytest

from agent.subagents.config import (
    HARD_MAX_PARALLEL,
    SubagentConfig,
    SubagentConfigurationError,
    load_subagent_config,
)


def _write(path: Path, text: str) -> Path:
    config_path = path.parent / "config.yml" if path.parent.name == "config" else path.parent / "config.yml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    indented = "\n".join(f"  {line}" if line else "" for line in text.strip().splitlines())
    config_path.write_text(f"schema_version: 1\nsubagents:\n{indented}\n", encoding="utf-8")
    return config_path


def test_missing_config_disables_subagents(tmp_path):
    config = load_subagent_config(tmp_path / "config")

    assert config.enabled is False
    assert config.list_profiles() == ()
    assert dict(config.profiles) == {}
    assert config.get_profile("explorer") is None


def test_default_profiles_are_immutable_and_not_shared():
    first = SubagentConfig()
    second = SubagentConfig()

    assert dict(first.profiles) == {}
    assert first.profiles is not second.profiles
    with pytest.raises(TypeError):
        first.profiles["explorer"] = object()  # type: ignore[index]


def test_loads_profiles_and_preserves_order(tmp_path):
    path = _write(
        tmp_path / "subagents.yml",
        """
enabled: true
max_parallel: 2
profiles:
  explorer:
    description: Read the repository.
    tools: [list_dir, read_file, "mcp__github__*"]
    allowed_skills: [official/review]
    preload_skills: [official/review]
    workspace_mode: shared_readonly
    model_role: fast
  developer:
    description: Run isolated checks.
    tools: [read_file, exec]
    workspace_mode: worktree
    timeout_seconds: 600
""",
    )

    config = load_subagent_config(path)

    assert config.enabled is True
    assert config.max_parallel == 2
    assert list(config.profiles) == ["explorer", "developer"]
    assert [profile.name for profile in config.list_profiles()] == ["explorer", "developer"]
    explorer = config.get_profile("explorer")
    assert explorer is not None
    assert explorer.tools == ("list_dir", "read_file", "mcp__github__*")
    assert explorer.denied_tools == ("delegate_tasks",)
    assert explorer.preload_skills == ("official/review",)
    assert explorer.model_role == "fast"


@pytest.mark.parametrize(
    "body,match",
    [
        (
            "enabled: true\nprofiles: {}\nunknown: true\n",
            "unknown fields",
        ),
        (
            "enabled: true\nprofiles:\n  explorer:\n    description: x\n"
            "    tools: [read_file]\n    actor: owner\n",
            "unknown fields",
        ),
        (
            f"enabled: true\nmax_parallel: {HARD_MAX_PARALLEL + 1}\nprofiles:\n"
            "  explorer:\n    description: x\n    tools: [read_file]\n",
            "outside allowed range",
        ),
        (
            "enabled: true\nprofiles:\n  explorer:\n    description: x\n"
            "    tools: ['mcp__*']\n",
            "Invalid Subagent tool matcher",
        ),
        (
            "enabled: true\nprofiles:\n  explorer:\n    description: x\n"
            "    tools: [read_file, exec]\n    workspace_mode: shared_readonly\n",
            "cannot allow exec",
        ),
        (
            "enabled: true\nprofiles:\n  explorer:\n    description: x\n"
            "    tools: [read_file]\n    allowed_skills: [official/review]\n"
            "    preload_skills: [team/review]\n",
            "preload_skills",
        ),
        (
            "enabled: true\nprofiles: {}\n",
            "requires at least one Profile",
        ),
    ],
)
def test_invalid_config_fails_closed(tmp_path, body, match):
    path = _write(tmp_path / "subagents.yml", body)

    with pytest.raises(SubagentConfigurationError, match=match):
        load_subagent_config(path)


def test_duplicate_yaml_keys_fail_closed(tmp_path):
    path = _write(
        tmp_path / "subagents.yml",
        """
enabled: true
profiles:
  explorer:
    description: first
    tools: [read_file]
  explorer:
    description: second
    tools: [list_dir]
""",
    )

    with pytest.raises(SubagentConfigurationError, match="Cannot read Subagent config"):
        load_subagent_config(path)


def test_denied_exec_makes_shared_readonly_profile_valid(tmp_path):
    path = _write(
        tmp_path / "subagents.yml",
        """
enabled: true
profiles:
  explorer:
    description: Parent may expose exec but this Profile denies it.
    tools: [read_file, exec]
    denied_tools: [exec]
    workspace_mode: shared_readonly
""",
    )

    profile = load_subagent_config(path).get_profile("explorer")

    assert profile is not None
    assert profile.denied_tools == ("exec", "delegate_tasks")
