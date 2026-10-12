# -*- coding: utf-8 -*-
"""Order reminders (PR-18, reworked for the 13:15 cutoff in PR-A, batch 3 2026-10-12): the facts of หน้างาน
(todo.build) for EVERY active branch account (not the four pilot branches) and the centre, as one Telegram message
a day — sent when the bot is configured (notify.telegram_config), stored either way — and as Web Push to the phones
of the people who own each line (routers/push_api.maybe_send_supply: kind "supply").

main._maybe_supply_reminder calls `morning` on the first API request after 07:00 (Thai
clock) each day and `cutoff` on the first after 13:15: the branches that did not beat
today's cutoff, and the orders now waiting in the warehouse queue. Both are best-effort
and never raise into the request that triggered them."""
from datetime import date, datetime
from typing import Optional
import html

from . import todo as _td
from .clock import now_bkk, today as bkk_today
from .pilot import branch_accounts

SUPPLY_TAG = "oshinei-supply"
TELEGRAM_MAX = 3800          # a Telegram message holds 4096 characters
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
    """The day's duties per branch account plus the centre's queue. `any_work` is
    False on a day nobody has to do anything (no message is worth sending). The text names only the branches with
    something to do (30 branches would not fit one Telegram message); `branches` lists them all."""
    today = today or bkk_today()
    centre = {it["key"]: it for it in _td.build(con, today=today)["items"]}
    lines, blines, branches, any_work = [], [], [], False
    for b in branch_accounts(con):
        d = _td.build(con, branch_id=b["branch_id"], today=today)
        bl = branch_line(d["items"], today)
        branches.append({"branch_id": b["branch_id"], "name": b["name"], **bl})
        any_work = any_work or bl["due_today"] or bl["pending"]
        if bl["due_today"] or bl["pending"]:
            blines.append(f"<b>{html.escape(b['name'])}</b>: {html.escape(bl['text'])}")
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
    dropped = 0                                              # one Telegram message holds 4096 characters: the rest stays in หน้างาน
    while blines and len("\n".join([head] + blines + lines + ([tail] if tail else []))) > TELEGRAM_MAX:
        blines.pop()
        dropped += 1
    if dropped:
        blines.append(f"และอีก {dropped} สาขา ดูที่หน้างาน")
    text = "\n".join([head] + blines + lines + ([tail] if tail else []))
    return {"today": today.isoformat(), "text": text, "any_work": any_work, "branches": branches, "queue": queue}


def cutoff(con, today: date = None, app_url: str = "", now: datetime = None) -> Optional[dict]:
    """After 13:15: the branches that did not submit today's order in time, and the orders waiting
    in the warehouse queue — None when no branch missed it and the queue is empty."""
    if now is None and today is None:
        now = now_bkk()
    today = today or now.date()
    centre = {it["key"]: it for it in _td.build(con, today=today, now=now)["items"]}
    q = centre.get("queue_pending") or {}
    missed = []
    for b in branch_accounts(con):
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


# ── Web Push (batch 3, 2026-10-12): who gets which line ─────────────────────────────────────────────────────────
# routers/push_api.maybe_send_supply / maybe_send_precutoff / maybe_send_cutoff call these and send the payloads to
# the devices of the people who own the line (kind "supply"). Everything here is pure reading: no push is sent.
def supply_url(tab: str, pane: str = None) -> str:
    """The address a tap opens: js/01-core.js supplyDeepLink reads go=supply&tab=…&pane=… and calls supplyGo."""
    return f"/?go=supply&tab={tab}" + (f"&pane={pane}" if pane else "")


def _aware(now: datetime = None) -> datetime:
    from .clock import BKK
    if now is None:
        return now_bkk()
    return now if now.tzinfo else now.replace(tzinfo=BKK)


def branch_morning(con, branch: dict, today: date = None, now: datetime = None) -> Optional[dict]:
    """The branch's morning line as a notification; None on a day nothing is open for it (an order already sent, a slip
    already confirmed, nothing to receive: the line would only say so)."""
    now = _aware(now)
    today = today or now.date()
    d = _td.build(con, branch_id=branch["branch_id"], today=today, now=now)
    bl = branch_line(d["items"], today)
    if not bl["pending"] or not bl["text"]:
        return None
    return {"title": f"{branch['name']} · งานวัตถุดิบและใบสั่งซื้อวันนี้", "body": bl["text"],
            "url": supply_url("supplyhome"), "tag": SUPPLY_TAG}


# the roles that own each centre line, in the words of todo.owner_th (js/16-supplyhome.js _SHM_ROLE_OWNERS)
CENTRE_ROLES = {"warehouse": "คลัง", "salessupport": "ประสานงานขาย", "operation": "ฝ่ายปฏิบัติการ", "accounting": "บัญชี"}


