"""Human-facing presentation helpers for Subagent capability state."""

from __future__ import annotations

from agent.protocols.auth import ActorContext
from agent.protocols.capability import CapabilityStatus

GENERIC_SUBAGENT_UNAVAILABLE_TEXT = (
    "Subagent is temporarily unavailable. Please contact an administrator."
)


def can_view_subagent_details(actor: ActorContext | None) -> bool:
    """Return whether one actor may see internal capability failure details."""

    if actor is None:
        return False
    return (
        actor.actor_type == "local_operator"
        or "owner" in actor.role_keys
        or actor.has_permission("audit.read")
    )


def format_subagent_unavailable(
    status: CapabilityStatus | None,
    *,
    include_details: bool,
) -> str:
    """Return readable command text without exposing the machine payload shape."""

    if not include_details:
        return GENERIC_SUBAGENT_UNAVAILABLE_TEXT
    message = (
        status.message.strip()
        if status is not None and status.message.strip()
        else "Subagent runtime is unavailable."
    )
    text = f"Subagent is currently unavailable: {message}"
    hint = status.hint.strip() if status is not None and status.hint.strip() else ""
    if hint:
        text = f"{text} {hint}"
    return text
