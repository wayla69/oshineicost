# -*- coding: utf-8 -*-
"""PR-PP2 · every sales figure follows the one period pattern (docs/PERIOD_PATTERN.md).

A day / week / month / year not finished is to date through the last CLOSED day and both bases are their first n
days: the week against the same weekdays last week (it was 28 days back), the month against days 1..n of last month,
the year against 1 Jan .. the same date a year back. The day still trading never enters a window; a base with no data
gives no % ("—"). The weekday-fair baseline stays on the management report §3 pace only.

The fixture is built relative to the real Thai today (the closed-day clause is SQL date('now','+7 hours')): 430 days of
flat sales (every day the same) and a one-bill sliver today - so a to-date comparison reads ~0 % and a part period set
against a whole one would read a collapse."""
import os
os.environ["OSHINEI_NO_SWAP"] = "1"
import datetime as dt
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))
import main  # noqa: E402
import conftest as _cf  # noqa: E402
import periods as P  # noqa: E402

TH_TODAY = _cf.bkk_today()   # the app's Bangkok day (rules.TH_TODAY_SQL) at the session's instant
pytestmark = pytest.mark.usefixtures("bkk_frozen")   # SQLite 'now' = the instant TH_TODAY is built from
YESTERDAY = (TH_TODAY - dt.timedelta(days=1)).isoformat()
FIRST = TH_TODAY - dt.timedelta(days=430)
PER_DAY = 2 * 10 * 300.0            # 2 branches × 10 bills × ฿300 ex-VAT


@pytest.fixture(scope="module")
def pc(tmp_path_factory):
    db = tmp_path_factory.mktemp("periodsales") / "test.db"
    _cf._build_db(str(db))
    con = sqlite3.connect(str(db))
    for t in ("sales_transaction", "sales_sku_daily", "sales_sku_monthly", "sales_month_summary", "pos_coupon_day", "pos_coupon_month"):
        con.execute(f"DELETE FROM {t}")
    d = FIRST
    while d <= TH_TODAY:
        ds = d.isoformat()
        n = 1 if d == TH_TODAY else 10
        for bid in (1, 2):
            con.executemany(
                "INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,food_subtotal,drink_subtotal,"
                "other_subtotal,sp_discount,discount,service_charge,total_inc_vat,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,'Easy')",
                [(bid, f"{ds}-{bid}-{k}", f"{ds}T12:{k:02d}:00", 2, 300.0, 0.0, 0.0, 0.0, 0.0, 0.0, 321.0) for k in range(n)])
            con.execute("INSERT INTO sales_sku_daily VALUES(?,?,?,?,?)", (bid, ds, 2, 5 * n, 0.0))
            con.execute("INSERT INTO pos_coupon_day VALUES(?,?,5,?,?)", (bid, ds, n, 10.0 * n))
        d += dt.timedelta(days=1)
    con.execute("INSERT INTO pos_coupon_month SELECT branch_id, substr(day, 1, 7), coupon_series_id, SUM(used_count), "
                "SUM(discount_amount) FROM pos_coupon_day GROUP BY branch_id, substr(day, 1, 7), coupon_series_id")
    con.commit(); con.close()
    import core
    prev = core.DB_PATH
    core.DB_PATH = db
    main.DB_PATH = db
    main._tls.con = None
    try: main.invalidate_cache()
    except Exception: pass
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        core.DB_PATH = prev
        main.DB_PATH = prev
        main._tls.con = None


