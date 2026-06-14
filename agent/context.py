"""Build LLM message context for one Agent turn."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.message import Message
from agent.prompt_loader import PromptLoader

DEFAULT_CONTEXT_PROMPTS = ["identity", "tool_use_policy", "skills_intro"]


class ContextBuilder:
    """Assemble system prompt, recent history, and the current user message."""

    def __init__(
        self,
        prompt_loader: PromptLoader,
        max_history_messages: int = 30,
        max_message_chars: int = 8000,
    ):
        if max_history_messages < 0:
            raise ValueError("max_history_messages must be non-negative")
        if max_message_chars <= 0:
            raise ValueError("max_message_chars must be positive")

        self.prompt_loader = prompt_loader
        self.max_history_messages = max_history_messages
        self.max_message_chars = max_message_chars

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
        prompts = self.prompt_loader.load_many(DEFAULT_CONTEXT_PROMPTS)
        return "\n\n".join(
            [
                "# Identity",
                prompts["identity"].strip(),
                "# Tool Use Policy",
                prompts["tool_use_policy"].strip(),
                "# Skill Use Policy",
                prompts["skills_intro"].strip(),
                "# Runtime",
                "Use tools only through the provided tool schemas.",
                f"workspace={workspace}",
                f"session_id={session_id}",
            ]
        )

    def _message_to_llm_dict(self, message: Message) -> dict[str, Any] | None:
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
        if len(content) <= self.max_message_chars:
            return content
        marker = "[truncated]"
        return f"{content[: self.max_message_chars]}{marker}"


def _tool_call_ids(tool_calls: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for tool_call in tool_calls:
        raw_id = tool_call.get("id") if isinstance(tool_call, dict) else None
        if not isinstance(raw_id, str) or not raw_id:
            return []
        if raw_id in ids:
            return []
        ids.append(raw_id)
    return ids
