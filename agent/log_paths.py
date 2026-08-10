"""Canonical paths and timezone for workspace runtime trace logs."""

from __future__ import annotations

from datetime import date, timedelta, timezone
from pathlib import Path

BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


def daily_trace_path(logs_dir: Path, day: date) -> Path:
    """Return the canonical JSONL trace path for one local calendar day."""

    return Path(logs_dir) / f"log-{day.isoformat()}.jsonl"
