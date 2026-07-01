"""Local workspace Skill discovery."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.protocols.skill import SkillError, SkillInfo
from agent.skills.markdown import parse_skill_markdown

_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_QUALIFIED_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+/[A-Za-z0-9_-]+$")
_IGNORED_DIRS = {"__pycache__"}


@dataclass(frozen=True)
class SkillRoot:
    """Runtime Skill package root for one configured source."""

    source: str
    root: Path


class SkillLoader:
    """Scan one or more local Skill roots for SKILL.md files."""

    def __init__(
        self,
        skill_roots: Path | str | SkillRoot | tuple[str, Path | str] | Iterable[
            Path | str | SkillRoot | tuple[str, Path | str]
        ],
        *,
        max_summary_chars: int = 800,
    ):
        """Resolve the Skill roots used for discovery."""

        self.skill_roots = _normalize_skill_roots(skill_roots)
        self.skills_dir = self.skill_roots[0] if self.skill_roots else Path()
        self.max_summary_chars = max_summary_chars
        self.load_errors: list[dict[str, Any]] = []
        self._by_qualified_name: dict[str, SkillInfo] = {}
        self._by_name: dict[str, list[SkillInfo]] = {}

    def list_skills(self) -> list[SkillInfo]:
        """Return valid skills sorted by qualified name."""

        self._scan()
        return [
            self._by_qualified_name[qualified_name]
            for qualified_name in sorted(self._by_qualified_name)
        ]

    def get_skill(self, name: str, source: str | None = None) -> SkillInfo:
        """Return one skill by name, or raise a structured SkillError."""

        requested = _validate_requested_name(name, source)
        self._scan()
        if "/" in requested:
            try:
                return self._by_qualified_name[requested]
            except KeyError as exc:
                raise SkillError(
                    f"Unknown Skill: {requested}",
                    "UNKNOWN_SKILL",
                    {"skill": requested},
                ) from exc

        matches = self._by_name.get(requested, [])
        if len(matches) > 1:
            candidates = sorted(skill.qualified_name for skill in matches)
            raise SkillError(
                "Skill name is ambiguous. Use a qualified skill name.",
                "AMBIGUOUS_SKILL",
                {"skill": requested, "candidates": candidates},
            )
        if matches:
            return matches[0]
        raise SkillError(
            f"Unknown Skill: {requested}",
            "UNKNOWN_SKILL",
            {"skill": requested},
        )

    def get_skill_body(self, name: str, source: str | None = None) -> str:
        """Read the full SKILL.md text for one skill."""

        info = self.get_skill(name, source=source)
        try:
            return info.skill_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillError(
                "Cannot read Skill file.",
                "SKILL_READ_ERROR",
                {"skill": info.qualified_name, "error_type": type(exc).__name__},
            ) from exc

    def _scan(self) -> None:
        """Scan direct child directories and collect valid SkillInfo objects."""

        self.load_errors = []
        self._by_qualified_name = {}
        self._by_name = {}
        if not self.skill_roots:
            return
        for skill_root in self.skill_roots:
            root_dir = skill_root.root
            if not root_dir.exists():
                continue
            if not root_dir.is_dir():
                self.load_errors.append(
                    {
                        "code": "INVALID_PARAM",
                        "path": str(root_dir),
                        "message": "skill root is not a directory",
                    }
                )
                continue

            for root in sorted(root_dir.iterdir(), key=lambda item: item.name.lower()):
                if not root.is_dir() or root.name.startswith(".") or root.name in _IGNORED_DIRS:
                    continue
                try:
                    info = self._load_skill_info(root, skill_root)
                except SkillError as exc:
                    self.load_errors.append(
                        {"path": str(root), "code": exc.code, "message": exc.output}
                    )
                    continue
                if info.qualified_name in self._by_qualified_name:
                    self.load_errors.append(
                        {
                            "path": str(root),
                            "code": "DUPLICATE_SKILL",
                            "message": f"Duplicate Skill name: {info.qualified_name}",
                        }
                    )
                    continue
                self._by_qualified_name[info.qualified_name] = info
                self._by_name.setdefault(info.name, []).append(info)

    def _load_skill_info(self, root: Path, skill_root: SkillRoot) -> SkillInfo:
        """Parse and validate one Skill directory."""

        skill_file = (root / "SKILL.md").resolve(strict=False)
        root_resolved = root.resolve(strict=False)
        if not _is_relative_to(skill_file, root_resolved) or not skill_file.exists():
            raise SkillError("Skill is missing SKILL.md.", "SKILL_READ_ERROR")
        if not skill_file.is_file():
            raise SkillError("Skill SKILL.md path is not a file.", "SKILL_READ_ERROR")

        try:
            parsed = parse_skill_markdown(
                skill_file.read_text(encoding="utf-8"),
                max_summary_chars=self.max_summary_chars,
            )
        except OSError as exc:
            raise SkillError(
                "Cannot read Skill file.",
                "SKILL_READ_ERROR",
                {"error_type": type(exc).__name__},
            ) from exc

        name = root.name
        if not _SKILL_NAME_RE.fullmatch(name):
            raise SkillError(
                f"Invalid Skill name: {name}",
                "INVALID_SKILL_NAME",
                {"skill": name},
            )
        frontmatter_name = _optional_text(parsed.frontmatter.get("name"))
        name_matches_directory = frontmatter_name == name
        description = _required_text(parsed.frontmatter.get("description"), "description")

        scripts_dir = (root_resolved / "scripts").resolve(strict=False)
        metadata = {
            "source": skill_root.source,
            "directory_name": root.name,
            "frontmatter_name": frontmatter_name,
            "name_matches_directory": name_matches_directory,
            "source_root": str(skill_root.root),
        }
        if not name_matches_directory:
            code = "SKILL_NAME_MISMATCH" if frontmatter_name else "MISSING_SKILL_FIELD"
            message = (
                "frontmatter name differs from directory name; directory name is used"
                if frontmatter_name
                else "frontmatter name is missing; directory name is used"
            )
            self.load_errors.append(
                {
                    "path": str(root),
                    "code": code,
                    "message": message,
                    "directory_name": root.name,
                    "frontmatter_name": frontmatter_name,
                    "canonical_name": name,
                }
            )
        return SkillInfo(
            source=skill_root.source,
            name=name,
            qualified_name=f"{skill_root.source}/{name}",
            description=description,
            root=root_resolved,
            skill_file=skill_file,
            scripts_dir=scripts_dir,
            summary=parsed.summary,
            metadata=metadata,
        )


def _validate_requested_name(name: str, source: str | None = None) -> str:
    """Validate a caller-supplied Skill name."""

    if not isinstance(name, str) or not name.strip():
        raise SkillError("Missing required parameter: name", "MISSING_PARAM", {"parameter": "name"})
    normalized_name = name.strip()
    if source is not None and str(source).strip():
        source_name = str(source).strip()
        if "/" in normalized_name:
            raise SkillError(
                "Skill source must not be provided with a qualified name.",
                "INVALID_SKILL_NAME",
                {"skill": normalized_name, "source": source_name},
            )
        requested = f"{source_name}/{normalized_name}"
    else:
        requested = normalized_name
    if "/" in requested:
        valid = _QUALIFIED_SKILL_NAME_RE.fullmatch(requested)
    else:
        valid = _SKILL_NAME_RE.fullmatch(requested)
    if not valid:
        raise SkillError(
            f"Invalid Skill name: {requested}",
            "INVALID_SKILL_NAME",
            {"skill": requested},
        )
    return requested


def _normalize_skill_roots(
    value: Path | str | SkillRoot | tuple[str, Path | str] | Iterable[
        Path | str | SkillRoot | tuple[str, Path | str]
    ],
) -> list[SkillRoot]:
    """Normalize root inputs into source-aware SkillRoot objects."""

    if isinstance(value, str | Path | SkillRoot) or _is_source_root_tuple(value):
        items = [value]
    else:
        items = list(value)
    return [_normalize_one_skill_root(item, index) for index, item in enumerate(items)]


def _normalize_one_skill_root(
    item: Path | str | SkillRoot | tuple[str, Path | str],
    index: int,
) -> SkillRoot:
    """Normalize one root item."""

    if isinstance(item, SkillRoot):
        return SkillRoot(source=item.source, root=item.root.expanduser().resolve())
    if _is_source_root_tuple(item):
        source, root = item
        return SkillRoot(source=_source_text(source), root=Path(root).expanduser().resolve())
    return SkillRoot(source=f"default-{index + 1}", root=Path(item).expanduser().resolve())


def _is_source_root_tuple(value: object) -> bool:
    """Return whether value looks like a (source, path) tuple."""

    return (
        isinstance(value, tuple)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], str | Path)
    )


def _source_text(value: object) -> str:
    """Return a valid source id."""

    text = str(value or "").strip()
    if not _SKILL_NAME_RE.fullmatch(text):
        raise SkillError(
            f"Invalid Skill source: {text}",
            "INVALID_SKILL_SOURCE",
            {"source": text},
        )
    return text


def _required_text(value: object, field_name: str) -> str:
    """Return a non-empty string frontmatter field."""

    if not isinstance(value, str) or not value.strip():
        raise SkillError(
            f"Skill frontmatter is missing required field: {field_name}",
            "MISSING_SKILL_FIELD",
            {"field": field_name},
        )
    return value.strip()


def _optional_text(value: object) -> str:
    """Return a stripped optional frontmatter text field."""

    if value is None:
        return ""
    return str(value).strip()


def _is_relative_to(path: Path, parent: Path) -> bool:
    """Return whether path resolves inside parent."""

    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
