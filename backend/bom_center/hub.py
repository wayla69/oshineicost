# -*- coding: utf-8 -*-
"""Phase 2 · Sprint S9 — the centre's side of branch ordering (docs/BOM_CENTER_PLAN.md §7.1-8/9, §7.5).

  * Sales Support's QUEUE: every submitted order, with its delivery day; Sales Support
    sets what will actually be sent (a change needs a reason — out of stock, substitute,
    pack size …), marks it processed with a PO reference, and works from a consolidated
    view per supplier × delivery day (Excel). SLA: submitted before 10:00 on the order
    day → processed by 14:00 the same day (Bangkok time).
  * Ops' COMPLIANCE: for every cycle the order calendar expects, four numbers in ฿ —
    suggested · confirmed by the branch · processed by Sales Support · received — so a
    gap can be placed at the branch, at the centre, or in the delivery.
  * The MONTHLY usage-variance summary per branch (periods ending in the month), by
    class and reason, against the month before — also a section of the Management Report.
"""
from datetime import date, datetime, timedelta, timezone

from .master import audit

SS_REASONS = (("short", "ซัพพลายเออร์ของขาด / ส่งได้ไม่ครบ"), ("substitute", "เปลี่ยนเป็นสินค้าทดแทน"),
              ("pack", "ปรับตามขนาดแพ็ค / ขั้นต่ำ"), ("merge", "รวมกับรอบอื่น"), ("stock", "ส่วนกลางมีของให้แทน"),
              ("other", "อื่น ๆ"))
SS_REASON_TH = dict(SS_REASONS)
BKK = timezone(timedelta(hours=7))
SLA_SUBMIT_BY, SLA_PROCESS_BY = 10, 14        # hours, Bangkok
TOL_PCT = 20.0                                 # compliance: confirmed vs suggested ฿ within ±20 % = ok


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _bkk(ts: str):
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(BKK)


def _per(con) -> dict:
    return {c: p for c, p in con.execute("SELECT code, qty_per_recv FROM bc_ingredient")}


def _base(units, unit, per):
    if units is None:
        return None
    return units * ((per or 1.0) if unit and unit != "base" else 1.0)


def _value(con, order_id: int, field: str, month: str) -> float:
    from .prices import price_for
    per = _per(con)
    v = 0.0
    for c, q, u in con.execute(f"SELECT ingredient_code, {field}, order_unit FROM bc_order_line WHERE order_id=?",
                               (order_id,)):
        p = price_for(con, c, month)
        if p and q:
            v += _base(q, u, per.get(c)) * p[0]
    return round(v, 2)


def delivery_of(con, branch_id: int, rnd: str, cycle_date: str) -> str:
    from .forecast import cover_period
    try:
        return cover_period(con, branch_id, rnd, date.fromisoformat(cycle_date))[1].isoformat()
    except Exception:
        return cycle_date


def sla(submitted_at: str, processed_at: str, cycle_date: str) -> dict:
    s, p = _bkk(submitted_at), _bkk(processed_at)
    on_time_submit = bool(s and s.date().isoformat() <= cycle_date and
                          (s.date().isoformat() < cycle_date or s.hour < SLA_SUBMIT_BY))
    out = {"submitted_on_time": on_time_submit, "processed_on_time": None, "hours_to_process": None}
    if s and p:
        out["hours_to_process"] = round((p - s).total_seconds() / 3600.0, 1)
        if on_time_submit:
            due = datetime.fromisoformat(cycle_date).replace(hour=SLA_PROCESS_BY, tzinfo=BKK)
            out["processed_on_time"] = p <= due
    return out


