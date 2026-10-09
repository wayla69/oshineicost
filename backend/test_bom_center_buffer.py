# -*- coding: utf-8 -*-
"""The branch's buffer (PR-19): "คาดว่ายอดขายรอบนี้จะต่างจากปกติ" — a % with a
reason on the order that scales the forecast of every item, while the frozen
suggestion stays the engine's base number and the 20 % rule judges against the
scaled one; plus the covers helper that turns expected customers into a %."""
from datetime import date, datetime

import pytest

from bom_center import forecast as FC
from bom_center import orders as OR
from bom_center.clock import BKK
from test_bom_center_s11 import _report, udon  # noqa: F401  (branch 1 = อุดรธานี, the centre's list)
from test_bom_center_s4b import con  # noqa: F401  (sales Sep–Oct 2026 at branch 1)

WED = date(2026, 10, 7)                                   # round 1's delivery (cutoff Monday 5 Oct 13:15)
NOW = datetime(2026, 10, 5, 8, 0, tzinfo=BKK)


def _rows(d):
    return {r["code"]: r for r in d["rows"]}


def test_uplift_scales_the_forecast_and_keeps_the_base(udon):
    base = FC.suggest(udon, 1, "1", WED, delivery=WED)
    up = FC.suggest(udon, 1, "1", WED, uplift_pct=20, delivery=WED)
    assert base["uplift_pct"] == 0 and up["uplift_pct"] == 20
    b, u = _rows(base), _rows(up)
    assert b and set(b) == set(u)
    for code, r in u.items():
        assert r["forecast_base"] == pytest.approx(b[code]["forecast"], rel=1e-6)
        assert r["forecast"] == pytest.approx(b[code]["forecast"] * 1.2, rel=1e-6)
        assert r["need"] >= r["need_base"] - 1e-9 and r["need_base"] == pytest.approx(b[code]["need"], rel=1e-6)


def test_plan_carries_the_buffer_and_base_units(udon):
    p = OR.plan(udon, 1, "1", WED, buffer_pct=50, now=NOW)
    assert p["buffer"]["pct"] == 50 and [r["code"] for r in p["buffer"]["reasons"]] == [k for k, _ in OR.BUFFER_REASONS]
    assert any(r["order_units"] > r["order_units_base"] for r in p["rows"] if r["order_units"])
    p0 = OR.plan(udon, 1, "1", WED, now=NOW)
    assert p0["buffer"]["pct"] == 0 and all(r["order_units"] == r["order_units_base"] for r in p0["rows"])
    with pytest.raises(ValueError):
        OR.plan(udon, 1, "1", WED, buffer_pct=150, now=NOW)


def test_save_freezes_the_base_and_relaxes_the_rule_by_the_buffer(udon):
    c = udon
    p = OR.plan(c, 1, "1", WED, buffer_pct=20, now=NOW)
    r = OR.save(c, 1, "1", p["order_day"], p, [], "u", buffer={"pct": 20, "reason": "promotion"})
    oid = r["order_id"]
    o = OR.detail(c, oid)
    assert o["buffer_pct"] == 20 and o["buffer_reason"] == "promotion" and o["buffer_reason_th"] == "โปรโมชัน"
    by = {l["code"]: l for l in o["lines"]}
    pr = {x["code"]: x for x in p["rows"]}
    for code, l in by.items():
        assert l["suggested_units"] == pr[code]["order_units_base"]          # the engine's number, frozen
        assert l["confirmed_units"] == pr[code]["order_units"]               # the branch starts from the buffered one
    assert r["needs_reason"] == []                                           # the buffer explains its own share
    # the plan without an explicit buffer now follows the order's own
    assert OR.plan(c, 1, "1", WED, now=NOW)["buffer"]["pct"] == 20
    # a line far beyond base × 1.2 still needs a reason; one at base × 1.2 does not
    code = max(by, key=lambda k: by[k]["suggested_units"] or 0)
    s = by[code]["suggested_units"]
    assert s >= 1
    r2 = OR.save(c, 1, "1", p["order_day"], p, [{"code": code, "confirmed_units": s * 1.2}], "u")
    assert code not in r2["needs_reason"]
    r3 = OR.save(c, 1, "1", p["order_day"], p, [{"code": code, "confirmed_units": s * 3}], "u")
    assert code in r3["needs_reason"]
    # the history and the hub queue carry the buffer
    assert OR.history(c, 1)[0]["buffer_pct"] == 20
    from bom_center import issues as IS
    IS.save(c, 1, "2026-10-05", [{"code": "V-007", "qty_used": 10, "unit": "กรัม"}], "u", submit=True, now=NOW)   # today's slip
    OR.save_flow(c, 1, "1", WED, [{"code": code, "confirmed_units": s}], "u", submit=True, now=NOW)
    from bom_center import hub as HB
    q = HB.queue(c, "submitted")
    assert q and q[0]["buffer_pct"] == 20 and q[0]["buffer_reason_th"] == "โปรโมชัน"


def test_buffer_validation(udon):
    c = udon
    p = OR.plan(c, 1, "2", date(2026, 10, 10), now=NOW)                       # round 2: Saturday
    with pytest.raises(ValueError):
        OR.save(c, 1, "2", p["order_day"], p, [], "u", buffer={"pct": 20})                     # no reason
    with pytest.raises(ValueError):
        OR.save(c, 1, "2", p["order_day"], p, [], "u", buffer={"pct": 20, "reason": "other"})  # other without note
    with pytest.raises(ValueError):
        OR.save(c, 1, "2", p["order_day"], p, [], "u", buffer={"pct": -80, "reason": "quiet"})  # out of range
    r = OR.save(c, 1, "2", p["order_day"], p, [], "u", buffer={"pct": 0, "reason": "promotion"})
    o = OR.detail(c, r["order_id"])
    assert o["buffer_pct"] is None and o["buffer_reason"] is None                           # 0 clears


def test_covers_helper_shape(udon):
    c = udon
    out = FC.covers(c, 1, "1", WED, delivery=WED)
    assert out["cover_from"] == "2026-10-07" and out["cover_to"] and out["order_day"] == "2026-10-05"
    if out["available"]:
        assert out["days"] and all({"date", "weekday", "baseline_pax"} <= set(d) for d in out["days"])
        assert out["baseline_total"] == sum(d["baseline_pax"] for d in out["days"])
    else:
        assert out["days"] == [] and out["baseline_total"] == 0


def test_covers_endpoint(client):
    r = client.get("/api/bom-center/order/covers?branch_id=1&round=1&delivery=2026-10-07")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["round"] == "1" and "available" in j and "days" in j and j["delivery"] == "2026-10-07"
    r2 = client.get("/api/bom-center/order/plan?branch_id=1&round=1&delivery=2026-10-07&buffer_pct=500")
    assert r2.status_code == 400
    r3 = client.get("/api/bom-center/order/covers?branch_id=1&round=1&order_day=2026-10-06")     # deprecated form still works
    assert r3.status_code == 200 and r3.json()["round"] == "1"
