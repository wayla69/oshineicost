"""RV-M (2026-10-24): the pre-launch review of every page and every function. One test per bug it fixed.

Static checks always run; the browser checks run where the Playwright client and a Chrome are installed (`py -3.14` here) and are
skipped otherwise, like test_phone_modes.py.
"""
import re
from pathlib import Path

import pytest

import auth
from test_pinned_no_branch_select import _browser, auth_on, live_server  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parent.parent


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _lum(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4   # noqa: E731
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])


def _cr(a, b):
    x, y = _lum(a), _lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


# ── the golden of /api/feedback/business depended on test order ──────────────────────────────────────────────────────────────
def test_the_fixture_has_the_bill_item_table_so_the_business_golden_does_not_depend_on_test_order(client):
    """The golden was captured after test_feedback_business.py had created sale_bill_item in the session DB; alone (or in a split
    run) the endpoint saw no bill tables and answered "branches": []. The fixture now has the table the sync always creates."""
    assert "CREATE TABLE IF NOT EXISTS sale_bill_item" in _src("backend/conftest.py")
    j = client.get("/api/feedback/business", params={"month_from": "2026-05", "month_to": "2026-06", "today": "2026-07-01", "fresh": 1}).json()
    assert [b["branch"] for b in j["branches"]] == ["BranchA", "BranchB"] and j["chain"]["rev"] == 12840 and j["load"]["days"] == 12


# ── ตัวจำลอง ran 10 px past a phone in แตะง่าย ───────────────────────────────────────────────────────────────────────────────
def test_a_sub_tab_row_that_is_not_a_subtab_bar_wraps_on_a_phone():
    html = _src("index.html")
    assert ".content-area :not(.subtab-bar) > .subtab-btn:where(:not(.ph-frame *)) { flex: 1 1 auto; }" in html
    sim = _src("tabs/25-simhub.html")
    assert "#faf5ff" not in sim and "BoM Cost · Cost Sensitivity" not in sim      # the title card follows the theme; Thai help


# ── contrast ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_sticky_first_head_cell_on_the_band_takes_the_band_ink_and_flat_tables_keep_their_own():
    css = _src("docs/ui-kit/theme-polish.css")
    rule = re.search(r"\.content-area table thead th:first-child:not\(([^{]+)\) \{ background: var\(--accent\); color: #ffffff !important; \}", css)
    assert rule, "the phone rule paints the band colour AND the white ink"
    for flat in (".tbl-top60 th", ".heatmap-table th", ".frc-tbl th", '[style*="background"]', "table:not(.wt) th"):
        assert flat in rule.group(1), flat


def test_the_active_chips_of_dark_mode_clear_4_5_to_1():
    css = _src("docs/ui-kit/theme-vivid.css")
    m = re.search(r"body\.dark \.lnch-tab\[aria-pressed=\"true\"\], body\.dark \.ms-seg-on \{ background: linear-gradient\(135deg, (#[0-9a-f]{6}), (#[0-9a-f]{6})\) !important; color: (#[0-9a-f]{6})", css)
    assert m
    assert min(_cr(m.group(3), m.group(1)), _cr(m.group(3), m.group(2))) >= 4.5


def test_every_dark_group_ink_clears_4_5_to_1_on_its_own_tint():
    css = _src("docs/ui-kit/theme-groups.css")
    dark = css[css.index("body.dark {"):]
    dark = dark[:dark.index("}")]
    pairs = re.findall(r"--g-([a-z]+)-ink: (#[0-9a-f]{6});\s+--g-\1-soft: (#[0-9a-f]{6});", dark)
    assert len(pairs) >= 10
    low = [(g, round(_cr(i, s), 2)) for g, i, s in pairs if _cr(i, s) < 4.5]
    assert not low, low


def test_header_inks_and_carets_follow_the_band():
    assert 'class="bench-caret text-indigo-500"' not in _src("tabs/20-benchmark.html")
    me = _src("tabs/13-menuefficiency.html")
    assert not re.search(r"<th[^>]*text-slate-400[^>]*>(หมวด|ประเภท)</th>", me)
    css = _src("docs/ui-kit/theme-polish.css")
    assert "body :is(thead th, .wt th) .sub { color: color-mix(in srgb, currentColor 82%, transparent); }" in css
    assert "> .tbl-stick,\nbody.ph-on.ph-fit .ph-frame table tbody tr:is(" in css.replace("\r\n", "\n")   # a tinted row's sticky cell
    assert '<p class="text-xs" style="color:var(--amber-ink)">⚠ <span id="profitCoverageMsg">' in _src("tabs/44-profit.html")
    assert ".text-pink-600, .text-pink-700 { color: var(--pink-ink) !important; }" in _src("index.html")


# ── 44 px targets in แตะง่าย ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_clickable_rows_cells_file_pickers_and_lone_links_are_44px_in_touch():
    css = _src("docs/ui-kit/theme-polish.css")
    for rule in (":is(tr[onclick] > td, td[onclick], th[onclick]) { height: 44px !important; }", ":is(div, li)[onclick] { min-height: 44px; }",
                 'input[type="file"] { min-height: 44px; }'):
        assert rule in css, rule


