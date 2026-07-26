"""Startup diagnostics for optional Session context engineering capabilities."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from agent.embedding.openai_compatible import load_embedding_provider
from agent.logging_utils import log_event
from agent.prompt_loader import PromptLoader, PromptNotFoundError
from agent.protocols.capability import CapabilityStatus
from agent.protocols.embedding import EmbeddingProvider

startup_logger = logging.getLogger("zcagent.agent.context")

CONTEXT_ENGINEERING_PROMPTS = (
    "context_compaction",
    "history_query_planner",
    "context_query_rewrite",
)


@dataclass(frozen=True)
class ContextEngineeringStartupResult:
    """Resolved optional provider plus a transport-neutral startup status."""

    embedding_provider: EmbeddingProvider | None
    status: CapabilityStatus


def check_context_engineering_startup(
    config_dir: Path | str,
    prompt_loader: PromptLoader,
) -> ContextEngineeringStartupResult:
    """Report missing derived-context inputs without disabling safe fallbacks."""

    missing_prompts: list[str] = []
    invalid_prompts: list[str] = []
    for name in CONTEXT_ENGINEERING_PROMPTS:
        try:
            prompt = prompt_loader.load(name)
        except PromptNotFoundError:
            missing_prompts.append(f"{name}.md")
            continue
        except (OSError, UnicodeError):
            invalid_prompts.append(f"{name}.md")
            continue
        if not prompt.strip():
            invalid_prompts.append(f"{name}.md")

    config_path = Path(config_dir) / "models.json"
    embedding_state = "available"
    embedding_error = ""
    try:
        embedding_provider = load_embedding_provider(config_dir)
    except Exception as exc:  # noqa: BLE001 - semantic retrieval remains optional.
        embedding_provider = None
        embedding_state = "invalid"
        embedding_error = type(exc).__name__
    else:
        if embedding_provider is None:
            embedding_state = "not_configured" if not config_path.exists() else "unavailable"

    details = {
        "missing_prompts": tuple(missing_prompts),
        "invalid_prompts": tuple(invalid_prompts),
        "embedding_state": embedding_state,
    }
    if embedding_error:
        details["embedding_error_type"] = embedding_error

    if not missing_prompts and not invalid_prompts and embedding_provider is not None:
        return ContextEngineeringStartupResult(
            embedding_provider=embedding_provider,
            status=CapabilityStatus(
                name="context_engineering",
                state="available",
                code="CONTEXT_ENGINEERING_AVAILABLE",
                message="Full Session context engineering and semantic retrieval are available.",
                details=details,
            ),
        )

    messages: list[str] = []
    hints: list[str] = []
    if missing_prompts or invalid_prompts:
        messages.append("One or more derived-context prompts are missing or invalid.")
        hints.append("Run zcagent init to non-destructively restore missing runtime prompts.")
    if embedding_state != "available":
        messages.append("Semantic retrieval is unavailable; full history, deterministic history queries, and FTS remain available.")
        hints.append("Configure routing.embedding in config/models.json and its API credential, then restart.")
    status = CapabilityStatus(
        name="context_engineering",
        state="degraded",
        code="CONTEXT_ENGINEERING_DEGRADED",
        message=" ".join(messages),
        hint=" ".join(dict.fromkeys(hints)),
        details=details,
    )
    log_event(
        startup_logger,
        logging.WARNING,
        "context.startup_degraded",
        code=status.code,
        missing_prompts=",".join(missing_prompts),
        invalid_prompts=",".join(invalid_prompts),
        embedding_state=embedding_state,
        embedding_error_type=embedding_error,
    )
    return ContextEngineeringStartupResult(
        embedding_provider=embedding_provider,
        status=status,
    )
