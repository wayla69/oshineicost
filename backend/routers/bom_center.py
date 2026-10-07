# -*- coding: utf-8 -*-
"""BoM Calculation Center API — /api/bom-center/* (docs/BOM_CENTER_PLAN.md, Sprint S1).

S1 surfaces: the ingredient master (M1), the Material Converter (M2) and the
monthly actual-purchase prices (M4). Every write is write-token gated and
recorded in bc_audit; every import is dry-run first (dry_run=true returns what
WOULD change, nothing is written).

Reads need any of cost.analysis / supply.ordering / bom.center / bom.edit;
writes need bom.center or bom.edit (auth.MODULE_RULES).
"""
import io
import json
import re
from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import auth as _auth
from core import get_db, invalidate_cache, require_write_token, require_write_token_unless_dry_run
from bom_center import central as _ce
from bom_center import codes as _cr
from bom_center import hub as _hub
from bom_center.clock import today as bkk_today
from bom_center import master as _m
from bom_center import orders as _or
from bom_center import pilot as _pl
from bom_center import prep as _pr
from bom_center import todo as _td
from bom_center import prices as _p
from bom_center import recipes as _r
from bom_center import release as _rl
from bom_center import stock as _st
from bom_center import variance as _va
from bom_center.schema import ensure as _ensure
from bom_center.units import Converter

router = APIRouter()
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _who(request: Request) -> str:
    try:
        return (_auth.parse_token(request.cookies.get(_auth.SESSION_COOKIE, "")) or {}).get("u") or "-"
    except Exception:
        return "-"


def _month_or_400(m: Optional[str]) -> str:
    if not m or not _MONTH.match(m):
        raise HTTPException(400, "เดือนต้องอยู่ในรูปแบบ YYYY-MM")
    return m


def _this_month() -> str:
    d = bkk_today()
    return f"{d.year:04d}-{d.month:02d}"


def _forced():
    try:
        from .ingredients import _forced_order
        return _forced_order()
    except Exception:
        return None


def _xlsx(wb, filename: str):
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _sheet(ws, title: str, headers: list, note: str):
    from openpyxl.styles import Font, PatternFill
    ws.title = title
    ws.append([note])
    ws["A1"].font = Font(italic=True, color="5F6C80")
    ws.append(headers)
    for c in ws[2]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A6B")
    for i, h in enumerate(headers):
        ws.column_dimensions[chr(65 + i) if i < 26 else "A"].width = max(12, min(40, len(str(h)) + 6))


# ── summary ──────────────────────────────────────────────────────────────────
@router.get("/api/bom-center/summary")
def bc_summary(month: Optional[str] = Query(None)):
    """Health of the master and the prices for `month` (default: this month)."""
    month = month if month and _MONTH.match(month) else _this_month()
    with get_db() as con:
        _ensure(con)
        kinds = dict(con.execute("SELECT kind, COUNT(*) FROM bc_ingredient WHERE active=1 GROUP BY kind"))
        n = sum(kinds.values())
        conv = Converter(con)
        table = _p.month_table(con, month)
        act = [r for r in table if r["active"]]
        no_base = sum(1 for r in act if not r["base_unit"])
        no_pack = con.execute("SELECT COUNT(*) FROM bc_ingredient WHERE active=1 AND kind='raw'"
                              " AND (qty_per_recv IS NULL OR qty_per_recv<=0)").fetchone()[0]
        used = {c for (c,) in con.execute("SELECT DISTINCT ingredient_code FROM bom_ingredients")}
        months = [r[0] for r in con.execute("SELECT DISTINCT month FROM bc_ingredient_price ORDER BY month DESC")]
        last_import = con.execute("SELECT at, who, row_key FROM bc_audit WHERE action='price_import'"
                                  " ORDER BY id DESC LIMIT 1").fetchone()
        missing_in_master = sorted(c for c in used if c and c not in conv.master and c != "#N/A")
    by_src = {}
    for r in act:
        by_src[r["source"] or "none"] = by_src.get(r["source"] or "none", 0) + 1
    return {
        "month": month, "ingredients": n, "kinds": kinds,
        "monitored": sum(1 for r in act if r["monitored"]),
        "priced": sum(1 for r in act if r["price_per_base"] is not None),
        "unpriced": sum(1 for r in act if r["price_per_base"] is None),
        "stale": sum(1 for r in act if (r["age_months"] or 0) > 2),
        "price_sources": by_src, "no_base_unit": no_base, "raw_without_pack": no_pack,
        "recipe_codes_missing": len(missing_in_master), "recipe_codes_missing_list": missing_in_master[:100],
        "price_months": months,
        "last_import": dict(zip(("at", "who", "month"), last_import)) if last_import else None,
    }


# ── code check: old-recipe codes vs the master ──────────────────────────────
_CR_STATUS_TH = {"missing": "ไม่อยู่ในทะเบียน", "conflict": "ชื่อไม่ตรง อาจเป็นสินค้าคนละรายการ",
                 "same": "ตรวจแล้ว: สินค้าเดียวกัน", "remap": "ตรวจแล้ว: ใช้รหัสอื่นแทน", "ok": "ชื่อตรงกัน"}


class CodeDecision(BaseModel):
    code: str
    status: str                      # same | remap | clear
    use_code: Optional[str] = None
    note: Optional[str] = None
    names: Optional[List[str]] = None  # default: the code's names that still need a decision


@router.get("/api/bom-center/code-review")
def bc_code_review(all: bool = Query(False)):
    """Every code the old recipes use: not in the master / name disagrees / checked."""
    with get_db() as con:
        _ensure(con)
        return _cr.review(con, with_ok=all)


@router.post("/api/bom-center/code-review", dependencies=[Depends(require_write_token)])
def bc_code_review_decide(body: CodeDecision, request: Request):
    with get_db() as con:
        _ensure(con)
        out = _write(con, lambda: _cr.decide(con, body.code, body.status, body.use_code, body.note, _who(request),
                                                     body.names))
    invalidate_cache()               # the order plan reads the decision
    return out


@router.get("/api/bom-center/code-review.xlsx")
def bc_code_review_xlsx(all: bool = Query(True)):
    from openpyxl import Workbook
    with get_db() as con:
        _ensure(con)
        d = _cr.review(con, with_ok=all)
    wb = Workbook()
    ws = wb.active
    _sheet(ws, "ตรวจรหัสสูตรเดิม",
           ["รหัส", "ชื่อในสูตรเดิม", "หน่วย", "จำนวนเมนูที่ใช้ชื่อนี้", "ชื่อในทะเบียน", "สถานะของชื่อนี้",
            "รหัสแนะนำ", "ชื่อรหัสแนะนำ", "ใช้รหัสแทน", "หมายเหตุ", "ผู้ตรวจ"],
           "หนึ่งแถวต่อหนึ่งคู่รหัสและชื่อที่สูตรเดิมใช้ สูตรเดิมใช้รหัสเดียวกันกับหลายชื่อ สถานะ 'ชื่อไม่ตรง' หมายถึงชื่อนี้อาจเป็นสินค้าคนละรายการกับรหัสในทะเบียน")
    for r in d["rows"]:
        sg = (r.get("suggest") or [{}])[0]
        for n in r["names"]:
            bad = n["status"] in ("conflict", "missing")
            ws.append([r["code"], n["name"], n["unit"], n["dishes"], r["master_name"],
                       _CR_STATUS_TH.get(n["status"], n["status"]), sg.get("code") if bad else None,
                       sg.get("name") if bad else None, n.get("use_code"), n.get("note"), n.get("who")])
    return _xlsx(wb, "bom_center_code_review.xlsx")


# ── M1 ingredient master ─────────────────────────────────────────────────────
@router.get("/api/bom-center/ingredients")
def bc_ingredients(month: Optional[str] = Query(None), q: Optional[str] = Query(None),
                   kind: Optional[str] = Query(None), issues: bool = Query(False)):
    month = month if month and _MONTH.match(month) else _this_month()
    with get_db() as con:
        _ensure(con)
        price = {r["code"]: r for r in _p.month_table(con, month)}
        rules = {}
        for code, unit, f, label in con.execute("SELECT ingredient_code, unit, factor_to_base, label FROM bc_unit_rule"):
            rules.setdefault(code, []).append({"unit": unit, "factor": f, "label": label})
        aliases = {}
        for a, c, s in con.execute("SELECT alias_code, ingredient_code, system FROM bc_ingredient_alias"):
            aliases.setdefault(c, []).append({"alias": a, "system": s})
        used = dict(con.execute("SELECT ingredient_code, COUNT(DISTINCT sku_id) FROM bom_ingredients"
                                " GROUP BY ingredient_code"))
        cols = ["code", "name_th", "name_en", "family", "kind", "category", "supplier", "base_unit", "recv_unit",
                "qty_per_recv", "loss_pct", "moq", "lead_days", "shelf_days", "monitored", "sourcing_default",
                "active", "note", "updated_at", "updated_by"]
        rows = [dict(zip(cols, r)) for r in con.execute(f"SELECT {', '.join(cols)} FROM bc_ingredient ORDER BY code")]
    ql = (q or "").strip().lower()
    out = []
    for r in rows:
        if kind and r["kind"] != kind:
            continue
        if ql and ql not in f"{r['code']} {r['name_th'] or ''} {r['name_en'] or ''}".lower():
            continue
        p = price.get(r["code"]) or {}
        r.update({"price_per_base": p.get("price_per_base"), "price_source": p.get("source"),
                  "priced_month": p.get("priced_month"), "price_age_months": p.get("age_months"),
                  "unit_rules": rules.get(r["code"], []), "aliases": aliases.get(r["code"], []),
                  "used_in_menus": used.get(r["code"], 0)})
        r["price_per_recv"] = (round(r["price_per_base"] * r["qty_per_recv"], 2)
                               if r["price_per_base"] and r["qty_per_recv"] else None)
        problems = []
        if not r["base_unit"]:
            problems.append("ไม่มีหน่วยฐาน")
        if r["kind"] == "raw" and not r["qty_per_recv"]:
            problems.append("ไม่มีขนาดบรรจุ")
        if r["price_per_base"] is None and r["kind"] != "zero_by_design":
            problems.append("ไม่มีราคา")
        elif (r["price_age_months"] or 0) > 2:
            problems.append(f"ราคาเก่า {r['price_age_months']} เดือน")
        r["problems"] = problems
        if issues and not problems:
            continue
        out.append(r)
    out.sort(key=lambda r: (-(r["used_in_menus"] or 0), r["code"]))
    return {"month": month, "rows": out}


class IngredientEdit(BaseModel):
    code: str
    name_th: Optional[str] = None
    name_en: Optional[str] = None
    kind: Optional[str] = None
    category: Optional[str] = None
    supplier: Optional[str] = None
    base_unit: Optional[str] = None
    recv_unit: Optional[str] = None
    qty_per_recv: Optional[float] = None
    loss_pct: Optional[float] = None
    moq: Optional[float] = None
    lead_days: Optional[float] = None
    shelf_days: Optional[float] = None
    monitored: Optional[int] = None
    sourcing_default: Optional[str] = None
    active: Optional[int] = None
    note: Optional[str] = None


