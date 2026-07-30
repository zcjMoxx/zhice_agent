"""Local MCP stdio Server used only by unit tests."""

from __future__ import annotations

import asyncio
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


@server.tool()
async def slow_echo(text: str, delay_seconds: float = 2.0) -> str:
    """Wait before returning so client cancellation can be verified."""

    await asyncio.sleep(delay_seconds)
    return f"slow:{text}"


if __name__ == "__main__":
    server.run(transport="stdio")
