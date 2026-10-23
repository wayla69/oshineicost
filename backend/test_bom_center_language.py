# -*- coding: utf-8 -*-
"""PR-M1 — the language gates of the วัตถุดิบและใบสั่งซื้อ module.

The glossary and the verb set of docs (plans/bom-ux-overhaul-phase3.md) are enforced here, mechanically:
what a person reads on these pages must not carry the engineer words, the department-name variants, the
raw statuses or the wrong facts the language audit found. Only text a person can read is scanned:
  * JS    — the non-comment lines that hold Thai text (identifiers such as `r.v0_cost` are skipped by the
            patterns, not by hand);
  * HTML  — the same, with <!-- comments --> and <style> blocks removed;
  * Python — the string literals that hold Thai text (docstrings and comments are not user-facing).
A legitimate hit goes into backend/language_allow.txt as `relative/path|text that must appear on the line`.

Also here: the 7 wrong facts of the audit are pinned, and the display-name / date helpers are exercised.
"""
import ast
import re
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
ALLOW_FILE = Path(__file__).resolve().parent / "language_allow.txt"
THAI = re.compile("[฀-๿]")

JS_FILES = ["js/01-core.js", "js/13-bom-center.js", "js/14-branch.js", "js/15-orderhub.js", "js/16-supplyhome.js"]
TAB_FILES = ["tabs/07-ordering.html", "tabs/08-ordersetup.html", "tabs/47-manual.html", "tabs/51-ingpurchase.html",
             "tabs/52-bomcenter.html", "tabs/53-branchstock.html", "tabs/54-orderhub.html", "tabs/55-supplyhome.html"]
DOC_FILES = sorted(str(p.relative_to(REPO)).replace("\\", "/") for p in (REPO / "docs" / "pilot").glob("*.html"))     + sorted(str(p.relative_to(REPO)).replace("\\", "/") for p in (REPO / "docs" / "pilot" / "cards").glob("*.html"))     # batch 3: the task cards
PY_FILES = sorted(str(p.relative_to(REPO)).replace("\\", "/") for p in (REPO / "backend" / "bom_center").glob("*.py")) \
    + ["backend/routers/bom_center.py", "scripts/branch_handout.py", "scripts/branch_task_cards.py"]

# files read by the branch, the warehouse and the centre's desks — no analyst vocabulary there
FLOOR_FILES = ["js/14-branch.js", "js/15-orderhub.js", "js/16-supplyhome.js", "tabs/53-branchstock.html", "tabs/54-orderhub.html",
               "tabs/55-supplyhome.html", "docs/pilot/branch_guide.html", "docs/pilot/warehouse_guide.html",
               "docs/pilot/salessupport_guide.html", "docs/pilot/accounting_guide.html", "docs/pilot/owner_guide.html",
               "docs/pilot/cards/card_issue.html", "docs/pilot/cards/card_order.html", "docs/pilot/cards/card_receive.html", "docs/pilot/cards/card_count.html"]

