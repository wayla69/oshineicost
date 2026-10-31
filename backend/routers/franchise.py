"""Franchise owner page "ร้านของฉัน" (PR-L, owner 2026-10-10) - GET /api/franchise/summary.

One read-only summary of ONE branch for the owner of a franchise branch (role `franchise`, module
`franchise.view`, login `own_<ตัวย่อ>`). Everything is taken from the engines the other pages already use; this
module only picks the numbers and words the owner may see - no maths of its own except the percentages named below.

  sales        Management report (reports.report_management, monthly, month-to-date), the last 30 days by one
               grouped query on the dashboard's revenue rule (rules.net_revenue_sum), and the month's top menus
               (sales.top_ordered)
  gp           the report's GP section (numbers only)
  ingredients  bom_center.variance.period (latest counted period) + issue_reports.daily (waste, streak flag)
  discipline   bom_center.issue_reports.log_monthly (ใบตัดเบิก) + bom_center.hub.compliance (ใบสั่งซื้อ, รับสินค้า)
  verdict      "ดี / ควรดู / ต้องแก้": at most 3 one-line formal Thai sentences each, from the thresholds below
  daily        (F1) one row per FINISHED day of the month (rules.closed_day_clause; the report's window ends on the last finished
               day): net sales, bills, customers, per bill and the change on the same weekday one week earlier. The same grouped
               query as the 30-day bars, on the same revenue rule (rules.net_revenue_sum: bill total / 1.07 less service charge)
  trend_12m    (F1) the last 12 closed months and the month in progress, as /api/reports/management/months draws them
               (reports.report_management_months: the report's own sums, weekday-fair MoM for the part month, the report's
               GP basis). Each month on its own with MoM / YoY - never a sum over months
  target       (F1) the month's sales target (targets.targets_for_month: a typed target wins, else the branch's own actual a
               year earlier) against the sales so far, pro-rated by the report's weekday weights while the month is open
  gp.coverage  (F1) the share of plates / sales with no cost yet, on the ONE coverage definition (rules.coverage_sums_sql:
               needs_cost_sql + costed_sql). Food cost leaves those dishes out, so it is a floor
  GET /api/franchise/insight  (F2) sales mix and busy hours, the controls (discounts / voided bills / cash, counts and baht
               only) and the guests' public reviews - see the F2 block near the end of this module; and the owner's morning
               push (franchise_morning_payload, sent by routers/push_api.maybe_send_franchise, kind "franchise")
  supply_ratio (F3) ยอดซื้อเทียบยอดขาย of the month and of last month (_purchase_ratio, see the F3 block at the end of this module)
  GET /api/franchise/supply   (F3) วัตถุดิบจากส่วนกลาง: delivery rounds, purchases against sales, claims, late orders, over-use
  GET /api/franchise/daily    สรุปประจำวัน (owner 2026-10-27): the branch's last FINISHED day (rules.latest_settled_day) and its
               month so far - see the block at the end of this module
  GET /api/franchise/overview (phone B, 2026-10-31) summary + insight + เรื่องที่ควรดู in one answer and the ควรดู findings with the
               detail each one goes to (franchise_findings) - see the block at the end of this module
  GET /api/franchise/summary.xlsx  (F1) the same month as an Excel file: สรุป / รายวัน / 12 เดือน / เมนูขายดี (F3: + วัตถุดิบ). The Fly image has
               no Chrome, so no server PDF; the page has a print stylesheet (A4 portrait) for a paper copy

Who sees which branch: the branch comes from the signed session (auth.scope_branch). A `branch_id` that names another
branch is refused with 403 `X-Denied: role`. A franchise login whose account carries no branch is refused too (it
would otherwise look like the centre, which may name any branch). A central login (management) must name `branch_id`.

Never in the payload: bills, recipes, prices per ingredient, other branches, staff or cashier names, accounting
exports, settings. A test (test_franchise_role.py) scans the payload for those words and keys.

Definitions that are not a plain copy of an engine's field
  mtd_vs_last_month_pct   report pace.mom_pct: month-to-date against last month on the SAME elapsed days, weighted by
                          weekday while the month is still open (mom_basis 'dow'); a complete month: against the full month
  mtd_vs_last_year_pct    report headline.rev_yoy_pct (same dates a year earlier; null before the history is long enough)
  gp.target_pct           the dashboard's standing target rule (targets.py: the branch's own actual in the same month a
                          year earlier; a typed target wins). targets.py carries revenue and customer targets only, no
                          GP %, so the rule is applied to GP %: the branch's GP % of targets.prev_year_month(month)
                          (target_basis 'last_year'), and only when that month has no costed data the GP % of the month
                          before (report gp.gp_pct_prev, target_basis 'prev_month'). The page labels it
                          "เป้า = เดือนเดียวกันปีก่อน" / "เดือนก่อน"
  gp.food_cost_pct        100 - gp_pct: food cost over net sales on the same basis as the GP shown beside it (a floor
                          while some dishes have no costed recipe)
  ingredients.diff_pct    variance.period gap_pct: actual use (begin + received - end count) over the use the sales imply,
                          for the latest period whose end count falls in or before the month; diff_value in baht
  ingredients.waste_pct_month  waste baht over issued baht (used + waste) of the month's confirmed slips
  ingredients.streak_flag      some trading days in a row with issues far over the BoM (issue_reports.STREAK_DAYS)
  discipline.slip_coverage_pct  confirmed slips over trading days; slip_on_time_pct = confirmed on the day over confirmed
  discipline.orders_on_time_pct purchase orders submitted by their cutoff over the cycles the calendar expected so far
  discipline.receipts_on_time   orders whose delivery day has passed and that the branch received by the day after delivery,
                                over the orders processed with a delivery day in the month that has passed
  discipline.claims_month       claims raised in the month (not cancelled)
  discount_pct            report headline.discount_pct (discount over sales); policy ceiling headline.discount_policy_pct
  daily[].vs_normal_pct  net sales of the day against its normal ("ปกติ", rules.daily_baseline: the median of the same weekday
                          over the 8 weeks before, days without bills skipped); null with fewer than 2 such weeks (normal_weeks)
  target.to_date          month target x the elapsed share of the month. While the month is open the share is weighted by
                          weekday exactly as the report's projection is (pace.proj_method 'dow': mtd / projected); without
                          that, elapsed days / days in the month; a complete month: the full target
  target.vs_to_date_pct   sales so far over target.to_date - 100 ; target.of_month_pct = sales so far over the month target

Verdict thresholds (module constants, one place to tune; a value that is null is skipped, never judged)
  sales vs last month   >= +5 good ; <= -3 watch ; <= -10 fix          sales vs last year  >= +5 good ; <= -10 watch
  GP vs reference       >= +1 pt good ; <= -1 pt watch ; <= -3 pt fix
  ingredient |diff|     <= 3 % good ; > 3 % watch ; > 8 % fix           waste share  <= 2 % good ; > 4 % watch ; > 8 % fix
  slip coverage         >= 95 % good ; < 80 % watch ; < 50 % fix        slip on time  < 70 % watch
  orders on time        >= 90 % good ; < 80 % watch ; < 60 % fix        receipts on time < 80 % watch
  claims in the month   >= 3 watch
  purchases / sales     (F3) up by more than T_PURCHASE_RISE_WATCH points on last month -> watch (an open month from
                        T_PURCHASE_MIN_DAYS finished days)
  discount              <= policy good ; > policy watch ; > 2 x policy fix
"""
import calendar as _cal
import json
import re
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

import auth as _auth
from core import get_db, ttl_cache

router = APIRouter()

MONTH_RE = r"^\d{4}-(0[1-9]|1[0-2])$"

# ── verdict thresholds (see the module docstring) ────────────────────────────
T_SALES_MOM_GOOD, T_SALES_MOM_WATCH, T_SALES_MOM_FIX = 5.0, -3.0, -10.0
T_SALES_YOY_GOOD, T_SALES_YOY_WATCH = 5.0, -10.0
T_GP_GOOD, T_GP_WATCH, T_GP_FIX = 1.0, -1.0, -3.0
T_ING_GOOD, T_ING_FIX = 3.0, 8.0
T_WASTE_GOOD, T_WASTE_WATCH, T_WASTE_FIX = 2.0, 4.0, 8.0
T_SLIP_GOOD, T_SLIP_WATCH, T_SLIP_FIX = 95.0, 80.0, 50.0
T_SLIP_ONTIME_WATCH = 70.0
T_ORD_GOOD, T_ORD_WATCH, T_ORD_FIX = 90.0, 80.0, 60.0
T_RECV_WATCH = 80.0
T_CLAIMS_WATCH = 3
DEFAULT_DISCOUNT_POLICY = 2.5
VERDICT_MAX = 3


def _num(x, nd=1):
    return None if x is None else round(float(x), nd)


def _month_bounds(month: str):
    y, m = int(month[:4]), int(month[5:7])
    return date(y, m, 1), date(y, m, _cal.monthrange(y, m)[1])


def _safe(label, fn, default=None):
    """A section that fails must not take the page down: log it and answer with the empty value."""
    try:
        return fn()
    except Exception as e:      # pragma: no cover - degraded, not broken
        print(f"[franchise] {label} unavailable: {e}")
        return default


# ── who is asking ────────────────────────────────────────────────────────────
def _resolve_branch(con, request: Request, branch_id: Optional[int]):
    """-> (branch_id, branch name). Pinned session: its own branch only. Refuses a franchise login with no branch."""
    payload = _auth.parse_token(request.cookies.get(_auth.SESSION_COOKIE, "")) if _auth.auth_enabled() else None
    scoped = _auth.scope_branch(request)
    if payload and payload.get("r") == "franchise" and not scoped:
        raise HTTPException(403, detail="บัญชีนี้ยังไม่ได้ผูกกับสาขา แจ้งผู้ดูแลระบบ", headers={"X-Denied": "role"})
    if scoped:
        r = con.execute("SELECT branch_id, name FROM branches WHERE name=?", (scoped,)).fetchone()
        if not r:
            raise HTTPException(403, detail="บัญชีนี้ผูกกับสาขาที่ไม่พบในระบบ แจ้งผู้ดูแลระบบ", headers={"X-Denied": "role"})
        if branch_id is not None and int(branch_id) != int(r[0]):
            raise HTTPException(403, detail="บัญชีเจ้าของสาขาดูได้เฉพาะสาขาของตนเอง", headers={"X-Denied": "role"})
        return int(r[0]), r[1]
    if branch_id is None:
        raise HTTPException(400, "เลือกสาขา")
    r = con.execute("SELECT branch_id, name FROM branches WHERE branch_id=? AND name <> 'ALL'", (branch_id,)).fetchone()
    if not r:
        raise HTTPException(404, "ไม่พบสาขา")
    return int(r[0]), r[1]


# ── sections ─────────────────────────────────────────────────────────────────
def _report(branch: str, month: Optional[str]) -> dict:
    from .reports import report_management
    raw = report_management(period="monthly", date=None, week=None, month=month, branch=branch, group=None,
                            lang="th", mtd="1")
    return json.loads(raw.body)


def _sales_and_gp(rep: dict, branch: str):
    """-> (sales, gp, discount_pct, discount_policy, last_day_date)"""
    if not rep.get("available"):
        return None, None, None, None, None
    H, G = rep.get("headline") or {}, rep.get("gp") or {}
    last = (rep.get("range") or [None, None])[1]
    sales = {"mtd": _num(H.get("revenue"), 2), "bills": H.get("bills"), "per_bill": _num(H.get("avg_ticket"), 0),
             # PR-PP2: the headline change is the calendar to-date rule (days 1..n vs days 1..n of last month);
             # the weekday-fair figure stays on the target pace (P)
             "mtd_vs_last_month_pct": _num(H.get("rev_wow_pct")),
             "mom_basis": H.get("mom_basis") or ("to_date" if rep.get("partial") else "month"),
             "mtd_vs_last_year_pct": _num(H.get("rev_yoy_pct")), "customers": H.get("customers"),
             "month_complete": not rep.get("partial"), "through": last, "days": rep.get("days_in_period"),
             "last_day": None, "series_30d": []}
    gp_pct = G.get("gp_pct")
    gp = {"gp_pct": _num(gp_pct), "target_pct": _num(G.get("gp_pct_prev")), "target_basis": "prev_month",
          "food_cost_pct": _num(100.0 - gp_pct) if gp_pct is not None else None}
    return sales, gp, _num(H.get("discount_pct")), H.get("discount_policy_pct"), last


def _gp_target(gp: dict, branch: str, month: str) -> None:
    """The dashboard's standing target rule (targets.py): a branch's target is its own actual in the same month a year
    earlier. targets.py holds revenue and customer targets only, so for GP % the same rule is applied here: the GP % of
    the branch's report for targets.prev_year_month(month) (target_basis 'last_year'); only when that month has no
    costed data does the previous month's GP % stay (target_basis 'prev_month', set in _sales_and_gp)."""
    from targets import prev_year_month
    want = prev_year_month(month)
    ly = _safe("gp last year", lambda: _report(branch, want), {}) or {}
    # the report falls back to another month when the one asked for has no data: only the month itself counts
    ok = ly.get("available") and ly.get("selected_month") == want and ((ly.get("range") or [""])[0])[:7] == want
    pct = (ly.get("gp") or {}).get("gp_pct") if ok else None
    if pct is not None:
        gp["target_pct"], gp["target_basis"] = _num(pct), "last_year"


TH_DOW = ("จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์")    # date.weekday() order


def _daily(con, sales: dict, bid: int, last_day: Optional[str]):
    """The last day of the report's window, the 30 days ending on it and (F1) every day of the month up to it, from ONE
    grouped query on the dashboard's own revenue rule (rules.net_revenue_sum: bill total / 1.07 less service charge).
    Only FINISHED days (rules.closed_day_clause): the report already ends its month-to-date window on the last finished
    day, and the clause keeps the day still trading out even when a caller passes a later day. A day without bills is null."""
    from rules import BASELINE_WEEKS, baseline_from_days, closed_day_clause, net_revenue_sum
    if not last_day:
        return
    end = date.fromisoformat(last_day)
    first = end.replace(day=1)
    # the table's first day compares with its normal: the same weekday over the 8 weeks before (rules.daily_baseline)
    start = min(end - timedelta(days=29), first - timedelta(days=7 * BASELINE_WEEKS))
    rows = {r[0]: (r[1], r[2], r[3]) for r in con.execute(
        f"SELECT date(t.sold_at), {net_revenue_sum('t')}, COUNT(*), COALESCE(SUM(t.customer_count),0) "
        f"FROM sales_transaction t WHERE t.branch_id=:bid AND t.source='Easy' "
        f"AND t.sold_at >= :s AND t.sold_at < :e AND {closed_day_clause('t.sold_at')} GROUP BY date(t.sold_at)",
        {"bid": bid, "s": start.isoformat(), "e": (end + timedelta(days=1)).isoformat()})}

    def rev(d):
        return _num(rows[d][0], 2) if d in rows else None

    s30 = end - timedelta(days=29)
    sales["series_30d"] = [{"day": (s30 + timedelta(days=i)).isoformat(), "revenue": rev((s30 + timedelta(days=i)).isoformat())}
                           for i in range(30)]
    sales["last_day"] = {"date": last_day, "revenue": rev(last_day) if last_day in rows else 0.0,
                         "bills": rows[last_day][1] if last_day in rows else 0}
    by_day = {d: {"rev": r[0] or 0, "bills": r[1] or 0, "cust": r[2] or 0, "disc": 0} for d, r in rows.items()}
    table = []
    for i in range((end - first).days + 1):
        dd = first + timedelta(days=i)
        d = dd.isoformat()
        r = rows.get(d)
        cur = rev(d)
        bl = baseline_from_days(by_day, d)
        table.append({"date": d, "weekday": TH_DOW[dd.weekday()], "revenue": cur,
                      "bills": r[1] if r else None, "customers": int(r[2]) if r else None,
                      "per_bill": _num(r[0] / r[1], 0) if r and r[1] else None,
                      "vs_normal_pct": bl["vs_normal_pct"] if cur is not None else None,
                      "normal_weeks": bl["normal"]["n_weeks"]})
    sales["daily"] = table
    sales["daily_through"] = table[-1]["date"] if table else None


