# -*- coding: utf-8 -*-
"""หน้างาน - the state of a task: ยังไม่เสร็จ · รอตรวจ · เสร็จแล้ว (PR-TF, owner 2026-10-21).

The promises:
  * `status` is untouched; every item and every close step also carries `state` (todo | review | done) and `state_th`;
  * done / na -> done; blocked -> review; every other open item -> todo;
  * the state depends on WHO is looking: the same draft in review is รอตรวจ for the kitchen that sent it and
    ยังไม่เสร็จ (its move) for the analyst who has to review it - and likewise for price files, claims, orders, releases;
  * REVIEW_KEYS names exactly the keys todo.py builds (no stale key), and with no viewer nobody waits;
  * over HTTP the live role of the signed-in account decides.
"""
import re
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth
from bom_center import price_submissions as PS
from bom_center import todo as TD
from bom_center import close_settings as CS
from test_bom_center_s11 import udon as _udon  # noqa: F401  (branch 1 = อุดรธานี, the centre's September report)
from test_bom_center_s4b import con  # noqa: F401  (schema ensured on a scratch DB)


@pytest.fixture
def udon(_udon):
    """PR-S1: the states of the recipe items (draft, review, acknowledge) are read in full mode; stock mode, the default, hides them."""
    CS.set_mode(_udon, "test", "full")
    return _udon

TODAY = date(2026, 10, 7)
M = "2026-09"
CSV = ("รหัส,ชื่อสินค้า,จำนวน,หน่วย,มูลค่ารวม,เดือน\n"
       "ING-PSUB,วัตถุดิบทดสอบไฟล์ราคา,1000,กรัม,5000,2098-03\n").encode("utf-8")


def _st(c, role, branch=None, **kw):
    """{base key: state} of the whole list as `role` sees it (a branch login passes its branch name)."""
    d = TD.build(c, M, None, branch, today=TODAY, viewer_role=role, **kw)
    return {i["key"].split(":")[0]: i["state"] for i in d["items"]}, d


def _draft(c, status="draft"):
    c.execute("INSERT INTO bc_release(label, kind, effective_month, status, price_month, created_by, submitted_by, submitted_at)"
              " VALUES('T 2026-11', 'monthly', '2026-11', ?, '2026-09', 't', 'k1', '2026-10-06T10:00:00+07:00')", (status,))
    return c.execute("SELECT MAX(release_id) FROM bc_release").fetchone()[0]


# ── the plain rules ──────────────────────────────────────────────────────────
def test_every_entry_has_a_state_and_status_is_untouched(udon):
    d = TD.build(udon, M, None, None, today=TODAY, viewer_role="analyst")
    for e in d["items"] + d["close"]["steps"]:
        assert e["status"] in ("done", "todo", "blocked", "na"), e["key"]
        assert e["state"] in ("todo", "review", "done") and e["state_th"] == TD.STATE_TH[e["state"]], e["key"]
    assert TD.STATE_TH == {"todo": "ยังไม่เสร็จ", "review": "รอตรวจ", "done": "เสร็จแล้ว"}


def test_done_and_na_are_done_blocked_is_review_open_is_todo():
    f = lambda status, key="x", **kw: TD.item_state({"key": key, "status": status, **kw}, "analyst")  # noqa: E731
    assert f("done") == "done" and f("na") == "done"
    assert f("blocked") == "review"
    assert f("todo") == "todo"
    assert TD.item_state({"key": "x", "status": "todo"}) == "todo"                 # no viewer


def test_na_is_done_and_blocked_is_review_in_a_real_list(udon):
    c = udon
    s, d = _st(c, "analyst")
    assert next(i for i in d["items"] if i["key"] == "acknowledge")["status"] == "na" and s["acknowledge"] == "done"
    steps = {x["key"]: x for x in d["close"]["steps"]}
    assert steps["code_links"]["status"] in ("na", "done", "todo")
    for x in d["close"]["steps"]:
        assert x["state"] == {"done": "done", "na": "done", "blocked": "review", "todo": "todo"}[x["status"]], x["key"]
    # a release that waits for its checks is blocked -> รอตรวจ
    c.execute("INSERT INTO bc_ingredient_price(ingredient_code, month, price_per_base, source) VALUES('13-01-009', ?, 0.2, 'purchase_avg')", (M,))
    _draft(c)
    s, d = _st(c, "analyst")
    rel = next(i for i in d["items"] if i["key"] == "release")
    assert rel["status"] == "blocked" and rel["state"] == "review" and rel["state_th"] == "รอตรวจ"


def test_without_a_viewer_nobody_waits(udon):
    c = udon
    _draft(c, "review")
    s, _ = _st(c, None)
    assert s["review_pending"] == "todo" and s["draft"] == "done"


# ── viewer dependence ────────────────────────────────────────────────────────
def test_a_draft_sent_for_review_is_review_for_the_kitchen_and_todo_for_the_analyst(udon):
    c = udon
    _draft(c, "review")
    kitchen, d = _st(c, "innovation")
    analyst, _ = _st(c, "analyst")
    mgmt, _ = _st(c, "management")
    dr = next(i for i in d["items"] if i["key"] == "draft")
    assert dr["status"] == "done" and dr.get("awaiting") is True                  # the kitchen did its part ...
    assert kitchen["draft"] == "review" and kitchen["review_pending"] == "review"  # ... and waits for the analyst
    assert analyst["review_pending"] == "todo" and analyst["draft"] == "done"      # the analyst's move
    assert mgmt["review_pending"] == "todo" and mgmt["draft"] == "done"
    # a draft the kitchen has not sent yet is the kitchen's own move
    c.execute("UPDATE bc_release SET status='draft'")
    kitchen, _ = _st(c, "innovation")
    assert kitchen["draft"] == "todo" and "review_pending" not in kitchen


