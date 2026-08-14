from __future__ import annotations

import pytest

from agent.llm.failover_provider import EndpointFailoverProvider
from agent.protocols.llm import (
    LLMEndpoint,
    LLMProviderError,
    LLMResponse,
    LLMResponseFormat,
)


def test_failover_provider_tries_preferred_endpoint_first():
    """The CLI-selected endpoint should be attempted before priority order."""

    calls: list[str] = []
    provider = EndpointFailoverProvider(
        [_endpoint("primary", "model-a", priority=1), _endpoint("backup", "model-b", priority=2)],
        preferred_endpoint="backup",
        provider_factory=_factory(calls, success={"backup": "ok"}),
    )

    response = provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "ok"
    assert calls == ["backup"]
    assert response.metadata["endpoint_name"] == "backup"
    assert response.metadata["attempted_endpoints"] == ["backup"]


def test_failover_provider_falls_back_by_priority_after_failure():
    """A failed preferred endpoint should not stop the turn when backup works."""

    calls: list[str] = []
    provider = EndpointFailoverProvider(
        [
            _endpoint("slow", "model-slow", priority=3),
            _endpoint("first", "model-first", priority=1),
            _endpoint("second", "model-second", priority=2),
        ],
        preferred_endpoint="slow",
        provider_factory=_factory(calls, success={"first": "from-first"}),
    )

    response = provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "from-first"
    assert calls == ["slow", "first"]
    assert response.metadata["attempted_endpoints"] == ["slow", "first"]


def test_failover_provider_preserves_response_format_across_endpoints():
    """Structured output must survive primary failure and backup selection."""

    calls = []
    response_format = LLMResponseFormat(
        name="travel_requirement_draft",
        schema={"type": "object", "properties": {}, "additionalProperties": False},
    )

    def factory(endpoint):
        class Provider:
            def chat(self, messages, tools=None, response_format=None):
                del messages, tools
                calls.append((endpoint.name, response_format))
                if endpoint.name == "primary":
                    raise LLMProviderError("failed")
                return LLMResponse(content="{}")

        return Provider()

    provider = EndpointFailoverProvider(
        [_endpoint("primary", "model-a"), _endpoint("backup", "model-b")],
        provider_factory=factory,
    )

    provider.chat(
        messages=[{"role": "user", "content": "hello"}],
        response_format=response_format,
    )

    assert calls == [("primary", response_format), ("backup", response_format)]


def test_failover_provider_preserves_generation_options_across_endpoints():
    """Call-scoped deterministic settings must survive endpoint failover."""

    from agent.protocols.llm import LLMGenerationOptions

    calls = []
    options = LLMGenerationOptions(temperature=0.0)

    def factory(endpoint):
        class Provider:
            def chat(self, messages, tools=None, generation_options=None):
                del messages, tools
                calls.append((endpoint.name, generation_options))
                if endpoint.name == "primary":
                    raise LLMProviderError("failed")
                return LLMResponse(content="{}")

        return Provider()

    provider = EndpointFailoverProvider(
        [_endpoint("primary", "model-a"), _endpoint("backup", "model-b")],
        provider_factory=factory,
    )

    provider.chat(
        messages=[{"role": "user", "content": "hello"}],
        generation_options=options,
    )

    assert calls == [("primary", options), ("backup", options)]


def test_failover_provider_reset_returns_to_priority_order():
    """Clearing a preference should restore priority/config order."""

    provider = EndpointFailoverProvider(
        [_endpoint("first", "model-first", priority=1), _endpoint("second", "model-second", priority=2)],
        preferred_endpoint="second",
        provider_factory=_factory([], success={"first": "ok"}),
    )

    assert provider.current_endpoint().name == "second"

    provider.reset_preferred()

    assert provider.current_endpoint().name == "first"


def test_failover_provider_skips_disabled_endpoints():
    """Disabled endpoints remain in config but are not attempted."""

    calls: list[str] = []
    provider = EndpointFailoverProvider(
        [
            _endpoint("disabled", "model-disabled", priority=1, enabled=False),
            _endpoint("enabled", "model-enabled", priority=2),
        ],
        provider_factory=_factory(calls, success={"enabled": "ok"}),
    )

    response = provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "ok"
    assert calls == ["enabled"]
    assert [endpoint.name for endpoint in provider.endpoints()] == ["enabled"]


