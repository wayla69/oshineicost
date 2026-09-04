"""The ingredient order plan: sales × recipe → what each branch should order,
pack sizes, central-vs-branch sourcing, issued cycles, actuals, compliance
letters, the festival calendar and buffer accuracy.

Moved out of main.py in Phase 2b (2026-09) by the AST: these endpoints and
the helpers only they use, unchanged. URLs are unchanged.
"""
from fastapi import APIRouter
from core import _group_filter
from core import _ingredient_family
from core import get_db
from core import invalidate_cache
from core import require_write_token
from core import ttl_cache
from datetime import datetime
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Response
from pydantic import BaseModel
from rules import EASY_TIER_CODES_SQL as _EASY_TIER_CODES_SQL
from typing import Dict
from typing import List
from typing import Optional

router = APIRouter()


_ING_BOM_MAP = """(
        SELECT sku_id AS sale_sku, sku_id AS bom_sku
          FROM (SELECT DISTINCT sku_id FROM bom_ingredients)
        UNION ALL
        SELECT s.sku_id, a.resolved_sku_id
          FROM bom_sku_alias a
          JOIN skus s ON s.sku_code = a.bom_sku_code
         WHERE s.sku_id NOT IN (SELECT DISTINCT sku_id FROM bom_ingredients)
    )"""


# Units that are counted in small base units and read better scaled up on a
# purchase order (2,400 กรัม → 2.4 กก.).
_ING_SCALE = {"กรัม": (1000.0, "กก."), "มิลลิลิตร": (1000.0, "ลิตร")}


def _ing_pretty(qty: float, unit: str):
    """(value, unit_label, text) for an order line — scales g→kg, ml→L."""
    q = float(qty or 0)
    u = (unit or "").strip()
    f, big = _ING_SCALE.get(u, (None, None))
    if f and q >= f:
        v = q / f
        return v, big, f"{v:,.2f} {big}"
    dec = 0 if q >= 100 else (1 if q >= 10 else 2)
    return q, u, f"{q:,.{dec}f} {u}".rstrip()


def _ing_window(con, days: int, date_to: Optional[str]):
    """Resolve the lookback window on sales_sku_daily. Returns (start, end, days)."""
    maxd = con.execute("SELECT MAX(day) FROM v_sales_sku_daily").fetchone()[0]
    if not maxd:
        return None, None, 0
    end = date_to or maxd
    if end > maxd:
        end = maxd
    days = max(1, min(int(days or 28), 365))
    start = con.execute("SELECT date(:e, :off)", {"e": end, "off": f"-{days - 1} day"}).fetchone()[0]
    return start, end, days


_ING_ORDER_DDL = """
CREATE TABLE IF NOT EXISTS ingredient_order_unit (
  ingredient_code TEXT PRIMARY KEY,
  base_unit       TEXT NOT NULL,
  order_unit      TEXT NOT NULL,
  qty_per_order   REAL NOT NULL,
  price_per_order REAL,
  moq             REAL DEFAULT 1,
  round_step      REAL DEFAULT 1,
  note            TEXT,
  source          TEXT,
  updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
)"""


# base unit -> (order unit label, base units per order unit)
_ING_UNIT_FALLBACK = {
    "กรัม": ("กก.", 1000.0),
    "มิลลิลิตร": ("ลิตร", 1000.0),
}


def _ing_order_units(con) -> Dict[str, dict]:
    con.execute(_ING_ORDER_DDL)
    return {r["ingredient_code"]: dict(r)
            for r in con.execute("SELECT * FROM ingredient_order_unit")}


def _ing_to_order(need_base: float, unit: str, pack: Optional[dict]):
    """Base-unit demand -> whole ordering units, always rounded UP.

    Returns the pack description, how many to order, and what actually arrives
    (packs x pack size) so the branch can see the carry-over it is accepting."""
    import math as _m
    if pack:
        per = float(pack.get("qty_per_order") or 0) or 1.0
        label = pack.get("order_unit") or unit
        step = float(pack.get("round_step") or 1) or 1.0
        moq = float(pack.get("moq") or 0)
        price = pack.get("price_per_order")
        known = True
    else:
        label, per = _ING_UNIT_FALLBACK.get(unit or "", (unit or "หน่วย", 1.0))
        step, moq, price, known = 1.0, 0.0, None, False
    units = 0.0
    if need_base > 0:
        units = _m.ceil((need_base / per) / step) * step
        if moq and units < moq:
            units = moq
    return {
        "order_unit": label,
        "qty_per_order": round(per, 4),
        "units": int(units) if float(units).is_integer() else round(units, 2),
        "actual_base": round(units * per, 2),
        "price_per_order": price,
        "pack_known": known,
    }


_ING_SOURCING_DDL = """
CREATE TABLE IF NOT EXISTS ingredient_sourcing (
  ingredient_code TEXT NOT NULL,
  branch          TEXT NOT NULL DEFAULT '',
  mode            TEXT NOT NULL DEFAULT 'central',
  note            TEXT,
  updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (ingredient_code, branch)
)"""


def _ing_sourcing(con):
    """Returns (defaults, per_branch) so a lookup is default → branch override."""
    con.execute(_ING_SOURCING_DDL)
    defaults, per_branch = {}, {}
    for r in con.execute("SELECT ingredient_code, branch, mode FROM ingredient_sourcing"):
        mode = (r["mode"] or "central").lower()
        if r["branch"]:
            per_branch[(r["ingredient_code"], r["branch"])] = mode
        else:
            defaults[r["ingredient_code"]] = mode
    return defaults, per_branch


def _ing_mode(code: str, branch: str, defaults: dict, per_branch: dict) -> str:
    if not code:
        return "central"
    return per_branch.get((code, branch or ""), defaults.get(code, "central"))


def _ing_plan_rows(con, start: str, end: str, days: int, branch: Optional[str],
                   group: Optional[str], basis: str, buffer_pct: float,
                   ing_type: Optional[str], q: Optional[str], group_family: bool,
                   per_branch: bool, include: str = "all"):
    """Core aggregation: consumption per ingredient (optionally per branch)."""
    filters = ["b.name != 'ALL'", "d.day BETWEEN :start AND :end"]
    params: Dict = {"start": start, "end": end}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    if ing_type:
        filters.append("bi.ingredient_type = :itype")
        params["itype"] = ing_type
    if q:
        filters.append("(bi.ingredient_name LIKE :q OR bi.ingredient_code LIKE :q)")
        params["q"] = f"%{q}%"

    # trend windows: last 7 days of the range vs the 7 before it
    params["r_start"] = con.execute("SELECT date(:e,'-6 day')", {"e": end}).fetchone()[0]
    params["p_start"] = con.execute("SELECT date(:e,'-13 day')", {"e": end}).fetchone()[0]

    key_cols = "b.name AS branch, " if per_branch else ""
    grp_cols = "b.branch_id, " if per_branch else ""
    sql = f"""
        SELECT {key_cols}
               COALESCE(NULLIF(bi.ingredient_code,''), '__' || bi.ingredient_name) AS ing_key,
               MAX(bi.ingredient_code) AS code,
               MAX(bi.ingredient_name) AS name,
               MAX(bi.ingredient_type) AS type,
               bi.unit                 AS unit,
               SUM(d.qty * bi.qty_per_serving) AS qty,
               SUM(CASE WHEN d.day >= :r_start THEN d.qty * bi.qty_per_serving ELSE 0 END) AS qty_recent,
               SUM(CASE WHEN d.day >= :p_start AND d.day < :r_start
                        THEN d.qty * bi.qty_per_serving ELSE 0 END) AS qty_prev,
               SUM(d.qty * bi.ingredient_cost) AS cost,
               COUNT(DISTINCT d.sku_id) AS n_menus
          FROM v_sales_sku_daily d
          JOIN branches b         ON b.branch_id = d.branch_id
          JOIN {_ING_BOM_MAP} m   ON m.sale_sku  = d.sku_id
          JOIN bom_ingredients bi ON bi.sku_id   = m.bom_sku
         WHERE {' AND '.join(filters)}
         GROUP BY {grp_cols}ing_key, bi.unit
    """
    raw = con.execute(sql, params).fetchall()

    # trading days per branch inside the window (a branch that opened mid-window,
    # or closed for refurbishment, must not be averaged over days it never sold)
    tf = ["b.name != 'ALL'", "d.day BETWEEN :start AND :end"]
    tp: Dict = {"start": start, "end": end}
    if branch:
        tf.append("b.name = :branch")
        tp["branch"] = branch
    _group_filter(tf, tp, group)
    trading, trading_recent = {}, {}
    for r in con.execute(f"""
        SELECT b.name AS branch, COUNT(DISTINCT d.day) AS nd,
               COUNT(DISTINCT CASE WHEN d.day >= date(:end,'-6 day') THEN d.day END) AS nr
          FROM v_sales_sku_daily d JOIN branches b ON b.branch_id = d.branch_id
         WHERE {' AND '.join(tf)} GROUP BY b.branch_id
    """, tp):
        trading[r["branch"]] = int(r["nd"] or 0)
        trading_recent[r["branch"]] = int(r["nr"] or 0)
    # chain-wide rows span every branch, so the window itself is the right divisor
    scope_days = trading.get(branch, days) if branch else days
    scope_recent = trading_recent.get(branch, min(7, days)) if branch else min(7, days)

    recent_days = min(7, days)
    out = []
    for r in raw:
        qty = float(r["qty"] or 0)
        if qty <= 0:
            continue
        cost = float(r["cost"] or 0)
        bkey = r["branch"] if per_branch else branch
        d_eff = (trading.get(bkey, days) if per_branch else scope_days) or days
        r_eff = (trading_recent.get(bkey, recent_days) if per_branch else scope_recent) or recent_days
        per_day_avg = qty / d_eff
        per_day_recent = float(r["qty_recent"] or 0) / r_eff
        per_day = per_day_recent if (basis == "recent" and per_day_recent > 0) else per_day_avg
        mult = 1.0 + (buffer_pct / 100.0)
        unit_cost = (cost / qty) if qty else 0.0
        prev = float(r["qty_prev"] or 0)
        trend = None
        if prev > 0 and days >= 14:
            trend = round(100.0 * (float(r["qty_recent"] or 0) - prev) / prev, 1)
        row = {
            "code": r["code"] or "",
            "name": r["name"] or "",
            "type": r["type"] or "other",
            "unit": r["unit"] or "",
            "qty_used": round(qty, 2),
            "per_day": round(per_day, 3),
            "per_week": round(per_day * 7, 2),
            "per_month": round(per_day * 30, 2),
            "order_week": round(per_day * 7 * mult, 2),
            "order_month": round(per_day * 30 * mult, 2),
            "unit_cost": round(unit_cost, 4),
            "cost_used": round(cost, 2),
            "value_week": round(per_day * 7 * mult * unit_cost, 2),
            "value_month": round(per_day * 30 * mult * unit_cost, 2),
            "n_menus": int(r["n_menus"] or 0),
            "trend_pct": trend,
        }
        if per_branch:
            row["branch"] = r["branch"]
        out.append(row)

    # tag sourcing BEFORE the family rollup — the rollup keys on it so a
    # central item never merges into the same line as a branch-sourced one
    src_def, src_branch = _ing_sourcing(con)
    for r in out:
        r["sourcing"] = _ing_mode(r["code"], r.get("branch") or branch or "",
                                  src_def, src_branch)
    if include == "central":
        out = [r for r in out if r["sourcing"] == "central"]
    elif include == "local":
        out = [r for r in out if r["sourcing"] == "local"]

    if group_family:
        fam: Dict[str, dict] = {}
        for r in out:
            k = ((r.get("branch") or "") + "|" + _ingredient_family(r["name"])
                 + "|" + (r["unit"] or "") + "|" + (r.get("sourcing") or ""))
            f = fam.get(k)
            if not f:
                f = dict(r)
                f["name"] = _ingredient_family(r["name"])
                f["code"] = ""
                f["n_variants"] = 0
                fam[k] = f
            else:
                for fld in ("qty_used", "per_day", "per_week", "per_month",
                            "order_week", "order_month", "cost_used",
                            "value_week", "value_month"):
                    f[fld] = round(f[fld] + r[fld], 3)
                f["n_menus"] = max(f["n_menus"], r["n_menus"])
            f["n_variants"] += 1
        out = list(fam.values())
        for f in out:
            f["unit_cost"] = round(f["cost_used"] / f["qty_used"], 4) if f["qty_used"] else 0

    packs = _ing_order_units(con)
    seen_units: Dict[str, set] = {}
    for r in out:
        key = (r.get("branch") or "") + "|" + (r["code"] or r["name"])
        seen_units.setdefault(key, set()).add(r["unit"])
    for r in out:
        key = (r.get("branch") or "") + "|" + (r["code"] or r["name"])
        r["mixed_unit"] = len(seen_units.get(key, ())) > 1
        r["order_week_disp"] = _ing_pretty(r["order_week"], r["unit"])[2]
        r["order_month_disp"] = _ing_pretty(r["order_month"], r["unit"])[2]
        r["per_day_disp"] = _ing_pretty(r["per_day"], r["unit"])[2]
        # consumption -> whole ordering units (rounded UP)
        pk = packs.get(r["code"]) if r["code"] else None
        if pk and (pk.get("base_unit") or r["unit"]) != r["unit"]:
            pk = None                      # pack is for a different base unit
        for hz in ("week", "month"):
            o = _ing_to_order(r["order_" + hz], r["unit"], pk)
            r["order_unit"] = o["order_unit"]
            r["qty_per_order"] = o["qty_per_order"]
            r["pack_known"] = o["pack_known"]
            r[f"order_{hz}_units"] = o["units"]
            r[f"order_{hz}_actual"] = o["actual_base"]
            r[f"order_{hz}_units_disp"] = (
                f"{o['units']:,g} {o['order_unit']}"
                # the carry-over only matters when a real pack size forces it;
                # on the กก./ลิตร fallback the parenthetical just repeats itself
                + (f" ({_ing_pretty(o['actual_base'], r['unit'])[2]})"
                   if o["pack_known"] and o["qty_per_order"] != 1 else ""))
            if o["price_per_order"]:
                r[f"value_{hz}"] = round(o["units"] * float(o["price_per_order"]), 2)
    out.sort(key=lambda x: x["value_month"], reverse=True)
    return out


