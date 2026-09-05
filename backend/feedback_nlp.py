# -*- coding: utf-8 -*-
"""Customer-feedback analysis: sentiment, themes and menu mentions for Thai
(and mixed Thai/English) review text.

Two engines, same output shape:
  • Claude (claude-haiku-4-5) when ANTHROPIC_API_KEY is set — batch prompt,
    JSON reply, the same urllib pattern as /api/menu/ai-ideas;
  • the lexicon here, always available and used as the fallback — substring
    matching (Thai has no word boundaries), a negation window ("ไม่อร่อย",
    "ไม่ค่อยสด"), the star rating as a tie-breaker, theme keyword groups, and
    menu mentions matched against the SKU names the caller passes in.

Every result says which engine produced it (`analysed_by`), so a summary can
be read with the right amount of trust.
"""
import json
import os
import re
import urllib.request

POSITIVE = ("อร่อย", "สด", "ประทับใจ", "คุ้ม", "ดีมาก", "ดีเยี่ยม", "สุดยอด", "ชอบ", "เยี่ยม", "ถูกใจ",
            "บริการดี", "ใส่ใจ", "สุภาพ", "เร็ว", "สะอาด", "หลากหลาย", "เต็มอิ่ม", "ฟิน", "แนะนำ", "กลับมาอีก",
            "ดีค่ะ", "ดีครับ", "โอเค", "ok", "good", "great", "excellent", "delicious", "fresh", "love", "recommend",
            "เนื้อนุ่ม", "หวานอร่อย", "ละลายในปาก", "มาตรฐาน", "น่ารัก", "ยิ้มแย้ม", "รวดเร็ว")
NEGATIVE = ("ไม่อร่อย", "ไม่สด", "คาว", "เหม็น", "แข็ง", "เหนียว", "เย็นชืด", "ชืด", "จืด", "เค็มไป", "เค็มมาก",
            "แพง", "ไม่คุ้ม", "รอนาน", "ช้า", "ลืมออเดอร์", "ไม่มา", "หมด", "ของหมด", "สกปรก", "แมลง", "ผม",
            "หยาบคาย", "ไม่สนใจ", "ไม่ใส่ใจ", "หน้าบึ้ง", "แย่", "ผิดหวัง", "ห่วย", "ไม่ประทับใจ", "ไม่แนะนำ",
            "ไม่กลับ", "ปริมาณน้อย", "ชิ้นเล็ก", "น้อยลง", "ลดคุณภาพ", "ร้อน", "เสียงดัง", "ท้องเสีย", "อาหารเป็นพิษ",
            "bad", "slow", "dirty", "rude", "expensive", "cold", "stale", "terrible", "worst", "disappointed", "not fresh")
