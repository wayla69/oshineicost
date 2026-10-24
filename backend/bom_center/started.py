# -*- coding: utf-8 -*-
"""PR-U1 (owner 2026-10-22) - the ONE "branch start date": the first day a branch is held to the supply module's work.

It is the earliest of what the branch itself did: its first submitted month-end count (ต้นงวด / ปลายงวด), its first issue slip
(ใบตัดเบิก) and the cutoff day of its first order that was sent (submitted, processed or received). The daily-slip log
(issue_reports.log), the pilot scorecard, the close checklist and the to-do list (todo.py), the ordering compliance and the
adoption grid (hub.compliance, adoption.grid) and the daily variance (issue_reports._span) all read it from here: a day, a round
or a month before it is "ยังไม่เริ่มใช้ระบบ" - not judged, never red. None = the branch has not started.
"""
from datetime import date

NOT_STARTED_TH = "ยังไม่เริ่มใช้ระบบ"


def branch_start(con, branch_id: int):
    """ISO date of the branch's first activity, or None."""
    r = con.execute(
        "SELECT MIN(d) FROM ("
        " SELECT MIN(count_date) AS d FROM bc_stock_count WHERE branch_id=? AND COALESCE(status,'submitted')='submitted'"
        "  AND kind IN ('begin','end')"
        " UNION ALL SELECT MIN(slip_date) FROM bc_issue_slip WHERE branch_id=?"
        " UNION ALL SELECT MIN(cycle_date) FROM bc_order WHERE branch_id=? AND status IN ('submitted','processed','received'))",
        (branch_id, branch_id, branch_id)).fetchone()
    return r[0] if r and r[0] else None


def effective_start(con, branch_id: int, today) -> str:
    """PL3 (RV3 F19): the day a branch is first held to the supply module's work, as a screen uses it.

    The branch start above when the branch has one; a branch with no activity yet has not started, so its FIRST day is today: the days
    before it are not "ยังไม่ทำ", and a round whose cutoff is before it is not "เลยเวลาส่ง · มีค่าปรับ". (Chosen over the date the branch
    login was created: an account made two weeks before the first slip would otherwise open on two weeks of missed days.)"""
    return branch_start(con, branch_id) or (today.isoformat() if isinstance(today, date) else str(today)[:10])


def started_by(con, branch_id: int, day) -> bool:
    """Had the branch started on `day` (a date or ISO string)?"""
    s = branch_start(con, branch_id)
    d = day.isoformat() if isinstance(day, date) else str(day)[:10]
    return bool(s) and s <= d


def late_exempt(con, branch_id: int, cycle_date, today, first_day: str = None) -> bool:
    """P3-1 (owner 2026-10-24, "Should be exempt"): the ONE rule for a round the branch is not held to. A round whose cutoff day
    (`cycle_date`) falls on or before the branch's first day (effective_start; the same calendar day at any time counts) is never
    "เลยเวลาส่ง · มีค่าปรับ": not a late duty in หน้างาน (so not in the nav badges), not named in the 13:15 note, not late on the order
    plan, not flagged late when the branch submits it (no late-fee question) and not counted in the ordering compliance. The branch
    orders for the next open round as usual; from its second day on the rule is as before. `first_day` saves the lookup."""
    d = cycle_date.isoformat() if isinstance(cycle_date, date) else str(cycle_date)[:10]
    return d <= (first_day or effective_start(con, branch_id, today))
