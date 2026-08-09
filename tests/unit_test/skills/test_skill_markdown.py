"""Tests for constrained SKILL.md parsing."""

import pytest

from agent.protocols.skill import SkillError
from agent.skills.markdown import parse_skill_markdown


def test_parse_skill_markdown_extracts_frontmatter_body_and_summary():
    """A minimal valid SKILL.md should produce metadata and a summary."""

    parsed = parse_skill_markdown(
        """---
name: demo
description: Demo skill.
---

# Demo

Use this skill for tests.
"""
    )

    assert parsed.frontmatter["name"] == "demo"
    assert parsed.frontmatter["description"] == "Demo skill."
    assert parsed.body.startswith("# Demo")
    assert parsed.summary == "Demo skill. Use this skill for tests."


def test_parse_skill_markdown_keeps_optional_frontmatter_as_text():
    """Optional frontmatter fields are accepted but not interpreted by core code."""

    parsed = parse_skill_markdown(
        """---
name: demo
description: Demo skill.
category: test
readonly: maybe
---
"""
    )

    assert parsed.frontmatter["category"] == "test"
    assert parsed.frontmatter["readonly"] == "maybe"


def test_parse_skill_markdown_requires_frontmatter():
    """Files without frontmatter are invalid Skills."""

    with pytest.raises(SkillError) as error:
        parse_skill_markdown("# Demo")

    assert error.value.code == "INVALID_SKILL_FRONTMATTER"


def test_parse_skill_markdown_requires_description_field():
    """Description stays required because summaries depend on it."""

    with pytest.raises(SkillError) as error:
        parse_skill_markdown(
            """---
name: demo
---
"""
        )

    assert error.value.code == "MISSING_SKILL_FIELD"
    assert error.value.metadata["field"] == "description"


def test_parse_skill_markdown_allows_missing_name_for_loader_fallback():
    """The loader owns canonical directory-name fallback for missing names."""

    parsed = parse_skill_markdown(
        """---
description: Demo skill.
---

Demo body.
"""
    )

    assert "name" not in parsed.frontmatter
    assert parsed.summary == "Demo skill. Demo body."


def test_parse_skill_markdown_truncates_summary():
    """Context summaries should stay bounded."""

    parsed = parse_skill_markdown(
        """---
name: demo
description: abcdefghij
---

klmnopqrst
""",
        max_summary_chars=15,
    )

    assert parsed.summary.endswith("[truncated]")
    assert len(parsed.summary) == 15


def test_parse_skill_markdown_rejects_duplicate_yaml_keys():
    with pytest.raises(SkillError) as error:
        parse_skill_markdown(
            """---
description: first
description: second
---
"""
        )

    assert error.value.code == "INVALID_SKILL_FRONTMATTER"
