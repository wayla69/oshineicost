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


NL = chr(10)
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


def test_tier_column_is_recorded_for_buffet_skus(monkeypatch, tmp_path):
    """Each buffet dish's price tier lands in sku_tier so /api/reports/price-tier
    can average recipe cost by tier — see _pt_bom_cost_by_tier in routers/reports.py."""
    con = _db()
    con.execute("CREATE TABLE sku_tier(sku_id INTEGER PRIMARY KEY, tier TEXT NOT NULL)")
    con.execute("INSERT INTO skus VALUES(1, 'OTHBFYAM00011', '(BF) ยำไทยปูอัด')")
    con.execute("INSERT INTO skus VALUES(2, 'OTHALSKS00002', '(AL) หัวปลาแซลมอน')")
    _run(monkeypatch, tmp_path, con,
         HEADER + "OTHBFYAM00011,459,x,10.5745\nOTHALSKS00002,None,y,18.13\n")
    # buffet dish gets its tier; à la carte's literal "None" text must NOT
    # become a fake tier value (Master Item Grand Menu.xlsx quirk)
    assert dict(con.execute("SELECT sku_id, tier FROM sku_tier").fetchall()) == {1: "459"}


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
    # The order now lives in ONE place — cost_sources.apply_all — which both the
    # app's startup and build_menu_fact call. Sheet, then file recipes (a donor
    # may live there), then aliases (whose fallback reads the effective cost the
    # sheet just fed), then the hand-verified patches, then the ฿0-only estimates.
    src = (main.HERE / "cost_sources.py").read_text(encoding="utf-8")
    body = src.split("def apply_all(", 1)[1]
    assert (body.index("load_grand_menu_costs(con)")
            < body.index("load_recipe_file(con)")
            < body.index("load_grand_menu_aliases(con)")
            < body.index("apply_cost_patches(con)")
            < body.index("load_estimated_costs(con)"))
    # …and neither caller may go back to applying sources on its own
    for caller in ("main.py", "build_menu_fact.py"):
        assert "apply_all" in (main.HERE / caller).read_text(encoding="utf-8"), caller
    assert "estimated_costs_2026-09.csv" not in (
        main.HERE / "build_menu_fact.py").read_text(encoding="utf-8"),         "build_menu_fact must not re-implement a cost source — use cost_sources.apply_all"
    for code, cost in main._COST_PATCHES:
        if code in sheet:
            assert abs(sheet[code] - cost) < 0.01, (
                f"{code} is in both the master sheet ({sheet[code]}) and _COST_PATCHES "
                f"({cost}); the patch wins at startup — reconcile them")



def test_apply_all_gives_the_app_and_the_fact_builder_one_answer(tmp_path):
    """The whole point of cost_sources: run it on ANY database — live or the
    sync's staging copy — and the same dish gets the same cost, by the same
    precedence. Patch > owner sheet; recipe and alias fill what has no cost;
    an estimate only ever fills ฿0."""
    import cost_sources, core
    db = tmp_path / "t.db"
    import conftest as _cf
    _cf._build_db(str(db))
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE IF NOT EXISTS bom_sku_alias(bom_sku_code TEXT PRIMARY KEY,"
                " resolved_sku_id INT, match_method TEXT, confidence REAL, notes TEXT)")
    mk = lambda i, code: con.execute(
        "INSERT INTO skus(sku_id, sku_code, description) VALUES(?,?,?)", (i, code, code))
    mk(9001, "KKN018")            # in _COST_PATCHES at 68.00
    mk(9002, "OTHBFSPC00007")     # กิมจิ: recipe file only  -> 4.41
    mk(9003, "OTHBFBVR00001")     # Pepsi: estimate only      -> 13.59
    mk(9004, "OTHBFSUS00003")     # owner sheet               -> 7.5
    con.execute("INSERT INTO sku_cost(sku_id, unit_cost) VALUES(9001, 20.0)")   # stale ฿20
    con.commit()
    cost_sources.apply_all(con)
    got = dict(con.execute("SELECT s.sku_code, e.recipe_cost FROM sku_effective_cost e "
                           "JOIN skus s USING(sku_id) WHERE s.sku_id >= 9001"))
    assert got["KKN018"] == 68.0                       # the patch re-asserts itself
    assert round(got["OTHBFSPC00007"], 2) == 4.41      # recipe from the file
    assert got["OTHBFBVR00001"] == 13.59               # estimate fills ฿0
    assert got["OTHBFSUS00003"] == 7.5                 # the owner's sheet
    # idempotent: a second run (the builder after the app, or vice versa) changes nothing
    cost_sources.apply_all(con)
    again = dict(con.execute("SELECT s.sku_code, e.recipe_cost FROM sku_effective_cost e "
                             "JOIN skus s USING(sku_id) WHERE s.sku_id >= 9001"))
    assert again == got

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


