"""Owner 2026-10-26 "Hide closed branches": ONE rule (rules.closed_branches) decides who a current-period branch table leaves
out - a branch with no sale in the period on screen AND none in its comparison period(s). A branch marked closed
(branches.active = 0) that sold in one of them stays a row (owner decision 2026-10-26: the rows add up to the chain total);
the mark alone counts only where there is no sales window (targets) or nobody sold at all. A branch picker lists the open
branches; the closed ones (rules.picker_closed_branches) come with closed=true under include_inactive, for the
"สาขาที่ปิดแล้ว" fold, so their history stays reachable.

The module adds three branches to the session fixture and takes them out again afterwards (the shared goldens never see them):
  ClosedX  marked closed (active 0), bills on PREV_MONTH-02 only (the base of the latest month: shown there, as it traded;
           hidden from a window where neither it nor its base has a bill)
  IdleY    open (active 1), a month row in 2025-12 only (has history, idle > 90 days: a picker leaves it out)
  NewZ     open, no sales at all yet (a branch being set up: the picker keeps it)
"""
import sqlite3
from pathlib import Path

import pytest

from conftest import EARLY_MONTH, LATEST_MONTH, PREV_MONTH

HERE = Path(__file__).parent
CLOSED, IDLE, NEW = "ClosedX", "IdleY", "NewZ"


@pytest.fixture(scope="module")
def shops(client, app_db):
    import core
    con = sqlite3.connect(str(app_db))
    con.executemany("INSERT INTO branches(branch_id,name,branch_type,active) VALUES(?,?,?,?)",
                    [(91, CLOSED, "master", 0), (92, IDLE, "master", 1), (93, NEW, "franchise", 1)])
    for i in range(3):
        con.execute("INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,food_subtotal,drink_subtotal,"
                    "other_subtotal,discount,service_charge,total_inc_vat,source) VALUES(91,?,?,4,900,100,0,0,0,1070.0,'Easy')",
                    (f"CX-{i}", f"{PREV_MONTH}-02T1{i}:00:00"))
    con.execute("INSERT INTO sales_sku_monthly(branch_id,period_month,sku_id,qty,subtotal_ex_vat,tax_total,total_inc_vat,source)"
                " VALUES(92,'2025-12',3,5,500,35,535,'Easy')")
    con.commit()
    core.invalidate_cache(rebuild_cost=False)
    yield con
    con.execute("DELETE FROM sales_transaction WHERE branch_id IN (91,92,93)")
    con.execute("DELETE FROM sales_sku_monthly WHERE branch_id IN (91,92,93)")
    con.execute("DELETE FROM branches WHERE branch_id IN (91,92,93)")
    con.commit(); con.close()
    core.invalidate_cache(rebuild_cost=False)


# ── the helper ───────────────────────────────────────────────────────────────
def test_a_closed_branch_is_hidden_in_a_window_it_did_not_trade_in(shops):
    import rules
    got = rules.closed_branches(shops, [(f"{LATEST_MONTH}-01", f"{LATEST_MONTH}-03")])
    assert {CLOSED, IDLE, NEW} <= got and "BranchA" not in got and "BranchB" not in got


def test_an_open_branch_shows_in_a_window_where_it_traded(shops):
    import rules
    assert IDLE not in rules.closed_branches(shops, ["2025-12"])                         # its month row counts
    assert IDLE not in rules.closed_branches(shops, [LATEST_MONTH, ("2025-12-01", "2025-12-31")])   # traded in a base: shown


def test_a_closed_branch_that_traded_in_the_period_or_its_base_is_shown(shops):
    import rules
    assert CLOSED not in rules.closed_branches(shops, [(f"{PREV_MONTH}-01", f"{PREV_MONTH}-31")])          # sold in the period
    assert CLOSED not in rules.closed_branches(shops, [(f"{LATEST_MONTH}-01", f"{LATEST_MONTH}-03"),
                                                       (f"{PREV_MONTH}-01", f"{PREV_MONTH}-03")])           # sold in the base


def test_a_closed_branch_with_no_sale_in_the_period_nor_its_base_is_hidden_unless_asked_for(shops):
    import rules
    w = [(f"{LATEST_MONTH}-01", f"{LATEST_MONTH}-03"), EARLY_MONTH]
    assert CLOSED in rules.closed_branches(shops, w) and 91 in rules.closed_branches(shops, w, ids=True)
    assert CLOSED not in rules.closed_branches(shops, w, keep=CLOSED)                     # the page is filtered to it


def test_no_window_or_an_empty_one_counts_only_the_flag(shops):
    import rules
    assert rules.closed_branches(shops) == {CLOSED}
    assert rules.closed_branches(shops, ["2031-01"]) == {CLOSED}                          # nobody traded: never empties a table
    assert rules.closed_branches(shops, [None, ("", "")]) == {CLOSED}


def test_a_slim_database_without_the_flag_or_the_month_table_reads_what_is_there():
    import rules
    con = sqlite3.connect(":memory:")
    con.executescript("CREATE TABLE branches(branch_id INTEGER PRIMARY KEY, name TEXT, branch_type TEXT);"
                      "INSERT INTO branches VALUES (1,'A','master'),(2,'B','master');"
                      "CREATE TABLE sales_transaction(branch_id INTEGER, sold_at TEXT, source TEXT);"
                      "INSERT INTO sales_transaction VALUES (1,'2031-02-03T12:00:00','Easy');")
    assert rules.closed_branches(con) == set()
    assert rules.closed_branches(con, ["2031-02"]) == {"B"}
    assert rules.picker_closed_branches(con) == set()                    # B has no history: a new branch stays


