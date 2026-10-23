"""The ingredient order plan: sales × recipe → what each branch should order,
pack sizes, central-vs-branch sourcing, issued cycles, actuals, compliance
letters, the festival calendar and buffer accuracy.

Moved out of main.py in Phase 2b (2026-09) by the AST: these endpoints and
the helpers only they use, unchanged. URLs are unchanged.
"""
import csv as _csv
import os as _os
from pathlib import Path as _Path

from fastapi import APIRouter
from core import _group_filter
from core import _ingredient_family
from core import get_db
from core import invalidate_cache
from core import require_write_token
from core import ttl_cache
from datetime import datetime
from fastapi import Depends
from fastapi import File
from fastapi import UploadFile
from fastapi import HTTPException
from fastapi import Query
from fastapi import Response
from pydantic import BaseModel
from rules import EASY_TIER_CODES_SQL as _EASY_TIER_CODES_SQL
from rules import GRAND_MENU_FROM
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


def _ing_window(con, days: int, date_to: Optional[str], date_from: Optional[str] = None):
    """Resolve the lookback window on sales_sku_daily. Returns (start, end, days).

    Two ways to say it: a LENGTH (`days` back from `date_to`, the rolling default)
    or an explicit `date_from` … `date_to` range, which wins when given and makes
    `days` the measured span. The explicit form exists because a rolling window is
    the wrong shape for an order cycle — "28 วันล่าสุด" straddles a month end, a
    menu change, or the cycle the branch is actually ordering for."""
    maxd = con.execute("SELECT MAX(day) FROM v_sales_sku_daily").fetchone()[0]
    if not maxd:
        return None, None, 0
    end = date_to or maxd
    if end > maxd:
        end = maxd
    if date_from:
        start = min(str(date_from)[:10], end)
        days = con.execute("SELECT julianday(:e) - julianday(:s) + 1",
                           {"e": end, "s": start}).fetchone()[0]
        return start, end, max(1, int(days or 1))
    days = max(1, min(int(days or 28), 365))
    start = con.execute("SELECT date(:e, :off)", {"e": end, "off": f"-{days - 1} day"}).fetchone()[0]
    return start, end, days


def _ing_letter_menu_era_line(start: str, end: str) -> str:
    """§2 of the order letter: which recipe book the quantities came from.

    Three cases, and the middle one is why the line exists — a reference window
    spanning the changeover averages a menu that was withdrawn with one that had
    just launched, so the branch is told the number is provisional instead of
    ordering against a blend it cannot see."""
    if start >= GRAND_MENU_FROM:
        return f"อ้างอิงเมนูปัจจุบัน (Grand Menu) ที่เริ่มใช้ตั้งแต่ {GRAND_MENU_FROM} เป็นต้นไป"
    if end >= GRAND_MENU_FROM:
        return (f"<b style='color:#b91c1c'>ช่วงอ้างอิงคาบเกี่ยววันเปลี่ยนเมนู {GRAND_MENU_FROM}</b> "
                f"— ปริมาณข้างต้นเป็นค่าเฉลี่ยของสูตรเมนูเดิมและเมนูปัจจุบันรวมกัน "
                f"กรุณาใช้เป็นค่าประมาณเบื้องต้น และยืนยันปริมาณจริงกับสาขาอีกครั้ง")
    return "อ้างอิงเมนูที่ใช้อยู่ในช่วงวันที่ระบุข้างต้น"


