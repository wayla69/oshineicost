# -*- coding: utf-8 -*-
"""💬 เสียงลูกค้า acceptance: open the tab, paste two reviews, upload, and see
the KPIs, themes and menu mentions render (Playwright, dev server)."""
import os, sys
from playwright.sync_api import sync_playwright

BASE = os.environ.get('MGT_BASE', 'http://localhost:8021/')
errors = []
with sync_playwright() as pw:
    b = pw.chromium.launch(channel='chrome', headless=True)
    pg = b.new_page(viewport={'width': 1400, 'height': 900})
    pg.on('pageerror', lambda e: errors.append(str(e)))
    pg.goto(BASE, wait_until='networkidle', timeout=90000)
    pg.wait_for_function("typeof switchTab === 'function' && typeof TABS === 'object' && TABS.feedback", timeout=30000)
    pg.wait_for_function("document.querySelectorAll('#tabsRoot > div[id^=\"tab-\"]').length >= 49", timeout=30000)
    pg.evaluate("() => { document.body.classList.add('force-desktop'); switchTab('feedback'); }")
    pg.wait_for_timeout(1500)
    shown = pg.evaluate("() => getComputedStyle(document.getElementById('tab-feedback')).display !== 'none'")
    pg.fill('#fbText', 'แซลมอนสดมาก บริการดี จะกลับมาอีก\nรอนานมาก พนักงานไม่สนใจ ทุเรียนหมด')
    pg.select_option('#fbSource', 'Google')
    pg.evaluate("() => uploadFeedback()")
    pg.wait_for_function("document.getElementById('fbUploadStatus').textContent.includes('นำเข้า')", timeout=30000)
    status = pg.evaluate("() => document.getElementById('fbUploadStatus').textContent")
    pg.wait_for_timeout(1500)
    kpis = pg.evaluate("() => document.getElementById('fbKpis').innerText")
    themes = pg.evaluate("() => document.getElementById('fbThemes').innerText")
    neg = pg.evaluate("() => document.getElementById('fbNeg').innerText")
    b.close()
print('tab shown:', shown)
print('upload status:', status)
print('kpis:', kpis.replace('\n', ' | ')[:160])
print('themes:', themes.replace('\n', ' | ')[:160])
print('negatives:', neg.replace('\n', ' | ')[:120])
print('page errors:', errors[:2])
ok = shown and 'นำเข้า' in status and 'รีวิวทั้งหมด' in kpis and 'รอนาน' in themes and not errors
print('RESULT:', 'OK' if ok else 'PROBLEMS')
sys.exit(0 if ok else 1)
