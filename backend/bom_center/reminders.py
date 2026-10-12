# -*- coding: utf-8 -*-
"""Order reminders (PR-18, reworked for the 13:15 cutoff in PR-A): the facts of หน้างาน
(todo.build) for the pilot branches and Sales Support as one Telegram message a day —
sent when the bot is configured (notify.telegram_config), stored either way.

main._maybe_supply_reminder calls `morning` on the first API request after 07:00 (Thai
clock) each day and `cutoff` on the first after 13:15: the branches that did not beat
today's cutoff, and the orders now waiting in the warehouse queue. Both are best-effort
and never raise into the request that triggered them."""
from datetime import date, datetime
from typing import Optional
import html

from . import todo as _td
from .clock import now_bkk, today as bkk_today
from .pilot import pilot_branches

WD_TH = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]      # date.weekday(): Mon = 0
MON_TH = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def thai_date(d: date) -> str:
    return f"{WD_TH[d.weekday()]} {d.day} {MON_TH[d.month - 1]} {d.year + 543}"


def branch_line(items: list, today: date = None) -> dict:
    """One branch's day from its todo items → {text, due_today, pending}."""
    by = {}
    for it in items:
        by.setdefault(it["key"].split(":")[0], it)
    orders = [it for it in items if it["key"].startswith("order_due_")]
    recv = by.get("receive_pending")
    parts, pending, due_today = [], False, False
    for o in orders:
        done = o["status"] == "done"
        if o.get("due_today"):
            due_today = True
            if done:
                parts.append(f"ส่งใบสั่งซื้อรอบส่ง{o['delivery_th']} แล้ว")
            elif o.get("late"):
                parts.append(f"ยังไม่ส่งใบสั่งซื้อรอบส่ง{o['delivery_th']} (ส่งล่าช้า มีค่าปรับ)")
                pending = True
            else:
                parts.append(f"ส่งใบสั่งซื้อรอบส่ง{o['delivery_th']} ภายในวันนี้ 13:15")
                pending = True
        elif o.get("late") and not done:                   # the cutoff passed on an earlier day
            parts.append(f"ยังไม่ส่งใบสั่งซื้อรอบส่ง{o['delivery_th']} (ส่งล่าช้า มีค่าปรับ)")
            pending = True
    if not parts and orders:
        nxt = min((o for o in orders if o["status"] != "done"), key=lambda o: o["cutoff_at"], default=None) \
            or min(orders, key=lambda o: o["cutoff_at"])
        parts.append(f"ไม่มีกำหนดส่งใบสั่งซื้อวันนี้ (ถัดไป ส่งใบสั่งซื้อภายใน {nxt['cutoff_th']} · รอบส่ง{nxt['delivery_th']})")
    slip = by.get("issue_today")
    if slip and slip.get("started"):                     # ใบตัดเบิก: yesterday's slip, only for a branch that has begun
        if slip.get("yesterday") in ("confirmed", "late"):
            parts.append("ใบตัดเบิกเมื่อวาน: ยืนยันแล้ว")
        else:
            parts.append("ใบตัดเบิกเมื่อวาน: ยังไม่ยืนยัน")
            pending = True
    if recv and recv.get("count"):
        parts.append(f"รับสินค้าตามใบสั่งซื้อ {recv['count']} ใบ")
        pending = True
    return {"text": " · ".join(parts), "due_today": due_today, "pending": pending}


