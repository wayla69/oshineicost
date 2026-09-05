# -*- coding: utf-8 -*-
"""💬 เสียงลูกค้า acceptance for the review sources: upload a Google Takeout
archive (two listings, four reviews incl. a star-only one and an owner reply)
through the tab, then see the sources card, the reply queue, the source links
and the updated status line render (Playwright, dev server on MGT_BASE)."""
import io
import json
import os
import sys
import zipfile
from playwright.sync_api import sync_playwright

BASE = os.environ.get('MGT_BASE', 'http://localhost:8021/')
OUT = os.environ.get('VERIFY_OUT', os.path.join(os.path.dirname(__file__), '..', '.tmp_takeout.zip'))


def _rv(rid, stars, comment, created, reply=None):
    d = {"reviewId": rid, "reviewer": {"displayName": "Somchai"}, "starRating": stars, "createTime": created, "updateTime": created}
    if comment is not None:
        d["comment"] = comment
    if reply:
        d["reviewReply"] = {"comment": reply, "updateTime": created}
    return d


buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("Takeout/Google Business Profile/account-1/location-9/data.json",
               json.dumps({"name": "locations/9", "title": "Oshinei Buffet ขอนแก่น", "metadata": {"placeId": "PID9"}}))
    z.writestr("Takeout/Google Business Profile/account-1/location-9/reviews.json", json.dumps({"reviews": [
        _rv("v1", "FIVE", "(Translated by Google) Great\n\n(Original) แซลมอนสดมาก บริการดี", "2026-09-01T05:00:00Z", reply="ขอบคุณค่ะ"),
        _rv("v2", "ONE", "รอนานมาก พนักงานไม่สนใจ ทุเรียนหมด", "2026-09-02T05:00:00Z"),
        _rv("v3", "FOUR", None, "2026-09-03T05:00:00Z")]}))
    z.writestr("Takeout/Google Business Profile/account-1/location-10/data.json",
               json.dumps({"name": "locations/10", "title": "Oshinei ระยอง"}))
    z.writestr("Takeout/Google Business Profile/account-1/location-10/reviews.json", json.dumps({"reviews": [
        _rv("v4", "TWO", "ของหมดตั้งแต่ทุ่มนึง", "2026-09-03T06:00:00Z")]}))
with open(OUT, "wb") as f:
    f.write(buf.getvalue())

errors = []
with sync_playwright() as pw:
    b = pw.chromium.launch(channel='chrome', headless=True)
    pg = b.new_page(viewport={'width': 1400, 'height': 1000})
    pg.on('pageerror', lambda e: errors.append(str(e)))
    pg.goto(BASE, wait_until='networkidle', timeout=90000)
    pg.wait_for_function("typeof switchTab === 'function' && typeof TABS === 'object' && TABS.feedback", timeout=30000)
    pg.wait_for_function("document.querySelectorAll('#tabsRoot > div[id^=\"tab-\"]').length >= 49", timeout=30000)
    pg.evaluate("() => { document.body.classList.add('force-desktop'); switchTab('feedback'); }")
    pg.wait_for_timeout(1500)
    before = pg.evaluate("() => document.getElementById('fbSources').innerText")
    pg.set_input_files('#fbFile', OUT)
    pg.evaluate("() => uploadFeedback()")
    pg.wait_for_function("document.getElementById('fbUploadStatus').textContent.includes('นำเข้า') || document.getElementById('fbUploadStatus').textContent.includes('ไม่สำเร็จ')", timeout=60000)
    status = pg.evaluate("() => document.getElementById('fbUploadStatus').textContent")
    pg.wait_for_timeout(2000)
    sources = pg.evaluate("() => document.getElementById('fbSources').innerText")
    st = pg.evaluate("() => document.getElementById('fbStatus').textContent")
    kpis = pg.evaluate("() => document.getElementById('fbKpis').innerText")
    neg = pg.evaluate("() => document.getElementById('fbNeg').innerText")
    links = pg.evaluate("() => document.querySelectorAll('#fbItems a[href*=\"search.google.com\"]').length")
    replies = pg.evaluate("() => document.getElementById('fbItems').innerText")
    pg.screenshot(path=os.path.join(os.path.dirname(OUT), '.tmp_feedback_sources.png'), full_page=False)
    b.close()
print('sources before:', before.replace('\n', ' | ')[:120])
print('upload status :', status)
print('status line   :', st)
print('sources after :', sources.replace('\n', ' | ')[:300])
print('kpis          :', kpis.replace('\n', ' | ')[:160])
print('negatives     :', neg.replace('\n', ' | ')[:200])
print('google links  :', links)
print('page errors   :', errors[:2])
# 3 links: the ขอนแก่น listing carries a placeId, the ระยอง one does not
ok = ('นำเข้า 4' in status and 'Google' in sources and 'รอสาขาตอบ' in st and links >= 3
      and 'ขอบคุณค่ะ' in replies and 'ให้ดาวอย่างเดียว' in replies and not errors)
print('RESULT:', 'OK' if ok else 'PROBLEMS')
sys.exit(0 if ok else 1)
