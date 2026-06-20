# -*- coding: utf-8 -*-
"""Generate the detailed Thai-language HTML cost-reduction report for Oshinei.
Buffet-100% focus. Minimal/formal design. Reads scripts/menu_analysis.json
(real DB figures, Jan-May 2026) and emits docs/COST_REDUCTION_REPORT.html.
Sections are exec'd from scripts/_sec_*.py (share globals)."""
import json, os, html

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = json.load(open(os.path.join(ROOT, "scripts", "menu_analysis.json"), encoding="utf-8"))
OUT = os.path.join(ROOT, "docs", "COST_REDUCTION_REPORT.html")

ANN = 2.4  # 5-month window -> annualized factor (12/5)

# ── Buffet-100% headline figures ─────────────────────────────────────────────
H = DATA["headline"]
CV = DATA["covers"]
REV5 = DATA["buffet_rev"]            # buffet package (tier) revenue, ex-VAT
COGS5 = DATA["buffet_cogs"]          # cost of food consumed during buffet
TIER5 = DATA["buffet_rev"]
FOODPCT = DATA["buffet_food_cost_pct"]      # buffet food cost % (66.3)
BLEND_FC = DATA["blended_food_cost_pct"]    # incl. drinks/service (57.4)
REV_Y = REV5 * ANN
COGS_Y = COGS5 * ANN
COVERS_Y = CV["covers"] * ANN
COST_PER_COVER = COGS5 / CV["covers"]
REV_PER_COVER = REV5 / CV["covers"]
FAM = DATA["families"]
SALMON_SHARE = FAM["salmon"]["share"]
SHRIMP_SHARE = FAM["shrimp"]["share"]
BR_SORTED = DATA["branches"]
BR_WORST = BR_SORTED[0]
BR_BEST = BR_SORTED[-1]
BR_SPREAD = round(BR_WORST["food_cost_pct"] - BR_BEST["food_cost_pct"])
SASHIMI_SHARE = DATA["categories"][0]["cost_share_pct"]
SALMON_SASHIMI_SHARE = DATA["top_cost_drivers"][0]["cost_share_pct"]
TARGET1 = 58   # near-term buffet food-cost target
TARGET2 = 55   # stretch


def b(n): return f"{round(n):,}"
def m(n): return f"฿{n/1e6:,.1f}M"
def esc(s): return html.escape(str(s))
def clean(desc): return esc(desc.replace("(Buffet)", "").replace("(B)", "").strip())

CHUNKS = []
def w(s): CHUNKS.append(s)


