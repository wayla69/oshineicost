"""Cost coverage has ONE definition (rules.needs_cost_sql / costed_sql), 2026-10-01.

Before this the home page, the P&L and the coverage tab each had their own rule
and disagreed (99.1% / 86.3% / 97.4% for the same September): the P&L ignored
estimates and counted buffet packages as gaps, the coverage tab counted a ฿0
sku_cost row as costed and ignored recipes. Also pins that a cost typed through
POST /api/cost survives the daily swap (it used to last until the next sync).
"""
import sqlite3
from pathlib import Path

import rules
from core import _bom_costed_rev_expr, _costed_rev_case

HERE = Path(__file__).parent


def _db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript((HERE / "schema.sql").read_text(encoding="utf-8"))
    con.executescript("""
      CREATE TABLE IF NOT EXISTS bom_ingredients (sku_id INTEGER, seq INTEGER, ingredient_code TEXT,
        ingredient_name TEXT, qty_per_serving REAL, unit TEXT, price_per_unit REAL,
        ingredient_cost REAL, ingredient_type TEXT, source_file TEXT);
      CREATE TABLE IF NOT EXISTS bom_sku_alias (bom_sku_code TEXT, resolved_sku_id INTEGER);
    """)
    con.execute("INSERT INTO branches(branch_id,name) VALUES(1,'T')")
    con.execute("INSERT INTO categories(category_id,name) VALUES(1,'X')")
    skus = [
        (1, "BUFFET"),          # package: never a gap
        (2, "R1"),              # costed by its own recipe
        (3, "C1"),              # costed by the cost table
        (4, "Z1"),              # a ฿0 cost-table row: NOT costed
        (5, "N1"),              # nothing at all: a gap
        (6, "OTHOTOTH00012"),   # penalty fee: never a gap
        (7, "OTHBFAPP00015"),   # steak sauce, costed inside the dishes: never a gap
        (8, "AL1"),             # costed through an alias to R1's recipe
        (9, "SERVICECHARGE"),
    ]
    con.executemany("INSERT INTO skus(sku_id,sku_code,description,category_id,unit) VALUES(?,?,?,1,'x')",
                    [(i, c, c) for i, c in skus])
    con.execute("INSERT INTO bom_ingredients VALUES(2,1,'I','fish',100,'g',0.5,50,'protein','f')")
    con.execute("INSERT INTO bom_sku_alias VALUES('AL1', 2)")
    con.executemany("INSERT INTO sku_cost(sku_id,unit_cost) VALUES(?,?)", [(1, 600.0), (3, 12.0), (4, 0.0)])
    for sid, qty, rev in [(1, 10, 6000), (2, 20, 0), (3, 5, 500), (4, 5, 250), (5, 5, 250),
                          (6, 3, 300), (7, 40, 0), (8, 2, 400), (9, 1, 100)]:
        con.execute("INSERT INTO sales_sku_monthly(branch_id,period_month,sku_id,qty,subtotal_ex_vat,"
                    "tax_total,total_inc_vat,source) VALUES(1,'2026-09',?,?,?,0,?,'Easy')", (sid, qty, rev, rev))
    return con


def test_one_definition_counts_what_should_carry_a_cost():
    con = _db()
    r = con.execute(f"""SELECT {rules.coverage_sums_sql('ssm')}
                          FROM sales_sku_monthly ssm JOIN skus sk ON sk.sku_id = ssm.sku_id""").fetchone()
    # in scope: R1 20 + C1 5 + Z1 5 + N1 5 + AL1 2 = 37 plates; costed: R1, C1, AL1 = 27
    assert r["cov_plates"] == 37 and r["cov_plates_costed"] == 27
    # revenue in scope: 0 + 500 + 250 + 250 + 400 = 1400; costed 900
    assert r["cov_rev"] == 1400 and r["cov_rev_costed"] == 900
    cov = rules.coverage_from_sums(r)
    assert cov["plates_pct"] == 73.0 and cov["uncosted_plates"] == 10
    assert cov["revenue_pct"] == 64.3 and cov["uncosted_revenue"] == 500


def test_costed_revenue_helpers_agree_with_cogs():
    """Both 'costed revenue' helpers count what COGS costs: recipes and aliases
    yes, a ฿0 cost-table row no (it used to count, and recipes did not)."""
    con = _db()
    got = {r["c"]: (r["a"], r["b"]) for r in con.execute(f"""
        SELECT sk.sku_code c, {_costed_rev_case('ssm', 'sk', 'sc')} a, {_bom_costed_rev_expr('ssm')} b
          FROM sales_sku_monthly ssm JOIN skus sk ON sk.sku_id = ssm.sku_id
          LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id""")}
    assert got["BUFFET"][0] == 6000            # package revenue: costed through the dishes
    assert got["C1"] == (500, 500)
    assert got["AL1"] == (400, 400)            # alias recipe counts
    assert got["Z1"] == (0, 0)                 # a ฿0 row is not a cost
    assert got["N1"] == (0, 0)


def test_cost_posted_in_the_app_survives_the_swap(client, app_db):
    """POST /api/cost (cost upload, Settings, the coverage fixer) also stores a
    cost_override intent; the swap brings the base DB's sku_cost back, and
    apply_overrides restores the typed number."""
    import cost_sources as cs

    def unit_cost(code):
        return {r["sku_code"]: r["unit_cost"] for r in client.get("/api/cost").json()}.get(code)

    before = unit_cost("B0001")
    try:
        assert client.post("/api/cost", json=[{"sku_code": "B0001", "unit_cost": 9.25}]).status_code == 200
        con = sqlite3.connect(str(app_db))
        assert con.execute("SELECT kind, unit_cost FROM cost_override WHERE sku_code='B0001'").fetchone() == ("cost", 9.25)
        # the swap: the base DB's value comes back
        con.execute("UPDATE sku_cost SET unit_cost=? WHERE sku_id=(SELECT sku_id FROM skus WHERE sku_code='B0001')",
                    (before,))
        con.commit()
        cs.apply_overrides(con)
        assert con.execute("SELECT unit_cost FROM sku_cost WHERE sku_id=(SELECT sku_id FROM skus"
                           " WHERE sku_code='B0001')").fetchone()[0] == 9.25
        # a 0 withdraws the intent
        client.post("/api/cost", json=[{"sku_code": "B0001", "unit_cost": 0}])
        assert con.execute("SELECT 1 FROM cost_override WHERE sku_code='B0001'").fetchone() is None
        con.close()
    finally:
        client.post("/api/cost", json=[{"sku_code": "B0001", "unit_cost": before}])
        con = sqlite3.connect(str(app_db))
        con.execute("DELETE FROM cost_override WHERE sku_code='B0001'")
        con.execute("DELETE FROM cost_provenance WHERE sku_id=(SELECT sku_id FROM skus WHERE sku_code='B0001')")
        con.commit()
        con.close()
