#!/usr/bin/env python3
"""
AUD-2 — off-site backup + restore-verify for oshinei.db.

The recon work + dashboard data live in ONE SQLite file on Fly's /data volume.
An auditor asks for two things: a backup schedule, and EVIDENCE of a successful
restore test. This script does both.

Usage
-----
Back up a DB file (verified copy + manifest with row counts & sha256):
    python backup_db.py backup --db /data/oshinei.db --out "D:/OneDrive/Oshinei Backups"

Pull the live Fly DB first, then back it up locally (needs flyctl + auth):
    flyctl ssh sftp get /data/oshinei.db backend/_fly_live.db --app oshinei-dashboard
    python backup_db.py backup --db backend/_fly_live.db --out "D:/OneDrive/Oshinei Backups"

Restore test (the part auditors actually want) — open a backup read-only and
verify integrity + that the key tables have rows. Prints PASS/FAIL + evidence:
    python backup_db.py verify --backup "D:/OneDrive/Oshinei Backups/oshinei_2026-06-13_0600.db"

Schedule the `backup` command daily (Windows Task Scheduler / cron) pointed at a
OneDrive/S3 folder. Keep >= 90 daily + 12 monthly copies. Run `verify` on the
latest backup at least once a quarter and keep the console output as evidence.
"""
import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

# Tables whose presence+rows prove a backup is usable (recon work + sales + users).
_KEY_TABLES = ["recon_uploads", "recon_bank_lines", "recon_audit_log",
               "sales_transaction", "app_user"]


def _counts(db: Path) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        out = {}
        for t in _KEY_TABLES:
            try:
                out[t] = con.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
            except sqlite3.Error:
                out[t] = "—"
        return out
    finally:
        con.close()


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_backup(args) -> int:
    db = Path(args.db)
    if not db.exists():
        print(f"[FAIL] source DB not found: {db}"); return 1
    # integrity check on the source before copying
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    ok = con.execute("PRAGMA quick_check").fetchone()[0]
    con.close()
    if ok != "ok":
        print(f"[FAIL] source DB failed quick_check: {ok}"); return 1

    out_dir = Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)
    stamp = args.stamp or "backup"          # caller passes a real timestamp (no clock dep here)
    dest = out_dir / f"oshinei_{stamp}.db"
    # Use sqlite's online backup API so a live (WAL) DB is copied consistently.
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    dst = sqlite3.connect(str(dest))
    with dst:
        src.backup(dst)
    src.close(); dst.close()

    counts = _counts(dest)
    digest = _sha256(dest)
    manifest = dest.with_suffix(".manifest.txt")
    lines = [f"backup of: {db}", f"file: {dest.name}", f"sha256: {digest}",
             f"size_MB: {round(dest.stat().st_size/1e6, 1)}", "row counts:"]
    lines += [f"  {t}: {n}" for t, n in counts.items()]
    manifest.write_text("\n".join(lines), encoding="utf-8")
    print("[OK] backup written:", dest)
    print("\n".join(lines))
    return 0


def cmd_verify(args) -> int:
    bk = Path(args.backup)
    if not bk.exists():
        print(f"[FAIL] backup not found: {bk}"); return 1
    con = sqlite3.connect(f"file:{bk}?mode=ro", uri=True)
    integ = con.execute("PRAGMA integrity_check").fetchone()[0]   # full check for a restore test
    con.close()
    counts = _counts(bk)
    has_recon = isinstance(counts.get("recon_uploads"), int) and counts["recon_uploads"] > 0
    has_sales = isinstance(counts.get("sales_transaction"), int) and counts["sales_transaction"] > 0
    ok = (integ == "ok") and has_recon and has_sales
    print(f"RESTORE TEST — {bk.name}")
    print(f"  integrity_check: {integ}")
    for t, n in counts.items():
        print(f"  {t}: {n}")
    print(f"  sha256: {_sha256(bk)}")
    print("\nRESULT:", "PASS — backup is restorable and complete" if ok
          else "FAIL — backup is corrupt or missing recon/sales data")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Oshinei DB backup + restore-verify (AUD-2)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup"); b.add_argument("--db", required=True)
    b.add_argument("--out", required=True); b.add_argument("--stamp", default="")
    b.set_defaults(func=cmd_backup)
    v = sub.add_parser("verify"); v.add_argument("--backup", required=True)
    v.set_defaults(func=cmd_verify)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
