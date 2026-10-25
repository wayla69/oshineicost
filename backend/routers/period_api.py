# -*- coding: utf-8 -*-
"""PR-PP2 · the sales figures of one period pattern (docs/PERIOD_PATTERN.md), for the pages that show a period's
headline with its two comparisons: เทียบช่วงก่อน + เทียบปีก่อน.

  GET /api/period/sales     revenue · covers · bills · per head · per bill · discount for the current window and its
                            two bases (and per branch with by_branch=1). The overall page's hero and KPI cards, its
                            best / needs-attention branch, the benchmark headline.
  GET /api/period/headline  the headline figures of one page (`page=`) on the same three windows - the pages that had
                            no comparison (guest, buffetperf, timeflow, staff, engineering, cashctl, custtype,
                            pricezone, cancelbill, opsreport) get the two chips on their headline figures only.

The windows are periods.windows: the anchor is the scope's last closed day, a period not finished is to date and both
bases are their first n days; a base that starts before the first day with bills is not compared (null -> "—").
A month-grain source (pos_customer_type_month) cannot cut a month to date: its current month is shown without
comparison and a year runs over the closed months only (`grain_note`)."""
import datetime as _dt
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

import periods as P
from core import get_db, ttl_cache
from rules import VAT_DIVISOR, net_revenue_sum, scope_filter

router = APIRouter()

_KINDS = ("day", "week", "month", "year", "range")


def _scope(branch, group):
    base = ["t.source='Easy'", "b.name!='ALL'"]
    params: dict = {}
    scope_filter(base, params, branch, group)
    return " AND ".join(base), params


def _win(con, kind, pick, date_from, date_to, where, params):
    k = P.kind_of(kind, "month")
    w = P.windows(con, k, where, params, pick=pick, date_from=date_from, date_to=date_to)
    if not w:
        return None
    return w


def _sales(con, where, params, s, e, by_branch=False):
    rows = con.execute(f"""
        SELECT {"b.name branch," if by_branch else ""}
               ROUND({net_revenue_sum('t')}, 2) rev,
               COALESCE(SUM(t.customer_count), 0) cust, COUNT(*) bills,
               ROUND(COALESCE(SUM(t.discount), 0), 2) disc,
               ROUND(COALESCE(SUM(t.total_inc_vat), 0), 2) inc_vat
        FROM sales_transaction t JOIN branches b ON b.branch_id = t.branch_id
        WHERE {where} AND date(t.sold_at) BETWEEN :_s AND :_e
        {"GROUP BY b.name" if by_branch else ""}""", {**params, "_s": s, "_e": e}).fetchall()

    def one(r):
        rev, cust, bills, disc = (r["rev"] or 0), (r["cust"] or 0), (r["bills"] or 0), (r["disc"] or 0)
        return {"revenue": rev, "customers": cust, "bills": bills, "discount": disc, "revenue_inc_vat": r["inc_vat"] or 0,
                "rev_per_head": round(rev / cust, 1) if cust else None,
                "avg_ticket": round(rev / bills, 1) if bills else None,
                # share of PRE-discount sales (the Discounts tab's base)
                "discount_pct": round(disc / (rev + disc) * 100, 2) if (rev + disc) else None,
                "party": round(cust / bills, 2) if bills else None}
    if by_branch:
        return {r["branch"]: one(r) for r in rows}
    r = rows[0] if rows else None
    return one(r) if r and r["bills"] else None


@router.get("/api/period/sales")
@ttl_cache
def period_sales(kind: str = Query("month"), pick: Optional[str] = Query(None),
                 date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                 branch: Optional[str] = Query(None), group: Optional[str] = Query(None),
                 by_branch: int = Query(0)):
    """The sales figures of one period and its two bases. kind day|week|month|year|range; pick = any day of the
    period wanted (default: the period of the last closed day); date_from/date_to for range."""
    if P.kind_of(kind, "") not in _KINDS:
        raise HTTPException(400, "ช่วงเวลาไม่ถูกต้อง")
    where, params = _scope(branch, group)
    with get_db() as con:
        w = _win(con, kind, pick, date_from, date_to, where, params)
        if not w:
            return {"period": None, "cur": None, "prev": None, "ly": None, "branches": []}
        cur = _sales(con, where, params, *w["cur"])
        prev = _sales(con, where, params, *w["prev"]) if w["has_prev"] else None
        ly = _sales(con, where, params, *w["ly"]) if w["has_ly"] else None
        branches = []
        if by_branch:
            bc = _sales(con, where, params, *w["cur"], by_branch=True)
            bp = _sales(con, where, params, *w["prev"], by_branch=True) if w["has_prev"] else {}
            bl = _sales(con, where, params, *w["ly"], by_branch=True) if w["has_ly"] else {}
            for nm, c in sorted(bc.items(), key=lambda kv: -(kv[1]["revenue"] or 0)):
                p, y = bp.get(nm), bl.get(nm)
                branches.append({"branch": nm, "cur": c, "prev": p, "ly": y,
                                 "rev_prev_pct": P.pct(c["revenue"], p["revenue"]) if p else None,
                                 "rev_ly_pct": P.pct(c["revenue"], y["revenue"]) if y else None})
    pct = {}
    if cur:
        for k in ("revenue", "customers", "bills", "rev_per_head", "avg_ticket"):
            pct[k] = {"prev": P.pct(cur[k], prev[k]) if prev else None, "ly": P.pct(cur[k], ly[k]) if ly else None}
        pct["discount_pct"] = {"prev": P.pp(cur["discount_pct"], prev["discount_pct"]) if prev else None,
                               "ly": P.pp(cur["discount_pct"], ly["discount_pct"]) if ly else None}
    return {"period": P.public(w), "cur": cur, "prev": prev, "ly": ly, "change": pct, "branches": branches}


