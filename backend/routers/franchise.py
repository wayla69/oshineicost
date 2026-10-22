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
  GET /api/franchise/summary.xlsx  (F1) the same month as an Excel file: สรุป / รายวัน / 12 เดือน / เมนูขายดี. The Fly image has
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
  daily[].vs_last_week_pct  net sales of the day against the same weekday seven days earlier (null when either has no sales)
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
  discount              <= policy good ; > policy watch ; > 2 x policy fix
"""
import calendar as _cal
import json
import re
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

import auth as _auth
from core import get_db

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
        raise HTTPException(403, detail="บัญชีนี้ยังไม่ได้ผูกกับสาขา กรุณาแจ้งผู้ดูแลระบบ", headers={"X-Denied": "role"})
    if scoped:
        r = con.execute("SELECT branch_id, name FROM branches WHERE name=?", (scoped,)).fetchone()
        if not r:
            raise HTTPException(403, detail="บัญชีนี้ผูกกับสาขาที่ไม่พบในระบบ กรุณาแจ้งผู้ดูแลระบบ", headers={"X-Denied": "role"})
        if branch_id is not None and int(branch_id) != int(r[0]):
            raise HTTPException(403, detail="บัญชีเจ้าของสาขาดูได้เฉพาะสาขาของตนเอง", headers={"X-Denied": "role"})
        return int(r[0]), r[1]
    if branch_id is None:
        raise HTTPException(400, "กรุณาเลือกสาขา")
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
    H, G, P = rep.get("headline") or {}, rep.get("gp") or {}, rep.get("pace") or {}
    last = (rep.get("range") or [None, None])[1]
    sales = {"mtd": _num(H.get("revenue"), 2), "bills": H.get("bills"), "per_bill": _num(H.get("avg_ticket"), 0),
             "mtd_vs_last_month_pct": _num(P.get("mom_pct")), "mom_basis": P.get("mom_basis"),
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
    from rules import closed_day_clause, net_revenue_sum
    if not last_day:
        return
    end = date.fromisoformat(last_day)
    first = end.replace(day=1)
    start = min(end - timedelta(days=29), first - timedelta(days=7))      # the table's first week compares with the month before
    rows = {r[0]: (r[1], r[2], r[3]) for r in con.execute(
        f"SELECT date(t.sold_at), ROUND({net_revenue_sum('t')},2), COUNT(*), COALESCE(SUM(t.customer_count),0) "
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
    table = []
    for i in range((end - first).days + 1):
        dd = first + timedelta(days=i)
        d, wk = dd.isoformat(), (dd - timedelta(days=7)).isoformat()
        r = rows.get(d)
        cur, prev = rev(d), rev(wk)
        table.append({"date": d, "weekday": TH_DOW[dd.weekday()], "revenue": cur,
                      "bills": r[1] if r else None, "customers": int(r[2]) if r else None,
                      "per_bill": _num(r[0] / r[1], 0) if r and r[1] else None,
                      "vs_last_week_pct": _num(100.0 * (cur - prev) / prev) if cur is not None and prev else None})
    sales["daily"] = table
    sales["daily_through"] = table[-1]["date"] if table else None


def _trend_12m(branch: str) -> dict:
    """The last 12 closed months and the month in progress, each on its own (never a sum over months), from the phone's
    month-by-month series (reports.report_management_months): the report's sums, its weekday-fair MoM for the part month
    and its GP basis, so a month reads here as it does in the management report."""
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


def _ingredients(con, bid: int, month: str) -> dict:
    from bom_center import issue_reports as IR
    from bom_center import variance as VA
    from bom_center.stock import periods
    first, last = _month_bounds(month)
    out = {"latest_period": None, "waste_pct_month": None, "streak_flag": False, "streak_days": 0}
    cut = (last + timedelta(days=1)).isoformat()
    ps = [p for p in periods(con, bid) if p[1]["date"] < cut]
    if ps:
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


def _verdict(sales, gp, ing, disc, discount_pct, policy) -> dict:
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
            word = "ใช้เกินสูตร" if p > 0 else "ใช้น้อยกว่าสูตร"
            if abs(p) <= T_ING_GOOD:
                add("good", f"การใช้วัตถุดิบรอบนับล่าสุดใกล้เคียงสูตร (ส่วนต่างร้อยละ {_pct_text(p)})")
            elif abs(p) > T_ING_FIX:
                add("fix", f"วัตถุดิบรอบนับล่าสุด{word}ร้อยละ {_pct_text(p)} (฿{abs(lp.get('diff_value') or 0):,.0f}) ควรตรวจสอบหน้างาน")
            else:
                add("watch", f"วัตถุดิบรอบนับล่าสุด{word}ร้อยละ {_pct_text(p)} (฿{abs(lp.get('diff_value') or 0):,.0f})")
        if ing.get("streak_flag"):
            add("fix", f"มีวัตถุดิบหลักที่เบิกเกินสูตรติดต่อกันหลายวัน ({ing['streak_days']} วันในเดือนนี้) ควรตรวจสอบหน้างาน")
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
                add("fix", f"ใบสั่งซื้อส่งทันเวลาตัดรอบเพียงร้อยละ {_pct_text(o)} การส่งล่าช้ามีค่าปรับ ควรแจ้งทีมสาขา")
            elif o < T_ORD_WATCH:
                add("watch", f"ใบสั่งซื้อส่งทันเวลาตัดรอบร้อยละ {_pct_text(o)}")
        r = disc.get("receipts_on_time")
        if r is not None and r < T_RECV_WATCH:
            add("watch", f"การบันทึกรับสินค้าทันกำหนดร้อยละ {_pct_text(r)} ควรบันทึกรับสินค้าภายในวันถัดจากวันส่ง")
        n = disc.get("claims_month")
        if n is not None and n >= T_CLAIMS_WATCH:
            add("watch", f"มีการแจ้งเคลมสินค้า {n} ครั้งในเดือนนี้")
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
        target = _safe("target", lambda: _target(con, rep, sales, name, sel)) if sales is not None and sel else None
        empty = {"months": [], "latest": None}
        trend = _safe("trend", lambda: _trend_12m(name), empty) if sales is not None else empty
        top = _safe("top menus", lambda: _top_menus(name, sel), []) if sel and sales is not None else []
        ing = _safe("ingredients", lambda: _ingredients(con, branch_id, sel), None) if sel else None
        disc = _safe("discipline", lambda: _discipline(con, branch_id, sel), None) if sel else None
    months = [{"month": m["month"], "label": m.get("label"), "mtd": bool(m.get("mtd"))} for m in (rep.get("months") or [])]
    return {"branch": name, "month": sel, "label": rep.get("label"), "months": months,
            "available": bool(sales), "sales": sales, "gp": gp, "ingredients": ing, "discipline": disc,
            "top_menus": top, "discount_pct": discount_pct, "target": target, "trend_12m": trend,
            "verdict": _verdict(sales, gp, ing, disc, discount_pct, policy)}


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


def franchise_workbook(d: dict):
    """-> openpyxl Workbook with the sheets สรุป / รายวัน / 12 เดือน / เมนูขายดี for one summary payload."""
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
    _sheet(ws, "รายวัน", ["วันที่", "วัน", "ยอดขาย (ไม่รวม VAT)", "จำนวนบิล", "จำนวนลูกค้า", "เฉลี่ยต่อบิล", "เทียบวันเดียวกันสัปดาห์ก่อน (%)"],
           f"{head} · เฉพาะวันที่ปิดการขายแล้ว")
    _xl_rows(ws, [(x["date"], x["weekday"], x["revenue"], x["bills"], x["customers"], x["per_bill"], x["vs_last_week_pct"])
                  for x in s.get("daily") or []], [None, None, _FX_BAHT, "#,##0", "#,##0", _FX_BAHT, _FX_PCT])

    ws = wb.create_sheet()
    _sheet(ws, "12 เดือน", ["เดือน", "สถานะ", "ยอดขาย (ไม่รวม VAT)", "เทียบเดือนก่อน (%)", "เทียบปีก่อน (%)", "จำนวนลูกค้า",
                            "เฉลี่ยต่อบิล", "กำไรขั้นต้น (%)"],
           f"ร้านของฉัน · สาขา{d.get('branch') or ''} · แต่ละเดือนแยกกัน เดือนที่ยังไม่จบเทียบช่วงวันเดียวกัน")
    _xl_rows(ws, [(m.get("label") or m["month"], f"สะสมถึง {m['through']}" if m.get("partial") and m.get("through") else "ครบเดือน",
                   m.get("revenue"), m.get("mom_pct"), m.get("yoy_pct"), m.get("customers"), m.get("per_bill"), m.get("gp_pct"))
                  for m in (d.get("trend_12m") or {}).get("months") or []],
             [None, None, _FX_BAHT, _FX_PCT, _FX_PCT, "#,##0", _FX_BAHT, _FX_PCT])

    ws = wb.create_sheet()
    _sheet(ws, "เมนูขายดี", ["อันดับ", "เมนู", "จำนวนจาน"], f"{head} · 10 อันดับตามจำนวนจาน")
    ws.column_dimensions["B"].width = 48
    _xl_rows(ws, [(i + 1, x["name"], x["plates"]) for i, x in enumerate(d.get("top_menus") or [])], [None, None, "#,##0"])
    for w in wb.worksheets:
        w.freeze_panes = "A3"
        w["A1"].font = Font(italic=True, color="5F6C80")
    return wb


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
    return _xlsx(franchise_workbook(d), _fname("ร้านของฉัน", d.get("branch"), d.get("month")))


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
T_CANCEL_WATCH, T_CANCEL_FIX = 2.0, 3.0        # voided bills, % of all bills (the บิลยกเลิก page's bands: normal < 2 %)
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
    ca = extra_cash(month=sel, branch=name, group=None)
    s = ca.get("summary") or {}
    days = [{"date": x["day"], "over_short": _num(x.get("over_short"), 0)}
            for x in (extra_cash_detail(branch=name, month=sel).get("days") or [])]
    cash = {"net": _num(s.get("net"), 0) if days else None, "short_total": _num(s.get("short_total"), 0) if days else None,
            "over_total": _num(s.get("over_total"), 0) if days else None, "short_days": s.get("short_days") if days else None,
            "days_counted": len(days), "days": days}
    return {"discount": discount, "voids": voids, "cash": cash, "flags": _control_flags(discount, voids, cash)}


_TH_MON = ("ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.")


def _th_date(iso) -> str:
    try:
        d = date.fromisoformat(str(iso)[:10])
        return f"{d.day} {_TH_MON[d.month - 1]}"
    except ValueError:
        return str(iso or "")


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
    if r is not None and voids.get("bills"):
        what = f"บิลยกเลิก {voids['bills']:,} ใบ (฿{voids['baht'] or 0:,.0f}) คิดเป็นร้อยละ {_pct_text(r)} ของบิลทั้งหมด"
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
            "rating_change": round(avg - pavg, 2) if avg is not None and pavg is not None else None, "latest": reviews}


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
    the last finished day's net sales against the same weekday a week earlier (summary.sales.daily), the month so far against
    the target to date (summary.target, F1) and the number of ต้องแก้ lines (the verdict's, plus the controls' voids and cash;
    the discount line is already the verdict's own, so it is counted once). None when the summary has no finished day."""
    s = (d or {}).get("sales") or {}
    ld = (s.get("last_day") or {}).get("date")
    if not d or not d.get("available") or not ld:
        return None
    row = next((x for x in (s.get("daily") or []) if x.get("date") == ld), None) or {}
    rev = row.get("revenue") if row else (s.get("last_day") or {}).get("revenue")
    wk = row.get("vs_last_week_pct")
    today = today or _thai_today()
    dd = date.fromisoformat(ld)
    when = "เมื่อวาน" if dd == today - timedelta(days=1) else "วันล่าสุดที่ปิดการขาย"
    chip = f" {'▲' if wk >= 0 else '▼'}{abs(wk):.1f}% เทียบวัน{TH_DOW[dd.weekday()]}ก่อน" if wk is not None else ""
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
