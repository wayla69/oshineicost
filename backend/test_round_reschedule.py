# -*- coding: utf-8 -*-
"""เลื่อนรอบส่ง (2026-10-29): ONE delivery of a round moves to a later day (bom_center/reschedule.py).

The promises:
  * Sales Support records a move for one or several branches with a required reason (free text required for อื่น ๆ); it can be
    edited and cancelled (a cancel needs a reason too); every change is audited; the weekly calendar is not changed;
  * only a regular delivery day of the round, only a later day (1..6 days), never twice for the same delivery;
  * the plan follows the new day: delivery_date_for / cover_for_delivery start the cover on the day the delivery actually comes,
    and the delivery before a postponed one covers the extra day; the cutoff stays the regular one;
  * an order of that branch + round + regular day carries `delay` (on the branch's list and detail and on the queue); its status
    is untouched;
  * the branch is told: a push to that branch's own devices and a notice in plain words;
  * the write is Sales Support's / management's: the branch and the analyst are refused (403), the table survives the DB swap.
"""
import sqlite3
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

import auth
import core
from bom_center import forecast as F
from bom_center import hub as HB
from bom_center import orders as OR
from bom_center import reschedule as RS
from bom_center.clock import BKK
from test_bom_center_s4b import con  # noqa: F401  (sales to 5 Oct 2026 at branch 1; default calendar: r1 Wed, r2 Sat, salmon Mon)

TODAY = date(2026, 10, 5)                      # Monday
WED, THU, SAT, SUN = date(2026, 10, 7), date(2026, 10, 8), date(2026, 10, 10), date(2026, 10, 11)
NOW = datetime(2026, 10, 5, 8, 0, tzinfo=BKK)
AS_OF = date(2026, 10, 5)
CRAB = "13-01-009"


def _audit(con, like="round_reschedule%"):  # noqa: F811
    return [r[0] for r in con.execute("SELECT action FROM bc_audit WHERE action LIKE ? ORDER BY id", (like,))]


# ── the rules of a move ──────────────────────────────────────────────────────
def test_record_needs_a_reason_and_a_later_regular_day(con):  # noqa: F811
    rec = lambda **k: RS.record(con, "ss1", k.get("ids", [1]), k.get("rnd", "1"), k.get("orig", WED), k.get("new", THU),  # noqa: E731
                                k.get("code", "transport"), k.get("note"), today=TODAY)
    with pytest.raises(ValueError, match="เลือกเหตุผล"):
        rec(code="")
    with pytest.raises(ValueError, match="อื่น ๆ"):
        rec(code="other", note="  ")
    with pytest.raises(ValueError, match="หลังวันส่งตามปกติ"):
        rec(new=WED)
    with pytest.raises(ValueError, match="ไม่เกิน 6 วัน"):
        rec(new=date(2026, 10, 14))
    with pytest.raises(ValueError, match="ไม่ได้ส่งวันพฤหัส"):         # round 1 of branch 1 is delivered on Wednesday
        rec(orig=THU, new=SAT)
    with pytest.raises(ValueError, match="ผ่านไปแล้ว"):
        RS.record(con, "ss1", [1], "1", date(2026, 9, 30), date(2026, 10, 1), "transport", today=TODAY)
    with pytest.raises(ValueError, match="อย่างน้อย 1 สาขา"):
        rec(ids=[])
    rows = rec(code="other", note="รถเสียกลางทาง")
    assert len(rows) == 1 and rows[0]["status"] == "active" and rows[0]["reason_note"] == "รถเสียกลางทาง"
    with pytest.raises(ValueError, match="อยู่แล้ว"):                     # one active move per delivery: edit it instead
        rec()
    assert _audit(con) == ["round_reschedule"]
    # the weekly calendar is untouched
    assert F.calendar(con, 1)["1"]["delivery_weekday"] == 3 and not F.calendar(con, 1)["1"]["filled"]


