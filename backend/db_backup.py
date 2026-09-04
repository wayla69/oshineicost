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
import datetime as _dt
import gzip
import hashlib
import re
import shutil
import sqlite3
import sys
import tempfile
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
    # the copy inherits the live DB's WAL mode and would leave -wal/-shm sidecars
    # beside the archive (rotation never matches them); fold it into one file
    dst.execute("PRAGMA journal_mode=DELETE")
    src.close(); dst.close()
    for suf in ("-wal", "-shm"):
        side = Path(str(dest) + suf)
        if side.exists():
            side.unlink()

    counts = _counts(dest)
    digest = _sha256(dest)
    manifest = dest.with_suffix(".manifest.txt")
    lines = [f"backup of: {db}", f"file: {dest.name}", f"sha256: {digest}",
             f"size_MB: {round(dest.stat().st_size/1e6, 1)}", "row counts:"]
    lines += [f"  {t}: {n}" for t, n in counts.items()]
    if getattr(args, "gzip", False):
        # a 1.1 GB copy a day does not fit a cloud folder; ~250 MB gzipped does.
        # The manifest keeps BOTH digests: the .db one is what verify sees after
        # decompressing, the .gz one is what the folder holds.
        gz = dest.with_name(dest.name + ".gz")
        with open(dest, "rb") as fi, gzip.open(gz, "wb", compresslevel=6) as fo:
            shutil.copyfileobj(fi, fo, 1 << 20)
        dest.unlink()
        lines += [f"gz_file: {gz.name}", f"gz_sha256: {_sha256(gz)}",
                  f"gz_size_MB: {round(gz.stat().st_size/1e6, 1)}"]
        dest = gz
    manifest.write_text("\n".join(lines), encoding="utf-8")
    print("[OK] backup written:", dest)
    print("\n".join(lines))
    removed = prune(out_dir, getattr(args, "keep_daily", 0), getattr(args, "keep_monthly", 0),
                    today=_dt.date.fromisoformat(args.today) if getattr(args, "today", "") else None)
    if removed:
        print(f"[OK] rotated out {len(removed)} older backup(s): " + ", ".join(p.name for p in removed))
    return 0


_STAMP = re.compile(r"^oshinei_(\d{4}-\d{2}-\d{2})(?:_\d{4})?\.db(?:\.gz)?$")


def prune(out_dir: Path, keep_daily: int, keep_monthly: int, today=None) -> list:
    """Rotation: keep every backup from the last `keep_daily` days plus the FIRST
    backup of each of the last `keep_monthly` months; remove the rest together
    with their manifests. Both 0 = keep everything. Files whose name is not a
    dated backup are never touched."""
    if not keep_daily and not keep_monthly:
        return []
    today = today or _dt.date.today()
    dated = []
    for p in sorted(out_dir.iterdir()):
        m = _STAMP.match(p.name)
        if m:
            dated.append((_dt.date.fromisoformat(m.group(1)), p))
    keep = set()
    for d, p in dated:
        if keep_daily and (today - d).days < keep_daily:
            keep.add(p)
    if keep_monthly:
        first_of_month = {}
        for d, p in dated:                      # sorted by name = by date, earliest first
            first_of_month.setdefault((d.year, d.month), p)
        months = sorted(first_of_month)[-keep_monthly:]
        keep.update(first_of_month[m] for m in months)
    removed = []
    for d, p in dated:
        if p in keep:
            continue
        p.unlink()
        man = out_dir / (re.sub(r"\.db(\.gz)?$", "", p.name) + ".manifest.txt")
        if man.exists():
            man.unlink()
        removed.append(p)
    return removed


def cmd_verify(args) -> int:
    bk = Path(args.backup)
    if not bk.exists():
        print(f"[FAIL] backup not found: {bk}"); return 1
    tmp = None
    if bk.suffix == ".gz":
        # a restore test of a compressed backup IS the decompression + the checks
        tmp = Path(tempfile.mkdtemp(prefix="oshinei_restore_")) / bk.name[:-3]
        with gzip.open(bk, "rb") as fi, open(tmp, "wb") as fo:
            shutil.copyfileobj(fi, fo, 1 << 20)
        print(f"  decompressed {bk.name} -> {round(tmp.stat().st_size/1e6, 1)} MB")
        bk = tmp
    try:
        return _verify_file(bk, Path(args.backup).name)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp.parent, ignore_errors=True)


def _verify_file(bk: Path, shown_name: str) -> int:
    con = sqlite3.connect(f"file:{bk}?mode=ro", uri=True)
    integ = con.execute("PRAGMA integrity_check").fetchone()[0]   # full check for a restore test
    con.close()
    counts = _counts(bk)
    has_recon = isinstance(counts.get("recon_uploads"), int) and counts["recon_uploads"] > 0
    has_sales = isinstance(counts.get("sales_transaction"), int) and counts["sales_transaction"] > 0
    ok = (integ == "ok") and has_recon and has_sales
    print(f"RESTORE TEST — {shown_name}")
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
    b.add_argument("--gzip", action="store_true", help="store the copy gzipped (~4x smaller)")
    b.add_argument("--keep-daily", type=int, default=0, help="rotation: keep this many days of backups")
    b.add_argument("--keep-monthly", type=int, default=0, help="rotation: keep the first backup of this many months")
    b.add_argument("--today", default="", help="rotation reference date (tests)")
    b.set_defaults(func=cmd_backup)
    v = sub.add_parser("verify"); v.add_argument("--backup", required=True)
    v.set_defaults(func=cmd_verify)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