def test_drop_closed_keeps_order_and_reads_names_ids_or_a_key():
    import rules
    rows = [{"branch": "A", "id": 1}, {"branch": "B", "id": 2}, {"branch": "C", "id": 3}]
    assert [r["branch"] for r in rules.drop_closed(rows, {"B"})] == ["A", "C"]
    assert [r["id"] for r in rules.drop_closed(rows, {1}, key="id")] == [2, 3]
    assert rules.drop_closed(rows, set()) == rows
    assert rules.month_window("2026-05") == ("2026-05", "2026-05") and rules.month_window() == ("0000-01", "9999-12")


def test_the_picker_set_is_flagged_or_idle_with_history(shops):
    import rules
    got = rules.picker_closed_branches(shops)
    assert CLOSED in got and IDLE in got and NEW not in got and "BranchA" not in got


# ── endpoints: three current-period tables ───────────────────────────────────
def _names(rows):
    return [r["branch"] for r in rows]


def test_daily_sales_hides_a_closed_branch_with_no_sale_in_the_day_nor_its_bases(client, shops):
    d = client.get("/api/sales/daily", params={"gran": "day", "date": f"{LATEST_MONTH}-03"}).json()
    assert CLOSED not in _names(d["branches"]) and {"BranchA", "BranchB"} <= set(_names(d["branches"]))


def test_daily_sales_shows_a_closed_branch_that_sold_in_the_base_so_the_rows_add_up(client, shops):
    d = client.get("/api/sales/daily?gran=month").json()                                # base = PREV_MONTH 1-3: ClosedX sold
    row = next(r for r in d["branches"] if r["branch"] == CLOSED)
    assert row["revenue"] == 0 and row["mom_rev"] > 0
    assert d["total"]["prev"]["bills"] == 3 + 2 * 3                                      # 3 ClosedX bills + 3 days x 2 branches
    assert round(sum(r["mom_rev"] or 0 for r in d["branches"]), 2) == d["total"]["prev"]["revenue"]
    one = client.get("/api/sales/daily", params={"gran": "month", "branch": CLOSED, "date": f"{PREV_MONTH}-15"}).json()
    assert _names(one["branches"]) == [CLOSED]                                          # reachable through the picker's fold


def test_the_branch_heat_map_shows_a_closed_branch_only_in_a_span_it_traded_in(client, shops):
    q = {"granularity": "daily"}
    rows = client.get("/api/analysis/heatmap_branch", params={**q, "date_from": f"{PREV_MONTH}-01", "date_to": f"{PREV_MONTH}-03"}).json()
    assert CLOSED in _names(rows) and "BranchA" in _names(rows)
    rows = client.get("/api/analysis/heatmap_branch", params={**q, "date_from": f"{LATEST_MONTH}-01", "date_to": f"{LATEST_MONTH}-03"}).json()
    assert CLOSED not in _names(rows) and "BranchA" in _names(rows)


def test_the_management_league_and_period_sales_keep_a_closed_branch_where_it_traded(client, shops):
    j = client.get("/api/reports/management", params={"period": "monthly", "month": PREV_MONTH}).json()
    assert CLOSED in _names(j["branches"]) and "BranchA" in _names(j["branches"])
    s = client.get("/api/period/sales", params={"kind": "month", "pick": f"{PREV_MONTH}-15", "by_branch": 1}).json()
    assert CLOSED in _names(s["branches"]) and "BranchA" in _names(s["branches"])


def test_pnl_and_period_sales_leave_it_out_where_it_did_not_trade(client, shops):
    p = client.get("/api/pnl/branch", params={"month": EARLY_MONTH}).json()
    assert CLOSED not in _names(p["branches"]) and "BranchA" in _names(p["branches"])
    s = client.get("/api/period/sales", params={"kind": "day", "pick": f"{LATEST_MONTH}-03", "by_branch": 1}).json()
    assert CLOSED not in _names(s["branches"]) and "BranchA" in _names(s["branches"])


def test_targets_leave_a_closed_branch_out(client, shops):
    assert CLOSED not in _names(client.get("/api/targets", params={"month": LATEST_MONTH}).json())


# ── the picker ───────────────────────────────────────────────────────────────
def test_the_branch_picker_lists_open_branches_and_flags_the_closed_ones(client, shops):
    open_ = [b["name"] for b in client.get("/api/branches").json()]
    assert CLOSED not in open_ and IDLE not in open_ and NEW in open_ and "BranchA" in open_
    allb = {b["name"]: b for b in client.get("/api/branches?include_inactive=true").json()}
    assert allb[CLOSED]["closed"] and allb[IDLE]["closed"] and not allb[NEW]["closed"] and not allb["BranchA"]["closed"]


def test_the_top_bar_and_the_phone_sheet_fold_the_closed_branches():
    core = (HERE.parent / "js" / "01-core.js").read_text(encoding="utf-8")
    tabs = (HERE.parent / "js" / "03-tabs-main.js").read_text(encoding="utf-8")
    mob = (HERE.parent / "js" / "09c-mobile.js").read_text(encoding="utf-8")
    assert "function branchSelectFill(" in core and "'สาขาที่ปิดแล้ว'" in core and "o.dataset.closed = '1'" in core
    assert "/api/branches?include_inactive=true`);" in tabs and "branchSelectFill(sel, data)" in tabs
    assert 'class="phb-closed"' in mob and "สาขาที่ปิดแล้ว (${shut.length})" in mob