def _ing_trading_days(con, start: str, end: str, branch: str) -> int:
    """Days this branch actually sold inside the window (the averaging divisor)."""
    row = con.execute(
        "SELECT COUNT(DISTINCT d.day) FROM v_sales_sku_daily d "
        "JOIN branches b ON b.branch_id = d.branch_id "
        "WHERE b.name = :branch AND d.day BETWEEN :start AND :end",
        {"branch": branch, "start": start, "end": end}).fetchone()
    return int(row[0] or 0)


def _ing_reapply_order_units(con, rows):
    """Re-round to whole packs after the seasonal uplift — scaling the already
    rounded number would leave fractional packs on the order."""
    packs = _ing_order_units(con)
    for r in rows:
        pk = packs.get(r["code"]) if r["code"] else None
        if pk and (pk.get("base_unit") or r["unit"]) != r["unit"]:
            pk = None
        for hz in ("week", "month"):
            o = _ing_to_order(r["order_" + hz], r["unit"], pk)
            r[f"order_{hz}_units"] = o["units"]
            r[f"order_{hz}_actual"] = o["actual_base"]
            r[f"order_{hz}_units_disp"] = (
                f"{o['units']:,g} {o['order_unit']}"
                + (f" ({_ing_pretty(o['actual_base'], r['unit'])[2]})"
                   if o["pack_known"] and o["qty_per_order"] != 1 else ""))
            if o["price_per_order"]:
                r[f"value_{hz}"] = round(o["units"] * float(o["price_per_order"]), 2)
        r["order_week_disp"] = _ing_pretty(r["order_week"], r["unit"])[2]
        r["order_month_disp"] = _ing_pretty(r["order_month"], r["unit"])[2]
        r["per_day_disp"] = _ing_pretty(r["per_day"], r["unit"])[2]


def _ing_coverage(con, start: str, end: str, branch: Optional[str], group: Optional[str]):
    """How much of what was sold actually has a recipe behind it.

    Buffet TIER packages are excluded from the denominator — they are the cover
    charge, not a dish, so they can never have ingredients."""
    filters = ["b.name != 'ALL'", "d.day BETWEEN :start AND :end",
               f"s.sku_code NOT IN {_EASY_TIER_CODES_SQL}"]
    params: Dict = {"start": start, "end": end}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    row = con.execute(f"""
        SELECT SUM(d.qty) AS tot,
               SUM(CASE WHEN m.sale_sku IS NOT NULL THEN d.qty ELSE 0 END) AS cov
          FROM v_sales_sku_daily d
          JOIN branches b ON b.branch_id = d.branch_id
          JOIN skus s     ON s.sku_id    = d.sku_id
          LEFT JOIN {_ING_BOM_MAP} m ON m.sale_sku = d.sku_id
         WHERE {' AND '.join(filters)}
    """, params).fetchone()
    tot = float(row["tot"] or 0)
    cov = float(row["cov"] or 0)
    missing = con.execute(f"""
        SELECT s.sku_code AS code, s.description AS name, ROUND(SUM(d.qty)) AS qty
          FROM v_sales_sku_daily d
          JOIN branches b ON b.branch_id = d.branch_id
          JOIN skus s     ON s.sku_id    = d.sku_id
          LEFT JOIN {_ING_BOM_MAP} m ON m.sale_sku = d.sku_id
         WHERE {' AND '.join(filters)} AND m.sale_sku IS NULL
         GROUP BY s.sku_id ORDER BY qty DESC LIMIT 8
    """, params).fetchall()
    return {
        "pct": round(100.0 * cov / tot, 1) if tot else 0.0,
        "qty_total": round(tot),
        "qty_covered": round(cov),
        "top_missing": [{"code": r["code"], "name": r["name"] or "", "qty": int(r["qty"] or 0)}
                        for r in missing],
    }


