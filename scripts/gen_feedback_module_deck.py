# -*- coding: utf-8 -*-
"""A 12-slide introduction deck for the เสียงลูกค้า (customer-voice) module.

For the audience that has never opened the tab: what problem it solves, the
nine channels it reads, the two things that make it more than a review inbox
(reading complaints against the till, and closing the loop on a fix), and what
turning a channel on costs in time. Pulled from the module's own docstrings
(backend/routers/feedback*.py, feedback_nlp.py, survey.py) and the live
rollout plan (backend/checklists/feedback_rollout.json) — nothing here is
invented; a number with no source in this repo is not in the deck.

    py scripts/gen_feedback_module_deck.py                # -> docs/FEEDBACK_MODULE_DECK.html + .pdf
    py scripts/gen_feedback_module_deck.py --no-pdf        # HTML only
"""
import argparse
import io
import json
import subprocess
from pathlib import Path
import sys as _sys
_sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'backend'))
from report_fonts import font_face_css  # KIRA UI, embedded (backend/report_fonts.py)

ROOT = Path(__file__).resolve().parents[1]
PLAN_JSON = ROOT / "backend" / "checklists" / "feedback_rollout.json"
OUT_HTML = ROOT / "docs" / "FEEDBACK_MODULE_DECK.html"
OUT_PDF = ROOT / "docs" / "FEEDBACK_MODULE_DECK.pdf"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")

try:
    import segno
except ImportError:
    segno = None


def esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def plan_stats():
    d = json.load(io.open(PLAN_JSON, encoding="utf-8"))
    phases = d["phases"]
    steps = sum(len(t["steps"]) for ph in phases for t in ph["tasks"])
    return {"phases": len(phases), "steps": steps, "horizon": d["horizon_days"]}


def qr_svg(url: str, size_mm: float = 26) -> str:
    if segno is None:
        return ""
    qr = segno.make(url, error="m")
    svg = qr.svg_inline(scale=1, border=0, svgclass=None, lineclass=None, omitsize=True)
    return svg.replace("<svg ", f'<svg width="{size_mm}mm" height="{size_mm}mm" ', 1)


