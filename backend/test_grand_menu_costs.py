"""Regression tests for the Grand Menu master-cost loader.

The Grand Menu (live 2026-09-01) is costed from an owner-maintained CSV rather
than a recipe. It is loaded at startup, not written once into a database,
because sync_mssql hot-swaps a fresh DB over the live one — a cost that lives
only in a database survives exactly until the next swap. These tests pin the
three behaviours that make that safe to run unattended on every boot.
"""
import sqlite3

import main


def _db():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE skus(sku_id INTEGER PRIMARY KEY, sku_code TEXT, description TEXT)")
    con.execute("CREATE TABLE sku_cost(sku_id INTEGER PRIMARY KEY, unit_cost REAL,"
                " effective_from TEXT, updated_at TEXT)")
    con.execute("CREATE TABLE sales_sku_monthly(sku_id INT, period_month TEXT,"
                " qty REAL, subtotal_ex_vat REAL)")
    return con


def _run(monkeypatch, tmp_path, con, csv_text):
    """Point the loader at an in-memory DB and a throwaway CSV."""
    p = tmp_path / "master_item_costs.csv"
    p.write_text(csv_text, encoding="utf-8")

    class _Ctx:
        def __enter__(self): return con
        def __exit__(self, *a): return False

    monkeypatch.setattr(main, "get_db", lambda *a, **k: _Ctx())
    monkeypatch.setattr(main, "invalidate_cache", lambda *a, **k: None)
    return main._load_grand_menu_costs(p)


HEADER = "sku_code,tier,name_th,cost_per_serve\n"


def test_applies_costs_for_known_skus(monkeypatch, tmp_path):
    con = _db()
    con.execute("INSERT INTO skus VALUES(1, 'OTHBFYAM00011', '(BF) ยำไทยปูอัด')")
    con.execute("INSERT INTO skus VALUES(2, 'OTHALSKS00002', '(AL) หัวปลาแซลมอน')")
    out = _run(monkeypatch, tmp_path, con,
               HEADER + "OTHBFYAM00011,459,x,10.5745\nOTHALSKS00002,,y,18.13\n")
    assert out["applied"] == 2
    got = dict(con.execute("SELECT sku_id, unit_cost FROM sku_cost").fetchall())
    assert round(got[1], 4) == 10.5745
    assert round(got[2], 2) == 18.13
    # dated to the day the Grand Menu went live, not to whenever the file was loaded
    assert con.execute("SELECT effective_from FROM sku_cost WHERE sku_id=1").fetchone()[0] == "2026-09-01"


def test_rejects_a_selling_price_pasted_into_the_cost_column(monkeypatch, tmp_path):
    """A price entered as a cost has reached this project three separate times.
    Where the SKU has sold, the ratio gives it away and the row must be refused
    rather than booking a dish at its own menu price."""
    con = _db()
    con.execute("INSERT INTO skus VALUES(1, 'OTHALSUS00040', '(AL) ซูชิฮามาจิ')")
    con.execute("INSERT INTO skus VALUES(2, 'OTHALSUS00033', '(AL) ซูชิกุ้ง')")
    # sold 10 for ฿1,000 ex-VAT → ฿100 each; ฿107 is that price with VAT on top
    con.execute("INSERT INTO sales_sku_monthly VALUES(1, '2026-09', 10, 1000.0)")
    con.execute("INSERT INTO sales_sku_monthly VALUES(2, '2026-09', 10, 1000.0)")
    out = _run(monkeypatch, tmp_path, con,
               HEADER + "OTHALSUS00040,,x,107.0\nOTHALSUS00033,,y,21.5\n")
    assert out["rejected_price_as_cost"] == 1
    assert out["applied"] == 1
    rows = con.execute("SELECT sku_id FROM sku_cost").fetchall()
    assert [r[0] for r in rows] == [2], "the price-shaped row must not be written"


def test_unknown_skus_and_junk_values_are_counted_not_fatal(monkeypatch, tmp_path):
    con = _db()
    con.execute("INSERT INTO skus VALUES(1, 'OTHBFYAM00011', '(BF) ยำไทยปูอัด')")
    out = _run(monkeypatch, tmp_path, con,
               HEADER
               + "OTHBFYAM00011,459,x,10.57\n"
               + "OTHBFNOPE00001,459,not in skus,5.0\n"
               + "OTHBFYAM00012,459,blank cost,\n"
               + "OTHBFYAM00013,459,zero cost,0\n")
    assert out == {"applied": 1, "unknown_sku": 1,
                   "rejected_price_as_cost": 0, "bad_value": 2}


