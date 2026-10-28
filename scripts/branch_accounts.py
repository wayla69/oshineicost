#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create the branch logins of the 30-branch rollout from the branch list (PR-I).

    py -3.12 scripts\\branch_accounts.py --list D:\\onboarding\\branches.xlsx                  # dry run (default): prints the plan
    py -3.12 scripts\\branch_accounts.py --list D:\\onboarding\\branches.xlsx --yes            # creates the logins on the live site
    py -3.12 scripts\\branch_accounts.py --list ... --base http://127.0.0.1:8046 --yes         # a local test server
    py -3.12 scripts\\branch_accounts.py --list ... --replace-logins                          # plan: new short logins, old ones off
    py -3.12 scripts\\branch_accounts.py --list ... --replace-logins --yes                    # run 1 creates, run 2 retires

The list is the same file branch_handout.py reads (columns สาขา · ตัวย่อ · ชื่อผู้ใช้ · LINE/ผู้รับ). The
login of a branch is wh_<ตัวย่อ> (a blank ชื่อผู้ใช้ becomes that; a branch with no abbreviation gets
wh_<branch id>). The abbreviation is the company's 3-letter branch code (owner 2026-10-27: cnx ubn udn kr1 srn hat
kkn ...); the five pilot branches (cnx ubn udn kr1 srn) have theirs built in.

What it does, per row:
  1. checks the branch name against the system's active branch list (GET /api/branches);
  2. skips a row whose login already exists, or whose branch already has a login (a second login for
     the same branch is never created; the existing one is reported) - unless --replace-logins / --replace-pilot;
  3. otherwise POST /api/auth/users {role: branch, branch: <name>} WITHOUT a password, so the server
     gives the login its own random temporary password and forces a change at first sign-in. The
     server answers that password once; the script writes it to temp_passwords.csv next to the list
     (never printed; branch_handout.py --passwords puts it on the cards).
409 from the server counts as "already exists". Writes accounts_result.csv next to the list (only with --yes).

The franchise owners (PR-L): the same list with `--role franchise` creates ONE read-only login per branch, own_<ตัวย่อ>
(role franchise, tied to the branch: it sees only the page ร้านของฉัน for that branch). Give it a list of the franchise
branches only. A list's ชื่อผู้ใช้ column counts only when it starts with own_; the result file is
owner_accounts_result.csv, and branch_handout.py --role franchise --list <that file> writes the owner messages.

The four old pilot logins created on 2026-10-07 are named branch_ubon branch_udon branch_khonkaen branch_rayong (the
pilot is now cnx ubn udn kr1 srn; --replace-pilot still handles exactly those four old logins).
With --yes --replace-pilot (and only then):
  * the branches that have an old login get a new wh_* login even though they already have the old one;
  * the old branch_* login is switched off (PUT /api/auth/users/<name> {"active": false}) in a LATER run, once
    the wh_* login already exists - so create the wh_* logins, send the new handout (branch_handout.py), then run
    the same command again to retire the old logins. Nothing is ever deleted.

Short logins (owner 2026-10-27): the logins made on 2026-10-22 carry long names (wh_chiangmai, wh_nakhonphanom ...)
and become wh_<3-letter code> (wh_cnx, wh_npm ...). --replace-logins generalises --replace-pilot: for each row, every
active branch login of that branch that is not the wanted wh_<ตัวย่อ> is old - the long wh_* names and the branch_*
pilot logins alike. The same two steps:
  run 1  --replace-logins --yes : creates wh_<ตัวย่อ> (no password sent: own temporary password + forced change); old ones stay on
  (send the new handout)
  run 2  --replace-logins --yes : the new login now exists, so each old login is switched off (active false, never deleted)
Without --yes, --replace-logins prints the plan per branch (สร้าง / ปิด / คงไว้) and changes nothing.
The server allows a second branch login for the same branch (POST /api/auth/users checks only the name).

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
        raise SystemExit(f"บัญชี {user} ยังใช้รหัสผ่านเริ่มต้น ให้เข้าสู่ระบบทางเบราว์เซอร์และตั้งรหัสผ่านใหม่ก่อน")


