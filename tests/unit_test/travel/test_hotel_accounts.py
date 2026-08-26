from __future__ import annotations

import json
import threading
import time
from contextlib import nullcontext
from datetime import date
from unittest.mock import MagicMock

import pytest

from agent.applications.travel.account_credentials import (
    CredentialStoreError,
    EnvironmentPlatformCredentialStore,
)
from agent.applications.travel.hotel_accounts import HotelAccountSupervisor
from integrations.hotel_browser_mcp import ctrip


def test_platform_credentials_round_trip_through_runtime_env(tmp_path):
    environment: dict[str, str] = {}
    store = EnvironmentPlatformCredentialStore(tmp_path, environ=environment)

    store.save("ctrip", "traveller@example.com", "secret-password")

    credential = store.load("ctrip")
    dotenv = (tmp_path / "config" / ".env").read_text(encoding="utf-8")
    assert credential.username == "traveller@example.com"
    assert credential.password == "secret-password"
    assert 'ZHICE_CTRIP_USERNAME="traveller@example.com"' in dotenv
    assert 'ZHICE_CTRIP_PASSWORD="secret-password"' in dotenv
    assert environment["ZHICE_CTRIP_PASSWORD"] == "secret-password"
    assert store.source("ctrip") == "workspace_env"
    assert store.account_hint("ctrip") == "tr***@example.com"
    assert store.updated_at("ctrip")
    assert store.delete("ctrip") is True
    assert store.configured("ctrip") is False


def test_platform_environment_secret_overrides_dotenv_and_cannot_be_deleted(tmp_path):
    dotenv_store = EnvironmentPlatformCredentialStore(tmp_path, environ={})
    dotenv_store.save("ctrip", "file-user", "file-password")
    store = EnvironmentPlatformCredentialStore(
        tmp_path,
        environ={
            "ZHICE_CTRIP_USERNAME": "injected-user",
            "ZHICE_CTRIP_PASSWORD": "injected-password",
        },
    )

    assert store.load("ctrip").username == "injected-user"
    assert store.source("ctrip") == "environment"
    with pytest.raises(CredentialStoreError, match="deployment environment"):
        store.delete("ctrip")


def test_hotel_account_snapshot_never_returns_plain_credentials(monkeypatch, tmp_path):
    store = EnvironmentPlatformCredentialStore(tmp_path, environ={})
    store.save("ctrip", "13800138000", "secret-password")
    monkeypatch.setattr(
        "agent.applications.travel.hotel_accounts._playwright_available",
        lambda: True,
    )
    supervisor = HotelAccountSupervisor(tmp_path, credential_store=store)

    snapshot = supervisor.admin_snapshot()
    serialized = json.dumps(snapshot, ensure_ascii=False)

    assert snapshot["credential_configured"] is True
    assert snapshot["login_supported"] is True
    assert snapshot["account_hint"] == "13***00"
    assert "13800138000" not in serialized
    assert "secret-password" not in serialized
    assert str(tmp_path) not in serialized


def test_hotel_credential_changes_stop_existing_login_first(monkeypatch, tmp_path):
    store = EnvironmentPlatformCredentialStore(tmp_path, environ={})
    supervisor = HotelAccountSupervisor(tmp_path, credential_store=store)
    configured_when_stopped: list[bool] = []
    monkeypatch.setattr(
        supervisor,
        "stop",
        lambda: configured_when_stopped.append(store.configured("ctrip")),
    )

    supervisor.save_credentials("traveller@example.com", "secret-password")
    supervisor.delete_credentials()

    assert configured_when_stopped == [False, True]
    assert store.configured("ctrip") is False


