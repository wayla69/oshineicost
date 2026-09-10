# -*- coding: utf-8 -*-
"""E4 — a login confined to one branch: the user record carries it, the token
carries it, and every feedback read / write is scoped by the server (a query
for another branch is ignored, another branch's row is 403). Per-branch
Telegram chats get their own alerts and Monday digest."""
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import auth  # noqa: E402
import fetch_feedback as FF  # noqa: E402
import notify  # noqa: E402
from routers import feedback_actions as A  # noqa: E402
from test_feedback_sources import _args, _seed_db  # noqa: E402

PW = "Pw@1234567"


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    yield


def _wipe(con, table):
    """Empty a table if it exists — never create a stub that would shadow the real DDL."""
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        con.execute(f"DELETE FROM {table}")


@pytest.fixture(autouse=True)
def _clean(request):
    yield
    if "client" not in request.fixturenames:
        return
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    for t in ("customer_feedback", "feedback_action", "feedback_survey_link"):
        _wipe(con, t)
    con.execute("DELETE FROM app_user WHERE username IN ('mgr_a', 'mgr_x')")
    con.commit(); con.close()
    try:
        import main
        main.invalidate_cache()
    except Exception:
        pass


def _fresh():
    import main
    return TestClient(main.app)


def _login(c, username, password=auth.DEFAULT_PASSWORD):
    return c.post("/api/auth/login", json={"username": username, "password": password})


def _voices(c, rows):
    body = [{"branch": b, "date": d, "source": "Google", "rating": r, "text": t, "external_id": f"s:{i}:{b}"}
            for i, (b, d, r, t) in enumerate(rows)]
    r = c.post("/api/feedback/upload", json={"rows": body, "by": "test"})
    assert r.status_code == 200, r.text
    return r.json()


def test_branch_login_sees_only_its_branch(client, app_db, auth_on):
    mg = _fresh(); assert _login(mg, "management1").status_code == 200
    # the account: created with a branch, listed with it, changeable, clearable; an unknown branch is refused
    assert mg.post("/api/auth/users", json={"username": "mgr_x", "password": PW, "role": "operation", "branch": "Nowhere"}).status_code == 400
    assert mg.post("/api/auth/users", json={"username": "mgr_a", "password": PW, "role": "operation", "branch": "BranchA"}).status_code == 200
    users = {u["username"]: u for u in mg.get("/api/auth/users").json()}
    assert users["mgr_a"]["branch"] == "BranchA" and users["management1"]["branch"] is None
    _voices(mg, [("BranchA", "2026-09-01", 1, "รอนานมาก พนักงานไม่สนใจ"), ("BranchA", "2026-09-02", 5, "แซลมอนสดมาก"),
                 ("BranchB", "2026-09-03", 1, "ของหมดตั้งแต่ทุ่มนึง"), ("BranchB", "2026-09-04", 4, "ดี")])
    ids = {r["branch_name"]: r["id"] for r in mg.get("/api/feedback/items").json()["items"]}
    assert set(ids) == {"BranchA", "BranchB"}

    a = _fresh()
    j = _login(a, "mgr_a", PW).json()
    assert j["branch"] == "BranchA" and "customer.feedback" in j["modules"]
    assert a.get("/api/auth/me").json()["branch"] == "BranchA"
    # reads: the scope wins over any query
    s = a.get("/api/feedback/summary", params={"branch": "BranchB"}).json()
    assert s["total"] == 2 and s["filters"]["branch"] == "BranchA" and [b["branch"] for b in s["branches"]] == ["BranchA"]
    items = a.get("/api/feedback/items", params={"branch": "BranchB"}).json()["items"]
    assert {i["branch_name"] for i in items} == {"BranchA"}
    d = a.get("/api/feedback/digest", params={"days": 30, "today": "2026-09-05", "group": "franchise"}).json()
    assert d["voices"] == 2
    # writes: own branch fine, the other branch's row is 403 on every per-item endpoint
    mine, theirs = ids["BranchA"], ids["BranchB"]
    assert a.post(f"/api/feedback/{mine}/flag", json={"flag": True, "note": "x"}).status_code == 200
    assert a.post(f"/api/feedback/{theirs}/flag", json={"flag": True, "note": "x"}).status_code == 403
    assert a.get(f"/api/feedback/{theirs}/reply-draft").status_code == 403
    assert a.post(f"/api/feedback/{theirs}/reply", json={"text": "ขออภัยค่ะ"}).status_code == 403
    assert a.post(f"/api/feedback/{theirs}/handled", json={"note": "โทรแล้ว"}).status_code == 403
    assert a.delete(f"/api/feedback/{theirs}/reply").status_code == 403
    assert a.delete(f"/api/feedback/{theirs}").status_code == 403
    assert a.post(f"/api/feedback/{mine}/handled", json={"note": "โทรแล้ว"}).status_code == 200
    # an upload from a branch login lands on that branch whatever the rows say
    r = a.post("/api/feedback/upload", json={"rows": [{"branch": "BranchB", "date": "2026-09-05", "source": "Google", "rating": 2, "text": "ทดสอบ", "external_id": "up1"}]}).json()
    assert r["inserted"] == 1
    assert {i["branch_name"] for i in a.get("/api/feedback/items").json()["items"]} == {"BranchA"}
    assert mg.get("/api/feedback/items", params={"branch": "BranchA"}).json()["items"][0]["text"] == "ทดสอบ"
    # actions: created on the scope whatever the body says; another branch's action is 403; the list is scoped
    act = a.post("/api/feedback/actions", json={"title": "แก้รอนาน", "branch": "BranchB", "theme": "รอนาน"}).json()
    assert act["branch_name"] == "BranchA"
    other = mg.post("/api/feedback/actions", json={"title": "ของหมด", "branch": "BranchB", "theme": "ปริมาณ/ของหมด"}).json()
    assert a.post(f"/api/feedback/actions/{other['id']}", json={"status": "doing"}).status_code == 403
    assert a.post(f"/api/feedback/actions/{act['id']}", json={"status": "doing"}).status_code == 200
    assert [x["branch_name"] for x in a.get("/api/feedback/actions", params={"branch": "BranchB"}).json()["actions"]] == ["BranchA"]
    assert len(mg.get("/api/feedback/actions").json()["actions"]) == 2
    # survey links: only the own branch; bulk and another branch are refused
    assert a.post("/api/feedback/survey-links", json={"all": True}).status_code == 403
    assert a.post("/api/feedback/survey-links", json={"branch_id": 2}).status_code == 403
    assert a.post("/api/feedback/survey-links", json={"branch_id": 1}).json()["branch"] == "BranchA"
    assert [x["branch"] for x in a.get("/api/feedback/survey-links").json()["links"]] == ["BranchA"]
    assert len(mg.get("/api/feedback/survey-links").json()["links"]) == 2
    # the scope is changed / cleared by management; takes effect at the next login
    assert mg.put("/api/auth/users/mgr_a", json={"branch": "Nowhere"}).status_code == 400
    assert mg.put("/api/auth/users/mgr_a", json={"branch": ""}).status_code == 200
    a2 = _fresh(); assert _login(a2, "mgr_a", PW).json()["branch"] is None
    assert a2.get("/api/feedback/summary").json()["total"] == 5
    # the digest reports who answers how fast, per branch
    dg = mg.get("/api/feedback/digest", params={"days": 30, "today": "2026-09-05"}).json()
    rb = {x["branch"]: x for x in dg["response_by_branch"]}
    assert rb["BranchA"]["answered"] == 1 and rb["BranchA"]["rate"] == 50 and rb["BranchB"]["answered"] == 0
    assert "ตอบช้าสุด: BranchA 50% (1/2" in notify.weekly_digest(dg)   # ≥ 2 answerable → listed; BranchB has one
    dg["response_by_branch"] = [{"branch": "BranchB", "answerable": 1, "answered": 0, "rate": 0, "median_days": None}]
    assert "ตอบช้าสุด" not in notify.weekly_digest(dg)


