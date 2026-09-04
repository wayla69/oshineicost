"""
upload_data.py — ONE easy way to upload Easy POS data into oshinei.db.

This is the single entry point for loading CLEANSED FLAT RAW exports
(the ``ES1_*_raw.xlsx`` monthly-SKU and ``23 ES_*_raw.xlsx`` per-transaction
flat tables in ``Data for Oshinei/Data Cleasing/raw/``). Point it at one file,
several files, or a folder — it auto-detects each file's kind and period and
loads it with a period+branch-scoped upsert (idempotent: re-running replaces
that month, never double-counts).

Why this exists (and is NOT redundant):
  The parsing/loading logic already lives in reingest_raw_2026.py. That script,
  however, only runs as a destructive FULL rebuild of ALL Easy sales. This module
  adds ZERO new parsing — it *reuses* reingest_raw_2026.parse_es1/parse_es_tx/
  load_es1/load_es_tx — and wraps them in the safe, incremental "drop in one
  month" workflow that's been proven in ingest_may26_update.py. One tool, both
  callers (CLI here + the web /api/upload endpoint), one code path.

SAFETY (same guarantees as the dedicated scripts):
  * PRESERVES every curated cost table — the loaders only touch Easy SALES rows.
    The run verifies the curated snapshot is unchanged and aborts loudly if not.
  * Period+branch-scoped: only the months present in your files are replaced.
  * Refuses to create BRANCHES that don't already exist (the duplicate-branch
    trap) unless you pass --allow-new-branch.
  * DRY-RUN by default. --apply makes a consistent sqlite backup first.

Usage:
  python upload_data.py                         # dry-run the whole raw/ folder
  python upload_data.py --apply                 # load the whole raw/ folder
  python upload_data.py "ES1_Jun 25_raw.xlsx"   # dry-run one file
  python upload_data.py FILE [FILE ...] --apply  # load specific files
  python upload_data.py --dir /path/to/folder --apply
"""
from __future__ import annotations

import argparse
import calendar
import sqlite3
import sys
from pathlib import Path

import reingest_raw_2026 as rr   # parse_es1/parse_es_tx/load_es1/load_es_tx/safe_backup/period_of/CURATED
# Cleansed-raw exports carry the FULL POS branch names (e.g. 'ขอนแก่น (1)'),
# while the live DB stores short canonical names. Reuse the proven alias map +
# remapper from the May-26 ingest instead of redefining them here.
from ingest_may26_update import _remap_branches as remap_branches

HERE     = Path(__file__).parent.resolve()
DB_PATH  = HERE / "oshinei.db"
RAW_DIR  = HERE.parent / "Data for Oshinei" / "Data Cleasing" / "raw"

CURATED = rr.CURATED


# ---------------------------------------------------------------------------
# Format detection — cleansed flat raw only (ES1_*_raw / 23 ES_*_raw)
# ---------------------------------------------------------------------------
def classify(path: Path) -> str:
    """Return 'es1' (monthly SKU), 'tx' (per-transaction), or '' if unrecognised.

    CLEANSED FLAT RAW ONLY: matches the '..._raw.xlsx' exports. The production
    multi-section files ('ES1_May 25.xlsx', '23 ES_May 25.xlsx' — no '_raw') are
    a DIFFERENT layout that the flat parsers would mis-read, so they are NOT
    matched here and fall through to a clean rejection instead of silent garbage.
    """
    stem = path.stem            # filename without extension
    if path.suffix.lower() != ".xlsx" or not stem.endswith("_raw"):
        return ""
    if stem.startswith("ES1_"):
        return "es1"
    if stem.startswith("23 ES_") or stem.startswith("ES_"):
        return "tx"
    return ""


def detect(path: Path) -> tuple[str, str]:
    """Return (kind, period) for a cleansed-raw file. Empty kind/period = unknown."""
    kind = classify(path)
    period = rr.period_of(path.stem) if kind else ""
    return kind, period


