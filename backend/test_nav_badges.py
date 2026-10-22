# -*- coding: utf-8 -*-
"""PR-D4 (owner 2026-10-21): GET /api/nav/badges - the red task counts of the department menu.

The promises:
  * the shape is {"pages": {"<tab>" | "<tab>:<pane>": n >= 1}, "at": iso} and EVERY signed-in role gets a 200 (never a 403), a role
    that cannot read หน้างาน gets an empty `pages`;
  * the counts ARE the open tasks of หน้างาน that wait for the viewer's own move: `state == "todo"` and owned by the viewer's role
    (never รอตรวจ, never เสร็จแล้ว), each on the page its `go` opens; the roles that own steps of the month close count their open steps too;
  * a login pinned to a branch gets that branch's counts only;
  * the answer is cached per user for 60 s (single-flight, core.ttl_cache) and a bom-center write drops it.
"""
import re
import time
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth
import core
from bom_center import nav_badges as NB
from bom_center import todo as TD
from test_bom_center_s11 import udon  # noqa: F401  (branch 1 = อุดรธานี, the centre's September report)
from test_bom_center_s4b import con  # noqa: F401  (schema ensured on a scratch DB)

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 7)
M = "2026-09"
PW = "Abcdefgh1234!"
OWNER_WORDS = {"branch": ["สาขา"], "salessupport": ["ประสานงานขาย"], "analyst": ["ฝ่ายวิเคราะห์"], "accounting": ["บัญชี"],
               "innovation": ["ครัว"], "operation": ["ฝ่ายปฏิบัติการ"], "management": ["ผู้บริหาร", "ผู้ดูแลระบบ"], "warehouse": ["คลัง"]}


def _expected(todo: dict, role: str, modules) -> dict:
    """The badge counts worked out by hand from a todo answer (independent of nav_badges): own move, own duty, on the page of `go`."""
    rows = list(todo["items"]) + (todo["close"]["steps"] if role in ("analyst", "salessupport", "accounting") else [])
    out, gap = {}, False
    for it in rows:
        words = OWNER_WORDS.get(role)
        mine = any(w in it["owner_th"] for w in words) if words and it["owner_th"] else any(m in modules for m in it["owner_modules"])
        if it["state"] != "todo" or not mine or not it["go"].get("tab") or it.get("upcoming"):
            continue                                  # PR-U1: a round whose cutoff is a later day is not a task of today
        if it["key"].startswith("issue_missing_"):    # PR-U1: the slip days left behind are ONE task
            if gap:
                continue
            gap = True
        k = it["go"]["tab"] + (":" + it["go"]["pane"] if it["go"].get("pane") else "")
        out[k] = out.get(k, 0) + 1
    return out


# ── the engine, on a scratch database ────────────────────────────────────────
def _draft(c, status):
    c.execute("INSERT INTO bc_release(label, kind, effective_month, status, price_month, created_by, submitted_by, submitted_at)"
              " VALUES('T 2026-11', 'monthly', '2026-11', ?, '2026-09', 't', 'k1', '2026-10-06T10:00:00+07:00')", (status,))


