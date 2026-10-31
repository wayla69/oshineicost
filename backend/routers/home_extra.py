"""Home + Extra domain router — the Daily-Brief home tiles (/api/home/*) and
the deeper-POS-analytics group (/api/extra/*), moved VERBATIM out of main.py
(P1 structure refactor, step 6). No logic changes; URLs unchanged. Same
host-module pattern as routers/reports.py.
"""
# Shared infrastructure and helpers come from core.py by name — the old
# `globals().update(getattr(main, …))` bootstrap is gone (Phase 2, 2026-09).
from fastapi import Query, Request
from typing import Optional
from rules import closed_branches, closed_day_clause, drop_closed, latest_settled_day, picker_closed_branches
from rules import (VAT_DIVISOR, coverage_from_sums, coverage_sums_sql, effective_unit_cost_sql, food_cost_target,
                   needs_cost_sql, net_revenue_row, net_revenue_sum, scope_filter, tier_code_sql)
from targets import targets_for_month
from core import (
    _FOOD_CATEGORY_FILTER, _cogs_case, get_db, ttl_cache,
)

from fastapi import APIRouter
router = APIRouter()




@router.get("/api/home/branch-scorecard")
@ttl_cache
def home_branch_scorecard(date: Optional[str] = Query(None), group: Optional[str] = Query(None),
                          branch: Optional[str] = Query(None)):
    """Per-branch scorecard for a focus day: revenue, customers, bills, rev/head, an 8-day sparkline and THE daily
    comparison - against "ปกติ", the median of the same weekday over the 8 weeks before (rules.daily_baseline; owner
    2026-10-31, the one daily baseline of the app). REVENUE ONLY (total_inc_vat/1.07 − service_charge).
    Each branch: normal (baht), normal_customers, normal_rph, normal_weeks, vs_normal_pct, vs_normal_baht, the cause
    (cust_vs_normal_pct, rph_vs_normal_pct, discount_pct against discount_pct_normal), status (down | up | ok | nobase |
    missing) and below_normal - THE "ต่ำกว่าปกติ" verdict every screen counts (below_count). `chain` = the scope summed per
    day against its own median: the figure the phone hero and the Daily Brief headline quote.
    DV2: branches are ranked by vs_normal_baht (most below normal first)."""
    import datetime as _dt
    from rules import (BASELINE_WEEKS, baseline_dates, baseline_from_days, baseline_label, chain_baseline, daily_series)
    base = ["t.source='Easy'", "b.name!='ALL'"]; params: dict = {}
    scope_filter(base, params, branch, group)
    where = " AND ".join(base)
    with get_db() as con:
        # the latest FINISHED day whose bills have arrived (rules.latest_settled_day) —
        # never the day still trading, nor a day the POS has not posted yet
        settled = latest_settled_day(con, where=where, params=params)
        maxd = settled["day"]
        if not maxd:
            return {"date": None, "days": [], "branches": []}
        focus = (date or maxd)[:10]
        fy, fm, fd = (int(x) for x in focus.split("-"))
        fdate = _dt.date(fy, fm, fd)
        start = fdate - _dt.timedelta(days=7)
        days = [(start + _dt.timedelta(days=i)).isoformat() for i in range(8)]   # the sparkline, incl focus
        per = daily_series(con, where, params, days + baseline_dates(focus))
        out = []
        for nm, dm in per.items():
            if not any(dm.get(d, {}).get("bills") for d in days):
                continue                                 # no sale in the sparkline week: not a row (closed_branches below)
            bl = baseline_from_days(dm, focus)
            N = bl["normal"]
            spark = [round(dm.get(d, {}).get("rev", 0), 0) for d in days]
            today = dm.get(focus, {})
            trev, tcust, tbills = today.get("rev", 0), today.get("cust", 0), today.get("bills", 0)
            out.append({"branch": nm, "revenue": trev, "customers": tcust, "bills": tbills,
                        "rev_per_head": round(trev / tcust, 1) if tcust else None,
                        "spark": spark,
                        "normal": round(N["revenue"], 0) if N["revenue"] is not None else None,
                        "normal_customers": N["customers"], "normal_rph": N["rev_per_head"],
                        "normal_weeks": N["n_weeks"],
                        "vs_normal_baht": bl["vs_normal_baht"], "vs_normal_pct": bl["vs_normal_pct"],
                        # PR-PD (owner 2026-10-29, no. 1) · the cause behind a day below normal, on the same normal
                        "cust_vs_normal_pct": bl["cust_vs_normal_pct"], "rph_vs_normal_pct": bl["rph_vs_normal_pct"],
                        "discount_pct": bl["discount_pct"], "discount_pct_normal": N["discount_pct"],
                        "status": bl["status"], "below_normal": bl["below_normal"],
                        # PR-PD no. 4: a branch with sales in the window but none on the focus day = its data has not
                        # arrived (or it was shut that day); never a "−100 %" to call about
                        "reported": trev > 0})
        out.sort(key=lambda r: (r["vs_normal_baht"] is None, r["vs_normal_baht"] if r["vs_normal_baht"] is not None else 0, -r["revenue"]))
        # owner 2026-10-26: a closed branch is not a row (rules.closed_branches: the focus day and the 7 days before it)
        out = drop_closed(out, closed_branches(con, [(start.isoformat(), focus)], keep=branch))
        ch = chain_baseline(con, per, focus, keep=branch)     # the brief's own chain figure (rules.daily_baseline)
    chain = {"revenue": ch["revenue"], "customers": ch["customers"], "bills": ch["bills"],
             "normal": round(ch["normal"]["revenue"], 0) if ch["normal"]["revenue"] is not None else None,
             "normal_weeks": ch["normal"]["n_weeks"], "normal_dates": ch["normal"]["dates"],
             "vs_normal_pct": ch["vs_normal_pct"], "vs_normal_baht": ch["vs_normal_baht"], "status": ch["status"]}
    nw = max((r["normal_weeks"] for r in out), default=0)
    return {"date": focus, "days": days, "branches": out, "chain": chain,
            "baseline_weeks": BASELINE_WEEKS, "normal_label": baseline_label(nw), "normal_label_short": baseline_label(nw, short=True),
            "below_count": sum(1 for r in out if r["below_normal"]),
            "reported": sum(1 for r in out if r["reported"]), "missing": [r["branch"] for r in out if not r["reported"]],
            "skipped_day": None if date else settled["skipped_day"], "skipped_bills": None if date else settled["skipped_bills"]}


# ═══════════════════════════════════════════════════════════════════════════
# EXTRA — deeper POS analytics from previously-untapped tables (separate group)
# All counts/amounts only · NO POS cost (iron rule: COGS stays on our BoM).
# ═══════════════════════════════════════════════════════════════════════════
def _extra_months(con, table, col="period_month"):
    r = con.execute(f"SELECT MAX({col}) m FROM {table}").fetchone()
    return r["m"] if r else None


