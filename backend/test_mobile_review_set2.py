# -*- coding: utf-8 -*-
"""Phone review set 2 (owner 2026-10-31, docs/UI_REVIEW_MOBILE_2026-10-31.md items 3, 6, 7, 8, 11; docs/PHONE_PLAN.md §10).

  3  a list row is two fixed lines: the shared row renderer uiRow2 (js/01-core.js) + a status chip of <= 6 characters (uiChipShort);
     the owner's summary lists and every PAGE_CARDS table (phCardList) go through them;
  6  the department pages' tiles carry ONE figure or status, work first, the rest folded under "หน้าอื่น (n)" - from the answers the sales
     hero already loads (brief + branch scorecard) and the nav badges, through the #528 code path (_phTileState / _phDtHtml);
  7  one empty card per page (uiEmpty), empty sub-sections hidden, grey skeletons while loading (uiSkel, .ui-skel-line);
  8  a filter is one row of chips on a phone, downloads under "⋯" (.ui-fbar / .ui-more, uiCompactFilters);
  11 a chart on a phone is at most 220 px; fewer than 3 points is a number, a doughnut / scatter / long bar list a 5-row ranking.
Static pins (the source), plus the pure helpers run in node.
"""
import json
import re
import subprocess
import tempfile
from pathlib import Path

from test_bom_center_batch2 import _fn, needs_node, NODE

ROOT = Path(__file__).resolve().parent.parent


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


CORE, MOB, HTML = _src("js/01-core.js"), _src("js/09c-mobile.js"), _src("index.html")


