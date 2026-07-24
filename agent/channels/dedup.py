"""Persistent inbound event deduplication."""

from agent.auth.store import SQLiteAuthStore


class ChannelDedupService:
    def __init__(self, store: SQLiteAuthStore):
        self.store = store

    def claim(self, channel: str, account_key: str, event_id: str, message_id: str = "") -> bool:
        if not event_id.strip():
            return False
        return self.store.claim_channel_event(
            channel=channel,
            account_key=account_key,
            event_id=event_id,
            message_id=message_id,
        )

    def finish(
        self,
        channel: str,
        account_key: str,
        event_id: str,
        *,
        status: str = "done",
        error_code: str = "",
    ) -> None:
        self.store.finish_channel_event(
            channel=channel,
            account_key=account_key,
            event_id=event_id,
            status=status,
            error_code=error_code,
        )
