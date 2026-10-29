"""PR-D3 (owner 2026-10-21): the phone shell - the department launcher as the phone's home, a bottom navigation that replaces the
branch strip and the folded top-bar controls, the branch and franchise homes. Phone = the app's own 700 px breakpoint.

Static checks (always run) pin the contract; the node checks lift the pure functions out of js/09c-mobile.js; the browser checks (they
run where the Playwright client and a Chrome are installed - `py -3.14` here - and are skipped otherwise) log in on a 390x844 phone
profile and walk the flows of the brief. The desktop is proved untouched by a 768 and a 1440 px login.
"""
import json
import re
from pathlib import Path

import pytest

import auth
from test_bom_center_batch2 import _fn, _node, needs_node
from test_pinned_no_branch_select import TOKEN, _browser, auth_on, live_server  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parent.parent


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


MOB, CORE, MAIN, HTML = _src("js/09c-mobile.js"), _src("js/01-core.js"), _src("js/03-tabs-main.js"), _src("index.html")
SHELL = MOB[MOB.index("PHONE SHELL (PR-D3"):MOB.index("// ── Pull to refresh on the summary")]


# ── static ───────────────────────────────────────────────────────────────────
def test_a_phone_boots_into_the_launcher_before_the_old_phone_first_path():
    start = MAIN[MAIN.index("async function startApp"):]
    assert start.index("if (_phoneApp())") < start.index("else if (_phoneFirst())")                  # the shell first, the old paths unchanged
    assert "await phBoot();" in start and "loadMobileSummary();" in start and "await ensureDesktopBooted();" in start
    # the old landing lines stay for the desktop and are covered by their own tests
    assert "_supplyPhoneRole() && _phoneModeSaved() !== 'summary') supplyOpen()" in MAIN
    assert re.search(r"function _phoneApp\(\) \{[^}]*max-width:700px", SHELL)                          # the app's own breakpoint, not 768


def test_the_desktop_boot_is_unchanged_and_the_shell_is_inert_without_body_ph_on():
    assert "if (!(opts && opts.noLanding)) switchTab(_landingTab());" in MAIN                          # a desktop still lands on its landing tab
    assert "function phOn() { return document.body.classList.contains('ph-on'); }" in CORE
    assert re.search(r"function _launcherLanding\(\) \{[^}]*max-width:700px", CORE)
    # every rule of the shell's stylesheet is keyed on body.ph-on or on an element that is display:none until then
    css = HTML[HTML.index("PHONE SHELL (PR-D3, 2026-10-21) — the department launcher"):HTML.index("PRINT — RE-ASSERTED LAST")]
    assert "#phHome, #phNav { display: none; }" in css
    media = css[css.index("@media (max-width: 700px) {"):]
    for sel in re.findall(r"^  ([^{/]+)\{", media, re.M):
        assert "body.ph-on" in sel, sel
    assert "#phHome, #phNav" in HTML.split("PRINT — RE-ASSERTED LAST")[1]                              # never on paper
    # the three old entry points hand over to the shell only when it is in force
    for name in ("showFullDashboard", "msOpenSupply", "showMobileSummary"):
        assert "if (phOn())" in _fn(MOB, name).split("\n")[1] + _fn(MOB, name).split("\n")[2], name


def test_the_bottom_navigation_replaces_the_branch_strip_and_the_top_bar_controls():
    assert "SUPPLY_PHONE_NAV" not in CORE and "_renderSupplyNav" not in CORE and 'id="supplyNav"' not in HTML and "#supplyNav button" not in HTML and "#supplyNav {" not in HTML
    assert 'id="phNav"' in HTML and 'id="phHome"' in HTML
    assert "phNavRender();" in _fn(CORE, "_updateClusterBar") and "phNavRender();" in CORE.split("function applyPermissions")[1].split("function stripBranchPickers")[0]
    # the user chip and the theme button leave the top bar on a phone; their controls are rows of ฉัน
    assert re.search(r"body\.ph-on #userChip, body\.ph-on #darkBtn \{ display: none !important; \}", HTML)
    for needle in ('id="phMeDark"', 'id="phMeLock"', 'id="phMePw"', 'onclick="doLogout()"', 'id="msPush"', 'id="msInstall"', "msPushEnable()"):
        assert needle in HTML.split('id="phv-me"')[1].split("</section>")[0], needle
    assert 'id="msPush"' not in HTML.split('id="mobileSummary"')[1].split('id="phHome"')[0]           # not in the summary any more
    assert "bottom: calc(56px + env(safe-area-inset-bottom, 0px))" in HTML and "min-height: 56px" in HTML      # 56 px + the safe area, 56 px targets


