#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create the branch logins of the 30-branch rollout from the branch list (PR-I).

    py -3.12 scripts\\branch_accounts.py --list D:\\onboarding\\branches.xlsx                  # dry run (default): prints the plan
    py -3.12 scripts\\branch_accounts.py --list D:\\onboarding\\branches.xlsx --yes            # creates the logins on the live site
    py -3.12 scripts\\branch_accounts.py --list ... --base http://127.0.0.1:8046 --yes         # a local test server

The list is the same file branch_handout.py reads (columns สาขา · ชื่อผู้ใช้ · LINE/ผู้รับ; a blank
username becomes branch_<branch id>; the four pilot branches keep their existing names).

What it does, per row:
  1. checks the branch name against the system's active branch list (GET /api/branches);
  2. skips a row whose login already exists, or whose branch already has a login (a second login for
     the same branch is never created; the existing one is reported);
  3. otherwise POST /api/auth/users {role: branch, branch: <name>} WITHOUT a password, so the server
     gives the shared default password and forces a change at first sign-in. The password is not
     read, sent or printed by this script (branch_handout.py puts it on the cards).
409 from the server counts as "already exists". Writes accounts_result.csv next to the list (only with --yes).

Authentication: user administration is Management's. The script signs in as a management user (the
account the owner uses in the browser): name from --admin-user / OSHINEI_ADMIN_USER, password from the
environment variable OSHINEI_ADMIN_PASSWORD, or typed at a prompt (hidden). The owner write-token
(OSHINEI_SEED_TOKEN, backend\\.env or backend\\.seed_tokens.txt, found the way backup_owner_tables.py
finds it) is also sent as X-Upload-Token when one exists; it is never printed.
Stdlib only apart from branch_list.py (and openpyxl when the list is an .xlsx).

