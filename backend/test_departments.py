"""PR-D1 (owner 2026-10-21): the department launcher - one registry (DEPARTMENTS in js/01-core.js), หน้าแรก for every role on a
desktop, the sidebar by department and หน้ารายงานทั้งหมด.

Static checks (always run) pin the contract the shell relies on; the registry check is the same script scripts/check-js.sh runs
(every page of TAB_CLUSTERS and SUPPLY_CHIPS stays reachable); the browser checks (they run where the Playwright client and a Chrome
are installed - `py -3.14` here - and are skipped otherwise) log in as several roles on a desktop and a phone.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import auth
from test_pinned_no_branch_select import TOKEN, _browser, auth_on, live_server  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parent.parent


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# ── static ───────────────────────────────────────────────────────────────────
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_every_page_stays_reachable():
    """scripts/check-departments.js: nothing in TAB_CLUSTERS / SUPPLY_CHIPS is on neither a department nor หน้ารายงานทั้งหมด."""
    r = subprocess.run(["node", "scripts/check-departments.js"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "10 departments" in r.stdout


def test_the_ten_departments_in_the_owners_order_with_hues_in_both_themes():
    core = _src("js/01-core.js")
    keys = re.findall(r"^  \{ key: '([a-z]+)', label: '([^']+)', duty: '([^']+)', icon: '[a-z]+', hue: '([a-z]+)', roles: \[([^\]]*)\]", core, re.M)
    assert [k[0] for k in keys] == ["mgmt", "mkt", "ops", "ana", "kit", "ss", "wh", "acc", "brn", "own"]
    assert [k[1] for k in keys] == ["ผู้บริหาร", "การตลาด", "ปฏิบัติการ", "ฝ่ายวิเคราะห์", "ครัว", "ประสานงานขาย", "คลัง", "บัญชี", "สาขา", "เจ้าของสาขา"]
    assert [re.findall(r"'([a-z]+)'", k[4])[0] for k in keys] == ["management", "marketing", "operation", "analyst", "innovation",
                                                                    "salessupport", "warehouse", "accounting", "branch", "franchise"]
    tokens = _src("docs/ui-kit/tokens.css")
    dark = tokens[tokens.index("Department hues"):]
    dark = dark[dark.index("body.dark"):]
    light = {"mgmt": ("#1f3a6b", "#dde4f0"), "mkt": ("#9b1c55", "#fbe3ee"), "ops": ("#8a3f00", "#fdebd8"), "ana": ("#5b3fa8", "#ebe5fa"),
             "kit": ("#2f6b1f", "#e3f2dc"), "ss": ("#1d5fa8", "#dceafa"), "wh": ("#7a5300", "#fbf0d0"), "acc": ("#0f6b6b", "#dcf1f0"),
             "brn": ("#a12d2d", "#f9e1e1"), "own": ("#3c4a5e", "#e6eaf0")}
    for k, (ink, tint) in light.items():
        assert re.search(rf"--dept-{k}-ink:\s+{ink};", tokens) and re.search(rf"--dept-{k}-tint:\s+{tint};", tokens), k      # the owner's approved hues
        assert f"--dept-{k}-ink:" in dark and f"--dept-{k}-tint:" in dark, k                          # and a dark pair
        assert f"--dept-{k}-side:" in tokens, k                                                       # the icon on the navy rail


def test_dept_text_colours_are_tokens_not_raw_hex():
    """The launcher fragments and slice keep colours in the token block: no hex in a text colour."""
    for rel in ("tabs/57-launcher.html", "tabs/58-allpages.html", "js/18-launcher.js"):
        for m in re.finditer(r"(?<![-\w])color:\s*(#[0-9a-fA-F]{3,8})", _src(rel)):
            pytest.fail(f"{rel}: raw hex text colour {m.group(1)}")


def test_the_shell_wires_the_launcher_without_a_new_route():
    html, core, main = _src("index.html"), _src("js/01-core.js"), _src("js/03-tabs-main.js")
    assert 'id="deptNav"' in html and 'id="navSecTools"' in html and 'data-lazy="/js/18-launcher.js"' in html
    assert "launcher: { load: () => loadLauncher(), ownFilters: true }" in core and "allpages: { load: () => loadAllPages(), ownFilters: true }" in core
    assert "switchTab(_landingTab())" in main and "renderDeptNav()" in main
    # a desktop lands on the launcher; the phone breakpoint is the app's own 700 px, so a phone keeps its phone-first boot
    assert re.search(r"function _launcherLanding\(\) \{[^}]*max-width:700px", core)
    js = _src("js/18-launcher.js")
    assert re.findall(r"api\(`\$\{API\}(/api/[a-z/-]+)", js) == ["/api/bom-center/todo"]               # the tasks of หน้างาน, nothing new
    assert "fetch(" not in js
    # the tiles open through the existing functions
    assert "supplyGo({ tab: p.tab, pane: p.pane })" in js and "switchTab(p.tab)" in js


def test_the_franchise_page_is_listed_only_for_the_franchise_role():
    """A central login gets a 400 from /api/franchise/summary (it must name a branch), so its old sidebar entry never showed."""
    core = _src("js/01-core.js")
    assert "{ tab: 'franchise', label: 'ร้านของฉัน', icon: 'store', roles: ['franchise'] }" in core
    assert "{ tab: 'franchise', label: 'ร้านของฉัน', roles: ['franchise'] }" in core


# ── browser ──────────────────────────────────────────────────────────────────
def _login(b, base, user, pw, viewport):
    ctx = b.new_context(viewport=viewport)
    assert ctx.request.post(base + "/api/auth/login", data={"username": user, "password": pw}).ok
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/", wait_until="networkidle", timeout=90000)
    page.wait_for_function("typeof switchTab === 'function' && window.AUTH && window.AUTH.authenticated", timeout=60000)
    return ctx, page, errors


def test_a_desktop_lands_on_the_launcher_and_each_role_sees_its_own_departments(live_server):
    pw = pytest.importorskip("playwright.sync_api")
    base = live_server
    with pw.sync_playwright() as p:
        b = _browser(p)
        mg = b.new_context()
        assert mg.request.post(base + "/api/auth/login", data={"username": "management1", "password": auth.DEFAULT_PASSWORD}).ok
        for u, role, br in (("br_dep_a", "branch", "BranchA"), ("own_dep_b", "franchise", "BranchB")):
            r = mg.request.post(base + "/api/auth/users", headers={"X-Upload-Token": TOKEN},
                                data={"username": u, "password": "Temp@12345", "role": role, "branch": br})
            assert r.status in (200, 409), r.text()
        seen = {}
        for user, pwd in (("management1", auth.DEFAULT_PASSWORD), ("salessupport1", auth.DEFAULT_PASSWORD), ("br_dep_a", "Temp@12345"),
                          ("own_dep_b", "Temp@12345")):
            ctx, page, errors = _login(b, base, user, pwd, {"width": 1440, "height": 900})
            page.wait_for_function("activeTab === 'launcher'", timeout=30000)
            page.wait_for_timeout(1200)
            seen[user] = page.evaluate("""() => ({
                depts: [...document.querySelectorAll('#lnchDepts .lnch-dept')].map(s => s.dataset.dept),
                nav: [...document.querySelectorAll('.sidebar-nav .nav-item')].filter(e => getComputedStyle(e).display !== 'none').map(e => e.id),
                tiles: Math.min(...[...document.querySelectorAll('.lnch-tile')].map(e => Math.round(e.getBoundingClientRect().height))),
                shell: (document.getElementById('selBranch') || {}).tagName })""")
            assert not errors, (user, errors)
            ctx.close()
        m = seen["management1"]
        assert m["depts"] == ["mgmt", "mkt", "ops", "ana", "kit", "ss", "wh", "acc", "brn"] and m["nav"][:2] == ["nav-launcher", "nav-dept-mgmt"]
        assert m["tiles"] >= 44
        ss = seen["salessupport1"]
        assert ss["depts"][0] == "ss" and "own" not in ss["depts"] and "brn" not in ss["depts"]          # the home department first
        assert seen["br_dep_a"]["depts"] == ["brn"] and seen["br_dep_a"]["shell"] == "INPUT"             # a branch login sees no other department
        assert seen["own_dep_b"]["depts"] == ["own"] and seen["own_dep_b"]["nav"] == ["nav-launcher", "nav-dept-own"]
        b.close()


def test_every_tile_and_every_all_pages_link_opens_its_page_and_a_phone_is_unchanged(live_server):
    pw = pytest.importorskip("playwright.sync_api")
    base = live_server
    with pw.sync_playwright() as p:
        b = _browser(p)
        ctx, page, errors = _login(b, base, "management1", auth.DEFAULT_PASSWORD, {"width": 1440, "height": 900})
        page.wait_for_function("activeTab === 'launcher'", timeout=30000)
        tiles = page.evaluate("deptVisible().flatMap(r => r.pages.map(x => [r.dept.key, x.i, x.p.tab, x.p.pane || null]))")
        assert len(tiles) >= 60
        bad = []
        for key, i, tab, pane in tiles:
            page.evaluate("([k, i]) => deptGo(k, i)", [key, i])
            page.wait_for_timeout(250)
            cur = page.evaluate("({ tab: activeTab, pane: _SUPPLY_PANE[activeTab] ? _SUPPLY_PANE[activeTab].get() : null, lit: document.querySelector('#deptNav .nav-item.active')?.dataset.dept })")
            if cur["tab"] != tab or (pane and cur["pane"] != pane) or cur["lit"] != key:
                bad.append((key, tab, pane, cur))
        assert not bad, bad[:5]
        assert not errors, errors
        ctx.close()
        # a phone keeps the phone-first boot: no department entry is shown, the strip is the old one
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        assert ctx.request.post(base + "/api/auth/login", data={"username": "management1", "password": auth.DEFAULT_PASSWORD}).ok
        page = ctx.new_page()
        page.goto(base + "/", wait_until="networkidle", timeout=90000)
        page.wait_for_function("typeof switchTab === 'function' && window.AUTH && window.AUTH.authenticated", timeout=60000)
        page.wait_for_timeout(2500)
        assert page.evaluate("[...document.querySelectorAll('.dept-entry')].filter(e => getComputedStyle(e).display !== 'none').length") == 0
        assert page.evaluate("activeTab") != "launcher"
        b.close()
