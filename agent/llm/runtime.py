"""Runtime helpers for building configured LLM providers."""

from __future__ import annotations

from pathlib import Path

from agent.config import load_llm_endpoints, resolve_llm_endpoint_alias
from agent.llm import create_llm_provider_chain
from agent.protocols.llm import LLMConfigurationError, LLMEndpoint, LLMProvider


def create_configured_llm_provider(
    config_dir: Path,
    endpoint_name: str = "auto",
) -> LLMProvider:
    """Build the runtime LLM provider chain from endpoint config."""

    endpoints = load_llm_endpoints(config_dir)
    preferred_endpoint = resolve_preferred_endpoint(config_dir, endpoint_name, endpoints)
    validate_startup_llm_endpoints(endpoints, preferred_endpoint)
    return create_llm_provider_chain(endpoints, preferred_endpoint=preferred_endpoint)


def validate_startup_llm_endpoints(
    endpoints: list[LLMEndpoint],
    preferred_endpoint: str | None,
) -> None:
    """Reject endpoint sets that cannot produce any chat response."""

    enabled = [endpoint for endpoint in endpoints if endpoint.enabled]
    if not enabled:
        raise LLMConfigurationError("No enabled LLM endpoints are configured.")
    if preferred_endpoint:
        for endpoint in enabled:
            if endpoint.name == preferred_endpoint:
                if not endpoint.api_key.strip():
                    raise LLMConfigurationError(
                        f"LLM endpoint {preferred_endpoint!r} is missing api_key."
                    )
                return
    if not any(endpoint.api_key.strip() for endpoint in enabled):
        raise LLMConfigurationError("No enabled LLM endpoints have api_key configured.")


def resolve_preferred_endpoint(
    config_dir: Path,
    endpoint_name: str,
    endpoints: list[LLMEndpoint],
) -> str | None:
    """Resolve a requested endpoint into the concrete startup preference.

    ``auto`` means: use a configured default alias if present, else a real
    endpoint named "default", else let the failover provider choose by priority.
    """

    resolved = resolve_llm_endpoint_alias(config_dir, endpoint_name)
    if resolved:
        return resolved
    default_alias = resolve_llm_endpoint_alias(config_dir, "default")
    endpoint_names = {endpoint.name for endpoint in endpoints if endpoint.enabled}
    if default_alias and default_alias in endpoint_names:
        return default_alias
    if "default" in endpoint_names:
        return "default"
    return None
