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
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

import auth as _auth
from core import get_db, invalidate_cache, require_write_token, require_write_token_unless_dry_run
from bom_center import aliases as _al
from bom_center import benchmark as _bm
from bom_center import central as _ce
from bom_center import claims as _cl
from bom_center import codes as _cr
from bom_center import hub as _hub
from bom_center import issues as _is
from bom_center import issue_reports as _ir
from bom_center.clock import today as bkk_today
from bom_center.fmt import CLASS_SHORT_TH as _CLASS_SHORT
from bom_center import master as _m
from bom_center import orders as _or
from bom_center import per_sales as _pv
from bom_center import pilot as _pl
from bom_center import prep as _pr
from bom_center import todo as _td
from bom_center import prices as _p
from bom_center import price_submissions as _ps
from bom_center import recipe_check as _rc
from bom_center import recipes as _r
from bom_center import release as _rl
from bom_center import stock as _st
from bom_center import variance as _va
from bom_center.schema import ensure as _ensure
from bom_center.units import Converter
from bom_center.units import norm as _unit_norm

router = APIRouter()
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _who(request: Request) -> str:
    try:
        return (_auth.parse_token(request.cookies.get(_auth.SESSION_COOKIE, "")) or {}).get("u") or "-"
    except Exception:
        return "-"


def _modules(request: Request) -> set:
    """The live module set of the signed-in user — same answer main.enforce_auth gives. Every module when auth is off."""
    if not _auth.auth_enabled():
        return set(_auth.MODULES)
    payload = _auth.parse_token(request.cookies.get(_auth.SESSION_COOKIE, "")) or {}
    try:
        with get_db() as con:
            row = _auth.get_user(con, payload.get("u", ""))
    except Exception:
        row = None
    return _auth.modules_for_role(row["role"]) if row and row["active"] else set()


def _month_or_400(m: Optional[str]) -> str:
    if not m or not _MONTH.match(m):
        raise HTTPException(400, "เดือนไม่ถูกต้อง (เช่น 2026-09)")
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


def _for_accounting(wb) -> None:
    """PR-Y: a file for the accounting system shows the code that system uses now; the register code is kept in the last column."""
    with get_db() as con:
        _ensure(con)
        _al.recode_workbook(wb, _al.current_map(con, "acc"))


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
    _fc.clear_usage_cache()           # the order plan's usage model reads this
    _drop_bom_days()             # … and so does the BoM day cache of the issue reports
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
        conv = Converter(con)
        labels = {(c, _unit_norm(u)): lb for c, u, lb in con.execute("SELECT ingredient_code, unit, label FROM bc_unit_rule")}
        for code in conv.ladders:                         # PR-U: each rule carries its ladder step and whether it is approximate
            for r in conv.ladder(code):
                rules.setdefault(code, []).append({
                    "unit": r["unit"], "factor": r["factor_to_base"], "label": labels.get((code, r["unit"])),
                    "parent_unit": r["parent_unit"], "qty_in_parent": r["qty_in_parent"], "kind": r["kind"],
                    "range_min": r["range_min"], "range_max": r["range_max"], "level": r["level"],
                    "approx": r["approx"], "how": r["how"]})
        aliases = {}                                      # PR-Y: only the codes in use; each says which system and whether it is the one to use
        live = _al.in_use(con)
        for a, c, s, vf in con.execute("SELECT alias_code, ingredient_code, system, valid_from FROM bc_ingredient_alias"
                                       " WHERE COALESCE(active,1)=1 ORDER BY alias_code"):
            aliases.setdefault(c, []).append({"alias": a, "system": s, "system_th": _al.SYSTEM_TH.get(s, s or ""), "valid_from": vf,
                                              "in_use": a in live})
        used = dict(con.execute("SELECT ingredient_code, COUNT(DISTINCT sku_id) FROM bom_ingredients"
                                " GROUP BY ingredient_code"))
        cols = ["code", "name_th", "name_en", "family", "kind", "category", "base_unit", "recv_unit",
                "qty_per_recv", "loss_pct", "moq", "lead_days", "shelf_days", "monitored", "sourcing_default",
                "active", "note", "storage_zone", "count_unit", "updated_at", "updated_by"]
        rows = [dict(zip(cols, r)) for r in con.execute(f"SELECT {', '.join(cols)} FROM bc_ingredient ORDER BY code")]
    ql = (q or "").strip().lower()
    out = []
    for r in rows:
        if kind and r["kind"] != kind:
            continue
        if ql and ql not in f"{r['code']} {r['name_th'] or ''} {r['name_en'] or ''} {' '.join(a['alias'] for a in aliases.get(r['code'], []))}".lower():
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
    storage_zone: Optional[str] = None       # a zone code (or its label); "" = back to the automatic guess
    count_unit: Optional[str] = None         # the unit a branch counts in; "" = the pack / base unit


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
    _fc.clear_usage_cache()           # the order plan's usage model reads this
    _drop_bom_days()             # … and so does the BoM day cache of the issue reports
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
        _fc.clear_usage_cache()           # the order plan's usage model reads this
        _drop_bom_days()             # … and so does the BoM day cache of the issue reports
    return {"dry_run": dry_run, "before": before, "after": after, **stats}


@router.get("/api/bom-center/ingredients/template.xlsx")
def bc_ingredients_template():
    """The whole master as a sheet: edit in Excel, import back (a row you delete is left alone)."""
    from openpyxl import Workbook
    heads = ["รหัส", "ชื่อวัตถุดิบ", "ชนิด", "หมวด", "หน่วยฐาน", "หน่วยรับ", "ขนาดบรรจุ",
             "สูญเสีย (%)", "ขั้นต่ำ", "ระยะส่ง (วัน)", "อายุเก็บ (วัน)", "บังคับสั่ง", "ช่องทางสั่ง", "โซนจัดเก็บ", "หน่วยที่นับ"]
    kind_th = {"raw": "ดิบ", "prepared": "กึ่งสำเร็จ", "consumable": "วัสดุสิ้นเปลือง", "zero_by_design": "ตั้งใจเป็น 0"}
    wb = Workbook()
    ws = wb.active
    with get_db() as con:
        _ensure(con)
        zl = _st.zone_labels(con)
        _sheet(ws, "ทะเบียนวัตถุดิบ", heads,
               "ห้ามแก้ไขคอลัมน์รหัส · ขนาดบรรจุ = จำนวนหน่วยฐานต่อ 1 หน่วยรับ (เช่น แซลมอน 1 กก. = 1000 กรัม) · "
               "ชนิด: ดิบ / กึ่งสำเร็จ / วัสดุสิ้นเปลือง / ตั้งใจเป็น 0 · ช่องทางสั่ง: ไดกิโตะ กรุ๊ป / สาขาซื้อเอง · "
               "โซนจัดเก็บ: " + " / ".join(zl.values()) + " (เว้นว่างได้) · "
               "หน่วยที่นับ = หน่วยที่สาขาใช้นับสต็อก (เว้นว่างได้ ใช้หน่วยรับหรือหน่วยฐาน) · "
               "สินค้าส่วนกลางทุกรายการสั่งจาก " + _ce.CENTRAL_SUPPLIER)
        for r in con.execute("SELECT code, name_th, kind, category, base_unit, recv_unit, qty_per_recv,"
                             " loss_pct, moq, lead_days, shelf_days, monitored, sourcing_default, storage_zone, count_unit"
                             " FROM bc_ingredient WHERE active=1 ORDER BY code"):
            r = list(r)
            r[2] = kind_th.get(r[2], r[2])
            r[11] = "ใช่" if r[11] else ""
            r[12] = "สาขาซื้อเอง" if r[12] == "local" else "ไดกิโตะ กรุ๊ป"
            r[13] = zl.get(r[13], r[13] or "")
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
        raise HTTPException(400, "ไม่พบหัวตาราง (ต้องมีคอลัมน์รหัส) ใช้แม่แบบของระบบ หรือแผ่นงาน DATAP")
    rows = []
    for row in grid[head_at + 1:]:
        if not any(str(c or "").strip() for c in row):
            continue
        rows.append({k: (row[j] if j < len(row) else None) for k, j in hmap.items()})
    return rows, sheet, sorted(hmap)


# ── PR-Y: a new code of an existing item is an alias; the recipes keep the register code ─────────────────────
def _can_alias(request: Request) -> bool:
    """Who may record an alias at once (the analyst, and Accounting through the price page); everyone else files a request."""
    return bool({"bom.center", "bom.prices"} & _modules(request))