def test_a_few_promo_sales_do_not_veto_the_owners_cost(monkeypatch, tmp_path):
    """OTHALHAR00006 sold 3 discounted sets and its ฿53 cost was refused as a
    price. The guard must wait for a real sample."""
    con = _db()
    con.execute("INSERT INTO skus VALUES(1, 'OTHALHAR00006', '(AL) แฮนด์โรล')")
    con.execute("INSERT INTO sales_sku_monthly VALUES(1, '2026-09', 3, 150.0)")   # ฿50 each, 3 sold
    out = _run(monkeypatch, tmp_path, con, HEADER + "OTHALHAR00006,,x,53.25\n")
    assert out["applied"] == 1 and out["rejected_price_as_cost"] == 0




# ─── recipes that ship as a file (_load_recipe_file) ───────────────────────────

REC_HEADER = ("sku_code,seq,ingredient_code,ingredient_name,qty_per_serving,unit,"
              "price_per_unit,ingredient_type,note" + NL)


def _run_rec(monkeypatch, tmp_path, con, csv_text):
    p = tmp_path / "recipes.csv"
    p.write_text(csv_text, encoding="utf-8")

    class _Ctx:
        def __enter__(self): return con
        def __exit__(self, *a): return False

    monkeypatch.setattr(main, "get_db", lambda *a, **k: _Ctx())
    monkeypatch.setattr(main, "invalidate_cache", lambda *a, **k: None)
    return main._load_recipe_file(p)


def _rec_db():
    con = _db()
    con.execute("""CREATE TABLE bom_ingredients(sku_id INT, seq INT, ingredient_code TEXT,
                   ingredient_name TEXT, qty_per_serving REAL, unit TEXT, price_per_unit REAL,
                   ingredient_cost REAL, ingredient_type TEXT, source_file TEXT,
                   PRIMARY KEY(sku_id, seq))""")
    return con


def test_recipe_file_seeds_a_dish_with_no_recipe(monkeypatch, tmp_path):
    """The owner's BoM workbooks are gitignored, so a dish they never covered can
    only get a recipe from a file that ships with the code."""
    con = _rec_db()
    con.execute("INSERT INTO skus VALUES(1, 'OTHBFSPC00007', '(BF) กิมจิ')")
    out = _run_rec(monkeypatch, tmp_path, con, REC_HEADER
                   + "OTHBFSPC00007,1,V-015,ผักกาดขาว,60,กรัม,0.0427,veg," + NL
                   + "OTHBFSPC00007,2,13-04-304,ซอสหมักกิมจิ,15,มิลลิลิตร,0.1232,sauce," + NL)
    assert out["skus"] == 1 and out["rows"] == 2
    rows = con.execute("SELECT seq, ingredient_cost FROM bom_ingredients ORDER BY seq").fetchall()
    assert [r[0] for r in rows] == [1, 2]
    assert round(sum(r[1] for r in rows), 2) == 4.41


def test_recipe_file_never_touches_a_dish_that_is_already_costed(monkeypatch, tmp_path):
    """An existing recipe or the owner's own cost both outrank the file."""
    con = _rec_db()
    con.execute("INSERT INTO skus VALUES(1, 'HASBOM', 'x')")
    con.execute("INSERT INTO skus VALUES(2, 'HASCOST', 'y')")
    con.execute("INSERT INTO bom_ingredients VALUES(1,1,'A','a',1,'ก',2.0,2.0,'veg','wb.xlsx')")
    con.execute("INSERT INTO sku_cost VALUES(2, 9.9, '2026-09-01', 'x')")
    out = _run_rec(monkeypatch, tmp_path, con, REC_HEADER
                   + "HASBOM,1,Z,z,99,กรัม,9.0,veg," + NL
                   + "HASCOST,1,Z,z,99,กรัม,9.0,veg," + NL)
    assert out["skus"] == 0 and out["already_costed"] == 2
    assert con.execute("SELECT COUNT(*) FROM bom_ingredients").fetchone()[0] == 1
    assert con.execute("SELECT ingredient_cost FROM bom_ingredients").fetchone()[0] == 2.0


