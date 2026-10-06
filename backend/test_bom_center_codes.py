"""BoM Center — the code check for the old recipes (bom_ingredients) against the master.

The promises:
  * an empty master never produces a wall of "missing" codes to work through by hand —
    seeding adds them, and only real problems are left to look at;
  * the old recipes reuse one code under several names (CK-001 = rice in 113 dishes and
    น้ำซุปใส in 2), so everything is judged per (code, name): a name that describes a
    different item than the master's is flagged, with the best-fitting master code
    suggested (หัวไชเท้า → V-007), and names that already fit are never moved;
  * names that only differ in wording (prep words, pack size, งาดํา/งาดำ) are not flagged;
  * the analyst's "use X instead" reaches the order plans (new forecast and the old
    แผนสั่งวัตถุดิบ) but never costs;
  * a bad decision (unknown code, remap to itself, no code, nothing to decide) is refused.
"""
import sqlite3
from datetime import date, timedelta

import pytest

from bom_center import codes as CR
from bom_center import forecast as F
from test_bom_center_s2 import _db


@pytest.fixture
def con(tmp_path):
    c = _db(tmp_path)
    c.execute("INSERT INTO bc_ingredient(code, name_th, kind, base_unit) VALUES('V-024', 'มะนาวแป้น 50 ลูก', 'raw', 'กรัม')")
    c.executemany("INSERT INTO bom_ingredients(sku_id, seq, ingredient_code, ingredient_name, qty_per_serving, unit,"
                  " price_per_unit, ingredient_cost, ingredient_type) VALUES(?,?,?,?,?,?,?,?,?)", [
                      (10, 1, "13-01-009", "ปูอัด ใช้ 3แท่ง หั่นครึ่ง", 45, "กรัม", 0.15, 6.75, "protein"),
                      (10, 2, "V-024", "หัวไชเท้าหั่นฝอย", 10, "กรัม", 0.04, 0.4, "veg"),
                      (11, 1, "V-024", "หัวไชเท้าหั่นฝอย", 5, "กรัม", 0.04, 0.2, "veg"),
                      (11, 2, "V-024", "มะนาวแป้น", 7, "กรัม", 0.04, 0.3, "veg"),      # the code's real meaning
                      (11, 3, "NEW-1", "ของใหม่ที่ยังไม่มีรหัส", 1, "กรัม", 1, 1, "other")])
    c.commit()
    yield c
    c.close()


def _row(con, code):
    return {r["code"]: r for r in CR.review(con, with_ok=True)["rows"]}[code]


def test_review_flags_only_the_names_that_mean_another_item(con):
    d = CR.review(con)
    by = {r["code"]: r for r in d["rows"]}
    assert d["used"] == 3
    assert d["counts"] == {"missing": 1, "conflict": 1, "same": 0, "remap": 0, "ok": 1}
    v = by["V-024"]
    assert v["status"] == "conflict" and v["dishes"] == 2
    assert v["todo_names"] == ["หัวไชเท้าหั่นฝอย"] and v["todo_dishes"] == 2
    assert {n["name"]: n["status"] for n in v["names"]} == {"หัวไชเท้าหั่นฝอย": "conflict", "มะนาวแป้น": "ok"}
    assert v["suggest"][0]["code"] == "V-007"                              # หัวไชเท้า
    assert by["NEW-1"]["status"] == "missing"
    assert "13-01-009" not in by                                           # ok codes hidden by default
    assert d["rows"][0]["code"] == "V-024"                                 # problems first
    assert _row(con, "13-01-009")["status"] == "ok"


@pytest.mark.parametrize("recipe, master", [
    ("ปูอัด ใช้ 3แท่ง หั่นครึ่ง /แท่งละ10กรัม", "ปูอัดซาชิมิ Kani Kamoboko Grad A 0.5Kg/Pack(20Pk)"),
    ("หอมไทยซอย", "ต้นหอมไทย ถุงใหญ่ 1 กก."),
    ("งาดำ", "เอโร่ งาดํา 500 ก. x 6"),
    ("แซลมอน", "ปลาแซลมอนเทร้านอร์เวย์ 4-5 KG/PCS SalmonFresh (KG)"),
    ("anything", ""),
])
def test_wording_differences_are_not_flagged(recipe, master):
    assert CR.same_item(recipe, master)


