"""DV6 (phone table review 2026-10-25, top-10 #9 + the owner's "อะไรที่มีประโยชน์ขึ้นหน้าจอของเจ้าของสาขาด้วย").

  * the management summary: every to-do line carries the baht it is about (actions[].baht / baht_label), lines of one severity run
    by that baht; each branch carries rev_delta (the baht behind wow_pct) and, with a target, target_gap (the baht behind
    vs_target_pct, the same month-end basis); the food-cost line's baht is the cost above the ONE target (rules.food_cost_target);
  * GET /api/franchise/decide (ร้านของฉัน · เรื่องที่ควรดู): the period's windows, ของแพง per customer (plates only), the menus ordered
    more per customer and the new menus (the menu-spike rule, no cost), food cost % against the target and เฉลี่ยทุกสาขา, the 6
    weeks' sales against the branch's own average - own branch only, no other branch's name, no price / recipe / staff key;
  * the phone: the summary follows the app's period / branch and writes them back; the เมนู tab is the cost-per-head risers in baht;
    the franchise page follows the top bar's period (week | month) and has no month picker of its own.
"""
import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "owner-token-dv6"


def _js(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# ── the management summary ───────────────────────────────────────────────────
def _mgt(client, **q):
    r = client.get("/api/reports/management", params={"period": "monthly", "lang": "th", **q})
    assert r.status_code == 200, r.text
    return r.json()


def test_every_action_line_carries_its_baht_and_one_severity_runs_by_it(client):
    seen = 0
    for q in ({}, {"branch": "BranchA"}, {"branch": "BranchB"}, {"month": "2026-06"}, {"month": "2026-05"}):
        j = _mgt(client, **q)
        if not j.get("available"):
            continue
        acts = j["actions"]
        rank = {"high": 0, "med": 1, "low": 2}
        assert [rank[a["sev"]] for a in acts] == sorted(rank[a["sev"]] for a in acts)
        for sev in rank:
            b = [abs(a.get("baht") or 0) for a in acts if a["sev"] == sev]
            assert b == sorted(b, reverse=True), (sev, acts)
        for a in acts:
            if a["text"].startswith("ไม่พบประเด็นเร่งด่วน"):
                continue
            seen += 1
            assert "baht" in a and isinstance(a.get("baht_label"), str) and a["baht_label"], a
    assert seen, "the fixture raised no to-do line"


def test_branch_rows_carry_the_baht_behind_their_percentages(client):
    rows = []
    for m in ("2026-06", "2026-05", "2026-04"):
        rows += [b for b in _mgt(client, month=m).get("branches") or [] if b.get("prev_revenue") is not None]
    assert rows
    for b in rows:
        assert b["rev_delta"] == round(b["revenue"] - b["prev_revenue"])
        assert (b["rev_delta"] < 0) == (b["wow_pct"] < 0) or b["wow_pct"] == 0
        if b.get("target"):
            base = b["projected"] if b.get("target_basis") == "projected" else b["revenue"]
            assert b["target_gap"] == round(base - b["target"])


def test_the_food_cost_line_is_the_baht_above_the_one_target(client):
    import core
    assert client.put("/api/settings", json={"food_cost_target": "20"}).status_code == 200
    try:
        core.invalidate_cache("report_management", rebuild_cost=False)
        j = _mgt(client, month="2026-06")
    finally:
        client.put("/api/settings", json={"food_cost_target": ""})
        con = sqlite3.connect(str(core.DB_PATH))
        con.execute("DELETE FROM app_settings WHERE key='food_cost_target'")
        con.commit()
        con.close()
        core.invalidate_cache("report_management", rebuild_cost=False)
    fc, rev = j["pace"].get("food_cost_pct"), j["pace"].get("mtd_revenue")
    line = [a for a in j["actions"] if a["text"].startswith("ต้นทุนอาหาร")]
    if fc is None or fc <= 20:
        pytest.skip("the fixture month has no food cost above 20 %")
    assert line, j["actions"]
    assert line[0]["baht"] == -round((fc - 20) / 100.0 * rev)
    assert "เป้า 20%" in line[0]["baht_label"]


# ── /api/franchise/decide ────────────────────────────────────────────────────
@pytest.fixture
def owners(client, app_db, monkeypatch):
    import main
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    c = sqlite3.connect(str(main.DB_PATH))
    h = auth.hash_password("Temp@12345")
    for name, br in (("own_dv6_b", "BranchB"), ("own_dv6_a", "BranchA")):
        c.execute("INSERT OR REPLACE INTO app_user(username, pw_hash, pw_salt, role, must_change, branch) VALUES(?, ?, ?, 'franchise', 0, ?)",
                  (name, h["hash"], h["salt"], br))
    c.commit()

    def login(u):
        t = TestClient(main.app)
        r = t.post("/api/auth/login", json={"username": u, "password": "Temp@12345"})
        assert r.status_code == 200, r.text
        return t
    try:
        yield {"b": login("own_dv6_b"), "a": login("own_dv6_a")}
    finally:
        c.execute("DELETE FROM app_user WHERE username IN ('own_dv6_b','own_dv6_a')")
        c.commit()
        c.close()


def _walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield str(k)
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)
    elif isinstance(o, str):
        yield o


