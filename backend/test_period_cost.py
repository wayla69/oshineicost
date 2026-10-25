# -*- coding: utf-8 -*-
"""PR-PP3 · every cost figure follows the one period pattern (docs/PERIOD_PATTERN.md).

COGS, GP, food-cost % and cost per head are read on periods.windows: a period not finished is to date through the last
CLOSED day and both bases are their first n days. One engine (period_cost.cost_sum): a whole month reads the month table,
a part month the day-level dish table; a window with no cost source (before the day table) has no figure and a reason
(never ฿0, never a part month against a whole one). The fixture is built relative to the real Thai today: 440 days of flat
sales, the day-level dish table only for the last 120 days (the sync's rolling window), the month table for every month, and
a one-bill sliver today - so a to-date comparison reads 0 % / 0.0 จุด and a part period set against a whole one would not."""
import os
os.environ["OSHINEI_NO_SWAP"] = "1"
import calendar
import datetime as dt
import json
import re
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
FIRST = TH_TODAY - dt.timedelta(days=440)
FLOOR = TH_TODAY - dt.timedelta(days=120)          # the first day of the day-level dish table
REV, QTY, COST = 500.0, 5, 10.0                     # per branch per day: dish revenue, plates, unit cost -> food cost 10 %
COVERS = 20                                          # per branch per day (10 bills × 2)


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    db = tmp_path_factory.mktemp("periodcost") / "test.db"
    _cf._build_db(str(db))
    con = sqlite3.connect(str(db))
    for t in ("sales_transaction", "sales_sku_daily", "sales_sku_monthly", "sales_month_summary", "sale_bill_cogs"):
        con.execute(f"DELETE FROM {t}")
    month: dict = {}
    d = FIRST
    while d <= TH_TODAY:
        ds = d.isoformat()
        n = 1 if d == TH_TODAY else 10
        for bid in (1, 2):
            con.executemany(
                "INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,food_subtotal,drink_subtotal,"
                "other_subtotal,sp_discount,discount,service_charge,total_inc_vat,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,'Easy')",
                [(bid, f"{ds}-{bid}-{k}", f"{ds}T12:{k:02d}:00", 2, 50.0, 0.0, 0.0, 0.0, 0.0, 0.0, 53.5) for k in range(n)])
            q, r = (QTY, REV) if n == 10 else (1, 100.0)
            if d >= FLOOR:
                con.execute("INSERT INTO sales_sku_daily VALUES(?,?,?,?,?)", (bid, ds, 3, q, r))
                for k in range(n):
                    if d >= TH_TODAY - dt.timedelta(days=31):
                        con.execute("INSERT INTO sale_bill_cogs VALUES(?,?,?,?)", (f"{ds}-{bid}-{k}", bid, ds, 5.0))
            m = month.setdefault((bid, ds[:7]), [0.0, 0.0])
            m[0] += q; m[1] += r
        d += dt.timedelta(days=1)
    for (bid, ym), (q, r) in month.items():
        con.execute("INSERT INTO sales_sku_monthly(branch_id,period_month,sku_id,qty,subtotal_ex_vat,tax_total,total_inc_vat,source)"
                    " VALUES(?,?,3,?,?,?,?,'Easy')", (bid, ym, q, r, round(r * 0.07, 2), round(r * 1.07, 2)))
    con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES('branch_fixed_costs', ?)",
                (json.dumps({"BranchA": 31000, "BranchB": 0}),))
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
        try: main.invalidate_cache()
        except Exception: pass


