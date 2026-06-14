"""LiteLLM proxy chat completions provider."""

from __future__ import annotations

from typing import Any, Callable

from agent.llm.openai_provider import OpenAIProvider
from agent.protocols.llm import LLMEndpoint


class LiteLLMProvider(OpenAIProvider):
    """Adapt a LiteLLM proxy endpoint to LLMProvider.

    LiteLLM proxy exposes an OpenAI-compatible `/chat/completions` API, so this
    provider intentionally reuses the same transport, message cleaning, tool
    schema handling, response parsing, and error redaction as OpenAIProvider.
    """

    def __init__(
        self,
        endpoint: LLMEndpoint,
        urlopen: Callable[..., Any] | None = None,
        timeout: float = 60.0,
    ):
        super().__init__(endpoint=endpoint, urlopen=urlopen, timeout=timeout)
