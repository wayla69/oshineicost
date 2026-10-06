"""BoM Center — Phase 2 · Sprint S9: Sales Support's queue, Ops' compliance, the monthly summary.

The promises:
  * every submitted order reaches Sales Support's queue with its delivery day and SLA
    (submitted before 10:00 → processed by 14:00, Bangkok time);
  * Sales Support sends what the branch confirmed unless it says why not (6 reasons,
    "other" needs a note), marks the order processed with a PO reference, and works
    from one list per supplier × delivery day;
  * compliance walks every cycle the calendar expects and places a gap at the branch
    (confirmed vs suggested), at the centre (processed vs confirmed) or in the delivery
    (received vs processed); a missed cycle is "not ordered", a draft "not submitted";
  * the monthly summary adds up the periods ending in the month per branch, by class and
    reason, with the month before;
  * Sales Support works the queue, Ops reads it, a branch login sees none of it.
"""
from datetime import date

import pytest

import auth
from bom_center import hub as HB
from bom_center import orders as OR
from bom_center import stock as ST
from bom_center import variance as VA
from test_bom_center_s4b import con  # noqa: F401

AS_OF = date(2026, 10, 5)
TUE = date(2026, 10, 6)


def _submitted(con, confirm_factor=1.0, reason=None):  # noqa: F811
    p = OR.plan(con, 1, "1", TUE, as_of=AS_OF)
    crab = next(r for r in p["rows"] if r["code"] == "13-01-009")
    q = round(crab["order_units"] * confirm_factor)
    out = OR.save(con, 1, "1", p["order_day"], p, [{"code": "13-01-009", "confirmed_units": q, "reason_code": reason}],
                  "udon", submit=True)
    con.commit()
    return out["order_id"], crab["order_units"], q


def test_queue_and_process(con):  # noqa: F811
    oid, sug, q = _submitted(con)
    rows = HB.queue(con)
    assert [r["order_id"] for r in rows] == [oid]
    r = rows[0]
    assert r["delivery"] == "2026-10-07" and r["status"] == "submitted" and r["lines"] >= 1
    with pytest.raises(ValueError):                              # sends less without saying why
        HB.process(con, oid, [{"code": "13-01-009", "processed_units": q - 1}], "ss")
    con.rollback()
    with pytest.raises(ValueError):
        HB.process(con, oid, [{"code": "13-01-009", "processed_units": q - 1, "reason": "other"}], "ss")
    con.rollback()
    out = HB.process(con, oid, [{"code": "13-01-009", "processed_units": q - 1, "reason": "short",
                                 "note": "ซัพพลายเออร์ส่งได้ไม่ครบ"}], "ss", po_ref="PO-2569-001")
    assert out["status"] == "processed" and out["value"] > 0
    d = OR.detail(con, oid)
    ln = next(l for l in d["lines"] if l["code"] == "13-01-009")
    assert ln["processed_units"] == q - 1 and ln["ss_reason_th"].startswith("ซัพพลายเออร์")
    assert d["po_ref"] == "PO-2569-001" and d["processed_by"] == "ss"
    assert HB.queue(con) == [] and HB.queue(con, "processed")[0]["order_id"] == oid
    with pytest.raises(ValueError):                              # the branch can no longer reopen it
        OR.reopen(con, 1, oid, "udon")
    # untouched lines default to what the branch confirmed and need no reason
    HB.process(con, oid, [], "ss")
    assert next(l for l in OR.detail(con, oid)["lines"] if l["code"] == "13-01-009")["processed_units"] == q


def test_consolidated_per_supplier_and_delivery(con):  # noqa: F811
    con.execute("UPDATE bc_ingredient SET supplier='บจ.ปูอัดไทย' WHERE code='13-01-009'")
    oid, _, q = _submitted(con)
    c = HB.consolidated(con, [oid])
    g = c["groups"][0]
    assert (g["supplier"], g["delivery"]) == ("บจ.ปูอัดไทย", "2026-10-07")
    it = g["items"][0]
    bname = con.execute("SELECT name FROM branches WHERE branch_id=1").fetchone()[0]
    assert it["code"] == "13-01-009" and it["total"] == q and it["by_branch"] == {bname: q}
    assert c["branches"] == [bname]
    assert HB.consolidated(con, []) == {"groups": [], "branches": []}


