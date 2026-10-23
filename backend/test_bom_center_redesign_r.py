# -*- coding: utf-8 -*-
"""PR-R — the month overview of ใบสรุปตัดเบิก and the suggested POS codes of สูตรตามระดับราคา.

The promises:
  * GET /api/bom-center/issue-summary/month?month= lists one row per counting branch with a period in the month: its dates,
    its state (ready = receipts confirmed and not posted · waiting = not confirmed · posted), and for a confirmed period the
    value and the unpriced lines of issue_summary(); sorted ready, waiting, posted; counts for the filter chips;
  * without `month` the latest month that has any period; a bad month is a 400; a branch login sees its own row only;
  * suggest_pos() offers a POS code only for an active OTHBF% / OTHAL% SKU of the SAME tier: the same name exactly, or a Thai
    name with a difflib ratio >= 0.8; one SKU is offered to one menu only; a SKU with a recipe in the release is not offered;
  * the unmatched list of tier_view() carries the suggestion.
"""
import pytest

import auth
from bom_center import recipes as R
from bom_center import variance as VA
from test_bom_center_issue_summary import TOKEN, _login, _restore, _seed, _snap, auth_on  # noqa: F401
from test_bom_center_s2 import _db

MONTH = "/api/bom-center/issue-summary/month"


# ── the suggester ────────────────────────────────────────────────────────────
@pytest.fixture
def con(tmp_path):
    c = _db(tmp_path)
    rows = [(20, "OTHBFPRK00041", "(B) ลิ้นหมูย่าง (1 ชิ้น ต่อ 1 เสิร์ฟ)", "459", 1),
            (21, "OTHBFSHA00102", "(B) ลูกชิ้นปลา (1 ชิ้น ต่อ 1 เสิร์ฟ)", "459", 1),
            (22, "OTHBFSHA00103", "(B) ลูกชิ้นปลารักบี้ (1 ชิ้น ต่อ 1 เสิร์ฟ)", "459", 1),
            (23, "OTHBFSEA00031", "(B) หมึกกระดอง", "629", 1),
            (24, "OTHBFSEA00099", "(B) ปูม้านึ่ง", "459", 0),                    # inactive: never offered
            (25, "OTHALSEA00001", "(A) ทูน่าซาชิมิ", "459", 1),
            (26, "OTHBFXXX00001", "(B) รหัสไม่มีชื่อไทยตรง", "459", 1)]
    for sid, code, desc, tier, active in rows:
        c.execute("INSERT INTO skus(sku_id, sku_code, description, category_id, unit, business_type, active) VALUES(?,?,?,3,'plate','buffet',?)",
                  (sid, code, desc, active))
        c.execute("INSERT INTO sku_tier(sku_id, tier) VALUES(?,?)", (sid, tier))
    c.execute("INSERT INTO bc_dish(dish_id, name_th, name_en, min_tier) VALUES(90, 'รหัสไม่มีชื่อไทยตรง', 'Hand Cut Beef', 459)")
    c.execute("INSERT INTO bc_dish_sku(sku_code, dish_id, channel) VALUES('OTHBFXXX00001', 90, 'BF')")
    c.commit()
    yield c
    c.close()


def test_suggest_the_same_name_exactly(con):
    got = R.suggest_pos(con, "ลูกชิ้นปลา", "Fish Balls", 459)
    assert got["sku_code"] == "OTHBFSHA00102" and got["method"] == "exact" and got["score"] == 1.0
    assert got["name"] == "(B) ลูกชิ้นปลา (1 ชิ้น ต่อ 1 เสิร์ฟ)"
    assert R.suggest_pos(con, "ลูกชิ้นปลารักบี้", "", 459)["sku_code"] == "OTHBFSHA00103"      # the longer name is its own menu


def test_suggest_a_near_thai_name(con):
    got = R.suggest_pos(con, "ลูกชิ้นปลาา", "", 459)                    # a typo in the kitchen's file
    assert got["sku_code"] == "OTHBFSHA00102" and got["method"] == "close" and R.SUGGEST_MIN <= got["score"] < 1
    assert R.suggest_pos(con, "ลิ้นหมู", "", 459) is None                # 0.78: below the line, so nothing is offered


def test_suggest_the_same_english_name(con):
    got = R.suggest_pos(con, "เนื้อวัวหั่นเอง", "Hand Cut Beef", 459)       # the dish of the SKU carries the English name
    assert got["sku_code"] == "OTHBFXXX00001" and got["method"] == "exact"


def test_suggest_nothing_when_nothing_is_close(con):
    assert R.suggest_pos(con, "เบอร์เกอร์วากิว", "Wagyu Burger", 459) is None
    assert R.suggest_pos(con, "", "", 459) is None


