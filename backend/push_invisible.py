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

DEFAULT_URL = "https://api-production-bffdb.up.railway.app"
KEY_FILE = HERE / ".invisible_key.txt"
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
        since = a.since or (datetime.now(tz=ZoneInfo("Asia/Bangkok")).date() - timedelta(days=a.days)).isoformat()
        rows = sales_rows(con, since)
        out["sales"] = {"since": since, "rows": len(rows), "results": push_chunked("/api/v1/adapters/sales", rows, **kw)}
    if a.menu:
        rows = menu_rows(con)
        out["menu"] = {"rows": len(rows), "results": push_chunked("/api/v1/adapters/menu", rows, **kw)}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
