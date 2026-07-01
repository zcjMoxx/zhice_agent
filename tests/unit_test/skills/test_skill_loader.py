"""Tests for local Skill discovery."""

import pytest

from agent.protocols.skill import SkillError
from agent.skills.loader import SkillLoader


def test_loader_returns_empty_for_missing_skills_dir(tmp_path):
    """A workspace with no skills should still start cleanly."""

    loader = SkillLoader(tmp_path / "skills")

    assert loader.list_skills() == []
    assert loader.load_errors == []


def test_loader_discovers_valid_skill_and_reads_body(tmp_path):
    """Valid direct child Skill directories should be exposed by name."""

    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "demo")
    loader = SkillLoader(skills_dir)

    skills = loader.list_skills()

    assert [skill.name for skill in skills] == ["demo"]
    assert [skill.qualified_name for skill in skills] == ["default-1/demo"]
    assert skills[0].source == "default-1"
    assert skills[0].description == "Demo skill."
    assert skills[0].metadata["frontmatter_name"] == "demo"
    assert skills[0].metadata["name_matches_directory"] is True
    assert "description: Demo skill." in loader.get_skill_body("demo")


def test_loader_records_invalid_directories_without_blocking_startup(tmp_path):
    """Broken Skill directories are skipped and recorded."""

    skills_dir = tmp_path / "skills"
    (skills_dir / ".hidden").mkdir(parents=True)
    (skills_dir / "__pycache__").mkdir()
    (skills_dir / "broken").mkdir()
    _write_skill(skills_dir, "valid")
    loader = SkillLoader(skills_dir)

    skills = loader.list_skills()

    assert [skill.name for skill in skills] == ["valid"]
    assert loader.load_errors == [
        {
            "path": str(skills_dir / "broken"),
            "code": "SKILL_READ_ERROR",
            "message": "Skill is missing SKILL.md.",
        }
    ]


def test_loader_rejects_invalid_directory_skill_name(tmp_path):
    """Canonical Skill names should stay provider/tool compatible."""

    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "bad skill")
    loader = SkillLoader(skills_dir)

    assert loader.list_skills() == []
    assert loader.load_errors[0]["code"] == "INVALID_SKILL_NAME"


def test_loader_uses_directory_name_when_frontmatter_name_differs(tmp_path):
    """Frontmatter name mismatches should warn without changing identity."""

    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "code-review", name="review")
    loader = SkillLoader(skills_dir)

    skills = loader.list_skills()

    assert [skill.name for skill in skills] == ["code-review"]
    assert skills[0].metadata["frontmatter_name"] == "review"
    assert skills[0].metadata["name_matches_directory"] is False
    assert loader.load_errors[0]["code"] == "SKILL_NAME_MISMATCH"


def test_loader_uses_directory_name_when_frontmatter_name_is_missing(tmp_path):
    """Missing frontmatter name should warn without blocking discovery."""

    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "demo", name="")
    loader = SkillLoader(skills_dir)

    skills = loader.list_skills()

    assert [skill.name for skill in skills] == ["demo"]
    assert skills[0].metadata["frontmatter_name"] == ""
    assert skills[0].metadata["name_matches_directory"] is False
    assert loader.load_errors[0]["code"] == "MISSING_SKILL_FIELD"


def test_loader_allows_same_name_across_sources_and_requires_qualified_lookup(tmp_path):
    """Cross-source same-name Skills should both load and reject ambiguous aliases."""

    official = tmp_path / "official" / "skills"
    team = tmp_path / "team" / "skills"
    _write_skill(official, "review")
    _write_skill(team, "review")
    loader = SkillLoader([("official", official), ("team", team)])

    skills = loader.list_skills()

    assert [skill.qualified_name for skill in skills] == ["official/review", "team/review"]
    assert loader.get_skill("official/review").source == "official"
    assert loader.get_skill("review", source="team").qualified_name == "team/review"
    with pytest.raises(SkillError) as error:
        loader.get_skill("review")

    assert error.value.code == "AMBIGUOUS_SKILL"
    assert error.value.metadata["candidates"] == ["official/review", "team/review"]


def test_loader_get_skill_reports_unknown_and_invalid_names(tmp_path):
    """Lookups should return structured SkillError values."""

    loader = SkillLoader(tmp_path / "skills")

    with pytest.raises(SkillError) as unknown:
        loader.get_skill("missing")
    with pytest.raises(SkillError) as invalid:
        loader.get_skill("../missing")

    assert unknown.value.code == "UNKNOWN_SKILL"
    assert invalid.value.code == "INVALID_SKILL_NAME"


def _write_skill(skills_dir, directory, *, name=None):
    skill_dir = skills_dir / directory
    skill_dir.mkdir(parents=True)
    name_line = f"name: {name if name is not None else directory}\n" if name != "" else ""
    skill_dir.joinpath("SKILL.md").write_text(
        f"""---
{name_line}description: Demo skill.
---

Demo body.
""",
        encoding="utf-8",
    )
    return skill_dir
