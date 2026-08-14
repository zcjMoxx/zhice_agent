from __future__ import annotations

import json
import os
import sys

import pytest

from agent.mcp.runtime import McpRuntime
from agent.protocols.auth import ActorContext
from agent.protocols.mcp import McpServerSpec

pytestmark = pytest.mark.integration


def test_real_amap_catalog_smoke(tmp_path):
    _enabled("AMAP", required=("AMAP_MAPS_API_KEY",))
    runtime = McpRuntime(
        [McpServerSpec(server_id="amap-maps", transport="stdio", command="npx", args=("-y", "@amap/amap-maps-mcp-server@0.0.8"), env={"AMAP_MAPS_API_KEY": os.environ["AMAP_MAPS_API_KEY"]}, startup_timeout_seconds=60, connect_timeout_seconds=30, call_timeout_seconds=30)],
        workspace=tmp_path,
    )
    try:
        _assert_ready(runtime, "amap-maps")
    finally:
        runtime.close()


def test_real_tavily_catalog_smoke(tmp_path):
    _enabled("TAVILY", required=("TAVILY_API_KEY",))
    runtime = McpRuntime(
        [McpServerSpec(server_id="tavily", transport="streamable_http", url=os.getenv("TAVILY_MCP_URL", "https://mcp.tavily.com/mcp"), headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"}, startup_timeout_seconds=30, connect_timeout_seconds=30, call_timeout_seconds=30)],
        workspace=tmp_path,
    )
    try:
        _assert_ready(runtime, "tavily")
    finally:
        runtime.close()


def test_real_12306_query_catalog_smoke(tmp_path):
    _enabled("12306", required=("ZHICE_12306_MCP_COMMAND", "ZHICE_12306_MCP_ARGS_JSON"))
    args = tuple(json.loads(os.environ["ZHICE_12306_MCP_ARGS_JSON"]))
    runtime = McpRuntime(
        [McpServerSpec(server_id="12306", transport="stdio", command=os.environ["ZHICE_12306_MCP_COMMAND"], args=args, startup_timeout_seconds=60, connect_timeout_seconds=30, call_timeout_seconds=30)],
        workspace=tmp_path,
    )
    try:
        _assert_ready(runtime, "12306")
    finally:
        runtime.close()


def test_real_open_meteo_query_smoke(tmp_path):
    _enabled("OPEN_METEO")
    runtime = McpRuntime(
        [McpServerSpec(server_id="open-meteo", transport="stdio", command=sys.executable, args=("-m", "integrations.open_meteo_mcp.server"), startup_timeout_seconds=20, connect_timeout_seconds=15, call_timeout_seconds=30)],
        workspace=tmp_path,
    )
    try:
        _assert_ready(runtime, "open-meteo")
        tool = next(item for item in runtime.tools_for_actor(_actor(), tmp_path / "files") if item.name.endswith("geocode_place"))
        result = tool.execute({"name": "大理", "count": 1, "language": "zh"})
        assert not result.is_error
    finally:
        runtime.close()


def test_real_xhs_login_search_detail_and_expired_cookie_smoke(tmp_path):
    _enabled("XHS", required=("XHS_READONLY_UPSTREAM_URL", "XHS_READONLY_COOKIE_DIR", "XHS_READONLY_COOKIE_FILE", "XHS_SMOKE_FEED_ID", "XHS_SMOKE_XSEC_TOKEN"))
    env = {key: os.environ[key] for key in ("XHS_READONLY_UPSTREAM_URL", "XHS_READONLY_COOKIE_DIR", "XHS_READONLY_COOKIE_FILE")}
    if os.getenv("XHS_READONLY_UPSTREAM_AUTHORIZATION"):
        env["XHS_READONLY_UPSTREAM_AUTHORIZATION"] = os.environ["XHS_READONLY_UPSTREAM_AUTHORIZATION"]
    runtime = McpRuntime(
        [McpServerSpec(server_id="xhs-readonly", transport="stdio", command=sys.executable, args=("-m", "integrations.xhs_readonly_mcp.server"), env=env, startup_timeout_seconds=30, connect_timeout_seconds=30, call_timeout_seconds=60)],
        workspace=tmp_path,
    )
    try:
        _assert_ready(runtime, "xhs-readonly")
        tools = {item.name: item for item in runtime.tools_for_actor(_actor(), tmp_path / "files")}
        assert set(tools) == {
            "mcp__xhs-readonly__check_login_status",
            "mcp__xhs-readonly__search_notes",
            "mcp__xhs-readonly__get_note_detail",
        }
        assert not tools["mcp__xhs-readonly__check_login_status"].execute({}).is_error
        assert not tools["mcp__xhs-readonly__search_notes"].execute({"keyword": "大理", "max_results": 3}).is_error
        assert not tools["mcp__xhs-readonly__get_note_detail"].execute({"feed_id": os.environ["XHS_SMOKE_FEED_ID"], "xsec_token": os.environ["XHS_SMOKE_XSEC_TOKEN"], "include_comments": False}).is_error
        # Cookie-expiry degradation is intentionally a separate operator action:
        # point XHS_READONLY_COOKIE_FILE at an expired fixture and rerun with
        # ZHICE_TRAVEL_SMOKE_XHS_EXPECT_AUTH_REQUIRED=1.
        if os.getenv("ZHICE_TRAVEL_SMOKE_XHS_EXPECT_AUTH_REQUIRED") == "1":
            expired = tools["mcp__xhs-readonly__check_login_status"].execute({})
            assert "TRAVEL_SOURCE_AUTH_REQUIRED" in expired.output
    finally:
        runtime.close()


def _enabled(source: str, *, required=()):
    if os.getenv(f"ZHICE_TRAVEL_SMOKE_{source}") != "1":
        pytest.skip(f"set ZHICE_TRAVEL_SMOKE_{source}=1 to run real smoke")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        pytest.skip(f"missing explicit smoke credentials/config: {', '.join(missing)}")


def _assert_ready(runtime, server_id):
    status = next(item for item in runtime.snapshot().servers if item.server_id == server_id)
    assert status.state == "ready"
    assert status.tool_count > 0


def _actor():
    return ActorContext(actor_type="user", user_id="smoke-user", username="smoke", display_name="Smoke", role_keys=frozenset({"viewer"}), permission_keys=frozenset(), channel="web")

