"""BoM improvement proposal · batch 3 (owner approved 2026-10-12): reminders that reach people, and readiness for 30 branches.

The promises:
  * 2.1 the supply reminders go by Web Push (kind "supply") to the phones of the people who own each line: a branch account gets its
    own branch's morning line, the warehouse the queue, Sales Support the claims / slips / unkeyed orders, Operations the over-issue
    streaks, Accounting the close step; one send per day (usage_meta), a device that muted "supply" is skipped, and the chain-wide
    kinds (revenue figures) never reach a branch or a role that cannot read the chain's sales;
  * the reminders follow the ACTIVE BRANCH ACCOUNTS, not the four pilot branches;
  * 2.2 at 11:00 Thai time each branch whose cutoff is today and whose order is not yet submitted is told "ภายในวันนี้ 13:15 (อีก 2 ชม.)";
    the 13:15 message (the branches that missed it) goes to Sales Support only;
  * 3.6 cancelling a receipt needs a reason (400 without one); the whole receipt is kept in bc_receipt_cancel and the audit log before it is
    deleted; Sales Support reads the last 7 days;
  * 4.7 the all-branches adoption grid: one row per branch account, three columns from the existing definitions, traffic lights, worst
    first; a branch login is refused (403); the cards for the floor are one A4 page each.
"""
import json
import re
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth
import main
from bom_center import adoption as AD
from bom_center import pilot as PL
from bom_center import reminders as RM
from bom_center import variance as VA
from bom_center.clock import BKK
from routers import push_api as P
from test_bom_center_s11 import udon  # noqa: F401  (branch 1 = อุดรธานี)
from test_bom_center_s4b import con  # noqa: F401

REPO = Path(__file__).resolve().parent.parent
TOKEN = "owner-token-xyz"
PW = "Temp@12345"
MON = date(2026, 10, 5)


# ── the branch accounts ──────────────────────────────────────────────────────
def _acct(c, username, branch, role="branch", active=1):
    auth.init_auth(c)
    c.execute("INSERT INTO app_user(username, pw_hash, pw_salt, role, branch, active) VALUES(?, 'x', 'y', ?, ?, ?)",
              (username, role, branch, active))


def test_branch_accounts_are_the_active_branch_logins_with_a_known_branch(udon):  # noqa: F811
    c = udon
    assert PL.branch_accounts(c) == []                                   # no user table yet / no account
    _acct(c, "br_udon", "อุดรธานี")
    _acct(c, "br_udon2", "อุดรธานี")
    _acct(c, "br_b_off", "BranchB", active=0)                            # an inactive account does not count
    _acct(c, "br_nowhere", "ไม่มีสาขานี้")                                  # a branch the system does not know
    _acct(c, "wh1", None, role="warehouse")                              # not a branch login
    got = PL.branch_accounts(c)
    assert [(b["branch_id"], b["name"], b["usernames"]) for b in got] == [(1, "อุดรธานี", ["br_udon", "br_udon2"])]


def test_the_reminders_follow_the_accounts_not_the_pilot_names(udon):  # noqa: F811
    c = udon
    c.execute("UPDATE branches SET name='ลพบุรี' WHERE branch_id=2")      # a branch that is not one of the four pilot names
    _acct(c, "br_lop", "ลพบุรี")
    rec = RM.morning(c, today=MON)
    assert [b["name"] for b in rec["branches"]] == ["ลพบุรี"]            # the pilot branch อุดรธานี has no account: not listed
    assert rec["any_work"] is True and "<b>ลพบุรี</b>" in rec["text"]
    miss = RM.cutoff(c, today=MON, now=datetime(2026, 10, 5, 13, 20, tzinfo=BKK))
    assert [m["name"] for m in miss["missed"]] == ["ลพบุรี"]


def test_a_long_branch_list_is_cut_to_what_fits_a_telegram_message(udon, monkeypatch):  # noqa: F811
    c = udon
    _acct(c, "br_udon", "อุดรธานี")
    big = [{"branch_id": 1, "name": "อุดรธานี", "usernames": ["br_udon"]}] * 90
    monkeypatch.setattr(RM, "branch_accounts", lambda con: big)
    rec = RM.morning(c, today=MON)
    assert len(rec["text"]) <= RM.TELEGRAM_MAX + 200 and "และอีก" in rec["text"] and "ดูที่หน้างาน" in rec["text"]


