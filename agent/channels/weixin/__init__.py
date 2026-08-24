"""Weixin ClawBot external-channel implementation."""

from agent.channels.weixin.adapter import WeixinClawAdapter
from agent.channels.weixin.binding import WeixinBindingService
from agent.channels.weixin.notification import WeixinNotificationProvider
from agent.channels.weixin.startup import build_weixin_adapter, check_weixin_startup

__all__ = [
    "WeixinBindingService",
    "WeixinClawAdapter",
    "WeixinNotificationProvider",
    "build_weixin_adapter",
    "check_weixin_startup",
]