# (name, regex, scope) — scope None = every scanned file, "floor" = FLOOR_FILES only
RULES = [
    ("draft in parentheses", r"\(draft\)", None),
    ("release ที่", r"release ที่", None),
    ("v0", r"(?<![\w.])v0(?![\w])", None),
    ("COST-RW", r"COST-RW", None),
    ("Cost-RW on a floor page", r"Cost-RW", "floor"),
    ("DATAP on a floor page", r"DATAP", "floor"),
    ("sheet", r"(?<![\w.\-/])sheet(?![\w\-.])", None),
    ("template", r"(?<![\w.\-/])template(?![\w\-.])", None),
    ("forbidden", r"(?<![\w.])forbidden(?![\w])", None),
    ("rehearsal", r"(?i)rehearsal", None),
    ("Sales Support", r"Sales Support", None),
    ("Ops", r"(?<![\w.])Ops(?![\w])", None),
    ("management as a department", r"(?<![\w.'\"])management(?![\w'\":])", None),
    ("ดำเนินการแล้ว", r"ดำเนินการแล้ว", None),
    ("ดำเนินการไม่สำเร็จ", r"ดำเนินการไม่สำเร็จ", None),
    ("ระบบจะ", r"ระบบจะ", None),
    ("12 ข้อ", r"12 ข้อ", None),
    ("ขั้นตอน 1–6 / 1–7", r"ขั้นตอน(?:ที่)? ?1[–-][67]", None),
    ("ปลดล็อกการบันทึก", r"ปลดล็อกการบันทึก", None),
    ("ปล่อย (the release is ประกาศใช้)", r"ปล่อย", None),
    ("ฉบับร่าง", r"ฉบับร่าง", None),
    ("Owner Write-Token / token", r"(?i)(?<![\w.\-])(?:owner write-)?token(?![\w\-.])", None),
    ("ซัพพลายเออร์", r"ซัพพลายเออร์", None),
    ("จัดซื้อในพื้นที่", r"จัดซื้อในพื้นที่", None),
    ("ฝ่ายขาย", r"ฝ่ายขาย", None),
    ("ชิป", r"ชิป", None),
    ("BoM written in English", r"(?<![\w.])BoM(?![\w])", None),
    ("COGS", r"COGS", None),
    ("Tier on a floor page", r"(?<![\w.])Tier(?![\w])", "floor"),
    ("คงเหลือ (ปลายงวด)", r"คงเหลือ \(ปลายงวด\)", None),
    ("ส่วนกลาง for a team", r"(?<!บัญชีสินค้า)(?<!สินค้า)(?<!รายงาน)(?<!รหัส)ส่วนกลาง", None),
    ("กรุณา in a status or field message", r"กรุณา", None),
    # batch 1 (2026-10-12): the cutoff is "ส่งใบสั่งซื้อภายใน …" everywhere; the floor reads the value class as a group, never the
    # analyst's unit-rule word, and cancels (not deletes) a receipt
    ("สั่งภายใน / ส่งภายใน (it is ส่งใบสั่งซื้อภายใน)", r"(?<!ยอด)(?:สั่ง|ส่ง)ภายใน", None),
    ("กฎหน่วย on a floor page", r"กฎหน่วย", "floor"),
    ("ชั้น A/B/C on a floor page", r"ชั้น ?[ABC](?![A-Za-z])", "floor"),
    ("ลบใบรับ (it is ยกเลิกใบรับสินค้า)", r"ลบใบรับ", None),
]


def _allow():
    out = []
    if ALLOW_FILE.exists():
        for ln in ALLOW_FILE.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "|" in ln:
                f, t = ln.split("|", 1)
                out.append((f.strip(), t.strip()))
    return out


def _strip_js_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return re.sub(r"(?<![:'\"`\\])//.*$", "", src, flags=re.M)


