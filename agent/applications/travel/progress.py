"""User-facing, bounded presentation for travel Tool runtime events."""

from __future__ import annotations

import json
import re
from typing import Any

from agent.applications.travel.source_ledger import source_operation
from agent.protocols.hook import (
    HookRuntime,
    PostToolHookRequest,
    PostToolHookResult,
    PreToolHookRequest,
    PreToolHookResult,
)
from agent.protocols.runtime_event import validate_runtime_event_presentation

_MAX_ITEMS = 5
_MAX_TEXT = 100
_MAX_RESULT_PARSE_CHARS = 128_000


class TravelProgressHookRuntime:
    """Compose configured Hooks with a travel-only presentation projector."""

    def __init__(self, delegate: HookRuntime | None = None):
        self.delegate = delegate

    def run_pre_tooluse(self, request: PreToolHookRequest) -> PreToolHookResult:
        base = (
            self.delegate.run_pre_tooluse(request)
            if self.delegate is not None
            else PreToolHookResult(action="continue")
        )
        if base.action == "block" or request.channel != "travel":
            return base
        if "tavily" not in request.tool_name.casefold():
            return base
        if not request.tool_name.casefold().endswith("tavily_search"):
            return base
        arguments = dict(base.arguments if base.action == "modify" else request.arguments)
        arguments["include_raw_content"] = False
        if "include_images" in arguments:
            arguments["include_images"] = False
        if "include_image_descriptions" in arguments:
            arguments["include_image_descriptions"] = False
        maximum = arguments.get("max_results")
        arguments["max_results"] = min(max(maximum, 1), 5) if isinstance(maximum, int) else 5
        depth = str(arguments.get("search_depth") or "").casefold()
        if arguments.get("country") and depth in {"fast", "ultra-fast", "ultra_fast"}:
            arguments["search_depth"] = "basic"
        return PreToolHookResult(action="modify", arguments=arguments)

    def run_post_tooluse(self, request: PostToolHookRequest) -> PostToolHookResult:
        base = (
            self.delegate.run_post_tooluse(request)
            if self.delegate is not None
            else PostToolHookResult()
        )
        if request.channel != "travel":
            return base
        try:
            travel = travel_tool_presentation(request)
            display = {**base.display, **travel.display}
            ui_metadata = travel.ui_metadata or base.ui_metadata
            display, ui_metadata = validate_runtime_event_presentation(display, ui_metadata)
            return PostToolHookResult(display=display, ui_metadata=ui_metadata)
        except (TypeError, ValueError, json.JSONDecodeError):
            return base


def travel_tool_presentation(request: PostToolHookRequest) -> PostToolHookResult:
    """Project one final Tool result into safe travel progress copy."""

    name = request.tool_name.casefold()
    if request.result_metadata.get("travel_progress_visibility") == "internal":
        return PostToolHookResult(display={"visibility": "internal"})
    if name in {"discover_tools", "load_skills", "request_travel_clarification"}:
        return PostToolHookResult(display={"visibility": "internal"})
    if source_operation(name) == "transport_lookup":
        return PostToolHookResult(display={"visibility": "internal"})
    if name == "run_skill":
        return _optimizer(request)
    if name == "request_travel_candidate_review":
        return (
            _simple(
                "候选方案准备未完成",
                "正在修正候选结构后重新校验",
                icon="route",
            )
            if request.is_error
            else PostToolHookResult(display={"visibility": "internal"})
        )
    if name == "finalize_travel_plan":
        return _simple(
            "完整计划校验未通过" if request.is_error else "完整计划已校验并保存",
            "正在自动修正结构后重新校验" if request.is_error else "日期、行程、预算和来源引用均已通过校验",
            icon="check",
        )
    if "amap" in name:
        return _amap(request)
    if "open-meteo" in name or "open_meteo" in name:
        return _weather(request)
    if "12306" in name or "train" in name or "rail" in name:
        return _rail(request)
    if "tavily" in name:
        return _web_search(request)
    if "xhs" in name or "xiaohongshu" in name:
        return _xhs(request)
    return PostToolHookResult()