def test_the_shipped_recipe_file_is_well_formed():
    import csv
    rows = list(csv.DictReader(open(main.RECIPE_FILE, encoding="utf-8-sig")))
    assert len(rows) >= 30
    seen = set()
    for r in rows:
        # mostly Grand Menu OTH* codes, but older delivery codes (DEL*, TA*, P-TW*)
        # still sell and carry weight-based platters (2026-10-01)
        assert r["sku_code"] and r["sku_code"] == r["sku_code"].strip() and " " not in r["sku_code"], r
        assert float(r["qty_per_serving"]) > 0 and float(r["price_per_unit"]) > 0, r
        assert r["ingredient_code"] and r["ingredient_name"], r
        key = (r["sku_code"], int(r["seq"]))
        assert key not in seen, f"duplicate seq: {key}"      # PK collision would drop a line
        seen.add(key)

# ─── estimated costs for dishes with no recipe at all (_load_estimated_costs) ──

EST_HEADER = "sku_code,unit_cost,basis,note\n"


def _run_est(monkeypatch, tmp_path, con, csv_text):
    p = tmp_path / "estimated_costs.csv"
    p.write_text(csv_text, encoding="utf-8")

    class _Ctx:
        def __enter__(self): return con
        def __exit__(self, *a): return False

    monkeypatch.setattr(main, "get_db", lambda *a, **k: _Ctx())
    monkeypatch.setattr(main, "invalidate_cache", lambda *a, **k: None)
    return main._load_estimated_costs(p)


def _est_db():
    con = _db()
    con.execute("CREATE TABLE bom_ingredients(sku_id INT, ingredient_cost REAL)")
    return con


def test_estimate_fills_a_dish_that_has_no_cost_at_all(monkeypatch, tmp_path):
    """Drinks and dipping sauces book ฿0 because nobody wrote them a recipe, and
    a ฿0 plate flatters every food-cost figure it lands in."""
    con = _est_db()
    con.execute("INSERT INTO skus VALUES(1, 'OTHBFBVR00001', '(BF) Pepsi')")
    out = _run_est(monkeypatch, tmp_path, con, EST_HEADER + "OTHBFBVR00001,13.59,requisition,x\n")
    assert out["applied"] == 1
    assert con.execute("SELECT unit_cost FROM sku_cost WHERE sku_id=1").fetchone()[0] == 13.59


def test_estimate_never_overwrites_a_real_cost(monkeypatch, tmp_path):
    """The weakest source in the chain: an owner's number and a recipe both
    outrank it, so a bad estimate can never quietly re-cost a costed dish."""
    con = _est_db()
    con.execute("INSERT INTO skus VALUES(1, 'A', 'owner-costed')")
    con.execute("INSERT INTO skus VALUES(2, 'B', 'recipe-costed')")
    con.execute("INSERT INTO sku_cost VALUES(1, 7.5, '2026-09-01', 'x')")
    con.execute("INSERT INTO bom_ingredients VALUES(2, 4.25)")
    out = _run_est(monkeypatch, tmp_path, con, EST_HEADER + "A,99.0,requisition,x\nB,99.0,requisition,y\n")
    assert out["applied"] == 0 and out["already_costed"] == 2
    assert con.execute("SELECT unit_cost FROM sku_cost WHERE sku_id=1").fetchone()[0] == 7.5
    assert con.execute("SELECT COUNT(*) FROM sku_cost WHERE sku_id=2").fetchone()[0] == 0


def test_estimate_skips_unknown_codes_and_junk_values(monkeypatch, tmp_path):
    con = _est_db()
    con.execute("INSERT INTO skus VALUES(1, 'REAL', 'x')")
    out = _run_est(monkeypatch, tmp_path, con,
                   EST_HEADER + "GHOST,5.0,requisition,x\nREAL,0,requisition,y\nREAL,-1,requisition,z\n")
    assert out["unknown_sku"] == 1 and out["bad_value"] == 2 and out["applied"] == 0


def test_a_corrected_estimate_replaces_our_own_earlier_one(monkeypatch, tmp_path):
    """The 5-litre-gallon fix moved ซอสเทอริยากิ from 6.90 to 5.21. "Skip anything
    already costed" would have frozen 6.90 forever on any DB that had seen it —
    the loader could not tell its own estimate from the owner's number."""
    con = _est_db()
    con.execute("INSERT INTO skus VALUES(1, 'OTHBFAPP00016', 'teriyaki')")
    _run_est(monkeypatch, tmp_path, con, EST_HEADER + "OTHBFAPP00016,6.90,unit-price,x" + NL)
    out = _run_est(monkeypatch, tmp_path, con, EST_HEADER + "OTHBFAPP00016,5.21,unit-price,y" + NL)
    assert out["updated"] == 1
    assert con.execute("SELECT unit_cost FROM sku_cost WHERE sku_id=1").fetchone()[0] == 5.21