# ════════════════════════════════════════════════════════════════════════
#  CSS — minimal / formal (white, restrained colour, emphasis only)
# ════════════════════════════════════════════════════════════════════════
CSS = r"""
:root{
  --ink:#1B2A30; --ink2:#465056; --muted:#8B979D; --faint:#B7C0C4;
  --teal:#13595A; --teal-d:#0E4445; --accent:#B0472D; --accent-d:#933A23;
  --green:#1F7A4D; --red:#B0472D; --gold:#8A6D1F;
  --line:#E3E6E7; --line2:#EEF0F1; --soft:#F5F7F7; --soft2:#FAFBFB;
  --bg:#FFFFFF;
}
*{box-sizing:border-box}
html{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{
  font-family:'IBM Plex Sans Thai','Sarabun',-apple-system,'Segoe UI',Tahoma,sans-serif;
  color:var(--ink); background:#EDEFF0; margin:0; line-height:1.66; font-size:14.5px;
}
.page{
  width:210mm; min-height:296mm; margin:0 auto 9mm; padding:21mm 20mm 17mm;
  background:var(--bg); position:relative; box-shadow:0 1px 8px rgba(0,0,0,.07);
}
@page{size:A4;margin:0}
@media print{
  *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
  body{background:#fff;font-size:11px}
  .page{box-shadow:none;margin:0;width:auto;min-height:auto;padding:14mm 15mm;page-break-after:always;overflow:hidden}
  .page:last-child{page-break-after:auto}
  .noprint{display:none!important}
  h2,h3,h4{page-break-after:avoid}
  table,figure,.callout,.tcard,.quad,.figrow,.bar-wrap{page-break-inside:avoid}
}
h1,h2,h3,h4{line-height:1.3;margin:0 0 .5em;font-weight:700;color:var(--ink)}
h2{font-size:22px;letter-spacing:-.2px;padding-bottom:.32em;border-bottom:1px solid var(--line);margin-bottom:.7em}
h3{font-size:17px;color:var(--teal-d);margin-top:1.5em}
h4{font-size:14.5px;color:var(--ink);margin-top:1.25em;font-weight:700}
p{margin:0 0 .8em} a{color:var(--teal-d)}
.lead{font-size:15.5px;color:var(--ink2);line-height:1.7}
.small{font-size:11.8px;color:var(--muted)}
.kicker{font-size:11px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;color:var(--accent);margin-bottom:.5em}
.sec-no{font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-bottom:.3em}
ul,ol{margin:.2em 0 1em;padding-left:1.25em} li{margin:.3em 0}
strong,b{color:var(--ink);font-weight:700}
em{color:var(--ink2)}
hr{border:none;border-top:1px solid var(--line);margin:1.5em 0}
.accent{color:var(--accent)} .tealc{color:var(--teal-d)} .greenc{color:var(--green)} .redc{color:var(--red)}

/* running footer */
.pfoot{position:absolute;left:20mm;right:20mm;bottom:9mm;display:flex;justify-content:space-between;
       font-size:9.5px;color:var(--muted);border-top:1px solid var(--line);padding-top:6px;letter-spacing:.3px}
@media print{.pfoot{left:16mm;right:16mm;bottom:8mm}}

/* tags / chips */
.tag{display:inline-block;font-size:10px;font-weight:700;padding:1px 8px;border-radius:3px;
     color:var(--teal-d);border:1px solid var(--line);letter-spacing:.4px;vertical-align:middle}
.tag.s{color:var(--accent)} .tag.green{color:var(--green)} .tag.red{color:var(--red)} .tag.g{color:var(--gold)}
.pill-list{display:flex;flex-wrap:wrap;gap:6px;margin:.5em 0}
.pill-list .p{border:1px solid var(--line);color:var(--ink2);border-radius:3px;padding:3px 9px;font-size:11.5px}

/* cover */
.cover{padding:0}
.cover .bar{display:none}
.cover .cbody{padding:34mm 22mm 16mm;border-top:3px solid var(--ink)}
.cover .wm{font-size:12px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:var(--ink)}
.cover .kicker{color:var(--muted)}
.pdfbtn{position:fixed;top:14px;right:14px;z-index:9999;background:var(--ink);color:#fff;border:none;
  padding:9px 16px;border-radius:5px;font-size:12.5px;font-weight:700;cursor:pointer;font-family:inherit;
  box-shadow:0 2px 10px rgba(0,0,0,.22);letter-spacing:.3px}
.pdfbtn:hover{background:#000}
@media print{.pdfbtn{display:none!important}}
.cover h1{font-size:40px;line-height:1.14;letter-spacing:-.6px;margin:.45em 0 .25em;color:var(--ink)}
.cover .sub{font-size:18px;color:var(--ink2);font-weight:500;line-height:1.5;max-width:88%}
.cover .rule{height:1px;background:var(--line);margin:26px 0}
.cover .prep{display:flex;gap:36px;flex-wrap:wrap;margin-bottom:8px}
.cover .prep .pp .h{font-size:10.5px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:var(--muted);margin-bottom:3px}
.cover .prep .pp .v{font-size:14.5px;color:var(--ink);font-weight:600;line-height:1.5}
.cover .meta{font-size:12.5px;color:var(--ink2);line-height:1.95;margin-top:14px}
.cover .kpis{display:flex;margin-top:30px;border-top:2px solid var(--ink)}
.cover .kpis .k{flex:1;padding:14px 16px 0 0}
.cover .kpis .k+ .k{border-left:1px solid var(--line);padding-left:18px}
.cover .kpis .k .n{font-size:27px;font-weight:800;color:var(--ink);line-height:1.05;letter-spacing:-.5px}
.cover .kpis .k .n.a{color:var(--accent)}
.cover .kpis .k .l{font-size:11px;color:var(--ink2);margin-top:5px;line-height:1.45}

/* figure blocks (replace cards) */
.figrow{display:flex;gap:0;margin:1.2em 0;border-top:2px solid var(--ink);border-bottom:1px solid var(--line)}
.figrow .fig{flex:1;padding:13px 16px 13px 0}
.figrow .fig+ .fig{border-left:1px solid var(--line);padding-left:18px}
.fig .n{font-size:25px;font-weight:800;color:var(--ink);line-height:1.05;letter-spacing:-.5px}
.fig .n.a{color:var(--accent)} .fig .n.green{color:var(--green)} .fig .n.teal{color:var(--teal-d)}
.fig .l{font-size:11.5px;color:var(--ink2);margin-top:5px;font-weight:600;line-height:1.4}
.fig .h{font-size:10.5px;color:var(--muted);margin-top:3px;line-height:1.4}

/* callouts: white, left rule, coloured title */
.callout{border-left:3px solid var(--teal);padding:10px 14px;margin:1.05em 0}
.callout.s{border-left-color:var(--accent)} .callout.green{border-left-color:var(--green)}
.callout.red{border-left-color:var(--red)} .callout.g{border-left-color:var(--gold)}
.callout .ct{font-weight:700;font-size:13.5px;margin-bottom:.25em;color:var(--ink)}
.callout.s .ct{color:var(--accent-d)} .callout.red .ct{color:var(--red)} .callout.green .ct{color:var(--green)}
.callout p:last-child{margin-bottom:0}

/* tables — minimal */
table{width:100%;border-collapse:collapse;margin:1em 0;font-size:12.5px}
th,td{padding:7px 9px;text-align:left;border-bottom:1px solid var(--line2);vertical-align:top}
th{color:var(--ink);font-weight:700;font-size:11.5px;letter-spacing:.2px;
   border-bottom:2px solid var(--ink)}
th.r,td.r{text-align:right;font-variant-numeric:tabular-nums} th.c,td.c{text-align:center}
tbody tr:last-child td{border-bottom:1px solid var(--line)}
tr.hl td{font-weight:700}
tr.hl td:first-child{border-left:2px solid var(--accent);padding-left:7px}
tr.tot td{border-top:2px solid var(--ink);font-weight:700}
td.pos{color:var(--green);font-weight:700} td.neg{color:var(--red);font-weight:700}
.tbl-cap{font-size:11px;color:var(--muted);margin:-.4em 0 1.3em}

/* bar chart — single colour */
.bar-wrap{margin:1em 0}
.bar-row{display:grid;grid-template-columns:178px 1fr 80px;align-items:center;gap:10px;margin:5px 0;font-size:12px}
.bar-row .lab{text-align:right;color:var(--ink2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar-track{background:var(--line2);height:15px;border-radius:2px;overflow:hidden}
.bar-fill{height:100%;background:var(--teal)}
.bar-row.acc .bar-fill{background:var(--accent)}
.bar-val{font-weight:700;color:var(--ink);font-variant-numeric:tabular-nums}

/* quadrant — white cells, thin border, corner label */
.quad{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:1.1em 0}
.quad .q{border:1px solid var(--line);padding:13px 15px;background:#fff}
.quad .q .qh{font-weight:700;font-size:14px;margin-bottom:.1em}
.quad .q.star .qh{color:var(--green)} .quad .q.plow .qh{color:var(--accent)}
.quad .q.puzzle .qh{color:var(--gold)} .quad .q.dog .qh{color:var(--ink2)}
.quad .q .qs{font-size:11px;color:var(--muted);margin-bottom:.5em;letter-spacing:.3px}

/* transform card — formal */
.tcard{border:1px solid var(--line);margin:1.25em 0;background:#fff}
.tcard .th{border-bottom:2px solid var(--ink);padding:11px 16px}
.tcard .th .tn{font-size:16px;font-weight:700;margin:0;color:var(--ink)}
.tcard .th .ts{font-size:11.5px;color:var(--ink2);margin-top:2px}
.tcard .tb{padding:14px 16px}
.flow{display:flex;align-items:stretch;gap:9px;margin:.5em 0;flex-wrap:wrap}
.flow .box{flex:1;min-width:128px;border:1px solid var(--line);padding:10px 12px;font-size:12px;background:#fff}
.flow .box .bx-h{font-weight:700;font-size:10px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:5px}
.flow .box.from .bx-h{color:var(--red)} .flow .box.add .bx-h{color:var(--green)} .flow .box.to .bx-h{color:var(--teal-d)}
.flow .arrow{display:flex;align-items:center;font-size:20px;color:var(--faint);font-weight:700}
.tmetrics{display:flex;gap:0;margin-top:.7em;border-top:1px solid var(--line)}
.tmetrics .tm{flex:1;text-align:center;padding:9px 4px}
.tmetrics .tm+ .tm{border-left:1px solid var(--line)}
.tmetrics .tm .v{font-size:17px;font-weight:800;color:var(--accent)} .tmetrics .tm .v.green{color:var(--green)}
.tmetrics .tm .k{font-size:10px;color:var(--muted);margin-top:2px}

/* TOC */
.toc .tline{display:flex;justify-content:space-between;border-bottom:1px solid var(--line2);padding:6px 0;font-size:13px}
.toc .tline .t{color:var(--ink)} .toc .tline .pg{color:var(--muted);font-weight:700}
.toc .tsec{font-weight:700;color:var(--teal-d);margin-top:13px;font-size:13.5px;letter-spacing:.2px}

/* waterfall */
.wf{margin:1.1em 0}
.wf-row{display:grid;grid-template-columns:248px 1fr 112px;gap:10px;align-items:center;margin:6px 0;font-size:12.5px}
.wf-row .wl{color:var(--ink2)}
.wf-track{background:var(--line2);height:20px;border-radius:2px}
.wf-fill{height:100%;background:var(--teal);border-radius:2px}
.wf-row .wv{text-align:right;font-weight:700;color:var(--teal-d);font-variant-numeric:tabular-nums}
.wf-row.tot .wf-fill{background:var(--accent)} .wf-row.tot .wv{color:var(--accent)} .wf-row.tot .wl{font-weight:700;color:var(--ink)}

/* misc */
.two{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.note{font-size:11.5px;color:var(--muted);font-style:italic}
.donut-wrap{display:flex;gap:22px;align-items:center;flex-wrap:wrap}
.legend{font-size:12px}
.legend .li{display:flex;align-items:center;gap:8px;margin:4px 0}
.legend .sw{width:11px;height:11px;border-radius:2px;flex:none}
.q-quote{border-left:3px solid var(--faint);padding:6px 14px;margin:.6em 0;font-size:12.5px;color:var(--ink2);font-style:italic}
.q-quote .who{display:block;font-style:normal;font-size:10.5px;color:var(--muted);margin-top:3px}
.checkbox,.warn-list{list-style:none;padding-left:0}
.checkbox li,.warn-list li{padding-left:22px;position:relative;margin:.35em 0}
.checkbox li:before{content:"\2713";position:absolute;left:0;color:var(--green);font-weight:800}
.warn-list li:before{content:"\00D7";position:absolute;left:1px;color:var(--red);font-weight:800;font-size:15px;line-height:1.1}
.teams{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:1.1em 0}
.teams .tm2{border:1px solid var(--line);padding:15px 17px}
.teams .tm2 .h{font-weight:700;font-size:14.5px;color:var(--teal-d);margin-bottom:.3em}
.teams .tm2 .r{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:.6em}
"""