def _trend_12m(branch: str) -> dict:
    """The last 12 closed months and the month in progress, each on its own (never a sum over months), from the phone's
    month-by-month series (reports.report_management_months): the report's sums, its to-date MoM for the part month
    (PR-PP2: days 1..n against days 1..n of last month) and its GP basis, so a month reads here as it does in the
    management report."""
    from .reports import report_management_months
    raw = report_management_months(branch=branch, group=None, lang="th", closed=12)
    d = json.loads(raw.body) if hasattr(raw, "body") else raw
    if not d.get("available"):
        return {"months": [], "latest": None}
    out = []
    for m in d.get("months") or []:
        mom, yoy = m.get("mom") or {}, m.get("yoy") or {}
        bills = m.get("bills") or 0
        out.append({"month": m["month"], "label": m.get("label"), "partial": bool(m.get("mtd") or m.get("partial")),
                    "through": m.get("through"), "days": m.get("days"),
                    "revenue": _num(m.get("revenue"), 2), "customers": m.get("customers"), "bills": bills,
                    "per_bill": _num(m["revenue"] / bills, 0) if bills and m.get("revenue") is not None else None,
                    "gp_pct": _num(m.get("gp_pct")),
                    "mom_pct": _num(mom.get("revenue")), "mom_basis": mom.get("basis"),
                    "mom_customers_pct": _num(mom.get("customers")), "mom_gp_pp": _num(mom.get("gp_pp")),
                    "yoy_pct": _num(yoy.get("revenue")) if yoy else None})
    return {"months": out, "latest": out[-1] if out else None}


def _target(con, rep: dict, sales: dict, branch: str, month: str) -> dict:
    """The month's sales target (targets.py: a typed target wins, else the branch's own actual a year earlier) against the
    sales so far. While the month is open the target to date follows the report's weekday weights (its projection), so a
    month that has had fewer weekends so far is not judged behind; a complete month is judged against the full target."""
    from targets import targets_for_month
    rows = targets_for_month(con, month, branch=branch)
    t = rows[0] if rows else {"source": "none", "revenue_target": 0, "basis_month": None}
    out = {"source": t["source"], "basis_month": t.get("basis_month"), "month_target": None, "to_date": None,
           "mtd": sales.get("mtd"), "vs_to_date_pct": None, "of_month_pct": None, "elapsed_share_pct": None,
           "weighting": None, "month_complete": bool(sales.get("month_complete"))}
    tgt = float(t.get("revenue_target") or 0)
    if t["source"] == "none" or tgt <= 0:
        out["source"] = "none"
        return out
    P = rep.get("pace") or {}
    if sales.get("month_complete"):
        share, how = 1.0, "month"
    elif P.get("proj_method") == "dow" and P.get("projected") and P.get("mtd_revenue"):
        share, how = min(1.0, float(P["mtd_revenue"]) / float(P["projected"])), "dow"
    elif P.get("days_total"):
        share, how = min(1.0, float(P.get("days_elapsed") or 0) / float(P["days_total"])), "days"
    else:
        share, how = 1.0, "month"
    mtd = float(sales.get("mtd") or 0)
    to_date = tgt * share
    out.update({"month_target": round(tgt, 0), "to_date": round(to_date, 0), "elapsed_share_pct": _num(100.0 * share),
                "weighting": how, "vs_to_date_pct": _num(100.0 * (mtd - to_date) / to_date) if to_date else None,
                "of_month_pct": _num(100.0 * mtd / tgt)})
    # phone B (2026-10-31): the เป้า card's run rate - what each day left must sell to reach the month target, against what a day has
    # sold so far (management's pace.run_rate rule: need_per_day <= 0 = ถึงเป้าแล้ว)
    left = int(P.get("days_total") or 0) - int(P.get("days_elapsed") or 0)
    if not sales.get("month_complete") and left > 0:
        done = int(P.get("days_elapsed") or 0)
        out.update({"days_left": left, "need_per_day": round((tgt - mtd) / left, 0),
                    "normal_per_day": round(mtd / done, 0) if done else None})
    return out


def _coverage(con, bid: int, month: str) -> Optional[dict]:
    """The share of the month's plates and sales that should carry a cost and do not yet, on the ONE coverage definition
    (rules.coverage_sums_sql = needs_cost_sql + costed_sql, the definition home, P&L and the coverage page use)."""
    from rules import coverage_from_sums, coverage_sums_sql
    r = con.execute(
        f"SELECT {coverage_sums_sql('s')} FROM v_sales_sku_monthly s JOIN skus sk ON sk.sku_id = s.sku_id "
        f"WHERE s.branch_id = :bid AND s.period_month = :m", {"bid": bid, "m": month}).fetchone()
    if not r or not r["cov_plates"]:
        return None
    cov = coverage_from_sums(r)
    pp, rp = cov.get("plates_pct"), cov.get("revenue_pct")
    return {"uncosted_plates_pct": _num(100.0 - pp) if pp is not None else None,
            "uncosted_sales_pct": _num(100.0 - rp) if rp is not None else None,
            "floor": bool((pp is not None and pp < 100) or (rp is not None and rp < 100))}


def _top_menus(branch: str, month: str) -> list:
    from .sales import top_ordered
    raw = top_ordered(month=month, month_from=None, month_to=None, branch=branch, group=None, sort="qty", limit=10)
    d = json.loads(raw.body)
    rows = d.get("items") or d.get("rows") or d.get("top") or (d if isinstance(d, list) else [])
    out = []
    for x in rows[:10]:
        nm = re.sub(r"^\((?:A|B)\)\s*", "", str(x.get("description") or x.get("name") or ""))
        out.append({"name": nm, "plates": _num(x.get("qty"), 0)})
    return out


def _stock_reconcile(con, bid: int, month: str):
    """F5: the branch's latest month-end period that ends in or before the month, reconciled the way its สิ้นเดือน variance tab
    is (issue_reports.reconcile: the system stock against the count). None when the branch has no such period."""
    from bom_center import issue_reports as IR
    from bom_center.stock import periods
    _, last = _month_bounds(month)
    cut = (last + timedelta(days=1)).isoformat()
    ps = [p for p in periods(con, bid) if p[1]["date"] < cut]
    if not ps:
        return None
    return IR.reconcile(con, bid, ps[-1][1]["count_id"])


def _ingredients(con, bid: int, month: str) -> dict:
    from bom_center import issue_reports as IR
    from bom_center import variance as VA
    from bom_center.stock import periods
    first, last = _month_bounds(month)
    from bom_center.close_settings import stock_mode
    out = {"latest_period": None, "waste_pct_month": None, "streak_flag": False, "streak_days": 0,
           "stock_mode": bool(stock_mode(con))}      # P5: the streak is judged against the branch's normal (stock mode) or the recipes (full mode)
    cut = (last + timedelta(days=1)).isoformat()
    ps = [p for p in periods(con, bid) if p[1]["date"] < cut]
    if out["stock_mode"]:
        # F5: no recipes in stock mode - the gap is the system stock against the count, as a share of what the period issued
        rec = _safe("stock reconcile", lambda: _stock_reconcile(con, bid, month))
        if rec:
            issued = sum((x["used"] + x["waste"]) * x["price_per_base"] for x in rec["rows"] if x.get("price_per_base") is not None)
            dv = rec["totals"]["diff_value"]
            out["latest_period"] = {"begin": rec["period"]["begin_date"], "end": rec["period"]["end_date"],
                                    "diff_pct": _num(100.0 * dv / issued) if issued > 0 else None,
                                    "diff_value": _num(dv, 0), "reconciled": bool(rec["status"]["receipts_confirmed"])}
    elif ps:
        v = VA.period(con, bid, ps[-1][1]["count_id"], summary_only=True)
        out["latest_period"] = {"begin": v["begin_date"], "end": v["end_date"], "diff_pct": _num(v.get("gap_pct")),
                                "diff_value": _num(v.get("gap_value"), 0), "reconciled": bool(v.get("receipts_confirmed"))}
    d = IR.daily(con, bid, month)
    t = d["totals"]
    if t["issued_value"]:
        out["waste_pct_month"] = _num(100.0 * t["waste_value"] / t["issued_value"])
    out["streak_days"] = int(t.get("flagged_days") or 0)
    out["streak_flag"] = out["streak_days"] > 0
    return out


def _discipline(con, bid: int, month: str) -> dict:
    from bom_center import hub
    from bom_center import issue_reports as IR
    from bom_center.clock import today as bkk_today
    out = {"module_active": False, "slip_coverage_pct": None, "slip_on_time_pct": None, "orders_on_time_pct": None,
           "receipts_on_time": None, "claims_month": None}
    if bid not in hub._phase2_branches(con):
        return out                              # the branch has not started counting or ordering: nothing to judge
    out["module_active"] = True
    first, last = _month_bounds(month)
    s = (IR.log_monthly(con, month, bid)["summary"] or [{}])[0]
    out["slip_coverage_pct"] = _num(s.get("coverage_pct"))
    out["slip_on_time_pct"] = _num(s.get("on_time_pct"))
    out["claims_month"] = s.get("claims", 0)
    comp = hub.compliance(con, first.isoformat(), last.isoformat(), bid)
    today = bkk_today().isoformat()
    past = [r for r in comp["rows"] if r["status"] != "upcoming"]
    if past:
        out["orders_on_time_pct"] = _num(100.0 * sum(1 for r in past if r.get("submitted_on_time")) / len(past))
    due = [r for r in comp["rows"] if r.get("order_id") and r.get("order_status") in ("processed", "received")
           and r.get("delivery") and r["delivery"] < today]
    if due:
        got = {oid: ts for oid, ts in con.execute(
            "SELECT order_id, received_at FROM bc_order WHERE order_id IN (%s)" % ",".join("?" * len(due)),
            [r["order_id"] for r in due])}
        ok = 0
        for r in due:
            at = hub._bkk(got.get(r["order_id"]))
            if at is not None and at.date() <= date.fromisoformat(r["delivery"]) + timedelta(days=1):
                ok += 1
        out["receipts_on_time"] = _num(100.0 * ok / len(due))
    return out


# ── the verdict ──────────────────────────────────────────────────────────────
def _pct_text(x):
    return f"{abs(x):.1f}".rstrip("0").rstrip(".") if x is not None else "-"


def _verdict(sales, gp, ing, disc, discount_pct, policy, supply=None) -> dict:
    rules = []          # (level, text), in the order the owner reads them

    def add(level, text):
        rules.append((level, text))

    if sales:
        m = sales.get("mtd_vs_last_month_pct")
        same = "เทียบช่วงวันเดียวกัน" if not sales.get("month_complete") else "เทียบทั้งเดือน"
        noun = "ยอดขายทั้งเดือน" if sales.get("month_complete") else "ยอดขายสะสม"
        if m is not None:
            if m >= T_SALES_MOM_GOOD:
                add("good", f"{noun}สูงกว่าเดือนก่อนร้อยละ {_pct_text(m)} ({same})")
            elif m <= T_SALES_MOM_FIX:
                add("fix", f"{noun}ต่ำกว่าเดือนก่อนร้อยละ {_pct_text(m)} ({same}) ควรตรวจสอบสาเหตุ")
            elif m <= T_SALES_MOM_WATCH:
                add("watch", f"{noun}ต่ำกว่าเดือนก่อนร้อยละ {_pct_text(m)} ({same})")
        y = sales.get("mtd_vs_last_year_pct")
        if y is not None:
            if y >= T_SALES_YOY_GOOD:
                add("good", f"{noun}สูงกว่าช่วงเดียวกันของปีก่อนร้อยละ {_pct_text(y)}")
            elif y <= T_SALES_YOY_WATCH:
                add("watch", f"{noun}ต่ำกว่าช่วงเดียวกันของปีก่อนร้อยละ {_pct_text(y)}")
    if gp and gp.get("gp_pct") is not None and gp.get("target_pct") is not None:
        d = gp["gp_pct"] - gp["target_pct"]
        ref = "เดือนเดียวกันของปีก่อน" if gp.get("target_basis") == "last_year" else "เดือนก่อน"
        if d >= T_GP_GOOD:
            add("good", f"กำไรขั้นต้นร้อยละ {_pct_text(gp['gp_pct'])} สูงกว่า{ref} {_pct_text(d)} จุด")
        elif d <= T_GP_FIX:
            add("fix", f"กำไรขั้นต้นร้อยละ {_pct_text(gp['gp_pct'])} ต่ำกว่า{ref} {_pct_text(d)} จุด ควรตรวจสอบต้นทุนอาหาร")
        elif d <= T_GP_WATCH:
            add("watch", f"กำไรขั้นต้นร้อยละ {_pct_text(gp['gp_pct'])} ต่ำกว่า{ref} {_pct_text(d)} จุด")
    if ing:
        lp = ing.get("latest_period")
        if lp and lp.get("diff_pct") is not None:
            p = lp["diff_pct"]
            stk = bool(ing.get("stock_mode"))
            word = ("นับได้มากกว่าระบบ" if p > 0 else "นับได้น้อยกว่าระบบ") if stk else ("ใช้เกินสูตร" if p > 0 else "ใช้น้อยกว่าสูตร")
            if abs(p) <= T_ING_GOOD:
                add("good", f"วัตถุดิบรอบนับล่าสุดตรงกับระบบ (ส่วนต่างร้อยละ {_pct_text(p)})" if stk else
                    f"การใช้วัตถุดิบรอบนับล่าสุดใกล้เคียงสูตร (ส่วนต่างร้อยละ {_pct_text(p)})")
            elif abs(p) > T_ING_FIX:
                add("fix", f"วัตถุดิบรอบนับล่าสุด{word}ร้อยละ {_pct_text(p)} (฿{abs(lp.get('diff_value') or 0):,.0f}) ควรตรวจสอบหน้างาน")
            else:
                add("watch", f"วัตถุดิบรอบนับล่าสุด{word}ร้อยละ {_pct_text(p)} (฿{abs(lp.get('diff_value') or 0):,.0f})")
        if ing.get("streak_flag"):
            from bom_center.fmt import comparator_th
            base = comparator_th(bool(ing.get("stock_mode")))
            add("fix", f"มีวัตถุดิบหลักที่เบิกเกิน{base}ติดต่อกันหลายวัน ({ing['streak_days']} วันในเดือนนี้) ควรตรวจสอบหน้างาน")
        w = ing.get("waste_pct_month")
        if w is not None:
            if w <= T_WASTE_GOOD:
                add("good", f"ของเสียร้อยละ {_pct_text(w)} ของมูลค่าวัตถุดิบที่เบิกใช้ในเดือนนี้")
            elif w > T_WASTE_FIX:
                add("fix", f"ของเสียร้อยละ {_pct_text(w)} ของมูลค่าวัตถุดิบที่เบิกใช้ในเดือนนี้ ควรตรวจสอบสาเหตุ")
            elif w > T_WASTE_WATCH:
                add("watch", f"ของเสียร้อยละ {_pct_text(w)} ของมูลค่าวัตถุดิบที่เบิกใช้ในเดือนนี้")
    if disc and disc.get("module_active"):
        c = disc.get("slip_coverage_pct")
        if c is not None:
            if c >= T_SLIP_GOOD:
                add("good", f"ใบตัดเบิกยืนยันครบร้อยละ {_pct_text(c)} ของวันที่เปิดขาย")
            elif c < T_SLIP_FIX:
                add("fix", f"ใบตัดเบิกยืนยันเพียงร้อยละ {_pct_text(c)} ของวันที่เปิดขาย ควรให้ทีมสาขายืนยันทุกวัน")
            elif c < T_SLIP_WATCH:
                add("watch", f"ใบตัดเบิกยืนยันร้อยละ {_pct_text(c)} ของวันที่เปิดขาย")
        s = disc.get("slip_on_time_pct")
        if s is not None and s < T_SLIP_ONTIME_WATCH:
            add("watch", f"ใบตัดเบิกยืนยันตรงวันเพียงร้อยละ {_pct_text(s)} ของใบที่ยืนยันแล้ว")
        o = disc.get("orders_on_time_pct")
        if o is not None:
            if o >= T_ORD_GOOD:
                add("good", f"ใบสั่งซื้อส่งทันเวลาตัดรอบร้อยละ {_pct_text(o)}")
            elif o < T_ORD_FIX:
                add("fix", f"ใบสั่งซื้อส่งทันเวลาตัดรอบเพียงร้อยละ {_pct_text(o)} การส่งช้ามีค่าปรับ ควรแจ้งทีมสาขา")
            elif o < T_ORD_WATCH:
                add("watch", f"ใบสั่งซื้อส่งทันเวลาตัดรอบร้อยละ {_pct_text(o)}")
        r = disc.get("receipts_on_time")
        if r is not None and r < T_RECV_WATCH:
            add("watch", f"การบันทึกรับสินค้าทันกำหนดร้อยละ {_pct_text(r)} ควรบันทึกรับสินค้าภายในวันถัดจากวันส่ง")
        n = disc.get("claims_month")
        if n is not None and n >= T_CLAIMS_WATCH:
            add("watch", f"มีการแจ้งเคลมสินค้า {n} ครั้งในเดือนนี้")
    sv = _supply_verdict(supply)                       # F3: purchases from the centre against sales, vs last month
    if sv:
        add(*sv)
    if discount_pct is not None:
        pol = float(policy) if policy else DEFAULT_DISCOUNT_POLICY
        if discount_pct <= pol:
            add("good", f"ส่วนลดร้อยละ {_pct_text(discount_pct)} ของยอดขาย อยู่ในเพดานร้อยละ {_pct_text(pol)}")
        elif discount_pct > 2 * pol:
            add("fix", f"ส่วนลดร้อยละ {_pct_text(discount_pct)} ของยอดขาย เกินเพดานร้อยละ {_pct_text(pol)} ควรตรวจสอบการให้ส่วนลด")
        else:
            add("watch", f"ส่วนลดร้อยละ {_pct_text(discount_pct)} ของยอดขาย เกินเพดานร้อยละ {_pct_text(pol)}")
    out = {lvl: [t for l, t in rules if l == lvl][:VERDICT_MAX] for lvl in ("good", "watch", "fix")}
    out["note"] = "" if rules else "ยังไม่มีข้อมูลเพียงพอสำหรับสรุปผลของเดือนนี้"
    return out


