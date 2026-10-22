"""PR-U3 (owner 2026-10-22) - polish of the supply module and ร้านของฉัน.

The promises:
  1. "ต้องนับ" on a ใบตัดเบิก row only where it is the reason the row is there (required, not issued or used lately, no stock, nothing typed);
  2. "ดูที่มา" opens something useful in plain words (why the item is on the slip, the unit ladder, the last count, the full name) with the
     code only beside them - a row with nothing more to say has no link, never the bare code;
  3. a unit that carries its contents is written one way on the box, the stock figure and the ladder: "แพ็ค 10 กก." -> "แพ็ค (10 กก.)";
  4. no English left on the top bar search and the error dialog;
  5. the page search also finds a BRANCH (picked: the branch filter), everything it draws is escaped (the old top-bar box escapes the text
     it echoes too); the number keys never switch tabs from a field, the phone or a supply / ร้านของฉัน work page;
  6. the receive confirm never says "· 0 รายการ" - every line short reads "รับไม่ครบทุกรายการ · แจ้งเคลม N รายการ";
  7. the manual receipt form sits under the receipts, folded (<details> "บันทึกสินค้าที่รับเอง"); the order receipt has its own status line;
  8. two value labels of the phone columns never overlap ("เดือนนี้" on ร้านของฉัน's 12 months);
  9. ร้านของฉัน writes a month one way, as its header and Excel file: "ต.ค. 2026", never "ต.ค. 69";
 10. the supply month controls start at the module's first month (GET …/first-month), not 18 months back.
"""
import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

import auth

REPO = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _src(rel):
    return (REPO / rel).read_text(encoding="utf-8")


def _fn(src, name):
    """A function of a js slice: a multi-line one up to its closing brace at column 0, a one-line one as its line."""
    m = re.search(r"^((?:async )?function %s\(.*\{.*\}[ \t]*(?://[^\n]*)?)$" % re.escape(name), src, re.M)
    if m:
        return m.group(1) + "\n"
    return re.search(r"((?:async )?function %s\([\s\S]*?\n})" % re.escape(name), src).group(1) + "\n"


def _const(src, name):
    return re.search(r"(const %s = [^\n]*)" % re.escape(name), src).group(1)


def _node(code):
    return json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", check=True).stdout)


BRN = _src("js/14-branch.js")
ESC = "const esc = s => String(s ?? '').replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));\n"
NUM = "function _bmcNum(v, d = 2) { return Number(v).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: d }); }\n"


# ── 1 · the "ต้องนับ" chip ──────────────────────────────────────────────────────────────────────────────────────────
@needs_node
def test_the_count_chip_shows_only_where_it_is_the_reason_the_row_is_on_the_slip():
    pre = "let _brnIssQty = {T: {used: '2', waste: ''}};\n" + _fn(BRN, "_brnIssAny") + "\n" + _fn(BRN, "_brnIssCountChip")
    got = _node(pre + """
console.log(JSON.stringify([
  _brnIssCountChip({code: 'A', required: true}),                                  // required and nothing else: the chip says why it is here
  _brnIssCountChip({code: 'B', required: true, recent: true}),                    // issued lately: it is here anyway
  _brnIssCountChip({code: 'C', required: true, used_recent: true}),               // the recipes used it
  _brnIssCountChip({code: 'D', required: true, system_qty: 4}),                   // in stock
  _brnIssCountChip({code: 'T', required: true}),                                  // typed on this slip
  _brnIssCountChip({code: 'E', required: false})]));""")
    assert got == [True, False, False, False, False, False]
    row = BRN[BRN.index("function brnIssRender()"):BRN.index("// เบิกเพิ่ม: the quantity to add is always a whole number")]
    assert "it.required ? _bmcChip('ต้องนับ'" not in row and "_brnIssCountChip(it) ? _bmcChip('ต้องนับ'" in row
    # the zone's ✓ still reads the required items
    assert "i.required" in _fn(BRN, "_brnIssZoneDone")


