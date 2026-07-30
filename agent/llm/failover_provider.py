"""LLM provider wrapper that tries multiple endpoints in priority order."""

from __future__ import annotations

import fnmatch
import random
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from agent.protocols.llm import (
    ContextBudget,
    LLMConfigurationError,
    LLMEndpoint,
    LLMProvider,
    LLMProviderError,
    LLMResponse,
    effective_input_token_limit,
)

ProviderFactory = Callable[[LLMEndpoint], LLMProvider]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]
RandomSource = Callable[[], float]


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
        clock: Clock = time.monotonic,
        sleep: Sleeper = time.sleep,
        random_source: RandomSource = random.random,
        total_deadline_seconds: float | None = None,
    ):
        """Keep only enabled endpoints and prepare name/order lookup tables."""

        self._endpoints = [endpoint for endpoint in endpoints if endpoint.enabled]
        if not self._endpoints:
            raise LLMConfigurationError("No enabled LLM endpoints are configured.")

        self._index = {endpoint.name: index for index, endpoint in enumerate(self._endpoints)}
        if len(self._index) != len(self._endpoints):
            raise LLMConfigurationError("LLM endpoint names must be unique.")

        self._provider_factory = provider_factory or _create_endpoint_provider
        self._clock = clock
        self._sleep = sleep
        self._random_source = random_source
        configured_deadline = min(endpoint.total_deadline_seconds for endpoint in self._endpoints)
        self._total_deadline_seconds = (
            configured_deadline if total_deadline_seconds is None else total_deadline_seconds
        )
        if self._total_deadline_seconds <= 0:
            raise LLMConfigurationError("LLM total deadline must be greater than zero.")
        self._cooldowns: dict[str, float] = {}
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

    @property
    def context_budget(self) -> ContextBudget:
        """Return the input budget safe for every enabled failover endpoint."""

        return ContextBudget(
            input_token_limit=min(
                effective_input_token_limit(endpoint) for endpoint in self._endpoints
            ),
            endpoint_names=tuple(endpoint.name for endpoint in self._endpoints),
        )

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
        """Retry transient failures, then fail over within one total deadline."""

        evidence: list[dict[str, Any]] = []
        failures: list[tuple[LLMEndpoint, LLMProviderError]] = []
        attempted_names: list[str] = []
        call_started = self._clock()
        deadline = call_started + self._total_deadline_seconds

        for endpoint in self._ordered_endpoints():
            now = self._clock()
            cooldown_until = self._cooldowns.get(endpoint.name, 0.0)
            if cooldown_until > now:
                evidence.append(
                    _skip_evidence(
                        endpoint,
                        reason="cooldown",
                        cooldown_remaining=cooldown_until - now,
                    )
                )
                continue
            self._cooldowns.pop(endpoint.name, None)

            if now >= deadline:
                evidence.append(_skip_evidence(endpoint, reason="total_deadline_exceeded"))
                break

            attempted_names.append(endpoint.name)
            for attempt_index in range(1, endpoint.max_attempts + 1):
                remaining = deadline - self._clock()
                if remaining <= 0:
                    evidence.append(_skip_evidence(endpoint, reason="total_deadline_exceeded"))
                    break
                request_endpoint = replace(
                    endpoint,
                    request_timeout_seconds=min(endpoint.request_timeout_seconds, remaining),
                )
                attempt_started = self._clock()
                started_at = _utc_now()
                try:
                    response = self._provider_factory(request_endpoint).chat(
                        messages=messages,
                        tools=tools,
                    )
                except Exception as exc:  # noqa: BLE001 - provider boundary normalization.
                    error = _normalize_provider_error(exc, endpoint)
                    failures.append((endpoint, error))
                    attempt = _failure_evidence(
                        endpoint,
                        attempt_index=attempt_index,
                        started_at=started_at,
                        duration_ms=(self._clock() - attempt_started) * 1000,
                        error=error,
                    )
                    evidence.append(attempt)

                    invalid_response_retry_available = (
                        error.code != "INVALID_RESPONSE" or attempt_index < 2
                    )
                    can_retry = (
                        error.retryable
                        and attempt_index < endpoint.max_attempts
                        and invalid_response_retry_available
                    )
                    if can_retry:
                        backoff = _retry_delay(
                            endpoint,
                            attempt_index,
                            error.retry_after_seconds,
                            self._random_source(),
                        )
                        remaining = deadline - self._clock()
                        if backoff >= remaining:
                            attempt["skip_reason"] = "total_deadline_exceeded"
                            break
                        attempt["backoff_ms"] = round(backoff * 1000, 3)
                        self._sleep(backoff)
                        continue

                    if error.retryable and endpoint.cooldown_seconds > 0:
                        cooldown_deadline = self._clock() + endpoint.cooldown_seconds
                        self._cooldowns[endpoint.name] = cooldown_deadline
                        attempt["cooldown_until"] = (
                            datetime.now(timezone.utc)
                            + timedelta(seconds=endpoint.cooldown_seconds)
                        ).isoformat()
                    break
                else:
                    evidence.append(
                        _success_evidence(
                            endpoint,
                            attempt_index=attempt_index,
                            started_at=started_at,
                            duration_ms=(self._clock() - attempt_started) * 1000,
                        )
                    )

                    metadata = dict(response.metadata)
                    metadata["endpoint_name"] = endpoint.name
                    metadata["model"] = endpoint.model
                    metadata["attempted_endpoints"] = attempted_names
                    metadata["provider_attempts"] = evidence
                    metadata["input_token_limit"] = self.context_budget.input_token_limit
                    metadata["input_price_per_million"] = endpoint.input_price_per_million
                    metadata["output_price_per_million"] = endpoint.output_price_per_million
                    return LLMResponse(
                        content=response.content,
                        tool_calls=list(response.tool_calls),
                        metadata=metadata,
                    )

        last_error = failures[-1][1] if failures else None
        raise LLMProviderError(
            _format_failover_error(failures, evidence),
            code=last_error.code if last_error else "PROVIDER_UNAVAILABLE",
            http_status=last_error.http_status if last_error else None,
            retryable=any(error.retryable for _, error in failures),
            endpoint=last_error.endpoint if last_error else "",
            model=last_error.model if last_error else "",
            attempts=evidence,
        )

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