# ── /api/period/headline: the headline figures of one page ─────────────────────────────────────────────────────
# figure = (key, label, unit '%'|'pp', up_good, format 'baht'|'int'|'pct'|'min'|'num')
_PAGES = {
    "guest": [("customers", "ลูกค้า (Pax)", "%", True, "int"), ("rev_per_head", "ยอด/หัว", "%", True, "baht"),
              ("bills", "บิล", "%", True, "int"), ("party", "ลูกค้าต่อบิล", "%", True, "num")],
    "buffetperf": [("revenue", "รายได้รวม", "%", True, "baht"), ("customers", "ลูกค้า (Pax)", "%", True, "int"),
                   ("rev_per_head", "ยอด/หัว", "%", True, "baht")],
    "timeflow": [("bills", "บิล", "%", True, "int"), ("customers", "ลูกค้า (Pax)", "%", True, "int"),
                 ("turn_min", "เวลาต่อโต๊ะ (นาที)", "%", False, "min")],
    "staff": [("bills", "บิล", "%", True, "int"), ("discount", "ส่วนลดรวม", "%", False, "baht"),
              ("discount_pct", "% ส่วนลด", "pp", False, "pct")],
    "engineering": [("item_qty", "จำนวนจานที่ขาย", "%", True, "int"), ("item_rev", "ยอดขายเมนู (ex-VAT)", "%", True, "baht")],
    "cashctl": [("short_total", "ยอดขาดรวม", "%", False, "baht"), ("short_days", "วัน-สาขาที่ขาด", "%", False, "int"),
                ("over_total", "ยอดเกินรวม", "%", False, "baht")],
    "custtype": [("ct_net", "ยอดรวม (ex-VAT)", "%", True, "baht"), ("ct_delivery_pct", "สัดส่วนเดลิเวอรี", "pp", True, "pct")],
    "pricezone": [("price_changes", "การแก้ราคา", "%", False, "int")],
    "cancelbill": [("cancel_bills", "บิลยกเลิก/ลบ", "%", False, "int"), ("cancel_amount", "มูลค่าที่ยกเลิก", "%", False, "baht"),
                   ("cancel_rate", "อัตรายกเลิก", "pp", False, "pct")],
    "opsreport": [("pay_total", "รับชำระรวม", "%", True, "baht"), ("turn_min", "โต๊ะหมุนเวียน เฉลี่ย (นาที)", "%", False, "min"),
                  ("void_value", "มูลค่า Void", "%", False, "baht")],
}
# the grain of a page's figures: day-level sources take every kind; a month-level one month and year only
PAGE_KINDS = {k: (["month", "year"] if k == "custtype" else ["week", "month", "year"]) for k in _PAGES}
_MONTH_GRAIN = {"custtype"}
_DELIV = r"grab|line|food\s*panda|foodpanda|delivery|robinhood|shopee|เดลิเวอรี|แกร็บ|ไลน์|ส่ง"


def _pos_where(branch, group):
    f = ["b.name!='ALL'"]
    p: dict = {}
    scope_filter(f, p, branch, group)
    return " AND ".join(f), p


# the first day each POS source holds (a window that starts before it is not counted: "—", never a made-up 0)
_SRC_FIRST = {"turn": ("pos_table_turn_day", "day"), "sku": ("sales_sku_daily", "day"), "cash": ("pos_cashcount_day", "day"),
              "ct": ("pos_customer_type_month", "period_month || '-01'"), "price": ("pos_price_change", "substr(change_date, 1, 10)"),
              "cancel": ("pos_cancel_bill_detail", "substr(sale_date, 1, 10)"), "pay": ("pos_payment_day", "day"),
              "void": ("pos_void_day", "day")}


