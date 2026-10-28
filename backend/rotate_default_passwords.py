# -*- coding: utf-8 -*-
"""Give every active account still on the shared default password its OWN temporary password.

Code guard 2 (2026-10-28): the logins are predictable (wh_<ตัวย่อ>, own_<ตัวย่อ>, management1 ...) and the shared
default opened every one of them, so one branch's handout opened every other branch. The live server now refuses the
shared default at sign-in (auth.default_refused); this script moves the accounts still on it to a random temporary
password each (auth.temp_password), forced change at first sign-in as before.

    python backend/rotate_default_passwords.py                 # dry run: lists the accounts, changes nothing
    python backend/rotate_default_passwords.py --yes           # sets the passwords, prints username,role,branch,password (CSV)

On the live server (the output is the only copy of the new passwords: keep it OUTSIDE the repo, give each one only to
its own account holder, delete it after the handout - scripts/branch_handout.py --passwords reads it):

    fly ssh console -a oshinei-dashboard -C "python /app/backend/rotate_default_passwords.py --yes" > %USERPROFILE%\\Oshinei_branch_handout\\temp_passwords.csv
"""
import argparse
import csv
import os
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import auth as _auth  # noqa: E402


def on_default(con) -> list:
    rows = con.execute("SELECT username, role, branch, pw_hash, pw_salt FROM app_user WHERE active=1 ORDER BY role, username").fetchall()
    return [r for r in rows if _auth.verify_password(_auth.DEFAULT_PASSWORD, r["pw_hash"], r["pw_salt"])]


def rotate(con) -> list:
    """-> [(username, role, branch, temp password)] for every active account that was on the shared default."""
    out = []
    for r in on_default(con):
        pw = _auth.temp_password()
        h = _auth.hash_password(pw)
        con.execute("UPDATE app_user SET pw_hash=?, pw_salt=?, must_change=1 WHERE username=?", (h["hash"], h["salt"], r["username"]))
        out.append((r["username"], r["role"], r["branch"] or "", pw))
    con.commit()
    return out


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="set the new passwords (without it: list only)")
    ap.add_argument("--db", default=os.environ.get("OSHINEI_DB", str(HERE / "oshinei.db")))
    a = ap.parse_args(argv)
    if not Path(a.db).exists():
        print(f"DB not found: {a.db}", file=sys.stderr)
        return 2
    con = sqlite3.connect(a.db, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        if not a.yes:
            rows = on_default(con)
            for r in rows:
                print(f"{r['username']}\t{r['role']}\t{r['branch'] or ''}")
            print(f"{len(rows)} active accounts on the shared default (dry run, nothing changed; --yes to rotate)", file=sys.stderr)
            return 0
        done = rotate(con)
    finally:
        con.close()
    w = csv.writer(sys.stdout, lineterminator="\n")
    w.writerow(["username", "role", "branch", "password"])
    w.writerows(done)
    print(f"{len(done)} accounts now have their own temporary password (forced change at first sign-in)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
