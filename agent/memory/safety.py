"""Content safety checks for durable Memory writes."""

from __future__ import annotations

import re

from agent.memory import MemoryStoreError

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|password|passwd|secret)\s*[:=]\s*\S{8,}"),
    re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+\S+"),
    re.compile(r"(?i)\b(sk-[A-Za-z0-9_-]{12,})\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


class MemorySafetyPolicy:
    """Reject obvious secrets, oversized text, and raw tool/log dumps."""

    def __init__(self, *, max_content_chars: int = 1000):
        self.max_content_chars = max_content_chars

    def validate(self, content: str) -> str:
        if not isinstance(content, str):
            raise MemoryStoreError(
                "MEMORY_SENSITIVE_CONTENT_REJECTED",
                "Memory content was rejected by the safety policy.",
            )
        normalized = content.strip()
        if (
            not normalized
            or len(normalized) > self.max_content_chars
            or any(pattern.search(normalized) for pattern in _SECRET_PATTERNS)
            or _looks_like_raw_output(normalized)
        ):
            raise MemoryStoreError(
                "MEMORY_SENSITIVE_CONTENT_REJECTED",
                "Memory content was rejected by the safety policy.",
            )
        return " ".join(normalized.split())


def _looks_like_raw_output(content: str) -> bool:
    lines = content.splitlines()
    if len(lines) >= 40:
        return True
    lowered = content.casefold()
    return len(content) > 500 and any(
        marker in lowered for marker in ("stdout:\n", "stderr:\n", "traceback (most recent call last)")
    )
