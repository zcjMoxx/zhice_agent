from __future__ import annotations

import re
from email.message import EmailMessage as MimeMessage

from agent.connections.protocols import ConnectionError, EmailMessage

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def validate_message(message: EmailMessage) -> None:
    if not message.recipients or len(message.recipients) > 20:
        raise ConnectionError("EMAIL_REJECTED", "email must have between 1 and 20 recipients")
    if any(not _EMAIL.fullmatch(value) for value in message.recipients):
        raise ConnectionError("EMAIL_REJECTED", "email recipient is invalid")
    if not message.subject or len(message.subject) > 200 or len(message.text) > 100_000:
        raise ConnectionError("EMAIL_REJECTED", "email content exceeds the allowed limits")


def mime_message(message: EmailMessage, *, sender: str) -> MimeMessage:
    validate_message(message)
    mime = MimeMessage()
    mime["From"] = sender
    mime["To"] = ", ".join(message.recipients)
    mime["Subject"] = message.subject
    mime.set_content(message.text)
    if message.html:
        mime.add_alternative(message.html, subtype="html")
    return mime
