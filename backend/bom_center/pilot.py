# -*- coding: utf-8 -*-
"""Phase 2 · Sprint S10 — pilot readiness and the pilot scorecard (docs/BOM_CENTER_PLAN.md §7.3).

READINESS (before the pilot starts): is the centre set up (ingredient master, prices,
sub-recipes), and does each pilot branch have a branch login, an order calendar and
its first (begin) count?

SCORECARD (during the 4-week pilot), per branch, against the plan's pass criteria:
    counts on order days   ≥ 90 %   (a submitted count on the order day of round 1 / 2)
    time to count          ≤ 20 min (median: first draft save → submit)
    gaps with a reason     ≥ 80 %   (flagged lines of periods ending in the window)
    forecast error, A      ≤ 15 %   (weekly MAPE of class-A ingredients, forecast backtest)
plus the share of order cycles the branch actually submitted.
"""
from datetime import date, datetime, timedelta

from .clock import today as bkk_today

PILOT = ("อุบลราชธานี", "อุดรธานี", "ขอนแก่น", "ระยอง")     # owner §10-11
TARGETS = {"count_coverage": 90.0, "count_minutes": 20.0, "reason_coverage": 80.0, "mape_a": 15.0}


def pilot_branches(con) -> list:
    out = []
    for name in PILOT:
        r = con.execute("SELECT branch_id, name FROM branches WHERE name=?", (name,)).fetchone()
        if r:
            out.append({"branch_id": r[0], "name": r[1]})
    return out


def _has(con, table: str) -> bool:
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone())


def readiness(con) -> dict:
    """What still has to be done before (or to keep) the pilot running."""
    from .forecast import calendar
    centre = {
        "ingredients": con.execute("SELECT COUNT(*) FROM bc_ingredient WHERE active=1").fetchone()[0],
        "price_months": [r[0] for r in con.execute(
            "SELECT DISTINCT month FROM bc_ingredient_price ORDER BY month DESC LIMIT 3")],
        "sub_recipes": con.execute("SELECT COUNT(*) FROM bc_prep_recipe").fetchone()[0],
        "releases": con.execute("SELECT COUNT(*) FROM bc_release WHERE kind='monthly' AND status='released'").fetchone()[0],
    }
    centre_steps = [
        ("master", centre["ingredients"] > 0, "เติมทะเบียนวัตถุดิบ (BoM Center → ภาพรวม → “เติมจากข้อมูลในระบบ”)"),
        ("prices", bool(centre["price_months"]), "นำเข้าราคาซื้อจริงเดือนล่าสุด (BoM Center → ราคารายเดือน)"),
        ("sub_recipes", centre["sub_recipes"] > 0, "นำเข้าสูตรกึ่งสำเร็จจากไฟล์ครัว (BoM Center → สูตรกึ่งสำเร็จ) — ไม่บังคับ แต่ทำให้สั่งวัตถุดิบดิบข้างในได้"),
    ]
    users = {}
    if _has(con, "app_user"):
        for u, b, active in con.execute("SELECT username, branch, active FROM app_user WHERE role='branch'"):
            users.setdefault(b, []).append({"username": u, "active": bool(active)})
    rows = []
    for b in pilot_branches(con):
        bid = b["branch_id"]
        cal = calendar(con, bid)
        first = con.execute("SELECT MIN(count_date), MAX(count_date), COUNT(*) FROM bc_stock_count WHERE branch_id=?"
                            " AND COALESCE(status,'submitted')='submitted'", (bid,)).fetchone()
        last_order = con.execute("SELECT MAX(cycle_date) FROM bc_order WHERE branch_id=? AND status<>'cancelled'",
                                 (bid,)).fetchone()[0]
        steps = [
            ("account", any(u["active"] for u in users.get(b["name"], [])),
             "สร้างบัญชีสาขา (ตั้งค่า → ผู้ใช้งาน → บทบาท “สาขา” + เลือกสาขา)"),
            ("calendar", all(cal[r]["filled"] for r in ("1", "2", "salmon")),
             "ฝ่ายขายตั้งวันสั่ง 3 รอบ (BoM Center → แนะนำยอดสั่ง → ตารางรอบสั่ง)"),
            ("begin_count", bool(first[0]), "สาขานับต้นงวดครั้งแรก (สาขาของฉัน → นับสต๊อก)"),
        ]
        rows.append({"branch_id": bid, "branch": b["name"], "accounts": users.get(b["name"], []),
                     "first_count": first[0], "last_count": first[1], "counts": first[2], "last_order": last_order,
                     "steps": [{"key": k, "done": ok, "todo": t} for k, ok, t in steps],
                     "ready": all(ok for _, ok, _ in steps)})
    return {"centre": centre, "centre_steps": [{"key": k, "done": ok, "todo": t} for k, ok, t in centre_steps],
            "branches": rows, "pilot": list(PILOT), "missing_branches": [n for n in PILOT
                                                                        if n not in {r["branch"] for r in rows}]}