# ── the endpoint ─────────────────────────────────────────────────────────────
def franchise_summary_data(branch_id: int, month: Optional[str] = None) -> dict:
    """The summary of one branch for one month. NOT wrapped in core.ttl_cache itself: the report and the top menus it
    calls are cached (5 minutes) and a cached call inside a cached call waits for the one heavy slot it already holds."""
    from bom_center.schema import ensure as _bc_ensure
    with get_db() as con:
        _bc_ensure(con)
        r = con.execute("SELECT name FROM branches WHERE branch_id=?", (branch_id,)).fetchone()
        name = r[0]
        rep = _safe("report", lambda: _report(name, month), {}) or {}
        sales, gp, discount_pct, policy, last_day = _sales_and_gp(rep, name)
        sel = rep.get("selected_month") or month
        if sales is not None:
            _safe("daily", lambda: _daily(con, sales, branch_id, last_day))
        if gp and gp.get("gp_pct") is not None and sel:
            _gp_target(gp, name, sel)
        if gp is not None and sel:
            gp["coverage"] = _safe("coverage", lambda: _coverage(con, branch_id, sel))
        if gp is not None:
            from rules import food_cost_target
            gp["food_cost_target_pct"] = food_cost_target(con)       # the ONE target (owner 2026-10-25: <= 58 % ในเป้า)
        target = _safe("target", lambda: _target(con, rep, sales, name, sel)) if sales is not None and sel else None
        empty = {"months": [], "latest": None}
        trend = _safe("trend", lambda: _trend_12m(name), empty) if sales is not None else empty
        top = _safe("top menus", lambda: _top_menus(name, sel), []) if sel and sales is not None else []
        ing = _safe("ingredients", lambda: _ingredients(con, branch_id, sel), None) if sel else None
        disc = _safe("discipline", lambda: _discipline(con, branch_id, sel), None) if sel else None
        supply = None
        if sel and sales is not None and _safe("supply start", lambda: _bc_started(con, branch_id, sel)[0], False):
            supply = _safe("supply ratio", lambda: _purchase_ratio(con, branch_id, name, sel, rep))
    months = [{"month": m["month"], "label": m.get("label"), "mtd": bool(m.get("mtd"))} for m in (rep.get("months") or [])]
    return {"branch": name, "month": sel, "label": rep.get("label"), "months": months,
            "available": bool(sales), "sales": sales, "gp": gp, "ingredients": ing, "discipline": disc,
            "top_menus": top, "discount_pct": discount_pct, "target": target, "trend_12m": trend,
            "supply_ratio": supply, "verdict": _verdict(sales, gp, ing, disc, discount_pct, policy, supply=supply)}


@router.get("/api/franchise/summary")
def franchise_summary(request: Request, month: Optional[str] = Query(None, pattern=MONTH_RE),
                      branch_id: Optional[int] = Query(None)):
    """ร้านของฉัน: one branch's month at a glance for its owner (see the module docstring)."""
    with get_db() as con:
        bid, _ = _resolve_branch(con, request, branch_id)
    return franchise_summary_data(bid, month)


# ── F1: the month as an Excel file ───────────────────────────────────────────
# The Fly image (Dockerfile: python:3.12-slim, no Chrome / Playwright) cannot render a PDF on the server, so the download
# is a workbook; a paper copy comes from the page itself (print stylesheet in index.html, A4 portrait). Every figure is
# the summary's own (franchise_summary_data), so the file and the page never disagree; no other branch, no chain figure.
_FX_BAHT, _FX_PCT = "#,##0", "0.0"
_TARGET_SRC = {"manual": "เป้าที่กำหนดไว้", "last_year": "ยอดขายเดือนเดียวกันของปีก่อน", "none": "ไม่มีเป้า"}


def _xl_rows(ws, rows, fmts):
    for r in rows:
        ws.append(list(r))
        for i, f in enumerate(fmts):
            if f:
                ws.cell(row=ws.max_row, column=i + 1).number_format = f


def franchise_workbook(d: dict, supply: Optional[dict] = None):
    """-> openpyxl Workbook with the sheets สรุป / รายวัน / 12 เดือน / เมนูขายดี for one summary payload, and (F3) วัตถุดิบ from
    the supply payload of the same month (franchise_supply_data) when one is given."""
    import openpyxl
    from openpyxl.styles import Font
    from .bom_center import _sheet
    wb = openpyxl.Workbook()
    s, g, t = d.get("sales") or {}, d.get("gp") or {}, d.get("target") or {}
    cov = g.get("coverage") or {}
    through = s.get("through") or s.get("daily_through")
    head = f"ร้านของฉัน · สาขา{d.get('branch') or ''} · {d.get('label') or d.get('month') or ''}"

    ws = wb.active
    _sheet(ws, "สรุป", ["หัวข้อ", "ค่า", "หมายเหตุ"], head)
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width, ws.column_dimensions["C"].width = 34, 18, 52
    basis = "เทียบช่วงวันเดียวกัน ถ่วงตามวันในสัปดาห์" if not s.get("month_complete") else "เทียบทั้งเดือน"
    pct = [None, _FX_PCT, None]
    baht = [None, _FX_BAHT, None]
    _xl_rows(ws, [("ข้อมูลถึงวันที่", through or "-", "วันล่าสุดที่ปิดการขายแล้ว")], [None, None, None])
    _xl_rows(ws, [("ยอดขาย (ไม่รวม VAT)", s.get("mtd"), "ทั้งเดือน" if s.get("month_complete") else "สะสมถึงวันที่ข้างต้น")], baht)
    _xl_rows(ws, [("เทียบเดือนก่อน (%)", s.get("mtd_vs_last_month_pct"), basis),
                  ("เทียบช่วงเดียวกันของปีก่อน (%)", s.get("mtd_vs_last_year_pct"), "")], pct)
    _xl_rows(ws, [("จำนวนบิล", s.get("bills"), ""), ("จำนวนลูกค้า", s.get("customers"), "")], [None, "#,##0", None])
    _xl_rows(ws, [("เฉลี่ยต่อบิล (บาท)", s.get("per_bill"), "")], baht)
    _xl_rows(ws, [("ส่วนลด (% ของยอดขาย)", d.get("discount_pct"), ""),
                  ("กำไรขั้นต้น (%)", g.get("gp_pct"), ""),
                  ("เป้ากำไรขั้นต้น (%)", g.get("target_pct"),
                   "เดือนเดียวกันของปีก่อน" if g.get("target_basis") == "last_year" else "เดือนก่อน"),
                  ("ต้นทุนอาหาร (% ของยอดขาย)", g.get("food_cost_pct"), "ค่าต่ำสุด: ไม่รวมเมนูที่ยังไม่มีต้นทุน"),
                  ("เมนูที่ยังไม่มีต้นทุน (% ของจำนวนจาน)", cov.get("uncosted_plates_pct"), ""),
                  ("เมนูที่ยังไม่มีต้นทุน (% ของยอดขาย)", cov.get("uncosted_sales_pct"), "")], pct)
    if t.get("source") and t["source"] != "none":
        _xl_rows(ws, [("เป้ายอดขายทั้งเดือน", t.get("month_target"), _TARGET_SRC.get(t["source"], "")),
                      ("เป้ายอดขายถึงวันที่ข้อมูล", t.get("to_date"),
                       "ถ่วงตามวันในสัปดาห์" if t.get("weighting") == "dow" else "ตามจำนวนวัน" if t.get("weighting") == "days" else "ทั้งเดือน")], baht)
        _xl_rows(ws, [("ยอดขายเทียบเป้าถึงวันที่ข้อมูล (%)", t.get("vs_to_date_pct"), ""),
                      ("ยอดขายคิดเป็นร้อยละของเป้าทั้งเดือน", t.get("of_month_pct"), "")], pct)
    else:
        _xl_rows(ws, [("เป้ายอดขาย", "-", "ยังไม่มีเป้าของเดือนนี้")], [None, None, None])
    v = d.get("verdict") or {}
    for key, word in (("good", "ดี"), ("watch", "ควรดู"), ("fix", "ต้องแก้")):
        for line in v.get(key) or []:
            ws.append([f"สรุป: {word}", "", line])

    ws = wb.create_sheet()
    _sheet(ws, "รายวัน", ["วันที่", "วัน", "ยอดขาย (ไม่รวม VAT)", "จำนวนบิล", "จำนวนลูกค้า", "เฉลี่ยต่อบิล", "เทียบปกติ (%) · ค่ากลางวันเดียวกัน 8 สัปดาห์"],
           f"{head} · เฉพาะวันที่ปิดการขายแล้ว")
    _xl_rows(ws, [(x["date"], x["weekday"], x["revenue"], x["bills"], x["customers"], x["per_bill"], x["vs_normal_pct"])
                  for x in s.get("daily") or []], [None, None, _FX_BAHT, "#,##0", "#,##0", _FX_BAHT, _FX_PCT])

    ws = wb.create_sheet()
    _sheet(ws, "12 เดือน", ["เดือน", "สถานะ", "ยอดขาย (ไม่รวม VAT)", "เทียบเดือนก่อน (%)", "เทียบปีก่อน (%)", "จำนวนลูกค้า",
                            "เฉลี่ยต่อบิล", "กำไรขั้นต้น (%)"],
           f"ร้านของฉัน · สาขา{d.get('branch') or ''} · แต่ละเดือนแยกกัน เดือนที่ยังไม่จบเทียบช่วงวันเดียวกัน")
    _xl_rows(ws, [(m.get("label") or _month_label(m["month"]), f"สะสมถึง {m['through']}" if m.get("partial") and m.get("through") else "ครบเดือน",
                   m.get("revenue"), m.get("mom_pct"), m.get("yoy_pct"), m.get("customers"), m.get("per_bill"), m.get("gp_pct"))
                  for m in (d.get("trend_12m") or {}).get("months") or []],
             [None, None, _FX_BAHT, _FX_PCT, _FX_PCT, "#,##0", _FX_BAHT, _FX_PCT])

    ws = wb.create_sheet()
    _sheet(ws, "เมนูขายดี", ["อันดับ", "เมนู", "จำนวนจาน"], f"{head} · 10 อันดับตามจำนวนจาน")
    ws.column_dimensions["B"].width = 48
    _xl_rows(ws, [(i + 1, x["name"], x["plates"]) for i, x in enumerate(d.get("top_menus") or [])], [None, None, "#,##0"])
    if supply is not None:
        _supply_sheet(wb.create_sheet(), supply, head)
    for w in wb.worksheets:
        w.freeze_panes = "A3"
        w["A1"].font = Font(italic=True, color="5F6C80")
    return wb


