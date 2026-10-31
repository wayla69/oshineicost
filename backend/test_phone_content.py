"""PR-MB (owner 2026-10-24): phone content for every page - card lists for the daily tables, help behind "?", clean headers and charts.

  * แตะง่าย (touch): a page's daily table is one card per row (phCardList, js/09c-mobile.js "CARD LISTS"): the status / class pill (colour =
    meaning), the name, a grey line, the three key figures on one line, an optional sparkline (msColumns, B1), a chevron to the MA row sheet;
    sort chips (the key figures + the name) and filter chips (the pill's values) on top; finished rows behind the shared state filter. พอดีจอ
    keeps the table. The tables and their key columns are registered in PAGE_CARDS (js/01-core.js), next to PAGE_PHONE.
  * row-number columns (#, ลำดับ, อันดับ) are hidden on a phone in both modes;
  * charts: a touch category axis names its first / middle / last label; a chart in the fit frame is drawn at (drawn size x device pixel ratio);
  * the stylesheet's braces balance (a stray "}" after the phone-shell block made browsers drop the "PRINT — RE-ASSERTED LAST" block).

Static and node checks here; the browser checks are tests/e2e/phone-cards.spec.js.
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


MOB, CORE, HTML = _src("js/09c-mobile.js"), _src("js/01-core.js"), _src("index.html")
CARDS = MOB[MOB.index("CARD LISTS (PR-MB"):MOB.index("// ── หน้าที่เปิดล่าสุด (ฉัน)")]
REG = CORE[CORE.index("const _SUPPLY_CENTRE"):CORE.index("// ── end page registry")]


def _run(js, expr):
    """node over a file (a command line has a length limit on Windows)"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js + f"\nconsole.log(JSON.stringify({expr}));")
    try:
        return json.loads(subprocess.run([NODE, f.name], capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    finally:
        Path(f.name).unlink()


# ── 1. the stylesheet: every brace closes what it opened ─────────────────────────────────────────────────────────────────────────────
def _brace_faults(css):
    css = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), css, flags=re.S)                       # comments
    css = re.sub(r"\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'", lambda m: " " * len(m.group(0)), css)              # strings
    depth, out = 0, []
    for i, c in enumerate(css):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth < 0:
                out.append(("stray }", css.count("\n", 0, i) + 1))
                depth = 0
    if depth:
        out.append(("unclosed {", depth))
    return out


def _styles(rel):
    src = _src(rel)
    return [(src.count("\n", 0, m.start(1)), m.group(1)) for m in re.finditer(r"<style[^>]*>(.*?)</style>", src, re.S)]


def test_the_brace_checker_sees_a_stray_and_an_unclosed_brace():
    assert _brace_faults("a { b: c; } } d { e: f; }") == [("stray }", 1)]
    assert _brace_faults("@media x { a { b: c; }") == [("unclosed {", 1)]
    assert _brace_faults("a { content: '}'; } /* } */ @media p { b { c: d; } }") == []


def test_every_style_block_and_stylesheet_balances_its_braces():
    """A stray "}" at the top level swallows the next rule: browsers dropped the whole "PRINT — RE-ASSERTED LAST" block behind one."""
    faults = []
    for rel in ["index.html"] + sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "tabs").glob("*.html")):
        for base, css in _styles(rel):
            faults += [(rel, k, v + base if k == "stray }" else v) for k, v in _brace_faults(css)]
    for p in sorted((ROOT / "docs" / "ui-kit").glob("*.css")):
        faults += [(p.name, k, v) for k, v in _brace_faults(p.read_text(encoding="utf-8"))]
    assert not faults, faults
    css = "".join(c for _, c in _styles("index.html"))
    i = css.index("PRINT — RE-ASSERTED LAST")
    assert re.search(r"@media print\s*\{", css[i:]), "the re-asserted print block is there"
    assert not _brace_faults(css[:i]), "nothing before the print block leaves a brace open or closes one too many"


