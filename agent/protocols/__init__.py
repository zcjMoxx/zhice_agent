"""Protocol interfaces for ZhiCe-Agent.

Protocol modules define contracts consumed by the Agent core. They should not
import concrete implementations from tools, sessions, LLM providers, or skills.
"""

from agent.protocols.mcp import (
    McpCatalogSnapshot,
    McpConnectionEvent,
    McpInteractionRequest,
    McpInteractionResponse,
    McpOAuthSpec,
    McpOAuthStatus,
    McpRuntimeFacade,
    McpRuntimeStatsSnapshot,
    McpServerSpec,
    McpServerStatus,
    McpToolDescriptor,
    McpToolStats,
)

__all__ = [
    "McpCatalogSnapshot",
    "McpConnectionEvent",
    "McpInteractionRequest",
    "McpInteractionResponse",
    "McpOAuthSpec",
    "McpOAuthStatus",
    "McpRuntimeFacade",
    "McpRuntimeStatsSnapshot",
    "McpServerSpec",
    "McpServerStatus",
    "McpToolDescriptor",
    "McpToolStats",
]
from agent.protocols.channel import (
    ChannelAttachment,
    ChannelCapabilities,
    ChannelChatRuntime,
    ChannelExecutionContext,
    ChannelQuote,
    ChannelReplyTarget,
    InboundChannelEvent,
)

__all__ = [
    "ChannelAttachment",
    "ChannelCapabilities",
    "ChannelChatRuntime",
    "ChannelExecutionContext",
    "ChannelQuote",
    "ChannelReplyTarget",
    "InboundChannelEvent",
]
