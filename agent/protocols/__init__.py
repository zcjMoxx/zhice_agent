"""Protocol interfaces for ZhiCe-Agent.

Protocol modules define contracts consumed by the Agent core. They should not
import concrete implementations from tools, sessions, LLM providers, or skills.
"""

from agent.protocols.mcp import (
    McpCatalogSnapshot,
    McpInteractionRequest,
    McpInteractionResponse,
    McpOAuthSpec,
    McpRuntimeFacade,
    McpServerSpec,
    McpServerStatus,
    McpToolDescriptor,
)

__all__ = [
    "McpCatalogSnapshot",
    "McpInteractionRequest",
    "McpInteractionResponse",
    "McpOAuthSpec",
    "McpRuntimeFacade",
    "McpServerSpec",
    "McpServerStatus",
    "McpToolDescriptor",
]