def test_hotel_login_check_reads_profile_without_submitting_credentials(monkeypatch, tmp_path):
    store = EnvironmentPlatformCredentialStore(tmp_path, environ={})
    store.save("ctrip", "traveller@example.com", "secret-password")
    monkeypatch.setattr(
        "agent.applications.travel.hotel_accounts._playwright_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "agent.applications.travel.hotel_accounts.check_ctrip_login",
        lambda workspace: {
            "state": "authenticated",
            "code": "OK",
            "message": "The Ctrip account is logged in.",
        },
    )
    supervisor = HotelAccountSupervisor(tmp_path, credential_store=store)

    assert supervisor.check_login() == "OK"

    snapshot = supervisor.admin_snapshot()
    assert snapshot["state"] == "authenticated"
    assert snapshot["code"] == "OK"
    assert snapshot["check_in_progress"] is False


def test_hotel_startup_check_runs_once_without_blocking(monkeypatch, tmp_path):
    store = EnvironmentPlatformCredentialStore(tmp_path, environ={})
    store.save("ctrip", "traveller@example.com", "secret-password")
    release = threading.Event()
    calls: list[object] = []
    monkeypatch.setattr(
        "agent.applications.travel.hotel_accounts._playwright_available",
        lambda: True,
    )

    def checked(workspace):
        calls.append(workspace)
        release.wait(timeout=2)
        return {
            "state": "auth_required",
            "code": "HOTEL_AUTH_REQUIRED",
            "message": "Login required.",
        }

    monkeypatch.setattr(
        "agent.applications.travel.hotel_accounts.check_ctrip_login",
        checked,
    )
    supervisor = HotelAccountSupervisor(tmp_path, credential_store=store)

    assert supervisor.start_initial_check() == "HOTEL_AUTH_CHECK_STARTED"
    assert supervisor.start_initial_check() == "HOTEL_AUTH_CHECK_ALREADY_STARTED"
    assert supervisor.admin_snapshot()["check_in_progress"] is True
    release.set()
    deadline = time.monotonic() + 2
    while supervisor.admin_snapshot()["check_in_progress"] and time.monotonic() < deadline:
        time.sleep(0.01)

    assert len(calls) == 1
    assert supervisor.admin_snapshot()["code"] == "HOTEL_AUTH_REQUIRED"


def test_server_login_helper_defaults_to_headless(monkeypatch, tmp_path):
    store = EnvironmentPlatformCredentialStore(tmp_path, environ={})
    store.save("ctrip", "traveller@example.com", "secret-password")
    commands: list[list[str]] = []
    process = MagicMock()
    process.poll.return_value = None
    process.wait.return_value = 1
    tree = MagicMock(process=process)
    monkeypatch.setattr(
        "agent.applications.travel.hotel_accounts._playwright_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "agent.applications.travel.hotel_accounts._headed_browser_available",
        lambda: False,
    )

    def spawned(command, **kwargs):
        commands.append(command)
        return tree

    monkeypatch.setattr(
        "agent.applications.travel.hotel_accounts.ManagedProcessTree.spawn",
        spawned,
    )
    supervisor = HotelAccountSupervisor(tmp_path, credential_store=store)

    assert supervisor.start_login() == "HOTEL_LOGIN_STARTED"
    assert commands and "--headed" not in commands[0]
    assert supervisor.admin_snapshot()["login_mode"] == "password_headless"


def test_ctrip_query_requires_existing_session_without_password_retry(monkeypatch, tmp_path):
    context = MagicMock()
    page = MagicMock()
    context.pages = [page]
    monkeypatch.setattr(ctrip, "_exclusive_profile", lambda workspace: nullcontext())
    monkeypatch.setattr(
        ctrip,
        "_persistent_context",
        lambda workspace, headless: nullcontext(context),
    )
    monkeypatch.setattr(ctrip, "_is_logged_in", lambda current_page: False)
    monkeypatch.setattr(
        ctrip,
        "_submit_password_login",
        lambda *args: pytest.fail("hotel queries must not submit saved credentials"),
    )

    with pytest.raises(ctrip.HotelBrowserError) as raised:
        ctrip.search_ctrip_hotels(
            tmp_path,
            city="上海",
            checkin="2026-09-01",
            checkout="2026-09-02",
        )

    assert raised.value.code == "HOTEL_AUTH_REQUIRED"