@pytest.mark.parametrize("recipe, master", [
    ("หัวไชเท้าหั่นฝอย", "มะนาวแป้น 50 ลูก"),
    ("น้ำซุปใส", "ข้าวสวยญี่ปุ่นปรุงรส CK-001"),
    ("เกลือป่น", "ไร่ทิพย์ ข้าวคั่ว 500 ก."),
])
def test_different_items_are_flagged(recipe, master):
    assert not CR.same_item(recipe, master)


def test_decisions_are_validated_and_reversible(con):
    with pytest.raises(ValueError):
        CR.decide(con, "V-024", "remap", None)                 # no target
    with pytest.raises(ValueError):
        CR.decide(con, "V-024", "remap", "V-024")              # itself
    with pytest.raises(ValueError):
        CR.decide(con, "V-024", "remap", "NOPE-1")             # not in the master
    with pytest.raises(ValueError):
        CR.decide(con, "V-024", "maybe")
    with pytest.raises(ValueError):
        CR.decide(con, "13-01-009", "same")                    # nothing to decide there
    out = CR.decide(con, "V-024", "remap", "V-007", "สูตรเดิมใช้รหัสมะนาว", who="analyst1")
    assert out["names"] == ["หัวไชเท้าหั่นฝอย"]                 # only the flagged name
    r = _row(con, "V-024")
    n = {x["name"]: x for x in r["names"]}["หัวไชเท้าหั่นฝอย"]
    assert r["status"] == "remap" and n["use_code"] == "V-007" and n["use_name"] == "หัวไชเท้า" and n["who"] == "analyst1"
    assert CR.remaps(con) == {("V-024", "หัวไชเท้าหั่นฝอย"): "V-007"}
    CR.decide(con, "V-024", "same", names=["หัวไชเท้าหั่นฝอย"])
    assert CR.remaps(con) == {} and _row(con, "V-024")["status"] == "same"
    CR.decide(con, "V-024", "clear")
    assert _row(con, "V-024")["status"] == "conflict"
    assert con.execute("SELECT COUNT(*) FROM bc_audit WHERE table_name='bc_code_review'").fetchone()[0] == 3


def test_remap_reaches_the_forecast_not_the_costs(con):
    per, _ = F._per_dish(con, "2026-10")
    assert {code for code, _ in per[10]} == {"13-01-009", "V-024"}
    CR.decide(con, "V-024", "remap", "V-007")
    per, stats = F._per_dish(con, "2026-10")
    assert {code for code, _ in per[10]} == {"13-01-009", "V-007"}
    assert sorted(code for code, _ in per[11]) == ["V-007", "V-024"]   # the lime line stays V-024 (NEW-1: not countable)
    assert stats["remapped_codes"] == 1
    # the old recipe rows themselves (what costs read) are untouched
    assert con.execute("SELECT COUNT(*) FROM bom_ingredients WHERE ingredient_code='V-024'").fetchone()[0] == 3


def test_remap_reaches_the_old_order_plan(con):
    from routers.ingredients import _code_remap_sql, _ing_consumed
    bname = con.execute("SELECT name FROM branches WHERE branch_id=1").fetchone()[0]
    d0 = date(2026, 9, 1)
    con.executemany("INSERT INTO sales_sku_daily VALUES(?,?,?,?,?)",
                    [(1, (d0 + timedelta(days=i)).isoformat(), sku, 4, 0.0) for i in range(3) for sku in (10, 11)])
    con.execute("CREATE VIEW IF NOT EXISTS v_sales_sku_daily AS SELECT * FROM sales_sku_daily")
    con.commit()
    con.row_factory = sqlite3.Row
    used = _ing_consumed(con, bname, "2026-09-01", "2026-09-03")
    assert used[("V-024", "กรัม")] == 3 * 4 * (10 + 5 + 7)
    CR.decide(con, "V-024", "remap", "V-007")
    used = _ing_consumed(con, bname, "2026-09-01", "2026-09-03")
    assert used[("V-007", "กรัม")] == 3 * 4 * (10 + 5)          # radish lines moved
    assert used[("V-024", "กรัม")] == 3 * 4 * 7                 # the lime line did not
    con.execute("DROP TABLE bc_code_review")
    assert _code_remap_sql(con) == ("", "bi.ingredient_code")    # an older DB: no-op
