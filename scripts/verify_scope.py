# -*- coding: utf-8 -*-
"""E4 acceptance (Playwright, dev server on :8021, auth OFF locally): the mobile
home shows the customer-voice queue with ✓ ตอบแล้ว, the settings user form has
the branch select, and the feedback tab renders the pinned-branch chip when the
session carries a branch (simulated by setting window.AUTH.branch)."""
import os, sys
from playwright.sync_api import sync_playwright

BASE = os.environ.get('MGT_BASE', 'http://localhost:8021/')
SC = os.environ.get('SHOT_DIR', os.path.dirname(os.path.abspath(__file__)))
errors = []
with sync_playwright() as pw:
    b = pw.chromium.launch(channel='chrome', headless=True)
    # phone: the mobile home with the voice queue
    m = b.new_page(viewport={'width': 390, 'height': 844})
    m.on('pageerror', lambda e: errors.append('mobile: ' + str(e)))
    m.on('dialog', lambda d: d.accept('โทรแล้ว ขอโทษลูกค้าแล้ว'))
    m.goto(BASE, wait_until='networkidle', timeout=90000)
    m.wait_for_function("typeof msLoadVoice === 'function'", timeout=30000)
    m.wait_for_function("document.getElementById('msVoiceWrap') && document.getElementById('msVoiceWrap').style.display !== 'none'", timeout=60000)
    m.wait_for_timeout(800)
    voice = m.evaluate("() => document.getElementById('msVoice').innerText")
    count = m.evaluate("() => document.getElementById('msVoiceCount').innerText")
    m.evaluate("() => document.getElementById('msVoiceWrap').scrollIntoView()")
    m.screenshot(path=os.path.join(SC, '.tmp_scope_mobile.png'))
    before = m.evaluate("() => document.querySelectorAll('#msVoice button').length")
    m.evaluate("() => { const b = document.querySelector('#msVoice button'); b && b.click(); }")
    m.wait_for_timeout(2500)
    after_txt = m.evaluate("() => document.getElementById('msVoice').innerText")

    # desktop: settings → users has the branch select; feedback tab pins a scoped branch
    pg = b.new_page(viewport={'width': 1400, 'height': 1000})
    pg.on('pageerror', lambda e: errors.append('desktop: ' + str(e)))
    pg.goto(BASE, wait_until='networkidle', timeout=90000)
    pg.wait_for_function("typeof switchTab === 'function' && TABS.feedback", timeout=30000)
    pg.evaluate("() => { document.body.classList.add('force-desktop'); switchTab('settings'); }")
    pg.wait_for_timeout(1500)
    pg.evaluate("() => switchSettingsTab('users')")
    pg.wait_for_function("document.getElementById('newUserBranch') && document.getElementById('newUserBranch').options.length > 1", timeout=30000)
    branch_opts = pg.evaluate("() => document.getElementById('newUserBranch').options.length")
    header = pg.evaluate("() => document.querySelector('#userTableBody') ? document.querySelector('#userTableBody').closest('table').querySelector('thead').innerText : ''")
    pg.evaluate("() => { window.AUTH.branch = 'ระยอง'; switchTab('feedback'); }")
    pg.wait_for_function("document.getElementById('fbBranchChip').innerText.includes('สาขาของคุณ')", timeout=30000)
    pg.wait_for_timeout(1500)
    chip = pg.evaluate("() => document.getElementById('fbBranchChip').innerText")
    period = pg.evaluate("() => document.getElementById('fbPeriod').innerText")
    branches_tbl = pg.evaluate("() => document.getElementById('fbBranches').innerText")
    pg.screenshot(path=os.path.join(SC, '.tmp_scope_tab.png'))
    b.close()

print('mobile queue  :', voice.replace('\n', ' | ')[:220])
print('count / btns  :', count, '/', before)
print('after handled :', after_txt.replace('\n', ' | ')[:120])
print('branch options:', branch_opts, '| header:', header.replace('\n', ' | ')[:80])
print('scoped chip   :', chip, '|', period[:80])
print('branch table  :', branches_tbl.replace('\n', ' | ')[:120])
print('page errors   :', errors[:3])
ok = ('ตอบแล้ว' in voice and before >= 1 and after_txt != voice and branch_opts > 1 and 'สาขา' in header
      and 'สาขาของคุณ: ระยอง' in chip and 'ระยอง' in period and not errors)
print('RESULT:', 'OK' if ok else 'PROBLEMS')
sys.exit(0 if ok else 1)
