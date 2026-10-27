"""📱 แบบสอบถาม QR — the chain's own feedback channel (E3 of the expansion plan).
Public reviews arrive a handful per branch per month: too few for 28 branches
to learn anything month to month. A QR on the table or the receipt asks the
guest for twenty seconds — a face from 1 to 5, what it was about, a line of
text and, if they want a call back, a phone or LINE id — and the answer lands
in `customer_feedback` as kind "survey", analysed like every other voice, so
the tab, the work queue (a negative answer with a contact = the branch calls
back), the branch league and the themes all see it.
  GET  /s/{code}                        the survey page (mobile, no login — the code names the branch)
  POST /api/feedback/survey/{code}      one answer → customer_feedback; login-exempt in main.py
  GET  /s/{code}/print                  an A5 poster with the QR, ready to print
  GET  /api/feedback/survey-links       every branch: code, link, poster, Google review url, scans, answers
  POST /api/feedback/survey-links       create a branch's link ({branch_id}) or every missing one ({all:true}) / set its Google review url / switch it off
Guards on the public endpoint: the code must exist and be active; a honeypot
field and a minimum time on the page catch bots; a per-code and a per-address
rate limit cap floods; every value is length-capped and escaped on the way
out. The contact is kept only for the call back (customer_feedback.contact)
and blanked after CONTACT_DAYS; the form says so.
"""
import collections
import datetime
import html
import secrets

import segno
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from feedback_host import branch_rows, get_db, require_write_token, scope_branch
from routers.feedback import _ensure as _ensure_feedback, _thai_today, insert_reviews

router = APIRouter()

SOURCE = "Survey (QR)"
CONTACT_DAYS = 90                # a guest's phone / LINE id is blanked after this many days
RATE_PER_CODE = 60               # answers per hour one branch's code accepts
RATE_PER_IP = 10                 # answers per hour from one address
MIN_SECONDS_ON_PAGE = 2          # faster than this is a script, not a guest
CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no 0/o, 1/l — the code is typed from a poster as a last resort

# the chips a guest can tap — the keys are the theme names feedback_nlp uses, so
# an aspect merges straight into the row's themes and the tab's theme list
ASPECTS = [("รสชาติ", "😋 รสชาติ / อร่อย"), ("ความสด", "🐟 ความสด"), ("บริการ", "🙋 บริการ"),
           ("รอนาน", "⏳ รอนาน / คิว"), ("ราคา/ความคุ้ม", "💸 ราคา / ความคุ้ม"), ("ความสะอาด", "🧼 ความสะอาด"),
           ("ปริมาณ/ของหมด", "🍽 ของหมด / เติมช้า"), ("บรรยากาศ", "🎐 บรรยากาศ")]
FACES = [(1, "😠", "แย่มาก"), (2, "😕", "ไม่ค่อยดี"), (3, "😐", "เฉย ๆ"), (4, "🙂", "ดี"), (5, "😍", "ประทับใจ")]

DDL = """
CREATE TABLE IF NOT EXISTS feedback_survey_link(
  branch_id         INTEGER PRIMARY KEY,
  branch_name       TEXT,
  code              TEXT UNIQUE,
  google_review_url TEXT,
  active            INTEGER DEFAULT 1,
  created_at        TEXT,
  created_by        TEXT,
  scans             INTEGER DEFAULT 0,
  responses         INTEGER DEFAULT 0
);
"""


def _ensure(con):
    _ensure_feedback(con)
    con.executescript(DDL)


def _now() -> float:            # seam for the rate-limit tests
    return time.time()


_HITS: dict = collections.defaultdict(collections.deque)


def _rate_ok(key: str, limit: int) -> bool:
    now = _now()
    q = _HITS[key]
    while q and q[0] < now - 3600:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    if len(_HITS) > 20000:                      # keep the table bounded: drop keys with no hit in the window
        for k in [k for k, d in _HITS.items() if not d or d[-1] < now - 3600]:
            _HITS.pop(k, None)
    return True


