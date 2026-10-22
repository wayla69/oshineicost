"""PR-F4 - the franchise owner writes to head office and reads the replies (routers/franchise_msgs.py).

  * the auth rule: GET + W on /api/franchise/messages for franchise.view (the owner) and franchise.inbox (management + operation);
    no other role reaches it, and the owner's module set is unchanged;
  * the owner creates, lists, reads and replies on its OWN branch's threads only; another branch's thread, a franchise account
    without a branch and a role without access are refused 403 X-Denied: role; the branch is never taken from the body or query;
  * no write token is asked of either side (the session is the gate), and the token opens nothing more;
  * input: plain text, 1,000 characters, control characters stripped, a <script> body stored as text and drawn escaped; topics and
    the context are whitelisted; a per-login daily limit (429);
  * head office: the inbox with branch / status filters, the unread badge (/api/nav/badges "messages"), close / reopen; a closed
    thread takes no reply; messages are immutable (no endpoint, and two triggers refuse UPDATE / DELETE);
  * push: a new owner message reaches management's devices only (kind franchise_msg), a reply reaches that branch's owner only
    (kind franchise_reply); neither carries a figure;
  * the owner's payload names no login, no other branch and no internal field; the tables survive the sync's DB swap;
  * the router imports its neighbours relatively (on Fly the app is backend.main).
"""
import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth
import core
from test_bom_center_batch2 import _fn, _node, needs_node

TOKEN = "owner-token-f4"
ROOT = Path(__file__).resolve().parent.parent
PW = "Temp@12345"
WRITES = ("POST", "PUT", "PATCH", "DELETE")


# ── the matrix (no server) ───────────────────────────────────────────────────
def test_the_rule_names_the_owner_and_head_office_only():
    rule = [r for r in auth.MODULE_RULES if r[1] == "/api/franchise/messages"]
    assert rule == [("*", "/api/franchise/messages", {"franchise.view", "franchise.inbox"})]
    assert auth.ROLE_MODULES["franchise"] == {"franchise.view"}                       # the owner's module set is unchanged
    holders = {r for r, m in auth.ROLE_MODULES.items() if "franchise.inbox" in m}
    assert holders == {"management", "operation"}
    fr = auth.ROLE_MODULES["franchise"]
    for m in ("GET",) + WRITES:
        assert auth.path_allowed(m, "/api/franchise/messages", fr)
        assert auth.path_allowed(m, "/api/franchise/messages/12/reply", fr)
        assert auth.path_allowed(m, "/api/franchise/messages/inbox", auth.ROLE_MODULES["operation"])
    assert not any(auth.path_allowed(m, "/api/franchise/summary", fr) for m in WRITES)  # the summary stays read-only
    assert not auth.path_allowed("GET", "/api/franchise/summary", auth.ROLE_MODULES["operation"])   # the inbox rule opens nothing else
    for role in ("marketing", "analyst", "accounting", "innovation", "salessupport", "warehouse", "branch"):
        mods = auth.ROLE_MODULES[role]
        assert not any(auth.path_allowed(m, "/api/franchise/messages/inbox", mods) for m in ("GET",) + WRITES), role


def test_the_tables_survive_the_sync_swap_and_the_router_imports_relatively():
    assert {"franchise_msg_thread", "franchise_msg_message"} <= set(core._PRESERVED_EXACT)
    for rel in ("routers/franchise_msgs.py", "routers/nav.py", "routers/push_api.py"):
        src = (Path(__file__).resolve().parent / rel).read_text(encoding="utf-8")
        assert not re.search(r"^\s*from routers[. ]", src, re.M) and not re.search(r"^\s*import routers", src, re.M), rel


def test_clean_text_keeps_plain_text_only():
    from routers import franchise_msgs as M
    t = M.clean_text("  สวัสดี\x00\x07 ครับ\r\n\r\n\r\n\r\nบรรทัด\u202eใหม่\t<script>alert(1)</script>  ", 1000)
    assert t == "สวัสดี ครับ\n\nบรรทัดใหม่ <script>alert(1)</script>"
    with pytest.raises(Exception):
        M.clean_text(" \n\t ", 1000)
    with pytest.raises(Exception):
        M.clean_text("ก" * 1001, 1000)
    assert M.clean_text("ก" * 1000, 1000) == "ก" * 1000


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


