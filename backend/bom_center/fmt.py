# -*- coding: utf-8 -*-
"""Display formats for text the people on the floor read (PR-M1, 2026-10-10).

One date format in the UI: `9/10/2026` (day/month/year, Gregorian), day chips `พ. 9/10`, times `13:15`,
months `กันยายน 2026`. ISO stays in the API fields, the Excel files and the database — only the sentences
built here (todo details, ValueError messages, status lines) use these. `who()` turns a login name into the
name a person recognises (app_user.display_name, else the branch / role label), never `salessupport1`.
"""
from datetime import date, datetime

MONTH_TH = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
            "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
WD_SHORT = ["จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส.", "อา."]            # Monday = 0


def _d(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def day_th(v) -> str:
    """2026-10-09 → 9/10/2026 (anything that is not a date comes back unchanged)."""
    d = _d(v)
    return f"{d.day}/{d.month}/{d.year}" if d else ("" if v is None else str(v))


def day_short_th(v) -> str:
    """2026-10-09 → ศ. 9/10"""
    d = _d(v)
    return f"{WD_SHORT[d.weekday()]} {d.day}/{d.month}" if d else ("" if v is None else str(v))


def month_th(m) -> str:
    """'2026-09' → 'กันยายน 2026'"""
    s = str(m or "")
    try:
        y, mo = int(s[:4]), int(s[5:7])
        return f"{MONTH_TH[mo - 1]} {y}"
    except (ValueError, IndexError):
        return s


def at_th(v) -> str:
    """2026-10-09T13:15:42+07:00 → 9/10/2026 13:15 (a bare date has no time part)."""
    if not v:
        return "-"
    s = str(v)
    d = _d(s)
    if not d:
        return s
    t = s[11:16] if len(s) >= 16 and s[10] in "T " else ""
    return f"{day_th(d)} {t}".strip()


def lbl(label) -> str:
    """A version's name as people read it: a stored label may end with the old "(draft)" tag (the status says it)."""
    import re
    return re.sub(r"\s*\((?:draft|ร่าง)\)\s*$", "", str(label or ""), flags=re.I)


def who(con, username) -> str:
    """The name a person recognises for a login: app_user.display_name, else สาขา{branch} for a branch
    login, else the Thai role label. A name that is not an account (a system job, a deleted user) is
    returned as it is; empty → '-'."""
    u = (str(username) if username is not None else "").strip()
    if not u:
        return "-"
    try:
        from auth import display_name_for
        n = display_name_for(con, u)
    except Exception:
        n = None
    return n or u