def _client_ip(request: Request) -> str:
    # Fly-Client-IP (set by Fly's proxy, which replaces any value a client sends), else the
    # socket peer - never the first X-Forwarded-For hop, which the guest's own browser/script
    # can write to dodge RATE_PER_IP. Same rule as auth.client_ip (the module does not import
    # the host directly - feedback_host.py is its only seam, test_feedback_kit.py).
    fly = (request.headers.get("fly-client-ip") or "").strip()
    if fly:
        return fly
    return request.client.host if request.client else "?"


_URL_BAD_CHARS = set("\"'<>\\`")


def _is_https_url(v) -> bool:
    """True for an absolute https:// link with a host and nothing that could break out
    of an href (whitespace, quotes, angle brackets, backslashes)."""
    from urllib.parse import urlsplit
    s = str(v or "").strip()
    if not s or any(c.isspace() or c in _URL_BAD_CHARS for c in s):
        return False
    try:
        u = urlsplit(s)
    except ValueError:
        return False
    return u.scheme.lower() == "https" and bool(u.netloc)


def _review_url_or_400(v):
    """The google_review_url to store: None for blank, the https:// link as typed, 400
    for anything else (a javascript: / data: / http: link would be rendered into the
    guest's thank-you <a href> on the public survey page)."""
    s = str(v or "").strip()[:500]
    if not s:
        return None
    if not _is_https_url(s):
        raise HTTPException(400, "ลิงก์รีวิว Google ต้องขึ้นต้นด้วย https://")
    return s


def _base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def _new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))


def _link(con, code: str):
    row = con.execute("SELECT branch_id, branch_name, code, google_review_url, active, scans, responses "
                      "FROM feedback_survey_link WHERE code=?", (code,)).fetchone()
    return dict(row) if row else None


def purge_contacts(con, today: str = None, days: int = CONTACT_DAYS) -> int:
    """Blank the call-back contact of answers older than `days` (PDPA: kept only
    as long as the call back needs it)."""
    cutoff = (datetime.date.fromisoformat(today or _thai_today()) - datetime.timedelta(days=days)).isoformat()
    cur = con.execute("UPDATE customer_feedback SET contact=NULL WHERE contact IS NOT NULL AND rated_at < ?", (cutoff,))
    return cur.rowcount or 0


# ── the public side ──────────────────────────────────────────────────────────
_CSS = """
*{box-sizing:border-box} body{margin:0;font-family:'KIRA UI','Noto Sans Thai',sans-serif;background:#f0f7f5;color:#1f2937}
.wrap{max-width:480px;margin:0 auto;padding:20px 16px 40px} .brand{font-weight:800;color:#0f766e;font-size:14px;letter-spacing:.04em}
h1{font-size:22px;margin:.3rem 0 .2rem} .sub{color:#6b7280;font-size:13px;margin-bottom:14px}
.faces{display:flex;gap:6px;justify-content:space-between;margin:8px 0 4px}
.face{flex:1;border:2px solid #e5e7eb;background:#fff;border-radius:14px;padding:10px 0 6px;text-align:center;cursor:pointer;font-size:30px;line-height:1;transition:.12s}
.face small{display:block;font-size:10px;color:#6b7280;margin-top:6px} .face.on{border-color:#0f766e;background:#ecfdf5;transform:scale(1.06)}
.lbl{font-size:13px;font-weight:600;margin:18px 0 6px} .chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{border:1px solid #d1d5db;background:#fff;border-radius:999px;padding:7px 12px;font-size:13px;cursor:pointer}
.chip.on{background:#0f766e;color:#fff;border-color:#0f766e}
textarea,input{width:100%;border:1px solid #d1d5db;border-radius:10px;padding:10px;font-size:15px;font-family:inherit;background:#fff}
textarea{min-height:84px;resize:vertical} .note{font-size:11px;color:#6b7280;margin-top:4px}
button.go{width:100%;margin-top:18px;background:#0f766e;color:#fff;border:0;border-radius:12px;padding:14px;font-size:16px;font-weight:700;cursor:pointer}
button.go:disabled{opacity:.5} .thanks{display:none;text-align:center;padding:30px 10px}
.thanks .big{font-size:48px} .thanks h2{margin:.4rem 0} .thanks p{color:#4b5563;font-size:14px}
a.gbtn{display:inline-block;margin-top:14px;background:#1a73e8;color:#fff;text-decoration:none;border-radius:12px;padding:12px 18px;font-weight:700}
.err{color:#b91c1c;font-size:13px;margin-top:8px;display:none} .hp{position:absolute;left:-9999px;opacity:0;height:0}
"""