@router.post("/api/bom-center/ingredients", dependencies=[Depends(require_write_token)])
def bc_ingredients_save(rows: List[IngredientEdit], request: Request):
    if not rows:
        raise HTTPException(400, "ไม่มีรายการ")
    for r in rows:
        if r.kind and r.kind not in ("raw", "prepared", "consumable", "zero_by_design"):
            raise HTTPException(400, f"{r.code}: ชนิดไม่ถูกต้อง")
        if r.sourcing_default and r.sourcing_default not in ("central", "local"):
            raise HTTPException(400, f"{r.code}: ช่องทางสั่งไม่ถูกต้อง")
    with get_db() as con:
        _ensure(con)
        try:
            stats = _m.update_rows(con, [r.dict(exclude_unset=True) for r in rows], _who(request))
            con.commit()
        except ValueError as e:
            con.rollback()
            raise HTTPException(400, str(e))
        except Exception:
            con.rollback()
            raise
    invalidate_cache("bc_")
    return {"ok": True, **stats}


@router.post("/api/bom-center/ingredients/seed", dependencies=[Depends(require_write_token_unless_dry_run)])
def bc_ingredients_seed(request: Request, dry_run: bool = Query(True)):
    """Build the master from pack sizes, recipe codes and the warehouse list.
    Inserts and fills blanks only; never overwrites what a person set."""
    with get_db() as con:
        _ensure(con)
        before = con.execute("SELECT COUNT(*) FROM bc_ingredient").fetchone()[0]
        try:
            stats = _m.seed_from_db(con, forced=_forced(), who=_m.SEED)
            after = con.execute("SELECT COUNT(*) FROM bc_ingredient").fetchone()[0]
            if dry_run:
                con.rollback()
            else:
                _m.audit(con, _who(request), "seed", "bc_ingredient", "-", before, after)
                con.commit()
        except Exception:
            con.rollback()
            raise
    if not dry_run:
        invalidate_cache("bc_")
    return {"dry_run": dry_run, "before": before, "after": after, **stats}


@router.get("/api/bom-center/ingredients/template.xlsx")
def bc_ingredients_template():
    """The whole master as a sheet: edit in Excel, import back (a row you delete is left alone)."""
    from openpyxl import Workbook
    heads = ["รหัส", "ชื่อวัตถุดิบ", "ชนิด", "หมวด", "ผู้ขาย", "หน่วยฐาน", "หน่วยรับ", "ขนาดบรรจุ",
             "สูญเสีย (%)", "ขั้นต่ำ", "Lead time", "อายุเก็บ (วัน)", "บังคับสั่ง", "ช่องทางสั่ง"]
    kind_th = {"raw": "ดิบ", "prepared": "กึ่งสำเร็จ", "consumable": "วัสดุสิ้นเปลือง", "zero_by_design": "ตั้งใจเป็น0"}
    wb = Workbook()
    _sheet(wb.active, "ทะเบียนวัตถุดิบ", heads,
           "ห้ามแก้ไขคอลัมน์รหัส · ขนาดบรรจุ = จำนวนหน่วยฐานต่อ 1 หน่วยรับ (เช่น แซลมอน 1 กก. = 1000 กรัม) · "
           "ชนิด: ดิบ / กึ่งสำเร็จ / วัสดุสิ้นเปลือง / ตั้งใจเป็น0 · ช่องทางสั่ง: ส่วนกลาง / สาขาซื้อเอง")
    ws = wb.active
    with get_db() as con:
        _ensure(con)
        for r in con.execute("SELECT code, name_th, kind, category, supplier, base_unit, recv_unit, qty_per_recv,"
                             " loss_pct, moq, lead_days, shelf_days, monitored, sourcing_default"
                             " FROM bc_ingredient WHERE active=1 ORDER BY code"):
            r = list(r)
            r[2] = kind_th.get(r[2], r[2])
            r[12] = "ใช่" if r[12] else ""
            r[13] = "สาขาซื้อเอง" if r[13] == "local" else "ส่วนกลาง"
            ws.append(r)
    return _xlsx(wb, f"BOM_CENTER_INGREDIENTS_{date.today().isoformat()}.xlsx")


async def _read_master_file(file: UploadFile):
    """The center's template, or a DATAP sheet (also inside the 80 MB Cost-RW workbook)."""
    from .ingredients import _norm_head
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "ไฟล์ไม่มีข้อมูล")
    if len(raw) > 120 * 1024 * 1024:
        raise HTTPException(400, "ไฟล์มีขนาดเกิน 120 MB")
    name = (file.filename or "").lower()
    if name.endswith(".csv"):
        import csv
        text = raw.decode("utf-8-sig", errors="replace")
        grid = list(csv.reader(io.StringIO(text)))
        sheet = "CSV"
    else:
        from openpyxl import load_workbook
        try:
            wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        except Exception as e:
            raise HTTPException(400, f"อ่านไฟล์ Excel ไม่ได้: {e}")
        sheet = "DATAP" if "DATAP" in wb.sheetnames else wb.sheetnames[0]
        grid = [list(r) for r in wb[sheet].iter_rows(values_only=True)]
    want = {k: tuple(_norm_head(a) for a in al) for k, al in _m.IMPORT_ALIASES.items()}
    head_at, hmap = -1, {}
    for i, row in enumerate(grid[:20]):
        cells = [_norm_head(c) for c in row]
        m = {}
        for k, al in want.items():
            for j, c in enumerate(cells):
                if c and c in al and j not in m.values():
                    m[k] = j
                    break
        if "code" in m and len(m) >= 3:
            head_at, hmap = i, m
            break
    if head_at < 0:
        raise HTTPException(400, "ไม่พบหัวตาราง (ต้องมีคอลัมน์รหัส) กรุณาใช้ template ของระบบ หรือ sheet DATAP")
    rows = []
    for row in grid[head_at + 1:]:
        if not any(str(c or "").strip() for c in row):
            continue
        rows.append({k: (row[j] if j < len(row) else None) for k, j in hmap.items()})
    return rows, sheet, sorted(hmap)


@router.post("/api/bom-center/ingredients/import", dependencies=[Depends(require_write_token_unless_dry_run)])
async def bc_ingredients_import(request: Request, file: UploadFile = File(...), dry_run: bool = Query(True),
                                month: Optional[str] = Query(None)):
    """Import the master template or a DATAP sheet. Values in the file overwrite."""
    rows, sheet, cols = await _read_master_file(file)
    with get_db() as con:
        _ensure(con)
        try:
            stats = _m.import_rows(con, rows, _who(request),
                                   default_month=month if month and _MONTH.match(month) else None)
            if dry_run:
                con.rollback()
            else:
                _m.audit(con, _who(request), "import", "bc_ingredient", file.filename,
                         None, f"{stats['inserted']} new / {stats['updated']} updated")
                con.commit()
        except Exception:
            con.rollback()
            raise
    if not dry_run:
        invalidate_cache("bc_")
    return {"dry_run": dry_run, "sheet": sheet, "columns": cols, "rows": len(rows),
            **{k: v for k, v in stats.items() if k != "skipped"}, "skipped": stats["skipped"][:200]}


# ── M2 Material Converter ────────────────────────────────────────────────────
class UnitRule(BaseModel):
    code: str
    unit: str
    factor_to_base: Optional[float] = None       # None deletes the rule
    label: Optional[str] = None


@router.post("/api/bom-center/units", dependencies=[Depends(require_write_token)])
def bc_units_save(rules: List[UnitRule], request: Request):
    with get_db() as con:
        _ensure(con)
        try:
            for r in rules:
                _m.set_unit_rule(con, r.code.strip(), r.unit, r.factor_to_base, r.label, _who(request))
        except ValueError as e:
            con.rollback()
            raise HTTPException(400, str(e))
        con.commit()
    invalidate_cache("bc_")
    return {"ok": True, "rules": len(rules)}


@router.get("/api/bom-center/convert")
def bc_convert(code: str = Query(...), qty: float = Query(1.0), unit: str = Query(...),
               month: Optional[str] = Query(None)):
    """'3 แท่ง of 13-01-009' -> base units, and what it costs at `month`'s price."""
    month = month if month and _MONTH.match(month) else _this_month()
    with get_db() as con:
        _ensure(con)
        conv = Converter(con)
        qb, how = conv.to_base(code, qty, unit)
        p = _p.price_for(con, code, month)
    return {"code": code, "qty": qty, "unit": unit, "qty_base": qb, "base_unit": conv.base_unit(code),
            "how": how, "price_per_base": p[0] if p else None,
            "cost": round(qb * p[0], 4) if (qb is not None and p) else None, "month": month}


# ── M4 monthly prices ────────────────────────────────────────────────────────
@router.get("/api/bom-center/prices")
def bc_prices(month: Optional[str] = Query(None)):
    month = month if month and _MONTH.match(month) else _this_month()
    with get_db() as con:
        _ensure(con)
        return {"month": month, "rows": _p.month_table(con, month)}


@router.get("/api/bom-center/prices/history")
def bc_price_history(code: str = Query(...)):
    with get_db() as con:
        _ensure(con)
        names = dict(con.execute("SELECT branch_id, name FROM branches"))
        h = _p.history(con, code)
    for r in h:
        for b in r["branches"]:
            b["branch"] = names.get(b["branch_id"], str(b["branch_id"]))
    return {"code": code, "rows": h}


_PRICE_ALIASES = {
    "code":       ("รหัส", "รหัสสินค้า", "รหัสวัตถุดิบ", "itemcode", "code"),
    "name":       ("รายละเอียด", "ชื่อสินค้า", "ชื่อวัตถุดิบ", "name", "description"),
    "qty":        ("จำนวน", "ปริมาณ", "qty", "quantity"),
    "unit":       ("หน่วยนับ", "หน่วย", "unit", "uom"),
    "unit_price": ("ราคาต่อหน่วย", "ราคา/หน่วย", "unitprice", "price"),
    "value":      ("มูลค่ารวม", "มูลค่า", "จำนวนเงิน", "value", "amount", "total"),
    "branch":     ("สาขา", "branch", "คลัง"),
    "month":      ("เดือน", "month", "วันที่", "date"),
}


@router.get("/api/bom-center/prices/template.xlsx")
def bc_prices_template():
    from openpyxl import Workbook
    wb = Workbook()
    _sheet(wb.active, "ยอดซื้อรายบรรทัด", ["รหัส", "ชื่อสินค้า", "จำนวน", "หน่วย", "มูลค่ารวม", "สาขา", "เดือน"],
           "1 แถว = 1 บรรทัดซื้อ/รับเข้า · มูลค่ารวมไม่รวม VAT · หน่วย: กิโลกรัม / กรัม / แพ็ค / ลิตร … (ระบบแปลงหน่วยให้) · "
           "เดือน YYYY-MM · หรืออัปโหลดไฟล์ 'รายงานจ่ายสินค้าภายใน' ของฝ่ายบัญชีได้โดยตรง")
    wb.active.append(["02-01-001", "ปลาแซลมอนเทร้านอร์เวย์ (ตัวอย่าง กรุณาลบแถวนี้)", 785.46, "กิโลกรัม", 294332.5,
                      "ขอนแก่น", "2026-09"])
    return _xlsx(wb, "BOM_CENTER_PURCHASE_LINES_TEMPLATE.xlsx")