CSS = font_face_css() + """
@page{size:338.67mm 190.5mm;margin:0}
*{box-sizing:border-box;-webkit-print-color-adjust:exact;print-color-adjust:exact}
html,body{margin:0;background:#e2e8f0}
body{font-family:'KIRA UI','Noto Sans Thai',sans-serif;color:#0f172a}
h1,h2,h3,.disp{font-family:'KIRA UI','Noto Sans Thai',sans-serif}

:root{
  --indigo:#1f3a6b; --indigo-mid:#1f3a6b; --indigo-deep:#16294d; --indigo-ink:#1e1b4b;
  --indigo-soft:#e8eef7; --indigo-line:#c9d5e8;
  --ink:#0f172a; --ink2:#334155; --ink3:#64748b; --ink4:#94a3b8;
  --paper:#ffffff; --page-bg:#f0f4f8; --line:#e2e8f0; --line-soft:#f1f5f9;
  --good:#0f9d78; --warn:#b45309; --bad:#be123c;
}

.slide{width:338.67mm;height:190.5mm;position:relative;overflow:hidden;background:var(--paper);
  page-break-after:always;break-after:page;display:flex;flex-direction:column}
.slide:last-child{page-break-after:auto;break-after:auto}

.kicker{font:700 10.5pt 'KIRA UI';letter-spacing:.14em;text-transform:uppercase;color:var(--indigo)}
.pageno{position:absolute;right:14mm;bottom:8mm;font:600 8.5pt ui-monospace;color:var(--ink4)}
.brandline{position:absolute;left:14mm;bottom:8mm;font:600 8.5pt 'KIRA UI';letter-spacing:.08em;color:var(--ink4)}

/* ── cover ── */
.cover{justify-content:center;padding:0 22mm;background:
  radial-gradient(120% 140% at 82% -10%,#e8eef7 0%,#ffffff 46%),var(--paper)}
.cover .band{position:absolute;inset:0 0 auto 0;height:5mm;
  background:linear-gradient(90deg,var(--indigo-ink) 0%,var(--indigo-deep) 45%,var(--indigo) 100%)}
.cover .wordmark{font:800 13pt 'KIRA UI';letter-spacing:.22em;color:var(--indigo-deep)}
.cover h1{font:800 46pt/1.08 'KIRA UI';margin:9mm 0 6mm;color:var(--ink);max-width:200mm}
.cover h1 em{font-style:normal;color:var(--indigo)}
.cover .sub{font:500 15pt/1.55 'KIRA UI';color:var(--ink2);max-width:150mm;margin:0 0 12mm}
.cover .chips{display:flex;gap:6mm;flex-wrap:wrap}
.chip{display:flex;align-items:center;gap:3mm;background:var(--indigo-soft);border:.6pt solid var(--indigo-line);
  border-radius:99px;padding:4mm 7mm;font:600 10.5pt 'KIRA UI';color:var(--indigo-deep)}
.chip b{font:800 15pt 'KIRA UI';color:var(--indigo-deep)}

/* ── generic content slide ── */
.pad{padding:14mm 16mm 12mm;flex:1;display:flex;flex-direction:column;justify-content:center}
.pad.top{justify-content:flex-start}
.pad.dense .lede{margin-bottom:5mm}
.slide h2{font:700 24pt 'KIRA UI';margin:2mm 0 3mm;color:var(--ink)}
.slide .lede{font:500 12.5pt/1.6 'KIRA UI';color:var(--ink2);max-width:230mm;margin:0 0 8mm}
.rule{height:2.4pt;width:26mm;background:var(--indigo);
  border-radius:2px;margin:4mm 0 0}

/* ── problem slide (3) ── */
.pain-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8mm;margin-top:4mm}
.pain{background:var(--page-bg);border:.6pt solid var(--line);border-radius:5mm;padding:8mm}
.pain .n{font:800 22pt 'KIRA UI';color:var(--indigo)}
.pain h3{font:700 13pt 'KIRA UI';margin:3mm 0 2mm;color:var(--ink)}
.pain p{font:400 10.8pt/1.55 'KIRA UI';color:var(--ink2);margin:0}

/* ── flow (4) ── */
.flow{display:flex;align-items:stretch;gap:0;margin-top:6mm}
.flow .step{flex:1;background:var(--page-bg);border:.6pt solid var(--line);border-radius:5mm;
  padding:8mm 7mm;position:relative;display:flex;flex-direction:column;gap:2mm}
.flow .step .k{font:700 8.5pt 'KIRA UI';letter-spacing:.1em;text-transform:uppercase;color:var(--indigo)}
.flow .step h3{font:700 13.5pt 'KIRA UI';margin:0;color:var(--ink)}
.flow .step p{font:400 10.3pt/1.5 'KIRA UI';color:var(--ink2);margin:0}
.flow .arrow{width:11mm;display:flex;align-items:center;justify-content:center;color:var(--indigo);font-size:16pt;flex-shrink:0}

/* ── channel grid (5) ── */
.ch-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:4.5mm;margin-top:3mm}
.ch{background:var(--paper);border:.6pt solid var(--line);border-radius:4mm;padding:4.5mm 6mm;
  box-shadow:0 1px 2px rgba(15,23,42,.04)}
.ch .e{font-size:13pt}
.ch h3{font:700 10.8pt 'KIRA UI';margin:1.6mm 0 1mm;color:var(--ink)}
.ch p{font:400 9.1pt/1.4 'KIRA UI';color:var(--ink3);margin:0}
.ch .tag{display:inline-block;margin-top:1.6mm;font:600 7.6pt 'KIRA UI';padding:.5mm 2.4mm;border-radius:99px}
.tag.live{background:#ecfdf5;color:#047857}
.tag.upload{background:#fffbeb;color:#92400e}
.tag.paid{background:#fef2f2;color:#b91c1c}

/* ── QR deep dive (6) ── */
.qr-wrap{display:flex;gap:14mm;align-items:center;margin-top:5mm}
.qr-card{flex:0 0 auto;background:var(--paper);border:2px solid var(--indigo-deep);border-radius:6mm;
  padding:7mm;box-shadow:0 3px 14px rgba(30,27,75,.16)}
.qr-facts{flex:1;display:flex;flex-direction:column;gap:4mm}
.fact{display:flex;gap:4mm;align-items:flex-start}
.fact .dot{flex:0 0 auto;width:7mm;height:7mm;border-radius:99px;background:var(--indigo-soft);
  border:.6pt solid var(--indigo-line);display:flex;align-items:center;justify-content:center;
  font:700 8.5pt 'KIRA UI';color:var(--indigo-deep);margin-top:.5mm}
.fact p{font:400 10.8pt/1.55 'KIRA UI';color:var(--ink2);margin:0}
.fact b{color:var(--ink)}

/* ── analysis (7) ── */
.split{display:flex;gap:10mm;margin-top:4mm}
.split>div{flex:1}
.eng{background:var(--page-bg);border:.6pt solid var(--line);border-radius:5mm;padding:7mm 8mm;height:100%}
.eng h3{font:700 13pt 'KIRA UI';margin:0 0 2mm;color:var(--indigo-deep)}
.eng ul{margin:2mm 0 0;padding-left:5mm}
.eng li{font:400 10.6pt/1.55 'KIRA UI';color:var(--ink2);margin:1.4mm 0}
.pillbar{display:flex;gap:2.5mm;flex-wrap:wrap;margin-top:5mm}
.pill{font:600 9pt 'KIRA UI';padding:2mm 4mm;border-radius:99px;background:var(--indigo-soft);
  color:var(--indigo-deep);border:.5pt solid var(--indigo-line)}

/* ── business cross-read (8) ── */
.cross{display:grid;grid-template-columns:repeat(3,1fr);gap:7mm;margin-top:5mm}
.cross .card{background:var(--paper);border:.6pt solid var(--line);border-radius:5mm;padding:7mm;
  display:flex;flex-direction:column;gap:2mm}
.cross .card .e{font-size:16pt}
.cross .card h3{font:700 12pt 'KIRA UI';margin:0;color:var(--ink)}
.cross .card p{font:400 10.3pt/1.55 'KIRA UI';color:var(--ink2);margin:0}

/* ── loop (9) ── */
.loop{display:flex;align-items:center;gap:0;margin-top:8mm}
.loop .node{flex:1;text-align:center}
.loop .node .circ{width:22mm;height:22mm;margin:0 auto 3mm;border-radius:99px;background:var(--indigo-soft);
  border:1.4pt solid var(--indigo-line);display:flex;align-items:center;justify-content:center;font-size:15pt}
.loop .node h3{font:700 11.5pt 'KIRA UI';margin:0 0 1mm;color:var(--ink)}
.loop .node p{font:400 9.3pt/1.4 'KIRA UI';color:var(--ink3);margin:0;padding:0 4mm}
.loop .conn{flex:0 0 16mm;display:flex;align-items:flex-start;justify-content:center;padding-top:10mm;
  color:var(--indigo);font-size:15pt}
.loop-note{margin-top:8mm;background:var(--indigo-soft);border-left:3pt solid var(--indigo);border-radius:2mm;
  padding:5mm 7mm;font:500 10.8pt/1.6 'KIRA UI';color:var(--indigo-deep)}

/* ── competitor + ai (10) ── */
.two{display:flex;gap:10mm;margin-top:4mm}
.two>div{flex:1;background:var(--page-bg);border:.6pt solid var(--line);border-radius:5mm;padding:7mm 8mm}
.two h3{font:700 13pt 'KIRA UI';margin:0 0 2mm;color:var(--indigo-deep)}
.two p{font:400 10.6pt/1.6 'KIRA UI';color:var(--ink2);margin:0 0 2mm}
.two .stat{font:800 20pt 'KIRA UI';color:var(--ink)}

/* ── rollout (11) ── */
.gantt-wrap{margin-top:4mm}
.gantt{display:flex;flex-direction:column;gap:1.4mm}
.grow{display:flex;align-items:center;gap:4mm}
.glabel{flex:0 0 62mm;font:500 8.6pt 'KIRA UI';color:var(--ink2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gtrack{flex:1;position:relative;height:4.6mm;background:var(--line-soft);border-radius:2px}
.gbar{position:absolute;top:0;height:100%;border-radius:2px}
.gdays{flex:0 0 20mm;font:500 7.8pt ui-monospace;color:var(--ink4);text-align:left}
.gaxis{margin-top:1mm}
.gaxis .gtrack{background:none;height:3mm}
.gtick{position:absolute;top:0;transform:translateX(-50%);font:500 7.6pt ui-monospace;color:var(--ink4)}
.rollstat{display:flex;gap:8mm;margin-top:5mm}
.rs{flex:1;background:var(--page-bg);border:.6pt solid var(--line);border-radius:5mm;padding:6mm 7mm;text-align:center}
.rs .v{font:800 20pt 'KIRA UI';color:var(--indigo)}
.rs .l{font:500 9.5pt 'KIRA UI';color:var(--ink3);margin-top:1mm}

/* ── CTA (12) ── */
.cta{justify-content:center;align-items:center;text-align:center;background:var(--indigo-ink);color:#fff}
.cta h2{font:800 30pt 'KIRA UI';color:#fff;margin:0 0 5mm}
.cta .lede{color:#e8eef7;margin:0 auto 10mm;text-align:center}
.cta-grid{display:flex;gap:8mm;justify-content:center;max-width:230mm;margin:0 auto}
.cta .card{flex:1;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.22);border-radius:5mm;
  padding:7mm 8mm;text-align:left;backdrop-filter:blur(2px)}
.cta .card .n{font:800 15pt 'KIRA UI';color:#c9d5e8}
.cta .card h3{font:700 12.5pt 'KIRA UI';margin:2mm 0 1.5mm;color:#fff}
.cta .card p{font:400 10pt/1.5 'KIRA UI';color:#e8eef7;margin:0}
"""