def centre_morning(con, today: date = None, now: datetime = None) -> dict:
    """{role: {"lines": [...], "url": ...}} — the centre's open work, split by the role that owns it: the queue to the
    warehouse; the claims, the slips not confirmed and the orders not yet keyed to Sales Support; the over-issue
    streaks to Operations; the close step "ใบสรุปตัดเบิก" to Accounting. A role with nothing open is not listed."""
    now = _aware(now)
    today = today or now.date()
    d = _td.build(con, today=today, now=now)
    items = {it["key"]: it for it in d["items"]}
    out: dict = {}

    def add(role, line, url):
        out.setdefault(role, {"lines": [], "urls": []})
        out[role]["lines"].append(line)
        out[role]["urls"].append(url)

    q = items.get("queue_pending") or {}
    if q.get("count"):
        add("warehouse", f"ใบสั่งซื้อค้างคิว {q['count']} ใบ ยืนยันยอดส่งภายใน 14:00 ของวันตัดรอบ"
            + (f" · ส่งล่าช้า {q['late_orders']} ใบ (มีค่าปรับ)" if q.get("late_orders") else ""), supply_url("orderhub", "queue"))
    cl = items.get("claims_pending") or {}
    if cl.get("count"):
        add("salessupport", f"เคลมสินค้ารอพิจารณา {cl['count']} เรื่อง", supply_url("orderhub", "claims"))
    im = items.get("issue_missing") or {}
    if im.get("count"):
        add("salessupport", f"ใบตัดเบิกของเมื่อวานยังไม่ยืนยัน {im['count']} สาขา: " + " · ".join(im.get("branch_names") or []),
            supply_url("orderhub", "issues"))
    ak = items.get("accounting_keyed") or {}
    if ak.get("count"):
        add("salessupport", f"ใบสั่งซื้อที่คลังจัดส่งแล้วยังไม่คีย์บัญชี {ak['count']} ใบ", supply_url("orderhub", "queue"))
    an = items.get("issue_anomaly") or {}
    if an.get("count"):
        add("operation", f"เบิกเกินสูตรติดต่อกัน {an['count']} สาขา: " + " · ".join(an.get("branch_names") or []),
            supply_url("orderhub", "issuevar"))
    step = next((s for s in (d.get("close") or {}).get("steps") or [] if s["key"] == "issue_posted"), None)
    if step:
        ready = [r for r in step.get("branches") or [] if r["status"] == "todo"]
        if ready:
            from .fmt import month_th
            add("accounting", f"ปิดงวดเดือน{month_th(d['close']['month'])}: ใบสรุปตัดเบิกรอดาวน์โหลดและบันทึกเข้าระบบบัญชี {len(ready)} สาขา",
                supply_url("bomcenter", "issue"))
    for role, v in out.items():
        urls = list(dict.fromkeys(v["urls"]))
        v["url"] = urls[0] if len(urls) == 1 else supply_url("supplyhome")      # two kinds of work: the page that lists both
        del v["urls"]
    return out


def centre_payload(role: str, rec: dict) -> dict:
    return {"title": f"{CENTRE_ROLES.get(role, 'ศูนย์')} · งานวัตถุดิบและใบสั่งซื้อวันนี้", "body": " · ".join(rec["lines"]),
            "url": rec["url"], "tag": SUPPLY_TAG}


def _left(minutes: int) -> str:
    return f"อีก {minutes // 60} ชม." if minutes >= 60 else f"อีก {max(minutes, 1)} นาที"


def branch_precutoff(con, branch: dict, today: date = None, now: datetime = None) -> Optional[dict]:
    """2.2 · 11:00, two hours before the 13:15 cutoff: the rounds whose cutoff is TODAY and whose order is not yet
    submitted. None when every order due today is in (or nothing is due, or the cutoff has passed)."""
    from .clock import BKK
    now = _aware(now)
    today = today or now.date()
    d = _td.build(con, branch_id=branch["branch_id"], today=today, now=now)
    lines = []
    for it in d["items"]:
        if not (it["key"].startswith("order_due_") and it.get("due_today") and it["status"] != "done" and not it.get("late")):
            continue
        cut = datetime.fromisoformat(it["cutoff_at"])
        cut = cut if cut.tzinfo else cut.replace(tzinfo=BKK)
        minutes = int((cut - now).total_seconds() // 60)
        if minutes <= 0:
            continue
        lines.append(f"ส่งใบสั่งซื้อรอบส่ง{it['delivery_th']} ภายในวันนี้ 13:15 ({_left(minutes)})")
    if not lines:
        return None
    return {"title": f"{branch['name']} · ใกล้ถึงเวลาตัดรอบ 13:15", "body": " · ".join(lines),
            "url": supply_url("branchstock", "order"), "tag": SUPPLY_TAG + "-cutoff"}


def cutoff_payload(rec: dict) -> Optional[dict]:
    """The 13:15 message, for Sales Support only: the branches that missed the cutoff (cutoff() above). None when
    nobody missed it (the warehouse queue is told in the morning line)."""
    missed = (rec or {}).get("missed") or []
    if not missed:
        return None
    names = " · ".join(f"{m['name']} (รอบส่ง{m['delivery_th']})" for m in missed)
    return {"title": f"ตัดรอบ 13:15 · ยังไม่ส่งใบสั่งซื้อ {len(missed)} สาขา", "body": names + " · ส่งล่าช้า มีค่าปรับ",
            "url": supply_url("supplyhome"), "tag": SUPPLY_TAG + "-missed"}
