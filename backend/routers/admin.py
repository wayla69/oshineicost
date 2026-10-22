"""Branch master data (fixed/labour costs, active flags), SKUs, monthly targets,
closed hours and app settings — the owner-edited reference tables.

Moved out of main.py in Phase 2b (2026-09) by the AST: these endpoints and
the helpers only they use, unchanged. URLs are unchanged.
"""
from fastapi import APIRouter
from core import get_db
from core import invalidate_cache
from core import require_write_token
from targets import targets_for_month
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from pydantic import BaseModel
from typing import Dict
from typing import List
from typing import Optional
import json as _json

router = APIRouter()


# ---------------------------------------------------------------------------
# /api/branches
# ---------------------------------------------------------------------------
@router.get("/api/branches")
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


@router.put("/api/branches/{branch_id}/type", dependencies=[Depends(require_write_token)])
def update_branch_type(branch_id: int, body: BranchTypeUpdate):
    if body.branch_type not in ("master", "franchise"):
        raise HTTPException(400, "branch_type must be 'master' or 'franchise'")
    with get_db() as con:
        con.execute("UPDATE branches SET branch_type=? WHERE branch_id=?", (body.branch_type, branch_id))
        con.commit()
    invalidate_cache()
    return {"ok": True}


# ---------------------------------------------------------------------------
# /api/closed_hours   -  operating hours per branch per day-of-week
# ---------------------------------------------------------------------------
class ClosedHoursEntry(BaseModel):
    branch_id: int
    dow:       int   # 0=Sun â€¦ 6=Sat
    hour_from: int   # 0..23 inclusive (open hour)
    hour_to:   int   # 0..23 inclusive (last open hour)
    is_closed: bool = False


@router.get("/api/closed_hours")
def get_closed_hours():
    with get_db() as con:
        rows = con.execute(
            """
            SELECT b.branch_id, b.name AS branch, ch.dow,
                   COALESCE(ch.hour_from, 10)  AS hour_from,
                   COALESCE(ch.hour_to,   21)  AS hour_to,
                   COALESCE(ch.is_closed, 0)   AS is_closed
            FROM branches b
            LEFT JOIN closed_hours ch ON ch.branch_id = b.branch_id
            WHERE b.name != 'ALL'
            ORDER BY b.name, ch.dow
            """
        ).fetchall()
    return [dict(r) for r in rows]


@router.put("/api/closed_hours", dependencies=[Depends(require_write_token)])
def put_closed_hours(entries: List[ClosedHoursEntry]):
    # fix D12: validate ranges before touching the DB
    for e in entries:
        if not (0 <= e.dow <= 6):
            raise HTTPException(400, f"dow must be 0-6, got {e.dow}")
        if not (0 <= e.hour_from <= 23):
            raise HTTPException(400, f"hour_from must be 0-23, got {e.hour_from}")
        if not (0 <= e.hour_to <= 23):
            raise HTTPException(400, f"hour_to must be 0-23, got {e.hour_to}")
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
@router.get("/api/settings")
def get_settings():
    with get_db() as con:
        rows = con.execute("SELECT key, value FROM app_settings").fetchall()
    # cloud_sync_status holds the pull's log tail (backend/cloud_sync.py): Management reads it from
    # /api/admin/cloud-sync/status (cloud_backup_status: /api/admin/cloud-backup/status), it is not a preference
    # every signed-in role should be handed
    return {r["key"]: r["value"] for r in rows if not r["key"].startswith(("cloud_sync_", "cloud_backup_"))}


@router.put("/api/settings", dependencies=[Depends(require_write_token)])
def put_settings(body: Dict[str, str]):
    with get_db() as con:
        cur = con.cursor()
        for k, v in body.items():
            cur.execute(
                "INSERT OR REPLACE INTO app_settings(key,value) VALUES(?,?)", (k, str(v))
            )
        con.commit()
    # settings feed cached endpoints (e.g. discount_alerts thresholds) → drop stale
    invalidate_cache("analysis_discount_alerts", "analysis_discounts", "report_management",
                     "home_brief")     # the food-cost band drives the home tile
    return {"ok": True}


# ---------------------------------------------------------------------------
# /api/branches/{id}   -  PUT: rename a branch
# ---------------------------------------------------------------------------
@router.put("/api/branches/{branch_id}", dependencies=[Depends(require_write_token)])
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


@router.put("/api/branches/{branch_id}/active", dependencies=[Depends(require_write_token)])
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
@router.get("/api/skus")
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


@router.put("/api/skus/{sku_code}/active", dependencies=[Depends(require_write_token)])
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
@router.get("/api/targets")
def get_targets(month: Optional[str] = Query(None)):
    """Every branch with its target for the month: a figure typed here wins
    (source 'manual'), otherwise the branch's own actual of the same month
    last year (source 'last_year' — the owner's rule, see targets.py), else 0
    (source 'none'). No month → the latest data month."""
    with get_db() as con:
        if not month:
            row = con.execute("SELECT MAX(strftime('%Y-%m', sold_at)) AS m FROM sales_transaction WHERE source = 'Easy'").fetchone()
            month = (row["m"] if row else None) or ""
        if not month:
            return []
        return targets_for_month(con, month)