@router.get("/api/extra/cash")
def extra_cash(month: Optional[str] = Query(None), branch: Optional[str] = Query(None),
               group: Optional[str] = Query(None)):
    """Cash over/short + ops summary per branch per day (loss prevention)."""
    filt = ["b.name!='ALL'"]; p: dict = {}
    scope_filter(filt, p, branch, group)
    tw = " AND ".join(filt)
    with get_db() as con:
        mon = month or _extra_months(con, "pos_cashcount_day", "substr(day,1,7)")
        if not mon:
            return {"month": None, "branches": [], "worst_days": [], "summary": {}}
        pm = {**p, "m": mon}
        JN = "FROM pos_cashcount_day x JOIN branches b ON b.branch_id=x.branch_id WHERE " + tw + " AND substr(x.day,1,7)=:m"
        branches = [dict(r) for r in con.execute(
            f"""SELECT b.name branch, ROUND(SUM(x.over_short)) over_short, ROUND(SUM(x.total_sales)) sales,
                SUM(x.pax) pax, ROUND(SUM(x.discount)) discount, ROUND(SUM(x.cancel)) cancel,
                SUM(CASE WHEN x.over_short<0 THEN 1 ELSE 0 END) short_days
                {JN} GROUP BY b.name ORDER BY over_short ASC""", pm).fetchall()]
        worst = [dict(r) for r in con.execute(
            f"""SELECT b.name branch, x.day, ROUND(x.over_short) over_short, ROUND(x.total_sales) sales, x.pax
                {JN} AND x.over_short<>0 ORDER BY x.over_short ASC LIMIT 15""", pm).fetchall()]
        s = con.execute(
            f"""SELECT ROUND(SUM(x.over_short)) net, ROUND(SUM(CASE WHEN x.over_short<0 THEN x.over_short ELSE 0 END)) short_total,
                ROUND(SUM(CASE WHEN x.over_short>0 THEN x.over_short ELSE 0 END)) over_total,
                SUM(CASE WHEN x.over_short<0 THEN 1 ELSE 0 END) short_days, COUNT(*) day_branch
                {JN}""", pm).fetchone()
        summary = {"net": (s["net"] or 0), "short_total": (s["short_total"] or 0),
                   "over_total": (s["over_total"] or 0), "short_days": (s["short_days"] or 0),
                   "day_branch": (s["day_branch"] or 0)}
        closed = closed_branches(con, [(mon, mon)], keep=branch)     # owner 2026-10-26: rows only; the summary keeps the month
    return {"month": mon, "branches": drop_closed(branches, closed), "worst_days": drop_closed(worst, closed), "summary": summary}


@router.get("/api/extra/customer-type")
def extra_customer_type(month: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                        group: Optional[str] = Query(None)):
    """Customer-type / channel mix: walk-in vs Grab vs Line Man vs takeaway vs credit."""
    filt = ["b.name!='ALL'"]; p: dict = {}
    scope_filter(filt, p, branch, group)
    tw = " AND ".join(filt)
    with get_db() as con:
        mon = month or _extra_months(con, "pos_customer_type_month")
        if not mon:
            return {"month": None, "types": []}
        # net is INC-VAT in pos_customer_type_month → divide to ex-VAT (rule: revenue
        # is always ex-VAT). The `customers` count is a POS channel-tag tally, ~10x
        # real covers — usable only for CHANNEL MIX %, never as absolute pax, so we
        # expose no per-head figure here (it would be meaningless).
        rows = [dict(r) for r in con.execute(
            f"""SELECT x.type_name type, SUM(x.customers) customers, ROUND(SUM(x.net)/{VAT_DIVISOR}) net
                FROM pos_customer_type_month x JOIN branches b ON b.branch_id=x.branch_id
                WHERE {tw} AND x.period_month=:m GROUP BY x.type_name ORDER BY customers DESC""",
            {**p, "m": mon}).fetchall()]
        tc = sum(r["customers"] or 0 for r in rows)
        tn = sum(r["net"] or 0 for r in rows)
        tc_guard = tc or 1
        tn_guard = tn or 1
        for r in rows:
            r["cust_pct"] = round((r["customers"] or 0) / tc_guard * 100, 1)
            r["net_pct"] = round((r["net"] or 0) / tn_guard * 100, 1)
        # Per-branch breakdown (แยกตามสาขา): total + delivery share per branch.
        brows = con.execute(
            f"""SELECT b.name branch, x.type_name type, SUM(x.customers) customers, ROUND(SUM(x.net)/{VAT_DIVISOR}) net
                FROM pos_customer_type_month x JOIN branches b ON b.branch_id=x.branch_id
                WHERE {tw} AND x.period_month=:m GROUP BY b.name, x.type_name""",
            {**p, "m": mon}).fetchall()
        closed = closed_branches(con, [(mon, mon)], keep=branch)     # owner 2026-10-26
    import re as _re
    _DELIV = _re.compile(r'grab|line|food\s*panda|foodpanda|delivery|robinhood|shopee|เดลิเวอรี|แกร็บ|ไลน์|ส่ง', _re.I)
    bmap: dict = {}
    for r in brows:
        e = bmap.setdefault(r["branch"], {"branch": r["branch"], "customers": 0, "net": 0, "delivery_cust": 0})
        e["customers"] += r["customers"] or 0
        e["net"] += r["net"] or 0
        if _DELIV.search(r["type"] or ""):
            e["delivery_cust"] += r["customers"] or 0
    by_branch = []
    for e in bmap.values():
        cu = e["customers"]
        by_branch.append({**e, "delivery_pct": round(e["delivery_cust"] / cu * 100, 1) if cu else 0,
                          "net_pct": round(e["net"] / tn_guard * 100, 1) if tn else 0})
    by_branch.sort(key=lambda r: -(r["net"] or 0))
    by_branch = drop_closed(by_branch, closed)
    return {"month": mon, "types": rows, "total_customers": tc, "total_net": tn,
            "by_branch": by_branch, "count_caveat": True}


@router.get("/api/extra/modifiers")
def extra_modifiers(branch: Optional[str] = Query(None), group: Optional[str] = Query(None),
                    limit: int = Query(25)):
    """Top customer special-requests (free modifiers) — for kitchen prep planning.
    All-time counts (no revenue; choices are free)."""
    filt = ["b.name!='ALL'"]; p: dict = {}
    scope_filter(filt, p, branch, group)
    tw = " AND ".join(filt)
    with get_db() as con:
        JN = "FROM pos_modifier x JOIN branches b ON b.branch_id=x.branch_id WHERE " + tw
        choices = [dict(r) for r in con.execute(
            f"SELECT x.choice_name choice, SUM(x.n) n, ROUND(SUM(x.qty)) qty {JN} "
            f"GROUP BY x.choice_name ORDER BY n DESC LIMIT :lim", {**p, "lim": limit}).fetchall()]
        tot = con.execute(f"SELECT SUM(x.n) n {JN}", p).fetchone()
    total = (tot["n"] or 0) if tot else 0
    for r in choices:
        r["pct"] = round((r["n"] or 0) / total * 100, 1) if total else 0
    return {"choices": choices, "total": total}


@router.get("/api/extra/price-zone")
def extra_price_zone(month: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                     group: Optional[str] = Query(None), limit: int = Query(100, ge=1, le=500)):  # fix B5
    """Price-override log (governance) + table-zone layout per branch."""
    filt = ["b.name!='ALL'"]; p: dict = {}
    scope_filter(filt, p, branch, group)
    tw = " AND ".join(filt)
    with get_db() as con:
        mfilt = ""
        if month:
            mfilt = " AND substr(x.change_date,1,7)=:m"; p["m"] = month
        changes = [dict(r) for r in con.execute(
            f"""SELECT x.change_date date, b.name branch, x.branch_id bid, x.sku_id sid,
                COALESCE(s.description,x.item_desc) item,
                ROUND(x.default_price) default_price, ROUND(x.new_price) new_price,
                ROUND(x.new_price - x.default_price) delta, x.user_name user
                FROM pos_price_change x JOIN branches b ON b.branch_id=x.branch_id
                LEFT JOIN skus s ON s.sku_id=x.sku_id WHERE {tw}{mfilt}
                ORDER BY x.change_date DESC LIMIT :lim""", {**p, "lim": limit}).fetchall()]
        # Price elasticity: avg daily qty 14d before vs 14d after each change
        # (same branch+sku, from sales_sku_daily). Directional signal only.
        import datetime as _dt
        drange = con.execute("SELECT MIN(day) lo, MAX(day) hi FROM sales_sku_daily").fetchone()
        dlo = drange["lo"] if drange else None; dhi = drange["hi"] if drange else None
        for c in changes:
            c["elasticity"] = None; c["qty_pct"] = None; c["before_qty"] = None; c["after_qty"] = None
            if not (c.get("sid") and c.get("bid") and dlo and c.get("date")):
                continue
            cd = c["date"][:10]
            if cd < dlo or cd > dhi:
                continue
            d0 = _dt.date(*(int(x) for x in cd.split("-")))
            bS = (d0 - _dt.timedelta(days=14)).isoformat(); bE = (d0 - _dt.timedelta(days=1)).isoformat()
            aS = cd; aE = (d0 + _dt.timedelta(days=13)).isoformat()
            er = con.execute("""
                SELECT AVG(CASE WHEN day BETWEEN :bS AND :bE THEN qty END) bq,
                       AVG(CASE WHEN day BETWEEN :aS AND :aE THEN qty END) aq
                FROM v_sales_sku_daily WHERE branch_id=:bid AND sku_id=:sid
                  AND day BETWEEN :bS AND :aE""",
                {"bid": c["bid"], "sid": c["sid"], "bS": bS, "bE": bE, "aS": aS, "aE": aE}).fetchone()
            bq = er["bq"]; aq = er["aq"]
            if bq and bq > 0 and aq is not None:
                c["before_qty"] = round(bq, 1); c["after_qty"] = round(aq, 1)
                qpct = (aq - bq) / bq * 100
                c["qty_pct"] = round(qpct, 1)
                dp = (c["new_price"] or 0) - (c["default_price"] or 0)
                ppct = (dp / c["default_price"] * 100) if c["default_price"] else None
                if ppct:
                    c["elasticity"] = round(qpct / ppct, 2)
        for c in changes:
            c.pop("bid", None); c.pop("sid", None)
        # strip month param for the zone query (zones have no date)
        zp = {k: v for k, v in p.items() if k != "m"}
        zones = [dict(r) for r in con.execute(
            f"""SELECT b.name branch, COUNT(*) zones, SUM(x.tables) tables, SUM(x.seats) seats
                FROM pos_zone x JOIN branches b ON b.branch_id=x.branch_id WHERE {tw}
                GROUP BY b.name ORDER BY tables DESC""", zp).fetchall()]
        cnt = con.execute(f"SELECT COUNT(*) n FROM pos_price_change x JOIN branches b ON b.branch_id=x.branch_id WHERE {tw}{mfilt}", p).fetchone()
    return {"changes": changes, "zones": zones, "change_count": (cnt["n"] or 0) if cnt else 0,
            "month": month}