def _days(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days + 1


def _covered(win):
    return win[0] >= FLOOR.isoformat()


# ── /api/period/cost ──────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kind", ["day", "week", "month"])
def test_period_cost_to_date_windows_and_same_day_bases(cc, kind):
    d = cc.get("/api/period/cost", params={"kind": kind}).json()
    w = P.resolve(kind, YESTERDAY)
    assert d["period"]["cur"] == list(w["cur"]) and d["period"]["cur"][1] == YESTERDAY   # never the day still trading
    c = d["cur"]
    assert c["cogs"] == pytest.approx(2 * QTY * COST * w["days"]) and c["food_cost_pct"] == 10.0
    assert c["cost_per_head"] == pytest.approx(QTY * COST / COVERS)
    if _covered(w["prev"]):
        assert d["change"]["prev"]["food_cost_pct"] == 0.0 and d["change"]["prev"]["cogs"] == 0.0   # same days on both sides
    # the year-ago base is before the day-level table: no figure, and the reason, never a made-up %
    assert d["ly"] is None and d["change"]["ly"]["food_cost_pct"] is None
    assert d["reasons"]["ly"].startswith("ต้นทุนรายวันมีตั้งแต่") and d["period"]["reasons"]["ly"] == d["reasons"]["ly"]


def test_period_cost_finished_month_reads_the_month_table_whole_vs_whole(cc):
    last = (TH_TODAY.replace(day=1) - dt.timedelta(days=1))
    d = cc.get("/api/period/cost", params={"kind": "month", "pick": last.replace(day=15).isoformat()}).json()
    assert d["period"]["complete"] is True and d["cur"]["src"] == "month"
    assert d["cur"]["cogs"] == pytest.approx(2 * QTY * COST * last.day)
    w = P.resolve("month", YESTERDAY, date_from=last.isoformat())
    if w["ly"][0] >= FIRST.isoformat():                  # the month a year back is in the month table: compared
        assert d["ly"] is not None and d["change"]["ly"]["food_cost_pct"] == 0.0


def test_period_cost_year_says_why_there_is_no_year_ago_base(cc):
    d = cc.get("/api/period/cost", params={"kind": "year"}).json()
    w = P.resolve("year", YESTERDAY)
    assert d["cur"] is not None and d["cur"]["src"] in ("mix", "day", "month")
    if w["ly"][0] < FIRST.isoformat():
        th = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."][FIRST.month - 1]
        assert d["ly"] is None and d["reasons"]["ly"] == f"ข้อมูลปีก่อนเริ่ม {th} {FIRST.year + 543}"


def test_period_cost_by_branch_and_scope(cc):
    d = cc.get("/api/period/cost", params={"kind": "month", "by_branch": 1}).json()
    assert {b["branch"] for b in d["branches"]} == {"BranchA", "BranchB"}
    a = cc.get("/api/period/cost", params={"kind": "month", "branch": "BranchA"}).json()
    assert a["cur"]["cogs"] == pytest.approx(d["cur"]["cogs"] / 2)


def test_reasons_name_the_month_the_data_starts():
    w = {"kind": "year", "first_day": "2025-05-23", "has_prev": False, "has_ly": False}
    assert P.reasons(w) == {"prev": "ข้อมูลปีก่อนเริ่ม พ.ค. 2568", "ly": "ข้อมูลปีก่อนเริ่ม พ.ค. 2568"}
    assert P.reasons(dict(w, kind="week", has_prev=True)) == {"ly": "ข้อมูลเริ่ม 23 พ.ค. 2568"}
    assert P.public(dict(P.resolve("year", "2026-10-19"), anchor="2026-10-19", has_prev=False, has_ly=False,
                         first_day="2025-05-23"))["reasons"]["ly"] == "ข้อมูลปีก่อนเริ่ม พ.ค. 2568"


def test_period_cost_needs_a_cost_module():
    import auth
    rule = next(r for r in auth.MODULE_RULES if r[1] == "/api/period/cost")
    assert rule[0] == "GET" and rule[2] == {"cost.analysis", "profit.pnl"}


# ── the pages' own endpoints ─────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("gran", ["week", "month"])
def test_cost_per_head_is_to_date_with_a_year_ago_column(cc, gran):
    d = cc.get("/api/sales/cost-per-head", params={"gran": gran}).json()
    w = P.resolve(gran, YESTERDAY)
    assert d["cur_range"] == list(w["cur"]) and d["prev_range"] == list(w["prev"]) and d["ly_range"] == list(w["ly"])
    t = d["total"]
    assert t["cost_per_head"] == pytest.approx(QTY * COST / COVERS)
    if _covered(w["prev"]):
        assert t["delta_pct"] == 0.0 and all(r["delta_pct"] == 0.0 for r in d["branches"])   # was: a part week vs a whole one
    assert t["ly_cost_per_head"] is None and t["ly_delta_pct"] is None and "ly" in d["reasons"]


def test_cost_per_head_before_the_day_table_is_unknown_not_zero(cc):
    day = (FLOOR - dt.timedelta(days=10)).isoformat()
    d = cc.get("/api/sales/cost-per-head", params={"gran": "day", "date": day}).json()
    assert d["cost_known"] is False and d["total"]["customers"] > 0 and d["total"]["cost_per_head"] is None
    assert d["reasons"]["cur"].startswith("ต้นทุนรายวันมีตั้งแต่")


def test_pnl_monthly_month_in_progress_is_to_date(cc):
    rows = cc.get("/api/pnl/monthly").json()
    last = rows[-1]
    if P.resolve("month", YESTERDAY)["complete"]:
        pytest.skip("the anchor is a month end: no month in progress")
    assert last["period_month"] == YESTERDAY[:7] and last["mom_basis"] == "to_date"
    assert last["mom_pct"] == 0.0                       # flat sales: the same days read 0 % (a part month vs a whole one did not)
    assert all(r["mom_basis"] == "full" for r in rows[:-1])


def test_branch_food_cost_boxes_and_movers_compare_the_same_days(cc):
    d = cc.get("/api/bom-cost/branch-foodcost-mom").json()
    if P.resolve("month", YESTERDAY)["complete"]:
        pytest.skip("the anchor is a month end")
    assert d["mom_basis"] == "to_date" and d["period"]["cur"][1] == YESTERDAY
    for b in d["branches"]:
        assert b["food_cost_pct"] == 10.0 and b["delta_pp"] == 0.0
        assert b["movers_up"] == [] and b["movers_down"] == []    # the same days: no ฿ bias from a whole month before
        assert b["ly_food_cost_pct"] is None


def test_cost_drivers_older_window_compares_whole_months(cc):
    m2 = (TH_TODAY.replace(day=1) - dt.timedelta(days=40)).strftime("%Y-%m")
    d = cc.get("/api/bom-cost/cost-drivers", params={"month_from": m2, "month_to": m2}).json()
    assert d["mom_basis"] == "month"                   # the to-date basis belongs to the month in progress only


def test_branch_month_drill_in_progress_is_to_date(cc):
    if P.resolve("month", YESTERDAY)["complete"]:
        pytest.skip("the anchor is a month end")
    d = cc.get("/api/pnl/branch-month-detail", params={"branch": "BranchA", "month": YESTERDAY[:7]}).json()
    assert d["to_date"] is True and d["period"]["cur"][1] == YESTERDAY
    assert d["summary"]["rev_delta_pct"] == 0.0 and d["summary"]["gp_margin_prev_pp"] == 0.0


def test_branch_pnl_fixed_cost_is_pro_rated_in_the_month_in_progress(cc):
    ym = YESTERDAY[:7]
    d = cc.get("/api/pnl/branch", params={"month_from": ym, "month_to": ym}).json()
    a = next(b for b in d["branches"] if b["branch"] == "BranchA")
    y, m = int(ym[:4]), int(ym[5:7])
    share = int(YESTERDAY[8:10]) / calendar.monthrange(y, m)[1]
    assert a["months_prorated"] == pytest.approx(round(share, 3), abs=1e-3)
    assert a["fixed_cost_period"] == pytest.approx(31000 * share, abs=1.0)


def test_home_food_cost_is_to_date_against_the_same_days(cc):
    b = cc.get("/api/home/brief").json()
    assert b["food_cost_pct_mtd"] == 10.0
    ch = b["cost_health"]
    assert ch["period"]["cur"][1] == YESTERDAY
    if _covered(P.resolve("month", YESTERDAY)["prev"]):
        assert ch["delta_pp"] == 0.0 and ch["prev_food_pct"] == 10.0
    assert ch["ly_food_pct"] is None and "ly" in ch["reasons"]


def test_pnl_bill_is_to_date_with_bases_inside_the_rolling_window(cc):
    d = cc.get("/api/reports/pnl-bill", params={"gran": "day"}).json()
    assert d["range"] == [YESTERDAY, YESTERDAY] and d["period"]["kind"] == "day"
    assert d["summary_prev"] and d["summary_prev"]["gp_pct"] == d["summary"]["gp_pct"]   # 7 days back is inside the window
    assert d["summary_ly"] is None and d["period"]["reasons"]["ly"]


def test_anomaly_box_cost_headline_is_to_date(cc):
    d = cc.get("/api/analysis/anomalies").json()
    cn = d.get("cost_now")
    assert cn and cn["pct"] == 10.0 and cn["period"]["cur"][1] <= YESTERDAY


# ── the pages: the top bar is their one period control ─────────────────────────────────────────────────────
REPO = HERE.parent
CORE = (REPO / "js" / "01-core.js").read_text(encoding="utf-8")


def _tab(name):
    m = re.search(rf"^  {name}: \{{.*$", CORE, re.M)
    assert m, name
    return m.group(0)


def test_cost_pages_declare_their_kinds():
    want = {"costhead": "['day', 'week', 'month', 'year']", "pnlbill": "['day', 'week', 'month']",
            "cost": "['month', 'year']", "gpbranch": "['week', 'month', 'year', 'range']",
            "bomcost": "['week', 'month', 'year', 'range']", "pnl": "['month', 'year', 'range']",
            "pnlbranch": "['month', 'year', 'range']", "costheadtop60": "['month']", "coverage": "['month', 'year']",
            "menuefficiency": "['month', 'year']", "ingpurchase": "['week', 'month', 'year', 'range']",
            "branchbom": "['week', 'month', 'year', 'range']"}
    for t, k in want.items():
        assert f"period: {{ kinds: {k} }}" in _tab(t), t


def test_cost_page_pickers_are_gone_or_hidden():
    ch = (REPO / "tabs" / "03-costhead.html").read_text(encoding="utf-8")
    assert 'id="chDate"' not in ch and 'id="chGranTrack"' not in ch
    pb = (REPO / "tabs" / "04-pnlbill.html").read_text(encoding="utf-8")
    assert 'id="pbDate"' not in pb and 'id="pbGranTrack"' not in pb
    gp = (REPO / "tabs" / "22-gpbranch.html").read_text(encoding="utf-8")
    assert 'id="gpbMonthFrom"' not in gp and "setGpbGranularity" not in gp
    bc = (REPO / "tabs" / "24-bomcost.html").read_text(encoding="utf-8")
    assert '<label class="hidden">จาก <input type="month" id="bcMonthFrom"' in bc
    pn = (REPO / "tabs" / "30-pnlbranch.html").read_text(encoding="utf-8")
    assert re.search(r'<div class="flex items-center gap-2 hidden">\s*<span[^>]*>แยกตามเดือน', pn)
    assert 'id="chtMonth" class="hidden"' in (REPO / "tabs" / "06-costheadtop60.html").read_text(encoding="utf-8")
    ip = (REPO / "tabs" / "51-ingpurchase.html").read_text(encoding="utf-8")
    assert re.search(r'<span class="hidden">\s*<input type="date" id="ipFrom"', ip)


def test_pro_rated_week_cogs_is_labelled_an_estimate():
    js = (REPO / "js" / "06-features.js").read_text(encoding="utf-8")
    assert "`${m.bucket} · ประมาณการ`" in js
    assert '"estimate": True' in (HERE / "routers" / "profit_pnl.py").read_text(encoding="utf-8")
