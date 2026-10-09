#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nightly off-site backup of the owner-data tables of the live app (PR-H).

The tables that exist ONLY on the live site - users, BoM Center (bc_*), reconciliation,
feedback, checklists, settings, push subscriptions ... (backend/core.py _PRESERVED_*) - are
the one thing the MSSQL sync cannot rebuild. This asks the live app for a SQLite file that
holds exactly those tables (schema, indexes, rows), verifies it, and keeps the last N files:

    py -3.12 scripts\\backup_owner_tables.py                    # download + verify + prune + log
    py -3.12 scripts\\backup_owner_tables.py --dry-run          # only ask the server what it would export
    py -3.12 scripts\\backup_owner_tables.py --restore-check FILE   # prove a saved file opens and every table reads

    --base URL        default https://oshinei-dashboard.fly.dev (override for a local test)
    --out DIR         default %USERPROFILE%\\Oshinei_backups
    --keep N          files to keep, default 30
    --token-env NAME  environment variable holding the admin token, default OSHINEI_SEED_TOKEN

The token (OSHINEI_SEED_TOKEN) is read from the environment, else backend\\.env, else
backend\\.seed_tokens.txt - the same places the other PC-side jobs read it. It travels in the
X-Seed-Token header only and is never printed or logged. Stdlib only (runs under any Python 3.9+).

Exit code: 0 = backup written and verified, 1 = failed (the reason is in backup.log), 2 = no token.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_BASE = "https://oshinei-dashboard.fly.dev"
FILE_RE = re.compile(r"^oshinei_owner_data_(\d{8}_\d{4})\.sqlite$")
LOG_NAME = "backup.log"
SHRINK_FAIL = 0.9            # a table below 90% of the live row count (and more than 5 rows short) fails the run
SHRINK_SLACK = 5


# ── token ────────────────────────────────────────────────────────────────────
def _first_token(raw: str) -> str:
    """OSHINEI_SEED_TOKEN may hold several 'label:token' entries; any one is accepted by the server."""
    first = (raw or "").replace("\n", ",").split(",")[0].strip()
    return first.split(":", 1)[1].strip() if ":" in first else first


