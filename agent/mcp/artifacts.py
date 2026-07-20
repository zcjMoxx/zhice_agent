"""Actor-scoped import boundary for MCP binary and temporary-file results."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

MAX_ARTIFACT_BYTES = 20 * 1024 * 1024
MAX_CALL_ARTIFACT_BYTES = 40 * 1024 * 1024
MAX_ARTIFACTS_PER_CALL = 16
MAX_ACTOR_MCP_BYTES = 200 * 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class McpArtifactError(RuntimeError):
    """Rejected artifact import with a stable error code."""

    def __init__(self, message: str, code: str = "MCP_ARTIFACT_INVALID"):
        super().__init__(message)
        self.code = code


class McpArtifactGateway:
    """Write Server results only below the current actor's ``files/mcp`` root."""

    def save_bytes(
        self,
        files_dir: Path | str,
        server_id: str,
        suggested_name: str,
        content: bytes,
    ) -> Path:
        """Save one bounded binary result without trusting Server paths."""

        if len(content) > MAX_ARTIFACT_BYTES:
            raise McpArtifactError("MCP artifact exceeds the size limit", "MCP_ARTIFACT_TOO_LARGE")
        root = (Path(files_dir).resolve() / "mcp" / _safe_component(server_id, "server")).resolve()
        root.mkdir(parents=True, exist_ok=True)
        current_size = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
        if current_size + len(content) > MAX_ACTOR_MCP_BYTES:
            raise McpArtifactError(
                "MCP artifact quota would be exceeded",
                "MCP_ARTIFACT_QUOTA_EXCEEDED",
            )
        name = _safe_filename(suggested_name)
        target = _unique_target(root, name, content)
        target.write_bytes(content)
        return target

    def import_temp_file(
        self,
        files_dir: Path | str,
        server_id: str,
        temp_root: Path | str,
        source: Path | str,
        *,
        suggested_name: str = "",
    ) -> Path:
        """Import a file only when it belongs to this Server's temp sandbox."""

        path, size = self.inspect_temp_file(temp_root, source)
        if size > MAX_ARTIFACT_BYTES:
            raise McpArtifactError("MCP artifact exceeds the size limit", "MCP_ARTIFACT_TOO_LARGE")
        return self.save_bytes(files_dir, server_id, suggested_name or path.name, path.read_bytes())

    def inspect_temp_file(
        self,
        temp_root: Path | str,
        source: Path | str,
    ) -> tuple[Path, int]:
        """Validate one Server-returned path before reading even its metadata."""

        sandbox = Path(temp_root).resolve()
        path = Path(source).resolve()
        if not _is_relative_to(path, sandbox) or not path.is_file():
            raise McpArtifactError("MCP temporary file is outside its sandbox")
        try:
            return path, path.stat().st_size
        except OSError as exc:
            raise McpArtifactError("Cannot inspect MCP temporary file") from exc


def _safe_filename(value: str) -> str:
    basename = Path(str(value or "artifact.bin").replace("\\", "/")).name
    normalized = _SAFE_NAME.sub("_", basename).strip("._")
    return (normalized or "artifact.bin")[:120]


def _safe_component(value: str, fallback: str) -> str:
    normalized = _SAFE_NAME.sub("_", value).strip("._")
    return normalized or fallback


def _unique_target(root: Path, name: str, content: bytes) -> Path:
    target = root / name
    if not target.exists():
        return target
    digest = hashlib.sha256(content).hexdigest()[:10]
    stem, suffix = target.stem, target.suffix
    candidate = root / f"{stem}-{digest}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = root / f"{stem}-{digest}-{counter}{suffix}"
        counter += 1
    return candidate


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