# ════════════════════════════════════════════════════════════════════════
#  Helper builders (minimal/formal)
# ════════════════════════════════════════════════════════════════════════
PAGE = [0]
def page(cls=""):
    PAGE[0] += 1
    w(f'<section class="page {cls}">')
    return PAGE[0]

def endpage(foot_l="Oshinei Enterprise · แผนกลยุทธ์ลดต้นทุนบุฟเฟ่ต์ 2026", foot_r=None):
    rr = foot_r if foot_r is not None else f"{PAGE[0]}"
    w(f'<div class="pfoot"><span>{foot_l}</span><span>Invisible Consulting · เอกสารลับ</span><span>{rr}</span></div>')
    w('</section>')

DON_PAL = ["#13595A","#3E8385","#B0472D","#8A6D1F","#6E8A8B","#A9B7B7","#4A6A6B","#D7C9A0"]

def barchart(rows, fmt=None, accent_idx=None):
    vals = [r[1] for r in rows]; mx = max(vals) if vals else 1
    w('<div class="bar-wrap">')
    for i,(lab,val) in enumerate(rows):
        pct = max(2, 100*val/mx) if mx else 0
        disp = fmt(val) if fmt else m(val)
        acc = ' acc' if (accent_idx is not None and i in (accent_idx if isinstance(accent_idx,(list,tuple,set)) else {accent_idx})) else ''
        w(f'<div class="bar-row{acc}"><div class="lab">{lab}</div>'
          f'<div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%"></div></div>'
          f'<div class="bar-val">{disp}</div></div>')
    w('</div>')

