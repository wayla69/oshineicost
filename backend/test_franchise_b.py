"""Phone B for the franchise owner (owner 2026-10-31, "เริ่มเลย"): ร้านของฉัน = overview cards, a card opens its detail screen.

  * franchise_findings (routers/franchise.py) - the ควรดู list of ONE branch from the figures the page already has: every kind carries
    a `go` (the detail screen and the element it lights), the thresholds are the shared ones (2.5 % ceiling via the controls' flags,
    void bands 0.5 / 1 %, food cost against the ONE target, rules.BELOW_NORMAL_PCT), a clean month gives an empty list;
  * GET /api/franchise/overview - the owner's own branch only (another branch_id is 403), the findings name only this branch, the
    route is read-only (no write rule opened) and the owner's one write path (messages to head office) is unchanged;
  * the page (js/17-franchise.js, tabs/56-franchise.html): the card ids, the findings drawn with uiRow2 + onGo, a card with nothing
    to show is left out (_frcCardsHtml under node), no management-only call (the phone summary's own detail machinery).
"""
import json
import re
from pathlib import Path

import pytest

import auth
from test_bom_center_batch2 import _fn, _node, needs_node
from test_franchise_role import _walk, auth_on, world      # noqa: F401  (fixtures used by name)
from test_franchise_daily import FORBIDDEN

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "js" / "17-franchise.js").read_text(encoding="utf-8")
TAB = (ROOT / "tabs" / "56-franchise.html").read_text(encoding="utf-8")
KINDS = {"discount": "controls", "voids": "controls", "reprints": "controls", "cash": "controls", "food_cost": "cost",
         "costly": "cost", "below_normal": "month", "voice": "voice"}


# ── the findings (pure) ──────────────────────────────────────────────────────
def _payloads(clean=False):
    from routers.franchise import _control_flags
    disc = {"pct": 2.0 if clean else 6.0, "policy_pct": 2.5, "baht": 1000, "over_policy_baht": None if clean else 3500}
    voids = {"bills": 2 if clean else 30, "baht": 500 if clean else 9000, "rate_pct": 0.2 if clean else 1.4}
    cash = {"days": [] if clean else [{"date": "2026-10-05", "over_short": -2500}], "short_days": 0 if clean else 1, "short_total": 0 if clean else -2500}
    summary = {"available": True, "gp": {"food_cost_pct": 55.0 if clean else 61.2, "food_cost_target_pct": 58.0},
               "sales": {"daily": [{"date": "2026-10-01", "vs_normal_pct": 2.0},
                                   {"date": "2026-10-02", "vs_normal_pct": 1.0 if clean else -9.5},
                                   {"date": "2026-10-03", "vs_normal_pct": None}]}}
    insight = {"available": True, "controls": {"discount": disc, "voids": voids, "cash": cash,
                                               "reprints": {"bills": 0 if clean else 4, "baht": 0 if clean else 800, "available": True},
                                               "flags": _control_flags(disc, voids, cash)},
               "voice": {"available": True, "unreplied": 0 if clean else 3, "negative": 0 if clean else 1, "rating_avg": 4.2}}
    decide = {"available": True, "costly": {"groups": [
        {"slug": "salmon", "label": "แซลมอน", "per_head": 9.1, "avg_per_head": 8.0, "vs_avg_pct": 5.0 if clean else 13.8, "over_avg_plates": 40 if clean else 320},
        {"slug": "eel", "label": "ปลาไหล", "per_head": 0.2, "avg_per_head": 0.3, "vs_avg_pct": -20.0, "over_avg_plates": -10}]}}
    return summary, insight, decide


def test_every_kind_goes_where_its_work_is():
    from routers.franchise import franchise_findings
    f = franchise_findings(*_payloads())
    assert {x["kind"] for x in f} == set(KINDS)
    for x in f:
        assert set(x) == {"kind", "level", "title", "val", "chip", "fact", "more", "go"}, x
        assert x["level"] in ("fix", "watch") and x["chip"] == ("ต้องแก้" if x["level"] == "fix" else "ควรดู")
        assert x["go"]["d"] == KINDS[x["kind"]] and re.fullmatch(r"frc[\w-]+", x["go"]["el"]), x
        assert len(x["chip"]) <= 7 and x["title"] and x["val"] and "\n" not in x["more"]
    by = {x["kind"]: x for x in f}
    assert by["discount"]["level"] == "fix" and by["discount"]["go"]["el"] == "frcCtlDisc"            # 6 % > 2 x the 2.5 % ceiling
    assert by["voids"]["level"] == "fix" and by["voids"]["go"]["el"] == "frcCtlVoids"                 # 1.4 % > the 1 % band
    assert by["reprints"]["go"]["el"] == "frcCtlReprint" and by["cash"]["go"]["el"] == "frcCtlCash"
    assert by["food_cost"]["level"] == "fix" and by["food_cost"]["go"]["el"] == "frcGp"
    assert by["costly"]["go"]["el"] == "frcCostly-salmon" and sum(x["kind"] == "costly" for x in f) == 1     # ปลาไหล is under the average
    assert by["below_normal"]["go"]["el"] == "frcD-2026-10-02" and by["below_normal"]["val"] == "1 วัน"
    assert by["voice"]["go"]["el"] == "frcVoice"
    levels = [x["level"] for x in f]
    assert levels == sorted(levels, key=lambda lv: lv != "fix")                                       # ต้องแก้ first


