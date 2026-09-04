"""Cost & BoM domain router — /api/cost/*, /api/bom/*, /api/bom-cost/*,
/api/branch-bom/*, moved VERBATIM out of main.py (P1 structure refactor,
step 7). No logic changes; URLs unchanged. The shared SQL core
(_cogs_case, _unified_cogs_expr, tier codes, …) deliberately STAYS in
main.py — every router pulls it from there at import time. Same host-module
pattern as routers/reports.py.
"""
# Shared infrastructure and helpers come from core.py by name — the old
# `globals().update(getattr(main, …))` bootstrap is gone (Phase 2, 2026-09).
from fastapi import Depends, HTTPException, Query
from fastapi.responses import Response as _Response
from pydantic import BaseModel
from typing import Dict, List, Optional
from datetime import datetime, timezone
import auth as _auth
from rules import ALL_TIER_CODES_SQL as _ALL_TIER_CODES_SQL, EASY_TIER_CODES_SQL as _EASY_TIER_CODES_SQL
from core import (
    BOM_TYPE_BENCHMARKS, CostEntry, DB_PATH, HERE, _FOOD_CATEGORY_FILTER,
    _bom_cost_period_filters, _build_sku_effective_cost, _cogs_case, _costed_rev_case, _effective_cost_subquery,
    _group_filter, _ingredient_family, _month_range_filter, _service_charge_map, _unified_cogs_expr,
    get_db, invalidate_cache, is_price_as_cost, require_write_token, ttl_cache,
)
try:
    from .sales import _mtd_window
except ImportError:
    from sales import _mtd_window

from fastapi import APIRouter
router = APIRouter()










@router.get("/api/cost")
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