def test_missing_file_is_a_no_op(monkeypatch, tmp_path):
    con = _db()
    monkeypatch.setattr(main, "invalidate_cache", lambda *a, **k: None)
    out = main._load_grand_menu_costs(tmp_path / "nope.csv")
    assert out["applied"] == 0


def test_shipped_file_is_well_formed():
    """The CSV is deployed with the code and read on every boot, so a typo in it
    is a production issue, not a data issue."""
    import csv
    path = main.GRAND_MENU_COST_FILE
    assert path.exists(), f"missing {path}"
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 384
    codes = [r["sku_code"] for r in rows]
    assert len(set(codes)) == len(codes), "duplicate sku_code"
    assert all(c.startswith(("OTHBF", "OTHAL")) for c in codes)
    costs = [float(r["cost_per_serve"]) for r in rows]
    assert all(c > 0 for c in costs)
    # cheapest buffet tier is ฿459 — a cost-per-serve near that is a pasted price
    assert max(costs) < 300, "a cost-per-serve this high looks like a price"


def test_patches_outrank_the_master_file():
    """_apply_cost_patches runs after the loader, so any SKU in both must end up
    at the patched value. Today they are disjoint; this fails loudly if a future
    edit puts a hand-verified SKU into the sheet without re-checking the order."""
    import csv
    with open(main.GRAND_MENU_COST_FILE, encoding="utf-8-sig", newline="") as fh:
        sheet = {r["sku_code"]: float(r["cost_per_serve"]) for r in csv.DictReader(fh)}
    src = (main.HERE / "main.py").read_text(encoding="utf-8")
    startup = src.split("def _bom_startup()", 1)[1].split("_sku_cost_monthly_ensure", 1)[0]
    # sheet, then aliases (whose fallback reads the effective cost the sheet just
    # fed), then the hand-verified patches, which must have the last word.
    assert (startup.index("_load_grand_menu_costs()")
            < startup.index("_load_grand_menu_aliases()")
            < startup.index("_apply_cost_patches()"))
    for code, cost in main._COST_PATCHES:
        if code in sheet:
            assert abs(sheet[code] - cost) < 0.01, (
                f"{code} is in both the master sheet ({sheet[code]}) and _COST_PATCHES "
                f"({cost}); the patch wins at startup — reconcile them")


# ─── alias loader ────────────────────────────────────────────────────────────
# Several Grand Menu codes are a renamed dish whose recipe still lives under the
# old code. bom_sku_alias resolves in ONE hop and reads only the donor's
# bom_ingredients — never the donor's own alias, never its sku_cost — so a
# plausible-looking mapping can silently cost a dish at ฿0. These tests pin the
# three cases that actually occur in the shipped file.

def _alias_db():
    con = _db()
    con.execute("CREATE TABLE bom_ingredients(sku_id INT, ingredient_cost REAL)")
    con.execute("CREATE TABLE bom_sku_alias(bom_sku_code TEXT PRIMARY KEY,"
                " resolved_sku_id INT, match_method TEXT, confidence REAL, notes TEXT)")
    con.execute("CREATE TABLE sku_effective_cost(sku_id INTEGER PRIMARY KEY, recipe_cost REAL)")
    return con


def _run_alias(monkeypatch, tmp_path, con, csv_text):
    p = tmp_path / "aliases.csv"
    p.write_text(csv_text, encoding="utf-8")

    class _Ctx:
        def __enter__(self): return con
        def __exit__(self, *a): return False

    monkeypatch.setattr(main, "get_db", lambda *a, **k: _Ctx())
    monkeypatch.setattr(main, "invalidate_cache", lambda *a, **k: None)
    return main._load_grand_menu_aliases(p)


AHEAD = "sku_code,donor_sku_code,note\n"


