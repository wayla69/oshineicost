"""Pack sizes corrected in the app must survive the daily DB swap.

Owner, 2026-09-21: "Make pack sizes edited in the app survive the daily sync."

ingredient_order_unit is written on BOTH sides: seed_order_units.py on the PC
(source='supplier-sheet', must keep reaching the site) and the ⚙️ tab / its Excel import
on the site (source='manual'). The seeder never overwrote a manual row; the swap replaced
the whole table. Now a person's row is carried forward over the PC's copy, row by row.
"""
import sqlite3

import core
import main
import routers.ingredients as ing


def _db(path, rows):
    c = sqlite3.connect(str(path))
    c.execute(ing._ING_ORDER_UNIT_DDL if hasattr(ing, "_ING_ORDER_UNIT_DDL") else _DDL)
    c.executemany("INSERT INTO ingredient_order_unit(ingredient_code, base_unit, order_unit, qty_per_order,"
                  " price_per_order, source) VALUES (?,?,?,?,?,?)", rows)
    c.execute("CREATE TABLE IF NOT EXISTS sales_transaction(id INTEGER PRIMARY KEY)")
    c.commit(); c.close()


_DDL = """CREATE TABLE IF NOT EXISTS ingredient_order_unit (
  ingredient_code TEXT PRIMARY KEY, base_unit TEXT NOT NULL, order_unit TEXT NOT NULL,
  qty_per_order REAL NOT NULL, price_per_order REAL, moq REAL DEFAULT 1, round_step REAL DEFAULT 1,
  note TEXT, source TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)"""


def _units(path):
    c = sqlite3.connect(str(path))
    out = {r[0]: r[1:] for r in c.execute(
        "SELECT ingredient_code, order_unit, qty_per_order, source FROM ingredient_order_unit")}
    c.close()
    return out


def test_a_pack_size_fixed_in_the_app_beats_the_pc_seed_and_the_seed_still_arrives(tmp_path):
    live, inc = tmp_path / "live.db", tmp_path / "inc.db"
    _db(live, [("SALMON", "กรัม", "กล่อง 10 กก.", 10000, 3200, "manual"),          # the kitchen corrected it on the site
               ("NORI", "แผ่น", "แพ็ค 100", 100, 373, "supplier-sheet"),          # yesterday's seed, since re-seeded
               ("WASABI", "กรัม", "หลอด", 43, 35, "manual")])                     # typed on the site, the PC never had it
    _db(inc, [("SALMON", "กรัม", "กก.", 1000, 320, "supplier-sheet"),             # the PC still has the old guess
              ("NORI", "แผ่น", "แพ็ค 50", 50, 190, "supplier-sheet"),             # the PC re-seeded from a new supplier sheet
              ("RICE", "กรัม", "กระสอบ 25 กก.", 25000, 1000, "supplier-sheet")])  # new on the PC
    safe, reason = main._carry_forward_and_verify(str(live), str(inc))
    assert safe, reason
    assert _units(inc) == {
        "SALMON": ("กล่อง 10 กก.", 10000.0, "manual"),           # the person wins
        "WASABI": ("หลอด", 43.0, "manual"),                       # and is not lost for being unknown to the PC
        "NORI": ("แพ็ค 50", 50.0, "supplier-sheet"),              # the PC's newer seed wins over its older one
        "RICE": ("กระสอบ 25 กก.", 25000.0, "supplier-sheet"),     # and new seeds still reach the site
    }


def test_no_manual_rows_means_the_pcs_table_arrives_untouched(tmp_path):
    live, inc = tmp_path / "live.db", tmp_path / "inc.db"
    _db(live, [("NORI", "แผ่น", "แพ็ค 100", 100, 373, "supplier-sheet")])
    _db(inc, [("NORI", "แผ่น", "แพ็ค 50", 50, 190, "supplier-sheet")])
    assert main._carry_forward_and_verify(str(live), str(inc))[0]
    assert _units(inc) == {"NORI": ("แพ็ค 50", 50.0, "supplier-sheet")}