def _supply_sheet(ws, sp: dict, head: str) -> None:
    """F3: the sheet วัตถุดิบ - the figures of the วัตถุดิบจากส่วนกลาง section (franchise_supply_data), nothing else."""
    from openpyxl.styles import Font
    from .bom_center import _sheet
    _sheet(ws, "วัตถุดิบ", ["หัวข้อ", "ค่า", "หมายเหตุ", "", "", ""], f"{head} · วัตถุดิบจากส่วนกลาง")
    for col, w in zip("ABCDEF", (30, 18, 40, 18, 22, 20)):
        ws.column_dimensions[col].width = w
    if not sp.get("started"):
        ws.append([sp.get("not_started_th") or NOT_STARTED_SUPPLY_TH])
        return
    pu, cl, lt = sp.get("purchases") or {}, sp.get("claims") or {}, sp.get("late") or {}
    upto = "" if pu.get("month_complete") else f"ถึงวันที่ {pu.get('through') or '-'} (วันล่าสุดที่ปิดการขาย)"
    _xl_rows(ws, [("ยอดสั่งจากส่วนกลาง (บาท)", pu.get("ordered"), f"{pu.get('rounds') or 0} รอบส่งในเดือนนี้"),
                  ("ยอดที่คลังจัดส่ง (บาท)", pu.get("delivered"), f"จัดส่งแล้ว {pu.get('rounds_delivered') or 0} รอบ")],
             [None, _FX_BAHT, None])
    _xl_rows(ws, [("ยอดซื้อเทียบยอดขาย (%)", pu.get("pct"), ("ยอดที่คลังจัดส่ง / ยอดขายไม่รวม VAT " + upto).strip()),
                  ("ยอดซื้อเทียบยอดขาย เดือนก่อน (%)", pu.get("pct_prev"), pu.get("month_prev") or ""),
                  ("เปลี่ยนแปลงจากเดือนก่อน (จุด)", pu.get("change_pp"), "")], [None, _FX_PCT, None])
    ws.append(["ส่งใบสั่งซื้อหลังเวลาส่งสุดท้าย (ใบ)", lt.get("count"), lt.get("text") or ""])
    for x in cl.get("by_status") or []:
        ws.append([f"เคลมสินค้า: {x['status_th']} (ครั้ง)", x["count"], ""])
    _xl_rows(ws, [("มูลค่าเคลมที่อนุมัติ (บาท)", cl.get("approved_value"), "")], [None, _FX_BAHT, None])

    def table(title, cols, rows, fmts):
        ws.append([])
        ws.append([title])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        ws.append(cols)
        for c in ws[ws.max_row]:
            c.font = Font(bold=True)
        _xl_rows(ws, rows, fmts)
    table("รอบส่งของเดือนนี้", ["วันส่ง", "รอบ", "มูลค่าที่สั่ง (บาท)", "มูลค่าที่จัดส่ง (บาท)", "สถานะ", "ส่งหลังเวลาส่งสุดท้าย"],
          [(r["delivery"], r["round_th"], r["ordered"], r["delivered"], r["status_th"], "ใช่ (มีค่าปรับ)" if r["late"] else "")
           for r in sp.get("rounds") or []], [None, None, _FX_BAHT, _FX_BAHT, None, None])
    table("เคลมสินค้าล่าสุด", ["วันที่แจ้ง", "รอบ", "รายการ", "จำนวน", "เหตุผล", "สถานะ"],
          [(c["date"], c.get("round_th") or "", i["name"], f"{i['qty']:g} {i['unit'] or ''}".strip() if i["qty"] is not None else "",
            i["reason_th"], c["status_th"] + (f" · {c['decision_note']}" if c.get("decision_note") else ""))
           for c in cl.get("latest") or [] for i in c["items"]], [None] * 6)
    ov = sp.get("overuse")
    if ov:
        from bom_center.close_settings import stock_mode
        with get_db() as _con:
            stk = stock_mode(_con)
        table(f"{'วัตถุดิบที่นับได้น้อยกว่าระบบ' if stk else 'วัตถุดิบที่ใช้เกินสูตร'} 5 อันดับ (รอบตรวจนับ {ov['begin']} ถึง {ov['end']})",
              ["วัตถุดิบ", "ขาด" if stk else "ใช้เกิน", "หน่วย", "มูลค่า (บาท)", "ต่ำกว่าระบบ (%)" if stk else "เกินสูตร (%)", ""],
              [(r["name"], r["qty"], r["unit"], r["value"], r["pct"], "") for r in ov.get("rows") or []],
              [None, "#,##0.##", None, _FX_BAHT, _FX_PCT, None])


@router.get("/api/franchise/summary.xlsx")
def franchise_summary_xlsx(request: Request, month: Optional[str] = Query(None, pattern=MONTH_RE),
                           branch_id: Optional[int] = Query(None)):
    """ดาวน์โหลดรายงานเดือนนี้: the summary of the month as an Excel file (same branch rules as the summary)."""
    from .bom_center import _fname, _xlsx
    with get_db() as con:
        bid, _ = _resolve_branch(con, request, branch_id)
    d = franchise_summary_data(bid, month)
    if not d.get("available"):
        raise HTTPException(404, "ยังไม่มีข้อมูลของเดือนนี้")
    sp = _safe("supply sheet", lambda: franchise_supply_data(bid, d.get("month")))
    return _xlsx(franchise_workbook(d, sp), _fname("ร้านของฉัน", d.get("branch"), d.get("month")))


# ── F2: what sells, the controls, the guests' voice ──────────────────────────
# GET /api/franchise/insight: the second half of ร้านของฉัน, loaded after the summary (the page asks for it once the cards above
# are drawn). The same branch rules as the summary; every figure comes from an engine a management page already shows:
#   mix.tiers      the management report's buffet price tiers (report.tier, Mgmt §11): covers, share of buffet covers this
#                  month and on last month's same window, spend per head
#   mix.lines      buffet (adult / child), à-la-carte and everything else as shares of the line sales, over the report's own
#                  window and its comparison window. The predicates are the Daily Brief's: rules.tier_code_sql = a buffet
#                  package (child = buffet._BUFFET_CHILD_CODES or BUFFETCHILD*), core._FOOD_CATEGORY_FILTER = an à-la-carte dish;
#                  the rest is drinks, delivery dishes and other lines. Covers = packages sold (buffet rows only)
#   mix.channels   the ประเภทลูกค้าและเดลิเวอรี page (home_extra.extra_customer_type): share of sales and share of the POS's
#                  customer-type tags, this month and last month. The tag count is a POS tally, so only its share is shown
#   peak           the report's hours (report.peak: sales and covers by hour over the window, the busiest hour, dayparts) and the
#                  ช่วงเวลาและรอบโต๊ะ page's weekday x hour grid (analysis_time.analysis_daypart_grid: sales per trading day)
#   controls       discounts: the report's headline (discount over pre-discount sales, the policy ceiling, the baht beyond it),
#                  by reason (analysis_discounts.discount_reasons) and by coupon / promotion (coupon_series_breakdown);
#                  voided bills (home_extra.extra_cancel_bills: count, baht, rate, by reason); cash over / short per day
#                  (home_extra.extra_cash + extra_cash_detail). Counts and baht only: no bill number, no cashier, no person
#   controls.flags "ควรดู / ต้องแก้" lines in the verdict's style, from the thresholds below
#   voice          the branch's public reviews (routers/feedback.py, kind 'review'): average stars and count this month and last
#                  month (feedback_summary), and the latest reviews up to the end of the month (feedback_items): date, stars,
#                  text, source. The feedback engine has no redaction helper; the names its analysis found in a review (the
#                  `staff` field) are masked in the text, and no internal note, action, reply status or assignee is sent
# Any text that names another branch (a customer-type label, a reason, a review) has that name replaced with "สาขาอื่น".
T_CANCEL_WATCH, T_CANCEL_FIX = 0.5, 1.0        # voided bills, % of all bills (owner 2026-10-22: ควรดู > 0.5 %, ต้องแก้ > 1 %; same bands as the บิลยกเลิก page)
T_CASH_DAY_FIX = 2000.0                         # one day's cash count short by at least this (push_api.CASH_SHORT_MIN)
T_CASH_SHORT_DAYS_WATCH = 3                     # days short in the month
MIX_TOP = 8                                     # rows per list (reasons, coupons, channels)
REVIEWS_SHOWN = 5
_SRC_LABEL = {"google": "Google", "facebook": "Facebook", "instagram": "Instagram", "tiktok": "TikTok", "line": "LINE",
              "wongnai": "Wongnai", "tripadvisor": "Tripadvisor", "survey": "แบบสอบถาม"}
_MASK = "พนักงาน"
_OTHER = "สาขาอื่น"
_DELIVERY_RE = re.compile(r"grab|line\s*man|food\s*panda|delivery|robinhood|shopee|เดลิเวอรี|แกร็บ", re.I)
_DAYPART_TH = {"Lunch": "กลางวัน (11:00-14:59)", "Dinner": "เย็น (17:00-22:59)", "Other": "ช่วงอื่น"}


def _plainj(v):
    """An in-process call to a @ttl_cache'd endpoint hands back a Response around JSON bytes."""
    if hasattr(v, "body"):
        v = v.body
    if isinstance(v, (bytes, bytearray, str)):
        return json.loads(v)
    return v


def _prev_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7]) - 1
    return f"{y - 1:04d}-12" if m == 0 else f"{y:04d}-{m:02d}"


def _scrubber(con, own: str):
    """-> fn(text) that replaces every OTHER branch's name with "สาขาอื่น" (the own name is kept, even inside a longer one)."""
    others = sorted((r[0] for r in con.execute("SELECT name FROM branches WHERE name <> 'ALL' AND name <> ?", (own,)) if r[0]),
                    key=len, reverse=True)

    def scrub(text):
        if not text:
            return text
        s = str(text).replace(own, "\x00") if own else str(text)
        for n in others:
            if n in s:
                s = s.replace("สาขา" + n, _OTHER).replace(n, _OTHER)
        return s.replace("\x00", own) if own else s
    return scrub


def _mix_lines(con, bid: int, s: str, e: str) -> dict:
    """Line sales of [s, e] split into adult buffet / child buffet / à-la-carte / other, and buffet packages sold."""
    from core import _FOOD_CATEGORY_FILTER
    from rules import tier_code_sql
    from .buffet import _BUFFET_CHILD_CODES
    child = "(s.sku_code IN (" + ",".join(f"'{c}'" for c in _BUFFET_CHILD_CODES) + ") OR s.sku_code LIKE 'BUFFETCHILD%')"
    buf = tier_code_sql("s.sku_code")
    r = con.execute(
        f"""SELECT COALESCE(SUM(d.subtotal_ex_vat),0),
                   COALESCE(SUM(CASE WHEN {buf} AND NOT {child} THEN d.subtotal_ex_vat END),0),
                   COALESCE(SUM(CASE WHEN {buf} AND NOT {child} THEN d.qty END),0),
                   COALESCE(SUM(CASE WHEN {buf} AND {child} THEN d.subtotal_ex_vat END),0),
                   COALESCE(SUM(CASE WHEN {buf} AND {child} THEN d.qty END),0),
                   COALESCE(SUM(CASE WHEN d.sku_id IN (SELECT sk.sku_id FROM skus sk JOIN categories c ON c.category_id = sk.category_id
                                                        WHERE {_FOOD_CATEGORY_FILTER}) THEN d.subtotal_ex_vat END),0)
            FROM v_sales_sku_daily d JOIN skus s ON s.sku_id = d.sku_id
            WHERE d.branch_id = :bid AND d.day BETWEEN :s AND :e""", {"bid": bid, "s": s, "e": e}).fetchone()
    return {"total": float(r[0] or 0), "adult": (float(r[1] or 0), float(r[2] or 0)),
            "child": (float(r[3] or 0), float(r[4] or 0)), "ala": float(r[5] or 0)}


_LINES = (("adult", "บุฟเฟ่ต์ผู้ใหญ่"), ("child", "บุฟเฟ่ต์เด็ก"), ("ala", "อาหารตามสั่ง"), ("other", "เครื่องดื่มและอื่น ๆ"))


def _line_rev(d: dict, key: str) -> float:
    if key == "other":
        return d["total"] - d["adult"][0] - d["child"][0] - d["ala"]
    return d["ala"] if key == "ala" else d[key][0]


def _lines_out(cur: dict, prev: Optional[dict]) -> list:
    def share(x, tot):
        return _num(100.0 * x / tot) if tot else None
    covers_c = cur["adult"][1] + cur["child"][1]
    covers_p = (prev["adult"][1] + prev["child"][1]) if prev else 0
    out = []
    for key, label in _LINES:
        row = {"key": key, "label": label, "sales": _num(_line_rev(cur, key), 0), "sales_share": share(_line_rev(cur, key), cur["total"]),
               "sales_share_prev": share(_line_rev(prev, key), prev["total"]) if prev else None,
               "covers": None, "covers_share": None, "covers_share_prev": None}
        if key in ("adult", "child"):
            row["covers"] = _num(cur[key][1], 0)
            row["covers_share"] = share(cur[key][1], covers_c)
            row["covers_share_prev"] = share(prev[key][1], covers_p) if prev else None
        out.append(row)
    return out


def _mix(con, rep: dict, bid: int, name: str, sel: str, scrub) -> dict:
    from .home_extra import extra_customer_type
    rng, cmp_rng = rep.get("range") or [None, None], rep.get("comp_range")
    T = rep.get("tier") or {}
    tiers = [{"tier": t.get("tier"), "label": t.get("label"), "covers": t.get("pax"), "covers_prev": t.get("pax_prev"),
              "covers_change_pct": t.get("d_pax_pct"), "share": t.get("share"), "share_prev": t.get("share_prev"),
              "per_head": _num(t.get("rev_head"), 0)} for t in (T.get("rows") or [])]
    lines = []
    if rng[0] and rng[1]:
        cur = _mix_lines(con, bid, rng[0], rng[1])
        prev = _mix_lines(con, bid, cmp_rng[0], cmp_rng[1]) if cmp_rng else None
        if cur["total"]:
            lines = _lines_out(cur, prev if prev and prev["total"] else None)

    def chan(month):
        d = extra_customer_type(month=month, branch=name, group=None)
        return {scrub(t["type"]): t for t in (d.get("types") or [])} if d.get("month") == month else {}
    now, before = chan(sel), chan(_prev_month(sel))
    channels = [{"type": k, "sales_share": v.get("net_pct"), "sales_share_prev": (before.get(k) or {}).get("net_pct"),
                 "tag_share": v.get("cust_pct"), "tag_share_prev": (before.get(k) or {}).get("cust_pct"),
                 "delivery": bool(_DELIVERY_RE.search(k or ""))}
                for k, v in sorted(now.items(), key=lambda kv: -(kv[1].get("net") or 0))][:MIX_TOP]
    return {"window": rng, "compare_window": cmp_rng, "tiers": tiers, "lines": lines, "channels": channels,
            "channels_month": sel if now else None, "channels_prev_month": _prev_month(sel) if before else None}


def _peak(rep: dict, name: str, sel: str) -> dict:
    from .analysis_time import analysis_daypart_grid
    P = rep.get("peak") or {}
    hours = [{"hour": h["hour"], "sales": _num(h.get("revenue"), 0), "covers": h.get("covers")} for h in (P.get("hourly") or [])]
    ph = P.get("peak_hour") or {}
    g = _plainj(analysis_daypart_grid(month=sel, month_from=None, month_to=None, branch=name, metric="revenue")) or {}
    cells = [{"dow": c["dow"], "hour": c["hour"], "sales": _num(c["value"], 0)} for c in (g.get("cells") or [])]
    best = max(cells, key=lambda c: c["sales"] or 0) if cells else None
    return {"hours": hours, "peak_hour": ph.get("hour"), "peak_hour_sales": ph.get("revenue"),
            "dayparts": [{"name": _DAYPART_TH.get(x["name"], x["name"]), "sales": x.get("revenue"), "covers": x.get("covers"),
                          "share": x.get("pct")} for x in (P.get("dayparts") or [])],
            "heat": cells, "heat_basis": "sales_per_trading_day", "heat_best": best}