def test_token_carries_the_branch_and_scope_reads_it(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1"); monkeypatch.setenv("OSHINEI_AUTH_SECRET", "k1")
    tok = auth.make_token(1, "u", "operation", branch="ขอนแก่น")
    assert auth.parse_token(tok)["b"] == "ขอนแก่น"
    assert "b" not in auth.parse_token(auth.make_token(1, "u", "operation"))

    class R:                                     # the only thing scope_branch reads
        cookies = {auth.SESSION_COOKIE: tok}
    assert auth.scope_branch(R()) == "ขอนแก่น"
    monkeypatch.setenv("OSHINEI_AUTH", "0")
    assert auth.scope_branch(R()) is None


def test_branch_chats_get_their_own_alert_and_digest(tmp_path, monkeypatch):
    # Pin the digest clock: the seeded voices are dated, so against the REAL
    # today they fall out of the 7-day window and the digest goes empty.
    monkeypatch.setattr(A, "_today", lambda: "2026-09-05")
    db = tmp_path / "local.db"; _seed_db(db)
    sink = FF.DbSink(db)
    sink.upload([{"branch": "ขอนแก่น", "date": "2026-09-03", "source": "Google", "rating": 1, "text": "รอนานมาก", "external_id": "b1"},
                 {"branch": "โคราช1", "date": "2026-09-03", "source": "Google", "rating": 1, "text": "ของหมด", "external_id": "b2"}], by="t")
    cfg = {"telegram": {"branches": {"ขอนแก่น": "-100111"}}}
    to, lines = [], []
    notifier_to = lambda text, chat: (to.append((chat, text)), True)[1]   # noqa: E731
    neg = [{"branch": "ขอนแก่น", "rating": 1, "text": "รอนานมาก"}, {"branch": "โคราช1", "rating": 1, "text": "ของหมด"}]
    assert FF._branch_alerts(neg, cfg, "Google", "https://app", notifier_to, lines.append) == 1
    assert to[0][0] == "-100111" and "รอนานมาก" in to[0][1] and "ของหมด" not in to[0][1]
    assert FF._branch_alerts(neg, {}, "Google", "https://app", notifier_to, lines.append) == 0
    monkeypatch.setattr(FF, "_weekday", lambda: 0)
    main_sent = []
    assert FF._digest_step(_args(), sink, lambda t: (main_sent.append(t), True)[1], lines.append, cfg=cfg, notifier_to=notifier_to) is True
    assert len(main_sent) == 1 and len(to) == 2 and to[1][0] == "-100111"
    assert "รอนาน 1" in to[1][1] and "ของหมด" not in to[1][1]         # the branch digest carries the branch's voices only
    assert sink.digest(7, "โคราช1")["voices"] == 1
