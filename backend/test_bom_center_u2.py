# -*- coding: utf-8 -*-
"""PR-U2 (owner 2026-10-22 "เริ่ม U2 ต่อ") - central roles, 30-branch defaults, unit leftovers of U1.

The promises:
  1. the write-code policy is kept, the flow is fixed: /api/auth/me says whether this session's saves need the code (`write_gated`),
     so a browser that holds none never calls /api/auth/token-check to be refused; while locked, the question before a save
     (uiConfirm / uiPrompt) carries the unlock field itself and checks the code before it answers - one dialog, one press;
  2. the default scope is every ACTIVE branch, not the four pilot branches: the "create the branch users" task, the centre's
     branch duties, the readiness list and the delivery-calendar page (the pilot four stay an optional filter);
  3. ONE data-quality task for the analyst: while the impact-ranked backlog has rows, "ข้อมูลตั้งต้นที่ยังว่าง" is not a second
     task - its blank-field lines ride on the backlog card, which opens the backlog card itself; the badges count it once;
  4. the phone header shows the person's display name, never the login ID;
  5. a department whose every open page is already a page of the person's own department adds no tile (คลัง no longer sees
     ประสานงานขาย for คิวใบสั่งซื้อ alone);
  6. a line with no pack size is typed in กิโลกรัม / ลิตร on every box that keys it and saved in base units with no drift
     (48.6 กิโลกรัม -> 48600 กรัม); the branch order row writes its system stock in the same unit as the order.
"""
import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth
from bom_center import claims as CL
from bom_center import nav_badges as NB
from bom_center import pilot as PL
from bom_center import todo as TD
from test_bom_center_s4b import con  # noqa: F401  (five weeks of sales at branch 1, 1 Sep - 5 Oct 2026)

REPO = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
TOKEN = "u2-write-token"
MON = date(2026, 10, 5)


def _src(rel):
    return (REPO / rel).read_text(encoding="utf-8")


def _fn(src, name):
    return re.search(r"((?:async )?function %s\([\s\S]*?\n})" % re.escape(name), src).group(1)


def _node(code):
    return json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", check=True).stdout)


# ── 1 · the unlock code ──────────────────────────────────────────────────────
def test_auth_me_says_whether_saves_need_the_code(client, monkeypatch):
    monkeypatch.delenv("OSHINEI_SEED_TOKEN", raising=False)
    assert client.get("/api/auth/me").json()["write_gated"] is False         # local dev: no code configured
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    assert client.get("/api/auth/me").json()["write_gated"] is True


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    yield


def test_a_central_role_is_gated_and_a_branch_login_is_not(client, auth_on):
    import main
    from core import get_db
    with get_db() as c:
        name = c.execute("SELECT name FROM branches WHERE name<>'ALL' ORDER BY branch_id LIMIT 1").fetchone()[0]
        if not auth.get_user(c, "u2_branch"):
            auth.create_user(c, "u2_branch", "Abcdefgh1234!", "branch", branch=name)
        c.commit()
    ss = TestClient(main.app)
    assert ss.post("/api/auth/login", json={"username": "salessupport1", "password": auth.DEFAULT_PASSWORD}).status_code == 200
    me = ss.get("/api/auth/me").json()
    assert me["role"] == "salessupport" and me["write_gated"] is True
    br = TestClient(main.app)
    assert br.post("/api/auth/login", json={"username": "u2_branch", "password": "Abcdefgh1234!"}).status_code == 200
    assert br.get("/api/auth/me").json()["write_gated"] is False                # its page shows no lock and no unlock field
    with get_db() as c:                                                          # the shared fixture DB is put back (debug mantra 64)
        c.execute("DELETE FROM app_user WHERE username='u2_branch'")
        c.commit()


_STUB = """
const _ls = {}; const localStorage = { getItem: k => (k in _ls ? _ls[k] : null), setItem: (k, v) => { _ls[k] = String(v); }, removeItem: k => { delete _ls[k]; } };
const window = {}; const API = ''; let calls = 0;
window._rawFetch = async (url, init) => { calls++; const ok = ((init || {}).headers || {})['X-Upload-Token'] === 'good';
  return { ok, status: ok ? 200 : 403, headers: { get: () => (ok ? null : 'token') }, json: async () => ({ ok: true, gated: true }) }; };
function _renderLockChip() {}
window.writeToken = { gated: null, unlocked: null };
"""