@router.get("/api/ingredients/order-plan")
@ttl_cache
def ingredients_order_plan(
    branch:       Optional[str] = Query(None),
    group:        Optional[str] = Query(None),
    days:         int           = Query(28, description="lookback window in days"),
    date_to:      Optional[str] = Query(None),
    basis:        str           = Query("avg", description="avg = whole window · recent = last 7 days"),
    buffer_pct:   float         = Query(10.0, description="safety buffer added on top of forecast demand"),
    type:         Optional[str] = Query(None, description="ingredient_type filter"),
    q:            Optional[str] = Query(None),
    group_family: bool          = Query(False),
    include:      str           = Query("all", description="all | central (แนะนำสั่ง) | local (สาขาซื้อเอง)"),
    season:       str           = Query("off", description="off | auto — scale for festivals from last year"),
    cycle_start:  Optional[str] = Query(None, description="first day of the cycle being ordered for"),
    limit:        int           = Query(400),
):
    """Per-branch ingredient demand → how much to order per week / per month.

    Derived from real sales × BoM. Excludes on-hand stock by design (see the
    module note above), so the answer is "expected consumption", and the caller
    subtracts what is already in the fridge."""
    buffer_pct = max(0.0, min(float(buffer_pct or 0), 100.0))
    basis = "recent" if str(basis).lower() == "recent" else "avg"
    with get_db() as con:
        start, end, days = _ing_window(con, days, date_to)
        if not start:
            return {"available": False, "reason": "no daily sales data"}
        include = include if include in ("all", "central", "local") else "all"
        rows = _ing_plan_rows(con, start, end, days, branch, group, basis, buffer_pct,
                              type, q, group_family, per_branch=False, include=include)
        n_local = len(_ing_plan_rows(con, start, end, days, branch, group, basis,
                                     buffer_pct, type, q, group_family,
                                     per_branch=False, include="local"))
        cov = _ing_coverage(con, start, end, branch, group)
        # festival / promotion scaling — applied last so the base numbers above
        # stay the honest measured demand and the uplift is auditable
        c_start = cycle_start or con.execute("SELECT date('now','localtime')").fetchone()[0]
        c_end = con.execute("SELECT date(:s,'+6 day')", {"s": c_start}).fetchone()[0]
        season_info = _ing_season_resolve(con, branch, c_start, c_end,
                                          "auto" if season == "auto" else "off")
        sf = float(season_info.get("factor") or 1.0)
        if abs(sf - 1.0) > 1e-6:
            for r in rows:
                for k in ("per_day", "per_week", "per_month", "order_week", "order_month",
                          "value_week", "value_month"):
                    r[k] = round(r[k] * sf, 4)
                r["season_factor"] = sf
            _ing_reapply_order_units(con, rows)
        by_branch = []
        if not branch:
            per_b = _ing_plan_rows(con, start, end, days, None, group, basis, buffer_pct,
                                   type, q, False, per_branch=True, include=include)
            agg: Dict[str, dict] = {}
            for r in per_b:
                a = agg.setdefault(r["branch"], {"branch": r["branch"], "ingredients": 0,
                                                 "value_week": 0.0, "value_month": 0.0})
                a["ingredients"] += 1
                a["value_week"] += r["value_week"]
                a["value_month"] += r["value_month"]
            by_branch = sorted(agg.values(), key=lambda x: x["value_month"], reverse=True)
            for a in by_branch:
                a["value_week"] = round(a["value_week"], 2)
                a["value_month"] = round(a["value_month"], 2)

    totals = {
        "ingredients": len(rows),
        "value_week": round(sum(r["value_week"] for r in rows), 2),
        "value_month": round(sum(r["value_month"] for r in rows), 2),
    }
    return {
        "available": True,
        "meta": {"date_from": start, "date_to": end, "days": days, "branch": branch or "",
                 "group": group or "", "basis": basis, "buffer_pct": buffer_pct,
                 "include": include, "local_count": n_local, "season": season_info,
                 "trading_days": _ing_trading_days(con, start, end, branch) if branch else days,
                 "coverage": cov, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M")},
        "totals": totals,
        "rows": rows[:max(1, min(int(limit or 400), 2000))],
        "branches": by_branch,
    }


@router.get("/api/ingredients/order-plan.xlsx")
def ingredients_order_plan_xlsx(
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    days:       int           = Query(28),
    date_to:    Optional[str] = Query(None),
    basis:      str           = Query("avg"),
    buffer_pct: float         = Query(10.0),
):
    """Excel export of the order plan — one row per branch × ingredient."""
    import io as _io_ing
    from fastapi.responses import StreamingResponse as _SRing
    try:
        from openpyxl import Workbook as _WB
        from openpyxl.styles import Font as _F, PatternFill as _PF, Alignment as _AL
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"openpyxl unavailable: {e}")
    buffer_pct = max(0.0, min(float(buffer_pct or 0), 100.0))
    basis = "recent" if str(basis).lower() == "recent" else "avg"
    with get_db() as con:
        start, end, days = _ing_window(con, days, date_to)
        if not start:
            raise HTTPException(status_code=404, detail="ไม่มีข้อมูลยอดขายรายวัน")
        rows = _ing_plan_rows(con, start, end, days, branch, group, basis, buffer_pct,
                              None, None, False, per_branch=not branch)
    wb = _WB()
    ws = wb.active
    ws.title = "แผนสั่งวัตถุดิบ"
    ws.append([f"แผนสั่งวัตถุดิบ · ข้อมูลขายจริง {start} ถึง {end} ({days} วัน) · "
               f"เผื่อ {buffer_pct:g}% · ฐาน {'7 วันล่าสุด' if basis == 'recent' else 'เฉลี่ยทั้งช่วง'}"])
    ws["A1"].font = _F(bold=True, size=12)
    ws.append([])
    head = (["สาขา"] if not branch else []) + [
        "รหัส", "วัตถุดิบ", "ประเภท", "หน่วยนับ", "ใช้จริงในช่วง", "เฉลี่ย/วัน",
        "ใช้/สัปดาห์", "ใช้/เดือน", "หน่วยสั่ง", "ขนาดบรรจุ",
        "สั่ง/สัปดาห์ (หน่วยสั่ง)", "สั่ง/เดือน (หน่วยสั่ง)",
        "ต้นทุน/หน่วย", "มูลค่า/สัปดาห์", "มูลค่า/เดือน",
        "จำนวนเมนูที่ใช้", "เทรนด์ 7 วัน %", "ตั้งขนาดบรรจุแล้ว", "การจัดหา"]
    ws.append(head)
    for c in ws[3]:
        c.font = _F(bold=True, color="FFFFFF")
        c.fill = _PF("solid", fgColor="0F766E")
        c.alignment = _AL(horizontal="center", wrap_text=True)
    for r in rows:
        ws.append(([r.get("branch", "")] if not branch else []) + [
            r["code"], r["name"], r["type"], r["unit"], r["qty_used"], r["per_day"],
            r["order_week"], r["order_month"], r.get("order_unit", ""),
            r.get("qty_per_order", ""), r.get("order_week_units", ""),
            r.get("order_month_units", ""), r["unit_cost"], r["value_week"],
            r["value_month"], r["n_menus"], r["trend_pct"],
            "ใช่" if r.get("pack_known") else "ยังไม่ตั้ง",
            "สาขาซื้อเอง" if r.get("sourcing") == "local" else "สั่งส่วนกลาง"])
    widths = ([22] if not branch else []) + [12, 34, 10, 9, 13, 11, 12, 12, 12,
                                             11, 15, 15, 12, 13, 13, 9, 12, 13, 13]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=3, column=i).column_letter].width = w
    ws.freeze_panes = "A4"
    buf = _io_ing.BytesIO()
    wb.save(buf)
    buf.seek(0)
    # HTTP headers are latin-1: a Thai branch name in filename= raises
    # UnicodeEncodeError and 500s the export. RFC 5987 carries the real name.
    from urllib.parse import quote as _q
    tag = (branch or group or "all").replace(" ", "_")
    ascii_tag = tag.encode("ascii", "ignore").decode() or "all"
    return _SRing(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                  headers={"Content-Disposition":
                           f"attachment; filename=ORDER_PLAN_{ascii_tag}_{end}.xlsx; "
                           f"filename*=UTF-8''{_q(f'ORDER_PLAN_{tag}_{end}.xlsx')}"})


