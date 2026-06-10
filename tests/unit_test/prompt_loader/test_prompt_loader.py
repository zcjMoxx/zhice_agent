"""Tests for UTF-8 Markdown prompt loading."""

import pytest

from agent.prompt_loader import PromptLoader, PromptNotFoundError, PromptPathError


def test_load_reads_utf8_prompt_without_suffix(tmp_path):
    """PromptLoader should read UTF-8 Markdown by semantic prompt name."""

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "identity.md").write_text("你好，智策 Agent。\n", encoding="utf-8")

    loader = PromptLoader(prompts_dir)

    assert loader.load("identity") == "你好，智策 Agent。\n"


def test_load_reads_prompt_with_suffix(tmp_path):
    """PromptLoader should also accept names that already include .md."""

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "skills_intro.md").write_text("Skill 说明\n", encoding="utf-8")

    loader = PromptLoader(prompts_dir)

    assert loader.load("skills_intro.md") == "Skill 说明\n"


def test_missing_prompt_raises_clear_error(tmp_path):
    """Missing prompt files should raise a dedicated error."""

    loader = PromptLoader(tmp_path)

    with pytest.raises(PromptNotFoundError):
        loader.load("missing")


def test_path_traversal_is_rejected(tmp_path):
    """Prompt names must not escape the configured prompts directory."""

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    loader = PromptLoader(prompts_dir)

    with pytest.raises(PromptPathError):
        loader.load("../secret")


def test_load_many_returns_requested_names(tmp_path):
    """load_many should return a mapping keyed by the caller's requested names."""

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "identity.md").write_text("identity", encoding="utf-8")
    (prompts_dir / "tool_use_policy.md").write_text("policy", encoding="utf-8")

    loader = PromptLoader(prompts_dir)

    assert loader.load_many(["identity", "tool_use_policy"]) == {
        "identity": "identity",
        "tool_use_policy": "policy",
    }