def test_the_phone_shell_adds_no_route_and_no_endpoint_of_its_own():
    # tasks: the supply todo of หน้างาน; the header figure: the Daily Brief's own call; PR-PD (owner 2026-10-29): the cause and
    # data-trust lines under it read the Daily Brief's branch scorecard (an existing route). Nothing else is fetched.
    assert sorted(set(re.findall(r"api\(`\$\{API\}(/api/[a-z/-]+)", SHELL))) == ["/api/bom-center/todo", "/api/home/branch-scorecard", "/api/home/brief"]
    assert "fetch(" not in SHELL
    # a tile opens through the existing functions
    assert "deptOpenPage(p)" in SHELL and "supplyGo({ tab: e.tab, pane: e.pane })" in SHELL and "_shmOpen(g.first.go)" in SHELL
    # the shell loads js/18-launcher.js alone and the other slices on the first page, so the phone-first boot stays light
    assert "phEnsureLauncher" in SHELL and "ensureDesktopBooted({ noLanding: true })" in SHELL
    assert "_loadSliceTag" in CORE and "_sliceLoads" in CORE


def test_no_branch_selector_on_the_branch_home_and_the_task_filter_is_the_one_of_the_desk():
    branch = _fn(SHELL, "_phBranchHome")
    assert "select" not in branch.lower() and "pinnedBranchName()" in branch
    assert "const _SHM_ROLE_OWNERS" in CORE and "function _shmMine(" in CORE
    assert "function _shmMine(" not in _src("js/16-supplyhome.js")
    assert "const _SHM_ROLE_OWNERS" not in _src("js/16-supplyhome.js")                                 # moved, not copied


def test_the_registry_helpers_the_phone_needs_are_still_defined_once():
    launcher = _src("js/18-launcher.js")
    for name in ("deptVisible", "deptPageAllowed", "deptOpenPage", "lnchDateLabel", "lnchWho", "_deptSvg"):
        assert len(re.findall(r"function %s\(" % name, launcher + CORE)) == 1, name


# ── node: the pure parts ─────────────────────────────────────────────────────
def _nav_prelude(role, allowed="() => true"):
    # PR-MA: the bar of each role; `allowed` stands in for the launcher's page rule (deptPageAllowed) of that login
    bars = re.search(r"const _PH_BARS = \{.*?\n\};", SHELL, re.S).group(0)
    home = re.search(r"const _PH_HOME_TAB = \{.*?\};", SHELL).group(0)
    return ("const DEPT_ICONS = new Proxy({}, { get: (t, k) => 'p-' + String(k) });\n"
            f"const window = {{ AUTH: {{ enabled: true, role: '{role}' }} }};\nconst deptPageAllowed = {allowed};\nconst tabAllowed = () => true;\n"
            + bars + "\n" + home + "\n" + "\n".join(_fn(SHELL, n) for n in ("_phKind", "_phRole", "_phPageOk", "_phHomeTab", "_phNavItems"))
            + "\nconst _PH_ICON = { home: 'h', search: 's', me: 'm' };")


# PR-MA (owner 2026-10-24): one bar per role, built for its daily work; ค้นหา is the magnifier of the header now
BARS = {
    "management": ["หน้าแรก", "ยอดขาย", "กำไร", "สาขา", "ฉัน"],
    "salessupport": ["หน้าแรก", "คิวใบสั่งซื้อ", "ใบตัดเบิกสาขา", "เคลม", "ฉัน"],
    "warehouse": ["หน้าแรก", "คิวใบสั่งซื้อ", "ฉัน"],
    "analyst": ["หน้าแรก", "ต้นทุนต่อจาน", "ราคารายเดือน", "ไฟล์ราคา", "ฉัน"],
    "accounting": ["หน้าแรก", "ใบสรุปตัดเบิก", "P&L รายสาขา", "GP รายสาขา", "ฉัน"],
    "operation": ["หน้าแรก", "ใบตัดเบิกสาขา", "ผลต่างตัดเบิกรายวัน", "ส่วนลด", "ฉัน"],
    "marketing": ["หน้าแรก", "ลูกค้าและบุฟเฟ่ต์", "วิเคราะห์เมนู", "เสียงลูกค้า", "ฉัน"],
    "innovation": ["หน้าแรก", "ทะเบียนวัตถุดิบ", "ฉัน"],
}


