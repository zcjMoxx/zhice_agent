"""Fail-closed Subagent capability checks that do not block the application."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from agent.logging_utils import log_event
from agent.prompt_loader import PromptLoader, PromptNotFoundError
from agent.protocols.capability import CapabilityStatus
from agent.subagents.config import (
    SubagentConfig,
    SubagentConfigurationError,
    load_subagent_config,
)

SUBAGENT_REQUIRED_PROMPTS = (
    "subagent",
    "subagent_orchestration",
    "subagent_once",
)
startup_logger = logging.getLogger("zcagent.agent.subagent")


@dataclass(frozen=True)
class SubagentStartupResult:
    """Validated config plus safe optional-capability status."""

    config: SubagentConfig
    status: CapabilityStatus


def check_subagent_startup(
    config_dir: Path,
    prompt_loader: PromptLoader,
) -> SubagentStartupResult:
    """Disable only Subagent when its explicit runtime inputs are invalid."""

    try:
        config = load_subagent_config(config_dir)
    except (SubagentConfigurationError, OSError) as exc:
        return _unavailable(
            "SUBAGENT_CONFIG_INVALID",
            "Subagent configuration is invalid.",
            "Fix the subagents section in config/config.yml, then restart the process.",
            error_type=type(exc).__name__,
        )
    if not config.enabled:
        return SubagentStartupResult(
            config=SubagentConfig(),
            status=CapabilityStatus(
                name="subagent",
                state="disabled",
                code="SUBAGENT_DISABLED",
                message="Subagent is not enabled for this workspace.",
                hint="Enable and configure the subagents section in config/config.yml.",
            ),
        )
    for prompt_name in SUBAGENT_REQUIRED_PROMPTS:
        try:
            prompt_loader.load(prompt_name)
        except PromptNotFoundError:
            return _unavailable(
                "SUBAGENT_PROMPT_NOT_FOUND",
                f"Required Subagent runtime prompt is missing: {prompt_name}.md",
                "Run zcagent init, then restart the process.",
                missing_prompt=f"{prompt_name}.md",
            )
    return SubagentStartupResult(
        config=config,
        status=CapabilityStatus(
            name="subagent",
            state="available",
            code="SUBAGENT_AVAILABLE",
            message="Subagent runtime is available.",
        ),
    )


def _unavailable(code: str, message: str, hint: str, **details) -> SubagentStartupResult:
    log_event(
        startup_logger,
        logging.WARNING,
        "subagent.runtime_unavailable",
        code=code,
        message=message,
        hint=hint,
        **details,
    )
    return SubagentStartupResult(
        config=SubagentConfig(),
        status=CapabilityStatus(
            name="subagent",
            state="unavailable",
            code=code,
            message=message,
            hint=hint,
            details=dict(details),
        ),
    )
