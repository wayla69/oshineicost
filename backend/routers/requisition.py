"""Requisitions vs recipes — what the branches DREW against what the recipes say
they should have used.

The app prices food cost off recipes (BoM); operations reads it off the stores'
issue reports. Asked on 2026-09-17 why operations saw 70-80% where the app showed
less, the answer turned out to be neither side's error: over พ.ค.-มิ.ย. 2569 the
five master branches drew ฿539/head against a recipe cost of ฿468/head — 1.15x,
i.e. 78.4% by draw vs 68.1% by recipe. That gap (over-portioning, waste, stock
build-up, dishes with no recipe) is the single largest controllable number in
the business, and it existed only in a throwaway script. This makes it a report.

(The throwaway script first said 1.37x / 56.8%. It had read recipe cost from a
stale local database that still priced durian at ฿20-30 a portion instead of ฿68
— ฿73 a head of understated recipe cost. One more reason the number belongs in
the app, computed by the same engine as every other figure, not beside it.)

READ THE NUMBER HONESTLY: a requisition is goods ISSUED to the branch, not goods
USED. A month that builds stock reads high and the next one low, so "by draw" is
an upper bound until closing stock counts are entered; with opening and closing
counts the report switches that branch-month to used = opening + drawn - closing.

Source file: the accounting system's "รายงานจ่ายสินค้าภายใน" workbook, one sheet
per store ("Use KR1", "Use CNX", …). Both tables live only on the running site
and are preserved across the daily DB swap (core._PRESERVED_EXACT).
"""
import io
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from core import _cogs_case, get_db, invalidate_cache, require_write_token

router = APIRouter()

# sheet code -> branches.branch_id. The two เรนชิน sheets (RKR, RUB) are a sister
# brand with no POS branch here and a different item-code scheme — skipped, and
# reported as skipped so nobody wonders where their rows went.
SHEET2BR = {"KR1": 4, "CNX": 7, "SRN": 24, "UDN": 25, "UBN": 26}
SISTER_SHEETS = {"RKR", "RUB"}
# A few recipes name an ingredient by a placeholder rather than its stock code.
# Without the bridge the biggest single line in the stores' issues — ฿7.6M of
# durian over two months — reads as "in no recipe at all" while ฿68 a portion of
# it is in fact booked.
RECIPE_CODE_TO_STOCK = {"EST-DURIAN": "FV00043"}

DDL = """
CREATE TABLE IF NOT EXISTS requisition_line(
  period_month TEXT NOT NULL, branch_id INTEGER NOT NULL, doc_no TEXT, seq INTEGER,
  item_code TEXT NOT NULL, item_name TEXT, unit TEXT, qty REAL, unit_price REAL, value REAL,
  uploaded_at TEXT);
CREATE INDEX IF NOT EXISTS idx_reqline_month ON requisition_line(period_month, branch_id);
CREATE TABLE IF NOT EXISTS stock_count(
  period_month TEXT NOT NULL, branch_id INTEGER NOT NULL, closing_value REAL NOT NULL,
  note TEXT, entered_by TEXT, entered_at TEXT, PRIMARY KEY(period_month, branch_id));
"""

_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(25\d{2})")


def _ensure(con):
    con.executescript(DDL)


