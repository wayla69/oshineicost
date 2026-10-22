"""PR-L - the franchise owner role (เจ้าของสาขา): role matrix, pinning, payload shape, nothing else reachable.

  * the role holds ONE module (franchise.view) and ONE read rule, GET /api/franchise; its only write rules are the six Web Push
    device endpoints (PR-F2, own devices only), so every other path - every /api/bom-center/*, /api/auth/users*, /api/reports/* read or write - is closed;
  * over HTTP: a franchise login reads /api/franchise/summary for its own branch (200, the payload keys), is refused
    (403, X-Denied: role) when it names another branch, and gets 403 on every other read and every write;
  * the payload carries no bills, recipes, prices, cashier or staff names and no other branch's name;
  * the franchise role does not skip the write token (core.is_branch_session stays branch-only);
  * an account of the role with no branch is refused (it would look like the centre), and cannot be created through the API.
"""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

import auth
import core

TOKEN = "owner-token-xyz"
MODS = auth.ROLE_MODULES["franchise"]
OPEN = auth.OPEN_TO_ALL
WRITES = ("POST", "PUT", "PATCH", "DELETE")
# paths a franchise login must not reach, with no rule of their own (closed because the role holds no sales.overview)
UNRULED = ["/api/sales/daily", "/api/sales/top_ordered", "/api/home/brief", "/api/home/branch-scorecard", "/api/analysis/sph",
           "/api/reports/management", "/api/reports/ops", "/api/reports/pnl-bill", "/api/pnl", "/api/buffet/gp",
           "/api/export/monthly-pack", "/api/feedback/items", "/api/menu/engineering", "/api/settings"]
BOM = ["/api/bom-center/stock/sheet", "/api/bom-center/stock/me", "/api/bom-center/issues", "/api/bom-center/order/plan",
       "/api/bom-center/claims", "/api/bom-center/variance/period", "/api/bom-center/todo", "/api/bom-center/ingredients",
       "/api/bom-center/prices", "/api/bom-center/recipes"]


# ── the matrix (no server) ───────────────────────────────────────────────────
def test_the_role_and_module_exist_and_the_role_holds_only_that_module():
    assert "franchise" in auth.ROLES and "franchise.view" in auth.MODULES
    assert MODS == {"franchise.view"}
    assert "franchise" in auth.PINNED_ROLES and "branch" in auth.PINNED_ROLES
    holders = {r for r, m in auth.ROLE_MODULES.items() if "franchise.view" in m}
    assert holders == {"franchise", "management"}        # management holds every module


def test_only_the_summary_get_is_open_to_the_role():
    assert auth.path_allowed("GET", "/api/franchise/summary", MODS)
    assert auth.path_allowed("GET", "/api/franchise/summary?month=2026-09".split("?")[0], MODS)
    for m in WRITES:
        assert not auth.path_allowed(m, "/api/franchise/summary", MODS), m           # no write rule for the role
    rules = [r for r in auth.MODULE_RULES if "franchise.view" in r[2]]
    # PR-F2: besides the one GET prefix, only the six Web Push device endpoints (own devices only, push_api) name the module
    push = [("*", "/api/push/" + p) for p in auth.PUSH_DEVICE_PATHS]
    assert rules[0] == ("GET", "/api/franchise", {"franchise.view"})
    assert [(m, pfx) for m, pfx, _ in rules[1:]] == push
    assert not auth.path_allowed("POST", "/api/push/anything-else", MODS) and not auth.path_allowed("GET", "/api/push", MODS)


def test_every_ruled_prefix_and_every_unruled_path_is_closed_to_the_role():
    bad = []
    for meths, pfx, mods in auth.MODULE_RULES:
        if pfx == "/api/franchise" or pfx.startswith(OPEN) or pfx in {"/api/push/" + p for p in auth.PUSH_DEVICE_PATHS}:
            continue
        for m in (("GET",) if meths == "GET" else ("GET",) + WRITES):
            if auth.path_allowed(m, pfx, MODS):
                bad.append((m, pfx))
    for p in UNRULED + BOM:
        for m in ("GET",) + WRITES:
            if p.startswith(OPEN):
                continue
            if auth.path_allowed(m, p, MODS):
                bad.append((m, p))
    assert not bad, bad


