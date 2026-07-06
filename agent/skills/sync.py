"""Synchronize configured Skill sources into workspace extends repositories."""

from __future__ import annotations

import filecmp
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agent.skills.loader import SkillRoot

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_VALID_SOURCE_NAMES = re.compile(r"^[A-Za-z0-9_-]+$")
_VALID_STARTUP_MODES = {"never", "always"}
_VALID_LOG_MODES = {"changes_only", "always"}
_IGNORED_COPY_NAMES = {
    "__pycache__",
    ".git",
    ".gitignore",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".venv",
    "node_modules",
    "tests",
    "test",
}


class SkillSyncError(RuntimeError):
    """Raised when Skill source sync configuration or execution fails."""


@dataclass(frozen=True)
class SkillSource:
    """One configured source repository or local directory."""

    name: str
    sync: bool
    local_dir: str = ""
    git_url: str = ""
    target: str = "master"


@dataclass(frozen=True)
class SkillSyncSettings:
    """Global sync behavior from workspace config/skill_sources.yml."""

    extends_dir: Path
    on_startup: str = "never"
    background: bool = False
    interval_seconds: int = 0
    log: str = "changes_only"


@dataclass
class SkillSourceResult:
    """Result for one configured source."""

    name: str
    status: str
    skills: int = 0
    new: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    message: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible source summary."""

        return {
            "name": self.name,
            "status": self.status,
            "skills": self.skills,
            "new": self.new,
            "changed": self.changed,
            "removed": self.removed,
            "unchanged": self.unchanged,
            "message": self.message,
            "error": self.error,
        }

    def has_changes(self) -> bool:
        """Return whether this source changed local runtime files."""

        return self.status == "synced" or bool(self.new or self.changed or self.removed)


@dataclass
class SkillSyncResult:
    """Summary returned by one sync run."""

    sources: list[SkillSourceResult] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible summary."""

        failed = [source for source in self.sources if source.status == "failed"]
        if failed and len(failed) == len(self.sources):
            status = "error"
        elif failed:
            status = "partial_error"
        else:
            status = "success"
        return {
            "status": status,
            "sources": [source.as_dict() for source in self.sources],
            "counts": {
                "sources": len(self.sources),
                "synced": sum(1 for source in self.sources if source.status == "synced"),
                "up_to_date": sum(1 for source in self.sources if source.status == "up_to_date"),
                "skipped": sum(1 for source in self.sources if source.status == "skipped"),
                "failed": len(failed),
            },
        }

    def has_changes(self) -> bool:
        """Return whether the sync changed files or reported failures."""

        return any(source.has_changes() or source.status == "failed" for source in self.sources)

    @property
    def errors(self) -> list[dict[str, str]]:
        """Return failed source messages for callers that need compact errors."""

        return [
            {"source": source.name, "message": source.error or source.message}
            for source in self.sources
            if source.status == "failed"
        ]


