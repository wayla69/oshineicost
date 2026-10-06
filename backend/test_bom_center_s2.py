"""BoM Center — Sprint S2: recipes by tier.

The promises:
  * a Cost-RW recipe block (no POS code, only names) lands on the right buffet SKU,
    or waits for a person — never on a wrong SKU, never twice on one SKU;
  * a person's answer (a code, or "not on sale") is remembered across imports;
  * a line costs base qty × that month's price ÷ (1 − loss), the line's own loss
    winning over the ingredient's; ฿0 only on purpose; anything uncostable is
    reported, not zero;
  * the basket of tier T holds every dish with min_tier <= T;
  * a released version is read-only.
"""
import io
import sqlite3

import pytest

import conftest as _cf
from bom_center import recipes as R
from bom_center import schema as S

SKUS = [  # sku_id, code, description, tier
    (10, "OTHBFSSM00020", "(B) ซาชิมิปูอัด (6 ชิ้น ต่อ 1 เสิร์ฟ)", "459"),
    (11, "OTHBFGNT00010", "(B) ไก่ย่างเทริยากิ (1 ชิ้น ต่อ 1 เสิร์ฟ)", "459"),
    (12, "OTHBFSUS00033", "(B) ซูชิกุ้ง (1 ชิ้น ต่อ 1 เสิร์ฟ)", "629"),
    (13, "OTHBFROL00007", "(B) หนังปลาแซลมอนกรอบโรล (3 ชิ้น ต่อ 1 เสิร์ฟ)", "459"),
    (14, "OTHBFSUS00006", "(B) ซูชิหอยเป๋าฮื้อ ซอสมันปู และอิคุระ (1 ชิ้น)", "1299"),
]


def _db(tmp_path):
    p = tmp_path / "s2.db"
    _cf._build_db(str(p))
    c = sqlite3.connect(str(p))
    S.ensure(c, commit=True)
    c.execute("CREATE TABLE IF NOT EXISTS sku_tier(sku_id INTEGER PRIMARY KEY, tier TEXT)")
    for sid, code, desc, tier in SKUS:
        c.execute("INSERT INTO skus(sku_id, sku_code, description, category_id, unit, business_type, active)"
                  " VALUES(?,?,?,3,'plate','buffet',1)", (sid, code, desc))
        c.execute("INSERT INTO sku_tier(sku_id, tier) VALUES(?,?)", (sid, tier))
    c.executemany("INSERT INTO bc_ingredient(code, name_th, kind, base_unit, loss_pct) VALUES(?,?,?,?,?)", [
        ("13-01-009", "ปูอัด", "raw", "กรัม", 0), ("V-007", "หัวไชเท้า", "raw", "กรัม", 10),
        ("02-01-001", "แซลมอน", "raw", "กรัม", 40), ("11-02-073", "สาหร่ายแผ่น", "raw", "แผ่น", 0)])
    c.executemany("INSERT INTO bc_ingredient_price(ingredient_code, month, price_per_base, source) VALUES(?,?,?,?)", [
        ("13-01-009", "2026-07", 0.15, "list_ref"), ("V-007", "2026-07", 0.04, "list_ref"),
        ("02-01-001", "2026-07", 0.3714, "list_ref"), ("11-02-073", "2026-07", 3.73, "list_ref")])
    c.commit()
    return c


@pytest.fixture
def con(tmp_path):
    c = _db(tmp_path)
    yield c
    c.close()


def _block(tier, th, en="", ings=None, total=None, sheet=None):
    return {"tier": tier, "th": th, "en": en, "serve": "6 ชิ้น ต่อ 1 เสิร์ฟ", "total": total,
            "sheet": sheet or f"BF_{tier}NET", "kind": "BF", "ings": ings or [
                {"seq": 1, "code": "13-01-009", "prod": "ปูอัดซาชิมิ", "ing": "ปูอัด", "detail": "แท่งละ 15 กรัม x 3",
                 "full_price": 1500, "full_qty": 10000, "loss": 0, "qty": 45, "unit": "กรัม", "cost": 6.75,
                 "raw_cost": "6.75"},
                {"seq": 2, "code": "V-007", "prod": "หัวไชเท้า", "ing": "หัวไชเท้า", "detail": "สไลด์เส้น",
                 "full_price": 40, "full_qty": 1000, "loss": 60.2, "qty": 10, "unit": "กรัม",
                 "cost": 0.4256224729, "raw_cost": "0.4256"}]}


def test_name_key():
    assert R.name_key("(B) ไก่ย่างเทริยากิ (1 ชิ้น ต่อ 1 เสิร์ฟ)") == R.name_key("ไก่ย่างเทอริยากิ")
    assert R.name_key("ซาชิมิปูอัด ") == R.name_key("(BF) ซาชิมิ ปูอัด (6 ชิ้น ต่อ 1 เสิร์ฟ)")


