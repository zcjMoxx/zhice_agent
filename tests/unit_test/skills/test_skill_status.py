"""Tests for persistent Skill source operational status."""

from __future__ import annotations

import json

from agent.skills.loader import SkillLoader
from agent.skills.sync import SkillSourceSync


def test_sync_persists_safe_source_status_and_load_health(tmp_path):
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    (source / "skills" / "demo").mkdir(parents=True)
    (source / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo.\n---\n\nDemo.\n",
        encoding="utf-8",
    )
    _config(workspace, source)
    sync = SkillSourceSync(
        workspace=workspace,
        config_dir=workspace / "config",
        extends_dir=workspace / "extends",
    )

    sync.sync()
    loader = SkillLoader(sync.skill_roots(), cache_path=workspace / "state" / "index.json")
    statuses = sync.status_store.list_statuses(skill_loader=loader, skill_sync=sync)

    assert statuses[0]["source"] == "official"
    assert statuses[0]["health"] == "healthy"
    assert statuses[0]["skill_count"] == 1
    assert statuses[0]["materialized_root"] == "extends/official"
    serialized = (workspace / "state" / "skill_sources.json").read_text(encoding="utf-8")
    assert str(source) not in serialized
    assert "credential" not in serialized


def test_corrupt_status_cache_is_rebuilt(tmp_path):
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    (source / "skills").mkdir(parents=True)
    _config(workspace, source)
    sync = SkillSourceSync(
        workspace=workspace,
        config_dir=workspace / "config",
        extends_dir=workspace / "extends",
    )
    status_path = workspace / "state" / "skill_sources.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text("{broken", encoding="utf-8")

    sync.sync()

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["sources"]["official"]["last_status"] == "skipped"


def _config(workspace, source):
    config = workspace / "config"
    config.mkdir(parents=True)
    (config / "config.yml").write_text(
        f"""schema_version: 1
skills:
  sync:
    on_startup: never
  sources:
    - name: official
      sync: true
      local_dir: "{source.as_posix()}"
      target: master
""",
        encoding="utf-8",
    )
