"""Workspace official SMTP notification adapter."""

from agent.integrations.email.personal_smtp import PersonalSMTPEmailProvider


class OfficialSMTPEmailProvider(PersonalSMTPEmailProvider):
    """Same secure transport, with credentials supplied only by workspace configuration."""