def test_a_clean_month_has_no_finding_and_a_missing_part_is_skipped():
    from routers.franchise import franchise_findings
    assert franchise_findings(*_payloads(clean=True)) == []
    s, i, d = _payloads()
    assert {x["kind"] for x in franchise_findings(s, None, None)} == {"food_cost", "below_normal"}     # no insight / decide: no crash
    assert franchise_findings(None, None, None) == []


def test_the_void_band_and_the_ceiling_are_the_shared_ones():
    from routers import franchise as F
    from routers import mgt_watch as W
    assert (F.T_CANCEL_WATCH, F.T_CANCEL_FIX) == (W.T_VOID_WATCH, W.T_VOID_FIX) == (0.5, 1.0)
    assert F.DEFAULT_DISCOUNT_POLICY == 2.5
    from rules import BELOW_NORMAL_PCT
    s, i, d = _payloads(clean=True)
    s["sales"]["daily"][1]["vs_normal_pct"] = BELOW_NORMAL_PCT
    assert [x["kind"] for x in F.franchise_findings(s, i, d)] == ["below_normal"]


# ── over HTTP ────────────────────────────────────────────────────────────────
def test_the_overview_is_the_owners_branch_only_and_read_only(world):
    b = world["b"]
    r = b.get("/api/franchise/overview", params={"month": "2026-06", "kind": "month"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) == {"summary", "insight", "decide", "findings"}
    assert d["summary"]["branch"] == "BranchB" and d["insight"]["branch"] == "BranchB"
    assert all(x["go"]["d"] in set(KINDS.values()) for x in d["findings"])
    assert "BranchA" not in json.dumps(d, ensure_ascii=False)
    hits = [(p, k, s) for p, k, s in _walk(d["findings"]) if any(w in s.lower() for w in FORBIDDEN)]
    assert not hits, hits
    assert "food_cost_target_pct" in d["summary"]["gp"]
    # another branch is refused, the period kind is checked, the route has no write
    r = b.get("/api/franchise/overview", params={"branch_id": 1})
    assert r.status_code == 403 and r.headers.get("X-Denied") == "role"
    assert b.get("/api/franchise/overview", params={"kind": "year"}).status_code == 400
    for m in ("post", "put", "delete"):
        r = getattr(b, m)("/api/franchise/overview")
        assert r.status_code in (403, 405), (m, r.status_code)
    assert not auth.path_allowed("POST", "/api/franchise/overview", auth.ROLE_MODULES["franchise"])
    assert world["a"].get("/api/franchise/overview", params={"month": "2026-06"}).json()["summary"]["branch"] == "BranchA"


def test_the_owner_can_still_ask_head_office_and_other_roles_still_cannot(world):
    from test_franchise_f4 import _drop_msgs
    _drop_msgs()
    try:
        b = world["b"]
        r = b.post("/api/franchise/messages", json={"topic": "controls", "body": "บิลยกเลิกเดือนนี้สูง ขอทราบสาเหตุ", "context": None})
        assert r.status_code == 200, r.text
        assert b.get("/api/franchise/messages").json()["unread"] == 0
        r = world["mg"].post("/api/franchise/messages", json={"topic": "sales", "body": "x", "context": None})
        assert r.status_code == 403 and r.headers.get("X-Denied") == "role"            # head office answers, it does not open a thread
    finally:
        _drop_msgs()


# ── the page ────────────────────────────────────────────────────────────────
def test_the_page_is_cards_then_details():
    for d in ("yesterday", "month", "target", "cost", "watch", "controls", "menu", "voice", "msg"):
        assert re.search(r"\b%s: '" % d, JS[JS.index("const _FRC_DT"):JS.index("let _frcDecVs")]), d
        assert f".frc-dt.d-{d} .frc-sec[data-d~=\"{d}\"]" in (ROOT / "index.html").read_text(encoding="utf-8"), d
    for d in ("yesterday", "month", "target", "cost", "watch", "controls", "menu", "voice", "msg"):
        assert f"card('{d}'" in JS, d
    assert 'id="frcCards"' in TAB and 'id="frcDetail"' in TAB and 'id="frcBack"' in TAB and 'id="frcMe"' in TAB
    assert TAB.index('id="frcCards"') < TAB.index('id="frcDetail"')
    # the findings: two-line rows that go (uiRow2 onGo → frcGo → uiFlash), from the server's list
    fnd = _fn(JS, "_frcFindings")
    assert "uiRow2(" in fnd and "onGo:" in fnd and "frcGo(" in fnd and "uiEmpty(" in fnd
    assert "uiFlash(" in _fn(JS, "frcGo")
    assert "_frcGetJson('/api/franchise/overview?" in JS and "frcShowDay(gen, _frcGetJson('/api/franchise/daily'))" in JS
    # history: a detail pushes an entry, popstate returns to the cards
    assert "history.pushState({ frd: d }" in _fn(JS, "frcOpen") and "addEventListener('popstate'" in JS
    # lists are uiRow2
    for f in ("_frcMenus", "_frcCostly", "_frcMenusUp", "_frcMoneyList"):
        assert "uiRow2(" in _fn(JS, f), f


def test_the_page_calls_no_management_only_code():
    """17-franchise.js reuses only the eager helpers (uiRow2, uiEmpty, uiSkel, uiFlash, msPchip, ...): never the management phone's own
    detail machinery or its payload."""
    for name in ("phOpenDetail", "_phShowDetail", "phOvRender", "_msRpt", "loadMobileSummary", "msRenderActive", "msShowView"):
        assert not re.search(r"\b%s\b" % name, JS), name


@needs_node
def test_a_card_with_nothing_to_show_is_left_out():
    pre = ("const esc = s => String(s == null ? '' : s).replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));\n"
           "const fmtN = n => String(Math.round(n)); const msPchip = v => '<i>' + v + '</i>'; const normalLbl = n => 'เทียบปกติ';\n"
           "const uiEmpty = o => '<empty>' + o.t + '</empty>'; const uiSkel = () => '<skel></skel>'; const _FRC_NA = '—';\n"
           "const _frcDay = s => s; const _FRC_DT = {};\n"
           + "\n".join(_fn(JS, n) for n in ("_frcBaht", "_frcPctText", "_frcDayCause", "_frcCardsHtml")))
    ids = "(h => [...h.matchAll(/data-d=\"(\\w+)\"/g)].map(m => m[1]))"
    # the first load: no summary yet - grey cards only
    assert "<skel>" in _node(pre, "_frcCardsHtml({ sum: null }, null)")
    # a month with sales but no target, no cost, no insight, no menus, no messages: เดือนนี้ + ควรดู only
    S = {"sum": {"available": True, "label": "ต.ค.", "sales": {"mtd": 1000, "mtd_vs_last_month_pct": -2}, "target": {"source": "none"},
                 "gp": {"food_cost_pct": None}, "top_menus": []}, "ins": None, "find": [], "day": None}
    assert _node(pre, f"{ids}(_frcCardsHtml({json.dumps(S)}, {{ on: false }}))") == ["month", "watch"]
    # everything there: the nine cards in the owner's question order; findings make ควรดู red
    S.update({"day": {"available": True, "day": {"date": "2026-10-30", "weekday": "พฤหัสบดี", "revenue": 50000, "vs_normal_pct": -9,
                                                     "normal_weeks": 8, "customers": 90, "bills": 30, "per_head": 555}},
              "ins": {"available": True, "controls": {"discount": {"pct": 2}, "voids": {"rate_pct": 0.3}, "reprints": {"bills": 1}, "flags": {"level": "good"}},
                      "voice": {"available": True, "unreplied": 2, "reviews_month": 5, "negative": 0}},
              "find": [{"level": "fix", "title": "บิลยกเลิกสูง"}]})
    S["sum"].update({"target": {"source": "manual", "month_target": 9000, "of_month_pct": 40, "vs_to_date_pct": -3, "need_per_day": 500, "normal_per_day": 300},
                     "gp": {"food_cost_pct": 60, "food_cost_target_pct": 58}, "top_menus": [{"name": "<b>ซูชิ</b>", "plates": 9}]})
    html = _node(pre, f"_frcCardsHtml({json.dumps(S, ensure_ascii=False)}, {{ on: true, unread: 1, threads: 2 }})")
    assert _node(pre, f"{ids}({json.dumps(html, ensure_ascii=False)})") == ["yesterday", "month", "target", "cost", "watch", "controls", "menu", "voice", "msg"]
    assert 'ph-ov-card is-bad ph-ov-wide' in html and "ต้องขายวันละ" in html and "เกินเป้า" in html
    assert re.search(r'is-bad[^>]*data-d="watch"', html) and "<b>ซูชิ</b>" not in html            # red bar when any; a dish name is escaped