NEGATORS = ("ไม่ค่อย", "ไม่ได้", "ไม่", "not ", "no ")
THEMES = {
    "รสชาติ": ("อร่อย", "รสชาติ", "เค็ม", "จืด", "หวาน", "เผ็ด", "ชืด", "แข็ง", "เหนียว", "taste", "delicious", "salty", "bland"),
    "ความสด": ("สด", "คาว", "เหม็น", "ท้องเสีย", "อาหารเป็นพิษ", "fresh", "stale", "smell"),
    "บริการ": ("บริการ", "พนักงาน", "สุภาพ", "หยาบคาย", "ใส่ใจ", "ไม่สนใจ", "หน้าบึ้ง", "ยิ้ม", "staff", "service", "rude"),
    "รอนาน": ("รอนาน", "ช้า", "คิว", "ลืมออเดอร์", "ไม่มา", "รวดเร็ว", "เร็ว", "slow", "wait", "queue"),
    "ราคา/ความคุ้ม": ("แพง", "คุ้ม", "ราคา", "โปร", "ส่วนลด", "expensive", "price", "worth", "value"),
    "ความสะอาด": ("สะอาด", "สกปรก", "แมลง", "ผม", "เหม็นอับ", "dirty", "clean", "cockroach"),
    "ปริมาณ/ของหมด": ("ปริมาณ", "น้อย", "เยอะ", "ชิ้นเล็ก", "หมด", "เติม", "portion", "small", "run out"),
    "บรรยากาศ": ("บรรยากาศ", "ร้อน", "เสียงดัง", "ที่จอดรถ", "แออัด", "โต๊ะ", "ambience", "noisy", "parking"),
}
# ── severity and the people named (E5) ──────────────────────────────────────
# A complaint that is a safety, legal or integrity matter must reach management
# the same day whatever its star count. Keyword → the reason shown on the pin.
SEVERE = {
    "อาหารเป็นพิษ": "อาหารเป็นพิษ", "ท้องเสีย": "ท้องเสีย", "อาเจียน": "อาเจียน", "food poisoning": "อาหารเป็นพิษ",
    "แมลงสาบ": "แมลงสาบ", "แมลง": "แมลง", "หนอน": "หนอน", "cockroach": "แมลงสาบ", "worm": "หนอน",
    "เส้นผม": "เส้นผม", "ผมในอาหาร": "เส้นผม", "เศษแก้ว": "เศษแก้ว", "เศษโลหะ": "เศษโลหะ", "glass": "เศษแก้ว",
    "โกง": "ทุจริต", "ทุจริต": "ทุจริต", "ขโมย": "ทุจริต", "คิดเงินเกิน": "คิดเงินเกิน", "เก็บเงินเกิน": "คิดเงินเกิน", "overcharge": "คิดเงินเกิน",
    "คุกคาม": "คุกคาม", "ลวนลาม": "คุกคาม", "harass": "คุกคาม", "ทำร้าย": "ทำร้าย", "ตะคอก": "ตะคอก", "ด่า": "ด่าลูกค้า",
    "บาดเจ็บ": "บาดเจ็บ", "ลื่นล้ม": "ลื่นล้ม", "ไฟไหม้": "ไฟไหม้", "ไฟดูด": "ไฟดูด", "ขาดทุน": "",
}
SEVERE.pop("ขาดทุน")
_STAFF_CUES = ("พนักงานชื่อ", "น้องชื่อ", "พี่ชื่อ", "ชื่อพนักงาน", "ชื่อน้อง", "ผู้จัดการชื่อ", "เด็กเสิร์ฟชื่อ", "เสิร์ฟชื่อ",
               "แคชเชียร์ชื่อ", "เชฟชื่อ", "น้อง", "พี่", "คุณ", "staff named", "named")
_NAME = re.compile(r"[฀-๿A-Za-z][฀-๿A-Za-z]{1,14}")
# Thai has no spaces: the run after a cue is trimmed at the first word that is
# not part of a name (a verb, an adjective, a particle, a lexicon word).
_STOP_AFTER = ("บริการ", "ดูแล", "น่ารัก", "ยิ้ม", "สุภาพ", "ใส่ใจ", "เสิร์ฟ", "พูด", "ช่วย", "เป็น", "ทำ", "เก็บ", "เดิน", "ตอบ",
               "ให้", "กับ", "และ", "แต่", "ที่", "ก็", "มาก", "ค่ะ", "ครับ", "คะ", "นะ", "เลย", "จัง", "สุด", "ไม่", "หน้า", "แนะนำ",
               "เติม", "มา", "ไป", "จะ", "แล้ว", "อยู่", "ได้", "คน", "ๆ", "จริง", "ขอ", "โทร", "เอา", "รับ", "คิด", "ลืม", "เดี๋ยว",
               "ยัง", "บอก", "ถาม", "ลูกค้า", "เก่งมาก", "ดี", "แย่", "รอ", "ช้า", "เร็ว", "ใจ", "เยี่ยม", "สาว", "ชาย", "หนุ่ม",
               "ประจำ", "โต๊ะ", "ครัว", "เคาน์เตอร์", "แคชเชียร์", "เสิร์ฟ", "ผู้จัดการ", "พนักงาน", "ทุก", "หลาย", "สอง", "อีก", "ใส่", "ต่อ", "ด่า")
_NOT_NAMES = {"ชื่อ", "ทุกคน", "คนนี้", "คนนั้น", "ท่าน", "เค้า", "เขา", "the", "was", "is", "very", "ของ", "ว่า", "ไหน", "ที่นี่",
              "ลูกค้า", "คุณ", "น้อง", "พี่", "โอชิเน", "ร้าน", "สาขา"}


def severity_of(text: str, sentiment: str = None, rating=None) -> tuple:
    """(level, reason): high = a safety / legal / integrity matter (keyword),
    med = a clearly negative voice (≤2★ or several negative words), low = the rest."""
    tl = (text or "").lower()
    for kw, reason in SEVERE.items():
        if kw in tl:
            return "high", reason
    try:
        r = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        r = None
    if sentiment == "neg" and ((r is not None and r <= 2) or _hits(tl, NEGATIVE) >= 2):
        return "med", ""
    return "low", ""