@router.get("/api/extra/cancel-bills")
def extra_cancel_bills(month: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                       group: Optional[str] = Query(None)):
    """Whole BILLS voided/deleted (Sale.Active=0), by branch and by reason.
    Distinct from item-level Void in the Ops report."""
    filt = ["b.name!='ALL'"]; p: dict = {}
    scope_filter(filt, p, branch, group)
    tw = " AND ".join(filt)
    with get_db() as con:
        mon = month or _extra_months(con, "pos_cancel_bill")
        if not mon:
            return {"month": None, "branches": [], "reasons": [], "summary": {}}
        pm = {**p, "m": mon}
        JN = "FROM pos_cancel_bill x JOIN branches b ON b.branch_id=x.branch_id WHERE " + tw + " AND x.period_month=:m"
        branches = [dict(r) for r in con.execute(
            f"SELECT b.name branch, SUM(x.bills) bills, ROUND(SUM(x.amount)) amount {JN} "
            f"GROUP BY b.name ORDER BY amount DESC, bills DESC", pm).fetchall()]   # DV2: by value, not by count
        reasons = [dict(r) for r in con.execute(
            f"""SELECT CASE WHEN x.reason_id=0 THEN 'ไม่ระบุเหตุผล'
                         ELSE COALESCE(rr.reason_desc, 'รหัส '||x.reason_id) END reason,
                   SUM(x.bills) bills, ROUND(SUM(x.amount)) amount,
                   -- DV1 (phone table review 2026-10-25): a code the POS reason list does not hold (รหัส -1: one bill of
                   -- ฿242K) is a data fault, not a reason - flagged so it is checked, never read as a cancel cause
                   CASE WHEN x.reason_id<>0 AND MAX(rr.reason_desc) IS NULL THEN 1 ELSE 0 END abnormal
                FROM pos_cancel_bill x JOIN branches b ON b.branch_id=x.branch_id
                LEFT JOIN pos_cancel_reason rr ON rr.reason_id=x.reason_id
                WHERE {tw} AND x.period_month=:m
                GROUP BY x.reason_id ORDER BY bills DESC""", pm).fetchall()]
        for r in reasons:
            r["abnormal"] = bool(r["abnormal"])
        s = con.execute(f"SELECT SUM(x.bills) bills, ROUND(SUM(x.amount)) amount {JN}", pm).fetchone()
        # context: valid bills that month (to express cancel rate)
        vb = con.execute(
            f"""SELECT COUNT(*) n FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
                WHERE {tw} AND t.source='Easy' AND strftime('%Y-%m',t.sold_at)=:m""", pm).fetchone()
        valid = (vb["n"] or 0) if vb else 0
        cbills = (s["bills"] or 0) if s else 0
        summary = {"bills": cbills, "amount": (s["amount"] or 0) if s else 0, "valid_bills": valid,
                   "cancel_rate": round(cbills / (valid + cbills) * 100, 2) if (valid + cbills) else None}
        closed = closed_branches(con, [(mon, mon)], keep=branch)     # owner 2026-10-26: rows only; the summary keeps the month
    return {"month": mon, "branches": drop_closed(branches, closed), "reasons": reasons, "summary": summary}


@router.get("/api/extra/cash/detail")
def extra_cash_detail(branch: str = Query(...), month: str = Query(...)):
    """Per-DAY cash over/short for one branch in a month (drill from the by-branch
    table). Cash variance is a drawer-close metric — per-day is its finest grain
    (there is no per-bill over/short)."""
    with get_db() as con:
        rows = [dict(r) for r in con.execute(
            """SELECT x.day, ROUND(x.over_short,2) over_short, ROUND(x.total_sales) sales,
                      x.pax, x.invoices, ROUND(x.discount) discount, ROUND(x.cancel) cancel
               FROM pos_cashcount_day x JOIN branches b ON b.branch_id=x.branch_id
               WHERE b.name=:branch AND substr(x.day,1,7)=:m ORDER BY x.day""",
            {"branch": branch, "m": month}).fetchall()]
    return {"branch": branch, "month": month, "days": rows}


@router.get("/api/extra/cash/day")
def extra_cash_day(branch: str = Query(...), day: str = Query(...)):
    """Single-day cash over/short drill for one branch (click a worst-day row).
    Cash variance has no per-bill grain, so the actionable context is: the day's
    headline (over/short, sales, pax, discount, cancel) + the individual voided
    bills on that exact day — voids are the most common cause of a drawer short.
    Reads only already-staged tables (pos_cashcount_day + pos_cancel_bill_detail);
    cancel_staged=False if the per-bill table predates the current sync."""
    with get_db() as con:
        hd = con.execute(
            """SELECT ROUND(x.over_short,2) over_short, ROUND(x.total_sales) sales,
                      x.pax, x.invoices, ROUND(x.discount) discount, ROUND(x.cancel) cancel
               FROM pos_cashcount_day x JOIN branches b ON b.branch_id=x.branch_id
               WHERE b.name=:branch AND x.day=:day""",
            {"branch": branch, "day": day}).fetchone()
        head = dict(hd) if hd else {}
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(pos_cancel_bill_detail)")}
            user_sel = "x.cancel_user" if "cancel_user" in cols else "NULL"
            bills = [dict(r) for r in con.execute(
                f"""SELECT x.sale_date, x.receipt_no, x.amount, x.reason_id,
                       CASE WHEN x.reason_id=0 THEN 'ไม่ระบุเหตุผล'
                            ELSE COALESCE(rr.reason_desc, 'รหัส '||x.reason_id) END reason,
                       {user_sel} AS cancel_user
                    FROM pos_cancel_bill_detail x JOIN branches b ON b.branch_id=x.branch_id
                    LEFT JOIN pos_cancel_reason rr ON rr.reason_id=x.reason_id
                    WHERE b.name=:branch AND substr(x.sale_date,1,10)=:day
                    ORDER BY x.sale_date""", {"branch": branch, "day": day}).fetchall()]
            cancel_staged = True
        except Exception:
            bills, cancel_staged = [], False
    return {"branch": branch, "day": day, "head": head,
            "cancel_bills": bills, "cancel_staged": cancel_staged}


