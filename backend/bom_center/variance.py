# -*- coding: utf-8 -*-
"""Phase 2 · Sprint S7 — goods received and usage variance per count period
(docs/BOM_CENTER_PLAN.md §7.1-2/4, §7.2-4).

A PERIOD runs from one submitted count to the next. A count reflects what is on
the shelves BEFORE that day's trading and deliveries, so a period covers the
sales and the deliveries from the begin count's date up to the day before the
end count.

    actual use   = begin + received − end                 (what left the shelves)
    BoM use      = Σ plates sold × recipe (+ trim loss)   (what the recipes say)
    gap          = actual − BoM          > 0 = used more than the recipes allow

Nothing is guessed: an item missing from either count has no actual use, and a
period whose deliveries the branch has not confirmed as complete is shown as
INCOMPLETE (a missing delivery would read as a shortage). Thresholds follow the
plan: class A ±5 %, B ±10 %, C ±20 % (class = share of BoM ฿ in the period).
"""
from datetime import date, datetime, timedelta, timezone

from .master import audit
from .units import Converter

THRESHOLD = {"A": 5.0, "B": 10.0, "C": 20.0}
REASONS = (("waste", "วัตถุดิบเสีย / ทิ้ง"), ("expired", "หมดอายุ"), ("staff", "ชิม / อาหารพนักงาน"),
           ("overportion", "เสิร์ฟเกินปริมาณตามสูตร"), ("transfer", "โอนให้สาขาอื่น"), ("miscount", "ตรวจนับผิด"),
           ("receipt", "บันทึกรับสินค้าไม่ครบ"), ("other", "อื่น ๆ"))
REASON_TH = dict(REASONS)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── goods received ───────────────────────────────────────────────────────────
def add_receipt(con, branch_id: int, receipt_date: str, lines: list, who: str, ref: str = None,
                note: str = None, source: str = "branch") -> dict:
    try:
        date.fromisoformat(receipt_date)
    except (TypeError, ValueError):
        raise ValueError("วันที่รับสินค้าต้องอยู่ในรูปแบบ YYYY-MM-DD")
    conv = Converter(con)
    clean, bad = {}, []
    for ln in lines or []:
        code = (ln.get("code") or "").strip()
        if not code:
            continue
        try:
            q = float(ln.get("qty"))
        except (TypeError, ValueError):
            raise ValueError(f"{code}: จำนวนต้องเป็นตัวเลข")
        if q <= 0:
            raise ValueError(f"{code}: จำนวนต้องมากกว่า 0")
        unit = (ln.get("unit") or "").strip() or conv.base_unit(code)
        qb, how = conv.to_base(code, q, unit)
        if qb is None:
            bad.append(f"{code}: {how}")
        prev = clean.get(code)
        if prev:                                             # the same item twice in one delivery: add up
            if prev[1] != unit:
                raise ValueError(f"{code}: ระบุซ้ำด้วยหน่วยที่ต่างกัน ({prev[1]} / {unit}) กรุณารวมเป็นบรรทัดเดียว")
            q = prev[0] + q
            qb = None if qb is None or prev[2] is None else prev[2] + qb
        clean[code] = (q, unit, qb)
    if not clean:
        raise ValueError("ยังไม่มีรายการรับสินค้า")
    if bad:
        raise ValueError("แปลงหน่วยไม่ได้: " + "; ".join(bad[:3]) + " กรุณาแจ้งส่วนกลางให้เพิ่มกฎหน่วย")
    cur = con.execute("INSERT INTO bc_receipt(branch_id, receipt_date, source, ref, note, entered_by, created_at)"
                      " VALUES(?,?,?,?,?,?,?)", (branch_id, receipt_date, source, ref, note, who, now()))
    rid = cur.lastrowid
    con.executemany("INSERT INTO bc_receipt_line(receipt_id, ingredient_code, qty, unit, qty_base) VALUES(?,?,?,?,?)",
                    [(rid, c, q, u, qb) for c, (q, u, qb) in clean.items()])
    audit(con, who, "receipt", "bc_receipt", str(rid), None, {"branch_id": branch_id, "date": receipt_date,
                                                             "lines": len(clean)})
    return {"receipt_id": rid, "lines": len(clean)}