@router.get("/api/ingredients/order-letter.print")
def ingredients_order_letter(
    branch:     Optional[str] = Query(None, description="one branch; omit for every branch (one page each)"),
    group:      Optional[str] = Query(None),
    days:       int           = Query(28),
    date_to:    Optional[str] = Query(None),
    basis:      str           = Query("avg"),
    buffer_pct: float         = Query(10.0),
    horizon:    str           = Query("week", description="week | month"),
    top:        int           = Query(40, description="ingredients listed per branch"),
    issuer:     Optional[str] = Query(None, description="department line on the signature block"),
    note:       Optional[str] = Query(None, description="extra paragraph before the table"),
):
    """Printable Thai notice to each branch: how much of each ingredient to order.

    One A4 page per branch. Deliberately states that on-hand stock is NOT
    deducted and leaves the signature blank — this is a template a human signs,
    not an auto-authorised purchase order."""
    import html as _html_ing
    horizon = "month" if str(horizon).lower() == "month" else "week"
    buffer_pct = max(0.0, min(float(buffer_pct or 0), 100.0))
    basis = "recent" if str(basis).lower() == "recent" else "avg"
    top = max(5, min(int(top or 40), 200))
    esc = lambda s: _html_ing.escape(str(s or ""))

    with get_db() as con:
        start, end, days = _ing_window(con, days, date_to)
        if not start:
            raise HTTPException(status_code=404, detail="ไม่มีข้อมูลยอดขายรายวัน")
        if branch:
            names = [branch]
        else:
            bf = ["name != 'ALL'", "COALESCE(active,1)=1"]
            bp: Dict = {}
            if group:
                bf.append("COALESCE(branch_type,'master') = :branch_group")
                bp["branch_group"] = group
            names = [r["name"] for r in con.execute(
                f"SELECT name FROM branches WHERE {' AND '.join(bf)} ORDER BY name", bp)]
        pages = []
        for nm in names:
            allrows = _ing_plan_rows(con, start, end, days, nm, None, basis, buffer_pct,
                                     None, None, False, per_branch=False)
            rows = [r for r in allrows if r.get("sourcing") != "local"]
            local = [r for r in allrows if r.get("sourcing") == "local"]
            if rows:
                pages.append((nm, rows[:top], _ing_coverage(con, start, end, nm, None), local))

    if not pages:
        raise HTTPException(status_code=404, detail="ไม่พบข้อมูลการใช้วัตถุดิบของสาขาที่เลือก")

    hz_label = "สัปดาห์" if horizon == "week" else "เดือน"
    qty_key = "order_week_units_disp" if horizon == "week" else "order_month_units_disp"
    val_key = "value_week" if horizon == "week" else "value_month"
    today = datetime.now().strftime("%d/%m/%Y")
    issuer_line = esc(issuer or "ฝ่ายวิเคราะห์ต้นทุนและจัดซื้อ")
    basis_label = "ค่าเฉลี่ย 7 วันล่าสุด" if basis == "recent" else f"ค่าเฉลี่ยตลอด {days} วัน"

    body = []
    for nm, rows, cov, local in pages:
        total_val = sum(r[val_key] for r in rows)
        trs = []
        for i, r in enumerate(rows, start=1):
            tr = r.get("trend_pct")
            trend_html = ("<span style='color:#b91c1c'>▲ %.0f%%</span>" % tr if tr and tr >= 10 else
                          "<span style='color:#047857'>▼ %.0f%%</span>" % abs(tr) if tr and tr <= -10 else
                          "<span style='color:#94a3b8'>–</span>")
            trs.append(
                f"<tr><td class='c'>{i}</td><td class='mono'>{esc(r['code'])}</td>"
                f"<td>{esc(r['name'])}</td><td class='r'>{esc(r['per_day_disp'])}</td>"
                f"<td class='r b'>{esc(r[qty_key])}</td>"
                f"<td class='r'>{r[val_key]:,.0f}</td><td class='c'>{trend_html}</td></tr>")
        body.append(f"""
<section class="page">
  <div class="head">
    <div class="brand">โอชิเน (Oshinei)</div>
    <div class="doc">ใบแจ้งปริมาณสั่งวัตถุดิบที่แนะนำ<br><span>Recommended Ingredient Order</span></div>
  </div>
  <table class="meta"><tr>
    <td><b>เรียน</b> ผู้จัดการสาขา {esc(nm)}</td>
    <td><b>วันที่</b> {today}</td></tr><tr>
    <td><b>เรื่อง</b> ปริมาณวัตถุดิบที่ควรสั่งประจำ{hz_label}</td>
    <td><b>อ้างอิงยอดขายจริง</b> {start} ถึง {end} ({days} วัน)</td></tr>
  </table>
  <p class="lead">ตามที่{issuer_line}ได้วิเคราะห์ปริมาณการใช้วัตถุดิบจริงของสาขา{esc(nm)}
     จากยอดขายรายวันคูณกับสูตรมาตรฐาน (BoM) จึงขอแจ้งปริมาณที่ควรสั่งประจำ{hz_label}
     โดยคำนวณจาก{basis_label} และเผื่อความผันผวนไว้ {buffer_pct:g}% ดังรายการต่อไปนี้</p>
  {('<p class="lead">' + esc(note) + '</p>') if note else ''}
  <table class="grid">
    <thead><tr><th>ลำดับ</th><th>รหัส</th><th>วัตถุดิบ</th><th>ใช้เฉลี่ย/วัน</th>
      <th>ควรสั่ง/{hz_label}</th><th>มูลค่าโดยประมาณ (บาท)</th><th>แนวโน้ม</th></tr></thead>
    <tbody>{''.join(trs)}</tbody>
    <tfoot><tr><td colspan="5" class="r b">รวมมูลค่าโดยประมาณ</td>
      <td class="r b">{total_val:,.0f}</td><td></td></tr></tfoot>
  </table>
  {('<p class="excl"><b>รายการที่สาขาจัดหาเอง (ไม่รวมในใบสั่งนี้):</b> '
     + esc(' · '.join(x['name'] for x in local[:14]))
     + (f" และอื่น ๆ อีก {len(local) - 14} รายการ" if len(local) > 14 else '')
     + '</p>') if local else ''}
  <div class="notes">
    <b>หมายเหตุสำคัญ</b>
    <ol>
      <li><b>ตัวเลขนี้คือปริมาณที่คาดว่าจะ “ใช้” ยังไม่ได้หักสต๊อกคงเหลือในร้าน</b>
          กรุณาตรวจนับของคงเหลือก่อนออกใบสั่งซื้อทุกครั้ง</li>
      <li>คำนวณจากสูตร BoM ที่ครอบคลุม {cov['pct']:g}% ของจำนวนจานที่ขายในช่วงอ้างอิง
          (เมนูที่ยังไม่มีสูตรจะไม่ปรากฏในรายการนี้)</li>
      <li>ปริมาณข้างต้นเป็นการประมาณการจากยอดขายที่ผ่านมา หากมีเทศกาล โปรโมชัน
          หรือการปิดปรับปรุง กรุณาปรับเพิ่ม–ลดตามความเหมาะสม</li>
      <li>ช่อง “ควรสั่ง” ปัดขึ้นเป็นจำนวนเต็มหน่วยสั่งแล้ว (ในวงเล็บคือปริมาณจริงที่จะได้รับ)
          รายการที่ยังไม่ได้ตั้งขนาดบรรจุจะแสดงเป็นหน่วยมาตรฐาน (กก./ลิตร)</li>
      <li>คอลัมน์ “แนวโน้ม” เทียบการใช้ 7 วันล่าสุดกับ 7 วันก่อนหน้า
          (▲ = ใช้เพิ่มขึ้น ควรเผื่อเพิ่ม · ▼ = ใช้ลดลง ควรลดปริมาณสั่ง)</li>
    </ol>
  </div>
  <div class="sign">
    <div>
      <div class="line"></div>
      <div class="cap">{issuer_line}</div>
      <div class="cap">ผู้จัดทำ · วันที่ ......../......../........</div>
    </div>
    <div>
      <div class="line"></div>
      <div class="cap">ผู้จัดการสาขา {esc(nm)}</div>
      <div class="cap">ผู้รับทราบ · วันที่ ......../......../........</div>
    </div>
  </div>
</section>""")

    html_out = f"""<!doctype html><html lang="th"><head><meta charset="utf-8">
<title>ใบแจ้งปริมาณสั่งวัตถุดิบ · {esc(pages[0][0] if branch else 'ทุกสาขา')} · {end}</title>
<style>
  @page {{ size: A4; margin: 14mm 12mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family:"Sarabun","Leelawadee UI","Tahoma",sans-serif; color:#0f172a;
         margin:0; background:#f1f5f9; font-size:12px; }}
  .page {{ background:#fff; width:190mm; min-height:263mm; margin:8mm auto; padding:10mm 9mm;
           box-shadow:0 1px 6px rgba(0,0,0,.12); page-break-after:always; }}
  .page:last-child {{ page-break-after:auto; }}
  .head {{ display:flex; justify-content:space-between; align-items:flex-end;
           border-bottom:2.5px solid #0f766e; padding-bottom:6px; margin-bottom:10px; }}
  .brand {{ font-size:20px; font-weight:700; color:#0f766e; letter-spacing:.5px; }}
  .doc {{ text-align:right; font-size:14px; font-weight:700; line-height:1.3; }}
  .doc span {{ font-size:10px; font-weight:400; color:#64748b; }}
  table.meta {{ width:100%; margin-bottom:10px; font-size:11.5px; }}
  table.meta td {{ padding:2px 0; }}
  table.meta td:last-child {{ text-align:right; color:#334155; }}
  .lead {{ text-indent:2em; line-height:1.75; margin:0 0 10px; }}
  table.grid {{ width:100%; border-collapse:collapse; font-size:11px; }}
  table.grid th {{ background:#0f766e; color:#fff; font-weight:600; padding:5px 4px;
                   border:1px solid #0f766e; }}
  table.grid td {{ border:1px solid #cbd5e1; padding:3.5px 5px; }}
  table.grid tbody tr:nth-child(even) {{ background:#f8fafc; }}
  table.grid tfoot td {{ background:#ecfdf5; border-top:2px solid #0f766e; }}
  .r {{ text-align:right; }} .c {{ text-align:center; }} .b {{ font-weight:700; }}
  .mono {{ font-family:"Consolas",monospace; font-size:10px; color:#475569; }}
  .excl {{ margin-top:8px; font-size:10.5px; color:#475569; background:#f1f5f9;
           border-left:3px solid #94a3b8; padding:6px 9px; line-height:1.6; }}
  .notes {{ margin-top:10px; font-size:10.5px; background:#fffbeb; border-left:3px solid #f59e0b;
            padding:7px 10px; line-height:1.6; }}
  .notes ol {{ margin:4px 0 0 16px; padding:0; }}
  .sign {{ display:flex; justify-content:space-between; gap:24px; margin-top:24px; }}
  .sign > div {{ width:46%; text-align:center; }}
  .line {{ border-bottom:1px dotted #475569; height:34px; }}
  .cap {{ font-size:10.5px; color:#475569; margin-top:3px; }}
  .toolbar {{ text-align:center; padding:10px; }}
  .toolbar button {{ background:#0f766e; color:#fff; border:0; border-radius:6px;
                     padding:8px 18px; font-size:13px; cursor:pointer; font-family:inherit; }}
  @media print {{ body {{ background:#fff; }} .toolbar {{ display:none; }}
                  .page {{ margin:0; box-shadow:none; width:auto; min-height:0; padding:0; }}
                  table.grid th {{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }} }}
</style></head><body>
<div class="toolbar"><button onclick="window.print()">🖨️ พิมพ์ / บันทึกเป็น PDF</button>
  &nbsp;<span style="font-size:11px;color:#64748b">{len(pages)} สาขา · 1 หน้าต่อสาขา</span></div>
{''.join(body)}
</body></html>"""
    return Response(content=html_out, media_type="text/html; charset=utf-8")


class OrderUnitRow(BaseModel):
    ingredient_code: str
    base_unit: str
    order_unit: str
    qty_per_order: float
    price_per_order: Optional[float] = None
    moq: Optional[float] = 1
    round_step: Optional[float] = 1
    note: Optional[str] = None


@router.get("/api/ingredients/order-units")
def ingredients_order_units(q: Optional[str] = Query(None)):
    """The consumption-unit → ordering-unit map (pack sizes), plus every
    ingredient still on the fallback so ops can see what needs setting up."""
    with get_db() as con:
        packs = _ing_order_units(con)
        used = con.execute("""
            SELECT ingredient_code AS code, MAX(ingredient_name) AS name, unit,
                   COUNT(*) AS n_lines
              FROM bom_ingredients
             WHERE ingredient_code <> ''
             GROUP BY ingredient_code, unit
        """).fetchall()
    rows = []
    for u in used:
        p = packs.get(u["code"])
        if p and (p.get("base_unit") or u["unit"]) != u["unit"]:
            p = None
        fb = _ING_UNIT_FALLBACK.get(u["unit"] or "", (u["unit"] or "หน่วย", 1.0))
        rows.append({
            "code": u["code"], "name": u["name"] or "", "base_unit": u["unit"] or "",
            "n_lines": u["n_lines"],
            "order_unit": (p or {}).get("order_unit", fb[0]),
            "qty_per_order": (p or {}).get("qty_per_order", fb[1]),
            "price_per_order": (p or {}).get("price_per_order"),
            "moq": (p or {}).get("moq", 1), "round_step": (p or {}).get("round_step", 1),
            "note": (p or {}).get("note", ""), "source": (p or {}).get("source", "fallback"),
            "pack_known": bool(p),
        })
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in r["code"].lower() or ql in r["name"].lower()]
    rows.sort(key=lambda r: (r["pack_known"], -r["n_lines"]))
    return {"rows": rows,
            "summary": {"total": len(rows),
                        "with_pack": sum(1 for r in rows if r["pack_known"]),
                        "fallback": sum(1 for r in rows if not r["pack_known"])}}


@router.post("/api/ingredients/order-units", dependencies=[Depends(require_write_token)])
def ingredients_order_units_save(rows: List[OrderUnitRow]):
    """Upsert pack sizes. qty_per_order must be > 0 — a zero would make the
    ceil() divide by zero and silently fall back to 1 base unit per pack."""
    bad = [r.ingredient_code for r in rows if not r.ingredient_code or r.qty_per_order <= 0]
    if bad:
        raise HTTPException(400, f"qty_per_order ต้องมากกว่า 0: {', '.join(bad[:5])}")
    with get_db() as con:
        con.execute(_ING_ORDER_DDL)
        for r in rows:
            con.execute("""
                INSERT INTO ingredient_order_unit
                    (ingredient_code, base_unit, order_unit, qty_per_order,
                     price_per_order, moq, round_step, note, source, updated_at)
                VALUES (?,?,?,?,?,?,?,?, 'manual', CURRENT_TIMESTAMP)
                ON CONFLICT(ingredient_code) DO UPDATE SET
                    base_unit=excluded.base_unit, order_unit=excluded.order_unit,
                    qty_per_order=excluded.qty_per_order,
                    price_per_order=excluded.price_per_order,
                    moq=excluded.moq, round_step=excluded.round_step,
                    note=excluded.note, source='manual',
                    updated_at=CURRENT_TIMESTAMP
            """, (r.ingredient_code.strip(), r.base_unit.strip(), r.order_unit.strip(),
                  float(r.qty_per_order), r.price_per_order,
                  float(r.moq or 1), float(r.round_step or 1), r.note))
        con.commit()
    invalidate_cache()
    return {"ok": True, "saved": len(rows)}


class SourcingRow(BaseModel):
    ingredient_code: str
    branch: Optional[str] = ""      # "" = ค่าเริ่มต้นทุกสาขา
    mode: str = "central"           # central = แนะนำสั่ง · local = สาขาซื้อเอง
    note: Optional[str] = None