def _survey_page(link: dict, code: str) -> str:
    b = html.escape(link["branch_name"] or "")
    faces = "".join(f'<div class="face" data-v="{v}" role="button" tabindex="0">{ico}<small>{lab}</small></div>' for v, ico, lab in FACES)
    chips = "".join(f'<span class="chip" data-a="{html.escape(k)}" role="button" tabindex="0">{html.escape(lab)}</span>' for k, lab in ASPECTS)
    return f"""<!doctype html><html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>โอชิเน {b} · บอกเราหน่อย</title><link rel="stylesheet" href="/vendor/kira-ui-font.css?v=7.0"><style>{_CSS}</style></head><body><div class="wrap">
<div class="brand">OSHINEI · {b}</div>
<form id="f" autocomplete="off">
<h1>วันนี้เป็นอย่างไรบ้าง?</h1><div class="sub">ใช้เวลาไม่ถึง 20 วินาที ทุกคำตอบถึงผู้จัดการสาขาโดยตรง</div>
<div class="faces" id="faces">{faces}</div>
<div class="lbl">เรื่องไหนบ้าง <span style="font-weight:400;color:#6b7280">(เลือกได้หลายข้อ)</span></div>
<div class="chips" id="chips">{chips}</div>
<div class="lbl">เล่าให้เราฟังหน่อย <span style="font-weight:400;color:#6b7280">(ไม่บังคับ)</span></div>
<textarea id="txt" maxlength="1000" placeholder="เช่น แซลมอนสดมาก แต่รอเติมนานหน่อย"></textarea>
<div class="lbl">อยากให้เราติดต่อกลับไหม? <span style="font-weight:400;color:#6b7280">(ไม่บังคับ)</span></div>
<input id="contact" maxlength="120" placeholder="เบอร์โทร หรือ LINE ID">
<div class="note">ใช้เพื่อติดต่อกลับเรื่องนี้เท่านั้น ระบบลบข้อมูลติดต่ออัตโนมัติหลัง {CONTACT_DAYS} วัน</div>
<input class="hp" id="website" name="website" tabindex="-1" autocomplete="off">
<button class="go" id="go" type="submit">ส่งความเห็น</button>
<div class="err" id="err"></div>
</form>
<div class="thanks" id="thanks"><div class="big">🙏</div><h2>ขอบคุณค่ะ</h2><p id="tmsg">ความเห็นของคุณถึงผู้จัดการสาขาแล้ว</p><div id="gwrap"></div></div>
</div>
<script>
(function(){{
  var code={code!r}, t0=Date.now(), rating=0, aspects=[];
  var $=function(s){{return document.querySelector(s)}};
  document.querySelectorAll('.face').forEach(function(el){{ el.onclick=function(){{ rating=+el.dataset.v; document.querySelectorAll('.face').forEach(function(x){{x.classList.toggle('on', x===el)}}); }}; }});
  document.querySelectorAll('.chip').forEach(function(el){{ el.onclick=function(){{ el.classList.toggle('on'); aspects=[].slice.call(document.querySelectorAll('.chip.on')).map(function(x){{return x.dataset.a}}); }}; }});
  $('#f').onsubmit=function(e){{
    e.preventDefault(); var err=$('#err'); err.style.display='none';
    if(!rating){{ err.textContent='แตะหน้าที่ตรงกับความรู้สึกของคุณก่อนนะคะ'; err.style.display='block'; return; }}
    $('#go').disabled=true;
    fetch('/api/feedback/survey/'+encodeURIComponent(code), {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{rating:rating, aspects:aspects, text:$('#txt').value, contact:$('#contact').value, website:$('#website').value, t0:t0}})}})
    .then(function(r){{ return r.json().then(function(j){{ return [r.status, j]; }}); }})
    .then(function(x){{
      if(x[0]!==200){{ throw new Error((x[1]&&x[1].detail)||'ส่งไม่สำเร็จ'); }}
      $('#f').style.display='none'; var t=$('#thanks'); t.style.display='block';
      if(x[1].callback) $('#tmsg').textContent='ขออภัยในประสบการณ์ครั้งนี้ค่ะ ผู้จัดการสาขาจะติดต่อกลับโดยเร็วที่สุด';
      var gu=String(x[1].review_url||''); if(gu && gu.slice(0,8).toLowerCase()==='https://') $('#gwrap').innerHTML='<p>ถ้าประทับใจ ช่วยบอกต่อบน Google ได้ไหมคะ</p><a class="gbtn" href="'+gu.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;')+'" rel="noopener">⭐ รีวิวเราบน Google</a>';
      try{{ localStorage.setItem('oshinei_survey_'+code, new Date().toISOString().slice(0,10)); }}catch(e){{}}
    }})
    .catch(function(e){{ err.textContent=e.message; err.style.display='block'; $('#go').disabled=false; }});
  }};
}})();
</script></body></html>"""