def _closed_through(con, branch_id: int):
    """The last END count's date: receipts before it belong to a measured period."""
    r = con.execute("SELECT MAX(count_date) FROM bc_stock_count WHERE branch_id=? AND kind='end'"
                    " AND COALESCE(status,'submitted')='submitted'", (branch_id,)).fetchone()
    return r[0] if r else None


def delete_receipt(con, branch_id: int, receipt_id: int, who: str) -> bool:
    r = con.execute("SELECT receipt_date, source FROM bc_receipt WHERE receipt_id=? AND branch_id=?",
                    (receipt_id, branch_id)).fetchone()
    if not r:
        raise ValueError("ไม่พบใบรับสินค้านี้")
    if r[1] == "claim":
        raise ValueError("รายการนี้เกิดจากการอนุมัติเคลม ลบไม่ได้ หากต้องแก้ไขให้แจ้งประสานงานขาย")
    closed = _closed_through(con, branch_id)
    if closed and r[0] < closed:
        raise ValueError(f"ใบรับสินค้าวันที่ {r[0]} อยู่ในรอบที่ตรวจนับปิดแล้ว ({closed}) จึงลบไม่ได้ กรุณาแจ้งส่วนกลาง")
    con.execute("DELETE FROM bc_receipt_line WHERE receipt_id=?", (receipt_id,))
    con.execute("DELETE FROM bc_receipt WHERE receipt_id=?", (receipt_id,))
    audit(con, who, "receipt_delete", "bc_receipt", str(receipt_id), r[0], None)
    return True


def receipts(con, branch_id: int, limit: int = 30) -> list:
    names = dict(con.execute("SELECT code, name_th FROM bc_ingredient"))
    out = []
    for rid, d, src, ref, by in con.execute(
            "SELECT receipt_id, receipt_date, source, ref, entered_by FROM bc_receipt WHERE branch_id=?"
            " ORDER BY receipt_date DESC, receipt_id DESC LIMIT ?", (branch_id, limit)).fetchall():
        lines = [{"code": c, "name": names.get(c), "qty": q, "unit": u, "qty_base": qb} for c, q, u, qb in con.execute(
            "SELECT ingredient_code, qty, unit, qty_base FROM bc_receipt_line WHERE receipt_id=? ORDER BY ingredient_code",
            (rid,))]
        out.append({"receipt_id": rid, "receipt_date": d, "source": src, "ref": ref, "entered_by": by, "lines": lines})
    closed = _closed_through(con, branch_id)
    for r in out:
        r["locked"] = bool(closed and r["receipt_date"] < closed)
    return out


# ── variance ─────────────────────────────────────────────────────────────────
def _count_lines(con, count_id: int) -> dict:
    return {c: qb for c, qb in con.execute(
        "SELECT ingredient_code, qty_base FROM bc_stock_count_line WHERE count_id=?", (count_id,)) if qb is not None}


def periods(con, branch_id: int) -> list:
    """Every measured period of a branch, newest first, with its headline numbers."""
    from .stock import periods as _p
    out = []
    for b, e in reversed(_p(con, branch_id)):
        v = period(con, branch_id, e["count_id"], summary_only=True)
        out.append(v)
    return out