def _trim_name(raw: str) -> str:
    """The name part of a run of characters after a cue: cut at the first
    non-name word; longest stop words first so 'เก่งมาก' beats 'มาก'."""
    name = raw
    cut = len(name)
    for w in sorted(set(_STOP_AFTER) | set(POSITIVE) | set(NEGATIVE) | set(SEVERE), key=len, reverse=True):
        k = name.find(w)
        if k == 0:                              # the run starts with a stop word → no name here
            return ""
        if 0 < k < cut:
            cut = k
    return name[:cut]


def staff_names(text: str) -> list:
    """People named in a voice — 'พนักงานชื่อเก่ง', 'น้องมิ้นท์น่ารัก', 'คุณ Joy ดูแลดี' —
    for praise and complaints by name."""
    t = text or ""
    found = []
    for cue in _STAFF_CUES:
        start = 0
        while True:
            i = t.find(cue, start)
            if i < 0:
                break
            j = i + len(cue)
            if i > 0 and _NAME.match(t[i - 1]):     # 'คุณ' inside 'ขอบคุณ' is not a cue
                start = j
                continue
            rest = t[j:j + 24].lstrip(" :")
            m = _NAME.match(rest)
            if m:
                name = _trim_name(m.group(0))
                if 2 <= len(name) <= 10 and name not in _NOT_NAMES and name not in found                         and name.lower() not in ("the", "was", "and") and not name.startswith("ๆ"):
                    found.append(name)
            start = j
    return found[:5]


_PREFIX = re.compile(r"^\((?:A|B|DL|TW)\)\s*")
_PAREN = re.compile(r"\s*\([^)]*\)\s*")


def clean_menu_name(desc: str) -> str:
    """'(B) แซลมอน ซาชิมิ (1 ชิ้น)' → 'แซลมอน ซาชิมิ'."""
    s = _PREFIX.sub("", (desc or "").strip())
    s = _PAREN.sub(" ", s).strip()
    return re.sub(r"\s+", " ", s)


def menu_index(names) -> list:
    """Distinct cleaned menu names, longest first (so 'แซลมอนดอง' wins over 'แซลมอน')."""
    seen, out = set(), []
    for n in names:
        c = clean_menu_name(n)
        if len(c) >= 3 and c not in seen:
            seen.add(c); out.append(c)
    out.sort(key=len, reverse=True)
    return out


def _hits(text_l: str, words) -> int:
    n = 0
    for w in words:
        start = 0
        while True:
            i = text_l.find(w, start)
            if i < 0:
                break
            n += 1
            start = i + len(w)
    return n


def _negated_positives(text_l: str) -> int:
    """Positive words preceded (within 3 chars) by a negator: 'ไม่อร่อย', 'ไม่ค่อยสด'."""
    n = 0
    for w in POSITIVE:
        start = 0
        while True:
            i = text_l.find(w, start)
            if i < 0:
                break
            window = text_l[max(0, i - 8):i]
            if any(window.endswith(neg) or window.rstrip().endswith(neg.strip()) for neg in NEGATORS):
                n += 1
            start = i + len(w)
    return n


def analyse_one(text: str, rating=None, menus=None) -> dict:
    """Lexicon analysis of one review. Returns sentiment/score/themes/menus."""
    t = (text or "").strip()
    tl = t.lower()
    negated = _negated_positives(tl)
    pos = _hits(tl, POSITIVE) - negated
    neg = _hits(tl, NEGATIVE) + negated
    score = (pos - neg) / (pos + neg + 1.0)
    if rating is not None:
        try:
            r = float(rating)
            if pos == neg == 0:
                score = (r - 3.0) / 2.0
            else:
                score = 0.7 * score + 0.3 * (r - 3.0) / 2.0
        except (TypeError, ValueError):
            pass
    sentiment = "pos" if score > 0.15 else ("neg" if score < -0.15 else "neu")
    sev, reason = severity_of(t, sentiment, rating)
    if sev == "high" and sentiment != "neg":
        sentiment, score = "neg", min(score, -0.5)          # a poisoning report is negative whatever the words
    themes = [th for th, kws in THEMES.items() if any(k in tl for k in kws)]
    mentioned = []
    if menus:
        for m in menus:
            if m.lower() in tl and not any(m.lower() in x.lower() for x in mentioned):
                mentioned.append(m)
            if len(mentioned) >= 3:
                break
    return {"sentiment": sentiment, "score": round(score, 3), "themes": themes, "menus": mentioned,
            "severity": sev, "severity_note": reason, "staff": staff_names(t)}


