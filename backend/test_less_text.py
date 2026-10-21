# -*- coding: utf-8 -*-
"""PR-D6 (owner 2026-10-21) — less narrative on the supply pages: a result is a toast, an explanation is behind a "?", finished work is hidden.

The rules (tabs 51-56 and js 13-16):
  * a result of an action (save, import, download, send, approve) is a TOAST (uiToast); the inline status line is kept only for progress and a
    draft ("บันทึกร่าง 10:42"); a field to fix stays inline AND is summarised by an error toast;
  * the explanation of a page or a card is behind a "?" button (uiHelp / uiQ / data-help): a fragment keeps at most 3 explanatory blocks (more
    than 80 characters of text) outside <details> and <template>;
  * every work list hides what is finished (done / processed / posted / not needed) and shows it through ONE shared filter (uiStateFilter);
  * the launcher has no explanatory lines, and the branch "วันนี้เหลือทำอะไร" strip shows only the lines that are open.
"""
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
FRAGMENTS = ["tabs/51-ingpurchase.html", "tabs/52-bomcenter.html", "tabs/53-branchstock.html", "tabs/54-orderhub.html",
             "tabs/55-supplyhome.html", "tabs/56-franchise.html"]
LIMIT = 3          # explanatory blocks per fragment
LONG = 80          # characters of text that make a block explanatory


def _src(rel):
    return (REPO / rel).read_text(encoding="utf-8")


