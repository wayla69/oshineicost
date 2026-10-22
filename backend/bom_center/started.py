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


def started_by(con, branch_id: int, day) -> bool:
    """Had the branch started on `day` (a date or ISO string)?"""
    s = branch_start(con, branch_id)
    d = day.isoformat() if isinstance(day, date) else str(day)[:10]
    return bool(s) and s <= d
