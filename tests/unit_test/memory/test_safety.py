from __future__ import annotations

import pytest

from agent.memory.markdown_store import MemoryStoreError
from agent.memory.safety import MemorySafetyPolicy


@pytest.mark.parametrize(
    "content",
    [
        "api_key=sk-secret-value-123456789",
        "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
        "Authorization: Bearer very-secret-token-value",
        "stdout:\n" + "log line\n" * 80,
    ],
)
def test_memory_safety_rejects_sensitive_or_tool_output_content(content):
    policy = MemorySafetyPolicy(max_content_chars=1000)

    with pytest.raises(MemoryStoreError) as exc_info:
        policy.validate(content)

    assert exc_info.value.code == "MEMORY_SENSITIVE_CONTENT_REJECTED"
    assert content not in str(exc_info.value)


def test_memory_safety_accepts_short_stable_preference():
    assert MemorySafetyPolicy().validate("回答代码问题时优先检查真实实现。") == (
        "回答代码问题时优先检查真实实现。"
    )
