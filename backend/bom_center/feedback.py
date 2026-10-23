# -*- coding: utf-8 -*-
"""Batch 4b (2.6) - the reasons a branch gives for a variance feed back into the recipes and the orders.

Three rules, evaluated on every request for the current month and the one before (a month's figures are final once it is over, so
"monthly" is the same function a month later), each making ONE task for the people who can act on it:

  formula_excess   the same item is explained as served above the recipe quantity (variance reason `overportion`, "เสิร์ฟเกินปริมาณตามสูตร")
                   or trimmed above the recipe (slip waste reason `prep_loss`, "ตัดแต่งทิ้งเกินปกติ") in 2 branches or more in one month, or in the
                   same branch in 2 months in a row  ->  "ตรวจสูตร: {item} ({branches})" for the kitchen and the analyst, linked to
                   ตรวจสูตรด้วยยอดซื้อ filtered to the item.
                   `overportion` is stored only as a variance reason and `prep_loss` only as a slip waste reason (the variance list has no
                   trimming reason), so a branch counts as having the second one in a month when it wrote it on at least PREP_LOSS_DAYS
                   confirmed slips of the item in that month - one slip is a bad day, not a pattern.
  miscount         `miscount` ("ตรวจนับผิด") at least 3 times in one branch in one month  ->  "ทบทวนการตรวจนับ: {branch}" for Operations.
  stock_less       the order deviation reason `stock_less` (the branch ordered less because the shelf had more than the system said) in at
                   least 3 order rounds of one item in one month  ->  "ตรวจคงเหลือตามระบบ: {branch} · {item}" for Sales Support.

A task is open while its rule holds AND nobody acknowledged it for that month. An acknowledgement is one row in bc_feedback_ack
(append-only: written once, never changed or deleted); when the rule stops holding the task is simply no longer produced.
Nothing here changes a recipe, a stock figure or an order.
"""
from datetime import date

from .fmt import month_th as _mth
from .master import audit
from .master import now as _now

MIN_BRANCHES = 2          # formula_excess: the same item in this many branches in a month ...
MIN_MONTHS = 2            # ... or in the same branch this many months in a row
PREP_LOSS_DAYS = 3        # a branch has "prep_loss" on an item in a month when it wrote it on this many slips
MISCOUNT_MIN = 3
STOCK_LESS_MIN = 3

RULES = {
    "formula_excess": {"owner": ("bom.recipes", "bom.center"), "owner_th": "ครัว / ฝ่ายวิเคราะห์", "ack": ("bom.recipes", "bom.center")},
    "miscount": {"owner": ("ops.report",), "owner_th": "ฝ่ายปฏิบัติการ", "ack": ("ops.report", "bom.center")},
    "stock_less": {"owner": ("supply.ordering",), "owner_th": "ประสานงานขาย", "ack": ("supply.ordering", "bom.center")},
}


def _month_add(m: str, n: int) -> str:
    k = int(m[:4]) * 12 + int(m[5:7]) - 1 + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def _months(today: date) -> list:
    """[this month, the month before] (YYYY-MM)."""
    cur = f"{today.year:04d}-{today.month:02d}"
    return [cur, _month_add(cur, -1)]


def _names(con) -> tuple:
    from .fmt import short_names
    short = short_names(con)
    items = {c: (short.get(c) or n or c) for c, n in con.execute("SELECT code, name_th FROM bc_ingredient")}
    branches = {b: n for b, n in con.execute("SELECT branch_id, name FROM branches")}
    return items, branches


def _acked(con) -> set:
    return {(r, s, m) for r, s, m in con.execute("SELECT rule, subject, month FROM bc_feedback_ack")}


def _variance_reason_months(con, reason: str, months: list) -> list:
    """[(branch_id, code, month)] of the variance reasons `reason` on periods PRICED in `months` (the month of the day before the end
    count, the month the period belongs to)."""
    lo = months[-1] + "-01"
    return [(b, c, m) for b, c, m in con.execute(
        "SELECT r.branch_id, r.ingredient_code, strftime('%Y-%m', date(e.count_date, '-1 day')) FROM bc_variance_reason r"
        " JOIN bc_stock_count e ON e.count_id=r.end_count_id WHERE r.reason_code=? AND date(e.count_date, '-1 day')>=?", (reason, lo))
        if m in months]


def _slip_waste_months(con, reason: str, months: list, min_days: int) -> list:
    """[(branch_id, code, month)] where the item carries the slip waste reason on at least `min_days` confirmed slips of the month."""
    lo = months[-1] + "-01"
    return [(b, c, m) for b, c, m, n in con.execute(
        "SELECT s.branch_id, l.ingredient_code, substr(s.slip_date, 1, 7), COUNT(DISTINCT s.slip_date) FROM bc_issue_slip s"
        " JOIN bc_issue_line l USING(slip_id) WHERE s.status='confirmed' AND l.waste_reason=? AND s.slip_date>=?"
        " GROUP BY s.branch_id, l.ingredient_code, substr(s.slip_date, 1, 7)", (reason, lo)) if n >= min_days and m in months]