def test_donor_with_a_recipe_is_aliased(monkeypatch, tmp_path):
    con = _alias_db()
    con.execute("INSERT INTO skus VALUES(1, 'OTHBFSPC00110', '(BF) ปีกไก่ทอด')")
    con.execute("INSERT INTO skus VALUES(2, 'B0119', '(B) ปีกไก่ทอด')")
    con.execute("INSERT INTO bom_ingredients VALUES(2, 12.48)")
    out = _run_alias(monkeypatch, tmp_path, con, AHEAD + "OTHBFSPC00110,B0119,x\n")
    assert out["aliased"] == 1 and out["cost_copied"] == 0
    row = con.execute("SELECT resolved_sku_id, match_method FROM bom_sku_alias"
                      " WHERE bom_sku_code='OTHBFSPC00110'").fetchone()
    assert row == (2, "grand-menu-2026-09")
    # and no flat cost was invented alongside it
    assert con.execute("SELECT COUNT(*) FROM sku_cost").fetchone()[0] == 0


def test_recipeless_donor_copies_the_cost_instead_of_aliasing_to_zero(monkeypatch, tmp_path):
    """UBN002 carries a flat sku_cost and no recipe. An alias to it resolves to
    SUM(bom_ingredients) = NULL, i.e. ฿0 — so the loader copies the cost."""
    con = _alias_db()
    con.execute("INSERT INTO skus VALUES(1, 'OTHBFSPC00045', '(BF) เนื้อวากิวย่างใบโฮบะ')")
    con.execute("INSERT INTO skus VALUES(2, 'UBN002', '(A) เนื้อวากิวย่างใบโฮบะ')")
    con.execute("INSERT INTO sku_effective_cost VALUES(2, 409.25)")
    out = _run_alias(monkeypatch, tmp_path, con, AHEAD + "OTHBFSPC00045,UBN002,x\n")
    assert out["cost_copied"] == 1 and out["aliased"] == 0
    assert con.execute("SELECT unit_cost FROM sku_cost WHERE sku_id=1").fetchone()[0] == 409.25
    assert con.execute("SELECT COUNT(*) FROM bom_sku_alias").fetchone()[0] == 0, \
        "an alias to a recipe-less donor would book the dish at ฿0"


def test_donor_with_neither_recipe_nor_cost_is_reported_not_written(monkeypatch, tmp_path):
    con = _alias_db()
    con.execute("INSERT INTO skus VALUES(1, 'OTHBFSPC00099', 'new')")
    con.execute("INSERT INTO skus VALUES(2, 'EMPTY001', 'donor with nothing')")
    out = _run_alias(monkeypatch, tmp_path, con, AHEAD + "OTHBFSPC00099,EMPTY001,x\n")
    assert out["donor_has_nothing"] == 1
    assert con.execute("SELECT COUNT(*) FROM bom_sku_alias").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM sku_cost").fetchone()[0] == 0


def test_missing_codes_are_counted_separately(monkeypatch, tmp_path):
    con = _alias_db()
    con.execute("INSERT INTO skus VALUES(1, 'OTHBFSPC00110', 'new')")
    out = _run_alias(monkeypatch, tmp_path, con,
                     AHEAD + "OTHBFSPC00110,NOPE001,donor missing\n"
                           + "OTHBFNOTHERE,B0119,sku missing\n")
    assert out["unknown_donor"] == 1 and out["unknown_sku"] == 1


def test_shipped_alias_file_never_points_at_an_aliased_donor():
    """The resolver does not chain. Two entries in this file exist only because
    the obvious donor (THBFSOU000xx) is itself an alias — if a future edit points
    at a donor that is aliased rather than at a recipe, the dish silently costs
    ฿0. This test refuses that shape."""
    import csv
    path = main.GRAND_MENU_ALIAS_FILE
    assert path.exists(), f"missing {path}"
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows, "alias file is empty"
    codes = [r["sku_code"] for r in rows]
    assert len(set(codes)) == len(codes), "a SKU is mapped twice"
    for r in rows:
        assert r["sku_code"] != r["donor_sku_code"], f"{r['sku_code']} points at itself"
    mapped = set(codes)
    for r in rows:
        assert r["donor_sku_code"] not in mapped, (
            f"{r['sku_code']} points at {r['donor_sku_code']}, which this same file "
            f"aliases — bom_sku_alias resolves in one hop, so this would cost ฿0")
