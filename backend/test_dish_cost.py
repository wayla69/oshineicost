# -*- coding: utf-8 -*-
"""PR-S2 - ต้นทุนต่อจานรายเดือน: the analyst imports the dish cost, month by month.

The promises (owner 2026-10-22, docs/STOCK_FIRST_PLAN.md O2 and O5):
  * per SKU the imported list beats the released recipe cost, with the same "latest month <=" rule, and
    nothing before the Grand Menu month (2026-09) ever changes; an empty list changes no number;
  * the SQL (rules.dated_cost_sql) and the Python readers (dated.costs_for) give the same answer;
  * a buffet package code has no cost per plate: refused at import;
  * the preview counts what would happen (unknown codes, bad values, moves over 30%, menus sold without a cost);
  * every import and edit is audited; the table survives the daily swap and the PC sync pulls it by code;
  * who may read and who may write; the write needs the owner token, a preview does not;
  * หน้างาน carries the task until no menu is sold without a cost, in stock mode too.
"""
import io
import json
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

import auth
import rules
from bom_center import dated as D
from bom_center import dish_cost as DC
from bom_center import todo as TD
from test_bom_center_s2 import _db

TIER = rules.ALL_TIER_CODES[0]
TOKEN = {"X-Upload-Token": "owner-token-xyz"}

# sku_id, code, description, sku_cost
DISHES = [
    (9001, "DCT-A", "เมนู เอ", 10.0),        # an old-chain cost only
    (9002, "DCT-B", "เมนู บี", None),        # no cost anywhere
    (9003, "DCT-C", "เมนู ซี", None),        # a released cost (20 from 2026-09)
    (9004, "DCT-D", "เมนู ดี", 8.0),
]


@pytest.fixture
def con(tmp_path):
    c = _db(tmp_path)
    for sid, code, desc, cost in DISHES:
        c.execute("INSERT INTO skus(sku_id, sku_code, description, category_id, unit, business_type, active)"
                  " VALUES(?,?,?,3,'plate','alacarte',1)", (sid, code, desc))
        if cost is not None:
            c.execute("INSERT INTO sku_cost(sku_id, unit_cost) VALUES(?,?)", (sid, cost))
    c.execute("INSERT OR IGNORE INTO skus(sku_id, sku_code, description, category_id, unit, business_type, active)"
              " VALUES(9100, ?, 'แพ็กเกจ', 3, 'pkg', 'buffet', 1)", (TIER,))
    c.execute("INSERT INTO bc_sku_cost_month(sku_id, month, unit_cost, release_id) VALUES(9003, '2026-09', 20.0, 1)")
    tier_id = c.execute("SELECT sku_id FROM skus WHERE sku_code=?", (TIER,)).fetchone()[0]
    for sid, qty in ((9001, 100), (9002, 40), (9003, 30), (9004, 5), (tier_id, 500)):
        c.execute("INSERT INTO sales_sku_daily VALUES(1, '2026-10-05', ?, ?, 0)", (sid, qty))
        c.execute("INSERT INTO sales_sku_daily VALUES(1, '2026-09-05', ?, ?, 0)", (sid, qty))
    c.commit()
    yield c
    c.close()


def _eff(con, sid, month):
    return con.execute(f"SELECT {rules.effective_unit_cost_sql('ss', repr(month))} FROM skus ss WHERE ss.sku_id=?",
                       (sid,)).fetchone()[0]


def _rows(*items):
    return [{"code": c, "name": "", "cost": v, "note": ""} for c, v in items]


def _xlsx(grid, sheet="Sheet1") -> bytes:
    wb = Workbook()
    wb.active.title = sheet
    for r in grid:
        wb.active.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


# ── precedence ───────────────────────────────────────────────────────────────
def test_an_empty_list_changes_no_number(con):
    assert con.execute("SELECT COUNT(*) FROM bc_sku_cost_list").fetchone()[0] == 0
    assert _eff(con, 9003, "2026-09") == 20.0 and _eff(con, 9003, "2026-10") == 20.0     # the release, as before
    assert _eff(con, 9001, "2026-10") == 10.0 and _eff(con, 9002, "2026-10") == 0
    mm = D.month_map(con)
    assert D.costs_for(mm, "2026-10") == {9003: 20.0}
    assert dict(mm) == {"2026-09": {9003: 20.0}} and mm.list == {}