# ── the payloads ──────────────────────────────────────────────────────────────
def test_the_branch_morning_push_says_what_is_open_and_opens_หน้างาน(udon):  # noqa: F811
    c = udon
    _acct(c, "br_udon", "อุดรธานี")
    b = PL.branch_accounts(c)[0]
    p = RM.branch_morning(c, b, today=MON, now=datetime(2026, 10, 5, 8, 0))
    assert p["title"] == "อุดรธานี · งานวัตถุดิบและใบสั่งซื้อวันนี้"
    assert "ส่งใบสั่งซื้อรอบส่งวันพุธ 7/10 ภายในวันนี้ 13:15" in p["body"]
    assert p["url"] == "/?go=supply&tab=supplyhome" and p["tag"] == RM.SUPPLY_TAG
    c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(1, '2026-10-05', '1', 'submitted')")
    assert RM.branch_morning(c, b, today=MON, now=datetime(2026, 10, 5, 8, 0)) is None      # sent already: nothing is open
    assert RM.branch_morning(c, b, today=date(2026, 10, 7), now=datetime(2026, 10, 7, 8, 0)) is None   # a day with no cutoff


def test_the_pre_cutoff_push_counts_the_time_left(udon):  # noqa: F811
    c = udon
    _acct(c, "br_udon", "อุดรธานี")
    b = PL.branch_accounts(c)[0]
    p = RM.branch_precutoff(c, b, today=MON, now=datetime(2026, 10, 5, 11, 1))
    assert p["body"] == "ส่งใบสั่งซื้อรอบส่งวันพุธ 7/10 ภายในวันนี้ 13:15 (อีก 2 ชม.)"
    assert p["url"] == "/?go=supply&tab=branchstock&pane=order"
    assert "อีก 1 ชม." in RM.branch_precutoff(c, b, today=MON, now=datetime(2026, 10, 5, 12, 0))["body"]
    assert "อีก 20 นาที" in RM.branch_precutoff(c, b, today=MON, now=datetime(2026, 10, 5, 12, 55))["body"]
    assert RM.branch_precutoff(c, b, today=MON, now=datetime(2026, 10, 5, 13, 16)) is None        # the cutoff has passed
    assert RM.branch_precutoff(c, b, today=date(2026, 10, 7), now=datetime(2026, 10, 7, 11, 1)) is None   # no cutoff that day
    c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(1, '2026-10-05', '1', 'submitted')")
    assert RM.branch_precutoff(c, b, today=MON, now=datetime(2026, 10, 5, 11, 1)) is None         # already in


def test_the_cutoff_push_names_the_branches_that_missed_it(udon):  # noqa: F811
    c = udon
    _acct(c, "br_udon", "อุดรธานี")
    rec = RM.cutoff(c, today=MON, now=datetime(2026, 10, 5, 13, 20, tzinfo=BKK))
    p = RM.cutoff_payload(rec)
    assert p["title"] == "ตัดรอบ 13:15 · ยังไม่ส่งใบสั่งซื้อ 1 สาขา" and "อุดรธานี" in p["body"] and "มีค่าปรับ" in p["body"]
    assert RM.cutoff_payload({"missed": []}) is None


def test_the_centre_lines_are_split_by_the_role_that_owns_them(udon):  # noqa: F811
    c = udon
    c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(1, '2026-10-05', '1', 'submitted')")
    got = RM.centre_morning(c, today=MON, now=datetime(2026, 10, 5, 8, 0))
    assert set(got) == {"warehouse"}
    assert got["warehouse"]["url"] == "/?go=supply&tab=orderhub&pane=queue" and "ใบสั่งซื้อค้างคิว 1 ใบ" in got["warehouse"]["lines"][0]
    pl = RM.centre_payload("warehouse", got["warehouse"])
    assert pl["title"] == "คลัง · งานวัตถุดิบและใบสั่งซื้อวันนี้" and pl["tag"] == RM.SUPPLY_TAG


# ── the sends, on the app's own database ─────────────────────────────────────
@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    monkeypatch.setenv("OSHINEI_VAPID_PRIVATE", "cHJpdmF0ZQ")
    monkeypatch.setenv("OSHINEI_VAPID_PUBLIC", "cHVibGlj")
    yield


def _login(user, pw=PW):
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return c


USERS = [("b3_br_a", "branch", "BranchA"), ("b3_br_b", "branch", "BranchB"), ("b3_wh", "warehouse", None),
         ("b3_ss", "salessupport", None), ("b3_acc", "accounting", None), ("b3_mg", "management", None)]


