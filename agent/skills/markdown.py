"""Minimal SKILL.md frontmatter and summary parsing."""

from __future__ import annotations

from dataclasses import dataclass

from agent.protocols.skill import SkillError

REQUIRED_SKILL_FIELDS = {"description"}


@dataclass(frozen=True)
class ParsedSkillMarkdown:
    """Parsed frontmatter, body, and compact summary for a Skill file."""

    frontmatter: dict[str, str]
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


def _parse_frontmatter_lines(lines: list[str]) -> dict[str, str]:
    """Parse simple key: value frontmatter without a YAML dependency."""

    result: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            raise SkillError(
                "Skill frontmatter supports only simple key: value lines.",
                "INVALID_SKILL_FRONTMATTER",
            )
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            raise SkillError("Skill frontmatter contains an empty key.", "INVALID_SKILL_FRONTMATTER")
        result[key] = _parse_scalar(raw_value.strip(), key=key)
    return result


def _parse_scalar(value: str, *, key: str) -> str:
    """Parse one simple scalar value."""

    unquoted = _strip_matching_quotes(value)
    if not unquoted:
        raise SkillError(
            f"Skill frontmatter field is empty: {key}",
            "INVALID_SKILL_FRONTMATTER",
            {"field": key},
        )
    return unquoted


def _strip_matching_quotes(value: str) -> str:
    """Remove one matching quote pair around a scalar value."""

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1].strip()
    return value.strip()


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
