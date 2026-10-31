# -*- coding: utf-8 -*-
"""บิลยกเลิก / พิมพ์ซ้ำ drill-down (owner 2026-10-31: "drill down ... ดูรายละเอียดทุกสาขาได้เลย รายสาขารายบิลเลย").

The ตรวจวันนี้ card (routers/mgt_watch.voids) names the three worst branches of one window. This answers the levels under it,
on the SAME counts (mgt_watch.void_counts / reprint_rows / void_bounds) and the same windows, so a figure never differs
between the card and the list (one answer per question):

  GET /api/reports/voids                    level 1 - every branch of the scope (company + franchise), the rate, the cancelled
                                            bills and baht, the reprints; branches with no void at the bottom
  GET /api/reports/voids?branch=X           level 2 - one branch: header (+ its 8-week normal rate), the reasons, every cancelled
                                            bill (pos_cancel_bill_detail) and every discounted bill printed more than once
  GET /api/reports/voids/bill?branch=X&receipt=R   level 3 - one reprinted bill: its lines (sale_bill_item, when the sync kept
                                            them) and its payments (sale_payment).
Every voided bill carries its class (rules.classify_voids: re-issued = a new bill at the branch within 30 min that matches it);
the level, the sort and the alert are on the voids NOT re-issued, the totals of every void stay beside them. A cancelled bill has no lines anywhere: the POS
                                            drops it from the sales (it is not in sales_transaction / sale_bill_item), so the phone
                                            shows the fields of level 2 for it.

Windows: window = day (เมื่อวาน, the last finished day) | week (7 วัน) | half (15 วัน) | month (the report's month, month-to-date).
Anchors as the card: `month` (YYYY-MM, else the latest month) gives cs..ce through the management report's own window
(reports._mgt_window, monthly, mtd); `date` pins the last finished day (level 1 returns it as `anchor`, so level 2 opened from it
reads the same window), else rules.latest_settled_day on the scope.
Who reads it: MODULE_RULES /api/reports/voids = report.management (the stricter of the management report's and ส่วนลด's rules);
a login pinned to a branch is refused another branch here as well.
"""
import datetime as _dt
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from core import get_db, ttl_cache
from rules import VOID_REISSUE_MIN, scope_filter
from routers import mgt_watch as W

router = APIRouter()

WINDOWS = ("day", "week", "half", "month")
BILL_CAP = 500                  # level 2 lists at most this many cancelled bills / reprints (the rest are counted in `more`)
NORMAL_WEEKS = 8                # the branch's normal rate = the 8 weeks before the window


def _where(branch, group):
    base = ["st.source='Easy'", "b.name!='ALL'"]
    params: dict = {}
    scope_filter(base, params, branch, group)
    return " AND ".join(base), params


def _anchors(con, month, date, where, params, th):
    """(cs, ce, day): the report's month window and the last finished day (<= ce), as the card computes them."""
    import calendar as _cal
    from routers.reports import _mgt_window
    MON = W._MON_TH if th else W._MON_EN
    w = _mgt_window(con, DOW=[], MON=MON, _cal=_cal, _dt=_dt, date=None, dlabel=lambda d: "", month=month, mtd=True,
                    params=params, period="monthly", th=th, week=None, where=where)
    if w.get("available") is False or not w.get("cs"):
        return None, None, None
    cs, ce = w["cs"], w["ce"]
    day = None
    if date:
        try:
            day = _dt.date.fromisoformat(date[:10])
        except ValueError:
            raise HTTPException(400, detail="date ต้องเป็น YYYY-MM-DD")
    else:
        try:
            from rules import latest_settled_day
            d = latest_settled_day(con, where=where.replace("st.", "t."), params=params)["day"]
            day = _dt.date.fromisoformat(d[:10]) if d else None
        except Exception:                           # pragma: no cover - the month still answers
            day = None
    if day:
        day = min(day, ce)
    return cs, ce, day


def _types(con, ids):
    ph = ",".join("?" * len(ids))
    return {r[0]: (r[1] or "master", r[2]) for r in con.execute(
        f"SELECT branch_id, COALESCE(branch_type,'master'), COALESCE(active,1) FROM branches WHERE branch_id IN ({ph})", list(ids))}