def _ep(user):
    return f"https://push.example/{user}"


@pytest.fixture
def world(client, auth_on):
    """Branch accounts for BranchA and BranchB, one login of each centre role, one device each; wiped afterwards."""
    client.get("/api/bom-center/hub/queue")                                 # creates the bc_* tables
    with main.get_db() as db:
        auth.init_auth(db)
        P.ensure(db)
        for u, role, br in USERS:
            auth.create_user(db, u, PW, role, br)
            db.execute("INSERT INTO push_subscription(username, endpoint, p256dh, auth, created_at, branch) VALUES(?,?,?,?,?,?)",
                       (u, _ep(u), "p", "a", "2026-10-05 07:00", br or ""))
        db.commit()
    yield
    with main.get_db() as db:
        db.execute("DELETE FROM push_subscription WHERE username LIKE 'b3_%'")
        db.execute("DELETE FROM app_user WHERE username LIKE 'b3_%'")
        db.execute("DELETE FROM usage_meta WHERE key LIKE 'push_supply%'")
        for t in ("bc_claim", "bc_order", "bc_receipt_cancel"):
            db.execute(f"DELETE FROM {t}")
        db.execute("DELETE FROM bc_receipt_line")
        db.execute("DELETE FROM bc_receipt")
        db.commit()


def _clear_meta():
    with main.get_db() as db:
        db.execute("DELETE FROM usage_meta WHERE key LIKE 'push_supply%'")
        db.commit()


def _sender(log):
    def send(sub, data):
        log.append((sub["endpoint"].rsplit("/", 1)[1], json.loads(data)))
        return 201
    return send


def _sql(sql, args=()):
    with main.get_db() as db:
        cur = db.execute(sql, args)
        db.commit()
        return cur


def test_the_morning_push_goes_to_the_owner_of_each_line_once_a_day(world):
    _sql("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(2, '2026-10-05', '1', 'submitted')")   # BranchB sent: in the queue
    _sql("INSERT INTO bc_claim(branch_id, order_id, status, created_by, created_at) VALUES(2, 1, 'submitted', 'x', '2026-10-04')")
    log = []
    send = _sender(log)
    assert P.maybe_send_supply(now=datetime(2026, 10, 5, 7, 59), send=send) is None                  # not yet 08:00
    rec = P.maybe_send_supply(now=datetime(2026, 10, 5, 8, 0), send=send)
    got = dict(log)
    assert set(got) == {"b3_br_a", "b3_wh", "b3_ss"}, rec          # BranchA still has to send; the queue; the claim - nobody else
    assert got["b3_br_a"]["title"] == "BranchA · งานวัตถุดิบและใบสั่งซื้อวันนี้" and got["b3_br_a"]["url"] == "/?go=supply&tab=supplyhome"
    assert "ภายในวันนี้ 13:15" in got["b3_br_a"]["body"]
    assert got["b3_wh"]["url"] == "/?go=supply&tab=orderhub&pane=queue" and "ค้างคิว 1 ใบ" in got["b3_wh"]["body"]
    assert got["b3_ss"]["url"] == "/?go=supply&tab=orderhub&pane=claims" and "เคลมสินค้ารอพิจารณา 1 เรื่อง" in got["b3_ss"]["body"]
    assert rec["day"] == "2026-10-05" and rec["sent"] == 3 and rec["targets"] == 3
    # a management device is not a target of any supply line
    assert "b3_mg" not in got and "b3_acc" not in got
    # once a day: later ticks and a second minute do nothing
    n = len(log)
    assert P.maybe_send_supply(now=datetime(2026, 10, 5, 8, 1), send=send) is None
    assert P.maybe_send_supply(now=datetime(2026, 10, 5, 15, 0), send=send) is None and len(log) == n
    # the next day it runs again
    assert P.maybe_send_supply(now=datetime(2026, 10, 6, 8, 0), send=send) is not None


def test_a_device_that_muted_supply_is_skipped(world):
    _sql("UPDATE push_subscription SET off='supply' WHERE username='b3_br_a'")
    log = []
    rec = P.maybe_send_supply(now=datetime(2026, 10, 5, 8, 0), send=_sender(log))
    assert "b3_br_a" not in dict(log) and rec["muted"] >= 1