# ── Sales Support's queue ────────────────────────────────────────────────────
def queue(con, status: str = "submitted", date_from: str = None, date_to: str = None) -> list:
    sts = ("submitted", "processed", "received") if status == "all" else (status,)
    q = ("SELECT o.order_id, o.branch_id, b.name, o.cycle_date, o.round, o.status, o.value, o.submitted_at,"
         " o.processed_at, o.processed_by, o.po_ref, o.stock_count_id,"
         " (SELECT COUNT(*) FROM bc_order_line l WHERE l.order_id=o.order_id AND COALESCE(l.confirmed_units,0)>0),"
         " (SELECT COUNT(*) FROM bc_order_line l WHERE l.order_id=o.order_id AND l.reason_code IS NOT NULL)"
         f" FROM bc_order o JOIN branches b USING(branch_id) WHERE o.status IN ({','.join('?' * len(sts))})")
    args = list(sts)
    if date_from:
        q += " AND o.cycle_date>=?"
        args.append(date_from)
    if date_to:
        q += " AND o.cycle_date<=?"
        args.append(date_to)
    q += " ORDER BY o.cycle_date, b.name, o.round"
    out = []
    for r in con.execute(q, args).fetchall():
        d = dict(zip(("order_id", "branch_id", "branch", "cycle_date", "round", "status", "value", "submitted_at",
                      "processed_at", "processed_by", "po_ref", "stock_count_id", "lines", "branch_changes"), r))
        d["delivery"] = delivery_of(con, d["branch_id"], d["round"], d["cycle_date"])
        d.update(sla(d["submitted_at"], d["processed_at"], d["cycle_date"]))
        d["stock_deducted"] = d.pop("stock_count_id") is not None
        out.append(d)
    return out


def process(con, order_id: int, lines: list, who: str, po_ref: str = None) -> dict:
    """Sales Support sets what will be sent. Default = what the branch confirmed."""
    o = con.execute("SELECT status, cycle_date FROM bc_order WHERE order_id=?", (order_id,)).fetchone()
    if not o:
        raise ValueError("ไม่พบออเดอร์")
    if o[0] not in ("submitted", "processed"):
        raise ValueError("ดำเนินการได้เฉพาะออเดอร์ที่สาขาส่งแล้ว")
    given = {(ln.get("code") or "").strip(): ln for ln in lines or []}
    for c, conf in con.execute("SELECT ingredient_code, confirmed_units FROM bc_order_line WHERE order_id=?",
                               (order_id,)).fetchall():
        ln = given.get(c)
        q = conf if not ln or ln.get("processed_units") is None else ln.get("processed_units")
        try:
            q = float(q or 0)
        except (TypeError, ValueError):
            raise ValueError(f"{c}: จำนวนต้องเป็นตัวเลข")
        if q < 0:
            raise ValueError(f"{c}: จำนวนติดลบไม่ได้")
        rs = ((ln or {}).get("reason") or "").strip() or None
        note = ((ln or {}).get("note") or "").strip() or None
        if abs(q - float(conf or 0)) > 1e-9:
            if not rs:
                raise ValueError(f"{c}: ส่งไม่ตรงกับที่สาขายืนยัน ต้องเลือกเหตุผล")
            if rs not in SS_REASON_TH:
                raise ValueError(f"{c}: เหตุผลไม่อยู่ในรายการ")
            if rs == "other" and not note:
                raise ValueError(f"{c}: เหตุผล “อื่น ๆ” ต้องเขียนอธิบาย")
        else:
            rs = note = None
        con.execute("UPDATE bc_order_line SET processed_units=?, ss_reason=?, ss_note=? WHERE order_id=?"
                    " AND ingredient_code=?", (q, rs, note, order_id, c))
    con.execute("UPDATE bc_order SET status='processed', processed_by=?, processed_at=?, po_ref=? WHERE order_id=?",
                (who, now(), (po_ref or "").strip() or None, order_id))
    audit(con, who, "order_process", "bc_order", str(order_id), o[0], {"po_ref": po_ref})
    return {"order_id": order_id, "status": "processed", "value": _value(con, order_id, "processed_units", o[1][:7])}