async def _price_lines_from_file(file: UploadFile, month: Optional[str]):
    """-> (lines, info, month). Accounting issue workbook (sheets 'Use XXX') or the template."""
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "ไฟล์ไม่มีข้อมูล")
    name = (file.filename or "").lower()
    if not name.endswith(".csv"):
        from openpyxl import load_workbook
        try:
            sheets = load_workbook(io.BytesIO(raw), read_only=True).sheetnames
        except Exception as e:
            raise HTTPException(400, f"อ่านไฟล์ Excel ไม่ได้: {e}")
        if any(s.replace("-", " ").upper().startswith("USE ") for s in sheets):
            from .requisition import parse_workbook
            lines, info = parse_workbook(raw)
            months = sorted({ln["period_month"] for ln in lines})
            if not lines:
                raise HTTPException(400, "ไม่พบรายการในรายงานจ่ายสินค้าภายใน")
            if month and month not in months:
                raise HTTPException(400, f"ไฟล์นี้เป็นเดือน {', '.join(months)} ไม่ใช่ {month}")
            month = month or months[-1]
            lines = [{"code": ln["item_code"], "name": ln["item_name"], "qty": ln["qty"], "unit": ln["unit"],
                      "value": ln["value"], "unit_price": ln["unit_price"], "branch_id": ln["branch_id"]}
                     for ln in lines if ln["period_month"] == month]
            return lines, {"format": "รายงานจ่ายสินค้าภายใน", **info}, month

    class _F:                                       # re-wrap the bytes for the shared reader
        filename = file.filename

        async def read(self):
            return raw
    from .ingredients import _read_upload_table
    rows, hmap, sheet = await _read_upload_table(_F(), _PRICE_ALIASES,
                                                 optional=("name", "unit_price", "value", "branch", "month"))
    if "value" not in hmap and "unit_price" not in hmap:
        raise HTTPException(400, "ต้องมีคอลัมน์ มูลค่ารวม หรือ ราคาต่อหน่วย")
    with get_db() as con:
        bmap = {str(n).strip(): b for b, n in con.execute("SELECT branch_id, name FROM branches")}
    lines, months = [], set()
    for r in rows:
        m = r.get("month")
        mm = _m._month_of(m) if m not in (None, "") else None
        if mm:
            months.add(mm)
        if month and mm and mm != month:
            continue
        b = r.get("branch")
        bid = bmap.get(str(b).strip()) if b not in (None, "") else None
        if bid is None and b not in (None, ""):
            try:
                bid = int(float(b))
            except (TypeError, ValueError):
                bid = None
        lines.append({"code": r.get("code"), "name": r.get("name"), "qty": r.get("qty"), "unit": r.get("unit"),
                      "value": r.get("value"), "unit_price": r.get("unit_price"), "branch_id": bid})
    if not month:
        if len(months) != 1:
            raise HTTPException(400, "กรุณาระบุเดือนที่จะนำเข้า (ไฟล์มีหลายเดือนหรือไม่มีคอลัมน์เดือน)")
        month = months.pop()
    return lines, {"format": "template", "sheet": sheet, "columns": sorted(hmap)}, month


def _rules_or_400(raw) -> list:
    """Unit rules a price import carries ([{code, unit, factor_to_base}] as JSON): the pack
    guard's suggestions the person ticked, saved in the same confirm as the prices."""
    if not raw:
        return []
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        raise HTTPException(400, "rules ต้องเป็น JSON")
    out = []
    for r in items or []:
        try:
            out.append({"code": str(r["code"]).strip(), "unit": str(r["unit"]).strip(), "factor_to_base": float(r["factor_to_base"])})
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "กฎหน่วยต้องมี code, unit และ factor_to_base")
    return out


def _import(lines, info, month, dry_run, who, note, add_unknown=False, rules=None):
    """Preview, and unless dry_run, write. get_db() hands out a long-lived
    per-thread connection, so a dry run must ROLL BACK explicitly — otherwise the
    codes add_unknown registered would ride along with the next request's commit.
    `rules` are written first, inside the same transaction, so the preview already
    prices the lines they unlock and a dry run discards them with the rest."""
    with get_db() as con:
        _ensure(con)
        try:
            for r in rules or []:
                _m.set_unit_rule(con, r["code"], r["unit"], r["factor_to_base"], "จากการนำเข้าราคา", who)
            agg = _p.aggregate(con, lines, add_unknown=add_unknown, who=who, month=month)
            pv = _p.preview(con, month, agg)
            result = None
            if dry_run:
                con.rollback()
            else:
                if not agg["codes"]:
                    raise HTTPException(400, "ไม่มีรายการที่ใช้ได้ กรุณาตรวจสอบรายการที่แปลงหน่วยไม่ได้ด้านล่าง")
                result = _p.apply(con, month, agg, who, note)
                con.commit()
        except Exception:
            con.rollback()
            raise
    if not dry_run:
        invalidate_cache("bc_")
    pv["issues"] = pv["issues"][:300]
    return {"dry_run": dry_run, "source": info, "lines": len(lines), **pv, "applied": result,
            "rules_applied": len(rules or [])}


class RulesBody(BaseModel):
    rules: List[UnitRule] = []


@router.post("/api/bom-center/prices/import", dependencies=[Depends(require_write_token_unless_dry_run)])
async def bc_prices_import(request: Request, file: UploadFile = File(...), month: Optional[str] = Query(None),
                           dry_run: bool = Query(True), add_unknown: bool = Query(False),
                           rules: Optional[str] = Form(None)):
    """A month of purchase lines -> weighted-average actual price per ingredient.
    `rules` (a JSON list in the form) are the pack guard's suggestions the person ticked."""
    if month:
        _month_or_400(month)
    rl = _rules_or_400(rules)
    lines, info, month = await _price_lines_from_file(file, month)
    return _import(lines, info, month, dry_run, _who(request), f"ไฟล์ {file.filename}", add_unknown, rules=rl)


@router.post("/api/bom-center/prices/from-requisition", dependencies=[Depends(require_write_token_unless_dry_run)])
def bc_prices_from_requisition(request: Request, month: str = Query(...), dry_run: bool = Query(True),
                               add_unknown: bool = Query(False), body: Optional[RulesBody] = None):
    """The same, from the รายงานจ่ายสินค้าภายใน rows already uploaded to the site."""
    _month_or_400(month)
    rl = [{"code": r.code, "unit": r.unit, "factor_to_base": float(r.factor_to_base or 0)} for r in (body.rules if body else [])
          if r.factor_to_base]
    with get_db() as con:
        has = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='requisition_line'").fetchone()
        rows = con.execute("SELECT item_code, item_name, qty, unit, value, unit_price, branch_id FROM requisition_line"
                           " WHERE period_month=?", (month,)).fetchall() if has else []
    if not rows:
        raise HTTPException(404, f"ยังไม่มีรายงานจ่ายสินค้าภายในเดือน {month} ในระบบ")
    lines = [dict(zip(("code", "name", "qty", "unit", "value", "unit_price", "branch_id"), r)) for r in rows]
    return _import(lines, {"format": "requisition_line ในระบบ"}, month, dry_run, _who(request),
                   "จากรายงานจ่ายสินค้าภายในที่อัปโหลดไว้", add_unknown, rules=rl)


@router.get("/api/bom-center/prices/requisition-months")
def bc_requisition_months():
    with get_db() as con:
        has = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='requisition_line'").fetchone()
        return {"months": [r[0] for r in con.execute(
            "SELECT DISTINCT period_month FROM requisition_line ORDER BY 1 DESC")] if has else []}


class ManualPrice(BaseModel):
    code: str
    month: str
    price_per_base: Optional[float] = None       # None withdraws the manual price
    note: Optional[str] = ""


@router.post("/api/bom-center/prices/manual", dependencies=[Depends(require_write_token)])
def bc_price_manual(body: ManualPrice, request: Request):
    _month_or_400(body.month)
    with get_db() as con:
        _ensure(con)
        if not con.execute("SELECT 1 FROM bc_ingredient WHERE code=?", (body.code,)).fetchone():
            raise HTTPException(404, "ไม่มีรหัสนี้ในทะเบียนวัตถุดิบ")
        try:
            _p.set_manual(con, body.code, body.month, body.price_per_base, body.note or "", _who(request))
        except ValueError as e:
            raise HTTPException(400, str(e))
        con.commit()
    invalidate_cache("bc_")
    return {"ok": True}


@router.post("/api/bom-center/prices/carry-forward", dependencies=[Depends(require_write_token)])
def bc_price_carry(request: Request, month: str = Query(...)):
    """Fill a month nobody imported yet with each ingredient's latest price."""
    _month_or_400(month)
    with get_db() as con:
        _ensure(con)
        n = _p.carry_forward(con, month, _who(request))
        con.commit()
    invalidate_cache("bc_")
    return {"ok": True, "month": month, "carried_forward": n}


# ── M3 recipes by tier (Sprint S2) ───────────────────────────────────────────
def _next_month(m: str) -> str:
    y, mo = int(m[:4]), int(m[5:7])
    return f"{y + (mo == 12):04d}-{1 if mo == 12 else mo + 1:02d}"


@router.get("/api/bom-center/releases")
def bc_releases():
    with get_db() as con:
        _ensure(con)
        rows = con.execute("SELECT release_id, label, kind, effective_month, status, scope, price_month, created_by,"
                           " created_at, released_by, released_at, acknowledged_by, acknowledged_at,"
                           " (SELECT COUNT(*) FROM bc_recipe r WHERE r.release_id=b.release_id),"
                           " (SELECT COUNT(*) FROM bc_release_check c WHERE c.release_id=b.release_id"
                           "   AND c.check_code='unmatched')"
                           " FROM bc_release b ORDER BY (kind='baseline'), effective_month DESC, release_id DESC").fetchall()
        priced = [r[0] for r in con.execute("SELECT DISTINCT month FROM bc_ingredient_price ORDER BY month DESC")]
        # costs follow ACTUAL purchase prices (owner decision): suggest the latest month the accounts
        # were imported for — not a later supplier quote's month (DATAP is dated 2026-07, actuals June)
        actual = con.execute("SELECT MAX(month) FROM bc_ingredient_price"
                             " WHERE source IN ('purchase_avg','manual')").fetchone()[0]
    cols = ("release_id", "label", "kind", "effective_month", "status", "scope", "price_month", "created_by",
            "created_at", "released_by", "released_at", "acknowledged_by", "acknowledged_at", "recipes", "unmatched")
    rels = [dict(zip(cols, r)) for r in rows]
    for r in rels:               # what the version waits for (หน้างาน and the pane read it)
        r["next_step"] = ("release" if r["kind"] == "monthly" and r["status"] in ("draft", "review")
                          else "acknowledge" if r["status"] == "released" and not r["acknowledged_at"] else None)
    return {"releases": rels, "price_months": priced,
            "suggest": {"effective_month": _next_month(_this_month()),
                        "price_month": actual or (priced[0] if priced else None)}}


@router.get("/api/bom-center/recipes")
def bc_recipes(release_id: int = Query(...), tier: Optional[int] = Query(None)):
    with get_db() as con:
        _ensure(con)
        if not _r.release_row(con, release_id):
            raise HTTPException(404, "ไม่พบ release")
        return _r.tier_view(con, release_id, tier)


@router.get("/api/bom-center/recipes-export.xlsx")
def bc_recipes_export(release_id: int = Query(...)):
    with get_db() as con:
        _ensure(con)
        rel = _r.release_row(con, release_id)
        if not rel:
            raise HTTPException(404, "ไม่พบ release")
        wb = _r.export_flat(con, release_id)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", rel["label"])
    return _xlsx(wb, f"BOM_RECIPES_{safe}.xlsx")


