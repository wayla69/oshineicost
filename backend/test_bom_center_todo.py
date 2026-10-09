# -*- coding: utf-8 -*-
"""หน้างาน (bom_center/todo.py): the to-do list and the month-close checklist.

The promises:
  * every entry has a status, an owner and a deep link the page can open;
  * the month's items follow a draft from checks to release to acknowledgement;
  * the close steps turn green as the central note, the two counts and the
    confirmation land, per branch — and the end count waits for the begin count;
  * a cutoff day asks the branch for its order (count and order are one flow) and the
    centre for the queue;
  * a branch login sees only its own branch and never the centre's keys;
  * the answer is cheap: no variance is computed unless ONE branch is in context;
  * every role but marketing may read it.
"""
from datetime import date

import pytest

import auth
from bom_center import central as CE
from bom_center import stock as ST
from bom_center import todo as TD
from bom_center import variance as VA
from test_bom_center_s11 import _report, udon  # noqa: F401  (branch 1 = อุดรธานี, the centre's September report)
from test_bom_center_s4b import con  # noqa: F401  (sales Sep–Oct 2026 at branch 1, default order calendar)

TODAY = date(2026, 10, 7)        # a Wednesday: Saturday's cutoff (Thursday 13:15) is the nearest in the default calendar
M = "2026-09"
ALL_MODULES = set(auth.MODULES)
PACK = [{"code": "13-01-009", "qty": 2, "unit": "แพ็ค 1 กก."}]


def _by(d):
    """items by key (a per-branch key is `key:branch_id`; one branch here)."""
    return {i["key"].split(":")[0]: i for i in d["items"]}


def _steps(d):
    return {s["key"]: s for s in d["close"]["steps"]}


def test_shape_owners_and_links(udon):
    d = TD.build(udon, M, None, None, today=TODAY)
    assert d["month"] == M and d["today"] == "2026-10-07" and d["scoped"] is False and d["branch"] is None
    assert [s["seq"] for s in d["close"]["steps"]] == list(range(1, 10))
    for e in d["items"] + d["close"]["steps"]:
        assert e["status"] in ("done", "todo", "blocked", "na"), e
        assert set(e["owner_modules"]) <= ALL_MODULES, e["key"]
        assert e["go"].get("tab"), e["key"]
    it = _by(d)
    assert it["master_seed"]["status"] == "done"                 # the fixture's master has rows
    assert it["price_month"]["status"] == "todo"                 # July quotes only, no September actuals
    assert it["draft"]["status"] == "todo" and it["draft"]["go"]["pane"] == "rec"
    assert it["acknowledge"]["status"] == "na"
    due = it["order_due_2"]                                      # Wednesday: Saturday's delivery is the nearest
    assert due["status"] == "todo" and due["title"] == "ส่งใบสั่งซื้อรอบส่งวันเสาร์ (10/10) ภายใน วันพฤหัส 13:15"
    assert due["go"]["extra"] == {"round": "2", "delivery": "2026-10-10"} and due["owner_modules"] == ["branch.stock"]
    assert d["close"]["branches"] == []                          # nobody has started closing September
    assert _steps(d)["deliveries"]["status"] == "todo" and _steps(d)["release_in_force"]["status"] == "todo"


def test_the_month_flow_from_prices_to_acknowledgement(udon):
    c = udon
    c.execute("INSERT INTO bc_ingredient_price(ingredient_code, month, price_per_base, source)"
                " VALUES('13-01-009', ?, 0.2, 'purchase_avg')", (M,))
    assert _by(TD.build(c, M, None, None, today=TODAY))["price_month"]["status"] == "done"
    c.execute("INSERT INTO bc_release(label, kind, effective_month, status, price_month, created_by)"
                " VALUES('T 2026-11', 'monthly', '2026-11', 'draft', '2026-09', 't')")
    rid = c.execute("SELECT MAX(release_id) FROM bc_release").fetchone()[0]
    it = _by(TD.build(c, M, None, None, today=TODAY))
    assert it["draft"]["status"] == "todo" and it["draft"]["go"]["extra"]["release_id"] == rid   # the kitchen still has to submit it
    assert it["checks"]["status"] == "todo" and "ยังไม่ได้ตรวจ" in it["checks"]["detail"]
    assert it["release"]["status"] == "blocked"
    c.execute("INSERT INTO bc_release_check(release_id, check_code, ref, severity, message)"
                " VALUES(?, 'C04', 'X', 'block', 'm')", (rid,))
    it = _by(TD.build(c, M, None, None, today=TODAY))
    assert it["checks"]["status"] == "todo" and it["checks"]["count"] == 1 and it["release"]["status"] == "blocked"
    c.execute("UPDATE bc_release_check SET resolved_by='t', resolved_at='x' WHERE release_id=?", (rid,))
    it = _by(TD.build(c, M, None, None, today=TODAY))
    assert it["checks"]["status"] == "done" and it["release"]["status"] == "todo"
    assert "25" in it["release"]["detail"] and "เหลือ 18 วัน" in it["release"]["detail"]
    c.execute("UPDATE bc_release SET status='released', released_by='a' WHERE release_id=?", (rid,))
    it = _by(TD.build(c, M, None, None, today=TODAY))
    assert it["acknowledge"]["status"] == "todo" and it["acknowledge"]["go"]["extra"]["release_id"] == rid
    assert it["draft"]["status"] == "done" and "ประกาศใช้แล้ว" in it["draft"]["detail"]
    c.execute("UPDATE bc_release SET acknowledged_by='m', acknowledged_at='x' WHERE release_id=?", (rid,))
    assert _by(TD.build(c, M, None, None, today=TODAY))["acknowledge"]["status"] == "done"