@router.get("/api/ingredients/sourcing")
def ingredients_sourcing(branch: Optional[str] = Query(None)):
    """Which ingredients the plan recommends ordering, and which are left to the
    branch. Anything with no row is 'central' (recommend) by default."""
    with get_db() as con:
        con.execute(_ING_SOURCING_DDL)
        rows = [dict(r) for r in con.execute(
            "SELECT ingredient_code, branch, mode, note, updated_at "
            "FROM ingredient_sourcing ORDER BY branch, ingredient_code")]
    if branch is not None:
        rows = [r for r in rows if r["branch"] in ("", branch)]
    return {"rows": rows,
            "summary": {"local_default": sum(1 for r in rows
                                             if not r["branch"] and r["mode"] == "local"),
                        "branch_overrides": sum(1 for r in rows if r["branch"])}}


@router.post("/api/ingredients/sourcing", dependencies=[Depends(require_write_token)])
def ingredients_sourcing_save(rows: List[SourcingRow]):
    """Set 'สั่งส่วนกลาง' / 'สาขาซื้อเอง' per ingredient (branch='' = ทุกสาขา).

    Setting a branch row back to the default is done by posting mode='default',
    which DELETES the override rather than pinning it — otherwise a later change
    to the chain-wide default would silently not reach that branch."""
    with get_db() as con:
        con.execute(_ING_SOURCING_DDL)
        for r in rows:
            code = (r.ingredient_code or "").strip()
            if not code:
                continue
            br = (r.branch or "").strip()
            mode = (r.mode or "central").lower()
            if mode == "default":
                con.execute("DELETE FROM ingredient_sourcing WHERE ingredient_code=? AND branch=?",
                            (code, br))
                continue
            if mode not in ("central", "local"):
                raise HTTPException(400, f"mode ต้องเป็น central | local | default: {mode}")
            con.execute("""
                INSERT INTO ingredient_sourcing (ingredient_code, branch, mode, note, updated_at)
                VALUES (?,?,?,?, CURRENT_TIMESTAMP)
                ON CONFLICT(ingredient_code, branch) DO UPDATE SET
                    mode=excluded.mode, note=excluded.note, updated_at=CURRENT_TIMESTAMP
            """, (code, br, mode, r.note))
        con.commit()
    invalidate_cache()
    return {"ok": True, "saved": len(rows)}


_ING_ISSUE_DDL = [
    """CREATE TABLE IF NOT EXISTS ingredient_order_issue (
        issue_id     INTEGER PRIMARY KEY,
        branch       TEXT NOT NULL,
        horizon      TEXT NOT NULL DEFAULT 'week',
        period_start TEXT NOT NULL,
        period_end   TEXT NOT NULL,
        basis_from   TEXT, basis_to TEXT, basis TEXT,
        buffer_pct   REAL,
        issued_at    TEXT DEFAULT CURRENT_TIMESTAMP,
        issued_by    TEXT, note TEXT,
        UNIQUE(branch, period_start, period_end, horizon))""",
    """CREATE TABLE IF NOT EXISTS ingredient_order_issue_line (
        issue_id        INTEGER NOT NULL,
        ingredient_code TEXT NOT NULL,
        name TEXT, base_unit TEXT, order_unit TEXT,
        qty_per_order REAL, rec_base REAL, rec_units REAL, unit_cost REAL,
        PRIMARY KEY (issue_id, ingredient_code))""",
    """CREATE TABLE IF NOT EXISTS ingredient_order_actual (
        issue_id        INTEGER NOT NULL,
        ingredient_code TEXT NOT NULL,
        qty_units  REAL NOT NULL DEFAULT 0,
        qty_base   REAL,
        source TEXT, note TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (issue_id, ingredient_code))""",
]


# Ordering below this share of the recommendation counts as under-ordering.
_ING_TOLERANCE_DEFAULT = 20.0


def _ing_issue_tables(con):
    for ddl in _ING_ISSUE_DDL:
        con.execute(ddl)


def _ing_cycle_dates(con, horizon: str, period_start: Optional[str]):
    """The order cycle a snapshot covers — defaults to the next 7 / 30 days."""
    if period_start:
        start = period_start
    else:
        start = con.execute("SELECT date('now','localtime')").fetchone()[0]
    span = "+6 day" if horizon == "week" else "+29 day"
    end = con.execute("SELECT date(:s, :sp)", {"s": start, "sp": span}).fetchone()[0]
    return start, end


class IssueBody(BaseModel):
    branch: Optional[str] = None      # omit = every active branch
    group: Optional[str] = None
    horizon: str = "week"
    period_start: Optional[str] = None
    days: int = 28
    date_to: Optional[str] = None
    basis: str = "avg"
    buffer_pct: float = 10.0
    top: int = 40
    issued_by: Optional[str] = None
    note: Optional[str] = None


@router.post("/api/ingredients/order-plan/issue", dependencies=[Depends(require_write_token)])
def ingredients_order_issue(body: IssueBody):
    """Freeze today's recommendation as an order cycle, one snapshot per branch.

    Re-issuing the same branch+period REPLACES the snapshot (and its recorded
    actuals) — a cycle is corrected, never silently duplicated."""
    horizon = "month" if str(body.horizon).lower() == "month" else "week"
    buffer_pct = max(0.0, min(float(body.buffer_pct or 0), 100.0))
    basis = "recent" if str(body.basis).lower() == "recent" else "avg"
    with get_db() as con:
        _ing_issue_tables(con)
        start_w, end_w, days = _ing_window(con, body.days, body.date_to)
        if not start_w:
            raise HTTPException(404, "ไม่มีข้อมูลยอดขายรายวัน")
        p_start, p_end = _ing_cycle_dates(con, horizon, body.period_start)
        if body.branch:
            names = [body.branch]
        else:
            bf, bp = ["name != 'ALL'", "COALESCE(active,1)=1"], {}
            if body.group:
                bf.append("COALESCE(branch_type,'master') = :g")
                bp["g"] = body.group
            names = [r["name"] for r in con.execute(
                f"SELECT name FROM branches WHERE {' AND '.join(bf)} ORDER BY name", bp)]
        issued = []
        for nm in names:
            rows = _ing_plan_rows(con, start_w, end_w, days, nm, None, basis, buffer_pct,
                                  None, None, False, per_branch=False, include="central")
            rows = rows[:max(5, min(int(body.top or 40), 300))]
            if not rows:
                continue
            con.execute("""INSERT INTO ingredient_order_issue
                    (branch, horizon, period_start, period_end, basis_from, basis_to,
                     basis, buffer_pct, issued_by, note)
                 VALUES (?,?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(branch, period_start, period_end, horizon) DO UPDATE SET
                     basis_from=excluded.basis_from, basis_to=excluded.basis_to,
                     basis=excluded.basis, buffer_pct=excluded.buffer_pct,
                     issued_at=CURRENT_TIMESTAMP, issued_by=excluded.issued_by,
                     note=excluded.note""",
                        (nm, horizon, p_start, p_end, start_w, end_w, basis,
                         buffer_pct, body.issued_by, body.note))
            iid = con.execute("""SELECT issue_id FROM ingredient_order_issue
                                  WHERE branch=? AND period_start=? AND period_end=? AND horizon=?""",
                              (nm, p_start, p_end, horizon)).fetchone()["issue_id"]
            con.execute("DELETE FROM ingredient_order_issue_line WHERE issue_id=?", (iid,))
            con.execute("DELETE FROM ingredient_order_actual WHERE issue_id=?", (iid,))
            key_units = "order_%s_units" % horizon
            key_base = "order_%s" % horizon
            con.executemany("""INSERT INTO ingredient_order_issue_line
                    (issue_id, ingredient_code, name, base_unit, order_unit,
                     qty_per_order, rec_base, rec_units, unit_cost)
                 VALUES (?,?,?,?,?,?,?,?,?)""",
                            [(iid, r["code"] or r["name"], r["name"], r["unit"],
                              r.get("order_unit") or r["unit"], r.get("qty_per_order") or 1,
                              r[key_base], r.get(key_units) or 0, r["unit_cost"])
                             for r in rows])
            issued.append({"issue_id": iid, "branch": nm, "lines": len(rows)})
        con.commit()
    invalidate_cache()
    return {"ok": True, "horizon": horizon, "period_start": p_start, "period_end": p_end,
            "issued": issued}


@router.get("/api/ingredients/order-cycles")
def ingredients_order_cycles(branch: Optional[str] = Query(None),
                             limit: int = Query(60)):
    """Issued cycles with their compliance counts, newest first."""
    with get_db() as con:
        _ing_issue_tables(con)
        f, p = [], {"lim": max(1, min(int(limit or 60), 500))}
        if branch:
            f.append("i.branch = :branch")
            p["branch"] = branch
        where = ("WHERE " + " AND ".join(f)) if f else ""
        rows = con.execute(f"""
            SELECT i.*,
                   (SELECT COUNT(*) FROM ingredient_order_issue_line l
                     WHERE l.issue_id = i.issue_id) AS lines,
                   (SELECT COUNT(*) FROM ingredient_order_actual a
                     WHERE a.issue_id = i.issue_id AND a.qty_units > 0) AS recorded
              FROM ingredient_order_issue i {where}
             ORDER BY i.period_start DESC, i.branch LIMIT :lim""", p).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["compliance"] = _ing_compliance_summary(con, r["issue_id"])
            out.append(d)
    return {"rows": out}


