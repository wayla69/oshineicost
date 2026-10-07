# -*- coding: utf-8 -*-
"""The gap audit's fixes (PR-20): the month's central note never doubles a branch's
own receipts, Ops reads the central list, ingredient writes need a supply module,
and "รับสินค้าตามใบสั่งซื้อ" opens the receive pane on the order."""
from datetime import date

import auth
from bom_center import central as CE
from bom_center import todo as TD
from test_bom_center_s11 import _report, udon  # noqa: F401  (branch 1 = อุดรธานี, the centre's September report)
from test_bom_center_s4b import con  # noqa: F401


def test_month_import_skips_a_branch_that_booked_its_own_receipts(udon):
    c = udon
    p = CE.parse_report(c, _report())
    CE.apply_catalogue(c, p, "ss")
    month = p["month"]
    pre = CE.deliveries(c, p, "ss", dry_run=True)
    b = next(x for x in pre["branches"] if x["branch_id"] == 1)
    assert b["own_receipts"] == 0 and not b.get("error")
    c.execute("INSERT INTO bc_receipt(branch_id, receipt_date, source, ref, entered_by, created_at)"
              " VALUES(1, ?, 'order', 'ORDER-9', 'u', '')", (f"{month}-10",))
    pre2 = CE.deliveries(c, p, "ss", dry_run=True)
    b2 = next(x for x in pre2["branches"] if x["branch_id"] == 1)
    assert b2["own_receipts"] == 1 and "บันทึกรับสินค้าเองแล้ว" in b2["error"]
    CE.deliveries(c, p, "ss", dry_run=False)
    assert c.execute("SELECT COUNT(*) FROM bc_receipt WHERE branch_id=1 AND ref=?", (f"CENTRAL-{month}",)).fetchone()[0] == 0
    # the centre's own earlier note does not count as the branch's
    c.execute("DELETE FROM bc_receipt WHERE ref='ORDER-9'")
    CE.deliveries(c, p, "ss", dry_run=False)
    assert c.execute("SELECT COUNT(*) FROM bc_receipt WHERE branch_id=1 AND ref=?", (f"CENTRAL-{month}",)).fetchone()[0] == 1
    b3 = next(x for x in CE.deliveries(c, p, "ss", dry_run=True)["branches"] if x["branch_id"] == 1)
    assert b3["own_receipts"] == 0 and b3["replaced"] == 1


def test_ops_reads_the_central_list_and_ingredient_writes_need_a_supply_module():
    ops = auth.ROLE_MODULES["operation"]
    assert auth.path_allowed("GET", "/api/bom-center/central", ops)
    assert auth.path_allowed("GET", "/api/bom-center/central/links", ops)
    assert not auth.path_allowed("POST", "/api/bom-center/central/import", ops)
    assert not auth.path_allowed("POST", "/api/ingredients/pack-sizes", auth.ROLE_MODULES["marketing"])
    assert auth.path_allowed("POST", "/api/ingredients/pack-sizes", auth.ROLE_MODULES["salessupport"])
    assert auth.path_allowed("POST", "/api/ingredients/pack-sizes", ops)
    assert auth.path_allowed("GET", "/api/ingredients/pack-sizes", auth.ROLE_MODULES["accounting"])


def test_receive_pending_opens_the_receive_pane_on_the_order(udon):
    c = udon
    c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(1, '2026-10-06', '1', 'processed')")
    oid = c.execute("SELECT MAX(order_id) FROM bc_order").fetchone()[0]
    it = {i["key"].split(":")[0]: i for i in TD.build(c, "2026-09", 1, None, today=date(2026, 10, 9))["items"]}
    go = it["receive_pending"]["go"]
    assert go["tab"] == "branchstock" and go["pane"] == "recv" and go["extra"]["order_id"] == oid