def donut(segments, size=160):
    tot = sum(s[1] for s in segments) or 1
    r=70; cx=cy=90; circ=2*3.14159265*r; off=0; arcs=[]
    for lab,val,col in segments:
        dash=val/tot*circ
        arcs.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{col}" stroke-width="30" '
                    f'stroke-dasharray="{dash:.2f} {circ-dash:.2f}" stroke-dashoffset="{-off:.2f}" '
                    f'transform="rotate(-90 {cx} {cy})"/>')
        off+=dash
    svg=(f'<svg viewBox="0 0 180 180" width="{size}" height="{size}">'+''.join(arcs)+
         f'<circle cx="{cx}" cy="{cy}" r="52" fill="#fff"/>'
         f'<text x="{cx}" y="{cy-3}" text-anchor="middle" font-size="13" font-weight="700" fill="#1B2A30">ต้นทุน</text>'
         f'<text x="{cx}" y="{cy+14}" text-anchor="middle" font-size="11.5" fill="#8B979D">บุฟเฟ่ต์</text></svg>')
    leg='<div class="legend">'+''.join(
        f'<div class="li"><span class="sw" style="background:{col}"></span>{lab} — <b>{100*val/tot:.1f}%</b></div>'
        for lab,val,col in segments)+'</div>'
    w(f'<div class="donut-wrap">{svg}{leg}</div>')

