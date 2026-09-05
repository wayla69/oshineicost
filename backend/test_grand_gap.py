# -*- coding: utf-8 -*-
"""close_grand_menu_gap.py — the classifier's rules, the apply / undo round trip
and the coverage figure, on a small in-memory DB shaped like the real one."""
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import close_grand_menu_gap as G  # noqa: E402


def _db():
    con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE categories(category_id INTEGER PRIMARY KEY, name TEXT);
    INSERT INTO categories VALUES (2,'Oshinei ALCOHOL'),(3,'Oshinei APPETIZER'),(12,'Oshinei SERVICE'),(15,'Oshinei SOFT DRINK'),(55,'Oshinei CONDIMENT');
    CREATE TABLE skus(sku_id INTEGER PRIMARY KEY, sku_code TEXT, description TEXT, category_id INTEGER, unit TEXT, active INTEGER, business_type TEXT);
    CREATE TABLE sku_cost(sku_id INTEGER PRIMARY KEY, unit_cost REAL, effective_from TEXT, updated_at TEXT);
    CREATE TABLE bom_ingredients(sku_id INTEGER, seq INTEGER, ingredient_code TEXT, ingredient_name TEXT, qty_per_serving REAL, unit TEXT,
                                 price_per_unit REAL, ingredient_cost REAL, ingredient_type TEXT, source_file TEXT);
    CREATE TABLE bom_sku_alias(bom_sku_code TEXT, resolved_sku_id INTEGER, match_method TEXT, confidence REAL, notes TEXT);
    CREATE TABLE sales_transaction(receipt_no TEXT, sold_at TEXT, branch_id INTEGER);
    CREATE TABLE sale_bill_item(receipt_no TEXT, sku_id INTEGER, qty REAL, rev REAL, bom_cost REAL);
    """)
    rows = [(1, "B0119", "(B) ปีกไก่ทอด (1 ชิ้น)", 3), (2, "THBFSOU00002", "(B) ซุปชาบู", 3), (3, "THBFSOU00003", "(B) ซุปสุกี้ยากี้", 3),
            (4, "THBFSUS00030", "(B) ซูชิแซลมอนเบิร์น(1 ชิ้น)", 3),
            (10, "OTHBFSPC00110", "(BF) ปีกไก่ทอด", None), (11, "OTHALSOU00004", "(AL) ซุปชาบู + ซุปสุกี้ยากี้ (1ที่)", None),
            (12, "OTHBFBVR00001", "(BF) Pepsi", None), (13, "OTHALBVR00007", "(AL) เบียร์ไฮเนเก้นขวดใหญ่", None),
            (14, "OTHBFAPP00020", "(BF) วาซาบิ", None), (15, "OTHBFSPC00044", "(BF) น้ำจิ้มซีฟู้ด", None),
            (16, "OTHOTOTH00014", "(OT) ค่าปรับบุฟเฟ่ต์ 999 Net (10 กรัม)", None), (17, "OTHDLPMO00001", "(DL) แซลมอน ซาชิมิ 500 กรัม", None),
            (18, "OTHDLPMO00011", "(DL) SALMON SUSHI (12 คำ)", None), (19, "OTHBFCRE00004", "(BF) ซอฟต์ครีมทุเรียน (1ถ้วย)", None),
            (20, "OTHBFSPC00066", "(BF) น้ำเปล่า รีฟิล", None)]
    con.executemany("INSERT INTO skus(sku_id, sku_code, description, category_id) VALUES (?,?,?,?)", rows)
    con.executemany("INSERT INTO sku_cost VALUES (?,?,?,?)", [(1, 12.4758, "2026-08-27", "x"), (4, 10.8333, "2026-08-27", "x")])
    con.executemany("INSERT INTO bom_ingredients VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [(2, 1, "c1", "น้ำซุป", 1, "ที่", 2.4335, 2.4335, "other", "old"), (3, 1, "c2", "น้ำซุป", 1, "ที่", 24.8724, 24.8724, "other", "old")])
    con.execute("INSERT INTO sales_transaction VALUES ('r1', '2026-09-02 12:00:00', 1)")
    con.executemany("INSERT INTO sale_bill_item VALUES ('r1', ?, ?, 0, 0)", [(10, 40), (11, 120), (12, 236), (15, 223), (16, 17), (19, 209), (1, 5)])
    return con


def test_classify_apply_undo_and_coverage():
    con = _db()
    G.rebuild(con)
    data = G.load(con)
    dec = {d["code"]: d for d in G.classify(data)}
    assert dec["OTHBFSPC00110"]["action"] == "cost" and dec["OTHBFSPC00110"]["unit_cost"] == 12.4758 and "B0119" in dec["OTHBFSPC00110"]["source"]
    assert dec["OTHALSOU00004"]["action"] == "cost" and dec["OTHALSOU00004"]["unit_cost"] == round(2.4335 + 24.8724, 4)
    assert dec["OTHBFBVR00001"]["category"] == "Oshinei SOFT DRINK" and dec["OTHALBVR00007"]["category"] == "Oshinei ALCOHOL"
    assert dec["OTHBFAPP00020"]["category"] == "Oshinei CONDIMENT" and dec["OTHBFSPC00044"]["category"] == "Oshinei CONDIMENT"
    assert dec["OTHOTOTH00014"]["category"] == "Oshinei SERVICE" and dec["OTHBFSPC00066"]["category"] == "Oshinei SOFT DRINK"
    assert dec["OTHDLPMO00001"]["unit_cost"] == round(500 * G.SALMON_NET_PRICE, 2) and dec["OTHDLPMO00001"]["source"].startswith("estimate:salmon")
    assert dec["OTHDLPMO00011"]["unit_cost"] == round(12 * 10.8333, 4)
    assert dec["OTHBFCRE00004"]["action"] == "worklist" and dec["OTHBFCRE00004"]["qty_sep"] == 209
    assert "B0119" not in dec                                     # only OTH stubs are decided
    before = G.nonfood_share_sept(con)
    assert before["lines"] == 7 and before["zero"] == 6           # every stub line books ฿0; the old dish does not
    log = G.apply(con, list(dec.values()))
    assert len(log["categories"]) == 6 and len(log["sku_cost"]) == 4
    after = G.nonfood_share_sept(con)
    assert after["lines"] == 4 and after["zero"] == 1             # drinks / condiments / fees left the food denominator; the soft cream still books zero
    eff = {r[0]: r[1] for r in con.execute("SELECT sku_id, recipe_cost FROM sku_effective_cost")}
    assert eff[10] == 12.4758 and round(eff[11], 4) == 27.3059 and eff[17] == round(500 * G.SALMON_NET_PRICE, 2) and eff[19] == 0
    assert con.execute("SELECT category_id FROM skus WHERE sku_id=12").fetchone()[0] == 15
    r = G.undo(con, log)
    assert r == {"categories": 6, "sku_cost": 4}
    assert con.execute("SELECT category_id FROM skus WHERE sku_id=12").fetchone()[0] is None
    assert con.execute("SELECT COUNT(*) FROM sku_cost WHERE sku_id IN (10, 11, 17, 18)").fetchone()[0] == 0
    assert G.nonfood_share_sept(con) == before


def test_norm_and_families():
    assert G.norm("(BF) ปีกไก่ทอด") == G.norm("(B) ปีกไก่ทอด (1 ชิ้น)") == "ปีกไก่ทอด"
    assert G.norm("(AL) โซบะเย็น เสิร์ฟพร้อมเทมปุระ(Buffet)") == "โซบะเย็นเสิร์ฟพร้อมเทมปุระ"
    assert G.family("OTHBFSPC00110") == "SPC" and G.variant("OTHDLPMO00001") == "DL" and G.variant("OTHOTOTH00014") == "OT"
