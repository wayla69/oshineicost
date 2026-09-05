# -*- coding: utf-8 -*-
"""E6 acceptance (Playwright, dev server on :8021): with no snapshot the card
explains what to configure; after a snapshot is posted the league renders with
gap, rank and the best competitor, and a branch click lists its competitors."""
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
    pg.evaluate("() => { document.body.classList.add('force-desktop'); switchTab('feedback'); }")
    pg.wait_for_function("document.getElementById('fbKpis').innerText.length > 0", timeout=30000)
    pg.evaluate("() => { const d = document.getElementById('fbComp'); d.open = true; d.scrollIntoView(); }")
    pg.wait_for_function("document.getElementById('fbCompBody').innerText.length > 10", timeout=30000)
    empty = pg.evaluate("() => document.getElementById('fbCompBody').innerText")
    posted = pg.evaluate("""async () => {
      const rows = [
        {branch: 'ขอนแก่น', place_id: 's', name: 'โอชิเน บุฟเฟ่ต์ ขอนแก่น', is_self: 1, rating: 4.3, ratings_total: 812},
        {branch: 'ขอนแก่น', place_id: 'c1', name: 'ชาบูอิ่มดี', rating: 4.6, ratings_total: 1400, distance_m: 550, price_level: 2},
        {branch: 'ขอนแก่น', place_id: 'c3', name: 'ซูชิบุฟเฟ่ต์', rating: 4.1, ratings_total: 60, distance_m: 900},
        {branch: 'ระยอง', place_id: 'sr', name: 'โอชิเน ระยอง', is_self: 1, rating: 4.5, ratings_total: 300},
        {branch: 'ระยอง', place_id: 'r1', name: 'หมูกระทะระยอง', rating: 4.2, ratings_total: 220, distance_m: 1200}];
      const r = await fetch('/api/feedback/competitors', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({month: '2026-09', rows})});
      return await r.json();
    }""")
    pg.evaluate("() => loadFeedbackCompetitors()")
    pg.wait_for_function("document.getElementById('fbCompBody').innerText.includes('ชาบูอิ่มดี')", timeout=30000)
    league = pg.evaluate("() => document.getElementById('fbCompBody').innerText")
    hint = pg.evaluate("() => document.getElementById('fbCompHint').innerText")
    pg.evaluate("() => loadFeedbackCompetitors('ขอนแก่น')")
    pg.wait_for_function("document.getElementById('fbCompBody').innerText.includes('ซูชิบุฟเฟ่ต์')", timeout=30000)
    detail = pg.evaluate("() => document.getElementById('fbCompBody').innerText")
    pg.screenshot(path=os.path.join(SC, '.tmp_competitors.png'))
    # leave no snapshot behind on the scratch DB
    pg.evaluate("async () => { await fetch('/api/feedback/competitors', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({month: '2026-09', rows: []})}); }")
    b.close()

print('empty state :', empty.replace('\n', ' ')[:120])
print('posted      :', posted)
print('hint        :', hint)
print('league      :', league.replace('\n', ' | ')[:300])
print('detail      :', detail.replace('\n', ' | ')[:200])
print('errors      :', errors[:3])
ok = ('api_key' in empty and posted.get('stored') == 5 and '2026-09' in hint and 'ขอนแก่น' in league and '-0.30' in league
      and '#2/3' in league and 'ระยอง' in league and '+0.30' in league and 'ซูชิบุฟเฟ่ต์' in detail and not errors)
print('RESULT:', 'OK' if ok else 'PROBLEMS')
sys.exit(0 if ok else 1)
