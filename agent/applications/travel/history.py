"""Rebuild bounded user-facing travel progress from persisted Session messages."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from agent.applications.travel.progress import travel_tool_presentation
from agent.message import Message
from agent.protocols.hook import PostToolHookRequest

_MAX_PROGRESS_ITEMS = 100


def project_travel_progress(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Project persisted Tool calls/results without returning their raw payloads."""

    calls: dict[str, tuple[str, dict[str, Any], str]] = {}
    items: list[dict[str, Any]] = []
    saw_planning_tool = False
    plan_saved = False

    for index, message in enumerate(messages):
        if message.role == "assistant":
            _remember_tool_calls(calls, message)
            continue
        if message.role != "tool":
            continue

        call_id = str(message.tool_call_id or "").strip()
        remembered = calls.get(call_id)
        tool_name = str(message.name or (remembered[0] if remembered else "")).strip()
        if not tool_name:
            continue
        effective_arguments = message.metadata.get("travel_effective_arguments")
        arguments = (
            dict(effective_arguments)
            if isinstance(effective_arguments, dict)
            else remembered[1] if remembered else {}
        )
        turn_id = str(message.turn_id or (remembered[2] if remembered else ""))
        is_error = bool(message.metadata.get("is_error"))
        try:
            presentation = travel_tool_presentation(
                PostToolHookRequest(
                    tool_name=tool_name,
                    arguments=arguments,
                    output=message.content,
                    is_error=is_error,
                    result_metadata=dict(message.metadata),
                    session_id="history",
                    turn_id=turn_id,
                    channel="travel",
                )
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if presentation.display.get("visibility") == "internal":
            continue

        if _is_finalization_delegation(tool_name, arguments):
            saw_planning_tool = True
            status = "error" if is_error else "done"
            items.extend(
                [
                    {
                        "id": f"{call_id or f'history-tool-{index}'}-lodging",
                        "stage": "validate",
                        "title": (
                            "住宿资料未完整取得"
                            if is_error
                            else "住宿与房价资料已汇总"
                        ),
                        "detail": (
                            "保留已取得的住宿资料并进入最终校验"
                            if is_error
                            else "已完成住宿来源补齐，准备最终校验"
                        ),
                        "status": status,
                        "lane": "lodging",
                    },
                    {
                        "id": f"{call_id or f'history-tool-{index}'}-transport",
                        "stage": "validate",
                        "title": (
                            "部分路线未完整取得"
                            if is_error
                            else "公共交通路线已汇总"
                        ),
                        "detail": (
                            "保留已取得的线路并进入最终校验"
                            if is_error
                            else "已完成线路来源补齐，准备最终校验"
                        ),
                        "status": status,
                        "lane": "transport",
                    },
                ]
            )
            continue

        title = str(presentation.display.get("title") or "").strip()
        detail = str(presentation.display.get("detail") or "").strip()
        if not title and not detail:
            continue
        saw_planning_tool = True
        stage = _stage(tool_name)
        item: dict[str, Any] = {
            "id": call_id or f"history-tool-{index}",
            "stage": stage,
            "title": title or "规划能力执行完成",
            "detail": detail or title,
            "status": "error" if is_error else "done",
        }
        result = _result_detail(presentation.ui_metadata)
        if result is not None:
            item["result"] = result
        items.append(item)
        if tool_name.casefold() == "finalize_travel_plan" and not is_error:
            plan_saved = True

    if not saw_planning_tool:
        return []
    items.insert(
        0,
        {
            "id": "history-requirements",
            "stage": "requirements",
            "title": "旅行条件已确认",
            "detail": "已根据确认后的日期、人数、预算与偏好开始规划",
            "status": "done",
        },
    )
    if plan_saved:
        items.append(
            {
                "id": "history-complete",
                "stage": "complete",
                "title": "旅行计划已完成",
                "detail": "完整行程已保存",
                "status": "done",
            }
        )
    return _bounded(items)


def _remember_tool_calls(
    calls: dict[str, tuple[str, dict[str, Any], str]], message: Message
) -> None:
    for raw in message.tool_calls:
        if not isinstance(raw, dict):
            continue
        call_id = str(raw.get("id") or "").strip()
        function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
        name = str(function.get("name") or "").strip()
        if not call_id or not name:
            continue
        arguments = _arguments(function.get("arguments"))
        calls[call_id] = (name, arguments, str(message.turn_id or ""))


def _arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _stage(tool_name: str) -> str:
    normalized = tool_name.casefold()
    if normalized == "finalize_travel_plan":
        return "validate"
    if normalized == "run_skill":
        return "solve"
    if "xhs" in normalized:
        return "guides"
    return "data"


def _is_finalization_delegation(tool_name: str, arguments: dict[str, Any]) -> bool:
    if tool_name.casefold() != "delegate_tasks":
        return False
    tasks = arguments.get("tasks")
    if not isinstance(tasks, list):
        return False
    profiles = {
        str(task.get("profile") or "").strip()
        for task in tasks
        if isinstance(task, dict)
    }
    return profiles == {"travel-final-stay", "travel-final-route"}


def _result_detail(metadata: dict[str, Any]) -> dict[str, Any] | None:
    if metadata.get("detail_type") != "search_results":
        return None
    data = metadata.get("detail_data")
    if not isinstance(data, dict):
        return None
    rows = data.get("items") if isinstance(data.get("items"), list) else []
    items = [
        {
            "title": str(row.get("title") or "").strip(),
            "detail": str(row.get("detail") or "").strip(),
        }
        for row in rows[:5]
        if isinstance(row, dict) and str(row.get("title") or "").strip()
    ]
    return {
        "provider": str(data.get("provider") or "").strip(),
        "query": str(data.get("query") or "").strip(),
        "summary": str(data.get("summary") or "").strip(),
        "resultCount": _non_negative_int(data.get("result_count"), len(items)),
        "items": items,
    }


def _bounded(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(items) <= _MAX_PROGRESS_ITEMS:
        return items
    return [items[0], *items[-(_MAX_PROGRESS_ITEMS - 1) :]]


def _non_negative_int(value: Any, fallback: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return fallback