# ── 2. the registry: every daily table of the MB list has its key columns ─────────────────────────────────────────────────────────────
MB_PAGES = {   # the brief's list (Thai name -> page key)
    "วิเคราะห์เมนู": "menu", "ปรับเมนู": "menuactionboard", "โอกาสเมนู": "menuopportunities", "กำไร SKU": "profit", "เทียบสาขา": "benchmark",
    "GP รายสาขา": "gpbranch", "P&L รายสาขา": "pnlbranch", "ยอดขายรายวัน": "dailysales", "ส่วนลด": "discounts", "พนักงาน": "staff",
    "บิลยกเลิก": "cancelbill", "เงินสดขาด/เกิน": "cashctl", "ต้นทุนต่อหัว": "costhead", "ความครอบคลุมต้นทุน": "coverage",
    "เป้าลดต้นทุน": "menuefficiency", "คิวใบสั่งซื้อ": "orderhub|queue", "เคลมสินค้า": "orderhub|claims", "ใบตัดเบิกสาขา": "orderhub|issues",
    "ผลต่างตัดเบิกรายวัน": "orderhub|issuevar", "ตรวจสอบการสั่ง": "orderhub|comp", "การใช้งานทุกสาขา": "orderhub|adoption",
    "ใช้วัตถุดิบต่อยอดขาย": "orderhub|persales", "ยอดแนะนำรายรอบ": "bomcenter|fc", "ราคารายเดือน": "bomcenter|price",
    "ต้นทุนต่อจาน": "bomcenter|dishcost", "ทะเบียนวัตถุดิบ": "bomcenter|ing", "ใบสรุปตัดเบิก": "bomcenter|issue", "เสียงลูกค้า": "feedback",
    "ข้อความจากเจ้าของสาขา": "fmsg", "High Value Product Monitor": "hvmonitor", "วิเคราะห์ระดับราคา": "pricetier",
}
# pages whose daily list is not a <table>: it is already one card per row, drawn by the page itself, with the shared state filter
OWN_CARDS = {
    "fmsg": "one row per conversation (button.fm-row, js/19-fmsg.js) with the shared state filter: no table",
}
# a table with fewer than three numeric columns shows what it has
FEWER = {("orderhub|queue", 0): 2, ("orderhub|claims", 0): 1, ("bomcenter|issue", 0): 2, ("feedback", 0): 2}


@needs_node
def test_every_page_of_the_list_has_its_tables_and_key_columns_registered():
    cards = _run(REG, "PAGE_CARDS")
    phone = _run(REG, "PAGE_PHONE")
    for name, key in MB_PAGES.items():
        if key in OWN_CARDS:
            assert key not in cards and len(OWN_CARDS[key]) > 20, key
            continue
        assert key in cards and cards[key], f"{name} ({key}) has no card list"
        assert key in phone or key.split("|")[0] in phone, key                         # a page of the phone registry
        for k, c in enumerate(cards[key]):
            assert c.get("sel"), (key, k)
            n = FEWER.get((key, k), 3)
            assert 1 <= len(c["keyCols"]) <= 3 and (len(c["keyCols"]) == n or (key, k) not in FEWER and k > 0), (key, k, c["keyCols"])
            assert not c.get("labels") or len(c["labels"]) == len(c["keyCols"]), (key, k)
    assert set(cards) <= set(MB_PAGES.values()), set(cards) - set(MB_PAGES.values())


@needs_node
def test_the_key_columns_are_headers_the_pages_print():
    """A key column is named as the table prints its header: the text is in the page's fragment or slices (a typo would show '—')."""
    cards = _run(REG, "PAGE_CARDS")
    src = "\n".join(_src(p.relative_to(ROOT).as_posix()) for p in sorted((ROOT / "tabs").glob("*.html")))
    src += "\n".join(_src(p.relative_to(ROOT).as_posix()) for p in sorted((ROOT / "js").glob("*.js")) if p.name != "01-core.js")
    src += CORE[:CORE.index("const PAGE_CARDS = {")] + CORE[CORE.index("// Pages that sit in no cluster or chip"):]      # not the registry itself
    for key, lst in cards.items():
        for c in lst:
            refs = list(c["keyCols"]) + [c.get("title")] + list(c.get("sub") or []) + [c.get("pill")]
            for r in refs:
                if r is None or isinstance(r, int):
                    continue
                alts = r if isinstance(r, list) else [r]
                assert any(a in src for a in alts), (key, r)