def slide(inner: str, no: int, extra_class: str = "") -> str:
    return (f'<section class="slide {extra_class}">{inner}'
            f'<div class="brandline">OSHINEI · เสียงลูกค้า</div>'
            f'<div class="pageno">{no:02d} / 12</div></section>')


def build_html() -> str:
    stats = plan_stats()

    # ── 1. cover ─────────────────────────────────────────────────────────────
    s1 = slide(f'''<div class="band"></div>
    <div class="wordmark">OSHINEI</div>
    <h1>โมดูล <em>เสียงลูกค้า</em><br>ฟังทุกช่องทาง ให้ทุกสาขาได้ยิน</h1>
    <p class="sub">ระบบเดียวที่ดึงรีวิวและคอมเมนต์จากทุกช่องทางที่ร้านเป็นเจ้าของ วิเคราะห์ความรู้สึกและหัวข้อให้อัตโนมัติ
    แล้วปิดวงจรด้วยการมอบหมายงานและวัดผลว่าดีขึ้นจริงหรือไม่</p>
    <div class="chips">
      <div class="chip"><b>9</b>&nbsp;ช่องทาง</div>
      <div class="chip"><b>28</b>&nbsp;สาขา</div>
      <div class="chip"><b>2</b>&nbsp;เอนจินวิเคราะห์ (Claude + เล็กซิคอนไทย)</div>
    </div>''', 1, "cover")

    # ── 2. the problem ───────────────────────────────────────────────────────
    s2 = slide(f'''<div class="pad">
      <div class="kicker">ทำไมต้องมีโมดูลนี้</div>
      <h2>รีวิวสาธารณะน้อยเกินกว่าจะสรุปอะไรได้ทุกเดือน</h2>
      <p class="lede">ร้านมี 28 สาขา รีวิวสาธารณะที่ตกถึงแต่ละสาขาต่อเดือนมีเพียงหยิบมือ กระจายอยู่คนละแอป
      ไม่มีใครมีเวลาไล่อ่านทุกช่องทางทุกวัน และเมื่ออ่านแล้วก็ไม่มีที่บันทึกว่าทำอะไรไปแล้วบ้าง</p>
      <div class="pain-grid">
        <div class="pain"><div class="n">28 สาขา</div><h3>ข้อมูลน้อยเกินไป</h3>
          <p>รีวิวสาธารณะต่อสาขาต่อเดือนน้อยเกินกว่าจะเห็นแนวโน้ม ยิ่งแยกรายสาขายิ่งไม่มีนัยสำคัญ</p></div>
        <div class="pain"><div class="n">6+ แอป</div><h3>กระจัดกระจาย</h3>
          <p>Google · Facebook · Instagram · TikTok · Grab · LINE MAN · foodpanda — ไม่มีใครเปิดดูครบทุกวัน</p></div>
        <div class="pain"><div class="n">0 บันทึก</div><h3>ปิดวงจรไม่ได้</h3>
          <p>อ่านเจอปัญหาแล้วบอกปากเปล่า ไม่มีเจ้าของงาน ไม่มีวันครบกำหนด ไม่มีใครวัดว่าหลังแก้ดีขึ้นจริงไหม</p></div>
      </div>
    </div>''', 2)

    # ── 3. what it is / flow ─────────────────────────────────────────────────
    s3 = slide(f'''<div class="pad">
      <div class="kicker">ภาพรวม</div>
      <h2>ดึง → วิเคราะห์ → แสดงในแท็บ → ปิดวงจร</h2>
      <p class="lede">งานประจำทุกเช้าดึงรีวิว/คอมเมนต์ใหม่จากทุกช่องทางที่เปิดไว้ วิเคราะห์อัตโนมัติ แล้วขึ้นในแท็บ
      เสียงลูกค้า พร้อมกล่องงานสำหรับสิ่งที่ต้องตามต่อ</p>
      <div class="flow">
        <div class="step"><span class="k">1 · ดึง</span><h3>รับเสียงจากทุกช่องทาง</h3>
          <p>งาน Task Scheduler ทุกเช้า 09:00 ดึงของใหม่จากช่องทางที่เปิดไว้ + อัปโหลดเองสำหรับ Grab/LINE MAN/foodpanda</p></div>
        <div class="arrow">→</div>
        <div class="step"><span class="k">2 · วิเคราะห์</span><h3>ความรู้สึก + หัวข้อ + เมนู</h3>
          <p>Claude เมื่อมี API key ไม่มีก็ใช้เล็กซิคอนไทยที่ตั้งไว้ในระบบ ทุกแถวบันทึกว่าเอนจินไหนวิเคราะห์</p></div>
        <div class="arrow">→</div>
        <div class="step"><span class="k">3 · แสดงผล</span><h3>แท็บเดียวเห็นทั้งเครือ</h3>
          <p>สรุป ความรู้สึก หัวข้อ เมนูที่ถูกพูดถึง รายสาขา พร้อมรีวิวลบล่าสุดที่ต้องตอบก่อน</p></div>
        <div class="arrow">→</div>
        <div class="step"><span class="k">4 · ปิดวงจร</span><h3>งาน → เจ้าของ → วัดผล</h3>
          <p>หัวข้อที่พบซ้ำกลายเป็นงานมีเจ้าของและกำหนดวันครบ วัดจำนวนเสียงลบก่อน-หลังว่าดีขึ้นจริงหรือไม่</p></div>
      </div>
    </div>''', 3)

    # ── 4. channels ───────────────────────────────────────────────────────────
    channels = [
        ("", "Google Business Profile", "รีวิวทุกสาขา: ดาว ข้อความ วันที่ การตอบกลับของสาขา — ดึงทุกคืนผ่าน API หรือเริ่มได้ทันทีด้วย Takeout", "live"),
        ("", "Facebook Page", "Recommendations (แนะนำ/ไม่แนะนำ) และคอมเมนต์ใต้โพสต์ของเพจ", "live"),
        ("", "แบบสอบถาม QR หน้าร้าน", "ช่องทางของร้านเอง: หน้า 1–5 + หัวข้อ + ข้อความ + เบอร์ติดต่อกลับ จากทุกโต๊ะทุกวัน ไม่ต้องขอสิทธิ์ใคร", "live"),
        ("", "Instagram", "คอมเมนต์ใต้โพสต์ของร้าน โพสต์ที่แท็กบัญชี และ @mention ผ่าน webhook", "live"),
        ("", "TikTok", "คอมเมนต์ใต้วิดีโอของร้าน พร้อมตอบกลับได้จากในแท็บ", "live"),
        ("▶️", "YouTube + ข่าว + Alerts", "วิดีโอ/คอมเมนต์ที่พูดถึงร้าน และการพูดถึงทั่วเว็บผ่าน Google News + Google Alerts", "live"),
        ("", "Grab · LINE MAN · foodpanda", "รีวิวจาก merchant portal ของแต่ละแพลตฟอร์ม ส่งออกเป็น CSV/XLSX แล้วอัปโหลดในแท็บ", "upload"),
        ("𝕏", "X (Twitter)", "โพสต์สาธารณะที่พูดถึงร้านในรอบ 7 วันล่าสุด — ต้องสมัคร API แบบเสียเงิน จึงให้เจ้าของตัดสินใจก่อน", "paid"),
        ("", "Telegram", "ไม่ใช่แหล่งรีวิว แต่เป็นช่องแจ้งเตือน: รีวิวลบใหม่ทันที + สรุปรายสัปดาห์ทุกวันจันทร์", "live"),
    ]
    tag_label = {"live": "พร้อมใช้ทันที", "upload": "อัปโหลดเอง", "paid": "ต้องสมัคร (เสียเงิน)"}
    ch_html = "".join(
        f'<div class="ch"><span class="e">{e}</span><h3>{esc(n)}</h3><p>{esc(d)}</p>'
        f'<span class="tag {tag}">{tag_label[tag]}</span></div>'
        for e, n, d, tag in channels)
    s4 = slide(f'''<div class="pad top dense">
      <div class="kicker">ช่องทางที่รองรับ</div>
      <h2>9 ช่องทาง ผ่าน API ทางการเท่านั้น</h2>
      <p class="lede">ดึงผ่าน API ที่ร้านเป็นเจ้าของบัญชี ไม่ใช่การกวาดหน้าเว็บ (scraping) ซึ่งผิดเงื่อนไขใช้งานและถูกบล็อกได้ทุกเมื่อ</p>
      <div class="ch-grid">{ch_html}</div>
    </div>''', 4)

    # ── 5. QR deep dive ──────────────────────────────────────────────────────
    demo_url = "https://oshinei-dashboard.fly.dev/s/xxxxxxxx"
    qr = qr_svg(demo_url, 30) or '<div style="width:30mm;height:30mm;background:#e8eef7"></div>'
    s5 = slide(f'''<div class="pad">
      <div class="kicker">ช่องทางของร้านเอง</div>
      <h2>แบบสอบถาม QR หน้าร้าน</h2>
      <p class="lede">รีวิวสาธารณะมาไม่กี่ครั้งต่อเดือน แต่ QR บนโต๊ะถามได้ทุกโต๊ะทุกวัน ใช้เวลาแขก 20 วินาที
      ไม่ต้องขอสิทธิ์จากแพลตฟอร์มไหนเลย เพราะเป็นช่องทางของร้านเอง</p>
      <div class="qr-wrap">
        <div class="qr-card">{qr}</div>
        <div class="qr-facts">
          <div class="fact"><div class="dot">1</div><p><b>ให้คะแนน 1–5</b> + หัวข้อที่พูดถึง + ข้อความสั้น + เบอร์/LINE
          ถ้าอยากให้ติดต่อกลับ — เก็บเบอร์ไว้เท่าที่จำเป็นแล้วลบทิ้งอัตโนมัติ</p></div>
          <div class="fact"><div class="dot">2</div><p><b>คำตอบลบพร้อมเบอร์ = ขึ้นกล่องงานทันที</b>
          ให้สาขาโทรกลับได้ก่อนที่แขกจะเดินออกจากร้าน</p></div>
          <div class="fact"><div class="dot">3</div><p><b>ให้ 4–5 ดาวจะเห็นปุ่มชวนไปรีวิว Google</b>
          ดันคนที่ประทับใจไปเขียนรีวิวสาธารณะ แทนที่จะปล่อยให้เงียบหาย</p></div>
          <div class="fact"><div class="dot">4</div><p><b>QR วาดจากเซิร์ฟเวอร์ ไม่ใช่จากอินเทอร์เน็ตร้าน</b>
          การ์ดที่พิมพ์แล้วใช้ได้เสมอ ต่อให้เน็ตร้านหลุดตอนพิมพ์ก็ยังสแกนติด</p></div>
        </div>
      </div>
    </div>''', 5)

    # ── 6. analysis engine ───────────────────────────────────────────────────
    themes = ["รสชาติ", "ความสด", "บริการ", "รอนาน", "ราคา/ความคุ้ม", "ความสะอาด", "ปริมาณ/ของหมด", "บรรยากาศ"]
    pills = "".join(f'<span class="pill">{esc(t)}</span>' for t in themes)
    s6 = slide(f'''<div class="pad">
      <div class="kicker">วิเคราะห์อัตโนมัติ</div>
      <h2>ทุกข้อความถูกอ่านให้ ไม่ใช่แค่เก็บไว้เฉยๆ</h2>
      <p class="lede">สองเอนจิน ผลลัพธ์รูปแบบเดียวกัน แต่ละแถวบันทึกว่าเอนจินไหนวิเคราะห์ เพื่อให้อ่านผลด้วยความมั่นใจที่เหมาะสม</p>
      <div class="split">
        <div class="eng"><h3>Claude (เมื่อตั้งค่า API key)</h3>
          <ul><li>อ่านบริบทได้ลึกกว่าคำต่อคำ เข้าใจประชด กันเอง แซว</li>
          <li>สรุปเรื่องราวรายสัปดาห์ + ตอบคำถามที่พิมพ์เป็นภาษาคน</li>
          <li>ร่างคำตอบรีวิวให้ ทีมแก้แล้วส่งได้เลย</li></ul></div>
        <div class="eng"><h3>เล็กซิคอนไทย (ใช้ได้เสมอ สำรอง)</h3>
          <ul><li>จับคำในกลุ่มหัวข้อ + หน้าต่างคำปฏิเสธ ("ไม่อร่อย" ไม่ใช่ "อร่อย")</li>
          <li>ดาวเป็นตัวช่วยตัดสินเมื่อข้อความกำกวม</li>
          <li>ทำงานได้ทันทีแม้ไม่มี API key — ไม่มีวันหยุดวิเคราะห์</li></ul></div>
      </div>
      <p style="margin-top:6mm;font:600 10.5pt 'KIRA UI';color:var(--ink3)">8 หัวข้อที่ระบบจับอัตโนมัติ + จับชื่อเมนูที่ถูกพูดถึงจากรายการขายจริง</p>
      <div class="pillbar">{pills}</div>
    </div>''', 6)

    # ── 7. business cross-read ───────────────────────────────────────────────
    s7 = slide(f'''<div class="pad">
      <div class="kicker">สิ่งที่เครื่องมือรีวิวทั่วไปทำไม่ได้</div>
      <h2>อ่านเสียงลูกค้าคู่กับข้อมูลการขายจริง</h2>
      <p class="lede">รีวิวเฉยๆ บอกได้แค่ว่าใครบ่นเรื่องอะไร แต่เทียบกับยอดขายจริงถึงจะรู้ว่าควรแก้อะไรก่อน</p>
      <div class="cross">
        <div class="card"><span class="e"></span><h3>ความบ่นคู่กับความยุ่ง</h3>
          <p>"รอนาน" ในวันที่สาขายุ่งกว่าปกติ 20% คือปัญหากำลังคน/เตรียมของช่วงพีค แต่ถ้าวันนั้นเงียบ คือปัญหามาตรฐานงาน</p></div>
        <div class="card"><span class="e"></span><h3>เมนูที่ถูกบ่นคู่กับยอดขาย/ต้นทุน</h3>
          <p>เมนูที่ถูกบ่นและติดขายดี 20% แรก คือเมนูที่ต้องแก้ก่อน เมนูที่ถูกชมและขายดี คือเมนูที่ต้องรักษาไว้</p></div>
        <div class="card"><span class="e">▲</span><h3>เสียงคู่กับรายได้ต่อสาขา</h3>
          <p>เทียบกับรายได้ ยอดลูกค้า สัดส่วนส่วนลด และค่าใช้จ่ายต่อหัว — คำร้อง "ราคา/ความคุ้ม" ต้องวัดกับตัวเลขจริง ไม่ใช่ความรู้สึก</p></div>
      </div>
    </div>''', 7)

    # ── 8. closing the loop ─────────────────────────────────────────────────
    s8 = slide(f'''<div class="pad">
      <div class="kicker">ปิดวงจร</div>
      <h2>จากเสียง → เป็นงาน → เป็นหลักฐานว่าดีขึ้นจริง</h2>
      <p class="lede">แท็บบอกว่าลูกค้าบ่นอะไรที่ไหน โมดูลนี้คือบันทึกว่าทำอะไรไปแล้วบ้าง</p>
      <div class="loop">
        <div class="node"><div class="circ"></div><h3>ตั้งงาน</h3><p>หัวข้อ + สาขา → งานพร้อมเจ้าของและวันครบกำหนด</p></div>
        <div class="conn">→</div>
        <div class="node"><div class="circ"></div><h3>วัดค่าฐาน</h3><p>นับเสียงลบต่อ 100 ก่อนเริ่มงาน (baseline)</p></div>
        <div class="conn">→</div>
        <div class="node"><div class="circ">✓</div><h3>ทำเสร็จ</h3><p>สถานะ เปิด → กำลังทำ → เสร็จ พร้อมบันทึกสิ่งที่ทำ</p></div>
        <div class="conn">→</div>
        <div class="node"><div class="circ"></div><h3>วัดซ้ำ</h3><p>นับเสียงลบหลังทำเสร็จ เทียบกับค่าฐาน</p></div>
        <div class="conn">→</div>
        <div class="node"><div class="circ"></div><h3>ยืนยันผล</h3><p>ดีขึ้น / เท่าเดิม / แย่ลง — ตัวเลขจากลูกค้าจริง ไม่ใช่ความเห็นทีม</p></div>
      </div>
      <div class="loop-note">สรุปรายสัปดาห์ส่งเข้า Telegram ทุกวันจันทร์ (และหัวข้อ §12 ของรายงานผู้บริหาร) — บอกทุกคนว่าเสียงลูกค้าขยับไปทางไหนแล้ว</div>
    </div>''', 8)

    # ── 9. competitors ────────────────────────────────────────────────────────
    s9a = f'''<h3>เทียบคู่แข่งรอบสาขา</h3>
      <p>ภาพรวมรายเดือนจาก Google Places: คะแนนและจำนวนรีวิวของสาขาเทียบกับร้านคู่แข่งรอบๆ ค่ากลางและอันดับดีที่สุดในย่าน
      อันดับของสาขา และการเปลี่ยนแปลงจากเดือนก่อน</p>
      <p class="stat">ทุกสาขา · ทุกเดือน</p>'''
    s9b = f'''<h3>AI เชิงลึก</h3>
      <p>เรื่องเล่ารายสัปดาห์ 3 ย่อหน้าสั้นๆ พร้อม 3 สิ่งที่ควรทำ และถามคำถามเป็นภาษาคนได้ ("สาขาไหนบ่นเรื่องรอนานมากสุดเดือนนี้")
      ทำงานได้แม้ไม่มี Claude — ใช้เอนจินกฎเกณฑ์แทน ทุกคำตอบบอกว่าเอนจินไหนตอบ</p>
      <p class="stat">ไม่มี API key ก็ใช้ได้</p>'''
    s9 = slide(f'''<div class="pad">
      <div class="kicker">มองไกลกว่ารีวิว</div>
      <h2>รู้จุดยืนเทียบคู่แข่ง และมีคนอ่านให้ทุกสัปดาห์</h2>
      <div class="two"><div>{s9a}</div><div>{s9b}</div></div>
    </div>''', 9)

    # ── 10. rollout ──────────────────────────────────────────────────────────
    gantt = build_gantt()
    s10 = slide(f'''<div class="pad">
      <div class="kicker">เริ่มใช้งาน</div>
      <h2>เปิดทีละช่องทาง ไม่ต้องรอให้ครบทุกอันก่อน</h2>
      <p class="lede">แผนติดตั้งแบบจับมือทำทีละคลิก อยู่ในแท็บ เช็คลิสต์ไปเป็นสถานะสด แชร์กันทั้งทีม พิมพ์เป็น PDF ได้ทุกเมื่อ</p>
      <div class="rollstat">
        <div class="rs"><div class="v">{stats['phases']}</div><div class="l">เฟส เรียงตามช่องทาง</div></div>
        <div class="rs"><div class="v">{stats['steps']}</div><div class="l">ขั้นตอนย่อย แต่ละขั้นมีวิธีทำ/เหตุผล/วิธีเช็ค/ถ้าติดขัด</div></div>
        <div class="rs"><div class="v">วันแรก</div><div class="l">Google Takeout ใช้งานได้ทันทีโดยไม่ต้องรอสิทธิ์ API</div></div>
        <div class="rs"><div class="v">~{stats['horizon']} วัน</div><div class="l">กรอบเวลาครบทุกช่องทาง (รวมช่วงรอ Google/TikTok อนุมัติ)</div></div>
      </div>
      <div class="gantt-wrap">{gantt}</div>
    </div>''', 10)

    # ── 11. what it takes from you (owner-facing) ────────────────────────────
    s11 = slide(f'''<div class="pad">
      <div class="kicker">ก่อนเริ่ม</div>
      <h2>สิ่งที่ต้องการจากเจ้าของ</h2>
      <p class="lede">แต่ละช่องทางต้องมีคนถือบัญชีของร้านยืนยันตัวตนครั้งเดียว หลังจากนั้นระบบดึงเองทุกคืน</p>
      <div class="cross">
        <div class="card"><span class="e"></span><h3>บัญชีของร้านเอง</h3>
          <p>Google Business Profile · Facebook Page · Instagram Business ที่ผูกกับเพจ · TikTok — ต้องเป็นบัญชีที่ร้านจัดการอยู่แล้ว</p></div>
        <div class="card"><span class="e">✓</span><h3>อนุมัติครั้งเดียวต่อช่องทาง</h3>
          <p>Google/TikTok ต้องรออนุมัติจากแพลตฟอร์ม 1–2 สัปดาห์ Facebook/Instagram อนุมัติทันที — ยื่นทุกช่องทางพร้อมกันได้เพื่อไม่ให้เสียเวลารอ</p></div>
        <div class="card"><span class="e"></span><h3>คนตอบรีวิวประจำวัน</h3>
          <p>ระบบร่างคำตอบให้ แต่ต้องมีคนอ่านผ่าน/แก้/กดส่งทุกเช้า และผู้จัดการเขตตามงานที่ตั้งไว้ทุกสัปดาห์</p></div>
      </div>
    </div>''', 11)

    # ── 12. CTA ──────────────────────────────────────────────────────────────
    s12 = slide(f'''<h2>เริ่มได้วันนี้ ไม่ต้องรอครบทุกช่องทาง</h2>
      <p class="lede">อัปโหลด Google Takeout ครั้งเดียว เห็นเสียงลูกค้าย้อนหลังทั้งหมดในแท็บได้ทันที
      ส่วนช่องทางอื่นเปิดเพิ่มทีละอันตามแผนในเช็คลิสต์</p>
      <div class="cta-grid">
        <div class="card"><div class="n">1</div><h3>อัปโหลด Google Takeout</h3><p>30 นาที ไม่ต้องรอสิทธิ์ API เห็นผลวันนี้</p></div>
        <div class="card"><div class="n">2</div><h3>เปิดแบบสอบถาม QR</h3><p>สร้างลิงก์สาขา พิมพ์การ์ด A5 ติดโต๊ะ — เก็บเสียงได้ทุกวันจากวันแรก</p></div>
        <div class="card"><div class="n">3</div><h3>เดินตามเช็คลิสต์</h3><p>ยื่นขอสิทธิ์ Google/Facebook/TikTok พร้อมกัน แล้วปล่อยให้รออนุมัติไปพร้อมงานอื่น</p></div>
      </div>''', 12, "cta")

    slides = "".join([s1, s2, s3, s4, s5, s6, s7, s8, s9, s10, s11, s12])
    return (f'<!doctype html><html lang="th"><head><meta charset="utf-8">'
            f'<title>เสียงลูกค้า — แนะนำโมดูล</title><style>{CSS}</style></head><body>{slides}</body></html>')


