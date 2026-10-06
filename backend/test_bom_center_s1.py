"""BoM Center — Sprint S1: ingredient master, Material Converter, monthly actual prices.

The promises:
  * a quantity in ANY unit people use becomes base units, or the line is reported
    with the reason — never guessed;
  * the month's price is Σ value ÷ Σ base quantity (owner decision §10-1), per
    ingredient and per branch, and a month nobody bought carries the last price
    with its age; a price a person typed is never overwritten by an import;
  * seeding fills blanks and never overwrites what a person set;
  * the accounting issue workbook (รายงานจ่ายสินค้าภายใน) imports as it comes.
"""
import io
import sqlite3

import pytest

import conftest as _cf
from bom_center import master as M
from bom_center import prices as P
from bom_center import schema as S
from bom_center.units import Converter, norm, to_order_units


@pytest.fixture
def con(tmp_path):
    p = tmp_path / "s1.db"
    _cf._build_db(str(p))
    c = sqlite3.connect(str(p))
    S.ensure(c, commit=True)
    c.executemany(
        "INSERT INTO bc_ingredient(code, name_th, kind, base_unit, recv_unit, qty_per_recv) VALUES(?,?,?,?,?,?)",
        [("02-01-001", "แซลมอน", "raw", "กรัม", "แพ็ค 1 กก.", 1000.0),
         ("01-01-040", "ข้าวญี่ปุ่น", "raw", "กรัม", "แพ็ค 25 กก.", 25000.0),
         ("13-01-009", "ปูอัด", "raw", "กรัม", None, None),
         ("11-01-047", "ซอสปลาไหล", "raw", "มิลลิลิตร", "แกลลอน", 3785.0),
         ("CK-013", "สไปซี่แซลมอน", "prepared", "กรัม", None, None)])
    c.execute("INSERT INTO bc_unit_rule(ingredient_code, unit, factor_to_base) VALUES('13-01-009','แท่ง',15)")
    c.commit()
    yield c
    c.close()


# ── converter ────────────────────────────────────────────────────────────────
def test_norm_spellings():
    assert norm("kg") == norm("กก.") == norm("กิโลกรัม") == "กิโลกรัม"
    assert norm(" ML ") == "มิลลิลิตร" and norm("g") == "กรัม"


def test_converter_resolution_order(con):
    cv = Converter(con)
    assert cv.to_base("02-01-001", 2, "กรัม")[0] == 2                     # base unit
    assert cv.to_base("02-01-001", 1.5, "กิโลกรัม")[0] == 1500            # metric step
    assert cv.to_base("02-01-001", 3, "แพ็ค,ห่อ")[0] == 3000              # receiving pack
    assert cv.to_base("13-01-009", 3, "แท่ง")[0] == 45                     # kitchen rule
    assert cv.to_base("11-01-047", 2, "ลิตร")[0] == 2000
    assert cv.to_base("11-01-047", 1, "แกลลอน")[0] == 3785
    q, why = cv.to_base("13-01-009", 1, "แพ็ค")                            # pack with no size
    assert q is None and "ยังไม่ระบุ" in why
    q, why = cv.to_base("02-01-001", 1, "ลิตร")                            # grams vs litres
    assert q is None and "แปลง" in why
    assert cv.to_base("XX-NOPE", 1, "กรัม")[0] is None


def test_accounting_spellings_and_one_rule_per_pack(con):
    assert norm("KG.") == "กิโลกรัม" and norm("Gal") == norm("แกลอน") == "แกลลอน"
    assert norm("PK") == norm("แพ็ค,ห่อ") == norm("แพ็ก") == "แพ็ค"
    cv = Converter(con)
    assert cv.to_base("02-01-001", 2, "KG.")[0] == 2000
    M.set_unit_rule(con, "02-01-001", "PK", 2000, None, "t")            # one rule …
    cv = Converter(con)
    assert cv.to_base("02-01-001", 1, "แพ็ค,ห่อ")[0] == 2000            # … covers every spelling
    assert cv.to_base("02-01-001", 1, "แพ็ค")[0] == 2000
    con.execute("INSERT INTO bc_ingredient(code, kind, base_unit) VALUES('EGG', 'raw', 'ชิ้น')")
    assert Converter(con).to_base("EGG", 30, "ชิ้น,ฟอง")[0] == 30          # composite unit, part by part


