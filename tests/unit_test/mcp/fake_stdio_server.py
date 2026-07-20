"""Local MCP stdio Server used only by unit tests."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel

server = FastMCP("zhice-test-mcp")


class Approval(BaseModel):
    code: str


@server.tool()
def echo(text: str) -> str:
    """Echo text from the caller."""

    return f"echo:{text}"


@server.tool()
def current_directory() -> str:
    """Return the Server process working directory."""

    return str(Path.cwd())


@server.tool()
async def request_code(ctx: Context) -> str:
    """Ask the caller for a short code through MCP Elicitation."""

    result = await ctx.elicit("Provide code", Approval)
    if result.action != "accept":
        return result.action
    return f"code:{result.data.code}"


if __name__ == "__main__":
    server.run(transport="stdio")