def build_gantt() -> str:
    """A compact horizontal bar per phase, days 0..stats['horizon']. Plain HTML/CSS rather than hand-
    rolled SVG: a bar is a track div with an absolutely-positioned fill sized and offset by percent,
    which behaves under Chrome's print-to-pdf the same way every other box on this page does — no
    viewBox-to-pixel scaling to get wrong."""
    d = json.load(io.open(PLAN_JSON, encoding="utf-8"))
    horizon = d["horizon_days"]
    phases = d["phases"]
    colors = ["#16294d", "#16294d", "#1f3a6b", "#1f3a6b", "#4a70b0"]
    rows = []
    for i, ph in enumerate(phases):
        d0, d1 = ph["days"]
        left_pct = 100 * d0 / horizon
        width_pct = max(1.6, 100 * (d1 - d0) / horizon)
        label = ph["name"].split("·")[-1].strip() if "·" in ph["name"] else ph["name"]
        label = label[:38] + ("…" if len(label) > 38 else "")
        rows.append(
            '<div class="grow">'
            f'<div class="glabel">{esc(label)}</div>'
            '<div class="gtrack">'
            f'<div class="gbar" style="left:{left_pct:.2f}%;width:{width_pct:.2f}%;background:{colors[i % len(colors)]}"></div>'
            '</div>'
            f'<div class="gdays">วัน {d0}–{d1}</div>'
            '</div>'
        )
    ticks = "".join(
        f'<div class="gtick" style="left:{100 * m / horizon:.2f}%">{m}</div>'
        for m in range(0, horizon + 1, 7))
    return ('<div class="gantt">' + "".join(rows)
            + f'<div class="gaxis"><div class="glabel"></div><div class="gtrack">{ticks}</div><div class="gdays"></div></div></div>')