def _fn(src, name):
    m = re.search(r"(?:async )?function %s\(" % re.escape(name), src)
    assert m, name
    i = src.index("{", src.index(")", m.end()))
    depth, j = 0, i
    while j < len(src):
        c = src[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
        j += 1
    raise AssertionError("unbalanced " + name)


def _node(prelude, expr):
    out = subprocess.run([NODE, "-e", prelude + f"\nconsole.log(JSON.stringify({expr}));"], capture_output=True, text=True,
                         encoding="utf-8", check=True).stdout
    return json.loads(out)


# ── the explanatory blocks of a fragment ────────────────────────────────────────────────────────────────────────
HIDDEN = {"script", "style", "template", "details", "option", "select", "textarea", "svg"}
BLOCK = {"div", "p", "section", "ul", "ol", "li", "table", "tr", "td", "th", "form", "h1", "h2", "h3", "h4", "fieldset", "label", "dl", "dd", "dt",
         "tbody", "thead", "nav", "header", "footer", "main", "article", "button"}
INLINE = {"span", "a", "b", "i", "small", "em", "strong", "summary"}
VOID = {"br", "img", "input", "hr", "meta", "link"}


class _Blocks(HTMLParser):
    """The text of every element that holds no block element inside it (a leaf block), outside <details> / <template> / <style> / <script>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.hidden, self.out = [], 0, []

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        self.stack.append({"t": tag, "own": [], "blk": False, "line": self.getpos()[0]})
        if tag in HIDDEN:
            self.hidden += 1
        elif tag in BLOCK and len(self.stack) > 1:
            self.stack[-2]["blk"] = True

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["t"] == tag:
                break
        else:
            return
        while len(self.stack) > i:
            n = self.stack.pop()
            if n["t"] in HIDDEN:
                self.hidden -= 1
            elif not n["blk"]:
                txt = re.sub(r"\s+", " ", "".join(n["own"])).strip()
                if len(txt) > LONG and n["t"] in BLOCK | INLINE:
                    self.out.append((n["line"], n["t"], txt))
            if self.stack and n["t"] not in HIDDEN:
                self.stack[-1]["own"].append("".join(n["own"]))

    def handle_data(self, data):
        if not self.hidden and self.stack:
            self.stack[-1]["own"].append(data)


def explanatory_blocks(rel):
    p = _Blocks()
    p.feed(_src(rel))
    return p.out


@pytest.mark.parametrize("rel", FRAGMENTS)
def test_a_fragment_has_at_most_three_explanatory_blocks_outside_help(rel):
    """No page of the supply module carries a paragraph of explanation: it is a <template> behind a "?" or a closed <details>."""
    blocks = explanatory_blocks(rel)
    assert len(blocks) <= LIMIT, f"{rel}: {len(blocks)} explanatory blocks > {LONG} characters\n" + "\n".join(f"  line {ln} <{t}> {txt[:90]}" for ln, t, txt in blocks)


def test_the_measure_sees_a_paragraph_and_ignores_help():
    """The gate itself: a long <p> counts, the same text in <template> or <details> does not."""
    text = "ก" * 120
    for html, n in [(f"<div><p>{text}</p></div>", 1), (f"<div><template><p>{text}</p></template></div>", 0),
                    (f"<div><details><summary>x</summary><p>{text}</p></details></div>", 0), (f"<div><p>{'ก' * 60}</p></div>", 0)]:
        p = _Blocks()
        p.feed(html)
        assert len(p.out) == n, html


def test_every_help_button_points_at_a_template_that_exists():
    """data-help="id" opens <template id="id" data-title="…">; a button without its template would open nothing."""
    for rel in FRAGMENTS:
        html = _src(rel)
        for ref in re.findall(r'data-help="([^"]+)"', html):
            assert re.search(r'<template id="%s"[^>]*data-title="[^"]+"' % re.escape(ref), html), f"{rel}: no template for {ref}"


def test_every_pane_of_a_supply_page_has_its_help_behind_the_header_question_mark():
    """The "?" in the header of a page (data-help-tab) opens the help of the pane that shows: a template hlp-<tab>-<pane> in the fragment, or the text of
    the role registered by orderhub (_OHB_HELP)."""
    bmc, brn, oh = _src("tabs/52-bomcenter.html"), _src("tabs/53-branchstock.html"), _src("tabs/54-orderhub.html")
    for html, tab in ((bmc, "bomcenter"), (brn, "branchstock"), (oh, "orderhub")):
        assert f'data-help-tab="{tab}"' in html and 'aria-label="คำอธิบาย"' in html, tab
    for pane in ("overview", "prep", "rel", "fc", "cal", "events", "zones", "ing", "price", "pricefile", "conv", "codereq", "rcheck", "rec", "issue"):
        assert f'<template id="hlp-bomcenter-{pane}"' in bmc, pane
    for pane in ("issue", "order", "count", "recv", "var"):
        assert f'<template id="hlp-branchstock-{pane}"' in brn, pane
    ohb = _src("js/15-orderhub.js")
    for pane in ("queue", "claims", "issues", "issuevar", "persales", "comp", "adoption", "pilot", "central", "month"):
        assert re.search(r"\n  %s: \{" % pane, ohb), pane
        assert f'id="ohb-pane-{pane}"' in oh and f'id="ohbHelp-{pane}"' not in oh, pane          # no folded block at the bottom of the pane any more
    for pane in ("rec", "issue"):                                  # PR-R's two panes: the "?" inside the pane is the same shared button
        span = re.search(r'<div id="bmc-pane-%s".*?(?=\n  <div id="bmc-pane-|\n  <!-- \u2500\u2500|\Z)' % pane, bmc, re.S).group(0)
        assert f'data-help="hlp-bomcenter-{pane}"' in span and "bmc-help" not in span and "rec-q" not in span, pane
    js = _src("js/13-bom-center.js")
    assert "function bmcRecHelp" not in js and "function bmcIssHelp" not in js           # their popovers are templates now
    assert _src("js/01-core.js").count("window.uiHelp = function") == 1                  # ONE uiHelp


def test_the_explanations_keep_their_facts():
    """The text was moved behind the "?", not dropped: the facts the D5 manual and the pilot guides quote are still in the fragments / scripts."""
    bmc, brn = _src("tabs/52-bomcenter.html"), _src("tabs/53-branchstock.html")
    for fact in ("BF_459NET ถึง BF_1,299NET", "13:15 สองวันก่อนวันส่ง", "ประกาศใช้ภายในวันที่ 25", "ตั้งหลายสาขาพร้อมกัน", "ปรับยอดซื้อตามสัดส่วนวัน"):
        assert fact in bmc, fact
    for fact in ("ตรวจนับสิ้นเดือน", "ใช้น้อยกว่าสูตร", "ยืนยันรับสินค้าครบ"):
        assert fact in brn, fact
    assert "มติเจ้าของ 13/10/2026" in _src("js/13-bom-center.js")
    assert "คลังระบุยอดส่งจริงภายใน 14:00" in _src("js/15-orderhub.js")


# ── the shared pieces of 01-core.js ─────────────────────────────────────────────────────────────────────────────
CORE = _src("js/01-core.js")


def test_one_toast_one_help_one_filter_in_the_core():
    for name in ("uiToast", "uiHelp", "uiQ", "uiPaneHelp", "uiStateFilter"):
        assert re.search(r"window\.%s\s*=" % name, CORE), name
    assert "function notifyError(msg) { uiToast(msg, 'error'); }" in CORE and "function notifySuccess(msg) { uiToast(msg, 'success'); }" in CORE
    assert "globalErrToast" not in CORE and "globalOkToast" not in CORE                       # the two older toasts are gone
    assert "shm-toast" not in _src("js/16-supplyhome.js") and "shm-toast" not in _src("index.html")      # and the third
    html = _src("index.html")
    for token in ("#uiToasts", ".ui-toast-error", ".ui-q", ".ui-help-back", ".tskf-btn"):
        assert token in html, token
    css = _src("docs/ui-kit/tokens.css")
    assert "--toast-bg" in css and "body.dark { --toast-shadow" in css


def test_toast_contract_in_the_source():
    """success / info 4 s, an error stays, max 3, role="status" (an error role="alert"), aria-live polite, a tap closes, the next step inside the toast."""
    src = CORE[CORE.index("const MAX = 3"):CORE.index("window.uiHelp = function")]
    assert "MAX = 3" in src and "error: 0" in src and "success: 4000" in src and "info: 4000" in src
    assert "k === 'error' ? 'alert' : 'status'" in src
    assert "setAttribute('aria-live', 'polite')" in src
    assert "el._close()" in src and "next(text2, label, fn)" in src
    assert "(max-width:700px)" in src                                  # the phone: above the sticky action bar or the bottom navigation


def test_help_dialog_contract_in_the_source():
    src = CORE[CORE.index("window.uiHelp = function"):CORE.index("window._uiHelpReg")]
    assert "role" in src and "aria-modal" in src and "aria-labelledby" in src
    assert "e.key === 'Escape'" in src and "e.key !== 'Tab'" in src                      # Esc closes, Tab stays inside
    assert "from.focus()" in src                                                         # the focus returns to the button that opened it


@needs_node
def test_state_filter_splits_remembers_and_falls_back():
    """uiStateFilter: default ยังไม่เสร็จ, a key the list does not offer reads as ยังไม่เสร็จ, the choice survives a blocked localStorage."""
    block = CORE[CORE.index("window.uiStateFilter = (function () {"):]
    block = block[:block.index("})();") + len("})();")]
    pre = ("let store = {}; global.localStorage = { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };\n"
           "global.document = { getElementById: id => null };\nconst window = global;\n" + block)
    r = _node(pre, "(() => { const u = window.uiStateFilter, out = {};"
                   " out.def = u.get(); u.set('done'); out.done = u.get(); u.set('review'); out.review3 = u.get(); out.review4 = u.get(['todo','review','done','all']);"
                   " out.split = u.split([1,2,3,4,5], x => x % 2 === 0).counts;"
                   " out.empty = u.empty('todo', 'pick', 'ใบ'); out.emptyAll = u.empty('all', 'pick', 'ใบ');"
                   " store = null; global.localStorage = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };"
                   " u.set('all'); out.blocked = u.get(); return out; })()")
    assert r["def"] == "todo" and r["done"] == "done" and r["review3"] == "todo" and r["review4"] == "review"
    assert r["split"] == {"todo": 3, "done": 2, "all": 5}
    assert "ไม่มีใบค้าง" in r["empty"] and "ดูทั้งหมด" in r["empty"] and "ไม่มีใบ" in r["emptyAll"] and "ดูทั้งหมด" not in r["emptyAll"]
    assert r["blocked"] == "all"


# ── where a status goes (13-bom-center.js) ────────────────────────────────────────────────────────────────────
BMC = _src("js/13-bom-center.js")


def _kind(msg, ink=None, sid="x"):
    pre = ("const _BMC_STATUS_FIELD = " + re.search(r"const _BMC_STATUS_FIELD = (.*?);\n", BMC).group(1) + ";\n" + _fn(BMC, "_bmcStatusKind"))
    return _node(pre, f"_bmcStatusKind({json.dumps(sid)}, {json.dumps(msg, ensure_ascii=False)}, {json.dumps(ink)})")


@needs_node
@pytest.mark.parametrize("msg, ink, want", [
    ("", None, {"inline": "clear"}),
    ("กำลังนำเข้า…", None, {"inline": "note"}),                                                   # progress stays a small line
    ("✓ บันทึกร่างแล้ว 10:42", "var(--good-ink)", {"inline": "note"}),                           # an autosaved draft too
    ("บันทึกร่างแล้ว 10:42 · ต้องระบุเหตุผล 2 รายการก่อนส่ง", "var(--amber-ink)", {"inline": "note"}),
    ("✓ บันทึกแล้ว", "var(--good-ink)", {"toast": "success", "inline": "clear"}),
    ("ไม่สำเร็จ: เครือข่ายขัดข้อง", "var(--bad-ink)", {"toast": "error", "inline": "clear"}),
    ("ใส่ชื่อของสินค้า", "var(--bad-ink)", {"toast": "error", "inline": "field"}),               # a field to fix: inline + the toast
    ("เลือกสาขาก่อนบันทึก", "var(--amber-ink)", {"toast": "warn", "inline": "warn"}),
    ("แก้ไขแล้ว ยังไม่บันทึก", "var(--amber-ink)", {"inline": "warn"}),                          # a state that is still true
    ("ดูตัวอย่างด้านล่าง แล้วกดบันทึก", None, {"toast": "info", "inline": "clear"}),
])
def test_where_a_status_goes(msg, ink, want):
    assert _kind(msg, ink) == want


@needs_node
def test_a_counter_of_a_table_stays_inline():
    assert _kind("12 จาก 274 รายการ · มีปัญหา 3", None, "bmcIngSummary") == {"inline": "note"}


def test_every_status_call_goes_through_the_two_routers_and_the_toast():
    """_bmcStatus and bmcStatus are the only writers of a status; both end in uiToast; หน้างาน's next step rides inside the success toast."""
    for fn in ("_bmcStatus", "bmcStatus"):
        assert "uiToast(" in _fn(BMC, fn), fn
    assert "shmAfterStatus(id, h)" in _fn(BMC, "_bmcStatus")
    shm = _src("js/16-supplyhome.js")
    assert "toast.next(" in _fn(shm, "_shmToast") and "uiToast(" in _fn(shm, "_shmToast")


# ── lists: finished work is hidden by default, one filter ─────────────────────────────────────────────────────────
def test_every_work_list_uses_the_one_filter():
    """The filter is called from every list the owner named (orders queue, claims, issue slips, close steps, register / backlog lists, import previews)."""
    bmc, oh, brn, shm = BMC, _src("js/15-orderhub.js"), _src("js/14-branch.js"), _src("js/16-supplyhome.js")
    want = {"bomcenter": (bmc, ["bmcSetupFilter", "bmcCrFilter", "bmcSubFilter", "bmcPrepChips", "bmcPpFilter", "bmcRlFilter", "bmcRcBar", "bmcCrqFilter", "bmcAvgFilter",
                                "bmcPackFilter", "bmcEvFilter"]),
            "orderhub": (oh, ["ohbStatusChips", "ohbClaimChips", "ohbIsChips", "ohbStepFilter", "ohbLinkSeg", "ohbCBar", "ohbAdFilter", "ohbPlFilter"]),
            "branchstock": (brn, ["brnHistory", "brnIssHistory", "brnOrdHistory", "brnRecvHistory", "brnClaimMine", "brnVarFilter"]),
            "supplyhome": (shm, ["shmFilter"])}
    for tab, (src, ids) in want.items():
        for i in ids:
            assert re.search(r"uiStateFilter\.bar\(\s*['\"]?%s|_brnHist\('%s'|id=\"%s" % (i, i, i), src) or i in src, (tab, i)
    assert "uiStateFilter.bar('shmFilter'" in shm and "uiStateFilter.get(_SHM_FILTER_KEYS)" in shm      # หน้างาน keeps รอตรวจ and uses the shared control
    assert "uiStateFilter.get(['todo', 'review', 'done', 'all'])" in bmc                              # a branch of a task is hidden the same way


def test_old_status_chips_and_folds_are_gone():
    oh, brn = _src("js/15-orderhub.js"), _src("js/14-branch.js")
    for old in ("ohbSetStatus", "_OHB_ST_WH", "ohbIsSetStatus", "ohbClaimSetStatus", "_OHB_CLAIM_ST"):
        assert old not in oh, old
    assert "ยืนยันครบ ${nOk} วัน" not in brn                                                    # the "N days confirmed" fold of the day strip
    brnf = _src("tabs/52-bomcenter.html")
    assert 'id="bmcAvgHist"' not in brnf and 'id="bmcPackHist"' not in brnf                       # the two history folds of ตั้งค่า → หน่วยและการแปลง


# ── the launcher and the strip of the branch ───────────────────────────────────────────────────────────────────────
def test_the_launcher_has_no_explanatory_line():
    html, js = _src("tabs/57-launcher.html"), _src("js/18-launcher.js")
    assert "เลือกงานจากไอคอนของแผนก" not in html and "แสดงเฉพาะแผนกที่บัญชีนี้มีสิทธิ์" not in js
    assert "lnchDeptHint" not in html and "lnchDeptHint" not in js and "lnch-lead" not in html


@needs_node
def test_the_branch_strip_shows_only_the_open_lines_and_hides_when_nothing_is_open():
    shm = _src("js/16-supplyhome.js")
    pre = ("const esc = s => String(s); const _SHM_WD = []; const _shmRole = () => 'branch'; const fmtLeft = h => 'x', fmtCutoff = s => 'c', fmtDayShort = s => 'd';\n"
           "const els = { shmToday: { style: {} }, shmTodayRows: { innerHTML: '' } }; const document = { getElementById: id => els[id] };\n"
           + _fn(shm, "_shmTodayRows") + "\n" + _fn(shm, "_shmTodayStrip"))
    done = {"items": [{"key": "issue_today", "status": "done", "confirmed_hhmm": "10:00", "go": {"tab": "branchstock", "pane": "issue"}},
                      {"key": "order_due_1:7", "status": "done", "cutoff_at": "2026-10-21T13:15:00+07:00", "round_th": "รอบ 1", "go": {"tab": "branchstock", "pane": "order"}}]}
    open_ = {"items": [{"key": "issue_today", "status": "todo", "slip_status": "missing", "go": {"tab": "branchstock", "pane": "issue"}}]}
    r = _node(pre, f"(() => {{ _shmTodayStrip({json.dumps(done)}, 'branch'); const a = [els.shmToday.style.display, els.shmTodayRows.innerHTML];"
                   f" _shmTodayStrip({json.dumps(open_)}, 'branch'); const b = [els.shmToday.style.display, els.shmTodayRows.innerHTML];"
                   f" _shmTodayStrip({json.dumps(open_)}, 'management'); return [a, b, els.shmToday.style.display]; }})()")
    (a_disp, _), (b_disp, b_rows), c_disp = r
    assert a_disp == "none"                                                                     # nothing open: the strip is not there
    assert b_disp == "" and "ตัดเบิกวันนี้" in b_rows and "✓" not in b_rows and "รับสินค้า" not in b_rows      # one open line, no finished line
    assert c_disp == "none"                                                                     # another role never sees it


# ── what "finished" means (owner correction 2026-10-21): done = the work is complete, never "has no data yet" ─────────────────────────────────
@needs_node
def test_prep_classification_no_recipe_is_unfinished_work():
    """สูตรกึ่งสำเร็จ: a sub-recipe with NO recipe (none), an incomplete one and one that needs checking are ยังไม่เสร็จ; only ok is เสร็จแล้ว."""
    block = CORE[CORE.index("window.uiStateFilter = (function () {"):]
    block = block[:block.index("})();") + len("})();")]
    pre = ("global.localStorage = { getItem: k => null, setItem() {} }; global.document = { getElementById: id => null };\nconst window = global;\n" + block
           + "\n" + _fn(BMC, "_bmcPrepDone"))
    rows = [{"status": s} for s in ["none"] * 63 + ["incomplete"] * 4 + ["check"] * 2 + ["ok"] * 5]
    r = _node(pre, f"(() => {{ const sp = window.uiStateFilter.split({json.dumps(rows)}, _bmcPrepDone); return [sp.counts, sp.todo.filter(x => x.status === 'none').length, sp.done.every(x => x.status === 'ok')]; }})()")
    assert r[0] == {"todo": 69, "done": 5, "all": 74} and r[1] == 63 and r[2] is True
    # the chips, the counts and the rows of the page all read the one classifier
    fn = _fn(BMC, "bmcRenderPrep")
    assert "uiStateFilter.split(d.rows, _bmcPrepDone)" in fn and "!_bmcPrepDone(r)" in fn and "'incomplete' || r.status === 'check'" not in fn


def test_other_lists_do_not_call_missing_data_finished():
    """The audit of the shared filter: a round not due yet is neither finished nor work; a usage line whose data is incomplete is work."""
    oh, brn = _src("js/15-orderhub.js"), _src("js/14-branch.js")
    comp = _fn(oh, "ohbRenderComp")
    assert "r.status !== 'upcoming'" in comp and "csp.counts.all = c.rows.length" in comp          # upcoming shows under ทั้งหมด only
    var = _fn(brn, "brnRenderVariance")
    assert "incompleteRows = flaggedAll.filter(r => r.data_incomplete)" in var and "todo: askOpen.length + incompleteRows.length" in var
