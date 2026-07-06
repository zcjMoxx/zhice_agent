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
    provider: str = ""
    max_tokens: int = 4096
    temperature: float = 0.7
    priority: int = 1
    enabled: bool = True
    role: str = "default"
    supported_models: tuple[str, ...] = ()


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