def _choices_or_400(raw):
    """The per-code answers a screen sends with an import ({code: {as, item}}); None = it sent none (the old behaviour)."""
    try:
        return _al.choices_from(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/bom-center/ingredients/import", dependencies=[Depends(require_write_token_unless_dry_run)])
async def bc_ingredients_import(request: Request, file: UploadFile = File(...), dry_run: bool = Query(True),
                                month: Optional[str] = Query(None), code_choices: Optional[str] = Form(None),
                                alias_system: str = Form("acc")):
    """Import the master template or a DATAP sheet. Values in the file overwrite. A code the register does not know is listed
    with the items it could be a new code of; `code_choices` is the answer per code (alias of an existing item / a new item)."""
    rows, sheet, cols = await _read_master_file(file)
    choices = _choices_or_400(code_choices)
    with get_db() as con:
        _ensure(con)
        try:
            stats = _m.import_rows(con, rows, _who(request),
                                   default_month=month if month and _MONTH.match(month) else None,
                                   choices=choices, can_alias=_can_alias(request), system=alias_system)
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
        _fc.clear_usage_cache()           # the order plan's usage model reads this
        _drop_bom_days()             # … and so does the BoM day cache of the issue reports
    return {"dry_run": dry_run, "sheet": sheet, "columns": cols, "rows": len(rows),
            **{k: v for k, v in stats.items() if k != "skipped"}, "skipped": stats["skipped"][:200]}


class CodeRequestBody(BaseModel):
    new_code: str
    ingredient_code: str
    system: Optional[str] = "acc"
    old_code: Optional[str] = None
    name: Optional[str] = None
    valid_from: Optional[str] = None
    note: Optional[str] = None


@router.post("/api/bom-center/codes/request", dependencies=[Depends(require_write_token)])
def bc_code_request(body: CodeRequestBody, request: Request):
    """Sales Support: this register item has a new code in another system. Files ONE request for the analyst; the register
    and the recipes are untouched."""
    with get_db() as con:
        _ensure(con)
        out = _write(con, lambda: _al.request(con, body.new_code, body.system, body.ingredient_code, _who(request),
                                              old_code=body.old_code, name=body.name, valid_from=body.valid_from, note=body.note))
    return {"ok": True, **out}


@router.get("/api/bom-center/codes/requests")
def bc_code_requests(status: str = Query("pending")):
    if status not in ("pending", "applied", "rejected", "all"):
        raise HTTPException(400, "สถานะไม่ถูกต้อง")
    with get_db() as con:
        _ensure(con)
        return {"status": status, "requests": _al.listing(con, status), "pending": _al.pending_count(con),
                "systems": [{"code": c, "label": t} for c, t in _al.SYSTEMS]}


class CodeDecideBody(BaseModel):
    request_id: int
    decision: str                                  # applied | rejected
    note: Optional[str] = None


@router.post("/api/bom-center/codes/request/decide", dependencies=[Depends(require_write_token)])
def bc_code_request_decide(body: CodeDecideBody, request: Request):
    """The analyst's answer: record the new code as an alias of the item (current for its system), or reject it with a reason
    Sales Support reads. The recipes are not edited: they keep the register code."""
    with get_db() as con:
        _ensure(con)
        out = _write(con, lambda: _al.decide(con, body.request_id, body.decision, body.note, _who(request)))
    invalidate_cache("bc_")
    _fc.clear_usage_cache()           # the order plan's usage model reads this
    _drop_bom_days()             # … and so does the BoM day cache of the issue reports
    return {"ok": True, **out}


@router.get("/api/bom-center/codes/suggest")
def bc_code_suggest(name: str = Query(""), code: Optional[str] = Query(None), unit: Optional[str] = Query(None)):
    """The register items a name (and the unit it is bought in) could belong to - best three first."""
    with get_db() as con:
        _ensure(con)
        return {"suggestions": _al.suggest_items(con, name, code=code, unit=unit)}


@router.get("/api/bom-center/ingredients/{code}/aliases")
def bc_ingredient_aliases(code: str):
    with get_db() as con:
        _ensure(con)
        try:
            return _al.aliases_of(con, code)
        except ValueError as e:
            raise HTTPException(404, str(e))


class AliasBody(BaseModel):
    action: str                                    # add | retire | current | register
    alias: Optional[str] = None
    system: Optional[str] = "acc"
    valid_from: Optional[str] = None
    note: Optional[str] = None
    make_current: Optional[bool] = True


@router.post("/api/bom-center/ingredients/{code}/aliases", dependencies=[Depends(require_write_token)])
def bc_ingredient_aliases_save(code: str, body: AliasBody, request: Request):
    """The analyst records, retires or switches the outside codes of one register item. An alias is retired (active=0), never deleted."""
    who = _who(request)
    with get_db() as con:
        _ensure(con)

        def run():
            if body.action == "add":
                return _al.add_alias(con, code, body.alias, body.system, body.valid_from, body.note, who,
                                     make_current=bool(body.make_current))
            if body.action == "register":
                return _al.use_register_code(con, code, body.system, who)
            if body.action in ("retire", "current"):
                r = con.execute("SELECT ingredient_code FROM bc_ingredient_alias WHERE alias_code=?", (_al._code(body.alias),)).fetchone()
                if not r or r[0] != code:
                    raise ValueError("รหัสนี้ไม่ใช่รหัสในระบบอื่นของรายการนี้")
                return _al.retire(con, body.alias, who) if body.action == "retire" else _al.set_current(con, body.alias, who)
            raise ValueError("คำสั่งไม่ถูกต้อง · ใช้ add / retire / current / register")
        out = _write(con, run)
        now_state = _al.aliases_of(con, code)
    invalidate_cache("bc_")
    _fc.clear_usage_cache()           # the order plan's usage model reads this
    _drop_bom_days()             # … and so does the BoM day cache of the issue reports
    return {"ok": True, **out, "state": now_state}


# ── M2 Material Converter ────────────────────────────────────────────────────
class UnitRule(BaseModel):
    code: str
    unit: str
    factor_to_base: Optional[float] = None       # None (and no qty_in_parent) deletes the rule
    label: Optional[str] = None
    # PR-U — a step of the unit ladder: 1 <unit> = qty_in_parent <parent_unit> (parent empty = the base unit)
    parent_unit: Optional[str] = None
    qty_in_parent: Optional[float] = None
    conv_kind: Optional[str] = None              # exact | avg (แน่นอน | เฉลี่ย)
    range_min: Optional[float] = None
    range_max: Optional[float] = None


@router.post("/api/bom-center/units", dependencies=[Depends(require_write_token)])
def bc_units_save(rules: List[UnitRule], request: Request):
    with get_db() as con:
        _ensure(con)
        try:
            for r in rules:
                _m.set_unit_rule(con, r.code.strip(), r.unit, r.factor_to_base, r.label, _who(request),
                                 parent_unit=r.parent_unit, qty_in_parent=r.qty_in_parent, conv_kind=r.conv_kind,
                                 range_min=r.range_min, range_max=r.range_max)
        except ValueError as e:
            con.rollback()
            raise HTTPException(400, str(e))
        con.commit()
    invalidate_cache("bc_")
    _fc.clear_usage_cache()           # the order plan's usage model reads this
    _drop_bom_days()             # … and so does the BoM day cache of the issue reports
    return {"ok": True, "rules": len(rules)}


class LadderRow(BaseModel):
    unit: str
    qty_in_parent: Optional[float] = None
    parent_unit: Optional[str] = None            # empty = the item's base unit
    conv_kind: Optional[str] = "exact"
    range_min: Optional[float] = None
    range_max: Optional[float] = None
    label: Optional[str] = None


class LadderBody(BaseModel):
    code: str
    rows: List[LadderRow]


@router.get("/api/bom-center/units/ladder")
def bc_units_ladder(code: str = Query(...)):
    """One item's unit ladder, with the factors worked out: {code, name, base_unit, recv_unit, qty_per_recv, count_unit, ladder, problems}."""
    from bom_center.units import MAX_STEPS, check_ladder
    with get_db() as con:
        _ensure(con)
        m = con.execute("SELECT name_th, base_unit, recv_unit, qty_per_recv, count_unit FROM bc_ingredient WHERE code=?",
                        (code.strip(),)).fetchone()
        if not m:
            raise HTTPException(404, f"ไม่พบรหัส {code} ในทะเบียน")
        conv = Converter(con)
        lad = conv.ladder(code.strip())
        problems = check_ladder(m[1], [{"unit": r["unit"], "parent_unit": r["parent_unit"], "qty_in_parent": r["qty_in_parent"],
                                        "conv_kind": r["kind"], "range_min": r["range_min"], "range_max": r["range_max"]}
                                       for r in lad]) if lad else []
    from bom_center.units import unit_word
    return {"code": code.strip(), "name": m[0], "base_unit": m[1], "recv_unit": m[2], "recv_word": unit_word(m[2]) or None,
            "qty_per_recv": m[3], "count_unit": m[4], "ladder": lad, "problems": problems, "max_steps": MAX_STEPS}


@router.post("/api/bom-center/units/ladder", dependencies=[Depends(require_write_token)])
def bc_units_ladder_save(body: LadderBody, request: Request):
    """Replace the WHOLE ladder of one item (validated as a whole; nothing is written when anything is wrong)."""
    with get_db() as con:
        _ensure(con)
        out = _write(con, lambda: _m.set_ladder(con, body.code, [r.dict() for r in body.rows], _who(request)))
    invalidate_cache("bc_")
    _fc.clear_usage_cache()           # the order plan's usage model reads this
    _drop_bom_days()             # … and so does the BoM day cache of the issue reports
    return {"ok": True, **out}


# ── PR-U2: the average weight per piece learned from receipts, and "ขนาดบรรจุไม่ตรง" from the branch ──────────────
@router.get("/api/bom-center/units/avg-proposals")
def bc_units_avg_proposals(days: int = Query(90)):
    """Every item with an average weight step: the step, what the receipts of the last `days` days say, and whether it is a
    proposal for the analyst (≥ 3 receipts, ≥ 10 pieces, a difference of ≥ 3 %, a receipt newer than the last decision)."""
    from bom_center import units as _u
    days = max(7, min(int(days), 365))
    with get_db() as con:
        _ensure(con)
        rows = _u.avg_proposals(con, days)
        hist = _u.avg_history(con)
    return {"days": days, "rows": rows, "proposals": sum(1 for r in rows if r["proposal"]), "history": hist,
            "need": {"receipts": _u.AVG_MIN_RECEIPTS, "pieces": _u.AVG_MIN_PIECES, "diff_pct": _u.AVG_MIN_DIFF_PCT}}


class AvgDecisionBody(BaseModel):
    code: str
    unit: str
    decision: str                                  # applied | kept
    note: Optional[str] = None


@router.post("/api/bom-center/units/avg-decision", dependencies=[Depends(require_write_token)])
def bc_units_avg_decision(body: AvgDecisionBody, request: Request):
    """The analyst's answer to a proposal: use the average the receipts show (the ladder changes through set_ladder), or keep
    the current one with a reason. Released months stay frozen (prep.freeze keeps the factor of a released line)."""
    from bom_center import units as _u
    with get_db() as con:
        _ensure(con)
        out = _write(con, lambda: _u.decide_avg(con, body.code, body.unit, body.decision, _who(request), note=body.note))
    invalidate_cache("bc_")
    _fc.clear_usage_cache()           # the order plan's usage model reads this
    _drop_bom_days()             # … and so does the BoM day cache of the issue reports
    return {"ok": True, **out}


class PackReportBody(BaseModel):
    order_id: int
    code: str
    unit: str
    qty: float
    parent: str
    note: Optional[str] = None


@router.post("/api/bom-center/units/pack-report", dependencies=[Depends(require_write_token)])
def bc_units_pack_report(body: PackReportBody, request: Request, branch_id: Optional[int] = Query(None)):
    """A branch reports, from its receive form, that a pack is not the registered size. Pinned to the branch's own order; it
    does not block or change the receipt and changes no register data (the analyst decides)."""
    from bom_center import pack_reports as _pk
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        try:
            return _write(con, lambda: _pk.submit(con, bid, body.order_id, body.code, body.unit, body.qty, body.parent,
                                                   body.note, _who(request)))
        except PermissionError as e:
            raise HTTPException(403, detail=str(e), headers={"X-Denied": "role"})


@router.get("/api/bom-center/units/pack-reports")
def bc_units_pack_reports(status: str = Query("pending")):
    from bom_center import pack_reports as _pk
    if status not in ("pending", "applied", "rejected", "all"):
        raise HTTPException(400, "สถานะไม่ถูกต้อง")
    with get_db() as con:
        _ensure(con)
        return {"status": status, "groups": _pk.listing(con, status), "pending": _pk.pending_count(con)}


class PackDecideBody(BaseModel):
    report_ids: List[int]
    decision: str                                  # applied | rejected
    note: Optional[str] = None


@router.post("/api/bom-center/units/pack-report/decide", dependencies=[Depends(require_write_token)])
def bc_units_pack_report_decide(body: PackDecideBody, request: Request):
    """The analyst's answer to one group of reports: apply the size (the ladder changes through set_ladder) or reject it
    with a reason the branch reads on its receive history."""
    from bom_center import pack_reports as _pk
    with get_db() as con:
        _ensure(con)
        out = _write(con, lambda: _pk.decide(con, body.report_ids, body.decision, body.note, _who(request)))
    invalidate_cache("bc_")
    _fc.clear_usage_cache()           # the order plan's usage model reads this
    _drop_bom_days()             # … and so does the BoM day cache of the issue reports
    return {"ok": True, **out}


@router.get("/api/bom-center/units/template.xlsx")
def bc_units_template(all: int = Query(0)):
    """The unit ladders (and the portion recipes) as an Excel file; `all=1` adds an empty row for every item that has none."""
    from openpyxl import Workbook
    from openpyxl.worksheet.datavalidation import DataValidation
    from bom_center import unit_sheet as _us
    wb = Workbook()
    ws = wb.active
    with get_db() as con:
        _ensure(con)
        _sheet(ws, _us.SHEET, _us.HEADERS, _us.NOTE)
        for r in _us.ladder_rows(con, bool(all)):
            ws.append(r)
        dv = DataValidation(type="list", formula1='"แน่นอน,เฉลี่ย"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"G3:G{max(ws.max_row, 400)}")
        ws2 = wb.create_sheet()
        _sheet(ws2, _us.PORTION_SHEET, _us.PORTION_HEADERS, _us.PORTION_NOTE)
        for r in _us.portion_rows(con):
            ws2.append(r)
    return _xlsx(wb, f"BOM_CENTER_UNITS_{date.today().isoformat()}.xlsx")


@router.post("/api/bom-center/units/import", dependencies=[Depends(require_write_token_unless_dry_run)])
async def bc_units_import(request: Request, file: UploadFile = File(...), dry_run: bool = Query(True)):
    """The unit-ladder file back in. A dry run shows, per code, the ladder with its factors and its problems and writes
    nothing; the commit replaces the ladder of every code in the file, or refuses the whole file when any code has a problem."""
    from bom_center import unit_sheet as _us
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "ไฟล์ไม่มีข้อมูล")
    if len(raw) > 30 * 1024 * 1024:
        raise HTTPException(400, "ไฟล์มีขนาดเกิน 30 MB")
    try:
        parsed = _us.parse(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with get_db() as con:
        _ensure(con)
        try:
            res = _us.apply(con, parsed, _who(request))
            if dry_run:
                con.rollback()
            elif res["problems"]:
                con.rollback()
                raise HTTPException(400, f"มีรายการที่ต้องแก้ไข {res['problems']} รายการ จึงยังไม่บันทึก")
            else:
                con.commit()
        except HTTPException:
            raise
        except Exception:
            con.rollback()
            raise
    if not dry_run:
        invalidate_cache("bc_")
        _fc.clear_usage_cache()           # the order plan's usage model reads this
        _drop_bom_days()             # … and so does the BoM day cache of the issue reports
    return {"dry_run": dry_run, "sheets": parsed["sheets"], "codes": len(res["ladders"]),
            "codes_ok": sum(1 for r in res["ladders"] if not r["problems"]),
            "portions": len(res["portions"]), "problems": res["problems"], "warnings": res["warnings"],
            "ladders": res["ladders"], "portion_rows": res["portions"], "month": res["month"]}


@router.get("/api/bom-center/convert")
def bc_convert(code: str = Query(...), qty: float = Query(1.0), unit: str = Query(...),
               month: Optional[str] = Query(None)):
    """'3 แท่ง of 13-01-009' -> base units, and what it costs at `month`'s price. `approx` = the conversion passes an
    average step (PR-U): every screen shows it with ≈."""
    month = month if month and _MONTH.match(month) else _this_month()
    with get_db() as con:
        _ensure(con)
        conv = Converter(con)
        qb, how = conv.to_base(code, qty, unit)
        approx = bool(conv.last_approx) if qb is not None else False
        sp = conv.span(code, unit) if approx else None
        p = _p.price_for(con, code, month)
    return {"code": code, "qty": qty, "unit": unit, "qty_base": qb, "base_unit": conv.base_unit(code),
            "how": how, "approx": approx, "range_base": [qty * sp[0], qty * sp[1]] if sp else None,
            "price_per_base": p[0] if p else None,
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
           "1 แถว = 1 บรรทัดซื้อ/รับเข้า · มูลค่ารวมไม่รวม VAT · หน่วย: กิโลกรัม / กรัม / แพ็ค / ลิตร … (แปลงหน่วยตอนนำเข้า) · "
           "เดือน เช่น 2026-09 · หรือนำเข้า 'รายงานจ่ายสินค้าภายใน' ของบัญชีได้โดยตรง")
    wb.active.append(["02-01-001", "ปลาแซลมอนเทร้านอร์เวย์ (ตัวอย่าง ลบแถวนี้ก่อนนำเข้า)", 785.46, "กิโลกรัม", 294332.5,
                      "ขอนแก่น", "2026-09"])
    return _xlsx(wb, "BOM_CENTER_PURCHASE_LINES_TEMPLATE.xlsx")


async def _price_lines_from_file(file: UploadFile, month: Optional[str]):
    """-> (lines, info, month) for an uploaded file."""
    return await _price_lines_from_bytes(await file.read(), file.filename, month)


async def _price_lines_from_bytes(raw: bytes, filename: Optional[str], month: Optional[str]):
    """-> (lines, info, month). Accounting issue workbook (sheets 'Use XXX') or the template.
    Shared by the analyst's upload and by the file Sales Support sent (stored bytes)."""
    if not raw:
        raise HTTPException(400, "ไฟล์ไม่มีข้อมูล")
    name = (filename or "").lower()
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

    _fname = filename

    class _F:                                       # re-wrap the bytes for the shared reader
        filename = _fname

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
            raise HTTPException(400, "ระบุเดือนที่จะนำเข้า (ไฟล์มีหลายเดือนหรือไม่มีคอลัมน์เดือน)")
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
        raise HTTPException(400, "คำขอไม่ถูกต้อง")
    out = []
    for r in items or []:
        try:
            out.append({"code": str(r["code"]).strip(), "unit": str(r["unit"]).strip(), "factor_to_base": float(r["factor_to_base"])})
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "กฎหน่วยต้องมี code, unit และ factor_to_base")
    return out


def _import(lines, info, month, dry_run, who, note, add_unknown=False, rules=None, choices=None, can_alias=True,
            alias_system="acc"):
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
            decided = None
            if choices is not None:        # PR-Y: the person's answer per unknown code - alias of an item / a new item; no answer = left out
                decided = _al.apply_choices(con, _al.unknown_in(con, lines), choices, who, can_alias, system=alias_system,
                                            note=note, add_new=lambda c, u: _m.add_from_purchase(con, c, u.get("name"), u.get("unit"), who))
            agg = _p.aggregate(con, lines, add_unknown=add_unknown, who=who, month=month)
            pv = _p.preview(con, month, agg)
            result = None
            if dry_run:
                con.rollback()
            else:
                if not agg["codes"]:
                    raise HTTPException(400, "ไม่มีรายการที่ใช้ได้ · ตรวจรายการที่แปลงหน่วยไม่ได้ด้านล่าง")
                result = _p.apply(con, month, agg, who, note)
                con.commit()
        except Exception:
            con.rollback()
            raise
    if not dry_run:
        invalidate_cache("bc_")
    pv["issues"] = pv["issues"][:300]
    return {"dry_run": dry_run, "source": info, "lines": len(lines), **pv, "applied": result,
            "rules_applied": len(rules or []), "codes_decided": decided}


class RulesBody(BaseModel):
    rules: List[UnitRule] = []
    code_choices: Optional[dict] = None             # PR-Y: {code: {as: alias|new, item}}
    alias_system: Optional[str] = "acc"


@router.post("/api/bom-center/prices/import", dependencies=[Depends(require_write_token_unless_dry_run)])
async def bc_prices_import(request: Request, file: UploadFile = File(...), month: Optional[str] = Query(None),
                           dry_run: bool = Query(True), add_unknown: bool = Query(False),
                           rules: Optional[str] = Form(None), code_choices: Optional[str] = Form(None),
                           alias_system: str = Form("acc")):
    """A month of purchase lines -> weighted-average actual price per ingredient.
    `rules` (a JSON list in the form) are the pack guard's suggestions the person ticked."""
    if month:
        _month_or_400(month)
    rl = _rules_or_400(rules)
    lines, info, month = await _price_lines_from_file(file, month)
    return _import(lines, info, month, dry_run, _who(request), f"ไฟล์ {file.filename}", add_unknown, rules=rl,
                   choices=_choices_or_400(code_choices), can_alias=_can_alias(request), alias_system=alias_system)


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
    return _import(lines, {"format": "รายงานจ่ายสินค้าภายใน (ที่นำเข้าไว้)"}, month, dry_run, _who(request),
                   "จากรายงานจ่ายสินค้าภายในที่นำเข้าไว้", add_unknown, rules=rl,
                   choices=_choices_or_400(body.code_choices if body else None), can_alias=_can_alias(request),
                   alias_system=(body.alias_system if body else None) or "acc")


@router.get("/api/bom-center/prices/requisition-months")
def bc_requisition_months():
    with get_db() as con:
        has = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='requisition_line'").fetchone()
        return {"months": [r[0] for r in con.execute(
            "SELECT DISTINCT period_month FROM requisition_line ORDER BY 1 DESC")] if has else []}


# ── PR-D: Sales Support sends the month's price file, the analyst reviews and imports it ──
def _parse_name(filename: Optional[str], mime: str) -> str:
    """The file name the price reader decides by, matched to what the bytes really are."""
    fn = (filename or "").strip() or "file"
    if mime == _ps.CSV_MIME:
        return fn if fn.lower().endswith(".csv") else fn + ".csv"
    return fn if not fn.lower().endswith(".csv") else fn[:-4] + ".xlsx"


def _sub_summary(res: dict) -> dict:
    """What the analyst needs at a glance, kept with the file."""
    return {"format": (res.get("source") or {}).get("format"), "lines": res.get("lines"),
            "priced": res.get("priced"), "value_total": res.get("value_total"), "counts": res.get("counts"),
            "issues": len(res.get("issues") or []), "unknown_codes": (res.get("unknown_codes") or [])[:50]}


@router.post("/api/bom-center/prices/submit", dependencies=[Depends(require_write_token)])
async def bc_prices_submit(request: Request, file: UploadFile = File(...), month: Optional[str] = Form(None),
                           note: Optional[str] = Form(None)):
    """Sales Support: send the month's price file to the analyst. The file must be readable (the same reader
    as the import, run as a dry run); nothing is priced until the analyst imports it."""
    _month_or_400(month)
    raw = await file.read(_ps.MAX_BYTES + 1)
    if not raw:
        raise HTTPException(400, "ไฟล์ไม่มีข้อมูล")
    if len(raw) > _ps.MAX_BYTES:
        raise HTTPException(400, "ไฟล์ใหญ่เกิน 12 MB")
    mime = _ps.sniff_xlsx(raw)
    if not mime:
        raise HTTPException(400, "ไฟล์ต้องเป็น Excel (.xlsx) หรือ CSV")
    lines, info, month = await _price_lines_from_bytes(raw, _parse_name(file.filename, mime), month)
    who = _who(request)
    res = _import(lines, info, month, True, who, "ตรวจไฟล์ก่อนส่ง")
    with get_db() as con:
        _ensure(con)
        out = _write(con, lambda: _ps.create(con, month, file.filename, raw, note, who, _sub_summary(res)))
    return {**out, "summary": _sub_summary(res), "unknown_items": res.get("unknown_items") or []}


@router.get("/api/bom-center/prices/submissions")
def bc_price_submissions(status: Optional[str] = Query(None), month: Optional[str] = Query(None),
                         limit: int = Query(50)):
    if status and status not in _ps.STATUS_TH:
        raise HTTPException(400, "คำขอไม่ถูกต้อง")
    if month:
        _month_or_400(month)
    with get_db() as con:
        _ensure(con)
        return {"submissions": _ps.listing(con, status, month, limit), "counts": _ps.counts(con),
                "status_th": _ps.STATUS_TH}


@router.get("/api/bom-center/prices/submissions/{sub_id}/file")
def bc_price_submission_file(sub_id: int):
    from urllib.parse import quote
    with get_db() as con:
        _ensure(con)
        try:
            fn, mime, data = _ps.blob(con, sub_id)
        except ValueError as e:
            raise HTTPException(404, str(e))
    return Response(content=data, media_type=mime,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fn)}",
                             "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@router.post("/api/bom-center/prices/submissions/{sub_id}/import",
             dependencies=[Depends(require_write_token_unless_dry_run)])
async def bc_price_submission_import(sub_id: int, request: Request, dry_run: bool = Query(True),
                                     add_unknown: bool = Query(False), rules: Optional[str] = Form(None),
                                     code_choices: Optional[str] = Form(None), alias_system: str = Form("acc")):
    """The analyst reviews (dry run) and imports the file Sales Support sent: the same import as an upload."""
    rl = _rules_or_400(rules)
    with get_db() as con:
        _ensure(con)
        h = _ps.head(con, sub_id)
        if not h:
            raise HTTPException(404, "ไม่พบไฟล์ราคาที่ส่งมา")
        if h["status"] != "pending":
            raise HTTPException(400, f"ไฟล์นี้พิจารณาแล้ว ({_ps.STATUS_TH.get(h['status'], h['status'])})")
        fn, mime, raw = _ps.blob(con, sub_id)
    lines, info, month = await _price_lines_from_bytes(raw, _parse_name(fn, mime), h["month"])
    who = _who(request)
    out = _import(lines, info, month, dry_run, who, f"ไฟล์จากประสานงานขาย {fn}", add_unknown, rules=rl,
                  choices=_choices_or_400(code_choices), can_alias=_can_alias(request), alias_system=alias_system)
    if not dry_run:
        with get_db() as con:
            _ensure(con)
            _write(con, lambda: _ps.decide(con, sub_id, "imported", who, None, out.get("applied")))
        out["submission"] = {"sub_id": sub_id, "status": "imported"}
    return out


