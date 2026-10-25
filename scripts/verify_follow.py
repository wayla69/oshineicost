# -*- coding: utf-8 -*-
"""Phase 8c acceptance: the tabs with a LOCAL picker follow the GLOBAL period
the same way before and after the follow rule moves into the TABS registry.

For each tab: set the global month → open the tab → the local picker holds
the global month (or its last day); change the global month → the picker
follows; pick a local value by hand → switch away and back → the hand pick
survives (a same-filter reload never overrides it); change the global again
→ it follows again. Prints one line per check; the two runs must be identical.
"""
import os, sys, json
from playwright.sync_api import sync_playwright

BASE = os.environ.get('MGT_BASE', 'http://localhost:8020/')
MONTH_TABS = [('cashctl', 'ccMonth'), ('custtype', 'ctMonth'), ('pricezone', 'pzMonth'),
              ('cancelbill', 'cbMonth'), ('opsreport', 'opMonth'), ('pnlbranch', 'pnlMonthSel')]
DATE_TABS = [('dailysales', 'dsDate'), ('costhead', 'chDate'), ('pnlbill', 'pbDate'), ('closetime', 'ctmDate')]
RANGE_TABS = [('heavyeater', 'heFrom', 'heTo')]
MRANGE_TABS = [('branchbom', 'bbomMonthFrom', 'bbomMonthTo')]

out = []
def say(*a):
    line = ' '.join(str(x) for x in a); out.append(line); print(line)

with sync_playwright() as pw:
    b = pw.chromium.launch(channel='chrome', headless=True)
    pg = b.new_page(viewport={'width': 1400, 'height': 900})
    errors = []
    pg.on('pageerror', lambda e: errors.append(str(e)))
    pg.goto(BASE, wait_until='networkidle', timeout=90000)
    pg.wait_for_function("typeof switchTab === 'function' && typeof TABS === 'object'", timeout=30000)
    pg.wait_for_function("document.querySelectorAll('#tabsRoot > div[id^=\"tab-\"]').length >= 48", timeout=30000)
    months = pg.evaluate("[...document.getElementById('selMonthTo').options].map(o => o.value).filter(Boolean).sort()")
    say('months available:', months[0], '..', months[-1], f'({len(months)})')
    m_a, m_b = months[-3], months[-4]      # two past, complete months

    def set_global_month(m):
        pg.evaluate("""m => { const f = document.getElementById('selMonthFrom'), t = document.getElementById('selMonthTo');
            f.value = m; t.value = m; t.dispatchEvent(new Event('change', {bubbles: true})); }""", m)
        pg.wait_for_timeout(700)

    def open_tab(name):
        pg.evaluate("n => { document.body.classList.add('force-desktop'); switchTab(n); }", name)
        pg.wait_for_timeout(900)

    def val(id_):
        return pg.evaluate("i => (document.getElementById(i) || {}).value ?? null", id_)

    def set_local(id_, v):
        pg.evaluate("([i, v]) => { const e = document.getElementById(i); e.value = v; }", [id_, v])

    for tab, sel in MONTH_TABS:
        set_global_month(m_a); open_tab(tab)
        say(f'{tab}:{sel} after global={m_a} ->', val(sel))
        set_global_month(m_b); say(f'{tab}:{sel} after global={m_b} ->', val(sel))
        set_local(sel, m_a); open_tab('home'); open_tab(tab); say(f'{tab}:{sel} hand pick {m_a} survives reload ->', val(sel))
        set_global_month(m_a); open_tab(tab); say(f'{tab}:{sel} after global={m_a} again ->', val(sel))
    for tab, inp in DATE_TABS:
        set_global_month(m_a); open_tab(tab)
        say(f'{tab}:{inp} after global={m_a} ->', val(inp))
        set_global_month(m_b); say(f'{tab}:{inp} after global={m_b} ->', val(inp))
        set_local(inp, m_a + '-15'); open_tab('home'); open_tab(tab); say(f'{tab}:{inp} hand pick survives reload ->', val(inp))
    for tab, f, t in RANGE_TABS:
        set_global_month(m_a); open_tab(tab)
        say(f'{tab}:{f}/{t} after global={m_a} ->', val(f), val(t))
        set_global_month(m_b); say(f'{tab}:{f}/{t} after global={m_b} ->', val(f), val(t))
    for tab, f, t in MRANGE_TABS:
        set_global_month(m_a); open_tab(tab)
        say(f'{tab}:{f}/{t} after global={m_a} ->', val(f), val(t))
        set_global_month(m_b); say(f'{tab}:{f}/{t} after global={m_b} ->', val(f), val(t))
    say('page errors:', len(errors), errors[:2])
    b.close()

tag = sys.argv[1] if len(sys.argv) > 1 else 'run'
open(os.path.join(os.path.dirname(__file__), f'verify_follow_{tag}.txt'), 'w', encoding='utf-8').write('\n'.join(out) + '\n')
