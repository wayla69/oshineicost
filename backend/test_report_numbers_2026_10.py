# -*- coding: utf-8 -*-
"""Wrong numbers and broken downloads found in the 2026-10 report review, one test per fix.

1. The 5 % service charge is not revenue (rules.net_revenue_* / v_sales_sku_monthly): the day/week
   buffet GP, the date-range summary, the daily/weekly gross profit, the branch report generator and
   the month comparison each still carried it somewhere.
2. receipt_no repeats across branches: every per-bill join matches the branch too, and the sync
   stages sale_bill_cogs / sale_bill_item per (branch, receipt).
3. Per-head bill brackets use the bill's net revenue: the buffet charge is in other_subtotal, so
   food_subtotal ÷ covers let only the à-la-carte bills through.
4. The run-rate forecast anchors on the last finished day, never the day still trading.
5. A Thai branch name in a download's file name no longer 500s (RFC 5987 filename*).
6. The gross-profit roll-up survives a day without monthly costing (cogs None).
7. The sync's "today" is the Bangkok date on any host (Fly runs on UTC).
8. The ordering page's "last complete month" preset is the whole previous month in UTC+7.
"""
import calendar
import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))
import conftest as _cf  # noqa: E402

THAI = "รังสิต"
DUP = "2026-06-02-1001-1"           # branch 1's fixture receipt, re-used by a branch-2 bill below
NET = 1070.0 / 1.07 - 50.0          # a fixture bill: ฿1,070 paid, ฿50 service charge -> ฿950 revenue


def _swap_db(db):
    import core
    import main
    prev = core.DB_PATH
    core.DB_PATH = db
    main.DB_PATH = db
    main._tls.con = None
    try:
        main.invalidate_cache()
    except Exception:
        pass
    return prev


def _restore_db(prev):
    import core
    import main
    core.DB_PATH = prev
    main.DB_PATH = prev
    main._tls.con = None
    try:
        main.invalidate_cache()
    except Exception:
        pass


@pytest.fixture(scope="module")
def rn_client(tmp_path_factory):
    """The hermetic fixture plus: a branch-2 bill that re-uses a branch-1 receipt_no (with its own
    per-bill cost and menu lines), two buffet bills whose charge sits in other_subtotal, a July bill
    with no monthly costing, and a Thai name on branch 2."""
    db = tmp_path_factory.mktemp("rn") / "test.db"
    _cf._build_db(str(db))
    con = sqlite3.connect(str(db))
    con.execute("UPDATE branches SET name=? WHERE branch_id=2", (THAI,))
    # the table as the sync now builds it: one row per (branch, receipt)
    rows = con.execute("SELECT receipt_no, branch_id, day, cogs_bom FROM sale_bill_cogs").fetchall()
    con.executescript("DROP TABLE sale_bill_cogs; CREATE TABLE sale_bill_cogs(receipt_no TEXT NOT NULL, branch_id INT NOT NULL,"
                      " day TEXT, cogs_bom REAL, PRIMARY KEY (branch_id, receipt_no));"
                      "DROP TABLE sale_bill_item; CREATE TABLE sale_bill_item(receipt_no TEXT, sku_id INT, qty REAL, rev REAL,"
                      " bom_cost REAL, branch_id INT);")
    con.executemany("INSERT INTO sale_bill_cogs VALUES(?,?,?,?)", rows)
    ins = ("INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,food_subtotal,drink_subtotal,"
           "other_subtotal,discount,service_charge,total_inc_vat,source) VALUES(?,?,?,?,?,?,?,?,?,?, 'Easy')")
    con.execute(ins, (2, DUP, "2026-06-02T19:00:00", 1, 500, 0, 0, 0, 0, 535.0))
    con.execute("INSERT INTO sale_bill_cogs VALUES(?,?,?,?)", (DUP, 2, "2026-06-02", 100.0))
    con.execute("INSERT INTO sale_bill_item VALUES(?,?,?,?,?,?)", (DUP, 3, 2, 1000, 20, 1))   # branch 1: (A) x2
    con.execute("INSERT INTO sale_bill_item VALUES(?,?,?,?,?,?)", (DUP, 2, 5, 0, 40, 2))      # branch 2: (B) x5
    for k in (1, 2):   # buffet bills: the charge in other_subtotal, nothing in food_subtotal
        con.execute(ins, (1, f"2026-06-02-9{k}-1", "2026-06-02T18:00:00", 4, 0, 0, 3000, 0, 0, 3210.0))
    con.execute(ins, (1, "2026-07-01-1-1", "2026-07-01T12:00:00", 2, 900, 100, 0, 0, 50, 1070.0))
    con.commit(); con.close()
    prev = _swap_db(db)
    try:
        import main
        with TestClient(main.app) as c:
            yield c
    finally:
        _restore_db(prev)


