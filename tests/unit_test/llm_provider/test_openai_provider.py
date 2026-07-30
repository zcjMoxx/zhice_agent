"""Tests for the OpenAI-compatible LLM provider."""

import json
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest


def test_openai_provider_sends_chat_completion_request(monkeypatch):
    """The provider should adapt protocol-neutral messages to OpenAI chat format."""

    from agent.llm.openai_provider import OpenAIProvider

    recorder = UrlopenRecorder(
        {
            "choices": [
                {
                    "message": {
                        "content": "hello",
                        "reasoning_content": "brief reasoning",
                        "tool_calls": [{"id": "call_1", "type": "function"}],
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }
    )

    response = OpenAIProvider(_endpoint(api_key="secret-openai"), urlopen=recorder).chat(
        messages=[
            {"role": "system", "content": "system", "extra": "drop"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1"}]},
            {"role": "user", "content": None},
        ],
        tools=None,
    )

    assert recorder.urls == ["https://example.test/v1/chat/completions"]
    request_body = json.loads(recorder.bodies[0])
    assert request_body["model"] == "fake-openai"
    assert request_body["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1"}]},
        {"role": "user", "content": "(empty)"},
    ]
    assert _headers_lower(recorder.headers[0])["authorization"] == "Bearer secret-openai"
    assert response.content == "hello"
    assert response.tool_calls == [{"id": "call_1", "type": "function"}]
    assert response.metadata["finish_reason"] == "stop"
    assert response.metadata["usage"] == {"prompt_tokens": 3, "completion_tokens": 2}
    assert response.metadata["reasoning_content"] == "brief reasoning"


def test_openai_provider_requires_api_key(monkeypatch):
    """Missing API keys should fail before any HTTP request is attempted."""

    from agent.llm.openai_provider import OpenAIProvider

    with pytest.raises(Exception, match="models.json"):
        OpenAIProvider(_endpoint()).chat(messages=[{"role": "user", "content": "hello"}])


def test_openai_provider_uses_local_json_api_key(monkeypatch):
    """Local workspace endpoint JSON may provide an API key for development."""

    from agent.llm.openai_provider import OpenAIProvider

    recorder = UrlopenRecorder({"choices": [{"message": {"content": "ok"}}]})

    OpenAIProvider(_endpoint(api_key="json-secret"), urlopen=recorder).chat(
        messages=[{"role": "user", "content": "hello"}]
    )

    assert _headers_lower(recorder.headers[0])["authorization"] == "Bearer json-secret"


def test_openai_provider_sends_tools_when_non_empty():
    """OpenAI-compatible tools should be included only when provided."""

    from agent.llm.openai_provider import OpenAIProvider

    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]
    recorder = UrlopenRecorder({"choices": [{"message": {"content": "ok"}}]})

    OpenAIProvider(_endpoint(api_key="json-secret"), urlopen=recorder).chat(
        messages=[{"role": "user", "content": "hello"}],
        tools=tools,
    )

    request_body = json.loads(recorder.bodies[0])
    assert request_body["tools"] == tools


def test_openai_provider_omits_tools_when_empty():
    """Empty tool lists should not be sent to the provider."""

    from agent.llm.openai_provider import OpenAIProvider

    recorder = UrlopenRecorder({"choices": [{"message": {"content": "ok"}}]})

    OpenAIProvider(_endpoint(api_key="json-secret"), urlopen=recorder).chat(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
    )

    request_body = json.loads(recorder.bodies[0])
    assert "tools" not in request_body


def test_openai_provider_preserves_multiple_tool_calls_order():
    """Provider parsing should keep tool call order stable."""

    from agent.llm.openai_provider import OpenAIProvider

    tool_calls = [
        {"id": "call_1", "type": "function"},
        {"id": "call_2", "type": "function"},
    ]
    recorder = UrlopenRecorder({"choices": [{"message": {"content": "", "tool_calls": tool_calls}}]})

    response = OpenAIProvider(_endpoint(api_key="json-secret"), urlopen=recorder).chat(
        messages=[{"role": "user", "content": "hello"}]
    )

    assert response.tool_calls == tool_calls