@router.get("/api/bom-center/buffet-skus")
def bc_buffet_skus(release_id: Optional[int] = Query(None)):
    """Buffet POS codes for the mapping picker — those without a recipe in the release first."""
    with get_db() as con:
        _ensure(con)
        have = {c for (c,) in con.execute("SELECT sku_code FROM bc_recipe WHERE release_id=?", (release_id or -1,))}
        tier = dict(con.execute("SELECT s.sku_code, t.tier FROM sku_tier t JOIN skus s USING(sku_id)")) \
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='sku_tier'").fetchone() else {}
        rows = [{"sku_code": c, "name": d, "tier": tier.get(c), "has_recipe": c in have}
                for c, d in con.execute("SELECT sku_code, description FROM skus WHERE sku_code LIKE 'OTHBF%'"
                                        " ORDER BY sku_code")]
    rows.sort(key=lambda r: (r["has_recipe"], r["sku_code"]))
    return {"rows": rows}


@router.get("/api/bom-center/recipes/{sku_code}")
def bc_recipe_detail(sku_code: str, release_id: int = Query(...)):
    with get_db() as con:
        _ensure(con)
        rel = _r.release_row(con, release_id)
        if not rel:
            raise HTTPException(404, "ไม่พบ release")
        out = _r.cost_recipe(con, release_id, sku_code)
        names = dict(con.execute("SELECT code, name_th FROM bc_ingredient"))
        for ln in out["lines"]:
            ln["master_name"] = names.get(ln.get("ingredient_code"))
        d = con.execute("SELECT d.name_th, d.name_en, d.min_tier, ds.serve_unit FROM bc_dish_sku ds"
                        " JOIN bc_dish d USING(dish_id) WHERE ds.sku_code=?", (sku_code,)).fetchone()
        pos = con.execute("SELECT description FROM skus WHERE sku_code=?", (sku_code,)).fetchone()
    out.update(release=rel, dish=dict(zip(("name_th", "name_en", "min_tier", "serve"), d)) if d else None,
               pos_name=pos[0] if pos else None)
    return out


class RecipeLine(BaseModel):
    ingredient_code: Optional[str] = None
    ingredient_name: Optional[str] = None
    qty: float = 0
    unit: Optional[str] = None
    loss_pct: Optional[float] = None
    zero_by_design: Optional[bool] = False
    prep_note: Optional[str] = None


def _write(con, fn):
    """Run a write on the shared per-thread connection: commit, or roll back on any error."""
    try:
        out = fn()
        con.commit()
        return out
    except ValueError as e:
        con.rollback()
        raise HTTPException(400, str(e))
    except Exception:
        con.rollback()
        raise


@router.put("/api/bom-center/recipes/{sku_code}", dependencies=[Depends(require_write_token)])
def bc_recipe_save(sku_code: str, lines: List[RecipeLine], request: Request, release_id: int = Query(...)):
    with get_db() as con:
        _ensure(con)
        _write(con, lambda: _r.save_lines(con, release_id, sku_code, [ln.dict() for ln in lines], _who(request)))
        out = _r.cost_recipe(con, release_id, sku_code)
    invalidate_cache("bc_")
    return {"ok": True, "total": out["total"], "problems": out["problems"]}


@router.post("/api/bom-center/recipes/import-cost-rw", dependencies=[Depends(require_write_token_unless_dry_run)])
async def bc_recipes_import_cost_rw(request: Request, file: UploadFile = File(...), dry_run: bool = Query(True),
                                    label: Optional[str] = Query(None), effective_month: Optional[str] = Query(None),
                                    price_month: Optional[str] = Query(None)):
    """The Cost-RW workbook's BF_<tier>NET sheets -> a buffet draft release."""
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "ไฟล์ไม่มีข้อมูล")
    if len(raw) > 120 * 1024 * 1024:
        raise HTTPException(400, "ไฟล์มีขนาดเกิน 120 MB")
    eff = effective_month or _next_month(_this_month())
    _month_or_400(eff)
    try:
        parsed = _r.parse_cost_rw(raw)
    except Exception as e:
        raise HTTPException(400, f"อ่านไฟล์ไม่ได้: {e}")
    if not parsed["blocks"]:
        raise HTTPException(400, "ไม่พบ sheet สูตรบุฟเฟ่ต์ (BF_<tier>NET…) ในไฟล์นี้")
    who = _who(request)
    with get_db() as con:
        _ensure(con)
        pm = price_month
        if not pm:
            r = con.execute("SELECT MAX(month) FROM bc_ingredient_price WHERE source IN ('purchase_avg','manual')").fetchone()
            pm = r[0] if r and r[0] else con.execute("SELECT MAX(month) FROM bc_ingredient_price").fetchone()[0]
        if not pm:
            raise HTTPException(400, "ยังไม่มีราคาวัตถุดิบในระบบ กรุณานำเข้าทะเบียน/DATAP หรือราคาซื้อจริงก่อน")
        _month_or_400(pm)
        lbl = label or f"{eff} บุฟเฟ่ต์ (draft)"
        try:
            rid = _r.get_or_create_draft(con, lbl, eff, pm, who)
            stats = _r.import_cost_rw(con, parsed, rid, who)
            view = _r.tier_view(con, rid)
            if dry_run:
                con.rollback()
            else:
                con.commit()
        except ValueError as e:
            con.rollback()
            raise HTTPException(400, str(e))
        except Exception:
            con.rollback()
            raise
    if not dry_run:
        invalidate_cache("bc_")
    return {"dry_run": dry_run, "release_id": None if dry_run else rid, "label": lbl, "effective_month": eff,
            "price_month": pm, "sheets": parsed["sheets"], "skipped": parsed["skipped"], **stats,
            "costed": len(view["rows"]), "with_problems": view["problems"], "sheet_mismatch": view["sheet_mismatch"],
            "mismatch_rows": [r for r in view["rows"] if r["sheet_diff"] is not None
                              and abs(r["sheet_diff"]) > 0.01][:60]}


class MapBody(BaseModel):
    release_id: int
    ref: str
    sku_code: str


@router.post("/api/bom-center/recipes/map", dependencies=[Depends(require_write_token)])
def bc_recipes_map(body: MapBody, request: Request):
    with get_db() as con:
        _ensure(con)
        out = _write(con, lambda: _r.apply_mapping(con, body.release_id, body.ref, body.sku_code.strip(),
                                                    _who(request)))
    invalidate_cache("bc_")
    return {"ok": True, **out}


@router.post("/api/bom-center/recipes/import-flat", dependencies=[Depends(require_write_token_unless_dry_run)])
async def bc_recipes_import_flat(request: Request, file: UploadFile = File(...), release_id: int = Query(...),
                                 dry_run: bool = Query(True)):
    raw = await file.read()
    with get_db() as con:
        _ensure(con)
        try:
            out = _r.import_flat(con, raw, release_id, _who(request))
            if dry_run:
                con.rollback()
            else:
                con.commit()
        except ValueError as e:
            con.rollback()
            raise HTTPException(400, str(e))
        except Exception:
            con.rollback()
            raise
    if not dry_run:
        invalidate_cache("bc_")
    return {"dry_run": dry_run, **out}


# ── M5 review & release (Sprint S3) ──────────────────────────────────────────
# Reads under /api/bom-center (cost.analysis …). Each action path carries its own
# permission in auth.MODULE_RULES (longest prefix wins): release = bom.release
# (analyst), acknowledge = bom.audit (management), retire = either, the rest bom.center.
@router.get("/api/bom-center/release-checks")
def bc_release_checks(release_id: int = Query(...), recompute: bool = Query(False)):
    """The checks as saved. recompute=true runs them afresh for a draft and shows the result
    WITHOUT saving it (a look needs no token); accepting or releasing saves them."""
    with get_db() as con:
        _ensure(con)
        rel = _r.release_row(con, release_id)
        if not rel:
            raise HTTPException(404, "ไม่พบ release")
        fresh = False
        if recompute and rel["kind"] == "monthly" and rel["status"] in ("draft", "review"):
            try:
                _rl.run_checks(con, release_id)
                out = _rl.checks_summary(con, release_id)
                fresh = True
            finally:
                con.rollback()
        else:
            out = _rl.checks_summary(con, release_id)
    out["fresh"] = fresh
    out["catalog"] = [{"code": k, "severity": v[0], "title": v[1]} for k, v in _rl.CHECKS.items()]
    return out


@router.get("/api/bom-center/release-compare")
def bc_release_compare(release_id: int = Query(...), month: Optional[str] = Query(None)):
    if month:
        _month_or_400(month)
    with get_db() as con:
        _ensure(con)
        if not _r.release_row(con, release_id):
            raise HTTPException(404, "ไม่พบ release")
        out = _rl.compare(con, release_id, month)
    out["dishes"] = out["dishes"][:300]
    return out


@router.post("/api/bom-center/release-act/check", dependencies=[Depends(require_write_token)])
def bc_release_run_checks(release_id: int = Query(...)):
    with get_db() as con:
        _ensure(con)
        rel = _r.release_row(con, release_id)
        if not rel or rel["kind"] != "monthly":
            raise HTTPException(404, "ไม่พบ release รายเดือน")
        return _write(con, lambda: _rl.run_checks(con, release_id))


class AcceptBody(BaseModel):
    release_id: int
    check_code: str
    ref: str
    reason: str


@router.post("/api/bom-center/release-act/accept", dependencies=[Depends(require_write_token)])
def bc_release_accept(body: AcceptBody, request: Request):
    with get_db() as con:
        _ensure(con)
        _write(con, lambda: _rl.accept(con, body.release_id, body.check_code, body.ref, body.reason, _who(request)))
        return _rl.checks_summary(con, body.release_id)


class ReleaseBody(BaseModel):
    backdate_reason: Optional[str] = None


class AcceptManyBody(BaseModel):
    release_id: int
    check_code: str
    refs: Optional[List[str]] = None       # None = every open row of that code
    reason: str


@router.post("/api/bom-center/release-act/accept-many", dependencies=[Depends(require_write_token)])
def bc_release_accept_many(body: AcceptManyBody, request: Request):
    """One reason for a whole code's open findings (shares /release-act/accept's permission)."""
    with get_db() as con:
        _ensure(con)
        if not _r.release_row(con, body.release_id):
            raise HTTPException(404, "ไม่พบ release")
        out = _write(con, lambda: _rl.accept_many(con, body.release_id, body.check_code, body.refs, body.reason,
                                                  _who(request)))
    invalidate_cache("bc_")
    return out


@router.post("/api/bom-center/release-act/release", dependencies=[Depends(require_write_token)])
def bc_release_do(request: Request, release_id: int = Query(...), body: Optional[ReleaseBody] = None):
    with get_db() as con:
        _ensure(con)
        out = _write(con, lambda: _rl.do_release(con, release_id, _who(request),
                                                  backdate_reason=(body.backdate_reason if body else None)))
    invalidate_cache()          # a release changes COGS from its month on: every cached report is stale
    return out


class NoteBody(BaseModel):
    release_id: int
    note: Optional[str] = ""


@router.post("/api/bom-center/release-act/acknowledge", dependencies=[Depends(require_write_token)])
def bc_release_ack(body: NoteBody, request: Request):
    with get_db() as con:
        _ensure(con)
        _write(con, lambda: _rl.acknowledge(con, body.release_id, _who(request), body.note or ""))
    return {"ok": True}


@router.post("/api/bom-center/release-act/retire", dependencies=[Depends(require_write_token)])
def bc_release_retire(body: NoteBody, request: Request):
    with get_db() as con:
        _ensure(con)
        _write(con, lambda: _rl.retire(con, body.release_id, _who(request), body.note or ""))
    invalidate_cache()          # the previous version's costs are back in every report
    return {"ok": True}


