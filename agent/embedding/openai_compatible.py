"""OpenAI-compatible embeddings endpoint adapter."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request


@dataclass(frozen=True)
class EmbeddingEndpoint:
    base_url: str
    model: str
    api_key: str
    dimensions: int | None = None
    timeout: float = 30.0
    batch_size: int = 16


class OpenAICompatibleEmbeddingProvider:
    """Call an OpenAI-compatible /embeddings endpoint without SDK coupling."""

    def __init__(self, endpoint: EmbeddingEndpoint, urlopen: Callable[..., Any] | None = None):
        self.endpoint = endpoint
        self._urlopen = urlopen or request.urlopen

    @property
    def identity(self) -> str:
        dimensions = self.endpoint.dimensions or "default"
        return f"openai-compatible:{self.endpoint.base_url}:{self.endpoint.model}:{dimensions}"

    @property
    def batch_size(self) -> int:
        return self.endpoint.batch_size

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        payload: dict[str, Any] = {"model": self.endpoint.model, "input": list(texts)}
        if self.endpoint.dimensions is not None:
            payload["dimensions"] = self.endpoint.dimensions
        req = request.Request(
            f"{self.endpoint.base_url.rstrip('/')}/embeddings",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.endpoint.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._urlopen(req, timeout=self.endpoint.timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except (error.URLError, error.HTTPError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"embedding request failed: {type(exc).__name__}") from exc
        rows = sorted(raw.get("data") or [], key=lambda item: int(item.get("index", 0)))
        vectors = [[float(number) for number in row.get("embedding") or []] for row in rows]
        if len(vectors) != len(texts) or any(not vector for vector in vectors):
            raise RuntimeError("embedding response shape did not match input")
        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) != 1:
            raise RuntimeError("embedding response dimensions are inconsistent")
        return vectors


def load_embedding_provider(config_dir: Path | str) -> OpenAICompatibleEmbeddingProvider | None:
    """Load the optional embedding route from the unified model registry."""

    path = Path(config_dir) / "models.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version", 1) != 1:
        raise ValueError("models.json must be a schema_version 1 object")
    routing = raw.get("routing", {})
    endpoints = raw.get("embedding", {})
    if not isinstance(routing, dict) or not isinstance(endpoints, dict):
        raise ValueError("models.json routing and embedding must be objects")
    route = str(routing.get("embedding") or "").strip()
    if not route:
        return None
    endpoint_name, separator, routed_model = route.partition("/")
    if not endpoint_name or (separator and not routed_model):
        raise ValueError("models.json routing.embedding must be endpoint or endpoint/model")
    item = endpoints.get(endpoint_name)
    if not isinstance(item, dict):
        raise ValueError(f"embedding endpoint is not configured: {endpoint_name}")
    if not item.get("enabled", True):
        return None
    if str(item.get("protocol") or "openai") != "openai":
        raise ValueError("embedding endpoint protocol must be openai")
    default_model = str(item.get("model") or "").strip()
    model = routed_model or default_model
    supported = item.get("supported_models", [])
    if not isinstance(supported, list):
        raise ValueError("embedding supported_models must be a list")
    if not model or (model != default_model and model not in supported):
        raise ValueError(f"embedding endpoint {endpoint_name!r} does not support model {model!r}")
    base_url = str(item.get("base_url") or "").strip()
    api_key = _resolve_secret(str(item.get("api_key") or ""))
    if not base_url or not api_key:
        return None
    batch_size = int(item.get("batch_size", 16))
    if batch_size < 1:
        raise ValueError("embedding batch_size must be positive")
    return OpenAICompatibleEmbeddingProvider(
        EmbeddingEndpoint(
            base_url=base_url,
            model=model,
            api_key=api_key,
            dimensions=(int(item["dimensions"]) if item.get("dimensions") else None),
            timeout=float(item.get("timeout", 30.0)),
            batch_size=batch_size,
        )
    )


def _resolve_secret(value: str) -> str:
    if value.startswith("${") and value.endswith("}"):
        return os.getenv(value[2:-1], "")
    return value