def period(con, branch_id: int, end_count_id: int = None, summary_only: bool = False) -> dict:
    """Actual vs BoM use for one period (default: the latest)."""
    from .forecast import _abc, usage_history
    from .prices import price_for
    from .stock import periods as _p
    ps = _p(con, branch_id)
    if not ps:
        raise ValueError("ยังไม่มีรอบสำหรับเปรียบเทียบ ต้องตรวจนับอย่างน้อย 2 ครั้ง (ต้นงวดและปลายงวด)")
    pair = ps[-1] if end_count_id is None else next((p for p in ps if p[1]["count_id"] == int(end_count_id)), None)
    if pair is None:
        raise ValueError("ไม่พบรอบนี้")
    b, e = pair
    d0, d1 = date.fromisoformat(b["date"]), date.fromisoformat(e["date"])
    last_day = d1 - timedelta(days=1)
    month = f"{last_day.year:04d}-{last_day.month:02d}"
    begin, end = _count_lines(con, b["count_id"]), _count_lines(con, e["count_id"])
    rec = {}
    for c, qb in con.execute(
            "SELECT l.ingredient_code, SUM(l.qty_base) FROM bc_receipt r JOIN bc_receipt_line l USING(receipt_id)"
            " WHERE r.branch_id=? AND r.receipt_date>=? AND r.receipt_date<? GROUP BY l.ingredient_code",
            (branch_id, b["date"], e["date"])):
        rec[c] = qb or 0.0
    n_receipts = con.execute("SELECT COUNT(*) FROM bc_receipt WHERE branch_id=? AND receipt_date>=? AND receipt_date<?",
                             (branch_id, b["date"], e["date"])).fetchone()[0]
    theo, days = {}, []
    if last_day >= d0:
        use, days, _ = usage_history(con, branch_id, d0, last_day, month)
        theo = {c: sum(s.values()) for c, s in use.items()}
    conf = con.execute("SELECT receipts_confirmed_by, receipts_confirmed_at FROM bc_variance_period WHERE end_count_id=?",
                       (e["count_id"],)).fetchone()
    reasons = {c: (r, n, w, a) for c, r, n, w, a in con.execute(
        "SELECT ingredient_code, reason_code, note, who, at FROM bc_variance_reason WHERE end_count_id=?", (e["count_id"],))}
    price = {}

    def p(c):
        if c not in price:
            pr = price_for(con, c, month)
            price[c] = pr[0] if pr else None
        return price[c]
    klass = _abc({c: q * (p(c) or 0.0) for c, q in theo.items()})
    names = {c: (n, u) for c, n, u in con.execute("SELECT code, name_th, base_unit FROM bc_ingredient")}
    rows = []
    for c in sorted(set(begin) | set(end) | set(theo) | set(rec)):
        k = klass.get(c, "C")
        t = theo.get(c, 0.0)
        r = rec.get(c, 0.0)
        row = {"code": c, "name": (names.get(c) or (c,))[0], "base_unit": (names.get(c) or (None, None))[1],
               "class": k, "begin": begin.get(c), "received": r, "end": end.get(c), "bom": round(t, 3),
               "price_per_base": p(c)}
        if c not in begin or c not in end:
            row.update(status="not_counted", actual=None, gap=None, gap_pct=None, gap_value=None)
        else:
            act = begin[c] + r - end[c]
            gap = act - t
            pct = (100.0 * gap / t) if t > 0 else None
            pv = p(c)
            row.update(actual=round(act, 3), gap=round(gap, 3), gap_pct=round(pct, 1) if pct is not None else None,
                       gap_value=round(gap * pv, 2) if pv is not None else None)
            if act < 0:
                row["status"] = "negative"                   # more on the shelf than began + arrived: a missing receipt or a miscount
            elif t <= 0:
                row["status"] = "no_bom" if act > 0 else "ok"
            elif abs(pct) <= THRESHOLD[k]:
                row["status"] = "ok"
            else:
                row["status"] = "over" if gap > 0 else "under"
        rs = reasons.get(c)
        if rs:
            row.update(reason=rs[0], reason_th=REASON_TH.get(rs[0], rs[0]), reason_note=rs[1], reason_by=rs[2])
        row["needs_reason"] = row["status"] in ("over", "under", "negative", "no_bom") and not rs
        rows.append(row)
    flagged = [r for r in rows if r["status"] in ("over", "under", "negative", "no_bom")]
    summary = {
        "branch_id": branch_id, "begin_count_id": b["count_id"], "end_count_id": e["count_id"],
        "begin_date": b["date"], "end_date": e["date"], "days": (d1 - d0).days, "trading_days": len(days),
        "price_month": month, "receipts": n_receipts,
        "receipts_confirmed": bool(conf), "receipts_confirmed_by": conf[0] if conf else None,
        "complete": bool(conf),
        "gap_value": round(sum(r["gap_value"] or 0 for r in rows if r["gap_value"] is not None and r["status"] != "not_counted"), 2),
        "over_value": round(sum(r["gap_value"] for r in flagged if (r["gap_value"] or 0) > 0), 2),
        "bom_value": round(sum(r["bom"] * (r["price_per_base"] or 0) for r in rows), 2),
        "flagged": len(flagged), "needs_reason": sum(1 for r in rows if r["needs_reason"]),
        "not_counted": sum(1 for r in rows if r["status"] == "not_counted" and r["bom"] > 0),
    }
    if summary["bom_value"]:
        summary["gap_pct"] = round(100.0 * summary["gap_value"] / summary["bom_value"], 1)
    if summary_only:
        return summary
    order = {"negative": 0, "over": 1, "under": 2, "no_bom": 3, "not_counted": 4, "ok": 5}
    rows.sort(key=lambda r: (order[r["status"]], -abs(r["gap_value"] or 0), r["code"]))
    return dict(summary, rows=rows, reasons=[{"code": k, "label": v} for k, v in REASONS],
                thresholds=THRESHOLD)