class NewDraftBody(BaseModel):
    from_release_id: int
    effective_month: str
    price_month: str
    label: Optional[str] = None


@router.post("/api/bom-center/release-act/new-draft", dependencies=[Depends(require_write_token)])
def bc_release_new_draft(body: NewDraftBody, request: Request):
    _month_or_400(body.effective_month)
    _month_or_400(body.price_month)
    label = body.label or f"{body.effective_month} บุฟเฟ่ต์ (draft)"
    with get_db() as con:
        _ensure(con)
        rid = _write(con, lambda: _rl.new_draft_from(con, body.from_release_id, label, body.effective_month,
                                                      body.price_month, _who(request)))
    return {"ok": True, "release_id": rid, "label": label}


@router.get("/api/bom-center/release-history")
def bc_release_history(release_id: int = Query(...)):
    """The audit trail of one release: import, edits, acceptances, release, acknowledge, retire."""
    with get_db() as con:
        _ensure(con)
        rows = con.execute("SELECT at, who, action, table_name, row_key, old_value, new_value FROM bc_audit"
                           " WHERE (table_name='bc_release' AND row_key=?)"
                           "    OR (table_name IN ('bc_recipe_line','bc_release_check') AND row_key LIKE ?)"
                           " ORDER BY id DESC LIMIT 300",
                           (str(release_id), f"{release_id}|%")).fetchall()
    cols = ("at", "who", "action", "table", "key", "old", "new")
    return {"rows": [dict(zip(cols, r)) for r in rows]}


# ── M7 Forecast Engine v1 + order calendar (Sprint S4b) ──────────────────────
# The calendar is the SALES team's to fill (owner §10-7) and Sales Support runs the
# suggestions: writes under /api/bom-center/order-calendar and /forecast are open to
# supply.ordering as well as bom.center (auth.MODULE_RULES).
from bom_center import forecast as _fc                                         # noqa: E402


def _parse_day(d: Optional[str]):
    from datetime import date as _d
    if not d:
        return _d.today()
    try:
        return _d.fromisoformat(d)
    except ValueError:
        raise HTTPException(400, "วันที่ต้องอยู่ในรูปแบบ YYYY-MM-DD")


@router.get("/api/bom-center/order-calendar")
def bc_order_calendar():
    with get_db() as con:
        _ensure(con)
        rows = []
        for bid, name in con.execute("SELECT branch_id, name FROM branches WHERE name != 'ALL'"
                                     " AND COALESCE(active, 1) = 1 ORDER BY name"):
            cal = _fc.calendar(con, bid)
            rows.append({"branch_id": bid, "branch": name, **{f"r{k}": v for k, v in cal.items()}})
    return {"rows": rows, "weekdays": _fc.WD_TH, "defaults": _fc.DEFAULT_CAL}


class CalRow(BaseModel):
    branch_id: int
    round: str
    order_weekday: int
    delivery_weekday: Optional[int] = None
    lead_days: Optional[int] = None


@router.post("/api/bom-center/order-calendar", dependencies=[Depends(require_write_token)])
def bc_order_calendar_save(rows: List[CalRow], request: Request):
    from datetime import datetime as _dt, timezone as _tz
    allowed = {"1": (2, 3), "2": (5, 6), "salmon": tuple(range(7))}            # owner: Tue/Wed · Fri/Sat
    who = _who(request)
    now = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%S")

    def go():
        for r in rows:
            if r.round not in allowed:
                raise ValueError(f"ไม่พบรอบ {r.round} ในระบบ")
            if r.order_weekday not in allowed[r.round]:
                raise ValueError(f"รอบ {r.round} สั่งได้เฉพาะวัน {', '.join(_fc.WD_TH[i] for i in allowed[r.round])}")
            if r.lead_days is not None and not (0 <= r.lead_days <= 14):
                raise ValueError("ระยะส่งต้องอยู่ระหว่าง 0–14 วัน")
            con.execute("INSERT OR REPLACE INTO bc_order_calendar(branch_id, round, order_weekday, delivery_weekday,"
                        " lead_days, filled_by, updated_at) VALUES(?,?,?,?,?,?,?)",
                        (r.branch_id, r.round, r.order_weekday, r.delivery_weekday, r.lead_days, who, now))
            _m.audit(con, who, "order_calendar", "bc_order_calendar", f"{r.branch_id}|{r.round}", None,
                     f"{r.order_weekday}/{r.delivery_weekday}/{r.lead_days}")
    with get_db() as con:
        _ensure(con)
        _write(con, go)
    return {"ok": True, "rows": len(rows)}


@router.get("/api/bom-center/forecast")
def bc_forecast(branch_id: int = Query(...), round: str = Query("1"), order_day: Optional[str] = Query(None),
                weeks: int = Query(6, ge=2, le=12), season: str = Query("auto")):
    if round not in ("1", "2", "salmon"):
        raise HTTPException(400, "รอบต้องเป็น 1, 2 หรือ salmon")
    with get_db() as con:
        _ensure(con)
        return _fc.suggest(con, branch_id, round, _parse_day(order_day), weeks=weeks,
                           season="auto" if season == "auto" else "off")


class RunBody(BaseModel):
    branch_id: int
    round: str = "1"
    order_day: Optional[str] = None
    weeks: int = 6
    season: str = "auto"


@router.post("/api/bom-center/forecast/run", dependencies=[Depends(require_write_token)])
def bc_forecast_save(body: RunBody, request: Request):
    """Freeze a suggestion: Phase 2's order screen and compliance judge against it."""
    if body.round not in ("1", "2", "salmon"):
        raise HTTPException(400, "รอบต้องเป็น 1, 2 หรือ salmon")
    with get_db() as con:
        _ensure(con)
        out = _fc.suggest(con, body.branch_id, body.round, _parse_day(body.order_day), weeks=body.weeks,
                          season="auto" if body.season == "auto" else "off")
        rid = _write(con, lambda: _fc.save_run(con, out, _who(request), body.dict()))
    return {"ok": True, "run_id": rid, "lines": len(out["rows"]), "value_total": out["value_total"]}


@router.get("/api/bom-center/forecast/runs")
def bc_forecast_runs(branch_id: Optional[int] = Query(None), limit: int = Query(50, ge=1, le=500)):
    with get_db() as con:
        _ensure(con)
        q = ("SELECT r.run_id, r.branch_id, b.name, r.cycle_date, r.round, r.cover_from, r.cover_to, r.created_at,"
             " r.created_by, (SELECT COUNT(*) FROM bc_forecast_line l WHERE l.run_id = r.run_id),"
             " (SELECT ROUND(SUM(COALESCE(l.suggested_units * l.qty_per_order, l.forecast_base + l.safety_base)"
             "  * l.price_per_base), 2) FROM bc_forecast_line l WHERE l.run_id = r.run_id)"
             " FROM bc_forecast_run r LEFT JOIN branches b USING(branch_id)")
        args = []
        if branch_id:
            q += " WHERE r.branch_id = ?"
            args.append(branch_id)
        rows = con.execute(q + " ORDER BY r.run_id DESC LIMIT ?", args + [limit]).fetchall()
    cols = ("run_id", "branch_id", "branch", "order_day", "round", "cover_from", "cover_to", "created_at",
            "created_by", "lines", "value")
    return {"rows": [dict(zip(cols, r)) for r in rows]}


@router.get("/api/bom-center/forecast/backtest")
def bc_forecast_backtest(branch_id: int = Query(...), weeks: int = Query(4, ge=1, le=8)):
    with get_db() as con:
        _ensure(con)
        return _fc.backtest(con, branch_id, weeks=weeks)


# ── Phase 1.5: sub-recipes of prepared items (prep.py) ──────────────────────
def _prep_month(con, month: Optional[str]) -> str:
    """The price month sub-recipes are costed at: asked, else the latest month that has prices."""
    if month and _MONTH.match(month):
        return month
    r = con.execute("SELECT MAX(month) FROM bc_ingredient_price").fetchone()
    return r[0] if r and r[0] else _this_month()


@router.get("/api/bom-center/prep")
def bc_prep_list(month: Optional[str] = Query(None)):
    """Every prepared item: has a sub-recipe? computed cost vs purchase price."""
    with get_db() as con:
        _ensure(con)
        m = _prep_month(con, month)
        rows = _pr.overview(con, m)
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"month": m, "rows": rows, "counts": counts, "tolerance_pct": _pr.TOLERANCE_PCT}


@router.get("/api/bom-center/prep/template.xlsx")
def bc_prep_template():
    from openpyxl import Workbook
    with get_db() as con:
        _ensure(con)
        rows = _pr.export_flat(con)
    wb = Workbook()
    ws = wb.active
    _sheet(ws, "สูตรกึ่งสำเร็จ", list(_pr.FLAT_COLS),
           "หนึ่งแถวต่อวัตถุดิบ 1 บรรทัด · batch_yield_qty และ batch_unit = ปริมาณที่ได้ต่อ 1 สูตร · การนำเข้าจะแทนที่สูตรของรหัสนั้นทั้งชุด")
    for r in rows:
        ws.append(r)
    return _xlsx(wb, "bom_center_sub_recipes.xlsx")


@router.get("/api/bom-center/prep/{code}")
def bc_prep_detail(code: str, month: Optional[str] = Query(None)):
    with get_db() as con:
        _ensure(con)
        try:
            return _pr.detail(con, code, _prep_month(con, month))
        except ValueError as e:
            raise HTTPException(404, str(e))


class PrepLine(BaseModel):
    ingredient_code: Optional[str] = None
    qty: Optional[float] = None
    unit: Optional[str] = None
    note: Optional[str] = None


class PrepBody(BaseModel):
    yield_qty: float
    yield_unit: str
    lines: List[PrepLine]


@router.put("/api/bom-center/prep/{code}", dependencies=[Depends(require_write_token)])
def bc_prep_save(code: str, body: PrepBody, request: Request):
    with get_db() as con:
        _ensure(con)
        n = _write(con, lambda: _pr.save(con, code, body.yield_qty, body.yield_unit,
                                          [ln.dict() for ln in body.lines], _who(request), source_ref="แก้ไขในหน้าเว็บ"))
    invalidate_cache("bc_")
    return {"ok": True, "lines": n}


@router.post("/api/bom-center/prep/import-semi", dependencies=[Depends(require_write_token_unless_dry_run)])
async def bc_prep_import_semi(request: Request, file: UploadFile = File(...), dry_run: bool = Query(True)):
    """The kitchen's BoM_Semi workbook -> sub-recipes, matched by name + batch size."""
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "ไฟล์ไม่มีข้อมูล")
    if len(raw) > 60 * 1024 * 1024:
        raise HTTPException(400, "ไฟล์มีขนาดเกิน 60 MB")
    try:
        parsed = _pr.parse_bom_semi(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"อ่านไฟล์ไม่ได้: {e}")
    with get_db() as con:
        _ensure(con)
        plan = _pr.plan_import(con, parsed)
        if dry_run:
            return {"dry_run": True, **plan}
        out = _write(con, lambda: _pr.apply_import(con, plan, _who(request)))
    invalidate_cache("bc_")
    return {"dry_run": False, **plan, **out}


class PrepMapBody(BaseModel):
    src_name: str
    prepared_code: str               # a master code, or '-' = this sheet row is not one of ours


