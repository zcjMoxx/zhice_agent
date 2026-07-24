"""Startup validation and shared sidecar construction for Weixin."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from agent.channels.weixin.adapter import WeixinClawAdapter
from agent.channels.weixin.binding import WeixinBindingService
from agent.channels.weixin.sidecar import WeixinSidecarClient
from agent.protocols.capability import CapabilityStatus


def check_weixin_startup(config, workspace: Path) -> CapabilityStatus:
    if not config.enabled:
        return CapabilityStatus("channel.weixin", "disabled", "CHANNEL_WEIXIN_DISABLED")
    node = shutil.which(config.node_path)
    if node is None:
        return CapabilityStatus(
            "channel.weixin",
            "unavailable",
            "CHANNEL_WEIXIN_NODE_MISSING",
            "Weixin is enabled but Node.js is unavailable.",
        )
    try:
        version = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=5, check=True
        ).stdout.strip()
        major = int(version.lstrip("v").split(".", 1)[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        major = 0
    if major < 22:
        return CapabilityStatus(
            "channel.weixin",
            "unavailable",
            "CHANNEL_WEIXIN_NODE_VERSION",
            "Weixin requires Node.js 22 or newer.",
        )
    try:
        entry = _resolve_sidecar_entry(config.sidecar_entry, workspace)
    except ValueError:
        return CapabilityStatus(
            "channel.weixin", "unavailable", "CHANNEL_WEIXIN_ENTRY_OUTSIDE_WORKSPACE"
        )
    required = (
        entry,
        entry.parent.parent / "package-lock.json",
        entry.parent.parent / "vendor" / "upstream-manifest.json",
        entry.parent.parent / "LICENSES" / "openclaw-weixin-MIT.txt",
    )
    if any(not path.is_file() for path in required):
        return CapabilityStatus(
            "channel.weixin",
            "unavailable",
            "CHANNEL_WEIXIN_SIDECAR_INCOMPLETE",
            "Weixin sidecar artifacts or provenance files are missing.",
        )
    return CapabilityStatus(
        "channel.weixin",
        "available",
        "CHANNEL_WEIXIN_SIDECAR_AVAILABLE",
        "Weixin sidecar and audited Transport driver are available.",
    )


def build_weixin_adapter(config, workspace, identity, conversations, dedup, runtime):
    status = check_weixin_startup(config, workspace)
    if not status.available:
        return None, None, status
    sidecar = WeixinSidecarClient(
        node_path=config.node_path,
        entry=_resolve_sidecar_entry(config.sidecar_entry, workspace),
        workspace=workspace,
    )
    binding = WeixinBindingService(
        identity.store,
        sidecar,
        workspace,
        timeout_seconds=config.binding_timeout_seconds,
    )
    adapter = WeixinClawAdapter(
        config, sidecar, binding, identity, conversations, dedup, runtime
    )
    return adapter, binding, status


def _resolve_sidecar_entry(value: str, workspace: Path) -> Path:
    configured = Path(value).expanduser()
    if configured.is_absolute():
        entry = configured.resolve()
        entry.relative_to(workspace.resolve())
        return entry
    install_root = Path(__file__).resolve().parents[3]
    entry = (install_root / configured).resolve()
    entry.relative_to(install_root)
    return entry