@needs_node
def test_no_token_check_without_a_stored_code_and_the_unlock_field_while_locked():
    core = _src("js/01-core.js")
    wl = re.search(r"(window\.writesLocked = function[\s\S]*?\n};)", core).group(1)
    ck = re.search(r"(window\.checkWriteToken = async function[\s\S]*?\n};)", core).group(1)
    pre = _STUB + "\n".join([_fn(core, "_writeTokenState"), _fn(core, "_writeTokenFromAuth"), wl, ck, _fn(core, "verifyWriteToken")])
    out = _node(pre + """
(async () => {
  const r = [];
  window.AUTH = { write_gated: true }; _writeTokenFromAuth();
  await verifyWriteToken();                                   // no code stored: nothing is asked of the server
  r.push(calls, window.writeToken.unlocked, window.writesLocked());
  r.push(await window.checkWriteToken('bad'), window.writesLocked());       // a wrong code is not stored
  r.push(await window.checkWriteToken('good'), window.writesLocked(), window.writeToken.unlocked, localStorage.getItem('oshinei_upload_token'));
  window.writeToken = { gated: null, unlocked: null }; window.AUTH = { write_gated: false }; _writeTokenFromAuth();
  r.push(window.writeToken.gated, window.writesLocked());     // a branch login: never locked
  console.log(JSON.stringify(r));
})();""")
    assert out == [0, False, True, False, True, True, False, True, "good", False, False]


def test_the_question_before_a_save_carries_the_unlock_field():
    core = _src("js/01-core.js")
    confirm = re.search(r"window\.uiConfirm = \(message, opt\) => \{[\s\S]*?\n\};", core).group(0)
    prompt = re.search(r"window\.uiPrompt = \(message, value, opt\) => \{[\s\S]*?\n\};", core).group(0)
    dialog = re.search(r"window\.uiDialog = function \(opt\) \{[\s\S]*?\n\};", core).group(0)
    assert "o.write !== false && window.writesLocked()" in confirm and "o.unlock = true" in confirm
    assert "unlock: o.write !== false && window.writesLocked()" in prompt
    # the field is checked inside the same dialog: a wrong code stays in the dialog, a right one lets the save go on the first press
    assert 'data-el="tok"' in dialog and "await window.checkWriteToken(t)" in dialog and "รหัสปลดล็อกไม่ถูกต้อง" in dialog
    # the page no longer asks token-check at load without a code; the fetch wrapper still retries a refused save once
    assert "if (!tok && window.writeToken.gated !== null)" in _fn(core, "verifyWriteToken")
    assert "_writeTokenFromAuth();" in _fn(core, "bootstrapAuth")
    # presets kept in this browser are not saves
    for rel in ("js/05-pnl-settings.js", "js/06-features.js"):
        assert "{ write: false }" in _src(rel), rel


# ── 2 · every active branch ──────────────────────────────────────────────────
def test_the_branch_users_task_covers_every_active_branch(con):  # noqa: F811
    names = [b["name"] for b in PL.active_branches(con)]
    assert len(names) >= 2
    it = {i["key"]: i for i in TD.build(con, None, None, None, today=MON)["items"]}["branch_account"]
    assert it["title"] == "สร้างผู้ใช้ของสาขา" and it["status"] == "todo" and it["count"] == len(names)
    assert "นำร่อง" not in it["title"] + it["detail"]
    auth.init_auth(con)
    for i, n in enumerate(names):
        auth.create_user(con, f"u2_branch_{i}", "Abcdefgh1234!", "branch", branch=n)
    it = {i["key"]: i for i in TD.build(con, None, None, None, today=MON)["items"]}["branch_account"]
    assert it["status"] == "done" and it["detail"] == "ทุกสาขามีผู้ใช้แล้ว"


