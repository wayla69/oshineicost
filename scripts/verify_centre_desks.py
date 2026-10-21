# -*- coding: utf-8 -*-
"""PR-M5 — check the centre's desks in a real browser: the warehouse queue on a phone, the claims and the slips of Sales
Support, the accounting summary page, the analyst's pages, and the ONE month control of the module.

    py -3.14 scripts\\verify_centre_desks.py --base http://127.0.0.1:8062 [--shots DIR] [--password PW] [--db COPY.db]

Start a scratch server on a COPY of a database first (it never touches the live app), for example from backend/:
    OSHINEI_DB=<copy of a DB> OSHINEI_NO_SWAP=1 OSHINEI_AUTH=1 OSHINEI_AUTH_SECRET=x OSHINEI_SEED_TOKEN=local-test-token \\
        py -3.12 -m uvicorn main:app --port 8062
with the accounts warehouse1 / salessupport1 / analyst1 / accounting1 / operation1 on the default password
(auth.DEFAULT_PASSWORD, read from backend/auth.py unless --password). With --db the script first puts two orders back to
"sent by the branch", a claim back to "waiting", a slip back to a draft in that copy, so there is something to look at.

Warehouse on a phone (390x844): the queue is cards (branch · round · lines · value · chips), not a table; only three status
chips; no date range, no consolidation, no Excel; an opened order is one stacked block per line (name, สั่ง, a 44 px ส่งจริง
box with 16 px text, the reason, the note) and the page does not scroll sideways.
Sales Support (1440): a claim is two lines without ids or logins; the stock effect is one sentence under the table; the slips
pane has no fourth month control, the slip head names no login and no test tag, and a draft shows "เปิดให้สาขาแก้ไข" off with
its reason; ONE month for the tab (label เดือน, Thai text), taken from the choice made on another page of the module and
kept when the chip changes; the default is this month on a working page and the previous month on ปิดงวดเดือน; the branch
selects list the branches that have data in the month.
Accounting: งวด + สาขา, the status chips, the three downloads as one group, the posting button, the reconciliation (count
units, a coloured difference, no formula), the movement table folded; the posting dialog states the lock.
Analyst: check titles with the code as a muted suffix, the recipes columns, the two menus, the register 100 rows a page with a
search, the seed question with three numbers, the price card with two actions and the options folded, the forecast formula line.
"""
import argparse
import datetime as dt
import os
import re
import sqlite3
import sys

from playwright.sync_api import sync_playwright

TH_MONTH = re.compile(r"^(มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม) \d{4}$")
LOGIN_ID = re.compile(r"\b(?:branch|salessupport|warehouse|analyst|accounting|operation|management|innovation)\d\b")


def bkk_month(delta=0):
    d = dt.datetime.now(dt.timezone(dt.timedelta(hours=7)))
    t = d.year * 12 + d.month - 1 + delta
    return f"{t // 12}-{t % 12 + 1:02d}"