def _qr_svg(url: str, size_mm: int = 62) -> str:
    """The QR as inline SVG, drawn here rather than by a script in the reader's browser.

    It used to be built client-side by a CDN copy of qrcode.js. On a screen a missing script is an
    empty box you notice; on the poster somebody already printed and taped to a counter it is an
    empty box nobody notices until a month of answers has not arrived. Inline SVG also prints at the
    printer's own resolution instead of a 300px bitmap, which is what makes a small code scan on the
    first try. `error="m"` recovers ~15% — enough to survive a scuff or a thumbprint on a card that
    lives next to a till.
    """
    qr = segno.make(url, error="m")
    # omitsize drops width/height and leaves only the viewBox, so the CSS below sizes it in mm and the
    # printer rasterises the vector itself
    svg = qr.svg_inline(scale=1, border=0, svgclass=None, lineclass=None, omitsize=True)
    return svg.replace("<svg ", f'<svg width="{size_mm}mm" height="{size_mm}mm" role="img" aria-label="QR code" ', 1)


def _poster_page(link: dict, url: str) -> str:
    b = html.escape(link["branch_name"] or "")
    u = html.escape(url)
    code = html.escape(link["code"] or "")
    return f"""<!doctype html><html lang="th"><head><meta charset="utf-8"><title>โปสเตอร์ QR · {b}</title>
<link rel="stylesheet" href="/vendor/kira-ui-font.css?v=7.0">
<style>
@page{{size:A5 portrait;margin:0}}
*{{box-sizing:border-box}}
body{{margin:0;background:#f0f4f8;color:#0f172a;
     font-family:'KIRA UI','Noto Sans Thai',sans-serif}}

/* the sheet is exactly A5 so what you see is what the printer puts on the card */
.sheet{{width:148mm;height:210mm;margin:8mm auto;background:#fff;position:relative;overflow:hidden;
       display:flex;flex-direction:column;align-items:center;
       padding:16mm 12mm 12mm;box-shadow:0 6px 28px rgba(15,23,42,.16)}}

/* the brand indigo (--indigo in docs/ui-kit/tokens.css), taken darker: ink on paper does not have a
   backlit screen behind it, so the shade that reads as brand on the dashboard reads as pale in print */
.sheet::before{{content:"";position:absolute;inset:0 0 auto 0;height:9mm;
               background:linear-gradient(90deg,#1e1b4b 0%,#3730a3 55%,#4f46e5 100%)}}

.brand{{font-size:11pt;font-weight:700;letter-spacing:.16em;color:#312e81;text-transform:uppercase}}
.branch{{font-size:12pt;font-weight:600;color:#334155;margin-top:1mm}}

h1{{font-size:27pt;line-height:1.15;font-weight:700;margin:9mm 0 0;text-align:center;letter-spacing:-.01em}}
.sub{{font-size:12.5pt;line-height:1.5;color:#334155;text-align:center;margin-top:4mm;max-width:104mm}}

/* the QR is the thing the poster exists for, so nothing competes with it */
.qr-wrap{{margin-top:auto;margin-bottom:auto;padding:7mm;background:#fff;border-radius:6mm;
         border:2.5px solid #312e81;box-shadow:0 3px 14px rgba(30,27,75,.16)}}
.qr-wrap svg{{display:block}}
.scan{{margin-top:5mm;font-size:12pt;font-weight:600;color:#312e81;text-align:center;letter-spacing:.01em}}

.foot{{width:100%;margin-top:auto;padding-top:6mm;border-top:1px solid #cbd5e1;text-align:center}}
.foot .thanks{{font-size:11.5pt;color:#334155}}
.foot .url{{margin-top:2.5mm;font-size:8.5pt;color:#64748b;word-break:break-all;
           font-family:ui-monospace,Consolas,monospace}}
.foot .code{{font-weight:700;color:#312e81;letter-spacing:.06em}}

.tools{{text-align:center;margin:10px 0 0}}
.tools button{{padding:9px 20px;font-size:14px;font-weight:600;color:#fff;background:#312e81;
              border:0;border-radius:8px;cursor:pointer}}
.tools .hint{{display:block;margin-top:6px;font-size:12px;color:#475569}}
@media print{{
  body{{background:#fff}}
  .sheet{{margin:0;box-shadow:none;width:148mm;height:210mm}}
  .tools{{display:none}}
}}
</style></head><body>
<div class="tools">
  <button onclick="window.print()">🖨 พิมพ์การ์ด A5</button>
  <span class="hint">ตั้งค่าเครื่องพิมพ์: ขนาด A5 · ขอบกระดาษ "ไม่มี" (None) · ปิด "ปรับขนาดให้พอดี"</span>
</div>

<div class="sheet">
  <div class="brand">OSHINEI</div>
  <div class="branch">สาขา{b}</div>

  <h1>วันนี้เป็นอย่างไรบ้าง?</h1>
  <div class="sub">สแกนเพื่อบอกเรา ใช้เวลาไม่ถึง 20 วินาที<br>ทุกคำตอบส่งถึงผู้จัดการสาขาโดยตรง</div>

  <div class="qr-wrap">{_qr_svg(url)}</div>
  <div class="scan">📷 เปิดกล้องมือถือแล้วสแกนได้เลย</div>

  <div class="foot">
    <div class="thanks">ขอบคุณที่ช่วยให้เราดีขึ้นทุกวัน 🙏</div>
    <div class="url">{u}<br><span class="code">รหัส {code}</span></div>
  </div>
</div>
</body></html>"""