# ── 1. service charge out of revenue ─────────────────────────────────────────
def test_buffet_gp_day_and_week_add_up_to_month_mode(client):
    month = client.get("/api/buffet/gp", params={"granularity": "month", "month_from": "2026-05", "month_to": "2026-05"}).json()
    assert month["period_total"]["revenue"] == pytest.approx(30000.0)
    for g in ("day", "week"):
        j = client.get("/api/buffet/gp", params={"granularity": g, "month_from": "2026-05", "month_to": "2026-05"}).json()
        assert j["period_total"]["revenue"] == pytest.approx(month["period_total"]["revenue"], abs=0.05), g
        assert j["period_total"]["bom_cost"] == pytest.approx(month["period_total"]["bom_cost"], abs=0.05), g
        assert j["period_total"]["margin_pct"] == pytest.approx(month["period_total"]["margin_pct"], abs=0.01), g


def test_summary_date_range_is_net_of_service_charge(client):
    j = client.get("/api/summary", params={"date_from": "2026-05-01", "date_to": "2026-05-03"}).json()
    assert j["revenue"] == pytest.approx(6 * NET)               # was 6 x 1,000: ÷1.07 only, the charge left in
    assert j["revenue_ex_vat"] == j["revenue"]
    assert j["avg_ticket"] == pytest.approx(NET)


def test_daily_gross_profit_adds_up_to_the_month(client):
    month = client.get("/api/analysis/gross_profit", params={"granularity": "monthly", "month": "2026-05"}).json()
    m_rev = sum(r["revenue"] for r in month)
    assert m_rev == pytest.approx(30000.0)
    for g in ("daily", "weekly"):
        rows = client.get("/api/analysis/gross_profit", params={"granularity": g, "month": "2026-05"}).json()
        assert sum(r["revenue"] for r in rows) == pytest.approx(m_rev, abs=0.05), g        # was 6,000 (shares off-basis)
        assert sum(r["cogs"] for r in rows) == pytest.approx(sum(r["cogs"] for r in month), abs=0.05), g
    # a part window keeps only its days' share of the month: 2 of 3 equal days
    rows = client.get("/api/analysis/gross_profit", params={"granularity": "daily", "date_from": "2026-05-01",
                                                            "date_to": "2026-05-02"}).json()
    assert sum(r["revenue"] for r in rows) == pytest.approx(m_rev * 2 / 3, abs=0.05)


def test_branch_report_generator_leaves_the_service_charge_out(tmp_path):
    db = tmp_path / "g.db"
    _cf._build_db(str(db))
    con = sqlite3.connect(str(db))
    con.execute("INSERT INTO skus(sku_id,sku_code,description,category_id) VALUES(9,'SERVICECHARGE','Service Charge',2)")
    con.execute("INSERT INTO sales_sku_monthly(branch_id,period_month,sku_id,qty,subtotal_ex_vat,tax_total,total_inc_vat,source)"
                " VALUES(1,'2026-05',9,1,750,52.5,802.5,'Easy')")
    con.commit()
    con.row_factory = sqlite3.Row
    import gen_branch_report as G
    m = G.scope_month(con, [1], "2026-05")
    assert m["revenue"] == pytest.approx(15000.0)               # was 15,750 with the SERVICECHARGE line
    assert all(d["code"] != "SERVICECHARGE" for d in G.menu_drivers(con, 1, "2026-04", "2026-05", 100, 100, top_n=50))
    con.close()


