from __future__ import annotations

from agent.applications.travel.source_ledger import (
    TravelSourceLedger,
    preferred_travel_tool_names,
    source_category,
)
from agent.protocols.tool import ToolResult


def test_source_ledger_tracks_expected_attempted_and_successful_categories():
    ledger = TravelSourceLedger()
    ledger.register_expected(
        "session-travel",
        [
            "mcp__amap-maps__maps_text_search",
            "mcp__open-meteo__get_forecast",
            "mcp__12306__get-tickets",
            "mcp__tavily__tavily_search",
            "mcp__xhs-readonly__search_notes",
        ],
    )
    ledger.observe(
        "session-travel",
        "mcp__amap-maps__maps_text_search",
        ToolResult(output='{"pois":[{"name":"大理古城"}]}', metadata={"code": "MCP_OK"}),
    )
    ledger.observe(
        "session-travel",
        "mcp__open-meteo__get_forecast",
        ToolResult(
            output='{"status":"error","code":"TRAVEL_WEATHER_OUT_OF_RANGE"}',
            metadata={"code": "MCP_OK"},
        ),
    )

    snapshot = ledger.snapshot("session-travel")
    assert snapshot.expected == frozenset({"maps", "weather", "transport", "web", "social"})
    assert snapshot.attempted == frozenset({"maps", "weather"})
    assert snapshot.successful == frozenset({"maps"})
    assert snapshot.missing_attempts == ("social", "transport", "web")


def test_source_ledger_does_not_record_failed_or_unrelated_tools_as_success():
    ledger = TravelSourceLedger()
    ledger.observe(
        "session-travel",
        "mcp__tavily__tavily_search",
        ToolResult(output="timeout", is_error=True, metadata={"code": "MCP_TOOL_TIMEOUT"}),
    )
    ledger.observe(
        "session-travel",
        "mcp__other__calendar",
        ToolResult(output="{}", metadata={"code": "MCP_OK"}),
    )

    snapshot = ledger.snapshot("session-travel")
    assert snapshot.attempted == frozenset({"web"})
    assert snapshot.successful == frozenset()
    assert snapshot.retry_required == ("web",)
    assert source_category("mcp__other__calendar") == ""


def test_search_sources_require_one_narrower_retry_after_an_empty_result():
    ledger = TravelSourceLedger()

    ledger.observe(
        "session-travel",
        "mcp__xhs-readonly__search_notes",
        ToolResult(output='{"status":"success","data":{"text":"{\\"feeds\\":[]}"}}'),
    )

    assert ledger.snapshot("session-travel").retry_required == ("social",)

    ledger.observe(
        "session-travel",
        "mcp__xhs-readonly__search_notes",
        ToolResult(output='{"status":"success","data":{"text":"{\\"feeds\\":[]}"}}'),
    )

    assert ledger.snapshot("session-travel").retry_required == ()


def test_auth_failure_is_not_retried_and_prior_web_success_survives_timeout():
    ledger = TravelSourceLedger()
    ledger.observe(
        "session-travel",
        "mcp__xhs-readonly__search_notes",
        ToolResult(
            output='{"status":"error","code":"TRAVEL_SOURCE_AUTH_REQUIRED"}',
            is_error=True,
            metadata={"code": "TRAVEL_SOURCE_AUTH_REQUIRED"},
        ),
    )
    ledger.observe(
        "session-travel",
        "mcp__tavily__tavily_search",
        ToolResult(output='{"results":[{"title":"大理古城"}]}', metadata={"code": "MCP_OK"}),
    )
    ledger.observe(
        "session-travel",
        "mcp__tavily__tavily_search",
        ToolResult(output="timeout", is_error=True, metadata={"code": "MCP_TOOL_TIMEOUT"}),
    )

    snapshot = ledger.snapshot("session-travel")
    assert snapshot.retry_required == ()
    assert snapshot.successful == frozenset({"web"})


def test_source_ledger_clear_removes_terminal_session_state():
    ledger = TravelSourceLedger()
    ledger.register_expected("session-travel", ["mcp__amap__search"])
    ledger.clear("session-travel")

    assert ledger.snapshot("session-travel").expected == frozenset()


def test_preferred_travel_tools_select_one_query_schema_per_source_category():
    names = [
        "mcp__amap__maps_search_detail",
        "mcp__amap__maps_text_search",
        "mcp__amap__maps_geo",
        "mcp__amap__maps_direction_walking",
        "mcp__open-meteo__get_historical_weather",
        "mcp__12306__get-tickets",
        "mcp__tavily__tavily_extract",
        "mcp__tavily__tavily_search",
        "mcp__xhs-readonly__get_note_detail",
        "mcp__xhs-readonly__search_notes",
    ]

    assert preferred_travel_tool_names(names) == (
        "mcp__amap__maps_text_search",
        "mcp__amap__maps_geo",
        "mcp__amap__maps_direction_walking",
        "mcp__open-meteo__get_historical_weather",
        "mcp__12306__get-tickets",
        "mcp__tavily__tavily_search",
        "mcp__xhs-readonly__search_notes",
    )
