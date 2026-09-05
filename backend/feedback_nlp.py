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
    themes = [th for th, kws in THEMES.items() if any(k in tl for k in kws)]
    mentioned = []
    if menus:
        for m in menus:
            if m.lower() in tl and not any(m.lower() in x.lower() for x in mentioned):
                mentioned.append(m)
            if len(mentioned) >= 3:
                break
    return {"sentiment": sentiment, "score": round(score, 3), "themes": themes, "menus": mentioned}


def analyse_with_claude(rows, menus, api_key: str, timeout=30) -> list:
    """rows: [{'text', 'rating'}]. Returns a list aligned with rows, or raises."""
    prompt = (
        "You classify customer reviews of a Thai buffet / à-la-carte restaurant chain. "
        "For each review return sentiment (pos|neg|neu), score (-1..1), themes (list, Thai, from: "
        + ", ".join(THEMES.keys()) + "), and menus (list of dish names mentioned, taken ONLY from this list: "
        + json.dumps(menus[:300], ensure_ascii=False) + "). Reply with a JSON array, same order, "
        "objects with keys sentiment, score, themes, menus. JSON only.\nReviews: "
        + json.dumps([{"i": i, "text": r.get("text", ""), "rating": r.get("rating")} for i, r in enumerate(rows)],
                     ensure_ascii=False)
    )
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", method="POST",
        headers={"Content-Type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01"},
        data=json.dumps({"model": "claude-haiku-4-5-20251001", "max_tokens": 4000,
                         "messages": [{"role": "user", "content": prompt}]}).encode("utf-8"))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    text = (data.get("content", [{}])[0].get("text", "") or "").strip()
    out = json.loads(text[text.find("["): text.rfind("]") + 1])
    if not isinstance(out, list) or len(out) != len(rows):
        raise ValueError("Claude reply does not align with the rows")
    cleaned = []
    for o in out:
        s = o.get("sentiment") if isinstance(o, dict) else None
        cleaned.append({"sentiment": s if s in ("pos", "neg", "neu") else "neu",
                        "score": round(float(o.get("score", 0) or 0), 3) if isinstance(o, dict) else 0.0,
                        "themes": [t for t in (o.get("themes") or []) if t in THEMES] if isinstance(o, dict) else [],
                        "menus": [m for m in (o.get("menus") or []) if isinstance(m, str)][:3] if isinstance(o, dict) else []})
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
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", method="POST",
        headers={"Content-Type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01"},
        data=json.dumps({"model": "claude-haiku-4-5-20251001", "max_tokens": 600,
                         "messages": [{"role": "user", "content": prompt}]}).encode("utf-8"))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    out = (data.get("content", [{}])[0].get("text", "") or "").strip()
    if not out:
        raise ValueError("empty reply")
    return out


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
