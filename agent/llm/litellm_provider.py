"""In-process LiteLLM SDK provider."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.llm.openai_provider import _clean_message, _read_api_key
from agent.protocols.llm import LLMConfigurationError, LLMEndpoint, LLMProviderError, LLMResponse

CompletionCallable = Callable[..., Any]


class LiteLLMProvider:
    """Adapt LiteLLM's Python SDK to the local LLMProvider protocol."""

    def __init__(
        self,
        endpoint: LLMEndpoint,
        completion: CompletionCallable | None = None,
        timeout: float = 60.0,
    ):
        """Store endpoint config and optional test double for litellm.completion."""

        self.endpoint = endpoint
        self._completion = completion
        self._timeout = timeout

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Call LiteLLM SDK with OpenAI-compatible messages and tools."""

        api_key = _read_api_key(self.endpoint)
        completion = self._completion or _load_litellm_completion()
        request_model = _format_litellm_model(self.endpoint)
        kwargs: dict[str, Any] = {
            "model": request_model,
            "messages": [_clean_message(message) for message in messages],
            "max_tokens": self.endpoint.max_tokens,
            "temperature": self.endpoint.temperature,
            "timeout": self._timeout,
            "num_retries": 0,
            "api_key": api_key,
        }
        if self.endpoint.base_url:
            kwargs["api_base"] = self.endpoint.base_url
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            raw = completion(**kwargs)
        except Exception as exc:  # noqa: BLE001 - provider boundary must redact and normalize.
            raise LLMProviderError(
                f"LiteLLM request failed: {_safe_litellm_error(str(exc), api_key)}"
            ) from exc

        return _parse_litellm_response(raw, request_model)


def _load_litellm_completion() -> CompletionCallable:
    """Import LiteLLM lazily so config validation can run without the SDK."""

    try:
        from litellm import completion
    except ImportError as exc:
        raise LLMConfigurationError(
            "LiteLLM SDK is not installed. Install project dependencies or run "
            "`pip install litellm`."
        ) from exc
    return completion


def _format_litellm_model(endpoint: LLMEndpoint) -> str:
    """Build the SDK model name from local provider + plain model fields."""

    return f"{endpoint.provider.strip()}/{endpoint.model.strip()}"


def _parse_litellm_response(raw: Any, fallback_model: str) -> LLMResponse:
    """Normalize LiteLLM SDK responses into the shared LLMResponse shape."""

    data = _response_to_dict(raw)
    choices = _get(data, "choices") or []
    first_choice = choices[0] if choices else {}
    message = _get(first_choice, "message") or {}
    content = _get(message, "content") or ""
    tool_calls = _get(message, "tool_calls") or []
    metadata: dict[str, Any] = {
        "model": _get(data, "model") or fallback_model,
        "finish_reason": _get(first_choice, "finish_reason"),
    }
    usage = _get(data, "usage")
    if usage is not None:
        metadata["usage"] = _response_to_dict(usage)
    reasoning_content = _get(message, "reasoning_content")
    if reasoning_content is not None:
        metadata["reasoning_content"] = reasoning_content
    return LLMResponse(
        content=str(content),
        tool_calls=_normalize_tool_calls(tool_calls),
        metadata=metadata,
    )


def _response_to_dict(value: Any) -> Any:
    """Recursively convert SDK response objects into plain Python values."""

    if isinstance(value, dict):
        return {key: _response_to_dict(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_response_to_dict(item) for item in value]
    if hasattr(value, "model_dump"):
        return _response_to_dict(value.model_dump())
    if hasattr(value, "dict"):
        return _response_to_dict(value.dict())
    return value


def _get(value: Any, key: str) -> Any:
    """Read a key from either a dict response or an SDK object."""

    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _normalize_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    """Return only dict-shaped tool calls after SDK response normalization."""

    normalized = _response_to_dict(tool_calls)
    if isinstance(normalized, list):
        return [item for item in normalized if isinstance(item, dict)]
    return []


def _safe_litellm_error(message: str, api_key: str) -> str:
    """Trim LiteLLM errors and redact the endpoint api_key."""

    safe = message or "unknown provider error"
    if api_key:
        safe = safe.replace(api_key, "[redacted]")
    return safe[:500]