def _ing_compliance_rows(con, issue_id: int, tolerance_pct: float):
    """Frozen recommendation vs what was actually ordered, per ingredient."""
    tol = max(0.0, min(float(tolerance_pct), 100.0))
    floor = 1.0 - tol / 100.0
    rows = []
    for r in con.execute("""
        SELECT l.*, COALESCE(a.qty_units, 0) AS act_units, a.qty_base AS act_base,
               a.note AS act_note, a.updated_at AS act_at
          FROM ingredient_order_issue_line l
          LEFT JOIN ingredient_order_actual a
                 ON a.issue_id = l.issue_id AND a.ingredient_code = l.ingredient_code
         WHERE l.issue_id = ?
         ORDER BY l.rec_base * COALESCE(l.unit_cost,0) DESC""", (issue_id,)):
        rec_base = float(r["rec_base"] or 0)
        per = float(r["qty_per_order"] or 1) or 1.0
        act_units = float(r["act_units"] or 0)
        act_base = float(r["act_base"]) if r["act_base"] is not None else act_units * per
        if rec_base <= 0:
            continue
        ratio = act_base / rec_base
        if act_units <= 0:
            status = "not_ordered"
        elif ratio < floor:
            status = "under"
        else:
            status = "ok"
        rows.append({
            "ingredient_code": r["ingredient_code"], "name": r["name"] or "",
            "base_unit": r["base_unit"] or "", "order_unit": r["order_unit"] or "",
            "qty_per_order": per,
            "rec_units": float(r["rec_units"] or 0), "rec_base": round(rec_base, 2),
            "rec_disp": _ing_pretty(rec_base, r["base_unit"])[2],
            "act_units": act_units, "act_base": round(act_base, 2),
            "act_disp": _ing_pretty(act_base, r["base_unit"])[2],
            "pct_of_rec": round(100.0 * ratio, 1),
            "gap_base": round(rec_base - act_base, 2),
            "gap_disp": _ing_pretty(max(0.0, rec_base - act_base), r["base_unit"])[2],
            "gap_value": round(max(0.0, rec_base - act_base) * float(r["unit_cost"] or 0), 2),
            "status": status, "note": r["act_note"] or "", "recorded_at": r["act_at"],
        })
    return rows


def _ing_compliance_summary(con, issue_id: int, tolerance_pct: float = _ING_TOLERANCE_DEFAULT):
    rows = _ing_compliance_rows(con, issue_id, tolerance_pct)
    n_not = sum(1 for r in rows if r["status"] == "not_ordered")
    n_under = sum(1 for r in rows if r["status"] == "under")
    return {"lines": len(rows), "not_ordered": n_not, "under": n_under,
            "ok": len(rows) - n_not - n_under,
            "gap_value": round(sum(r["gap_value"] for r in rows
                                   if r["status"] != "ok"), 2),
            "compliant": (n_not + n_under) == 0}


@router.get("/api/ingredients/order-compliance")
def ingredients_order_compliance(issue_id: int = Query(...),
                                 tolerance_pct: float = Query(_ING_TOLERANCE_DEFAULT)):
    """What the branch was told to order vs what it actually ordered."""
    with get_db() as con:
        _ing_issue_tables(con)
        head = con.execute("SELECT * FROM ingredient_order_issue WHERE issue_id=?",
                           (issue_id,)).fetchone()
        if not head:
            raise HTTPException(404, "ไม่พบรอบสั่งนี้")
        rows = _ing_compliance_rows(con, issue_id, tolerance_pct)
        summary = _ing_compliance_summary(con, issue_id, tolerance_pct)
    return {"issue": dict(head), "tolerance_pct": tolerance_pct,
            "summary": summary, "rows": rows}


class ActualRow(BaseModel):
    ingredient_code: str
    qty_units: float = 0
    note: Optional[str] = None


class ActualBody(BaseModel):
    issue_id: int
    rows: List[ActualRow]
    source: Optional[str] = "manual"


@router.post("/api/ingredients/order-actual", dependencies=[Depends(require_write_token)])
def ingredients_order_actual(body: ActualBody):
    """Record what a branch actually ordered, in the ORDER units of that cycle.

    qty is converted to base units using the pack size FROZEN in the issue, so a
    later pack-size change never rewrites history."""
    with get_db() as con:
        _ing_issue_tables(con)
        if not con.execute("SELECT 1 FROM ingredient_order_issue WHERE issue_id=?",
                           (body.issue_id,)).fetchone():
            raise HTTPException(404, "ไม่พบรอบสั่งนี้")
        packs = {r["ingredient_code"]: float(r["qty_per_order"] or 1)
                 for r in con.execute(
                     "SELECT ingredient_code, qty_per_order FROM ingredient_order_issue_line "
                     "WHERE issue_id=?", (body.issue_id,))}
        saved = 0
        for r in body.rows:
            code = (r.ingredient_code or "").strip()
            if code not in packs:
                continue                      # not part of this cycle — ignore silently
            qty = max(0.0, float(r.qty_units or 0))
            con.execute("""INSERT INTO ingredient_order_actual
                    (issue_id, ingredient_code, qty_units, qty_base, source, note, updated_at)
                 VALUES (?,?,?,?,?,?, CURRENT_TIMESTAMP)
                 ON CONFLICT(issue_id, ingredient_code) DO UPDATE SET
                     qty_units=excluded.qty_units, qty_base=excluded.qty_base,
                     source=excluded.source, note=excluded.note,
                     updated_at=CURRENT_TIMESTAMP""",
                        (body.issue_id, code, qty, qty * packs[code], body.source, r.note))
            saved += 1
        con.commit()
    invalidate_cache()
    return {"ok": True, "saved": saved}


