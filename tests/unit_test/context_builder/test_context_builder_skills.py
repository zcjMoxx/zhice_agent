"""Tests for injecting Skill summaries into chat context."""

from pathlib import Path

from agent.message import Message
from agent.prompt_loader import PromptLoader
from agent.protocols.skill import SkillInfo


def test_context_builder_injects_available_skill_summaries(tmp_path):
    """Only compact summaries should enter the system prompt by default."""

    from agent.core.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    provider = FakeSkillProvider(
        [
            _skill_info(tmp_path, "demo", summary="Demo summary."),
            _skill_info(tmp_path, "report", summary="Report summary."),
        ]
    )
    builder = ContextBuilder(PromptLoader(prompts_dir), skills=provider)

    messages = builder.build([], Message(role="user", content="hello"), tmp_path, "default")
    system = messages[0]["content"]

    assert "# Available Skills" in system
    assert "- `official/demo`: Demo summary." in system
    assert "- `official/report`: Report summary." in system
    assert "readonly" not in system


def test_context_builder_omits_empty_or_broken_skill_provider(tmp_path):
    """Skill discovery failures should not block ordinary chat context."""

    from agent.core.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)

    empty = ContextBuilder(PromptLoader(prompts_dir), skills=FakeSkillProvider([])).build(
        [], Message(role="user", content="hello"), tmp_path, "default"
    )
    broken = ContextBuilder(PromptLoader(prompts_dir), skills=BrokenSkillProvider()).build(
        [], Message(role="user", content="hello"), tmp_path, "default"
    )

    assert "# Available Skills" not in empty[0]["content"]
    assert "# Available Skills" not in broken[0]["content"]


def test_context_builder_limits_skill_count_and_summary_chars(tmp_path):
    """Available Skills prompt should have count and character bounds."""

    from agent.core.context import ContextBuilder

    prompts_dir = _write_required_prompts(tmp_path)
    provider = FakeSkillProvider(
        [
            _skill_info(tmp_path, "alpha", summary="a" * 100),
            _skill_info(tmp_path, "beta", summary="b" * 100),
        ]
    )
    builder = ContextBuilder(
        PromptLoader(prompts_dir),
        skills=provider,
        max_skill_summaries=1,
        max_skill_summary_chars=80,
    )

    system = builder.build([], Message(role="user", content="hello"), tmp_path, "default")[0][
        "content"
    ]

    assert "`official/alpha`" in system
    assert "`official/beta`" not in system
    assert "[truncated]" in system


def test_context_builder_depends_only_on_skill_protocol():
    """ContextBuilder should not import the concrete SkillLoader."""

    source = Path("agent/core/context.py").read_text(encoding="utf-8")

    assert "agent.skills" not in source
    assert "SkillLoader" not in source


def _write_required_prompts(tmp_path: Path) -> Path:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "identity.md").write_text("identity prompt", encoding="utf-8")
    (prompts_dir / "tool_use_policy.md").write_text("tool policy prompt", encoding="utf-8")
    (prompts_dir / "skills_intro.md").write_text("skills intro prompt", encoding="utf-8")
    return prompts_dir


def _skill_info(tmp_path, name, *, summary):
    root = tmp_path / "skills" / name
    return SkillInfo(
        source="official",
        name=name,
        qualified_name=f"official/{name}",
        description=f"{name} description",
        root=root,
        skill_file=root / "SKILL.md",
        scripts_dir=root / "scripts",
        summary=summary,
    )


class FakeSkillProvider:
    def __init__(self, skills):
        self.skills = skills

    def list_skills(self):
        return list(self.skills)

    def get_skill(self, name, source=None):
        raise AssertionError(name)

    def get_skill_body(self, name, source=None):
        raise AssertionError(name)


class BrokenSkillProvider:
    def list_skills(self):
        raise RuntimeError("boom")

    def get_skill(self, name, source=None):
        raise AssertionError(name)

    def get_skill_body(self, name, source=None):
        raise AssertionError(name)