def test_nothing_is_sent_without_keys_and_the_day_is_still_claimed(world, monkeypatch):
    monkeypatch.delenv("OSHINEI_VAPID_PRIVATE", raising=False)
    monkeypatch.delenv("OSHINEI_VAPID_PUBLIC", raising=False)
    log = []
    rec = P.maybe_send_supply(now=datetime(2026, 10, 5, 8, 0), send=_sender(log))
    assert log == [] and rec["configured"] is False
    assert P.maybe_send_supply(now=datetime(2026, 10, 5, 8, 5), send=_sender(log)) is None


def test_the_pre_cutoff_push_at_eleven(world):
    _sql("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(2, '2026-10-05', '1', 'submitted')")    # BranchB is in
    log = []
    send = _sender(log)
    assert P.maybe_send_precutoff(now=datetime(2026, 10, 5, 10, 59), send=send) is None and log == []              # 10:59: no
    rec = P.maybe_send_precutoff(now=datetime(2026, 10, 5, 11, 1), send=send)                                      # 11:01: yes
    assert [u for u, _ in log] == ["b3_br_a"], rec                                                                  # only the branch that has not sent
    p = log[0][1]
    assert p["body"] == "ส่งใบสั่งซื้อรอบส่งวันพุธ 7/10 ภายในวันนี้ 13:15 (อีก 2 ชม.)" and p["url"] == "/?go=supply&tab=branchstock&pane=order"
    assert P.maybe_send_precutoff(now=datetime(2026, 10, 5, 11, 2), send=send) is None and len(log) == 1           # a second run: no
    assert P.maybe_send_precutoff(now=datetime(2026, 10, 5, 13, 20), send=send) is None                            # after the cutoff: no


def test_the_pre_cutoff_push_skips_a_branch_whose_order_is_in(world):
    _sql("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(1, '2026-10-05', '1', 'submitted')")
    _sql("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(2, '2026-10-05', '1', 'submitted')")
    log = []
    rec = P.maybe_send_precutoff(now=datetime(2026, 10, 5, 11, 1), send=_sender(log))
    assert log == [] and rec["groups"] == []


def test_the_thirteen_fifteen_push_is_for_sales_support_only(world):
    log = []
    send = _sender(log)
    assert P.maybe_send_cutoff(now=datetime(2026, 10, 5, 13, 14), send=send) is None
    rec = P.maybe_send_cutoff(now=datetime(2026, 10, 5, 13, 16), send=send)
    assert [u for u, _ in log] == ["b3_ss"], rec                     # not the branches, not the warehouse
    body = log[0][1]["body"]
    assert "BranchA" in body and "BranchB" in body and "มีค่าปรับ" in body and log[0][1]["url"] == "/?go=supply&tab=supplyhome"
    assert P.maybe_send_cutoff(now=datetime(2026, 10, 5, 13, 30), send=send) is None and len(log) == 1
    _clear_meta()
    assert P.maybe_send_cutoff(now=datetime(2026, 10, 5, 20, 5), send=send) is None              # a restart at night does not announce it


def test_the_scheduler_runs_every_supply_step_even_when_one_fails(monkeypatch):
    src = (REPO / "backend" / "main.py").read_text(encoding="utf-8")
    for name in ("maybe_send_supply", "maybe_send_precutoff", "maybe_send_cutoff"):
        assert f"_push_api.{name}" in src
    assert "for step in (" in src and "one failing step never stops the next" in src


# ── who is offered which kind, and who receives the chain's figures ──────────────
def test_the_chain_wide_kinds_never_reach_a_branch_or_the_warehouse(world):
    log = []
    with main.get_db() as db:
        r = P.send_to_all(db, {"title": "t", "body": "รายได้", "url": "/"}, send=_sender(log), chain_only=True, kind="morning")
        db.commit()
    got = {u for u, _ in log}
    assert "b3_mg" in got and "b3_ss" in got                          # roles that read the chain's sales
    assert not got & {"b3_br_a", "b3_br_b", "b3_wh"}                  # a branch login and the warehouse do not
    assert r["targets"] == len(log)


