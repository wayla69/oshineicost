"""PR-M2 — ONE order value everywhere.

An order showed ฿114,778 in the warehouse queue and the branch history but ฿46,350 on the order page and after the
warehouse had sent it. The queue and the history read `bc_order.value`, a snapshot taken when the branch last saved;
the order page and the warehouse's confirmation priced the lines again. The snapshot goes stale when the warehouse
sends other quantities, or when the centre's list or the month's prices are imported after the order was saved.

The promise now: every place that shows a value — queue, history, order detail, the warehouse's confirmation —
prices the lines now, through hub.with_values (hub._value), and says which value it is:
  มูลค่าที่สั่ง (confirmed_units) until the warehouse sends, มูลค่าที่จัดส่ง (processed_units) afterwards.
"""
from datetime import date, datetime

import pytest

from bom_center import hub as HB
from bom_center import issues as IS
from bom_center import orders as OR
from bom_center.clock import BKK
from test_bom_center_s4b import con  # noqa: F401

AS_OF = date(2026, 10, 5)
WED = date(2026, 10, 7)
NOW = datetime(2026, 10, 5, 8, 0, tzinfo=BKK)


def _submitted(con):  # noqa: F811
    p = OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    crab = next(r for r in p["rows"] if r["code"] == "13-01-009")
    IS.save(con, 1, "2026-10-05", [{"code": "V-007", "qty_used": 10, "unit": "กรัม"}], "udon", submit=True, now=NOW)
    out = OR.save_flow(con, 1, "1", WED, [{"code": "13-01-009", "confirmed_units": crab["order_units"]}],
                       "udon", submit=True, now=NOW, as_of=AS_OF)
    con.commit()
    return out["order_id"], crab["order_units"]


def test_a_stale_snapshot_is_never_shown(con):  # noqa: F811
    oid, q = _submitted(con)
    live = HB._value(con, oid, "confirmed_units", "2026-10")
    assert live > 0
    con.execute("UPDATE bc_order SET value=? WHERE order_id=?", (live * 2.4763, oid))      # the snapshot went stale
    con.commit()
    queue = HB.queue(con)[0]
    hist = OR.history(con, 1)[0]
    detail = OR.detail(con, oid)
    for row in (queue, hist, detail):
        assert row["value"] == pytest.approx(live) and row["value_ordered"] == pytest.approx(live)
        assert row["value_sent"] is None and row["value_label"] == "มูลค่าที่สั่ง"


def test_after_the_warehouse_sends_every_place_shows_the_sent_value(con):  # noqa: F811
    oid, q = _submitted(con)
    ordered = HB._value(con, oid, "confirmed_units", "2026-10")
    out = HB.process(con, oid, [{"code": "13-01-009", "processed_units": q - 1, "reason": "short"}], "wh")
    con.commit()
    sent = HB._value(con, oid, "processed_units", "2026-10")
    assert 0 < sent < ordered
    assert out["value"] == pytest.approx(sent) and out["value_label"] == "มูลค่าที่จัดส่ง"
    assert out["value_ordered"] == pytest.approx(ordered)
    for row in (HB.queue(con, "processed")[0], OR.history(con, 1)[0], OR.detail(con, oid)):
        assert row["value"] == pytest.approx(sent) and row["value_sent"] == pytest.approx(sent)
        assert row["value_ordered"] == pytest.approx(ordered) and row["value_label"] == "มูลค่าที่จัดส่ง"


def test_the_saving_branch_gets_the_same_number(con):  # noqa: F811
    oid, q = _submitted(con)
    row = OR.detail(con, oid)
    assert row["value"] == pytest.approx(OR._value(con, oid, "2026-10-05"))