def test_an_estimate_the_owner_overrode_is_never_ours_again(monkeypatch, tmp_path):
    con = _est_db()
    con.execute("INSERT INTO skus VALUES(1, 'X', 'x')")
    _run_est(monkeypatch, tmp_path, con, EST_HEADER + "X,6.90,unit-price,x" + NL)
    con.execute("UPDATE sku_cost SET unit_cost=9.99 WHERE sku_id=1")       # the owner speaks
    out = _run_est(monkeypatch, tmp_path, con, EST_HEADER + "X,5.21,unit-price,y" + NL)
    assert out["already_costed"] == 1 and out["updated"] == 0
    assert con.execute("SELECT unit_cost FROM sku_cost WHERE sku_id=1").fetchone()[0] == 9.99
    assert con.execute("SELECT COUNT(*) FROM cost_provenance").fetchone()[0] == 0


def test_an_untagged_estimate_from_before_provenance_is_adopted(monkeypatch, tmp_path):
    """Databases from 2026-09-17 hold estimates with no tag. If the value is
    exactly the file's, it is ours — adopt it so it can still be corrected."""
    con = _est_db()
    con.execute("INSERT INTO skus VALUES(1, 'X', 'x')")
    con.execute("INSERT INTO sku_cost VALUES(1, 6.90, '2026-09-01', 'x')")   # untagged
    assert _run_est(monkeypatch, tmp_path, con, EST_HEADER + "X,6.90,unit-price,x" + NL)["unchanged"] == 1
    assert _run_est(monkeypatch, tmp_path, con, EST_HEADER + "X,5.21,unit-price,y" + NL)["updated"] == 1


def test_a_recipe_file_edit_replaces_our_rows_but_never_a_real_recipe(monkeypatch, tmp_path):
    con = _rec_db()
    con.execute("INSERT INTO skus VALUES(1, 'OURS', 'x')")
    con.execute("INSERT INTO skus VALUES(2, 'THEIRS', 'y')")
    con.execute("INSERT INTO bom_ingredients VALUES(2,1,'A','a',1,'ก',2.0,2.0,'veg','kitchen.xlsx')")
    first = REC_HEADER + "OURS,1,V-015,ผักกาดขาว,60,กรัม,0.05,veg," + NL + "THEIRS,1,Z,z,9,กรัม,9,veg," + NL
    assert _run_rec(monkeypatch, tmp_path, con, first)["skus"] == 1
    again = REC_HEADER + "OURS,1,V-015,ผักกาดขาว,80,กรัม,0.05,veg," + NL + "THEIRS,1,Z,z,9,กรัม,9,veg," + NL
    out = _run_rec(monkeypatch, tmp_path, con, again)
    assert out["updated"] == 1 and out["already_costed"] == 1
    assert con.execute("SELECT qty_per_serving FROM bom_ingredients WHERE sku_id=1").fetchone()[0] == 80
    assert con.execute("SELECT ingredient_cost FROM bom_ingredients WHERE sku_id=2").fetchone()[0] == 2.0
    assert _run_rec(monkeypatch, tmp_path, con, again)["unchanged"] == 1      # and then it is stable


def test_the_shipped_estimate_file_parses_and_is_plausible():
    """The file ships in the image; a typo in it would silently mis-cost a dish."""
    import csv
    rows = list(csv.DictReader(open(main.ESTIMATED_COST_FILE, encoding="utf-8-sig")))
    assert len(rows) >= 20
    seen = set()
    for r in rows:
        # OTH* is the Grand Menu series; a handful of pre-Sep codes are still
        # being sold and are costed here too (B0300, the old twin-pot soup).
        assert r["sku_code"] and r["sku_code"] not in seen, r
        seen.add(r["sku_code"])
        assert 0 < float(r["unit_cost"]) < 200, r        # a dish, never a whole bill
        assert r["basis"] in ("requisition", "unit-price", "peer"), r
        assert r["note"].strip(), r

# ─── BoM cost averaged by price tier (routers/reports.py) ───────────────────

