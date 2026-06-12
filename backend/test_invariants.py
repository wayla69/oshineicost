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
    # BoM tables (the unified COGS expr, 2026-06-02, resolves recipe cost first
    # then falls back to sku_cost). Create them if schema.sql didn't, and leave
    # them EMPTY so A0001 falls back to its sku_cost (10.0) — keeping the original
    # test expectations valid while exercising the unified expression.
    con.executescript("""
      CREATE TABLE IF NOT EXISTS bom_ingredients (
        sku_id INTEGER, seq INTEGER, ingredient_code TEXT, ingredient_name TEXT,
        qty_per_serving REAL, unit TEXT, price_per_unit REAL, ingredient_cost REAL,
        ingredient_type TEXT, source_file TEXT);
      CREATE TABLE IF NOT EXISTS bom_sku_alias (
        bom_sku_code TEXT, resolved_sku_id INTEGER);
    """)
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


# ---------------------------------------------------------------------------
# R1 — VAT is centralized and ex_vat() is correct (guards the persona-style bug)
# ---------------------------------------------------------------------------
def test_vat_constants_and_helper():
    assert main.VAT_RATE == 0.07
    assert abs(main.VAT_DIVISOR - 1.07) < 1e-9
    assert abs(main.ex_vat(1070.0) - 1000.0) < 1e-6
    assert main.ex_vat(0) == 0
    assert main.ex_vat(None) == 0


def test_per_head_is_ex_vat_not_inc_vat():
    """Spend-per-head must be ex-VAT. Locks in the persona fix: a bill of
    inc-VAT 1070 for 2 covers is 500 ex-VAT/head, NOT 535."""
    con = fresh_db()
    con.execute(
        "INSERT INTO sales_transaction"
        "(branch_id,receipt_no,sold_at,customer_count,food_subtotal,drink_subtotal,"
        " other_subtotal,total_inc_vat,source) VALUES(1,'R1','2025-06-01T12:00:00',2,1000,0,0,1070.0,'Easy')"
    )
    con.commit()
    per_head = con.execute(
        "SELECT SUM(total_inc_vat)/1.07/NULLIF(SUM(customer_count),0) FROM sales_transaction"
    ).fetchone()[0]
    assert abs(per_head - 500.0) < 1e-6           # ex-VAT
    assert abs(per_head - 535.0) > 1.0            # would be the inc-VAT bug


# ---------------------------------------------------------------------------
# R2 — price-as-cost guard (the recurrence defense): catches price loaded as
#      cost, never blocks a genuine (fractional) food cost, exempts nothing by
#      itself (tier exemption is applied at the call site).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("unit_cost,ex_price,expected", [
    (107.0, 100.0, True),    # exactly price x 1.07  -> bogus
    (104.0, 100.0, True),    # within band
    (112.0, 100.0, True),    # top of band
    (30.0,  100.0, False),   # real 30% food cost   -> fine
    (50.0,  100.0, False),   # real 50% food cost   -> fine
    (120.0, 100.0, False),   # above band (data weird, but not the VAT signature)
    (0,     100.0, False),   # no cost
    (50.0,  0,     False),   # no price to compare
    (50.0,  None,  False),   # price unknown -> cannot judge -> allowed
])
def test_is_price_as_cost(unit_cost, ex_price, expected):
    assert main.is_price_as_cost(unit_cost, ex_price) is expected


def test_price_as_cost_band_brackets_vat_multiplier():
    # The band must straddle the VAT divisor so the canonical signature is caught.
    assert main.PRICE_AS_COST_LO < main.VAT_DIVISOR < main.PRICE_AS_COST_HI