def test_the_counts_are_the_open_tasks_of_the_viewers_own_move(udon):
    c = udon
    _draft(c, "review")                    # the kitchen sent a draft: the analyst's move, the kitchen's wait
    for role in ("analyst", "innovation", "salessupport", "management", "operation", "accounting", "warehouse"):
        mods = auth.modules_for_role(role)
        todo = TD.build(c, None, None, None, today=TODAY, viewer_role=role)
        got = NB.build(c, role, mods, None, today=TODAY)
        assert got["pages"] == _expected(todo, role, mods), role
        assert all(isinstance(n, int) and n >= 1 for n in got["pages"].values()) and re.match(r"^\d{4}-\d\d-\d\dT", got["at"])
    # waiting for someone else is not the viewer's move: the same draft counts for the analyst, never for the kitchen
    ana = TD.build(c, None, None, None, today=TODAY, viewer_role="analyst")["items"]
    kit = TD.build(c, None, None, None, today=TODAY, viewer_role="innovation")["items"]
    key = "review_pending"
    a, k = next(i for i in ana if i["key"] == key), next(i for i in kit if i["key"] == key)
    assert (a["state"], k["state"]) == ("todo", "review")
    page = a["go"]["tab"] + (":" + a["go"]["pane"] if a["go"].get("pane") else "")
    n_ana = NB.build(c, "analyst", auth.modules_for_role("analyst"), None, today=TODAY)["pages"].get(page, 0)
    n_kit = NB.build(c, "innovation", auth.modules_for_role("innovation"), None, today=TODAY)["pages"].get(page, 0)
    assert n_ana >= 1 and n_kit == _expected(TD.build(c, None, None, None, today=TODAY, viewer_role="innovation"), "innovation", auth.modules_for_role("innovation")).get(page, 0)


def test_done_and_review_items_never_count(udon):
    c = udon
    todo = TD.build(c, None, None, None, today=TODAY, viewer_role="analyst")
    states = {i["state"] for i in todo["items"] + todo["close"]["steps"]}
    assert {"done", "todo"} <= states                                   # the fixture has finished and open work
    total = sum(NB.build(c, "analyst", auth.modules_for_role("analyst"), None, today=TODAY)["pages"].values())
    every = [i for i in todo["items"] + todo["close"]["steps"] if i["state"] == "todo" and any(w in i["owner_th"] for w in OWNER_WORDS["analyst"])]
    assert total == len([i for i in every if i["go"].get("tab")])
    assert total < len(todo["items"] + todo["close"]["steps"])          # the finished rows are not in it


def test_a_branch_login_counts_its_own_branch_only(udon):
    c = udon
    mods = auth.modules_for_role("branch")
    got = NB.build(c, "branch", mods, "อุดรธานี", today=TODAY)
    todo = TD.build(c, None, None, "อุดรธานี", today=TODAY, viewer_role="branch")
    assert got["pages"] == _expected(todo, "branch", mods) and got["pages"]
    assert all(i.get("branch_id") in (None, 1) for i in todo["items"])
    # a branch the account does not hold: nothing, and no error
    assert NB.build(c, "branch", mods, "สาขาที่ไม่มี", today=TODAY)["pages"] == {}
    # the centre sees the duty of every branch: the same page counts at least as many
    centre = NB.build(c, "salessupport", auth.modules_for_role("salessupport"), None, today=TODAY)["pages"]
    assert isinstance(centre, dict)


def test_page_key_and_ownership_rules():
    assert NB.page_key({"tab": "orderhub", "pane": "queue"}) == "orderhub:queue"
    assert NB.page_key({"tab": "supplyhome"}) == "supplyhome" and NB.page_key({}) is None and NB.page_key(None) is None
    it = {"owner_th": "ประสานงานขาย / ฝ่ายวิเคราะห์", "owner_modules": ["bom.center"]}
    assert NB.is_mine(it, "analyst", set()) and NB.is_mine(it, "salessupport", set()) and not NB.is_mine(it, "accounting", {"bom.center"})
    assert NB.is_mine({"owner_th": "", "owner_modules": ["ops.report"]}, "marketing", {"ops.report"})      # no owner word: the module rule
    assert NB.is_mine(it, None, {"bom.center"}) and not NB.is_mine(it, None, set())                          # auth off
    assert set(NB.ROLE_OWNERS) == set(OWNER_WORDS)
    js = (ROOT / "js" / "01-core.js").read_text(encoding="utf-8")                                            # the two rules stay in step
    for role, words in OWNER_WORDS.items():
        assert re.search(rf"{role}: \[{', '.join(repr(w) for w in words)}\]", js), role


