from __future__ import annotations

import json

import pytest

from agent.mcp.config import McpConfigError, load_mcp_server_specs


def test_loads_stdio_http_sse_and_env_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TEST_TOKEN", "secret-token")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "local": {"command": "python", "args": ["server.py"], "cwd": "work"},
                    "remote": {
                        "url": "https://example.com/mcp",
                        "headers": {"Authorization": "Bearer ${MCP_TEST_TOKEN}"},
                    },
                    "legacy": {"url": "https://example.com/sse", "transport": "sse"},
                }
            }
        ),
        encoding="utf-8",
    )

    specs = load_mcp_server_specs(config_dir)

    assert [spec.transport for spec in specs] == ["stdio", "streamable_http", "sse"]
    assert specs[1].headers["Authorization"] == "Bearer secret-token"
    assert specs[0].cwd == "work"


@pytest.mark.parametrize("cwd", ["../users", "C:/Users/example"])
def test_rejects_stdio_cwd_outside_temp_sandbox(tmp_path, cwd):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"local": {"command": "python", "cwd": cwd}}}),
        encoding="utf-8",
    )

    with pytest.raises(McpConfigError, match="temp sandbox"):
        load_mcp_server_specs(config_dir)


def test_rejects_unknown_fields(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"local": {"command": "python", "tools": []}}}),
        encoding="utf-8",
    )

    with pytest.raises(McpConfigError, match="Unknown MCP config fields"):
        load_mcp_server_specs(config_dir)
