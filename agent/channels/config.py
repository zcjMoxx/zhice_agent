"""Runtime configuration for optional external channels."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from agent.runtime_config import load_runtime_section


class ChannelConfigurationError(RuntimeError):
    """Raised when the config.yml channels section is invalid or unsafe."""


_ENV_VALUE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


@dataclass(frozen=True)
class QQAccountConfig:
    """One QQ bot account; secrets are excluded from repr."""

    key: str
    app_id: str = field(repr=False)
    app_secret: str = field(repr=False)
    web_base_url: str = "http://127.0.0.1:10086"
    c2c_enabled: bool = True
    group_enabled: bool = True
    group_require_mention: bool = True
    http_timeout_seconds: int = 15
    max_parallel_conversations: int = 8
    max_attachment_bytes: int = 20 * 1024 * 1024


@dataclass(frozen=True)
class QQChannelConfig:
    enabled: bool = False
    transport: str = "websocket"
    accounts: tuple[QQAccountConfig, ...] = ()


@dataclass(frozen=True)
class WeixinChannelConfig:
    enabled: bool = False
    transport: str = "sidecar_stdio"
    node_path: str = "node"
    sidecar_entry: str = "integrations/weixin_sidecar/dist/main.js"
    binding_timeout_seconds: int = 480
    max_parallel_conversations: int = 8
    text_chunk_limit: int = 4000


@dataclass(frozen=True)
class ChannelConfiguration:
    qq: QQChannelConfig = QQChannelConfig()
    weixin: WeixinChannelConfig = WeixinChannelConfig()
    order: tuple[str, ...] = ("qq", "weixin")


def load_channel_configuration(config_dir: Path) -> ChannelConfiguration:
    """Load config.yml channels; a missing section disables all channels."""

    path = config_dir / "config.yml"
    try:
        channels_raw = load_runtime_section(config_dir, "channels", default={})
    except ValueError as exc:
        raise ChannelConfigurationError(f"Invalid channel config: {path}") from exc
    if not isinstance(channels_raw, dict):
        raise ChannelConfigurationError("config.yml channels must be a mapping")
    if not channels_raw:
        return ChannelConfiguration()
    configured_order = tuple(
        str(key) for key in channels_raw if str(key) in {"qq", "weixin"}
    )
    channel_order = configured_order + tuple(
        key for key in ("qq", "weixin") if key not in configured_order
    )
    qq_raw = channels_raw.get("qq", {}) or {}
    if not isinstance(qq_raw, dict):
        raise ChannelConfigurationError("channels.qq must be a mapping")
    enabled = _bool(qq_raw.get("enabled", False), "channels.qq.enabled")
    transport = str(qq_raw.get("transport", "websocket")).strip().lower()
    if transport != "websocket":
        raise ChannelConfigurationError("channels.qq.transport must be websocket")
    account_rows = qq_raw.get("accounts", []) or []
    if not isinstance(account_rows, list):
        raise ChannelConfigurationError("channels.qq.accounts must be a list")
    accounts: list[QQAccountConfig] = []
    seen: set[str] = set()
    for index, item in enumerate(account_rows):
        if not isinstance(item, dict):
            raise ChannelConfigurationError(f"channels.qq.accounts[{index}] must be a mapping")
        key = str(item.get("key", "")).strip()
        if not key or key in seen:
            raise ChannelConfigurationError("QQ account keys must be non-empty and unique")
        seen.add(key)
        accounts.append(
            QQAccountConfig(
                key=key,
                app_id=_secret(item.get("app_id", "")),
                app_secret=_secret(item.get("app_secret", "")),
                web_base_url=_web_base_url(item.get("web_base_url", "http://127.0.0.1:10086")),
                c2c_enabled=_bool(item.get("c2c_enabled", True), "c2c_enabled"),
                group_enabled=_bool(item.get("group_enabled", True), "group_enabled"),
                group_require_mention=_bool(
                    item.get("group_require_mention", True), "group_require_mention"
                ),
                http_timeout_seconds=_bounded_int(
                    item.get("http_timeout_seconds", 15),
                    "http_timeout_seconds",
                    minimum=1,
                    maximum=60,
                ),
                max_parallel_conversations=_positive_int(
                    item.get("max_parallel_conversations", 8), "max_parallel_conversations"
                ),
                max_attachment_bytes=_positive_int(
                    item.get("max_attachment_bytes", 20 * 1024 * 1024),
                    "max_attachment_bytes",
                ),
            )
        )
    weixin_raw = channels_raw.get("weixin", {}) or {}
    if not isinstance(weixin_raw, dict):
        raise ChannelConfigurationError("channels.weixin must be a mapping")
    weixin_transport = str(weixin_raw.get("transport", "sidecar_stdio")).strip().lower()
    if weixin_transport != "sidecar_stdio":
        raise ChannelConfigurationError("channels.weixin.transport must be sidecar_stdio")
    return ChannelConfiguration(
        qq=QQChannelConfig(enabled=enabled, transport=transport, accounts=tuple(accounts)),
        weixin=WeixinChannelConfig(
            enabled=_bool(weixin_raw.get("enabled", False), "channels.weixin.enabled"),
            transport=weixin_transport,
            node_path=str(weixin_raw.get("node_path", "node")).strip() or "node",
            sidecar_entry=str(
                weixin_raw.get("sidecar_entry", "integrations/weixin_sidecar/dist/main.js")
            ).strip(),
            binding_timeout_seconds=_positive_int(
                weixin_raw.get("binding_timeout_seconds", 480),
                "channels.weixin.binding_timeout_seconds",
            ),
            max_parallel_conversations=_positive_int(
                weixin_raw.get("max_parallel_conversations", 8),
                "channels.weixin.max_parallel_conversations",
            ),
            text_chunk_limit=_positive_int(
                weixin_raw.get("text_chunk_limit", 4000),
                "channels.weixin.text_chunk_limit",
            ),
        ),
        order=channel_order,
    )


def _secret(value: object) -> str:
    text = str(value or "").strip()
    match = _ENV_VALUE.fullmatch(text)
    return os.getenv(match.group(1), "") if match else text


def _bool(value: object, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ChannelConfigurationError(f"{field_name} must be true or false")


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise ChannelConfigurationError(f"{field_name} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ChannelConfigurationError(f"{field_name} must be a positive integer") from exc
    if result < 1:
        raise ChannelConfigurationError(f"{field_name} must be a positive integer")
    return result


def _bounded_int(
    value: object,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool):
        raise ChannelConfigurationError(
            f"{field_name} must be between {minimum} and {maximum}"
        )
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ChannelConfigurationError(
            f"{field_name} must be between {minimum} and {maximum}"
        ) from exc
    if result < minimum or result > maximum:
        raise ChannelConfigurationError(
            f"{field_name} must be between {minimum} and {maximum}"
        )
    return result


def _web_base_url(value: object) -> str:
    text = str(value or "").strip().rstrip("/")
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
        raise ChannelConfigurationError("web_base_url must be an http(s) origin")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ChannelConfigurationError("web_base_url must not contain a path, query, or fragment")
    return text
