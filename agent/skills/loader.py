"""Local workspace Skill discovery."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.protocols.auth import ActorContext
from agent.protocols.skill import ExecutableSkillInfo, SkillError, SkillInfo, SkillProvider
from agent.skills.markdown import parse_skill_markdown

_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_QUALIFIED_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+/[A-Za-z0-9_-]+$")
_IGNORED_DIRS = {"__pycache__"}


@dataclass(frozen=True)
class SkillRoot:
    """Runtime Skill package root for one configured source."""

    source: str
    root: Path
    allowed_roles: tuple[str, ...] = ()
    allowed_permissions: tuple[str, ...] = ()


class SkillLoader:
    """Scan one or more local Skill roots for SKILL.md files."""

    def __init__(
        self,
        skill_roots: Path | str | SkillRoot | tuple[str, Path | str] | Iterable[
            Path | str | SkillRoot | tuple[str, Path | str]
        ],
        *,
        max_summary_chars: int = 800,
        cache_path: Path | str | None = None,
    ):
        """Resolve the Skill roots used for discovery."""

        self.skill_roots = _normalize_skill_roots(skill_roots)
        self.skills_dir = self.skill_roots[0] if self.skill_roots else Path()
        self.max_summary_chars = max_summary_chars
        self.load_errors: list[dict[str, Any]] = []
        self._by_qualified_name: dict[str, SkillInfo] = {}
        self._by_name: dict[str, list[SkillInfo]] = {}
        self.cache_path = Path(cache_path).expanduser().resolve() if cache_path else None
        self._last_fingerprint = ""
        self._scan_lock = threading.RLock()

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

    def for_actor(self, actor: ActorContext) -> ActorFilteredSkillProvider:
        """Return a source-policy-filtered provider for one authenticated actor."""

        return ActorFilteredSkillProvider(self, actor)

    def list_skills_for_actor(self, actor: ActorContext) -> list[SkillInfo]:
        """Return only Skills whose source policy allows the actor."""

        return self.for_actor(actor).list_skills()

    def get_skill_for_actor(
        self,
        actor: ActorContext,
        name: str,
        source: str | None = None,
    ) -> SkillInfo:
        """Resolve one Skill and fail closed when its source is hidden."""

        return self.for_actor(actor).get_skill(name, source=source)

    def _scan(self) -> None:
        """Reuse a valid derived index or rebuild it from source truth."""

        with self._scan_lock:
            fingerprint = self._fingerprint()
            if fingerprint == self._last_fingerprint:
                return
            if self._load_cache(fingerprint):
                self._last_fingerprint = fingerprint
                return
            self._scan_uncached()
            self._last_fingerprint = fingerprint
            self._save_cache(fingerprint)

    def invalidate(self, source: str | None = None) -> None:
        """Atomically invalidate the derived index after a source mutation."""

        del source  # The first cache is intentionally one small all-source index.
        with self._scan_lock:
            self._last_fingerprint = ""
            self._by_qualified_name = {}
            self._by_name = {}
            self.load_errors = []
            if self.cache_path is not None:
                try:
                    self.cache_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def refresh_index(self, source: str | None = None) -> list[SkillInfo]:
        """Force a truth scan and return the refreshed catalog."""

        self.invalidate(source)
        return self.list_skills()

    def _scan_uncached(self) -> None:
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

    def _fingerprint(self) -> str:
        digest = hashlib.sha256()
        for skill_root in self.skill_roots:
            digest.update(skill_root.source.encode("utf-8"))
            digest.update(str(skill_root.root).encode("utf-8"))
            digest.update("\0".join(skill_root.allowed_roles).encode("utf-8"))
            digest.update("\0".join(skill_root.allowed_permissions).encode("utf-8"))
            root = skill_root.root
            if not root.is_dir():
                digest.update(b"missing-root")
                continue
            try:
                children = sorted(root.iterdir(), key=lambda item: item.name.lower())
            except OSError:
                digest.update(b"unreadable-root")
                continue
            for child in children:
                if child.name.startswith(".") or child.name in _IGNORED_DIRS:
                    continue
                digest.update(child.name.encode("utf-8", errors="replace"))
                skill_file = child / "SKILL.md"
                try:
                    stat = skill_file.stat()
                    digest.update(str(stat.st_mtime_ns).encode("ascii"))
                    digest.update(str(stat.st_size).encode("ascii"))
                    digest.update(hashlib.sha256(skill_file.read_bytes()).digest())
                except OSError:
                    digest.update(b"missing-skill-file")
        return digest.hexdigest()

    def _load_cache(self, fingerprint: str) -> bool:
        if self.cache_path is None or not self.cache_path.is_file():
            return False
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != 1 or payload.get("fingerprint") != fingerprint:
                return False
            raw_skills = payload.get("skills")
            raw_errors = payload.get("load_errors")
            if not isinstance(raw_skills, list) or not isinstance(raw_errors, list):
                raise ValueError("invalid Skill index cache")
            roots = {root.source: root for root in self.skill_roots}
            skills = [_skill_from_cache(item, roots) for item in raw_skills]
            self._by_qualified_name = {skill.qualified_name: skill for skill in skills}
            self._by_name = {}
            for skill in skills:
                self._by_name.setdefault(skill.name, []).append(skill)
            self.load_errors = [dict(item) for item in raw_errors if isinstance(item, dict)]
            return True
        except (OSError, ValueError, TypeError, json.JSONDecodeError, SkillError):
            try:
                self.cache_path.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def _save_cache(self, fingerprint: str) -> None:
        if self.cache_path is None:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": 1,
                "fingerprint": fingerprint,
                "skills": [_skill_to_cache(skill) for skill in self._by_qualified_name.values()],
                "load_errors": self.load_errors,
            }
            temporary = self.cache_path.with_name(self.cache_path.name + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(self.cache_path)
        except (OSError, TypeError, ValueError):
            try:
                temporary.unlink(missing_ok=True)
            except (OSError, UnboundLocalError):
                pass

    def _load_skill_info(self, root: Path, skill_root: SkillRoot) -> SkillInfo:
        """Parse and validate one Skill directory."""

        skill_file = (root / "SKILL.md").resolve(strict=False)
        root_resolved = root.resolve(strict=False)
        source_root_resolved = skill_root.root.resolve(strict=False)
        if not _is_relative_to(root_resolved, source_root_resolved):
            raise SkillError("Skill root is outside its source.", "SKILL_ROOT_OUTSIDE_SOURCE")
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
            "allowed_roles": list(skill_root.allowed_roles),
            "allowed_permissions": list(skill_root.allowed_permissions),
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
        executable = None
        runtime = parsed.frontmatter.get("runtime")
        if runtime is not None:
            try:
                executable = _parse_executable_runtime(runtime, root_resolved)
            except SkillError as exc:
                metadata["runtime_error"] = {"code": exc.code, "message": exc.output}
                self.load_errors.append(
                    {
                        "path": str(root),
                        "code": exc.code,
                        "message": exc.output,
                        "qualified_name": f"{skill_root.source}/{name}",
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
            executable=executable,
        )


class ActorFilteredSkillProvider:
    """Apply source visibility before catalog, body, or executable lookup."""

    def __init__(self, parent: SkillProvider, actor: ActorContext):
        self.parent = parent
        self.actor = actor

    def list_skills(self) -> list[SkillInfo]:
        return [skill for skill in self.parent.list_skills() if _actor_can_see(skill, self.actor)]

    def get_skill(self, name: str, source: str | None = None) -> SkillInfo:
        skill = self.parent.get_skill(name, source=source)
        if not _actor_can_see(skill, self.actor):
            raise SkillError("Unknown Skill.", "UNKNOWN_SKILL", {"skill": skill.qualified_name})
        return skill

    def get_skill_body(self, name: str, source: str | None = None) -> str:
        skill = self.get_skill(name, source=source)
        return self.parent.get_skill_body(skill.qualified_name)

    def invalidate(self, source: str | None = None) -> None:
        invalidate = getattr(self.parent, "invalidate", None)
        if callable(invalidate):
            invalidate(source)


def _skill_to_cache(skill: SkillInfo) -> dict[str, Any]:
    executable = None
    if skill.executable is not None:
        executable = {
            "runtime_type": skill.executable.runtime_type,
            "entrypoint": skill.executable.entrypoint.relative_to(skill.root).as_posix(),
            "protocol": skill.executable.protocol,
            "timeout_seconds": skill.executable.timeout_seconds,
            "params_schema": skill.executable.params_schema,
        }
    return {
        "source": skill.source,
        "name": skill.name,
        "description": skill.description,
        "summary": skill.summary,
        "metadata": skill.metadata,
        "executable": executable,
    }


def _skill_from_cache(value: object, roots: dict[str, SkillRoot]) -> SkillInfo:
    if not isinstance(value, dict):
        raise SkillError("Skill index cache is invalid.", "SKILL_CACHE_INVALID")
    source = str(value.get("source") or "")
    name = str(value.get("name") or "")
    skill_root = roots.get(source)
    if skill_root is None or not _SKILL_NAME_RE.fullmatch(name):
        raise SkillError("Skill index cache is invalid.", "SKILL_CACHE_INVALID")
    root = (skill_root.root / name).resolve(strict=False)
    source_root = skill_root.root.resolve(strict=False)
    skill_file = (root / "SKILL.md").resolve(strict=False)
    if not _is_relative_to(root, source_root) or not skill_file.is_file():
        raise SkillError("Skill index cache is invalid.", "SKILL_CACHE_INVALID")
    executable = None
    raw_executable = value.get("executable")
    if raw_executable is not None:
        if not isinstance(raw_executable, dict):
            raise SkillError("Skill index cache is invalid.", "SKILL_CACHE_INVALID")
        entrypoint = (root / str(raw_executable.get("entrypoint") or "")).resolve(strict=False)
        if not _is_relative_to(entrypoint, root) or not entrypoint.is_file():
            raise SkillError("Skill index cache is invalid.", "SKILL_CACHE_INVALID")
        executable = ExecutableSkillInfo(
            runtime_type="python",
            entrypoint=entrypoint,
            protocol="ndjson-v1",
            timeout_seconds=int(raw_executable.get("timeout_seconds") or 60),
            params_schema=(
                dict(raw_executable["params_schema"])
                if isinstance(raw_executable.get("params_schema"), dict)
                else None
            ),
        )
    metadata = value.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata = {
        **metadata,
        "allowed_roles": list(skill_root.allowed_roles),
        "allowed_permissions": list(skill_root.allowed_permissions),
    }
    return SkillInfo(
        source=source,
        name=name,
        qualified_name=f"{source}/{name}",
        description=str(value.get("description") or ""),
        root=root,
        skill_file=skill_file,
        scripts_dir=(root / "scripts").resolve(strict=False),
        summary=str(value.get("summary") or ""),
        metadata=metadata,
        executable=executable,
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
        return SkillRoot(
            source=item.source,
            root=item.root.expanduser().resolve(),
            allowed_roles=tuple(item.allowed_roles),
            allowed_permissions=tuple(item.allowed_permissions),
        )
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


def _parse_executable_runtime(value: object, skill_root: Path) -> ExecutableSkillInfo:
    """Validate the first explicit executable Skill runtime declaration."""

    if not isinstance(value, dict):
        raise SkillError("Skill runtime must be a mapping.", "INVALID_SKILL_RUNTIME")
    supported = {"type", "entrypoint", "protocol", "timeout_seconds", "params_schema"}
    unknown = sorted(set(value) - supported)
    if unknown:
        raise SkillError(
            "Skill runtime contains unsupported fields.",
            "INVALID_SKILL_RUNTIME",
            {"fields": unknown},
        )
    runtime_type = str(value.get("type") or "").strip()
    if runtime_type != "python":
        raise SkillError("Skill runtime type must be python.", "INVALID_SKILL_RUNTIME")
    protocol = str(value.get("protocol") or "").strip()
    if protocol != "ndjson-v1":
        raise SkillError("Skill runtime protocol must be ndjson-v1.", "INVALID_SKILL_RUNTIME")
    raw_entrypoint = value.get("entrypoint")
    if not isinstance(raw_entrypoint, str) or not raw_entrypoint.strip():
        raise SkillError("Skill runtime entrypoint is required.", "INVALID_SKILL_RUNTIME")
    entrypoint_value = Path(raw_entrypoint.strip())
    if entrypoint_value.is_absolute():
        raise SkillError("Skill runtime entrypoint must be relative.", "INVALID_SKILL_ENTRYPOINT")
    entrypoint = (skill_root / entrypoint_value).resolve(strict=False)
    if not _is_relative_to(entrypoint, skill_root) or not entrypoint.is_file():
        raise SkillError("Skill runtime entrypoint is invalid.", "INVALID_SKILL_ENTRYPOINT")
    timeout = value.get("timeout_seconds", 60)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 900:
        raise SkillError(
            "Skill runtime timeout_seconds must be between 1 and 900.",
            "INVALID_SKILL_RUNTIME",
        )
    params_schema = value.get("params_schema")
    if params_schema is not None and not isinstance(params_schema, dict):
        raise SkillError("Skill runtime params_schema must be a mapping.", "INVALID_SKILL_RUNTIME")
    return ExecutableSkillInfo(
        runtime_type="python",
        entrypoint=entrypoint,
        protocol="ndjson-v1",
        timeout_seconds=timeout,
        params_schema=dict(params_schema) if params_schema is not None else None,
    )


def _actor_can_see(skill: SkillInfo, actor: ActorContext) -> bool:
    roles = tuple(str(item) for item in skill.metadata.get("allowed_roles", ()))
    permissions = tuple(str(item) for item in skill.metadata.get("allowed_permissions", ()))
    if roles and not set(roles).intersection(actor.role_keys):
        return False
    return not permissions or all(actor.has_permission(permission) for permission in permissions)