def test_pack_guard_refuses_a_tenfold_price_and_suggests_the_pack(con):
    # the master says one pack of shrimp = the 10 kg box; accounting means a 1 kg PK
    con.execute("INSERT INTO bc_ingredient(code, name_th, kind, base_unit, recv_unit, qty_per_recv)"
                " VALUES('03-01-179', 'กุ้งขาว', 'raw', 'กรัม', 'ลัง 10 กก.', 10000)")
    con.execute("INSERT INTO bc_ingredient_price(ingredient_code, month, price_per_base, source)"
                " VALUES('03-01-179', '2026-07', 0.3098, 'list_ref')")
    lines = [{"code": "03-01-179", "qty": 10, "unit": "PK", "value": 3100},
             {"code": "03-01-179", "qty": 5, "unit": "แพ็ค,ห่อ", "value": 1549}]
    agg = P.aggregate(con, lines, month="2026-06")                         # June: the reference is LATER (July)
    assert "03-01-179" not in agg["codes"]
    assert all(abs(i["suggest_factor"] - 1000) < 2 for i in agg["issues"]) and len(agg["issues"]) == 2
    pv = P.preview(con, "2026-06", agg)
    assert len(pv["suggested_rules"]) == 1                                 # PK and แพ็ค,ห่อ are one pack
    s = pv["suggested_rules"][0]
    assert s["unit"] == "แพ็ค" and s["factor"] == 1000 and s["consistent"]
    M.set_unit_rule(con, s["code"], s["unit"], s["factor"], None, "t")
    agg = P.aggregate(con, lines, month="2026-06")
    assert agg["codes"]["03-01-179"]["avg"] == pytest.approx(4649 / 15000)
    # without a reference price the guard cannot judge, and does not invent one
    assert "13-01-009" not in P.aggregate(con, [{"code": "13-01-009", "qty": 1, "unit": "แพ็ค", "value": 1}],
                                          month="2026-06")["codes"]


def test_nice_factor():
    assert P.nice_factor(1005.7) == 1000 and P.nice_factor(20.1) == 20 and P.nice_factor(498.3) == 500
    assert P.nice_factor(1499.3) == 1500 and P.nice_factor(190.1) == 190
    assert P.nice_factor(557.0) == 557                                     # not near a round number: kept


def test_add_unknown_registers_with_a_guessed_base_and_rolls_back(con):
    lines = [{"code": "FV00043", "name": "ทุเรียน", "qty": 10, "unit": "กิโลกรัม", "value": 3800},
             {"code": "EGG-9", "name": "ไข่ไก่", "qty": 30, "unit": "ฟอง", "value": 120}]
    assert "FV00043" not in P.aggregate(con, lines)["codes"]
    agg = P.aggregate(con, lines, add_unknown=True, who="t")
    assert {a["code"]: a["base_unit"] for a in agg["added"]} == {"FV00043": "กรัม", "EGG-9": "ฟอง"}
    assert agg["codes"]["FV00043"]["avg"] == pytest.approx(0.38) and agg["codes"]["EGG-9"]["avg"] == 4
    con.rollback()
    assert con.execute("SELECT COUNT(*) FROM bc_ingredient WHERE code='FV00043'").fetchone()[0] == 0


def test_order_units_round_up():
    assert to_order_units(2500, 1000) == 3
    assert to_order_units(2500, 1000, moq=5) == 5
    assert to_order_units(2500, 1000, step=2) == 4
    assert to_order_units(0, 1000) == 0
    assert to_order_units(3000, 1000) == 3                                 # exact, no float creep