def parse_workbook(data: bytes):
    """-> (lines, info). lines = dicts ready for requisition_line; info says which
    sheets were read, which were skipped and why, and the month each one covers."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    lines, info = [], {"sheets": [], "skipped": []}
    for sn in wb.sheetnames:
        code = sn.replace("Use-", "").replace("Use ", "").strip().upper()
        if code in SISTER_SHEETS:
            info["skipped"].append({"sheet": sn, "why": "แบรนด์เรนชิน — ไม่มีสาขาใน POS นี้"})
            continue
        if code not in SHEET2BR:
            info["skipped"].append({"sheet": sn, "why": "ไม่รู้จักรหัสสาขานี้"})
            continue
        month, doc, n, total = None, None, 0, 0.0
        for r in wb[sn].iter_rows(values_only=True):
            c = list(r[1:13]) + [None] * 13
            a, _dt, _dept, _note, seq, item, name, _wh, qty, unit, price, value = c[:12]
            sa = str(a) if a is not None else ""
            if month is None and "วันที่จาก" in sa:
                m = _DATE_RE.search(sa)
                if m:
                    month = f"{int(m.group(3)) - 543:04d}-{int(m.group(2)):02d}"
            if a is not None and seq is None and re.match(r"^[A-Z]{2}\d{6,}", sa):
                doc = sa.strip()                       # a document header row (OU6906001 …)
            if seq is None or item is None or name is None:
                continue
            try:
                q, p, v = float(qty or 0), float(price or 0), float(value or 0)
                s = int(float(seq))
            except (TypeError, ValueError):
                continue
            if q <= 0:
                continue
            lines.append({"sheet": code, "branch_id": SHEET2BR[code], "doc_no": doc, "seq": s,
                          "item_code": str(item).strip(), "item_name": str(name).strip(),
                          "unit": str(unit or "").strip(), "qty": q, "unit_price": p, "value": v})
            n += 1
            total += v
        if month is None:
            info["skipped"].append({"sheet": sn, "why": "หาเดือนจากหัวรายงานไม่เจอ (วันที่จาก …)"})
            lines = [ln for ln in lines if ln["sheet"] != code]
            continue
        for ln in lines:
            if ln["sheet"] == code:
                ln["period_month"] = month
        info["sheets"].append({"sheet": sn, "branch_id": SHEET2BR[code], "month": month,
                               "rows": n, "value": round(total, 2)})
    return lines, info


@router.post("/api/requisition/upload", dependencies=[Depends(require_write_token)])
async def requisition_upload(file: UploadFile = File(...), dry_run: bool = Query(False)):
    """Upload one month's รายงานจ่ายสินค้าภายใน. Re-uploading a month REPLACES the
    branch-months the file contains (and only those), so a corrected file is safe."""
    data = await file.read()
    try:
        lines, info = parse_workbook(data)
    except Exception as e:
        raise HTTPException(400, f"อ่านไฟล์ไม่ได้: {e}")
    if not lines:
        raise HTTPException(400, {"detail": "ไม่พบรายการเบิกในไฟล์", "info": info})
    info["dry_run"] = dry_run
    if dry_run:
        return info
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    with get_db() as con:
        _ensure(con)
        for s in info["sheets"]:
            con.execute("DELETE FROM requisition_line WHERE period_month=? AND branch_id=?",
                        (s["month"], s["branch_id"]))
        con.executemany(
            "INSERT INTO requisition_line(period_month, branch_id, doc_no, seq, item_code, item_name,"
            " unit, qty, unit_price, value, uploaded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [(ln["period_month"], ln["branch_id"], ln["doc_no"], ln["seq"], ln["item_code"],
              ln["item_name"], ln["unit"], ln["qty"], ln["unit_price"], ln["value"], now) for ln in lines])
        con.commit()
    invalidate_cache()
    return info


class _StockCount(BaseModel):
    period_month: str
    branch_id: int
    closing_value: float
    note: Optional[str] = ""


@router.post("/api/requisition/stock-count", dependencies=[Depends(require_write_token)])
def stock_count_set(body: _StockCount):
    """Closing stock VALUE (฿) for one branch-month. With this month's AND the
    previous month's count, the report uses used = opening + drawn - closing."""
    if not re.match(r"^\d{4}-\d{2}$", body.period_month) or body.closing_value < 0:
        raise HTTPException(400, "period_month ต้องเป็น YYYY-MM และมูลค่าต้องไม่ติดลบ")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    with get_db() as con:
        _ensure(con)
        con.execute("INSERT OR REPLACE INTO stock_count VALUES(?,?,?,?,?,?)",
                    (body.period_month, body.branch_id, body.closing_value, body.note or "", "-", now))
        con.commit()
    invalidate_cache()
    return {"ok": True}


def _prev_month(m: str) -> str:
    y, mo = int(m[:4]), int(m[5:7])
    return f"{y - 1}-12" if mo == 1 else f"{y}-{mo - 1:02d}"