_urlopen = urllib.request.urlopen          # seam: the tests swap in a fake
CLAUDE_MODEL = "claude-haiku-4-5-20251001"


def claude_text(prompt: str, api_key: str, max_tokens: int = 1500, timeout: int = 30, model: str = None) -> str:
    """One user turn → the reply text (raises on any failure)."""
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", method="POST",
        headers={"Content-Type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01"},
        data=json.dumps({"model": model or CLAUDE_MODEL, "max_tokens": max_tokens,
                         "messages": [{"role": "user", "content": prompt}]}).encode("utf-8"))
    with _urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    text = (data.get("content", [{}])[0].get("text", "") or "").strip()
    if not text:
        raise ValueError("empty reply")
    return text


def claude_json(prompt: str, api_key: str, max_tokens: int = 1500, timeout: int = 30, array: bool = False):
    text = claude_text(prompt, api_key, max_tokens, timeout)
    a, b = ("[", "]") if array else ("{", "}")
    return json.loads(text[text.find(a): text.rfind(b) + 1])


def analyse_with_claude(rows, menus, api_key: str, timeout=30) -> list:
    """rows: [{'text', 'rating'}]. Returns a list aligned with rows, or raises."""
    prompt = (
        "You classify customer reviews of a Thai buffet / à-la-carte restaurant chain. "
        "For each review return sentiment (pos|neg|neu), score (-1..1), themes (list, Thai, from: "
        + ", ".join(THEMES.keys()) + "), menus (list of dish names mentioned, taken ONLY from this list: "
        + json.dumps(menus[:300], ensure_ascii=False) + "), severity (high = food safety, foreign object, "
        "illness, fraud / overcharge, harassment, injury; med = clearly negative; low = the rest), "
        "severity_note (Thai, 2-4 words, the reason when high, else empty) and staff (list of employee "
        "names mentioned, as written, else empty). Sarcasm counts as negative. Reply with a JSON array, "
        "same order, objects with keys sentiment, score, themes, menus, severity, severity_note, staff. JSON only.\nReviews: "
        + json.dumps([{"i": i, "text": r.get("text", ""), "rating": r.get("rating")} for i, r in enumerate(rows)],
                     ensure_ascii=False)
    )
    out = claude_json(prompt, api_key, max_tokens=4000, timeout=timeout, array=True)
    if not isinstance(out, list) or len(out) != len(rows):
        raise ValueError("Claude reply does not align with the rows")
    cleaned = []
    for o in out:
        if not isinstance(o, dict):
            o = {}
        s = o.get("sentiment")
        sev = o.get("severity") if o.get("severity") in ("high", "med", "low") else "low"
        cleaned.append({"sentiment": s if s in ("pos", "neg", "neu") else "neu",
                        "score": round(float(o.get("score", 0) or 0), 3),
                        "themes": [t for t in (o.get("themes") or []) if t in THEMES],
                        "menus": [m for m in (o.get("menus") or []) if isinstance(m, str)][:3],
                        "severity": sev, "severity_note": str(o.get("severity_note") or "")[:40] if sev == "high" else "",
                        "staff": [str(x)[:30] for x in (o.get("staff") if isinstance(o.get("staff"), list) else []) if isinstance(x, str) and x.strip()][:5]})
    return cleaned


def analyse(rows, menu_names, api_key=None) -> tuple:
    """Analyse many reviews. Returns (results, engine). Claude in batches of 40
    when a key is given; any failure falls back to the lexicon for that batch."""
    menus = menu_index(menu_names)
    key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
    results, engines = [], set()
    for i in range(0, len(rows), 40):
        batch = rows[i:i + 40]
        done = None
        if key:
            try:
                done = analyse_with_claude(batch, menus, key)
                engines.add("claude")
            except Exception:
                done = None
        if done is None:
            done = [analyse_one(r.get("text", ""), r.get("rating"), menus) for r in batch]
            engines.add("lexicon")
        results.extend(done)
    engine = "claude" if engines == {"claude"} else ("lexicon" if engines == {"lexicon"} else "mixed")
    return results, engine


