"""Batch 1 · item 1.2 — the season factor no longer counts the weekday pattern twice.

The order forecast already uses weekday means; the factor compares last year's days with the SAME WEEKDAY of a normal
stretch (364 days back, so the weekdays line up) and pairs a public holiday with the same holiday of last year."""
import sqlite3
from datetime import date, timedelta

import pytest

import thai_holidays as TH
from routers import ingredients as ING

# Mon..Sun covers of a flat season with a strong weekend peak
PATTERN = [100, 100, 100, 110, 150, 250, 220]


def _db(branches=("X",), start=date(2025, 6, 1), end=date(2025, 11, 30), mult=None, only=None):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE branches (branch_id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE sales_transaction (branch_id INTEGER, sold_at TEXT, customer_count INTEGER);
    """)
    mult = mult or {}
    for i, name in enumerate(branches, 1):
        con.execute("INSERT INTO branches VALUES (?,?)", (i, name))
        if only and name not in only:
            continue
        d = start
        while d <= end:
            pax = PATTERN[d.weekday()] * mult.get(d, 1.0)
            con.execute("INSERT INTO sales_transaction VALUES (?,?,?)", (i, d.isoformat() + " 12:00:00", round(pax)))
            d += timedelta(days=1)
    return con


def test_weekday_pattern_does_not_bias_the_factor():
    con = _db()
    for s, e in (("2026-10-14", "2026-10-16"),      # Wed-Fri
                 ("2026-10-17", "2026-10-20")):     # Sat-Tue
        r = ING._ing_season_factor(con, "X", s, e)
        assert r["available"] and r["method"] == "weekday_364"
        assert r["factor"] == pytest.approx(1.0, abs=0.01), (s, e, r)
        assert r["ly_from"] == (date.fromisoformat(s) - timedelta(days=364)).isoformat()


def test_a_real_seasonal_lift_is_measured():
    lift = {d: 1.2 for d in (date(2025, 10, 15) + timedelta(days=i) for i in range(3))}      # Wed-Fri 15-17/10/2025
    r = ING._ing_season_factor(_db(mult=lift), "X", "2026-10-14", "2026-10-16")
    assert r["factor"] == pytest.approx(1.2, abs=0.02)


def test_a_holiday_is_paired_with_last_years_holiday():
    # 23/10 (Chulalongkorn Day) was a Thursday in 2025 and sold 1.6x a normal Thursday
    con = _db(mult={date(2025, 10, 23): 1.6})
    r = ING._ing_season_factor(con, "X", "2026-10-21", "2026-10-23")
    assert r["available"]
    assert r["holidays"] == [{"date": "2026-10-23", "ly_date": "2025-10-23", "name": "วันปิยมหาราช"}]
    # 22/10/2026 would be compared with last year's holiday (23/10/2025): left out rather than mismatched
    assert r["skipped"] == [{"date": "2026-10-22", "reason": "last_year_was_holiday"}]
    assert r["days_compared"] == 2
    # Wed 22/10/2025 (100 vs 100) + holiday Thu (176 vs 110) = 276 / 210
    assert r["factor"] == pytest.approx(276 / 210, abs=0.01)
    # without the holiday uplift the same window is flat
    flat = ING._ing_season_factor(_db(), "X", "2026-10-21", "2026-10-23")
    assert flat["factor"] == pytest.approx(1.0, abs=0.01)


def test_holiday_without_a_counterpart_is_left_out():
    # 2/1/2026 was a one-off special holiday: nothing to pair it with
    con = _db(start=date(2024, 9, 1), end=date(2025, 3, 31))
    r = ING._ing_season_factor(con, "X", "2026-01-01", "2026-01-03")
    skipped = {x["date"]: x["reason"] for x in r["skipped"]}
    assert skipped.get("2026-01-02") == "holiday_no_counterpart"


def test_clamp_is_kept():
    big = {d: 10.0 for d in (date(2025, 10, 15) + timedelta(days=i) for i in range(3))}
    r = ING._ing_season_factor(_db(mult=big), "X", "2026-10-14", "2026-10-16")
    assert r["factor"] == 3.0 and r["clamped"] is True and r["raw_factor"] > 3.0
    tiny = {d: 0.05 for d in (date(2025, 10, 15) + timedelta(days=i) for i in range(3))}
    r2 = ING._ing_season_factor(_db(mult=tiny), "X", "2026-10-14", "2026-10-16")
    assert r2["factor"] == 0.5 and r2["clamped"] is True


def test_branch_without_history_falls_back_to_the_chain():
    con = _db(branches=("X", "NEW"), only={"X"})
    r = ING._ing_season_factor(con, "NEW", "2026-10-14", "2026-10-16")
    assert r["available"] and r["scope"] == "chain"
    assert r["factor"] == pytest.approx(1.0, abs=0.01)


def test_not_enough_history_means_factor_one():
    con = _db(start=date(2026, 1, 1), end=date(2026, 3, 1))              # nothing a year back
    r = ING._ing_season_factor(con, "X", "2026-10-14", "2026-10-16")
    assert r["factor"] == 1.0 and r["available"] is False and r["method"] == "weekday_364"
    # the resolver keeps its contract: no data -> factor 1.0 with a reason
    res = ING._ing_season_resolve(con, "X", "2026-10-14", "2026-10-16", "auto")
    assert res["factor"] == 1.0 and res["mode"] == "auto-nodata"


def test_resolver_carries_the_evidence_keys_the_ui_reads():
    con = _db()
    res = ING._ing_season_resolve(con, "X", "2026-10-14", "2026-10-16", "auto")
    d = res["detail"]
    for k in ("ly_from", "ly_to", "ly_pax_per_day", "baseline_pax_per_day", "baseline_from", "baseline_to",
              "raw_factor", "clamped", "scope", "method"):
        assert k in d, k
    assert res["mode"] == "auto" and res["factor"] == pytest.approx(1.0, abs=0.01)


# ─── the holiday table ───────────────────────────────────────────────────────────────────────────────────────────

def test_holiday_table_shape():
    assert all(d.weekday() < 5 for d in TH.HOLIDAYS), "only observed days off (no weekend dates)"
    per_year = {y: sum(1 for d in TH.HOLIDAYS if d.year == y) for y in (2024, 2025, 2026, 2027)}
    assert per_year == {2024: 18, 2025: 19, 2026: 19, 2027: 18}
    assert set(d.year for d in TH.HOLIDAYS) == {2024, 2025, 2026, 2027}


def test_same_holiday_last_year():
    assert TH.same_holiday_last_year(date(2026, 10, 23)) == date(2025, 10, 23)
    assert TH.same_holiday_last_year(date(2026, 3, 3)) == date(2025, 2, 12)       # Makha Bucha moves with the moon
    assert TH.same_holiday_last_year(date(2026, 4, 14)) == date(2025, 4, 14)
    assert TH.same_holiday_last_year(date(2027, 2, 22)) is None                   # a substitution of Makha; 2026 had none
    assert TH.same_holiday_last_year(date(2026, 1, 2)) is None                    # one-off special day
    assert TH.same_holiday_last_year(date(2026, 10, 22)) is None                  # not a holiday


def test_moving_buddhist_days_carry_their_real_dates():
    assert TH.holiday_on(date(2026, 3, 3)).key == "makha"
    assert TH.holiday_on(date(2026, 6, 1)).key == "sub:visakha"                   # Visakha fell on Sun 31/5/2026
    assert TH.holiday_on(date(2026, 7, 29)).key == "asarnha"
    assert TH.holiday_on(date(2027, 5, 20)).key == "visakha"
    assert TH.holiday_on(date(2027, 10, 25)).key == "sub:chulalongkorn"