class RejectSubmission(BaseModel):
    note: Optional[str] = None


@router.post("/api/bom-center/prices/submissions/{sub_id}/reject", dependencies=[Depends(require_write_token)])
def bc_price_submission_reject(sub_id: int, body: RejectSubmission, request: Request):
    """The analyst does not accept the file: a reason is required, and Sales Support reads it."""
    with get_db() as con:
        _ensure(con)
        out = _write(con, lambda: _ps.decide(con, sub_id, "rejected", _who(request), body.note))
    return out


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
                           "   AND c.check_code='unmatched'),"
                           " submitted_by, submitted_at, notes"
                           " FROM bc_release b ORDER BY (kind='baseline'), effective_month DESC, release_id DESC").fetchall()
        priced = [r[0] for r in con.execute("SELECT DISTINCT month FROM bc_ingredient_price ORDER BY month DESC")]
        # costs follow ACTUAL purchase prices (owner decision): suggest the latest month the accounts
        # were imported for — not a later supplier quote's month (DATAP is dated 2026-07, actuals June)
        actual = con.execute("SELECT MAX(month) FROM bc_ingredient_price"
                             " WHERE source IN ('purchase_avg','manual')").fetchone()[0]
    cols = ("release_id", "label", "kind", "effective_month", "status", "scope", "price_month", "created_by",
            "created_at", "released_by", "released_at", "acknowledged_by", "acknowledged_at", "recipes", "unmatched",
            "submitted_by", "submitted_at", "notes")
    rels = [dict(zip(cols, r)) for r in rows]
    for r in rels:               # what the version waits for (หน้างาน and the pane read it)
        r["next_step"] = ("submit" if r["kind"] == "monthly" and r["status"] == "draft"
                          else "release" if r["kind"] == "monthly" and r["status"] == "review"
                          else "acknowledge" if r["status"] == "released" and not r["acknowledged_at"] else None)
    return {"releases": rels, "price_months": priced,
            "suggest": {"effective_month": _next_month(_this_month()),
                        "price_month": actual or (priced[0] if priced else None)}}


@router.get("/api/bom-center/recipes")
def bc_recipes(release_id: int = Query(...), tier: Optional[int] = Query(None)):
    with get_db() as con:
        _ensure(con)
        if not _r.release_row(con, release_id):
            raise HTTPException(404, "ไม่พบเวอร์ชัน")
        return _r.tier_view(con, release_id, tier)


@router.get("/api/bom-center/recipes-export.xlsx")
def bc_recipes_export(release_id: int = Query(...)):
    with get_db() as con:
        _ensure(con)
        rel = _r.release_row(con, release_id)
        if not rel:
            raise HTTPException(404, "ไม่พบเวอร์ชัน")
        wb = _r.export_flat(con, release_id)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", rel["label"])
    return _xlsx(wb, f"BOM_RECIPES_{safe}.xlsx")


# declared before /recipes/{sku_code}: "template.xlsx" would otherwise be read as a SKU code
@router.get("/api/bom-center/recipes/template.xlsx")
def bc_recipes_template(release_id: Optional[int] = Query(None)):
    """The blank multi-menu recipe template for the kitchen (many menus in one file)."""
    with get_db() as con:
        _ensure(con)
        wb = _r.template_flat(con, release_id)
    return _xlsx(wb, "BOM_RECIPES_TEMPLATE.xlsx")


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
            raise HTTPException(404, "ไม่พบเวอร์ชัน")
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


def _drop_bom_days() -> None:
    """A recipe-side input changed (release, recipes, sub-recipes, code links, units ...): the issue reports'
    cached BoM days (bcd_demand_day) are rebuilt on their next read. Next to every forecast.clear_usage_cache()."""
    with get_db() as con:
        _ensure(con)
        _write(con, lambda: _ir.drop_bom_days(con))


@router.put("/api/bom-center/recipes/{sku_code}", dependencies=[Depends(require_write_token)])
def bc_recipe_save(sku_code: str, lines: List[RecipeLine], request: Request, release_id: int = Query(...)):
    mods = _modules(request)
    with get_db() as con:
        _ensure(con)
        _write(con, lambda: _r.save_lines(con, release_id, sku_code, [ln.dict() for ln in lines], _who(request),
                                          modules=mods))
        out = _r.cost_recipe(con, release_id, sku_code)
    invalidate_cache("bc_")
    _fc.clear_usage_cache()           # the order plan's usage model reads this
    _drop_bom_days()             # … and so does the BoM day cache of the issue reports
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
        raise HTTPException(400, "ไม่พบแผ่นงานสูตรบุฟเฟ่ต์ (BF_…NET) ในไฟล์นี้")
    who = _who(request)
    mods = _modules(request)
    with get_db() as con:
        _ensure(con)
        pm = price_month
        if not pm:
            r = con.execute("SELECT MAX(month) FROM bc_ingredient_price WHERE source IN ('purchase_avg','manual')").fetchone()
            pm = r[0] if r and r[0] else con.execute("SELECT MAX(month) FROM bc_ingredient_price").fetchone()[0]
        if not pm:
            raise HTTPException(400, "ยังไม่มีราคาวัตถุดิบ · นำเข้าทะเบียนหรือราคาซื้อจริงก่อน")
        _month_or_400(pm)
        lbl = label or f"{eff} บุฟเฟ่ต์ (ร่าง)"
        try:
            rid = _r.get_or_create_draft(con, lbl, eff, pm, who, modules=mods)
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
        _fc.clear_usage_cache()           # the order plan's usage model reads this
        _drop_bom_days()             # … and so does the BoM day cache of the issue reports
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
    mods = _modules(request)
    with get_db() as con:
        _ensure(con)
        out = _write(con, lambda: _r.apply_mapping(con, body.release_id, body.ref, body.sku_code.strip(),
                                                    _who(request), modules=mods))
    invalidate_cache("bc_")
    _fc.clear_usage_cache()           # the order plan's usage model reads this
    _drop_bom_days()             # … and so does the BoM day cache of the issue reports
    return {"ok": True, **out}


@router.post("/api/bom-center/recipes/import-flat", dependencies=[Depends(require_write_token_unless_dry_run)])
async def bc_recipes_import_flat(request: Request, file: UploadFile = File(...), release_id: int = Query(...),
                                 dry_run: bool = Query(True)):
    raw = await file.read()
    mods = _modules(request)
    with get_db() as con:
        _ensure(con)
        try:
            out = _r.import_flat(con, raw, release_id, _who(request), modules=mods)
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
        _fc.clear_usage_cache()           # the order plan's usage model reads this
        _drop_bom_days()             # … and so does the BoM day cache of the issue reports
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
            raise HTTPException(404, "ไม่พบเวอร์ชัน")
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
            raise HTTPException(404, "ไม่พบเวอร์ชัน")
        out = _rl.compare(con, release_id, month)
    out["dishes"] = out["dishes"][:300]
    return out


@router.post("/api/bom-center/release-act/check", dependencies=[Depends(require_write_token)])
def bc_release_run_checks(release_id: int = Query(...)):
    with get_db() as con:
        _ensure(con)
        rel = _r.release_row(con, release_id)
        if not rel or rel["kind"] != "monthly":
            raise HTTPException(404, "ไม่พบเวอร์ชันรายเดือน")
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
            raise HTTPException(404, "ไม่พบเวอร์ชัน")
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
    _fc.clear_usage_cache()           # the order plan's usage model reads this
    _drop_bom_days()             # … and so does the BoM day cache of the issue reports
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
    _fc.clear_usage_cache()           # the order plan's usage model reads this
    _drop_bom_days()             # … and so does the BoM day cache of the issue reports
    return {"ok": True}


class NewDraftBody(BaseModel):
    from_release_id: Optional[int] = None        # None = a fresh draft (the kitchen builds it from the template)
    effective_month: str
    price_month: str
    label: Optional[str] = None


@router.post("/api/bom-center/release-act/new-draft", dependencies=[Depends(require_write_token)])
def bc_release_new_draft(body: NewDraftBody, request: Request):
    _month_or_400(body.effective_month)
    _month_or_400(body.price_month)
    label = body.label or f"{body.effective_month} บุฟเฟ่ต์ (ร่าง)"
    mods = _modules(request)
    who = _who(request)

    def go():
        if body.from_release_id is not None:
            return _rl.new_draft_from(con, body.from_release_id, label, body.effective_month, body.price_month, who)
        existed = con.execute("SELECT 1 FROM bc_release WHERE label=?", (label,)).fetchone()
        rid = _r.get_or_create_draft(con, label, body.effective_month, body.price_month, who, modules=mods)
        if not existed:
            _m.audit(con, who, "new_draft", "bc_release", str(rid), None, body.effective_month)
        return rid

    with get_db() as con:
        _ensure(con)
        rid = _write(con, go)
    invalidate_cache("bc_")
    _fc.clear_usage_cache()           # the order plan's usage model reads this
    _drop_bom_days()             # … and so does the BoM day cache of the issue reports
    return {"ok": True, "release_id": rid, "label": label}


class ReleaseIdBody(BaseModel):
    release_id: int


@router.post("/api/bom-center/release-act/submit-review", dependencies=[Depends(require_write_token)])
def bc_release_submit_review(body: ReleaseIdBody, request: Request):
    """The kitchen hands a finished draft to the analyst."""
    with get_db() as con:
        _ensure(con)
        _write(con, lambda: _rl.submit_review(con, body.release_id, _who(request)))
    invalidate_cache("bc_")
    _fc.clear_usage_cache()           # the order plan's usage model reads this
    _drop_bom_days()             # … and so does the BoM day cache of the issue reports
    return {"ok": True}


@router.post("/api/bom-center/release-act/send-back", dependencies=[Depends(require_write_token)])
def bc_release_send_back(body: NoteBody, request: Request):
    """The analyst returns a release in review to the kitchen, with the reason."""
    with get_db() as con:
        _ensure(con)
        _write(con, lambda: _rl.send_back(con, body.release_id, body.note or "", _who(request)))
    invalidate_cache("bc_")
    _fc.clear_usage_cache()           # the order plan's usage model reads this
    _drop_bom_days()             # … and so does the BoM day cache of the issue reports
    return {"ok": True}


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
    from bom_center.clock import today as _bkk_today
    if not d:
        return _bkk_today()                       # the Thai calendar day, not the UTC server's
    try:
        return _d.fromisoformat(d)
    except ValueError:
        raise HTTPException(400, "วันที่ไม่ถูกต้อง")


@router.get("/api/bom-center/order-calendar")
def bc_order_calendar():
    with get_db() as con:
        _ensure(con)
        rows = []
        for bid, name in con.execute("SELECT branch_id, name FROM branches WHERE name != 'ALL'"
                                     " AND COALESCE(active, 1) = 1 ORDER BY name"):
            cal = _fc.calendar(con, bid)
            spec = _pv.reference_spec(con, bid)
            rows.append({"branch_id": bid, "branch": name, "ref_branch_id": _pv.reference_for(con, bid),
                         "ref_mode": spec["mode"],           # PR-X: 'benchmark' (the default) / 'branch' / 'none'
                         **{f"r{k}": v for k, v in cal.items()}})
        bm = _bm.settings(con)
    from bom_center.pilot import PILOT                       # the page can show the pilot branches alone
    return {"rows": rows, "weekdays": _fc.WD_TH, "defaults": _fc.DEFAULT_CAL, "pilot": list(PILOT),
            "cutoff_time": f"{_fc.cutoff_time():%H:%M}", "new_branch_days": _pv.NEW_BRANCH_DAYS,
            "benchmark": {"label": bm["label"], "selected": bm["selected"],
                          "names": [b["name"] for b in bm["branches"] if b["selected"]]}}


class RefBranchBody(BaseModel):
    branch_id: int
    ref_branch_id: Optional[int] = None         # a branch = that one branch; None = the default, the benchmark (PR-X) ...
    ref_mode: Optional[str] = None              # ... unless 'none' (no reference: today's behaviour) is asked for


@router.post("/api/bom-center/order-calendar/ref-branch", dependencies=[Depends(require_write_token)])
def bc_order_calendar_ref(body: RefBranchBody, request: Request):
    """สาขาอ้างอิงสำหรับสาขาใหม่ (PR-V): a branch with fewer than 14 trading days of sales is planned from this branch's usage
    per ฿100,000 of sales x the sales it expects. app_settings `bc_ref_branch`; the sales team's, like the calendar."""
    with get_db() as con:
        _ensure(con)
        try:
            if body.ref_mode not in (None, "", "none", "benchmark", "branch"):
                raise HTTPException(400, "รูปแบบสาขาอ้างอิงไม่ถูกต้อง")
            out = _write(con, lambda: _pv.set_reference(con, _who(request), body.branch_id, body.ref_branch_id,
                                                        mode="none" if body.ref_mode == "none" else None))
        except ValueError as e:
            raise HTTPException(400, str(e))
    invalidate_cache("bc_")
    return out


# ── PR-X · ค่ากลางจากสาขาของบริษัท (benchmark.py): which branches count, and the temporary stock buffer ──
class BenchmarkBody(BaseModel):
    branch_ids: Optional[List[int]] = None       # None = leave the set as it is
    buffer_pct: Optional[float] = None           # None = leave the buffer as it is


@router.get("/api/bom-center/benchmark")
def bc_benchmark(request: Request):
    """The settings card: every branch (with its type) and whether it counts as a company branch, the stock buffer and the
    default four. The centre's (supply.ordering / bom.center); a branch login never reaches it (auth + this check)."""
    if _auth.scope_branch(request):
        raise HTTPException(403, "ผู้ใช้ของสาขาไม่มีสิทธิ์ดูค่ากลางของสาขาอื่น")
    with get_db() as con:
        _ensure(con)
        return _bm.settings(con)


@router.post("/api/bom-center/benchmark", dependencies=[Depends(require_write_token)])
def bc_benchmark_save(body: BenchmarkBody, request: Request):
    """Save the branches that make the benchmark and / or the buffer % (0-50). The sales team's, like the calendar."""
    with get_db() as con:
        _ensure(con)
        out = _write(con, lambda: _bm.set_benchmark(con, _who(request), body.branch_ids, body.buffer_pct))
    invalidate_cache("bc_")
    return out


class CalRow(BaseModel):
    branch_id: int
    round: str
    delivery_weekday: Optional[int] = None       # SQLite %w: Sunday = 0 … Saturday = 6 — the only value the sales team sets
    order_weekday: Optional[int] = None          # ignored: derived from the delivery day (kept so an old client still posts)
    lead_days: Optional[int] = None              # ignored: derived