def test_suggest_stays_in_the_tier_and_skips_inactive_skus(con):
    assert R.suggest_pos(con, "หมึกกระดอง", "", 459) is None                 # the SKU is ฿629
    assert R.suggest_pos(con, "หมึกกระดอง", "", 629)["sku_code"] == "OTHBFSEA00031"
    assert R.suggest_pos(con, "ปูม้านึ่ง", "", 459) is None                  # inactive
    assert R.suggest_pos(con, "ทูน่าซาชิมิ", "", 459)["sku_code"] == "OTHALSEA00001"      # OTHAL% counts too


def test_suggest_skips_a_sku_that_has_a_recipe_in_the_release(con):
    rid = R.get_or_create_draft(con, "พ.ย. (ร่าง)", "2026-11", "2026-07", "t")
    R.write_recipe(con, rid, "OTHBFSHA00102", [{"ingredient_code": "13-01-009", "ingredient_name": "ปูอัด", "qty": 10, "unit": "กรัม"}],
                   source="cost-rw", who="t")
    assert R.suggest_pos(con, "ลูกชิ้นปลา", "", 459, rid) is None
    assert R.suggest_pos(con, "ลูกชิ้นปลา", "", 459, -1)["sku_code"] == "OTHBFSHA00102"      # another release: still free


def test_one_sku_is_offered_to_one_menu_only(con):
    us = [{"tier": 459, "th": "ลูกชิ้นปลาา", "en": ""}, {"tier": 459, "th": "ลูกชิ้นปลา", "en": "Fish Balls"}, {"tier": 459, "th": "เบอร์เกอร์", "en": ""}]
    R.suggest_unmatched(con, -1, us)
    assert us[1]["suggest"]["sku_code"] == "OTHBFSHA00102" and us[1]["suggest"]["method"] == "exact"     # the stronger claim keeps it
    assert us[0]["suggest"] is None and us[2]["suggest"] is None


def test_the_unmatched_list_of_the_tier_view_carries_the_suggestion(con):
    import json
    rid = R.get_or_create_draft(con, "พ.ย. (ร่าง)", "2026-11", "2026-07", "t")
    payload = {"tier": 459, "en": "Fish Balls", "th": "ลูกชิ้นปลา", "total": 13.5, "name_key": R.name_key("ลูกชิ้นปลา"), "lines": [{"qty": 1}]}
    other = dict(payload, th="เบอร์เกอร์", en="Burger", name_key=R.name_key("เบอร์เกอร์"))
    for ref, p in (("459|a", payload), ("459|b", other)):
        con.execute("INSERT INTO bc_release_check(release_id, check_code, ref, severity, message) VALUES(?, 'unmatched', ?, 'block', ?)",
                    (rid, ref, json.dumps(p, ensure_ascii=False)))
    con.commit()
    u = {x["ref"]: x for x in R.tier_view(con, rid)["unmatched"]}
    assert u["459|a"]["suggest"]["sku_code"] == "OTHBFSHA00102" and u["459|b"]["suggest"] is None


# ── the month overview over HTTP ─────────────────────────────────────────────
def test_the_month_overview_rows_states_values_and_pinning(client, app_db, auth_on):  # noqa: F811
    import main
    client.get("/api/bom-center/hub/queue")                                         # creates the bc_* tables
    snap = _snap(main.DB_PATH)
    ids = _seed(main.DB_PATH)
    e1, e2 = ids[1], ids[2]
    try:
        acc, mg = _login("accounting1"), _login("management1")
        mg.post("/api/auth/users", headers=TOKEN, json={"username": "br_month_test", "password": "Temp@12345",
                                                         "role": "branch", "branch": "BranchA"})
        br = _login("br_month_test", "Temp@12345")
        # nothing confirmed: both branches wait, no value is computed
        d = acc.get(MONTH).json()
        assert d["month"] == "2026-09" and d["months"] == ["2026-09"]
        assert [(r["name"], r["status"]) for r in d["rows"]] == [("BranchA", "waiting"), ("BranchB", "waiting")]
        assert d["counts"] == {"all": 2, "open": 2, "ready": 0, "waiting": 2, "posted": 0}
        a = d["rows"][0]
        assert (a["begin_date"], a["end_date"], a["days"], a["end_count_id"]) == ("2026-09-01", "2026-10-01", 30, e1)
        assert a["used_value"] is None and a["unpriced"] is None and a["receipts_confirmed"] is False
        # confirmed and not posted: ready first, with the value of issue_summary()
        assert mg.post("/api/bom-center/variance/confirm-receipts?branch_id=1", json={"end_count_id": e1}, headers=TOKEN).status_code == 200
        d = acc.get(f"{MONTH}?month=2026-09").json()
        assert [(r["name"], r["status"]) for r in d["rows"]] == [("BranchA", "ready"), ("BranchB", "waiting")]
        a = d["rows"][0]
        full = acc.get(f"/api/bom-center/issue-summary?end_count_id={e1}&branch_id=1").json()["totals"]
        assert a["used_value"] == pytest.approx(full["used_value"]) == pytest.approx(22.5) and a["unpriced"] == full["unpriced"] == 0
        assert d["counts"]["ready"] == 1 and d["counts"]["open"] == 2
        # posted: the state, who and when; sorted after the branch that still waits
        assert acc.post("/api/bom-center/issue-summary/posted", json={"end_count_id": e1, "posted": True}, headers=TOKEN).status_code == 200
        d = acc.get(MONTH).json()
        assert [(r["name"], r["status"]) for r in d["rows"]] == [("BranchB", "waiting"), ("BranchA", "posted")]
        a = d["rows"][1]
        assert a["accounting_posted_by"] == "accounting1" and a["accounting_posted_at"] and a["used_value"] == pytest.approx(22.5)
        assert d["counts"] == {"all": 2, "open": 1, "ready": 0, "waiting": 1, "posted": 1}
        # un-posting puts it back among the ready ones
        assert acc.post("/api/bom-center/issue-summary/posted", json={"end_count_id": e1, "posted": False}, headers=TOKEN).status_code == 200
        assert [(r["name"], r["status"]) for r in acc.get(MONTH).json()["rows"]] == [("BranchA", "ready"), ("BranchB", "waiting")]
        # a month with no period is an empty list; a bad month is refused
        d = acc.get(f"{MONTH}?month=2026-01").json()
        assert d["rows"] == [] and d["counts"]["all"] == 0 and d["months"] == ["2026-09"]
        for bad in ("2026-9", "bad", "2026-13x"):
            r = acc.get(f"{MONTH}?month={bad}")
            assert r.status_code == 400 and "เดือนไม่ถูกต้อง" in r.json()["detail"], bad
        # a branch login: its own row only, whatever the month
        d = br.get(MONTH).json()
        assert [r["name"] for r in d["rows"]] == ["BranchA"] and d["counts"]["all"] == 1
        # the roles: Accounting, Operation and Sales Support read it; the warehouse does not
        pa, m = auth.path_allowed, auth.modules_for_role
        assert all(pa("GET", MONTH, m(role)) for role in ("accounting", "operation", "salessupport", "branch", "management", "analyst"))
        assert not pa("GET", MONTH, m("warehouse"))
        assert e2 != e1
    finally:
        _restore(main.DB_PATH, snap)


