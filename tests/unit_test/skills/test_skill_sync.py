"""Tests for configured Skill source synchronization."""

import shutil
from pathlib import Path

import pytest
import yaml

from agent.skills.sync import SkillSourceSync, SkillSyncError

ROOT = Path(__file__).resolve().parents[3]


def test_default_config_uses_only_the_builtin_local_skill_source():
    """The public default must not contain a fake remote fallback URL."""

    template = (ROOT / "config" / "config.example.yml").read_text(
        encoding="utf-8"
    )
    source = yaml.safe_load(template)["skills"]["sources"][0]

    assert 'local_dir: "${ZHICE_AGENT_SKILL_REPO}"' in template
    assert "https://example.com/skills.git" not in template
    assert source["git_url"] is None
    assert source["target"] == "master"


def _write_skills_section(config_dir, body):
    indented = "\n".join(f"  {line}" if line else "" for line in body.strip().splitlines())
    config_dir.joinpath("config.yml").write_text(
        f"schema_version: 1\nskills:\n{indented}\n",
        encoding="utf-8",
    )


def test_sync_loads_empty_config_when_missing(tmp_path):
    """Missing skill_sources.yml should keep sync disabled."""

    workspace = tmp_path / "workspace"
    sync = SkillSourceSync(
        workspace=workspace,
        config_dir=workspace / "config",
        extends_dir=workspace / "extends",
    )

    settings, sources = sync.load()

    assert sources == []
    assert settings.extends_dir == (workspace / "extends").resolve()
    assert sync.sync_on_startup() is None
    assert sync.skill_roots() == []


def test_manual_sync_requires_config(tmp_path):
    """Manual sync should explain that skill_sources.yml must be initialized."""

    workspace = tmp_path / "workspace"
    sync = SkillSourceSync(
        workspace=workspace,
        config_dir=workspace / "config",
        extends_dir=workspace / "extends",
    )

    with pytest.raises(SkillSyncError, match="Run zcagent init"):
        sync.sync()


def test_config_schema_uses_lightweight_source_fields(tmp_path):
    """The source config should parse the new lightweight schema."""

    workspace = tmp_path / "workspace"
    source_repo = tmp_path / "source-repo"
    _write_skill(source_repo / "skills", "demo")
    _write_config(workspace, source_repo)
    sync = SkillSourceSync(
        workspace=workspace,
        config_dir=workspace / "config",
        extends_dir=workspace / "extends",
    )

    settings, sources = sync.load()

    assert settings.on_startup == "never"
    assert settings.background is False
    assert settings.interval_seconds == 0
    assert sources[0].name == "official"
    assert sources[0].local_dir == source_repo.resolve().as_posix()
    assert sources[0].target == "master"


def test_local_source_mirrors_complete_repo_under_extends_source(tmp_path):
    """A local source should mirror the whole repo into extends/{source}."""

    workspace = tmp_path / "workspace"
    source_repo = tmp_path / "source-repo"
    _write_skill(source_repo / "skills", "demo", body="version one")
    (source_repo / "hooks").mkdir(parents=True)
    (source_repo / "hooks" / "pre.py").write_text("hook\n", encoding="utf-8")
    (source_repo / "shared").mkdir()
    (source_repo / "shared" / "helper.py").write_text("helper\n", encoding="utf-8")
    (source_repo / ".git").mkdir()
    (source_repo / ".git" / "HEAD").write_text("ignored\n", encoding="utf-8")
    (source_repo / "tests").mkdir()
    (source_repo / "tests" / "test_demo.py").write_text("ignored\n", encoding="utf-8")
    _write_config(workspace, source_repo)
    sync = SkillSourceSync(
        workspace=workspace,
        config_dir=workspace / "config",
        extends_dir=workspace / "extends",
    )

    result = sync.sync()

    source_result = result.sources[0]
    assert source_result.name == "official"
    assert source_result.status == "synced"
    assert source_result.new == ["demo"]
    assert (workspace / "extends" / "official" / "skills" / "demo" / "SKILL.md").is_file()
    assert (workspace / "extends" / "official" / "skills" / "demo" / "scripts" / "main.py").is_file()
    assert (workspace / "extends" / "official" / "skills" / "demo" / "references" / "guide.md").is_file()
    assert (workspace / "extends" / "official" / "hooks" / "pre.py").is_file()
    assert (workspace / "extends" / "official" / "shared" / "helper.py").is_file()
    assert not (workspace / "extends" / "official" / ".git").exists()
    assert not (workspace / "extends" / "official" / "tests").exists()
    roots = sync.skill_roots()
    assert [(root.source, root.root) for root in roots] == [
        ("official", (workspace / "extends" / "official" / "skills").resolve())
    ]