@needs_node
def test_the_bottom_navigation_per_role():
    def items(role, allowed="() => true"):
        return _node(_nav_prelude(role, allowed), "_phNavItems()")
    for role, want in BARS.items():
        got = items(role)
        assert [i["label"] for i in got] == want, role
        assert "ค้นหา" not in [i["label"] for i in got], role                                     # the old search slot is gone
        assert got[0]["nb"] == "t", role                                                          # the old งานของฉัน count sits on หน้าแรก
    assert [i["label"] for i in items("branch")] == ["หน้าแรก", "ตัดเบิก", "สั่งของ", "รับของ", "ฉัน"]
    assert [i["label"] for i in items("franchise")] == ["หน้าแรก", "ฉัน"]
    panes = [(i.get("tab"), i.get("pane")) for i in items("branch")]
    assert panes[1:4] == [("branchstock", "issue"), ("branchstock", "order"), ("branchstock", "recv")]
    # the red counts: the pages with tasks carry their nav_badges key
    nb = {i["label"]: i.get("nb") for i in items("salessupport")}
    assert nb["คิวใบสั่งซื้อ"] == "p:orderhub:queue" and nb["ใบตัดเบิกสาขา"] == "p:orderhub:issues" and nb["เคลม"] == "p:orderhub:claims"
    nb = {i["label"]: i.get("nb") for i in items("analyst")}
    assert nb["ต้นทุนต่อจาน"] == "p:bomcenter:dishcost" and nb["ราคารายเดือน"] == "p:bomcenter:price" and nb["ไฟล์ราคา"] == "p:bomcenter:pricefile"
    assert {i["label"]: i.get("nb") for i in items("accounting")}["ใบสรุปตัดเบิก"] == "p:bomcenter:issue"
    nb = {i["label"]: i.get("nb") for i in items("operation")}
    assert nb["ผลต่างตัดเบิกรายวัน"] == "p:orderhub:issuevar" and nb["ส่วนลด"] == ""
    # a page the login may not open is left out of its bar (a role with three entries shows three)
    assert [i["label"] for i in items("salessupport", "p => p.pane !== 'claims'")] == ["หน้าแรก", "คิวใบสั่งซื้อ", "ใบตัดเบิกสาขา", "ฉัน"]
    # หน้าแรก of Sales Support and the analyst is หน้างาน, of marketing สรุปประจำวัน; the others open the launcher
    assert items("salessupport")[0]["tab"] == "supplyhome" and items("analyst")[0]["tab"] == "supplyhome" and items("marketing")[0]["tab"] == "home"
    assert items("management")[0]["tab"] is None and items("warehouse")[0]["tab"] is None


@needs_node
def test_the_tasks_are_the_roles_open_items_grouped_like_the_desk():
    owners = re.search(r"const _SHM_ROLE_OWNERS = \{.*?\};", CORE, re.S).group(0)
    pre = ("const hasModule = () => false;\n"
           "const window = { AUTH: { enabled: true, role: 'branch' } };\n" + owners + "\n" + _fn(CORE, "_shmMine") + "\n" + _fn(CORE, "shmIsOpenTask") + "\n" + _fn(SHELL, "_phState") + "\n" + _fn(SHELL, "_phGroups"))
    items = [
        {"key": "issue_today", "title": "ยืนยันใบตัดเบิก", "status": "todo", "owner_th": "สาขา", "seq": 2, "branch": "A"},
        {"key": "order_due_1", "title": "ส่งใบสั่งซื้อ", "status": "todo", "owner_th": "สาขา", "seq": 1, "urgent": True, "branch": "A"},
        {"key": "order_due_1", "title": "ส่งใบสั่งซื้อ", "status": "todo", "owner_th": "สาขา", "seq": 1, "urgent": False, "branch": "B"},
        {"key": "price", "title": "ราคา", "status": "todo", "owner_th": "ฝ่ายวิเคราะห์", "seq": 3},          # another desk's
        {"key": "done", "title": "เสร็จ", "status": "done", "owner_th": "สาขา", "seq": 4},               # not open
        {"key": "wait", "title": "รอตรวจ", "status": "blocked", "state": "review", "owner_th": "สาขา", "seq": 5},   # waiting for review: hidden by default (PR-TF)
        {"key": "fin", "title": "เสร็จ2", "status": "todo", "state": "done", "owner_th": "สาขา", "seq": 6},
    ]
    out = _node(pre, f"_phGroups({json.dumps(items, ensure_ascii=False)}).map(g => [g.first.key, g.branches, g.urgent])")
    assert out == [["order_due_1", ["A", "B"], True], ["issue_today", ["A"], False]]                    # urgent first; one row per duty


