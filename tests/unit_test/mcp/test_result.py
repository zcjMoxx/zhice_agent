from __future__ import annotations

import base64
from types import SimpleNamespace

from agent.mcp.artifacts import McpArtifactGateway
from agent.mcp.result import normalize_mcp_result


def test_normalizes_text_structured_content_and_binary_artifact(tmp_path):
    result = SimpleNamespace(
        isError=False,
        structuredContent={"count": 1},
        content=[
            SimpleNamespace(type="text", text="done"),
            SimpleNamespace(
                type="image",
                data=base64.b64encode(b"png-bytes").decode("ascii"),
                mimeType="image/png",
            ),
        ],
    )

    normalized = normalize_mcp_result(
        result,
        server_id="image",
        files_dir=tmp_path / "files",
        temp_root=tmp_path / "runtime",
        artifact_gateway=McpArtifactGateway(),
    )

    assert not normalized.is_error
    assert '"count":1' in normalized.output
    assert "done" in normalized.output
    assert normalized.metadata["artifacts"] == ["mcp/image/image-2.png"]
    assert (tmp_path / "files" / "mcp" / "image" / "image-2.png").read_bytes() == b"png-bytes"
