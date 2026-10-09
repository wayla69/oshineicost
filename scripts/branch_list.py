# -*- coding: utf-8 -*-
"""The branch list the onboarding scripts share (PR-I, 30-branch rollout).

One row per branch, in an Excel or CSV file:

    สาขา        the branch name exactly as the system shows it (required)
    ชื่อผู้ใช้     the login name; blank = generated (branch_<branch id>)
    LINE/ผู้รับ   who receives the handout (a LINE group, a person) - optional

`branch_accounts.py` creates the logins from it and writes accounts_result.csv (same columns plus the
outcome); `branch_handout.py` reads the same file, or that result file, to write the messages and cards.
No password and no token is ever part of a list.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

H_BRANCH, H_USER, H_RECIP, H_RESULT = "สาขา", "ชื่อผู้ใช้", "LINE/ผู้รับ", "ผล"
HEADERS = [H_BRANCH, H_USER, H_RECIP]
RESULT_HEADERS = HEADERS + [H_RESULT, "หมายเหตุ"]
USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,39}$")

# the four pilot logins created on 2026-10-07; they keep their names
PILOT_USERNAMES = {
    "อุบลราชธานี": "branch_ubon",
    "อุดรธานี": "branch_udon",
    "ขอนแก่น": "branch_khonkaen",
    "ระยอง": "branch_rayong",
}

# result labels written by branch_accounts.py (column ผล)
R_CREATED, R_EXISTS, R_FAILED, R_PLAN = "สร้างแล้ว", "มีอยู่แล้ว", "ไม่สำเร็จ", "จะสร้าง"

_ALIASES = {
    "branch": ("สาขา", "branch", "ชื่อสาขา"),
    "user": ("ชื่อผู้ใช้", "username", "user", "login"),
    "recip": ("line/ผู้รับ", "line", "ผู้รับ", "recipient", "ผู้รับ/line"),
    "result": ("ผล", "result", "status"),
}


def _norm(s) -> str:
    return re.sub(r"[\s_\-.·:()\[\]*]+", "", str(s or "").strip().lower())


def _grid(path: Path) -> list:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"ไม่พบไฟล์รายชื่อสาขา: {p}")
    if p.suffix.lower() in (".csv", ".txt"):
        raw = p.read_bytes()
        for enc in ("utf-8-sig", "utf-8", "cp874"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise SystemExit("อ่านไฟล์ CSV ไม่ได้ (ต้องเป็น UTF-8)")
        return [list(r) for r in csv.reader(text.splitlines())]
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise SystemExit("ต้องติดตั้ง openpyxl เพื่ออ่านไฟล์ Excel (py -3.12 -m pip install openpyxl) หรือใช้ไฟล์ .csv")
    wb = load_workbook(p, read_only=True, data_only=True)
    return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]


def read_list(path) -> list:
    """-> [{branch, username, recipient, result, line}] in file order. Raises SystemExit with a plain Thai message."""
    grid = _grid(path)
    head_at, cols = -1, {}
    for i, row in enumerate(grid[:15]):
        cells = [_norm(c) for c in row]
        m = {}
        for k, al in _ALIASES.items():
            for j, c in enumerate(cells):
                if c and c in [_norm(a) for a in al] and j not in m.values():
                    m[k] = j
                    break
        if "branch" in m:
            head_at, cols = i, m
            break
    if head_at < 0:
        raise SystemExit(f"ไม่พบหัวตารางในไฟล์ (ต้องมีคอลัมน์ “{H_BRANCH}”) กรุณาใช้แม่แบบจาก branch_handout.py --template")
    out, seen, problems = [], {}, []
    for i, row in enumerate(grid[head_at + 1:], start=head_at + 2):
        def cell(k):
            j = cols.get(k)
            return str(row[j]).strip() if j is not None and j < len(row) and row[j] is not None else ""
        branch = cell("branch")
        if not branch:
            continue
        user = cell("user")
        if user and not USER_RE.match(user):
            problems.append(f"แถวที่ {i}: ชื่อผู้ใช้ “{user}” ใช้ได้เฉพาะ a-z 0-9 _ . - ยาว 3–40 ตัวอักษร")
        if branch in seen:
            problems.append(f"แถวที่ {i}: สาขา “{branch}” ซ้ำกับแถวที่ {seen[branch]}")
        seen[branch] = i
        out.append({"branch": branch, "username": user, "recipient": cell("recip"), "result": cell("result"), "line": i})
    names = [r["username"] for r in out if r["username"]]
    for u in {u for u in names if names.count(u) > 1}:
        problems.append(f"ชื่อผู้ใช้ “{u}” ซ้ำในหลายสาขา")
    if problems:
        raise SystemExit("ไฟล์รายชื่อสาขามีข้อผิดพลาด:\n  " + "\n  ".join(problems))
    if not out:
        raise SystemExit("ไฟล์รายชื่อสาขาไม่มีรายการ")
    return out


def username_for(branch: str, branch_id=None):
    """The login name for a branch whose list row leaves it blank: the pilot branch keeps its existing name,
    every other branch gets branch_<branch id>. None when the id is not known (the caller must say so:
    branch names are Thai, so there is no ASCII name to fall back on, and two scripts guessing differently
    would hand a branch a login that does not exist)."""
    if branch in PILOT_USERNAMES:
        return PILOT_USERNAMES[branch]
    if branch_id is not None:
        return f"branch_{int(branch_id)}"
    return None


def write_template(path) -> Path:
    """An example list: the four pilot branches filled in, a note row on top."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "รายชื่อสาขา"
    ws.append(["กรอกหนึ่งสาขาต่อหนึ่งแถว · ชื่อสาขาต้องตรงกับที่ระบบแสดง · เว้นชื่อผู้ใช้ว่างได้ ระบบจะตั้งเป็น branch_<รหัสสาขา> · "
               "ช่อง LINE/ผู้รับใช้จดว่าจะส่งข้อความให้ใคร (ไม่บังคับ) · ห้ามใส่รหัสผ่านในไฟล์นี้"])
    ws["A1"].font = Font(italic=True, color="5F6C80")
    ws.append(HEADERS)
    for c in ws[2]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A6B")
    for name, user in PILOT_USERNAMES.items():
        ws.append([name, user, ""])
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 30
    ws.freeze_panes = "A3"
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    wb.save(p)
    return p


def write_result(path, rows) -> Path:
    """accounts_result.csv: the list columns plus the outcome (UTF-8 with BOM so Excel opens Thai text)."""
    p = Path(path)
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(RESULT_HEADERS)
        for r in rows:
            w.writerow([r.get("branch", ""), r.get("username", ""), r.get("recipient", ""), r.get("result", ""), r.get("note", "")])
    return p
