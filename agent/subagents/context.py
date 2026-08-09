"""Fresh, bounded context assembly for one delegated child Agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.message import Message
from agent.prompt_loader import PromptLoader, PromptNotFoundError
from agent.protocols.llm import ContextBudget
from agent.protocols.skill import SkillError, SkillInfo, SkillProvider


class FilteredSkillProvider:
    """Expose only parent-visible Skills selected by one Profile."""

    def __init__(self, parent: SkillProvider, allowed_skills: tuple[str, ...]):
        self.parent = parent
        self.allowed_skills = frozenset(allowed_skills)

    def list_skills(self) -> list[SkillInfo]:
        if not self.allowed_skills:
            return []
        return [
            skill
            for skill in self.parent.list_skills()
            if skill.qualified_name in self.allowed_skills
        ]

    def get_skill(self, name: str, source: str | None = None) -> SkillInfo:
        skill = self.parent.get_skill(name, source)
        if skill.qualified_name not in self.allowed_skills:
            raise SkillError(
                "Subagent Skill is not allowed.",
                "SUBAGENT_SKILL_NOT_ALLOWED",
                {"skill": skill.qualified_name},
            )
        return skill

    def get_skill_body(self, name: str, source: str | None = None) -> str:
        skill = self.get_skill(name, source)
        return self.parent.get_skill_body(skill.name, skill.source)


class SubagentContextBuilder:
    """Build a child-only system prompt without copying parent history."""

    def __init__(
        self,
        prompt_loader: PromptLoader,
        *,
        profile_name: str,
        profile_description: str,
        expected_output: str = "",
        skills: SkillProvider | None = None,
        preload_skills: tuple[str, ...] = (),
        max_message_chars: int = 8000,
        max_skill_chars: int = 12000,
    ):
        self.prompt_loader = prompt_loader
        self.profile_name = profile_name
        self.profile_description = profile_description
        self.expected_output = expected_output
        self.skills = skills
        self.preload_skills = preload_skills
        self.max_message_chars = max_message_chars
        self.max_skill_chars = max_skill_chars

    def build(
        self,
        history: list[Message],
        user_message: Message,
        workspace: Path,
        session_id: str,
        context_budget: ContextBudget | None = None,
    ) -> list[dict[str, Any]]:
        """Return a fresh system prompt and the delegated task only."""

        del history, context_budget
        if user_message.role != "user":
            raise ValueError("user_message must have role 'user'")
        return [
            {
                "role": "system",
                "content": self._build_system_prompt(workspace, session_id),
            },
            {
                "role": "user",
                "content": self._truncate(user_message.content),
            },
        ]

    def _build_system_prompt(self, workspace: Path, session_id: str) -> str:
        base = self.prompt_loader.load("subagent").strip()
        parts = [
            "# Subagent",
            base,
            "# Profile",
            f"name={self.profile_name}\ndescription={self.profile_description}",
        ]
        if self.expected_output:
            parts.extend(["# Expected Output", self._truncate(self.expected_output)])
        memory_policy = self._optional_prompt("memory_policy")
        if memory_policy:
            parts.extend(["# Memory Policy", memory_policy])
        skill_text = self._preloaded_skill_text()
        if skill_text:
            parts.extend(["# Preloaded Skills", skill_text])
        parts.extend(
            [
                "# Runtime",
                "Use only the provided tool schemas and return a concise result to the parent Agent.",
                f"workspace={workspace}",
                f"child_session_id={session_id}",
            ]
        )
        return "\n\n".join(parts)

    def _optional_prompt(self, name: str) -> str:
        try:
            return self.prompt_loader.load(name).strip()
        except PromptNotFoundError:
            return ""

    def _preloaded_skill_text(self) -> str:
        if self.skills is None or not self.preload_skills:
            return ""
        chunks: list[str] = []
        used = 0
        for qualified_name in self.preload_skills:
            source, separator, name = qualified_name.partition("/")
            try:
                body = self.skills.get_skill_body(name if separator else source, source if separator else None)
            except Exception:  # noqa: BLE001 - one unavailable preload must not expose another Skill.
                continue
            remaining = self.max_skill_chars - used
            if remaining <= 0:
                break
            bounded = body[:remaining]
            chunks.append(f"## {qualified_name}\n{bounded}")
            used += len(bounded)
        return "\n\n".join(chunks)

    def _truncate(self, value: str) -> str:
        if len(value) <= self.max_message_chars:
            return value
        return value[: self.max_message_chars] + "[truncated]"