# ── 3. the renderer ───────────────────────────────────────────────────────────────────────────────────────────────────────────
PRE = ("const esc = s => String(s ?? '').replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));\n"
       + re.search(r"const _PH_CL_PAGE = [^\n]*\n", CARDS).group(0) + re.search(r"const _PH_CL_TONE = [^\n]*\n", CARDS).group(0)
       # phone review item 3 (2026-10-31): the pill is a chip of <= 6 characters (uiChipShort, js/01-core.js)
       + CORE[CORE.index("const _UI_CHIP_SHORT = ["):CORE.index("// the chip: colour = meaning")]
       + _fn(MOB, "phCardList"))
ROWS = [
    {"i": 0, "title": "ขอนแก่น", "sub": "KK01", "figs": [{"l": "ยอดขาย", "v": "฿5.0M", "n": 5e6}, {"l": "GP %", "v": "28%", "n": 28}, {"l": "ลูกค้า", "v": "7,598", "n": 7598}],
     "pill": {"t": "ใกล้ขอบ", "k": "ใกล้ขอบ", "tone": "warn"}, "done": False},
    {"i": 1, "title": "ชลบุรี", "sub": "CB01", "figs": [{"l": "ยอดขาย", "v": "฿9.1M", "n": 9.1e6}, {"l": "GP %", "v": "41%", "n": 41}, {"l": "ลูกค้า", "v": "9,001", "n": 9001}],
     "pill": {"t": "ในเป้า", "k": "ในเป้า", "tone": "good"}, "done": True},
    {"i": 2, "title": "ระยอง", "sub": "RY01", "figs": [{"l": "ยอดขาย", "v": "฿1.2M", "n": 1.2e6}, {"l": "GP %", "v": "12%", "n": 12}, {"l": "ลูกค้า", "v": "1,500", "n": 1500}],
     "pill": {"t": "นอกเป้า", "k": "นอกเป้า", "tone": "bad"}, "done": False},
    {"i": 3, "title": "รวมทุกสาขา", "figs": [{"l": "ยอดขาย", "v": "฿15.3M", "n": 1.53e7}], "total": True},
]


def _list(spec):
    return _run(PRE, f"phCardList({json.dumps(spec, ensure_ascii=False)})")


@needs_node
def test_a_card_is_two_fixed_lines_the_name_and_one_figure_then_the_chip_and_one_grey_line():
    # phone review item 3 (owner 2026-10-31): line 1 = the name + the ONE key figure; line 2 = the chip + the grey line (the code, then the
    # other figures as "label value"); every column is behind the tap (the row sheet). It was pill / name / grey line / three figures.
    h = _list({"id": "t", "rows": ROWS, "keys": ["ยอดขาย", "GP %", "ลูกค้า"]})
    card = re.search(r'<li class="ph-cl-card">(.*?)</li>', h, re.S).group(1)
    assert ('<span class="ph-cl-l1"><span class="ph-cl-t">ขอนแก่น</span><span class="ph-cl-f" data-l="ยอดขาย"><span class="ph-cl-fv">฿5.0M</span></span></span>'
            in card)                                                                                  # the first figure is the key one
    assert card.count('class="ph-cl-f"') == 1 and card.index("ph-cl-l1") < card.index("ph-cl-l2")
    l2 = card[card.index('<span class="ph-cl-l2">'):]
    assert '<span class="ph-cl-pill" data-tone="watch">ใกล้ขอบ</span>' in l2                      # colour = meaning (the theme's watch ink)
    assert '<span class="ph-cl-s">KK01 · GP % 28% · ลูกค้า 7,598</span>' in l2
    assert 'onclick="phCardOpen(\'t\',0)"' in card and 'class="ph-cl-chev"' in card and "ดูทุกคอลัมน์" in card
    assert 'aria-label="ขอนแก่น · ยอดขาย ฿5.0M · ดูทุกคอลัมน์"' in card
    # sorted by a figure: that figure is the one on line 1
    s = _list({"id": "t", "rows": ROWS, "keys": ["ยอดขาย", "GP %", "ลูกค้า"], "sort": {"k": 1, "dir": -1}})
    assert re.search(r'<span class="ph-cl-t">ชลบุรี</span><span class="ph-cl-f" data-l="GP %"><span class="ph-cl-fv">41%</span>', s)
    # a long status keeps <= 6 characters on the chip, its words in the title
    long = [dict(ROWS[0], pill={"t": "ไม่มีในตาราง (ใช้ต้นทุนเดิม)", "k": "ใช้เดิม", "tone": "warn"})]
    assert '<span class="ph-cl-pill" data-tone="watch" title="ไม่มีในตาราง (ใช้ต้นทุนเดิม)">ใช้เดิม</span>' in _list({"id": "t", "rows": long, "keys": ["ยอดขาย"]})
    assert '<li class="ph-cl-card ph-cl-tot">' in h and "รวมทุกสาขา" in h                         # a total row stays first, as a summary card
    assert h.index("รวมทุกสาขา") < h.index("ขอนแก่น")