@needs_node
def test_why_an_item_is_on_the_slip_in_plain_words():
    stk = "let BC_MODE = 'full'; function stkWord(f, s) { return BC_MODE === 'stock' ? s : f; }\n"      # PR-S4: the stock-first words (js/01-core.js)
    got = _node(stk + _fn(BRN, "_brnIssWhy") + """
console.log(JSON.stringify([_brnIssWhy({recent: true, used_recent: true, system_qty: 3, required: true, monitored: true}),
  _brnIssWhy({used_recent: true, required: true}), _brnIssWhy({system_qty: 0}), _brnIssWhy({system_qty: -2, required: true})]));""")
    assert got == [["เบิกใน 14 วันที่ผ่านมา", "มีของในระบบ", "รายการบังคับสั่ง ต้องนับทุกเดือน"],
                   ["สูตรอาหารใช้ใน 14 วันที่ผ่านมา", "กลุ่มมูลค่าสูง ต้องนับทุกเดือน"], [], ["กลุ่มมูลค่าสูง ต้องนับทุกเดือน"]]
    stock_js = stk + "BC_MODE = 'stock';\n" + _fn(BRN, "_brnIssWhy") + "\nconsole.log(JSON.stringify(_brnIssWhy({used_recent: true})));"
    assert _node(stock_js) == ["ใช้ใน 14 วันที่ผ่านมา"]                                  # PR-S4: stock mode does not say "recipe"


# ── 2 · ดูที่มา ──────────────────────────────────────────────────────────────────────────────────────────────────────
@needs_node
def test_the_source_link_is_never_the_bare_code():
    pre = ESC + _fn(BRN, "_brnSrc") + "\n" + _fn(BRN, "_brnLine")
    got = _node(pre + """
const hid = h => (h.match(/<span class="brn-srcv" hidden>([\\s\\S]*)<\\/span>$/) || [])[1] || '';
console.log(JSON.stringify([
  _brnSrc({code: '03-01-319', name: 'Brisket'}),                                           // nothing more to say: no link
  hid(_brnSrc({code: 'X1', name: 'ปูอัดซาชิมิ Kani', short_name: 'ปูอัด'})),             // the full name, then the code
  hid(_brnSrc({code: 'X2', name: 'a'}, ['เบิกใน 14 วันที่ผ่านมา', '', '1 แพ็ค (10 กก.) = 10,000 กรัม'])),
  hid(_brnSrc({code: '<b>', name: 'x'}, ['<img src=x>'])),                               // everything is escaped
  _brnLine('', 'ส่งจริง', null, 'x'), _brnLine(_brnSrc({code: 'Q', name: 'q'}), 'ต้องใช้ 2')]));""")
    assert got[0] == ""
    assert got[1] == " ปูอัดซาชิมิ Kani · รหัส X1"
    assert got[2] == " เบิกใน 14 วันที่ผ่านมา · 1 แพ็ค (10 กก.) = 10,000 กรัม · รหัส X2"
    assert got[3] == " &lt;img src=x&gt; · รหัส &lt;b&gt;"
    assert got[4] == "ส่งจริง · x" and got[5] == "ต้องใช้ 2"                                  # no stray " · " when there is no link
    # the slip row passes its facts: why, the ladder, the last count
    assert "_brnSrc(it, [_brnIssWhy(it).join(' · '), _brnLadderPlain(it)" in BRN and "นับล่าสุด" in BRN
    # no row writes "${_brnSrc(x)} · " any more (it would start with a separator when there is no link)
    assert not re.search(r"\$\{_brnSrc\([a-z]+\)\} · ", BRN)


# ── 3 · one way to write a unit with its contents ──────────────────────────────────────────────────────────────────
@needs_node
def test_a_unit_with_its_contents_reads_the_same_on_the_box_the_stock_and_the_ladder():
    pre = NUM + ESC + _fn(BRN, "_brnNice") + _fn(BRN, "_brnUnitW") + _fn(BRN, "_brnUnitFull") + _fn(BRN, "_brnSysTxt") + _fn(BRN, "_brnLadderPlain") \
        + "\n" + _fn(BRN, "_brnLadderTxt")
    got = _node(pre + """
console.log(JSON.stringify([_brnUnitFull('แพ็ค 10 กก.'), _brnUnitFull('กล่อง (10 แพ็ค)'), _brnUnitFull('แพ็ค'), _brnUnitFull(''), _brnUnitFull('ขวด 750 มล.'),
  _brnSysTxt(4, 'แพ็ค 10 กก.'), _brnSysTxt(0.5, 'แพ็ค 10 กก.'),
  _brnLadderPlain({count_unit: 'แพ็ค 10 กก.', base_unit: 'กรัม', per_count_unit: 10000}), _brnLadderPlain({count_unit: 'กรัม', base_unit: 'กรัม', per_count_unit: 1}),
  _brnLadderTxt({count_unit: 'แพ็ค <1>', base_unit: 'กรัม', per_count_unit: 2})]));""")
    assert got == ["แพ็ค (10 กก.)", "กล่อง (10 แพ็ค)", "แพ็ค", "", "ขวด (750 มล.)", "4 แพ็ค (10 กก.)", "น้อยกว่า 1 แพ็ค (10 กก.)",
                   "1 แพ็ค (10 กก.) = 10,000 กรัม", "", "1 แพ็ค &lt;1&gt; = 2 กรัม"]
    # the slip's box caption, the count row's unit and the order row's unit use it
    assert "<span class=\"brn-cap\">${label} · ${esc(_brnUnitFull(unit))}</span>" in BRN
    assert "esc(_brnUnitFull(it.count_unit || it.base_unit || ''))" in BRN
    assert "return _brnUnitFull(_brnOrdF(r) > 1" in _fn(BRN, "_brnOrdUnitShow")