def test_the_kinds_a_login_is_offered(world):
    def kinds(user):
        c = _login(user)
        assert c.post("/api/push/subscribe", json={"endpoint": f"https://push.example/me-{user}", "keys": {"p256dh": "p", "auth": "a"}}).status_code == 200
        j = c.post("/api/push/prefs", json={"endpoint": f"https://push.example/me-{user}"}).json()
        return [k["id"] for k in j["kinds"]]
    chain = ["morning", "weekly", "drop", "stale", "cash", "disc"]
    assert kinds("b3_br_a") == ["supply"]
    assert kinds("b3_wh") == ["supply"]
    assert kinds("b3_acc") == chain + ["supply"]                     # accounting reads the chain's sales, so it keeps those kinds too
    assert kinds("b3_ss") == chain + ["supply"]
    assert kinds("b3_mg") == chain                                    # the manager receives no supply line
    _sql("DELETE FROM push_subscription WHERE endpoint LIKE 'https://push.example/me-%'")


def test_a_branch_device_sees_only_itself_in_the_push_status(world):
    _sql("INSERT OR REPLACE INTO usage_meta(key, value) VALUES('push_morning', ?)", (json.dumps({"payload": {"title": "เมื่อวาน 2.05M"}}, ensure_ascii=False),))
    try:
        j = _login("b3_br_a").get("/api/push/status").json()
        assert j["last"] is None and j["devices"] == j["my_devices"] == 1            # no revenue figure, no chain-wide device count
        assert _login("b3_mg").get("/api/push/status").json()["last"]["payload"]["title"] == "เมื่อวาน 2.05M"
        assert _login("b3_wh").get("/api/push/vapid-public").status_code == 200       # the warehouse may reach the push endpoints (no sales.overview)
    finally:
        _sql("DELETE FROM usage_meta WHERE key='push_morning'")


def test_the_franchise_owner_reaches_only_the_push_device_endpoints():
    # PR-F2: the franchise owner turns its own morning summary on and off (kind "franchise", test_franchise_f2.py); any other
    # /api/push path stays closed to the role
    fr = auth.ROLE_MODULES["franchise"]
    assert auth.path_allowed("POST", "/api/push/subscribe", fr) and auth.path_allowed("GET", "/api/push/vapid-public", fr)
    assert not auth.path_allowed("POST", "/api/push/other", fr) and not auth.path_allowed("GET", "/api/push", fr)
    for role in ("branch", "warehouse", "salessupport", "operation", "accounting", "management"):
        mods = auth.ROLE_MODULES[role]
        assert auth.path_allowed("POST", "/api/push/subscribe", mods) and auth.path_allowed("GET", "/api/push/vapid-public", mods), role


def test_a_branch_login_cannot_read_the_grid_or_the_cancelled_receipts(world):
    br = _login("b3_br_a")
    assert br.get("/api/bom-center/hub/adoption").status_code == 403
    assert br.get("/api/bom-center/hub/cancelled-receipts").status_code == 403
    ss = _login("b3_ss")
    assert ss.get("/api/bom-center/hub/adoption").status_code == 200
    assert ss.get("/api/bom-center/hub/cancelled-receipts").status_code == 200


# ── 3.6 cancelling a receipt ──────────────────────────────────────────────────────
def _receipt(c, day="2026-10-07"):
    out = VA.add_receipt(c, 1, day, [{"code": "13-01-009", "qty": 3, "unit": "แพ็ค 1 กก."}], "udon", ref="ORDER-5", note="x")
    c.commit()
    return out["receipt_id"]


def test_a_receipt_cannot_be_cancelled_without_a_reason(con):  # noqa: F811
    rid = _receipt(con)
    for bad in (None, "", "  ", "ก"):
        with pytest.raises(ValueError, match="เหตุผล"):
            VA.delete_receipt(con, 1, rid, "udon", bad)
    assert con.execute("SELECT COUNT(*) FROM bc_receipt WHERE receipt_id=?", (rid,)).fetchone()[0] == 1       # nothing was removed
    assert con.execute("SELECT COUNT(*) FROM bc_receipt_cancel").fetchone()[0] == 0