def test_the_centre_reads_every_active_branch_and_an_inactive_one_is_left_out(con):  # noqa: F811
    ids = {b["branch_id"] for b in PL.active_branches(con)}
    d = TD.build(con, None, None, None, today=MON)
    assert {i["branch_id"] for i in d["items"] if i["key"].startswith("issue_today")} == ids
    off = sorted(ids)[-1]
    con.execute("UPDATE branches SET active=0 WHERE branch_id=?", (off,))
    d = TD.build(con, None, None, None, today=MON)
    assert off not in {i["branch_id"] for i in d["items"] if i["key"].startswith("issue_today")}
    assert off not in {b["branch_id"] for b in PL.readiness(con)["branches"]}


def test_the_names_of_thirty_branches_stay_one_line():
    assert TD._names_th(["ก", "ข"]) == "ก · ข"
    assert TD._names_th([f"สาขา{i}" for i in range(30)]) == "สาขา0 · สาขา1 · สาขา2 · สาขา3 · สาขา4 และอีก 25 สาขา"


def test_the_screens_default_to_every_branch():
    bmc = _src("js/13-bom-center.js")
    assert "let _bmcCalPilotOnly = false;" in bmc and "เฉพาะสาขานำร่อง (${pilot.length})" in bmc       # the optional filter stays
    assert "ความพร้อมและผลนำร่อง 4 สาขา" not in _src("tabs/54-orderhub.html")
    assert "สาขานำร่องมีผู้ใช้" not in _src("backend/bom_center/todo.py")


# ── 3 · one data-quality task ────────────────────────────────────────────────
_BACKLOG = {"top": [{"kind": "unit_missing", "code": "X"}], "window": None, "counts": {"unit_missing": 188}, "total": 188}


def test_the_master_gaps_ride_on_the_backlog_card():
    gaps = TD._e("master_gaps", 35, "setup", "ข้อมูลตั้งต้นที่ยังว่าง", "todo", "", count=188, owner=("bom.center",),
                 owner_th="ฝ่ายวิเคราะห์", go={"tab": "bomcenter", "pane": "ing"}, gaps=[{"key": "no_moq", "count": 188}])
    bk = TD._e("backlog", 36, "setup", "งานแก้ข้อมูลค้างเรียงตามผลกระทบ (188 รายการ)", "todo", count=188, owner=("bom.center",),
               owner_th="ฝ่ายวิเคราะห์", go={"tab": "bomcenter", "pane": "overview", "anchor": "bmcBacklogCard"})
    out = TD._merge_gaps_into_backlog([gaps], [bk])
    assert [i["key"] for i in out] == ["backlog"] and out[0]["gaps"] == gaps["gaps"]
    # no backlog: the gaps card stays the task it was
    assert TD._merge_gaps_into_backlog([gaps], []) == [gaps]


def test_the_analyst_sees_the_work_once(con, monkeypatch):  # noqa: F811
    import bom_center.backlog as BL
    from bom_center import close_settings as CS
    CS.set_mode(con, "test", "full")                   # PR-S1: the recipe backlog card is part of the full list; stock mode (the default) leaves it out
    monkeypatch.setattr(BL, "build", lambda c, top=10: dict(_BACKLOG))
    monkeypatch.setattr(TD, "_master_gaps_item", lambda c: TD._e(
        "master_gaps", 35, "setup", "ข้อมูลตั้งต้นที่ยังว่าง", "todo", "", count=188, owner=("bom.center",), owner_th="ฝ่ายวิเคราะห์",
        go={"tab": "bomcenter", "pane": "ing", "extra": {"gap": "no_moq"}}, gaps=[{"key": "no_moq", "count": 188}]))
    d = TD.build(con, None, None, None, today=MON, viewer_role="analyst")
    keys = [i["key"] for i in d["items"]]
    assert "master_gaps" not in keys and keys.count("backlog") == 1
    bk = next(i for i in d["items"] if i["key"] == "backlog")
    assert bk["go"] == {"tab": "bomcenter", "pane": "overview", "anchor": "bmcBacklogCard"} and bk["gaps"]
    mine = NB.mine_open(d, "analyst", auth.modules_for_role("analyst"))
    assert [i["key"] for i in mine].count("backlog") == 1 and "master_gaps" not in [i["key"] for i in mine]
    pages = NB.build(con, "analyst", auth.modules_for_role("analyst"), None, today=MON)["pages"]
    assert pages.get("bomcenter:overview") == sum(1 for i in mine if NB.page_key(i["go"]) == "bomcenter:overview")