@router.post("/api/cost", dependencies=[Depends(require_write_token)])
def post_cost(entries: List[CostEntry]):
    """Upsert unit costs by sku_code. Returns count of rows updated.

    GUARD (2026-06-01): rejects any non-tier entry whose unit_cost lands in the
    price-as-cost band vs the SKU's ex-VAT selling price (ratio 1.03–1.12 ≈ the
    VAT multiplier). This is the ONLY code path that writes sku_cost, so it's
    where re-uploading the price spreadsheet as "costs" reintroduces the bad
    data. Such rows are skipped and reported instead of silently corrupting
    every margin number. Tier SKUs (BUFFET*) are exempt — their cost is ignored
    by _cogs_case anyway.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    updated = 0
    skipped: list = []
    with get_db() as con:
        cur = con.cursor()
        # Pre-compute ex-VAT avg selling price for the incoming SKUs (one query).
        codes = [e.sku_code for e in entries]
        price_by_code: dict = {}
        if codes:
            ph = ",".join("?" for _ in codes)
            for code, q, rev in cur.execute(
                f"""SELECT sk.sku_code, SUM(ssm.qty), SUM(ssm.subtotal_ex_vat)
                    FROM skus sk JOIN v_sales_sku_monthly ssm ON ssm.sku_id = sk.sku_id
                    WHERE sk.sku_code IN ({ph})
                    GROUP BY sk.sku_code
                    HAVING SUM(ssm.qty) > 0 AND SUM(ssm.subtotal_ex_vat) > 0""",
                codes,
            ).fetchall():
                price_by_code[code] = (rev or 0) / (q or 1)
        for entry in entries:
            row = cur.execute(
                "SELECT sku_id FROM skus WHERE sku_code = ?", (entry.sku_code,)
            ).fetchone()
            if row is None:
                continue
            sku_id = row[0]
            is_tier = str(entry.sku_code).upper().startswith("BUFFET")
            price = price_by_code.get(entry.sku_code)
            if not is_tier and is_price_as_cost(entry.unit_cost, price):
                skipped.append({
                    "sku_code":  entry.sku_code,
                    "unit_cost": round(entry.unit_cost, 2),
                    "ex_vat_price": round(price, 2),
                    "ratio":     round(entry.unit_cost / price, 3),
                })
                continue   # looks like price-as-cost — do NOT write
            cur.execute(
                """INSERT OR REPLACE INTO sku_cost(sku_id, unit_cost, effective_from, updated_at)
                   VALUES(?, ?, ?, ?)""",
                (sku_id, entry.unit_cost, now[:10], now),
            )
            updated += 1
        con.commit()
    invalidate_cache()   # cost data changed — clear all cached analytics responses
    resp = {"updated": updated}
    if skipped:
        resp["skipped_price_as_cost"] = len(skipped)
        resp["skipped_samples"] = skipped[:10]
        resp["warning"] = (
            f"{len(skipped)} entr{'y' if len(skipped)==1 else 'ies'} skipped: "
            "the value matched the ex-VAT selling price × ~1.07, i.e. it looks "
            "like the PRICE was entered as cost. Enter the real per-unit cost."
        )
    return resp


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


@router.get("/api/cost/monthly")
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


@router.post("/api/cost/monthly", dependencies=[Depends(require_write_token)])
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


@router.delete("/api/cost/monthly", dependencies=[Depends(require_write_token)])
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


@router.get("/api/cost/monthly/template")
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




@router.get("/api/cost/diff")
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
        # Bootstrap core schema if the DB is empty/partial (e.g. a fresh hosted
        # volume). schema.sql is shipped beside this file and is fully idempotent
        # (CREATE TABLE IF NOT EXISTS …). On a populated DB `branches` already
        # exists, so this is skipped and behaviour is unchanged.
        has_branches = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='branches'"
        ).fetchone()
        if not has_branches:
            schema_path = HERE / "schema.sql"
            if schema_path.exists():
                con.executescript(schema_path.read_text(encoding="utf-8"))
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
        # branch_type migration (idempotent). Guarded so a DB still missing the
        # core tables (schema.sql absent) can't crash import.
        branch_cols = [r[1] for r in con.execute("PRAGMA table_info(branches)").fetchall()]
        if branch_cols and "branch_type" not in branch_cols:
            con.execute("ALTER TABLE branches ADD COLUMN branch_type TEXT DEFAULT 'master'")
        if branch_cols and "active" not in branch_cols:
            con.execute("ALTER TABLE branches ADD COLUMN active INTEGER DEFAULT 1")
        # skus.active migration (idempotent)
        sku_cols = [r[1] for r in con.execute("PRAGMA table_info(skus)").fetchall()]
        if sku_cols and "active" not in sku_cols:
            con.execute("ALTER TABLE skus ADD COLUMN active INTEGER DEFAULT 1")
        # per-bill discount reason + coupon series (idempotent) — powers the
        # cashier-discount drill "ประเภทส่วนลด/คูปอง" column. Populated by sync_mssql.
        st_cols = [r[1] for r in con.execute("PRAGMA table_info(sales_transaction)").fetchall()]
        if st_cols and "discount_reason_id" not in st_cols:
            con.execute("ALTER TABLE sales_transaction ADD COLUMN discount_reason_id INTEGER")
        if st_cols and "coupon_series_id" not in st_cols:
            con.execute("ALTER TABLE sales_transaction ADD COLUMN coupon_series_id INTEGER")
        if st_cols and "print_times" not in st_cols:
            con.execute("ALTER TABLE sales_transaction ADD COLUMN print_times INTEGER DEFAULT 0")
        # per-bill sitting duration (open→close, minutes) — populated by sync_mssql
        # from SaleTab. Powers the "ระยะเวลานั่ง" column on the loss-bill table.
        if st_cols and "dwell_min" not in st_cols:
            con.execute("ALTER TABLE sales_transaction ADD COLUMN dwell_min INTEGER")
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
# Load dynamic role permissions into memory cache (falls back to hardcoded ROLE_MODULES if not stored)
with get_db() as _boot_con:
    _auth.refresh_perm_cache(_boot_con)




# ---------------------------------------------------------------------------
# /api/cost/detail   -  searchable SKU cost list with revenue stats
# ---------------------------------------------------------------------------
@router.get("/api/cost/detail")
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

    # SCOPE (2026-06-01): Cost Management lists ONLY menu items actually sold via
    # Easy. This excludes ~845 legacy / CM-POS-era / never-sold active SKUs that
    # are not part of the current (Easy-only since 2026-05-27) menu and would
    # otherwise clutter the cost worklist. Enforced by requiring an Easy sales row.
    filters.append(
        "EXISTS (SELECT 1 FROM sales_sku_monthly s "
        "WHERE s.sku_id = sk.sku_id AND s.source = 'Easy')"
    )
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    cnt_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT sk.sku_id, sk.sku_code,
                   COALESCE(sk.description,'') AS description,
                   c.name AS category,
                   COALESCE(sk.business_type,'buffet') AS business_type,  -- buffet-first default (85% of business)
                   sc.unit_cost, sc.updated_at,
                   -- Effective COGS the rest of the app actually uses (BoM recipe →
                   -- alias BoM → sku_cost → 0). Lets the worklist show a real cost for
                   -- the ~98 recipe-costed SKUs whose manual sc.unit_cost is NULL, so
                   -- they no longer read as "uncosted" when they're costed via BoM.
                   (SELECT sec.recipe_cost FROM sku_effective_cost sec
                      WHERE sec.sku_id = sk.sku_id) AS effective_cost,
                   ROUND(SUM(CASE WHEN ssm.source='Easy' THEN ssm.subtotal_ex_vat END), 0) AS total_revenue,
                   ROUND(SUM(CASE WHEN ssm.source='Easy' THEN ssm.qty END), 0)            AS total_qty
            FROM skus sk
            JOIN  categories c   ON c.category_id = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id = sk.sku_id
            LEFT JOIN v_sales_sku_monthly ssm ON ssm.sku_id = sk.sku_id
            {where}
            GROUP BY sk.sku_id
            {having_clause}
            {sort_clause}
            LIMIT :limit OFFSET :offset
            """,
            params,
        ).fetchall()

        # fix E2: wrap in subquery to apply the same HAVING filter as the main query
        total = con.execute(
            f"""
            SELECT COUNT(*) FROM (
                SELECT sk.sku_id
                FROM skus sk
                JOIN categories c ON c.category_id = sk.category_id
                LEFT JOIN sku_cost sc ON sc.sku_id = sk.sku_id
                LEFT JOIN v_sales_sku_monthly ssm ON ssm.sku_id = sk.sku_id
                {where}
                GROUP BY sk.sku_id
                {having_clause}
            )
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
@router.get("/api/cost/template")
def cost_template():
    """SKUs actually sold through Easy (real line items) with sales context +
    current unit_cost for CSV template download.

    Scope (2026-06-01): only SKUs with at least one Easy sale — excludes the
    ~847 never-sold active SKUs that just bloat the sheet — and excludes buffet
    TIER codes (BUFFET*), whose 'cost' is the customer payment, not a line-item
    cost (they're handled by _cogs_case, never costed here). Sorted by revenue
    desc so highest-volume items to cost appear first.
    """
    with get_db() as con:
        rows = con.execute(
            """
            SELECT sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   c.name AS category,
                   COALESCE(CAST(sc.unit_cost AS TEXT), '') AS unit_cost,
                   ROUND(SUM(ssm.subtotal_ex_vat), 0) AS total_revenue,
                   ROUND(SUM(ssm.qty), 0)             AS total_qty,
                   MAX(ssm.period_month)              AS last_sold
            FROM skus sk
            JOIN  categories c    ON c.category_id = sk.category_id
            JOIN v_sales_sku_monthly ssm ON ssm.sku_id = sk.sku_id AND ssm.source = 'Easy'
            LEFT JOIN sku_cost sc ON sc.sku_id = sk.sku_id
            WHERE COALESCE(sk.active, 1) = 1
              AND sk.sku_code NOT LIKE 'BUFFET%'
              AND c.name NOT IN ('Payment', 'Other', 'Oshinei DELIVERY', 'Special DELIVERY')
              AND UPPER(sk.sku_code) NOT IN ('SERVICECHARGE', 'SP', 'SERVICE')
            GROUP BY sk.sku_id
            HAVING SUM(ssm.qty) > 0
            ORDER BY total_revenue DESC, sk.sku_code
            """
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# /api/cost/analysis   -  full per-SKU menu engineering analysis
# ---------------------------------------------------------------------------
@router.get("/api/cost/analysis")
@ttl_cache
def cost_analysis(
    branch:     Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    include_buffet_drink: bool = Query(False),
    group:      Optional[str] = Query(None),
):
    """
    Per-SKU revenue + cost breakdown with menu engineering quadrant classification.
    Returns items with sales > 0 only. Excludes buffet/drink/dessert/payment by default
    so the analysis focuses on à la carte food items.
    Quadrant = star | plowhorse | puzzle | dog | uncosted
    """
    filters = ["b.name != 'ALL'", "ssm.source = 'Easy'"]   # fix E8
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
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
            FROM v_sales_sku_monthly ssm
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

    # Buffet-component leak guard: a few buffet item-level SKUs (B0xxx) carry a
    # negligible à-la-carte revenue (the meal is paid via the buffet tier) while
    # their real BoM cost is large — yielding nonsense margins in this menu-
    # engineering view (e.g. B0119 ปีกไก่ทอด: ฿10 revenue vs ฿3M cost = −30,306,864%).
    # Their per-unit ex-VAT price is ≈฿0, so drop items priced below a small floor;
    # real à-la-carte food is ฿20+/unit, so nothing legitimate is touched. Counted
    # (not silently capped) and surfaced as summary.buffet_leak_excluded. The whole-
    # store KPI block (ws_*) is computed separately and still includes every row.
    _LEAK_PRICE_FLOOR = 5.0   # ฿ ex-VAT per unit
    def _unit_price(r):
        q = r["qty"] or 0
        return (r["revenue_ex_vat"] or 0) / q if q else 0.0
    _leak_ids = {r["sku_id"] for r in items
                 if (r["qty"] or 0) > 0 and _unit_price(r) < _LEAK_PRICE_FLOOR}
    buffet_leak_excluded = len(_leak_ids)
    if _leak_ids:
        items = [r for r in items if r["sku_id"] not in _leak_ids]

    # Effective-cost-aware costed flag + derived GP/margin (Phase 8 monthly cost).
    # "Costed" means the COALESCE chain (monthly exact -> monthly prior -> default)
    # resolved a unit cost for at least one row — broader than sc.unit_cost alone.
    for r in items:
        # Raw unified BoM COGS — kept even when has_cost (sku_cost-based) is False, so
        # recipe-costed items (e.g. buffet-component SKUs with a BoM but no sku_cost
        # row) still expose their true ingredient cost for cost-contribution views.
        r["bom_cogs"] = r["cogs"]
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
    costed_items      = [r for r in items if r["has_cost"]]
    total_cogs        = sum(r["cogs"]        or 0 for r in costed_items)
    # Revenue-weighted margin EXCLUDING implausible items (margin < -100%, i.e.
    # cost > 2x revenue). Those are near-zero-revenue, high-qty buffet-component
    # SKUs that otherwise drag the headline KPI to nonsense (e.g. -7500%). They
    # remain in the table; only the aggregate excludes them.
    sane_margin_items = [
        r for r in costed_items
        if r["margin_pct"] is not None and r["margin_pct"] >= -100
    ]
    sane_gp     = sum(r["gross_profit"]   or 0 for r in sane_margin_items)
    sane_rev_ex = sum(r["revenue_ex_vat"] or 0 for r in sane_margin_items)
    overall_margin = round(sane_gp / sane_rev_ex * 100, 1) if sane_rev_ex > 0 else None
    excluded_outliers = len(costed_items) - len(sane_margin_items)

    for r in items:
        r["revenue_mix_pct"] = round(r["revenue"] / total_revenue * 100, 2) if total_revenue else 0
        r["qty_mix_pct"]     = round(r["qty"]     / total_qty     * 100, 2) if total_qty     else 0

    # â"€â"€ Menu engineering quadrant (costed items only) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    # margin_pct can be None even when unit_cost is set (rows with subtotal_ex_vat=0).
    # Filter them out for the avg/quadrant math; otherwise sum() raises TypeError.
    quad_items = [r for r in costed_items if r["margin_pct"] is not None]
    if quad_items:
        def _median(xs):
            s = sorted(xs); n = len(s)
            return 0 if n == 0 else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)
        # MEDIAN boundaries, not mean: a few tiny-revenue SKUs produce margins of
        # ±thousands-of-% (gross_profit / a few baht), which drag a mean off-scale
        # and misclassify everything (the classic "everything is a Puzzle" bug).
        med_qty    = _median([r["qty"]        for r in quad_items])
        med_margin = _median([r["margin_pct"] for r in quad_items])
        for r in items:
            if not r["has_cost"] or r["margin_pct"] is None:   # fix E7: use has_cost (covers BoM-only items)
                r["quadrant"] = "uncosted"
            else:
                hi_pop = r["qty"]        >= med_qty
                hi_mar = r["margin_pct"] >= med_margin
                r["quadrant"] = (
                    "star"      if hi_pop and hi_mar  else
                    "plowhorse" if hi_pop             else
                    "puzzle"    if hi_mar             else
                    "dog"
                )
        avg_qty_all    = med_qty
        avg_margin_all = med_margin
    else:
        for r in items:
            r["quadrant"] = "uncosted"
        avg_qty_all, avg_margin_all = None, None

    # â"€â"€ Pareto (revenue) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    cumrev = 0
    for r in items:
        cumrev += r["revenue"] or 0
        r["cum_revenue_pct"] = round(cumrev / total_revenue * 100, 1) if total_revenue else 0

    # ── WHOLE-STORE reconciled headline totals (2026-06 fix) ──────────────────
    # The per-item table above is à-la-carte-focused (BUFFET category excluded for
    # menu-engineering). But its summed revenue (฿31M, à-la-carte only) does NOT
    # reconcile with the COGS (which includes buffet B0xxx dish cost) → produced a
    # nonsense GROSS PROFIT of -242M with a 70.8% margin. The KPI cards must show
    # the WHOLE-STORE figures (all categories, unified COGS) so rev/COGS/GP/margin
    # agree with every other tab (~฿944M rev, ~40% GP). Per-item analytics keep
    # their own à-la-carte scope below.
    ws_filters = ["b.name != 'ALL'", "ssm.source = 'Easy'"]
    ws_params: dict = {}
    if branch:
        ws_filters.append("b.name = :wbranch"); ws_params["wbranch"] = branch
    _group_filter(ws_filters, ws_params, group)
    _month_range_filter(ws_filters, ws_params, month_from, month_to, month, col="ssm.period_month")
    ws_where = "WHERE " + " AND ".join(ws_filters)
    with get_db() as con:
        ws = con.execute(f"""
            SELECT ROUND(SUM(ssm.subtotal_ex_vat), 2) AS revenue,
                   ROUND(SUM({_unified_cogs_expr('ssm')}), 2) AS cogs,
                   ROUND(SUM(ssm.qty), 0) AS qty
            FROM v_sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus sk    ON sk.sku_id   = ssm.sku_id
            {ws_where}
        """, ws_params).fetchone()
    ws_rev  = (ws["revenue"] or 0) if ws else 0
    ws_cogs = (ws["cogs"] or 0) if ws else 0
    ws_gp   = ws_rev - ws_cogs
    ws_margin = round(ws_gp / ws_rev * 100, 1) if ws_rev > 0 else None

    return {
        "items": items,
        "summary": {
            # Headline = whole-store reconciled (rev/COGS/GP/margin all agree)
            "total_revenue":      round(ws_rev, 2),
            "total_cogs":         round(ws_cogs, 2),
            "total_gross_profit": round(ws_gp, 2),
            "overall_margin_pct": ws_margin,
            "total_qty":          round(ws["qty"] or 0, 0) if ws else 0,
            # à-la-carte menu-engineering scope (for the table / scatter / pareto)
            "alacarte_revenue":   round(total_revenue, 2),
            "alacarte_cogs":      round(total_cogs, 2),
            "alacarte_margin_pct": overall_margin,
            "total_skus":         len(items),
            "costed_skus":        len(costed_items),
            "uncosted_skus":      len(items) - len(costed_items),
            "avg_qty":            round(avg_qty_all, 1) if avg_qty_all else None,
            "avg_margin":         round(avg_margin_all, 1) if avg_margin_all else None,
            "margin_outliers_excluded": excluded_outliers,
            "buffet_leak_excluded": buffet_leak_excluded,
        },
    }




# ---------------------------------------------------------------------------
# GET /api/cost/audit   🔴1
# Flag SKUs where unit_cost looks suspicious vs actual selling price
# ---------------------------------------------------------------------------
@router.get("/api/cost/audit")
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
            LEFT JOIN v_sales_sku_monthly ssm ON ssm.sku_id = sk.sku_id
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
        implied = item["implied_selling_price"]
        qty = item["total_qty"] or 0

        # Buffet-included dish: sold in volume (qty>0) but ~zero à-la-carte revenue
        # (customer pays via the buffet tier, not per plate) → avg sell ≈ 0, which
        # makes the cost/price ratio meaningless (huge false 'Error'). Detect these
        # by data (implied≈0 with real qty), NOT a stale code-prefix list, and
        # skip the ratio check — rely on z-score only. Mark as 'tier' so the UI
        # groups them as buffet, not errors.
        is_buffet_incl = qty > 0 and (implied is None or implied < 1.0)
        item["is_buffet_included"] = is_buffet_incl

        if item["is_tier"] or is_buffet_incl:
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
# GET /api/cost/uncosted-coverage  — cost-data coverage tracker (Analysis #3)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# /api/cost/suggestions — ESTIMATED unit costs for dishes that have none
# ---------------------------------------------------------------------------
def _cost_suggestions(con, limit=400):
    """Suggest a unit cost for every selling dish that has none, derived ONLY
    from dishes we have already costed. Nothing here is written to sku_cost —
    these are review candidates for the owner, and every row carries the method
    and the sample it came from so a bad suggestion is obvious on sight.

    Priority (strongest signal first):
      1. ingredient family  — median cost of costed dishes sharing the main
         ingredient (reuses _ingredient_family, the same map the BoM views use).
         A salmon dish is priced from other salmon dishes.
      2. category median    — median cost of costed dishes in the same category.
      3. price ratio        — selling price x the chain's median cost/price
         ratio, for dishes that carry a real à-la-carte price.
    Confidence reflects both the method and the size of the sample behind it."""
    import statistics as _stat
    eff = ("COALESCE((SELECT recipe_cost FROM sku_effective_cost WHERE sku_id=s.sku_id),"
           "(SELECT NULLIF(unit_cost,0) FROM sku_cost WHERE sku_id=s.sku_id),0)")
    rows = con.execute(f"""
        SELECT s.sku_id, s.sku_code, s.description, c.name cat, {eff} eff,
               (SELECT ROUND(SUM(qty)) FROM v_sales_sku_monthly WHERE sku_id=s.sku_id) qty,
               (SELECT ROUND(SUM(subtotal_ex_vat)/NULLIF(SUM(qty),0),2)
                  FROM v_sales_sku_monthly WHERE sku_id=s.sku_id) price
        FROM skus s JOIN categories c ON c.category_id=s.category_id
        WHERE s.sku_code NOT IN {_ALL_TIER_CODES_SQL}
          AND s.sku_id IN (SELECT sk.sku_id FROM skus sk
                           JOIN categories c2 ON c2.category_id=sk.category_id
                           WHERE {_FOOD_CATEGORY_FILTER.replace('c.', 'c2.').replace('sk.', 'sk.')})
    """).fetchall()

    costed = [r for r in rows if (r["eff"] or 0) > 0]
    fam_pool, cat_pool, ratios = {}, {}, []
    for r in costed:
        fam_pool.setdefault(_ingredient_family(r["description"] or ""), []).append(r["eff"])
        cat_pool.setdefault(r["cat"], []).append(r["eff"])
        if r["price"] and r["price"] > 0 and r["eff"] / r["price"] < 1.5:
            ratios.append(r["eff"] / r["price"])
    median_ratio = round(_stat.median(ratios), 3) if ratios else 0.2

    out = []
    for r in rows:
        if (r["eff"] or 0) > 0 or not (r["qty"] or 0):
            continue                                   # already costed / never sold
        # A family median built on a handful of peers is not evidence — it is
        # how a ฿2 dipping sauce inherits ฿20 from four costed sauces. Demand a
        # real sample before trusting a median, and never let an estimate claim
        # a dish costs most of what it sells for.
        fam = _ingredient_family(r["description"] or "")
        pool = fam_pool.get(fam) if fam != "Other · อื่นๆ" else None
        if pool and len(pool) >= 8:
            est, method = _stat.median(pool), "ingredient"
            basis, n = fam, len(pool)
            conf = "high" if len(pool) >= 20 else "medium"
        elif cat_pool.get(r["cat"]) and len(cat_pool[r["cat"]]) >= 8:
            pool = cat_pool[r["cat"]]
            est, method = _stat.median(pool), "category"
            basis, n = r["cat"], len(pool)
            conf = "medium" if len(pool) >= 20 else "low"
        elif r["price"] and r["price"] > 0:
            est, method = r["price"] * median_ratio, "price_ratio"
            basis, n = f"ราคาขาย x {median_ratio}", len(ratios)
            conf = "low"
        else:
            continue                                   # no defensible signal
        # Sanity cap: a dish whose estimate exceeds 60% of its own selling price
        # is almost certainly a bad analogue, not a loss-leader. Cap and demote.
        if r["price"] and r["price"] > 0 and est > r["price"] * 0.6:
            est, conf = r["price"] * 0.6, "low"
            basis += " · จำกัดที่ 60% ของราคาขาย"
        est = round(est, 2)
        out.append({
            "sku_id": r["sku_id"], "sku_code": r["sku_code"],
            "menu": (r["description"] or r["sku_code"]).replace("(A) ", "").replace("(B) ", ""),
            "category": r["cat"], "qty": round(r["qty"] or 0),
            "price": r["price"], "suggested_cost": est,
            "method": method, "basis": basis, "sample": n, "confidence": conf,
            # what booking this suggestion would add to COGS over all history
            "impact": round(est * (r["qty"] or 0)),
        })
    out.sort(key=lambda x: -x["impact"])
    return out[:limit], median_ratio


@router.get("/api/cost/suggestions")
@ttl_cache
def cost_suggestions(limit: int = Query(400, ge=1, le=2000)):
    """Review list of ESTIMATED costs for uncosted dishes (never auto-applied)."""
    with get_db() as con:
        items, ratio = _cost_suggestions(con, limit)
    return {
        "items": items,
        "count": len(items),
        "total_impact": sum(i["impact"] for i in items),
        "median_cost_price_ratio": ratio,
        "note": ("ค่าประมาณจากเมนูที่มีต้นทุนจริงแล้วเท่านั้น — ยังไม่ถูกบันทึกลงระบบ "
                 "ตรวจแล้วค่อยกรอกที่ ตั้งค่า → Cost Management"),
    }


@router.get("/api/cost/suggestions.csv")
def cost_suggestions_csv(limit: int = Query(2000, ge=1, le=5000)):
    """The same suggestions as a CSV, shaped for bulk review/entry."""
    import csv as _csv
    import io as _io2
    with get_db() as con:
        items, _ = _cost_suggestions(con, limit)
    buf = _io2.StringIO()
    w = _csv.writer(buf)
    w.writerow(["sku_code", "menu", "category", "qty_sold", "selling_price",
                "suggested_cost", "method", "basis", "sample_size",
                "confidence", "cogs_impact"])
    for i in items:
        w.writerow([i["sku_code"], i["menu"], i["category"], i["qty"], i["price"],
                    i["suggested_cost"], i["method"], i["basis"], i["sample"],
                    i["confidence"], i["impact"]])
    return _Response(content="\ufeff" + buf.getvalue(),      # BOM so Excel reads Thai
                     media_type="text/csv; charset=utf-8",
                     headers={"Content-Disposition":
                              "attachment; filename=cost_suggestions.csv"})


@router.get("/api/cost/uncosted-coverage")
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
            FROM v_sales_sku_monthly ssm
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
            FROM v_sales_sku_monthly ssm
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
            FROM v_sales_sku_monthly ssm
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




@router.get("/api/bom/sku-list")
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


@router.get("/api/bom/composition")
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




@router.get("/api/bom/consumption-analysis")
@ttl_cache
def bom_consumption_analysis(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    limit:      int           = Query(50),
    group_family: bool        = Query(False, description="Roll up name variants into ingredient families (e.g. all salmon → Salmon)"),
):
    """
    Period rollup of ingredient consumption across all sold menus.
    Joins sales_sku_monthly x bom_ingredients.

    group_family=true consolidates the many spelling variants of the same
    ingredient (e.g. 24 salmon names) into one family line, so the list reflects
    true ingredient dominance (salmon ≈ 25% of cost) instead of being fragmented.
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
          FROM v_sales_sku_monthly ssm
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
    # Optional family roll-up: consolidate name variants (24 salmon spellings →
    # one "Salmon" line) so dominance is visible instead of fragmented.
    if group_family:
        fam: Dict[str, dict] = {}
        for r in all_rows:
            label = _ingredient_family(r["name"])
            f = fam.setdefault(label, {
                "code": "", "name": label, "type": r["type"], "unit": r["unit"],
                "total_qty": 0.0, "total_cost": 0.0, "n_menus": 0, "n_variants": 0,
            })
            f["total_qty"]  += r["total_qty"]
            f["total_cost"] += r["total_cost"]
            f["n_menus"]     = max(f["n_menus"], r["n_menus"])
            f["n_variants"] += 1
        all_rows = sorted(fam.values(), key=lambda x: x["total_cost"], reverse=True)
        for r in all_rows:
            r["total_qty"]  = round(r["total_qty"], 2)
            r["total_cost"] = round(r["total_cost"], 2)
            r["pct"]        = round(100.0 * r["total_cost"] / grand_total, 2) if grand_total > 0 else 0

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
        "grouped":          group_family,
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
@router.get("/api/cost/recalibration")
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
    # fix G3: use LEFT JOIN and HAVING so BoM-only SKUs (no sku_cost row) are included
    cogs_expr = _cogs_case(ssm='ssm', sk='sk', sc='sc')
    filters = [f"sk.sku_code NOT IN {_EASY_TIER_CODES_SQL}",
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
            FROM v_sales_sku_monthly ssm
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
            FROM v_sales_sku_monthly ssm
            JOIN skus sk ON sk.sku_id = ssm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY sk.sku_id
            HAVING SUM({cogs_expr}) > 0
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
            FROM v_sales_sku_monthly ssm
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


@router.post("/api/cost/bulk-update", dependencies=[Depends(require_write_token)])
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
def _bba_setup(*, branch, granularity, month_from, month_to):
    # names this section hands on; None where a path never assigns one
    cogs_expr = None
    costed_rev_expr = None
    is_chain = None
    params = None
    where = None
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
    return {"cogs_expr": cogs_expr, "costed_rev_expr": costed_rev_expr, "granularity": granularity, "is_chain": is_chain, "params": params, "where": where}


def _bba_query(*, branch, cogs_expr, costed_rev_expr, is_chain, params, top_n, where):
    # names this section hands on; None where a path never assigns one
    cogs = None
    costed_rev_ex = None
    coverage_row = None
    food_pct = None
    gp = None
    gp_pct = None
    has_bom_map = None
    ing_menus = None
    ing_rows = None
    monthly_by_sku = None
    revenue_ex = None
    revenue_inc = None
    top_menu_rows = None
    totals_row = None
    trend_rows = None
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
            FROM v_sales_sku_monthly ssm
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
              FROM v_sales_sku_monthly ssm
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
              FROM v_sales_sku_monthly ssm
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
                  FROM v_sales_sku_monthly ssm
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
                FROM v_sales_sku_monthly ssm
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
                    FROM v_sales_sku_monthly ssm
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
                FROM v_sales_sku_monthly ssm
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
    costed_rev_ex = float(totals_row["costed_rev_ex"] or 0)   # coverage diagnostic
    gp            = revenue_ex - cogs
    gp_pct        = (gp   / revenue_ex * 100) if revenue_ex > 0 else None
    food_pct      = (cogs / revenue_ex * 100) if revenue_ex > 0 else None
    return {"cogs": cogs, "costed_rev_ex": costed_rev_ex, "coverage_row": coverage_row, "food_pct": food_pct, "gp": gp, "gp_pct": gp_pct, "has_bom_map": has_bom_map, "ing_menus": ing_menus, "ing_rows": ing_rows, "monthly_by_sku": monthly_by_sku, "revenue_ex": revenue_ex, "revenue_inc": revenue_inc, "top_menu_rows": top_menu_rows, "totals_row": totals_row, "trend_rows": trend_rows}


def _bba_trend(*, trend_rows):
    # names this section hands on; None where a path never assigns one
    monthly_trend = None
    months_list = None
    n_months = None
    # period
    months_list   = [r["m"] for r in trend_rows]
    n_months      = len(months_list)

    monthly_trend = []
    for r in trend_rows:
        m_cogs      = float(r["cogs"] or 0)
        m_rev       = float(r["revenue_ex"] or 0)
        m_gp        = m_rev - m_cogs
        m_gp_pct    = (m_gp   / m_rev * 100) if m_rev > 0 else None
        m_food_pct  = (m_cogs / m_rev * 100) if m_rev > 0 else None
        monthly_trend.append({
            "month":         r["m"],
            "revenue":       round(m_rev, 2),
            "cogs":          round(m_cogs, 2),
            "gp":            round(m_gp, 2),
            "gp_pct":        round(m_gp_pct, 1)   if m_gp_pct   is not None else None,
            "food_cost_pct": round(m_food_pct, 1) if m_food_pct is not None else None,
        })
    return {"monthly_trend": monthly_trend, "months_list": months_list, "n_months": n_months}


def _bba_top_menus(*, cogs, has_bom_map, monthly_by_sku, top_menu_rows):
    # names this section hands on; None where a path never assigns one
    top_menus = None
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
    return {"top_menus": top_menus}


def _bba_concentration(*, cogs, top_menus):
    # names this section hands on; None where a path never assigns one
    concentration = None
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
    return {"concentration": concentration}


def _bba_ingredient_types(*, cogs, coverage_row, ing_menus, ing_rows):
    # names this section hands on; None where a path never assigns one
    cost_by_type_out = None
    menus_total = None
    menus_with_recipe = None
    top_ingredients = None
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
    return {"cost_by_type_out": cost_by_type_out, "menus_total": menus_total, "menus_with_recipe": menus_with_recipe, "top_ingredients": top_ingredients}


def _bba_granular_trend(*, branch, granularity, month_from, month_to, monthly_trend, months_list, trend_rows):
    # names this section hands on; None where a path never assigns one
    trend = None
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
            bgp = v["rev"] - v["cogs"]
            gpp = (bgp / v["rev"] * 100) if v["rev"] > 0 else None
            fcp = (v["cogs"] / v["rev"] * 100) if v["rev"] > 0 else None
            trend.append({
                "bucket":        k,
                "revenue":       round(v["rev"],   2),
                "cogs":          round(v["cogs"],  2),
                "gp":            round(bgp,        2),
                "gp_pct":        round(gpp, 1) if gpp is not None else None,
                "food_cost_pct": round(fcp, 1) if fcp is not None else None,
            })
    return {"trend": trend}


@router.get("/api/branch-bom/analysis")
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
    # Each stage is its own function (_bba_*, Phase 3b 2026-09); they run in
    # order and pass what they compute forward through `ctx`. The statements are
    # the ones this function used to hold inline — golden pins the output.
    ctx = {"branch": branch, "granularity": granularity, "month_from": month_from, "month_to": month_to, "top_n": top_n}
    ctx.update(_bba_setup(branch=ctx.get("branch"), granularity=ctx.get("granularity"), month_from=ctx.get("month_from"), month_to=ctx.get("month_to")))
    ctx.update(_bba_query(branch=ctx.get("branch"), cogs_expr=ctx.get("cogs_expr"), costed_rev_expr=ctx.get("costed_rev_expr"), is_chain=ctx.get("is_chain"), params=ctx.get("params"), top_n=ctx.get("top_n"), where=ctx.get("where")))
    ctx.update(_bba_trend(trend_rows=ctx.get("trend_rows")))
    ctx.update(_bba_top_menus(cogs=ctx.get("cogs"), has_bom_map=ctx.get("has_bom_map"), monthly_by_sku=ctx.get("monthly_by_sku"), top_menu_rows=ctx.get("top_menu_rows")))
    ctx.update(_bba_concentration(cogs=ctx.get("cogs"), top_menus=ctx.get("top_menus")))
    ctx.update(_bba_ingredient_types(cogs=ctx.get("cogs"), coverage_row=ctx.get("coverage_row"), ing_menus=ctx.get("ing_menus"), ing_rows=ctx.get("ing_rows")))
    ctx.update(_bba_granular_trend(branch=ctx.get("branch"), granularity=ctx.get("granularity"), month_from=ctx.get("month_from"), month_to=ctx.get("month_to"), monthly_trend=ctx.get("monthly_trend"), months_list=ctx.get("months_list"), trend_rows=ctx.get("trend_rows")))
    cogs = ctx["cogs"]
    concentration = ctx["concentration"]
    cost_by_type_out = ctx["cost_by_type_out"]
    costed_rev_ex = ctx["costed_rev_ex"]
    food_pct = ctx["food_pct"]
    gp = ctx["gp"]
    gp_pct = ctx["gp_pct"]
    is_chain = ctx["is_chain"]
    menus_total = ctx["menus_total"]
    menus_with_recipe = ctx["menus_with_recipe"]
    monthly_trend = ctx["monthly_trend"]
    months_list = ctx["months_list"]
    n_months = ctx["n_months"]
    revenue_ex = ctx["revenue_ex"]
    revenue_inc = ctx["revenue_inc"]
    top_ingredients = ctx["top_ingredients"]
    top_menus = ctx["top_menus"]
    totals_row = ctx["totals_row"]
    trend = ctx["trend"]

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




@router.get("/api/bom-cost/overview")
@ttl_cache
def bom_cost_overview(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    granularity: str          = Query("month", description="day | week | month"),
    incl_service: bool        = Query(False, description="Count ex-VAT service charge as revenue (and in the Food-Cost%/GP% denominator)"),
):
    """Chain-wide BoM cost overview - KPIs, heatmap, top-50 SKUs, top-30 ingredients,
    branch breakdown."""
    if granularity not in ("day", "week", "month"):
        granularity = "month"

    where, params = _bom_cost_period_filters(month_from, month_to)
    if group in ("master", "franchise"):
        where += " AND COALESCE(b.branch_type,'master') = :branch_group"
        params["branch_group"] = group
    cogs_expr       = _cogs_case('ssm', 'sk', 'sc')
    costed_rev_expr = _costed_rev_case('ssm', 'sk', 'sc')

    with get_db() as con:
        tot = con.execute(f"""
            SELECT COALESCE(SUM(ssm.subtotal_ex_vat), 0)       AS revenue_ex,
                   COALESCE(SUM({cogs_expr}), 0)               AS cogs,
                   COALESCE(SUM({costed_rev_expr}), 0)         AS costed_rev_ex,
                   COUNT(DISTINCT ssm.sku_id)                  AS menus_total
              FROM v_sales_sku_monthly ssm
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
              FROM v_sales_sku_monthly ssm
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
              FROM v_sales_sku_monthly ssm
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
                  FROM v_sales_sku_monthly ssm
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
              FROM v_sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY b.name
            ORDER BY cogs DESC
        """, params).fetchall()

        svc_map = _service_charge_map(con, month_from, month_to) if incl_service else {}

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
                FROM v_sales_sku_monthly ssm
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

    svc_total     = sum(svc_map.values())           # 0 when incl_service is False
    svc_by_branch: Dict[str, float] = {}
    for (br, _m), v in svc_map.items():
        svc_by_branch[br] = svc_by_branch.get(br, 0.0) + v

    revenue_ex    = float(tot["revenue_ex"]    or 0) + svc_total
    cogs          = float(tot["cogs"]          or 0)
    costed_rev_ex = float(tot["costed_rev_ex"] or 0) + svc_total   # coverage diagnostic only
    gp            = revenue_ex - cogs
    gp_pct        = (gp   / revenue_ex * 100) if revenue_ex > 0 else None
    food_pct      = (cogs / revenue_ex * 100) if revenue_ex > 0 else None

    heatmap = []
    for r in hmap:
        rev    = float(r["rev"]    or 0)
        c      = float(r["cogs"]   or 0)
        fc     = (c / rev * 100) if rev > 0 else None
        heatmap.append({
            "branch": r["branch"], "month": r["m"],
            "revenue": round(rev, 2), "cogs": round(c, 2),
            "gp": round(rev - c, 2),
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
        svc    = svc_by_branch.get(r["branch"], 0.0)
        full   = float(r["rev"] or 0) + svc
        fc     = (c / full * 100) if full > 0 else None
        branches_out.append({
            "branch": r["branch"],
            "revenue": round(float(r["rev"] or 0) + svc, 2),
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
            "service_charge": round(svc_total, 2),
            "incl_service":  bool(incl_service),
        },
        "heatmap":          heatmap,
        "top_skus":         top_skus,
        "concentration":    concentration,
        "branch_breakdown": branches_out,
        "top_ingredients":  ings,
        "cost_by_ingredient_type": {k: round(v, 2) for k, v in by_type.items()},
    }


@router.get("/api/bom-cost/cost-drivers")
@ttl_cache
def bom_cost_drivers(
    scope:      str           = Query("chain", description="chain | branch"),
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    limit:      int           = Query(60, ge=1, le=200),
):
    """Unified Top-N cost-driver SKUs (BoM COGS) used by ALL BoM-cost modes
    (overview / branch / compare) so they look identical. Returns per-SKU window
    COGS + %share + prev-period COGS + MoM% (MTD-aware, STANDING RULE) and a
    grand-total row. COGS via our BoM only (iron rule)."""
    brs = [branch] if (scope == "branch" and branch and branch != "ALL") else None
    where, params = _bom_cost_period_filters(month_from, month_to, brs)
    cogs_expr = _cogs_case("ssm", "sk", "sc")
    JOINS = ("FROM v_sales_sku_monthly ssm JOIN branches b ON b.branch_id=ssm.branch_id "
             "JOIN skus sk ON sk.sku_id=ssm.sku_id LEFT JOIN sku_cost sc ON sc.sku_id=ssm.sku_id")
    with get_db() as con:
        rows = con.execute(f"""
            SELECT ssm.sku_id, sk.sku_code, COALESCE(sk.description,'') description,
                   COALESCE(sk.business_type,'other') business_type,
                   COALESCE(SUM(ssm.qty),0) qty, COALESCE(SUM({cogs_expr}),0) total_cogs,
                   CASE WHEN SUM(ssm.qty)>0 THEN SUM({cogs_expr})/SUM(ssm.qty) ELSE 0 END unit_cost
            {JOINS} {where} GROUP BY ssm.sku_id HAVING total_cogs>0
            ORDER BY total_cogs DESC LIMIT :lim""", {**params, "lim": limit}).fetchall()
        tot = con.execute(f"SELECT COALESCE(SUM({cogs_expr}),0) c, COALESCE(SUM(ssm.qty),0) q {JOINS} {where}", params).fetchone()
        total_all = float(tot["c"] or 0); qty_all = float(tot["q"] or 0)
        ids = [r["sku_id"] for r in rows]

        # MoM per SKU: equal-period (MTD) when latest month incomplete, else month-over-month.
        mom_map: dict = {}; mom_basis = "month"; cutoff = None
        cur_tot = prev_tot = 0.0
        mtd = _mtd_window(con)
        if ids:
            phd = ",".join(f":k{i}" for i in range(len(ids)))
            kp = {f"k{i}": v for i, v in enumerate(ids)}
            bclause = ""
            if brs:
                bclause = " AND b.name=:cbr"; kp["cbr"] = branch
            if mtd:
                mom_basis = "mtd"; cutoff = mtd["cutoff_day"]
                ucx = _unified_cogs_expr("ssm")
                kp.update({"cs": mtd["cur_s"], "ce": mtd["cur_e"], "ps": mtd["prev_s"], "pe": mtd["prev_e"]})
                drows = con.execute(f"""
                    SELECT ssm.sku_id,
                      SUM(CASE WHEN ssm.day BETWEEN :cs AND :ce THEN ({ucx}) ELSE 0 END) cur,
                      SUM(CASE WHEN ssm.day BETWEEN :ps AND :pe THEN ({ucx}) ELSE 0 END) prev
                    FROM (SELECT branch_id, sku_id, qty, day, 'Easy' AS source FROM v_sales_sku_daily
                          WHERE sku_id IN ({phd}) AND (day BETWEEN :cs AND :ce OR day BETWEEN :ps AND :pe)) ssm
                    JOIN branches b ON b.branch_id=ssm.branch_id
                    WHERE b.name!='ALL'{bclause} GROUP BY ssm.sku_id""", kp).fetchall()
                for d in drows:
                    cur = float(d["cur"] or 0); prev = float(d["prev"] or 0)
                    cur_tot += cur; prev_tot += prev
                    mom_map[d["sku_id"]] = {"prev": round(prev, 2),
                                            "mom": round((cur - prev) / prev * 100, 1) if prev > 0 else None}
            else:
                # monthly: latest two months present in window
                mrows = con.execute(f"""SELECT DISTINCT ssm.period_month m {JOINS} {where} ORDER BY m DESC LIMIT 2""", params).fetchall()
                lm = mrows[0]["m"] if mrows else None
                pm = mrows[1]["m"] if len(mrows) > 1 else None
                if lm:
                    kp2 = {**kp, "lm": lm, "pm": pm or "__none__"}
                    mrows2 = con.execute(f"""
                        SELECT ssm.sku_id, ssm.period_month m, COALESCE(SUM({cogs_expr}),0) c
                        {JOINS} WHERE ssm.sku_id IN ({phd}) AND ssm.period_month IN (:lm,:pm){bclause}
                        GROUP BY ssm.sku_id, ssm.period_month""", kp2).fetchall()
                    tmp: dict = {}
                    for d in mrows2:
                        tmp.setdefault(d["sku_id"], {})[d["m"]] = float(d["c"] or 0)
                    for sid, mm in tmp.items():
                        cur = mm.get(lm, 0); prev = mm.get(pm) if pm else None
                        cur_tot += cur; prev_tot += (prev or 0)
                        mom_map[sid] = {"prev": round(prev, 2) if prev is not None else None,
                                        "mom": round((cur - prev) / prev * 100, 1) if (prev and prev > 0) else None}

        items = []
        for r in rows:
            mm = mom_map.get(r["sku_id"], {})
            items.append({
                "sku_code": r["sku_code"], "description": r["description"],
                "business_type": r["business_type"], "qty": round(float(r["qty"] or 0), 2),
                "unit_cost": round(float(r["unit_cost"] or 0), 2),
                "total_cogs": round(float(r["total_cogs"] or 0), 2),
                "pct_of_total": round(float(r["total_cogs"] or 0) / total_all * 100, 1) if total_all else None,
                "prev_cogs": mm.get("prev"), "mom_pct": mm.get("mom"),
            })
        shown = sum(i["total_cogs"] for i in items)
        total_mom = round((cur_tot - prev_tot) / prev_tot * 100, 1) if (mom_basis and prev_tot > 0) else None
        return {"scope": scope, "branch": branch, "mom_basis": mom_basis, "mtd_cutoff_day": cutoff,
                "items": items, "total": {
                    "total_cogs": round(total_all, 2), "qty": round(qty_all, 2),
                    "shown_cogs": round(shown, 2),
                    "shown_pct": round(shown / total_all * 100, 1) if total_all else None,
                    "prev_cogs": round(prev_tot, 2) if prev_tot else None, "mom_pct": total_mom}}


@router.get("/api/bom-cost/branch-foodcost-mom")
@ttl_cache
def bom_cost_branch_foodcost_mom(
    month: Optional[str] = Query(None, description="'Current' month (YYYY-MM); default = latest in data"),
    movers_n: int        = Query(5, description="How many top menu movers up/down per branch"),
    incl_service: bool   = Query(False, description="Count ex-VAT service charge as revenue (and in the Food-Cost% denominator)"),
):
    """Per-branch Food Cost % for the latest month vs the previous month (MoM),
    plus the top menu cost movers (increase/decrease) that drove each branch's change.
    Powers the Branch Food-Cost KPI boxes + drill-down."""
    cogs_expr       = _cogs_case('ssm', 'sk', 'sc')
    costed_rev_expr = _costed_rev_case('ssm', 'sk', 'sc')
    movers_n = max(1, min(20, int(movers_n)))

    with get_db() as con:
        months = [r["m"] for r in con.execute(
            "SELECT DISTINCT period_month AS m FROM sales_sku_monthly "
            "ORDER BY period_month DESC LIMIT 36"
        ).fetchall()]
        if not months:
            return {"month": None, "prev_month": None, "branches": []}
        cur_m  = month if (month and month in months) else months[0]
        idx    = months.index(cur_m)
        prev_m = months[idx + 1] if idx + 1 < len(months) else None

        ms = [cur_m] + ([prev_m] if prev_m else [])
        ph = ",".join(f":m{i}" for i in range(len(ms)))
        params = {f"m{i}": m for i, m in enumerate(ms)}

        agg_rows = con.execute(f"""
            SELECT b.name AS branch, ssm.period_month AS m,
                   COALESCE(SUM(ssm.subtotal_ex_vat), 0) AS rev,
                   COALESCE(SUM({cogs_expr}), 0)         AS cogs,
                   COALESCE(SUM({costed_rev_expr}), 0)   AS costed
              FROM v_sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
             WHERE b.name != 'ALL' AND COALESCE(b.active,1) = 1
               AND ssm.period_month IN ({ph})
             GROUP BY b.name, ssm.period_month
        """, params).fetchall()

        mover_rows = con.execute(f"""
            SELECT b.name AS branch, ssm.sku_id AS sku_id,
                   sk.sku_code AS sku_code,
                   COALESCE(sk.description,'') AS description,
                   ssm.period_month AS m,
                   COALESCE(SUM({cogs_expr}), 0) AS cogs,
                   COALESCE(SUM(ssm.qty), 0)     AS qty
              FROM v_sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
             WHERE b.name != 'ALL' AND COALESCE(b.active,1) = 1
               AND ssm.period_month IN ({ph})
             GROUP BY b.name, ssm.sku_id, ssm.period_month
        """, params).fetchall()

        svc_map = _service_charge_map(con, prev_m or cur_m, cur_m) if incl_service else {}

    # ── assemble per-branch aggregates (service charge folded into rev + costed) ──
    agg: Dict[str, dict] = {}
    for r in agg_rows:
        svc = svc_map.get((r["branch"], r["m"]), 0.0)
        d = agg.setdefault(r["branch"], {})
        d[r["m"]] = {
            "rev":    float(r["rev"]    or 0) + svc,
            "cogs":   float(r["cogs"]   or 0),
            "costed": float(r["costed"] or 0) + svc,   # service = revenue with no COGS
        }

    # ── per-branch menu movers (cur_cogs - prev_cogs) ───────────────────
    mv: Dict[str, Dict[int, dict]] = {}
    for r in mover_rows:
        bm = mv.setdefault(r["branch"], {})
        s  = bm.setdefault(r["sku_id"], {
            "sku_code": r["sku_code"], "description": r["description"],
            "cur": 0.0, "prev": 0.0, "cur_qty": 0.0, "prev_qty": 0.0,
        })
        if r["m"] == cur_m:
            s["cur"]      = float(r["cogs"] or 0)
            s["cur_qty"]  = float(r["qty"] or 0)
        elif prev_m and r["m"] == prev_m:
            s["prev"]     = float(r["cogs"] or 0)
            s["prev_qty"] = float(r["qty"] or 0)

    def _fc(v):
        return (v["cogs"] / v["rev"] * 100) if v.get("rev", 0) > 0 else None

    out = []
    for branch, d in agg.items():
        cur  = d.get(cur_m, {"rev": 0, "cogs": 0, "costed": 0})
        prev = d.get(prev_m, {"rev": 0, "cogs": 0, "costed": 0}) if prev_m else None
        fc      = _fc(cur)
        prev_fc = _fc(prev) if prev else None
        delta_pp = round(fc - prev_fc, 1) if (fc is not None and prev_fc is not None) else None

        # menu movers for this branch
        movers = []
        for sid, s in mv.get(branch, {}).items():
            delta = s["cur"] - s["prev"]
            if abs(delta) < 1:    # ignore noise under ฿1
                continue
            movers.append({
                "sku_code":    s["sku_code"],
                "description": s["description"],
                "cogs":        round(s["cur"], 2),
                "prev_cogs":   round(s["prev"], 2),
                "delta":       round(delta, 2),
                "qty":         round(s["cur_qty"], 2),
                "prev_qty":    round(s["prev_qty"], 2),
                "is_new":      s["prev"] <= 0 and s["cur"] > 0,
                "is_gone":     s["cur"] <= 0 and s["prev"] > 0,
            })
        ups   = sorted([m for m in movers if m["delta"] > 0], key=lambda x: -x["delta"])[:movers_n]
        downs = sorted([m for m in movers if m["delta"] < 0], key=lambda x: x["delta"])[:movers_n]

        out.append({
            "branch":             branch,
            "revenue":            round(cur["rev"], 2),
            "costed_revenue":     round(cur["costed"], 2),
            "prev_costed_revenue": round(prev["costed"], 2) if prev else None,
            "cogs":               round(cur["cogs"], 2),
            "food_cost_pct":      round(fc, 1) if fc is not None else None,
            "prev_revenue":       round(prev["rev"], 2)  if prev else None,
            "prev_cogs":          round(prev["cogs"], 2) if prev else None,
            "prev_food_cost_pct": round(prev_fc, 1) if prev_fc is not None else None,
            "delta_pp":           delta_pp,
            "cogs_delta":         round(cur["cogs"] - (prev["cogs"] if prev else 0), 2),
            "movers_up":          ups,
            "movers_down":        downs,
        })

    # worst (highest food cost %) first; None food-cost sinks to the bottom
    out.sort(key=lambda x: (x["food_cost_pct"] is None, -(x["food_cost_pct"] or 0)))
    return {"month": cur_m, "prev_month": prev_m, "incl_service": bool(incl_service), "branches": out}


@router.get("/api/bom-cost/compare")
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
              FROM v_sales_sku_monthly ssm
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
                FROM v_sales_sku_monthly ssm
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
                FROM v_sales_sku_monthly ssm
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
        gp     = rev - c
        out[r["branch"]].update({
            "revenue":        round(rev, 2),
            "cogs":           round(c, 2),
            "costed_revenue": round(costed, 2),
            "gp":             round(gp, 2),
            "gp_pct":         round(gp / rev * 100, 1) if rev > 0 else None,
            "food_cost_pct":  round(c / rev * 100, 1) if rev > 0 else None,
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


@router.get("/api/bom-cost/simulator-base")
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
              FROM v_sales_sku_monthly ssm
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
                   COALESCE(SUM(ssm.subtotal_ex_vat), 0) AS period_revenue,
                   COALESCE(SUM({cogs_expr}), 0)        AS period_cogs,
                   CASE WHEN SUM(ssm.qty) > 0 THEN SUM({cogs_expr})/SUM(ssm.qty) ELSE 0 END
                     AS current_unit_cost
              FROM v_sales_sku_monthly ssm
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
            "gp":             round(revenue - cogs, 2),
            "gp_pct":         round((revenue - cogs) / revenue * 100, 1) if revenue > 0 else None,
            "food_cost_pct":  round(cogs / revenue * 100, 1) if revenue > 0 else None,
        },
        "skus": [{
            "sku_id":            r["sku_id"],
            "sku_code":          r["sku_code"],
            "description":       r["description"],
            "business_type":     r["business_type"],
            "qty":               round(float(r["qty"] or 0), 2),
            "period_revenue":    round(float(r["period_revenue"] or 0), 2),
            "period_cogs":       round(float(r["period_cogs"] or 0), 2),
            "current_unit_cost": round(float(r["current_unit_cost"] or 0), 4),
        } for r in sku_rows],
    }


class ScenarioOverride(BaseModel):
    sku_code: str
    unit_cost: float
    qty: Optional[float] = None        # scenario serving count (None = unchanged)
    disabled: Optional[bool] = False   # menu closed / discontinued


def _ensure_scenario_cols(con):
    cols = {r["name"] for r in con.execute("PRAGMA table_info(cost_scenario_item)")}
    if "new_qty" not in cols:
        con.execute("ALTER TABLE cost_scenario_item ADD COLUMN new_qty REAL")
    if "disabled" not in cols:
        con.execute("ALTER TABLE cost_scenario_item ADD COLUMN disabled INTEGER DEFAULT 0")
    # Phase B (ROI ledger): per-item effective unit cost snapshotted at adoption.
    if "baseline_cost" not in cols:
        con.execute("ALTER TABLE cost_scenario_item ADD COLUMN baseline_cost REAL")
    hcols = {r["name"] for r in con.execute("PRAGMA table_info(cost_scenario)")}
    if "adopted_at" not in hcols:
        con.execute("ALTER TABLE cost_scenario ADD COLUMN adopted_at TEXT")
    if "adopted_by" not in hcols:
        con.execute("ALTER TABLE cost_scenario ADD COLUMN adopted_by TEXT")
    if "baseline_month" not in hcols:
        con.execute("ALTER TABLE cost_scenario ADD COLUMN baseline_month TEXT")


class ScenarioCreate(BaseModel):
    name: str
    description: Optional[str] = None
    overrides: List[ScenarioOverride]


class SimulateBody(BaseModel):
    overrides: List[ScenarioOverride]
    month_from: Optional[str] = None
    month_to: Optional[str] = None


@router.post("/api/bom-cost/scenarios", dependencies=[Depends(require_write_token)])
def create_scenario(body: ScenarioCreate):
    nm = (body.name or "").strip()
    if not nm:
        raise HTTPException(400, "name is required")
    with get_db() as con:
        _ensure_scenario_cols(con)
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
                    "INSERT OR REPLACE INTO cost_scenario_item(scenario_id, sku_id, unit_cost, new_qty, disabled) "
                    "VALUES(?,?,?,?,?)", (scen_id, sid, float(o.unit_cost),
                                          (float(o.qty) if o.qty is not None else None),
                                          1 if o.disabled else 0)
                )
                inserted += 1
        else:
            inserted = 0
        con.commit()
    invalidate_cache("list_scenarios", "get_scenario")
    return {"id": scen_id, "name": nm, "items_inserted": inserted}


@router.get("/api/bom-cost/scenarios")
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


@router.get("/api/bom-cost/scenarios/{scen_id}")
def get_scenario(scen_id: int):
    with get_db() as con:
        _ensure_scenario_cols(con)
        head = con.execute("SELECT * FROM cost_scenario WHERE id=?", (scen_id,)).fetchone()
        if head is None:
            raise HTTPException(404, "scenario not found")
        items = con.execute("""
            SELECT csi.sku_id, sk.sku_code,
                   COALESCE(sk.description,'') AS description,
                   COALESCE(sk.business_type,'other') AS business_type,
                   csi.unit_cost, csi.new_qty, COALESCE(csi.disabled,0) AS disabled
              FROM cost_scenario_item csi
              JOIN skus sk ON sk.sku_id = csi.sku_id
             WHERE csi.scenario_id=?
             ORDER BY sk.sku_code
        """, (scen_id,)).fetchall()
    return {"scenario": dict(head), "items": [dict(r) for r in items]}


@router.delete("/api/bom-cost/scenarios/{scen_id}", dependencies=[Depends(require_write_token)])
def delete_scenario(scen_id: int):
    with get_db() as con:
        cur = con.execute("UPDATE cost_scenario SET is_archived=1 WHERE id=?", (scen_id,))
        con.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "scenario not found")
    invalidate_cache("list_scenarios", "get_scenario")
    return {"id": scen_id, "archived": True}


@router.post("/api/bom-cost/simulate")
def simulate_scenario(body: SimulateBody):
    """Recompute chain + per-branch GP using the given unit_cost overrides."""
    where, params = _bom_cost_period_filters(body.month_from, body.month_to)
    cogs_expr       = _cogs_case('ssm', 'sk', 'sc')
    costed_rev_expr = _costed_rev_case('ssm', 'sk', 'sc')

    overrides     = {o.sku_code: float(o.unit_cost) for o in (body.overrides or [])}
    override_qty  = {o.sku_code: float(o.qty) for o in (body.overrides or []) if o.qty is not None}  # fix H3
    disabled_skus = {o.sku_code for o in (body.overrides or []) if o.disabled}                        # fix H3

    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.name AS branch, ssm.sku_id, sk.sku_code,
                   COALESCE(SUM(ssm.qty), 0)            AS qty,
                   COALESCE(SUM({cogs_expr}), 0)        AS base_cogs,
                   COALESCE(SUM({costed_rev_expr}), 0)  AS base_costed,
                   COALESCE(SUM(ssm.subtotal_ex_vat),0) AS revenue
              FROM v_sales_sku_monthly ssm
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
        if code in disabled_skus:         # fix H3: disabled items → 0 COGS + 0 costed rev
            new_c   = 0.0
            new_cst = 0.0
        elif code in overrides and base_c > 0:
            scen_qty = override_qty.get(code, qty)   # fix H3: use scenario qty if set
            new_c    = scen_qty * overrides[code]
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


# ── Phase B — Savings Ledger (provable ROI) ──────────────────────────────────
# Turn a what-if cost_scenario into a tracked outcome. On ADOPT we snapshot each
# item's effective unit cost (baseline). Thereafter "realized" saving = how much
# cheaper the SKU is NOW vs that baseline, × the qty sold since adoption — so it
# only accrues once the cheaper recipe is actually implemented. "potential" =
# baseline → scenario target (the goal). progress = realized / potential.

class AdoptBody(BaseModel):
    adopted_by: Optional[str] = None


@router.post("/api/bom-cost/scenarios/{scen_id}/adopt", dependencies=[Depends(require_write_token)])
def adopt_scenario(scen_id: int, body: Optional[AdoptBody] = None):
    """Mark a scenario adopted: snapshot per-item baseline effective cost + stamp
    adopted_at and the baseline (latest data) month."""
    with get_db() as con:
        _ensure_scenario_cols(con)
        head = con.execute("SELECT id FROM cost_scenario WHERE id=?", (scen_id,)).fetchone()
        if head is None:
            raise HTTPException(404, "scenario not found")
        _build_sku_effective_cost(con, force=False)   # ensure current costs exist
        bm = con.execute("SELECT MAX(period_month) m FROM v_sales_sku_monthly").fetchone()["m"]
        now = datetime.now().isoformat(timespec="seconds")
        items = con.execute("SELECT sku_id FROM cost_scenario_item WHERE scenario_id=?", (scen_id,)).fetchall()
        for it in items:
            base = con.execute("SELECT recipe_cost FROM sku_effective_cost WHERE sku_id=?",
                               (it["sku_id"],)).fetchone()
            con.execute("UPDATE cost_scenario_item SET baseline_cost=? WHERE scenario_id=? AND sku_id=?",
                        (base["recipe_cost"] if base else None, scen_id, it["sku_id"]))
        con.execute("UPDATE cost_scenario SET adopted_at=?, adopted_by=?, baseline_month=? WHERE id=?",
                    (now, (body.adopted_by if body else None), bm, scen_id))
        con.commit()
    invalidate_cache("list_scenarios", "get_scenario")
    return {"id": scen_id, "adopted_at": now, "baseline_month": bm, "items": len(items)}


@router.post("/api/bom-cost/scenarios/{scen_id}/unadopt", dependencies=[Depends(require_write_token)])
def unadopt_scenario(scen_id: int):
    """Revert adoption (clears the ledger baseline)."""
    with get_db() as con:
        _ensure_scenario_cols(con)
        if con.execute("SELECT id FROM cost_scenario WHERE id=?", (scen_id,)).fetchone() is None:
            raise HTTPException(404, "scenario not found")
        con.execute("UPDATE cost_scenario SET adopted_at=NULL, adopted_by=NULL, baseline_month=NULL WHERE id=?", (scen_id,))
        con.execute("UPDATE cost_scenario_item SET baseline_cost=NULL WHERE scenario_id=?", (scen_id,))
        con.commit()
    invalidate_cache("list_scenarios", "get_scenario")
    return {"id": scen_id, "adopted": False}


@router.get("/api/bom-cost/scenarios/{scen_id}/savings")
def scenario_savings(scen_id: int):
    """Realized-vs-potential cost savings for an adopted scenario, with a
    cumulative monthly series. Honest: realized stays 0 until the cheaper cost is
    actually in the data (current effective cost < adoption baseline)."""
    with get_db() as con:
        _ensure_scenario_cols(con)
        head = con.execute("SELECT * FROM cost_scenario WHERE id=?", (scen_id,)).fetchone()
        if head is None:
            raise HTTPException(404, "scenario not found")
        head = dict(head)
        if not head.get("adopted_at"):
            return {"scenario": {"id": scen_id, "name": head["name"]}, "adopted": False}
        _build_sku_effective_cost(con, force=False)
        bm = head.get("baseline_month")
        rows = con.execute("""
            SELECT csi.sku_id, sk.sku_code, COALESCE(sk.description,'') AS description,
                   csi.unit_cost AS target_cost, csi.baseline_cost,
                   (SELECT recipe_cost FROM sku_effective_cost se WHERE se.sku_id=csi.sku_id) AS current_cost
              FROM cost_scenario_item csi JOIN skus sk ON sk.sku_id=csi.sku_id
             WHERE csi.scenario_id=? AND COALESCE(csi.disabled,0)=0
        """, (scen_id,)).fetchall()
        items = [dict(r) for r in rows]
        sku_ids = [it["sku_id"] for it in items]
        qty_by, months = {}, set()
        if sku_ids and bm:
            ph = ",".join("?" * len(sku_ids))
            for r in con.execute(
                f"""SELECT sku_id, period_month, SUM(qty) q FROM v_sales_sku_monthly
                     WHERE sku_id IN ({ph}) AND period_month >= ?
                     GROUP BY sku_id, period_month""", (*sku_ids, bm)).fetchall():
                qty_by[(r["sku_id"], r["period_month"])] = r["q"] or 0
                months.add(r["period_month"])
    months = sorted(months)
    per_item, monthly = [], {m: 0.0 for m in months}
    realized_total = potential_total = 0.0
    for it in items:
        base = it["baseline_cost"]
        if base is None:
            continue
        cur = it["current_cost"] if it["current_cost"] is not None else base
        tgt = it["target_cost"] if it["target_cost"] is not None else cur
        unit_real = base - cur          # achieved so far (now vs adoption baseline)
        unit_pot  = base - tgt          # full goal (baseline vs scenario target)
        qty_total = sum(qty_by.get((it["sku_id"], m), 0) for m in months)
        r_save, p_save = unit_real * qty_total, unit_pot * qty_total
        realized_total += r_save
        potential_total += p_save
        for m in months:
            monthly[m] += unit_real * qty_by.get((it["sku_id"], m), 0)
        per_item.append({
            "sku_code": it["sku_code"], "description": it["description"],
            "baseline_cost": round(base, 4), "current_cost": round(cur, 4),
            "target_cost": round(tgt, 4), "qty_since_adopt": round(qty_total, 0),
            "realized_saving": round(r_save, 2), "potential_saving": round(p_save, 2),
            "unit_progress_pct": round(unit_real / unit_pot * 100, 1) if unit_pot > 0 else None,
        })
    cum, series = 0.0, []
    for m in months:
        cum += monthly[m]
        series.append({"month": m, "realized": round(monthly[m], 2), "cumulative": round(cum, 2)})
    per_item.sort(key=lambda r: -r["realized_saving"])
    return {
        "scenario": {"id": scen_id, "name": head["name"], "adopted_at": head.get("adopted_at"),
                     "adopted_by": head.get("adopted_by"), "baseline_month": bm},
        "adopted": True,
        "totals": {
            "realized_saving": round(realized_total, 2),
            "potential_saving": round(potential_total, 2),
            "progress_pct": round(realized_total / potential_total * 100, 1) if potential_total > 0 else None,
            "months_tracked": len(months),
        },
        "items": per_item,
        "series": series,
    }


@router.get("/api/bom-cost/recipe/{sku_code}")
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