@router.get("/api/ingredients/compliance-letter.print")
def ingredients_compliance_letter(
    issue_id:      Optional[int] = Query(None, description="one cycle; omit to sweep a period"),
    period_start:  Optional[str] = Query(None),
    horizon:       str           = Query("week"),
    tolerance_pct: float         = Query(_ING_TOLERANCE_DEFAULT),
    due_date:      Optional[str] = Query(None, description="deadline to correct"),
    issuer:        Optional[str] = Query(None),
    note:          Optional[str] = Query(None),
):
    """Warning notice to branches that did not order centrally, or ordered more
    than `tolerance_pct` below the recommendation.

    Compliant branches get NO page — a notice is only produced where there is
    something concrete to answer for, and every line shows the frozen
    recommendation, the recorded actual and the shortfall it is based on."""
    import html as _html_c
    esc = lambda s: _html_c.escape(str(s or ""))
    horizon = "month" if str(horizon).lower() == "month" else "week"

    with get_db() as con:
        _ing_issue_tables(con)
        if issue_id:
            heads = con.execute("SELECT * FROM ingredient_order_issue WHERE issue_id=?",
                                (issue_id,)).fetchall()
        else:
            f = ["horizon = :hz"]
            p: Dict = {"hz": horizon}
            if period_start:
                f.append("period_start = :ps")
                p["ps"] = period_start
            else:
                last = con.execute("SELECT MAX(period_start) FROM ingredient_order_issue "
                                   "WHERE horizon=:hz", p).fetchone()[0]
                if not last:
                    raise HTTPException(404, "ยังไม่มีรอบสั่งที่ออกไว้")
                f.append("period_start = :ps")
                p["ps"] = last
            heads = con.execute(f"SELECT * FROM ingredient_order_issue "
                                f"WHERE {' AND '.join(f)} ORDER BY branch", p).fetchall()
        if not heads:
            raise HTTPException(404, "ไม่พบรอบสั่งตามเงื่อนไข")
        pages = []
        for h in heads:
            rows = _ing_compliance_rows(con, h["issue_id"], tolerance_pct)
            bad = [r for r in rows if r["status"] != "ok"]
            if bad:
                pages.append((dict(h), bad, _ing_compliance_summary(
                    con, h["issue_id"], tolerance_pct)))
        if not pages:
            return Response(content=
                "<!doctype html><meta charset='utf-8'>"
                "<div style=\"font-family:Sarabun,Tahoma,sans-serif;padding:40px;"
                "text-align:center;color:#047857\"><h2>✅ ไม่มีสาขาที่ต้องออกหนังสือแจ้งเตือน</h2>"
                "<p style='color:#475569'>ทุกสาขาสั่งวัตถุดิบผ่านส่วนกลางครบตามแผน "
                f"(เกณฑ์: ต่ำกว่าที่แนะนำเกิน {tolerance_pct:g}%)</p></div>",
                media_type="text/html; charset=utf-8")

    today = datetime.now().strftime("%d/%m/%Y")
    due = due_date or ""
    issuer_line = esc(issuer or "ฝ่ายวิเคราะห์ต้นทุนและจัดซื้อ")
    hz_label = "สัปดาห์" if horizon == "week" else "เดือน"

    body = []
    for h, bad, summ in pages:
        trs = []
        for i, r in enumerate(bad, start=1):
            if r["status"] == "not_ordered":
                st = "<span class='bad'>ไม่ได้สั่งผ่านส่วนกลาง</span>"
                pct = "0%"
            else:
                st = "<span class='warn'>ต่ำกว่าแผน</span>"
                pct = f"{r['pct_of_rec']:.0f}%"
            trs.append(
                f"<tr><td class='c'>{i}</td><td class='mono'>{esc(r['ingredient_code'])}</td>"
                f"<td>{esc(r['name'])}</td>"
                f"<td class='r'>{r['rec_units']:,g} {esc(r['order_unit'])}</td>"
                f"<td class='r'>{r['act_units']:,g} {esc(r['order_unit'])}</td>"
                f"<td class='c'>{pct}</td>"
                f"<td class='r'>{esc(r['gap_disp'])}</td>"
                f"<td class='r'>{r['gap_value']:,.0f}</td><td class='c'>{st}</td></tr>")
        body.append(f"""
<section class="page">
  <div class="head">
    <div class="brand">โอชิเน (Oshinei)</div>
    <div class="doc">หนังสือแจ้งเตือน<br><span>การสั่งวัตถุดิบไม่เป็นไปตามแผน</span></div>
  </div>
  <table class="meta"><tr>
    <td><b>เรียน</b> ผู้จัดการสาขา {esc(h['branch'])}</td>
    <td><b>วันที่</b> {today}</td></tr><tr>
    <td><b>เรื่อง</b> การสั่งวัตถุดิบผ่านส่วนกลางต่ำกว่าแผนที่แจ้งไว้</td>
    <td><b>รอบสั่ง</b> {h['period_start']} ถึง {h['period_end']} ({hz_label})</td></tr><tr>
    <td colspan="2"><b>อ้างถึง</b> ใบแจ้งปริมาณสั่งวัตถุดิบที่ออกเมื่อ {esc(str(h['issued_at'])[:16])}
        (อ้างอิงยอดขายจริง {h['basis_from']} ถึง {h['basis_to']})</td></tr>
  </table>
  <p class="lead">ตามที่{issuer_line}ได้แจ้งปริมาณวัตถุดิบที่ควรสั่งประจำ{hz_label}ให้สาขา{esc(h['branch'])}
     ทราบตามเอกสารอ้างถึงนั้น จากการตรวจสอบพบว่าสาขามีการสั่งวัตถุดิบผ่านส่วนกลาง
     <b>ไม่ครบตามแผนจำนวน {len(bad)} รายการ</b>
     (ไม่ได้สั่งเลย {summ['not_ordered']} รายการ · สั่งต่ำกว่าที่แนะนำเกิน {tolerance_pct:g}%
     จำนวน {summ['under']} รายการ) คิดเป็นมูลค่าส่วนที่ขาด {summ['gap_value']:,.0f} บาท
     รายละเอียดดังนี้</p>
  {('<p class="lead">' + esc(note) + '</p>') if note else ''}
  <table class="grid">
    <thead><tr><th>ลำดับ</th><th>รหัส</th><th>วัตถุดิบ</th><th>แผนแจ้งให้สั่ง</th>
      <th>สั่งจริง</th><th>% ของแผน</th><th>ส่วนที่ขาด</th><th>มูลค่าที่ขาด (บาท)</th><th>สถานะ</th></tr></thead>
    <tbody>{''.join(trs)}</tbody>
  </table>
  <div class="action">
    <b>ขอให้สาขาดำเนินการดังนี้</b>
    <ol>
      <li>ชี้แจงเหตุผลของการไม่สั่ง หรือสั่งต่ำกว่าแผนในแต่ละรายการข้างต้น</li>
      <li>หากเป็นการจัดซื้อจากผู้ขายภายนอก ให้แจ้งชื่อผู้ขาย ปริมาณ และราคาที่ซื้อ
          พร้อมแนบหลักฐานการซื้อเพื่อประกอบการพิจารณา</li>
      <li>ปรับการสั่งซื้อรอบถัดไปให้เป็นไปตามแผนที่ส่วนกลางแจ้ง
          หรือประสานเพื่อขอปรับแผนหากปริมาณที่แจ้งไม่สอดคล้องกับการใช้งานจริง</li>
    </ol>
    <p class="due"><b>กำหนดชี้แจง/แก้ไขภายในวันที่ {esc(due) if due else '......../......../........'}</b>
       — หากพ้นกำหนดข้างต้นแล้วยังไม่มีการแก้ไขหรือชี้แจง
       บริษัทจะดำเนินการตามขั้นตอนถัดไป</p>
  </div>
  <div class="notes">
    ปริมาณ “แผนแจ้งให้สั่ง” ในตารางนี้เป็นตัวเลขที่บันทึกไว้ ณ วันที่ออกใบแจ้ง
    ไม่ได้คำนวณใหม่ภายหลัง · เกณฑ์การแจ้งเตือน = สั่งต่ำกว่าแผนเกิน {tolerance_pct:g}%
    · หากสาขามีวัตถุดิบคงเหลือยกมาจากรอบก่อน กรุณาระบุในคำชี้แจงเพื่อประกอบการพิจารณา
  </div>
  <div class="sign">
    <div><div class="line"></div><div class="cap">{issuer_line}</div>
      <div class="cap">ผู้ออกหนังสือ · วันที่ ......../......../........</div></div>
    <div><div class="line"></div><div class="cap">ผู้จัดการสาขา {esc(h['branch'])}</div>
      <div class="cap">ผู้รับทราบ · วันที่ ......../......../........</div></div>
  </div>
</section>""")

    html_out = f"""<!doctype html><html lang="th"><head><meta charset="utf-8">
<title>หนังสือแจ้งเตือนการสั่งวัตถุดิบ · {len(pages)} สาขา</title>
<style>
  @page {{ size: A4; margin: 14mm 12mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family:"Sarabun","Leelawadee UI","Tahoma",sans-serif; color:#0f172a;
         margin:0; background:#f1f5f9; font-size:12px; }}
  .page {{ background:#fff; width:190mm; min-height:263mm; margin:8mm auto; padding:10mm 9mm;
           box-shadow:0 1px 6px rgba(0,0,0,.12); page-break-after:always; }}
  .page:last-child {{ page-break-after:auto; }}
  .head {{ display:flex; justify-content:space-between; align-items:flex-end;
           border-bottom:2.5px solid #b91c1c; padding-bottom:6px; margin-bottom:10px; }}
  .brand {{ font-size:20px; font-weight:700; color:#0f766e; letter-spacing:.5px; }}
  .doc {{ text-align:right; font-size:14px; font-weight:700; line-height:1.3; color:#b91c1c; }}
  .doc span {{ font-size:10px; font-weight:400; color:#64748b; }}
  table.meta {{ width:100%; margin-bottom:10px; font-size:11.5px; }}
  table.meta td {{ padding:2px 0; vertical-align:top; }}
  table.meta td:last-child {{ text-align:right; color:#334155; }}
  table.meta td[colspan] {{ text-align:left; }}
  .lead {{ text-indent:2em; line-height:1.75; margin:0 0 10px; }}
  table.grid {{ width:100%; border-collapse:collapse; font-size:10.5px; }}
  table.grid th {{ background:#b91c1c; color:#fff; font-weight:600; padding:5px 4px;
                   border:1px solid #b91c1c; }}
  table.grid td {{ border:1px solid #cbd5e1; padding:3.5px 5px; }}
  table.grid tbody tr:nth-child(even) {{ background:#fef2f2; }}
  .r {{ text-align:right; }} .c {{ text-align:center; }}
  .mono {{ font-family:"Consolas",monospace; font-size:10px; color:#475569; }}
  .bad {{ color:#b91c1c; font-weight:700; }}
  .warn {{ color:#b45309; font-weight:700; }}
  .action {{ margin-top:12px; font-size:11px; background:#fff7ed; border-left:3px solid #ea580c;
             padding:8px 11px; line-height:1.65; }}
  .action ol {{ margin:4px 0 6px 16px; padding:0; }}
  .due {{ margin:6px 0 0; color:#b91c1c; }}
  .notes {{ margin-top:9px; font-size:10px; color:#475569; background:#f1f5f9;
            border-left:3px solid #94a3b8; padding:6px 9px; line-height:1.6; }}
  .sign {{ display:flex; justify-content:space-between; gap:24px; margin-top:22px; }}
  .sign > div {{ width:46%; text-align:center; }}
  .line {{ border-bottom:1px dotted #475569; height:34px; }}
  .cap {{ font-size:10.5px; color:#475569; margin-top:3px; }}
  .toolbar {{ text-align:center; padding:10px; }}
  .toolbar button {{ background:#b91c1c; color:#fff; border:0; border-radius:6px;
                     padding:8px 18px; font-size:13px; cursor:pointer; font-family:inherit; }}
  @media print {{ body {{ background:#fff; }} .toolbar {{ display:none; }}
                  .page {{ margin:0; box-shadow:none; width:auto; min-height:0; padding:0; }}
                  table.grid th {{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }} }}
</style></head><body>
<div class="toolbar"><button onclick="window.print()">🖨️ พิมพ์ / บันทึกเป็น PDF</button>
  &nbsp;<span style="font-size:11px;color:#64748b">{len(pages)} สาขาที่ต้องแจ้งเตือน · 1 หน้าต่อสาขา</span></div>
{''.join(body)}
</body></html>"""
    return Response(content=html_out, media_type="text/html; charset=utf-8")


_ING_CAL_DDL = """
CREATE TABLE IF NOT EXISTS demand_calendar (
  event_id  INTEGER PRIMARY KEY,
  name      TEXT NOT NULL,
  date_from TEXT NOT NULL,
  date_to   TEXT NOT NULL,
  branch    TEXT NOT NULL DEFAULT '',
  factor    REAL,
  note      TEXT,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
)"""


_SEASON_BASELINE_DAYS = 56      # "normal" stretch used as the denominator


_SEASON_GAP_DAYS = 14           # ends this far before the festival starts


_SEASON_MIN_COVER = 0.6         # need this share of days present in both windows


_SEASON_CLAMP = (0.5, 3.0)      # refuse absurd factors from thin data


def _ing_daily_pax(con, branch: Optional[str], start: str, end: str):
    """(avg daily covers, days present) for a branch (or the chain) in a window."""
    f = ["date(t.sold_at) BETWEEN :s AND :e", "b.name != 'ALL'"]
    p: Dict = {"s": start, "e": end}
    if branch:
        f.append("b.name = :branch")
        p["branch"] = branch
    row = con.execute(f"""
        SELECT AVG(pax) AS avg_pax, COUNT(*) AS nd FROM (
            SELECT date(t.sold_at) AS d, SUM(t.customer_count) AS pax
              FROM sales_transaction t JOIN branches b ON b.branch_id = t.branch_id
             WHERE {' AND '.join(f)} GROUP BY date(t.sold_at))
    """, p).fetchone()
    return float(row["avg_pax"] or 0), int(row["nd"] or 0)


def _ing_season_factor(con, branch: Optional[str], start: str, end: str):
    """How much busier the same calendar dates were last year vs normal.

    Returns a dict that always carries `factor` (1.0 when it cannot be measured)
    plus the evidence, so the UI and the letter can show WHY it was scaled."""
    ly_start = con.execute("SELECT date(:d,'-1 year')", {"d": start}).fetchone()[0]
    ly_end = con.execute("SELECT date(:d,'-1 year')", {"d": end}).fetchone()[0]
    base_end = con.execute("SELECT date(:d, :g)", {"d": ly_start,
                                                   "g": f"-{_SEASON_GAP_DAYS} day"}).fetchone()[0]
    base_start = con.execute("SELECT date(:d, :g)", {"d": base_end,
                                                     "g": f"-{_SEASON_BASELINE_DAYS - 1} day"}).fetchone()[0]
    want_t = (datetime.strptime(ly_end, "%Y-%m-%d") - datetime.strptime(ly_start, "%Y-%m-%d")).days + 1
    out = {"factor": 1.0, "available": False, "scope": branch or "chain",
           "ly_from": ly_start, "ly_to": ly_end,
           "baseline_from": base_start, "baseline_to": base_end}

    def measure(scope):
        t_pax, t_nd = _ing_daily_pax(con, scope, ly_start, ly_end)
        b_pax, b_nd = _ing_daily_pax(con, scope, base_start, base_end)
        if t_nd < max(1, want_t * _SEASON_MIN_COVER) or b_nd < _SEASON_BASELINE_DAYS * _SEASON_MIN_COVER:
            return None
        if b_pax <= 0 or t_pax <= 0:
            return None
        return t_pax, b_pax

    m = measure(branch) if branch else None
    if m:
        out["scope"] = branch
    else:
        # a branch that did not exist last year falls back to the chain shape
        m = measure(None)
        out["scope"] = "chain" if m else (branch or "chain")
    if not m:
        return out
    t_pax, b_pax = m
    raw = t_pax / b_pax
    out.update({"factor": round(max(_SEASON_CLAMP[0], min(raw, _SEASON_CLAMP[1])), 3),
                "raw_factor": round(raw, 3), "available": True,
                "ly_pax_per_day": round(t_pax, 1), "baseline_pax_per_day": round(b_pax, 1),
                "clamped": not (_SEASON_CLAMP[0] <= raw <= _SEASON_CLAMP[1])})
    return out


