import json
import logging

from agent.mcp.startup import check_mcp_startup


def test_missing_mcp_config_is_disabled(tmp_path):
    result = check_mcp_startup(tmp_path / "config")

    assert result.specs == ()
    assert result.status.state == "disabled"
    assert result.status.code == "MCP_CONFIG_MISSING"


def test_empty_mcp_config_is_disabled(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")

    result = check_mcp_startup(config_dir)

    assert result.specs == ()
    assert result.status.state == "disabled"
    assert result.status.code == "MCP_DISABLED"


def test_valid_mcp_config_is_available(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "local": {"command": "python", "args": ["server.py"]},
                }
            }
        ),
        encoding="utf-8",
    )

    result = check_mcp_startup(config_dir)

    assert len(result.specs) == 1
    assert result.specs[0].server_id == "local"
    assert result.status.state == "available"
    assert result.status.details == {"server_count": 1}


def test_invalid_mcp_config_disables_only_mcp_and_logs_safe_warning(tmp_path, caplog):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    secret = "super-secret-token"
    (config_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "remote": {
                        "url": "https://example.test/mcp",
                        "headers": {"Authorization": f"Bearer {secret}"},
                        "cwd": "../outside",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="zcagent.agent.mcp"):
        result = check_mcp_startup(config_dir)

    assert result.specs == ()
    assert result.status.state == "unavailable"
    assert result.status.code == "MCP_CONFIG_INVALID"
    assert result.status.details["config_file"] == "mcp.json"
    assert secret not in caplog.text
    assert "outside" not in caplog.text


def test_missing_mcp_placeholder_is_unavailable_without_leaking_name_value(
    tmp_path,
    monkeypatch,
    caplog,
):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.delenv("MCP_PRIVATE_TOKEN", raising=False)
    (config_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "remote": {
                        "url": "https://example.test/mcp",
                        "headers": {"Authorization": "Bearer ${MCP_PRIVATE_TOKEN}"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="zcagent.agent.mcp"):
        result = check_mcp_startup(config_dir)

    assert result.status.state == "unavailable"
    assert result.specs == ()
    assert "MCP_PRIVATE_TOKEN" not in caplog.text
