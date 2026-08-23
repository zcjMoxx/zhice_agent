"""Compatibility exports for the provider-neutral email protocol."""

from agent.connections.protocols import EmailMessage, EmailProvider, EmailSendResult

__all__ = ["EmailMessage", "EmailProvider", "EmailSendResult"]
