#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pull the tables that live ONLY on Fly into the backup folder (AUD-2, part 3).

The nightly DB push replaces oshinei.db on Fly with the local copy and carries a
set of tables forward (core._PRESERVED_*): users, customer voices, survey links,
actions, competitor snapshots, checklist ticks, the recon uploads. Those rows
never exist on this PC, so the local DB backup does not cover them. This asks
the live app for exactly that set through the token-gated export
(GET /api/_admin/export-state, seed token in the X-Seed-Token header) and writes it next to the DB backups:

    fly_state_<stamp>.ndjson.gz     one JSON line per table header / row / final counts
    fly_state_<stamp>.manifest.txt  row counts + sha256 (the restore test reads it)

    python backend/pull_fly_state.py pull   --out "…/Oshinei Backups" --stamp 2026-09-05_0730 [--keep 14]
    python backend/pull_fly_state.py verify --file "…/fly_state_2026-09-05_0730.ndjson.gz"   # restore into memory, compare counts
    python backend/pull_fly_state.py restore --file … --db backend/oshinei.db [--tables customer_feedback,feedback_action]

The token is the seed / write token (env OSHINEI_WRITE_TOKEN / OSHINEI_SEED_TOKEN
or backend/.seed_tokens.txt) — the same one the data refresh uses. Every
network read goes through `_open(url)` so the tests can serve a file instead.
"""
import argparse
import base64
import gzip
import hashlib
import json
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

from fetch_feedback import DEFAULT_API, resolve_token  # noqa: E402

FILE_RE = re.compile(r"^fly_state_(\d{4}-\d{2}-\d{2}_\d{4})\.ndjson\.gz$")


def _open(url: str, timeout: int = 300, headers: dict = None):
    """The network seam: a readable binary stream for the URL."""
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Oshinei pull_fly_state", **(headers or {})}),
                                  timeout=timeout)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_export(path: Path):
    """Yield (kind, payload) from an export file: ('table', {name, columns}),
    ('row', {t, r}), ('done', {counts, exported_at})."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            if "table" in o:
                yield "table", o
            elif "done" in o:
                yield "done", o
            else:
                yield "row", o


def counts_in(path: Path) -> tuple:
    """(rows counted per table, the counts the server declared, exported_at)."""
    seen, declared, at = {}, None, None
    for kind, o in read_export(path):
        if kind == "table":
            seen.setdefault(o["table"], 0)
        elif kind == "row":
            seen[o["t"]] = seen.get(o["t"], 0) + 1
        else:
            declared, at = o.get("counts") or {}, o.get("exported_at")
    return seen, declared, at