def _calendar_put(con, who: str, branch_id: int, rnd: str, dw: int, old=None) -> None:
    """One (branch, round) delivery day. The single write path of the page save and the file import:
    the cutoff weekday and the lead are derived, and the change is audited."""
    from datetime import datetime as _dt, timezone as _tz
    if rnd not in _fc.DEFAULT_CAL:
        raise ValueError(f"ไม่พบรอบ {rnd}")
    if dw is None or not (0 <= int(dw) <= 6):
        raise ValueError("ระบุวันส่งของรอบ (อาทิตย์–เสาร์)")
    dw = int(dw)
    cw = _fc.cutoff_weekday(dw)                          # the weekday the branch has to order by (13:15)
    con.execute("INSERT OR REPLACE INTO bc_order_calendar(branch_id, round, order_weekday, delivery_weekday,"
                " lead_days, filled_by, updated_at) VALUES(?,?,?,?,?,?,?)",
                (branch_id, rnd, cw, dw, (dw - cw) % 7, who, _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%S")))
    _m.audit(con, who, "order_calendar", "bc_order_calendar", f"{branch_id}|{rnd}",
             None if old is None else f"delivery {old}", f"delivery {dw} / cutoff {cw}")


@router.post("/api/bom-center/order-calendar", dependencies=[Depends(require_write_token)])
def bc_order_calendar_save(rows: List[CalRow], request: Request):
    who = _who(request)

    def go():
        for r in rows:
            _calendar_put(con, who, r.branch_id, r.round, r.delivery_weekday)
    with get_db() as con:
        _ensure(con)
        _write(con, go)
    return {"ok": True, "rows": len(rows)}


# ── bulk delivery days from a file (30-branch onboarding): template + dry-run preview + save ──
_CAL_ROUNDS = ("1", "2", "salmon")
_CAL_HEADERS = ["สาขา", "รอบ 1 วันส่ง", "รอบ 2 วันส่ง", "แซลมอนสด วันส่ง"]
_CAL_ALIASES = {
    "branch": ("สาขา", "branch"),
    "1":      ("รอบ 1 วันส่ง", "รอบ 1", "round 1", "r1"),
    "2":      ("รอบ 2 วันส่ง", "รอบ 2", "round 2", "r2"),
    "salmon": ("แซลมอนสด วันส่ง", "แซลมอนสด", "แซลมอน", "salmon"),
}


def _calendar_branches(con) -> list:
    """[(branch_id, name)] of the active branches, as the calendar page lists them."""
    return [(b, n) for b, n in con.execute("SELECT branch_id, name FROM branches WHERE name != 'ALL'"
                                           " AND COALESCE(active, 1) = 1 ORDER BY name")]


@router.get("/api/bom-center/order-calendar/template.xlsx")
def bc_order_calendar_template():
    """Every active branch with its delivery days as they stand now, to edit and send back."""
    from openpyxl import Workbook
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook()
    ws = wb.active
    ct = f"{_fc.cutoff_time():%H:%M}"
    _sheet(ws, "วันส่งของสาขา", _CAL_HEADERS,
           f"ตั้งวันส่งของแต่ละรอบรายสาขา เลือกหรือพิมพ์ อาทิตย์ จันทร์ อังคาร พุธ พฤหัส ศุกร์ เสาร์ · เว้นว่างหมายถึงไม่เปลี่ยน · "
           f"ไม่ต้องกรอกเวลาตัดรอบ (คำนวณจากวันส่ง): สาขาต้องส่งใบสั่งซื้อก่อน {ct} สองวันก่อนวันส่ง "
           f"(รอบส่งวันอังคาร ตัดรอบวันเสาร์ {ct}) · ห้ามแก้ชื่อสาขา")
    with get_db() as con:
        _ensure(con)
        for bid, name in _calendar_branches(con):
            cal = _fc.calendar(con, bid)
            ws.append([name] + [_fc.WD_TH[cal[k]["delivery_weekday"]] for k in _CAL_ROUNDS])
        last = ws.max_row
    dv = DataValidation(type="list", formula1='"' + ",".join(_fc.WD_TH) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"B3:D{max(last, 3)}")
    ws.column_dimensions["A"].width = 24
    ws.freeze_panes = "B3"
    ref = wb.create_sheet("วันส่งและวันตัดรอบ")                 # a lookup for the person filling the file
    ref.append(["วันส่ง", f"วันตัดรอบ (เวลา {ct})"])
    for wd in range(7):
        ref.append([_fc.WD_TH[wd], _fc.WD_TH[_fc.cutoff_weekday(wd)]])
    ref.column_dimensions["A"].width = 14
    ref.column_dimensions["B"].width = 26
    return _xlsx(wb, "ORDER_CALENDAR_TEMPLATE.xlsx")


def _branch_key(name) -> str:
    t = str(name or "").strip()
    return t[len("สาขา"):].strip() if t.startswith("สาขา") else t


def _calendar_plan(con, rows: list, hmap: dict) -> dict:
    """The file's rows against the calendar as it stands. Reads only; nothing is written here.

    A blank cell leaves that round as it is. A row counts as a change when its day differs from the current one."""
    branches = _calendar_branches(con)
    by_name = {_branch_key(n): (b, n) for b, n in branches}
    all_names = {_branch_key(n): (b, n, a) for b, n, a in con.execute(
        "SELECT branch_id, name, COALESCE(active, 1) FROM branches WHERE name != 'ALL'")}
    out, errors, seen = [], [], set()
    for i, r in enumerate(rows):
        line = i + 1
        raw_branch = r.get("branch")
        if raw_branch in (None, "") or not str(raw_branch).strip():
            continue
        key = _branch_key(raw_branch)
        label = str(raw_branch).strip()
        if key not in by_name:
            why = "สาขานี้ปิดใช้งานอยู่" if key in all_names else "ไม่พบสาขานี้ในระบบ ตรวจชื่อให้ตรงกับที่ระบบแสดง"
            errors.append({"line": line, "branch": label, "why": why})
            continue
        bid, name = by_name[key]
        if bid in seen:
            errors.append({"line": line, "branch": name, "why": "สาขานี้ปรากฏซ้ำในไฟล์ · เหลือแถวเดียว"})
            continue
        seen.add(bid)
        cal = _fc.calendar(con, bid)
        row = {"branch_id": bid, "branch": name, "change": False, "errors": 0}
        for k in _CAL_ROUNDS:
            was = int(cal[k]["delivery_weekday"])
            try:
                new = _fc.parse_weekday(r.get(k)) if k in hmap else None
            except ValueError as e:
                errors.append({"line": line, "branch": name, "round": k, "why": f"{_fc.ROUND_TH[k]}: {e}"})
                row["errors"] += 1
                new = None
            now = was if new is None else new
            changed = new is not None and new != was
            row[f"r{k}"] = {"was": was, "now": now, "was_th": _fc.WD_TH[was], "now_th": _fc.WD_TH[now],
                            "cutoff_th": f"วัน{_fc.WD_TH[_fc.cutoff_weekday(now)]} {_fc.cutoff_time():%H:%M}",
                            "changed": changed, "default": not cal[k]["filled"]}
            row["change"] = row["change"] or changed
        out.append(row)
    return {"rows": out, "errors": errors}


@router.post("/api/bom-center/order-calendar/import", dependencies=[Depends(require_write_token_unless_dry_run)])
async def bc_order_calendar_import(request: Request, file: UploadFile = File(...), dry_run: bool = Query(True)):
    """Delivery days for many branches from the template. dry_run=true returns what would change and writes
    nothing; a real save writes the changed rounds only, and refuses a file that has errors."""
    from .ingredients import _read_upload_table
    rows, hmap, sheet = await _read_upload_table(file, _CAL_ALIASES, optional=_CAL_ROUNDS)
    if "branch" not in hmap or not any(k in hmap for k in _CAL_ROUNDS):
        raise HTTPException(400, "ไม่พบคอลัมน์ สาขา และวันส่ง · ใช้แม่แบบที่ดาวน์โหลดจากหน้านี้")
    who = _who(request)
    with get_db() as con:
        _ensure(con)
        plan = _calendar_plan(con, rows, hmap)
        changed = [r for r in plan["rows"] if r["change"]]
        n_rounds = sum(1 for r in changed for k in _CAL_ROUNDS if r[f"r{k}"]["changed"])
        out = {"dry_run": dry_run, "sheet": sheet, "rows": plan["rows"], "errors": plan["errors"],
               "branches": len(plan["rows"]), "changed_branches": len(changed), "changed_rounds": n_rounds,
               "saved": 0}
        if dry_run:
            return out
        if plan["errors"]:
            raise HTTPException(400, f"ไฟล์มี {len(plan['errors'])} รายการที่ไม่ถูกต้อง · แก้ไฟล์แล้วนำเข้าใหม่ "
                                     f"(แถวแรก: {plan['errors'][0]['branch']} — {plan['errors'][0]['why']})")

        def go():
            for r in changed:
                for k in _CAL_ROUNDS:
                    if r[f"r{k}"]["changed"]:
                        _calendar_put(con, who, r["branch_id"], k, r[f"r{k}"]["now"], old=r[f"r{k}"]["was"])
            _m.audit(con, who, "order_calendar_import", "bc_order_calendar", file.filename, None,
                     f"{len(changed)} branches / {n_rounds} rounds")
        _write(con, go)
        out["saved"] = n_rounds
    return out


@router.get("/api/bom-center/forecast")
def bc_forecast(branch_id: int = Query(...), round: str = Query("1"), order_day: Optional[str] = Query(None),
                weeks: int = Query(6, ge=2, le=12), season: str = Query("auto")):
    if round not in ("1", "2", "salmon"):
        raise HTTPException(400, "รอบต้องเป็น 1, 2 หรือแซลมอนสด")
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
        raise HTTPException(400, "รอบต้องเป็น 1, 2 หรือแซลมอนสด")
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
           "หนึ่งแถวต่อวัตถุดิบ 1 บรรทัด · batch_yield_qty และ batch_unit = ปริมาณที่ได้ต่อ 1 สูตร · นำเข้าแล้วสูตรของรหัสนั้นถูกแทนที่ทั้งชุด")
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
    no_loss: Optional[int] = 0


class PrepBody(BaseModel):
    yield_qty: float
    yield_unit: str
    lines: List[PrepLine]
    mode: Optional[str] = None                   # 'portion' keeps a portion recipe a portion recipe when the generic editor saves it
    note: Optional[str] = None


class PortionBody(BaseModel):
    code: str                                    # the prepared item (existing, or a new code with `name`)
    name: Optional[str] = None                   # required when the code is not in the master yet
    raw_code: str
    qty: float
    unit: str
    pieces: float
    out_unit: Optional[str] = "ชิ้น"
    note: Optional[str] = None


@router.get("/api/bom-center/prep/portion/preview")
def bc_prep_portion_preview(raw_code: str = Query(...), qty: float = Query(...), unit: str = Query(...),
                            pieces: float = Query(...), month: Optional[str] = Query(None)):
    """'1 ตัว of 02-01-001 -> 225 ชิ้น': the factor, whether it is approximate, grams per piece and the cost per piece."""
    with get_db() as con:
        _ensure(con)
        try:
            return _pr.portion_preview(con, raw_code, qty, unit, pieces, _prep_month(con, month))
        except ValueError as e:
            raise HTTPException(400, str(e))


@router.post("/api/bom-center/prep/portion", dependencies=[Depends(require_write_token)])
def bc_prep_portion_save(body: PortionBody, request: Request):
    with get_db() as con:
        _ensure(con)
        out = _write(con, lambda: _pr.save_portion(con, body.code, body.raw_code, body.qty, body.unit, body.pieces,
                                                    body.out_unit, _who(request), note=body.note, name=body.name,
                                                    month=_prep_month(con, None)))
    invalidate_cache("bc_")
    _fc.clear_usage_cache()           # the order plan's usage model reads this
    _drop_bom_days()             # … and so does the BoM day cache of the issue reports
    return {"ok": True, **out}


@router.put("/api/bom-center/prep/{code}", dependencies=[Depends(require_write_token)])
def bc_prep_save(code: str, body: PrepBody, request: Request):
    with get_db() as con:
        _ensure(con)
        n = _write(con, lambda: _pr.save(con, code, body.yield_qty, body.yield_unit,
                                          [ln.dict() for ln in body.lines], _who(request), source_ref="แก้ไขในหน้าเว็บ",
                                          mode=body.mode, note=body.note))
    invalidate_cache("bc_")
    _fc.clear_usage_cache()           # the order plan's usage model reads this
    _drop_bom_days()             # … and so does the BoM day cache of the issue reports
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
    _fc.clear_usage_cache()           # the order plan's usage model reads this
    _drop_bom_days()             # … and so does the BoM day cache of the issue reports
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
    _fc.clear_usage_cache()           # the order plan's usage model reads this
    _drop_bom_days()             # … and so does the BoM day cache of the issue reports
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
            raise HTTPException(403, detail="ผู้ใช้นี้ผูกกับสาขาที่ไม่พบ · แจ้งผู้ดูแลระบบ", headers={"X-Denied": "role"})
        if branch_id is not None and int(branch_id) != int(r[0]):
            raise HTTPException(403, detail="บัญชีสาขาเข้าถึงได้เฉพาะสาขาของตัวเอง", headers={"X-Denied": "role"})
        return int(r[0])
    if branch_id is None:
        raise HTTPException(400, "เลือกสาขา")
    if not con.execute("SELECT 1 FROM branches WHERE branch_id=?", (branch_id,)).fetchone():
        raise HTTPException(404, "ไม่พบสาขา")
    return int(branch_id)


@router.get("/api/bom-center/stock/me")
def bc_stock_me(request: Request, month: Optional[str] = Query(None)):
    """Who is counting: a branch login gets its branch; the centre gets the branch list. With `month` (YYYY-MM, PR-M5) the
    list holds only the branches that have a slip, order, count or receipt dated in that month."""
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
        elif month:
            try:
                with_data = _st.branches_with_data(con, month)
            except ValueError as e:
                raise HTTPException(400, str(e))
            branches = [b for b in branches if b["branch_id"] in with_data]
        return {"scoped": bool(scoped), "branch": scoped, "branches": branches,
                "zones": [{"zone": z, "label": t} for z, t in _st.zones(con)]}


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
def bc_stock_counts(request: Request, branch_id: Optional[int] = Query(None), limit: int = Query(20),
                    kind: Optional[str] = Query(None)):
    """`kind=month` lists the month-end counts (begin / end) only; omitted = every count, spot ones included."""
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        return {"branch_id": bid, "counts": _st.history(con, bid, max(1, min(limit, 200)),
                                                       kinds=_st.MONTH_KINDS if kind == "month" else None)}


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


# PR-K: the storage zones (โซนจัดเก็บ) — the centre adds, renames, deletes and reorders them
class ZoneRow(BaseModel):
    code: Optional[str] = None          # stable; blank on a new zone (the system makes one)
    label: str


def _zones_payload(con) -> dict:
    use = _st.zone_usage(con)
    return {"zones": [{"code": z, "label": t, "items": use.get(z, 0), "fallback": z == _st.FALLBACK_ZONE}
                      for z, t in _st.zones(con)],
            "defaults": [{"code": z, "label": t} for z, t in _st.DEFAULT_ZONES]}


@router.get("/api/bom-center/zones")
def bc_zones():
    with get_db() as con:
        _ensure(con)
        return _zones_payload(con)


@router.post("/api/bom-center/zones", dependencies=[Depends(require_write_token)])
def bc_zones_save(rows: List[ZoneRow], request: Request):
    """Replace the zone list in the order given. A zone that still holds ingredients cannot be deleted."""
    with get_db() as con:
        _ensure(con)
        _write(con, lambda: _st.set_zones(con, [r.dict() for r in rows], _who(request)))
        out = _zones_payload(con)
    invalidate_cache("bc_")
    return {"ok": True, **out}


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


# ── ใบตัดเบิก (issues.py): the branch's daily issue slip — used + wasted per item, confirmed once a day ──
class IssueLine(BaseModel):
    code: str
    qty_used: Optional[float] = None        # in `unit` (default: the unit the item is counted in)
    qty_waste: Optional[float] = None
    waste_reason: Optional[str] = None      # required when qty_waste > 0 (issues.WASTE_REASONS)
    unit: Optional[str] = None
    note: Optional[str] = None


class IssueBody(BaseModel):
    date: Optional[str] = None              # the slip's day; omitted = today (Bangkok)
    lines: List[IssueLine] = []
    submit: bool = False                    # confirm the slip
    note: Optional[str] = None


class IssueAppendBody(BaseModel):
    date: Optional[str] = None              # the slip's day (must be today and confirmed); omitted = today (Bangkok)
    lines: List[IssueLine] = []             # quantities ADDED to the confirmed slip of the day (PR-M7)


class IssueReopenBody(BaseModel):
    slip_id: int
    note: str                               # why the confirmed slip is opened again (logged)


@router.get("/api/bom-center/issues/sheet")
def bc_issue_sheet(request: Request, branch_id: Optional[int] = Query(None), date: Optional[str] = Query(None)):
    """The list a branch issues from on one day, with the system stock before that day's issue."""
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        try:
            return _is.sheet(con, bid, date or bkk_today().isoformat())
        except ValueError as e:
            raise HTTPException(400, str(e))


@router.post("/api/bom-center/issues/save", dependencies=[Depends(require_write_token)])
def bc_issue_save(body: IssueBody, request: Request, branch_id: Optional[int] = Query(None)):
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        return _write(con, lambda: _is.save(con, bid, body.date or bkk_today().isoformat(),
                                            [ln.dict() for ln in body.lines], _who(request), submit=body.submit,
                                            note=body.note))


@router.post("/api/bom-center/issues/append", dependencies=[Depends(require_write_token)])
def bc_issue_append(body: IssueAppendBody, request: Request, branch_id: Optional[int] = Query(None)):
    """PR-M7 · เบิกเพิ่ม: add lines to the slip of TODAY that is already confirmed (no reopen). Logged as 'append'."""
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        return _write(con, lambda: _is.append(con, bid, body.date or bkk_today().isoformat(),
                                              [ln.dict() for ln in body.lines], _who(request)))


@router.get("/api/bom-center/issues/status")
def bc_issue_status(request: Request, branch_id: Optional[int] = Query(None), date_from: Optional[str] = Query(None),
                    date_to: Optional[str] = Query(None)):
    """One status per calendar day (confirmed / late / draft / missing) — the day strip of the branch page."""
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        d1 = _day_or_today(date_to)
        d0 = _day_or_today(date_from) if date_from else d1 - timedelta(days=6)
        if d0 > d1 or (d1 - d0).days > 92:
            raise HTTPException(400, "ช่วงวันที่ต้องไม่เกิน 92 วัน")
        return {"branch_id": bid, "date_from": d0.isoformat(), "date_to": d1.isoformat(),
                "days": _is.status_days(con, bid, d0, d1)}


@router.post("/api/bom-center/issues/reopen", dependencies=[Depends(require_write_token)])
def bc_issue_reopen(body: IssueReopenBody, request: Request):
    """Sales Support (or the analyst) opens a confirmed slip for the branch to edit; the reason is logged."""
    with get_db() as con:
        _ensure(con)
        return _write(con, lambda: _is.reopen(con, body.slip_id, _who(request), body.note))


@router.get("/api/bom-center/issues")
def bc_issues_list(request: Request, branch_id: Optional[int] = Query(None), date_from: Optional[str] = Query(None),
                   date_to: Optional[str] = Query(None), status: Optional[str] = Query(None)):
    with get_db() as con:
        _ensure(con)
        bid = _claim_scope(con, request, branch_id)
        try:
            rows = _is.listing(con, bid, _day_or_today(date_from) if date_from else None,
                               _day_or_today(date_to) if date_to else None, status)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"slips": rows, "statuses": [{"code": c, "label": t} for c, t in _is.STATUS_TH.items()]}


