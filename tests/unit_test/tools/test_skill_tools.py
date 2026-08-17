"""Tests for Skill tools exposed through the tool registry."""

import json

from agent.skills.loader import SkillLoader
from agent.skills.sync import SkillSourceSync
from agent.tools import create_default_tool_registry
from agent.tools.skill import LoadSkillsTool, SyncSkillsTool


def test_load_skills_tool_returns_full_skill_body(tmp_path):
    """load_skills should expose one complete SKILL.md on demand."""

    workspace, loader = _make_skill(tmp_path)

    result = LoadSkillsTool(workspace, loader).execute({"name": "demo"})

    assert result.is_error is False
    assert "skill: official/demo" in result.output
    header = result.output.split("\n\n", 1)[0]
    assert "category:" not in header
    assert "readonly:" not in header
    assert "Demo body." in result.output
    assert result.metadata["skill"] == "official/demo"
    assert result.metadata["source"] == "official"
    assert "category" not in result.metadata
    assert "readonly" not in result.metadata


def test_load_skills_tool_reports_unknown_skill(tmp_path):
    """Unknown Skill lookups should return structured tool errors."""

    workspace, loader = _make_skill(tmp_path)

    result = LoadSkillsTool(workspace, loader).execute({"name": "missing"})

    assert result.is_error is True
    assert result.metadata["code"] == "UNKNOWN_SKILL"
    assert json.loads(result.output)["code"] == "UNKNOWN_SKILL"


def test_load_skills_tool_supports_source_and_reports_ambiguous_names(tmp_path):
    """load_skills should support qualified names and return candidates for aliases."""

    workspace = tmp_path / "workspace"
    _write_source_skill(workspace / "official", "review")
    _write_source_skill(workspace / "team", "review")
    loader = SkillLoader(
        [
            ("official", workspace / "official" / "skills"),
            ("team", workspace / "team" / "skills"),
        ]
    )
    tool = LoadSkillsTool(workspace, loader)

    qualified = tool.execute({"name": "official/review"})
    redundant_source = tool.execute(
        {"name": "official/review", "source": "official"}
    )
    sourced = tool.execute({"name": "review", "source": "team"})
    ambiguous = tool.execute({"name": "review"})

    assert qualified.metadata["skill"] == "official/review"
    assert redundant_source.metadata["skill"] == "official/review"
    assert sourced.metadata["skill"] == "team/review"
    assert ambiguous.is_error is True
    payload = json.loads(ambiguous.output)
    assert payload["code"] == "AMBIGUOUS_SKILL"
    assert payload["candidates"] == ["official/review", "team/review"]


def test_default_registry_adds_skill_tools_only_with_provider(tmp_path):
    """The factory should preserve no-Skill mode for tests and light runtimes."""

    workspace, loader = _make_skill(tmp_path)
    skill_sync = _make_skill_sync(workspace, tmp_path / "source")

    without_skills = create_default_tool_registry(workspace).definitions()
    with_skills = create_default_tool_registry(workspace, skills=loader).definitions()
    with_sync = create_default_tool_registry(
        workspace,
        skills=loader,
        skill_sync=skill_sync,
    ).definitions()

    assert "load_skills" not in _tool_names(without_skills)
    assert "sync_skills" not in _tool_names(without_skills)
    assert "run_skill_script" not in _tool_names(with_skills)
    assert "load_skills" in _tool_names(with_skills)
    assert "sync_skills" in _tool_names(with_sync)


def test_sync_skills_tool_uses_configured_sources_only(tmp_path):
    """sync_skills should sync configured sources without accepting arbitrary URLs."""

    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    _write_source_skill(source, "demo")
    sync = _make_skill_sync(workspace, source)

    result = SyncSkillsTool(workspace, sync).execute({})

    payload = json.loads(result.output)
    assert result.is_error is False
    assert payload["status"] == "success"
    assert payload["sources"][0]["status"] == "synced"
    assert payload["sources"][0]["new"] == ["demo"]
    assert (workspace / "extends" / "official" / "skills" / "demo" / "SKILL.md").is_file()


def test_sync_skills_tool_reports_unconfigured_source(tmp_path):
    """sync_skills should reject source names that are not in skill_sources.yml."""

    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    _write_source_skill(source, "demo")
    sync = _make_skill_sync(workspace, source)

    result = SyncSkillsTool(workspace, sync).execute({"source": "https://example.test/repo.git"})

    payload = json.loads(result.output)
    assert result.is_error is True
    assert payload["status"] == "error"
    assert payload["sources"][0]["status"] == "failed"
    assert payload["sources"][0]["error"] == "Skill source is not configured"


def test_sync_skills_tool_reports_missing_config(tmp_path):
    """sync_skills should explain when workspace skill_sources.yml is absent."""

    workspace = tmp_path / "workspace"
    sync = SkillSourceSync(
        workspace=workspace,
        config_dir=workspace / "config",
        extends_dir=workspace / "extends",
    )

    result = SyncSkillsTool(workspace, sync).execute({})

    payload = json.loads(result.output)
    assert result.is_error is True
    assert payload["code"] == "SKILL_SYNC_ERROR"
    assert "Run zcagent init" in payload["message"]


def test_load_skills_tool_output_can_be_truncated(tmp_path):
    """Loaded Skill bodies should stay bounded before they enter session history."""

    workspace, loader = _make_skill(tmp_path, body="x" * 2000)

    result = LoadSkillsTool(workspace, loader).execute({"name": "demo", "max_chars": 1000})

    assert result.metadata["truncated"] is True
    assert result.output.endswith("[truncated]")


def _tool_names(definitions):
    return {definition["function"]["name"] for definition in definitions}


def _make_skill(tmp_path, *, body="Demo body."):
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "demo"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        f"""---
name: demo
description: Demo skill.
---

{body}
""",
        encoding="utf-8",
    )
    return workspace, SkillLoader([("official", workspace / "skills")])


def _make_skill_sync(workspace, source):
    config_dir = workspace / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    body = f"""
sync:
  on_startup: never
  background:
    enabled: false
    interval_seconds: 0
sources:
  - name: official
    sync: true
    local_dir: "{source.resolve().as_posix()}"
"""
    indented = "\n".join(f"  {line}" if line else "" for line in body.strip().splitlines())
    config_dir.joinpath("config.yml").write_text(
        f"schema_version: 1\nskills:\n{indented}\n",
        encoding="utf-8",
    )
    return SkillSourceSync(
        workspace=workspace,
        config_dir=config_dir,
        extends_dir=workspace / "extends",
    )


def _write_source_skill(source, name):
    skill_dir = source / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(
        f"""---
name: {name}
description: Demo skill.
---

Demo body.
""",
        encoding="utf-8",
    )