@router.get("/api/targets/suggest")
def suggest_targets(
    month:    Optional[str] = Query(None, description="target month (default: latest data month)"),
    uplift:   float = Query(1.05, ge=0.5, le=3.0, description="stretch factor vs trailing avg"),
    lookback: int   = Query(3, ge=1, le=12),
):
    """Suggest per-branch revenue/customer targets from each branch's trailing
    `lookback`-month average × `uplift`. Read-only — the client fills the editor so
    the owner reviews before saving. Seeds the (currently empty) branch_targets."""
    with get_db() as con:
        latest = con.execute("SELECT MAX(period_month) m FROM v_sales_sku_monthly").fetchone()["m"]
        target_month = month or latest
        mrows = con.execute(
            "SELECT DISTINCT period_month FROM sales_sku_monthly WHERE period_month < ? "
            "ORDER BY period_month DESC LIMIT ?", (target_month, lookback)).fetchall()
        mlist = [r["period_month"] for r in mrows]
        if not mlist:
            return {"month": target_month, "uplift": uplift, "lookback": 0,
                    "trailing_months": [], "suggestions": []}
        ph = ",".join("?" * len(mlist))
        rev = con.execute(
            f"""SELECT b.name br, AVG(m.rev) avg_rev
                  FROM (SELECT branch_id, period_month, SUM(subtotal_ex_vat) rev
                          FROM v_sales_sku_monthly WHERE period_month IN ({ph})
                         GROUP BY branch_id, period_month) m
                  JOIN branches b ON b.branch_id = m.branch_id
                 WHERE b.name != 'ALL' AND COALESCE(b.active,1) = 1
                 GROUP BY b.name""", mlist).fetchall()
        cust = con.execute(
            f"""SELECT b.name br, AVG(m.c) avg_c
                  FROM (SELECT branch_id, strftime('%Y-%m', sold_at) pm, SUM(customer_count) c
                          FROM sales_transaction WHERE source='Easy'
                           AND strftime('%Y-%m', sold_at) IN ({ph})
                         GROUP BY branch_id, pm) m
                  JOIN branches b ON b.branch_id = m.branch_id
                 WHERE b.name != 'ALL' GROUP BY b.name""", mlist).fetchall()
    cmap = {r["br"]: (r["avg_c"] or 0) for r in cust}
    out = [{
        "branch": r["br"],
        "revenue_target":  round((r["avg_rev"] or 0) * uplift, -3),   # nearest ฿1,000
        "customer_target": int(round(cmap.get(r["br"], 0) * uplift)),
    } for r in rev]
    out.sort(key=lambda x: -x["revenue_target"])
    return {"month": target_month, "uplift": uplift, "lookback": len(mlist),
            "trailing_months": mlist, "suggestions": out}


@router.put("/api/targets", dependencies=[Depends(require_write_token)])
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


def _assert_known_branch_keys(con, costs: Dict[str, float], setting_label: str):
    """Reject cost payloads whose keys don't match a real branches.name.

    Guards against silently persisting corrupt/garbled keys — e.g. the
    branch_fixed_costs '?????'-key incident where Thai names were mangled to
    literal ASCII '?' at write time, so every P&L fixed.get(name, 0) read 0 and
    operating profit collapsed to gross profit. A bad key now fails loudly
    (HTTP 422) and nothing is saved, instead of corrupting the settings row.
    """
    valid = {r["name"] for r in con.execute("SELECT name FROM branches")}
    unknown = sorted(k for k in costs if k not in valid)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={
                "error": f"Unknown branch name(s) in {setting_label}; nothing saved.",
                "unknown_keys": unknown,
                "valid_branches": sorted(valid),
            },
        )


@router.get("/api/branches/fixed-costs")
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


@router.post("/api/branches/fixed-costs", dependencies=[Depends(require_write_token)])
def save_branch_fixed_costs(body: BranchFixedCostBody):
    """Save per-branch monthly fixed costs."""
    value = _json.dumps(body.costs)
    with get_db() as con:
        _assert_known_branch_keys(con, body.costs, "branch_fixed_costs")
        con.execute(
            "INSERT OR REPLACE INTO app_settings(key, value) VALUES('branch_fixed_costs', :v)",
            {"v": value},
        )
        con.commit()
    invalidate_cache("buffet_pnl", "pnl_branch")   # cached P&L reads fixed costs
    return {"saved": len(body.costs)}


@router.get("/api/branches/labor-costs")
def get_branch_labor_costs():
    """Return {branch_name: monthly_labor_cost} from app_settings."""
    with get_db() as con:
        row = con.execute("SELECT value FROM app_settings WHERE key='branch_labor_costs'").fetchone()
    if row:
        try: return _json.loads(row["value"])
        except Exception: return {}
    return {}