def prepare(db):
    """Scratch copies only: two orders sent by the branch, a claim waiting, a slip as a draft."""
    con = sqlite3.connect(db, timeout=120)
    try:
        con.execute("UPDATE bc_order SET status='submitted', processed_by=NULL, processed_at=NULL, po_ref=NULL, received_at=NULL WHERE order_id IN (3, 5)")
        con.execute("UPDATE bc_order_line SET processed_units=NULL, received_units=NULL WHERE order_id IN (3, 5)")
        con.execute("UPDATE bc_claim SET status='submitted', decided_by=NULL, decided_at=NULL, decision_note=NULL WHERE claim_id=1")
        con.execute("UPDATE bc_issue_slip SET status='draft', confirmed_by=NULL, confirmed_at=NULL")
        con.commit()
    finally:
        con.close()


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8062")
    ap.add_argument("--password", default=os.environ.get("OSHINEI_ROLES_PASSWORD", ""))
    ap.add_argument("--shots", default="")
    ap.add_argument("--db", default="", help="the scratch DB copy the server runs on (orders, a claim and a slip are put back to a state worth looking at)")
    a = ap.parse_args(argv)
    if not a.password:
        m = re.search(r'^DEFAULT_PASSWORD\s*=\s*"([^"]+)"', open(os.path.join(os.path.dirname(__file__), "..", "backend", "auth.py"), encoding="utf-8").read(), re.M)
        a.password = m.group(1) if m else ""
    if a.shots:
        os.makedirs(a.shots, exist_ok=True)
    if a.db:
        prepare(a.db)
    bad = []

    def check(ok, what):
        print(("ok  " if ok else "BAD ") + what)
        if not ok:
            bad.append(what)

    def shot(page, name, **kw):
        if a.shots:
            page.screenshot(path=os.path.join(a.shots, name), **kw)

    def session(b, user, phone=False):
        ctx = b.new_context(viewport={"width": 390 if phone else 1440, "height": 844 if phone else 900},
                            is_mobile=phone, has_touch=phone, locale="th-TH")
        ctx.add_init_script("try{localStorage.setItem('oshinei_upload_token','local-test-token')}catch(e){}")
        r = ctx.request.post(a.base + "/api/auth/login", data={"username": user, "password": a.password})
        if not r.ok:
            raise SystemExit(f"login failed for {user} ({r.status})")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(a.base + "/", wait_until="networkidle", timeout=120000)
        page.wait_for_function("typeof supplyGo === 'function' && window.AUTH && window.AUTH.role", timeout=60000)
        page.wait_for_timeout(800)
        return ctx, page, errors

    def go(page, **kw):
        page.evaluate("o => supplyGo(o)", kw)
        page.wait_for_timeout(900)

    def clear_ctx(page):
        page.evaluate("() => { try { localStorage.removeItem('oshinei_supply_ctx:' + window.AUTH.username); } catch (e) {} }")

    def dialog(page):
        return page.evaluate("() => ({ title: document.querySelector('[data-el=title]').textContent, msg: document.querySelector('[data-el=msg]').textContent,"
                             " ok: document.querySelector('[data-el=msg]').closest('form').querySelector('[type=submit]').textContent })")

    def close_dialog(page):
        page.evaluate("() => { const c = document.querySelector('[data-act=cancel]'); if (c) c.click(); }")
        page.wait_for_timeout(200)

    def sel_text(page, sel):
        return page.evaluate("s => { const e = document.querySelector(s); return e && e.selectedOptions[0] ? e.selectedOptions[0].textContent.trim() : null }", sel)

    with sync_playwright() as pw:
        b = pw.chromium.launch()

        # ════════════ warehouse, phone ════════════
        print("== warehouse1 · phone 390x844")
        ctx, page, errors = session(b, "warehouse1", phone=True)
        go(page, tab="orderhub", pane="queue")
        page.wait_for_selector("#ohbQueue .ohb-card", timeout=20000)
        chips = page.eval_on_selector_all("#ohbStatusChips button", "els => els.map(e => e.textContent.trim())")
        check(chips == ["รอยืนยันยอดส่ง", "คลังจัดส่งแล้ว", "ทั้งหมด"], f"three status chips for the warehouse: {chips}")
        check(not page.is_visible("#ohbQCentre"), "no date range, consolidation or Excel controls for the warehouse")
        pane_text = page.inner_text("#ohb-pane-queue")
        check("Excel" not in pane_text and "สรุปรวม" not in pane_text and "คีย์บัญชี" not in pane_text, "the queue pane has no Excel / consolidation / accounting words")
        n_cards = page.locator("#ohbQueue .ohb-card").count()
        check(n_cards >= 2 and page.locator("#ohbQueue table").count() == 0, f"the queue is {n_cards} cards, not a table")
        card = page.inner_text("#ohbQueue .ohb-card >> nth=0")
        check(bool(re.search(r"รอบ|แซลมอน", card)) and "รายการ" in card and "฿" in card and "#" not in card, f"a card holds branch · round · lines · value: {card!r}")
        h = page.evaluate("() => document.querySelector('#ohbQueue .ohb-card').getBoundingClientRect().height")
        check(h >= 56, f"a card is at least 56 px high ({h:.0f})")
        shot(page, "wh_phone_queue.png")
        page.click("#ohbQueue .ohb-card >> nth=0")
        page.wait_for_selector("#ohbDetail .ohb-line", timeout=20000)
        check(page.locator("#ohbDetail table").count() == 0, "the opened order has no table")
        n_lines = page.locator("#ohbDetail .ohb-line").count()
        first = page.evaluate("""() => { const l = document.querySelector('#ohbDetail .ohb-line'); const i = l.querySelector('.ohb-sent');
          const r = i.getBoundingClientRect(), s = l.querySelector('select').getBoundingClientRect();
          return { name: l.querySelector('.ohb-line-n').textContent.length, q: l.querySelector('.ohb-line-q').textContent, h: r.height, fs: getComputedStyle(i).fontSize,
                   right: r.right, sright: s.right, selH: s.height, hasNote: !!l.querySelector('input[id^=ohbN_]'), w: innerWidth, sw: document.documentElement.scrollWidth } }""")
        check(n_lines >= 1 and first["name"] > 0 and "สั่ง" in first["q"], f"{n_lines} stacked blocks: name and สั่ง ({first['q']!r})")
        check(first["h"] >= 44 and first["fs"] == "16px", f"ส่งจริง box is {first['h']:.0f} px high with {first['fs']} text")
        check(first["selH"] >= 44 and first["hasNote"], "the reason select is 44 px and the note box is there")
        check(first["right"] <= first["w"] and first["sright"] <= first["w"] and first["sw"] <= first["w"] + 1, "nothing in the order runs off the right edge")
        page.fill("#ohbDetail .ohb-sent >> nth=0", "abc")
        page.wait_for_timeout(200)
        err = page.inner_text("#ohbDetail .ohb-line-e >> nth=0")
        check("ตัวเลข" in err, f"a word in ส่งจริง shows its note: {err!r}")
        shot(page, "wh_phone_order.png", full_page=True)
        check(not errors, f"no page errors {errors[:2]}")
        ctx.close()

        print("== warehouse1 · desktop 1440")
        ctx, page, errors = session(b, "warehouse1")
        go(page, tab="orderhub", pane="queue")
        page.wait_for_selector("#ohbQueue", timeout=20000)
        chips = page.eval_on_selector_all("#ohbStatusChips button", "els => els.map(e => e.textContent.trim())")
        check(chips == ["รอยืนยันยอดส่ง", "คลังจัดส่งแล้ว", "ทั้งหมด"] and not page.is_visible("#ohbQCentre"), "desktop warehouse: three chips and no centre controls")
        check(page.locator("#ohbQueue .ohb-card").count() == 0, "desktop keeps the rows, not the cards")
        ctx.close()

        # ════════════ Sales Support, desktop ════════════
        print("== salessupport1 · desktop 1440")
        ctx, page, errors = session(b, "salessupport1")
        clear_ctx(page)
        # claims
        go(page, tab="orderhub", pane="claims")
        page.evaluate("ohbClaimSetStatus('all')")
        page.wait_for_selector("#ohbClaimList .ohb-claimrow", timeout=20000)
        rows = page.eval_on_selector_all("#ohbClaimList .ohb-claimrow", "els => els.map(e => ({ l1: e.children[0].textContent.trim(), l2: e.children[1].textContent.replace(/\\s+/g, ' ').trim(), n: e.children.length }))")
        check(all(r["n"] == 2 for r in rows), f"{len(rows)} claim rows, two lines each")
        check(all(re.fullmatch(r".+ · .+ ส่ง \d{1,2}/\d{1,2} · \d+ รายการ", r["l1"]) for r in rows), f"line 1 = branch · round ส่ง d/m · N รายการ: {rows[0]['l1']!r}")
        check(all(("#" not in r["l1"] + r["l2"]) and "โดย" not in r["l1"] + r["l2"] and not LOGIN_ID.search(r["l1"] + r["l2"]) for r in rows), "no id and no login in a claim row")
        check(all(re.search(r"แจ้ง", r["l2"]) and re.search(r"อนุมัติ|รอ|ไม่อนุมัติ|ยกเลิก", r["l2"]) for r in rows), f"line 2 = where it was raised + status: {rows[0]['l2']!r}")
        shot(page, "ss_claims.png")
        page.evaluate("ohbClaimSetStatus('submitted')")
        page.wait_for_selector("#ohbClaimList .ohb-claimrow", timeout=20000)
        page.click("#ohbClaimList .ohb-claimrow >> nth=0")
        page.wait_for_selector("#ohbClaimDetail #ohbClaimEffect", timeout=20000)
        btns = page.eval_on_selector_all("#ohbClaimDetail button", "els => els.map(e => e.textContent.trim())")
        check("อนุมัติ" in btns and "ไม่อนุมัติ" in btns and not any("อนุมัติรับเคลม" in t for t in btns), f"one verb on the buttons: {btns}")
        order = page.evaluate("""() => { const d = document.getElementById('ohbClaimDetail'); const t = d.querySelector('table'), e = d.querySelector('#ohbClaimEffect'), n = d.querySelector('#ohbClaimNote');
          return { afterTable: !!(t.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING), beforeNote: !n || !!(e.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_FOLLOWING), text: e.textContent } }""")
        check(order["afterTable"] and order["beforeNote"] and order["text"].count("เคลมนี้") == 1, f"the stock effect is one sentence under the table: {order['text']!r}")
        page.fill("#ohbClaimNote", "")
        page.evaluate("ohbClaimDecide('reject')")
        page.wait_for_timeout(300)
        check("ระบุเหตุผลที่ไม่อนุมัติ" in page.inner_text("#ohbClaimStatus"), "a reject without a reason is refused in words")
        shot(page, "ss_claim_detail.png")

        # slips (the pane that had a 4th month control)
        clear_ctx(page)
        go(page, tab="orderhub", pane="issues")
        page.wait_for_selector("#ohbIsList", timeout=20000)
        page.wait_for_timeout(800)
        pane = page.inner_text("#ohb-pane-issues")
        check("เดือนของไฟล์ Excel" not in pane and page.locator("#ohb-pane-issues input[type=month]").count() == 0, "no 4th month control in the slips pane")
        month_selects = page.evaluate("() => [...document.querySelectorAll('#tab-orderhub select.supply-month')].filter(e => e.offsetParent !== null).length")
        check(month_selects == 1, f"ONE month control is visible on the tab ({month_selects})")
        labels = page.eval_on_selector_all("#ohb-pane-issues label", "els => els.map(e => e.textContent.replace(/\\s+/g, ' ').trim())")
        check(any(t.startswith("สาขา") for t in labels) and any(t.startswith("ตั้งแต่") for t in labels) and any(t.startswith("ถึง") for t in labels), f"filters: สาขา · ตั้งแต่ · ถึง {labels}")
        check(page.inner_text("#ohbMonthBar label").split()[0] == "เดือน", "the month label is เดือน")
        cur = bkk_month(0)
        check(page.eval_on_selector("#ohbMonth", "e => e.value") == cur and TH_MONTH.match(sel_text(page, "#ohbMonth") or ""),
              f"issues starts at this month, in Thai: {sel_text(page, '#ohbMonth')!r}")
        # the branch list = the branches with data in the month
        api = lambda path: ctx.request.get(a.base + path).json()
        want = sorted(x["name"] for x in api(f"/api/bom-center/stock/me?month={cur}")["branches"])
        have = sorted(page.eval_on_selector_all("#ohbIsBranch option", "els => els.map(e => e.textContent.trim())"))
        have = [t for t in have if t != "ทุกสาขา"]
        check(have == want, f"the branch select lists the branches with data in {cur}: {have}")
        # choose the previous month: the list follows
        prev = bkk_month(-1)
        page.select_option("#ohbMonth", prev)
        page.wait_for_timeout(1200)
        want = sorted(x["name"] for x in api(f"/api/bom-center/stock/me?month={prev}")["branches"])
        have = sorted(t for t in page.eval_on_selector_all("#ohbIsBranch option", "els => els.map(e => e.textContent.trim())") if t != "ทุกสาขา")
        frm = page.eval_on_selector("#ohbIsFrom", "e => e.value")
        check(frm == prev + "-01" and set(want) <= set(have) and set(have) <= set(want) | {x["name"] for x in api(f"/api/bom-center/stock/me?month={cur}")["branches"]},
              f"a new month moves the range to it ({frm}) and the branches with it: {have}")
        # a slip
        page.evaluate("ohbIsSetStatus('all')")
        link = page.locator("#ohbIsList a", has_text="ดูใบตัดเบิก")
        if link.count() == 0:
            page.fill("#ohbIsFrom", cur + "-01"); page.fill("#ohbIsTo", bkk_month(0) + "-28"); page.dispatch_event("#ohbIsTo", "change")
            page.wait_for_timeout(1200)
            link = page.locator("#ohbIsList a", has_text="ดูใบตัดเบิก")
        if link.count():
            link.first.click()
            page.wait_for_selector("#ohbIsDetail table", timeout=20000)
            head = page.inner_text("#ohbIsDetail")
            head_top = head.split("ประวัติของใบ")[0]
            check(not LOGIN_ID.search(head_top) and "ทดสอบ" not in head_top, "the slip head shows no login and no test tag")
            off = page.locator("#ohbIsReopenOff")
            if off.count():
                check(off.is_disabled() and "เปิดให้สาขาแก้ไข" in off.inner_text() and "ใบนี้ยังเป็นร่าง สาขาแก้ไขได้เอง" in page.inner_text("#ohbIsDetail"),
                      "on a draft เปิดให้สาขาแก้ไข is off and says why")
            else:
                check("เปิดให้สาขาแก้ไข" in head, "on a confirmed slip เปิดให้สาขาแก้ไข is there")
            shot(page, "ss_slip.png", full_page=True)
        else:
            print("note: no slip with a detail link in the range — the slip-head checks are skipped")

        # one month for the whole module
        clear_ctx(page)
        go(page, tab="orderhub", pane="central")
        page.wait_for_timeout(900)
        v = page.eval_on_selector("#ohbMonth", "e => e.value")
        check(v == bkk_month(-1), f"ปิดงวดเดือน starts at the previous month ({v})")
        check(page.is_visible("#ohbMonthBar") and "เดือน" in page.inner_text("#ohbMonthBar"), "the month control is on the page")
        go(page, tab="orderhub", pane="issues")
        v = page.eval_on_selector("#ohbMonth", "e => e.value")
        check(v == bkk_month(0), f"the slips pane starts at this month when nobody has chosen ({v})")
        go(page, tab="orderhub", pane="central")
        pick = bkk_month(-3)
        page.select_option("#ohbMonth", pick)
        page.wait_for_timeout(900)
        seen = {}
        for name, kw, sel in (("orderhub issues", dict(tab="orderhub", pane="issues"), "#ohbMonth"), ("bomcenter price", dict(tab="bomcenter", pane="price"), "#bmcMonth"),
                              ("bomcenter forecast", dict(tab="bomcenter", pane="fc"), "#bmcMonth"), ("home", dict(tab="supplyhome"), "#shmMonth")):
            go(page, **kw)
            page.wait_for_timeout(500)
            seen[name] = (page.eval_on_selector(sel, "e => e.value"), sel_text(page, sel), page.evaluate("s => (document.querySelector(s).closest('label') || {}).textContent || ''", sel).strip().split("\n")[0].split()[0])
        check(all(v[0] == pick for v in seen.values()), f"the chosen month ({pick}) is the month on every page: {[(k, v[0]) for k, v in seen.items()]}")
        check(all(TH_MONTH.match(v[1] or "") for v in seen.values()), f"shown in Thai: {[v[1] for v in seen.values()]}")
        check(all(v[2] == "เดือน" for v in seen.values()), f"labelled เดือน everywhere: {[v[2] for v in seen.values()]}")
        # switching a chip on the same tab keeps it
        go(page, tab="bomcenter", pane="price")
        before = page.eval_on_selector("#bmcMonth", "e => e.value")
        go(page, tab="bomcenter", pane="ing")
        go(page, tab="bomcenter", pane="fc")
        check(page.eval_on_selector("#bmcMonth", "e => e.value") == before == pick, "the value is kept when the chip changes")
        # home: the branch select lists the branches with data in the month
        go(page, tab="supplyhome")
        page.wait_for_timeout(900)
        m = page.eval_on_selector("#shmMonth", "e => e.value")
        want = sorted(x["name"] for x in api(f"/api/bom-center/stock/me?month={m}")["branches"])
        have = sorted(t for t in page.eval_on_selector_all("#shmBranch option", "els => els.map(e => e.textContent.trim())") if t != "ทุกสาขา")
        check(have == want, f"หน้างาน lists the branches with data in {m}: {have}")
        check(not errors, f"no page errors {errors[:2]}")
        ctx.close()

        # ════════════ Operations ════════════
        print("== operation1 · desktop")
        ctx, page, errors = session(b, "operation1")
        clear_ctx(page)
        go(page, tab="orderhub", pane="issuevar")
        page.wait_for_timeout(1200)
        m = page.eval_on_selector("#ohbMonth", "e => e.value")
        want = sorted(x["name"] for x in ctx.request.get(a.base + f"/api/bom-center/stock/me?month={m}").json()["branches"])
        have = sorted(t for t in page.eval_on_selector_all("#ohbIvBranch option", "els => els.map(e => e.textContent.trim())") if not t.startswith("—"))
        check(m == bkk_month(0) and have == want, f"ผลต่างตัดเบิกรายวัน: this month, branches with data only {have}")
        check(page.locator("#ohb-pane-issuevar input[type=month]").count() == 0, "no second month control on the page")
        check(not errors, f"no page errors {errors[:2]}")
        ctx.close()

        # ════════════ Accounting ════════════
        print("== accounting1 · desktop")
        ctx, page, errors = session(b, "accounting1")
        clear_ctx(page)
        go(page, tab="bomcenter", pane="issue")
        page.wait_for_selector("#bmcIssFilter button", timeout=30000)
        page.wait_for_timeout(1500)
        # PR-R: the page is the month overview (one row per branch); the detail of a period is in a drawer
        order = page.evaluate("""() => { const ids = ['bmcIssMonth', 'bmcIssFilter', 'bmcIssAllBtn', 'bmcIssList'];
          const el = ids.map(i => document.getElementById(i)); const ok = [];
          for (let i = 1; i < el.length; i++) ok.push(!!(el[i - 1].compareDocumentPosition(el[i]) & Node.DOCUMENT_POSITION_FOLLOWING));
          return ok } """)
        check(all(order), f"page order: ปิดงวดเดือน · filter chips · month download · the table {order}")
        mopt = sel_text(page, "#bmcIssMonth") or ""
        check(bool(re.fullmatch(r"(มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม) \d{4}", mopt)), f"month option: {mopt!r}")
        chips = [re.sub(r"\s*\(?\d+\)?$", "", t) for t in page.eval_on_selector_all("#bmcIssFilter button", "els => els.map(e => e.textContent.trim())")]
        check(chips == ["ยังไม่เสร็จ", "พร้อมบันทึก", "รอสาขายืนยัน", "บันทึกแล้ว", "ทั้งหมด"], f"the filter chips: {chips}")
        check(page.evaluate("() => document.querySelector('#bmcIssFilter button[aria-pressed=true]').textContent.trim().startsWith('ยังไม่เสร็จ')"), "the default filter is ยังไม่เสร็จ")
        rows = page.eval_on_selector_all("#bmcIssList tbody tr", "els => els.map(e => e.textContent.replace(/\\s+/g, ' ').trim())")
        check(not any("บันทึกแล้ว" in r for r in rows), "finished (posted) rows are out of the default view")
        post = page.locator("#bmcIssList .wt-btn.is-primary")
        if post.count():
            check(post.first.inner_text().strip() == "บันทึกเข้าระบบบัญชีแล้ว", "a ready row has the primary button บันทึกเข้าระบบบัญชีแล้ว")
            post.first.click()
            page.wait_for_timeout(300)
            d = dialog(page)
            check("ล็อก" in d["msg"] and d["ok"] == "บันทึกเข้าระบบบัญชีแล้ว", f"the dialog states the lock: {d['msg'][:80]!r}")
            close_dialog(page)
        outside = page.evaluate("() => [...document.querySelectorAll('#bmc-pane-issue > *:not(.iss-drawer):not(.iss-back):not(style)')].map(e => e.innerText).join('\\n')")
        check(not re.search(r"ยกมา[^\n]*\+[^\n]*[−-]|ผลต่าง =", outside), "no formula on the page (it is behind the ?)")
        page.locator("#bmcIssList .iss-branch").first.click()
        page.wait_for_selector("#bmcIssRecon", state="visible", timeout=30000)
        page.wait_for_timeout(1500)
        recon = page.inner_text("#bmcIssRecon")
        check("คงเหลือตามระบบ" in recon and "นับจริง" in recon and not re.search(r"ยกมา[^\n]*\+[^\n]*−", recon), "the drawer's reconciliation has no movement formula")
        check(page.evaluate("() => { const d = document.getElementById('bmcIssMoveBox'); return d.tagName === 'DETAILS' && !d.open }"), "the movement table is folded")
        check(page.locator("#bmcIssDownloads button").count() >= 2, "the drawer keeps the period and month-slips downloads")
        shot(page, "acc_issue_drawer.png")
        page.keyboard.press("Escape")
        check(not page.is_visible("#bmcIssDrawer"), "Esc closes the drawer")
        shot(page, "acc_issue.png", full_page=True)
        check(not errors, f"no page errors {errors[:2]}")
        ctx.close()

        # ════════════ Analyst ════════════
        print("== analyst1 · desktop")
        ctx, page, errors = session(b, "analyst1")
        clear_ctx(page)
        go(page, tab="bomcenter", pane="rel")
        page.wait_for_timeout(2500)
        groups = page.eval_on_selector_all("#bmcRlChecks details[data-check]", "els => els.map(e => { const s = e.querySelector('summary'); return { title: s.querySelector('b').textContent.trim(), code: (s.querySelector('.bmc-code') || {}).textContent, text: s.textContent.replace(/\\s+/g, ' ').trim() } })")
        if groups:
            check(all(g["code"] and re.fullmatch(r"· (C\d\d|UNM)", g["code"].strip()) for g in groups), f"{len(groups)} check groups, the code is a muted suffix: {groups[0]['text']!r}")
            check(all(not re.match(r"^(C\d\d|UNM)\b", g["title"]) and len(g["title"]) > 8 for g in groups), "the title is plain words, not a code")
            c0 = page.evaluate("() => getComputedStyle(document.querySelector('#bmcRlChecks .bmc-code')).color + '|' + getComputedStyle(document.querySelector('#bmcRlChecks details[data-check] summary b')).color")
            check(c0.split("|")[0] != c0.split("|")[1], f"the code is muted against the title ({c0})")
        else:
            print("note: this release has no check rows — the title checks are skipped")
        shot(page, "an_release.png", full_page=True)

        go(page, tab="bomcenter", pane="rec")
        page.wait_for_selector("#bmcRecKpis button", timeout=30000)
        page.wait_for_timeout(1500)
        # PR-R: a step-led page: version card (four steps + the next action), four tiles, one work card with tabs
        steps = page.eval_on_selector_all("#bmcRecSteps li.rec-step", "els => els.map(e => e.textContent.replace(/\\s+/g, ' ').trim())")
        check(len(steps) in (0, 4), f"the four steps (or none for the old system's version): {steps}")
        check(page.locator("#bmcRecKpis button").count() == 4, "four tiles")
        tabs = [re.sub(r"\s*\d+$", "", t) for t in page.eval_on_selector_all("#bmcRecTabs button", "els => els.map(e => e.textContent.trim())")]
        check(tabs == ["ยังไม่จับคู่", "ต้องตรวจ", "ทุกเมนู"], f"the work tabs: {tabs}")
        page.click("#bmcRecTabs button:has-text('ทุกเมนู')")
        page.wait_for_selector("#bmcRecWork thead th", timeout=10000)
        heads = page.eval_on_selector_all("#bmcRecWork thead th", "els => els.map(e => e.textContent.trim())")
        i = heads.index("ต้นทุน/เสิร์ฟ") if "ต้นทุน/เสิร์ฟ" in heads else -1
        check(i >= 0 and heads[i:i + 4] == ["ต้นทุน/เสิร์ฟ", "ตามไฟล์ครัว", "ระบบเดิม", "ต่าง %"], f"recipe columns: {heads}")
        menus = page.eval_on_selector_all("#bmc-pane-rec details.bmc-menu > summary", "els => els.map(e => e.textContent.trim())")
        check(menus == ["นำเข้าไฟล์ ▾", "ดาวน์โหลด ▾"], f"the header actions are two menus: {menus}")
        page.click("#bmcRecImportMenu > summary")
        t = page.inner_text("#bmcRecImportMenu .bmc-menu-pop")
        check("ไฟล์ครัว Cost-RW" in t and "ไฟล์สูตรตามระดับราคา" in t and "สูตรกึ่งสำเร็จ" in t, "นำเข้าไฟล์ holds ไฟล์ครัว Cost-RW · ไฟล์สูตรตามระดับราคา · สูตรกึ่งสำเร็จ")
        check(TH_MONTH.match(sel_text(page, "#bmcCrwEff") or "") is not None, f"the effective month is a Thai month list: {sel_text(page, '#bmcCrwEff')!r}")
        shot(page, "an_recipes_menu.png")
        page.click("#bmcRecDownloadMenu > summary")
        check(not page.evaluate("() => document.getElementById('bmcRecImportMenu').open"), "opening the other menu closes the first")
        t = page.inner_text("#bmcRecDownloadMenu .bmc-menu-pop")
        check("แม่แบบสูตรตามระดับราคา" in t and "สูตรตามระดับราคา (Excel)" in t, "ดาวน์โหลด holds แม่แบบ · สูตรตามระดับราคา (Excel)")

        go(page, tab="bomcenter", pane="ing")
        page.wait_for_selector("#bmcIngBody tr", timeout=30000)
        n_rows = page.locator("#bmcIngBody tr").count()
        total = page.evaluate("() => _bmcIng.length")
        check(n_rows == min(100, total), f"the register shows {n_rows} rows of {total}")
        if total > 100:
            pager = page.inner_text("#bmcIngPager")
            check(f"แสดง 1–100 จาก {total} รายการ" in pager and "หน้า 1 จาก" in pager, f"pager: {pager!r}")
            first = page.inner_text("#bmcIngBody tr >> nth=0 >> td >> nth=0")
            page.click("#bmcIngPager >> text=ถัดไป")
            page.wait_for_timeout(300)
            check(page.inner_text("#bmcIngBody tr >> nth=0 >> td >> nth=0") != first, "the next page shows other rows")
            page.click("#bmcIngPager >> text=ก่อนหน้า")
        code = page.inner_text("#bmcIngBody tr >> nth=0 >> td >> nth=0").split("\n")[0].strip()
        page.fill("#bmcIngQ", code)
        page.wait_for_timeout(300)
        check(page.locator("#bmcIngBody tr").count() >= 1 and page.locator("#bmcIngBody tr").count() < max(2, total), "the search narrows all rows, not only the page")
        page.fill("#bmcIngQ", "")
        page.click("text=เติมจากข้อมูลในระบบ >> nth=0")
        page.wait_for_selector("[data-el=msg]", timeout=60000)
        d = dialog(page)
        check(bool(re.search(r"เพิ่มรหัสใหม่ \d+ · เติมช่องว่าง \d+ · ไม่เปลี่ยน \d+ รายการ — ดำเนินการต่อหรือไม่\?", d["msg"])), f"the seed dialog is a question with three numbers: {d['msg']!r}")
        close_dialog(page)

        go(page, tab="bomcenter", pane="price")
        page.wait_for_timeout(1200)
        check(page.locator("#bmc-pane-price label:has-text('นำเข้าไฟล์')").first.is_visible() and page.locator("#bmc-pane-price button:has-text('ยกราคาเดือนก่อนมาใช้')").first.is_visible(),
              "the import card shows two actions: นำเข้าไฟล์ and ยกราคาเดือนก่อนมาใช้")
        opts_open = page.evaluate("() => document.querySelector('#bmc-pane-price details.bmc-opts').open")
        check(not opts_open and not page.is_visible("#bmcAddUnknown") and not page.is_visible("#bmcReqMonth"), "the rest is folded under ตัวเลือก")
        page.click("#bmc-pane-price details.bmc-opts > summary")
        check(page.is_visible("#bmcAddUnknown") and page.is_visible("#bmcReqMonth") and page.is_visible("text=ดาวน์โหลดแม่แบบยอดซื้อรายบรรทัด"), "ตัวเลือก holds the report month, the checkbox and the template link")
        shot(page, "an_price.png")

        go(page, tab="bomcenter", pane="fc")
        page.wait_for_selector("#bmcFcBranch option", state="attached", timeout=30000)
        page.click("text=คำนวณยอดแนะนำ")
        page.wait_for_selector("#bmcFcFormula", timeout=120000)
        f = page.inner_text("#bmcFcFormula")
        check(bool(re.match(r"ยอดแนะนำ = ยอดขาย \d+ สัปดาห์ × ปฏิทิน ×\d+\.\d\d × ปีก่อน ×\d+\.\d\d", f)), f"the forecast line: {f!r}")
        head = page.inner_text("#bmcFcHead")
        check(not re.search(r"\([^()]*\(", head), "no nested parentheses in the forecast head")
        shot(page, "an_forecast.png")
        check(not errors, f"no page errors {errors[:2]}")
        ctx.close()
        b.close()

    print()
    print("ALL OK" if not bad else f"{len(bad)} FAILED:\n  " + "\n  ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
