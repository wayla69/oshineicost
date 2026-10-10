# -*- coding: utf-8 -*-
"""PR-M2 — check the role-scoped bar and the folded help of วัตถุดิบและใบสั่งซื้อ in a real browser.

    py -3.14 scripts\\verify_roles_nav.py --base http://127.0.0.1:8056 [--shots DIR] [--password PW]

Start a scratch server on a COPY of a database first (it never touches the live app), for example from backend/:
    OSHINEI_DB=<copy of a DB> OSHINEI_NO_SWAP=1 OSHINEI_AUTH=1 OSHINEI_AUTH_SECRET=x OSHINEI_SEED_TOKEN=local-test-token \\
        py -3.12 -m uvicorn main:app --port 8056
with the accounts branch1 / warehouse1 / salessupport1 / analyst1 / innovation1 / accounting1 / operation1 / management1
on the default password (auth.DEFAULT_PASSWORD, read from backend/auth.py unless --password).

Per role it logs in, and
  * reads the chips the bar offers (the real code path: SUPPLY_CHIPS through _supplyChipAllowed / _supplyItemAllowed) and
    compares them with the plan; the DOM of #clusterBar must show the same level-1 chips;
  * opens every page the bar offers and asserts that every .bmc-help is closed, and that no block of text above the page's
    first control is longer than 160 characters;
  * takes a screenshot of หน้างาน and of the bar (DIR/<role>_home.png, <role>_bar.png);
and asserts that no page error was raised. (The titles of the new questions are checked by hand on the scratch server.)
"""
import argparse
import os
import re
import sys

from playwright.sync_api import sync_playwright

HOME = "งานของฉันและปิดงวดเดือน"
PILOT = "สาขานำร่อง 4 สาขา"
LEGACY = ["แผนสั่งวัตถุดิบ (ระบบเดิม)", "ยอดซื้อเทียบยอดขาย", "ขนาดบรรจุ", "ปฏิทินเทศกาล"]
EVERYTHING = {
    "หน้างาน": [HOME, PILOT],
    # PR-M7: งานประจำเดือน (the monthly routine, in the order it is done) is split from ข้อมูลตั้งต้น (set up rarely); the bar's order is this dict's order
    "งานประจำเดือน": ["ไฟล์ราคา", "ราคารายเดือน", "สูตรตามระดับราคา", "สูตรกึ่งสำเร็จ", "ตรวจและประกาศใช้"],
    "ใบสั่งซื้อ": ["คิวใบสั่งซื้อ", "เคลมสินค้า", "ยอดแนะนำรายรอบ", "ตรวจสอบการสั่ง", "ใบตัดเบิกสาขา", "ผลต่างตัดเบิกรายวัน"],
    "ปิดงวดเดือน": ["นำเข้าข้อมูลปิดงวด", "สรุปผลต่างรายเดือน", "ใบสรุปตัดเบิก"],
    "ข้อมูลตั้งต้น": ["ทะเบียนวัตถุดิบ", "หน่วยและการแปลง", "ความพร้อมและรหัสสูตรเดิม"],
    "สาขาของฉัน": ["ตัดเบิก", "สั่งสินค้า", "รับสินค้า", "ผลต่างการใช้", "ตรวจนับสิ้นเดือน"],
    "ตั้งค่า": ["รอบส่งของสาขา", "โซนจัดเก็บ"] + LEGACY,
}
EXPECTED = {
    "branch": ("branch1", {"หน้างาน": [HOME], "สาขาของฉัน": ["ตัดเบิก", "สั่งสินค้า", "รับสินค้า", "ผลต่างการใช้", "ตรวจนับสิ้นเดือน"]}),
    "warehouse": ("warehouse1", {"หน้างาน": [HOME], "ใบสั่งซื้อ": ["คิวใบสั่งซื้อ"]}),
    "salessupport": ("salessupport1", {
        "หน้างาน": [HOME, PILOT], "ข้อมูลตั้งต้น": ["ไฟล์ราคา"],
        "ใบสั่งซื้อ": ["คิวใบสั่งซื้อ", "เคลมสินค้า", "ยอดแนะนำรายรอบ", "ใบตัดเบิกสาขา", "ผลต่างตัดเบิกรายวัน"],
        "ปิดงวดเดือน": ["นำเข้าข้อมูลปิดงวด", "สรุปผลต่างรายเดือน"], "ตั้งค่า": ["รอบส่งของสาขา", "โซนจัดเก็บ"] + LEGACY}),
    "analyst": ("analyst1", {**EVERYTHING, "ตั้งค่า": ["รอบส่งของสาขา", "โซนจัดเก็บ", "ยอดซื้อเทียบยอดขาย"]}),
    "innovation": ("innovation1", {"หน้างาน": [HOME], "ข้อมูลตั้งต้น": ["ทะเบียนวัตถุดิบ"], "สูตรและการประกาศใช้": ["สูตรตามระดับราคา", "สูตรกึ่งสำเร็จ"]}),
    "accounting": ("accounting1", {"หน้างาน": [HOME], "ข้อมูลตั้งต้น": ["ราคารายเดือน"], "ปิดงวดเดือน": ["ใบสรุปตัดเบิก"]}),
    "operation": ("operation1", {"หน้างาน": [HOME], "ใบสั่งซื้อ": ["ตรวจสอบการสั่ง", "ใบตัดเบิกสาขา", "ผลต่างตัดเบิกรายวัน"], "ปิดงวดเดือน": ["สรุปผลต่างรายเดือน"]}),
    "management": ("management1", EVERYTHING),
}
# the first row of #clusterBar per role (a chip with one page is named by the page; a role with 5 pages or fewer gets a flat row)
LEVEL1 = {
    "branch": ["หน้างาน", "สาขาของฉัน"],
    "warehouse": ["หน้างาน", "คิวใบสั่งซื้อ"],
    "salessupport": ["หน้างาน", "ไฟล์ราคา", "ใบสั่งซื้อ", "ปิดงวดเดือน", "ตั้งค่า"],
    "analyst": list(EVERYTHING),
    "innovation": ["หน้างาน", "ทะเบียนวัตถุดิบ", "สูตรตามระดับราคา", "สูตรกึ่งสำเร็จ"],
    "accounting": ["หน้างาน", "ราคารายเดือน", "ใบสรุปตัดเบิก"],
    "operation": ["หน้างาน", "ตรวจสอบการสั่ง", "ใบตัดเบิกสาขา", "ผลต่างตัดเบิกรายวัน", "สรุปผลต่างรายเดือน"],
    "management": list(EVERYTHING),
}
PANE_ROOT = {"supplyhome": "#tab-supplyhome", "bomcenter": "#bmc-pane-%s", "orderhub": "#ohb-pane-%s", "branchstock": "#brn-pane-%s"}

