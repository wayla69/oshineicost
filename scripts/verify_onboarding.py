# -*- coding: utf-8 -*-
"""Three owner asks of 2026-09-05 reach the screens: the role start card (three
tabs for the role, each button switches, dismiss removes it), the Settings →
Monthly Targets editor shows where every target comes from (automatic = last
year's same month, on real data), and the people section carries the weekly
digest line + send button. Against a dev server on the scratch DB (auth off,
so the card is invoked directly):

    OSHINEI_DB=<db> py -3.12 -m uvicorn main:app --port 8021   (in backend/)
    PYTHONIOENCODING=utf-8 py -3.14 scripts/verify_onboarding.py
"""
import os, sys
from playwright.sync_api import sync_playwright

BASE = os.environ.get('MGT_BASE', 'http://localhost:8021/')
checks = []


def check(label, ok, detail=''):
    checks.append((label, bool(ok), detail))
    print(('  ✓ ' if ok else '  ✗ ') + label + (f' — {detail}' if detail and not ok else ''))


with sync_playwright() as pw:
    b = pw.chromium.launch(channel='chrome', headless=True)
    pg = b.new_page(viewport={'width': 1400, 'height': 900})
    errors = []
    pg.on('pageerror', lambda e: errors.append(str(e)))
    pg.goto(BASE, wait_until='networkidle', timeout=90000)
    pg.wait_for_function("typeof switchTab === 'function' && typeof showStartCard === 'function'", timeout=30000)
    pg.wait_for_function("document.querySelectorAll('#tabsRoot > div[id^=\"tab-\"]').length >= 48", timeout=30000)
    pg.evaluate("() => document.body.classList.add('force-desktop')")

    # (2) the role start card
    pg.evaluate("() => { switchTab('home'); showStartCard('accounting'); }")
    pg.wait_for_timeout(500)
    card = pg.evaluate("() => (document.getElementById('homeStartCard') || {}).innerText || ''")
    check('start card renders for accounting', 'เริ่มต้นใช้งาน' in card and 'ขั้นที่ 3' in card, card[:160])
    check('card names the three accounting tabs', all(k in card for k in ('Discounts', 'รายงานปฏิบัติการ', 'Management Report')), card[:300])
    pg.click('#homeStartCard button:nth-of-type(1)') if False else None
    pg.evaluate("() => document.querySelector('#homeStartCard .grid button').click()")
    pg.wait_for_timeout(800)
    active = pg.evaluate("() => activeTab")
    check('first card button opens the discounts tab', active == 'discounts', active)
    pg.evaluate("() => switchTab('home')")
    pg.evaluate("() => dismissStartCard()")
    pg.wait_for_timeout(300)
    gone = pg.evaluate("() => !document.getElementById('homeStartCard')")
    dismissed = pg.evaluate("() => !!localStorage.getItem('startCard:anon')")
    check('เข้าใจแล้ว removes the card and remembers it', gone and dismissed, f'gone={gone} remembered={dismissed}')
    pg.evaluate("() => localStorage.removeItem('startCard:anon')")
    for role in ('management', 'operation', 'marketing', 'analyst', 'innovation'):
        pg.evaluate(f"() => showStartCard('{role}')")
        n = pg.evaluate("() => document.querySelectorAll('#homeStartCard .grid button').length")
        check(f'{role} card has 3 tabs', n == 3, str(n))
    pg.evaluate("() => dismissStartCard(); localStorage.removeItem('startCard:anon')")

    # (3) targets: automatic = last year's same month, shown per row
    pg.evaluate("() => { switchTab('settings'); switchSettingsTab('targets'); }")
    pg.wait_for_function("(document.getElementById('targetBody') || {}).innerText && document.getElementById('targetBody').innerText.length > 50", timeout=60000)
    month = pg.evaluate("() => document.getElementById('targetMonth').value")
    tb = pg.evaluate("() => document.getElementById('targetBody').innerText")
    head = pg.evaluate("() => document.querySelector('#settab-targets thead').innerText")
    check('targets table has the ที่มาของเป้า column', 'ที่มาของเป้า' in head, head[:120])
    auto = pg.evaluate("() => [...document.querySelectorAll('#targetBody tr')].filter(tr => tr.innerText.includes('อัตโนมัติ')).length")
    rows = pg.evaluate("() => document.querySelectorAll('#targetBody tr').length")
    check(f'automatic targets on real data for {month}', auto > 0 and 'ยอดจริง' in tb, f'auto rows {auto}/{rows}')
    ph = pg.evaluate("() => [...document.querySelectorAll('#targetBody .target-rev-input')].filter(i => i.placeholder && i.placeholder !== '—').length")
    check('automatic rows show the figure as placeholder, input left blank', ph == auto, f'placeholders {ph} vs auto {auto}')
    api_rows = pg.evaluate(f"async () => (await (await fetch('/api/targets?month={month}')).json())")
    srcs = {r['source'] for r in api_rows}
    check('/api/targets carries source + basis_month', srcs <= {'manual', 'last_year', 'none'} and api_rows[0]['basis_month'] == f'{int(month[:4]) - 1}{month[4:]}', f'{srcs} {api_rows[0].get("basis_month")}')

    # (1) the weekly digest line + button in the people section
    pg.evaluate("() => switchSettingsTab('usage')")
    pg.wait_for_function("(document.getElementById('usageStatsBody') || {}).innerText && document.getElementById('usageStatsBody').innerText.includes('สรุปรายสัปดาห์')", timeout=60000)
    txt = pg.evaluate("() => document.getElementById('usageStatsBody').innerText")
    check('people section shows the weekly digest line', 'สรุปรายสัปดาห์' in txt and 'ส่งสรุปตอนนี้' in txt, txt[:200])
    pg.evaluate("() => sendAdoptionDigest()")
    pg.wait_for_function("(document.getElementById('adoptionDigestStatus') || {}).innerText && document.getElementById('adoptionDigestStatus').innerText.startsWith('✓')", timeout=30000)
    st = pg.evaluate("() => document.getElementById('adoptionDigestStatus').innerText")
    check('send-now builds this week\'s digest (in-app when Telegram is not configured)', st.startswith('✓') and 'เก็บในแอปแล้ว' in st, st)
    d = pg.evaluate("async () => (await (await fetch('/api/usage/adoption-digest')).json())")
    check('digest stored and previewable', d['latest'] and d['latest']['week'] == d['week'] and d['preview'].startswith('📣'), str(d.get('latest', {}))[:120])
    b.close()

check('no page errors', not errors, '; '.join(errors)[:300])
bad = [c for c in checks if not c[1]]
print('RESULT:', 'OK — start card, automatic targets, weekly digest' if not bad else f'BROKEN ({len(bad)} failed)')
sys.exit(0 if not bad else 1)