def _row(name, btype, bills, cnt, rep, min_bills):
    """One branch of a window. cnt = (all voids, baht, NOT re-issued, their baht) of mgt_watch.void_counts. The level and the
    sort are on the voids not re-issued (owner 2026-10-31: a void followed by a new bill is a correction); voids / amount /
    rate_pct stay every void, as the card's bills / amount / rate_pct."""
    cb, amt, ob, oamt = cnt
    tot = bills + cb
    r = {"branch": name, "type": btype, "bills_total": tot, "voids": cb, "amount": round(amt), "rate_pct": None,
         "voids_open": ob, "amount_open": round(oamt), "rate_open_pct": None, "voids_reissued": cb - ob, "level": "none",
         "reprints": rep.get("bills", 0), "reprint_baht": rep.get("baht", 0)}
    if tot > 0:
        r["rate_pct"], r["rate_open_pct"] = round(cb / tot * 100, 2), round(ob / tot * 100, 2)
        if min_bills and tot < min_bills:
            r["few"] = True                         # the card gives no verdict on a handful of bills
        else:
            r["level"] = W.void_level(ob / tot * 100)
    return r


def _level1(con, ids, s, e, where, params, cover, window, cls=None):
    source, canc, bills = W.void_counts(con, ids, s, e, cover, month_ok=(window == "month"), cls=cls)
    rep = {r["branch"]: r for r in W.reprint_rows(con, s, e, where, params)}
    types = _types(con, ids)
    min_bills = W.T_VOID_MIN_BILLS if window == "day" else 0
    rows = []
    for bid, name in ids.items():
        cnt = canc.get(bid, (0, 0, 0, 0))
        b = bills.get(bid, 0)
        btype, active = types.get(bid, ("master", 1))
        if not active and not b and not cnt[0]:     # a closed branch with nothing in the window is not a row
            continue
        rows.append(_row(name, btype, b, cnt, rep.get(name, {}), min_bills))
    rows.sort(key=lambda r: (r["voids"] == 0, -(r["rate_open_pct"] or 0), -(r["rate_pct"] or 0), -r["voids"], r["branch"]))
    tv, to, tb = sum(r["voids"] for r in rows), sum(r["voids_open"] for r in rows), sum(r["bills_total"] for r in rows)
    total = {"voids": tv, "amount": sum(r["amount"] for r in rows), "bills_total": tb,
             "rate_pct": round(tv / tb * 100, 2) if tb else None,
             "voids_open": to, "amount_open": sum(r["amount_open"] for r in rows), "voids_reissued": tv - to,
             "rate_open_pct": round(to / tb * 100, 2) if tb else None,
             "reprints": sum(r["reprints"] for r in rows), "reprint_baht": sum(r["reprint_baht"] for r in rows),
             "branches": len(rows), "flagged": sum(1 for r in rows if r["level"] in ("fix", "watch"))}
    return source, rows, total


def _reason(rs, rid):
    """The reason's name; 0 / NULL = the cashier picked none (most of the POS's cancellations of an empty bill)."""
    if not rid:
        return "ไม่ระบุเหตุผล"
    return rs.get(rid) or f"รหัส {rid}"


def _reasons(con):
    try:
        return {r[0]: r[1] for r in con.execute("SELECT reason_id, reason_desc FROM pos_cancel_reason")}
    except Exception:
        return {}


def bill_lists(con, bid, s, e, where, params, cls=None, cap=BILL_CAP):
    """One branch's bills of s..e: (voids, reasons, voids beyond the cap, reprints, reprints beyond the cap) - every voided bill
    (newest first, with its class: reissued_as = the receipt of the new bill, or None = not re-issued) and every discounted bill
    printed more than once. The drill-down's level 2 and the report's §7.1 both read this. cap=None = every bill (the .xlsx)."""
    from rules import classify_voids, receipt_label
    rs = _reasons(con)
    bills, reasons, more = [], [], 0
    try:
        q = con.execute("""SELECT rowid, sale_date, receipt_no, reason_id, amount, cancel_user, discount FROM pos_cancel_bill_detail
                           WHERE branch_id=? AND date(sale_date) BETWEEN ? AND ? ORDER BY sale_date DESC""",
                        (bid, s.isoformat(), e.isoformat())).fetchall()
    except Exception:                               # no detail table
        q = []
    if q:
        if cls is None:
            cls = classify_voids(con, [bid], s, e)
        n = len(q) if cap is None else cap
        more = max(0, len(q) - n)
        bills = [{"at": r[1], "receipt": r[2] or "", "amount": round(r[4] or 0), "reason": _reason(rs, r[3]),
                  "user": r[5] or "", "discount": round(r[6] or 0), "reissued_as": cls.get(r[0]),
                  "reissued_no": receipt_label(cls.get(r[0])) if cls.get(r[0]) else None} for r in q[:n]]
        acc: dict = {}
        for r in q:
            a = acc.setdefault(_reason(rs, r[3]), [0, 0, 0])
            a[0] += 1; a[1] += r[4] or 0; a[2] += 0 if cls.get(r[0]) else 1
        reasons = [{"reason": k, "n": v[0], "amount": round(v[1]), "n_open": v[2]}
                   for k, v in sorted(acc.items(), key=lambda kv: (-kv[1][0], kv[0]))]
    rq = con.execute(f"""SELECT st.sold_at, st.receipt_no, st.total_inc_vat, st.discount, st.print_times, st.cashier
                         FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
                         WHERE {where} AND st.branch_id=:bid AND date(st.sold_at) BETWEEN :s AND :e
                           AND st.discount>0 AND COALESCE(st.print_times,0)>1
                         ORDER BY st.sold_at DESC""", {**params, "bid": bid, "s": s.isoformat(), "e": e.isoformat()}).fetchall()
    rn = len(rq) if cap is None else cap
    reprints = [{"at": r[0], "receipt": r[1], "no": receipt_label(r[1]), "total": round(r[2] or 0), "discount": round(r[3] or 0), "prints": r[4] or 0,
                 "cashier": r[5] or ""} for r in rq[:rn]]
    return bills, reasons, more, reprints, max(0, len(rq) - rn)


