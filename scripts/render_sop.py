# -*- coding: utf-8 -*-
"""Render docs/sop/*.html to A4 PDF with page numbers (Chromium via Playwright, py -3.14).
Run from the repo root:  py -3.14 scripts\render_sop.py [docs/sop/WP-SCM-001_ingredient_order_procedure.html]
The HTML carries its own @page rules (portrait default, a named landscape page for the swim-lane chart)."""
import io, os, re, sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "sop", "WP-SCM-001_ingredient_order_procedure.html"))
PDF = SRC[:-5] + ".pdf"
# the footer's ฉบับที่ comes from the cover table ("ฉบับที่ / วันที่มีผล"), so the two can never disagree
_m = re.search(r"ฉบับที่ / วันที่มีผล</th>\s*<td>\s*([0-9.]+)", io.open(SRC, encoding="utf-8").read())
VER = _m.group(1) if _m else "?"
FOOT = ('<div style="width:100%;font-family:\'Leelawadee UI\',Tahoma;font-size:8pt;color:#5b6b82;padding:0 16mm;display:flex;justify-content:space-between">'
        f'<span>WP-SCM-001 ฉบับที่ {VER} · คู่มือการทำงาน กระบวนการบริหารวัตถุดิบและใบสั่งซื้อสาขา · ฉบับร่าง รอการอนุมัติ</span>'
        '<span>หน้า <span class="pageNumber"></span> / <span class="totalPages"></span></span></div>')
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_page()
    pg.goto("file:///" + SRC.replace("\\", "/"), wait_until="networkidle")
    wide = pg.evaluate("[...document.querySelectorAll('table')].filter(t => t.scrollWidth > t.clientWidth + 2).length")
    pg.pdf(path=PDF, format="A4", print_background=True, display_header_footer=True, header_template="<div></div>", footer_template=FOOT,
           margin={"top": "16mm", "bottom": "18mm", "left": "14mm", "right": "14mm"}, prefer_css_page_size=True)
    b.close()
print("written", PDF, "| version", VER, "| tables wider than their box:", wide)