BAR_JS = """() => ({
  role: window.AUTH && window.AUTH.role,
  chips: SUPPLY_CHIPS.filter(_supplyChipAllowed).map(c => [c.label, c.items.filter(_supplyItemAllowed).map(i => i.label)]),
  items: SUPPLY_CHIPS.filter(_supplyChipAllowed).flatMap(c => c.items.filter(_supplyItemAllowed).map(i => [i.tab, i.pane || null, i.label])) })"""

# folded help and the text above the first control of one page
PANE_JS = """(sel) => {
  const root = document.querySelector(sel);
  if (!root) return { missing: true };
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const inHelp = e => !!e.closest('.bmc-help');
  const helps = [...root.querySelectorAll('.bmc-help')].map(h => h.open);
  const controls = [...root.querySelectorAll('input:not([type=hidden]),select,textarea,button,a[onclick],label[style*="cursor:pointer"]')].filter(e => !inHelp(e) && vis(e));
  const first = controls[0] || null;
  const long = [];
  for (const e of root.querySelectorAll('p,div,span')) {
    if (inHelp(e) || !vis(e)) continue;
    if (first && !(first.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING) ) { /* before or around the first control */ }
    if (first && (e.contains(first) || (first.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING))) continue;
    if (e.querySelector('div,p,table,ul,input,select,button,textarea')) continue;       // a leaf block of text only
    const t = (e.innerText || '').trim();
    if (t.length > 160) long.push(t.slice(0, 60) + '… (' + t.length + ')');
  }
  return { helps, firstControl: first ? (first.id || first.tagName) : null, long };
}"""

