"""Fixed subprocess entry for Owner-initiated Ctrip account login."""

from __future__ import annotations

import argparse

from integrations.hotel_browser_mcp.ctrip import (
    HotelBrowserError,
    login_ctrip,
    write_safe_status,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--manual-timeout-seconds", type=int, default=300)
    args = parser.parse_args()
    try:
        result = login_ctrip(
            args.workspace,
            headless=False,
            manual_timeout_seconds=max(30, min(args.manual_timeout_seconds, 600)),
        )
    except HotelBrowserError as exc:
        result = {"state": "unavailable", "code": exc.code, "message": exc.message}
    except Exception:
        result = {
            "state": "unavailable",
            "code": "HOTEL_LOGIN_FAILED",
            "message": "The Ctrip login helper failed.",
        }
    write_safe_status(args.workspace, result)
    return 0 if result.get("state") == "authenticated" else 1


if __name__ == "__main__":
    raise SystemExit(main())