@router.get("/api/extra/cancel-bills/detail")
def extra_cancel_bills_detail(branch: str = Query(...), month: Optional[str] = Query(None),
                              limit: int = Query(400, ge=1, le=2000)):
    """Individual voided/deleted bills for one branch (drill to check each error).
    Reads pos_cancel_bill_detail, staged by sync_mssql from Sale.Active=0. Returns
    staged=False if that table isn't present yet (run a sync to populate)."""
    with get_db() as con:
        try:
            params = {"branch": branch, "lim": limit}
            mfilt = ""
            if month:
                mfilt = " AND x.period_month=:m"; params["m"] = month
            # cancel_user column only exists after a sync with the updated sync_mssql; select it
            # defensively so the drill keeps working on an older DB that predates the column.
            cols = {r[1] for r in con.execute("PRAGMA table_info(pos_cancel_bill_detail)")}
            user_sel = "x.cancel_user" if "cancel_user" in cols else "NULL"
            rows = [dict(r) for r in con.execute(
                f"""SELECT x.sale_date, x.receipt_no, x.amount, x.reason_id,
                       CASE WHEN x.reason_id=0 THEN 'ไม่ระบุเหตุผล'
                            ELSE COALESCE(rr.reason_desc, 'รหัส '||x.reason_id) END reason,
                       {user_sel} AS cancel_user
                    FROM pos_cancel_bill_detail x JOIN branches b ON b.branch_id=x.branch_id
                    LEFT JOIN pos_cancel_reason rr ON rr.reason_id=x.reason_id
                    WHERE b.name=:branch{mfilt}
                    ORDER BY x.sale_date DESC LIMIT :lim""", params).fetchall()]
            return {"branch": branch, "month": month, "bills": rows, "staged": True,
                    "capped": len(rows) >= limit}
        except Exception:
            # pos_cancel_bill_detail not yet staged (older DB) — drill gracefully empty.
            return {"branch": branch, "month": month, "bills": [], "staged": False}