@needs_node
def test_sort_chips_are_the_name_and_the_key_figures_and_sort_both_ways():
    h = _list({"id": "t", "rows": ROWS, "keys": ["ยอดขาย", "GP %", "ลูกค้า"]})
    chips = re.findall(r'<button type="button" class="ph-cl-chip" aria-pressed="(true|false)" onclick="phCardSort\(\'t\',([^)]*)\)">([^<]*)', h)
    assert [c[2] for c in chips] == ["ชื่อ", "ยอดขาย", "GP %", "ลูกค้า"] and all(c[0] == "false" for c in chips)
    order = lambda html: [t for t in re.findall(r'<span class="ph-cl-t">([^<]*)</span>', html) if t != "รวมทุกสาขา"]
    assert order(h) == ["ขอนแก่น", "ชลบุรี", "ระยอง"]                                               # no chip pressed: the table's own order
    down = _list({"id": "t", "rows": ROWS, "keys": ["ยอดขาย", "GP %", "ลูกค้า"], "sort": {"k": 0, "dir": -1}})
    assert order(down) == ["ชลบุรี", "ขอนแก่น", "ระยอง"] and "ยอดขาย ↓" in down and 'aria-pressed="true" onclick="phCardSort(\'t\',0)"' in down
    up = _list({"id": "t", "rows": ROWS, "keys": ["ยอดขาย", "GP %", "ลูกค้า"], "sort": {"k": 1, "dir": 1}})
    assert order(up) == ["ระยอง", "ขอนแก่น", "ชลบุรี"] and "GP % ↑" in up
    name = _list({"id": "t", "rows": ROWS, "keys": ["ยอดขาย"], "sort": {"k": "name", "dir": 1}})
    assert order(name) == sorted(order(name), key=lambda s: s)  # ก-ฮ (localeCompare 'th' agrees with code points for these names)


@needs_node
def test_filter_chips_are_the_pill_values_with_counts():
    h = _list({"id": "t", "rows": ROWS, "keys": ["ยอดขาย"]})
    f = re.findall(r'onclick="phCardFilter\(\'t\',([^)]*)\)">([^<]*) <span class="ph-cl-n">(\d+)</span>', h)
    assert [(x[1], x[2]) for x in f] == [("ทั้งหมด", "3"), ("ใกล้ขอบ", "1"), ("ในเป้า", "1"), ("นอกเป้า", "1")]
    one = _list({"id": "t", "rows": ROWS, "keys": ["ยอดขาย"], "filter": "นอกเป้า"})
    assert re.findall(r'<span class="ph-cl-t">([^<]*)</span>', one) == ["รวมทุกสาขา", "ระยอง"]


@needs_node
def test_finished_rows_are_hidden_by_default_behind_the_shared_state_filter():
    base = {"id": "t", "rows": ROWS, "keys": ["ยอดขาย"], "hasDone": True}
    todo = _list(dict(base, state="todo"))
    assert "ชลบุรี" not in todo and "ขอนแก่น" in todo and 'data-cl-st="t"' in todo                 # the place of uiStateFilter.bar
    assert "ชลบุรี" in _list(dict(base, state="done")) and "ขอนแก่น" not in _list(dict(base, state="done"))
    assert all(t in _list(dict(base, state="all")) for t in ("ชลบุรี", "ขอนแก่น", "ระยอง"))
    assert 'data-cl-st' not in _list({"id": "t", "rows": ROWS, "keys": ["ยอดขาย"]})                 # a table with no finished rows: no state chips
    # the apply step fills the bar with the shared control (uiStateFilter.bar, its counts and its pick)
    draw = _fn(MOB, "_phClDraw")
    assert "uiStateFilter.bar(sb, { f: uiStateFilter.get(), counts: { todo: body.length - done, done, all: body.length }, pick: 'phCardState' })" in draw
    assert "uiStateFilter.set(f)" in _fn(MOB, "phCardState")