def stat_cards(cards):
    """Render as a minimal figure row (keeps old call sites working)."""
    nmap = {"r":"a","s":"a","t":"teal","green":"green","":""}
    w('<div class="figrow">')
    for c in cards:
        nc = nmap.get(c.get("ncls",""), "")
        h = f'<div class="h">{c["h"]}</div>' if c.get("h") else ''
        w(f'<div class="fig"><div class="n {nc}">{c["n"]}</div><div class="l">{c["l"]}</div>{h}</div>')
    w('</div>')

# TOC tracking
toc_entries = []
def toc_sec(label): toc_entries.append(("sec", label, PAGE[0]))
def toc_sub(label): toc_entries.append(("sub", label, PAGE[0]))

# ════════════════════════════════════════════════════════════════════════
#  Savings model (annualized THB, ranges)
# ════════════════════════════════════════════════════════════════════════
SAVE = [
    ("วัตถุดิบแซลมอน: เปลี่ยนวิธีตัด (saku/แช่แข็งทะเล) + ครัวกลาง + ล็อกสัญญา", 28, 45),
    ("ดัดแปลงเมนู (Transform): ยำ / ชุดปลาดิบ / แซลมอนดอง / ของหวาน", 15, 25),
    ("คุมพอร์ชัน + จำกัดเมนูพรีเมียม (กุ้ง/ทุเรียน/อูนิ/ปลาไหล)", 12, 20),
    ("ลดของเสีย (ติดตามต้นทุน/วัน + เป้า <180 ก./หัว + audit การตัด)", 12, 22),
    ("โค้ชสาขาต้นทุนสูงผิดปกติ ให้เข้าใกล้ค่ากลางเครือ", 7, 12),
]
SAVE_LO = sum(s[1] for s in SAVE); SAVE_HI = sum(s[2] for s in SAVE)
SAVE_MID = (SAVE_LO + SAVE_HI) / 2
PTS_MID = SAVE_MID*1e6 / REV_Y * 100