def _strip_html(src: str) -> str:
    src = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    src = re.sub(r"<style\b.*?</style>", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    src = re.sub(r"(</?)template\b", r"\1tpl", src)           # PR-D6: the <template> tag of a help text is markup, not the English word on a screen
    return _strip_js_comments(src) if "<script" in src else src


# js/01-core.js and the manual hold the whole app; only the parts of this module are scanned
CORE_REGIONS = [("const ROLE_START", "async function maybeShowStartCard"), ("const ROLE_LABEL_TH", "const MODULE_LABEL_TH"),
                ("'supply.ordering':'", "'customer.feedback'"), ("window.uiDialog = function", "window.askWriteToken"),
                ("function _renderLockIcon", "let _sessionExpiredShown"), ("const _SUPPLY_CENTRE", "const TAB_CLUSTERS")]
MANUAL_HINT = re.compile(r"วัตถุดิบและใบสั่งซื้อ|สาขาของฉัน|ใบสั่งซื้อ|ใบตัดเบิก|ปิดงวด|ตรวจนับสิ้นเดือน|สูตรกึ่งสำเร็จ|ประสานงานขาย|คลัง")


def _thai_lines(rel: str):
    """(line_no, text) of the lines a person can read."""
    src = (REPO / rel).read_text(encoding="utf-8")
    src = _strip_html(src) if rel.endswith(".html") else _strip_js_comments(src)
    keep = None
    if rel == "js/01-core.js":
        keep = set()
        for a, b in CORE_REGIONS:
            i = src.find(a)
            j = src.find(b, i + 1) if i >= 0 else -1
            assert i >= 0 and j > i, f"region markers moved in {rel}: {a!r}"
            keep |= set(range(src[:i].count("\n") + 1, src[:j].count("\n") + 2))
    for i, ln in enumerate(src.splitlines(), 1):
        if THAI.search(ln) and (keep is None or i in keep) and (rel != "tabs/47-manual.html" or MANUAL_HINT.search(ln)):
            yield i, ln


def _py_literals(rel: str):
    """(line_no, text) of the Thai-bearing string literals of a Python file, docstrings excluded."""
    tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
    skip = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = getattr(n, "body", [])
            if b and isinstance(b[0], ast.Expr) and isinstance(getattr(b[0], "value", None), ast.Constant) \
                    and isinstance(b[0].value.value, str):
                skip.add(id(b[0].value))
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip and THAI.search(n.value):
            yield n.lineno, n.value


def _scan():
    allow = _allow()
    hits = []

    def check(rel, ln_no, text):
        floor = rel in FLOOR_FILES
        for name, rx, scope in RULES:
            if scope == "floor" and not floor:
                continue
            if re.search(rx, text) and not any(f == rel and t in text for f, t in allow):
                hits.append(f"{rel}:{ln_no}: [{name}] {text.strip()[:140]}")

    for rel in JS_FILES + TAB_FILES + DOC_FILES:
        for ln_no, text in _thai_lines(rel):
            check(rel, ln_no, text)
    for rel in PY_FILES:
        for ln_no, text in _py_literals(rel):
            check(rel, ln_no, text)
    return hits


def test_no_forbidden_words_in_the_module():
    hits = _scan()
    assert not hits, f"{len(hits)} line(s) with a word the glossary forbids (add a legitimate one to backend/language_allow.txt):\n" \
        + "\n".join(hits[:60])


# ── the 7 wrong facts of the language audit, pinned ────────────────────────────────────────────────────
def test_fact_check_count_is_not_written_down():
    """The number of release checks is not repeated in text (it was 12 while checks.CHECKS holds 14 + UNM)."""
    from bom_center import release as rl
    assert sum(1 for k in rl.CHECKS if k.startswith("C")) == 14
    for rel in ("js/13-bom-center.js", "backend/bom_center/todo.py", "tabs/47-manual.html"):
        assert "12 ข้อ" not in (REPO / rel).read_text(encoding="utf-8")


def test_fact_close_has_nine_steps_and_the_texts_say_so():
    from bom_center import todo
    src = (REPO / "backend" / "bom_center" / "todo.py").read_text(encoding="utf-8")
    seqs = sorted({int(m) for m in re.findall(r'per_branch\("[a-z_]+", (\d+),', src)} | {1, 2, 3, 9})
    assert seqs == list(range(1, 10))
    manual = (REPO / "tabs" / "47-manual.html").read_text(encoding="utf-8")
    assert "โหมดเต็มระบบมี 9 ขั้น" in manual and "ปิดงวดเดือนในโหมดสต๊อกมี 5 ขั้น" in manual     # PL1/PL2: nine steps in the full mode, one list of five in the stock mode
    assert not re.search(r"ขั้นตอนที่? ?1[–-][67]", manual)
    assert todo.CLOSE_TOLERANCE_DAYS == 2


def test_fact_issue_summary_header_names_the_counted_stock():
    from routers import bom_center as r
    assert r.ISSUE_HEADERS[6] == "นับจริง (ปลายงวด)"
    assert "คงเหลือ (ปลายงวด)" not in r.ISSUE_HEADERS and "นับจริง (สิ้นเดือน)" in r.RECON_HEADERS      # the branch's own file says สิ้นเดือน; the accountant's keeps ปลายงวด


def test_fact_release_statuses_are_thai():
    from bom_center import release as rl
    assert rl.STATUS_TH == {"draft": "ร่าง", "review": "รอตรวจ", "released": "ประกาศใช้แล้ว", "retired": "ยกเลิกใช้"}


def test_fact_pilot_readiness_does_not_name_a_vanished_menu():
    src = (REPO / "backend" / "bom_center" / "pilot.py").read_text(encoding="utf-8")
    assert "BoM Center →" not in src and "ภาพรวม →" not in src


def test_fact_every_start_card_entry_of_salessupport_is_rendered():
    js = (REPO / "js" / "01-core.js").read_text(encoding="utf-8")
    assert ".slice(0, 5)" in js and ".slice(0, 3)" not in js.split("function showStartCard")[1].split("function dismissStartCard")[0]


def test_fact_na_has_one_label():
    a = (REPO / "js" / "15-orderhub.js").read_text(encoding="utf-8")
    b = (REPO / "js" / "16-supplyhome.js").read_text(encoding="utf-8")
    # ศูนย์สั่งของ keeps "ไม่มีงาน"; หน้างาน (PR-TF) has no status chip any more: a task of status na is เสร็จแล้ว there (state done)
    assert "na: ['ไม่มีงาน'" in a and "st === 'done' || st === 'na' ? 'done'" in b and "done: 'เสร็จแล้ว'" in b


# ── display names, dates ────────────────────────────────────────────────────────────────────────────────
def test_display_name_falls_back_to_the_branch_or_role_label(tmp_path):
    import auth
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    auth.init_auth(con)
    for u, role, br, dn in (("branch1", "branch", "อุดรธานี", None), ("salessupport1", "salessupport", None, None),
                            ("a1", "analyst", None, "คุณนุ่น")):
        con.execute("INSERT INTO app_user(username, pw_hash, pw_salt, role, branch, display_name) VALUES(?,?,?,?,?,?)",
                    (u, "x", "y", role, br, dn))
    assert auth.display_name_for(con, "branch1") == "สาขาอุดรธานี"
    assert auth.display_name_for(con, "salessupport1") == "ประสานงานขาย"
    assert auth.display_name_for(con, "a1") == "คุณนุ่น"
    assert auth.display_name_for(con, "ghost") is None
    names = auth.names_map(con)
    assert names["branch1"] == "สาขาอุดรธานี" and names["a1"] == "คุณนุ่น"
    auth.update_user(con, "a1", display_name="")
    assert auth.display_name_for(con, "a1") == "ฝ่ายวิเคราะห์"


def test_server_side_date_and_name_helpers():
    from bom_center import fmt
    assert fmt.day_th("2026-10-09") == "9/10/2026"
    assert fmt.day_short_th("2026-10-09") == "ศ. 9/10"
    assert fmt.month_th("2026-09") == "กันยายน 2026"
    assert fmt.at_th("2026-10-09T13:15:42+07:00") == "9/10/2026 13:15"
    assert fmt.day_th(None) == "" and fmt.month_th("") == ""
    assert fmt.who(None, "") == "-" and fmt.who(None, "owner") == "owner"


def test_js_helpers_exist_and_every_bom_call_passes_a_specific_title():
    core = (REPO / "js" / "01-core.js").read_text(encoding="utf-8")
    for fn in ("function fmtDay(", "function fmtDayShort(", "function fmtMonthTh(", "function fmtAt(", "function whoTh("):
        assert fn in core, fn
    assert "o.ok != null && o.okText == null" in core           # uiConfirm: {title, ok, cancel}
    for rel in ("js/13-bom-center.js", "js/14-branch.js", "js/15-orderhub.js"):
        src = (REPO / rel).read_text(encoding="utf-8")
        for m in re.finditer(r"uiConfirm\(", src):
            tail = src[m.start(): m.start() + 1400]
            window = tail.split("\n\n")[0]
            assert "title:" in window or "ask," in window, f"{rel}: uiConfirm without a specific title near {src[:m.start()].count(chr(10)) + 1}"