def test_decide_answers_the_owners_branch_for_the_period(owners):
    for kind in ("week", "month"):
        r = owners["b"].get("/api/franchise/decide", params={"kind": kind})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["available"] is True and d["period"]["kind"] == kind
        assert {"costly", "menus", "food_cost", "weeks"} <= set(d)
        fc = d["food_cost"]
        assert {"pct", "prev_pct", "avg_pct", "vs_avg_pp", "gap_baht", "target_pct", "target_gap_baht"} <= set(fc)
        assert fc["target_pct"] == 58.0
        assert len(d["weeks"]["weeks"]) == 6
        for w in d["weeks"]["weeks"]:
            assert w["to"] <= d["period"]["cur"][1] and w["to"] > w["from"]
        if d["costly"]:
            for g in d["costly"]["groups"]:
                assert not any("cost" in k for k in g), g             # plates only
        if d["menus"]:
            assert len(d["menus"]["rising"]) <= 5 and len(d["menus"]["new"]) <= 5


def test_decide_follows_the_pick_of_a_past_period(owners):
    d = owners["b"].get("/api/franchise/decide", params={"kind": "month", "pick": "2026-05-15"}).json()
    if not d["available"]:
        pytest.skip("no data")
    assert d["period"]["cur"][0] == "2026-05-01" and d["period"]["cur"][1] == "2026-05-31"
    assert d["weeks"]["weeks"][-1]["to"] <= "2026-05-31"


def test_decide_is_pinned_and_holds_no_other_branch_price_or_staff(owners):
    assert owners["b"].get("/api/franchise/decide", params={"kind": "year"}).status_code == 400
    r = owners["b"].get("/api/franchise/decide", params={"branch_id": 1})
    assert r.status_code == 403 and r.headers.get("X-Denied") == "role"
    forbidden = ("recipe", "price", "cashier", "staff", "supplier", "receipt_no", "bill_no", "username", "password", "token", "bom")
    for who, other in (("b", "BranchA"), ("a", "BranchB")):
        for kind in ("week", "month"):
            d = owners[who].get("/api/franchise/decide", params={"kind": kind}).json()
            hits = [s for s in _walk(d) if any(w in s.lower() for w in forbidden)]
            assert not hits, hits
            text = json.dumps(d, ensure_ascii=False)
            assert other not in text and '"branch"' not in text


def test_the_role_reaches_decide_through_its_one_rule():
    assert auth.path_allowed("GET", "/api/franchise/decide", auth.ROLE_MODULES["franchise"])


# ── the phone ────────────────────────────────────────────────────────────────
def test_the_summary_follows_and_writes_the_apps_period_and_branch():
    m = _js("js/09c-mobile.js")
    html = _js("index.html")
    assert 'id="msBranch" onchange="msPickChanged()"' in html and 'id="msMonthSel" onchange="msPickChanged()"' in html
    assert "function msPullGlobal()" in m and "gPeriodResolve()" in m
    assert re.search(r"function msPickChanged\(\)[\s\S]*?gPeriodSet\(\{ kind: 'month', pick: m \? m \+ '-01' : '' \}, true\)", m)
    assert re.search(r"async function loadMobileSummary\(force\) \{\s*if \(!_msFromPick\) \{ try \{ msPullGlobal\(\);", m)


def test_the_summary_tabs_answer_in_baht():
    m = _js("js/09c-mobile.js")
    html = _js("index.html")
    assert "function msRisersHtml" not in m and "มาแรงเดือนนี้" not in html     # the % of pieces list is gone
    assert "function msCostRisersHtml(j, key, top)" in m and "msCostRisersHtml(j, 'msmenu', 15)" in m
    assert "_msBranchSort = 'chg'" in m and 'id="msSortChg"' in html and "msSortMom" not in html
    assert re.search(r"function msActionsHtml\(j\)[\s\S]*?a\.baht[\s\S]*?a\.baht_label", m)
    assert "b.rev_delta - a.rev_delta" in m                                         # เพิ่ม / ลด มากสุด by baht


def test_the_franchise_page_follows_the_top_bars_period():
    core = _js("js/01-core.js")
    assert "franchise: { load: () => loadFranchise(), follow: { period: { kinds: ['week', 'month'] } } }" in core
    frc = _js("js/17-franchise.js")
    assert "frcMonthChanged" not in frc and "/api/franchise/decide?" in frc
    assert re.search(r"function _frcPeriod\(\)[\s\S]*?gPeriodGet\(\)[\s\S]*?gPeriodResolve\(\)", frc)
    tab = _js("tabs/56-franchise.html")
    assert 'id="frcPeriodChip"' in tab and "phPeriodOpen()" in tab and "<select" not in tab
    # branch words; no office words on the owner's cards
    block = frc[frc.index("// ── DV6"):frc.index("// ── PR-F2")]
    for w in ("ของแพง", "เฉลี่ยทุกสาขา", "กินต่อหัว"):
        assert w in block
    for w in ("กรุณา", "ระบบจะ", "ฉบับร่าง", "ประมาณการ", "ค่ากลาง"):
        assert w not in block, w
