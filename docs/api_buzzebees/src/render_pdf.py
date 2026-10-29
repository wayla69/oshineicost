# -*- coding: utf-8 -*-
"""Render docs/api_buzzebees/Easy_POS_Buzzebees_API_v1.html -> PDF (Playwright, Chrome).
Usage: py -3.14 src/render_pdf.py <html> <pdf> [footer label]"""
import os, sys
from playwright.sync_api import sync_playwright

HTML = os.path.abspath(sys.argv[1]); PDF = os.path.abspath(sys.argv[2])
url = "file:///" + HTML.replace("\\", "/")
FOOT = ('<div style="font-size:7.5px;width:100%%;text-align:center;color:#777;'
        'font-family:Arial,sans-serif;">%s &mdash; '
        'page <span class="pageNumber"></span> / <span class="totalPages"></span></div>') % (sys.argv[3] if len(sys.argv) > 3 else "Easy POS &times; Buzzebees API")
with sync_playwright() as p:
    try:
        b = p.chromium.launch(channel="chrome", headless=True)
    except Exception as e:
        print("chrome channel failed:", e); b = p.chromium.launch(headless=True)
    pg = b.new_page()
    pg.goto(url, wait_until="networkidle", timeout=60000)
    pg.evaluate("() => document.fonts.ready")
    pg.wait_for_timeout(500)
    fam = pg.evaluate("() => document.fonts.check(\"12px 'KIRA UI'\")")
    print("KIRA loaded:", fam)
    pg.emulate_media(media="print")
    pg.pdf(path=PDF, format="A4", print_background=True, prefer_css_page_size=True,
           display_header_footer=True, header_template="<div></div>", footer_template=FOOT,
           margin={"top": "15mm", "bottom": "17mm", "left": "16mm", "right": "16mm"})
    b.close()
print("WROTE", PDF, os.path.getsize(PDF))