def test_a_cancelled_receipt_is_kept_whole_with_its_reason(con):  # noqa: F811
    rid = _receipt(con)
    assert VA.delete_receipt(con, 1, rid, "udon", "บันทึกซ้ำ  วันเดียวกัน")
    assert con.execute("SELECT COUNT(*) FROM bc_receipt WHERE receipt_id=?", (rid,)).fetchone()[0] == 0           # the stock figure follows
    assert con.execute("SELECT COUNT(*) FROM bc_receipt_line WHERE receipt_id=?", (rid,)).fetchone()[0] == 0
    row = con.execute("SELECT receipt_id, branch_id, receipt_date, ref, line_count, cancel_reason, cancelled_by, cancelled_at, snapshot"
                      " FROM bc_receipt_cancel").fetchone()
    assert row[:6] == (rid, 1, "2026-10-07", "ORDER-5", 1, "บันทึกซ้ำ วันเดียวกัน") and row[6] == "udon" and row[7]
    snap = json.loads(row[8])
    assert snap["receipt_date"] == "2026-10-07" and snap["note"] == "x" and snap["entered_by"] == "udon"
    assert snap["lines"][0]["ingredient_code"] == "13-01-009" and snap["lines"][0]["qty"] == 3 and snap["lines"][0]["qty_base"] == 3000
    a = con.execute("SELECT who, action, old_value, new_value FROM bc_audit WHERE action='receipt_cancel'").fetchone()
    assert a[0] == "udon" and a[3] == "บันทึกซ้ำ วันเดียวกัน" and json.loads(a[2])["lines"][0]["ingredient_code"] == "13-01-009"


def test_the_last_seven_days_of_cancelled_receipts(con):  # noqa: F811
    rid = _receipt(con)
    VA.delete_receipt(con, 1, rid, "udon", "ผิดสาขา")
    now = con.execute("SELECT cancelled_at FROM bc_receipt_cancel").fetchone()[0]
    today = date.fromisoformat(now[:10])
    rows = VA.cancelled_receipts(con, 7, today=today)
    assert len(rows) == 1 and rows[0]["reason"] == "ผิดสาขา" and rows[0]["branch"] == "BranchA" and rows[0]["lines"] == 1
    assert rows[0]["ref_th"].startswith("รับตามใบสั่งซื้อ") or rows[0]["ref_th"] is None
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", rows[0]["cancelled_at"])                              # the branch's clock
    assert VA.cancelled_receipts(con, 7, today=date.fromisoformat("2026-12-31")) == []                           # older than 7 days: gone from the list
    con.execute("UPDATE bc_receipt_cancel SET cancelled_at='2026-01-01T00:00:00'")
    assert VA.cancelled_receipts(con, 7, today=today) == []


def test_the_api_refuses_a_cancel_without_a_reason_and_keeps_the_receipt(world):
    with main.get_db() as db:
        from bom_center.schema import ensure
        ensure(db)
        cur = db.execute("INSERT INTO bc_receipt(branch_id, receipt_date, source, ref, entered_by, created_at) VALUES(1, '2026-10-07', 'branch', 'x', 'b3_br_a', '2026-10-07T01:00:00')")
        rid = cur.lastrowid
        db.execute("INSERT INTO bc_receipt_line(receipt_id, ingredient_code, qty, unit, qty_base) VALUES(?, 'X-1', 2, 'กก.', 2000)", (rid,))
        db.commit()
    br = _login("b3_br_a")
    assert br.delete(f"/api/bom-center/stock/receipt/{rid}", headers={"X-Upload-Token": TOKEN}).status_code == 400
    r = br.request("DELETE", f"/api/bom-center/stock/receipt/{rid}", json={"reason": ""})
    assert r.status_code == 400 and "เหตุผล" in r.text
    assert _sql("SELECT COUNT(*) FROM bc_receipt WHERE receipt_id=?", (rid,)).fetchone()[0] == 1
    ok = br.request("DELETE", f"/api/bom-center/stock/receipt/{rid}", json={"reason": "บันทึกซ้ำ"})
    assert ok.status_code == 200 and ok.json()["deleted"] is True
    ss = _login("b3_ss")
    rows = ss.get("/api/bom-center/hub/cancelled-receipts?days=7").json()["rows"]
    assert len(rows) == 1 and rows[0]["reason"] == "บันทึกซ้ำ" and rows[0]["branch"] == "BranchA" and rows[0]["receipt_date"] == "2026-10-07"


# ── 4.7 the adoption grid ────────────────────────────────────────────────────────
def _slip(c, day, late=0, status="confirmed", at=None):
    c.execute("INSERT INTO bc_issue_slip(branch_id, slip_date, status, created_by, created_at, updated_at, confirmed_by, confirmed_at, late)"
              " VALUES(1, ?, ?, 'u', ?, ?, 'u', ?, ?)", (day, status, day, day, at or f"{day}T05:00:00+00:00", late))


