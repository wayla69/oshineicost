# -*- coding: utf-8 -*-
"""A price import in one confirm (BoM Center UX overhaul, PR-9).

The pack guard refuses a line whose pack size contradicts the master and suggests
a unit rule. Until now the person saved the rules, then uploaded the same file
again. Now the ticked rules travel WITH the import: a preview carrying them already
prices the lines they unlock, the real call writes rules and prices in one
transaction, and a preview writes neither.
"""
import io
import sqlite3

from openpyxl import Workbook

TOKEN = "s3cret-write-token"
CODE = "03-01-179"


def _book():
    """The accounting workbook for June 2569 with two shrimp lines bought by the 1 kg pack."""
    wb = Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet("Use KR1")
    for r in [[None, "บริษัท โอชิเน"], [None, "รายงานจ่ายสินค้าภายใน"], [None, "วันที่จาก  01/06/2569  ถึง  30/06/2569"],
              [None, "เอกสาร", "วันที่เอกสาร", "แผนก", "หมายเหตุ", "ลำดับ", "รหัสสินค้า", "รายละเอียด", "คลังที่", "จำนวน",
               "หน่วยนับ", "ราคาต่อหน่วย", "มูลค่ารวม"],
              [None, "OU6906001", None, None, None, None, None, None, None, None, None, None, None],
              [None, None, None, None, None, 1, CODE, "กุ้งขาว", 1, 10, "PK", 310, 3100],
              [None, None, None, None, None, 2, CODE, "กุ้งขาว", 1, 5, "แพ็ค,ห่อ", 309.8, 1549]]:
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def _rows(db_path):
    c = sqlite3.connect(str(db_path))
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bc%'")]
    snap = {t: c.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
    c.close()
    return snap


def _restore(db_path, snap):
    c = sqlite3.connect(str(db_path))
    for t, rows in snap.items():
        c.execute(f"DELETE FROM {t}")
        if rows:
            c.executemany(f"INSERT INTO {t} VALUES({','.join('?' * len(rows[0]))})", rows)
    c.commit()
    c.close()


def test_rules_travel_with_the_import(client, monkeypatch):
    import main
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    client.get("/api/bom-center/summary")                       # creates the bc_* tables
    snap = _rows(main.DB_PATH)
    c = sqlite3.connect(str(main.DB_PATH))
    # the master says one pack of shrimp is the 10 kg box; a July quote gives the guard its reference
    c.execute("INSERT OR REPLACE INTO bc_ingredient(code, name_th, kind, base_unit, recv_unit, qty_per_recv, active)"
              " VALUES(?, 'กุ้งขาว', 'raw', 'กรัม', 'ลัง 10 กก.', 10000, 1)", (CODE,))
    c.execute("INSERT OR REPLACE INTO bc_ingredient_price(ingredient_code, month, price_per_base, source)"
              " VALUES(?, '2026-07', 0.3098, 'list_ref')", (CODE,))
    c.commit()
    c.close()
    try:
        # the preview: the guard refuses the lines and suggests the 1 kg pack
        r = client.post("/api/bom-center/prices/import?dry_run=true", files={"file": ("u.xlsx", _book())})
        assert r.status_code == 200, r.text
        pre = r.json()
        assert pre["priced"] == 0 and len(pre["suggested_rules"]) == 1
        s = pre["suggested_rules"][0]
        assert s["code"] == CODE and s["unit"] == "แพ็ค" and s["factor"] == 1000
        rules = [{"code": s["code"], "unit": s["unit"], "factor_to_base": s["factor"]}]
        before = _rows(main.DB_PATH)
        # a preview WITH the rule prices the lines — and still writes nothing
        r = client.post("/api/bom-center/prices/import?dry_run=true", files={"file": ("u.xlsx", _book())},
                        data={"rules": __import__("json").dumps(rules)})
        assert r.status_code == 200, r.text
        pre2 = r.json()
        assert pre2["priced"] == 1 and pre2["rules_applied"] == 1
        assert [x for x in pre2["rows"] if x["code"] == CODE][0]["price_per_base"] - 4649 / 15000 < 1e-6
        assert _rows(main.DB_PATH) == before
        # the real call: rule and prices in one go
        r = client.post("/api/bom-center/prices/import?dry_run=false", files={"file": ("u.xlsx", _book())},
                        data={"rules": __import__("json").dumps(rules)}, headers={"X-Upload-Token": TOKEN})
        assert r.status_code == 200, r.text
        assert r.json()["applied"]["written"] >= 1 and r.json()["rules_applied"] == 1
        c = sqlite3.connect(str(main.DB_PATH))
        assert c.execute("SELECT factor_to_base FROM bc_unit_rule WHERE ingredient_code=? AND unit='แพ็ค'", (CODE,)).fetchone()[0] == 1000
        assert c.execute("SELECT source FROM bc_ingredient_price WHERE ingredient_code=? AND month='2026-06'", (CODE,)).fetchone()[0] == "purchase_avg"
        c.close()
        # a malformed rule is refused before anything happens
        r = client.post("/api/bom-center/prices/import?dry_run=true", files={"file": ("u.xlsx", _book())},
                        data={"rules": "[{\"code\": \"x\"}]"})
        assert r.status_code == 400
    finally:
        _restore(main.DB_PATH, snap)