# ════════════════════════════════════════════════════════════════════════
#  DOCUMENT HEAD + COVER
# ════════════════════════════════════════════════════════════════════════
w('<!DOCTYPE html><html lang="th"><head><meta charset="utf-8">')
w('<meta name="viewport" content="width=device-width,initial-scale=1">')
w('<title>แผนกลยุทธ์ลดต้นทุนบุฟเฟ่ต์ — Oshinei Enterprise (2026)</title>')
w('<link rel="preconnect" href="https://fonts.googleapis.com">')
w('<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Thai:wght@400;500;600;700&family=Sarabun:wght@400;500;600;700;800&display=swap" rel="stylesheet">')
w(f'<style>{CSS}</style></head><body>')
w('<button class="pdfbtn noprint" onclick="window.print()">บันทึกเป็น PDF (Save as PDF)</button>')

# ---- COVER ----
page("cover")
w('<div class="cbody">')
w('<div class="wm">Invisible Consulting</div>')
w('<div class="kicker" style="margin-top:24px">เอกสารลับ · รายงานเชิงกลยุทธ์</div>')
w('<h1>แผนกลยุทธ์ลดต้นทุน<br>บุฟเฟ่ต์</h1>')
w('<div class="sub">วิศวกรรมเมนู (Menu Engineering) เพื่อ “คงเมนูยอดนิยม–ลดต้นทุน” '
  'พร้อมแนวทางบูรณาการด้านอาหาร เมนูสินค้า การตลาด และการเสิร์ฟ</div>')
w('<div class="rule"></div>')
w('<div class="prep">')
w('<div class="pp"><div class="h">จัดทำโดย</div><div class="v">ทีมนวัตกรรมเมนูและพัฒนาอาหาร<br>'
  '<span style="color:var(--ink2);font-weight:500">Culinary Innovation &amp; Menu Development</span></div></div>')
w('<div class="pp"><div class="h">ร่วมกับ</div><div class="v">ทีมกลยุทธ์การตลาดและแบรนด์<br>'
  '<span style="color:var(--ink2);font-weight:500">Marketing &amp; Brand Strategy</span></div></div>')
w('</div>')
w('<div class="meta">'
  '<b>จัดทำเพื่อ:</b> คณะผู้บริหาร Oshinei Enterprise — เครือบุฟเฟ่ต์ปลาดิบ/แซลมอนญี่ปุ่น 26 สาขาทั่วประเทศ<br>'
  '<b>ขอบเขต:</b> วิเคราะห์เฉพาะธุรกิจบุฟเฟ่ต์ (Buffet 100%) — ไม่รวมเมนู à‑la‑carte/เครื่องดื่ม/ค่าบริการ<br>'
  '<b>ฐานข้อมูล:</b> ยอดขายจริงรายเมนู ม.ค.–พ.ค. 2569 (ระบบ ERP, 26 สาขา, 295,404 บิล, 806,972 ลูกค้า)<br>'
  '<b>ข้อมูลตลาด:</b> งานวิจัย/สถิติสาธารณะ (กสิกรฯ, SCB EIC, JETRO, LINE MAN Wongnai, Norwegian Seafood Council ฯลฯ)</div>')
