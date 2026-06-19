"""LLM provider implementations."""

from agent.llm.failover_provider import EndpointFailoverProvider
from agent.llm.litellm_provider import LiteLLMProvider
from agent.llm.openai_provider import OpenAIProvider
from agent.protocols.llm import (
    LLMConfigurationError,
    LLMEndpoint,
    LLMProvider,
    LLMProviderError,
    LLMResponse,
)


def create_llm_provider(endpoint: LLMEndpoint) -> LLMProvider:
    """Create a concrete provider for a validated endpoint."""

    if endpoint.protocol == "openai":
        return OpenAIProvider(endpoint)
    if endpoint.protocol == "litellm":
        return LiteLLMProvider(endpoint)
    raise LLMConfigurationError(f"Unsupported LLM protocol: {endpoint.protocol}")


def create_llm_provider_chain(
    endpoints: list[LLMEndpoint],
    *,
    preferred_endpoint: str | None = None,
) -> LLMProvider:
    """Create an LLM provider that can expose model state and fail over."""

    enabled = [endpoint for endpoint in endpoints if endpoint.enabled]
    if not enabled:
        raise LLMConfigurationError("No enabled LLM endpoints are configured.")
    if preferred_endpoint and preferred_endpoint not in {endpoint.name for endpoint in enabled}:
        raise LLMConfigurationError(f"LLM endpoint is not configured or enabled: {preferred_endpoint}")
    return EndpointFailoverProvider(enabled, preferred_endpoint=preferred_endpoint)


__all__ = [
    "EndpointFailoverProvider",
    "create_llm_provider",
    "create_llm_provider_chain",
    "LLMConfigurationError",
    "LLMEndpoint",
    "LLMProvider",
    "LLMProviderError",
    "LLMResponse",
    "LiteLLMProvider",
    "OpenAIProvider",
]