@router.get("/s/{code}", response_class=HTMLResponse)
def survey_page(code: str):
    with get_db() as con:
        _ensure(con)
        link = _link(con, code)
        if not link or not link["active"]:
            return HTMLResponse("<!doctype html><meta charset='utf-8'><body style='font-family:sans-serif;text-align:center;padding:40px'>"
                                "<h2>ลิงก์นี้ไม่ใช้งานแล้ว</h2><p>ขอบคุณที่ตั้งใจบอกเรา บอกพนักงานได้เลยค่ะ</p></body>", status_code=404)
        con.execute("UPDATE feedback_survey_link SET scans = COALESCE(scans,0) + 1 WHERE code=?", (code,))
        con.commit()
    return HTMLResponse(_survey_page(link, code))


@router.get("/s/{code}/print", response_class=HTMLResponse)
def survey_poster(code: str, request: Request):
    with get_db() as con:
        _ensure(con)
        link = _link(con, code)
    if not link:
        raise HTTPException(404, "unknown code")
    return HTMLResponse(_poster_page(link, f"{_base_url(request)}/s/{code}"))


@router.post("/api/feedback/survey/{code}")
async def survey_submit(code: str, request: Request):
    """One guest's answer. Silently accepted (nothing stored) when the honeypot
    is filled or the page was open for less than MIN_SECONDS_ON_PAGE."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "bad body")
    if not isinstance(body, dict):
        raise HTTPException(400, "bad body")
    try:
        rating = int(body.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0
    if rating < 1 or rating > 5:
        raise HTTPException(400, "เลือกคะแนน 1–5")
    text = str(body.get("text") or "").strip()[:1000]
    contact = str(body.get("contact") or "").strip()[:120]
    aspects = [a for a in (body.get("aspects") or []) if isinstance(a, str) and a in dict(ASPECTS)][:8]
    bot = bool(str(body.get("website") or "").strip())
    try:
        t0 = float(body.get("t0") or 0) / 1000.0
        if t0 and (_now() - t0) < MIN_SECONDS_ON_PAGE:
            bot = True
    except (TypeError, ValueError):
        pass
    with get_db() as con:
        _ensure(con)
        link = _link(con, code)
        if not link or not link["active"]:
            raise HTTPException(404, "ลิงก์นี้ไม่ใช้งานแล้ว")
        if bot:
            return {"ok": True, "review_url": None, "callback": False}
        ip = _client_ip(request)
        if not _rate_ok(f"code:{code}", RATE_PER_CODE) or not _rate_ok(f"ip:{ip}", RATE_PER_IP):
            raise HTTPException(429, "ส่งบ่อยเกินไป ลองใหม่ในอีกสักครู่นะคะ")
        today = _thai_today()
        row = {"date": today, "branch": link["branch_name"], "source": SOURCE, "rating": float(rating), "text": text,
               "external_id": f"survey:{uuid.uuid4().hex}", "kind": "survey",
               "sentiment_hint": "neg" if rating <= 2 else ("pos" if rating >= 4 else None),
               "themes_extra": aspects, "contact": contact or None}
        out = insert_reviews(con, [row], by="survey", today=today)
        con.execute("UPDATE feedback_survey_link SET responses = COALESCE(responses,0) + ? WHERE code=?", (out["inserted"], code))
        purge_contacts(con, today)
        con.commit()
    gurl = link.get("google_review_url") or None          # a stored non-https link is never handed out
    return {"ok": True, "review_url": gurl if (rating >= 4 and _is_https_url(gurl)) else None,
            "callback": bool(contact) and rating <= 3}


# ── the manager's side ───────────────────────────────────────────────────────
def _links_table(con, base: str) -> list:
    today = _thai_today()
    since = (datetime.date.fromisoformat(today) - datetime.timedelta(days=30)).isoformat()
    stats = {r["branch_name"]: dict(r) for r in con.execute(
        "SELECT branch_name, COUNT(*) AS n30, AVG(rating) AS avg30, "
        "SUM(CASE WHEN sentiment='neg' THEN 1 ELSE 0 END) AS neg30 "
        "FROM customer_feedback WHERE COALESCE(kind,'review')='survey' AND rated_at >= ? GROUP BY branch_name", (since,))}
    links = {r["branch_id"]: dict(r) for r in con.execute("SELECT * FROM feedback_survey_link")}
    out = []
    for b in [x for x in branch_rows(con) if x["active"]]:
        lk = links.get(b["branch_id"])
        st = stats.get(b["name"], {})
        out.append({"branch_id": b["branch_id"], "branch": b["name"], "code": lk["code"] if lk else None,
                    "url": f"{base}/s/{lk['code']}" if lk else None, "print_url": f"/s/{lk['code']}/print" if lk else None,
                    "google_review_url": (lk or {}).get("google_review_url") or "", "active": bool((lk or {}).get("active", 0)),
                    "scans": (lk or {}).get("scans") or 0, "responses": (lk or {}).get("responses") or 0,
                    "n30": st.get("n30") or 0, "neg30": st.get("neg30") or 0,
                    "avg30": round(st["avg30"], 2) if st.get("avg30") is not None else None})
    return out


@router.get("/api/feedback/survey-links")
def survey_links(request: Request):
    scope = scope_branch(request)
    with get_db() as con:
        _ensure(con)
        links = _links_table(con, _base_url(request))
        if scope:
            links = [x for x in links if x["branch"] == scope]
        return {"links": links, "aspects": [k for k, _ in ASPECTS], "contact_days": CONTACT_DAYS}


@router.post("/api/feedback/survey-links", dependencies=[Depends(require_write_token)])
async def survey_link_save(request: Request):
    """{branch_id, google_review_url?, active?, by?} — creates the branch's code
    on first call; later calls update the url / the switch."""
    body = await request.json()
    scope = scope_branch(request)
    if body.get("all"):                      # every active branch without a code gets one
        if scope:
            raise HTTPException(403, "สร้างได้เฉพาะสาขาของคุณ")
        with get_db() as con:
            _ensure(con)
            have = {r[0] for r in con.execute("SELECT branch_id FROM feedback_survey_link")}
            made = 0
            for b in [x for x in branch_rows(con) if x["active"]]:
                if b["branch_id"] in have:
                    continue
                code = _new_code()
                while con.execute("SELECT 1 FROM feedback_survey_link WHERE code=?", (code,)).fetchone():
                    code = _new_code()
                con.execute("INSERT INTO feedback_survey_link(branch_id, branch_name, code, active, created_at, created_by) VALUES (?,?,?,1,?,?)",
                            (b["branch_id"], b["name"], code, _thai_today(), str(body.get("by") or "")[:60]))
                made += 1
            con.commit()
            return {"created": made, "links": _links_table(con, _base_url(request))}
    try:
        bid = int(body.get("branch_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "branch_id")
    with get_db() as con:
        _ensure(con)
        b = next((x for x in branch_rows(con) if x["branch_id"] == bid), None)
        if not b:
            raise HTTPException(404, "ไม่พบสาขา")
        if scope and b["name"] != scope:
            raise HTTPException(403, "สาขานี้อยู่นอกสิทธิ์ของคุณ")
        row = con.execute("SELECT * FROM feedback_survey_link WHERE branch_id=?", (bid,)).fetchone()
        if row is None:
            code = _new_code()
            while con.execute("SELECT 1 FROM feedback_survey_link WHERE code=?", (code,)).fetchone():
                code = _new_code()
            con.execute("INSERT INTO feedback_survey_link(branch_id, branch_name, code, google_review_url, active, created_at, created_by) "
                        "VALUES (?,?,?,?,?,?,?)", (bid, b["name"], code, _review_url_or_400(body.get("google_review_url")),
                                                   1 if body.get("active", True) else 0, _thai_today(), str(body.get("by") or "")[:60]))
        else:
            sets, vals = ["branch_name=?"], [b["name"]]
            if "google_review_url" in body:
                sets.append("google_review_url=?"); vals.append(_review_url_or_400(body.get("google_review_url")))
            if "active" in body:
                sets.append("active=?"); vals.append(1 if body.get("active") else 0)
            vals.append(bid)
            con.execute(f"UPDATE feedback_survey_link SET {', '.join(sets)} WHERE branch_id=?", vals)
        con.commit()
        links = _links_table(con, _base_url(request))
    return next(x for x in links if x["branch_id"] == bid)
