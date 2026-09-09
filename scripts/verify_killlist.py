# -*- coding: utf-8 -*-
"""Kill list — ON HOLD (owner, 2026-09-05): nothing is hidden. Every cluster bar
shows all of its members (no "⋯" chip), the 17 candidates still open, and
Settings → 📈 การใช้งานจริง lists them as "เฝ้าดู" with counts. When SOFT_KILL is
non-empty again, the fold checks at the bottom run instead. Against a dev server:

    OSHINEI_DB=<db> py -3.12 -m uvicorn main:app --port 8021   (in backend/)
    PYTHONIOENCODING=utf-8 py -3.14 scripts/verify_killlist.py
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
    pg.wait_for_function("typeof switchTab === 'function' && typeof SOFT_KILL === 'object' && typeof KILL_CANDIDATES === 'object'", timeout=30000)
    pg.wait_for_function("document.querySelectorAll('#tabsRoot > div[id^=\"tab-\"]').length >= 48", timeout=30000)
    pg.evaluate("() => document.body.classList.add('force-desktop')")
    n_cand = pg.evaluate("() => KILL_CANDIDATES.length")
    n_fold = pg.evaluate("() => SOFT_KILL.size")
    check('17 candidates under watch', n_cand == 17, str(n_cand))
    on_hold = n_fold == 0
    check('on hold: nothing folded', on_hold, f'SOFT_KILL.size={n_fold}')

    if on_hold:
        # every cluster bar shows all of its members, no ⋯ chip
        for tab, must in (('cost', ['BoM Cost', 'Simulators', 'Top60', 'ความครอบคลุม', 'นักกินหนัก']),
                          ('opsreport', ['ส่วนลด', 'พนักงาน', 'เงินสด', 'บิลยกเลิก', 'Modifiers', 'ราคา/โซน', 'ปิดร้าน']),
                          ('gpbranch', ['P&L รายสาขา', 'P&L ต่อบิล', 'Benchmark', 'กำไร SKU'])):
            pg.evaluate(f"() => switchTab('{tab}')")
            pg.wait_for_timeout(600)
            bar = pg.evaluate("() => document.getElementById('clusterBar').innerText")
            missing = [m for m in must if m not in bar]
            check(f'{tab} cluster bar shows every member', not missing and '⋯' not in bar, f'missing={missing} bar={bar[:120]}')
        # a candidate still opens directly
        pg.evaluate("() => switchTab('costheadtop60')")
        pg.wait_for_timeout(800)
        disp = pg.evaluate("() => getComputedStyle(document.getElementById('tab-costheadtop60')).display")
        check('candidate tab opens', disp != 'none', disp)
    else:
        pg.evaluate("() => switchTab('cost')")
        pg.wait_for_timeout(600)
        more = pg.evaluate("() => (document.getElementById('clusterMore') || {}).innerText || ''")
        check('cost cluster shows a ⋯ chip', more.strip().startswith('⋯'), more)
        pg.click('#clusterMore')
        pg.wait_for_timeout(300)
        check('⋯ unfolds', pg.evaluate("() => !!document.getElementById('clusterFold')"))

    # Settings → usage: the watched list with counts
    pg.evaluate("() => { switchTab('settings'); switchSettingsTab('usage'); }")
    pg.wait_for_timeout(2500)
    txt = pg.evaluate("() => (document.getElementById('usageStatsBody') || {}).innerText || ''")
    check('usage panel renders', 'ไม่สำเร็จ' not in txt and 'Top 20 API endpoints' in txt, txt[:80])
    check('kill list section says on hold' if on_hold else 'kill list section present', ('พักไว้' in txt) if on_hold else ('Kill list' in txt), txt[:200])
    check('watched list includes Top60', 'Top 60' in txt)
    b.close()

check('no page errors', not errors, '; '.join(errors)[:200])
bad = [c for c in checks if not c[1]]
print('RESULT:', 'OK — kill list on hold, everything visible' if not bad else f'BROKEN ({len(bad)} failed)')
sys.exit(0 if not bad else 1)