def test_the_list_beats_the_release_per_sku_and_carries_forward(con):
    DC.commit(con, "2026-09", _rows(("DCT-C", 25), ("DCT-A", 12)), "analyst1")
    assert _eff(con, 9003, "2026-09") == 25.0                  # the list beats the release
    assert _eff(con, 9003, "2026-10") == 25.0                  # October has no import: September carries
    assert _eff(con, 9001, "2026-09") == 12.0 and _eff(con, 9004, "2026-10") == 8.0   # a SKU the list lacks keeps its chain
    DC.commit(con, "2026-10", _rows(("DCT-C", 30)), "analyst1")
    assert _eff(con, 9003, "2026-09") == 25.0 and _eff(con, 9003, "2026-10") == 30.0
    assert _eff(con, 9001, "2026-10") == 12.0                  # per SKU: A carries from September
    # the Python readers say the same as the SQL
    mm = D.month_map(con)
    for month in ("2026-09", "2026-10", "2026-11"):
        py = D.costs_for(mm, month)
        for sid in (9001, 9002, 9003, 9004):
            sql = _eff(con, sid, month)
            assert py.get(sid, sql) == pytest.approx(sql), (month, sid)
    assert D.costs_for(mm, "2026-11")[9003] == 30.0 and D.costs_for(mm, "2026-09")[9003] == 25.0


def test_nothing_before_the_grand_menu_month_moves(con):
    con.execute("INSERT INTO bc_sku_cost_list(sku_id, month, unit_cost) VALUES(9001, '2026-08', 99.0)")   # a stray row, written around the checks
    assert _eff(con, 9001, "2026-08") == 10.0                  # the old chain, never even looked up
    assert D.costs_for(D.month_map(con), "2026-08") == {}
    q = f"SELECT SUM({rules.cogs_expr('ssm')}) FROM sales_sku_monthly ssm WHERE ssm.sku_id=9001 AND ssm.period_month=?"
    con.execute("INSERT INTO sales_sku_monthly(branch_id, period_month, sku_id, qty, subtotal_ex_vat, tax_total, total_inc_vat, source)"
                " VALUES(1,'2026-08',9001,3,0,0,0,'Easy'),(1,'2026-09',9001,3,0,0,0,'Easy')")
    assert con.execute(q, ("2026-08",)).fetchone()[0] == pytest.approx(30.0)
    assert con.execute(q, ("2026-09",)).fetchone()[0] == pytest.approx(3 * 99.0)    # the stray August row carries into September


def test_a_release_rebuild_never_touches_the_list(con):
    DC.commit(con, "2026-09", _rows(("DCT-A", 12)), "analyst1")
    D.rebuild(con)
    assert con.execute("SELECT sku_id, month, unit_cost FROM bc_sku_cost_list").fetchall() == [(9001, "2026-09", 12.0)]


# ── reading the file ─────────────────────────────────────────────────────────
def test_parse_the_template_and_the_grand_menu_layout():
    t = DC.parse(_xlsx([["หมายเหตุของไฟล์"], DC.HEADERS, ["DCT-A", "เมนู เอ", 11.5, "ปรับราคา"], ["DCT-B", "เมนู บี", None, ""],
                        ["DCT-C", "เมนู ซี", "abc", ""]], sheet=DC.SHEET), "x.xlsx")
    assert t["format"] == "แม่แบบต้นทุนต่อจาน"
    assert [(r["code"], r["cost"], r["note"]) for r in t["rows"]] == [("DCT-A", 11.5, "ปรับราคา"), ("DCT-C", "abc", "")]   # a blank cost is not provided
    gm = DC.parse(_xlsx([[], [None, None, None, None, None, None, "U"], [],
                         [None, "ลำดับที่", "New Item Code AL", "New Item Code BF", "ชื่อไทย", "ชื่ออังกฤษ", "ต้นทุน BF", "ต้นทุน AL"],
                         [None, 1, "OTHALA", "OTHBFA", "จาน ก", "A", 100.0, 120.0],
                         [None, 2, "OTHALB", None, "จาน ข", "B", None, 2.9],
                         [None, 3, "OTHBFC", None, "จาน ค", "C", None, 6.32]]), "BOM Grand Menu.xlsx")
    assert gm["format"] == "BOM Grand Menu"
    got = {r["code"]: r["cost"] for r in gm["rows"]}
    assert got == {"OTHALA": 120.0, "OTHBFA": 100.0, "OTHALB": 2.9, "OTHBFC": 6.32}        # AL and BF each its own cost
    csv = "รหัส POS,ชื่อเมนู,ต้นทุนต่อจาน (บาท),หมายเหตุ\nDCT-A,เอ,9.5,\n".encode("utf-8")
    assert DC.parse(csv, "a.csv")["rows"][0]["cost"] == "9.5"
    with pytest.raises(ValueError):
        DC.parse(_xlsx([["a", "b"], [1, 2]]), "bad.xlsx")


