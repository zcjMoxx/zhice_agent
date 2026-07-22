"""Fail-closed loading for workspace Subagent capability Profiles."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from agent.protocols.subagent import SubagentProfile

DEFAULT_MAX_PARALLEL = 3
HARD_MAX_PARALLEL = 8
DEFAULT_MAX_TASKS_PER_CALL = 4
HARD_MAX_TASKS_PER_CALL = 8
HARD_MAX_DEPTH = 1
DEFAULT_MAX_SUBAGENTS_PER_PARENT_TURN = 6
HARD_MAX_SUBAGENTS_PER_PARENT_TURN = 12
DEFAULT_MAX_BATCHES_PER_PARENT_TURN = 1
HARD_MAX_BATCHES_PER_PARENT_TURN = 2
DEFAULT_MAX_BATCH_RESULT_CHARS = 32000
HARD_MAX_BATCH_RESULT_CHARS = 64000
HARD_MAX_TOOL_ITERATIONS = 50
HARD_MAX_TIMEOUT_SECONDS = 1800
DEFAULT_MAX_RESULT_CHARS = 12000
HARD_MAX_RESULT_CHARS = 24000

_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_EXACT_TOOL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MCP_PATTERN_RE = re.compile(r"^mcp__[A-Za-z0-9_-]+__\*$")
_SKILL_RE = re.compile(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)?$")
_WORKSPACE_MODES = {"shared_readonly", "worktree", "shared_exclusive"}
_MODEL_ROLES = {"inherit", "fast", "reasoning"}


class SubagentConfigurationError(ValueError):
    """Raised when an explicit Subagent config cannot be trusted."""


@dataclass(frozen=True)
class SubagentConfig:
    """Validated workspace-level Subagent limits and Profiles."""

    enabled: bool = False
    max_parallel: int = DEFAULT_MAX_PARALLEL
    max_tasks_per_call: int = DEFAULT_MAX_TASKS_PER_CALL
    max_depth: int = HARD_MAX_DEPTH
    max_subagents_per_parent_turn: int = DEFAULT_MAX_SUBAGENTS_PER_PARENT_TURN
    max_batches_per_parent_turn: int = DEFAULT_MAX_BATCHES_PER_PARENT_TURN
    max_batch_result_chars: int = DEFAULT_MAX_BATCH_RESULT_CHARS
    profiles: Mapping[str, SubagentProfile] = MappingProxyType({})

    def list_profiles(self) -> tuple[SubagentProfile, ...]:
        """Return Profiles in stable YAML order."""

        return tuple(self.profiles.values())

    def get_profile(self, name: str) -> SubagentProfile | None:
        """Return one exact Profile or None; never select a fallback."""

        return self.profiles.get(name)


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_subagent_config(config_path: Path) -> SubagentConfig:
    """Load subagents.yml; a missing or empty file disables Subagents."""

    path = Path(config_path).expanduser().resolve()
    if path.is_dir() or (not path.exists() and path.suffix == ""):
        path = path / "subagents.yml"
    if not path.exists():
        return SubagentConfig()
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise SubagentConfigurationError(f"Cannot read Subagent config: {path.name}") from exc
    if raw is None:
        return SubagentConfig()
    if not isinstance(raw, dict):
        raise SubagentConfigurationError("Subagent config root must be an object")
    allowed = {
        "enabled",
        "max_parallel",
        "max_tasks_per_call",
        "max_depth",
        "max_subagents_per_parent_turn",
        "max_batches_per_parent_turn",
        "max_batch_result_chars",
        "profiles",
    }
    _reject_unknown(raw, allowed, "Subagent config")
    enabled = _boolean(raw.get("enabled", False), "enabled")
    profiles_raw = raw.get("profiles", {})
    if not isinstance(profiles_raw, dict):
        raise SubagentConfigurationError("profiles must be an object")
    profiles = {
        name: _parse_profile(name, value) for name, value in profiles_raw.items()
    }
    if enabled and not profiles:
        raise SubagentConfigurationError("enabled Subagent config requires at least one Profile")
    return SubagentConfig(
        enabled=enabled,
        max_parallel=_bounded_int(
            raw.get("max_parallel", DEFAULT_MAX_PARALLEL),
            "max_parallel",
            1,
            HARD_MAX_PARALLEL,
        ),
        max_tasks_per_call=_bounded_int(
            raw.get("max_tasks_per_call", DEFAULT_MAX_TASKS_PER_CALL),
            "max_tasks_per_call",
            1,
            HARD_MAX_TASKS_PER_CALL,
        ),
        max_depth=_bounded_int(raw.get("max_depth", HARD_MAX_DEPTH), "max_depth", 1, 1),
        max_subagents_per_parent_turn=_bounded_int(
            raw.get(
                "max_subagents_per_parent_turn",
                DEFAULT_MAX_SUBAGENTS_PER_PARENT_TURN,
            ),
            "max_subagents_per_parent_turn",
            1,
            HARD_MAX_SUBAGENTS_PER_PARENT_TURN,
        ),
        max_batches_per_parent_turn=_bounded_int(
            raw.get("max_batches_per_parent_turn", DEFAULT_MAX_BATCHES_PER_PARENT_TURN),
            "max_batches_per_parent_turn",
            1,
            HARD_MAX_BATCHES_PER_PARENT_TURN,
        ),
        max_batch_result_chars=_bounded_int(
            raw.get("max_batch_result_chars", DEFAULT_MAX_BATCH_RESULT_CHARS),
            "max_batch_result_chars",
            1000,
            HARD_MAX_BATCH_RESULT_CHARS,
        ),
        profiles=MappingProxyType(profiles),
    )


def _parse_profile(name: Any, raw: Any) -> SubagentProfile:
    if not isinstance(name, str) or not _PROFILE_NAME_RE.fullmatch(name):
        raise SubagentConfigurationError(f"Invalid Subagent Profile name: {name!r}")
    if not isinstance(raw, dict):
        raise SubagentConfigurationError(f"Subagent Profile {name!r} must be an object")
    allowed = {
        "description",
        "tools",
        "denied_tools",
        "allowed_skills",
        "preload_skills",
        "workspace_mode",
        "max_tool_iterations",
        "timeout_seconds",
        "max_result_chars",
        "allow_model_invocation",
        "model_role",
    }
    _reject_unknown(raw, allowed, f"Subagent Profile {name!r}")
    description = _required_text(raw.get("description"), f"profiles.{name}.description", 500)
    tools = _tool_patterns(raw.get("tools"), f"profiles.{name}.tools", required=True)
    denied_tools = _tool_patterns(
        raw.get("denied_tools", ["delegate_tasks"]),
        f"profiles.{name}.denied_tools",
        required=False,
    )
    if "delegate_tasks" not in denied_tools:
        denied_tools = (*denied_tools, "delegate_tasks")
    workspace_mode = _choice(
        raw.get("workspace_mode", "shared_readonly"),
        f"profiles.{name}.workspace_mode",
        _WORKSPACE_MODES,
    )
    if workspace_mode == "shared_readonly" and _patterns_include("exec", tools, denied_tools):
        raise SubagentConfigurationError(
            f"Subagent Profile {name!r} cannot allow exec in shared_readonly mode"
        )
    allowed_skills = _skill_names(
        raw.get("allowed_skills", []), f"profiles.{name}.allowed_skills"
    )
    preload_skills = _skill_names(
        raw.get("preload_skills", []), f"profiles.{name}.preload_skills"
    )
    if any(skill not in allowed_skills for skill in preload_skills):
        raise SubagentConfigurationError(
            f"Subagent Profile {name!r} preload_skills must be included in allowed_skills"
        )
    return SubagentProfile(
        name=name,
        description=description,
        tools=tools,
        denied_tools=denied_tools,
        allowed_skills=allowed_skills,
        preload_skills=preload_skills,
        workspace_mode=workspace_mode,  # type: ignore[arg-type]
        max_tool_iterations=_bounded_int(
            raw.get("max_tool_iterations", 10),
            f"profiles.{name}.max_tool_iterations",
            0,
            HARD_MAX_TOOL_ITERATIONS,
        ),
        timeout_seconds=_bounded_int(
            raw.get("timeout_seconds", 180),
            f"profiles.{name}.timeout_seconds",
            1,
            HARD_MAX_TIMEOUT_SECONDS,
        ),
        max_result_chars=_bounded_int(
            raw.get("max_result_chars", DEFAULT_MAX_RESULT_CHARS),
            f"profiles.{name}.max_result_chars",
            1,
            HARD_MAX_RESULT_CHARS,
        ),
        allow_model_invocation=_boolean(
            raw.get("allow_model_invocation", True),
            f"profiles.{name}.allow_model_invocation",
        ),
        model_role=_choice(
            raw.get("model_role", "inherit"),
            f"profiles.{name}.model_role",
            _MODEL_ROLES,
        ),  # type: ignore[arg-type]
    )


def _tool_patterns(value: Any, field: str, *, required: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or (required and not value):
        requirement = "a non-empty list" if required else "a list"
        raise SubagentConfigurationError(f"{field} must be {requirement}")
    patterns: list[str] = []
    for item in value:
        text = _required_text(item, field, 128)
        if not (_EXACT_TOOL_RE.fullmatch(text) or _MCP_PATTERN_RE.fullmatch(text)):
            raise SubagentConfigurationError(f"Invalid Subagent tool matcher: {text}")
        if text not in patterns:
            patterns.append(text)
    return tuple(patterns)


def _skill_names(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise SubagentConfigurationError(f"{field} must be a list")
    names: list[str] = []
    for item in value:
        name = _required_text(item, field, 129)
        if not _SKILL_RE.fullmatch(name):
            raise SubagentConfigurationError(f"Invalid Subagent Skill name: {name}")
        if name not in names:
            names.append(name)
    return tuple(names)


def _patterns_include(name: str, allowed: tuple[str, ...], denied: tuple[str, ...]) -> bool:
    return any(_matches(name, pattern) for pattern in allowed) and not any(
        _matches(name, pattern) for pattern in denied
    )


def _matches(name: str, pattern: str) -> bool:
    return name == pattern or (pattern.endswith("__*") and name.startswith(pattern[:-1]))


def _reject_unknown(raw: dict, allowed: set[str], field: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise SubagentConfigurationError(f"{field} has unknown fields: {unknown}")


def _required_text(value: Any, field: str, max_chars: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SubagentConfigurationError(f"{field} must be a non-empty string")
    text = value.strip()
    if len(text) > max_chars:
        raise SubagentConfigurationError(f"{field} exceeds max length {max_chars}")
    return text


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise SubagentConfigurationError(f"{field} must be a boolean")
    return value


def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SubagentConfigurationError(f"{field} must be an integer")
    if value < minimum or value > maximum:
        raise SubagentConfigurationError(f"{field} is outside allowed range")
    return value


def _choice(value: Any, field: str, choices: set[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise SubagentConfigurationError(f"{field} must be one of {sorted(choices)}")
    return value
