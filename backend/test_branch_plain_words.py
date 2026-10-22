# -*- coding: utf-8 -*-
"""PR-PW (owner 2026-10-22) — everyday words for branch staff across the ordering process.

docs/BRANCH_WORDS.md is the glossary: one word per idea, the same on every screen, message, guide, card, handout and deck a
branch reads. This gate keeps it so, mechanically:
  * the branch words of the glossary are on the branch's screens (a word nobody writes any more is a gap in the apply);
  * the office words they replaced are gone from every file a branch reads. Only text a person can read is scanned: comments and
    the search keywords of the page registry (js/01-core.js PAGE_KEYWORDS, which keep the old words on purpose) are left out.
A legitimate old word goes in ALLOW as `(file, text that must appear on the line)`.
"""
import io
import re
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# every file a branch reads (screens, messages, Excel forms, guides, task cards, the handout)
BRANCH_FILES = [
    "js/14-branch.js", "js/16-supplyhome.js", "tabs/53-branchstock.html",
    "docs/pilot/branch_guide.html", "docs/pilot/cards/card_issue.html", "docs/pilot/cards/card_order.html",
    "docs/pilot/cards/card_receive.html", "docs/pilot/cards/card_count.html",
    "scripts/branch_task_cards.py", "scripts/branch_handout.py", "backend/bom_center/branch_forms.py",
]

# office word (regex) -> branch word in the glossary
REPLACED = [
    (r"ตัดรอบ", "เวลาส่งสุดท้าย"), (r"ยอดแนะนำ", "จำนวนที่ควรสั่ง"), (r"คงเหลือตามระบบ", "ของในระบบ"), (r"ปกติของสาขา", "ที่สาขาใช้ปกติ"),
    (r"ค่ากลางทุกสาขา", "เฉลี่ยทุกสาขา"), (r"ผลต่าง", "ส่วนต่าง"), (r"ต้นงวด", "นับครั้งแรก"), (r"ปลายงวด", "นับสิ้นเดือน"),
    (r"กระทบยอด", "เทียบของ"), (r"ตรวจนับ", "นับของ"), (r"สั่งสินค้า", "สั่งของ"), (r"(?<!ป)รับสินค้า", "รับของ"),
    (r"ล่าช้า", "ส่งช้า"), (r"หน่วยฐาน", "หน่วยเล็กสุด"), (r"ขนาดบรรจุ", "ขนาดแพ็ค"), (r"ประมาณการ", "ประมาณ"),
    (r"ยืนยัน", "ส่ง / แจ้งว่า…"), (r"เคลม", "แจ้งของขาด/เสีย"), (r"รอบส่ง(?!ของ)", "รอบส่งของ"), (r"เกณฑ์", "ต่างมาก / ช่วงที่ยอมรับ"),
    (r"ส่วนกลาง", "สำนักงานใหญ่"), (r"ดำเนินการ", "ทำ"), (r"งวด", "เดือน"),
]

# the branch words that must be on the screens (word -> a file that writes it)
BRANCH_WORDS = {
    "สั่งของ": "js/16-supplyhome.js", "รับของ": "js/16-supplyhome.js", "นับของสิ้นเดือน": "tabs/53-branchstock.html",
    "จำนวนที่ควรสั่ง": "js/14-branch.js", "ของในระบบ": "js/14-branch.js", "ที่สาขาใช้ปกติ": "js/14-branch.js",
    "เฉลี่ยทุกสาขา": "js/14-branch.js", "ส่วนต่าง": "js/14-branch.js", "นับครั้งแรก": "js/14-branch.js", "นับสิ้นเดือน": "js/14-branch.js",
    "เทียบของ": "js/14-branch.js", "แจ้งของขาด/เสีย": "js/14-branch.js", "ส่งใบตัดเบิก": "js/14-branch.js", "ส่งช้า": "js/14-branch.js",
    "เวลาส่งสุดท้าย": "js/14-branch.js", "รอบส่งของ": "js/14-branch.js", "ขนาดแพ็ค": "js/14-branch.js", "หน่วยเล็กสุด": "js/14-branch.js",
    "ต่างมาก": "js/14-branch.js", "แจ้งว่าบันทึกรับของครบ": "js/14-branch.js",
}