def _ing_calendar_hit(con, branch: Optional[str], start: str, end: str):
    """A named event overlapping the cycle — branch-specific beats chain-wide."""
    con.execute(_ING_CAL_DDL)
    rows = con.execute("""
        SELECT * FROM demand_calendar
         WHERE date_from <= :e AND date_to >= :s AND (branch = '' OR branch = :b)
         ORDER BY (branch = '') ASC, (date_to - date_from) ASC""",
        {"s": start, "e": end, "b": branch or ""}).fetchall()
    return dict(rows[0]) if rows else None


def _ing_season_resolve(con, branch: Optional[str], start: str, end: str, mode: str):
    """mode: off | auto. Returns the factor actually applied plus its reason."""
    if mode != "auto" or not start or not end:
        return {"factor": 1.0, "mode": "off", "reason": ""}
    ev = _ing_calendar_hit(con, branch, start, end)
    if ev and ev.get("factor"):
        return {"factor": float(ev["factor"]), "mode": "calendar-manual",
                "event": ev["name"], "reason": f"ตั้งค่าไว้ในปฏิทิน ({ev['name']})",
                "window": [ev["date_from"], ev["date_to"]]}
    win = ([ev["date_from"], ev["date_to"]] if ev else [start, end])
    s = _ing_season_factor(con, branch, win[0], win[1])
    if not s["available"]:
        return {"factor": 1.0, "mode": "auto-nodata", "event": (ev or {}).get("name", ""),
                "reason": "ไม่มีข้อมูลปีก่อนมากพอ จึงไม่ปรับ", "detail": s}
    label = (ev["name"] + " · ") if ev else ""
    return {"factor": s["factor"], "mode": "auto", "event": (ev or {}).get("name", ""),
            "reason": (f"{label}อ้างอิงยอดลูกค้าช่วงเดียวกันปีก่อน "
                       f"({s['ly_from']}–{s['ly_to']} เฉลี่ย {s['ly_pax_per_day']:,.0f} คน/วัน "
                       f"เทียบช่วงปกติ {s['baseline_pax_per_day']:,.0f} คน/วัน)"),
            "window": win, "detail": s}


@router.get("/api/ingredients/season-factor")
def ingredients_season_factor(start: str = Query(...), end: str = Query(...),
                              branch: Optional[str] = Query(None),
                              mode: str = Query("auto")):
    """Preview the festival/promotion factor for a cycle before issuing it."""
    with get_db() as con:
        return _ing_season_resolve(con, branch, start, end,
                                   "auto" if mode == "auto" else "off")


class CalendarEvent(BaseModel):
    event_id: Optional[int] = None
    name: str
    date_from: str
    date_to: str
    branch: Optional[str] = ""
    factor: Optional[float] = None      # None = derive from last year
    note: Optional[str] = None


@router.get("/api/ingredients/demand-calendar")
def ingredients_demand_calendar():
    with get_db() as con:
        con.execute(_ING_CAL_DDL)
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM demand_calendar ORDER BY date_from DESC")]
    return {"rows": rows}


@router.post("/api/ingredients/demand-calendar", dependencies=[Depends(require_write_token)])
def ingredients_demand_calendar_save(events: List[CalendarEvent]):
    with get_db() as con:
        con.execute(_ING_CAL_DDL)
        for e in events:
            if e.date_from > e.date_to:
                raise HTTPException(400, f"ช่วงวันที่ไม่ถูกต้อง: {e.name}")
            if e.event_id:
                con.execute("""UPDATE demand_calendar SET name=?, date_from=?, date_to=?,
                                 branch=?, factor=?, note=?, updated_at=CURRENT_TIMESTAMP
                                WHERE event_id=?""",
                            (e.name.strip(), e.date_from, e.date_to, (e.branch or "").strip(),
                             e.factor, e.note, e.event_id))
            else:
                con.execute("""INSERT INTO demand_calendar
                                 (name, date_from, date_to, branch, factor, note)
                               VALUES (?,?,?,?,?,?)""",
                            (e.name.strip(), e.date_from, e.date_to, (e.branch or "").strip(),
                             e.factor, e.note))
        con.commit()
    invalidate_cache()
    return {"ok": True, "saved": len(events)}


@router.delete("/api/ingredients/demand-calendar/{event_id}",
            dependencies=[Depends(require_write_token)])
def ingredients_demand_calendar_delete(event_id: int):
    with get_db() as con:
        con.execute(_ING_CAL_DDL)
        con.execute("DELETE FROM demand_calendar WHERE event_id=?", (event_id,))
        con.commit()
    invalidate_cache()
    return {"ok": True}


def _pctl(sorted_vals, q: float):
    """Linear-interpolated percentile on an already-sorted list."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * max(0.0, min(q, 1.0))
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _ing_consumed(con, branch: str, start: str, end: str):
    """Actual consumption per ingredient for one branch over a closed window."""
    rows = con.execute(f"""
        SELECT COALESCE(NULLIF(bi.ingredient_code,''), '__' || bi.ingredient_name) AS code,
               bi.unit AS unit,
               SUM(d.qty * bi.qty_per_serving) AS qty
          FROM v_sales_sku_daily d
          JOIN branches b         ON b.branch_id = d.branch_id
          JOIN {_ING_BOM_MAP} m   ON m.sale_sku  = d.sku_id
          JOIN bom_ingredients bi ON bi.sku_id   = m.bom_sku
         WHERE b.name = :branch AND d.day BETWEEN :start AND :end
         GROUP BY code, bi.unit
    """, {"branch": branch, "start": start, "end": end}).fetchall()
    return {(r["code"], r["unit"] or ""): float(r["qty"] or 0) for r in rows}


@router.get("/api/ingredients/order-accuracy")
@ttl_cache
def ingredients_order_accuracy(
    branch:        Optional[str] = Query(None),
    service_level: float         = Query(80.0, description="buffer should cover this % of past cycles"),
    min_cycles:    int           = Query(2, description="ingredients seen in fewer cycles are listed but not tuned"),
    limit:         int           = Query(200),
):
    """How well the plan predicted real consumption, and what buffer to use next.

    Only CLOSED cycles count — a cycle whose period_end is past the last day of
    sales data would be judged on days that have not happened yet."""
    sl = max(50.0, min(float(service_level or 80), 99.0)) / 100.0
    with get_db() as con:
        _ing_issue_tables(con)
        maxd = con.execute("SELECT MAX(day) FROM sales_sku_daily").fetchone()[0]
        if not maxd:
            return {"available": False, "reason": "no daily sales data"}
        f = ["i.period_end <= :maxd"]
        p: Dict = {"maxd": maxd}
        if branch:
            f.append("i.branch = :branch")
            p["branch"] = branch
        issues = con.execute(
            f"SELECT * FROM ingredient_order_issue i WHERE {' AND '.join(f)} "
            f"ORDER BY i.period_start DESC LIMIT 60", p).fetchall()
        if not issues:
            return {"available": False,
                    "reason": "ยังไม่มีรอบสั่งที่ปิดแล้ว (period_end ต้องผ่านวันล่าสุดของข้อมูลขาย)",
                    "data_through": maxd}

        per_ing: Dict[tuple, dict] = {}
        per_branch: Dict[str, dict] = {}
        n_lines = 0
        for i in issues:
            consumed = _ing_consumed(con, i["branch"], i["period_start"], i["period_end"])
            buf = float(i["buffer_pct"] or 0)
            for l in con.execute(
                    "SELECT * FROM ingredient_order_issue_line WHERE issue_id=?",
                    (i["issue_id"],)):
                rec = float(l["rec_base"] or 0)
                if rec <= 0:
                    continue
                used = consumed.get((l["ingredient_code"], l["base_unit"] or ""), 0.0)
                raw = rec / (1.0 + buf / 100.0)          # forecast before the buffer
                if raw <= 0:
                    continue
                ratio = used / raw                        # >1 = we under-forecast
                n_lines += 1
                k = (l["ingredient_code"], l["base_unit"] or "")
                e = per_ing.setdefault(k, {
                    "code": l["ingredient_code"], "name": l["name"] or "",
                    "unit": l["base_unit"] or "", "n": 0,
                    "rec": 0.0, "used": 0.0, "ratios": []})
                e["n"] += 1
                e["rec"] += rec
                e["used"] += used
                e["ratios"].append(ratio)
                b = per_branch.setdefault(i["branch"], {"branch": i["branch"], "n": 0,
                                                        "short": 0, "ratios": []})
                b["n"] += 1
                b["ratios"].append(ratio)
                if used > rec:
                    b["short"] += 1

    def finish(e):
        rs = sorted(e.pop("ratios"))
        p50, p80 = _pctl(rs, 0.5), _pctl(rs, sl)
        e["ratio_median"] = round(p50, 3) if p50 is not None else None
        e["ratio_service"] = round(p80, 3) if p80 is not None else None
        if p80 is not None and e["n"] >= min_cycles:
            e["suggested_buffer_pct"] = round(max(0.0, (p80 - 1.0) * 100.0), 1)
        else:
            e["suggested_buffer_pct"] = None
        return e

    ings = [finish(e) for e in per_ing.values()]
    for e in ings:
        e["rec"] = round(e["rec"], 2)
        e["used"] = round(e["used"], 2)
        e["rec_disp"] = _ing_pretty(e["rec"], e["unit"])[2]
        e["used_disp"] = _ing_pretty(e["used"], e["unit"])[2]
        e["verdict"] = ("under" if (e["ratio_median"] or 1) > 1.05
                        else "over" if (e["ratio_median"] or 1) < 0.8 else "ok")
    ings.sort(key=lambda e: -(e["ratio_median"] or 0))
    brs = [finish(b) for b in per_branch.values()]
    for b in brs:
        b["short_pct"] = round(100.0 * b["short"] / b["n"], 1) if b["n"] else 0

    flat = sorted([x for e in ings for x in [e["ratio_median"]] if x is not None])
    headline = {
        "cycles_evaluated": len(issues),
        "lines_evaluated": n_lines,
        "service_level_pct": round(sl * 100, 1),
        "median_ratio": round(_pctl(flat, 0.5), 3) if flat else None,
        "suggested_buffer_pct": (round(max(0.0, (_pctl(flat, sl) - 1.0) * 100.0), 1)
                                 if flat else None),
        "buffer_used_median": round(sum(float(i["buffer_pct"] or 0) for i in issues) / len(issues), 1),
        "data_through": maxd,
    }
    return {"available": True, "headline": headline,
            "by_ingredient": ings[:max(1, min(int(limit or 200), 1000))],
            "by_branch": sorted(brs, key=lambda b: -b["short_pct"])}
