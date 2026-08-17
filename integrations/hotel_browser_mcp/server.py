"""Catalog-level read-only MCP for account-observed Ctrip hotel prices."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from integrations.hotel_browser_mcp.ctrip import (
    HotelBrowserError,
    check_ctrip_login,
    search_ctrip_hotels,
)

server = FastMCP("zhice-hotel-browser-readonly")


@server.tool()
async def check_hotel_login_status() -> dict[str, Any]:
    """Check the dedicated Ctrip browser profile without exposing credentials."""

    return await _run(check_ctrip_login, _workspace())


@server.tool()
async def search_hotels(
    city: str,
    checkin: str,
    checkout: str,
    keyword: str = "",
    rooms: int = 1,
    adults: int = 2,
    max_results: int = 5,
) -> dict[str, Any]:
    """Search Ctrip in read-only mode and return bounded account-observed prices."""

    return await _run(
        search_ctrip_hotels,
        _workspace(),
        city=city,
        checkin=checkin,
        checkout=checkout,
        keyword=keyword,
        rooms=rooms,
        adults=adults,
        max_results=max_results,
    )


async def _run(function, *args, **kwargs) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except HotelBrowserError as exc:
        return {"status": "error", "code": exc.code, "message": exc.message}
    except Exception as exc:
        return {
            "status": "error",
            "code": "HOTEL_SOURCE_UNAVAILABLE",
            "message": "The local read-only hotel source is unavailable.",
            "error_type": type(exc).__name__,
        }


def _workspace() -> Path:
    value = os.getenv("ZHICE_AGENT_WORKSPACE", "").strip()
    if not value:
        raise HotelBrowserError(
            "HOTEL_SOURCE_UNAVAILABLE",
            "ZHICE_AGENT_WORKSPACE is not configured for the hotel source.",
        )
    return Path(value).expanduser().resolve()


if __name__ == "__main__":
    server.run(transport="stdio")
