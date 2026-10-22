"""BoM Center — Phase 2 · Sprint S6: branch stock counts and the branch login.

The promises:
  * a branch counts what it actually used in the last four weeks plus the monitored
    list, grouped by storage zone, in the unit it handles (packs) — converted to the
    recipe's base unit;
  * the first submitted count is the BEGIN stock; every later one is an END and,
    automatically, the begin of the next period;
  * a count is a draft until submitted once; bad numbers and unconvertible units are
    refused; a draft can be thrown away;
  * the branch login reaches only its own branch's stock pages: every other API is
    closed to a role without sales.overview, and asking for another branch is refused.
"""
from datetime import date

import pytest
from fastapi import HTTPException

import auth
from bom_center import stock as ST
from test_bom_center_s4b import con  # noqa: F401  (five weeks of sales at branch 1)

AS_OF = date(2026, 10, 5)


@pytest.fixture
def db(con):  # noqa: F811
    con.execute("INSERT INTO bc_ingredient(code, name_th, kind, base_unit, recv_unit, qty_per_recv, monitored)"
                " VALUES('S-011', 'เกลือป่น 1 กก.', 'raw', 'กรัม', 'ถุง 1 กก.', 1000, 1)")
    con.commit()
    return con


def _items(sh):
    return {i["code"]: dict(i, zone=z["zone"]) for z in sh["zones"] for i in z["items"]}


def test_the_sheet_lists_what_the_branch_uses_plus_the_monitored_list(db):
    sh = ST.sheet(db, 1, AS_OF)
    it = _items(sh)
    assert set(it) == {"13-01-009", "02-01-001", "S-011"}      # crab stick + salmon sold, salt monitored
    assert sh["next_kind"] == "begin" and sh["last_count"] is None and sh["draft"] is None
    assert it["S-011"]["required"] and it["S-011"]["monitored"]
    assert it["13-01-009"]["count_unit"] == "แพ็ค 1 กก." and it["13-01-009"]["per_count_unit"] == 1000
    assert it["13-01-009"]["use_per_day"] > 0


def test_begin_then_end_and_the_end_is_the_next_begin(db):
    out = ST.save(db, 1, [{"code": "13-01-009", "qty": 2.5, "unit": "แพ็ค 1 กก."}], "udon", count_date="2026-10-01")
    assert out["status"] == "draft" and out["kind"] == "begin"
    assert ST.sheet(db, 1, AS_OF)["draft"]["count_id"] == out["count_id"]
    assert _items(ST.sheet(db, 1, AS_OF))["13-01-009"]["draft_qty"] == 2.5
    out = ST.save(db, 1, [{"code": "S-011", "qty": 3, "unit": "ถุง 1 กก."}, {"code": "02-01-001", "qty": 2, "unit": "แพ็ค 1 กก."}],
                  "udon", submit=True, count_date="2026-10-01")          # PR-M4: every required item is counted
    assert out["status"] == "submitted" and out["lines"] == 3
    d = ST.detail(db, out["count_id"])
    assert {l["code"]: l["qty_base"] for l in d["lines"]} == {"13-01-009": 2500.0, "S-011": 3000.0, "02-01-001": 2000.0}
    sh = ST.sheet(db, 1, AS_OF)
    assert sh["next_kind"] == "end" and sh["last_count"]["kind"] == "begin"
    assert _items(sh)["13-01-009"]["last_qty"] == 2.5
    # PR-M4: every required (บังคับ) item must be counted before a count is submitted
    out2 = ST.save(db, 1, [{"code": "13-01-009", "qty": 1}, {"code": "02-01-001", "qty": 1}, {"code": "S-011", "qty": 1}], "udon",
                   submit=True, count_date="2026-10-04")
    assert out2["kind"] == "end"
    ps = ST.periods(db, 1)
    assert ps == [({"count_id": out["count_id"], "date": "2026-10-01"}, {"count_id": out2["count_id"], "date": "2026-10-04"})]
    assert [h["kind"] for h in ST.history(db, 1)] == ["end", "begin"]
    # PR-J1: a confirmed daily issue slip (ใบตัดเบิก) is not a count — it changes neither the begin/end sequence
    # nor the periods
    from datetime import datetime
    from bom_center import issues as IS
    from bom_center.clock import BKK
    s = IS.save(db, 1, "2026-10-05", [{"code": "13-01-009", "qty_used": 1, "unit": "แพ็ค 1 กก."}], "udon", submit=True,
                now=datetime(2026, 10, 5, 20, 0, tzinfo=BKK))
    assert s["status"] == "confirmed"
    assert ST.sheet(db, 1, AS_OF)["next_kind"] == "end" and ST.sheet(db, 1, AS_OF)["last_count"]["count_id"] == out2["count_id"]
    assert ST.draft_of(db, 1) is None and ST.periods(db, 1) == ps
    assert [h["kind"] for h in ST.history(db, 1)] == ["end", "begin"]
    assert [h["kind"] for h in ST.history(db, 1, kinds=ST.MONTH_KINDS)] == ["end", "begin"]


