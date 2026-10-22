# -*- coding: utf-8 -*-
"""Phase 2 · Sprint S10 — pilot readiness and the pilot scorecard (docs/BOM_CENTER_PLAN.md §7.3).

READINESS (before the pilot starts): is the centre set up (ingredient master, prices,
sub-recipes), and does each pilot branch have a branch login, an order calendar and
its first (begin) count?

SCORECARD (during the pilot, five branches from 1 Nov 2026), per branch, against the pass criteria:
    issue slips confirmed  ≥ 95 %   (days of the window with a confirmed ใบตัดเบิก)
    slips confirmed on the day ≥ 90 %   (of the confirmed ones, those not marked late)
    gaps with a reason     ≥ 80 %   (flagged lines of periods ending in the window)
    forecast error, A      ≤ 15 %   (weekly MAPE of class-A ingredients, forecast backtest)
plus the share of order cycles the branch actually submitted.
"""
from datetime import date, datetime, timedelta

from .clock import today as bkk_today

PILOT = ("เชียงใหม่", "อุบลราชธานี", "อุดรธานี", "โคราช1", "สุรินทร์")     # owner 2026-10-22: 5 branches, opening count 1 Nov 2026
TARGETS = {"issue_coverage": 95.0, "issue_on_time": 90.0, "reason_coverage": 80.0, "mape_a": 15.0}


def pilot_branches(con) -> list:
    out = []
    for name in PILOT:
        r = con.execute("SELECT branch_id, name FROM branches WHERE name=?", (name,)).fetchone()
        if r:
            out.append({"branch_id": r[0], "name": r[1]})
    return out


def active_branches(con) -> list:
    """[{branch_id, name}] of every ACTIVE branch (branches.active, 'ALL' left out), by name - the one definition todo._branch_rows
    uses. PR-U2 (30-branch rollout): the default scope of the readiness list and of หน้างาน; the five PILOT branches stay the scope of
    the pilot scorecard only."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(branches)")}
    sql = "SELECT branch_id, name FROM branches WHERE name <> 'ALL'" + (" AND COALESCE(active, 1) = 1" if "active" in cols else "")
    return [{"branch_id": r[0], "name": r[1]} for r in con.execute(sql + " ORDER BY name")]


def branch_accounts(con) -> list:
    """Every ACTIVE branch account (role 'branch' with a branch) -> [{branch_id, name, usernames}], one row per branch,
    sorted by name. Batch 3 (30-branch rollout): the reminders and the adoption grid follow the accounts that exist,
    not the five pilot branches. A branch that has no active account is not listed; an account whose branch is
    unknown or inactive is skipped."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(branches)")}
    try:
        users = con.execute("SELECT username, branch FROM app_user WHERE role='branch' AND COALESCE(active,1)=1"
                            " AND COALESCE(branch,'')<>'' ORDER BY username").fetchall()
    except Exception:                                         # no user table (a fixture without auth)
        return []
    by_name = {}
    for u, b in users:
        by_name.setdefault(b, []).append(u)
    sql = "SELECT branch_id, name FROM branches WHERE name<>'ALL'" + (" AND COALESCE(active,1)=1" if "active" in cols else "")
    out = [{"branch_id": r[0], "name": r[1], "usernames": by_name[r[1]]}
           for r in con.execute(sql + " ORDER BY name") if r[1] in by_name]
    return out


def _has(con, table: str) -> bool:
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone())


