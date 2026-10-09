# -*- coding: utf-8 -*-
"""PR-M3 — check the branch's daily pages (ตัดเบิก · สั่งสินค้า) in a real browser, on a phone and on a desktop.

    py -3.14 scripts\\verify_branch_phone.py --base http://127.0.0.1:8058 [--shots DIR] [--password PW]

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
Desktop (1440): the same pages still list, save and ask.
"""
import argparse
import datetime as dt
import os
import re
import sys

from playwright.sync_api import sync_playwright

ISSUE_ROW = "#brnIssList .brn-row"


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8058")
    ap.add_argument("--password", default=os.environ.get("OSHINEI_ROLES_PASSWORD", ""))
    ap.add_argument("--shots", default="")
    ap.add_argument("--user", default="branch1")
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
        check(not errors, "desktop: no page error" + (f" — {errors[:2]}" if errors else ""))
        ctx.close()
        b.close()
    print("numbers:", nums)
    print("PASS" if not bad else f"FAIL ({len(bad)})")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