w('<div class="kpis">')
w(f'<div class="k"><div class="n">{FOODPCT:.1f}%</div><div class="l">ต้นทุนอาหารบุฟเฟ่ต์<br>(เป้าหมายดี 30–45%)</div></div>')
w(f'<div class="k"><div class="n">{SALMON_SHARE:.0f}%</div><div class="l">ต้นทุนมาจากแซลมอน<br>วัตถุดิบเดียว</div></div>')
w(f'<div class="k"><div class="n">฿{SAVE_LO}–{SAVE_HI}M</div><div class="l">โอกาสประหยัด/ปี<br>โดยไม่ลดคุณค่าที่ลูกค้าเห็น</div></div>')
w('</div>')
w('<div class="meta" style="margin-top:26px;color:var(--muted);font-size:11px">20 มิถุนายน 2569 · ฉบับที่ 2 (ปรับเป็น Buffet 100% + เพิ่มงานวิจัยตลาด)</div>')
w('</div>')
endpage(foot_r="")

# ---- TEAMS / METHODOLOGY ----
page()
w('<div class="kicker">เกี่ยวกับรายงานฉบับนี้</div>')
w('<h2>คณะผู้จัดทำ ระเบียบวิธี และความน่าเชื่อถือ</h2>')
w('<p class="lead">รายงานนี้จัดทำโดยทีมที่ปรึกษาเฉพาะทางสองทีมที่ได้รับการว่าจ้างจาก Oshinei '
  'เพื่อยกระดับความสามารถในการทำกำไรของธุรกิจบุฟเฟ่ต์ ผ่านการพัฒนาเมนูและการตลาดที่อิงข้อมูลจริง.</p>')
w('<div class="teams">')
w('<div class="tm2"><div class="r">ทีมที่ 1</div><div class="h">ทีมนวัตกรรมเมนูและพัฒนาอาหาร</div>'
  '<p style="margin-bottom:0;font-size:12.5px">รับผิดชอบการวิเคราะห์ต้นทุนระดับเมนู วิศวกรรมเมนู (Menu Engineering) '
  'การออกแบบเมนูดัดแปลง (Transform) กลยุทธ์วัตถุดิบ/ครัวกลาง การคุมพอร์ชันและของเสีย '
  'และแนวทางการเสิร์ฟ — โดยยึดหลัก “ลดต้นทุนโดยรักษาคุณภาพและความพึงพอใจของลูกค้า”.</p></div>')
w('<div class="tm2"><div class="r">ทีมที่ 2</div><div class="h">ทีมกลยุทธ์การตลาดและแบรนด์</div>'
  '<p style="margin-bottom:0;font-size:12.5px">รับผิดชอบงานวิจัยตลาด (Market Research) การวิเคราะห์คู่แข่ง '
  'พฤติกรรมและเสียงผู้บริโภค กลยุทธ์ราคา/tier รายภาค การตลาดดิจิทัล '
  'และการปกป้อง “คุณค่าที่ลูกค้ารับรู้” ระหว่างการลดต้นทุน.</p></div>')
w('</div>')
w('<h3>ระเบียบวิธี (Methodology)</h3>')
w('<ul>'
  '<li><b>ข้อมูลต้นทุนจริง:</b> ดึงยอดขายรายเมนู/สาขา/เดือน จากระบบ ERP ของบริษัทโดยตรง '
  '(ม.ค.–พ.ค. 2569 — 5 เดือนเต็มที่ข้อมูลครบ 26 สาขา) คำนวณต้นทุนด้วยสูตรเดียวกับระบบบัญชี.</li>'
  '<li><b>โฟกัสบุฟเฟ่ต์ 100%:</b> วิเคราะห์เฉพาะอาหารที่ลูกค้าบริโภคในบุฟเฟ่ต์ '
  'เทียบกับรายได้แพ็กเกจบุฟเฟ่ต์ — ตัดเครื่องดื่ม/ค่าบริการ/à‑la‑carte ออก เพื่อให้เห็นต้นทุนบุฟเฟ่ต์ที่แท้จริง.</li>'
  '<li><b>วิจัยตลาดอิงข้อมูลสาธารณะ:</b> ตัวเลขตลาด/คู่แข่ง/ผู้บริโภค/ราคาแซลมอน อ้างอิงงานวิจัยและสถิติที่ตรวจสอบได้ '
  '(ระบุแหล่งทุกตัวเลข; ตัวเลขที่มาจากแหล่งเดียว/ประเมิน ได้กำกับไว้).</li>'
  '<li><b>เสียงลูกค้า (Voice of Customer):</b> สังเคราะห์รีวิวจริงจาก Google, Wongnai, Pantip และโซเชียล '
  'รวมรีวิวสาขา Oshinei เอง — เพื่อระบุว่าการลดต้นทุน “ตรงไหนปลอดภัย ตรงไหนลูกค้าจะลงโทษ”.</li>'
  '</ul>')