def readiness(con) -> dict:
    """What still has to be done before a branch starts (PR-U2: every active branch, not the four pilot branches)."""
    from .forecast import calendar
    centre = {
        "ingredients": con.execute("SELECT COUNT(*) FROM bc_ingredient WHERE active=1").fetchone()[0],
        "price_months": [r[0] for r in con.execute(
            "SELECT DISTINCT month FROM bc_ingredient_price ORDER BY month DESC LIMIT 3")],
        "sub_recipes": con.execute("SELECT COUNT(*) FROM bc_prep_recipe").fetchone()[0],
        "releases": con.execute("SELECT COUNT(*) FROM bc_release WHERE kind='monthly' AND status='released'").fetchone()[0],
    }
    centre_steps = [
        ("master", centre["ingredients"] > 0, "เติมทะเบียนวัตถุดิบ (ฝ่ายวิเคราะห์ → ทะเบียนวัตถุดิบ)"),
        ("prices", bool(centre["price_months"]), "นำเข้าราคาซื้อจริงเดือนล่าสุด (ฝ่ายวิเคราะห์ → ราคารายเดือน)"),
        ("sub_recipes", centre["sub_recipes"] > 0, "นำเข้าสูตรกึ่งสำเร็จ (ฝ่ายวิเคราะห์ → สูตรกึ่งสำเร็จ) · ไม่บังคับ"),
    ]
    users = {}
    if _has(con, "app_user"):
        for u, b, active in con.execute("SELECT username, branch, active FROM app_user WHERE role='branch'"):
            users.setdefault(b, []).append({"username": u, "active": bool(active)})
    rows = []
    for b in active_branches(con):
        bid = b["branch_id"]
        cal = calendar(con, bid)
        first = con.execute("SELECT MIN(count_date), MAX(count_date), COUNT(*) FROM bc_stock_count WHERE branch_id=?"
                            " AND COALESCE(status,'submitted')='submitted' AND kind IN ('begin','end')",
                            (bid,)).fetchone()          # the month-end count; a count made while ordering is not the begin stock
        last_order = con.execute("SELECT MAX(cycle_date) FROM bc_order WHERE branch_id=? AND status<>'cancelled'",
                                 (bid,)).fetchone()[0]
        steps = [
            ("account", any(u["active"] for u in users.get(b["name"], [])),
             "สร้างบัญชีผู้ใช้ของสาขา (ตั้งค่า → ผู้ใช้งาน → บทบาท “สาขา” และเลือกสาขา)"),
            ("calendar", all(cal[r]["filled"] for r in ("1", "2", "salmon")),
             "ประสานงานขายกำหนดวันส่งของ 3 รอบ (ประสานงานขาย → รอบส่งของสาขา)"),
            ("begin_count", bool(first[0]), "สาขาตรวจนับต้นงวดครั้งแรก (สาขา → ตรวจนับสิ้นเดือน)"),
        ]
        rows.append({"branch_id": bid, "branch": b["name"], "accounts": users.get(b["name"], []),
                     "first_count": first[0], "last_count": first[1], "counts": first[2], "last_order": last_order,
                     "steps": [{"key": k, "done": ok, "todo": t} for k, ok, t in steps],
                     "ready": all(ok for _, ok, _ in steps)})
    return {"centre": centre, "centre_steps": [{"key": k, "done": ok, "todo": t} for k, ok, t in centre_steps],
            "branches": rows, "pilot": list(PILOT), "missing_branches": [n for n in PILOT
                                                                        if not any(b["name"] == n for b in pilot_branches(con))]}


def _minutes(a: str, b: str):
    try:
        da, db = datetime.fromisoformat(a), datetime.fromisoformat(b)
    except (TypeError, ValueError):
        return None
    return (db - da).total_seconds() / 60.0


