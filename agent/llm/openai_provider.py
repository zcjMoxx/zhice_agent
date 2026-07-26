"""OpenAI-compatible chat completions provider."""

from __future__ import annotations

import json
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
        self._timeout = timeout

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
        return _parse_openai_response(raw, self.endpoint.model)

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
            raise _provider_http_error(exc, api_key) from exc
        except error.URLError as exc:
            raise LLMProviderError(f"LLM HTTP request failed: {_safe_reason(exc)}") from exc

        try:
            return json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise LLMProviderError("LLM response was not valid JSON") from exc


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


def _parse_openai_response(raw: dict[str, Any], fallback_model: str) -> LLMResponse:
    """Convert the OpenAI wire response into provider-neutral LLMResponse."""

    choices = raw.get("choices") or []
    first_choice = choices[0] if choices else {}
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


def _provider_http_error(exc: error.HTTPError, api_key: str) -> LLMProviderError:
    """Build a redacted provider error from an HTTPError body."""

    body = ""
    try:
        body = exc.read().decode("utf-8", errors="replace")[:1000]
    except Exception:
        body = ""
    body = body.replace(api_key, "[redacted]") if api_key else body
    detail = f": {body}" if body else ""
    return LLMProviderError(f"LLM HTTP request failed with status {exc.code}{detail}")


def _safe_reason(exc: error.URLError) -> str:
    """Return a bounded network-error reason for user-facing messages."""

    return str(getattr(exc, "reason", exc))[:500]
