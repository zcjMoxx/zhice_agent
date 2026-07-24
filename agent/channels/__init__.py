"""External channel application-shell services."""

from agent.channels.config import ChannelConfiguration, QQAccountConfig, load_channel_configuration
from agent.channels.conversation import ChannelConversationService
from agent.channels.dedup import ChannelDedupService
from agent.channels.identity import ExternalIdentityService
from agent.channels.manager import ChannelManager
from agent.channels.runtime_adapter import ChannelRuntimeAdapter

__all__ = [
    "ChannelConfiguration",
    "ChannelConversationService",
    "ChannelDedupService",
    "ChannelManager",
    "ChannelRuntimeAdapter",
    "ExternalIdentityService",
    "QQAccountConfig",
    "load_channel_configuration",
]