def test_match_exact_fuzzy_anytier_and_unmatched(con):
    blocks = [_block(459, "ซาชิมิปูอัด "), _block(459, "ไก่ย่างเทอริยากิ"),
              _block(459, "ซูชิกุ้ง"),                          # exists only at 629 -> any tier
              _block(459, "หนังปลาแซลมอนโรล"),                  # close to "…กรอบโรล" -> fuzzy
              _block(459, "ลูกชิ้นหมู")]                          # nothing like it
    R.match_blocks(con, blocks)
    got = {b["th"].strip(): (b["sku_code"], b["method"]) for b in blocks}
    assert got["ซาชิมิปูอัด"] == ("OTHBFSSM00020", "exact")
    assert got["ไก่ย่างเทอริยากิ"] == ("OTHBFGNT00010", "exact")
    assert got["ซูชิกุ้ง"] == ("OTHBFSUS00033", "exact-anytier")
    assert got["หนังปลาแซลมอนโรล"] == ("OTHBFROL00007", "fuzzy")
    assert got["ลูกชิ้นหมู"] == (None, None)


def test_one_sku_never_takes_two_blocks(con):
    blocks = [_block(459, "ซาชิมิปูอัด"), _block(459, "ซาชิมิปูอัด")]
    R.match_blocks(con, blocks)
    assert [b["sku_code"] for b in blocks].count("OTHBFSSM00020") == 1
    assert any(b.get("why") for b in blocks)


def test_import_cost_and_tie_out(con):
    rid = R.get_or_create_draft(con, "T", "2026-11", "2026-07", "t")
    st = R.import_cost_rw(con, {"blocks": [_block(459, "ซาชิมิปูอัด", total=7.175622), _block(459, "ลูกชิ้นหมู")]},
                          rid, "t")
    assert st["matched"] == 1 and len(st["unmatched"]) == 1
    c = R.cost_recipe(con, rid, "OTHBFSSM00020")
    # 45 g × 0.15 + 10 g × 0.04 ÷ (1 − 6.02 %)  — the LINE's loss (60.2 / 1000), not the master's 10 %
    assert c["total"] == pytest.approx(6.75 + 0.4 / (1 - 0.0602), abs=1e-6)
    assert c["total"] == pytest.approx(7.175622, abs=0.01)
    v = R.tier_view(con, rid)
    assert v["sheet_mismatch"] == 0 and v["rows"][0]["min_tier"] == 459
    assert len(v["unmatched"]) == 1


def test_mapping_is_remembered_and_skip_is_remembered(con):
    rid = R.get_or_create_draft(con, "T", "2026-11", "2026-07", "t")
    st = R.import_cost_rw(con, {"blocks": [_block(459, "ไส้หมู"), _block(459, "ลูกชิ้นหมู")]}, rid, "t")
    assert len(st["unmatched"]) == 2
    refs = [r for (r,) in con.execute("SELECT ref FROM bc_release_check WHERE release_id=? ORDER BY ref", (rid,))]
    lookup = {r.split("|")[1]: r for r in refs}
    R.apply_mapping(con, rid, lookup[R.name_key("ไส้หมู")], "-", "t")             # not on sale
    R.apply_mapping(con, rid, lookup[R.name_key("ลูกชิ้นหมู")], "OTHBFGNT00010", "t")
    assert R.cost_recipe(con, rid, "OTHBFGNT00010")["in_release"]
    again = R.import_cost_rw(con, {"blocks": [_block(459, "ไส้หมู"), _block(459, "ลูกชิ้นหมู")]}, rid, "t")
    assert again["unmatched"] == [] and again["by_method"] == {"map": 1} and len(again["skipped_not_on_sale"]) == 1
    with pytest.raises(ValueError):
        R.apply_mapping(con, rid, "459|nothing", "OTHBFGNT00010", "t")


