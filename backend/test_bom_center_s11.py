"""BoM Center — Phase 2 · Sprint S11: the central list and a month closed after the fact.

The promises:
  * the centre's monthly report (ยอดสั่งสินค้าทุกสาขา) is read as it is: the unit column
    "กล20.00/PK" is a box of 20 packs, a quantity "2/39.00" is 2 boxes + 39 packs, the
    branch columns are matched to branches by the label under the code;
  * one sale unit is put into base units without guessing — the unit itself, a unit
    rule, kg→g, a pack size written in the name; the master's own pack only as a
    last resort and marked for a person to check;
  * the list decides sourcing: on it = ordered from the centre (page 1, sent to Sales
    Support, at the centre's price); off it = bought locally (page 2, never sent);
  * the month's quantities become one goods-received note per branch, re-importable;
  * a count can come from the template, dated when it was taken, so a month closes.
"""
import io
from datetime import date

import pytest
from openpyxl import Workbook

from bom_center import central as CE
from bom_center import orders as OR
from bom_center import stock as ST
from bom_center import variance as VA
from test_bom_center_s4b import con  # noqa: F401  (sales Sep–Oct 2026 at branch 1; crab 13-01-009 + salmon 02-01-001)

ITEMS = [  # code, name, unit label, branch qty, price, sale unit
    ("13-01-009", "ปูอัดซาชิมิ Kani Kamoboko 0.5Kg/Pack(20Pk)", "กล20.00/PK", "      2/10.00", 1500, "Box"),
    ("02-01-001", "ปลาแซลมอนเทร้านอร์เวย์ SalmonFresh (KG)", "KG1.00/KG", 120.5, 0, "KG"),
    ("16-01-021", "ผ้ากันเปื้อน สีดำ", "ชน1.00/ชน", 5, 137, "Pcs."),
    ("41-00-02", "ค่าส่งสินค้า", "ครั้ง", 2, 0, "ครั้ง"),
]


def _report(items=ITEMS, title="กันยายน 2026") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = title
    ws.append(["ลำดับ", "รหัสสินค้า", "รายละเอียด", "หน่วยนับ", "C1001", "C1099", "รหัสสินค้า2", "ราคาขาย", "หน่วยขาย"])
    ws.append([None, None, None, None, "อุดร", "ไม่มีสาขานี้"])
    ws.append([None, None, None, None, "ปริมาณขายสุทธิ", "ปริมาณขายสุทธิ"])
    for i, (code, name, unit, q, price, su) in enumerate(items, 1):
        ws.append([i, code, name, unit, q, "#N/A", code, price, su])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def udon(con):  # noqa: F811
    con.execute("UPDATE branches SET name='อุดรธานี' WHERE branch_id=1")
    CE.ensure(con)
    return con


def test_reading_the_report(udon):
    p = CE.parse_report(udon, _report())
    assert p["month"] == "2026-09"
    assert [(b["code"], b["branch_id"]) for b in p["branches"]] == [("C1001", 1), ("C1099", None)]
    crab = p["items"][0]
    assert (crab["outer"], crab["inner_qty"], crab["inner"]) == ("กล", 20.0, "PK")
    assert crab["qty"]["C1001"] == 2.5                       # 2 boxes + 10 of 20 packs
    assert crab["qty"]["C1099"] is None                      # #N/A = nothing delivered
    assert CE.parse_qty("13/8.00", 10) == 13.8 and CE.parse_qty(0, 10) is None
    assert CE._month_of("ก.ย. 2569") == "2026-09"