class SkillSourceSync:
    """Load workspace Skill source config and sync source repos under extends."""

    def __init__(
        self,
        *,
        workspace: Path | str,
        config_dir: Path | str,
        extends_dir: Path | str,
        skill_repo: Path | str | None = None,
    ):
        """Store runtime paths used for Skill source synchronization."""

        self.workspace = Path(workspace).expanduser().resolve()
        self.config_dir = Path(config_dir).expanduser().resolve()
        self.extends_dir = Path(extends_dir).expanduser().resolve()
        self.skill_repo = Path(skill_repo).expanduser().resolve() if skill_repo else _default_skill_repo()
        self.config_path = self.config_dir / "skill_sources.yml"

    def sync_on_startup(self) -> SkillSyncResult | None:
        """Run startup sync according to config, returning None when disabled."""

        if not self.has_config():
            return None
        settings, sources = self.load()
        if settings.on_startup == "never":
            return None
        return self.sync()

    def sync(
        self,
        *,
        source_names: list[str] | None = None,
    ) -> SkillSyncResult:
        """Synchronize configured sources into extends_dir/{source}."""

        if not self.has_config():
            raise SkillSyncError(
                f"Skill source config is missing: {self.config_path}. Run zcagent init to create it."
            )
        settings, sources = self.load()
        selected = set(source_names or [])
        seen_sources: set[str] = set()
        result = SkillSyncResult()
        settings.extends_dir.mkdir(parents=True, exist_ok=True)

        for source in sources:
            if selected and source.name not in selected:
                continue
            seen_sources.add(source.name)
            if not source.sync:
                result.sources.append(
                    SkillSourceResult(
                        name=source.name,
                        status="skipped",
                        message="sync=false",
                    )
                )
                continue
            try:
                result.sources.append(self._sync_source(source, settings))
            except SkillSyncError as exc:
                result.sources.append(
                    SkillSourceResult(
                        name=source.name,
                        status="failed",
                        message=str(exc),
                        error=str(exc),
                    )
                )
        for source_name in sorted(selected - seen_sources):
            result.sources.append(
                SkillSourceResult(
                    name=source_name,
                    status="failed",
                    message="Skill source is not configured",
                    error="Skill source is not configured",
                )
            )
        return result

    def load(self) -> tuple[SkillSyncSettings, list[SkillSource]]:
        """Load and validate workspace config/skill_sources.yml."""

        if not self.config_path.exists():
            return SkillSyncSettings(extends_dir=self.extends_dir), []
        try:
            raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise SkillSyncError(f"Invalid skill_sources.yml: {exc}") from exc
        if not isinstance(raw, dict):
            raise SkillSyncError("skill_sources.yml must be a mapping")

        expanded = _expand_placeholders(raw, self._placeholder_vars())
        settings = _parse_settings(expanded, self.extends_dir)
        _validate_runtime_path(settings.extends_dir, self.workspace, "extends_dir")
        sources = _parse_sources(expanded.get("sources"))
        _validate_source_dirs(sources)
        return settings, sources

    def has_config(self) -> bool:
        """Return whether workspace config/skill_sources.yml exists."""

        return self.config_path.exists()

    def skill_roots(self) -> list[SkillRoot]:
        """Return configured runtime Skill package roots under extends_dir."""

        if not self.has_config():
            return []
        settings, sources = self.load()
        roots: list[SkillRoot] = []
        for source in sources:
            if not source.sync:
                continue
            roots.append(SkillRoot(source=source.name, root=_source_skill_root(settings.extends_dir, source)))
        return roots

    def has_installed_skills(self) -> bool:
        """Return whether any configured runtime source already has Skills."""

        return any(
            root.root.exists() and any(root.root.glob("*/SKILL.md"))
            for root in self.skill_roots()
        )

    def _sync_source(
        self,
        source: SkillSource,
        settings: SkillSyncSettings,
    ) -> SkillSourceResult:
        """Synchronize one source and return its source-level result."""

        source_repo_root = self._source_repo_root(source)
        repo_dir = _source_repo_dir(settings.extends_dir, source)
        if source_repo_root is not None:
            return _mirror_repo_root(source, source_repo_root, repo_dir)
        if source.git_url:
            changed = self._ensure_git_source(source, repo_dir)
            source_skill_root = (repo_dir / "skills").resolve()
            if not source_skill_root.is_dir():
                raise SkillSyncError(f"git Skill source root does not exist: {source_skill_root}")
            return _scan_runtime_skill_root(source, source_skill_root, changed=changed)
        raise SkillSyncError("source requires local_dir or git_url")

    def _source_repo_root(self, source: SkillSource) -> Path | None:
        """Return configured local repo root when it exists."""

        if not source.local_dir:
            return None
        root = Path(source.local_dir).expanduser().resolve()
        if not root.exists():
            return None
        if not root.is_dir():
            raise SkillSyncError(f"local Skill source is not a directory: {root}")
        return root

    def _ensure_git_source(self, source: SkillSource, repo_dir: Path) -> bool:
        """Clone or refresh one git source directly under extends_dir."""

        if not source.git_url:
            raise SkillSyncError("git source requires git_url")
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        if not repo_dir.exists():
            _run_git(["clone", source.git_url, str(repo_dir)], cwd=repo_dir.parent)
            changed = True
        else:
            changed = False
        if not (repo_dir / ".git").is_dir():
            raise SkillSyncError(f"source runtime directory is not a git repository: {repo_dir}")
        before = _git_head(repo_dir)
        _checkout_git_source(repo_dir, source)
        after = _git_head(repo_dir)
        changed = changed or before != after
        return changed

    def _placeholder_vars(self) -> dict[str, str]:
        """Return built-in placeholders plus process environment."""

        values = {key: value for key, value in os.environ.items()}
        values["ZHICE_AGENT_WORKSPACE"] = str(self.workspace)
        values["ZHICE_AGENT_SKILL_REPO"] = str(self.skill_repo)
        return values


