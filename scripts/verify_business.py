# -*- coding: utf-8 -*-
"""E2 acceptance (Playwright, dev server on :8021 with the scratch copy of the
live DB): the "📊 เสียงลูกค้า × ข้อมูลธุรกิจ" panel opens, reads the till and
renders the three blocks with a reading."""
import os, sys
from playwright.sync_api import sync_playwright

BASE = os.environ.get('MGT_BASE', 'http://localhost:8021/')
SC = os.environ.get('SHOT_DIR', os.path.dirname(os.path.abspath(__file__)))
errors = []
with sync_playwright() as pw:
    b = pw.chromium.launch(channel='chrome', headless=True)
    pg = b.new_page(viewport={'width': 1400, 'height': 1000})
    pg.on('pageerror', lambda e: errors.append(str(e)))
    pg.goto(BASE, wait_until='networkidle', timeout=90000)
    pg.wait_for_function("typeof switchTab === 'function' && TABS.feedback", timeout=30000)
    pg.evaluate("() => { document.body.classList.add('force-desktop'); switchTab('feedback'); }")
    pg.wait_for_function("document.getElementById('fbKpis').innerText.length > 0", timeout=30000)
    pg.evaluate("() => { const d = document.getElementById('fbBiz'); d.open = true; d.scrollIntoView(); }")
    pg.wait_for_function("document.getElementById('fbBizBranches').innerText.length > 20", timeout=120000)
    pg.wait_for_timeout(600)
    hint = pg.evaluate("() => document.getElementById('fbBizHint').innerText")
    notes = pg.evaluate("() => document.getElementById('fbBizNotes').innerText")
    load = pg.evaluate("() => document.getElementById('fbBizLoad').innerText")
    menus = pg.evaluate("() => document.getElementById('fbBizMenus').innerText")
    branches = pg.evaluate("() => document.getElementById('fbBizBranches').innerText")
    pg.screenshot(path=os.path.join(SC, '.tmp_business.png'))
    b.close()

print('hint     :', hint)
print('notes    :', notes.replace('\n', ' | ')[:240])
print('load     :', load.replace('\n', ' | ')[:200])
print('menus    :', menus.replace('\n', ' | ')[:240])
print('branches :', branches.replace('\n', ' | ')[:260])
print('errors   :', errors[:3])
ok = ('เสียง' in hint and notes and 'รอนาน' in load and 'อ่านว่า' in load and ('เมนู' in menus) and 'ส่วนลด' in branches
      and '฿' in branches and not errors)
print('RESULT:', 'OK' if ok else 'PROBLEMS')
sys.exit(0 if ok else 1)