# ── preview, commit, edit ────────────────────────────────────────────────────
def test_a_package_code_has_no_cost_per_plate(con):
    pre = DC.preview(con, "2026-10", _rows((TIER, 5), ("BUFFETKIDS", 5), ("DCT-A", 10)))
    assert pre["tier_codes"] == [TIER, "BUFFETKIDS"] and pre["imported"] == 1      # the BUFFET* prefix is a package too, SKU or not
    assert pre["unknown_codes"] == []
    with pytest.raises(ValueError, match="รหัสแพ็กเกจบุฟเฟ่ต์ไม่มีต้นทุนต่อจาน"):
        DC.set_one(con, "2026-10", TIER, 5, "", "analyst1")
    DC.commit(con, "2026-10", _rows((TIER, 5), ("DCT-A", 10)), "analyst1")
    assert con.execute("SELECT COUNT(*) FROM bc_sku_cost_list").fetchone()[0] == 1


def test_preview_counts(con):
    pre = DC.preview(con, "2026-10", _rows(("DCT-A", 20), ("DCT-C", 21), ("DCT-D", 8), ("NOPE", 1), ("DCT-B", -3), ("DCT-A", 20)))
    assert pre["imported"] == 3 and pre["unknown_codes"] == ["NOPE"]
    assert [b["code"] for b in pre["bad_values"]] == ["DCT-B"] and pre["bad_values"][0]["why"] == "ต้นทุนต้องเป็นตัวเลขไม่ติดลบ"
    # A moves 10 -> 20 (+100%); C 20 -> 21 (+5%); D 8 -> 8
    assert [(c["code"], c["old"], c["new"], c["pct"]) for c in pre["changed_30pct"]] == [("DCT-A", 10.0, 20.0, 100.0)]
    # B (40 plates, no cost) is still without a list row: the one menu sold without a cost the file does not fix
    sw = {r["code"]: r for r in pre["sold_without_cost"]}
    assert set(sw) == {"DCT-B"} and sw["DCT-B"]["plates"] == 40 and sw["DCT-B"]["current_cost"] == 0
    assert sw["DCT-B"]["current_source"] == "ไม่มีต้นทุน"
    # nothing was written by a preview
    assert con.execute("SELECT COUNT(*) FROM bc_sku_cost_list").fetchone()[0] == 0
    # the package line is never a menu sold without a cost
    assert TIER not in sw
    pre2 = DC.preview(con, "2026-10", [])
    assert {r["code"] for r in pre2["sold_without_cost"]} == {"DCT-A", "DCT-B", "DCT-C", "DCT-D"}
    assert {r["code"]: r["current_source"] for r in pre2["sold_without_cost"]}["DCT-C"] == "สูตรที่ประกาศใช้"


