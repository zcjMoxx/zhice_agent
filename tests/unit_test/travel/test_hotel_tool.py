from __future__ import annotations

import json

from agent.applications.travel.hotel_tool import SearchTravelHotelsTool
from agent.applications.travel.source_ledger import TravelSourceLedger
from agent.protocols.auth import ActorContext
from agent.protocols.tool import ToolExecutionContext
from integrations.hotel_browser_mcp.ctrip import HotelBrowserError


def test_hotel_tool_filters_and_records_account_observed_prices(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agent.applications.travel.hotel_tool.search_ctrip_hotels",
        lambda *_args, **_kwargs: {
            "status": "success",
            "code": "OK",
            "retrieved_at": "2026-08-16T00:00:00Z",
            "query": {"city": "贵阳"},
            "hotels": [
                {
                    "name": "舒适酒店",
                    "rating": 4.7,
                    "price_cny": 288,
                    "price_text": "¥288",
                    "summary": "舒适酒店 评分 4.7 ¥288",
                    "source_url": "https://hotels.ctrip.com/hotels/detail/1",
                },
                {
                    "name": "豪华酒店",
                    "rating": 4.8,
                    "price_cny": 688,
                    "price_text": "¥688",
                    "summary": "豪华酒店 评分 4.8 ¥688",
                    "source_url": "https://hotels.ctrip.com/hotels/detail/2",
                },
            ],
        },
    )
    ledger = TravelSourceLedger()
    tool = SearchTravelHotelsTool(tmp_path, ledger)

    result = tool.execute_with_context(
        {
            "city": "贵阳",
            "checkin": "2026-09-01",
            "checkout": "2026-09-03",
            "max_price_cny": 400,
            "max_results": 5,
        },
        _context(),
    )

    payload = json.loads(result.output)
    assert result.is_error is False
    assert payload["provider"] == "ctrip-account-observation"
    assert payload["count"] == 1
    assert payload["hotels"][0]["name"] == "舒适酒店"
    assert payload["hotels"][0]["observed_price_per_night_cny"] == 288
    assert ledger.snapshot("travel-session").successful == frozenset({"lodging"})

    duplicate = tool.execute_with_context(
        {
            "city": "贵阳",
            "checkin": "2026-09-01",
            "checkout": "2026-09-03",
            "max_price_cny": 400,
            "max_results": 5,
        },
        _context(),
    )
    assert duplicate.is_error is True
    assert duplicate.metadata["code"] == "TRAVEL_SOURCE_ALREADY_QUERIED"


def test_hotel_tool_keeps_auth_failure_structured(monkeypatch, tmp_path):
    def fail(*_args, **_kwargs):
        raise HotelBrowserError(
            "HOTEL_MANUAL_VERIFICATION_REQUIRED",
            "Ctrip requires manual verification.",
        )

    monkeypatch.setattr("agent.applications.travel.hotel_tool.search_ctrip_hotels", fail)
    ledger = TravelSourceLedger()
    result = SearchTravelHotelsTool(tmp_path, ledger).execute_with_context(
        {
            "city": "贵阳",
            "checkin": "2026-09-01",
            "checkout": "2026-09-03",
        },
        _context(),
    )

    assert result.is_error is True
    assert result.metadata["code"] == "HOTEL_MANUAL_VERIFICATION_REQUIRED"
    snapshot = ledger.snapshot("travel-session")
    assert snapshot.attempted == frozenset({"lodging"})
    assert snapshot.retry_required == ()


def test_hotel_tool_drops_generic_preference_keyword(monkeypatch, tmp_path):
    captured = {}

    def search(*_args, **kwargs):
        captured.update(kwargs)
        return {"status": "success", "code": "OK", "hotels": []}

    monkeypatch.setattr("agent.applications.travel.hotel_tool.search_ctrip_hotels", search)
    SearchTravelHotelsTool(tmp_path, TravelSourceLedger()).execute_with_context(
        {
            "city": "大理",
            "checkin": "2026-08-18",
            "checkout": "2026-08-20",
            "keyword": "舒适型 位置便利 中低价位",
        },
        _context(),
    )

    assert captured["keyword"] == ""


def _context() -> ToolExecutionContext:
    return ToolExecutionContext(
        actor=ActorContext(
            actor_type="user",
            user_id="user-a",
            username="user-a",
            display_name="User A",
            role_keys=frozenset({"viewer"}),
            permission_keys=frozenset(),
            channel="web",
        ),
        session_id="travel-session",
        turn_id="travel-turn",
        turn_index=1,
        channel="travel",
    )