def plan_rows(rows: list, branches: dict, users: list, replace_pilot: bool = False, role: str = "branch",
              replace_logins: bool = False) -> list:
    """Decide each row: -> [{branch, abbr, username, recipient, action: create|exists|fail, retire, note}]. Pure, so it is tested
    without a server. `retire` = the old logins of the branch to switch off; run() switches them off only once the
    wanted login already existed before the run (create first, retire in a later run).
      replace_pilot  : the old logins are exactly the four branch_* pilot logins of 2026-10-07 (BL.LEGACY_PILOT_USERNAMES);
      replace_logins : every other active branch login of the branch is old (the long wh_* names, the branch_* pilot
                       logins, any login that is not the wanted wh_<ตัวย่อ>).
    Without either, a branch that already has a login keeps it and no second login is made.
    `role` is the kind of login the run makes: branch (wh_*) or franchise (own_*, one per branch, never retires anything)."""
    if role != "branch":
        replace_pilot = replace_logins = False
    by_user = {u["username"]: u for u in users}
    legacy = set(BL.LEGACY_PILOT_USERNAMES.values())
    by_branch = {}                                        # branch -> its active logins of this role
    for u in users:
        if u.get("role") == role and u.get("active", 1) and u.get("branch"):
            by_branch.setdefault(u["branch"], []).append(u["username"])
    out, taken = [], set()
    for r in rows:
        name = r["branch"]
        row = {"branch": name, "abbr": r.get("abbr", ""), "username": r["username"], "recipient": r.get("recipient", ""),
               "action": "fail", "retire": [], "note": ""}
        out.append(row)
        if name not in branches:
            row["note"] = "ไม่พบสาขานี้ในรายชื่อสาขาที่เปิดใช้งาน ตรวจชื่อให้ตรงกับระบบ"
            continue
        wanted = BL.login_of(r, role) or BL.username_for(name, branches[name], r.get("abbr", ""), role)
        current = [u for u in by_branch.get(name, []) if u != wanted]
        if replace_logins:
            old = current
        elif replace_pilot:
            old = [u for u in current if u in legacy]
        else:
            old = []
        have = [u for u in current if u not in old]       # an old login does not count as "this branch already has one"
        row["retire"] = old
        if wanted and wanted in by_user:
            if by_user[wanted].get("branch") != name or by_user[wanted].get("role") != role:
                row.update(username=wanted, note="ชื่อผู้ใช้นี้ถูกใช้กับบัญชีอื่นแล้ว", retire=[])
                continue
            row.update(username=wanted, action="exists", note="มีบัญชีนี้อยู่แล้ว")
            if row["retire"] and not by_user[wanted].get("active", 1):
                row.update(retire=[], note=f"บัญชี {wanted} ถูกปิดอยู่ จึงไม่ปิดบัญชีเดิม")   # keep the old one working
            continue
        if have:
            row.update(username=have[0], action="exists", note="สาขานี้มีบัญชีอยู่แล้ว ไม่สร้างบัญชีที่สอง")
            continue
        if not wanted:
            row.update(note="ไม่ทราบรหัสสาขา จึงตั้งชื่อผู้ใช้ไม่ได้", retire=[])
            continue
        if wanted in taken:
            row.update(username=wanted, note="ชื่อผู้ใช้ซ้ำกับสาขาอื่นในรายชื่อ", retire=[])
            continue
        taken.add(wanted)
        row.update(username=wanted, action="create", note="")     # made in this run: the old ones are retired by the next run
    return out


def plan_word(p: dict) -> str:
    """The plan of one row in a few words: สร้าง / คงไว้, plus the old logins that are (or will be) switched off."""
    if p["action"] == "fail":
        return "ไม่ดำเนินการ"
    word = "สร้าง" if p["action"] == "create" else "คงไว้"
    if p["retire"]:
        word += " · ปิด " + ", ".join(p["retire"]) + (" (รอบถัดไป)" if p["action"] == "create" else "")
    return word


