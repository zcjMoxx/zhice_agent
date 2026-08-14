from __future__ import annotations

import pytest
import yaml

from agent.mcp.config import McpConfigError, load_mcp_server_specs


def _write_mcp(config_dir, servers):
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yml").write_text(
        yaml.safe_dump(
            {"schema_version": 1, "mcp": {"servers": servers}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_loads_stdio_http_sse_and_env_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TEST_TOKEN", "secret-token")
    config_dir = tmp_path / "config"
    _write_mcp(
        config_dir,
        {
                    "local": {"command": "python", "args": ["server.py"], "cwd": "work"},
                    "remote": {
                        "url": "https://example.com/mcp",
                        "headers": {"Authorization": "Bearer ${MCP_TEST_TOKEN}"},
                    },
                    "legacy": {
                        "url": "https://example.com/sse",
                        "transport": "sse",
                        "proxy_mode": "environment",
                    },
        },
    )

    specs = load_mcp_server_specs(config_dir)

    assert [spec.transport for spec in specs] == ["stdio", "streamable_http", "sse"]
    assert specs[1].headers["Authorization"] == "Bearer secret-token"
    assert specs[1].proxy_mode == "direct"
    assert specs[2].proxy_mode == "environment"
    assert specs[0].cwd == "work"


@pytest.mark.parametrize("cwd", ["../users", "C:/Users/example"])
def test_rejects_stdio_cwd_outside_temp_sandbox(tmp_path, cwd):
    config_dir = tmp_path / "config"
    _write_mcp(config_dir, {"local": {"command": "python", "cwd": cwd}})

    with pytest.raises(McpConfigError, match="temp sandbox"):
        load_mcp_server_specs(config_dir)


def test_rejects_unknown_fields(tmp_path):
    config_dir = tmp_path / "config"
    _write_mcp(config_dir, {"local": {"command": "python", "tools": []}})

    with pytest.raises(McpConfigError, match="Unknown MCP config fields"):
        load_mcp_server_specs(config_dir)


@pytest.mark.parametrize("proxy_mode", ["auto", "", True])
def test_rejects_invalid_remote_proxy_mode(tmp_path, proxy_mode):
    config_dir = tmp_path / "config"
    _write_mcp(
        config_dir,
        {
            "remote": {
                "url": "https://example.com/mcp",
                "proxy_mode": proxy_mode,
            }
        },
    )

    with pytest.raises(McpConfigError, match="proxy_mode"):
        load_mcp_server_specs(config_dir)


def test_rejects_proxy_mode_for_stdio_server(tmp_path):
    config_dir = tmp_path / "config"
    _write_mcp(
        config_dir,
        {"local": {"command": "python", "proxy_mode": "environment"}},
    )

    with pytest.raises(McpConfigError, match="only valid for remote"):
        load_mcp_server_specs(config_dir)
