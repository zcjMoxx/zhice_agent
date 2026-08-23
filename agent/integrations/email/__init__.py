"""Email providers used by workflow nodes."""

from agent.connections.protocols import EmailMessage, EmailSendResult
from agent.integrations.email.official_smtp import OfficialSMTPEmailProvider
from agent.integrations.email.personal_smtp import PersonalSMTPEmailProvider

__all__ = ["EmailMessage", "EmailSendResult", "OfficialSMTPEmailProvider", "PersonalSMTPEmailProvider"]
