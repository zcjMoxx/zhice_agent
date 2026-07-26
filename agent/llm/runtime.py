"""Runtime helpers for building configured LLM providers."""

from __future__ import annotations

from pathlib import Path

from agent.config import load_llm_endpoints, resolve_llm_endpoint_alias, resolve_model_route
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
    provider = create_llm_provider_chain(endpoints, preferred_endpoint=preferred_endpoint)
    _, routed_model = resolve_model_route(
        config_dir,
        "default" if endpoint_name == "auto" else endpoint_name,
    )
    if preferred_endpoint and routed_model:
        endpoint = next(item for item in endpoints if item.name == preferred_endpoint)
        if routed_model != endpoint.model and routed_model not in endpoint.supported_models:
            raise LLMConfigurationError(
                f"Endpoint {preferred_endpoint!r} does not support routed model {routed_model!r}"
            )
        provider.set_preferred(preferred_endpoint, routed_model)
    return provider


def create_optional_aliased_llm_provider(
    config_dir: Path,
    alias: str,
) -> LLMProvider | None:
    """Build an aliased provider when the alias or same-named endpoint exists."""

    endpoints = load_llm_endpoints(config_dir)
    resolved = resolve_llm_endpoint_alias(config_dir, alias)
    enabled_names = {endpoint.name for endpoint in endpoints if endpoint.enabled}
    if resolved not in enabled_names:
        return None
    return create_configured_llm_provider(config_dir, alias)


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