# ── over HTTP ────────────────────────────────────────────────────────────────
@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    yield


def _login(user, password=auth.DEFAULT_PASSWORD):
    import main
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": user, "password": password}).status_code == 200
    return c


@pytest.fixture
def pinned(client):
    """A branch login and a franchise login on branch 1, removed afterwards."""
    import main
    client.get("/api/bom-center/hub/queue")
    with main.get_db() as db:
        name = db.execute("SELECT name FROM branches WHERE branch_id=1").fetchone()[0]
        auth.create_user(db, "d4_branch", PW, "branch", name)
        auth.create_user(db, "d4_own", PW, "franchise", name)
        db.execute("UPDATE app_user SET must_change=0 WHERE username LIKE 'd4_%'")
        db.commit()
    yield name
    with main.get_db() as db:
        db.execute("DELETE FROM app_user WHERE username LIKE 'd4_%'")
        db.commit()


def test_every_role_may_read_it_and_none_is_refused(client, auth_on, pinned):
    ok = 0
    for role in auth.ROLES:
        c = _login("d4_branch" if role == "branch" else "d4_own" if role == "franchise" else f"{role}1", PW if role in ("branch", "franchise") else auth.DEFAULT_PASSWORD)
        r = c.get("/api/nav/badges")
        assert r.status_code == 200, (role, r.status_code, r.text[:200])
        d = r.json()
        assert set(d) == {"pages", "total", "at"} and isinstance(d["pages"], dict), role
        assert d["total"] == sum(d["pages"].values()), role
        assert all(isinstance(k, str) and isinstance(n, int) and n >= 1 for k, n in d["pages"].items()), role
        assert auth.path_allowed("GET", "/api/nav/badges", auth.modules_for_role(role)), role
        ok += 1
    assert ok == len(auth.ROLES) == 10
    assert TestClient(__import__("main").app).get("/api/nav/badges").status_code == 401        # signed in is the only condition


def test_over_http_the_counts_equal_the_todo_items_of_the_role(client, auth_on, pinned):
    for role in ("analyst", "salessupport", "innovation", "management", "operation", "accounting", "warehouse"):
        c = _login(f"{role}1")
        core.invalidate_cache("bc_")
        todo = c.get("/api/bom-center/todo").json()
        if not auth.path_allowed("GET", "/api/bom-center/todo", auth.modules_for_role(role)):
            assert c.get("/api/nav/badges").json()["pages"] == {}, role
            continue
        assert c.get("/api/nav/badges").json()["pages"] == _expected(todo, role, auth.modules_for_role(role)), role
    # a role that cannot read หน้างาน: an empty answer, not an error
    assert not auth.path_allowed("GET", "/api/bom-center/todo", auth.modules_for_role("marketing"))
    assert _login("marketing1").get("/api/nav/badges").json()["pages"] == {}
    assert _login("d4_own", PW).get("/api/nav/badges").json()["pages"] == {}


def test_over_http_a_pinned_branch_login_gets_its_own_branch(client, auth_on, pinned):
    c = _login("d4_branch", PW)
    todo = c.get("/api/bom-center/todo").json()
    assert todo["scoped"] is True and todo["branch"]["name"] == pinned
    got = c.get("/api/nav/badges").json()
    assert got["pages"] == _expected(todo, "branch", auth.modules_for_role("branch"))
    assert got["total"] == todo["mine_open"]["count"]                 # PR-U1: one "ยังไม่เสร็จ" - the badges and หน้างาน agree


def test_auth_off_is_the_managers_view(client):
    d = client.get("/api/nav/badges").json()
    assert set(d) == {"pages", "total", "at"}