# ── 4 · the phone header ─────────────────────────────────────────────────────
@needs_node
def test_the_phone_header_shows_the_name_never_the_login():
    mob, lnch = _src("js/09c-mobile.js"), _src("js/18-launcher.js")
    pre = ("const ROLE_LABEL_TH = { warehouse: 'คลัง (Warehouse)', management: 'ผู้บริหาร' };\nconst window = {};\n"
           + _fn(lnch, "lnchWho") + "\n" + _fn(mob, "_phUserId"))
    out = _node(pre + """
const r = [];
window.AUTH = { enabled: true, username: 'warehouse1', role: 'warehouse', display_name: 'คลัง' }; r.push(_phUserId());
window.AUTH = { enabled: true, username: 'management1', role: 'management', names: {} }; r.push(_phUserId());
window.AUTH = { enabled: true, username: 'warehouse1', role: 'warehouse', display_name: 'สมชาย ใจดี' }; r.push(_phUserId());
window.AUTH = { enabled: false }; r.push(_phUserId());
console.log(JSON.stringify(r));""")
    assert out == ["คลัง", "ผู้บริหาร", "สมชาย ใจดี", ""]
    assert "warehouse1" not in out and "management1" not in out


# ── 5 · no extra department tile ─────────────────────────────────────────────
@needs_node
def test_the_warehouse_sees_no_sales_support_tile():
    core, lnch = _src("js/01-core.js"), _src("js/18-launcher.js")
    deps = re.search(r"(const DEPARTMENTS = \[[\s\S]*?\n\];)", core).group(1)
    pre = (deps + "\nlet ROLE = 'warehouse', OPEN = new Set(['orderhub|queue']);\nfunction _deptRole() { return ROLE; }\n"
           "function deptPageAllowed(p) { return OPEN.has(p.tab + '|' + (p.pane || '')); }\n" + _fn(lnch, "deptVisible"))
    out = _node(pre + """
const r = [deptVisible().map(x => x.dept.key)];
OPEN = new Set(['orderhub|queue', 'orderhub|claims']); r.push(deptVisible().map(x => x.dept.key));      // a page of its own there: the tile shows
ROLE = 'management'; OPEN = new Set(['bomcenter|rel', 'bomcenter|price', 'mgtreport']); r.push(deptVisible().map(x => x.dept.key));
console.log(JSON.stringify(r));""")
    assert out[0] == ["wh"]
    assert out[1] == ["wh", "ss"]
    assert out[2][0] == "mgmt" and "ana" in out[2]                    # another department with pages of its own stays


# ── 6 · units of a line with no pack size ────────────────────────────────────
@needs_node
def test_typing_in_kilograms_round_trips_in_grams():
    bmc = _src("js/13-bom-center.js")
    pre = "\n".join(_fn(bmc, n) for n in ("bmcTypeF", "bmcTypeUnit", "bmcToTyped", "bmcFromTyped"))
    out = _node(pre + """
const kg = { no_pack: true, show_unit: 'กิโลกรัม', show_factor: 1000 }, pack = { no_pack: false, show_factor: 1 };
const f = bmcTypeF(kg), r = [];
r.push(f, bmcTypeF(pack), bmcTypeUnit(kg, 'กรัม'), bmcTypeUnit(pack, 'แพ็ค'));
r.push(bmcFromTyped('48.6', f), bmcFromTyped('48,6', f), bmcFromTyped('62.995', f), bmcFromTyped('0.001', f), bmcFromTyped('3', bmcTypeF(pack)));
r.push(bmcToTyped(48600, f), bmcToTyped(18558, f), bmcToTyped('', f), bmcToTyped(null, f));
// every stored gram figure survives box -> save unchanged
let drift = 0; for (let g = 0; g <= 200000; g += 7.3) { const s = Math.round(g * 1000) / 1000; if (Math.abs(bmcFromTyped(bmcToTyped(s, f), f) - s) > 1e-9) drift++; }
// every typed kilogram figure with up to 3 decimals is the exact gram figure
for (let i = 0; i <= 100000; i += 37) { const t = (i / 1000).toFixed(3); if (bmcFromTyped(t, f) !== Math.round(i)) drift++; }
r.push(drift, Number.isNaN(bmcFromTyped('abc', f)));
console.log(JSON.stringify(r));""")
    assert out == [1000, 1, "กิโลกรัม", "แพ็ค", 48600, 48600, 62995, 1, 3, "48.6", "18.558", "", "", 0, True]


