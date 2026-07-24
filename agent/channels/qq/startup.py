"""QQ startup validation and adapter construction."""

from __future__ import annotations

import importlib.util

from agent.channels.qq.adapter import QQChannelAdapter
from agent.channels.qq.transport import BotpyQQTransport
from agent.protocols.capability import CapabilityStatus


def check_qq_startup(config) -> CapabilityStatus:
    if not config.enabled:
        return CapabilityStatus("channel.qq", "disabled", "CHANNEL_QQ_DISABLED")
    if not config.accounts:
        return CapabilityStatus(
            "channel.qq",
            "unavailable",
            "CHANNEL_QQ_ACCOUNT_MISSING",
            "QQ is enabled but no account is configured.",
        )
    if importlib.util.find_spec("botpy") is None:
        return CapabilityStatus(
            "channel.qq",
            "unavailable",
            "CHANNEL_QQ_SDK_MISSING",
            "QQ is enabled but qq-botpy is not installed.",
            hint="Install the project qq extra.",
        )
    invalid = [account.key for account in config.accounts if not account.app_id or not account.app_secret]
    if invalid:
        return CapabilityStatus(
            "channel.qq",
            "unavailable",
            "CHANNEL_QQ_CREDENTIALS_MISSING",
            "One or more enabled QQ accounts have missing credentials.",
            details={"accounts": invalid},
        )
    return CapabilityStatus(
        "channel.qq",
        "available",
        "CHANNEL_QQ_AVAILABLE",
        "QQ channel configuration is available.",
        details={"accounts": [account.key for account in config.accounts]},
    )


def build_qq_adapters(config, identity, conversations, dedup, runtime):
    status = check_qq_startup(config)
    if not status.available:
        return (), status
    return (
        tuple(
            QQChannelAdapter(
                account,
                BotpyQQTransport(account),
                identity,
                conversations,
                dedup,
                runtime,
            )
            for account in config.accounts
        ),
        status,
    )
