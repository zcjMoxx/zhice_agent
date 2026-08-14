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
    request_timeout_seconds: float = 180.0
    max_attempts: int = 2
    total_deadline_seconds: float = 180.0
    retry_backoff_seconds: float = 0.5
    retry_backoff_max_seconds: float = 8.0
    retry_jitter_ratio: float = 0.1
    cooldown_seconds: float = 30.0


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


@dataclass(frozen=True)
class LLMResponseFormat:
    """Provider-neutral strict JSON Schema requested for one LLM call."""

    name: str
    schema: dict[str, Any]
    strict: bool = True

    def to_openai(self) -> dict[str, Any]:
        """Return the OpenAI-compatible response_format request shape."""

        return {
            "type": "json_schema",
            "json_schema": {
                "name": self.name,
                "strict": self.strict,
                "schema": self.schema,
            },
        }


@dataclass(frozen=True)
class LLMGenerationOptions:
    """Optional call-scoped generation controls independent of endpoint defaults."""

    temperature: float | None = None

    def __post_init__(self) -> None:
        if self.temperature is not None and not 0.0 <= self.temperature <= 2.0:
            raise ValueError("temperature must be between 0 and 2")


@dataclass
class LLMStreamChunk:
    """One provider-neutral streaming update from an LLM provider."""

    content_delta: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class LLMProviderError(RuntimeError):
    """Structured, provider-neutral error raised by LLM providers.

    ``message`` remains the first positional argument so older adapters and
    test doubles can keep raising ``LLMProviderError("...")``.  The extra
    fields are deliberately safe and bounded; raw response bodies, request
    messages, credentials, and tool arguments must never be attached here.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "PROVIDER_ERROR",
        http_status: int | None = None,
        retryable: bool = False,
        safe_message: str | None = None,
        endpoint: str = "",
        model: str = "",
        attempts: list[dict[str, Any]] | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        safe = (safe_message or message or "LLM provider request failed.")[:500]
        super().__init__(safe)
        self.code = code
        self.http_status = http_status
        self.retryable = retryable
        self.safe_message = safe
        self.endpoint = endpoint
        self.model = model
        self.attempts = list(attempts or [])
        self.retry_after_seconds = retry_after_seconds


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
        response_format: LLMResponseFormat | None = None,
        generation_options: LLMGenerationOptions | None = None,
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
