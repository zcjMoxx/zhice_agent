"""LLM provider protocol and shared data structures."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class LLMEndpoint:
    """Configuration for one LLM endpoint."""

    name: str
    protocol: str
    base_url: str
    model: str
    api_key: str
    context_window: int = 131072
    provider: str = ""
    # Maximum number of output tokens requested from the provider.
    max_tokens: int = 4096
    temperature: float = 0.7
    priority: int = 1
    enabled: bool = True
    role: str = "default"
    supported_models: tuple[str, ...] = ()
    input_price_per_million: float = 0.0
    output_price_per_million: float = 0.0


def effective_input_token_limit(endpoint: LLMEndpoint) -> int:
    """Return the endpoint input budget after reserving maximum output tokens."""

    return endpoint.context_window - endpoint.max_tokens


@dataclass(frozen=True)
class ContextBudget:
    """Input budget that remains valid across one complete failover chain."""

    input_token_limit: int
    endpoint_names: tuple[str, ...] = ()


@dataclass
class LLMResponse:
    """Provider-neutral LLM response."""

    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize missing provider content to an empty string."""

        if self.content is None:
            self.content = ""


@dataclass
class LLMStreamChunk:
    """One provider-neutral streaming update from an LLM provider."""

    content_delta: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class LLMProviderError(RuntimeError):
    """Base error raised by LLM providers."""


class LLMConfigurationError(LLMProviderError):
    """Raised when LLM configuration is missing or invalid."""


class LLMContextBudgetError(LLMProviderError):
    """Raised when required prompt content cannot fit the endpoint input budget."""


@dataclass(frozen=True)
class ModelSelection:
    """Provider-neutral, call-scoped endpoint and model selection."""

    endpoint_name: str
    model_name: str
    source: str = "system"
    reason_code: str = ""
    context_budget: ContextBudget | None = None


class LLMProvider(Protocol):
    """Minimal synchronous chat contract consumed by AgentLoop."""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Return one assistant response for the provided messages."""


class StreamingLLMProvider(LLMProvider, Protocol):
    """Optional streaming contract consumed when a provider implements it."""

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterable[LLMStreamChunk | str]:
        """Yield incremental assistant content for the provided messages."""


class LLMProviderResolver(Protocol):
    """Bind a call-scoped ModelSelection to an independent provider."""

    def bind(self, selection: ModelSelection) -> LLMProvider:
        """Return a provider whose mutable preference is not shared across turns."""

    def context_budget(self, selection: ModelSelection | None = None) -> ContextBudget:
        """Return the input budget valid for every endpoint in the failover chain."""