def test_costing_problems_zero_by_design_and_errors(con):
    rid = R.get_or_create_draft(con, "T", "2026-11", "2026-07", "t")
    ings = [
        {"seq": 1, "code": "02-01-001", "ing": "แซลมอน", "detail": "ส่วนบราวน์", "full_price": 0, "full_qty": 1000,
         "loss": 400, "qty": 30, "unit": "กรัม", "cost": 0, "raw_cost": "0"},               # trimmings: ฿0 on purpose
        {"seq": 2, "code": "#N/A", "ing": "อะไรสักอย่าง", "full_price": 0, "full_qty": 0, "loss": 0,
         "qty": 5, "unit": "กรัม", "cost": 0, "raw_cost": "#N/A"},                          # broken: a problem
        {"seq": 3, "code": "11-02-073", "ing": "สาหร่าย", "full_price": 373, "full_qty": 100, "loss": 0,
         "qty": 0.5, "unit": "แผ่น", "cost": 1.865, "raw_cost": "1.865"},
        {"seq": 4, "code": "13-01-009", "ing": "ปูอัด", "full_price": 1500, "full_qty": 10000, "loss": 0,
         "qty": 2, "unit": "ลิตร", "cost": 0, "raw_cost": "0"}]                               # grams vs litres
    R.import_cost_rw(con, {"blocks": [_block(459, "ซาชิมิปูอัด", ings=ings)]}, rid, "t")
    c = R.cost_recipe(con, rid, "OTHBFSSM00020")
    by = {ln["seq"]: ln for ln in c["lines"]}
    assert by[1].get("zero") and by[1]["cost"] == 0 and not by[1]["problem"]
    assert by[2]["problem"] == "ไม่มีรหัสวัตถุดิบ"
    assert by[3]["cost"] == pytest.approx(1.865)
    assert by[4]["problem"] and "แปลง" in by[4]["problem"]
    assert c["problems"] == 2


def test_tier_baskets_are_cumulative(con):
    rid = R.get_or_create_draft(con, "T", "2026-11", "2026-07", "t")
    R.import_cost_rw(con, {"blocks": [_block(459, "ซาชิมิปูอัด"), _block(629, "ซูชิกุ้ง"),
                                      _block(1299, "ซูชิหอยเป๋าฮื้อ ซอสมันปู และอิคุระ")]}, rid, "t")
    sizes = {t: len(R.tier_view(con, rid, t)["rows"]) for t in R.TIERS}
    assert sizes == {459: 1, 629: 2, 789: 2, 999: 2, 1299: 3}


def test_edit_and_release_lock_and_flat_roundtrip(con):
    rid = R.get_or_create_draft(con, "T", "2026-11", "2026-07", "t")
    R.import_cost_rw(con, {"blocks": [_block(459, "ซาชิมิปูอัด")]}, rid, "t")
    R.save_lines(con, rid, "OTHBFSSM00020", [{"ingredient_code": "13-01-009", "ingredient_name": "ปูอัด", "qty": 60,
                                             "unit": "กรัม"}], "t")
    assert R.cost_recipe(con, rid, "OTHBFSSM00020")["total"] == pytest.approx(9.0)
    with pytest.raises(ValueError):
        R.save_lines(con, rid, "OTHBFSSM00020", [{"ingredient_code": "13-01-009", "qty": 1, "loss_pct": 120}], "t")
    wb = R.export_flat(con, rid)
    buf = io.BytesIO()
    wb.save(buf)
    R.save_lines(con, rid, "OTHBFSSM00020", [{"ingredient_code": "13-01-009", "qty": 1, "unit": "กรัม"}], "t")
    out = R.import_flat(con, buf.getvalue(), rid, "t")
    assert out["recipes"] == 1 and out["errors"] == []
    assert R.cost_recipe(con, rid, "OTHBFSSM00020")["total"] == pytest.approx(9.0)     # back to the exported recipe
    con.execute("UPDATE bc_release SET status='released' WHERE release_id=?", (rid,))
    with pytest.raises(ValueError):
        R.save_lines(con, rid, "OTHBFSSM00020", [], "t")
    with pytest.raises(ValueError):
        R.get_or_create_draft(con, "T", "2026-11", "2026-07", "t")


def _cost_rw_workbook():
    """A two-sheet workbook in the Cost-RW layout: header row, name row, column row, lines, total row."""
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name, (en, th, lines, total) in {
        "BF_459NET_1menu": ("Kani Sashimi", "ซาชิมิปูอัด ",
                            [(1, "13-01-009", "ปูอัดซาชิมิ", "ปูอัด", "x3", 1500, 10000, 0, 10000, 0.15, 45, "กรัม", 6.75)], 6.75),
        "สำเนาของ BF_459NET_1menu": ("Kani Sashimi", "ซาชิมิปูอัด", [], 0),
        "BF_629NET_1menu": ("Ebi Sushi", "ซูชิกุ้ง",
                            [(1, "11-02-073", "สาหร่าย", "สาหร่าย", "", 373, 100, 0, 100, 3.73, 0.5, "แผ่น", 1.865)], 1.865),
    }.items():
        ws = wb.create_sheet(name)
        ws.append([None, "ต้นทุนราคาเมนู"])
        ws.append([None, "รูปภาพอาหาร", None, "Product Item Code", "MENU NAME", None, None, None, "ชื่อภาษาไทย"])
        ws.append([None, None, None, None, en, None, None, None, th])
        ws.append([None, "ลำดับ", "รหัสสินค้า", "ชื่อรายละเอียดผลิตภัณฑ์", "ส่วนผสม", "รายละเอียด", "ราคาเต็ม",
                   "น้ำหนักเต็ม", "ตัดวัตถุดิบสูญเสีย", "ปริมาณการใช้", "ราคา/หน่วย", "ปริมาณวัตถุดิบ", "หน่วย", "ต้นทุนสูตร"])
        for ln in lines:
            ws.append([None, *ln])
        ws.append([None, "ราคา (บาท)", None, None, "ปริมาณการเสิร์ฟ", " 6 ชิ้น ต่อ 1 เสิร์ฟ", None, None, "ต้นทุน",
                   None, None, None, None, total])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def test_parse_cost_rw_layout():
    p = R.parse_cost_rw(_cost_rw_workbook())
    assert [s["tier"] for s in p["sheets"]] == [459, 629]
    assert p["skipped"] and "สำเนา" in p["skipped"][0]["sheet"]
    b = p["blocks"][0]
    assert b["tier"] == 459 and b["th"].strip() == "ซาชิมิปูอัด" and b["total"] == 6.75 and len(b["ings"]) == 1


