"""Adapter from channel routing to the existing shared application runtime."""

from __future__ import annotations

from agent.channels.conversation import ChannelConversationService
from agent.protocols.auth import ActorContext
from agent.protocols.channel import ChannelExecutionContext, RuntimeEventCallback


class ChannelRuntimeAdapter:
    """Keep conversation routing and channel context outside AgentLoop."""

    def __init__(self, runtime, conversations: ChannelConversationService):
        self.runtime = runtime
        self.conversations = conversations

    def run_chat_events(
        self,
        actor: ActorContext,
        session_id: str,
        message: str,
        *,
        turn_id: str,
        on_event: RuntimeEventCallback,
        command_profile: str,
        request_id: str,
        channel_context: ChannelExecutionContext,
    ):
        return self.runtime.run_chat_events(
            actor,
            session_id,
            message,
            turn_id=turn_id,
            on_event=on_event,
            command_profile=command_profile,
            request_id=request_id,
            channel_context=channel_context,
        )

    def dispatch(
        self,
        actor: ActorContext,
        message: str,
        *,
        turn_id: str,
        on_event: RuntimeEventCallback,
        request_id: str,
        channel_context: ChannelExecutionContext,
    ):
        if message.strip().lower() == "/new":
            route = self.conversations.rotate(actor, channel_context)
            content = f"New session: `{route.session_id}`"
            on_event({"type": "text_delta", "content": content})
            from agent.app.runtime import ChatTurnResult

            return ChatTurnResult(content=content, turn_id=turn_id)
        if message.strip().lower().startswith("/confirm "):
            if channel_context.conversation_type != "c2c":
                content = "Tool confirmation is only available in direct chat or Web."
            else:
                parts = message.strip().split()
                if len(parts) != 3 or parts[2].lower() not in {"approve", "deny"}:
                    content = "Usage: `/confirm <confirmation-id> approve|deny`"
                else:
                    status = self.runtime.decide_tool_confirmation(
                        actor,
                        parts[1],
                        parts[2].lower() == "approve",
                    )
                    content = f"Confirmation status: `{status}`"
            on_event({"type": "text_delta", "content": content})
            from agent.app.runtime import ChatTurnResult

            return ChatTurnResult(content=content, turn_id=turn_id)
        route = self.conversations.resolve(actor, channel_context)
        return self.run_chat_events(
            actor,
            route.session_id,
            message,
            turn_id=turn_id,
            on_event=on_event,
            command_profile=channel_context.capabilities.command_profile,
            request_id=request_id,
            channel_context=channel_context,
        )

    def stop_turn(self, actor: ActorContext, session_id: str, turn_id: str | None = None) -> bool:
        del turn_id
        result = self.runtime.cancel_session(actor, session_id)
        return bool(result.get("cancelled"))

    def resume_confirmation(self, actor: ActorContext, confirmation_id: str, decision: str):
        broker = self.runtime.confirmation_broker
        if broker is None:
            raise RuntimeError("confirmation is unavailable")
        return broker.decide(actor, confirmation_id, decision == "approve")