@router.get("/api/extra/closing-time")
def extra_closing_time(branch: Optional[str] = Query(None), group: Optional[str] = Query(None),
                       date: Optional[str] = Query(None)):
    """Per-branch data freshness + typical shop closing time.

    Surfaces the data-lag issue: each branch's LATEST transaction (all-time) shows
    how current its synced data is; a branch sitting several days behind the chain
    maximum has not reported / synced recent sales. The typical closing time (avg
    of each day's last bill over the trailing 30 days) is the operational read —
    times before 06:00 are treated as after-midnight closes (+24h) so a 01:30 close
    sorts after a 23:00 one rather than looking like the earliest open.

    When ``date`` (YYYY-MM-DD) is given, switches to single-day mode: for that exact
    date it shows each branch's last-bill time + bill count and flags branches that
    posted NO data that day (mode='date'). Use it to inspect a specific day's gaps,
    e.g. which branches reported on 2026-06-04."""
    filt = ["b.name!='ALL'"]; p: dict = {}
    scope_filter(filt, p, branch, group)
    tw = " AND ".join(filt)
    import datetime as _dt
    with get_db() as con:
        bounds = con.execute("SELECT MAX(sold_at) mx, MIN(sold_at) mn FROM sales_transaction").fetchone()
        dm = bounds["mx"]
        if not dm:
            return {"data_max": None, "today": _dt.date.today().isoformat(),
                    "branches": [], "summary": {}}
        data_max_date = dm[:10]
        data_min_date = (bounds["mn"] or dm)[:10]
        d_hi = _dt.date(*(int(x) for x in data_max_date.split("-")))
        since = (d_hi - _dt.timedelta(days=30)).isoformat()

        # All-time latest per branch (freshness) + lifetime bill count. This is also
        # the branch universe — so single-day mode can flag branches with NO data.
        last_rows = {r["branch"]: r for r in con.execute(
            f"""SELECT b.name branch, MAX(t.sold_at) last_sold, COUNT(*) total_bills
                FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
                WHERE {tw} GROUP BY b.name""", p).fetchall()}
        # owner 2026-10-26: a closed branch is not a freshness row nor a "missing" branch of every day. The picker's set
        # (rules.picker_closed_branches: marked closed, or no bill in the last 90 days), so a branch whose sync stalls
        # for a few weeks still shows here as behind / inactive.
        for _nm in picker_closed_branches(con) - {branch}:
            last_rows.pop(_nm, None)

        # ── Single-day mode ──────────────────────────────────────────────────
        if date:
            day_rows = {r["branch"]: r for r in con.execute(
                f"""SELECT b.name branch, MAX(t.sold_at) day_last, COUNT(*) day_bills
                    FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
                    WHERE {tw} AND date(t.sold_at)=:date
                    GROUP BY b.name""", {**p, "date": date}).fetchall()}
            out = []; reported = missing = 0; day_bill_total = 0
            for name, lr in last_rows.items():
                dr = day_rows.get(name)
                day_bills = (dr["day_bills"] if dr else 0) or 0
                day_last = dr["day_last"] if dr else None
                day_close = None
                if day_last:
                    day_close = f"{day_last[11:13]}:{day_last[14:16]}"
                rep = day_bills > 0
                reported += int(rep); missing += int(not rep); day_bill_total += day_bills
                out.append({"branch": name, "day_bills": day_bills, "day_last": day_last,
                            "day_close": day_close, "last_sold": lr["last_sold"],
                            "status": "reported" if rep else "missing"})
            # Missing branches first (the gaps you came to see), then by name.
            out.sort(key=lambda r: (r["status"] != "missing", r["branch"]))
            return {"mode": "date", "date": date, "data_max": dm,
                    "data_max_date": data_max_date, "data_min_date": data_min_date,
                    "today": _dt.date.today().isoformat(), "branches": out,
                    "summary": {"reported": reported, "missing": missing,
                                "branch_count": len(out), "day_bills": day_bill_total}}

        # Trailing-30d reporting coverage, one row per day: how many branches
        # posted anything. This is the thing the per-branch table cannot show —
        # a branch that is current today but went silent for three days last week
        # reads as "อัปเดต" there, and the hole only appears here.
        n_branches = len(last_rows) or 1
        daily = [{"day": r["d"], "branches": int(r["nb"] or 0),
                  "bills": int(r["nbill"] or 0),
                  "missing": max(0, n_branches - int(r["nb"] or 0))}
                 for r in con.execute(
                     f"""SELECT date(t.sold_at) d, COUNT(DISTINCT t.branch_id) nb, COUNT(*) nbill
                           FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
                          WHERE {tw} AND t.sold_at >= :since
                          GROUP BY date(t.sold_at) ORDER BY d""", {**p, "since": since})]

        # Trailing-30d typical close: average of each day's last-bill time-of-day.
        close_rows = {r["branch"]: r for r in con.execute(
            f"""WITH day_close AS (
                  SELECT t.branch_id, date(t.sold_at) d, MAX(t.sold_at) last_at
                  FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
                  WHERE {tw} AND t.sold_at >= :since
                  GROUP BY t.branch_id, date(t.sold_at)
                )
                SELECT b.name branch, COUNT(*) days_open,
                  AVG( CAST(substr(dc.last_at,12,2) AS REAL)*60 + CAST(substr(dc.last_at,15,2) AS REAL)
                       + CASE WHEN CAST(substr(dc.last_at,12,2) AS INTEGER) < 6 THEN 1440 ELSE 0 END
                     ) avg_close_min
                FROM day_close dc JOIN branches b ON b.branch_id=dc.branch_id
                GROUP BY b.name""", {**p, "since": since}).fetchall()}

    branches = []
    behind = current = 0
    max_behind = 0
    for name, lr in last_rows.items():
        last_sold = lr["last_sold"]
        last_date = last_sold[:10] if last_sold else None
        days_behind = None
        if last_date:
            d_b = _dt.date(*(int(x) for x in last_date.split("-")))
            days_behind = (d_hi - d_b).days
        cr = close_rows.get(name)
        avg_min = round(cr["avg_close_min"]) if cr and cr["avg_close_min"] is not None else None
        avg_close = None
        if avg_min is not None:
            hh = (avg_min // 60) % 24; mm = avg_min % 60
            avg_close = f"{hh:02d}:{mm:02d}"
        if days_behind == 0:
            status = "current"; current += 1
        elif days_behind is not None and days_behind >= 14:
            status = "inactive"; behind += 1
        else:
            status = "behind"; behind += 1
        if days_behind is not None:
            max_behind = max(max_behind, days_behind)
        branches.append({
            "branch": name, "last_sold": last_sold, "last_date": last_date,
            "days_behind": days_behind, "avg_close": avg_close, "avg_close_min": avg_min,
            "days_open": (cr["days_open"] if cr else 0), "total_bills": lr["total_bills"],
            "status": status,
        })
    # Most-stale first so problems sit at the top.
    branches.sort(key=lambda r: (-(r["days_behind"] if r["days_behind"] is not None else 9999)))
    summary = {"behind": behind, "current": current, "max_days_behind": max_behind,
               "branch_count": len(branches)}
    # Days inside the window where NOT every branch reported — the silent gaps.
    summary["gap_days"] = sum(1 for d in daily if d["missing"] > 0)
    summary["window_days"] = len(daily)
    return {"mode": "latest", "data_max": dm, "data_max_date": data_max_date,
            "data_min_date": data_min_date, "today": _dt.date.today().isoformat(),
            "branches": branches, "daily": daily, "window_from": since,
            "summary": summary}




# ---------------------------------------------------------------------------
# /api/home/brief  — Daily Brief summary
# ---------------------------------------------------------------------------
@ttl_cache
def home_brief(branch: Optional[str] = Query(None)):
    """Yesterday vs target + MTD + recent anomalies + top/bottom branch."""
    with get_db() as con:
        # "Yesterday" = the latest FINISHED day whose bills have arrived
        # (rules.latest_settled_day). Plain MAX(sold_at) became today after the noon
        # sync — a few hours of bills shown as a -99.9% day (owner 2026-10-02); and the
        # 06:00 sync runs before the previous day is posted, so a finished day with a
        # handful of bills is skipped the same way (2026-10-08). The whole chain sets
        # the day, a branch filter does not move it — every tile reads the same day.
        settled = latest_settled_day(con)
        yesterday = settled["day"]
        if not yesterday:
            return {"yesterday": None}

        from datetime import datetime as _dt
        try:
            yd = _dt.strptime(yesterday, "%Y-%m-%d")
            dow_names = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
            dow_label = dow_names[yd.weekday()]
            current_month = yd.strftime("%Y-%m")
        except Exception:
            dow_label = ""
            current_month = yesterday[:7]

        # Branch filter
        branch_filter = ""
        branch_params: dict = {"yesterday": yesterday}
        if branch:
            branch_filter = " AND b.name = :branch"
            branch_params["branch"] = branch

        # Revenue / tx / customers yesterday
        yest_row = con.execute(f"""
            SELECT
                ROUND({net_revenue_sum('st')}, 2)        AS revenue,
                COUNT(*)                                AS tx_count,
                COALESCE(SUM(st.customer_count), 0)    AS customer_count,
                ROUND(AVG{net_revenue_row('st')}, 2)        AS avg_ticket
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            WHERE DATE(st.sold_at) = :yesterday
              AND st.source = 'Easy'
              AND b.name != 'ALL'
              {branch_filter}
        """, branch_params).fetchone()

        # THE daily comparison (owner 2026-10-31): the day against "ปกติ" - the median of the same weekday over the 8 weeks
        # before (rules.daily_baseline, the chain = the branches trading now summed per day; the scorecard's `chain`)
        from rules import daily_baseline
        _bw = ["t.source='Easy'", "b.name!='ALL'"] + (["b.name=:branch"] if branch else [])
        base_day = daily_baseline(con, " AND ".join(_bw), {"branch": branch} if branch else {}, day=yesterday, keep=branch)


        # MTD revenue: day 1 .. the settled day (PR-PP2 - the day still trading never enters a to-date window;
        # this summed the whole calendar month, today's first bills included)
        mtd_params: dict = {"month": current_month, "upto": yesterday}
        if branch:
            mtd_params["branch"] = branch
        mtd_row = con.execute(f"""
            SELECT
                ROUND({net_revenue_sum('st')}, 2)     AS mtd_revenue,
                COUNT(DISTINCT DATE(st.sold_at))    AS mtd_days
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            WHERE strftime('%Y-%m', st.sold_at) = :month AND DATE(st.sold_at) <= :upto
              AND st.source = 'Easy'
              AND b.name != 'ALL'
              {'AND b.name = :branch' if branch else ''}
        """, mtd_params).fetchone()
        # Revenue by branch yesterday (top 3)
        yest_branch_rows = con.execute(f"""
            SELECT b.name AS branch,
                   ROUND({net_revenue_sum('st')}, 2) AS revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            WHERE DATE(st.sold_at) = :yesterday
              AND st.source = 'Easy'
              AND b.name != 'ALL'
              {branch_filter}
            GROUP BY b.name
            ORDER BY revenue DESC
            LIMIT 3
        """, branch_params).fetchall()

        # MTD by branch
        mtd_branch_rows = con.execute(f"""
            SELECT b.name AS branch,
                   ROUND({net_revenue_sum('st')}, 2) AS mtd_revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            WHERE strftime('%Y-%m', st.sold_at) = :month AND DATE(st.sold_at) <= :upto
              AND st.source = 'Easy'
              AND b.name != 'ALL'
              {'AND b.name = :branch' if branch else ''}
            GROUP BY b.name
            ORDER BY mtd_revenue DESC
        """, mtd_params).fetchall()

        # Branch targets for current month — typed, else last year's same month (targets.py)
        try:
            target_map = {r["branch"]: r["revenue_target"] for r in targets_for_month(con, current_month) if r["revenue_target"]}
        except Exception:
            target_map = {}

        mtd_by_branch = []
        for r in mtd_branch_rows:
            br = r["branch"]
            mtd_by_branch.append({
                "branch": br,
                "mtd_revenue": r["mtd_revenue"],
                "revenue_target": target_map.get(br),
            })

        # MTD BoM food cost % - PR-PP3: the one period pattern (period_cost.compare on the month to date through the
        # settled day): COGS over the same table's dish revenue, day 1..the settled day, against the same days of last
        # month and of the month a year back (it was the whole month table - today's lines included - over the bills'
        # revenue, and the movement compared it with the WHOLE previous month).
        import periods as _P
        import period_cost as _PC
        _fw = ["t.source='Easy'", "b.name!='ALL'"] + (["b.name=:branch"] if branch else [])
        _w = _P.windows(con, "month", " AND ".join(_fw), {"branch": branch} if branch else {}, anchor_day=yesterday)
        _fc = _PC.compare(con, _w, branch, None) if _w else None
        _cur = (_fc or {}).get("cur") or None
        food_cost_pct_mtd = round(_cur["food_cost_pct"], 1) if _cur and _cur["food_cost_pct"] is not None else None
        mtd_bom_cost = _cur["cogs"] if _cur else None
        mtd_rev_val = _cur["revenue"] if _cur else None
        cost_health = _cost_health(con, current_month, branch, food_cost_pct_mtd, mtd_rev_val, _fc)

    return {
        "yesterday": yesterday,
        "skipped_day": settled["skipped_day"],       # a newer finished day set aside: its bills had not arrived
        "skipped_bills": settled["skipped_bills"],
        "dow_label": dow_label,
        "revenue_yesterday": yest_row["revenue"] if yest_row else 0,
        "tx_yesterday": yest_row["tx_count"] if yest_row else 0,
        "customer_yesterday": yest_row["customer_count"] if yest_row else 0,
        "avg_ticket_yesterday": yest_row["avg_ticket"] if yest_row else 0,
        # the one daily baseline (rules.daily_baseline): the median of the same weekday over the 8 weeks before
        "revenue_normal": (base_day or {}).get("normal", {}).get("revenue"),
        "normal_weeks": (base_day or {}).get("normal", {}).get("n_weeks", 0),
        "revenue_vs_normal_pct": (base_day or {}).get("vs_normal_pct"),
        "normal_status": (base_day or {}).get("status"),
        "normal_label": (base_day or {}).get("label"),
        "normal_label_short": (base_day or {}).get("label_short"),
        "mtd_revenue": mtd_row["mtd_revenue"] if mtd_row else 0,
        "mtd_days": mtd_row["mtd_days"] if mtd_row else 0,
        "yesterday_by_branch": [dict(r) for r in yest_branch_rows],
        "mtd_by_branch": mtd_by_branch,
        "food_cost_pct_mtd": food_cost_pct_mtd,
        "mtd_bom_cost": mtd_bom_cost,
        "cost_health": cost_health,
    }


@router.get("/api/home/brief")
def home_brief_route(request: Request, branch: Optional[str] = Query(None)):
    """The Daily Brief for the signed-in role: the cached brief, minus the food-cost figures for a role that holds
    neither cost.analysis nor profit.pnl (PL3 - the page shows no cost sentence for it, and the payload carries none)."""
    import json as _j
    import auth as _auth
    from fastapi import Response as _R
    resp = home_brief(branch=branch)
    if _auth.request_modules(request) & {"cost.analysis", "profit.pnl"}:
        return resp
    try:
        data = _j.loads(resp.body)
    except Exception:
        return resp
    if isinstance(data, dict):
        data["food_cost_pct_mtd"] = None
        data["mtd_bom_cost"] = None
        ch = data.get("cost_health")
        data["cost_health"] = {"giveaways": ch["giveaways"]} if isinstance(ch, dict) and "giveaways" in ch else {}
    return _R(content=_j.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), media_type="application/json")


