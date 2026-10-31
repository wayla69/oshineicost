"""ต้องทำ → ไปทำ (owner 2026-10-31, "กดแล้วทำงานต่อได้เลย").

  * every to-do line of the management report carries `go`, where its work is done: {"kind": "branch", "branch"} (the branch sheet),
    {"kind": "ms", "view", ...} (a detail screen of the phone summary, view = a key of _PH_DETAIL, js/09c-mobile.js) or
    {"kind": "tab", "tab"} (a page of the app); the branch line names its branch, the dish line its dish, the cash-heavy line the branch
    that holds most of it;
  * every price-tier line goes to the price-tier page;
  * the phone: a tap on a ต้องทำ row calls msActionGo(go); the ▾ alone expands the sentence (uiRow2 o.onGo, js/01-core.js).
"""
import re
from pathlib import Path

import core
from routers import reports as R

ROOT = Path(__file__).resolve().parent.parent
MS_VIEWS = {"yesterday", "month", "target", "todo", "watch", "voice", "sales", "branches", "menu", "profit", "discounts"}
FOCUS = {None, "cash_heavy", "heavy", "reprint", "target"}


def _js(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _known(go):
    assert isinstance(go, dict), go
    k = go.get("kind")
    if k == "branch":
        return bool(go.get("branch"))
    if k == "ms":
        return go.get("view") in MS_VIEWS and go.get("focus") in FOCUS
    if k == "tab":
        return bool(go.get("tab"))
    return False


def _mgt(client, **q):
    r = client.get("/api/reports/management", params={"period": "monthly", "lang": "th", **q})
    assert r.status_code == 200, r.text
    return r.json()


def test_every_action_of_the_monthly_payload_has_a_known_go(client):
    for q in ({}, {"mtd": "1"}, {"branch": "BranchA"}, {"month": "2026-05"}):
        for a in _mgt(client, **q)["actions"]:
            assert _known(a.get("go")), a
    for p in ("daily", "weekly"):
        r = client.get("/api/reports/management", params={"period": p, "lang": "en"}).json()
        assert all(_known(a.get("go")) for a in r["actions"])


def _wrap(monkeypatch, name, fn):
    real = getattr(R, name)

    def w(*a, **k):
        out = real(*a, **k)
        fn(out)
        return out
    monkeypatch.setattr(R, name, w)
    core.invalidate_cache("report_management", rebuild_cost=False)   # the report is cached (core.HEAVY_TTL)


def test_the_lines_go_where_their_work_is_done(client, monkeypatch):
    def fell(out):                                     # one branch down 40 %, the chain down 15 %
        b = next(x for x in out["branches"] if (x.get("revenue") or 0) > 0)
        b.update(wow_pct=-40.0, rev_delta=-4000)

    def leak(out):
        out["cash_heavy"] = {"bills": 3, "baht": 900, "top": {"branch": "BranchB", "shift": "เย็น", "bills": 2, "baht": 700, "share_pct": 78}}
        out["heavy"] = dict(out.get("heavy") or {}, bills=2, baht=500)
        out["reprint"] = dict(out.get("reprint") or {}, bills=1, baht=100)

    def head(out):
        h = out["headline"]
        h["prev"] = dict(h.get("prev") or {}, revenue=(h.get("revenue") or 0) * 1.2 + 1)
        h["rev_wow_pct"] = -15.0
    _wrap(monkeypatch, "_mgt_branches", fell)
    _wrap(monkeypatch, "_mgt_leak", leak)
    _wrap(monkeypatch, "_mgt_headline", head)
    A = _mgt(client)["actions"]
    assert all(_known(a.get("go")) for a in A), A
    br = [a for a in A if a["go"]["kind"] == "branch"]
    assert br and all(a["text"].startswith(a["go"]["branch"]) for a in br)
    assert any(a["go"] == {"kind": "ms", "view": "branches"} and "ทั้งเครือ" in a["text"] for a in A)
    ch = [a for a in A if a["go"].get("focus") == "cash_heavy"]
    assert ch and ch[0]["go"] == {"kind": "ms", "view": "discounts", "focus": "cash_heavy", "branch": "BranchB"}
    assert any(a["go"] == {"kind": "ms", "view": "discounts", "focus": "heavy"} for a in A)
    assert any(a["go"] == {"kind": "ms", "view": "discounts", "focus": "reprint"} for a in A)


def test_the_dish_line_carries_the_dish(client, monkeypatch):
    def menu(out):
        out["cost_up"] = [{"menu": "(B) แซลมอนซาชิมิ", "delta": 2.5, "cost_per_head": 9.0, "price_delta": 0.0, "usage_delta": 2.5}]
    _wrap(monkeypatch, "_mgt_menu", menu)
    A = _mgt(client, month="2026-06")["actions"]                 # a month with a month before it (has_prev)
    d = [a for a in A if a["go"].get("view") == "menu"]
    assert d and d[0]["go"] == {"kind": "ms", "view": "menu", "menu": "(B) แซลมอนซาชิมิ"} and "แซลมอนซาชิมิ" in d[0]["text"]


def test_every_management_action_line_in_the_source_sets_go():
    src = (ROOT / "backend" / "routers" / "reports.py").read_text(encoding="utf-8")
    blk = src[src.index("# ---- auto-generated action items"):src.index("# §12 — what the guests said")]
    n = 0
    for m in re.finditer(r"actions(?:\.append\(| = \[)\{", blk):
        n += 1
        assert '"go":' in blk[m.end():m.end() + 260], blk[m.start():m.start() + 160]
    assert n >= 11
    i = src.index("def price_tier_report")
    pt = src[i:src.index("@router", i)]
    assert pt.count("actions.append({") == pt.count('actions.append({"go": _PT_GO,') >= 9
    assert R._PT_GO == {"kind": "tab", "tab": "pricetier"}


def test_the_phone_row_goes_and_the_chevron_expands():
    ms, core = _js("js/09c-mobile.js"), _js("js/01-core.js")
    assert "async function msActionGo(go)" in ms
    body = ms[ms.index("function msActionsHtml(j)"):ms.index("async function msActionGo(go)")]
    assert "onGo: go ? 'msActionGo(JSON.parse(this.dataset.go))'" in body and "data-go=" in body and "_msBrChips(L.branches)" in body
    go = ms[ms.index("async function msActionGo(go)"):ms.index("async function msGoFocus")]
    for k in ("msOpenBranch(go.branch)", "_phShowDetail(v)", "history.pushState({ phd: v }", "_msBranchSort = 'tgt'", "deptOpenPage({ tab: go.tab })"):
        assert k in go, k
    foc = ms[ms.index("async function msGoFocus"):]
    assert "_msNorm(go.menu)" in foc and "uiFlash(" in foc and "#msDiscCards" in foc
    row = core[core.index("function uiRow2(o)"):core.index("function uiRow2Toggle")]
    assert "if (o.onGo)" in row and "uiRow2Chev(this,event)" in row and "uiRow2Skip(event)" in row
    assert "function uiRow2Chev(btn, e)" in core and "function uiFlash(el)" in core
    # existing callers keep their behaviour: without onGo, o.more still expands the whole row
    assert 'const act = o.more ? ` role="button" tabindex="0" aria-expanded="false" onclick="uiRow2Toggle(this,event)"' in row
    css = _js("index.html")
    assert ".ui-hl {" in css and "var(--accent-soft)" in css[css.index(".ui-hl {"):css.index(".ui-hl {") + 200]


def test_the_manual_says_tap_goes_and_the_chevron_reads():
    m = _js("tabs/47-manual.html")
    art = m[m.index('id="mt-mgmt-phone"'):m.index("</article>", m.index('id="mt-mgmt-phone"'))]
    assert "แตะแถว → ไปทำ" in art and "แตะ ▾ → อ่านประโยคเต็ม" in art
    assert "ต้องทำ แตะแล้วไปทำ" in m[m.index("มีอะไรใหม่"):]