def _minutes(a: str, b: str):
    try:
        da, db = datetime.fromisoformat(a), datetime.fromisoformat(b)
    except (TypeError, ValueError):
        return None
    return (db - da).total_seconds() / 60.0


def scorecard(con, branch_id: int, weeks: int = 4, as_of: date = None) -> dict:
    from .forecast import _wd, backtest, calendar
    from .stock import periods as _periods
    from .variance import period
    as_of = as_of or bkk_today()
    start = as_of - timedelta(days=7 * weeks - 1)
    # the pilot starts at the branch's first count — days before it are not held against it
    first = con.execute("SELECT MIN(count_date) FROM bc_stock_count WHERE branch_id=?"
                        " AND COALESCE(status,'submitted')='submitted'", (branch_id,)).fetchone()[0]
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
    counted_days = {r[0] for r in con.execute(
        "SELECT count_date FROM bc_stock_count WHERE branch_id=? AND COALESCE(status,'submitted')='submitted'"
        " AND count_date BETWEEN ? AND ?", (branch_id, start.isoformat(), as_of.isoformat()))}
    submitted = {(r[0], r[1]) for r in con.execute(
        "SELECT round, cycle_date FROM bc_order WHERE branch_id=? AND status IN ('submitted','processed','received')"
        " AND cycle_date BETWEEN ? AND ?", (branch_id, start.isoformat(), as_of.isoformat()))}
    mins = sorted(m for m in (_minutes(a, b) for a, b in con.execute(
        "SELECT created_at, submitted_at FROM bc_stock_count WHERE branch_id=? AND status='submitted'"
        " AND count_date BETWEEN ? AND ?", (branch_id, start.isoformat(), as_of.isoformat()))) if m is not None)
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

    def pct(a, b):
        return round(100.0 * a / b, 1) if b else None
    metrics = {
        "count_coverage": pct(sum(1 for _, d in cycles if d in counted_days), len(cycles)),
        "count_minutes": round(mins[len(mins) // 2], 1) if mins else None,
        "reason_coverage": pct(with_reason, flagged) if flagged else None,
        "mape_a": mape_a,
        "order_submission": pct(sum(1 for c in cycles if c in submitted), len(cycles)),
    }
    passes = {
        "count_coverage": None if metrics["count_coverage"] is None else metrics["count_coverage"] >= TARGETS["count_coverage"],
        "count_minutes": None if metrics["count_minutes"] is None else metrics["count_minutes"] <= TARGETS["count_minutes"],
        "reason_coverage": None if metrics["reason_coverage"] is None else metrics["reason_coverage"] >= TARGETS["reason_coverage"],
        "mape_a": None if metrics["mape_a"] is None else metrics["mape_a"] <= TARGETS["mape_a"],
    }
    known = [v for v in passes.values() if v is not None]
    return {"branch_id": branch_id, "from": start.isoformat(), "to": as_of.isoformat(), "weeks": weeks,
            "cycles": len(cycles), "counts": len(counted_days), "flagged": flagged, "metrics": metrics,
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
