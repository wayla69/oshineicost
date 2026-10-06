# -*- coding: utf-8 -*-
"""Every JS slice loads and every tab still opens: boot the shell, confirm the
functions that live in each slice exist, switch to EVERY key of the TABS
registry and open the mobile shell — with zero page errors. The net for any
re-slicing of js/*.js (load order is shared global scope). Against a dev server:

    OSHINEI_DB=<db> py -3.12 -m uvicorn main:app --port 8021   (in backend/)
    PYTHONIOENCODING=utf-8 py -3.14 scripts/verify_slices.py
"""
import os, sys
from playwright.sync_api import sync_playwright

BASE = os.environ.get('MGT_BASE', 'http://localhost:8021/')
# one function per slice, so a slice that failed to load is named
PER_SLICE = {'01-core.js': 'switchTab', '02-mgt-report.js': 'loadMgtReport', '03-tabs-main.js': 'loadTrend',
             '04-report-cost.js': 'loadCostAnalysis', '05-pnl-settings.js': 'savePreferences', '06-features.js': 'loadBenchmark',
             '07-home.js': 'loadHome', '08-guest-buffet.js': 'loadBuffetPerformance',
             '09-analysis-tabs.js': 'loadEngineering', '09b-cost-pnl.js': 'loadCostCoverage', '09c-mobile.js': 'showFullDashboard',
             '09d-bom-tools.js': 'initBomDeepDive', '09e-menu-compare-bom.js': 'loadMenuCompare',
             '10-ordering.js': 'loadOrderPlan', '11-feedback.js': 'loadFeedback', '12-checklist.js': 'loadChecklist',
             '13-bom-center.js': 'loadBomCenter', '14-branch.js': 'loadBranchStock', '15-orderhub.js': 'loadOrderHub'}
checks = []


def check(label, ok, detail=''):
    checks.append((label, bool(ok), detail))
    print(('  ✓ ' if ok else '  ✗ ') + label + (f' — {detail}' if detail and not ok else ''))


with sync_playwright() as pw:
    b = pw.chromium.launch(channel='chrome', headless=True)
    pg = b.new_page(viewport={'width': 1400, 'height': 900})
    errors, failed = [], []
    pg.on('pageerror', lambda e: errors.append(str(e)))
    pg.on('response', lambda r: failed.append(f'{r.status} {r.url}') if r.url.find('/js/') > 0 and r.status >= 400 else None)
    pg.goto(BASE, wait_until='networkidle', timeout=90000)
    pg.wait_for_function("typeof switchTab === 'function' && typeof TABS === 'object'", timeout=30000)
    pg.wait_for_function("document.querySelectorAll('#tabsRoot > div[id^=\"tab-\"]').length >= 48", timeout=30000)
    check('no failed /js/ responses', not failed, '; '.join(failed)[:200])
    tags = pg.evaluate("() => [...document.querySelectorAll('script[src^=\"/js/\"]')].map(s => s.getAttribute('src').split('?')[0].slice(4))")
    check('shell loads the slices in name order', tags == sorted(tags) and len(tags) == len(PER_SLICE), str(tags))
    missing = [f for f, fn in PER_SLICE.items() if pg.evaluate(f"() => typeof {fn}") != 'function']
    check('one function from every slice is defined', not missing, str(missing))
    pg.evaluate("() => document.body.classList.add('force-desktop')")
    keys = pg.evaluate("() => Object.keys(TABS)")
    broken = []
    for k in keys:
        before = len(errors)
        try:
            pg.evaluate(f"() => switchTab('{k}')")
            pg.wait_for_timeout(350)
        except Exception as e:                       # noqa: BLE001
            broken.append(f'{k}: {e}')
            continue
        if len(errors) > before:
            broken.append(f'{k}: {errors[-1][:100]}')
    pg.wait_for_timeout(4000)                        # let the last loaders settle
    check(f'every tab opens ({len(keys)})', not broken, '; '.join(broken)[:300])
    # the mobile shell (09c) still boots and returns
    pg.set_viewport_size({'width': 390, 'height': 800})          # the mobile layout is a media query
    pg.evaluate("() => showMobileSummary()")
    pg.wait_for_timeout(3000)
    ms = pg.evaluate("() => [...document.querySelectorAll('[id^=\"ms\"]')].filter(e => e.offsetParent !== null).length")
    check('mobile shell renders', ms > 0, f'visible ms* elements: {ms}')
    pg.set_viewport_size({'width': 1400, 'height': 900})
    pg.evaluate("() => showFullDashboard()")
    pg.wait_for_timeout(500)
    b.close()

check('no page errors', not errors, '; '.join(errors)[:300])
bad = [c for c in checks if not c[1]]
print('RESULT:', 'OK — every slice loads, every tab opens' if not bad else f'BROKEN ({len(bad)} failed)')
sys.exit(0 if not bad else 1)