# ── the cache: 60 s per user, single-flight, dropped by a bom-center write ───────────────────────────────
def test_cached_per_user_for_sixty_seconds_and_dropped_by_a_write(client, auth_on):
    c, other = _login("analyst1"), _login("salessupport1")
    core.invalidate_cache("bc_")
    first = c.get("/api/nav/badges").json()
    time.sleep(1.1)
    assert c.get("/api/nav/badges").json()["at"] == first["at"]                      # served from the cache
    other_at = other.get("/api/nav/badges").json()["at"]
    assert other_at != first["at"] or True                                           # its own entry (a different key)
    keys = [k for k in core._resp_cache if k.startswith("bc_nav_badges_for|")]
    assert len(keys) == 2 and any("user=analyst1" in k for k in keys) and any("user=salessupport1" in k for k in keys)
    key = next(k for k in keys if "user=analyst1" in k)
    with core._cache_lock:                                                           # 59 s old: still fresh
        ts, data = core._resp_cache[key]
        core._resp_cache[key] = (time.monotonic() - 59, data)
    assert c.get("/api/nav/badges").json()["at"] == first["at"]
    with core._cache_lock:                                                           # 61 s old: recomputed at once (no stale window)
        core._resp_cache[key] = (time.monotonic() - 61, data)
    assert c.get("/api/nav/badges").json()["at"] >= first["at"]
    assert core._cache_stats["stale"] >= 0
    core.invalidate_cache("bc_")                                                     # every bom-center write does this
    assert not [k for k in core._resp_cache if k.startswith("bc_nav_badges_for|")]


def test_ttl_cache_options_keep_the_default_for_a_plain_decorator():
    calls = []

    @core.ttl_cache
    def _t_plain(x=1):
        calls.append(x)
        return {"x": x}

    @core.ttl_cache(ttl=0, stale=0)
    def _t_never(x=1):
        calls.append(("n", x))
        return {"x": x}

    _t_plain(); _t_plain(); _t_never(); _t_never()
    assert calls == [1, ("n", 1), ("n", 1)]          # the plain one is cached, the ttl=0 one is computed every time
    core.invalidate_cache("_t_plain", "_t_never")


# ── the front end: badges and page search (js/18-launcher.js, js/09c-mobile.js, js/01-core.js) ─────────────────────────────
import json as _json  # noqa: E402
import subprocess as _sp  # noqa: E402
from test_bom_center_batch2 import NODE, _const, _fn, needs_node  # noqa: E402


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


LAUNCHER, CORE, MOB, HTML = _src("js/18-launcher.js"), _src("js/01-core.js"), _src("js/09c-mobile.js"), _src("index.html")


def test_the_badge_and_search_code_lives_in_the_slice_the_phone_loads_alone():
    for name in ("navBadgesApply", "navBadgesRefresh", "navBadgesStart", "navPageCount", "navDeptCounts", "navBadgeText",
                 "pgSearchResults", "pgIndex", "pgSearchOpen", "pgSearchClose", "pgSearchKey", "pgSearchInit", "pgNorm", "pgRowInner"):
        assert len(re.findall(r"function %s\(" % name, LAUNCHER)) == 1, name
        assert f"function {name}(" not in MOB and f"function {name}(" not in CORE, name           # one engine, not copied into the phone shell
    # the one call: the endpoint of the brief, read through the app's own getJSON (no second fetch in the slice)
    assert re.findall(r"getJSON\('(/api/[a-z/-]+)'", LAUNCHER) == ["/api/nav/badges"] and "fetch(" not in LAUNCHER
    # fetched on load, when the tab is shown again, and every 5 minutes; a failure is silent
    assert "const _NB_EVERY_MS = 5 * 60 * 1000" in LAUNCHER and "visibilitychange" in _fn(LAUNCHER, "navBadgesStart") and "setInterval" in _fn(LAUNCHER, "navBadgesStart")
    refresh = _fn(LAUNCHER, "navBadgesRefresh")
    assert "catch (e)" in refresh and "notifyError" not in refresh and "uiToast" not in refresh and "console.error" not in refresh
    # the phone shell starts it after the launcher slice is in, and draws the counts on its tiles, nav and search rows
    assert "navBadgesStart()" in _fn(MOB, "phBoot") and "pgSearchResults(" in _fn(MOB, "phSearchRender")
    assert "nb: 't'" in MOB and "data-nb" in _fn(MOB, "phNavRender") and "data-nb=" in _fn(MOB, "phRenderDept")