def _day_coverage(parsed: dict, period: str) -> dict | None:
    """Calendar-day coverage check for a per-transaction (tx) parse.

    The Easy POS monthly exports have a confirmed upstream bug: they DROP the
    last calendar day of the month (exclusive end-date on the export). That day's
    sales then silently vanish — every absolute total (revenue, COGS, bills) runs
    ~1 day (often a payday peak) low. This turns that silent loss into a loud,
    per-file warning so it can never pass unnoticed again.

    Returns {expected_days, present_days, missing_days, last_present,
    expected_last, last_day_missing} or None if the period is unparseable.
    Fix lives UPSTREAM (re-export with an inclusive end date) — the data simply
    is not in the file, so the loader cannot reconstruct it.
    """
    try:
        y, mo = (int(x) for x in period.split("-"))
        ndays = calendar.monthrange(y, mo)[1]
    except Exception:
        return None
    present = set()
    for txs in parsed.get("branches", {}).values():
        for t in txs:
            sa = t.get("sold_at") or ""
            if len(sa) >= 10:
                present.add(sa[:10])
    expected = {f"{y:04d}-{mo:02d}-{d:02d}" for d in range(1, ndays + 1)}
    missing = sorted(expected - present)
    last_expected = f"{y:04d}-{mo:02d}-{ndays:02d}"
    return {
        "expected_days":  ndays,
        "present_days":   len(present & expected),
        "missing_days":   [d[-2:] for d in missing],
        "last_present":   max(present) if present else None,
        "expected_last":  last_expected,
        "last_day_missing": last_expected not in present,
    }


# ---------------------------------------------------------------------------
# Per-file ingest on an OPEN connection — shared by the CLI and the web endpoint
# ---------------------------------------------------------------------------
def ingest_one(con: sqlite3.Connection, path: Path, *,
               apply: bool = False, allow_new_branch: bool = False) -> dict:
    """
    Parse + (optionally) load a single cleansed-raw file using the existing
    reingest_raw_2026 loaders. Period+branch-scoped, so it REPLACES that month.

    Does NOT back up or commit-with-ANALYZE — the caller owns that (the CLI
    `run()` backs up once for the whole batch; the web endpoint manages its own
    connection lifecycle). load_es1/load_es_tx commit their own writes.

    Returns a report dict; raises ValueError on an unrecognisable file or a
    new-branch violation.
    """
    kind, period = detect(path)
    if not kind:
        raise ValueError(
            f"Unrecognised file: {path.name}. Expected a cleansed flat raw export "
            f"named like 'ES1_Jun 25_raw.xlsx' or '23 ES_Jun 25_raw.xlsx'."
        )
    if not period:
        raise ValueError(f"Cannot parse period (YYYY-MM) from filename: {path.name}")

    parsed = rr.parse_es1(path, period) if kind == "es1" else rr.parse_es_tx(path, period)
    # Normalise full POS branch names → canonical DB names (merging any dups).
    parsed = remap_branches(parsed)

    branches = list(parsed["branches"].keys())

    # Branch safety: never silently create branches (the duplicate-branch trap).
    existing = {r[0] for r in con.execute("SELECT name FROM branches")}
    new_branches = sorted(b for b in branches if b not in existing)
    if new_branches and not allow_new_branch:
        raise ValueError(
            f"{path.name}: {len(new_branches)} branch name(s) not in the DB "
            f"(would create duplicates): {', '.join(repr(b) for b in new_branches)}. "
            f"Pass --allow-new-branch (CLI) only if these are genuinely new branches."
        )

    rows = sum(len(v) for v in parsed["branches"].values())
    coverage = _day_coverage(parsed, period) if kind == "tx" else None

    if apply:
        if kind == "es1":
            loaded = rr.load_es1(con, parsed, path.name)
        else:
            loaded, _summ = rr.load_es_tx(con, parsed, path.name)
        rows = loaded

    return {
        "file": path.name,
        "kind": "monthly_sku" if kind == "es1" else "transactions",
        "period": period,
        "branches": len(branches),
        "new_branches": new_branches,
        "rows": rows,
        "applied": apply,
        "coverage": coverage,
    }


# ---------------------------------------------------------------------------
# CLI driver — batch + backup + curated-table integrity proof
# ---------------------------------------------------------------------------
def _resolve_inputs(files: list[str], folder: str | None) -> list[Path]:
    if files:
        return [Path(f).resolve() for f in files]
    base = Path(folder).resolve() if folder else RAW_DIR
    es1 = sorted(base.glob("ES1_*_raw.xlsx"))
    tx  = sorted(base.glob("23 ES_*_raw.xlsx"))
    return tx + es1   # transactions first, then SKU — mirrors reingest order


