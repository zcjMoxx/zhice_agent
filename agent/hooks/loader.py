"""Load explicit workspace Hook registrations without directory scanning."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from agent.hooks.config import (
    DEFAULT_HOOK_OUTPUT_CHARS,
    DEFAULT_HOOK_TIMEOUT_SECONDS,
    MAX_HOOK_OUTPUT_CHARS,
    MAX_HOOK_TIMEOUT_SECONDS,
    MIN_HOOK_OUTPUT_CHARS,
    HookConfigurationError,
    HookRegistry,
    HookSpec,
)

_HOOK_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ROLE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_PERMISSION_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_STAGES = {"pre_tooluse", "post_tooluse"}


def load_hook_registry(config_path: Path, *, workspace: Path) -> HookRegistry:
    """Load one hooks.yml file; a missing file means Hooks are disabled."""

    workspace = Path(workspace).expanduser().resolve()
    config_path = Path(config_path).expanduser().resolve()
    if not config_path.exists():
        return HookRegistry()
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise HookConfigurationError(f"Cannot read Hook config: {config_path}") from exc
    if raw is None:
        return HookRegistry()
    if not isinstance(raw, dict) or set(raw) - {"version", "hooks"}:
        raise HookConfigurationError("Hook config must contain only version and hooks")
    if raw.get("version", 1) != 1:
        raise HookConfigurationError("Hook config version must be 1")
    entries = raw.get("hooks", [])
    if not isinstance(entries, list):
        raise HookConfigurationError("Hook config hooks must be a list")

    names: set[str] = set()
    hooks: list[HookSpec] = []
    for index, entry in enumerate(entries):
        hooks.append(_parse_hook(entry, index=index, workspace=workspace, names=names))
    return HookRegistry(tuple(hooks))


def _parse_hook(
    entry: Any,
    *,
    index: int,
    workspace: Path,
    names: set[str],
) -> HookSpec:
    if not isinstance(entry, dict):
        raise HookConfigurationError(f"Hook entry {index} must be an object")
    allowed = {
        "name",
        "stage",
        "script",
        "tools",
        "exempt_roles",
        "exempt_permissions",
        "timeout_seconds",
        "max_output_chars",
    }
    unknown = set(entry) - allowed
    if unknown:
        raise HookConfigurationError(f"Hook entry {index} has unknown fields: {sorted(unknown)}")
    name = _required_text(entry.get("name"), f"hooks[{index}].name")
    if not _HOOK_NAME_RE.fullmatch(name) or name in names:
        raise HookConfigurationError(f"Hook name is invalid or duplicated: {name}")
    names.add(name)
    stage = _required_text(entry.get("stage"), f"hooks[{index}].stage")
    if stage not in _STAGES:
        raise HookConfigurationError(f"Unsupported Hook stage: {stage}")
    script = _resolve_script(entry.get("script"), workspace, index)
    tools = _parse_tools(entry.get("tools", ["*"]), index)
    exempt_roles = _parse_roles(entry.get("exempt_roles", []), index)
    exempt_permissions = _parse_permissions(entry.get("exempt_permissions", []), index)
    timeout_seconds = _bounded_float(
        entry.get("timeout_seconds", DEFAULT_HOOK_TIMEOUT_SECONDS),
        f"hooks[{index}].timeout_seconds",
        minimum=0.1,
        maximum=MAX_HOOK_TIMEOUT_SECONDS,
    )
    max_output_chars = _bounded_int(
        entry.get("max_output_chars", DEFAULT_HOOK_OUTPUT_CHARS),
        f"hooks[{index}].max_output_chars",
        minimum=MIN_HOOK_OUTPUT_CHARS,
        maximum=MAX_HOOK_OUTPUT_CHARS,
    )
    return HookSpec(
        name=name,
        stage=stage,  # type: ignore[arg-type]
        script=script,
        tools=tools,
        exempt_roles=exempt_roles,
        exempt_permissions=exempt_permissions,
        timeout_seconds=timeout_seconds,
        max_output_chars=max_output_chars,
    )


def _resolve_script(value: Any, workspace: Path, index: int) -> Path:
    raw = _required_text(value, f"hooks[{index}].script")
    raw = raw.replace("${ZHICE_AGENT_WORKSPACE}", str(workspace))
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise HookConfigurationError(f"Hook script is outside workspace: {raw}") from exc
    if resolved.suffix.lower() != ".py" or not resolved.is_file():
        raise HookConfigurationError(f"Hook script must be an existing Python file: {raw}")
    return resolved


def _parse_tools(value: Any, index: int) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise HookConfigurationError(f"hooks[{index}].tools must be a non-empty list")
    tools: list[str] = []
    for item in value:
        name = _required_text(item, f"hooks[{index}].tools")
        if name != "*" and not _TOOL_NAME_RE.fullmatch(name):
            raise HookConfigurationError(f"Invalid Hook tool matcher: {name}")
        if name not in tools:
            tools.append(name)
    if "*" in tools and len(tools) != 1:
        raise HookConfigurationError("Hook wildcard tool matcher must be used alone")
    return tuple(tools)


def _parse_roles(value: Any, index: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise HookConfigurationError(f"hooks[{index}].exempt_roles must be a list")
    roles: list[str] = []
    for item in value:
        role = _required_text(item, f"hooks[{index}].exempt_roles")
        if not _ROLE_KEY_RE.fullmatch(role):
            raise HookConfigurationError(f"Invalid Hook exempt role: {role}")
        if role not in roles:
            roles.append(role)
    return tuple(roles)


def _parse_permissions(value: Any, index: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise HookConfigurationError(f"hooks[{index}].exempt_permissions must be a list")
    permissions: list[str] = []
    for item in value:
        permission = _required_text(item, f"hooks[{index}].exempt_permissions")
        if not _PERMISSION_KEY_RE.fullmatch(permission):
            raise HookConfigurationError(f"Invalid Hook exempt permission: {permission}")
        if permission not in permissions:
            permissions.append(permission)
    return tuple(permissions)


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HookConfigurationError(f"Hook config field is required: {field}")
    return value.strip()


def _bounded_float(value: Any, field: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise HookConfigurationError(f"Hook config field must be a number: {field}")
    try:
        resolved = float(value)
    except (TypeError, ValueError) as exc:
        raise HookConfigurationError(f"Hook config field must be a number: {field}") from exc
    if resolved < minimum or resolved > maximum:
        raise HookConfigurationError(f"Hook config field is outside allowed range: {field}")
    return resolved


def _bounded_int(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HookConfigurationError(f"Hook config field must be an integer: {field}")
    if value < minimum or value > maximum:
        raise HookConfigurationError(f"Hook config field is outside allowed range: {field}")
    return value