def test_openai_provider_http_error_does_not_leak_secret(monkeypatch):
    """HTTP failures should be converted to a safe provider error."""

    from agent.llm.openai_provider import OpenAIProvider

    with pytest.raises(Exception) as exc_info:
        OpenAIProvider(_endpoint(api_key="secret-openai"), urlopen=RaisingUrlopen()).chat(
            messages=[{"role": "user", "content": "hello"}]
        )

    assert "secret-openai" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, "AUTH_FAILED", False),
        (403, "AUTH_FAILED", False),
        (404, "MODEL_NOT_FOUND", False),
        (429, "RATE_LIMITED", True),
        (500, "PROVIDER_UNAVAILABLE", True),
        (504, "TIMEOUT", True),
    ],
)
def test_openai_provider_classifies_http_errors(status, code, retryable):
    """HTTP failures should expose stable safe semantics to the retry wrapper."""

    from agent.llm.openai_provider import OpenAIProvider
    from agent.protocols.llm import LLMProviderError

    with pytest.raises(LLMProviderError) as exc_info:
        OpenAIProvider(
            _endpoint(api_key="secret-openai"),
            urlopen=StatusUrlopen(status, headers={"Retry-After": "2"}),
        ).chat(messages=[{"role": "user", "content": "hello"}])

    assert exc_info.value.code == code
    assert exc_info.value.http_status == status
    assert exc_info.value.retryable is retryable
    assert exc_info.value.endpoint == "default"
    assert exc_info.value.model == "fake-openai"
    assert "secret-openai" not in exc_info.value.safe_message
    if status == 429:
        assert exc_info.value.retry_after_seconds == 2


def test_openai_provider_classifies_network_and_invalid_response_errors():
    """Transport and malformed JSON failures should remain distinguishable."""

    from agent.llm.openai_provider import OpenAIProvider
    from agent.protocols.llm import LLMProviderError

    with pytest.raises(LLMProviderError) as network_error:
        OpenAIProvider(
            _endpoint(api_key="key"),
            urlopen=lambda request, timeout=None: (_ for _ in ()).throw(URLError("offline")),
        ).chat(messages=[{"role": "user", "content": "hello"}])
    with pytest.raises(LLMProviderError) as invalid_error:
        OpenAIProvider(
            _endpoint(api_key="key"),
            urlopen=lambda request, timeout=None: RawResponse(b"not-json"),
        ).chat(messages=[{"role": "user", "content": "hello"}])

    assert network_error.value.code == "NETWORK_ERROR"
    assert network_error.value.retryable is True
    assert invalid_error.value.code == "INVALID_RESPONSE"
    assert invalid_error.value.retryable is True


def test_litellm_provider_classifies_sdk_status_and_redacts_secret():
    """LiteLLM adapters should normalize upstream SDK errors without leaking keys."""

    from agent.llm.litellm_provider import LiteLLMProvider
    from agent.protocols.llm import LLMProviderError

    sdk_error = FakeSdkError("rate limited litellm-secret", status_code=429, retry_after=3)
    with pytest.raises(LLMProviderError) as exc_info:
        LiteLLMProvider(
            _endpoint_for_litellm(),
            completion=lambda **kwargs: (_ for _ in ()).throw(sdk_error),
        ).chat(messages=[{"role": "user", "content": "hello"}])

    assert exc_info.value.code == "RATE_LIMITED"
    assert exc_info.value.retryable is True
    assert exc_info.value.retry_after_seconds == 3
    assert "litellm-secret" not in str(exc_info.value)


def test_create_llm_provider_creates_litellm_provider():
    """LiteLLM endpoints should use the in-process LiteLLMProvider adapter."""

    from agent.llm import create_llm_provider
    from agent.llm.litellm_provider import LiteLLMProvider
    from agent.protocols.llm import LLMEndpoint

    endpoint = LLMEndpoint(
        name="claude",
        protocol="litellm",
        base_url="",
        api_key="dummy",
        provider="anthropic",
        model="claude-sonnet-4",
        context_window=32768,
    )

    provider = create_llm_provider(endpoint)

    assert isinstance(provider, LiteLLMProvider)


