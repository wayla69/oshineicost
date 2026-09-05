# -*- coding: utf-8 -*-
"""Kill list stage 1 (docs/LEAN_KILL_LIST_2026-09.html): the cluster bar folds the
never-opened tabs behind a "⋯" chip, the chip unfolds them dimmed, a folded tab
still opens (and is never hidden while active), and Settings → 📈 การใช้งานจริง
lists the kill list with its counts. Run against a dev server:

    OSHINEI_DB=<db> py -3.12 -m uvicorn main:app --port 8021   (in backend/)
    py -3.14 scripts/verify_killlist.py
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
    pg.wait_for_function("typeof switchTab === 'function' && typeof SOFT_KILL === 'object'", timeout=30000)
    pg.wait_for_function("document.querySelectorAll('#tabsRoot > div[id^=\"tab-\"]').length >= 48", timeout=30000)
    pg.evaluate("() => document.body.classList.add('force-desktop')")
    n_kill = pg.evaluate("() => SOFT_KILL.size")
    check('SOFT_KILL has 17 tabs', n_kill == 17, str(n_kill))

    # 💰 cost cluster: 8 members, 4 folded (simhub, costhead, costheadtop60, coverage)
    pg.evaluate("() => switchTab('cost')")
    pg.wait_for_timeout(600)
    bar = pg.evaluate("() => document.getElementById('clusterBar').innerText")
    more = pg.evaluate("() => (document.getElementById('clusterMore') || {}).innerText || ''")
    check('cost cluster shows ⋯ 4', more.strip().startswith('⋯') and '4' in more, more)
    check('folded tab not in bar while folded', 'Top60' not in bar and 'Simulators' not in bar, bar)
    check('kept tabs still in bar', 'BoM Cost' in bar and 'นักกินหนัก' in bar, bar)
    pg.click('#clusterMore')
    pg.wait_for_timeout(300)
    bar2 = pg.evaluate("() => document.getElementById('clusterBar').innerText")
    check('⋯ unfolds the 4 tabs', 'Top60' in bar2 and 'Simulators' in bar2 and 'ความครอบคลุม' in bar2, bar2)
    check('fold-back chip present', pg.evaluate("() => !!document.getElementById('clusterFold')"))
    # a folded tab opens from the unfolded chip and stays visible as the active chip
    pg.evaluate("() => switchTab('costheadtop60')")
    pg.wait_for_timeout(800)
    active = pg.evaluate("() => getComputedStyle(document.getElementById('tab-costheadtop60')).display")
    bar3 = pg.evaluate("() => document.getElementById('clusterBar').innerText")
    check('folded tab panel opens', active != 'none', active)
    check('active folded tab shown in bar', 'Top60' in bar3, bar3)
    # fold back, then a folded tab opened directly (bookmark) is still shown
    pg.click('#clusterFold')
    pg.wait_for_timeout(300)
    pg.evaluate("() => switchTab('bomcost')")
    pg.wait_for_timeout(500)
    bar4 = pg.evaluate("() => document.getElementById('clusterBar').innerText")
    check('folded again after ‹', 'Top60' not in bar4 and '⋯' in bar4, bar4)
    pg.evaluate("() => switchTab('closetime')")
    pg.wait_for_timeout(500)
    bar5 = pg.evaluate("() => document.getElementById('clusterBar').innerText")
    check('direct open of a folded ops tab keeps it visible', 'ปิดร้าน' in bar5 and 'ปฏิบัติการ' in bar5, bar5)

    # Settings → usage: kill list table
    pg.evaluate("() => { switchTab('settings'); switchSettingsTab('usage'); }")
    pg.wait_for_timeout(2500)
    txt = pg.evaluate("() => (document.getElementById('usageStatsBody') || {}).innerText || ''")
    check('usage panel renders', 'ไม่สำเร็จ' not in txt and 'Top 20 API endpoints' in txt, txt[:80])
    check('kill list section present', 'Kill list' in txt and '2026-10-05' in txt, txt[:200])
    check('kill list lists Top60', 'Top 60' in txt)
    b.close()

check('no page errors', not errors, '; '.join(errors)[:200])
bad = [c for c in checks if not c[1]]
print('RESULT:', 'OK — kill list stage 1 behaves' if not bad else f'BROKEN ({len(bad)} failed)')
sys.exit(0 if not bad else 1)
