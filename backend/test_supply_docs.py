# -*- coding: utf-8 -*-
"""PR-M6 — the written guides of วัตถุดิบและใบสั่งซื้อ follow the screens.

  * the role rows of the manual (tabs/47-manual.html) list, per role, the DEPARTMENTS the person really sees (PR-D5: DEPARTMENTS in
    js/01-core.js with the permission rule of js/18-launcher.js deptVisible, read here in Python) and nothing else;
  * the written guides name a page the way the menu reaches it now, "<แผนก> → <หน้า>", never by the old menu words;
  * the guides of docs/pilot are written for a phone: a 16 px base, a viewport, and tasks that fit one screen;
  * every bold label of a guide is a word that exists on a screen (js/, tabs/, the Thai strings of backend/bom_center),
    so a renamed button breaks this test instead of the guide;
  * every guide is reachable from the manual or the onboarding document, and the handout texts say what the guides say.
"""
import re
from pathlib import Path

import pytest

import auth

import test_supply_chips_roles as T

REPO = Path(__file__).resolve().parent.parent
PILOT = REPO / "docs" / "pilot"
MANUAL = (REPO / "tabs" / "47-manual.html").read_text(encoding="utf-8")
GUIDES = sorted(PILOT.glob("*_guide.html"))

ROLE_ROWS = {"branch", "warehouse", "salessupport", "operation", "analyst", "innovation", "accounting", "management", "franchise"}


