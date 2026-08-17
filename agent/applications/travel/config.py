"""Fail-closed configuration for the travel application."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.runtime_config import RuntimeConfigurationError, load_runtime_section


class TravelConfigurationError(RuntimeConfigurationError):
    """Raised when an explicit travel section cannot be trusted."""


logger = logging.getLogger(__name__)
_DEPRECATED_FIELDS = {
    "default_mode",
    "max_search_results",
    "deep_subagent_count",
    "xhs_readonly_enabled",
}


@dataclass(frozen=True)
class TravelConfig:
    """Validated workspace-level limits for travel planning."""

    enabled: bool = False
    max_evidence_items: int = 40
    max_plan_bytes: int = 512 * 1024


def load_travel_config(config_dir: Path | str) -> TravelConfig:
    """Load config.yml travel; a missing section intentionally disables it."""

    try:
        raw = load_runtime_section(config_dir, "travel", default={})
    except ValueError as exc:
        raise TravelConfigurationError("Cannot read travel config: config.yml") from exc
    if raw is None or raw == "":
        return TravelConfig()
    if not isinstance(raw, dict):
        raise TravelConfigurationError("config.yml travel must be a mapping")
    if not raw:
        return TravelConfig()
    allowed = {
        "enabled",
        "max_evidence_items",
        "max_plan_bytes",
        *_DEPRECATED_FIELDS,
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise TravelConfigurationError(f"config.yml travel has unknown fields: {unknown}")
    _validate_deprecated_fields(raw)
    return TravelConfig(
        enabled=_boolean(raw.get("enabled", False), "travel.enabled"),
        max_evidence_items=_integer(
            raw.get("max_evidence_items", 40), "travel.max_evidence_items", 1, 100
        ),
        max_plan_bytes=_integer(
            raw.get("max_plan_bytes", 512 * 1024),
            "travel.max_plan_bytes",
            64 * 1024,
            2 * 1024 * 1024,
        ),
    )


def _validate_deprecated_fields(raw: dict[str, Any]) -> None:
    present = sorted(_DEPRECATED_FIELDS.intersection(raw))
    if not present:
        return
    if "default_mode" in raw and raw["default_mode"] not in {"quick", "deep"}:
        raise TravelConfigurationError("config.yml travel.default_mode must be quick or deep")
    if "max_search_results" in raw:
        _integer(raw["max_search_results"], "travel.max_search_results", 1, 20)
    if "deep_subagent_count" in raw:
        _integer(raw["deep_subagent_count"], "travel.deep_subagent_count", 1, 3)
    if "xhs_readonly_enabled" in raw:
        _boolean(raw["xhs_readonly_enabled"], "travel.xhs_readonly_enabled")
    logger.warning(
        "Ignoring deprecated travel configuration fields: %s",
        ", ".join(present),
    )


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise TravelConfigurationError(f"config.yml {field} must be a boolean")
    return value


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise TravelConfigurationError(
            f"config.yml {field} must be an integer between {minimum} and {maximum}"
        )
    return value