def _format_failover_error(
    failures: list[tuple[LLMEndpoint, LLMProviderError]],
    evidence: list[dict[str, Any]],
) -> str:
    """Build one safe error message after every endpoint attempt fails."""

    if not failures:
        if evidence:
            return "LLM provider request failed: all endpoints were skipped."
        return "LLM provider request failed: no endpoints were attempted."

    lines = ["All enabled LLM endpoints failed."]
    seen: set[str] = set()
    for endpoint, exc in failures:
        if endpoint.name in seen:
            continue
        seen.add(endpoint.name)
        lines.append(
            f"- {endpoint.name} ({endpoint.model}): {exc.code}: "
            f"{_safe_error_message(exc.safe_message, endpoint)}"
        )
    return "\n".join(lines)


def _safe_error_message(message: str, endpoint: LLMEndpoint) -> str:
    """Redact endpoint secrets before including provider errors in CLI output."""

    safe = message or "unknown provider error"
    if endpoint.api_key:
        safe = safe.replace(endpoint.api_key, "[redacted]")
    return safe[:300]


def _normalize_provider_error(exc: Exception, endpoint: LLMEndpoint) -> LLMProviderError:
    """Attach endpoint identity and redact legacy/unstructured provider failures."""

    if isinstance(exc, LLMProviderError):
        return LLMProviderError(
            _safe_error_message(exc.safe_message, endpoint),
            code=exc.code,
            http_status=exc.http_status,
            retryable=exc.retryable,
            endpoint=exc.endpoint or endpoint.name,
            model=exc.model or endpoint.model,
            attempts=exc.attempts,
            retry_after_seconds=exc.retry_after_seconds,
        )
    return LLMProviderError(
        "LLM provider request failed.",
        endpoint=endpoint.name,
        model=endpoint.model,
    )


def _retry_delay(
    endpoint: LLMEndpoint,
    attempt_index: int,
    retry_after_seconds: float | None,
    random_value: float,
) -> float:
    """Return Retry-After or bounded exponential backoff with light jitter."""

    if retry_after_seconds is not None:
        return max(0.0, retry_after_seconds)
    base = min(
        endpoint.retry_backoff_seconds * (2 ** (attempt_index - 1)),
        endpoint.retry_backoff_max_seconds,
    )
    jitter = base * endpoint.retry_jitter_ratio * ((2 * min(max(random_value, 0.0), 1.0)) - 1)
    return max(0.0, base + jitter)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_evidence(endpoint: LLMEndpoint, *, attempt_index: int, result: str) -> dict[str, Any]:
    return {
        "endpoint_name": endpoint.name,
        "model": endpoint.model,
        "attempt_index": attempt_index,
        "started_at": None,
        "duration_ms": 0.0,
        "result": result,
        "error_code": None,
        "http_status": None,
        "retryable": False,
        "backoff_ms": 0.0,
        "skip_reason": None,
        "cooldown_until": None,
    }


def _success_evidence(
    endpoint: LLMEndpoint,
    *,
    attempt_index: int,
    started_at: str,
    duration_ms: float,
) -> dict[str, Any]:
    item = _base_evidence(endpoint, attempt_index=attempt_index, result="success")
    item["started_at"] = started_at
    item["duration_ms"] = round(max(duration_ms, 0.0), 3)
    return item


def _failure_evidence(
    endpoint: LLMEndpoint,
    *,
    attempt_index: int,
    started_at: str,
    duration_ms: float,
    error: LLMProviderError,
) -> dict[str, Any]:
    item = _base_evidence(endpoint, attempt_index=attempt_index, result="error")
    item.update(
        {
            "started_at": started_at,
            "duration_ms": round(max(duration_ms, 0.0), 3),
            "error_code": error.code,
            "http_status": error.http_status,
            "retryable": error.retryable,
        }
    )
    return item


def _skip_evidence(
    endpoint: LLMEndpoint,
    *,
    reason: str,
    cooldown_remaining: float | None = None,
) -> dict[str, Any]:
    item = _base_evidence(endpoint, attempt_index=0, result="skipped")
    item["skip_reason"] = reason
    if cooldown_remaining is not None:
        item["cooldown_until"] = (
            datetime.now(timezone.utc) + timedelta(seconds=cooldown_remaining)
        ).isoformat()
    return item