# The confirmed slips as an Excel file, for Accounting's program (declared before /issues/{slip_id}).
SLIP_EXPORT_HEADERS = ["วันที่", "สาขา", "รหัส", "วัตถุดิบ", "หน่วย", "เบิกใช้", "ของเสีย", "สาเหตุของเสีย", "ราคาต่อหน่วย",
                       "มูลค่าเบิกใช้", "มูลค่าของเสีย", "เลขที่ใบ", "ยืนยันเมื่อ", "ล่าช้า"]
SLIP_SUMMARY_HEADERS = ["สาขา", "จำนวนใบ", "ใบที่ยืนยันล่าช้า", "จำนวนรายการ", "มูลค่าเบิกใช้", "มูลค่าของเสีย", "มูลค่ารวม"]


def _slip_lines_sheet(ws, title: str, rows: list, note: str) -> None:
    """One row per slip line (confirmed slips only) — a clean table: no totals row, so a program can read it."""
    _sheet(ws, title, SLIP_EXPORT_HEADERS, note)
    for r in rows:
        reason = None
        if r["qty_waste"] and r["waste_reason_th"]:
            reason = r["waste_reason_th"] + (f" — {r['note']}" if r["note"] else "")
        ws.append([r["date"], r["branch"], r["code"], r["name"], r["base_unit"], r["qty_used"], r["qty_waste"], reason,
                   r["unit_price"], r["used_value"], r["waste_value"], r["slip_id"], r["confirmed_at"],
                   "ล่าช้า" if r["late"] else ""])
    for row in ws.iter_rows(min_row=3, max_row=ws.max_row):
        for c in row[5:7]:
            c.number_format = "#,##0.###"
        row[8].number_format = "#,##0.0000"
        row[9].number_format = "#,##0.00"
        row[10].number_format = "#,##0.00"
    ws.column_dimensions["D"].width = 44
    ws.column_dimensions["H"].width = 30
    ws.freeze_panes = "C3"


def _slip_summary_sheet(ws, title: str, rows: list, note: str) -> None:
    from openpyxl.styles import Font
    _sheet(ws, title, SLIP_SUMMARY_HEADERS, note)
    per = {}
    for r in rows:
        b = per.setdefault(r["branch"] or "-", {"slips": set(), "late": set(), "lines": 0, "used": 0.0, "waste": 0.0})
        b["slips"].add(r["slip_id"])
        if r["late"]:
            b["late"].add(r["slip_id"])
        b["lines"] += 1
        b["used"] += r["used_value"] or 0.0
        b["waste"] += r["waste_value"] or 0.0
    tot = [0, 0, 0, 0.0, 0.0]
    for name in sorted(per):
        b = per[name]
        vals = [len(b["slips"]), len(b["late"]), b["lines"], round(b["used"], 2), round(b["waste"], 2)]
        ws.append([name] + vals + [round(b["used"] + b["waste"], 2)])
        tot = [x + y for x, y in zip(tot, vals)]
    ws.append(["รวม"] + [round(x, 2) if isinstance(x, float) else x for x in tot] + [round(tot[3] + tot[4], 2)])
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    for row in ws.iter_rows(min_row=3, max_row=ws.max_row):
        for c in row[4:7]:
            c.number_format = "#,##0.00"
    ws.column_dimensions["A"].width = 28


@router.get("/api/bom-center/issues/export.xlsx")
def bc_issues_export(request: Request, month: Optional[str] = Query(None), branch_id: Optional[int] = Query(None)):
    """The confirmed slips of a month (one branch, or every branch for the centre / Accounting), priced at the
    purchase price of each slip's month. Drafts are never in it."""
    from openpyxl import Workbook
    month = _month_or_400(month)
    with get_db() as con:
        _ensure(con)
        bid = _claim_scope(con, request, branch_id)
        rows = _ir.slip_rows(con, month=month, branch_id=bid)
    if not rows:
        raise HTTPException(400, f"ยังไม่มีใบตัดเบิกที่ยืนยันแล้วในเดือน {month}")
    who = f"สาขา{rows[0]['branch']}" if bid is not None else "ทุกสาขา"
    note = (f"ใบตัดเบิกที่ยืนยันแล้ว {who} เดือน {month} · หน่วยเป็นหน่วยฐานของวัตถุดิบ · ราคาต่อหน่วย = ราคาซื้อจริงของเดือนที่ใบตัดเบิกระบุ · "
            "ไม่รวมใบที่ยังเป็นร่างหรือที่เปิดแก้ไขอยู่ · “ล่าช้า” = ใบที่ยืนยันหลังวันที่ของใบ")
    wb = Workbook()
    _slip_lines_sheet(wb.active, "ใบตัดเบิกที่ยืนยัน", rows, note)
    _slip_summary_sheet(wb.create_sheet(), "สรุปต่อสาขา", rows, note)
    _for_accounting(wb)
    return _xlsx(wb, f"bom_center_issue_slips_{'b%d_' % bid if bid is not None else ''}{month}.xlsx")


@router.get("/api/bom-center/issues/export/{slip_id}.xlsx")
def bc_issue_export_one(slip_id: int, request: Request):
    """One confirmed slip as an Excel file."""
    from openpyxl import Workbook
    with get_db() as con:
        _ensure(con)
        head = _is._row_by_id(con, slip_id)
        if not head:
            raise HTTPException(404, "ไม่พบใบตัดเบิก")
        _stock_branch(con, request, head["branch_id"])
        if head["status"] != "confirmed":
            raise HTTPException(400, "ใบตัดเบิกนี้ยังไม่ยืนยัน จึงยังส่งออกไม่ได้")
        rows = _ir.slip_rows(con, slip_id=slip_id)
    wb = Workbook()
    _slip_lines_sheet(wb.active, "ใบตัดเบิก", rows,
                      f"ใบตัดเบิก สาขา{rows[0]['branch'] if rows else ''} วันที่ {head['slip_date']} เลขที่ใบ {slip_id} · "
                      f"ยืนยันโดย {head['confirmed_by'] or '-'}")
    _for_accounting(wb)
    return _xlsx(wb, f"bom_center_issue_slip_{slip_id}_{head['slip_date']}.xlsx")


@router.get("/api/bom-center/issues/{slip_id}")
def bc_issue_detail(slip_id: int, request: Request):
    with get_db() as con:
        _ensure(con)
        try:
            d = _is.detail(con, slip_id)
        except ValueError as e:
            raise HTTPException(404, str(e))
        _stock_branch(con, request, d["branch_id"])      # a branch login reads only its own slips
        return d


# ── Phase 2 · S7: goods received + usage variance (variance.py) ─────────────
class ReceiptLine(BaseModel):
    code: str
    qty: float
    unit: Optional[str] = None
    pieces: Optional[float] = None       # PR-U2: whole pieces counted, optional, for an item sold by weight (a salmon)


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


# ── ใบสรุปตัดเบิก: the period's actual usage, downloaded by Accounting and ticked as posted ──
ISSUE_HEADERS = ["รหัส", "วัตถุดิบ", "หน่วย", "ยกมา (ต้นงวด)", "รับเข้า", "เคลม/ปรับ", "นับจริง (ปลายงวด)",
                 "ตัดเบิก (ใช้จริง)", "ราคาต่อหน่วย", "มูลค่าตัดเบิก", "ที่มาราคา"]
ISSUE_ALL_HEADERS = ["สาขา", "งวดเริ่ม", "งวดสิ้นสุด", "จำนวนรายการ", "มูลค่ารับเข้า", "มูลค่าเคลม/ปรับ", "มูลค่าตัดเบิก",
                     "ไม่มีราคา", "สถานะบัญชี"]


class PostedBody(BaseModel):
    end_count_id: int
    posted: bool = True


def _issue_note(s: dict) -> str:
    p, st = s["period"], s["status"]
    note = (f"ใบสรุปตัดเบิกวัตถุดิบ สาขา{s['branch']['name']} งวด {p['begin_date']} ถึง {p['end_date']} (เดือน {p['month']})"
            " · จัดทำจากผลตรวจนับต้นงวด/ปลายงวดและใบรับสินค้า"
            " · ตัดเบิก = ยกมา + รับเข้า + เคลม/ปรับ − คงเหลือ"
            f" · ราคาต่อหน่วย = ราคาซื้อจริงเดือน {p['month']}")
    if st.get("receipts_confirmed_by"):
        note += f" · ยืนยันรับสินค้าครบโดย {st['receipts_confirmed_by']}"
    return note


def _issue_sheet(ws, title: str, s: dict) -> None:
    from openpyxl.comments import Comment
    from openpyxl.styles import Font
    _sheet(ws, title, ISSUE_HEADERS, _issue_note(s))
    for ln in s["lines"]:
        ws.append([ln["code"], ln["name"], ln["base_unit"], ln["begin"], ln["received"], ln["claims"], ln["end"],
                   ln["used"], ln["unit_price"], ln["value"], ln["price_source"]])
        if ln["note"]:
            ws.cell(row=ws.max_row, column=8).comment = Comment(ln["note"], "ระบบ")
    ws.append(["", "รวม", "", "", "", "", "", "", "", s["totals"]["used_value"]])
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    for row in ws.iter_rows(min_row=3, max_row=ws.max_row):
        for c in row[3:8]:
            c.number_format = "#,##0.###"
        row[8].number_format = "#,##0.0000"
        row[9].number_format = "#,##0.00"
    ws.column_dimensions["B"].width = 44
    ws.column_dimensions["K"].width = 24
    ws.freeze_panes = "A3"


def _period_slip_rows(con, bid: int, s: dict) -> list:
    """The confirmed slips dated inside a ใบสรุปตัดเบิก period [begin, end) — the end day belongs to the next period."""
    p = s["period"]
    last = date.fromisoformat(p["end_date"]) - timedelta(days=1)
    return _ir.slip_rows(con, branch_id=bid, d0=p["begin_date"], d1=last)


def _period_slip_note(s: dict, recon: dict) -> str:
    p, sl = s["period"], recon["slips"]
    return (f"ใบตัดเบิกรายวันที่ยืนยันแล้ว สาขา{s['branch']['name']} งวด {p['begin_date']} ถึง {p['end_date']} (เดือน {p['month']}) · "
            f"ใบตัดเบิกยืนยันแล้ว {sl['confirmed_days']}/{sl['trading_days']} วันขาย · ราคาต่อหน่วย = ราคาซื้อจริงของเดือนที่ใบตัดเบิกระบุ · "
            "ไม่รวมใบที่ยังเป็นร่าง")