@needs_node
def test_an_empty_list_says_so_in_one_line_with_the_action():
    e = _list({"id": "t", "rows": [], "keys": ["ยอดขาย"], "empty": {"t": "ไม่มีข้อมูลในเดือนนี้"}})
    assert re.search(r'<div class="ph-cl-empty" role="status"><span>ไม่มีข้อมูลในเดือนนี้</span><button type="button" class="ph-cl-act" onclick="phCardEmptyAct\(\'t\'\)">เลือกช่วงอื่น</button></div>', e)
    assert "ph-cl-card" not in e and "ph-cl-chip" not in e
    d = _list({"id": "t", "rows": [r for r in ROWS if r.get("done")], "keys": ["ยอดขาย"], "hasDone": True, "state": "todo"})
    assert "ไม่มีรายการค้าง" in d and "phCardState('all')" in d and "ดูทั้งหมด" in d


@needs_node
def test_a_long_list_draws_fifty_cards_then_more():
    rows = [{"i": i, "title": f"r{i:03d}", "figs": [{"l": "x", "v": str(i), "n": i}]} for i in range(120)]
    h = _list({"id": "t", "rows": rows, "keys": ["x"]})
    assert h.count('class="ph-cl-card"') == 50 and "แสดงอีก 50 จาก 70 รายการ" in h
    assert _list({"id": "t", "rows": rows, "keys": ["x"], "limit": 100}).count('class="ph-cl-card"') == 100


@needs_node
def test_a_ticked_row_keeps_its_tick_and_a_group_row_its_place():
    rows = [{"i": 0, "title": "สาขา ก", "figs": [], "grp": True},
            {"i": 1, "title": "สมชาย", "group": "สาขา ก", "figs": [{"l": "x", "v": "1", "n": 1}], "tick": {"on": True, "l": "เลือกใบสั่งซื้อ 1", "dis": False}}]
    h = _list({"id": "t", "rows": rows, "keys": ["x"]})
    assert '<label class="ph-cl-tick"><input type="checkbox" aria-label="เลือกใบสั่งซื้อ 1" checked onchange="phCardTick(\'t\',1,this.checked)"></label>' in h
    assert h.index("สาขา ก") < h.index("สมชาย") and "ph-cl-tot" in h                                  # the group's row above its members
    s = _list({"id": "t", "rows": rows, "keys": ["x"], "sort": {"k": 0, "dir": -1}})
    assert '<span class="ph-cl-t">สาขา ก</span>' not in s and '<span class="ph-cl-s">สาขา ก</span>' in s   # sorted: the group names each card


# ── 4. reading a table ───────────────────────────────────────────────────────────────────────────────────────────────────────────
@needs_node
def test_numbers_dates_columns_and_pill_groups_are_read_as_the_table_prints_them():
    pre = (re.search(r"const _phNorm = [^\n]*\n", CARDS).group(0) + re.search(r"const _phPillKey = [^\n]*\n", CARDS).group(0)
           + _fn(MOB, "_phNum") + "\n" + _fn(MOB, "_phDateTh") + "\n" + _fn(MOB, "_phColIdx"))
    r = _run(pre, """[['฿1,234', '12.5%', '−3', '(1,200)', '1.2K', '3.40M', '2 ล้าน', '▼ 3.2%', '▲ 4%', '—', '12 KG', '฿-50'].map(_phNum),
        _phDateTh('ตัดรอบ 2020-10-19 · 2026-01-05'.replace('2026', String(new Date().getFullYear()))),
        [_phColIdx(['#', 'เมนู', 'GP ()', 'GP %', 'ยอดขาย ↕'], 'GP %'), _phColIdx(['#', 'เมนู', 'GP ()', 'GP %'], 'GP'), _phColIdx(['a', 'ยอดขาย ↕'], 'ยอดขาย'),
         _phColIdx(['a', 'b'], 'zzz'), _phColIdx(['a', 'b', 'c'], -1)],
        ['สาขาแก้ไข 2 รายการ', 'คีย์บัญชีแล้ว 17/10/2026', 'คลังจัดส่งแล้ว · PO-6910-0028', 'A', '● แดง'].map(_phPillKey)]""")
    assert r[0] == [1234, 12.5, -3, -1200, 1200, 3400000, 2000000, -3.2, 4, None, 12, -50]
    assert r[1] == "ตัดรอบ 19/10/20 · 5/1"
    assert r[2] == [3, 2, 1, -1, 2]
    assert r[3] == ["สาขาแก้ไข", "คีย์บัญชีแล้ว", "คลังจัดส่งแล้ว", "A", "● แดง"]