# ── reply drafts ─────────────────────────────────────────────────────────────
BRAND = "โอชิเน"
_REPLY_THEME = {
    "รอนาน": "เรื่องการรอนาน", "บริการ": "เรื่องการบริการของพนักงาน", "ความสด": "เรื่องความสดของวัตถุดิบ",
    "ปริมาณ/ของหมด": "เรื่องเมนูหมดหรือปริมาณอาหาร", "ความสะอาด": "เรื่องความสะอาด",
    "ราคา/ความคุ้ม": "เรื่องราคาและความคุ้มค่า", "รสชาติ": "เรื่องรสชาติอาหาร", "บรรยากาศ": "เรื่องบรรยากาศร้าน",
}


def draft_reply_template(sentiment, themes=(), menus=(), branch="", rating=None) -> str:
    """A polite Thai reply the branch can send as-is or edit: apology naming
    the themes for a negative review, thanks naming the dish for a positive one."""
    where = f"สาขา{branch}" if branch else ""
    sign = f"ทีมงาน{BRAND}" + (f" {where}" if where else "")
    try:
        r = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        r = None
    if sentiment == "neg" or (r is not None and r <= 2 and sentiment != "pos"):
        issues = [_REPLY_THEME[t] for t in themes if t in _REPLY_THEME][:2]
        about = ("ใน" + "และ".join(issues)) if issues else "ที่ไม่ประทับใจในครั้งนี้"
        return (f"{sign}ต้องขออภัยอย่างสูงสำหรับประสบการณ์{about}ค่ะ เราได้แจ้งผู้จัดการสาขาให้ตรวจสอบและปรับปรุงทันที "
                "หากสะดวก รบกวนแจ้งวันและเวลาที่มาใช้บริการทางข้อความส่วนตัว เพื่อให้เราดูแลได้ตรงจุด "
                "ขอโอกาสให้เราได้ดูแลคุณลูกค้าอีกครั้งนะคะ 🙏")
    if sentiment == "pos" or (r is not None and r >= 4):
        dish = f" โดยเฉพาะ{menus[0]}" if menus else ""
        return (f"ขอบคุณมากค่ะที่มาใช้บริการ{BRAND}" + (f" {where}" if where else "") +
                f" และแบ่งปันความประทับใจ{dish} ทีมงานดีใจมากค่ะ หวังว่าจะได้ต้อนรับคุณลูกค้าอีกครั้งเร็ว ๆ นี้นะคะ 🙏")
    return (f"ขอบคุณสำหรับรีวิวค่ะ {sign}จะนำความคิดเห็นไปปรับปรุงการบริการให้ดียิ่งขึ้น "
            "หวังว่าจะได้ต้อนรับคุณลูกค้าอีกครั้งนะคะ 🙏")


def draft_reply_with_claude(text, sentiment, themes, menus, branch, rating, api_key: str, timeout=30) -> str:
    prompt = (
        f"You write replies from the restaurant to customer reviews of {BRAND} (Oshinei), a Thai buffet and "
        "à-la-carte chain. Write ONE reply in polite Thai (ค่ะ), 40-80 words, that addresses the specific points "
        "of the review, apologises without excuses when something went wrong, never promises compensation, and "
        f"invites the guest back. Sign as ทีมงาน{BRAND}" + (f" สาขา{branch}" if branch else "") + ". Reply text only.\n"
        f"Review ({rating if rating is not None else '-'}★, {sentiment}; themes {', '.join(themes or [])}; "
        f"menus {', '.join(menus or [])}): {text}"
    )
    return claude_text(prompt, api_key, max_tokens=600, timeout=timeout)


def draft_reply(row: dict, api_key=None) -> tuple:
    """(draft, engine): Claude when a key is available, the template otherwise."""
    themes = row.get("themes") or []
    menus = row.get("menus") or []
    if isinstance(themes, str):
        themes = json.loads(themes or "[]")
    if isinstance(menus, str):
        menus = json.loads(menus or "[]")
    args = (row.get("sentiment") or "neu", themes, menus, row.get("branch") or row.get("branch_name") or "", row.get("rating"))
    key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
    if key:
        try:
            return draft_reply_with_claude(row.get("text") or "", *args, api_key=key), "claude"
        except Exception:
            pass
    return draft_reply_template(*args), "template"


# ── the weekly narrative and "ask the data" (E5) ──────────────────────────────
def _fmt_delta(cur, prev, unit="", digits=0):
    if cur is None or prev is None:
        return ""
    d = cur - prev
    if abs(d) < (0.05 if digits else 0.5):
        return " (เท่ากับสัปดาห์ก่อน)"
    return f" ({'เพิ่มขึ้น' if d > 0 else 'ลดลง'} {abs(d):.{digits}f}{unit} จากช่วงก่อน)"


