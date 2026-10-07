# -*- coding: utf-8 -*-
"""The branch's flows in fewer steps (BoM Center UX overhaul, PR-8).

The promises:
  * without a round, the order page opens on the round whose order day comes first;
  * the branch's order list can be narrowed to the orders still to be received, each
    with its delivery day and line count — so an order is received from the รับสินค้า
    pane whatever today's cycle is;
  * an order's detail names its delivery day.
"""
from datetime import date

from bom_center import orders as OR
from test_bom_center_s4b import con  # noqa: F401  (branch 1, default order calendar: Tue / Fri / Mon salmon)


def test_the_round_whose_order_day_comes_first(con):
    assert OR.next_round(con, 1, date(2026, 10, 5)) == "salmon"   # Monday: salmon's own day
    assert OR.next_round(con, 1, date(2026, 10, 6)) == "1"        # Tuesday
    assert OR.next_round(con, 1, date(2026, 10, 7)) == "2"        # Wednesday: Friday is nearer than next Tuesday
    assert OR.next_round(con, 1, date(2026, 10, 8)) == "2"        # Thursday


def test_history_narrows_to_the_orders_awaiting_receipt(con):
    con.executemany("INSERT INTO bc_order(branch_id, cycle_date, round, status, value) VALUES(1, ?, ?, ?, 0)",
                    [("2026-10-06", "1", "received"), ("2026-10-09", "2", "processed"),
                     ("2026-10-13", "1", "submitted"), ("2026-10-02", "2", "cancelled")])
    assert [o["status"] for o in OR.history(con, 1, 20)] == ["submitted", "processed", "received", "cancelled"]
    open_ = OR.history(con, 1, 20, statuses=OR.OPEN)
    assert [o["cycle_date"] for o in open_] == ["2026-10-13", "2026-10-09"]
    assert all("delivery" in o and "lines" in o and o["status_th"] for o in open_)
    assert open_[1]["delivery"] == "2026-10-10"           # round 2 on Friday: delivered the next day
    oid = open_[1]["order_id"]
    assert OR.detail(con, oid)["delivery"] == "2026-10-10"


def test_the_endpoints(client):
    me = client.get("/api/bom-center/stock/me").json()
    bid = me["branches"][0]["branch_id"]
    r = client.get(f"/api/bom-center/orders?status=open&branch_id={bid}")
    assert r.status_code == 200 and isinstance(r.json()["orders"], list)
    r = client.get(f"/api/bom-center/order/plan?branch_id={bid}")          # no round: the server picks
    assert r.status_code == 200, r.text
    assert r.json()["round"] in ("1", "2", "salmon")