def test_the_cards_follow_the_mode_and_keep_the_table_in_the_page():
    apply_ = _fn(MOB, "phCardsApply")
    assert "_phClOn()" in apply_ and "_phRowNums(panel)" in apply_
    assert "!document.body.classList.contains('ph-fit')" in _fn(MOB, "_phClOn")                      # พอดีจอ keeps the table
    assert "phCardsSchedule();" in _fn(MOB, "phFitApply")                                            # every mode change and page switch
    assert "box.classList.add('ph-cl-src')" in _fn(MOB, "_phClDraw")                                 # hidden, not removed: the sheet reads its row
    assert "phRowSheet(r.tr, r.title)" in _fn(MOB, "phCardOpen")
    assert "cb.dispatchEvent(new Event('change', { bubbles: true }))" in _fn(MOB, "phCardTick")       # the row's own handler runs
    assert "cfg.fields === 'table' && edits(t)" in apply_                                            # a writer's editable table stays the table
    css = HTML[HTML.index("PR-MB · card lists"):]
    assert ".ph-cl { display: none; }" in css and "body.ph-on:not(.ph-fit) .ph-cl { display: block;" in css
    assert "body.ph-on:not(.ph-fit) .ph-cl-src { display: none !important; }" in css
    for tone, tok in (("good", "good"), ("fix", "fix"), ("watch", "watch"), ("neu", "neu")):
        assert f'.ph-cl-pill[data-tone="{tone}"] {{ background: var(--{tok}-t); color: var(--{tok}-i); }}' in css, tone
    assert re.search(r"\.ph-cl-chip \{[^}]*min-height: 44px", css) and re.search(r"\.ph-cl-hit \{[^}]*min-height: 44px", css)
    for sel in (".ph-cl-t", ".ph-cl-fv"):
        assert re.search(re.escape(sel) + r" \{[^}]*font-size: 15px", css), sel
    for sel in (".ph-cl-t", ".ph-cl-s", ".ph-cl-pill"):                                             # item 3: one line each, never wrapping
        assert re.search(re.escape(sel) + r" \{[^}]*white-space: nowrap", css), sel
    for sel in (".ph-cl-fl", ".ph-cl-s"):                                                           # the small text is >= 12 px, a theme ink
        m = re.search(re.escape(sel) + r" \{([^}]*)\}", css).group(1)
        assert float(re.search(r"font-size: ([\d.]+)px", m).group(1)) >= 12 and "var(--text-secondary)" in m, sel


def test_the_queue_and_the_adoption_list_draw_their_table_for_the_shared_cards():
    oh = _src("js/15-orderhub.js")
    assert "function _ohbSharedCards() { return document.body.classList.contains('ph-on') && !document.body.classList.contains('supply-phone'); }" in oh
    assert oh.count("const phone = _ohbPhone() && !_ohbSharedCards();") == 2                        # the branch login keeps its own cards


# ── 5. row numbers, the sheet ──────────────────────────────────────────────────────────────────────────────────────────────────
@needs_node
def test_row_number_columns_are_hidden_on_a_phone_and_left_out_of_the_sheet():
    rn = re.search(r"const _PH_RN_RE = (.*?);", CARDS).group(1)
    r = _run(f"const re = {rn};", "['#', 'ลำดับ', 'อันดับ', 'No.', 'ที่', 'เมนู', '#เมนู', 'สาขา'].map(s => re.test(s))")
    assert r == [True, True, True, True, True, False, False, False]
    assert "body.ph-on .ph-rn { display: none !important; }" in HTML
    nums = _fn(MOB, "_phRowNums")
    assert "/^[\\d.\\s]*$/.test(c.textContent || '')" in nums                                          # a label such as "รวม" in that column stays
    assert "_phRowNums(panel);" in _fn(MOB, "phFitLayout")                                           # fit too, before its width is read
    sheet = _fn(MOB, "phRowSheet")
    assert "c.classList.contains('ph-rn') || _PH_RN_RE.test(_phNorm(heads[i]))" in sheet and "(title || (pairs.length ? pairs[0][1] : '') || 'รายการ')" in sheet
    # the sticky first column of a fit table moves to the column after a hidden row number
    assert "table :is(tbody, tfoot) :is(td, th).ph-rn + :is(td, th) { position: sticky; left: 0;" in HTML