def consolidated(con, order_ids: list) -> dict:
    """Per supplier × delivery day: each ingredient's total and its branches (processed, else confirmed)."""
    if not order_ids:
        return {"groups": [], "branches": []}
    ph = ",".join("?" * len(order_ids))
    ing = {c: (n, s, recv, base, per) for c, n, s, recv, base, per in con.execute(
        "SELECT code, name_th, supplier, recv_unit, base_unit, qty_per_recv FROM bc_ingredient")}
    groups, branches = {}, set()
    for oid, bid, bname, cyc, rnd, c, conf, proc, unit in con.execute(
            "SELECT o.order_id, o.branch_id, b.name, o.cycle_date, o.round, l.ingredient_code, l.confirmed_units,"
            f" l.processed_units, l.order_unit FROM bc_order o JOIN branches b USING(branch_id) JOIN bc_order_line l"
            f" USING(order_id) WHERE o.order_id IN ({ph})", order_ids).fetchall():
        q = proc if proc is not None else conf
        if not q:
            continue
        n, sup, recv, base, per = ing.get(c, (c, None, None, None, None))
        dlv = delivery_of(con, bid, rnd, cyc)
        key = (sup or "ไม่ระบุซัพพลายเออร์", dlv)
        g = groups.setdefault(key, {"supplier": key[0], "delivery": dlv, "items": {}})
        it = g["items"].setdefault(c, {"code": c, "name": n or c, "unit": recv if unit != "base" else base,
                                       "total": 0.0, "by_branch": {}})
        it["total"] += q
        it["by_branch"][bname] = it["by_branch"].get(bname, 0.0) + q
        branches.add(bname)
    out = []
    for (sup, dlv), g in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        g["items"] = sorted(g["items"].values(), key=lambda i: i["name"])
        out.append(g)
    return {"groups": out, "branches": sorted(branches)}


# ── Ops: compliance ──────────────────────────────────────────────────────────
def _phase2_branches(con) -> list:
    return [r[0] for r in con.execute(
        "SELECT branch_id FROM bc_stock_count UNION SELECT branch_id FROM bc_order ORDER BY 1")]


def compliance(con, date_from: str, date_to: str, branch_id: int = None) -> dict:
    """Every cycle the calendar expects between the dates, for every branch already on Phase 2."""
    from .forecast import calendar, _wd
    d0, d1 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    today = date.today()
    names = dict(con.execute("SELECT branch_id, name FROM branches"))
    rows = []
    for bid in ([branch_id] if branch_id else _phase2_branches(con)):
        cal = calendar(con, bid)
        for rnd in ("1", "2", "salmon"):
            wd = cal[rnd]["order_weekday"]
            d = d0
            while d <= d1:
                if _wd(d) == wd:
                    rows.append(_cycle(con, bid, names.get(bid), rnd, d.isoformat(), today))
                d += timedelta(days=1)
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    rows.sort(key=lambda r: (r["cycle_date"], r["branch"] or "", r["round"]))
    return {"from": date_from, "to": date_to, "rows": rows, "counts": counts, "tolerance_pct": TOL_PCT}


def _cycle(con, bid, bname, rnd, cyc, today) -> dict:
    r = con.execute("SELECT order_id, status, submitted_at, processed_at FROM bc_order WHERE branch_id=? AND round=?"
                    " AND cycle_date=? AND status<>'cancelled' ORDER BY order_id DESC LIMIT 1", (bid, rnd, cyc)).fetchone()
    row = {"branch_id": bid, "branch": bname, "round": rnd, "cycle_date": cyc, "order_id": None,
           "suggested": None, "confirmed": None, "processed": None, "received": None}
    if not r:
        row["status"] = "not_ordered" if cyc < today.isoformat() else "upcoming"
        return row
    oid, st, sub, proc = r
    m = cyc[:7]
    row.update(order_id=oid, order_status=st, suggested=_value(con, oid, "suggested_units", m),
               confirmed=_value(con, oid, "confirmed_units", m))
    if st in ("processed", "received"):
        row["processed"] = _value(con, oid, "processed_units", m)
    if st == "received":
        row["received"] = _value(con, oid, "received_units", m)
    row.update(sla(sub, proc, cyc))
    if st in ("suggested", "confirmed"):
        row["status"] = "not_submitted"
    else:
        s, c = row["suggested"] or 0.0, row["confirmed"] or 0.0
        pct = (100.0 * (c - s) / s) if s else (100.0 if c else 0.0)
        row["confirmed_vs_suggested_pct"] = round(pct, 1)
        row["status"] = "ok" if abs(pct) <= TOL_PCT else ("over" if pct > 0 else "under")
    if row["processed"] is not None and row["confirmed"]:
        row["centre_change_pct"] = round(100.0 * (row["processed"] - row["confirmed"]) / row["confirmed"], 1)
    if row["received"] is not None and row["processed"]:
        row["delivery_gap_pct"] = round(100.0 * (row["received"] - row["processed"]) / row["processed"], 1)
    return row