def test_month_and_value_rules(con):
    with pytest.raises(ValueError, match="ต้นทุนต่อจานเริ่มได้ตั้งแต่เดือน 2026-09"):
        DC.preview(con, "2026-08", [])
    with pytest.raises(ValueError, match="เดือนไม่ถูกต้อง"):
        DC.preview(con, "2026-9", [])
    with pytest.raises(ValueError, match="ต้นทุนต้องเป็นตัวเลขไม่ติดลบ"):
        DC.set_one(con, "2026-10", "DCT-A", -1, "", "analyst1")
    with pytest.raises(ValueError, match="ต้นทุนต้องเป็นตัวเลขไม่ติดลบ"):
        DC.set_one(con, "2026-10", "DCT-A", "สิบ", "", "analyst1")
    with pytest.raises(ValueError, match="ไม่มีรายการที่นำเข้าได้"):
        DC.commit(con, "2026-10", _rows(("NOPE", 1)), "analyst1")
    DC.set_one(con, "2026-10", "DCT-D", 0, "", "analyst1", confirm_zero=True)   # zero is a number >= 0, written only when confirmed (PL1)


def test_commit_and_edit_are_audited_and_upsert(con):
    DC.commit(con, "2026-10", _rows(("DCT-A", 11), ("DCT-D", 9)), "analyst1")
    DC.commit(con, "2026-10", _rows(("DCT-A", 13)), "analyst2")
    assert con.execute("SELECT unit_cost, imported_by FROM bc_sku_cost_list WHERE sku_id=9001").fetchone() == (13.0, "analyst2")
    assert con.execute("SELECT COUNT(*) FROM bc_sku_cost_list").fetchone()[0] == 2
    DC.set_one(con, "2026-10", "DCT-A", 14.5, "ปรับตามใบเสนอราคา", "analyst1")
    assert con.execute("SELECT unit_cost, note FROM bc_sku_cost_list WHERE sku_id=9001").fetchone() == (14.5, "ปรับตามใบเสนอราคา")
    aud = con.execute("SELECT who, action, row_key, old_value, new_value FROM bc_audit WHERE action LIKE 'dish_cost_%' ORDER BY id").fetchall()
    assert aud == [("analyst1", "dish_cost_import", "2026-10", None, "2 รายการ"),
                   ("analyst2", "dish_cost_import", "2026-10", None, "1 รายการ"),
                   ("analyst1", "dish_cost_edit", "2026-10:DCT-A", "13", "14.5")]


def test_month_table_and_its_sources(con):
    DC.commit(con, "2026-09", _rows(("DCT-A", 12)), "analyst1")
    DC.commit(con, "2026-10", _rows(("DCT-D", 9)), "analyst1")
    t = DC.month_table(con, "2026-10")
    by = {r["code"]: r for r in t["rows"]}
    assert TIER not in by
    assert by["DCT-D"]["source"] == "นำเข้า" and by["DCT-D"]["unit_cost"] == 9.0 and by["DCT-D"]["prev_cost"] == 8.0
    assert by["DCT-A"]["source"] == "ยกมาจากเดือน 2026-09" and by["DCT-A"]["unit_cost"] == 12.0 and by["DCT-A"]["change_pct"] == 0.0
    assert by["DCT-B"]["source"] == "ไม่มีในตาราง (ใช้ต้นทุนเดิม)" and by["DCT-B"]["plates"] == 40
    assert by["DCT-C"]["source"] == "ไม่มีในตาราง (ใช้ต้นทุนเดิม)" and by["DCT-C"]["unit_cost"] == 20.0     # the app's number today
    assert by["DCT-A"]["change_pct"] == 0.0 and by["DCT-D"]["change_pct"] == 12.5
    assert t["counts"] == {"all": 4, "imported": 1, "carried": 1, "none": 2, "changed30": 0}
    assert [r["source_kind"] for r in t["rows"]][:2] == ["none", "none"]                       # the gaps first
    assert t["last_import_by"] == "analyst1"
    ms = DC.months(con, "2026-11")
    assert [m["month"] for m in ms] == ["2026-09", "2026-10", "2026-11"] and [m["listed"] for m in ms] == [1, 1, 0]


def test_the_template_lists_last_months_menus_with_the_cost_in_use(con):
    rows = DC.template_rows(con, "2026-10")
    assert [r[0] for r in rows] == ["DCT-A", "DCT-B", "DCT-C", "DCT-D"]
    assert {r[0]: r[2] for r in rows} == {"DCT-A": 10.0, "DCT-B": None, "DCT-C": 20.0, "DCT-D": 8.0}