def test_several_branches_at_once_then_edit_and_cancel(con):  # noqa: F811
    con.execute("INSERT OR IGNORE INTO branches(branch_id, name) VALUES(2, 'สาขาสอง')")
    rows = RS.record(con, "ss1", [1, 2], "2", SAT, SUN, "holiday", None, today=TODAY)
    assert [r["branch_id"] for r in rows] == [1, 2] and len({r["batch_id"] for r in rows}) == 1
    a = rows[0]
    with pytest.raises(ValueError, match="ไม่มีอะไรเปลี่ยน"):
        RS.edit(con, "ss2", a["id"], SUN, "holiday", None)
    e = RS.edit(con, "ss2", a["id"], date(2026, 10, 12), "supplier_late", "ปลาเข้าช้า")
    assert e["new_date"] == "2026-10-12" and e["updated_by"] == "ss2"
    with pytest.raises(ValueError, match="เหตุผลที่ยกเลิก"):
        RS.cancel(con, "ss2", a["id"], " ")
    c = RS.cancel(con, "ss2", a["id"], "รถพร้อมแล้ว")
    assert c["status"] == "cancelled" and c["cancel_reason"] == "รถพร้อมแล้ว"
    with pytest.raises(ValueError, match="ยกเลิกแล้ว"):
        RS.edit(con, "ss2", a["id"], SUN, "holiday", None)
    assert _audit(con) == ["round_reschedule", "round_reschedule", "round_reschedule_edit", "round_reschedule_cancel"]
    old, new = con.execute("SELECT old_value, new_value FROM bc_audit WHERE action='round_reschedule_edit'").fetchone()
    assert "2026-10-11" in old and "2026-10-12" in new and "ของจากผู้ขายมาช้า" in new
    # after the cancel the delivery is back on its regular day; branch 2's move stands
    assert RS.actual(con, 1, "2", SAT) == SAT and RS.actual(con, 2, "2", SAT) == SUN
    # a cancelled move may be recorded again
    assert RS.record(con, "ss1", [1], "2", SAT, SUN, "transport", None, today=TODAY)


# ── the plan follows the new day ─────────────────────────────────────────────
def test_delivery_date_for_and_the_cover_window(con):  # noqa: F811
    cyc = F.cutoff_for(WED).date()                                          # Mon 5 Oct
    assert RS.delivery_date_for(con, 1, "1", cyc) == WED
    assert F.cover_for_delivery(con, 1, "1", WED) == (cyc, WED, WED, date(2026, 10, 9))
    RS.record(con, "ss1", [1], "1", WED, THU, "transport", None, today=TODAY)
    assert RS.delivery_date_for(con, 1, "1", cyc) == THU
    # the cover starts on Thursday; the cutoff (and the cycle) stay the regular ones
    assert F.cover_for_delivery(con, 1, "1", WED) == (cyc, WED, THU, date(2026, 10, 9))
    # round 2 (Sat) postponed to Sunday: the round-1 order before it lasts one more day
    RS.record(con, "ss1", [1], "2", SAT, SUN, "transport", None, today=TODAY)
    assert F.cover_for_delivery(con, 1, "1", WED)[3] == SAT
    assert F.cover_for_delivery(con, 1, "2", SAT)[2] == SUN


@pytest.mark.usefixtures("recipe_mode")           # the forecast rows are built from recipes (stock mode needs 14 days of slips)
def test_the_plan_covers_from_the_new_day_and_says_so(con):  # noqa: F811
    p0 = OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    assert p0["delay"] is None and p0["cover_from"] == "2026-10-07" and p0["round_notices"] == []
    RS.record(con, "ss1", [1], "1", WED, THU, "transport", None, today=TODAY)
    con.commit()
    F._bump_gens(None)
    p = OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    assert p["delivery"] == "2026-10-07" and p["delivery_actual"] == "2026-10-08"
    assert p["cover_from"] == "2026-10-08" and p["cover_days"] == p0["cover_days"] - 1
    assert p["cutoff_at"] == p0["cutoff_at"] and p["open"] is True                # the regular cutoff
    crab0 = next(r for r in p0["rows"] + p0["local_rows"] if r["code"] == CRAB)
    crab = next(r for r in p["rows"] + p["local_rows"] if r["code"] == CRAB)
    assert crab["forecast"] < crab0["forecast"]                                   # Thu + Fri instead of Wed + Thu + Fri
    assert p["delay"]["to"] == "2026-10-08" and p["delay"]["reason"] == "รถขนส่งไม่พร้อม" and p["delay"]["label"] == "ล่าช้า"
    dl = next(x for x in p["deliveries"] if x["round"] == "1" and x["delivery"] == "2026-10-07")
    assert dl["delay"]["to_th"] == "พฤ. 8/10"
    n = p["round_notices"][0]
    assert n["title"] == "รอบ 1 · เลื่อนรอบส่งของ" and n["lines"][:3] == ["ปกติส่ง พ. 7/10", "เลื่อนเป็น พฤ. 8/10", "เหตุผล: รถขนส่งไม่พร้อม"]
    assert n["lines"][3].startswith("แจ้งเมื่อ ") and n["before_cutoff"] is True and n["ordered"] is False
    assert "ยอดแนะนำคิดถึงวันส่งใหม่แล้ว" in n["hint"]


