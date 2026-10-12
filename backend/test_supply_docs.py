# -*- coding: utf-8 -*-
"""PR-M6 — the written guides of วัตถุดิบและใบสั่งซื้อ follow the screens.

  * the role rows of the manual (tabs/47-manual.html) list, per role, the buttons the bar really offers
    (SUPPLY_CHIPS `roles:` in js/01-core.js, read the way test_supply_chips_roles.py reads it) and nothing else;
  * the guides of docs/pilot are written for a phone: a 16 px base, a viewport, and tasks that fit one screen;
  * every bold label of a guide is a word that exists on a screen (js/, tabs/, the Thai strings of backend/bom_center),
    so a renamed button breaks this test instead of the guide;
  * every guide is reachable from the manual or the onboarding document, and the handout texts say what the guides say.
"""
import re
from pathlib import Path

import pytest

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


def test_the_manual_has_one_row_per_role_and_each_lists_exactly_its_bar():
    rows = _manual_rows()
    assert set(rows) == ROLE_ROWS
    for role, row in rows.items():
        # the first row of the bar the role really gets
        assert row["chips"] == T._level1(role), f"{role}: manual {row['chips']} != bar {T._level1(role)}"
        # every page named under a button is a page of that role's bar (nothing the role cannot open)
        bar_pages = {p for items in T._bar(role).values() for p in items}
        stray = [p for p in row["pages"] if p not in bar_pages]
        assert not stray, f"{role}: pages the bar does not offer: {stray}"


def test_every_chip_and_page_label_of_the_manual_exists_in_supply_chips():
    chips = T._chips()
    labels = {c["label"] for c in chips} | {i["label"] for c in chips for i in c["items"]}
    for role, row in _manual_rows().items():
        for lbl in row["chips"] + row["pages"]:
            assert lbl in labels, f"{role}: '{lbl}' is not a label of SUPPLY_CHIPS"


def test_a_role_that_has_a_bar_names_each_of_its_pages_in_the_manual():
    """Whatever a role can open under a button appears in its row (analyst and management: everything but the older pages)."""
    rows = _manual_rows()
    assert "ทุกหน้าเหมือนแถวของฝ่ายวิเคราะห์" in MANUAL          # the manager holds every page: one sentence instead of 23 names
    for role in ("branch", "warehouse", "salessupport", "operation", "analyst", "innovation", "accounting"):
        bar_pages = {p for chip, items in T._bar(role).items() for p in items if p != T.HOME and p not in T.LEGACY}
        named = set(rows[role]["pages"]) | set(rows[role]["chips"])
        # a flat bar or a one-page button is named by its page; the pages inside a chip are listed under it
        missing = bar_pages - named
        assert not missing, f"{role}: the row does not name {sorted(missing)}"


def test_the_owner_guide_is_linked_from_the_manual_and_the_owner_row_names_the_page():
    assert "/docs/pilot/owner_guide.html" in MANUAL
    row = re.search(r'<tr[^>]*data-role="franchise"[^>]*>(.*?)</tr>', MANUAL, re.S).group(1)
    assert "ร้านของฉัน" in row and 'class="chip"' not in row                 # one page, no bar
    assert (PILOT / "owner_guide.html").exists()


def test_the_close_has_nine_steps_in_the_manual():
    block = re.search(r'id="manualCloseSteps".*?</ol>', MANUAL, re.S).group(0)
    assert len(re.findall(r"<li>", block)) == 9 and "9 ขั้นตอน" in MANUAL


def test_the_branch_flow_of_the_manual_names_the_phone_screens():
    block = re.search(r'id="manualBranchFlow".*?</ol>', MANUAL, re.S).group(0)
    for w in ("ยืนยันใบตัดเบิก", "ส่งใบสั่งซื้อ", "รับครบ", "มีรายการไม่ครบ", "บันทึกรับสินค้า", "แจ้งเคลม", "ส่งผลตรวจนับ", "ยืนยันรับสินค้าครบ"):
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
    for w in ("ใบตัดเบิก", "ยืนยันใบตัดเบิกของวันนี้ก่อน", "13:15", "รับครบ", "มีรายการไม่ครบ", "ส่งใบสั่งซื้อ", "ยืนยัน", "ตัดเบิก · สั่งสินค้า · รับสินค้า · ผลต่าง", "ตรวจนับ"):
        assert w in txt, w
    for w in ("ปล่อย", "token", "Sales Support", "ส่วนกลาง"):
        assert w not in txt, w
    card = HO.card("อุดรธานี", "wh_udon", "PW-X")
    assert "ตัดเบิก" in card and "ยืนยัน" in card and "รับครบ" in card


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
