# -*- coding: utf-8 -*-
"""Display formats for text the people on the floor read (PR-M1, 2026-10-10).

One date format in the UI: `9/10/2026` (day/month/year, Gregorian), day chips `พ. 9/10`, times `13:15`,
months `กันยายน 2026`. ISO stays in the API fields, the Excel files and the database — only the sentences
built here (todo details, ValueError messages, status lines) use these. `who()` turns a login name into the
name a person recognises (app_user.display_name, else the branch / role label), never `salessupport1`.
"""
from datetime import date, datetime, timedelta, timezone

_BKK = timezone(timedelta(hours=7))

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


def bkk_stamp(v):
    """A stored stamp as the Bangkok clock (QA 2026-10-13 M1): -> datetime in Asia/Bangkok, or None when `v` has no time part.
    The tables hold UTC: "2026-10-09T06:15:42" (naive, as master.now() writes it) or "2026-10-09T06:15:42+00:00" (aware); a stamp written
    with a SPACE ("2026-10-09 13:15", the reports' own format) is already the branch's clock and is kept as it is."""
    s = str(v or "").strip()
    if len(s) < 16 or s[10] not in "T ":
        return None
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        if s[10] == " ":
            return t.replace(tzinfo=_BKK)
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(_BKK)


def day_at_th(v) -> str:
    """The Bangkok day of a stored stamp: 2026-10-09T18:30:00+00:00 -> 10/10/2026."""
    t = bkk_stamp(v)
    return day_th(t.date()) if t else day_th(v)


def at_th(v) -> str:
    """2026-10-09T06:15:42+00:00 → 9/10/2026 13:15 (Bangkok clock; a bare date has no time part)."""
    if not v:
        return "-"
    s = str(v)
    d = _d(s)
    if not d:
        return s
    t = bkk_stamp(s)
    return f"{day_th(t.date())} {t:%H:%M}" if t else day_th(d)


def lbl(label) -> str:
    """A version's name as people read it: a stored label may end with the old "(draft)" tag (the status says it)."""
    import re
    return re.sub(r"\s*\((?:draft|ร่าง)\)\s*$", "", str(label or ""), flags=re.I)


# 5.2: ONE set of words for why stock was lost - the slip's waste reasons and the variance reasons draw from it, so the same code
# reads the same on both pages (the stored codes never change)
REASON_LABEL = {"waste": "วัตถุดิบเสีย / ทิ้ง", "expired": "หมดอายุ", "spill": "หก / ตก / แตก", "prep_loss": "ตัดแต่งเกินสูตร",
                "staff": "ชิม / อาหารพนักงาน", "overportion": "เสิร์ฟเกินปริมาณตามสูตร", "transfer": "โอนให้สาขาอื่น",
                "miscount": "นับของผิด", "receipt": "บันทึกรับของไม่ครบ", "other": "อื่น ๆ (ระบุ)"}


CLASS_TH = {"A": "ของแพง", "B": "ของราคาปานกลาง", "C": "ของราคาถูก"}         # the ABC class of an ingredient
CLASS_SHORT_TH = {"A": "แพง", "B": "ปานกลาง", "C": "ถูก"}                                    # …in a table cell under "กลุ่มมูลค่า"


def reasons(*codes) -> tuple:
    """((code, label), …) in the order given, the labels from REASON_LABEL."""
    return tuple((c, REASON_LABEL[c]) for c in codes)


def item(src, code) -> str:
    """The name a person recognises for an ingredient code (the branch never reads `03-01-179`). `src` is a connection or a
    Converter (which already holds the names); a code the register does not know comes back as it is."""
    c = str(code or "").strip()
    names = getattr(src, "names", None)
    if names is not None:
        return names.get(c) or c
    try:                                                   # 4.1: the short name when the register has one
        r = src.execute("SELECT COALESCE(NULLIF(TRIM(short_name), ''), name_th) FROM bc_ingredient WHERE code=?", (c,)).fetchone()
    except Exception:
        try:
            r = src.execute("SELECT name_th FROM bc_ingredient WHERE code=?", (c,)).fetchone()
        except Exception:
            r = None
    return (r[0] if r and r[0] else None) or c


def short_names(con) -> dict:
    """{code: short name} of the register items that have one (4.1). Empty when the column does not exist yet."""
    try:
        return {c: s.strip() for c, s in con.execute(
            "SELECT code, short_name FROM bc_ingredient WHERE short_name IS NOT NULL AND TRIM(short_name) <> ''")}
    except Exception:
        return {}


