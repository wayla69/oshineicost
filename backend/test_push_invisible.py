"""push_invisible — the window a daily push covers.

The push failed silently for 15 days (2026-09-06 -> 09-21) after the Railway api
domain was renamed. With a flat 3-day window the fixed script would have resumed
and left those days missing for good; `since_day` reaches back to the last
successful day instead.
"""
from datetime import date

import push_invisible as pi

TODAY = date(2026, 9, 21)


def _state(tmp_path, text):
    f = tmp_path / ".invisible_last_ok.txt"
    f.write_text(text, encoding="utf-8")
    return f


def test_no_state_file_means_plain_days(tmp_path):
    assert pi.since_day(3, TODAY, tmp_path / "absent.txt") == "2026-09-18"


def test_healthy_daily_run_keeps_the_three_day_window(tmp_path):
    assert pi.since_day(3, TODAY, _state(tmp_path, "2026-09-20")) == "2026-09-18"


def test_an_outage_is_covered_from_the_last_good_day(tmp_path):
    assert pi.since_day(3, TODAY, _state(tmp_path, "2026-09-05")) == "2026-09-05"


def test_catch_up_is_bounded(tmp_path):
    assert pi.since_day(3, TODAY, _state(tmp_path, "2025-01-01")) == "2026-06-23"   # 90 days


def test_garbage_state_is_ignored(tmp_path):
    assert pi.since_day(3, TODAY, _state(tmp_path, "not a date")) == "2026-09-18"


def test_default_host_is_not_the_renamed_one():
    assert "api-production-bffdb" not in pi.DEFAULT_URL