def _parse_settings(raw: dict[str, Any], default_extends_dir: Path) -> SkillSyncSettings:
    """Parse global sync settings."""

    sync = raw.get("sync") if isinstance(raw.get("sync"), dict) else {}
    on_startup = str(sync.get("on_startup", "never")).strip() or "never"
    if on_startup not in _VALID_STARTUP_MODES:
        raise SkillSyncError(f"invalid sync.on_startup: {on_startup}")
    raw_background = sync.get("background")
    if raw_background is None:
        background = {}
    elif isinstance(raw_background, dict):
        background = raw_background
    else:
        raise SkillSyncError("sync.background must be a mapping")
    background_enabled = _coerce_bool(
        background.get("enabled", False),
        "sync.background.enabled",
    )
    interval_seconds = _coerce_int(
        background.get("interval_seconds", 0),
        "sync.background.interval_seconds",
    )
    if interval_seconds < 0:
        raise SkillSyncError("sync.background.interval_seconds must be >= 0")
    log = str(sync.get("log", "changes_only")).strip() or "changes_only"
    if log not in _VALID_LOG_MODES:
        raise SkillSyncError(f"invalid sync.log: {log}")
    return SkillSyncSettings(
        extends_dir=Path(str(raw.get("extends_dir") or default_extends_dir))
        .expanduser()
        .resolve(),
        on_startup=on_startup,
        background=background_enabled,
        interval_seconds=interval_seconds,
        log=log,
    )


def _parse_sources(raw_sources: object) -> list[SkillSource]:
    """Parse source entries from config."""

    if raw_sources is None:
        return []
    if not isinstance(raw_sources, list):
        raise SkillSyncError("sources must be a list")
    sources: list[SkillSource] = []
    for index, item in enumerate(raw_sources):
        if not isinstance(item, dict):
            raise SkillSyncError(f"sources[{index}] must be a mapping")
        name = _required_text(item.get("name"), f"sources[{index}].name")
        if not _VALID_SOURCE_NAMES.fullmatch(name):
            raise SkillSyncError(f"sources[{index}].name is invalid: {name}")
        local_dir = str(item.get("local_dir") or "")
        git_url = str(item.get("git_url") or "")
        if not local_dir and not git_url:
            raise SkillSyncError(f"sources[{index}] requires local_dir or git_url")
        sources.append(
            SkillSource(
                name=name,
                sync=_coerce_bool(item.get("sync", True), f"sources[{index}].sync"),
                local_dir=local_dir,
                git_url=git_url,
                target=str(item.get("target") or "master"),
            )
        )
    return sources


def _validate_source_dirs(sources: list[SkillSource]) -> None:
    """Validate unique source names."""

    names: set[str] = set()
    for source in sources:
        if source.name in names:
            raise SkillSyncError(f"duplicate Skill source name: {source.name}")
        names.add(source.name)


def _mirror_repo_root(
    source: SkillSource,
    source_repo_root: Path,
    target_repo_root: Path,
) -> SkillSourceResult:
    """Mirror a complete local source repository into extends/{source}."""

    source_skill_root = (source_repo_root / "skills").resolve()
    skill_dirs = _iter_skill_dirs(source_skill_root)
    if not skill_dirs:
        return SkillSourceResult(
            name=source.name,
            status="skipped",
            message=f"no Skill packages found in {source_skill_root}",
        )
    if source_repo_root.resolve() == target_repo_root.resolve():
        unchanged = [skill_dir.name for skill_dir in skill_dirs]
        return SkillSourceResult(
            name=source.name,
            status="up_to_date",
            skills=len(unchanged),
            unchanged=unchanged,
        )

    target_skill_root = (target_repo_root / "skills").resolve()
    source_names = {skill_dir.name for skill_dir in skill_dirs}
    new: list[str] = []
    changed: list[str] = []
    removed: list[str] = []
    unchanged: list[str] = []

    for skill_dir in skill_dirs:
        target = target_skill_root / skill_dir.name
        if not target.exists():
            new.append(skill_dir.name)
            continue
        if _dirs_equal(skill_dir, target):
            unchanged.append(skill_dir.name)
            continue
        changed.append(skill_dir.name)

    if target_skill_root.exists():
        for target in _iter_skill_dirs(target_skill_root):
            if target.name not in source_names:
                removed.append(target.name)

    repo_changed = not target_repo_root.exists() or not _dirs_equal(source_repo_root, target_repo_root)
    if repo_changed:
        if target_repo_root.exists():
            shutil.rmtree(target_repo_root)
        _copy_repo_dir(source_repo_root, target_repo_root)

    status = "synced" if repo_changed else "up_to_date"
    return SkillSourceResult(
        name=source.name,
        status=status,
        skills=len(source_names),
        new=new,
        changed=changed,
        removed=removed,
        unchanged=unchanged,
    )


def _scan_runtime_skill_root(
    source: SkillSource,
    source_skill_root: Path,
    *,
    changed: bool,
) -> SkillSourceResult:
    """Return status for a git source that is already materialized in extends."""

    skill_dirs = _iter_skill_dirs(source_skill_root)
    if not skill_dirs:
        return SkillSourceResult(
            name=source.name,
            status="skipped",
            message=f"no Skill packages found in {source_skill_root}",
        )
    return SkillSourceResult(
        name=source.name,
        status="synced" if changed else "up_to_date",
        skills=len(skill_dirs),
        unchanged=[skill_dir.name for skill_dir in skill_dirs],
    )


