# -*- coding: utf-8 -*-
"""The Settings → การใช้งานจริง panel must render (it said "โหลดข้อมูลไม่สำเร็จ" for
weeks: loadUsageStats() called the global api() while a local `const api`
declared later in the same function shadowed it — a temporal-dead-zone
ReferenceError the try/catch swallowed. tsc --checkJs found it, 2026-09-04)."""
import os, sys
from playwright.sync_api import sync_playwright

BASE = os.environ.get('MGT_BASE', 'http://localhost:8021/')
with sync_playwright() as pw:
    b = pw.chromium.launch(channel='chrome', headless=True)
    pg = b.new_page(viewport={'width': 1400, 'height': 900})
    errors = []
    pg.on('pageerror', lambda e: errors.append(str(e)))
    pg.goto(BASE, wait_until='networkidle', timeout=90000)
    pg.wait_for_function("typeof switchTab === 'function' && typeof TABS === 'object'", timeout=30000)
    pg.wait_for_function("document.querySelectorAll('#tabsRoot > div[id^=\"tab-\"]').length >= 48", timeout=30000)
    pg.evaluate("() => { document.body.classList.add('force-desktop'); switchTab('settings'); switchSettingsTab('usage'); }")
    pg.wait_for_timeout(2500)
    txt = pg.evaluate("() => (document.getElementById('usageStatsBody') || {}).innerText || ''")
    b.close()
ok = ('ไม่สำเร็จ' not in txt) and ('Top 20 API endpoints' in txt)
print('usage panel text (head):', txt[:120].replace('\n', ' | '))
print('page errors:', errors[:2])
print('RESULT:', 'OK — panel renders' if ok and not errors else 'BROKEN')
sys.exit(0 if ok and not errors else 1)
