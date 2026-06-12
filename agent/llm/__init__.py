"""LLM provider implementations."""

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
        raise LLMConfigurationError(
            "LiteLLMProvider is not implemented yet. Anthropic, Gemini, DeepSeek, "
            "and other non-OpenAI providers should be routed through LiteLLM later."
        )
    raise LLMConfigurationError(f"Unsupported LLM protocol: {endpoint.protocol}")


__all__ = [
    "create_llm_provider",
    "LLMConfigurationError",
    "LLMEndpoint",
    "LLMProvider",
    "LLMProviderError",
    "LLMResponse",
    "OpenAIProvider",
]