ALLOW = [
    # a file downloaded before the wording changed is still read by the importer
    ("backend/bom_center/branch_forms.py", '"รับสินค้า "'),
    ("backend/bom_center/branch_forms.py", '"สาเหตุส่วนต่าง"'),
]


def _strip_js_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return re.sub(r"(?<![:'\"`\\])//.*$", "", src, flags=re.M)


def _strip_html(src: str) -> str:
    src = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    src = re.sub(r"<style\b.*?</style>", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return _strip_js_comments(src) if "<script" in src else src


def _py_strings(src: str) -> str:
    """The string literals of a Python file, one per line (docstrings are text for the maintainer, not for the branch)."""
    out, prev = [], None
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.STRING and prev not in (tokenize.INDENT, tokenize.NEWLINE, tokenize.NL, None) or \
                (tok.type == tokenize.STRING and prev is not None and not tok.string.startswith(('"""', "'''"))):
            out.append((tok.start[0], tok.string))
        if tok.type not in (tokenize.NL, tokenize.COMMENT):
            prev = tok.type
    return "\n".join(f"{n}\t{s}" for n, s in out)


def _text(rel: str) -> str:
    src = (REPO / rel).read_text(encoding="utf-8")
    if rel.endswith(".js"):
        return _strip_js_comments(src)
    if rel.endswith(".html"):
        return _strip_html(src)
    if rel.endswith(".py"):
        return _py_strings(src)
    return src


def test_the_glossary_exists_and_names_every_branch_word():
    gl = (REPO / "docs" / "BRANCH_WORDS.md").read_text(encoding="utf-8")
    for w in BRANCH_WORDS:
        assert w in gl, f"docs/BRANCH_WORDS.md does not list {w}"
    for pat, _ in REPLACED:
        word = re.sub(r"\(\?[^)]*\)", "", pat)
        assert word in gl, f"docs/BRANCH_WORDS.md does not list the replaced word {word}"


def test_the_branch_words_are_on_the_screens():
    for w, rel in BRANCH_WORDS.items():
        assert w in _text(rel), f"{w} is not written in {rel}"


def test_the_replaced_office_words_are_gone_from_every_file_a_branch_reads():
    bad = []
    for rel in BRANCH_FILES:
        for i, ln in enumerate(_text(rel).splitlines(), 1):
            for pat, new in REPLACED:
                if re.search(pat, ln) and not any(f == rel and t in ln for f, t in ALLOW):
                    bad.append(f"{rel}:{i}: {pat} -> {new} | {ln.strip()[:120]}")
    assert not bad, "office words a branch would read:\n  " + "\n  ".join(bad[:25])


def test_the_server_words_a_branch_reads_follow_the_glossary():
    from bom_center import claims, issues, orders
    for name, table in (("issues.STATUS_TH", issues.STATUS_TH), ("orders.STATUS_TH", orders.STATUS_TH), ("claims.STATUS_TH", claims.STATUS_TH)):
        for k, v in table.items():
            assert not re.search(r"ยืนยัน|ล่าช้า|รับสินค้า|เคลม", v), f"{name}[{k}] = {v}"
    assert issues.STATUS_TH["confirmed"] == "ส่งแล้ว" and issues.STATUS_TH["late"] == "ส่งช้า"
    assert orders.STATUS_TH["received"] == "รับของแล้ว"


def test_search_keeps_the_old_words_as_keywords():
    core = (REPO / "js" / "01-core.js").read_text(encoding="utf-8")
    for key, olds in (("'branchstock|order'", ("สั่งสินค้า", "ยอดแนะนำ")), ("'branchstock|recv'", ("รับสินค้า", "เคลม")),
                      ("'branchstock|var'", ("ผลต่าง",)), ("'branchstock|count'", ("ตรวจนับ", "ต้นงวด", "ปลายงวด"))):
        line = next(ln for ln in core.splitlines() if key + ":" in ln)
        seg = line[line.index(key):]
        seg = seg[:seg.index("',", seg.index(":")) if "'," in seg[seg.index(":"):] else len(seg)]
        for o in olds:
            assert o in seg, f"{key} lost the search keyword {o}"