def test_a_staged_db_without_the_table_gets_it_created_for_the_manual_rows(tmp_path):
    live, inc = tmp_path / "live.db", tmp_path / "inc.db"
    _db(live, [("SALMON", "กรัม", "กล่อง 10 กก.", 10000, 3200, "manual")])
    c = sqlite3.connect(str(inc)); c.execute("CREATE TABLE sales_transaction(id INTEGER PRIMARY KEY)"); c.commit(); c.close()
    assert main._carry_forward_and_verify(str(live), str(inc))[0]
    assert _units(inc) == {"SALMON": ("กล่อง 10 กก.", 10000.0, "manual")}


def test_the_swap_is_refused_if_a_persons_row_would_be_lost(tmp_path, monkeypatch):
    live, inc = tmp_path / "live.db", tmp_path / "inc.db"
    _db(live, [("SALMON", "กรัม", "กล่อง 10 กก.", 10000, 3200, "manual")])
    _db(inc, [])
    monkeypatch.setattr(core, "_carry_forward_merged", lambda *_: None)            # the merge silently did nothing
    safe, reason = core._carry_forward_and_verify(str(live), str(inc))
    assert not safe and "ingredient_order_unit" in reason


def test_a_live_db_that_never_had_the_table_is_fine(tmp_path):
    live, inc = tmp_path / "live.db", tmp_path / "inc.db"
    c = sqlite3.connect(str(live)); c.execute("CREATE TABLE app_user(id INTEGER PRIMARY KEY)"); c.commit(); c.close()
    _db(inc, [("NORI", "แผ่น", "แพ็ค 50", 50, 190, "supplier-sheet")])
    assert main._carry_forward_and_verify(str(live), str(inc))[0]
    assert _units(inc) == {"NORI": ("แพ็ค 50", 50.0, "supplier-sheet")}


def test_an_older_table_without_a_source_column_is_skipped_not_fatal(tmp_path):
    # caught by the gate: test_forced_order builds exactly this shape, and the merge raised
    live, inc = tmp_path / "live.db", tmp_path / "inc.db"
    for path, val in ((live, "on the site"), (inc, "from the PC")):
        c = sqlite3.connect(str(path))
        c.execute("CREATE TABLE ingredient_order_unit(ingredient_code TEXT PRIMARY KEY, order_unit TEXT)")
        c.execute("INSERT INTO ingredient_order_unit VALUES('X', ?)", (val,))
        c.commit(); c.close()
    safe, reason = main._carry_forward_and_verify(str(live), str(inc))
    assert safe, reason
    c = sqlite3.connect(str(inc))
    assert c.execute("SELECT order_unit FROM ingredient_order_unit").fetchall() == [("from the PC",)]
    c.close()


def test_what_the_app_writes_is_what_the_swap_keeps(client, monkeypatch):
    """The tag is the contract: if the tab ever stops writing source='manual', the swap would
    start eating its edits again - silently."""
    monkeypatch.delenv("OSHINEI_SEED_TOKEN", raising=False)
    r = client.post("/api/ingredients/order-units", json=[{
        "ingredient_code": "PACK-TEST", "base_unit": "กรัม", "order_unit": "ถุง 5 กก.", "qty_per_order": 5000}])
    assert r.status_code == 200, r.text
    con = sqlite3.connect(str(main.DB_PATH))
    src = con.execute("SELECT source FROM ingredient_order_unit WHERE ingredient_code='PACK-TEST'").fetchone()[0]
    n = con.execute(f"SELECT COUNT(*) FROM ingredient_order_unit WHERE {core._MERGED_ROWS['ingredient_order_unit'][1]}"
                    " AND ingredient_code='PACK-TEST'").fetchone()[0]
    con.execute("DELETE FROM ingredient_order_unit WHERE ingredient_code='PACK-TEST'"); con.commit(); con.close()
    main.invalidate_cache()
    assert src == "manual" and n == 1


def test_the_nightly_backup_includes_the_pack_sizes(client, monkeypatch):
    import gzip, json
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "admin:" + "cd" * 32)
    con = sqlite3.connect(str(main.DB_PATH)); con.execute(_DDL); con.commit(); con.close()
    r = client.get("/api/_admin/export-state", headers={"X-Seed-Token": "cd" * 32})
    assert r.status_code == 200
    tables = {json.loads(l)["table"] for l in gzip.decompress(r.content).decode("utf-8").splitlines() if '"table"' in l[:12]}
    assert "ingredient_order_unit" in tables