def test_failover_provider_raises_combined_error_when_all_endpoints_fail():
    """The final error should explain which endpoints were attempted."""

    provider = EndpointFailoverProvider(
        [_endpoint("a", "model-a", api_key="secret-a"), _endpoint("b", "model-b")],
        provider_factory=_factory([], success={}),
    )

    with pytest.raises(LLMProviderError) as exc_info:
        provider.chat(messages=[{"role": "user", "content": "hello"}])

    message = str(exc_info.value)
    assert "All enabled LLM endpoints failed" in message
    assert "a (model-a)" in message
    assert "b (model-b)" in message
    assert "secret-a" not in message


def test_failover_provider_matches_endpoint_by_name_or_endpoint_model():
    """The /model command can switch by endpoint name or endpoint/model."""

    provider = EndpointFailoverProvider(
        [_endpoint("a", "model-a", supported_models=("model-a-plus",)), _endpoint("b", "model-b")],
        provider_factory=_factory([], success={"a": "ok"}),
    )

    by_name, name_error = provider.match_endpoint("b")
    by_endpoint_model, model_error = provider.match_endpoint("a/model-a-plus")
    missing, missing_error = provider.match_endpoint("missing")

    assert by_name is not None and by_name.name == "b"
    assert name_error == ""
    assert by_endpoint_model is not None and by_endpoint_model.name == "a"
    assert by_endpoint_model.model == "model-a-plus"
    assert model_error == ""
    assert missing is None
    assert "Unknown endpoint" in missing_error


def test_failover_provider_does_not_match_bare_model_names():
    """Switching by model string should stay explicit even when model names are unique."""

    provider = EndpointFailoverProvider(
        [_endpoint("a", "model-a"), _endpoint("b", "model-b")],
        provider_factory=_factory([], success={"a": "ok"}),
    )

    endpoint, error = provider.match_endpoint("model-a")

    assert endpoint is None
    assert "Unknown endpoint" in error


def test_failover_provider_rejects_unsupported_endpoint_model_override():
    """Endpoint/model switching should fail before a real LLM request when unsupported."""

    provider = EndpointFailoverProvider(
        [_endpoint("a", "model-a", supported_models=("model-a", "model-a-plus"))],
        provider_factory=_factory([], success={"a": "ok"}),
    )

    endpoint, error = provider.match_endpoint("a/model-b")

    assert endpoint is None
    assert "does not list model" in error
    assert "model-a-plus" in error


def test_failover_provider_allows_supported_model_glob_override():
    """Supported model patterns allow controlled endpoint/model overrides."""

    provider = EndpointFailoverProvider(
        [_endpoint("a", "gpt-5", supported_models=("gpt-*",))],
        provider_factory=_factory([], success={"a": "ok"}),
    )

    endpoint, error = provider.match_endpoint("a/gpt-5.1")

    assert endpoint is not None
    assert endpoint.model == "gpt-5.1"
    assert error == ""


def test_failover_provider_uses_model_override_only_for_preferred_endpoint():
    """An endpoint/model override should not leak into later failover endpoints."""

    calls: list[tuple[str, str]] = []
    provider = EndpointFailoverProvider(
        [_endpoint("a", "model-a", priority=1), _endpoint("b", "model-b", priority=2)],
        preferred_endpoint="a",
        provider_factory=_recording_factory(calls, success={"b": "ok"}),
    )

    provider.set_preferred("a", "model-a-plus")
    response = provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "ok"
    assert calls == [("a", "model-a-plus"), ("b", "model-b")]


def test_failover_provider_uses_smallest_enabled_endpoint_input_budget():
    """A request prepared for the preferred endpoint must remain safe after failover."""

    provider = EndpointFailoverProvider(
        [
            _endpoint(
                "large",
                "model-large",
                context_window=32768,
                max_tokens=4096,
            ),
            _endpoint(
                "small",
                "model-small",
                context_window=16384,
                max_tokens=2048,
            ),
        ],
        preferred_endpoint="large",
        provider_factory=_factory([], success={"large": "ok"}),
    )

    response = provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert provider.context_budget.input_token_limit == 14336
    assert provider.context_budget.endpoint_names == ("large", "small")
    assert response.metadata["input_token_limit"] == 14336


