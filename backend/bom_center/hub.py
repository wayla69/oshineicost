# -*- coding: utf-8 -*-
"""Phase 2 · Sprint S9 — the centre's side of branch ordering (docs/BOM_CENTER_PLAN.md §7.1-8/9, §7.5).

  * Sales Support's QUEUE: every submitted order, with its delivery day; Sales Support
    sets what will actually be sent (a change needs a reason — out of stock, substitute,
    pack size …), marks it processed with a PO reference, and works from a consolidated
    view per delivery day (Excel; one supplier, central.CENTRAL_SUPPLIER). The cutoff: the branch submits before 13:15
    two days before the delivery day (`cycle_date` is that cutoff date); a later order is
    accepted but flagged `late` (late fee at the outside warehouse). The warehouse then
    processes it by 14:00 of the cutoff day (Bangkok time).
  * Ops' COMPLIANCE: for every cycle the order calendar expects, four numbers in ฿ —
    suggested · confirmed by the branch · processed by Sales Support · received — so a
    gap can be placed at the branch, at the centre, or in the delivery.
  * The MONTHLY usage-variance summary per branch (periods ending in the month), by
    class and reason, against the month before — also a section of the Management Report.
"""
from datetime import date, datetime, timedelta, timezone

from .clock import today as bkk_today
from .fmt import item as _item
from .master import audit

SS_REASONS = (("short", "ผู้จำหน่ายสินค้าขาด / ส่งได้ไม่ครบ"), ("substitute", "เปลี่ยนเป็นสินค้าทดแทน"),
              ("pack", "ปรับตามขนาดแพ็ค / ขั้นต่ำ"), ("merge", "รวมกับรอบอื่น"), ("stock", "คลังส่งจากสต็อกแทน"),
              ("other", "อื่น ๆ"))
SS_REASON_TH = dict(SS_REASONS)
BKK = timezone(timedelta(hours=7))
SLA_PROCESS_BY = 14                            # hour of the cutoff day, Bangkok (the branch cutoff itself: forecast.CUTOFF_TIME)
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


VALUE_ORDERED_TH = "มูลค่าที่สั่ง"        # what the branch confirmed (confirmed_units)
VALUE_SENT_TH = "มูลค่าที่จัดส่ง"          # what the warehouse sent (processed_units)


def _pricer(con):
    """(per, price): the pack sizes, and a memoised `price(code, month)` = the baht per base unit the branch
    pays (the centre's price when the item is on its list, else the month's ingredient price)."""
    from .central import central_map, unit_price
    per = _per(con)
    cmap = central_map(con)
    memo = {}

    def price(code, month):
        k = (code, month)
        if k not in memo:
            memo[k] = unit_price(con, code, month, cmap)[0]
        return memo[k]
    return per, price


def _value(con, order_id: int, field: str, month: str) -> float:
    """The baht value of ONE quantity column of an order (suggested_units, confirmed_units, processed_units,
    received_units), priced for `month`. Every order value shown anywhere comes through here."""
    per, price = _pricer(con)
    v = 0.0
    for c, q, u in con.execute(f"SELECT ingredient_code, {field}, order_unit FROM bc_order_line WHERE order_id=?",
                               (order_id,)):
        p = price(c, month)
        if p and q:
            v += _base(q, u, per.get(c)) * p
    return round(v, 2)


