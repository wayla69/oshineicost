# -*- coding: utf-8 -*-
"""PR-M3 / PR-M4 — check the branch's pages in a real browser, on a phone and on a desktop: the daily pages
(ตัดเบิก · สั่งสินค้า, M3) and รับสินค้า · เคลมภายหลัง · ผลต่าง · ตรวจนับสิ้นเดือน (M4).

    py -3.14 scripts\\verify_branch_phone.py --base http://127.0.0.1:8058 [--shots DIR] [--password PW] [--db COPY.db]

Start a scratch server on a COPY of a database first (it never touches the live app), for example from backend/:
    OSHINEI_DB=<copy of a DB> OSHINEI_NO_SWAP=1 OSHINEI_AUTH=1 OSHINEI_AUTH_SECRET=x OSHINEI_SEED_TOKEN=local-test-token \\
        py -3.12 -m uvicorn main:app --port 8058
with the account branch1 on the default password (auth.DEFAULT_PASSWORD, read from backend/auth.py unless --password).
The script writes to that copy: it confirms yesterday's slip when there is none (so there is a "เมื่อวาน" hint) and types
into today's draft.

Phone (390x844):
  * ตัดเบิก: the first input is on screen without scrolling (day strip above it), at least 6 rows fit one screen when the
    page is scrolled, the progress line has no "/243", the zone chips show a count of filled items and no "x/y",
    every box has its unit under it, an empty box hints at the previous slip, the recent items come first in a zone,
    a typed value is saved by itself and the status reads "บันทึกร่างแล้ว hh:mm" at 14 px, a negative or non-numeric
    value turns red with a note under the row and sends NO request, a waste above 0 opens a 16 px reason select, the
    confirm dialog counts the items left empty ("ไม่ได้กรอก");
  * สั่งสินค้า: one-line rounds with a state chip, the buffer card folded, a row with "แนะนำ" · "คงเหลือ" · the order box with
    its unit and a "ดูที่มา" that opens the formula, the customer-count helper as a bottom sheet.
  * รับสินค้า (M4, needs --db: the checks make their own order sent by the warehouse in that scratch copy): the form opens with one
    question and two 52 px buttons; รับครบ = one line "รับครบ N รายการ" + the acknowledgement (a link opens the terms) + the save
    button in the sticky bar with the reason it is off written under it, ONE call to /receive with every line at the quantity sent
    (the taps are counted); มีรายการไม่ครบ = a search, only the picked line expands (44 px box, the gap, the reason, the claim box),
    a word in the box turns red with its note, one call, one receive-time claim;
  * เคลมภายหลัง: the photo line once at the top, nothing listed per line until picked, a picked item opens (44 px quantity, reason,
    a note box for อื่น ๆ), every refusal is written under its line, the claim list is two lines without ids;
  * ผลต่าง: tabs รายงวด · รายวัน · สิ้นเดือน, the head is three numbers and ONE action chip, only the high-value lines have a form,
    the rest sit folded under "รายการอื่น", quantities in count units, an empty save shows its error under the line, a reason
    saved on one line and on two ticked lines at once, one card per day with a 3-column item list, the month-end tab in count units;
  * ตรวจนับ: the zone chip gets a ✓ when every required item is counted, the submit is refused with only a "กลับไปนับ" button
    (no request leaves) and the server refuses the same way; the 6th strip entry shows on days 1–3 (Bangkok clock) only.
Desktop (1440): the same pages still list, save and ask.
PR-M7 (phone, runs last because it confirms today's slip): เบิกเหมือนเมื่อวาน fills the empty boxes from the previous slip and leaves a
typed box alone, its status names the slip and the count, the autosave then runs; สั่งตามยอดแนะนำทั้งหมด sets every box to the live
suggestion (a confirm names the edited rows first); เบิกเพิ่ม on today's confirmed slip: empty boxes, a note, บันทึกการเบิกเพิ่ม + ยกเลิก,
the confirm dialog, the line grows by the amount typed, the log ends with 'append', yesterday's slip is refused; the วันนี้เหลือทำอะไร
strip has three lines (dot, status, link); the install card (fake beforeinstallprompt, an iPhone Safari user agent, × remembered);
ตัวอักษรใหญ่ (x1.15, remembered, at least 5 rows per screen, the sticky row still above the strip).
"""
import argparse
import datetime as dt
import os
import re
import sys

from playwright.sync_api import sync_playwright

ISSUE_ROW = "#brnIssList .brn-row"
# no child of a row's name column is wider than its box (the meta line wraps, it is never cut with an ellipsis), and where a
# row has a system-stock span it takes height (it is on screen, not squeezed away)
CLIP_JS = """(root) => { const clipped = []; let n = 0;
  document.querySelectorAll(root + ' .brn-namecol').forEach(c => { n++;
    c.querySelectorAll('*').forEach(e => { if (e.scrollWidth > e.clientWidth + 1 && e.clientWidth > 0 && getComputedStyle(e).display !== 'inline') clipped.push((e.className || e.tagName) + ' ' + e.scrollWidth + '>' + e.clientWidth + ' · ' + e.innerText.slice(0, 40)); });
    c.querySelectorAll('.brn-sys').forEach(s => { if (!s.getClientRects().length || s.getBoundingClientRect().height < 8) clipped.push('hidden stock ' + s.innerText); });
    const sub = c.querySelector('.brn-sub'); if (sub && (sub.scrollHeight > sub.clientHeight + 1 || getComputedStyle(sub).textOverflow === 'ellipsis')) clipped.push('sub cut ' + sub.innerText.slice(0, 40)); });
  return { n, clipped }; }"""


def fresh_processed_order(db_path):
    """Scratch copies only: make exactly ONE order that the warehouse has sent (status processed) for the receive checks —
    every older processed order of the branch is marked received, and a new one is cloned (lines at the quantity sent)."""
    import sqlite3
    con = sqlite3.connect(db_path, timeout=60)
    try:
        bid = con.execute("SELECT branch_id FROM branches WHERE name='อุดรธานี'").fetchone()[0]
        con.execute("UPDATE bc_order SET status='received', received_at=COALESCE(received_at, '2026-10-09T03:00:00+00:00') WHERE branch_id=? AND status='processed'", (bid,))
        src = con.execute("SELECT order_id FROM bc_order WHERE branch_id=? AND round IN ('1','2') AND status IN ('received','cancelled','processed')"
                          " ORDER BY (SELECT COUNT(*) FROM bc_order_line l WHERE l.order_id=bc_order.order_id AND l.processed_units>0) DESC, order_id LIMIT 1", (bid,)).fetchone()[0]
        cols = [r[1] for r in con.execute("PRAGMA table_info(bc_order)") if r[1] != "order_id"]
        sel = ", ".join("'processed'" if c == "status" else "NULL" if c in ("received_at", "ack_at", "ack_by", "accounting_keyed_at", "accounting_keyed_by") else c for c in cols)
        con.execute(f"INSERT INTO bc_order({', '.join(cols)}) SELECT {sel} FROM bc_order WHERE order_id=?", (src,))
        new = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute("INSERT INTO bc_order_line(order_id, ingredient_code, suggested_units, confirmed_units, processed_units, received_units, order_unit, reason_code, note, ss_reason, ss_note)"
                    " SELECT ?, ingredient_code, suggested_units, confirmed_units, COALESCE(processed_units, confirmed_units), NULL, order_unit, reason_code, note, NULL, NULL"
                    " FROM bc_order_line WHERE order_id=?", (new, src))
        con.commit()
        return new
    finally:
        con.close()


DLG_JS = """() => { const m = document.querySelector('[data-el=msg]'); if (!m) return null;
  return { title: document.querySelector('[data-el=title]').textContent, msg: m.textContent,
           ok: m.closest('form').querySelector('[type=submit]').textContent, cancel: document.querySelector('[data-act=cancel]').textContent }; }"""