def test_failover_provider_retries_retryable_error_and_records_attempts():
    """Transient failures should retry the same endpoint with bounded evidence."""

    calls: list[str] = []
    clock = _FakeClock()
    provider = EndpointFailoverProvider(
        [_endpoint("primary", "model", max_attempts=2, retry_backoff_seconds=0.5)],
        provider_factory=_sequenced_factory(
            calls,
            [
                LLMProviderError("temporary", code="NETWORK_ERROR", retryable=True),
                LLMResponse(content="ok"),
            ],
        ),
        clock=clock,
        sleep=clock.sleep,
        random_source=lambda: 0.5,
    )

    response = provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert calls == ["primary", "primary"]
    assert clock.now == 0.5
    assert [item["result"] for item in response.metadata["provider_attempts"]] == [
        "error",
        "success",
    ]
    assert response.metadata["provider_attempts"][0]["error_code"] == "NETWORK_ERROR"
    assert response.metadata["provider_attempts"][0]["backoff_ms"] == 500


def test_failover_provider_does_not_retry_non_retryable_error():
    """Authentication failures should fail over immediately instead of retrying in place."""

    calls: list[str] = []
    provider = EndpointFailoverProvider(
        [
            _endpoint("primary", "model-a", max_attempts=3),
            _endpoint("backup", "model-b"),
        ],
        provider_factory=_per_endpoint_factory(
            calls,
            errors={
                "primary": LLMProviderError(
                    "auth failed", code="AUTH_FAILED", http_status=401, retryable=False
                )
            },
            success={"backup": "ok"},
        ),
    )

    response = provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "ok"
    assert calls == ["primary", "backup"]
    assert response.metadata["provider_attempts"][0]["http_status"] == 401


def test_failover_provider_honors_retry_after():
    """A valid Retry-After value should take precedence over exponential backoff."""

    clock = _FakeClock()
    provider = EndpointFailoverProvider(
        [_endpoint("primary", "model", max_attempts=2)],
        provider_factory=_sequenced_factory(
            [],
            [
                LLMProviderError(
                    "limited",
                    code="RATE_LIMITED",
                    retryable=True,
                    retry_after_seconds=3,
                ),
                LLMResponse(content="ok"),
            ],
        ),
        clock=clock,
        sleep=clock.sleep,
    )

    response = provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert clock.now == 3
    assert response.metadata["provider_attempts"][0]["backoff_ms"] == 3000


def test_failover_provider_retries_invalid_response_at_most_once():
    """Malformed responses should not consume an arbitrarily high configured attempt count."""

    calls: list[str] = []
    clock = _FakeClock()
    provider = EndpointFailoverProvider(
        [_endpoint("primary", "model", max_attempts=5)],
        provider_factory=_sequenced_factory(
            calls,
            [
                LLMProviderError("invalid", code="INVALID_RESPONSE", retryable=True),
                LLMProviderError("invalid", code="INVALID_RESPONSE", retryable=True),
                LLMResponse(content="should-not-run"),
            ],
        ),
        clock=clock,
        sleep=clock.sleep,
        random_source=lambda: 0.5,
    )

    with pytest.raises(LLMProviderError) as exc_info:
        provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert calls == ["primary", "primary"]
    assert len(exc_info.value.attempts) == 2


def test_failover_provider_total_deadline_stops_new_endpoint_attempts():
    """No new endpoint request should start after the shared deadline expires."""

    calls: list[str] = []
    clock = _FakeClock()

    def factory(endpoint):
        return _ClockAdvancingFailure(endpoint, calls, clock)

    provider = EndpointFailoverProvider(
        [
            _endpoint("primary", "model-a", max_attempts=1),
            _endpoint("backup", "model-b", max_attempts=1),
        ],
        provider_factory=factory,
        clock=clock,
        sleep=clock.sleep,
        total_deadline_seconds=1,
    )

    with pytest.raises(LLMProviderError) as exc_info:
        provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert calls == ["primary"]
    assert exc_info.value.attempts[-1]["skip_reason"] == "total_deadline_exceeded"


