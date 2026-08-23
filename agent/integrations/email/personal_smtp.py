"""TLS-only personal SMTP adapter using an app password."""

from __future__ import annotations

import smtplib
import ssl

from agent.connections.protocols import ConnectionError, EmailMessage, EmailSendResult
from agent.integrations.email._common import mime_message

_PORTS = {465, 587}


class PersonalSMTPEmailProvider:
    def __init__(self, *, host: str, port: int, security: str, username: str,
                 app_password: str, from_address: str, timeout_seconds: float = 20):
        if not host or port not in _PORTS or security not in {"tls", "starttls"}:
            raise ConnectionError("CONNECTION_SMTP_INSECURE", "SMTP requires TLS/STARTTLS on an allowed port")
        if (security == "tls" and port != 465) or (security == "starttls" and port != 587):
            raise ConnectionError("CONNECTION_SMTP_INSECURE", "SMTP security does not match the port")
        self.host, self.port, self.security = host, port, security
        self.username, self.app_password, self.from_address = username, app_password, from_address
        self.timeout_seconds = timeout_seconds

    def verify(self) -> None:
        try:
            with self._connect() as client:
                client.login(self.username, self.app_password)
        except (TimeoutError, OSError, smtplib.SMTPException) as exc:
            raise ConnectionError("EMAIL_REJECTED", "SMTP connection verification failed") from exc

    def send(self, message: EmailMessage) -> EmailSendResult:
        mime = mime_message(message, sender=self.from_address)
        try:
            with self._connect() as client:
                client.login(self.username, self.app_password)
                refused = client.send_message(mime)
        except (TimeoutError, smtplib.SMTPServerDisconnected) as exc:
            raise ConnectionError("EMAIL_OUTCOME_UNKNOWN", "SMTP outcome is unknown") from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise ConnectionError("EMAIL_REJECTED", "SMTP server rejected the message") from exc
        if refused:
            raise ConnectionError("EMAIL_REJECTED", "SMTP server refused one or more recipients")
        return EmailSendResult("accepted", message="SMTP server accepted the message")

    def _connect(self):
        context = ssl.create_default_context()
        if self.security == "tls":
            return smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout_seconds, context=context)
        client = smtplib.SMTP(self.host, self.port, timeout=self.timeout_seconds)
        try:
            client.starttls(context=context)
        except Exception:
            client.close()
            raise
        return client
