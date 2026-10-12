"""/api/reports/management/months — the phone's month-by-month view (รายเดือน).

One call returns the last N closed months plus the month in progress (month-to-date).
It adds no formula of its own: every figure must equal what /api/reports/management
prints for the same month and scope (the one-answer-per-question rule), and the month in
progress is compared on the report's weekday-fair baseline, never against a whole month.

Built relative to the real Thai today (the report anchors on SQL date('now','+7 hours'),
which cannot be frozen), so the month in progress exists only from the 2nd of a month.
"""
import os
os.environ["OSHINEI_NO_SWAP"] = "1"
import calendar
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

TH_TODAY = (dt.datetime.utcnow() + dt.timedelta(hours=7)).date()
BACK = 17                      # months of history before the current one (reaches past the YoY guard)
DAILY_WINDOW = 3               # sales_sku_daily covers only the last 3 months; older months read sales_sku_monthly
NO_COST_MONTHS = 2             # the oldest months carry no cost source at all


def _shift(first, n):
    """first-of-month `first` moved by n months."""
    idx = first.year * 12 + (first.month - 1) + n
    return dt.date(idx // 12, idx % 12 + 1, 1)


def _swap_db(db):
    import core
    prev = core.DB_PATH
    core.DB_PATH = db
    main.DB_PATH = db
    main._tls.con = None
    try: main.invalidate_cache()
    except Exception: pass
    return prev


def _restore_db(prev):
    import core
    core.DB_PATH = prev
    main.DB_PATH = prev
    main._tls.con = None
    try: main.invalidate_cache()
    except Exception: pass


def _build(path, with_sales=True):
    _cf._build_db(str(path))
    con = sqlite3.connect(str(path))
    for t in ("sales_transaction", "sales_sku_daily", "sales_sku_monthly", "sales_month_summary", "sale_bill_cogs", "sale_payment"):
        con.execute(f"DELETE FROM {t}")
    if with_sales:
        cur_first = TH_TODAY.replace(day=1)
        for k in range(-BACK, 1):
            first = _shift(cur_first, k)
            last = calendar.monthrange(first.year, first.month)[1]
            # the current month runs through today (the open day is in the data, as live);
            # older months stop at day 28 — enough for every window the report reads
            n_days = TH_TODAY.day if k == 0 else 28
            n_days = min(n_days, last)
            for i in range(1, n_days + 1):
                day = first.replace(day=i)
                ds = day.isoformat()
                wd = day.weekday()
                for bid in (1, 2):
                    # revenue moves with the month, the branch and the weekday; so do the covers
                    scale = (1 + 0.1 * ((k + BACK) % 5)) * (1.5 if wd >= 5 else 1.0) * (1.0 if bid == 1 else 0.6)
                    inc = round(1070.0 * scale, 2)
                    con.execute(
                        "INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,"
                        "food_subtotal,drink_subtotal,other_subtotal,sp_discount,discount,"
                        "service_charge,total_inc_vat,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,'Easy')",
                        (bid, f"{ds}-{bid}", f"{ds}T12:00:00", 6 + wd + bid, 1000.0, 0.0, 0.0, 0.0, 20.0, 40.0, inc))
                    if k >= -DAILY_WINDOW:
                        con.execute("INSERT INTO sales_sku_daily VALUES(?,?,?,?,?)", (bid, ds, 3, 5 + wd, 400.0))
            if -BACK + NO_COST_MONTHS <= k:
                for bid in (1, 2):
                    qty = 100 + 10 * ((k + BACK) % 5) + 5 * bid
                    con.execute(
                        "INSERT INTO sales_sku_monthly(branch_id,period_month,sku_id,qty,subtotal_ex_vat,tax_total,"
                        "total_inc_vat,source) VALUES(?,?,?,?,?,?,?, 'Easy')",
                        (bid, first.strftime("%Y-%m"), 3, qty, 5000.0, 350.0, 5350.0))
    con.commit(); con.close()


@pytest.fixture(scope="module")
def mm(tmp_path_factory):
    db = tmp_path_factory.mktemp("mgtmonths") / "test.db"
    _build(db)
    prev = _swap_db(db)
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        _restore_db(prev)


@pytest.fixture(scope="module")
def empty(tmp_path_factory):
    db = tmp_path_factory.mktemp("mgtmonths0") / "test.db"
    _build(db, with_sales=False)
    prev = _swap_db(db)
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        _restore_db(prev)


def _series(c, **q):
    r = c.get("/api/reports/management/months", params=q)
    assert r.status_code == 200, r.text
    return r.json()


def _report(c, month, **q):
    r = c.get("/api/reports/management", params={"period": "monthly", "mtd": "1", "lang": "th", "month": month, **q})
    assert r.status_code == 200, r.text
    return r.json()


# ── shape ──────────────────────────────────────────────────────────────────

def test_twelve_closed_months_then_the_month_in_progress(mm):
    s = _series(mm)
    assert s["available"] is True
    months = s["months"]
    closed = [m for m in months if not m["mtd"]]
    assert len(closed) == 12
    assert [m["month"] for m in months] == sorted(m["month"] for m in months)      # oldest → newest
    cur = TH_TODAY.strftime("%Y-%m")
    if TH_TODAY.day >= 2:
        assert s["mtd"] is True and months[-1]["month"] == cur
        assert months[-1]["mtd"] is True and months[-1]["partial"] is True
        assert months[-1]["through"] == (TH_TODAY - dt.timedelta(days=1)).isoformat()
        assert months[-1]["range"] == [TH_TODAY.replace(day=1).isoformat(), months[-1]["through"]]
        assert s["through"] == months[-1]["through"]
        # the last closed month is the one before the current month
        assert closed[-1]["month"] == _shift(TH_TODAY.replace(day=1), -1).strftime("%Y-%m")
    else:
        assert s["mtd"] is False and months[-1]["month"] != cur
    assert all(m["through"] is None and m["partial"] is False for m in closed)
    for m in months:
        assert m["revenue"] > 0 and m["customers"] > 0 and m["rev_per_head"] > 0
        assert set(m["mom"]) >= {"revenue", "customers", "rev_per_head", "gp_pp", "basis", "range"}


def test_closed_count_is_a_parameter(mm):
    s = _series(mm, closed=3)
    assert len([m for m in s["months"] if not m["mtd"]]) == 3


def test_no_cumulative_total_anywhere(mm):
    """Owner rule: month after month, never an accumulated sum across months."""
    s = _series(mm)
    assert not any(k in s for k in ("total", "totals", "cumulative", "ytd"))
    assert not any(k in m for m in s["months"] for k in ("total", "cumulative", "ytd"))


# ── one answer per question: every month equals the management report ─────────

def _equal_to_report(c, **scope):
    s = _series(c, **scope)
    assert s["months"]
    for m in s["months"]:
        r = _report(c, m["month"], **scope)
        H, G, P = r["headline"], r["gp"], r["pace"]
        assert r["selected_month"] == m["month"]
        assert m["range"] == r["range"], m["month"]
        assert (m["revenue"], m["customers"], m["rev_per_head"]) == (H["revenue"], H["customers"], H["rev_per_head"]), m["month"]
        assert m["gp_pct"] == G["gp_pct"], m["month"]
        if m["mtd"]:
            assert r["partial"] is True
            # the month in progress: the report's weekday-fair baseline (§3), not a whole month
            assert m["mom"]["basis"] == P["mom_basis"] == "dow"
            assert m["mom"]["revenue"] == P["mom_pct"], m["month"]
            assert m["mom"]["range"] == r["comp_range"]
        else:
            assert m["mom"]["basis"] == "month"
            assert m["mom"]["range"] == r["comp_range"], m["month"]
            assert m["mom"]["revenue"] == H.get("rev_wow_pct"), m["month"]
            assert m["mom"]["customers"] == H.get("cust_wow_pct"), m["month"]
            assert m["mom"]["rev_per_head"] == H.get("rph_wow_pct"), m["month"]
            assert m["mom"]["gp_pp"] == G.get("gp_pct_delta_pp"), m["month"]
        if m["yoy"]:
            assert m["yoy"]["revenue"] == H.get("rev_yoy_pct"), m["month"]
            assert m["yoy"]["customers"] == H.get("cust_yoy_pct"), m["month"]
            assert m["yoy"]["rev_per_head"] == H.get("rph_yoy_pct"), m["month"]
            assert m["yoy"]["range"] == r["yoy_range"]
    return s


def test_every_month_equals_the_management_report(mm):
    s = _equal_to_report(mm)
    assert len(s["months"]) >= 12


def test_group_filter_matches_the_report_and_splits_the_chain(mm):
    chain = _series(mm)
    master = _equal_to_report(mm, group="master")
    franchise = _equal_to_report(mm, group="franchise")
    assert master["branch_scope"] == "Group: master"
    by = lambda s: {m["month"]: m["revenue"] for m in s["months"]}
    for mo, rev in by(chain).items():
        assert by(master)[mo] != rev and by(franchise)[mo] != rev          # each differs from the chain …
        assert by(master)[mo] + by(franchise)[mo] == pytest.approx(rev, abs=0.05)   # … and together they are the chain


def test_branch_filter_matches_the_report(mm):
    one = _equal_to_report(mm, branch="BranchA")
    master = _series(mm, group="master")           # BranchA is the only master branch
    assert [m["revenue"] for m in one["months"]] == [m["revenue"] for m in master["months"]]


@pytest.mark.skipif(TH_TODAY.day < 2, reason="the month in progress exists only from the 2nd")
def test_month_in_progress_is_weekday_fair_for_covers_and_spend_too(mm):
    """Revenue, covers and spend per head all use the same baseline as the daily-sales tile."""
    s = _series(mm)
    cur = s["months"][-1]
    assert cur["mtd"] and cur["mom"]["basis"] == "dow"
    d = mm.get("/api/sales/daily", params={"gran": "month"}).json()
    if d.get("mom_basis") == "dow" and d["cur_range"] == cur["range"]:
        t = d["total"]
        assert cur["mom"]["revenue"] == t["mom_pct"]
        assert cur["mom"]["customers"] == t["cust_mom_pct"]
        assert cur["mom"]["rev_per_head"] == t["rph_mom_pct"]
    # a part month must never be judged against a whole one: the raw whole-month figure differs
    prev_full = mm.get("/api/reports/management", params={"period": "monthly", "month": cur["mom"]["range"][0][:7]}).json()["headline"]["revenue"]
    assert cur["revenue"] < prev_full


# ── cost data and the year-on-year guard ─────────────────────────────────────

def test_months_without_a_cost_source_have_no_gp_not_a_fake_100(mm):
    # the 12 closed months are all inside the costed range; ask for the whole history to reach the uncosted ones
    s = _series(mm, closed=23)
    uncosted = [m for m in s["months"] if m["cost_basis"] == "unavailable"]
    assert uncosted, "the oldest seeded months have no cost source"
    for m in uncosted:
        assert m["gp_pct"] is None and m["gp"] is None and m["food_cost"] is None
        assert m["mom"]["gp_pp"] is None
    costed = [m for m in s["months"] if m["gp_pct"] is not None]
    assert costed and all(m["cost_basis"] in ("monthly", "daily") for m in costed)
    assert all(0 < m["gp_pct"] < 100 for m in costed)


def test_year_on_year_only_where_the_year_ago_window_is_complete(mm):
    s = _series(mm, closed=23)
    for m in s["months"]:
        if m["range"][1] < s["yoy_from"]:
            assert m["yoy"] is None, m["month"]
    assert any(m["yoy"] for m in s["months"]) or TH_TODAY < dt.date(2026, 7, 1)


# ── degenerate data ──────────────────────────────────────────────────────────

def test_empty_database(empty):
    assert _series(empty) == {"available": False}


def test_unknown_branch_has_no_series(mm):
    assert _series(mm, branch="NoSuchBranch") == {"available": False}


# ── the shared window helper ─────────────────────────────────────────────────

def test_month_bounds_helper():
    from routers import reports as R
    # a closed month: itself, the whole previous month, the same month last year
    cs, ce, ps, pe, ys, ye, partial = R._mgt_month_bounds(dt, calendar, "2024-03", None, None, dt.date(2024, 4, 5))
    assert (cs, ce) == (dt.date(2024, 3, 1), dt.date(2024, 3, 31))
    assert (ps, pe) == (dt.date(2024, 2, 1), dt.date(2024, 2, 29))             # leap February
    assert (ys, ye) == (dt.date(2023, 3, 1), dt.date(2023, 3, 31)) and partial is False
    # the month in progress: both comparison windows clamp to the elapsed days
    cs, ce, ps, pe, ys, ye, partial = R._mgt_month_bounds(dt, calendar, "2024-03", "2024-03", dt.date(2024, 3, 30), dt.date(2024, 3, 30))
    assert ce == dt.date(2024, 3, 30) and partial is True
    assert pe == dt.date(2024, 2, 29)                                           # Feb has no 30th → its last day
    assert ye == dt.date(2023, 3, 30)
    # a month that outruns the data is partial too
    assert R._mgt_month_bounds(dt, calendar, "2024-03", None, None, dt.date(2024, 3, 20))[6] is True