def _db():
    import main
    c = sqlite3.connect(str(main.DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def _drop_msgs():
    c = _db()
    c.execute("DROP TABLE IF EXISTS franchise_msg_message")       # the immutable rows cannot be DELETEd (triggers): drop the table
    c.execute("DROP TABLE IF EXISTS franchise_msg_thread")
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='push_subscription'").fetchone():
        c.execute("DELETE FROM push_subscription WHERE endpoint LIKE 'https://push.example/f4-%'")
    c.commit()
    c.close()


@pytest.fixture
def world(client, app_db, auth_on):
    """Owners of BranchB and BranchA, one with no branch; management1, operation1 and marketing1 from the seed."""
    mg = _login("management1")
    for name, br in (("own_f4_b", "BranchB"), ("own_f4_a", "BranchA")):
        r = mg.post("/api/auth/users", headers={"X-Upload-Token": TOKEN},
                    json={"username": name, "password": PW, "role": "franchise", "branch": br})
        assert r.status_code in (200, 409), r.text
    c = _db()
    h = auth.hash_password(PW)
    c.execute("INSERT OR REPLACE INTO app_user(username, pw_hash, pw_salt, role, must_change, branch) VALUES('own_f4_nb', ?, ?, 'franchise', 0, NULL)",
              (h["hash"], h["salt"]))
    c.commit()
    c.close()
    _drop_msgs()
    try:
        yield {"b": _login("own_f4_b", PW), "a": _login("own_f4_a", PW), "nb": _login("own_f4_nb", PW), "mg": mg,
               "op": _login("operation1"), "mk": _login("marketing1")}
    finally:
        _drop_msgs()
        c = _db()
        c.execute("DELETE FROM app_user WHERE username IN ('own_f4_b','own_f4_a','own_f4_nb')")
        c.commit()
        c.close()


def _ask(c, body="ยอดขายสัปดาห์นี้ลดลง สาเหตุคืออะไร", topic="sales", context=None, **kw):
    return c.post("/api/franchise/messages", json={"topic": topic, "body": body, "context": context}, **kw)


def _role_denied(r):
    return r.status_code == 403 and r.headers.get("X-Denied") == "role"


def test_the_owner_creates_lists_reads_and_replies_on_its_own_branch(world):
    b = world["b"]
    ctx = {"level": "fix", "month": "2026-06", "line": "ส่วนลดเดือนนี้ 4.1% สูงกว่านโยบาย 2.5%"}
    r = _ask(b, context=ctx)
    assert r.status_code == 200, r.text
    t = r.json()["thread"]
    assert t["status"] == "open" and t["topic_th"] == "ยอดขาย" and t["context"]["level_th"] == "ต้องแก้" and t["unread"] == 0
    lst = b.get("/api/franchise/messages").json()
    assert [x["id"] for x in lst["threads"]] == [t["id"]] and {x["id"] for x in lst["topics"]} == {"sales", "cost", "orders", "controls", "other"}
    th = b.get(f"/api/franchise/messages/{t['id']}").json()
    assert [m["side"] for m in th["messages"]] == ["owner"] and th["messages"][0]["author"] == "เจ้าของสาขาBranchB"
    r = b.post(f"/api/franchise/messages/{t['id']}/reply", json={"body": "ขอเพิ่มเติมครับ"})
    assert r.status_code == 200 and [m["side"] for m in r.json()["messages"]] == ["owner", "owner"]
    # the branch is the session's: a body / query naming another branch changes nothing or is refused
    assert _role_denied(b.get("/api/franchise/messages", params={"branch_id": 1}))
    r = b.post("/api/franchise/messages", params={"branch_id": 1}, json={"topic": "sales", "body": "x"})
    assert _role_denied(r)
    r = b.post("/api/franchise/messages", json={"topic": "sales", "body": "y", "branch_id": 1, "branch": "BranchA"})
    assert r.status_code == 200
    with _db() as c:
        assert {x[0] for x in c.execute("SELECT DISTINCT branch_id FROM franchise_msg_thread")} == {2}


def test_another_branch_no_branch_and_other_roles_are_refused(world):
    t = _ask(world["b"]).json()["thread"]
    a = world["a"]
    assert a.get("/api/franchise/messages").json()["threads"] == []                      # BranchA sees none of BranchB's
    assert _role_denied(a.get(f"/api/franchise/messages/{t['id']}"))
    assert _role_denied(a.post(f"/api/franchise/messages/{t['id']}/reply", json={"body": "แทรก"}))
    assert _role_denied(a.post(f"/api/franchise/messages/{t['id']}/status", json={"status": "closed"}))
    assert _role_denied(a.get("/api/franchise/messages/inbox"))                          # the inbox is head office's
    nb = world["nb"]
    for r in (nb.get("/api/franchise/messages"), _ask(nb), nb.get(f"/api/franchise/messages/{t['id']}")):
        assert _role_denied(r), r.status_code
    mk = world["mk"]                                                                       # marketing: no module, the middleware refuses
    for r in (mk.get("/api/franchise/messages/inbox"), mk.get(f"/api/franchise/messages/{t['id']}"),
              mk.post(f"/api/franchise/messages/{t['id']}/reply", json={"body": "x"})):
        assert _role_denied(r), r.status_code
    # head office cannot pose as an owner (no new thread) and the owner cannot close
    assert _role_denied(_ask(world["mg"]))
    assert _role_denied(world["b"].post(f"/api/franchise/messages/{t['id']}/status", json={"status": "closed"}))
    assert world["b"].get("/api/franchise/messages/999999").status_code == 404


def test_no_write_token_is_asked_and_the_token_opens_nothing_more(world):
    r = _ask(world["b"])                                  # the test client sends no X-Upload-Token
    assert r.status_code == 200 and r.headers.get("X-Denied") is None
    tid = r.json()["thread"]["id"]
    assert world["op"].post(f"/api/franchise/messages/{tid}/reply", json={"body": "รับทราบครับ"}).status_code == 200
    r = world["b"].post("/api/franchise/summary", headers={"X-Upload-Token": TOKEN}, json={})
    assert _role_denied(r)
    r = world["b"].post("/api/bom-center/order", headers={"X-Upload-Token": TOKEN}, json={})
    assert _role_denied(r)


def test_input_rules(world):
    b = world["b"]
    assert _ask(b, body="ก" * 1001).status_code == 400
    assert _ask(b, body="   \n ").status_code == 400
    assert _ask(b, topic="hack").status_code == 400
    assert _ask(b, context={"level": "boom"}).status_code == 400
    assert _ask(b, context={"month": "2026-13"}).status_code == 400
    assert _ask(b, context={"line": "ก" * 301}).status_code == 400
    assert _ask(b, context="text").status_code == 422
    evil = "<script>alert(1)</script><img src=x onerror=alert(2)>\x00"
    t = _ask(b, body=evil, context={"line": "<b>x</b>"}).json()["thread"]
    with _db() as c:
        assert c.execute("SELECT body FROM franchise_msg_message WHERE thread_id=?", (t["id"],)).fetchone()[0] == evil[:-1]
    th = world["mg"].get(f"/api/franchise/messages/{t['id']}").json()
    assert th["messages"][0]["body"] == evil[:-1] and th["context"]["line"] == "<b>x</b>"     # text, as typed; the page escapes it


def test_the_daily_limit_per_login(world, monkeypatch):
    from routers import franchise_msgs as M
    monkeypatch.setattr(M, "DAILY_LIMIT_OWNER", 3)
    b = world["b"]
    tid = _ask(b).json()["thread"]["id"]
    assert b.post(f"/api/franchise/messages/{tid}/reply", json={"body": "2"}).status_code == 200
    assert _ask(b).status_code == 200                                                   # the third message of the day
    r = b.post(f"/api/franchise/messages/{tid}/reply", json={"body": "4"})
    assert r.status_code == 429 and "20" not in r.json()["detail"] and "3" in r.json()["detail"]
    assert _ask(b).status_code == 429
    assert _ask(world["a"]).status_code == 200                                          # per login, not per app


def test_head_office_inbox_badges_reply_close_and_reopen(world):
    b, mg, op = world["b"], world["mg"], world["op"]
    t1 = _ask(b, topic="cost", body="ต้นทุนวัตถุดิบสูงขึ้น").json()["thread"]
    t2 = _ask(world["a"], topic="orders", body="ของส่งไม่ครบ").json()["thread"]
    inbox = mg.get("/api/franchise/messages/inbox").json()
    assert {x["id"] for x in inbox["threads"]} == {t1["id"], t2["id"]} and inbox["unread"] == 2
    row = next(x for x in inbox["threads"] if x["id"] == t1["id"])
    assert row["branch"] == "BranchB" and row["unread"] == 1 and row["last"]["body"] == "ต้นทุนวัตถุดิบสูงขึ้น"
    assert {(x["branch"], x["open"]) for x in inbox["branches"]} == {("BranchA", 1), ("BranchB", 1)}
    assert [x["id"] for x in mg.get("/api/franchise/messages/inbox", params={"branch_id": 2}).json()["threads"]] == [t1["id"]]
    assert op.get("/api/franchise/messages/inbox").status_code == 200
    assert mg.get("/api/nav/badges").json().get("messages") == {"fmsg": 2}
    assert op.get("/api/nav/badges").json().get("messages") == {"fmsg": 2}
    assert "messages" not in world["mk"].get("/api/nav/badges").json()
    assert "messages" not in b.get("/api/nav/badges").json()                             # nothing unread for the owner yet
    # head office opens and replies: its unread clears, the owner's appears
    th = mg.get(f"/api/franchise/messages/{t1['id']}").json()
    assert th["unread"] == 0 and th["branch"] == "BranchB"
    r = mg.post(f"/api/franchise/messages/{t1['id']}/reply", json={"body": "กำลังตรวจสอบราคาจากผู้ขายครับ"})
    assert r.status_code == 200, r.text
    assert mg.get("/api/nav/badges").json().get("messages") == {"fmsg": 1}
    assert b.get("/api/nav/badges").json().get("messages") == {"franchise": 1}
    assert "messages" not in world["a"].get("/api/nav/badges").json()                   # BranchA's owner has no reply
    lst = b.get("/api/franchise/messages").json()
    assert lst["unread"] == 1
    th = b.get(f"/api/franchise/messages/{t1['id']}").json()
    assert [m["side"] for m in th["messages"]] == ["owner", "hq"] and th["messages"][1]["author"] == "ผู้บริหาร"   # a role label, never a login
    assert "messages" not in b.get("/api/nav/badges").json()
    # close: the owner can read it, nobody can reply; the default inbox leaves it out; reopen brings it back
    r = mg.post(f"/api/franchise/messages/{t1['id']}/status", json={"status": "closed"})
    assert r.status_code == 200 and r.json()["status"] == "closed" and r.json()["closed_by"] == "ผู้บริหาร"
    assert b.post(f"/api/franchise/messages/{t1['id']}/reply", json={"body": "ยังไม่ครบ"}).status_code == 409
    assert mg.post(f"/api/franchise/messages/{t1['id']}/reply", json={"body": "x"}).status_code == 409
    assert b.get("/api/franchise/messages").json()["threads"][-1]["status"] == "closed"         # open first, closed after
    assert t1["id"] not in {x["id"] for x in mg.get("/api/franchise/messages/inbox").json()["threads"]}
    assert t1["id"] in {x["id"] for x in mg.get("/api/franchise/messages/inbox", params={"status": "closed"}).json()["threads"]}
    assert mg.post(f"/api/franchise/messages/{t1['id']}/status", json={"status": "bogus"}).status_code == 400
    r = op.post(f"/api/franchise/messages/{t1['id']}/status", json={"status": "open"})
    assert r.status_code == 200 and r.json()["status"] == "open"
    assert b.post(f"/api/franchise/messages/{t1['id']}/reply", json={"body": "ขอบคุณครับ"}).status_code == 200
    # audit: who and when on every row, kept server side
    with _db() as c:
        rows = c.execute("SELECT side, author_user, author_name, author_role, created_at FROM franchise_msg_message WHERE thread_id=? ORDER BY id",
                         (t1["id"],)).fetchall()
        assert [(r[0], r[1], r[3]) for r in rows] == [("owner", "own_f4_b", "franchise"), ("hq", "management1", "management"),
                                                       ("owner", "own_f4_b", "franchise")]
        assert all(r[2] and re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$", r[4]) for r in rows)
        th_row = c.execute("SELECT created_by, closed_by FROM franchise_msg_thread WHERE id=?", (t1["id"],)).fetchone()
        assert th_row[0] == "own_f4_b" and th_row[1] is None                              # reopened: the close is cleared


def test_messages_are_immutable(world):
    _ask(world["b"])
    with _db() as c:
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("UPDATE franchise_msg_message SET body='edited'")
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("DELETE FROM franchise_msg_message")
    import main
    paths = {(m, r.path) for r in main.app.routes if getattr(r, "path", "").startswith("/api/franchise/messages") for m in r.methods}
    assert not any(m in ("PUT", "PATCH", "DELETE") for m, _ in paths), paths


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


def test_the_owner_payload_names_no_login_no_other_branch_and_no_internal_field(world):
    b, mg = world["b"], world["mg"]
    t = _ask(b).json()["thread"]
    mg.post(f"/api/franchise/messages/{t['id']}/reply", json={"body": "ตอบแล้วครับ"})
    _ask(world["a"])
    payloads = [b.get("/api/franchise/messages").json(), b.get(f"/api/franchise/messages/{t['id']}").json(),
                b.post(f"/api/franchise/messages/{t['id']}/reply", json={"body": "ok"}).json()]
    forbidden = ("management1", "own_f4_b", "username", "password", "token", "author_user", "created_by", "closed_by", "branch_id",
                 "cashier", "staff", "price", "recipe")
    for d in payloads:
        hits = [(p, k, s) for p, k, s in _walk(d) if any(w in s.lower() for w in forbidden)]
        assert not hits, hits
        text = json.dumps(d, ensure_ascii=False)
        assert "BranchA" not in text and '"branch"' not in text and "waiting_owner" not in text


# ── push ─────────────────────────────────────────────────────────────────────
def _keys(monkeypatch):
    monkeypatch.setenv("OSHINEI_VAPID_PRIVATE", "cHJpdmF0ZQ")
    monkeypatch.setenv("OSHINEI_VAPID_PUBLIC", "cHVibGlj")


@pytest.fixture
def pushed(world, monkeypatch):
    from routers import franchise_msgs as M
    _keys(monkeypatch)
    log = []
    monkeypatch.setattr(M, "_PUSH_SEND", lambda sub, data: (log.append((sub["endpoint"], json.loads(data))), 201)[1])
    for who in ("b", "a", "mg", "op", "mk"):
        r = world[who].post("/api/push/subscribe", json={"endpoint": f"https://push.example/f4-{who}", "keys": {"p256dh": "p", "auth": "a"}})
        assert r.status_code == 200, (who, r.text)
    world["log"] = log
    yield world


def _kinds(c, who):
    return [k["id"] for k in c.post("/api/push/prefs", json={"endpoint": f"https://push.example/f4-{who}"}).json()["kinds"]]


def test_push_kinds_are_offered_to_their_roles_only(pushed):
    assert _kinds(pushed["b"], "b") == ["franchise", "franchise_reply"]
    assert "franchise_msg" in _kinds(pushed["mg"], "mg") and "franchise_reply" not in _kinds(pushed["mg"], "mg")
    assert "franchise_msg" not in _kinds(pushed["op"], "op") and "franchise_msg" not in _kinds(pushed["mk"], "mk")


def test_a_new_message_reaches_management_and_a_reply_reaches_that_owner_only(pushed):
    from routers import franchise_msgs as M
    log = pushed["log"]
    t = _ask(pushed["b"], topic="orders").json()["thread"]
    assert [e for e, _ in log] == ["https://push.example/f4-mg"]                         # management only (not operation, not marketing, no owner)
    p = log[0][1]
    assert p["url"] == f"/?go=fmsg&t={t['id']}" and "BranchB" in p["body"] and "ใบสั่งซื้อและเคลม" in p["body"]
    assert not re.search(r"\d", p["body"])                                               # no figure in the text
    log.clear()
    pushed["op"].post(f"/api/franchise/messages/{t['id']}/reply", json={"body": "ส่งเพิ่มพรุ่งนี้"})
    assert [e for e, _ in log] == ["https://push.example/f4-b"]                          # that branch's owner only
    assert log[0][1]["url"] == f"/?go=franchise&t={t['id']}" and "ส่งเพิ่มพรุ่งนี้" not in log[0][1]["body"]
    log.clear()
    pushed["b"].post(f"/api/franchise/messages/{t['id']}/reply", json={"body": "ขอบคุณครับ"})
    assert [e for e, _ in log] == ["https://push.example/f4-mg"]
    # a muted device hears nothing; the target lists are the pure rule
    log.clear()
    pushed["mg"].post("/api/push/prefs", json={"endpoint": "https://push.example/f4-mg", "off": ["franchise_msg"]})
    _ask(pushed["b"])
    assert log == []
    import main
    with main.get_db() as con:
        assert M.hq_push_users(con) == ["management1"]
        assert M.owner_push_users(con, "BranchB") == ["own_f4_b"] and M.owner_push_users(con, "BranchA") == ["own_f4_a"]


# ── sign-in off (local development) ──────────────────────────────────────────
def test_auth_off_the_owner_side_names_a_branch(client):
    _drop_msgs()
    try:
        assert client.get("/api/franchise/messages").status_code == 400
        r = client.post("/api/franchise/messages", params={"branch_id": 2}, json={"topic": "other", "body": "ทดสอบ"})
        assert r.status_code == 200, r.text
        assert client.get("/api/franchise/messages/inbox").json()["threads"][0]["branch"] == "BranchB"
    finally:
        _drop_msgs()


# ── the page draws what people typed as text ─────────────────────────────────
@needs_node
def test_the_render_helpers_escape_what_people_typed():
    core_js = (ROOT / "js" / "01-core.js").read_text(encoding="utf-8")
    frc = (ROOT / "js" / "17-franchise.js").read_text(encoding="utf-8")
    esc_src = re.search(r"^const esc = s => .*?\n.*?;\n", core_js, re.M | re.S).group(0)
    pre = (esc_src + "const fmtN = n => String(n);\n"
           + re.search(r"^const _FM_TH_MON = .*?;$", frc, re.M).group(0) + "\n"
           + "\n".join(_fn(frc, n) for n in ("_fmWhen", "_fmMonthTh", "_fmCtxText", "_fmMessagesHtml", "_fmRowHtml")))
    evil = "<script>alert(1)</script><img src=x onerror=alert(2)>\"'"
    msgs = [{"side": "owner", "author": "<b>เจ้าของ</b>", "body": evil, "created_at": "2026-10-22 14:05:09"},
            {"side": "hq", "author": "ผู้บริหาร", "body": "ok", "created_at": "2026-10-22 15:00:00"}]
    html = _node(pre, f"_fmMessagesHtml({json.dumps(msgs, ensure_ascii=False)}, 'owner')")
    assert "<script" not in html and "<img" not in html and "<b>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "&quot;&#39;" in html
    assert html.index("fm-msg fm-mine") < html.index("ผู้บริหาร")                         # the reader's own message is marked as its own
    row = _node(pre, f"_fmRowHtml({json.dumps({'id': '1);alert(9', 'topic_th': evil, 'branch': evil, 'status': 'closed', 'unread': 1, 'last_at': '2026-10-22 14:05:09', 'last': {'side': 'hq', 'body': evil}}, ensure_ascii=False)}, 'fmsgOpen', true)")
    assert "<script" not in row and "<img" not in row and "fmsgOpen(0)" in row and "alert(9" not in row
    ctx = _node(pre, "_fmCtxText({level: 'fix', month: '2026-10', line: 'ส่วนลด'})")
    assert ctx == "ต้องแก้ · ต.ค. 2569 · ส่วนลด"


def test_the_pages_draw_message_text_through_esc_only():
    """No innerHTML of a message field without esc(): every interpolation of body / author / topic / branch / line goes through it."""
    for rel in ("js/17-franchise.js", "js/19-fmsg.js"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        start = src.index("PR-F4") if rel.endswith("17-franchise.js") else 0
        for m in re.finditer(r"\$\{([^{}]*\.(?:body|author|topic_th|branch|line|label)\b[^{}]*)\}", src[start:]):
            expr = m.group(1)
            for f in re.finditer(r"[\w\]\)]\.(?:body|author|topic_th|branch|line|label)\b", expr):
                assert "esc(" in expr[:f.start()], (rel, m.group(0))      # the field sits inside an esc( ... ) call