DIALOG_JS = "() => { const d = document.querySelector('[role=dialog]'); return d ? { title: d.querySelector('[data-el=title]').textContent, msg: d.querySelector('[data-el=msg]').textContent, ok: d.querySelector('[type=submit]').textContent } : null; }"


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8056")
    ap.add_argument("--password", default=os.environ.get("OSHINEI_ROLES_PASSWORD", ""))
    ap.add_argument("--shots", default="")
    ap.add_argument("--token", default="local-test-token")
    ap.add_argument("--roles", default=",".join(EXPECTED), help="comma list of roles to check")
    a = ap.parse_args(argv)
    if not a.password:
        m = re.search(r'^DEFAULT_PASSWORD\s*=\s*"([^"]+)"', open(os.path.join(os.path.dirname(__file__), "..", "backend", "auth.py"), encoding="utf-8").read(), re.M)
        a.password = m.group(1) if m else ""
    if a.shots:
        os.makedirs(a.shots, exist_ok=True)
    bad = []

    def check(ok, what):
        print(("ok  " if ok else "BAD ") + what)
        if not ok:
            bad.append(what)

    with sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            b = p.chromium.launch(headless=True)
        for role in [r for r in a.roles.split(",") if r in EXPECTED]:
            user, expected = EXPECTED[role]
            phone = role == "branch"
            ctx = b.new_context(viewport={"width": 390 if phone else 1440, "height": 844 if phone else 900},
                                is_mobile=phone, has_touch=phone, locale="th-TH")
            ctx.add_init_script(f"try{{localStorage.setItem('oshinei_upload_token','{a.token}')}}catch(e){{}}")
            r = ctx.request.post(a.base + "/api/auth/login", data={"username": user, "password": a.password})
            if not r.ok:
                check(False, f"{role}: login failed ({r.status})")
                ctx.close()
                continue
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(a.base + "/", wait_until="networkidle", timeout=120000)
            page.wait_for_function("typeof supplyGo === 'function' && window.AUTH && window.AUTH.role", timeout=60000)
            page.wait_for_timeout(1200)
            print(f"== {role} ({user})")
            bar = page.evaluate(BAR_JS)
            got = {k: v for k, v in bar["chips"]}
            check(got == expected, f"{role}: the bar offers {[(k, len(v)) for k, v in bar['chips']]}" + ("" if got == expected else f" · expected {[(k, len(v)) for k, v in expected.items()]}"))
            page.evaluate("supplyOpen()")
            page.wait_for_timeout(2500)
            level1 = page.evaluate("[...document.querySelectorAll('#clusterBar > button')].map(b => b.textContent.trim())")
            check(level1 == LEVEL1[role], f"{role}: the first row of #clusterBar = {level1}")
            if a.shots:
                page.screenshot(path=os.path.join(a.shots, f"{role}_home.png"), full_page=True)
                el = page.query_selector("#clusterBar")
                if el and el.is_visible():
                    el.screenshot(path=os.path.join(a.shots, f"{role}_bar.png"))
            # every page of the bar: help closed, nothing long above the first control
            for tab, pane, label in bar["items"]:
                if tab not in PANE_ROOT:
                    continue
                page.evaluate("([t, p]) => supplyGo({tab: t, pane: p})", [tab, pane])
                page.wait_for_timeout(1400)
                sel = PANE_ROOT[tab] % pane if "%s" in PANE_ROOT[tab] else PANE_ROOT[tab]
                res = page.evaluate(PANE_JS, sel)
                if res.get("missing"):
                    check(False, f"{role} · {label}: pane {sel} not found")
                    continue
                check(all(h is False for h in res["helps"]), f"{role} · {label}: {len(res['helps'])} help block(s), all closed" if res["helps"] else f"{role} · {label}: (no help block)")
                check(not res["long"], f"{role} · {label}: nothing above the first control ({res['firstControl']}) is longer than 160 characters" + (f" — {res['long']}" if res["long"] else ""))
            if role == "management":
                # the legacy group: one button in ตั้งค่า, its four pages in a row of their own
                page.evaluate("supplyGo({tab:'bomcenter', pane:'cal'})")
                page.wait_for_timeout(1000)
                btn = page.query_selector("#supplyLegacyBtn")
                check(btn is not None and "หน้าระบบเดิม" in btn.inner_text(), "management: ตั้งค่า has the one group หน้าระบบเดิม")
                if btn:
                    btn.click()
                    page.wait_for_timeout(300)
                    row = page.evaluate("[...document.querySelectorAll('#supplyLegacyRow button')].map(b => b.textContent.trim())")
                    check(row == LEGACY, f"management: the group holds {row}")
            check(not errors, f"{role}: no page error" + (f" — {errors[:2]}" if errors else ""))
            ctx.close()
        b.close()
    print("PASS" if not bad else f"FAIL ({len(bad)})")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