def narrative_template(d: dict) -> dict:
    """The digest as three short Thai paragraphs and three actions, from rules
    — what a careful area manager would write from the same numbers."""
    r = d.get("range") or ["", ""]
    v = d.get("voices") or 0
    if not v:
        return {"overview": f"ช่วง {r[0]} ถึง {r[1]} ยังไม่มีเสียงลูกค้าเข้ามา — เปิดการดึงอัตโนมัติหรือติด QR แบบสอบถามที่โต๊ะเพื่อให้มีข้อมูลสรุปสัปดาห์หน้า",
                "fix": "", "praise": "", "actions": ["ทำตาม Checklist เปิดใช้ ให้แหล่งข้อมูลแรกทำงาน"]}
    neg = d.get("negatives") or 0
    th = d.get("themes") or []
    watch = d.get("branches_watch") or []
    praised = d.get("branches_praised") or []
    resp = d.get("response") or {}
    a = d.get("actions") or {}
    over = (f"ช่วง {r[0]} ถึง {r[1]} มีเสียงลูกค้า {v} เสียง{_fmt_delta(v, d.get('prev_voices'))} "
            f"เชิงลบ {d.get('neg_pct') or 0:g}%{_fmt_delta(d.get('neg_pct'), d.get('prev_neg_pct'), ' จุด', 1)}")
    if d.get("avg_rating") is not None:
        over += f" คะแนนเฉลี่ย {d['avg_rating']:.2f} ดาว{_fmt_delta(d.get('avg_rating'), d.get('prev_avg_rating'), '', 2)}"
    if resp.get("answerable"):
        over += f" สาขาตอบกลับได้ {resp.get('rate') or 0}% ของเสียงที่ต้องตอบ"
        if d.get("sla_breached"):
            over += f" และมี {d['sla_breached']} รายการค้างเกิน SLA"
    if d.get("severe"):
        over += f" ⚠ มีเรื่องร้ายแรง {d['severe']} รายการที่ต้องจัดการวันนี้"
    fix = ""
    if neg and th:
        top = [t for t in th if t.get("neg")] or th
        t0 = top[0]
        fix = f"เรื่องที่ถูกบ่นมากที่สุดคือ \"{t0['theme']}\" ({t0['n']} เสียง"
        if t0.get("prev_n") is not None:
            fix += f", สัปดาห์ก่อน {t0['prev_n']}"
        fix += ")"
        if len(top) > 1:
            fix += " รองลงมา " + " และ ".join(f"\"{t['theme']}\" ({t['n']})" for t in top[1:3])
        if watch:
            w = watch[0]
            fix += (f" สาขาที่ต้องดูก่อนคือ {w['branch']} เชิงลบ {w['neg_pct']:g}% ({w['neg']} จาก {w['n']} เสียง)"
                    + (f" เรื่องหลัก {w['top_theme']}" if w.get("top_theme") else ""))
    elif neg:
        fix = f"มีเสียงเชิงลบ {neg} เสียงแต่ยังไม่จับหัวข้อได้ชัด — อ่านข้อความจริงในกล่องงาน"
    else:
        fix = "สัปดาห์นี้ไม่มีเสียงเชิงลบ"
    praise = ""
    if praised:
        p = praised[0]
        praise = f"สาขาที่ได้คำชมมากที่สุดคือ {p['branch']} ({p['avg_rating']:.2f} ดาว จาก {p['n']} เสียง)"
        pos_th = [t for t in th if t.get("n", 0) - t.get("neg", 0) > t.get("neg", 0)]
        if pos_th:
            praise += " สิ่งที่ลูกค้าชมคือ " + ", ".join(t["theme"] for t in pos_th[:2])
    elif d.get("positives"):
        praise = f"มีเสียงชม {d['positives']} เสียง"
    if a.get("better") or a.get("worse") or a.get("same"):
        praise += (" · งานแก้ที่พิสูจน์ผลแล้ว: " + ", ".join(x for x in (f"ดีขึ้น {a['better']}" if a.get("better") else "",
                                                                     f"เท่าเดิม {a['same']}" if a.get("same") else "",
                                                                     f"แย่ลง {a['worse']}" if a.get("worse") else "") if x))
    actions = []
    if d.get("severe"):
        actions.append("เรื่องร้ายแรงที่ปักหมุดไว้: ผู้จัดการเขตโทรหาลูกค้าและสรุปในกลุ่มภายในวันนี้")
    if d.get("sla_breached"):
        actions.append(f"ตอบ {d['sla_breached']} รายการที่เกิน SLA ก่อนเที่ยง แล้วคุมให้ 'รอสาขาตอบ' เป็น 0 ทุกวัน")
    if watch and th:
        t0 = ([t for t in th if t.get("neg")] or th)[0]
        actions.append(f"เปิดงานแก้เรื่อง \"{t0['theme']}\" ที่ {watch[0]['branch']} ระบุผู้รับผิดชอบและกำหนดเสร็จ")
    if a.get("overdue"):
        actions.append(f"งานแก้เกินกำหนด {a['overdue']} งาน — อัปเดตสถานะหรือเลื่อนกำหนดพร้อมเหตุผล")
    if praised:
        actions.append(f"บอกทีม {praised[0]['branch']} ว่าลูกค้าชมอะไร แล้วขอให้แชร์วิธีให้สาขาอื่น")
    if not actions:
        actions.append("รักษาระดับ: ตอบทุกเสียงภายใน 1 วัน และดูหัวข้อที่ขยับขึ้นในสัปดาห์หน้า")
    return {"overview": over, "fix": fix, "praise": praise, "actions": actions[:3]}


