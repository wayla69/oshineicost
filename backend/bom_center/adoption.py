# -*- coding: utf-8 -*-
"""All-branches adoption grid (batch 3, 4.7 of the improvement proposal, owner approved 2026-10-12).

One row per ACTIVE BRANCH ACCOUNT (users with role 'branch' and a branch - pilot.branch_accounts, not the four pilot
branches), for the last 7 days (today included, today's open slip and order not judged yet), for Sales Support, Operations
and management. Nothing here is a new definition; each column reads the one the pilot and the compliance reports already use:

    ใบตัดเบิกตรงวัน        issue_reports.log summary: slips confirmed within their own day  ÷  days the branch traded
                           (pilot.scorecard's issue_on_time counted over trading days; a late or missing slip is a miss)
    ส่งใบสั่งซื้อตรงเวลา    hub.compliance rows: cycles submitted by the 13:15 cutoff  ÷  cycles due
                           (pilot.scorecard's order_submission, with the late flag - bc_order.late - counted as a miss)
    รับสินค้าบันทึกแล้ว     orders the warehouse sent whose delivery day fell in the window and has come  →  how many the
                           branch has recorded as received (todo.receive_pending's own condition: processed, delivery ≤ today)
    ใช้งานล่าสุด           the newest of the branch accounts' last login and the branch's last write (slip, order, receipt, count)

Traffic light per column and per branch: green ≥ 90 %, yellow 60-89 %, red < 60 %; a branch with no activity at all in the
window is red. The branch's light is the worst of its columns; the grid is sorted worst first.
"""
from datetime import date, timedelta

from . import hub as _hub
from . import issue_reports as _ir
from .clock import today as bkk_today
from .pilot import branch_accounts
from .started import branch_start

DAYS = 7
GREEN_MIN = 90.0
YELLOW_MIN = 60.0
RANK = {"red": 0, "yellow": 1, "green": 2, "not_started": 3}
LIGHT_TH = {"green": "เขียว", "yellow": "เหลือง", "red": "แดง", "not_started": "ยังไม่เริ่มใช้ระบบ"}
SUBMITTED = ("submitted", "processed", "received")


def light(pct):
    """None for a column with nothing to judge (no trading day, no order due, nothing delivered)."""
    if pct is None:
        return None
    return "green" if pct >= GREEN_MIN else "yellow" if pct >= YELLOW_MIN else "red"


def _pct(a, b):
    return round(100.0 * a / b, 1) if b else None


def _last_login(con, usernames: list):
    if not usernames:
        return None
    try:
        r = con.execute("SELECT MAX(ts) FROM recon_audit_log WHERE entity='auth' AND action='login' AND by IN (%s)"
                        % ",".join("?" * len(usernames)), tuple(usernames)).fetchone()
    except Exception:                                   # the table is created by the first login
        return None
    return _ir._bkk(r[0])[1] if r and r[0] else None


def _last_write(con, bid: int):
    """The newest stamp the branch itself wrote: a slip action, an order step, a receipt, a count."""
    best = None
    for sql in ("SELECT MAX(at) FROM bc_issue_log WHERE branch_id=?",
                "SELECT MAX(COALESCE(received_at, submitted_at, confirmed_at)) FROM bc_order WHERE branch_id=?",
                "SELECT MAX(created_at) FROM bc_receipt WHERE branch_id=? AND source IN ('branch','order')",
                "SELECT MAX(created_at) FROM bc_stock_count WHERE branch_id=?"):
        try:
            v = con.execute(sql, (bid,)).fetchone()[0]
        except Exception:
            continue
        stamp = _ir._bkk(v)[1] if v else None
        if stamp and (best is None or stamp > best):
            best = stamp
    return best