def test_bad_counts_are_refused(db):
    with pytest.raises(ValueError):
        ST.save(db, 1, [{"code": "13-01-009", "qty": -1}], "udon")
    db.rollback()
    with pytest.raises(ValueError):
        ST.save(db, 1, [], "udon", submit=True)                 # nothing counted
    db.rollback()
    out = ST.save(db, 1, [{"code": "13-01-009", "qty": 1, "unit": "ช้อนโต๊ะ"}], "udon")   # no rule for a tablespoon
    assert out["unconverted"]
    with pytest.raises(ValueError):
        ST.save(db, 1, [], "udon", submit=True)
    db.rollback()
    with pytest.raises(ValueError):
        ST.save(db, 1, [{"code": "13-01-009", "qty": 1}], "udon", count_date="1/10/2026")
    db.rollback()
    with pytest.raises(ValueError):
        ST.sheet(db, 999)


def test_a_blank_removes_a_line_and_a_draft_can_be_discarded(db):
    out = ST.save(db, 1, [{"code": "13-01-009", "qty": 1}, {"code": "S-011", "qty": 2}], "udon")
    assert out["lines"] == 2
    assert ST.save(db, 1, [{"code": "S-011", "qty": None}], "udon")["lines"] == 1
    assert ST.discard_draft(db, 1, "udon") and ST.draft_of(db, 1) is None
    assert not ST.discard_draft(db, 1, "udon")


def test_the_centre_sets_zone_and_count_unit(db):
    db.execute("INSERT INTO bc_unit_rule(ingredient_code, unit, factor_to_base, label) VALUES('S-011', 'ช้อน', 15, 'ช้อนตวง')")
    assert ST.set_layout(db, [{"code": "S-011", "zone": "dry", "count_unit": "ช้อน"}], "analyst") == 1
    it = _items(ST.sheet(db, 1, AS_OF))["S-011"]
    assert it["zone"] == "dry" and not it["zone_guessed"] and it["count_unit"] == "ช้อน" and it["per_count_unit"] == 15
    with pytest.raises(ValueError):
        ST.set_layout(db, [{"code": "S-011", "zone": "attic"}], "analyst")
    with pytest.raises(ValueError):
        ST.set_layout(db, [{"code": "S-011", "count_unit": "ช้อนโต๊ะ"}], "analyst")
    with pytest.raises(ValueError):
        ST.set_layout(db, [{"code": "NOPE"}], "analyst")


