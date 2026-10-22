"""BoM Center — Phase 2 · Sprint S9: Sales Support's queue, Ops' compliance, the monthly summary.

The promises:
  * every submitted order reaches Sales Support's queue with its delivery day and SLA
    (submitted before the 13:15 cutoff → processed by 14:00 of the cutoff day, Bangkok time);
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
from datetime import date, datetime

import pytest

import auth
from bom_center import hub as HB
from bom_center import issues as IS
from bom_center import orders as OR
from bom_center import stock as ST
from bom_center import variance as VA
from bom_center.clock import BKK
from test_bom_center_s4b import con  # noqa: F401

AS_OF = date(2026, 10, 5)
WED = date(2026, 10, 7)                                           # round 1's delivery; its cutoff is Monday 5 Oct 13:15
NOW = datetime(2026, 10, 5, 8, 0, tzinfo=BKK)


def _submitted(con, confirm_factor=1.0, reason=None):  # noqa: F811
    p = OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    crab = next(r for r in p["rows"] if r["code"] == "13-01-009")
    q = round(crab["order_units"] * confirm_factor)
    IS.save(con, 1, "2026-10-05", [{"code": "V-007", "qty_used": 10, "unit": "กรัม"}], "udon", submit=True, now=NOW)  # today's slip
    out = OR.save_flow(con, 1, "1", WED, [{"code": "13-01-009", "confirmed_units": q, "reason_code": reason}],
                       "udon", submit=True, now=NOW, as_of=AS_OF)
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
    assert ln["processed_units"] == q - 1 and ln["ss_reason_th"].startswith("ผู้จำหน่าย")
    assert d["po_ref"] == "PO-2569-001" and d["processed_by"] == "ss"
    assert HB.queue(con) == [] and HB.queue(con, "processed")[0]["order_id"] == oid
    with pytest.raises(ValueError):                              # the branch can no longer reopen it
        OR.reopen(con, 1, oid, "udon")
    # untouched lines default to what the branch confirmed and need no reason
    HB.process(con, oid, [], "ss")
    assert next(l for l in OR.detail(con, oid)["lines"] if l["code"] == "13-01-009")["processed_units"] == q


def test_consolidated_per_delivery(con):  # noqa: F811
    con.execute("UPDATE bc_ingredient SET supplier='บจ.ปูอัดไทย' WHERE code='13-01-009'")   # the column stays, nothing reads it
    oid, _, q = _submitted(con)
    c = HB.consolidated(con, [oid])
    g = c["groups"][0]
    assert g["delivery"] == "2026-10-07" and "supplier" not in g and len(c["groups"]) == 1
    assert c["supplier"] == "บริษัท ไดกิโตะ กรุ๊ป จำกัด"
    it = g["items"][0]
    bname = con.execute("SELECT name FROM branches WHERE branch_id=1").fetchone()[0]
    assert it["code"] == "13-01-009" and it["total"] == q and it["by_branch"] == {bname: q}
    assert c["branches"] == [bname]
    assert HB.consolidated(con, []) == {"supplier": "บริษัท ไดกิโตะ กรุ๊ป จำกัด", "groups": [], "branches": []}


def test_sla_in_bangkok_time():
    # cycle_date is the cutoff date: in by 13:15 that day, processed by 14:00
    on = HB.sla("2026-10-06T02:00:00+00:00", "2026-10-06T06:30:00+00:00", "2026-10-06")   # 09:00 → 13:30
    assert on == {"submitted_on_time": True, "late": False, "processed_on_time": True, "hours_to_process": 4.5}
    late = HB.sla("2026-10-06T02:00:00+00:00", "2026-10-06T08:00:00+00:00", "2026-10-06")  # done 15:00
    assert late["processed_on_time"] is False
    after = HB.sla("2026-10-06T06:30:00+00:00", None, "2026-10-06")                        # sent 13:30, past 13:15
    assert after["submitted_on_time"] is False and after["late"] is True and after["processed_on_time"] is None


def test_compliance_places_the_gap(con):  # noqa: F811
    oid, sug, q = _submitted(con, confirm_factor=2.0, reason="event")
    HB.process(con, oid, [{"code": "13-01-009", "processed_units": q - 1, "reason": "short"}], "ss")
    OR.receive(con, 1, oid, [{"code": "13-01-009", "received_units": q - 2, "reason": "short"}], "udon",
               receipt_date="2026-10-07", ack=True)
    con.commit()
    qr = HB.queue(con, "received")[0]                              # the gap was claimed in the same save
    assert qr["claims"] == 1 and qr["claims_pending"] == 1
    c = HB.compliance(con, "2026-09-22", "2026-10-06", branch_id=1)
    by = {(r["cycle_date"], r["round"]): r for r in c["rows"]}
    r = by[("2026-10-05", "1")]                                    # keyed by the cutoff date (a Monday for Wednesday)
    assert r["status"] == "over" and r["confirmed_vs_suggested_pct"] == pytest.approx(100.0, abs=15)
    assert r["processed"] < r["confirmed"] and r["centre_change_pct"] < 0
    assert r["received"] < r["processed"] and r["delivery_gap_pct"] < 0
    assert r["delivery"] == "2026-10-07" and r["late"] is False
    # PR-U1: the branch started on 5 Oct (its first slip): the rounds before it are not judged
    assert by[("2026-09-28", "1")]["status"] == "not_started"      # a Monday with no order, before the start
    assert by[("2026-09-24", "2")]["status"] == "not_started"      # Thursday: round 2's cutoff, before the start
    assert by[("2026-10-01", "2")]["status"] == "not_started"
    assert c["counts"]["over"] == 1


def test_a_draft_is_not_submitted(con):  # noqa: F811
    IS.save(con, 1, "2026-10-04", [{"code": "V-007", "qty_used": 10, "unit": "กรัม"}], "udon", submit=True, now=NOW)  # PR-U1: started
    p = OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    OR.save(con, 1, "1", p["order_day"], p, [], "udon")
    con.commit()
    r = next(x for x in HB.compliance(con, "2026-10-05", "2026-10-05", 1)["rows"] if x["round"] == "1")
    assert r["status"] == "not_submitted"


def test_monthly_summary(con):  # noqa: F811
    def count(day, lines):
        return ST.save(con, 1, [{"code": c, "qty": q, "unit": "กรัม"} for c, q in lines.items()], "udon",
                       submit=True, count_date=day, require_all=False)["count_id"]
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
    ss, ops, br, wh = (auth.ROLE_MODULES[r] for r in ("salessupport", "operation", "branch", "warehouse"))
    assert auth.path_allowed("GET", "/api/bom-center/hub/queue", ss)
    # owner 2026-10-08: the WAREHOUSE updates what is sent; Sales Support reads the queue and exports it
    assert auth.path_allowed("POST", "/api/bom-center/hub/process/1", wh)
    assert not auth.path_allowed("POST", "/api/bom-center/hub/process/1", ss)
    assert auth.path_allowed("GET", "/api/bom-center/hub/queue", wh)
    assert auth.path_allowed("GET", "/api/bom-center/hub/consolidated", ss)
    assert auth.path_allowed("GET", "/api/bom-center/hub/compliance", ops)
    assert not auth.path_allowed("POST", "/api/bom-center/hub/process/1", ops)
    assert not auth.path_allowed("GET", "/api/bom-center/hub/queue", br)


import pytest as _pytest_s3  # noqa: E402
pytestmark = _pytest_s3.mark.usefixtures("recipe_mode")   # PR-S3: this module builds its forecast, plans and count sheets from recipes (mode full)
