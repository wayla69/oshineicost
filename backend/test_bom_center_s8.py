"""BoM Center — Phase 2 · Sprint S8: the branch order.

The promises:
  * the suggestion deducts what will still be on the shelf: the last count, plus what
    arrived, minus what the sales used since, minus what the days before the cover will
    use, plus what is already on order — never below zero, in whole packs;
  * an item the branch did not count is not deducted, and a branch that never counted
    orders on the forecast alone (the order says so) — counting is encouraged, not forced;
  * the suggested quantities are frozen when the order is created; a confirmed quantity
    more than 20 % away needs a reason before it can be submitted;
  * submitted → (Sales Support) → received; a submitted order can be reopened, a
    received one books a goods receipt that the variance reads; an open order counts as
    "on order" for the next cycle;
  * PR-A: an order is made for a DELIVERY day (its cycle_date is the 13:15 cutoff two days
    before), and the branch counts what is on its shelf in the same flow (`save_flow`);
  * the branch login orders for its own branch only.
"""
from datetime import date, datetime

import pytest

import auth
from bom_center import orders as OR
from bom_center import stock as ST
from bom_center import variance as VA
from bom_center.clock import BKK
from test_bom_center_s4b import con  # noqa: F401  (sales to 5 Oct 2026 at branch 1; crab stick 1 kg packs)

AS_OF = date(2026, 10, 5)
WED = date(2026, 10, 7)                                           # round 1's delivery: cutoff Monday 5 Oct 13:15
NOW = datetime(2026, 10, 5, 8, 0, tzinfo=BKK)                     # Monday morning — before the cutoff
CRAB = "13-01-009"


def _crab(p):
    return next(r for r in p["rows"] if r["code"] == CRAB)


def _counts(*codes):
    """The shelf counted as empty: the suggestion is then the forecast alone."""
    return [{"code": c, "qty": 0, "unit": None} for c in codes]


