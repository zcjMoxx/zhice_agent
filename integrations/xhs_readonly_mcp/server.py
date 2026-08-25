"""Catalog-level read-only proxy for an isolated Xiaohongshu MCP service."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP

server = FastMCP("zhice-xhs-readonly")

_LOGIN_TOOL_NAMES = ("check_login_status", "login_status")
_SEARCH_TOOL_NAMES = ("search_feeds", "search_notes", "search")
_DETAIL_TOOL_NAMES = ("get_feed_detail", "get_note_detail", "feed_detail")
_MAX_UPSTREAM_RESULT_CHARS = 128_000
_MAX_RESULT_CHARS = 10_000
_SORT_BY_UPSTREAM = {
    "general": "综合",
    "latest": "最新",
    "most_liked": "最多点赞",
    "most_commented": "最多评论",
    "most_collected": "最多收藏",
}
_NOTE_TYPE_UPSTREAM = {"all": "不限", "video": "视频", "image": "图文"}


class RateLimitError(RuntimeError):
    """Raised before an upstream call when the global minute quota is full."""


class _GlobalRateLimiter:
    """One process-wide limiter shared by all users and all three read tools."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._timestamps: list[float] = []
        self._last_call = 0.0

    async def acquire(self) -> None:
        maximum = _env_int("XHS_READONLY_MAX_CALLS_PER_MINUTE", 20, 1, 120)
        minimum_interval = _env_float("XHS_READONLY_MIN_INTERVAL_SECONDS", 1.0, 0.0, 60.0)
        while True:
            wait_for = 0.0
            with self._lock:
                now = time.monotonic()
                self._timestamps = [item for item in self._timestamps if now - item < 60]
                if len(self._timestamps) >= maximum:
                    raise RateLimitError
                wait_for = max(wait_for, minimum_interval - (now - self._last_call))
                if wait_for <= 0:
                    self._timestamps.append(now)
                    self._last_call = now
                    return
            await asyncio.sleep(min(wait_for, 2.0))


_RATE_LIMITER = _GlobalRateLimiter()


@server.tool()
async def check_login_status() -> dict[str, Any]:
    """Check the isolated service account login state without exposing Cookie content."""

    guard = _cookie_guard()
    if guard is not None:
        return guard
    result = await _call_upstream(_LOGIN_TOOL_NAMES, {})
    if result["status"] == "success" and _looks_logged_out(result.get("data")):
        return _error("TRAVEL_SOURCE_AUTH_REQUIRED", "Xiaohongshu login is required.")
    return result


@server.tool()
async def search_notes(
    keyword: str,
    sort_by: str = "general",
    note_type: str = "all",
    max_results: int = 10,
) -> dict[str, Any]:
    """Search public Xiaohongshu notes using the isolated read-only service account."""

    guard = _cookie_guard()
    if guard is not None:
        return guard
    query = _required_text(keyword, "keyword", 160)
    if sort_by not in {"general", "latest", "most_liked", "most_commented", "most_collected"}:
        raise ValueError("sort_by is invalid")
    if note_type not in {"all", "video", "image"}:
        raise ValueError("note_type is invalid")
    bounded = _integer(max_results, "max_results", 1, 20)
    upstream_args: dict[str, Any] = {"keyword": query, "limit": bounded}
    if sort_by != "general" or note_type != "all":
        upstream_args["filters"] = {
            "sort_by": _SORT_BY_UPSTREAM[sort_by],
            "note_type": _NOTE_TYPE_UPSTREAM[note_type],
        }
    result = await _call_upstream(_SEARCH_TOOL_NAMES, upstream_args)
    if result.get("code") == "TRAVEL_SOURCE_PAGE_CONNECTION_CLOSED":
        # This is a transport recovery, not a second keyword strategy. Keep the
        # exact same destination query and allow only one fresh browser-page attempt.
        await asyncio.sleep(
            _env_float("XHS_READONLY_CONNECTION_RETRY_DELAY_SECONDS", 1.0, 0.0, 5.0)
        )
        result = await _call_upstream(_SEARCH_TOOL_NAMES, upstream_args)
    _limit_search_results(result, bounded)
    if _search_result_is_empty(result):
        login = await _call_upstream(_LOGIN_TOOL_NAMES, {})
        if login.get("status") == "success" and _looks_logged_out(login.get("data")):
            return _error("TRAVEL_SOURCE_AUTH_REQUIRED", "Xiaohongshu login is required.")
    _limit_result_data(result)
    result["max_results"] = bounded
    result["untrusted_content"] = True
    return result


