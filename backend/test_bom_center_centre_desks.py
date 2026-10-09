# -*- coding: utf-8 -*-
"""PR-M5 — the centre's desks.

Server side of the pass (the pages themselves are checked in a browser by scripts/verify_centre_desks.py):
  * the branch selects of Sales Support and Operations list only the branches that have data in the month:
    GET /api/bom-center/stock/me?month=YYYY-MM keeps the branches with a slip, an order, a count or a receipt dated in
    that month; without `month` the whole list; a bad month is refused; a branch login still gets its own branch only;
  * the reconciliation rows the accounting page reads carry the count unit and its size (`count_unit`, `per_count_unit`),
    so the quantities can be shown in the unit the branch counts in;
  * the module's one month: the page fragments no longer hold a native month input and carry the month selects the
    shared helper fills (the label is เดือน), and the orderhub fragment no longer has the 4th "เดือนของไฟล์ Excel" control.
"""
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth
from bom_center import stock as ST

REPO = Path(__file__).resolve().parent.parent
TOKEN = "owner-token-xyz"


@pytest.fixture
def world(client, app_db):
    """BranchA (id 1) has a slip in September; BranchB (id 2) has an order in October; nobody has anything in August."""
    import main
    client.get("/api/bom-center/hub/queue")                       # creates the bc_* tables
    c = sqlite3.connect(str(main.DB_PATH))
    ids = {}
    ids["slip"] = c.execute("INSERT INTO bc_issue_slip(branch_id, slip_date, status, created_by, created_at, updated_at)"
                            " VALUES(1, '2026-09-02', 'draft', 'x', '2026-09-02', '2026-09-02')").lastrowid
    ids["order"] = c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, value)"
                             " VALUES(2, '2026-10-03', '1', 'submitted', 0)").lastrowid
    c.commit()
    try:
        yield ids
    finally:
        c.execute("DELETE FROM bc_issue_slip WHERE slip_id=?", (ids["slip"],))
        c.execute("DELETE FROM bc_order WHERE order_id=?", (ids["order"],))
        c.commit()
        c.close()


def test_branches_with_data_unit(world):
    import main
    c = sqlite3.connect(str(main.DB_PATH))
    try:
        assert ST.branches_with_data(c, "2026-09") == {1}
        assert ST.branches_with_data(c, "2026-10") == {2}
        assert ST.branches_with_data(c, "2026-08") == set()
        # a count and a receipt count as data too
        c.execute("INSERT INTO bc_stock_count(branch_id, count_date, kind, counted_by, created_at) VALUES(2, '2026-09-30', 'end', 'x', '2026-09-30')")
        c.execute("INSERT INTO bc_receipt(branch_id, receipt_date, source, entered_by, created_at) VALUES(1, '2026-10-05', 'branch', 'x', '2026-10-05')")
        assert ST.branches_with_data(c, "2026-09") == {1, 2}
        assert ST.branches_with_data(c, "2026-10") == {1, 2}
        c.rollback()
        for bad in ("", "2026-9", "2026-13", "september", None):
            with pytest.raises(ValueError):
                ST.branches_with_data(c, bad)
    finally:
        c.close()


def test_stock_me_month_lists_only_branches_with_data(client, world):
    allb = client.get("/api/bom-center/stock/me").json()["branches"]
    assert {b["branch_id"] for b in allb} >= {1, 2}
    sep = client.get("/api/bom-center/stock/me?month=2026-09").json()["branches"]
    assert [b["branch_id"] for b in sep] == [1]
    octo = client.get("/api/bom-center/stock/me?month=2026-10").json()["branches"]
    assert [b["branch_id"] for b in octo] == [2]
    assert client.get("/api/bom-center/stock/me?month=2026-08").json()["branches"] == []
    r = client.get("/api/bom-center/stock/me?month=2026-9")
    assert r.status_code == 400


def test_a_branch_login_keeps_its_own_branch_whatever_the_month(client, world, monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    import main
    mg = TestClient(main.app)
    assert mg.post("/api/auth/login", json={"username": "management1", "password": auth.DEFAULT_PASSWORD}).status_code == 200
    r = mg.post("/api/auth/users", headers={"X-Upload-Token": TOKEN},
                json={"username": "br_m5_a", "password": "Temp@12345", "role": "branch", "branch": "BranchA"})
    assert r.status_code in (200, 409), r.text
    try:
        b = TestClient(main.app)
        assert b.post("/api/auth/login", json={"username": "br_m5_a", "password": "Temp@12345"}).status_code == 200
        for m in ("", "?month=2026-09", "?month=2026-10", "?month=2026-08"):
            rows = b.get("/api/bom-center/stock/me" + m).json()["branches"]
            assert rows == [{"branch_id": 1, "name": "BranchA"}], (m, rows)
    finally:
        c = sqlite3.connect(str(main.DB_PATH))
        c.execute("DELETE FROM app_user WHERE username='br_m5_a'")
        c.commit()
        c.close()


def test_reconcile_count_unit_fields_exist(tmp_path):
    from bom_center import variance as VA
    from test_bom_center_s4b import _db
    con = _db(tmp_path)
    con.execute("UPDATE bc_ingredient SET recv_unit='แพ็ค 1 กก.', qty_per_recv=1000 WHERE code='13-01-009'")
    rows = [{"code": "13-01-009", "base_unit": "กรัม"}, {"code": "NOPE-1", "base_unit": "กรัม"}]
    VA.with_count_units(con, rows)
    assert rows[0]["per_count_unit"] == 1000.0 and rows[0]["count_unit"]
    assert rows[1]["count_unit"] == "กรัม" and rows[1]["per_count_unit"] == 1.0
    src = (REPO / "backend" / "bom_center" / "issue_reports.py").read_text(encoding="utf-8")
    assert "VA.with_count_units(con, rows)" in src            # reconcile() adds them to every row


# ── the pages: one month control, in Thai ─────────────────────────────────────────
def test_no_native_month_input_is_left_in_the_module_pages():
    for rel in ("tabs/52-bomcenter.html", "tabs/53-branchstock.html", "tabs/54-orderhub.html", "tabs/55-supplyhome.html"):
        src = (REPO / rel).read_text(encoding="utf-8")
        assert 'type="month"' not in src, rel
    oh = (REPO / "tabs/54-orderhub.html").read_text(encoding="utf-8")
    assert "เดือนของไฟล์ Excel" not in oh
    assert not re.search(r'id="ohb(Is|Iv|Close)Month"', oh)              # the tab has ONE month: #ohbMonth
    assert 'id="ohbMonth"' in oh
    for rel, ids in (("tabs/52-bomcenter.html", ("bmcMonth",)), ("tabs/55-supplyhome.html", ("shmMonth",)),
                     ("tabs/53-branchstock.html", ("brnVarDayMonth",)), ("tabs/54-orderhub.html", ("ohbMonth",))):
        src = (REPO / rel).read_text(encoding="utf-8")
        for i in ids:
            assert re.search(r'<select id="%s" class="supply-month"' % i, src), (rel, i)
    assert "เดือนราคา" not in (REPO / "tabs/52-bomcenter.html").read_text(encoding="utf-8").split("</div>")[0]


def test_the_month_helper_lists_eighteen_thai_months():
    core = (REPO / "js/01-core.js").read_text(encoding="utf-8")
    assert "const SUPPLY_MONTHS_BACK = 18;" in core
    assert "function supplyMonthPick(" in core and "function supplyMonthPut(" in core
    assert "supplyBranchesWithData" in core and "/api/bom-center/stock/me?month=" in core