@router.post("/api/bom-center/prep/map", dependencies=[Depends(require_write_token)])
def bc_prep_map(body: PrepMapBody, request: Request):
    with get_db() as con:
        _ensure(con)
        _write(con, lambda: _pr.set_map(con, body.src_name, body.prepared_code, _who(request)))
    return {"ok": True}


@router.post("/api/bom-center/prep/import-flat", dependencies=[Depends(require_write_token_unless_dry_run)])
async def bc_prep_import_flat(request: Request, file: UploadFile = File(...), dry_run: bool = Query(True)):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "ไฟล์ไม่มีข้อมูล")
    with get_db() as con:
        _ensure(con)
        try:
            out = _pr.import_flat(con, raw, _who(request), dry_run=dry_run)
        except ValueError as e:
            con.rollback()
            raise HTTPException(400, str(e))
        if not dry_run and not out["errors"]:
            con.commit()
    invalidate_cache("bc_")
    return out


# ── Phase 2 · S6: branch stock counts (stock.py) ────────────────────────────
# ── หน้างาน: the to-do list and the month-close checklist (todo.py) ─────────
@router.get("/api/bom-center/todo")
def bc_todo(request: Request, month: Optional[str] = Query(None), branch_id: Optional[int] = Query(None)):
    """What each person has to do now, and where `month` stands on its way to closing.
    A branch login gets its own branch only; the centre may name one or see them all."""
    if month:
        _month_or_400(month)
    with get_db() as con:
        _ensure(con)
        try:
            return _td.build(con, month, branch_id, _auth.scope_branch(request))
        except PermissionError as e:
            raise HTTPException(403, str(e))
        except ValueError as e:
            raise HTTPException(404, str(e))


def _stock_branch(con, request: Request, branch_id: Optional[int]) -> int:
    """The branch a call works on. A branch login (its account carries a branch) is
    pinned to it — asking for another branch is refused; the centre must name one."""
    scoped = _auth.scope_branch(request)
    if scoped:
        r = con.execute("SELECT branch_id FROM branches WHERE name=?", (scoped,)).fetchone()
        if not r:
            raise HTTPException(403, "บัญชีนี้ผูกกับสาขาที่ไม่พบในระบบ กรุณาแจ้งผู้ดูแลระบบ")
        if branch_id is not None and int(branch_id) != int(r[0]):
            raise HTTPException(403, "บัญชีสาขาเข้าถึงได้เฉพาะสาขาของตัวเอง")
        return int(r[0])
    if branch_id is None:
        raise HTTPException(400, "กรุณาเลือกสาขา")
    if not con.execute("SELECT 1 FROM branches WHERE branch_id=?", (branch_id,)).fetchone():
        raise HTTPException(404, "ไม่พบสาขา")
    return int(branch_id)


@router.get("/api/bom-center/stock/me")
def bc_stock_me(request: Request):
    """Who is counting: a branch login gets its branch; the centre gets the branch list."""
    with get_db() as con:
        _ensure(con)
        scoped = _auth.scope_branch(request)
        rows = con.execute("SELECT branch_id, name FROM branches WHERE name <> 'ALL'"
                           " AND COALESCE(active, 1) = 1 ORDER BY name").fetchall() \
            if "active" in {r[1] for r in con.execute("PRAGMA table_info(branches)")} else \
            con.execute("SELECT branch_id, name FROM branches WHERE name <> 'ALL' ORDER BY name").fetchall()
        branches = [{"branch_id": r[0], "name": r[1]} for r in rows]
        if scoped:
            branches = [b for b in branches if b["name"] == scoped]
        return {"scoped": bool(scoped), "branch": scoped, "branches": branches,
                "zones": [{"zone": z, "label": t} for z, t in _st.ZONES]}


@router.get("/api/bom-center/stock/sheet")
def bc_stock_sheet(request: Request, branch_id: Optional[int] = Query(None)):
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        try:
            return _st.sheet(con, bid)
        except ValueError as e:
            raise HTTPException(400, str(e))


class StockLine(BaseModel):
    code: str
    qty: Optional[float] = None
    unit: Optional[str] = None
    note: Optional[str] = None


class StockBody(BaseModel):
    lines: List[StockLine] = []
    submit: bool = False
    count_date: Optional[str] = None
    note: Optional[str] = None


@router.post("/api/bom-center/stock/count", dependencies=[Depends(require_write_token)])
def bc_stock_save(body: StockBody, request: Request, branch_id: Optional[int] = Query(None)):
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        return _write(con, lambda: _st.save(con, bid, [ln.dict() for ln in body.lines], _who(request),
                                            submit=body.submit, count_date=body.count_date, note=body.note))


@router.delete("/api/bom-center/stock/draft", dependencies=[Depends(require_write_token)])
def bc_stock_discard(request: Request, branch_id: Optional[int] = Query(None)):
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        return {"discarded": _write(con, lambda: _st.discard_draft(con, bid, _who(request)))}


@router.get("/api/bom-center/stock/counts")
def bc_stock_counts(request: Request, branch_id: Optional[int] = Query(None), limit: int = Query(20)):
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        return {"branch_id": bid, "counts": _st.history(con, bid, max(1, min(limit, 200)))}


@router.get("/api/bom-center/stock/count/{count_id}")
def bc_stock_count_detail(count_id: int, request: Request):
    with get_db() as con:
        _ensure(con)
        try:
            d = _st.detail(con, count_id)
        except ValueError as e:
            raise HTTPException(404, str(e))
        _stock_branch(con, request, d["branch_id"])      # a branch login reads only its own counts
        return d


class LayoutRow(BaseModel):
    code: str
    zone: Optional[str] = None
    count_unit: Optional[str] = None


@router.post("/api/bom-center/stock/layout", dependencies=[Depends(require_write_token)])
def bc_stock_layout(rows: List[LayoutRow], request: Request):
    with get_db() as con:
        _ensure(con)
        n = _write(con, lambda: _st.set_layout(con, [r.dict() for r in rows], _who(request)))
    return {"ok": True, "updated": n}


# ── Phase 2 · S7: goods received + usage variance (variance.py) ─────────────
class ReceiptLine(BaseModel):
    code: str
    qty: float
    unit: Optional[str] = None


class ReceiptBody(BaseModel):
    receipt_date: str
    lines: List[ReceiptLine]
    ref: Optional[str] = None
    note: Optional[str] = None


@router.get("/api/bom-center/stock/receipts")
def bc_receipts(request: Request, branch_id: Optional[int] = Query(None), limit: int = Query(30)):
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        return {"branch_id": bid, "receipts": _va.receipts(con, bid, max(1, min(limit, 200)))}


@router.post("/api/bom-center/stock/receipt", dependencies=[Depends(require_write_token)])
def bc_receipt_add(body: ReceiptBody, request: Request, branch_id: Optional[int] = Query(None)):
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        return _write(con, lambda: _va.add_receipt(con, bid, body.receipt_date, [ln.dict() for ln in body.lines],
                                                   _who(request), ref=body.ref, note=body.note))


@router.delete("/api/bom-center/stock/receipt/{receipt_id}", dependencies=[Depends(require_write_token)])
def bc_receipt_delete(receipt_id: int, request: Request, branch_id: Optional[int] = Query(None)):
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        return {"deleted": _write(con, lambda: _va.delete_receipt(con, bid, receipt_id, _who(request)))}


@router.get("/api/bom-center/variance/periods")
def bc_variance_periods(request: Request, branch_id: Optional[int] = Query(None)):
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        return {"branch_id": bid, "periods": _va.periods(con, bid)}


@router.get("/api/bom-center/variance/period")
def bc_variance_period(request: Request, branch_id: Optional[int] = Query(None),
                       end_count_id: Optional[int] = Query(None)):
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        try:
            return _va.period(con, bid, end_count_id)
        except ValueError as e:
            raise HTTPException(404, str(e))


@router.get("/api/bom-center/variance/overview")
def bc_variance_overview():
    """The centre's list: every counting branch's latest period."""
    with get_db() as con:
        _ensure(con)
        return {"branches": _va.overview(con)}


class ReasonBody(BaseModel):
    end_count_id: int
    code: str
    reason: str
    note: Optional[str] = None


@router.post("/api/bom-center/variance/reason", dependencies=[Depends(require_write_token)])
def bc_variance_reason(body: ReasonBody, request: Request, branch_id: Optional[int] = Query(None)):
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        _write(con, lambda: _va.set_reason(con, bid, body.end_count_id, body.code, body.reason, body.note, _who(request)))
    return {"ok": True}


class ConfirmBody(BaseModel):
    end_count_id: int
    undo: bool = False


@router.post("/api/bom-center/variance/confirm-receipts", dependencies=[Depends(require_write_token)])
def bc_variance_confirm(body: ConfirmBody, request: Request, branch_id: Optional[int] = Query(None)):
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        _write(con, lambda: _va.confirm_receipts(con, bid, body.end_count_id, _who(request), undo=body.undo))
    return {"ok": True}


# ── Phase 2 · S8: the branch order (orders.py) ──────────────────────────────
def _round_or_400(r: str) -> str:
    if r not in ("1", "2", "salmon"):
        raise HTTPException(400, "รอบต้องเป็น 1 / 2 / salmon")
    return r


def _day_or_today(d: Optional[str]) -> date:
    if not d:
        return bkk_today()
    try:
        return date.fromisoformat(d)
    except ValueError:
        raise HTTPException(400, "วันที่ต้องอยู่ในรูปแบบ YYYY-MM-DD")


@router.get("/api/bom-center/order/plan")
def bc_order_plan(request: Request, round: Optional[str] = Query(None), order_day: Optional[str] = Query(None),
                  branch_id: Optional[int] = Query(None)):
    """This round's suggestion net of stock + the cycle's order if one exists. Without a
    round, the one whose order day comes first — the branch page opens on it."""
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        day = _day_or_today(order_day)
        rnd = _round_or_400(round) if round else _or.next_round(con, bid, day)
        out = _or.plan(con, bid, rnd, day)
        out["round"] = rnd
        return out


class OrderLine(BaseModel):
    code: str
    confirmed_units: Optional[float] = None
    reason_code: Optional[str] = None
    note: Optional[str] = None


class OrderBody(BaseModel):
    round: str
    order_day: Optional[str] = None
    lines: List[OrderLine] = []
    submit: bool = False
    note: Optional[str] = None


@router.post("/api/bom-center/order/save", dependencies=[Depends(require_write_token)])
def bc_order_save(body: OrderBody, request: Request, branch_id: Optional[int] = Query(None)):
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        rnd = _round_or_400(body.round)
        p = _or.plan(con, bid, rnd, _day_or_today(body.order_day))
        out = _write(con, lambda: _or.save(con, bid, rnd, p["order_day"], p, [ln.dict() for ln in body.lines],
                                           _who(request), submit=body.submit, note=body.note))
    return out


@router.get("/api/bom-center/orders")
def bc_orders(request: Request, branch_id: Optional[int] = Query(None), limit: int = Query(20),
              status: Optional[str] = Query(None)):
    """The branch's orders; status=open → sent or processed (awaiting receipt), or a comma list."""
    sts = _or.OPEN if status == "open" else (tuple(x for x in status.split(",") if x) if status and status != "all" else None)
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        return {"branch_id": bid, "orders": _or.history(con, bid, max(1, min(limit, 200)), statuses=sts)}


@router.get("/api/bom-center/order/{order_id}")
def bc_order_detail(order_id: int, request: Request):
    with get_db() as con:
        _ensure(con)
        try:
            o = _or.detail(con, order_id)
        except ValueError as e:
            raise HTTPException(404, str(e))
        _stock_branch(con, request, o["branch_id"])
        return o