def _level2(con, bid, name, s, e, where, params, cover, window):
    from rules import classify_voids
    cls = classify_voids(con, [bid], s, e)
    source, rows, total = _level1(con, {bid: name}, s, e, where, params, cover, window, cls=cls)
    head = rows[0] if rows else _row(name, _types(con, {bid: name}).get(bid, ("master", 1))[0], 0, (0, 0, 0, 0), {}, 0)
    bills, reasons, more, reprints, rmore = bill_lists(con, bid, s, e, where, params, cls=cls)
    if source != "detail":
        bills, reasons, more = [], [], 0
    # the branch's normal: the same rate (voids not re-issued) over the 8 weeks before the window, while the detail table holds them
    normal = None
    ns, ne = s - _dt.timedelta(days=7 * NORMAL_WEEKS), s - _dt.timedelta(days=1)
    nsrc, ncanc, nbills = W.void_counts(con, {bid: name}, ns, ne, cover, month_ok=False)
    if nsrc == "detail":
        cb, _, ob, _ = ncanc.get(bid, (0, 0, 0, 0)); tot = nbills.get(bid, 0) + cb
        if tot:
            normal = {"rate_open_pct": round(ob / tot * 100, 2), "rate_pct": round(cb / tot * 100, 2),
                      "range": [ns.isoformat(), ne.isoformat()], "voids": cb, "voids_open": ob, "bills_total": tot}
    return source, {"header": {**head, "normal": normal}, "reasons": reasons, "bills": bills, "bills_more": more,
                    "reprints": reprints, "reprints_more": rmore}


REPORT_CAP = 20                 # the management report's §7.1 prints 20 bills per list per branch ("… อีก k บิล"); the .xlsx all


def report_section(con, cs, ce, where, params, branch=None, group=None, whole_month=False, cap=REPORT_CAP) -> dict:
    """§7.1 of the management report (owner 2026-10-31, "แยก grouping บิล voided หรือ พิมพ์ซ้ำ"): the drill-down's level 1 over the
    report's own cs..ce, then, for every branch with at least one void or reprint (worst rate of voids NOT re-issued first), its
    level-2 lists (bill_lists, `cap` bills a list; None = every bill, for the .xlsx). The branches with neither are one total.
    whole_month: the period is a calendar month, so a window older than the per-bill table may fall back to the monthly count."""
    from rules import VOID_REISSUE_MIN, classify_voids
    ids = W._scope_ids(con, branch, group)
    if not ids:
        return {"available": False}
    cover = W._detail_cover(con)
    cls = classify_voids(con, list(ids), cs, ce)
    source, rows, total = _level1(con, ids, cs, ce, where, params, cover, "month" if whole_month else "range", cls=cls)
    by_name = {v: k for k, v in ids.items()}
    shown, rest = [], []
    for r in rows:
        if r["voids"] or r["reprints"]:
            bills, reasons, more, reprints, rmore = bill_lists(con, by_name[r["branch"]], cs, ce, where, params, cls=cls, cap=cap)
            if source != "detail":
                bills, reasons, more = [], [], 0
            shown.append({**r, "reasons": reasons, "bills": bills, "bills_more": more, "reprint_bills": reprints, "reprints_more": rmore})
        else:
            rest.append(r)
    return {"available": True, "source": source, "range": [cs.isoformat(), ce.isoformat()], "total": total,
            "bands": {"watch": W.T_VOID_WATCH, "fix": W.T_VOID_FIX}, "reissue_min": VOID_REISSUE_MIN, "cap": cap,
            "branches": shown, "rest": {"branches": len(rest), "bills_total": sum(r["bills_total"] for r in rest)}}


