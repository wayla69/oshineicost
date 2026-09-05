# -*- coding: utf-8 -*-
"""✅ Go-live checklist acceptance (Playwright, dev server on MGT_BASE): open the
tab, tick a step and see it attributed, watch the Complete button stay disabled,
tick everything through the page's own action, complete → banner + lock,
reopen → unlocked."""
import os
import sys
from playwright.sync_api import sync_playwright

BASE = os.environ.get('MGT_BASE', 'http://localhost:8021/')
errors = []
with sync_playwright() as pw:
    b = pw.chromium.launch(channel='chrome', headless=True)
    pg = b.new_page(viewport={'width': 1400, 'height': 1000})
    pg.on('pageerror', lambda e: errors.append(str(e)))
    pg.goto(BASE, wait_until='networkidle', timeout=90000)
    pg.wait_for_function("typeof switchTab === 'function' && typeof TABS === 'object' && TABS.checklist", timeout=30000)
    pg.wait_for_function("document.querySelectorAll('#tabsRoot > div[id^=\"tab-\"]').length >= 50", timeout=30000)
    pg.evaluate("() => { document.body.classList.add('force-desktop'); switchTab('checklist'); }")
    pg.wait_for_function("document.querySelectorAll('#clPhases input[type=checkbox]').length >= 80", timeout=30000)
    shown = pg.evaluate("() => getComputedStyle(document.getElementById('tab-checklist')).display !== 'none'")
    n_steps = pg.evaluate("() => document.querySelectorAll('#clPhases input[type=checkbox]').length")
    # reset any earlier run: untick everything that is ticked (through the page's own action)
    pg.evaluate("""async () => { if (document.getElementById('clDone').style.display !== 'none') { await _clPost('/api/checklist/feedback_rollout/reopen', _clBody({reason: 'verify'})); }
      const st = await getJSON('/api/checklist/feedback_rollout');
      for (const id of Object.keys(st.steps)) { if (st.steps[id].done) await _clPost(`/api/checklist/feedback_rollout/step/${id}`, _clBody({done: false})); } }""")
    pg.fill('#clWho', 'ทดสอบ')
    pg.evaluate("() => loadChecklist(true)")
    pg.wait_for_function("document.getElementById('clLine').textContent.startsWith('0 จาก')", timeout=30000)
    disabled_before = pg.evaluate("() => document.getElementById('clComplete').disabled")
    pg.click('#clcb-s0101')
    pg.wait_for_function("document.getElementById('clby-s0101').textContent.includes('ทดสอบ')", timeout=30000)
    line1 = pg.evaluate("() => document.getElementById('clLine').textContent")
    pct1 = pg.evaluate("() => document.getElementById('clPct').textContent")
    # complete must be refused while steps are open (call the same action the button runs, without the confirm)
    refused = pg.evaluate("""async () => { try { await _clPost('/api/checklist/feedback_rollout/complete', _clBody({})); return 'accepted'; } catch (e) { return (e.detail && e.detail.message) || e.message; } }""")
    # tick everything, then complete
    pg.evaluate("""async () => { const plan = _clPlan; for (const ph of plan.phases) for (const t of ph.tasks) for (const s of t.steps) await _clPost(`/api/checklist/feedback_rollout/step/${s.id}`, _clBody({done: true})); }""")
    pg.evaluate("() => loadChecklist(true)")
    pg.wait_for_function("document.getElementById('clPct').textContent === '100%'", timeout=30000)
    disabled_full = pg.evaluate("() => document.getElementById('clComplete').disabled")
    pg.once('dialog', lambda d: d.accept())
    pg.click('#clComplete')
    pg.wait_for_function("document.getElementById('clDone').style.display !== 'none'", timeout=30000)
    banner = pg.evaluate("() => document.getElementById('clDone').innerText")
    locked_cb = pg.evaluate("() => document.getElementById('clcb-s0101').disabled")
    pg.screenshot(path=os.path.join(os.path.dirname(__file__), '..', '.tmp_checklist_done.png'), full_page=False)
    # reopen (auth off → allowed), then leave the scratch state clean
    pg.once('dialog', lambda d: d.accept('verify done'))
    pg.evaluate("() => reopenChecklist()")
    pg.wait_for_function("document.getElementById('clDone').style.display === 'none'", timeout=30000)
    reopened_cb = pg.evaluate("() => document.getElementById('clcb-s0101').disabled")
    log = pg.evaluate("() => document.getElementById('clLog').innerText")
    b.close()
print('tab shown       :', shown, '| steps:', n_steps)
print('complete before :', 'disabled' if disabled_before else 'ENABLED?!')
print('after one tick  :', pct1, '|', line1[:90])
print('refused message :', refused[:90])
print('complete at 100%:', 'enabled' if not disabled_full else 'DISABLED?!')
print('banner          :', banner.replace('\n', ' ')[:120])
print('locked/reopened :', locked_cb, '/', reopened_cb)
print('log head        :', log.replace('\n', ' | ')[:120])
print('page errors     :', errors[:2])
ok = (shown and n_steps == 80 and disabled_before and pct1 != '0%' and 'ทดสอบ' in line1 and 'ยังไม่ครบ' in refused
      and not disabled_full and 'ปิดสมบูรณ์แล้ว' in banner and locked_cb and not reopened_cb and 'เปิดแก้ไขอีกครั้ง' in log and not errors)
print('RESULT:', 'OK' if ok else 'PROBLEMS')
sys.exit(0 if ok else 1)
