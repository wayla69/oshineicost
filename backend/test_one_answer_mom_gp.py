"""One answer per question - two inconsistencies found while building the phone month view.

A. A month in progress is compared on the weekday-fair baseline (owner rule, report §3). The
   phone summary chips (revenue, customers, spend per head) and the branch league used the
   raw same-days figure (customers +32.9 % while the month-by-month card and the daily tile
   said -1.6 %). The report's headline must now equal the months series, the §3 line and the
   daily tile; a closed month is untouched.

B. September 2026 gross profit read 40.24 % on the P&L page and 43.3 % in the management
   report. The P&L multiplies by the RELEASED cost of a month from the Grand Menu cut-over
   (rules.dated_cost_sql); the report multiplied by the undated cost. Both now read the same
   COGS expression, so a released cost reaches every page.
"""
import os
os.environ["OSHINEI_NO_SWAP"] = "1"
import re
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))
import main  # noqa: E402
from test_mgt_months import TH_TODAY, _build, _report, _restore_db, _series, _swap_db  # noqa: E402

RELEASED = 25.0          # the released cost of SKU 3 from September 2026 (its sku_cost is 10.0)


@pytest.fixture(scope="module")
def oa(tmp_path_factory):
    db = tmp_path_factory.mktemp("oneanswer") / "test.db"
    _build(db)
    prev = _swap_db(db)
    try:
        with TestClient(main.app) as c:
            # released costs are rebuilt from the releases at startup, so seed them once the app is up
            from bom_center.dated import DDL
            con = sqlite3.connect(str(db))
            con.execute(DDL)
            con.execute("DELETE FROM bc_sku_cost_month")
            con.executemany("INSERT INTO bc_sku_cost_month(sku_id, month, unit_cost, release_id) VALUES(3, ?, ?, 1)",
                            [("2026-09", RELEASED), ("2026-10", RELEASED)])
            con.commit(); con.close()
            main._tls.con = None
            main.invalidate_cache()
            yield c, db
    finally:
        _restore_db(prev)


# ── A: the month in progress ────────────────────────────────────────────────────

needs_mtd = pytest.mark.skipif(TH_TODAY.day < 2, reason="the month in progress exists only from the 2nd")


@needs_mtd
def test_headline_chips_of_the_month_in_progress_equal_series_pace_and_daily_tile(oa):
    c, _ = oa
    cur_m = TH_TODAY.strftime("%Y-%m")
    r = _report(c, cur_m)
    H, P = r["headline"], r["pace"]
    assert r["partial"] is True and H["mom_basis"] == "dow" and P["mom_basis"] == "dow"
    cur = _series(c)["months"][-1]
    assert cur["month"] == cur_m and cur["mom"]["basis"] == "dow"
    assert H["rev_wow_pct"] == cur["mom"]["revenue"] == P["mom_pct"]
    assert H["cust_wow_pct"] == cur["mom"]["customers"]
    assert H["rph_wow_pct"] == cur["mom"]["rev_per_head"]
    d = c.get("/api/sales/daily", params={"gran": "month"}).json()
    assert d["mom_basis"] == "dow" and d["cur_range"] == r["range"]
    t = d["total"]
    assert (H["rev_wow_pct"], H["cust_wow_pct"], H["rph_wow_pct"]) == (t["mom_pct"], t["cust_mom_pct"], t["rph_mom_pct"])


@needs_mtd
def test_branch_league_of_the_month_in_progress_equals_the_daily_tile(oa):
    c, _ = oa
    r = _report(c, TH_TODAY.strftime("%Y-%m"))
    d = c.get("/api/sales/daily", params={"gran": "month"}).json()
    assert d["mom_basis"] == "dow"
    tile = {b["branch"]: b["mom_pct"] for b in d["branches"]}
    assert r["branches"]
    for b in r["branches"]:
        assert b["wow_pct"] == tile[b["branch"]], b["branch"]