def _first(con, src, memo):
    if src not in memo:
        t, col = _SRC_FIRST[src]
        try:
            memo[src] = con.execute(f"SELECT MIN({col}) FROM {t}").fetchone()[0]
        except Exception:
            memo[src] = None
    return memo[src]


def _figures(con, page, s, e, where, params, pw, pp_, memo=None):
    """{key: value} of one window [s, e] (ISO days) for `page`; a source that starts after `s` gives no value."""
    out: dict = {}
    memo = {} if memo is None else memo
    keys = {f[0] for f in _PAGES[page]}
    has = lambda src: bool(_first(con, src, memo)) and s >= _first(con, src, memo)   # noqa: E731
    if keys & {"customers", "rev_per_head", "bills", "party", "revenue", "discount", "discount_pct"}:
        out.update(_sales(con, where, params, s, e) or {})
    keys = {k for k in keys if not (
        (k == "turn_min" and not has("turn")) or (k in ("item_qty", "item_rev") and not has("sku")) or
        (k in ("short_total", "short_days", "over_total") and not has("cash")) or
        (k in ("ct_net", "ct_delivery_pct") and not has("ct")) or (k == "price_changes" and not has("price")) or
        (k in ("cancel_bills", "cancel_amount", "cancel_rate") and not has("cancel")) or
        (k == "pay_total" and not has("pay")) or (k == "void_value" and not has("void")))}
    if "turn_min" in keys:
        r = con.execute(f"""SELECT SUM(x.tabs * x.avg_turn_min) tm, SUM(x.tabs) tabs FROM pos_table_turn_day x
                            JOIN branches b ON b.branch_id = x.branch_id
                            WHERE {pw} AND x.day BETWEEN :_s AND :_e AND x.avg_turn_min > 0""", {**pp_, "_s": s, "_e": e}).fetchone()
        out["turn_min"] = round(r["tm"] / r["tabs"], 1) if r and r["tabs"] else None
    if keys & {"item_qty", "item_rev"}:
        r = con.execute(f"""SELECT SUM(x.qty) q, SUM(x.subtotal_ex_vat) v FROM v_sales_sku_daily x
                            JOIN branches b ON b.branch_id = x.branch_id
                            WHERE {pw} AND x.day BETWEEN :_s AND :_e""", {**pp_, "_s": s, "_e": e}).fetchone()
        out["item_qty"] = round(r["q"] or 0) if r and r["q"] else None
        out["item_rev"] = round(r["v"] or 0, 2) if r and r["v"] else None
    if keys & {"short_total", "short_days", "over_total"}:
        r = con.execute(f"""SELECT ROUND(-SUM(CASE WHEN x.over_short < 0 THEN x.over_short ELSE 0 END)) st,
                                   ROUND(SUM(CASE WHEN x.over_short > 0 THEN x.over_short ELSE 0 END)) ot,
                                   SUM(CASE WHEN x.over_short < 0 THEN 1 ELSE 0 END) sd, COUNT(*) n
                            FROM pos_cashcount_day x JOIN branches b ON b.branch_id = x.branch_id
                            WHERE {pw} AND x.day BETWEEN :_s AND :_e""", {**pp_, "_s": s, "_e": e}).fetchone()
        if r and r["n"]:
            out.update({"short_total": r["st"] or 0, "over_total": r["ot"] or 0, "short_days": r["sd"] or 0})
    if keys & {"ct_net", "ct_delivery_pct"}:
        import re as _re
        rows = con.execute(f"""SELECT x.type_name t, SUM(x.customers) c, SUM(x.net) / {VAT_DIVISOR} n
                               FROM pos_customer_type_month x JOIN branches b ON b.branch_id = x.branch_id
                               WHERE {pw} AND x.period_month BETWEEN :_ms AND :_me GROUP BY x.type_name""",
                           {**pp_, "_ms": s[:7], "_me": e[:7]}).fetchall()
        tc = sum(r["c"] or 0 for r in rows)
        if rows:
            dc = sum(r["c"] or 0 for r in rows if _re.search(_DELIV, r["t"] or "", _re.I))
            out["ct_net"] = round(sum(r["n"] or 0 for r in rows), 2)
            out["ct_delivery_pct"] = round(dc / tc * 100, 2) if tc else None
    if "price_changes" in keys:
        r = con.execute(f"""SELECT COUNT(*) n FROM pos_price_change x JOIN branches b ON b.branch_id = x.branch_id
                            WHERE {pw} AND substr(x.change_date, 1, 10) BETWEEN :_s AND :_e""", {**pp_, "_s": s, "_e": e}).fetchone()
        out["price_changes"] = r["n"] if r else 0
    if keys & {"cancel_bills", "cancel_amount", "cancel_rate"}:
        r = con.execute(f"""SELECT COUNT(*) n, ROUND(SUM(x.amount)) a FROM pos_cancel_bill_detail x
                            JOIN branches b ON b.branch_id = x.branch_id
                            WHERE {pw} AND substr(x.sale_date, 1, 10) BETWEEN :_s AND :_e""", {**pp_, "_s": s, "_e": e}).fetchone()
        valid = (_sales(con, where, params, s, e) or {}).get("bills") or 0
        n = (r["n"] or 0) if r else 0
        out.update({"cancel_bills": n, "cancel_amount": (r["a"] or 0) if r else 0,
                    "cancel_rate": round(n / (valid + n) * 100, 2) if (valid + n) else None})
    if "pay_total" in keys:
        r = con.execute(f"""SELECT ROUND(SUM(x.amount)) a FROM pos_payment_day x JOIN branches b ON b.branch_id = x.branch_id
                            WHERE {pw} AND x.day BETWEEN :_s AND :_e""", {**pp_, "_s": s, "_e": e}).fetchone()
        out["pay_total"] = (r["a"] or None) if r else None
    if "void_value" in keys:
        r = con.execute(f"""SELECT ROUND(SUM(x.ext_price)) v FROM pos_void_day x JOIN branches b ON b.branch_id = x.branch_id
                            WHERE {pw} AND x.day BETWEEN :_s AND :_e""", {**pp_, "_s": s, "_e": e}).fetchone()
        out["void_value"] = (r["v"] or 0) if r else 0
    return out


