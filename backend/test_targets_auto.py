# -*- coding: utf-8 -*-
"""Targets roll forward on their own: a branch's target for a month is its own
actual of the same month a year earlier (net revenue + covers); a typed target
wins; a branch without last year's month has none. The API, the home brief and
the management report all read the one helper."""
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import targets as T  # noqa: E402


def _db():
    con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE branches(branch_id INTEGER PRIMARY KEY, name TEXT, branch_type TEXT, active INTEGER DEFAULT 1);
    INSERT INTO branches VALUES (1,'ALL','master',1),(2,'A','master',1),(3,'B','franchise',1),(4,'New','master',1);
    CREATE TABLE branch_targets(id INTEGER PRIMARY KEY AUTOINCREMENT, branch_id INTEGER, period_month TEXT,
                                revenue_target REAL DEFAULT 0, customer_target INTEGER DEFAULT 0, UNIQUE(branch_id, period_month));
    CREATE TABLE sales_transaction(branch_id INTEGER, sold_at TEXT, total_inc_vat REAL, service_charge REAL, customer_count INTEGER, source TEXT);
    """)
    # September 2025: A sold two bills, B one; nothing for 'New'; a CM-era row must not count
    con.executemany("INSERT INTO sales_transaction VALUES (?,?,?,?,?,?)", [
        (2, "2025-09-03 12:00:00", 1070.0, 50.0, 4, "Easy"),      # net 950
        (2, "2025-09-20 19:00:00", 2140.0, 0.0, 6, "Easy"),       # net 2000
        (3, "2025-09-10 18:00:00", 535.0, 0.0, 2, "Easy"),        # net 500
        (3, "2025-09-11 18:00:00", 9999.0, 0.0, 9, "CM"),
        (2, "2025-10-01 12:00:00", 1070.0, 0.0, 1, "Easy"),       # next month — not this basis
    ])
    return con


def test_prev_year_month():
    assert T.prev_year_month("2026-09") == "2025-09" and T.prev_year_month("2027-01") == "2026-01"


def test_last_year_actual_becomes_the_target_and_typed_wins():
    con = _db()
    rows = {r["branch"]: r for r in T.targets_for_month(con, "2026-09")}
    assert set(rows) == {"A", "B", "New"}                                  # 'ALL' never
    a = rows["A"]
    assert a["source"] == "last_year" and a["basis_month"] == "2025-09"
    assert a["revenue_target"] == 2950.0 and a["customer_target"] == 10       # 950 + 2000 net, 4 + 6 covers
    assert rows["B"]["revenue_target"] == 500.0 and rows["B"]["customer_target"] == 2   # the CM row is ignored
    n = rows["New"]
    assert n["source"] == "none" and n["revenue_target"] == 0 and n["customer_target"] == 0
    assert T.target_revenue_total(con, "2026-09") == 3450.0
    assert T.target_revenue_total(con, "2026-09", branch="A") == 2950.0
    assert T.target_revenue_total(con, "2026-09", group="franchise") == 500.0
    # a typed target for B wins; A stays automatic; the basis is still reported
    con.execute("INSERT INTO branch_targets(branch_id, period_month, revenue_target, customer_target) VALUES (3, '2026-09', 800, 5)")
    rows = {r["branch"]: r for r in T.targets_for_month(con, "2026-09")}
    assert rows["B"]["source"] == "manual" and rows["B"]["revenue_target"] == 800 and rows["B"]["customer_target"] == 5
    assert rows["B"]["basis_revenue"] == 500.0 and rows["A"]["source"] == "last_year"
    # a typed 0 means "back to automatic"
    con.execute("UPDATE branch_targets SET revenue_target = 0 WHERE branch_id = 3")
    assert {r["branch"]: r["source"] for r in T.targets_for_month(con, "2026-09")}["B"] == "last_year"
    # the year after: 2027-09 is measured against 2026-09, which has nothing yet
    assert all(r["source"] == "none" for r in T.targets_for_month(con, "2027-09"))


def test_api_and_readers_use_the_helper(client):
    j = client.get("/api/targets", params={"month": "2026-06"}).json()
    assert j and {"branch", "revenue_target", "customer_target", "source", "basis_month"} <= set(j[0])
    assert all(r["source"] in ("manual", "last_year", "none") for r in j) and j[0]["basis_month"] == "2025-06"
    assert client.get("/api/targets").status_code == 200                 # no month → the latest data month
    # a typed target through the API shows up as manual and drives the same rows
    r = client.put("/api/targets", json=[{"branch": j[0]["branch"], "period_month": "2026-06", "revenue_target": 123456, "customer_target": 7}])
    assert r.status_code == 200 and r.json()["saved"] == 1
    try:
        j2 = {x["branch"]: x for x in client.get("/api/targets", params={"month": "2026-06"}).json()}
        assert j2[j[0]["branch"]]["source"] == "manual" and j2[j[0]["branch"]]["revenue_target"] == 123456
        m = client.get("/api/reports/management", params={"period": "monthly"}).json()
        pace = m.get("pace") or {}
        assert "target" in pace                                            # the report's pace block still reads a target
    finally:
        client.put("/api/targets", json=[{"branch": j[0]["branch"], "period_month": "2026-06", "revenue_target": 0, "customer_target": 0}])
        import core
        core.invalidate_cache()