def test_compare_net_sales_comes_from_the_bills(client):
    j = client.get("/api/analysis/compare", params={"month": "2026-05", "vs": "2026-04"}).json()
    assert j["mtd"] is False
    assert j["current"]["net_sales"] == pytest.approx(6 * NET)  # the bills, net of the charge (summary row says 30,000)
    assert j["prior"]["net_sales"] == pytest.approx(30000.0)    # no bills in April: the summary row is all there is


# ── 2. receipt_no repeats across branches ────────────────────────────────────
def test_bill_cost_joins_match_the_branch(rn_client):
    s = rn_client.get("/api/buffet/heavy-eater-tail").json()["summary"]
    assert s["bills"] == 13                                     # 12 fixture bills + the branch-2 twin, each once
    assert s["cogs"] == pytest.approx(12 * 520.0 + 100.0)
    j = rn_client.get("/api/sales/heavy-eaters", params={"date_from": "2026-06-02", "date_to": "2026-06-02"}).json()
    assert j["n_bills"] == 3


def test_bill_drill_reads_the_chosen_branch(rn_client):
    j = rn_client.get("/api/reports/pnl-bill-items", params={"receipt": DUP, "branch": THAI}).json()
    assert j["header"]["branch"] == THAI and j["header"]["cust"] == 1
    assert [x["sku_code"] for x in j["items"]] == ["B0001"]     # branch 2's own line, not branch 1's steak
    j = rn_client.get("/api/reports/pnl-bill-items", params={"receipt": DUP, "branch": "BranchA"}).json()
    assert j["header"]["branch"] == "BranchA" and [x["sku_code"] for x in j["items"]] == ["A0001"]


def _stage_twins(old_item_table=False, incr_from=None):
    """Run the real stagers against a FakeMC whose per-bill pull holds ONE receipt at two branches."""
    sync = pytest.importorskip("sync_mssql")
    import test_sync_staging as T
    pat = r"s\.SaleId sid, s\.BranchId bid, s\.SaleNum num"
    twins = [{"sid": 55, "bid": 10, "num": 7, "d": "2026-06-01", "iid": 100, "qty": 2.0, "rev": 500.0},
             {"sid": 55, "bid": 11, "num": 7, "d": "2026-06-01", "iid": 100, "qty": 5.0, "rev": 0.0}]

    class MC(T.FakeMC):
        def execute(self, sql, params=None):
            if re.search(pat, sql):
                self._rows = twins
                return
            super().execute(sql, params)

    sq = sqlite3.connect(":memory:")
    sq.row_factory = sqlite3.Row
    sq.executescript((HERE / "schema.sql").read_text(encoding="utf-8"))
    sq.executescript("CREATE TABLE IF NOT EXISTS bom_sku_alias(bom_sku_code TEXT PRIMARY KEY, resolved_sku_id INTEGER,"
                     " match_method TEXT, confidence REAL, notes TEXT);")
    sq.execute("INSERT INTO branches(branch_id,name) VALUES(1,'BranchA'),(2,'BranchB')")
    sq.execute("INSERT INTO categories(category_id,name) VALUES(1,'Food')")
    sq.execute("INSERT INTO skus(sku_id,sku_code,description,category_id) VALUES(1,'A0001','dish',1)")
    sq.execute("INSERT INTO sku_cost(sku_id,unit_cost) VALUES(1, 10.0)")
    for bid in (1, 2):   # both bills lose money (cost > ฿10 revenue), so their lines are staged
        sq.execute("INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,total_inc_vat,source)"
                   " VALUES(?,'2026-06-01-7-55','2026-06-01T12:00:00', 10.7, 'Easy')", (bid,))
    if old_item_table:   # a DB synced before the column existed, holding an old discount bill's line
        sq.execute("CREATE TABLE sale_bill_item(receipt_no TEXT, sku_id INT, qty REAL, rev REAL, bom_cost REAL)")
        sq.execute("INSERT INTO sale_bill_item VALUES('2025-01-05-1-1', 1, 1, 100, 10)")
    windows = [("2026-06-01", "2026-07-01")] if incr_from else [("2026-01-01", "2026-02-01")]
    sync.stage_reports(sq, MC(), "2026-01-01", bid_map={10: 1, 11: 2}, item_map={100: 1},
                       name2bid={"BranchA": 1, "BranchB": 2}, windows=windows, incr_from=incr_from)
    return sq, T._n_windows(31)