def _controls(con, rep: dict, name: str, sel: str, scrub) -> dict:
    from .analysis_discounts import coupon_series_breakdown, discount_reasons
    from .home_extra import extra_cancel_bills, extra_cash, extra_cash_detail
    H = rep.get("headline") or {}
    pol = float(H.get("discount_policy_pct") or DEFAULT_DISCOUNT_POLICY)
    dr = discount_reasons(month_from=None, month_to=None, month=sel, branch=name, group=None)
    cs = coupon_series_breakdown(month_from=None, month_to=None, month=sel, branch=name, group=None)
    discount = {"pct": _num(H.get("discount_pct")), "policy_pct": _num(pol), "baht": _num(H.get("discount"), 0),
                "over_policy_baht": _num(H.get("discount_excess"), 0) if H.get("discount_excess") is not None else None,
                "by_reason": [{"reason": scrub(r.get("reason")), "bills": r.get("bills"), "baht": r.get("amount"), "share": r.get("pct")}
                              for r in (dr.get("reasons") or []) if (r.get("amount") or 0) > 0][:MIX_TOP],
                "by_coupon": [{"name": scrub(r.get("name")), "uses": r.get("uses"), "baht": r.get("amount"), "share": r.get("pct")}
                              for r in (cs.get("series") or []) if (r.get("amount") or 0) > 0][:MIX_TOP]}
    cb = extra_cancel_bills(month=sel, branch=name, group=None)
    same = cb.get("month") == sel
    cbs = (cb.get("summary") or {}) if same else {}
    voids = {"bills": cbs.get("bills") or 0, "baht": cbs.get("amount") or 0, "rate_pct": cbs.get("cancel_rate"),
             "by_reason": [{"reason": scrub(r.get("reason")), "bills": r.get("bills"), "baht": r.get("amount")}
                           for r in ((cb.get("reasons") or []) if same else [])][:MIX_TOP]}
    try:
        voids.update(void_open(con, name, sel) or {})     # the finding is on the voids not re-issued (owner 2026-10-31)
    except Exception as e:                                # pragma: no cover - the month's count still answers
        print(f"[franchise] void classes unavailable: {e}")
    ca = extra_cash(month=sel, branch=name, group=None)
    s = ca.get("summary") or {}
    days = [{"date": x["day"], "over_short": _num(x.get("over_short"), 0)}
            for x in (extra_cash_detail(branch=name, month=sel).get("days") or [])]
    cash = {"net": _num(s.get("net"), 0) if days else None, "short_total": _num(s.get("short_total"), 0) if days else None,
            "over_total": _num(s.get("over_total"), 0) if days else None, "short_days": s.get("short_days") if days else None,
            "days_counted": len(days), "days": days}
    # phone B (2026-10-31): the discounted bills printed more than once - the report's own money-leak signal (reports.py leak.reprinted:
    # discount > 0 AND print_times > 1), counts and baht only
    rp = (rep.get("leak") or {}).get("reprinted") or {}
    reprints = {"bills": int(rp.get("bills") or 0), "baht": _num(rp.get("baht") or 0, 0), "available": bool(rp) and not rp.get("unavailable")}
    return {"discount": discount, "voids": voids, "cash": cash, "reprints": reprints, "flags": _control_flags(discount, voids, cash)}


_TH_MON = ("ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.")


def _month_label(m) -> str:
    """U3 (owner 2026-10-22): ONE way to write a month on ร้านของฉัน, its Excel file included - the management report's "ต.ค. 2026"."""
    s = str(m or "")
    try:
        return f"{_TH_MON[int(s[5:7]) - 1]} {int(s[:4])}"
    except (ValueError, IndexError):
        return s


def _th_date(iso) -> str:
    try:
        d = date.fromisoformat(str(iso)[:10])
        return f"{d.day} {_TH_MON[d.month - 1]}"
    except ValueError:
        return str(iso or "")


def void_open(con, name: str, month: str) -> Optional[dict]:
    """2026-10-31 (owner): the month's voided bills NOT re-issued (rules.classify_voids, through mgt_watch.void_counts - the
    ตรวจวันนี้ card's own count) at one branch: {voids_open, amount_open, voids_reissued, rate_open_pct}, or None while the
    per-bill table does not cover the month (the finding then stays on every void, as before)."""
    from . import mgt_watch as W
    row = con.execute("SELECT branch_id FROM branches WHERE name=?", (name,)).fetchone()
    try:
        y, m = int(str(month)[:4]), int(str(month)[5:7])
    except ValueError:
        return None
    if not row:
        return None
    bid = row[0]
    s, e = date(y, m, 1), date(y, m, _cal.monthrange(y, m)[1])
    src, canc, bills = W.void_counts(con, {bid: name}, s, e, W._detail_cover(con), month_ok=False)
    if src != "detail":
        return None
    cnt = canc.get(bid, (0, 0, 0, 0, 0))
    cb, _amt, ob, oamt = cnt[:4]
    zb = cnt[4] if len(cnt) > 4 else 0
    tot = bills.get(bid, 0) + cb
    return {"voids_open": ob, "amount_open": round(oamt), "voids_reissued": cb - ob - zb, "voids_zero": zb,
            "rate_open_pct": round(ob / tot * 100, 2) if tot else None}


def _control_flags(discount: dict, voids: dict, cash: dict) -> dict:
    """ควรดู / ต้องแก้ lines for the controls card, in the verdict's words (counts and baht, never a person)."""
    out = []
    p, pol = discount.get("pct"), discount.get("policy_pct") or DEFAULT_DISCOUNT_POLICY
    if p is not None:
        if p > 2 * pol:
            out.append(("fix", "discount", f"ส่วนลดร้อยละ {_pct_text(p)} ของยอดขาย เกินเพดานร้อยละ {_pct_text(pol)} ควรตรวจสอบการให้ส่วนลด"))
        elif p > pol:
            out.append(("watch", "discount", f"ส่วนลดร้อยละ {_pct_text(p)} ของยอดขาย เกินเพดานร้อยละ {_pct_text(pol)}"))
    r = voids.get("rate_pct")
    if voids.get("rate_open_pct") is not None:            # 2026-10-31 (owner): a void followed by a new bill is a correction
        r = voids["rate_open_pct"]
        what = (f"บิลยกเลิกแล้วไม่ออกใหม่ {voids['voids_open']:,} ใบ (฿{voids.get('amount_open') or 0:,.0f}) คิดเป็นร้อยละ {_pct_text(r)} "
                f"ของบิลทั้งหมด (รวมยกเลิกทั้งหมด {voids['bills']:,} ใบ)")
        if not voids.get("voids_open"):
            r = None
    else:
        what = f"บิลยกเลิก {voids['bills']:,} ใบ (฿{voids['baht'] or 0:,.0f}) คิดเป็นร้อยละ {_pct_text(r)} ของบิลทั้งหมด"
    if r is not None and voids.get("bills"):
        if r > T_CANCEL_FIX:
            out.append(("fix", "voids", what + " สูงกว่าเกณฑ์ปกติ ควรตรวจสอบเหตุผลการยกเลิก"))
        elif r > T_CANCEL_WATCH:
            out.append(("watch", "voids", what))
    days = cash.get("days") or []
    bad = [x for x in days if (x["over_short"] or 0) <= -T_CASH_DAY_FIX]
    if bad:
        worst = min(bad, key=lambda x: x["over_short"])
        more = f" และอีก {len(bad) - 1} วัน" if len(bad) > 1 else ""
        out.append(("fix", "cash", f"เงินสดขาด ฿{-worst['over_short']:,.0f} ในวันที่ {_th_date(worst['date'])}{more} ควรตรวจสอบการนับเงินสด"))
    elif (cash.get("short_days") or 0) >= T_CASH_SHORT_DAYS_WATCH:
        out.append(("watch", "cash", f"เงินสดขาด {cash['short_days']} วันในเดือนนี้ รวม ฿{-(cash.get('short_total') or 0):,.0f}"))
    level = "fix" if any(lv == "fix" for lv, _, _ in out) else "watch" if out else "good"
    return {"level": level, "items": [{"level": lv, "key": k, "text": t} for lv, k, t in out]}


def _mask_names(text: str, names) -> str:
    """The names the feedback analysis found in a review (its `staff` field), masked in the text."""
    s = text or ""
    for n in sorted({str(x).strip() for x in (names or []) if str(x).strip()}, key=len, reverse=True):
        if len(n) >= 2:
            s = s.replace(n, _MASK)
    return s


def _voice(name: str, sel: str, scrub) -> dict:
    from .feedback import feedback_items, feedback_summary
    sm = feedback_summary(None, branch=name, month_from=sel, month_to=sel, source=None, kind="review")
    prev = sm.get("prev") or {}
    it = feedback_items(None, branch=name, month_from=None, month_to=sel, sentiment=None, theme=None, menu=None, source=None,
                        kind="review", needs_reply=0, flagged=0, sla=0, sort="newest", limit=REVIEWS_SHOWN)
    reviews = []
    for x in it.get("items") or []:
        src = str(x.get("source") or "").strip()
        reviews.append({"date": str(x.get("rated_at") or "")[:10], "stars": x.get("rating"),
                        "text": scrub(_mask_names(x.get("text") or "", x.get("staff")))[:400],
                        "source": _SRC_LABEL.get(src.lower(), src or "-")})
    avg, pavg = sm.get("avg_rating"), prev.get("avg_rating")
    return {"available": bool(sm.get("total") or reviews), "month": sel, "rating_avg": avg, "reviews_month": sm.get("total") or 0,
            "rating_avg_prev": pavg, "reviews_prev": prev.get("total"),
            "rating_change": round(avg - pavg, 2) if avg is not None and pavg is not None else None, "latest": reviews,
            # phone B (2026-10-31): the month's reviews nobody has answered yet and the negative ones (the เสียงลูกค้า card's count)
            "unreplied": int(sm.get("unreplied") or 0), "negative": int((sm.get("sentiment") or {}).get("neg") or 0)}


def franchise_insight_data(branch_id: int, month: Optional[str] = None) -> dict:
    """The F2 sections of one branch for one month (see the comment above). Each part fails on its own."""
    with get_db() as con:
        name = con.execute("SELECT name FROM branches WHERE branch_id=?", (branch_id,)).fetchone()[0]
        scrub = _scrubber(con, name)
        rep = _safe("report", lambda: _report(name, month), {}) or {}
        sel = rep.get("selected_month") or month
        ok = bool(rep.get("available")) and bool(sel)
        mix = _safe("mix", lambda: _mix(con, rep, branch_id, name, sel, scrub)) if ok else None
        peak = _safe("peak", lambda: _peak(rep, name, sel)) if ok else None
        controls = _safe("controls", lambda: _controls(con, rep, name, sel, scrub)) if ok else None
        voice = _safe("voice", lambda: _voice(name, sel, scrub)) if sel else None
    return {"branch": name, "month": sel, "label": rep.get("label"), "available": ok,
            "through": (rep.get("range") or [None, None])[1], "mix": mix, "peak": peak, "controls": controls, "voice": voice}


@router.get("/api/franchise/insight")
def franchise_insight(request: Request, month: Optional[str] = Query(None, pattern=MONTH_RE),
                      branch_id: Optional[int] = Query(None)):
    """ร้านของฉัน, second half: sales mix and busy hours, controls, the guests' reviews (same branch rules as the summary)."""
    with get_db() as con:
        bid, _ = _resolve_branch(con, request, branch_id)
    return franchise_insight_data(bid, month)


# ── F2: the franchise owner's morning notification (routers/push_api.maybe_send_franchise) ──
def _fmt_k(v) -> str:
    v = float(v or 0)
    if abs(v) >= 1e6:
        return f"{v / 1e6:.2f}M"
    if abs(v) >= 1e3:
        return f"{v / 1e3:.1f}K"
    return f"{v:,.0f}"


def _thai_today() -> date:
    from datetime import datetime
    return (datetime.utcnow() + timedelta(hours=7)).date()


def franchise_morning_payload(d: dict, ins: Optional[dict], today: Optional[date] = None) -> Optional[dict]:
    """Title + body of the morning push, from the summary (franchise_summary_data) and the controls (franchise_insight_data):
    the last finished day's net sales against its normal (summary.sales.daily: the median of the same weekday over 8 weeks), the month so far against
    the target to date (summary.target, F1) and the number of ต้องแก้ lines (the verdict's, plus the controls' voids and cash;
    the discount line is already the verdict's own, so it is counted once). None when the summary has no finished day."""
    s = (d or {}).get("sales") or {}
    ld = (s.get("last_day") or {}).get("date")
    if not d or not d.get("available") or not ld:
        return None
    row = next((x for x in (s.get("daily") or []) if x.get("date") == ld), None) or {}
    rev = row.get("revenue") if row else (s.get("last_day") or {}).get("revenue")
    wk = row.get("vs_normal_pct")
    today = today or _thai_today()
    dd = date.fromisoformat(ld)
    when = "เมื่อวาน" if dd == today - timedelta(days=1) else "วันล่าสุดที่ปิดการขาย"
    chip = f" {'▲' if wk >= 0 else '▼'}{abs(wk):.1f}% เทียบปกติ" if wk is not None else ""
    title = f"ร้านของฉัน · {when} ({_th_date(ld)}) ฿{_fmt_k(rev)}{chip}"
    bits = []
    t = d.get("target") or {}
    if t.get("source") not in (None, "none") and t.get("vs_to_date_pct") is not None:
        v = t["vs_to_date_pct"]
        bits.append(f"ยอดสะสมเดือนนี้{'สูงกว่า' if v >= 0 else 'ต่ำกว่า'}เป้าถึงวันนี้ {abs(v):.1f}%")
    elif s.get("mtd") is not None:
        bits.append(f"ยอดสะสมเดือนนี้ ฿{_fmt_k(s['mtd'])} (ยังไม่มีเป้า)")
    fixes = len((d.get("verdict") or {}).get("fix") or [])
    fixes += sum(1 for x in ((((ins or {}).get("controls") or {}).get("flags") or {}).get("items") or [])
                 if x.get("level") == "fix" and x.get("key") != "discount")
    bits.append(f"ต้องแก้ {fixes} รายการ" if fixes else "ไม่มีรายการต้องแก้")
    return {"title": title, "body": " · ".join(bits), "url": "/?go=franchise", "tag": "oshinei-franchise", "fix_count": fixes}


# ── F3: วัตถุดิบจากส่วนกลาง - what the branch buys from the centre ─────────────────
# GET /api/franchise/supply: the third lazy-loaded part of ร้านของฉัน (the same branch rules as the summary). Every figure is the
# supply module's own, read only:
#   rounds         the month's orders the branch SENT (submitted / processed / received; a draft or a cancelled order is not an
#                  order) whose delivery day (hub.delivery_of) falls in the month, named "รอบ 1 · อ. 28/10" (forecast.ROUND_TH +
#                  fmt.day_short_th: never an order number). ordered = hub.with_values value_ordered (confirmed_units), delivered =
#                  value_sent (processed_units, once the warehouse has sent it): the ONE order value of the branch and warehouse
#                  screens, so there is no second pricing formula and no per-item unit cost in the payload. status_th =
#                  orders.STATUS_TH, the words the branch's own screens show; late = bc_order.late (submitted after the cutoff)
#   purchases      month totals of ordered / delivered, and ยอดซื้อเทียบยอดขาย % = delivered value / net sales ex-VAT (the report's
#                  headline revenue, the summary's sales.mtd: same revenue rule). While the month is open both sides stop at the
#                  report's last finished day (deliveries on or before it); last month = all its deliveries over its full sales.
#                  change_pp = this month's % less last month's %
#   claims         claims RAISED in the month (Bangkok day of created_at, cancelled ones left out): count by status, the approved
#                  value (approved_units valued by hub._pricer / hub._base for the order's month, as the order lines are), the
#                  latest CLAIMS_SHOWN with item name, quantity, reason (claims.REASON_TH), status and the decision note the branch
#                  already reads on its claim screen. No person's name, no branch note
#   late           the number of the month's rounds submitted after the cutoff. The late fee has no engine: the count only
#   overuse        the latest counted variance period (the summary's ingredients block: variance.period of the last period whose
#                  end count falls in or before the month): the OVERUSE_TOP lines used most over the recipes in baht (status
#                  'over'; a line the data cannot judge - variance 'data_incomplete' - left out), quantity in the item's readable
#                  unit (units.readable). null when there is no counted period
# Before the branch started the supply module (started.branch_start: none, or after the month) the payload says only
# started=false and the page shows one neutral line, never red.
T_PURCHASE_RISE_WATCH = 3.0     # ยอดซื้อเทียบยอดขาย up by more than this many points on last month -> ควรดู
T_PURCHASE_MIN_DAYS = 7         # an open month is judged from this many finished days (deliveries come in rounds)
CLAIMS_SHOWN = 5
OVERUSE_TOP = 5
SENT = ("submitted", "processed", "received")
CLAIM_STATUS_TH = {"submitted": "รอพิจารณา", "approved": "อนุมัติ", "rejected": "ไม่อนุมัติ"}
NOT_STARTED_SUPPLY_TH = "สาขายังไม่เริ่มใช้ระบบสั่งวัตถุดิบ"