def _ing_era_note(start: str, end: str) -> Optional[dict]:
    """A window straddling the Grand Menu changeover averages demand for dishes
    that were withdrawn with dishes that had just launched — the recipes behind
    the two halves are different, so the per-day figure is a blend of two menus,
    not a forecast of either. Flag it (the caller decides whether to cut)."""
    if not (start and end) or not (start < GRAND_MENU_FROM <= end):
        return None
    return {"straddles": True, "menu_era_from": GRAND_MENU_FROM,
            "suggest_from": GRAND_MENU_FROM,
            "reason": (f"ช่วงที่เลือกคร่อมวันเปลี่ยนเมนู {GRAND_MENU_FROM} — "
                       f"ปริมาณที่ได้เป็นค่าเฉลี่ยของเมนูเก่ากับเมนูใหม่ปนกัน "
                       f"แนะนำให้เริ่มช่วงที่ {GRAND_MENU_FROM}")}


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
    """Base-unit demand -> a WHOLE number of ordering units, always rounded UP.

    Owner, 2026-09-22: "ปัดเศษให้เป็นจำนวนเต็ม (ปัดขึ้นทุกกรณี)". So `units` is an int,
    without exception: a pack step below 1 (half-cartons) is ignored, a step of 6
    still rounds up to the next multiple of 6, a MOQ still applies, and the
    กก./ลิตร fallback rounds up to whole kilos / litres.

    Returns the pack description, how many to order, and what actually arrives
    (packs x pack size) so the branch can see the carry-over it is accepting."""
    import math as _m
    if pack:
        per = float(pack.get("qty_per_order") or 0) or 1.0
        label = pack.get("order_unit") or unit
        step = int(float(pack.get("round_step") or 1)) or 1        # never a fraction of a unit
        moq = int(_m.ceil(float(pack.get("moq") or 0)))
        price = pack.get("price_per_order")
        known = True
    else:
        label, per = _ING_UNIT_FALLBACK.get(unit or "", (unit or "หน่วย", 1.0))
        step, moq, price, known = 1, 0, None, False
    units = 0
    if need_base > 0:
        units = int(_m.ceil(_m.ceil(need_base / per) / step) * step)
        if moq and units < moq:
            units = moq
    return {
        "order_unit": label,
        "qty_per_order": round(per, 4),
        "units": units,
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


# ── the forced-order list (บังคับสั่ง) ─────────────────────────────────────────
# Owner, 2026-09-21: "ปริมาณที่ควรสั่ง รายวัตถุดิบ ให้ใช้ตามนี้ครับ ที่บังคับสั่ง เบื้องต้นจะได้
# มอนิเตอร์ถูก" - with the central warehouse's own stock sheet. Until then EVERY ingredient
# with a recipe (~196 codes) defaulted to "สั่งส่วนกลาง", so the branch letter and the
# compliance check judged a branch on garnish it rightly buys at the market.
#
# backend/data/forced_order_*.csv (newest wins) IS the list: a listed code defaults to
# 'central' (monitored), every other code to 'local'. It only moves the DEFAULT - a tick or
# un-tick made in the app (ingredient_sourcing) still wins, per branch or chain-wide.
# No file -> the old behaviour (everything central).
#
# `recipe_codes`: the warehouse and the recipes do not always share a code for the same
# material (whole frozen salmon 03-03-438 is recipe ingredient 02-01-001 แซลมอน; 13-01-451
# is NEW CODE-021 ตับปลาอังกิโมะ). Those recipe codes count as listed too, else a third of
# the sheet could never be monitored. Membership only - quantities stay in recipe units.
_FORCED_DIR = _Path(__file__).resolve().parents[1] / "data"
_FORCED_CACHE: dict = {"key": None, "val": None}
_FORCED_NONE: dict = {"file": "", "items": [], "codes": {}}


def _forced_file():
    override = _os.environ.get("OSHINEI_FORCED_ORDER_FILE")
    if override is not None:                       # "" switches the list off (the test-suite does)
        return _Path(override) if override else None
    files = sorted(_FORCED_DIR.glob("forced_order_*.csv"))
    return files[-1] if files else None


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _forced_order() -> dict:
    """{'file', 'items': [...], 'codes': {warehouse OR recipe code: item}}"""
    f = _forced_file()
    if not f or not f.exists():
        return _FORCED_NONE
    key = (str(f), f.stat().st_mtime_ns)
    if _FORCED_CACHE["key"] == key:
        return _FORCED_CACHE["val"]
    items, codes = [], {}
    with open(f, encoding="utf-8-sig", newline="") as fh:
        for row in _csv.DictReader(fh):
            code = (row.get("code") or "").strip()
            if not code or code in codes:
                continue
            item = {"code": code, "name": (row.get("name") or "").strip(),
                    "unit": (row.get("unit") or "").strip(), "group": (row.get("group") or "").strip(),
                    "remark": (row.get("remark") or "").strip(),
                    "monthly_need": _num(row.get("monthly_need")), "stock_months": _num(row.get("stock_months")),
                    "sheet_no": int(_num(row.get("sheet_no")) or 0),
                    "recipe_codes": [c.strip() for c in (row.get("recipe_codes") or "").split("|") if c.strip()]}
            items.append(item)
            codes[code] = item
    for item in items:                              # a warehouse code always beats an alias
        for alias in item["recipe_codes"]:
            codes.setdefault(alias, item)
    val = {"file": f.name, "items": items, "codes": codes}
    _FORCED_CACHE.update(key=key, val=val)
    return val


def _forced_default(code: str) -> str:
    codes = _forced_order()["codes"]
    return "central" if (not codes or code in codes) else "local"


def _ing_mode(code: str, branch: str, defaults: dict, per_branch: dict) -> str:
    if not code:
        return "central"
    return per_branch.get((code, branch or ""), defaults.get(code, _forced_default(code)))


def _code_remap_sql(con):
    """(JOIN, code expression) applying the BoM Center code check (bc_code_review):
    an old-recipe line (code + name) the analyst marked "really means X" is planned as X.
    Planning only — costs never read this. No table → no-op."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='bc_code_review'").fetchone():
        return "", "bi.ingredient_code"
    return ("LEFT JOIN bc_code_review cr ON cr.code = TRIM(bi.ingredient_code)"
            " AND cr.recipe_name = COALESCE(bi.ingredient_name, '') AND cr.status = 'remap'",
            "COALESCE(cr.use_code, bi.ingredient_code)")


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
    rj, rc = _code_remap_sql(con)
    sql = f"""
        SELECT {key_cols}
               COALESCE(NULLIF({rc},''), '__' || bi.ingredient_name) AS ing_key,
               MAX({rc}) AS code,
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
          {rj}
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
    fo = _forced_order()
    forced = fo["codes"]
    for r in out:
        r["sourcing"] = _ing_mode(r["code"], r.get("branch") or branch or "",
                                  src_def, src_branch)
        item = forced.get(r["code"])
        r["forced"] = bool(item)
        r["forced_group"] = item["group"] if item else ""
        r["forced_as"] = item["code"] if item and item["code"] != r["code"] else ""   # listed under this warehouse code
        r["forced_name"] = item["name"] if item else ""                                # the sheet's own wording
        r["no_usage"] = False
    if include == "central":
        out = [r for r in out if r["sourcing"] == "central"]
    elif include == "local":
        out = [r for r in out if r["sourcing"] == "local"]

    # Owner 2026-10-02 ("แสดง 48 รายการตามใบคลังเสมอ"): every line of the warehouse
    # sheet is on the plan even when nothing used it in the window — a zero row
    # under the sheet's own code, name and unit, flagged no_usage — so the list
    # the branches are told to order from is always the list on screen (chain
    # and per branch alike; the letters read include=central). A type filter
    # cannot be answered for a sheet line, so that stays a real-usage view.
    if include != "local" and fo["items"] and not ing_type:
        present = {(r.get("branch") or "", r["code"]) for r in out}
        scopes = sorted({r.get("branch") or "" for r in out}) if per_branch else [""]
        ql = (q or "").lower()
        for bname in scopes:
            for item in fo["items"]:
                if any((bname, k) in present for k in [item["code"]] + item["recipe_codes"]):
                    continue
                if ql and ql not in (item["name"] + " " + item["code"]).lower():
                    continue
                mode = _ing_mode(item["code"], bname or branch or "", src_def, src_branch)
                if include == "central" and mode != "central":
                    continue
                row = {"code": item["code"], "name": item["name"], "type": "", "unit": item["unit"] or "",
                       "qty_used": 0.0, "per_day": 0.0, "per_week": 0.0, "per_month": 0.0,
                       "order_week": 0.0, "order_month": 0.0, "unit_cost": 0.0, "cost_used": 0.0,
                       "value_week": 0.0, "value_month": 0.0, "n_menus": 0, "trend_pct": None,
                       "sourcing": mode, "forced": True, "forced_group": item["group"], "forced_as": "",
                       "forced_name": item["name"], "no_usage": True}
                if per_branch:
                    row["branch"] = bname
                out.append(row)

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
                f["forced"] = f["forced"] or r["forced"]
                f["forced_group"] = f["forced_group"] or r["forced_group"]
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
    date_from:    Optional[str] = Query(None, description="explicit window start — wins over `days`"),
    split_branches: bool        = Query(False, description="also return every branch's own quantity per ingredient"),
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
        start, end, days = _ing_window(con, days, date_to, date_from)
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
        by_branch, branch_names = [], []
        if not branch:
            per_b = _ing_plan_rows(con, start, end, days, None, group, basis, buffer_pct,
                                   type, q, False, per_branch=True, include=include)
            agg: Dict[str, dict] = {}
            for r in per_b:
                a = agg.setdefault(r["branch"], {"branch": r["branch"], "ingredients": 0,
                                                 "value_week": 0.0, "value_month": 0.0})
                if r.get("no_usage"):
                    continue          # a sheet line nobody used is not an ingredient the branch consumed
                a["ingredients"] += 1
                a["value_week"] += r["value_week"]
                a["value_month"] += r["value_month"]
            by_branch = sorted(agg.values(), key=lambda x: x["value_month"], reverse=True)
            for a in by_branch:
                a["value_week"] = round(a["value_week"], 2)
                a["value_month"] = round(a["value_month"], 2)
            if split_branches:
                # One line per ingredient with every branch's own quantity beside
                # it: the chain total says what to buy, this says who it is for.
                # Keyed on code|name because a family rollup blanks the code.
                branch_names = [a["branch"] for a in by_branch]
                per_ing: Dict[str, dict] = {}
                for r in per_b:
                    per_ing.setdefault(f"{r['code']}|{r['name']}", {})[r["branch"]] = {
                        "per_day": r["per_day"], "order_week": r["order_week"],
                        "order_month": r["order_month"], "value_month": r["value_month"],
                        "order_week_units": r.get("order_week_units"),
                        "order_month_units": r.get("order_month_units")}
                for r in rows:
                    r["by_branch"] = per_ing.get(f"{r['code']}|{r['name']}", {})

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
                 "forced": {"file": _forced_order()["file"], "items": len(_forced_order()["items"])},
                 "window_mode": "range" if date_from else "rolling",
                 "era": _ing_era_note(start, end), "menu_era_from": GRAND_MENU_FROM,
                 "branch_names": branch_names,
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
    date_from:  Optional[str] = Query(None),
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
        start, end, days = _ing_window(con, days, date_to, date_from)
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
    __import__('xl_safe').guard_workbook(wb)  # PL1 #9: no cell becomes a formula
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


def _sheet_title(name: str, taken: set) -> str:
    """An Excel sheet name: no []:*?/\\, at most 31 characters, unique."""
    t = "".join(ch for ch in str(name or "สาขา") if ch not in '[]:*?/\\').strip()[:31] or "สาขา"
    base, n = t, 2
    while t in taken:
        suffix = f" ({n})"
        t = base[:31 - len(suffix)] + suffix
        n += 1
    taken.add(t)
    return t


@router.get("/api/ingredients/order-plan-by-branch.xlsx")
def ingredients_order_plan_by_branch_xlsx(
    branch:      Optional[str] = Query(None, description="one branch; omit for every branch (one sheet each)"),
    group:       Optional[str] = Query(None),
    days:        int           = Query(28),
    date_to:     Optional[str] = Query(None),
    date_from:   Optional[str] = Query(None),
    basis:       str           = Query("avg"),
    buffer_pct:  float         = Query(10.0),
    include:     str           = Query("central", description="central (รายการที่สั่งผ่านส่วนกลาง) | all | local"),
    season:      Optional[str] = Query(None, description="auto = scale each branch by its own festival factor"),
    cycle_start: Optional[str] = Query(None),
    top:         int           = Query(0, description="ingredients per branch, 0 = all"),
):
    """ปริมาณที่ควรสั่ง รายวัตถุดิบ — one sheet per branch, whole numbers, rounded up.

    Owner, 2026-09-22: "ต้องทำเป็นรายงานแยกสาขาครับ จะได้แนะนำสาขาได้ถูก และปัดเศษให้เป็น
    จำนวนเต็ม (ปัดขึ้นทุกกรณี)". The chain-level export lists every branch x ingredient
    on one long sheet; this is the same numbers laid out for handing to A branch:
    a สรุป sheet, then one sheet per branch with only what that branch should order.
    Default include=central: the items branches must order through the warehouse —
    the forced-order list plus anything ticked สั่งส่วนกลาง — because a branch is
    advised on those, not on what it buys at the market."""
    buffer_pct = max(0.0, min(float(buffer_pct or 0), 100.0))
    basis = "recent" if str(basis).lower() == "recent" else "avg"
    include = include if include in ("all", "central", "local") else "central"
    with get_db() as con:
        start, end, days = _ing_window(con, days, date_to, date_from)
        if not start:
            raise HTTPException(status_code=404, detail="ไม่มีข้อมูลยอดขายรายวัน")
        names = [branch] if branch else _ing_branch_names(con)
        per_b = _ing_plan_rows(con, start, end, days, branch, group, basis, buffer_pct,
                               None, None, False, per_branch=not branch, include=include)
        if branch:
            for r in per_b:
                r["branch"] = branch
        by: Dict[str, list] = {}
        for r in per_b:
            by.setdefault(r["branch"], []).append(r)
        c_start = cycle_start or con.execute("SELECT date('now','localtime')").fetchone()[0]
        c_end = con.execute("SELECT date(:s,'+6 day')", {"s": c_start}).fetchone()[0]
        factors: Dict[str, dict] = {}
        for b, rows in by.items():
            info = _ing_season_resolve(con, b, c_start, c_end, "auto" if season == "auto" else "off")
            sf = float(info.get("factor") or 1.0)
            factors[b] = info
            if abs(sf - 1.0) > 1e-6:                    # each branch scaled by ITS OWN factor
                for r in rows:
                    for k in ("per_day", "per_week", "per_month", "order_week", "order_month",
                              "value_week", "value_month"):
                        r[k] = round(r[k] * sf, 4)
                _ing_reapply_order_units(con, rows)
            rows.sort(key=lambda x: x["value_month"], reverse=True)
            if top and top > 0:
                del rows[top:]

    from openpyxl.styles import Alignment as _AL, Font as _F
    wb = _wb_new("สรุป")
    ws = wb.active
    inc_th = {"central": "เฉพาะรายการที่สั่งผ่านส่วนกลาง (บังคับสั่ง + ที่ติ๊กสั่งส่วนกลาง)",
              "all": "ทุกรายการ", "local": "เฉพาะรายการที่สาขาซื้อเอง"}[include]
    note = (f"ข้อมูลขายจริง {start} ถึง {end} ({days} วัน) · ฐาน {'7 วันล่าสุด' if basis == 'recent' else 'เฉลี่ยทั้งช่วง'} · "
            f"เผื่อ {buffer_pct:g}% · {inc_th} · ปริมาณสั่งปัดขึ้นเป็นจำนวนเต็มทุกรายการ"
            + (" · ปรับตามเทศกาลรายสาขา" if season == "auto" else " · ไม่รวมปัจจัยเทศกาล"))
    ws.append(["ปริมาณที่ควรสั่ง รายวัตถุดิบ — แยกสาขา (1 แผ่นต่อสาขา)"]); ws["A1"].font = _F(bold=True, size=13)
    ws.append([note]); ws["A2"].font = _F(size=9, color="475569")
    ws.append([])
    ws.append(["สาขา", "จำนวนรายการ", "มูลค่าที่ควรสั่ง/สัปดาห์ (บาท)", "มูลค่าที่ควรสั่ง/เดือน (บาท)", "ปัจจัยเทศกาล", "แผ่น"])
    _style_header(ws, 4)
    taken: set = set()
    titles = {b: _sheet_title(b, taken) for b in names}
    order = sorted(names, key=lambda b: -sum(r["value_month"] for r in by.get(b, [])))
    tw = tm = 0.0
    for b in order:
        rows = by.get(b, [])
        vw, vm = sum(r["value_week"] for r in rows), sum(r["value_month"] for r in rows)
        tw += vw; tm += vm
        f = factors.get(b, {}).get("factor")
        ws.append([b, len(rows), round(vw, 2), round(vm, 2), f"x{f:g}" if f and abs(f - 1) > 1e-6 else "—", titles[b]])
    ws.append(["รวม", sum(len(by.get(b, [])) for b in names), round(tw, 2), round(tm, 2), "", ""])
    for c in ws[ws.max_row]:
        c.font = _F(bold=True)
    for col, w in zip("ABCDEF", (24, 14, 26, 26, 14, 24)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=5, min_col=3, max_col=4):
        for c in row:
            c.number_format = "#,##0"
    ws.freeze_panes = "A5"

    head = ["ลำดับ", "รหัส", "วัตถุดิบ", "บังคับสั่ง", "หน่วยนับ", "ใช้เฉลี่ย/วัน", "หน่วยสั่ง",
            "ขนาดบรรจุ (1 หน่วยสั่ง = ? หน่วยนับ)", "ควรสั่ง/สัปดาห์", "ควรสั่ง/เดือน",
            "มูลค่า/เดือน (บาท)", "แนวโน้ม 7 วัน %", "หมายเหตุ"]
    for b in order:
        rows = by.get(b, [])
        s = wb.create_sheet(titles[b])
        s.append([f"ปริมาณที่ควรสั่ง รายวัตถุดิบ — {b}"]); s["A1"].font = _F(bold=True, size=13)
        s.append([note]); s["A2"].font = _F(size=9, color="475569")
        s.append([])
        s.append(head)
        _style_header(s, 4)
        for i, r in enumerate(rows, start=1):
            forced = ("ตปท." if r.get("forced_group") == "import" else "✓") if r.get("forced") else ""
            remark = [] if r.get("pack_known") else ["ยังไม่ตั้งขนาดบรรจุ — ใช้หน่วยมาตรฐาน"]
            if r.get("sourcing") == "local":
                remark.append("สาขาซื้อเอง")
            if r.get("forced_as"):
                remark.append(f"รหัสในใบคลัง {r['forced_as']}")
            if r.get("no_usage"):
                remark.append("ไม่มีการใช้ในช่วงนี้ — รายการบังคับสั่ง ติดตามต่อ")
            s.append([i, r["code"], r["name"], forced, r["unit"], round(float(r["per_day"]), 2),
                      r.get("order_unit") or r["unit"], r.get("qty_per_order"),
                      int(r.get("order_week_units") or 0), int(r.get("order_month_units") or 0),
                      round(float(r["value_month"]), 2), r.get("trend_pct"), " · ".join(remark) or None])
        if rows:
            s.append([]); s.append(["รวมมูลค่า/เดือน", None, None, None, None, None, None, None, None, None,
                                    round(sum(r["value_month"] for r in rows), 2)])
            for c in s[s.max_row]:
                c.font = _F(bold=True)
        else:
            s.append(["ไม่มีรายการตามเงื่อนไขที่เลือก"])
        for col, w in zip("ABCDEFGHIJKLM", (7, 13, 40, 10, 10, 13, 12, 22, 15, 15, 17, 14, 34)):
            s.column_dimensions[col].width = w
        for row in s.iter_rows(min_row=5, max_row=4 + len(rows)):
            row[8].number_format = row[9].number_format = "#,##0"        # whole numbers, as promised
            row[10].number_format = "#,##0"
            row[5].number_format = "#,##0.00"
            row[2].alignment = _AL(wrap_text=True, vertical="top")
        s.freeze_panes = "A5"
    return _xlsx_attachment(wb, f"ORDER_PLAN_BY_BRANCH_{end}.xlsx")


@router.get("/api/ingredients/order-letter.print")
def ingredients_order_letter(
    branch:     Optional[str] = Query(None, description="one branch; omit for every branch (one page each)"),
    group:      Optional[str] = Query(None),
    days:       int           = Query(28),
    date_to:    Optional[str] = Query(None),
    date_from:  Optional[str] = Query(None),
    basis:      str           = Query("avg"),
    buffer_pct: float         = Query(10.0),
    horizon:    str           = Query("week", description="week | month"),
    top:        int           = Query(40, description="ingredients listed per branch"),
    issuer:     Optional[str] = Query(None, description="department line on the signature block"),
    note:       Optional[str] = Query(None, description="extra paragraph before the table"),
    contract_ref: Optional[str] = Query(None, description="ข้อสัญญาแฟรนไชส์ที่อ้างถึง เช่น 'ข้อ 7.2 และ 9.1'"),
    reply_by:   Optional[str] = Query(None, description="กำหนดยืนยันปริมาณสั่งกลับ (YYYY-MM-DD)"),
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
        start, end, days = _ing_window(con, days, date_to, date_from)
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
        # Which branches are franchisees: the franchise-obligation section is a
        # contractual reference and must NOT print on a company-owned branch's
        # copy — that branch has no franchise agreement to be reminded of.
        btype = {r["name"]: (r["branch_type"] or "master") for r in con.execute(
            "SELECT name, branch_type FROM branches WHERE name != 'ALL'")}
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

    # ── §2 ข้อมูลอ้างอิงการออกเมนู ──────────────────────────────────────────
    # Where the quantities came from, stated as facts the branch can check
    # against its own POS. The menu era matters most: a recipe book changed on
    # GRAND_MENU_FROM, so a reference window reaching before it produced these
    # numbers from two different menus and the letter has to say so rather than
    # let the branch order against a blend.
    era_line = _ing_letter_menu_era_line(start, end)
    reply_line = (f'<li>กรุณา<b>ยืนยันปริมาณที่จะสั่งกลับภายในวันที่ {esc(reply_by)}</b> '
                  f'หากไม่ยืนยันภายในกำหนด จะถือตามปริมาณที่แนะนำในเอกสารนี้</li>'
                  if reply_by else '')

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
        # ── §3 หน้าที่ตามสัญญาแฟรนไชส์ ──────────────────────────────────────
        # Only on a franchisee's copy. The obligations below are the substance
        # the agreement covers; the CLAUSE NUMBERS are not invented here — the
        # caller passes contract_ref, and until it does the letter prints a blank
        # to be filled in by hand. A fabricated clause citation in a notice sent
        # to a franchisee is worse than no citation.
        is_franchise = btype.get(nm, "master") == "franchise"
        ref_txt = (f"ตามสัญญาแฟรนไชส์ {esc(contract_ref)}" if contract_ref
                   else "ตามสัญญาแฟรนไชส์ ข้อ ................")
        franchise_block = (f"""
  <div class="sec duty">
    <div class="sec-h">๓. หน้าที่ของสาขาตามสัญญาแฟรนไชส์</div>
    <p class="lead" style="text-indent:0;margin-bottom:6px">{ref_txt}
       สาขามีหน้าที่ที่เกี่ยวข้องกับเอกสารฉบับนี้ ดังนี้</p>
    <ol class="duties">
      <li><b>จัดหาวัตถุดิบผ่านช่องทางที่บริษัทกำหนด</b> — วัตถุดิบที่อยู่ในรายการข้างต้น
          ต้องสั่งผ่านส่วนกลางหรือผู้จำหน่ายที่บริษัทรับรอง เพื่อรักษามาตรฐานรสชาติ
          ความปลอดภัยของอาหาร และเงื่อนไขราคาที่ตกลงร่วมกัน
          (รายการที่ระบุว่า “สาขาจัดหาเอง” ไม่อยู่ในข้อนี้)</li>
      <li><b>คงสูตรและปริมาณการเสิร์ฟตามมาตรฐาน</b> — ปริมาณที่แนะนำคำนวณจากสูตรมาตรฐาน
          การเปลี่ยนวัตถุดิบ ผู้จำหน่าย หรือขนาดเสิร์ฟเองโดยไม่ได้รับอนุมัติเป็นลายลักษณ์อักษร
          ถือเป็นการไม่ปฏิบัติตามมาตรฐานที่ตกลงไว้</li>
      <li><b>สั่งซื้อให้เพียงพอต่อการให้บริการ</b> — สาขาต้องมีวัตถุดิบเพียงพอตลอดเวลาทำการ
          การสั่งต่ำกว่าปริมาณที่แนะนำอย่างมีนัยสำคัญจนทำให้ของขาดระหว่างให้บริการ
          กระทบต่อลูกค้าและชื่อเสียงของแบรนด์โดยรวม</li>
      <li><b>ส่งข้อมูลการขายและการสั่งซื้อตามรอบ</b> — ข้อมูลยอดขายรายวันคือฐานของการคำนวณนี้
          หากข้อมูลขาดหรือส่งล่าช้า ปริมาณที่แนะนำจะคลาดเคลื่อน
          และสาขาต้องบันทึกปริมาณที่สั่งจริงกลับเข้าระบบทุกรอบ</li>
      <li><b>ให้ความร่วมมือในการตรวจสอบ</b> — บริษัทอาจขอตรวจสอบเอกสารการสั่งซื้อ
          ใบส่งของ และสต๊อกคงเหลือ เพื่อสอบทานกับปริมาณที่แนะนำในเอกสารฉบับนี้</li>
    </ol>
    <p class="fineprint">หมายเหตุ: ข้อความข้างต้นเป็นการสรุปสาระสำคัญเพื่อความสะดวกในการปฏิบัติงาน
       ไม่ได้แทนที่ข้อความในสัญญาแฟรนไชส์ หากมีข้อความใดขัดหรือแย้งกัน ให้ยึดตามสัญญาเป็นสำคัญ</p>
  </div>""" if is_franchise else "")
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
  <p class="lead">ตามที่{issuer_line}ได้วิเคราะห์ปริมาณการใช้วัตถุดิบจริงของสาขา {esc(nm)}
     จากยอดขายรายวันคูณกับสูตรมาตรฐาน (BoM) จึงขอแจ้งปริมาณที่ควรสั่งประจำ{hz_label}
     โดยคำนวณจาก{basis_label} และเผื่อความผันผวนไว้ {buffer_pct:g}% ดังรายการต่อไปนี้</p>
  {('<p class="lead">' + esc(note) + '</p>') if note else ''}
  <div class="sec-h">๑. ปริมาณวัตถุดิบที่แนะนำให้สั่ง ประจำ{hz_label}</div>
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
  <div class="sec">
    <div class="sec-h">๒. ข้อมูลอ้างอิงและวิธีคำนวณ</div>
    <table class="ref"><tbody>
      <tr><td>ยอดขายที่ใช้อ้างอิง</td><td>{start} ถึง {end} ({days} วัน) · สาขา {esc(nm)}</td></tr>
      <tr><td>ฐานการคำนวณ</td><td>{basis_label} × สูตรมาตรฐาน (BoM) และเผื่อความผันผวน {buffer_pct:g}%</td></tr>
      <tr><td>เมนูที่อ้างอิง</td><td>{era_line}</td></tr>
      <tr><td>ความครอบคลุมของสูตร</td><td>{cov['pct']:g}% ของจำนวนจานที่ขายในช่วงอ้างอิง
          (เมนูที่ยังไม่มีสูตร BoM จะไม่ปรากฏในรายการนี้)</td></tr>
      <tr><td>หน่วยสั่ง</td><td>ช่อง “ควรสั่ง” ปัดขึ้นเป็นจำนวนเต็มหน่วยสั่งแล้ว
          (ในวงเล็บคือปริมาณจริงที่จะได้รับ) · รายการที่ยังไม่ได้ตั้งขนาดบรรจุแสดงเป็นหน่วยมาตรฐาน (กก./ลิตร)</td></tr>
      <tr><td>คอลัมน์แนวโน้ม</td><td>เทียบการใช้ 7 วันล่าสุดกับ 7 วันก่อนหน้า
          (▲ = ใช้เพิ่มขึ้น ควรเผื่อเพิ่ม · ▼ = ใช้ลดลง ควรลดปริมาณสั่ง)</td></tr>
    </tbody></table>
    <p class="warn"><b>ข้อควรทราบ:</b> ตัวเลขนี้คือปริมาณที่คาดว่าจะ<b>ใช้</b>
       <b>ยังไม่ได้หักสต๊อกคงเหลือในร้าน</b> กรุณาตรวจนับของคงเหลือก่อนออกใบสั่งซื้อทุกครั้ง ·
       เป็นการประมาณการจากยอดขายที่ผ่านมา หากมีเทศกาล โปรโมชัน หรือการปิดปรับปรุง
       กรุณาปรับเพิ่ม–ลดตามความเหมาะสม</p>
  </div>
  {franchise_block}
  <div class="sec">
    <div class="sec-h">สิ่งที่ขอให้สาขาดำเนินการ</div>
    <ol class="duties">
      <li>ตรวจนับสต๊อกคงเหลือ แล้วหักออกจากปริมาณที่แนะนำก่อนออกใบสั่งซื้อ</li>
      {reply_line}
      <li>เมื่อสั่งแล้ว บันทึก<b>ปริมาณที่สั่งจริง</b>กลับเข้าระบบ เพื่อใช้สอบทานความแม่นของการพยากรณ์รอบถัดไป</li>
      <li>หากปริมาณที่แนะนำไม่สอดคล้องกับหน้างาน (เช่น มีจัดเลี้ยง ปิดปรับปรุง หรือโปรโมชันเฉพาะสาขา)
          กรุณาแจ้ง{issuer_line}ก่อนถึงรอบสั่ง</li>
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
<link rel="stylesheet" href="/vendor/kira-ui-font.css?v=7.0">
<style>
  @page {{ size: A4; margin: 14mm 12mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family:"KIRA UI","Noto Sans Thai",sans-serif; color:#0f172a;
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
  .mono {{ font-family:ui-monospace,Consolas,monospace; font-size:10px; color:#475569; }}
  .excl {{ margin-top:8px; font-size:10.5px; color:#475569; background:#f1f5f9;
           border-left:3px solid #94a3b8; padding:6px 9px; line-height:1.6; }}
  .notes {{ margin-top:10px; font-size:10.5px; background:#fffbeb; border-left:3px solid #f59e0b;
            padding:7px 10px; line-height:1.6; }}
  .notes ol {{ margin:4px 0 0 16px; padding:0; }}
  .sec {{ margin-top:12px; }}
  .sec-h {{ font-size:12.5px; font-weight:700; color:#0f766e; margin:0 0 5px;
            border-left:4px solid #0f766e; padding-left:7px; }}
  table.ref {{ width:100%; border-collapse:collapse; font-size:10.5px; }}
  table.ref td {{ border:1px solid #e2e8f0; padding:4px 7px; vertical-align:top;
                  line-height:1.55; }}
  table.ref td:first-child {{ width:27%; background:#f8fafc; font-weight:600; color:#334155; }}
  .warn {{ margin:7px 0 0; font-size:10.5px; background:#fffbeb; border-left:3px solid #f59e0b;
           padding:7px 10px; line-height:1.6; }}
  .duty {{ background:#f8fafc; border:1px solid #cbd5e1; border-radius:5px; padding:8px 10px; }}
  ol.duties {{ margin:4px 0 0 17px; padding:0; font-size:10.5px; line-height:1.65; }}
  ol.duties li {{ margin-bottom:3px; }}
  .fineprint {{ margin:7px 0 0; font-size:9.5px; color:#64748b; line-height:1.55; }}
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
<div class="toolbar"><button onclick="window.print()">พิมพ์ / บันทึกเป็น PDF</button>
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


@router.get("/api/ingredients/forced-order")
def ingredients_forced_order():
    """The forced-order list as the app understands it: each line of the owner's sheet,
    whether any recipe uses it (directly or through `recipe_codes`), and so whether the
    plan can put a number on it. A listed item no recipe uses can be neither recommended
    nor monitored - that is the kitchen's worklist, not a silent gap."""
    fo = _forced_order()
    with get_db() as con:
        used = {r[0]: r[1] for r in con.execute(
            "SELECT ingredient_code, COUNT(DISTINCT sku_id) FROM bom_ingredients "
            "WHERE ingredient_code IS NOT NULL AND ingredient_code <> '' GROUP BY 1")}
    items = []
    for it in fo["items"]:
        found = [c for c in [it["code"]] + it["recipe_codes"] if c in used]
        items.append({**it, "in_recipes": bool(found), "matched_codes": found,
                      "menus": sum(used[c] for c in found),
                      "via_alias": bool(found) and it["code"] not in found})
    return {"file": fo["file"], "items": items,
            "summary": {"items": len(items), "monitorable": sum(1 for i in items if i["in_recipes"]),
                        "via_alias": sum(1 for i in items if i["via_alias"]),
                        "not_in_recipes": sum(1 for i in items if not i["in_recipes"]),
                        "import": sum(1 for i in items if i["group"] == "import")}}


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
    preview:       bool          = Query(False, description="stamp it ร่าง — for reading before it is sent"),
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
                "<!doctype html><meta charset='utf-8'><link rel='stylesheet' href='/vendor/kira-ui-font.css?v=7.0'>"
                "<div style=\"font-family:'KIRA UI','Noto Sans Thai',sans-serif;padding:40px;"
                "text-align:center;color:#047857\"><h2>ไม่มีสาขาที่ต้องออกหนังสือแจ้งเตือน</h2>"
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

    # A draft is read, not sent: it is stamped ร่าง on every page and says so in
    # the toolbar, so a preview can never be mistaken for the issued notice —
    # this letter names a branch and asks it to answer for a shortfall.
    # Plain string, single braces: it is substituted INTO the f-string below, so
    # its braces are never re-processed and must not be doubled.
    draft_css = ("""
  .draftmark { color:#b91c1c; border:2px dashed #b91c1c; border-radius:6px;
               padding:3px 10px; font-size:12px; font-weight:700; display:inline-block;
               margin-bottom:8px; letter-spacing:.5px; }
""" if preview else "")
    draft_bar = ('<span style="color:#b91c1c;font-weight:700;font-size:12px;'
                 'margin-right:10px">ร่าง — ตรวจก่อนออกจริง</span>' if preview else "")
    if preview:
        body = [b.replace('<div class="head">',
                          '<div class="draftmark">ร่าง (DRAFT) — ยังไม่ได้ออกจดหมายฉบับนี้</div>'
                          '<div class="head">', 1) for b in body]
    html_out = f"""<!doctype html><html lang="th"><head><meta charset="utf-8">
<title>{'ร่าง · ' if preview else ''}หนังสือแจ้งเตือนการสั่งวัตถุดิบ · {len(pages)} สาขา</title>
<link rel="stylesheet" href="/vendor/kira-ui-font.css?v=7.0">
<style>
  @page {{ size: A4; margin: 14mm 12mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family:"KIRA UI","Noto Sans Thai",sans-serif; color:#0f172a;
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
  .mono {{ font-family:ui-monospace,Consolas,monospace; font-size:10px; color:#475569; }}
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
{draft_css}
</style></head><body>
<div class="toolbar">{draft_bar}<button onclick="window.print()">พิมพ์ / บันทึกเป็น PDF</button>
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


_SEASON_SHIFT_DAYS = 364        # last year's window: 52 whole weeks back, so the weekdays line up


def _ing_pax_by_day(con, branch: Optional[str], start: str, end: str) -> Dict[str, float]:
    """{date: covers} for a branch (or the chain) in a window; days without covers are left out."""
    f = ["date(t.sold_at) BETWEEN :s AND :e", "b.name != 'ALL'"]
    p: Dict = {"s": start, "e": end}
    if branch:
        f.append("b.name = :branch")
        p["branch"] = branch
    rows = con.execute(f"""
        SELECT date(t.sold_at) AS d, SUM(t.customer_count) AS pax
          FROM sales_transaction t JOIN branches b ON b.branch_id = t.branch_id
         WHERE {' AND '.join(f)} GROUP BY date(t.sold_at)
    """, p).fetchall()
    return {r["d"]: float(r["pax"]) for r in rows if r["pax"] and r["pax"] > 0}


def _ing_season_factor(con, branch: Optional[str], start: str, end: str):
    """How much busier last year's comparable days were than a normal day of the same weekday.

    Method `weekday_364`: every day of the window is paired with the day 364 days earlier (the same weekday);
    a public holiday (thai_holidays) is paired with the same holiday of last year instead (23/10 with 23/10), and a
    plain day that falls on a holiday of last year is left out, so a holiday is never compared with a plain day.
    Each last-year day is judged against the mean of the SAME WEEKDAY over a 56-day baseline that ends 14 days before
    the window (holidays left out of the baseline): factor = sum of covers / sum of those weekday means. The weekday
    pattern (weekend peak) therefore cancels, which the order forecast already handles with its own weekday means.

    Returns a dict that always carries `factor` (1.0 when it cannot be measured)
    plus the evidence, so the UI and the letter can show WHY it was scaled."""
    import thai_holidays as TH
    from datetime import date, timedelta
    s0, e0 = date.fromisoformat(start), date.fromisoformat(end)
    shift = timedelta(days=_SEASON_SHIFT_DAYS)
    ly_s, ly_e = s0 - shift, e0 - shift
    base_e = ly_s - timedelta(days=_SEASON_GAP_DAYS)
    base_s = base_e - timedelta(days=_SEASON_BASELINE_DAYS - 1)
    want_t = (e0 - s0).days + 1
    out = {"factor": 1.0, "available": False, "scope": branch or "chain", "method": "weekday_364",
           "ly_from": ly_s.isoformat(), "ly_to": ly_e.isoformat(),
           "baseline_from": base_s.isoformat(), "baseline_to": base_e.isoformat()}

    pairs, skipped = [], []                      # (day, last year's day, holiday name | None)
    for d in TH.days(s0, e0):
        h = TH.holiday_on(d)
        if h:
            ly = TH.same_holiday_last_year(d)
            if ly is None:
                skipped.append({"date": d.isoformat(), "reason": "holiday_no_counterpart"})
                continue
        else:
            ly = d - shift
            if TH.is_holiday(ly):
                skipped.append({"date": d.isoformat(), "reason": "last_year_was_holiday"})
                continue
        pairs.append((d, ly, h.name if h else None))
    if not pairs:
        out["skipped"] = skipped
        return out
    lo, hi = min(p[1] for p in pairs), max(p[1] for p in pairs)

    def measure(scope):
        ly_pax = _ing_pax_by_day(con, scope, lo.isoformat(), hi.isoformat())
        base = _ing_pax_by_day(con, scope, base_s.isoformat(), base_e.isoformat())
        if len(base) < _SEASON_BASELINE_DAYS * _SEASON_MIN_COVER:
            return None
        by_wd: Dict[int, list] = {}
        for ds, p in base.items():
            bd = date.fromisoformat(ds)
            if not TH.is_holiday(bd):
                by_wd.setdefault(bd.weekday(), []).append(p)
        actual = expected = 0.0
        used = []
        for d, ly, name in pairs:
            p, wd = ly_pax.get(ly.isoformat()), by_wd.get(ly.weekday())
            if not p or not wd:
                continue
            actual += p
            expected += sum(wd) / len(wd)
            used.append((d, ly, name))
        if len(used) < max(1, want_t * _SEASON_MIN_COVER) or actual <= 0 or expected <= 0:
            return None
        return actual, expected, used

    m = measure(branch) if branch else None
    if m:
        out["scope"] = branch
    else:
        # a branch that did not exist last year falls back to the chain shape
        m = measure(None)
        out["scope"] = "chain" if m else (branch or "chain")
    if not m:
        out["skipped"] = skipped
        return out
    actual, expected, used = m
    n = len(used)
    raw = actual / expected
    out.update({"factor": round(max(_SEASON_CLAMP[0], min(raw, _SEASON_CLAMP[1])), 3),
                "raw_factor": round(raw, 3), "available": True,
                "ly_pax_per_day": round(actual / n, 1), "baseline_pax_per_day": round(expected / n, 1),
                "days_compared": n, "skipped": skipped,
                "holidays": [{"date": d.isoformat(), "ly_date": ly.isoformat(), "name": name}
                             for d, ly, name in used if name],
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
            "reason": (f"{label}อ้างอิงยอดลูกค้าวันเดียวกันของปีก่อน "
                       f"({s['ly_from']}–{s['ly_to']} เฉลี่ย {s['ly_pax_per_day']:,.0f} คน/วัน "
                       f"เทียบวันปกติในวันเดียวกันของสัปดาห์ {s['baseline_pax_per_day']:,.0f} คน/วัน)"),
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
    rj, rc = _code_remap_sql(con)
    rows = con.execute(f"""
        SELECT COALESCE(NULLIF({rc},''), '__' || bi.ingredient_name) AS code,
               bi.unit AS unit,
               SUM(d.qty * bi.qty_per_serving) AS qty
          FROM v_sales_sku_daily d
          JOIN branches b         ON b.branch_id = d.branch_id
          JOIN {_ING_BOM_MAP} m   ON m.sale_sku  = d.sku_id
          JOIN bom_ingredients bi ON bi.sku_id   = m.bom_sku
          {rj}
         WHERE b.name = :branch AND d.day BETWEEN :start AND :end
         GROUP BY 1, 2
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


# ═══════════════════════════════════════════════════════════════════════════
# Excel round-trips — download a template, fill it in, import it back
# ═══════════════════════════════════════════════════════════════════════════
# The numbers ops actually work with live in another system, and the exchange
# format is a spreadsheet. Everything below is one shape: a GET that hands out a
# sheet already carrying the keys (so nobody types a code by hand) plus the blank
# column to fill, and a POST that reads that sheet back.
#
# Import is deliberately tolerant about the file it gets — a sheet that has been
# through another system arrives with renamed headers, a title row above them,
# stray columns and numbers stored as text. Columns are matched by header ALIAS
# (below), the header row is located rather than assumed, and a row that can't be
# read is REPORTED, never silently dropped: a half-imported order round that
# looks complete is worse than a refusal.

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx_attachment(wb, filename: str):
    """A workbook as a download. HTTP headers are latin-1, so a Thai filename
    goes out RFC 5987-encoded with an ASCII fallback (same rule as the exports
    above — a Thai branch name in a bare filename= raises UnicodeEncodeError)."""
    import io as _io
    from urllib.parse import quote as _q
    from fastapi.responses import StreamingResponse as _SR
    buf = _io.BytesIO()
    __import__('xl_safe').guard_workbook(wb)  # PL1 #9: no cell becomes a formula
    wb.save(buf)
    buf.seek(0)
    ascii_name = filename.encode("ascii", "ignore").decode() or "template.xlsx"
    return _SR(buf, media_type=_XLSX_MEDIA,
               headers={"Content-Disposition":
                        f"attachment; filename={ascii_name}; "
                        f"filename*=UTF-8''{_q(filename)}"})


def _wb_new(title: str):
    try:
        from openpyxl import Workbook
    except Exception as e:                                   # pragma: no cover
        raise HTTPException(status_code=503, detail=f"openpyxl unavailable: {e}")
    wb = Workbook()
    wb.active.title = title[:31]
    return wb


def _style_header(ws, row: int, color: str = "0F766E"):
    from openpyxl.styles import Alignment, Font, PatternFill
    for c in ws[row]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=color)
        c.alignment = Alignment(horizontal="center", wrap_text=True)
    # The string form, not ws.cell(...): touching a cell MATERIALISES that row,
    # so a later ws.append() would start one row below it and leave a blank line
    # between the header and the data.
    ws.freeze_panes = f"A{row + 1}"


def _norm_head(s) -> str:
    """A header cell reduced to something comparable: lowercased, stripped of
    spaces and the punctuation a round trip through another system adds."""
    t = str(s or "").strip().lower()
    for ch in " \t\n\r_-.·:()[]/\\*฿%":
        t = t.replace(ch, "")
    return t


def _to_num(v):
    """A cell as a number, or None. Tolerates '1,234.50', '฿1,234', ' 12 ' and
    the text-formatted numbers exports produce."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().replace(",", "").replace("฿", "").replace(" ", "")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _to_day(v) -> Optional[str]:
    """A cell as an ISO date, or None. Accepts a real date/datetime cell and the
    usual typed forms (2026-09-08, 08/09/2026, 8 Sep 2026 is NOT accepted — an
    ambiguous month/day order is refused rather than guessed)."""
    from datetime import date as _date, datetime as _dt
    if isinstance(v, _dt):
        return v.date().isoformat()
    if isinstance(v, _date):
        return v.isoformat()
    t = str(v or "").strip()[:10]
    if not t:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return _dt.strptime(t, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _monday(day: str) -> Optional[str]:
    """The Monday of `day`'s week — the key weekly figures are stored under, so
    the same week entered as Wednesday or as Sunday lands on one row."""
    from datetime import date as _date, timedelta as _td
    try:
        d = _date.fromisoformat(day)
    except (TypeError, ValueError):
        return None
    return (d - _td(days=d.weekday())).isoformat()


async def _read_upload_table(file, aliases: Dict[str, tuple], max_rows: int = 20000,
                             optional: tuple = ()):
    """Read an uploaded .xlsx/.xlsm/.csv into dicts keyed by the names in
    `aliases` ({key: (header alias, ...)}).

    The header row is FOUND, not assumed: exports from other systems put a title,
    a date stamp or a blank line above it. The first row within the first 20 that
    matches at least half the requested keys wins. Unmatched columns are ignored.
    Returns (rows, header_map, sheet_name)."""
    import io as _io
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "ไฟล์ว่าง")
    if len(raw) > 12 * 1024 * 1024:
        raise HTTPException(400, "ไฟล์ใหญ่เกิน 12 MB")
    name = (getattr(file, "filename", "") or "").lower()
    grid: List[list] = []
    sheet = ""
    if name.endswith(".csv") or name.endswith(".txt"):
        import csv as _csv
        for enc in ("utf-8-sig", "utf-8", "cp874", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:                                                # pragma: no cover
            raise HTTPException(400, "อ่านไฟล์ CSV ไม่ได้ (encoding ไม่รองรับ)")
        grid = [row for row in _csv.reader(_io.StringIO(text))][:max_rows + 40]
        sheet = "CSV"
    else:
        try:
            from openpyxl import load_workbook
        except Exception as e:                               # pragma: no cover
            raise HTTPException(503, f"openpyxl unavailable: {e}")
        try:
            wb = load_workbook(_io.BytesIO(raw), data_only=True, read_only=True)
        except Exception as e:
            raise HTTPException(400, f"อ่านไฟล์ Excel ไม่ได้: {e}")
        ws = wb.active
        sheet = ws.title
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i > max_rows + 40:
                break
            grid.append(list(row))
    if not grid:
        raise HTTPException(400, "ไม่พบข้อมูลในไฟล์")

    want = {k: tuple(_norm_head(a) for a in al) for k, al in aliases.items()}
    head_at, header_map = -1, {}
    for i, row in enumerate(grid[:20]):
        cells = [_norm_head(c) for c in row]
        m = {}
        # Exact alias first; then a prefix match, because a header that survived
        # another system usually keeps its name and gains a unit — "ยอดซื้อ
        # วัตถุดิบ (บาท)", "Amount THB". Prefix, not containment: "ยอดขาย" must
        # never satisfy "ยอดซื้อ".
        for exact in (True, False):
            for k, al in want.items():
                if k in m:
                    continue
                for j, c in enumerate(cells):
                    if not c or j in m.values():
                        continue
                    if (c in al) if exact else any(len(a) >= 4 and c.startswith(a) for a in al):
                        m[k] = j
                        break
        # optional columns must not make a header row harder to recognise
        if len([k for k in m if k not in optional]) >= max(1, (len(want) - len(optional) + 1) // 2):
            head_at, header_map = i, m
            break
    if head_at < 0:
        raise HTTPException(400, "ไม่พบหัวตารางที่รู้จักในไฟล์ — กรุณาใช้ template ที่ดาวน์โหลดจากระบบ")

    rows = []
    for row in grid[head_at + 1:head_at + 1 + max_rows]:
        if not any(str(c or "").strip() for c in row):
            continue
        rows.append({k: (row[j] if j < len(row) else None) for k, j in header_map.items()})
    return rows, header_map, sheet


# ── ตั้งค่าการสั่งซื้อ (pack sizes) — template + import ──────────────────────
_ORDER_UNIT_ALIASES = {
    "code":       ("รหัส", "รหัสวัตถุดิบ", "code", "ingredientcode", "sku"),
    "name":       ("วัตถุดิบ", "ชื่อวัตถุดิบ", "name", "ingredientname"),
    "base_unit":  ("หน่วยนับ", "หน่วยฐาน", "baseunit", "unit"),
    "order_unit": ("หน่วยสั่ง", "orderunit", "packunit"),
    "qty_per_order": ("ขนาดบรรจุ", "ปริมาณต่อหน่วยสั่ง", "qtyperorder", "packsize"),
    "price_per_order": ("ราคาต่อหน่วยสั่ง", "ราคาหน่วยสั่ง", "priceperorder", "packprice"),
    "moq":        ("ขั้นต่ำ", "moq", "minorder"),
    "round_step": ("ปัดเป็นทวีคูณของ", "roundstep", "step"),
    "note":       ("หมายเหตุ", "note", "remark"),
}


@router.get("/api/ingredients/order-units.template.xlsx")
def ingredients_order_units_template(only_missing: bool = Query(False)):
    """The pack-size sheet, pre-filled with what is already set so an import is
    an EDIT of the current state and never a silent reset of the rows the
    spreadsheet happened not to carry."""
    data = ingredients_order_units(None)
    rows = [r for r in data["rows"] if not (only_missing and r["pack_known"])]
    wb = _wb_new("ตั้งค่าการสั่งซื้อ")
    ws = wb.active
    ws.append(["ตั้งค่าการสั่งซื้อ (ขนาดบรรจุ) — แก้ไขค่าในไฟล์นี้แล้วนำเข้ากลับ"])
    ws.append(["กรอก 'ขนาดบรรจุ' = กี่หน่วยนับต่อ 1 หน่วยสั่ง (เช่น กล่อง 5 กก. → หน่วยสั่ง=กล่อง, ขนาดบรรจุ=5) "
               "· ห้ามแก้คอลัมน์ 'รหัส' · แถวที่ไม่ต้องการเปลี่ยน ลบทิ้งได้"])
    ws.append(["รหัส", "วัตถุดิบ", "หน่วยนับ", "หน่วยสั่ง", "ขนาดบรรจุ",
               "ราคาต่อหน่วยสั่ง", "ขั้นต่ำ", "ปัดเป็นทวีคูณของ", "หมายเหตุ", "สถานะปัจจุบัน"])
    _style_header(ws, 3)
    for r in rows:
        ws.append([r["code"], r["name"], r["base_unit"], r["order_unit"], r["qty_per_order"],
                   r["price_per_order"], r["moq"], r["round_step"], r["note"],
                   "ตั้งแล้ว" if r["pack_known"] else "ยังไม่ตั้ง (ใช้ค่ามาตรฐาน)"])
    for i, w in enumerate([14, 34, 10, 12, 12, 15, 10, 16, 24, 22], start=1):
        ws.column_dimensions[ws.cell(row=3, column=i).column_letter].width = w
    return _xlsx_attachment(wb, "ORDER_UNITS_TEMPLATE.xlsx")


@router.post("/api/ingredients/order-units/import",
             dependencies=[Depends(require_write_token)])
async def ingredients_order_units_import(file: UploadFile = File(...),
                                         dry_run: bool = Query(False)):
    """Import the filled pack-size sheet. A row with no code, or a pack size that
    is not > 0, is rejected with its row number — qty_per_order divides in the
    ceil(), so a zero would silently become "1 base unit per pack"."""
    rows, hmap, sheet = await _read_upload_table(file, _ORDER_UNIT_ALIASES)
    if "code" not in hmap or "qty_per_order" not in hmap:
        raise HTTPException(400, "ไฟล์ต้องมีคอลัมน์ 'รหัส' และ 'ขนาดบรรจุ'")
    known = {r["code"]: r for r in ingredients_order_units(None)["rows"]}
    ok, errors = [], []
    for i, r in enumerate(rows, start=1):
        code = str(r.get("code") or "").strip()
        if not code:
            continue                       # a blank line under the table, not an error
        qpo = _to_num(r.get("qty_per_order"))
        if code not in known:
            errors.append({"row": i, "code": code, "why": "ไม่พบรหัสนี้ในสูตร BoM"})
            continue
        if qpo is None or qpo <= 0:
            errors.append({"row": i, "code": code, "why": "ขนาดบรรจุต้องเป็นตัวเลขมากกว่า 0"})
            continue
        base = known[code]
        ok.append(OrderUnitRow(
            ingredient_code=code,
            base_unit=str(r.get("base_unit") or base["base_unit"] or "").strip() or base["base_unit"],
            order_unit=str(r.get("order_unit") or base["order_unit"] or "").strip() or base["order_unit"],
            qty_per_order=qpo,
            price_per_order=_to_num(r.get("price_per_order")),
            moq=_to_num(r.get("moq")) or 1,
            round_step=_to_num(r.get("round_step")) or 1,
            note=(str(r.get("note")).strip() if r.get("note") is not None else None)))
    if dry_run:
        return {"ok": True, "dry_run": True, "sheet": sheet,
                "would_save": len(ok), "rows_read": len(rows), "errors": errors}
    if ok:
        ingredients_order_units_save(ok)
    return {"ok": True, "sheet": sheet, "saved": len(ok),
            "rows_read": len(rows), "errors": errors}


# ── ปริมาณที่สั่งจริง (per issued cycle) — template + import ─────────────────
_ACTUAL_ALIASES = {
    "issue_id":  ("รอบสั่งid", "issueid", "รอบที่", "cycleid"),
    "branch":    ("สาขา", "branch", "shop", "store"),
    "code":      ("รหัส", "รหัสวัตถุดิบ", "code", "ingredientcode", "sku"),
    "name":      ("วัตถุดิบ", "ชื่อวัตถุดิบ", "name", "ingredientname"),
    "qty_units": ("สั่งจริงหน่วยสั่ง", "สั่งจริง", "จำนวนที่สั่ง", "ปริมาณสั่ง",
                  "qtyunits", "actual", "orderqty", "qty"),
    "note":      ("หมายเหตุ", "note", "remark"),
}


def _ing_open_issues(con, issue_id: Optional[int], branch: Optional[str],
                     period_start: Optional[str], limit: int = 40):
    f, p = [], {"lim": max(1, min(int(limit), 200))}
    if issue_id:
        f.append("issue_id = :iid"); p["iid"] = int(issue_id)
    if branch:
        f.append("branch = :br"); p["br"] = branch
    if period_start:
        f.append("period_start = :ps"); p["ps"] = period_start
    where = ("WHERE " + " AND ".join(f)) if f else ""
    return con.execute(f"SELECT * FROM ingredient_order_issue {where} "
                       f"ORDER BY period_start DESC, branch LIMIT :lim", p).fetchall()


@router.get("/api/ingredients/order-actual.template.xlsx")
def ingredients_order_actual_template(
    issue_id:     Optional[int] = Query(None, description="one cycle; omit to sweep"),
    branch:       Optional[str] = Query(None),
    period_start: Optional[str] = Query(None),
):
    """The "how much did the branch really order" sheet for issued cycles.

    One row per cycle × ingredient, carrying the frozen recommendation and an
    EMPTY 'สั่งจริง' column. The issue id and code travel with each row, so the
    import needs no matching by name — an ingredient renamed between issuing and
    filing still lands on the right line."""
    with get_db() as con:
        _ing_issue_tables(con)
        issues = _ing_open_issues(con, issue_id, branch, period_start)
        if not issues:
            raise HTTPException(404, "ไม่พบรอบสั่งที่ออกไว้ — กด ออกรอบสั่ง ก่อน")
        lines = {}
        for i in issues:
            lines[i["issue_id"]] = con.execute(
                "SELECT * FROM ingredient_order_issue_line WHERE issue_id=? "
                "ORDER BY rec_base * COALESCE(unit_cost,0) DESC", (i["issue_id"],)).fetchall()
        prev = {}
        for i in issues:
            for a in con.execute("SELECT ingredient_code, qty_units FROM ingredient_order_actual "
                                 "WHERE issue_id=?", (i["issue_id"],)):
                prev[(i["issue_id"], a["ingredient_code"])] = a["qty_units"]
    wb = _wb_new("ปริมาณสั่งจริง")
    ws = wb.active
    ws.append(["บันทึกปริมาณที่สั่งจริง — กรอกคอลัมน์ 'สั่งจริง (หน่วยสั่ง)' แล้วนำเข้ากลับ"])
    ws.append(["ห้ามแก้คอลัมน์ 'รอบสั่ง ID' และ 'รหัส' · เว้นว่าง = ยังไม่บันทึก · 0 = ไม่ได้สั่งรายการนี้"])
    ws.append(["รอบสั่ง ID", "สาขา", "รอบสั่ง", "รหัส", "วัตถุดิบ", "หน่วยสั่ง",
               "แผน (หน่วยสั่ง)", "สั่งจริง (หน่วยสั่ง)", "หมายเหตุ"])
    _style_header(ws, 3)
    for i in issues:
        for l in lines[i["issue_id"]]:
            per = float(l["qty_per_order"] or 1) or 1.0
            rec_units = l["rec_units"] if "rec_units" in l.keys() and l["rec_units"] is not None \
                else round(float(l["rec_base"] or 0) / per, 2)
            ws.append([i["issue_id"], i["branch"], i["period_start"], l["ingredient_code"],
                       l["ingredient_name"], l["order_unit"], rec_units,
                       prev.get((i["issue_id"], l["ingredient_code"])), None])
    for idx, w in enumerate([11, 20, 12, 14, 32, 11, 16, 18, 24], start=1):
        ws.column_dimensions[ws.cell(row=3, column=idx).column_letter].width = w
    tag = f"{issue_id or branch or period_start or 'all'}".replace(" ", "_")
    return _xlsx_attachment(wb, f"ORDER_ACTUAL_TEMPLATE_{tag}.xlsx")


@router.post("/api/ingredients/order-actual/import",
             dependencies=[Depends(require_write_token)])
async def ingredients_order_actual_import(file: UploadFile = File(...),
                                          dry_run: bool = Query(False)):
    """Import the filled 'สั่งจริง' sheet. Rows are grouped by cycle and handed
    to the same writer the on-screen form uses, so the pack size frozen in the
    cycle — not today's — converts units to base."""
    rows, hmap, sheet = await _read_upload_table(file, _ACTUAL_ALIASES)
    if "issue_id" not in hmap or "code" not in hmap:
        raise HTTPException(400, "ไฟล์ต้องมีคอลัมน์ 'รอบสั่ง ID' และ 'รหัส' — ใช้ template จากระบบ")
    by_issue: Dict[int, List[ActualRow]] = {}
    errors, blanks = [], 0
    for i, r in enumerate(rows, start=1):
        code = str(r.get("code") or "").strip()
        iid = _to_num(r.get("issue_id"))
        if not code and iid is None:
            continue
        if iid is None or not code:
            errors.append({"row": i, "why": "ต้องมีทั้ง 'รอบสั่ง ID' และ 'รหัส'"})
            continue
        qty = _to_num(r.get("qty_units"))
        if qty is None:
            blanks += 1                    # not filled in yet — leave it untouched
            continue
        if qty < 0:
            errors.append({"row": i, "code": code, "why": "ปริมาณติดลบ"})
            continue
        by_issue.setdefault(int(iid), []).append(ActualRow(
            ingredient_code=code, qty_units=qty,
            note=(str(r.get("note")).strip() if r.get("note") is not None else None)))
    if dry_run:
        return {"ok": True, "dry_run": True, "sheet": sheet, "rows_read": len(rows),
                "would_save": sum(len(v) for v in by_issue.values()),
                "cycles": sorted(by_issue), "blank": blanks, "errors": errors}
    saved = 0
    for iid, rs in by_issue.items():
        try:
            saved += ingredients_order_actual(ActualBody(issue_id=iid, rows=rs,
                                                         source="excel"))["saved"]
        except HTTPException as e:
            errors.append({"issue_id": iid, "why": str(e.detail)})
    return {"ok": True, "sheet": sheet, "saved": saved, "rows_read": len(rows),
            "cycles": sorted(by_issue), "blank": blanks, "errors": errors}


# ═══════════════════════════════════════════════════════════════════════════
# ยอดซื้อวัตถุดิบรายสาขา (weekly) vs ยอดขายของสาขานั้น
# ═══════════════════════════════════════════════════════════════════════════
# What each branch SPENT on ingredients comes from the purchasing system, not
# from this database — it arrives as a spreadsheet of branch + week + amount.
# Sales for the same week are ours, so the pair gives the one ratio ops actually
# manage to: ฿ spent on ingredients per ฿ of sales.
#
# This is a different measure from the BoM food cost everywhere else in the
# dashboard: BoM is what the recipes SAY the food sold should have cost, this is
# what was BOUGHT. Buying runs ahead of selling (stock-ups, deliveries landing on
# a Friday), so a single week can swing wildly while a 4-week view is stable —
# the endpoint returns both and the tab says which is which.

_ING_PURCHASE_DDL = """
CREATE TABLE IF NOT EXISTS ingredient_purchase_week (
  branch      TEXT NOT NULL,
  week_start  TEXT NOT NULL,          -- Monday of the week (ISO)
  amount      REAL NOT NULL,          -- ฿ purchased, as imported
  note        TEXT,
  source      TEXT,
  updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (branch, week_start)
)"""

_PURCHASE_ALIASES = {
    "branch": ("สาขา", "ชื่อสาขา", "branch", "branchname", "shop", "store", "สาขาที่"),
    "pos_code": ("รหัสสาขา POS", "รหัสสาขา", "branchcode", "poscode", "pos code", "branch code"),
    "week":   ("สัปดาห์", "สัปดาห์เริ่ม", "วันที่", "weekstart", "week", "date", "วันเริ่มสัปดาห์"),
    "amount": ("ยอดซื้อวัตถุดิบ", "ยอดซื้อ", "ยอดรวม", "จำนวนเงิน", "มูลค่า", "amount",
               "total", "purchase", "purchaseamount", "ยอดขายวัตถุดิบ"),
    "note":   ("หมายเหตุ", "note", "remark"),
}


def _ing_branch_names(con) -> List[str]:
    return [r[0] for r in con.execute(
        "SELECT name FROM branches WHERE name <> 'ALL' AND COALESCE(active,1)=1 ORDER BY name")]


POS_CODE_HEADER = "รหัสสาขา POS"      # must NOT start with "สาขา": the import matches headers by prefix


def _ing_branch_pos(con) -> Dict[str, dict]:
    """{our branch name: {'code': '0001', 'pos_id': 5, 'pos_name': ..., 'branch_id': 3}} - the ids
    the sales team / Easy POS use, written by the sync (sync_mssql.write_branch_pos). Empty on a
    DB the sync has not rebuilt yet; a branch the POS knows under two rows keeps both codes."""
    try:
        rows = con.execute(
            "SELECT b.name, p.branch_id, p.pos_branch_id, p.pos_branch_code, p.pos_branch_name "
            "FROM branch_pos p JOIN branches b ON b.branch_id = p.branch_id ORDER BY p.pos_branch_id").fetchall()
    except Exception:
        return {}
    out: Dict[str, dict] = {}
    for name, bid, pid, code, pname in rows:
        code = (code or "").strip()
        o = out.setdefault(name, {"code": code, "pos_id": pid, "pos_name": pname, "branch_id": bid})
        if code and code not in o["code"].split(", "):
            o["code"] = ", ".join(x for x in (o["code"], code) if x)
    return out


def _add_branch_code_sheet(wb, pos: Dict[str, dict], names: List[str]) -> None:
    """A second sheet spelling out which branch is which, in every numbering in use."""
    ws = wb.create_sheet("รหัสสาขา")
    ws.append(["สาขา (ชื่อในระบบนี้)", POS_CODE_HEADER, "POS BranchId", "ชื่อสาขาใน POS", "branch_id (ระบบนี้)"])
    _style_header(ws, 1, "475569")
    for n in names:
        p = pos.get(n) or {}
        ws.append([n, p.get("code"), p.get("pos_id"), p.get("pos_name"), p.get("branch_id")])
    for col, w in zip("ABCDE", (24, 16, 14, 38, 20)):
        ws.column_dimensions[col].width = w
        for cell in ws[col][1:]:
            cell.number_format = "@"                 # "0001" must stay text, not become 1


def _ing_purchases_data(date_from=None, date_to=None, branch=None, group=None) -> dict:
    """The body of /api/ingredients/purchases as a plain function.

    The endpoint itself is @ttl_cache'd, which hands back a pre-serialised
    Response — fine over HTTP, useless to the template builder below, which
    needs the dict. Both call this."""
    from rules import net_revenue_sum
    with get_db() as con:
        con.execute(_ING_PURCHASE_DDL)
        maxd = con.execute("SELECT MAX(day) FROM v_sales_sku_daily").fetchone()[0]
        end = (date_to or maxd or con.execute("SELECT date('now','localtime')").fetchone()[0])[:10]
        start = (date_from or con.execute("SELECT date(:e,'-83 day')", {"e": end}).fetchone()[0])[:10]
        w_start, w_end = _monday(start) or start, _monday(end) or end

        pf, pp = ["week_start BETWEEN :s AND :e"], {"s": w_start, "e": w_end}
        if branch:
            pf.append("branch = :br"); pp["br"] = branch
        buys = con.execute(f"SELECT branch, week_start, amount, note, source, updated_at "
                           f"FROM ingredient_purchase_week WHERE {' AND '.join(pf)}", pp).fetchall()

        sf = ["b.name <> 'ALL'", "date(st.sold_at) BETWEEN :s AND :e"]
        sp: Dict = {"s": start, "e": end}
        if branch:
            sf.append("b.name = :br"); sp["br"] = branch
        _group_filter(sf, sp, group)
        sales = con.execute(f"""
            SELECT b.name AS branch,
                   date(st.sold_at, '-' || ((strftime('%w', st.sold_at) + 6) % 7) || ' day') AS week_start,
                   ROUND({net_revenue_sum('st')}, 2) AS revenue,
                   SUM(COALESCE(st.customer_count,0))  AS covers
              FROM sales_transaction st
              JOIN branches b ON b.branch_id = st.branch_id
             WHERE {' AND '.join(sf)}
             GROUP BY b.name, week_start""", sp).fetchall()
        known = _ing_branch_names(con)

    cells: Dict[tuple, dict] = {}
    for r in sales:
        cells[(r["branch"], r["week_start"])] = {
            "branch": r["branch"], "week_start": r["week_start"],
            "revenue": float(r["revenue"] or 0), "covers": int(r["covers"] or 0),
            "amount": None, "note": "", "source": "", "updated_at": ""}
    for r in buys:
        c = cells.setdefault((r["branch"], r["week_start"]), {
            "branch": r["branch"], "week_start": r["week_start"],
            "revenue": 0.0, "covers": 0, "amount": None, "note": "",
            "source": "", "updated_at": ""})
        c["amount"] = float(r["amount"] or 0)
        c["note"] = r["note"] or ""
        c["source"] = r["source"] or ""
        c["updated_at"] = r["updated_at"] or ""
    rows = []
    for c in cells.values():
        c["pct"] = (round(c["amount"] / c["revenue"] * 100, 1)
                    if c["amount"] is not None and c["revenue"] else None)
        c["per_cover"] = (round(c["amount"] / c["covers"], 2)
                          if c["amount"] is not None and c["covers"] else None)
        c["revenue"] = round(c["revenue"], 2)
        rows.append(c)
    rows.sort(key=lambda r: (r["week_start"], r["branch"]), reverse=True)

    per_branch: Dict[str, dict] = {}
    for c in rows:
        b = per_branch.setdefault(c["branch"], {"branch": c["branch"], "weeks": 0,
                                                "weeks_missing": 0, "amount": 0.0,
                                                "revenue": 0.0, "covers": 0})
        b["weeks"] += 1
        b["revenue"] += c["revenue"]
        b["covers"] += c["covers"]
        if c["amount"] is None:
            b["weeks_missing"] += 1
        else:
            b["amount"] += c["amount"]
    for b in per_branch.values():
        b["amount"] = round(b["amount"], 2)
        b["revenue"] = round(b["revenue"], 2)
        b["pct"] = round(b["amount"] / b["revenue"] * 100, 1) if b["revenue"] else None
        b["per_cover"] = round(b["amount"] / b["covers"], 2) if b["covers"] else None
    branches_out = sorted(per_branch.values(), key=lambda b: -(b["pct"] or 0))

    tot_amt = sum(b["amount"] for b in branches_out)
    tot_rev = sum(b["revenue"] for b in branches_out)
    weeks = sorted({c["week_start"] for c in rows})
    return {
        "available": True,
        "meta": {"date_from": start, "date_to": end,
                 "week_from": w_start, "week_to": w_end,
                 "branch": branch or "", "group": group or "",
                 "weeks": weeks, "branch_names": known,
                 "imported_weeks": len({(c["branch"], c["week_start"]) for c in rows
                                        if c["amount"] is not None}),
                 "missing_weeks": sum(1 for c in rows if c["amount"] is None)},
        "totals": {"amount": round(tot_amt, 2), "revenue": round(tot_rev, 2),
                   "pct": round(tot_amt / tot_rev * 100, 1) if tot_rev else None},
        "rows": rows,
        "branches": branches_out,
    }


@router.get("/api/ingredients/purchases")
@ttl_cache
def ingredients_purchases(date_from: Optional[str] = Query(None),
                          date_to:   Optional[str] = Query(None),
                          branch:    Optional[str] = Query(None),
                          group:     Optional[str] = Query(None)):
    """Imported ingredient spend per branch per week, against that branch's own
    sales for the same week.

    A week appears when EITHER side has something, so a week that was sold but
    never imported shows as a gap instead of quietly vanishing — the missing
    import is the finding."""
    return _ing_purchases_data(date_from, date_to, branch, group)


class PurchaseRow(BaseModel):
    branch: str
    week_start: str
    amount: float
    note: Optional[str] = None


@router.post("/api/ingredients/purchases", dependencies=[Depends(require_write_token)])
def ingredients_purchases_save(rows: List[PurchaseRow], source: str = Query("manual")):
    """Upsert weekly ingredient spend. The week is snapped to its Monday so the
    same week filed as Wednesday and as Sunday is one row, not two."""
    with get_db() as con:
        con.execute(_ING_PURCHASE_DDL)
        known = set(_ing_branch_names(con))
        saved, errors = 0, []
        for i, r in enumerate(rows, start=1):
            br = (r.branch or "").strip()
            wk = _monday(_to_day(r.week_start) or "")
            if br not in known:
                errors.append({"row": i, "branch": br, "why": "ไม่พบสาขานี้"}); continue
            if not wk:
                errors.append({"row": i, "branch": br, "why": "วันที่ไม่ถูกต้อง"}); continue
            if r.amount is None or float(r.amount) < 0:
                errors.append({"row": i, "branch": br, "why": "ยอดต้องไม่ติดลบ"}); continue
            con.execute("""INSERT INTO ingredient_purchase_week
                    (branch, week_start, amount, note, source, updated_at)
                 VALUES (?,?,?,?,?, CURRENT_TIMESTAMP)
                 ON CONFLICT(branch, week_start) DO UPDATE SET
                     amount=excluded.amount, note=excluded.note,
                     source=excluded.source, updated_at=CURRENT_TIMESTAMP""",
                        (br, wk, float(r.amount), r.note, source))
            saved += 1
        con.commit()
    invalidate_cache()
    return {"ok": True, "saved": saved, "errors": errors}


@router.delete("/api/ingredients/purchases", dependencies=[Depends(require_write_token)])
def ingredients_purchases_delete(branch: str = Query(...), week_start: str = Query(...)):
    with get_db() as con:
        con.execute(_ING_PURCHASE_DDL)
        wk = _monday(_to_day(week_start) or "") or week_start
        cur = con.execute("DELETE FROM ingredient_purchase_week WHERE branch=? AND week_start=?",
                          (branch, wk))
        con.commit()
    invalidate_cache()
    return {"ok": True, "deleted": cur.rowcount}


def _week_span(monday: str):
    """(sunday_iso, 'W37 · 07/09–13/09/2026') for the week starting `monday`."""
    from datetime import date as _date, timedelta as _td
    m = _date.fromisoformat(str(monday)[:10])
    s = m + _td(days=6)
    y, wk, _ = m.isocalendar()
    return s.isoformat(), f"W{wk:02d}/{y} · {m:%d/%m}–{s:%d/%m/%Y}"


@router.get("/api/ingredients/purchases.template.xlsx")
def ingredients_purchases_template(weeks: int = Query(8),
                                   date_to: Optional[str] = Query(None),
                                   branch: Optional[str] = Query(None)):
    """A branch × week grid to fill in: one row per branch per week, with that
    week's sales already shown so whoever fills it can sanity-check the amount
    as they type. Anything already imported is pre-filled."""
    n = max(1, min(int(weeks or 8), 52))
    # Called in-process, so every argument goes in positionally — an omitted one
    # would arrive as FastAPI's Query() placeholder object, not as None.
    d = _ing_purchases_data(date_to=date_to, branch=branch)
    have = {(r["branch"], r["week_start"]): r for r in d["rows"]}
    with get_db() as con:
        end_wk = _monday(d["meta"]["date_to"]) or d["meta"]["date_to"]
        wks = [con.execute("SELECT date(:e, :o)", {"e": end_wk, "o": f"-{i * 7} day"}).fetchone()[0]
               for i in range(n)][::-1]
        names = [branch] if branch else _ing_branch_names(con)
        pos = _ing_branch_pos(con)
    wb = _wb_new("ยอดซื้อวัตถุดิบรายสัปดาห์")
    ws = wb.active
    ws.append(["ยอดซื้อวัตถุดิบรายสาขา (รายสัปดาห์) — กรอกคอลัมน์ 'ยอดซื้อวัตถุดิบ (บาท)' แล้วนำเข้ากลับ"])
    ws.append(["1 สัปดาห์ = จันทร์ถึงอาทิตย์ · ดึงยอดซื้อจากอีกระบบตามช่วง 'สัปดาห์เริ่ม' → 'ถึงวันที่' "
               "· 'สัปดาห์ที่' เป็นเลขสัปดาห์แบบ ISO (W1 = สัปดาห์แรกที่มีวันพฤหัสของปี) "
               "· 'ยอดขาย' เป็นตัวเลขของระบบเอง มีไว้ตรวจทาน ไม่ต้องแก้ "
               "· 'รหัสสาขา POS' = รหัสที่ทีมขาย/Easy POS ใช้ (ดูตารางเทียบในแผ่น 'รหัสสาขา')"])
    # The purchasing system is queried by date range, so each row spells out its
    # own Monday→Sunday and ISO week. Only 'สัปดาห์เริ่ม' is read back on import.
    ws.append(["สาขา", POS_CODE_HEADER, "สัปดาห์เริ่ม", "ถึงวันที่", "สัปดาห์ที่",
               "ยอดซื้อวัตถุดิบ (บาท)", "ยอดขาย (ระบบ)", "หมายเหตุ"])
    _style_header(ws, 3, "7C3AED")
    for b in names:
        for w in wks:
            h = have.get((b, w))
            sun, wlabel = _week_span(w)
            ws.append([b, (pos.get(b) or {}).get("code") or None, w, sun, wlabel,
                       (h or {}).get("amount"), (h or {}).get("revenue"), (h or {}).get("note") or None])
            ws.cell(row=ws.max_row, column=2).number_format = "@"      # "0001", not 1
    for i, wdt in enumerate([24, 14, 13, 13, 26, 22, 16, 28], start=1):
        ws.column_dimensions[ws.cell(row=3, column=i).column_letter].width = wdt
    _add_branch_code_sheet(wb, pos, names)
    wb.active = 0                                    # the import reads the active sheet
    return _xlsx_attachment(wb, f"INGREDIENT_PURCHASES_TEMPLATE_{d['meta']['date_to']}.xlsx")


@router.post("/api/ingredients/purchases/import", dependencies=[Depends(require_write_token)])
async def ingredients_purchases_import(file: UploadFile = File(...),
                                       dry_run: bool = Query(False)):
    """Import the weekly spend sheet — the format the purchasing system exports:
    branch, a date in the week, and the total. Nothing else is required."""
    rows, hmap, sheet = await _read_upload_table(file, _PURCHASE_ALIASES, optional=("pos_code",))
    if "branch" not in hmap or "amount" not in hmap:
        raise HTTPException(400, "ไฟล์ต้องมีคอลัมน์ 'สาขา' และ 'ยอดซื้อวัตถุดิบ'")
    with get_db() as con:
        known = set(_ing_branch_names(con))
        by_code = {c: n for n, p in _ing_branch_pos(con).items() for c in p["code"].split(", ") if c}
    ok, errors, blanks = [], [], 0
    for i, r in enumerate(rows, start=1):
        br = str(r.get("branch") or "").strip()
        code = str(r.get("pos_code") or "").strip()
        if code.isdigit() and code not in by_code:       # Excel turned "0001" into 1
            code = next((c for c in by_code if c.isdigit() and int(c) == int(code)), code)
        if br not in known and code in by_code:          # the name was retyped, the code was not
            br = by_code[code]
        if not br:
            continue
        amt = _to_num(r.get("amount"))
        if amt is None:
            blanks += 1                    # not filled in yet
            continue
        day = _to_day(r.get("week")) if "week" in hmap else None
        wk = _monday(day) if day else None
        if br not in known:
            errors.append({"row": i, "branch": br, "why": "ไม่พบสาขานี้ในระบบ"}); continue
        if not wk:
            errors.append({"row": i, "branch": br,
                           "why": "ไม่พบ/อ่านวันที่ของสัปดาห์ไม่ได้ (ใช้รูปแบบ 2026-09-08)"}); continue
        if amt < 0:
            errors.append({"row": i, "branch": br, "why": "ยอดติดลบ"}); continue
        ok.append(PurchaseRow(branch=br, week_start=wk, amount=amt,
                              note=(str(r.get("note")).strip() if r.get("note") is not None else None)))
    if dry_run:
        return {"ok": True, "dry_run": True, "sheet": sheet, "rows_read": len(rows),
                "would_save": len(ok), "blank": blanks, "errors": errors}
    res = ingredients_purchases_save(ok, source="excel") if ok else {"saved": 0, "errors": []}
    return {"ok": True, "sheet": sheet, "saved": res["saved"], "rows_read": len(rows),
            "blank": blanks, "errors": errors + res.get("errors", [])}
