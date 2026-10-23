# -*- coding: utf-8 -*-
"""PR-S1 - the system mode (docs/STOCK_FIRST_PLAN.md): stock first, the recipes (BoM) hidden until Phase 2.

  * app_settings `bc_mode` is `stock` when the key is absent; `full` is the whole app; a bad value is refused; every change is audited;
  * GET /api/bom-center/mode is open to EVERY signed-in role (a branch or franchise login holds no sales.overview); POST is management's
    alone (bom.audit) and needs the write token;
  * todo.build in stock mode has none of RECIPE_KEYS (items or close steps) and still has the price, order, claim and close items;
    in full mode the list is what it was before this PR;
  * the ingredient register is written by a login with bom.center only while the mode is stock (403 with the fixed text otherwise);
  * app_settings is a preserved table, so the mode survives a database swap.
"""
import sqlite3
from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import auth
import core
from bom_center import close_settings as CS
from bom_center import nav_badges as NB
from bom_center import todo as TD
from test_bom_center_s11 import udon  # noqa: F401  (branch 1 = อุดรธานี, the centre's September report)
from test_bom_center_s4b import con  # noqa: F401  (sales Sep–Oct 2026 at branch 1, default order calendar)

TOKEN = "owner-token-xyz"
TODAY = date(2026, 10, 7)
M = "2026-09"
DETAIL = "โหมดสต๊อกก่อน: ทะเบียนวัตถุดิบแก้ไขได้เฉพาะฝ่ายวิเคราะห์"


def _keys(d):
    return {TD._base_key(i["key"]) for i in d["items"]}, {s["key"] for s in d["close"]["steps"]}