def _prev_month(m: str) -> str:
    y, mo = int(m[:4]), int(m[5:7])
    return f"{y - 1}-12" if mo == 1 else f"{y}-{mo - 1:02d}"


def _cost_health(con, month, branch, food_pct, mtd_rev, fc=None):
    """Everything the home page needs to say something TRUE about food cost.

    A bare percentage against a fixed bar told the owner nothing: the bar was an
    à-la-carte norm, so the tile was red every day, and the figure itself is a
    floor whenever dishes are uncosted. This returns the one target of this
    business, how the figure MOVED against last month (the signal worth a glance),
    how complete the costing is, and the cost work that is waiting on a human.
    Each part degrades to None on a DB that lacks its tables — never an error on
    the first screen anyone sees."""
    out = {"food_pct": food_pct, "food_cost_target": food_cost_target(con), "delta_pp": None, "prev_food_pct": None,
           "coverage_pct": None, "uncosted_plates": None, "est_food_pct": None,
           "pending_review": None, "giveaways": None, "requisition": None}
    bf = "AND b.name = :branch" if branch else ""
    p = {"m": month, "pm": _prev_month(month), **({"branch": branch} if branch else {})}
    cogs = _cogs_case("s", "sk", "c")
    # movement - PR-PP3: the month to date against the same days of last month (เทียบช่วงก่อน) and of the month a year
    # back (เทียบปีก่อน), both on ONE basis (cogs over the same table's revenue); a base with no cost source is None and
    # its reason rides along (`reasons`), so the card says "—" instead of comparing a part month with a whole one
    if fc:
        fp, fl = (fc.get("prev") or {}).get("food_cost_pct"), (fc.get("ly") or {}).get("food_cost_pct")
        out["prev_food_pct"] = round(fp, 1) if fp is not None else None
        out["ly_food_pct"] = round(fl, 1) if fl is not None else None
        out["delta_pp"] = (fc.get("change") or {}).get("prev", {}).get("food_cost_pct")
        out["ly_delta_pp"] = (fc.get("change") or {}).get("ly", {}).get("food_cost_pct")
        out["period"] = fc.get("period")
        out["reasons"] = {k: v for k, v in (fc.get("reasons") or {}).items() if v}
    else:
        # no period comparison handed in (a direct call): both whole months on ONE basis, as before PR-PP3
        try:
            rows = {r["period_month"]: r for r in con.execute(f"""
                SELECT s.period_month, SUM({cogs}) AS cg, SUM(s.subtotal_ex_vat) AS rev
                  FROM v_sales_sku_monthly s JOIN skus sk ON sk.sku_id = s.sku_id
                  LEFT JOIN sku_cost c ON c.sku_id = s.sku_id
                  JOIN branches b ON b.branch_id = s.branch_id
                 WHERE s.period_month IN (:m, :pm) AND b.name != 'ALL' {bf}
                 GROUP BY s.period_month""", p).fetchall()}
            cur, prev = rows.get(month), rows.get(p["pm"])
            if cur and prev and cur["rev"] and prev["rev"]:
                a, b_ = cur["cg"] / cur["rev"] * 100, prev["cg"] / prev["rev"] * 100
                out["prev_food_pct"], out["delta_pp"] = round(b_, 1), round(a - b_, 1)
        except Exception:
            pass
    try:
        # coverage by PLATES (buffet dishes carry ~฿0 revenue, so a revenue-weighted
        # figure always says "fine"), on the app-wide definition in rules.py
        r = con.execute(f"""
            SELECT {coverage_sums_sql('s')},
                   SUM(CASE WHEN {needs_cost_sql()} AND s.qty > 0
                            THEN s.qty * {effective_unit_cost_sql('s', 's.period_month')} ELSE 0 END) AS cg
              FROM v_sales_sku_monthly s JOIN skus sk ON sk.sku_id = s.sku_id
              JOIN branches b ON b.branch_id = s.branch_id
             WHERE s.period_month = :m AND b.name != 'ALL' {bf}
            """, p).fetchone()
        if r and r["cov_plates"]:
            cov = coverage_from_sums(r)
            out["coverage_pct"] = cov["plates_pct"]
            out["uncosted_plates"] = cov["uncosted_plates"]
            out["revenue_coverage_pct"] = cov["revenue_pct"]
            costed = r["cov_plates_costed"] or 0
            if food_pct is not None and costed and mtd_rev:
                out["est_food_pct"] = round(
                    food_pct + cov["uncosted_plates"] * (r["cg"] / costed) / mtd_rev * 100, 1)
    except Exception:
        pass
    try:
        # UNDATED ON PURPOSE: this prices the proposals awaiting confirmation at the
        # cost the proposal itself carries (sku_effective_cost), not at a released cost.
        r = con.execute("""
            SELECT COUNT(*) AS n,
                   SUM(COALESCE(e.recipe_cost, 0) * COALESCE((SELECT SUM(m.qty) FROM sales_sku_monthly m
                        WHERE m.sku_id = p.sku_id AND m.period_month = ?), 0)) AS cg
              FROM cost_provenance p LEFT JOIN sku_effective_cost e USING(sku_id)
             WHERE p.source != 'confirmed'""", (month,)).fetchone()
        if r and r["n"]:
            out["pending_review"] = {"count": r["n"], "cogs_month": round(r["cg"] or 0)}
    except Exception:
        pass
    try:
        # given away: the dish's own name says so. Costed now, so it finally has a price tag.
        rows = con.execute(f"""
            SELECT COALESCE(sk.description, sk.sku_code) AS name, SUM(s.qty) AS q,
                   SUM(s.qty * {effective_unit_cost_sql('s', 's.period_month')}) AS v
              FROM v_sales_sku_monthly s JOIN skus sk ON sk.sku_id = s.sku_id
              JOIN branches b ON b.branch_id = s.branch_id
             WHERE s.period_month = :m AND b.name != 'ALL' {bf} AND s.qty > 0
               AND (sk.description LIKE '%ฟรี%' OR sk.description LIKE '%Free%' OR sk.description LIKE '%แถม%')
               AND NOT {tier_code_sql('sk.sku_code')}
             GROUP BY sk.sku_id HAVING v > 0 ORDER BY v DESC""", p).fetchall()
        if rows:
            out["giveaways"] = {"value": round(sum(x["v"] for x in rows)),
                                "plates": round(sum(x["q"] for x in rows)),
                                "top": [{"name": x["name"], "plates": round(x["q"]), "value": round(x["v"])}
                                        for x in rows[:3]]}
    except Exception:
        pass
    try:
        last_full = _prev_month(month)
        got = [x[0] for x in con.execute("SELECT DISTINCT period_month FROM requisition_line ORDER BY 1")]
        req = {"latest_month": got[-1] if got else None,
               "missing_month": last_full if last_full not in got else None, "worst": None}
        if got:
            lm = got[-1]
            worst = None
            for bname, d, cg in con.execute(f"""
                    SELECT b.name, q.drawn,
                           (SELECT SUM({cogs}) FROM v_sales_sku_monthly s JOIN skus sk ON sk.sku_id = s.sku_id
                             LEFT JOIN sku_cost c ON c.sku_id = s.sku_id
                             WHERE s.branch_id = q.branch_id AND s.period_month = ?) AS cg
                      FROM (SELECT branch_id, SUM(value) AS drawn FROM requisition_line
                             WHERE period_month = ? GROUP BY branch_id) q
                      JOIN branches b ON b.branch_id = q.branch_id""", (lm, lm)):
                if cg and d and (worst is None or d / cg > worst["ratio"]):
                    worst = {"branch": bname, "ratio": round(d / cg, 2), "month": lm}
            req["worst"] = worst
        out["requisition"] = req
    except Exception:
        # no requisition_line table yet = nothing was ever uploaded, which is itself
        # the thing to say (the table is only created by the first upload)
        out["requisition"] = {"latest_month": None, "missing_month": _prev_month(month), "worst": None}
    return out


