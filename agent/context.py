"""Build LLM message context for one Agent turn."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.message import Message
from agent.prompt_loader import PromptLoader
from agent.protocols.skill import SkillProvider

DEFAULT_CONTEXT_PROMPTS = ["identity", "tool_use_policy", "skills_intro"]


class ContextBuilder:
    """Assemble system prompt, recent history, and the current user message."""

    def __init__(
        self,
        prompt_loader: PromptLoader,
        skills: SkillProvider | None = None,
        max_history_messages: int = 30,
        max_message_chars: int = 8000,
        max_skill_summaries: int = 50,
        max_skill_summary_chars: int = 5000,
    ):
        """Configure prompt source and history/message size limits."""

        if max_history_messages < 0:
            raise ValueError("max_history_messages must be non-negative")
        if max_message_chars <= 0:
            raise ValueError("max_message_chars must be positive")
        if max_skill_summaries < 0:
            raise ValueError("max_skill_summaries must be non-negative")
        if max_skill_summary_chars <= 0:
            raise ValueError("max_skill_summary_chars must be positive")

        self.prompt_loader = prompt_loader
        self.skills = skills
        self.max_history_messages = max_history_messages
        self.max_message_chars = max_message_chars
        self.max_skill_summaries = max_skill_summaries
        self.max_skill_summary_chars = max_skill_summary_chars

    def build(
        self,
        history: list[Message],
        user_message: Message,
        workspace: Path,
        session_id: str,
    ) -> list[dict[str, Any]]:
        """Return OpenAI-style messages for one chat turn."""

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": self._build_system_prompt(workspace=workspace, session_id=session_id),
            }
        ]

        recent_history = history[-self.max_history_messages :] if self.max_history_messages else []
        messages.extend(self._history_to_llm_dicts(recent_history))

        current_user = self._message_to_llm_dict(user_message)
        if current_user is None or current_user["role"] != "user":
            raise ValueError("user_message must have role 'user'")
        messages.append(current_user)
        return messages

    def _build_system_prompt(self, workspace: Path, session_id: str) -> str:
        """Combine runtime prompts with workspace/session facts for the LLM."""

        prompts = self.prompt_loader.load_many(DEFAULT_CONTEXT_PROMPTS)
        parts = [
            "# Identity",
            prompts["identity"].strip(),
            "# Tool Use Policy",
            prompts["tool_use_policy"].strip(),
            "# Skill Use Policy",
            prompts["skills_intro"].strip(),
        ]
        skill_summary = self._build_available_skills_prompt()
        if skill_summary:
            parts.extend(["# Available Skills", skill_summary])
        parts.extend(
            [
                "# Runtime",
                "Use tools only through the provided tool schemas.",
                f"workspace={workspace}",
                f"session_id={session_id}",
            ]
        )
        return "\n\n".join(parts)

    def _message_to_llm_dict(self, message: Message) -> dict[str, Any] | None:
        """Convert an internal Message to the provider-neutral chat shape."""

        if message.role not in {"system", "user", "assistant", "tool"}:
            return None

        converted: dict[str, Any] = {
            "role": message.role,
            "content": self._truncate(message.content),
        }
        if message.name:
            converted["name"] = message.name
        if message.tool_call_id:
            converted["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            converted["tool_calls"] = message.tool_calls
        return converted

    def _history_to_llm_dicts(self, history: list[Message]) -> list[dict[str, Any]]:
        """Convert history while preserving OpenAI-compatible tool call blocks."""

        converted: list[dict[str, Any]] = []
        index = 0
        while index < len(history):
            message = history[index]
            if message.role == "tool":
                index += 1
                continue

            if message.role == "assistant" and message.tool_calls:
                tool_call_ids = _tool_call_ids(message.tool_calls)
                block: list[dict[str, Any]] = []
                assistant_message = self._message_to_llm_dict(message)
                if assistant_message is not None:
                    block.append(assistant_message)

                seen_tool_call_ids: set[str] = set()
                cursor = index + 1
                while cursor < len(history) and history[cursor].role == "tool":
                    tool_message = history[cursor]
                    tool_call_id = tool_message.tool_call_id
                    if tool_call_id in tool_call_ids and tool_call_id not in seen_tool_call_ids:
                        tool_dict = self._message_to_llm_dict(tool_message)
                        if tool_dict is not None:
                            block.append(tool_dict)
                            seen_tool_call_ids.add(tool_call_id)
                    cursor += 1

                if tool_call_ids and seen_tool_call_ids == set(tool_call_ids):
                    converted.extend(block)
                index = cursor
                continue

            message_dict = self._message_to_llm_dict(message)
            if message_dict is not None:
                converted.append(message_dict)
            index += 1
        return converted

    def _truncate(self, content: str) -> str:
        """Trim overly long message content before sending it to the LLM."""

        if len(content) <= self.max_message_chars:
            return content
        marker = "[truncated]"
        return f"{content[: self.max_message_chars]}{marker}"

    def _build_available_skills_prompt(self) -> str:
        """Return a compact list of available Skills for the system prompt."""

        if self.skills is None or self.max_skill_summaries == 0:
            return ""
        try:
            skills = self.skills.list_skills()
        except Exception:  # noqa: BLE001 - bad Skill metadata should not block chat startup.
            return ""
        lines = []
        for skill in skills[: self.max_skill_summaries]:
            lines.append(f"- `{skill.qualified_name}`: {skill.summary}")
        text = "\n".join(lines)
        if len(text) <= self.max_skill_summary_chars:
            return text
        marker = "[truncated]"
        return f"{text[: self.max_skill_summary_chars]}{marker}"


def _tool_call_ids(tool_calls: list[dict[str, Any]]) -> list[str]:
    """Return valid unique tool call ids, or empty when the block is unsafe."""

    ids: list[str] = []
    for tool_call in tool_calls:
        raw_id = tool_call.get("id") if isinstance(tool_call, dict) else None
        if not isinstance(raw_id, str) or not raw_id:
            return []
        if raw_id in ids:
            return []
        ids.append(raw_id)
    return ids