def _amap(request: PostToolHookRequest) -> PostToolHookResult:
    query = _first_text(request.arguments, "keywords", "keyword", "destination", "address")
    if request.is_error:
        return _source_error("高德地图", query)
    try:
        payloads = _json_objects(request.output)
        payload = _preferred_object(
            payloads,
            ("pois", "geocodes", "return", "route", "paths", "name", "distance"),
        )
    except json.JSONDecodeError:
        payloads = []
        payload = {}
    geocodes = _first_list(payload, "geocodes", "return")
    if geocodes:
        items = _geocode_items(geocodes, query)
        detail = f"解析到 {len(geocodes)} 个地址候选，展示前 {min(len(items), _MAX_ITEMS)} 个"
        return _results("高德地图", query or "地址解析", detail, items, len(geocodes))
    pois = _first_list_from(payloads, "pois", "results", "data")
    items = _named_items(pois, detail_keys=("address", "type", "distance"))
    if "detail" in request.tool_name.casefold() and not items and _text(payload.get("name")):
        items = _named_items([payload], detail_keys=("address", "type", "business_area"))
    if "detail" in request.tool_name.casefold() and items:
        detail = f"已核对 {items[0]['title']} 的地址与地点信息"
    elif items:
        detail = f"返回 {len(pois)} 个结果，展示前 {min(len(items), _MAX_ITEMS)} 个候选"
    elif any(part in request.tool_name.casefold() for part in ("direction", "route", "distance")):
        items = _route_items(payload, request.arguments, request.output)
        detail = "已核对路线距离、预计时长与出行方式"
    else:
        detail = "查询完成，未返回可展示的地点候选"
    visible_query = query or (items[0]["title"] if items and "detail" in request.tool_name.casefold() else "地点与路线")
    return _results("高德地图", visible_query, detail, items, len(pois) or len(items))


def _weather(request: PostToolHookRequest) -> PostToolHookResult:
    query = _first_text(request.arguments, "name", "city") or _date_query(request.arguments)
    if request.is_error:
        return _source_error("Open-Meteo", query)
    payloads = _json_objects(request.output)
    payload = _preferred_object(payloads, ("daily", "results", "freshness"))
    locations = _named_items(_first_list_from(payloads, "results"), detail_keys=("admin1", "country"))
    if locations:
        return _results(
            "Open-Meteo",
            query or "地点定位",
            f"已返回 {len(locations)} 个地点候选，行政区域核对通过后用于天气查询",
            [],
            len(locations),
        )
    daily = payload.get("daily") if isinstance(payload.get("daily"), dict) else {}
    dates = daily.get("time") if isinstance(daily.get("time"), list) else []
    freshness = _text(payload.get("freshness"))
    mode = "历史天气参考" if freshness == "historical" else "天气预报"
    detail = f"已取得 {len(dates)} 天{mode}" if dates else f"已完成{mode}查询"
    return _results("Open-Meteo", query or "旅行日期天气", detail, [], len(dates))


def _rail(request: PostToolHookRequest) -> PostToolHookResult:
    origin = _first_text(
        request.arguments, "fromStation", "from_station", "origin", "from"
    )
    destination = _first_text(
        request.arguments, "toStation", "to_station", "destination", "to"
    )
    query = " → ".join(item for item in (origin, destination) if item)
    if request.is_error:
        return _source_error("铁路 12306", query)
    payloads = _json_objects(request.output)
    payload = _preferred_object(payloads, ("trains", "results", "data", "status"))
    rows = _first_list_from(payloads, "trains", "results", "data")
    items = _named_items(
        rows,
        title_keys=("train_no", "train_code", "code", "name"),
        detail_keys=("departure_time", "arrival_time", "duration", "status"),
    )
    if not items:
        items = _rail_text_items(request.output)
    status = _text(payload.get("status")).casefold()
    sale_open_date = _text(payload.get("sale_open_date"))
    detail = (
        f"当前车次尚未开售，预计 {sale_open_date} 开售，届时再复核"
        if status == "not_on_sale" and sale_open_date
        else "当前车次尚未开售，已作为出发前复核项"
        if status == "not_on_sale"
        else (
        f"返回 {len(rows) or len(items)} 个车次结果，展示前 {min(len(items), _MAX_ITEMS)} 个"
        if rows or items
        else "已完成铁路交通核对"
        )
    )
    return _results(
        "铁路 12306", query or "跨城交通", detail, items, len(rows) or len(items)
    )