# ── prices ───────────────────────────────────────────────────────────────────
LINES = [   # two branches buy salmon in kg at different prices, rice by the pack
    {"code": "02-01-001", "qty": 100, "unit": "กิโลกรัม", "value": 37000, "branch_id": 1},
    {"code": "02-01-001", "qty": 50, "unit": "กิโลกรัม", "value": 20000, "branch_id": 2},
    {"code": "01-01-040", "qty": 4, "unit": "แพ็ค,ห่อ", "unit_price": 1080, "branch_id": 1},
    {"code": "13-01-009", "qty": 2, "unit": "แพ็ค", "value": 300, "branch_id": 1},     # no pack size
    {"code": "99-99-999", "qty": 1, "unit": "กรัม", "value": 5},                       # not in master
]


def test_weighted_average_per_code_and_branch(con):
    agg = P.aggregate(con, LINES)
    sal = agg["codes"]["02-01-001"]
    assert sal["qty_base"] == 150000 and sal["value"] == 57000
    assert sal["avg"] == pytest.approx(57000 / 150000)                       # not the mean of 0.37 and 0.40
    assert agg["branches"][("02-01-001", 1)]["avg"] == pytest.approx(0.37)
    assert agg["branches"][("02-01-001", 2)]["avg"] == pytest.approx(0.40)
    assert agg["codes"]["01-01-040"]["avg"] == pytest.approx(4320 / 100000)  # value from qty × unit price
    whys = {i["code"]: i["why"] for i in agg["issues"]}
    assert "13-01-009" in whys and "99-99-999" in whys


def test_alias_resolves_warehouse_code(con):
    con.execute("INSERT INTO bc_ingredient_alias(alias_code, ingredient_code, system) VALUES('03-03-438','02-01-001','warehouse')")
    agg = P.aggregate(con, [{"code": "03-03-438", "qty": 1, "unit": "กิโลกรัม", "value": 400}])
    assert "02-01-001" in agg["codes"]


def test_apply_carry_forward_age_and_manual_kept(con):
    P.apply(con, "2026-08", P.aggregate(con, LINES), "t")
    assert P.price_for(con, "02-01-001", "2026-08")[1] == "purchase_avg"
    # September: only rice is bought; salmon carries August's price, aged 1
    P.apply(con, "2026-09", P.aggregate(con, [LINES[2]]), "t")
    p = P.price_for(con, "02-01-001", "2026-09")
    assert p[1] == "carry_forward" and p[3] == 1 and p[0] == pytest.approx(0.38)
    # a month with no import at all: price_for still answers, aged 2
    assert P.price_for(con, "02-01-001", "2026-10")[3] == 2
    # a manual price survives a re-import of the same month
    P.set_manual(con, "02-01-001", "2026-09", 0.45, "ซัพพลายเออร์แจ้งขึ้นราคา", "t")
    res = P.apply(con, "2026-09", P.aggregate(con, LINES[:2]), "t")
    assert res["kept_manual"] == 1
    assert P.price_for(con, "02-01-001", "2026-09")[:2] == (0.45, "manual")
    with pytest.raises(ValueError):
        P.set_manual(con, "02-01-001", "2026-09", 0.5, "", "t")            # a reason is required
    pv = P.preview(con, "2026-10", P.aggregate(con, [{"code": "02-01-001", "qty": 1, "unit": "กิโลกรัม", "value": 600}]))
    row = pv["rows"][0]
    assert row["prev_price"] == pytest.approx(0.45) and row["flag"] == "flag" and row["change_pct"] > 25


def test_unpriced_ingredient_is_not_invented(con):
    P.apply(con, "2026-08", P.aggregate(con, LINES[:2]), "t")
    assert P.price_for(con, "CK-013", "2026-08") is None
    assert any(r["code"] == "CK-013" and r["price_per_base"] is None for r in P.month_table(con, "2026-08"))