# ── 6. charts ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@needs_node
def test_a_touch_category_axis_names_its_first_middle_and_last_label():
    tune = _fn(CORE, "_phoneChartTune")
    pre = "const __wrap = { style: { height: '' } };\n" + tune
    r = _run(pre, """(() => { const out = {};
        const mk = n => ({ type: 'bar', data: { labels: Array.from({ length: n }, (_, i) => 'd' + i), datasets: [{}] }, options: {} });
        [12, 5].forEach(n => { const c = mk(n); _phoneChartTune(c, { parentElement: __wrap }); const t = c.options.scales.x.ticks;
          const ctx = { getLabelForValue: v => 'd' + v }; const ticks = Array.from({ length: n }, (_, i) => ({ value: i }));
          out[n] = { auto: t.autoSkip, lim: t.maxTicksLimit, rot: t.maxRotation, labels: ticks.map((x, i) => t.callback.call(ctx, i, i, ticks)), font: t.font.size }; });
        const own = { type: 'bar', data: { labels: [], datasets: [{}] }, options: { scales: { x: { ticks: { maxTicksLimit: 9 } } } } };
        _phoneChartTune(own, { parentElement: __wrap }); out.own = own.options.scales.x.ticks;
        const kept = { type: 'bar', data: { labels: [], datasets: [{}] }, options: { scales: { x: { ticks: { callback: (v) => 'w' + v } } } } };
        _phoneChartTune(kept, { parentElement: __wrap }); const kt = kept.options.scales.x.ticks; const tk = Array.from({ length: 9 }, (_, i) => ({ value: i }));
        out.kept = tk.map((x, i) => kt.callback.call({ getLabelForValue: v => 'd' + v }, i, i, tk));
        return out; })()""")
    assert r["12"]["labels"] == ["d0", "", "", "", "", "d5", "", "", "", "", "", "d11"] and r["12"]["auto"] is False and r["12"]["rot"] == 0
    assert r["12"].get("lim") is None and r["12"]["font"] >= 12
    assert r["5"]["labels"] == ["d0", "d1", "d2", "d3", "d4"]                                         # six or fewer: every label
    assert r["own"] == {"maxTicksLimit": 9, "autoSkip": True, "font": {"size": 12}}                   # a chart's own choice wins
    assert r["kept"] == ["w0", "", "", "", "w4", "", "", "", "w8"]                                     # its own label format is kept


def test_a_chart_in_the_fit_frame_is_drawn_for_the_size_it_is_shown_at():
    crisp = _fn(MOB, "_phChartsCrisp")
    assert "const dpr = window.devicePixelRatio || 1" in crisp and "cv.closest('.ph-z100') ? 1 : scale" in crisp
    assert "ch.options.devicePixelRatio = want" in crisp and "ch.resize()" in crisp
    assert "_phChartsCrisp(panel, s);" in _fn(MOB, "phFitLayout")                                    # at rest: drawn x the frame's scale
    assert "_phChartsCrisp(el, 1);" in _fn(MOB, "phZoomToggle")                                      # a double tap: redrawn for 100 %
    assert "_phChartsCrisp(panel, 0);" in _fn(MOB, "_phFitOff")                                      # touch: the browser's own ratio


# ── 7. docs ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_phone_plan_has_the_card_rule_and_the_key_columns_of_every_page():
    plan = _src("docs/PHONE_PLAN.md")
    assert "## 7. Card lists" in plan and "PAGE_CARDS" in plan and "keyCols" in plan
    for name, key in MB_PAGES.items():
        assert f"| {name} |" in plan, name
    man = _src("tabs/47-manual.html")
    assert "ปุ่มเรียง" in man and "ปุ่มกรอง" in man
