"""Provision a dashboard login from the command line.

The admin API (POST /api/auth/users) refuses the shared default password by
design — check_password_policy() rejects it — so handing someone the documented
"first login" credential can only be done here, on the same seeding path
backend/auth.py:seed_users() uses. The account is always created with
must_change=1, so the very first login is forced through the change-password
dialog (js/01-core.js) before any page loads.

Usage (local DB at backend/oshinei.db, or wherever OSHINEI_DB points):

    python backend/provision_user.py marketing1 --role marketing

    # confine the login to one branch
    python backend/provision_user.py marketing1 --role marketing --branch อุบล

    # give a specific temporary password instead of the shared default
    python backend/provision_user.py marketing1 --role marketing --password 'Temp#First2026'

    # user forgot their password and never changed it → put it back
    python backend/provision_user.py marketing1 --role marketing --reset

On Fly (production DB lives in the machine's volume):

    fly ssh console -a <app> -C "python /app/backend/provision_user.py marketing1 --role marketing"

Idempotent: re-running without --reset leaves an existing account untouched and
exits 0.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).parent.resolve()
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import auth as _auth  # noqa: E402


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Create a dashboard login with a "
                                             "default password that must be changed at first login.")
    ap.add_argument("username")
    ap.add_argument("--role", required=True, choices=_auth.ROLES)
    ap.add_argument("--branch", default=None,
                    help="confine this login to one branch (default: the whole chain)")
    ap.add_argument("--password", default=None,
                    help="temporary password to set instead of a random one "
                         f"(must be ≥ {_auth.MIN_PASSWORD_LEN} chars)")
    ap.add_argument("--reset", action="store_true",
                    help="if the account already exists, give it a new temporary password and "
                         "re-arm the forced change")
    ap.add_argument("--db", default=os.environ.get("OSHINEI_DB", str(HERE / "oshinei.db")),
                    help="path to oshinei.db (default: $OSHINEI_DB or backend/oshinei.db)")
    args = ap.parse_args(argv)

    if not Path(args.db).exists():
        print(f"[provision] DB not found: {args.db}", file=sys.stderr)
        return 2

    con = _connect(args.db)
    try:
        _auth.init_auth(con)                      # table + additive migrations
        try:
            outcome = _auth.provision_user(
                con, args.username, args.role,
                branch=args.branch, password=args.password,
                reset_existing=args.reset,
            )
        except ValueError as e:
            print(f"[provision] {e}", file=sys.stderr)
            return 1
    finally:
        con.close()

    shown = args.password or _auth.last_temp_password()
    if outcome == "exists":
        print(f"[provision] {args.username} already exists — unchanged "
              f"(use --reset to give it a new temporary password)")
    else:
        verb = "created" if outcome == "created" else "password reset for"
        print(f"[provision] {verb} {args.username} (role={args.role}, "
              f"branch={args.branch or 'ทุกสาขา'})")
        print(f"[provision] password: {shown}  — must be changed at first login")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
