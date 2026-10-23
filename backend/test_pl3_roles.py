# -*- coding: utf-8 -*-
"""PL3 (RV3 findings F1-F4): what a role may see is decided ONCE, by the server's own path rules.

  * /api/auth/me hands the browser the rule list (auth.client_rules); the page's pathAllowedForMe() is the twin of auth.path_allowed -
    a node run over every rule prefix and every role proves the two agree, so a page never asks for an answer the server refuses
    (no console 403) and never shows a card for data its role cannot load;
  * a role with neither cost.analysis nor profit.pnl gets the management summary, its month-by-month series, the daily brief, the
    latest-month comparison and the month-on-month insights WITHOUT cost, gross profit or food-cost figures - removed in the payload,
    not only hidden on the page;
  * a role that holds a cost module still gets all of it.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth

REPO = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
PW = auth.DEFAULT_PASSWORD


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    yield


def _as(app_db, user):
    import main
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"username": user, "password": PW})
    assert r.status_code == 200, r.text
    return c


def test_me_carries_the_rules(client, app_db, auth_on):
    c = _as(app_db, "management1")
    me = c.get("/api/auth/me").json()
    assert me["rules"] and all(isinstance(p, str) and isinstance(m, list) for p, m in me["rules"])
    assert "/api/profit" in [p for p, _ in me["rules"]]
    assert "/api/auth/" in me["open_to_all"]
    assert me["rules"] == auth.client_rules()


@needs_node
def test_client_rule_check_equals_server_path_allowed(tmp_path):
    """js/01-core.js pathAllowedForMe() gives the same answer as auth.path_allowed for every GET rule prefix (and a few paths no rule names)."""
    src = (REPO / "js/01-core.js").read_text(encoding="utf-8")
    a = src.index("let _AUTH_RULES")
    b = src.index("function hasModule(")
    code = src[a:b]
    paths = {p for p, _ in auth.client_rules()}
    paths |= {p + "/x" for p in list(paths)}
    paths |= {"/api/sales/monthly", "/api/summary", "/api/nav/counts", "/api/bom-center/mode", "/api/forecast", "/api/market/news",
              "/api/overall/latest-month-mtd", "/api/home/brief", "/api/analysis/anomalies"}
    paths = sorted(paths)
    cases = []
    for role in auth.ROLES:
        mods = sorted(auth.modules_for_role(role))
        for p in paths:
            cases.append([role, mods, p, auth.path_allowed("GET", p, set(mods))])
    prelude = ("global.window = { AUTH: { enabled: true, modules: [] } };\n" + code +
               "\n_authRulesFrom(" + json.dumps({"rules": auth.client_rules(), "open_to_all": list(auth.OPEN_TO_ALL)}) + ");\n"
               "const cases = " + json.dumps(cases) + ";\n"
               "const bad = []; for (const [role, mods, p, want] of cases) { window.AUTH.modules = mods; "
               "if (pathAllowedForMe(p) !== want) bad.push([role, p, want]); }\n"
               "console.log(JSON.stringify({ n: cases.length, bad }));")
    js = tmp_path / "rules_check.js"
    js.write_text(prelude, encoding="utf-8")
    out = subprocess.run([NODE, str(js)], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    res = json.loads(out)
    assert res["n"] > 500 and res["bad"] == [], res["bad"][:10]


def test_data_needs_cards_name_a_real_rule_path(client):
    """Every data-needs path in the page fragments is a path the server really serves (a typo would hide a card from everyone)."""
    seen = 0
    for f in sorted((REPO / "tabs").glob("*.html")):
        for m in re.finditer(r'data-needs="([^"]+)"', f.read_text(encoding="utf-8")):
            for p in m.group(1).split(","):
                seen += 1
                assert client.get(p.strip()).status_code != 404, (f.name, p)
    assert seen >= 3


# ── the payloads ────────────────────────────────────────────────────────────────────────────────────────────────────────
COST_KEYS_GP = {"gp_pct", "gp_per_head", "food_cost", "bridge", "bridge_yoy", "gp_pct_prev", "gp_pct_delta_pp", "cost_basis"}


def test_management_summary_without_cost_for_marketing(client, app_db, auth_on):
    q = {"period": "monthly", "lang": "th", "mtd": "1"}
    mk = _as(app_db, "marketing1").get("/api/reports/management", params=q)
    mg = _as(app_db, "management1").get("/api/reports/management", params=q)
    assert mk.status_code == 200 and mg.status_code == 200
    a, b = mk.json(), mg.json()
    assert set(a["gp"]) == {"revenue", "hidden"} and not (COST_KEYS_GP & set(a["gp"]))
    assert a["menu"]["top"] == [] and a["menu"]["risers"] == [] and a["menu"]["savers"] == []
    for k in ("food_cost_pct", "bom_cost_mtd", "cost_coverage", "food_cost_pct_prev"):
        assert k not in a["pace"], k
    assert a["actions"] and not any(("ต้นทุนอาหาร" in x["text"] or "food cost" in x["text"].lower()) for x in a["actions"])
    assert "stock_variance" not in a
    for r in (a.get("tier") or {}).get("rows", []):
        assert not ({"cogs_head", "cogs_head_prev", "gp_head", "food_pct"} & set(r))
    assert "drivers" not in (a.get("tier") or {})
    # the sales side is the same for everyone
    assert a["headline"]["revenue"] == b["headline"]["revenue"]
    assert a["branches"] == b["branches"]
    # a role with a cost module keeps all of it
    assert "gp_pct" in b["gp"] and "food_cost_pct" in b["pace"]
    assert "hidden" not in b["gp"]
    for role in ("accounting1", "analyst1", "innovation1"):
        j = _as(app_db, role).get("/api/reports/management", params=q).json()
        assert "hidden" not in j["gp"], role


def test_months_series_without_gp_for_roles_without_cost(client, app_db, auth_on):
    for user in ("marketing1", "operation1", "salessupport1"):
        j = _as(app_db, user).get("/api/reports/management/months").json()
        assert j["months"], user
        for r in j["months"]:
            assert not ({"gp_pct", "gp", "food_cost", "cost_basis"} & set(r)), (user, r)
            assert "gp_pp" not in r["mom"]
    j = _as(app_db, "accounting1").get("/api/reports/management/months").json()
    assert any("gp_pct" in r for r in j["months"])


def test_latest_month_mtd_without_cogs_for_roles_without_cost(client, app_db, auth_on):
    for user in ("marketing1", "operation1", "salessupport1"):
        j = _as(app_db, user).get("/api/overall/latest-month-mtd").json()
        assert j["available"] and j["cogs_comparable"] is False, user
        assert j["cur"]["cogs"] is None and j["cur"]["gross_profit"] is None and j["prev"]["cogs"] is None
        assert j["cur"]["revenue_ex_vat"] is not None
    j = _as(app_db, "accounting1").get("/api/overall/latest-month-mtd").json()
    assert j["cur"]["cogs"] is not None


def test_brief_without_food_cost_for_roles_without_cost(client, app_db, auth_on):
    for user in ("marketing1", "operation1", "salessupport1"):
        j = _as(app_db, user).get("/api/home/brief").json()
        assert j.get("food_cost_pct_mtd") is None and j.get("mtd_bom_cost") is None, user
        assert not (set(j.get("cost_health") or {}) - {"giveaways"}), user
    mg = _as(app_db, "management1").get("/api/home/brief").json()
    assert "cost_health" in mg


def test_insights_without_cost_sentences_for_roles_without_cost(client, app_db, auth_on):
    for user in ("marketing1", "operation1", "salessupport1"):
        j = _as(app_db, user).get("/api/analysis/anomalies").json()
        assert all(i.get("type") not in ("cost_spike", "cost_drop") for i in j["insights"]), user
    # the cost insights are only ever produced for the roles that may see them: the cached answer itself still has them
    import routers.analysis as an
    assert an._ANOMALY_COST_TYPES == {"cost_spike", "cost_drop"}


def test_denied_cost_endpoints_stay_closed(client, app_db, auth_on):
    c = _as(app_db, "marketing1")
    for p in ("/api/buffet/menu-cost-efficiency", "/api/profit/summary", "/api/gp/by-branch-month", "/api/analysis/gross_profit"):
        assert c.get(p).status_code == 403, p
