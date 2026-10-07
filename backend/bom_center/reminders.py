# -*- coding: utf-8 -*-
"""Order-day reminders (PR-18): the facts of หน้างาน (todo.build) for the pilot
branches and Sales Support as one Telegram message a day — sent when the bot
is configured (notify.telegram_config), stored either way.

main._maybe_supply_reminder calls `morning` on the first API request after
07:00 (Thai clock) each day and `sla` on the first after 10:00; both are
best-effort and never raise into the request that triggered them."""
from datetime import date
from typing import Optional
import html

from . import todo as _td
from .pilot import pilot_branches

WD_TH = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]      # date.weekday(): Mon = 0
MON_TH = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def thai_date(d: date) -> str:
    return f"{WD_TH[d.weekday()]} {d.day} {MON_TH[d.month - 1]} {d.year + 543}"


def _round_name(key: str) -> str:
    r = key.split(":")[0].rsplit("_", 1)[1]           # order_today_<r>[:bid]
    return "แซลมอนสด" if r == "salmon" else f"รอบ {r}"


def branch_line(items: list) -> dict:
    """One branch's day from its todo items → {text, order_day, pending}."""
    by = {}
    for it in items:
        by.setdefault(it["key"].split(":")[0], it)
    orders = [it for it in items if it["key"].startswith("order_today_")]
    count = by.get("count_today")
    recv = by.get("receive_pending")
    parts, pending = [], False
    order_day = bool(orders)
    if order_day:
        parts.append("วันสั่ง" + " และ".join(_round_name(o["key"]) for o in orders))
        if count:
            ok = count["status"] == "done"
            parts.append("ตรวจนับแล้ว" if ok else "ยังไม่ตรวจนับ")
            pending |= not ok
        for o in orders:
            ok = o["status"] == "done"
            rn = _round_name(o["key"])
            parts.append(f"ส่งใบสั่งซื้อ{rn} แล้ว" if ok else f"ยังไม่ส่งใบสั่งซื้อ{rn}")
            pending |= not ok
    else:
        nxt = (count or {}).get("detail") or ""
        parts.append("ไม่มีวันสั่ง" + (f" ({nxt})" if nxt else ""))
    if recv and recv.get("count"):
        parts.append(f"รับสินค้าตามใบสั่งซื้อ {recv['count']} ใบ")
        pending = True
    return {"text": " · ".join(parts), "order_day": order_day, "pending": pending}


def morning(con, today: date = None, app_url: str = "") -> dict:
    """The day's duties per pilot branch plus the centre's queue. `any_work` is
    False on a day nobody has to do anything (no message is worth sending)."""
    today = today or _td.bkk_today()
    centre = {it["key"]: it for it in _td.build(con, today=today)["items"]}
    lines, branches, any_work = [], [], False
    for b in pilot_branches(con):
        d = _td.build(con, branch_id=b["branch_id"], today=today)
        bl = branch_line(d["items"])
        branches.append({"branch_id": b["branch_id"], "name": b["name"], **bl})
        any_work = any_work or bl["order_day"] or bl["pending"]
        lines.append(f"<b>{html.escape(b['name'])}</b>: {html.escape(bl['text'])}")
    q = centre.get("queue_pending") or {}
    queue = {"count": q.get("count") or 0, "late": bool(q.get("urgent"))}
    if queue["count"]:
        any_work = True
        lines.append(f"<b>Sales Support</b>: ใบสั่งซื้อค้างคิว {queue['count']} ใบ"
                     + (" · มีใบที่เลยวันสั่งแล้ว" if queue["late"] else ""))
    head = f"<b>วัตถุดิบและใบสั่งซื้อ · {thai_date(today)}</b>"
    tail = f"หน้างาน: {html.escape(app_url.rstrip('/'))}/" if app_url else ""
    text = "\n".join([head] + lines + ([tail] if tail else []))
    return {"today": today.isoformat(), "text": text, "any_work": any_work, "branches": branches, "queue": queue}


def sla(con, today: date = None, app_url: str = "") -> Optional[dict]:
    """After 10:00: the orders still waiting in the queue, or None when none wait."""
    today = today or _td.bkk_today()
    centre = {it["key"]: it for it in _td.build(con, today=today)["items"]}
    q = centre.get("queue_pending") or {}
    if not q.get("count"):
        return None
    text = (f"<b>ใบสั่งซื้อค้างคิว {q['count']} ใบ</b> — ดำเนินการภายใน 14:00 ของวันสั่ง"
            + (" · มีใบที่เลยวันสั่งแล้ว" if q.get("urgent") else "")
            + (f"\nคิวใบสั่งซื้อ: {html.escape(app_url.rstrip('/'))}/" if app_url else ""))
    return {"today": today.isoformat(), "text": text, "count": q["count"], "late": bool(q.get("urgent"))}