def _bc_started(con, bid: int, month: str):
    """-> (started for this month, start date). A month that ends before the branch's first day in the supply module counts as
    not started (started.branch_start: the earliest count, slip or sent order)."""
    from bom_center.started import branch_start
    start = branch_start(con, bid)
    _, last = _month_bounds(month)
    return bool(start) and start <= last.isoformat(), start


def _supply_rounds(con, bid: int, month: str) -> list:
    """The month's sent orders by delivery day, valued by hub.with_values (see the block comment)."""
    from bom_center.fmt import day_short_th
    from bom_center.forecast import ROUND_TH
    from bom_center.hub import delivery_of, with_values
    from bom_center.orders import STATUS_TH
    first, last = _month_bounds(month)
    rows = []
    for oid, cyc, rnd, st, ddate, late in con.execute(
            "SELECT order_id, cycle_date, round, status, delivery_date, COALESCE(late,0) FROM bc_order WHERE branch_id=?"
            f" AND status IN ({','.join('?' * len(SENT))}) AND cycle_date BETWEEN ? AND ? ORDER BY cycle_date, order_id",
            [bid, *SENT, (first - timedelta(days=14)).isoformat(), last.isoformat()]).fetchall():
        dlv = str(delivery_of(con, bid, rnd, cyc, ddate))[:10]
        if first.isoformat() <= dlv <= last.isoformat():
            rows.append({"order_id": oid, "cycle_date": cyc, "status": st, "round": rnd, "delivery": dlv, "late": bool(late)})
    with_values(con, rows)
    out = []
    for r in sorted(rows, key=lambda r: (r["delivery"], r["cycle_date"], r["order_id"])):
        rnd = ROUND_TH.get(str(r["round"]), str(r["round"]))
        out.append({"delivery": r["delivery"], "round_th": f"{rnd} · {day_short_th(r['delivery'])}",
                    "ordered": r["value_ordered"], "delivered": r["value_sent"], "status": r["status"],
                    "status_th": STATUS_TH.get(r["status"], r["status"]), "late": r["late"]})
    return out


def _month_sales(name: str, month: str, rep: Optional[dict] = None):
    """-> (net sales ex-VAT, through, complete) of the branch's month from the management report (the summary's sales.mtd),
    or (None, None, None) when the report has no data for that very month."""
    rep = rep if rep is not None else (_safe("report", lambda: _report(name, month), {}) or {})
    ok = rep.get("available") and rep.get("selected_month") == month and ((rep.get("range") or [""])[0] or "")[:7] == month
    if not ok:
        return None, None, None
    H = rep.get("headline") or {}
    return H.get("revenue"), (rep.get("range") or [None, None])[1], not rep.get("partial")


def _purchase_ratio(con, bid: int, name: str, month: str, rep: Optional[dict] = None, rounds: Optional[list] = None) -> dict:
    """ยอดซื้อเทียบยอดขาย of the month and of last month (see the block comment)."""
    rounds = rounds if rounds is not None else _supply_rounds(con, bid, month)
    sales, through, complete = _month_sales(name, month, rep)
    upto = through if (through and not complete) else None
    got = sum(r["delivered"] for r in rounds if r["delivered"] is not None and (upto is None or r["delivery"] <= upto))
    pct = _num(100.0 * got / sales) if sales else None
    prev = _prev_month(month)
    p_pct = p_got = p_sales = None
    p_range = None
    if _bc_started(con, bid, prev)[0]:
        p_rounds = _supply_rounds(con, bid, prev)
        if upto:
            # PR-PP3 · the one period pattern: an open month (to its last finished day, n days) is set against the first n
            # days of last month - its deliveries on those days over its net sales of those days (it was the WHOLE last month)
            import periods as _P
            from .period_api import _sales as _period_sales   # a neighbour: relative (test_franchise_f2)
            ps, pe = _P.resolve("month", upto)["prev"]
            p_range = [ps, pe]
            p_got = round(sum(r["delivered"] for r in p_rounds if r["delivered"] is not None and ps <= r["delivery"] <= pe), 2)
            p_sales = (_period_sales(con, "t.source='Easy' AND b.branch_id=:_bid", {"_bid": bid}, ps, pe) or {}).get("revenue")
        else:
            p_sales, _, _ = _month_sales(name, prev)
            p_got = round(sum(r["delivered"] for r in p_rounds if r["delivered"] is not None), 2)
        p_pct = _num(100.0 * p_got / p_sales) if p_sales else None
    first, _ = _month_bounds(month)
    days = (date.fromisoformat(through) - first).days + 1 if through else 0
    return {"pct": pct, "delivered_to_date": round(got, 2), "sales_to_date": _num(sales, 2), "through": through,
            "month_complete": bool(complete), "days": days,
            "pct_prev": p_pct, "delivered_prev": p_got, "sales_prev": _num(p_sales, 2), "month_prev": prev,
            "prev_range": p_range, "basis": "to_date" if upto else "full",
            "change_pp": _num(pct - p_pct) if pct is not None and p_pct is not None else None}


def _supply_claims(con, bid: int, month: str, scrub) -> dict:
    """Claims raised in the month: counts by status, the approved value and the latest ones (see the block comment)."""
    from bom_center.claims import REASON_TH
    from bom_center.fmt import bkk_stamp, item, round_day_th
    from bom_center.hub import _base, _pricer, delivery_of
    from bom_center.orders import unit_of_line
    from bom_center.units import readable
    first, last = _month_bounds(month)
    per, value_of = _pricer(con)
    ing = {c: (r, b) for c, r, b in con.execute("SELECT code, recv_unit, base_unit FROM bc_ingredient")}
    picked = []
    for cid, oid, st, at, note, cyc, rnd, ddate in con.execute(
            "SELECT c.claim_id, c.order_id, c.status, c.created_at, c.decision_note, o.cycle_date, o.round, o.delivery_date"
            " FROM bc_claim c LEFT JOIN bc_order o ON o.order_id = c.order_id WHERE c.branch_id=? AND c.status<>'cancelled'"
            " ORDER BY c.created_at DESC, c.claim_id DESC", (bid,)).fetchall():
        t = bkk_stamp(at)
        day = t.date() if t else (date.fromisoformat(str(at)[:10]) if at else None)
        if day and first <= day <= last:
            picked.append((cid, oid, st, day, note, cyc, rnd, ddate))
    counts = {k: 0 for k in CLAIM_STATUS_TH}
    approved_value = 0.0
    latest = []
    for cid, oid, st, day, note, cyc, rnd, ddate in picked:
        counts[st] = counts.get(st, 0) + 1
        units = {c: u for c, u in con.execute("SELECT ingredient_code, order_unit FROM bc_order_line WHERE order_id=?", (oid,))}
        lines = con.execute("SELECT ingredient_code, claim_units, approved_units, reason_code FROM bc_claim_line WHERE claim_id=?"
                            " ORDER BY ingredient_code", (cid,)).fetchall()
        if st == "approved":
            m = str(cyc or "")[:7] or month
            for c, _q, a, _r in lines:
                p = value_of(c, m)
                if p and a:
                    approved_value += _base(a, units.get(c), per.get(c)) * p
        if len(latest) < CLAIMS_SHOWN:
            items = []
            for c, q, a, rs in lines:
                recv, base = ing.get(c, (None, None))
                u = units.get(c) or "base"
                if u == "base":
                    qty, word = readable(q, base)
                else:
                    qty, word = q, unit_of_line(recv, base, u)[1]
                items.append({"name": item(con, c), "qty": _num(qty, 2), "unit": word, "reason_th": REASON_TH.get(rs, rs)})
            dlv = delivery_of(con, bid, rnd, cyc, ddate) if cyc else None
            decided = st in ("approved", "rejected") and (note or "").strip()
            latest.append({"date": day.isoformat(), "round_th": round_day_th(rnd, dlv) if cyc else None, "items": items,
                           "status": st, "status_th": CLAIM_STATUS_TH.get(st, st),
                           "decision_note": scrub(note.strip()) if decided else None})
    return {"count": len(picked),
            "by_status": [{"status": k, "status_th": v, "count": counts.get(k, 0)} for k, v in CLAIM_STATUS_TH.items()],
            "approved_value": round(approved_value, 2), "latest": latest}


def _supply_overuse(con, bid: int, month: str):
    """The latest counted period's lines used most over the recipes, in baht (see the block comment). None without a period."""
    from bom_center import variance as VA
    from bom_center.fmt import item
    from bom_center.stock import periods
    from bom_center.units import readable
    from bom_center.close_settings import stock_mode
    if stock_mode(con):
        return _supply_short(con, bid, month)
    _, last = _month_bounds(month)
    cut = (last + timedelta(days=1)).isoformat()
    ps = [p for p in periods(con, bid) if p[1]["date"] < cut]
    if not ps:
        return None
    v = VA.period(con, bid, ps[-1][1]["count_id"])
    over = [r for r in v["rows"] if r.get("status") == "over" and (r.get("gap_value") or 0) > 0 and not r.get("data_incomplete")]
    over.sort(key=lambda r: -r["gap_value"])
    rows = []
    for r in over[:OVERUSE_TOP]:
        q, u = readable(r["gap"], r.get("base_unit"))
        rows.append({"name": item(con, r["code"]), "qty": _num(q, 2), "unit": u, "value": _num(r["gap_value"], 0),
                     "pct": _num(r.get("gap_pct"))})
    return {"begin": v["begin_date"], "end": v["end_date"], "rows": rows, "lines_over": len(over)}


def _supply_short(con, bid: int, month: str):
    """F5, stock mode: the OVERUSE_TOP items counted furthest below the system stock (largest negative diff in baht) in the latest
    month-end reconcile; same row shape as the recipe version (qty / value / pct are the shortfall, positive). None without a period."""
    from bom_center.fmt import item
    from bom_center.units import readable
    rec = _stock_reconcile(con, bid, month)
    if not rec:
        return None
    short = [r for r in rec["rows"] if r.get("diff") is not None and r["diff"] < 0 and (r.get("diff_value") or 0) < 0]
    short.sort(key=lambda r: r["diff_value"])
    rows = []
    for r in short[:OVERUSE_TOP]:
        q, u = readable(-r["diff"], r.get("base_unit"))
        rows.append({"name": item(con, r["code"]), "qty": _num(q, 2), "unit": u, "value": _num(-r["diff_value"], 0),
                     "pct": _num(-r["diff_pct"]) if r.get("diff_pct") is not None else None})
    return {"begin": rec["period"]["begin_date"], "end": rec["period"]["end_date"], "rows": rows, "lines_over": len(short)}


def franchise_supply_data(branch_id: int, month: Optional[str] = None) -> dict:
    """วัตถุดิบจากส่วนกลาง of one branch for one month (see the block comment). Each part fails on its own."""
    from bom_center.clock import today as bkk_today
    from bom_center.schema import ensure as _bc_ensure
    with get_db() as con:
        _bc_ensure(con)
        name = con.execute("SELECT name FROM branches WHERE branch_id=?", (branch_id,)).fetchone()[0]
        rep = _safe("report", lambda: _report(name, month), {}) or {}
        sel = rep.get("selected_month") or month or bkk_today().isoformat()[:7]
        started, start = _bc_started(con, branch_id, sel)
        out = {"branch": name, "month": sel, "label": rep.get("label") or _month_label(sel), "started": started,
               "start_date": start if started else None, "not_started_th": None if started else NOT_STARTED_SUPPLY_TH,
               "rounds": [], "purchases": None, "claims": None, "late": None, "overuse": None}
        if not started:
            return out
        scrub = _scrubber(con, name)
        rounds = _safe("supply rounds", lambda: _supply_rounds(con, branch_id, sel), []) or []
        ratio = _safe("purchase ratio", lambda: _purchase_ratio(con, branch_id, name, sel, rep, rounds)) or {}
        n_late = sum(1 for r in rounds if r["late"])
        out.update(
            rounds=rounds,
            purchases={"ordered": round(sum(r["ordered"] or 0 for r in rounds), 2),
                       "delivered": round(sum(r["delivered"] for r in rounds if r["delivered"] is not None), 2),
                       "rounds": len(rounds), "rounds_delivered": sum(1 for r in rounds if r["delivered"] is not None), **ratio},
            claims=_safe("supply claims", lambda: _supply_claims(con, branch_id, sel, scrub)),
            late={"count": n_late, "text": f"ส่งหลังเวลาส่งสุดท้าย {n_late} ใบ (มีค่าปรับ)" if n_late else "ส่งทันเวลาตัดรอบทุกใบ"},
            overuse=_safe("overuse", lambda: _supply_overuse(con, branch_id, sel)))
    return out


def _supply_verdict(ratio: Optional[dict]):
    """(level, text) for the summary's verdict, or None: ยอดซื้อเทียบยอดขาย up by more than T_PURCHASE_RISE_WATCH points on last month,
    judged once the month has T_PURCHASE_MIN_DAYS finished days (the claims line is the discipline block's, not repeated here)."""
    if not ratio or ratio.get("change_pp") is None or ratio.get("pct") is None:
        return None
    if not ratio.get("month_complete") and (ratio.get("days") or 0) < T_PURCHASE_MIN_DAYS:
        return None
    d = ratio["change_pp"]
    if d > T_PURCHASE_RISE_WATCH:
        return ("watch", f"ยอดซื้อวัตถุดิบจากส่วนกลางคิดเป็นร้อยละ {_pct_text(ratio['pct'])} ของยอดขาย "
                         f"สูงกว่าเดือนก่อน {_pct_text(d)} จุด ควรตรวจสอบการสั่งและการใช้วัตถุดิบ")
    return None


@router.get("/api/franchise/supply")
def franchise_supply(request: Request, month: Optional[str] = Query(None, pattern=MONTH_RE),
                     branch_id: Optional[int] = Query(None)):
    """ร้านของฉัน, วัตถุดิบจากส่วนกลาง: the month's delivery rounds, purchases against sales, claims, late orders and the lines
    used most over the recipes (same branch rules as the summary)."""
    with get_db() as con:
        bid, _ = _resolve_branch(con, request, branch_id)
    return franchise_supply_data(bid, month)