def _env_file_value(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    return ""


def resolve_token(env_name: str = "OSHINEI_SEED_TOKEN", repo: Path = REPO) -> str:
    for name in (env_name, "OSHINEI_WRITE_TOKEN"):
        v = _first_token(os.environ.get(name, ""))
        if v:
            return v
    v = _first_token(_env_file_value(repo / "backend" / ".env", "OSHINEI_SEED_TOKEN"))
    if v:
        return v
    for fname in (".seed_token.txt", ".seed_tokens.txt"):
        f = repo / "backend" / fname
        if f.exists():
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    return _first_token(line)
    return ""


# ── network (the seam the tests can replace) ─────────────────────────────────
def _request(url: str, token: str, timeout: int):
    return urllib.request.urlopen(urllib.request.Request(
        url, headers={"X-Seed-Token": token, "User-Agent": "Oshinei backup_owner_tables"}), timeout=timeout)


def fetch_json(base: str, token: str, timeout: int = 300) -> dict:
    with _request(f"{base.rstrip('/')}/api/_admin/backup/owner-data.json", token, timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def download(base: str, token: str, dest: Path, timeout: int = 600) -> dict:
    """Stream the export to `dest`; returns {'counts': {table: rows} the server declared for THIS file, 'filename'}."""
    with _request(f"{base.rstrip('/')}/api/_admin/backup/owner-data.sqlite", token, timeout) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
        counts = json.loads(r.headers.get("X-Backup-Counts") or "null")
        m = re.search(r"filename=([^\s;]+)", r.headers.get("Content-Disposition") or "")
    return {"counts": counts, "filename": m.group(1).strip('"') if m else ""}


# ── verify ───────────────────────────────────────────────────────────────────
def table_counts(path: Path) -> dict:
    """Open the file read-only, require integrity_check == ok, return {table: rows}."""
    con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            raise sqlite3.DatabaseError(f"integrity_check: {ok}")
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name")]
        return {n: con.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        con.close()


def verify_file(path: Path, declared: dict | None = None, live: dict | None = None) -> dict:
    """The checks a backup must pass. Returns {'ok', 'problems', 'warnings', 'tables', 'rows', 'counts'}.
    declared = row counts the server sent with THIS download (exact match required);
    live     = {table: rows} from the JSON endpoint just after (rows may move, so only a clear loss fails)."""
    problems, warnings, counts = [], [], {}
    try:
        counts = table_counts(path)
    except Exception as e:                                           # not a SQLite file, corrupt, unreadable
        return {"ok": False, "problems": [f"the file does not open: {e}"], "warnings": [], "tables": 0, "rows": 0, "counts": {}}
    if not counts:
        problems.append("the file holds no tables")
    if declared is not None:
        for t in sorted(set(declared) | set(counts)):
            if t not in counts:
                problems.append(f"{t}: declared by the server but missing in the file")
            elif t not in declared:
                problems.append(f"{t}: in the file but not declared by the server")
            elif counts[t] != declared[t]:
                problems.append(f"{t}: file has {counts[t]} rows, the server declared {declared[t]}")
    if live is not None:
        drift = 0
        for t in sorted(set(live) | set(counts)):
            if t not in counts:
                problems.append(f"{t}: live now has {live[t]} rows but the file has no such table")
            elif t not in live:
                warnings.append(f"{t}: in the file, not on the live app now")
            elif counts[t] != live[t]:
                drift += 1
                if live[t] - counts[t] > SHRINK_SLACK and counts[t] < live[t] * SHRINK_FAIL:
                    problems.append(f"{t}: file has {counts[t]} rows, live has {live[t]}")
        if drift:
            warnings.append(f"{drift} table(s) changed between the download and the check (normal on a live app)")
    return {"ok": not problems, "problems": problems, "warnings": warnings,
            "tables": len(counts), "rows": sum(counts.values()), "counts": counts}


def restore_check(path: Path) -> dict:
    """Open a COPY of the file and read every table (a read-only query each). Proves the backup is usable."""
    path = Path(path)
    if not path.exists():
        return {"ok": False, "problems": [f"missing: {path}"], "tables": {}}
    tmp = Path(tempfile.mkdtemp(prefix="restore_check_"))
    problems, tables = [], {}
    try:
        copy = tmp / path.name
        shutil.copy2(path, copy)
        con = sqlite3.connect(f"file:{copy.as_posix()}?mode=ro", uri=True)
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok":
                problems.append(f"integrity_check: {ok}")
            for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name").fetchall():
                try:
                    n = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                    con.execute(f'SELECT * FROM "{name}" LIMIT 1').fetchall()
                    tables[name] = n
                except sqlite3.Error as e:
                    problems.append(f"{name}: {e}")
            if not tables:
                problems.append("the file holds no tables")
        finally:
            con.close()
    except Exception as e:
        problems.append(f"the file does not open: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"ok": not problems, "problems": problems, "tables": tables}


# ── housekeeping ─────────────────────────────────────────────────────────────
def prune(out: Path, keep: int) -> list:
    if not keep or keep <= 0:
        return []
    files = sorted((p for p in Path(out).glob("oshinei_owner_data_*.sqlite") if FILE_RE.match(p.name)),
                   key=lambda p: FILE_RE.match(p.name).group(1))
    removed = []
    for p in files[:-keep] if len(files) > keep else []:
        try:
            p.unlink()
            removed.append(p.name)
        except OSError:
            pass
    return removed


def log_line(out: Path, status: str, **fields) -> str:
    """One line per run: date | OK/FAIL | file | bytes | tables | rows | note. Never carries the token."""
    parts = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), status] + [f"{k}={v}" for k, v in fields.items() if v not in (None, "")]
    line = " | ".join(parts)
    try:
        Path(out).mkdir(parents=True, exist_ok=True)
        with open(Path(out) / LOG_NAME, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    return line


def _clean_err(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code} from the live app" + (" (the admin token was refused)" if e.code == 403 else "")
    return f"{type(e).__name__}: {e}"[:300]


# ── commands ─────────────────────────────────────────────────────────────────
def run_backup(args, token: str) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    part = out / "owner_data.part"
    try:
        info = download(args.base, token, part, timeout=args.timeout)
    except Exception as e:
        part.unlink(missing_ok=True)
        print(log_line(out, "FAIL", reason="download: " + _clean_err(e)))
        return 1
    live = None
    try:
        live = {t["name"]: t["rows"] for t in fetch_json(args.base, token, timeout=args.timeout)["tables"]}
    except Exception as e:                                           # the header check below is the exact one
        live_note = "live-compare skipped: " + _clean_err(e)
    else:
        live_note = ""
    res = verify_file(part, declared=info["counts"], live=live)
    if info["counts"] is None:
        res["problems"].append("the server sent no X-Backup-Counts header")
        res["ok"] = False
    if not res["ok"]:
        bad = out / "owner_data.rejected"
        bad.unlink(missing_ok=True)
        part.replace(bad)
        print(log_line(out, "FAIL", bytes=bad.stat().st_size, tables=res["tables"], rows=res["rows"],
                       reason="; ".join(res["problems"])[:600]))
        return 1
    name = info["filename"] if FILE_RE.match(info["filename"] or "") else f"oshinei_owner_data_{datetime.now():%Y%m%d_%H%M}.sqlite"
    dest = out / name
    part.replace(dest)
    removed = prune(out, args.keep)
    print(log_line(out, "OK", file=name, bytes=dest.stat().st_size, tables=res["tables"], rows=res["rows"],
                   pruned=len(removed) or None, note="; ".join(res["warnings"] + ([live_note] if live_note else [])) or None))
    return 0


def run_dry(args, token: str) -> int:
    try:
        j = fetch_json(args.base, token, timeout=args.timeout)
    except Exception as e:
        print(log_line(Path(args.out), "FAIL", mode="dry-run", reason=_clean_err(e)))
        return 1
    for t in j["tables"]:
        print(f"    {t['name']:<40} {t['rows']:>9}")
    print(log_line(Path(args.out), "DRY", bytes=j["bytes"], tables=len(j["tables"]), rows=j["rows"], generated_at=j["generated_at"]))
    return 0


def run_restore_check(args) -> int:
    res = restore_check(Path(args.restore_check))
    for t, n in sorted(res["tables"].items()):
        print(f"    {t:<40} {n:>9} rows  read OK")
    for p in res["problems"]:
        print("  FAIL:", p)
    print(f"RESTORE CHECK: {'PASS - ' + str(len(res['tables'])) + ' tables, ' + str(sum(res['tables'].values())) + ' rows read from a copy' if res['ok'] else 'FAIL'}")
    return 0 if res["ok"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Back up the live app's owner-data tables to this PC")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--out", default=str(Path(os.environ.get("USERPROFILE") or Path.home()) / "Oshinei_backups"))
    ap.add_argument("--keep", type=int, default=30)
    ap.add_argument("--token-env", default="OSHINEI_SEED_TOKEN", help="environment variable holding the admin token")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--dry-run", action="store_true", help="only ask the server what it would export")
    ap.add_argument("--once", action="store_true", help="one run now (the default; for the setup script)")
    ap.add_argument("--restore-check", metavar="FILE", help="open a copy of a saved backup and read every table")
    args = ap.parse_args(argv)
    if args.restore_check:
        return run_restore_check(args)
    token = resolve_token(args.token_env)
    if not token:
        print(log_line(Path(args.out), "FAIL", reason=f"no admin token: set {args.token_env}, backend\\.env or backend\\.seed_tokens.txt"))
        return 2
    return run_dry(args, token) if args.dry_run else run_backup(args, token)


if __name__ == "__main__":
    sys.exit(main())