def test_sla_in_bangkok_time():
    on = HB.sla("2026-10-06T02:00:00+00:00", "2026-10-06T06:30:00+00:00", "2026-10-06")   # 09:00 → 13:30
    assert on == {"submitted_on_time": True, "processed_on_time": True, "hours_to_process": 4.5}
    late = HB.sla("2026-10-06T02:00:00+00:00", "2026-10-06T08:00:00+00:00", "2026-10-06")  # done 15:00
    assert late["processed_on_time"] is False
    after = HB.sla("2026-10-06T04:00:00+00:00", None, "2026-10-06")                        # sent 11:00
    assert after["submitted_on_time"] is False and after["processed_on_time"] is None


def test_compliance_places_the_gap(con):  # noqa: F811
    oid, sug, q = _submitted(con, confirm_factor=2.0, reason="event")
    HB.process(con, oid, [{"code": "13-01-009", "processed_units": q - 1, "reason": "short"}], "ss")
    OR.receive(con, 1, oid, [{"code": "13-01-009", "received_units": q - 2}], "udon", receipt_date="2026-10-07")
    con.commit()
    c = HB.compliance(con, "2026-09-22", "2026-10-06", branch_id=1)
    by = {(r["cycle_date"], r["round"]): r for r in c["rows"]}
    r = by[("2026-10-06", "1")]
    assert r["status"] == "over" and r["confirmed_vs_suggested_pct"] == pytest.approx(100.0, abs=15)
    assert r["processed"] < r["confirmed"] and r["centre_change_pct"] < 0
    assert r["received"] < r["processed"] and r["delivery_gap_pct"] < 0
    assert by[("2026-09-29", "1")]["status"] == "not_ordered"      # a Tuesday with no order
    assert by[("2026-09-25", "2")]["status"] == "not_ordered"      # Friday round
    assert c["counts"]["over"] == 1


def test_a_draft_is_not_submitted(con):  # noqa: F811
    p = OR.plan(con, 1, "1", TUE, as_of=AS_OF)
    OR.save(con, 1, "1", p["order_day"], p, [], "udon")
    con.commit()
    r = next(x for x in HB.compliance(con, "2026-10-06", "2026-10-06", 1)["rows"] if x["round"] == "1")
    assert r["status"] == "not_submitted"


def test_monthly_summary(con):  # noqa: F811
    def count(day, lines):
        return ST.save(con, 1, [{"code": c, "qty": q, "unit": "กรัม"} for c, q in lines.items()], "udon",
                       submit=True, count_date=day)["count_id"]
    count("2026-09-08", {"13-01-009": 10000, "02-01-001": 5000})
    e = count("2026-09-15", {"13-01-009": 1000, "02-01-001": 2360})      # crab: far more used than the recipes
    VA.set_reason(con, 1, e, "13-01-009", "waste", None, "udon")
    count("2026-10-02", {"13-01-009": 500, "02-01-001": 100})
    con.commit()
    m = HB.monthly(con, "2026-09")
    b = m["branches"][0]
    assert b["branch_id"] == 1 and b["periods"] == 1 and b["days"] == 7
    assert b["gap_value"] > 0 and b["by_class"][next(k for k, v in b["by_class"].items() if v)] > 0
    assert m["top_reasons"][0]["reason"] == "waste" and m["top_reasons"][0]["items"] == 1
    assert m["previous"]["month"] == "2026-08" and b["prev_gap_pct"] is None
    o = HB.monthly(con, "2026-10")
    assert o["branches"][0]["periods"] == 1 and o["branches"][0]["prev_gap_pct"] == b["gap_pct"]


def test_who_may_do_what():
    ss, ops, br = (auth.ROLE_MODULES[r] for r in ("salessupport", "operation", "branch"))
    assert auth.path_allowed("GET", "/api/bom-center/hub/queue", ss)
    assert auth.path_allowed("POST", "/api/bom-center/hub/process/1", ss)
    assert auth.path_allowed("GET", "/api/bom-center/hub/compliance", ops)
    assert not auth.path_allowed("POST", "/api/bom-center/hub/process/1", ops)
    assert not auth.path_allowed("GET", "/api/bom-center/hub/queue", br)
