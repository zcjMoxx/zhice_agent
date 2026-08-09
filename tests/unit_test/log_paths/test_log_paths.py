from datetime import date

from agent.log_paths import daily_trace_path


def test_daily_trace_path_uses_flat_date_named_jsonl(tmp_path):
    day = date(2026, 8, 9)

    assert daily_trace_path(tmp_path / "logs", day) == (
        tmp_path / "logs" / "log-2026-08-09.jsonl"
    )