def evaluate(con, today: date = None, include_acked: bool = False) -> list:
    """Every task the three rules make for this month and the one before. Each:
    {rule, subject, month, key, title, detail, owner_modules, owner_th, ack_modules, branches[{branch_id, name}], item?, acked}
    Acknowledged tasks are left out unless `include_acked`."""
    from .clock import today as _today
    today = today or _today()
    months = _months(today)
    items, bnames = _names(con)
    acked = _acked(con)
    tasks = []

    def add(rule, subject, month, title, detail, branches, **extra):
        t = {"rule": rule, "subject": subject, "month": month, "key": f"{rule}:{subject}:{month}", "title": title, "detail": detail,
             "owner_modules": list(RULES[rule]["owner"]), "owner_th": RULES[rule]["owner_th"], "ack_modules": list(RULES[rule]["ack"]),
             "branches": [{"branch_id": b, "name": bnames.get(b) or str(b)} for b in branches],
             "acked": (rule, subject, month) in acked}
        t.update(extra)
        if include_acked or not t["acked"]:
            tasks.append(t)

    # 1 · the recipe quantity is exceeded for the same item
    flagged = set(_variance_reason_months(con, "overportion", months)) | set(_slip_waste_months(con, "prep_loss", months, PREP_LOSS_DAYS))
    by_item_month, by_pair = {}, {}
    for b, c, m in flagged:
        by_item_month.setdefault((c, m), set()).add(b)
        by_pair.setdefault((b, c), set()).add(m)
    seen = {}
    for (c, m), bs in by_item_month.items():
        if len(bs) >= MIN_BRANCHES:
            seen.setdefault((c, m), set()).update(bs)
    for (b, c), ms in by_pair.items():
        for m in ms:
            if _month_add(m, -1) in ms:                         # two months in a row: the later month carries the task
                seen.setdefault((c, m), set()).add(b)
    for (c, m), bs in sorted(seen.items()):
        bl = sorted(bs, key=lambda b: bnames.get(b) or "")
        names = " · ".join(bnames.get(b) or str(b) for b in bl)
        add("formula_excess", c, m, f"ตรวจสูตร: {items.get(c, c)} ({names})",
            f"เดือน{_mth(m)} · ใช้เกินปริมาณตามสูตร (เสิร์ฟเกินหรือตัดแต่งเกิน) ซ้ำใน {len(bl)} สาขา หรือต่อเนื่อง 2 เดือน · เทียบกับยอดซื้อที่หน้าตรวจสูตรด้วยยอดซื้อ",
            bl, item=c, item_name=items.get(c, c))
    # 2 · the count is wrong again and again in one branch
    cnt = {}
    for b, c, m in _variance_reason_months(con, "miscount", months):
        cnt[(b, m)] = cnt.get((b, m), 0) + 1
    for (b, m), n in sorted(cnt.items()):
        if n >= MISCOUNT_MIN:
            add("miscount", str(b), m, f"ทบทวนการตรวจนับ: {bnames.get(b) or b}",
                f"เดือน{_mth(m)} · เหตุผลผลต่าง “ตรวจนับผิด” {n} รายการ · อบรมวิธีนับและหน่วยนับให้พนักงานสาขา", [b], count=n)
    # 3 · the branch orders less because the shelf has more than the system says
    lo = months[-1] + "-01"
    sl = {}
    for b, c, m, n in con.execute(
            "SELECT o.branch_id, l.ingredient_code, substr(o.cycle_date, 1, 7), COUNT(DISTINCT o.order_id) FROM bc_order o"
            " JOIN bc_order_line l USING(order_id) WHERE l.reason_code='stock_less' AND o.status<>'cancelled' AND o.cycle_date>=?"
            " GROUP BY o.branch_id, l.ingredient_code, substr(o.cycle_date, 1, 7)", (lo,)):
        if m in months and n >= STOCK_LESS_MIN:
            sl[(b, c, m)] = n
    for (b, c, m), n in sorted(sl.items()):
        add("stock_less", f"{b}|{c}", m, f"ตรวจคงเหลือตามระบบ: {bnames.get(b) or b} · {items.get(c, c)}",
            f"เดือน{_mth(m)} · สาขาสั่งน้อยกว่าที่แนะนำเพราะของจริงมากกว่าคงเหลือตามระบบ {n} รอบ · ตรวจใบตัดเบิกและการรับสินค้าของรายการนี้", [b],
            item=c, item_name=items.get(c, c), count=n)
    tasks.sort(key=lambda t: (t["month"] != months[0], t["rule"], t["title"]))
    return tasks


def acknowledge(con, rule: str, subject: str, month: str, who: str, note: str = None) -> dict:
    """Record that the task was read. Refused when the task is not open now (the condition cleared, or it is already acknowledged)."""
    if rule not in RULES:
        raise ValueError("ไม่พบชนิดของงานนี้")
    open_ = {(t["rule"], t["subject"], t["month"]) for t in evaluate(con)}
    if (rule, str(subject), str(month)) not in open_:
        raise ValueError("ไม่พบงานนี้ อาจรับทราบไปแล้ว หรือเงื่อนไขของงานหมดไปแล้ว")
    n = " ".join(str(note or "").split())[:300] or None
    cur = con.execute("INSERT INTO bc_feedback_ack(rule, subject, month, acked_by, acked_at, note) VALUES(?,?,?,?,?,?)",
                      (rule, str(subject), str(month), who, _now(), n))
    audit(con, who, "feedback_ack", "bc_feedback_ack", f"{rule}:{subject}:{month}", None, n)
    return {"ack_id": cur.lastrowid, "rule": rule, "subject": str(subject), "month": month}