@ttl_cache
def report_voids(window: str = Query("month"), month: Optional[str] = Query(None), date: Optional[str] = Query(None),
                 branch: Optional[str] = Query(None), group: Optional[str] = Query(None), lang: str = Query("th")):
    th = lang != "en"
    if window not in WINDOWS:
        window = "month"
    where, params = _where(branch, group)
    with get_db() as con:
        cs, ce, day = _anchors(con, month, date, where, params, th)
        if not cs:
            return {"available": False, "line": "ยังไม่มีข้อมูลยอดขาย" if th else "no sales data"}
        if window != "month" and not day:
            window = "month"
        s, e = W.void_bounds(window, cs, ce, day)
        cover = W._detail_cover(con)
        ids = W._scope_ids(con, branch, group)
        if not ids:
            return {"available": False, "line": "ไม่พบสาขา" if th else "no such branch"}
        out = {"available": True, "window": window, "range": [s.isoformat(), e.isoformat()], "label": W.range_label(s, e, th),
               "month_range": [cs.isoformat(), ce.isoformat()], "anchor": day.isoformat() if day else None,
               "windows": [k for k in WINDOWS if k == "month" or day],
               "bands": {"watch": W.T_VOID_WATCH, "fix": W.T_VOID_FIX}, "min_bills_day": W.T_VOID_MIN_BILLS, "scope": branch or group or "",
               "reissue_min": VOID_REISSUE_MIN}
        if branch:
            bid = next(iter(ids))
            source, l2 = _level2(con, bid, ids[bid], s, e, where, params, cover, window)
            return {**out, "level": 2, "branch": ids[bid], "source": source, **l2}
        source, rows, total = _level1(con, ids, s, e, where, params, cover, window)
        return {**out, "level": 1, "source": source, "rows": rows, "total": total}


@router.get("/api/reports/voids")
def report_voids_route(request: Request, window: str = Query("month"), month: Optional[str] = Query(None),
                       date: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                       group: Optional[str] = Query(None), lang: str = Query("th")):
    """Level 1 (every branch) or, with `branch`, level 2 (the branch's cancelled and reprinted bills) of one window."""
    branch = _pin(request, branch)
    return report_voids(window=window, month=month, date=date, branch=branch, group=group, lang=lang)


def _pin(request, branch):
    import auth as _auth
    scoped = _auth.scope_branch(request)
    if scoped:
        if branch and branch != scoped:
            raise HTTPException(403, detail="บัญชีนี้ดูได้เฉพาะสาขาของตนเอง", headers={"X-Denied": "role"})
        return scoped
    return branch


@router.get("/api/reports/voids/bill")
def report_voids_bill(request: Request, branch: str = Query(...), receipt: str = Query(...)):
    """Level 3 - one bill that was sold (a reprinted bill): its header, lines (sale_bill_item, when kept) and payments."""
    from rules import bill_item_branch_sql, net_revenue_row
    branch = _pin(request, branch)
    with get_db() as con:
        h = con.execute(f"""SELECT t.branch_id, b.name, t.receipt_no, t.sold_at, t.cashier, t.table_no, t.customer_count,
                                   ROUND(t.total_inc_vat), ROUND(COALESCE(t.discount,0)), COALESCE(t.print_times,0), ROUND({net_revenue_row('t')})
                            FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
                            WHERE b.name=? AND t.receipt_no=? AND t.source='Easy' ORDER BY t.sold_at DESC LIMIT 1""",
                        (branch, receipt)).fetchone()
        if not h:
            return {"available": False, "line": "ไม่พบบิลนี้ในยอดขาย"}
        lines, kept = [], False
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sale_bill_item'").fetchone():
            m = bill_item_branch_sql(con, "i", "t1").replace("t1.branch_id", "?")
            prm = [receipt] + ([h[0]] if m else [])
            lines = [{"menu": (r[0] or r[1] or "—").replace("(B) ", "").replace("(A) ", ""), "qty": r[2] or 0, "rev": r[3] or 0}
                     for r in con.execute(f"""SELECT s.description, s.sku_code, ROUND(SUM(i.qty),2), ROUND(SUM(i.rev))
                                              FROM sale_bill_item i JOIN skus s ON s.sku_id=i.sku_id
                                              WHERE i.receipt_no=?{m} GROUP BY i.sku_id ORDER BY 4 DESC, 3 DESC""", prm)]
            kept = bool(lines)
        pays = []
        try:
            pays = [{"code": r[0], "amount": round(r[1] or 0)} for r in con.execute(
                "SELECT payment_code, SUM(amount) FROM sale_payment WHERE branch_id=? AND receipt_no=? GROUP BY payment_code ORDER BY 2 DESC",
                (h[0], receipt))]
        except Exception:
            pays = []
    return {"available": True, "branch": h[1], "receipt": h[2], "at": h[3], "cashier": h[4] or "", "table": h[5] or "",
            "covers": h[6] or 0, "total": h[7] or 0, "discount": h[8] or 0, "prints": h[9], "net": h[10] or 0,
            "lines": lines, "lines_kept": kept, "payments": pays}
