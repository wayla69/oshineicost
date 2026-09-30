"""Push Oshinei's daily numbers to Invisible Voice (the customer-voice SaaS).

Invisible Voice keeps two business tables per tenant — `sales_daily` (revenue,
covers, bills, discount per location per day) and `menu_items` (names for menu
mention detection) — filled by the tenant's POS through its adapter API. This
is Oshinei's adapter: it reads the local dashboard DB after the daily MSSQL
sync and pushes the last few days (idempotent upserts, so re-runs are safe).

    py -3.12 push_invisible.py                 # last 3 days of sales
    py -3.12 push_invisible.py --days 30       # re-push a month
    py -3.12 push_invisible.py --since 2025-05-01 --locations --menu   # first load

Config: INVISIBLE_API_URL (default: the Railway api domain) and the API key in
INVISIBLE_API_KEY or backend/.invisible_key.txt (gitignored; scope sales:write).

Self-healing: a successful run records the newest DAY IT PUSHED in
backend/.invisible_last_ok.txt, and the next run reaches back to that day if it is
further than --days. The window used to be a flat 3 days, so the 15 days this
failed silently (2026-09-06 -> 09-21: the api domain was renamed and the old host
answers 404 "Application not found") would have stayed a hole forever.
It records the pushed day, not the run date: on 2026-09-30 the local DB was still
at 09-26 when both pushes ran (Auto Sync had failed), each pushed 0 rows and
stamped "09-30", and the next run would have started at 09-28 - 09-27 never sent.
A run that pushes nothing leaves the mark where it was.
Revenue is the dashboard's rule (rules.net_revenue_sum: inc-VAT ÷ 1.07 minus
service charge) — the same figure the P&L shows.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from rules import net_revenue_sum

# Renamed from api-production-bffdb on 2026-09-06; a renamed Railway domain stops
# serving at once (the edge answers 404 "Application not found"), it never redirects.
DEFAULT_URL = "https://invisiblevoice-api.up.railway.app"
KEY_FILE = HERE / ".invisible_key.txt"
STATE_FILE = HERE / ".invisible_last_ok.txt"   # day of the last successful sales push
MAX_CATCHUP_DAYS = 90                          # an outage longer than this needs --since
DB_FILE = HERE / "oshinei.db"
CHUNK = 4000                                   # the API caps a call at 5,000 rows


def location_code(branch_id: int) -> str:
    return f"BR{int(branch_id):02d}"


def api_key() -> str:
    key = os.environ.get("INVISIBLE_API_KEY", "").strip()
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        sys.exit("no API key: set INVISIBLE_API_KEY or write backend/.invisible_key.txt")
    return key


def post(path: str, rows: list[dict], *, base: str, key: str, dry: bool) -> dict:
    if dry:
        return {"dry_run": True, "rows": len(rows)}
    req = urllib.request.Request(base.rstrip("/") + path, method="POST",
                                 data=json.dumps({"rows": rows}, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "X-API-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:400]
        sys.exit(f"{path} → HTTP {e.code}: {body}")


def push_chunked(path: str, rows: list[dict], **kw) -> list[dict]:
    return [post(path, rows[i:i + CHUNK], **kw) for i in range(0, len(rows), CHUNK)] if rows else []


def since_day(days: int, today, state_file: Path = STATE_FILE) -> str:
    """First day to push: `days` back, or the last successful day if that is further.

    The last good day itself is re-sent (it may have been a partial day when it
    was pushed). An unreadable or absent state file means plain `days`."""
    since = today - timedelta(days=days)
    try:
        last_ok = datetime.strptime(state_file.read_text(encoding="utf-8").strip(), "%Y-%m-%d").date()
    except (OSError, ValueError):
        return since.isoformat()
    return max(min(since, last_ok), today - timedelta(days=MAX_CATCHUP_DAYS)).isoformat()


def pushed_through(rows: list[dict]) -> str | None:
    """The newest day these sales rows cover, or None when there are none.

    This, not the run date, is what .invisible_last_ok.txt records: a push from a
    stale local DB must not move the mark past days it never sent."""
    return max((r["day"] for r in rows), default=None)


def location_rows(con: sqlite3.Connection) -> list[dict]:
    return [{"code": location_code(bid), "name": name, "active": bool(active)}
            for bid, name, active in con.execute("SELECT branch_id, name, active FROM branches ORDER BY branch_id")]


def sales_rows(con: sqlite3.Connection, since: str) -> list[dict]:
    sql = f"""
        SELECT substr(st.sold_at, 1, 10) AS day, st.branch_id,
               {net_revenue_sum('st')} AS revenue,
               COUNT(*) AS bills, SUM(COALESCE(st.customer_count, 0)) AS covers,
               SUM(COALESCE(st.discount, 0) + COALESCE(st.sp_discount, 0)) AS discount
        FROM sales_transaction st
        WHERE st.sold_at >= :since
        GROUP BY 1, 2 ORDER BY 1, 2"""
    return [{"location_code": location_code(bid), "day": day, "revenue": round(rev or 0, 2), "bills": int(bills or 0),
             "covers": int(covers or 0), "discount": round(disc or 0, 2)}
            for day, bid, rev, bills, covers, disc in con.execute(sql, {"since": since})]


def menu_rows(con: sqlite3.Connection) -> list[dict]:
    sql = """
        SELECT s.sku_code, s.description, COALESCE(c.name, ''), s.active,
               COALESCE(sc.unit_cost, NULLIF(ec.recipe_cost, 0))
        FROM skus s
        LEFT JOIN categories c ON c.category_id = s.category_id
        LEFT JOIN sku_cost sc ON sc.sku_id = s.sku_id
        LEFT JOIN sku_effective_cost ec ON ec.sku_id = s.sku_id
        WHERE s.description IS NOT NULL AND s.description <> ''
        ORDER BY s.sku_code"""
    return [{"code": code, "name": name, "category": cat, "active": bool(active),
             "unit_cost": round(cost, 4) if cost is not None else None}
            for code, name, cat, active, cost in con.execute(sql)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=3, help="push sales from N days ago (default 3)")
    ap.add_argument("--since", help="push sales from this day (YYYY-MM-DD) instead of --days")
    ap.add_argument("--locations", action="store_true", help="also push the branch list (creates missing locations)")
    ap.add_argument("--menu", action="store_true", help="also push the menu names")
    ap.add_argument("--no-sales", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=str(DB_FILE))
    ap.add_argument("--url", default=os.environ.get("INVISIBLE_API_URL", DEFAULT_URL))
    a = ap.parse_args(argv)
    kw = {"base": a.url, "key": "-" if a.dry_run else api_key(), "dry": a.dry_run}
    con = sqlite3.connect(a.db)
    out: dict = {}
    if a.locations:
        out["locations"] = push_chunked("/api/v1/adapters/locations", location_rows(con), **kw)
    if not a.no_sales:
        today = datetime.now(tz=ZoneInfo("Asia/Bangkok")).date()
        since = a.since or since_day(a.days, today)
        rows = sales_rows(con, since)
        out["sales"] = {"since": since, "rows": len(rows), "results": push_chunked("/api/v1/adapters/sales", rows, **kw)}
        last_day = pushed_through(rows)
        if last_day and not a.dry_run and a.db == str(DB_FILE):   # post() exits on any HTTP error, so this is a success
            STATE_FILE.write_text(last_day, encoding="utf-8")
    if a.menu:
        rows = menu_rows(con)
        out["menu"] = {"rows": len(rows), "results": push_chunked("/api/v1/adapters/menu", rows, **kw)}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