def test_the_open_scaffolding_is_the_same_as_the_branch_logins_and_has_no_write_rule():
    assert auth.path_allowed("POST", "/api/auth/me/password", MODS)                   # a forced password change must work
    for m in WRITES:
        assert not auth.path_allowed(m, "/api/branches", MODS), m                     # W /api/branches needs system.settings


def test_the_franchise_role_does_not_skip_the_write_token(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    for role, expect in (("franchise", False), ("branch", True)):
        tok = auth.make_token(1, "x", role, branch="BranchB")
        req = type("R", (), {"cookies": {auth.SESSION_COOKIE: tok}})()
        assert core.is_branch_session(req) is expect, role


# ── over HTTP ────────────────────────────────────────────────────────────────
@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    yield


def _login(user, pw=auth.DEFAULT_PASSWORD):
    import main
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def world(client, app_db, auth_on):
    """A franchise owner for BranchB (id 2, a franchise branch) and one for BranchA (id 1), plus one with no branch."""
    import main
    client.get("/api/bom-center/hub/queue")
    mg = _login("management1")
    for name, br in (("own_t_b", "BranchB"), ("own_t_a", "BranchA")):
        r = mg.post("/api/auth/users", headers={"X-Upload-Token": TOKEN},
                    json={"username": name, "password": "Temp@12345", "role": "franchise", "branch": br})
        assert r.status_code in (200, 409), r.text
    c = sqlite3.connect(str(main.DB_PATH))
    h = auth.hash_password("Temp@12345")
    c.execute("INSERT OR REPLACE INTO app_user(username, pw_hash, pw_salt, role, must_change, branch) VALUES('own_t_nobranch', ?, ?, 'franchise', 0, NULL)",
              (h["hash"], h["salt"]))
    c.commit()
    try:
        yield {"b": _login("own_t_b", "Temp@12345"), "a": _login("own_t_a", "Temp@12345"), "mg": mg,
               "nobranch": _login("own_t_nobranch", "Temp@12345")}
    finally:
        c.execute("DELETE FROM app_user WHERE username IN ('own_t_b','own_t_a','own_t_nobranch')")
        c.commit()
        c.close()


def test_login_reports_the_role_the_module_and_the_branch(world):
    me = world["b"].get("/api/auth/me").json()
    assert me["role"] == "franchise" and me["branch"] == "BranchB" and me["modules"] == ["franchise.view"]


def test_auth_me_names_hold_no_other_branch_or_franchise_login(world):
    """A pinned session gets its own login and the centre people's names only; a centre session gets all of them."""
    r = world["mg"].post("/api/auth/users", headers={"X-Upload-Token": TOKEN},
                         json={"username": "br_names_x", "password": "Temp@12345", "role": "branch", "branch": "BranchA"})
    assert r.status_code in (200, 409), r.text
    try:
        pinned_logins = {"own_t_a", "own_t_b", "br_names_x", "branch1", "branch2"}
        # a franchise login
        names = world["b"].get("/api/auth/me").json()["names"]
        assert "own_t_b" in names and not (pinned_logins - {"own_t_b"}) & set(names)
        assert "salessupport1" in names and "analyst1" in names          # the centre names its screens show
        # a branch login
        names = _login("br_names_x", "Temp@12345").get("/api/auth/me").json()["names"]
        assert "br_names_x" in names and not (pinned_logins - {"br_names_x"}) & set(names)
        assert "warehouse1" in names
        # a centre login keeps the full map
        names = _login("salessupport1").get("/api/auth/me").json()["names"]
        assert {"own_t_a", "own_t_b", "br_names_x", "branch1"} <= set(names)
    finally:
        import main
        c = sqlite3.connect(str(main.DB_PATH))
        c.execute("DELETE FROM app_user WHERE username='br_names_x'")
        c.commit()
        c.close()


def test_the_api_refuses_a_franchise_account_without_a_branch(world):
    r = world["mg"].post("/api/auth/users", headers={"X-Upload-Token": TOKEN},
                         json={"username": "own_t_x", "password": "Temp@12345", "role": "franchise"})
    assert r.status_code == 400 and "สาขา" in r.json()["detail"]
    r = world["mg"].post("/api/auth/users", headers={"X-Upload-Token": TOKEN}, json={"username": "own_t_y", "role": "franchise"})   # the default-password path
    assert r.status_code == 400


def test_summary_payload_has_the_agreed_keys_and_is_pinned(world):
    r = world["b"].get("/api/franchise/summary", params={"month": "2026-06"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["branch"] == "BranchB" and d["month"] == "2026-06" and d["available"] is True
    assert {"sales", "gp", "ingredients", "discipline", "top_menus", "discount_pct", "verdict", "months"} <= set(d)
    assert {"last_day", "mtd", "mtd_vs_last_month_pct", "mtd_vs_last_year_pct", "bills", "per_bill", "series_30d"} <= set(d["sales"])
    assert {"date", "revenue"} <= set(d["sales"]["last_day"]) and len(d["sales"]["series_30d"]) == 30
    assert {"gp_pct", "target_pct", "target_basis", "food_cost_pct"} <= set(d["gp"])
    assert d["gp"]["target_basis"] == "prev_month"          # the test data has nothing a year earlier: the fallback
    assert {"latest_period", "waste_pct_month", "streak_flag"} <= set(d["ingredients"])
    assert {"slip_coverage_pct", "slip_on_time_pct", "orders_on_time_pct", "receipts_on_time", "claims_month"} <= set(d["discipline"])
    assert isinstance(d["top_menus"], list) and len(d["top_menus"]) <= 10
    assert all(set(x) == {"name", "plates"} for x in d["top_menus"])
    assert set(d["verdict"]) >= {"good", "watch", "fix"} and all(len(d["verdict"][k]) <= 3 for k in ("good", "watch", "fix"))
    assert all(isinstance(t, str) and "\n" not in t for k in ("good", "watch", "fix") for t in d["verdict"][k])
    assert {m["month"] for m in d["months"]} >= {"2026-06", "2026-05"}
    # the other owner sees the other branch, not this one
    assert world["a"].get("/api/franchise/summary", params={"month": "2026-06"}).json()["branch"] == "BranchA"


def test_naming_another_branch_is_refused_and_naming_its_own_is_fine(world):
    r = world["b"].get("/api/franchise/summary", params={"branch_id": 1, "month": "2026-06"})
    assert r.status_code == 403 and r.headers.get("X-Denied") == "role"
    assert world["b"].get("/api/franchise/summary", params={"branch_id": 2, "month": "2026-06"}).status_code == 200
    assert world["b"].get("/api/franchise/summary", params={"month": "2026-13"}).status_code == 422       # not a month


def test_a_franchise_login_without_a_branch_is_refused(world):
    for q in ({}, {"branch_id": 1}, {"branch_id": 2}):
        r = world["nobranch"].get("/api/franchise/summary", params=q)
        assert r.status_code == 403 and r.headers.get("X-Denied") == "role", q


def test_the_centre_must_name_a_branch(world):
    assert world["mg"].get("/api/franchise/summary").status_code == 400
    assert world["mg"].get("/api/franchise/summary", params={"branch_id": 1, "month": "2026-06"}).json()["branch"] == "BranchA"


def test_every_other_read_and_every_write_is_refused(world):
    b = world["b"]
    bad = []
    for p in BOM + ["/api/auth/users", "/api/reports/management?period=daily", "/api/reports/ops",
                    "/api/sales/daily", "/api/home/brief", "/api/cost", "/api/settings", "/api/export/monthly-pack"]:
        r = b.get(p)
        if not (r.status_code == 403 and r.headers.get("X-Denied") == "role"):
            bad.append(("GET", p, r.status_code))
    for m, p in (("POST", "/api/franchise/summary"), ("PUT", "/api/franchise/summary"), ("DELETE", "/api/franchise/summary"),
                 ("POST", "/api/bom-center/stock/count"), ("POST", "/api/bom-center/order"), ("POST", "/api/bom-center/issues"),
                 ("POST", "/api/bom-center/claims"), ("POST", "/api/auth/users"), ("PUT", "/api/auth/users/own_t_b"),
                 ("DELETE", "/api/auth/users/own_t_a"), ("POST", "/api/cost"), ("POST", "/api/targets"), ("POST", "/api/upload/sales"),
                 ("POST", "/api/branches"), ("POST", "/api/reports/export")):
        for hdr in ({}, {"X-Upload-Token": TOKEN}):                    # the token does not open anything either
            r = b.request(m, p, json={}, headers=hdr)
            if not (r.status_code == 403 and r.headers.get("X-Denied") == "role"):
                bad.append((m, p, bool(hdr), r.status_code, r.headers.get("X-Denied")))
    assert not bad, bad
    # the role matrix is under /api/auth/ (open scaffolding): its handler refuses anyone who is not an admin
    assert b.get("/api/auth/role-permissions").status_code == 403
    assert b.put("/api/auth/role-permissions", json={"permissions": {"franchise": ["system.admin"]}}, headers={"X-Upload-Token": TOKEN}).status_code == 403
    assert auth.ROLE_MODULES["franchise"] == {"franchise.view"}


def _walk(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield path + "/" + str(k), "key", str(k)
            yield from _walk(v, path + "/" + str(k))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _walk(v, f"{path}[{i}]")
    elif isinstance(o, str):
        yield path, "str", o


def test_the_payload_holds_no_bill_recipe_price_staff_or_other_branch(world):
    forbidden = ("recipe", "price", "cashier", "staff", "supplier", "receipt_no", "bill_no", "username", "password", "token", "bom")
    for who, other in (("b", "BranchA"), ("a", "BranchB")):
        d = world[who].get("/api/franchise/summary", params={"month": "2026-06"}).json()
        hits = [(p, kind, s) for p, kind, s in _walk(d) if any(w in s.lower() for w in forbidden)]
        assert not hits, hits
        text = json.dumps(d, ensure_ascii=False)
        assert other not in text
        # the only branch name anywhere is the owner's own
        assert text.count('"branch"') == 1


def test_gp_target_is_the_same_month_last_year_with_the_previous_month_as_fallback(monkeypatch):
    """The standing target rule (targets.py): own actual of the same month a year earlier; last month only without it."""
    from routers import franchise as F
    asked = []

    def fake(branch, month):
        asked.append(month)
        return {"available": True, "selected_month": month, "range": [month + "-01", month + "-30"], "gp": {"gp_pct": 41.5}}
    monkeypatch.setattr(F, "_report", fake)
    gp = {"gp_pct": 43.0, "target_pct": 40.0, "target_basis": "prev_month"}
    F._gp_target(gp, "BranchB", "2026-09")
    assert asked == ["2025-09"] and gp["target_pct"] == 41.5 and gp["target_basis"] == "last_year"
    monkeypatch.setattr(F, "_report", lambda b, m: {"available": False})
    gp = {"gp_pct": 43.0, "target_pct": 40.0, "target_basis": "prev_month"}
    F._gp_target(gp, "BranchB", "2026-09")
    assert gp["target_pct"] == 40.0 and gp["target_basis"] == "prev_month"
    monkeypatch.setattr(F, "_report", lambda b, m: {"available": True, "selected_month": m, "range": [m + "-01", m + "-30"], "gp": {"gp_pct": None}})   # no costed data a year ago
    F._gp_target(gp, "BranchB", "2026-09")
    assert gp["target_basis"] == "prev_month"
    monkeypatch.setattr(F, "_report", lambda b, m: {"available": True, "selected_month": "2026-06", "range": ["2026-06-01", "2026-06-30"], "gp": {"gp_pct": 30.0}})   # the report fell back to another month
    F._gp_target(gp, "BranchB", "2026-09")
    assert gp["target_pct"] == 40.0 and gp["target_basis"] == "prev_month"
    # the verdict names the reference it compared with
    v = F._verdict(None, {"gp_pct": 45.0, "target_pct": 41.5, "target_basis": "last_year"}, None, None, None, None)
    assert any("เดือนเดียวกันของปีก่อน" in t for t in v["good"])
    v = F._verdict(None, {"gp_pct": 45.0, "target_pct": 41.5, "target_basis": "prev_month"}, None, None, None, None)
    assert any("สูงกว่าเดือนก่อน" in t for t in v["good"])


def test_auth_off_the_endpoint_still_needs_a_branch(client):
    assert client.get("/api/franchise/summary").status_code == 400
    assert client.get("/api/franchise/summary", params={"branch_id": 2, "month": "2026-06"}).status_code == 200