@router.post("/api/bom-center/order/{order_id}/reopen", dependencies=[Depends(require_write_token)])
def bc_order_reopen(order_id: int, request: Request, branch_id: Optional[int] = Query(None)):
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        _write(con, lambda: _or.reopen(con, bid, order_id, _who(request)))
    return {"ok": True}


@router.post("/api/bom-center/order/{order_id}/cancel", dependencies=[Depends(require_write_token)])
def bc_order_cancel(order_id: int, request: Request, branch_id: Optional[int] = Query(None)):
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        _write(con, lambda: _or.cancel(con, bid, order_id, _who(request)))
    return {"ok": True}


class ReceiveLine(BaseModel):
    code: str
    received_units: float


class ReceiveBody(BaseModel):
    lines: List[ReceiveLine]
    receipt_date: Optional[str] = None


@router.post("/api/bom-center/order/{order_id}/receive", dependencies=[Depends(require_write_token)])
def bc_order_receive(order_id: int, body: ReceiveBody, request: Request, branch_id: Optional[int] = Query(None)):
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        return _write(con, lambda: _or.receive(con, bid, order_id, [ln.dict() for ln in body.lines], _who(request),
                                               receipt_date=body.receipt_date))


# ── Phase 2 · S9: the centre's side — Sales Support queue, Ops compliance, monthly (hub.py) ──
_HUB_STATUS_TH = {"open": "รอดำเนินการและดำเนินการแล้ว", "submitted": "รอดำเนินการ", "processed": "ดำเนินการแล้ว",
                  "received": "สาขารับแล้ว", "all": "ทุกสถานะ"}


def _hub_status_or_400(status: Optional[str]) -> str:
    st = status or "open"
    if st not in _HUB_STATUS_TH:
        raise HTTPException(400, "status ต้องเป็น open / submitted / processed / received / all")
    return st


def _ids_or_filter(con, ids: Optional[str], date_from: Optional[str], date_to: Optional[str],
                   status: str = "open") -> list:
    """The orders to consolidate: named ids, else those of `status` in the date range
    (open = sent or processed, the ones Sales Support still buys for)."""
    if ids:
        try:
            return [int(x) for x in ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(400, "ids ต้องเป็นตัวเลขคั่นด้วยเครื่องหมายจุลภาค")
    sts = ("submitted", "processed") if status == "open" else ("all",) if status == "all" else (status,)
    out = []
    for st in sts:
        out += [r["order_id"] for r in _hub.queue(con, st, date_from, date_to)]
    return out


def _sheet_title(name: str, used: set) -> str:
    """An Excel sheet title: 31 characters, none of the characters Excel forbids (colon,
    slashes, question mark, asterisk, brackets), unique in the workbook."""
    t = re.sub(r"[:\\/?*\[\]]", " ", str(name or "ไม่ระบุผู้จำหน่าย")).strip()[:31] or "ผู้จำหน่าย"
    base, n = t, 2
    while t in used:
        suf = f" ({n})"
        t = base[:31 - len(suf)] + suf
        n += 1
    used.add(t)
    return t


@router.get("/api/bom-center/hub/queue")
def bc_hub_queue(status: str = Query("submitted"), date_from: Optional[str] = Query(None),
                 date_to: Optional[str] = Query(None)):
    if status not in ("submitted", "processed", "received", "all"):
        raise HTTPException(400, "status ต้องเป็น submitted / processed / received / all")
    with get_db() as con:
        _ensure(con)
        rows = _hub.queue(con, status, date_from, date_to)
    return {"status": status, "orders": rows, "reasons": [{"code": k, "label": v} for k, v in _hub.SS_REASONS],
            "sla": {"submit_by": _hub.SLA_SUBMIT_BY, "process_by": _hub.SLA_PROCESS_BY}}


@router.get("/api/bom-center/hub/order/{order_id}")
def bc_hub_order(order_id: int):
    with get_db() as con:
        _ensure(con)
        try:
            o = _or.detail(con, order_id)
        except ValueError as e:
            raise HTTPException(404, str(e))
        o["branch"] = (con.execute("SELECT name FROM branches WHERE branch_id=?", (o["branch_id"],)).fetchone() or [None])[0]
        o["delivery"] = _hub.delivery_of(con, o["branch_id"], o["round"], o["cycle_date"])
        return o


class ProcessLine(BaseModel):
    code: str
    processed_units: Optional[float] = None
    reason: Optional[str] = None
    note: Optional[str] = None


class ProcessBody(BaseModel):
    lines: List[ProcessLine] = []
    po_ref: Optional[str] = None


@router.post("/api/bom-center/hub/process/{order_id}", dependencies=[Depends(require_write_token)])
def bc_hub_process(order_id: int, body: ProcessBody, request: Request):
    with get_db() as con:
        _ensure(con)
        return _write(con, lambda: _hub.process(con, order_id, [ln.dict() for ln in body.lines], _who(request),
                                                po_ref=body.po_ref))


@router.get("/api/bom-center/hub/consolidated")
def bc_hub_consolidated(ids: Optional[str] = Query(None), date_from: Optional[str] = Query(None),
                        date_to: Optional[str] = Query(None), status: Optional[str] = Query(None)):
    st = _hub_status_or_400(status)
    with get_db() as con:
        _ensure(con)
        out = _hub.consolidated(con, _ids_or_filter(con, ids, date_from, date_to, st))
    out["status"], out["status_th"] = st, _HUB_STATUS_TH[st]
    return out


@router.get("/api/bom-center/hub/consolidated.xlsx")
def bc_hub_consolidated_xlsx(ids: Optional[str] = Query(None), date_from: Optional[str] = Query(None),
                             date_to: Optional[str] = Query(None), status: Optional[str] = Query(None),
                             split: bool = Query(False)):
    """The consolidated workbook for the orders of `status` (default open); split=true adds one
    sheet per supplier after the two standard sheets, so a supplier gets its own page."""
    from openpyxl import Workbook
    st = _hub_status_or_400(status)
    with get_db() as con:
        _ensure(con)
        oids = _ids_or_filter(con, ids, date_from, date_to, st)
        c = _hub.consolidated(con, oids)
        per_branch = []
        for oid in oids:
            o = _or.detail(con, oid)
            bname = (con.execute("SELECT name FROM branches WHERE branch_id=?", (o["branch_id"],)).fetchone() or [None])[0]
            dlv = _hub.delivery_of(con, o["branch_id"], o["round"], o["cycle_date"])
            for ln in o["lines"]:
                q = ln["processed_units"] if ln["processed_units"] is not None else ln["confirmed_units"]
                if q:
                    per_branch.append([bname, dlv, o["cycle_date"], o["round"], ln["code"], ln["name"], q,
                                       "แพ็ค" if ln["order_unit"] != "base" else "หน่วยฐาน", o["po_ref"], o["order_id"]])
    wb = Workbook()
    ws = wb.active
    br = c["branches"]
    _sheet(ws, "รวมตามผู้จำหน่าย", ["วันส่ง", "ผู้จำหน่าย", "รหัส", "วัตถุดิบ", "หน่วย", "รวม"] + br,
           f"ใบสั่งซื้อสถานะ “{_HUB_STATUS_TH[st]}”" + (f" วันสั่ง {date_from or '…'} ถึง {date_to or '…'}" if (date_from or date_to) else "")
           + " สรุปรวมตามผู้จำหน่าย × วันส่ง (ใช้ยอดที่ Sales Support ดำเนินการ หากยังไม่ดำเนินการ ใช้ยอดที่สาขายืนยัน)")
    for g in c["groups"]:
        for it in g["items"]:
            ws.append([g["delivery"], g["supplier"], it["code"], it["name"], it["unit"], it["total"]] +
                      [it["by_branch"].get(b) for b in br])
    ws2 = wb.create_sheet()
    _sheet(ws2, "ต่อสาขา", ["สาขา", "วันส่ง", "วันสั่ง", "รอบ", "รหัส", "วัตถุดิบ", "จำนวน", "หน่วย", "PO", "ใบสั่งซื้อ"],
           "ทุกบรรทัดของใบสั่งซื้อที่เลือก")
    for r in per_branch:
        ws2.append(r)
    if split:                                   # one page per supplier, its items by delivery day
        by_sup = {}
        for g in c["groups"]:
            by_sup.setdefault(g["supplier"], []).append(g)
        used = {ws.title, ws2.title}
        for sup, gs in sorted(by_sup.items()):
            w = wb.create_sheet()
            _sheet(w, _sheet_title(sup, used), ["วันส่ง", "รหัส", "วัตถุดิบ", "หน่วย", "รวม"] + br,
                   f"ผู้จำหน่าย {sup} · ใบสั่งซื้อสถานะ “{_HUB_STATUS_TH[st]}”")
            for g in gs:
                for it in g["items"]:
                    w.append([g["delivery"], it["code"], it["name"], it["unit"], it["total"]] + [it["by_branch"].get(b) for b in br])
    return _xlsx(wb, f"bom_center_orders_{st}_{date_from or 'all'}_{date_to or 'all'}.xlsx")


def _dates_or_week(date_from: Optional[str], date_to: Optional[str]) -> tuple:
    t = bkk_today()
    d1 = date_to or t.isoformat()
    d0 = date_from or (date.fromisoformat(d1) - timedelta(days=13)).isoformat()
    for d in (d0, d1):
        try:
            date.fromisoformat(d)
        except ValueError:
            raise HTTPException(400, "วันที่ต้องอยู่ในรูปแบบ YYYY-MM-DD")
    return d0, d1


@router.get("/api/bom-center/hub/compliance")
def bc_hub_compliance(date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                      branch_id: Optional[int] = Query(None)):
    d0, d1 = _dates_or_week(date_from, date_to)
    with get_db() as con:
        _ensure(con)
        return _hub.compliance(con, d0, d1, branch_id)


@router.get("/api/bom-center/hub/compliance.xlsx")
def bc_hub_compliance_xlsx(date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None)):
    from openpyxl import Workbook
    d0, d1 = _dates_or_week(date_from, date_to)
    with get_db() as con:
        _ensure(con)
        c = _hub.compliance(con, d0, d1)
    th = {"ok": "ตามแนะนำ", "over": "สั่งเกิน", "under": "สั่งขาด", "not_ordered": "ไม่ได้สั่ง",
          "not_submitted": "ยังไม่ส่ง", "upcoming": "ยังไม่ถึงกำหนด"}
    wb = Workbook()
    ws = wb.active
    _sheet(ws, "Compliance การสั่ง", ["วันสั่ง", "สาขา", "รอบ", "สถานะ", "แนะนำ ฿", "สาขายืนยัน ฿", "ส่วนกลางดำเนินการ ฿",
                                       "รับจริง ฿", "ยืนยัน vs แนะนำ %", "ส่วนกลางเปลี่ยน %", "ส่งขาด %", "ส่งทันเวลา", "ดำเนินการทันเวลา"],
           f"รอบที่ปฏิทินกำหนด {d0} ถึง {d1} · สถานะ “ตามแนะนำ” หมายถึงยอดยืนยันต่างจากยอดแนะนำไม่เกิน ±{_hub.TOL_PCT:g}%")
    for r in c["rows"]:
        ws.append([r["cycle_date"], r["branch"], r["round"], th.get(r["status"], r["status"]), r["suggested"], r["confirmed"],
                   r["processed"], r["received"], r.get("confirmed_vs_suggested_pct"), r.get("centre_change_pct"),
                   r.get("delivery_gap_pct"), r.get("submitted_on_time"), r.get("processed_on_time")])
    return _xlsx(wb, "bom_center_order_compliance.xlsx")


