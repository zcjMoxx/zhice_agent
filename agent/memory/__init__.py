"""Long-term Memory implementations."""

from __future__ import annotations

from typing import Any


class MemoryStoreError(RuntimeError):
    """Stable Memory failure safe to convert into a ToolResult."""

    def __init__(self, code: str, message: str, metadata: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.metadata = dict(metadata or {})


__all__ = ["MemoryStoreError"]