def _web_search(request: PostToolHookRequest) -> PostToolHookResult:
    query = _first_text(request.arguments, "query", "search_query")
    try:
        payloads = _json_objects(request.output)
        payload = _preferred_object(payloads, ("results", "items", "data", "status"))
    except json.JSONDecodeError:
        payload = {}
    if request.is_error or _payload_failed(payload):
        return _source_error("Tavily 网页检索", query, request, payload)
    query = _first_text(payload, "query") or query
    rows = _first_list_from(payloads, "results", "items", "data")
    items = _named_items(rows, title_keys=("title", "name"), detail_keys=("content", "excerpt", "snippet"))
    detail = _search_summary(
        rows,
        items,
        found=f"找到 {len(rows)} 条网页资料，展示前 {min(len(items), _MAX_ITEMS)} 条筛选摘要",
        empty="没有找到匹配的网页资料，建议缩短关键词或拆分地点后重试",
        unreadable="网页资料已返回，但当前格式暂无法生成摘要",
        recognized=bool(payloads),
    )
    return _results("Tavily 网页检索", query or "公开攻略与官方信息", detail, items, len(rows))


def _xhs(request: PostToolHookRequest) -> PostToolHookResult:
    query = _first_text(request.arguments, "keyword")
    try:
        payloads = _json_objects(request.output)
        payload = _preferred_object(payloads, ("feeds", "items", "notes", "results", "data", "status"))
    except json.JSONDecodeError:
        payload = {}
    if request.is_error or _payload_failed(payload):
        return _source_error("小红书只读", query, request, payload)
    rows = _first_list_from(payloads, "feeds", "items", "notes", "results")
    items = _named_items(
        rows,
        title_keys=("title", "note_card", "noteCard", "display_title", "displayTitle", "name"),
        detail_keys=("desc", "description", "nickname", "nickName", "author", "user"),
    )
    detail = _search_summary(
        rows,
        items,
        found=f"读取 {len(rows)} 条公开经验，展示前 {min(len(items), _MAX_ITEMS)} 条筛选摘要",
        empty="上游本轮返回空结果；如有具体景点，将仅收窄重试一次",
        unreadable="公开笔记已返回，但当前格式暂无法生成摘要",
        recognized=bool(payloads),
    )
    return _results("小红书只读", query or "旅行经验与避坑", detail, items, len(rows))


def _optimizer(request: PostToolHookRequest) -> PostToolHookResult:
    if request.is_error:
        return _simple("候选行程未通过可行性筛选", "正在根据预算、路线或时间冲突调整候选方案", icon="route")
    payload = _json_object(request.output)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    selected = data.get("selected_candidate") if isinstance(data.get("selected_candidate"), dict) else {}
    candidate_id = _first_text(selected, "candidate_id")
    candidates = request.arguments.get("params", {}).get("candidates") if isinstance(request.arguments.get("params"), dict) else []
    candidate_count = len(candidates) if isinstance(candidates, list) else 0
    budget = data.get("budget") if isinstance(data.get("budget"), dict) else {}
    quality = data.get("quality_gate") if isinstance(data.get("quality_gate"), dict) else {}
    details = []
    if candidate_id:
        details.append({"title": "已采用候选方案", "detail": _candidate_places(selected)})
    if budget:
        details.append({"title": "预算区间", "detail": _budget_text(budget)})
    if quality:
        details.append({"title": "路线与强度", "detail": _quality_text(quality)})
    summary = f"比较 {candidate_count} 个候选后，已采用可行方案" if candidate_count and candidate_id else "候选行程已通过预算、路线与时间校验"
    return _results("行程可行性筛选", "预算、路线、时间与每日强度", summary, details, candidate_count)