# ── the order carries ล่าช้า, its status untouched ───────────────────────────
def test_an_order_of_the_moved_delivery_carries_delay(con):  # noqa: F811
    con.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, value, delivery_date) VALUES(1, '2026-10-05', '1', 'submitted', 0, '2026-10-07')")
    con.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, value, delivery_date) VALUES(1, '2026-10-08', '2', 'submitted', 0, '2026-10-10')")
    oid, other = [r[0] for r in con.execute("SELECT order_id FROM bc_order ORDER BY order_id")][-2:]
    assert OR.detail(con, oid)["delay"] is None
    RS.record(con, "ss1", [1], "1", WED, THU, "other", "ถนนปิด", today=TODAY)
    d = OR.detail(con, oid)
    assert d["status"] == "submitted" and d["delay"]["from"] == "2026-10-07" and d["delay"]["to"] == "2026-10-08"
    assert d["delay"]["reason"] == "ถนนปิด" and d["delay"]["by"] == "ss1"
    hist = {o["order_id"]: o for o in OR.history(con, 1)}
    assert hist[oid]["delay"]["to"] == "2026-10-08" and hist[other]["delay"] is None and "branch_id" not in hist[oid]
    q = {o["order_id"]: o for o in HB.queue(con, "all")}
    assert q[oid]["delay"]["to"] == "2026-10-08" and q[oid]["status"] == "submitted" and q[other]["delay"] is None
    assert [o["order_id"] for o in HB.queue(con, "submitted")] == [oid, other]           # the status filter is unchanged
    n = RS.notices(con, 1, TODAY, NOW)[0]
    assert n["ordered"] is True and "แจ้งประสานงานขาย" in n["hint"]


def test_notices_show_a_recent_cancellation(con):  # noqa: F811
    r = RS.record(con, "ss1", [1], "1", WED, THU, "transport", None, today=TODAY)[0]
    RS.cancel(con, "ss1", r["id"], "รถพร้อมแล้ว")
    ns = RS.notices(con, 1, TODAY, NOW)
    assert ns and ns[0]["status"] == "cancelled" and ns[0]["lines"][0] == "กลับมาส่งตามปกติ พ. 7/10"


# ── the push goes to the branch's own devices ────────────────────────────────
def test_the_push_reaches_that_branch_only(con, monkeypatch):  # noqa: F811
    from routers import push_api as P
    P.ensure(con)
    con.execute("CREATE TABLE IF NOT EXISTS app_user(username TEXT PRIMARY KEY, pw_hash TEXT, pw_salt TEXT, role TEXT,"
                " must_change INTEGER, branch TEXT, active INTEGER DEFAULT 1)")
    con.execute("INSERT OR IGNORE INTO branches(branch_id, name) VALUES(2, 'สาขาสอง')")
    name1 = con.execute("SELECT name FROM branches WHERE branch_id=1").fetchone()[0]
    for u, br in (("br_one", name1), ("br_two", "สาขาสอง"), ("ss_x", "")):
        con.execute("INSERT OR REPLACE INTO app_user(username, pw_hash, pw_salt, role, branch, active) VALUES(?,?,?,?,?,1)",
                    (u, "x", "y", "branch" if br else "salessupport", br))
        con.execute("INSERT INTO push_subscription(username, endpoint, p256dh, auth, created_at, branch) VALUES(?,?,?,?,?,?)",
                    (u, f"https://push.example/rs-{u}", "k", "a", "2026-10-05", br))
    con.commit()
    sent = []

    class _Ctx:
        def __enter__(self):
            return con

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(RS, "PUSH_SEND", lambda sub, data: sent.append((sub["endpoint"], data)) or 201)
    r = RS.record(con, "ss1", [1], "1", WED, THU, "transport", None, today=TODAY)[0]
    RS.send_push(lambda: _Ctx(), [(r, "new")])
    assert [e for e, _ in sent] == ["https://push.example/rs-br_one"]
    import json
    body = json.loads(sent[0][1])
    assert body["title"] == f"{name1} · เลื่อนรอบส่งของ"
    assert body["body"] == "รอบ 1 ปกติส่ง พ. 7/10 เลื่อนเป็น พฤ. 8/10 · เหตุผล: รถขนส่งไม่พร้อม"
    assert body["url"] == "/?go=supply&tab=branchstock&pane=order"
    sent.clear()
    RS.send_push(lambda: _Ctx(), [(RS.cancel(con, "ss1", r["id"], "รถพร้อม"), "cancel")])
    assert "กลับมาส่งตามปกติ" in json.loads(sent[0][1])["body"]