# ── wording and numbers ──────────────────────────────────────────────────────────────────────────────────────────────────────
def test_no_english_sentences_left_on_the_cost_and_pnl_pages_and_fc_percent_is_capped():
    for rel, s in (("tabs/45-pnl.html", "Click headers to sort"), ("tabs/43-cost.html", "unlock the full margin"),
                   ("js/05-pnl-settings.js", "Margin rate used"), ("js/05-pnl-settings.js", "Est. GP/bill"), ("js/07-home.js", "market context unavailable")):
        assert s not in _src(rel), (rel, s)
    assert "fcRaw != null && fcRaw <= 999 ? fcRaw : null" in _src("js/08-guest-buffet.js")


# ── found by the flow runs ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_every_csv_the_page_writes_passes_the_formula_guard():
    """ส่วนลด's reason export wrote a POS reason "=1+1" raw: downloadCSV (8 callers) now escapes it, numbers stay numbers."""
    from test_less_text import NODE, _fn, _node
    if NODE is None:
        pytest.skip("node is not installed")
    src = _src("js/05-pnl-settings.js")
    assert "new Blob(['﻿' + csvGuard(text)]" in src
    got = _node(_fn(src, "csvGuard"), 'csvGuard(\'a,=1+1,"@SUM(A1), x",-12.5,+3,-,"he said ""hi""",-x\\n2,\\t9,ok\')')
    assert got == 'a,"\'=1+1","\'@SUM(A1), x",-12.5,+3,-,"he said ""hi""","\'-x"\n2,"\'\t9",ok'


def test_a_counter_line_carries_a_save_result_to_its_toast():
    """ทะเบียนวัตถุดิบ reports its save on bmcIngSummary; the counter rule swallowed the success and the refusal (no toast)."""
    from test_less_text import NODE, _kind
    if NODE is None:
        pytest.skip("node is not installed")
    assert _kind("บันทึก 1 รายการแล้ว", "var(--good-ink)", "bmcIngSummary")["toast"] == "success"
    assert _kind("ไม่สำเร็จ: x", "var(--bad-ink)", "bmcIngSummary")["toast"] == "error"
    assert _kind("12 จาก 274 รายการ · มีปัญหา 3", None, "bmcIngSummary") == {"inline": "note"}


def test_the_flow_fixes_are_wired():
    core, bmc, frc, ohb = _src("js/01-core.js"), _src("js/13-bom-center.js"), _src("js/17-franchise.js"), _src("js/15-orderhub.js")
    assert "if (tok && window.writeToken.unlocked == null && typeof verifyWriteToken === 'function') verifyWriteToken();" in core
    assert "await bmcLoadDishCost('reset');\n    bmcDcView('imported');" in bmc.replace("\r\n", "\n")
    assert frc.count("t => t.status === 'closed' && !t.unread") == 2           # an unread reply keeps a closed thread in the default list
    assert "const cur = _ohbIsWant != null ? String(_ohbIsWant) : sel.value;" in ohb   # การใช้งานทุกสาขา -> ใบตัดเบิกสาขา of that branch
    assert "ต่อไป: สาขา" in ohb                                                    # the count form says where it moved on to
    br = _src("js/14-branch.js")
    assert "brnConfirmRecv(false, ${Number(eid)})" in br and "tick: 'brnRcSelect'" in br   # stock mode: month close + ticks on สิ้นเดือน
    assert 'id="phBrnSearch" onclick="phOpenSearch()"' in _src("js/09c-mobile.js")
    assert "const must = new Set(all.filter(x => x.needs_reason)" in ohb           # every line that needs a reason is listed


# ── browser ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_simhub_in_touch_and_a_fit_table_head_on_a_phone(live_server):
    pw = pytest.importorskip("playwright.sync_api")
    base = live_server
    with pw.sync_playwright() as p:
        b = _browser(p)
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        assert ctx.request.post(base + "/api/auth/login", data={"username": "management1", "password": auth.DEFAULT_PASSWORD}).ok
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/", wait_until="networkidle", timeout=90000)
        page.wait_for_function("document.body.classList.contains('ph-on') && document.querySelectorAll('#phNav .ph-nav-i').length", timeout=60000)
        page.evaluate("async () => { await phOpenPage(() => switchTab('simhub')); }")
        page.wait_for_timeout(2500)
        if page.evaluate("document.body.classList.contains('ph-fit')"):
            page.evaluate("phModeToggle()")
            page.wait_for_timeout(2000)
        w = page.evaluate("(() => { const ca = document.querySelector('.content-area'); return [ca.scrollWidth, ca.clientWidth, document.body.classList.contains('ph-fit')]; })()")
        assert w[2] is False and w[0] <= w[1], w
        page.evaluate("async () => { await phOpenPage(() => switchTab('pnl')); }")
        page.wait_for_timeout(2500)
        if not page.evaluate("document.body.classList.contains('ph-fit')"):
            page.evaluate("phModeToggle()")
            page.wait_for_timeout(2000)
        ink = page.evaluate("""(() => { const th = [...document.querySelectorAll('#tab-pnl table thead th:first-child')].find(e => e.offsetParent);
            return th ? [getComputedStyle(th).color, getComputedStyle(th).backgroundColor] : null; })()""")
        assert ink and ink[0] == "rgb(255, 255, 255)", ink
        assert not errors, errors
        b.close()