# ---------------------------------------------------------------------------
# R3 — unified COGS must be alias-safe (2026-06-02 overhaul).
# The unified expr embeds a correlated subquery that joins `skus`; if its inner
# alias collides with the caller's outer alias, COGS inflates ~40×. This locks
# in that COGS is identical whether the sales table is aliased 'ssm' or 's'.
# ---------------------------------------------------------------------------
def test_unified_cogs_alias_safe():
    con = fresh_db()
    add_sales(con, 2, "2025-06", qty=10, ex_vat=1000.0)   # A0001, sku_cost 10.0
    add_sales(con, 1, "2025-06", qty=5,  ex_vat=2500.0)   # BUFFET tier -> 0 cost
    def cogs(alias):
        expr = main._unified_cogs_expr(alias)
        sql = (f"SELECT ROUND(SUM({expr}),2) FROM sales_sku_monthly {alias} "
               f"JOIN skus sk ON sk.sku_id = {alias}.sku_id")
        return con.execute(sql).fetchone()[0]
    c_ssm = cogs("ssm")
    c_s   = cogs("s")     # the alias that used to collide
    assert c_ssm == c_s, f"alias collision: ssm={c_ssm} vs s={c_s}"
    assert c_ssm == 100.0, f"expected 10 qty x 10.0 cost = 100 (tier excluded), got {c_ssm}"


# ---------------------------------------------------------------------------
# R4 — endpoint audit fixes (2026-06-02). Lock in the two data-correctness
# fixes found during the full endpoint audit so they can't silently regress.
# ---------------------------------------------------------------------------
import inspect  # noqa: E402


def test_sph_endpoint_uses_ex_vat():
    """Spend-per-head must be ex-VAT: total_inc_vat / 1.07 / customer_count.
    Regression: it used to divide raw total_inc_vat by customer_count (+7%)."""
    src = inspect.getsource(inspect.unwrap(main.analysis_sph))
    assert "/ 1.07" in src, "SPH no longer divides by VAT divisor"
    assert "total_inc_vat / st.customer_count" not in src, \
        "SPH regressed to VAT-inflated raw inc-VAT / customer_count"


def test_buffet_per_bill_uses_real_cost_ratio():
    """Per-bill cost must use the real per-month buffet food-cost ratio, not the
    old fabricated 'tier_price * 0.55' (flat 45%-margin) guess."""
    src = inspect.getsource(inspect.unwrap(main.buffet_per_bill))
    assert "ratio_by_month" in src, "buffet_per_bill no longer uses real cost ratio"
    assert "* 0.55" not in src, "buffet_per_bill regressed to fabricated 45%-margin guess"


def test_buffet_food_cost_ratio_logic():
    """Buffet food cost % = unified COGS of buffet ITEM rows ÷ buffet TIER revenue.
    Tier rows carry revenue but ZERO cost; item rows carry the cost."""
    con = fresh_db()
    # The trimmed test schema has no business_type column — add it (real schema has it).
    cols = [r["name"] for r in con.execute("PRAGMA table_info(skus)").fetchall()]
    if "business_type" not in cols:
        con.execute("ALTER TABLE skus ADD COLUMN business_type TEXT")
    # Make sku 1 the buffet tier (revenue, cost excluded) and add a buffet item.
    con.execute("UPDATE skus SET business_type='buffet_tier' WHERE sku_id=1")
    con.execute("INSERT INTO skus(sku_id,sku_code,description,category_id,unit,business_type)"
                " VALUES(4,'B0001','Buffet item',1,'plate','buffet')")
    con.execute("INSERT INTO sku_cost(sku_id,unit_cost) VALUES(4, 8.0)")
    con.commit()
    add_sales(con, 1, "2025-06", qty=100, ex_vat=10000.0)  # tier: revenue 10000, cost 0
    add_sales(con, 4, "2025-06", qty=500, ex_vat=0.0)       # item: cost 500*8 = 4000
    expr = main._unified_cogs_expr("sm", "sk")
    row = con.execute(f"""
        SELECT SUM(CASE WHEN sk.business_type='buffet_tier' THEN sm.subtotal_ex_vat ELSE 0 END) AS rev,
               SUM({expr}) AS cogs
        FROM sales_sku_monthly sm JOIN skus sk ON sk.sku_id = sm.sku_id
        WHERE sk.business_type IN ('buffet','buffet_tier')
    """).fetchone()
    assert row["rev"] == 10000.0          # only tier rows count as buffet revenue
    assert row["cogs"] == 4000.0          # tier=0 + item 500*8
    assert round(row["cogs"] / row["rev"] * 100, 1) == 40.0  # food cost %


