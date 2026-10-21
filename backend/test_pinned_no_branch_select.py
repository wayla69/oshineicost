"""PR-K hard rule (owner 2026-10-10): a pinned session (role `branch`, later `franchise`) sees NO branch selector on any
supply page — not a hidden <select>, not a disabled one — and the page never asks for the branch list.

Two checks:
  * static (always runs): every branch picker in the page fragments carries data-branch-picker, which is what
    stripBranchPickers() (js/01-core.js) removes for a pinned login, and applyPermissions() calls it;
  * browser (runs where the Playwright client and a Chrome/Chromium are installed — `py -3.14` here; skipped
    otherwise): log in as the branch, walk every reachable page and assert that no <select> in the DOM lists another
    branch, no picker element exists, the shell's branch filter is a hidden field and the branch-list endpoints
    were never requested.
"""
import re
import socket
import sqlite3
import threading
import time
from pathlib import Path

import pytest

import auth

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TOKEN = "owner-token-xyz"


# ── static ───────────────────────────────────────────────────────────────────
def test_every_branch_picker_in_the_fragments_is_tagged():
    """A <select id="…Branch"> without data-branch-picker would survive for a pinned login. (Selects that filter
    something else, such as bbomBranch on the BoM analysis page, are not supply pickers and are not listed.)"""
    supply = ("07-ordering", "08-ordersetup", "51-ingpurchase", "52-bomcenter", "53-branchstock", "54-orderhub", "55-supplyhome")
    found = 0
    for name in supply:
        html = (ROOT / "tabs" / f"{name}.html").read_text(encoding="utf-8")
        for m in re.finditer(r"<select\b[^>]*\bid=\"([A-Za-z0-9]*Branch)\"[^>]*>", html):
            found += 1
            assert "data-branch-picker" in m.group(0), (name, m.group(1))
    assert found >= 10, found                                           # the sweep really saw the pickers


def test_the_strip_is_wired_in_and_the_branch_list_is_skipped_for_a_pinned_login():
    core = (ROOT / "js" / "01-core.js").read_text(encoding="utf-8")
    assert "function stripBranchPickers()" in core and re.search(r"applyPermissions\(\)[\s\S]*?stripBranchPickers\(\);", core)
    assert "select[data-branch-picker]" in core and "'branch', 'franchise'" in core
    assert "if (isPinnedSession()) return;" in (ROOT / "js" / "03-tabs-main.js").read_text(encoding="utf-8")   # loadBranches
    brn = (ROOT / "js" / "14-branch.js").read_text(encoding="utf-8")
    assert "if (isPinnedSession())" in brn and brn.index("if (isPinnedSession())") < brn.index("${API}/api/bom-center/stock/me")


def test_the_franchise_page_has_no_branch_picker_and_never_asks_for_the_branch_list():
    """PR-L: ร้านของฉัน shows the branch as text. Its fragment holds no <select> but the month picker, and its slice
    never calls the branch-list endpoints; the one sidebar entry is the only one a franchise login keeps."""
    html = (ROOT / "tabs" / "56-franchise.html").read_text(encoding="utf-8")
    assert [m.group(1) for m in re.finditer(r"<select\b[^>]*\bid=\"([A-Za-z0-9]+)\"", html)] == ["frcMonth"]
    assert "data-branch-picker" not in html and 'id="frcBranchName"' in html
    js = (ROOT / "js" / "17-franchise.js").read_text(encoding="utf-8")
    assert not re.search(r"/api/branches|/stock/me|branch_id|getBranch\(", js)
    core = (ROOT / "js" / "01-core.js").read_text(encoding="utf-8")
    assert "window.AUTH.role === 'franchise'" in core and "el.id === 'nav-franchise'" in core
    assert "'branch', 'franchise'" in core


# ── browser ──────────────────────────────────────────────────────────────────
@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    yield


@pytest.fixture
def live_server(client, app_db, auth_on):
    """The app on a real port, on the test database (the Playwright browser cannot use the TestClient)."""
    import uvicorn
    import main
    client.get("/api/bom-center/hub/queue")                           # creates the bc_* tables
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    cfg = uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(cfg)
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        srv.should_exit = True
        th.join(timeout=10)


def _browser(p):
    for kw in ({"channel": "chrome"}, {}):
        try:
            return p.chromium.launch(headless=True, **kw)
        except Exception:
            continue
    pytest.skip("no Chrome / Chromium for Playwright")