def test_the_list_sets_units_and_sourcing(udon):
    p = CE.parse_report(udon, _report())
    pl = {r["code"]: r for r in CE.plan_catalogue(udon, p)["rows"]}
    assert pl["13-01-009"]["base_per_sale"] == 10000.0 and "ชื่อสินค้า" in pl["13-01-009"]["factor_how"]
    assert pl["02-01-001"]["base_per_sale"] == 1000.0
    assert pl["16-01-021"]["is_new"] and pl["16-01-021"]["base_unit"] == "ชิ้น"
    assert pl["41-00-02"]["status"] == "skip"
    out = CE.apply_catalogue(udon, p, "ss")
    assert out["saved"] == 3 and out["inserted"] == 1
    m = {c: (r, q, s, k) for c, r, q, s, k in udon.execute(
        "SELECT code, recv_unit, qty_per_recv, sourcing_default, kind FROM bc_ingredient")}
    assert m["13-01-009"][:3] == ("กล่อง (20 แพ็ค)", 10000.0, "central")
    assert m["16-01-021"][3] == "consumable"
    assert all(v[2] == "local" for c, v in m.items() if c not in ("13-01-009", "02-01-001", "16-01-021"))
    rule = udon.execute("SELECT factor_to_base FROM bc_unit_rule WHERE ingredient_code='13-01-009' AND unit='แพ็ค'").fetchone()
    assert rule[0] == 500.0                                   # one pack of the box, for counts and receipts
    assert CE.items(udon)["count"] == 3
    assert CE.unit_price(udon, "13-01-009", "2026-09")[0] == pytest.approx(0.15)   # ฿1,500 / 10,000 g


def test_the_master_pack_is_a_last_resort_and_flagged(udon):
    udon.execute("UPDATE bc_ingredient SET qty_per_recv=6000, recv_unit='ลัง' WHERE code='13-01-009'")
    p = CE.parse_report(udon, _report([("13-01-009", "ปูอัด", "กล20.00/PK", 1, 1500, "Box")]))
    r = CE.plan_catalogue(udon, p)["rows"][0]
    assert r["base_per_sale"] == 6000.0 and r["factor_how"].startswith("จากทะเบียน") and "20" in r["factor_how"]
    p = CE.parse_report(udon, _report([("13-01-009", "ปูอัด", "กล20.00/BT", 1, 1500, "Box")]))
    udon.execute("UPDATE bc_ingredient SET qty_per_recv=NULL WHERE code='13-01-009'")
    r = CE.plan_catalogue(udon, p)["rows"][0]
    assert r["status"] == "needs_factor" and r["base_per_sale"] is None


def test_order_pages_central_and_local(udon):
    CE.apply_catalogue(udon, CE.parse_report(udon, _report(ITEMS[:1])), "ss")      # only the crab is central
    p = OR.plan(udon, 1, "1", date(2026, 10, 7), as_of=date(2026, 10, 5))           # Wednesday's delivery
    assert p["central_list"] and [r["code"] for r in p["rows"]] == ["13-01-009"]
    crab = p["rows"][0]
    assert crab["price_source"] == "central" and crab["price_per_sale"] == 1500
    assert crab["order_unit"] == "pack" and crab["qty_per_recv"] == 10000.0
    s = OR.plan(udon, 1, "salmon", date(2026, 10, 12), as_of=date(2026, 10, 5))     # Monday's salmon
    assert s["rows"] == [] and [r["code"] for r in s["local_rows"]] == ["02-01-001"]   # salmon: bought locally
    with pytest.raises(ValueError, match="ไม่อยู่ในบัญชีสินค้าส่วนกลาง"):
        OR.save(udon, 1, "1", p["order_day"], p, [{"code": "02-01-001", "confirmed_units": 1,
                                                    "reason_code": "event"}], "udon")