def test_skill_repo_placeholder_points_to_repo_root(tmp_path, monkeypatch):
    """${ZHICE_AGENT_SKILL_REPO} should point at a source repo root."""

    workspace = tmp_path / "workspace"
    skill_repo = tmp_path / "skill-repo"
    monkeypatch.setenv("ZHICE_AGENT_SKILL_REPO", str(tmp_path / "ignored-env-repo"))
    _write_skill(skill_repo / "skills", "demo")
    config_dir = workspace / "config"
    config_dir.mkdir(parents=True)
    _write_skills_section(
        config_dir,
        """
sync:
  on_startup: never
  background:
    enabled: false
    interval_seconds: 0
sources:
  - name: official
    sync: true
    local_dir: "${ZHICE_AGENT_SKILL_REPO}"
""",
    )
    sync = SkillSourceSync(
        workspace=workspace,
        config_dir=config_dir,
        extends_dir=workspace / "extends",
        skill_repo=skill_repo,
    )

    result = sync.sync()

    assert result.sources[0].new == ["demo"]
    assert (workspace / "extends" / "official" / "skills" / "demo" / "SKILL.md").is_file()
    assert [(root.source, root.root) for root in sync.skill_roots()] == [
        ("official", (workspace / "extends" / "official" / "skills").resolve())
    ]


def test_skill_repo_environment_overrides_builtin_default(tmp_path, monkeypatch):
    """The dotenv-backed environment should override the packaged Skill repo."""

    workspace = tmp_path / "workspace"
    skill_repo = tmp_path / "environment-skill-repo"
    _write_skill(skill_repo / "skills", "demo")
    config_dir = workspace / "config"
    config_dir.mkdir(parents=True)
    _write_skills_section(
        config_dir,
        """
sync:
  on_startup: never
sources:
  - name: official
    sync: true
    local_dir: "${ZHICE_AGENT_SKILL_REPO}"
""",
    )
    monkeypatch.setenv("ZHICE_AGENT_SKILL_REPO", str(skill_repo))
    sync = SkillSourceSync(
        workspace=workspace,
        config_dir=config_dir,
        extends_dir=workspace / "extends",
    )

    result = sync.sync()

    assert sync.skill_repo == skill_repo.resolve()
    assert result.sources[0].new == ["demo"]
    assert (workspace / "extends" / "official" / "skills" / "demo" / "SKILL.md").is_file()


def test_empty_skill_repo_environment_uses_builtin_default(tmp_path, monkeypatch):
    """An empty dotenv value should behave like an omitted override."""

    monkeypatch.setenv("ZHICE_AGENT_SKILL_REPO", "  ")

    sync = SkillSourceSync(
        workspace=tmp_path / "workspace",
        config_dir=tmp_path / "workspace" / "config",
        extends_dir=tmp_path / "workspace" / "extends",
    )

    assert sync.skill_repo == (Path(__file__).resolve().parents[3] / "skill_repo").resolve()


def test_default_skill_repo_points_to_repo_root():
    """The built-in Skill repo should be the source repo root, not its skills subdir."""

    from agent.skills.sync import _default_skill_repo

    default_repo = _default_skill_repo()

    assert default_repo.name == "skill_repo"
    assert (default_repo / "skills").is_dir()


