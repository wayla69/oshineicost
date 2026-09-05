# -*- coding: utf-8 -*-
"""The discount policy ceiling reaches the screens: the Discounts tab's fourth
KPI is the ฿ over policy, branch headers flag a branch over the ceiling with the
฿ amount, Settings → ค่าเริ่มต้น has the field and saving it changes the tab
(then it is restored). Run against a dev server on the scratch DB:

    OSHINEI_DB=<db> py -3.12 -m uvicorn main:app --port 8021   (in backend/)
    PYTHONIOENCODING=utf-8 py -3.14 scripts/verify_discount_policy.py
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
    pg.wait_for_function("typeof switchTab === 'function' && typeof TABS === 'object'", timeout=30000)
    pg.wait_for_function("document.querySelectorAll('#tabsRoot > div[id^=\"tab-\"]').length >= 48", timeout=30000)
    pg.evaluate("() => document.body.classList.add('force-desktop')")

    def save_policy(value):
        pg.evaluate("() => { switchTab('settings'); switchSettingsTab('prefs'); }")
        pg.evaluate("() => loadPreferences()")          # awaited: the form is filled from the server before we type
        pg.wait_for_function("document.getElementById('prefDiscountPolicy') && document.getElementById('prefDiscountPolicy').value !== ''", timeout=30000)
        pg.fill('#prefDiscountPolicy', value)
        pg.evaluate("() => savePreferences()")
        pg.wait_for_function("(document.getElementById('prefSaveStatus') || {}).innerText === '✓ Saved'", timeout=30000)

    # start from the default whatever an earlier run left behind
    save_policy('2.5')

    # the Discounts tab
    pg.evaluate("() => switchTab('discounts')")
    pg.wait_for_function("(document.getElementById('disc-kpi-row') || {}).innerText && document.getElementById('disc-kpi-row').innerText.includes('เพดาน')", timeout=240000)
    kpi = pg.evaluate("() => document.getElementById('disc-kpi-row').innerText")
    check('KPI tile "เกินเพดานส่วนลด 2.5%" present', 'เกินเพดานส่วนลด 2.5%' in kpi, kpi[:200])
    check('KPI tile shows the branch count over the ceiling', 'สาขาเกินเพดาน' in kpi, kpi[:200])
    policy = pg.evaluate("() => (_discData || {}).policy_pct")
    over = pg.evaluate("() => ((_discData || {}).by_branch || []).filter(b => b.over_policy).length")
    check('endpoint carries policy_pct', policy == 2.5, str(policy))
    body = pg.evaluate("() => (document.getElementById('discCashierBody') || {}).innerText || ''")
    check('branch header shows ฿ over policy when a branch is over', (over == 0) or ('เกินเพดาน' in body), f'over={over} body={body[:120]}')

    # Settings → ค่าเริ่มต้น: the field, saving a tighter ceiling, the tab follows (no filter change needed), restore
    pg.evaluate("() => { switchTab('settings'); switchSettingsTab('prefs'); }")
    pg.wait_for_function("document.getElementById('prefDiscountPolicy') && document.getElementById('prefDiscountPolicy').value !== ''", timeout=30000)
    val = pg.evaluate("() => document.getElementById('prefDiscountPolicy').value")
    check('Settings field prefilled with 2.5', val == '2.5', val)
    save_policy('0.5')
    pg.evaluate("() => switchTab('discounts')")
    pg.wait_for_function("(_discData || {}).policy_pct === 0.5", timeout=240000)
    kpi2 = pg.evaluate("() => document.getElementById('disc-kpi-row').innerText")
    over2 = pg.evaluate("() => ((_discData || {}).by_branch || []).filter(b => b.over_policy).length")
    check('tab follows the saved ceiling (0.5%) without a filter change', 'เกินเพดานส่วนลด 0.5%' in kpi2, kpi2[:200])
    check('a tighter ceiling puts more branches over it', over2 > over and over2 > 0, f'{over} → {over2}')
    body2 = pg.evaluate("() => (document.getElementById('discCashierBody') || {}).innerText || ''")
    check('branch headers show the ฿ over policy', 'เกินเพดาน' in body2, body2[:160])
    # restore
    save_policy('2.5')
    pg.evaluate("() => switchTab('discounts')")
    pg.wait_for_function("(_discData || {}).policy_pct === 2.5", timeout=240000)
    check('restored to 2.5', True)

    # Management report §7 wording carries the ceiling
    pg.evaluate("() => switchTab('mgtreport')")
    pg.wait_for_function("(() => { const t = (document.getElementById('tab-mgtreport') || {}).innerText || ''; return t.includes('วินัยส่วนลด') || t.includes('Discount discipline'); })()", timeout=240000)
    mg = pg.evaluate("() => document.getElementById('tab-mgtreport').innerText")
    i = max(mg.find('วินัยส่วนลด'), mg.find('Discount discipline'))
    check('management report §7 mentions the policy ceiling (TH or EN)', 'เพดานนโยบาย 2.5%' in mg or '2.5% policy ceiling' in mg, mg[i:i + 200])
    b.close()

check('no page errors', not errors, '; '.join(errors)[:200])
bad = [c for c in checks if not c[1]]
print('RESULT:', 'OK — discount policy reaches the screens' if not bad else f'BROKEN ({len(bad)} failed)')
sys.exit(0 if not bad else 1)
