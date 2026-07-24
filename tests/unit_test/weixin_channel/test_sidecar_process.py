from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from agent.channels.weixin.sidecar import WeixinSidecarClient, WeixinSidecarError


@pytest.mark.integration
def test_real_node_sidecar_smoke_and_workspace_lease(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    major = int(
        subprocess.run(
            [node, "--version"], capture_output=True, text=True, check=True, timeout=5
        ).stdout.strip().lstrip("v").split(".", 1)[0]
    )
    if major < 22:
        pytest.skip("Node.js 22 or newer is required")
    entry = (
        Path(__file__).resolve().parents[3]
        / "integrations"
        / "weixin_sidecar"
        / "dist"
        / "main.js"
    )
    first = WeixinSidecarClient(node_path=node, entry=entry, workspace=tmp_path)
    second = WeixinSidecarClient(node_path=node, entry=entry, workspace=tmp_path)
    first.start()
    try:
        health = first.request("health.get")
        assert health["type"] == "health.status"
        assert health["status"] == "available"
        assert health["code"] == "OK"
        with pytest.raises(WeixinSidecarError, match="lease"):
            second.start()
    finally:
        first.stop()