@needs_mtd
def test_a_scoped_month_in_progress_keeps_the_same_rule(oa):
    c, _ = oa
    r = _report(c, TH_TODAY.strftime("%Y-%m"), branch="BranchA")
    cur = _series(c, branch="BranchA")["months"][-1]
    assert r["headline"]["mom_basis"] == "dow"
    assert r["headline"]["rev_wow_pct"] == cur["mom"]["revenue"]
    assert r["headline"]["cust_wow_pct"] == cur["mom"]["customers"]


def test_a_closed_month_keeps_the_plain_month_on_month(oa):
    c, _ = oa
    closed = _series(c)["months"][-2]
    assert closed["mtd"] is False
    H = _report(c, closed["month"])["headline"]
    assert "mom_basis" not in H and "baseline" not in H
    assert H["rev_wow_pct"] == closed["mom"]["revenue"]
    assert H["rev_wow_pct"] == round((H["revenue"] - H["prev"]["revenue"]) / H["prev"]["revenue"] * 100, 1)


# ── B: one cost, one gross profit ──────────────────────────────────────────────

def _sku3_qty(db, table, month):
    con = sqlite3.connect(str(db))
    try:
        if table == "daily":
            return con.execute("SELECT SUM(qty) FROM sales_sku_daily WHERE sku_id=3 AND day LIKE ?", (month + "-%",)).fetchone()[0]
        return con.execute("SELECT SUM(qty) FROM sales_sku_monthly WHERE sku_id=3 AND period_month=?", (month,)).fetchone()[0]
    finally:
        con.close()


def test_report_gp_uses_the_released_cost_like_the_pnl_page(oa):
    c, db = oa
    # September 2026: report (day roll-up), month series, P&L and Profit pages
    daily_qty = _sku3_qty(db, "daily", "2026-09")
    monthly_qty = _sku3_qty(db, "monthly", "2026-09")
    assert daily_qty and monthly_qty
    rep = _report(c, "2026-09")["gp"]
    assert rep["cost_basis"] == "daily"
    assert rep["food_cost"] == pytest.approx(daily_qty * RELEASED, abs=1)
    ser = {m["month"]: m for m in _series(c)["months"]}["2026-09"]
    assert ser["food_cost"] == pytest.approx(daily_qty * RELEASED, abs=1)
    assert ser["gp_pct"] == rep["gp_pct"]
    pnl = {r["period_month"]: r for r in c.get("/api/pnl/monthly").json()}
    assert pnl["2026-09"]["cogs"] == pytest.approx(monthly_qty * RELEASED, abs=1)
    prof = {r["period_month"]: r for r in c.get("/api/profit/summary").json()["monthly_trend"]}
    assert prof["2026-09"]["cogs"] == pytest.approx(pnl["2026-09"]["cogs"], abs=0.5)
    # the same expression on the same window gives the same cost on either roll-up
    import datetime as dt
    from routers.reports import _mgt_all_cost
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        sw, sp = "b.name!='ALL'", {}
        assert _mgt_all_cost(con, sw, sp, dt.date(2026, 9, 1), dt.date(2026, 9, 30), "monthly") == pytest.approx(
            pnl["2026-09"]["cogs"], abs=0.5)
    finally:
        con.close()


def test_months_before_the_cutover_keep_the_old_cost(oa):
    c, db = oa
    rep = _report(c, "2026-08")["gp"]
    assert rep["food_cost"] == pytest.approx(_sku3_qty(db, "daily", "2026-08") * 10.0, abs=1)   # sku_cost, untouched


def test_the_report_never_multiplies_by_an_undated_unit_cost():
    """Drift guard: a sales line is priced at the cost in force for ITS month. A bare
    effective_unit_cost_sql('x') - no month - skips the released cost, which is how the
    report and the P&L page came to disagree. Coverage tests (> 0) live in rules.py."""
    src = (HERE / "routers" / "reports.py").read_text(encoding="utf-8")
    bare = re.findall(r"effective_unit_cost_sql\(\s*'[a-z_0-9]+'\s*\)", src)
    assert not bare, bare
