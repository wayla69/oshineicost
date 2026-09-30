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


# The mark is the newest day PUSHED, not the run date (2026-09-30: two pushes from a
# DB still at 09-26 sent 0 rows, stamped 09-30, and 09-27 would never have been sent).
def test_mark_is_the_newest_pushed_day():
    rows = [{"day": "2026-09-27", "location_code": "A"}, {"day": "2026-09-29", "location_code": "A"},
            {"day": "2026-09-28", "location_code": "B"}]
    assert pi.pushed_through(rows) == "2026-09-29"


def test_an_empty_push_leaves_the_mark_alone():
    assert pi.pushed_through([]) is None


def test_stale_db_push_then_next_run_still_reaches_back(tmp_path):
    # the last good push covered through 09-26; a run on 09-30 pushed nothing, so the
    # file still says 09-26 and the next run (10-01, --days 3) starts there, not at 09-28
    state = _state(tmp_path, "2026-09-26")
    assert pi.since_day(3, date(2026, 10, 1), state) == "2026-09-26"