Exit code: 0 = every row created or already there, 1 = at least one row failed, 2 = could not start.
"""
from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import branch_list as BL  # noqa: E402
from backup_owner_tables import DEFAULT_BASE, resolve_token  # noqa: E402  (the same token lookup the nightly backup uses)


class Api:
    """A cookie-keeping JSON client. The seam the tests replace."""

    def __init__(self, base: str, token: str = "", timeout: int = 60):
        self.base, self.token, self.timeout = base.rstrip("/"), token, timeout
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, method: str, path: str, body=None):
        """-> (status, parsed JSON or None, raw text). Never raises on an HTTP error status."""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json", "User-Agent": "Oshinei branch_accounts"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.token and method != "GET":
            headers["X-Upload-Token"] = self.token
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                status, text = r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status, text = e.code, e.read().decode("utf-8", "replace")
        try:
            return status, json.loads(text), text
        except ValueError:
            return status, None, text


def _detail(status, js, text) -> str:
    d = js.get("detail") if isinstance(js, dict) else None
    if isinstance(d, dict):
        d = d.get("message")
    return f"{status} {str(d or text)[:120]}".strip()


def sign_in(api, user: str, password: str) -> None:
    """Sign in unless the server runs without logins (a local test server). Exits 2 on a refusal."""
    st, js, _ = api.request("GET", "/api/auth/me")
    if st == 200 and isinstance(js, dict) and js.get("enabled") is False:
        return
    st, js, text = api.request("POST", "/api/auth/login", {"username": user, "password": password})
    if st != 200:
        raise SystemExit(f"เข้าสู่ระบบด้วย {user} ไม่สำเร็จ ({_detail(st, js, text)})")
    st, js, _ = api.request("GET", "/api/auth/me")
    if not (isinstance(js, dict) and js.get("authenticated")):
        raise SystemExit("เข้าสู่ระบบแล้วแต่ระบบไม่ยืนยันตัวตน")
    if js.get("must_change"):
        raise SystemExit(f"บัญชี {user} ยังใช้รหัสผ่านเริ่มต้น กรุณาเข้าสู่ระบบทางเบราว์เซอร์และตั้งรหัสผ่านใหม่ก่อน")


def plan_rows(rows: list, branches: dict, users: list) -> list:
    """Decide each row: -> [{branch, username, recipient, action: create|exists|fail, note}]. Pure, so it is tested without a server."""
    by_user = {u["username"]: u for u in users}
    by_branch = {}
    for u in users:
        if u.get("role") == "branch" and u.get("active", 1) and u.get("branch"):
            by_branch.setdefault(u["branch"], []).append(u["username"])
    out, taken = [], set()
    for r in rows:
        name = r["branch"]
        row = {"branch": name, "username": r["username"], "recipient": r.get("recipient", ""), "action": "fail", "note": ""}
        out.append(row)
        if name not in branches:
            row["note"] = "ไม่พบสาขานี้ในรายชื่อสาขาที่เปิดใช้งาน ตรวจชื่อให้ตรงกับระบบ"
            continue
        have = by_branch.get(name, [])
        wanted = r["username"] or BL.username_for(name, branches[name])
        if wanted and wanted in by_user:
            if by_user[wanted].get("branch") != name or by_user[wanted].get("role") != "branch":
                row["username"], row["note"] = wanted, "ชื่อผู้ใช้นี้ถูกใช้กับบัญชีอื่นแล้ว"
                continue
            row.update(username=wanted, action="exists", note="มีบัญชีนี้อยู่แล้ว")
            continue
        if have:
            row.update(username=have[0], action="exists", note="สาขานี้มีบัญชีอยู่แล้ว ไม่สร้างบัญชีที่สอง")
            continue
        if not wanted:
            row["note"] = "ไม่ทราบรหัสสาขา จึงตั้งชื่อผู้ใช้ไม่ได้"
            continue
        if wanted in taken:
            row["username"], row["note"] = wanted, "ชื่อผู้ใช้ซ้ำกับสาขาอื่นในรายชื่อ"
            continue
        taken.add(wanted)
        row.update(username=wanted, action="create", note="")
    return out


def run(api, rows: list, apply: bool) -> list:
    """Fetch the branch list and the users, decide, and (apply=True) create. -> rows with `result`."""
    st, js, text = api.request("GET", "/api/branches")
    if st != 200 or not isinstance(js, list):
        raise SystemExit(f"อ่านรายชื่อสาขาไม่ได้ ({_detail(st, js, text)})")
    branches = {b["name"]: b["id"] for b in js if b.get("active", True)}
    st, js, text = api.request("GET", "/api/auth/users")
    if st != 200 or not isinstance(js, list):
        raise SystemExit(f"อ่านรายชื่อผู้ใช้ไม่ได้ ({_detail(st, js, text)}) ต้องเข้าสู่ระบบด้วยบัญชี Management")
    plan = plan_rows(rows, branches, js)
    for p in plan:
        if p["action"] == "fail":
            p["result"] = BL.R_FAILED
        elif p["action"] == "exists":
            p["result"] = BL.R_EXISTS
        elif not apply:
            p["result"] = BL.R_PLAN
        else:
            st, js, text = api.request("POST", "/api/auth/users", {"username": p["username"], "role": "branch", "branch": p["branch"]})
            if st == 200:
                p["result"] = BL.R_CREATED
            elif st == 409:
                p["result"], p["note"] = BL.R_EXISTS, "มีบัญชีนี้อยู่แล้ว"
            else:
                p["result"], p["note"] = BL.R_FAILED, _detail(st, js, text)
    return plan


def print_table(plan: list) -> None:
    w = max([len(p["branch"]) for p in plan] + [6])
    print(f"{'สาขา':<{w}}  {'ชื่อผู้ใช้':<22}  ผล")
    for p in plan:
        print(f"{p['branch']:<{w}}  {p['username'] or '-':<22}  {p['result']}" + (f"  ({p['note']})" if p["note"] else ""))
    n = {k: sum(1 for p in plan if p["result"] == k) for k in (BL.R_CREATED, BL.R_PLAN, BL.R_EXISTS, BL.R_FAILED)}
    print(f"\nสรุป: สร้างแล้ว {n[BL.R_CREATED]} · จะสร้าง {n[BL.R_PLAN]} · มีอยู่แล้ว {n[BL.R_EXISTS]} · ไม่สำเร็จ {n[BL.R_FAILED]} · รวม {len(plan)} สาขา")


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", required=True, help="รายชื่อสาขา (.xlsx หรือ .csv)")
    ap.add_argument("--base", default=DEFAULT_BASE, help=f"ที่อยู่ระบบ (ค่าเริ่มต้น {DEFAULT_BASE})")
    ap.add_argument("--yes", action="store_true", help="สร้างบัญชีจริง (ไม่ระบุ = แสดงแผนเท่านั้น)")
    ap.add_argument("--dry-run", action="store_true", help="แสดงแผนโดยไม่สร้างบัญชี (ค่าเริ่มต้น)")
    ap.add_argument("--admin-user", default=os.environ.get("OSHINEI_ADMIN_USER", "management1"),
                    help="บัญชี Management ที่ใช้เข้าสู่ระบบ (หรือ OSHINEI_ADMIN_USER)")
    ap.add_argument("--token-env", default="OSHINEI_SEED_TOKEN", help="ชื่อตัวแปรที่เก็บ write token")
    a = ap.parse_args(argv)
    if a.yes and a.dry_run:
        print("ใช้ --yes และ --dry-run พร้อมกันไม่ได้", file=sys.stderr)
        return 2
    try:
        rows = BL.read_list(a.list)
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 2
    print(f"{'สร้างบัญชีจริง' if a.yes else 'แผน (dry run) ไม่มีการสร้างบัญชี'} · {len(rows)} สาขา · {a.base}")
    pw = os.environ.get("OSHINEI_ADMIN_PASSWORD") or ""
    api = Api(a.base, resolve_token(a.token_env))
    st, js, _ = api.request("GET", "/api/auth/me")
    if not (st == 200 and isinstance(js, dict) and js.get("enabled") is False):
        if not pw:
            pw = getpass.getpass(f"รหัสผ่านของ {a.admin_user}: ")
    try:
        sign_in(api, a.admin_user, pw)
        plan = run(api, rows, apply=a.yes)
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 2
    print_table(plan)
    if a.yes:
        out = BL.write_result(Path(a.list).with_name("accounts_result.csv"), plan)
        print(f"เขียนผลลัพธ์ที่ {out}")
        print("ขั้นต่อไป: py -3.12 scripts\\branch_handout.py --list " + f'"{out}"')
    else:
        print("นี่คือแผนเท่านั้น ถ้าถูกต้องให้รันซ้ำพร้อม --yes")
    return 1 if any(p["result"] == BL.R_FAILED for p in plan) else 0


if __name__ == "__main__":
    raise SystemExit(main())