# ── หน้างาน ───────────────────────────────────────────────────────────────────
def test_the_task_stays_until_every_sold_menu_has_a_cost(con):
    it = TD._dish_cost_item(con, "2026-10")[0]
    assert it["key"] == "dish_cost_month" and it["status"] == "todo" and it["count"] == 4
    assert it["title"] == "นำเข้าต้นทุนต่อจานเดือน" + TD._mth("2026-10") + " (เมนูที่ขายแต่ไม่มีต้นทุน 4)"
    assert it["owner_modules"] == ["bom.center"] and it["owner_th"] == "ฝ่ายวิเคราะห์"
    assert it["go"] == {"tab": "bomcenter", "pane": "dishcost", "month": "2026-10"}
    DC.commit(con, "2026-10", _rows(("DCT-A", 11), ("DCT-B", 4), ("DCT-C", 21)), "analyst1")
    assert TD._dish_cost_item(con, "2026-10")[0]["count"] == 1
    DC.set_one(con, "2026-10", "DCT-D", 9, "", "analyst1")
    it = TD._dish_cost_item(con, "2026-10")[0]
    assert it["status"] == "done" and it["detail"].startswith("ครบ · นำเข้าล่าสุด") and "analyst1" in it["detail"]
    assert TD._dish_cost_item(con, "2026-08") == []                    # before the Grand Menu month: nothing to ask
    assert "dish_cost_month" not in TD.RECIPE_KEYS                     # not a recipe item: it stays in stock mode
    items = TD.build(con, "2026-10", today=date(2026, 10, 22))["items"]
    assert any(i["key"] == "dish_cost_month" for i in items)


# ── the daily swap and the PC sync ───────────────────────────────────────────
def test_the_list_is_a_preserved_table(tmp_path, con):
    import core
    import main
    names = {r[0] for r in con.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND {core._preserved_pred('name')}")}
    assert "bc_sku_cost_list" in names and "bc_sku_cost_month" in names and "bcd_cost_cache" not in names
    DC.commit(con, "2026-10", _rows(("DCT-A", 11)), "analyst1")
    con.commit()
    live = tmp_path / "live.db"
    inc = tmp_path / "inc.db"
    b = sqlite3.connect(str(live))
    con.backup(b)
    b.close()
    sqlite3.connect(str(inc)).close()
    main._carry_forward_preserved(str(live), str(inc))
    c2 = sqlite3.connect(str(inc))
    assert c2.execute("SELECT sku_id, month, unit_cost, imported_by FROM bc_sku_cost_list").fetchall() == [(9001, "2026-10", 11.0, "analyst1")]
    c2.close()


def test_pull_from_live_mirrors_both_tables_by_code_and_never_raises(tmp_path, monkeypatch, con):
    DC.commit(con, "2026-09", _rows(("DCT-A", 12), ("DCT-C", 25)), "analyst1")
    rel = D.export_rows(con)
    lst = D.export_list_rows(con)
    assert {r[0] for r in lst} == {"DCT-A", "DCT-C"} and lst[0][2:] == [12.0, None, "analyst1", lst[0][5]]
    sq = sqlite3.connect(str(tmp_path / "staging.db"))                         # different sku ids on the other side
    sq.executescript("CREATE TABLE skus(sku_id INTEGER PRIMARY KEY, sku_code TEXT);"
                     "INSERT INTO skus VALUES (77, 'DCT-A'), (78, 'DCT-C');")
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "backup:tok")
    monkeypatch.delenv("OSHINEI_SYNC_ON_LIVE", raising=False)
    seen = []

    class _Resp:
        def __init__(self, body): self.body = body
        def read(self): return self.body
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake(req, timeout=0):
        seen.append(req.full_url)
        rows = lst if req.full_url.endswith("bc-sku-cost-list") else rel
        return _Resp(json.dumps({"rows": rows}).encode())
    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert D.pull_from_live(sq, api="http://x") == len(rel)
    assert [u.rsplit("/", 1)[1] for u in seen] == ["bc-sku-cost-month", "bc-sku-cost-list"]
    assert sq.execute("SELECT sku_id, month, unit_cost FROM bc_sku_cost_list ORDER BY sku_id").fetchall() == [(77, "2026-09", 12.0), (78, "2026-09", 25.0)]
    mm = D.month_map(sq)
    assert D.costs_for(mm, "2026-10")[78] == 25.0 and bool(mm)

    def boom(req, timeout=0):
        raise OSError("offline")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert D.pull_from_live(sq, api="http://x") == 0
    assert sq.execute("SELECT COUNT(*) FROM bc_sku_cost_list").fetchone()[0] == 2        # and nothing wiped

    def old_deploy(req, timeout=0):                                                     # an older deploy: no list route
        if req.full_url.endswith("bc-sku-cost-list"):
            raise OSError("404")
        return _Resp(json.dumps({"rows": rel}).encode())
    monkeypatch.setattr("urllib.request.urlopen", old_deploy)
    assert D.pull_from_live(sq, api="http://x") == len(rel)
    assert sq.execute("SELECT COUNT(*) FROM bc_sku_cost_list").fetchone()[0] == 2        # the list stays as it was


