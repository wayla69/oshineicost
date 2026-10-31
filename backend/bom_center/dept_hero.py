# -*- coding: utf-8 -*-
"""The department hero of the phone's หน้าแรก (owner 2026-10-31, docs/UI_REVIEW_MOBILE_2026-10-31.md item 4: "เอายอดขายทั้งเครือออกได้").

Every HQ department's หน้าแรก answers "วันนี้ต้องทำอะไร" for THAT department: one headline number, one line, the one page to open. The chain
sales hero stays only for management (report.management) and operation; branch and franchise logins keep their own screens. This module adds
NO business rule and no query of its own beyond a clock line: every figure is a row หน้างาน (todo.build) already built, read through the badges'
own rule (nav_badges.mine_open), so the hero, the red badges, the tiles and หน้างาน's "งานของฉัน" print the same numbers.

`build` answers {dept, label, n, unit, line, tone, go, pages, known} or None for a role without a department hero:
  * label / n / unit - the headline ("ใบที่ต้องยืนยันยอดส่ง" 3 "ใบ"); with nothing of the viewer's own open, label = "", n = None and the screen
    prints "ไม่มีงานค้าง" in green;
  * line - the one line under it (the clock to 14:00, the other open counts, or the next scheduled thing when there is no work);
  * tone - bad (overdue / money lost) · watch (waiting) · good (nothing open);
  * go - the page the hero opens (a todo `go`: {tab, pane, ...});
  * pages - {"<tab>[:<pane>]": {"n": n, "tone": "bad"|"watch"}}: the open tasks per page (n == the badge count of the page);
  * info - {"<tab>[:<pane>]": n}: a figure on a page that is not the viewer's own move (an amber "waiting" tile, never a badge);
  * open - the length of the viewer's own open list (== the badges' total);
  * known - the pages where the department has duties and none is open (a green tile "เรียบร้อย"); every other page has no status.
"""
from datetime import datetime

from .clock import BKK, now_bkk
from .fmt import MONTH_TH

# the roles with a department hero -> the DEPARTMENTS key (js/01-core.js) whose colour and pages the phone draws
ROLE_DEPT = {"warehouse": "wh", "salessupport": "ss", "accounting": "acc", "analyst": "ana", "innovation": "kit", "marketing": "mkt"}
SALES_HERO_ROLES = ("management", "operation")      # keep the chain sales hero (เมื่อวาน vs ปกติ)
WH_DUE_HOUR = 14                                    # the warehouse confirms what it sends by 14:00 of the cutoff day (hub.SLA_PROCESS_BY)
WD_SHORT = ("จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส.", "อา.")


def _base(key) -> str:
    return str(key or "").split(":")[0]


def _find(rows, key):
    return next((r for r in rows if _base(r.get("key")) == key), None)


def _n(it) -> int:
    if not it or it.get("state") != "todo":
        return 0
    return int(it.get("count") or 1)


def _mth(month: str) -> str:
    try:
        return MONTH_TH[int(str(month)[5:7]) - 1]
    except (ValueError, IndexError):
        return str(month or "")


def _day_th(d) -> str:
    return f"{WD_SHORT[d.weekday()]} {d.day}/{d.month}"


def _next_cutoff(items: list, now: datetime):
    """The nearest round cutoff still ahead over every branch (the branches' own order_due rows): "ตัดรอบถัดไป พฤ. 15/10 เวลา 13:15"."""
    best = None
    for it in items:
        if not _base(it.get("key")).startswith("order_due_") or not it.get("cutoff_at"):
            continue
        try:
            at = datetime.fromisoformat(it["cutoff_at"])
        except ValueError:
            continue
        if at >= now and (best is None or at < best[0]):
            best = (at, it.get("cutoff_th") or f"{_day_th(at.date())} เวลา {at:%H:%M}")
    return f"ตัดรอบถัดไป {best[1]}" if best else ""


