"""Provider-neutral text embedding contract."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class EmbeddingProvider(Protocol):
    """Convert bounded text batches into same-dimension vectors."""

    @property
    def identity(self) -> str:
        """Return a stable provider/model identity used for invalidation."""

    @property
    def batch_size(self) -> int:
        """Return the endpoint-specific maximum texts per request."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one vector for each input text, preserving input order."""