def _tier_db(rows):
    """(sku_id, tier, cost) rows → the three tables the tier helpers read."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE skus(sku_id INTEGER PRIMARY KEY, sku_code TEXT, description TEXT)")
    con.execute("CREATE TABLE sku_tier(sku_id INTEGER PRIMARY KEY, tier TEXT NOT NULL)")
    con.execute("CREATE TABLE sku_effective_cost(sku_id INTEGER PRIMARY KEY, recipe_cost REAL)")
    for sku_id, tier, cost in rows:
        con.execute("INSERT INTO skus VALUES(?,?,?)", (sku_id, f"OTHBF{sku_id:05d}", f"dish {sku_id}"))
        con.execute("INSERT INTO sku_tier VALUES(?,?)", (sku_id, tier))
        con.execute("INSERT INTO sku_effective_cost VALUES(?,?)", (sku_id, cost))
    return con


def test_bom_cost_by_tier_averages_recipe_cost_cumulatively():
    """_pt_bom_cost_by_tier is a static (non-date-scoped) average of recipe cost
    over every dish a diner on that tier may eat — the tier's own dishes PLUS
    every cheaper tier's, because the buffet is inclusive upward."""
    from routers.reports import _pt_bom_cost_by_tier
    con = _tier_db([(1, "459", 10.0), (2, "459", 12.0), (3, "629", 20.0)])
    out = _pt_bom_cost_by_tier(con)
    assert out == {"459": {"bom_avg_cost": 11.0, "bom_dish_count": 2},
                   "629": {"bom_avg_cost": 14.0, "bom_dish_count": 3}}


def test_bom_dishes_by_tier_is_cumulative_and_richest_first():
    """_pt_bom_dishes_by_tier backs the per-tier drilldown: every dish the tier
    can eat — its own plus the cheaper tiers' — sorted so the priciest (the ones
    pulling the average up) come first."""
    from routers.reports import _pt_bom_dishes_by_tier
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE skus(sku_id INTEGER PRIMARY KEY, sku_code TEXT, description TEXT)")
    con.execute("CREATE TABLE sku_tier(sku_id INTEGER PRIMARY KEY, tier TEXT NOT NULL)")
    con.execute("CREATE TABLE sku_effective_cost(sku_id INTEGER PRIMARY KEY, recipe_cost REAL)")
    con.execute("INSERT INTO skus VALUES(1, 'OTHBF00001', 'ยำไทยปูอัด')")
    con.execute("INSERT INTO skus VALUES(2, 'OTHBF00002', 'หัวปลาแซลมอนย่างเกลือ')")
    con.execute("INSERT INTO skus VALUES(3, 'OTHBF00003', 'สเต๊กหอยเชลล์')")
    con.executemany("INSERT INTO sku_tier VALUES(?,?)", [(1, "459"), (2, "459"), (3, "629")])
    con.executemany("INSERT INTO sku_effective_cost VALUES(?,?)", [(1, 10.0), (2, 18.13), (3, 60.0)])
    yam = {"sku_code": "OTHBF00001", "label": "ยำไทยปูอัด", "bom_cost": 10.0}
    salmon = {"sku_code": "OTHBF00002", "label": "หัวปลาแซลมอนย่างเกลือ", "bom_cost": 18.13}
    scallop = {"sku_code": "OTHBF00003", "label": "สเต๊กหอยเชลล์", "bom_cost": 60.0}
    out = _pt_bom_dishes_by_tier(con)
    assert out == {"459": [salmon, yam], "629": [scallop, salmon, yam]}


def test_bom_dishes_by_tier_ignores_a_non_numeric_tier():
    """"promo" is a bucket of odd-priced bills, not a rung on the price ladder —
    it must not swallow the real tiers' dishes or appear as one."""
    from routers.reports import _pt_bom_dishes_by_tier
    con = _tier_db([(1, "459", 10.0), (2, "promo", 99.0)])
    out = _pt_bom_dishes_by_tier(con)
    assert list(out) == ["459"] and len(out["459"]) == 1


def test_bom_dishes_by_tier_falls_back_to_sku_code_without_a_description():
    from routers.reports import _pt_bom_dishes_by_tier
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE skus(sku_id INTEGER PRIMARY KEY, sku_code TEXT, description TEXT)")
    con.execute("CREATE TABLE sku_tier(sku_id INTEGER PRIMARY KEY, tier TEXT NOT NULL)")
    con.execute("CREATE TABLE sku_effective_cost(sku_id INTEGER PRIMARY KEY, recipe_cost REAL)")
    con.execute("INSERT INTO skus VALUES(1, 'OTHBF00009', NULL)")
    con.execute("INSERT INTO sku_tier VALUES(1, '459')")
    con.execute("INSERT INTO sku_effective_cost VALUES(1, 5.0)")
    out = _pt_bom_dishes_by_tier(con)
    assert out["459"][0]["label"] == "OTHBF00009"


def test_bom_cost_by_tier_is_empty_before_the_table_exists():
    """A DB copy from before this feature (or the shared test fixture) has no
    sku_tier table yet — must degrade to no BoM figures, not crash the report."""
    from routers.reports import _pt_bom_cost_by_tier
    con = sqlite3.connect(":memory:")
    assert _pt_bom_cost_by_tier(con) == {}
