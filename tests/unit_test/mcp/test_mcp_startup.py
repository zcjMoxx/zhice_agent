import logging

import yaml

from agent.mcp.startup import check_mcp_startup


def _write_mcp(config_dir, servers):
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yml").write_text(
        yaml.safe_dump(
            {"schema_version": 1, "mcp": {"servers": servers}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_missing_mcp_config_is_disabled(tmp_path):
    result = check_mcp_startup(tmp_path / "config")

    assert result.specs == ()
    assert result.status.state == "disabled"
    assert result.status.code == "MCP_CONFIG_MISSING"


def test_empty_mcp_config_is_disabled(tmp_path):
    config_dir = tmp_path / "config"
    _write_mcp(config_dir, {})

    result = check_mcp_startup(config_dir)

    assert result.specs == ()
    assert result.status.state == "disabled"
    assert result.status.code == "MCP_DISABLED"


def test_valid_mcp_config_is_available(tmp_path):
    config_dir = tmp_path / "config"
    _write_mcp(config_dir, {"local": {"command": "python", "args": ["server.py"]}})

    result = check_mcp_startup(config_dir)

    assert len(result.specs) == 1
    assert result.specs[0].server_id == "local"
    assert result.status.state == "available"
    assert result.status.details == {"server_count": 1}


def test_invalid_server_degrades_only_that_server(tmp_path, caplog):
    config_dir = tmp_path / "config"
    _write_mcp(
        config_dir,
        {
            "valid": {"command": "python"},
            "invalid": {"command": "python", "cwd": "../outside"},
        },
    )

    with caplog.at_level(logging.WARNING, logger="zcagent.agent.mcp"):
        result = check_mcp_startup(config_dir)

    assert [spec.server_id for spec in result.specs] == ["valid"]
    assert result.status.state == "degraded"
    assert result.status.code == "MCP_CONFIG_PARTIAL"
    assert result.status.details == {
        "server_count": 1,
        "invalid_server_count": 1,
        "invalid_server_ids": ["invalid"],
    }
    assert "mcp.server_config_invalid" in caplog.text


def test_invalid_mcp_config_disables_only_mcp_and_logs_safe_warning(tmp_path, caplog):
    config_dir = tmp_path / "config"
    secret = "super-secret-token"
    _write_mcp(
        config_dir,
        {
                    "remote": {
                        "url": "https://example.test/mcp",
                        "headers": {"Authorization": f"Bearer {secret}"},
                        "cwd": "../outside",
                    }
        },
    )

    with caplog.at_level(logging.WARNING, logger="zcagent.agent.mcp"):
        result = check_mcp_startup(config_dir)

    assert result.specs == ()
    assert result.status.state == "unavailable"
    assert result.status.code == "MCP_CONFIG_INVALID"
    assert result.status.details["config_file"] == "config.yml"
    assert secret not in caplog.text
    assert "outside" not in caplog.text


def test_missing_mcp_placeholder_is_unavailable_without_leaking_name_value(
    tmp_path,
    monkeypatch,
    caplog,
):
    config_dir = tmp_path / "config"
    monkeypatch.delenv("MCP_PRIVATE_TOKEN", raising=False)
    _write_mcp(
        config_dir,
        {
                    "remote": {
                        "url": "https://example.test/mcp",
                        "headers": {"Authorization": "Bearer ${MCP_PRIVATE_TOKEN}"},
                    }
        },
    )

    with caplog.at_level(logging.WARNING, logger="zcagent.agent.mcp"):
        result = check_mcp_startup(config_dir)

    assert result.status.state == "unavailable"
    assert result.specs == ()
    assert "MCP_PRIVATE_TOKEN" not in caplog.text