def _iter_skill_dirs(source_dir: Path) -> list[Path]:
    """Return direct child directories that look like Skill packages."""

    if not source_dir.is_dir():
        return []
    return [
        child
        for child in sorted(source_dir.iterdir(), key=lambda path: path.name.lower())
        if child.is_dir() and not child.name.startswith(".") and (child / "SKILL.md").is_file()
    ]


def _copy_repo_dir(source: Path, target: Path) -> None:
    """Copy one complete Skill source repository."""

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=_copy_ignore)


def _copy_ignore(_directory: str, names: list[str]) -> set[str]:
    """Ignore Python and Git cache directories when syncing Skills."""

    return {name for name in names if _should_ignore_copy_name(name)}


def _dirs_equal(left: Path, right: Path) -> bool:
    """Return whether two directories have the same file tree and file bytes."""

    comparison = filecmp.dircmp(left, right, ignore=list(_IGNORED_COPY_NAMES))
    if (
        _visible_names(comparison.left_only)
        or _visible_names(comparison.right_only)
        or _visible_names(comparison.funny_files)
    ):
        return False
    for file_name in comparison.common_files:
        if _should_ignore_copy_name(file_name):
            continue
        if not filecmp.cmp(left / file_name, right / file_name, shallow=False):
            return False
    return all(
        _dirs_equal(left / name, right / name)
        for name in comparison.common_dirs
        if not _should_ignore_copy_name(name)
    )


def _source_repo_dir(extends_dir: Path, source: SkillSource) -> Path:
    """Return the runtime repository directory for one source."""

    return (extends_dir / source.name).resolve()


def _source_skill_root(extends_dir: Path, source: SkillSource) -> Path:
    """Return the runtime directory that directly contains Skill packages."""

    return (_source_repo_dir(extends_dir, source) / "skills").resolve()


def _checkout_git_source(repo_dir: Path, source: SkillSource) -> None:
    """Fetch and checkout the configured git target."""

    _run_git(["fetch", "origin"], cwd=repo_dir)
    target = source.target or "master"
    _run_git(["checkout", target], cwd=repo_dir, check=False)
    _run_git(["reset", "--hard", f"origin/{target}"], cwd=repo_dir)


def _git_head(repo_dir: Path) -> str:
    """Return current git HEAD or empty string when unavailable."""

    return _run_git(["rev-parse", "HEAD"], cwd=repo_dir, check=False).strip()


def _run_git(args: list[str], *, cwd: Path, check: bool = True) -> str:
    """Run git with shell disabled and bounded output."""

    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=60,
        shell=False,
        encoding="utf-8",
        errors="replace",
    )
    if check and completed.returncode != 0:
        error = (completed.stderr or completed.stdout or "").strip()
        raise SkillSyncError(f"git {' '.join(args)} failed: {error[:500]}")
    return completed.stdout


def _expand_placeholders(value: Any, variables: dict[str, str]) -> Any:
    """Expand ${VAR} placeholders in config strings."""

    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda match: variables.get(match.group(1), ""), value)
    if isinstance(value, list):
        return [_expand_placeholders(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _expand_placeholders(item, variables) for key, item in value.items()}
    return value


def _required_text(value: object, field_name: str) -> str:
    """Return a non-empty config string."""

    text = str(value or "").strip()
    if not text:
        raise SkillSyncError(f"missing required field: {field_name}")
    return text


def _coerce_int(value: object, field_name: str) -> int:
    """Parse an integer config field."""

    try:
        return int(value or 0)
    except (TypeError, ValueError) as exc:
        raise SkillSyncError(f"{field_name} must be an integer") from exc


def _coerce_bool(value: object, field_name: str) -> bool:
    """Parse a boolean config field."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise SkillSyncError(f"{field_name} must be a boolean")


def _validate_runtime_path(path: Path, workspace: Path, field_name: str) -> None:
    """Require generated runtime directories to stay inside the workspace."""

    try:
        path.relative_to(workspace)
    except ValueError as exc:
        raise SkillSyncError(f"{field_name} must be inside ZHICE_AGENT_WORKSPACE") from exc


def _visible_names(names: list[str]) -> list[str]:
    """Filter copy-ignored names from a dircmp name list."""

    return [name for name in names if not _should_ignore_copy_name(name)]


def _should_ignore_copy_name(name: str) -> bool:
    """Return whether sync should ignore this file or directory name."""

    return name in _IGNORED_COPY_NAMES or name.endswith(".pyc")


def _default_skill_repo() -> Path:
    """Return the built-in official Skill source directory."""

    return Path(__file__).resolve().parents[2] / "skill_repo"