# ── 4 · no English on the top bar and the error dialog ─────────────────────────────────────────────────────────────
def test_no_english_left_on_the_top_bar_search_and_the_error_dialog():
    idx, feat = _src("index.html"), _src("js/06-features.js")
    assert "Search branches / SKUs" not in idx and 'placeholder="ค้นหาสาขาหรือรหัสเมนู"' in idx
    assert "title.textContent = 'Error'" not in feat and "title.textContent = 'เกิดข้อผิดพลาด'" in feat
    for word in ("Could not open dive-in", "Could not render chart", "No results for", ">BRANCH<", "'<div class=\"search-group-title\">SKUs</div>'"):
        assert word not in feat, word


# ── 5 · the search finds a branch; everything is escaped; the number keys ─────────────────────────────────────────────
@needs_node
def test_the_page_search_finds_a_branch_and_escapes_it():
    lau = _src("js/18-launcher.js")
    pre = "const PAGE_KEYWORDS = {};\n" + _fn(lau, "pgNorm") + "\n" + _fn(lau, "_pgEntry") + "\n" + _fn(lau, "pgBranchHits")
    got = _node(pre + """
const names = ['อุดรธานี', 'อุดรธานี 2', 'ชลบุรี', 'บ้านอุดร', '<img src=x onerror=alert(1)>'];
console.log(JSON.stringify([pgBranchHits('อุดร', names).map(e => [e.label, e.branch, e.cluster, e.extra]), pgBranchHits('', names),
  pgBranchHits('อุ ดร ธา นี', names).map(e => e.label), pgBranchHits('img', names).map(e => e.branch), pgBranchHits('zz', names)]));""")
    assert got[0] == [["อุดรธานี", "อุดรธานี", "สาขา", True], ["อุดรธานี 2", "อุดรธานี 2", "สาขา", True], ["บ้านอุดร", "บ้านอุดร", "สาขา", True]]
    assert got[1] == [] and got[2] == ["อุดรธานี", "อุดรธานี 2"] and got[3] == ["<img src=x onerror=alert(1)>"] and got[4] == []
    # a row is drawn through pgRowInner, which escapes the label and the chip; picking a branch applies the filter
    row = _fn(lau, "pgRowInner")
    assert "esc(e.label)" in row and "esc(chip)" in row
    assert "if (e.branch) { pgApplyBranch(e.branch); return; }" in _fn(lau, "pgOpen")
    assert "pgBranchHits(text, opt && opt.branches ? opt.branches : pgBranchNames())" in _fn(lau, "pgSearchResults")
    assert "tabAllowed('home')" in _fn(lau, "pgBranchNames")                                  # a pinned login has no branch to pick
    assert "if (e.branch)" in _fn(_src("js/09c-mobile.js"), "phSearchGo")
    # the old top-bar box escapes the text it echoes
    rs = _fn(_src("js/06-features.js"), "renderSearchResults")
    assert "${esc(q)}" in rs and '"${q}"' not in rs


@needs_node
def test_the_number_keys_stay_off_fields_the_phone_and_work_pages():
    feat = _src("js/06-features.js")
    pre = "const _SUPPLY_PANE = {branchstock: {}, orderhub: {}, supplyhome: {}, bomcenter: {}, ordersetup: {}}; let _ph = false; function phOn() { return _ph; }\n" \
        + _fn(feat, "_digitJumpOk")
    got = _node(pre + """
const r = [_digitJumpOk('home', {tagName: 'BODY'}), _digitJumpOk('trend', {tagName: 'BUTTON'}), _digitJumpOk('home', {tagName: 'INPUT'}),
  _digitJumpOk('home', {tagName: 'DIV', isContentEditable: true}), _digitJumpOk('branchstock', {tagName: 'BODY'}), _digitJumpOk('orderhub', {}),
  _digitJumpOk('franchise', {}), _digitJumpOk('launcher', {tagName: 'BODY'})];
_ph = true; r.push(_digitJumpOk('home', {tagName: 'BODY'}));
console.log(JSON.stringify(r));""")
    assert got == [True, True, False, False, False, False, False, True, False]
    assert "TAB_NUMS[e.key] && _digitJumpOk(activeTab, e.target)" in feat
    # the documented letters and "/" are untouched
    assert "else if (e.key === 'd') { toggleDarkMode(); }" in feat and "else if (e.key === '/')" in feat