# ── the monthly variance summary ─────────────────────────────────────────────
def _month_bounds(month: str):
    y, m = int(month[:4]), int(month[5:7])
    first = date(y, m, 1)
    nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return first, nxt


def _prev(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y - (m == 1):04d}-{12 if m == 1 else m - 1:02d}"


def monthly(con, month: str, with_previous: bool = True) -> dict:
    """Usage variance per branch for the periods that END in `month`."""
    from .stock import periods as _periods
    from .variance import REASON_TH, period
    first, nxt = _month_bounds(month)
    rows, reasons = [], {}
    names = dict(con.execute("SELECT branch_id, name FROM branches"))
    for bid in _phase2_branches(con):
        agg = {"branch_id": bid, "branch": names.get(bid), "periods": 0, "days": 0, "bom_value": 0.0,
               "gap_value": 0.0, "over_value": 0.0, "by_class": {"A": 0.0, "B": 0.0, "C": 0.0},
               "flagged": 0, "needs_reason": 0, "complete": 0}
        for b, e in _periods(con, bid):
            if not (first.isoformat() <= e["date"] < nxt.isoformat()):
                continue
            p = period(con, bid, e["count_id"])
            agg["periods"] += 1
            agg["days"] += p["days"]
            agg["bom_value"] += p["bom_value"]
            agg["gap_value"] += p["gap_value"]
            agg["over_value"] += p["over_value"]
            agg["flagged"] += p["flagged"]
            agg["needs_reason"] += p["needs_reason"]
            agg["complete"] += 1 if p["complete"] else 0
            for r in p["rows"]:
                if r["gap_value"] is not None and r["status"] != "not_counted":
                    agg["by_class"][r["class"]] += r["gap_value"]
                if r.get("reason"):
                    k = r["reason"]
                    x = reasons.setdefault(k, {"reason": k, "label": REASON_TH.get(k, k), "items": 0, "value": 0.0})
                    x["items"] += 1
                    x["value"] += r["gap_value"] or 0.0
        if not agg["periods"]:
            continue
        agg["gap_pct"] = round(100.0 * agg["gap_value"] / agg["bom_value"], 1) if agg["bom_value"] else None
        for k in ("bom_value", "gap_value", "over_value"):
            agg[k] = round(agg[k], 2)
        agg["by_class"] = {k: round(v, 2) for k, v in agg["by_class"].items()}
        rows.append(agg)
    rows.sort(key=lambda r: -(r["gap_value"] or 0))
    out = {"month": month, "branches": rows,
           "total": {"bom_value": round(sum(r["bom_value"] for r in rows), 2),
                     "gap_value": round(sum(r["gap_value"] for r in rows), 2),
                     "over_value": round(sum(r["over_value"] for r in rows), 2)},
           "top_reasons": sorted(({**v, "value": round(v["value"], 2)} for v in reasons.values()),
                                 key=lambda v: -abs(v["value"]))[:5]}
    t = out["total"]
    t["gap_pct"] = round(100.0 * t["gap_value"] / t["bom_value"], 1) if t["bom_value"] else None
    if with_previous:
        prev = monthly(con, _prev(month), with_previous=False)
        pb = {r["branch_id"]: r for r in prev["branches"]}
        for r in rows:
            p = pb.get(r["branch_id"])
            r["prev_gap_pct"] = p["gap_pct"] if p else None
            r["prev_gap_value"] = p["gap_value"] if p else None
        out["previous"] = {"month": prev["month"], "total": prev["total"]}
    return out