def test_litellm_provider_calls_in_process_sdk_completion():
    """LiteLLMProvider should call the LiteLLM SDK instead of an HTTP proxy."""

    from agent.llm.litellm_provider import LiteLLMProvider
    from agent.protocols.llm import LLMEndpoint

    tools = [{"type": "function", "function": {"name": "exec", "parameters": {}}}]
    recorder = CompletionRecorder(
        {
            "model": "anthropic/claude-sonnet-4",
            "choices": [
                {
                    "message": {
                        "content": "ok",
                        "tool_calls": [{"id": "call_1", "type": "function"}],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }
    )

    endpoint = LLMEndpoint(
        name="claude",
        protocol="litellm",
        base_url="",
        api_key="litellm-secret",
        provider="anthropic",
        model="claude-sonnet-4",
        context_window=32768,
        max_tokens=256,
        temperature=0.1,
    )

    response = LiteLLMProvider(endpoint, completion=recorder).chat(
        messages=[{"role": "user", "content": "run"}],
        tools=tools,
    )

    request_body = recorder.calls[0]
    assert request_body["model"] == "anthropic/claude-sonnet-4"
    assert request_body["api_key"] == "litellm-secret"
    assert "api_base" not in request_body
    assert request_body["tools"] == tools
    assert request_body["tool_choice"] == "auto"
    assert request_body["max_tokens"] == 256
    assert request_body["temperature"] == 0.1
    assert request_body["num_retries"] == 0
    assert response.content == "ok"
    assert response.tool_calls == [{"id": "call_1", "type": "function"}]
    assert response.metadata["finish_reason"] == "tool_calls"
    assert response.metadata["usage"] == {"prompt_tokens": 5, "completion_tokens": 3}


def test_litellm_provider_passes_custom_api_base_when_configured():
    """Custom LiteLLM gateways may still be configured through base_url."""

    from agent.llm.litellm_provider import LiteLLMProvider
    from agent.protocols.llm import LLMEndpoint

    recorder = CompletionRecorder({"choices": [{"message": {"content": "ok"}}]})
    endpoint = LLMEndpoint(
        name="custom",
        protocol="litellm",
        base_url="https://gateway.test/v1",
        api_key="gateway-secret",
        provider="openai",
        model="custom-model",
        context_window=32768,
    )

    LiteLLMProvider(endpoint, completion=recorder).chat(
        messages=[{"role": "user", "content": "hello"}]
    )

    assert recorder.calls[0]["api_base"] == "https://gateway.test/v1"


def _endpoint(api_key=""):
    from agent.protocols.llm import LLMEndpoint

    return LLMEndpoint(
        name="default",
        protocol="openai",
        base_url="https://example.test/v1",
        api_key=api_key,
        model="fake-openai",
        context_window=32768,
        max_tokens=128,
        temperature=0.2,
    )


def _headers_lower(headers: dict[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}


class UrlopenRecorder:
    def __init__(self, payload):
        self.payload = payload
        self.urls: list[str] = []
        self.headers: list[dict[str, str]] = []
        self.bodies: list[str] = []

    def __call__(self, request, timeout=None):
        self.urls.append(request.full_url)
        self.headers.append(dict(request.header_items()))
        self.bodies.append(request.data.decode("utf-8"))
        return FakeResponse(self.payload)


class CompletionRecorder:
    def __init__(self, payload):
        self.payload = payload
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.payload


class RaisingUrlopen:
    def __call__(self, request, timeout=None):
        raise HTTPError(request.full_url, 500, "boom secret-openai", hdrs=None, fp=None)


class StatusUrlopen:
    def __init__(self, status, headers=None):
        self.status = status
        self.headers = headers or {}

    def __call__(self, request, timeout=None):
        raise HTTPError(
            request.full_url,
            self.status,
            "provider failure",
            hdrs=self.headers,
            fp=BytesIO(b'{"error":"sensitive body"}'),
        )


class RawResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


class FakeSdkError(Exception):
    def __init__(self, message, *, status_code, retry_after=None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def _endpoint_for_litellm():
    from agent.protocols.llm import LLMEndpoint

    return LLMEndpoint(
        name="litellm",
        protocol="litellm",
        base_url="",
        api_key="litellm-secret",
        provider="openai",
        model="gpt-test",
    )


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")