@pytest.fixture
def grid_db(udon):  # noqa: F811
    c = udon
    c.execute("UPDATE branches SET name='ลพบุรี' WHERE branch_id=2")
    _acct(c, "br_udon", "อุดรธานี")
    _acct(c, "br_lop", "ลพบุรี")                                  # a branch with an account and no activity at all
    # branch 1 (sales every day 1-5 Oct): slips 1-3 on time, 4 late, 5 not made
    for d in ("2026-10-01", "2026-10-02", "2026-10-03"):
        _slip(c, d)
    _slip(c, "2026-10-04", late=1, at="2026-10-05T05:00:00+00:00")
    # orders: round 1 of Mon 5 Oct sent in time; round 2 of Thu 1 Oct sent late; the salmon of Sat 3 Oct never made
    c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, submitted_at, late, delivery_date) VALUES(1,'2026-10-05','1','submitted','2026-10-05T04:00:00+00:00',0,'2026-10-07')")
    c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, submitted_at, late, delivery_date, processed_at) VALUES(1,'2026-10-01','2','processed','2026-10-01T08:00:00+00:00',1,'2026-10-03','2026-10-02T04:00:00+00:00')")
    c.commit()
    return c


def test_the_grid_reads_the_existing_definitions(grid_db):
    g = AD.grid(grid_db, today=date(2026, 10, 6))
    assert (g["from"], g["to"], g["days"], g["total"]) == ("2026-09-30", "2026-10-06", 7, 2)
    rows = {r["branch"]: r for r in g["rows"]}
    u = rows["อุดรธานี"]
    assert u["issues"]["on_time"] == 3 and u["issues"]["days"] == 5 and u["issues"]["pct"] == 60.0 and u["issues"]["light"] == "yellow"
    assert u["orders"]["on_time"] == 1 and u["orders"]["due"] == 3 and u["orders"]["light"] == "red"      # r1 in time; r2 late; salmon missing
    assert u["receive"] == {"received": 0, "delivered": 1, "pct": 0.0, "light": "red"}                        # the 3 Oct delivery is not recorded
    assert u["status"] == "red" and u["active"] is True                                                       # the worst column decides
    lop = rows["ลพบุรี"]                                        # PR-U1: never started - neutral, not red
    assert lop["status"] == "not_started" and lop["status_th"] == "ยังไม่เริ่มใช้ระบบ" and lop["active"] is False
    assert lop["issues"]["pct"] is None and lop["last_active"] is None


def test_the_grid_is_sorted_worst_first_and_counts_the_lights(grid_db):
    # make อุดรธานี green: every column at 100 %
    c = grid_db
    c.execute("DELETE FROM bc_issue_slip")
    for d in ("2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"):
        _slip(c, d)
    c.execute("UPDATE bc_order SET late=0")
    c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, submitted_at, late) VALUES(1,'2026-10-03','salmon','received','2026-10-03T04:00:00+00:00',0)")
    c.execute("UPDATE bc_order SET status='received' WHERE round='2'")
    c.commit()
    g = AD.grid(c, today=date(2026, 10, 6))
    assert [r["branch"] for r in g["rows"]] == ["อุดรธานี", "ลพบุรี"]                 # PR-U1: a branch that has not started comes last
    u = g["rows"][0]
    assert (u["issues"]["pct"], u["orders"]["pct"], u["receive"]["pct"]) == (100.0, 100.0, 100.0) and u["status"] == "green"
    assert g["counts"] == {"green": 1, "yellow": 0, "red": 0, "not_started": 1}
    assert AD.summary_line(g) == "ทั้งหมด 2 สาขา · เขียว 1 · เหลือง 0 · แดง 0 · ยังไม่เริ่มใช้ระบบ 1"


def test_the_lights():
    assert [AD.light(x) for x in (100, 90, 89.9, 60, 59.9, 0, None)] == ["green", "green", "yellow", "yellow", "red", "red", None]


def test_the_grid_endpoint_shape(world):
    ss = _login("b3_ss")
    j = ss.get("/api/bom-center/hub/adoption").json()
    assert {"rows", "counts", "total", "from", "to", "line", "thresholds"} <= set(j)
    assert {r["branch"] for r in j["rows"]} == {"BranchA", "BranchB"} and j["line"].startswith("ทั้งหมด 2 สาขา")
    assert all({"issues", "orders", "receive", "last_active", "status_th"} <= set(r) for r in j["rows"])


# ── the screens ──────────────────────────────────────────────────────────────────
def _src(rel):
    return (REPO / rel).read_text(encoding="utf-8")


