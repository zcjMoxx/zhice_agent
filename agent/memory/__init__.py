"""Long-term Memory implementations."""

from __future__ import annotations

from typing import Any

from agent.memory.startup import (
    MemoryExtractionStartupResult,
    check_memory_extraction_startup,
)


class MemoryStoreError(RuntimeError):
    """Stable Memory failure safe to convert into a ToolResult."""

    def __init__(self, code: str, message: str, metadata: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.metadata = dict(metadata or {})


__all__ = [
    "MemoryExtractionStartupResult",
    "MemoryStoreError",
    "check_memory_extraction_startup",
]