def scorecard(con, branch_id: int, weeks: int = 4, as_of: date = None) -> dict:
    from .forecast import _wd, backtest, calendar
    from .issues import status_days
    from .stock import periods as _periods
    from .variance import period
    as_of = as_of or bkk_today()
    start = as_of - timedelta(days=7 * weeks - 1)
    # the pilot starts at the branch's first month-end count or first issue slip — days before it are not held against it
    from .started import branch_start                 # PR-U1: the one branch start date
    first = branch_start(con, branch_id)
    started = first is not None or con.execute(
        "SELECT 1 FROM bc_order WHERE branch_id=? AND status IN ('submitted','processed','received')",
        (branch_id,)).fetchone() is not None
    if first and date.fromisoformat(first) > start:
        start = date.fromisoformat(first)
    cal = calendar(con, branch_id)
    # order days of the two regular rounds in the window (the plan's "count before ordering")
    cycles = []
    d = start
    while d <= as_of:
        for rnd in ("1", "2"):
            if _wd(d) == int(cal[rnd]["order_weekday"]):
                cycles.append((rnd, d.isoformat()))
        d += timedelta(days=1)
    month_counts = con.execute(
        "SELECT COUNT(*) FROM bc_stock_count WHERE branch_id=? AND COALESCE(status,'submitted')='submitted'"
        " AND kind IN ('begin','end') AND count_date BETWEEN ? AND ?",
        (branch_id, start.isoformat(), as_of.isoformat())).fetchone()[0]
    # the daily issue slips: one verdict per calendar day of the window; the day still open (as_of, not yet
    # confirmed) is not judged
    days = status_days(con, branch_id, start, as_of) if start <= as_of else []
    if days and days[-1]["status"] in ("missing", "draft") and days[-1]["date"] == as_of.isoformat():
        days = days[:-1]
    slips = {"days": len(days), "confirmed": sum(1 for x in days if x["status"] == "confirmed"),
             "late": sum(1 for x in days if x["status"] == "late"), "draft": sum(1 for x in days if x["status"] == "draft"),
             "missing": sum(1 for x in days if x["status"] == "missing")}
    submitted = {(r[0], r[1]) for r in con.execute(
        "SELECT round, cycle_date FROM bc_order WHERE branch_id=? AND status IN ('submitted','processed','received')"
        " AND cycle_date BETWEEN ? AND ?", (branch_id, start.isoformat(), as_of.isoformat()))}
    flagged = with_reason = 0
    for b, e in _periods(con, branch_id):
        if start.isoformat() <= e["date"] <= as_of.isoformat():
            p = period(con, branch_id, e["count_id"])
            for r in p["rows"]:
                if r["status"] in ("over", "under", "negative", "no_bom"):
                    flagged += 1
                    with_reason += 1 if r.get("reason") else 0
    bt = {}
    try:
        bt = backtest(con, branch_id, weeks=min(weeks, 4), as_of=as_of)
    except Exception:
        bt = {"available": False}
    mape_a = (bt.get("mape_by_class") or {}).get("A") if bt.get("available") else None
    try:                                                   # batch 4b (2.5): what the orders achieved, beside the forecast error
        from .outcome import window as _outcome
        oc = _outcome(con, branch_id, start, as_of, as_of)
        oc.pop("rows", None)
    except Exception:
        oc = {"deliveries": 0, "measured": 0, "cover_days": None, "stockouts": 0, "stockout_rounds": 0}

    def pct(a, b):
        return round(100.0 * a / b, 1) if b else None
    metrics = {
        "issue_coverage": pct(slips["confirmed"] + slips["late"], slips["days"]),
        "issue_on_time": pct(slips["confirmed"], slips["confirmed"] + slips["late"]),
        "reason_coverage": pct(with_reason, flagged) if flagged else None,
        "mape_a": mape_a,
        "cover_days": oc["cover_days"],
        "stockouts": oc["stockouts"] if oc["measured"] else None,
        "order_submission": pct(sum(1 for c in cycles if c in submitted), len(cycles)),
    }
    passes = {
        "issue_coverage": None if metrics["issue_coverage"] is None else metrics["issue_coverage"] >= TARGETS["issue_coverage"],
        "issue_on_time": None if metrics["issue_on_time"] is None else metrics["issue_on_time"] >= TARGETS["issue_on_time"],
        "reason_coverage": None if metrics["reason_coverage"] is None else metrics["reason_coverage"] >= TARGETS["reason_coverage"],
        "mape_a": None if metrics["mape_a"] is None else metrics["mape_a"] <= TARGETS["mape_a"],
    }
    known = [v for v in passes.values() if v is not None]
    return {"branch_id": branch_id, "from": start.isoformat(), "to": as_of.isoformat(), "weeks": weeks,
            "cycles": len(cycles), "counts": month_counts, "slips": slips, "flagged": flagged, "metrics": metrics, "outcome": oc,
            "targets": TARGETS, "passes": passes,
            "started": started,
            "verdict": ("not_started" if not started else
                        "pass" if known and len(known) == 4 and all(known) else
                        "fail" if any(v is False for v in known) else "pending")}


def overview(con, weeks: int = 4) -> dict:
    rows = []
    for b in pilot_branches(con):
        s = scorecard(con, b["branch_id"], weeks)
        s["branch"] = b["name"]
        rows.append(s)
    return {"weeks": weeks, "branches": rows, "targets": TARGETS}