# ── 6 · the receive confirm ──────────────────────────────────────────────────────────────────────────────────────────
@needs_node
def test_the_receive_confirm_says_what_it_records():
    got = _node(_fn(BRN, "_brnRcvHead") + """
console.log(JSON.stringify([_brnRcvHead(4, 0, 4), _brnRcvHead(4, 2, 4), _brnRcvHead(4, 4, 0), _brnRcvHead(4, 3, 1), _brnRcvHead(3, 0, 0)]));""")
    assert got == ["รับไม่ครบทุกรายการ · แจ้งของขาด 4 รายการ", "รับไม่ครบทุกรายการ · แจ้งของขาด 4 รายการ", "รับ 4 รายการ",
                   "รับ 3 รายการ · แจ้งของขาด 1 รายการ", "ไม่ได้รับของ"]
    fn = _fn(BRN, "brnReceiveOrder")
    assert "${_brnRcvHead(lines.length, n, short)}" in fn and "· ${n} รายการ · วันที่" not in fn
    # the line under the picked rows and the line under the save button never say "รับครบ 0 รายการ" either
    assert "${n ? `รับครบ ${n} รายการ` : ''}" in _fn(BRN, "brnRcvPickedRender")
    assert "${n - st.picked.length ? `รับครบ ${n - st.picked.length} รายการ · ` : ''}" in _fn(BRN, "brnRecvAckChanged")


# ── 7 · the manual receipt form ─────────────────────────────────────────────────────────────────────────────────────
def test_the_manual_receipt_form_is_folded_under_the_receipts():
    h = _src("tabs/53-branchstock.html")
    pane = h[h.index('<div id="brn-pane-recv"'):h.index('<template id="hlp-branchstock-recv"')]
    assert pane.index('id="brnRecvOrder"') < pane.index('id="brnRecvOrdDone"') < pane.index('id="brnClaimCard"') \
        < pane.index('id="brnRecvHistory"') < pane.index('<details id="brnRecvManual"') < pane.index('id="brnRecvDate"')
    det = pane[pane.index('<details id="brnRecvManual"'):]
    assert " open" not in det[:det.index(">")] and "<summary" in det and "บันทึกของที่รับเอง</summary>" in det
    assert det.index("</details>") > det.index('id="brnRecvStatus"')                          # the manual form's own status stays inside it
    assert "_bmcStatus('brnRecvOrdDone'" in _fn(BRN, "brnReceiveOrder") and "_bmcStatus('brnRecvStatus'" not in _fn(BRN, "brnReceiveOrder")
    assert "brnRecvOrdDone: 'receive_pending'" in _src("js/16-supplyhome.js")


# ── 8 · the phone columns ───────────────────────────────────────────────────────────────────────────────────────────
@needs_node
def test_two_value_labels_of_the_columns_never_overlap():
    mob = _src("js/09c-mobile.js")
    pre = ESC + _fn(mob, "msColumns")
    # ร้านของฉัน's case: the last full month is high, the month in progress a little lower, side by side at the right edge
    got = _node(pre + """
const vals = [4923483, 4802947, 5434311, 6031203, 5665725, 5813366, 5972276, 5360273, 4095005, 4076093, 4519999, 4138887, 2810692];
const svg = msColumns(vals, {partial: 12, labels: vals.map((v, i) => 'm' + i), fmt: v => (v / 1e6).toFixed(2) + 'M', grain: 'month', height: 96});
const t = [...svg.matchAll(/<text x="([\\d.]+)" y="([\\d.]+)" text-anchor="middle" font-size="(\\d+)"[^>]*>([^<]*)<\\/text>/g)].map(m => ({x: +m[1], y: +m[2], s: +m[3], t: m[4]}));
const lab = t.filter(o => o.y < 80);
let clash = 0;
for (let i = 0; i < lab.length; i++) for (let j = i + 1; j < lab.length; j++) {
  const a = lab[i], b = lab[j], wa = a.t.length * a.s * 0.56, wb = b.t.length * b.s * 0.56;
  if (Math.abs(a.x - b.x) < (wa + wb) / 2 && Math.abs(a.y - b.y) < a.s * 1.6) clash++;
}
console.log(JSON.stringify([clash, lab.map(o => o.t)]));""")
    assert got[0] == 0, got
    assert "4.14M" in got[1]