def test_the_branch_role_is_closed_by_default():
    mods = auth.ROLE_MODULES["branch"]
    allowed = lambda m, p: auth.path_allowed(m, p, mods)   # noqa: E731
    assert allowed("GET", "/api/bom-center/stock/sheet")
    assert allowed("POST", "/api/bom-center/stock/count")
    assert allowed("GET", "/api/branches") and allowed("GET", "/api/months") and allowed("POST", "/api/usage/tab")
    assert not allowed("POST", "/api/bom-center/stock/layout")      # the centre's
    assert not allowed("GET", "/api/sales/daily")                    # no rule — closed for this role
    assert not allowed("GET", "/api/home/brief")
    assert not allowed("GET", "/api/bom-center/recipes")
    assert not allowed("GET", "/api/bom-center/summary")
    # every central role keeps default-allow
    for r in ("marketing", "operation", "analyst", "accounting", "innovation", "salessupport"):
        assert auth.path_allowed("GET", "/api/sales/daily", auth.ROLE_MODULES[r]), r
    assert auth.path_allowed("GET", "/api/bom-center/stock/sheet", auth.ROLE_MODULES["salessupport"])
    assert "branch.stock" in auth.ROLE_MODULES["management"]


def test_a_branch_account_needs_a_branch(db):
    db.row_factory = __import__("sqlite3").Row
    auth.init_auth(db)
    bname = db.execute("SELECT name FROM branches WHERE branch_id=1").fetchone()[0]
    with pytest.raises(ValueError):
        auth.create_user(db, "branch_x", "Abcdefgh1234!", "branch")
    auth.create_user(db, "branch_ud", "Abcdefgh1234!", "branch", branch=bname)
    with pytest.raises(ValueError):
        auth.update_user(db, "branch_ud", branch=None)
    auth.create_user(db, "ana1", "Abcdefgh1234!", "analyst")
    with pytest.raises(ValueError):
        auth.update_user(db, "ana1", role="branch")


def test_a_branch_login_is_pinned_to_its_branch(db, monkeypatch):
    from routers import bom_center as RB
    bname = db.execute("SELECT name FROM branches WHERE branch_id=1").fetchone()[0]
    monkeypatch.setattr(auth, "scope_branch", lambda request: bname)
    assert RB._stock_branch(db, None, None) == 1
    assert RB._stock_branch(db, None, 1) == 1
    with pytest.raises(HTTPException) as e:
        RB._stock_branch(db, None, 2)
    assert e.value.status_code == 403
    monkeypatch.setattr(auth, "scope_branch", lambda request: None)
    with pytest.raises(HTTPException) as e:
        RB._stock_branch(db, None, None)
    assert e.value.status_code == 400
    assert RB._stock_branch(db, None, 1) == 1


# ── owner 2026-10-13 · "ปิดงวดสิ้นเดือนต้องนับ 100%": a count is submitted only with EVERY item of the sheet counted ──
def _required(sh):
    return [i for z in sh["zones"] for i in z["items"] if i["required"]]


def _everything(sh):
    return [i for z in sh["zones"] for i in z["items"]]


def _zeros(sh, qty=0):
    return [{"code": i["code"], "qty": qty, "unit": i["count_unit"]} for i in _everything(sh)]


def test_a_count_with_an_uncounted_item_is_refused(db):
    items = _everything(ST.sheet(db, 1, AS_OF))
    assert len(items) >= 3, "the fixture must have a few items on the sheet"
    first = items[0]
    ST.save(db, 1, [{"code": first["code"], "qty": 1, "unit": first["count_unit"]}], "udon", count_date="2026-10-01")      # a draft is always allowed
    with pytest.raises(ValueError) as e:
        ST.save(db, 1, [], "udon", submit=True, count_date="2026-10-01")
    msg = str(e.value)
    n = len(items) - 1
    assert msg.startswith(f"ยังไม่ได้นับ {n} รายการ ปิดงวดสิ้นเดือนต้องตรวจนับครบทุกรายการ (เช่น ") and msg.endswith(")")
    assert items[1]["name"] in msg and first["name"] not in msg
    db.rollback()
    assert ST.draft_of(db, 1) is None                     # the refused call stored nothing (the router rolls back too)
    # a 0 is a count: every item at 0 is accepted
    out = ST.save(db, 1, _zeros(ST.sheet(db, 1, AS_OF)), "udon", submit=True, count_date="2026-10-01")
    assert out["status"] == "submitted" and out["lines"] == len(items)


