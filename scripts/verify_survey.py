# -*- coding: utf-8 -*-
"""📱 QR survey acceptance (Playwright, dev server on :8021): create a branch
link from the tab's panel, open the guest page on a phone-sized viewport, tap a
face + a chip, type, leave a contact, submit → thanks; then the tab shows the
answer as kind แบบสอบถาม, in the work queue with 📞, and the poster page draws."""
import os, sys
from playwright.sync_api import sync_playwright

BASE = os.environ.get('MGT_BASE', 'http://localhost:8021/')
SC = os.environ.get('SHOT_DIR', os.path.dirname(os.path.abspath(__file__)))
errors = []
with sync_playwright() as pw:
    b = pw.chromium.launch(channel='chrome', headless=True)
    pg = b.new_page(viewport={'width': 1400, 'height': 1000})
    pg.on('pageerror', lambda e: errors.append('tab: ' + str(e)))
    pg.goto(BASE, wait_until='networkidle', timeout=90000)
    pg.wait_for_function("typeof switchTab === 'function' && TABS.feedback", timeout=30000)
    pg.evaluate("() => { document.body.classList.add('force-desktop'); switchTab('feedback'); }")
    pg.wait_for_timeout(2000)
    # the manager creates the link for the first branch that has none, and sets a review url
    link = pg.evaluate("""async () => {
      const d = await (await fetch('/api/feedback/survey-links')).json();
      const l = d.links.find(x => !x.code) || d.links[0];
      const r = await fetch('/api/feedback/survey-links', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({branch_id: l.branch_id, google_review_url: 'https://g.page/r/test/review', by: 'verify'})});
      return await r.json();
    }""")
    code, branch = link['code'], link['branch']
    pg.evaluate("() => { const d = document.getElementById('fbSurvey'); d.open = true; d.scrollIntoView(); }")
    pg.wait_for_function("document.getElementById('fbSurveyLinks').innerText.includes('โปสเตอร์')", timeout=30000)
    panel = pg.evaluate("() => document.getElementById('fbSurveyLinks').innerText")
    pg.screenshot(path=os.path.join(SC, '.tmp_survey_panel.png'))

    # the guest
    m = b.new_page(viewport={'width': 390, 'height': 844})
    m.on('pageerror', lambda e: errors.append('guest: ' + str(e)))
    m.goto(f"{BASE}s/{code}", wait_until='networkidle', timeout=60000)
    m.screenshot(path=os.path.join(SC, '.tmp_survey_form.png'), full_page=True)
    m.click('.face[data-v="2"]')
    m.click('.chip[data-a="รอนาน"]')
    m.fill('#txt', 'รอเติมซาชิมินานมาก โต๊ะข้าง ๆ ได้ก่อน')
    m.fill('#contact', '08x-verify-000')
    m.wait_for_timeout(2500)                       # the bot guard wants a human's pause
    m.click('#go')
    m.wait_for_function("getComputedStyle(document.getElementById('thanks')).display !== 'none'", timeout=30000)
    thanks = m.evaluate("() => document.getElementById('thanks').innerText")
    m.screenshot(path=os.path.join(SC, '.tmp_survey_thanks.png'))
    # a happy guest sees the Google button
    m.goto(f"{BASE}s/{code}", wait_until='networkidle', timeout=60000)
    m.click('.face[data-v="5"]'); m.click('.chip[data-a="ความสด"]'); m.fill('#txt', 'แซลมอนสดมาก')
    m.wait_for_timeout(2500); m.click('#go')
    m.wait_for_function("getComputedStyle(document.getElementById('thanks')).display !== 'none'", timeout=30000)
    gbtn = m.evaluate("() => { const a = document.querySelector('#gwrap a'); return a ? a.href : ''; }")
    m.screenshot(path=os.path.join(SC, '.tmp_survey_thanks5.png'))
    # the poster
    m.set_viewport_size({'width': 700, 'height': 1000})
    m.goto(f"{BASE}s/{code}/print", wait_until='networkidle', timeout=60000)
    m.wait_for_timeout(1500)
    qr = m.evaluate("() => !!document.querySelector('#qr img, #qr canvas')")
    m.screenshot(path=os.path.join(SC, '.tmp_survey_poster.png'))

    # back in the tab
    pg.evaluate("() => loadFeedback(true)")
    pg.wait_for_function("document.getElementById('fbKindChips').innerText.includes('แบบสอบถาม')", timeout=30000)
    pg.wait_for_timeout(2000)
    chips = pg.evaluate("() => document.getElementById('fbKindChips').innerText")
    queue = pg.evaluate("() => document.getElementById('fbNeg').innerText")
    pg.evaluate("() => setFeedbackKind('survey')"); pg.wait_for_timeout(2000)
    kpis = pg.evaluate("() => document.getElementById('fbKpis').innerText")
    pg.evaluate("() => { document.querySelector('main').scrollTop = 0; }")
    pg.screenshot(path=os.path.join(SC, '.tmp_survey_tab.png'))
    b.close()

print('link          :', code, branch)
print('panel         :', panel.replace('\n', ' | ')[:200])
print('thanks (2★)   :', thanks.replace('\n', ' '))
print('google btn 5★ :', gbtn)
print('poster QR     :', qr)
print('kind chips    :', chips.replace('\n', ' | '))
print('queue         :', queue.replace('\n', ' | ')[:240])
print('kpis (survey) :', kpis.replace('\n', ' | ')[:120])
print('page errors   :', errors[:3])
ok = (len(code) == 8 and 'โปสเตอร์' in panel and 'ติดต่อกลับ' in thanks and gbtn.startswith('https://g.page/r/test/review')
      and qr and 'แบบสอบถาม' in chips and '📞 08x-verify-000' in queue and 'รอนาน' in queue and not errors)
print('RESULT:', 'OK' if ok else 'PROBLEMS')
sys.exit(0 if ok else 1)