def test_deliveries_then_counts_close_the_month(udon):
    p = CE.parse_report(udon, _report())
    CE.apply_catalogue(udon, p, "ss")
    pre = CE.deliveries(udon, p, "ss", dry_run=True)
    b = {x["code"]: x for x in pre["branches"]}
    assert b["C1099"]["error"] and b["C1001"]["lines"] == 3 and pre["receipt_date"] == "2026-09-30"
    assert b["C1001"]["value_central"] == pytest.approx(2.5 * 1500 + 5 * 137)
    CE.deliveries(udon, p, "ss", dry_run=False)
    CE.deliveries(udon, p, "ss", dry_run=False)               # imported twice: still one note
    rec = VA.receipts(udon, 1)
    assert len(rec) == 1 and rec[0]["ref"] == "CENTRAL-2026-09" and rec[0]["source"] == "central"
    assert {ln["code"]: ln["qty_base"] for ln in rec[0]["lines"]}["13-01-009"] == 25000.0

    def sheet(rows):
        wb = Workbook()
        ws = wb.active
        ws.append(["นับสต๊อก …"])
        ws.append(["โซน", "รหัส", "ชื่อ", "หน่วยนับ", "จำนวนที่นับ", "หมายเหตุ"])
        for r in rows:
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        return ST.read_count_file(buf.getvalue())
    rows = [t["code"] for t in ST.template_rows(udon, 1)]
    assert "13-01-009" in rows and "16-01-021" not in rows    # the list is counted, aprons are not
    begin = sheet([["แช่แข็ง", "13-01-009", "ปูอัด", "แพ็ค", 10, None], ["แช่เย็น", "02-01-001", "แซลมอน", "กิโลกรัม", 5, None],
                   ["", "13-01-009x", "", "", None, None]])
    assert len(begin) == 2                                    # a row without a quantity is not counted
    bad = ST.import_count(udon, 1, "2026-09-01", begin + [{"code": "XX-1", "qty": 1, "unit": "", "note": None}], "ss")
    assert not bad["ok"] and bad["unknown"] == ["XX-1"]
    assert ST.import_count(udon, 1, "2026-09-01", begin, "ss")["ok"]          # dry run writes nothing
    assert not ST.history(udon, 1)
    a = ST.import_count(udon, 1, "2026-09-01", begin, "ss", dry_run=False)
    assert a["kind"] == "begin" and a["status"] == "submitted"
    with pytest.raises(ValueError, match="นำเข้าซ้ำไม่ได้"):
        ST.import_count(udon, 1, "2026-09-01", begin, "ss", dry_run=False)
    end = sheet([["", "13-01-009", "", "แพ็ค", 4, None], ["", "02-01-001", "", "กิโลกรัม", 3, None]])
    e = ST.import_count(udon, 1, "2026-10-01", end, "ss", dry_run=False)
    v = VA.period(udon, 1, e["count_id"])
    crab = {r["code"]: r for r in v["rows"]}["13-01-009"]
    assert (v["begin_date"], v["end_date"], v["receipts"]) == ("2026-09-01", "2026-10-01", 1)
    assert crab["begin"] == 5000.0 and crab["received"] == 25000.0 and crab["end"] == 2000.0
    assert crab["actual"] == 28000.0 and crab["bom"] > 0
    with pytest.raises(ValueError, match="งวดที่ปิดแล้ว"):                          # the month is closed now
        VA.delete_receipt(udon, 1, rec[0]["receipt_id"], "ss", "ทดสอบ")
    again = CE.deliveries(udon, p, "ss", dry_run=True)
    assert "แทนที่ใบรับไม่ได้" in {x["code"]: x for x in again["branches"]}["C1001"]["error"]


def test_a_recipes_older_code_is_counted_as_the_one_delivered(udon):
    # the centre now sends crab stick as 13-01-777; the recipes still say 13-01-009
    p = CE.parse_report(udon, _report([("13-01-777", "ปูอัด Kani Kamaboko 0.5Kg/Pack(20Pk)", "กล20.00/PK", 3, 1500, "Box")]))
    CE.apply_catalogue(udon, p, "ss")
    v = CE.link_view(udon)
    assert v["suggestions"][0]["old_code"] == "13-01-009"
    assert v["suggestions"][0]["options"][0]["new_code"] == "13-01-777"
    before = OR.plan(udon, 1, "1", date(2026, 10, 7), as_of=date(2026, 10, 5))
    assert before["rows"] == [] and [r["code"] for r in before["local_rows"]] == ["13-01-009"]
    CE.set_link(udon, "13-01-009", "13-01-777", "ss")
    after = OR.plan(udon, 1, "1", date(2026, 10, 7), as_of=date(2026, 10, 5))
    assert [r["code"] for r in after["rows"]] == ["13-01-777"]                    # ordered from the centre now
    assert after["rows"][0]["forecast"] == pytest.approx(before["local_rows"][0]["forecast"])
    assert CE.link_view(udon)["links"][0]["new_code"] == "13-01-777"
    with pytest.raises(ValueError, match="ต่อเนื่อง"):
        CE.set_link(udon, "02-01-001", "13-01-009", "ss")
    with pytest.raises(ValueError, match="รหัสเดียวกัน"):
        CE.set_link(udon, "02-01-001", "02-01-001", "ss")
    CE.set_link(udon, "13-01-009", None, "ss")
    assert CE.links(udon) == {}
