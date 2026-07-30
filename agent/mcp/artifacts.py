"""Actor-scoped import boundary for MCP binary and temporary-file results."""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

MAX_ARTIFACT_BYTES = 20 * 1024 * 1024
MAX_CALL_ARTIFACT_BYTES = 40 * 1024 * 1024
MAX_ARTIFACTS_PER_CALL = 16
MAX_ACTOR_MCP_BYTES = 200 * 1024 * 1024
MAX_ARTIFACT_VERSIONS_PER_SERVER = 128
MAX_ARTIFACT_PREVIEW_BYTES = 4096
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
        self.enforce_retention(files_dir, server_id)
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
        root = (Path(files_dir).resolve() / "mcp" / _safe_component(server_id, "server")).resolve()
        root.mkdir(parents=True, exist_ok=True)
        current_size = sum(candidate.stat().st_size for candidate in root.rglob("*") if candidate.is_file())
        if current_size + size > MAX_ACTOR_MCP_BYTES:
            raise McpArtifactError(
                "MCP artifact quota would be exceeded",
                "MCP_ARTIFACT_QUOTA_EXCEEDED",
            )
        target = _unique_stream_target(root, _safe_filename(suggested_name or path.name), path)
        with path.open("rb") as source_stream, target.open("xb") as target_stream:
            shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
        self.enforce_retention(files_dir, server_id)
        return target

    def preview(
        self,
        files_dir: Path | str,
        server_id: str,
        artifact: Path | str,
        *,
        max_bytes: int = MAX_ARTIFACT_PREVIEW_BYTES,
    ) -> dict[str, object]:
        """Return a bounded preview without allowing paths outside the actor root."""

        if isinstance(max_bytes, bool) or not 1 <= max_bytes <= MAX_ARTIFACT_PREVIEW_BYTES:
            raise McpArtifactError("MCP artifact preview size is invalid")
        root = (Path(files_dir).resolve() / "mcp" / _safe_component(server_id, "server")).resolve()
        path = Path(artifact)
        if not path.is_absolute():
            path = root / path
        path = path.resolve()
        if not _is_relative_to(path, root) or not path.is_file():
            raise McpArtifactError("MCP artifact is outside the actor root")
        size = path.stat().st_size
        with path.open("rb") as stream:
            content = stream.read(max_bytes)
        return {
            "path": path.relative_to(Path(files_dir).resolve()).as_posix(),
            "size": size,
            "preview": content.decode("utf-8", errors="replace"),
            "truncated": size > len(content),
        }

    def enforce_retention(
        self,
        files_dir: Path | str,
        server_id: str,
        *,
        max_files: int = MAX_ARTIFACT_VERSIONS_PER_SERVER,
    ) -> int:
        """Remove only the oldest MCP-generated versions beyond the bounded policy."""

        if isinstance(max_files, bool) or max_files < 1:
            raise McpArtifactError("MCP artifact retention limit is invalid")
        root = (Path(files_dir).resolve() / "mcp" / _safe_component(server_id, "server")).resolve()
        if not root.exists():
            return 0
        files = sorted(
            (path for path in root.iterdir() if path.is_file()),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
        )
        removed = 0
        for path in files[: max(0, len(files) - max_files)]:
            path.unlink()
            removed += 1
        return removed

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


def _unique_stream_target(root: Path, name: str, source: Path) -> Path:
    target = root / name
    if not target.exists():
        return target
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    stem, suffix = target.stem, target.suffix
    candidate = root / f"{stem}-{digest.hexdigest()[:10]}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = root / f"{stem}-{digest.hexdigest()[:10]}-{counter}{suffix}"
        counter += 1
    return candidate


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