# ── browser ──────────────────────────────────────────────────────────────────
PHONE = {"width": 390, "height": 844}


def _phone(b, base, user, pwd):
    ctx = b.new_context(viewport=PHONE, is_mobile=True, has_touch=True)
    assert ctx.request.post(base + "/api/auth/login", data={"username": user, "password": pwd}).ok
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/", wait_until="networkidle", timeout=90000)
    page.wait_for_function("document.body.classList.contains('ph-on') && document.querySelectorAll('#phNav .ph-nav-i').length", timeout=60000)
    page.wait_for_timeout(1500)
    return ctx, page, errors


def test_a_phone_login_opens_the_launcher_then_a_department_then_a_page(live_server):
    pw = pytest.importorskip("playwright.sync_api")
    base = live_server
    with pw.sync_playwright() as p:
        b = _browser(p)
        ctx, page, errors = _phone(b, base, "management1", auth.DEFAULT_PASSWORD)
        home = page.evaluate("""() => ({ view: _phView, booted: _desktopBooted, depts: [...document.querySelectorAll('#phv-home .ph-dept')].map(e => e.dataset.dept || e.querySelector('.ph-dept-n').textContent),
            nav: [...document.querySelectorAll('#phNav .ph-nav-i')].map(e => e.querySelector('span').textContent.trim()), navH: Math.round(document.getElementById('phNav').getBoundingClientRect().height),
            hscroll: document.documentElement.scrollWidth > innerWidth, sidebar: getComputedStyle(document.getElementById('sidebar')).display,
            small: [...document.querySelectorAll('#phHome a, #phNav button')].filter(e => e.offsetParent).map(e => e.getBoundingClientRect().height).filter(h => h < 44).length })""")
        assert home["view"] == "home" and home["booted"] is False                                       # the heavy shell has not been fetched
        assert len(home["depts"]) == 9 and home["nav"] == BARS["management"] and home["navH"] == 56
        assert not home["hscroll"] and home["sidebar"] == "none" and home["small"] == 0
        # a department: header tint + two-column grid of tiles; the owner's summary is its first tile
        page.locator(".ph-dept", has_text="ผู้บริหาร").first.click()
        page.wait_for_function("_phView === 'dept'")
        tiles = page.evaluate("[...document.querySelectorAll('#phv-dept .ph-ptile')].map(e => e.innerText.trim())")
        assert tiles[0] == "สรุปผู้บริหาร" and "ยอดขายรายวัน" in tiles
        assert page.evaluate("getComputedStyle(document.querySelector('#phv-dept .ph-ptiles')).gridTemplateColumns.split(' ').length") == 2
        # the summary, still intact behind it, opens from that tile and the bottom navigation goes home
        page.locator("#phv-dept .ph-ptile", has_text="สรุปผู้บริหาร").click()
        page.wait_for_function("_phView === 'summary'")
        page.wait_for_function("document.getElementById('msKpis').children.length > 0", timeout=30000)
        page.locator("#phNav .ph-nav-i", has_text="หน้าแรก").click()
        page.wait_for_function("_phView === 'home'")
        # a page: the shell boots on this first tap, the page replaces the launcher
        page.locator(".ph-dept", has_text="ผู้บริหาร").first.click()
        page.locator("#phv-dept .ph-ptile", has_text="ยอดขายรายวัน").click()
        page.wait_for_function("_phView === 'page' && activeTab === 'dailysales'", timeout=60000)
        assert page.evaluate("_desktopBooted") is True
        # the search: every page this login may open; the legacy ones are there too
        page.locator("#phTbSearch").click()                                                     # PR-MA: the magnifier of the top bar
        page.fill("#phSearchInput", "ปฏิทินเทศกาล")
        # PR-D4: a result is icon + page name + department chip (the same engine as the desktop panel)
        assert page.evaluate("[...document.querySelectorAll('#phSearchRes .ph-row')].map(e => e.querySelector('.pgs-name').textContent + '|' + e.querySelector('.pgs-chip').textContent)") == ["ปฏิทินเทศกาลและวันหยุด|ประสานงานขาย"]
        page.locator("#phSearchRes .ph-row").click()
        page.wait_for_function("_phView === 'page' && activeTab === 'bomcenter'", timeout=30000)
        # ฉัน: theme, sign out, change password
        page.locator("#phNav .ph-nav-i:has(span:text-is('ฉัน'))").click()
        assert page.evaluate("[...document.querySelectorAll('#phv-me .ph-row, #phMeOut')].filter(e => e.offsetParent).map(e => e.innerText.trim().split('\\n')[0])")[:1] == ["โหมดมืด"]
        assert not errors, errors
        ctx.close()
        b.close()