def run(files: list[str], folder: str | None, *,
        apply: bool, allow_new_branch: bool, db_path: Path = DB_PATH) -> int:
    paths = _resolve_inputs(files, folder)
    missing = [p for p in paths if not p.exists()]
    if missing:
        for p in missing:
            print(f"ABORT — file not found: {p}", file=sys.stderr)
        return 1
    if not paths:
        print(f"Nothing to upload — no cleansed-raw files found in {folder or RAW_DIR}",
              file=sys.stderr)
        return 1

    print(f"DB    : {db_path}")
    print(f"Files : {len(paths)}")
    print(f"Mode  : {'APPLY' if apply else 'DRY-RUN (no writes)'}\n")

    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys=OFF")

    # Snapshot curated tables so we can PROVE we didn't touch them.
    pre = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in CURATED}
    pre_costsum = con.execute("SELECT COALESCE(SUM(unit_cost),0) FROM sku_cost").fetchone()[0]

    # Validate every file up front (kind/period/branches) before any write.
    reports: list[dict] = []
    try:
        for p in paths:
            r = ingest_one(con, p, apply=False, allow_new_branch=allow_new_branch)
            reports.append(r)
            print(f"  [{r['kind']:<12}] {p.name:34} {r['period']}  "
                  f"rows={r['rows']:>7,}  branches={r['branches']}"
                  + (f"  NEW BRANCHES: {r['new_branches']}" if r['new_branches'] else ""))
            cov = r.get("coverage")
            if cov and cov["missing_days"]:
                tail = " (incl. LAST DAY — upstream export off-by-one)" if cov["last_day_missing"] else ""
                print(f"      ⚠ COVERAGE: {r['period']} missing {len(cov['missing_days'])} "
                      f"day(s) {cov['missing_days']} — last present {cov['last_present']}, "
                      f"expected {cov['expected_last']}{tail}", file=sys.stderr)
    except ValueError as e:
        print(f"\nABORT — {e}", file=sys.stderr)
        con.close()
        return 1

    if not apply:
        print("\nDRY-RUN complete — no data written. Re-run with --apply to perform.")
        con.close()
        return 0

    # ---- APPLY ----
    bk = rr.safe_backup(db_path)
    print(f"\nSafe backup -> {bk.name}\n")

    applied: list[dict] = []
    for p in paths:
        r = ingest_one(con, p, apply=True, allow_new_branch=allow_new_branch)
        applied.append(r)
        print(f"  loaded {r['kind']:<12} {p.name:34} {r['period']}  rows={r['rows']:>7,}")

    # Prove curated tables are untouched.
    post = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in CURATED}
    post_costsum = con.execute("SELECT COALESCE(SUM(unit_cost),0) FROM sku_cost").fetchone()[0]
    print("\n--- curated tables (must be unchanged) ---")
    ok = True
    for t in CURATED:
        same = pre[t] == post[t]
        ok = ok and same
        print(f"  {t:20} {pre[t]} -> {post[t]}  {'OK' if same else '*** CHANGED ***'}")
    cs_ok = abs(pre_costsum - post_costsum) < 0.01
    ok = ok and cs_ok
    print(f"  sku_cost sum         {pre_costsum:.2f} -> {post_costsum:.2f}  "
          f"{'OK' if cs_ok else '*** CHANGED ***'}")

    con.execute("ANALYZE")
    con.commit()
    con.close()

    if not ok:
        print("\nWARNING: curated tables changed — review immediately.", file=sys.stderr)
        return 2
    print("\nDone.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Easy upload of cleansed-raw Easy POS data.")
    ap.add_argument("files", nargs="*", help="Specific file(s) to upload (default: whole raw/ folder).")
    ap.add_argument("--dir", dest="folder", default=None,
                    help="Folder of cleansed-raw files to upload (default: Data Cleasing/raw).")
    ap.add_argument("--apply", action="store_true", help="Perform the upload (default: dry-run).")
    ap.add_argument("--allow-new-branch", action="store_true",
                    help="Permit creating branch names not already in the DB.")
    args = ap.parse_args()
    return run(args.files, args.folder, apply=args.apply,
               allow_new_branch=args.allow_new_branch)


if __name__ == "__main__":
    sys.exit(main())