# ── 9 · ร้านของฉัน's months ─────────────────────────────────────────────────────────────────────────────────────────
@needs_node
def test_the_franchise_page_writes_a_month_one_way():
    frc = _src("js/17-franchise.js")
    got = _node(_const(frc, "_FRC_MON") + "\n" + _fn(frc, "_frcMonthLabel") + """
console.log(JSON.stringify([_frcMonthLabel({month: '2026-10', label: 'ต.ค. 2026'}), _frcMonthLabel({month: '2026-10'}), _frcMonthLabel({month: '2025-01'}),
  _frcMonthLabel({month: 'x'})]));""")
    assert got == ["ต.ค. 2026", "ต.ค. 2026", "ม.ค. 2025", "x"]
    assert "msPeriodLabel(m.month" not in frc
    from routers import franchise as F
    assert F._month_label("2026-10") == "ต.ค. 2026" and F._month_label("2025-12") == "ธ.ค. 2025" and F._month_label("") == ""
    src = _src("backend/routers/franchise.py")
    assert "month_th(" not in src and 'm.get("label") or _month_label(m["month"])' in src


# ── 10 · the supply month controls ───────────────────────────────────────────────────────────────────────────────────
@needs_node
def test_the_supply_month_list_starts_at_the_first_month_with_data():
    core = _src("js/01-core.js")
    pre = _const(core, "SUPPLY_MONTHS_BACK") + "\n" + _fn(core, "supplyMonthAdd") + "\n" + _fn(core, "supplyMonthList")
    got = _node(pre + """
const L = (...a) => { const l = supplyMonthList(...a); return [l.length, l[0], l[l.length - 1]]; };
console.log(JSON.stringify([L('2026-10', 0, '2026-09', ''), L('2026-10', 0, null, ''), L('2026-10', 3, '2026-09', ''), L('2026-10', 0, '2026-12', ''),
  L('2026-10', 0, '2026-09', '2026-05'), L('2026-10', 0, '2020-01', '')]));""")
    assert got == [[2, "2026-10", "2026-09"],                 # Sep and Oct only
                   [18, "2026-10", "2025-05"],                # nothing known yet: the 18 months
                   [5, "2027-01", "2026-09"],                 # months ahead stay
                   [1, "2026-10", "2026-10"],                 # never later than this month
                   [3, "2026-10", "2026-05"],                 # a kept older choice stays in the list
                   [18, "2026-10", "2025-05"]]                # never more than 18 back
    fill = _fn(core, "supplyMonthFill")
    assert "_supplyFirstLoad()" in fill and "supplyMonthList(supplyMonthNow(), fwd, _supplyFirstMonth, keep)" in fill


def _mini_db():
    con = sqlite3.connect(":memory:")
    con.executescript("""CREATE TABLE bc_issue_slip(branch_id INT, slip_date TEXT); CREATE TABLE bc_order(branch_id INT, cycle_date TEXT, status TEXT);
        CREATE TABLE bc_stock_count(branch_id INT, count_date TEXT); CREATE TABLE bc_receipt(branch_id INT, receipt_date TEXT);""")
    return con


def test_first_month_reads_every_dated_record_and_skips_cancelled_orders():
    from bom_center import stock as ST
    con = _mini_db()
    assert ST.first_month(con) is None
    con.execute("INSERT INTO bc_order VALUES(1, '2025-05-03', 'cancelled')")             # a cancelled order is not the start
    con.execute("INSERT INTO bc_issue_slip VALUES(2, '2026-09-14')")
    con.execute("INSERT INTO bc_stock_count VALUES(1, '2026-10-01')")
    assert ST.first_month(con) == "2026-09"
    con.execute("INSERT INTO bc_receipt VALUES(1, '2026-08-30')")
    assert ST.first_month(con) == "2026-08" and ST.first_month(con, 1) == "2026-08" and ST.first_month(con, 2) == "2026-09"
    assert ST.first_month(con, 9) is None


def test_the_first_month_paths_are_open_to_the_roles_with_month_controls():
    rm = auth.ROLE_MODULES
    assert auth.path_allowed("GET", "/api/bom-center/stock/first-month", rm["branch"])
    assert auth.path_allowed("GET", "/api/bom-center/stock/first-month", rm["salessupport"])
    assert auth.path_allowed("GET", "/api/bom-center/first-month", rm["analyst"])
    assert not auth.path_allowed("GET", "/api/bom-center/stock/first-month", rm["franchise"])
    src = _src("backend/routers/bom_center.py")
    assert '@router.get("/api/bom-center/stock/first-month")\n@router.get("/api/bom-center/first-month")' in src.replace("\r\n", "\n")