def test_failover_provider_skips_endpoint_during_cooldown():
    """An exhausted transient endpoint should be skipped until its cooldown ends."""

    calls: list[str] = []
    clock = _FakeClock()
    provider = EndpointFailoverProvider(
        [
            _endpoint("primary", "model-a", max_attempts=1, cooldown_seconds=10),
            _endpoint("backup", "model-b", max_attempts=1),
        ],
        provider_factory=_per_endpoint_factory(
            calls,
            errors={
                "primary": LLMProviderError(
                    "offline", code="NETWORK_ERROR", retryable=True
                )
            },
            success={"backup": "ok"},
        ),
        clock=clock,
        sleep=clock.sleep,
    )

    provider.chat(messages=[{"role": "user", "content": "first"}])
    second = provider.chat(messages=[{"role": "user", "content": "second"}])

    assert calls == ["primary", "backup", "backup"]
    assert second.metadata["provider_attempts"][0]["skip_reason"] == "cooldown"


def _endpoint(
    name: str,
    model: str,
    *,
    priority: int = 1,
    enabled: bool = True,
    api_key: str = "key",
    supported_models: tuple[str, ...] = (),
    context_window: int = 32768,
    max_tokens: int = 4096,
    max_attempts: int = 2,
    retry_backoff_seconds: float = 0.5,
    cooldown_seconds: float = 30.0,
) -> LLMEndpoint:
    return LLMEndpoint(
        name=name,
        protocol="openai",
        base_url=f"https://{name}.test/v1",
        api_key=api_key,
        model=model,
        context_window=context_window,
        max_tokens=max_tokens,
        priority=priority,
        enabled=enabled,
        supported_models=supported_models,
        max_attempts=max_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
        cooldown_seconds=cooldown_seconds,
    )


def _factory(calls: list[str], *, success: dict[str, str]):
    def create(endpoint: LLMEndpoint):
        return _FakeProvider(endpoint, calls, success)

    return create


def _recording_factory(calls: list[tuple[str, str]], *, success: dict[str, str]):
    def create(endpoint: LLMEndpoint):
        return _RecordingProvider(endpoint, calls, success)

    return create


class _FakeProvider:
    def __init__(self, endpoint: LLMEndpoint, calls: list[str], success: dict[str, str]):
        self.endpoint = endpoint
        self.calls = calls
        self.success = success

    def chat(self, messages, tools=None):
        self.calls.append(self.endpoint.name)
        if self.endpoint.name not in self.success:
            raise LLMProviderError(f"{self.endpoint.api_key} failed")
        return LLMResponse(content=self.success[self.endpoint.name])


class _RecordingProvider:
    def __init__(self, endpoint: LLMEndpoint, calls: list[tuple[str, str]], success: dict[str, str]):
        self.endpoint = endpoint
        self.calls = calls
        self.success = success

    def chat(self, messages, tools=None):
        self.calls.append((self.endpoint.name, self.endpoint.model))
        if self.endpoint.name not in self.success:
            raise LLMProviderError(f"{self.endpoint.api_key} failed")
        return LLMResponse(content=self.success[self.endpoint.name])


def _sequenced_factory(calls, outcomes):
    remaining = list(outcomes)

    def create(endpoint):
        class Provider:
            def chat(self, messages, tools=None):
                calls.append(endpoint.name)
                outcome = remaining.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        return Provider()

    return create


def _per_endpoint_factory(calls, *, errors, success):
    def create(endpoint):
        class Provider:
            def chat(self, messages, tools=None):
                calls.append(endpoint.name)
                if endpoint.name in errors:
                    raise errors[endpoint.name]
                return LLMResponse(content=success[endpoint.name])

        return Provider()

    return create


class _FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _ClockAdvancingFailure:
    def __init__(self, endpoint, calls, clock):
        self.endpoint = endpoint
        self.calls = calls
        self.clock = clock

    def chat(self, messages, tools=None):
        self.calls.append(self.endpoint.name)
        self.clock.now += 2
        raise LLMProviderError("timeout", code="TIMEOUT", retryable=True)