w('<div class="callout"><div class="ct">หลักความน่าเชื่อถือ</div>'
  '<p>ทุกตัวเลขต้นทุนในรายงานมาจากระบบจริงและสามารถตรวจสอบย้อนกลับได้; '
  'ทุกตัวเลขตลาดมีแหล่งอ้างอิงกำกับ (ภาคผนวก จ.). ข้อเสนอประหยัดเป็น “ช่วงประเมิน” '
  'พร้อมแนวทางนำร่อง–วัดผลก่อนขยาย ไม่ใช่การรับประกันผล.</p></div>')
w('<p class="small">หมายเหตุด้านขอบเขต: ตัวเลข “ต่อปี” ประเมินจากฐาน 5 เดือน ×2.4. '
  '“ต้นทุนอาหารบุฟเฟ่ต์ 66.3%” = ต้นทุนอาหารที่ลูกค้าบริโภค ÷ รายได้แพ็กเกจบุฟเฟ่ต์ (ไม่รวม VAT); '
  'หากรวมเครื่องดื่ม/ค่าบริการเป็นรายได้ด้วย สัดส่วนต้นทุนต่อรายได้รวมจะเท่ากับ 57.4%.</p>')
endpage()

# ---- TOC ----
page()
w('<div class="kicker">สารบัญ</div><h2>เนื้อหาในรายงาน</h2>')
w('<p class="lead">บทสรุปผู้บริหาร 5 หน้า ตามด้วยเนื้อหาเชิงลึก 9 ส่วน และภาคผนวกข้อมูล '
  'โดยมี <b>ส่วนที่ 3 (วิศวกรรมเมนู)</b> เป็นหัวใจหลัก และ <b>ส่วนที่ 7 (วิจัยตลาดและการตลาด)</b> '
  'เป็นงานวิจัยตลาดอิงข้อมูลจริง.</p>')
w('@@TOC@@')
endpage()

# ── Load section bodies (exec summary first, then parts, then appendix) ──
_SECTION_FILES = [
    "_sec_exec.py",
    "_sec_part1_cost.py",
    "_sec_part2_market.py",
    "_sec_part3_menu.py",
    "_sec_part4_supply.py",
    "_sec_part5_waste.py",
    "_sec_part6_serve.py",
    "_sec_part_mktresearch.py",
    "_sec_part7_marketing.py",
    "_sec_part8_roadmap.py",
    "_sec_appendix.py",
]
for _sf in _SECTION_FILES:
    _p = os.path.join(ROOT, "scripts", _sf)
    if os.path.exists(_p):
        exec(compile(open(_p, encoding="utf-8").read(), _p, "exec"), globals())

def render():
    html_str = "".join(CHUNKS)
    toc = []
    for kind, label, pg in toc_entries:
        if kind == "sec":
            toc.append(f'<div class="tline"><span class="tsec">{label}</span><span class="pg">{pg}</span></div>')
        else:
            toc.append(f'<div class="tline"><span class="t">&nbsp;&nbsp;&nbsp;{label}</span><span class="pg">{pg}</span></div>')
    html_str = html_str.replace("@@TOC@@", '<div class="toc">' + "".join(toc) + "</div>")
    html_str += "</body></html>"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8").write(html_str)
    print("WROTE", OUT, "| pages:", PAGE[0], "| bytes:", len(html_str), "| toc:", len(toc_entries))

if __name__ == "__main__":
    render()