def test_the_fact_build_prices_a_month_with_the_list():
    """build_menu_fact / sync_mssql read dated.month_map + costs_for: a list-only database still counts as "has dated costs"."""
    mm = D.CostMap()
    mm.list = {"2026-09": {1: 7.0}}
    assert bool(mm) and D.costs_for(mm, "2026-10") == {1: 7.0} and D.costs_for(mm, "2026-08") == {}


# ── over HTTP: who may do what ───────────────────────────────────────────────
@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "owner-token-xyz")
    yield


def _login(user):
    import main
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": user, "password": auth.DEFAULT_PASSWORD}).status_code == 200
    return c


@pytest.fixture
def web(client, app_db):
    client.get("/api/bom-center/hub/queue")                            # creates the bc_* tables
    c = sqlite3.connect(str(app_db))
    c.execute("INSERT OR REPLACE INTO skus(sku_id, sku_code, description, category_id, unit, business_type, active)"
              " VALUES(9501, 'DCW-1', 'เมนูทดสอบเว็บ', 3, 'plate', 'alacarte', 1)")
    c.execute("INSERT INTO sales_sku_daily VALUES(1, '2026-10-06', 9501, 12, 0)")
    c.commit()
    c.close()
    yield app_db
    c = sqlite3.connect(str(app_db))
    c.execute("DELETE FROM bc_sku_cost_list")
    c.execute("DELETE FROM bc_audit WHERE action LIKE 'dish_cost_%'")
    c.execute("DELETE FROM sales_sku_daily WHERE sku_id=9501")
    c.execute("DELETE FROM skus WHERE sku_id=9501")
    c.commit()
    c.close()


def _file(rows):
    return {"file": ("ต้นทุน.xlsx", _xlsx([DC.HEADERS] + rows, sheet=DC.SHEET))}


