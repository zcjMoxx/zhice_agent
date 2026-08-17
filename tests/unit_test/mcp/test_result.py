from __future__ import annotations

import base64
import json
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


def test_compacts_oversized_structured_search_without_failing(tmp_path):
    result = SimpleNamespace(
        isError=False,
        structuredContent={
            "query": "河南攻略",
            "results": [
                {
                    "title": f"结果 {index}",
                    "url": f"https://example.com/{index}",
                    "content": "摘要" * 3000,
                    "raw_content": "原始正文" * 10000,
                }
                for index in range(20)
            ],
        },
        content=[],
    )

    normalized = normalize_mcp_result(
        result,
        server_id="tavily",
        files_dir=tmp_path / "files",
        temp_root=tmp_path / "runtime",
        artifact_gateway=McpArtifactGateway(),
    )
    payload = json.loads(normalized.output)

    assert not normalized.is_error
    assert normalized.metadata["code"] == "MCP_OK"
    assert normalized.metadata["structured_truncated"] is True
    assert payload["truncated"] is True
    assert len(payload["results"]) == 5
    assert "raw_content" not in payload["results"][0]
    assert len(payload["results"][0]["content"]) <= 1001


def test_long_text_preserves_head_and_tail_with_fixed_output_budget(tmp_path):
    result = SimpleNamespace(
        isError=False,
        structuredContent=None,
        content=[SimpleNamespace(type="text", text="HEAD" + "a" * 9000 + "b" * 9000 + "TAIL")],
    )

    normalized = normalize_mcp_result(
        result,
        server_id="long-list",
        files_dir=tmp_path / "files",
        temp_root=tmp_path / "runtime",
        artifact_gateway=McpArtifactGateway(),
    )

    assert normalized.output.startswith("HEAD")
    assert normalized.output.endswith("TAIL")
    assert "[truncated middle]" in normalized.output
    assert len(normalized.output) == 12000
    assert normalized.metadata["truncated"] is True
    assert normalized.metadata["original_chars"] == 18008
    assert normalized.metadata["returned_chars"] == 12000