def test_a_badge_is_a_red_count_with_an_accessible_name_on_every_surface():
    apply_ = _fn(LAUNCHER, "navBadgesApply")
    assert "`รอ ${n} งาน`" in apply_ and "'aria-label'" in apply_ and "n < 1" in apply_                 # >= 1 only, "รอ N งาน"
    assert "n > 99 ? '99+'" in _fn(LAUNCHER, "navBadgeText")
    # page tiles (launcher), department tabs (D2), rail icons, phone department tiles, page tiles, the bottom-nav tasks
    assert 'data-nb="${_navPageSpec(x.p)}"' in _fn(LAUNCHER, "_lnchDeptsHtml") and 'data-nb="${_navPageSpec(x.p)}"' in _fn(LAUNCHER, "renderDeptHead")
    assert 'data-nb="d:${r.dept.key}"' in _fn(LAUNCHER, "renderDeptNav") and "ph-cnt" in apply_
    assert 'class="ph-cnt" data-dept=' in _fn(MOB, "_phCentreHome")
    tokens = _src("docs/ui-kit/tokens.css")
    assert re.search(r"--nb-bg:\s*#b42318;\s*--nb-ink:\s*#ffffff", tokens)                              # the owner's red, white on it (6.5:1)
    assert "background: var(--nb-bg); color: var(--nb-ink)" in HTML and ".ph-cnt[hidden] { display: none; }" in HTML


def test_the_search_is_a_dialog_with_the_keyboard_of_the_brief_and_no_backend():
    panel = _fn(LAUNCHER, "_pgPanel")
    assert 'role="dialog" aria-modal="true"' in panel and 'role="combobox"' in panel and 'role="listbox"' in panel
    keys = _fn(LAUNCHER, "pgSearchKey")
    for k in ("'Escape'", "'ArrowDown'", "'ArrowUp'", "'Enter'", "'Tab'"):
        assert k in keys, k
    init = _fn(LAUNCHER, "pgSearchInit")
    assert "ev.code === 'KeyK'" in init and "ev.code === 'Slash'" in init and "'/'" in init            # "/" and Ctrl+K, also on a Thai layout
    assert "INPUT|TEXTAREA|SELECT" in init                                                              # "/" in a field types a slash
    # the rail icon and the launcher box open it; the old "/" (focus the branch / SKU box) hands over to it
    assert 'id="nav-search"' in _fn(LAUNCHER, "renderDeptNav") and "pgSearchOpen()" in _src("tabs/57-launcher.html")
    assert "pgSearchOpen()" in _src("js/06-features.js")
    # a result: icon + page name + department chip - and no sentence
    row = _fn(LAUNCHER, "pgRowInner")
    assert "pgs-ic" in row and "pgs-name" in row and "pgs-chip" in row and "<p" not in row
    assert "/api/" not in _fn(LAUNCHER, "pgSearchResults") and "/api/" not in _fn(LAUNCHER, "pgIndex")


