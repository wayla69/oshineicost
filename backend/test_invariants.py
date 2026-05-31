"""
Hermetic regression tests for the Oshinei cost-analysis invariants.

These lock in the hard rules and the 2026-05-31 review fixes so they can't
silently regress. They build a tiny in-memory SQLite DB (no server, no real
oshinei.db needed) and exercise the canonical SQL helpers + validators in main.

Run:  cd backend && python -m pytest test_invariants.py -v
      (pip install pytest  if not present)
"""
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import main  # noqa: E402  (path set above)
from fastapi import HTTPException  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def fresh_db() -> sqlite3.Connection:
    """In-memory DB with the real schema and a minimal, controlled dataset."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript((HERE / "schema.sql").read_text(encoding="utf-8"))

    con.executemany("INSERT INTO branches(branch_id,name) VALUES(?,?)",
                    [(1, "Test Branch")])
    con.executemany("INSERT INTO categories(category_id,name) VALUES(?,?)",
                    [(1, "BUFFET"), (2, "A La Carte")])
    con.executemany(
        "INSERT INTO skus(sku_id,sku_code,description,category_id,unit) VALUES(?,?,?,?,?)",
        [
            (1, "BUFFET", "Buffet tier", 1, "pax"),   # tier SKU -> cost must be excluded
            (2, "A0001",  "Steak",       2, "plate"),  # normal costed SKU
            (3, "A0002",  "Salad",       2, "plate"),  # normal but UNcosted SKU
        ],
    )
    # Default lifetime costs (note: the tier SKU intentionally HAS a cost that
    # must be ignored by _cogs_case to avoid double-counting with item rows).
    con.executemany("INSERT INTO sku_cost(sku_id,unit_cost) VALUES(?,?)",
                    [(1, 99.0), (2, 10.0)])  # sku 3 deliberately uncosted
    con.commit()
    return con


def add_sales(con, sku_id, period, qty, ex_vat, source="Easy"):
    inc = round(ex_vat * 1.07, 4)
    con.execute(
        "INSERT INTO sales_sku_monthly"
        "(branch_id,period_month,sku_id,qty,subtotal_ex_vat,tax_total,total_inc_vat,source)"
        " VALUES(1,?,?,?,?,?,?,?)",
        (period, sku_id, qty, ex_vat, round(inc - ex_vat, 4), inc, source),
    )
    con.commit()


# ---------------------------------------------------------------------------
# Rule 1 — VAT is never revenue / fixtures keep ex and inc distinct
# ---------------------------------------------------------------------------
def test_inc_vat_is_distinct_from_ex_vat():
    con = fresh_db()
    add_sales(con, 2, "2025-06", qty=10, ex_vat=1000.0)
    r = con.execute("SELECT subtotal_ex_vat, total_inc_vat FROM sales_sku_monthly").fetchone()
    assert r["subtotal_ex_vat"] == 1000.0
    assert abs(r["total_inc_vat"] - 1070.0) < 1e-6
    assert r["total_inc_vat"] > r["subtotal_ex_vat"]   # VAT inflates the gross


# ---------------------------------------------------------------------------
# Rule 3 — COGS via _cogs_case: tier SKUs contribute ZERO cost
# ---------------------------------------------------------------------------
def test_cogs_excludes_buffet_tier():
    con = fresh_db()
    add_sales(con, 1, "2025-06", qty=5,  ex_vat=2500.0)   # BUFFET tier (cost must be 0)
    add_sales(con, 2, "2025-06", qty=10, ex_vat=1000.0)   # A0001 (cost = 10*10 = 100)
    rows = con.execute(f"""
        SELECT sk.sku_code AS code, SUM({main._cogs_case()}) AS cogs
        FROM sales_sku_monthly ssm
        JOIN skus sk ON sk.sku_id = ssm.sku_id
        LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
        GROUP BY sk.sku_code
    """).fetchall()
    cogs = {r["code"]: r["cogs"] for r in rows}
    assert cogs["BUFFET"] == 0          # tier excluded despite having unit_cost 99
    assert cogs["A0001"] == 10 * 10.0   # normal SKU costed normally


def test_cogs_zero_for_uncosted_non_tier():
    con = fresh_db()
    add_sales(con, 3, "2025-06", qty=4, ex_vat=400.0)     # A0002 has no sku_cost
    val = con.execute(f"""
        SELECT SUM({main._cogs_case()}) AS cogs
        FROM sales_sku_monthly ssm
        JOIN skus sk ON sk.sku_id = ssm.sku_id
        LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
    """).fetchone()["cogs"]
    assert val == 0   # uncosted -> contributes 0, not NULL-crash


# ---------------------------------------------------------------------------
# Rule 4 — _effective_cost_subquery COALESCE: monthly exact -> prior -> default
# ---------------------------------------------------------------------------
def test_effective_cost_resolution_order():
    con = fresh_db()
    # default for A0001 is 10. Add monthly overrides.
    con.executemany(
        "INSERT INTO sku_cost_monthly(sku_id,period_month,unit_cost) VALUES(2,?,?)",
        [("2025-05", 7.0), ("2025-06", 5.0)],
    )
    con.commit()
    # rows in three periods so each row's period drives resolution
    add_sales(con, 2, "2025-06", qty=1, ex_vat=100.0)  # exact monthly -> 5
    add_sales(con, 2, "2025-07", qty=1, ex_vat=100.0)  # no July -> carry June? prior = latest < 2025-07 = 2025-06 -> 5
    add_sales(con, 2, "2025-04", qty=1, ex_vat=100.0)  # before any monthly -> default 10

    eff = main._effective_cost_subquery()
    rows = con.execute(f"""
        SELECT ssm.period_month AS m, {eff} AS cost
        FROM sales_sku_monthly ssm
        LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
        WHERE ssm.sku_id = 2
        ORDER BY ssm.period_month
    """).fetchall()
    cost = {r["m"]: r["cost"] for r in rows}
    assert cost["2025-04"] == 10.0   # default (no monthly at/ before)
    assert cost["2025-06"] == 5.0    # exact monthly
    assert cost["2025-07"] == 5.0    # carry-forward from latest prior (June)


# ---------------------------------------------------------------------------
# _costed_rev_case — tier revenue counted, uncosted non-tier excluded
# ---------------------------------------------------------------------------
def test_costed_rev_case():
    con = fresh_db()
    add_sales(con, 1, "2025-06", qty=5,  ex_vat=2500.0)   # tier -> counts as costed revenue
    add_sales(con, 2, "2025-06", qty=10, ex_vat=1000.0)   # costed -> counts
    add_sales(con, 3, "2025-06", qty=4,  ex_vat=400.0)    # uncosted non-tier -> 0
    rows = con.execute(f"""
        SELECT sk.sku_code AS code, {main._costed_rev_case()} AS rev
        FROM sales_sku_monthly ssm
        JOIN skus sk ON sk.sku_id = ssm.sku_id
        LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
    """).fetchall()
    rev = {r["code"]: r["rev"] for r in rows}
    assert rev["BUFFET"] == 2500.0   # tier revenue is included
    assert rev["A0001"] == 1000.0    # costed revenue included
    assert rev["A0002"] == 0         # uncosted revenue excluded from costed base


# ---------------------------------------------------------------------------
# A6 — ISO week SQL matches Python isocalendar(), incl. year boundaries
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("d", [
    "2025-12-29", "2026-01-01", "2025-01-01", "2024-12-31",
    "2025-06-15", "2025-05-27", "2027-01-04", "2023-01-01",
])
def test_iso_week_matches_isocalendar(d):
    con = sqlite3.connect(":memory:")
    expr = main._iso_week_sql(f"'{d}'")
    sql_val = con.execute(f"SELECT {expr}").fetchone()[0]
    y, w, _ = date.fromisoformat(d).isocalendar()
    assert sql_val == f"{y}-W{w:02d}"


# ---------------------------------------------------------------------------
# C3 — _ym_parts validates and raises 400 on bad input
# ---------------------------------------------------------------------------
def test_ym_parts_valid():
    assert main._ym_parts("2025-06") == (2025, 6)
    assert main._ym_parts(" 2025-12 ") == (2025, 12)


@pytest.mark.parametrize("bad", ["garbage", "2025-13", "2025-00", "25-6", "2025/06", "", "2025-6"])
def test_ym_parts_rejects_bad(bad):
    with pytest.raises(HTTPException) as ei:
        main._ym_parts(bad)
    assert ei.value.status_code == 400


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