def m7_checks(a, b, session, check, shot, nums):
    """PR-M7 — the six usability items, on the branch phone (390x844): เบิกเหมือนเมื่อวาน · สั่งตามยอดแนะนำทั้งหมด · เบิกเพิ่ม ·
    the วันนี้เหลือทำอะไร strip · the install card and ตัวอักษรใหญ่. Runs LAST: it confirms today's slip of the scratch copy."""
    ctx, page, errors = session(b, True)
    posts = []
    page.on("request", lambda rq: posts.append((rq.method, rq.url)) if rq.method == "POST" else None)
    n_posts = lambda frag: len([1 for m_, u in posts if frag in u])  # noqa: E731
    today = page.evaluate("new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Bangkok' })")
    sheet = ctx.request.get(f"{a.base}/api/bom-center/issues/sheet?date={today}").json()
    already = sheet["status"]["status"] in ("confirmed", "late")

    def dlg():
        page.wait_for_selector("[data-el=msg]", timeout=8000)
        return page.evaluate(DLG_JS)

    # ── 1 · เบิกเหมือนเมื่อวาน ──
    print("== phone · M7 · เบิกเหมือนเมื่อวาน")
    page.evaluate("supplyGo({tab:'branchstock', pane:'issue'})")
    page.wait_for_selector(ISSUE_ROW, timeout=60000)
    page.wait_for_timeout(1200)
    if already:
        print("note: today's slip is already confirmed in this copy — the fill check is skipped (run on a fresh copy)")
    else:
        btn = page.query_selector("#brnIssFillBtn")
        check(btn is not None and btn.is_visible() and "เบิกเหมือนเมื่อวาน" in btn.inner_text(), "the head card has the button 'เบิกเหมือนเมื่อวาน' while the slip is a draft")
        left = page.evaluate("""() => _brnIss.zones.flatMap(z => z.items).filter(i => { const c = _brnIssQty[i.code] || { used: '', waste: '' };
            return (i.prev_used > 0 && c.used === '') || (i.prev_waste > 0 && c.waste === ''); }).length""")
        if btn and not left:
            print("note: every box that has a hint is already filled in this copy (a second run) — the fill check is skipped")
        if btn and left:
            exp = page.evaluate("""() => { const q = _brnIssQty; let n = 0; const first = []; _brnIss.zones.forEach(z => z.items.forEach(i => {
                const c = q[i.code] || { used: '', waste: '' };
                if ((i.prev_used > 0 && c.used === '') || (i.prev_waste > 0 && c.waste === '')) n++;
                if (i.prev_used > 0) first.push(i.code); })); return { n, first }; }""")
            typed_code = exp["first"][0]
            typed = page.query_selector(f"#brnIssU_{typed_code}")
            typed.scroll_into_view_if_needed()
            typed.fill("9")
            page.wait_for_timeout(200)
            exp = page.evaluate("""() => { const q = _brnIssQty; let n = 0; _brnIss.zones.forEach(z => z.items.forEach(i => {
                const c = q[i.code] || { used: '', waste: '' };
                if ((i.prev_used > 0 && c.used === '') || (i.prev_waste > 0 && c.waste === '')) n++; })); return n; }""")
            before = n_posts("/issues/save")
            page.evaluate("window.scrollTo(0,0); document.querySelector('main.content-area').scrollTop = 0")
            page.click("#brnIssFillBtn")
            page.wait_for_timeout(300)
            st = page.inner_text("#brnIssStatus")
            check(re.match(r"^เติมจากใบวันที่ \d+/\d+ แล้ว \d+ รายการ · แก้เฉพาะรายการที่ต่าง$", st) is not None and f"แล้ว {exp} รายการ" in st,
                  f"the status reads '{st}' (expected {exp} items)")
            kept = page.evaluate(f"document.getElementById('brnIssU_{typed_code}').value")
            check(kept == "9", f"a box the person already typed is left alone (still {kept!r})")
            filled = page.evaluate("""(tc) => _brnIss.zones.flatMap(z => z.items).filter(i => i.prev_used > 0 && i.code !== tc).slice(0, 3)
                .map(i => [i.prev_used, (_brnIssQty[i.code] || {}).used])""", typed_code)
            check(all(float(u or 0) == float(p_) for p_, u in filled), f"the empty boxes took yesterday's quantities: {filled}")
            shot(page, "m7_fill_status.png")
            page.wait_for_function("/บันทึกร่างแล้ว \\d\\d:\\d\\d/.test(document.getElementById('brnIssStatus').innerText)", timeout=9000)
            st2 = page.inner_text("#brnIssStatus")
            check(n_posts("/issues/save") == before + 1 and "เติมจากใบวันที่" in st2, f"the normal autosave then runs once and keeps the note: '{st2}'")
            # a second tap changes nothing (no empty box is left that has a hint)
            page.click("#brnIssFillBtn")
            page.wait_for_timeout(200)
            check(page.inner_text("#brnIssStatus").startswith("ไม่มีช่องว่างที่ต้องเติม") or "แล้ว 0 รายการ" not in page.inner_text("#brnIssStatus"),
                  "a second tap fills nothing more: " + page.inner_text("#brnIssStatus"))
            fill_w = page.evaluate("document.querySelector('#brn-pane-issue .brn-sticky-bottom').getBoundingClientRect().width")
            check(fill_w > 300, "the sticky action row is unchanged in width")

    # ── 2 · สั่งตามยอดแนะนำทั้งหมด ──
    print("== phone · M7 · สั่งตามยอดแนะนำทั้งหมด")
    page.evaluate("supplyGo({tab:'branchstock', pane:'order'})")
    page.wait_for_selector("#brnOrdList .brn-row", timeout=90000)
    page.wait_for_timeout(1500)
    ob = page.query_selector("#brnOrdFillBtn")
    editable = page.evaluate("!_brnPlan.order || ['suggested','confirmed'].includes(_brnPlan.order.status)")
    if not editable or ob is None:
        print("note: the order of this round is already sent — the fill-all check is skipped")
    else:
        check(ob.is_visible() and ob.inner_text().strip() == "สั่งตามยอดแนะนำทั้งหมด", "the order page has the button 'สั่งตามยอดแนะนำทั้งหมด' for an editable draft")
        code = page.evaluate("_brnPlan.rows.find(r => r.order_units > 0).code")
        box = page.query_selector(f"#brnOrd_{code}")
        box.scroll_into_view_if_needed()
        box.fill("7")
        page.wait_for_timeout(100)
        page.evaluate("document.querySelector('main.content-area').scrollTop = 0; window.scrollTo(0,0)")
        # nothing is edited elsewhere in this draft, so the dialog counts exactly the rows that differ
        n_diff = page.evaluate("_brnPlan.rows.filter(r => Number(_brnOrdQty[r.code] || 0) !== Number(r.order_units)).length")
        page.click("#brnOrdFillBtn")
        d = dlg()
        check(d["title"] == "ใส่ยอดแนะนำทั้งหมด" and d["ok"] == "ใส่ยอดแนะนำ" and d["msg"] == f"ยืนยันการใส่ยอดแนะนำทับ {n_diff} รายการที่แก้ไว้?",
              f"an edited row asks first: '{d['title']}' · '{d['msg']}' · [{d['ok']}]")
        shot(page, "m7_fill_all_confirm.png")
        page.click("form:has([data-el=msg]) [type=submit]")
        page.wait_for_timeout(500)
        st = page.inner_text("#brnOrdStatus")
        check(st == "ใส่ยอดแนะนำให้ทุกรายการแล้ว · แก้เฉพาะรายการที่ต่าง", f"the status reads '{st}'")
        same = page.evaluate("_brnPlan.rows.every(r => Number(_brnOrdQty[r.code]) === Number(r.order_units))")
        shown = page.evaluate("[...document.querySelectorAll('#brnOrdList .brn-qty')].every(i => Number(i.value) === Number(_brnPlan.rows.find(r => i.id === 'brnOrd_' + r.code).order_units))")
        dev = page.evaluate("[...document.querySelectorAll('#brnOrdList .brn-qty')].filter(i => i.style.borderColor.includes('amber')).length")
        check(same and shown and dev == 0, "every row's box now holds the live suggestion and none is marked as deviating")
        shot(page, "m7_fill_all_done.png")
        sv = n_posts("/order/save")
        page.wait_for_function("/บันทึกร่างแล้ว/.test(document.getElementById('brnOrdStatus').innerText)", timeout=9000)
        check(n_posts("/order/save") == sv + 1, "the draft saved itself once")
        page.click("#brnOrdFillBtn")
        page.wait_for_timeout(400)
        check(page.query_selector("[data-el=msg]") is None, "with nothing edited the button asks nothing")

    # ── 3 · เบิกเพิ่ม ──
    print("== phone · M7 · เบิกเพิ่ม")
    page.evaluate("supplyGo({tab:'branchstock', pane:'issue'})")
    page.wait_for_selector(ISSUE_ROW, timeout=60000)
    page.wait_for_timeout(1000)
    if not already:
        if page.evaluate("!_brnIssAny(_brnIss.zones[0].items[0].code)"):
            page.fill("#brnIssList .brn-qty >> nth=0", "4")
        page.evaluate("brnIssSave(false)")
        page.wait_for_timeout(600)
        page.evaluate("void brnIssSave(true)")                  # it waits for the dialog: do not await it here
        d = dlg()
        check(d["title"] == "ยืนยันใบตัดเบิก", "the slip is confirmed (the normal dialog)")
        page.click("form:has([data-el=msg]) [type=submit]")
        page.wait_for_function("_brnIss && _brnIss.status.status !== 'draft' && !_brnIss.editable", timeout=15000)
        page.wait_for_timeout(600)
    sheet = ctx.request.get(f"{a.base}/api/bom-center/issues/sheet?date={today}").json()
    check(sheet["can_append"] is True, "the server offers เบิกเพิ่ม on today's confirmed slip")
    ap = page.query_selector("#brnIssActions button:has-text('เบิกเพิ่ม')")
    check(ap is not None and ap.is_visible(), "the sticky row of a confirmed slip has the button 'เบิกเพิ่ม'")
    bx = ap.bounding_box() if ap else None
    check(bx is not None and bx["x"] + bx["width"] <= 390, f"the button is inside the 390 px screen ({bx})")
    shot(page, "m7_append_button.png")
    lines0 = {l["code"]: l for l in sheet["lines"]}
    code = next(iter(lines0))
    base_used = lines0[code]["qty_used"]
    ap.click()
    page.wait_for_timeout(500)
    banner = page.inner_text("#brnIssBanner")
    ph = page.evaluate("[...document.querySelectorAll('#brnIssList .brn-qty')].slice(0, 6).map(i => [i.placeholder, i.value, i.disabled])")
    check("รายการที่กรอกจะถูกบวกเพิ่มในใบของวันนี้ที่ยืนยันแล้ว" in banner, "the head note says the entries are added to the confirmed slip")
    check(all(p_[0] == "เพิ่ม" and p_[1] == "" and p_[2] is False for p_ in ph), f"all boxes are empty and open, placeholder 'เพิ่ม': {ph[:2]}")
    acts = page.evaluate("[...document.querySelectorAll('#brnIssActions button')].map(b => b.innerText.trim())")
    check(acts == ["บันทึกการเบิกเพิ่ม", "ยกเลิก"], f"the sticky row is {acts}")
    shot(page, "m7_append_mode.png")
    page.click("#brnIssActions button:has-text('ยกเลิก')")
    page.wait_for_timeout(300)
    check(page.evaluate("!_brnIssAppend && document.querySelector('#brnIssList .brn-qty').disabled"), "ยกเลิก leaves the append mode (boxes locked again)")
    page.click("#brnIssActions button:has-text('เบิกเพิ่ม')")
    page.wait_for_timeout(300)
    n_app = n_posts("/issues/append")
    page.click("#brnIssActions button:has-text('บันทึกการเบิกเพิ่ม')")
    page.wait_for_timeout(300)
    check(n_posts("/issues/append") == n_app and "ใส่จำนวนที่เบิกเพิ่มอย่างน้อย 1 รายการ" in page.inner_text("#brnIssStatus"), "saving with nothing typed sends no request")
    if not page.query_selector(f"#brnIssU_{code}"):
        # the item sits in another zone: open that zone first
        page.evaluate(f"brnIssSetZone(_brnIss.zones.find(z => z.items.some(i => i.code === {code!r})).zone)")
        page.wait_for_timeout(200)
    page.fill(f"#brnIssU_{code}", "2")
    page.click("#brnIssActions button:has-text('บันทึกการเบิกเพิ่ม')")
    d = dlg()
    dm = f"{int(today[8:10])}/{int(today[5:7])}"
    check(d["title"] == "ยืนยันการเบิกเพิ่ม" and d["ok"] == "บันทึก" and d["msg"].startswith(f"บันทึกการเบิกเพิ่ม 1 รายการ ในใบตัดเบิก {dm}?"),
          f"the dialog: '{d['title']}' · '{d['msg'].splitlines()[0]}' · [{d['ok']}]")
    shot(page, "m7_append_confirm.png")
    page.click("form:has([data-el=msg]) [type=submit]")
    page.wait_for_function("/เบิกเพิ่มแล้ว 1 รายการ/.test(document.getElementById('brnIssStatus').innerText)", timeout=15000)
    check(page.inner_text("#brnIssStatus") == "✓ เบิกเพิ่มแล้ว 1 รายการ", "status: " + page.inner_text("#brnIssStatus"))
    page.wait_for_timeout(500)
    sheet2 = ctx.request.get(f"{a.base}/api/bom-center/issues/sheet?date={today}").json()
    l2 = {l["code"]: l for l in sheet2["lines"]}[code]
    check(abs(l2["qty_used"] - (base_used + 2)) < 1e-9, f"the line grew from {base_used} to {l2['qty_used']} (+2)")
    check(page.evaluate("document.getElementById('brnIssHead').innerText").count("เบิกเพิ่ม 1 ครั้ง") == 1 and sheet2["status"]["appends"] == 1 and sheet2["status"]["status"] in ("confirmed", "late"),
          "the slip stays confirmed and its head says 'เบิกเพิ่ม 1 ครั้ง'")
    check(page.evaluate("!_brnIssAppend && _brnIss.status.confirm_count === " + str(sheet["status"]["confirm_count"])), "the page reloaded the locked slip; nothing was re-confirmed")
    shot(page, "m7_append_done.png")
    det = ctx.request.get(f"{a.base}/api/bom-center/issues/{sheet2['slip']['slip_id']}").json()
    check([x["action"] for x in det["log"]][-1] == "append" and det["appends"] == 1, f"the log ends with an 'append' row: {[x['action'] for x in det['log']]}")
    # the 400 for a day that is not today
    y = (dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat()
    r = ctx.request.post(f"{a.base}/api/bom-center/issues/append", data={"date": y, "lines": [{"code": code, "qty_used": 1}]})
    check(r.status == 400 and "เบิกเพิ่มได้เฉพาะใบตัดเบิกของวันนี้ที่ยืนยันแล้ว" in r.text(), f"yesterday's slip is refused: {r.status}")

    # ── 4 · วันนี้เหลือทำอะไร ──
    print("== phone · M7 · วันนี้เหลือทำอะไร")
    page.evaluate("supplyGo({tab:'supplyhome'})")
    page.wait_for_selector(".shm-today-row", timeout=60000)
    page.wait_for_timeout(1000)
    rows = page.evaluate("[...document.querySelectorAll('#shmToday .shm-today-row')].map(r => ({ t: r.innerText.replace(/\\s+/g, ' ').trim(), link: !!r.querySelector('a'), dot: r.querySelector('span').innerText.trim(), h: r.getBoundingClientRect().height }))")
    print("     ", [r["t"] for r in rows])
    check(len(rows) == 3, f"the strip has three lines ({len(rows)})")
    # the dot is ● while an earlier day is still open (a backlog the copy may hold), ✓ when nothing is left
    check(len(rows) == 3 and re.match(r"^[✓●] ตัดเบิก · ยืนยันแล้ว \d\d:\d\d", rows[0]["t"]) is not None and "เบิกเพิ่ม 1 ครั้ง" in rows[0]["t"], f"line 1 reads '{rows[0]['t'] if rows else ''}'")
    check(len(rows) == 3 and rows[1]["t"].split(" ", 1)[1].startswith("สั่งสินค้า · ") and rows[2]["t"].split(" ", 1)[1].startswith("รับสินค้า · "), "lines 2 and 3 are สั่งสินค้า and รับสินค้า")
    check(all(r["link"] and r["t"].endswith("ไปที่งาน →") and r["dot"] in ("✓", "●") and r["h"] >= 43 for r in rows), "each line has a ✓/● dot, the link 'ไปที่งาน →' and is 44 px high")
    pos = page.evaluate("document.getElementById('shmToday').getBoundingClientRect().top < document.getElementById('shmMineCard').getBoundingClientRect().top")
    check(pos, "the strip sits above the task cards")
    ver = page.evaluate("document.getElementById('shmNote').innerText")
    check("สูตร" not in ver, f"no version line for the branch: '{ver}'")
    shot(page, "m7_today_strip.png")
    page.click("#shmToday .shm-today-row[data-k=recv] a")
    page.wait_for_function("document.getElementById('brn-pane-recv').style.display !== 'none'", timeout=15000)
    check(True, "the link of a line opens its page (รับสินค้า)")

    # ── 5a · the install card ──
    print("== phone · M7 · ติดตั้งเป็นแอป")
    page.evaluate("supplyGo({tab:'supplyhome'})")
    page.wait_for_selector(".shm-today-row", timeout=30000)
    page.evaluate("localStorage.removeItem('oshinei_brn_install_dismiss')")
    check(page.evaluate("document.getElementById('shmInstall').style.display === 'none'"), "no install card when the browser offers nothing (desktop Chrome here)")
    page.evaluate("""() => { const e = new Event('beforeinstallprompt', { cancelable: true }); e.prompt = () => { window.__prompted = true; }; e.userChoice = Promise.resolve({ outcome: 'dismissed' }); window.dispatchEvent(e); }""")
    page.wait_for_timeout(300)
    vis = page.evaluate("({ shown: document.getElementById('shmInstall').style.display !== 'none', title: document.querySelector('#shmInstall .font-semibold').innerText, btn: getComputedStyle(document.getElementById('shmInstallBtn')).display !== 'none' })")
    check(vis["shown"] and vis["title"] == "ติดตั้งเป็นแอปบนหน้าจอโทรศัพท์" and vis["btn"], f"the card appears with the install button: {vis}")
    shot(page, "m7_install_card.png")
    page.click("#shmInstallBtn")
    page.wait_for_timeout(300)
    check(page.evaluate("window.__prompted === true"), "the button calls the browser's install prompt")
    page.evaluate("""() => { const e = new Event('beforeinstallprompt', { cancelable: true }); e.prompt = () => {}; e.userChoice = Promise.resolve({ outcome: 'dismissed' }); window.dispatchEvent(e); }""")
    page.wait_for_timeout(200)
    page.click("#shmInstall button[aria-label=ปิด]")
    page.wait_for_timeout(200)
    check(page.evaluate("document.getElementById('shmInstall').style.display === 'none' && Number(localStorage.getItem('oshinei_brn_install_dismiss')) > Date.now()"), "× hides the card and remembers it on this device")
    page.reload(wait_until="networkidle")
    page.wait_for_function("typeof supplyGo === 'function' && window.AUTH && window.AUTH.role", timeout=60000)
    page.evaluate("supplyGo({tab:'supplyhome'})")
    page.wait_for_selector(".shm-today-row", timeout=30000)
    page.evaluate("""() => { const e = new Event('beforeinstallprompt', { cancelable: true }); e.prompt = () => {}; e.userChoice = Promise.resolve({ outcome: 'dismissed' }); window.dispatchEvent(e); }""")
    page.wait_for_timeout(300)
    check(page.evaluate("document.getElementById('shmInstall').style.display === 'none'"), "after a reload the dismissed card stays away")
    page.evaluate("localStorage.removeItem('oshinei_brn_install_dismiss')")
    ctx.close()
    # iPhone Safari: no event, a one-line instruction instead
    c3 = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="th-TH",
                       user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1")
    c3.add_init_script("try{localStorage.setItem('oshinei_upload_token','local-test-token')}catch(e){}")
    c3.request.post(a.base + "/api/auth/login", data={"username": a.user, "password": a.password})
    p3 = c3.new_page()
    p3.goto(a.base + "/", wait_until="networkidle", timeout=120000)
    p3.wait_for_function("typeof supplyGo === 'function' && window.AUTH && window.AUTH.role", timeout=60000)
    p3.evaluate("supplyGo({tab:'supplyhome'})")
    p3.wait_for_selector(".shm-today-row", timeout=30000)
    p3.wait_for_timeout(500)
    ios = p3.evaluate("({ shown: document.getElementById('shmInstall').style.display !== 'none', hint: document.getElementById('shmInstallHint').innerText, btn: getComputedStyle(document.getElementById('shmInstallBtn')).display })")
    check(ios["shown"] and ios["btn"] == "none" and "เพิ่มไปยังหน้าจอโฮม" in ios["hint"], f"iPhone Safari: no button, the line reads '{ios['hint']}'")
    if a.shots:
        p3.screenshot(path=os.path.join(a.shots, "m7_install_ios.png"))
    c3.close()

    # ── 5b · ตัวอักษรใหญ่ ──
    print("== phone · M7 · ตัวอักษรใหญ่")
    ctx, page, errors2 = session(b, True)
    errors += errors2
    page.evaluate("localStorage.removeItem('oshinei_brn_big')")
    page.evaluate("supplyGo({tab:'branchstock', pane:'issue'})")
    page.wait_for_selector(ISSUE_ROW, timeout=60000)
    page.wait_for_timeout(1000)
    big = page.query_selector("#brnBigBtn")
    bb = big.bounding_box() if big else None
    check(big is not None and big.is_visible() and bb["x"] + bb["width"] <= 390 and "ตัวอักษรใหญ่" in big.inner_text(), f"the cluster line has the toggle 'ตัวอักษรใหญ่' inside the screen ({bb})")
    f0 = page.evaluate("getComputedStyle(document.documentElement).fontSize")
    shot(page, "m7_font_normal.png")

    def rows_per_screen():
        page.evaluate("document.querySelector('main.content-area').scrollTop = 700")
        page.wait_for_timeout(400)
        r = page.evaluate("""() => { const top = document.querySelector('.brn-zonebar').getBoundingClientRect().bottom;
          const bot = document.querySelector('#brn-pane-issue .brn-sticky-bottom').getBoundingClientRect().top;
          return [...document.querySelectorAll('#brnIssList .brn-row')].filter(r => { const x = r.getBoundingClientRect(); return x.top >= top - 1 && x.bottom <= bot + 1; }).length; }""")
        page.evaluate("document.querySelector('main.content-area').scrollTop = 0")
        return r
    n0 = rows_per_screen()
    page.click("#brnBigBtn")
    page.wait_for_timeout(400)
    st = page.evaluate("({ cls: document.documentElement.classList.contains('brn-big'), fs: getComputedStyle(document.documentElement).fontSize, pressed: document.getElementById('brnBigBtn').getAttribute('aria-pressed'), key: localStorage.getItem('oshinei_brn_big'), name: parseFloat(getComputedStyle(document.querySelector('#brnIssList .brn-name')).fontSize) })")
    check(st["cls"] and abs(float(st["fs"][:-2]) / float(f0[:-2]) - 1.15) < 0.01 and st["pressed"] == "true" and st["key"] == "1" and st["name"] >= 14.9,
          f"the toggle sets x1.15 ({f0} -> {st['fs']}, the item name {st['name']}px) and remembers it ({st['key']})")
    n1 = rows_per_screen()
    nums["rows_per_screen_big"] = n1
    check(n1 >= 5, f"{n1} rows per screen with the big font (normal: {n0}) at 390x844")
    shot(page, "m7_font_big.png")
    page.evaluate("document.querySelector('main.content-area').scrollTop = 700")
    page.wait_for_timeout(300)
    shot(page, "m7_font_big_scrolled.png")
    stick = page.evaluate("""() => { const s = document.querySelector('#brn-pane-issue .brn-sticky-bottom').getBoundingClientRect(); const nav = document.getElementById('supplyNav').getBoundingClientRect();
      return { bottom: s.bottom, navTop: nav.top, h: s.height, w: s.width }; }""")
    check(stick["bottom"] <= stick["navTop"] + 1 and stick["w"] <= 390, f"the sticky row still sits above the bottom strip ({stick})")
    page.evaluate("supplyGo({tab:'branchstock', pane:'order'})")
    page.wait_for_selector("#brnOrdList .brn-row", timeout=60000)
    page.wait_for_timeout(600)
    shot(page, "m7_font_big_order.png")
    page.reload(wait_until="networkidle")
    page.wait_for_function("typeof supplyGo === 'function' && window.AUTH && window.AUTH.role", timeout=60000)
    page.wait_for_timeout(500)
    check(page.evaluate("document.documentElement.classList.contains('brn-big')"), "after a reload the big font is still on (remembered on this device)")
    page.evaluate("supplyGo({tab:'branchstock', pane:'issue'})")
    page.wait_for_selector(ISSUE_ROW, timeout=60000)
    page.click("#brnBigBtn")
    page.wait_for_timeout(300)
    check(page.evaluate("!document.documentElement.classList.contains('brn-big') && localStorage.getItem('oshinei_brn_big') === '0'"), "a second tap turns it off")
    check(not errors, "M7: no page error" + (f" — {errors[:2]}" if errors else ""))
    ctx.close()

    # ── 6 · the centre's view of the slip: ใบตัดเบิกสาขา shows "เบิกเพิ่ม N ครั้ง" and the log line (Sales Support, desktop) ──
    print("== desktop · M7 · ใบตัดเบิกสาขา")
    c4 = b.new_context(viewport={"width": 1440, "height": 900}, locale="th-TH")
    c4.add_init_script("try{localStorage.setItem('oshinei_upload_token','local-test-token')}catch(e){}")
    if c4.request.post(a.base + "/api/auth/login", data={"username": "salessupport1", "password": a.password}).ok:
        p4 = c4.new_page()
        p4.goto(a.base + "/", wait_until="networkidle", timeout=120000)
        p4.wait_for_function("typeof supplyGo === 'function' && window.AUTH && window.AUTH.role", timeout=60000)
        sid = ctx_slip_id(a, b, today)
        p4.evaluate("supplyGo({tab:'orderhub', pane:'issues'})")
        p4.wait_for_selector("#ohbIsDetail", state="attached", timeout=30000)
        p4.evaluate(f"ohbIsOpen({sid})")
        p4.wait_for_function("document.getElementById('ohbIsDetail').innerText.includes('ประวัติของใบ')", timeout=30000)
        txt = p4.inner_text("#ohbIsDetail")
        check("เบิกเพิ่ม 1 ครั้ง" in txt and re.search(r"เบิกเพิ่ม · .+ · \d+ รายการ · เบิกใช้ \d+ รายการ", txt) is not None,
              "the slip detail of ใบตัดเบิกสาขา shows 'เบิกเพิ่ม 1 ครั้ง' and the log line: " + re.sub(r"\s+", " ", txt)[-150:])
        if a.shots:
            p4.screenshot(path=os.path.join(a.shots, "m7_centre_slip_detail.png"))
    c4.close()


def ctx_slip_id(a, b, today):
    """The id of the branch's slip of `today` (read with the branch's own login)."""
    c = b.new_context(locale="th-TH")
    c.request.post(a.base + "/api/auth/login", data={"username": a.user, "password": a.password})
    sid = c.request.get(f"{a.base}/api/bom-center/issues/sheet?date={today}").json()["status"]["slip_id"]
    c.close()
    return sid


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8058")
    ap.add_argument("--password", default=os.environ.get("OSHINEI_ROLES_PASSWORD", ""))
    ap.add_argument("--shots", default="")
    ap.add_argument("--user", default="branch1")
    ap.add_argument("--db", default="", help="the scratch DB copy the server runs on: the receive checks make their own sent orders in it")
    ap.add_argument("--only-m7", action="store_true", help="run only the PR-M7 section (it confirms today's slip of the scratch copy)")
    a = ap.parse_args(argv)
    if not a.password:
        m = re.search(r'^DEFAULT_PASSWORD\s*=\s*"([^"]+)"', open(os.path.join(os.path.dirname(__file__), "..", "backend", "auth.py"), encoding="utf-8").read(), re.M)
        a.password = m.group(1) if m else ""
    if a.shots:
        os.makedirs(a.shots, exist_ok=True)
    bad = []
    nums = {}

    def check(ok, what):
        print(("ok  " if ok else "BAD ") + what)
        if not ok:
            bad.append(what)

    def shot(page, name, **kw):
        if a.shots:
            page.screenshot(path=os.path.join(a.shots, name), **kw)

    def session(b, phone):
        ctx = b.new_context(viewport={"width": 390 if phone else 1440, "height": 844 if phone else 900},
                            is_mobile=phone, has_touch=phone, locale="th-TH")
        ctx.add_init_script("try{localStorage.setItem('oshinei_upload_token','local-test-token')}catch(e){}")
        r = ctx.request.post(a.base + "/api/auth/login", data={"username": a.user, "password": a.password})
        if not r.ok:
            raise SystemExit(f"login failed ({r.status})")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(a.base + "/", wait_until="networkidle", timeout=120000)
        page.wait_for_function("typeof supplyGo === 'function' && window.AUTH && window.AUTH.role", timeout=60000)
        page.wait_for_timeout(1000)
        return ctx, page, errors

    def seed_yesterday(ctx):
        """A confirmed slip for yesterday (2 lines) so that today's sheet has a previous slip to hint with."""
        y = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        sh = ctx.request.get(f"{a.base}/api/bom-center/issues/sheet?date={y}").json()
        if sh["status"]["status"] in ("confirmed", "late"):
            return y, False
        items = [i for z in sh["zones"] for i in z["items"] if i.get("count_unit")][:3]
        lines = [{"code": i["code"], "qty_used": 3 if k else 2, "unit": i["count_unit"]} for k, i in enumerate(items)]
        r = ctx.request.post(f"{a.base}/api/bom-center/issues/save", data={"date": y, "lines": lines, "submit": True})
        if not r.ok:
            print("note: could not confirm yesterday's slip:", r.status, r.text()[:160])
        return y, r.ok

    with sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            b = p.chromium.launch(headless=True)

        if a.only_m7:                                           # the PR-M7 section alone (it needs yesterday's slip, which it seeds itself)
            c0, _p0, _e0 = session(b, True)
            seed_yesterday(c0)
            c0.close()
            m7_checks(a, b, session, check, shot, nums)
            b.close()
            print("numbers:", nums)
            print("PASS" if not bad else f"FAIL ({len(bad)})")
            return 0 if not bad else 1

        # ══════════════ phone ══════════════
        ctx, page, errors = session(b, True)
        y, seeded = seed_yesterday(ctx)
        saves = []
        page.on("request", lambda rq: saves.append(rq.url) if "/issues/save" in rq.url and rq.method == "POST" else None)
        page.evaluate("supplyGo({tab:'branchstock', pane:'issue'})")
        page.wait_for_selector(ISSUE_ROW, timeout=60000)
        page.wait_for_timeout(1200)
        print("== phone · ตัดเบิก")
        m = page.evaluate("""() => {
          const vis = e => !!(e.offsetWidth || e.offsetHeight);
          const strip = document.getElementById('brnIssDays').getBoundingClientRect();
          const q = [...document.querySelectorAll('#brnIssList .brn-qty')].find(vis);
          const r = q.getBoundingClientRect();
          const nav = document.getElementById('supplyNav');
          return { vh: innerHeight, stripBottom: strip.bottom, inputTop: r.top, inputBottom: r.bottom, inputH: r.height,
                   navTop: nav ? nav.getBoundingClientRect().top : innerHeight,
                   progress: document.getElementById('brnIssProgress').innerText,
                   zones: [...document.querySelectorAll('#brnIssZones button')].map(b => b.innerText.trim()),
                   days: [...document.querySelectorAll('#brnIssDays button')].map(b => b.innerText.replace(/\\s+/g, ' ').trim()),
                   rows: document.querySelectorAll('#brnIssList .brn-row').length }; }""")
        nums["first_input_top"] = round(m["inputTop"])
        check(m["inputBottom"] <= m["navTop"] and m["inputTop"] >= m["stripBottom"],
              f"the first input is on screen without scrolling, below the day strip (top {m['inputTop']:.0f}px, strip ends {m['stripBottom']:.0f}px, viewport {m['vh']}px, bottom strip at {m['navTop']:.0f}px)")
        check(m["inputH"] >= 43.5, f"the input is {m['inputH']:.0f}px high (44px)")
        check(bool(re.search(r"กรอกแล้ว \d+ รายการ", m["progress"])) and not re.search(r"/\s*2\d\d|\d+\s*/\s*\d{2,}", m["progress"]),
              f"progress reads '{m['progress'].replace(chr(10), ' ')}' (no '/243')")
        check(not any(re.search(r"\d+\s*/\s*\d+", z) for z in m["zones"]), f"zone chips show no 'x/y': {m['zones']}")
        print("     day strip:", m["days"])
        check(any(d.startswith("วันนี้") for d in m["days"]) and len(m["days"]) <= 9, "the day strip: today first, then only what is left to do")
        if seeded or any("ยืนยันครบ" in d for d in m["days"]):
            check(any("ยืนยันครบ" in d for d in m["days"]), "confirmed days are folded into one 'ยืนยันครบ N วัน' chip")
        # rows: caption (unit) under each box, a hint from the previous slip, recent items first
        c = page.evaluate("""() => {
          const rows = [...document.querySelectorAll('#brnIssList .brn-row')].slice(0, 6);
          return rows.map(r => { const ins = [...r.querySelectorAll('.brn-qty')]; const caps = [...r.querySelectorAll('.brn-cap')];
            return ins.map((i, k) => ({ below: caps[k].getBoundingClientRect().top >= i.getBoundingClientRect().bottom - 1, cap: caps[k].innerText, ph: i.placeholder })); }).flat(); }""")
        check(all(x["below"] and " · " in x["cap"] and len(x["cap"].split(" · ")[1].strip()) > 0 for x in c), f"every box has its unit under it: {[x['cap'] for x in c[:4]]}")
        clip = page.evaluate(CLIP_JS, "#brnIssList")
        check(clip["n"] > 0 and not clip["clipped"], f"no text in the name column is clipped ({clip['n']} rows; the system stock 'คงเหลือ' is readable)" + (f" — {clip['clipped'][:3]}" if clip["clipped"] else ""))
        hints = page.evaluate("[...document.querySelectorAll('#brnIssList .brn-qty')].map(i => i.placeholder).filter(Boolean)")
        check(any(h.startswith("เมื่อวาน") for h in hints) or not seeded, f"an empty box hints at yesterday's slip: {hints[:3]}")
        order = page.evaluate("_brnIss.zones.map(z => z.items.map(i => i.recent ? 1 : 0))")
        check(all(z == sorted(z, reverse=True) for z in order), "in every zone the items issued on a confirmed slip of the last 14 days come first")
        shot(page, "issue_top.png")
        # rows per screen, scrolled
        page.evaluate("document.querySelector('main.content-area').scrollTop = 700")
        page.wait_for_timeout(500)
        n = page.evaluate("""() => {
          const top = document.querySelector('.brn-zonebar').getBoundingClientRect().bottom;
          const bot = document.querySelector('#brn-pane-issue .brn-sticky-bottom').getBoundingClientRect().top;
          return { top, bot, n: [...document.querySelectorAll('#brnIssList .brn-row')].filter(r => { const x = r.getBoundingClientRect(); return x.top >= top - 1 && x.bottom <= bot + 1; }).length }; }""")
        nums["rows_per_screen"] = n["n"]
        check(n["n"] >= 6, f"{n['n']} rows fit one screen when scrolled (between {n['top']:.0f}px and {n['bot']:.0f}px)")
        shot(page, "issue_scrolled.png")
        # autosave: one value typed -> one request, the status says so at 14 px
        rowsq = page.query_selector_all("#brnIssList .brn-qty")
        u0 = rowsq[0]
        before = len(saves)
        u0.scroll_into_view_if_needed()
        u0.fill("2")
        page.wait_for_function("document.getElementById('brnIssStatus').innerText.includes('กำลังบันทึก') || /บันทึกร่างแล้ว/.test(document.getElementById('brnIssStatus').innerText)", timeout=8000)
        page.wait_for_function("/บันทึกร่างแล้ว \\d\\d:\\d\\d/.test(document.getElementById('brnIssStatus').innerText)", timeout=8000)
        st = page.evaluate("(() => { const e = document.getElementById('brnIssStatus'); return { t: e.innerText, fs: getComputedStyle(e).fontSize }; })()")
        check(len(saves) == before + 1 and st["fs"] == "14px", f"the autosave sent one request; the status reads '{st['t']}' at {st['fs']}")
        shot(page, "issue_saved.png")
        # a negative value and a word: red + note under the row + NO request
        for bad_val in ("-5", "abc"):
            inp = page.query_selector_all("#brnIssList .brn-qty")[2]
            code = inp.get_attribute("id")[len("brnIssU_"):]
            before = len(saves)
            inp.scroll_into_view_if_needed()
            inp.fill(bad_val)
            page.wait_for_timeout(300)
            r = page.evaluate("""(code) => { const i = document.getElementById('brnIssU_' + code), e = document.getElementById('brnIssE_' + code);
              return { border: i.style.borderColor, inv: i.getAttribute('aria-invalid'), note: e.innerText, shown: !!(e.offsetWidth || e.offsetHeight),
                       below: e.getBoundingClientRect().top >= i.getBoundingClientRect().bottom - 1 }; }""", code)
            check("bad-ink" in r["border"] and r["inv"] == "true" and "จำนวนต้องเป็นตัวเลข ≥ 0" in r["note"] and r["shown"] and r["below"],
                  f"'{bad_val}' turns the box red at once with the note under the row ({r['note']!r})")
            page.wait_for_timeout(3600)                                   # longer than the 2.5 s autosave
            st = page.evaluate("document.getElementById('brnIssStatus').innerText")
            check(len(saves) == before, f"'{bad_val}' sends no save request")
            check("บันทึกร่างแล้ว" not in st and "ไม่ได้บันทึก" in st, f"no 'บันทึกร่างแล้ว' after a dropped line; the status reads '{st}'")
            if bad_val == "-5":
                shot(page, "issue_invalid.png")
            inp.fill("")
            page.wait_for_timeout(200)
            check(page.evaluate("(c) => document.getElementById('brnIssE_' + c).innerText === ''", code), "the note goes away once the box is empty")
        page.wait_for_timeout(3200)
        # waste: the reason select opens inline, 16 px
        w =page.query_selector_all("#brnIssList .brn-row")[3].query_selector_all(".brn-qty")[1]
        wcode = w.get_attribute("id")[len("brnIssW_"):]
        w.scroll_into_view_if_needed()
        w.fill("1")
        page.wait_for_selector(f"#brnIssR_{wcode}", timeout=3000)
        sel = page.evaluate("(c) => { const s = document.getElementById('brnIssR_' + c); const r = s.getBoundingClientRect(); const i = document.getElementById('brnIssW_' + c).getBoundingClientRect(); return { fs: getComputedStyle(s).fontSize, h: r.height, below: r.top >= i.bottom - 1 }; }", wcode)
        check(sel["fs"] == "16px" and sel["below"], f"a waste above 0 opens the reason select under the row (font {sel['fs']}, {sel['h']:.0f}px high)")
        page.select_option(f"#brnIssR_{wcode}", "waste")
        page.wait_for_timeout(3300)
        # the confirm dialog counts the empty items
        page.evaluate("document.querySelector('main.content-area').scrollTop = 0")
        page.click("text=ยืนยันใบตัดเบิก >> visible=true")
        page.wait_for_selector("form [data-el=msg]", timeout=5000)
        dlg = page.evaluate("() => ({ title: document.querySelector('[data-el=title]').textContent, msg: document.querySelector('[data-el=msg]').textContent, ok: document.querySelector('[data-el=msg]').closest('form').querySelector('[type=submit]').textContent, cancel: document.querySelector('[data-act=cancel]').textContent })")
        print("     dialog:", dlg["msg"].replace("\n", " ⏎ "))
        check("ไม่ได้กรอก" in dlg["msg"] and re.search(r"กรอก \d+ รายการ · ของเสีย \d+ รายการ · ไม่ได้กรอก \d+ รายการ \(ถือว่าไม่ได้เบิก\)", dlg["msg"]) is not None,
              "the confirm dialog states how many items were filled, wasted and left empty")
        check((dlg["title"], dlg["ok"], dlg["cancel"]) == ("ยืนยันใบตัดเบิก", "ยืนยัน", "กลับไปแก้"), f"title / buttons: {dlg['title']} · {dlg['ok']} · {dlg['cancel']}")
        shot(page, "issue_confirm.png")
        page.click("[data-act=cancel]")
        # the zone chip counts what was filled
        chips = page.evaluate("[...document.querySelectorAll('#brnIssZones button')].map(b => b.innerText.trim())")
        check(any(re.search(r"· \d+", z) for z in chips), f"a zone chip shows the number of filled items: {chips}")
        # "โซนนี้ครบ": every REQUIRED item of a zone filled -> a ✓ on its chip (checked on the page's own state: no request goes out)
        zdone = page.evaluate("""() => { const z = _brnIss.zones.find(x => x.items.some(i => i.required)); const keep = JSON.stringify(_brnIssQty);
          const had = document.getElementById('brnIssZones').innerText.includes('✓');
          z.items.filter(i => i.required).forEach(i => { _brnIssQty[i.code] = Object.assign({ used: '', waste: '', reason: '', note: '' }, _brnIssQty[i.code], { used: '1' }); });
          brnIssRender();
          const withTick = [...document.querySelectorAll('#brnIssZones button')].filter(b => b.innerText.includes('✓')).map(b => b.innerText.trim());
          _brnIssQty = JSON.parse(keep); brnIssRender();
          return { had, withTick, zone: z.label, after: document.getElementById('brnIssZones').innerText.includes('✓') }; }""")
        check(not zdone["had"] and any(zdone["zone"] in t for t in zdone["withTick"]) and not zdone["after"],
              f"a zone whose required items are all filled gets a ✓ on its chip: {zdone['withTick']}")
        # a missing day opens its own slip
        miss = page.query_selector("#brnIssDays button:has-text('ยังไม่ทำ')")
        if miss:
            label = miss.inner_text().replace("\n", " ")
            miss.click()
            page.wait_for_function("_brnIss && _brnIss.date !== _brnIss.today && !document.getElementById('brnIssList').innerText.includes('กำลังโหลด')", timeout=10000)
            head = page.evaluate("document.getElementById('brnIssHead').innerText.replace(/\\s+/g, ' ')")
            day = page.evaluate("fmtDay(_brnIss.date)")
            check(day in head and page.evaluate("_brnIss.editable === true"), f"tapping '{label}' opens that day's slip, editable: {head}")
            page.click("#brnIssDays button >> nth=0")                 # back to today
            page.wait_for_timeout(1500)
        # clean up today's draft so the scratch copy stays tidy
        for code in (wcode,):
            page.fill(f"#brnIssW_{wcode}", "")
        for inp in page.query_selector_all("#brnIssList .brn-qty")[:1]:
            inp.fill("")
        page.wait_for_timeout(3200)

        # ── สั่งสินค้า ──
        print("== phone · สั่งสินค้า")
        page.evaluate("supplyGo({tab:'branchstock', pane:'order'})")
        page.wait_for_selector("#brnOrdList .brn-row, #brnOrdList .text-xs", timeout=90000)
        page.wait_for_timeout(1500)
        o = page.evaluate("""() => ({
          rounds: [...document.querySelectorAll('#brnOrdDeliv button')].map(b => ({ t: b.innerText.replace(/\\s+/g, ' ').trim(), h: b.getBoundingClientRect().height })),
          buf: document.getElementById('brnOrdBuffer').innerText.replace(/\\s+/g, ' ').trim(), bufInput: !!document.getElementById('brnBufPct'),
          pages: [...document.querySelectorAll('#brnOrdPages button')].map(b => b.innerText.trim()),
          rows: document.querySelectorAll('#brnOrdList .brn-row').length })""")
        print("     rounds:", [r["t"] for r in o["rounds"]])
        check(len(o["rounds"]) > 0 and all(re.search(r"^.+ · ส่ง .+ · สั่งภายใน .+ \d\d:\d\d (เปิดรับ|เลยเวลา|สั่งแล้ว)$", r["t"]) for r in o["rounds"]),
              "every round is one line 'รอบ · ส่ง … · สั่งภายใน …' with a state chip")
        check("ยอดขายรอบนี้ต่างจากปกติ?" in o["buf"] and not o["bufInput"], "the buffer card is folded under 'ยอดขายรอบนี้ต่างจากปกติ?'")
        if o["pages"]:
            check(o["pages"][0].startswith("สั่งจากไดกิโตะ") and o["pages"][1].startswith("ซื้อเอง"), f"sub-tabs: {o['pages']}")
        shot(page, "order_top.png")
        if o["rows"]:
            row = page.evaluate("""() => { const r = document.querySelector('#brnOrdList .brn-row'); const t = r.innerText.replace(/\\s+/g, ' ');
              const q = r.querySelector('.brn-qty'); const cap = r.querySelector('.brn-cap');
              return { t, cap: cap.innerText, below: cap.getBoundingClientRect().top >= q.getBoundingClientRect().bottom - 1, h: q.getBoundingClientRect().height }; }""")
            check("แนะนำ" in row["t"] and "ดูที่มา" in row["t"] and row["cap"].startswith("สั่ง · ") and row["below"] and row["h"] >= 43.5,
                  f"a row: name · แนะนำ · คงเหลือ · the order box with its unit ({row['cap']}) and 'ดูที่มา'")
            clip = page.evaluate(CLIP_JS, "#brnOrdList")
            check(not clip["clipped"], f"order rows: no text in the name column is clipped ({clip['n']} rows)" + (f" — {clip['clipped'][:3]}" if clip["clipped"] else ""))
            check("ชั้น " not in row["t"] and "ใช้ใน" not in row["t"] and "พยากรณ์" not in row["t"], "no class / recipe / formula text on the closed row")
            page.click("#brnOrdList .brn-why >> nth=0")
            page.wait_for_timeout(300)
            why = page.evaluate("document.querySelector('#brnOrdList .brn-row').innerText.replace(/\\s+/g, ' ')")
            check("ต้องการ" in why and not re.search(r"(?<!กิโล)กรัม", why), f"'ดูที่มา' opens the formula in count units (no grams): …{why[-90:]}")
            shot(page, "order_row_open.png")
        page.click("text=ยอดขายรอบนี้ต่างจากปกติ? >> visible=true")
        page.wait_for_selector("#brnBufPct", timeout=3000)
        shot(page, "order_buffer.png")
        page.click("text=คำนวณจากจำนวนลูกค้า >> visible=true")
        page.wait_for_selector("#brnCovSheet.open", timeout=8000)
        page.wait_for_timeout(1200)
        sheet = page.evaluate("""() => { const c = document.querySelector('#brnCovSheet .brn-sheet-card').getBoundingClientRect();
          return { bottom: c.bottom, top: c.top, w: c.width, vh: innerHeight, pos: getComputedStyle(document.getElementById('brnCovSheet')).position }; }""")
        check(sheet["pos"] == "fixed" and abs(sheet["bottom"] - sheet["vh"]) < 2 and sheet["w"] >= 388, f"the customer-count helper is a bottom sheet (card {sheet['top']:.0f}–{sheet['bottom']:.0f}px of {sheet['vh']}px)")
        shot(page, "order_covers.png")
        page.click("#brnCovSheet button[aria-label=ปิด]")
        check(not page.evaluate("document.getElementById('brnCovSheet').classList.contains('open')"), "the sheet closes")
        # ═════════════ PR-M4 · รับสินค้า · เคลมภายหลัง · ผลต่าง · ตรวจนับ ═════════════
        reqs = []
        page.on("request", lambda rq: reqs.append((rq.method, rq.url, rq.post_data or (rq.post_data_buffer or b"").decode("utf-8", "ignore"))) if rq.method == "POST" else None)
        taps = [0]

        def tap(sel, **kw):
            taps[0] += 1
            page.click(sel, **kw)

        def posts(part):
            return [r for r in reqs if part in r[1]]

        def dialog():
            return page.evaluate("() => ({ title: document.querySelector('[data-el=title]').textContent, msg: document.querySelector('[data-el=msg]').textContent, ok: document.querySelector('[data-el=msg]').closest('form').querySelector('[type=submit]').textContent, cancelShown: getComputedStyle(document.querySelector('[data-act=cancel]')).display !== 'none' })")

        def api(path):
            return ctx.request.get(a.base + path).json()

        # ── รับสินค้า ──
        print("== phone · รับสินค้า")
        if a.db:
            fresh_processed_order(a.db)
        else:
            print("note: no --db, the receive sections use the processed order that is there (if any)")
        page.evaluate("supplyGo({tab:'branchstock', pane:'recv'})")
        page.wait_for_timeout(500)
        page.evaluate("brnLoadRecv()")
        openo = api("/api/bom-center/orders?status=open&limit=20")["orders"]
        sent_orders = [o for o in openo if o["status"] == "processed"]
        if len(sent_orders) != 1:
            check(False, f"expected exactly one order sent by the warehouse for the receive test, found {len(sent_orders)} (pass --db of the scratch copy)")
        else:
            oid = sent_orders[0]["order_id"]
            det = api(f"/api/bom-center/order/{oid}")
            lines_all = [l for l in det["lines"] if float(l.get("processed_units") or 0) > 0 or l.get("received_units")]
            page.wait_for_selector("#brnRecvOrder .brn-q", timeout=15000)
            page.wait_for_timeout(500)
            q = page.evaluate("""() => { const f = document.getElementById('brnRecvOrder'); const b = document.getElementById('brnRecvOrdSave').getBoundingClientRect();
              const w = document.getElementById('brnRecvWhy'); const nav = document.getElementById('supplyNav');
              const bt = [...f.querySelectorAll('.brn-qbtn')].map(x => ({ t: x.innerText.trim(), h: x.getBoundingClientRect().height }));
              return { q: f.querySelector('.brn-q').innerText.trim(), bt, rows: f.querySelectorAll('.brn-row').length, dis: document.getElementById('brnRecvOrdSave').disabled,
                       why: w.innerText.trim(), whyBelow: w.getBoundingClientRect().top >= b.bottom - 1, saveBottom: b.bottom, saveTop: b.top, navTop: nav ? nav.getBoundingClientRect().top : innerHeight,
                       sticky: getComputedStyle(f.querySelector('.brn-rcv-bar')).position, vh: innerHeight, formTop: f.getBoundingClientRect().top }; }""")
            check(q["q"] == "รับครบตามที่คลังส่งทุกรายการ?" and [x["t"] for x in q["bt"]] == ["รับครบ", "มีรายการไม่ครบ"] and all(x["h"] >= 43.5 for x in q["bt"]),
                  f"the form opens with ONE question and two big buttons: {q['q']} · {[(x['t'], round(x['h'])) for x in q['bt']]}")
            check(q["rows"] == 0, f"no line is listed before the answer ({len(lines_all)} lines in the order)")
            check(q["dis"] and "รับครบ" in q["why"] and q["whyBelow"], f"the save button is off and says why under it: '{q['why']}'")
            check(q["sticky"] == "sticky" and q["saveBottom"] <= q["navTop"] + 1 and q["saveTop"] >= 0, f"the save button sits in the sticky bar, on screen without scrolling (button {q['saveTop']:.0f}–{q['saveBottom']:.0f}px, strip at {q['navTop']:.0f}px)")
            shot(page, "recv_question.png")
            # the “รับครบ” path
            taps[0] = 0
            n0 = len(posts("/receive"))
            tap("#brnRcvAll")
            bd = page.evaluate("() => ({ t: document.getElementById('brnRcvBody').innerText.replace(/\\s+/g, ' ').trim(), rows: document.querySelectorAll('#brnRecvOrder .brn-row').length, why: document.getElementById('brnRecvWhy').innerText.trim(), ack: document.querySelector('#brnRecvAck + span').innerText.trim(), terms: [...document.querySelectorAll('#brnRecvOrder .brn-link')].map(x => x.innerText.trim()) })")
            check(bd["t"].startswith(f"รับครบ {len(lines_all)} รายการ") and bd["rows"] == 0, f"รับครบ → the lines stay folded as one line: '{bd['t'][:60]}'")
            check(bd["ack"] == "ตรวจสภาพและจำนวนครบแล้ว" and "เงื่อนไขการเคลม" in bd["terms"] and "ติ๊ก" in bd["why"], f"the acknowledgement is one checkbox '{bd['ack']}' with a link 'เงื่อนไขการเคลม'; the bar says: {bd['why']}")
            page.click("text=เงื่อนไขการเคลม")                                     # (reading the terms is not a step of the path)
            terms = page.evaluate("(() => { const t = document.getElementById('brnRcvTerms'); return { shown: t.style.display !== 'none', t: t.innerText }; })()")
            check(terms["shown"] and "เคลมภายหลังอาจไม่ได้รับพิจารณา" in terms["t"], "the link opens the full claim terms")
            page.click("text=เงื่อนไขการเคลม")
            shot(page, "recv_all.png")
            tap("#brnRecvAck")
            check(not page.evaluate("document.getElementById('brnRecvOrdSave').disabled"), "ticking the acknowledgement turns the save button on")
            tap("#brnRecvOrdSave")
            page.wait_for_selector("form [data-el=msg]", timeout=5000)
            dlg = dialog()
            print("     dialog:", dlg["msg"].replace("\n", " ⏎ "))
            check(dlg["title"] == "ยืนยันการบันทึกรับสินค้า" and f"{len(lines_all)} รายการ" in dlg["msg"] and "รับครบทุกรายการ" in dlg["msg"] and dlg["ok"] == "บันทึกรับสินค้า",
                  f"the confirmation states the quantity: {dlg['msg'][:90]}")
            taps[0] += 1
            page.evaluate("document.getElementById('brnRecvStatus').textContent = ''")
            page.click("form:has([data-el=msg]) [type=submit]")
            page.wait_for_function("/บันทึกรับสินค้าแล้ว/.test(document.getElementById('brnRecvStatus').innerText)", timeout=15000)
            rc = posts("/receive")[n0:]
            body = __import__("json").loads(rc[0][2]) if rc else {}
            bl = body.get("lines", [])
            sent_by = {l["code"]: float(l.get("processed_units") or 0) for l in lines_all}
            check(len(rc) == 1 and body.get("ack") is True and len(bl) == len(lines_all) and all(float(x["received_units"]) == sent_by[x["code"]] and "reason" not in x for x in bl),
                  f"รับครบ = ONE call to /receive with all {len(bl)} lines at the quantity sent, ack true, no reason")
            nums["taps_receive_all"] = taps[0]
            check(taps[0] <= 4, f"รับครบ took {taps[0]} taps inside the open form (รับครบ · tick · บันทึก · ยืนยัน); 3 before the confirmation; 5 with the card's รับสินค้า button when the form is not already open")
            print("     taps:", taps[0])
            dd = api(f"/api/bom-center/order/{oid}")
            check(dd["status"] == "received" and dd["claims"] == [], "the order is received and no claim was raised")

            # the “มีรายการไม่ครบ” path
            if a.db:
                fresh_processed_order(a.db)
                page.evaluate("brnLoadRecv()")
            openo = api("/api/bom-center/orders?status=open&limit=20")["orders"]
            sent_orders = [o for o in openo if o["status"] == "processed"]
            oid = sent_orders[0]["order_id"]
            det = api(f"/api/bom-center/order/{oid}")
            lines_all = [l for l in det["lines"] if float(l.get("processed_units") or 0) > 0 or l.get("received_units")]
            sent_by = {l["code"]: float(l.get("processed_units") or 0) for l in lines_all}
            page.wait_for_selector("#brnRecvOrder .brn-q", timeout=15000)
            page.wait_for_function("document.getElementById('brnRecvOrder').style.display !== 'none' && !document.getElementById('brnRcvAll').getAttribute('aria-pressed').includes('true')", timeout=10000)
            tap("#brnRcvSome")
            check(page.evaluate("!!document.getElementById('brnRcvQ') && document.querySelectorAll('#brnRecvOrder .brn-row').length === 0"), "มีรายการไม่ครบ → a search box, no line is open yet")
            pick = next(l for l in lines_all if float(l.get("processed_units") or 0) >= 2)
            term = pick["name"].strip()[:5]
            page.fill("#brnRcvQ", term)
            hits = page.evaluate("[...document.querySelectorAll('#brnRcvRes .brn-pick')].map(b => b.innerText.replace(/\\s+/g, ' ').trim())")
            check(0 < len(hits) <= 8, f"the search lists at most 8 lines: {len(hits)} for '{term}'")
            page.click(f"#brnRcvRes .brn-pick:has-text('{pick['name'].strip()[:12]}') >> nth=0")
            card = page.evaluate("""() => { const r = document.querySelectorAll('#brnRecvOrder .brn-row'); const i = r[0] && r[0].querySelector('.brn-qty'); const rect = i && i.getBoundingClientRect();
              return { n: r.length, h: rect && rect.height, v: i && i.value, rest: document.getElementById('brnRcvRest').innerText.replace(/\\s+/g, ' ').trim(), diffShown: r[0] && getComputedStyle(r[0].querySelector('[id^=brnRcvD_]')).display !== 'none' }; }""")
            check(card["n"] == 1 and card["h"] >= 43.5 and float(card["v"]) == sent_by[pick["code"]], f"only the picked line expands: รับจริง box {card['h']:.0f}px high, pre-set to the sent quantity {card['v']}")
            check(card["rest"].startswith(f"รับครบ {len(lines_all) - 1} รายการ"), f"the rest stay folded as '{card['rest']}'")
            page.fill(f"#brnRcvO_{pick['code']}", "abc")
            err = page.evaluate("(c) => ({ t: document.getElementById('brnRcvE_' + c).innerText, inv: document.getElementById('brnRcvO_' + c).getAttribute('aria-invalid') })", pick["code"])
            check("ตัวเลข" in err["t"] and err["inv"] == "true", f"a word in รับจริง turns red with the note under the line: {err['t']}")
            short_to = sent_by[pick["code"]] - 1
            page.fill(f"#brnRcvO_{pick['code']}", f"{short_to:g}")
            gap = page.evaluate("""(c) => ({ d: getComputedStyle(document.getElementById('brnRcvD_' + c)).display, n: document.getElementById('brnRcvDn_' + c).innerText, r: document.getElementById('brnRcvR_' + c).value,
              rh: document.getElementById('brnRcvR_' + c).getBoundingClientRect().height, fs: getComputedStyle(document.getElementById('brnRcvR_' + c)).fontSize,
              box: getComputedStyle(document.getElementById('brnRecvClaimBox')).display, boxT: document.getElementById('brnRecvClaimTitle').innerText })""", pick["code"])
            check(gap["d"] != "none" and gap["n"] == "1" and gap["r"] == "short" and gap["rh"] >= 43.5 and gap["fs"] == "16px" and gap["box"] != "none" and "1 รายการ" in gap["boxT"],
                  f"a shortfall shows the gap, the reason (default ส่งไม่ครบ, {gap['rh']:.0f}px, {gap['fs']}) and the claim box: {gap['boxT']}")
            shot(page, "recv_some.png")
            tap("#brnRecvAck")
            tap("#brnRecvOrdSave")
            page.wait_for_selector("form [data-el=msg]", timeout=5000)
            dlg = dialog()
            check("แจ้งเคลมส่วนต่าง 1 รายการ" in dlg["msg"] and "รับครบทุกรายการ" not in dlg["msg"], f"the confirmation names the claim: {dlg['msg'][:130]}")
            n0 = len(posts("/receive"))
            page.evaluate("document.getElementById('brnRecvStatus').textContent = ''")
            page.click("form:has([data-el=msg]) [type=submit]")
            page.wait_for_function("/บันทึกรับสินค้าแล้ว/.test(document.getElementById('brnRecvStatus').innerText)", timeout=15000)
            rc = posts("/receive")[n0:]
            body = __import__("json").loads(rc[0][2]) if rc else {}
            bl = body.get("lines", [])
            shorts = [x for x in bl if x.get("reason")]
            check(len(rc) == 1 and len(bl) == len(lines_all) and len(shorts) == 1 and shorts[0]["code"] == pick["code"] and float(shorts[0]["received_units"]) == short_to
                  and all(float(x["received_units"]) == sent_by[x["code"]] for x in bl if x is not shorts[0]),
                  f"one call, every line sent: {len(bl)} lines, the picked one short with a reason, the others at the sent quantity")
            st = page.evaluate("document.getElementById('brnRecvStatus').innerText")
            check("แจ้งเคลม 1 รายการ" in st and "#" not in st, f"the claim is raised in the same save: '{st}'")
            dd = api(f"/api/bom-center/order/{oid}")
            check(dd["status"] == "received" and len(dd["claims"]) == 1 and dd["claims"][0]["origin"] == "receive" and dd["claims"][0]["lines"][0]["claim_units"] == 1, "the order is received and ONE receive-time claim of 1 pack exists")
            shot(page, "recv_done.png")

            # ── เคลมภายหลัง ──
            print("== phone · เคลมภายหลัง")
            page.evaluate("brnLoadRecv()")
            page.wait_for_selector("#brnClaimList .brn-act", timeout=15000)
            page.wait_for_timeout(600)
            claim_row = page.evaluate("[...document.querySelectorAll('#brnClaimList a.brn-claimrow')].map(a => ({ t: a.innerText.replace(/\\s+/g, ' ').trim(), kids: a.children.length }))")
            check(claim_row and all(re.match(r"^.+ · ส่ง \d+/\d+ · \d+ รายการ .+$", r["t"]) and "#" not in r["t"] and r["kids"] == 2 for r in claim_row),
                  f"a claim reads as two lines 'round · ส่ง d/m · N รายการ' + chips, no id: {[r['t'] for r in claim_row[:2]]}")
            check(any("แจ้งขณะรับสินค้า" in r["t"] for r in claim_row) and any("รอประสานงานขายพิจารณา" in r["t"] or "อนุมัติ" in r["t"] for r in claim_row), "the origin chip (แจ้งขณะรับสินค้า) and the status are on the second line")
            shot(page, "claim_list.png")
            tap("#brnClaimList .brn-act >> nth=0")
            page.wait_for_selector("#brnClQ", timeout=10000)
            cf = page.evaluate("""() => { const card = document.getElementById('brnClaimCard'); const form = document.getElementById('brnClaimForm');
              const occ = (card.innerText.match(/ส่งรูปทางไลน์ถึงประสานงานขาย/g) || []).length;
              return { occ, inForm: form.innerText.includes('ส่งรูปทางไลน์ถึงประสานงานขาย'), top: card.innerText.indexOf('ส่งรูปทางไลน์ถึงประสานงานขาย') < card.innerText.indexOf('แจ้งเคลม ·'),
                       rows: document.querySelectorAll('#brnClPicked .brn-row').length, dis: document.getElementById('brnClaimSendBtn').disabled, hint: document.getElementById('brnClaimStatus').innerText.trim(),
                       qh: document.getElementById('brnClQ').getBoundingClientRect().height }; }""")
            check(cf["occ"] == 1 and not cf["inForm"] and cf["top"], "the photo instruction 'ส่งรูปทางไลน์ถึงประสานงานขาย' appears ONCE, at the top of the card")
            check(cf["rows"] == 0 and cf["dis"] and "อย่างน้อย 1 รายการ" in cf["hint"], f"nothing is listed per line: pick items first (send is off: '{cf['hint']}')")
            cl = api(f"/api/bom-center/claims/claimable?order_id={oid}")
            cpick = next(l for l in cl["lines"] if l["remaining_units"] >= 1)
            page.fill("#brnClQ", cpick["name"].strip()[:6])
            page.click(f"#brnClRes .brn-pick:has-text('{cpick['name'].strip()[:12]}') >> nth=0")
            row = page.evaluate("""(c) => { const r = document.querySelectorAll('#brnClPicked .brn-row'); const q = document.getElementById('brnCq_' + c); return { n: r.length, h: q.getBoundingClientRect().height,
              noteShown: getComputedStyle(document.getElementById('brnCnBox_' + c)).display !== 'none', dis: document.getElementById('brnClaimSendBtn').disabled }; }""", cpick["code"])
            check(row["n"] == 1 and row["h"] >= 43.5 and not row["noteShown"] and not row["dis"], f"only the picked item opens: quantity box {row['h']:.0f}px, no note box until 'อื่น ๆ'")
            page.select_option(f"#brnCr_{cpick['code']}", "other")
            check(page.evaluate("(c) => getComputedStyle(document.getElementById('brnCnBox_' + c)).display !== 'none'", cpick["code"]), "choosing อื่น ๆ opens the note box")
            tap("#brnClaimSendBtn")                                                         # nothing typed yet: the message stays under the line
            e = page.evaluate("(c) => document.getElementById('brnCe_' + c).innerText", cpick["code"])
            check("มากกว่า 0" in e, f"a missing quantity is refused under its line: {e}")
            page.fill(f"#brnCq_{cpick['code']}", "1")
            tap("#brnClaimSendBtn")
            e = page.evaluate("(c) => document.getElementById('brnCe_' + c).innerText", cpick["code"])
            check("อื่น ๆ" in e and "คำอธิบาย" in e, f"อื่น ๆ without a note is refused under its line: {e}")
            page.select_option(f"#brnCr_{cpick['code']}", "damaged")
            shot(page, "claim_form.png")
            tap("#brnClaimSendBtn")
            page.wait_for_selector("form [data-el=msg]", timeout=5000)
            dlg = dialog()
            check(dlg["msg"].startswith("ส่งแจ้งเคลม 1 รายการ") and "#" not in dlg["msg"], f"the confirmation: {dlg['msg']}")
            n0 = len(posts("/claims"))
            page.click("form:has([data-el=msg]) [type=submit]")
            page.wait_for_function("/ส่งแจ้งเคลมแล้ว/.test(document.getElementById('brnClaimMsg').innerText)", timeout=15000)
            cp = posts("/claims")[n0:]
            check(len(cp) == 1 and '"claim_units":1' in cp[0][2] and "damaged" in cp[0][2], "one claim call with the one picked line")
            page.wait_for_timeout(800)
            claim_row = page.evaluate("[...document.querySelectorAll('#brnClaimList a.brn-claimrow')].map(a => a.innerText.replace(/\\s+/g, ' ').trim())")
            check(any("แจ้งภายหลัง" in t and "รอประสานงานขายพิจารณา" in t for t in claim_row), f"the new claim shows 'แจ้งภายหลัง' + 'รอประสานงานขายพิจารณา': {claim_row[:2]}")
            shot(page, "claim_sent.png")

        # ── ผลต่าง ──
        print("== phone · ผลต่าง")
        page.evaluate("supplyGo({tab:'branchstock', pane:'var'})")
        page.wait_for_selector("#brnVarList .brn-vrow, #brnVarList .text-sm", timeout=60000)
        page.wait_for_timeout(1200)
        periods = api("/api/bom-center/variance/periods")["periods"]
        vp = api(f"/api/bom-center/variance/period?end_count_id={periods[0]['end_count_id']}") if periods else None
        tabs = page.evaluate("[...document.querySelectorAll('#brnVarTabs button')].map(b => b.innerText.trim())")
        check(tabs == ["รายงวด", "รายวัน", "สิ้นเดือน"], f"the three tabs: {tabs}")
        if vp:
            head = page.evaluate("""() => ({ tiles: [...document.querySelectorAll('#brnVarHead .brn-v3t')].map(t => t.innerText.replace(/\\s+/g, ' ').trim()),
              chip: (document.querySelector('#brnVarHead .brn-chipbtn') || {}).innerText || '', h: (document.querySelector('#brnVarHead .brn-chipbtn') || { getBoundingClientRect: () => ({ height: 0 }) }).getBoundingClientRect().height,
              asks: document.querySelectorAll('#brnVarList .brn-vrow').length, selects: document.querySelectorAll('#brnVarList .brn-vrow select').length,
              others: (document.querySelector('#brnVarOthers summary') || {}).innerText, othersOpen: !!(document.getElementById('brnVarOthers') || {}).open,
              firstIsAsk: !!document.querySelector('#brnVarList > .brn-vrow, #brnVarList > .brn-multi') })""")
            check(len(head["tiles"]) == 3 and head["tiles"][0].startswith("ใช้จริง") and head["tiles"][1].startswith("ตามสูตร") and head["tiles"][2].startswith("ผลต่าง"), f"the head is three numbers: {head['tiles']}")
            check(head["chip"] == f"ต้องให้เหตุผล {vp['totals']['ask_reason_open']} รายการ" and head["h"] >= 43.5, f"one action chip: '{head['chip']}' ({head['h']:.0f}px)")
            check(head["asks"] == vp["totals"]["ask_reason"] and head["selects"] == head["asks"], f"only the {vp['totals']['ask_reason']} high-value lines have a form ({head['asks']} rows, {head['selects']} selects)")
            check(head["others"] == f"รายการอื่น {vp['totals']['other_flagged']} รายการ" and not head["othersOpen"], f"the rest are folded as '{head['others']}'")
            check(vp["totals"]["ask_reason"] < vp["flagged"], f"{vp['flagged']} lines are over the threshold but only {vp['totals']['ask_reason']} are asked (class A + the top 20 by ฿)")
            shot(page, "var_period_top.png")
            # a line in count units
            line = page.evaluate("document.querySelector('#brnVarList .brn-vrow .text-xs').innerText.replace(/\\s+/g, ' ').trim()")
            check(re.match(r"^ใช้จริง .+ · ตามสูตร .+ · (เกิน|น้อยกว่า) .+ \([+−]\d", line) is not None, f"the line reads: {line}")
            rows_txt = page.evaluate("[...document.querySelectorAll('#brnVarList .brn-vrow')].map(r => [r.dataset.code, r.querySelector('.text-xs').innerText])")
            by_code = {r["code"]: r for r in vp["rows"]}
            def unit_of(c):                                           # the count unit as the page writes it (a bracket note dropped)
                return re.sub(r"\s*\([^)]*\)", "", by_code[c]["count_unit"] or "").strip()
            not_g = [(c, t_) for c, t_ in rows_txt if by_code[c]["count_unit"] != "กรัม"]
            wrong = [c for c, t_ in not_g if t_.count(unit_of(c)) < 2]       # the unit is written after the used AND the recipe quantity
            check(not_g and not wrong, f"quantities are in the unit counted: {len(not_g)} of {len(rows_txt)} asked lines are not counted in grams and every one reads in its count unit (e.g. {not_g[0][1][:70]!r}); grams only where the count unit is grams")
            # inline error: save without a reason
            code0 = page.evaluate("document.querySelector('#brnVarList .brn-vrow[data-open=\"1\"]').dataset.code")
            head_before = page.evaluate("document.getElementById('brnVarHead').innerText")
            page.click(f"#brnVarList .brn-vrow[data-code='{code0}'] button:has-text('บันทึก')")
            er = page.evaluate("""(c) => { const e = document.getElementById('brnVarE_' + c); const r = e.closest('.brn-vrow'); return { t: e.innerText, inRow: !!r, below: e.getBoundingClientRect().top >= r.querySelector('select').getBoundingClientRect().bottom - 1, head: document.getElementById('brnVarHead').innerText, status: document.getElementById('brnVarStatus').innerText }; }""", code0)
            check("เลือกเหตุผล" in er["t"] and er["inRow"] and er["below"] and er["head"] == head_before and er["status"] == "", f"an empty save shows its error under the line, not in the header: '{er['t']}'")
            shot(page, "var_error_inline.png")
            # one reason on one line
            n_open = vp["totals"]["ask_reason_open"]
            page.select_option(f"#brnRs_{code0}", "miscount")
            page.click(f"#brnVarList .brn-vrow[data-code='{code0}'] button:has-text('บันทึก')")
            page.wait_for_function("(n) => document.querySelector('#brnVarHead .brn-chipbtn') ? document.querySelector('#brnVarHead .brn-chipbtn').innerText.includes('ต้องให้เหตุผล ' + (n - 1) + ' ') : n === 1", arg=n_open, timeout=15000)
            check(api(f"/api/bom-center/variance/period?end_count_id={periods[0]['end_count_id']}")["totals"]["ask_reason_open"] == n_open - 1, f"one reason saved: the chip went from {n_open} to {n_open - 1}")
            # the same reason for several ticked lines
            if n_open >= 4:
                codes = page.evaluate("[...document.querySelectorAll('#brnVarList .brn-vrow[data-open=\"1\"]')].slice(0, 2).map(r => r.dataset.code)")
                for c in codes:
                    page.check(f"#brnVarList .brn-vrow[data-code='{c}'] .brn-chk input")
                check(page.evaluate("document.getElementById('brnVarSelN').innerText").startswith("เลือก 2 รายการ"), "two ticked lines are counted")
                page.click("#brnVarMultiBtn")
                check("เลือกเหตุผล" in page.evaluate("document.getElementById('brnVarMultiE').innerText"), "no reason chosen → the message is under the multi-select bar")
                page.select_option("#brnVarMultiR", "waste")
                page.click("#brnVarMultiBtn")
                page.wait_for_function("(n) => document.querySelector('#brnVarHead .brn-chipbtn').innerText.includes('ต้องให้เหตุผล ' + (n - 3) + ' ')", arg=n_open, timeout=20000)
                after = api(f"/api/bom-center/variance/period?end_count_id={periods[0]['end_count_id']}")
                saved = [r for r in after["rows"] if r["code"] in codes]
                check(all(r.get("reason") == "waste" for r in saved) and after["totals"]["ask_reason_open"] == n_open - 3, "ใช้เหตุผลเดียวกันกับที่เลือก: both lines saved with one reason in one action")
            # the others, folded, no form
            page.click("#brnVarOthers summary")
            oth = page.evaluate("({ forms: document.querySelectorAll('#brnVarOthers select, #brnVarOthers button:not(.brn-link)').length, rows: document.querySelectorAll('#brnVarOthers > div').length })")
            check(oth["forms"] == 0 and oth["rows"] > 0, f"'รายการอื่น' lists {oth['rows']} lines with no form")
            shot(page, "var_period_others.png")
        # daily
        page.click("#brnVarTabs button:has-text('รายวัน')")
        page.wait_for_selector("#brnVarDayList .brn-daycard, #brnVarDayList .text-xs", timeout=60000)
        page.wait_for_timeout(800)
        dc = page.evaluate("""() => ({ cards: document.querySelectorAll('#brnVarDayList .brn-daycard').length, tables: document.querySelectorAll('#brnVarDayList table').length,
          first: (document.querySelector('#brnVarDayList .brn-daycard') || { innerText: '' }).innerText.replace(/\\s+/g, ' ').trim(),
          h: (document.querySelector('#brnVarDayList .brn-daycard') || { getBoundingClientRect: () => ({ height: 0 }) }).getBoundingClientRect().height })""")
        check(dc["cards"] > 0 and dc["tables"] == 0 and dc["h"] >= 43.5, f"รายวัน = one card per day, no 9-column table ({dc['cards']} cards): {dc['first'][:80]}")
        shot(page, "var_daily.png")
        dly = api("/api/bom-center/issue-reports/daily?month=" + page.evaluate("document.getElementById('brnVarDayMonth').value"))
        best = max((r for r in dly["rows"] if r["trading"]), key=lambda r: r["bom_value"], default=None)
        conf = page.query_selector(f"#brnVarDayList .brn-daycard[onclick*=\"'{best['date']}'\"]") if best else None
        if conf:
            conf.click()
            page.wait_for_selector("#brnVarDayDetail table, #brnVarDayDetail .text-xs", timeout=20000)
            dd = page.evaluate("({ th: [...document.querySelectorAll('#brnVarDayDetail th')].map(x => x.innerText.trim()), tr: document.querySelectorAll('#brnVarDayDetail tbody tr').length, cell: (document.querySelector('#brnVarDayDetail tbody tr td:nth-child(2)') || { innerText: '' }).innerText.replace(/\\s+/g, ' ') })")
            check(dd["th"] == ["รายการ", "เบิก / ตามสูตร", "ผลต่าง"] and dd["tr"] > 0, f"a day opens its items in three columns: {dd['th']} ({dd['tr']} rows) e.g. '{dd['cell']}'")
            shot(page, "var_daily_day.png")
        # month end
        page.click("#brnVarTabs button:has-text('สิ้นเดือน')")
        page.wait_for_selector("#brnRcList .text-xs, #brnRcList div", timeout=60000)
        page.wait_for_timeout(1000)
        rc = page.evaluate("[...document.querySelectorAll('#brnRcList > div .text-xs')].map(x => x.innerText.replace(/\\s+/g, ' ').trim()).filter(t => t.includes('คงเหลือตามระบบ'))")
        recon = api(f"/api/bom-center/issue-reports/reconcile?end_count_id={periods[0]['end_count_id']}") if periods else None
        if rc and recon:
            nonmass = [x["code"] for x in recon["rows"] if x["count_unit"] != "กรัม"]
            check(len(rc) > 0 and not any("กรัม" in t for t in rc[:5]) or not nonmass, f"สิ้นเดือน: quantities in count units, e.g. {rc[0][:110]}")
        shot(page, "var_recon.png")
        page.click("#brnVarTabs button:has-text('รายงวด')")

        # ── ตรวจนับสิ้นเดือน ──
        print("== phone · ตรวจนับสิ้นเดือน")
        page.evaluate("supplyGo({tab:'branchstock', pane:'count'})")
        page.wait_for_selector("#brnList div", timeout=60000)
        page.wait_for_timeout(800)
        sh = api("/api/bom-center/stock/sheet")
        req = [i for z in sh["zones"] for i in z["items"] if i["required"]]
        cnt = page.evaluate("""() => ({ zones: [...document.querySelectorAll('#brnZones button')].map(b => b.innerText.trim()), progress: document.getElementById('brnProgress').innerText.replace(/\\s+/g, ' '),
          help: document.getElementById('brnHowTo').tagName, info: document.getElementById('brnInfo').innerText })""")
        check(not any(re.search(r"\d+/\d+", z) for z in cnt["zones"]) and cnt["help"] == "DETAILS", f"count zone chips are 'label · n' (no x/y); วิธีนับ stays folded: {cnt['zones']}")
        ll = api("/api/bom-center/stock/sheet")
        lq = next((i for z in ll["zones"] for i in z["items"] if i["last_qty"] is not None), None)
        if lq:
            off = [i["code"] for z in ll["zones"] for i in z["items"] if i["last_qty"] is not None and i["last_unit"] != i["count_unit"]]
            txt = page.evaluate("[...document.querySelectorAll('#brnList .brn-namecol')].map(n => n.innerText.replace(/\\s+/g, ' ')).filter(t => t.includes('ครั้งก่อน')).slice(0, 3)")
            seg = [re.search(r"ครั้งก่อน .*", t_).group(0) for t_ in txt]
            check(not off and seg and all(lq["count_unit"] and True for _ in seg), f"'ครั้งก่อน' is read in the unit counted now (server: last_unit == count_unit for all {sum(1 for z in ll['zones'] for i in z['items'] if i['last_qty'] is not None)} counted items): {seg[:1]}")
        # the zone tick (page state only: no request goes out)
        zt = page.evaluate("""() => { const s = _brnSheet; const z = s.zones.find(x => x.items.some(i => i.required)); const keep = JSON.stringify(_brnQty);
          const before = [...document.querySelectorAll('#brnZones button')].some(b => b.innerText.includes('✓'));
          s.zones.forEach(zz => zz.items.filter(i => i.required).forEach(i => { if (zz === z) _brnQty[i.code] = '1'; else delete _brnQty[i.code]; })); brnRender();
          const withTick = [...document.querySelectorAll('#brnZones button')].filter(b => b.innerText.includes('✓')).map(b => b.innerText.trim());
          _brnQty = JSON.parse(keep); brnRender(); return { before, withTick, zone: z.label, after: [...document.querySelectorAll('#brnZones button')].some(b => b.innerText.includes('✓')) }; }""")
        check(not zt["before"] and len(zt["withTick"]) == 1 and zt["zone"] in zt["withTick"][0] and not zt["after"], f"a zone whose required items are all counted gets a ✓: {zt['withTick']}")
        shot(page, "count_top.png")
        # the submit is refused while a required item is uncounted
        n_save0 = len(posts("/stock/count"))
        missing = page.evaluate("_brnSheet.zones.flatMap(z => z.items).filter(i => i.required && !_brnCounted(i.code)).length")
        page.evaluate("document.querySelector('main.content-area').scrollTop = 0")
        page.click("text=ส่งผลตรวจนับ >> visible=true")
        page.wait_for_selector("form [data-el=msg]", timeout=5000)
        dlg = dialog()
        print("     dialog:", dlg["msg"])
        check(missing > 0 and re.match(rf"^ยังไม่นับรายการบังคับ {missing} รายการ \(เช่น .+\) นับให้ครบก่อนส่ง$", dlg["msg"]) is not None and dlg["ok"] == "กลับไปนับ" and not dlg["cancelShown"],
              f"the submit is refused with only a 'กลับไปนับ' button: {dlg['msg']}")
        shot(page, "count_refuse.png")
        page.click("form:has([data-el=msg]) [type=submit]")
        page.wait_for_timeout(400)
        check(len(posts("/stock/count")) == n_save0, "the refused submit sends no request")
        sv = ctx.request.post(a.base + "/api/bom-center/stock/count", data={"lines": [{"code": req[0]["code"], "qty": 1, "unit": req[0]["count_unit"]}], "submit": True})
        check(sv.status == 400 and "ยังไม่นับรายการบังคับ" in sv.text() and "นับให้ครบก่อนส่ง" in sv.text(), f"the server refuses the same way: {sv.status} {sv.text()[:110]}")

        # ── the 6th entry of the strip, days 1–3 only ──
        print("== phone · แถบล่าง")
        page.evaluate("supplyGo({tab:'supplyhome'})")
        page.wait_for_timeout(800)
        strip = page.evaluate("[...document.querySelectorAll('#supplyNav button')].map(b => b.innerText.trim())")
        day_now = int(dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).strftime("%d"))
        check(len(strip) == (6 if day_now <= 3 else 5), f"today is day {day_now}: the strip has {len(strip)} entries ({strip})")
        for day, want in (("2026-10-02T09:00:00+07:00", 6), ("2026-10-03T23:30:00+07:00", 6), ("2026-10-04T00:30:00+07:00", 5), ("2026-10-20T12:00:00+07:00", 5)):
            c2 = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="th-TH")
            c2.add_init_script("try{localStorage.setItem('oshinei_upload_token','local-test-token')}catch(e){}")
            c2.request.post(a.base + "/api/auth/login", data={"username": a.user, "password": a.password})
            p2 = c2.new_page()
            p2.clock.set_fixed_time(day)
            p2.goto(a.base + "/", wait_until="networkidle", timeout=120000)
            p2.wait_for_function("typeof supplyGo === 'function' && window.AUTH && window.AUTH.role", timeout=60000)
            p2.wait_for_timeout(800)
            items = p2.evaluate("[...document.querySelectorAll('#supplyNav button')].map(b => b.innerText.trim())")
            check(len(items) == want and (("ตรวจนับ" in items) == (want == 6)), f"{day[:10]} {day[11:16]} (Bangkok): the strip has {len(items)} entries {items}")
            if want == 6 and day.startswith("2026-10-02"):
                p2.click("#supplyNav button:has-text('ตรวจนับ')")
                p2.wait_for_function("document.getElementById('brn-pane-count').style.display !== 'none'", timeout=20000)
                lit = p2.evaluate("(document.querySelector('#supplyNav button.active') || { innerText: '' }).innerText.trim()")
                check(lit == "ตรวจนับ", "the 6th entry opens ตรวจนับสิ้นเดือน and is lit")
                if a.shots:
                    p2.screenshot(path=os.path.join(a.shots, "strip6.png"))
            c2.close()
        check(not errors, "phone: no page error" + (f" — {errors[:2]}" if errors else ""))
        ctx.close()

        # ══════════════ desktop ══════════════
        ctx, page, errors = session(b, False)
        saves2 = []
        page.on("request", lambda rq: saves2.append(rq.url) if "/issues/save" in rq.url and rq.method == "POST" else None)
        print("== desktop 1440")
        page.evaluate("supplyGo({tab:'branchstock', pane:'issue'})")
        page.wait_for_selector(ISSUE_ROW, timeout=60000)
        page.wait_for_timeout(800)
        d = page.evaluate("({ rows: document.querySelectorAll('#brnIssList .brn-row').length, zones: document.querySelectorAll('#brnIssZones button').length, progress: document.getElementById('brnIssProgress').innerText })")
        check(d["rows"] > 0 and d["zones"] > 0 and "กรอกแล้ว" in d["progress"], f"ตัดเบิก lists {d['rows']} rows in {d['zones']} zones · {d['progress']}")
        page.fill("#brnIssList .brn-qty >> nth=0", "1")
        page.wait_for_function("/บันทึกร่างแล้ว \\d\\d:\\d\\d/.test(document.getElementById('brnIssStatus').innerText)", timeout=8000)
        check(len(saves2) == 1, "desktop: the autosave saves the draft")
        page.fill("#brnIssList .brn-qty >> nth=0", "")
        page.wait_for_timeout(3200)
        page.fill("#brnIssQ", "แซลมอน")
        page.wait_for_timeout(300)
        hit = page.evaluate("[...document.querySelectorAll('#brnIssList .brn-name')].every(n => n.textContent.includes('แซลมอน'))")
        check(hit, "desktop: the search lists only matching items (across the zones)")
        shot(page, "desktop_issue.png")
        page.evaluate("supplyGo({tab:'branchstock', pane:'order'})")
        page.wait_for_selector("#brnOrdList .brn-row, #brnOrdList .text-xs", timeout=90000)
        page.wait_for_timeout(1000)
        d = page.evaluate("({ rows: document.querySelectorAll('#brnOrdList .brn-row').length, rounds: document.querySelectorAll('#brnOrdDeliv button').length, buf: !!document.getElementById('brnBufPct') })")
        check(d["rounds"] > 0 and not d["buf"], f"desktop: สั่งสินค้า shows {d['rounds']} rounds, {d['rows']} rows, the buffer folded")
        shot(page, "desktop_order.png")
        # PR-M4 panes still load and answer on a desktop
        page.evaluate("supplyGo({tab:'branchstock', pane:'var'})")
        page.wait_for_selector("#brnVarList .brn-vrow, #brnVarList .text-sm", timeout=60000)
        page.wait_for_timeout(800)
        d = page.evaluate("({ tiles: document.querySelectorAll('#brnVarHead .brn-v3t').length, asks: document.querySelectorAll('#brnVarList .brn-vrow').length, tabs: [...document.querySelectorAll('#brnVarTabs button')].map(b => b.innerText.trim()) })")
        check(d["tiles"] == 3 and d["asks"] > 0 and d["tabs"] == ["รายงวด", "รายวัน", "สิ้นเดือน"], f"desktop: ผลต่าง shows the three numbers and {d['asks']} lines to explain")
        shot(page, "desktop_var.png")
        page.evaluate("supplyGo({tab:'branchstock', pane:'recv'})")
        page.wait_for_selector("#brnClaimList", timeout=30000)
        page.wait_for_timeout(1200)
        d = page.evaluate("({ claims: document.querySelectorAll('#brnClaimList a.brn-claimrow').length, photo: document.getElementById('brnClaimCard').innerText.includes('ส่งรูปทางไลน์ถึงประสานงานขาย') })")
        check(d["claims"] > 0 and d["photo"], f"desktop: รับสินค้า lists {d['claims']} claims without ids and carries the photo line")
        shot(page, "desktop_recv.png")
        page.evaluate("supplyGo({tab:'branchstock', pane:'count'})")
        page.wait_for_selector("#brnList div", timeout=60000)
        page.wait_for_timeout(600)
        check(page.evaluate("[...document.querySelectorAll('#brnZones button')].length > 0"), "desktop: ตรวจนับสิ้นเดือน lists its zones")
        shot(page, "desktop_count.png")
        check(not errors, "desktop: no page error" + (f" — {errors[:2]}" if errors else ""))
        ctx.close()
        m7_checks(a, b, session, check, shot, nums)          # PR-M7: runs last, it confirms today's slip of the scratch copy
        b.close()
    print("numbers:", nums)
    print("PASS" if not bad else f"FAIL ({len(bad)})")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
