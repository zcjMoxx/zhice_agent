from __future__ import annotations

from agent.prompt_loader import PromptLoader
from agent.protocols.subagent import subagent_unavailable_payload
from agent.subagents.startup import check_subagent_startup


def test_missing_config_disables_subagent_without_error(tmp_path):
    result = check_subagent_startup(tmp_path / "config", PromptLoader(tmp_path / "prompts"))

    assert result.config.enabled is False
    assert result.status.state == "disabled"
    assert result.status.code == "SUBAGENT_DISABLED"


def test_invalid_config_isolated_as_unavailable(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_dir.joinpath("subagents.yml").write_text("enabled: [invalid]\n", encoding="utf-8")

    result = check_subagent_startup(config_dir, PromptLoader(tmp_path / "prompts"))

    assert result.config.enabled is False
    assert result.status.code == "SUBAGENT_CONFIG_INVALID"


def test_missing_enabled_prompt_returns_precise_use_error(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_dir.joinpath("subagents.yml").write_text(
        "enabled: true\nprofiles:\n  explorer:\n    description: inspect\n"
        "    tools: [read_file]\n",
        encoding="utf-8",
    )
    prompts = tmp_path / "prompts"
    prompts.mkdir()

    result = check_subagent_startup(config_dir, PromptLoader(prompts))

    assert result.status.code == "SUBAGENT_PROMPT_NOT_FOUND"
    assert result.status.details == {"missing_prompt": "subagent.md"}
    assert subagent_unavailable_payload(result.status) == {
        "code": "SUBAGENT_RUNTIME_UNAVAILABLE",
        "cause_code": "SUBAGENT_PROMPT_NOT_FOUND",
        "message": "Required Subagent runtime prompt is missing: subagent.md",
        "hint": "Run zcagent init, then restart the process.",
    }


def test_all_required_prompts_keep_subagent_available(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_dir.joinpath("subagents.yml").write_text(
        "enabled: true\nprofiles:\n  explorer:\n    description: inspect\n"
        "    tools: [read_file]\n",
        encoding="utf-8",
    )
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for name in ("subagent", "subagent_orchestration", "subagent_once"):
        (prompts / f"{name}.md").write_text(name, encoding="utf-8")

    result = check_subagent_startup(config_dir, PromptLoader(prompts))

    assert result.config.enabled is True
    assert result.status.state == "available"
    assert result.status.code == "SUBAGENT_AVAILABLE"