def run(api, rows: list, apply: bool, replace_pilot: bool = False, role: str = "branch", replace_logins: bool = False) -> list:
    """Fetch the branch list and the users, decide, and (apply=True) create. With replace_pilot / replace_logins the old
    logins (see plan_rows) are switched off once the wanted wh_* login exists. -> rows with `result`."""
    st, js, text = api.request("GET", "/api/branches")
    if st != 200 or not isinstance(js, list):
        raise SystemExit(f"อ่านรายชื่อสาขาไม่ได้ ({_detail(st, js, text)})")
    branches = {b["name"]: b["id"] for b in js if b.get("active", True)}
    st, js, text = api.request("GET", "/api/auth/users")
    if st != 200 or not isinstance(js, list):
        raise SystemExit(f"อ่านรายชื่อผู้ใช้ไม่ได้ ({_detail(st, js, text)}) ต้องเข้าสู่ระบบด้วยบัญชี Management")
    plan = plan_rows(rows, branches, js, replace_pilot=replace_pilot, role=role, replace_logins=replace_logins)
    for p in plan:
        if p["action"] == "fail":
            p["result"] = BL.R_FAILED
        elif p["action"] == "exists":
            p["result"] = BL.R_EXISTS
        elif not apply:
            p["result"] = BL.R_PLAN
        else:
            st, js, text = api.request("POST", "/api/auth/users", {"username": p["username"], "role": role, "branch": p["branch"]})
            if st == 200:
                p["result"] = BL.R_CREATED
                p["password"] = (js or {}).get("temp_password") or "" if isinstance(js, dict) else ""
            elif st == 409:
                p["result"], p["note"] = BL.R_EXISTS, "มีบัญชีนี้อยู่แล้ว"
            else:
                p["result"], p["note"] = BL.R_FAILED, _detail(st, js, text)
        if p["retire"]:                                   # only for a wh_* login that already existed before this run
            if apply and p["action"] == "exists" and p["result"] == BL.R_EXISTS:
                done = []
                for old in p["retire"]:
                    st, js, text = api.request("PUT", f"/api/auth/users/{old}", {"active": False})
                    done.append(f"ปิดบัญชี {old} แล้ว" if st == 200 else f"ปิดบัญชี {old} ไม่สำเร็จ ({_detail(st, js, text)})")
                p["note"] = "; ".join(done)
            elif not apply:
                p["note"] = ""                            # the plan column (plan_word) already names the old logins
            elif p["action"] == "create" and p["result"] == BL.R_CREATED:
                p["note"] = "บัญชีเดิม " + ", ".join(p["retire"]) + " ยังใช้งานได้ รันคำสั่งเดิมซ้ำหลังส่งข้อความแจกสาขาเพื่อปิดบัญชีเดิม"
    return plan


def keep_created(plan: list, dest: Path) -> None:
    """A re-run finds the logins it made earlier and reports them 'already exists'. The earlier result file
    remembers which ones this script created (and so still carry their temporary password): keep that mark,
    otherwise the handout would skip them."""
    if not dest.exists():
        return
    try:
        before = {(r["branch"], r["username"]) for r in BL.read_list(dest) if r["result"] == BL.R_CREATED}
    except SystemExit:
        return
    for p in plan:
        if p["result"] == BL.R_EXISTS and (p["branch"], p["username"]) in before:
            p["result"], p["note"] = BL.R_CREATED, "สร้างไว้ในการรันก่อนหน้า"


def write_passwords(path: Path, plan: list, role: str) -> None:
    """temp_passwords.csv: username,role,branch,password - the same columns backend/rotate_default_passwords.py prints.
    Rows already in the file (an earlier run) are kept; a login made again gets its new password."""
    import csv
    rows = {}
    if path.exists():
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = {r["username"]: r for r in csv.DictReader(f) if r.get("username")}
    for p in plan:
        rows[p["username"]] = {"username": p["username"], "role": role, "branch": p["branch"], "password": p["password"]}
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["username", "role", "branch", "password"])
        w.writeheader()
        w.writerows(rows.values())