def test_sync_stages_bill_cost_per_branch_and_receipt():
    sq, n = _stage_twins()
    cogs = {r["branch_id"]: r["cogs_bom"] for r in sq.execute("SELECT * FROM sale_bill_cogs")}
    assert cogs == {1: 20.0 * n, 2: 50.0 * n}                   # was one row: branch 1 holding both bills (70 x n)
    lines = {(r["branch_id"], r["qty"]) for r in sq.execute("SELECT * FROM sale_bill_item")}
    assert lines == {(1, 2.0), (2, 5.0)}


def test_sync_adds_branch_id_to_an_older_bill_item_table():
    """An incremental run keeps the full discount-bill history: the older table gains the column in place."""
    sq, _ = _stage_twins(old_item_table=True, incr_from="2026-06-01")
    rows = {(r["receipt_no"], r["branch_id"]) for r in sq.execute("SELECT * FROM sale_bill_item")}
    assert ("2025-01-05-1-1", None) in rows                     # history kept, not rebuilt
    assert {("2026-06-01-7-55", 1), ("2026-06-01-7-55", 2)} <= rows


def test_bill_item_branch_match_is_skipped_on_an_old_table():
    import rules
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE sale_bill_item(receipt_no TEXT, sku_id INT, qty REAL, rev REAL, bom_cost REAL)")
    assert rules.bill_item_branch_sql(con) == ""
    con.execute("ALTER TABLE sale_bill_item ADD COLUMN branch_id INT")
    assert rules.bill_item_branch_sql(con, "i", "st") == " AND (i.branch_id IS NULL OR i.branch_id = st.branch_id)"


# ── 3. per-head brackets on net revenue ──────────────────────────────────────
def test_buffet_per_bill_counts_bills_whose_charge_is_in_other_subtotal(rn_client):
    o = rn_client.get("/api/profit/buffet_per_bill", params={"month_from": "2026-06", "month_to": "2026-06"}).json()["overall"]
    assert o["bills"] == 9                                      # was 7: the two buffet bills had food_subtotal 0
    assert o["tier_mix"].get(">720") == 8                       # 2 x 4 covers at ฿750 a head (3,210 / 1.07 / 4)


