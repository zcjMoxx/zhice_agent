"""OpenAI-compatible chat completions provider."""

from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib import error, request

from agent.protocols.llm import (
    LLMConfigurationError,
    LLMEndpoint,
    LLMProviderError,
    LLMResponse,
)

_ALLOWED_MESSAGE_KEYS = {"role", "content", "tool_calls", "tool_call_id", "name"}
_default_urlopen = request.urlopen


class OpenAIProvider:
    """Adapt an OpenAI-compatible chat endpoint to LLMProvider."""

    def __init__(
        self,
        endpoint: LLMEndpoint,
        urlopen: Callable[..., Any] | None = None,
        timeout: float = 60.0,
    ):
        """Store endpoint config and injectable HTTP transport for tests."""

        self.endpoint = endpoint
        self._urlopen = urlopen or _default_urlopen
        self._timeout = min(timeout, endpoint.request_timeout_seconds)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Send one chat completion request to an OpenAI-compatible endpoint."""

        api_key = _read_api_key(self.endpoint)
        payload: dict[str, Any] = {
            "model": self.endpoint.model,
            "messages": [_clean_message(message) for message in messages],
            "max_tokens": self.endpoint.max_tokens,
            "temperature": self.endpoint.temperature,
        }
        if tools:
            payload["tools"] = tools

        raw = self._post_json("chat/completions", payload, api_key)
        return _parse_openai_response(raw, self.endpoint.model, endpoint=self.endpoint)

    def _post_json(self, path: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
        """POST JSON to the configured base URL and decode the JSON response."""

        url = f"{self.endpoint.base_url.rstrip('/')}/{path}"
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._urlopen(req, timeout=self._timeout) as response:
                response_body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            raise _provider_http_error(exc, self.endpoint, api_key) from exc
        except error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            is_timeout = isinstance(reason, (TimeoutError, socket.timeout))
            raise LLMProviderError(
                "LLM request timed out." if is_timeout else "LLM network request failed.",
                code="TIMEOUT" if is_timeout else "NETWORK_ERROR",
                retryable=True,
                endpoint=self.endpoint.name,
                model=self.endpoint.model,
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise LLMProviderError(
                "LLM request timed out.",
                code="TIMEOUT",
                retryable=True,
                endpoint=self.endpoint.name,
                model=self.endpoint.model,
            ) from exc

        try:
            return json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise LLMProviderError(
                "LLM response was not valid JSON.",
                code="INVALID_RESPONSE",
                retryable=True,
                endpoint=self.endpoint.name,
                model=self.endpoint.model,
            ) from exc


def _read_api_key(endpoint: LLMEndpoint) -> str:
    """Return the configured API key or raise a setup-focused error."""

    api_key = endpoint.api_key.strip()
    if api_key:
        return api_key
    raise LLMConfigurationError("LLM API key is missing. Set api_key in models.json.")


def _clean_message(message: dict[str, Any]) -> dict[str, Any]:
    """Keep only OpenAI-supported message fields and normalize empty content."""

    cleaned = {key: value for key, value in message.items() if key in _ALLOWED_MESSAGE_KEYS}
    role = cleaned.get("role")
    content = cleaned.get("content")
    has_tool_calls = bool(cleaned.get("tool_calls"))
    if content is None:
        cleaned["content"] = None if role == "assistant" and has_tool_calls else "(empty)"
    elif content == "":
        cleaned["content"] = None if role == "assistant" and has_tool_calls else "(empty)"
    return cleaned


def _parse_openai_response(
    raw: dict[str, Any],
    fallback_model: str,
    *,
    endpoint: LLMEndpoint | None = None,
) -> LLMResponse:
    """Convert the OpenAI wire response into provider-neutral LLMResponse."""

    choices = raw.get("choices") or []
    if not isinstance(choices, list) or not choices:
        raise LLMProviderError(
            "LLM response did not contain a valid choice.",
            code="INVALID_RESPONSE",
            retryable=True,
            endpoint=endpoint.name if endpoint else "",
            model=endpoint.model if endpoint else fallback_model,
        )
    first_choice = choices[0] if choices else {}
    if not isinstance(first_choice, dict) or not isinstance(first_choice.get("message"), dict):
        raise LLMProviderError(
            "LLM response did not contain a valid assistant message.",
            code="INVALID_RESPONSE",
            retryable=True,
            endpoint=endpoint.name if endpoint else "",
            model=endpoint.model if endpoint else fallback_model,
        )
    message = first_choice.get("message") or {}
    content = message.get("content") or ""
    tool_calls = message.get("tool_calls") or []
    metadata: dict[str, Any] = {
        "model": raw.get("model") or fallback_model,
        "finish_reason": first_choice.get("finish_reason"),
    }
    if "usage" in raw:
        metadata["usage"] = raw["usage"]
    if "reasoning_content" in message:
        metadata["reasoning_content"] = message["reasoning_content"]
    return LLMResponse(content=content, tool_calls=tool_calls, metadata=metadata)


def _provider_http_error(
    exc: error.HTTPError,
    endpoint: LLMEndpoint,
    api_key: str,
) -> LLMProviderError:
    """Classify an HTTP failure without retaining its potentially sensitive body."""

    del api_key  # The response body is intentionally not read or exposed.
    status = int(exc.code)
    code, retryable, message = _http_error_semantics(status)
    return LLMProviderError(
        message,
        code=code,
        http_status=status,
        retryable=retryable,
        endpoint=endpoint.name,
        model=endpoint.model,
        retry_after_seconds=_retry_after_seconds(exc.headers),
    )


def _http_error_semantics(status: int) -> tuple[str, bool, str]:
    """Map HTTP status codes to stable provider-neutral semantics."""

    if status in {401, 403}:
        return "AUTH_FAILED", False, "LLM provider authentication failed."
    if status == 404:
        return "MODEL_NOT_FOUND", False, "LLM model or endpoint was not found."
    if status == 429:
        return "RATE_LIMITED", True, "LLM provider rate limit was reached."
    if status in {408, 504}:
        return "TIMEOUT", True, "LLM request timed out."
    if 500 <= status <= 599:
        return "PROVIDER_UNAVAILABLE", True, "LLM provider is temporarily unavailable."
    return "PROVIDER_ERROR", False, f"LLM provider request failed with status {status}."


def _retry_after_seconds(headers: Any) -> float | None:
    """Parse a bounded Retry-After delta or HTTP date."""

    if headers is None:
        return None
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, min(float(raw), 300.0))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(str(raw))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, min((retry_at - datetime.now(timezone.utc)).total_seconds(), 300.0))
        except (TypeError, ValueError, OverflowError):
            return None