def morning(con, today: date = None, app_url: str = "") -> dict:
    """The day's duties per pilot branch plus the centre's queue. `any_work` is
    False on a day nobody has to do anything (no message is worth sending)."""
    today = today or bkk_today()
    centre = {it["key"]: it for it in _td.build(con, today=today)["items"]}
    lines, branches, any_work = [], [], False
    for b in pilot_branches(con):
        d = _td.build(con, branch_id=b["branch_id"], today=today)
        bl = branch_line(d["items"], today)
        branches.append({"branch_id": b["branch_id"], "name": b["name"], **bl})
        any_work = any_work or bl["due_today"] or bl["pending"]
        lines.append(f"<b>{html.escape(b['name'])}</b>: {html.escape(bl['text'])}")
    q = centre.get("queue_pending") or {}
    queue = {"count": q.get("count") or 0, "late": bool(q.get("urgent")), "late_orders": q.get("late_orders") or 0}
    if queue["count"]:
        any_work = True
        lines.append(f"<b>คลัง</b>: ใบสั่งซื้อค้างคิว {queue['count']} ใบ"
                     + (f" · ส่งล่าช้า {queue['late_orders']} ใบ (มีค่าปรับ)" if queue["late_orders"] else "")
                     + (" · มีใบที่เลยวันตัดรอบแล้ว" if queue["late"] else ""))
    cl = centre.get("claims_pending") or {}
    if cl.get("count"):
        any_work = True
        lines.append(f"<b>ประสานงานขาย</b>: เคลมสินค้ารอพิจารณา {cl['count']} เรื่อง")
    im = centre.get("issue_missing") or {}
    if im.get("count"):                                      # ใบตัดเบิกของเมื่อวาน
        any_work = True
        lines.append(f"<b>ประสานงานขาย</b>: ใบตัดเบิกของเมื่อวานยังไม่ยืนยัน {im['count']} สาขา · "
                     + html.escape(" · ".join(im.get("branch_names") or [])))
    an = centre.get("issue_anomaly") or {}
    if an.get("count"):
        any_work = True
        from .issue_reports import OVER_PCT, STREAK_DAYS
        lines.append(f"<b>ฝ่ายปฏิบัติการ</b>: เบิกเกินสูตรมากกว่า {OVER_PCT:.0f}% ติดต่อกันอย่างน้อย {STREAK_DAYS} วัน: "
                     + html.escape(" · ".join(f"{a['branch']} ({', '.join(a['items'])})" for a in an.get("anomalies") or [])))
    head = f"<b>วัตถุดิบและใบสั่งซื้อ · {thai_date(today)}</b>"
    tail = f"หน้างาน: {html.escape(app_url.rstrip('/'))}/" if app_url else ""
    text = "\n".join([head] + lines + ([tail] if tail else []))
    return {"today": today.isoformat(), "text": text, "any_work": any_work, "branches": branches, "queue": queue}


def cutoff(con, today: date = None, app_url: str = "", now: datetime = None) -> Optional[dict]:
    """After 13:15: the pilot branches that did not submit today's order in time, and the orders waiting
    in the warehouse queue — None when no branch missed it and the queue is empty."""
    if now is None and today is None:
        now = now_bkk()
    today = today or now.date()
    centre = {it["key"]: it for it in _td.build(con, today=today, now=now)["items"]}
    q = centre.get("queue_pending") or {}
    missed = []
    for b in pilot_branches(con):
        for it in _td.build(con, branch_id=b["branch_id"], today=today, now=now)["items"]:
            if it["key"].startswith("order_due_") and it.get("due_today") and it.get("late") and it["status"] != "done":
                missed.append({"branch_id": b["branch_id"], "name": b["name"], "delivery": it["delivery"],
                               "delivery_th": it["delivery_th"]})
    n = q.get("count") or 0
    if not missed and not n:
        return None
    lines = [f"<b>ตัดรอบ 13:15 · {thai_date(today)}</b>"]
    lines += [f"{html.escape(m['name'])}: ยังไม่ส่งใบสั่งซื้อรอบส่ง{m['delivery_th']} (ส่งล่าช้า มีค่าปรับ)" for m in missed]
    if n:
        lines.append(f"คลัง: ใบสั่งซื้อค้างคิว {n} ใบ ยืนยันยอดส่งภายใน 14:00 ของวันตัดรอบ"
                     + (f" · ส่งล่าช้า {q['late_orders']} ใบ (มีค่าปรับ)" if q.get("late_orders") else ""))
    if app_url:
        lines.append(f"คิวใบสั่งซื้อ: {html.escape(app_url.rstrip('/'))}/")
    return {"today": today.isoformat(), "text": "\n".join(lines), "count": n, "late": bool(q.get("urgent")),
            "missed": missed}