# ── the setting ──────────────────────────────────────────────────────────────
def test_default_is_stock_when_the_key_is_absent():
    c = sqlite3.connect(":memory:")
    assert CS.mode(c) == "stock"                                      # no app_settings table at all
    c.execute("CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    assert CS.mode(c) == "stock" and CS.stock_mode(c) is True        # the table, no key
    assert CS.mode_info(c) == {"mode": "stock", "label_th": "สต๊อกก่อน (ปิดส่วนสูตร)"}
    c.execute("INSERT INTO app_settings VALUES('bc_mode', 'nonsense')")
    assert CS.mode(c) == "stock"                                      # an unreadable value never opens the recipes


def test_set_get_round_trip_audit_and_bad_value(udon):
    assert CS.mode(udon) == "stock"
    assert CS.set_mode(udon, "mgr", "full") == {"mode": "full", "label_th": "เต็มระบบ (รวมสูตร)"}
    assert CS.mode(udon) == "full" and CS.stock_mode(udon) is False
    CS.set_mode(udon, "mgr", " STOCK ")
    assert CS.mode(udon) == "stock"
    rows = udon.execute("SELECT who, old_value, new_value, row_key FROM bc_audit WHERE action='bc_mode' ORDER BY id").fetchall()
    assert rows == [("mgr", "stock", "full", "bc_mode"), ("mgr", "full", "stock", "bc_mode")]
    for bad in ("", "both", None, 1, "ful"):
        with pytest.raises(ValueError, match="โหมดต้องเป็น stock หรือ full"):
            CS.set_mode(udon, "mgr", bad)
    assert CS.mode(udon) == "stock" and udon.execute("SELECT COUNT(*) FROM bc_audit WHERE action='bc_mode'").fetchone()[0] == 2


def test_the_mode_survives_a_database_swap(tmp_path):
    live, inc = tmp_path / "live.db", tmp_path / "inc.db"
    c = sqlite3.connect(str(live))
    c.execute("CREATE TABLE app_user(id INTEGER PRIMARY KEY, name TEXT)")
    c.execute("INSERT INTO app_user(name) VALUES('admin')")
    c.execute("CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    c.execute("INSERT INTO app_settings VALUES('bc_mode', 'full')")
    c.commit(); c.close()
    c = sqlite3.connect(str(inc))
    c.execute("CREATE TABLE sales_transaction(id INTEGER PRIMARY KEY)")
    c.commit(); c.close()
    import main
    safe, reason = main._carry_forward_and_verify(str(live), str(inc))
    assert safe, reason
    c = sqlite3.connect(str(inc))
    assert CS.mode(c) == "full"
    c.close()
    assert "app_settings" in core._PRESERVED_EXACT


# ── the endpoints and who may call them ──────────────────────────────────────
@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    yield


def _login(user, pw=auth.DEFAULT_PASSWORD):
    import main
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def world(client, app_db, auth_on):
    """One login per role; a branch and a franchise login are pinned to BranchA. The mode is put back to the default afterwards."""
    import main
    client.get("/api/bom-center/hub/queue")
    mg = _login("management1")
    for name, role in (("sm_branch", "branch"), ("sm_owner", "franchise")):
        r = mg.post("/api/auth/users", headers={"X-Upload-Token": TOKEN},
                    json={"username": name, "password": "Temp@12345", "role": role, "branch": "BranchA"})
        assert r.status_code in (200, 409), r.text
    out = {"management": mg}
    for role in ("analyst", "innovation", "warehouse", "accounting", "salessupport", "operation", "marketing"):
        out[role] = _login(role + "1")
    out["branch"], out["franchise"] = _login("sm_branch", "Temp@12345"), _login("sm_owner", "Temp@12345")
    try:
        yield out
    finally:
        c = sqlite3.connect(str(main.DB_PATH))
        c.execute("DELETE FROM app_user WHERE username IN ('sm_branch','sm_owner')")
        c.execute("DELETE FROM app_settings WHERE key='bc_mode'")
        c.execute("DELETE FROM bc_audit WHERE action='bc_mode'")
        c.commit()
        c.close()


def test_every_signed_in_role_reads_the_mode(world):
    for role, c in world.items():
        r = c.get("/api/bom-center/mode")
        assert r.status_code == 200, (role, r.text)
        assert r.json() == {"mode": "stock", "label_th": "สต๊อกก่อน (ปิดส่วนสูตร)"}, role


def test_the_matrix_lets_every_role_read_and_only_bom_audit_write():
    for role in auth.ROLES:
        mods = auth.ROLE_MODULES[role]
        assert auth.path_allowed("GET", "/api/bom-center/mode", mods), role
        assert auth.path_allowed("POST", "/api/bom-center/mode", mods) is ("bom.audit" in mods), role
    assert [r for r in auth.ROLES if "bom.audit" in auth.ROLE_MODULES[r]] == ["management"]


def test_only_management_with_the_token_changes_the_mode(world):
    body = {"mode": "full"}
    for role, c in world.items():
        if role == "management":
            continue
        r = c.post("/api/bom-center/mode", json=body, headers={"X-Upload-Token": TOKEN})
        assert r.status_code == 403, (role, r.status_code)
    mg = world["management"]
    r = mg.post("/api/bom-center/mode", json=body)                                   # no token
    assert r.status_code == 403 and r.headers.get("X-Denied") == "token"
    assert mg.get("/api/bom-center/mode").json()["mode"] == "stock"
    assert mg.post("/api/bom-center/mode", json={"mode": "both"}, headers={"X-Upload-Token": TOKEN}).status_code == 400
    r = mg.post("/api/bom-center/mode", json=body, headers={"X-Upload-Token": TOKEN})
    assert r.status_code == 200 and r.json() == {"mode": "full", "label_th": "เต็มระบบ (รวมสูตร)"}
    for role, c in world.items():                                                     # every role sees the new mode
        assert c.get("/api/bom-center/mode").json()["mode"] == "full", role
    assert mg.post("/api/bom-center/mode", json={"mode": "stock"}, headers={"X-Upload-Token": TOKEN}).json()["mode"] == "stock"


# ── หน้างาน, the badges ──────────────────────────────────────────────────────
def test_todo_in_stock_mode_has_no_recipe_item_and_keeps_the_rest(udon):
    full_i, full_s = None, None
    CS.set_mode(udon, "t", "full")
    d_full = TD.build(udon, M, None, None, today=TODAY)
    full_i, full_s = _keys(d_full)
    CS.set_mode(udon, "t", "stock")
    d = TD.build(udon, M, None, None, today=TODAY)
    items, steps = _keys(d)
    assert not (items & set(TD.RECIPE_KEYS)), items & set(TD.RECIPE_KEYS)
    assert not (steps & set(TD.RECIPE_KEYS)), steps & set(TD.RECIPE_KEYS)
    assert not any(i.get("rule") == "formula_excess" for i in d["items"])
    # what stays: prices, orders, the slip, claims, counts and the month close
    assert {"price_month", "price_submit", "master_seed", "master_issues"} <= items
    assert "order_due" in items and "issue_today" in items            # (the round suffix of order_due_1 / _2 / _salmon is dropped by _base_key)
    # PL1: the month close of stock mode is ONE list of five steps (the stepper and หน้างาน print it); the grouped ones keep the full-mode steps as `parts`
    assert steps == {"central_report", "code_links", "counts", "receipts_confirm", "issue_posted"} and "monthly_summary" not in steps
    assert {"deliveries", "begin_count", "end_count"} <= {p["key"] for s in d["close"]["steps"] for p in s.get("parts", [])}
    # full mode is today's list: stock = full minus the recipe keys, nothing else moved
    assert {"draft", "acknowledge", "sub_recipes", "code_review"} <= full_i and "release_in_force" in full_s
    assert items == full_i - set(TD.RECIPE_KEYS)
    assert full_s - set(TD.RECIPE_KEYS) == {"central_report", "code_links", "deliveries", "begin_count", "end_count", "receipts_confirm", "issue_posted"}
    assert [s["seq"] for s in d_full["close"]["steps"]] == list(range(1, 10))
    assert d["close"]["total"] == len(d["close"]["steps"]) == 5
    assert d["release"]["in_force"] == d_full["release"]["in_force"]
    # the seed button lives on the register page, which stays
    assert next(i for i in d["items"] if i["key"] == "master_seed")["go"] == {"tab": "bomcenter", "pane": "ing"}
    assert next(i for i in d_full["items"] if i["key"] == "master_seed")["go"]["pane"] == "overview"


def test_no_item_of_stock_mode_opens_a_hidden_recipe_page(udon):
    hidden = {"rec", "prep", "rel", "rcheck", "overview"}
    CS.set_mode(udon, "t", "stock")
    d = TD.build(udon, M, None, None, today=TODAY)
    bad = [(i["key"], i["go"]) for i in d["items"] + d["close"]["steps"] if i["go"].get("tab") == "bomcenter" and i["go"].get("pane") in hidden]
    assert not bad, bad


def test_nav_badges_follow_the_todo_list(udon):
    CS.set_mode(udon, "t", "full")
    full = NB.build(udon, "analyst", auth.ROLE_MODULES["analyst"], today=TODAY)
    CS.set_mode(udon, "t", "stock")
    stock = NB.build(udon, "analyst", auth.ROLE_MODULES["analyst"], today=TODAY)
    assert not [k for k in stock["pages"] if k in ("bomcenter:rec", "bomcenter:prep", "bomcenter:rel", "bomcenter:rcheck", "bomcenter:overview")]
    assert any(k in full["pages"] for k in ("bomcenter:rec", "bomcenter:rel", "bomcenter:rcheck", "bomcenter:overview"))   # the full list does count them
    assert stock["total"] <= full["total"]
    CS.set_mode(udon, "t", "stock")


def test_the_reminders_name_no_recipe_item():
    """The morning and cutoff texts read these keys only (bom_center/reminders.py): none of them is a recipe key."""
    import re
    src = open(TD.__file__.replace("todo.py", "reminders.py"), encoding="utf-8").read()
    named = set(re.findall(r'(?:get|\[)\("([a-z_]+)"', src)) | set(re.findall(r'\.get\("([a-z_]+)"\)', src))
    assert not (named & set(TD.RECIPE_KEYS)), named & set(TD.RECIPE_KEYS)
    assert "queue_pending" in src and "claims_pending" in src


# ── the ingredient register ──────────────────────────────────────────────────
class _Req:
    def __init__(self):
        self.cookies = {}


def _guard_with(monkeypatch, modules):
    from routers import bom_center as R
    monkeypatch.setattr(R, "_modules", lambda request: set(modules))
    return R


def test_register_write_guard_by_mode_and_module(client, monkeypatch):
    import main
    client.get("/api/bom-center/mode")
    innovation = auth.ROLE_MODULES["innovation"]
    assert "bom.center" not in innovation
    R = _guard_with(monkeypatch, innovation)
    c = sqlite3.connect(str(main.DB_PATH))
    try:
        c.execute("DELETE FROM app_settings WHERE key='bc_mode'")           # absent = stock
        c.commit()
        with pytest.raises(HTTPException) as ei:
            R._register_write_guard(_Req())
        assert ei.value.status_code == 403 and ei.value.detail == DETAIL
        R2 = _guard_with(monkeypatch, auth.ROLE_MODULES["analyst"])
        R2._register_write_guard(_Req())                                      # the analyst holds bom.center: fine in stock mode
        c.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES('bc_mode', 'full')")
        c.commit()
        R2._register_write_guard(_Req())                                      # … and in full mode
        _guard_with(monkeypatch, innovation)._register_write_guard(_Req())    # full mode: the guard does not add a refusal
    finally:
        c.execute("DELETE FROM app_settings WHERE key='bc_mode'")
        c.commit()
        c.close()


def test_every_ingredient_master_write_endpoint_carries_the_guard(client, monkeypatch):
    """Stock mode, a caller without bom.center: all four register writes answer 403 with the fixed text; full mode lets them through."""
    import main
    client.get("/api/bom-center/mode")
    _guard_with(monkeypatch, auth.ROLE_MODULES["innovation"])
    c = sqlite3.connect(str(main.DB_PATH))
    c.execute("INSERT OR REPLACE INTO bc_ingredient(code, name_th, kind, base_unit, recv_unit, qty_per_recv, active)"
              " VALUES('SM-1', 'ทดสอบโหมด', 'raw', 'กรัม', 'แพ็ค', 500, 1)")
    c.execute("DELETE FROM app_settings WHERE key='bc_mode'")
    c.commit()
    calls = [lambda: client.post("/api/bom-center/ingredients", json=[{"code": "SM-1", "note": "x"}]),
             lambda: client.post("/api/bom-center/ingredients/seed?dry_run=true"),
             lambda: client.post("/api/bom-center/ingredients/import?dry_run=true", files={"file": ("a.csv", b"code\nSM-1\n", "text/csv")}),
             lambda: client.post("/api/bom-center/ingredients/SM-1/aliases", json={"action": "register", "alias": "SM-1"})]
    try:
        for call in calls:
            r = call()
            assert r.status_code == 403 and r.json()["detail"] == DETAIL, (r.status_code, r.text)
        c.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES('bc_mode', 'full')")
        c.commit()
        r = client.post("/api/bom-center/ingredients", json=[{"code": "SM-1", "note": "x"}])
        assert r.status_code == 200, r.text                                   # full mode: the edit goes through
        assert c.execute("SELECT note FROM bc_ingredient WHERE code='SM-1'").fetchone()[0] == "x"
        for call in calls[1:]:
            assert call().status_code != 403
        _guard_with(monkeypatch, auth.ROLE_MODULES["analyst"])
        c.execute("DELETE FROM app_settings WHERE key='bc_mode'")             # stock mode again: the analyst still writes
        c.commit()
        assert client.post("/api/bom-center/ingredients", json=[{"code": "SM-1", "note": "y"}]).status_code == 200
    finally:
        c.execute("DELETE FROM bc_ingredient WHERE code='SM-1'")
        c.execute("DELETE FROM app_settings WHERE key='bc_mode'")
        c.commit()
        c.close()


def test_innovation_login_is_refused_the_register_write_over_http(world):
    """With the real session: the kitchen holds no bom.center, so stock mode and full mode both refuse it (the register is the analyst's)."""
    r = world["innovation"].post("/api/bom-center/ingredients", json=[{"code": "NOPE-1", "note": "x"}], headers={"X-Upload-Token": TOKEN})
    assert r.status_code == 403
    r = world["analyst"].post("/api/bom-center/ingredients", json=[{"code": "NOPE-1", "note": "x"}], headers={"X-Upload-Token": TOKEN})
    assert r.status_code != 403                                                    # the analyst passes the guard (400/200 is the edit's own answer)