# ── who may write, and the table survives the swap ───────────────────────────
def test_the_rule_is_sales_support_and_management_only():
    pa = auth.path_allowed
    path = "/api/bom-center/round-reschedule"
    for role in ("salessupport", "management"):
        assert pa("POST", path, auth.ROLE_MODULES[role]) and pa("POST", path + "/3/cancel", auth.ROLE_MODULES[role]), role
    for role in ("branch", "analyst", "warehouse", "accounting", "franchise", "marketing", "innovation", "operation"):
        assert not pa("POST", path, auth.ROLE_MODULES[role]) and not pa("POST", path + "/3/edit", auth.ROLE_MODULES[role]), role
    assert not pa("GET", path, auth.ROLE_MODULES["branch"])
    assert pa("GET", "/api/bom-center/order/round-notices", auth.ROLE_MODULES["branch"])
    assert "bc" in core._PRESERVED_PREFIXES                     # every bc_* table survives the sync's DB swap


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "owner-token-rs")
    yield


def _login(user, pw=auth.DEFAULT_PASSWORD):
    import main
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return c


def test_http_sales_support_writes_branch_reads_its_notice(client, app_db, auth_on, monkeypatch):
    import main
    tok = {"X-Upload-Token": "owner-token-rs"}
    monkeypatch.setattr(RS, "PUSH_SEND", lambda sub, data: 201)
    mg, ss = _login("management1"), _login("salessupport1")
    r = mg.post("/api/auth/users", headers=tok, json={"username": "br_rs_a", "password": "Temp@12345", "role": "branch", "branch": "BranchA"})
    assert r.status_code in (200, 409), r.text
    br = _login("br_rs_a", "Temp@12345")
    body = {"branch_ids": [1, 2], "round": "1", "orig_date": "2098-03-05", "new_date": "2098-03-07",
            "reason_code": "transport", "reason_note": "รถเข้าศูนย์"}
    ids = []
    try:
        r = br.post("/api/bom-center/round-reschedule", json=body, headers=tok)
        assert r.status_code == 403 and r.headers.get("X-Denied") == "role"
        assert br.get("/api/bom-center/round-reschedule").status_code == 403
        r = ss.post("/api/bom-center/round-reschedule", json=body)
        assert r.status_code == 403 and r.headers.get("X-Denied") == "token"              # the owner token, like the calendar
        r = ss.post("/api/bom-center/round-reschedule", json={**body, "reason_code": ""}, headers=tok)
        assert r.status_code == 400 and "เลือกเหตุผล" in r.json()["detail"]
        r = ss.post("/api/bom-center/round-reschedule", json=body, headers=tok)
        assert r.status_code == 200, r.text
        ids = r.json()["ids"]
        assert r.json()["branches"] == 2
        lst = ss.get("/api/bom-center/round-reschedule?date_from=2098-03-01&date_to=2098-03-31").json()
        assert {x["id"] for x in lst["rows"]} == set(ids) and lst["can_write"] is True
        assert [x["code"] for x in lst["reasons"]] == ["transport", "supplier_late", "holiday", "other"]
        # the branch sees its own notice only (pinned to BranchA)
        n = br.get("/api/bom-center/order/round-notices").json()
        assert n["branch_id"] == 1 and [x["branch_id"] for x in n["notices"]] == [1]
        assert n["notices"][0]["lines"][1] == "เลื่อนเป็น ศ. 7/3"
        assert br.get("/api/bom-center/order/round-notices?branch_id=2").status_code == 403
        r = br.post(f"/api/bom-center/round-reschedule/{ids[0]}/cancel", json={"reason": "x"}, headers=tok)
        assert r.status_code == 403
        r = ss.post(f"/api/bom-center/round-reschedule/{ids[0]}/edit", headers=tok,
                    json={"new_date": "2098-03-06", "reason_code": "holiday"})
        assert r.status_code == 200, r.text
        r = ss.post(f"/api/bom-center/round-reschedule/{ids[0]}/cancel", headers=tok, json={"reason": ""})
        assert r.status_code == 400
        r = ss.post(f"/api/bom-center/round-reschedule/{ids[0]}/cancel", headers=tok, json={"reason": "รถพร้อมแล้ว"})
        assert r.status_code == 200
        k = sqlite3.connect(str(main.DB_PATH))
        acts = [a for (a,) in k.execute("SELECT action FROM bc_audit WHERE table_name='bc_round_reschedule' AND row_key LIKE '%2098-03-05'"
                                        " ORDER BY id")]
        k.close()
        assert acts == ["round_reschedule", "round_reschedule", "round_reschedule_edit", "round_reschedule_cancel"]
    finally:
        k = sqlite3.connect(str(main.DB_PATH))
        k.execute("DELETE FROM bc_round_reschedule WHERE orig_date='2098-03-05'")
        k.execute("DELETE FROM bc_audit WHERE table_name='bc_round_reschedule' AND row_key LIKE '%2098-03-05'")
        k.commit()
        k.close()
