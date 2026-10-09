"""PR-K · a branch login is pinned to its own branch on every Phase-2 endpoint (owner 2026-10-10).

The sweep walks the router's route table instead of a hand-written list, so an endpoint added later is covered
without anyone remembering to add it here:

  * every /issues*, /issue-reports*, /issue-summary*, /order*, /claims*, /variance* and /stock* route that takes a
    `branch_id` refuses a branch session that names ANOTHER branch: 403 with `X-Denied: role` (a write is refused
    the same way, or never reaches its handler because the body is incomplete — never 2xx);
  * a record that belongs to another branch (count, slip, order, claim) is refused by id: 403 `X-Denied: role`;
  * the centre-only routes (those a branch has no module for, or whose handler has no branch) are closed to it.
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

import auth

TOKEN = "owner-token-xyz"
PREFIXES = ("/api/bom-center/issues", "/api/bom-center/issue-reports", "/api/bom-center/issue-summary",
            "/api/bom-center/order", "/api/bom-center/claim", "/api/bom-center/variance", "/api/bom-center/stock")
PLACEHOLDER = {"end_count_id": "1", "month": "2026-09", "day": "2026-09-01", "count_date": "2026-09-01",
               "date_from": "2026-09-01", "date_to": "2026-09-30"}
# centre-only routes (no branch in the request): a branch login must not get through at all
CENTRE_ONLY = [("GET", "/api/bom-center/issue-summary-all.xlsx?month=2026-09"),
               ("GET", "/api/bom-center/variance/overview"),
               ("POST", "/api/bom-center/claim-act/decide"), ("POST", "/api/bom-center/issue-summary/posted"),
               ("POST", "/api/bom-center/issues/reopen"), ("POST", "/api/bom-center/stock/layout")]


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    yield


def _login(user, pw=auth.DEFAULT_PASSWORD):
    import main
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": user, "password": pw}).status_code == 200
    return c


@pytest.fixture
def world(client, app_db, auth_on):
    """A branch login for BranchA (id 1) and one record of every kind that belongs to BranchB (id 2)."""
    import main
    client.get("/api/bom-center/hub/queue")                       # creates the bc_* tables
    mg = _login("management1")
    r = mg.post("/api/auth/users", headers={"X-Upload-Token": TOKEN},
                json={"username": "br_scope_a", "password": "Temp@12345", "role": "branch", "branch": "BranchA"})
    assert r.status_code in (200, 409), r.text
    c = sqlite3.connect(str(main.DB_PATH))
    ids = {}
    ids["count_id"] = c.execute("INSERT INTO bc_stock_count(branch_id, count_date, kind, counted_by, created_at, status, submitted_at)"
                                " VALUES(2, '2026-09-01', 'begin', 'x', '2026-09-01', 'submitted', '2026-09-01')").lastrowid
    ids["slip_id"] = c.execute("INSERT INTO bc_issue_slip(branch_id, slip_date, status, created_by, created_at, updated_at)"
                               " VALUES(2, '2026-09-02', 'draft', 'x', '2026-09-02', '2026-09-02')").lastrowid
    ids["order_id"] = c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, value)"
                                " VALUES(2, '2026-09-03', '1', 'submitted', 0)").lastrowid
    ids["claim_id"] = c.execute("INSERT INTO bc_claim(branch_id, order_id, status, created_by, created_at)"
                                " VALUES(2, ?, 'submitted', 'x', '2026-09-03')", (ids["order_id"],)).lastrowid
    ids["photo_id"] = c.execute("INSERT INTO bc_claim_photo(claim_id, mime, size, data, created_at) VALUES(?, 'image/png', 1, x'00', '2026-09-03')",
                                (ids["claim_id"],)).lastrowid
    ids["receipt_id"] = c.execute("SELECT COALESCE(MAX(receipt_id), 0) + 1000 FROM bc_receipt").fetchone()[0]
    c.commit()
    try:
        yield _login("br_scope_a", "Temp@12345"), ids
    finally:
        c.execute("DELETE FROM bc_claim_photo WHERE claim_id=?", (ids["claim_id"],))
        c.execute("DELETE FROM bc_claim WHERE claim_id=?", (ids["claim_id"],))
        c.execute("DELETE FROM bc_order WHERE order_id=?", (ids["order_id"],))
        c.execute("DELETE FROM bc_issue_slip WHERE slip_id=?", (ids["slip_id"],))
        c.execute("DELETE FROM bc_stock_count WHERE count_id=?", (ids["count_id"],))
        c.execute("DELETE FROM app_user WHERE username='br_scope_a'")
        c.commit()
        c.close()


def _routes():
    from routers import bom_center as bc
    for r in bc.router.routes:
        if getattr(r, "path", "").startswith(PREFIXES):
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                yield m, r


def _url(route, ids, branch_id=None):
    path = route.path
    for p in route.dependant.path_params:
        path = path.replace("{" + p.name + "}", str(ids.get(p.name, 1)))
    q = {}
    for p in route.dependant.query_params:
        if p.name == "branch_id":
            q["branch_id"] = branch_id
        elif p.field_info.is_required() or p.name in PLACEHOLDER:
            q[p.name] = ids.get(p.name, PLACEHOLDER.get(p.name, "1"))
    q = {k: v for k, v in q.items() if v is not None}
    return path + ("?" + "&".join(f"{k}={v}" for k, v in q.items()) if q else "")


def _call(c, method, url):
    h = {"X-Upload-Token": TOKEN}
    return c.request(method, url, headers=h, json={}) if method != "GET" else c.get(url)


def test_the_sweep_really_covers_the_phase_2_routes():
    got = {(m, r.path) for m, r in _routes()}
    for must in (("GET", "/api/bom-center/stock/sheet"), ("GET", "/api/bom-center/issues/sheet"), ("GET", "/api/bom-center/order/plan"),
                 ("GET", "/api/bom-center/claims"), ("GET", "/api/bom-center/variance/period"),
                 ("GET", "/api/bom-center/issue-summary"), ("GET", "/api/bom-center/issue-reports/daily")):
        assert must in got, must
    with_branch = [(m, r.path) for m, r in _routes() if "branch_id" in {p.name for p in r.dependant.query_params}]
    assert len(with_branch) >= 30, len(with_branch)


def test_naming_another_branch_is_refused_on_every_route(world):
    b, ids = world
    swept, bad = 0, []
    for m, r in _routes():
        if "branch_id" not in {p.name for p in r.dependant.query_params}:
            continue
        resp = _call(b, m, _url(r, ids, branch_id=2))
        swept += 1
        ok = (resp.status_code == 403 and resp.headers.get("X-Denied") == "role") or \
             (m != "GET" and resp.status_code == 422)               # a write whose body is incomplete never reaches its handler
        if not ok:
            bad.append((m, r.path, resp.status_code, resp.headers.get("X-Denied"), resp.text[:80]))
    assert swept >= 30
    assert not bad, bad


def test_another_branchs_records_are_refused_by_id(world):
    b, ids = world
    bad = []
    for m, r in _routes():
        names = {p.name for p in r.dependant.path_params}
        if not names & {"count_id", "slip_id", "order_id", "claim_id"}:
            continue
        for bid in (None, 1):                                            # without branch_id, and naming its own branch
            resp = _call(b, m, _url(r, ids, branch_id=bid))
            refused = resp.status_code == 403 and resp.headers.get("X-Denied") == "role"
            # a write on another branch's record may also answer "not found for this branch" (400/404) or stop at its
            # incomplete body (422): what matters is that it never succeeds and never shows the record
            gone = m != "GET" and resp.status_code in (400, 404, 422)
            if not (refused or gone):
                bad.append((m, r.path, bid, resp.status_code, resp.text[:80]))
    assert not bad, bad


def test_centre_only_routes_are_closed_to_a_branch_login(world):
    b, ids = world
    bad = []
    for m, url in CENTRE_ONLY:
        resp = _call(b, m, url)
        if not (resp.status_code == 403 and resp.headers.get("X-Denied") == "role"):
            bad.append((m, url, resp.status_code, resp.headers.get("X-Denied"), resp.text[:80]))
    assert not bad, bad


def test_its_own_branch_still_works(world):
    b, ids = world
    assert b.get("/api/bom-center/stock/sheet?branch_id=1").status_code == 200
    assert b.get("/api/bom-center/stock/sheet").status_code == 200           # unspecified = its own
    assert b.get("/api/bom-center/stock/me").json()["branches"] == [{"branch_id": 1, "name": "BranchA"}]