def test_the_close_steps_follow_the_data_per_branch(udon):
    c = udon
    p = CE.parse_report(c, _report())
    CE.apply_catalogue(c, p, "ss")
    st = _steps(TD.build(c, M, None, None, today=TODAY))
    assert "บัญชีสินค้าส่วนกลาง" in st["central_report"]["detail"]
    assert st["code_links"]["status"] in ("todo", "done")
    CE.deliveries(c, p, "ss", [1], dry_run=False)
    d = TD.build(c, M, None, None, today=TODAY)
    st = _steps(d)
    assert d["close"]["branches"] == [{"branch_id": 1, "name": "อุดรธานี"}]
    assert st["deliveries"]["status"] == "done" and st["deliveries"]["branches"][0]["branch"] == "อุดรธานี"
    assert st["begin_count"]["status"] == "todo" and st["begin_count"]["go"]["extra"] == {"count_date": "2026-09-01"}
    assert st["end_count"]["status"] == "blocked" and st["receipts_confirm"]["status"] == "blocked"
    ST.save(c, 1, PACK, "u", submit=True, count_date="2026-09-01")
    st = _steps(TD.build(c, M, None, None, today=TODAY))
    assert st["begin_count"]["status"] == "done" and st["end_count"]["status"] == "todo"
    out = ST.save(c, 1, [{"code": "13-01-009", "qty": 1, "unit": "แพ็ค 1 กก."}], "u", submit=True, count_date="2026-10-01")
    st = _steps(TD.build(c, M, None, None, today=TODAY))
    assert st["end_count"]["status"] == "done" and st["receipts_confirm"]["status"] == "todo"
    assert st["receipts_confirm"]["go"] == {"tab": "branchstock", "pane": "var", "branch": 1, "extra": {"end_count_id": out["count_id"]}}
    assert st["monthly_summary"]["status"] == "todo"
    VA.confirm_receipts(c, 1, out["count_id"], "u")
    d = TD.build(c, M, None, None, today=TODAY)
    st = _steps(d)
    assert st["receipts_confirm"]["status"] == "done" and st["monthly_summary"]["status"] == "done"
    assert d["close"]["done"] >= 5


def test_a_cutoff_day_asks_for_the_order_and_the_queue(udon):
    c = udon
    mon = date(2026, 10, 5)                                      # round 1's cutoff day: Wednesday's delivery, 13:15
    it = _by(TD.build(c, M, None, None, today=mon))
    assert it["order_due_1"]["status"] == "todo" and it["order_due_1"]["due_today"] is True
    assert it["order_due_1"]["go"] == {"tab": "branchstock", "pane": "order",
                                       "extra": {"round": "1", "delivery": "2026-10-07"}, "branch": 1}
    assert it["queue_pending"]["status"] == "done"
    c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(1, '2026-10-05', '1', 'submitted')")
    it = _by(TD.build(c, M, None, None, today=mon))
    assert it["order_due_1"]["status"] == "done" and "ส่งแล้ว" in it["order_due_1"]["detail"]
    assert it["queue_pending"]["status"] == "todo" and it["queue_pending"]["count"] == 1
    assert it["queue_pending"]["urgent"] is False
    assert "ภายใน 14:00 ของวันตัดรอบ" in it["queue_pending"]["detail"]
    assert _by(TD.build(c, M, None, None, today=date(2026, 10, 6)))["queue_pending"]["urgent"] is True


def test_a_branch_login_sees_only_its_branch(udon):
    c = udon
    d = TD.build(c, M, None, "อุดรธานี", today=TODAY)
    assert d["scoped"] is True and d["branch"]["branch_id"] == 1
    assert all(i.get("branch_id") == 1 for i in d["items"])
    assert not {"price_month", "queue_pending", "draft", "calendar"} & {i["key"].split(":")[0] for i in d["items"]}
    assert d["close"]["branches"] == [{"branch_id": 1, "name": "อุดรธานี"}]
    assert _steps(d)["begin_count"]["go"]["tab"] == "branchstock"
    with pytest.raises(PermissionError):
        TD.build(c, M, 2, "อุดรธานี", today=TODAY)


def test_the_centre_view_never_computes_a_variance(udon, monkeypatch):
    from bom_center import variance

    def boom(*a, **k):
        raise AssertionError("variance.period was called for the centre view")
    monkeypatch.setattr(variance, "period", boom)
    TD.build(udon, M, None, None, today=TODAY)
    with pytest.raises(AssertionError):
        TD.build(udon, M, 1, None, today=TODAY)       # one branch in context: the summary IS computed


def test_every_role_but_marketing_may_read_it():
    # marketing has no supply module; the franchise owner (PR-L) reads its own page only, never the work list
    for role, mods in auth.ROLE_MODULES.items():
        assert auth.path_allowed("GET", "/api/bom-center/todo", mods) == (role not in ("marketing", "franchise")), role


def test_the_endpoint(client):
    r = client.get("/api/bom-center/todo")
    assert r.status_code == 200, r.text
    assert {"month", "today", "items", "close", "branches", "release"} <= set(r.json())
    assert client.get("/api/bom-center/todo?month=bad").status_code == 400
