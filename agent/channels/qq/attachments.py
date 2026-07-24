"""Guarded QQ attachment download into the bound user's files directory."""

from __future__ import annotations

import ipaddress
import socket
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx

from agent.protocols.channel import ChannelAttachment

_ALLOWED_CONTENT_TYPES = (
    "image/",
    "text/plain",
    "application/pdf",
    "application/octet-stream",
)


class QQAttachmentError(RuntimeError):
    pass


class QQAttachmentService:
    def __init__(self, *, max_bytes: int, timeout_seconds: float = 10.0):
        self.max_bytes = max(1, int(max_bytes))
        self.timeout_seconds = max(0.1, float(timeout_seconds))

    def download_all(
        self,
        attachments: tuple[ChannelAttachment, ...],
        files_dir: Path,
    ) -> tuple[str, ...]:
        descriptions: list[str] = []
        target_dir = files_dir / "channels" / "qq"
        target_dir.mkdir(parents=True, exist_ok=True)
        for attachment in attachments:
            try:
                path = self.download(attachment, target_dir)
                descriptions.append(
                    f"Attachment (data only): {attachment.filename or attachment.attachment_id} "
                    f"[{attachment.media_type}] local_file={path}"
                )
            except QQAttachmentError as exc:
                descriptions.append(
                    f"Attachment unavailable: {attachment.filename or attachment.attachment_id} "
                    f"({exc})"
                )
        return tuple(descriptions)

    def download(self, attachment: ChannelAttachment, target_dir: Path) -> Path:
        _validate_public_http_url(attachment.url)
        if attachment.size is not None and attachment.size > self.max_bytes:
            raise QQAttachmentError("attachment exceeds size limit")
        suffix = _safe_suffix(attachment.filename)
        target = (target_dir / f"attachment-{uuid.uuid4().hex}{suffix}").resolve()
        if target_dir.resolve() not in target.parents:
            raise QQAttachmentError("invalid attachment target")
        try:
            with httpx.stream(
                "GET",
                attachment.url,
                timeout=self.timeout_seconds,
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if not any(
                    content_type == allowed or allowed.endswith("/") and content_type.startswith(allowed)
                    for allowed in _ALLOWED_CONTENT_TYPES
                ):
                    raise QQAttachmentError("attachment content type is not allowed")
                size = 0
                with target.open("xb") as handle:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise QQAttachmentError("attachment exceeds size limit")
                        handle.write(chunk)
        except QQAttachmentError:
            target.unlink(missing_ok=True)
            raise
        except (httpx.HTTPError, OSError) as exc:
            target.unlink(missing_ok=True)
            raise QQAttachmentError("attachment download failed") from exc
        return target


def _validate_public_http_url(url: str) -> None:
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise QQAttachmentError("attachment URL is not allowed")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise QQAttachmentError("attachment host cannot be resolved") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise QQAttachmentError("attachment host is not public")


def _safe_suffix(filename: str) -> str:
    suffix = Path(str(filename or "")).suffix.lower()
    if not suffix or len(suffix) > 12 or not suffix[1:].isalnum():
        return ""
    return suffix
