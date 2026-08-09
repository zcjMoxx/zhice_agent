"""Minimal SKILL.md frontmatter and summary parsing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from agent.protocols.skill import SkillError

REQUIRED_SKILL_FIELDS = {"description"}


class _SkillFrontmatterLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate keys."""


def _unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise yaml.constructor.ConstructorError(
                "while constructing Skill frontmatter",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_SkillFrontmatterLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _unique_mapping,
)


@dataclass(frozen=True)
class ParsedSkillMarkdown:
    """Parsed frontmatter, body, and compact summary for a Skill file."""

    frontmatter: dict[str, Any]
    body: str
    summary: str


def parse_skill_markdown(text: str, *, max_summary_chars: int = 800) -> ParsedSkillMarkdown:
    """Parse the constrained frontmatter format supported by first-stage Skills."""

    if max_summary_chars <= 0:
        raise ValueError("max_summary_chars must be positive")
    if not text.startswith("---"):
        raise SkillError(
            "Skill markdown must start with frontmatter.",
            "INVALID_SKILL_FRONTMATTER",
        )

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError(
            "Skill markdown must start with frontmatter.",
            "INVALID_SKILL_FRONTMATTER",
        )

    end_index = _frontmatter_end_index(lines)
    if end_index is None:
        raise SkillError(
            "Skill frontmatter is not closed.",
            "INVALID_SKILL_FRONTMATTER",
        )

    frontmatter = _parse_frontmatter_lines(lines[1:end_index])
    missing = sorted(REQUIRED_SKILL_FIELDS - set(frontmatter))
    if missing:
        raise SkillError(
            f"Skill frontmatter is missing required field: {missing[0]}",
            "MISSING_SKILL_FIELD",
            {"field": missing[0]},
        )

    body = "\n".join(lines[end_index + 1 :]).strip()
    summary = _build_summary(
        description=str(frontmatter["description"]),
        body=body,
        max_chars=max_summary_chars,
    )
    return ParsedSkillMarkdown(frontmatter=frontmatter, body=body, summary=summary)


def _frontmatter_end_index(lines: list[str]) -> int | None:
    """Return the closing frontmatter delimiter line index."""

    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return index
    return None


def _parse_frontmatter_lines(lines: list[str]) -> dict[str, Any]:
    """Parse bounded YAML frontmatter, including explicit runtime mappings."""

    raw = "\n".join(lines)
    if len(raw.encode("utf-8")) > 64 * 1024:
        raise SkillError("Skill frontmatter is too large.", "INVALID_SKILL_FRONTMATTER")
    try:
        value = yaml.load(raw, Loader=_SkillFrontmatterLoader) if raw.strip() else {}
    except yaml.YAMLError as exc:
        raise SkillError(
            "Skill frontmatter is invalid YAML.",
            "INVALID_SKILL_FRONTMATTER",
        ) from exc
    if not isinstance(value, dict):
        raise SkillError(
            "Skill frontmatter must be a mapping.",
            "INVALID_SKILL_FRONTMATTER",
        )
    if any(not isinstance(key, str) or not key.strip() for key in value):
        raise SkillError(
            "Skill frontmatter keys must be non-empty strings.",
            "INVALID_SKILL_FRONTMATTER",
        )
    return {str(key).strip(): item for key, item in value.items()}


def _build_summary(*, description: str, body: str, max_chars: int) -> str:
    """Build a short summary from description and the first body paragraph."""

    parts = [description.strip()]
    first_paragraph = _first_body_paragraph(body)
    if first_paragraph and first_paragraph not in parts:
        parts.append(first_paragraph)
    summary = " ".join(part for part in parts if part)
    if len(summary) <= max_chars:
        return summary
    marker = "[truncated]"
    if max_chars <= len(marker):
        return marker[:max_chars]
    return f"{summary[: max_chars - len(marker)]}{marker}"


def _first_body_paragraph(body: str) -> str:
    """Return the first non-heading paragraph from markdown body text."""

    paragraph_lines: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            if paragraph_lines:
                break
            continue
        if line.startswith("#") or line == "---":
            continue
        paragraph_lines.append(line)
    return " ".join(paragraph_lines)