def grid(con, today: date = None, days: int = DAYS) -> dict:
    today = today or bkk_today()
    d1 = today
    d0 = today - timedelta(days=days - 1)
    branches = branch_accounts(con)
    ids = [b["branch_id"] for b in branches]
    # ── ใบตัดเบิก: the lateness log's one-line-per-branch summary ──
    slips = {s["branch_id"]: s for s in _ir.log(con, d0, d1, None, today)["summary"]}
    # ── ใบสั่งซื้อ: the compliance rows of the window ──
    comp = _hub.compliance(con, d0.isoformat(), d1.isoformat(), branch_ids=ids, with_issues=False)["rows"] if ids else []
    due, on_time = {}, {}
    for r in comp:
        sent = r.get("order_status") in SUBMITTED
        if not sent and (r["cycle_date"] >= today.isoformat() or r["status"] == "not_started"):
            continue                                    # today's (or a coming) cycle that is not in yet is not due yet; nor one before the start
        due[r["branch_id"]] = due.get(r["branch_id"], 0) + 1
        if sent and not r.get("late"):
            on_time[r["branch_id"]] = on_time.get(r["branch_id"], 0) + 1
    # ── รับสินค้า: orders sent by the warehouse whose delivery day is in the window ──
    delivered, received = {}, {}
    if ids:
        lo = (d0 - timedelta(days=14)).isoformat()
        for oid, bid, rnd, cyc, ddate, st in con.execute(
                "SELECT order_id, branch_id, round, cycle_date, delivery_date, status FROM bc_order"
                " WHERE status IN ('processed','received') AND cycle_date BETWEEN ? AND ? AND branch_id IN (%s)"
                % ",".join("?" * len(ids)), (lo, d1.isoformat(), *ids)).fetchall():
            dlv = _hub.delivery_of(con, bid, rnd, cyc, ddate)
            if d0.isoformat() <= dlv <= d1.isoformat():
                delivered[bid] = delivered.get(bid, 0) + 1
                if st == "received":
                    received[bid] = received.get(bid, 0) + 1
    rows = []
    since = f"{d0.isoformat()} 00:00"
    for b in branches:
        bid = b["branch_id"]
        s = slips.get(bid) or {}
        issues = {"on_time": s.get("on_time", 0), "days": s.get("trading_days", 0)}
        issues["pct"] = _pct(issues["on_time"], issues["days"])
        orders = {"on_time": on_time.get(bid, 0), "due": due.get(bid, 0)}
        orders["pct"] = _pct(orders["on_time"], orders["due"])
        recv = {"received": received.get(bid, 0), "delivered": delivered.get(bid, 0)}
        recv["pct"] = _pct(recv["received"], recv["delivered"])
        for c in (issues, orders, recv):
            c["light"] = light(c["pct"])
        login, write = _last_login(con, b["usernames"]), _last_write(con, bid)
        last = max([x for x in (login, write) if x], default=None)
        kind = None if not last else ("บันทึกข้อมูล" if last == write else "เข้าสู่ระบบ")
        active = bool(last and last >= since)
        lights = [c["light"] for c in (issues, orders, recv) if c["light"]]
        start = branch_start(con, bid)                  # PR-U1: before its first activity a branch is neutral, never red
        if not start or start > today.isoformat():
            status, why = "not_started", ""
        elif not active:
            status, why = "red", "ไม่มีการใช้งานใน 7 วัน"
        elif lights:
            status = min(lights, key=lambda x: RANK[x])
            why = ""
        else:
            status, why = "green", "ไม่มีงานครบกำหนดใน 7 วัน"
        pcts = [c["pct"] for c in (issues, orders, recv) if c["pct"] is not None]
        rows.append({"branch_id": bid, "branch": b["name"], "accounts": b["usernames"], "status": status,
                     "status_th": LIGHT_TH[status], "why": why, "issues": issues, "orders": orders, "receive": recv,
                     "last_active": last, "last_active_kind": kind, "active": active,
                     "worst_pct": min(pcts) if pcts else (None if active else -1.0)})
    rows.sort(key=lambda r: (RANK[r["status"]], r["worst_pct"] if r["worst_pct"] is not None else 101.0, r["branch"] or ""))
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in RANK}
    return {"from": d0.isoformat(), "to": d1.isoformat(), "days": days, "today": today.isoformat(), "rows": rows,
            "counts": counts, "total": len(rows), "thresholds": {"green": GREEN_MIN, "yellow": YELLOW_MIN}}


def summary_line(g: dict) -> str:
    """The one line หน้างาน shows: 'ทั้งหมด N สาขา · เขียว N · เหลือง N · แดง N'."""
    c = g["counts"]
    return (f"ทั้งหมด {g['total']} สาขา · เขียว {c['green']} · เหลือง {c['yellow']} · แดง {c['red']}"
            + (f" · ยังไม่เริ่มใช้ระบบ {c['not_started']}" if c.get("not_started") else ""))
