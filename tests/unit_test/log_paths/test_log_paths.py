from datetime import UTC, date, datetime

from agent.log_paths import BEIJING_TIMEZONE, daily_trace_path


def test_daily_trace_path_uses_flat_date_named_jsonl(tmp_path):
    day = date(2026, 8, 9)

    assert daily_trace_path(tmp_path / "logs", day) == (
        tmp_path / "logs" / "log-2026-08-09.jsonl"
    )


def test_beijing_timezone_uses_fixed_utc_plus_eight_boundary():
    utc_time = datetime(2026, 8, 9, 16, 30, tzinfo=UTC)

    beijing_time = utc_time.astimezone(BEIJING_TIMEZONE)

    assert beijing_time.isoformat() == "2026-08-10T00:30:00+08:00"
