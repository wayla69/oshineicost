"""PR-MA (owner 2026-10-24): the phone shell for the whole app - two display modes, row sheets, one bottom bar per role.

  * แตะง่าย (touch) = the phone layout (12 px text floor, 44 px targets, stacked cards); พอดีจอ (fit) = the page's desktop layout in a frame
    760-900 px wide (820 px when its width cannot be read) scaled to the screen width with CSS zoom. The lead's rule (2026-10-24, on the owner's
    "ตัวเล็กและมีซูมได้") replaced the brief's 10.5 px: at rest the smallest text is >= 5.5 px on a 390 px screen, and >= 12 px after a double tap
    shows a table or a chart at 100 %. So the frame is never wider than the width at which 12 px reads 5.5 px, and the text in it is >= 12 px.
  * every page of a department or of หน้ารายงานทั้งหมด names its default in PAGE_PHONE (js/01-core.js; scripts/check-departments.js fails without);
  * one bottom bar per role (_PH_BARS, js/09c-mobile.js): its pages are pages the role may open, the pages with tasks carry their nav_badges key;
    ค้นหา is the magnifier of the header.

Static and node checks here; the browser checks are tests/e2e/phone-modes.spec.js (menu, gpbranch, orderhub|queue, bomcenter|dishcost).
"""
import json
import re
import subprocess
import tempfile
from pathlib import Path

from test_bom_center_batch2 import _fn, _node, needs_node, NODE

ROOT = Path(__file__).resolve().parent.parent


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


MOB, CORE, HTML, POLISH = _src("js/09c-mobile.js"), _src("js/01-core.js"), _src("index.html"), _src("docs/ui-kit/theme-polish.css")
MODES = MOB[MOB.index("PHONE DISPLAY MODES (PR-MA"):MOB.index("// ── Service worker (sw.js)")]
REG = CORE[CORE.index("const _SUPPLY_CENTRE"):CORE.index("// ── end page registry")]

# the brief's per-page defaults (with the lead's decisions of 2026-10-24 for the pages the brief did not name)
FIT = ["overall", "dailysales", "trend", "gpbranch", "pnlbranch", "cost", "buffetperf", "childbuffet", "menu", "menuactionboard",
       "menuopportunities", "menucompare", "feedback", "custtype", "timeflow", "opsreport", "discounts", "cashctl", "cancelbill", "staff", "modifiers",
       "pricezone", "closetime", "costhead", "coverage", "menuefficiency", "pricetier", "simhub", "profit", "benchmark", "pnl", "pnlbill", "orderhub|comp",
       "orderhub|issues", "orderhub|issuevar", "orderhub|adoption", "orderhub|queue", "orderhub|claims", "orderhub|persales", "bomcenter|fc",
       "bomcenter|price", "bomcenter|issue", "fmsg",
       "guest", "compare", "more", "heavyeater", "bomcost", "menuimpact", "branchbom", "ops", "report", "action", "checklist", "ordering",
       "ingpurchase", "ordersetup|pack", "orderhub|month", "bomcenter|rcheck", "bomcenter|overview"]
# DV5 (phone table review 2026-10-25): รายงานผู้บริหาร opens in touch on a phone - the answer strip, the PDF and the summary
TOUCH = ["mgtreport", "branchstock|issue", "branchstock|order", "branchstock|recv", "branchstock|var", "branchstock|count", "franchise", "supplyhome", "launcher",
         "home", "settings", "bomcenter|cal", "bomcenter|events", "bomcenter|zones", "bomcenter|codereq", "orderhub|central", "bomcenter|pricefile",
         "bomcenter|rec", "bomcenter|prep", "bomcenter|conv"]


