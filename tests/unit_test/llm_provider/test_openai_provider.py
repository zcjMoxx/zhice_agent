"""Tests for the OpenAI-compatible LLM provider."""

import json
from urllib.error import HTTPError

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

    with pytest.raises(Exception, match="llm_endpoints.json"):
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


def test_create_llm_provider_creates_litellm_provider():
    """LiteLLM proxy endpoints should use the LiteLLMProvider adapter."""

    from agent.llm import create_llm_provider
    from agent.llm.litellm_provider import LiteLLMProvider
    from agent.protocols.llm import LLMEndpoint

    endpoint = LLMEndpoint(
        name="claude",
        protocol="litellm",
        base_url="https://api.anthropic.com/v1",
        api_key="dummy",
        model="anthropic/claude-sonnet-4",
    )

    provider = create_llm_provider(endpoint)

    assert isinstance(provider, LiteLLMProvider)


def test_litellm_provider_sends_proxy_chat_completion_request():
    """LiteLLMProvider should speak to LiteLLM proxy's OpenAI-compatible API."""

    from agent.llm.litellm_provider import LiteLLMProvider
    from agent.protocols.llm import LLMEndpoint

    tools = [{"type": "function", "function": {"name": "exec", "parameters": {}}}]
    recorder = UrlopenRecorder(
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
        base_url="http://127.0.0.1:4000/v1",
        api_key="litellm-secret",
        model="anthropic/claude-sonnet-4",
        max_tokens=256,
        temperature=0.1,
    )

    response = LiteLLMProvider(endpoint, urlopen=recorder).chat(
        messages=[{"role": "user", "content": "run"}],
        tools=tools,
    )

    assert recorder.urls == ["http://127.0.0.1:4000/v1/chat/completions"]
    request_body = json.loads(recorder.bodies[0])
    assert request_body["model"] == "anthropic/claude-sonnet-4"
    assert request_body["tools"] == tools
    assert _headers_lower(recorder.headers[0])["authorization"] == "Bearer litellm-secret"
    assert response.content == "ok"
    assert response.tool_calls == [{"id": "call_1", "type": "function"}]
    assert response.metadata["finish_reason"] == "tool_calls"
    assert response.metadata["usage"] == {"prompt_tokens": 5, "completion_tokens": 3}


def _endpoint(api_key=""):
    from agent.protocols.llm import LLMEndpoint

    return LLMEndpoint(
        name="default",
        protocol="openai",
        base_url="https://example.test/v1",
        api_key=api_key,
        model="fake-openai",
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


class RaisingUrlopen:
    def __call__(self, request, timeout=None):
        raise HTTPError(request.full_url, 500, "boom secret-openai", hdrs=None, fp=None)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")