def _manual_rows():
    table = re.search(r'<table id="manualSupplyRoles".*?</table>', MANUAL, re.S)
    assert table, "the role table of the module section is missing from the manual"
    rows = {}
    for m in re.finditer(r'<tr[^>]*data-role="(\w+)"[^>]*>(.*?)</tr>', table.group(0), re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", m.group(2), re.S)
        rows[m.group(1)] = {
            "chips": re.findall(r'<b class="chip">([^<]+)</b>', cells[1]),
            "pages": re.findall(r'<i class="pg">([^<]+)</i>', cells[1]),
        }
    return rows


# ── DEPARTMENTS (js/01-core.js), read the way test_supply_chips_roles.py reads SUPPLY_CHIPS ─────────────────
def _departments():
    m = re.search(r"const DEPARTMENTS = \[(.*?)\n\];\n", T.JS, re.S)
    assert m, "DEPARTMENTS not found in js/01-core.js"
    out, cur = [], None
    for line in m.group(1).splitlines():
        h = re.match(r"\s*\{ key: '(\w+)', label: '([^']+)', duty: '([^']+)', icon: '(\w+)', hue: '(\w+)', roles: \[([^\]]*)\]", line)
        if h:
            cur = {"key": h.group(1), "label": h.group(2), "roles": re.findall(r"'(\w+)'", h.group(6)), "pages": []}
            out.append(cur)
            continue
        p = re.match(r"\s*\{ tab: '(\w+)'(?:, pane: '(\w+)')?(, bom: true)?, label: '([^']+)', icon: '(\w+)'(.*?)\}[,\]]?\s*(?://.*)?$", line)
        if p:
            rest = p.group(6)
            roles = re.search(r"roles: \[([^\]]*)\]", rest)
            mods = re.search(r"mods: \[([^\]]*)\]", rest)
            cur["pages"].append({"tab": p.group(1), "pane": p.group(2), "label": p.group(4), "bom": bool(p.group(3)),
                                 "roles": re.findall(r"'(\w+)'", roles.group(1)) if roles else None,
                                 "mods": re.findall(r"'([\w.]+)'", mods.group(1)) if mods else None})
    return out


def _dept_visible(role):
    """[(department, [page, ...])] as js/18-launcher.js deptVisible gives them: tabAllowed, the entry's own mods / roles, and for a pane of
    the supply module the chip item that names exactly that pane (its mods and roles); the person's home department first."""
    mods = set(auth.MODULES) if role == "management" else set(auth.ROLE_MODULES.get(role, set()))
    tabmod = T._tab_modules()
    items = [i for c in T._chips() for i in c["items"]]

    def tab_ok(t):
        need = tabmod.get(t)
        return not need or bool(set(need) & mods)

    def item_ok(it):
        return tab_ok(it["tab"]) and (not it["mods"] or bool(set(it["mods"]) & mods)) and (it["roles"] is None or role in it["roles"])

    rows = []
    for order, d in enumerate(_departments()):
        pages = []
        for p in d["pages"]:
            if not tab_ok(p["tab"]) or (p["mods"] and not set(p["mods"]) & mods) or (p["roles"] and role not in p["roles"]):
                continue
            if p["pane"]:
                named = [i for i in items if i["tab"] == p["tab"] and i["pane"] == p["pane"]]
                if named and not any(item_ok(i) for i in named):
                    continue
            pages.append(p)
        if pages:
            rows.append((role in d["roles"], order, d, pages))
    rows.sort(key=lambda r: (-int(r[0]), r[1]))
    return [(r[2], r[3]) for r in rows]


SUPPLY_TABS = {"bomcenter", "orderhub", "branchstock", "supplyhome"}


def test_the_manual_has_one_row_per_role_and_each_lists_exactly_the_departments_it_sees():
    rows = _manual_rows()
    assert set(rows) == ROLE_ROWS
    for role, row in rows.items():
        seen = _dept_visible(role)
        assert row["chips"] == [d["label"] for d, _ in seen], f"{role}: manual {row['chips']} != departments {[d['label'] for d, _ in seen]}"
        # every page named under a department is a page of that department that the role may open (nothing it cannot open)
        allowed = {(d["label"], p["label"]) for d, pages in seen for p in pages}
        named_in = {p for p in row["pages"]}
        assert named_in <= {lbl for _, lbl in allowed}, f"{role}: pages the role cannot open: {sorted(named_in - {lbl for _, lbl in allowed})}"


def test_a_role_names_every_supply_page_of_its_own_department_in_the_manual():
    """The home department of a role lists its supply pages (bomcenter, orderhub, branchstock panes) in the row; the manager holds every
    department and is sent to the map instead of 70 names."""
    rows = _manual_rows()
    assert "แผนที่เมนูตามแผนก" in re.search(r'<tr[^>]*data-role="management"[^>]*>(.*?)</tr>', MANUAL, re.S).group(1)
    for role in ("branch", "warehouse", "salessupport", "operation", "analyst", "innovation", "accounting", "franchise"):
        seen = _dept_visible(role)
        home, pages = next((d, p) for d, p in seen if role in d["roles"])
        want = {p["label"] for p in pages if (p["tab"] in SUPPLY_TABS or p["tab"] == "franchise") and not p["bom"]}
        missing = want - set(rows[role]["pages"])
        assert not missing, f"{role}: the row does not name {sorted(missing)} of {home['label']}"
        # PR-S4: a recipe page (bom: true) is hidden in stock mode, so the row leaves it out and the appendix of the manual names it
        appendix = re.search(r'id="manualRecipeAppendix".*?</ol>', MANUAL, re.S).group(0)
        gone = {p["label"] for p in pages if (p["tab"] in SUPPLY_TABS) and p["bom"]}
        assert all(g in appendix for g in gone), f"{role}: {sorted(g for g in gone if g not in appendix)} missing from the recipe appendix"


def test_every_department_and_page_label_of_the_manual_exists_in_departments():
    labels = {p["label"] for d in _departments() for p in d["pages"]}
    names = {d["label"] for d in _departments()}
    for role, row in _manual_rows().items():
        for lbl in row["chips"]:
            assert lbl in names, f"{role}: '{lbl}' is not a department"
        for lbl in row["pages"]:
            assert lbl in labels, f"{role}: '{lbl}' is not a page label of DEPARTMENTS"


def test_the_manual_has_the_department_map_drawn_from_departments_at_run_time():
    """แผนที่เมนูตามแผนก: the markup holds only the empty body; renderManualDeptMap (js/18-launcher.js) fills it from DEPARTMENTS each time the
    manual opens (TABS.manual.load), so the map cannot drift from the menu."""
    card = re.search(r'<div class="card p-5 mb-4" id="manualDeptMap">.*?<div id="manualDeptMapBody" class="mdm-grid"></div>', MANUAL, re.S)
    assert card and "แผนที่เมนูตามแผนก" in card.group(0)
    for d in _departments():
        assert f'data-dept="{d["key"]}"' not in MANUAL           # no department is typed into the page
    launcher = (REPO / "js" / "18-launcher.js").read_text(encoding="utf-8")
    fn = re.search(r"function renderManualDeptMap\(\) \{.*?\n\}\n", launcher, re.S).group(0)
    assert "DEPARTMENTS.map" in fn and "deptVisible()" in fn and "manualDeptMapBody" in fn
    assert "manual: { load: () => { if (typeof renderManualDeptMap === 'function') renderManualDeptMap(); } }" in T.JS


def test_the_owner_guide_is_linked_from_the_manual_and_the_owner_row_names_the_page():
    assert "/docs/pilot/owner_guide.html" in MANUAL
    row = re.search(r'<tr[^>]*data-role="franchise"[^>]*>(.*?)</tr>', MANUAL, re.S).group(1)
    assert "ร้านของฉัน" in row and row.count('class="chip"') == 1            # one department, one page
    assert (PILOT / "owner_guide.html").exists()


def test_the_close_has_five_steps_in_the_manual_and_nine_in_full_mode():
    block = re.search(r'id="manualCloseSteps".*?</ol>', MANUAL, re.S).group(0)
    assert len(re.findall(r"<li>", block)) == 5 and "ปิดงวดเดือนในโหมดสต๊อกมี 5 ขั้น" in MANUAL and "โหมดเต็มระบบมี 9 ขั้น" in MANUAL     # PL1/PL2: one list of five steps


def test_the_branch_flow_of_the_manual_names_the_phone_screens():
    block = re.search(r'id="manualBranchFlow".*?</ol>', MANUAL, re.S).group(0)
    for w in ("ส่งใบตัดเบิก", "ส่งใบสั่งซื้อ", "รับครบ", "มีรายการไม่ครบ", "บันทึกรับของ", "แจ้งของขาด/เสีย", "ส่งผลนับ", "แจ้งว่าบันทึกรับของครบ"):
        assert w in block, w


# ── phone format of the guides ───────────────────────────────────────────────────────────────────────────
def _tasks(html):
    return re.findall(r'<section class="task"[^>]*>(.*?)</section>', html, re.S)


def _plain(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def test_there_is_a_guide_for_every_role_that_has_work_in_the_module():
    names = {p.name for p in GUIDES}
    assert names == {"branch_guide.html", "salessupport_guide.html", "warehouse_guide.html", "accounting_guide.html", "owner_guide.html"}


@pytest.mark.parametrize("path", GUIDES, ids=lambda p: p.name)
def test_a_guide_is_written_for_a_phone(path):
    html = path.read_text(encoding="utf-8")
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html
    assert re.search(r"body \{[^}]*font:16px/", html), "the base font is 16 px"
    assert not re.search(r"\bfont:1[0-5](?:\.\d+)?px", html.split("</style>")[0].split("@media print")[0]), "no body text under 16 px except where noted"
    tasks = _tasks(html)
    assert len(tasks) >= 4
    for t in tasks:
        title = re.search(r"<h2>(.*?)</h2>", t).group(1)
        steps = len(re.findall(r"<li>", t))
        assert steps <= 9, f"{path.name} · {title}: {steps} steps"
        assert len(_plain(t)) <= 1250, f"{path.name} · {title}: {len(_plain(t))} characters, more than one screen"
    assert len(_plain(html)) <= 14000, f"{path.name}: the guide is a wall of text"


@pytest.mark.parametrize("path", GUIDES, ids=lambda p: p.name)
def test_every_bold_label_of_a_guide_is_on_a_screen(path):
    """A bold word in a guide is a button, a status or a title the person sees: it must exist in the app's own text."""
    src = ""
    for f in list((REPO / "js").glob("*.js")) + list((REPO / "tabs").glob("*.html")):
        src += f.read_text(encoding="utf-8")
    for f in (REPO / "backend" / "bom_center").glob("*.py"):
        src += f.read_text(encoding="utf-8")
    src += (REPO / "backend" / "routers" / "bom_center.py").read_text(encoding="utf-8")
    src += (REPO / "backend" / "routers" / "franchise.py").read_text(encoding="utf-8")
    src += (REPO / "index.html").read_text(encoding="utf-8")        # the shell: the phone's bottom navigation and the ฉัน sheet
    html = path.read_text(encoding="utf-8")
    body = html.split("<body>")[1]
    missing = []
    for lbl in sorted(set(re.findall(r"<b>([^<]+)</b>", body))):
        lbl = lbl.strip()
        core = lbl.replace("…", "").rstrip(":").strip()
        if lbl in ALLOW_BOLD or core in ALLOW_BOLD:
            continue
        if core in src:
            continue
        # a phrase made of labels ("ปิดงวดเดือน → สรุปผลต่างรายเดือน", "ขาดใบตัดเบิก N วัน", "ตัดรอบ ตั้งแต่ ถึง"): every part is on a screen
        parts = [p for p in re.split(r"\s*→\s*|\bN\b|\bX/Y\b|\s+", core) if p and p not in ("+", "·")]
        if not parts or not all(p in src for p in parts):
            missing.append(lbl)
    assert not missing, f"{path.name}: bold labels that no screen shows: {missing}"


# bold words that are not a label of one screen: a department, a figure, or a phrase made of two labels
ALLOW_BOLD = {
    "เบิกใช้", "ของเสีย", "สาเหตุ", "ประสานงานขาย", "ผลต่าง", "ใช้จริง", "ตามสูตร",
    "เพิ่มไปยังหน้าจอหลัก",                   # the browser's own menu item, not the app's
    "สาขาปรับ +20% (โปรโมชัน)",               # the chip is built from the branch's own percent and reason
}


# ── the handout and the onboarding document say what the guides say ─────────────────────────────────────────
def _load(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    import sys
    sys.path.insert(0, str(REPO / "scripts"))
    spec.loader.exec_module(m)
    return m


def test_the_branch_handout_carries_the_daily_rule_and_the_receive_question():
    HO = _load("branch_handout")
    txt = HO.message("อุดรธานี", "wh_udon", "PW-X")
    for w in ("ใบตัดเบิก", "ส่งใบตัดเบิกของวันนี้ก่อน", "13:15", "รับครบ", "มีรายการไม่ครบ", "ส่งใบสั่งซื้อ", "เวลาส่งสุดท้าย", "หน้าแรก · ตัดเบิก · สั่งของ · รับของ · ฉัน", "นับของ"):
        assert w in txt, w
    for w in ("ปล่อย", "token", "Sales Support", "ส่วนกลาง"):
        assert w not in txt, w
    card = HO.card("อุดรธานี", "wh_udon", "PW-X")
    assert "ตัดเบิก" in card and "ส่งใบตัดเบิก" in card and "รับครบ" in card


def test_the_owner_handout_names_the_cards_and_the_guide():
    HO = _load("branch_handout")
    txt = HO.owner_message("อุดรธานี", "own_udon", "PW-X")
    for w in ("ร้านของฉัน", "ดี / ควรดู / ต้องแก้", "ยอดขาย", "กำไรขั้นต้น", "วัตถุดิบ", "วินัย", "เมนูขายดี", "ดูได้อย่างเดียว", "owner_guide.html"):
        assert w in txt, w
    assert "13:15" not in txt
    assert "owner_guide.html" in HO.owner_card("อุดรธานี", "own_udon", "PW-X")


def test_the_onboarding_document_lists_both_accounts_and_the_guides():
    doc = (REPO / "docs" / "ONBOARDING_30_BRANCHES.md").read_text(encoding="utf-8")
    for w in ("wh_<ตัวย่อ>", "own_<ตัวย่อ>", "branch_guide.html", "warehouse_guide.html", "salessupport_guide.html", "accounting_guide.html",
              "owner_guide.html", "docs\\pilot", "ตัดเบิก"):
        assert w in doc, w
    for w in ("Owner Write-Token", "Sales Support"):
        assert w not in doc, w


def test_the_overhaul_document_has_the_phase_3_block_and_the_m6_row():
    doc = (REPO / "docs" / "BOM_CENTER_UX_OVERHAUL.md").read_text(encoding="utf-8")
    assert "### 5.1 Phase 3" in doc
    for pr in ("PR-M1", "PR-M2", "PR-M3", "PR-M4", "PR-M5", "PR-M6"):
        assert re.search(rf"\| {pr} \|", doc), pr


# ── PR-D5 · the documents describe the department navigation, not the old menu ───────────────────────────────
OLD_MENU = [
    r"เมนูซ้าย", r"ปุ่มด้านบน", r"แถบมุมมอง", r"แถบด้านบน", r"แถบด้านซ้าย", r"7 หัวข้อ", r"หน้าระบบเดิม", r"ชิป",
    r"สาขาของฉัน ?[→›]", r"งานประจำเดือน ?[→›]", r"ข้อมูลตั้งต้น ?[→›]", r"ปิดงวดเดือน ?[→›]", r"ใบสั่งซื้อ ?[→›]", r"หน้างาน ?›",
    r"ตั้งค่า ?[→›] ?(?:รอบส่งของสาขา|โซนจัดเก็บ|ปฏิทินเทศกาล|แจ้งเปลี่ยนรหัสสินค้า)",
    r"เมนู <b>วัตถุดิบและใบสั่งซื้อ",
]
DOCS_D5 = (["tabs/47-manual.html", "docs/ONBOARDING_30_BRANCHES.md", "scripts/branch_handout.py", "scripts/branch_task_cards.py",
            "backend/bom_center/pilot.py"]
           + [f"docs/pilot/{n}" for n in ("branch_guide.html", "salessupport_guide.html", "warehouse_guide.html", "accounting_guide.html",
                                          "owner_guide.html", "pilot_plan.html")]
           + [f"docs/pilot/cards/card_{n}.html" for n in ("issue", "order", "receive", "count")]
           + ["docs/sop/WP-SCM-001_ingredient_order_procedure.html"])


def _doc_text(rel):
    txt = (REPO / rel).read_text(encoding="utf-8")
    if rel.startswith("docs/sop/"):
        txt = re.sub(r"<tr><td>1\.\d+</td>.*?</tr>", "", txt, flags=re.S)       # the revision history records what a page was called then
    if rel == "tabs/47-manual.html":
        txt = re.sub(r"<!--.*?-->", "", txt, flags=re.S)
        # the dated update notes of the top card are a log: "2026-10-01 (จัดกลุ่มใหม่)" etc. keep their own wording, but none may send a reader to an old bar
    return txt


@pytest.mark.parametrize("rel", DOCS_D5)
def test_no_document_sends_a_reader_by_the_old_menu(rel):
    txt = _doc_text(rel)
    hits = [(p, m.group(0)) for p in OLD_MENU for m in re.finditer(p, txt)]
    assert not hits, f"{rel}: old menu words {hits[:5]}"


def test_the_overhaul_document_has_the_department_section_and_the_d_rows():
    doc = (REPO / "docs" / "BOM_CENTER_UX_OVERHAUL.md").read_text(encoding="utf-8")
    assert "### 3.5 การนำทางตามแผนก" in doc
    for pr in ("PR-D1", "PR-D2", "PR-TF", "PR-D3", "PR-R", "PR-D6", "PR-D4", "PR-D5"):
        assert re.search(rf"\| {pr} \|", doc), pr


def test_the_sop_is_version_1_21_with_a_revision_row_and_the_page_locations():
    sop = (REPO / "docs" / "sop" / "WP-SCM-001_ingredient_order_procedure.html").read_text(encoding="utf-8")
    assert re.search(r"ฉบับที่ / วันที่มีผล</th>\s*<td>1\.21 /", sop)      # PL2: 1.21 (23 ต.ค. 2026) is the review polish (one five-step close list, the 14-slip-day rule, no "ฉบับร่าง"); 1.20 (23 ต.ค. 2026) is the everyday-words pass; 1.19 added the Excel forms; 1.18 widened the history to 8 weeks; 1.17 carried the stock-first rewrite
    assert re.search(r"<tr><td>1\.16</td><td>21 ต\.ค\. 2026</td>", sop) and re.search(r"<tr><td>1\.17</td><td>22 ต\.ค\. 2026</td>", sop) and re.search(r"<tr><td>1\.18</td><td>22 ต\.ค\. 2026</td>", sop)
    assert re.search(r"<tr><td>1\.19</td><td>23 ต\.ค\. 2026</td>", sop) and re.search(r"<tr><td>1\.20</td><td>23 ต\.ค\. 2026</td>", sop)
    assert re.search(r"<tr><td>1\.21</td><td>23 ต\.ค\. 2026</td>", sop)
    assert "ฉบับร่าง" not in re.sub(r"<tr><td>1\.\d+</td>.*?</tr>", "", sop, flags=re.S)       # PL2: the status line says รอการอนุมัติ
    assert "<h3>7.7 ปิดเดือน (5 ขั้นในโหมดสต๊อก)</h3>" in sop
    assert "ตำแหน่งของหน้าในแอป" in sop and "ปฏิบัติการ → การใช้งานทุกสาขา" in sop


# ── PL2 · the documents say what the app says about the first 14 slip days and the month close ─────────────────
def test_documents_state_the_14_slip_day_rule_and_the_five_step_close():
    for rel in ("docs/pilot/branch_guide.html", "docs/pilot/pilot_plan.html", "docs/pilot/salessupport_guide.html",
                "docs/ONBOARDING_30_BRANCHES.md", "tabs/47-manual.html", "scripts/branch_handout.py"):
        txt = (REPO / rel).read_text(encoding="utf-8")
        assert "14 วัน" in txt and ("กรอกยอดสั่งเอง" in txt or "กรอกช่อง" in txt), rel
    for rel in ("docs/pilot/salessupport_guide.html", "docs/sop/WP-SCM-001_ingredient_order_procedure.html", "tabs/47-manual.html"):
        txt = (REPO / rel).read_text(encoding="utf-8")
        assert "5 ขั้น" in txt and "ใบสรุปตัดเบิก" in txt, rel
    gl = (REPO / "docs" / "STOCK_FIRST_GOLIVE.md").read_text(encoding="utf-8")
    for pr in ("#441", "#442", "#443"):
        assert pr in gl, pr
    assert "ฉบับร่าง" not in gl
