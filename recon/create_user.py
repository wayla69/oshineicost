"""Create (or reset) a recon login account from the command line.

    py -3.12 -m recon.create_user <username> --role manager --name "ชื่อที่แสดง"
    py -3.12 -m recon.create_user <username> --reset          # forgotten password / lock-out

For local use, or on the Fly machine via `fly ssh console` (python -m recon.create_user …).
Works on the database in OSHINEI_DB (default /data/oshinei.db). The password is
asked twice with getpass — never passed on the command line, never printed.
The account must change its password at first login. Both actions are written
to recon_audit_log (by 'cli').

--reset is the lock-out escape hatch: new password, must change at next login,
lockout cleared, account re-activated, every old session ended.
"""
from __future__ import annotations

import argparse
import getpass
import sys

from . import auth as A
from . import core


def _ask_password() -> str:
    pw = getpass.getpass("รหัสผ่าน (อย่างน้อย 8 ตัว): ")
    if getpass.getpass("ยืนยันรหัสผ่าน: ") != pw:
        raise SystemExit("รหัสผ่านสองครั้งไม่ตรงกัน — ยกเลิก")
    problem = A.password_problem(pw)
    if problem:
        raise SystemExit(problem)
    return pw


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m recon.create_user",
                                 description="Create or reset a recon login account.")
    ap.add_argument("username")
    ap.add_argument("--role", choices=A.ROLES, default="manager")
    ap.add_argument("--name", default="", help="display name (default: the username)")
    ap.add_argument("--reset", action="store_true",
                    help="reset an EXISTING account's password (and unlock / re-activate it)")
    args = ap.parse_args(argv)

    with core.get_db() as conn:
        conn.executescript(core._SCHEMA.read_text(encoding="utf-8"))   # recon_users if missing
        row = conn.execute("SELECT * FROM recon_users WHERE username=?", (args.username.strip(),)).fetchone()
        if args.reset:
            if not row:
                raise SystemExit(f"ไม่พบผู้ใช้ {args.username}")
            pw = _ask_password()
            A.set_password(conn, row["id"], pw, must_change=True)
            conn.execute("UPDATE recon_users SET active=1 WHERE id=?", (row["id"],))
            core._audit(conn, "recon_user", row["id"], "reset_password", "cli",
                        [{"field": "username", "new": row["username"]}])
            conn.commit()
            print(f"รีเซ็ตรหัสผ่านของ {row['username']} แล้ว — ต้องเปลี่ยนรหัสเมื่อเข้าสู่ระบบครั้งถัดไป")
            return 0
        if row:
            raise SystemExit(f"มีผู้ใช้ {row['username']} อยู่แล้ว — ใช้ --reset เพื่อตั้งรหัสใหม่")
        pw = _ask_password()
        problem = A.validate_new_user(args.username, args.name, args.role, pw)
        if problem:
            raise SystemExit(problem)
        uid = A.create_user(conn, args.username, args.name, args.role, pw, created_by="cli")
        core._audit(conn, "recon_user", uid, "create", "cli", [
            {"field": "username", "new": args.username.strip()}, {"field": "role", "new": args.role}])
        conn.commit()
    print(f"สร้างผู้ใช้ {args.username} ({A.ROLE_TH[args.role]}) แล้ว — ต้องเปลี่ยนรหัสเมื่อเข้าสู่ระบบครั้งแรก")
    return 0


if __name__ == "__main__":
    sys.exit(main())