def narrative(d: dict, api_key=None) -> tuple:
    """({overview, fix, praise, actions}, engine) — Claude writes from the digest
    when a key is set; the template otherwise (and on any failure)."""
    key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
    if key and (d.get("voices") or 0) > 0:
        try:
            slim = {k: d.get(k) for k in ("range", "prev_range", "voices", "prev_voices", "neg_pct", "prev_neg_pct", "avg_rating",
                                         "prev_avg_rating", "response", "unreplied", "sla_breached", "flagged", "severe", "themes",
                                         "branches_watch", "branches_praised", "response_by_branch", "survey", "actions")}
            prompt = (
                f"You are the customer-experience lead of {BRAND} (Oshinei), a Thai buffet restaurant chain. From the "
                "weekly customer-voice digest below (JSON), write for the management group chat, in Thai, plain and "
                "specific, no greetings: overview (2 sentences: volume, negative share and rating vs the week before, "
                "answer rate), fix (2-3 sentences: the top complaint themes with counts and the branch behind them), "
                "praise (1-2 sentences: what guests praised and where; mention verified fixes if any), and actions "
                "(exactly 3 concrete actions for this week, each one sentence naming a branch or theme). Use only the "
                "numbers in the digest. Reply as JSON with keys overview, fix, praise, actions. JSON only.\n"
                + json.dumps(slim, ensure_ascii=False)
            )
            out = claude_json(prompt, key, max_tokens=1200)
            if isinstance(out, dict) and out.get("overview"):
                return ({"overview": str(out.get("overview") or "")[:1200], "fix": str(out.get("fix") or "")[:1200],
                         "praise": str(out.get("praise") or "")[:800],
                         "actions": [str(x)[:300] for x in (out.get("actions") or []) if str(x).strip()][:3]}, "claude")
        except Exception:
            pass
    return narrative_template(d), "template"


_STOP = {"ไหม", "อะไร", "ยังไง", "อย่างไร", "บ้าง", "มี", "กี่", "เท่าไร", "เท่าไหร่", "ที่", "ของ", "ใน", "และ", "หรือ", "ครับ", "ค่ะ",
         "คะ", "สาขา", "ลูกค้า", "เรื่อง", "ว่า", "ไหน", "ทำไม", "the", "what", "how", "many", "is", "are", "about", "do", "did", "any"}


