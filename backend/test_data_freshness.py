# -*- coding: utf-8 -*-
"""⏰ ความสดของข้อมูล — the trailing-30d reporting coverage behind the tab's
"สาขาที่มีข้อมูล ต่อวัน" chart.

The per-branch table can only say where each branch stands TODAY: a branch that
is current now but went silent for three days last week reads as "อัปเดต" there.
The daily series is the only place that hole shows, and every other number on
the dashboard for those days is computed from incomplete data — so a gap day has
to be counted, not smoothed over.
"""
import sqlite3

import pytest

import conftest as _cf
import main


def _rows(client, **params):
    return client.get("/api/extra/closing-time", params=params).json()


def test_daily_coverage_counts_every_day_in_the_window(client):
    j = _rows(client)
    daily = j["daily"]
    assert daily, "the freshness view must carry a daily series"
    assert [d["day"] for d in daily] == sorted(d["day"] for d in daily)
    assert j["summary"]["window_days"] == len(daily)
    n = j["summary"]["branch_count"]
    for d in daily:
        assert d["missing"] == max(0, n - d["branches"])
        assert d["bills"] >= d["branches"], "a reporting branch posted at least one bill"


def test_a_fully_reported_window_has_no_gap_days(client):
    j = _rows(client)
    assert j["summary"]["gap_days"] == sum(1 for d in j["daily"] if d["missing"])
    # the shared fixture has both branches trading every day it covers
    assert j["summary"]["gap_days"] == 0
    assert all(d["missing"] == 0 for d in j["daily"])


def test_a_branch_going_silent_for_a_day_shows_up_as_a_gap(client):
    """The case the per-branch table cannot show: BranchB stops reporting for one
    day in the middle of the window and is back the next."""
    day = f"{_cf.LATEST_MONTH}-02"
    con = sqlite3.connect(str(main.DB_PATH))
    removed = con.execute(
        "SELECT branch_id, receipt_no, sold_at, customer_count, food_subtotal, drink_subtotal,"
        " other_subtotal, discount, service_charge, total_inc_vat, source"
        " FROM sales_transaction WHERE branch_id=2 AND date(sold_at)=?", (day,)).fetchall()
    if not removed:
        con.close()
        pytest.skip("fixture has no BranchB rows on that day")
    try:
        con.execute("DELETE FROM sales_transaction WHERE branch_id=2 AND date(sold_at)=?", (day,))
        con.commit()
        con.close()
        main.invalidate_cache()

        j = _rows(client)
        gap = {d["day"]: d for d in j["daily"]}[day]
        assert gap["missing"] == 1, "the silent branch must be counted as missing on that day"
        assert gap["branches"] == j["summary"]["branch_count"] - 1
        assert j["summary"]["gap_days"] == 1
        # …and BranchB still reads as up to date, which is exactly why the daily
        # series has to exist: the table alone would hide this.
        b = {r["branch"]: r for r in j["branches"]}["BranchB"]
        assert b["days_behind"] == 0 and b["status"] == "current"
    finally:
        con = sqlite3.connect(str(main.DB_PATH))
        con.executemany(
            "INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,"
            "food_subtotal,drink_subtotal,other_subtotal,discount,service_charge,total_inc_vat,source)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)", removed)
        con.commit()
        con.close()
        main.invalidate_cache()
    assert _rows(client)["summary"]["gap_days"] == 0     # fixture restored


def test_single_day_mode_carries_no_daily_series(client):
    """A 30-day series under a one-day question is noise — the tab hides the
    charts in that mode, and the payload has nothing to draw."""
    j = _rows(client, date=f"{_cf.LATEST_MONTH}-03")
    assert j["mode"] == "date"
    assert "daily" not in j
