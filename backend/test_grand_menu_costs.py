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
    startup = src.split("def _bom_startup()", 1)[1][:400]
    assert startup.index("_load_grand_menu_costs()") < startup.index("_apply_cost_patches()")
    for code, cost in main._COST_PATCHES:
        if code in sheet:
            assert abs(sheet[code] - cost) < 0.01, (
                f"{code} is in both the master sheet ({sheet[code]}) and _COST_PATCHES "
                f"({cost}); the patch wins at startup — reconcile them")