def ask_search(question: str, rows: list) -> dict:
    """No key: the question's words matched against the voices (substring, so
    Thai needs no tokeniser) — the count, the split, and the closest examples."""
    q = (question or "").strip()
    terms = [w for w in re.split(r"[\s,.?!/]+", q) if len(w) >= 2 and w.lower() not in _STOP]
    # Thai questions are often one long run: the known theme / dish names inside it are the
    # real terms, and a long run that contains one of them is the question, not a term
    inner = [t for t in THEMES if t in q] + [w for w in ("แซลมอน", "ทุเรียน", "ซาชิมิ", "ชาบู", "หอย", "กุ้ง", "ปู", "เนื้อ", "ข้าว", "ซุป", "ของหวาน") if w in q]
    terms = [w for w in terms if not (len(w) > 12 and any(x in w for x in inner))]
    terms += [x for x in inner if x not in terms]
    for w in list(terms):
        if len(w) > 12 and w != q:
            terms.remove(w)
    hits = []
    for r in rows:
        blob = f"{r.get('text') or ''} {r.get('branch_name') or ''} {r.get('themes') or ''} {r.get('menus') or ''}".lower()
        score = sum(1 for t in terms if t.lower() in blob)
        if score:
            hits.append((score, r))
    hits.sort(key=lambda x: (-x[0], x[1].get("rated_at") or ""), reverse=False)
    hits = [r for _, r in sorted(hits, key=lambda x: (-x[0], -(len(x[1].get("text") or ""))))]
    neg = sum(1 for r in hits if r.get("sentiment") == "neg")
    by_branch = {}
    for r in hits:
        by_branch[r.get("branch_name") or "(ไม่ระบุสาขา)"] = by_branch.get(r.get("branch_name") or "(ไม่ระบุสาขา)", 0) + 1
    top_b = sorted(by_branch.items(), key=lambda kv: -kv[1])[:3]
    if not terms:
        answer = "ระบุคำที่อยากค้นในคำถาม เช่น ชื่อเมนู หัวข้อ หรือชื่อสาขา (ตั้ง ANTHROPIC_API_KEY เพื่อให้ AI ตอบเป็นประโยค)"
    elif not hits:
        answer = f"ไม่พบเสียงที่กล่าวถึง {', '.join(terms)} ในช่วงนี้"
    else:
        answer = (f"พบ {len(hits)} เสียงที่กล่าวถึง {', '.join(terms)} — เชิงลบ {neg} เสียง"
                  + (" สาขาที่พูดถึงมากสุด: " + ", ".join(f"{b} ({n})" for b, n in top_b) if top_b else ""))
    return {"answer": answer, "terms": terms, "matches": len(hits), "negative": neg,
            "cites": [{"id": r.get("id"), "date": r.get("rated_at"), "branch": r.get("branch_name"), "rating": r.get("rating"),
                       "sentiment": r.get("sentiment"), "text": (r.get("text") or "")[:200]} for r in hits[:8]]}


def ask(question: str, rows: list, digest: dict = None, api_key=None, max_rows: int = 150) -> tuple:
    """({answer, cites, ...}, engine): Claude reads the most recent voices (and
    the digest numbers) and answers with citations; without a key, a search."""
    key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
    base = ask_search(question, rows)
    if key and rows:
        try:
            ctx = [{"id": r.get("id"), "d": (r.get("rated_at") or "")[:10], "b": r.get("branch_name") or "", "r": r.get("rating"),
                    "s": r.get("sentiment"), "t": (r.get("text") or "")[:220]} for r in rows[:max_rows]]
            slim = {k: (digest or {}).get(k) for k in ("range", "voices", "neg_pct", "avg_rating", "themes", "branches_watch", "branches_praised")}
            prompt = (
                f"You answer a manager's question about customer feedback of {BRAND} (Oshinei), a Thai buffet chain. "
                "Use ONLY the voices and the digest below; if they do not contain the answer, say so. Answer in Thai, "
                "3-6 sentences with the numbers you counted, then list the ids of up to 6 voices that support the answer. "
                "Reply as JSON: {\"answer\": string, \"cite_ids\": [ints]}. JSON only.\n"
                f"Question: {question}\nDigest: {json.dumps(slim, ensure_ascii=False)}\nVoices: {json.dumps(ctx, ensure_ascii=False)}"
            )
            out = claude_json(prompt, key, max_tokens=900)
            if isinstance(out, dict) and out.get("answer"):
                ids = [int(x) for x in (out.get("cite_ids") or []) if str(x).lstrip("-").isdigit()]
                by_id = {r.get("id"): r for r in rows}
                cites = [{"id": i, "date": by_id[i].get("rated_at"), "branch": by_id[i].get("branch_name"), "rating": by_id[i].get("rating"),
                          "sentiment": by_id[i].get("sentiment"), "text": (by_id[i].get("text") or "")[:200]} for i in ids if i in by_id][:6]
                return ({"answer": str(out["answer"])[:1500], "cites": cites or base["cites"][:4], "terms": base["terms"],
                         "matches": base["matches"], "negative": base["negative"]}, "claude")
        except Exception:
            pass
    return base, "search"
