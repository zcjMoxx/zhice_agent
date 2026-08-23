"""Provider-neutral contracts for user-owned external connections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ExternalConnection:
    id: str
    owner_user_id: str
    provider: str
    account_display: str
    scopes: tuple[str, ...]
    expires_at: str | None
    status: str
    created_at: str
    updated_at: str


class ConnectionError(RuntimeError):
    """Structured connection failure safe to map to an API response."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class EmailMessage:
    recipients: tuple[str, ...]
    subject: str
    text: str
    html: str | None = None


@dataclass(frozen=True)
class EmailSendResult:
    status: str
    provider_message_id: str | None = None
    message: str = ""


class EmailProvider(Protocol):
    def send(self, message: EmailMessage) -> EmailSendResult: ...
