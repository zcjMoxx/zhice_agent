"""Shared parsing for the unified workspace config/config.yml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml


class RuntimeConfigurationError(ValueError):
    """Raised when the unified config root cannot be trusted."""


@dataclass(frozen=True)
class OperationsTerminalConfig:
    """Non-secret main-Web projection settings for the independent Ops UI."""

    enabled: bool = False
    url: str = ""
    presentation: str = "both"


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


def load_runtime_config(config_dir: Path | str) -> dict[str, Any]:
    path = Path(config_dir) / "config.yml"
    if not path.exists():
        return {}
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RuntimeConfigurationError(f"Cannot read unified config: {path}") from exc
    if not isinstance(raw, dict):
        raise RuntimeConfigurationError("config.yml must contain a mapping")
    version = raw.get("schema_version", 1)
    if version != 1:
        raise RuntimeConfigurationError("config.yml schema_version must be 1")
    return raw


def load_runtime_section(
    config_dir: Path | str,
    section: str,
    *,
    default: Any = None,
) -> Any:
    raw = load_runtime_config(config_dir)
    return raw.get(section, default)


def load_operations_terminal_config(config_dir: Path | str) -> OperationsTerminalConfig:
    """Load and validate the non-secret independent Ops terminal link."""

    operations = load_runtime_section(config_dir, "operations", default={})
    if operations is None:
        operations = {}
    if not isinstance(operations, dict):
        raise RuntimeConfigurationError("config.yml operations must be a mapping")
    terminal = operations.get("terminal", {})
    if terminal is None:
        terminal = {}
    if not isinstance(terminal, dict):
        raise RuntimeConfigurationError("config.yml operations.terminal must be a mapping")

    enabled = _strict_bool(terminal.get("enabled", False), "operations.terminal.enabled")
    raw_url = terminal.get("url", "")
    if not isinstance(raw_url, str):
        raise RuntimeConfigurationError("config.yml operations.terminal.url must be a string")
    url = raw_url.strip()
    presentation = terminal.get("presentation", "both")
    if not isinstance(presentation, str):
        raise RuntimeConfigurationError(
            "config.yml operations.terminal.presentation must be a string"
        )
    presentation = presentation.strip() or "both"
    if presentation not in {"new_tab", "embed", "both"}:
        raise RuntimeConfigurationError(
            "config.yml operations.terminal.presentation must be new_tab, embed, or both"
        )
    if enabled and not url:
        raise RuntimeConfigurationError(
            "config.yml operations.terminal.url is required when enabled"
        )
    if url:
        _validate_operations_url(url)
    return OperationsTerminalConfig(
        enabled=enabled,
        url=url,
        presentation=presentation,
    )


def _strict_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise RuntimeConfigurationError(f"config.yml {field_name} must be a boolean")


def _validate_operations_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise RuntimeConfigurationError(
            "config.yml operations.terminal.url is invalid"
        ) from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeConfigurationError(
            "config.yml operations.terminal.url must be an absolute HTTP(S) URL"
        )
    if parsed.username is not None or parsed.password is not None:
        raise RuntimeConfigurationError(
            "config.yml operations.terminal.url must not contain credentials"
        )
    if parsed.query or parsed.fragment:
        raise RuntimeConfigurationError(
            "config.yml operations.terminal.url must not contain query or fragment"
        )
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and parsed.hostname not in local_hosts:
        raise RuntimeConfigurationError(
            "config.yml operations.terminal.url must use HTTPS outside local development"
        )
    if port is not None and not 1 <= port <= 65535:
        raise RuntimeConfigurationError(
            "config.yml operations.terminal.url contains an invalid port"
        )