def test_a_pinned_branch_login_sees_no_branch_selector_anywhere(live_server):
    pw = pytest.importorskip("playwright.sync_api")
    import main
    base = live_server
    with pw.sync_playwright() as p:
        b = _browser(p)
        mg = b.new_context()
        assert mg.request.post(base + "/api/auth/login", data={"username": "management1", "password": auth.DEFAULT_PASSWORD}).ok
        r = mg.request.post(base + "/api/auth/users", headers={"X-Upload-Token": TOKEN},
                            data={"username": "br_pin_a", "password": "Temp@12345", "role": "branch", "branch": "BranchA"})
        assert r.status in (200, 409), r.text()
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        assert ctx.request.post(base + "/api/auth/login", data={"username": "br_pin_a", "password": "Temp@12345"}).ok
        page = ctx.new_page()
        seen = []
        page.on("request", lambda rq: seen.append(rq.url))
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/", wait_until="networkidle", timeout=90000)
        page.wait_for_function("typeof supplyGo === 'function' && window.AUTH && window.AUTH.role === 'branch'", timeout=30000)
        # PR-D3: a phone opens on the launcher; the shell (fragments + slices) boots on the first page opened from it
        page.wait_for_function("document.body.classList.contains('ph-on')", timeout=30000)
        page.evaluate("phOpenPage(() => {})")
        page.wait_for_function("_desktopBooted === true", timeout=90000)
        page.wait_for_timeout(1000)
        other = "BranchB"
        panes = [("supplyhome", None), ("branchstock", "issue"), ("branchstock", "order"), ("branchstock", "recv"),
                 ("branchstock", "var"), ("branchstock", "count")]
        for tab, pane in panes:
            page.evaluate("([t, p]) => supplyGo({tab: t, pane: p})", [tab, pane])
            page.wait_for_timeout(1500)
            bad = page.evaluate("""(other) => ({
                naming: [...document.querySelectorAll('select')].filter(s => [...s.options].some(o => o.textContent.includes(other))).map(s => s.id || '(select)'),
                pickers: [...document.querySelectorAll('select[data-branch-picker], #brnBranch, #ohbIvBranch, #ohbIsBranch, #ohbCntBranch, #bmcIssBranch, #bmcFcBranch, #shmBranch')].map(e => e.id),
                ids: [...document.querySelectorAll('#tab-supplyhome select, #tab-bomcenter select, #tab-orderhub select, #tab-branchstock select, #tab-ordering select, #tab-ingpurchase select, #tab-ordersetup select')].map(s => s.id).filter(i => /branch/i.test(i)),
                shell: (document.getElementById('selBranch') || {}).tagName })""", other)
            assert not bad["naming"] and not bad["pickers"] and not bad["ids"], (tab, pane, bad)
            assert bad["shell"] == "INPUT", bad                           # the shell's branch filter is a hidden field
            assert page.evaluate("document.querySelector('#brnBranchName')?.textContent || ''") in ("BranchA", "")
        page.evaluate("supplyGo({tab: 'branchstock', pane: 'issue'})")
        page.wait_for_timeout(1500)
        assert "BranchA" in page.evaluate("document.querySelector('#brnBranchName').textContent")   # the branch is text
        forbidden = [u for u in seen if re.search(r"/api/branches(\?|$)|/stock/me", u)]
        assert not forbidden, forbidden                                   # the branch list is never requested
        assert not [e for e in errors if "Branch" in e or "null" in e], errors
        b.close()


def test_a_pinned_franchise_login_sees_no_branch_selector_and_one_sidebar_entry(live_server):
    """PR-L: the same rule for the owner's login - ร้านของฉัน is its only page, the branch is text, no picker."""
    pw = pytest.importorskip("playwright.sync_api")
    base = live_server
    with pw.sync_playwright() as p:
        b = _browser(p)
        mg = b.new_context()
        assert mg.request.post(base + "/api/auth/login", data={"username": "management1", "password": auth.DEFAULT_PASSWORD}).ok
        r = mg.request.post(base + "/api/auth/users", headers={"X-Upload-Token": TOKEN},
                            data={"username": "own_pin_b", "password": "Temp@12345", "role": "franchise", "branch": "BranchB"})
        assert r.status in (200, 409), r.text()
        ctx = b.new_context(viewport={"width": 375, "height": 812}, is_mobile=True, has_touch=True)
        assert ctx.request.post(base + "/api/auth/login", data={"username": "own_pin_b", "password": "Temp@12345"}).ok
        page = ctx.new_page()
        seen, errors = [], []
        page.on("request", lambda rq: seen.append(rq.url))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/", wait_until="networkidle", timeout=90000)
        page.wait_for_function("typeof switchTab === 'function' && window.AUTH && window.AUTH.role === 'franchise'", timeout=30000)
        page.wait_for_function("document.body.classList.contains('ph-on')", timeout=30000)            # PR-D3: the phone shell; ร้านของฉัน is one tap away
        page.evaluate("phOpenPage(() => switchTab('franchise'))")
        page.wait_for_function("document.getElementById('tab-franchise') && document.getElementById('tab-franchise').style.display !== 'none'", timeout=90000)
        page.wait_for_timeout(2500)
        bad = page.evaluate("""() => ({
            naming: [...document.querySelectorAll('select')].filter(s => [...s.options].some(o => o.textContent.includes('BranchA'))).map(s => s.id || '(select)'),
            pickers: [...document.querySelectorAll('select[data-branch-picker], #brnBranch, #ohbIvBranch, #bmcIssBranch, #bmcFcBranch, #shmBranch')].map(e => e.id),
            nav: [...document.querySelectorAll('.sidebar-nav .nav-item')].filter(e => getComputedStyle(e).display !== 'none').map(e => e.id),
            shell: (document.getElementById('selBranch') || {}).tagName, name: document.getElementById('frcBranchName').textContent })""")
        assert not bad["naming"] and not bad["pickers"], bad
        assert bad["nav"] == ["nav-franchise"] and bad["shell"] == "INPUT" and bad["name"] == "BranchB", bad
        page.evaluate("switchTab('home')")                                # no other page opens
        page.wait_for_timeout(500)
        assert page.evaluate("activeTab") == "franchise"
        forbidden = [u for u in seen if re.search(r"/api/branches(\?|$)|/stock/me", u)]
        assert not forbidden, forbidden
        assert not errors, errors
        b.close()