# ── 4. forecast anchors on the last finished day ─────────────────────────────
@pytest.fixture(scope="module")
def live_client(tmp_path_factory):
    today = _cf.bkk_today()
    db = tmp_path_factory.mktemp("rnlive") / "test.db"
    _cf._build_db(str(db))
    con = sqlite3.connect(str(db))
    con.execute("DELETE FROM sales_transaction")
    pm = (today.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    days = [pm.replace(day=i) for i in range(1, calendar.monthrange(pm.year, pm.month)[1] + 1)]
    days += [today.replace(day=i) for i in range(1, today.day + 1)]
    for day in days:
        n = 1 if day == today else 10           # today: one early bill, the sliver still trading
        for bid in (1, 2):
            for k in range(n):
                con.execute("INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,food_subtotal,"
                            "drink_subtotal,other_subtotal,discount,service_charge,total_inc_vat,source)"
                            " VALUES(?,?,?,2,300,0,0,0,0,321.0,'Easy')", (bid, f"{day}-{bid}-{k}", f"{day}T12:{k:02d}:00"))
    con.commit(); con.close()
    prev = _swap_db(db)
    try:
        import main
        with TestClient(main.app) as c:
            yield c, today
    finally:
        _restore_db(prev)


@pytest.mark.usefixtures("bkk_frozen")
def test_forecast_leaves_out_the_day_still_trading(live_client):
    c, today = live_client
    yday = today - dt.timedelta(days=1)
    j = c.get("/api/forecast", params={"mode": "month"}).json()
    assert j["available"] and j["period"] == yday.strftime("%Y-%m")
    assert j["days_elapsed"] == yday.day                        # was today's day-of-month
    assert j["mtd"] == pytest.approx(yday.day * 20 * 300.0)     # today's lone bill is not in the run-rate
    assert all(d["day"] <= yday.isoformat() for d in j["daily"])
    b = c.get("/api/forecast", params={"mode": "branches"}).json()
    assert b["days_elapsed"] == yday.day
    assert sum(r["mtd"] for r in b["branches"]) == pytest.approx(yday.day * 20 * 300.0)


# ── 5. Thai file names in downloads ──────────────────────────────────────────
def test_feedback_daily_report_download_with_a_thai_branch(rn_client):
    r = rn_client.get("/api/feedback/daily-report.xlsx", params={"date": "2026-06-02", "branch": THAI})
    assert r.status_code == 200                                 # was UnicodeEncodeError -> 500
    assert _cf.dl_name(r) == f"FEEDBACK_REPORT_2026-06-02_2026-06-02_{THAI}.xlsx"
    r.headers["content-disposition"].encode("latin-1")          # the header itself is plain latin-1


def test_attachment_header_fallback_is_ascii():
    from downloads import attachment
    h = attachment(f"รายงาน_{THAI}.xlsx")
    h.encode("latin-1")
    assert 'filename="download.xlsx"' in h and "filename*=UTF-8''" in h
    assert attachment("buffet_gp_2026-09_2026-09.xlsx").startswith('attachment; filename="buffet_gp_2026-09_2026-09.xlsx"')


# ── 6. gross-profit roll-up with an uncosted day ─────────────────────────────
def test_gross_profit_rollup_survives_a_day_without_monthly_costing(rn_client):
    for g in ("daily", "weekly"):
        r = rn_client.get("/api/analysis/gross_profit", params={"granularity": g, "month": "2026-07"})
        assert r.status_code == 200, (g, r.text[:200])          # was TypeError: += None
        rows = r.json()
        assert sum(x["revenue"] for x in rows) == pytest.approx(NET)
        assert sum(x["cogs"] for x in rows) == 0


# ── 7. the sync's Bangkok day ────────────────────────────────────────────────
def test_sync_today_is_bangkok_not_the_host_clock(monkeypatch):
    sync = pytest.importorskip("sync_mssql")
    want = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=7)).date()
    assert sync.bkk_today() in (want, want + dt.timedelta(days=1))     # (a run that crosses Bangkok midnight)
    monkeypatch.setattr(sync, "bkk_today", lambda: dt.date(2026, 11, 1))   # 06:00 Bangkok on the 1st = 23:00 UTC on the 31st
    assert sync._month_windows("2026-09-01")[-1] == ("2026-11-01", "2026-12-01")
    src = (HERE / "sync_mssql.py").read_text(encoding="utf-8")
    assert "date.today()" not in src.replace("date.today() is the UTC date", "")


# ── 8. ordering page "last complete month" preset ────────────────────────────
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_order_plan_last_month_preset_in_bangkok():
    js = (HERE.parent / "js" / "10-ordering.js").read_text(encoding="utf-8")
    a = js.index("function opQuickRange(kind)")
    b = js.index("\nfunction ", a + 10)
    fn = js[a:b]
    src = ("const els = {opFrom: {value: ''}, opTo: {value: ''}};"
           "const document = {getElementById: id => els[id]};"
           "let _opLastMeta = null; function loadOrderPlan() {}\n" + fn +
           "\nconst out = [];"
           "for (const d of ['2026-10-22', '2026-01-15', '2024-03-01']) {"
           "  _opLastMeta = {date_to: d}; opQuickRange('month'); out.push([els.opFrom.value, els.opTo.value]); }"
           "process.stdout.write(JSON.stringify(out));")
    env = dict(os.environ, TZ="Asia/Bangkok")
    res = subprocess.run([NODE, "-e", src], capture_output=True, text=True, encoding="utf-8", timeout=60, env=env)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout) == [["2026-09-01", "2026-09-30"], ["2025-12-01", "2025-12-31"], ["2024-02-01", "2024-02-29"]]