def test_ctrip_query_rejects_unsupported_occupancy_before_browser(tmp_path):
    with pytest.raises(ctrip.HotelBrowserError) as raised:
        ctrip.search_ctrip_hotels(
            tmp_path,
            city="上海",
            checkin="2026-09-01",
            checkout="2026-09-02",
            rooms=2,
            adults=2,
        )

    assert raised.value.code == "HOTEL_OCCUPANCY_UNSUPPORTED"


def test_ctrip_dated_result_url_keeps_destination_and_replaces_dates():
    result = ctrip._dated_result_url(  # noqa: SLF001 - pure adapter boundary test
        "https://hotels.ctrip.com/hotels/list?cityId=2&cityName=%E4%B8%8A%E6%B5%B7&checkin=old",
        date.fromisoformat("2026-09-01"),
        date.fromisoformat("2026-09-03"),
    )

    assert "cityId=2" in result
    assert "checkin=2026-09-01" in result
    assert "checkout=2026-09-03" in result
    assert "curr=CNY" in result


def test_ctrip_destination_selection_uses_visible_exact_city_candidate():
    page = MagicMock()
    destination = MagicMock()
    page.get_by_placeholder.return_value.first = destination
    candidates = MagicMock()
    candidates.count.return_value = 3
    hidden_template = MagicMock()
    hidden_template.is_visible.return_value = False
    exact_city = MagicMock()
    exact_city.is_visible.return_value = True
    destination.input_value.return_value = "西安"
    hotel_city_fragment = MagicMock()
    hotel_city_fragment.is_visible.return_value = True
    hotel_city_fragment.locator.return_value.inner_text.return_value = "中国-陕西-西安"
    candidates.nth.side_effect = [hidden_template, exact_city, hotel_city_fragment]
    page.get_by_text.return_value = candidates

    ctrip._select_destination_candidate(page, "西安")  # noqa: SLF001

    destination.fill.assert_called_once_with("")
    destination.type.assert_called_once_with("西安", delay=120)
    exact_city.click.assert_called_once_with()
    hotel_city_fragment.click.assert_not_called()


def test_ctrip_destination_candidates_remove_one_province_level_prefix():
    assert ctrip._destination_query_candidates("重庆忠县") == (  # noqa: SLF001
        "重庆忠县",
        "忠县",
    )
    assert ctrip._destination_query_candidates("四川省阿坝州") == (  # noqa: SLF001
        "四川省阿坝州",
        "阿坝州",
    )
    assert ctrip._destination_query_candidates("河南洛阳") == (  # noqa: SLF001
        "河南洛阳",
        "洛阳",
    )
    assert ctrip._destination_query_candidates("西安") == ("西安",)  # noqa: SLF001


def test_ctrip_card_parser_removes_advertisement_suffix_from_hotel_name():
    page = MagicMock()
    page.evaluate.return_value = [{
        "text": "大观酒店(大理高铁站店) 广告 4.7 超棒 100条点评 ¥304 起 查看详情",
        "href": "",
    }]

    rows = ctrip._extract_cards(page, 5)  # noqa: SLF001

    assert rows[0]["name"] == "大观酒店(大理高铁站店)"
    assert rows[0]["price_cny"] == 304


def test_ctrip_profile_lock_rejects_parallel_access(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def hold_profile() -> None:
        with ctrip._exclusive_profile(tmp_path):  # noqa: SLF001 - concurrency boundary
            entered.set()
            release.wait(timeout=5)

    thread = threading.Thread(target=hold_profile)
    thread.start()
    assert entered.wait(timeout=2)
    try:
        with pytest.raises(ctrip.HotelBrowserError) as raised:
            with ctrip._exclusive_profile(tmp_path):  # noqa: SLF001
                pass
        assert raised.value.code == "HOTEL_BROWSER_BUSY"
    finally:
        release.set()
        thread.join(timeout=2)