def test_the_branch_and_franchise_homes_have_their_own_bottom_navigation(live_server):
    pw = pytest.importorskip("playwright.sync_api")
    base = live_server
    with pw.sync_playwright() as p:
        b = _browser(p)
        mg = b.new_context()
        assert mg.request.post(base + "/api/auth/login", data={"username": "management1", "password": auth.DEFAULT_PASSWORD}).ok
        for u, role, br in (("br_ph_a", "branch", "BranchA"), ("own_ph_b", "franchise", "BranchB")):
            r = mg.request.post(base + "/api/auth/users", headers={"X-Upload-Token": TOKEN},
                                data={"username": u, "password": "Temp@12345", "role": role, "branch": br})
            assert r.status in (200, 409), r.text()
        ctx, page, errors = _phone(b, base, "br_ph_a", "Temp@12345")
        home = page.evaluate("""() => ({ title: document.querySelector('#phv-home .ph-head-t h1').textContent, nav: [...document.querySelectorAll('#phNav .ph-nav-i')].map(e => e.querySelector('span').textContent.trim()),
            tiles: [...document.querySelectorAll('#phv-home .ph-bigtile-n')].map(e => e.textContent), todo: document.getElementById('phTodoH').textContent,
            picker: document.querySelectorAll('#phHome select').length })""")
        assert home["title"] == "BranchA" and home["picker"] == 0 and home["todo"] == "ต้องทำวันนี้"
        assert home["nav"] == ["หน้าแรก", "ตัดเบิก", "สั่งของ", "รับของ", "ฉัน"]
        assert home["tiles"] == ["ตัดเบิก", "สั่งของ", "รับของ", "ส่วนต่างการใช้", "นับของสิ้นเดือน"]
        page.locator("#phNav .ph-nav-i:has(span:text-is('สั่งของ'))").click()
        page.wait_for_function("_phView === 'page' && activeTab === 'branchstock' && _SUPPLY_PANE.branchstock.get() === 'order'", timeout=60000)
        assert page.evaluate("document.querySelector('#phNav .ph-nav-i.active span').textContent.trim()") == "สั่งของ"
        assert not errors, errors
        ctx.close()
        ctx, page, errors = _phone(b, base, "own_ph_b", "Temp@12345")
        assert page.evaluate("[...document.querySelectorAll('#phNav .ph-nav-i')].map(e => e.querySelector('span').textContent.trim())") == ["หน้าแรก", "ฉัน"]
        # PR-F1: the owner's หน้าแรก IS ร้านของฉัน - the login opens on the page itself, there is no launcher of one tile
        page.wait_for_function("_phView === 'page' && activeTab === 'franchise'", timeout=60000)
        assert page.evaluate("document.querySelector('#phNav .ph-nav-i.active span').textContent.trim()") == "หน้าแรก"
        assert not errors, errors
        ctx.close()
        b.close()


def test_a_desktop_width_never_gets_the_phone_shell(live_server):
    pw = pytest.importorskip("playwright.sync_api")
    base = live_server
    with pw.sync_playwright() as p:
        b = _browser(p)
        for vp in ({"width": 768, "height": 1024}, {"width": 1440, "height": 900}):
            ctx = b.new_context(viewport=vp)
            assert ctx.request.post(base + "/api/auth/login", data={"username": "management1", "password": auth.DEFAULT_PASSWORD}).ok
            page = ctx.new_page()
            page.goto(base + "/", wait_until="networkidle", timeout=90000)
            page.wait_for_function("typeof activeTab === 'string' && activeTab === 'launcher'", timeout=60000)
            page.wait_for_timeout(1500)
            seen = page.evaluate("""() => ({ on: document.body.classList.contains('ph-on'), nav: getComputedStyle(document.getElementById('phNav')).display,
                home: getComputedStyle(document.getElementById('phHome')).display, side: getComputedStyle(document.getElementById('sidebar')).display,
                depts: document.querySelectorAll('#lnchDepts .lnch-dept').length })""")
            assert seen == {"on": False, "nav": "none", "home": "none", "side": "flex", "depts": 9}, (vp, seen)
            ctx.close()
        b.close()