def _days(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days + 1


# ── /api/sales/daily (ยอดขายรายวัน, the home tile, the phone) ──────────────────────────────────────────────────
@pytest.mark.parametrize("kind", ["day", "week", "month", "year"])
def test_daily_sales_windows_are_the_rule(pc, kind):
    d = pc.get("/api/sales/daily", params={"gran": kind}).json()
    w = P.resolve(kind, YESTERDAY)
    assert d["cur_range"] == list(w["cur"]) and d["prev_range"] == list(w["prev"]) and d["yoy_range"] == list(w["ly"])
    assert d["cur_range"][1] == YESTERDAY                     # the day still trading is never in a window
    assert d["period"]["label_th"] == w["label_th"] and d["period"]["complete"] == w["complete"]
    t = d["total"]
    assert t["revenue"] == pytest.approx(PER_DAY * w["days"], rel=1e-6)
    if w["prev"][0] < FIRST.isoformat():                # the base starts before the data: no base, no %
        assert d["has_prev"] is False and t["prev"] is None and t["mom_pct"] is None
        return
    assert t["prev"]["revenue"] == pytest.approx(PER_DAY * _days(*w["prev"]), rel=1e-6)
    if kind != "year":
        # same days on both sides: flat sales read 0 % (a part period against a whole one would read a collapse)
        assert t["mom_pct"] == 0.0
    assert d["mom_basis"] == ("full" if w["complete"] else "to_date")


def test_week_to_date_is_the_same_weekdays_last_week_not_28_days_back(pc):
    d = pc.get("/api/sales/daily", params={"gran": "week"}).json()
    cs, ce = (dt.date.fromisoformat(x) for x in d["cur_range"])
    ps, pe = (dt.date.fromisoformat(x) for x in d["prev_range"])
    assert (cs - ps).days == 7 and (ce - pe).days == 7 and cs.weekday() == 0
    ys, _ = (dt.date.fromisoformat(x) for x in d["yoy_range"])
    assert (cs - ys).days == 364 and ys.weekday() == 0


def test_year_ago_base_before_the_first_day_gives_no_percent(pc):
    d = pc.get("/api/sales/daily", params={"gran": "year"}).json()
    w = P.resolve("year", YESTERDAY)
    if w["ly"][0] < FIRST.isoformat():                    # the year-ago window starts before the data: a part base
        assert d["has_ly"] is False and d["total"]["yoy_pct"] is None and d["total"]["ly"] is None
    trend = d["trend"]
    assert trend[-1]["partial"] == (not w["complete"]) and trend[-1]["end"] == YESTERDAY
    assert all(x["end"] >= FIRST.isoformat() for x in trend)       # no bar before the first day with bills


def test_home_month_to_date_stops_at_the_last_closed_day(pc):
    j = pc.get("/api/home/brief").json()
    assert j["yesterday"] == YESTERDAY
    w = P.resolve("month", YESTERDAY)
    assert j["mtd_days"] == w["days"]
    assert j["mtd_revenue"] == pytest.approx(PER_DAY * w["days"], rel=1e-6)   # today's sliver is not in it


# ── /api/period/sales and /api/period/headline ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kind", ["week", "month", "year"])
def test_period_sales_equals_the_daily_sales_tile(pc, kind):
    s = pc.get("/api/period/sales", params={"kind": kind, "by_branch": 1}).json()
    d = pc.get("/api/sales/daily", params={"gran": kind}).json()
    assert s["period"]["cur"] == d["cur_range"] and s["period"]["prev"] == d["prev_range"]
    assert s["cur"]["revenue"] == pytest.approx(d["total"]["revenue"], abs=0.01)
    assert s["change"]["revenue"]["prev"] == d["total"]["mom_pct"]
    assert {b["branch"] for b in s["branches"]} == {"BranchA", "BranchB"}
    if not s["period"]["has_ly"]:
        assert s["ly"] is None and s["change"]["revenue"]["ly"] is None


def test_period_sales_pick_and_range(pc):
    w = P.resolve("month", YESTERDAY)
    prev_first = w["prev"][0]
    s = pc.get("/api/period/sales", params={"kind": "month", "pick": prev_first}).json()
    assert s["period"]["complete"] is True and s["period"]["cur"][0] == prev_first     # a finished month, whole
    r = pc.get("/api/period/sales", params={"kind": "range", "date_from": prev_first, "date_to": TH_TODAY.isoformat()}).json()
    assert r["period"]["cur"][1] == YESTERDAY                                            # a range is cut at the anchor
    assert pc.get("/api/period/sales", params={"kind": "decade"}).status_code == 400


def test_headline_pages_answer_without_a_source_table(pc):
    for page in ("guest", "timeflow", "cancelbill", "pricezone", "custtype", "opsreport", "cashctl", "staff", "engineering", "buffetperf"):
        r = pc.get("/api/period/headline", params={"page": page, "kind": "month"})
        assert r.status_code == 200, (page, r.text)
        j = r.json()
        assert j["period"]["cur"][1] <= YESTERDAY and j["figures"], page
    g = pc.get("/api/period/headline", params={"page": "guest", "kind": "month"}).json()
    f = {x["key"]: x for x in g["figures"]}
    assert f["bills"]["cur"] == 20 * P.resolve("month", YESTERDAY)["days"] and f["bills"]["prev_change"] == 0.0
    # the month-grain source cannot cut a month to date: no comparison for the month in progress
    ct = pc.get("/api/period/headline", params={"page": "custtype", "kind": "month"}).json()
    if not P.resolve("month", YESTERDAY)["complete"]:
        assert all(x["prev_change"] is None and x["ly_change"] is None for x in ct["figures"])
    assert pc.get("/api/period/headline", params={"page": "nope"}).status_code == 400


# ── the free compare pages and the guards ────────────────────────────────────────────────────────────────────
def test_trend_compare_guards_a_month_in_progress_on_either_side(pc):
    w = P.resolve("month", YESTERDAY)
    if w["complete"]:
        pytest.skip("the anchor is a month end - no month in progress")
    cur_m, prev_m = w["cur"][0][:7], w["prev"][0][:7]
    for a, b in ((cur_m, prev_m), (prev_m, cur_m)):           # B in progress was a whole A against a part B
        j = pc.get("/api/analysis/compare", params={"month": a, "vs": b}).json()
        assert j["mtd"] is True and j["mtd_cutoff_day"] == w["days"]
        assert j["delta"]["revenue"] == 0.0


def test_period_compare_cuts_every_period_to_the_same_days(pc):
    w = P.resolve("month", YESTERDAY)
    if w["complete"]:
        pytest.skip("the anchor is a month end - no month in progress")
    j = pc.get("/api/analysis/period_compare", params={"a_month_from": w["cur"][0][:7], "a_month_to": w["cur"][0][:7],
                                                         "b_month_from": w["prev"][0][:7], "b_month_to": w["prev"][0][:7]}).json()
    assert j["to_date"] is True and j["n_days"] == w["days"]
    # B is listed only when it had to be cut: on day 30 of a 31-day month a 30-day previous month is already whole (2026-10-30)
    assert j["windows"]["a"] == list(w["cur"]) and j["windows"].get("b", list(w["prev"])) == list(w["prev"])
    assert j["delta"]["revenue"] == 0.0


def test_data_window_ends_on_the_last_closed_day(pc):
    j = pc.get("/api/meta/data-window").json()
    assert j["max_date"] == YESTERDAY


def test_coupon_series_month_in_progress_is_to_date(pc):
    j = pc.get("/api/analysis/coupon-series").json()
    w = P.resolve("month", YESTERDAY)
    if not w["complete"]:
        assert j["mom"]["to_date"] is True and j["mom"]["range"] == list(w["cur"])
        assert all(r["mom_pct"] == 0.0 for r in j["series"] if r["mom_pct"] is not None)


def test_lifecycle_leaves_out_the_month_in_progress(pc):
    w = P.resolve("month", YESTERDAY)
    j = pc.get("/api/menu/lifecycle").json()
    months = j.get("months") or [m for s in j.get("skus", []) for m in (s.get("months") or [])]
    if months and not w["complete"]:
        assert max(months) < YESTERDAY[:7]


# ── the management report: a year, a week to date ─────────────────────────────────────────────────────────────
def test_management_report_year_to_date(pc):
    j = pc.get("/api/reports/management", params={"period": "yearly", "lang": "th"}).json()
    w = P.resolve("year", YESTERDAY)
    assert j["available"] and j["range"] == list(w["cur"]) and j["partial"] == (not w["complete"])
    assert j["years"][0]["year"] == YESTERDAY[:4]
    if w["ly"][0] >= FIRST.isoformat():
        assert j["comp_range"] == list(w["ly"]) and j["yoy_range"] == list(w["ly"])
    else:
        assert j["has_prev"] is False and j["comp_range"] is None


def test_management_report_week_to_date_is_offered_and_compared_on_the_same_days(pc):
    w = P.resolve("week", YESTERDAY)
    j = pc.get("/api/reports/management", params={"period": "weekly"}).json()
    assert j["to_date"] is False                                    # the default stays the latest finished week
    if w["complete"]:
        return
    assert j["weeks"][0]["to_date"] is True and j["weeks"][0]["start"] == w["cur"][0]
    k = pc.get("/api/reports/management", params={"period": "weekly", "week": w["cur"][0]}).json()
    assert k["to_date"] is True and k["range"] == list(w["cur"]) and k["comp_range"] == list(w["prev"])
    assert k["headline"]["rev_wow_pct"] == 0.0


def test_management_month_in_progress_headline_is_to_date_and_pace_keeps_the_weekday_basis(pc):
    w = P.resolve("month", YESTERDAY)
    if w["complete"]:
        pytest.skip("the anchor is a month end")
    j = pc.get("/api/reports/management", params={"period": "monthly", "month": w["cur"][0][:7]}).json()
    assert j["to_date"] is True and j["range"] == list(w["cur"]) and j["comp_range"] == list(w["prev"])
    assert j["headline"]["mom_basis"] == "to_date" and j["headline"]["rev_wow_pct"] == 0.0
    assert j["pace"]["mom_basis"] == "dow"


# ── periods.guard / span_of (pure) ──────────────────────────────────────────────────────────────────────────
def test_guard_cuts_the_periods_to_the_lead_period_to_date():
    g = P.guard({"a": ("2026-10-01", "2026-10-31"), "b": ("2026-09-01", "2026-09-30")}, "2026-10-19", "2025-05-23")
    assert g["to_date"] and g["n_days"] == 19
    assert g["windows"] == {"a": ("2026-10-01", "2026-10-19"), "b": ("2026-09-01", "2026-09-19")}
    g = P.guard({"a": ("2026-09-01", "2026-09-30"), "b": ("2026-10-01", "2026-10-31")}, "2026-10-19", "2025-05-23")
    assert g["windows"] == {"b": ("2026-10-01", "2026-10-19"), "a": ("2026-09-01", "2026-09-19")}
    g = P.guard({"a": ("2026-08-01", "2026-08-31"), "b": ("2026-07-01", "2026-07-31")}, "2026-10-19", "2025-05-23")
    assert g["windows"] == {} and g["to_date"] is False                      # finished periods: whole vs whole
    g = P.guard({"a": ("2025-06-01", "2025-06-30"), "b": ("2025-05-01", "2025-05-31")}, "2026-10-19", "2025-05-23")
    assert g["no_base"] == ["b"]                                              # a base before the data: no %
    assert P.span_of("2026-02", "2026-02") == ("2026-02-01", "2026-02-28")
    assert P.span_of(date_from="2026-10-05", date_to="2026-10-09") == ("2026-10-05", "2026-10-09")
    assert P.span_of() is None
    assert P.kind_of("weekly") == "week" and P.kind_of("yearly") == "year" and P.kind_of("x", "day") == "day"
    assert P.shift("month", "2026-01-15", -1) == "2025-12-01" and P.shift("week", "2026-10-21", -1) == "2026-10-12"
