from __future__ import annotations

import pytest

from agent.mcp.artifacts import McpArtifactError, McpArtifactGateway


def test_saves_artifact_under_actor_files_root_and_sanitizes_name(tmp_path):
    gateway = McpArtifactGateway()

    target = gateway.save_bytes(tmp_path / "files", "mail", "../report?.txt", b"hello")

    assert target.read_bytes() == b"hello"
    assert target.parent == (tmp_path / "files" / "mcp" / "mail").resolve()
    assert target.name == "report_.txt"


def test_rejects_temp_file_outside_server_sandbox(tmp_path):
    gateway = McpArtifactGateway()
    sandbox = tmp_path / "runtime" / "tmp"
    sandbox.mkdir(parents=True)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(McpArtifactError, match="outside"):
        gateway.import_temp_file(tmp_path / "files", "mail", sandbox, outside)
