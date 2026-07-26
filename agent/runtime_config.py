"""Shared parsing for the unified workspace config/config.yml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class RuntimeConfigurationError(ValueError):
    """Raised when the unified config root cannot be trusted."""


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
