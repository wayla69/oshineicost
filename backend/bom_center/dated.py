# -*- coding: utf-8 -*-
"""bom_center.dated — the cut-over: released costs become the COGS, month by month.

Owner decision 2026-10-06: the center's costs apply FROM 1 September 2026 (the
Grand Menu changeover, rules.GRAND_MENU_FROM) and never to any earlier month —
the old engine's numbers for August and before stay exactly as they are, even
for Grand Menu codes that were sold early (81k buffet plates in August 2026).

The app's COGS engine holds ONE cost per SKU (sku_effective_cost). Rather than
touch the ~200 places that multiply by it, one table carries the exceptions:

    bc_sku_cost_month(sku_id, month, unit_cost, release_id)

one row per SKU per month from the cut-over month on, for every SKU a RELEASED
monthly version covers in that month (release_in_force rules: the newest
released version with effective_month <= month). rules.effective_unit_cost_sql
looks here FIRST when the sales row's month is on/after the cut-over, and falls
through to the old chain otherwise — so with no release, every number in the
app is byte-identical (the golden tests prove it), and a release changes exactly
its SKUs, exactly its months.

It is a bc_* table on purpose: rebuilt here on every release / retire / startup,
but preserved across the daily DB swap like the releases it is derived from, and
pulled by the PC sync (pull_from_live) so the per-bill COGS and the tier fact
tables it builds use the same costs as the app.
"""
from datetime import date, datetime, timezone

import rules

from .clock import today as bkk_today

CUTOVER = rules.GRAND_MENU_FROM            # '2026-09-01'
CUTOVER_MONTH = CUTOVER[:7]                # '2026-09'

DDL = ("CREATE TABLE IF NOT EXISTS bc_sku_cost_month("
       " sku_id INTEGER NOT NULL, month TEXT NOT NULL, unit_cost REAL NOT NULL,"
       " release_id INTEGER, built_at TEXT, PRIMARY KEY (sku_id, month))")


def ensure(con):
    con.execute(DDL)


def _months(first: str, last: str):
    y, m = int(first[:4]), int(first[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _next(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y + (m == 12):04d}-{1 if m == 12 else m + 1:02d}"


def rebuild(con, today: date = None) -> dict:
    """Recompute every row from the released monthly versions. Compute, then replace.
    Months run from the cut-over to next month (or the latest effective month)."""
    from .recipes import release_costs
    ensure(con)
    today = today or bkk_today()
    rels = con.execute("SELECT release_id, effective_month FROM bc_release WHERE kind='monthly' AND status='released'"
                       " AND effective_month >= ? ORDER BY effective_month, release_id", (CUTOVER_MONTH,)).fetchall()
    last = _next(f"{today.year:04d}-{today.month:02d}")
    if rels and rels[-1][1] > last:
        last = rels[-1][1]
    sku_id = dict(con.execute("SELECT sku_code, sku_id FROM skus"))
    costs = {rid: release_costs(con, rid) for rid, _ in rels}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    rows = []
    for month in _months(CUTOVER_MONTH, last):
        in_force = {}
        for rid, eff in rels:                      # oldest first: a newer version overwrites per SKU
            if eff <= month:
                for code, (total, problems) in costs[rid].items():
                    if code in sku_id and not rules.is_tier_code(code):
                        in_force[sku_id[code]] = (float(total or 0.0), rid)
        rows += [(sid, month, c, rid, now) for sid, (c, rid) in in_force.items()]
    con.execute("DELETE FROM bc_sku_cost_month")
    con.executemany("INSERT INTO bc_sku_cost_month(sku_id, month, unit_cost, release_id, built_at) VALUES(?,?,?,?,?)",
                    rows)
    return {"rows": len(rows), "releases": len(rels), "months": _months(CUTOVER_MONTH, last) if rows else []}


def month_map(con) -> dict:
    """{month: {sku_id: unit_cost}} as stored — use costs_for(map, month) to read it."""
    ensure(con)
    out = {}
    for sid, month, c in con.execute("SELECT sku_id, month, unit_cost FROM bc_sku_cost_month"):
        out.setdefault(month, {})[sid] = c
    return out


def costs_for(mmap: dict, month: str) -> dict:
    """{sku_id: unit_cost} in force for `month` — the same rule as rules.dated_cost_sql:
    nothing before the cut-over; else, per SKU, the latest stored month <= `month`."""
    if not month or month < CUTOVER_MONTH:
        return {}
    out = {}
    for m in sorted(k for k in mmap if k <= month):
        out.update(mmap[m])
    return out


def pull_from_live(sq, api: str = None, timeout: int = 25) -> int:
    """Mirror the live site's bc_sku_cost_month into this (staging) DB before the
    per-bill COGS and the fact tables are built. NON-FATAL like the confirmed-cost
    pull: offline / no token / an older deploy just leaves what is there."""
    import json
    import os
    import urllib.request
    from pathlib import Path
    try:
        ensure(sq)
        if os.environ.get("OSHINEI_SYNC_ON_LIVE") == "1":
            # the cloud sync copies the LIVE database, which already holds this table (bc_ is preserved):
            # asking the site for its own rows would be a request to ourselves for what we already have
            print("      dated costs: the staging copy is the live DB - using its own")
            return 0
        import refresh_fly_db as _r
        token = os.environ.get("OSHINEI_SEED_TOKEN", "").strip()
        if ":" in token:
            token = token.split(":", 1)[1].strip()
        if not token:
            here = Path(__file__).resolve().parent.parent
            for name in (".seed_token.txt", ".seed_tokens.txt"):
                f = here / name
                if f.exists():
                    token = _r._parse_token_file(f) or ""
                    if token:
                        break
        if not token:
            print("      dated costs: no seed token here — using the DB's own")
            return 0
        api = (api or os.environ.get("OSHINEI_API") or "https://oshinei-dashboard.fly.dev").rstrip("/")
        req = urllib.request.Request(f"{api}/api/_admin/bc-sku-cost-month", headers={"X-Seed-Token": token})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rows = data.get("rows") or []
        codes = dict(sq.execute("SELECT sku_code, sku_id FROM skus"))
        sq.execute("DELETE FROM bc_sku_cost_month")
        # keyed by sku_CODE in transit: ids are local to each database
        sq.executemany("INSERT OR REPLACE INTO bc_sku_cost_month(sku_id, month, unit_cost, release_id, built_at)"
                       " VALUES(?,?,?,?,?)",
                       [(codes[c], m, u, rid, at) for c, m, u, rid, at in rows if c in codes])
        sq.commit()
        print(f"      dated costs pulled from the live site: {len(rows)} rows")
        return len(rows)
    except Exception as e:
        print(f"      dated costs NOT pulled ({type(e).__name__}: {e}) — continuing with the DB's own")
        return 0


def export_rows(con) -> list:
    """For /api/_admin/bc-sku-cost-month: rows keyed by sku_code (portable across DBs)."""
    ensure(con)
    return [list(r) for r in con.execute(
        "SELECT s.sku_code, d.month, d.unit_cost, d.release_id, d.built_at FROM bc_sku_cost_month d"
        " JOIN skus s USING(sku_id) ORDER BY d.month, s.sku_code")]