def _page(go) -> str:
    from .nav_badges import page_key
    return page_key(go) or ""


def _tiles(d: dict, role, modules, mine: list) -> tuple:
    """(pages, known): the open tasks per page (the badges' figure) and the pages of the department's duties with nothing open."""
    from .nav_badges import CLOSE_ROLES, is_mine
    pages = {}
    for it in mine:
        k = _page(it.get("go"))
        p = pages.setdefault(k, {"n": 0, "tone": "watch"})
        p["n"] += 1
        if it.get("urgent"):
            p["tone"] = "bad"
    rows = list(d["items"]) + (list(d["close"]["steps"]) if role in CLOSE_ROLES else [])
    known = sorted({_page(it.get("go")) for it in rows if is_mine(it, role, modules) and _page(it.get("go"))} - set(pages))
    return pages, known


def _wh_line(con, now: datetime) -> tuple:
    """(line, late) for the warehouse's open queue: the time left to 14:00 of the oldest cutoff day still waiting."""
    row = con.execute("SELECT MIN(cycle_date) FROM bc_order WHERE status='submitted'").fetchone()
    if not row or not row[0]:
        return "", False
    try:
        day = datetime.fromisoformat(str(row[0])[:10]).date()
    except ValueError:
        return "", False
    due = datetime(day.year, day.month, day.day, WH_DUE_HOUR, 0, tzinfo=BKK)
    left = (due - now).total_seconds()
    if left <= 0:
        return f"เลยเวลา 14:00 ของวันตัดรอบ {_day_th(day)} แล้ว", True
    if day == now.date():
        h, m = int(left // 3600), int(left % 3600 // 60)
        return (f"เหลือ {h} ชม. {m} นาที ถึง 14:00" if h else f"เหลือ {m} นาที ถึง 14:00"), h < 1
    return f"ยืนยันภายใน 14:00 {_day_th(day)}", False


def _resched_today(con, today) -> int:
    """The deliveries moved from or to today (เลื่อนรอบส่ง, reschedule.py), active only."""
    try:
        return int(con.execute("SELECT COUNT(*) FROM bc_round_reschedule WHERE status='active' AND (orig_date=? OR new_date=?)",
                               (today.isoformat(), today.isoformat())).fetchone()[0] or 0)
    except Exception:                                   # a database before reschedule.py: nothing moved
        return 0


def _first(parts: list):
    """The headline part: the first of the viewer's OWN parts with something open."""
    return next((p for p in parts if p["own"] and p["n"]), None)


def _join(parts: list, head) -> str:
    return " · ".join(f"{p['label']} {p['n']:,}" + (f" {p['unit']}" if p.get("unit") else "") for p in parts if p["n"] and p is not head)


def _info(parts: list, pages: dict) -> dict:
    """{page: n} for the figures that are not the viewer's own move (the warehouse's queue seen by Sales Support, the menus the analyst has
    not costed seen by the kitchen): an amber tile "waiting", never a badge."""
    out = {}
    for p in parts:
        k = _page(p.get("go"))
        if not p["own"] and p["n"] and k and k not in pages:
            out[k] = out.get(k, 0) + p["n"]
    return out


def _answer(dept, parts, mine, pages, known, zero_line, extra_line=""):
    info = _info(parts, pages)
    known = [k for k in known if k not in info]
    head = _first(parts)
    if head is None and mine:                           # open work that is none of the named parts: the list itself
        it = mine[0]
        head = {"label": "งานของฉัน", "n": len(mine), "unit": "งาน", "go": it.get("go"), "own": True,
                "tone": "bad" if any(m.get("urgent") for m in mine) else "watch"}
        line = _join(parts, None) or it.get("title") or ""        # the department's figures, else the first task's name
    elif head is not None:
        line = _join(parts, head)
    else:
        figs = _join(parts, None)
        line = " · ".join(x for x in (figs, zero_line) if x)
        go = next((p["go"] for p in parts if p["n"] and p.get("go")), None) or next((p["go"] for p in parts if p.get("go")), None)
        return {"dept": dept, "label": "", "n": None, "unit": "", "line": line, "tone": "good", "go": go,
                "pages": pages, "info": info, "known": known, "open": 0}
    line = " · ".join(x for x in (extra_line, line) if x)
    if not line and len(mine) > 1:                      # one figure only: the line names the rest of the list
        line = f"งานของฉันทั้งหมด {len(mine):,} งาน"
    return {"dept": dept, "label": head["label"], "n": head["n"], "unit": head.get("unit") or "", "line": line,
            "tone": head.get("tone") or "watch", "go": head.get("go"), "pages": pages, "info": info, "known": known, "open": len(mine)}


def build(con, d: dict, role, modules, mine: list, now: datetime = None) -> dict:
    """The hero of a department role from a todo.build answer `d` and its own open list `mine` (nav_badges.mine_open). None for any other role."""
    dept = ROLE_DEPT.get(role or "")
    if not dept or dept == "mkt" or d is None:
        return None
    now = now or now_bkk()
    if now.tzinfo is None:
        now = now.replace(tzinfo=BKK)
    items, steps = d["items"], d["close"]["steps"]
    pages, known = _tiles(d, role, modules, mine)
    mine_keys = {id(m) for m in mine}

    def part(label, it, unit, own=True, tone="watch", n=None):
        n = _n(it) if n is None else n
        if own and it is not None and id(it) not in mine_keys and n:
            n = 0                                      # not on the viewer's own list (another state / owner): never a headline
        return {"label": label, "n": n, "unit": unit, "own": own, "go": (it or {}).get("go"),
                "tone": "bad" if (it or {}).get("urgent") else tone}

    if role == "warehouse":
        q = _find(items, "queue_pending")
        p = part("ใบที่ต้องยืนยันยอดส่ง", q, "ใบ")
        line, late = _wh_line(con, now) if p["n"] else ("", False)
        if late:
            p["tone"] = "bad"
        if p["n"]:
            return _answer(dept, [p], mine, pages, known, "", extra_line=line)
        processed_today = con.execute("SELECT COUNT(*) FROM bc_order WHERE cycle_date=? AND status IN ('processed','received')",
                                      (now.date().isoformat(),)).fetchone()[0]
        nxt = _next_cutoff(items, now)
        zl = " · ".join(x for x in (("ยืนยันยอดส่งของวันนี้ครบแล้ว" if processed_today else "วันนี้ไม่มีรอบ"), nxt) if x)
        return _answer(dept, [dict(p, go=(q or {}).get("go") or {"tab": "orderhub", "pane": "queue"})], mine, pages, known, zl)

    if role == "salessupport":
        q = _find(items, "queue_pending")
        parts = [part("ใบตัดเบิกยังไม่ส่ง", _find(items, "issue_missing"), "สาขา", tone="bad"),
                 part("เคลมรอพิจารณา", _find(items, "claims_pending"), "เรื่อง"),
                 part("ใบสั่งซื้อยังไม่คีย์บัญชี", _find(items, "accounting_keyed"), "ใบ"),
                 # the queue is the warehouse's move; Sales Support follows it - a figure of the line, never the headline
                 dict(part("ใบสั่งซื้อในคิว", q, "ใบ", own=False), go=(q or {}).get("go") or {"tab": "orderhub", "pane": "queue"}),
                 {"label": "รอบส่งที่เลื่อนวันนี้", "n": _resched_today(con, now.date()), "unit": "รอบ", "own": False,
                  "go": {"tab": "bomcenter", "pane": "cal"}, "tone": "watch"}]
        return _answer(dept, parts, mine, pages, known, _next_cutoff(items, now))

    if role == "accounting":
        st = _find(steps, "issue_posted")
        rows = (st or {}).get("branches") or []
        ready = sum(1 for r in rows if r.get("status") == "todo")
        wait = sum(1 for r in rows if r.get("status") == "blocked")
        month = d.get("month") or ""
        step_txt = f"ปิดงวดเดือน{_mth(month)} ขั้นที่ {st['seq']}/{len(steps)}" if st else ""
        parts = [part("ใบสรุปตัดเบิกพร้อมบันทึก", st, "สาขา", n=ready if st and st.get("state") == "todo" else 0),
                 {"label": "รอสาขา", "n": wait, "unit": "สาขา", "own": False, "go": (st or {}).get("go"), "tone": "watch"}]
        done = st and st.get("status") == "done"
        zl = f"ปิดงวดเดือน{_mth(month)} บันทึกครบแล้ว" if done else step_txt
        ans = _answer(dept, parts, mine, pages, known, zl, extra_line=step_txt if ready else "")
        if not ans.get("go"):
            ans["go"] = {"tab": "bomcenter", "pane": "issue", "month": month} if month else {"tab": "bomcenter", "pane": "issue"}
        return ans

    if role == "analyst":
        parts = [part("เมนูยังไม่มีต้นทุนต่อจาน", _find(items, "dish_cost_month"), "เมนู"),
                 part("ไฟล์ราคารอตรวจ", _find(items, "price_review"), "ไฟล์"),
                 part("ขนาดบรรจุรอตรวจ", _find(items, "pack_report_review"), "เรื่อง"),
                 part("น้ำหนักต่อชิ้นรอตรวจ", _find(items, "unit_avg_review"), "รายการ"),
                 part("รหัสใหม่รอตรวจ", _find(items, "code_request_review"), "เรื่อง")]
        dc = _find(items, "dish_cost_month")
        zl = f"ต้นทุนต่อจานเดือน{_mth(d.get('month'))} ครบแล้ว" if dc and dc.get("status") == "done" else ""
        ans = _answer(dept, parts, mine, pages, known, zl)
        if not ans.get("go"):
            ans["go"] = {"tab": "bomcenter", "pane": "dishcost"}
        return ans

    # innovation (ครัว): its own recipe tasks (full mode only - stock mode hides every recipe page) and, as a figure of the line, the menus
    # the analyst has not costed yet (the kitchen reads ต้นทุนต่อจาน)
    dc = _find(items, "dish_cost_month")
    parts = [part("สูตรที่ต้องส่งตรวจ", _find(items, "draft"), "เวอร์ชัน"),
             part("รายการที่ต้องแก้ก่อนประกาศใช้", _find(items, "checks"), "รายการ"),
             dict(part("เมนูยังไม่มีต้นทุนต่อจาน", dc, "เมนู", own=False),
                  go=(dc or {}).get("go") or {"tab": "bomcenter", "pane": "dishcost"})]
    zl = "" if _n(dc) else "ทุกเมนูที่ขายมีต้นทุนต่อจานแล้ว"
    return _answer(dept, parts, mine, pages, known, zl)


def marketing(n: int, late: int, sla_days: int) -> dict:
    """การตลาด: the customer voices waiting for an answer and those past the reply time - counted by routers/feedback.py's own rule
    (feedback.reply_counts, the queue of เสียงลูกค้า), handed in by routers/nav.py."""
    go = {"tab": "feedback"}
    if not n:
        return {"dept": "mkt", "label": "", "n": None, "unit": "", "line": "ไม่มีเสียงลูกค้ารอตอบ", "tone": "good", "go": go,
                "pages": {}, "info": {}, "known": ["feedback"], "open": 0}
    return {"dept": "mkt", "label": "เสียงลูกค้ารอตอบ", "n": n, "unit": "รายการ",
            "line": f"เกินเวลาตอบ {late:,} รายการ (เกิน {sla_days} วัน)" if late else f"ตอบภายใน {sla_days} วัน",
            "tone": "bad" if late else "watch", "go": go, "pages": {"feedback": {"n": n, "tone": "bad" if late else "watch"}},
            "info": {}, "known": [], "open": n}