def add_short(con, obj, _map=None):
    """4.1: put `short_name` beside `name` in every dict of a payload that names an ingredient (it has `code` and `name`) — the
    branch screens write the short name when there is one and the full name otherwise, with the code under "ดูที่มา". The payload
    is changed in place and returned; a dict whose code has no short name is left as it is."""
    m = short_names(con) if _map is None else _map
    if not m:
        return obj
    if isinstance(obj, dict):
        c = obj.get("code")
        if "name" in obj and c in m:
            obj["short_name"] = m[c]
        for v in obj.values():
            if isinstance(v, (dict, list)):
                add_short(con, v, m)
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, (dict, list)):
                add_short(con, v, m)
    return obj


def round_day_th(round_key, delivery) -> str:
    """4.2: how a worker names an order: "รอบ 2 (ส่ง 10/10)" - the round and its delivery day, never ORDER-8 / #9."""
    from .forecast import ROUND_TH
    d = _d(delivery)
    return f"{ROUND_TH.get(str(round_key), str(round_key))}" + (f" (ส่ง {d.day}/{d.month})" if d else "")


# 4.6: a gap in words. The sign of "ส่วนต่าง −฿16,197 −8.1%" is read the wrong way round as often as the right one; the words carry the
# direction instead: "ใช้น้อยกว่าสูตร ฿16,197 (8.1%)". kind: use = what the branch used against the recipes, issue = what the slip issued
# against the recipes, count = the physical count against the system stock. The Excel files keep the signed numbers.
GAP_WORDS = {"use": ("ใช้มากกว่าสูตร", "ใช้น้อยกว่าสูตร", "ใช้ตามสูตร"),
             "issue": ("เบิกมากกว่าสูตร", "เบิกน้อยกว่าสูตร", "เบิกตามสูตร"),
             "count": ("นับได้มากกว่าระบบ", "นับได้น้อยกว่าระบบ", "นับตรงกับระบบ")}


def gap_dir(value, pct=None) -> int:
    """+1 more, -1 less, 0 the same (a baht gap under ฿0.5 and a % under 0.05 are the same). `value` wins when both are given."""
    try:
        v = None if value is None else float(value)
        p = None if pct is None else float(pct)
    except (TypeError, ValueError):
        return 0
    if v is not None:
        if abs(v) < 0.5 and (p is None or abs(p) < 0.05):
            return 0
        if v != 0:
            return 1 if v > 0 else -1
    if p is None or abs(p) < 0.05:
        return 0
    return 1 if p > 0 else -1


def gap_th(value, pct=None, kind="use") -> str:
    """"ใช้น้อยกว่าสูตร ฿16,197 (8.1%)" from the baht gap and the % (either may be None); "ใช้ตามสูตร" when there is none."""
    more, less, same = GAP_WORDS.get(kind, GAP_WORDS["use"])
    d = gap_dir(value, pct)
    if d == 0:
        return same
    out = more if d > 0 else less
    has_baht = value is not None and abs(float(value)) >= 0.5                  # ฿0 is not written
    if has_baht:
        out += f" ฿{abs(float(value)):,.0f}"
    if pct is not None and abs(float(pct)) >= 0.05:
        p = abs(float(pct))
        t = f"{p:.0f}%" if p >= 10 else f"{p:.1f}%"
        out += f" ({t})" if has_baht else f" {t}"
    return out


def no_unit(src, code, unit=None) -> str:
    """The one sentence for a quantity that cannot be turned into the base unit: "ผักชี ยังไม่ได้ตั้งหน่วย (ช้อนโต๊ะ)"."""
    u = str(unit or "").strip()
    return f"{item(src, code)} ยังไม่ได้ตั้งหน่วย" + (f" ({u})" if u and u != "-" else "")


def cutoff_label(dt) -> str:
    """The cutoff as it is written everywhere (the sentence is "ส่งใบสั่งซื้อภายใน …"): พฤ. 15/10 เวลา 13:15"""
    return f"{WD_SHORT[dt.weekday()]} {dt.day}/{dt.month} เวลา {dt:%H:%M}"


def left_th(hours) -> str:
    """Time left until a cutoff: "อีก 2 วัน 19 ชม." from 24 hours up, hours only under 24 ("อีก 5 ชม."), minutes under one hour."""
    h = max(float(hours or 0), 0.0)
    if h < 1:
        return f"อีก {int(round(h * 60))} นาที"
    whole = int(h)
    d, r = divmod(whole, 24)
    if d == 0:
        return f"อีก {r} ชม."
    return f"อีก {d} วัน" + (f" {r} ชม." if r else "")


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