def set_reason(con, branch_id: int, end_count_id: int, code: str, reason: str, note: str, who: str):
    if reason not in REASON_TH:
        raise ValueError("กรุณาเลือกเหตุผลจากรายการ")
    if reason == "other" and not (note or "").strip():
        raise ValueError("เหตุผล “อื่น ๆ” ต้องระบุคำอธิบาย")
    if not con.execute("SELECT 1 FROM bc_stock_count WHERE count_id=? AND branch_id=?", (end_count_id, branch_id)).fetchone():
        raise ValueError("ไม่พบรอบนี้ของสาขา")
    con.execute("INSERT INTO bc_variance_reason(end_count_id, ingredient_code, branch_id, reason_code, note, who, at)"
                " VALUES(?,?,?,?,?,?,?) ON CONFLICT(end_count_id, ingredient_code) DO UPDATE SET"
                " reason_code=excluded.reason_code, note=excluded.note, who=excluded.who, at=excluded.at",
                (end_count_id, code, branch_id, reason, (note or "").strip() or None, who, now()))
    audit(con, who, "variance_reason", "bc_variance_reason", f"{end_count_id}|{code}", None, reason)


def confirm_receipts(con, branch_id: int, end_count_id: int, who: str, undo: bool = False):
    if not con.execute("SELECT 1 FROM bc_stock_count WHERE count_id=? AND branch_id=?", (end_count_id, branch_id)).fetchone():
        raise ValueError("ไม่พบรอบนี้ของสาขา")
    if undo:
        con.execute("DELETE FROM bc_variance_period WHERE end_count_id=?", (end_count_id,))
    else:
        con.execute("INSERT OR REPLACE INTO bc_variance_period(end_count_id, branch_id, receipts_confirmed_by,"
                    " receipts_confirmed_at) VALUES(?,?,?,?)", (end_count_id, branch_id, who, now()))
    audit(con, who, "receipts_confirm" if not undo else "receipts_unconfirm", "bc_variance_period", str(end_count_id))


def overview(con) -> list:
    """The latest period of every branch that has one — the centre's list to check."""
    out = []
    for bid, name in con.execute("SELECT DISTINCT c.branch_id, b.name FROM bc_stock_count c JOIN branches b"
                                 " USING(branch_id) ORDER BY b.name").fetchall():
        try:
            s = period(con, bid, summary_only=True)
        except ValueError:
            s = {"branch_id": bid, "end_date": None}
        s["branch"] = name
        last = con.execute("SELECT MAX(count_date) FROM bc_stock_count WHERE branch_id=? AND"
                           " COALESCE(status,'submitted')='submitted'", (bid,)).fetchone()[0]
        s["last_count"] = last
        out.append(s)
    return out