@server.tool()
async def get_note_detail(
    feed_id: str,
    xsec_token: str,
    include_comments: bool = False,
) -> dict[str, Any]:
    """Read one Xiaohongshu note detail; comments stay off unless explicitly requested."""

    guard = _cookie_guard()
    if guard is not None:
        return guard
    identifier = _required_text(feed_id, "feed_id", 200)
    access_ref = _required_text(xsec_token, "xsec_token", 1000)
    if not isinstance(include_comments, bool):
        raise ValueError("include_comments must be boolean")
    result = await _call_upstream(
        _DETAIL_TOOL_NAMES,
        {
            "feed_id": identifier,
            "xsec_token": access_ref,
            "load_all_comments": include_comments,
        },
    )
    _limit_result_data(result)
    result["untrusted_content"] = True
    return result


async def _call_upstream(candidates: tuple[str, ...], args: dict[str, Any]) -> dict[str, Any]:
    try:
        await _RATE_LIMITER.acquire()
    except RateLimitError:
        return _error("TRAVEL_SOURCE_RATE_LIMITED", "Xiaohongshu source is rate limited.")
    try:
        url = _upstream_url()
    except ValueError:
        return _error("TRAVEL_SOURCE_UNAVAILABLE", "Xiaohongshu read-only upstream is not configured.")
    timeout = _env_float("XHS_READONLY_TIMEOUT_SECONDS", 30.0, 1.0, 120.0)
    try:
        async with AsyncExitStack() as stack:
            client = await stack.enter_async_context(
                httpx.AsyncClient(timeout=timeout, trust_env=False)
            )
            read_stream, write_stream, _ = await stack.enter_async_context(
                streamable_http_client(url, http_client=client)
            )
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await asyncio.wait_for(session.initialize(), timeout=timeout)
            catalog = await asyncio.wait_for(session.list_tools(), timeout=timeout)
            available = {str(tool.name): tool for tool in catalog.tools}
            selected = next((name for name in candidates if name in available), "")
            if not selected:
                return _error(
                    "TRAVEL_SOURCE_UNAVAILABLE",
                    "Xiaohongshu upstream does not expose the required read-only capability.",
                )
            call_args = _filter_arguments(args, available[selected])
            result = await asyncio.wait_for(session.call_tool(selected, call_args), timeout=timeout)
    except BaseExceptionGroup as exc:
        return _group_error(exc)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403}:
            return _error("TRAVEL_SOURCE_AUTH_REQUIRED", "Xiaohongshu login is required.")
        if exc.response.status_code == 429:
            return _error("TRAVEL_SOURCE_RATE_LIMITED", "Xiaohongshu source is rate limited.")
        return _error("TRAVEL_SOURCE_UNAVAILABLE", "Xiaohongshu source is unavailable.")
    except (httpx.HTTPError, TimeoutError, asyncio.TimeoutError, OSError, ValueError) as exc:
        return {
            **_error("TRAVEL_SOURCE_UNAVAILABLE", "Xiaohongshu source is unavailable."),
            "error_type": type(exc).__name__,
        }
    data = _serialize_result(result)
    if bool(getattr(result, "isError", False) or getattr(result, "is_error", False)):
        text = json.dumps(data, ensure_ascii=False).casefold()
        code = _upstream_tool_error_code(text)
        message = (
            "Xiaohongshu query timed out."
            if code == "TRAVEL_SOURCE_TIMEOUT"
            else "Xiaohongshu read-only query failed."
        )
        return _error(code, message)
    return {
        "status": "success",
        "code": "OK",
        "provider": "xiaohongshu-readonly",
        "source_type": "social_post",
        "freshness": "snapshot",
        "retrieved_at": _utc_now(),
        "data": data,
    }