def _issue_or_400(con, bid: int, end_count_id: int) -> dict:
    try:
        s = _va.issue_summary(con, bid, end_count_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    if not s["status"]["receipts_confirmed"]:
        raise HTTPException(400, "งวดนี้ยังไม่ยืนยันรับสินค้าครบ จึงยังจัดทำใบสรุปตัดเบิกไม่ได้ "
                                 "· ให้สาขาหรือประสานงานขายยืนยันก่อน")
    return s


@router.get("/api/bom-center/issue-summary/periods")
def bc_issue_periods(request: Request, branch_id: Optional[int] = Query(None)):
    """Every counting branch's begin/end periods with their confirmation and posting state (a branch login: its own)."""
    with get_db() as con:
        _ensure(con)
        if _auth.scope_branch(request) or branch_id is not None:
            branch_id = _stock_branch(con, request, branch_id)
        return {"branches": _va.issue_periods(con, branch_id)}


@router.get("/api/bom-center/issue-summary")
def bc_issue_summary(request: Request, end_count_id: int = Query(...), branch_id: Optional[int] = Query(None)):
    """The ใบสรุปตัดเบิก of one period as JSON (a preview is allowed before the receipts are confirmed)."""
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        try:
            return _va.issue_summary(con, bid, end_count_id)
        except ValueError as e:
            raise HTTPException(404, str(e))


@router.get("/api/bom-center/issue-summary.xlsx")
def bc_issue_summary_xlsx(request: Request, end_count_id: int = Query(...), branch_id: Optional[int] = Query(None)):
    from openpyxl import Workbook
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        s = _issue_or_400(con, bid, end_count_id)
        slips = _period_slip_rows(con, bid, s)
        recon = _ir.reconcile(con, bid, end_count_id)
    wb = Workbook()
    # three sheets (PR-J4): the daily slips of the period · system stock vs the count · the movement summary (PR-F)
    _slip_lines_sheet(wb.active, "ใบตัดเบิกรายวัน", slips, _period_slip_note(s, recon))
    _recon_sheet(wb.create_sheet(), "กระทบยอด", recon)
    _issue_sheet(wb.create_sheet(), "สรุปเคลื่อนไหว", s)
    _for_accounting(wb)
    return _xlsx(wb, f"bom_center_issue_summary_b{bid}_{s['period']['month']}.xlsx")


@router.get("/api/bom-center/issue-summary-all.xlsx")
def bc_issue_summary_all_xlsx(request: Request, month: str = Query(...)):
    """One workbook for the month: a summary sheet, the daily slips, and per confirmed branch period its movement and reconciliation sheets."""
    from openpyxl import Workbook
    _month_or_400(month)
    if _auth.scope_branch(request):
        raise HTTPException(403, detail="บัญชีสาขาดาวน์โหลดได้เฉพาะใบสรุปตัดเบิกของสาขาตัวเอง", headers={"X-Denied": "role"})
    with get_db() as con:
        _ensure(con)
        jobs = []
        for b in _va.issue_periods(con):
            for p in b["periods"]:
                if p["month"] == month:
                    jobs.append((b, p))
        done = {p["end_count_id"]: _va.issue_summary(con, b["branch_id"], p["end_count_id"])
                for b, p in jobs if p["receipts_confirmed"]}
        slips = {k: _period_slip_rows(con, s["branch"]["branch_id"], s) for k, s in done.items()}
        recons = {k: _ir.reconcile(con, s["branch"]["branch_id"], k) for k, s in done.items()}
    if not done:
        raise HTTPException(400, f"ยังไม่มีงวดที่ยืนยันรับสินค้าครบในเดือน {month}")
    wb = Workbook()
    used = set()
    ws = wb.active
    _sheet(ws, _sheet_title("สรุปทุกสาขา", used), ISSUE_ALL_HEADERS,
           f"สรุปใบสรุปตัดเบิกวัตถุดิบทุกสาขา เดือน {month} · แต่ละสาขามีแผ่นงานรายการของตัวเอง · "
           "ตัดเบิก = ยกมา + รับเข้า + เคลม/ปรับ − คงเหลือ · ราคาต่อหน่วย = ราคาซื้อจริงของเดือนที่งวดสิ้นสุด")
    sheets = []
    for b, p in jobs:
        if p["receipts_confirmed"]:
            s = done[p["end_count_id"]]
            t = s["totals"]
            posted = p["accounting_posted_at"]
            ws.append([b["name"], p["begin_date"], p["end_date"], t["lines"], t["received_value"], t["claims_value"],
                       t["used_value"], t["unpriced"], f"บันทึกแล้ว {posted[:10]}" if posted else "ยังไม่บันทึก"])
            sheets.append(s)
        else:
            ws.append([b["name"], p["begin_date"], p["end_date"], None, None, None, None, None,
                       "ยังไม่ยืนยันรับสินค้าครบ (ไม่มีแผ่นงาน)"])
    for row in ws.iter_rows(min_row=3, max_row=ws.max_row):
        for c in row[4:7]:
            c.number_format = "#,##0.00"
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["I"].width = 40
    # PR-J4: the daily slips of every confirmed period, then per branch its movement sheet and its reconciliation
    all_slips = [r for s in sheets for r in slips[s["period"]["end_count_id"]]]
    _slip_lines_sheet(wb.create_sheet(), _sheet_title("ใบตัดเบิกรายวัน", used), all_slips,
                      f"ใบตัดเบิกรายวันที่ยืนยันแล้วของทุกสาขาในงวดที่ยืนยันรับสินค้าครบ เดือน {month} · ไม่รวมใบที่ยังเป็นร่าง")
    for s in sheets:
        name = s["branch"]["name"]
        _issue_sheet(wb.create_sheet(), _sheet_title(name, used), s)
        _recon_sheet(wb.create_sheet(), _sheet_title(f"กระทบยอด {name}", used), recons[s["period"]["end_count_id"]])
    _for_accounting(wb)
    return _xlsx(wb, f"bom_center_issue_summary_all_{month}.xlsx")


@router.post("/api/bom-center/issue-summary/posted", dependencies=[Depends(require_write_token)])
def bc_issue_posted(body: PostedBody, request: Request):
    """Accounting: the ใบสรุปตัดเบิก of this period is posted (or no longer posted) in the accounting system."""
    with get_db() as con:
        _ensure(con)
        pinned = _stock_branch(con, request, None) if _auth.scope_branch(request) else None
        out = _write(con, lambda: _va.set_posted(con, body.end_count_id, body.posted, _who(request), pinned))
    invalidate_cache("bc_")
    return out


# ── PR-J3 · issue-slip reports: system stock vs count, daily issue vs BoM (issue_reports.py) ──
RECON_HEADERS = ["รหัส", "วัตถุดิบ", "หน่วย", "กลุ่มมูลค่า", "ยกมา (ต้นงวด)", "รับเข้า", "เคลม/ปรับ", "ตัดเบิก (ใช้)", "ของเสีย",
                 "คงเหลือตามระบบ", "นับจริง (ปลายงวด)", "ผลต่าง (จำนวน)", "ผลต่าง (มูลค่า)", "ผลต่าง %", "สถานะ", "เหตุผล",
                 "หมายเหตุ", "ราคาต่อหน่วย"]
DAILY_HEADERS = ["วันที่", "วัน", "ใบตัดเบิก", "มูลค่าเบิกใช้", "มูลค่าของเสีย", "รวมเบิก (มูลค่า)", "ตามสูตร (มูลค่า)",
                 "ผลต่าง (มูลค่า)", "ผลต่าง %", "รายการกลุ่มมูลค่าสูงที่เบิกเกินเกณฑ์", "เบิกเกินต่อเนื่อง"]
DAILY_ITEM_HEADERS = ["วันที่", "รหัส", "วัตถุดิบ", "หน่วย", "กลุ่มมูลค่า", "เบิกใช้", "ของเสีย", "รวมเบิก", "ตามสูตร",
                      "ผลต่าง (จำนวน)", "ผลต่าง %", "ราคาต่อหน่วย", "ผลต่าง (มูลค่า)", "สัญญาณ"]


def _recon_or_404(con, bid: int, end_count_id: int) -> dict:
    try:
        return _ir.reconcile(con, bid, end_count_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/api/bom-center/issue-reports/reconcile")
def bc_ir_reconcile(request: Request, end_count_id: int = Query(...), branch_id: Optional[int] = Query(None)):
    """R1: the system stock (the slips) against the month-end count, item by item."""
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        return _recon_or_404(con, bid, end_count_id)


@router.get("/api/bom-center/issue-reports/reconcile.xlsx")
def bc_ir_reconcile_xlsx(request: Request, end_count_id: int = Query(...), branch_id: Optional[int] = Query(None)):
    from openpyxl import Workbook
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        r = _recon_or_404(con, bid, end_count_id)
    wb = Workbook()
    _recon_sheet(wb.active, "กระทบยอด", r)
    return _xlsx(wb, f"bom_center_issue_reconcile_b{bid}_{r['period']['month']}.xlsx")


def _recon_sheet(ws, title: str, r: dict) -> None:
    """The reconciliation (system stock vs the count) of one period as a sheet; also used by the ใบสรุปตัดเบิก workbooks."""
    from openpyxl.styles import Font
    p, sl = r["period"], r["slips"]
    _sheet(ws, title, RECON_HEADERS,
           f"รายงานสินค้าคงเหลือตามตัดเบิกเทียบกับสินค้านับจริง สาขา{r['branch']['name']} งวด {p['begin_date']} ถึง "
           f"{p['end_date']} (เดือน {p['month']}) · คงเหลือตามระบบ = ยกมา + รับเข้า + เคลม/ปรับ − ตัดเบิก − ของเสีย · "
           "ผลต่าง = นับจริง − คงเหลือตามระบบ · เกณฑ์ กลุ่มมูลค่าสูง ±5% · กลาง ±10% · ต่ำ ±20% · "
           f"ใบตัดเบิกยืนยันแล้ว {sl['confirmed_days']}/{sl['trading_days']} วัน")
    for x in r["rows"]:
        ws.append([x["code"], x["name"], x["base_unit"], _CLASS_SHORT.get(x["class"], x["class"]), x["begin"], x["received"], x["claims"], x["used"],
                   x["waste"], x["system"], x["count"], x["diff"], x["diff_value"], x["diff_pct"], x["status_th"],
                   x.get("reason_th"), x.get("reason_note"), x["price_per_base"]])
    t = r["totals"]
    ws.append(["", "รวม", "", "", "", "", "", "", "", "", "", "", t["diff_value"]])
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    for row in ws.iter_rows(min_row=3, max_row=ws.max_row):
        for c in row[4:12]:
            c.number_format = "#,##0.###"
        row[12].number_format = "#,##0.00"
        row[13].number_format = "0.0"
        row[17].number_format = "#,##0.0000"
    ws.column_dimensions["B"].width = 44
    ws.column_dimensions["O"].width = 24
    ws.column_dimensions["P"].width = 26
    ws.column_dimensions["Q"].width = 30
    ws.freeze_panes = "C3"


@router.get("/api/bom-center/issue-reports/daily")
def bc_ir_daily(request: Request, branch_id: Optional[int] = Query(None), month: Optional[str] = Query(None)):
    """R2: per trading day, the confirmed issue (baht) against the BoM of what was sold, with the streak flags."""
    month = _month_or_400(month) if month else _this_month()
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        return _ir.daily(con, bid, month)


@router.get("/api/bom-center/issue-reports/daily/day")
def bc_ir_daily_day(request: Request, day: str = Query(...), branch_id: Optional[int] = Query(None)):
    """R2 drill-down: the items of one day."""
    try:
        date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "วันที่ไม่ถูกต้อง")
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        return _ir.daily_day(con, bid, day)


@router.get("/api/bom-center/issue-reports/daily.xlsx")
def bc_ir_daily_xlsx(request: Request, branch_id: Optional[int] = Query(None), month: Optional[str] = Query(None)):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    month = _month_or_400(month) if month else _this_month()
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        d = _ir.daily(con, bid, month)
        items = _ir.daily_items(con, bid, month)
    t = d["totals"]
    wb = Workbook()
    ws = wb.active
    _sheet(ws, "รายวัน", DAILY_HEADERS,
           f"ผลต่างตัดเบิกรายวัน สาขา{d['branch']['name']} เดือน {month} · ตัดเบิกเทียบตามสูตรจากยอดขายของวันนั้น "
           f"(ราคาซื้อเดือน {month}) · นับเฉพาะใบตัดเบิกที่ยืนยันแล้ว · กลุ่มมูลค่าสูงเบิกเกินสูตรมากกว่า {_ir.OVER_PCT:.0f}% "
           f"ติดต่อกัน {_ir.STREAK_DAYS} วันขายขึ้นไป ถือเป็นเบิกเกินต่อเนื่อง")
    for r in d["rows"]:
        ws.append([r["date"], r["weekday_th"], r["slip_status_th"], r["used_value"], r["waste_value"], r["issued_value"],
                   r["bom_value"] if r["trading"] else None, r["var_value"], r["var_pct"],
                   ", ".join(f"{x['name']} ({x['var_pct']:+.1f}%)" for x in r["over_items"]), ", ".join(r["streak"])])
    ws.append(["รวม", "", "", t["used_value"], t["waste_value"], t["issued_value"], t["bom_value"], t["var_value"],
               t["var_pct"]])
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    for row in ws.iter_rows(min_row=3, max_row=ws.max_row):
        for c in row[3:8]:
            c.number_format = "#,##0.00"
        row[8].number_format = "0.0"
    ws.column_dimensions["J"].width = 48
    ws.column_dimensions["K"].width = 36
    ws.freeze_panes = "A3"
    w2 = wb.create_sheet()
    _sheet(w2, "รายการต่อวัน", DAILY_ITEM_HEADERS,
           f"รายการวัตถุดิบต่อวัน สาขา{d['branch']['name']} เดือน {month} · หน่วยเป็นหน่วยฐานของวัตถุดิบ · "
           "ผลต่าง = รวมเบิก − ตามสูตร · แถวที่ใบตัดเบิกยังไม่ยืนยันไม่มีตัวเลขเบิก")
    for it in items:
        w2.append([it["date"], it["code"], it["name"], it["base_unit"], _CLASS_SHORT.get(it["class"], it["class"]), it["used"], it["waste"], it["issued"],
                   it["bom"], it["var_qty"], it["var_pct"], it["price_per_base"], it["var_value"], it["signal_th"]])
    for row in w2.iter_rows(min_row=3, max_row=w2.max_row):
        for c in row[5:10]:
            c.number_format = "#,##0.###"
        row[10].number_format = "0.0"
        row[11].number_format = "#,##0.0000"
        row[12].number_format = "#,##0.00"
    w2.column_dimensions["C"].width = 44
    w2.freeze_panes = "D3"
    return _xlsx(wb, f"bom_center_issue_daily_b{bid}_{month}.xlsx")


# ── PR-J4 · R3 the lateness log: how each branch handled its slips (issue_reports.log) ──
LOG_HEADERS = ["วันที่", "สาขา", "สถานะใบตัดเบิก", "ยืนยันเมื่อ", "ผู้ยืนยัน", "ยืนยันซ้ำ (ครั้ง)", "เปิดแก้ไข (ครั้ง)",
               "เหตุผลเปิดแก้ไข", "ใบสั่งซื้อส่งล่าช้า (ใบ)", "เคลม (เรื่อง)", "หมายเหตุ"]
LOG_SUMMARY_HEADERS = ["สาขา", "วันขาย", "ยืนยันตรงวัน", "ล่าช้า", "ยังไม่ยืนยัน", "ไม่มีใบ", "ตรงวัน %", "มีใบ %",
                       "เปิดแก้ไข (ครั้ง)", "ยืนยันซ้ำ (ครั้ง)", "ใบสั่งซื้อส่งล่าช้า (ใบ)", "เคลม (เรื่อง)"]


def _log_range(date_from: Optional[str], date_to: Optional[str]) -> tuple:
    d0, d1 = _dates_or_week(date_from, date_to)
    if d0 > d1 or (date.fromisoformat(d1) - date.fromisoformat(d0)).days > 92:
        raise HTTPException(400, "ช่วงวันที่ต้องไม่เกิน 92 วัน")
    return d0, d1


@router.get("/api/bom-center/issue-reports/log")
def bc_ir_log(request: Request, date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
              branch_id: Optional[int] = Query(None)):
    """R3: per branch and day — on time / late / draft / missing, reconfirms, reopens and why, late purchase orders, claims."""
    d0, d1 = _log_range(date_from, date_to)
    with get_db() as con:
        _ensure(con)
        return _ir.log(con, d0, d1, _claim_scope(con, request, branch_id))


@router.get("/api/bom-center/issue-reports/log/monthly")
def bc_ir_log_monthly(request: Request, month: Optional[str] = Query(None), branch_id: Optional[int] = Query(None)):
    month = _month_or_400(month) if month else _this_month()
    with get_db() as con:
        _ensure(con)
        return _ir.log_monthly(con, month, _claim_scope(con, request, branch_id))


@router.get("/api/bom-center/issue-reports/log.xlsx")
def bc_ir_log_xlsx(request: Request, date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                   month: Optional[str] = Query(None), branch_id: Optional[int] = Query(None)):
    """The lateness log as a workbook: the days (oldest first) and a summary per branch. `month` replaces the dates."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    if month:
        _month_or_400(month)
    else:
        d0, d1 = _log_range(date_from, date_to)
    with get_db() as con:
        _ensure(con)
        bid = _claim_scope(con, request, branch_id)
        lg = _ir.log_monthly(con, month, bid) if month else _ir.log(con, d0, d1, bid)
    label = f"เดือน {month}" if month else f"{d0} ถึง {d1}"
    note = (f"บันทึกความล่าช้าของใบตัดเบิก {label} · นับเฉพาะวันที่สาขามียอดขายหรือมีใบตัดเบิก ไม่นับวันก่อนสาขาเริ่มใช้งานและไม่นับวันนี้ที่ยังไม่ครบวัน · "
            "ตรงวัน % = ยืนยันตรงวัน ÷ ใบที่ยืนยันแล้ว · มีใบ % = ใบที่ยืนยันแล้ว ÷ วันขาย")
    wb = Workbook()
    ws = wb.active
    _sheet(ws, "บันทึกความล่าช้า", LOG_HEADERS, note)
    for r in sorted(lg["rows"], key=lambda x: (x["date"], x["branch"] or "")):
        ws.append([r["date"], r["branch"], r["status_th"], r["confirmed_at"], r["confirmed_by"], r["reconfirms"] or None,
                   r["reopens"] or None, r["note"], r["late_orders"] or None, r["claims"] or None, r["status_note"] or None])
    ws.column_dimensions["D"].width = 18
    ws.column_dimensions["H"].width = 48
    ws.column_dimensions["K"].width = 32
    ws.freeze_panes = "C3"
    w2 = wb.create_sheet()
    _sheet(w2, "สรุปต่อสาขา", LOG_SUMMARY_HEADERS, note)
    for s in lg["summary"]:
        w2.append([s["branch"], s["trading_days"], s["on_time"], s["late"], s["draft"], s["missing"], s["on_time_pct"],
                   s["coverage_pct"], s["reopens"], s["reconfirms"], s["late_orders"], s["claims"]])
    t = lg["totals"]
    ok = t["on_time"] + t["late"]
    w2.append(["รวม", t["trading_days"], t["on_time"], t["late"], t["draft"], t["missing"],
               round(100.0 * t["on_time"] / ok, 1) if ok else None,
               round(100.0 * ok / t["trading_days"], 1) if t["trading_days"] else None,
               t["reopens"], t["reconfirms"], t["late_orders"], t["claims"]])
    for c in w2[w2.max_row]:
        c.font = Font(bold=True)
    for row in w2.iter_rows(min_row=3, max_row=w2.max_row):
        row[6].number_format = "0.0"
        row[7].number_format = "0.0"
    w2.column_dimensions["A"].width = 28
    w2.freeze_panes = "B3"
    tag = month or f"{d0}_{d1}"
    return _xlsx(wb, f"bom_center_issue_log_{'b%d_' % bid if bid is not None else ''}{tag}.xlsx")


# ── Phase 2 · S8: the branch order (orders.py) ──────────────────────────────
def _round_or_400(r: str) -> str:
    if r not in ("1", "2", "salmon"):
        raise HTTPException(400, "รอบต้องเป็น 1, 2 หรือแซลมอนสด")
    return r


def _day_or_today(d: Optional[str]) -> date:
    if not d:
        return bkk_today()
    try:
        return date.fromisoformat(d)
    except ValueError:
        raise HTTPException(400, "วันที่ไม่ถูกต้อง")


def _resolve_delivery(con, bid: int, rnd: Optional[str], delivery: Optional[str], order_day: Optional[str]) -> tuple:
    """(round, delivery date) for a plan / save request. `delivery` names the delivery day (with its round);
    the deprecated `order_day` means "the window open on that day"; neither = the delivery the branch should
    be working on now — the nearest whose 13:15 cutoff has not passed (else the nearest)."""
    if rnd:
        _round_or_400(rnd)
    now = _fc.now_bkk()
    if delivery:
        d = _day_or_today(delivery)
        if not rnd:                                           # the round whose calendar delivers on that day
            rnd = next((x["round"] for x in _fc.next_deliveries(con, bid, now.date(), horizon_days=21, now=now)
                        if x["delivery"] == d.isoformat()), None)
            if not rnd:
                raise HTTPException(400, "ระบุรอบของวันส่งนี้")
        if _fc._wd(d) != int(_fc.calendar(con, bid)[rnd]["delivery_weekday"]):
            raise HTTPException(400, "วันส่งไม่ตรงกับวันส่งที่กำหนดไว้ของรอบนี้ · เลือกจากรายการรอบส่ง")
        return rnd, d
    if order_day:
        day = _day_or_today(order_day)
        rnd = rnd or _or.next_round(con, bid, day)
        return rnd, _fc.cover_period(con, bid, rnd, day)[1]
    items = _fc.next_deliveries(con, bid, now.date(), now=now)
    if rnd:
        items = [x for x in items if x["round"] == rnd]
    pick = _fc.pick_delivery(items)
    return pick["round"], date.fromisoformat(pick["delivery"])


@router.get("/api/bom-center/order/plan")
def bc_order_plan(request: Request, round: Optional[str] = Query(None), delivery: Optional[str] = Query(None),
                  order_day: Optional[str] = Query(None), branch_id: Optional[int] = Query(None),
                  buffer_pct: Optional[float] = Query(None), expected_sales: Optional[float] = Query(None)):
    """The suggestion for one delivery, net of the count the branch typed + the cycle's order if one
    exists. Without a delivery, the nearest one still open for ordering — the branch page opens on it.
    `buffer_pct` (PR-19) applies the branch's expectation; omitted = the order's own.
    `expected_sales` (PR-V, baht ex VAT for the cover period) matters only to a new branch with a reference branch.
    `order_day` is deprecated: "the window open on that day"."""
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        rnd, dlv = _resolve_delivery(con, bid, round, delivery, order_day)
        try:
            out = _or.plan(con, bid, rnd, dlv, buffer_pct=buffer_pct,
                           expected_sales=(expected_sales if expected_sales and expected_sales > 0 else None))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        out["round"] = rnd
        return out


@router.get("/api/bom-center/order/covers")
def bc_order_covers(request: Request, round: Optional[str] = Query(None), delivery: Optional[str] = Query(None),
                    order_day: Optional[str] = Query(None), branch_id: Optional[int] = Query(None)):
    """PR-19: the customers the cover period normally brings, day by day (the forecast's
    weekday baseline and last year's same dates) — the helper behind "คำนวณจากจำนวนลูกค้า"."""
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        rnd, dlv = _resolve_delivery(con, bid, round, delivery, order_day)
        out = _fc.covers(con, bid, rnd, bkk_today(), delivery=dlv)
        out["round"] = rnd
        return out


class OrderLine(BaseModel):
    code: str
    confirmed_units: Optional[float] = None
    reason_code: Optional[str] = None
    note: Optional[str] = None


class OrderBody(BaseModel):
    # (a `counts` field used to sit here — the count made while ordering was retired in PR-J1; a client that
    # still sends it is ignored, FastAPI drops unknown fields)
    round: str
    delivery: Optional[str] = None
    order_day: Optional[str] = None        # deprecated: "the window open on that day"
    lines: List[OrderLine] = []
    submit: bool = False
    note: Optional[str] = None
    buffer: Optional[dict] = None          # PR-19: {pct, reason, note}; absent = keep the order's own
    ack_late: bool = False                 # the branch confirmed the late fee for a submit after the cutoff


@router.post("/api/bom-center/order/save", dependencies=[Depends(require_write_token)])
def bc_order_save(body: OrderBody, request: Request, branch_id: Optional[int] = Query(None)):
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        rnd, dlv = _resolve_delivery(con, bid, body.round, body.delivery, body.order_day)

        def go():
            try:
                return _or.save_flow(con, bid, rnd, dlv, [ln.dict() for ln in body.lines], _who(request),
                                     submit=body.submit, note=body.note, buffer=body.buffer, ack_late=body.ack_late)
            except _or.LateSubmit as e:                      # the branch has to confirm the late fee first
                con.rollback()
                raise HTTPException(400, {"code": "late", "message": str(e), "cutoff_at": e.cutoff_at,
                                          "cutoff_th": e.cutoff_th})
        out = _write(con, go)
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
            o = _or.detail(con, order_id, receive=True)
        except ValueError as e:
            raise HTTPException(404, str(e))
        _stock_branch(con, request, o["branch_id"])
        o["claim_reasons"] = [{"code": c, "label": t} for c, t in _cl.REASONS]
        o["claims"] = _cl.for_order(con, order_id)
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
    received_units: Optional[float] = None   # in the line's order unit; ignored when `weight` is given
    weight: Optional[float] = None      # PR-W: the weight weighed at receiving (in the unit of the item's average, กิโลกรัม); it IS the received quantity
    pieces: Optional[float] = None      # PR-U2: whole pieces counted, required when the item is sold by weight and the quantity is > 0
    reason: Optional[str] = None        # why the received quantity is below what the warehouse sent
    note: Optional[str] = None


class ReceiveBody(BaseModel):
    lines: List[ReceiveLine]
    receipt_date: Optional[str] = None
    ack: bool = False                   # the branch checked condition and quantity (enforced in orders.receive)
    claim_note: Optional[str] = None    # a message to Sales Support with the claim raised by this receipt


@router.post("/api/bom-center/order/{order_id}/receive", dependencies=[Depends(require_write_token)])
def bc_order_receive(order_id: int, body: ReceiveBody, request: Request, branch_id: Optional[int] = Query(None)):
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        out = _write(con, lambda: _or.receive(con, bid, order_id, [ln.dict() for ln in body.lines], _who(request),
                                              receipt_date=body.receipt_date, ack=body.ack, claim_note=body.claim_note))
    invalidate_cache("bc_")
    return out


# ── PR-V · วัตถุดิบที่ใช้ต่อยอดขาย ฿100,000 (per_sales.py): a second way to read the same usage ──
PER_SALES_SHEET = "ต่อยอดขาย 100000"
PER_SALES_COMPARE = "เทียบสาขา"


def _per_sales_headers(weeks: int) -> list:
    return ["สาขา", "รหัส", "วัตถุดิบ", "หน่วย", f"ต่อยอดขาย ฿100,000 ({weeks} สัปดาห์ล่าสุด)", f"{weeks} สัปดาห์ก่อนหน้า",
            "เปลี่ยนแปลง %", "แกว่งต่อสัปดาห์ %", "มูลค่าต่อยอดขาย ฿100,000", "การใช้ตามสูตรเทียบค่ากลาง %", "ยอดซื้อเทียบค่ากลาง %", "หมายเหตุ"]


def _strip_benchmark(out: dict) -> dict:
    """A branch login never sees the other branches, and the benchmark is made of them: leave it (and every comparison with it)
    out of what a pinned session gets (PR-X)."""
    out = dict(out, benchmark=None, purchase_benchmark=None, chain=None)
    bs = []
    for b in out.get("branches") or []:
        b = dict(b, is_benchmark=None, vs_benchmark_pct=None, below_benchmark=False, below_benchmark_th=None, no_purchase_th=None,
                 purchase_per_100k_value=None, purchase_source=None, purchase_days=None, purchase_vs_benchmark_pct=None)
        b["items"] = [dict(it, bench_per_100k=None, vs_benchmark_pct=None) for it in b["items"]]
        bs.append(b)
    out["branches"] = bs
    return out


def _per_sales_note(it: dict) -> str:
    parts = []
    if it.get("steady") is False:
        parts.append(f"ใช้ไม่สม่ำเสมอ (แกว่ง ±{it['swing_pct']:g}% ต่อสัปดาห์)")
    elif it.get("steady") is None and it.get("swing_pct") is None and it.get("per_100k"):
        parts.append("ข้อมูลยังไม่พอ")
    if it.get("prev_per_100k") is None:
        parts.append("ไม่มีข้อมูลช่วงก่อนหน้า")
    return " · ".join(parts)


@router.get("/api/bom-center/per-sales")
def bc_per_sales(request: Request, branch_id: Optional[int] = Query(None), weeks: int = Query(4, ge=1, le=8)):
    """The last `weeks` weeks against the `weeks` before: per branch the usage of each ingredient per ฿100,000 of sales (ex VAT),
    items by value, the change, the weekly swing and whether it is steady; plus the chain total (sum of usage / sum of sales).
    A branch login gets its own branch only."""
    with get_db() as con:
        _ensure(con)
        bid = _claim_scope(con, request, branch_id)
        out = _pv.report(con, weeks, [bid] if bid is not None else None)
        if bid is not None:
            out = dict(out, chain=None)
        if _auth.scope_branch(request):
            out = _strip_benchmark(out)
        out = dict(out, scoped=bool(_auth.scope_branch(request)))
        return out


@router.get("/api/bom-center/per-sales.xlsx")
def bc_per_sales_xlsx(request: Request, branch_id: Optional[int] = Query(None), weeks: int = Query(4, ge=1, le=8)):
    from openpyxl import Workbook
    with get_db() as con:
        _ensure(con)
        bid = _claim_scope(con, request, branch_id)
        r = _pv.report(con, weeks, [bid] if bid is not None else None)
        if _auth.scope_branch(request):
            r = _strip_benchmark(r)
    chain = r["chain"] if bid is None else None
    bench = r.get("benchmark")
    wb = Workbook()
    ws = wb.active
    cur, prv = r["current"], r["previous"]
    _sheet(ws, PER_SALES_SHEET, _per_sales_headers(r["weeks"]),
           f"วัตถุดิบที่ใช้ต่อยอดขาย ฿100,000 (ไม่รวมภาษีมูลค่าเพิ่ม) · {r['weeks']} สัปดาห์ล่าสุด {cur['from']} ถึง {cur['to']}"
           + (f" เทียบ {prv['from']} ถึง {prv['to']}" if prv else "") + " · ปริมาณใช้ตามสูตรเดียวกับยอดแนะนำ (รวมส่วนสูญเสีย) "
           f"· มูลค่าตามราคาเดือน {r['month']} · แกว่งต่อสัปดาห์ = ส่วนเบี่ยงเบนมาตรฐานของอัตรารายสัปดาห์ ÷ ค่าเฉลี่ย "
           f"ถ้าเกิน {r['steady_pct']:g}% ถือว่าใช้ไม่สม่ำเสมอ"
           + (f" · {_bm.LABEL} = ค่ากลาง (มัธยฐาน) ของ {', '.join(x['name'] for x in bench['branches'])} · “การใช้ตามสูตรเทียบค่ากลาง %” = ส่วนต่างของปริมาณใช้ตามสูตรจากค่ากลาง · “ยอดซื้อเทียบค่ากลาง %” = ส่วนต่างของมูลค่าที่ซื้อจากไดกิโตะ กรุ๊ปต่อยอดขาย ฿100,000 จากค่ากลาง (ป้ายเตือนใช้ตัวเลขนี้)"
              f" · ช่วงยอมรับสต็อก ±{r['buffer_pct']:g}% ใช้ชั่วคราวจนกว่าจะมีผลตรวจนับปลายงวดครบทุกสาขา · แถว “ทั้งเครือ” คือผลรวมทุกสาขา" if bench else ""))
    blocks = [(b["branch"], b["items"]) for b in r["branches"]] + ([(_bm.LABEL, bench["items"])] if bench else []) \
        + ([("ทั้งเครือ", chain["items"])] if chain else [])
    pbench = r.get("purchase_benchmark")
    bymap = {b["branch"]: b for b in r["branches"]}
    src_th = {"price_file": "ไฟล์ราคารายสาขา", "orders": "ใบสั่งซื้อที่ส่งจากไดกิโตะ กรุ๊ป"}
    for name, items in blocks:
        b = bymap.get(name)
        if (bench or pbench) and (b or name == _bm.LABEL):      # one summary line per block: the value, and what it BUYS from the centre
            if b:
                if b.get("purchase_per_100k_value") is not None:
                    note = (f"ยอดซื้อจากไดกิโตะ กรุ๊ป ฿{b['purchase_per_100k_value']:,.0f} ต่อยอดขาย ฿100,000 (ที่มา {src_th.get(b['purchase_source'])} · {b['purchase_days']} วันขาย)"
                            + (f" · {_pv.BELOW_TH}: ต่ำกว่าค่ากลางเกิน {r['buffer_pct']:g}%" if b.get("below_benchmark") else ""))
                else:
                    note = b.get("no_purchase_th") or ""
                ws.append([name, None, "สรุปมูลค่าต่อยอดขาย ฿100,000", None, None, None, None, None, b["value_per_100k"],
                           b.get("vs_benchmark_pct"), b.get("purchase_vs_benchmark_pct"), note])
            else:
                ws.append([name, None, "สรุปมูลค่าต่อยอดขาย ฿100,000", None, None, None, None, None, bench["value_per_100k"], None, None,
                           (f"ยอดซื้อจากไดกิโตะ กรุ๊ป ฿{pbench['per_100k_value']:,.0f} ต่อยอดขาย ฿100,000 (มัธยฐานของ {pbench['n']} สาขา)" if pbench else "")])
        for it in items:
            ws.append([name, it["code"], it["name"], it["unit"], it["per_100k"], it["prev_per_100k"], it["change_pct"],
                       it["swing_pct"], it["value_per_100k"], it.get("vs_benchmark_pct"), None, _per_sales_note(it)])
    for row in ws.iter_rows(min_row=3, max_row=ws.max_row):
        for c in row[4:6]:
            c.number_format = "#,##0.00"
        row[6].number_format = "0.0"
        row[7].number_format = "0.0"
        row[8].number_format = "#,##0.00"
        row[9].number_format = "0.0"
        row[10].number_format = "0.0"
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["C"].width = 40
    ws.column_dimensions["L"].width = 60
    ws.freeze_panes = "D3"
    w2 = wb.create_sheet()
    heads = [b["branch"] for b in r["branches"]] + ([_bm.LABEL] if bench else []) + (["ทั้งเครือ"] if chain else [])
    _sheet(w2, PER_SALES_COMPARE, ["รหัส", "วัตถุดิบ", "หน่วย"] + heads,
           f"ปริมาณที่ใช้ต่อยอดขาย ฿100,000 ของ {r['weeks']} สัปดาห์ล่าสุด เทียบรายสาขา (หน่วยเดียวกับแผ่นแรก)")
    tables = [{it["code"]: it for it in b["items"]} for b in r["branches"]] \
        + ([{it["code"]: it for it in bench["items"]}] if bench else []) + ([{it["code"]: it for it in chain["items"]}] if chain else [])
    first = {}
    for t in tables:
        for code, it in t.items():
            first.setdefault(code, it)
    order = sorted(first, key=lambda c: -max((t[c]["value_per_100k"] or 0) for t in tables if c in t))
    for code in order:
        w2.append([code, first[code]["name"], first[code]["unit"]] + [t[code]["per_100k"] if code in t else None for t in tables])
    for row in w2.iter_rows(min_row=3, max_row=w2.max_row):
        for c in row[3:]:
            c.number_format = "#,##0.00"
    w2.column_dimensions["B"].width = 40
    w2.freeze_panes = "D3"
    return _xlsx(wb, f"bom_center_per_sales_{'b%d_' % bid if bid is not None else ''}{r['end']}.xlsx")


# ── PR-X · ตรวจสูตรและหน่วยด้วยยอดซื้อจริง (recipe_check.py): analyst / management only ──────────────
RECIPE_CHECK_HEADS = ["รหัส", "วัตถุดิบ", "หน่วยฐาน", "ซื้อจริงต่อยอดขาย ฿100,000 (ค่ากลาง)", "ตามสูตรต่อยอดขาย ฿100,000 (ค่ากลาง)",
                      "ส่วนต่าง %", "ช่วงยอมรับ ±%", "ผลตรวจ", "มูลค่าส่วนต่างต่อยอดขาย ฿100,000"]


def _recipe_check_or_403(request: Request, month: Optional[str]) -> dict:
    if _auth.scope_branch(request):
        raise HTTPException(403, "ผู้ใช้ของสาขาเข้าถึงหน้าตรวจสูตรด้วยยอดซื้อไม่ได้")
    if month and not _MONTH.match(month):
        raise HTTPException(400, "เดือนต้องอยู่ในรูปแบบ YYYY-MM")
    with get_db() as con:
        _ensure(con)
        return _rc.check(con, month or None)


@router.get("/api/bom-center/recipe-check")
def bc_recipe_check(request: Request, month: Optional[str] = Query(None)):
    """ตรวจสูตรและหน่วยด้วยยอดซื้อจริง: per ingredient what the benchmark branches bought per ฿100,000 of sales against what the
    recipes say, for one month (default: the newest finished month with purchases). Read-only: nothing is changed."""
    return _recipe_check_or_403(request, month)


@router.get("/api/bom-center/recipe-check.xlsx")
def bc_recipe_check_xlsx(request: Request, month: Optional[str] = Query(None)):
    from openpyxl import Workbook
    r = _recipe_check_or_403(request, month)
    br = [b["branch"] for b in r["branches"]]
    heads = RECIPE_CHECK_HEADS + [f"{n} · {k}" for n in br for k in ("ซื้อจริง", "ตามสูตร", "ส่วนต่าง %")] + ["หมายเหตุ"]
    wb = Workbook()
    ws = wb.active
    notes = [f"ตรวจสูตรและหน่วยด้วยยอดซื้อจริง · เดือน {r['month']} · ค่ากลางสาขาของบริษัท = มัธยฐานของ {', '.join(br) or '-'}"
             " · ยอดขายไม่รวมภาษีมูลค่าเพิ่มและค่าบริการ · หน่วยเป็นหน่วยฐานของวัตถุดิบ"]
    if r["scaled"]:
        notes.append("ยอดซื้อของทั้งเดือนถูกปรับตามสัดส่วนวันที่มียอดขายรายวัน ("
                     + ", ".join(f"{b['branch']} {b['sales_days']}/{b['month_days']} วัน" for b in r["branches"] if b["scaled"]) + ")")
    notes.append(f"ช่วงยอมรับ ±{r['buffer_pct']:g}% ใช้ชั่วคราวจนกว่าจะมีผลตรวจนับปลายงวดครบทุกสาขา · สาขาที่มีผลตรวจนับครบใช้ยอดซื้อปรับด้วยสต็อกและเกณฑ์ผลต่างปกติ")
    _sheet(ws, _rc.SHEET, heads, " · ".join(notes))
    for x in r["rows"]:
        row = [x["code"], x["name"], x["base_unit"], x["bought"], x["recipe"], x["diff_pct"], x["tolerance_pct"], x["status_th"],
               x["impact_value"]]
        by = {b["branch_id"]: b for b in x["branches"]}
        for b in r["branches"]:
            y = by.get(b["branch_id"]) or {}
            row += [y.get("bought"), y.get("recipe"), y.get("diff_pct")]
        row.append("ตรวจหน่วยของวัตถุดิบ (หน่วยและการแปลง)" if x["status"] == "x10" else
                   "ตรวจรหัสและสูตรที่ใช้วัตถุดิบนี้" if x["status"] in ("no_buy", "no_recipe") else "")
        ws.append(row)
    for row in ws.iter_rows(min_row=3, max_row=ws.max_row):
        for c in row[3:5]:
            c.number_format = "#,##0.00"
        row[5].number_format = "0.0"
        row[8].number_format = "#,##0.00"
        for c in row[9:-1]:
            c.number_format = "#,##0.00"
    ws.column_dimensions["B"].width = 42
    ws.freeze_panes = "D3"
    return _xlsx(wb, f"bom_center_recipe_check_{r['month'] or 'none'}.xlsx")


# ── เคลมสินค้า (owner 2026-10-08): the branch reports goods that arrived wrong, with a reason (photos go by LINE); Sales Support decides ──
def _claim_scope(con, request: Request, branch_id: Optional[int]):
    """A branch login is pinned to its own branch; the centre names one, or None for every branch."""
    if _auth.scope_branch(request):
        return _stock_branch(con, request, branch_id)
    if branch_id is None:
        return None
    if not con.execute("SELECT 1 FROM branches WHERE branch_id=?", (branch_id,)).fetchone():
        raise HTTPException(404, "ไม่พบสาขา")
    return int(branch_id)


def _claim_head_or_403(con, request: Request, claim_id: int) -> dict:
    h = _cl._head(con, claim_id)
    if not h:
        raise HTTPException(404, "ไม่พบรายการเคลม")
    if _auth.scope_branch(request) and h["branch_id"] != _stock_branch(con, request, None):
        raise HTTPException(403, detail="บัญชีสาขาเข้าถึงได้เฉพาะเคลมของสาขาตัวเอง", headers={"X-Denied": "role"})
    return h


@router.get("/api/bom-center/claims/claimable")
def bc_claims_claimable(request: Request, order_id: int = Query(...), branch_id: Optional[int] = Query(None)):
    """The lines of a received order and how much of each can still be claimed."""
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        try:
            return {**_cl.claimable(con, bid, order_id), "reasons": [{"code": c, "label": t} for c, t in _cl.REASONS],
                    "max_photos": _cl.MAX_PHOTOS}
        except ValueError as e:
            raise HTTPException(400, str(e))


@router.get("/api/bom-center/claims")
def bc_claims_list(request: Request, status: Optional[str] = Query(None), branch_id: Optional[int] = Query(None),
                   limit: int = Query(50)):
    if status and status not in ("all", *_cl.STATUS_TH):
        raise HTTPException(400, "คำขอไม่ถูกต้อง")
    with get_db() as con:
        _ensure(con)
        bid = _claim_scope(con, request, branch_id)
        return {"claims": _cl.listing(con, bid, status, max(1, min(limit, 200))),
                "reasons": [{"code": c, "label": t} for c, t in _cl.REASONS],
                "statuses": [{"code": c, "label": t} for c, t in _cl.STATUS_TH.items()],
                "origins": [{"code": c, "label": t} for c, t in _cl.ORIGIN_TH.items()]}


@router.get("/api/bom-center/claims/{claim_id}")
def bc_claim_detail(claim_id: int, request: Request):
    with get_db() as con:
        _ensure(con)
        _claim_head_or_403(con, request, claim_id)
        return _cl.detail(con, claim_id)


@router.get("/api/bom-center/claims/{claim_id}/photo/{photo_id}")
def bc_claim_photo(claim_id: int, photo_id: int, request: Request):
    with get_db() as con:
        _ensure(con)
        _claim_head_or_403(con, request, claim_id)
        try:
            mime, data, _bid = _cl.photo(con, claim_id, photo_id)
        except ValueError as e:
            raise HTTPException(404, str(e))
    return Response(content=data, media_type=mime, headers={"Cache-Control": "private, max-age=3600",
                                                            "X-Content-Type-Options": "nosniff",
                                                            "Content-Disposition": "inline"})


@router.post("/api/bom-center/claims", dependencies=[Depends(require_write_token)])
async def bc_claim_create(request: Request, payload: str = Form(...), photos: List[UploadFile] = File(default=[]),
                          branch_id: Optional[int] = Query(None)):
    """One LATER claim = one received order + its lines with a reason each (multipart; photos are optional,
    the branch sends them to Sales Support by LINE)."""
    try:
        p = json.loads(payload)
        order_id = int(p["order_id"])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "ข้อมูลการเคลมไม่ถูกต้อง")
    raws = []
    for f in photos:
        b = await f.read(_cl.MAX_PHOTO_BYTES + 1)
        if b:
            raws.append(b)
    with get_db() as con:
        _ensure(con)
        bid = _stock_branch(con, request, branch_id)
        out = _write(con, lambda: _cl.create(con, bid, order_id, p.get("lines") or [], raws, p.get("note"), _who(request)))
    invalidate_cache("bc_")
    return out


@router.post("/api/bom-center/claims/{claim_id}/cancel", dependencies=[Depends(require_write_token)])
def bc_claim_cancel(claim_id: int, request: Request):
    with get_db() as con:
        _ensure(con)
        _claim_head_or_403(con, request, claim_id)
        out = _write(con, lambda: _cl.cancel(con, claim_id, _who(request)))
    invalidate_cache("bc_")
    return out


class ClaimDecision(BaseModel):
    claim_id: int
    decision: str                       # approve | reject
    lines: List[dict] = []              # [{code, approved_units}] — absent = everything claimed
    note: Optional[str] = None


@router.post("/api/bom-center/claim-act/decide", dependencies=[Depends(require_write_token)])
def bc_claim_decide(body: ClaimDecision, request: Request):
    """Sales Support: approve or reject (a reason is required). A claim raised later adjusts the stock when
    approved; one raised while receiving adds back whatever is not approved."""
    with get_db() as con:
        _ensure(con)
        out = _write(con, lambda: _cl.decide(con, body.claim_id, body.decision, body.lines, body.note, _who(request)))
    invalidate_cache("bc_")
    return out


# ── Phase 2 · S9: the centre's side — Sales Support queue, Ops compliance, monthly (hub.py) ──
_HUB_STATUS_TH = {"open": "รอยืนยันยอดส่งและคลังจัดส่งแล้ว", "submitted": "รอยืนยันยอดส่ง", "processed": "คลังจัดส่งแล้ว",
                  "received": "สาขารับแล้ว", "all": "ทุกสถานะ"}


def _hub_status_or_400(status: Optional[str]) -> str:
    st = status or "open"
    if st not in _HUB_STATUS_TH:
        raise HTTPException(400, "คำขอไม่ถูกต้อง")
    return st


def _ids_or_filter(con, ids: Optional[str], date_from: Optional[str], date_to: Optional[str],
                   status: str = "open", keyed: Optional[int] = None) -> list:
    """The orders to consolidate: named ids, else those of `status` in the date range
    (open = sent or processed, the ones Sales Support still buys for)."""
    if ids:
        try:
            return [int(x) for x in ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(400, "คำขอไม่ถูกต้อง")
    sts = ("submitted", "processed") if status == "open" else ("all",) if status == "all" else (status,)
    out = []
    for st in sts:
        out += [r["order_id"] for r in _hub.queue(con, st, date_from, date_to, keyed)]
    return out


def _sheet_title(name: str, used: set) -> str:
    """An Excel sheet title: 31 characters, none of the characters Excel forbids (colon,
    slashes, question mark, asterisk, brackets), unique in the workbook."""
    t = re.sub(r"[:\\/?*\[\]]", " ", str(name or "แผ่นงาน")).strip()[:31] or "แผ่นงาน"
    base, n = t, 2
    while t in used:
        suf = f" ({n})"
        t = base[:31 - len(suf)] + suf
        n += 1
    used.add(t)
    return t


@router.get("/api/bom-center/hub/queue")
def bc_hub_queue(status: str = Query("submitted"), date_from: Optional[str] = Query(None),
                 date_to: Optional[str] = Query(None), keyed: Optional[int] = Query(None)):
    if status not in ("submitted", "processed", "received", "all"):
        raise HTTPException(400, "คำขอไม่ถูกต้อง")
    if keyed not in (None, 0, 1):
        raise HTTPException(400, "คำขอไม่ถูกต้อง")
    with get_db() as con:
        _ensure(con)
        rows = _hub.queue(con, status, date_from, date_to, keyed)
    return {"status": status, "orders": rows, "reasons": [{"code": k, "label": v} for k, v in _hub.SS_REASONS],
            "sla": {"cutoff_time": f"{_fc.cutoff_time():%H:%M}", "process_by": _hub.SLA_PROCESS_BY}}


@router.get("/api/bom-center/hub/order/{order_id}")
def bc_hub_order(order_id: int):
    with get_db() as con:
        _ensure(con)
        try:
            o = _or.detail(con, order_id)
        except ValueError as e:
            raise HTTPException(404, str(e))
        o["branch"] = (con.execute("SELECT name FROM branches WHERE branch_id=?", (o["branch_id"],)).fetchone() or [None])[0]
        o["claims"] = _cl.for_order(con, order_id)
        o["accounting_keyed_date"] = _hub.keyed_date(o.get("accounting_keyed_at"))
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


class KeyedBody(BaseModel):
    order_ids: List[int] = []
    keyed: bool = True


@router.post("/api/bom-center/hub/keyed", dependencies=[Depends(require_write_token)])
def bc_hub_keyed(body: KeyedBody, request: Request):
    """Sales Support marks (or unmarks) orders it has keyed into the external accounting system."""
    with get_db() as con:
        _ensure(con)
        out = _write(con, lambda: _hub.set_keyed(con, body.order_ids, body.keyed, _who(request)))
    invalidate_cache("bc_")
    return out


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
                             split: Optional[str] = Query(None)):
    """The consolidated workbook for the orders of `status` (default open): the total per delivery day and the
    lines per branch. Every central item comes from one company, so the note row names it. `split` is accepted
    and ignored (old links and bookmarks still carry it)."""
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
            dlv = o["delivery"]
            for ln in o["lines"]:
                q = ln["processed_units"] if ln["processed_units"] is not None else ln["confirmed_units"]
                if q:
                    per_branch.append([bname, dlv, o["cycle_date"], o["round"], ln["code"], ln["name"], q,
                                       "แพ็ค" if ln["order_unit"] != "base" else "หน่วยฐาน", o["po_ref"], o["order_id"],
                                       "ส่งล่าช้า (มีค่าปรับ)" if o.get("late") else "",
                                       (f"คีย์แล้ว {_hub.keyed_date(o['accounting_keyed_at'])}"
                                        if o.get("accounting_keyed_at") else "ยังไม่คีย์")])
    wb = Workbook()
    ws = wb.active
    br = c["branches"]
    _sheet(ws, "รวมตามวันส่ง", ["วันส่ง", "รหัส", "วัตถุดิบ", "หน่วย", "รวม"] + br,
           f"สินค้าส่วนกลางสั่งจาก {c['supplier']} · ใบสั่งซื้อสถานะ “{_HUB_STATUS_TH[st]}”"
           + (f" วันตัดรอบ {date_from or '…'} ถึง {date_to or '…'}" if (date_from or date_to) else "")
           + " สรุปรวมตามวันส่ง (ใช้ยอดส่งจริงของคลัง ถ้ายังไม่มีใช้ยอดที่สาขายืนยัน)")
    for g in c["groups"]:
        for it in g["items"]:
            ws.append([g["delivery"], it["code"], it["name"], it["unit"], it["total"]] +
                      [it["by_branch"].get(b) for b in br])
    ws2 = wb.create_sheet()
    _sheet(ws2, "ต่อสาขา", ["สาขา", "วันส่ง", "วันตัดรอบ", "รอบส่ง", "รหัส", "วัตถุดิบ", "จำนวน", "หน่วย", "PO", "ใบสั่งซื้อ", "ล่าช้า", "คีย์บัญชี"],
           "ทุกบรรทัดของใบสั่งซื้อที่เลือก")
    for r in per_branch:
        ws2.append(r)
    _for_accounting(wb)
    return _xlsx(wb, f"bom_center_orders_{st}_{date_from or 'all'}_{date_to or 'all'}.xlsx")


def _dates_or_week(date_from: Optional[str], date_to: Optional[str]) -> tuple:
    t = bkk_today()
    d1 = date_to or t.isoformat()
    d0 = date_from or (date.fromisoformat(d1) - timedelta(days=13)).isoformat()
    for d in (d0, d1):
        try:
            date.fromisoformat(d)
        except ValueError:
            raise HTTPException(400, "วันที่ไม่ถูกต้อง")
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
    _sheet(ws, "ตรวจสอบการสั่ง", ["วันตัดรอบ", "สาขา", "รอบส่ง", "สถานะ", "แนะนำ ฿", "สาขายืนยัน ฿", "ส่งจริง ฿",
                                       "รับจริง ฿", "ยืนยันเทียบแนะนำ %", "คลังเปลี่ยน %", "ส่งขาด %", "ส่งทันเวลา",
                                       "ยืนยันยอดส่งทันเวลา", "ล่าช้า"],
           f"รอบส่งที่ปฏิทินกำหนด {d0} ถึง {d1} · สถานะ “ตามแนะนำ” หมายถึงยอดยืนยันต่างจากยอดแนะนำไม่เกิน ±{_hub.TOL_PCT:g}%")
    for r in c["rows"]:
        ws.append([r["cycle_date"], r["branch"], r["round"], th.get(r["status"], r["status"]), r["suggested"], r["confirmed"],
                   r["processed"], r["received"], r.get("confirmed_vs_suggested_pct"), r.get("centre_change_pct"),
                   r.get("delivery_gap_pct"), r.get("submitted_on_time"), r.get("processed_on_time"),
                   "ส่งล่าช้า (มีค่าปรับ)" if r.get("late") else ""])
    w2 = wb.create_sheet()                                   # PR-J4: how each branch kept its daily issue slips in the same dates
    _sheet(w2, "ใบตัดเบิก", LOG_SUMMARY_HEADERS,
           f"การทำใบตัดเบิกของแต่ละสาขา {d0} ถึง {d1} · นับเฉพาะวันที่สาขามียอดขาย · ตรงวัน % = ยืนยันตรงวัน ÷ ใบที่ยืนยันแล้ว · "
           "มีใบ % = ใบที่ยืนยันแล้ว ÷ วันขาย")
    for s in c["issues"]:
        w2.append([s["branch"], s["trading_days"], s["on_time"], s["late"], s["draft"], s["missing"], s["on_time_pct"],
                   s["coverage_pct"], s["reopens"], s["reconfirms"], s["late_orders"], s["claims"]])
    w2.column_dimensions["A"].width = 28
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
                            set_local: bool = Query(True), month: Optional[str] = Query(None),
                            code_choices: Optional[str] = Form(None), alias_system: str = Form("acc")):
    """The centre's monthly report (or this list's own export) -> the central list.
    Everything not on it becomes 'local' (the branch buys it itself)."""
    raw = await _read_xlsx(file)
    choices = _choices_or_400(code_choices)
    with get_db() as con:
        _ensure(con)
        p = _parse_central(con, raw, month)
        if dry_run:
            pl = _ce.plan_catalogue(con, p)
            return {"dry_run": True, "month": p["month"], "sheet": p["sheet"], "branches": p["branches"],
                    **pl["counts"], "rows": pl["rows"]}
        out = _write(con, lambda: _ce.apply_catalogue(con, p, _who(request), set_local_others=set_local, choices=choices,
                                                      can_alias=_can_alias(request), system=alias_system))
    invalidate_cache("bc_")
    _fc.clear_usage_cache()           # an alias recorded by the import changes how the usage model reads a code
    _drop_bom_days()
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
           "บัญชีสินค้าที่สาขาสั่งจากไดกิโตะ กรุ๊ป · อัตราแปลงไม่ถูกต้อง: แก้คอลัมน์ “1 หน่วยขาย = หน่วยฐาน” แล้วนำเข้าอีกครั้ง"
           " · สินค้าที่ไม่อยู่ในบัญชีนี้ สาขาซื้อเอง")
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
    _fc.clear_usage_cache()           # the order plan's usage model reads this
    _drop_bom_days()             # … and so does the BoM day cache of the issue reports
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
        raise HTTPException(400, "คำขอไม่ถูกต้อง")
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
                                  branch_ids: Optional[str] = Query(None), receipt_date: Optional[str] = Query(None),
                                  code_choices: Optional[str] = Form(None), alias_system: str = Form("acc")):
    """The centre's monthly report ONCE: the central list AND one goods-received note per
    branch, previewed together and saved in one transaction — the list first, so the
    notes convert with the order units it puts in the master. The preview shows every
    branch; the save books the branches named in branch_ids and none when it is empty."""
    raw = await _read_xlsx(file)
    try:
        ids = [int(x) for x in branch_ids.split(",") if x.strip()] if branch_ids else []
    except ValueError:
        raise HTTPException(400, "คำขอไม่ถูกต้อง")
    who = _who(request)
    choices = _choices_or_400(code_choices)
    with get_db() as con:
        _ensure(con)
        p = _parse_central(con, raw, month)
        try:
            rows = _ce.plan_catalogue(con, p)["rows"] if dry_run else []
            cat = _ce.apply_catalogue(con, p, who, set_local_others=set_local, choices=choices,
                                      can_alias=_can_alias(request), system=alias_system)
            if not p.get("month"):          # the list's own export: no month, no quantities → the list alone
                if ids:
                    raise ValueError("ไฟล์นี้ไม่มีงวดของรายงาน จึงสร้างใบรับสินค้าไม่ได้ · เลือกเดือนของรายงาน")
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
        _fc.clear_usage_cache()           # an alias recorded by the import changes how the usage model reads a code
        _drop_bom_days()
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
    heads = ["โซน", "รหัส", "ชื่อ", "หน่วยนับ", "จำนวนที่นับ", "1 หน่วยนับ = หน่วยฐาน", "หน่วยฐาน", "กลุ่มมูลค่า", "ต้องนับ",
             "หมายเหตุ"]
    _sheet(wb.active, "ตรวจนับ", heads,
           f"แบบฟอร์มตรวจนับสินค้าคงเหลือ สาขา{name} · วันที่ตรวจนับ {count_date or '……'} (นับก่อนเปิดร้านและก่อนรับสินค้าของวันนั้น)"
           " · กรอกจำนวนตามหน่วยนับ (เปลี่ยนหน่วยได้ เช่น แพ็ค/กิโลกรัม) · ช่องที่เว้นว่างถือว่าไม่ได้ตรวจนับ")
    ws = wb.active
    for r in rows:
        ws.append([r["zone"], r["code"], r["name"], r["count_unit"], None, r["per_count_unit"], r["base_unit"],
                   _CLASS_SHORT.get(r["class"], r["class"]), "ต้องนับ" if r["required"] else "", None])
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
