"""Local MCP HTTP/SSE Server used only by unit tests."""

from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("streamable-http", "sse"), required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    server = FastMCP("zhice-http-test", host="127.0.0.1", port=args.port)

    @server.tool()
    def echo(text: str) -> str:
        """Echo text over a remote transport."""

        return f"remote:{text}"

    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