def _group_error(exc: BaseExceptionGroup) -> dict[str, Any]:
    """Map TaskGroup failures to a stable public cause without leaking upstream text."""

    leaves = list(_exception_leaves(exc))
    if any(isinstance(item, httpx.HTTPStatusError) and item.response.status_code in {401, 403} for item in leaves):
        return _error("TRAVEL_SOURCE_AUTH_REQUIRED", "Xiaohongshu login is required.")
    if any(isinstance(item, httpx.HTTPStatusError) and item.response.status_code == 429 for item in leaves):
        return _error("TRAVEL_SOURCE_RATE_LIMITED", "Xiaohongshu source is rate limited.")
    if any(isinstance(item, (TimeoutError, asyncio.TimeoutError, httpx.TimeoutException)) for item in leaves):
        return _error("TRAVEL_SOURCE_TIMEOUT", "Xiaohongshu query timed out.")
    if any(isinstance(item, (httpx.ConnectError, ConnectionError)) for item in leaves):
        return _error(
            "TRAVEL_SOURCE_UPSTREAM_OFFLINE",
            "Xiaohongshu local read-only service is not running.",
        )
    error = _error("TRAVEL_SOURCE_UNAVAILABLE", "Xiaohongshu source is unavailable.")
    error["error_type"] = next((type(item).__name__ for item in leaves), type(exc).__name__)
    return error


def _upstream_tool_error_code(text: str) -> str:
    """Preserve safe auth/timeout causes returned as an MCP tool error."""

    normalized = str(text or "").casefold()
    if any(marker in normalized for marker in ("login", "登录", "cookie", "unauthorized")):
        return "TRAVEL_SOURCE_AUTH_REQUIRED"
    if any(
        marker in normalized
        for marker in ("context deadline exceeded", "timed out", "timeout", "超时")
    ):
        return "TRAVEL_SOURCE_TIMEOUT"
    if any(
        marker in normalized
        for marker in ("err_connection_closed", "connection closed", "连接被关闭")
    ):
        return "TRAVEL_SOURCE_PAGE_CONNECTION_CLOSED"
    return "TRAVEL_SOURCE_UNAVAILABLE"


def _exception_leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        leaves: list[BaseException] = []
        for nested in exc.exceptions:
            leaves.extend(_exception_leaves(nested))
        return leaves
    return [exc]


def _filter_arguments(args: dict[str, Any], descriptor: Any) -> dict[str, Any]:
    schema = getattr(descriptor, "inputSchema", {}) or getattr(descriptor, "input_schema", {})
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    if not isinstance(properties, dict):
        return args
    filtered = {key: value for key, value in args.items() if key in properties}
    aliases = {
        "feed_id": ("id", "note_id"),
        "xsec_token": ("token",),
        "load_all_comments": ("include_comments",),
        "filters": ("filter",),
        "limit": ("max_results", "count"),
    }
    for key, alternatives in aliases.items():
        if key not in args or key in filtered:
            continue
        alternative = next((name for name in alternatives if name in properties), "")
        if alternative:
            filtered[alternative] = args[key]
    return filtered


def _serialize_result(result: Any) -> Any:
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    if structured is not None:
        return _bounded_json(structured)
    content = getattr(result, "content", [])
    values = []
    for item in content if isinstance(content, list) else []:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            values.append(text)
    return _bounded_json({"text": "\n".join(values)})


def _bounded_json(value: Any) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        encoded = json.dumps({"text": str(value)}, ensure_ascii=False)
    if len(encoded) > _MAX_UPSTREAM_RESULT_CHARS:
        return {"text": encoded[:_MAX_UPSTREAM_RESULT_CHARS], "truncated": True}
    return json.loads(encoded)