def _closed_months(w):
    """A month-grain source: the current window cut to whole closed months (None when not one closes)."""
    cs, ce = w["cur"]
    if w["complete"]:
        return w
    if w["kind"] != "year":
        return None
    last = _dt.date.fromisoformat(ce).replace(day=1) - _dt.timedelta(days=1)       # the last closed month's last day
    if last.isoformat() < cs:
        return None
    r = P.resolve("year", last.isoformat(), date_from=cs)
    r.update({k: w[k] for k in ("anchor", "first_day")})
    r["has_prev"], r["has_ly"] = P.covered(r["prev"], w["first_day"]), P.covered(r["ly"], w["first_day"])
    return r


@router.get("/api/period/headline")
@ttl_cache
def period_headline(page: str = Query(...), kind: str = Query("month"), pick: Optional[str] = Query(None),
                    date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                    branch: Optional[str] = Query(None), group: Optional[str] = Query(None)):
    """The headline figures of `page` on the current window and its two bases."""
    if page not in _PAGES:
        raise HTTPException(400, "ไม่รู้จักหน้านี้")
    k = P.kind_of(kind, "month")
    if k not in PAGE_KINDS[page] + ["range"]:
        k = "month"
    where, params = _scope(branch, group)
    pw, pp_ = _pos_where(branch, group)
    note = None
    with get_db() as con:
        w = _win(con, k, pick, date_from, date_to, where, params)
        if not w:
            return {"page": page, "period": None, "figures": [], "kinds": PAGE_KINDS[page]}
        shown = w
        if page in _MONTH_GRAIN:
            shown = _closed_months(w)
            if shown is None:
                note = "ข้อมูลนี้เก็บเป็นรายเดือน เดือนที่ยังไม่จบจึงยังเทียบไม่ได้"
                shown = dict(w, has_prev=False, has_ly=False)
            elif shown is not w:
                note = "ข้อมูลนี้เก็บเป็นรายเดือน จึงนับถึงเดือนที่ปิดแล้ว"
        memo: dict = {}
        cur = _figures(con, page, *shown["cur"], where, params, pw, pp_, memo)
        prev = _figures(con, page, *shown["prev"], where, params, pw, pp_, memo) if shown["has_prev"] else {}
        ly = _figures(con, page, *shown["ly"], where, params, pw, pp_, memo) if shown["has_ly"] else {}
    figs = []
    for key, label, unit, up_good, fm in _PAGES[page]:
        c, p, y = cur.get(key), prev.get(key), ly.get(key)
        ch = P.pp if unit == "pp" else P.pct
        figs.append({"key": key, "label": label, "unit": unit, "up_good": up_good, "format": fm,
                     "cur": c, "prev": p, "ly": y,
                     "prev_change": ch(c, p) if p is not None else None, "ly_change": ch(c, y) if y is not None else None})
    return {"page": page, "period": P.public(shown), "figures": figs, "kinds": PAGE_KINDS[page], "grain_note": note}
