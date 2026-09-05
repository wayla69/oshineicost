# -*- coding: utf-8 -*-
"""E5 acceptance (Playwright, dev server on :8021, no API key): the 🧠 card
writes the rule-based narrative, "ask the data" answers with matches and cites,
a severe voice shows 🔥 and is pinned automatically, and the staff card lists
the people named."""
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
    # two voices the lexicon should grade: a severe one (pins itself) and a named compliment
    posted = pg.evaluate("""async () => {
      const rows = [
        {text: 'กินแล้วท้องเสียทั้งบ้าน พนักงานชื่อเก่งช่วยประสานดี', branch: 'ขอนแก่น', source: 'Google', rating: 2, date: '2026-09-05', external_id: 'ai:sev1'},
        {text: 'น้องมิ้นท์บริการดีมาก เติมแซลมอนเร็ว', branch: 'ระยอง', source: 'Google', rating: 5, date: '2026-09-05', external_id: 'ai:pos1'}];
      const r = await fetch('/api/feedback/upload', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({rows, by: 'verify'})});
      return await r.json();
    }""")
    pg.evaluate("() => { document.body.classList.add('force-desktop'); switchTab('feedback'); }")
    pg.wait_for_function("document.getElementById('fbNeg').innerText.includes('🔥')", timeout=30000)
    pg.wait_for_timeout(1000)
    queue = pg.evaluate("() => document.getElementById('fbNeg').innerText")
    staff = pg.evaluate("() => document.getElementById('fbStaff').innerText")
    kpi = pg.evaluate("() => document.getElementById('fbKpis').innerText")
    pg.evaluate("() => loadFeedbackNarrative()")
    pg.wait_for_function("document.getElementById('fbNarrative').innerText.includes('ช่วง')", timeout=30000)
    narrative = pg.evaluate("() => document.getElementById('fbNarrative').innerText")
    engine = pg.evaluate("() => document.getElementById('fbAiEngine').innerText")
    pg.fill('#fbAskQ', 'ลูกค้าบ่นเรื่องแซลมอนสาขาไหนบ้าง')
    pg.evaluate("() => askFeedback()")
    pg.wait_for_function("document.getElementById('fbAskA').innerText.includes('เสียง')", timeout=30000)
    answer = pg.evaluate("() => document.getElementById('fbAskA').innerText")
    pg.evaluate("() => document.getElementById('fbAiCard').scrollIntoView()")
    pg.wait_for_timeout(400)
    pg.screenshot(path=os.path.join(SC, '.tmp_ai_card.png'))
    pg.evaluate("() => document.getElementById('fbQueueCard').scrollIntoView()")
    pg.wait_for_timeout(300)
    pg.screenshot(path=os.path.join(SC, '.tmp_ai_queue.png'))
    b.close()

print('posted        :', posted.get('inserted'), 'inserted')
print('queue         :', queue.replace('\n', ' | ')[:260])
print('staff card    :', staff.replace('\n', ' | ')[:160])
print('kpi           :', kpi.replace('\n', ' | ')[:200])
print('narrative     :', engine, '|', narrative.replace('\n', ' | ')[:220])
print('answer        :', answer.replace('\n', ' | ')[:220])
print('page errors   :', errors[:3])
ok = ('🔥' in queue and 'อัตโนมัติ: ร้ายแรง' in queue and 'เก่ง' in staff and 'มิ้นท์' in staff and 'ร้ายแรง' in kpi
      and 'กฎ' in engine and 'ช่วง' in narrative and 'เสียง' in answer and 'แซลมอน' in answer and not errors)
print('RESULT:', 'OK' if ok else 'PROBLEMS')
sys.exit(0 if ok else 1)