def _limit_search_results(result: dict[str, Any], maximum: int) -> None:
    if result.get("status") != "success":
        return
    data = result.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        return
    try:
        payload = json.loads(data["text"])
    except (TypeError, ValueError):
        return
    if not isinstance(payload, dict) or not isinstance(payload.get("feeds"), list):
        return
    feeds = payload["feeds"]
    payload["total_count"] = len(feeds)
    payload["feeds"] = [
        compact
        for item in feeds[:maximum]
        if (compact := _compact_feed(item)) is not None
    ]
    payload["count"] = len(payload["feeds"])
    data["text"] = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _compact_feed(value: Any) -> dict[str, Any] | None:
    """Keep searchable note facts while dropping large cover/avatar payloads."""

    if not isinstance(value, dict):
        return None
    card = value.get("noteCard") or value.get("note_card")
    card = card if isinstance(card, dict) else {}
    user = card.get("user") if isinstance(card.get("user"), dict) else {}
    note_id = str(value.get("id") or value.get("note_id") or "").strip()
    title = str(
        card.get("displayTitle")
        or card.get("display_title")
        or value.get("title")
        or value.get("name")
        or ""
    ).strip()
    if not note_id and not title:
        return None
    nickname = str(user.get("nickname") or user.get("nickName") or "").strip()
    description = str(
        card.get("desc")
        or card.get("description")
        or value.get("desc")
        or value.get("description")
        or ""
    ).strip()
    token = str(value.get("xsecToken") or value.get("xsec_token") or "").strip()
    compact: dict[str, Any] = {
        "id": note_id,
        "noteCard": {
            "displayTitle": title[:300],
            "user": {"nickname": nickname[:120]},
        },
        "source_url": (
            f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else ""
        ),
    }
    if description:
        compact["noteCard"]["description"] = description[:800]
    if token:
        compact["xsecToken"] = token[:1000]
    return compact


def _search_result_is_empty(result: dict[str, Any]) -> bool:
    if result.get("status") != "success":
        return False
    data = result.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        return False
    try:
        payload = json.loads(data["text"])
    except (TypeError, ValueError):
        return False
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("feeds"), list)
        and not payload["feeds"]
    )


def _limit_result_data(result: dict[str, Any]) -> None:
    if result.get("status") != "success" or "data" not in result:
        return
    encoded = json.dumps(
        result["data"], ensure_ascii=False, default=str, separators=(",", ":")
    )
    if len(encoded) <= _MAX_RESULT_CHARS:
        return
    result["data"] = {"text": encoded[:_MAX_RESULT_CHARS], "truncated": True}


def _cookie_guard() -> dict[str, Any] | None:
    configured = os.getenv("XHS_READONLY_COOKIE_FILE", "").strip()
    if not configured:
        return None
    cookie_root = Path(os.getenv("XHS_READONLY_COOKIE_DIR", configured)).expanduser().resolve()
    if cookie_root.is_file():
        cookie_root = cookie_root.parent
    cookie_file = Path(configured).expanduser()
    if not cookie_file.is_absolute():
        cookie_file = cookie_root / cookie_file
    cookie_file = cookie_file.resolve(strict=False)
    try:
        cookie_file.relative_to(cookie_root)
    except ValueError:
        return _error("TRAVEL_SOURCE_AUTH_REQUIRED", "Xiaohongshu Cookie path is outside the isolated volume.")
    if not cookie_file.is_file() or cookie_file.stat().st_size <= 0:
        return _error("TRAVEL_SOURCE_AUTH_REQUIRED", "Xiaohongshu login is required.")
    return None


def _looks_logged_out(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False, default=str).casefold()
    return any(marker in text for marker in ("not logged", "未登录", "登录失效", "cookie expired"))


def _upstream_url() -> str:
    value = os.getenv("XHS_READONLY_UPSTREAM_URL", "").strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("XHS_READONLY_UPSTREAM_URL is invalid")
    allowed_http_hosts = {"127.0.0.1", "localhost", "::1"}
    allowed_http_hosts.update(
        item.strip().casefold()
        for item in os.getenv("XHS_READONLY_HTTP_HOST_ALLOWLIST", "").split(",")
        if item.strip()
    )
    if parsed.scheme != "https" and parsed.hostname.casefold() not in allowed_http_hosts:
        raise ValueError("XHS_READONLY_UPSTREAM_URL must use HTTPS outside local service isolation")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("XHS_READONLY_UPSTREAM_URL must not contain credentials")
    return value


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "code": code, "message": message, "retrieved_at": _utc_now()}


def _required_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f"{field} is invalid")
    return value.strip()


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{field} is invalid")
    return value


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} is invalid") from exc
    return _integer(value, name, minimum, maximum)


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} is invalid") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} is invalid")
    return value


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    server.run(transport="stdio")