@needs_node
def test_the_order_row_writes_its_system_stock_in_the_order_unit():
    bmc, brn = _src("js/13-bom-center.js"), _src("js/14-branch.js")
    pre = ("function _bmcNum(v, d = 2) { return Number(v).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: d }); }\n"
           + "\n".join([_fn(bmc, "bmcTypeF"), _fn(brn, "_brnNice"), _fn(brn, "_brnUnitW"), _fn(brn, "_brnSysTxt"), _fn(brn, "_brnUnitLabel"),
                        _fn(brn, "_brnOrdF"), _fn(brn, "_brnOrdUnitShow"), _fn(brn, "_brnOrdSysTxt")]))
    out = _node(pre + """
const r = { no_pack: true, show_unit: 'กิโลกรัม', show_factor: 1000, order_unit: 'base', base_unit: 'กรัม', system_unit: 'กรัม', count_unit: 'กรัม' };
console.log(JSON.stringify([_brnOrdSysTxt(Object.assign({}, r, { system_qty: -62995 })), _brnOrdSysTxt(Object.assign({}, r, { system_qty: 1250 })),
  _brnOrdSysTxt({ order_unit: 'pack', recv_unit: 'แพ็ค', base_unit: 'กรัม', system_unit: 'แพ็ค', system_qty: 4 })]));""")
    assert out == ["-63 กิโลกรัม", "1.25 กิโลกรัม", "4 แพ็ค"]          # _bmcNum drops a trailing .0 ("-63.0" in a Thai locale prints "-63")


def test_every_box_that_keys_a_no_pack_line_converts():
    ohb, brn = _src("js/15-orderhub.js"), _src("js/14-branch.js")
    assert "bmcFromTyped(raw, bmcTypeF(l))" in _fn(ohb, "_ohbPRead")                      # ส่งจริง of the warehouse
    assert "bmcToTyped(sent, bmcTypeF(l))" in ohb and "bmcToTyped(l.confirmed_units ?? '', bmcTypeF(l))" in _fn(ohb, "ohbFillAll")
    assert "bmcFromTyped(el.value, bmcTypeF(l))" in ohb                                    # the approved quantity of a claim
    assert "bmcFromTyped(raw, _brnRcvF(l))" in _fn(brn, "_brnRcvTyped")                   # รับจริง of the branch
    assert "bmcFromTyped(raw, bmcTypeF(l))" in brn                                        # เคลม of the branch
    assert "_brnOrdSysTxt(r)" in brn


def test_claim_lines_carry_the_typing_unit(con):  # noqa: F811
    con.execute("INSERT INTO bc_order(order_id, branch_id, cycle_date, round, status) VALUES(901, 1, '2026-10-01', '1', 'received')")
    con.execute("INSERT INTO bc_order_line(order_id, ingredient_code, order_unit, confirmed_units, processed_units, received_units)"
                " VALUES(901, '13-01-009', 'base', 48600, 48600, 48600)")
    con.execute("INSERT INTO bc_order_line(order_id, ingredient_code, order_unit, confirmed_units, processed_units, received_units)"
                " VALUES(901, '02-01-001', 'pack', 3, 3, 3)")
    lines = {ln["code"]: ln for ln in CL.claimable(con, 1, 901)["lines"]}
    assert lines["13-01-009"]["no_pack"] is True and lines["13-01-009"]["show_factor"] == 1000.0
    assert lines["13-01-009"]["show_unit"] == "กิโลกรัม"
    assert lines["02-01-001"]["no_pack"] is False and lines["02-01-001"]["show_factor"] == 1.0
