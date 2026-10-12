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


# 5.2: ONE set of words for why stock was lost - the slip's waste reasons and the variance reasons draw from it, so the same code
# reads the same on both pages (the stored codes never change)
REASON_LABEL = {"waste": "วัตถุดิบเสีย / ทิ้ง", "expired": "หมดอายุ", "spill": "หก / ตก / แตก", "prep_loss": "ตัดแต่งเกินสูตร",
                "staff": "ชิม / อาหารพนักงาน", "overportion": "เสิร์ฟเกินปริมาณตามสูตร", "transfer": "โอนให้สาขาอื่น",
                "miscount": "ตรวจนับผิด", "receipt": "บันทึกรับสินค้าไม่ครบ", "other": "อื่น ๆ (ระบุ)"}


CLASS_TH = {"A": "กลุ่มมูลค่าสูง", "B": "กลุ่มมูลค่ากลาง", "C": "กลุ่มมูลค่าต่ำ"}         # the ABC class of an ingredient
CLASS_SHORT_TH = {"A": "สูง", "B": "กลาง", "C": "ต่ำ"}                                    # …in a table cell under "กลุ่มมูลค่า"


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
    try:
        r = src.execute("SELECT name_th FROM bc_ingredient WHERE code=?", (c,)).fetchone()
    except Exception:
        r = None
    return (r[0] if r and r[0] else None) or c


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
