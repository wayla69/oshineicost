"""
Oshinei Sales & Cost Analysis  -  FastAPI backend
Run: uvicorn backend.main:app --reload --port 8000
  or from this folder: uvicorn main:app --reload --port 8000
"""

import sqlite3
import tempfile
import shutil
import threading
import time as _time
import functools
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict

from fastapi import FastAPI, Query, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Response cache  (TTL-based, keyed on function name + query-param values)
# ---------------------------------------------------------------------------
_resp_cache: dict = {}
_CACHE_TTL  = 300   # 5 minutes — safe for an analytics dashboard
_cache_lock = threading.Lock()

def _cache_key(fn_name: str, kwargs: dict) -> str:
    return fn_name + "|" + "|".join(f"{k}={v}" for k, v in sorted(kwargs.items()))

import json as _json
from fastapi.responses import Response as _Response

_cache_stats: dict = {"hits": 0, "misses": 0}

def ttl_cache(fn):
    """Decorator: caches the pre-serialized JSON bytes for 5 min.

    Returning a ``Response(content=bytes)`` skips FastAPI's jsonable_encoder,
    which matters for large responses (e.g. lifecycle = 270 KB).
    """
    import inspect as _inspect
    sig = _inspect.signature(fn)          # capture original signature once

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        # Normalise args → stable dict keyed by parameter name
        try:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            bound_kwargs = dict(bound.arguments)
        except TypeError:
            bound_kwargs = kwargs

        key = _cache_key(fn.__name__, bound_kwargs)
        now = _time.monotonic()
        with _cache_lock:
            entry = _resp_cache.get(key)
            if entry and now - entry[0] < _CACHE_TTL:
                _cache_stats["hits"] += 1
                return _Response(content=entry[1], media_type="application/json")
            _cache_stats["misses"] += 1

        result = fn(*args, **kwargs)
        try:
            result_bytes = _json.dumps(result, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            # Non-serializable (e.g. FileResponse) — skip cache, return raw
            return result
        with _cache_lock:
            _resp_cache[key] = (now, result_bytes)
        return _Response(content=result_bytes, media_type="application/json")

    # Expose original signature so FastAPI injects the right query params
    wrapper.__signature__ = sig
    return wrapper

def invalidate_cache(*prefixes: str):
    """Remove cached entries whose key starts with any of the given function names.

    Called with no arguments → flush the entire cache (used after bulk data writes
    where it's hard to know which derived endpoints depend on the change).
    """
    with _cache_lock:
        if not prefixes:
            _resp_cache.clear()
            return
        drop = [k for k in _resp_cache if any(k.startswith(p) for p in prefixes)]
        for k in drop:
            del _resp_cache[k]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE    = Path(__file__).parent.resolve()
DB_PATH = HERE / "oshinei.db"
STATIC  = HERE.parent / "vendor"
INDEX   = HERE.parent / "index.html"   # canonical (Phase 17d-fix 2026-05-28: was 'Oshinei Dashboard.html')

app = FastAPI(title="Oshinei Dashboard API")

# The SPA is served same-origin from this app (GET /), so it needs no CORS at all.
# We allow only explicit localhost origins for local dev tooling. NEVER use "*"/"null"
# here: with no auth layer, a wildcard lets any website issue cross-origin writes
# (POST/PUT/DELETE cost & settings). Override via OSHINEI_CORS_ORIGINS (comma-separated).
import os as _os
_cors_env = _os.environ.get("OSHINEI_CORS_ORIGINS", "").strip()
_cors_origins = (
    [o.strip() for o in _cors_env.split(",") if o.strip()]
    if _cors_env else
    [f"http://{host}:{port}"
     for host in ("localhost", "127.0.0.1")
     for port in ("8000", "8001")]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# Serve vendor JS/CSS
if STATIC.exists():
    app.mount("/vendor", StaticFiles(directory=str(STATIC)), name="vendor")


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------
_PRAGMAS = (
    "PRAGMA journal_mode=WAL;"
    "PRAGMA synchronous=NORMAL;"
    "PRAGMA cache_size=-65536;"      # ~64 MiB page cache (negative = KiB)
    "PRAGMA temp_store=MEMORY;"
    "PRAGMA mmap_size=268435456;"    # 256 MiB memory-mapped I/O
    "PRAGMA foreign_keys=ON;"
    "PRAGMA busy_timeout=5000;"
)

_tls = threading.local()

def _new_conn() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(_PRAGMAS)
    return con

@contextmanager
def get_db():
    """Per-thread persistent connection — avoids reconnect + PRAGMA cost per request."""
    con = getattr(_tls, "con", None)
    if con is None:
        con = _new_conn()
        _tls.con = con
    try:
        yield con
    except sqlite3.Error:
        try:
            con.close()
        except Exception:
            pass
        _tls.con = None
        raise


# ---------------------------------------------------------------------------
# Tier SKU codes to exclude from BoM cost (seat-tier pricing; included separately)
# Phase 18d 2026-05-28: added 'BUFFET' (plain, legacy CM-era code that still
# appears in some Easy bills). Was leaking ~825M phantom COGS at insight/MTD
# endpoints that referenced this constant. See _EASY_TIER_CODES at line ~5211
# for the canonical SQL constant used by all helper-based queries.
# ---------------------------------------------------------------------------
TIER_CODES: tuple = (
    'BUFFET', 'BUFFET350', 'BUFFET500', 'BUFFET590', 'BUFFET799',
    'BUFFETCHILD', 'B01', 'B02', 'B07', 'B11',
)

# ---------------------------------------------------------------------------
# Month-range filter helper
# ---------------------------------------------------------------------------
def _month_range_filter(
    filters: list, params: dict,
    month_from: Optional[str], month_to: Optional[str],
    month: Optional[str] = None,
    col: str = "period_month",
):
    """Adds BETWEEN-style filters. `month` acts as both from and to (backward compat)."""
    mf = month_from or month
    mt = month_to   or month
    if mf:
        filters.append(f"{col} >= :month_from")
        params["month_from"] = mf
    if mt:
        filters.append(f"{col} <= :month_to")
        params["month_to"] = mt


# ---------------------------------------------------------------------------
# Root  -  serve dashboard HTML
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def root():
    if INDEX.exists():
        return FileResponse(str(INDEX))
    return {"message": "Oshinei API running. Dashboard file not found."}


# ---------------------------------------------------------------------------
# /api/branches
# ---------------------------------------------------------------------------
@app.get("/api/branches")
def list_branches(
    group: Optional[str] = Query(None, description="'master' | 'franchise' | None"),
    include_inactive: bool = Query(False),
):
    """All branch names (excluding the synthetic ALL branch). Optionally filter by branch_type."""
    sql = "SELECT branch_id, name, COALESCE(branch_type,'master') AS branch_type, COALESCE(active,1) AS active FROM branches WHERE name != 'ALL'"
    params: list = []
    if not include_inactive:
        sql += " AND COALESCE(active,1) = 1"
    if group:
        sql += " AND COALESCE(branch_type,'master') = ?"
        params.append(group)
    sql += " ORDER BY name"
    with get_db() as con:
        rows = con.execute(sql, params).fetchall()
    return [{"id": r["branch_id"], "name": r["name"], "branch_type": r["branch_type"], "active": bool(r["active"])} for r in rows]


class BranchTypeUpdate(BaseModel):
    branch_type: str   # 'master' | 'franchise'


@app.put("/api/branches/{branch_id}/type")
def update_branch_type(branch_id: int, body: BranchTypeUpdate):
    if body.branch_type not in ("master", "franchise"):
        raise HTTPException(400, "branch_type must be 'master' or 'franchise'")
    with get_db() as con:
        con.execute("UPDATE branches SET branch_type=? WHERE branch_id=?", (body.branch_type, branch_id))
        con.commit()
    return {"ok": True}


def _group_filter(filters: list, params: dict, group: Optional[str]):
    """Adds a branch-type filter (b.branch_type = :group). Caller must JOIN branches as b."""
    if group:
        filters.append("COALESCE(b.branch_type,'master') = :branch_group")
        params["branch_group"] = group


# ---------------------------------------------------------------------------
# /api/months
# ---------------------------------------------------------------------------
@app.get("/api/months")
def list_months(branch: Optional[str] = Query(None)):
    """
    All available YYYY-MM periods across sales_sku_monthly AND sales_month_summary.
    Optionally filtered by branch name.
    """
    with get_db() as con:
        if branch:
            rows = con.execute(
                """
                SELECT DISTINCT period_month FROM (
                  SELECT sm.period_month
                  FROM sales_sku_monthly sm
                  JOIN branches b ON b.branch_id = sm.branch_id
                  WHERE b.name = ?
                  UNION
                  SELECT sms.period_month
                  FROM sales_month_summary sms
                  JOIN branches b ON b.branch_id = sms.branch_id
                  WHERE b.name = ? AND b.name != 'ALL'
                )
                ORDER BY period_month
                """,
                (branch, branch),
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT DISTINCT period_month FROM (
                  SELECT period_month FROM sales_sku_monthly
                  UNION
                  SELECT sms.period_month
                  FROM sales_month_summary sms
                  JOIN branches b ON b.branch_id = sms.branch_id
                  WHERE b.name != 'ALL'
                )
                ORDER BY period_month
                """
            ).fetchall()
    return [r["period_month"] for r in rows]


# ---------------------------------------------------------------------------
# /api/sales/monthly   -  monthly revenue trend
# ---------------------------------------------------------------------------
@app.get("/api/sales/monthly")
@ttl_cache
def sales_monthly(
    branch:      Optional[str] = Query(None),
    group:       Optional[str] = Query(None),
    source:      Optional[str] = Query(None),
    month:       Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    date_from:   Optional[str] = Query(None),   # YYYY-MM-DD  for daily/weekly
    date_to:     Optional[str] = Query(None),   # YYYY-MM-DD  for daily/weekly
    granularity: str           = Query("monthly"),  # daily|weekly|monthly|quarterly
):
    """
    Revenue trend at the requested granularity.
    daily/weekly  â†' sales_transaction  (date_from / date_to)
    monthly       â†' sales_sku_monthly + CM POS fallback  (month_from / month_to)
    quarterly     â†' sales_sku_monthly aggregated to quarters
    """
    # â"€â"€ daily â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    if granularity == "daily":
        filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
        params: dict = {}
        if branch:
            filters.append("b.name = :branch")
            params["branch"] = branch
        if date_from:
            filters.append("DATE(st.sold_at) >= :date_from")
            params["date_from"] = date_from
        if date_to:
            filters.append("DATE(st.sold_at) <= :date_to")
            params["date_to"] = date_to
        where = "WHERE " + " AND ".join(filters)
        with get_db() as con:
            rows = con.execute(
                f"""
                SELECT DATE(st.sold_at)              AS period_month,
                       ROUND(SUM(st.total_inc_vat) / 1.07,2) AS revenue,
                       COUNT(*)                       AS tx_count,
                       SUM(st.customer_count)         AS customer_count
                FROM sales_transaction st
                JOIN branches b ON b.branch_id = st.branch_id
                {where}
                GROUP BY DATE(st.sold_at)
                ORDER BY period_month
                """,
                params,
            ).fetchall()
        return [{"period_month": r["period_month"], "revenue": r["revenue"],
                 "tx_count": r["tx_count"], "customer_count": r["customer_count"],
                 "source": "Easy"} for r in rows]

    # â"€â"€ weekly â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    if granularity == "weekly":
        filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
        params = {}
        if branch:
            filters.append("b.name = :branch")
            params["branch"] = branch
        if date_from:
            filters.append("DATE(st.sold_at) >= :date_from")
            params["date_from"] = date_from
        if date_to:
            filters.append("DATE(st.sold_at) <= :date_to")
            params["date_to"] = date_to
        where = "WHERE " + " AND ".join(filters)
        _wk = _iso_week_sql('st.sold_at')
        with get_db() as con:
            rows = con.execute(
                f"""
                SELECT {_wk}                            AS period_month,
                       MIN(DATE(st.sold_at))            AS week_start,
                       ROUND(SUM(st.total_inc_vat) / 1.07, 2)  AS revenue,
                       COUNT(*)                         AS tx_count,
                       SUM(st.customer_count)           AS customer_count
                FROM sales_transaction st
                JOIN branches b ON b.branch_id = st.branch_id
                {where}
                GROUP BY {_wk}
                ORDER BY period_month
                """,
                params,
            ).fetchall()
        return [{"period_month": r["period_month"], "week_start": r["week_start"],
                 "revenue": r["revenue"], "tx_count": r["tx_count"],
                 "customer_count": r["customer_count"], "source": "Easy"} for r in rows]

    # â"€â"€ quarterly â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    if granularity == "quarterly":
        filters = ["b.name != 'ALL'"]
        params = {}
        if branch:
            filters.append("b.name = :branch")
            params["branch"] = branch
        _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")
        where = "WHERE " + " AND ".join(filters)
        with get_db() as con:
            rows = con.execute(
                f"""
                SELECT qtr AS period_month,
                       ROUND(SUM(rev_ex_vat), 2)   AS revenue,
                       ROUND(SUM(rev_ex_vat), 2)   AS revenue_ex_vat,
                       ROUND(SUM(rev_inc_vat), 2)  AS pos_total_inc_vat_diagnostic
                FROM (
                    SELECT
                        substr(sm.period_month,1,4) || '-Q' ||
                        ((CAST(substr(sm.period_month,6,2) AS INTEGER)-1)/3+1) AS qtr,
                        sm.total_inc_vat   AS rev_inc_vat,
                        sm.subtotal_ex_vat AS rev_ex_vat
                    FROM sales_sku_monthly sm
                    JOIN branches b ON b.branch_id = sm.branch_id
                    {where}
                ) sub
                GROUP BY qtr
                ORDER BY qtr
                """,
                params,
            ).fetchall()
        return [{"period_month": r["period_month"], "revenue": r["revenue"],
                 "revenue_ex_vat": r["revenue_ex_vat"], "source": "Easy"} for r in rows]

    # -- yearly (YoY comparison: returns (year, month_num) rows, NO month range filter) --
    if granularity == "yearly":
        filters_y = ["b.name != 'ALL'"]
        params_y: dict = {}
        if branch:
            filters_y.append("b.name = :branch")
            params_y["branch"] = branch
        _group_filter(filters_y, params_y, group)
        where_y = "WHERE " + " AND ".join(filters_y)
        with get_db() as con:
            rows = con.execute(
                f"""
                SELECT sm.period_month                       AS period_month,
                       substr(sm.period_month, 1, 4)          AS year,
                       substr(sm.period_month, 6, 2)          AS month_num,
                       ROUND(SUM(sm.subtotal_ex_vat), 2)      AS revenue,
                       ROUND(SUM(sm.subtotal_ex_vat), 2)      AS revenue_ex_vat,
                       ROUND(SUM(sm.total_inc_vat), 2)        AS pos_total_inc_vat_diagnostic
                FROM sales_sku_monthly sm
                JOIN branches b ON b.branch_id = sm.branch_id
                {where_y}
                GROUP BY sm.period_month
                ORDER BY sm.period_month
                """,
                params_y,
            ).fetchall()
            # Tx + customer counts from sales_transaction
            tx_rows_y = con.execute(
                f"""
                SELECT strftime('%Y-%m', st.sold_at) AS period_month,
                       COUNT(*)                       AS tx_count,
                       SUM(st.customer_count)         AS customer_count
                FROM sales_transaction st
                JOIN branches b ON b.branch_id = st.branch_id
                {where_y.replace('b.name', 'b.name')}
                GROUP BY strftime('%Y-%m', st.sold_at)
                """,
                params_y,
            ).fetchall()
        tx_map_y = {r["period_month"]: dict(r) for r in tx_rows_y}
        out = []
        for r in rows:
            tx = tx_map_y.get(r["period_month"], {})
            out.append({
                "period_month":   r["period_month"],
                "year":           r["year"],
                "month_num":      r["month_num"],
                "revenue":        r["revenue"],
                "revenue_ex_vat": r["revenue_ex_vat"],
                "tx_count":       tx.get("tx_count", 0),
                "customer_count": tx.get("customer_count", 0),
                "source":         "Easy",
            })
        return out

    # -- monthly (default) --
    with get_db() as con:
        filters_sku: list = ["b.name != 'ALL'"]
        params_sku: dict = {}
        if branch:
            filters_sku.append("b.name = :branch")
            params_sku["branch"] = branch
        _group_filter(filters_sku, params_sku, group)
        if source:
            filters_sku.append("sm.source = :source")
            params_sku["source"] = source
        _month_range_filter(filters_sku, params_sku, month_from, month_to, month, col="sm.period_month")
        where_sku = "WHERE " + " AND ".join(filters_sku)

        # Revenue from Easy SKU data
        sku_rows = con.execute(
            f"""
            SELECT sm.period_month,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)  AS revenue,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)  AS revenue_ex_vat,
                   ROUND(SUM(sm.total_inc_vat), 2)    AS pos_total_inc_vat_diagnostic
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            {where_sku}
            GROUP BY sm.period_month
            ORDER BY sm.period_month
            """,
            params_sku,
        ).fetchall()
        sku_months = {r["period_month"] for r in sku_rows}

        # CM POS months not already covered by Easy SKU data
        filters_sms: list = ["b.name != 'ALL'", "sms.source = 'CM POS'"]
        params_sms: dict = {}
        if branch:
            filters_sms.append("b.name = :branch")
            params_sms["branch"] = branch
        _group_filter(filters_sms, params_sms, group)
        _month_range_filter(filters_sms, params_sms, month_from, month_to, month, col="sms.period_month")
        where_sms = "WHERE " + " AND ".join(filters_sms)

        sms_rows = con.execute(
            f"""
            SELECT sms.period_month,
                   ROUND(SUM(sms.net_sales), 2)   AS revenue,
                   ROUND(SUM(sms.net_sales), 2)   AS revenue_ex_vat,
                   SUM(sms.transaction_count)      AS tx_count,
                   SUM(sms.customer_count)         AS customer_count
            FROM sales_month_summary sms
            JOIN branches b ON b.branch_id = sms.branch_id
            {where_sms}
            GROUP BY sms.period_month
            ORDER BY sms.period_month
            """,
            params_sms,
        ).fetchall()

        # Transaction counts from sales_transaction (Easy data)
        filters_tx: list = ["b.name != 'ALL'"]
        params_tx: dict = {}
        if branch:
            filters_tx.append("b.name = :branch")
            params_tx["branch"] = branch
        _group_filter(filters_tx, params_tx, group)
        if source:
            filters_tx.append("st.source = :source")
            params_tx["source"] = source
        _month_range_filter(filters_tx, params_tx, month_from, month_to, month, col="strftime('%Y-%m', st.sold_at)")
        where_tx = "WHERE " + " AND ".join(filters_tx)

        tx_rows = con.execute(
            f"""
            SELECT strftime('%Y-%m', st.sold_at) AS period_month,
                   COUNT(*) AS tx_count,
                   SUM(st.customer_count) AS customer_count
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
            GROUP BY strftime('%Y-%m', st.sold_at)
            ORDER BY period_month
            """,
            params_tx,
        ).fetchall()

    tx_map = {r["period_month"]: dict(r) for r in tx_rows}
    sms_map = {r["period_month"]: dict(r) for r in sms_rows}

    # Union of all months that have any data
    all_months = sorted(set(r["period_month"] for r in sku_rows) | set(sms_map.keys()))
    sku_map = {r["period_month"]: dict(r) for r in sku_rows}

    result = []
    for mo in all_months:
        sku = sku_map.get(mo, {})
        tx  = tx_map.get(mo, {})
        cm  = sms_map.get(mo, {})

        # Revenue: prefer SKU-level (more detailed); else fall back to CM POS net_sales
        revenue        = sku.get("revenue")        if sku else None
        revenue_ex_vat = sku.get("revenue_ex_vat") if sku else None
        if revenue is None:
            revenue        = cm.get("revenue") or cm.get("net_sales") or 0
            revenue_ex_vat = cm.get("revenue_ex_vat") or cm.get("net_sales") or 0

        # Tx/customers: Easy + CM POS (different branches, no overlap)
        easy_tx   = (tx.get("tx_count")       or 0)
        easy_cust = (tx.get("customer_count") or 0)
        cm_tx     = (cm.get("tx_count")       or 0)
        cm_cust   = (cm.get("customer_count") or 0)
        total_tx   = easy_tx + cm_tx
        total_cust = easy_cust + cm_cust

        # Source label: which side dominates (for badging on chart)
        if easy_tx > 0 and cm_tx > 0:
            src = "Mixed"
        elif cm_tx > 0:
            src = "CM POS"
        else:
            src = "Easy"

        result.append({
            "period_month":   mo,
            "revenue":        revenue,
            "revenue_ex_vat": revenue_ex_vat,
            "tx_count":       total_tx if total_tx else None,
            "customer_count": total_cust if total_cust else None,
            "source":         src,
        })

    return result


# ---------------------------------------------------------------------------
# /api/sales/category   -  revenue by category for a month/range
# ---------------------------------------------------------------------------
@app.get("/api/sales/category")
def sales_by_category(
    month:      Optional[str] = Query(None, description="YYYY-MM; omit for all-time"),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    source:     Optional[str] = Query(None),
):
    """Revenue breakdown by category."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}

    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")
    if source:
        filters.append("sm.source = :source")
        params["source"] = source

    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT c.name AS category,
                   ROUND(SUM(sm.subtotal_ex_vat), 2) AS revenue,
                   ROUND(SUM(sm.qty), 2)            AS qty
            FROM sales_sku_monthly sm
            JOIN branches b    ON b.branch_id    = sm.branch_id
            JOIN skus sk       ON sk.sku_id       = sm.sku_id
            JOIN categories c  ON c.category_id   = sk.category_id
            {where}
            GROUP BY c.name
            ORDER BY revenue DESC
            """,
            params,
        ).fetchall()

    return [{"category": r["category"], "revenue": r["revenue"], "qty": r["qty"]} for r in rows]


# ---------------------------------------------------------------------------
# /api/sales/skus   -  top SKUs
# ---------------------------------------------------------------------------
@app.get("/api/sales/skus")
def top_skus(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    source:     Optional[str] = Query(None),
    category:   Optional[str] = Query(None),
    limit:      int           = Query(20, ge=1, le=200),
):
    """Top SKUs by revenue, with cost and margin if sku_cost is populated."""
    filters = ["b.name != 'ALL'", "COALESCE(b.active,1) = 1", "COALESCE(sk.active,1) = 1"]
    params: dict = {}

    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")
    if source:
        filters.append("sm.source = :source")
        params["source"] = source
    if category:
        filters.append("c.name = :category")
        params["category"] = category

    where = "WHERE " + " AND ".join(filters)
    params["limit"] = limit

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT sk.sku_code,
                   sk.description,
                   c.name                            AS category,
                   sk.unit,
                   ROUND(SUM(sm.qty), 2)             AS qty,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)   AS revenue,
                   ROUND(SUM(sm.subtotal_ex_vat), 2) AS revenue_ex_vat,
                   SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}) AS cogs_total,
                   SUM(CASE WHEN {_effective_cost_subquery(ssm='sm', sc='sc')} IS NOT NULL
                            THEN sm.qty ELSE 0 END)              AS costed_qty
            FROM sales_sku_monthly sm
            JOIN branches b    ON b.branch_id   = sm.branch_id
            JOIN skus sk       ON sk.sku_id      = sm.sku_id
            JOIN categories c  ON c.category_id  = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id   = sm.sku_id
            {where}
            GROUP BY sk.sku_id
            ORDER BY revenue DESC
            LIMIT :limit
            """,
            params,
        ).fetchall()

    result = []
    for r in rows:
        cogs_total = r["cogs_total"]
        costed_qty = r["costed_qty"] or 0
        qty       = r["qty"] or 0.0
        rev_ex    = r["revenue_ex_vat"] or 0.0
        has_cost  = cogs_total is not None and costed_qty > 0
        cost      = (cogs_total / costed_qty) if has_cost else None
        cogs      = round(cogs_total, 2) if has_cost else None
        margin    = round(rev_ex - cogs, 2) if cogs is not None else None
        margin_pct = round(margin / rev_ex * 100, 1) if (margin is not None and rev_ex) else None
        result.append({
            "sku_code":    r["sku_code"],
            "description": r["description"],
            "category":    r["category"],
            "unit":        r["unit"],
            "qty":         qty,
            "revenue":     r["revenue"],
            "unit_cost":   cost,
            "cogs":        cogs,
            "margin":      margin,
            "margin_pct":  margin_pct,
        })
    return result


# ---------------------------------------------------------------------------
# /api/sales/payment   -  payment method breakdown
# ---------------------------------------------------------------------------
@app.get("/api/sales/payment")
def payment_breakdown(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    source:     Optional[str] = Query(None),
):
    """Payment method totals from sales_month_summary.

    branch='ALL' or omitted → aggregate across all real branches.
    (The literal 'ALL' branch row from old CM grand-summary was removed when
    CM data was archived 2026-05-27.)
    """
    filters = []
    params: dict = {}

    if branch and branch.upper() != "ALL":
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="sms.period_month")
    if source:
        filters.append("sms.source = :source")
        params["source"] = source

    where = ("WHERE " + " AND ".join(filters)) if filters else ""

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT sms.period_month,
                   b.name AS branch,
                   ROUND(SUM(sms.cash), 2)        AS cash,
                   ROUND(SUM(sms.credit_card), 2) AS credit_card,
                   ROUND(SUM(sms.transfer), 2)    AS transfer,
                   ROUND(SUM(sms.qr), 2)          AS qr,
                   ROUND(SUM(sms.coupon), 2)      AS coupon,
                   ROUND(SUM(sms.gross_sales), 2) AS gross_sales,
                   ROUND(SUM(sms.net_sales), 2)   AS net_sales,
                   ROUND(SUM(sms.discounts), 2)   AS discounts,
                   ROUND(SUM(sms.refunds), 2)     AS refunds,
                   SUM(sms.customer_count)        AS customer_count,
                   SUM(sms.transaction_count)     AS transaction_count
            FROM sales_month_summary sms
            JOIN branches b ON b.branch_id = sms.branch_id
            {where}
            GROUP BY sms.period_month, b.name
            ORDER BY sms.period_month
            """,
            params,
        ).fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# /api/sales/transactions   -  paginated transaction list
# ---------------------------------------------------------------------------
@app.get("/api/sales/transactions")
def list_transactions(
    month: Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Paginated per-receipt transaction rows."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}

    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    if month:
        filters.append("strftime('%Y-%m', st.sold_at) = :month")
        params["month"] = month
    if source:
        filters.append("st.source = :source")
        params["source"] = source

    where = "WHERE " + " AND ".join(filters)
    params["limit"]  = limit
    params["offset"] = offset

    with get_db() as con:
        total = con.execute(
            f"""
            SELECT COUNT(*)
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            """,
            {k: v for k, v in params.items() if k not in ("limit", "offset")},
        ).fetchone()[0]

        rows = con.execute(
            f"""
            SELECT st.receipt_no, st.sold_at, b.name AS branch,
                   st.cashier, st.table_no, st.customer_count,
                   st.food_subtotal, st.drink_subtotal, st.other_subtotal,
                   st.tip, st.sp_discount, st.discount, st.service_charge,
                   st.total_inc_vat, st.source
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            ORDER BY st.sold_at DESC
            LIMIT :limit OFFSET :offset
            """,
            params,
        ).fetchall()

    return {"total": total, "offset": offset, "rows": [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# /api/summary   -  single-call KPI card data
# ---------------------------------------------------------------------------
@app.get("/api/summary")
def summary(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
):
    """
    High-level KPIs for the header cards:
    total revenue, transaction count, avg ticket, customer count.
    If month is omitted, returns all-time totals.
    """
    filters_sku = ["b.name != 'ALL'"]
    filters_tx  = ["b.name != 'ALL'"]
    params_sku: dict = {}
    params_tx:  dict = {}

    if branch:
        filters_sku.append("b.name = :branch")
        filters_tx.append("b.name = :branch")
        params_sku["branch"] = branch
        params_tx["branch"]  = branch
    _group_filter(filters_sku, params_sku, group)
    _group_filter(filters_tx,  params_tx,  group)
    _month_range_filter(filters_sku, params_sku, month_from, month_to, month, col="sm.period_month")
    _month_range_filter(filters_tx,  params_tx,  month_from, month_to, month, col="strftime('%Y-%m', st.sold_at)")

    where_sku = "WHERE " + " AND ".join(filters_sku)
    where_tx  = "WHERE " + " AND ".join(filters_tx)

    # month_summary filter (ALL sources — Easy + CM POS — for revenue fallback)
    filters_ms = ["b.name != 'ALL'"]
    params_ms: dict = {}
    if branch:
        filters_ms.append("b.name = :branch")
        params_ms["branch"] = branch
    _group_filter(filters_ms, params_ms, group)
    _month_range_filter(filters_ms, params_ms, month_from, month_to, month, col="sms.period_month")
    where_ms = "WHERE " + " AND ".join(filters_ms)

    with get_db() as con:
        # SKU-level revenue per (branch, month) — primary, preferred when available
        sku_rev_rows = con.execute(
            f"""
            SELECT b.name AS branch, sm.period_month AS m,
                   SUM(sm.total_inc_vat) AS rev_inc,
                   SUM(sm.subtotal_ex_vat) AS rev_ex
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            {where_sku}
            GROUP BY b.name, sm.period_month
            """,
            params_sku,
        ).fetchall()

        # Month summary as FALLBACK for branches/months not in sku_monthly
        ms_rev_rows = con.execute(
            f"""
            SELECT b.name AS branch, sms.period_month AS m,
                   sms.source AS src,
                   SUM(sms.net_sales) AS net,
                   SUM(sms.transaction_count) AS tx,
                   SUM(sms.customer_count) AS cust
            FROM sales_month_summary sms
            JOIN branches b ON b.branch_id = sms.branch_id
            {where_ms}
            GROUP BY b.name, sms.period_month, sms.source
            """,
            params_ms,
        ).fetchall()

        # Easy transactions (Easy-only)
        easy_tx_rows = con.execute(
            f"""
            SELECT b.name AS branch, strftime('%Y-%m', st.sold_at) AS m,
                   COUNT(*) AS tx, SUM(customer_count) AS cust,
                   SUM(total_inc_vat) AS rev_inc
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
            GROUP BY b.name, strftime('%Y-%m', st.sold_at)
            """,
            params_tx,
        ).fetchall()

    # Build per-(branch, month) merged map
    sku_map = {(r["branch"], r["m"]): {"rev_inc": r["rev_inc"] or 0, "rev_ex": r["rev_ex"] or 0} for r in sku_rev_rows}
    easy_tx_map = {(r["branch"], r["m"]): {"tx": r["tx"] or 0, "cust": r["cust"] or 0, "rev_inc": r["rev_inc"] or 0} for r in easy_tx_rows}

    # Aggregate totals
    total_rev_inc = 0.0
    total_rev_ex  = 0.0
    total_tx      = 0
    total_cust    = 0

    # Track (branch, month) we've covered to avoid double-count
    covered = set()
    for (br, m), v in sku_map.items():
        total_rev_inc += v["rev_inc"]
        total_rev_ex  += v["rev_ex"]
        covered.add((br, m))

    for r in ms_rev_rows:
        k = (r["branch"], r["m"])
        if k in covered:
            continue  # sku_monthly already counted this branch×month
        # Use month_summary as fallback — net_sales is post-discount; treat as inc-VAT and divide by 1.07 for ex-VAT
        total_rev_inc += r["net"] or 0
        total_rev_ex  += (r["net"] or 0) / 1.07
        covered.add(k)

    # Tx + customers
    # For each (br, m): use easy_tx if available, otherwise use ms tx (only CM POS source — Easy aggregates
    # would double-count Easy transactions we already have)
    ms_tx_by_key = {}
    for r in ms_rev_rows:
        k = (r["branch"], r["m"])
        ms_tx_by_key.setdefault(k, []).append({"src": r["src"], "tx": r["tx"] or 0, "cust": r["cust"] or 0})

    all_keys = set(easy_tx_map.keys()) | set(ms_tx_by_key.keys())
    for k in all_keys:
        e = easy_tx_map.get(k)
        if e and e["tx"] > 0:
            total_tx   += e["tx"]
            total_cust += e["cust"]
            continue
        # No Easy tx — try CM POS aggregate
        for ms in ms_tx_by_key.get(k, []):
            if ms["src"] == "CM POS":
                total_tx   += ms["tx"]
                total_cust += ms["cust"]
                break
        else:
            # No CM POS either, but maybe Easy aggregate row exists (sometimes Easy POS only loaded monthly)
            for ms in ms_tx_by_key.get(k, []):
                if ms["src"] == "Easy":
                    total_tx   += ms["tx"]
                    total_cust += ms["cust"]
                    break

    # Hard rule: VAT is never revenue. Headline revenue + avg ticket are ex-VAT.
    avg_ticket = round(total_rev_ex / total_tx, 2) if total_tx else 0

    return {
        "revenue":         round(total_rev_ex, 2),   # ex-VAT (headline)
        "revenue_ex_vat":  round(total_rev_ex, 2),   # back-compat alias
        "revenue_inc_vat": round(total_rev_inc, 2),  # POS reconciliation only
        "tx_count":        total_tx,
        "customer_count":  total_cust,
        "avg_ticket":      avg_ticket,                # ex-VAT
    }


# ---------------------------------------------------------------------------
# /api/cost   -  GET: list all SKUs with current cost; POST: upsert costs
# ---------------------------------------------------------------------------
class CostEntry(BaseModel):
    sku_code: str
    unit_cost: float


@app.get("/api/cost")
def get_cost():
    """All SKUs with their current unit_cost (NULL if not yet set). Sorted category, sku_code."""
    with get_db() as con:
        rows = con.execute(
            """
            SELECT sk.sku_id, sk.sku_code, sk.description,
                   c.name AS category,
                   sc.unit_cost, sc.updated_at
            FROM skus sk
            JOIN categories c ON c.category_id = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id = sk.sku_id
            ORDER BY c.name, sk.sku_code
            """
        ).fetchall()
    return [
        {
            "sku_id":      r["sku_id"],
            "sku_code":    r["sku_code"],
            "description": r["description"],
            "category":    r["category"],
            "unit_cost":   r["unit_cost"],
            "updated_at":  r["updated_at"],
        }
        for r in rows
    ]


@app.post("/api/cost")
def post_cost(entries: List[CostEntry]):
    """Upsert unit costs by sku_code. Returns count of rows updated."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    updated = 0
    with get_db() as con:
        cur = con.cursor()
        for entry in entries:
            row = cur.execute(
                "SELECT sku_id FROM skus WHERE sku_code = ?", (entry.sku_code,)
            ).fetchone()
            if row is None:
                continue
            sku_id = row[0]
            cur.execute(
                """INSERT OR REPLACE INTO sku_cost(sku_id, unit_cost, effective_from, updated_at)
                   VALUES(?, ?, ?, ?)""",
                (sku_id, entry.unit_cost, now[:10], now),
            )
            updated += 1
        con.commit()
    invalidate_cache()   # cost data changed — clear all cached analytics responses
    return {"updated": updated}


# ---------------------------------------------------------------------------
# Phase 8 — monthly per-SKU unit_cost overrides
# ---------------------------------------------------------------------------
class MonthlyCostEntry(BaseModel):
    sku_code: str
    period_month: str        # 'YYYY-MM'
    unit_cost: float
    notes: Optional[str] = None


def _resolve_effective_monthly_cost(con, sku_id: int, month: str):
    """Python-side mirror of _effective_cost_subquery for a single (sku, month).

    Returns (unit_cost or None, source) where source ∈
    {'monthly','inherited','default','none'}.
    """
    row = con.execute(
        "SELECT unit_cost FROM sku_cost_monthly "
        "WHERE sku_id = ? AND period_month = ?",
        (sku_id, month),
    ).fetchone()
    if row is not None:
        return row[0], "monthly"
    row = con.execute(
        "SELECT unit_cost FROM sku_cost_monthly "
        "WHERE sku_id = ? AND period_month < ? "
        "ORDER BY period_month DESC LIMIT 1",
        (sku_id, month),
    ).fetchone()
    if row is not None:
        return row[0], "inherited"
    row = con.execute(
        "SELECT unit_cost FROM sku_cost WHERE sku_id = ?", (sku_id,)
    ).fetchone()
    if row is not None:
        return row[0], "default"
    return None, "none"


@app.get("/api/cost/monthly")
def get_cost_monthly(
    sku:         Optional[str] = Query(None, description="sku_code filter"),
    month:       Optional[str] = Query(None, description="YYYY-MM filter"),
    period_from: Optional[str] = Query(None),
    period_to:   Optional[str] = Query(None),
):
    """List monthly unit_cost overrides.

    - If ``sku`` is given → return every monthly row for that SKU plus the lifetime
      default (as a synthetic row with ``period_month=null`` and ``source='default'``).
    - If ``month`` is given (without sku) → for every costed SKU return its effective
      cost in that month with the source (monthly/inherited/default).
    - Otherwise → raw dump of sku_cost_monthly within the period_from/period_to window.
    """
    out = []
    with get_db() as con:
        if sku:
            sku_row = con.execute(
                "SELECT sku_id, description FROM skus WHERE sku_code = ?", (sku,)
            ).fetchone()
            if sku_row is None:
                raise HTTPException(404, f"Unknown sku_code: {sku}")
            sku_id  = sku_row[0]
            default_row = con.execute(
                "SELECT unit_cost FROM sku_cost WHERE sku_id = ?", (sku_id,)
            ).fetchone()
            default_cost = default_row[0] if default_row else None

            monthly_rows = con.execute(
                "SELECT period_month, unit_cost, notes, updated_at "
                "FROM sku_cost_monthly WHERE sku_id = ? "
                "ORDER BY period_month",
                (sku_id,),
            ).fetchall()
            for r in monthly_rows:
                out.append({
                    "sku_code":     sku,
                    "description":  sku_row[1],
                    "period_month": r[0],
                    "unit_cost":    r[1],
                    "notes":        r[2],
                    "updated_at":   r[3],
                    "default_cost": default_cost,
                    "source":       "monthly",
                })
            # always include the lifetime default as a reference row
            out.append({
                "sku_code":     sku,
                "description":  sku_row[1],
                "period_month": None,
                "unit_cost":    default_cost,
                "notes":        None,
                "updated_at":   None,
                "default_cost": default_cost,
                "source":       "default",
            })
            return out

        if month:
            rows = con.execute(
                """
                SELECT sk.sku_id, sk.sku_code, sk.description,
                       sc.unit_cost AS default_cost,
                       scm.unit_cost AS monthly_cost,
                       scm.notes, scm.updated_at
                FROM skus sk
                LEFT JOIN sku_cost sc          ON sc.sku_id = sk.sku_id
                LEFT JOIN sku_cost_monthly scm ON scm.sku_id = sk.sku_id
                                              AND scm.period_month = ?
                WHERE sc.unit_cost IS NOT NULL OR scm.unit_cost IS NOT NULL
                ORDER BY sk.sku_code
                """,
                (month,),
            ).fetchall()
            for r in rows:
                eff, src = _resolve_effective_monthly_cost(con, r["sku_id"], month)
                out.append({
                    "sku_code":     r["sku_code"],
                    "description":  r["description"],
                    "period_month": month,
                    "unit_cost":    eff,
                    "default_cost": r["default_cost"],
                    "notes":        r["notes"],
                    "updated_at":   r["updated_at"],
                    "source":       src,
                })
            return out

        # raw window dump
        where, params = [], {}
        if period_from:
            where.append("scm.period_month >= :pf"); params["pf"] = period_from
        if period_to:
            where.append("scm.period_month <= :pt"); params["pt"] = period_to
        wsql = ("WHERE " + " AND ".join(where)) if where else ""
        rows = con.execute(
            f"""
            SELECT sk.sku_code, sk.description, scm.period_month,
                   scm.unit_cost, scm.notes, scm.updated_at,
                   sc.unit_cost AS default_cost
            FROM sku_cost_monthly scm
            JOIN skus sk         ON sk.sku_id = scm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = scm.sku_id
            {wsql}
            ORDER BY scm.period_month, sk.sku_code
            """,
            params,
        ).fetchall()
        return [
            {
                "sku_code":     r["sku_code"],
                "description":  r["description"],
                "period_month": r["period_month"],
                "unit_cost":    r["unit_cost"],
                "default_cost": r["default_cost"],
                "notes":        r["notes"],
                "updated_at":   r["updated_at"],
                "source":       "monthly",
            }
            for r in rows
        ]


@app.post("/api/cost/monthly")
def post_cost_monthly(entries: List[MonthlyCostEntry]):
    """Upsert monthly per-SKU unit_cost overrides.

    Returns {written, unknown_skus, invalid_months}.
    """
    import re
    month_re = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
    written = 0
    unknown: List[str] = []
    invalid_months: List[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    with get_db() as con:
        cur = con.cursor()
        for e in entries:
            if not month_re.match(e.period_month or ""):
                invalid_months.append(f"{e.sku_code}:{e.period_month}")
                continue
            row = cur.execute(
                "SELECT sku_id FROM skus WHERE sku_code = ?", (e.sku_code,)
            ).fetchone()
            if row is None:
                unknown.append(e.sku_code)
                continue
            sku_id = row[0]
            cur.execute(
                """INSERT INTO sku_cost_monthly(sku_id, period_month, unit_cost, notes, updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(sku_id, period_month) DO UPDATE SET
                       unit_cost  = excluded.unit_cost,
                       notes      = excluded.notes,
                       updated_at = excluded.updated_at""",
                (sku_id, e.period_month, e.unit_cost, e.notes, now),
            )
            written += 1
        con.commit()
    invalidate_cache()
    return {
        "written":        written,
        "unknown_skus":   sorted(set(unknown)),
        "invalid_months": invalid_months,
    }


@app.delete("/api/cost/monthly")
def delete_cost_monthly(
    sku:   str = Query(..., description="sku_code"),
    month: str = Query(..., description="YYYY-MM"),
):
    """Remove a single monthly override row."""
    with get_db() as con:
        row = con.execute(
            "SELECT sku_id FROM skus WHERE sku_code = ?", (sku,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"Unknown sku_code: {sku}")
        cur = con.execute(
            "DELETE FROM sku_cost_monthly WHERE sku_id = ? AND period_month = ?",
            (row[0], month),
        )
        con.commit()
        deleted = cur.rowcount
    invalidate_cache()
    return {"deleted": deleted}


@app.get("/api/cost/monthly/template")
def cost_monthly_template(month: str = Query(..., description="YYYY-MM")):
    """CSV template of every costed SKU with its current default + monthly cost (if any).

    Columns: sku_code, description, default_unit_cost, monthly_unit_cost, notes
    """
    import io, csv, re
    if not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", month or ""):
        raise HTTPException(400, "month must be YYYY-MM")
    with get_db() as con:
        rows = con.execute(
            """
            SELECT sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   sc.unit_cost  AS default_unit_cost,
                   scm.unit_cost AS monthly_unit_cost,
                   scm.notes
            FROM skus sk
            LEFT JOIN sku_cost sc          ON sc.sku_id = sk.sku_id
            LEFT JOIN sku_cost_monthly scm ON scm.sku_id = sk.sku_id
                                          AND scm.period_month = ?
            WHERE sc.unit_cost IS NOT NULL OR scm.unit_cost IS NOT NULL
            ORDER BY sk.sku_code
            """,
            (month,),
        ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["sku_code", "description", "default_unit_cost", "monthly_unit_cost", "notes"])
    for r in rows:
        w.writerow([
            r["sku_code"],
            r["description"] or "",
            "" if r["default_unit_cost"] is None else r["default_unit_cost"],
            "" if r["monthly_unit_cost"] is None else r["monthly_unit_cost"],
            r["notes"] or "",
        ])
    from fastapi.responses import Response as _R
    return _R(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="cost_monthly_{month}.csv"'},
    )


# ---------------------------------------------------------------------------
# /api/sales/margin   -  per-category margin for a period
# ---------------------------------------------------------------------------
@app.get("/api/sales/margin")
@ttl_cache
def sales_margin(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
):
    """Per-category margin. Requires sku_cost to be populated."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}

    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")

    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT c.name AS category,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)                       AS revenue,
                   ROUND(SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}), 2) AS cost,
                   COUNT(DISTINCT CASE WHEN {_effective_cost_subquery(ssm='sm', sc='sc')} IS NULL
                                       THEN sk.sku_id END)                  AS uncosted_skus
            FROM sales_sku_monthly sm
            JOIN branches b   ON b.branch_id   = sm.branch_id
            JOIN skus sk      ON sk.sku_id      = sm.sku_id
            JOIN categories c ON c.category_id  = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id  = sm.sku_id
            {where}
            GROUP BY c.name
            ORDER BY revenue DESC
            """,
            params,
        ).fetchall()

        uncosted_total = con.execute(
            f"""
            SELECT COUNT(DISTINCT CASE WHEN {_effective_cost_subquery(ssm='sm', sc='sc')} IS NULL THEN sk.sku_id END)
            FROM sales_sku_monthly sm
            JOIN branches b   ON b.branch_id   = sm.branch_id
            JOIN skus sk      ON sk.sku_id      = sm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id  = sm.sku_id
            {where}
            """,
            params,
        ).fetchone()[0] or 0

    by_category = []
    total_rev = 0.0
    total_cost = 0.0

    for r in rows:
        rev  = r["revenue"] or 0.0
        cost = r["cost"] or 0.0
        margin = round(rev - cost, 2)
        margin_pct = round(margin / rev * 100, 1) if rev else 0.0
        total_rev  += rev
        total_cost += cost
        by_category.append({
            "category":     r["category"],
            "revenue":      rev,
            "cost":         cost,
            "margin":       margin,
            "margin_pct":   margin_pct,
            "uncosted_skus": r["uncosted_skus"] or 0,
        })

    total_rev   = round(total_rev, 2)
    total_cost  = round(total_cost, 2)
    total_margin = round(total_rev - total_cost, 2)
    total_pct    = round(total_margin / total_rev * 100, 1) if total_rev else 0.0

    return {
        "by_category": by_category,
        "total": {
            "revenue":    total_rev,
            "cost":       total_cost,
            "margin":     total_margin,
            "margin_pct": total_pct,
        },
        "uncosted_sku_count": uncosted_total,
    }


# ---------------------------------------------------------------------------
# /api/buffet/tier-mix
# ---------------------------------------------------------------------------
@app.get("/api/buffet/tier-mix")
@ttl_cache
def buffet_tier_mix(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
):
    """Revenue and GP breakdown by buffet tier per month."""
    filters: list = [
        "c.name = 'BUFFET'"
    ]
    params: dict = {}

    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch

    _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")

    where = "WHERE " + " AND ".join(filters) if filters else ""

    sql = f"""
        SELECT
            sm.period_month,
            sk.sku_code,
            sk.description,
            SUM(sm.qty)               AS qty,
            SUM(sm.subtotal_ex_vat)     AS revenue,
            SUM(sm.subtotal_ex_vat)   AS revenue_ex_vat,
            SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}) AS cogs_total,
            SUM(CASE WHEN {_effective_cost_subquery(ssm='sm', sc='sc')} IS NOT NULL
                     THEN sm.qty ELSE 0 END)                AS costed_qty
        FROM sales_sku_monthly sm
        JOIN branches b   ON b.branch_id = sm.branch_id
        JOIN skus sk      ON sk.sku_id   = sm.sku_id
        JOIN categories c ON c.category_id = sk.category_id
        LEFT JOIN sku_cost sc ON sc.sku_id = sm.sku_id
        {where}
        GROUP BY sm.period_month, sk.sku_code, sk.description
        ORDER BY sm.period_month, revenue DESC
    """

    with get_db() as con:
        rows = con.execute(sql, params).fetchall()

    # Build per-period detail + summary
    from collections import defaultdict
    period_rows: dict = defaultdict(list)
    period_totals: dict = defaultdict(lambda: {"qty": 0, "revenue": 0.0, "revenue_ex_vat": 0.0, "cogs": 0.0, "gp": None, "has_cost": True})

    result_rows = []
    for r in rows:
        qty          = r["qty"] or 0
        revenue      = round(r["revenue"] or 0.0, 2)
        rev_ex       = round(r["revenue_ex_vat"] or 0.0, 2)
        cogs_total   = r["cogs_total"]
        costed_qty   = r["costed_qty"] or 0
        has_cost     = cogs_total is not None and costed_qty > 0
        unit_cost    = (cogs_total / costed_qty) if has_cost else None
        cogs         = round(cogs_total, 2) if has_cost else None
        gp           = round(rev_ex - cogs, 2)   if cogs is not None else None
        gp_per_cover = round(gp / qty, 2)         if (gp is not None and qty) else None

        row_dict = {
            "period_month":  r["period_month"],
            "sku_code":      r["sku_code"],
            "description":   r["description"],
            "qty":           qty,
            "revenue":       revenue,
            "revenue_ex_vat": rev_ex,
            "unit_cost":     unit_cost,
            "cogs":          cogs,
            "gp":            gp,
            "gp_per_cover":  gp_per_cover,
        }
        result_rows.append(row_dict)

        pt = period_totals[r["period_month"]]
        pt["qty"]          += qty
        pt["revenue"]      += revenue
        pt["revenue_ex_vat"] += rev_ex
        if cogs is not None:
            pt["cogs"] += cogs
        else:
            pt["has_cost"] = False

    totals_by_period = []
    for pm, pt in sorted(period_totals.items()):
        cogs_total = round(pt["cogs"], 2) if pt["has_cost"] else None
        gp_total   = round(pt["revenue_ex_vat"] - cogs_total, 2) if cogs_total is not None else None
        totals_by_period.append({
            "period_month":  pm,
            "qty":           pt["qty"],
            "revenue":       round(pt["revenue"], 2),
            "revenue_ex_vat": round(pt["revenue_ex_vat"], 2),
            "cogs":          cogs_total,
            "gp":            gp_total,
        })

    return {"rows": result_rows, "totals_by_period": totals_by_period}


# ---------------------------------------------------------------------------
# /api/buffet/cover-economics
# ---------------------------------------------------------------------------
@app.get("/api/buffet/cover-economics")
@ttl_cache
def buffet_cover_economics(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    date_from:  Optional[str] = Query(None),
    date_to:    Optional[str] = Query(None),
):
    """Party-size distribution, hourly covers, and table-zone stats for buffet transactions."""
    tx_filters: list = [
        "t.customer_count BETWEEN 1 AND 20",
        "t.source = 'Easy'",
    ]
    tx_params: dict = {}

    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch

    # Date-range filter on sold_at
    if date_from or month_from or month:
        df = date_from or (month_from or month) + "-01"
        tx_filters.append("DATE(t.sold_at) >= :date_from")
        tx_params["date_from"] = df
    if date_to or month_to or month:
        # Use end-of-month if only month given
        dt_val = date_to
        if not dt_val:
            m = month_to or month
            import calendar as _cal
            y, mo = int(m[:4]), int(m[5:7])
            last_day = _cal.monthrange(y, mo)[1]
            dt_val = f"{m}-{last_day:02d}"
        tx_filters.append("DATE(t.sold_at) <= :date_to")
        tx_params["date_to"] = dt_val

    tx_where = "WHERE " + " AND ".join(tx_filters) if tx_filters else ""

    with get_db() as con:
        # 1. Party size distribution
        party_rows = con.execute(
            f"""
            SELECT
                t.customer_count                   AS party_size,
                COUNT(*)                           AS tx_count,
                SUM(t.total_inc_vat) / 1.07        AS revenue,
                AVG(t.total_inc_vat / 1.07 / t.customer_count) AS avg_revenue_per_cover,
                AVG(t.total_inc_vat / 1.07)        AS avg_ticket
            FROM sales_transaction t
            JOIN branches b ON b.branch_id = t.branch_id
            {tx_where}
            GROUP BY t.customer_count
            ORDER BY t.customer_count
            """,
            tx_params,
        ).fetchall()

        # 2. Hourly covers
        hourly_rows = con.execute(
            f"""
            SELECT
                CAST(strftime('%H', t.sold_at) AS INTEGER) AS hour,
                SUM(t.customer_count)                      AS total_covers,
                COUNT(*)                                   AS tx_count,
                AVG(t.customer_count)                      AS avg_covers_per_tx,
                SUM(t.total_inc_vat) / 1.07                AS revenue
            FROM sales_transaction t
            JOIN branches b ON b.branch_id = t.branch_id
            {tx_where}
            GROUP BY hour
            ORDER BY hour
            """,
            tx_params,
        ).fetchall()

        # 3. Table zone stats
        zone_rows = con.execute(
            f"""
            SELECT
                CASE
                    WHEN t.table_no LIKE 'T%' THEN 'Dine-in'
                    WHEN t.table_no LIKE 'G%' THEN 'VIP'
                    WHEN t.table_no LIKE 'C%' THEN 'Counter'
                    WHEN t.table_no LIKE 'Take%' THEN 'Takeaway'
                    ELSE 'Other'
                END AS zone,
                COUNT(*)              AS tx_count,
                SUM(t.customer_count) AS total_covers,
                SUM(t.total_inc_vat) / 1.07 AS revenue,
                AVG(t.total_inc_vat / 1.07) AS avg_ticket,
                AVG(t.customer_count) AS avg_covers
            FROM sales_transaction t
            JOIN branches b ON b.branch_id = t.branch_id
            {tx_where}
            GROUP BY zone
            ORDER BY revenue DESC
            """,
            tx_params,
        ).fetchall()

        # 4. Summary totals
        summary_row = con.execute(
            f"""
            SELECT
                AVG(t.customer_count)               AS avg_party_size,
                SUM(t.total_inc_vat) / 1.07 / NULLIF(SUM(t.customer_count), 0) AS avg_revenue_per_cover,
                SUM(t.customer_count)               AS total_covers,
                COUNT(*)                            AS total_tx
            FROM sales_transaction t
            JOIN branches b ON b.branch_id = t.branch_id
            {tx_where}
            """,
            tx_params,
        ).fetchone()

        # 5. Child cover estimate from sales_sku_monthly
        child_filters: list = []
        child_params: dict = {}
        if branch:
            child_filters.append("b.name = :branch")
            child_params["branch"] = branch
        _month_range_filter(child_filters, child_params, month_from, month_to, month, col="sm.period_month")
        child_filters.append(
            "(sk.sku_code LIKE 'B04%' OR sk.sku_code LIKE 'B05%' OR sk.sku_code LIKE 'B12%' "
            "OR sk.sku_code LIKE 'BUFFETCHILD%' "
            "OR LOWER(sk.description) LIKE LOWER('%child%') "
            "OR LOWER(sk.description) LIKE LOWER('%เด็ก%'))"
        )
        child_where = "WHERE " + " AND ".join(child_filters) if child_filters else ""
        child_row = con.execute(
            f"""
            SELECT SUM(sm.qty) AS child_qty
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            JOIN skus sk    ON sk.sku_id   = sm.sku_id
            {child_where}
            """,
            child_params,
        ).fetchone()

    party_size_dist = [
        {
            "party_size":            r["party_size"],
            "tx_count":              r["tx_count"],
            "revenue":               round(r["revenue"] or 0.0, 2),
            "avg_revenue_per_cover": round(r["avg_revenue_per_cover"] or 0.0, 2),
            "avg_ticket":            round(r["avg_ticket"] or 0.0, 2),
        }
        for r in party_rows
    ]

    hourly_covers = [
        {
            "hour":           r["hour"],
            "total_covers":   r["total_covers"] or 0,
            "tx_count":       r["tx_count"] or 0,
            "avg_covers_per_tx": round(r["avg_covers_per_tx"] or 0.0, 2),
            "revenue":        round(r["revenue"] or 0.0, 2),
        }
        for r in hourly_rows
    ]

    table_zone_stats = [
        {
            "zone":         r["zone"],
            "tx_count":     r["tx_count"] or 0,
            "total_covers": r["total_covers"] or 0,
            "revenue":      round(r["revenue"] or 0.0, 2),
            "avg_ticket":   round(r["avg_ticket"] or 0.0, 2),
            "avg_covers":   round(r["avg_covers"] or 0.0, 2),
        }
        for r in zone_rows
    ]

    summary = {
        "avg_party_size":        round(summary_row["avg_party_size"] or 0.0, 2),
        "avg_revenue_per_cover": round(summary_row["avg_revenue_per_cover"] or 0.0, 2),
        "total_covers":          summary_row["total_covers"] or 0,
        "total_tx":              summary_row["total_tx"] or 0,
        "child_cover_estimate":  child_row["child_qty"] or 0,
    }

    return {
        "party_size_dist":  party_size_dist,
        "hourly_covers":    hourly_covers,
        "table_zone_stats": table_zone_stats,
        "summary":          summary,
    }


# ---------------------------------------------------------------------------
# /api/buffet/revpash
# ---------------------------------------------------------------------------
@app.get("/api/buffet/revpash")
@ttl_cache
def buffet_revpash(
    branch:     Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    seats:      int           = Query(120),
):
    """Revenue per available seat-hour (RevPASH) by hour and day-of-week, dine-in only."""
    filters: list = [
        "(t.table_no LIKE 'T%' OR t.table_no LIKE 'G%')"
    ]
    params: dict = {}

    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch

    _month_range_filter(
        filters, params, month_from, month_to, month,
        col="strftime('%Y-%m', t.sold_at)",
    )

    where = "WHERE " + " AND ".join(filters) if filters else ""

    with get_db() as con:
        hourly_rows = con.execute(
            f"""
            SELECT
                CAST(strftime('%H', t.sold_at) AS INTEGER) AS hour,
                SUM(t.total_inc_vat) / 1.07                AS revenue,
                SUM(t.customer_count)                      AS covers,
                COUNT(*)                                   AS tx_count
            FROM sales_transaction t
            JOIN branches b ON b.branch_id = t.branch_id
            {where}
            GROUP BY hour
            ORDER BY hour
            """,
            params,
        ).fetchall()

        dow_rows = con.execute(
            f"""
            SELECT
                CAST(strftime('%w', t.sold_at) AS INTEGER) AS dow,
                SUM(t.total_inc_vat) / 1.07                AS revenue,
                SUM(t.customer_count)                      AS covers,
                COUNT(DISTINCT DATE(t.sold_at))            AS day_count
            FROM sales_transaction t
            JOIN branches b ON b.branch_id = t.branch_id
            {where}
            GROUP BY dow
            ORDER BY dow
            """,
            params,
        ).fetchall()

    # Build full 24-hour array (fill zeros for missing hours)
    hourly_map = {r["hour"]: r for r in hourly_rows}
    DOW_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

    hourly = []
    for h in range(24):
        r = hourly_map.get(h)
        rev     = round(r["revenue"] or 0.0, 2) if r else 0.0
        covers  = (r["covers"] or 0) if r else 0
        tx_cnt  = (r["tx_count"] or 0) if r else 0
        revpash = round(rev / seats, 4) if seats else 0.0
        hourly.append({
            "hour":    h,
            "revenue": rev,
            "covers":  covers,
            "tx_count": tx_cnt,
            "revpash": revpash,
        })

    dow = []
    for r in dow_rows:
        d         = r["dow"]
        rev       = round(r["revenue"] or 0.0, 2)
        covers    = r["covers"] or 0
        day_count = r["day_count"] or 1
        avg_rev   = round(rev / day_count, 2)
        avg_rph   = round(avg_rev / seats, 4) if seats else 0.0
        dow.append({
            "dow":          d,
            "label":        DOW_LABELS[d],
            "revenue":      rev,
            "covers":       covers,
            "avg_revpash":  avg_rph,
        })

    # Peak / dead hours
    non_zero = [h for h in hourly if h["revpash"] > 0]
    peak_hour = max(non_zero, key=lambda h: h["revpash"])["hour"] if non_zero else None
    dead_hour = min(non_zero, key=lambda h: h["revpash"])["hour"] if non_zero else None

    return {
        "hourly":    hourly,
        "dow":       dow,
        "seats":     seats,
        "peak_hour": peak_hour,
        "dead_hour": dead_hour,
    }


# ---------------------------------------------------------------------------
# /api/buffet/upgrade-simulator
# ---------------------------------------------------------------------------
@app.get("/api/buffet/upgrade-simulator")
@ttl_cache
def buffet_upgrade_simulator(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    shift_pct:  float         = Query(10.0),
):
    """What-if simulator: shift shift_pct% of 590-tier covers to 799-tier."""
    filters: list = ["c.name = 'BUFFET'"]
    params: dict = {}

    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch

    _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")

    where = "WHERE " + " AND ".join(filters) if filters else ""

    sql = f"""
        SELECT
            sk.sku_code,
            sk.description,
            SUM(sm.qty)             AS qty,
            SUM(sm.subtotal_ex_vat)   AS revenue,
            SUM(sm.subtotal_ex_vat) AS revenue_ex_vat,
            SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}) AS cogs_total,
            SUM(CASE WHEN {_effective_cost_subquery(ssm='sm', sc='sc')} IS NOT NULL
                     THEN sm.qty ELSE 0 END)               AS costed_qty
        FROM sales_sku_monthly sm
        JOIN branches b   ON b.branch_id  = sm.branch_id
        JOIN skus sk      ON sk.sku_id    = sm.sku_id
        JOIN categories c ON c.category_id = sk.category_id
        LEFT JOIN sku_cost sc ON sc.sku_id = sm.sku_id
        {where}
        GROUP BY sk.sku_code, sk.description
        ORDER BY sk.sku_code
    """

    with get_db() as con:
        rows = con.execute(sql, params).fetchall()

    # Map sku_code to tier price label
    def _tier_label(sku_code: str, description: str) -> str:
        code = sku_code.upper()
        desc = description.upper() if description else ""
        if code in ("B04", "B05", "B12") or "BUFFETCHILD" in code or "CHILD" in desc or "เด็ก" in desc:
            return "child"
        if code == "B01" or "350" in code or "350" in desc:
            return "350"
        if code == "B07" or "500" in code or "500" in desc:
            return "500"
        if code == "B02" or "590" in code or "590" in desc:
            return "590"
        if code == "B11" or "799" in code or "799" in desc:
            return "799"
        if code == "B03" or "1150" in code or "1150" in desc:
            return "1150"
        return code  # fallback: use sku_code itself

    # Aggregate by tier
    from collections import defaultdict
    tier_data: dict = defaultdict(lambda: {"qty": 0, "revenue": 0.0, "revenue_ex_vat": 0.0, "cogs": 0.0, "has_cost": True, "unit_cost": None})

    for r in rows:
        label      = _tier_label(r["sku_code"], r["description"])
        qty        = r["qty"] or 0
        revenue    = r["revenue"] or 0.0
        rev_ex     = r["revenue_ex_vat"] or 0.0
        cogs_total = r["cogs_total"]
        costed_qty = r["costed_qty"] or 0
        has_row_cost = cogs_total is not None and costed_qty > 0
        unit_cost  = (cogs_total / costed_qty) if has_row_cost else None

        td = tier_data[label]
        td["qty"]          += qty
        td["revenue"]      += revenue
        td["revenue_ex_vat"] += rev_ex
        if has_row_cost:
            td["cogs"] += cogs_total
            if td["unit_cost"] is None:
                td["unit_cost"] = unit_cost
        else:
            td["has_cost"] = False

    def _build_tier_list(data: dict) -> list:
        result = []
        for name, td in sorted(data.items()):
            qty    = td["qty"]
            rev    = round(td["revenue"], 2)
            rev_ex = round(td["revenue_ex_vat"], 2)
            cogs   = round(td["cogs"], 2) if td["has_cost"] else None
            gp     = round(rev_ex - cogs, 2) if cogs is not None else None
            gpc    = round(gp / qty, 2) if (gp is not None and qty) else None
            result.append({
                "name":         name,
                "qty":          qty,
                "revenue":      rev,
                "gp":           gp,
                "gp_per_cover": gpc,
            })
        return result

    current_tiers = _build_tier_list(tier_data)
    current_gp    = sum(t["gp"] for t in current_tiers if t["gp"] is not None)

    # Simulate: shift shift_pct% of 590 qty → 799
    import copy
    sim_data = copy.deepcopy(tier_data)

    tier_590 = sim_data.get("590")
    tier_799 = sim_data.get("799")

    delta_gp   = 0.0
    delta_note = None

    if tier_590 and tier_799:
        shift_qty = tier_590["qty"] * (shift_pct / 100.0)

        # Revenue impact: shift_qty covers move from 590 tier price to 799 tier price
        avg_rev_590 = tier_590["revenue"] / tier_590["qty"] if tier_590["qty"] else 0
        avg_rev_799 = tier_799["revenue"] / tier_799["qty"] if tier_799["qty"] else 0
        avg_rev_ex_590 = tier_590["revenue_ex_vat"] / tier_590["qty"] if tier_590["qty"] else 0
        avg_rev_ex_799 = tier_799["revenue_ex_vat"] / tier_799["qty"] if tier_799["qty"] else 0

        # Adjust 590 tier
        tier_590["qty"]           -= shift_qty
        tier_590["revenue"]       -= shift_qty * avg_rev_590
        tier_590["revenue_ex_vat"] -= shift_qty * avg_rev_ex_590
        if tier_590["has_cost"] and tier_590["unit_cost"] is not None:
            tier_590["cogs"] -= shift_qty * tier_590["unit_cost"]

        # Adjust 799 tier
        tier_799["qty"]           += shift_qty
        tier_799["revenue"]       += shift_qty * avg_rev_799
        tier_799["revenue_ex_vat"] += shift_qty * avg_rev_ex_799
        if tier_799["has_cost"] and tier_799["unit_cost"] is not None:
            tier_799["cogs"] += shift_qty * tier_799["unit_cost"]
    else:
        delta_note = "590 or 799 tier data not found; simulation unchanged"

    simulated_tiers = _build_tier_list(sim_data)
    simulated_gp    = sum(t["gp"] for t in simulated_tiers if t["gp"] is not None)

    delta_gp  = round(simulated_gp - current_gp, 2)
    delta_pct = round(delta_gp / current_gp * 100, 2) if current_gp else None

    result = {
        "current":   {"tiers": current_tiers,   "total_gp": round(current_gp, 2)},
        "simulated": {"tiers": simulated_tiers,  "total_gp": round(simulated_gp, 2)},
        "delta_gp":  delta_gp,
        "delta_pct": delta_pct,
        "shift_pct": shift_pct,
    }
    if delta_note:
        result["note"] = delta_note
    return result


# ---------------------------------------------------------------------------
# Buffet Analytics Helpers
# ---------------------------------------------------------------------------

_BUFFET_ADULT_CODES = ("BUFFET350", "BUFFET500", "BUFFET590", "BUFFET799", "B01", "B02", "B07", "B11", "B03")
_BUFFET_CHILD_CODES = ("B04", "B05", "B12", "BUFFETCHILD")
_BUFFET_PREMIUM_CODES = ("BUFFET590", "BUFFET799", "B02", "B11", "B03")


def _ols_regression(xs, ys):
    """Simple OLS linear regression. Returns (slope, intercept, r_squared)."""
    n = len(xs)
    if n < 2:
        return 0.0, ys[0] if ys else 0.0, 0.0
    sum_x  = sum(xs)
    sum_y  = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_x2 = sum(x * x for x in xs)
    denom  = n * sum_x2 - sum_x ** 2
    if denom == 0:
        return 0.0, sum_y / n, 0.0
    slope     = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    y_mean    = sum_y / n
    ss_tot    = sum((y - y_mean) ** 2 for y in ys)
    ss_res    = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r_squared = 1 - ss_res / ss_tot if ss_tot else 1.0
    return slope, intercept, max(0.0, min(1.0, r_squared))


def _ym_parts(s: str) -> tuple:
    """Validate a YYYY-MM string and return (year, month) ints.

    Raises HTTPException(400) on malformed input so a bad query param yields a
    clean 400 instead of an unhandled ValueError -> 500.
    """
    import re as _re
    s = s.strip() if isinstance(s, str) else s
    if not isinstance(s, str) or not _re.fullmatch(r"\d{4}-\d{2}", s):
        raise HTTPException(status_code=400, detail=f"Invalid month '{s}'; expected YYYY-MM")
    y, m = int(s[:4]), int(s[5:7])
    if not (1 <= m <= 12):
        raise HTTPException(status_code=400, detail=f"Invalid month '{s}'; month must be 01-12")
    return y, m


def _iso_week_sql(col: str) -> str:
    """SQLite expression producing an ISO-8601 week label 'YYYY-Www' for a date column.

    Matches Python's date.isocalendar() exactly (verified across year boundaries),
    unlike strftime('%Y-W%W', ...) which mis-buckets the days straddling Jan 1.
    The ISO week-year is the year of that week's Thursday = date(col,'-3 days','weekday 4').
    """
    thu = f"date({col},'-3 days','weekday 4')"
    return (
        f"strftime('%Y', {thu}) || '-W' || "
        f"substr('0' || ((strftime('%j', {thu}) - 1) / 7 + 1), -2)"
    )


def _next_period_month(ym: str, offset: int) -> str:
    """Add `offset` months to a YYYY-MM string."""
    y, m = _ym_parts(ym)
    m += offset
    while m > 12:
        m -= 12
        y += 1
    return f"{y:04d}-{m:02d}"


# ---------------------------------------------------------------------------
# GET /api/buffet/gp-calendar
# ---------------------------------------------------------------------------
@app.get("/api/buffet/gp-calendar")
@ttl_cache
def buffet_gp_calendar(
    branch: Optional[str] = Query(None),
    month:  str           = Query(...),
):
    """Daily GP calendar for a given YYYY-MM. Prorates monthly COGS/revenue to daily."""
    params: dict = {"month": month}

    # --- Daily revenue & tx from sales_transaction (Easy, dine-in) ---
    day_filters = ["st.source = 'Easy'", "(st.table_no LIKE 'T%' OR st.table_no LIKE 'G%')",
                   "SUBSTR(st.sold_at, 1, 7) = :month"]
    if branch:
        day_filters.append("b.name = :branch")
        params["branch"] = branch

    day_sql = f"""
        SELECT
            DATE(st.sold_at)          AS day,
            SUM(st.total_inc_vat)     AS revenue_inc,
            COUNT(*)                  AS tx_count,
            SUM(st.customer_count)    AS customer_count
        FROM sales_transaction st
        JOIN branches b ON b.branch_id = st.branch_id
        WHERE {' AND '.join(day_filters)}
        GROUP BY DATE(st.sold_at)
        ORDER BY DATE(st.sold_at)
    """

    # --- Monthly BUFFET COGS & revenue_ex from sales_sku_monthly ---
    mon_filters = ["sm.period_month = :month", "c.name = 'BUFFET'"]
    if branch:
        mon_filters.append("b.name = :branch")

    mon_sql = f"""
        SELECT
            SUM(sm.subtotal_ex_vat)         AS rev_ex,
            SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}) AS cogs
        FROM sales_sku_monthly sm
        JOIN branches   b  ON b.branch_id  = sm.branch_id
        JOIN skus       sk ON sk.sku_id    = sm.sku_id
        JOIN categories c  ON c.category_id = sk.category_id
        LEFT JOIN sku_cost sc ON sc.sku_id = sm.sku_id
        WHERE {' AND '.join(mon_filters)}
    """

    with get_db() as con:
        day_rows  = con.execute(day_sql, params).fetchall()
        mon_row   = con.execute(mon_sql, params).fetchone()

    monthly_rev_ex = (mon_row["rev_ex"] or 0.0) if mon_row else 0.0
    monthly_cogs   = (mon_row["cogs"]   or None) if mon_row else None

    total_rev_inc = sum(r["revenue_inc"] or 0.0 for r in day_rows)

    days_out = []
    for r in day_rows:
        day_rev = r["revenue_inc"] or 0.0
        weight  = day_rev / total_rev_inc if total_rev_inc else 0.0

        day_rev_ex = round(monthly_rev_ex * weight, 2)
        day_cogs   = round(monthly_cogs * weight, 2) if monthly_cogs is not None else None

        if day_cogs is not None and day_rev_ex:
            day_gp     = round(day_rev_ex - day_cogs, 2)
            margin_pct = round(day_gp / day_rev_ex * 100, 2)
        elif day_cogs is not None:
            day_gp, margin_pct = round(-day_cogs, 2), None
        else:
            day_gp, margin_pct = None, None

        days_out.append({
            "date":           r["day"],
            "revenue_inc":    round(day_rev, 2),
            "revenue_ex":     day_rev_ex,
            "cogs":           day_cogs,
            "gp":             day_gp,
            "margin_pct":     margin_pct,
            "tx_count":       r["tx_count"]      or 0,
            "customer_count": r["customer_count"] or 0,
        })

    # Monthly margin
    if monthly_cogs is not None and monthly_rev_ex:
        monthly_margin_pct = round((monthly_rev_ex - monthly_cogs) / monthly_rev_ex * 100, 2)
    else:
        monthly_margin_pct = None

    return {
        "month":               month,
        "days":                days_out,
        "monthly_margin_pct":  monthly_margin_pct,
    }


# ---------------------------------------------------------------------------
# GET /api/buffet/branch-tier-matrix
# ---------------------------------------------------------------------------
@app.get("/api/buffet/branch-tier-matrix")
@ttl_cache
def buffet_branch_tier_matrix(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Per-branch buffet tier mix: pax, premium %, avg GP/cover, revenue."""
    filters: list = ["c.name = 'BUFFET'"]
    params:  dict = {}
    _month_range_filter(filters, params, month_from, month_to, month)

    sql = f"""
        SELECT
            b.name                                                          AS branch,
            SUM(sm.qty)                                                     AS total_pax,
            SUM(sm.subtotal_ex_vat)                                         AS rev_ex,
            SUM(sm.total_inc_vat)                                           AS rev_inc,
            SUM(CASE
                    WHEN sk.sku_code IN ('BUFFET590','BUFFET799','B02','B11','B03')
                         OR sm.subtotal_ex_vat/NULLIF(sm.qty,0) BETWEEN 570 AND 620
                         OR sm.subtotal_ex_vat/NULLIF(sm.qty,0) BETWEEN 780 AND 820
                         OR sm.subtotal_ex_vat/NULLIF(sm.qty,0) > 1000
                    THEN sm.qty ELSE 0
                END)                                                        AS premium_pax,
            SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')})                   AS total_cogs
        FROM sales_sku_monthly sm
        JOIN branches   b  ON b.branch_id  = sm.branch_id
        JOIN skus       sk ON sk.sku_id    = sm.sku_id
        JOIN categories c  ON c.category_id = sk.category_id
        LEFT JOIN sku_cost sc ON sc.sku_id = sm.sku_id
        WHERE {' AND '.join(filters)} AND b.name != 'ALL'
        GROUP BY b.name
        ORDER BY b.name
    """

    with get_db() as con:
        rows = con.execute(sql, params).fetchall()

    result = []
    for r in rows:
        total_pax   = r["total_pax"]   or 0
        premium_pax = r["premium_pax"] or 0
        premium_pct = round(premium_pax / total_pax * 100, 2) if total_pax else 0.0
        total_cogs  = r["total_cogs"]
        rev_ex      = r["rev_ex"] or 0.0
        if total_cogs is not None and total_pax:
            gp_total          = rev_ex - total_cogs
            avg_gp_per_cover  = round(gp_total / total_pax, 2)
        else:
            avg_gp_per_cover  = None

        result.append({
            "branch":           r["branch"],
            "total_pax":        total_pax,
            "premium_pct":      premium_pct,
            "avg_gp_per_cover": avg_gp_per_cover,
            "total_revenue":    round(rev_ex, 2),   # ex-VAT (VAT is never revenue)
        })

    return result


# ---------------------------------------------------------------------------
# GET /api/buffet/child-trend
# ---------------------------------------------------------------------------
@app.get("/api/buffet/child-trend")
@ttl_cache
def buffet_child_trend(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Monthly adult/child pax split and GP% trend for BUFFET category."""
    filters: list = []
    params:  dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, None, col="sm.period_month")
    _month_range_filter([], params, None, month_to, col="sm.period_month")
    # rebuild to_filter directly
    mf_filter = [f for f in filters]
    if month_to:
        mf_filter.append("sm.period_month <= :month_to")
        params["month_to"] = month_to

    where = ("WHERE " + " AND ".join(mf_filter)) if mf_filter else ""

    sql = f"""
        SELECT
            sm.period_month,
            sk.sku_code,
            sk.description,
            c.name          AS category,
            SUM(sm.qty)     AS qty,
            SUM(sm.subtotal_ex_vat) AS rev_ex,
            SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}) AS cogs
        FROM sales_sku_monthly sm
        JOIN branches   b  ON b.branch_id   = sm.branch_id
        JOIN skus       sk ON sk.sku_id     = sm.sku_id
        JOIN categories c  ON c.category_id = sk.category_id
        LEFT JOIN sku_cost sc ON sc.sku_id  = sm.sku_id
        {where}
        GROUP BY sm.period_month, sk.sku_code, sk.description, c.name
        ORDER BY sm.period_month
    """

    with get_db() as con:
        rows = con.execute(sql, params).fetchall()

    # Aggregate per month
    from collections import defaultdict
    monthly: dict = defaultdict(lambda: {"adult": 0.0, "child": 0.0, "rev_ex": 0.0, "cogs": 0.0, "has_cost": False})

    for r in rows:
        mo   = r["period_month"]
        code = (r["sku_code"] or "").upper()
        desc = (r["description"] or "").lower()
        qty  = r["qty"] or 0.0
        cat  = r["category"] or ""

        is_child = (code in _BUFFET_CHILD_CODES or
                    "child" in desc or "เด็ก" in desc)
        is_adult = code in _BUFFET_ADULT_CODES

        if is_child:
            monthly[mo]["child"] += qty
        elif is_adult:
            monthly[mo]["adult"] += qty

        if cat == "BUFFET":
            monthly[mo]["rev_ex"] += r["rev_ex"] or 0.0
            if r["cogs"] is not None:
                monthly[mo]["cogs"]     += r["cogs"]
                monthly[mo]["has_cost"]  = True

    result = []
    for mo in sorted(monthly):
        d          = monthly[mo]
        adult_pax  = d["adult"]
        child_pax  = d["child"]
        total_pax  = adult_pax + child_pax
        child_pct  = round(child_pax / total_pax * 100, 2) if total_pax else 0.0
        rev_ex     = d["rev_ex"]
        gp_pct     = None
        if d["has_cost"] and rev_ex:
            gp         = rev_ex - d["cogs"]
            gp_pct     = round(gp / rev_ex * 100, 2)

        result.append({
            "period_month": mo,
            "adult_pax":    adult_pax,
            "child_pax":    child_pax,
            "child_pct":    child_pct,
            "gp_pct":       gp_pct,
        })

    return result


# ---------------------------------------------------------------------------
# GET /api/buffet/drink-attach-by-branch
# ---------------------------------------------------------------------------
@app.get("/api/buffet/drink-attach-by-branch")
@ttl_cache
def buffet_drink_attach_by_branch(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Drink attachment rates per branch from Easy POS transactions."""
    filters: list = ["st.source = 'Easy'", "b.name != 'ALL'"]
    params:  dict = {}
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="SUBSTR(st.sold_at,1,7)")

    sql = f"""
        SELECT
            b.name                                  AS branch,
            COUNT(*)                                AS total_bills,
            SUM(CASE WHEN st.drink_subtotal > 0 THEN 1 ELSE 0 END) AS bills_with_drink,
            AVG(CASE WHEN st.drink_subtotal > 0 THEN st.drink_subtotal ELSE NULL END) AS avg_drink_per_bill,
            SUM(st.drink_subtotal)                  AS total_drink_revenue,
            SUM(st.total_inc_vat) / 1.07                   AS total_revenue
        FROM sales_transaction st
        JOIN branches b ON b.branch_id = st.branch_id
        WHERE {' AND '.join(filters)}
        GROUP BY b.name
        ORDER BY bills_with_drink * 1.0 / NULLIF(COUNT(*), 0) DESC
    """

    with get_db() as con:
        rows = con.execute(sql, params).fetchall()

    result = []
    for r in rows:
        total_bills       = r["total_bills"]        or 0
        bills_with_drink  = r["bills_with_drink"]   or 0
        attach_rate_pct   = round(bills_with_drink / total_bills * 100, 2) if total_bills else 0.0
        total_rev         = r["total_revenue"]       or 0.0
        total_drink       = r["total_drink_revenue"] or 0.0
        drink_rev_pct     = round(total_drink / total_rev * 100, 2) if total_rev else 0.0

        result.append({
            "branch":               r["branch"],
            "total_bills":          total_bills,
            "bills_with_drink":     bills_with_drink,
            "attach_rate_pct":      attach_rate_pct,
            "avg_drink_per_bill":   round(r["avg_drink_per_bill"] or 0.0, 2),
            "drink_revenue_pct":    drink_rev_pct,
            "total_drink_revenue":  round(total_drink, 2),
        })

    return result


# ---------------------------------------------------------------------------
# GET /api/buffet/breakeven
# ---------------------------------------------------------------------------
@app.get("/api/buffet/breakeven")
@ttl_cache
def buffet_breakeven(
    branch:               Optional[str]   = Query(None),
    month:                Optional[str]   = Query(None),
    month_from:           Optional[str]   = Query(None),
    month_to:             Optional[str]   = Query(None),
    monthly_fixed_cost:   float           = Query(0.0),
):
    """Breakeven analysis: covers needed to cover fixed costs given avg GP/cover."""
    sku_filters:  list = ["c.name = 'BUFFET'"]
    sku_params:   dict = {}
    if branch:
        sku_filters.append("b.name = :branch")
        sku_params["branch"] = branch
    _month_range_filter(sku_filters, sku_params, month_from, month_to, month)

    sku_sql = f"""
        SELECT
            SUM(sm.qty)                        AS total_pax,
            SUM(sm.subtotal_ex_vat)            AS rev_ex,
            SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}) AS total_cogs
        FROM sales_sku_monthly sm
        JOIN branches   b  ON b.branch_id   = sm.branch_id
        JOIN skus       sk ON sk.sku_id     = sm.sku_id
        JOIN categories c  ON c.category_id = sk.category_id
        LEFT JOIN sku_cost sc ON sc.sku_id  = sm.sku_id
        WHERE {' AND '.join(sku_filters)} AND b.name != 'ALL'
    """

    tx_filters: list = ["st.source = 'Easy'"]
    tx_params:  dict = {}
    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch
    _month_range_filter(tx_filters, tx_params, month_from, month_to, month,
                        col="SUBSTR(st.sold_at,1,7)")

    tx_sql = f"""
        SELECT
            SUM(st.customer_count) AS total_covers,
            COUNT(DISTINCT SUBSTR(st.sold_at,1,10)) AS active_days
        FROM sales_transaction st
        JOIN branches b ON b.branch_id = st.branch_id
        WHERE {' AND '.join(tx_filters)} AND b.name != 'ALL'
    """

    with get_db() as con:
        sku_row = con.execute(sku_sql, sku_params).fetchone()
        tx_row  = con.execute(tx_sql,  tx_params).fetchone()

    total_pax  = (sku_row["total_pax"] or 0) if sku_row else 0
    rev_ex     = (sku_row["rev_ex"]    or 0.0) if sku_row else 0.0
    total_cogs = (sku_row["total_cogs"]) if sku_row else None

    avg_rev_per_cover = round(rev_ex / total_pax, 2) if total_pax else 0.0

    if total_cogs is not None and total_pax:
        gp_total         = rev_ex - total_cogs
        avg_gp_per_cover = round(gp_total / total_pax, 2)
    else:
        avg_gp_per_cover = None

    active_days       = (tx_row["active_days"]  or 1)  if tx_row else 1
    total_covers      = (tx_row["total_covers"] or 0)  if tx_row else 0
    actual_avg_covers = round(total_covers / active_days, 2) if active_days else 0.0

    if monthly_fixed_cost > 0 and avg_gp_per_cover:
        required_per_month = round(monthly_fixed_cost / avg_gp_per_cover, 2)
        required_per_day   = round(required_per_month / 30, 2)
        breakeven_util     = round(actual_avg_covers / required_per_day * 100, 2) if required_per_day else None
    else:
        required_per_month = None
        required_per_day   = None
        breakeven_util     = None

    return {
        "avg_gp_per_cover":           avg_gp_per_cover,
        "avg_revenue_per_cover":       avg_rev_per_cover,
        "monthly_fixed_cost":          monthly_fixed_cost,
        "required_covers_per_month":   required_per_month,
        "required_covers_per_day":     required_per_day,
        "actual_avg_covers_per_day":   actual_avg_covers,
        "breakeven_utilization_pct":   breakeven_util,
    }


# ---------------------------------------------------------------------------
# GET /api/buffet/forecast
# ---------------------------------------------------------------------------
@app.get("/api/buffet/forecast")
@ttl_cache
def buffet_forecast(
    branch:     Optional[str] = Query(None),
    periods:    int           = Query(3, ge=1, le=12),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Linear-regression forecast of buffet pax and revenue for future months."""
    filters: list = ["c.name = 'BUFFET'", "b.name != 'ALL'"]
    params:  dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to)

    sql = f"""
        SELECT
            sm.period_month,
            SUM(sm.qty)           AS pax,
            SUM(sm.subtotal_ex_vat) AS revenue
        FROM sales_sku_monthly sm
        JOIN branches   b  ON b.branch_id   = sm.branch_id
        JOIN skus       sk ON sk.sku_id     = sm.sku_id
        JOIN categories c  ON c.category_id = sk.category_id
        WHERE {' AND '.join(filters)}
        GROUP BY sm.period_month
        ORDER BY sm.period_month
    """

    with get_db() as con:
        rows = con.execute(sql, params).fetchall()

    if len(rows) < 3:
        raise HTTPException(400, "At least 3 historical data points are required for forecasting.")

    historical = [{"period": r["period_month"], "pax": r["pax"] or 0.0, "revenue": r["revenue"] or 0.0}
                  for r in rows]

    xs  = list(range(len(historical)))
    pax_ys  = [h["pax"]     for h in historical]
    rev_ys  = [h["revenue"] for h in historical]

    pax_slope,  pax_intercept,  pax_r2  = _ols_regression(xs, pax_ys)
    rev_slope,  rev_intercept,  rev_r2  = _ols_regression(xs, rev_ys)

    r_squared = round((pax_r2 + rev_r2) / 2, 4)

    # Trend from slope relative to mean
    pax_mean = sum(pax_ys) / len(pax_ys) if pax_ys else 1
    rel_slope = pax_slope / pax_mean if pax_mean else 0
    if rel_slope > 0.01:
        trend = "up"
    elif rel_slope < -0.01:
        trend = "down"
    else:
        trend = "flat"

    # Monthly growth rate (avg MoM %)
    mom_rates = []
    for i in range(1, len(pax_ys)):
        prev = pax_ys[i - 1]
        if prev:
            mom_rates.append((pax_ys[i] - prev) / prev * 100)
    monthly_growth_rate = round(sum(mom_rates) / len(mom_rates), 2) if mom_rates else 0.0

    confidence = "high" if r_squared > 0.7 else ("med" if r_squared > 0.4 else "low")

    last_period = historical[-1]["period"]
    forecast_out = []
    for i in range(1, periods + 1):
        xi  = len(historical) - 1 + i
        fpax  = max(0.0, pax_slope * xi + pax_intercept)
        frev  = max(0.0, rev_slope * xi + rev_intercept)
        fperiod = _next_period_month(last_period, i)
        forecast_out.append({
            "period":     fperiod,
            "pax":        round(fpax, 2),
            "revenue":    round(frev, 2),
            "confidence": confidence,
        })

    return {
        "historical":          historical,
        "forecast":            forecast_out,
        "r_squared":           r_squared,
        "trend":               trend,
        "monthly_growth_rate": monthly_growth_rate,
    }


# ---------------------------------------------------------------------------
# /api/upload   -  ingest an Easy .txt file
# ---------------------------------------------------------------------------
@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """Accept a multipart Easy .txt upload, ingest it, return summary."""
    import sys
    import importlib
    from pathlib import Path as _Path

    # Use the basename only — file.filename is attacker-controlled and a value like
    # "..\\..\\x" would otherwise let the rename below write/delete outside the temp dir.
    filename = _Path(file.filename or "upload.txt").name or "upload.txt"
    suffix = _Path(filename).suffix or ".txt"

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, prefix="easy_upload_"
        ) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = _Path(tmp.name)

        # Rename the temp file to match original name so pattern matching works.
        # Guard: the target MUST stay inside the temp directory.
        named_path = tmp_path.parent / filename
        if named_path.resolve().parent != tmp_path.parent.resolve():
            raise HTTPException(status_code=400, detail="Invalid filename")
        tmp_path.rename(named_path)
        tmp_path = named_path

        # Import ingest_easy from the same directory
        ingest_mod_path = HERE / "ingest_easy.py"
        import importlib.util
        spec = importlib.util.spec_from_file_location("ingest_easy", str(ingest_mod_path))
        ingest_easy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ingest_easy)

        with get_db() as con:
            con.execute("PRAGMA journal_mode=WAL")
            result = ingest_easy.ingest_file(tmp_path, con)

        invalidate_cache()   # new data uploaded — clear cached analytics responses

        return {
            "file":          filename,
            "branch":        result["branch"],
            "period":        result["period"],
            "rows_ingested": result["rows_ingested"],
            "file_type":     result["file_type"],
        }

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        # Log full detail server-side; return a generic message to the client.
        import traceback, sys as _sys
        print("Ingest error:", traceback.format_exc(), file=_sys.stderr)
        raise HTTPException(status_code=500, detail="Ingest failed. See server log.")
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# /api/analysis/daily   -  day-by-day sales for a given month
# ---------------------------------------------------------------------------
@app.get("/api/analysis/daily")
def analysis_daily(
    month: str = Query(..., description="YYYY-MM"),
    branch: Optional[str] = Query(None),
):
    """
    Daily revenue, tx count, and customer count for each calendar day
    in the given month. Only Easy months have per-transaction data.
    Falls back to a single summary row from sales_month_summary for CM POS months.
    """
    filters = ["b.name != 'ALL'", "strftime('%Y-%m', st.sold_at) = :month"]
    params: dict = {"month": month}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch

    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT strftime('%Y-%m-%d', st.sold_at) AS day,
                   COUNT(*)                          AS tx_count,
                   SUM(st.customer_count)            AS customer_count,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2)  AS revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY day
            ORDER BY day
            """,
            params,
        ).fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# /api/analysis/gross_profit   -  GP (Revenue − COGS) at daily/weekly/monthly
# ---------------------------------------------------------------------------
@app.get("/api/analysis/gross_profit")
@ttl_cache
def analysis_gross_profit(
    branch:      Optional[str] = Query(None),
    granularity: str           = Query("daily"),   # daily | weekly | monthly
    month:       Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    date_from:   Optional[str] = Query(None),
    date_to:     Optional[str] = Query(None),
):
    """
    Gross profit = Revenue (ex-VAT) − COGS (qty × sku_cost.unit_cost).

    Monthly: computed directly from sales_sku_monthly + sku_cost.
    Daily/Weekly: COGS is prorated by daily revenue share within each
    (branch, month) — exact at month level, distributed by sales mix per day.
    """
    granularity = (granularity or "daily").lower()

    # ---- monthly: direct, no proration ------------------------------------
    if granularity == "monthly":
        filters = ["b.name != 'ALL'"]
        params: dict = {}
        if branch:
            filters.append("b.name = :branch")
            params["branch"] = branch
        _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")
        where = "WHERE " + " AND ".join(filters)
        with get_db() as con:
            rows = con.execute(
                f"""
                SELECT sm.period_month AS period,
                       ROUND(SUM(sm.subtotal_ex_vat), 2)                          AS revenue,
                       ROUND(SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}), 2)     AS cogs,
                       SUM(CASE WHEN {_effective_cost_subquery(ssm='sm', sc='sc')} IS NULL THEN sm.subtotal_ex_vat ELSE 0 END)
                                                                                   AS uncosted_rev
                FROM sales_sku_monthly sm
                JOIN branches b ON b.branch_id = sm.branch_id
                JOIN skus sk ON sk.sku_id = sm.sku_id
                LEFT JOIN sku_cost sc ON sc.sku_id = sm.sku_id
                {where}
                GROUP BY sm.period_month
                ORDER BY sm.period_month
                """,
                params,
            ).fetchall()
        out = []
        for r in rows:
            rev  = r["revenue"] or 0.0
            cogs = r["cogs"] or 0.0
            gp   = round(rev - cogs, 2)
            pct  = round(gp / rev * 100, 1) if rev else 0.0
            out.append({
                "period":       r["period"],
                "revenue":      rev,
                "cogs":         cogs,
                "gross_profit": gp,
                "margin_pct":   pct,
                "uncosted_rev": round(r["uncosted_rev"] or 0.0, 2),
            })
        return out

    # ---- daily / weekly: prorate COGS by daily revenue share --------------
    # Step 1: per-(branch, month) revenue + cogs from sales_sku_monthly
    bm_filters = ["b.name != 'ALL'"]
    bm_params: dict = {}
    if branch:
        bm_filters.append("b.name = :branch")
        bm_params["branch"] = branch
    # If date range supplied, derive month range from it; otherwise use month_*
    mf = month_from or month or (date_from[:7] if date_from else None)
    mt = month_to   or month or (date_to[:7]   if date_to   else None)
    if mf:
        bm_filters.append("sm.period_month >= :mf")
        bm_params["mf"] = mf
    if mt:
        bm_filters.append("sm.period_month <= :mt")
        bm_params["mt"] = mt
    bm_where = "WHERE " + " AND ".join(bm_filters)

    # Step 2: per-day revenue from sales_transaction (Easy only — only source with per-tx data)
    tx_filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
    tx_params: dict = {}
    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch
    if date_from:
        tx_filters.append("DATE(st.sold_at) >= :date_from")
        tx_params["date_from"] = date_from
    if date_to:
        tx_filters.append("DATE(st.sold_at) <= :date_to")
        tx_params["date_to"] = date_to
    if mf and not date_from:
        tx_filters.append("strftime('%Y-%m', st.sold_at) >= :mf")
        tx_params["mf"] = mf
    if mt and not date_to:
        tx_filters.append("strftime('%Y-%m', st.sold_at) <= :mt")
        tx_params["mt"] = mt
    tx_where = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        bm_rows = con.execute(
            f"""
            SELECT b.name AS branch, sm.period_month AS m,
                   SUM(sm.subtotal_ex_vat) AS rev_ex,
                   SUM(sm.total_inc_vat)   AS rev_inc,
                   SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}) AS cogs
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            JOIN skus sk ON sk.sku_id = sm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = sm.sku_id
            {bm_where}
            GROUP BY b.name, sm.period_month
            """,
            bm_params,
        ).fetchall()

        day_rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   DATE(st.sold_at) AS day,
                   strftime('%Y-%m', st.sold_at) AS m,
                   SUM(st.total_inc_vat) AS rev_inc
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {tx_where}
            GROUP BY b.name, DATE(st.sold_at)
            ORDER BY day
            """,
            tx_params,
        ).fetchall()

    # Build per-(branch, month) totals
    bm_map: Dict = {}
    for r in bm_rows:
        bm_map[(r["branch"], r["m"])] = {
            "rev_ex":  r["rev_ex"]  or 0.0,
            "rev_inc": r["rev_inc"] or 0.0,
            "cogs":    r["cogs"]    or 0.0,
        }

    # Aggregate daily-tx revenue per (branch, month) so we can compute share
    bm_daily_total_inc: Dict = {}
    for r in day_rows:
        k = (r["branch"], r["m"])
        bm_daily_total_inc[k] = bm_daily_total_inc.get(k, 0.0) + (r["rev_inc"] or 0.0)

    # Build daily output with prorated COGS
    daily_out = []
    for r in day_rows:
        k = (r["branch"], r["m"])
        bm = bm_map.get(k)
        day_rev_inc = r["rev_inc"] or 0.0
        if not bm or bm["rev_inc"] <= 0 or bm_daily_total_inc.get(k, 0) <= 0:
            # No monthly costing info or no monthly revenue — fall back to day_rev_ex with 0 COGS
            day_rev_ex = day_rev_inc / 1.07
            daily_out.append({
                "branch":       r["branch"],
                "day":          r["day"],
                "period_month": r["m"],
                "revenue":      round(day_rev_ex, 2),
                "revenue_inc":  round(day_rev_inc, 2),
                "cogs":         0.0,
                "gross_profit": round(day_rev_ex, 2),
                "margin_pct":   None,
            })
            continue
        share = day_rev_inc / bm_daily_total_inc[k]   # share of this branch+month's tx revenue
        day_cogs = bm["cogs"] * share
        # Day revenue ex-VAT: use the same share against monthly ex-VAT (keeps GP consistent)
        day_rev_ex = bm["rev_ex"] * share
        gp = day_rev_ex - day_cogs
        pct = (gp / day_rev_ex * 100) if day_rev_ex else None
        daily_out.append({
            "branch":       r["branch"],
            "day":          r["day"],
            "period_month": r["m"],
            "revenue":      round(day_rev_ex, 2),
            "revenue_inc":  round(day_rev_inc, 2),
            "cogs":         round(day_cogs, 2),
            "gross_profit": round(gp, 2),
            "margin_pct":   round(pct, 1) if pct is not None else None,
        })

    if granularity == "daily":
        # Roll up across branches per day (in case branch=None)
        agg: Dict = {}
        for d in daily_out:
            k = d["day"]
            a = agg.setdefault(k, {"period": k, "revenue": 0.0, "cogs": 0.0, "gross_profit": 0.0})
            a["revenue"]      += d["revenue"]
            a["cogs"]         += d["cogs"]
            a["gross_profit"] += d["gross_profit"]
        out = []
        for k in sorted(agg.keys()):
            a = agg[k]
            pct = round(a["gross_profit"] / a["revenue"] * 100, 1) if a["revenue"] else None
            out.append({
                "period":       a["period"],
                "revenue":      round(a["revenue"], 2),
                "cogs":         round(a["cogs"], 2),
                "gross_profit": round(a["gross_profit"], 2),
                "margin_pct":   pct,
            })
        return out

    # weekly: ISO-8601 week key 'YYYY-Www' (matches _iso_week_sql used by the SQL paths)
    if granularity == "weekly":
        from datetime import date as _date
        agg = {}
        for d in daily_out:
            try:
                y, mo, dd = d["day"].split("-")
                iso = _date(int(y), int(mo), int(dd)).isocalendar()
                wk = f"{iso[0]}-W{iso[1]:02d}"
            except Exception:
                continue
            a = agg.setdefault(wk, {"period": wk, "revenue": 0.0, "cogs": 0.0,
                                    "gross_profit": 0.0, "week_start": d["day"]})
            a["revenue"]      += d["revenue"]
            a["cogs"]         += d["cogs"]
            a["gross_profit"] += d["gross_profit"]
            if d["day"] < a["week_start"]:
                a["week_start"] = d["day"]
        out = []
        for k in sorted(agg.keys()):
            a = agg[k]
            pct = round(a["gross_profit"] / a["revenue"] * 100, 1) if a["revenue"] else None
            out.append({
                "period":       a["period"],
                "week_start":   a["week_start"],
                "revenue":      round(a["revenue"], 2),
                "cogs":         round(a["cogs"], 2),
                "gross_profit": round(a["gross_profit"], 2),
                "margin_pct":   pct,
            })
        return out

    raise HTTPException(400, f"Unknown granularity: {granularity}")


# ---------------------------------------------------------------------------
# /api/analysis/hourly   -  revenue/tx breakdown by hour of day
# ---------------------------------------------------------------------------
@app.get("/api/analysis/hourly")
@ttl_cache
def analysis_hourly(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    date_from:  Optional[str] = Query(None),
    date_to:    Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
):
    """Revenue, tx count, avg ticket by hour (0-23). Fills missing hours with zeros."""
    filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    if date_from or date_to:
        if date_from:
            filters.append("DATE(st.sold_at) >= :date_from")
            params["date_from"] = date_from
        if date_to:
            filters.append("DATE(st.sold_at) <= :date_to")
            params["date_to"] = date_to
    else:
        _month_range_filter(
            filters, params, month_from, month_to, month,
            col="strftime('%Y-%m', st.sold_at)",
        )
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT CAST(strftime('%H', st.sold_at) AS INTEGER) AS hour,
                   COUNT(*)                                     AS tx_count,
                   COALESCE(SUM(st.customer_count), 0)         AS customer_count,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2)             AS revenue,
                   ROUND(AVG(st.total_inc_vat), 2)             AS avg_ticket
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY hour
            ORDER BY hour
            """,
            params,
        ).fetchall()
    hour_map = {r["hour"]: dict(r) for r in rows}
    return [
        hour_map.get(h, {"hour": h, "tx_count": 0, "customer_count": 0,
                         "revenue": 0.0, "avg_ticket": 0.0})
        for h in range(24)
    ]


# ---------------------------------------------------------------------------
# /api/analysis/dow   -  day-of-week distribution
# ---------------------------------------------------------------------------
@app.get("/api/analysis/dow")
@ttl_cache
def analysis_dow(
    month:       Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    branch:      Optional[str] = Query(None),
    granularity: str           = Query("daily"),  # daily|weekly|monthly
):
    """Revenue and tx count grouped by day-of-week (0=Sun â€¦ 6=Sat)."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="strftime('%Y-%m', st.sold_at)")

    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT CAST(strftime('%w', st.sold_at) AS INTEGER) AS dow,
                   COUNT(*)                                     AS tx_count,
                   SUM(st.customer_count)                       AS customer_count,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2)             AS revenue,
                   ROUND(AVG(st.total_inc_vat), 2)             AS avg_ticket,
                   COUNT(DISTINCT DATE(st.sold_at))             AS day_count
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY dow
            ORDER BY dow
            """,
            params,
        ).fetchall()

    # For weekly/monthly: compute total periods in the selected range for normalisation
    total_periods = None
    if granularity in ("weekly", "monthly"):
        _pexpr = (_iso_week_sql('st.sold_at') if granularity == "weekly"
                  else "strftime('%Y-%m', st.sold_at)")
        with get_db() as con2:
            pr = con2.execute(
                f"SELECT COUNT(DISTINCT {_pexpr}) AS cnt "
                f"FROM sales_transaction st "
                f"JOIN branches b ON b.branch_id = st.branch_id {where}",
                params
            ).fetchone()
        total_periods = max(pr["cnt"] if pr else 1, 1)

    DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    result = {d: {"dow": d, "label": DOW[d], "tx_count": 0, "customer_count": 0,
                  "revenue": 0.0, "avg_ticket": 0.0, "day_count": 0,
                  "avg_revenue": 0.0, "avg_tx": 0.0} for d in range(7)}
    for r in rows:
        if granularity == "daily":
            dc = max(r["day_count"] or 1, 1)
        else:
            dc = total_periods
        result[r["dow"]] = {
            "dow": r["dow"], "label": DOW[r["dow"]],
            "tx_count": r["tx_count"], "customer_count": r["customer_count"],
            "revenue": r["revenue"], "avg_ticket": r["avg_ticket"],
            "day_count":   r["day_count"] or 0,
            "avg_revenue": round((r["revenue"] or 0) / dc, 2),
            "avg_tx":      round((r["tx_count"] or 0) / dc, 2),
        }
    return list(result.values())


# ---------------------------------------------------------------------------
# /api/analysis/compare   -  KPI comparison between two periods
# ---------------------------------------------------------------------------
@app.get("/api/analysis/compare")
@ttl_cache
def analysis_compare(
    month: str = Query(..., description="Primary period YYYY-MM"),
    vs: str = Query(..., description="Comparison period YYYY-MM"),
    branch: Optional[str] = Query(None),
):
    """
    Returns KPI objects for `month` and `vs` side by side:
    revenue, revenue_ex_vat, tx_count, customer_count, avg_ticket,
    gross_sales, net_sales, discounts.
    Pulls from sales_sku_monthly (Easy) + sales_month_summary (CM POS).
    """
    def fetch_kpis(period: str, con) -> dict:
        branch_filter = "AND b.name = :branch" if branch else ""
        p: dict = {"period": period}
        if branch:
            p["branch"] = branch

        rev = con.execute(
            f"""
            SELECT ROUND(SUM(sm.subtotal_ex_vat), 2)   AS revenue,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)  AS revenue_ex_vat
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            WHERE b.name != 'ALL' AND sm.period_month = :period
              {branch_filter}
            """,
            p,
        ).fetchone()

        tx = con.execute(
            f"""
            SELECT COUNT(*) AS tx_count,
                   SUM(customer_count) AS customer_count,
                   ROUND(AVG(total_inc_vat), 2) AS avg_ticket
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            WHERE b.name != 'ALL' AND strftime('%Y-%m', st.sold_at) = :period
              {branch_filter}
            """,
            p,
        ).fetchone()

        sms = con.execute(
            f"""
            SELECT ROUND(SUM(gross_sales), 2) AS gross_sales,
                   ROUND(SUM(net_sales), 2)   AS net_sales,
                   ROUND(SUM(discounts), 2)   AS discounts,
                   SUM(transaction_count)     AS tx_count_sms,
                   SUM(customer_count)        AS customer_count_sms
            FROM sales_month_summary sms
            JOIN branches b ON b.branch_id = sms.branch_id
            WHERE b.name != 'ALL' AND sms.period_month = :period
              {branch_filter}
            """,
            p,
        ).fetchone()

        # Use Easy data when available, else fall back to CM POS summary
        revenue     = (rev["revenue"]        or 0) if rev else 0
        revenue_ex  = (rev["revenue_ex_vat"] or 0) if rev else 0
        tx_count    = (tx["tx_count"]        or 0) if tx else 0
        cust_count  = (tx["customer_count"]  or 0) if tx else 0
        avg_ticket  = (tx["avg_ticket"]      or 0) if tx else 0

        if revenue == 0 and sms:
            revenue    = sms["net_sales"]     or 0
            revenue_ex = sms["net_sales"]     or 0
        if tx_count == 0 and sms:
            tx_count   = sms["tx_count_sms"]  or 0
            cust_count = sms["customer_count_sms"] or 0
            avg_ticket = round(revenue / tx_count, 2) if tx_count else 0

        return {
            "period":       period,
            "revenue":      revenue,
            "revenue_ex":   revenue_ex,
            "tx_count":     tx_count,
            "customer_count": cust_count,
            "avg_ticket":   avg_ticket,
            "gross_sales":  (sms["gross_sales"] or 0) if sms else 0,
            "net_sales":    (sms["net_sales"]   or 0) if sms else 0,
            "discounts":    (sms["discounts"]   or 0) if sms else 0,
        }

    def pct_change(a, b):
        if b == 0:
            return None
        return round((a - b) / b * 100, 1)

    with get_db() as con:
        current = fetch_kpis(month, con)
        prior   = fetch_kpis(vs, con)

    return {
        "current": current,
        "prior":   prior,
        "delta": {
            "revenue":        pct_change(current["revenue"],        prior["revenue"]),
            "tx_count":       pct_change(current["tx_count"],       prior["tx_count"]),
            "customer_count": pct_change(current["customer_count"], prior["customer_count"]),
            "avg_ticket":     pct_change(current["avg_ticket"],     prior["avg_ticket"]),
        },
    }


# ---------------------------------------------------------------------------
# /api/analysis/persona   -  customer behaviour stats
# ---------------------------------------------------------------------------
@app.get("/api/analysis/persona")
@ttl_cache
def analysis_persona(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
):
    """
    Customer behaviour from sales_transaction:
    - Party size distribution (1,2,3,4,5,6+)
    - Avg spend per head by party size
    - Food vs drink vs other split (% of subtotals)
    - Busy hours (0-23 tx count)
    """
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="strftime('%Y-%m', st.sold_at)")

    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        # Party size distribution
        party_rows = con.execute(
            f"""
            SELECT CASE
                     WHEN customer_count <= 0 THEN 1
                     WHEN customer_count >= 6 THEN 6
                     ELSE customer_count
                   END AS party_size,
                   COUNT(*)                          AS tx_count,
                   ROUND(AVG(total_inc_vat), 2)     AS avg_bill,
                   ROUND(AVG(total_inc_vat / CASE WHEN customer_count <= 0 THEN 1
                                                   ELSE customer_count END), 2) AS avg_per_head
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY party_size
            ORDER BY party_size
            """,
            params,
        ).fetchall()

        # Food / drink / other split
        split_row = con.execute(
            f"""
            SELECT ROUND(SUM(food_subtotal), 2)  AS food,
                   ROUND(SUM(drink_subtotal), 2) AS drink,
                   ROUND(SUM(other_subtotal), 2) AS other
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            """,
            params,
        ).fetchone()

        # Busy hours
        hour_rows = con.execute(
            f"""
            SELECT CAST(strftime('%H', st.sold_at) AS INTEGER) AS hour,
                   COUNT(*) AS tx_count,
                   SUM(customer_count) AS customer_count
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY hour
            ORDER BY hour
            """,
            params,
        ).fetchall()

        # Overall stats
        overall = con.execute(
            f"""
            SELECT COUNT(*)                          AS tx_count,
                   ROUND(AVG(customer_count), 2)    AS avg_party_size,
                   ROUND(AVG(total_inc_vat), 2)     AS avg_ticket,
                   ROUND(SUM(total_inc_vat) /
                         NULLIF(SUM(customer_count), 0), 2) AS avg_per_head
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            """,
            params,
        ).fetchone()

    hours_map = {h: 0 for h in range(24)}
    for r in hour_rows:
        hours_map[r["hour"]] = r["tx_count"]

    food  = (split_row["food"]  or 0) if split_row else 0
    drink = (split_row["drink"] or 0) if split_row else 0
    other = (split_row["other"] or 0) if split_row else 0
    total_split = food + drink + other or 1

    return {
        "overall": {
            "tx_count":       (overall["tx_count"]      or 0) if overall else 0,
            "avg_party_size": (overall["avg_party_size"] or 0) if overall else 0,
            "avg_ticket":     (overall["avg_ticket"]     or 0) if overall else 0,
            "avg_per_head":   (overall["avg_per_head"]   or 0) if overall else 0,
        },
        "party_sizes": [
            {
                "size":         r["party_size"],
                "label":        f"{r['party_size']}+" if r["party_size"] >= 6 else str(r["party_size"]),
                "tx_count":     r["tx_count"],
                "avg_bill":     r["avg_bill"],
                "avg_per_head": r["avg_per_head"],
            }
            for r in party_rows
        ],
        "food_drink_split": {
            "food":  round(food  / total_split * 100, 1),
            "drink": round(drink / total_split * 100, 1),
            "other": round(other / total_split * 100, 1),
            "food_abs": food, "drink_abs": drink, "other_abs": other,
        },
        "busy_hours": [{"hour": h, "tx_count": hours_map[h]} for h in range(24)],
    }


# ---------------------------------------------------------------------------
# /api/analysis/branches   -  cross-branch KPI comparison
# ---------------------------------------------------------------------------
@app.get("/api/analysis/branches")
@ttl_cache
def analysis_branches(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
):
    """KPIs for every branch side-by-side. Combines Easy (transaction-level) and
    CM POS (monthly aggregates) so transition months show full coverage."""
    filters_tx = ["b.name != 'ALL'"]
    params_tx: dict = {}
    _group_filter(filters_tx, params_tx, group)
    _month_range_filter(filters_tx, params_tx, month_from, month_to, month, col="strftime('%Y-%m', st.sold_at)")
    where_tx = "WHERE " + " AND ".join(filters_tx)

    filters_cm = ["b.name != 'ALL'", "sms.source = 'CM POS'"]
    params_cm: dict = {}
    _group_filter(filters_cm, params_cm, group)
    _month_range_filter(filters_cm, params_cm, month_from, month_to, month, col="sms.period_month")
    where_cm = "WHERE " + " AND ".join(filters_cm)

    with get_db() as con:
        easy_rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   COUNT(*)                                           AS tx_count,
                   SUM(st.customer_count)                            AS customer_count,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2)                  AS revenue,
                   SUM(CASE WHEN st.discount > 0 THEN 1 ELSE 0 END) AS discount_tx,
                   ROUND(SUM(st.discount), 2)                        AS total_discount
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
            GROUP BY b.name
            """,
            params_tx,
        ).fetchall()
        cm_rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   SUM(sms.transaction_count) AS tx_count,
                   SUM(sms.customer_count)    AS customer_count,
                   ROUND(SUM(sms.net_sales), 2) AS revenue,
                   ROUND(SUM(sms.discounts),2) AS total_discount
            FROM sales_month_summary sms
            JOIN branches b ON b.branch_id = sms.branch_id
            {where_cm}
            GROUP BY b.name
            """,
            params_cm,
        ).fetchall()

    merged: dict = {}
    for r in easy_rows:
        merged[r["branch"]] = {
            "branch": r["branch"],
            "tx_count":       r["tx_count"] or 0,
            "customer_count": r["customer_count"] or 0,
            "revenue":        r["revenue"] or 0,
            "discount_tx":    r["discount_tx"] or 0,
            "total_discount": r["total_discount"] or 0,
        }
    for r in cm_rows:
        if r["branch"] in merged:
            m = merged[r["branch"]]
            m["tx_count"]       += r["tx_count"] or 0
            m["customer_count"] += r["customer_count"] or 0
            m["revenue"]        += r["revenue"] or 0
            m["total_discount"] += r["total_discount"] or 0
        else:
            merged[r["branch"]] = {
                "branch": r["branch"],
                "tx_count":       r["tx_count"] or 0,
                "customer_count": r["customer_count"] or 0,
                "revenue":        r["revenue"] or 0,
                "discount_tx":    0,
                "total_discount": r["total_discount"] or 0,
            }

    # Compute derived metrics
    out = []
    for m in merged.values():
        avg_ticket   = round(m["revenue"] / m["tx_count"], 2) if m["tx_count"] else 0
        discount_rate = round(100.0 * m["discount_tx"] / m["tx_count"], 1) if m["tx_count"] else 0
        out.append({
            **m,
            "avg_ticket":   avg_ticket,
            "discount_rate": discount_rate,
        })
    out.sort(key=lambda r: -r["revenue"])
    return out


# ---------------------------------------------------------------------------
# /api/analysis/cashiers   -  cashier leaderboard
# ---------------------------------------------------------------------------
@app.get("/api/analysis/cashiers")
def analysis_cashiers(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    limit:      int           = Query(30, ge=1, le=100),
):
    """Cashier performance ranked by revenue."""
    filters = ["b.name != 'ALL'", "TRIM(COALESCE(st.cashier,'')) != ''"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)
    params["limit"] = limit

    with get_db() as con:
        # Per-(cashier, branch) revenue so we can find the primary branch in Python
        br_rows = con.execute(
            f"""
            SELECT st.cashier, b.name AS branch,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS rev
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY st.cashier, b.name
            """, {k: v for k, v in params.items() if k != "limit"}
        ).fetchall()
        from collections import defaultdict
        per_cashier = defaultdict(list)
        for r in br_rows:
            per_cashier[r["cashier"]].append({"branch": r["branch"], "rev": r["rev"] or 0})

        rows = con.execute(
            f"""
            SELECT st.cashier,
                   COUNT(*)                                           AS tx_count,
                   SUM(st.customer_count)                            AS customer_count,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2)                  AS revenue,
                   ROUND(AVG(st.total_inc_vat), 2)                  AS avg_ticket,
                   SUM(CASE WHEN st.discount > 0 THEN 1 ELSE 0 END) AS discount_tx,
                   ROUND(SUM(st.discount), 2)                        AS total_discount,
                   ROUND(100.0 * SUM(CASE WHEN st.discount > 0 THEN 1 ELSE 0 END)
                         / COUNT(*), 1)                              AS discount_rate
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY st.cashier
            ORDER BY revenue DESC
            LIMIT :limit
            """,
            params,
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        brs = per_cashier.get(d["cashier"], [])
        brs.sort(key=lambda x: -x["rev"])
        d["primary_branch"] = brs[0]["branch"] if brs else None
        d["branch_count"]   = len(brs)
        d["branches"]       = [b["branch"] for b in brs]
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# /api/analysis/discounts   -  discount monitor
# ---------------------------------------------------------------------------
@app.get("/api/analysis/discounts")
def analysis_discounts(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
):
    """Discount analysis: overall summary, by branch, trend (all months), top cashiers."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)

    # Trend always spans all months  -  ignore the month param
    trend_filters = ["b.name != 'ALL'"]
    trend_params: dict = {}
    if branch:
        trend_filters.append("b.name = :branch")
        trend_params["branch"] = branch
    trend_where = "WHERE " + " AND ".join(trend_filters)

    with get_db() as con:
        overall = con.execute(
            f"""
            SELECT COUNT(*) AS total_tx,
                   SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END) AS discount_tx,
                   ROUND(SUM(discount), 2)                        AS total_discount,
                   ROUND(AVG(CASE WHEN discount > 0 THEN discount END), 0) AS avg_discount
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            """,
            params,
        ).fetchone()

        by_branch = con.execute(
            f"""
            SELECT b.name AS branch,
                   COUNT(*) AS total_tx,
                   SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END) AS discount_tx,
                   ROUND(100.0 * SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END)
                         / COUNT(*), 1) AS rate,
                   ROUND(SUM(discount), 2) AS total_discount,
                   ROUND(AVG(CASE WHEN discount > 0 THEN discount END), 0) AS avg_discount
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name
            ORDER BY rate DESC
            """,
            params,
        ).fetchall()

        trend = con.execute(
            f"""
            SELECT strftime('%Y-%m', st.sold_at) AS period_month,
                   COUNT(*) AS total_tx,
                   SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END) AS discount_tx,
                   ROUND(100.0 * SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END)
                         / COUNT(*), 1) AS rate,
                   ROUND(SUM(discount), 2) AS total_discount
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {trend_where}
            GROUP BY period_month
            ORDER BY period_month
            """,
            trend_params,
        ).fetchall()

        top_cashiers = con.execute(
            f"""
            SELECT st.cashier,
                   b.name AS branch,
                   COUNT(*) AS total_tx,
                   SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END) AS discount_tx,
                   ROUND(100.0 * SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END)
                         / COUNT(*), 1) AS rate,
                   ROUND(SUM(discount), 2) AS total_discount,
                   ROUND(AVG(CASE WHEN discount > 0 THEN discount END), 0) AS avg_discount
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY st.cashier, b.name
            HAVING SUM(discount) > 0
            ORDER BY total_discount DESC
            LIMIT 20
            """,
            params,
        ).fetchall()

    return {
        "overall":      dict(overall) if overall else {},
        "by_branch":    [dict(r) for r in by_branch],
        "trend":        [dict(r) for r in trend],
        "top_cashiers": [dict(r) for r in top_cashiers],
    }


# ===========================================================================
# NEW ADVANCED FEATURES  -  period compare, heatmap, anomalies, health, search
# ===========================================================================

def _build_period_filters(branch, month_from, month_to, date_from, date_to,
                          col_month="strftime('%Y-%m', st.sold_at)",
                          col_date="DATE(st.sold_at)"):
    """Helper: build SQL WHERE clauses from period range params (supports both month and date)."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    if date_from:
        filters.append(f"{col_date} >= :date_from")
        params["date_from"] = date_from
    if date_to:
        filters.append(f"{col_date} <= :date_to")
        params["date_to"] = date_to
    if month_from and not date_from:
        filters.append(f"{col_month} >= :month_from")
        params["month_from"] = month_from
    if month_to and not date_to:
        filters.append(f"{col_month} <= :month_to")
        params["month_to"] = month_to
    return filters, params


def _fetch_period_kpis(con, branch, month_from, month_to, date_from, date_to):
    """Aggregate KPIs from sales_transaction over an arbitrary period."""
    filters, params = _build_period_filters(branch, month_from, month_to, date_from, date_to)
    where = "WHERE " + " AND ".join(filters)
    row = con.execute(
        f"""
        SELECT COUNT(*)                                  AS tx_count,
               COALESCE(SUM(st.customer_count), 0)      AS customer_count,
               ROUND(COALESCE(SUM(st.total_inc_vat) / 1.07, 0), 2) AS revenue,
               ROUND(COALESCE(AVG(st.total_inc_vat), 0), 2) AS avg_ticket,
               ROUND(COALESCE(SUM(st.discount), 0), 2)  AS discount,
               ROUND(COALESCE(SUM(st.food_subtotal), 0), 2)  AS food,
               ROUND(COALESCE(SUM(st.drink_subtotal), 0), 2) AS drink
        FROM sales_transaction st
        JOIN branches b ON b.branch_id = st.branch_id
        {where}
        """,
        params,
    ).fetchone()

    # Top branches in this period
    branches_top = con.execute(
        f"""
        SELECT b.name AS branch,
               ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS revenue,
               COUNT(*) AS tx_count
        FROM sales_transaction st
        JOIN branches b ON b.branch_id = st.branch_id
        {where}
        GROUP BY b.name
        ORDER BY revenue DESC
        LIMIT 10
        """,
        params,
    ).fetchall()

    # Top SKUs (uses sales_sku_monthly w/ month range only)
    sku_filters = ["b.name != 'ALL'"]
    sku_params: dict = {}
    if branch:
        sku_filters.append("b.name = :branch")
        sku_params["branch"] = branch
    # Convert date_from/to to month range if necessary
    mf = month_from or (date_from[:7] if date_from else None)
    mt = month_to   or (date_to[:7]   if date_to   else None)
    if mf:
        sku_filters.append("sm.period_month >= :mf")
        sku_params["mf"] = mf
    if mt:
        sku_filters.append("sm.period_month <= :mt")
        sku_params["mt"] = mt
    sku_where = "WHERE " + " AND ".join(sku_filters)
    skus_top = con.execute(
        f"""
        SELECT sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
               ROUND(SUM(sm.subtotal_ex_vat), 2) AS revenue,
               ROUND(SUM(sm.qty), 0)            AS qty
        FROM sales_sku_monthly sm
        JOIN branches b ON b.branch_id = sm.branch_id
        JOIN skus sk    ON sk.sku_id    = sm.sku_id
        {sku_where}
        GROUP BY sk.sku_id
        ORDER BY revenue DESC
        LIMIT 10
        """,
        sku_params,
    ).fetchall()

    return {
        "revenue":        (row["revenue"]        or 0) if row else 0,
        "tx_count":       (row["tx_count"]       or 0) if row else 0,
        "customer_count": (row["customer_count"] or 0) if row else 0,
        "avg_ticket":     (row["avg_ticket"]     or 0) if row else 0,
        "discount":       (row["discount"]       or 0) if row else 0,
        "food":           (row["food"]           or 0) if row else 0,
        "drink":          (row["drink"]          or 0) if row else 0,
        "top_branches":   [dict(b) for b in branches_top],
        "top_skus":       [dict(s) for s in skus_top],
    }


# ---------------------------------------------------------------------------
# /api/analysis/period_compare   -  A vs B range comparison
# ---------------------------------------------------------------------------
@app.get("/api/analysis/period_compare")
@ttl_cache
def period_compare(
    a_month_from: Optional[str] = Query(None),
    a_month_to:   Optional[str] = Query(None),
    a_date_from:  Optional[str] = Query(None),
    a_date_to:    Optional[str] = Query(None),
    b_month_from: Optional[str] = Query(None),
    b_month_to:   Optional[str] = Query(None),
    b_date_from:  Optional[str] = Query(None),
    b_date_to:    Optional[str] = Query(None),
    c_month_from: Optional[str] = Query(None),
    c_month_to:   Optional[str] = Query(None),
    c_date_from:  Optional[str] = Query(None),
    c_date_to:    Optional[str] = Query(None),
    d_month_from: Optional[str] = Query(None),
    d_month_to:   Optional[str] = Query(None),
    d_date_from:  Optional[str] = Query(None),
    d_date_to:    Optional[str] = Query(None),
    branch:       Optional[str] = Query(None),
):
    """Compare up to 4 arbitrary periods A/B/C/D. Returns KPIs, deltas, top branches/SKUs."""
    def pct(a, b):
        if not b: return None
        return round((a - b) / b * 100, 1)

    def _has(mf, mt, df, dt):
        return any([mf, mt, df, dt])

    def _get_rev(period_data, name):
        if not period_data: return 0
        return next((x["revenue"] for x in period_data["top_branches"] if x["branch"] == name), 0)

    with get_db() as con:
        a = _fetch_period_kpis(con, branch, a_month_from, a_month_to, a_date_from, a_date_to)
        b = _fetch_period_kpis(con, branch, b_month_from, b_month_to, b_date_from, b_date_to)
        c = _fetch_period_kpis(con, branch, c_month_from, c_month_to, c_date_from, c_date_to) \
            if _has(c_month_from, c_month_to, c_date_from, c_date_to) else None
        d = _fetch_period_kpis(con, branch, d_month_from, d_month_to, d_date_from, d_date_to) \
            if _has(d_month_from, d_month_to, d_date_from, d_date_to) else None

    # Collect all branch names across active periods
    all_names = set(
        [br["branch"] for br in a["top_branches"]] +
        [br["branch"] for br in b["top_branches"]] +
        ([br["branch"] for br in c["top_branches"]] if c else []) +
        ([br["branch"] for br in d["top_branches"]] if d else [])
    )

    rank_a = {br["branch"]: i + 1 for i, br in enumerate(a["top_branches"])}
    rank_b = {br["branch"]: i + 1 for i, br in enumerate(b["top_branches"])}
    rank_c = {br["branch"]: i + 1 for i, br in enumerate(c["top_branches"])} if c else {}
    rank_d = {br["branch"]: i + 1 for i, br in enumerate(d["top_branches"])} if d else {}

    branch_changes = []
    for name in all_names:
        rev_a = _get_rev(a, name)
        rev_b = _get_rev(b, name)
        rev_c = _get_rev(c, name)
        rev_d = _get_rev(d, name)
        branch_changes.append({
            "branch":     name,
            "revenue_a":  rev_a, "revenue_b": rev_b,
            "revenue_c":  rev_c, "revenue_d": rev_d,
            "delta_b_pct": pct(rev_a, rev_b),
            "delta_c_pct": pct(rev_a, rev_c) if c else None,
            "delta_d_pct": pct(rev_a, rev_d) if d else None,
            "rank_a": rank_a.get(name), "rank_b": rank_b.get(name),
            "rank_c": rank_c.get(name), "rank_d": rank_d.get(name),
            "rank_shift": (rank_b.get(name, 99) - rank_a.get(name, 99)),
        })
    branch_changes.sort(key=lambda x: -(x["revenue_a"] or 0))

    return {
        "a": {**a, "label": _period_label(a_month_from, a_month_to, a_date_from, a_date_to)},
        "b": {**b, "label": _period_label(b_month_from, b_month_to, b_date_from, b_date_to)},
        "c": {**c, "label": _period_label(c_month_from, c_month_to, c_date_from, c_date_to)} if c else None,
        "d": {**d, "label": _period_label(d_month_from, d_month_to, d_date_from, d_date_to)} if d else None,
        "delta": {
            "revenue":        pct(a["revenue"],        b["revenue"]),
            "tx_count":       pct(a["tx_count"],       b["tx_count"]),
            "customer_count": pct(a["customer_count"], b["customer_count"]),
            "avg_ticket":     pct(a["avg_ticket"],     b["avg_ticket"]),
            "discount":       pct(a["discount"],       b["discount"]),
        },
        "branch_changes": branch_changes,
    }


def _period_label(mf, mt, df, dt):
    if df or dt:
        return f"{df or '...'} â†' {dt or '...'}"
    if mf or mt:
        return f"{mf or '...'} â†' {mt or '...'}"
    return "All time"


# ---------------------------------------------------------------------------
# /api/analysis/heatmap_branch   -  branch  x  period heatmap
# ---------------------------------------------------------------------------
@app.get("/api/analysis/heatmap_branch")
@ttl_cache
def heatmap_branch(
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    date_from:   Optional[str] = Query(None),
    date_to:     Optional[str] = Query(None),
    granularity: str           = Query("weekly"),  # daily|weekly|monthly
):
    """Branch × period revenue grid (ex-VAT).
    Monthly: Easy from sales_sku_monthly (source=Easy) + CM POS from sales_month_summary (source=CM POS).
    Daily/Weekly: Easy from sales_transaction + CM POS monthly aggregates (period label = YYYY-MM)."""

    # ── MONTHLY: Easy SKU-level + CM POS monthly summary ───────────────────────
    if granularity == "monthly":
        # Easy: use SKU-level subtotal (ex-VAT) — source='Easy' only to avoid double-counting
        filters_easy = ["b.name != 'ALL'", "sm.source = 'Easy'"]
        params_easy: dict = {}
        _month_range_filter(filters_easy, params_easy, month_from, month_to, None, col="sm.period_month")
        where_easy = "WHERE " + " AND ".join(filters_easy)

        # CM POS: use monthly net_sales aggregate — source='CM POS' only
        filters_cm = ["b.name != 'ALL'", "sms.source = 'CM POS'"]
        params_cm: dict = {}
        _month_range_filter(filters_cm, params_cm, month_from, month_to, None, col="sms.period_month")
        where_cm = "WHERE " + " AND ".join(filters_cm)

        with get_db() as con:
            easy_rows = con.execute(
                f"""
                SELECT b.name AS branch,
                       sm.period_month AS period,
                       ROUND(SUM(sm.subtotal_ex_vat), 2) AS revenue
                FROM sales_sku_monthly sm
                JOIN branches b ON b.branch_id = sm.branch_id
                {where_easy}
                GROUP BY b.name, sm.period_month
                """,
                params_easy,
            ).fetchall()
            cm_rows = con.execute(
                f"""
                SELECT b.name AS branch,
                       sms.period_month AS period,
                       ROUND(SUM(sms.net_sales), 2) AS revenue
                FROM sales_month_summary sms
                JOIN branches b ON b.branch_id = sms.branch_id
                {where_cm}
                GROUP BY b.name, sms.period_month
                """,
                params_cm,
            ).fetchall()

        # Additive merge — Easy branches and CM POS branches use different POS systems
        # so the same branch×month should never appear in both, but SUM defensively if it does
        merged: dict = {}
        for r in easy_rows:
            merged[(r["branch"], r["period"])] = {"branch": r["branch"], "period": r["period"], "revenue": r["revenue"] or 0}
        for r in cm_rows:
            k = (r["branch"], r["period"])
            if k in merged:
                merged[k]["revenue"] = round(merged[k]["revenue"] + (r["revenue"] or 0), 2)
            else:
                merged[k] = {"branch": r["branch"], "period": r["period"], "revenue": r["revenue"] or 0}
        out = sorted(merged.values(), key=lambda x: (x["branch"], x["period"]))
        return out

    # ── DAILY / WEEKLY: Easy transaction-level + CM POS monthly fallback ────────
    # CM POS has no daily granularity; include its monthly aggregates so those
    # branches still appear in the heatmap (period label will be YYYY-MM)
    period_expr = "DATE(st.sold_at)" if granularity == "daily" else _iso_week_sql('st.sold_at')
    filters, params = _build_period_filters(None, month_from, month_to, date_from, date_to)
    where = "WHERE " + " AND ".join(filters)

    filters_cm2 = ["b.name != 'ALL'", "sms.source = 'CM POS'"]
    params_cm2: dict = {}
    _month_range_filter(filters_cm2, params_cm2, month_from, month_to, None, col="sms.period_month")
    where_cm2 = "WHERE " + " AND ".join(filters_cm2)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   {period_expr} AS period,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS revenue,
                   COUNT(*) AS tx_count
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name, period
            ORDER BY b.name, period
            """,
            params,
        ).fetchall()
        cm2_rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   sms.period_month AS period,
                   ROUND(SUM(sms.net_sales), 2) AS revenue,
                   0 AS tx_count
            FROM sales_month_summary sms
            JOIN branches b ON b.branch_id = sms.branch_id
            {where_cm2}
            GROUP BY b.name, sms.period_month
            """,
            params_cm2,
        ).fetchall()
    return [dict(r) for r in rows] + [dict(r) for r in cm2_rows]


# ---------------------------------------------------------------------------
# /api/analysis/anomalies   -  z-score outliers
# ---------------------------------------------------------------------------
@app.get("/api/analysis/anomalies")
@ttl_cache
def analysis_anomalies(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    threshold:  float         = Query(1.5, description="z-score threshold"),
):
    """
    Statistical outliers + insights. Returns:
    - spikes / dips in revenue (z > threshold)
    - fastest-growing / biggest-dropping branches month-over-month
    - top-performing / underperforming SKUs vs their own average
    Combines Easy (sales_transaction) + CM POS (sales_month_summary) so all branches are included.
    """
    insights: List[dict] = []

    # 1. Monthly revenue z-score: Easy tx + CM POS monthly aggregates
    filters_tx = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters_tx.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters_tx, params, month_from, month_to, None,
                        col="strftime('%Y-%m', st.sold_at)")
    where_tx = "WHERE " + " AND ".join(filters_tx)

    filters_cm = ["b.name != 'ALL'", "sms.source = 'CM POS'"]
    params_cm: dict = {}
    if branch:
        filters_cm.append("b.name = :branch")
        params_cm["branch"] = branch
    _month_range_filter(filters_cm, params_cm, month_from, month_to, None, col="sms.period_month")
    where_cm = "WHERE " + " AND ".join(filters_cm)

    with get_db() as con:
        easy_monthly = con.execute(
            f"""
            SELECT strftime('%Y-%m', st.sold_at) AS month,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
            GROUP BY month
            ORDER BY month
            """,
            params,
        ).fetchall()

        cm_monthly = con.execute(
            f"""
            SELECT sms.period_month AS month,
                   ROUND(SUM(sms.net_sales), 2) AS revenue
            FROM sales_month_summary sms
            JOIN branches b ON b.branch_id = sms.branch_id
            {where_cm}
            GROUP BY sms.period_month
            ORDER BY sms.period_month
            """,
            params_cm,
        ).fetchall()

        # Branch MoM growth — Easy tx
        bgrowth_easy = con.execute(
            """
            SELECT b.name AS branch,
                   strftime('%Y-%m', st.sold_at) AS month,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            WHERE b.name != 'ALL'
            GROUP BY b.name, month
            ORDER BY b.name, month
            """,
        ).fetchall()

        # Branch MoM growth — CM POS
        bgrowth_cm = con.execute(
            """
            SELECT b.name AS branch,
                   sms.period_month AS month,
                   ROUND(SUM(sms.net_sales), 2) AS revenue
            FROM sales_month_summary sms
            JOIN branches b ON b.branch_id = sms.branch_id
            WHERE b.name != 'ALL' AND sms.source = 'CM POS'
            GROUP BY b.name, sms.period_month
            ORDER BY b.name, sms.period_month
            """,
        ).fetchall()

    # Merge Easy + CM POS monthly totals
    rev_by_month: dict = {}
    for r in easy_monthly:
        rev_by_month[r["month"]] = (rev_by_month.get(r["month"]) or 0) + (r["revenue"] or 0)
    for r in cm_monthly:
        rev_by_month[r["month"]] = (rev_by_month.get(r["month"]) or 0) + (r["revenue"] or 0)
    monthly_combined = [{"month": m, "revenue": v} for m, v in sorted(rev_by_month.items())]

    # Merge branch growth rows
    bgrowth_map: dict = {}
    for r in bgrowth_easy:
        bgrowth_map.setdefault(r["branch"], {})[r["month"]] = (r["revenue"] or 0)
    for r in bgrowth_cm:
        prev = bgrowth_map.setdefault(r["branch"], {}).get(r["month"]) or 0
        bgrowth_map[r["branch"]][r["month"]] = prev + (r["revenue"] or 0)
    bgrowth = [
        {"branch": br, "month": mo, "revenue": rev}
        for br, months in bgrowth_map.items()
        for mo, rev in sorted(months.items())
    ]
    monthly = monthly_combined

    revs = [r["revenue"] for r in monthly if r["revenue"]]
    if len(revs) >= 3:
        mean = sum(revs) / len(revs)
        var  = sum((v - mean) ** 2 for v in revs) / len(revs)
        sd   = var ** 0.5
        if sd > 0:
            for r in monthly:
                if not r["revenue"]:
                    continue
                z = (r["revenue"] - mean) / sd
                if z >= threshold:
                    insights.append({
                        "type": "spike", "severity": "good",
                        "metric": "revenue", "period": r["month"],
                        "value": r["revenue"], "z_score": round(z, 2),
                        "message": f"📈 รายได้พุ่งในเดือน {r['month']} — {r['revenue']:,.0f} ({(z * sd / mean * 100):+.1f}% เทียบค่าเฉลี่ย)",
                    })
                elif z <= -threshold:
                    insights.append({
                        "type": "dip", "severity": "bad",
                        "metric": "revenue", "period": r["month"],
                        "value": r["revenue"], "z_score": round(z, 2),
                        "message": f"📉 รายได้ลดในเดือน {r['month']} — {r['revenue']:,.0f} ({(z * sd / mean * 100):+.1f}% เทียบค่าเฉลี่ย)",
                    })

    # Branch MoM growth
    by_branch: dict = {}
    for r in bgrowth:
        by_branch.setdefault(r["branch"], []).append(r)
    growths = []
    for name, recs in by_branch.items():
        if len(recs) >= 2:
            last = recs[-1]["revenue"] or 0
            prev = recs[-2]["revenue"] or 0
            if prev > 0:
                growths.append({
                    "branch": name, "growth_pct": round((last - prev) / prev * 100, 1),
                    "last_revenue": last, "prev_revenue": prev,
                    "last_month": recs[-1]["month"], "prev_month": recs[-2]["month"],
                })
    growths.sort(key=lambda x: -x["growth_pct"])
    if growths:
        top = growths[0]
        if top["growth_pct"] > 5:
            insights.append({
                "type": "growth", "severity": "good", "metric": "branch_mom",
                "branch": top["branch"], "value": top["growth_pct"],
                "message": f"🚀 เติบโตเร็วสุด: {top['branch']} +{top['growth_pct']:.1f}% MoM ({top['last_revenue']:,.0f})",
            })
        worst = growths[-1]
        if worst["growth_pct"] < -5:
            insights.append({
                "type": "drop", "severity": "bad", "metric": "branch_mom",
                "branch": worst["branch"], "value": worst["growth_pct"],
                "message": f"⚠️ ลดมากสุด: {worst['branch']} {worst['growth_pct']:.1f}% MoM ({worst['last_revenue']:,.0f})",
            })

    # BoM cost % spike / dip detection (MoM delta >= 5 pp)
    # Phase 18d: migrated to _cogs_case helper so monthly cost overrides + tier
    # exclusion (incl. plain BUFFET) are unified with the rest of the app.
    try:
        with get_db() as con2:
            cost_monthly_rows = con2.execute(f"""
                SELECT s.period_month AS month,
                       ROUND(SUM({_cogs_case('s','sk','c')}), 2) AS bom_cost,
                       ROUND(SUM(s.subtotal_ex_vat), 2)          AS revenue
                FROM sales_sku_monthly s
                JOIN skus sk     ON sk.sku_id = s.sku_id
                LEFT JOIN sku_cost c ON c.sku_id = s.sku_id
                JOIN branches b  ON b.branch_id = s.branch_id
                WHERE b.name != 'ALL'
                GROUP BY s.period_month
                ORDER BY s.period_month
            """).fetchall()
        cost_series = [
            (r["month"], r["bom_cost"], r["revenue"])
            for r in cost_monthly_rows
            if r["bom_cost"] and r["revenue"] and r["revenue"] > 0
        ]
        for i in range(1, len(cost_series)):
            m, bc, rev   = cost_series[i]
            _, pbc, prev = cost_series[i - 1]
            curr_pct = bc  / rev  * 100
            prev_pct = pbc / prev * 100
            delta = curr_pct - prev_pct
            if delta >= 5:
                insights.append({
                    "type": "cost_spike", "severity": "bad",
                    "metric": "bom_cost_pct", "period": m,
                    "value": round(curr_pct, 1),
                    "message": f"⚠️ ต้นทุน BoM พุ่งในเดือน {m} — {curr_pct:.1f}% of revenue (+{delta:.1f}pp จากเดือนก่อน)",
                })
            elif delta <= -5:
                insights.append({
                    "type": "cost_drop", "severity": "good",
                    "metric": "bom_cost_pct", "period": m,
                    "value": round(curr_pct, 1),
                    "message": f"✅ ต้นทุน BoM ลดในเดือน {m} — {curr_pct:.1f}% of revenue ({delta:.1f}pp จากเดือนก่อน)",
                })
    except Exception:
        pass

    return {"insights": insights, "branch_growth": growths[:10]}


# ---------------------------------------------------------------------------
# /api/analysis/health   -  composite Health Score per branch
# ---------------------------------------------------------------------------
@app.get("/api/analysis/health")
def analysis_health(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
):
    """
    Composite health score per branch (0-100):
      = 0.4 * revenue_score    (normalized vs leader)
      + 0.3 * growth_score     (last vs prior month, capped ±50%)
      + 0.2 * consistency_score (1 - coefficient of variation)
      + 0.1 * momentum_score   (slope of last 3 periods)
    Combines Easy (sales_transaction) + CM POS (sales_month_summary) so all branches are scored.
    """
    filters_tx = ["b.name != 'ALL'"]
    params_tx: dict = {}
    _group_filter(filters_tx, params_tx, group)
    _month_range_filter(filters_tx, params_tx, month_from, month_to, None,
                        col="strftime('%Y-%m', st.sold_at)")
    where_tx = "WHERE " + " AND ".join(filters_tx)

    filters_cm = ["b.name != 'ALL'", "sms.source = 'CM POS'"]
    params_cm: dict = {}
    _group_filter(filters_cm, params_cm, group)
    _month_range_filter(filters_cm, params_cm, month_from, month_to, None, col="sms.period_month")
    where_cm = "WHERE " + " AND ".join(filters_cm)

    with get_db() as con:
        easy_rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   strftime('%Y-%m', st.sold_at) AS month,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
            GROUP BY b.name, month
            ORDER BY b.name, month
            """,
            params_tx,
        ).fetchall()
        cm_rows_h = con.execute(
            f"""
            SELECT b.name AS branch,
                   sms.period_month AS month,
                   ROUND(SUM(sms.net_sales), 2) AS revenue
            FROM sales_month_summary sms
            JOIN branches b ON b.branch_id = sms.branch_id
            {where_cm}
            GROUP BY b.name, sms.period_month
            ORDER BY b.name, sms.period_month
            """,
            params_cm,
        ).fetchall()

    # Merge Easy + CM POS per branch×month (additive — different POS systems)
    branch_month_h: dict = {}
    for r in easy_rows:
        branch_month_h.setdefault(r["branch"], {})[r["month"]] = \
            branch_month_h.get(r["branch"], {}).get(r["month"], 0) + (r["revenue"] or 0)
    for r in cm_rows_h:
        branch_month_h.setdefault(r["branch"], {})[r["month"]] = \
            branch_month_h.get(r["branch"], {}).get(r["month"], 0) + (r["revenue"] or 0)

    by_branch: dict = {
        br: [months[m] for m in sorted(months)]
        for br, months in branch_month_h.items()
    }

    if not by_branch:
        return []

    max_total = max(sum(v) for v in by_branch.values()) or 1

    out = []
    for branch, revs in by_branch.items():
        total = sum(revs)
        revenue_score = min(100, total / max_total * 100)

        if len(revs) >= 2 and revs[-2] > 0:
            growth_pct = (revs[-1] - revs[-2]) / revs[-2] * 100
            growth_score = max(0, min(100, 50 + growth_pct))
        else:
            growth_score = 50.0
            growth_pct = 0.0

        if len(revs) >= 2:
            mean = sum(revs) / len(revs)
            var  = sum((v - mean) ** 2 for v in revs) / len(revs)
            sd   = var ** 0.5
            cv   = sd / mean if mean else 1
            consistency_score = max(0, min(100, (1 - cv) * 100))
        else:
            consistency_score = 50.0

        if len(revs) >= 3:
            last3 = revs[-3:]
            slope = (last3[-1] - last3[0]) / (last3[0] or 1) * 100
            momentum_score = max(0, min(100, 50 + slope))
        else:
            momentum_score = 50.0

        composite = (
            0.4 * revenue_score +
            0.3 * growth_score  +
            0.2 * consistency_score +
            0.1 * momentum_score
        )
        out.append({
            "branch": branch,
            "score": round(composite, 1),
            "revenue_score":     round(revenue_score, 1),
            "growth_score":      round(growth_score, 1),
            "consistency_score": round(consistency_score, 1),
            "momentum_score":    round(momentum_score, 1),
            "total_revenue":     round(total, 2),
            "last_month_revenue": revs[-1] if revs else 0,
            "growth_pct":        round(growth_pct, 1),
        })
    out.sort(key=lambda x: -x["score"])
    return out


# ---------------------------------------------------------------------------
# /api/search   -  autocomplete for branches + SKUs
# ---------------------------------------------------------------------------
@app.get("/api/search")
def global_search(q: str = Query(..., min_length=1), limit: int = Query(15, ge=1, le=50)):
    """Autocomplete search across branches and SKUs."""
    qlike = f"%{q.lower()}%"
    with get_db() as con:
        branches = con.execute(
            """
            SELECT branch_id AS id, name
            FROM branches
            WHERE name != 'ALL' AND LOWER(name) LIKE ?
            ORDER BY name
            LIMIT ?
            """,
            (qlike, limit),
        ).fetchall()
        skus = con.execute(
            """
            SELECT sk.sku_code, sk.description, c.name AS category
            FROM skus sk
            JOIN categories c ON c.category_id = sk.category_id
            WHERE LOWER(sk.sku_code) LIKE ? OR LOWER(COALESCE(sk.description,'')) LIKE ?
            ORDER BY sk.sku_code
            LIMIT ?
            """,
            (qlike, qlike, limit),
        ).fetchall()
    return {
        "branches": [{"type": "branch", "id": r["id"], "name": r["name"]} for r in branches],
        "skus":     [{"type": "sku", "sku_code": r["sku_code"],
                      "description": r["description"], "category": r["category"]}
                     for r in skus],
    }


# ---------------------------------------------------------------------------
# /api/closed_hours   -  operating hours per branch per day-of-week
# ---------------------------------------------------------------------------
class ClosedHoursEntry(BaseModel):
    branch_id: int
    dow:       int   # 0=Sun â€¦ 6=Sat
    hour_from: int   # 0..23 inclusive (open hour)
    hour_to:   int   # 0..23 inclusive (last open hour)
    is_closed: bool = False


def _init_closed_hours():
    if not DB_PATH.exists():
        return
    with get_db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS closed_hours (
                branch_id INTEGER NOT NULL REFERENCES branches(branch_id),
                dow       INTEGER NOT NULL,
                hour_from INTEGER NOT NULL DEFAULT 0,
                hour_to   INTEGER NOT NULL DEFAULT 23,
                is_closed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (branch_id, dow)
            )
        """)
        con.commit()
_init_closed_hours()


@app.get("/api/closed_hours")
def get_closed_hours():
    with get_db() as con:
        rows = con.execute(
            """
            SELECT b.branch_id, b.name AS branch, ch.dow,
                   COALESCE(ch.hour_from, 10)  AS hour_from,
                   COALESCE(ch.hour_to,   22)  AS hour_to,
                   COALESCE(ch.is_closed, 0)   AS is_closed
            FROM branches b
            LEFT JOIN closed_hours ch ON ch.branch_id = b.branch_id
            WHERE b.name != 'ALL'
            ORDER BY b.name, ch.dow
            """
        ).fetchall()
    return [dict(r) for r in rows]


@app.put("/api/closed_hours")
def put_closed_hours(entries: List[ClosedHoursEntry]):
    with get_db() as con:
        cur = con.cursor()
        for e in entries:
            cur.execute(
                """INSERT OR REPLACE INTO closed_hours
                       (branch_id, dow, hour_from, hour_to, is_closed)
                   VALUES(?,?,?,?,?)""",
                (e.branch_id, e.dow, e.hour_from, e.hour_to, 1 if e.is_closed else 0),
            )
        con.commit()
    return {"ok": True, "saved": len(entries)}


# ===========================================================================
# ADVANCED ANALYTICS  -  yoy, forecast, cohort, affinity, percentiles, ai
# ===========================================================================

@app.get("/api/analysis/yoy")
@ttl_cache
def analysis_yoy(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
):
    """For each month in range, return current revenue + same month last year."""
    if not month_from and not month_to:
        return []
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    where_branch = " AND " + " AND ".join(filters[1:]) if len(filters) > 1 else ""

    with get_db() as con:
        # Pull all months in range
        rng = con.execute(
            f"""
            SELECT sm.period_month, ROUND(SUM(sm.subtotal_ex_vat),2) AS revenue
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            WHERE b.name != 'ALL'
              AND sm.period_month BETWEEN :mf AND :mt
              {where_branch}
            GROUP BY sm.period_month
            ORDER BY sm.period_month
            """,
            {**params, "mf": month_from or "0000-00", "mt": month_to or "9999-99"},
        ).fetchall()

        out = []
        for r in rng:
            cur_mo = r["period_month"]
            # Same month last year
            yr, mo = cur_mo.split("-")
            ly_mo = f"{int(yr)-1}-{mo}"
            ly = con.execute(
                f"""
                SELECT ROUND(SUM(sm.subtotal_ex_vat),2) AS revenue
                FROM sales_sku_monthly sm
                JOIN branches b ON b.branch_id = sm.branch_id
                WHERE b.name != 'ALL' AND sm.period_month = :ly_mo {where_branch}
                """,
                {**params, "ly_mo": ly_mo},
            ).fetchone()
            ly_rev = (ly["revenue"] if ly and ly["revenue"] else 0) or 0
            yoy_pct = ((r["revenue"] - ly_rev) / ly_rev * 100) if ly_rev > 0 else None
            out.append({
                "period_month": cur_mo,
                "revenue":      r["revenue"],
                "ly_period":    ly_mo,
                "ly_revenue":   ly_rev,
                "yoy_pct":      round(yoy_pct, 1) if yoy_pct is not None else None,
            })
    return out


@app.get("/api/analysis/forecast")
@ttl_cache
def analysis_forecast(
    branch: Optional[str] = Query(None),
    group:  Optional[str] = Query(None),
):
    """
    Projects revenue for the current month using daily run-rate from transactions
    in the latest month. Returns mtd, days_elapsed, days_total, projected, confidence_lo, confidence_hi.
    """
    filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        latest = con.execute(
            f"""
            SELECT MAX(DATE(st.sold_at)) AS d
            FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id {where}
            """, params
        ).fetchone()
        if not latest or not latest["d"]:
            return {"available": False}
        from datetime import datetime as _dt, timedelta as _td
        from calendar import monthrange
        d = _dt.strptime(latest["d"], "%Y-%m-%d")
        period = d.strftime("%Y-%m")
        params["period"] = period
        filters_p = filters + ["strftime('%Y-%m', st.sold_at) = :period"]
        where_p = "WHERE " + " AND ".join(filters_p)
        daily = con.execute(
            f"""
            SELECT DATE(st.sold_at) AS day, SUM(st.total_inc_vat) / 1.07 AS rev
            FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id {where_p}
            GROUP BY day ORDER BY day
            """, params
        ).fetchall()
    if not daily:
        return {"available": False}
    days_in_month = monthrange(d.year, d.month)[1]
    days_elapsed = len(daily)
    revs = [r["rev"] or 0 for r in daily]
    mtd = sum(revs)
    avg_daily = mtd / max(1, days_elapsed)
    projected = avg_daily * days_in_month
    # Confidence band Â±1 SD of daily revenue projected over remaining days
    if len(revs) >= 2:
        mean = sum(revs)/len(revs)
        sd = (sum((v-mean)**2 for v in revs)/len(revs)) ** 0.5
        remain = days_in_month - days_elapsed
        band = sd * (remain ** 0.5)
    else:
        band = 0
    return {
        "available":  True,
        "period":     period,
        "days_elapsed": days_elapsed,
        "days_total":   days_in_month,
        "mtd":          round(mtd, 2),
        "avg_daily":    round(avg_daily, 2),
        "projected":    round(projected, 2),
        "lo":           round(projected - band, 2),
        "hi":           round(projected + band, 2),
        "daily":        [{"day": r["day"], "revenue": r["rev"] or 0} for r in daily],
    }


@app.get("/api/analysis/cohort")
@ttl_cache
def analysis_cohort(
    branch: Optional[str] = Query(None),
    group:  Optional[str] = Query(None),
):
    """
    Approximate cohort retention: groups receipts by cashier+date as a proxy customer
    if no customer ID. Since restaurants typically lack stable customer IDs, this falls
    back to grouping receipts per month and showing same-period repeat-visit proxy
    (table_no + cashier as a weak identifier).
    Returns a {cohort_month: {0:100, 1:retention%, ...}} matrix.
    """
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT strftime('%Y-%m', st.sold_at) AS month,
                   COUNT(DISTINCT st.receipt_no) AS tx_count,
                   SUM(st.customer_count)        AS customer_count,
                   ROUND(SUM(st.total_inc_vat) / 1.07,2) AS revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY month
            ORDER BY month
            """, params,
        ).fetchall()
    # Without unique customer IDs, render a returning-customer proxy as customer_count growth rate vs avg
    months = [r["month"] for r in rows]
    if not months:
        return {"cohorts": [], "matrix": []}
    avgCust = sum((r["customer_count"] or 0) for r in rows) / len(rows) or 1
    matrix = []
    for r in rows:
        # Synthetic retention curve: index against avg customers
        base = (r["customer_count"] or 0)
        ratio = base / avgCust * 100 if avgCust else 0
        matrix.append({
            "cohort":  r["month"],
            "tx":      r["tx_count"],
            "customers": r["customer_count"] or 0,
            "revenue": r["revenue"] or 0,
            "vs_avg":  round(ratio, 1),
        })
    return {
        "note": "Restaurant POS data lacks unique customer IDs; this view shows monthly customer volume vs the period average - useful as a soft retention signal.",
        "cohorts": months,
        "matrix":  matrix,
    }


@app.get("/api/analysis/affinity")
@ttl_cache
def analysis_affinity(
    sku_code: Optional[str] = Query(None, description="Anchor SKU  -  find what's bought with it"),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    limit:      int           = Query(10, ge=1, le=50),
):
    """
    Item affinity via receipt co-occurrence.
    NOTE: sales_transaction has totals only, not line items per receipt.
    Use sales_sku_monthly grouped at (branch, month) as a proxy basket.
    Returns SKUs most likely to appear in the same period+branch as the anchor.
    """
    if not sku_code:
        return {"items": [], "note": "Pass sku_code to anchor"}
    filters = ["b.name != 'ALL'"]
    params: dict = {"anchor": sku_code}
    if branch:
        filters.append("b.name = :branch"); params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, None, col="sm.period_month")
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        # Find baskets (branch+month) where anchor sku appeared
        anchor_baskets = con.execute(
            f"""
            SELECT DISTINCT sm.branch_id || '|' || sm.period_month AS basket
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            JOIN skus sk    ON sk.sku_id    = sm.sku_id
            {where} AND sk.sku_code = :anchor
            """, params,
        ).fetchall()
        baskets = {r["basket"] for r in anchor_baskets}
        if not baskets:
            return {"items": [], "anchor": sku_code, "baskets": 0}

        # Find co-occurring SKUs across those baskets
        rows = con.execute(
            f"""
            SELECT sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
                   COUNT(DISTINCT sm.branch_id || '|' || sm.period_month) AS co_count,
                   ROUND(SUM(sm.subtotal_ex_vat), 2) AS co_revenue,
                   ROUND(SUM(sm.qty), 0) AS co_qty
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            JOIN skus sk    ON sk.sku_id    = sm.sku_id
            {where} AND sk.sku_code != :anchor
              AND sm.branch_id || '|' || sm.period_month IN ({','.join('?' for _ in baskets)})
            GROUP BY sk.sku_id
            ORDER BY co_count DESC, co_revenue DESC
            LIMIT :limit
            """, {**params, "limit": limit, **{f"_b{i}": v for i, v in enumerate(baskets)}},
        ).fetchall() if False else None

        # The IN clause with named params + dict isn't well-supported in sqlite3  -  use a tuple-based query instead
        basket_list = list(baskets)
        placeholders = ",".join("?" * len(basket_list))
        # Rebuild with positional params
        sql = f"""
            SELECT sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
                   COUNT(DISTINCT sm.branch_id || '|' || sm.period_month) AS co_count,
                   ROUND(SUM(sm.subtotal_ex_vat), 2) AS co_revenue,
                   ROUND(SUM(sm.qty), 0) AS co_qty
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            JOIN skus sk    ON sk.sku_id    = sm.sku_id
            WHERE b.name != 'ALL' AND sk.sku_code != ?
              AND sm.branch_id || '|' || sm.period_month IN ({placeholders})
            GROUP BY sk.sku_id
            ORDER BY co_count DESC, co_revenue DESC
            LIMIT ?
        """
        rows = con.execute(sql, [sku_code, *basket_list, limit]).fetchall()
    n_baskets = len(baskets)
    return {
        "anchor":   sku_code,
        "baskets":  n_baskets,
        "items":    [{**dict(r), "lift": round((r["co_count"]/n_baskets)*100, 1)} for r in rows],
    }


@app.get("/api/analysis/percentiles")
@ttl_cache
def analysis_percentiles(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """For each branch, compute percentile rank in revenue, avg ticket, customers."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    _month_range_filter(filters, params, month_from, month_to, None, col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   COUNT(*)                                AS tx,
                   SUM(st.customer_count)                  AS cust,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2)        AS revenue,
                   ROUND(AVG(st.total_inc_vat), 2)        AS avg_ticket
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name
            """, params,
        ).fetchall()
    arr = [dict(r) for r in rows]
    def pctile(values, v):
        if not values: return 0
        ordered = sorted(values)
        rank = sum(1 for x in ordered if x <= v)
        return round(rank / len(ordered) * 100)
    revs   = [r["revenue"]   or 0 for r in arr]
    custs  = [r["cust"]      or 0 for r in arr]
    avgs   = [r["avg_ticket"] or 0 for r in arr]
    for r in arr:
        r["p_revenue"]    = pctile(revs,  r["revenue"]   or 0)
        r["p_customers"]  = pctile(custs, r["cust"]      or 0)
        r["p_avg_ticket"] = pctile(avgs,  r["avg_ticket"] or 0)
    arr.sort(key=lambda x: -(x["revenue"] or 0))
    return arr


@app.get("/api/cost/diff")
@ttl_cache
def cost_diff(threshold_pct: float = Query(10.0, description="Flag changes >= this %")):
    """List SKUs whose unit_cost was recently updated and the change vs prior value (rough)."""
    # We don't have cost history  -  return SKUs with cost recently set as a placeholder
    with get_db() as con:
        rows = con.execute(
            """
            SELECT sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
                   sc.unit_cost, sc.updated_at
            FROM sku_cost sc JOIN skus sk ON sk.sku_id = sc.sku_id
            WHERE sc.updated_at IS NOT NULL
            ORDER BY sc.updated_at DESC
            LIMIT 20
            """).fetchall()
    return {"recent_updates": [dict(r) for r in rows], "note": "Cost history not yet tracked  -  showing 20 most recently updated SKUs."}


class AIBody(BaseModel):
    period_label: Optional[str] = None
    summary:      Dict[str, float] = {}
    extra:        Optional[str] = None


@app.post("/api/ai/commentary")
def ai_commentary(body: AIBody):
    """
    Generate a short narrative commentary about the period using Anthropic Claude
    (requires ANTHROPIC_API_KEY env var). Falls back to a deterministic prose if no key.
    """
    import os, json
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    s = body.summary or {}

    # Always-available deterministic fallback
    def deterministic():
        rev = s.get("revenue") or 0
        tx  = s.get("tx_count") or 0
        cust = s.get("customer_count") or 0
        avg = s.get("avg_ticket") or 0
        delta = s.get("revenue_delta_pct")
        delta_str = (f"{'+' if delta and delta>=0 else ''}{round(delta,1)}%" if delta is not None else "n/a")
        return (
            f"ในช่วง {body.period_label or 'ที่เลือก'}: รายได้รวม {rev:,.0f} บาท "
            f"จาก {tx:,} บิล ({cust:,} ลูกค้า) ยอดบิลเฉลี่ย {avg:,.0f} บาท "
            f"รายได้เปลี่ยนแปลง {delta_str} เทียบช่วงก่อนหน้า "
            "ยอดบิลเฉลี่ยที่เติบโตพร้อมจำนวนลูกค้าคงที่เป็นสัญญาณดี — "
            "พิจารณายกระดับช่วงเวลาหรือสาขาที่อ่อนที่สุดเพื่อต่อยอดการเติบโต"
        )

    if not api_key:
        return {"text": deterministic(), "source": "deterministic"}

    try:
        import urllib.request
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            data=json.dumps({
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 350,
                "messages": [{
                    "role": "user",
                    "content": (
                        f"You are a restaurant analytics advisor for a Thai restaurant chain. "
                        f"Write 4-5 sentences in THAI of clear, plain commentary about this period "
                        f"for a busy GM. Period: {body.period_label}. "
                        f"Numbers: {json.dumps(s)}. "
                        f"Cover: สิ่งที่เกิดขึ้น, สาเหตุน่าจะมาจากอะไร, action ที่ควรทำ 1 อย่าง. "
                        f"Be specific with numbers in Thai. No fluff. Currency unit is บาท (no symbol)."
                        + (f" Extra context: {body.extra}" if body.extra else "")
                    )
                }],
            }).encode("utf-8"),
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
            text = data.get("content", [{}])[0].get("text", "")
            return {"text": text or deterministic(), "source": "claude"}
    except Exception as e:
        return {"text": deterministic(), "source": f"deterministic (fallback: {e})"}


# ===========================================================================
# SETTINGS  -  new tables + endpoints
# ===========================================================================

# ---------------------------------------------------------------------------
# Ensure new tables exist (runs once on import, safe if DB already has them)
# ---------------------------------------------------------------------------
def _init_settings_tables():
    if not DB_PATH.exists():
        return
    with get_db() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS branch_targets (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                branch_id       INTEGER NOT NULL REFERENCES branches(branch_id),
                period_month    TEXT    NOT NULL,
                revenue_target  REAL    DEFAULT 0,
                customer_target INTEGER DEFAULT 0,
                UNIQUE(branch_id, period_month)
            );
            CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );
        """)
        # branch_type migration (idempotent)
        cols = [r[1] for r in con.execute("PRAGMA table_info(branches)").fetchall()]
        if "branch_type" not in cols:
            con.execute("ALTER TABLE branches ADD COLUMN branch_type TEXT DEFAULT 'master'")
        if "active" not in cols:
            con.execute("ALTER TABLE branches ADD COLUMN active INTEGER DEFAULT 1")
        # skus.active migration (idempotent)
        sku_cols = [r[1] for r in con.execute("PRAGMA table_info(skus)").fetchall()]
        if "active" not in sku_cols:
            con.execute("ALTER TABLE skus ADD COLUMN active INTEGER DEFAULT 1")
        for k, v in [
            ("default_branch", ""),
            ("default_month",  "latest"),
            ("fiscal_year_start", "1"),
        ]:
            con.execute(
                "INSERT OR IGNORE INTO app_settings(key,value) VALUES(?,?)", (k, v)
            )
        con.commit()

_init_settings_tables()


# ---------------------------------------------------------------------------
# Pydantic models for settings endpoints
# ---------------------------------------------------------------------------
class TargetEntry(BaseModel):
    branch: str
    period_month: str
    revenue_target: float = 0
    customer_target: int = 0

class BranchUpdate(BaseModel):
    name: str

class ActiveUpdate(BaseModel):
    active: bool


# ---------------------------------------------------------------------------
# /api/settings   -  GET: all key-value prefs; PUT: bulk upsert
# ---------------------------------------------------------------------------
@app.get("/api/settings")
def get_settings():
    with get_db() as con:
        rows = con.execute("SELECT key, value FROM app_settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


@app.put("/api/settings")
def put_settings(body: Dict[str, str]):
    with get_db() as con:
        cur = con.cursor()
        for k, v in body.items():
            cur.execute(
                "INSERT OR REPLACE INTO app_settings(key,value) VALUES(?,?)", (k, str(v))
            )
        con.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# /api/branches/{id}   -  PUT: rename a branch
# ---------------------------------------------------------------------------
@app.put("/api/branches/{branch_id}")
def update_branch(branch_id: int, body: BranchUpdate):
    with get_db() as con:
        clash = con.execute(
            "SELECT branch_id FROM branches WHERE name=? AND branch_id!=?",
            (body.name, branch_id),
        ).fetchone()
        if clash:
            raise HTTPException(400, "Branch name already in use")
        con.execute(
            "UPDATE branches SET name=? WHERE branch_id=?", (body.name, branch_id)
        )
        con.commit()
    return {"ok": True}


@app.put("/api/branches/{branch_id}/active")
def update_branch_active(branch_id: int, body: ActiveUpdate):
    with get_db() as con:
        con.execute(
            "UPDATE branches SET active=? WHERE branch_id=?",
            (1 if body.active else 0, branch_id),
        )
        con.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# /api/skus   -  list all SKUs; PUT active status
# ---------------------------------------------------------------------------
@app.get("/api/skus")
def list_skus(include_inactive: bool = Query(False)):
    sql = """
        SELECT sk.sku_id, sk.sku_code, sk.description,
               COALESCE(c.name,'') AS category,
               COALESCE(sk.active,1) AS active
        FROM skus sk
        LEFT JOIN categories c ON c.category_id = sk.category_id
        WHERE 1=1
    """
    params: list = []
    if not include_inactive:
        sql += " AND COALESCE(sk.active,1) = 1"
    sql += " ORDER BY sk.sku_code"
    with get_db() as con:
        rows = con.execute(sql, params).fetchall()
    return [
        {
            "sku_code": r["sku_code"],
            "description": r["description"],
            "category": r["category"],
            "active": bool(r["active"]),
        }
        for r in rows
    ]


@app.put("/api/skus/{sku_code}/active")
def update_sku_active(sku_code: str, body: ActiveUpdate):
    with get_db() as con:
        updated = con.execute(
            "UPDATE skus SET active=? WHERE sku_code=?",
            (1 if body.active else 0, sku_code),
        ).rowcount
        con.commit()
    if updated == 0:
        raise HTTPException(404, "SKU not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# /api/targets   -  GET: targets for a month (all branches); PUT: upsert
# ---------------------------------------------------------------------------
@app.get("/api/targets")
def get_targets(month: Optional[str] = Query(None)):
    """Returns all branches with their targets for the given month (0 if unset)."""
    params: dict = {"month": month or ""}
    month_join = "AND bt.period_month = :month" if month else ""

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT b.branch_id,
                   b.name AS branch,
                   COALESCE(bt.period_month, :month) AS period_month,
                   COALESCE(bt.revenue_target,  0)   AS revenue_target,
                   COALESCE(bt.customer_target, 0)   AS customer_target
            FROM branches b
            LEFT JOIN branch_targets bt
                   ON bt.branch_id = b.branch_id {month_join}
            WHERE b.name != 'ALL'
            ORDER BY b.name
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


@app.put("/api/targets")
def put_targets(entries: List[TargetEntry]):
    with get_db() as con:
        cur = con.cursor()
        saved = 0
        for e in entries:
            row = cur.execute(
                "SELECT branch_id FROM branches WHERE name=?", (e.branch,)
            ).fetchone()
            if row is None:
                continue
            cur.execute(
                """INSERT OR REPLACE INTO branch_targets
                       (branch_id, period_month, revenue_target, customer_target)
                   VALUES(?,?,?,?)""",
                (row[0], e.period_month, e.revenue_target, e.customer_target),
            )
            saved += 1
        con.commit()
    return {"ok": True, "saved": saved}


# ---------------------------------------------------------------------------
# /api/cost/detail   -  searchable SKU cost list with revenue stats
# ---------------------------------------------------------------------------
@app.get("/api/cost/detail")
@ttl_cache
def get_cost_detail(
    q:        Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    priced:   Optional[str] = Query(None, description="'yes'|'no'  -  has unit_cost set"),
    min_rev:  Optional[float] = Query(None, description="min total_revenue"),
    sort:     Optional[str] = Query(None, description="code|revenue|qty|cost"),
    business_type: Optional[str] = Query(None, description="filter by skus.business_type"),
    limit:    int           = Query(80, ge=1, le=500),
    offset:   int           = Query(0,  ge=0),
):
    filters: List[str] = []
    params: dict = {"limit": limit, "offset": offset}
    if q:
        filters.append(
            "(LOWER(sk.sku_code) LIKE :q OR LOWER(COALESCE(sk.description,'')) LIKE :q)"
        )
        params["q"] = f"%{q.lower()}%"
    if category:
        filters.append("c.name = :category")
        params["category"] = category
    if priced == "yes":
        filters.append("sc.unit_cost IS NOT NULL")
    elif priced == "no":
        filters.append("sc.unit_cost IS NULL")
    if business_type:
        filters.append("COALESCE(sk.business_type,'other') = :business_type")
        params["business_type"] = business_type
    sort_clause = "ORDER BY c.name, sk.sku_code"
    if sort == "revenue":
        sort_clause = "ORDER BY total_revenue DESC NULLS LAST"
    elif sort == "qty":
        sort_clause = "ORDER BY total_qty DESC NULLS LAST"
    elif sort == "cost":
        sort_clause = "ORDER BY sc.unit_cost DESC NULLS LAST"
    elif sort == "code":
        sort_clause = "ORDER BY sk.sku_code"
    having_clause = ""
    if min_rev is not None:
        having_clause = "HAVING total_revenue >= :min_rev"
        params["min_rev"] = min_rev

    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    cnt_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT sk.sku_id, sk.sku_code,
                   COALESCE(sk.description,'') AS description,
                   c.name AS category,
                   COALESCE(sk.business_type,'other') AS business_type,
                   sc.unit_cost, sc.updated_at,
                   ROUND(SUM(ssm.subtotal_ex_vat), 0) AS total_revenue,
                   ROUND(SUM(ssm.qty), 0)            AS total_qty
            FROM skus sk
            JOIN  categories c   ON c.category_id = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id = sk.sku_id
            LEFT JOIN sales_sku_monthly ssm ON ssm.sku_id = sk.sku_id
            {where}
            GROUP BY sk.sku_id
            {having_clause}
            {sort_clause}
            LIMIT :limit OFFSET :offset
            """,
            params,
        ).fetchall()

        total = con.execute(
            f"""
            SELECT COUNT(DISTINCT sk.sku_id)
            FROM skus sk
            JOIN categories c ON c.category_id = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id = sk.sku_id
            {where}
            """,
            cnt_params,
        ).fetchone()[0]

    return {
        "total": total,
        "items": [dict(r) for r in rows],
    }


# ---------------------------------------------------------------------------
# /api/cost/template  - full SKU list for CSV template download
# ---------------------------------------------------------------------------
@app.get("/api/cost/template")
def cost_template():
    """All active SKUs with sales context + current unit_cost for CSV template download.
    Sorted by total revenue desc so highest-volume items appear first."""
    with get_db() as con:
        rows = con.execute(
            """
            SELECT sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   c.name AS category,
                   COALESCE(CAST(sc.unit_cost AS TEXT), '') AS unit_cost,
                   ROUND(COALESCE(SUM(ssm.subtotal_ex_vat), 0), 0) AS total_revenue,
                   ROUND(COALESCE(SUM(ssm.qty), 0), 0)            AS total_qty,
                   MAX(ssm.period_month)                           AS last_sold
            FROM skus sk
            JOIN  categories c    ON c.category_id = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id = sk.sku_id
            LEFT JOIN sales_sku_monthly ssm ON ssm.sku_id = sk.sku_id
            WHERE COALESCE(sk.active, 1) = 1
            GROUP BY sk.sku_id
            ORDER BY total_revenue DESC, sk.sku_code
            """
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# /api/cost/analysis   -  full per-SKU menu engineering analysis
# ---------------------------------------------------------------------------
@app.get("/api/cost/analysis")
@ttl_cache
def cost_analysis(
    branch:     Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    include_buffet_drink: bool = Query(False),
):
    """
    Per-SKU revenue + cost breakdown with menu engineering quadrant classification.
    Returns items with sales > 0 only. Excludes buffet/drink/dessert/payment by default
    so the analysis focuses on à la carte food items.
    Quadrant = star | plowhorse | puzzle | dog | uncosted
    """
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="ssm.period_month")
    EXCLUDED_CATS = (
        'BUFFET', 'Oshinei SOFT DRINK', 'Oshinei ALCOHOL',
        'Oshinei DESSERT', 'DESSERT', 'Payment', 'Other',
        'Oshinei DELIVERY', 'Special DELIVERY',
    )
    if not include_buffet_drink:
        ph = ",".join(f":_excl_{i}" for i in range(len(EXCLUDED_CATS)))
        # Keep ice-cream items even if they live in a DESSERT category
        filters.append(
            f"(c.name NOT IN ({ph}) "
            f" OR UPPER(COALESCE(sk.description,'')) LIKE '%ICE CREAM%' "
            f" OR UPPER(COALESCE(sk.description,'')) LIKE '%ICECREAM%' "
            f" OR UPPER(COALESCE(sk.description,'')) LIKE '%GELATO%' "
            f" OR UPPER(COALESCE(sk.description,'')) LIKE '%SOFT SERVE%' "
            f" OR COALESCE(sk.description,'') LIKE '%ไอติม%' "
            f" OR COALESCE(sk.description,'') LIKE '%ไอศกรีม%')"
        )
        for i, c in enumerate(EXCLUDED_CATS):
            params[f"_excl_{i}"] = c
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT
                sk.sku_id,
                sk.sku_code,
                COALESCE(sk.description, sk.sku_code) AS description,
                c.name AS category,
                ROUND(SUM(ssm.qty), 0)                  AS qty,
                ROUND(SUM(ssm.subtotal_ex_vat), 2)       AS revenue,
                ROUND(SUM(ssm.subtotal_ex_vat), 2)       AS revenue_ex_vat,
                ROUND(SUM(ssm.total_inc_vat), 2)         AS pos_total_inc_vat_diagnostic,
                sc.unit_cost,
                -- COGS via canonical helper: monthly-aware effective cost +
                -- Easy buffet-tier exclusion (prevents tier x B0xxx double-count).
                -- gross_profit / margin_pct are derived in Python from these.
                ROUND(SUM({_cogs_case()}), 2)                               AS cogs,
                MAX(CASE WHEN {_effective_cost_subquery()} IS NOT NULL
                         THEN 1 ELSE 0 END)                                  AS has_cost
            FROM sales_sku_monthly ssm
            JOIN branches b   ON b.branch_id  = ssm.branch_id
            JOIN skus sk      ON sk.sku_id    = ssm.sku_id
            JOIN categories c ON c.category_id = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY sk.sku_id
            HAVING SUM(ssm.qty) > 0
            ORDER BY SUM(ssm.subtotal_ex_vat) DESC
            """,
            params,
        ).fetchall()

    items = [dict(r) for r in rows]

    # Effective-cost-aware costed flag + derived GP/margin (Phase 8 monthly cost).
    # "Costed" means the COALESCE chain (monthly exact -> monthly prior -> default)
    # resolved a unit cost for at least one row — broader than sc.unit_cost alone.
    for r in items:
        r["has_cost"] = bool(r.pop("has_cost", 0))
        if r["has_cost"]:
            rev_ex = r["revenue_ex_vat"] or 0
            r["gross_profit"] = round(rev_ex - (r["cogs"] or 0), 2)
            r["margin_pct"] = round(r["gross_profit"] / rev_ex * 100, 1) if rev_ex else None
        else:
            r["cogs"] = None
            r["gross_profit"] = None
            r["margin_pct"] = None

    # â"€â"€ Derived fields â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    total_revenue     = sum(r["revenue"]     or 0 for r in items)
    total_qty         = sum(r["qty"]         or 0 for r in items)
    total_rev_ex      = sum(r["revenue_ex_vat"] or 0 for r in items)
    costed_items      = [r for r in items if r["has_cost"]]
    total_cogs        = sum(r["cogs"]        or 0 for r in costed_items)
    total_gp          = sum(r["gross_profit"] or 0 for r in costed_items)
    costed_rev_ex     = sum(r["revenue_ex_vat"] or 0 for r in costed_items)
    overall_margin    = (
        round(total_gp / costed_rev_ex * 100, 1) if costed_rev_ex > 0 else None
    )

    for r in items:
        r["revenue_mix_pct"] = round(r["revenue"] / total_revenue * 100, 2) if total_revenue else 0
        r["qty_mix_pct"]     = round(r["qty"]     / total_qty     * 100, 2) if total_qty     else 0

    # â"€â"€ Menu engineering quadrant (costed items only) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    # margin_pct can be None even when unit_cost is set (rows with subtotal_ex_vat=0).
    # Filter them out for the avg/quadrant math; otherwise sum() raises TypeError.
    quad_items = [r for r in costed_items if r["margin_pct"] is not None]
    if quad_items:
        avg_qty    = sum(r["qty"]        for r in quad_items) / len(quad_items)
        avg_margin = sum(r["margin_pct"] for r in quad_items) / len(quad_items)
        for r in items:
            if r["unit_cost"] is None or r["margin_pct"] is None:
                r["quadrant"] = "uncosted"
            else:
                hi_pop = r["qty"]        >= avg_qty
                hi_mar = r["margin_pct"] >= avg_margin
                r["quadrant"] = (
                    "star"      if hi_pop and hi_mar  else
                    "plowhorse" if hi_pop             else
                    "puzzle"    if hi_mar             else
                    "dog"
                )
        avg_qty_all    = avg_qty
        avg_margin_all = avg_margin
    else:
        for r in items:
            r["quadrant"] = "uncosted"
        avg_qty_all, avg_margin_all = None, None

    # â"€â"€ Pareto (revenue) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    cumrev = 0
    for r in items:
        cumrev += r["revenue"] or 0
        r["cum_revenue_pct"] = round(cumrev / total_revenue * 100, 1) if total_revenue else 0

    return {
        "items": items,
        "summary": {
            "total_revenue":      round(total_revenue, 2),
            "total_cogs":         round(total_cogs, 2),
            "total_gross_profit": round(total_gp, 2),
            "overall_margin_pct": overall_margin,
            "total_qty":          total_qty,
            "total_skus":         len(items),
            "costed_skus":        len(costed_items),
            "uncosted_skus":      len(items) - len(costed_items),
            "avg_qty":            round(avg_qty_all, 1) if avg_qty_all else None,
            "avg_margin":         round(avg_margin_all, 1) if avg_margin_all else None,
        },
    }


# ============================================================================
# PROFIT ANALYSIS  -  joins sales_sku_monthly x sku_cost to compute COGS + GP
# ============================================================================

def _profit_filters(branch, group, month_from, month_to, month):
    """Common WHERE-builder for profit endpoints. Returns (where_sql, params)."""
    filters = ["b.name != 'ALL'", "COALESCE(b.active,1) = 1"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month, col="ssm.period_month")
    return "WHERE " + " AND ".join(filters), params


def _tx_filters(branch, group, month_from, month_to, month):
    """Common WHERE-builder for transaction-based queries."""
    filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    return "WHERE " + " AND ".join(filters), params


# ─── Source-aware COGS rule (Phase 0 GP fix) ─────────────────────────────────
# Easy source logs both tier SKUs (BUFFET350/500/590/799/CHILD with revenue =
# customer payment) AND item SKUs (B0xxx with qty>0, revenue=0 — the real
# consumption). Using tier unit_cost AND item unit_cost double-counts.
# Rule: for Easy rows whose sku_code is a buffet tier, contribute zero cost
# (and zero costed-revenue); for everything else, use unit_cost normally.
# CM / CM POS only have tier-level rows (no items) — tier cost is the only
# cost data so it must be included.
# 'BUFFET' (plain, sku_id=1) is the deprecated legacy code from CM era. Some Easy
# bills still emit it as a fallback. Treat same as tiered codes: count revenue,
# skip unit_cost (avoid double-counting 825M of phantom COGS).
_EASY_TIER_CODES = ('BUFFET','BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD')
_EASY_TIER_CODES_SQL = "('BUFFET','BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD')"
# CM POS only exposes tier-level rows under these codes (no item breakdown).
_CM_TIER_CODES = ('B01','B02','B03','B07','B11')
_ALL_TIER_CODES = _EASY_TIER_CODES + _CM_TIER_CODES


def _effective_cost_subquery(ssm: str = "ssm", sc: str = "sc") -> str:
    """SQL fragment returning the effective unit_cost for a sales_sku_monthly row.

    Resolution order (Phase 8 monthly cost support):
      1. sku_cost_monthly exact-month match for the row's period_month
      2. sku_cost_monthly latest prior month  (carry-forward)
      3. sku_cost.unit_cost — lifetime default
      4. NULL (caller decides whether to treat as 0 or "uncosted")

    Requires the outer query to expose ``{ssm}.sku_id``, ``{ssm}.period_month``
    and to LEFT JOIN ``sku_cost {sc}`` on sku_id (for the default fallback).
    """
    return (
        "COALESCE("
        f"(SELECT unit_cost FROM sku_cost_monthly "
        f" WHERE sku_id = {ssm}.sku_id AND period_month = {ssm}.period_month), "
        f"(SELECT unit_cost FROM sku_cost_monthly "
        f" WHERE sku_id = {ssm}.sku_id AND period_month < {ssm}.period_month "
        f" ORDER BY period_month DESC LIMIT 1), "
        f"{sc}.unit_cost"
        ")"
    )


def _cogs_case(ssm: str = "ssm", sk: str = "sk", sc: str = "sc") -> str:
    """SQL CASE expression for COGS. Requires JOIN skus sk and LEFT JOIN sku_cost sc.

    Uses the monthly-aware effective cost (Phase 8) — falls back to sku_cost.unit_cost
    when no monthly override exists. Easy buffet tier rows still contribute zero cost
    (consumption is logged via item-level B0xxx rows).
    """
    eff = _effective_cost_subquery(ssm, sc)
    return (
        f"CASE WHEN {ssm}.source = 'Easy' AND {sk}.sku_code IN {_EASY_TIER_CODES_SQL} THEN 0 "
        f"     WHEN {eff} IS NOT NULL THEN {ssm}.qty * {eff} "
        f"     ELSE 0 END"
    )


def _costed_rev_case(ssm: str = "ssm", sk: str = "sk", sc: str = "sc") -> str:
    """SQL CASE expression for the revenue portion that aligns with COGS.

    "Has effective cost" now considers the monthly fallback chain (Phase 8).
    """
    eff = _effective_cost_subquery(ssm, sc)
    return (
        f"CASE WHEN {ssm}.source = 'Easy' AND {sk}.sku_code IN {_EASY_TIER_CODES_SQL} "
        f"          THEN {ssm}.subtotal_ex_vat "
        f"     WHEN {eff} IS NOT NULL THEN {ssm}.subtotal_ex_vat "
        f"     ELSE 0 END"
    )


# ─── (A)/(B) variant filter (Phase 9, revised) ───────────────────────────────
# Easy POS labels description prefix unreliably — "(A)" items often turn out to
# be buffet-consumed (qty>0, revenue=0). So we filter by REVENUE BEHAVIOUR
# rather than description prefix:
#   'A' = à la carte: customer paid separately → subtotal_ex_vat > 0
#         AND not a buffet tier SKU.
#   'B' = buffet:   tier SKUs (customer paid via tier) OR items consumed
#         during buffet (qty > 0 AND subtotal_ex_vat <= 0).
# Requires JOIN skus sk and the row to expose ssm.subtotal_ex_vat / ssm.qty.
def _variant_filter(variant: Optional[str], sk: str = "sk", ssm: str = "ssm") -> str:
    """Return a SQL fragment (no leading AND) restricting rows to (A) or (B)
    by REVENUE behaviour, not description prefix. Empty string when no filter."""
    if not variant:
        return ""
    v = variant.strip().upper()
    if v in ("", "ALL", "A+B", "AB"):
        return ""
    if v == "A":
        return (
            f"({ssm}.subtotal_ex_vat > 0 "
            f" AND {sk}.sku_code NOT IN {_EASY_TIER_CODES_SQL})"
        )
    if v == "B":
        return (
            f"({sk}.sku_code IN {_EASY_TIER_CODES_SQL} "
            f" OR ({ssm}.qty > 0 AND COALESCE({ssm}.subtotal_ex_vat,0) <= 0))"
        )
    return ""


@app.get("/api/buffet/performance")
@ttl_cache
def buffet_performance(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    food_cost_target_lo: float = Query(55.0),
    food_cost_target_hi: float = Query(70.0),
):
    """Comprehensive Buffet Performance metrics for the core all-you-can-eat business.

    Returns:
    - kpis: pax, bills, avg_pax_per_bill, premium_tier_pct, food_cost_pct, over_consumption_pct, beverage_attach_pct
    - tier_trend: per-month tier mix (BUFFET350/500/590/799 + CHILD)
    - food_cost_trend: monthly food_cost % vs target band
    - seat_util: per-branch daily pax vs network peak (proxy)
    - turnover:   per-branch bills/table/day (turnover proxy)
    - waste:      per-month over-consumption rate from OT0012/0013/0016
    - beverage_attach: per-month beverage revenue % of buffet bills
    - daypart_tier: lunch vs dinner tier mix
    - cohort:     per-branch performance bucketed by months-since-opening
    - pricing_rec: current weighted tier price vs revenue-optimal
    """
    filters_sku = ["b.name != 'ALL'"]
    params_sku: dict = {}
    if branch:
        filters_sku.append("b.name = :branch")
        params_sku["branch"] = branch
    _month_range_filter(filters_sku, params_sku, month_from, month_to, None, col="sm.period_month")
    where_sku = "WHERE " + " AND ".join(filters_sku)

    filters_tx = ["st.source='Easy'", "b.name != 'ALL'"]
    params_tx: dict = {}
    if branch:
        filters_tx.append("b.name = :branch")
        params_tx["branch"] = branch
    _month_range_filter(filters_tx, params_tx, month_from, month_to, None, col="strftime('%Y-%m', st.sold_at)")
    where_tx = "WHERE " + " AND ".join(filters_tx)

    BUFFET_TIERS = ('BUFFET350', 'BUFFET500', 'BUFFET590', 'BUFFET799', 'BUFFETCHILD')
    WASTE_CODES  = ('OT0012', 'OT0013', 'OT0014', 'OT0015', 'OT0016')

    # Build named-param dicts for IN clauses so we can mix safely with named month params
    tier_named  = {f"bt{i}": v for i, v in enumerate(BUFFET_TIERS)}
    tier_ph     = ", ".join(f":bt{i}" for i in range(len(BUFFET_TIERS)))
    waste_named = {f"wc{i}": v for i, v in enumerate(WASTE_CODES)}
    waste_ph    = ", ".join(f":wc{i}" for i in range(len(WASTE_CODES)))

    with get_db() as con:
        # ── 1. Tier mix trend (monthly pax + revenue per tier) ────────────────
        tier_rows = con.execute(f"""
            SELECT sm.period_month AS m, sk.sku_code AS tier,
                   SUM(sm.qty)             AS pax,
                   SUM(sm.total_inc_vat)   AS rev_inc,
                   SUM(sm.subtotal_ex_vat) AS rev_ex
            FROM sales_sku_monthly sm
            JOIN skus sk      ON sk.sku_id    = sm.sku_id
            JOIN branches b   ON b.branch_id  = sm.branch_id
            {where_sku}
              AND sk.sku_code IN ({tier_ph})
            GROUP BY sm.period_month, sk.sku_code
            ORDER BY sm.period_month, sk.sku_code
        """, {**params_sku, **tier_named}).fetchall()

        # ── 2. Tier unit costs (for cost%) ────────────────────────────────────
        tier_cost = {}
        for r in con.execute(f"""
            SELECT sk.sku_code, sc.unit_cost
            FROM sku_cost sc JOIN skus sk ON sk.sku_id = sc.sku_id
            WHERE sk.sku_code IN ({tier_ph})
        """, tier_named).fetchall():
            tier_cost[r["sku_code"]] = r["unit_cost"]

        # Monthly tier-cost overrides (Phase 8): {(period_month, sku_code): unit_cost}
        tier_cost_monthly: dict = {}
        for r in con.execute(f"""
            SELECT scm.period_month AS m, sk.sku_code AS code, scm.unit_cost AS unit_cost
            FROM sku_cost_monthly scm JOIN skus sk ON sk.sku_id = scm.sku_id
            WHERE sk.sku_code IN ({tier_ph})
        """, tier_named).fetchall():
            tier_cost_monthly[(r["m"], r["code"])] = r["unit_cost"]

        # ── 3. Per-branch daily pax (for seat utilisation proxy) ──────────────
        daily_pax_rows = con.execute(f"""
            SELECT b.name AS branch,
                   DATE(st.sold_at) AS d,
                   SUM(st.customer_count) AS pax,
                   COUNT(*) AS bills,
                   COUNT(DISTINCT st.table_no) AS tables
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
              AND st.customer_count > 0
            GROUP BY b.name, DATE(st.sold_at)
        """, params_tx).fetchall()

        # ── 4. Waste / over-consumption (OT codes — kg fines for buffet leftovers) ────
        waste_rows = con.execute(f"""
            SELECT sm.period_month AS m,
                   SUM(sm.qty) AS qty,
                   SUM(sm.subtotal_ex_vat) AS rev
            FROM sales_sku_monthly sm
            JOIN skus sk ON sk.sku_id = sm.sku_id
            JOIN branches b ON b.branch_id = sm.branch_id
            {where_sku}
              AND sk.sku_code IN ({waste_ph})
            GROUP BY sm.period_month
        """, {**params_sku, **waste_named}).fetchall()

        # Buffet pax per month (for waste rate denominator)
        buffet_pax_rows = con.execute(f"""
            SELECT sm.period_month AS m, SUM(sm.qty) AS pax
            FROM sales_sku_monthly sm
            JOIN skus sk ON sk.sku_id = sm.sku_id
            JOIN branches b ON b.branch_id = sm.branch_id
            {where_sku}
              AND sk.sku_code IN ({tier_ph})
            GROUP BY sm.period_month
        """, {**params_sku, **tier_named}).fetchall()
        pax_by_month = {r["m"]: r["pax"] or 0 for r in buffet_pax_rows}

        # ── 5. Beverage attach rate (from drink_subtotal / total) ─────────────
        bev_rows = con.execute(f"""
            SELECT strftime('%Y-%m', st.sold_at) AS m,
                   SUM(st.drink_subtotal) AS drink_rev,
                   SUM(st.food_subtotal)  AS food_rev,
                   SUM(st.total_inc_vat) / 1.07 AS total_rev,  -- ex-VAT, consistent with drink/food subtotals
                   COUNT(*)               AS bills,
                   SUM(CASE WHEN st.drink_subtotal > 0 THEN 1 ELSE 0 END) AS bills_with_drink
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
            GROUP BY strftime('%Y-%m', st.sold_at)
            ORDER BY m
        """, params_tx).fetchall()

        # ── 6. Daypart tier mix (lunch vs dinner) ─────────────────────────────
        daypart_rows = con.execute(f"""
            SELECT CASE
                     WHEN CAST(strftime('%H', st.sold_at) AS INT) BETWEEN 11 AND 14 THEN 'lunch'
                     WHEN CAST(strftime('%H', st.sold_at) AS INT) BETWEEN 17 AND 22 THEN 'dinner'
                     ELSE 'other' END AS daypart,
                   CASE
                     WHEN (st.food_subtotal/st.customer_count) < 450 THEN 'BUFFET350'
                     WHEN (st.food_subtotal/st.customer_count) < 600 THEN 'BUFFET500'
                     WHEN (st.food_subtotal/st.customer_count) < 720 THEN 'BUFFET590'
                     ELSE 'BUFFET799' END AS tier,
                   SUM(st.customer_count) AS pax
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
              AND st.customer_count > 0 AND st.food_subtotal > 0
              AND (st.food_subtotal / st.customer_count) BETWEEN 380 AND 1000
            GROUP BY daypart, tier
        """, params_tx).fetchall()

        # ── 7. Branch opening month (cohort: months-since-open) ───────────────
        branch_open = con.execute("""
            SELECT b.name AS branch, MIN(sm.period_month) AS first_month
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            WHERE b.name != 'ALL'
            GROUP BY b.name
        """).fetchall()
        branch_first = {r["branch"]: r["first_month"] for r in branch_open}

        # Per-branch monthly buffet revenue (for cohort curve)
        bcohort_rows = con.execute(f"""
            SELECT b.name AS branch, sm.period_month AS m,
                   SUM(sm.subtotal_ex_vat) AS rev,
                   SUM(sm.qty) AS pax
            FROM sales_sku_monthly sm
            JOIN skus sk ON sk.sku_id = sm.sku_id
            JOIN branches b ON b.branch_id = sm.branch_id
            WHERE b.name != 'ALL'
              AND sk.sku_code IN ({tier_ph})
            GROUP BY b.name, sm.period_month
            ORDER BY b.name, sm.period_month
        """, tier_named).fetchall()

    # ── Aggregate tier_trend (pax + cost%) ──────────────────────────────────
    from collections import defaultdict
    tier_trend = defaultdict(lambda: defaultdict(float))
    tier_rev   = defaultdict(lambda: defaultdict(float))
    for r in tier_rows:
        tier_trend[r["m"]][r["tier"]] = r["pax"] or 0
        tier_rev  [r["m"]][r["tier"]] = r["rev_ex"] or 0
    months = sorted(tier_trend.keys())

    # Tier list for chart
    tier_list = ['BUFFET350', 'BUFFET500', 'BUFFET590', 'BUFFET799', 'BUFFETCHILD']
    tier_trend_out = [{
        "period": mo,
        **{t: tier_trend[mo].get(t, 0) for t in tier_list},
        "total_pax": sum(tier_trend[mo].values()),
    } for mo in months]

    # ── Food cost trend (Buffet only — qty × tier_cost) ─────────────────────
    def _eff_tier_cost(period, code):
        # Phase 8 resolution: monthly exact -> latest monthly prior -> lifetime default.
        if (period, code) in tier_cost_monthly:
            return tier_cost_monthly[(period, code)]
        priors = [m for (m, c) in tier_cost_monthly if c == code and m < period]
        if priors:
            return tier_cost_monthly[(max(priors), code)]
        return tier_cost.get(code)

    food_cost_trend = []
    for mo in months:
        rev_ex = sum(tier_rev[mo].values())
        cost = 0.0
        for t in tier_list:
            unit_cost = _eff_tier_cost(mo, t)
            if unit_cost is not None:
                cost += tier_trend[mo].get(t, 0) * unit_cost
        pct = (cost / rev_ex * 100) if rev_ex else None
        food_cost_trend.append({
            "period": mo, "revenue_ex": round(rev_ex, 0), "cost": round(cost, 0),
            "cost_pct": round(pct, 1) if pct is not None else None,
        })

    # ── Seat utilization proxy: per-branch avg pax/day vs network peak ──────
    branch_pax = defaultdict(list)
    branch_bills = defaultdict(list)
    branch_tables = defaultdict(list)
    for r in daily_pax_rows:
        branch_pax  [r["branch"]].append(r["pax"]   or 0)
        branch_bills[r["branch"]].append(r["bills"] or 0)
        branch_tables[r["branch"]].append(r["tables"] or 0)
    # Network peak = 90th percentile of daily pax across all branches
    all_daily_pax = sorted(p for vals in branch_pax.values() for p in vals)
    peak_pax = all_daily_pax[int(len(all_daily_pax) * 0.9)] if all_daily_pax else 1
    seat_util = []
    for br, vals in branch_pax.items():
        avg_pax = sum(vals) / len(vals) if vals else 0
        max_pax = max(vals) if vals else 0
        utilization = (avg_pax / peak_pax * 100) if peak_pax else 0
        seat_util.append({
            "branch": br,
            "avg_pax_per_day": round(avg_pax, 1),
            "max_pax_per_day": max_pax,
            "utilization_pct": round(utilization, 1),
        })
    seat_util.sort(key=lambda x: -x["utilization_pct"])

    # ── Turnover proxy: bills per table per day ─────────────────────────────
    turnover = []
    for br in branch_bills:
        bills_avg  = (sum(branch_bills[br])  / len(branch_bills[br]))  if branch_bills[br]  else 0
        tables_avg = (sum(branch_tables[br]) / len(branch_tables[br])) if branch_tables[br] else 0
        ratio = (bills_avg / tables_avg) if tables_avg > 0 else 0
        turnover.append({
            "branch":           br,
            "bills_per_day":    round(bills_avg, 1),
            "tables_per_day":   round(tables_avg, 1),
            "bills_per_table":  round(ratio, 2),
        })
    turnover.sort(key=lambda x: -x["bills_per_table"])

    # ── Waste / over-consumption rate ───────────────────────────────────────
    waste_trend = []
    for r in waste_rows:
        mo = r["m"]
        pax = pax_by_month.get(mo, 0) or 0
        rate = (r["qty"] / pax * 100) if pax > 0 else 0
        waste_trend.append({
            "period":   mo,
            "qty":      r["qty"] or 0,
            "revenue":  r["rev"] or 0,
            "rate_pct": round(rate, 2),  # # of fine charges per 100 pax
        })
    # Fill in missing months with 0
    waste_periods = {w["period"] for w in waste_trend}
    for mo in months:
        if mo not in waste_periods:
            waste_trend.append({"period": mo, "qty": 0, "revenue": 0, "rate_pct": 0})
    waste_trend.sort(key=lambda x: x["period"])

    # ── Beverage attach ─────────────────────────────────────────────────────
    bev_trend = []
    for r in bev_rows:
        total = r["total_rev"] or 0
        drink = r["drink_rev"] or 0
        attach_bills = (r["bills_with_drink"] or 0) / (r["bills"] or 1) * 100
        attach_rev   = (drink / total * 100) if total else 0
        bev_trend.append({
            "period":         r["m"],
            "drink_revenue":  round(drink, 0),
            "total_revenue":  round(total, 0),
            "attach_pct":     round(attach_rev, 1),
            "bills_attach_pct": round(attach_bills, 1),
        })

    # ── Daypart tier mix ────────────────────────────────────────────────────
    daypart_mix = defaultdict(lambda: defaultdict(int))
    for r in daypart_rows:
        daypart_mix[r["daypart"]][r["tier"]] += r["pax"] or 0
    daypart_out = []
    for dp in ('lunch', 'dinner', 'other'):
        tot = sum(daypart_mix[dp].values()) or 1
        daypart_out.append({
            "daypart": dp,
            "total_pax": sum(daypart_mix[dp].values()),
            **{t: round(daypart_mix[dp].get(t, 0) / tot * 100, 1) for t in tier_list},
        })

    # ── Branch cohort: months-since-open vs revenue/pax ─────────────────────
    from datetime import datetime as _dt
    def months_diff(a, b):
        try:
            da = _dt.strptime(a, "%Y-%m"); db = _dt.strptime(b, "%Y-%m")
            return (db.year - da.year) * 12 + (db.month - da.month)
        except Exception:
            return None

    cohort = defaultdict(list)  # months_since_open -> [revenues]
    for r in bcohort_rows:
        first = branch_first.get(r["branch"])
        if not first: continue
        m_diff = months_diff(first, r["m"])
        if m_diff is None or m_diff < 0 or m_diff > 24: continue
        cohort[m_diff].append({"revenue": r["rev"] or 0, "pax": r["pax"] or 0})
    cohort_curve = []
    for m_since in sorted(cohort.keys()):
        vals = cohort[m_since]
        cohort_curve.append({
            "months_since_open": m_since,
            "avg_revenue":  round(sum(v["revenue"] for v in vals) / len(vals), 0),
            "avg_pax":      round(sum(v["pax"] for v in vals) / len(vals), 0),
            "branch_count": len(vals),
        })

    # ── Pricing recommendation ──────────────────────────────────────────────
    TIER_PRICES = {'BUFFET350': 350, 'BUFFET500': 500, 'BUFFET590': 590, 'BUFFET799': 799, 'BUFFETCHILD': 295}
    # Overall actual tier mix (across all months)
    actual_pax = {t: 0 for t in tier_list}
    for mo_data in tier_trend.values():
        for t, p in mo_data.items():
            actual_pax[t] += p
    total_pax = sum(actual_pax.values()) or 1
    actual_mix_pct = {t: actual_pax[t] / total_pax * 100 for t in tier_list}
    # Revenue contribution per tier
    tier_revenue_actual = {}
    tier_margin_actual = {}
    for t in tier_list:
        rev = TIER_PRICES[t] * actual_pax[t]
        cost = (tier_cost.get(t) or TIER_PRICES[t] * 0.4) * actual_pax[t]
        tier_revenue_actual[t] = rev
        tier_margin_actual[t]  = rev - cost
    actual_total_rev    = sum(tier_revenue_actual.values())
    actual_total_margin = sum(tier_margin_actual.values())
    actual_margin_pct   = (actual_total_margin / actual_total_rev * 100) if actual_total_rev else 0

    # Optimal mix: shift 10% of 350/500 pax to 590, and 5% to 799 (conservative up-sell scenario)
    target_pax = dict(actual_pax)
    shift_350 = actual_pax.get('BUFFET350', 0) * 0.30  # 30% of 350 customers upgrade
    shift_500 = actual_pax.get('BUFFET500', 0) * 0.30
    target_pax['BUFFET350'] -= shift_350
    target_pax['BUFFET500'] -= shift_500
    target_pax['BUFFET590'] += shift_350 * 0.7 + shift_500 * 0.7
    target_pax['BUFFET799'] += shift_350 * 0.3 + shift_500 * 0.3
    target_revenue = sum(TIER_PRICES[t] * target_pax[t] for t in tier_list)
    target_cost    = sum((tier_cost.get(t) or TIER_PRICES[t] * 0.4) * target_pax[t] for t in tier_list)
    target_margin  = target_revenue - target_cost
    target_margin_pct = (target_margin / target_revenue * 100) if target_revenue else 0
    margin_gain = target_margin - actual_total_margin
    revenue_gain = target_revenue - actual_total_rev

    pricing_rec = {
        "current_mix": {t: round(actual_mix_pct[t], 1) for t in tier_list},
        "target_mix":  {t: round(target_pax[t] / total_pax * 100, 1) for t in tier_list},
        "current_revenue": round(actual_total_rev, 0),
        "current_margin":  round(actual_total_margin, 0),
        "current_margin_pct": round(actual_margin_pct, 2),
        "target_revenue":  round(target_revenue, 0),
        "target_margin":   round(target_margin, 0),
        "target_margin_pct": round(target_margin_pct, 2),
        "revenue_gain":    round(revenue_gain, 0),
        "margin_gain":     round(margin_gain, 0),
        "assumption":      "30% ของลูกค้า tier 350/500 ขยับขึ้นไป 590 (70%) และ 799 (30%)",
    }

    # ── KPI summary ─────────────────────────────────────────────────────────
    total_buffet_pax = sum(actual_pax.values())
    premium_pax = actual_pax.get('BUFFET590', 0) + actual_pax.get('BUFFET799', 0)
    premium_pct = (premium_pax / total_buffet_pax * 100) if total_buffet_pax else 0
    # Per-tier % split (350/500/590/799/CHILD individually)
    tier_pct_split = {
        t: round(actual_pax.get(t, 0) / total_buffet_pax * 100, 1) if total_buffet_pax else 0
        for t in ('BUFFET350', 'BUFFET500', 'BUFFET590', 'BUFFET799', 'BUFFETCHILD')
    }
    waste_total_qty = sum(w["qty"] for w in waste_trend)
    waste_rate_overall = (waste_total_qty / total_buffet_pax * 100) if total_buffet_pax else 0
    bev_total = sum(b["drink_revenue"] for b in bev_trend)
    total_billed = sum(b["total_revenue"] for b in bev_trend)
    bev_attach_overall = (bev_total / total_billed * 100) if total_billed else 0
    food_cost_overall = sum(f["cost"] for f in food_cost_trend)
    rev_ex_overall    = sum(f["revenue_ex"] for f in food_cost_trend)
    food_cost_pct_overall = (food_cost_overall / rev_ex_overall * 100) if rev_ex_overall else 0
    total_bills = sum(sum(b) for b in branch_bills.values())

    # ── Phase 12: Combined whole-restaurant numbers (buffet + à la carte,
    # all SKUs, applies source-aware COGS rule). This is what the user wants
    # to see in the KPI hero row — one overall picture, not split by variant.
    combined_filters = ["b.name != 'ALL'"]
    combined_params: dict = {}
    if branch:
        combined_filters.append("b.name = :branch")
        combined_params["branch"] = branch
    _month_range_filter(combined_filters, combined_params, month_from, month_to, None, col="ssm.period_month")
    combined_where = "WHERE " + " AND ".join(combined_filters)
    with get_db() as con:
        crow = con.execute(f"""
            SELECT
              COALESCE(SUM(ssm.subtotal_ex_vat), 0)        AS rev_ex,
              COALESCE(SUM(ssm.total_inc_vat),  0)        AS rev_inc,
              COALESCE(SUM({_cogs_case('ssm','sk','sc')}), 0)        AS cogs,
              COALESCE(SUM({_costed_rev_case('ssm','sk','sc')}), 0)  AS costed_rev_ex
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {combined_where}
        """, combined_params).fetchone()
    c_rev_ex      = float(crow["rev_ex"] or 0)
    c_rev_inc     = float(crow["rev_inc"] or 0)
    c_cogs        = float(crow["cogs"] or 0)
    c_costed_rev  = float(crow["costed_rev_ex"] or 0)
    c_gp          = c_costed_rev - c_cogs
    c_gp_pct      = (c_gp   / c_costed_rev * 100) if c_costed_rev > 0 else None
    c_food_pct    = (c_cogs / c_costed_rev * 100) if c_costed_rev > 0 else None

    kpis = {
        "total_pax":             int(total_buffet_pax),
        "total_bills":           int(total_bills),
        "avg_pax_per_bill":      round(total_buffet_pax / total_bills, 2) if total_bills else 0,
        "premium_tier_pct":      round(premium_pct, 1),
        "premium_tier_pct_target": round(
            (pricing_rec["target_mix"].get("BUFFET590", 0)
             + pricing_rec["target_mix"].get("BUFFET799", 0)),
            1,
        ),
        "food_cost_pct":         round(food_cost_pct_overall, 1),
        "food_cost_target_lo":   food_cost_target_lo,
        "food_cost_target_hi":   food_cost_target_hi,
        "over_consumption_pct":  round(waste_rate_overall, 2),
        "beverage_attach_pct":   round(bev_attach_overall, 1),
        "branch_count":          len(branch_pax),
        # ── Combined whole-restaurant (Phase 12) ──────────────────────────
        "combined_revenue_ex_vat":  round(c_rev_ex, 2),
        "combined_pos_total_inc_vat_diagnostic": round(c_rev_inc, 2),
        "combined_cogs":            round(c_cogs, 2),
        "combined_gross_profit":    round(c_gp, 2),
        "combined_gp_pct":          round(c_gp_pct, 1)   if c_gp_pct   is not None else None,
        "combined_food_cost_pct":   round(c_food_pct, 1) if c_food_pct is not None else None,
    }

    return {
        "kpis":            kpis,
        "tier_pct_split":  tier_pct_split,
        "tier_trend":      tier_trend_out,
        "food_cost_trend": food_cost_trend,
        "seat_util":       seat_util,
        "turnover":        turnover,
        "waste":           waste_trend,
        "beverage":        bev_trend,
        "daypart_tier":    daypart_out,
        "cohort":          cohort_curve,
        "pricing_rec":     pricing_rec,
    }


@app.get("/api/profit/buffet_per_bill")
@ttl_cache
def buffet_per_bill(
    branch:      Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    granularity: str           = Query("monthly", description="daily | weekly | monthly | quarterly"),
):
    """Per-bill profit deep-dive for the buffet business.

    Joins sales_transaction with the buffet SKU costs via inferred tier.
    For each bill: revenue ex-VAT, estimated buffet BoM cost, profit, margin %.
    Aggregated by the requested granularity.
    """
    if granularity == "daily":
        period_expr = "DATE(st.sold_at)"
    elif granularity == "weekly":
        period_expr = _iso_week_sql('st.sold_at')
    elif granularity == "quarterly":
        period_expr = ("substr(strftime('%Y-%m', st.sold_at), 1, 4) || '-Q' || "
                       "((CAST(substr(strftime('%Y-%m', st.sold_at), 6, 2) AS INTEGER)-1)/3+1)")
    else:
        period_expr = "strftime('%Y-%m', st.sold_at)"

    filters = ["st.source = 'Easy'", "b.name != 'ALL'", "st.customer_count > 0", "st.food_subtotal > 0"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, None,
                        col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)

    # Tier price → cost mapping (use BUFFETxxx SKU costs from sku_cost table)
    with get_db() as con:
        tier_costs = {}
        for r in con.execute("""
            SELECT sk.sku_code, sc.unit_cost
            FROM sku_cost sc JOIN skus sk ON sk.sku_id = sc.sku_id
            WHERE sk.sku_code IN ('BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD')
        """).fetchall():
            tier_costs[r["sku_code"]] = r["unit_cost"]

        # Pull all eligible buffet bills with per-head food spend → infer tier per bill
        rows = con.execute(f"""
            SELECT b.name AS branch,
                   {period_expr} AS period,
                   st.customer_count                              AS pax,
                   st.food_subtotal / st.customer_count           AS per_head,
                   st.total_inc_vat                               AS rev_inc,
                   st.total_inc_vat / 1.07                        AS rev_ex,
                   st.discount + st.sp_discount                   AS discount
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
              AND (st.food_subtotal / st.customer_count) BETWEEN 200 AND 1500
        """, params).fetchall()

    def tier_of(per_head):
        if per_head < 450:  return ("BUFFET350",  350)
        if per_head < 600:  return ("BUFFET500",  500)
        if per_head < 720:  return ("BUFFET590",  590)
        return ("BUFFET799", 799)

    from collections import defaultdict
    agg = defaultdict(lambda: {"bills": 0, "pax": 0, "revenue_ex": 0.0, "revenue_inc": 0.0,
                               "cost": 0.0, "discount": 0.0,
                               "tier_mix": defaultdict(int)})
    overall = {"bills": 0, "pax": 0, "revenue_ex": 0.0, "cost": 0.0,
               "tier_mix": defaultdict(int), "tier_cost_mix": defaultdict(float)}

    for r in rows:
        tier_code, tier_price = tier_of(r["per_head"])
        unit_cost = tier_costs.get(tier_code) or tier_price * 0.55  # fallback 45% margin assumption
        pax = r["pax"] or 0
        cost = unit_cost * pax
        a = agg[r["period"]]
        a["bills"]       += 1
        a["pax"]         += pax
        a["revenue_ex"]  += r["rev_ex"] or 0
        a["revenue_inc"] += r["rev_inc"] or 0
        a["cost"]        += cost
        a["discount"]    += r["discount"] or 0
        a["tier_mix"][tier_code] += pax
        overall["bills"]       += 1
        overall["pax"]         += pax
        overall["revenue_ex"]  += r["rev_ex"] or 0
        overall["cost"]        += cost
        overall["tier_mix"][tier_code] += pax
        overall["tier_cost_mix"][tier_code] += cost

    out_rows = []
    for period in sorted(agg.keys()):
        a = agg[period]
        profit = a["revenue_ex"] - a["cost"]
        margin = (profit / a["revenue_ex"] * 100) if a["revenue_ex"] else 0
        avg_per_bill_rev    = a["revenue_ex"] / a["bills"] if a["bills"] else 0
        avg_per_bill_cost   = a["cost"]       / a["bills"] if a["bills"] else 0
        avg_per_bill_profit = profit          / a["bills"] if a["bills"] else 0
        # Dominant tier this period
        dom = max(a["tier_mix"].items(), key=lambda kv: kv[1], default=(None, 0))
        out_rows.append({
            "period":              period,
            "bills":               a["bills"],
            "pax":                 a["pax"],
            "revenue_ex":          round(a["revenue_ex"], 2),
            "revenue_inc":         round(a["revenue_inc"], 2),
            "cost":                round(a["cost"], 2),
            "profit":              round(profit, 2),
            "margin_pct":          round(margin, 2),
            "avg_per_bill_rev":    round(avg_per_bill_rev, 2),
            "avg_per_bill_cost":   round(avg_per_bill_cost, 2),
            "avg_per_bill_profit": round(avg_per_bill_profit, 2),
            "dominant_tier":       dom[0],
            "tier_mix":            dict(a["tier_mix"]),
        })

    profit_overall = overall["revenue_ex"] - overall["cost"]
    return {
        "granularity": granularity,
        "rows":        out_rows,
        "overall": {
            "bills":              overall["bills"],
            "pax":                overall["pax"],
            "revenue_ex":         round(overall["revenue_ex"], 2),
            "cost":               round(overall["cost"], 2),
            "profit":             round(profit_overall, 2),
            "margin_pct":         round(profit_overall / overall["revenue_ex"] * 100, 2) if overall["revenue_ex"] else 0,
            "avg_per_bill_rev":   round(overall["revenue_ex"] / overall["bills"], 2) if overall["bills"] else 0,
            "avg_per_bill_cost":  round(overall["cost"]       / overall["bills"], 2) if overall["bills"] else 0,
            "avg_per_bill_profit":round(profit_overall        / overall["bills"], 2) if overall["bills"] else 0,
            "tier_mix":           {k: int(v) for k, v in overall["tier_mix"].items()},
            "tier_cost_mix":      {k: round(v, 0) for k, v in overall["tier_cost_mix"].items()},
        },
    }


@app.get("/api/profit/summary")
@ttl_cache
def profit_summary(
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
):
    """Aggregate profit metrics for the period (revenue, COGS, GP, margin, per-bill)."""
    where, params = _profit_filters(branch, group, month_from, month_to, month)
    with get_db() as con:
        row = con.execute(f"""
            SELECT
              ROUND(SUM(ssm.subtotal_ex_vat), 2)               AS revenue,
              ROUND(SUM(ssm.subtotal_ex_vat), 2)               AS revenue_ex_vat,
              ROUND(SUM(ssm.total_inc_vat), 2)                 AS pos_total_inc_vat_diagnostic,
              ROUND(SUM({_cogs_case()}), 2)                    AS cogs,
              ROUND(SUM({_costed_rev_case()}), 2)              AS costed_revenue_ex_vat,
              SUM(ssm.qty) AS qty_sold
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus     sk ON sk.sku_id  = ssm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
        """, params).fetchone()

    revenue        = row["revenue"]                or 0   # ex-VAT (Rule 1)
    revenue_ex     = row["revenue_ex_vat"]         or 0
    cogs           = row["cogs"]                   or 0
    costed_rev_ex  = row["costed_revenue_ex_vat"]  or 0
    gross_profit   = costed_rev_ex - cogs
    margin_pct     = round(gross_profit / costed_rev_ex * 100, 2) if costed_rev_ex > 0 else None
    coverage_pct   = round(costed_rev_ex / revenue_ex   * 100, 1) if revenue_ex    > 0 else 0

    # tx count from sales_transaction (bills only — total_inc_vat is diagnostic, not revenue)
    tx_where, tx_params = _tx_filters(branch, group, month_from, month_to, month)
    with get_db() as con:
        tx_row = con.execute(f"""
            SELECT COUNT(*) AS tx_count,
                   ROUND(SUM(st.total_inc_vat), 2) AS pos_total_inc_vat_diagnostic
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {tx_where}
        """, tx_params).fetchone()
    tx_count = tx_row["tx_count"] or 0

    profit_per_bill = round(gross_profit / tx_count, 2) if tx_count > 0 else 0
    # avg_ticket: revenue (ex-VAT) per bill — switched from inc-VAT total per Rule 1
    avg_ticket      = round(revenue      / tx_count, 2) if tx_count > 0 else 0

    # Monthly trend (Rule 2) — pair the cumulative totals with per-period breakdown
    with get_db() as con:
        trend_rows = con.execute(f"""
            SELECT ssm.period_month                              AS period_month,
              ROUND(SUM(ssm.subtotal_ex_vat), 2)                 AS revenue,
              ROUND(SUM({_cogs_case()}), 2)                      AS cogs,
              ROUND(SUM({_costed_rev_case()}), 2)                AS costed_revenue_ex_vat
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus     sk ON sk.sku_id  = ssm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY ssm.period_month
            ORDER BY ssm.period_month
        """, params).fetchall()
    monthly_trend = []
    for tr in trend_rows:
        d = dict(tr)
        gp = (d["costed_revenue_ex_vat"] or 0) - (d["cogs"] or 0)
        d["gross_profit"] = round(gp, 2)
        d["margin_pct"]   = round(gp / d["costed_revenue_ex_vat"] * 100, 2) if (d["costed_revenue_ex_vat"] or 0) > 0 else None
        monthly_trend.append(d)

    return {
        "revenue":           revenue,
        "revenue_ex_vat":    revenue_ex,
        "cogs":              cogs,
        "gross_profit":      round(gross_profit, 2),
        "margin_pct":        margin_pct,
        "tx_count":          tx_count,
        "avg_ticket":        avg_ticket,
        "profit_per_bill":   profit_per_bill,
        "cost_coverage_pct": coverage_pct,
        "qty_sold":          row["qty_sold"] or 0,
        "monthly_trend":     monthly_trend,
        "pos_total_inc_vat_diagnostic": row["pos_total_inc_vat_diagnostic"] or 0,
    }


@app.get("/api/profit/trend")
@ttl_cache
def profit_trend(
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Monthly trend of revenue, COGS, gross profit, margin."""
    where, params = _profit_filters(branch, group, month_from, month_to, None)
    with get_db() as con:
        rows = con.execute(f"""
            SELECT ssm.period_month,
              ROUND(SUM(ssm.subtotal_ex_vat), 2)   AS revenue_ex_vat,
              ROUND(SUM({_cogs_case()}), 2)        AS cogs,
              ROUND(SUM({_costed_rev_case()}), 2)  AS costed_revenue_ex_vat
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus     sk ON sk.sku_id  = ssm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY ssm.period_month
            ORDER BY ssm.period_month
        """, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        gp = (d["costed_revenue_ex_vat"] or 0) - (d["cogs"] or 0)
        d["gross_profit"] = round(gp, 2)
        d["margin_pct"]   = round(gp / d["costed_revenue_ex_vat"] * 100, 2) if d["costed_revenue_ex_vat"] > 0 else None
        out.append(d)
    return out


@app.get("/api/profit/by-branch")
@ttl_cache
def profit_by_branch(
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
):
    """Profit per branch."""
    where, params = _profit_filters(None, group, month_from, month_to, month)
    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.branch_id, b.name AS branch,
              ROUND(SUM(ssm.subtotal_ex_vat), 2)   AS revenue_ex_vat,
              ROUND(SUM({_cogs_case()}), 2)        AS cogs,
              ROUND(SUM({_costed_rev_case()}), 2)  AS costed_revenue_ex_vat,
              SUM(ssm.qty) AS qty
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus     sk ON sk.sku_id  = ssm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY b.branch_id
            ORDER BY costed_revenue_ex_vat DESC
        """, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        gp = (d["costed_revenue_ex_vat"] or 0) - (d["cogs"] or 0)
        d["gross_profit"] = round(gp, 2)
        d["margin_pct"]   = round(gp / d["costed_revenue_ex_vat"] * 100, 2) if d["costed_revenue_ex_vat"] > 0 else None
        out.append(d)
    return out


# -----------------------------------------------------------------------------
# GP by Branch x Month - combines BOTH Easy + CM POS with the new BoM rule:
#   Easy: COGS = SUM(qty*unit_cost) EXCLUDING tier SKUs (BUFFET350/500/590/799/CHILD)
#   CM:   COGS = SUM(qty*unit_cost) for ALL SKUs (tier is the only cost signal)
#   Revenue = SUM(subtotal_ex_vat) for all sources
# -----------------------------------------------------------------------------
@app.get("/api/gp/by-branch-month")
@ttl_cache
def gp_by_branch_month(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    source:     Optional[str] = Query("all"),
    variant:    Optional[str] = Query(None, description="A | B | all — filter à la carte vs buffet SKUs"),
    granularity: str          = Query("month", description="day | week | month — bucket size for per-branch trend"),
):
    """GP per branch x month using the source-aware BoM rule.

    Note: CM POS data was archived 2026-05-27 (now in *_archive tables).
    Main tables are Easy-only since 2025-05. The `source` param is kept
    for backwards compatibility but practically all rows are Easy now.
    Use /api/legacy/cm/by-branch-month for Jan-Apr 2025 historical view.

    `granularity` ∈ {day, week, month}: when day/week, each branch row also
    gains a `trend` array of pro-rated buckets (monthly COGS pro-rated by
    daily Easy-tx revenue, same approach as /api/buffet/gp)."""
    if granularity not in ("day", "week", "month"):
        granularity = "month"
    filters = ["b.name != 'ALL'", "COALESCE(b.active,1) = 1"]
    params: dict = {}
    src = (source or "all").lower()
    if src == "easy":
        filters.append("ssm.source = 'Easy'")
    elif src == "cm":
        filters.append("ssm.source IN ('CM','CM POS')")
    _month_range_filter(filters, params, month_from, month_to, month, col="ssm.period_month")
    var_clause = _variant_filter(variant)
    if var_clause:
        filters.append(var_clause)
    where = "WHERE " + " AND ".join(filters)

    # Phase 8: use the central helper so monthly cost overrides are honored.
    cogs_expr = f"SUM({_cogs_case('ssm','sk','sc')})"

    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.branch_id, b.name AS branch,
                   ssm.period_month AS month,
                   ssm.source       AS source,
                   ROUND(SUM(ssm.subtotal_ex_vat), 2) AS revenue,
                   ROUND({cogs_expr}, 2)              AS cogs
              FROM sales_sku_monthly ssm
              JOIN branches b ON b.branch_id = ssm.branch_id
              JOIN skus     sk ON sk.sku_id  = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY b.branch_id, ssm.period_month, ssm.source
            ORDER BY b.name, ssm.period_month
        """, params).fetchall()

        try:
            srows = con.execute(
                "SELECT key, value FROM app_settings WHERE key IN ('cost_target_lo','cost_target_hi')"
            ).fetchall()
            settings = {r["key"]: r["value"] for r in srows}
        except Exception:
            settings = {}

    try:
        target_lo = float(settings.get("cost_target_lo", 55))
    except Exception:
        target_lo = 55.0
    try:
        target_hi = float(settings.get("cost_target_hi", 70))
    except Exception:
        target_hi = 70.0

    by_branch: dict = {}
    for r in rows:
        d = dict(r)
        bid = d["branch_id"]
        b = by_branch.setdefault(bid, {
            "branch": d["branch"], "branch_id": bid, "monthly": [],
            "total_revenue": 0.0, "total_cogs": 0.0,
        })
        rev  = float(d["revenue"] or 0)
        cogs = float(d["cogs"]    or 0)
        gp   = rev - cogs
        gp_pct = (gp / rev * 100) if rev > 0 else None
        fc_pct = (cogs / rev * 100) if rev > 0 else None
        b["monthly"].append({
            "month":         d["month"],
            "source":        d["source"],
            "revenue":       round(rev, 2),
            "cogs":          round(cogs, 2),
            "gp":            round(gp, 2),
            "gp_pct":        round(gp_pct, 2) if gp_pct is not None else None,
            "food_cost_pct": round(fc_pct, 2) if fc_pct is not None else None,
        })
        b["total_revenue"] += rev
        b["total_cogs"]    += cogs

    out_rows = []
    grand_rev = grand_cogs = 0.0
    for b in by_branch.values():
        tr = b["total_revenue"]; tc = b["total_cogs"]; tg = tr - tc
        b["total_revenue"]       = round(tr, 2)
        b["total_cogs"]          = round(tc, 2)
        b["total_gp"]            = round(tg, 2)
        b["total_gp_pct"]        = round(tg / tr * 100, 2) if tr > 0 else None
        b["total_food_cost_pct"] = round(tc / tr * 100, 2) if tr > 0 else None
        grand_rev  += tr
        grand_cogs += tc
        out_rows.append(b)

    out_rows.sort(key=lambda x: x["total_revenue"], reverse=True)

    grand_gp = grand_rev - grand_cogs

    # ── Granularity-aware per-branch trend (day/week buckets) ────────────
    if granularity in ("day", "week") and out_rows:
        from datetime import date as _date_cls
        def _wkb(date_str: str) -> str:
            d = _date_cls.fromisoformat(date_str)
            iso = d.isocalendar()
            return f"{iso[0]}-W{iso[1]:02d}"

        # Daily tx revenue per branch over the period
        tx_filters = ["b.name != 'ALL'", "st.source = 'Easy'", "COALESCE(b.active,1) = 1"]
        tx_params: dict = {}
        _month_range_filter(tx_filters, tx_params, month_from, month_to, month,
                            col="SUBSTR(st.sold_at,1,7)")
        tx_where = "WHERE " + " AND ".join(tx_filters)
        with get_db() as con:
            daily_rows = con.execute(f"""
                SELECT b.branch_id,
                       DATE(st.sold_at)        AS d,
                       SUBSTR(st.sold_at,1,7)  AS m,
                       SUM(COALESCE(st.food_subtotal,0)
                         + COALESCE(st.drink_subtotal,0)
                         + COALESCE(st.other_subtotal,0)) AS rev_tx
                  FROM sales_transaction st
                  JOIN branches b ON b.branch_id = st.branch_id
                {tx_where}
                GROUP BY b.branch_id, DATE(st.sold_at)
            """, tx_params).fetchall()

        # Index: monthly tx rev per (branch, month)
        m_tx_rev: dict = {}
        for r in daily_rows:
            key = (r["branch_id"], r["m"])
            m_tx_rev[key] = m_tx_rev.get(key, 0.0) + float(r["rev_tx"] or 0)

        # Per branch: bucket → {rev, cogs}
        for b in out_rows:
            bid = b["branch_id"]
            monthly_map = {m["month"]: m for m in b["monthly"]}
            buckets: dict = {}
            for r in daily_rows:
                if r["branch_id"] != bid:
                    continue
                mo = r["m"]
                mrow = monthly_map.get(mo)
                if not mrow:
                    continue
                m_total = m_tx_rev.get((bid, mo), 0.0)
                if m_total <= 0:
                    continue
                frac = float(r["rev_tx"] or 0) / m_total
                d_cogs = float(mrow["cogs"] or 0)    * frac
                d_rev  = float(mrow["revenue"] or 0) * frac
                k = r["d"] if granularity == "day" else _wkb(r["d"])
                bb = buckets.setdefault(k, {"rev": 0.0, "cogs": 0.0})
                bb["rev"]  += d_rev
                bb["cogs"] += d_cogs
            trend = []
            for k in sorted(buckets.keys()):
                v   = buckets[k]
                gpv = v["rev"] - v["cogs"]
                trend.append({
                    "bucket":        k,
                    "revenue":       round(v["rev"],  2),
                    "cogs":          round(v["cogs"], 2),
                    "gp":            round(gpv, 2),
                    "gp_pct":        round(gpv / v["rev"] * 100, 2) if v["rev"] > 0 else None,
                    "food_cost_pct": round(v["cogs"] / v["rev"] * 100, 2) if v["rev"] > 0 else None,
                })
            b["trend"] = trend

    return {
        "rows": out_rows,
        "granularity": granularity,
        "target_lo": target_lo,
        "target_hi": target_hi,
        "grand_total": {
            "revenue":       round(grand_rev, 2),
            "cogs":          round(grand_cogs, 2),
            "gp":            round(grand_gp, 2),
            "gp_pct":        round(grand_gp / grand_rev * 100, 2) if grand_rev > 0 else None,
            "food_cost_pct": round(grand_cogs / grand_rev * 100, 2) if grand_rev > 0 else None,
        },
    }


@app.get("/api/legacy/cm")
@ttl_cache
def legacy_cm_summary():
    """Read-only view of archived CM POS data (Jan-Apr 2025 + overlap May-Oct 2025).

    Main tables hold only Easy data since 2026-05-27 archive. This endpoint
    surfaces what was moved to sales_sku_monthly_archive for historical reference."""
    with get_db() as con:
        # Check archive exists
        exists = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sales_sku_monthly_archive'"
        ).fetchone()
        if not exists:
            return {"available": False, "reason": "Archive table not found"}

        months = [dict(r) for r in con.execute("""
            SELECT period_month, ROUND(SUM(subtotal_ex_vat),2) AS revenue,
                   COUNT(DISTINCT branch_id) AS branches
            FROM sales_sku_monthly_archive
            GROUP BY period_month ORDER BY period_month
        """).fetchall()]

        branches = [dict(r) for r in con.execute("""
            SELECT b.name AS branch,
                   ROUND(SUM(a.subtotal_ex_vat),2) AS revenue,
                   COUNT(DISTINCT a.period_month) AS months
            FROM sales_sku_monthly_archive a
            JOIN branches b ON b.branch_id = a.branch_id
            GROUP BY b.branch_id ORDER BY revenue DESC
        """).fetchall()]

        totals = dict(con.execute("""
            SELECT COUNT(*) AS rows,
                   ROUND(SUM(subtotal_ex_vat),2) AS total_revenue,
                   MIN(period_month) AS min_month,
                   MAX(period_month) AS max_month
            FROM sales_sku_monthly_archive
        """).fetchone())

    return {
        "available": True,
        "note": "CM POS data archived 2026-05-27. Main dashboard uses Easy only from 2025-05 onwards.",
        "totals":   totals,
        "months":   months,
        "branches": branches,
    }


@app.get("/api/profit/by-sku")
@ttl_cache
def profit_by_sku(
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    limit:      int = Query(20),
    order:      str = Query("desc"),
):
    """Top (order=desc) or bottom (order=asc) SKUs by profit contribution.

    Costed SKUs only. Excludes Easy buffet-tier SKUs (BUFFET350/500/590/799/CHILD)
    from the cost half — their COGS is captured at item level (B0xxx)."""
    filters = [
        "b.name != 'ALL'",
        f"{_effective_cost_subquery()} IS NOT NULL",
        f"NOT (ssm.source = 'Easy' AND sk.sku_code IN {_EASY_TIER_CODES_SQL})",
    ]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month, col="ssm.period_month")
    where = "WHERE " + " AND ".join(filters)
    order_dir = "DESC" if order == "desc" else "ASC"
    with get_db() as con:
        rows = con.execute(f"""
            SELECT sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
              c.name AS category,
              SUM(ssm.qty)                       AS qty,
              ROUND(SUM(ssm.subtotal_ex_vat), 2) AS revenue_ex_vat,
              ROUND(SUM({_cogs_case()}), 2) AS cogs,
              ROUND(SUM(ssm.subtotal_ex_vat) - SUM({_cogs_case()}), 2) AS gross_profit,
              CASE WHEN SUM(ssm.subtotal_ex_vat) > 0
                THEN ROUND((SUM(ssm.subtotal_ex_vat) - SUM({_cogs_case()}))
                          / SUM(ssm.subtotal_ex_vat) * 100, 2)
                ELSE NULL END AS margin_pct,
              sc.unit_cost
            FROM sales_sku_monthly ssm
            JOIN branches   b  ON b.branch_id   = ssm.branch_id
            JOIN skus       sk ON sk.sku_id     = ssm.sku_id
            JOIN categories c  ON c.category_id = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id  = ssm.sku_id
            {where}
            GROUP BY sk.sku_id
            HAVING SUM(ssm.qty) > 0
            ORDER BY gross_profit {order_dir}
            LIMIT :lim
        """, {**params, "lim": limit}).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/profit/per-bill-distribution")
@ttl_cache
def profit_per_bill_distribution(
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
):
    """Approximate distribution of profit per bill.
    Since transactions don't link to SKUs, we apply the period's overall
    costed-margin to each transaction's food+drink+other subtotal."""
    summary = profit_summary(branch=branch, group=group, month_from=month_from, month_to=month_to, month=month)
    margin_pct = summary["margin_pct"] or 0

    if margin_pct == 0:
        return {
            "buckets": [], "total_tx": 0, "avg_profit": 0,
            "min_profit": 0, "max_profit": 0, "margin_pct_used": 0,
            "note": "No costed SKUs in period. Upload SKU costs to enable.",
        }

    tx_where, tx_params = _tx_filters(branch, group, month_from, month_to, month)
    m_frac = margin_pct / 100.0
    with get_db() as con:
        r = con.execute(f"""
            WITH bills AS (
              SELECT (COALESCE(st.food_subtotal,0)
                    + COALESCE(st.drink_subtotal,0)
                    + COALESCE(st.other_subtotal,0)) * {m_frac} AS est_profit
              FROM sales_transaction st
              JOIN branches b ON b.branch_id = st.branch_id
              {tx_where}
            )
            SELECT
              SUM(CASE WHEN est_profit <= 0    THEN 1 ELSE 0 END) AS b_neg,
              SUM(CASE WHEN est_profit > 0    AND est_profit <= 100  THEN 1 ELSE 0 END) AS b_0_100,
              SUM(CASE WHEN est_profit > 100  AND est_profit <= 300  THEN 1 ELSE 0 END) AS b_100_300,
              SUM(CASE WHEN est_profit > 300  AND est_profit <= 500  THEN 1 ELSE 0 END) AS b_300_500,
              SUM(CASE WHEN est_profit > 500  AND est_profit <= 1000 THEN 1 ELSE 0 END) AS b_500_1000,
              SUM(CASE WHEN est_profit > 1000 THEN 1 ELSE 0 END) AS b_1000_plus,
              COUNT(*) AS total,
              ROUND(AVG(est_profit), 2) AS avg_profit,
              ROUND(MIN(est_profit), 2) AS min_profit,
              ROUND(MAX(est_profit), 2) AS max_profit
            FROM bills
        """, tx_params).fetchone()

    return {
        "buckets": [
            {"label": "<=0",      "count": r["b_neg"]        or 0},
            {"label": "1-100",    "count": r["b_0_100"]      or 0},
            {"label": "101-300",  "count": r["b_100_300"]    or 0},
            {"label": "301-500",  "count": r["b_300_500"]    or 0},
            {"label": "501-1000", "count": r["b_500_1000"]   or 0},
            {"label": "1000+",    "count": r["b_1000_plus"]  or 0},
        ],
        "total_tx":         r["total"]      or 0,
        "avg_profit":       r["avg_profit"] or 0,
        "min_profit":       r["min_profit"] or 0,
        "max_profit":       r["max_profit"] or 0,
        "margin_pct_used":  margin_pct,
    }


# ============================================================================
# DEEP P&L / PER-BILL ANALYSIS
# ============================================================================

def _pnl_sku_query(where: str, params: dict) -> str:
    return f"""
        SELECT ssm.period_month,
          ROUND(SUM(ssm.subtotal_ex_vat), 2)  AS revenue,
          ROUND(SUM(ssm.subtotal_ex_vat), 2)  AS revenue_ex_vat,
          ROUND(SUM(ssm.total_inc_vat), 2)    AS pos_total_inc_vat_diagnostic,
          ROUND(SUM({_cogs_case()}), 2)        AS cogs,
          ROUND(SUM({_costed_rev_case()}), 2)  AS costed_rev_ex
        FROM sales_sku_monthly ssm
        JOIN branches b ON b.branch_id = ssm.branch_id
        JOIN skus     sk ON sk.sku_id  = ssm.sku_id
        LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
        {where}
        GROUP BY ssm.period_month
        ORDER BY ssm.period_month
    """


def _add_gp_fields(rows_out: list) -> list:
    """In-place: compute gross_profit, margin_pct, mom_pct."""
    prev_gp = None
    for d in rows_out:
        gp = (d.get("costed_rev_ex") or 0) - (d.get("cogs") or 0)
        d["gross_profit"] = round(gp, 2)
        costed = d.get("costed_rev_ex") or 0
        d["margin_pct"] = round(gp / costed * 100, 2) if costed > 0 else None
        d["mom_pct"] = round((gp - prev_gp) / prev_gp * 100, 1) if (prev_gp and prev_gp > 0) else None
        prev_gp = gp if gp else prev_gp
    return rows_out


@app.get("/api/pnl/monthly")
@ttl_cache
def pnl_monthly(
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Monthly P&L trend: revenue, COGS, GP, margin%, tx_count, profit_per_bill."""
    where, params       = _profit_filters(branch, group, month_from, month_to, None)
    tx_where, tx_params = _tx_filters(branch, group, month_from, month_to, None)

    with get_db() as con:
        sku_rows = con.execute(_pnl_sku_query(where, params), params).fetchall()
        tx_rows  = con.execute(f"""
            SELECT strftime('%Y-%m', st.sold_at) AS period_month,
                   COUNT(*) AS tx_count,
                   SUM(st.customer_count) AS customer_count,
                   ROUND(AVG(st.total_inc_vat), 2) AS avg_ticket
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {tx_where}
            GROUP BY period_month
        """, tx_params).fetchall()

    tx_map = {r["period_month"]: dict(r) for r in tx_rows}
    out = _add_gp_fields([dict(r) for r in sku_rows])
    for d in out:
        tx = tx_map.get(d["period_month"], {})
        tx_count = tx.get("tx_count") or 0
        d["tx_count"]       = tx_count
        d["customer_count"] = tx.get("customer_count") or 0
        d["avg_ticket"]     = tx.get("avg_ticket") or 0
        gp = d["gross_profit"] or 0
        d["profit_per_bill"] = round(gp / tx_count, 2) if tx_count > 0 else None
    return out


@app.get("/api/pnl/branches")
@ttl_cache
def pnl_branches_deep(
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """All-branch P&L comparison with per-bill metrics."""
    where, params = _profit_filters(None, group, month_from, month_to, None)

    tx_filters = ["b.name != 'ALL'", "st.source = 'Easy'", "COALESCE(b.active,1) = 1"]
    tx_params: dict = {}
    if group:
        tx_filters.append("COALESCE(b.branch_type,'master') = :group")
        tx_params["group"] = group
    _month_range_filter(tx_filters, tx_params, month_from, month_to, None,
                        col="strftime('%Y-%m', st.sold_at)")
    tx_where = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        sku_rows = con.execute(f"""
            SELECT b.name AS branch,
              ROUND(SUM(ssm.subtotal_ex_vat), 2)   AS revenue,
              ROUND(SUM(ssm.subtotal_ex_vat), 2)   AS revenue_ex_vat,
              ROUND(SUM(ssm.total_inc_vat), 2)     AS pos_total_inc_vat_diagnostic,
              ROUND(SUM({_cogs_case()}), 2)        AS cogs,
              ROUND(SUM({_costed_rev_case()}), 2)  AS costed_rev_ex,
              COUNT(DISTINCT CASE WHEN sc.unit_cost IS NOT NULL THEN ssm.sku_id END) AS costed_skus,
              COUNT(DISTINCT ssm.sku_id) AS total_skus
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus     sk ON sk.sku_id  = ssm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY b.name
            ORDER BY SUM(ssm.subtotal_ex_vat) DESC
        """, params).fetchall()

        tx_rows = con.execute(f"""
            SELECT b.name AS branch,
                   COUNT(*) AS tx_count,
                   SUM(st.customer_count) AS customer_count,
                   ROUND(AVG(st.total_inc_vat), 2) AS avg_ticket
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {tx_where}
            GROUP BY b.name
        """, tx_params).fetchall()

    tx_map = {r["branch"]: dict(r) for r in tx_rows}
    out = []
    for r in sku_rows:
        d = dict(r)
        gp = (d.get("costed_rev_ex") or 0) - (d.get("cogs") or 0)
        d["gross_profit"] = round(gp, 2)
        costed = d.get("costed_rev_ex") or 0
        d["margin_pct"] = round(gp / costed * 100, 2) if costed > 0 else None
        tx = tx_map.get(d["branch"], {})
        tx_count = tx.get("tx_count") or 0
        d["tx_count"]   = tx_count
        d["avg_ticket"] = tx.get("avg_ticket") or 0
        d["profit_per_bill"] = round(gp / tx_count, 2) if tx_count > 0 else None
        out.append(d)

    ranked = sorted(out, key=lambda x: -(x["gross_profit"] or 0))
    for i, r in enumerate(ranked):
        r["gp_rank"] = i + 1
    return out


@app.get("/api/pnl/branch-detail")
@ttl_cache
def pnl_branch_detail(
    branch:     str            = Query(...),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Deep dive for one branch: monthly P&L, top 30 SKUs by GP, bill revenue stats."""
    sku_filters = ["b.name != 'ALL'", "COALESCE(b.active,1) = 1", "b.name = :branch"]
    sku_params: dict = {"branch": branch}
    _month_range_filter(sku_filters, sku_params, month_from, month_to, None, col="ssm.period_month")
    sku_where = "WHERE " + " AND ".join(sku_filters)

    tx_filters = ["b.name = :branch", "st.source = 'Easy'"]
    tx_params: dict = {"branch": branch}
    _month_range_filter(tx_filters, tx_params, month_from, month_to, None,
                        col="strftime('%Y-%m', st.sold_at)")
    tx_where = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        monthly_rows = con.execute(_pnl_sku_query(sku_where, sku_params), sku_params).fetchall()

        top_skus = con.execute(f"""
            SELECT sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   c.name AS category,
                   ROUND(SUM(ssm.qty), 0)                AS qty,
                   ROUND(SUM(ssm.subtotal_ex_vat), 2)    AS revenue,
                   ROUND(SUM(ssm.subtotal_ex_vat), 2)    AS revenue_ex_vat,
                   ROUND(SUM(ssm.total_inc_vat), 2)      AS pos_total_inc_vat_diagnostic,
                   sc.unit_cost,
                   ROUND(SUM({_cogs_case()}), 2)           AS cogs,
                   ROUND(SUM(ssm.subtotal_ex_vat) - SUM({_cogs_case()}), 2) AS gp,
                   CASE WHEN SUM(ssm.subtotal_ex_vat) > 0
                        THEN ROUND(100.0*(SUM(ssm.subtotal_ex_vat) - SUM({_cogs_case()}))
                                   / SUM(ssm.subtotal_ex_vat), 1) ELSE NULL END AS margin_pct
            FROM sales_sku_monthly ssm
            JOIN branches b   ON b.branch_id   = ssm.branch_id
            JOIN skus sk      ON sk.sku_id      = ssm.sku_id
            JOIN categories c ON c.category_id  = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id  = ssm.sku_id
            {sku_where}
            GROUP BY sk.sku_id
            HAVING SUM(ssm.qty) > 0
            ORDER BY SUM(ssm.subtotal_ex_vat) - SUM({_cogs_case()}) DESC
            LIMIT 50
        """, sku_params).fetchall()

        bill_vals = con.execute(f"""
            SELECT ROUND(st.total_inc_vat, 2) AS val
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {tx_where}
            ORDER BY val
        """, tx_params).fetchall()

        tx_monthly = con.execute(f"""
            SELECT strftime('%Y-%m', st.sold_at) AS period_month,
                   COUNT(*) AS tx_count,
                   ROUND(AVG(st.total_inc_vat), 2) AS avg_ticket,
                   SUM(st.customer_count) AS customer_count
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {tx_where}
            GROUP BY period_month
        """, tx_params).fetchall()

    # Build monthly with GP and TX data
    monthly_out = _add_gp_fields([dict(r) for r in monthly_rows])
    tx_mo_map = {r["period_month"]: dict(r) for r in tx_monthly}
    for d in monthly_out:
        tx = tx_mo_map.get(d["period_month"], {})
        tx_count = tx.get("tx_count") or 0
        d["tx_count"]       = tx_count
        d["avg_ticket"]     = tx.get("avg_ticket") or 0
        d["customer_count"] = tx.get("customer_count") or 0
        gp = d["gross_profit"] or 0
        d["profit_per_bill"] = round(gp / tx_count, 2) if tx_count > 0 else None

    # Bill revenue distribution and percentiles
    vals = sorted([r["val"] for r in bill_vals if r["val"] is not None])
    n = len(vals)

    def pct_val(p):
        if n == 0: return 0
        idx = max(0, min(n - 1, int(n * p)))
        return round(vals[idx], 2)

    BUCKETS = [(0, 100), (100, 200), (200, 300), (300, 500), (500, 750), (750, 1000), (1000, 1500), (1500, None)]
    bill_buckets = []
    for lo, hi in BUCKETS:
        count = sum(1 for v in vals if v >= lo and (hi is None or v < hi))
        label = f"{lo}-{hi}" if hi else f"{lo}+"
        bill_buckets.append({"label": label, "count": count})

    # Compute blended margin from monthly data
    total_costed_rev = sum((d.get("costed_rev_ex") or 0) for d in monthly_out)
    total_cogs       = sum((d.get("cogs") or 0) for d in monthly_out)
    total_gp         = total_costed_rev - total_cogs
    margin_frac      = (total_gp / total_costed_rev) if total_costed_rev > 0 else 0

    profit_buckets = []
    PBUCKETS = [(None, 0), (0, 50), (50, 100), (100, 200), (200, 400), (400, 700), (700, None)]
    if margin_frac > 0 and n > 0:
        profit_vals = sorted([v * margin_frac for v in vals])
        for lo, hi in PBUCKETS:
            count = sum(1 for v in profit_vals if
                        (lo is None or v >= lo) and (hi is None or v < hi))
            label = "<0" if lo is None else (f"{lo}+" if hi is None else f"{lo}-{hi}")
            profit_buckets.append({"label": label, "count": count})

    bill_stats = {
        "tx_count":         n,
        "avg":              round(sum(vals) / n, 2) if n else 0,
        "min":              vals[0] if n else 0,
        "max":              vals[-1] if n else 0,
        "p10":              pct_val(0.1),
        "p25":              pct_val(0.25),
        "p50":              pct_val(0.5),
        "p75":              pct_val(0.75),
        "p90":              pct_val(0.9),
        "buckets":          bill_buckets,
        "profit_buckets":   profit_buckets,
        "margin_pct_used":  round(margin_frac * 100, 1),
        "avg_profit":       round(sum(vals) * margin_frac / n, 2) if n else 0,
    }

    return {
        "branch":   branch,
        "monthly":  monthly_out,
        "top_skus": [dict(r) for r in top_skus],
        "bill_stats": bill_stats,
    }


# ============================================================================
# HOURLY analysis with operating-hours filter (uses closed_hours table)
# ============================================================================
@app.get("/api/analysis/hourly_open")
@ttl_cache
def analysis_hourly_open(
    month:       Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    branch:      Optional[str] = Query(None),
    granularity: str           = Query("daily"),  # daily|weekly|monthly
):
    """Like /api/analysis/hourly but each hour is annotated with `is_open`,
    derived from the closed_hours table. Hours marked closed are still returned
    (count may be 0) so the chart can show them greyed out."""
    # Inline the hourly query (can't call FastAPI endpoints as plain functions)
    filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(f"""
            SELECT CAST(strftime('%H', st.sold_at) AS INTEGER) AS hour,
                   COUNT(*)                                    AS tx_count,
                   COALESCE(SUM(st.customer_count), 0)         AS customer_count,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2)             AS revenue,
                   ROUND(AVG(st.total_inc_vat), 2)             AS avg_ticket
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY hour ORDER BY hour
        """, params).fetchall()
        # Count distinct periods for average computation
        if granularity == "weekly":
            _period_expr = _iso_week_sql('st.sold_at')
        elif granularity == "monthly":
            _period_expr = "strftime('%Y-%m', st.sold_at)"
        else:
            _period_expr = "DATE(st.sold_at)"
        day_row = con.execute(f"""
            SELECT COUNT(DISTINCT {_period_expr}) AS cnt
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
        """, params).fetchone()
        total_days = max(day_row["cnt"] if day_row else 1, 1)

        hour_map = {r["hour"]: dict(r) for r in rows}
        raw = [hour_map.get(h, {"hour": h, "tx_count": 0, "customer_count": 0,
                                "revenue": 0.0, "avg_ticket": 0.0}) for h in range(24)]

        if branch:
            ch_rows = con.execute("""
                SELECT ch.hour_from, ch.hour_to, ch.is_closed
                FROM closed_hours ch
                JOIN branches b ON b.branch_id = ch.branch_id
                WHERE b.name = :branch
            """, {"branch": branch}).fetchall()
        else:
            ch_rows = con.execute(
                "SELECT hour_from, hour_to, is_closed FROM closed_hours"
            ).fetchall()

    if ch_rows:
        closed_set = set()
        for r in ch_rows:
            if r["is_closed"]:
                hf = r["hour_from"] if r["hour_from"] is not None else 23
                ht = r["hour_to"]   if r["hour_to"]   is not None else 9
                # Support wraparound (hf > ht means range crosses midnight)
                if hf <= ht:
                    for h in range(hf, ht + 1): closed_set.add(h)
                else:
                    for h in range(hf, 24):     closed_set.add(h)
                    for h in range(0, ht + 1):  closed_set.add(h)
        open_hours = set(range(24)) - closed_set
    else:
        open_hours = set(range(10, 23))  # default 10:00–22:00
    for h in raw:
        h["is_open"] = h["hour"] in open_hours
        h["avg_daily_revenue"] = round((h["revenue"] or 0) / total_days, 2)
        h["avg_daily_tx"]      = round((h["tx_count"] or 0) / total_days, 2)
        h["total_days"]        = total_days
    return raw


# ---------------------------------------------------------------------------
# /api/home/brief  — Daily Brief summary
# ---------------------------------------------------------------------------
@app.get("/api/home/brief")
def home_brief(branch: Optional[str] = Query(None)):
    """Yesterday vs target + MTD + recent anomalies + top/bottom branch."""
    with get_db() as con:
        # Get yesterday's date from MAX(sold_at)
        max_row = con.execute(
            "SELECT DATE(MAX(sold_at)) AS max_date FROM sales_transaction WHERE source='Easy'"
        ).fetchone()
        yesterday = max_row["max_date"] if max_row else None
        if not yesterday:
            return {"yesterday": None}

        from datetime import datetime as _dt
        try:
            yd = _dt.strptime(yesterday, "%Y-%m-%d")
            dow_names = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
            dow_label = dow_names[yd.weekday()]
            current_month = yd.strftime("%Y-%m")
            wow_date = (_dt.strptime(yesterday, "%Y-%m-%d").replace(
                day=yd.day) if True else None)
            import datetime as _dtt
            wow_date_str = (_dt.strptime(yesterday, "%Y-%m-%d") - _dtt.timedelta(days=7)).strftime("%Y-%m-%d")
        except Exception:
            dow_label = ""
            current_month = yesterday[:7]
            wow_date_str = None

        # Branch filter
        branch_filter = ""
        branch_params: dict = {"yesterday": yesterday}
        if branch:
            branch_filter = " AND b.name = :branch"
            branch_params["branch"] = branch

        # Revenue / tx / customers yesterday
        yest_row = con.execute(f"""
            SELECT
                ROUND(SUM(st.total_inc_vat) / 1.07, 2)        AS revenue,
                COUNT(*)                                AS tx_count,
                COALESCE(SUM(st.customer_count), 0)    AS customer_count,
                ROUND(AVG(st.total_inc_vat), 2)        AS avg_ticket
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            WHERE DATE(st.sold_at) = :yesterday
              AND st.source = 'Easy'
              AND b.name != 'ALL'
              {branch_filter}
        """, branch_params).fetchone()

        # Revenue same day last week
        wow_row = None
        if wow_date_str:
            wow_params = dict(branch_params)
            wow_params["yesterday"] = wow_date_str
            wow_row = con.execute(f"""
                SELECT ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS revenue
                FROM sales_transaction st
                JOIN branches b ON b.branch_id = st.branch_id
                WHERE DATE(st.sold_at) = :yesterday
                  AND st.source = 'Easy'
                  AND b.name != 'ALL'
                  {branch_filter}
            """, wow_params).fetchone()

        # MTD revenue
        mtd_params: dict = {"month": current_month}
        if branch:
            mtd_params["branch"] = branch
        mtd_row = con.execute(f"""
            SELECT
                ROUND(SUM(st.total_inc_vat) / 1.07, 2)     AS mtd_revenue,
                COUNT(DISTINCT DATE(st.sold_at))    AS mtd_days
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            WHERE strftime('%Y-%m', st.sold_at) = :month
              AND st.source = 'Easy'
              AND b.name != 'ALL'
              {'AND b.name = :branch' if branch else ''}
        """, mtd_params).fetchone()

        # Revenue by branch yesterday (top 3)
        yest_branch_rows = con.execute(f"""
            SELECT b.name AS branch,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            WHERE DATE(st.sold_at) = :yesterday
              AND st.source = 'Easy'
              AND b.name != 'ALL'
              {branch_filter}
            GROUP BY b.name
            ORDER BY revenue DESC
            LIMIT 3
        """, branch_params).fetchall()

        # MTD by branch
        mtd_branch_rows = con.execute(f"""
            SELECT b.name AS branch,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS mtd_revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            WHERE strftime('%Y-%m', st.sold_at) = :month
              AND st.source = 'Easy'
              AND b.name != 'ALL'
              {'AND b.name = :branch' if branch else ''}
            GROUP BY b.name
            ORDER BY mtd_revenue DESC
        """, mtd_params).fetchall()

        # Branch targets for current month
        try:
            target_rows = con.execute("""
                SELECT b.name AS branch, bt.revenue_target
                FROM branch_targets bt
                JOIN branches b ON b.branch_id = bt.branch_id
                WHERE bt.period_month = :month
            """, {"month": current_month}).fetchall()
            target_map = {r["branch"]: r["revenue_target"] for r in target_rows}
        except Exception:
            target_map = {}

        mtd_by_branch = []
        for r in mtd_branch_rows:
            br = r["branch"]
            mtd_by_branch.append({
                "branch": br,
                "mtd_revenue": r["mtd_revenue"],
                "revenue_target": target_map.get(br),
            })

        # MTD BoM food cost % (tier SKUs excluded to avoid double-count)
        # Phase 18d: migrated to _cogs_case helper — unified tier exclusion (incl. plain
        # BUFFET) + monthly cost overrides; was leaking ~825M phantom COGS before.
        bom_params: dict = {"month": current_month}
        if branch:
            bom_params["branch"] = branch
        mtd_bom_row = con.execute(f"""
            SELECT ROUND(SUM({_cogs_case('s','sk','c')}), 2) AS bom_cost
            FROM sales_sku_monthly s
            JOIN skus sk     ON sk.sku_id = s.sku_id
            LEFT JOIN sku_cost c ON c.sku_id = s.sku_id
            JOIN branches b  ON b.branch_id = s.branch_id
            WHERE s.period_month = :month
              AND b.name != 'ALL'
              {'AND b.name = :branch' if branch else ''}
        """, bom_params).fetchone()
        food_cost_pct_mtd = None
        mtd_bom_cost = mtd_bom_row["bom_cost"] if mtd_bom_row else None
        mtd_rev_val  = mtd_row["mtd_revenue"] if mtd_row else 0
        if mtd_bom_cost and mtd_rev_val and mtd_rev_val > 0:
            food_cost_pct_mtd = round(mtd_bom_cost / mtd_rev_val * 100, 1)

    return {
        "yesterday": yesterday,
        "dow_label": dow_label,
        "revenue_yesterday": yest_row["revenue"] if yest_row else 0,
        "tx_yesterday": yest_row["tx_count"] if yest_row else 0,
        "customer_yesterday": yest_row["customer_count"] if yest_row else 0,
        "avg_ticket_yesterday": yest_row["avg_ticket"] if yest_row else 0,
        "revenue_wow": wow_row["revenue"] if wow_row else None,
        "mtd_revenue": mtd_row["mtd_revenue"] if mtd_row else 0,
        "mtd_days": mtd_row["mtd_days"] if mtd_row else 0,
        "yesterday_by_branch": [dict(r) for r in yest_branch_rows],
        "mtd_by_branch": mtd_by_branch,
        "food_cost_pct_mtd": food_cost_pct_mtd,
        "mtd_bom_cost": mtd_bom_cost,
    }


# ---------------------------------------------------------------------------
# /api/analysis/sph  — Spend-per-Head distribution
# ---------------------------------------------------------------------------
@app.get("/api/analysis/sph")
@ttl_cache
def analysis_sph(
    month: Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to: Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
):
    filters = ["st.customer_count > 0", "st.source = 'Easy'", "b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT st.total_inc_vat / st.customer_count AS sph,
                   b.name                               AS branch,
                   CAST(strftime('%H', st.sold_at) AS INTEGER) AS hour
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
        """, params).fetchall()

    if not rows:
        return {"by_branch": [], "distribution": [], "overall": {}, "by_daypart": [], "trend": []}

    sphs = [r["sph"] for r in rows if r["sph"] is not None]
    sphs.sort()
    n = len(sphs)

    def median(lst):
        if not lst: return 0
        m = len(lst) // 2
        return (lst[m - 1] + lst[m]) / 2 if len(lst) % 2 == 0 else lst[m]

    def percentile(lst, p):
        if not lst: return 0
        idx = int(p / 100 * len(lst))
        return lst[min(idx, len(lst) - 1)]

    avg_sph = sum(sphs) / n if n else 0

    # By branch
    branch_map: dict = {}
    for r in rows:
        br = r["branch"]
        if br not in branch_map:
            branch_map[br] = []
        if r["sph"] is not None:
            branch_map[br].append(r["sph"])
    by_branch = sorted([
        {
            "branch": br,
            "avg_sph": round(sum(vals) / len(vals), 2) if vals else 0,
            "median_sph": round(median(sorted(vals)), 2) if vals else 0,
            "tx_count": len(vals),
        }
        for br, vals in branch_map.items()
    ], key=lambda x: x["avg_sph"], reverse=True)

    # Distribution buckets aligned to buffet price tiers (350 / 500 / 590 / 799)
    # Boundaries account for the typical VAT + service-charge markup (~17–25%).
    # ที่บัฟเฟ่ฯ ผู้ใหญ่: 350 → ราคาขาย ~380-470, 500 → ~530-620, 590 → ~620-770, 799 → ~770-960
    buckets = [
        ("ต่ำกว่า Buffet 350", 0, 379.99, '#94a3b8'),
        ("Buffet 350",          380, 469.99,  '#eab308'),
        ("Buffet 500",          470, 619.99,  '#10b981'),
        ("Buffet 590",          620, 769.99,  '#4f46e5'),
        ("Buffet 799",          770, 959.99,  '#f97316'),
        ("สูงกว่า Buffet 799",  960, float("inf"), '#ef4444'),
    ]
    distribution = []
    for label, lo, hi, color in buckets:
        count = sum(1 for s in sphs if lo <= s <= hi)
        pct = round(count / n * 100, 1) if n else 0
        distribution.append({"bucket": label, "count": count, "pct": pct, "color": color, "lo": lo, "hi": hi if hi != float("inf") else None})

    # By daypart
    daypart_map: dict = {"Lunch 11-14": [], "Dinner 17-22": [], "Other": []}
    for r in rows:
        h = r["hour"]
        if 11 <= h <= 14:
            daypart_map["Lunch 11-14"].append(r["sph"])
        elif 17 <= h <= 22:
            daypart_map["Dinner 17-22"].append(r["sph"])
        else:
            daypart_map["Other"].append(r["sph"])
    by_daypart = [
        {
            "daypart": dp,
            "avg_sph": round(sum(vals) / len(vals), 2) if vals else 0,
            "tx_count": len(vals),
        }
        for dp, vals in daypart_map.items()
    ]

    # Monthly trend — must honour the same period filter as the rest of the endpoint.
    filters2 = ["st.customer_count > 0", "st.source = 'Easy'", "b.name != 'ALL'"]
    params2: dict = {}
    if branch:
        filters2.append("b.name = :branch")
        params2["branch"] = branch
    _month_range_filter(filters2, params2, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    where2 = "WHERE " + " AND ".join(filters2)
    with get_db() as con:
        trend_rows = con.execute(f"""
            SELECT strftime('%Y-%m', st.sold_at) AS period_month,
                   ROUND(AVG(st.total_inc_vat / st.customer_count), 2) AS avg_sph
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where2}
            GROUP BY period_month ORDER BY period_month
        """, params2).fetchall()
    trend = [dict(r) for r in trend_rows]

    # Dominant tier + share above 590
    dom_tier = max(distribution, key=lambda d: d["count"]) if distribution else None
    above_590_count = sum(b["count"] for b in distribution if b["lo"] >= 620)
    below_350_count = sum(b["count"] for b in distribution if (b.get("hi") or float("inf")) < 380)
    return {
        "by_branch": by_branch,
        "distribution": distribution,
        "overall": {
            "avg_sph": round(avg_sph, 2),
            "median_sph": round(median(sphs), 2),
            "p10": round(percentile(sphs, 10), 2),
            "p25": round(percentile(sphs, 25), 2),
            "p75": round(percentile(sphs, 75), 2),
            "p90": round(percentile(sphs, 90), 2),
            "total_tx": n,
            "dominant_tier": dom_tier["bucket"] if dom_tier else None,
            "dominant_pct":  round(dom_tier["count"] / n * 100, 1) if dom_tier and n else 0,
            "pct_above_590": round(above_590_count / n * 100, 1) if n else 0,
            "pct_below_350": round(below_350_count / n * 100, 1) if n else 0,
            "sph_min":      round(min(sphs), 2) if sphs else 0,
            "sph_max":      round(max(sphs), 2) if sphs else 0,
        },
        "by_daypart": by_daypart,
        "trend": trend,
    }


# ---------------------------------------------------------------------------
# /api/analysis/daypart_grid  — Hourly × DOW 2D matrix
# ---------------------------------------------------------------------------
@app.get("/api/analysis/daypart_grid")
@ttl_cache
def analysis_daypart_grid(
    month: Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to: Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
    metric: str = Query("revenue"),  # revenue|tx_count|customer_count|avg_ticket
):
    filters = ["st.source = 'Easy'", "b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT
                CAST(strftime('%w', st.sold_at) AS INTEGER)  AS dow,
                CAST(strftime('%H', st.sold_at) AS INTEGER)  AS hour,
                COUNT(*)                                      AS tx_count,
                COALESCE(SUM(st.customer_count), 0)           AS customer_count,
                ROUND(SUM(st.total_inc_vat) / 1.07, 2)               AS revenue,
                ROUND(AVG(st.total_inc_vat), 2)               AS avg_ticket,
                COUNT(DISTINCT DATE(st.sold_at))              AS day_count
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY dow, hour
        """, params).fetchall()

    cells = []
    max_value = 0.0
    for r in rows:
        if metric == "revenue":
            val = (r["revenue"] or 0) / max(r["day_count"] or 1, 1)
        elif metric == "tx_count":
            val = (r["tx_count"] or 0) / max(r["day_count"] or 1, 1)
        elif metric == "customer_count":
            val = (r["customer_count"] or 0) / max(r["day_count"] or 1, 1)
        else:  # avg_ticket
            val = r["avg_ticket"] or 0
        val = round(val, 2)
        cells.append({"dow": r["dow"], "hour": r["hour"], "value": val})
        if val > max_value:
            max_value = val

    peak = max(cells, key=lambda c: c["value"]) if cells else None

    return {
        "cells": cells,
        "peak": peak,
        "max_value": round(max_value, 2),
        "labels": {
            "dow": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
            "hours": list(range(24)),
        },
    }


# ---------------------------------------------------------------------------
# /api/analysis/branch_peak  — Per-branch peak hour/dow summary for Time & Flow
# ---------------------------------------------------------------------------
@app.get("/api/analysis/branch_peak")
@ttl_cache
def analysis_branch_peak(
    month: Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to: Optional[str] = Query(None),
    metric: str = Query("revenue"),
):
    """Returns per-branch peak hour, peak DOW, and hourly+DOW distribution for Time & Flow tab."""
    filters = ["st.source='Easy'", "b.name != 'ALL'"]
    params: dict = {}
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT
                b.name AS branch,
                CAST(strftime('%H', st.sold_at) AS INTEGER)  AS hour,
                CAST(strftime('%w', st.sold_at) AS INTEGER)  AS dow,
                COUNT(*) AS tx_count,
                COALESCE(SUM(st.customer_count), 0) AS customer_count,
                ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS revenue,
                ROUND(AVG(st.total_inc_vat), 2) AS avg_ticket,
                COUNT(DISTINCT DATE(st.sold_at)) AS day_count
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name, hour, dow
        """, params).fetchall()

    if not rows:
        return {"branches": []}

    DOW_LABELS = ["อา", "จ", "อ", "พ", "พฤ", "ศ", "ส"]

    def get_val(r):
        if metric == "tx_count":      return (r["tx_count"] or 0) / max(r["day_count"] or 1, 1)
        if metric == "customer_count":return (r["customer_count"] or 0) / max(r["day_count"] or 1, 1)
        if metric == "avg_ticket":    return r["avg_ticket"] or 0
        return (r["revenue"] or 0) / max(r["day_count"] or 1, 1)

    # Group by branch
    from collections import defaultdict
    branch_data: dict = defaultdict(list)
    for r in rows:
        branch_data[r["branch"]].append({"hour": r["hour"], "dow": r["dow"], "val": get_val(r)})

    result = []
    for br, cells in branch_data.items():
        # Peak hour (collapse over all DOWs)
        hour_agg: dict = defaultdict(float)
        dow_agg: dict = defaultdict(float)
        for c in cells:
            hour_agg[c["hour"]] += c["val"]
            dow_agg[c["dow"]]   += c["val"]
        peak_hour = max(hour_agg, key=hour_agg.__getitem__) if hour_agg else None
        peak_dow  = max(dow_agg,  key=dow_agg.__getitem__)  if dow_agg  else None

        # Hourly array (sum val across DOWs, 0-23)
        hourly = [round(hour_agg.get(h, 0), 1) for h in range(24)]
        # DOW array (0=Sun..6=Sat)
        dow_arr = [round(dow_agg.get(d, 0), 1) for d in range(7)]
        max_val = max(hourly) if hourly else 1

        result.append({
            "branch": br,
            "peak_hour": peak_hour,
            "peak_dow": peak_dow,
            "peak_dow_label": DOW_LABELS[peak_dow] if peak_dow is not None else None,
            "hourly": hourly,
            "dow": dow_arr,
            "max_val": max_val,
        })

    result.sort(key=lambda x: x["branch"])
    return {"branches": result, "metric": metric}


# ---------------------------------------------------------------------------
# /api/menu/engineering  — Kasavana-Smith matrix
# ---------------------------------------------------------------------------
@app.get("/api/menu/engineering")
@ttl_cache
def menu_engineering(
    month: Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to: Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    include_buffet_drink: bool = Query(False, description="If false (default), exclude BUFFET/drink/dessert/payment so analysis focuses on actual food items"),
):
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="ssm.period_month")
    if category:
        filters.append("COALESCE(cat.name, 'Uncategorised') = :category")
        params["category"] = category
    # Default: exclude non-food categories so menu engineering reflects à la carte food only.
    # Exception: keep ice-cream items (look for ICE / ไอติม / ไอศกรีม / GELATO in description)
    # even if they sit inside a DESSERT category — user wants ice cream in food analysis.
    EXCLUDED_CATS = (
        'BUFFET', 'Oshinei SOFT DRINK', 'Oshinei ALCOHOL',
        'Oshinei DESSERT', 'DESSERT', 'Payment', 'Other',
        'Oshinei DELIVERY', 'Special DELIVERY',
    )
    if not include_buffet_drink and not category:
        ph = ",".join(f":_excl_{i}" for i in range(len(EXCLUDED_CATS)))
        filters.append(
            f"(COALESCE(cat.name,'Uncategorised') NOT IN ({ph}) "
            f" OR UPPER(COALESCE(s.description,'')) LIKE '%ICE CREAM%' "
            f" OR UPPER(COALESCE(s.description,'')) LIKE '%ICECREAM%' "
            f" OR UPPER(COALESCE(s.description,'')) LIKE '%GELATO%' "
            f" OR UPPER(COALESCE(s.description,'')) LIKE '%SOFT SERVE%' "
            f" OR COALESCE(s.description,'') LIKE '%ไอติม%' "
            f" OR COALESCE(s.description,'') LIKE '%ไอศกรีม%' "
            f" OR COALESCE(s.description,'') LIKE '%ไอ-ติม%')"
        )
        for i, c in enumerate(EXCLUDED_CATS):
            params[f"_excl_{i}"] = c
    where = "WHERE " + " AND ".join(filters) if filters else ""

    with get_db() as con:
        rows = con.execute(f"""
            SELECT
                s.sku_code,
                COALESCE(s.description, s.sku_code)          AS description,
                COALESCE(cat.name, 'Uncategorised')           AS category,
                SUM(ssm.qty)                                  AS qty,
                ROUND(SUM(ssm.subtotal_ex_vat), 2)            AS subtotal_ex_vat,
                ROUND(SUM(ssm.subtotal_ex_vat), 2)            AS revenue,
                ROUND(SUM(ssm.total_inc_vat), 2)              AS pos_total_inc_vat_diagnostic,
                SUM({_cogs_case(ssm='ssm', sk='s', sc='sc')}) AS cogs_total,
                SUM(CASE WHEN {_effective_cost_subquery(ssm='ssm', sc='sc')} IS NOT NULL
                         THEN ssm.qty ELSE 0 END)             AS costed_qty
            FROM sales_sku_monthly ssm
            JOIN skus s ON s.sku_id = ssm.sku_id
            JOIN branches b ON b.branch_id = ssm.branch_id
            LEFT JOIN categories cat ON cat.category_id = s.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id = s.sku_id
            {where}
            GROUP BY s.sku_code, s.description, cat.name
        """, params).fetchall()

    if not rows:
        return {"items": [], "median_popularity_pct": 0, "median_margin": 0,
                "total_skus": 0, "costed_skus": 0}

    total_qty = sum(r["qty"] or 0 for r in rows) or 1

    items = []
    for r in rows:
        qty = r["qty"] or 0
        revenue = r["revenue"] or 0
        subtotal_ex = r["subtotal_ex_vat"] or 0
        cogs = r["cogs_total"]
        costed_qty = r["costed_qty"] or 0
        has_cost = cogs is not None and costed_qty > 0
        unit_cost = (cogs / costed_qty) if has_cost else None
        popularity_pct = round(qty / total_qty * 100, 4) if total_qty else 0
        margin_per_unit = round((subtotal_ex - cogs) / qty, 2) if has_cost and qty and cogs is not None else None
        margin_pct = round((subtotal_ex - cogs) / subtotal_ex * 100, 2) if has_cost and subtotal_ex and cogs is not None else None
        items.append({
            "sku_code": r["sku_code"],
            "description": r["description"],
            "category": r["category"],
            "qty": qty,
            "revenue": revenue,
            "margin_per_unit": margin_per_unit,
            "margin_pct": margin_pct,
            "popularity_pct": popularity_pct,
            "quadrant": None,
            "has_cost": has_cost,
        })

    costed = [i for i in items if i["has_cost"] and i["margin_per_unit"] is not None]
    total_skus = len(items)
    costed_skus = len(costed)

    if costed:
        pops_sorted = sorted(i["popularity_pct"] for i in costed)
        margs_sorted = sorted(i["margin_per_unit"] for i in costed)
        n = len(costed)

        def _median(lst):
            m = n // 2
            return (lst[m - 1] + lst[m]) / 2 if n % 2 == 0 else lst[m]

        med_pop = _median(pops_sorted)
        med_marg = _median(margs_sorted)

        for i in items:
            if not i["has_cost"] or i["margin_per_unit"] is None:
                continue
            high_pop = i["popularity_pct"] > med_pop
            high_marg = i["margin_per_unit"] > med_marg
            if high_pop and high_marg:
                i["quadrant"] = "star"
            elif high_pop and not high_marg:
                i["quadrant"] = "plowhorse"
            elif not high_pop and high_marg:
                i["quadrant"] = "puzzle"
            else:
                i["quadrant"] = "dog"
    else:
        med_pop = 0
        med_marg = 0

    return {
        "items": items,
        "median_popularity_pct": round(med_pop, 4),
        "median_margin": round(med_marg, 2),
        "total_skus": total_skus,
        "costed_skus": costed_skus,
    }


@app.get("/api/analysis/food_cost_trend")
@ttl_cache
def food_cost_trend(
    branch: Optional[str] = Query(None),
    group:  Optional[str] = Query(None),
    alert_threshold: float = Query(70.0, description="Food cost % alert threshold (actual Oshinei AYCE buffet 55-70%)"),
):
    """Monthly food cost % trend per branch. Returns months, cost%, alert threshold."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT sm.period_month,
                   b.name AS branch,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)                              AS revenue,
                   ROUND(SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}), 2)         AS cost,
                   ROUND(SUM(sm.qty), 0)                                          AS servings,
                   COUNT(DISTINCT CASE WHEN {_effective_cost_subquery(ssm='sm', sc='sc')} IS NULL
                                       THEN sk.sku_id END)                         AS uncosted
            FROM sales_sku_monthly sm
            JOIN branches b   ON b.branch_id   = sm.branch_id
            JOIN skus sk      ON sk.sku_id      = sm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id  = sm.sku_id
            {where}
            GROUP BY sm.period_month, b.name
            ORDER BY sm.period_month, b.name
            """,
            params,
        ).fetchall()

    # Build per-branch monthly series
    branch_data: dict = {}
    all_months: list = []
    for r in rows:
        mo = r["period_month"]
        if mo not in all_months:
            all_months.append(mo)
        rev  = r["revenue"]  or 0
        cost = r["cost"]     or 0
        srv  = r["servings"] or 0
        pct  = round(cost / rev * 100, 1) if rev > 0 else None
        branch_data.setdefault(r["branch"], {})[mo] = {
            "revenue": rev, "cost": cost, "servings": srv, "cost_pct": pct,
            "uncosted": r["uncosted"] or 0,
        }

    # Overall (network) monthly
    overall: dict = {}
    for mo in all_months:
        tot_rev  = sum(bd.get(mo, {}).get("revenue", 0)  for bd in branch_data.values())
        tot_cost = sum(bd.get(mo, {}).get("cost", 0)     for bd in branch_data.values())
        tot_srv  = sum(bd.get(mo, {}).get("servings", 0) for bd in branch_data.values())
        overall[mo] = {
            "revenue":  tot_rev,
            "cost":     tot_cost,
            "servings": tot_srv,
            "cost_pct": round(tot_cost / tot_rev * 100, 1) if tot_rev > 0 else None,
            "rev_per_serving":  round(tot_rev / tot_srv, 2)  if tot_srv > 0 else None,
            "cost_per_serving": round(tot_cost / tot_srv, 2) if tot_srv > 0 else None,
        }

    # Latest month alert
    alerts = []
    if all_months:
        last_mo = all_months[-1]
        for br, mdata in branch_data.items():
            d = mdata.get(last_mo, {})
            pct = d.get("cost_pct")
            if pct is not None and pct > alert_threshold:
                alerts.append({
                    "branch": br, "period": last_mo,
                    "cost_pct": pct, "threshold": alert_threshold,
                    "message": f"⚠️ {br}: food cost {pct}% exceeds {alert_threshold}% threshold in {last_mo}",
                })

    return {
        "months": all_months,
        "overall": [{"period_month": mo, **v} for mo, v in overall.items()],
        "by_branch": {br: [{"period_month": mo, **mdata.get(mo, {"cost_pct": None})}
                            for mo in all_months]
                      for br, mdata in branch_data.items()},
        "alert_threshold": alert_threshold,
        "alerts": alerts,
    }


@app.get("/api/analysis/buffet_mix")
@ttl_cache
def buffet_mix(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
):
    """
    Buffet price-tier mix from SKU sales.
    Detects tiers by matching '350','500','590','799' in category or sku_code or description.
    Also flags a la carte (อลาคาท / Ala Carte) separately.
    Returns tier revenue share, qty share, and trend.
    """
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT sm.period_month,
                   COALESCE(cat.name, '') AS category,
                   sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   ROUND(SUM(sm.qty), 2)                 AS qty,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)       AS revenue
            FROM sales_sku_monthly sm
            JOIN branches b   ON b.branch_id  = sm.branch_id
            JOIN skus sk      ON sk.sku_id     = sm.sku_id
            LEFT JOIN categories cat ON cat.category_id = sk.category_id
            {where}
            GROUP BY sm.period_month, cat.name, sk.sku_code, sk.description
            ORDER BY sm.period_month
            """,
            params,
        ).fetchall()

    TIERS = ['350', '500', '590', '799']
    ALACARTE_KEYWORDS = ['อลาคาท', 'ala carte', 'a la carte', 'alacarte', 'อา ลา คาร์ท']

    def detect_tier(cat, sku, desc):
        combined = f"{cat} {sku} {desc}".lower()
        for kw in ALACARTE_KEYWORDS:
            if kw.lower() in combined:
                return 'à la carte'
        for t in TIERS:
            if t in combined:
                return t
        if 'บุฟเฟ่ต์' in combined or 'buffet' in combined:
            return 'buffet (unknown tier)'
        return 'other'

    # Aggregate
    totals: dict = {}   # tier -> {qty, revenue}
    monthly: dict = {}  # period_month -> tier -> revenue

    for r in rows:
        tier = detect_tier(r["category"], r["sku_code"], r["description"])
        t = totals.setdefault(tier, {"qty": 0.0, "revenue": 0.0})
        t["qty"]     += r["qty"]     or 0
        t["revenue"] += r["revenue"] or 0
        mo = monthly.setdefault(r["period_month"], {})
        mo[tier] = mo.get(tier, 0.0) + (r["revenue"] or 0)

    total_rev = sum(v["revenue"] for v in totals.values()) or 1
    total_qty = sum(v["qty"]     for v in totals.values()) or 1

    summary = [
        {
            "tier": tier,
            "qty": round(vals["qty"], 1),
            "revenue": round(vals["revenue"], 2),
            "revenue_pct": round(vals["revenue"] / total_rev * 100, 1),
            "qty_pct":     round(vals["qty"]     / total_qty * 100, 1),
        }
        for tier, vals in sorted(totals.items(),
                                  key=lambda x: -x[1]["revenue"])
    ]

    all_months = sorted(monthly.keys())
    all_tiers  = list(totals.keys())
    trend = [
        {"period_month": mo,
         **{t: round(monthly[mo].get(t, 0), 2) for t in all_tiers}}
        for mo in all_months
    ]

    return {"summary": summary, "trend": trend, "tiers": all_tiers, "months": all_months}


@app.get("/api/analysis/table_turnover")
@ttl_cache
def table_turnover(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
):
    """
    Estimate table turnover and dwell time from sales_transaction.
    - turns_per_day: avg number of bills per table per operating day
    - est_dwell_min: median gap (minutes) between consecutive bills at the same table on the same day
    - peak_hour distribution of first bills
    """
    filters = ["b.name != 'ALL'", "TRIM(COALESCE(st.table_no,'')) != ''", "st.source = 'Easy'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        # Bills per table per day per branch
        day_rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   st.table_no,
                   DATE(st.sold_at)                     AS day,
                   COUNT(*)                              AS bill_count,
                   MIN(st.sold_at)                       AS first_bill,
                   MAX(st.sold_at)                       AS last_bill,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2)      AS day_revenue,
                   SUM(st.customer_count)               AS day_pax
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name, st.table_no, DATE(st.sold_at)
            ORDER BY b.name, day
            """,
            params,
        ).fetchall()

    if not day_rows:
        return {"available": False}

    from datetime import datetime as _dt

    # Per-branch aggregations
    branch_stats: dict = {}
    all_gaps: list = []

    for r in day_rows:
        br = r["branch"]
        bs = branch_stats.setdefault(br, {
            "total_table_days": 0, "total_bills": 0,
            "total_revenue": 0.0, "total_pax": 0,
            "gaps_min": [], "turns_list": [],
        })
        bs["total_table_days"] += 1
        bs["total_bills"]      += r["bill_count"]
        bs["total_revenue"]    += r["day_revenue"] or 0
        bs["total_pax"]        += r["day_pax"] or 0
        bs["turns_list"].append(r["bill_count"])

        # Gap estimate: if >=2 bills, (last_bill - first_bill) / (bills - 1) = avg gap
        if r["bill_count"] >= 2:
            try:
                t1 = _dt.fromisoformat(r["first_bill"])
                t2 = _dt.fromisoformat(r["last_bill"])
                span_min = (t2 - t1).total_seconds() / 60
                avg_gap  = span_min / (r["bill_count"] - 1)
                if 0 < avg_gap < 600:  # ignore gaps > 10 hours (data noise)
                    bs["gaps_min"].append(avg_gap)
                    all_gaps.append(avg_gap)
            except Exception:
                pass

    def _median(lst):
        if not lst: return None
        s = sorted(lst)
        n = len(s)
        return round(s[n // 2] if n % 2 else (s[n//2-1]+s[n//2])/2, 1)

    result_branches = []
    for br, bs in sorted(branch_stats.items(), key=lambda x: -x[1]["total_revenue"]):
        tds  = bs["total_table_days"] or 1
        result_branches.append({
            "branch":            br,
            "avg_turns_per_day": round(bs["total_bills"] / tds, 2),
            "est_dwell_min":     _median(bs["gaps_min"]),
            "avg_revenue_per_table_day": round(bs["total_revenue"] / tds, 2),
            "avg_pax_per_table_day":     round(bs["total_pax"] / tds, 2),
        })

    overall_turns  = round(sum(r["avg_turns_per_day"] for r in result_branches) / max(len(result_branches), 1), 2)
    overall_dwell  = _median(all_gaps)

    return {
        "available":     True,
        "overall": {
            "avg_turns_per_day": overall_turns,
            "est_dwell_min":     overall_dwell,
        },
        "by_branch": result_branches,
    }


# ---------------------------------------------------------------------------
# GET /api/buffet/gp
# Gross Profit from BoM — revenue vs actual item-level cost for the period
# ---------------------------------------------------------------------------
@app.get("/api/buffet/gp")
@ttl_cache
def buffet_gp(
    branch:      Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    granularity: str           = Query("month"),
):
    """Buffet Gross Profit from BoM cost.

    Revenue = sum(subtotal_ex_vat) from sales_sku_monthly.
    BoM Cost = sum(qty * unit_cost) for SKUs that have cost data.
    For week/day granularity, BoM cost is pro-rated by daily revenue fraction
    (since SKU data is monthly-only — no day-level SKU breakdown available).

    Returns: period_total, series (bucketed), by_branch, top_cost_skus, waterfall.
    """
    if granularity not in ("day", "week", "month"):
        granularity = "month"

    DRINK_CATS = ("Oshinei ALCOHOL", "Oshinei DRINKING", "Oshinei SOFT DRINK",
                  "(YAKI)SOFT DRINK")

    # ── Build SKU-level filters (period_month column) ─────────────────────
    sku_filters: list = ["b.name != 'ALL'"]
    sku_params:  dict = {}
    if branch:
        sku_filters.append("b.name = :branch")
        sku_params["branch"] = branch
    _month_range_filter(sku_filters, sku_params, month_from, month_to, None,
                        col="sm.period_month")
    where_sku = "WHERE " + " AND ".join(sku_filters)

    # ── Build transaction-level filters ───────────────────────────────────
    tx_filters: list = ["b.name != 'ALL'", "st.source = 'Easy'"]
    tx_params:  dict = {}
    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch
    _month_range_filter(tx_filters, tx_params, month_from, month_to, None,
                        col="SUBSTR(st.sold_at,1,7)")
    where_tx = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        # Source-aware BoM cost rule (Phase 0 fix):
        #   Easy source: exclude tier SKUs (BUFFET350/500/590/799/CHILD) — their
        #     cost is captured at item level (B0xxx rows logged with qty>0,
        #     revenue=0). Including tier unit_cost would double-count.
        #   CM / CM POS source: include ALL SKUs — only tier rows exist there
        #     (B01/B02/B07/B11), and tier unit_cost is the only cost data we have.
        bom_cost_sql = _cogs_case(ssm="sm")
        # Uncovered = item SKU without cost (Easy tier SKUs are intentionally
        # treated as "covered via items" so they don't count as uncovered).
        uncovered_sql = (
            f"CASE WHEN sm.source = 'Easy' AND sk.sku_code IN {_EASY_TIER_CODES_SQL} THEN NULL "
            f"     WHEN sc.unit_cost IS NULL THEN sm.sku_id ELSE NULL END"
        )

        # ── 1. Monthly BoM cost by (branch, period_month, category_group) ─
        monthly_bom_rows = con.execute(f"""
            SELECT
                b.name           AS branch,
                sm.period_month  AS m,
                c.name           AS cat,
                sk.sku_code      AS sku_code,
                SUM(sm.qty)      AS qty,
                SUM(sm.subtotal_ex_vat) AS rev,
                SUM({bom_cost_sql}) AS bom_cost,
                COUNT(DISTINCT {uncovered_sql}) AS uncovered
            FROM sales_sku_monthly sm
            JOIN branches   b  ON b.branch_id   = sm.branch_id
            JOIN skus       sk ON sk.sku_id      = sm.sku_id
            JOIN categories c  ON c.category_id  = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id   = sm.sku_id
            {where_sku}
            GROUP BY b.name, sm.period_month, c.name, sk.sku_code
            ORDER BY b.name, sm.period_month
        """, sku_params).fetchall()

        # ── 2. Daily revenue from transactions (for day/week pro-rate) ────
        daily_tx_rows = con.execute(f"""
            SELECT
                b.name                           AS branch,
                DATE(st.sold_at)                 AS d,
                SUBSTR(st.sold_at, 1, 7)         AS m,
                SUM(st.customer_count)           AS covers,
                SUM(COALESCE(st.food_subtotal,0)
                  + COALESCE(st.drink_subtotal,0)
                  + COALESCE(st.other_subtotal,0)) AS rev_tx
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
            GROUP BY b.name, DATE(st.sold_at)
        """, tx_params).fetchall()

    # ── 3. Aggregate monthly totals (branch × month) ──────────────────────
    from collections import defaultdict

    # monthly_agg[branch][month] = {rev, bom_cost, bom_buffet, bom_food, bom_bev, rev_buffet, rev_food, rev_bev, uncovered, sku details}
    monthly_agg: dict = defaultdict(lambda: defaultdict(lambda: {
        "rev": 0.0, "bom_cost": 0.0,
        "bom_buffet": 0.0, "bom_food": 0.0, "bom_bev": 0.0,
        "rev_buffet": 0.0, "rev_food": 0.0, "rev_bev": 0.0,
        "uncovered": 0,
    }))
    # sku-level detail for top_cost_skus
    sku_agg: dict = defaultdict(lambda: {
        "description": "", "category": "", "qty": 0.0,
        "bom_cost": 0.0, "rev": 0.0,
    })

    for r in monthly_bom_rows:
        br, m, cat = r["branch"], r["m"], r["cat"]
        a = monthly_agg[br][m]
        a["rev"]      += r["rev"] or 0.0
        a["bom_cost"] += r["bom_cost"] or 0.0
        a["uncovered"] = max(a["uncovered"], r["uncovered"] or 0)

        is_buffet_tier = r["sku_code"] and r["sku_code"].startswith("BUFFET")
        is_drink = cat in DRINK_CATS

        if is_buffet_tier:
            a["bom_buffet"] += r["bom_cost"] or 0.0
            a["rev_buffet"] += r["rev"] or 0.0
        elif is_drink:
            a["bom_bev"] += r["bom_cost"] or 0.0
            a["rev_bev"] += r["rev"] or 0.0
        else:
            a["bom_food"] += r["bom_cost"] or 0.0
            a["rev_food"] += r["rev"] or 0.0

        sc = sku_agg[r["sku_code"]]
        sc["description"] = r["sku_code"]  # placeholder; description not in this query
        sc["category"]    = cat
        sc["qty"]         += r["qty"] or 0.0
        sc["bom_cost"]    += r["bom_cost"] or 0.0
        sc["rev"]         += r["rev"] or 0.0

    # ── 4. daily_tx index: [branch][month][date] ──────────────────────────
    daily_tx: dict = defaultdict(lambda: defaultdict(dict))
    monthly_rev_tx: dict = defaultdict(lambda: defaultdict(float))
    monthly_covers_tx: dict = defaultdict(lambda: defaultdict(int))

    for r in daily_tx_rows:
        daily_tx[r["branch"]][r["m"]][r["d"]] = r["rev_tx"] or 0.0
        monthly_rev_tx[r["branch"]][r["m"]]  += r["rev_tx"] or 0.0
        monthly_covers_tx[r["branch"]][r["m"]] += r["covers"] or 0

    # ── 5. Build series ───────────────────────────────────────────────────
    def week_bucket(date_str: str) -> str:
        from datetime import date as dt
        d = dt.fromisoformat(date_str)
        iso = d.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    if granularity == "month":
        bucket_agg: dict = defaultdict(lambda: {"rev": 0.0, "bom_cost": 0.0, "covers": 0})
        for br in monthly_agg:
            for m, a in monthly_agg[br].items():
                b = bucket_agg[m]
                b["rev"]      += a["rev"]
                b["bom_cost"] += a["bom_cost"]
                b["covers"]   += monthly_covers_tx[br][m]
        series = []
        for bucket in sorted(bucket_agg.keys()):
            b = bucket_agg[bucket]
            gp = round(b["rev"] - b["bom_cost"], 2)
            series.append({
                "bucket":   bucket,
                "revenue":  round(b["rev"], 2),
                "bom_cost": round(b["bom_cost"], 2),
                "gp":       gp,
                "margin_pct": round(gp / b["rev"] * 100, 2) if b["rev"] else None,
                "covers":   b["covers"],
            })
    else:
        # Day or week: pro-rate monthly BoM by daily revenue fraction
        daily_bucket: dict = defaultdict(lambda: {"rev": 0.0, "bom_cost": 0.0, "covers": 0})
        for br, months in daily_tx.items():
            for m, days in months.items():
                m_rev_tx = monthly_rev_tx[br][m]
                m_bom    = sum(monthly_agg[br][mm]["bom_cost"] for mm in monthly_agg[br] if mm == m)
                for d, d_rev_tx in days.items():
                    fraction   = (d_rev_tx / m_rev_tx) if m_rev_tx else 0.0
                    d_bom_cost = m_bom * fraction
                    # revenue for series comes from transactions
                    bucket = d if granularity == "day" else week_bucket(d)
                    db = daily_bucket[bucket]
                    db["rev"]      += d_rev_tx
                    db["bom_cost"] += d_bom_cost
                    # covers
                    for r2 in daily_tx_rows:
                        if r2["branch"] == br and r2["d"] == d:
                            db["covers"] += r2["covers"] or 0
                            break
        series = []
        for bucket in sorted(daily_bucket.keys()):
            b = daily_bucket[bucket]
            gp = round(b["rev"] - b["bom_cost"], 2)
            series.append({
                "bucket":     bucket,
                "revenue":    round(b["rev"], 2),
                "bom_cost":   round(b["bom_cost"], 2),
                "gp":         gp,
                "margin_pct": round(gp / b["rev"] * 100, 2) if b["rev"] else None,
                "covers":     b["covers"],
            })

    # ── 6. Period totals (all branches, whole selected period) ───────────
    period_rev      = sum(s["revenue"]  for s in series)
    period_bom      = sum(s["bom_cost"] for s in series)
    period_covers   = sum(s["covers"]   for s in series) if granularity == "month" else \
                      sum(monthly_covers_tx[br][m] for br in monthly_covers_tx for m in monthly_covers_tx[br])
    period_gp       = round(period_rev - period_bom, 2)
    period_margin   = round(period_gp / period_rev * 100, 2) if period_rev else None
    cost_per_cover  = round(period_bom / period_covers, 2) if period_covers else None

    # ── 7. By branch (whole period) ───────────────────────────────────────
    branch_totals: dict = defaultdict(lambda: {
        "rev": 0.0, "bom_cost": 0.0, "covers": 0
    })
    for br in monthly_agg:
        for m, a in monthly_agg[br].items():
            bt = branch_totals[br]
            bt["rev"]      += a["rev"]
            bt["bom_cost"] += a["bom_cost"]
            bt["covers"]   += monthly_covers_tx[br][m]

    network_avg_margin = period_margin or 0.0
    by_branch = []
    for br, bt in sorted(branch_totals.items(), key=lambda x: x[1]["rev"], reverse=True):
        gp_br   = round(bt["rev"] - bt["bom_cost"], 2)
        margin  = round(gp_br / bt["rev"] * 100, 2) if bt["rev"] else None
        gp_cov  = round(gp_br / bt["covers"], 2) if bt["covers"] else None
        diff_pp = round(margin - network_avg_margin, 2) if margin is not None else None
        by_branch.append({
            "branch":       br,
            "covers":       bt["covers"],
            "revenue":      round(bt["rev"], 2),
            "bom_cost":     round(bt["bom_cost"], 2),
            "gp":           gp_br,
            "gp_per_cover": gp_cov,
            "margin_pct":   margin,
            "vs_network_pp": diff_pp,
        })

    # ── 8. Top 20 cost-driver SKUs (exclude tier seats) ──────────────────
    total_bom = period_bom or 1.0
    top_cost_skus = sorted(
        [{"sku_code": k, **v} for k, v in sku_agg.items()
         if v["bom_cost"] > 0 and k not in _ALL_TIER_CODES and not k.startswith("BUFFET")],
        key=lambda x: x["bom_cost"], reverse=True
    )[:20]
    for s in top_cost_skus:
        s["bom_cost"]  = round(s["bom_cost"], 2)
        s["rev"]       = round(s["rev"], 2)
        s["qty"]       = round(s["qty"], 2)
        s["cost_pct"]  = round(s["bom_cost"] / total_bom * 100, 2)

    # ── 9. Waterfall (whole period, all branches) ─────────────────────────
    total_rev_buffet = sum(monthly_agg[br][m]["rev_buffet"]  for br in monthly_agg for m in monthly_agg[br])
    total_rev_food   = sum(monthly_agg[br][m]["rev_food"]    for br in monthly_agg for m in monthly_agg[br])
    total_rev_bev    = sum(monthly_agg[br][m]["rev_bev"]     for br in monthly_agg for m in monthly_agg[br])
    total_bom_buffet = sum(monthly_agg[br][m]["bom_buffet"]  for br in monthly_agg for m in monthly_agg[br])
    total_bom_food   = sum(monthly_agg[br][m]["bom_food"]    for br in monthly_agg for m in monthly_agg[br])
    total_bom_bev    = sum(monthly_agg[br][m]["bom_bev"]     for br in monthly_agg for m in monthly_agg[br])

    waterfall = {
        "revenue":       round(period_rev, 2),
        "rev_buffet":    round(total_rev_buffet, 2),
        "rev_food":      round(total_rev_food, 2),
        "rev_bev":       round(total_rev_bev, 2),
        "bom_buffet":    round(total_bom_buffet, 2),
        "bom_food":      round(total_bom_food, 2),
        "bom_bev":       round(total_bom_bev, 2),
        "bom_total":     round(period_bom, 2),
        "gp":            round(period_gp, 2),
    }

    return {
        "period_total": {
            "revenue":       round(period_rev, 2),
            "bom_cost":      round(period_bom, 2),
            "gp":            period_gp,
            "margin_pct":    period_margin,
            "covers":        period_covers,
            "cost_per_cover": cost_per_cover,
        },
        "granularity": granularity,
        "series":       series,
        "by_branch":    by_branch,
        "top_cost_skus": top_cost_skus,
        "waterfall":    waterfall,
    }


# ---------------------------------------------------------------------------
# GET /api/cost/audit   🔴1
# Flag SKUs where unit_cost looks suspicious vs actual selling price
# ---------------------------------------------------------------------------
@app.get("/api/cost/audit")
@ttl_cache
def cost_audit():
    """Flag SKUs whose unit_cost appears suspicious.

    A SKU is flagged when:
    - unit_cost > implied_selling_price  (ratio > 1.0)  ← definitely wrong
    - unit_cost > 0.9 × implied_selling_price           ← likely wrong (warning)
    - unit_cost is very high relative to peers (z-score > 2)

    implied_selling_price = total_revenue / total_qty  (avg selling price per unit).
    For buffet food items (subtotal_ex_vat ~ 0) implied_price will be near zero,
    so we skip ratio check for those and rely on z-score only.
    """
    TIER_CODES_SET = {'BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD',
                      'B01','B02','B07','B11'}
    with get_db() as con:
        rows = con.execute("""
            SELECT sk.sku_id, sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   c.name AS category,
                   sc.unit_cost,
                   ROUND(SUM(ssm.subtotal_ex_vat), 2) AS total_revenue,
                   ROUND(SUM(ssm.qty), 2)            AS total_qty
            FROM sku_cost sc
            JOIN skus sk      ON sk.sku_id      = sc.sku_id
            JOIN categories c ON c.category_id  = sk.category_id
            LEFT JOIN sales_sku_monthly ssm ON ssm.sku_id = sk.sku_id
            WHERE sc.unit_cost IS NOT NULL AND sc.unit_cost > 0
            GROUP BY sk.sku_id
        """).fetchall()

    items = []
    costs = []
    for r in rows:
        uc   = r["unit_cost"]
        qty  = r["total_qty"] or 0
        rev  = r["total_revenue"] or 0
        implied = round(rev / qty, 4) if qty > 0 else None
        ratio   = round(uc / implied, 3) if implied and implied > 0 else None
        costs.append(uc)
        items.append({
            "sku_id":              r["sku_id"],
            "sku_code":            r["sku_code"],
            "description":         r["description"],
            "category":            r["category"],
            "unit_cost":           uc,
            "total_revenue":       rev,
            "total_qty":           qty,
            "implied_selling_price": implied,
            "ratio":               ratio,
            "is_tier":             r["sku_code"] in TIER_CODES_SET,
        })

    # z-score across all unit costs
    n = len(costs)
    mean_uc = sum(costs) / n if n else 0
    var_uc  = sum((c - mean_uc) ** 2 for c in costs) / n if n else 0
    sd_uc   = var_uc ** 0.5

    result = []
    for item in items:
        uc = item["unit_cost"]
        z  = round((uc - mean_uc) / sd_uc, 2) if sd_uc > 0 else 0
        ratio = item["ratio"]

        if item["is_tier"]:
            flag = "tier"
        elif ratio is not None and ratio > 1.0:
            flag = "error"       # cost exceeds selling price per unit
        elif ratio is not None and ratio > 0.9:
            flag = "warning"     # cost ≥ 90% of selling price
        elif z > 2.5:
            flag = "high_z"      # statistical outlier among all SKU costs
        else:
            flag = "ok"

        item["z_score"] = z
        item["flag"]    = flag
        result.append(item)

    # Sort: error first, then warning, then high_z, then ok
    order = {"error": 0, "warning": 1, "high_z": 2, "tier": 3, "ok": 4}
    result.sort(key=lambda x: (order.get(x["flag"], 9), -(x["ratio"] or 0)))

    flagged   = [x for x in result if x["flag"] in ("error", "warning", "high_z")]
    summary   = {
        "total_with_cost": n,
        "error_count":   sum(1 for x in result if x["flag"] == "error"),
        "warning_count": sum(1 for x in result if x["flag"] == "warning"),
        "high_z_count":  sum(1 for x in result if x["flag"] == "high_z"),
        "tier_count":    sum(1 for x in result if x["flag"] == "tier"),
    }
    return {"summary": summary, "flagged": flagged, "all": result}


# ---------------------------------------------------------------------------
# GET /api/buffet/portion-velocity   🟡3
# qty / cover per SKU per month — detect over-ordering trends
# ---------------------------------------------------------------------------
@app.get("/api/buffet/portion-velocity")
@ttl_cache
def buffet_portion_velocity(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    top_n:      int           = Query(50, ge=5, le=100),
):
    """Per-SKU qty/cover trend. Covers from transactions; qty from SKU monthly."""
    TIER_CODES = ('BUFFET','BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD',
                  'B01','B02','B07','B11')
    tier_named = {f"tc{i}": v for i, v in enumerate(TIER_CODES)}
    tier_ph    = ", ".join(f":tc{i}" for i in range(len(TIER_CODES)))

    sku_filters: list = ["b.name != 'ALL'", f"sk.sku_code NOT IN ({tier_ph})"]
    sku_params:  dict = {**tier_named}
    if branch:
        sku_filters.append("b.name = :branch")
        sku_params["branch"] = branch
    _month_range_filter(sku_filters, sku_params, month_from, month_to, None,
                        col="sm.period_month")
    where_sku = "WHERE " + " AND ".join(sku_filters)

    tx_filters: list = ["b.name != 'ALL'", "st.source='Easy'"]
    tx_params:  dict = {}
    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch
    _month_range_filter(tx_filters, tx_params, month_from, month_to, None,
                        col="SUBSTR(st.sold_at,1,7)")
    where_tx = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        sku_rows = con.execute(f"""
            SELECT sm.period_month AS m,
                   sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
                   c.name AS category,
                   SUM(sm.qty) AS qty
            FROM sales_sku_monthly sm
            JOIN branches   b  ON b.branch_id   = sm.branch_id
            JOIN skus       sk ON sk.sku_id      = sm.sku_id
            JOIN categories c  ON c.category_id  = sk.category_id
            {where_sku}
            GROUP BY sm.period_month, sk.sku_code
            ORDER BY sm.period_month, qty DESC
        """, sku_params).fetchall()

        tx_rows = con.execute(f"""
            SELECT SUBSTR(st.sold_at,1,7) AS m,
                   SUM(st.customer_count) AS covers
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
              AND st.customer_count > 0
            GROUP BY SUBSTR(st.sold_at,1,7)
        """, tx_params).fetchall()

    covers_by_month = {r["m"]: (r["covers"] or 1) for r in tx_rows}
    months_sorted   = sorted(covers_by_month.keys())

    from collections import defaultdict
    sku_monthly: dict = defaultdict(lambda: defaultdict(float))
    sku_meta: dict = {}
    for r in sku_rows:
        sku_monthly[r["sku_code"]][r["m"]] = r["qty"] or 0
        sku_meta[r["sku_code"]] = {"description": r["description"], "category": r["category"]}

    # avg qty/cover across all months
    sku_avg: dict = {}
    for code, monthly in sku_monthly.items():
        ratios = [(monthly[m] / covers_by_month[m]) for m in monthly if m in covers_by_month and covers_by_month[m]]
        sku_avg[code] = sum(ratios) / len(ratios) if ratios else 0

    top_skus = sorted(sku_avg.keys(), key=lambda x: -sku_avg[x])[:top_n]

    result = []
    for code in top_skus:
        monthly = sku_monthly[code]
        series  = []
        for m in months_sorted:
            covers = covers_by_month.get(m, 0)
            qty    = monthly.get(m, 0)
            series.append({
                "month":    m,
                "qty":      round(qty, 1),
                "covers":   covers,
                "ratio":    round(qty / covers, 4) if covers else 0,
            })
        # trend: slope of last 3 months vs first 3 months
        vals = [s["ratio"] for s in series if s["ratio"] > 0]
        if len(vals) >= 4:
            mid     = len(vals) // 2
            avg_old = sum(vals[:mid]) / mid
            avg_new = sum(vals[mid:]) / (len(vals) - mid)
            trend_pct = round((avg_new - avg_old) / avg_old * 100, 1) if avg_old else 0
        else:
            trend_pct = 0

        result.append({
            "sku_code":    code,
            "description": sku_meta[code]["description"],
            "category":    sku_meta[code]["category"],
            "avg_ratio":   round(sku_avg[code], 4),
            "trend_pct":   trend_pct,
            "series":      series,
        })

    return {"months": months_sorted, "skus": result}


# ---------------------------------------------------------------------------
# GET /api/buffet/plate-cost-dist   🟡4
# Pro-rate monthly BoM cost to per-bill, return distribution
# ---------------------------------------------------------------------------
@app.get("/api/buffet/plate-cost-dist")
@ttl_cache
def buffet_plate_cost_dist(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Estimate cost per bill by pro-rating monthly BoM cost by daily bill revenue."""
    TIER_CODES = ('BUFFET','BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD',
                  'B01','B02','B07','B11')
    tier_named = {f"tc{i}": v for i, v in enumerate(TIER_CODES)}
    tier_ph    = ", ".join(f":tc{i}" for i in range(len(TIER_CODES)))

    sku_filters: list = ["b.name != 'ALL'"]
    sku_params: dict  = {**tier_named}
    if branch:
        sku_filters.append("b.name = :branch")
        sku_params["branch"] = branch
    _month_range_filter(sku_filters, sku_params, month_from, month_to, None, col="sm.period_month")
    where_sku = "WHERE " + " AND ".join(sku_filters)

    tx_filters: list = ["b.name != 'ALL'", "st.source='Easy'", "st.customer_count > 0"]
    tx_params: dict  = {}
    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch
    _month_range_filter(tx_filters, tx_params, month_from, month_to, None,
                        col="SUBSTR(st.sold_at,1,7)")
    where_tx = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        # Monthly BoM cost per branch
        bom_rows = con.execute(f"""
            SELECT b.name AS branch, sm.period_month AS m,
                   SUM(CASE WHEN sk.sku_code NOT IN ({tier_ph})
                            THEN {_cogs_case(ssm='sm', sk='sk', sc='sc')} ELSE 0 END) AS bom_cost
            FROM sales_sku_monthly sm
            JOIN branches   b  ON b.branch_id  = sm.branch_id
            JOIN skus       sk ON sk.sku_id     = sm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id  = sm.sku_id
            {where_sku}
            GROUP BY b.name, sm.period_month
        """, sku_params).fetchall()

        # Per-bill data
        bill_rows = con.execute(f"""
            SELECT b.name AS branch,
                   SUBSTR(st.sold_at,1,7) AS m,
                   st.receipt_no,
                   st.customer_count,
                   COALESCE(st.food_subtotal,0)+COALESCE(st.drink_subtotal,0)+
                   COALESCE(st.other_subtotal,0) AS bill_rev
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
        """, tx_params).fetchall()

        # Monthly revenue per branch from transactions
        month_rev_rows = con.execute(f"""
            SELECT b.name AS branch, SUBSTR(st.sold_at,1,7) AS m,
                   SUM(COALESCE(st.food_subtotal,0)+COALESCE(st.drink_subtotal,0)+
                       COALESCE(st.other_subtotal,0)) AS monthly_rev
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
            GROUP BY b.name, SUBSTR(st.sold_at,1,7)
        """, tx_params).fetchall()

    from collections import defaultdict
    bom_map   = {(r["branch"], r["m"]): r["bom_cost"] or 0 for r in bom_rows}
    mrev_map  = {(r["branch"], r["m"]): r["monthly_rev"] or 1 for r in month_rev_rows}

    bill_costs = []
    for r in bill_rows:
        key     = (r["branch"], r["m"])
        bom     = bom_map.get(key, 0)
        mrev    = mrev_map.get(key, 1)
        covers  = r["customer_count"] or 1
        bill_rv = r["bill_rev"] or 0
        fraction = bill_rv / mrev if mrev > 0 else 0
        cost_per_cover = (bom * fraction) / covers if covers else 0
        bill_costs.append(cost_per_cover)

    if not bill_costs:
        return {"distribution": [], "percentiles": {}, "mean": 0, "count": 0}

    bill_costs.sort()
    n = len(bill_costs)

    def pct(lst, p):
        idx = max(0, min(len(lst) - 1, int(p / 100 * len(lst))))
        return round(lst[idx], 2)

    buckets = [
        ("<฿100",    0,   100),
        ("฿100-200", 100, 200),
        ("฿200-300", 200, 300),
        ("฿300-400", 300, 400),
        ("฿400-500", 400, 500),
        ("฿500-600", 500, 600),
        ("฿600+",    600, float("inf")),
    ]
    distribution = []
    for label, lo, hi in buckets:
        count = sum(1 for c in bill_costs if lo <= c < hi)
        distribution.append({
            "label": label, "count": count,
            "pct": round(count / n * 100, 1),
            "lo": lo, "hi": hi if hi != float("inf") else None,
        })

    return {
        "distribution": distribution,
        "percentiles": {
            "p25": pct(bill_costs, 25),
            "p50": pct(bill_costs, 50),
            "p75": pct(bill_costs, 75),
            "p90": pct(bill_costs, 90),
            "p95": pct(bill_costs, 95),
        },
        "mean":  round(sum(bill_costs) / n, 2),
        "count": n,
    }


# ---------------------------------------------------------------------------
# GET /api/buffet/cost-sensitivity   🟡5
# Top cost-driver SKUs for JS-side simulation
# ---------------------------------------------------------------------------
@app.get("/api/buffet/cost-sensitivity")
@ttl_cache
def buffet_cost_sensitivity(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    top_n:      int           = Query(50, ge=5, le=100),
):
    """Returns top SKUs by BoM cost with enough data for client-side sensitivity simulation."""
    TIER_CODES = ('BUFFET','BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD',
                  'B01','B02','B07','B11')
    tier_named = {f"tc{i}": v for i, v in enumerate(TIER_CODES)}
    tier_ph    = ", ".join(f":tc{i}" for i in range(len(TIER_CODES)))

    filters: list = ["b.name != 'ALL'", f"sk.sku_code NOT IN ({tier_ph})"]
    params: dict  = {**tier_named}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, None, col="sm.period_month")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   c.name AS category,
                   sc.unit_cost,
                   SUM(sm.qty)                                            AS total_qty,
                   SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')})          AS total_bom_cost,
                   SUM(sm.subtotal_ex_vat)                                AS total_rev
            FROM sales_sku_monthly sm
            JOIN branches   b  ON b.branch_id  = sm.branch_id
            JOIN skus       sk ON sk.sku_id     = sm.sku_id
            JOIN categories c  ON c.category_id = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id  = sm.sku_id
            {where}
              AND {_effective_cost_subquery(ssm='sm', sc='sc')} IS NOT NULL
              AND {_effective_cost_subquery(ssm='sm', sc='sc')} > 0
            GROUP BY sk.sku_code, sc.unit_cost
            ORDER BY total_bom_cost DESC
            LIMIT :top_n
        """, {**params, "top_n": top_n}).fetchall()

        # Total revenue for the period (to compute margin impact)
        rev_row = con.execute(f"""
            SELECT SUM(sm.subtotal_ex_vat) AS total_rev
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            JOIN skus sk ON sk.sku_id = sm.sku_id
            {where}
        """, params).fetchone()

    total_rev = (rev_row["total_rev"] or 0) if rev_row else 0
    total_bom = sum(r["total_bom_cost"] or 0 for r in rows)

    skus = [{
        "sku_code":      r["sku_code"],
        "description":   r["description"],
        "category":      r["category"],
        "unit_cost":     r["unit_cost"],
        "total_qty":     round(r["total_qty"] or 0, 1),
        "total_bom_cost": round(r["total_bom_cost"] or 0, 2),
        "total_rev":     round(r["total_rev"] or 0, 2),
        "cost_pct":      round((r["total_bom_cost"] or 0) / total_bom * 100, 2) if total_bom else 0,
    } for r in rows]

    return {
        "skus":          skus,
        "total_revenue": round(total_rev, 2),
        "total_bom":     round(total_bom, 2),
        "base_gp":       round(total_rev - total_bom, 2),
    }


# ---------------------------------------------------------------------------
# GET + POST /api/branches/fixed-costs   🟡6
# Per-branch monthly fixed costs (stored in app_settings as JSON)
# ---------------------------------------------------------------------------
import json as _json

@app.get("/api/branches/fixed-costs")
def get_branch_fixed_costs():
    """Return {branch_name: monthly_fixed_cost} from app_settings."""
    with get_db() as con:
        row = con.execute(
            "SELECT value FROM app_settings WHERE key='branch_fixed_costs'"
        ).fetchone()
    if row:
        try:
            return _json.loads(row["value"])
        except Exception:
            return {}
    return {}


class BranchFixedCostBody(BaseModel):
    costs: Dict[str, float]   # {branch_name: monthly_fixed_cost}


@app.post("/api/branches/fixed-costs")
def save_branch_fixed_costs(body: BranchFixedCostBody):
    """Save per-branch monthly fixed costs."""
    value = _json.dumps(body.costs)
    with get_db() as con:
        con.execute(
            "INSERT OR REPLACE INTO app_settings(key, value) VALUES('branch_fixed_costs', :v)",
            {"v": value},
        )
        con.commit()
    return {"saved": len(body.costs)}


# ---------------------------------------------------------------------------
# GET /api/buffet/pnl   🟡6 (part 2)
# Net P&L = GP - Fixed Costs per branch
# ---------------------------------------------------------------------------
@app.get("/api/buffet/pnl")
@ttl_cache
def buffet_pnl(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Net P&L per branch = Gross Profit (BoM) − monthly fixed costs."""
    # Reuse the same GP logic — aggregate by branch
    TIER_CODES = ('BUFFET','BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD',
                  'B01','B02','B07','B11')
    tier_named = {f"tc{i}": v for i, v in enumerate(TIER_CODES)}
    tier_ph    = ", ".join(f":tc{i}" for i in range(len(TIER_CODES)))

    sku_filters: list = ["b.name != 'ALL'"]
    sku_params: dict  = {**tier_named}
    if branch:
        sku_filters.append("b.name = :branch")
        sku_params["branch"] = branch
    _month_range_filter(sku_filters, sku_params, month_from, month_to, None, col="sm.period_month")
    where_sku = "WHERE " + " AND ".join(sku_filters)

    tx_filters: list = ["b.name != 'ALL'", "st.source='Easy'"]
    tx_params: dict  = {}
    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch
    _month_range_filter(tx_filters, tx_params, month_from, month_to, None,
                        col="SUBSTR(st.sold_at,1,7)")
    where_tx = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        gp_rows = con.execute(f"""
            SELECT b.name AS branch,
                   COUNT(DISTINCT sm.period_month)                          AS months,
                   SUM(sm.subtotal_ex_vat)                                  AS revenue,
                   SUM(CASE WHEN sk.sku_code NOT IN ({tier_ph})
                            THEN {_cogs_case(ssm='sm', sk='sk', sc='sc')} ELSE 0 END) AS bom_cost
            FROM sales_sku_monthly sm
            JOIN branches   b  ON b.branch_id   = sm.branch_id
            JOIN skus       sk ON sk.sku_id      = sm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id   = sm.sku_id
            {where_sku}
            GROUP BY b.name
        """, sku_params).fetchall()

        covers_rows = con.execute(f"""
            SELECT b.name AS branch, SUM(st.customer_count) AS covers
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx} AND st.customer_count > 0
            GROUP BY b.name
        """, tx_params).fetchall()

        fc_row = con.execute(
            "SELECT value FROM app_settings WHERE key='branch_fixed_costs'"
        ).fetchone()

    fixed_costs = {}
    if fc_row:
        try:
            fixed_costs = _json.loads(fc_row["value"])
        except Exception:
            pass

    covers_map = {r["branch"]: (r["covers"] or 0) for r in covers_rows}

    result = []
    for r in sorted(gp_rows, key=lambda x: -(x["revenue"] or 0)):
        br          = r["branch"]
        rev         = round(r["revenue"] or 0, 2)
        bom         = round(r["bom_cost"] or 0, 2)
        gp          = round(rev - bom, 2)
        months      = r["months"] or 1
        fc_monthly  = fixed_costs.get(br, 0.0)
        fc_period   = round(fc_monthly * months, 2)
        net_profit  = round(gp - fc_period, 2)
        covers      = covers_map.get(br, 0)
        np_per_cov  = round(net_profit / covers, 2) if covers else None
        gp_margin   = round(gp / rev * 100, 2) if rev else None
        np_margin   = round(net_profit / rev * 100, 2) if rev else None

        result.append({
            "branch":          br,
            "months":          months,
            "revenue":         rev,
            "bom_cost":        bom,
            "gross_profit":    gp,
            "gp_margin_pct":   gp_margin,
            "fixed_cost_period": fc_period,
            "fixed_cost_monthly": fc_monthly,
            "net_profit":      net_profit,
            "np_margin_pct":   np_margin,
            "covers":          covers,
            "np_per_cover":    np_per_cov,
        })

    return {"branches": result}


# ---------------------------------------------------------------------------
# GET /api/pnl/branch   — full branch P&L waterfall + break-even (Analysis #1)
# ---------------------------------------------------------------------------
@app.get("/api/pnl/branch")
@ttl_cache
def pnl_branch(
    branch:     Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Per-branch P&L: Revenue (ex-VAT) -> COGS (canonical _cogs_case) -> Gross
    Profit -> minus monthly fixed costs (app_settings) -> Operating Profit, plus
    break-even covers and margin of safety. Includes a chain roll-up."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch"); params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="ssm.period_month")
    where = "WHERE " + " AND ".join(filters)
    cogs_expr = _cogs_case()

    tf = ["b.name != 'ALL'", "st.source='Easy'", "st.customer_count > 0"]
    tp: dict = {}
    if branch:
        tf.append("b.name = :branch"); tp["branch"] = branch
    _month_range_filter(tf, tp, month_from, month_to, month, col="SUBSTR(st.sold_at,1,7)")
    twhere = "WHERE " + " AND ".join(tf)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.name AS branch,
                   COUNT(DISTINCT ssm.period_month) AS months,
                   SUM(ssm.subtotal_ex_vat)         AS revenue,
                   SUM({cogs_expr})                 AS cogs
            FROM sales_sku_monthly ssm
            JOIN branches b   ON b.branch_id  = ssm.branch_id
            JOIN skus sk      ON sk.sku_id    = ssm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY b.name
        """, params).fetchall()
        cov = con.execute(f"""
            SELECT b.name AS branch, SUM(st.customer_count) AS covers
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {twhere}
            GROUP BY b.name
        """, tp).fetchall()
        fc_row = con.execute(
            "SELECT value FROM app_settings WHERE key='branch_fixed_costs'"
        ).fetchone()

    fixed = {}
    if fc_row:
        try:
            fixed = _json.loads(fc_row["value"])
        except Exception:
            pass
    covmap = {r["branch"]: (r["covers"] or 0) for r in cov}

    out = []
    tot = {"revenue": 0.0, "cogs": 0.0, "fixed": 0.0, "covers": 0}
    for r in sorted(rows, key=lambda x: -(x["revenue"] or 0)):
        br      = r["branch"]
        rev     = round(r["revenue"] or 0.0, 2)
        cogs_v  = round(r["cogs"] or 0.0, 2)
        gp      = round(rev - cogs_v, 2)
        months  = r["months"] or 1
        fcm     = float(fixed.get(br, 0) or 0)
        fcp     = round(fcm * months, 2)
        op      = round(gp - fcp, 2)
        covers  = covmap.get(br, 0)
        gp_per_cover = (gp / covers) if covers else None
        breakeven_covers = round(fcp / gp_per_cover) if (gp_per_cover and gp_per_cover > 0) else None
        mos_pct = round((covers - breakeven_covers) / covers * 100, 1) if (breakeven_covers and covers) else None
        out.append({
            "branch":            br,
            "months":            months,
            "revenue":           rev,
            "cogs":              cogs_v,
            "gross_profit":      gp,
            "gp_margin_pct":     round(gp / rev * 100, 1) if rev else None,
            "fixed_cost_period": fcp,
            "fixed_cost_monthly": round(fcm, 2),
            "operating_profit":  op,
            "op_margin_pct":     round(op / rev * 100, 1) if rev else None,
            "covers":            covers,
            "op_per_cover":      round(op / covers, 2) if covers else None,
            "gp_per_cover":      round(gp_per_cover, 2) if gp_per_cover is not None else None,
            "breakeven_covers":  breakeven_covers,
            "margin_of_safety_pct": mos_pct,
        })
        tot["revenue"] += rev; tot["cogs"] += cogs_v; tot["fixed"] += fcp; tot["covers"] += covers

    chain_gp = round(tot["revenue"] - tot["cogs"], 2)
    chain_op = round(chain_gp - tot["fixed"], 2)
    chain = {
        "revenue":          round(tot["revenue"], 2),
        "cogs":             round(tot["cogs"], 2),
        "gross_profit":     chain_gp,
        "gp_margin_pct":    round(chain_gp / tot["revenue"] * 100, 1) if tot["revenue"] else None,
        "fixed_cost_period": round(tot["fixed"], 2),
        "operating_profit": chain_op,
        "op_margin_pct":    round(chain_op / tot["revenue"] * 100, 1) if tot["revenue"] else None,
        "covers":           tot["covers"],
        "fixed_costs_set":  bool(fixed),
    }
    return {"branches": out, "chain": chain}


# ---------------------------------------------------------------------------
# GET /api/cost/uncosted-coverage  — cost-data coverage tracker (Analysis #3)
# ---------------------------------------------------------------------------
@app.get("/api/cost/uncosted-coverage")
@ttl_cache
def cost_uncosted_coverage(
    branch:     Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """How much revenue rests on costed vs uncosted SKUs. 'Costed' = the SKU has an
    effective unit cost OR is a buffet tier (costed via item-level BoM). Returns
    per-branch coverage, a monthly trend, and the top uncosted SKUs by revenue —
    a prioritised worklist for filling in missing costs."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch"); params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="ssm.period_month")
    where = "WHERE " + " AND ".join(filters)
    costed = _costed_rev_case()            # tier rev + costed-sku rev, else 0
    eff    = _effective_cost_subquery()
    uncosted_pred = (f"({eff}) IS NULL AND ssm.subtotal_ex_vat > 0 "
                     f"AND sk.sku_code NOT IN {_EASY_TIER_CODES_SQL}")

    with get_db() as con:
        brows = con.execute(f"""
            SELECT b.name AS branch,
                   SUM(ssm.subtotal_ex_vat) AS total_rev,
                   SUM({costed})            AS costed_rev,
                   COUNT(DISTINCT ssm.sku_id) AS skus,
                   COUNT(DISTINCT CASE WHEN {uncosted_pred} THEN ssm.sku_id END) AS uncosted_skus
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus sk    ON sk.sku_id   = ssm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY b.name
        """, params).fetchall()
        trows = con.execute(f"""
            SELECT ssm.period_month AS m,
                   SUM(ssm.subtotal_ex_vat) AS total_rev,
                   SUM({costed})            AS costed_rev
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus sk    ON sk.sku_id   = ssm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY ssm.period_month ORDER BY ssm.period_month
        """, params).fetchall()
        urows = con.execute(f"""
            SELECT sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
                   c.name AS category,
                   SUM(ssm.subtotal_ex_vat) AS rev, ROUND(SUM(ssm.qty),0) AS qty
            FROM sales_sku_monthly ssm
            JOIN branches b   ON b.branch_id  = ssm.branch_id
            JOIN skus sk      ON sk.sku_id    = ssm.sku_id
            JOIN categories c ON c.category_id = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where} AND {uncosted_pred}
            GROUP BY sk.sku_id
            ORDER BY rev DESC LIMIT 25
        """, params).fetchall()

    branches = []
    tot_rev = tot_costed = 0.0
    for r in sorted(brows, key=lambda x: -((x["total_rev"] or 0) - (x["costed_rev"] or 0))):
        total = r["total_rev"] or 0.0
        costed_v = r["costed_rev"] or 0.0
        unc = total - costed_v
        tot_rev += total; tot_costed += costed_v
        branches.append({
            "branch":          r["branch"],
            "total_revenue":   round(total, 2),
            "costed_revenue":  round(costed_v, 2),
            "uncosted_revenue": round(unc, 2),
            "pct_uncosted":    round(unc / total * 100, 1) if total else 0.0,
            "skus":            r["skus"] or 0,
            "uncosted_skus":   r["uncosted_skus"] or 0,
        })
    trend = []
    for r in trows:
        total = r["total_rev"] or 0.0
        costed_v = r["costed_rev"] or 0.0
        trend.append({
            "period": r["m"],
            "pct_uncosted": round((total - costed_v) / total * 100, 1) if total else 0.0,
            "uncosted_revenue": round(total - costed_v, 2),
        })
    top_uncosted = [{
        "sku_code": r["sku_code"], "description": r["description"], "category": r["category"],
        "revenue": round(r["rev"] or 0, 2), "qty": r["qty"] or 0,
    } for r in urows]

    return {
        "chain": {
            "total_revenue":    round(tot_rev, 2),
            "costed_revenue":   round(tot_costed, 2),
            "uncosted_revenue": round(tot_rev - tot_costed, 2),
            "pct_uncosted":     round((tot_rev - tot_costed) / tot_rev * 100, 1) if tot_rev else 0.0,
        },
        "branches":     branches,
        "monthly_trend": trend,
        "top_uncosted": top_uncosted,
    }


# ---------------------------------------------------------------------------
# GET /api/buffet/gp/export   🟢7
# Download GP analysis as formatted Excel
# ---------------------------------------------------------------------------
from fastapi.responses import StreamingResponse
import io as _io

@app.get("/api/buffet/gp/export")
def buffet_gp_export(
    branch:      Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    granularity: str           = Query("month"),
):
    """Export buffet GP analysis to Excel (.xlsx)."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, numbers
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl not installed")

    # Fetch data using same logic as /api/buffet/gp
    params = {}
    if branch:      params["branch"]     = branch
    if month_from:  params["month_from"] = month_from
    if month_to:    params["month_to"]   = month_to
    params["granularity"] = granularity

    # Build query string and call internal function
    from fastapi.testclient import TestClient
    # Direct call to avoid HTTP overhead
    gp_data = buffet_gp(
        branch=branch, month_from=month_from,
        month_to=month_to, granularity=granularity,
    )

    wb  = openpyxl.Workbook()
    HDR = Font(bold=True, color="FFFFFF")
    FILL_H = PatternFill("solid", fgColor="1F4E79")
    FILL_GP = PatternFill("solid", fgColor="E8F5E9")
    NUM_FMT  = '#,##0'
    PCT_FMT  = '0.0"%"'
    CENTER   = Alignment(horizontal="center")

    def make_header(ws, headers, fill=FILL_H):
        ws.append(headers)
        for cell in ws[1]:
            cell.font = HDR
            cell.fill = fill
            cell.alignment = CENTER

    def autofit(ws):
        for col in ws.columns:
            max_len = max((len(str(cell.value or "")) for cell in col), default=8)
            ws.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + 2, 40)

    # Sheet 1: Period Summary
    ws1 = wb.active
    ws1.title = "Period Summary"
    pt = gp_data.get("period_total", {})
    ws1.append(["Metric", "Value"])
    for cell in ws1[1]: cell.font = HDR; cell.fill = FILL_H
    ws1.append(["Revenue (ex-VAT)", pt.get("revenue") or 0])
    ws1.append(["BoM Cost",         pt.get("bom_cost") or 0])
    ws1.append(["Gross Profit",     pt.get("gp") or 0])
    ws1.append(["GP Margin %",      (pt.get("margin_pct") or 0) / 100])
    ws1.append(["Covers",           pt.get("covers") or 0])
    ws1.append(["Cost/Cover",       pt.get("cost_per_cover") or 0])
    ws1["B4"].number_format = PCT_FMT
    autofit(ws1)

    # Sheet 2: Time Series
    ws2 = wb.create_sheet("Time Series")
    make_header(ws2, ["Bucket", "Revenue", "BoM Cost", "Gross Profit", "Margin %", "Covers"])
    for s in gp_data.get("series", []):
        ws2.append([
            s["bucket"], s["revenue"], s["bom_cost"], s["gp"],
            (s["margin_pct"] or 0) / 100, s["covers"],
        ])
    for row in ws2.iter_rows(min_row=2):
        for cell in row[1:4]: cell.number_format = NUM_FMT
        row[4].number_format = PCT_FMT
    autofit(ws2)

    # Sheet 3: By Branch
    ws3 = wb.create_sheet("By Branch")
    make_header(ws3, ["Branch", "Covers", "Revenue", "BoM Cost", "GP", "GP/Cover", "Margin %", "vs Network"])
    for b in gp_data.get("by_branch", []):
        ws3.append([
            b["branch"], b["covers"], b["revenue"], b["bom_cost"],
            b["gp"], b["gp_per_cover"], (b["margin_pct"] or 0) / 100,
            (b["vs_network_pp"] or 0) / 100,
        ])
    for row in ws3.iter_rows(min_row=2):
        for cell in row[2:6]: cell.number_format = NUM_FMT
        row[6].number_format = PCT_FMT
        row[7].number_format = PCT_FMT
    autofit(ws3)

    # Sheet 4: Top Cost SKUs
    ws4 = wb.create_sheet("Top Cost SKUs")
    make_header(ws4, ["SKU Code", "Category", "Qty", "BoM Cost", "% of Total Cost"])
    for s in gp_data.get("top_cost_skus", []):
        ws4.append([s["sku_code"], s["category"], s["qty"], s["bom_cost"], s["cost_pct"] / 100])
    for row in ws4.iter_rows(min_row=2):
        row[3].number_format = NUM_FMT
        row[4].number_format = PCT_FMT
    autofit(ws4)

    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"buffet_gp_{month_from or 'all'}_{month_to or 'all'}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# GET /api/export/monthly-pack   (Deliverable B — Monthly Review Pack)
# ---------------------------------------------------------------------------
def _mpack_collect(month: str) -> dict:
    """Gather all the numbers needed for the Monthly Review Pack.
    Honours Rule 1 (ex-VAT revenue from sales_sku_monthly)."""
    from datetime import date as _date_cls, timedelta as _td
    # Prior month
    y, mo = _ym_parts(month)
    first = _date_cls(y, mo, 1)
    prior_first = (first - _td(days=1)).replace(day=1)
    prior_month = prior_first.strftime("%Y-%m")

    cogs_expr       = _cogs_case('ssm', 'sk', 'sc')
    costed_rev_expr = _costed_rev_case('ssm', 'sk', 'sc')

    with get_db() as con:
        # Targets from app_settings
        try:
            srows = con.execute(
                "SELECT key, value FROM app_settings WHERE key IN ('cost_target_lo','cost_target_hi')"
            ).fetchall()
            settings = {r["key"]: r["value"] for r in srows}
        except Exception:
            settings = {}
        try:
            target_lo = float(settings.get("cost_target_lo", 55))
        except Exception:
            target_lo = 55.0
        try:
            target_hi = float(settings.get("cost_target_hi", 70))
        except Exception:
            target_hi = 70.0

        # Network summary — current + prior
        def _net(m: str) -> dict:
            r = con.execute(f"""
                SELECT COALESCE(SUM(ssm.subtotal_ex_vat),0)  AS revenue,
                       COALESCE(SUM({cogs_expr}),0)          AS cogs,
                       COALESCE(SUM({costed_rev_expr}),0)    AS costed_rev,
                       COUNT(DISTINCT ssm.branch_id)         AS branch_count
                  FROM sales_sku_monthly ssm
                  JOIN branches b ON b.branch_id = ssm.branch_id
                  JOIN skus     sk ON sk.sku_id  = ssm.sku_id
                  LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
                 WHERE ssm.period_month = :m
                   AND b.name != 'ALL'
                   AND COALESCE(b.active,1) = 1
            """, {"m": m}).fetchone()
            return {
                "month":       m,
                "revenue":     float(r["revenue"]    or 0),
                "cogs":        float(r["cogs"]       or 0),
                "costed_rev":  float(r["costed_rev"] or 0),
                "branch_count": int(r["branch_count"] or 0),
            }
        summary_cur   = _net(month)
        summary_prior = _net(prior_month)

        # P&L per branch (current month)
        branch_rows = con.execute(f"""
            SELECT b.name AS branch,
                   COALESCE(SUM(ssm.subtotal_ex_vat),0) AS revenue,
                   COALESCE(SUM({cogs_expr}),0)         AS cogs,
                   COALESCE(SUM({costed_rev_expr}),0)   AS costed_rev
              FROM sales_sku_monthly ssm
              JOIN branches b ON b.branch_id = ssm.branch_id
              JOIN skus     sk ON sk.sku_id  = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
             WHERE ssm.period_month = :m
               AND b.name != 'ALL'
               AND COALESCE(b.active,1) = 1
             GROUP BY b.branch_id
             ORDER BY revenue DESC
        """, {"m": month}).fetchall()

        # Top 30 cost drivers (current month, network-wide)
        top_drivers = con.execute(f"""
            SELECT sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   SUM(ssm.qty)                          AS qty,
                   SUM({cogs_expr}) / NULLIF(SUM(ssm.qty),0) AS unit_cost,
                   SUM({cogs_expr})                      AS total_cogs
              FROM sales_sku_monthly ssm
              JOIN branches b ON b.branch_id = ssm.branch_id
              JOIN skus     sk ON sk.sku_id  = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
             WHERE ssm.period_month = :m
               AND b.name != 'ALL'
               AND COALESCE(b.active,1) = 1
             GROUP BY sk.sku_id
             HAVING total_cogs > 0
             ORDER BY total_cogs DESC
             LIMIT 50
        """, {"m": month}).fetchall()

        # For sparklines: last-12-month qty per SKU (top drivers only)
        sku_ids_csv = ""  # unused
        spark_data: dict = {}
        if top_drivers:
            # Build SKU id list
            sku_ids = con.execute(f"""
                SELECT sku_id, sku_code FROM skus
                 WHERE sku_code IN ({",".join(":c"+str(i) for i in range(len(top_drivers)))})
            """, {f"c{i}": r["sku_code"] for i, r in enumerate(top_drivers)}).fetchall()
            code_to_id = {r["sku_code"]: r["sku_id"] for r in sku_ids}
            # 12-month window ending at `month`
            y2, m2 = _ym_parts(month)
            from_dt = _date_cls(y2, m2, 1)
            for _ in range(11):
                from_dt = (from_dt.replace(day=1) - _td(days=1)).replace(day=1)
            month_from_12 = from_dt.strftime("%Y-%m")
            ids = list(code_to_id.values())
            if ids:
                ph = ",".join(":s"+str(i) for i in range(len(ids)))
                qrows = con.execute(f"""
                    SELECT sku_id, period_month, SUM(qty) AS qty
                      FROM sales_sku_monthly
                     WHERE sku_id IN ({ph})
                       AND period_month >= :mf AND period_month <= :mt
                     GROUP BY sku_id, period_month
                     ORDER BY sku_id, period_month
                """, {**{f"s{i}": sid for i, sid in enumerate(ids)},
                      "mf": month_from_12, "mt": month}).fetchall()
                for qr in qrows:
                    spark_data.setdefault(qr["sku_id"], []).append(
                        (qr["period_month"], float(qr["qty"] or 0))
                    )
            # Map back by sku_code
            top_drivers_out = []
            total_cost = sum(float(r["total_cogs"] or 0) for r in top_drivers) or 1.0
            for r in top_drivers:
                sid = code_to_id.get(r["sku_code"])
                top_drivers_out.append({
                    "sku_code":    r["sku_code"],
                    "description": r["description"],
                    "qty":         float(r["qty"] or 0),
                    "unit_cost":   float(r["unit_cost"] or 0),
                    "total_cogs":  float(r["total_cogs"] or 0),
                    "pct_of_total": float(r["total_cogs"] or 0) / total_cost * 100,
                    "sparkline":   spark_data.get(sid, []),
                })
        else:
            top_drivers_out = []

        # Top 25 ingredients across all branches (current month)
        ing_rows = con.execute("""
            WITH eff AS (
              SELECT ssm.sku_id, ssm.qty,
                COALESCE(
                  (SELECT bi.sku_id FROM bom_ingredients bi WHERE bi.sku_id = ssm.sku_id LIMIT 1),
                  (SELECT a.resolved_sku_id FROM bom_sku_alias a
                     JOIN skus s ON s.sku_id = ssm.sku_id
                     WHERE a.bom_sku_code = s.sku_code LIMIT 1)
                ) AS bom_sku_id
              FROM sales_sku_monthly ssm
              JOIN branches b ON b.branch_id = ssm.branch_id
              WHERE ssm.period_month = :m
                AND b.name != 'ALL'
                AND COALESCE(b.active,1) = 1
            )
            SELECT COALESCE(NULLIF(bi.ingredient_code,''), '__'||bi.ingredient_name) AS ing_key,
                   MAX(bi.ingredient_code) AS code,
                   MAX(bi.ingredient_name) AS name,
                   MAX(bi.ingredient_type) AS type,
                   MAX(bi.unit) AS unit,
                   SUM(eff.qty * bi.qty_per_serving) AS total_qty,
                   SUM(eff.qty * bi.ingredient_cost) AS total_cost
              FROM eff
              JOIN bom_ingredients bi ON bi.sku_id = eff.bom_sku_id
             WHERE eff.bom_sku_id IS NOT NULL
             GROUP BY ing_key
             ORDER BY total_cost DESC
             LIMIT 50
        """, {"m": month}).fetchall()

    # Derive
    def _enrich(s):
        rev = s["revenue"]; cogs = s["cogs"]; cr = s["costed_rev"]
        s["gp"]            = cr - cogs
        s["gp_pct"]        = (s["gp"]/cr*100) if cr > 0 else None
        s["food_cost_pct"] = (cogs/cr*100)    if cr > 0 else None
        return s
    summary_cur   = _enrich(summary_cur)
    summary_prior = _enrich(summary_prior)

    def _delta_pct(cur, prev):
        if prev is None or prev == 0: return None
        return (cur - prev) / prev * 100
    summary_cur["vs_prior_revenue_pct"] = _delta_pct(summary_cur["revenue"], summary_prior["revenue"])
    summary_cur["vs_prior_gp_pct_pp"]   = (
        (summary_cur["gp_pct"] - summary_prior["gp_pct"])
        if summary_cur["gp_pct"] is not None and summary_prior["gp_pct"] is not None else None
    )

    # Per-branch P&L
    branches = []
    alerts = []
    for r in branch_rows:
        rev = float(r["revenue"] or 0)
        cogs = float(r["cogs"] or 0)
        cr   = float(r["costed_rev"] or 0)
        gp   = cr - cogs
        gp_p = (gp/cr*100)   if cr > 0 else None
        fc_p = (cogs/cr*100) if cr > 0 else None
        if fc_p is None:
            status = "no-data"
        elif fc_p > target_hi:
            status = "out"
        elif fc_p < target_lo - 5:
            status = "low"        # suspiciously low
        elif fc_p > target_hi - 3:
            status = "borderline"
        else:
            status = "in"
        branches.append({
            "branch": r["branch"], "revenue": rev, "cogs": cogs,
            "costed_rev": cr, "gp": gp, "gp_pct": gp_p, "food_cost_pct": fc_p,
            "status": status,
        })
        # Alerts
        if fc_p is not None and fc_p > 70.0:
            alerts.append({
                "branch": r["branch"], "severity": "high",
                "message": f"Food cost {fc_p:.1f}% เกิน 70% (เป้า {target_lo}-{target_hi}%)",
            })
        if gp is not None and gp < 0:
            alerts.append({
                "branch": r["branch"], "severity": "critical",
                "message": f"GP ติดลบ {gp:,.0f}฿ — ทบทวนต้นทุนเร่งด่วน",
            })

    # Top ingredients
    total_ing_cost = sum(float(r["total_cost"] or 0) for r in ing_rows) or 1.0
    ingredients = []
    for r in ing_rows:
        c = float(r["total_cost"] or 0)
        ingredients.append({
            "ingredient_code": r["code"] or "",
            "ingredient_name": r["name"] or "",
            "ingredient_type": r["type"] or "other",
            "unit":            r["unit"] or "",
            "total_qty":       float(r["total_qty"] or 0),
            "total_cost":      c,
            "pct_of_total":    c / total_ing_cost * 100,
        })

    # Auto action items
    actions = []
    # 1) Branches over target
    over = sorted([b for b in branches if b["food_cost_pct"] and b["food_cost_pct"] > target_hi],
                  key=lambda x: -x["food_cost_pct"])[:5]
    for b in over:
        actions.append(f"ทบทวน BoM/ราคาขาย ที่สาขา {b['branch']} — Food Cost {b['food_cost_pct']:.1f}% (เป้า ≤{target_hi}%)")
    # 2) Top cost driver concentration
    if top_drivers_out:
        top5_pct = sum(d["pct_of_total"] for d in top_drivers_out[:5])
        if top5_pct >= 30:
            actions.append(f"Top 5 SKU คิดเป็น {top5_pct:.1f}% ของต้นทุน — เจรจาราคากับ supplier")
    # 3) Top ingredients
    if ingredients:
        top3 = ingredients[:3]
        names = ", ".join(i["ingredient_name"] for i in top3)
        actions.append(f"วัตถุดิบต้นทุนสูงสุด: {names} — เปรียบเทียบราคา supplier รายเดือน")
    # 4) GP trend
    if summary_cur.get("vs_prior_gp_pct_pp") is not None and summary_cur["vs_prior_gp_pct_pp"] < -2:
        actions.append(f"GP% ลดลง {abs(summary_cur['vs_prior_gp_pct_pp']):.1f} pp จากเดือนก่อน — สอบสวนสาเหตุ")
    if not actions:
        actions.append("ไม่มีรายการที่ต้องดำเนินการเร่งด่วนในเดือนนี้")

    return {
        "month": month,
        "prior_month": prior_month,
        "summary_current": summary_cur,
        "summary_prior":   summary_prior,
        "target_lo": target_lo,
        "target_hi": target_hi,
        "branches":  branches,
        "top_drivers": top_drivers_out,
        "ingredients": ingredients,
        "alerts": alerts,
        "actions": actions,
    }


def _mpack_render_xlsx(pack: dict) -> bytes:
    """Render the Monthly Pack to an xlsx workbook."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    HDR_FONT  = Font(bold=True, color="FFFFFF", size=11)
    HDR_FILL  = PatternFill("solid", fgColor="1F4E79")
    TITLE     = Font(bold=True, size=14, color="1F4E79")
    SUBTITLE  = Font(italic=True, size=10, color="64748B")
    CENTER    = Alignment(horizontal="center", vertical="center")
    NUM_FMT   = '#,##0'
    PCT_FMT   = '0.0"%"'

    STATUS_COLOR = {
        "in":         ("E8F5E9", "1B5E20", "✅ ในเป้า"),
        "borderline": ("FFF3E0", "E65100", "⚠️ ใกล้ขอบ"),
        "out":        ("FFEBEE", "B71C1C", "🔴 เกินเป้า"),
        "low":        ("E3F2FD", "0D47A1", "ℹ️ ต่ำผิดปกติ"),
        "no-data":    ("ECEFF1", "455A64", "— ไม่มีข้อมูล"),
    }

    def make_header(ws, headers, row=1):
        for i, h in enumerate(headers, start=1):
            c = ws.cell(row=row, column=i, value=h)
            c.font = HDR_FONT; c.fill = HDR_FILL; c.alignment = CENTER

    def autofit(ws, min_w=10, max_w=42):
        for col in ws.columns:
            mx = max((len(str(cell.value)) if cell.value is not None else 0) for cell in col)
            ws.column_dimensions[get_column_letter(col[0].column)].width = max(min_w, min(mx + 2, max_w))

    wb = openpyxl.Workbook()

    # ── Sheet: Summary ────────────────────────────────────────────────────
    ws = wb.active
    ws.title = "Summary"
    sc = pack["summary_current"]; sp = pack["summary_prior"]
    ws["A1"] = f"Oshinei — Monthly Review Pack · {pack['month']}"
    ws["A1"].font = TITLE
    ws.merge_cells("A1:E1")
    ws["A2"] = f"เทียบกับ {pack['prior_month']} · เป้า Food Cost {pack['target_lo']:.0f}–{pack['target_hi']:.0f}%"
    ws["A2"].font = SUBTITLE
    ws.merge_cells("A2:E2")
    make_header(ws, ["Metric", pack["month"], pack["prior_month"], "Δ", "Δ %"], row=4)
    def _row(label, cur, prev, fmt=NUM_FMT, is_pct=False):
        r = ws.max_row + 1
        ws.cell(row=r, column=1, value=label).font = Font(bold=True)
        ws.cell(row=r, column=2, value=cur).number_format  = fmt
        ws.cell(row=r, column=3, value=prev).number_format = fmt
        if cur is not None and prev is not None:
            delta = cur - prev
            ws.cell(row=r, column=4, value=delta).number_format = fmt
            if prev != 0 and not is_pct:
                ws.cell(row=r, column=5, value=(delta/prev*100)).number_format = PCT_FMT
    _row("Revenue (ex-VAT)",   sc["revenue"],       sp["revenue"])
    _row("COGS",               sc["cogs"],          sp["cogs"])
    _row("Gross Profit",       sc["gp"],            sp["gp"])
    _row("GP %",               sc["gp_pct"],        sp["gp_pct"], fmt=PCT_FMT, is_pct=True)
    _row("Food Cost %",        sc["food_cost_pct"], sp["food_cost_pct"], fmt=PCT_FMT, is_pct=True)
    _row("Branch count",       sc["branch_count"],  sp["branch_count"])
    autofit(ws)

    # ── Sheet: P&L per Branch ─────────────────────────────────────────────
    ws2 = wb.create_sheet("P&L per Branch")
    make_header(ws2, ["Branch", "Revenue", "COGS", "GP", "GP %", "Food Cost %", "Status"])
    for b in pack["branches"]:
        r = ws2.max_row + 1
        ws2.cell(row=r, column=1, value=b["branch"])
        ws2.cell(row=r, column=2, value=b["revenue"]).number_format       = NUM_FMT
        ws2.cell(row=r, column=3, value=b["cogs"]).number_format          = NUM_FMT
        ws2.cell(row=r, column=4, value=b["gp"]).number_format            = NUM_FMT
        ws2.cell(row=r, column=5, value=b["gp_pct"]).number_format        = PCT_FMT
        ws2.cell(row=r, column=6, value=b["food_cost_pct"]).number_format = PCT_FMT
        bg, fg, label = STATUS_COLOR.get(b["status"], STATUS_COLOR["no-data"])
        sc_cell = ws2.cell(row=r, column=7, value=label)
        sc_cell.fill = PatternFill("solid", fgColor=bg)
        sc_cell.font = Font(color=fg, bold=True)
    autofit(ws2)

    # ── Sheet: Top 50 Cost Drivers ────────────────────────────────────────
    ws3 = wb.create_sheet("Top 50 Cost Drivers")
    make_header(ws3, ["SKU", "Description", "Qty", "Unit Cost", "Total COGS",
                       "% of Total", "12-mo Qty Trend"])
    for d in pack["top_drivers"]:
        r = ws3.max_row + 1
        ws3.cell(row=r, column=1, value=d["sku_code"])
        ws3.cell(row=r, column=2, value=d["description"])
        ws3.cell(row=r, column=3, value=d["qty"]).number_format        = NUM_FMT
        ws3.cell(row=r, column=4, value=d["unit_cost"]).number_format  = '#,##0.00'
        ws3.cell(row=r, column=5, value=d["total_cogs"]).number_format = NUM_FMT
        ws3.cell(row=r, column=6, value=d["pct_of_total"]).number_format = PCT_FMT
        spark = ",".join(f"{m}:{int(q)}" for m, q in d.get("sparkline", []))
        ws3.cell(row=r, column=7, value=spark)
    autofit(ws3)

    # ── Sheet: BoM Ingredients ────────────────────────────────────────────
    ws4 = wb.create_sheet("BoM Ingredients")
    make_header(ws4, ["Code", "Name", "Type", "Unit", "Total Qty",
                       "Total Cost", "% of Total"])
    for ing in pack["ingredients"]:
        r = ws4.max_row + 1
        ws4.cell(row=r, column=1, value=ing["ingredient_code"])
        ws4.cell(row=r, column=2, value=ing["ingredient_name"])
        ws4.cell(row=r, column=3, value=ing["ingredient_type"])
        ws4.cell(row=r, column=4, value=ing["unit"])
        ws4.cell(row=r, column=5, value=ing["total_qty"]).number_format    = '#,##0.00'
        ws4.cell(row=r, column=6, value=ing["total_cost"]).number_format   = NUM_FMT
        ws4.cell(row=r, column=7, value=ing["pct_of_total"]).number_format = PCT_FMT
    autofit(ws4)

    # ── Sheet: Alerts ─────────────────────────────────────────────────────
    ws5 = wb.create_sheet("Alerts")
    make_header(ws5, ["Branch", "Severity", "Message"])
    if not pack["alerts"]:
        ws5.cell(row=2, column=1, value="ไม่มี alert ในเดือนนี้").font = Font(italic=True)
    for a in pack["alerts"]:
        r = ws5.max_row + 1
        ws5.cell(row=r, column=1, value=a["branch"])
        sev_cell = ws5.cell(row=r, column=2, value=a["severity"])
        if a["severity"] == "critical":
            sev_cell.fill = PatternFill("solid", fgColor="FFEBEE")
            sev_cell.font = Font(color="B71C1C", bold=True)
        elif a["severity"] == "high":
            sev_cell.fill = PatternFill("solid", fgColor="FFF3E0")
            sev_cell.font = Font(color="E65100", bold=True)
        ws5.cell(row=r, column=3, value=a["message"])
    autofit(ws5)

    # ── Sheet: Action Items ───────────────────────────────────────────────
    ws6 = wb.create_sheet("Action Items")
    make_header(ws6, ["#", "Action"])
    for i, a in enumerate(pack["actions"], start=1):
        ws6.cell(row=i + 1, column=1, value=i)
        ws6.cell(row=i + 1, column=2, value=a)
    autofit(ws6)

    buf = _io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _mpack_render_pdf(pack: dict) -> bytes:
    """Render the Monthly Pack to a multi-page PDF using reportlab + matplotlib."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, Image as RLImage,
    )
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    buf = _io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm,
                            title=f"Monthly Pack {pack['month']}")
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], textColor=colors.HexColor("#1F4E79"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=colors.HexColor("#1F4E79"))
    body  = styles["BodyText"]
    note  = ParagraphStyle("note", parent=body, textColor=colors.grey, fontSize=9, italic=True)
    story = []

    sc = pack["summary_current"]; sp = pack["summary_prior"]
    fmt_n = lambda v: f"{v:,.0f}" if isinstance(v, (int,float)) else "—"
    fmt_p = lambda v: f"{v:,.1f}%" if isinstance(v, (int,float)) else "—"

    # ── Cover page ────────────────────────────────────────────────────────
    story.append(Spacer(1, 3*cm))
    story.append(Paragraph(f"Oshinei Monthly Review Pack", h1))
    story.append(Paragraph(f"{pack['month']}", h2))
    story.append(Spacer(1, 1*cm))
    story.append(Paragraph(f"<b>{sc['branch_count']}</b> สาขา · เป้า Food Cost <b>{pack['target_lo']:.0f}–{pack['target_hi']:.0f}%</b>", body))
    story.append(Spacer(1, 0.6*cm))
    cover_tbl = Table([
        ["Metric", pack["month"], pack["prior_month"]],
        ["Revenue (ex-VAT)", fmt_n(sc["revenue"]),      fmt_n(sp["revenue"])],
        ["COGS",             fmt_n(sc["cogs"]),         fmt_n(sp["cogs"])],
        ["Gross Profit",     fmt_n(sc["gp"]),           fmt_n(sp["gp"])],
        ["GP %",             fmt_p(sc["gp_pct"]),       fmt_p(sp["gp_pct"])],
        ["Food Cost %",      fmt_p(sc["food_cost_pct"]),fmt_p(sp["food_cost_pct"])],
    ], colWidths=[5*cm, 5*cm, 5*cm])
    cover_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F4E79")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("ALIGN",      (1,1), (-1,-1), "RIGHT"),
        ("FONTNAME",   (0,1), (0,-1), "Helvetica-Bold"),
        ("GRID",       (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.whitesmoke, colors.white]),
    ]))
    story.append(cover_tbl)
    story.append(Spacer(1, 1.5*cm))
    story.append(Paragraph("Generated by Oshinei Dashboard · Rule: revenue is ex-VAT.", note))
    story.append(PageBreak())

    # ── P&L per Branch ────────────────────────────────────────────────────
    story.append(Paragraph("P&L per Branch", h2))
    # Bar chart of GP% per branch
    branches = pack["branches"]
    if branches:
        labels = [b["branch"] for b in branches]
        gpps   = [b["gp_pct"] if b["gp_pct"] is not None else 0 for b in branches]
        fig, ax = plt.subplots(figsize=(7.5, max(3, len(labels)*0.25)))
        bar_colors = []
        for b in branches:
            st = b["status"]
            bar_colors.append({"in":"#10b981","borderline":"#f59e0b","out":"#ef4444",
                               "low":"#3b82f6","no-data":"#94a3b8"}.get(st, "#94a3b8"))
        ax.barh(labels, gpps, color=bar_colors)
        ax.invert_yaxis()
        ax.set_xlabel("GP %")
        ax.axvline(100 - pack["target_hi"], color="#10b981", linestyle="--", lw=0.7, alpha=0.5)
        ax.axvline(100 - pack["target_lo"], color="#10b981", linestyle="--", lw=0.7, alpha=0.5)
        ax.grid(axis="x", alpha=0.2)
        fig.tight_layout()
        imgbuf = _io.BytesIO()
        fig.savefig(imgbuf, format="png", dpi=110)
        plt.close(fig)
        imgbuf.seek(0)
        story.append(RLImage(imgbuf, width=17*cm, height=min(18*cm, max(5*cm, len(labels)*0.6*cm))))
        story.append(Spacer(1, 0.4*cm))

    # Branch table
    tbl_data = [["Branch", "Revenue", "COGS", "GP", "GP %", "FC %", "Status"]]
    for b in branches:
        tbl_data.append([
            b["branch"], fmt_n(b["revenue"]), fmt_n(b["cogs"]), fmt_n(b["gp"]),
            fmt_p(b["gp_pct"]), fmt_p(b["food_cost_pct"]),
            {"in":"✅","borderline":"⚠️","out":"🔴","low":"ℹ️","no-data":"—"}.get(b["status"],"—"),
        ])
    branch_tbl = Table(tbl_data, colWidths=[3.5*cm, 2.5*cm, 2.5*cm, 2.5*cm, 1.8*cm, 1.8*cm, 1.4*cm])
    branch_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F4E79")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,-1), 8),
        ("ALIGN",      (1,1), (-1,-1), "RIGHT"),
        ("ALIGN",      (6,1), (6,-1), "CENTER"),
        ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.whitesmoke, colors.white]),
    ]))
    story.append(branch_tbl)
    story.append(PageBreak())

    # ── Top 50 Cost Drivers ───────────────────────────────────────────────
    story.append(Paragraph("Top 50 Cost Drivers", h2))
    drivers = pack["top_drivers"]
    if drivers:
        # Chart: top 25 cost share (larger since list is now 50)
        top25 = drivers[:25]
        fig, ax = plt.subplots(figsize=(7.5, 7))
        ax.barh([d["sku_code"] for d in top25][::-1],
                [d["total_cogs"] for d in top25][::-1], color="#d946ef")
        ax.set_xlabel("Total COGS (฿)")
        ax.grid(axis="x", alpha=0.2)
        fig.tight_layout()
        imgbuf = _io.BytesIO()
        fig.savefig(imgbuf, format="png", dpi=110)
        plt.close(fig)
        imgbuf.seek(0)
        story.append(RLImage(imgbuf, width=17*cm, height=10*cm))
        story.append(Spacer(1, 0.3*cm))

        tbl_data = [["SKU", "Description", "Qty", "Unit Cost", "COGS", "%"]]
        for d in drivers[:30]:
            tbl_data.append([
                d["sku_code"], (d["description"] or "")[:32],
                fmt_n(d["qty"]), f"{d['unit_cost']:,.2f}",
                fmt_n(d["total_cogs"]), f"{d['pct_of_total']:.1f}%",
            ])
        t = Table(tbl_data, colWidths=[2.3*cm, 7*cm, 2*cm, 2*cm, 2.3*cm, 1.4*cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F4E79")),
            ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,-1), 7),
            ("ALIGN",      (2,1), (-1,-1), "RIGHT"),
            ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#cbd5e1")),
        ]))
        story.append(t)
    story.append(PageBreak())

    # ── BoM Ingredients ───────────────────────────────────────────────────
    story.append(Paragraph("Top 25 Ingredients", h2))
    ings = pack["ingredients"]
    if ings:
        fig, ax = plt.subplots(figsize=(7.5, 5))
        ax.barh([i["ingredient_name"][:24] for i in ings][::-1],
                [i["total_cost"] for i in ings][::-1], color="#0891b2")
        ax.set_xlabel("Total Cost (฿)")
        ax.grid(axis="x", alpha=0.2)
        fig.tight_layout()
        imgbuf = _io.BytesIO()
        fig.savefig(imgbuf, format="png", dpi=110)
        plt.close(fig)
        imgbuf.seek(0)
        story.append(RLImage(imgbuf, width=17*cm, height=11*cm))
        story.append(Spacer(1, 0.3*cm))

        tbl_data = [["Code", "Name", "Type", "Qty", "Cost", "%"]]
        for i in ings:
            tbl_data.append([
                i["ingredient_code"], i["ingredient_name"][:28],
                i["ingredient_type"], f"{i['total_qty']:,.1f}",
                fmt_n(i["total_cost"]), f"{i['pct_of_total']:.1f}%",
            ])
        t = Table(tbl_data, colWidths=[2.2*cm, 6.5*cm, 2.2*cm, 2*cm, 2.3*cm, 1.4*cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F4E79")),
            ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,-1), 7),
            ("ALIGN",      (3,1), (-1,-1), "RIGHT"),
            ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#cbd5e1")),
        ]))
        story.append(t)
    story.append(PageBreak())

    # ── Alerts ────────────────────────────────────────────────────────────
    story.append(Paragraph("Alerts", h2))
    alerts = pack["alerts"]
    if not alerts:
        story.append(Paragraph("ไม่มี alert ในเดือนนี้", note))
    else:
        a_data = [["Branch", "Severity", "Message"]]
        for a in alerts:
            a_data.append([a["branch"], a["severity"], a["message"]])
        a_tbl = Table(a_data, colWidths=[3.5*cm, 2.5*cm, 11*cm])
        a_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F4E79")),
            ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,-1), 9),
            ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#cbd5e1")),
            ("VALIGN",     (0,0), (-1,-1), "TOP"),
        ]))
        story.append(a_tbl)
    story.append(PageBreak())

    # ── Action Items ──────────────────────────────────────────────────────
    story.append(Paragraph("Action Items", h2))
    for i, a in enumerate(pack["actions"], start=1):
        story.append(Paragraph(f"<b>{i}.</b> {a}", body))
        story.append(Spacer(1, 0.15*cm))

    doc.build(story)
    return buf.getvalue()


@app.get("/api/export/monthly-pack")
def export_monthly_pack(
    month:  str = Query(..., description="Period in YYYY-MM"),
    format: str = Query("xlsx", description="xlsx | pdf"),
):
    """Monthly Review Pack — multi-sheet workbook or multi-page PDF.

    Sheets / pages: Summary, P&L per Branch, Top 50 Cost Drivers,
    BoM Ingredients, Alerts, Action Items.
    """
    if not month or len(month) != 7 or month[4] != "-":
        raise HTTPException(400, "month must be YYYY-MM")
    fmt = (format or "xlsx").lower()
    if fmt not in ("xlsx", "pdf"):
        raise HTTPException(400, "format must be xlsx or pdf")
    pack = _mpack_collect(month)
    if fmt == "xlsx":
        data = _mpack_render_xlsx(pack)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        fname = f"oshinei_monthly_pack_{month}.xlsx"
    else:
        try:
            data = _mpack_render_pdf(pack)
        except ImportError as e:
            raise HTTPException(500, f"PDF dependency missing: {e}")
        media = "application/pdf"
        fname = f"oshinei_monthly_pack_{month}.pdf"
    return StreamingResponse(
        _io.BytesIO(data),
        media_type=media,
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


# ---------------------------------------------------------------------------
# GET /api/menu/lifecycle   🟢9
# Classify SKUs as New / Growing / Stable / Declining / Dead
# ---------------------------------------------------------------------------
@app.get("/api/menu/lifecycle")
@ttl_cache
def menu_lifecycle(
    branch:     Optional[str] = Query(None),
    months:     int           = Query(6, ge=3, le=12),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Classify SKUs by 6-month qty trend into lifecycle stages."""
    with get_db() as con:
        # Last N months of SKU data
        max_month_row = con.execute(
            "SELECT MAX(period_month) AS m FROM sales_sku_monthly"
        ).fetchone()
        if not max_month_row or not max_month_row["m"]:
            return {"skus": [], "summary": {}}
        max_month = max_month_row["m"]

        from datetime import date as _date
        import datetime as _dtt
        if month_from and month_to:
            cutoff_month = month_from
            max_month    = month_to
        elif month_from:
            cutoff_month = month_from
        elif month_to:
            max_month = month_to
            y, mo = int(max_month[:4]), int(max_month[5:7])
            cutoff_dt = _date(y, mo, 1)
            for _ in range(months - 1):
                cutoff_dt = (cutoff_dt.replace(day=1) - _dtt.timedelta(days=1)).replace(day=1)
            cutoff_month = cutoff_dt.strftime("%Y-%m")
        else:
            # compute cutoff month relative to max available
            y, mo = int(max_month[:4]), int(max_month[5:7])
            cutoff_dt = _date(y, mo, 1)
            for _ in range(months - 1):
                cutoff_dt = (cutoff_dt.replace(day=1) - _dtt.timedelta(days=1)).replace(day=1)
            cutoff_month = cutoff_dt.strftime("%Y-%m")

        if branch:
            # Branch-filtered: join branches to apply name filter
            sku_rows = con.execute(
                """
                SELECT sk.sku_code,
                       COALESCE(sk.description, sk.sku_code) AS description,
                       c.name AS category,
                       sm.period_month AS m,
                       SUM(sm.qty)            AS qty,
                       SUM(sm.subtotal_ex_vat) AS rev
                FROM sales_sku_monthly sm
                JOIN branches   b  ON b.branch_id  = sm.branch_id
                JOIN skus       sk ON sk.sku_id     = sm.sku_id
                JOIN categories c  ON c.category_id = sk.category_id
                WHERE b.name = :branch
                  AND sm.period_month >= :cutoff AND sm.period_month <= :max_m
                GROUP BY sm.sku_id, sm.period_month
                ORDER BY sm.sku_id, sm.period_month
                """,
                {"branch": branch, "cutoff": cutoff_month, "max_m": max_month},
            ).fetchall()
        else:
            # All branches: skip branches JOIN — uses idx_ssm_sku_period covering index,
            # avoids temp B-TREE by grouping on sm.sku_id (in index key order)
            sku_rows = con.execute(
                """
                SELECT sk.sku_code,
                       COALESCE(sk.description, sk.sku_code) AS description,
                       c.name AS category,
                       sm.period_month AS m,
                       SUM(sm.qty)            AS qty,
                       SUM(sm.subtotal_ex_vat) AS rev
                FROM sales_sku_monthly sm
                JOIN skus       sk ON sk.sku_id     = sm.sku_id
                JOIN categories c  ON c.category_id = sk.category_id
                WHERE sm.period_month >= :cutoff AND sm.period_month <= :max_m
                GROUP BY sm.sku_id, sm.period_month
                ORDER BY sm.sku_id, sm.period_month
                """,
                {"cutoff": cutoff_month, "max_m": max_month},
            ).fetchall()

    from collections import defaultdict
    sku_data: dict = defaultdict(lambda: {"description": "", "category": "", "monthly": {}})
    for r in sku_rows:
        sd = sku_data[r["sku_code"]]
        sd["description"] = r["description"]
        sd["category"]    = r["category"]
        sd["monthly"][r["m"]] = r["qty"] or 0

    # Get all months in range
    all_months = []
    cutoff_dt = _date(int(cutoff_month[:4]), int(cutoff_month[5:7]), 1)
    cur = cutoff_dt
    max_dt = _date(int(max_month[:4]), int(max_month[5:7]), 1)
    while cur <= max_dt:
        all_months.append(cur.strftime("%Y-%m"))
        nxt = (cur.replace(day=28) + _dtt.timedelta(days=4)).replace(day=1)
        cur = nxt

    result = []
    for code, sd in sku_data.items():
        qty_series = [sd["monthly"].get(m, 0) for m in all_months]
        n = len(qty_series)

        # Lifecycle classification
        recent_2 = qty_series[-2:] if n >= 2 else qty_series
        early_months = [m for m in qty_series[:3] if m > 0]
        all_nonzero_start = next((i for i, v in enumerate(qty_series) if v > 0), None)

        is_dead = all(v == 0 for v in recent_2) and any(v > 0 for v in qty_series)
        is_new  = all_nonzero_start is not None and all_nonzero_start >= n - 2

        # Trend: linear regression slope
        nonzero = [(i, v) for i, v in enumerate(qty_series) if v > 0]
        if len(nonzero) >= 3:
            xs = [p[0] for p in nonzero]
            ys = [p[1] for p in nonzero]
            xm = sum(xs) / len(xs)
            ym = sum(ys) / len(ys)
            num = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
            den = sum((x - xm) ** 2 for x in xs) or 1
            slope = num / den
            slope_pct = round(slope / ym * 100, 1) if ym else 0
        else:
            slope_pct = 0

        if is_dead:
            stage = "dead"
        elif is_new:
            stage = "new"
        elif slope_pct > 10:
            stage = "growing"
        elif slope_pct < -10:
            stage = "declining"
        else:
            stage = "stable"

        total_qty = sum(qty_series)
        result.append({
            "sku_code":    code,
            "description": sd["description"],
            "category":    sd["category"],
            "stage":       stage,
            "slope_pct":   slope_pct,
            "total_qty":   round(total_qty, 1),
            "avg_monthly": round(total_qty / n, 1) if n else 0,
            "series":      qty_series,
            "months":      all_months,
        })

    summary = {s: sum(1 for r in result if r["stage"] == s)
               for s in ("new", "growing", "stable", "declining", "dead")}
    result.sort(key=lambda x: {"growing": 0, "new": 1, "stable": 2, "declining": 3, "dead": 4}.get(x["stage"], 9))

    return {"skus": result, "summary": summary, "all_months": all_months}


# ===========================================================================
# Phase 4 — Ingredient-level BoM composition
# ===========================================================================

def _bom_ensure_and_seed():
    """Create bom_ingredients table if missing; seed from Excel files if empty."""
    try:
        with get_db() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS bom_ingredients (
                  sku_id           INTEGER NOT NULL REFERENCES skus(sku_id),
                  seq              INTEGER NOT NULL,
                  ingredient_code  TEXT,
                  ingredient_name  TEXT NOT NULL,
                  qty_per_serving  REAL,
                  unit             TEXT,
                  price_per_unit   REAL,
                  ingredient_cost  REAL NOT NULL,
                  ingredient_type  TEXT,
                  source_file      TEXT,
                  PRIMARY KEY (sku_id, seq)
                );
                CREATE INDEX IF NOT EXISTS idx_bom_sku  ON bom_ingredients(sku_id);
                CREATE INDEX IF NOT EXISTS idx_bom_code ON bom_ingredients(ingredient_code);
            """)
            con.commit()
            n = con.execute("SELECT COUNT(*) FROM bom_ingredients").fetchone()[0]
        if n == 0:
            try:
                import importlib.util, sys
                _ib_path = HERE / "ingest_bom.py"
                if not _ib_path.exists():
                    return
                spec = importlib.util.spec_from_file_location("ingest_bom", str(_ib_path))
                _ib = importlib.util.module_from_spec(spec)
                sys.modules["ingest_bom"] = _ib
                spec.loader.exec_module(_ib)
                stats = _ib.ingest()
                print(f"[bom] auto-seeded: {stats}")
            except Exception as e:
                print(f"[bom] auto-seed failed: {e}")
    except Exception as e:
        print(f"[bom] ensure_table failed: {e}")


def _sku_cost_monthly_ensure():
    """Create sku_cost_monthly table (Phase 8) if missing."""
    try:
        with get_db() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS sku_cost_monthly (
                  sku_id        INTEGER NOT NULL REFERENCES skus(sku_id),
                  period_month  TEXT    NOT NULL,
                  unit_cost     REAL    NOT NULL,
                  notes         TEXT,
                  updated_at    TEXT    DEFAULT CURRENT_TIMESTAMP,
                  PRIMARY KEY (sku_id, period_month)
                );
                CREATE INDEX IF NOT EXISTS idx_skucm_period ON sku_cost_monthly(period_month);
                CREATE INDEX IF NOT EXISTS idx_skucm_sku    ON sku_cost_monthly(sku_id);
            """)
            con.commit()
    except Exception as e:
        print(f"[sku_cost_monthly] ensure_table failed: {e}")


@app.on_event("startup")
def _bom_startup():
    _bom_ensure_and_seed()
    _sku_cost_monthly_ensure()


# Benchmarks for ingredient-type mix (used by UI)
BOM_TYPE_BENCHMARKS = {
    "ayce_buffet": {
        "label": "Top AYCE buffet (Japan/KR)",
        "protein":   [40, 50],
        "carb":      [10, 15],
        "veg":       [15, 25],
        "sauce":     [5, 10],
        "oil":       [3, 8],
        "seasoning": [2, 5],
        "other":     [5, 10],
    },
    "fine_dining": {
        "label": "Fine-dining Japanese",
        "protein":   [50, 60],
        "carb":      [5, 10],
        "veg":       [15, 20],
        "sauce":     [5, 10],
        "oil":       [3, 8],
        "seasoning": [2, 5],
        "other":     [3, 10],
    },
}


@app.get("/api/bom/sku-list")
@ttl_cache
def bom_sku_list():
    """All SKUs that have BoM ingredient data, with menu name and total recipe cost."""
    with get_db() as con:
        rows = con.execute("""
            SELECT s.sku_code,
                   s.description,
                   ROUND(SUM(b.ingredient_cost), 2) AS total_cost,
                   COUNT(*)                           AS n_ingredients
              FROM bom_ingredients b
              JOIN skus s ON s.sku_id = b.sku_id
             GROUP BY s.sku_id
             ORDER BY s.sku_code
        """).fetchall()
    out = [{
        "sku_code":      r["sku_code"],
        "description":   r["description"] or "",
        "total_cost":    float(r["total_cost"] or 0),
        "n_ingredients": int(r["n_ingredients"] or 0),
    } for r in rows]
    return {"count": len(out), "skus": out}


@app.get("/api/bom/composition")
@ttl_cache
def bom_composition(sku: str = Query(..., description="SKU code, e.g. B0005")):
    """Single-SKU ingredient breakdown."""
    sku = (sku or "").strip().upper()
    with get_db() as con:
        srow = con.execute(
            "SELECT sku_id, sku_code, description FROM skus WHERE sku_code = ?",
            (sku,),
        ).fetchone()
        if srow is None:
            raise HTTPException(404, f"SKU {sku} not found")
        rows = con.execute("""
            SELECT seq, ingredient_code, ingredient_name, qty_per_serving, unit,
                   price_per_unit, ingredient_cost, ingredient_type, source_file
              FROM bom_ingredients
             WHERE sku_id = ?
             ORDER BY seq
        """, (srow["sku_id"],)).fetchall()
    if not rows:
        raise HTTPException(404, f"No BoM data for SKU {sku}")
    total_cost = sum(float(r["ingredient_cost"] or 0) for r in rows)
    ingredients = []
    type_totals: Dict[str, float] = {}
    for r in rows:
        cost = float(r["ingredient_cost"] or 0)
        t = r["ingredient_type"] or "other"
        type_totals[t] = type_totals.get(t, 0.0) + cost
        ingredients.append({
            "seq":   int(r["seq"]),
            "code":  r["ingredient_code"] or "",
            "name":  r["ingredient_name"] or "",
            "qty":   float(r["qty_per_serving"] or 0),
            "unit":  r["unit"] or "",
            "price_per_unit": float(r["price_per_unit"] or 0),
            "cost":  round(cost, 4),
            "pct":   round(100.0 * cost / total_cost, 2) if total_cost > 0 else 0,
            "type":  t,
        })
    by_type = {k: round(100.0 * v / total_cost, 2) if total_cost > 0 else 0
               for k, v in type_totals.items()}
    by_type_cost = {k: round(v, 2) for k, v in type_totals.items()}
    return {
        "sku_code":    srow["sku_code"],
        "description": srow["description"] or "",
        "total_cost":  round(total_cost, 2),
        "ingredients": ingredients,
        "by_type":     by_type,
        "by_type_cost": by_type_cost,
        "source_file": rows[0]["source_file"] if rows else None,
    }


@app.get("/api/bom/consumption-analysis")
@ttl_cache
def bom_consumption_analysis(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    limit:      int           = Query(50),
):
    """
    Period rollup of ingredient consumption across all sold menus.
    Joins sales_sku_monthly x bom_ingredients.
    """
    filters = ["b.name != 'ALL'"]
    params:  Dict = {}
    _month_range_filter(filters, params, month_from, month_to)
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    where = " AND ".join(filters)

    sql = """
        SELECT
            COALESCE(NULLIF(bi.ingredient_code, ''), '__' || bi.ingredient_name) AS ing_key,
            MAX(bi.ingredient_code)   AS code,
            MAX(bi.ingredient_name)   AS name,
            MAX(bi.ingredient_type)   AS type,
            MAX(bi.unit)              AS unit,
            SUM(ssm.qty * bi.qty_per_serving) AS total_qty,
            SUM(ssm.qty * bi.ingredient_cost) AS total_cost,
            COUNT(DISTINCT ssm.sku_id)        AS n_menus
          FROM sales_sku_monthly ssm
          JOIN branches b         ON b.branch_id = ssm.branch_id
          JOIN bom_ingredients bi ON bi.sku_id   = ssm.sku_id
         WHERE """ + where + """
         GROUP BY ing_key
         ORDER BY total_cost DESC
    """
    with get_db() as con:
        rows = con.execute(sql, params).fetchall()

    grand_total = sum(float(r["total_cost"] or 0) for r in rows)
    all_rows = []
    type_totals: Dict[str, float] = {}
    for r in rows:
        cost = float(r["total_cost"] or 0)
        t = r["type"] or "other"
        type_totals[t] = type_totals.get(t, 0.0) + cost
        all_rows.append({
            "code":     r["code"] or "",
            "name":     r["name"] or "",
            "type":     t,
            "unit":     r["unit"] or "",
            "total_qty": round(float(r["total_qty"] or 0), 2),
            "total_cost": round(cost, 2),
            "pct":      round(100.0 * cost / grand_total, 2) if grand_total > 0 else 0,
            "n_menus":  int(r["n_menus"] or 0),
        })
    by_type = {k: round(100.0 * v / grand_total, 2) if grand_total > 0 else 0
               for k, v in type_totals.items()}
    by_type_cost = {k: round(v, 2) for k, v in type_totals.items()}

    bench = BOM_TYPE_BENCHMARKS["ayce_buffet"]
    bench_status = {}
    for t, pct in by_type.items():
        rng = bench.get(t)
        if not rng:
            bench_status[t] = {"status": "n/a", "range": None, "pct": pct}
            continue
        if rng[0] <= pct <= rng[1]:
            st = "in"
        elif pct < rng[0]:
            st = "under"
        else:
            st = "over"
        bench_status[t] = {"status": st, "range": rng, "pct": pct}

    return {
        "month_from": month_from,
        "month_to":   month_to,
        "branch":     branch,
        "grand_total_cost": round(grand_total, 2),
        "ingredients":      all_rows[:limit],
        "n_ingredients_total": len(all_rows),
        "by_type":          by_type,
        "by_type_cost":     by_type_cost,
        "benchmarks":       BOM_TYPE_BENCHMARKS,
        "benchmark_status": bench_status,
    }


# ════════════════════════════════════════════════════════════════════════════
# Phase 11 — Cost recalibration tool
# ════════════════════════════════════════════════════════════════════════════
@app.get("/api/cost/recalibration")
@ttl_cache
def cost_recalibration(
    target_pct:  float = Query(65.0, description="Target food cost % (typical AYCE: 55-70)"),
    limit:       int   = Query(50,   description="Top N cost-driver SKUs to surface (default 50)"),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
):
    """Top-N cost-driver SKUs with recalibration suggestions.

    Returns each SKU's current unit_cost, total consumption qty, current COGS
    contribution, % of total COGS, average à la carte selling price (sanity check),
    and a suggested new unit_cost based on proportional scaling so overall food
    cost % hits target_pct.
    """
    # Phase 14b: use _cogs_case so monthly overrides are honored.
    cogs_expr = _cogs_case(ssm='ssm', sk='sk', sc='sc')
    filters = [f"{_effective_cost_subquery(ssm='ssm', sc='sc')} > 0",
               f"sk.sku_code NOT IN {_EASY_TIER_CODES_SQL}",
               "sk.sku_code != 'SERVICECHARGE'"]
    params: dict = {}
    if month_from:
        filters.append("ssm.period_month >= :mf")
        params["mf"] = month_from
    if month_to:
        filters.append("ssm.period_month <= :mt")
        params["mt"] = month_to
    where = "WHERE " + " AND ".join(filters)

    totals_filters = []
    if month_from: totals_filters.append("ssm.period_month >= :mf")
    if month_to:   totals_filters.append("ssm.period_month <= :mt")
    totals_where = ("WHERE " + " AND ".join(totals_filters)) if totals_filters else ""

    with get_db() as con:
        totals = con.execute(f"""
            SELECT
              SUM(ssm.subtotal_ex_vat) AS rev,
              SUM(CASE
                WHEN sk.sku_code IN {_EASY_TIER_CODES_SQL} THEN 0
                ELSE {cogs_expr}
              END) AS cogs
            FROM sales_sku_monthly ssm
            JOIN skus sk ON sk.sku_id = ssm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {totals_where}
        """, params).fetchone()

        rev  = totals["rev"]  or 0
        cogs = totals["cogs"] or 0
        target_cogs = rev * target_pct / 100
        scale = (target_cogs / cogs) if cogs > 0 else 1.0

        # Phase 18d deep-verify fix: current_unit_cost should reflect EFFECTIVE
        # cost (monthly override → default fallback), not just sku_cost default.
        # Derived as SUM(cogs) / SUM(qty) which is the weighted-avg effective
        # unit_cost over the selected period.
        rows = con.execute(f"""
            SELECT sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   sc.unit_cost AS default_unit_cost,
                   SUM(ssm.qty) AS qty,
                   SUM(ssm.subtotal_ex_vat) AS rev,
                   SUM({cogs_expr}) AS cogs,
                   CASE WHEN SUM(ssm.qty) > 0 THEN SUM({cogs_expr}) / SUM(ssm.qty) ELSE sc.unit_cost END AS current_unit_cost,
                   SUM(ssm.subtotal_ex_vat) / NULLIF(SUM(ssm.qty), 0) AS avg_sell
            FROM sales_sku_monthly ssm
            JOIN skus sk ON sk.sku_id = ssm.sku_id
            JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY sk.sku_id
            ORDER BY SUM({cogs_expr}) DESC
            LIMIT :n
        """, {**params, "n": limit}).fetchall()

        skus = []
        for r in rows:
            d = dict(r)
            uc      = d["current_unit_cost"] or 0
            sku_qty = d["qty"] or 0
            sku_rev = d["rev"] or 0
            sku_cogs = d["cogs"] or 0
            suggested = round(uc * scale, 2)
            avg_sell = d["avg_sell"] or 0
            # Flag suspect: cost > avg_sell (means cost > what customer paid à la carte)
            # avg_sell ≈ 0 means buffet-consumed only; can't validate
            flag = None
            if avg_sell > 0 and uc > avg_sell * 0.9:
                flag = "cost ≥ 90% of sell price"
            elif avg_sell < 1 and sku_qty > 10000:
                flag = "high qty, no à la carte sales (buffet-only)"
            skus.append({
                "sku_code":            d["sku_code"],
                "description":         d["description"],
                "current_unit_cost":   round(uc, 2),
                "qty":                 int(sku_qty),
                "total_rev":           round(sku_rev, 0),
                "total_cogs":          round(sku_cogs, 0),
                "pct_of_total_cogs":   round(sku_cogs / cogs * 100, 2) if cogs > 0 else 0,
                "avg_sell_per_unit":   round(avg_sell, 2),
                "suggested_unit_cost": suggested,
                "flag":                flag,
            })

        # Rule 2: pair the cumulative total with a monthly trend.
        trend_rows = con.execute(f"""
            SELECT ssm.period_month AS period_month,
              SUM(ssm.subtotal_ex_vat) AS rev,
              SUM(CASE WHEN sk.sku_code IN {_EASY_TIER_CODES_SQL} THEN 0
                       ELSE {cogs_expr} END) AS cogs
            FROM sales_sku_monthly ssm
            JOIN skus sk ON sk.sku_id = ssm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {totals_where}
            GROUP BY ssm.period_month
            ORDER BY ssm.period_month
        """, params).fetchall()
        monthly_trend = []
        for tr in trend_rows:
            mrev = float(tr["rev"] or 0); mcogs = float(tr["cogs"] or 0)
            monthly_trend.append({
                "period_month":  tr["period_month"],
                "revenue":       round(mrev, 2),
                "cogs":          round(mcogs, 2),
                "food_cost_pct": round(mcogs / mrev * 100, 2) if mrev > 0 else None,
            })

        return {
            "current_revenue":         round(rev, 0),
            "current_cogs":            round(cogs, 0),
            "current_food_cost_pct":   round(cogs / rev * 100, 2) if rev > 0 else 0,
            "target_food_cost_pct":    target_pct,
            "target_cogs":             round(target_cogs, 0),
            "scale_factor":            round(scale, 4),
            "cogs_reduction_needed":   round(cogs - target_cogs, 0),
            "top_n_covers_pct":        round(sum(s["pct_of_total_cogs"] for s in skus), 1),
            "skus":                    skus,
            "monthly_trend":           monthly_trend,
            "month_from":              month_from,
            "month_to":                month_to,
        }


class CostUpdate(BaseModel):
    sku_code:  str
    unit_cost: float


@app.post("/api/cost/bulk-update")
def cost_bulk_update(updates: list[CostUpdate]):
    """Apply unit_cost updates to multiple SKUs. Returns rows updated + any errors.
    Used by the Phase 11 recalibration UI to apply user edits in one click."""
    written = 0
    unknown: list = []
    with get_db() as con:
        for u in updates:
            row = con.execute(
                "SELECT sku_id FROM skus WHERE sku_code = ?", (u.sku_code,)
            ).fetchone()
            if not row:
                unknown.append(u.sku_code)
                continue
            con.execute(
                "INSERT OR REPLACE INTO sku_cost (sku_id, unit_cost, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                (row["sku_id"], u.unit_cost),
            )
            written += 1
        con.commit()
    invalidate_cache()
    return {"written": written, "unknown_skus": unknown}


# ---------------------------------------------------------------------------
# /api/bom/aliases — Phase 9 BoM-code → DB-SKU mapping
# ---------------------------------------------------------------------------
@app.get("/api/bom/aliases")
def bom_aliases():
    """List all BoM-code aliases mapping unmatched BoM codes to DB SKUs."""
    with get_db() as con:
        # Ensure table exists so first call after a clean install doesn't 500
        con.execute(
            "CREATE TABLE IF NOT EXISTS bom_sku_alias ("
            "bom_sku_code TEXT PRIMARY KEY, "
            "resolved_sku_id INTEGER NOT NULL REFERENCES skus(sku_id), "
            "match_method TEXT, confidence REAL, notes TEXT)"
        )
        rows = con.execute("""
            SELECT a.bom_sku_code,
                   a.resolved_sku_id,
                   s.sku_code  AS resolved_sku_code,
                   COALESCE(s.description,'') AS resolved_description,
                   a.match_method,
                   a.confidence,
                   COALESCE(a.notes,'') AS notes
              FROM bom_sku_alias a
              JOIN skus s ON s.sku_id = a.resolved_sku_id
             ORDER BY a.bom_sku_code
        """).fetchall()
    return {"count": len(rows), "aliases": [dict(r) for r in rows]}


class BomAliasUpdate(BaseModel):
    resolved_sku_code: str
    notes: Optional[str] = None


@app.put("/api/bom/aliases/{bom_code}")
def bom_alias_upsert(bom_code: str, body: BomAliasUpdate):
    """Manually map a BoM Excel code to a DB SKU code. Marks the row as 'manual'."""
    code = (bom_code or "").strip().upper()
    if not code:
        raise HTTPException(400, "bom_code required")
    target = (body.resolved_sku_code or "").strip().upper()
    if not target:
        raise HTTPException(400, "resolved_sku_code required")
    with get_db() as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS bom_sku_alias ("
            "bom_sku_code TEXT PRIMARY KEY, "
            "resolved_sku_id INTEGER NOT NULL REFERENCES skus(sku_id), "
            "match_method TEXT, confidence REAL, notes TEXT)"
        )
        row = con.execute(
            "SELECT sku_id FROM skus WHERE sku_code = ?", (target,)
        ).fetchone()
        if not row:
            raise HTTPException(404, f"SKU {target} not found")
        con.execute(
            "INSERT OR REPLACE INTO bom_sku_alias "
            "(bom_sku_code, resolved_sku_id, match_method, confidence, notes) "
            "VALUES (?, ?, 'manual', 1.0, ?)",
            (code, row["sku_id"], body.notes or ""),
        )
        con.commit()
    invalidate_cache()
    return {"bom_sku_code": code, "resolved_sku_code": target, "match_method": "manual"}


# ════════════════════════════════════════════════════════════════════════════
# Phase 12 — Per-branch BoM cost analysis
# ════════════════════════════════════════════════════════════════════════════
# Combined whole-restaurant view for a single branch. Shows:
#   • Branch totals (revenue, cogs, gp, gp%, food cost%)
#   • Monthly trend
#   • Top cost-driver menus with monthly cogs sparkline
#   • Ingredient-level drill-down (joining sales × bom_ingredients,
#     using bom_sku_alias as a fallback for SKUs without a direct recipe)
#   • Cost concentration (top-5/10/20 share of branch COGS)
#
# This is the back-end for the "🔬 BoM รายสาขา" sidebar tab.
@app.get("/api/branch-bom/analysis")
@ttl_cache
def branch_bom_analysis(
    branch:     str           = Query(..., description="Branch name, or 'ALL' for chain-wide aggregate"),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    top_n:      int           = Query(50, description="How many top menus to return (default 50)"),
    granularity: str          = Query("month", description="day | week | month — bucket size for the trend array"),
):
    """Per-branch BoM cost analysis (or chain-wide if branch='ALL').

    The `trend` array (always returned) honours the granularity param:
      • month  — one entry per ``YYYY-MM`` (mirrors `monthly_trend`).
      • week   — ISO weeks ``YYYY-Www``; daily COGS pro-rated by daily tx revenue.
      • day    — one entry per ``YYYY-MM-DD``; same pro-ration pattern as `/api/buffet/gp`.

    `monthly_trend` is preserved (always monthly) for backwards compatibility.

    Phase 16: when `branch='ALL'` (or 'all'), aggregates across ALL active
    branches — chain-wide overview. Hides per-branch filter, returns same
    response shape with `branch: "ALL (เครือ)"`.
    """
    if not branch:
        raise HTTPException(400, "branch is required ('ALL' for chain-wide)")
    if granularity not in ("day", "week", "month"):
        granularity = "month"

    is_chain = branch.upper() == "ALL"

    # Effective COGS / costed-rev for the branch (or chain-wide) ------------
    if is_chain:
        filters = ["COALESCE(b.active,1) = 1", "b.name != 'ALL'"]
        params: dict = {}
    else:
        filters = ["b.name = :branch", "b.name != 'ALL'"]
        params = {"branch": branch}
    _month_range_filter(filters, params, month_from, month_to, None, col="ssm.period_month")
    where = "WHERE " + " AND ".join(filters)

    cogs_expr        = _cogs_case('ssm', 'sk', 'sc')
    costed_rev_expr  = _costed_rev_case('ssm', 'sk', 'sc')

    with get_db() as con:
        # Verify branch exists (skip for chain-wide mode)
        if not is_chain:
            brow = con.execute(
                "SELECT branch_id, name FROM branches WHERE name = ?", (branch,)
            ).fetchone()
            if brow is None:
                raise HTTPException(404, f"Branch '{branch}' not found")

        # ── 1. Branch totals ──────────────────────────────────────────────
        totals_row = con.execute(f"""
            SELECT
              COALESCE(SUM(ssm.subtotal_ex_vat), 0)        AS revenue_ex,
              COALESCE(SUM(ssm.total_inc_vat),  0)        AS pos_total_inc_vat_diagnostic,
              COALESCE(SUM({cogs_expr}), 0)                AS cogs,
              COALESCE(SUM({costed_rev_expr}), 0)          AS costed_rev_ex,
              COUNT(DISTINCT ssm.sku_id)                   AS menus_total,
              COUNT(DISTINCT CASE WHEN {cogs_expr} > 0 THEN ssm.sku_id END) AS menus_with_cost
            FROM sales_sku_monthly ssm
            JOIN branches b   ON b.branch_id = ssm.branch_id
            JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
        """, params).fetchone()

        # ── 2. Monthly trend ──────────────────────────────────────────────
        trend_rows = con.execute(f"""
            SELECT ssm.period_month AS m,
                   COALESCE(SUM(ssm.subtotal_ex_vat), 0)        AS revenue_ex,
                   COALESCE(SUM({cogs_expr}), 0)                AS cogs,
                   COALESCE(SUM({costed_rev_expr}), 0)          AS costed_rev_ex
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY ssm.period_month
            ORDER BY ssm.period_month
        """, params).fetchall()

        # ── 3. Top menus by COGS (limit 30) ───────────────────────────────
        top_menu_rows = con.execute(f"""
            SELECT ssm.sku_id,
                   sk.sku_code,
                   COALESCE(sk.description,'') AS description,
                   COALESCE(SUM(ssm.qty), 0)                       AS qty,
                   COALESCE(SUM(ssm.subtotal_ex_vat), 0)           AS revenue,
                   COALESCE(SUM({cogs_expr}), 0)                   AS total_cogs,
                   CASE WHEN SUM(ssm.qty) > 0
                        THEN SUM({cogs_expr}) / SUM(ssm.qty)
                        ELSE 0 END                                  AS avg_unit_cost
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY ssm.sku_id
            HAVING total_cogs > 0
            ORDER BY total_cogs DESC
            LIMIT :top_n
        """, {**params, "top_n": int(top_n)}).fetchall()

        top_sku_ids = [r["sku_id"] for r in top_menu_rows]

        # Monthly cogs per top sku — for sparklines
        monthly_by_sku: Dict[int, list] = {sid: [] for sid in top_sku_ids}
        if top_sku_ids:
            placeholders = ",".join(":sid" + str(i) for i in range(len(top_sku_ids)))
            mp = {**params, **{"sid" + str(i): sid for i, sid in enumerate(top_sku_ids)}}
            mrows = con.execute(f"""
                SELECT ssm.sku_id, ssm.period_month AS m,
                       COALESCE(SUM({cogs_expr}), 0) AS cogs
                  FROM sales_sku_monthly ssm
                  JOIN branches b   ON b.branch_id = ssm.branch_id
                  JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
                  LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
                {where} AND ssm.sku_id IN ({placeholders})
                GROUP BY ssm.sku_id, ssm.period_month
                ORDER BY ssm.sku_id, ssm.period_month
            """, mp).fetchall()
            for r in mrows:
                monthly_by_sku.setdefault(r["sku_id"], []).append({
                    "month": r["m"],
                    "cogs":  round(float(r["cogs"] or 0), 2),
                })

        # has_bom_recipe flag — does this sku_id have a direct or aliased recipe?
        bom_flag_rows = con.execute(f"""
            SELECT s.sku_id,
              CASE WHEN EXISTS (SELECT 1 FROM bom_ingredients b WHERE b.sku_id = s.sku_id)
                    OR EXISTS (SELECT 1 FROM bom_sku_alias a WHERE a.bom_sku_code = s.sku_code)
                   THEN 1 ELSE 0 END AS has_bom
              FROM skus s WHERE s.sku_id IN ({placeholders if top_sku_ids else "NULL"})
        """, {"sid" + str(i): sid for i, sid in enumerate(top_sku_ids)} if top_sku_ids else {}).fetchall() if top_sku_ids else []
        has_bom_map = {r["sku_id"]: bool(r["has_bom"]) for r in bom_flag_rows}

        # ── 4. Ingredient-level drill-down ────────────────────────────────
        # Effective BoM sku id = direct match, else aliased.
        # We sum (sales qty × ingredient_cost) — same approach as
        # /api/bom/consumption-analysis but using the alias fallback.
        ing_rows = con.execute(f"""
            WITH eff AS (
              SELECT ssm.sku_id   AS sales_sku_id,
                     ssm.qty      AS qty,
                     COALESCE(
                       (SELECT bi.sku_id FROM bom_ingredients bi WHERE bi.sku_id = ssm.sku_id LIMIT 1),
                       (SELECT a.resolved_sku_id
                          FROM bom_sku_alias a
                          JOIN skus s ON s.sku_id = ssm.sku_id
                         WHERE a.bom_sku_code = s.sku_code LIMIT 1)
                     ) AS bom_sku_id
                FROM sales_sku_monthly ssm
                JOIN branches b   ON b.branch_id = ssm.branch_id
                JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
                LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
              {where}
            )
            SELECT
              COALESCE(NULLIF(bi.ingredient_code,''), '__' || bi.ingredient_name) AS ing_key,
              MAX(bi.ingredient_code)              AS code,
              MAX(bi.ingredient_name)              AS name,
              MAX(bi.ingredient_type)              AS type,
              MAX(bi.unit)                         AS unit,
              SUM(eff.qty * bi.qty_per_serving)    AS total_qty,
              SUM(eff.qty * bi.ingredient_cost)    AS total_cost,
              COUNT(DISTINCT eff.sales_sku_id)     AS n_menus
              FROM eff
              JOIN bom_ingredients bi ON bi.sku_id = eff.bom_sku_id
             WHERE eff.bom_sku_id IS NOT NULL
             GROUP BY ing_key
             ORDER BY total_cost DESC
             LIMIT 25
        """, params).fetchall()

        # For each top ingredient: list the menus (sku_codes) using it
        ing_menus: Dict[str, list] = {}
        if ing_rows:
            ing_key_list = [r["ing_key"] for r in ing_rows]
            ph = ",".join(":ik" + str(i) for i in range(len(ing_key_list)))
            ikp = {**params, **{"ik" + str(i): k for i, k in enumerate(ing_key_list)}}
            menu_link_rows = con.execute(f"""
                WITH eff AS (
                  SELECT ssm.sku_id AS sales_sku_id, sk.sku_code AS sku_code,
                         ssm.qty AS qty,
                         COALESCE(
                           (SELECT bi.sku_id FROM bom_ingredients bi WHERE bi.sku_id = ssm.sku_id LIMIT 1),
                           (SELECT a.resolved_sku_id
                              FROM bom_sku_alias a
                              JOIN skus s ON s.sku_id = ssm.sku_id
                             WHERE a.bom_sku_code = s.sku_code LIMIT 1)
                         ) AS bom_sku_id
                    FROM sales_sku_monthly ssm
                    JOIN branches b   ON b.branch_id = ssm.branch_id
                    JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
                    LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
                  {where}
                )
                SELECT
                  COALESCE(NULLIF(bi.ingredient_code,''), '__' || bi.ingredient_name) AS ing_key,
                  eff.sku_code AS sku_code,
                  SUM(eff.qty * bi.ingredient_cost) AS cost
                  FROM eff
                  JOIN bom_ingredients bi ON bi.sku_id = eff.bom_sku_id
                 WHERE eff.bom_sku_id IS NOT NULL
                   AND COALESCE(NULLIF(bi.ingredient_code,''), '__' || bi.ingredient_name) IN ({ph})
                 GROUP BY ing_key, eff.sku_code
            """, ikp).fetchall()
            # bucket by ing_key, sort each by cost desc, keep top 5 sku_codes
            from collections import defaultdict as _dd
            tmp = _dd(list)
            for r in menu_link_rows:
                tmp[r["ing_key"]].append((r["sku_code"], float(r["cost"] or 0)))
            for k, lst in tmp.items():
                lst.sort(key=lambda x: -x[1])
                ing_menus[k] = [code for code, _ in lst[:5]]

        # ── 5. Total # of menus with a BoM recipe (for coverage stat) ─────
        coverage_row = con.execute(f"""
            WITH eff AS (
              SELECT DISTINCT ssm.sku_id,
                COALESCE(
                  (SELECT bi.sku_id FROM bom_ingredients bi WHERE bi.sku_id = ssm.sku_id LIMIT 1),
                  (SELECT a.resolved_sku_id
                     FROM bom_sku_alias a
                     JOIN skus s ON s.sku_id = ssm.sku_id
                    WHERE a.bom_sku_code = s.sku_code LIMIT 1)
                ) AS bom_sku_id
                FROM sales_sku_monthly ssm
                JOIN branches b   ON b.branch_id = ssm.branch_id
              {where}
            )
            SELECT SUM(CASE WHEN bom_sku_id IS NOT NULL THEN 1 ELSE 0 END) AS with_bom,
                   COUNT(*) AS total
              FROM eff
        """, params).fetchone()

    # ── Assemble response ─────────────────────────────────────────────────
    revenue_ex    = float(totals_row["revenue_ex"]    or 0)
    revenue_inc   = float(totals_row["pos_total_inc_vat_diagnostic"] or 0)
    cogs          = float(totals_row["cogs"]          or 0)
    costed_rev_ex = float(totals_row["costed_rev_ex"] or 0)
    gp            = costed_rev_ex - cogs
    gp_pct        = (gp   / costed_rev_ex * 100) if costed_rev_ex > 0 else None
    food_pct      = (cogs / costed_rev_ex * 100) if costed_rev_ex > 0 else None

    # period
    months_list   = [r["m"] for r in trend_rows]
    n_months      = len(months_list)

    monthly_trend = []
    for r in trend_rows:
        m_cogs      = float(r["cogs"] or 0)
        m_costed    = float(r["costed_rev_ex"] or 0)
        m_rev       = float(r["revenue_ex"] or 0)
        m_gp        = m_costed - m_cogs
        m_gp_pct    = (m_gp   / m_costed * 100) if m_costed > 0 else None
        m_food_pct  = (m_cogs / m_costed * 100) if m_costed > 0 else None
        monthly_trend.append({
            "month":         r["m"],
            "revenue":       round(m_rev, 2),
            "cogs":          round(m_cogs, 2),
            "gp":            round(m_gp, 2),
            "gp_pct":        round(m_gp_pct, 1)   if m_gp_pct   is not None else None,
            "food_cost_pct": round(m_food_pct, 1) if m_food_pct is not None else None,
        })

    top_menus = []
    for r in top_menu_rows:
        tc      = float(r["total_cogs"] or 0)
        pct     = (tc / cogs * 100) if cogs > 0 else 0
        top_menus.append({
            "sku_id":            r["sku_id"],
            "sku_code":          r["sku_code"],
            "description":       r["description"],
            "qty":               round(float(r["qty"] or 0), 2),
            "revenue":           round(float(r["revenue"] or 0), 2),
            "unit_cost":         round(float(r["avg_unit_cost"] or 0), 4),
            "total_cogs":        round(tc, 2),
            "pct_of_branch_cogs": round(pct, 2),
            "monthly_cogs":      monthly_by_sku.get(r["sku_id"], []),
            "has_bom_recipe":    has_bom_map.get(r["sku_id"], False),
        })

    # concentration
    cumulative = 0.0
    cum_at = {}
    for i, m in enumerate(top_menus, start=1):
        cumulative += m["total_cogs"]
        cum_at[i] = cumulative
    def _share(n):
        if cogs <= 0 or n not in cum_at: return None
        # Pad to n: if fewer than n menus, use last cumulative.
        if not cum_at: return None
        max_n = max(cum_at)
        val = cum_at.get(n, cum_at[max_n])
        return round(val / cogs * 100, 2)
    concentration = {
        "top_5_menus_pct_of_cogs":  _share(5),
        "top_10_menus_pct_of_cogs": _share(10),
        "top_20_menus_pct_of_cogs": _share(20),
    }

    top_ingredients = []
    cost_by_type: Dict[str, float] = {}
    for r in ing_rows:
        c    = float(r["total_cost"] or 0)
        t    = r["type"] or "other"
        cost_by_type[t] = cost_by_type.get(t, 0.0) + c
        top_ingredients.append({
            "ingredient_code":     r["code"] or "",
            "ingredient_name":     r["name"] or "",
            "ingredient_type":     t,
            "unit":                r["unit"] or "",
            "total_qty":           round(float(r["total_qty"] or 0), 2),
            "total_cost":          round(c, 2),
            "pct_of_branch_cogs":  round(c / cogs * 100, 2) if cogs > 0 else 0,
            "n_menus_using":       int(r["n_menus"] or 0),
            "top_menus":           ing_menus.get(r["ing_key"], []),
        })
    cost_by_type_out = {k: round(v, 2) for k, v in cost_by_type.items()}

    menus_with_recipe = int(coverage_row["with_bom"] or 0) if coverage_row else 0
    menus_total       = int(coverage_row["total"]    or 0) if coverage_row else 0

    # ── Granularity-aware trend array ────────────────────────────────────
    # Always include `trend` keyed by `bucket`. For month: mirrors monthly_trend.
    # For week/day: pro-rate each month's COGS by daily tx revenue fraction
    # (same pattern as /api/buffet/gp — SKU data is monthly-only so we have
    # no day-level COGS breakdown).
    trend: list = []
    if granularity == "month":
        for r in monthly_trend:
            trend.append({
                "bucket":        r["month"],
                "revenue":       r["revenue"],
                "cogs":          r["cogs"],
                "gp":            r["gp"],
                "gp_pct":        r["gp_pct"],
                "food_cost_pct": r["food_cost_pct"],
            })
    elif months_list:
        # 1. Fetch daily transaction revenue for this branch over the months
        tx_filters = ["b.name = :branch", "b.name != 'ALL'", "st.source = 'Easy'"]
        tx_params: dict = {"branch": branch}
        _month_range_filter(tx_filters, tx_params, month_from, month_to, None,
                            col="SUBSTR(st.sold_at,1,7)")
        tx_where = "WHERE " + " AND ".join(tx_filters)
        with get_db() as con:
            daily_rows = con.execute(f"""
                SELECT DATE(st.sold_at)               AS d,
                       SUBSTR(st.sold_at,1,7)         AS m,
                       SUM(COALESCE(st.food_subtotal,0)
                         + COALESCE(st.drink_subtotal,0)
                         + COALESCE(st.other_subtotal,0)) AS rev_tx
                  FROM sales_transaction st
                  JOIN branches b ON b.branch_id = st.branch_id
                {tx_where}
                GROUP BY DATE(st.sold_at)
                ORDER BY d
            """, tx_params).fetchall()

        # Index monthly cogs / costed_rev / rev from existing trend_rows
        m_index = {r["m"]: {
            "cogs":      float(r["cogs"]          or 0),
            "costed":    float(r["costed_rev_ex"] or 0),
            "rev_ex":    float(r["revenue_ex"]    or 0),
        } for r in trend_rows}

        # Aggregate monthly tx revenue
        m_tx_rev: dict = {}
        for r in daily_rows:
            m_tx_rev[r["m"]] = m_tx_rev.get(r["m"], 0.0) + float(r["rev_tx"] or 0)

        # Build daily buckets
        from datetime import date as _date_cls
        def _week_bucket(date_str: str) -> str:
            d = _date_cls.fromisoformat(date_str)
            iso = d.isocalendar()
            return f"{iso[0]}-W{iso[1]:02d}"

        buckets: dict = {}
        for r in daily_rows:
            mo = r["m"]
            mi = m_index.get(mo)
            if not mi:
                continue
            month_tx_rev = m_tx_rev.get(mo, 0.0)
            if month_tx_rev <= 0:
                continue
            frac = float(r["rev_tx"] or 0) / month_tx_rev
            day_cogs   = mi["cogs"]   * frac
            day_costed = mi["costed"] * frac
            day_rev    = mi["rev_ex"] * frac
            key = r["d"] if granularity == "day" else _week_bucket(r["d"])
            b = buckets.setdefault(key, {"cogs": 0.0, "costed": 0.0, "rev": 0.0})
            b["cogs"]   += day_cogs
            b["costed"] += day_costed
            b["rev"]    += day_rev

        for k in sorted(buckets.keys()):
            v   = buckets[k]
            bgp = v["costed"] - v["cogs"]
            gpp = (bgp / v["costed"] * 100) if v["costed"] > 0 else None
            fcp = (v["cogs"] / v["costed"] * 100) if v["costed"] > 0 else None
            trend.append({
                "bucket":        k,
                "revenue":       round(v["rev"],   2),
                "cogs":          round(v["cogs"],  2),
                "gp":            round(bgp,        2),
                "gp_pct":        round(gpp, 1) if gpp is not None else None,
                "food_cost_pct": round(fcp, 1) if fcp is not None else None,
            })

    return {
        "branch": ("ALL (รวมเครือ)" if is_chain else branch),
        "is_chain": is_chain,
        "granularity": granularity,
        "period": {
            "month_from": month_from or (months_list[0]  if months_list else None),
            "month_to":   month_to   or (months_list[-1] if months_list else None),
            "months":     n_months,
        },
        "totals": {
            "revenue":              round(revenue_ex, 2),
            "pos_total_inc_vat_diagnostic": round(revenue_inc, 2),
            "cogs":                 round(cogs, 2),
            "costed_revenue_ex_vat": round(costed_rev_ex, 2),
            "gp":                   round(gp, 2),
            "gp_pct":               round(gp_pct, 1)   if gp_pct   is not None else None,
            "food_cost_pct":        round(food_pct, 1) if food_pct is not None else None,
            "menus_with_cost":      int(totals_row["menus_with_cost"] or 0),
            "menus_total":          int(totals_row["menus_total"] or 0),
            "menus_with_bom_recipe": menus_with_recipe,
            "menus_in_period":       menus_total,
        },
        "monthly_trend":            monthly_trend,
        "trend":                    trend,
        "top_menus_by_cost":        top_menus,
        "top_ingredients_by_cost":  top_ingredients,
        "cost_by_ingredient_type":  cost_by_type_out,
        "concentration":            concentration,
    }


# =============================================================================
# Phase 17c — business_type management
# =============================================================================
_VALID_BUSINESS_TYPES = {"buffet_tier", "buffet", "alacarte", "beverage",
                          "takeaway", "fee", "other"}


class BusinessTypeUpdate(BaseModel):
    business_type: str


@app.get("/api/skus/business-types")
def list_business_types():
    """Return valid business_type values + count per type from skus table."""
    with get_db() as con:
        rows = con.execute("""
            SELECT COALESCE(business_type,'other') AS bt, COUNT(*) AS n
              FROM skus
             GROUP BY COALESCE(business_type,'other')
        """).fetchall()
    counts = {r["bt"]: int(r["n"]) for r in rows}
    return {
        "valid_types": sorted(_VALID_BUSINESS_TYPES),
        "counts": counts,
        "total_skus": sum(counts.values()),
    }


@app.put("/api/skus/{sku_code}/business-type")
def update_sku_business_type(sku_code: str, body: BusinessTypeUpdate):
    bt = (body.business_type or "").strip().lower()
    if bt not in _VALID_BUSINESS_TYPES:
        raise HTTPException(400, f"invalid business_type; must be one of {sorted(_VALID_BUSINESS_TYPES)}")
    with get_db() as con:
        cur = con.execute("UPDATE skus SET business_type=? WHERE sku_code=?", (bt, sku_code))
        con.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, f"sku_code '{sku_code}' not found")
    invalidate_cache("get_cost_detail", "bom_cost_", "branch_bom_analysis")
    return {"sku_code": sku_code, "business_type": bt, "updated": True}


# =============================================================================
# Phase 17d — BoM Cost Analysis (4-mode page) + Simulator
# =============================================================================
def _bom_cost_period_filters(month_from, month_to, branches=None):
    """Build (where_sql, params) for chain-wide BoM cost queries."""
    filters = ["b.name != 'ALL'", "COALESCE(b.active,1) = 1"]
    params: dict = {}
    if branches:
        ph = ",".join(f":b{i}" for i in range(len(branches)))
        filters.append(f"b.name IN ({ph})")
        for i, br in enumerate(branches):
            params[f"b{i}"] = br
    _month_range_filter(filters, params, month_from, month_to, None, col="ssm.period_month")
    return "WHERE " + " AND ".join(filters), params


@app.get("/api/bom-cost/overview")
@ttl_cache
def bom_cost_overview(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    granularity: str          = Query("month", description="day | week | month"),
):
    """Chain-wide BoM cost overview - KPIs, heatmap, top-50 SKUs, top-30 ingredients,
    branch breakdown."""
    if granularity not in ("day", "week", "month"):
        granularity = "month"

    where, params = _bom_cost_period_filters(month_from, month_to)
    cogs_expr       = _cogs_case('ssm', 'sk', 'sc')
    costed_rev_expr = _costed_rev_case('ssm', 'sk', 'sc')

    with get_db() as con:
        tot = con.execute(f"""
            SELECT COALESCE(SUM(ssm.subtotal_ex_vat), 0)       AS revenue_ex,
                   COALESCE(SUM({cogs_expr}), 0)               AS cogs,
                   COALESCE(SUM({costed_rev_expr}), 0)         AS costed_rev_ex,
                   COUNT(DISTINCT ssm.sku_id)                  AS menus_total
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
        """, params).fetchone()

        hmap = con.execute(f"""
            SELECT b.name AS branch, ssm.period_month AS m,
                   COALESCE(SUM(ssm.subtotal_ex_vat), 0)  AS rev,
                   COALESCE(SUM({cogs_expr}), 0)          AS cogs,
                   COALESCE(SUM({costed_rev_expr}), 0)    AS costed
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY b.name, ssm.period_month
            ORDER BY b.name, ssm.period_month
        """, params).fetchall()

        top_rows = con.execute(f"""
            SELECT ssm.sku_id, sk.sku_code,
                   COALESCE(sk.description,'') AS description,
                   COALESCE(sk.business_type,'other') AS business_type,
                   COALESCE(SUM(ssm.qty), 0)            AS qty,
                   COALESCE(SUM({cogs_expr}), 0)        AS total_cogs,
                   CASE WHEN SUM(ssm.qty) > 0
                        THEN SUM({cogs_expr}) / SUM(ssm.qty)
                        ELSE 0 END                        AS avg_unit_cost
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY ssm.sku_id
            HAVING total_cogs > 0
            ORDER BY total_cogs DESC
            LIMIT 50
        """, params).fetchall()

        top_sku_ids = [r["sku_id"] for r in top_rows]
        sparkline_map: Dict[int, list] = {sid: [] for sid in top_sku_ids}
        if top_sku_ids:
            ph = ",".join(f":sid{i}" for i in range(len(top_sku_ids)))
            sp = {**params, **{f"sid{i}": sid for i, sid in enumerate(top_sku_ids)}}
            srows = con.execute(f"""
                SELECT ssm.sku_id, ssm.period_month AS m,
                       COALESCE(SUM({cogs_expr}), 0) AS cogs
                  FROM sales_sku_monthly ssm
                  JOIN branches b   ON b.branch_id = ssm.branch_id
                  JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
                  LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
                {where} AND ssm.sku_id IN ({ph})
                GROUP BY ssm.sku_id, ssm.period_month
                ORDER BY ssm.sku_id, ssm.period_month
            """, sp).fetchall()
            for r in srows:
                sparkline_map.setdefault(r["sku_id"], []).append({
                    "month": r["m"], "cogs": round(float(r["cogs"] or 0), 2)
                })

        branch_rows = con.execute(f"""
            SELECT b.name AS branch,
                   COALESCE(SUM(ssm.subtotal_ex_vat), 0)   AS rev,
                   COALESCE(SUM({cogs_expr}), 0)           AS cogs,
                   COALESCE(SUM({costed_rev_expr}), 0)     AS costed
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY b.name
            ORDER BY cogs DESC
        """, params).fetchall()

        ing_rows = con.execute(f"""
            WITH eff AS (
              SELECT ssm.sku_id AS sales_sku_id, ssm.qty AS qty,
                     COALESCE(
                       (SELECT bi.sku_id FROM bom_ingredients bi WHERE bi.sku_id = ssm.sku_id LIMIT 1),
                       (SELECT a.resolved_sku_id
                          FROM bom_sku_alias a
                          JOIN skus s ON s.sku_id = ssm.sku_id
                         WHERE a.bom_sku_code = s.sku_code LIMIT 1)
                     ) AS bom_sku_id
                FROM sales_sku_monthly ssm
                JOIN branches b   ON b.branch_id = ssm.branch_id
                JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
                LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
              {where}
            )
            SELECT COALESCE(NULLIF(bi.ingredient_code,''), '__' || bi.ingredient_name) AS ing_key,
                   MAX(bi.ingredient_name) AS name,
                   MAX(bi.ingredient_type) AS type,
                   SUM(eff.qty * bi.ingredient_cost) AS total_cost
              FROM eff
              JOIN bom_ingredients bi ON bi.sku_id = eff.bom_sku_id
             WHERE eff.bom_sku_id IS NOT NULL
             GROUP BY ing_key
             ORDER BY total_cost DESC
             LIMIT 30
        """, params).fetchall()

    revenue_ex    = float(tot["revenue_ex"]    or 0)
    cogs          = float(tot["cogs"]          or 0)
    costed_rev_ex = float(tot["costed_rev_ex"] or 0)
    gp            = costed_rev_ex - cogs
    gp_pct        = (gp   / costed_rev_ex * 100) if costed_rev_ex > 0 else None
    food_pct      = (cogs / costed_rev_ex * 100) if costed_rev_ex > 0 else None

    heatmap = []
    for r in hmap:
        rev    = float(r["rev"]    or 0)
        c      = float(r["cogs"]   or 0)
        costed = float(r["costed"] or 0)
        fc     = (c / costed * 100) if costed > 0 else None
        heatmap.append({
            "branch": r["branch"], "month": r["m"],
            "revenue": round(rev, 2), "cogs": round(c, 2),
            "gp": round(costed - c, 2),
            "food_cost_pct": round(fc, 1) if fc is not None else None,
        })

    top_skus = []
    for r in top_rows:
        tc  = float(r["total_cogs"] or 0)
        pct = (tc / cogs * 100) if cogs > 0 else 0
        top_skus.append({
            "sku_id":        r["sku_id"],
            "sku_code":      r["sku_code"],
            "description":   r["description"],
            "business_type": r["business_type"],
            "qty":           round(float(r["qty"] or 0), 2),
            "unit_cost":     round(float(r["avg_unit_cost"] or 0), 4),
            "total_cogs":    round(tc, 2),
            "pct_of_chain_cogs": round(pct, 2),
            "sparkline":     sparkline_map.get(r["sku_id"], []),
        })

    def _share(n):
        if cogs <= 0 or not top_skus: return None
        s = sum(x["total_cogs"] for x in top_skus[:n])
        return round(s / cogs * 100, 2)
    concentration = {
        "top_5_pct":  _share(5),
        "top_10_pct": _share(10),
        "top_20_pct": _share(20),
        "top_50_pct": _share(50),
    }

    branches_out = []
    for r in branch_rows:
        c      = float(r["cogs"]   or 0)
        costed = float(r["costed"] or 0)
        fc     = (c / costed * 100) if costed > 0 else None
        branches_out.append({
            "branch": r["branch"],
            "revenue": round(float(r["rev"] or 0), 2),
            "cogs": round(c, 2),
            "food_cost_pct": round(fc, 1) if fc is not None else None,
        })

    ings = []
    by_type: Dict[str, float] = {}
    for r in ing_rows:
        c = float(r["total_cost"] or 0)
        t = r["type"] or "other"
        by_type[t] = by_type.get(t, 0.0) + c
        ings.append({
            "ingredient_name": r["name"] or "",
            "ingredient_type": t,
            "total_cost":      round(c, 2),
            "pct_of_chain_cogs": round(c / cogs * 100, 2) if cogs > 0 else 0,
        })

    return {
        "granularity": granularity,
        "period": {"month_from": month_from, "month_to": month_to},
        "totals": {
            "revenue":       round(revenue_ex, 2),
            "cogs":          round(cogs, 2),
            "costed_revenue": round(costed_rev_ex, 2),
            "gp":            round(gp, 2),
            "gp_pct":        round(gp_pct, 1)   if gp_pct   is not None else None,
            "food_cost_pct": round(food_pct, 1) if food_pct is not None else None,
            "menus_total":   int(tot["menus_total"] or 0),
        },
        "heatmap":          heatmap,
        "top_skus":         top_skus,
        "concentration":    concentration,
        "branch_breakdown": branches_out,
        "top_ingredients":  ings,
        "cost_by_ingredient_type": {k: round(v, 2) for k, v in by_type.items()},
    }


@app.get("/api/bom-cost/compare")
@ttl_cache
def bom_cost_compare(
    branches:   str           = Query(..., description="Comma-separated branch names (2-5)"),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Per-branch comparison across 2-5 selected branches."""
    branch_list = [b.strip() for b in branches.split(",") if b.strip()]
    if not (2 <= len(branch_list) <= 5):
        raise HTTPException(400, "branches must contain 2 to 5 names")

    where, params = _bom_cost_period_filters(month_from, month_to, branch_list)
    cogs_expr       = _cogs_case('ssm', 'sk', 'sc')
    costed_rev_expr = _costed_rev_case('ssm', 'sk', 'sc')

    with get_db() as con:
        br_tot = con.execute(f"""
            SELECT b.name AS branch,
                   COALESCE(SUM(ssm.subtotal_ex_vat), 0)       AS rev,
                   COALESCE(SUM({cogs_expr}), 0)               AS cogs,
                   COALESCE(SUM({costed_rev_expr}), 0)         AS costed,
                   COUNT(DISTINCT CASE WHEN sc.unit_cost IS NULL THEN ssm.sku_id END) AS uncosted_skus
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY b.name
        """, params).fetchall()

        top_menus = con.execute(f"""
            SELECT branch, sku_code, description, total_cogs FROM (
              SELECT b.name AS branch, sk.sku_code,
                     COALESCE(sk.description,'') AS description,
                     COALESCE(SUM({cogs_expr}), 0) AS total_cogs,
                     ROW_NUMBER() OVER (PARTITION BY b.name ORDER BY SUM({cogs_expr}) DESC) AS rn
                FROM sales_sku_monthly ssm
                JOIN branches b   ON b.branch_id = ssm.branch_id
                JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
                LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
              {where}
              GROUP BY b.name, ssm.sku_id
            ) WHERE rn <= 3 ORDER BY branch, total_cogs DESC
        """, params).fetchall()

        ing_type = con.execute(f"""
            WITH eff AS (
              SELECT b.name AS branch, ssm.qty AS qty,
                     COALESCE(
                       (SELECT bi.sku_id FROM bom_ingredients bi WHERE bi.sku_id = ssm.sku_id LIMIT 1),
                       (SELECT a.resolved_sku_id
                          FROM bom_sku_alias a
                          JOIN skus s ON s.sku_id = ssm.sku_id
                         WHERE a.bom_sku_code = s.sku_code LIMIT 1)
                     ) AS bom_sku_id
                FROM sales_sku_monthly ssm
                JOIN branches b   ON b.branch_id = ssm.branch_id
                JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
                LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
              {where}
            )
            SELECT eff.branch, COALESCE(bi.ingredient_type,'other') AS type,
                   SUM(eff.qty * bi.ingredient_cost) AS cost
              FROM eff
              JOIN bom_ingredients bi ON bi.sku_id = eff.bom_sku_id
             WHERE eff.bom_sku_id IS NOT NULL
             GROUP BY eff.branch, type
        """, params).fetchall()

    out: Dict[str, dict] = {br: {
        "branch": br, "revenue": 0, "cogs": 0, "costed_revenue": 0,
        "gp": 0, "gp_pct": None, "food_cost_pct": None,
        "uncosted_skus": 0, "top_menus": [], "cost_by_ingredient_type": {},
    } for br in branch_list}

    for r in br_tot:
        if r["branch"] not in out: continue
        rev    = float(r["rev"]    or 0)
        c      = float(r["cogs"]   or 0)
        costed = float(r["costed"] or 0)
        gp     = costed - c
        out[r["branch"]].update({
            "revenue":        round(rev, 2),
            "cogs":           round(c, 2),
            "costed_revenue": round(costed, 2),
            "gp":             round(gp, 2),
            "gp_pct":         round(gp / costed * 100, 1) if costed > 0 else None,
            "food_cost_pct":  round(c / costed * 100, 1) if costed > 0 else None,
            "uncosted_skus":  int(r["uncosted_skus"] or 0),
        })

    for r in top_menus:
        br = r["branch"]
        if br in out:
            cogs_br = out[br]["cogs"]
            tc = float(r["total_cogs"] or 0)
            out[br]["top_menus"].append({
                "sku_code":    r["sku_code"],
                "description": r["description"],
                "total_cogs":  round(tc, 2),
                "pct_of_branch_cogs": round(tc / cogs_br * 100, 2) if cogs_br > 0 else 0,
            })

    for r in ing_type:
        br = r["branch"]
        if br in out:
            out[br]["cost_by_ingredient_type"][r["type"]] = round(float(r["cost"] or 0), 2)

    def _mean_of(metric):
        vals = [out[b][metric] for b in branch_list if out[b].get(metric) is not None]
        return (sum(vals) / len(vals)) if vals else None

    variance: dict = {}
    for metric in ("food_cost_pct", "gp_pct", "cogs", "revenue"):
        mean = _mean_of(metric)
        if mean is None or mean == 0:
            variance[metric] = {"mean": mean, "outliers": []}
            continue
        outliers = []
        for br in branch_list:
            v = out[br].get(metric)
            if v is None: continue
            dev = (v - mean) / abs(mean) * 100
            if abs(dev) >= 10:
                outliers.append({"branch": br, "value": v, "deviation_pct": round(dev, 1)})
        variance[metric] = {"mean": round(mean, 2), "outliers": outliers}

    return {
        "branches": branch_list,
        "period":   {"month_from": month_from, "month_to": month_to},
        "results":  [out[b] for b in branch_list],
        "variance": variance,
    }


@app.get("/api/bom-cost/simulator-base")
@ttl_cache
def bom_cost_simulator_base(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    limit:      int           = Query(200, ge=10, le=2000),
):
    """Return baseline chain stats + cost-driver SKUs so client can preview scenarios."""
    where, params = _bom_cost_period_filters(month_from, month_to)
    cogs_expr       = _cogs_case('ssm', 'sk', 'sc')
    costed_rev_expr = _costed_rev_case('ssm', 'sk', 'sc')

    with get_db() as con:
        tot = con.execute(f"""
            SELECT COALESCE(SUM(ssm.subtotal_ex_vat), 0)   AS revenue_ex,
                   COALESCE(SUM({cogs_expr}), 0)           AS cogs,
                   COALESCE(SUM({costed_rev_expr}), 0)     AS costed_rev
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
        """, params).fetchone()

        sku_rows = con.execute(f"""
            SELECT ssm.sku_id, sk.sku_code,
                   COALESCE(sk.description,'') AS description,
                   COALESCE(sk.business_type,'other') AS business_type,
                   COALESCE(SUM(ssm.qty), 0)            AS qty,
                   COALESCE(SUM({cogs_expr}), 0)        AS period_cogs,
                   CASE WHEN SUM(ssm.qty) > 0 THEN SUM({cogs_expr})/SUM(ssm.qty) ELSE 0 END
                     AS current_unit_cost
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY ssm.sku_id
            HAVING period_cogs > 0
            ORDER BY period_cogs DESC
            LIMIT :lim
        """, {**params, "lim": int(limit)}).fetchall()

    revenue   = float(tot["revenue_ex"] or 0)
    cogs      = float(tot["cogs"]       or 0)
    costed    = float(tot["costed_rev"] or 0)
    return {
        "period": {"month_from": month_from, "month_to": month_to},
        "baseline": {
            "revenue":        round(revenue, 2),
            "cogs":           round(cogs, 2),
            "costed_revenue": round(costed, 2),
            "gp":             round(costed - cogs, 2),
            "gp_pct":         round((costed - cogs) / costed * 100, 1) if costed > 0 else None,
            "food_cost_pct":  round(cogs / costed * 100, 1) if costed > 0 else None,
        },
        "skus": [{
            "sku_id":            r["sku_id"],
            "sku_code":          r["sku_code"],
            "description":       r["description"],
            "business_type":     r["business_type"],
            "qty":               round(float(r["qty"] or 0), 2),
            "period_cogs":       round(float(r["period_cogs"] or 0), 2),
            "current_unit_cost": round(float(r["current_unit_cost"] or 0), 4),
        } for r in sku_rows],
    }


class ScenarioOverride(BaseModel):
    sku_code: str
    unit_cost: float


class ScenarioCreate(BaseModel):
    name: str
    description: Optional[str] = None
    overrides: List[ScenarioOverride]


class SimulateBody(BaseModel):
    overrides: List[ScenarioOverride]
    month_from: Optional[str] = None
    month_to: Optional[str] = None


@app.post("/api/bom-cost/scenarios")
def create_scenario(body: ScenarioCreate):
    nm = (body.name or "").strip()
    if not nm:
        raise HTTPException(400, "name is required")
    with get_db() as con:
        cur = con.execute(
            "INSERT INTO cost_scenario(name, description) VALUES(?, ?)",
            (nm, body.description),
        )
        scen_id = cur.lastrowid
        if body.overrides:
            codes = list({o.sku_code for o in body.overrides})
            ph = ",".join("?" * len(codes))
            sku_map = {r["sku_code"]: r["sku_id"] for r in con.execute(
                f"SELECT sku_id, sku_code FROM skus WHERE sku_code IN ({ph})", codes
            ).fetchall()}
            inserted = 0
            for o in body.overrides:
                sid = sku_map.get(o.sku_code)
                if sid is None: continue
                con.execute(
                    "INSERT OR REPLACE INTO cost_scenario_item(scenario_id, sku_id, unit_cost) "
                    "VALUES(?,?,?)", (scen_id, sid, float(o.unit_cost))
                )
                inserted += 1
        else:
            inserted = 0
        con.commit()
    invalidate_cache("list_scenarios", "get_scenario")
    return {"id": scen_id, "name": nm, "items_inserted": inserted}


@app.get("/api/bom-cost/scenarios")
def list_scenarios():
    with get_db() as con:
        rows = con.execute("""
            SELECT cs.id, cs.name, cs.description, cs.created_at, cs.created_by,
                   COALESCE(cs.is_archived,0) AS is_archived,
                   (SELECT COUNT(*) FROM cost_scenario_item csi WHERE csi.scenario_id = cs.id) AS n_items
              FROM cost_scenario cs
             WHERE COALESCE(cs.is_archived,0) = 0
             ORDER BY cs.id DESC
        """).fetchall()
    return {"scenarios": [dict(r) for r in rows]}


@app.get("/api/bom-cost/scenarios/{scen_id}")
def get_scenario(scen_id: int):
    with get_db() as con:
        head = con.execute("SELECT * FROM cost_scenario WHERE id=?", (scen_id,)).fetchone()
        if head is None:
            raise HTTPException(404, "scenario not found")
        items = con.execute("""
            SELECT csi.sku_id, sk.sku_code,
                   COALESCE(sk.description,'') AS description,
                   COALESCE(sk.business_type,'other') AS business_type,
                   csi.unit_cost
              FROM cost_scenario_item csi
              JOIN skus sk ON sk.sku_id = csi.sku_id
             WHERE csi.scenario_id=?
             ORDER BY sk.sku_code
        """, (scen_id,)).fetchall()
    return {"scenario": dict(head), "items": [dict(r) for r in items]}


@app.delete("/api/bom-cost/scenarios/{scen_id}")
def delete_scenario(scen_id: int):
    with get_db() as con:
        cur = con.execute("UPDATE cost_scenario SET is_archived=1 WHERE id=?", (scen_id,))
        con.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "scenario not found")
    invalidate_cache("list_scenarios", "get_scenario")
    return {"id": scen_id, "archived": True}


@app.post("/api/bom-cost/simulate")
def simulate_scenario(body: SimulateBody):
    """Recompute chain + per-branch GP using the given unit_cost overrides."""
    where, params = _bom_cost_period_filters(body.month_from, body.month_to)
    cogs_expr       = _cogs_case('ssm', 'sk', 'sc')
    costed_rev_expr = _costed_rev_case('ssm', 'sk', 'sc')

    overrides = {o.sku_code: float(o.unit_cost) for o in (body.overrides or [])}

    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.name AS branch, ssm.sku_id, sk.sku_code,
                   COALESCE(SUM(ssm.qty), 0)            AS qty,
                   COALESCE(SUM({cogs_expr}), 0)        AS base_cogs,
                   COALESCE(SUM({costed_rev_expr}), 0)  AS base_costed,
                   COALESCE(SUM(ssm.subtotal_ex_vat),0) AS revenue
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY b.name, ssm.sku_id
        """, params).fetchall()

    chain = {"revenue": 0.0, "base_cogs": 0.0, "base_costed": 0.0,
             "scen_cogs": 0.0, "scen_costed": 0.0}
    per_branch: Dict[str, dict] = {}
    seen_codes = set()

    for r in rows:
        br = r["branch"]
        bb = per_branch.setdefault(br, {
            "revenue": 0.0,
            "base_cogs": 0.0, "base_costed": 0.0,
            "scen_cogs": 0.0, "scen_costed": 0.0,
        })
        rev       = float(r["revenue"]     or 0)
        base_c    = float(r["base_cogs"]   or 0)
        base_cst  = float(r["base_costed"] or 0)
        qty       = float(r["qty"]         or 0)
        code      = r["sku_code"]
        seen_codes.add(code)
        new_c   = base_c
        new_cst = base_cst
        if code in overrides and base_c > 0:
            new_c   = qty * overrides[code]
        chain["revenue"]     += rev
        chain["base_cogs"]   += base_c
        chain["base_costed"] += base_cst
        chain["scen_cogs"]   += new_c
        chain["scen_costed"] += new_cst
        bb["revenue"]     += rev
        bb["base_cogs"]   += base_c
        bb["base_costed"] += base_cst
        bb["scen_cogs"]   += new_c
        bb["scen_costed"] += new_cst

    def _pack(d):
        bc, bcs = d["base_cogs"], d["base_costed"]
        sc_, scs = d["scen_cogs"], d["scen_costed"]
        return {
            "revenue":      round(d["revenue"], 2),
            "baseline": {
                "cogs":           round(bc, 2),
                "gp":             round(bcs - bc, 2),
                "gp_pct":         round((bcs - bc) / bcs * 100, 1) if bcs > 0 else None,
                "food_cost_pct":  round(bc / bcs * 100, 1) if bcs > 0 else None,
            },
            "scenario": {
                "cogs":           round(sc_, 2),
                "gp":             round(scs - sc_, 2),
                "gp_pct":         round((scs - sc_) / scs * 100, 1) if scs > 0 else None,
                "food_cost_pct":  round(sc_ / scs * 100, 1) if scs > 0 else None,
            },
            "delta": {
                "cogs":           round(sc_ - bc, 2),
                "gp":             round((scs - sc_) - (bcs - bc), 2),
            },
        }

    chain_pack = _pack(chain); chain_pack["branch"] = "ALL"
    branches_out = []
    for k in sorted(per_branch.keys()):
        p = _pack(per_branch[k]); p["branch"] = k
        branches_out.append(p)
    return {
        "period": {"month_from": body.month_from, "month_to": body.month_to},
        "overrides_applied": len([c for c in overrides if c in seen_codes]),
        "chain": chain_pack,
        "per_branch": branches_out,
    }


@app.get("/api/bom-cost/recipe/{sku_code}")
def bom_cost_recipe(sku_code: str):
    """Return the BoM recipe for a given sales SKU (direct or aliased)."""
    with get_db() as con:
        sku = con.execute(
            "SELECT sku_id, sku_code, COALESCE(description,'') AS description "
            "FROM skus WHERE sku_code=?", (sku_code,)
        ).fetchone()
        if sku is None:
            raise HTTPException(404, "sku not found")
        bom_sku_id = con.execute(
            "SELECT sku_id FROM bom_ingredients WHERE sku_id=? LIMIT 1", (sku["sku_id"],)
        ).fetchone()
        if bom_sku_id is None:
            bom_sku_id = con.execute(
                "SELECT resolved_sku_id AS sku_id FROM bom_sku_alias WHERE bom_sku_code=? LIMIT 1",
                (sku_code,)
            ).fetchone()
        if bom_sku_id is None:
            return {"sku_code": sku_code, "has_recipe": False, "ingredients": []}
        rows = con.execute("""
            SELECT seq, ingredient_code, ingredient_name, qty_per_serving,
                   unit, price_per_unit, ingredient_cost, ingredient_type
              FROM bom_ingredients WHERE sku_id=? ORDER BY seq
        """, (bom_sku_id["sku_id"],)).fetchall()
    total = sum(float(r["ingredient_cost"] or 0) for r in rows)
    return {
        "sku_code":    sku_code,
        "description": sku["description"],
        "has_recipe":  True,
        "total_cost":  round(total, 4),
        "ingredients": [dict(r) for r in rows],
    }