def test_a_file_import_must_carry_every_item_and_says_what_it_becomes(db):
    """Owner 2026-10-13 (replaces QA M5's "refuse unless confirmed"): a file that leaves ANY item of the sheet blank is refused, dry run and
    saving alike, with no override; the answer says whether the file is the ต้นงวด or the ปลายงวด and how many items it counts."""
    part = [{"code": "S-011", "qty": 2.0, "unit": "ถุง 1 กก.", "note": None}]
    pre = ST.import_count(db, 1, "2026-09-01", part, "analyst")
    sheet_n = len(_everything(ST.sheet(db, 1)))
    assert pre["incomplete"] and not pre["ok"] and pre["blank"] == sheet_n - 1 and pre["kind"] == "begin" and pre["kind_th"] == "ต้นงวด"
    assert pre["counted_th"] == f"นับมา 1 จาก {sheet_n} รายการ · รายการที่ไม่ได้กรอกถือว่าไม่ได้นับ"
    assert pre["incomplete_th"].startswith(f"ยังไม่ได้นับ {sheet_n - 1} รายการ ปิดงวดสิ้นเดือนต้องตรวจนับครบทุกรายการ (เช่น ") and pre["missing"]
    with pytest.raises(ValueError, match="ปิดงวดสิ้นเดือนต้องตรวจนับครบทุกรายการ"):
        ST.import_count(db, 1, "2026-09-01", part, "analyst", dry_run=False)
    with pytest.raises(TypeError):                                                           # there is no accept_partial any more
        ST.import_count(db, 1, "2026-09-01", part, "analyst", accept_partial=True)
    assert ST.draft_of(db, 1) is None and ST.last_submitted(db, 1) is None
    full = [{**ln, "unit": ln["unit"], "note": None} for ln in _zeros(ST.sheet(db, 1), 2)]
    ok = ST.import_count(db, 1, "2026-09-01", full, "analyst")
    assert ok["ok"] and not ok["incomplete"] and ok["blank"] == 0 and ok["counted"] == sheet_n
    out = ST.import_count(db, 1, "2026-09-01", full, "analyst", dry_run=False)
    assert out["ok"] and out["status"] == "submitted"
    nxt = ST.import_count(db, 1, "2026-10-01", full, "analyst")                              # the second file is the END of the period
    assert nxt["kind"] == "end" and nxt["kind_th"] == "ปลายงวด" and nxt["closes"] == {"begin_date": "2026-09-01", "end_date": "2026-10-01"}
    assert "ปิดงวด" in nxt["role_th"] and "1/9/2026" in nxt["role_th"] and nxt["ok"]


def test_last_count_is_read_in_the_unit_counted_now(db):
    ST.save(db, 1, [{"code": i["code"], "qty": (2500 if i["code"] == "13-01-009" else 0), "unit": "กรัม"}
                    for i in _required(ST.sheet(db, 1, AS_OF)) + [i for z in ST.sheet(db, 1, AS_OF)["zones"] for i in z["items"]
                                                                  if i["code"] == "13-01-009" and not i["required"]]],
            "udon", submit=True, count_date="2026-10-01")
    crab = _items(ST.sheet(db, 1, AS_OF))["13-01-009"]
    assert crab["count_unit"] == "แพ็ค 1 กก." and crab["last_unit"] == crab["count_unit"]      # typed in grams, read in packs
    assert crab["last_qty"] == 2.5 and crab["last_base"] == 2500.0


import pytest as _pytest_s3  # noqa: E402
pytestmark = _pytest_s3.mark.usefixtures("recipe_mode")   # PR-S3: this module builds its forecast, plans and count sheets from recipes (mode full)