def test_http_roles_token_and_preview(web, auth_on):
    an, ac, mg, br = _login("analyst1"), _login("accounting1"), _login("management1"), None
    # the analyst imports: a preview needs no token and writes nothing; the real import needs the owner token
    r = an.post("/api/bom-center/dish-cost/import?month=2026-10&dry_run=true", files=_file([["DCW-1", "x", 15, ""]]))
    assert r.status_code == 200 and r.json()["imported"] == 1 and r.json()["dry_run"] is True
    assert r.json()["sold_without_cost"] == [] or r.json()["sold_without_cost"][0]["code"] != "DCW-1"
    assert sqlite3.connect(str(web)).execute("SELECT COUNT(*) FROM bc_sku_cost_list").fetchone()[0] == 0
    r = an.post("/api/bom-center/dish-cost/import?month=2026-10&dry_run=false", files=_file([["DCW-1", "x", 15, ""]]))
    assert r.status_code == 403 and r.headers.get("X-Denied") == "token"
    r = an.post("/api/bom-center/dish-cost/import?month=2026-10&dry_run=false", files=_file([["DCW-1", "x", 15, ""]]), headers=TOKEN)
    assert r.status_code == 200 and r.json()["imported"] == 1 and r.json()["format"] == "แม่แบบต้นทุนต่อจาน"
    # a bad month and a missing header are refused in Thai
    assert an.post("/api/bom-center/dish-cost/import?month=2026-08&dry_run=true", files=_file([])).json()["detail"].startswith("ต้นทุนต่อจานเริ่มได้")
    assert an.post("/api/bom-center/dish-cost/import?month=2026-10&dry_run=true", files={"file": ("a.xlsx", _xlsx([["x"], [1]]))}).status_code == 400
    # the analyst edits one row
    assert an.post("/api/bom-center/dish-cost/edit", json={"month": "2026-10", "sku_code": "DCW-1", "unit_cost": 16}).headers.get("X-Denied") == "token"
    r = an.post("/api/bom-center/dish-cost/edit", json={"month": "2026-10", "sku_code": "DCW-1", "unit_cost": 16, "note": "ปรับ"}, headers=TOKEN)
    assert r.status_code == 200 and r.json()["unit_cost"] == 16
    # reading: the analyst, accounting and management
    for who in (an, ac, mg):
        d = who.get("/api/bom-center/dish-cost?month=2026-10")
        assert d.status_code == 200, d.text
        assert {x["code"]: x["unit_cost"] for x in d.json()["rows"]}["DCW-1"] == 16.0
        assert who.get("/api/bom-center/dish-cost/months").status_code == 200
        assert who.get("/api/bom-center/dish-cost/template.xlsx?month=2026-10").status_code == 200
        ex = who.get("/api/bom-center/dish-cost/export.xlsx?month=2026-10")
        assert ex.status_code == 200 and ex.content[:2] == b"PK"
    # accounting does not write the dish cost (no bom.center / bom.prices module)
    assert ac.post("/api/bom-center/dish-cost/import?month=2026-10&dry_run=true", files=_file([])).status_code == 403
    assert ac.post("/api/bom-center/dish-cost/edit", json={"month": "2026-10", "sku_code": "DCW-1", "unit_cost": 1}, headers=TOKEN).status_code == 403
    # a branch login reads and writes nothing here
    import main
    adm = _login("management1")
    adm.post("/api/auth/users", headers=TOKEN, json={"username": "br_dc_a", "password": "Temp@12345", "role": "branch", "branch": "BranchA"})
    try:
        br = TestClient(main.app)
        assert br.post("/api/auth/login", json={"username": "br_dc_a", "password": "Temp@12345"}).status_code == 200
        assert br.get("/api/bom-center/dish-cost?month=2026-10").status_code == 403
        assert br.post("/api/bom-center/dish-cost/edit", json={"month": "2026-10", "sku_code": "DCW-1", "unit_cost": 1}, headers=TOKEN).status_code == 403
    finally:
        c = sqlite3.connect(str(web))
        c.execute("DELETE FROM app_user WHERE username='br_dc_a'")
        c.commit()
        c.close()


def test_http_import_refreshes_the_reports_and_the_sync_route_is_token_gated(web, monkeypatch):
    import main
    monkeypatch.delenv("OSHINEI_AUTH", raising=False)
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "backup:tok")
    monkeypatch.delenv("OSHINEI_WRITE_TOKEN", raising=False)
    c = TestClient(main.app)
    calls = []
    import routers.bom_center as rb
    monkeypatch.setattr(rb, "invalidate_cache", lambda *a, **k: calls.append(a))
    r = c.post("/api/bom-center/dish-cost/import?month=2026-10&dry_run=false", files=_file([["DCW-1", "x", 15, ""]]),
               headers={"X-Upload-Token": "tok"})
    assert r.status_code == 200, r.text
    assert calls == [()]                                              # one full invalidate_cache() after the write; none for the preview
    c.post("/api/bom-center/dish-cost/import?month=2026-10&dry_run=true", files=_file([["DCW-1", "x", 15, ""]]))
    assert calls == [()]
    assert c.get("/api/_admin/bc-sku-cost-list", headers={"X-Seed-Token": "wrong"}).status_code == 404
    ok = c.get("/api/_admin/bc-sku-cost-list", headers={"X-Seed-Token": "tok"})
    assert ok.status_code == 200 and ok.json()["columns"] == ["sku_code", "month", "unit_cost", "note", "imported_by", "imported_at"]
    assert [r[:3] for r in ok.json()["rows"]] == [["DCW-1", "2026-10", 15.0]]