# ── master ───────────────────────────────────────────────────────────────────
def test_seed_fills_blanks_and_never_overwrites(con):
    con.execute("CREATE TABLE IF NOT EXISTS ingredient_order_unit(ingredient_code TEXT PRIMARY KEY, base_unit TEXT,"
                " order_unit TEXT, qty_per_order REAL, price_per_order REAL, moq REAL, round_step REAL, note TEXT,"
                " source TEXT, updated_at TEXT)")
    con.executemany("INSERT INTO ingredient_order_unit VALUES(?,?,?,?,?,?,1,?, 'supplier-sheet', '')", [
        ("13-01-009", "กรัม", "แพ็ค 1 กก.", 1000, 150, 1, "ปูอัดซาชิมิ"),   # fills the missing pack size
        ("02-01-001", "กรัม", "แพ็ค 2 กก.", 2000, 740, 1, "แซลมอนเดิม"),  # must NOT overwrite 1000
        ("V-009", "กรัม", "ถุง 1 กก.", 1000, 120, 1, "ต้นหอมไทย")])        # new code
    con.execute("INSERT INTO bom_ingredients(sku_id, seq, ingredient_code, ingredient_name, qty_per_serving, unit,"
                " price_per_unit, ingredient_cost, ingredient_type) VALUES(2, 9, 'HK-006', 'ซุปมิโสะ', 150, 'มล.', 0.03, 4.5, 'sauce')")
    forced = {"items": [{"code": "03-03-438", "name": "แซลมอนแช่แข็ง", "recipe_codes": ["02-01-001"]}]}
    st = M.seed_from_db(con, forced=forced)
    row = lambda c: con.execute("SELECT qty_per_recv, name_th, kind, base_unit, monitored FROM bc_ingredient WHERE code=?", (c,)).fetchone()  # noqa: E731
    assert row("13-01-009")[0] == 1000
    assert row("02-01-001")[0] == 1000 and row("02-01-001")[1] == "แซลมอน"
    assert row("V-009") is not None
    assert row("HK-006")[2] == "prepared" and row("HK-006")[3] == "มิลลิลิตร"
    assert row("03-03-438")[4] == 1
    assert con.execute("SELECT ingredient_code FROM bc_ingredient_alias WHERE alias_code='03-03-438'").fetchone()[0] == "02-01-001"
    assert P.price_for(con, "V-009", "2026-07")[:2] == (0.12, "list_ref")
    again = M.seed_from_db(con, forced=forced)
    assert again["inserted"] == 0 and st["inserted"] >= 3


def test_import_datap_layout_overwrites_and_prices_reference(con):
    rows = [{"code": "02-01-001", "name": "ปลาแซลมอนเทร้านอร์เวย์", "base_unit": "กรัม", "qty_per_recv": 1000,
             "full_price": 371.45, "loss_pct": 40, "supplier": "01_DAIKI FOOD", "date": "2569-07-23 00:00:00"},
            {"code": "#N/A", "name": "x"},
            {"code": "S-099", "name": "เกลือใหม่ ไม่มี loss", "base_unit": "กรัม"},   # new code, blank loss -> default 0
            {"code": "V-007", "name": "หัวไชเท้า", "base_unit": "กรัม", "loss_pct": 160}]
    st = M.import_rows(con, rows, "t")
    r = con.execute("SELECT loss_pct, supplier, name_th FROM bc_ingredient WHERE code='02-01-001'").fetchone()
    assert r == (40.0, "01_DAIKI FOOD", "ปลาแซลมอนเทร้านอร์เวย์")
    assert P.price_for(con, "02-01-001", "2026-07")[0] == pytest.approx(0.37145)
    assert len(st["skipped"]) == 2                                           # #N/A and loss 160 %
    assert con.execute("SELECT loss_pct FROM bc_ingredient WHERE code='S-099'").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM bc_audit WHERE action='update'").fetchone()[0] >= 1


def test_family_and_kind():
    assert M.family_of("02-01-001") == "ACC" and M.family_of("CK-013") == "CK" and M.family_of("5130-04") == "NNNN"
    assert M.kind_of("DK-001") == "prepared" and M.kind_of("วัสดุสิ้นเปลือง-001") == "consumable"
    assert M.kind_of("V-009") == "raw"