@router.get("/api/requisition/vs-recipe")
def requisition_vs_recipe(month: Optional[str] = Query(None),
                          month_to: Optional[str] = Query(None),
                          top_n: int = Query(25)):
    """Per branch: ฿ drawn (or used, where stock counts allow) vs recipe COGS, per
    head and as food-cost %, plus the ingredients where the gap is largest."""
    cogs = _cogs_case("ssm", "sk", "sc")
    with get_db() as con:
        _ensure(con)
        months = [r[0] for r in con.execute(
            "SELECT DISTINCT period_month FROM requisition_line ORDER BY period_month")]
        if not months:
            return {"ready": False, "months": [], "why": "ยังไม่มีใบเบิกในระบบ — อัปโหลดรายงานจ่ายสินค้าภายในก่อน"}
        m_from = month if month in months else months[-1]
        m_to = month_to if (month_to in months and month_to >= m_from) else m_from
        span = [m for m in months if m_from <= m <= m_to]
        ph = ",".join("?" * len(span))

        drawn = {}
        for bid, m, v in con.execute(
                f"SELECT branch_id, period_month, SUM(value) FROM requisition_line "
                f"WHERE period_month IN ({ph}) GROUP BY branch_id, period_month", span):
            drawn[(bid, m)] = v or 0.0
        counts = {(b, m): v for b, m, v in con.execute(
            "SELECT branch_id, period_month, closing_value FROM stock_count")}
        bids = sorted({b for b, _ in drawn})
        bph = ",".join("?" * len(bids))
        names = dict(con.execute(f"SELECT branch_id, name FROM branches WHERE branch_id IN ({bph})", bids))

        rec = {}
        for bid, m, rev, cg in con.execute(f"""
                SELECT ssm.branch_id, ssm.period_month, SUM(ssm.subtotal_ex_vat), SUM({cogs})
                  FROM v_sales_sku_monthly ssm JOIN skus sk ON sk.sku_id = ssm.sku_id
                  LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
                 WHERE ssm.branch_id IN ({bph}) AND ssm.period_month IN ({ph})
                 GROUP BY ssm.branch_id, ssm.period_month""", bids + span):
            rec[(bid, m)] = (rev or 0.0, cg or 0.0)
        covers = {}
        # the monthly roll-up, not sales_transaction: same covers every other
        # per-head report uses, and no strftime scan over 750k bills
        for bid, m, px in con.execute(f"""
                SELECT branch_id, period_month, SUM(customer_count) FROM sales_month_summary
                 WHERE branch_id IN ({bph}) AND period_month IN ({ph}) GROUP BY 1, 2""", bids + span):
            covers[(bid, m)] = px or 0

        rows, tot = [], {"covers": 0, "issued": 0.0, "actual": 0.0, "recipe": 0.0, "rev": 0.0}
        for bid in bids:
            agg = {"covers": 0, "issued": 0.0, "actual": 0.0, "recipe": 0.0, "rev": 0.0}
            counted = 0
            for m in span:
                d = drawn.get((bid, m), 0.0)
                opening, closing = counts.get((bid, _prev_month(m))), counts.get((bid, m))
                used = d
                if opening is not None and closing is not None:
                    used = opening + d - closing
                    counted += 1
                rev, cg = rec.get((bid, m), (0.0, 0.0))
                agg["issued"] += d; agg["actual"] += used; agg["recipe"] += cg
                agg["rev"] += rev; agg["covers"] += covers.get((bid, m), 0)
            for k in tot:
                tot[k] += agg[k]
            rows.append(_row(names.get(bid, str(bid)), bid, agg,
                             "used" if counted == len(span) else "issued", counted, len(span)))

        # ingredient gap: ฿ drawn per item code vs ฿ the recipes book for it
        booked = {}
        for code, v in con.execute(f"""
                SELECT bi.ingredient_code, SUM(ssm.qty * bi.ingredient_cost)
                  FROM v_sales_sku_monthly ssm JOIN bom_ingredients bi ON bi.sku_id = ssm.sku_id
                 WHERE ssm.branch_id IN ({bph}) AND ssm.period_month IN ({ph})
                 GROUP BY bi.ingredient_code""", bids + span):
            code = RECIPE_CODE_TO_STOCK.get(code, code)
            booked[code] = booked.get(code, 0.0) + (v or 0.0)
        items = []
        for code, name, unit, q, v in con.execute(
                f"""SELECT item_code, MAX(item_name), MAX(unit), SUM(qty), SUM(value) FROM requisition_line
                    WHERE period_month IN ({ph}) GROUP BY item_code ORDER BY SUM(value) DESC""", span):
            b = booked.get(code, 0.0)
            items.append({"code": code, "name": name, "unit": unit, "qty": round(q or 0, 2),
                          "drawn": round(v or 0), "booked": round(b),
                          "gap": round((v or 0) - b),
                          "in_recipes": code in booked})
        items.sort(key=lambda x: -x["gap"])
    basis = "used" if rows and all(r["basis"] == "used" for r in rows) else "issued"
    return {"ready": True, "months": months, "month_from": m_from, "month_to": m_to,
            "branches": sorted(rows, key=lambda r: -(r["ratio"] or 0)),
            "total": _row("รวม", None, tot, basis, 0, 0),
            "ingredients": items[:max(1, min(100, top_n))],
            "not_in_any_recipe": round(sum(i["drawn"] for i in items if not i["in_recipes"])),
            "caveat": ("เบิก = ของที่จ่ายออกจากคลัง ไม่ใช่ของที่ใช้จริง เดือนที่ตุนสต๊อกจะสูงเกินจริง — "
                       "กรอกมูลค่าสต๊อกสิ้นเดือน (เดือนนี้และเดือนก่อน) เพื่อให้คิดเป็น ใช้จริง = ต้นงวด + เบิก − ปลายงวด")}


def _row(name, bid, a, basis, counted, n_months):
    px, rev = a["covers"], a["rev"]
    rd = lambda x, n=2: round(x, n) if x is not None else None
    return {"branch": name, "branch_id": bid, "covers": px, "basis": basis,
            "months_counted": counted, "months": n_months,
            "issued": round(a["issued"]), "actual": round(a["actual"]), "recipe": round(a["recipe"]),
            "actual_head": rd(a["actual"] / px) if px else None,
            "recipe_head": rd(a["recipe"] / px) if px else None,
            "rev_head": rd(rev / px) if px else None,
            "ratio": rd(a["actual"] / a["recipe"]) if a["recipe"] else None,
            "food_pct_actual": rd(a["actual"] / rev * 100, 1) if rev else None,
            "food_pct_recipe": rd(a["recipe"] / rev * 100, 1) if rev else None,
            "gap_value": round(a["actual"] - a["recipe"])}
