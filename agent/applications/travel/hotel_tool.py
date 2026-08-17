"""Bounded read-only hotel observation Tool for travel parent and child Agents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.applications.travel.source_ledger import TravelSourceLedger
from agent.protocols.tool import ToolExecutionContext, ToolResult
from agent.tools.base import BaseTool
from integrations.hotel_browser_mcp.ctrip import HotelBrowserError, search_ctrip_hotels


class SearchTravelHotelsTool(BaseTool):
    """Expose account-observed Ctrip prices without exposing account state."""

    name = "search_travel_hotels"
    description = (
        "Search Ctrip read-only for dated hotel or homestay cards. Use one room and two adults "
        "as the current bounded quote occupancy. Return only observed planning references; never "
        "book, pay, or claim prices are guaranteed."
    )
    parameters = {
        "type": "object",
        "properties": {
            "city": {"type": "string", "minLength": 1, "maxLength": 80},
            "checkin": {"type": "string", "format": "date"},
            "checkout": {"type": "string", "format": "date"},
            "keyword": {"type": "string", "maxLength": 120},
            "accommodation_type": {
                "type": "string",
                "enum": ["any", "hotel", "homestay", "apartment"],
            },
            "min_price_cny": {"type": ["number", "null"], "minimum": 0},
            "max_price_cny": {"type": ["number", "null"], "minimum": 0},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["city", "checkin", "checkout"],
        "additionalProperties": False,
    }

    def __init__(self, workspace: Path | str, ledger: TravelSourceLedger):
        super().__init__(workspace)
        self.ledger = ledger

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        return self._search(args, session_id="")

    def execute_with_context(
        self,
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        if context.channel != "travel" or not context.session_id:
            return _error(
                "TRAVEL_HOTEL_ACCESS_DENIED",
                "Hotel observations are available only inside an active travel plan.",
            )
        return self._search(args, session_id=context.root_session_id or context.session_id)

    def _search(self, args: dict[str, Any], *, session_id: str) -> ToolResult:
        normalized = _arguments(args)
        if isinstance(normalized, ToolResult):
            return normalized
        guard = self.ledger.admit_call(session_id, self.name, normalized)
        if guard is not None:
            return guard
        keyword = _search_keyword(str(normalized.get("keyword") or ""))
        accommodation_type = str(normalized.get("accommodation_type") or "any")
        if accommodation_type in {"homestay", "apartment"}:
            marker = "民宿" if accommodation_type == "homestay" else "公寓"
            if marker not in keyword:
                keyword = " ".join(item for item in (keyword, marker) if item)
        try:
            payload = search_ctrip_hotels(
                self.workspace,
                city=str(normalized["city"]),
                checkin=str(normalized["checkin"]),
                checkout=str(normalized["checkout"]),
                keyword=keyword,
                rooms=1,
                adults=2,
                max_results=10,
            )
            payload = _bounded_payload(
                payload,
                minimum=normalized.get("min_price_cny"),
                maximum=normalized.get("max_price_cny"),
                limit=int(normalized.get("max_results") or 5),
                accommodation_type=accommodation_type,
            )
            result = ToolResult(
                output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                metadata={
                    "code": str(payload.get("code") or "OK"),
                    "tool_name": self.name,
                    "provider": "ctrip-account-observation",
                },
            )
        except HotelBrowserError as exc:
            result = _error(exc.code, exc.message)
        except Exception as exc:  # pragma: no cover - defensive external browser boundary
            result = ToolResult(
                output=json.dumps(
                    {
                        "status": "error",
                        "code": "HOTEL_SOURCE_UNAVAILABLE",
                        "message": "The local read-only hotel source is unavailable.",
                        "error_type": type(exc).__name__,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                is_error=True,
                metadata={"code": "HOTEL_SOURCE_UNAVAILABLE", "tool_name": self.name},
            )
        self.ledger.observe(session_id, self.name, result, normalized)
        return result


def _arguments(args: object) -> dict[str, Any] | ToolResult:
    if not isinstance(args, dict):
        return _error("HOTEL_QUERY_INVALID", "Hotel search arguments must be an object.")
    allowed = {
        "city",
        "checkin",
        "checkout",
        "keyword",
        "accommodation_type",
        "min_price_cny",
        "max_price_cny",
        "max_results",
    }
    if set(args) - allowed:
        return _error("HOTEL_QUERY_INVALID", "Hotel search contains unsupported fields.")
    city = str(args.get("city") or "").strip()
    checkin = str(args.get("checkin") or "").strip()
    checkout = str(args.get("checkout") or "").strip()
    if not city or len(city) > 80 or not checkin or not checkout:
        return _error("HOTEL_QUERY_INVALID", "City, check-in, and check-out are required.")
    minimum = _optional_price(args.get("min_price_cny"))
    maximum = _optional_price(args.get("max_price_cny"))
    if minimum is False or maximum is False or (
        isinstance(minimum, float) and isinstance(maximum, float) and minimum > maximum
    ):
        return _error("HOTEL_QUERY_INVALID", "Hotel price range is invalid.")
    try:
        limit = int(args.get("max_results", 5))
    except (TypeError, ValueError):
        limit = 0
    if isinstance(args.get("max_results"), bool) or not 1 <= limit <= 10:
        return _error("HOTEL_QUERY_INVALID", "max_results must be between 1 and 10.")
    accommodation_type = str(args.get("accommodation_type") or "any").strip()
    if accommodation_type not in {"any", "hotel", "homestay", "apartment"}:
        return _error("HOTEL_QUERY_INVALID", "Accommodation type is invalid.")
    return {
        "city": city,
        "checkin": checkin,
        "checkout": checkout,
        "keyword": str(args.get("keyword") or "").strip()[:120],
        "accommodation_type": accommodation_type,
        "min_price_cny": minimum if isinstance(minimum, float) else None,
        "max_price_cny": maximum if isinstance(maximum, float) else None,
        "max_results": limit,
    }


def _optional_price(value: object) -> float | None | bool:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number if 0 <= number <= 1_000_000 else False


def _search_keyword(value: str) -> str:
    """Do not send planning preferences to Ctrip as if they were hotel names."""

    keyword = " ".join(str(value or "").split())
    if not keyword:
        return ""
    preference_markers = (
        "舒适型",
        "位置便利",
        "交通便利",
        "中低价",
        "中等价位",
        "性价比",
        "预算友好",
        "靠近景点",
    )
    concrete_markers = (
        "酒店",
        "宾馆",
        "旅馆",
        "客栈",
        "民宿",
        "公寓",
        "全季",
        "汉庭",
        "亚朵",
        "如家",
        "锦江",
    )
    if any(marker in keyword for marker in preference_markers) and not any(
        marker in keyword for marker in concrete_markers
    ):
        return ""
    return keyword[:120]


def _bounded_payload(
    payload: dict[str, Any],
    *,
    minimum: object,
    maximum: object,
    limit: int,
    accommodation_type: str,
) -> dict[str, Any]:
    hotels = payload.get("hotels") if isinstance(payload, dict) else None
    rows: list[dict[str, Any]] = []
    for item in hotels if isinstance(hotels, list) else []:
        if not isinstance(item, dict):
            continue
        price = item.get("price_cny")
        numeric = float(price) if isinstance(price, (int, float)) and not isinstance(price, bool) else None
        if isinstance(minimum, float) and (numeric is None or numeric < minimum):
            continue
        if isinstance(maximum, float) and (numeric is None or numeric > maximum):
            continue
        rows.append(
            {
                "name": str(item.get("name") or "").strip()[:120],
                "rating": item.get("rating"),
                "observed_price_per_night_cny": numeric,
                "price_text": str(item.get("price_text") or "").strip()[:40],
                "summary": str(item.get("summary") or "").strip()[:500],
                "source_url": str(item.get("source_url") or "").strip()[:1000],
            }
        )
        if len(rows) >= limit:
            break
    return {
        "status": str(payload.get("status") or "success"),
        "code": str(payload.get("code") or "OK"),
        "provider": "ctrip-account-observation",
        "source_type": "live_query",
        "freshness": "live",
        "retrieved_at": str(payload.get("retrieved_at") or ""),
        "query": payload.get("query") if isinstance(payload.get("query"), dict) else {},
        "accommodation_type": accommodation_type,
        "price_label": "Ctrip account-observed price",
        "disclaimer": "Prices are observed planning references and may change.",
        "count": len(rows),
        "hotels": rows,
    }


def _error(code: str, message: str) -> ToolResult:
    return ToolResult(
        output=json.dumps(
            {"status": "error", "code": code, "message": message},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        is_error=True,
        metadata={"code": code, "tool_name": SearchTravelHotelsTool.name},
    )


__all__ = ["SearchTravelHotelsTool"]