def test_price_files_sent_and_pending(udon):
    c = udon
    sid = PS.create(c, M, "a.csv", CSV, None, "salessupport1")["sub_id"]
    ss, d = _st(c, "salessupport")
    an, _ = _st(c, "analyst")
    assert ss["price_submit"] == "review" and ss["price_review"] == "review"
    assert an["price_review"] == "todo" and an["price_submit"] == "done"
    PS.decide(c, sid, "imported", "analyst1")                                     # nothing waits any more
    ss, _ = _st(c, "salessupport")
    assert ss["price_submit"] == "done" and "price_review" not in ss


def test_released_version_waits_for_management(udon):
    c = udon
    c.execute("INSERT INTO bc_ingredient_price(ingredient_code, month, price_per_base, source) VALUES('13-01-009', ?, 0.2, 'purchase_avg')", (M,))
    rid = _draft(c)
    c.execute("UPDATE bc_release SET status='released', released_by='a' WHERE release_id=?", (rid,))
    an, _ = _st(c, "analyst")
    mg, _ = _st(c, "management")
    assert an["acknowledge"] == "review" and mg["acknowledge"] == "todo"
    c.execute("UPDATE bc_release SET acknowledged_by='m', acknowledged_at='x' WHERE release_id=?", (rid,))
    an, _ = _st(c, "analyst")
    assert an["acknowledge"] == "done"


def test_branch_claims_and_sent_orders_wait_for_the_centre(udon):
    c = udon
    cyc = next(i for i in TD.build(c, M, None, "อุดรธานี", today=TODAY)["items"] if i["key"].startswith("order_due_2"))["cutoff_date"]
    br, d = _st(c, "branch", "อุดรธานี")
    assert br["order_due_2"] == "todo"                                            # not sent: the branch's move
    c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(1, ?, '2', 'submitted')", (cyc,))
    br, _ = _st(c, "branch", "อุดรธานี")
    assert br["order_due_2"] == "review"                                          # sent, the warehouse has not processed it
    wh, _ = _st(c, "warehouse")
    assert wh["queue_pending"] == "todo"                                          # the warehouse's move
    c.execute("UPDATE bc_order SET status='processed'")
    br, _ = _st(c, "branch", "อุดรธานี")
    assert br["order_due_2"] == "done"
    # a claim the branch raised, waiting for Sales Support
    oid = c.execute("SELECT MAX(order_id) FROM bc_order").fetchone()[0]
    c.execute("INSERT INTO bc_claim(branch_id, order_id, status) VALUES(1, ?, 'submitted')", (oid,))
    br, d = _st(c, "branch", "อุดรธานี")
    co = next(i for i in d["items"] if i["key"].startswith("claims_open"))
    assert co["status"] == "na" and br["claims_open"] == "review"                 # na would read "done"; the branch is waiting
    ss, _ = _st(c, "salessupport")
    assert ss["claims_pending"] == "todo" and ss["claims_open"] == "done"
    c.execute("UPDATE bc_claim SET status='approved'")
    br, _ = _st(c, "branch", "อุดรธานี")
    assert "claims_open" not in br


# ── the classification table ─────────────────────────────────────────────────
def test_review_keys_are_real_keys_and_real_roles():
    src = (Path(TD.__file__)).read_text(encoding="utf-8")
    built = set(re.findall(r'_e\(f?"([a-z_]+?)(?:_\{[^"]*)?"', src)) | {"order_due"}
    for k, (roles, kind) in TD.REVIEW_KEYS.items():
        assert k in built, f"REVIEW_KEYS names {k}, which todo.py does not build"
        assert roles and set(roles) <= set(auth.ROLES) and kind in ("own", "queue"), (k, roles, kind)
    # every item that can wait is documented in the module's own table
    for k in TD.REVIEW_KEYS:
        assert re.search(rf"^#\s+{k}\s", src, re.M), f"{k} is missing from the REVIEW_KEYS comment table"


def test_base_key_drops_the_branch_and_the_round():
    assert TD._base_key("order_due_2:5") == "order_due" and TD._base_key("order_due_salmon:1") == "order_due"
    assert TD._base_key("claims_open:3") == "claims_open" and TD._base_key("feedback:abc") == "feedback"


# ── over HTTP: the live role decides ─────────────────────────────────────────
@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    yield


def _login(user):
    import main
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": user, "password": auth.DEFAULT_PASSWORD}).status_code == 200
    return c


def test_the_endpoint_returns_states_for_the_signed_in_role(client, auth_on):
    for user in ("analyst1", "innovation1", "salessupport1", "management1"):
        d = _login(user).get("/api/bom-center/todo").json()
        assert all(e.get("state") in ("todo", "review", "done") for e in d["items"] + d["close"]["steps"]), user


def test_the_endpoint_without_auth_has_states_and_nobody_waits(client):
    d = client.get("/api/bom-center/todo").json()
    assert all(e["state"] == {"done": "done", "na": "done", "blocked": "review", "todo": "todo"}[e["status"]] for e in d["items"] + d["close"]["steps"])