def print_table(plan: list) -> None:
    w = max([len(p["branch"]) for p in plan] + [6])
    print(f"{'สาขา':<{w}}  {'ชื่อผู้ใช้':<14}  {'แผน':<34}  ผล")
    for p in plan:
        print(f"{p['branch']:<{w}}  {p['username'] or '-':<14}  {plan_word(p):<34}  {p['result']}" + (f"  ({p['note']})" if p["note"] else ""))
    n = {k: sum(1 for p in plan if p["result"] == k) for k in (BL.R_CREATED, BL.R_PLAN, BL.R_EXISTS, BL.R_FAILED)}
    old = sum(len(p["retire"]) for p in plan)
    print(f"\nสรุป: สร้างแล้ว {n[BL.R_CREATED]} · จะสร้าง {n[BL.R_PLAN]} · มีอยู่แล้ว {n[BL.R_EXISTS]} · ไม่สำเร็จ {n[BL.R_FAILED]} · รวม {len(plan)} สาขา"
          + (f" · บัญชีเดิมที่ปิดหรือจะปิด {old} บัญชี" if old else ""))


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", required=True, help="รายชื่อสาขา (.xlsx หรือ .csv)")
    ap.add_argument("--base", default=DEFAULT_BASE, help=f"ที่อยู่ระบบ (ค่าเริ่มต้น {DEFAULT_BASE})")
    ap.add_argument("--yes", action="store_true", help="สร้างบัญชีจริง (ไม่ระบุ = แสดงแผนเท่านั้น)")
    ap.add_argument("--dry-run", action="store_true", help="แสดงแผนโดยไม่สร้างบัญชี (ค่าเริ่มต้น)")
    ap.add_argument("--replace-pilot", action="store_true",
                    help="สาขาที่มีบัญชี branch_* เดิม 4 บัญชีได้บัญชี wh_* ใหม่ และบัญชี branch_* เดิมถูกปิดในการรันครั้งถัดไป (ใช้ร่วมกับ --yes เท่านั้น)")
    ap.add_argument("--replace-logins", action="store_true",
                    help="สาขาที่มีบัญชีชื่ออื่น (เช่น wh_chiangmai หรือ branch_ubon) ได้บัญชี wh_<ตัวย่อ> ใหม่ และบัญชีเดิมถูกปิด "
                         "(ไม่ลบ) ในการรันครั้งถัดไปหลังมีบัญชีใหม่แล้ว · ไม่ระบุ --yes = แสดงแผน สร้าง / ปิด / คงไว้")
    ap.add_argument("--role", choices=("branch", "franchise"), default="branch",
                    help="branch = บัญชีสาขา wh_<ตัวย่อ> (ค่าเริ่มต้น) · franchise = บัญชีเจ้าของสาขา own_<ตัวย่อ> (ดูผลอย่างเดียว)")
    ap.add_argument("--admin-user", default=os.environ.get("OSHINEI_ADMIN_USER", "management1"),
                    help="บัญชี Management ที่ใช้เข้าสู่ระบบ (หรือ OSHINEI_ADMIN_USER)")
    ap.add_argument("--token-env", default="OSHINEI_SEED_TOKEN", help="ชื่อตัวแปรที่เก็บ write token")
    a = ap.parse_args(argv)
    if a.yes and a.dry_run:
        print("ใช้ --yes และ --dry-run พร้อมกันไม่ได้", file=sys.stderr)
        return 2
    if a.replace_pilot and not a.yes:
        print("--replace-pilot ใช้ได้เฉพาะพร้อม --yes (ถ้าไม่ระบุ --yes ได้เพียงแผน)", file=sys.stderr)
        return 2
    if a.replace_pilot and a.role != "branch":
        print("--replace-pilot ใช้ได้เฉพาะบัญชีสาขา (--role branch)", file=sys.stderr)
        return 2
    if a.replace_logins and a.role != "branch":
        print("--replace-logins ใช้ได้เฉพาะบัญชีสาขา (--role branch)", file=sys.stderr)
        return 2
    if a.replace_logins and a.replace_pilot:
        print("เลือกอย่างใดอย่างหนึ่ง: --replace-logins หรือ --replace-pilot", file=sys.stderr)
        return 2
    try:
        rows = BL.read_list(a.list)
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 2
    print(f"{'สร้างบัญชีจริง' if a.yes else 'แผน (dry run) ไม่มีการสร้างบัญชี'} · {'เจ้าของสาขา (own_*)' if a.role == 'franchise' else 'สาขา (wh_*)'} · {len(rows)} สาขา · {a.base}")
    pw = os.environ.get("OSHINEI_ADMIN_PASSWORD") or ""
    api = Api(a.base, resolve_token(a.token_env))
    st, js, _ = api.request("GET", "/api/auth/me")
    if not (st == 200 and isinstance(js, dict) and js.get("enabled") is False):
        if not pw:
            pw = getpass.getpass(f"รหัสผ่านของ {a.admin_user}: ")
    try:
        sign_in(api, a.admin_user, pw)
        plan = run(api, rows, apply=a.yes, replace_pilot=a.replace_pilot, role=a.role, replace_logins=a.replace_logins)
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 2
    dest = Path(a.list).with_name("owner_accounts_result.csv" if a.role == "franchise" else "accounts_result.csv")
    if a.yes:
        keep_created(plan, dest)
    print_table(plan)
    if a.yes:
        out = BL.write_result(dest, plan)
        print(f"เขียนผลลัพธ์ที่ {out}")
        made = [p for p in plan if p.get("password")]
        pwf = dest.with_name("temp_passwords.csv")
        if made:
            write_passwords(pwf, made, a.role)
            print(f"รหัสผ่านชั่วคราว {len(made)} บัญชีอยู่ที่ {pwf} (ไฟล์เดียวที่มีรหัส ห้ามอัปโหลด ลบหลังแจกเสร็จ)")
        print("ขั้นต่อไป: py -3.12 scripts\\branch_handout.py " + ("--role franchise " if a.role == "franchise" else "")
              + f'--list "{out}" --passwords "{pwf}"')
        if any(p["retire"] and p["action"] == "create" for p in plan):
            print("บัญชีเดิมยังใช้งานได้ หลังส่งข้อความแจกสาขาแล้ว รันคำสั่งเดิมซ้ำอีกครั้งเพื่อปิดบัญชีเดิม")
    else:
        print("นี่คือแผนเท่านั้น ถ้าถูกต้องให้รันซ้ำพร้อม --yes")
    return 1 if any(p["result"] == BL.R_FAILED for p in plan) else 0


if __name__ == "__main__":
    raise SystemExit(main())
