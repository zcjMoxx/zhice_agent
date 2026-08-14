"""Fail-closed configuration for the travel application."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.runtime_config import RuntimeConfigurationError, load_runtime_section


class TravelConfigurationError(RuntimeConfigurationError):
    """Raised when an explicit travel section cannot be trusted."""


@dataclass(frozen=True)
class TravelConfig:
    """Validated workspace-level limits for travel planning."""

    enabled: bool = False
    default_mode: str = "quick"
    max_search_results: int = 8
    max_evidence_items: int = 40
    deep_subagent_count: int = 3
    xhs_readonly_enabled: bool = True
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
        "default_mode",
        "max_search_results",
        "max_evidence_items",
        "deep_subagent_count",
        "xhs_readonly_enabled",
        "max_plan_bytes",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise TravelConfigurationError(f"config.yml travel has unknown fields: {unknown}")
    enabled = _boolean(raw.get("enabled", False), "travel.enabled")
    default_mode = raw.get("default_mode", "quick")
    if not isinstance(default_mode, str) or default_mode not in {"quick", "deep"}:
        raise TravelConfigurationError("config.yml travel.default_mode must be quick or deep")
    return TravelConfig(
        enabled=enabled,
        default_mode=default_mode,
        max_search_results=_integer(
            raw.get("max_search_results", 8), "travel.max_search_results", 1, 20
        ),
        max_evidence_items=_integer(
            raw.get("max_evidence_items", 40), "travel.max_evidence_items", 1, 100
        ),
        deep_subagent_count=_integer(
            raw.get("deep_subagent_count", 3), "travel.deep_subagent_count", 1, 3
        ),
        xhs_readonly_enabled=_boolean(
            raw.get("xhs_readonly_enabled", True), "travel.xhs_readonly_enabled"
        ),
        max_plan_bytes=_integer(
            raw.get("max_plan_bytes", 512 * 1024),
            "travel.max_plan_bytes",
            64 * 1024,
            2 * 1024 * 1024,
        ),
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