def test_local_source_reports_unchanged_updated_and_removed(tmp_path):
    """Repeated syncs should distinguish unchanged, changed, and removed packages."""

    workspace = tmp_path / "workspace"
    source_repo = tmp_path / "source-repo"
    skill_dir = _write_skill(source_repo / "skills", "demo", body="version one")
    stale_dir = _write_skill(source_repo / "skills", "stale", body="remove me")
    _write_config(workspace, source_repo)
    sync = SkillSourceSync(
        workspace=workspace,
        config_dir=workspace / "config",
        extends_dir=workspace / "extends",
    )

    first = sync.sync()
    second = sync.sync()
    skill_dir.joinpath("SKILL.md").write_text(_skill_markdown("demo", "version two"), encoding="utf-8")
    shutil.rmtree(stale_dir)
    third = sync.sync()

    assert first.sources[0].new == ["demo", "stale"]
    assert second.sources[0].status == "up_to_date"
    assert second.sources[0].unchanged == ["demo", "stale"]
    assert third.sources[0].status == "synced"
    assert third.sources[0].changed == ["demo"]
    assert third.sources[0].removed == ["stale"]
    assert "version two" in (
        workspace / "extends" / "official" / "skills" / "demo" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert not (workspace / "extends" / "official" / "skills" / "stale").exists()


def test_startup_always_syncs_updates(tmp_path):
    """sync.on_startup=always should refresh an existing runtime Skill."""

    workspace = tmp_path / "workspace"
    source_repo = tmp_path / "source-repo"
    skill_dir = _write_skill(source_repo / "skills", "demo", body="version one")
    _write_config(workspace, source_repo, on_startup="always")
    sync = SkillSourceSync(
        workspace=workspace,
        config_dir=workspace / "config",
        extends_dir=workspace / "extends",
    )
    sync.sync()
    skill_dir.joinpath("SKILL.md").write_text(_skill_markdown("demo", "version two"), encoding="utf-8")

    result = sync.sync_on_startup()

    assert result is not None
    assert result.sources[0].changed == ["demo"]


def test_startup_if_missing_is_rejected(tmp_path):
    """sync.on_startup only supports never and always."""

    workspace = tmp_path / "workspace"
    source_repo = tmp_path / "source-repo"
    _write_skill(source_repo / "skills", "demo")
    _write_config(workspace, source_repo, on_startup="if_missing")
    sync = SkillSourceSync(
        workspace=workspace,
        config_dir=workspace / "config",
        extends_dir=workspace / "extends",
    )

    with pytest.raises(SkillSyncError, match="sync.on_startup"):
        sync.load()


def test_sync_reports_unknown_requested_source(tmp_path):
    """Manual sync should reject names that are not configured sources."""

    workspace = tmp_path / "workspace"
    source_repo = tmp_path / "source-repo"
    _write_skill(source_repo / "skills", "demo")
    _write_config(workspace, source_repo)
    sync = SkillSourceSync(
        workspace=workspace,
        config_dir=workspace / "config",
        extends_dir=workspace / "extends",
    )

    result = sync.sync(source_names=["missing"])

    assert result.errors == [
        {"source": "missing", "message": "Skill source is not configured"}
    ]
    assert not (workspace / "extends" / "official" / "skills" / "demo").exists()


def test_source_sync_false_skips_source(tmp_path):
    """sources[].sync=false should skip that source without installing Skills."""

    workspace = tmp_path / "workspace"
    source_repo = tmp_path / "source-repo"
    _write_skill(source_repo / "skills", "demo")
    _write_config(workspace, source_repo, sync_enabled=False)
    sync = SkillSourceSync(
        workspace=workspace,
        config_dir=workspace / "config",
        extends_dir=workspace / "extends",
    )

    result = sync.sync()

    assert result.sources[0].status == "skipped"
    assert result.sources[0].message == "sync=false"
    assert not (workspace / "extends" / "official" / "skills" / "demo").exists()


def test_sync_reports_empty_source_directory(tmp_path):
    """A configured source with no Skill packages should explain why nothing installed."""

    workspace = tmp_path / "workspace"
    source_repo = tmp_path / "source-repo"
    (source_repo / "skills").mkdir(parents=True)
    source_repo.joinpath("README.md").write_text("empty source\n", encoding="utf-8")
    _write_config(workspace, source_repo)
    sync = SkillSourceSync(
        workspace=workspace,
        config_dir=workspace / "config",
        extends_dir=workspace / "extends",
    )

    result = sync.sync()

    assert result.sources[0].status == "skipped"
    assert result.sources[0].message.startswith("no Skill packages found in")


def test_runtime_extends_dir_must_stay_in_workspace(tmp_path):
    """Configured runtime write location should not escape the workspace."""

    workspace = tmp_path / "workspace"
    config_dir = workspace / "config"
    config_dir.mkdir(parents=True)
    _write_skills_section(
        config_dir,
        f"""
extends_dir: "{_yaml_path(tmp_path / "outside-extends")}"
sync:
  on_startup: always
sources: []
""",
    )
    sync = SkillSourceSync(
        workspace=workspace,
        config_dir=config_dir,
        extends_dir=workspace / "extends",
    )

    with pytest.raises(SkillSyncError, match="extends_dir"):
        sync.load()


def _write_config(workspace, source_repo, *, on_startup="never", sync_enabled=True):
    config_dir = workspace / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    _write_skills_section(
        config_dir,
        f"""
sync:
  on_startup: {on_startup}
  background:
    enabled: false
    interval_seconds: 0
  log: changes_only
sources:
  - name: official
    sync: {_yaml_bool(sync_enabled)}
    local_dir: "{_yaml_path(source_repo)}"
""",
    )


def _write_skill(root, name, *, body="demo body"):
    skill_dir = root / name
    script_dir = skill_dir / "scripts"
    references_dir = skill_dir / "references"
    script_dir.mkdir(parents=True, exist_ok=True)
    references_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(_skill_markdown(name, body), encoding="utf-8")
    script_dir.joinpath("main.py").write_text("print('{}')\n", encoding="utf-8")
    references_dir.joinpath("guide.md").write_text("guide\n", encoding="utf-8")
    return skill_dir


def _skill_markdown(name, body):
    return f"""---
name: {name}
description: Demo skill.
---

{body}
"""


def _yaml_path(path):
    return path.resolve().as_posix()


def _yaml_bool(value):
    return "true" if value else "false"