def to_pdf(html_path: Path, pdf_path: Path) -> bool:
    chrome = CHROME if CHROME.exists() else None
    if not chrome:
        import shutil
        found = shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("msedge")
        chrome = Path(found) if found else None
    if not chrome:
        print("Chrome/Edge not found — HTML only")
        return False
    cmd = [str(chrome), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
           "--virtual-time-budget=8000", f"--print-to-pdf={pdf_path.resolve()}", html_path.resolve().as_uri()]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if not pdf_path.exists():
        print("PDF failed:", (r.stderr or "")[-400:])
        return False
    try:
        import pikepdf
        with pikepdf.open(pdf_path) as pdf:
            n = len(pdf.pages)
        print(f"pdf  -> {pdf_path.relative_to(ROOT)}  ({pdf_path.stat().st_size // 1024} KB, {n} page(s))"
              + ("" if n == 12 else "  ⚠ expected 12"))
    except Exception:
        print(f"pdf  -> {pdf_path.relative_to(ROOT)}  ({pdf_path.stat().st_size // 1024} KB)")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pdf", action="store_true")
    args = ap.parse_args()

    html = build_html()
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"html -> {OUT_HTML.relative_to(ROOT)}  ({len(html) // 1024} KB)")
    if not args.no_pdf:
        to_pdf(OUT_HTML, OUT_PDF)


if __name__ == "__main__":
    main()
