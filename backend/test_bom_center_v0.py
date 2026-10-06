"""BoM Calculation Center — Sprint S0 (docs/BOM_CENTER_PLAN.md §5.7, docs/ADR-001).

Three promises the foundation makes before any screen exists:
  1. a table named bc_* survives the daily DB swap, bcd_* is rebuilt instead;
  2. ensure() can run on any connection, any number of times;
  3. Release v0 reproduces today's engine SKU by SKU — zero, flat-wins-recipe,
     recipe, alias-to-lines, uncosted — so the later cut-over moves nothing.
"""
import sqlite3

import pytest

import conftest as _cf
import core
import main
from bom_center import schema as bcs
from bom_center import engine as bce
from bom_center import snapshot_v0 as v0

EXTRA_SKUS = [
    # sku_id, code, description, category, unit, business_type
    (4, "R0001", "(B) recipe only", 3, "plate", "buffet"),
    (5, "F0001", "(B) flat cost hides a recipe", 3, "plate", "buffet"),
    (6, "X0001", "(B) alias to R0001", 3, "plate", "buffet"),
    (7, "U0001", "(B) uncosted", 3, "plate", "buffet"),
    (8, "BUFFET799", "buffet package", 1, "pax", "buffet_tier"),
    (9, "Z0001", "(B) recipe with a zero line", 3, "plate", "buffet"),
]
BOM = [
    # sku_id, seq, code, name, qty, unit, price, cost, type
    (4, 1, "ING-A", "a", 10.0, "กรัม", 0.5, 5.0, "protein"),
    (4, 2, "ING-B", "b", 2.0, "กรัม", 1.25, 2.5, "veg"),
    (5, 1, "ING-A", "a", 100.0, "กรัม", 0.5, 50.0, "protein"),    # hidden by flat 12
    (9, 1, "ING-Z", "z", 1.0, "ชิ้น", 0.0, 0.0, "other"),           # zero line
    (9, 2, "ING-A", "a", 4.0, "กรัม", 0.5, 2.0, "protein"),
]
EXPECTED = {           # the legacy precedence, by hand
    "BUFFET": 0.0,     # fixture: sku_cost 99 on a package -> 0
    "B0001": 8.0, "A0001": 10.0,
    "R0001": 7.5, "F0001": 12.0, "X0001": 7.5, "U0001": 0.0, "BUFFET799": 0.0, "Z0001": 2.0,
}


@pytest.fixture
def db(tmp_path):
    """A private copy of the fixture DB with one SKU per precedence branch."""
    p = tmp_path / "bc.db"
    _cf._build_db(str(p))
    con = sqlite3.connect(str(p))
    con.executemany("INSERT INTO skus(sku_id,sku_code,description,category_id,unit,business_type,active)"
                    " VALUES(?,?,?,?,?,?,1)", EXTRA_SKUS)
    con.executemany("INSERT INTO bom_ingredients(sku_id, seq, ingredient_code, ingredient_name, qty_per_serving,"
                    " unit, price_per_unit, ingredient_cost, ingredient_type, source_file) VALUES(?,?,?,?,?,?,?,?,?,'t')",
                    BOM)
    con.executemany("INSERT INTO sku_cost(sku_id, unit_cost) VALUES(?,?)", [(5, 12.0), (8, 799.0)])
    con.execute("INSERT INTO bom_sku_alias(bom_sku_code, resolved_sku_id, match_method) VALUES('X0001', 4, 'test')")
    con.commit()
    yield con
    con.close()


def test_bc_prefix_is_preserved_and_bcd_is_not():
    assert "bc" in core._PRESERVED_PREFIXES
    pred = core._preserved_pred("name")
    con = sqlite3.connect(":memory:")
    names = ("bc_release", "bc_order_line", "bcd_cost_cache", "bcd_demand_day", "bcx", "bc", "recon_x", "bom_ingredients")
    union = " UNION ALL ".join(f"SELECT '{n}' AS name" for n in names)
    kept = {r[0] for r in con.execute(f"SELECT name FROM ({union}) WHERE {pred}")}
    assert kept == {"bc_release", "bc_order_line", "recon_x"}


def test_ensure_is_idempotent_and_complete(db):
    bcs.ensure(db)
    bcs.ensure(db)
    have = bcs.existing(db)
    assert set(bcs.TABLES) <= have
    assert all(t.startswith("bc_") for t in bcs.OWNED_TABLES)
    assert all(t.startswith("bcd_") for t in bcs.DERIVED_TABLES)


def test_v0_reproduces_the_legacy_engine(db):
    stats = v0.build_v0(db)
    assert stats["mismatches"] == [], stats
    assert stats["max_abs_diff"] <= v0.TOLERANCE
    cache = dict(db.execute("SELECT sku_code, unit_cost FROM bcd_cost_cache WHERE release_id=?",
                            (stats["release_id"],)))
    for code, want in EXPECTED.items():
        assert cache[code] == pytest.approx(want), code
    kinds = dict(db.execute("SELECT sku_code, kind FROM bc_recipe WHERE release_id=?", (stats["release_id"],)))
    assert kinds["BUFFET"] == "zero" and kinds["BUFFET799"] == "zero"
    assert kinds["F0001"] == "flat" and kinds["B0001"] == "flat"
    assert kinds["R0001"] == "recipe" and kinds["Z0001"] == "recipe"
    assert kinds["X0001"] == "alias"
    assert "U0001" not in kinds                      # uncosted: no row, cache 0
    assert stats["kinds"]["none"] == 1
    # the recipe a flat cost was hiding is kept for the analyst, with its exact cost
    f_lines = db.execute("SELECT seq, line_cost FROM bc_recipe_line WHERE release_id=? AND sku_code='F0001'",
                         (stats["release_id"],)).fetchall()
    assert f_lines == [(1, 50.0)]
    zero = db.execute("SELECT zero_by_design FROM bc_recipe_line WHERE release_id=? AND sku_code='Z0001' AND seq=1",
                      (stats["release_id"],)).fetchone()[0]
    assert zero == 1
    assert stats["cache_rows"] == db.execute("SELECT COUNT(*) FROM skus").fetchone()[0]


def test_v0_is_replaced_not_duplicated(db):
    a = v0.build_v0(db)
    b = v0.build_v0(db)
    assert a["release_id"] == b["release_id"]
    assert db.execute("SELECT COUNT(*) FROM bc_release").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM bc_recipe_line WHERE release_id=?", (b["release_id"],)).fetchone()[0] == b["lines"]
    assert a["lines"] == b["lines"] == len(BOM)


def test_release_in_force_prefers_the_newest_released(db):
    rid = v0.build_v0(db)["release_id"]
    assert bce.release_in_force(db, "2026-06") == rid
    db.execute("INSERT INTO bc_release(label, kind, effective_month, status) VALUES('2026-07 draft','monthly','2026-07','draft')")
    assert bce.release_in_force(db, "2026-07") == rid           # a draft never governs
    db.execute("INSERT INTO bc_release(label, kind, effective_month, status) VALUES('2026-07','monthly','2026-07','released')")
    m = db.execute("SELECT release_id FROM bc_release WHERE label='2026-07'").fetchone()[0]
    assert bce.release_in_force(db, "2026-07") == m
    assert bce.release_in_force(db, "2026-06") == rid           # history keeps its own version


def test_startup_creates_the_center_tables(client):
    con = sqlite3.connect(str(main.DB_PATH))
    try:
        assert set(bcs.TABLES) <= bcs.existing(con)
    finally:
        con.close()