def cmd_pull(args) -> int:
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    token = resolve_token(args.token)
    if not token:
        print("✗ no token — set OSHINEI_SEED_TOKEN or backend/.seed_tokens.txt")
        return 1
    url = f"{args.api.rstrip('/')}/api/_admin/export-state"
    dest = out / f"fly_state_{args.stamp}.ndjson.gz"
    tmp = dest.with_suffix(".part")
    try:
        with _open(url, headers={"X-Seed-Token": token}) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
    except Exception as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        print(f"✗ pull failed: {e}")
        return 1
    tmp.replace(dest)
    try:
        seen, declared, at = counts_in(dest)
    except Exception as e:
        print(f"✗ the file is not a readable export: {e}")
        return 1
    if declared is None:
        print("✗ the export ended without its final counts line (truncated?)")
        return 1
    total = sum(seen.values())
    lines = [f"file: {dest.name}", f"exported_at: {at}", f"sha256: {_sha256(dest)}", f"bytes: {dest.stat().st_size}",
             f"tables: {len(seen)}", f"rows: {total}", ""] + [f"{t}: {n}" for t, n in sorted(seen.items())]
    dest.with_name(f"fly_state_{args.stamp}.manifest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"✓ fly state → {dest} ({dest.stat().st_size // 1024} KB, {len(seen)} tables, {total} rows)")
    for t, n in sorted(seen.items()):
        if n:
            print(f"    {t:<32} {n:>8}")
    removed = prune(out, args.keep)
    if removed:
        print(f"    pruned {len(removed)} older file(s)")
    return 0


def prune(out: Path, keep: int) -> list:
    if not keep or keep <= 0:
        return []
    files = sorted([p for p in out.glob("fly_state_*.ndjson.gz") if FILE_RE.match(p.name)],
                   key=lambda p: FILE_RE.match(p.name).group(1))
    removed = []
    for p in files[:-keep] if len(files) > keep else []:
        man = p.with_name(p.name.replace(".ndjson.gz", ".manifest.txt"))
        for f in (p, man):
            try:
                f.unlink(); removed.append(f.name)
            except OSError:
                pass
    return removed


def restore_into(con: sqlite3.Connection, path: Path, tables=None) -> dict:
    """Load the export into `con`: an existing table is emptied and refilled by
    column name; a missing one is created from the export's columns (typeless —
    a disaster-recovery fallback, not the app's DDL). Returns rows per table."""
    want = set(tables) if tables else None
    cols, done = {}, {}
    for kind, o in read_export(path):
        if kind == "table":
            t = o["table"]
            if want and t not in want:
                continue
            cols[t] = o["columns"]
            exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
            if exists:
                have = {r[1] for r in con.execute(f"PRAGMA table_info([{t}])")}
                for c in o["columns"]:
                    if c not in have:
                        con.execute(f"ALTER TABLE [{t}] ADD COLUMN [{c}]")
                con.execute(f"DELETE FROM [{t}]")
            else:
                con.execute(f"CREATE TABLE [{t}]({', '.join('[' + c + ']' for c in o['columns'])})")
            done[t] = 0
        elif kind == "row":
            t = o["t"]
            if t not in cols:
                continue
            cs = cols[t]
            vals = [base64.b64decode(v["b64"]) if isinstance(v, dict) and "b64" in v else v for v in o["r"]]
            con.execute(f"INSERT INTO [{t}]({', '.join('[' + c + ']' for c in cs)}) VALUES ({', '.join('?' * len(cs))})", vals)
            done[t] += 1
    con.commit()
    return done


def cmd_verify(args) -> int:
    path = Path(args.file)
    if not path.exists():
        print(f"✗ missing: {path}")
        return 1
    seen, declared, at = counts_in(path)
    con = sqlite3.connect(":memory:")
    got = restore_into(con, path)
    back = {t: con.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0] for t in got}
    con.close()
    problems = []
    if declared is None:
        problems.append("no final counts line (truncated download)")
    else:
        for t, n in declared.items():
            if seen.get(t, 0) != n:
                problems.append(f"{t}: file has {seen.get(t, 0)} rows, the server declared {n}")
    for t, n in seen.items():
        if back.get(t) != n:
            problems.append(f"{t}: restored {back.get(t)} of {n}")
    man = path.with_name(path.name.replace(".ndjson.gz", ".manifest.txt"))
    if man.exists():
        m = man.read_text(encoding="utf-8")
        sha = re.search(r"^sha256: (\w+)$", m, re.M)
        if sha and sha.group(1) != _sha256(path):
            problems.append("sha256 differs from the manifest")
    print(f"{path.name}: {len(seen)} tables, {sum(seen.values())} rows, exported {at}")
    for p in problems:
        print("  ✗", p)
    print("\nFLY STATE RESULT:", "PASS — the Fly-only tables restore completely" if not problems else "FAIL")
    return 0 if not problems else 1


def cmd_restore(args) -> int:
    path, db = Path(args.file), Path(args.db)
    if not path.exists() or not db.exists():
        print("✗ file or db missing")
        return 1
    tables = [t.strip() for t in (args.tables or "").split(",") if t.strip()] or None
    con = sqlite3.connect(str(db))
    try:
        done = restore_into(con, path, tables)
    finally:
        con.close()
    for t, n in sorted(done.items()):
        print(f"  {t:<32} {n:>8} row(s)")
    print(f"✓ restored {len(done)} table(s) into {db}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Pull / verify / restore the Fly-only tables")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull"); p.add_argument("--out", required=True); p.add_argument("--stamp", required=True)
    p.add_argument("--api", default=DEFAULT_API); p.add_argument("--token", default=None); p.add_argument("--keep", type=int, default=14)
    v = sub.add_parser("verify"); v.add_argument("--file", required=True)
    r = sub.add_parser("restore"); r.add_argument("--file", required=True); r.add_argument("--db", required=True); r.add_argument("--tables", default="")
    args = ap.parse_args(argv)
    return {"pull": cmd_pull, "verify": cmd_verify, "restore": cmd_restore}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
