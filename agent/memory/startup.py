"""Startup boundary for optional background Memory extraction."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agent.logging_utils import log_event
from agent.prompt_loader import PromptLoader, PromptNotFoundError
from agent.protocols.capability import CapabilityStatus

startup_logger = logging.getLogger("zcagent.agent.memory")


@dataclass(frozen=True)
class MemoryExtractionStartupResult:
    """Whether background extraction may start plus its safe status."""

    enabled: bool
    status: CapabilityStatus


def check_memory_extraction_startup(
    prompt_loader: PromptLoader,
    *,
    enabled: bool = True,
) -> MemoryExtractionStartupResult:
    """Validate extraction-only inputs without disabling Memory read/write."""

    if not enabled:
        return MemoryExtractionStartupResult(
            enabled=False,
            status=CapabilityStatus(
                name="memory_extraction",
                state="disabled",
                code="MEMORY_EXTRACTION_DISABLED",
                message="Background Memory extraction is disabled.",
            ),
        )
    try:
        prompt = prompt_loader.load("memory_extraction")
    except PromptNotFoundError as exc:
        return _unavailable(
            "MEMORY_EXTRACTION_PROMPT_NOT_FOUND",
            "Required built-in Memory extraction prompt is missing: memory_extraction.md",
            "Run zcagent init, then restart the process.",
            exc,
        )
    except (OSError, UnicodeError) as exc:
        return _unavailable(
            "MEMORY_EXTRACTION_PROMPT_INVALID",
            "Built-in Memory extraction prompt is unreadable or invalid.",
            "Restore prompts/memory_extraction.md, then restart the process.",
            exc,
        )
    if not prompt.strip():
        return _unavailable(
            "MEMORY_EXTRACTION_PROMPT_INVALID",
            "Built-in Memory extraction prompt is empty or invalid.",
            "Restore prompts/memory_extraction.md, then restart the process.",
            ValueError("empty prompt"),
        )
    return MemoryExtractionStartupResult(
        enabled=True,
        status=CapabilityStatus(
            name="memory_extraction",
            state="available",
            code="MEMORY_EXTRACTION_AVAILABLE",
            message="Background Memory extraction is available.",
        ),
    )


def _unavailable(
    code: str,
    message: str,
    hint: str,
    exc: BaseException,
) -> MemoryExtractionStartupResult:
    log_event(
        startup_logger,
        logging.WARNING,
        "memory.extraction_unavailable",
        code=code,
        message=message,
        hint=hint,
        prompt_file="memory_extraction.md",
        error_type=type(exc).__name__,
    )
    return MemoryExtractionStartupResult(
        enabled=False,
        status=CapabilityStatus(
            name="memory_extraction",
            state="unavailable",
            code=code,
            message=message,
            hint=hint,
            details={
                "prompt_file": "memory_extraction.md",
                "error_type": type(exc).__name__,
            },
        ),
    )
