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
    from routers.reports import report_management
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


def _daily(con, sales: dict, branch: str, last_day: Optional[str]):
    """The last day of the report's window and the 30 days ending on it: one grouped query with the dashboard's own
    revenue rule (rules.net_revenue_sum: bill total / 1.07 less service charge). A day without bills is null."""
    from rules import net_revenue_sum, scope_filter
    if not last_day:
        return
    base, params = ["t.source='Easy'", "b.name!='ALL'"], {}
    scope_filter(base, params, branch, None)
    end = date.fromisoformat(last_day)
    start = end - timedelta(days=29)
    rows = {r[0]: (r[1], r[2]) for r in con.execute(
        f"SELECT date(t.sold_at), ROUND({net_revenue_sum('t')},2), COUNT(*) FROM sales_transaction t "
        f"JOIN branches b ON b.branch_id=t.branch_id WHERE {' AND '.join(base)} "
        f"AND t.sold_at >= :s AND t.sold_at < :e GROUP BY date(t.sold_at)",
        {**params, "s": start.isoformat(), "e": (end + timedelta(days=1)).isoformat()})}
    series = []
    for i in range(30):
        d = (start + timedelta(days=i)).isoformat()
        series.append({"day": d, "revenue": _num(rows[d][0], 2) if d in rows else None})
    sales["series_30d"] = series
    sales["last_day"] = {"date": last_day, "revenue": _num(rows[last_day][0], 2) if last_day in rows else 0.0,
                         "bills": rows[last_day][1] if last_day in rows else 0}


def _top_menus(branch: str, month: str) -> list:
    from routers.sales import top_ordered
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
            _safe("daily", lambda: _daily(con, sales, name, last_day))
        if gp and gp.get("gp_pct") is not None and sel:
            _gp_target(gp, name, sel)
        top = _safe("top menus", lambda: _top_menus(name, sel), []) if sel and sales is not None else []
        ing = _safe("ingredients", lambda: _ingredients(con, branch_id, sel), None) if sel else None
        disc = _safe("discipline", lambda: _discipline(con, branch_id, sel), None) if sel else None
    months = [{"month": m["month"], "label": m.get("label"), "mtd": bool(m.get("mtd"))} for m in (rep.get("months") or [])]
    return {"branch": name, "month": sel, "label": rep.get("label"), "months": months,
            "available": bool(sales), "sales": sales, "gp": gp, "ingredients": ing, "discipline": disc,
            "top_menus": top, "discount_pct": discount_pct,
            "verdict": _verdict(sales, gp, ing, disc, discount_pct, policy)}


@router.get("/api/franchise/summary")
def franchise_summary(request: Request, month: Optional[str] = Query(None, pattern=MONTH_RE),
                      branch_id: Optional[int] = Query(None)):
    """ร้านของฉัน: one branch's month at a glance for its owner (see the module docstring)."""
    with get_db() as con:
        bid, _ = _resolve_branch(con, request, branch_id)
    return franchise_summary_data(bid, month)