# ── the two panes as pages ───────────────────────────────────────────────────
def _src(rel):
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / rel).read_text(encoding="utf-8")


def test_the_issue_pane_is_the_month_overview():
    tab, js = _src("tabs/52-bomcenter.html"), _src("js/13-bom-center.js")
    pane = tab[tab.index('<div id="bmc-pane-issue"'):tab.index('<!-- ── ingredient master')]
    for i in ("bmcIssMonth", "bmcIssFilter", "bmcIssAllBtn", "bmcIssList", "bmcIssDrawer", "bmcIssRecon", "bmcIssMoveBox", "bmcIssStatus"):
        assert f'id="{i}"' in pane, i
    for gone in ('id="bmcIssPeriod"', 'id="bmcIssBranch"', "วิธีใช้หน้านี้"):          # the two selects and the folded how-to
        assert gone not in pane, gone
    assert "/api/bom-center/issue-summary/month" in js and "uiHelp(" in js
    assert "let _bmcIssFilter = 'open'" in js                                          # finished work is out of the default view
    assert "[['open', 'ยังไม่เสร็จ'], ['ready', 'พร้อมบันทึก'], ['waiting', 'รอสาขาส่ง'], ['posted', 'บันทึกแล้ว'], ['all', 'ทั้งหมด']]" in js


def test_the_recipes_pane_is_step_led():
    tab, js = _src("tabs/52-bomcenter.html"), _src("js/13-bom-center.js")
    pane = tab[tab.index('<div id="bmc-pane-rec"'):tab.index('<!-- ── review & release')]
    for i in ("bmcRel", "bmcRecSteps", "bmcRecNext", "bmcRecKpis", "bmcRecTabs", "bmcTierChips", "bmcRecQ", "bmcRecWork",
              "bmcRecImportMenu", "bmcRecDownloadMenu", "bmcCrwEff", "bmcCrwPm", "bmcEditor", "bmcRecStatus", "bmcRecReport"):
        assert f'id="{i}"' in pane, i
    assert 'id="bmcRecIssues"' not in pane and "เฉพาะรายการที่ต้องตรวจ" not in pane      # the checkbox became the ต้องตรวจ tab
    for step in ("ร่าง (ครัวสร้างสูตร)", "ฝ่ายวิเคราะห์ตรวจ", "ประกาศใช้", "ผู้บริหารรับทราบ"):
        assert step in js, step
    for label in ("ส่งตรวจ", "ส่งกลับแก้ไข", "ไปตรวจและประกาศใช้", "รับทราบ", "ไม่มีงานค้างในฉบับนี้", "จับคู่ตามที่เสนอ"):
        assert label in js, label
    # the shared "?" dialog lives in the core slice
    core = _src("js/01-core.js")
    assert "window.uiHelp = function (title, html, opener)" in core and "function uiHelp(" in _src("js/globals.d.ts")
