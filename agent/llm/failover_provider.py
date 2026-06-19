"""LLM provider wrapper that tries multiple endpoints in priority order."""

from __future__ import annotations

import fnmatch
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from agent.protocols.llm import (
    LLMConfigurationError,
    LLMEndpoint,
    LLMProvider,
    LLMProviderError,
    LLMResponse,
)

ProviderFactory = Callable[[LLMEndpoint], LLMProvider]


class EndpointFailoverProvider:
    """Expose one LLMProvider facade over multiple configured endpoints.

    The AgentLoop still calls a single ``chat()`` method. This wrapper decides
    which endpoint to try first, supports the local ``/model`` command, and
    falls back to other enabled endpoints by priority if a provider call fails.
    """

    def __init__(
        self,
        endpoints: list[LLMEndpoint],
        *,
        preferred_endpoint: str | None = None,
        provider_factory: ProviderFactory | None = None,
    ):
        """Keep only enabled endpoints and prepare name/order lookup tables."""

        self._endpoints = [endpoint for endpoint in endpoints if endpoint.enabled]
        if not self._endpoints:
            raise LLMConfigurationError("No enabled LLM endpoints are configured.")

        self._index = {endpoint.name: index for index, endpoint in enumerate(self._endpoints)}
        if len(self._index) != len(self._endpoints):
            raise LLMConfigurationError("LLM endpoint names must be unique.")

        self._provider_factory = provider_factory or _create_endpoint_provider
        self._preferred_endpoint = ""
        self._preferred_model = ""
        if preferred_endpoint:
            self.set_preferred(preferred_endpoint)

    @property
    def preferred_endpoint(self) -> str:
        """Return explicit preference, or the first endpoint by priority."""

        return self._preferred_endpoint or self._ordered_endpoints()[0].name

    def endpoints(self) -> list[LLMEndpoint]:
        """Return enabled endpoints in original configuration order."""

        return list(self._endpoints)

    def current_endpoint(self) -> LLMEndpoint:
        """Return the effective endpoint that will be tried first."""

        return self._effective_endpoint(self._endpoint_by_name(self.preferred_endpoint))

    def set_preferred(self, endpoint_name: str, model: str | None = None) -> None:
        """Set the endpoint, and optionally a model override, tried before failover.

        ``model`` is only a temporary override for this endpoint. It is not
        copied onto fallback endpoints because they may use different providers.
        """

        if endpoint_name not in self._index:
            raise LLMConfigurationError(f"LLM endpoint is not configured or enabled: {endpoint_name}")
        self._preferred_endpoint = endpoint_name
        self._preferred_model = (model or "").strip()

    def reset_preferred(self) -> None:
        """Clear the explicit preferred endpoint and return to priority order."""

        self._preferred_endpoint = ""
        self._preferred_model = ""

    def match_endpoint(self, target: str) -> tuple[LLMEndpoint | None, str]:
        """Resolve a /model target such as ``claude`` or ``claude/opus``.

        Text before ``/`` is always the endpoint name. Text after ``/`` is a
        model override that must be allowed by that endpoint's supported_models.
        """

        normalized = target.strip()
        if not normalized:
            return None, "Endpoint name is required."

        endpoint_name, separator, model = normalized.partition("/")
        endpoint_name = endpoint_name.strip()
        model = model.strip()
        if not endpoint_name:
            return None, "Endpoint name is required."
        if separator and not model:
            return None, "Model name is required after endpoint/."

        endpoint = self._index.get(endpoint_name)
        if endpoint is None:
            return None, f"Unknown endpoint: {endpoint_name}"
        selected = self._endpoints[endpoint]
        if model:
            if not _endpoint_supports_model(selected, model):
                supported = _format_supported_models(selected)
                return (
                    None,
                    f"Endpoint {selected.name!r} does not list model {model!r} as supported. "
                    f"Supported models: {supported}.",
                )
            return replace(selected, model=model), ""
        return selected, ""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Try endpoints in order until one provider returns a response."""

        attempts: list[tuple[LLMEndpoint, Exception]] = []
        attempted_names: list[str] = []

        for endpoint in self._ordered_endpoints():
            attempted_names.append(endpoint.name)
            try:
                response = self._provider_factory(endpoint).chat(messages=messages, tools=tools)
            except Exception as exc:  # noqa: BLE001 - failover should try the next endpoint.
                attempts.append((endpoint, exc))
                continue

            metadata = dict(response.metadata)
            metadata["endpoint_name"] = endpoint.name
            metadata["model"] = endpoint.model
            metadata["attempted_endpoints"] = attempted_names
            return LLMResponse(
                content=response.content,
                tool_calls=list(response.tool_calls),
                metadata=metadata,
            )

        raise LLMProviderError(_format_failover_error(attempts))

    def _ordered_endpoints(self) -> list[LLMEndpoint]:
        """Return call order: preferred first, then enabled endpoints by priority."""

        ordered = sorted(self._endpoints, key=lambda ep: (ep.priority, self._index[ep.name]))
        if self._preferred_endpoint:
            preferred = self._endpoint_by_name(self._preferred_endpoint)
            ordered = [self._effective_endpoint(preferred)] + [
                endpoint for endpoint in ordered if endpoint.name != preferred.name
            ]
        return ordered

    def _endpoint_by_name(self, name: str) -> LLMEndpoint:
        """Find an enabled endpoint by its resolved endpoint.name."""

        for endpoint in self._endpoints:
            if endpoint.name == name:
                return endpoint
        raise LLMConfigurationError(f"LLM endpoint is not configured or enabled: {name}")

    def _effective_endpoint(self, endpoint: LLMEndpoint) -> LLMEndpoint:
        """Apply the temporary /model override to the preferred endpoint only."""

        if self._preferred_endpoint == endpoint.name and self._preferred_model:
            return replace(endpoint, model=self._preferred_model)
        return endpoint


def _create_endpoint_provider(endpoint: LLMEndpoint) -> LLMProvider:
    """Create the concrete provider adapter for one endpoint protocol."""

    if endpoint.protocol == "openai":
        from agent.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(endpoint)
    if endpoint.protocol == "litellm":
        from agent.llm.litellm_provider import LiteLLMProvider

        return LiteLLMProvider(endpoint)
    raise LLMConfigurationError(f"Unsupported LLM protocol: {endpoint.protocol}")


def _endpoint_supports_model(endpoint: LLMEndpoint, model: str) -> bool:
    """Check whether a /model override is allowed for this endpoint."""

    if model == endpoint.model:
        return True
    for pattern in endpoint.supported_models:
        if model == pattern or fnmatch.fnmatchcase(model, pattern):
            return True
    return False


def _format_supported_models(endpoint: LLMEndpoint) -> str:
    """Format model allowlist for user-facing error messages."""

    supported = [endpoint.model]
    for model in endpoint.supported_models:
        if model not in supported:
            supported.append(model)
    return ", ".join(supported)


def _format_failover_error(attempts: list[tuple[LLMEndpoint, Exception]]) -> str:
    """Build one safe error message after every endpoint attempt fails."""

    if not attempts:
        return "LLM provider request failed: no endpoints were attempted."

    lines = ["All enabled LLM endpoints failed."]
    for endpoint, exc in attempts:
        lines.append(
            f"- {endpoint.name} ({endpoint.model}): "
            f"{type(exc).__name__}: {_safe_error_message(str(exc), endpoint)}"
        )
    return "\n".join(lines)


def _safe_error_message(message: str, endpoint: LLMEndpoint) -> str:
    """Redact endpoint secrets before including provider errors in CLI output."""

    safe = message or "unknown provider error"
    if endpoint.api_key:
        safe = safe.replace(endpoint.api_key, "[redacted]")
    return safe[:300]