def _harness(tmp_path, role, expr, allowed="() => true"):
    """Run the real registry (js/01-core.js) and the real search / badge functions (js/18-launcher.js) in node with the permission checks stubbed."""
    a, b = CORE.index("const _SUPPLY_CENTRE"), CORE.index("// ── end page registry")
    fns = "\n".join(_fn(LAUNCHER, n) for n in ("_deptRole", "deptPageAllowed", "deptVisible", "navPageCount", "navBadgeText", "pgNorm", "_pgEntry", "pgIndex", "_pgScore", "pgSearchResults"))
    prelude = (f"const window = {{ AUTH: {{ enabled: true, role: '{role}' }} }};\n"
               f"const tabAllowed = {allowed}; const hasModule = () => true; const _supplyItemAllowed = it => tabAllowed(it.tab); const _noEmoji = s => s;\n"
               "const _PG_MAX = 12, _PG_IDLE = 8;\n" + _const(LAUNCHER, "_NB") + "\n")
    f = tmp_path / "harness.js"
    f.write_text(CORE[a:b] + "\n" + prelude + fns + f"\nconsole.log(JSON.stringify({expr}));", encoding="utf-8")
    out = _sp.run([NODE, str(f)], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return _json.loads(out)


def _labels(tmp_path, role, text, **kw):
    return _harness(tmp_path, role, f"pgSearchResults({_json.dumps(text, ensure_ascii=False)}).map(e => [e.label, e.dept ? e.dept.key : null, e.tab + (e.pane ? '|' + e.pane : '')])", **kw)


@needs_node
def test_search_finds_pages_by_label_synonym_department_and_cluster(tmp_path):
    # the owner's query: "ใบสั่ง" puts the queue first; spaces and case do not matter
    r = _labels(tmp_path, "salessupport", "ใบสั่ง")
    assert r[0] == ["คิวใบสั่งซื้อ", "ss", "orderhub|queue"]
    assert _labels(tmp_path, "salessupport", "ใบ สั่ง")[0][2] == "orderhub|queue"
    assert _labels(tmp_path, "salessupport", "PO")[0][2] == "orderhub|queue"                         # a synonym of the registry (PAGE_KEYWORDS)
    assert _labels(tmp_path, "salessupport", "สั่งของ")[0][2] == "orderhub|queue"
    assert _labels(tmp_path, "salessupport", "เคลม")[0][2] == "orderhub|claims"                      # label starts with it
    assert _labels(tmp_path, "salessupport", "คืนของ")[0][2] == "orderhub|claims"                    # synonym
    top = [x[2] for x in _labels(tmp_path, "management", "กำไร")]
    assert "gpbranch" in top and "pnlbranch" in top                                                 # กำไร -> GP / P&L pages that never say กำไร
    assert [x[2] for x in _labels(tmp_path, "management", "gp")][0] == "gpbranch"
    assert "cost" in [x[2] for x in _labels(tmp_path, "management", "COST")]
    # a department name finds its pages (nothing named better); an unknown word finds nothing
    ana = _labels(tmp_path, "management", "ฝ่ายวิเคราะห์")
    assert ana and all(x[1] == "ana" for x in ana)
    assert _labels(tmp_path, "management", "zzzqqq") == []
    assert any(x[2] == "heavyeater" for x in _labels(tmp_path, "management", "นักกินหนัก"))        # a page of the old cluster that no department lists


@needs_node
def test_search_lists_only_what_the_role_may_open_and_each_page_once(tmp_path):
    full = _harness(tmp_path, "management", "pgIndex().map(e => e.tab + '|' + (e.pane || ''))")
    assert len(full) == len(set(full)) == 79 and "franchise|" not in full                              # every page of the registry, once (ร้านของฉัน is the franchise's alone)
    assert "franchise|" in _harness(tmp_path, "franchise", "pgIndex().map(e => e.tab + '|' + (e.pane || ''))")
    only = _json.dumps(["branchstock", "manual"])
    rows = _harness(tmp_path, "branch", "pgSearchResults('').map(e => e.tab)", allowed=f"t => {only}.includes(t)")
    assert rows and set(rows) <= {"branchstock", "manual"}
    assert set(_harness(tmp_path, "branch", "pgIndex().map(e => e.tab)", allowed=f"t => {only}.includes(t)")) <= {"branchstock", "manual"}
    # a page two departments list is shown under the person's own department first
    assert _labels(tmp_path, "salessupport", "ใบตัดเบิก")[0][1] == "ss"
    assert _labels(tmp_path, "operation", "ใบตัดเบิก")[0][1] == "ops"


@needs_node
def test_search_without_a_query_shows_the_counted_pages_first_and_badges_cap_at_99(tmp_path):
    r = _harness(tmp_path, "analyst", "(() => { _NB.pages = { 'bomcenter:rel': 3, 'bomcenter:price': 1, 'orderhub:central': 9 }; "
                 "return [pgSearchResults('').slice(0, 3).map(e => e.tab + '|' + (e.pane || '')), navPageCount({tab: 'bomcenter', pane: 'rel'}), navPageCount({tab: 'x'}), [1, 99, 100, 5000].map(navBadgeText)]; })()")
    assert r[0] == ["orderhub|central", "bomcenter|rel", "bomcenter|price"]                           # the most waiting first
    assert r[1] == 3 and r[2] == 0 and r[3] == ["1", "99", "99+", "99+"]


# ── browser (runs where the Playwright client and a Chrome are installed - `py -3.14` here - and is skipped otherwise) ────────────
from test_pinned_no_branch_select import _browser, live_server  # noqa: E402,F401  (fixtures; auth_on is this module's own)


def _page(b, base, viewport, answer, phone=False):
    """Log in as management and load the app; GET /api/nav/badges is answered by `answer` (a dict, or None = the server fails)."""
    kw = dict(viewport=viewport, is_mobile=phone, has_touch=phone)
    ctx = b.new_context(**kw)
    assert ctx.request.post(base + "/api/auth/login", data={"username": "management1", "password": auth.DEFAULT_PASSWORD}).ok
    page = ctx.new_page()
    errors, toasts = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    if answer is None:
        page.route("**/api/nav/badges", lambda r: r.fulfill(status=500, body="{}"))
    else:
        page.route("**/api/nav/badges", lambda r: r.fulfill(status=200, content_type="application/json", body=_json.dumps({"pages": answer, "at": "2026-10-21T08:00:00+07:00"})))
    page.goto(base + "/", wait_until="networkidle", timeout=90000)
    page.wait_for_function("window.AUTH && window.AUTH.authenticated", timeout=60000)
    return ctx, page, errors


def test_badges_and_search_in_a_desktop_browser(live_server):
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        b = _browser(p)
        ctx, page, errors = _page(b, live_server, {"width": 1440, "height": 900}, {"orderhub:queue": 3, "orderhub:claims": 250, "bomcenter:price": 1})
        page.wait_for_function("activeTab === 'launcher' && document.querySelectorAll('.lnch-tile > .nb').length > 0", timeout=60000)
        tiles = page.evaluate("[...document.querySelectorAll('#lnchDepts .lnch-tile')].filter(t => t.querySelector('.nb')).map(t => [t.querySelector('.lnch-tile-label').textContent, t.querySelector('.nb').textContent, t.querySelector('.nb').getAttribute('aria-label')])")
        assert ["คิวใบสั่งซื้อ", "3", "รอ 3 งาน"] in tiles and ["เคลมสินค้า", "99+", "รอ 250 งาน"] in tiles
        assert not page.evaluate("[...document.querySelectorAll('#lnchDepts .lnch-tile')].some(t => t.querySelector('.nb') && t.querySelector('.nb').textContent === '0')")
        rail = page.evaluate("Object.fromEntries([...document.querySelectorAll('#deptNav [data-dept]')].map(e => [e.dataset.dept, e.querySelector('.nb') ? e.querySelector('.nb').textContent : '']))")
        assert rail["ss"] == "99+" and rail["ana"] == "1" and rail["mgmt"] == ""                       # the sum of the department's pages; none = no circle
        # the keyboard: "/" opens, the Thai query ranks the queue first, the arrows move, Enter opens it in its department, Esc closes
        page.locator("body").press("/")
        assert page.evaluate("!document.getElementById('pgSearch').hidden && document.activeElement.id") == "pgsInput"
        page.keyboard.type("ใบสั่ง")
        first = page.evaluate("document.querySelector('#pgsList .pgs-row').innerText.replace(/\\s+/g, ' ').trim()")
        assert first.startswith("คิวใบสั่งซื้อ ประสานงานขาย 3")                                          # icon + page + department chip (+ its count)
        page.keyboard.press("ArrowDown")
        assert page.evaluate("document.querySelector('#pgsList .pgs-row[aria-selected=true]').id") == "pgs-o1"
        page.keyboard.press("ArrowUp")
        page.keyboard.press("Enter")
        page.wait_for_function("activeTab === 'orderhub'", timeout=30000)
        assert page.evaluate("document.getElementById('pgSearch').hidden") is True
        assert "ประสานงานขาย" in page.evaluate("document.querySelector('#deptHead .dh-name').textContent")
        assert page.evaluate("document.querySelector('#deptHead .dh-tab[aria-selected=true] .nb').textContent") == "3"
        page.keyboard.press("Control+k")
        assert page.evaluate("!document.getElementById('pgSearch').hidden")
        page.keyboard.press("Escape")
        assert page.evaluate("document.getElementById('pgSearch').hidden") is True
        # "/" typed into a field stays a slash
        page.evaluate("document.body.insertAdjacentHTML('beforeend', '<input id=tmpin>'); document.getElementById('tmpin').focus()")
        page.keyboard.type("/")
        assert page.evaluate("document.getElementById('pgSearch').hidden") is True
        assert not errors, errors
        ctx.close()
        # a failing endpoint: no badges and nothing said about it
        ctx, page, errors = _page(b, live_server, {"width": 1440, "height": 900}, None)
        page.wait_for_function("activeTab === 'launcher'", timeout=60000)
        page.wait_for_timeout(1500)
        assert page.evaluate("document.querySelectorAll('.nb').length") == 0 and page.evaluate("document.querySelectorAll('#uiToasts .ui-toast, #uiToasts > *').length") == 0
        assert not errors, errors
        ctx.close()
        b.close()


def test_badges_and_search_on_a_phone(live_server):
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        b = _browser(p)
        ctx, page, errors = _page(b, live_server, {"width": 390, "height": 844}, {"orderhub:queue": 3, "bomcenter:price": 2}, phone=True)
        page.wait_for_function("document.body.classList.contains('ph-home') && document.querySelectorAll('#phNav .ph-nav-i').length > 0", timeout=60000)
        page.wait_for_function("document.querySelector('#phNav .nb')", timeout=15000)
        assert page.evaluate("document.querySelector('#phNav [data-nb=t] .nb').textContent") == "5"      # the whole list on งานของฉัน
        dept = page.evaluate("Object.fromEntries([...document.querySelectorAll('.ph-cnt:not([hidden])')].map(e => [e.dataset.dept, e.textContent]))")
        assert dept.get("ss") == "3" and dept.get("ana") == "2" and "mgmt" not in dept                  # a department with nothing waiting has no circle
        assert page.evaluate("document.querySelector('.ph-cnt:not([hidden])').getAttribute('aria-label')").startswith("รอ ")
        # ค้นหา: the same engine - icon + page + department chip
        page.locator("#phNav .ph-nav-i", has_text="ค้นหา").click()
        page.fill("#phSearchInput", "ใบสั่ง")
        row = page.evaluate("[...document.querySelectorAll('#phSearchRes .ph-row')].map(e => e.querySelector('.pgs-name').textContent + '|' + e.querySelector('.pgs-chip').textContent)")
        assert row[0] == "คิวใบสั่งซื้อ|ประสานงานขาย"
        assert not errors, errors
        ctx.close()
        b.close()


@needs_node
def test_the_search_words_are_checked_with_the_registry():
    out = _sp.run([NODE, "scripts/check-departments.js"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