# ---------------------------------------------------------------------------
# /api/home/leak-watch  — daily money-leak radar for the Daily Brief
# ---------------------------------------------------------------------------
@router.get("/api/home/leak-watch")
@ttl_cache
def home_leak_watch(date: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                    group: Optional[str] = Query(None)):
    """One focus day's money-leak signals (default = latest day) for the Daily Brief:
      · heavy_discount — bills discounted >=30% of the (reconstructed) pre-discount base, incl. free bills
      · cash_heavy     — the subset paid mostly in CASH (the classic skim flag — no card/transfer trail)
      · reprinted      — discounted bills printed more than once (re-print abuse)
      · zero_gross     — discount-only / zeroed bills (gross <= 0)
    All from sales_transaction at date grain. Void/cancelled bills live in pos_cancel_bill
    (month-grain only) and are intentionally not folded in here."""
    base = f"(({net_revenue_row('st')}) + st.discount)"
    with get_db() as con:
        maxd = con.execute(
            f"SELECT DATE(MAX(sold_at)) FROM sales_transaction WHERE source='Easy' AND {closed_day_clause('sold_at')}"
        ).fetchone()[0]
        if not maxd:
            return {"date": None}
        focus = (date or maxd)[:10]
        bf = ""; p: dict = {"focus": focus}
        if branch:
            bf += " AND b.name=:branch"; p["branch"] = branch
        if group:
            bf += " AND COALESCE(b.branch_type,'master')=:grp"; p["grp"] = group
        where = f"WHERE DATE(st.sold_at)=:focus AND st.source='Easy' AND b.name!='ALL'{bf}"
        heavy = con.execute(f"""
            SELECT b.name AS branch, COUNT(*) AS bills, ROUND(SUM(st.discount)) AS baht
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            {where} AND st.discount>0 AND {base}>0 AND st.discount*1.0/{base} >= 0.30
            GROUP BY b.name ORDER BY baht DESC""", p).fetchall()
        # Resolve each heavy bill's dominant tender via an indexed (branch_id, receipt_no,
        # amount) lookup, computed in a derived table over ONLY the heavy bills (post-WHERE)
        # — not a ROW_NUMBER() window over the entire sale_payment table.
        dom_code = ("(SELECT CASE WHEN UPPER(sp.payment_code)='CASH' THEN 'Cash' ELSE sp.payment_code END "
                    "FROM sale_payment sp WHERE sp.branch_id=st.branch_id AND sp.receipt_no=st.receipt_no "
                    "ORDER BY sp.amount DESC LIMIT 1)")
        cash_heavy = con.execute(f"""
            SELECT branch, COUNT(*) AS bills, ROUND(SUM(discount)) AS baht FROM (
              SELECT b.name AS branch, st.discount AS discount, {dom_code} dc
              FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
              {where} AND st.discount>0 AND {base}>0 AND st.discount*1.0/{base} >= 0.30
            ) WHERE dc='Cash'
            GROUP BY branch ORDER BY baht DESC""", p).fetchall()
        reprint = con.execute(f"""
            SELECT b.name AS branch, COUNT(*) AS bills, ROUND(SUM(st.discount)) AS baht
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            {where} AND st.discount>0 AND COALESCE(st.print_times,0)>1
            GROUP BY b.name ORDER BY bills DESC""", p).fetchall()
        zero = con.execute(f"""
            SELECT b.name AS branch, COUNT(*) AS bills, ROUND(SUM(st.discount)) AS baht
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            {where} AND st.discount>0 AND {base} <= 0
            GROUP BY b.name ORDER BY baht DESC""", p).fetchall()
        # ALL discounted bills for the focus day — the reconciling total the abnormal
        # subsets above are carved from (ties out with the Discounts menu for the day).
        total = con.execute(f"""
            SELECT b.name AS branch, COUNT(*) AS bills, ROUND(SUM(st.discount)) AS baht
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            {where} AND st.discount>0
            GROUP BY b.name ORDER BY baht DESC""", p).fetchall()

    def pack(rows):
        L = [dict(r) for r in rows]
        return {"bills": sum(r["bills"] for r in L),
                "baht": sum(r["baht"] or 0 for r in L),
                "by_branch": L}
    return {"date": focus, "heavy_discount": pack(heavy), "cash_heavy": pack(cash_heavy),
            "reprinted": pack(reprint), "zero_gross": pack(zero), "total": pack(total)}