class BranchLaborCostBody(BaseModel):
    costs: Dict[str, float]   # {branch_name: monthly_labor_cost}


@router.post("/api/branches/labor-costs", dependencies=[Depends(require_write_token)])
def save_branch_labor_costs(body: BranchLaborCostBody):
    """Save per-branch monthly labor cost (the largest CONTROLLABLE cost)."""
    value = _json.dumps(body.costs)
    with get_db() as con:
        _assert_known_branch_keys(con, body.costs, "branch_labor_costs")
        con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES('branch_labor_costs', :v)", {"v": value})
        con.commit()
    invalidate_cache("buffet_pnl", "pnl_branch")   # cached P&L reads labor costs
    return {"saved": len(body.costs)}


# =============================================================================
# Phase 17c — business_type management
# =============================================================================
_VALID_BUSINESS_TYPES = {"buffet_tier", "buffet", "alacarte", "beverage",
                          "takeaway", "fee", "other"}


class BusinessTypeUpdate(BaseModel):
    business_type: str


@router.put("/api/skus/{sku_code}/business-type", dependencies=[Depends(require_write_token)])
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


# ---------------------------------------------------------------------------
# /api/branch-downtime  -  days a branch was legitimately not trading
# ---------------------------------------------------------------------------
# The data-health check (/api/data-quality) cannot tell a closure from a sync
# that lost a day — the POS has no "closed today" record. These rows are the
# human answer; the check then reports them as their own benign finding rather
# than dropping them, so a wrong entry stays visible. Same table the
# backend/mark_downtime.py CLI writes, and it is preserved across the daily push
# (core._PRESERVED_EXACT) because nothing can re-derive it.
_DOWNTIME_REASONS = ("closed", "pre_open", "renovation", "disaster")


class DowntimeEntry(BaseModel):
    branch:    str
    date_from: str
    date_to:   str
    reason:    str = "closed"
    note:      str = ""


def _iso_or_400(s: str, field: str) -> str:
    import datetime as _dt
    try:
        _dt.date.fromisoformat((s or "").strip())
    except ValueError:
        raise HTTPException(400, f"{field} ต้องเป็นวันที่รูปแบบ YYYY-MM-DD")
    return s.strip()


@router.get("/api/branch-downtime")
def list_branch_downtime():
    with get_db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS branch_downtime (
                id INTEGER PRIMARY KEY AUTOINCREMENT, branch TEXT NOT NULL,
                date_from TEXT NOT NULL, date_to TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT 'closed', note TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(branch, date_from, date_to))""")
        rows = con.execute("SELECT * FROM branch_downtime ORDER BY branch, date_from").fetchall()
    out = []
    for r in rows:
        import datetime as _dt
        days = (_dt.date.fromisoformat(r["date_to"]) - _dt.date.fromisoformat(r["date_from"])).days + 1
        out.append({**dict(r), "days": days})
    return {"reasons": list(_DOWNTIME_REASONS), "rows": out}


@router.post("/api/branch-downtime", dependencies=[Depends(require_write_token)])
def add_branch_downtime(body: DowntimeEntry):
    d_from = _iso_or_400(body.date_from, "date_from")
    d_to = _iso_or_400(body.date_to, "date_to")
    if d_from > d_to:
        raise HTTPException(400, "date_from ต้องไม่เกิน date_to")
    if body.reason not in _DOWNTIME_REASONS:
        raise HTTPException(400, f"reason ต้องเป็นหนึ่งใน {list(_DOWNTIME_REASONS)}")
    branch = (body.branch or "").strip()
    with get_db() as con:
        # A typo would record downtime against a branch nobody can see, and the
        # false "missing data" alarm it was meant to silence would stay up.
        if not con.execute("SELECT 1 FROM branches WHERE name=?", (branch,)).fetchone():
            raise HTTPException(400, f"ไม่พบสาขา '{branch}'")
        try:
            con.execute("INSERT INTO branch_downtime(branch,date_from,date_to,reason,note) "
                        "VALUES (?,?,?,?,?)", (branch, d_from, d_to, body.reason, body.note or ""))
            con.commit()
        except Exception as e:
            if "UNIQUE" in str(e):
                raise HTTPException(409, "ช่วงวันนี้ของสาขานี้ถูกบันทึกไว้แล้ว")
            raise
    invalidate_cache("data_quality")
    return {"ok": True}


@router.delete("/api/branch-downtime/{row_id}", dependencies=[Depends(require_write_token)])
def delete_branch_downtime(row_id: int):
    with get_db() as con:
        cur = con.execute("DELETE FROM branch_downtime WHERE id=?", (row_id,))
        con.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "ไม่พบรายการนี้")
    invalidate_cache("data_quality")
    return {"ok": True, "deleted": row_id}
