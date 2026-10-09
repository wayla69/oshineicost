# -*- coding: utf-8 -*-
"""Check that a pinned login (role branch / franchise) sees no branch selector on any supply page.

    py -3.14 scripts\\verify_pinned.py --base http://127.0.0.1:8047 --user branch1 --password <password> --other ขอนแก่น

Logs in with the given account, walks หน้างาน and every page of สาขาของฉัน (a franchise owner: its one page ร้านของฉัน,
and the sidebar must hold exactly that one entry), and fails (exit 1) when any <select>
in the page lists the --other branch, a branch picker element exists, the shell's branch filter is still a
<select>, or the branch-list endpoints (/api/branches, /api/bom-center/stock/me) were requested. The same
checks run in backend/test_pinned_no_branch_select.py wherever the Playwright client is installed (Playwright
for Python is installed for py -3.14 only on this PC). The password is read from the option or
OSHINEI_PINNED_PASSWORD; it is never printed.
"""
import argparse
import os
import re
import sys

from playwright.sync_api import sync_playwright

PANES = [("supplyhome", None), ("branchstock", "issue"), ("branchstock", "order"), ("branchstock", "recv"),
         ("branchstock", "var"), ("branchstock", "count")]
CHECK = """(other) => ({
  naming: [...document.querySelectorAll('select')].filter(s => [...s.options].some(o => o.textContent.includes(other))).map(s => s.id || '(select)'),
  pickers: [...document.querySelectorAll('select[data-branch-picker], #brnBranch, #ohbIvBranch, #ohbIsBranch, #ohbCntBranch, #bmcIssBranch, #bmcFcBranch, #shmBranch')].map(e => e.id),
  branchIds: [...document.querySelectorAll('#tab-supplyhome select, #tab-bomcenter select, #tab-orderhub select, #tab-branchstock select, #tab-ordering select, #tab-ingpurchase select, #tab-ordersetup select')].map(s => s.id).filter(i => /branch/i.test(i)),   // the supply pages' own selects
  shell: (document.getElementById('selBranch') || {}).tagName || null,
  selects: document.querySelectorAll('select').length })"""


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8047")
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", default=os.environ.get("OSHINEI_PINNED_PASSWORD", ""))
    ap.add_argument("--other", required=True, help="the name of another branch: no select may list it")
    ap.add_argument("--width", type=int, default=390)
    a = ap.parse_args(argv)
    if not a.password:
        m = re.search(r'^DEFAULT_PASSWORD\s*=\s*"([^"]+)"', open(os.path.join(os.path.dirname(__file__), "..", "backend", "auth.py"), encoding="utf-8").read(), re.M)
        a.password = m.group(1) if m else ""
    bad = 0
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            b = p.chromium.launch(headless=True)
        ctx = b.new_context(viewport={"width": a.width, "height": 844}, is_mobile=a.width < 700, has_touch=a.width < 700)
        r = ctx.request.post(a.base + "/api/auth/login", data={"username": a.user, "password": a.password})
        if not r.ok:
            print(f"login failed ({r.status})")
            return 2
        page = ctx.new_page()
        seen, errors = [], []
        page.on("request", lambda rq: seen.append(rq.url))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(a.base + "/", wait_until="networkidle", timeout=90000)
        page.wait_for_function("typeof supplyGo === 'function' && window.AUTH && window.AUTH.role", timeout=30000)
        role = page.evaluate("window.AUTH.role")
        page.wait_for_timeout(1000)
        for tab, pane in ([("franchise", None)] if role == "franchise" else PANES):
            if role == "franchise":
                page.evaluate("switchTab('franchise')")
            else:
                page.evaluate("([t, p]) => supplyGo({tab: t, pane: p})", [tab, pane])
            page.wait_for_timeout(2500 if role == "franchise" else 1500)
            res = page.evaluate(CHECK, a.other)
            ok = not res["naming"] and not res["pickers"] and not res["branchIds"] and res["shell"] == "INPUT"
            if role == "franchise":      # PR-L: the sidebar holds exactly the one entry, and its page is the one on show
                nav = page.evaluate("[...document.querySelectorAll('.sidebar-nav .nav-item')].filter(e => getComputedStyle(e).display !== 'none').map(e => e.id)")
                ok = ok and nav == ["nav-franchise"] and page.evaluate("document.getElementById('tab-franchise').style.display") != "none"
                print(f"   sidebar entries: {nav}")
            bad += 0 if ok else 1
            print(f"{'ok ' if ok else 'BAD'} {tab}/{pane or '-'}: selects on page={res['selects']} naming-other={res['naming']} pickers={res['pickers']} branch-ids={res['branchIds']} shell-filter={res['shell']}")
        forbidden = [u for u in seen if re.search(r"/api/branches(\?|$)|/stock/me", u)]
        print(f"role={role} branch-list requests: {len(forbidden)} {forbidden[:3]} · page errors: {errors[:3]}")
        bad += len(forbidden)
        b.close()
    print("PASS" if not bad else f"FAIL ({bad})")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
