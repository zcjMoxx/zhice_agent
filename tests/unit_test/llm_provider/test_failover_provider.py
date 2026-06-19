from __future__ import annotations

import pytest

from agent.llm.failover_provider import EndpointFailoverProvider
from agent.protocols.llm import LLMEndpoint, LLMProviderError, LLMResponse


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


def _endpoint(
    name: str,
    model: str,
    *,
    priority: int = 1,
    enabled: bool = True,
    api_key: str = "key",
    supported_models: tuple[str, ...] = (),
) -> LLMEndpoint:
    return LLMEndpoint(
        name=name,
        protocol="openai",
        base_url=f"https://{name}.test/v1",
        api_key=api_key,
        model=model,
        priority=priority,
        enabled=enabled,
        supported_models=supported_models,
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