def _results(provider: str, query: str, summary: str, items: list[dict[str, str]], count: int) -> PostToolHookResult:
    safe_items = [
        {"title": _text(item.get("title")), "detail": _text(item.get("detail"))}
        for item in items[:_MAX_ITEMS]
        if _text(item.get("title"))
    ]
    return PostToolHookResult(
        display={"title": f"{provider}查询完成", "detail": summary, "icon": "search", "visibility": "normal"},
        ui_metadata={
            "detail_type": "search_results",
            "detail_data": {
                "provider": _text(provider),
                "query": _text(query),
                "summary": _text(summary, 180),
                "result_count": max(0, int(count)),
                "items": safe_items,
            },
        },
    )


def _simple(title: str, detail: str, *, icon: str) -> PostToolHookResult:
    return PostToolHookResult(display={"title": title, "detail": detail, "icon": icon, "visibility": "normal"})


def _source_error(
    provider: str,
    query: str,
    request: PostToolHookRequest | None = None,
    payload: dict[str, Any] | None = None,
) -> PostToolHookResult:
    target = f"（{query}）" if query else ""
    code = str((payload or {}).get("code") or (request.result_metadata.get("code") if request else "") or "").upper()
    reasons = {
        "TRAVEL_SOURCE_UPSTREAM_OFFLINE": "本地只读服务未启动",
        "TRAVEL_SOURCE_AUTH_REQUIRED": "登录状态已失效",
        "TRAVEL_SOURCE_TIMEOUT": "本次查询超时",
        "TRAVEL_SOURCE_PAGE_CONNECTION_CLOSED": "上游搜索页面连接被关闭，同关键词有界重试后仍未恢复",
        "MCP_TOOL_TIMEOUT": "本次查询超时",
        "MCP_OUTPUT_TOO_LARGE": "返回内容过大，已改用精简查询重试",
        "TRAVEL_SOURCE_RATE_LIMITED": "当前请求较多，已触发限流",
        "MCP_TRANSPORT_ERROR": "连接临时中断，服务会自动重连",
    }
    output_text = str(request.output or "") if request else ""
    reason = (
        "高德接口瞬时并发额度已满，本次有界重试后仍未恢复"
        if "CUQPS_HAS_EXCEEDED_THE_LIMIT" in output_text.upper()
        else reasons.get(code, "本次查询未成功")
    )
    return _simple(
        f"{provider}暂未取得结果",
        f"{target}{reason}；已安全降级，规划会保留其它已核验信息",
        icon="warning",
    )


def _payload_failed(payload: dict[str, Any]) -> bool:
    return str(payload.get("status") or "").casefold() in {"error", "failed", "failure"}


def _json_object(value: Any) -> dict[str, Any]:
    objects = _json_objects(value)
    return objects[0] if objects else {}


def _json_objects(value: Any) -> list[dict[str, Any]]:
    """Decode bounded MCP output, including structured and text JSON documents."""

    if isinstance(value, dict):
        roots = [value]
    elif (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_RESULT_PARSE_CHARS
    ):
        return []
    else:
        roots = []
        decoder = json.JSONDecoder()
        offset = 0
        while offset < len(value) and len(roots) < 8:
            while offset < len(value) and value[offset].isspace():
                offset += 1
            if offset >= len(value):
                break
            try:
                parsed, end = decoder.raw_decode(value, offset)
            except json.JSONDecodeError:
                next_object = min(
                    (position for position in (value.find("{", offset + 1), value.find("[", offset + 1)) if position >= 0),
                    default=-1,
                )
                if next_object < 0:
                    break
                offset = next_object
                continue
            if isinstance(parsed, dict):
                roots.append(parsed)
            offset = max(end, offset + 1)
    expanded: list[dict[str, Any]] = []
    seen: set[str] = set()
    queue: list[tuple[Any, int]] = [(item, 0) for item in roots]
    while queue and len(expanded) < 24:
        current, depth = queue.pop(0)
        if not isinstance(current, dict):
            continue
        marker = json.dumps(current, ensure_ascii=False, sort_keys=True, default=str)[:4_000]
        if marker in seen:
            continue
        seen.add(marker)
        expanded.append(current)
        if depth >= 4:
            continue
        for key in ("data", "result", "content", "text", "payload", "output"):
            nested = current.get(key)
            if isinstance(nested, dict):
                queue.append((nested, depth + 1))
            elif isinstance(nested, str) and len(nested) <= 16_000:
                queue.extend((item, depth + 1) for item in _json_objects(nested)[:4])
    return expanded