@router.get("/api/bom-center/hub/monthly")
def bc_hub_monthly(month: Optional[str] = Query(None)):
    with get_db() as con:
        _ensure(con)
        m = month if month and _MONTH.match(month) else _rl.last_full_month(con)
        return _hub.monthly(con, m)


# ── Phase 2 · S10: pilot readiness and scorecard (pilot.py) ─────────────────
@router.get("/api/bom-center/hub/pilot")
def bc_hub_pilot(weeks: int = Query(4)):
    """Before the pilot: what is still to set up. During it: each branch against §7.3's pass criteria."""
    with get_db() as con:
        _ensure(con)
        return {"readiness": _pl.readiness(con), "scorecard": _pl.overview(con, max(1, min(weeks, 8)))}


# ── Phase 2 · S11: the central list, the month's deliveries, counts from a file ──
async def _read_xlsx(file: UploadFile, limit_mb: int = 20) -> bytes:
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "ไฟล์ไม่มีข้อมูล")
    if len(raw) > limit_mb * 1024 * 1024:
        raise HTTPException(400, f"ไฟล์ใหญ่เกิน {limit_mb} MB")
    return raw


def _parse_central(con, raw: bytes, month: Optional[str]):
    if month:
        _month_or_400(month)
    try:
        p = _ce.parse_report(con, raw, month)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"อ่านไฟล์ไม่ได้: {e}")
    if not p["items"]:
        raise HTTPException(400, "ไม่พบรายการสินค้าในไฟล์")
    return p


@router.get("/api/bom-center/central")
def bc_central():
    with get_db() as con:
        _ensure(con)
        return _ce.items(con)


@router.post("/api/bom-center/central/import", dependencies=[Depends(require_write_token_unless_dry_run)])
async def bc_central_import(request: Request, file: UploadFile = File(...), dry_run: bool = Query(True),
                            set_local: bool = Query(True), month: Optional[str] = Query(None)):
    """The centre's monthly report (or this list's own export) -> the central list.
    Everything not on it becomes 'local' (the branch buys it itself)."""
    raw = await _read_xlsx(file)
    with get_db() as con:
        _ensure(con)
        p = _parse_central(con, raw, month)
        if dry_run:
            pl = _ce.plan_catalogue(con, p)
            return {"dry_run": True, "month": p["month"], "sheet": p["sheet"], "branches": p["branches"],
                    **pl["counts"], "rows": pl["rows"]}
        out = _write(con, lambda: _ce.apply_catalogue(con, p, _who(request), set_local_others=set_local))
    invalidate_cache("bc_")
    return {"dry_run": False, **out}


@router.get("/api/bom-center/central.xlsx")
def bc_central_xlsx():
    """The list in the report's own layout + the base-unit factor: edit it and import it back."""
    from openpyxl import Workbook
    with get_db() as con:
        _ensure(con)
        d = _ce.items(con)
    wb = Workbook()
    heads = ["ลำดับ", "รหัสสินค้า", "รายละเอียด", "หน่วยนับ", "ราคาขาย", "หน่วยขาย", "1 หน่วยขาย = หน่วยฐาน",
             "หน่วยฐาน", "หน่วยสั่งในระบบ", "ที่มาของอัตราแปลง", "งวดราคา"]
    _sheet(wb.active, "บัญชีสินค้าส่วนกลาง", heads,
           "บัญชีสินค้าที่สาขาสั่งจากส่วนกลาง หากอัตราแปลงไม่ถูกต้อง ให้แก้คอลัมน์ “1 หน่วยขาย = หน่วยฐาน” แล้วนำเข้าอีกครั้ง"
           " · สินค้าที่ไม่อยู่ในบัญชีนี้ สาขาจัดซื้อในพื้นที่")
    ws = wb.active
    for i, r in enumerate(d["items"], 1):
        ws.append([i, r["code"], r["name"], r["unit_label"], r["price_per_sale"], r["sale_unit"], r["base_per_sale"],
                   r["base_unit"], r["recv_unit"], r["factor_how"], r["price_month"]])
    for col, w in (("C", 48), ("I", 22), ("J", 52)):
        ws.column_dimensions[col].width = w
    return _xlsx(wb, "bom_center_central_list.xlsx")


@router.get("/api/bom-center/central/links")
def bc_central_links():
    """Recipe codes counted as the code the centre sends, and suggested pairs to confirm."""
    with get_db() as con:
        _ensure(con)
        return _ce.link_view(con)


class LinkBody(BaseModel):
    old_code: str
    new_code: Optional[str] = None
    note: Optional[str] = None


@router.post("/api/bom-center/central/links", dependencies=[Depends(require_write_token)])
def bc_central_links_save(body: List[LinkBody], request: Request):
    who = _who(request)
    with get_db() as con:
        _ensure(con)
        _write(con, lambda: [_ce.set_link(con, b.old_code, b.new_code, who, b.note) for b in body])
    invalidate_cache("bc_")
    return {"saved": len(body)}


@router.post("/api/bom-center/central/deliveries", dependencies=[Depends(require_write_token_unless_dry_run)])
async def bc_central_deliveries(request: Request, file: UploadFile = File(...), dry_run: bool = Query(True),
                                branch_ids: Optional[str] = Query(None), month: Optional[str] = Query(None),
                                receipt_date: Optional[str] = Query(None)):
    """The month's quantities per branch -> one goods-received note per branch (ref CENTRAL-YYYY-MM)."""
    raw = await _read_xlsx(file)
    try:
        ids = [int(x) for x in branch_ids.split(",") if x.strip()] if branch_ids else None
    except ValueError:
        raise HTTPException(400, "branch_ids ต้องเป็นตัวเลขคั่นด้วยเครื่องหมายจุลภาค")
    with get_db() as con:
        _ensure(con)
        p = _parse_central(con, raw, month)
        if dry_run:
            try:
                return _ce.deliveries(con, p, _who(request), ids, dry_run=True, receipt_date=receipt_date)
            except ValueError as e:
                raise HTTPException(400, str(e))
        out = _write(con, lambda: _ce.deliveries(con, p, _who(request), ids, dry_run=False,
                                                 receipt_date=receipt_date))
    invalidate_cache("bc_")
    return out


@router.post("/api/bom-center/central/month-import", dependencies=[Depends(require_write_token_unless_dry_run)])
async def bc_central_month_import(request: Request, file: UploadFile = File(...), dry_run: bool = Query(True),
                                  set_local: bool = Query(True), month: Optional[str] = Query(None),
                                  branch_ids: Optional[str] = Query(None), receipt_date: Optional[str] = Query(None)):
    """The centre's monthly report ONCE: the central list AND one goods-received note per
    branch, previewed together and saved in one transaction — the list first, so the
    notes convert with the order units it puts in the master. The preview shows every
    branch; the save books the branches named in branch_ids and none when it is empty."""
    raw = await _read_xlsx(file)
    try:
        ids = [int(x) for x in branch_ids.split(",") if x.strip()] if branch_ids else []
    except ValueError:
        raise HTTPException(400, "branch_ids ต้องเป็นตัวเลขคั่นด้วยเครื่องหมายจุลภาค")
    who = _who(request)
    with get_db() as con:
        _ensure(con)
        p = _parse_central(con, raw, month)
        try:
            rows = _ce.plan_catalogue(con, p)["rows"] if dry_run else []
            cat = _ce.apply_catalogue(con, p, who, set_local_others=set_local)
            if not p.get("month"):          # the list's own export: no month, no quantities → the list alone
                if ids:
                    raise ValueError("ไฟล์นี้ไม่มีงวดของรายงาน จึงสร้างใบรับสินค้าไม่ได้ (ระบุเดือนด้วย month=YYYY-MM หากต้องการ)")
                dl = {"month": None, "receipt_date": None, "dry_run": dry_run, "branches": [],
                      "note": "ไฟล์นี้ไม่มีงวดและยอดจัดส่งรายสาขา จึงบันทึกเฉพาะบัญชีสินค้า"}
            elif dry_run:
                dl = _ce.deliveries(con, p, who, None, dry_run=True, receipt_date=receipt_date)
            elif ids:
                dl = _ce.deliveries(con, p, who, ids, dry_run=False, receipt_date=receipt_date)
            else:
                dl = {"month": p["month"], "receipt_date": receipt_date, "dry_run": False, "branches": []}
            if dry_run:
                con.rollback()
            else:
                con.commit()
        except ValueError as e:
            con.rollback()
            raise HTTPException(400, str(e))
        except Exception:
            con.rollback()
            raise
    if not dry_run:
        invalidate_cache("bc_")
    return {"dry_run": dry_run, "month": p["month"], "sheet": p["sheet"], "catalogue": {**cat, "rows": rows},
            "deliveries": dl}


@router.get("/api/bom-center/stock/count-template.xlsx")
def bc_stock_count_template(request: Request, branch_id: Optional[int] = Query(None),
                            count_date: Optional[str] = Query(None)):
    """The branch's count list as a sheet to fill in (a count on paper, or a month closed later)."""
    from openpyxl import Workbook
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        name = con.execute("SELECT name FROM branches WHERE branch_id=?", (bid,)).fetchone()[0]
        rows = _st.template_rows(con, bid)
    wb = Workbook()
    heads = ["โซน", "รหัส", "ชื่อ", "หน่วยนับ", "จำนวนที่นับ", "1 หน่วยนับ = หน่วยฐาน", "หน่วยฐาน", "ชั้น", "รายการบังคับนับ",
             "หมายเหตุ"]
    _sheet(wb.active, "ตรวจนับ", heads,
           f"แบบฟอร์มตรวจนับสินค้าคงเหลือ สาขา{name} · วันที่ตรวจนับ {count_date or '……'} (นับก่อนเปิดขายและก่อนรับสินค้าของวันนั้น)"
           " · กรอกจำนวนตามหน่วยนับ (เปลี่ยนหน่วยได้ เช่น แพ็ค/กิโลกรัม) · ช่องที่เว้นว่างถือว่าไม่ได้ตรวจนับ")
    ws = wb.active
    for r in rows:
        ws.append([r["zone"], r["code"], r["name"], r["count_unit"], None, r["per_count_unit"], r["base_unit"],
                   r["class"], "บังคับ" if r["required"] else "", None])
    ws.column_dimensions["C"].width = 44
    ws.freeze_panes = "A3"
    safe = re.sub(r"[^\w-]", "_", f"{bid}_{count_date or 'date'}")
    return _xlsx(wb, f"stock_count_{safe}.xlsx")


@router.post("/api/bom-center/stock/count-import", dependencies=[Depends(require_write_token_unless_dry_run)])
async def bc_stock_count_import(request: Request, file: UploadFile = File(...), count_date: str = Query(...),
                                branch_id: Optional[int] = Query(None), dry_run: bool = Query(True)):
    """A whole count from the template, submitted with the date it was counted."""
    raw = await _read_xlsx(file)
    try:
        lines = _st.read_count_file(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        if dry_run:
            try:
                return _st.import_count(con, bid, count_date, lines, _who(request), dry_run=True)
            except ValueError as e:
                raise HTTPException(400, str(e))
        out = _write(con, lambda: _st.import_count(con, bid, count_date, lines, _who(request), dry_run=False))
    invalidate_cache("bc_")
    return out