def _run(js, expr):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js + f"\nconsole.log(JSON.stringify({expr}));")
    try:
        return json.loads(subprocess.run([NODE, f.name], capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    finally:
        Path(f.name).unlink()


ESC = "const esc = s => String(s ?? '').replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));\n"
CHIP = CORE[CORE.index("const _UI_CHIP_SHORT = ["):CORE.index("// uiEmpty(o) - ONE empty state per page")]


# ── item 3 · the shared row ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_one_shared_row_renderer_lives_in_core_and_the_summary_lists_use_it():
    row = _fn(CORE, "uiRow2")
    assert 'class="ms-r2 ui-r2' in row and "ms-r2-a" in row and "ms-r2-b" in row and "ui-r2-bar" in row and "uiChip(" in row
    assert "const msRow2 = o => uiRow2(o);" in MOB and "function msRow2(o) {" not in MOB           # one renderer, the old name delegates
    for fn in ("msCostRisersHtml", "msTopMenuHtml", "msRenderBranches", "msActionsHtml", "msRenderWatch"):
        assert re.search(r"\b(uiRow2|msRow2)\(", _fn(MOB, fn)), fn
    assert "ms-why" not in _fn(MOB, "_msRiserCause")                                               # the cause is one short chip now
    assert "dvMoreBtn('msbr', nB, 10)" in _fn(MOB, "msRenderBranches")                            # 10 branches, then ดูทั้งหมด
    css = HTML
    assert re.search(r"\.ms-r2-t \{[^}]*white-space: nowrap", css) and re.search(r"\.ui-chip \{[^}]*white-space: nowrap", css)


@needs_node
def test_a_status_chip_keeps_at_most_six_characters_and_its_words_in_the_title():
    r = _run(ESC + CHIP + "\n" + _fn(CORE, "uiChip"), """[
        ['ไม่มีในตาราง (ใช้ต้นทุนเดิม)', 'รอยืนยันยอดส่ง', 'คลังจัดส่งแล้ว · PO-1', 'สาขารับแล้ว', 'ส่งช้า (มีค่าปรับ)', 'สาขาแก้ไข 2 รายการ',
         'ในเป้า', '▲5%', 'ยกเลิก', 'รายการที่ยาวมากจริงๆ'].map(uiChipShort),
        ['ไม่มีในตาราง (ใช้ต้นทุนเดิม)', 'รอยืนยันยอดส่ง', 'คลังจัดส่งแล้ว', 'อนุมัติแล้ว', 'ส่งตรงวัน'].map(t => _uiCells(uiChipShort(t)) <= 6),
        uiChip('ไม่มีในตาราง (ใช้ต้นทุนเดิม)', 'warn'), uiChip('ในเป้า', 'good')]""")
    assert r[0] == ["ใช้เดิม", "รอยืนยัน", "ส่งแล้ว", "รับแล้ว", "ส่งช้า", "แก้ไข", "ในเป้า", "▲5%", "ยกเลิก", "รายการ…"]
    assert all(r[1])
    assert r[2] == '<span class="ui-chip" data-tone="watch" title="ไม่มีในตาราง (ใช้ต้นทุนเดิม)" aria-label="ไม่มีในตาราง (ใช้ต้นทุนเดิม)">ใช้เดิม</span>'
    assert r[3] == '<span class="ui-chip" data-tone="good">ในเป้า</span>'


@needs_node
def test_the_row_is_two_lines_with_the_rest_behind_a_tap():
    pre = ESC + CHIP + "\n" + _fn(CORE, "uiChip") + "\n" + _fn(CORE, "uiRow2")
    h = _run(pre, "uiRow2({ title: 'ปลาแซลมอน', val: '+฿321K', valTone: 'bad', tone: 'bad', chip: { t: 'ตักเพิ่ม', tone: 'watch' }, fact: '+฿3.65/หัว · 29%', bar: { pct: 100 }, more: 'ต้นทุนต่อจาน +฿0.10' })")
    a, b = h.index('class="ms-r2-a"'), h.index('class="ms-r2-b"')
    assert a < b and "ปลาแซลมอน" in h[a:b] and "+฿321K" in h[a:b]                                  # line 1: the name and the ONE number
    assert 'data-tone="watch">ตักเพิ่ม' in h[b:] and "ui-r2-bar" in h[b:]                            # line 2: the chip, the fact, the bar
    assert 'class="ms-r2-x">ต้นทุนต่อจาน +฿0.10' in h and 'aria-expanded="false"' in h and "uiRow2Toggle(this,event)" in h


def test_the_card_list_is_two_lines_and_its_pill_a_short_chip():
    cl = _fn(MOB, "phCardList")
    assert 'class="ph-cl-l1"' in cl and 'class="ph-cl-l2"' in cl and "uiChipShort(p.t)" in cl
    assert "ph-cl-figs" not in cl and "ph-cl-own" not in cl                                         # no three-figure row, no chip stack
    spec = _fn(MOB, "_phCardSpec")
    assert "_phPillKey(uiChipShort(ptxt))" in spec and "inkTone(" in spec


# ── item 6 · tiles with status ────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_department_pages_tiles_carry_their_status_through_the_one_code_path():
    vals = _fn(MOB, "_phSalesVals")
    for k in ("v.dailysales", "v.home", "v.discounts", "v.cost", "v.gpbranch"):
        assert k in vals, k
    assert "_PH_DISC_CEIL" in vals and "food_cost_target" in vals and "api(" not in vals
    load = _fn(MOB, "phTileDataLoad")
    assert re.findall(r"api\(`\$\{API\}(/api/[a-z/-]+)`\)", load) == ["/api/home/brief", "/api/home/branch-scorecard"]   # the hero's own two answers
    assert "_msBriefCache['']" in load and "_msScorecard" in load                                    # ... read through the hero's caches
    assert "(h.vals || {})[k]" in _fn(MOB, "_phTileState")
    assert "_phDtHtml(d, { head: false, first: _phSummaryTile(d) })" in _fn(MOB, "phRenderDept")    # ผู้บริหาร / ปฏิบัติการ pages
    assert "_phDtHtml(vis[0].dept)" in _fn(MOB, "_phCentreHome")                                    # operation's own home grid
    assert "_phDeptChip(r)" in _fn(MOB, "_phDeptTiles") and 'id="phDeptGrid"' in _fn(MOB, "_phCentreHome")
    assert "phTilesApply()" in _fn(MOB, "phDeptHeroApply")                                          # the badges' answer redraws them too
    assert "หน้าอื่น (" in _fn(MOB, "_phDtHtml")


# ── item 7 · one empty card, skeletons ────────────────────────────────────────────────────────────────────────────────────────────
def test_empty_pages_show_one_card_and_loading_is_grey_blocks():
    assert 'class="ui-empty" role="status"' in _fn(CORE, "uiEmpty") and 'class="ui-empty-b"' in _fn(CORE, "uiEmpty")
    assert 'aria-busy="true"' in _fn(CORE, "uiSkel") and "ui-skel-row" in _fn(CORE, "uiSkel")
    for rel, needle in (("js/11-feedback.js", "uiEmpty({ icon: 'msg'"), ("js/15-orderhub.js", "uiEmpty({ icon: 'doc', t: !d.branches.length"),
                        ("js/15-orderhub.js", "uiEmpty({ icon: 'check', t: 'ไม่มีใบสั่งซื้อในช่วงนี้'"), ("js/15-orderhub.js", "uiManualGo('mt-ss-issues')"),
                        ("js/13-bom-center.js", "ยังไม่มีวัตถุดิบในทะเบียน"), ("js/14-branch.js", "uiManualGo('mt-brn-issue')"),
                        ("js/14-branch.js", "ยังไม่มีของที่ต้องรับ"), ("js/15-orderhub.js", "list.innerHTML = uiSkel(4)"),
                        ("js/13-bom-center.js", "uiSkel(4)")):
        assert needle in _src(rel), (rel, needle)
    assert "_fbDataShown(!!d.total)" in _src("js/11-feedback.js")                                   # the empty period hides every data section
    assert "card.style.display = rows.length ? '' : 'none'" in _fn(_src("js/14-branch.js"), "_brnHist")   # an empty history card is hidden
    # the card list keeps a page's own empty card; a loading line on a phone is a grey bar
    assert "emptyHtml = own.outerHTML" in _fn(MOB, "_phCardSpec") and "if (e.html && !stEmpty && !filter) list = e.html;" in _fn(MOB, "phCardList")
    assert "ui-skel-line" in _fn(CORE, "phTextFloor") and "_PH_BUSY_RE" in _fn(CORE, "phTextFloor")
    assert re.search(r"body\.ph-on \.ui-skel-line \{[^}]*color: transparent", HTML) and re.search(r"\.ui-skel-row, \.ui-skel-kpi \{[^}]*skeletonPulse", HTML)
    assert "uiSkel(4, 'kpi')" in _fn(MOB, "_phCentreHome")


# ── item 8 · one filter row, ⋯ ────────────────────────────────────────────────────────────────────────────────────────────────────
def test_filters_are_one_row_and_downloads_sit_under_the_more_menu():
    oh, bmc, shm = _src("tabs/54-orderhub.html"), _src("tabs/52-bomcenter.html"), _src("tabs/55-supplyhome.html")
    for html, more, ids in ((oh, 'id="ohbIsMore"', ('id="ohbIsBranch"', 'id="ohbIsFrom"', 'id="ohbIsTo"', 'onclick="ohbIsXlsx()"', 'onclick="ohbIsLogXlsx()"')),
                            (bmc, 'id="bmcDcMore"', ('id="bmcDcMonth"', 'id="bmcDcQ"', 'onclick="bmcDcTemplate()"', 'onclick="bmcDcExport()"'))):
        assert more in html and 'class="ui-more-btn"' in html and 'class="ui-fbar' in html, more
        for i in ids:                                                                                  # every control kept (ids, handlers)
            assert i in html, i
        pop = html[html.index(more):]
        assert pop.index("ui-more-pop") < pop.index(ids[-1])                                        # the downloads are inside the menu
    assert "ui-fbar" in shm and "ui-ph-hide" in shm and 'id="shmMonth"' in shm and 'id="shmBranch"' in shm
    assert "uiCompactFilters(panel);" in _fn(MOB, "phCardsApply") and "UI_MORE_BTN" in _fn(CORE, "uiCompactFilters")
    assert "row.prepend(lab)" in _fn(_src("js/15-orderhub.js"), "_ohbMonthSync")                   # the month joins the row on a phone
    assert re.search(r"body\.ph-on \.ui-fbar \{[^}]*flex-wrap: nowrap", HTML) and re.search(r"body\.ph-on \.ui-more-pop \{[^}]*display: none", HTML)
    assert re.search(r"\.ui-more-pop \{ display: contents; \}", HTML)                              # a desktop keeps them inline
    assert re.search(r"body\.ph-on \.tskf-seg \{[^}]*flex-wrap: nowrap", HTML) and re.search(r"body\.ph-on \.tskf-btn[^{]*\{[^}]*min-height: 44px", HTML)


# ── item 11 · charts ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_phone_chart_is_at_most_220_px_and_tiny_data_is_a_number():
    assert "const PH_CHART_MAX = 220;" in CORE
    tune = _fn(CORE, "_phoneChartTune")
    assert "'320px'" not in tune and "Math.min(MAX" in tune and "o.maintainAspectRatio = false" in tune
    mk = _fn(CORE, "mkChart")
    assert mk.index("_phChartSwap(id, config, ctx)") < mk.index("_phoneChartTune(config, ctx)") and "delete chartInstances[id]; return null;" in mk
    sw = _fn(CORE, "_phChartSwap")
    assert "n > 0 && n < 3" in sw and "['doughnut', 'pie', 'polarArea']" in sw and "type === 'scatter' || type === 'bubble'" in sw and "n > 10" in sw
    assert "config.phKeep" in sw and "#mgtRptBody" in sw
    assert "const PH_CHART_RANK = 5;" in CORE and "ดูทั้งหมด (" in _fn(CORE, "_phRankHtml")
    assert "uiPhoneFold(pk && pk.closest('.grid')" in _src("js/07-home.js")                          # one chart per question on สรุปประจำวัน


@needs_node
def test_the_ranking_shows_five_rows_then_the_rest_behind_a_button():
    pre = ESC + "const fmtN = n => String(n);\nconst PH_CHART_RANK = 5;\n" + _fn(CORE, "_phRankHtml")
    h = _run(pre, "_phRankHtml('หมวด', Array.from({ length: 8 }, (_, i) => ({ l: 'r' + i, v: (i + 1) * 10, c: '#000' })), v => String(v))")
    assert h.count("<li") == 8 and h.count('class="ph-rank-more" hidden') == 3 and "ดูทั้งหมด (8)" in h
    assert h.index("r7") < h.index("r0")                                                             # the largest first


def test_the_docs_carry_the_five_rules():
    plan = _src("docs/PHONE_PLAN.md")
    assert "## 10. Phone review set 2" in plan and "uiRow2" in plan and "PH_CHART_MAX" in plan and "uiCompactFilters" in plan
    man = _src("tabs/47-manual.html")
    assert "โทรศัพท์อ่านง่ายขึ้น" in man and "ใต้ปุ่ม <b>⋯</b>" in man and "หน้าอื่น" in man
    assert (ROOT / "docs" / "UI_REVIEW_MOBILE_2026-10-31.md").exists()