def _preferred_object(objects: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[str, Any]:
    failed = [item for item in objects if _payload_failed(item)]
    if failed:
        return max(failed, key=lambda item: sum(key in item for key in keys))
    return max(objects, key=lambda item: sum(key in item for key in keys), default={})


def _first_list_from(objects: list[dict[str, Any]], *keys: str) -> list[Any]:
    for value in objects:
        found = _first_list(value, *keys)
        if found:
            return found
        if any(isinstance(value.get(key), list) for key in keys):
            return []
    return []


def _search_summary(
    rows: list[Any],
    items: list[dict[str, str]],
    *,
    found: str,
    empty: str,
    unreadable: str,
    recognized: bool,
) -> str:
    if items:
        return found
    if rows:
        return unreadable
    return empty if recognized else unreadable


def _first_list(value: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        item = value.get(key)
        if isinstance(item, list):
            return item[:100]
        if isinstance(item, dict):
            for nested_key in ("items", "results", "pois", "feeds", "trains"):
                nested = item.get(nested_key)
                if isinstance(nested, list):
                    return nested[:100]
    return []


def _named_items(
    rows: list[Any],
    *,
    title_keys: tuple[str, ...] = ("name", "title"),
    detail_keys: tuple[str, ...] = (),
) -> list[dict[str, str]]:
    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _nested_first_text(row, title_keys)
        if not title:
            continue
        detail_parts = []
        for key in detail_keys:
            value = _nested_first_text(row, (key,))
            if value and value not in detail_parts:
                detail_parts.append(value)
        items.append({"title": title, "detail": " · ".join(detail_parts[:2])})
        if len(items) >= _MAX_ITEMS:
            break
    return items


def _geocode_items(rows: list[Any], query: str) -> list[dict[str, str]]:
    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        address_parts = [
            _text(row.get(key))
            for key in ("province", "city", "district", "street", "number")
        ]
        address = _text(row.get("formatted_address")) or "".join(
            part for part in address_parts if part
        )
        title = address or query or "地址候选"
        location = _text(row.get("location"))
        detail = " · ".join(
            part
            for part in (
                _text(row.get("district")),
                f"坐标 {location}" if location else "",
            )
            if part
        )
        items.append({"title": title[:_MAX_TEXT], "detail": detail[:_MAX_TEXT]})
        if len(items) >= _MAX_ITEMS:
            break
    return items


def _rail_text_items(output: str) -> list[dict[str, str]]:
    pattern = re.compile(
        r"(?m)^(?P<train>[GDCZTKYSL]\d+)"
        r"(?:\([^\r\n]*?\))?\s+"
        r"(?P<origin>[\u4e00-\u9fff]+)(?:\([^\r\n]*?\))?\s*->\s*"
        r"(?P<destination>[\u4e00-\u9fff]+)(?:\([^\r\n]*?\))?\s+"
        r"(?P<departure>\d{2}:\d{2})\s*->\s*(?P<arrival>\d{2}:\d{2})"
        r"(?:\s+(?P<duration>\d{2}:\d{2}))?"
    )
    items = []
    for match in pattern.finditer(str(output or "")[:_MAX_RESULT_PARSE_CHARS]):
        duration = match.group("duration") or ""
        detail = (
            f"{match.group('origin')} {match.group('departure')} → "
            f"{match.group('destination')} {match.group('arrival')}"
        )
        if duration:
            detail += f" · 历时 {duration}"
        items.append({"title": match.group("train"), "detail": detail})
        if len(items) >= _MAX_ITEMS:
            break
    return items


def _nested_first_text(value: dict[str, Any], keys: tuple[str, ...], depth: int = 0) -> str:
    for key in keys:
        item = value.get(key)
        if isinstance(item, dict):
            nested = _nested_first_text(
                item,
                (
                    "display_title",
                    "displayTitle",
                    "title",
                    "name",
                    "nickname",
                    "nickName",
                    "desc",
                    "description",
                ),
                depth + 1,
            )
            if nested:
                return nested
        text = _text(item)
        if text:
            return text
    if depth < 3:
        for item in list(value.values())[:20]:
            if isinstance(item, dict):
                nested = _nested_first_text(item, keys, depth + 1)
                if nested:
                    return nested
    return ""


def _route_items(
    payload: dict[str, Any],
    arguments: dict[str, Any],
    raw_output: object = "",
) -> list[dict[str, str]]:
    origin = _first_text(arguments, "origin", "from")
    destination = _first_text(arguments, "destination", "to")
    title = " → ".join(item for item in (origin, destination) if item) or "路线方案"
    transit_leg = _first_transit_leg(payload)
    truncated_metrics = _truncated_route_metrics(raw_output)
    if not transit_leg:
        transit_leg = _truncated_route_transit_leg(raw_output)
    if transit_leg and _has_route_metric_envelope(payload):
        duration = _route_level_metric(payload, ("duration", "duration_minutes", "time"))
        distance = _route_level_metric(payload, ("distance", "distance_km"))
    elif truncated_metrics:
        distance, duration = truncated_metrics
    elif transit_leg:
        duration = None
        distance = None
    else:
        duration = _nested_find(payload, ("duration", "duration_minutes", "time"))
        distance = _nested_find(payload, ("distance", "distance_km"))
    detail = " · ".join(
        item
        for item in (
            _amap_distance(distance),
            _amap_duration(duration),
            transit_leg,
        )
        if item
    )
    return [{"title": title, "detail": detail}]


def _truncated_route_metrics(value: object) -> tuple[str, str | None] | None:
    """Recover only top-level AMap totals from a bounded head/tail response.

    The MCP transport deliberately inserts ``[truncated middle]`` into oversized
    route JSON.  Generic recursive parsing can then mistake a nested walking step
    (for example 155 metres) for the whole route.  The route envelope lives in the
    retained head, so use only its explicit total distance and first option duration.
    """

    text = _truncated_route_text(value)
    if "[truncated middle]" not in text:
        return None
    route = re.search(
        r'"route"\s*:\s*\{.{0,1200}?"distance"\s*:\s*"?(\d+(?:\.\d+)?)"?',
        text,
        flags=re.DOTALL,
    )
    if not route:
        return None
    tail = text[route.end() : route.end() + 1200]
    duration = re.search(r'"duration"\s*:\s*"?(\d+(?:\.\d+)?)"?', tail)
    return route.group(1), duration.group(1) if duration else None


def _truncated_route_transit_leg(value: object) -> str:
    text = _truncated_route_text(value)
    if "[truncated middle]" not in text:
        return ""
    match = re.search(
        r'"buslines"\s*:\s*\[\s*\{.{0,1000}?'
        r'"name"\s*:\s*"([^"]+)".{0,500}?'
        r'"departure_stop"\s*:\s*\{\s*"name"\s*:\s*"([^"]+)"\s*\}.{0,500}?'
        r'"arrival_stop"\s*:\s*\{\s*"name"\s*:\s*"([^"]+)"',
        text,
        flags=re.DOTALL,
    )
    return f"{match.group(1)}：{match.group(2)} → {match.group(3)}" if match else ""


def _truncated_route_text(value: object) -> str:
    """Unwrap a persisted ToolResult once before parsing its bounded route text."""

    text = str(value or "")
    try:
        payload = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return text
    if isinstance(payload, dict) and isinstance(payload.get("output"), str):
        return payload["output"]
    return text


def _has_route_metric_envelope(value: dict[str, Any]) -> bool:
    """Only trust totals that remain attached to a complete route envelope."""

    route = value.get("route")
    if isinstance(route, dict):
        return True
    data = value.get("data")
    if isinstance(data, dict) and isinstance(data.get("route"), dict):
        return True
    return bool(
        _first_text(value, "origin", "from")
        and _first_text(value, "destination", "to")
        and any(isinstance(value.get(key), list) for key in ("transits", "paths"))
    )


def _route_level_metric(value: object, keys: tuple[str, ...], depth: int = 0) -> Any:
    if depth > 4:
        return None
    if isinstance(value, dict):
        for key in keys:
            if key in value and isinstance(value[key], str | int | float):
                return value[key]
        for key in ("data", "route", "transits", "paths"):
            if key not in value:
                continue
            found = _route_level_metric(value[key], keys, depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value[:5]:
            found = _route_level_metric(item, keys, depth + 1)
            if found is not None:
                return found
    return None


def _amap_distance(value: object) -> str:
    try:
        meters = float(value)
    except (TypeError, ValueError):
        return ""
    return f"距离 {meters / 1000:.1f} 公里" if meters >= 1000 else f"距离 {meters:.0f} 米"


def _amap_duration(value: object) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return ""
    return f"约 {max(1, round(seconds / 60))} 分钟"


def _first_transit_leg(value: object, depth: int = 0) -> str:
    if depth > 8:
        return ""
    if isinstance(value, dict):
        if all(key in value for key in ("name", "departure_stop", "arrival_stop")):
            line = _text(value.get("name"))
            departure = _nested_first_text(value, ("departure_stop",))
            arrival = _nested_first_text(value, ("arrival_stop",))
            if line and departure and arrival:
                return f"{line}：{departure} → {arrival}"
        for item in value.values():
            found = _first_transit_leg(item, depth + 1)
            if found:
                return found
    elif isinstance(value, list):
        for item in value[:20]:
            found = _first_transit_leg(item, depth + 1)
            if found:
                return found
    return ""


def _nested_find(value: Any, keys: tuple[str, ...], depth: int = 0) -> Any:
    if depth > 3:
        return None
    if isinstance(value, dict):
        for key in keys:
            if key in value and isinstance(value[key], str | int | float):
                return value[key]
        for item in list(value.values())[:20]:
            found = _nested_find(item, keys, depth + 1)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value[:5]:
            found = _nested_find(item, keys, depth + 1)
            if found is not None:
                return found
    return None


def _format_metric(value: Any, label: str) -> str:
    text = _text(value)
    return f"{label} {text}" if text else ""


def _first_text(value: dict[str, Any], *keys: str) -> str:
    for key in keys:
        text = _text(value.get(key))
        if text:
            return text
    return ""


def _text(value: Any, limit: int = _MAX_TEXT) -> str:
    if value is None or isinstance(value, bool | dict | list):
        return ""
    normalized = " ".join(str(value).split())
    return normalized[:limit]


def _date_query(arguments: dict[str, Any]) -> str:
    start = _first_text(arguments, "start_date")
    end = _first_text(arguments, "end_date")
    return " 至 ".join(item for item in (start, end) if item)


def _candidate_places(candidate: dict[str, Any]) -> str:
    days = candidate.get("days") if isinstance(candidate.get("days"), list) else []
    places = []
    for day in days[:10]:
        if not isinstance(day, dict):
            continue
        for activity in day.get("activities", [])[:8] if isinstance(day.get("activities"), list) else []:
            if isinstance(activity, dict):
                place = _first_text(activity, "place")
                if place and place not in places:
                    places.append(place)
            if len(places) >= 5:
                break
    return "、".join(places) or "已通过完整日期与活动校验"


def _budget_text(budget: dict[str, Any]) -> str:
    lower = _text(budget.get("lower"))
    expected = _text(budget.get("expected"))
    upper = _text(budget.get("upper"))
    return f"¥{lower}–¥{upper}，预期约 ¥{expected}"[:_MAX_TEXT]


def _quality_text(quality: dict[str, Any]) -> str:
    minutes = _text(quality.get("route_minutes"))
    distance = _text(quality.get("route_distance_km"))
    coverage = quality.get("evidence_coverage")
    coverage_text = f"证据覆盖 {float(coverage) * 100:.0f}%" if isinstance(coverage, int | float) else ""
    return " · ".join(item for item in (f"交通约 {minutes} 分钟" if minutes else "", f"路线约 {distance} 公里" if distance else "", coverage_text) if item)[:_MAX_TEXT]


__all__ = ["TravelProgressHookRuntime", "travel_tool_presentation"]