# ── DV6 (phone table review 2026-10-25, owner: "อะไรที่มีประโยชน์ขึ้นหน้าจอของเจ้าของสาขาด้วย") ─────────────────────────────
# GET /api/franchise/decide - เรื่องที่ควรดู: the decision items of ONE branch for the period of the top bar (kind week | month,
# pick = any day of it; the windows are periods.windows on the branch's own last closed day, the period before to date as the
# base). Every figure comes from the engine the office pages read; this block only picks the owner's own row and the chain's
# aggregate ("เฉลี่ยทุกสาขา" - never another branch's row or name):
#   costly     ของแพง: the 8 high-value ingredient groups (ingredient_groups.compute_plates, the plates view of the High Value
#              Product Monitor) - plates per customer now / the period before / เฉลี่ยทุกสาขา, and why the plates moved
#              (ลูกค้า / กินต่อหัว, in plates). Plates only: no cost. Sorted by the plates over the chain's per-head rate
#              (what serving more than the chain adds), then by plates
#   menus      the per-head menu rule of the menu-spike card (sales._menu_spike_period, the plates view - with_cost False): the
#              menus ordered more per customer than their normal (the 3 weeks before, weekday-fair), top 5 by extra plates; and
#              the new / returning menus of the period (top 5 by plates)
#   food_cost  food cost % of sales (period_cost.cost_sum, the one cost engine of the period pattern) now / the period before,
#              against the ONE target (rules.food_cost_target; target_gap_baht = (pct - target) x the branch's sales) and against
#              เฉลี่ยทุกสาขา (the chain's pooled figure; gap_baht = (pct - chain pct) x the branch's sales)
#   weeks      the last DECIDE_WEEKS whole Monday-Sunday weeks ending on or before the period's end: net sales
#              (rules.net_revenue_sum, finished days only) against the branch's own average of those weeks (the heat strip)
DECIDE_KINDS = ("week", "month")
DECIDE_WEEKS = 6
DECIDE_TOP = 5
DATE_RE = r"^\d{4}-\d{2}-\d{2}$"
_DECIDE_SPIKE = {"week": "week", "month": "mtd"}


def _decide_window(con, bid: int, kind: str, pick: Optional[str]):
    import periods as P
    return P.windows(con, kind, "t.source='Easy' AND b.branch_id=:bid", {"bid": bid}, pick=pick)


def _decide_costly(con, w: dict, name: str) -> dict:
    import ingredient_groups as IG
    import periods as P
    d = IG.compute_plates(con, w, None, None)
    me = next((b for b in d.get("branches") or [] if b.get("branch") == name), None) or {}
    chain = d.get("chain") or {}
    n = float(me.get("customers") or 0)
    rows = []
    for g in d.get("groups") or []:
        s = g["slug"]
        c = (me.get("cells") or {}).get(s) or {}
        a = (chain.get("cells") or {}).get(s) or {}
        if not (c.get("plates") or (c.get("prev") or {}).get("plates")):
            continue
        h, h0, ha = c.get("plates_per_head"), (c.get("prev") or {}).get("plates_per_head"), a.get("plates_per_head")
        cause = c.get("cause") or {}
        rows.append({"slug": s, "label": g["label"], "plates": c.get("plates"), "per_head": h, "prev_per_head": h0,
                     "per_head_change_pct": P.pct(h, h0) if h is not None and h0 else None,
                     "avg_per_head": ha, "vs_avg_pct": P.pct(h, ha) if h is not None and ha else None,
                     "over_avg_plates": round((h - ha) * n, 1) if h is not None and ha is not None and n else None,
                     "plates_change": cause.get("total"), "plates_change_pct": cause.get("total_pct"),
                     "by_customers": cause.get("customers"), "by_per_head": cause.get("plates_per_head"),
                     "main": cause.get("main")})
    rows.sort(key=lambda r: (-(r["over_avg_plates"] if r["over_avg_plates"] is not None else -1e9), -(r["plates"] or 0)))
    return {"customers": me.get("customers"), "prev_customers": me.get("prev_customers"),
            "customers_change_pct": (me.get("change") or {}).get("customers"),
            "base_label": (d.get("base") or {}).get("label_th"), "groups": rows}


def _decide_menus(con, w: dict, name: str) -> dict:
    from .sales import _menu_spike_period
    d = _menu_spike_period(con, _DECIDE_SPIKE[w["kind"]], w["cur"][1], None, None, " AND b.name=:branch", {"branch": name},
                           300, 1.5, with_cost=False)
    up = [r for r in (d.get("spikes") or []) if (r.get("excess") or 0) > 0]
    rising = [{"menu": r["menu"], "plates": r.get("today"), "rate": r.get("rate"), "normal_rate": r.get("normal_rate"),
               "extra_plates": r.get("excess"), "x": r.get("x"), "per_buffet": r.get("basis") == "buffet"}
              for r in up[:DECIDE_TOP]]
    new = [{"menu": m["menu"], "plates": m.get("qty"), "rate": m.get("rate"), "first_sold_th": m.get("first_sold_th"),
            "age_days": m.get("age_days"), "returning": bool(m.get("returning"))}
           for m in (d.get("new_menus") or [])[:DECIDE_TOP]]
    return {"from": d.get("date_from"), "to": d.get("date_to"), "normal_from": d.get("baseline_from"),
            "normal_to": d.get("baseline_to"), "rising": rising, "rising_count": len(up),
            "new": new, "new_count": d.get("new_count") or 0}


def _decide_food_cost(con, w: dict, name: str) -> dict:
    import period_cost as PC
    import periods as P
    from rules import food_cost_target
    floor = PC.cost_floor(con)
    cur, why = PC.cost_sum(con, *w["cur"], name, None, floor)
    prev = PC.cost_sum(con, *w["prev"], name, None, floor)[0] if w.get("has_prev") else None
    avg = PC.cost_sum(con, *w["cur"], None, None, floor)[0]
    avg_prev = PC.cost_sum(con, *w["prev"], None, None, floor)[0] if w.get("has_prev") else None

    def fc(x):
        return _num((x or {}).get("food_cost_pct"))
    pct, ppct, apct, appct = fc(cur), fc(prev), fc(avg), fc(avg_prev)
    rev = (cur or {}).get("revenue")
    both = pct is not None and apct is not None
    tgt = food_cost_target(con)                       # the ONE target (owner 2026-10-25, Settings → ค่าเริ่มต้น)
    return {"target_pct": tgt,
            "target_gap_baht": round((pct - tgt) / 100.0 * rev) if pct is not None and rev else None,
            "pct": pct, "prev_pct": ppct, "change_pp": P.pp(pct, ppct) if pct is not None and ppct is not None else None,
            "avg_pct": apct, "avg_prev_pct": appct,
            "avg_change_pp": P.pp(apct, appct) if apct is not None and appct is not None else None,
            "vs_avg_pp": P.pp(pct, apct) if both else None,
            "gap_baht": round((pct - apct) / 100.0 * rev) if both and rev else None,
            "coverage_pct": (cur or {}).get("cost_coverage_pct"), "reason": why}


def _decide_weeks(con, bid: int, end_iso: str) -> dict:
    import periods as P
    from rules import closed_day_clause, net_revenue_sum
    end = date.fromisoformat(end_iso[:10])
    last_sun = end - timedelta(days=(end.weekday() + 1) % 7)       # the last whole Monday-Sunday week ends on this Sunday
    first_mon = last_sun - timedelta(days=7 * DECIDE_WEEKS - 1)
    rows = {r[0]: (r[1] or 0.0, r[2] or 0) for r in con.execute(
        f"SELECT date(t.sold_at), {net_revenue_sum('t')}, COALESCE(SUM(t.customer_count),0) FROM sales_transaction t "
        f"WHERE t.branch_id=:bid AND t.source='Easy' AND t.sold_at >= :s AND t.sold_at < :e "
        f"AND {closed_day_clause('t.sold_at')} GROUP BY date(t.sold_at)",
        {"bid": bid, "s": first_mon.isoformat(), "e": (last_sun + timedelta(days=1)).isoformat()})}
    weeks = []
    for i in range(DECIDE_WEEKS):
        a = first_mon + timedelta(days=7 * i)
        days = [(a + timedelta(days=j)).isoformat() for j in range(7)]
        got = [rows[x] for x in days if x in rows]
        weeks.append({"from": a.isoformat(), "to": days[-1], "label": P.week_span_th(a, a + timedelta(days=6)),
                      "revenue": round(sum(g[0] for g in got), 2) if got else None,
                      "customers": int(sum(g[1] for g in got)) if got else None})
    live = [x["revenue"] for x in weeks if x["revenue"]]
    avg = sum(live) / len(live) if len(live) >= 3 else None
    for x in weeks:
        x["vs_avg_pct"] = _num(100.0 * (x["revenue"] / avg - 1)) if avg and x["revenue"] else None
    return {"weeks": weeks, "avg": round(avg, 2) if avg else None}


def franchise_decide_data(branch_id: int, kind: str = "week", pick: Optional[str] = None) -> dict:
    """เรื่องที่ควรดู of one branch for one period (see the block comment above)."""
    import periods as P
    with get_db() as con:
        name = con.execute("SELECT name FROM branches WHERE branch_id=?", (branch_id,)).fetchone()[0]
        w = _decide_window(con, branch_id, kind, pick)
        if not w:
            return {"available": False, "period": None, "costly": None, "menus": None, "food_cost": None, "weeks": None}
        return {"available": True, "period": P.public(w) or {},
                "costly": _safe("costly", lambda: _decide_costly(con, w, name)),
                "menus": _safe("menus", lambda: _decide_menus(con, w, name)),
                "food_cost": _safe("food cost", lambda: _decide_food_cost(con, w, name)),
                "weeks": _safe("weeks", lambda: _decide_weeks(con, branch_id, w["cur"][1]))}


@ttl_cache
def franchise_decide_payload(branch_id: int, kind: str, pick: Optional[str]):
    """The cached answer of /api/franchise/decide (the branch is resolved from the session before this is called)."""
    return franchise_decide_data(branch_id, kind, pick)


@router.get("/api/franchise/decide")
def franchise_decide(request: Request, kind: str = Query("week"), pick: Optional[str] = Query(None, pattern=DATE_RE),
                     branch_id: Optional[int] = Query(None)):
    """ร้านของฉัน · เรื่องที่ควรดู: ของแพง per customer, the menus ordered more per customer, new menus, food cost % against
    เฉลี่ยทุกสาขา and the last 6 weeks' sales against the branch's own average - for the top bar's period (week | month)."""
    if kind not in DECIDE_KINDS:
        raise HTTPException(400, "ช่วงเวลาต้องเป็นสัปดาห์ (week) หรือเดือน (month)")
    with get_db() as con:
        bid, _ = _resolve_branch(con, request, branch_id)
    return franchise_decide_payload(bid, kind, pick)


# ── สรุปประจำวัน: the last finished day at the top of ร้านของฉัน (owner 2026-10-27, mockup B) ──────────────────────
# GET /api/franchise/daily: the owner's own branch only (the same branch rules as the summary), and never the day still
# trading. The day does NOT follow the top bar's period: it is always the branch's last finished day.
#   day     rules.latest_settled_day on the branch's own bills (closed_day_clause, and it steps back over a finished day the
#           POS has not posted yet). Net sales on the dashboard's revenue rule (rules.net_revenue_sum: bill total / 1.07 less
#           service charge), bills, customers, per head and per bill; vs_normal_pct = against its normal (rules.daily_baseline:
#           the median of the same weekday over the 8 weeks before; null under 2 such weeks, normal_weeks says how many). discount_pct = discount over the PRE-discount base (rules.
#           discount_base_row, the report's and the Discounts tab's base) against the policy ceiling (rules.
#           discount_policy_pct): level 'good' within it, 'watch' above it, 'fix' above twice it (the controls card's bands).
#           food_cost_pct = period_cost.cost_sum over the one day (the cost engine of the period pattern) against the ONE
#           target (rules.food_cost_target); null with the engine's reason when the day has no cost source. No last-year
#           figure for the day: the owner left it out (2026-10-27).
#   month   the month that holds the day, from the management report the summary reads (to date through its last finished
#           day): sales so far, against last month and last year on the same days (the summary's own fields), the sales
#           target (_target: targets.py, weekday-fair while open) and the month's voided bills (home_extra.extra_cancel_bills,
#           monthly only: the POS gives voided bills per month) with the controls card's bands T_CANCEL_WATCH / T_CANCEL_FIX.
#           When the report counts a later day than the card (the newest finished day has not fully arrived - latest_settled_day
#           skipped it, e.g. พิษณุโลก 26 Oct 2026 with 14 bills at 10:00), the month ends on the card's day instead (_month_to_day,
#           owner 2026-10-27): the same to-date windows (periods.windows: days 1..n of last month, the same dates last year), the
#           same revenue rule and the report's weekday-fair projection (rules.month_projection) for the target to date
def _daily_day(con, bid: int, name: str, day: str) -> dict:
    import period_cost as PC
    from rules import (daily_baseline, discount_base_row, discount_policy_pct, food_cost_status_th, food_cost_target,
                       net_revenue_sum)
    d = date.fromisoformat(day)
    rows = {r[0]: r for r in con.execute(
        f"SELECT date(t.sold_at), {net_revenue_sum('t')}, COUNT(*), COALESCE(SUM(t.customer_count),0), "
        f"COALESCE(SUM(t.discount),0), SUM({discount_base_row('t')}) FROM sales_transaction t "
        f"WHERE t.branch_id=:bid AND t.source='Easy' AND date(t.sold_at) = :d GROUP BY date(t.sold_at)",
        {"bid": bid, "d": day})}
    cur = rows.get(day)
    rev = _num(cur[1], 2) if cur else None
    bills, cust = (cur[2], int(cur[3])) if cur else (0, 0)
    # THE daily comparison: the day against "ปกติ" (rules.daily_baseline - the scorecard's own figure for this branch)
    bl = (daily_baseline(con, "t.source='Easy' AND t.branch_id=:bid", {"bid": bid}, day=day, by_branch=True) or {}).get(name) or {}
    nb = bl.get("normal") or {}
    disc = _num(100.0 * cur[4] / cur[5]) if cur and cur[5] else None
    pol = float(discount_policy_pct(con))
    dlevel = None if disc is None else "fix" if disc > 2 * pol else "watch" if disc > pol else "good"
    fc, why = _safe("daily food cost", lambda: PC.cost_sum(con, day, day, name, None, PC.cost_floor(con)), (None, None))
    fpct = _num((fc or {}).get("food_cost_pct"))
    tgt = food_cost_target(con)
    return {"date": day, "weekday": TH_DOW[d.weekday()], "revenue": rev, "bills": bills, "customers": cust,
            "per_head": _num(rev / cust, 0) if rev is not None and cust else None,
            "per_bill": _num(rev / bills, 0) if rev is not None and bills else None,
            "normal": _num(nb.get("revenue"), 2) if nb.get("revenue") is not None else None, "normal_weeks": nb.get("n_weeks", 0),
            "vs_normal_pct": bl.get("vs_normal_pct"), "normal_status": bl.get("status"), "normal_label": bl.get("label_short"),
            "discount_pct": disc, "discount_policy_pct": _num(pol), "discount_level": dlevel,
            "food_cost_pct": fpct, "food_cost_target_pct": _num(tgt),
            "food_cost_status_th": food_cost_status_th(fpct, tgt) if fpct is not None else None,
            "food_cost_reason": None if fpct is not None else (why or "ยังไม่มีต้นทุนของวันนี้")}


def _daily_voids(name: str, month: str) -> dict:
    from .home_extra import extra_cancel_bills
    cb = extra_cancel_bills(month=month, branch=name, group=None)
    s = (cb.get("summary") or {}) if cb.get("month") == month else {}
    r = s.get("cancel_rate")
    level = None if r is None else "fix" if r > T_CANCEL_FIX else "watch" if r > T_CANCEL_WATCH else "good"
    return {"bills": s.get("bills") or 0, "rate_pct": _num(r, 2), "level": level,
            "watch_pct": T_CANCEL_WATCH, "fix_pct": T_CANCEL_FIX}