def test_endpoints_import_map_edit_export(client):
    import main
    c = sqlite3.connect(str(main.DB_PATH))
    S.ensure(c, commit=True)
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bc%'")]
    snap = {t: c.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
    c.execute("CREATE TABLE IF NOT EXISTS sku_tier(sku_id INTEGER PRIMARY KEY, tier TEXT)")
    for sid, code, desc, tier in SKUS[:3]:
        c.execute("INSERT OR IGNORE INTO skus(sku_id, sku_code, description, category_id, unit, business_type, active)"
                  " VALUES(?,?,?,3,'plate','buffet',1)", (sid, code, desc))
        c.execute("INSERT OR REPLACE INTO sku_tier(sku_id, tier) VALUES(?,?)", (sid, tier))
    c.executemany("INSERT OR REPLACE INTO bc_ingredient(code, name_th, kind, base_unit) VALUES(?,?,?,?)",
                  [("13-01-009", "ปูอัด", "raw", "กรัม"), ("11-02-073", "สาหร่าย", "raw", "แผ่น")])
    c.executemany("INSERT OR REPLACE INTO bc_ingredient_price(ingredient_code, month, price_per_base, source)"
                  " VALUES(?,?,?, 'list_ref')", [("13-01-009", "2026-07", 0.15), ("11-02-073", "2026-07", 3.73)])
    c.commit()
    c.close()
    try:
        data = _cost_rw_workbook()
        url = "/api/bom-center/recipes/import-cost-rw?effective_month=2026-11&price_month=2026-07&label=TEST"
        pre = client.post(url + "&dry_run=true", files={"file": ("cost.xlsx", data)}).json()
        assert pre["matched"] == 2 and pre["release_id"] is None and pre["sheet_mismatch"] == 0
        assert not any(r["label"] == "TEST" for r in client.get("/api/bom-center/releases").json()["releases"])
        done = client.post(url + "&dry_run=false", files={"file": ("cost.xlsx", data)}).json()
        rid = done["release_id"]
        v = client.get(f"/api/bom-center/recipes?release_id={rid}&tier=459").json()
        assert [r["sku_code"] for r in v["rows"]] == ["OTHBFSSM00020"]
        assert v["rows"][0]["cost"] == pytest.approx(6.75)
        d = client.get(f"/api/bom-center/recipes/OTHBFSSM00020?release_id={rid}").json()
        assert d["dish"]["min_tier"] == 459 and d["lines"][0]["cost"] == pytest.approx(6.75)
        r = client.put(f"/api/bom-center/recipes/OTHBFSSM00020?release_id={rid}",
                       json=[{"ingredient_code": "13-01-009", "qty": 60, "unit": "กรัม"}])
        assert r.status_code == 200 and r.json()["total"] == pytest.approx(9.0)
        assert client.get(f"/api/bom-center/recipes-export.xlsx?release_id={rid}").status_code == 200
        assert client.get(f"/api/bom-center/buffet-skus?release_id={rid}").status_code == 200
        bad = client.post("/api/bom-center/recipes/map", json={"release_id": rid, "ref": "x", "sku_code": "OTHBFGNT00010"})
        assert bad.status_code == 400
    finally:
        c = sqlite3.connect(str(main.DB_PATH))
        for t in [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bc%'")]:
            c.execute(f"DELETE FROM {t}")
            rows = snap.get(t) or []
            if rows:
                c.executemany(f"INSERT INTO {t} VALUES({','.join('?' * len(rows[0]))})", rows)
        c.execute("DELETE FROM sku_tier WHERE sku_id IN (10, 11, 12)")
        c.execute("DELETE FROM skus WHERE sku_id IN (10, 11, 12)")
        c.commit()
        c.close()