# ---------------------------------------------------------------------------
# R5 — _bom_unit_cost_sql (waste-BoM, cost-drivers): sku_cost (non-zero) →
#      recipe → alias → 0, with BUFFET tier forced to 0. Mirrors _unified_cogs_expr
#      but per-serving (cost-management table first since 2026-06-11).
# ---------------------------------------------------------------------------
def test_bom_unit_cost_sql_tier_zero_and_fallback():
    con = fresh_db()
    expr = main._bom_unit_cost_sql("s")
    rows = con.execute(f"SELECT s.sku_code AS code, {expr} AS uc FROM skus s").fetchall()
    uc = {r["code"]: r["uc"] for r in rows}
    assert uc["BUFFET"] == 0          # tier forced to 0 despite sku_cost 99
    assert uc["A0001"] == 10.0        # falls back to sku_cost
    assert uc["A0002"] in (0, None) or uc["A0002"] == 0   # uncosted → 0


# ---------------------------------------------------------------------------
# R6 — Cost-priority flip (2026-06-11): the Cost-Management table (sku_cost)
#      is the source of truth. A non-zero sku_cost.unit_cost BEATS the BoM
#      recipe; a 0/absent sku_cost falls through to the BoM. Regression for
#      B0040 (sku_cost 13.40 vs inflated BoM 38.14 — BoM used to win).
# ---------------------------------------------------------------------------
def test_sku_cost_beats_bom_and_zero_falls_through():
    con = fresh_db()
    # A0001: sku_cost 10.0 AND a BoM recipe summing 38.0 → sku_cost must win.
    con.execute("INSERT INTO bom_ingredients(sku_id,seq,ingredient_name,ingredient_cost)"
                " VALUES(2,1,'scallop',38.0)")
    # A0002: sku_cost 0 (means 'no data') AND a BoM recipe 7.5 → BoM must win.
    con.execute("INSERT INTO sku_cost(sku_id,unit_cost) VALUES(3, 0)")
    con.execute("INSERT INTO bom_ingredients(sku_id,seq,ingredient_name,ingredient_cost)"
                " VALUES(3,1,'lettuce',7.5)")
    con.commit()
    add_sales(con, 2, "2025-06", qty=5, ex_vat=500.0)
    add_sales(con, 3, "2025-06", qty=2, ex_vat=100.0)
    rows = con.execute(f"""
        SELECT sk.sku_code AS code, SUM({main._unified_cogs_expr('ssm')}) AS cogs
        FROM sales_sku_monthly ssm JOIN skus sk ON sk.sku_id = ssm.sku_id
        GROUP BY sk.sku_code
    """).fetchall()
    cogs = {r["code"]: r["cogs"] for r in rows}
    assert cogs["A0001"] == 5 * 10.0, "non-zero sku_cost must beat the BoM recipe"
    assert cogs["A0002"] == 2 * 7.5,  "sku_cost=0 must fall through to the BoM"
    # _build_sku_effective_cost must produce the SAME priority (it's the hot path).
    main._build_sku_effective_cost(con, force=True)
    eff = {r[0]: r[1] for r in con.execute(
        "SELECT sku_id, recipe_cost FROM sku_effective_cost")}
    assert eff[2] == 10.0 and eff[3] == 7.5
    # per-serving helper agrees too
    uc = {r["code"]: r["uc"] for r in con.execute(
        f"SELECT s.sku_code AS code, {main._bom_unit_cost_sql('s')} AS uc FROM skus s")}
    assert uc["A0001"] == 10.0 and uc["A0002"] == 7.5


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