def _month_to_day(con, bid: int, name: str, day: str) -> Optional[dict]:
    """The month that holds `day`, to date through `day` (see the block comment above); the shape of the report path."""
    import periods as P
    from rules import month_projection, net_revenue_sum
    w = P.windows(con, "month", "t.source='Easy' AND t.branch_id=:bid", {"bid": bid}, anchor_day=day)
    if not w:
        return None

    def rev(span):
        r = con.execute(f"SELECT {net_revenue_sum('t')} FROM sales_transaction t WHERE t.branch_id=:bid AND t.source='Easy' "
                        f"AND date(t.sold_at) BETWEEN :s AND :e", {"bid": bid, "s": span[0], "e": span[1]}).fetchone()
        return float(r[0]) if r and r[0] is not None else None
    mtd = rev(w["cur"])
    if mtd is None:
        return None
    prev = rev(w["prev"]) if w.get("has_prev") else None
    ly = rev(w["ly"]) if w.get("has_ly") else None
    first = date.fromisoformat(w["cur"][0])
    d = date.fromisoformat(day)
    days_total = _cal.monthrange(first.year, first.month)[1]
    pr = month_projection(con, "st.source='Easy' AND st.branch_id=:bid", {"bid": bid}, first, d.day, days_total, mtd)
    sales = {"mtd": _num(mtd, 2), "month_complete": bool(w["complete"])}
    pace = {"proj_method": pr["method"], "projected": pr["projected"], "mtd_revenue": mtd, "days_elapsed": d.day,
            "days_total": days_total}
    mon = day[:7]
    return {"month": mon, "label": f"เดือน {_TH_MON[first.month - 1]} {first.year}" + ("" if w["complete"] else f" (1–{d.day})"),
            "through": day, "complete": bool(w["complete"]), "mtd": sales["mtd"],
            "vs_last_month_pct": _num(P.pct(mtd, prev)) if prev else None, "mom_basis": "month" if w["complete"] else "to_date",
            "vs_last_year_pct": _num(P.pct(mtd, ly)) if ly else None,
            "target": _safe("daily target", lambda: _target(con, {"pace": pace}, sales, name, mon)),
            "voids": _safe("daily voids", lambda: _daily_voids(name, mon))}


def franchise_daily_data(branch_id: int) -> dict:
    """สรุปประจำวัน of one branch (see the block comment above). NOT wrapped in core.ttl_cache: the report it reads is cached."""
    from rules import latest_settled_day
    with get_db() as con:
        name = con.execute("SELECT name FROM branches WHERE branch_id=?", (branch_id,)).fetchone()[0]
        st = latest_settled_day(con, "t.source='Easy' AND t.branch_id=:bid", {"bid": branch_id})
        day = st.get("day")
        if not day:
            return {"available": False, "branch": name, "day": None, "month": None}
        out = {"available": True, "branch": name, "skipped_day": st.get("skipped_day"),
               "day": _daily_day(con, branch_id, name, day), "month": None}
        mon = day[:7]
        rep = _safe("daily report", lambda: _report(name, mon), {}) or {}
        sales = _sales_and_gp(rep, name)[0] if rep.get("selected_month") == mon else None
        if sales and sales.get("through") and sales["through"] > day:     # the report counts a day the card skipped
            out["month"] = _safe("daily month to day", lambda: _month_to_day(con, branch_id, name, day))
        elif sales:
            out["month"] = {"month": mon, "label": rep.get("label"), "through": sales["through"],
                            "complete": sales["month_complete"], "mtd": sales["mtd"],
                            "vs_last_month_pct": sales["mtd_vs_last_month_pct"], "mom_basis": sales["mom_basis"],
                            "vs_last_year_pct": sales["mtd_vs_last_year_pct"],
                            "target": _safe("daily target", lambda: _target(con, rep, sales, name, mon)),
                            "voids": _safe("daily voids", lambda: _daily_voids(name, mon))}
    return out


@router.get("/api/franchise/daily")
def franchise_daily(request: Request, branch_id: Optional[int] = Query(None)):
    """ร้านของฉัน · สรุปประจำวัน: the branch's last finished day and its month so far (see the block comment above)."""
    with get_db() as con:
        bid, _ = _resolve_branch(con, request, branch_id)
    return franchise_daily_data(bid)


# ── phone B for the owner (owner 2026-10-31, "เริ่มเลย"): GET /api/franchise/overview ─────────────────────────────────────
# ร้านของฉัน is overview cards first (เมื่อวาน · เดือนนี้ · เป้า · ต้นทุนอาหาร · ควรดู · การควบคุม · เมนู · เสียงลูกค้า · ติดต่อสำนักงานใหญ่),
# each opening a detail screen (js/17-franchise.js). The cards need the month summary, the F2 insight and เรื่องที่ควรดู at once, so this
# endpoint returns the three payloads the page used to fetch one by one (the same functions, run side by side) plus ONE list of findings
# for the ควรดู card, built from those figures only (franchise_findings: no query of its own). /summary, /insight and /decide stay for the
# Excel file, the morning push and head office.
#   findings   [{kind, level 'fix'|'watch', title, val, chip, fact, more, go: {d: detail screen, el: element id on it}}], fix first.
#              kinds and their thresholds (one place each):
#                discount     the month's discount over the ceiling (controls.discount; > 2 x ceiling = fix)       -> การควบคุม
#                voids        voided bills over T_CANCEL_WATCH % of bills (> T_CANCEL_FIX = fix)                    -> การควบคุม
#                reprints     discounted bills printed more than once (the report's leak.reprinted), any = watch    -> การควบคุม
#                cash         the controls' cash flag (a day short by T_CASH_DAY_FIX, or T_CASH_SHORT_DAYS_WATCH days) -> การควบคุม
#                food_cost    food cost % of the month over the ONE target (rules.food_cost_target) = fix           -> ต้นทุนอาหาร
#                costly       a high-value group eaten T_COSTLY_VS_AVG % or more per customer above เฉลี่ยทุกสาขา, with
#                             plates over the average (decide.costly; at most COSTLY_SHOWN)                          -> ต้นทุนอาหาร
#                below_normal finished days of the month at or under rules.BELOW_NORMAL_PCT against their normal
#                             (rules.daily_baseline; fix when one is at or under BIG_BELOW_NORMAL_PCT)               -> เดือนนี้
#                voice        reviews of the month not answered yet, or negative ones                               -> เสียงลูกค้า
T_COSTLY_VS_AVG = 10.0
COSTLY_SHOWN = 2


def _pct1(v) -> str:
    return "—" if v is None else f"{float(v):.1f}".rstrip("0").rstrip(".") + "%"


def franchise_findings(summary: Optional[dict], insight: Optional[dict], decide: Optional[dict] = None) -> list:
    """The ควรดู list of ONE branch from the payloads the page already has (see the block comment above). Pure: no query."""
    from rules import BELOW_NORMAL_PCT, BIG_BELOW_NORMAL_PCT
    S, I, D = summary or {}, insight or {}, decide or {}
    out = []

    def add(kind, level, title, val, fact, more, d, el):
        out.append({"kind": kind, "level": level, "title": title, "val": val, "chip": "ต้องแก้" if level == "fix" else "ควรดู",
                    "fact": fact, "more": more, "go": {"d": d, "el": el}})

    C = I.get("controls") or {}
    flags = {f.get("key"): f for f in ((C.get("flags") or {}).get("items") or [])}
    dc = C.get("discount") or {}
    if "discount" in flags:
        over = dc.get("over_policy_baht")
        add("discount", flags["discount"]["level"], "ส่วนลดเกินเพดาน", _pct1(dc.get("pct")),
            f"เพดาน {_pct1(dc.get('policy_pct'))}" + (f" · เกิน ฿{over:,.0f}" if over else ""), flags["discount"]["text"], "controls", "frcCtlDisc")
    v = C.get("voids") or {}
    if "voids" in flags:
        if v.get("rate_open_pct") is not None:           # the voids not re-issued, the total in brackets (owner 2026-10-31)
            add("voids", flags["voids"]["level"], "ยกเลิกแล้วไม่ออกใหม่", _pct1(v.get("rate_open_pct")),
                f"{int(v.get('voids_open') or 0):,} ใบ · ฿{float(v.get('amount_open') or 0):,.0f} · รวมยกเลิกทั้งหมด {int(v.get('bills') or 0):,}",
                flags["voids"]["text"], "controls", "frcCtlVoids")
        else:
            add("voids", flags["voids"]["level"], "บิลยกเลิกสูง", _pct1(v.get("rate_pct")),
                f"{int(v.get('bills') or 0):,} ใบ · ฿{float(v.get('baht') or 0):,.0f}", flags["voids"]["text"], "controls", "frcCtlVoids")
    rp = C.get("reprints") or {}
    if rp.get("bills"):
        add("reprints", "watch", "บิลพิมพ์ซ้ำที่มีส่วนลด", f"{int(rp['bills']):,} บิล", f"ส่วนลด ฿{float(rp.get('baht') or 0):,.0f}",
            f"บิลที่มีส่วนลดและพิมพ์ซ้ำ {int(rp['bills']):,} บิล ส่วนลดรวม ฿{float(rp.get('baht') or 0):,.0f} ควรตรวจสอบเหตุผลการพิมพ์ซ้ำ",
            "controls", "frcCtlReprint")
    if "cash" in flags:
        cs = C.get("cash") or {}
        add("cash", flags["cash"]["level"], "เงินสดขาด", f"{int(cs.get('short_days') or 0):,} วัน",
            f"รวม ฿{-(float(cs.get('short_total') or 0)):,.0f}", flags["cash"]["text"], "controls", "frcCtlCash")
    g = S.get("gp") or {}
    fc, tgt = g.get("food_cost_pct"), g.get("food_cost_target_pct")
    if fc is not None and tgt is not None and fc > tgt:
        add("food_cost", "fix", "ต้นทุนอาหารเกินเป้า", _pct1(fc), f"เป้า ≤{_pct1(tgt)} · เกิน {fc - tgt:.1f} จุด",
            f"ต้นทุนอาหารร้อยละ {_pct_text(fc)} ของยอดขาย เกินเป้าร้อยละ {_pct_text(tgt)} อยู่ {fc - tgt:.1f} จุด", "cost", "frcGp")
    groups = [x for x in ((D.get("costly") or {}).get("groups") or [])
              if (x.get("vs_avg_pct") or 0) >= T_COSTLY_VS_AVG and (x.get("over_avg_plates") or 0) > 0]
    for x in sorted(groups, key=lambda r: -(r.get("over_avg_plates") or 0))[:COSTLY_SHOWN]:
        add("costly", "watch", f"{x['label']} ต่อลูกค้าสูง", f"+{x['vs_avg_pct']:.0f}%",
            f"เกินเฉลี่ยทุกสาขา {x['over_avg_plates']:,.0f} จาน",
            f"{x['label']} {x.get('per_head') or 0:.2f} จานต่อลูกค้า สูงกว่าเฉลี่ยทุกสาขา ({x.get('avg_per_head') or 0:.2f}) "
            f"ร้อยละ {x['vs_avg_pct']:.0f} คิดเป็น {x['over_avg_plates']:,.0f} จาน", "cost", "frcCostly-" + str(x.get("slug") or ""))
    days = [r for r in ((S.get("sales") or {}).get("daily") or [])
            if r.get("vs_normal_pct") is not None and r["vs_normal_pct"] <= BELOW_NORMAL_PCT]
    if days:
        last = days[-1]
        big = any(r["vs_normal_pct"] <= BIG_BELOW_NORMAL_PCT for r in days)
        add("below_normal", "fix" if big else "watch", "วันที่ยอดต่ำกว่าปกติ", f"{len(days):,} วัน",
            f"ล่าสุด {_th_date(last['date'])} {last['vs_normal_pct']:+.1f}%".replace("-", "−"),
            f"เดือนนี้มี {len(days):,} วันที่ยอดขายต่ำกว่าปกติตั้งแต่ร้อยละ {-BELOW_NORMAL_PCT:.0f} ขึ้นไป "
            f"ล่าสุดวันที่ {_th_date(last['date'])} ต่ำกว่าปกติร้อยละ {-last['vs_normal_pct']:.1f}", "month", "frcD-" + last["date"])
    vo = I.get("voice") or {}
    un, neg = int(vo.get("unreplied") or 0), int(vo.get("negative") or 0)
    if un or neg:
        add("voice", "watch", "รีวิวรอตอบ" if un else "รีวิวติ", f"{un or neg:,} รีวิว",
            (f"ติ {neg:,} · " if neg and un else "") + (f"คะแนน {vo['rating_avg']:.1f}" if vo.get("rating_avg") is not None else "เดือนนี้"),
            f"รีวิวของเดือนนี้ที่ยังไม่มีคำตอบ {un:,} รีวิว รีวิวติ {neg:,} รีวิว", "voice", "frcVoice")
    out.sort(key=lambda f: 0 if f["level"] == "fix" else 1)
    return out


def franchise_overview_data(branch_id: int, month: Optional[str], kind: str = "month", pick: Optional[str] = None) -> dict:
    """The three payloads of the page, side by side (each opens its own connection), and the findings built from them."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as ex:
        fs = ex.submit(franchise_summary_data, branch_id, month)
        fi = ex.submit(franchise_insight_data, branch_id, month)
        fd = ex.submit(_safe, "decide", lambda: _plainj(franchise_decide_payload(branch_id, kind, pick)))
        summary, insight, decide = fs.result(), fi.result(), fd.result()
    return {"summary": summary, "insight": insight, "decide": decide,
            "findings": franchise_findings(summary, insight, decide) if summary.get("available") else []}


@router.get("/api/franchise/overview")
def franchise_overview(request: Request, month: Optional[str] = Query(None, pattern=MONTH_RE), kind: str = Query("month"),
                       pick: Optional[str] = Query(None, pattern=DATE_RE), branch_id: Optional[int] = Query(None)):
    """ร้านของฉัน · the overview cards: summary + insight + เรื่องที่ควรดู + the ควรดู findings (same branch rules as the summary)."""
    if kind not in DECIDE_KINDS:
        raise HTTPException(400, "ช่วงเวลาต้องเป็นสัปดาห์ (week) หรือเดือน (month)")
    with get_db() as con:
        bid, _ = _resolve_branch(con, request, branch_id)
    return franchise_overview_data(bid, month, kind, pick)


# ── บิลยกเลิก / พิมพ์ซ้ำ of the owner's own branch (owner 2026-10-31, "ทำเลย") ──────────────────────────────────────
# The management phone's level 2 (routers/voids.report_voids with `branch`), reached through the franchise rule
# (GET /api/franchise = franchise.view): a franchise login reads its own branch only (_resolve_branch), head office may pass
# branch_id. Month window only - the page is monthly. The classes are the drill-down's (ไม่ออกใหม่ · ออกใหม่แล้ว · บิลเปล่า).
@router.get("/api/franchise/voids")
def franchise_voids(request: Request, month: Optional[str] = Query(None, pattern=MONTH_RE), branch_id: Optional[int] = Query(None)):
    """ร้านของฉัน · การควบคุม › บิลยกเลิก: every cancelled and reprinted bill of the owner's branch in the month."""
    from routers import voids as V
    with get_db() as con:
        _, name = _resolve_branch(con, request, branch_id)
    return V.report_voids(window="month", month=month, date=None, branch=name, group=None, lang="th")


@router.get("/api/franchise/voids/bill")
def franchise_voids_bill(request: Request, receipt: str = Query(...), branch_id: Optional[int] = Query(None)):
    """One reprinted bill of the owner's branch: its lines and payments (routers/voids.report_voids_bill, same pin)."""
    from routers import voids as V
    with get_db() as con:
        _, name = _resolve_branch(con, request, branch_id)
    return V.report_voids_bill(request, branch=name, receipt=receipt)
