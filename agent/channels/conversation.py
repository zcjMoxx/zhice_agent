"""Persistent external conversation to internal Session routing."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from agent.auth.session_access import SessionAccessService
from agent.auth.store import SQLiteAuthStore
from agent.protocols.auth import ActorContext
from agent.protocols.channel import ChannelExecutionContext


@dataclass(frozen=True)
class ChannelConversation:
    route_id: str
    session_id: str
    created: bool = False


class ChannelConversationService:
    """Resolve and rotate stable per-owner external conversation routes."""

    def __init__(self, store: SQLiteAuthStore, sessions: SessionAccessService):
        self.store = store
        self.sessions = sessions

    def resolve(
        self, actor: ActorContext, context: ChannelExecutionContext
    ) -> ChannelConversation:
        row = self._get(actor, context)
        if row is not None:
            return ChannelConversation(str(row["id"]), str(row["current_session_id"]))
        return self.rotate(actor, context, created=True)

    def rotate(
        self,
        actor: ActorContext,
        context: ChannelExecutionContext,
        *,
        created: bool = False,
    ) -> ChannelConversation:
        if actor.user_id is None:
            raise ValueError("internal user is required for channel conversation routing")
        session_id = f"{context.channel}_{uuid.uuid4().hex}"
        self.sessions.ensure_session(
            actor,
            session_id,
            channel=context.channel,
            conversation_type=context.conversation_type,
            external_chat_id=context.external_conversation_id,
            external_thread_id=context.external_thread_id,
            write=True,
        )
        row = self.store.channel_conversation_upsert(
            channel=context.channel,
            account_key=context.account_key,
            conversation_type=context.conversation_type,
            external_conversation_id=context.external_conversation_id,
            external_thread_id=context.external_thread_id,
            owner_user_id=str(actor.user_id),
            current_session_id=session_id,
        )
        return ChannelConversation(str(row["id"]), session_id, created=created)

    def _get(self, actor: ActorContext, context: ChannelExecutionContext):
        return self.store.channel_conversation_get(
            channel=context.channel,
            account_key=context.account_key,
            conversation_type=context.conversation_type,
            external_conversation_id=context.external_conversation_id,
            external_thread_id=context.external_thread_id,
            owner_user_id=str(actor.user_id or ""),
        )