def test_the_branch_screen_asks_for_a_reason_and_sends_it():
    js = _src("js/14-branch.js")
    fn = js[js.index("async function brnRecvDelete"):js.index("// ── receiving an order (S8")]
    assert "uiPrompt(" in fn and "required: true" in fn and "JSON.stringify({ reason })" in fn and "method: 'DELETE'" in fn
    assert "ยกเลิกใบรับสินค้า" in fn


def test_the_adoption_pane_and_the_หน้างาน_summary_exist():
    core, oh, shm = _src("js/01-core.js"), _src("js/15-orderhub.js"), _src("js/16-supplyhome.js")
    tab, home = _src("tabs/54-orderhub.html"), _src("tabs/55-supplyhome.html")
    assert "pane: 'adoption', label: 'การใช้งานทุกสาขา', mods: _SUPPLY_CENTRE" in core
    assert "สาขานำร่อง 4 สาขา'" not in core.split("const SUPPLY_LEGACY_LABEL")[0]          # the หน้างาน entry is gone
    assert 'id="ohb-pane-adoption"' in tab and "\n  adoption: {" in oh and 'id="ohbCancelledReceipts"' in tab      # PR-D6: its help is behind the "?" of the page header
    assert "'adoption'" in oh and "ohbLoadAdoption" in oh and "ohbAdOpen" in oh and "ohbLoadCancelledReceipts" in oh
    assert "ใบรับสินค้าที่สาขายกเลิก 7 วันล่าสุด" in oh
    assert 'id="shmAdoption"' in home and 'id="shmPush"' in home and "รับการแจ้งเตือนบนมือถือ" in home
    assert "shmAdoptionLoad" in shm and "shmPushInit" in shm
    assert "mods: _SUPPLY_CENTRE" in core and re.search(r"pane: 'adoption'.*roles: \['salessupport', 'operation', 'analyst', 'management'\]", core)


def test_a_tap_on_a_supply_push_opens_the_page():
    core, mob, tabs = _src("js/01-core.js"), _src("js/09c-mobile.js"), _src("js/03-tabs-main.js")
    assert "function supplyDeepLink(" in core and "go') !== 'supply'" in core and "supplyGo({ tab, pane })" in core
    assert "_supplyPendingLink = location.search" in mob and "supplyDeepLink(d.url)" in mob
    assert "supplyDeepLink(l)" in tabs and "force-desktop" in tabs.split("async function startApp")[1].split("try {")[0]
    # every address the server builds is one the client understands
    for m in re.finditer(r"supply_url\(\"(\w+)\"(?:, \"(\w+)\")?\)", _src("backend/bom_center/reminders.py")):
        tab, pane = m.group(1), m.group(2)
        assert tab in ("supplyhome", "orderhub", "branchstock", "bomcenter")
        if pane:
            assert re.search(r"tab: '%s', pane: '%s'" % (tab, pane), core), (tab, pane)


def test_the_push_card_is_the_one_the_owner_uses():
    mob = _src("js/09c-mobile.js")
    for fn in ("msPushRender", "msPushKinds", "msPushKindsSave", "msPushEnable", "msPushDisable", "msPushTest"):
        assert re.search(r"async function %s\([^)]*\bp\b" % fn, mob), fn
    assert "async function msPushVapid" in mob
    html = _src("tabs/55-supplyhome.html")
    for fn in ("msPushEnable('shmPush')", "msPushDisable('shmPush')", "msPushTest('shmPush')"):
        assert fn in html
    assert "msPushEnable()" in _src("index.html")                                              # the owner's own card is unchanged


# ── the printed task cards ───────────────────────────────────────────────────────
def test_the_task_cards_exist_one_page_each():
    cards = REPO / "docs" / "pilot" / "cards"
    names = ["issue", "order", "receive", "count"]
    for n in names:
        h = cards / f"card_{n}.html"
        assert h.exists(), h
        txt = h.read_text(encoding="utf-8")
        assert "@page" in txt and "A4" in txt
        assert not re.search(r"กรุณา|ส่วนกลาง|ระบบจะ", txt)
    pdfs = [cards / f"card_{n}.pdf" for n in names]
    assert all(p.exists() and p.stat().st_size > 5000 for p in pdfs)
    for p in pdfs:                                                   # one A4 page each
        raw = p.read_bytes()
        assert len(re.findall(rb"/Type\s*/Page[^s]", raw)) == 1, p
