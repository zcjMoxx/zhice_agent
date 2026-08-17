"""Low-volume Ctrip account login and hotel observation queries."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from agent.applications.travel.account_credentials import (
    CredentialStoreError,
    EnvironmentPlatformCredentialStore,
    PlatformCredential,
)

_CTRIP_LOGIN_URL = "https://passport.ctrip.com/user/login"
_CTRIP_HOTEL_HOME = "https://hotels.ctrip.com/"
_PROFILE_LOCK = threading.Lock()
_MAX_CARD_TEXT = 1600


class HotelBrowserError(RuntimeError):
    """Stable internal error that never contains page bodies or credentials."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def login_ctrip(
    workspace: Path | str,
    *,
    headless: bool,
    manual_timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Use environment credentials and a persistent profile to establish login."""

    root = Path(workspace).expanduser().resolve()
    credential = EnvironmentPlatformCredentialStore(root).load("ctrip")
    with _exclusive_profile(root):
        with _persistent_context(root, headless=headless) as context:
            page = context.pages[0] if context.pages else context.new_page()
            if _is_logged_in(page):
                return _status("authenticated", "OK", "The Ctrip account is logged in.")
            _submit_password_login(page, credential)
            if _wait_for_login(page, 15):
                return _status("authenticated", "OK", "The Ctrip account is logged in.")
            if headless:
                return _status(
                    "auth_required",
                    "HOTEL_MANUAL_VERIFICATION_REQUIRED",
                    "Ctrip requires manual verification.",
                )
            if _wait_for_login(page, manual_timeout_seconds):
                return _status("authenticated", "OK", "The Ctrip account is logged in.")
            return _status(
                "auth_required",
                "HOTEL_LOGIN_VERIFICATION_TIMEOUT",
                "Ctrip manual verification was not completed in time.",
            )


def check_ctrip_login(workspace: Path | str) -> dict[str, Any]:
    """Check the fixed profile without exposing browser state."""

    root = Path(workspace).expanduser().resolve()
    with _exclusive_profile(root):
        with _persistent_context(root, headless=True) as context:
            page = context.pages[0] if context.pages else context.new_page()
            if _is_logged_in(page):
                return _status("authenticated", "OK", "The Ctrip account is logged in.")
    return _status(
        "auth_required",
        "HOTEL_AUTH_REQUIRED",
        "The Ctrip account needs login.",
    )


def search_ctrip_hotels(
    workspace: Path | str,
    *,
    city: str,
    checkin: str,
    checkout: str,
    keyword: str = "",
    rooms: int = 1,
    adults: int = 2,
    max_results: int = 5,
) -> dict[str, Any]:
    """Return bounded account-observed hotel cards without booking actions."""

    root = Path(workspace).expanduser().resolve()
    city_name = _required_text(city, "city", 80)
    query_keyword = _optional_text(keyword, 120)
    arrival, departure = _dates(checkin, checkout)
    if rooms != 1 or adults != 2:
        raise HotelBrowserError(
            "HOTEL_OCCUPANCY_UNSUPPORTED",
            "The first Ctrip adapter currently supports one room and two adults.",
        )
    if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 10:
        raise HotelBrowserError("HOTEL_QUERY_INVALID", "max_results must be between 1 and 10.")
    try:
        credential = EnvironmentPlatformCredentialStore(root).load("ctrip")
    except CredentialStoreError as exc:
        raise HotelBrowserError(
            "HOTEL_CREDENTIALS_NOT_CONFIGURED",
            "Ctrip credentials have not been configured.",
        ) from exc

    with _exclusive_profile(root):
        with _persistent_context(root, headless=True) as context:
            page = context.pages[0] if context.pages else context.new_page()
            if not _is_logged_in(page):
                _submit_password_login(page, credential)
                if not _wait_for_login(page, 15):
                    raise HotelBrowserError(
                        "HOTEL_MANUAL_VERIFICATION_REQUIRED",
                        "Ctrip requires manual verification in the administration page.",
                    )
            result_url = _discover_city_result_url(page, city_name, query_keyword)
            dated_url = _dated_result_url(result_url, arrival, departure)
            page.goto(dated_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2500)
            cards = _extract_cards(page, max_results)

    return {
        "status": "success",
        "code": "OK",
        "provider": "ctrip-account-observation",
        "source_type": "live_query",
        "freshness": "live",
        "retrieved_at": _utc_now(),
        "query": {
            "city": city_name,
            "checkin": arrival.isoformat(),
            "checkout": departure.isoformat(),
            "rooms": rooms,
            "adults": adults,
            "keyword": query_keyword,
        },
        "price_label": "Ctrip account-observed price",
        "disclaimer": "Prices are planning references observed for this account and may change.",
        "count": len(cards),
        "hotels": cards,
    }


@contextmanager
def _persistent_context(workspace: Path, *, headless: bool) -> Iterator[Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise HotelBrowserError(
            "HOTEL_BROWSER_DEPENDENCY_MISSING",
            "The optional Playwright hotel-browser dependency is not installed.",
        ) from exc
    profile = (workspace / "state" / "browser_profiles" / "ctrip").resolve()
    try:
        profile.relative_to(workspace)
    except ValueError as exc:
        raise HotelBrowserError("HOTEL_PROFILE_INVALID", "The browser profile path is invalid.") from exc
    profile.mkdir(parents=True, exist_ok=True)
    channel = os.getenv("HOTEL_BROWSER_CHANNEL", "chrome").strip() or None
    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                channel=channel,
                headless=headless,
                locale="zh-CN",
                viewport={"width": 1366, "height": 768},
                args=["--no-first-run", "--no-default-browser-check"],
            )
        except Exception as exc:
            raise HotelBrowserError(
                "HOTEL_BROWSER_START_FAILED",
                "The dedicated Ctrip browser profile could not be opened.",
            ) from exc
        try:
            yield context
        finally:
            context.close()


@contextmanager
def _exclusive_profile(workspace: Path) -> Iterator[None]:
    deadline = time.monotonic() + 3.0
    if not _PROFILE_LOCK.acquire(timeout=3.0):
        raise HotelBrowserError(
            "HOTEL_BROWSER_BUSY",
            "The Ctrip account browser is already handling another request.",
        )
    try:
        lock_path = (workspace / "state" / "browser_profiles" / "ctrip.lock").resolve()
        try:
            lock_path.relative_to(workspace)
        except ValueError as exc:
            raise HotelBrowserError(
                "HOTEL_PROFILE_INVALID",
                "The browser profile lock path is invalid.",
            ) from exc
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            _lock_profile_file(handle, deadline)
            try:
                yield
            finally:
                _unlock_profile_file(handle)
    finally:
        _PROFILE_LOCK.release()


def _lock_profile_file(handle: Any, deadline: float) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        while True:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise HotelBrowserError(
                        "HOTEL_BROWSER_BUSY",
                        "The Ctrip account browser is already handling another request.",
                    ) from exc
                time.sleep(0.1)
    else:
        import fcntl

        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise HotelBrowserError(
                        "HOTEL_BROWSER_BUSY",
                        "The Ctrip account browser is already handling another request.",
                    ) from exc
                time.sleep(0.1)


def _unlock_profile_file(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _is_logged_in(page: Any) -> bool:
    try:
        page.goto(_CTRIP_HOTEL_HOME, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(1000)
        login = page.get_by_role("button", name="\u767b\u5f55", exact=True)
        return login.count() == 0 or not login.first.is_visible()
    except Exception:
        return False


def _submit_password_login(page: Any, credential: PlatformCredential) -> None:
    try:
        page.goto(_CTRIP_LOGIN_URL, wait_until="domcontentloaded", timeout=45_000)
        username = page.get_by_role(
            "textbox",
            name=re.compile("\u56fd\u5185\u624b\u673a\u53f7|\u7528\u6237\u540d|\u90ae\u7bb1|\u5361\u53f7"),
        ).first
        password = page.locator('input[type="password"]').first
        username.fill(credential.username)
        password.fill(credential.password)
        checkbox = page.locator('input[type="checkbox"]').first
        if checkbox.count() and not checkbox.is_checked():
            checkbox.check(force=True)
        page.get_by_role("button", name=re.compile("^\u767b\\s*\u5f55$")).click()
    except Exception as exc:
        raise HotelBrowserError(
            "HOTEL_LOGIN_FORM_CHANGED",
            "The Ctrip login form could not be completed.",
        ) from exc


def _wait_for_login(page: Any, seconds: int) -> bool:
    deadline = time.monotonic() + max(1, seconds)
    while time.monotonic() < deadline:
        try:
            if "passport.ctrip.com/user/login" not in page.url:
                return _is_logged_in(page)
            body = page.locator("body").inner_text(timeout=2000)
            if any(
                marker in body
                for marker in (
                    "\u5b89\u5168\u9a8c\u8bc1",
                    "\u62d6\u52a8\u6ed1\u5757",
                    "\u77ed\u4fe1\u9a8c\u8bc1\u7801",
                )
            ):
                page.wait_for_timeout(1000)
                continue
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _discover_city_result_url(page: Any, city: str, keyword: str) -> str:
    try:
        page.goto(_CTRIP_HOTEL_HOME, wait_until="domcontentloaded", timeout=45_000)
        _select_destination_candidate(page, city)
        if keyword:
            page.get_by_placeholder(
                "\u4f4d\u7f6e/\u54c1\u724c/\u9152\u5e97"
            ).first.fill(keyword)
        search = page.get_by_role("button", name="\u641c\u7d22", exact=True)
        if not search.count():
            raise HotelBrowserError(
                "HOTEL_SEARCH_FORM_CHANGED",
                "Ctrip hotel search controls could not be found.",
            )
        search.last.click()
        page.wait_for_url(
            re.compile(r"^https://hotels\.ctrip\.com/hotels/list(?:\?|$)"),
            wait_until="domcontentloaded",
            timeout=45_000,
        )
    except HotelBrowserError:
        raise
    except Exception as exc:
        raise HotelBrowserError(
            "HOTEL_CITY_RESOLUTION_FAILED",
            "Ctrip could not resolve the requested destination.",
        ) from exc
    return str(page.url)


def _select_destination_candidate(page: Any, city: str) -> None:
    destination = page.get_by_placeholder("\u76ee\u7684\u5730").first
    destination.click()
    destination.fill("")
    destination.type(city, delay=120)
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        candidates = page.get_by_text(city, exact=True)
        for index in range(min(candidates.count(), 50)):
            candidate = candidates.nth(index)
            try:
                if not candidate.is_visible():
                    continue
                candidate.click()
                page.wait_for_timeout(200)
                selected = str(destination.input_value() or "").strip()
                if city in selected or selected in city:
                    return
            except Exception:
                continue
        page.wait_for_timeout(250)
    raise HotelBrowserError(
        "HOTEL_CITY_RESOLUTION_FAILED",
        "Ctrip did not return a selectable destination.",
    )


def _dated_result_url(value: str, checkin: date, checkout: date) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname != "hotels.ctrip.com" or not parsed.path.startswith("/hotels/list"):
        raise HotelBrowserError("HOTEL_RESULT_URL_INVALID", "Ctrip returned an unexpected result page.")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(
        {
            "checkin": checkin.isoformat(),
            "checkout": checkout.isoformat(),
            "crn": "1",
            "curr": "CNY",
            "locale": "zh-CN",
        }
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _extract_cards(page: Any, maximum: int) -> list[dict[str, Any]]:
    script = r"""
    (maximum) => {
      const detailNodes = Array.from(document.querySelectorAll('button,a,span'))
        .filter((node) => (node.textContent || '').trim() === '\u67e5\u770b\u8be6\u60c5');
      const seen = new Set();
      const rows = [];
      for (const detail of detailNodes) {
        let node = detail;
        let card = null;
        for (let depth = 0; depth < 9 && node; depth += 1, node = node.parentElement) {
          const text = (node.innerText || '').trim();
          const detailCount = (text.match(/\u67e5\u770b\u8be6\u60c5/g) || []).length;
          if (text.length >= 30 && text.length <= 1800 && detailCount === 1) card = node;
        }
        if (!card) continue;
        const text = (card.innerText || '').replace(/\s+/g, ' ').trim();
        if (!text || seen.has(text)) continue;
        seen.add(text);
        const href = Array.from(card.querySelectorAll('a[href]'))
          .map((a) => a.href)
          .find((url) => /hotels\.ctrip\.com\/hotels\/(detail|hotel)/.test(url)) || '';
        rows.push({ text, href });
        if (rows.length >= maximum) break;
      }
      return rows;
    }
    """
    try:
        raw = page.evaluate(script, maximum)
    except Exception as exc:
        raise HotelBrowserError(
            "HOTEL_RESULT_PARSE_FAILED",
            "Ctrip hotel cards could not be read.",
        ) from exc
    results: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()[:_MAX_CARD_TEXT]
        if not text:
            continue
        rating_match = re.search(
            "([1-5]\\.\\d)\\s*(\u8d85\u68d2|\u5f88\u597d|\u4e0d\u9519|\u4e00\u822c)?",
            text,
        )
        price_matches = re.findall(r"[¥￥]\s*([0-9][0-9,]*)", text)
        name_end = rating_match.start() if rating_match else min(len(text), 80)
        name = re.sub(r"\s*广告\s*$", "", text[:name_end]).strip()[:120]
        price = price_matches[-1].replace(",", "") if price_matches else ""
        href = str(item.get("href") or "")
        if href and not href.startswith("https://hotels.ctrip.com/"):
            href = ""
        results.append(
            {
                "name": name,
                "rating": float(rating_match.group(1)) if rating_match else None,
                "price_cny": int(price) if price.isdigit() else None,
                "price_text": f"¥{price}" if price else "",
                "summary": text,
                "source_url": href,
            }
        )
    return results[:maximum]


def write_safe_status(workspace: Path | str, payload: dict[str, Any]) -> None:
    root = Path(workspace).expanduser().resolve()
    state_dir = (root / "state" / "platform_accounts" / "ctrip").resolve()
    try:
        state_dir.relative_to(root)
    except ValueError as exc:
        raise HotelBrowserError("HOTEL_STATUS_PATH_INVALID", "The status path is invalid.") from exc
    allowed = {"state", "code", "message", "updated_at"}
    safe = {key: payload[key] for key in allowed if key in payload}
    safe.setdefault("updated_at", _utc_now())
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "status.json"
    temporary = state_dir / "status.tmp"
    temporary.write_text(json.dumps(safe, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


def _status(state: str, code: str, message: str) -> dict[str, Any]:
    return {"state": state, "code": code, "message": message, "updated_at": _utc_now()}


def _dates(checkin: str, checkout: str) -> tuple[date, date]:
    try:
        arrival = date.fromisoformat(str(checkin))
        departure = date.fromisoformat(str(checkout))
    except ValueError as exc:
        raise HotelBrowserError("HOTEL_QUERY_INVALID", "Dates must use YYYY-MM-DD.") from exc
    if departure <= arrival or (departure - arrival).days > 30:
        raise HotelBrowserError(
            "HOTEL_QUERY_INVALID",
            "checkout must be after checkin and the stay must not exceed 30 nights.",
        )
    return arrival, departure


def _required_text(value: object, field: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise HotelBrowserError("HOTEL_QUERY_INVALID", f"{field} is invalid.")
    return text


def _optional_text(value: object, maximum: int) -> str:
    text = str(value or "").strip()
    if len(text) > maximum:
        raise HotelBrowserError("HOTEL_QUERY_INVALID", "keyword is invalid.")
    return text


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "HotelBrowserError",
    "check_ctrip_login",
    "login_ctrip",
    "search_ctrip_hotels",
    "write_safe_status",
]