def _registry(expr):
    # the registry block is long: node reads it from a file (a command line has a length limit on Windows)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(REG + f"\nconsole.log(JSON.stringify({expr}));")
    try:
        return json.loads(subprocess.run([NODE, f.name], capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    finally:
        Path(f.name).unlink()


# ── the defaults ─────────────────────────────────────────────────────────────────────────────────────────────────────────
@needs_node
def test_every_page_of_the_registry_has_a_phone_default():
    keys = _registry("""(() => { const s = new Set(); DEPARTMENTS.forEach(d => d.pages.forEach(p => s.add(p.tab + (p.pane ? '|' + p.pane : ''))));
        allPagesModel(() => true).forEach(g => g.entries.forEach(e => s.add(e.tab + (e.pane ? '|' + e.pane : ''))));
        return [...s].map(k => [k, PAGE_PHONE[k] === undefined ? null : PAGE_PHONE[k]]); })()""")
    assert len(keys) >= 80
    missing = [k for k, v in keys if v is None]
    assert not missing, missing
    for k, v in keys:
        vals = list(v.values()) if isinstance(v, dict) else [v]
        assert vals and set(vals) <= {"fit", "touch"}, (k, v)


@needs_node
def test_the_defaults_follow_the_brief():
    got = _registry("Object.fromEntries(%s.map(k => [k, phonePageDefault(...k.split('|'))]))" % json.dumps(FIT + TOUCH))
    assert [k for k in FIT if got[k] != "fit"] == []
    assert [k for k in TOUCH if got[k] != "touch"] == []
    # two views: the dish cost table / the ingredient list fit, their import / edit touch
    two = _registry("[phonePageDefault('bomcenter', 'dishcost', 'table'), phonePageDefault('bomcenter', 'dishcost', 'import'),"
                    " phonePageDefault('bomcenter', 'ing', 'list'), phonePageDefault('bomcenter', 'ing', 'edit'), phonePageDefault('nosuchpage', '')]")
    assert two == ["fit", "touch", "fit", "touch", "touch"]


@needs_node
def test_the_registry_check_fails_a_page_without_a_default():
    out = subprocess.run([NODE, str(ROOT / "scripts" / "check-departments.js")], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    assert re.search(r"phone defaults - \d+ pages open in พอดีจอ \(fit\), \d+ in แตะง่าย \(touch\)", out.stdout)
    chk = _src("scripts/check-departments.js")
    assert "has no phone default" in chk and "PAGE_PHONE" in chk


# ── the fit frame ────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_fit_frame_rule_is_the_leads_rule():
    assert "const _PH_FIT_MIN = 760, _PH_FIT_MAX = 900, _PH_FIT_DEF = 820, _PH_FIT_TEXT = 12, _PH_FIT_FLOOR = 5.5" in MODES
    lay = _fn(MODES, "phFitLayout")
    assert "Math.floor(avail * _PH_FIT_TEXT / _PH_FIT_FLOOR)" in lay                                   # never so wide that 12 px reads < 5.5 px
    assert "Math.min(Math.max(nat, _PH_FIT_MIN), _PH_FIT_MAX, cap)" in lay
    assert "panel.style.zoom" in lay and "scale(${s})" in lay                                          # zoom, transform as the fallback
    assert "_PH_FIT_TEXT + 'px', 'important'" in _fn(MODES, "_phFitFloor")                           # the text floor inside the frame
    assert "10.5" not in MODES


@needs_node
def test_the_frame_width_for_a_phone():
    lay = ("const _PH_FIT_MIN = 760, _PH_FIT_MAX = 900, _PH_FIT_DEF = 820, _PH_FIT_TEXT = 12, _PH_FIT_FLOOR = 5.5;\n"
           "function w(avail, nat) { const cap = Math.floor(avail * _PH_FIT_TEXT / _PH_FIT_FLOOR); const W = Math.min(Math.max(nat || _PH_FIT_DEF, _PH_FIT_MIN), _PH_FIT_MAX, cap);"
           " return [W, +(12 * avail / W).toFixed(2)]; }")
    # a 390 px phone (382 px inside the 4 px side padding of fit) and a 430 px one, for a narrow, a default and a wide page
    got = _node(lay, "[w(382, 500), w(382, 0), w(382, 1400), w(422, 1400)]")
    assert got[0][0] == 760 and got[1][0] == 820 and got[2][0] == 833 and got[3][0] == 900
    assert all(x[1] >= 5.5 for x in got)


def test_the_phone_rules_of_the_touch_layout_stay_out_of_the_frame():
    for needle in (':where(body:not(.ph-fit)) .content-area [class*="grid-cols-3"]', ':where(body:not(.ph-fit)) .content-area div.flex:has(> div > canvas)',
                   ':where(body:not(.ph-fit)) .wt thead { display: none; }', ':where(body:not(.ph-fit)) .content-area table td:first-child'):
        assert needle in HTML, needle
    assert ':where(:not(.ph-frame *)) { min-height: 44px !important; }' in POLISH
    assert ':where(body:not(.ph-fit)) .content-area [class~="grid-cols-1"][class*="lg:grid-cols-"]' in POLISH
    # the desktop columns of Tailwind (keyed to the viewport) apply inside the frame; the header row and the first column stay pinned
    css = HTML[HTML.index("PHONE DISPLAY MODES (PR-MA"):]
    assert r"body.ph-on.ph-fit .ph-frame .md\:grid-cols-4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }" in css
    assert "body.ph-on.ph-fit .ph-frame thead th { position: sticky; top: 0;" in css
    assert "body.ph-on.ph-fit .ph-frame table :is(tbody td, tfoot td, tfoot th):first-child { position: sticky; left: 0;" in css
    assert "body.ph-on.ph-fit .content-area { padding-left: 4px; padding-right: 4px; overflow-x: hidden; }" in css
    # the charts of a fit page are the desktop charts, with no font under 12 px; the order queue is the desktop table
    assert "if (document.body.classList.contains('ph-fit')) return false;" in _fn(CORE, "_onPhoneScreen")
    assert "_fitChartFonts(o)" in _fn(CORE, "mkChart")
    assert "!document.body.classList.contains('ph-fit')" in _src("js/15-orderhub.js")


def test_pinch_zoom_stays_the_browsers():
    vp = re.search(r'<meta name="viewport" content="([^"]+)"', HTML).group(1)
    assert "user-scalable=no" not in vp and "maximum-scale=1" not in vp.replace(" ", "")


@needs_node
def test_the_fit_chart_font_floor():
    pre = _fn(CORE, "_fitChartFonts")
    got = _node(pre, "(() => { const o = { plugins: { legend: { labels: { font: { size: 10 } } }, datalabels: { font: { size: 11, weight: 600 } } },"
                     " scales: { x: { ticks: { font: { size: 14 } } }, y: { ticks: { font: {} } } } }; _fitChartFonts(o); return o; })()")
    assert got["plugins"]["legend"]["labels"]["font"]["size"] == 12 and got["plugins"]["datalabels"]["font"] == {"size": 12, "weight": 600}
    assert got["scales"]["x"]["ticks"]["font"]["size"] == 14 and got["scales"]["y"]["ticks"]["font"]["size"] == 12


# ── the toggle, the row sheet, the double tap ───────────────────────────────────────────────────────────────────────────
def test_the_toggle_sits_in_the_phone_top_bar_and_is_remembered_per_page():
    bar = HTML[HTML.index('<div class="top-bar">'):HTML.index('<!-- ░░ ACTIONS zone')]
    for needle in ('id="phModeBtn"', 'onclick="phModeToggle()"', 'id="phModeL"', 'id="phTbSearch"', 'onclick="phOpenSearch()"', 'id="phTbAv"', 'onclick="phOpenMe()"'):
        assert needle in bar, needle
    assert "#phModeBtn, #phTbSearch, #phTbAv { display: none; }" in HTML                                # a desktop never sees them
    tog = _fn(MODES, "phModeToggle")
    assert "localStorage.setItem(_PH_MODE_KEY" in tog and "try {" in tog
    assert "_phPageKey(pg)" in _fn(MODES, "phModeOf")
    assert "phFitSchedule()" in _fn(CORE, "_updateClusterBar") and "phFitApply();" in _fn(MOB, "_phSetView")
    # ตัวอักษรใหญ่ of the branch pages stays: the branch login never gets the fit frame
    assert "!document.body.classList.contains('supply-phone')" in _fn(MODES, "_phFitAllowed")
    assert "function brnBigToggle" in _src("js/14-branch.js") or "brn-big" in _src("js/14-branch.js")


def test_a_row_opens_the_sheet_with_its_columns_status_and_actions():
    sheet = _fn(MODES, "phRowSheet")
    assert "_phHeads(table, n)" in sheet and "ph-rs-row" in sheet and "ph-rs-pill" in sheet and "phRowAct(" in sheet
    assert "sh.classList.add('open', 'ph-rowsheet')" in sheet                                           # the existing sheet (#msSheet)
    close = _fn(MOB, "msCloseSheet")
    assert "'ph-rowsheet'" in close and "phSheetBack" in close
    assert 'id="phSheetBack" hidden onclick="msCloseSheet()"' in HTML                                    # a tap outside closes
    assert "dy > 80" in MOB and "msCloseSheet(); return;" in MOB                                          # a swipe down closes
    # a button, a link or a field of a row does its own job; a double tap (two taps within 350 ms) zooms instead of opening the sheet
    assert "if (t.closest(_PH_INTERACTIVE)) return;" in MODES and "now - _phTap.t < 350" in MODES and "phZoomToggle(zt)" in MODES


@needs_node
def test_the_row_sheet_reads_the_headers_with_their_spans():
    pre = _fn(MODES, "_phHeads")
    expr = """(() => { const c = (t, s) => ({ innerText: t, colSpan: s || 1 });
      const table = { tHead: { rows: [ { cells: [c('สาขา'), c('ยอดขาย', 2)] }, { cells: [c(''), c('บาท'), c('%')] } ] } };
      return _phHeads(table, 3); })()"""
    assert _node(pre, expr) == ["สาขา", "บาท", "%"]


# ── the role bars, the header search, ฉัน ───────────────────────────────────────────────────────────────────────────────
ROLE_PAGES_JS = r"""
const fs = require('fs'), vm = require('vm');
const core = fs.readFileSync(%(core)s, 'utf8'), launcher = fs.readFileSync(%(launcher)s, 'utf8'), mob = fs.readFileSync(%(mob)s, 'utf8');
const fnSrc = (src, name) => { const m = new RegExp('^(async )?function ' + name + '\\(', 'm').exec(src); const open = src.indexOf('{', src.indexOf(')', m.index));
  let d = 0, j = open; for (; j < src.length; j++) { if (src[j] === '{') d++; else if (src[j] === '}' && --d === 0) break; } return src.slice(m.index, j + 1); };
const a = core.indexOf('const _SUPPLY_CENTRE'), b = core.indexOf('// ── end page registry');
const ctx = vm.createContext({ console, window: { AUTH: { enabled: true, role: 'management', modules: [] } }, document: {}, localStorage: {}, navigator: {} });
vm.runInContext('var BC_MODE = "stock";', ctx);
vm.runInContext(core.slice(core.indexOf('const TAB_MODULE'), core.indexOf('function firstAllowedTab')), ctx);
vm.runInContext(core.slice(a, b), ctx);
vm.runInContext(['_supplyRoleOk', '_supplyItemAllowed'].map(n => fnSrc(core, n)).join('\n') + '\n' + ['_deptRole', 'deptPageAllowed'].map(n => fnSrc(launcher, n)).join('\n'), ctx);
vm.runInContext(/const _PH_BARS = \{[\s\S]*?\n\};/.exec(mob)[0].replace('const _PH_BARS', 'var _PH_BARS') + '\n' + /const _PH_HOME_TAB = \{.*?\};/.exec(mob)[0].replace('const ', 'var '), ctx);
const out = {};
for (const role of Object.keys(vm.runInContext('_PH_BARS', ctx))) for (const mode of ['stock', 'full']) {
  vm.runInContext(`BC_MODE = '${mode}'; window.AUTH = { enabled: true, role: '${role}', modules: ROLE_MODULES_TH['${role}'] };`, ctx);
  out[role + ':' + mode] = vm.runInContext(`_PH_BARS['${role}'].map(p => [p.label, deptPageAllowed(p)]).concat(_PH_HOME_TAB['${role}'] ? [['home:' + _PH_HOME_TAB['${role}'], tabAllowed(_PH_HOME_TAB['${role}'])]] : [])`, ctx);
}
console.log(JSON.stringify(out));
"""


@needs_node
def test_every_entry_of_a_role_bar_is_a_page_that_role_may_open():
    js = ROLE_PAGES_JS % {k: json.dumps(str(ROOT / p)) for k, p in (("core", "js/01-core.js"), ("launcher", "js/18-launcher.js"), ("mob", "js/09c-mobile.js"))}
    got = json.loads(subprocess.run([NODE, "-e", js], capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    assert len(got) == 16
    bad = {k: [x for x in v if not x[1]] for k, v in got.items() if any(not x[1] for x in v)}
    assert not bad, bad


def test_search_is_the_magnifier_of_the_header():
    hero = _fn(MOB, "_phHero")
    assert 'id="phHeroSearch" onclick="phOpenSearch()"' in hero
    # phone B (owner 2026-10-30): the avatar (ฉัน) is in the one header above the hero
    top = HTML[HTML.index('<header id="phTop"'):]
    assert 'id="phTopAv" onclick="phOpenMe()"' in top[:top.index("</header>")]
    bars = re.search(r"const _PH_BARS = \{.*?\n\};", MOB, re.S).group(0)
    assert "ค้นหา" not in bars and "phOpenSearch" not in _fn(MOB, "_phNavItems")
    assert "phHomeTap()" in _fn(MOB, "_phNavItems") and "nb: 't'" in _fn(MOB, "_phNavItems")


def test_me_starts_with_the_last_three_pages():
    me = HTML[HTML.index('id="phv-me"'):]
    me = me[:me.index("</section>")]
    assert me.index('id="phMeRecentWrap"') < me.index('id="phMeDark"')                                  # first, as big tiles
    assert "หน้าที่เปิดล่าสุด" in me
    add = _fn(MODES, "phRecentAdd")
    assert "slice(0, 3)" in add and "try {" in add and "_phRecentHtml()" in _fn(MOB, "phMeRender")


def test_the_top_bar_title_is_one_line_and_the_coverage_chip_short():
    css = HTML[HTML.index("PHONE DISPLAY MODES (PR-MA"):]
    assert "body.ph-on .topbar-scope > .page-title { order: 0; flex: 1 1 0%; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" in css
    # owner 2026-10-26 ("ท… ▾" at 360 px): two rows - the title beside the tools, then the note and the two chips the full width of the bar
    assert "body.ph-on .top-bar .topbar-scope { display: contents; }" in css
    assert "body.ph-on .top-bar::after { content: ''; order: 2; flex: 0 0 100%; height: 0; }" in css
    assert "body.ph-on.tb-folded .top-bar { flex-wrap: nowrap; }" not in css
    assert "body.ph-on #dataCoverageNote .dcn-x { display: none; }" in css
    main = _src("js/03-tabs-main.js")
    assert '<span class="dcn-x">ข้อมูล</span>ถึง ${d} ${TH_MON[m-1]}<span class="dcn-x"> ${y+543}</span>' in main


def test_no_banned_words_and_no_narrative_in_the_new_shell_text():
    new = MODES + _fn(MOB, "_phNavItems") + HTML[HTML.index("PHONE DISPLAY MODES (PR-MA"):HTML.index("PHONE DISPLAY MODES (PR-MA") + 9000]
    for w in ("กรุณา", "ระบบจะ", "ฉบับร่าง"):
        assert w not in new, w