# ---------------------------------------------------------------------------
# /api/home/menu-movers  — yesterday's biggest menu movers UP and DOWN (by impact)
# ---------------------------------------------------------------------------
@router.get("/api/home/menu-movers")
@ttl_cache
def home_menu_movers(date: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                     group: Optional[str] = Query(None), limit: int = Query(6, ge=1, le=30),
                     min_ph: float = Query(0.5, ge=0.0), min_qty: int = Query(10, ge=1)):
    """Biggest menu movers UP and DOWN on one day, measured PER HEAD (plates per 100
    customers) vs the trailing 21-day average — owner wants per-head, not totals, because it
    strips out the traffic confound: +plates can just mean +customers, but a per-head rise
    means each customer genuinely chose it more. Ranked by absolute per-100-head change.
    A mover must clear ALL of: min_qty plates today (real presence), a meaningful ratio
    (>=1.5x up / <=0.5x down), and min_ph plates/100-head of swing (impact floor — keeps a
    slow/busy day from reading every menu as a mover, and big-%/no-impact items out).
    Qty only from sales_sku_daily; customers from sales_transaction (no cost — iron rule)."""
    import datetime as _dt
    with get_db() as con:
        maxd = con.execute(f"SELECT MAX(day) FROM sales_sku_daily WHERE {closed_day_clause('day')}").fetchone()[0]
        if not maxd:
            return {"date": None, "up": [], "down": []}
        focus = (date or maxd)[:10]
        fy, fm, fd = (int(x) for x in focus.split("-"))
        ws = (_dt.date(fy, fm, fd) - _dt.timedelta(days=21)).isoformat()
        bf = ""; p: dict = {"focus": focus, "ws": ws}
        if branch:
            bf += " AND b.name=:branch"; p["branch"] = branch
        if group:
            bf += " AND COALESCE(b.branch_type,'master')=:grp"; p["grp"] = group
        # customers per day in scope — the denominator for per-head
        today_c_row = con.execute(f"""
            SELECT SUM(st.customer_count) c
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            WHERE st.source='Easy' AND b.name!='ALL' AND DATE(st.sold_at)=:focus {bf}""", p).fetchone()
        today_c = (today_c_row["c"] if today_c_row else 0) or 0
        if not today_c:
            return {"date": focus, "up": [], "down": [], "today_customers": 0}
        p["tc"] = today_c
        rows = con.execute(f"""
            WITH cust AS (
              SELECT DATE(st.sold_at) day, SUM(st.customer_count) c
              FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
              WHERE st.source='Easy' AND b.name!='ALL'
                AND DATE(st.sold_at)>=:ws AND DATE(st.sold_at)<:focus {bf}
              GROUP BY day),
            food AS (
              SELECT sk.sku_id FROM skus sk
              JOIN categories c ON c.category_id=sk.category_id
              WHERE {_FOOD_CATEGORY_FILTER}),
            today AS (
              SELECT d.sku_id, SUM(d.qty) q, SUM(d.subtotal_ex_vat) rev
              FROM v_sales_sku_daily d JOIN branches b ON b.branch_id=d.branch_id
              WHERE d.day=:focus {bf} GROUP BY d.sku_id),
            histday AS (
              SELECT d.day, d.sku_id, SUM(d.qty) daily, SUM(d.subtotal_ex_vat) drev
              FROM v_sales_sku_daily d JOIN branches b ON b.branch_id=d.branch_id
              WHERE d.day>=:ws AND d.day<:focus {bf}
              GROUP BY d.day, d.sku_id),
            hist AS (
              SELECT hd.sku_id, AVG(hd.daily * 1.0 / cust.c) avg_ph, AVG(hd.drev) avg_rev, COUNT(*) n
              FROM histday hd JOIN cust ON cust.day=hd.day
              WHERE cust.c > 0
              GROUP BY hd.sku_id)
            SELECT s.description, s.sku_code,
                   COALESCE(t.q,0) today, COALESCE(h.avg_ph,0) avg_ph, COALESCE(h.n,0) n,
                   COALESCE(t.rev,0) rev_today, COALESCE(h.avg_rev,0) rev_avg
            FROM hist h
            LEFT JOIN today t ON t.sku_id=h.sku_id
            JOIN skus s ON s.sku_id=h.sku_id
            WHERE h.n >= 10 AND h.sku_id IN (SELECT sku_id FROM food)
            UNION
            SELECT s.description, s.sku_code, t.q today, 0 avg_ph, 0 n,
                   t.rev rev_today, 0 rev_avg
            FROM today t JOIN skus s ON s.sku_id=t.sku_id
            WHERE t.sku_id NOT IN (SELECT sku_id FROM hist WHERE n >= 10)
              AND t.sku_id IN (SELECT sku_id FROM food)
        """, p).fetchall()
        # total food revenue in scope today — denominator for the movers' revenue share
        frow = con.execute(f"""
            SELECT COALESCE(SUM(d.subtotal_ex_vat),0) rev
            FROM v_sales_sku_daily d JOIN branches b ON b.branch_id=d.branch_id
            WHERE d.day=:focus {bf}
              AND d.sku_id IN (SELECT sk.sku_id FROM skus sk
                               JOIN categories c ON c.category_id=sk.category_id
                               WHERE {_FOOD_CATEGORY_FILTER})""", p).fetchone()
        today_food_rev = (frow["rev"] if frow else 0) or 0
        # Buffet PACKAGE sales (the real revenue driver for buffet dishes, which carry
        # no line revenue): covers + revenue + avg price/cover, focus day vs trailing avg.
        bp = con.execute(f"""
            SELECT COALESCE(SUM(CASE WHEN d.day=:focus THEN d.subtotal_ex_vat END),0) rev_t,
                   COALESCE(SUM(CASE WHEN d.day=:focus THEN d.qty END),0) cov_t,
                   COALESCE(SUM(CASE WHEN d.day<:focus THEN d.subtotal_ex_vat END),0) rev_h,
                   COALESCE(SUM(CASE WHEN d.day<:focus THEN d.qty END),0) cov_h,
                   COUNT(DISTINCT CASE WHEN d.day<:focus THEN d.day END) hdays
            FROM v_sales_sku_daily d JOIN branches b ON b.branch_id=d.branch_id JOIN skus s ON s.sku_id=d.sku_id
            WHERE d.day>=:ws AND d.day<=:focus AND {tier_code_sql('s.sku_code')} {bf}""", p).fetchone()
        hd = (bp["hdays"] or 1) if bp else 1
        buf = {
            "rev_today": round(bp["rev_t"]), "rev_avg": round(bp["rev_h"] / hd),
            "covers_today": round(bp["cov_t"]), "covers_avg": round(bp["cov_h"] / hd),
            "ppc_today": round(bp["rev_t"] / bp["cov_t"]) if bp["cov_t"] else 0,
            "ppc_avg": round(bp["rev_h"] / bp["cov_h"]) if bp["cov_h"] else 0,
        } if bp else None
        # à-la-carte food revenue trailing daily average (today = today_food_rev)
        af = con.execute(f"""
            SELECT COALESCE(SUM(d.subtotal_ex_vat),0) rev, COUNT(DISTINCT d.day) hdays
            FROM v_sales_sku_daily d JOIN branches b ON b.branch_id=d.branch_id
            WHERE d.day>=:ws AND d.day<:focus {bf}
              AND d.sku_id IN (SELECT sk.sku_id FROM skus sk
                               JOIN categories c ON c.category_id=sk.category_id
                               WHERE {_FOOD_CATEGORY_FILTER})""", p).fetchone()
        ala_avg = round((af["rev"] or 0) / (af["hdays"] or 1)) if af else 0
    up, down = [], []
    for r in rows:
        today = r["today"] or 0
        avg_ph = r["avg_ph"] or 0          # avg plates per customer (trailing)
        n = r["n"] or 0
        name = (r["description"] or r["sku_code"] or "").replace("(B) ", "").replace("(A) ", "")
        today_ph = today / today_c
        ph100 = round(today_ph * 100, 2)   # plates per 100 customers today
        rev_today = round(r["rev_today"] or 0)                 # this menu's revenue today
        rev_delta = round((r["rev_today"] or 0) - (r["rev_avg"] or 0))  # vs trailing avg day
        if n < 10:
            # new / no-history item: only an UP mover, and only if it has real presence
            if today >= max(min_qty, 20):
                up.append({"menu": name, "today": round(today), "ph100": ph100, "avg_ph100": 0,
                           "delta_ph100": ph100, "pct": None, "is_new": True,
                           "rev_today": rev_today, "rev_delta": rev_today})
            continue
        if avg_ph <= 0 or today < min_qty:
            continue
        ratio = today_ph / avg_ph
        delta100 = (today_ph - avg_ph) * 100      # per-100-head swing (the impact metric)
        if delta100 >= min_ph and ratio >= 1.5:
            up.append({"menu": name, "today": round(today), "ph100": ph100,
                       "avg_ph100": round(avg_ph * 100, 2), "delta_ph100": round(delta100, 2),
                       "pct": round((ratio - 1) * 100), "is_new": False,
                       "rev_today": rev_today, "rev_delta": rev_delta})
        elif delta100 <= -min_ph and ratio <= 0.5:
            down.append({"menu": name, "today": round(today), "ph100": ph100,
                         "avg_ph100": round(avg_ph * 100, 2), "delta_ph100": round(delta100, 2),
                         "pct": round((ratio - 1) * 100), "is_new": False,
                         "rev_today": rev_today, "rev_delta": rev_delta})
    up.sort(key=lambda s: -s["delta_ph100"])
    down.sort(key=lambda s: s["delta_ph100"])
    up_lim, down_lim = up[:limit], down[:limit]
    # Revenue relationship: how the displayed menu-mix shift moved food revenue.
    rev_up = round(sum(x["rev_delta"] for x in up_lim))      # extra ฿ from rising menus
    rev_down = round(sum(x["rev_delta"] for x in down_lim))  # ฿ lost from falling menus (<=0)
    top_gain = max(up_lim, key=lambda s: s["rev_delta"], default=None)
    top_loss = min(down_lim, key=lambda s: s["rev_delta"], default=None)
    return {"date": focus, "window_days": 21, "min_ph": min_ph, "today_customers": today_c,
            "up": up_lim, "down": down_lim,
            "today_food_rev": round(today_food_rev),
            "rev_up_delta": rev_up, "rev_down_delta": rev_down, "rev_net_delta": rev_up + rev_down,
            "top_gain": top_gain, "top_loss": top_loss,
            "sales": {"buffet": buf, "alacarte_today": round(today_food_rev), "alacarte_avg": ala_avg}}