# ── endpoints ────────────────────────────────────────────────────────────────
def _xlsx(rows, sheets=None):
    from openpyxl import Workbook
    wb = Workbook()
    if sheets:
        wb.remove(wb.active)
        for name, grid in sheets.items():
            ws = wb.create_sheet(name)
            for r in grid:
                ws.append(r)
    else:
        for r in rows:
            wb.active.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def test_endpoints_seed_import_prices_and_convert(client):
    import main
    c = sqlite3.connect(str(main.DB_PATH))
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bc%'")]
    snap = {t: c.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
    c.close()
    try:
        r = client.post("/api/bom-center/ingredients/seed?dry_run=false")
        assert r.status_code == 200, r.text
        data = _xlsx([["รหัส", "ชื่อวัตถุดิบ", "หน่วยฐาน", "หน่วยรับ", "ขนาดบรรจุ", "สูญเสีย (%)"],
                      ["ING-T1", "วัตถุดิบทดสอบ", "กรัม", "ถุง", 500, 10]])
        pre = client.post("/api/bom-center/ingredients/import?dry_run=true", files={"file": ("m.xlsx", data)}).json()
        assert pre["inserted"] == 1 and pre["dry_run"]
        assert client.get("/api/bom-center/ingredients?q=ING-T1").json()["rows"] == []      # dry run wrote nothing
        assert client.post("/api/bom-center/ingredients/import?dry_run=false", files={"file": ("m.xlsx", data)}).status_code == 200
        conv = client.get("/api/bom-center/convert", params={"code": "ING-T1", "qty": 2, "unit": "ถุง"}).json()
        assert conv["qty_base"] == 1000
        # the accounting workbook, exactly as it comes (title rows, month in the header, one sheet per store)
        book = _xlsx(None, sheets={"Use KR1": [
            [None, "บริษัท โอชิเน"], [None, "รายงานจ่ายสินค้าภายใน"], [None, "วันที่จาก  01/09/2569  ถึง  30/09/2569"],
            [None, "เอกสาร", "วันที่เอกสาร", "แผนก", "หมายเหตุ", "ลำดับ", "รหัสสินค้า", "รายละเอียด", "คลังที่", "จำนวน", "หน่วยนับ", "ราคาต่อหน่วย", "มูลค่ารวม"],
            [None, "OU6909001", None, None, None, None, None, None, None, None, None, None, None],
            [None, None, None, None, None, 1, "ING-T1", "วัตถุดิบทดสอบ", 1, 3, "ถุง", 50, 150],
            [None, None, None, None, None, 2, "ING-T1", "วัตถุดิบทดสอบ", 1, 2, "กิโลกรัม", 120, 240]]})
        pv = client.post("/api/bom-center/prices/import?dry_run=true", files={"file": ("u.xlsx", book)}).json()
        assert pv["month"] == "2026-09" and pv["source"]["format"] == "รายงานจ่ายสินค้าภายใน"
        row = [x for x in pv["rows"] if x["code"] == "ING-T1"][0]
        assert row["qty_base"] == 3500 and row["price_per_base"] == pytest.approx(390 / 3500, abs=1e-6)
        done = client.post("/api/bom-center/prices/import?dry_run=false", files={"file": ("u.xlsx", book)}).json()
        assert done["applied"]["written"] >= 1
        hist = client.get("/api/bom-center/prices/history?code=ING-T1").json()["rows"]
        assert hist[0]["source"] == "purchase_avg" and hist[0]["branches"]
        s = client.get("/api/bom-center/summary?month=2026-09").json()
        assert s["ingredients"] >= 1 and s["price_sources"].get("purchase_avg", 0) >= 1
        bad = client.post("/api/bom-center/prices/manual", json={"code": "ING-T1", "month": "2026-09", "price_per_base": 0.2})
        assert bad.status_code == 400                                         # no reason given
        assert client.post("/api/bom-center/prices/manual", json={"code": "ING-T1", "month": "2026-09",
                           "price_per_base": 0.2, "note": "ทดสอบ"}).status_code == 200
        assert client.get("/api/bom-center/prices/template.xlsx").status_code == 200
        assert client.get("/api/bom-center/ingredients/template.xlsx").status_code == 200
    finally:
        c = sqlite3.connect(str(main.DB_PATH))
        for t, rows in snap.items():
            c.execute(f"DELETE FROM {t}")
            if rows:
                c.executemany(f"INSERT INTO {t} VALUES({','.join('?' * len(rows[0]))})", rows)
        c.commit()
        c.close()