def with_values(con, rows: list) -> list:
    """The ONE order value of the queue, the history, the order page and the warehouse's confirmation (PR-M2).

    The column `bc_order.value` is a snapshot written when the branch last saved: it goes stale when the
    warehouse sends other quantities, or when the centre's list or the month's prices are imported after the
    order was saved (an order showed ฿114,778 in the queue and ฿46,350 once the warehouse had sent it). So
    nothing reads that column any more: every row gets its values priced now, from the lines, by `_value`.
      value_ordered  confirmed_units   ("มูลค่าที่สั่ง")
      value_sent     processed_units   ("มูลค่าที่จัดส่ง") — only once the warehouse has sent the order
      value          the one to show: the sent value when there is one, else the ordered value
      value_label    the words that go with `value`
    A row needs order_id, status and cycle_date."""
    per, price = _pricer(con)
    ids = [r["order_id"] for r in rows if r.get("order_id") is not None]
    lines = {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for oid, c, conf, proc, u in con.execute(
                "SELECT order_id, ingredient_code, confirmed_units, processed_units, order_unit FROM bc_order_line"
                f" WHERE order_id IN ({','.join('?' * len(part))})", part):
            lines.setdefault(oid, []).append((c, conf, proc, u))
    for r in rows:
        month = str(r.get("cycle_date") or "")[:7]
        ordered = sent = 0.0
        for c, conf, proc, u in lines.get(r.get("order_id"), []):
            p = price(c, month)
            if not p:
                continue
            if conf:
                ordered += _base(conf, u, per.get(c)) * p
            if proc:
                sent += _base(proc, u, per.get(c)) * p
        r["value_ordered"] = round(ordered, 2)
        r["value_sent"] = round(sent, 2) if r.get("status") in ("processed", "received") else None
        r["value"] = r["value_sent"] if r["value_sent"] is not None else r["value_ordered"]
        r["value_label"] = VALUE_SENT_TH if r["value_sent"] is not None else VALUE_ORDERED_TH
    return rows


def delivery_of(con, branch_id: int, rnd: str, cycle_date: str, delivery_date: str = None) -> str:
    """The delivery day of an order: the stored one, else the first delivery of the round after its cutoff date."""
    if delivery_date:
        return delivery_date
    from .forecast import _next_on_or_after, calendar
    try:
        dw = calendar(con, branch_id)[rnd]["delivery_weekday"]
        return _next_on_or_after(date.fromisoformat(cycle_date) + timedelta(days=1), int(dw)).isoformat()
    except Exception:
        return cycle_date


def sla(submitted_at: str, processed_at: str, cycle_date: str, cutoff_at: str = None) -> dict:
    """On time = submitted by the cutoff (`cutoff_at`; without it 13:15 of `cycle_date`, the cutoff date).
    The warehouse then has until 14:00 of the cutoff day."""
    from .forecast import cutoff_time
    s, p = _bkk(submitted_at), _bkk(processed_at)
    cut = datetime.fromisoformat(cutoff_at) if cutoff_at else \
        datetime.combine(date.fromisoformat(cycle_date), cutoff_time(), tzinfo=BKK)
    on_time_submit = bool(s and s <= cut)
    out = {"submitted_on_time": on_time_submit, "late": bool(s and not on_time_submit),
           "processed_on_time": None, "hours_to_process": None}
    if s and p:
        out["hours_to_process"] = round((p - s).total_seconds() / 3600.0, 1)
        if on_time_submit:
            due = datetime.fromisoformat(cycle_date).replace(hour=SLA_PROCESS_BY, tzinfo=BKK)
            out["processed_on_time"] = p <= due
    return out


# ── Sales Support's queue ────────────────────────────────────────────────────
def queue(con, status: str = "submitted", date_from: str = None, date_to: str = None, keyed: int = None) -> list:
    sts = ("submitted", "processed", "received") if status == "all" else (status,)
    q = ("SELECT o.order_id, o.branch_id, b.name, o.cycle_date, o.round, o.status, o.value, o.submitted_at,"
         " o.processed_at, o.processed_by, o.po_ref, o.stock_count_id, o.buffer_pct, o.buffer_reason,"
         " (SELECT COUNT(*) FROM bc_order_line l WHERE l.order_id=o.order_id AND COALESCE(l.confirmed_units,0)>0),"
         " (SELECT COUNT(*) FROM bc_order_line l WHERE l.order_id=o.order_id AND l.reason_code IS NOT NULL),"
         " COALESCE(o.late,0), o.cutoff_at, o.delivery_date,"
         " (SELECT COUNT(*) FROM bc_claim c WHERE c.order_id=o.order_id AND c.status<>'cancelled'),"
         " (SELECT COUNT(*) FROM bc_claim c WHERE c.order_id=o.order_id AND c.status='submitted'),"
         " o.accounting_keyed_at, o.accounting_keyed_by"
         f" FROM bc_order o JOIN branches b USING(branch_id) WHERE o.status IN ({','.join('?' * len(sts))})")
    args = list(sts)
    if date_from:
        q += " AND o.cycle_date>=?"
        args.append(date_from)
    if date_to:
        q += " AND o.cycle_date<=?"
        args.append(date_to)
    if keyed is not None:
        q += " AND (o.accounting_keyed_at IS NOT NULL)=?"
        args.append(1 if keyed else 0)
    q += " ORDER BY o.cycle_date, b.name, o.round"
    out = []
    for r in con.execute(q, args).fetchall():
        d = dict(zip(("order_id", "branch_id", "branch", "cycle_date", "round", "status", "value", "submitted_at",
                      "processed_at", "processed_by", "po_ref", "stock_count_id", "buffer_pct", "buffer_reason",
                      "lines", "branch_changes", "late", "cutoff_at", "delivery_date", "claims", "claims_pending",
                      "accounting_keyed_at", "accounting_keyed_by"), r))
        d["accounting_keyed"] = d["accounting_keyed_at"] is not None
        d["accounting_keyed_date"] = keyed_date(d["accounting_keyed_at"])
        from .orders import BUFFER_REASON_TH
        d["buffer_reason_th"] = BUFFER_REASON_TH.get(d["buffer_reason"]) if d["buffer_reason"] else None
        d["delivery"] = delivery_of(con, d["branch_id"], d["round"], d["cycle_date"], d.pop("delivery_date"))
        late = bool(d["late"])
        d.update(sla(d["submitted_at"], d["processed_at"], d["cycle_date"], d["cutoff_at"]))
        d["late"] = late                                  # the flag recorded when the order was submitted
        d["stock_deducted"] = d.pop("stock_count_id") is not None
        out.append(d)
    return with_values(con, out)             # the stored snapshot (o.value) is replaced by the one live value


def keyed_date(ts: str):
    """The Bangkok date (YYYY-MM-DD) a keyed mark was set, or None."""
    d = _bkk(ts)
    return d.date().isoformat() if d else None


def set_keyed(con, order_ids: list, keyed: bool, who: str) -> dict:
    """Sales Support marks (or unmarks) orders it has keyed into the external accounting system.
    Only an order the warehouse has processed (or the branch has received) can be keyed."""
    ids = sorted({int(i) for i in (order_ids or [])})
    if not ids:
        raise ValueError("เลือกใบสั่งซื้ออย่างน้อย 1 ใบ")
    rows = {oid: st for oid, st in con.execute(
        f"SELECT order_id, status FROM bc_order WHERE order_id IN ({','.join('?' * len(ids))})", ids)}
    missing = [i for i in ids if i not in rows]
    if missing:
        raise ValueError("ไม่พบใบสั่งซื้อ " + ", ".join(f"#{i}" for i in missing))
    if keyed:
        bad = [i for i in ids if rows[i] not in ("processed", "received")]
        if bad:
            raise ValueError("ใบสั่งซื้อ " + ", ".join(f"#{i}" for i in bad)
                             + " คลังยังไม่ยืนยันยอดส่ง จึงคีย์เข้าระบบบัญชีไม่ได้")
    at = now() if keyed else None
    by = who if keyed else None
    action = "order_accounting_keyed" if keyed else "order_accounting_unkeyed"
    for i in ids:
        con.execute("UPDATE bc_order SET accounting_keyed_at=?, accounting_keyed_by=? WHERE order_id=?", (at, by, i))
        audit(con, who, action, "bc_order", str(i), rows[i], None)
    return {"updated": len(ids), "keyed": bool(keyed), "at": at}


def process(con, order_id: int, lines: list, who: str, po_ref: str = None) -> dict:
    """Sales Support sets what will be sent. Default = what the branch confirmed."""
    o = con.execute("SELECT status, cycle_date FROM bc_order WHERE order_id=?", (order_id,)).fetchone()
    if not o:
        raise ValueError("ไม่พบใบสั่งซื้อ")
    if o[0] not in ("submitted", "processed"):
        raise ValueError("ยืนยันยอดส่งได้เฉพาะใบสั่งซื้อที่สาขาส่งแล้ว")
    given = {(ln.get("code") or "").strip(): ln for ln in lines or []}
    for c, conf in con.execute("SELECT ingredient_code, confirmed_units FROM bc_order_line WHERE order_id=?",
                               (order_id,)).fetchall():
        ln = given.get(c)
        q = conf if not ln or ln.get("processed_units") is None else ln.get("processed_units")
        try:
            q = float(q or 0)
        except (TypeError, ValueError):
            raise ValueError(f"{_item(con, c)}: จำนวนต้องเป็นตัวเลข")
        if q < 0:
            raise ValueError(f"{_item(con, c)}: จำนวนต้องไม่ติดลบ")
        rs = ((ln or {}).get("reason") or "").strip() or None
        note = ((ln or {}).get("note") or "").strip() or None
        if abs(q - float(conf or 0)) > 1e-9:
            if not rs:
                raise ValueError(f"{_item(con, c)}: จำนวนส่งไม่ตรงกับที่สาขายืนยัน · เลือกเหตุผล")
            if rs not in SS_REASON_TH:
                raise ValueError(f"{_item(con, c)}: เหตุผลไม่อยู่ในรายการ")
            if rs == "other" and not note:
                raise ValueError(f"{_item(con, c)}: เหตุผล “อื่น ๆ” ต้องระบุคำอธิบาย")
        else:
            rs = note = None
        con.execute("UPDATE bc_order_line SET processed_units=?, ss_reason=?, ss_note=? WHERE order_id=?"
                    " AND ingredient_code=?", (q, rs, note, order_id, c))
    con.execute("UPDATE bc_order SET status='processed', processed_by=?, processed_at=?, po_ref=? WHERE order_id=?",
                (who, now(), (po_ref or "").strip() or None, order_id))
    audit(con, who, "order_process", "bc_order", str(order_id), o[0], {"po_ref": po_ref})
    v = with_values(con, [{"order_id": order_id, "status": "processed", "cycle_date": o[1]}])[0]
    return {"order_id": order_id, "status": "processed", "value": v["value_sent"], "value_label": v["value_label"],
            "value_ordered": v["value_ordered"], "value_sent": v["value_sent"]}


def consolidated(con, order_ids: list) -> dict:
    """Per delivery day: each ingredient's total and its branches (processed, else confirmed). Every central
    item comes from one company (central.CENTRAL_SUPPLIER), so there is one group per delivery day."""
    from .central import CENTRAL_SUPPLIER
    if not order_ids:
        return {"supplier": CENTRAL_SUPPLIER, "groups": [], "branches": []}
    ph = ",".join("?" * len(order_ids))
    ing = {c: (n, recv, base, per) for c, n, recv, base, per in con.execute(
        "SELECT code, name_th, recv_unit, base_unit, qty_per_recv FROM bc_ingredient")}
    groups, branches = {}, set()
    for oid, bid, bname, cyc, rnd, c, conf, proc, unit, ddate in con.execute(
            "SELECT o.order_id, o.branch_id, b.name, o.cycle_date, o.round, l.ingredient_code, l.confirmed_units,"
            f" l.processed_units, l.order_unit, o.delivery_date FROM bc_order o JOIN branches b USING(branch_id)"
            f" JOIN bc_order_line l USING(order_id) WHERE o.order_id IN ({ph})", order_ids).fetchall():
        q = proc if proc is not None else conf
        if not q:
            continue
        n, recv, base, per = ing.get(c, (c, None, None, None))
        dlv = delivery_of(con, bid, rnd, cyc, ddate)
        g = groups.setdefault(dlv, {"delivery": dlv, "items": {}})
        it = g["items"].setdefault(c, {"code": c, "name": n or c, "unit": recv if unit != "base" else base,
                                       "total": 0.0, "by_branch": {}})
        it["total"] += q
        it["by_branch"][bname] = it["by_branch"].get(bname, 0.0) + q
        branches.add(bname)
    out = []
    for dlv, g in sorted(groups.items()):
        g["items"] = sorted(g["items"].values(), key=lambda i: i["name"])
        out.append(g)
    return {"supplier": CENTRAL_SUPPLIER, "groups": out, "branches": sorted(branches)}


# ── Ops: compliance ──────────────────────────────────────────────────────────
def _phase2_branches(con) -> list:
    return [r[0] for r in con.execute(
        "SELECT branch_id FROM bc_stock_count UNION SELECT branch_id FROM bc_order ORDER BY 1")]


def compliance(con, date_from: str, date_to: str, branch_id: int = None) -> dict:
    """Every cycle the calendar expects between the dates, for every branch already on Phase 2."""
    from .forecast import calendar, _wd
    d0, d1 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    today = bkk_today()
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
    from .issue_reports import log as _slip_log          # the daily issue slips beside the orders (PR-J4)
    issues = _slip_log(con, d0, d1, branch_id, today)["summary"]
    return {"from": date_from, "to": date_to, "rows": rows, "counts": counts, "tolerance_pct": TOL_PCT, "issues": issues}


def _cycle(con, bid, bname, rnd, cyc, today) -> dict:
    r = con.execute("SELECT order_id, status, submitted_at, processed_at, cutoff_at, COALESCE(late,0), delivery_date"
                    " FROM bc_order WHERE branch_id=? AND round=?"
                    " AND cycle_date=? AND status<>'cancelled' ORDER BY order_id DESC LIMIT 1", (bid, rnd, cyc)).fetchone()
    row = {"branch_id": bid, "branch": bname, "round": rnd, "cycle_date": cyc, "order_id": None,
           "suggested": None, "confirmed": None, "processed": None, "received": None, "late": False,
           "delivery": delivery_of(con, bid, rnd, cyc)}
    if not r:
        row["status"] = "not_ordered" if cyc < today.isoformat() else "upcoming"
        return row
    oid, st, sub, proc, cutoff_at, late, ddate = r
    row["delivery"] = delivery_of(con, bid, rnd, cyc, ddate)
    m = cyc[:7]
    row.update(order_id=oid, order_status=st, suggested=_value(con, oid, "suggested_units", m),
               confirmed=_value(con, oid, "confirmed_units", m))
    if st in ("processed", "received"):
        row["processed"] = _value(con, oid, "processed_units", m)
    if st == "received":
        row["received"] = _value(con, oid, "received_units", m)
    row.update(sla(sub, proc, cyc, cutoff_at))
    row["late"] = bool(late)
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