def test_without_a_count_the_forecast_alone(con):  # noqa: F811
    p = OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    r = _crab(p)
    assert not p["stock"]["deducted"] and r["on_hand"] is None and r["counted"] is False
    assert r["need"] == pytest.approx(r["forecast"] + r["safety"], abs=0.02)
    assert r["order_unit"] == "pack" and r["order_units"] == pytest.approx(-(-r["need"] // 1000))
    assert p["order"] is None and p["order_day"] == "2026-10-05" and p["cycle_date"] == "2026-10-05"
    assert p["delivery"] == "2026-10-07" and p["cutoff_th"] == "วันจันทร์ 13:15" and p["late"] is False


def test_the_count_is_deducted(con):  # noqa: F811
    ST.save(con, 1, [{"code": CRAB, "qty": 3, "unit": "แพ็ค 1 กก."}], "udon", submit=True,
            count_date="2026-10-05")
    p = OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    r = _crab(p)
    assert p["stock"]["deducted"] and p["stock"]["basis"]["date"] == "2026-10-05"
    assert r["on_hand"] < 3000                                   # the 5th's sales came off the count
    assert r["projected"] == pytest.approx(r["on_hand"] - r["pre_cover_use"] + r["on_order"], abs=0.02)
    assert r["need"] == pytest.approx(max(0.0, r["forecast"] + r["safety"] - max(r["projected"], 0.0)), abs=0.02)
    assert r["pre_cover_use"] > 0                                # Tuesday 6th is used before Wednesday's delivery
    # a full shelf means nothing to order
    ST.save(con, 1, [{"code": CRAB, "qty": 500, "unit": "แพ็ค 1 กก."}], "udon", submit=True,
            count_date="2026-10-06")
    r = _crab(OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW))
    assert r["need"] == 0 and r["order_units"] == 0


def test_confirm_submit_reopen_receive(con):  # noqa: F811
    p = OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    sug = _crab(p)["order_units"]
    cnt = _counts(CRAB, "V-007")                                  # an ordered line needs a counted on-hand
    flow = lambda lines, **kw: OR.save_flow(con, 1, "1", WED, lines, kw.pop("counts", cnt), "udon",  # noqa: E731
                                            now=NOW, as_of=AS_OF, **kw)
    out = flow([{"code": CRAB, "confirmed_units": sug * 3}])
    assert out["needs_reason"] == [CRAB]
    with pytest.raises(ValueError):
        flow([], submit=True)
    with pytest.raises(ValueError):                              # "other" needs a note
        flow([{"code": CRAB, "confirmed_units": sug * 3, "reason_code": "other"}], submit=True)
    con.rollback()
    out = flow([{"code": CRAB, "confirmed_units": sug * 3, "reason_code": "event", "note": "จองกลุ่ม 40 คน"},
                {"code": "V-007", "confirmed_units": 2, "reason_code": "event"}], submit=True)
    assert out["status"] == "submitted" and out["value"] > 0 and out["late"] == 0 and out["uncounted"] == []
    assert out["count"]["status"] == "submitted"                  # the count is submitted with its order
    con.commit()                                                 # each API call commits on its own
    o = OR.detail(con, out["order_id"])
    lines = {l["code"]: l for l in o["lines"]}
    assert lines[CRAB]["suggested_units"] == sug                  # frozen
    assert lines[CRAB]["reason_th"] == "มีงาน / โปรโมชัน / ลูกค้าจองกลุ่ม"
    assert lines["V-007"]["suggested_units"] == 0
    assert o["delivery"] == "2026-10-07" and o["cycle_date"] == "2026-10-05" and o["cutoff_at"].startswith("2026-10-05T13:15")
    with pytest.raises(ValueError):                              # submitted: no edits until reopened
        flow([{"code": CRAB, "confirmed_units": 1}], counts=None)
    con.rollback()
    # the plan now shows the order, and it is "on order" for a later cycle
    assert OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)["order"]["status"] == "submitted"
    assert OR.on_order(con, 1, date(2026, 10, 9))[CRAB] == pytest.approx(sug * 3 * 1000)
    OR.reopen(con, 1, out["order_id"], "udon")
    assert OR.detail(con, out["order_id"])["status"] == "suggested"
    flow([{"code": CRAB, "confirmed_units": sug}], counts=None, submit=True)
    with pytest.raises(ValueError):
        OR.receive(con, 2, out["order_id"], [], "udon")          # another branch
    rec = OR.receive(con, 1, out["order_id"], [{"code": "13-01-009", "received_units": sug}], "udon",
                     receipt_date="2026-10-07")
    assert rec["status"] == "received" and rec["receipt_id"]
    r = next(x for x in VA.receipts(con, 1) if x["receipt_id"] == rec["receipt_id"])
    assert r["source"] == "order" and r["ref"] == f"ORDER-{out['order_id']}"
    assert r["lines"][0]["qty_base"] == pytest.approx(sug * 1000)
    assert OR.on_order(con, 1, date(2026, 10, 9)) == {}          # received: no longer on its way
    with pytest.raises(ValueError):
        OR.reopen(con, 1, out["order_id"], "udon")
    with pytest.raises(ValueError):
        OR.cancel(con, 1, out["order_id"], "udon")
    assert [h["status"] for h in OR.history(con, 1)] == ["received"]


def test_bad_quantities_and_cancel(con):  # noqa: F811
    p = OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    with pytest.raises(ValueError):                              # not in the master
        OR.save(con, 1, "1", p["order_day"], p, [{"code": "NOPE-1", "confirmed_units": 1}], "udon")
    con.rollback()
    with pytest.raises(ValueError):
        OR.save(con, 1, "1", p["order_day"], p, [{"code": "13-01-009", "confirmed_units": -1}], "udon")
    con.rollback()
    out = OR.save(con, 1, "1", p["order_day"], p, [{"code": "13-01-009", "confirmed_units": 0}], "udon")
    con.commit()
    with pytest.raises(ValueError):                              # nothing ordered
        OR.save(con, 1, "1", p["order_day"], p, [{"code": "13-01-009", "confirmed_units": 0,
                                                  "reason_code": "stock_more"}], "udon", submit=True)
    con.rollback()
    OR.cancel(con, 1, out["order_id"], "udon")
    assert OR.detail(con, out["order_id"])["status"] == "cancelled"
    assert OR.find(con, 1, "1", p["order_day"]) is None          # a fresh order can start


def test_deviation_rule():
    assert not OR._needs_reason(10, 12)                          # 20 %
    assert OR._needs_reason(10, 13)
    assert not OR._needs_reason(2, 3)                            # one pack either way is fine
    assert OR._needs_reason(0, 2) and not OR._needs_reason(0, 0)


def test_branch_role_may_order():
    mods = auth.ROLE_MODULES["branch"]
    assert auth.path_allowed("GET", "/api/bom-center/order/plan", mods)
    assert auth.path_allowed("POST", "/api/bom-center/order/save", mods)
    assert auth.path_allowed("POST", "/api/bom-center/order/7/receive", mods)
    assert not auth.path_allowed("POST", "/api/bom-center/order-calendar", mods)   # the sales team's table
