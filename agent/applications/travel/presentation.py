"""Small presentation helpers kept outside AgentLoop and transport code."""

from __future__ import annotations

from agent.applications.travel.schemas import TravelPlanV1, TravelRequestV1


def travel_plan_title(request: TravelRequestV1) -> str:
    """Return a bounded human-readable title derived only from plan requirements."""

    destination = " / ".join(request.destinations)
    return f"{request.origin} → {destination} {request.duration_days} 日旅行计划"[:200]


def travel_plan_summary(plan: TravelPlanV1) -> str:
    """Return a compact, factual post-finalization summary for ToolResult."""

    request = plan.request
    budget = plan.data.get("budget", {})
    unknown_count = len(plan.data.get("unknowns", []))
    return (
        f"已保存 {request.origin} 到 {' / '.join(request.destinations)}的"
        f"{request.duration_days}日计划；预算预期约 ¥{float(budget.get('expected', 0)):,.0f}，"
        f"仍有 {unknown_count} 项需要出发前复核。"
    )

