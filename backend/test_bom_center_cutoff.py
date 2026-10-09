# -*- coding: utf-8 -*-
"""PR-A — the delivery-day calendar and the 13:15 cutoff (PR-J1: the count made while ordering is gone).

The promises:
  * the sales team sets the DELIVERY weekday; an order must be submitted before 13:15 two
    days before it (a Tuesday delivery: Saturday) — the cutoff weekday and the lead are derived,
    and a row saved the old way (order weekday + lead) reads as the delivery weekday it implies;
  * (PR-J1) the branch no longer counts while ordering: saving or submitting an order writes no
    stock count, and a submit needs the issue slip of the day confirmed; 'spot' rows left by the
    old flow are ignored by every month-end reader;
  * a submit after the cutoff is refused until the late fee is acknowledged, then accepted and
    flagged (queue, SLA, Excel);
  * the todo, the reminders and the exports speak the same cutoff.
"""
import io
import sqlite3
from datetime import date, datetime

import pytest
from openpyxl import load_workbook

from bom_center import forecast as F
from bom_center import hub as HB
from bom_center import issues as IS
from bom_center import orders as OR
from bom_center import schema as SC
from bom_center import stock as ST
from bom_center import todo as TD
from bom_center.clock import BKK
from test_bom_center_export import _restore, _rows
from test_bom_center_s11 import udon  # noqa: F401  (branch 1 = อุดรธานี)
from test_bom_center_s4b import con  # noqa: F401  (sales to 5 Oct 2026 at branch 1; crab stick 1 kg packs)

AS_OF = date(2026, 10, 5)
WED = date(2026, 10, 7)
CRAB = "13-01-009"


def at(d, h, m=0):
    return datetime(2026, 10, d, h, m, tzinfo=BKK)


NOW = at(5, 8)                       # Monday 08:00 — before round 1's cutoff
LATE = at(5, 15)                     # Monday 15:00 — after it
SLIP_LINE = [{"code": "V-007", "qty_used": 10, "unit": "กรัม"}]


def slip(c, now=NOW):
    """The branch confirms the issue slip of `now`'s day."""
    IS.save(c, 1, now.date().isoformat(), SLIP_LINE, "udon", submit=True, now=now)
    c.commit()


# ── the rule ─────────────────────────────────────────────────────────────────
def test_cutoff_is_1315_two_days_before_and_a_sunday_moves_to_saturday():
    assert F.cutoff_for(date(2026, 10, 6)) == at(3, 13, 15)            # Tuesday delivery -> Saturday
    assert F.cutoff_for(date(2026, 10, 7)) == at(5, 13, 15)            # Wednesday -> Monday
    assert F.cutoff_for(date(2026, 10, 11)) == at(9, 13, 15)           # Sunday -> Friday
    assert F.cutoff_for(date(2026, 10, 12)) == at(10, 13, 15)          # Monday -> Saturday
    assert F.cutoff_for(WED).isoformat() == "2026-10-05T13:15:00+07:00"
    assert (F.cutoff_weekday(2), F.cutoff_weekday(3), F.cutoff_weekday(0)) == (6, 1, 5)
    assert F.cutoff_th(F.cutoff_for(WED)) == "วันจันทร์ 13:15" and F.day_th(WED) == "วันพุธ 7/10"
    assert f"{F.cutoff_time():%H:%M}" == "13:15"


def test_calendar_defaults_the_delivery_weekday_and_derives_the_rest(con):  # noqa: F811
    cal = F.calendar(con, 1)
    assert {r: cal[r]["delivery_weekday"] for r in cal} == {"1": 3, "2": 6, "salmon": 1}
    assert {r: cal[r]["order_weekday"] for r in cal} == {"1": 1, "2": 4, "salmon": 6}
    assert {r: cal[r]["lead_days"] for r in cal} == {"1": 2, "2": 2, "salmon": 2}
    assert [cal[r]["cutoff_th"] for r in ("1", "2", "salmon")] == ["วันจันทร์ 13:15", "วันพฤหัส 13:15", "วันเสาร์ 13:15"]
    assert not any(c["filled"] for c in cal.values())
    con.execute("INSERT INTO bc_order_calendar(branch_id, round, order_weekday, delivery_weekday, lead_days, filled_by)"
                " VALUES(1, '1', 0, 2, 0, 'sales')")               # a Tuesday delivery
    c1 = F.calendar(con, 1)["1"]
    assert (c1["delivery_weekday"], c1["order_weekday"], c1["lead_days"], c1["filled"]) == (2, 6, 3, True)
    assert c1["cutoff_th"] == "วันเสาร์ 13:15"


@pytest.mark.parametrize("order_wd, lead, delivery_wd", [(2, 1, 3), (3, 1, 4), (5, 1, 6), (6, 1, 0), (1, 7, 1)])
def test_a_legacy_row_reads_as_the_delivery_it_implies_and_is_backfilled(con, order_wd, lead, delivery_wd):  # noqa: F811
    con.execute("DELETE FROM bc_order_calendar")
    con.execute("INSERT INTO bc_order_calendar(branch_id, round, order_weekday, lead_days, filled_by)"
                " VALUES(1, '1', ?, ?, 'sales')", (order_wd, lead))
    assert F.calendar(con, 1)["1"]["delivery_weekday"] == delivery_wd         # (Tue,1)->Wed, (Sat,1)->Sun, (Mon,7)->Mon …
    OR.backfill(con)
    assert con.execute("SELECT delivery_weekday FROM bc_order_calendar WHERE round='1'").fetchone()[0] == delivery_wd
    assert F.calendar(con, 1)["1"]["order_weekday"] == F.cutoff_weekday(delivery_wd)


def test_backfill_gives_old_orders_a_delivery_day_and_never_touches_the_connection_when_idle(con):  # noqa: F811
    con.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(1, '2026-10-06', '1', 'submitted')")
    con.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(1, '2026-10-09', '2', 'processed')")
    con.commit()
    SC.ensure(con, commit=True)
    rows = con.execute("SELECT cycle_date, delivery_date, cutoff_at, late FROM bc_order ORDER BY order_id").fetchall()
    assert rows == [("2026-10-06", "2026-10-07", "2026-10-05T13:15:00+07:00", 0),
                    ("2026-10-09", "2026-10-10", "2026-10-08T13:15:00+07:00", 0)]
    SC.ensure(con)                                                             # idempotent …
    assert not con.in_transaction                                              # … and a no-op opens no write transaction
    assert con.execute("SELECT COUNT(*) FROM bc_order WHERE delivery_date IS NULL").fetchone()[0] == 0


# ── cover periods and the coming deliveries ──────────────────────────────────
def test_cover_for_delivery_all_rounds(con):  # noqa: F811
    assert F.cover_for_delivery(con, 1, "1", WED) == (date(2026, 10, 5), WED, WED, date(2026, 10, 9))
    assert F.cover_for_delivery(con, 1, "2", date(2026, 10, 10)) == (date(2026, 10, 8), date(2026, 10, 10),
                                                                      date(2026, 10, 10), date(2026, 10, 13))
    assert F.cover_for_delivery(con, 1, "salmon", date(2026, 10, 12)) == (date(2026, 10, 10), date(2026, 10, 12),
                                                                           date(2026, 10, 12), date(2026, 10, 18))
    con.execute("INSERT INTO bc_order_calendar(branch_id, round, order_weekday, delivery_weekday, lead_days)"
                " VALUES(1, '1', 6, 2, 3)")                                    # round 1 on Tuesdays
    assert F.cover_for_delivery(con, 1, "1", date(2026, 10, 6)) == (date(2026, 10, 3), date(2026, 10, 6),
                                                                     date(2026, 10, 6), date(2026, 10, 9))


def test_next_deliveries_and_the_one_to_work_on(con):  # noqa: F811
    items = F.next_deliveries(con, 1, date(2026, 10, 5), now=NOW)
    assert [(i["round"], i["delivery"]) for i in items] == [("1", "2026-10-07"), ("2", "2026-10-10"),
                                                            ("salmon", "2026-10-12"), ("1", "2026-10-14")]
    first = items[0]
    assert first["round_th"] == "รอบ 1" and first["delivery_th"] == "วันพุธ 7/10" and first["cycle_date"] == "2026-10-05"
    assert first["cutoff_at"] == "2026-10-05T13:15:00+07:00" and first["cutoff_th"] == "วันจันทร์ 13:15"
    assert first["open"] is True and first["late"] is False and 5.2 <= first["hours_left"] <= 5.3
    assert (first["cover_from"], first["cover_to"]) == ("2026-10-07", "2026-10-09")
    assert F.pick_delivery(items)["round"] == "1"
    after = F.next_deliveries(con, 1, date(2026, 10, 5), now=LATE)             # past Monday 13:15
    assert after[0]["late"] is True and after[0]["hours_left"] < 0
    assert F.pick_delivery(after)["round"] == "2"                              # the first one still open
    assert OR.next_round(con, 1, date(2026, 10, 5), now=LATE) == "2"
    assert F.pick_delivery([]) is None
    # with only the day, the clock reads 08:00 that morning
    assert F.next_deliveries(con, 1, date(2026, 10, 5))[0]["open"] is True


# ── the plan by delivery ─────────────────────────────────────────────────────
def test_plan_by_delivery_carries_the_cutoff_and_the_count_unit(con):  # noqa: F811
    p = OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    assert p["cycle_date"] == p["order_day"] == "2026-10-05" and p["delivery"] == "2026-10-07"
    assert p["cutoff_th"] == "วันจันทร์ 13:15" and p["delivery_th"] == "วันพุธ 7/10"
    assert p["cutoff_at"] == "2026-10-05T13:15:00+07:00" and p["open"] is True and p["late"] is False
    assert [d["round"] for d in p["deliveries"]] == ["1", "2", "salmon", "1"] and "count" not in p
    crab = next(r for r in p["rows"] if r["code"] == CRAB)
    assert crab["count_unit"] == "แพ็ค 1 กก." and crab["per_count_unit"] == 1000 and crab["system_qty"] is None
    late = OR.plan(con, 1, "1", WED, as_of=AS_OF, now=LATE)
    assert late["late"] is True and late["open"] is False and late["hours_left"] < 0


# ── ordering without a count ─────────────────────────────────────────────────
def test_saving_an_order_writes_no_stock_count(con):  # noqa: F811
    out = OR.save_flow(con, 1, "1", WED, [], "udon", now=NOW, as_of=AS_OF)
    assert out["status"] == "suggested" and out["slip_today"]["status"] == "missing" and "count" not in out
    assert con.execute("SELECT COUNT(*) FROM bc_stock_count").fetchone()[0] == 0
    assert con.execute("SELECT stock_count_id FROM bc_order WHERE order_id=?", (out["order_id"],)).fetchone()[0] is None
    # a month-end count is the order's basis (the id is kept), still no count of its own
    cid = ST.save(con, 1, [{"code": CRAB, "qty": 3, "unit": "แพ็ค 1 กก."}], "udon", submit=True, count_date="2026-10-05")["count_id"]
    again = OR.save_flow(con, 1, "1", WED, [], "udon", now=NOW, as_of=AS_OF)
    assert con.execute("SELECT stock_count_id FROM bc_order WHERE order_id=?", (again["order_id"],)).fetchone()[0] == cid
    assert con.execute("SELECT COUNT(*) FROM bc_stock_count").fetchone()[0] == 1


def test_an_order_needs_todays_confirmed_slip_and_an_earlier_gap_only_warns(con):  # noqa: F811
    line = [{"code": "V-007", "confirmed_units": 2, "reason_code": "event"}]
    OR.save_flow(con, 1, "1", WED, line, "udon", now=NOW, as_of=AS_OF)                    # saving a draft is fine …
    con.commit()
    with pytest.raises(ValueError, match="ต้องยืนยันใบตัดเบิกของวันนี้ก่อนส่งใบสั่งซื้อ"):  # … sending it is not
        OR.save_flow(con, 1, "1", WED, line, "udon", submit=True, now=NOW, as_of=AS_OF)
    con.rollback()
    IS.save(con, 1, "2026-10-05", SLIP_LINE, "udon", now=NOW)                              # a draft slip is not enough
    con.commit()
    with pytest.raises(ValueError, match="ต้องยืนยันใบตัดเบิกของวันนี้"):
        OR.save_flow(con, 1, "1", WED, line, "udon", submit=True, now=NOW, as_of=AS_OF)
    con.rollback()
    ST.save(con, 1, [{"code": CRAB, "qty": 3, "unit": "แพ็ค 1 กก."}], "udon", submit=True, count_date="2026-10-02")
    con.commit()                                                                          # 2, 3 and 4 Oct have no slip
    p = OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    assert p["stock"]["missing_days"] == ["2026-10-02", "2026-10-03", "2026-10-04"]       # a warning, today is not in it
    IS.save(con, 1, "2026-10-05", SLIP_LINE, "udon", submit=True, now=NOW)
    crab = next(r for r in p["rows"] if r["code"] == CRAB)["order_units"]                  # the draft's crab line follows the stock
    ok = OR.save_flow(con, 1, "1", WED, line + [{"code": CRAB, "confirmed_units": crab}], "udon", submit=True, now=NOW, as_of=AS_OF)
    assert ok["status"] == "submitted" and ok["late"] == 0 and ok["delivery"] == "2026-10-07"
    con.commit()
    o = OR.detail(con, ok["order_id"])
    assert o["status"] == "submitted" and o["late"] == 0 and o["delivery"] == "2026-10-07"
    assert o["cutoff_at"] == "2026-10-05T13:15:00+07:00" and o["submitted_at"] == "2026-10-05T01:00:00+00:00"
    assert con.execute("SELECT COUNT(*) FROM bc_stock_count WHERE kind='spot'").fetchone()[0] == 0   # nothing from the order flow
    assert ST.sheet(con, 1, AS_OF)["next_kind"] == "end"                                 # the month-end sequence is untouched


def test_a_draft_suggestion_follows_the_system_stock_and_is_frozen_when_sent(con):  # noqa: F811
    first = OR.save_flow(con, 1, "1", WED, [], "udon", now=NOW, as_of=AS_OF)
    crab = lambda: next(ln for ln in OR.detail(con, first["order_id"])["lines"] if ln["code"] == CRAB)  # noqa: E731
    s0 = crab()["suggested_units"]
    assert s0 > 0 and crab()["confirmed_units"] == s0
    ST.save(con, 1, [{"code": CRAB, "qty": 500, "unit": "แพ็ค 1 กก."}], "udon", submit=True, count_date="2026-10-05")
    OR.save_flow(con, 1, "1", WED, [{"code": CRAB, "confirmed_units": 0}], "udon", now=NOW, as_of=AS_OF)   # a full shelf: nothing to order
    assert crab()["suggested_units"] == 0 and crab()["confirmed_units"] == 0           # no reason is demanded for the change
    slip(con)
    out = OR.save_flow(con, 1, "1", WED, [{"code": "V-007", "confirmed_units": 1, "reason_code": "event"}], "udon",
                       submit=True, now=NOW, as_of=AS_OF)
    assert out["status"] == "submitted" and out["needs_reason"] == []
    assert crab()["suggested_units"] == 0


def test_a_late_submit_needs_the_late_fee_acknowledged_and_is_flagged(con):  # noqa: F811
    slip(con, LATE)                                                                  # Monday's slip, confirmed at 15:00
    args = (con, 1, "1", WED, [{"code": "V-007", "confirmed_units": 2, "reason_code": "event"}], "udon")
    with pytest.raises(OR.LateSubmit) as e:
        OR.save_flow(*args, submit=True, now=LATE, as_of=AS_OF)
    assert e.value.cutoff_th == "วันจันทร์ 13:15" and e.value.cutoff_at == "2026-10-05T13:15:00+07:00"
    assert "ค่าปรับล่าช้า" in str(e.value) and "คลังสินค้าภายนอก" in str(e.value)
    con.rollback()
    out = OR.save_flow(*args, submit=True, ack_late=True, now=LATE, as_of=AS_OF)
    assert out["status"] == "submitted" and out["late"] == 1
    con.commit()
    q = HB.queue(con)[0]
    assert q["order_id"] == out["order_id"] and q["late"] is True and q["submitted_on_time"] is False
    assert q["delivery"] == "2026-10-07" and q["cutoff_at"] == "2026-10-05T13:15:00+07:00"
    assert HB.compliance(con, "2026-10-05", "2026-10-05", 1)["rows"][0]["late"] is True
    # reopened: back to a draft with its count, and no longer late
    OR.reopen(con, 1, out["order_id"], "udon")
    o = OR.detail(con, out["order_id"])
    assert o["status"] == "suggested" and o["late"] == 0
    # an in-time submit after the reopen is not late
    again = OR.save_flow(con, 1, "1", WED, [], "udon", submit=True, now=NOW, as_of=AS_OF)
    assert again["late"] == 0
    assert HB.queue(con)[0]["late"] is False and HB.queue(con)[0]["submitted_on_time"] is True


def test_reopen_and_cancel_leave_the_stock_counts_and_the_slip_alone(con):  # noqa: F811
    ST.save(con, 1, [{"code": CRAB, "qty": 3, "unit": "แพ็ค 1 กก."}], "udon", submit=True, count_date="2026-10-02")
    slip(con)
    a = OR.save_flow(con, 1, "1", WED, [{"code": "V-007", "confirmed_units": 2, "reason_code": "event"}], "udon",
                     submit=True, now=NOW, as_of=AS_OF)
    OR.reopen(con, 1, a["order_id"], "udon")
    OR.cancel(con, 1, a["order_id"], "udon")
    assert OR.detail(con, a["order_id"])["status"] == "cancelled"
    assert con.execute("SELECT COUNT(*) FROM bc_stock_count").fetchone()[0] == 1          # the month-end count, nothing else
    assert IS.status_of(con, 1, "2026-10-05")["status"] == "confirmed"                  # the slip is the branch's, not the order's


def test_legacy_spot_rows_are_ignored_by_every_month_end_reader(udon):  # noqa: F811
    c = udon
    cur = c.execute("INSERT INTO bc_stock_count(branch_id, count_date, kind, counted_by, status, submitted_at, created_at)"
                    " VALUES(1, '2026-09-01', 'spot', 'old', 'submitted', '2026-09-01T01:00:00+00:00', '2026-09-01T01:00:00+00:00')")
    c.execute("INSERT INTO bc_stock_count_line(count_id, ingredient_code, qty_kitchen, unit, qty_base) VALUES(?,?,?,?,?)",
              (cur.lastrowid, CRAB, 2, "แพ็ค 1 กก.", 2000.0))
    c.commit()
    # not the begin stock, not a period, not the system stock's basis, not the close step
    sh = ST.sheet(c, 1, AS_OF)
    assert sh["next_kind"] == "begin" and sh["draft"] is None and sh["last_count"] is None
    assert ST.periods(c, 1) == [] and ST.history(c, 1, kinds=ST.MONTH_KINDS) == []
    assert ST.system_position(c, 1, AS_OF)["basis"] is None
    assert [h["kind"] for h in ST.history(c, 1)] == ["spot"]                                # the row is still there to read
    st = {s["key"]: s for s in TD.build(c, "2026-09", None, None, today=date(2026, 10, 7))["close"]["steps"]}
    assert st["begin_count"]["status"] == "todo"
    # a month-end import for the same date is accepted, and is the begin
    r = ST.import_count(c, 1, "2026-09-01", [{"code": CRAB, "qty": 2, "unit": "แพ็ค 1 กก.", "note": None}], "u", dry_run=False)
    assert r["ok"] and r["kind"] == "begin"
    st = {s["key"]: s for s in TD.build(c, "2026-09", None, None, today=date(2026, 10, 7))["close"]["steps"]}
    assert st["begin_count"]["status"] == "done"
    with pytest.raises(TypeError):                                                         # the writer takes no kind any more
        ST.save(c, 1, [], "u", kind="spot")


def test_stock_position_is_the_system_stock_plus_what_is_on_order(con):  # noqa: F811
    x = ST.save(con, 1, [{"code": CRAB, "qty": 5, "unit": "แพ็ค 1 กก."}], "u", submit=True, count_date="2026-10-02")["count_id"]
    ST.save(con, 1, [{"code": CRAB, "qty": 1, "unit": "แพ็ค 1 กก."}], "u", count_date="2026-10-04")        # a draft is no basis
    con.commit()
    sp = OR.stock_position(con, 1, AS_OF, date(2026, 10, 5))
    assert sp["basis"] == {"count_id": x, "date": "2026-10-02"} and sp["qty"][CRAB] == 5000.0
    assert sp["through"] == "2026-10-05" and sp["on_order"] == {}
    assert sp["missing_days"] == ["2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"]


def test_sla_reads_the_stored_cutoff():
    cut = "2026-10-05T13:15:00+07:00"
    ok = HB.sla("2026-10-05T06:00:00+00:00", "2026-10-05T06:50:00+00:00", "2026-10-05", cut)        # 13:00 sent, 13:50 done
    assert ok["submitted_on_time"] is True and ok["late"] is False and ok["processed_on_time"] is True
    late = HB.sla("2026-10-05T06:30:00+00:00", None, "2026-10-05", cut)                             # 13:30 sent
    assert late["submitted_on_time"] is False and late["late"] is True
    assert HB.sla(None, None, "2026-10-05", cut)["late"] is False                                   # nothing sent yet


# ── หน้างาน ──────────────────────────────────────────────────────────────────
def _due(d):
    return {i["key"].split(":")[0]: i for i in d["items"] if i["key"].startswith("order_due_")}


def test_todo_names_the_delivery_and_the_cutoff_per_round(udon):  # noqa: F811
    c = udon
    d = _due(TD.build(c, "2026-09", None, None, today=date(2026, 10, 5), now=NOW))
    assert set(d) == {"order_due_1", "order_due_2", "order_due_salmon"}
    r1, r2, sal = d["order_due_1"], d["order_due_2"], d["order_due_salmon"]
    assert r1["title"] == "ส่งใบสั่งซื้อรอบส่งวันพุธ (7/10) ภายใน วันจันทร์ 13:15"
    assert sal["title"] == "ส่งใบสั่งซื้อแซลมอนสด รอบส่งวันจันทร์ (12/10) ภายใน วันเสาร์ 13:15"
    assert r1["seq"] == 310 and r1["owner_modules"] == ["branch.stock"] and r1["owner_th"] == "สาขา" and r1["branch_id"] == 1
    assert r1["go"] == {"tab": "branchstock", "pane": "order", "extra": {"round": "1", "delivery": "2026-10-07"}, "branch": 1}
    assert (r1["round_th"], r1["delivery"], r1["delivery_th"], r1["cutoff_th"], r1["cutoff_date"]) == \
        ("รอบ 1", "2026-10-07", "วันพุธ 7/10", "วันจันทร์ 13:15", "2026-10-05")
    assert r1["due_today"] is True and r1["late"] is False and r1["urgent"] is False and r1["status"] == "todo"
    assert r1["detail"] == "เหลือ 5 ชม. · ยังไม่ได้เปิดใบสั่งซื้อ · ต้องยืนยันใบตัดเบิกวันนี้ก่อน"
    assert r2["due_today"] is False and sal["due_today"] is False
    # under four hours left: urgent
    near = _due(TD.build(c, "2026-09", None, None, today=date(2026, 10, 5), now=at(5, 10)))["order_due_1"]
    assert near["urgent"] is True and near["late"] is False
    # past the cutoff and not sent: late, urgent, with the late-fee wording
    late = _due(TD.build(c, "2026-09", None, None, today=date(2026, 10, 5), now=LATE))["order_due_1"]
    assert late["late"] is True and late["urgent"] is True and late["status"] == "todo"
    assert late["detail"] == "เลยเวลาตัดรอบ มีค่าปรับล่าช้า หากยังต้องการสั่ง ให้ส่งใบสั่งซื้อโดยเร็ว · ต้องยืนยันใบตัดเบิกวันนี้ก่อน"
    # a draft order says so; a sent one is done and no longer urgent
    c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(1, '2026-10-05', '1', 'suggested')")
    assert _due(TD.build(c, "2026-09", None, None, today=date(2026, 10, 5), now=NOW))["order_due_1"]["detail"] \
        == "เหลือ 5 ชม. · สถานะ: ร่าง (ยังไม่ส่ง) · ต้องยืนยันใบตัดเบิกวันนี้ก่อน"
    slip(c)                                                                              # the slip is confirmed: the suffix goes
    assert _due(TD.build(c, "2026-09", None, None, today=date(2026, 10, 5), now=NOW))["order_due_1"]["detail"] \
        == "เหลือ 5 ชม. · สถานะ: ร่าง (ยังไม่ส่ง)"
    c.execute("UPDATE bc_order SET status='submitted', late=1")
    done = _due(TD.build(c, "2026-09", None, None, today=date(2026, 10, 5), now=LATE))["order_due_1"]
    assert done["status"] == "done" and done["urgent"] is False and done["detail"].startswith("สถานะ: ")
    q = {i["key"]: i for i in TD.build(c, "2026-09", None, None, today=date(2026, 10, 5), now=LATE)["items"]}["queue_pending"]
    assert q["detail"] == "รอ 1 ใบ ภายใน 14:00 ของวันตัดรอบ · ส่งล่าช้า 1 ใบ (มีค่าปรับ)"
    q2 = {i["key"]: i for i in TD.build(c, "2026-09", None, None, today=date(2026, 10, 6), now=at(6, 8))["items"]}["queue_pending"]
    assert q2["detail"].endswith("· มีใบที่เลยวันตัดรอบแล้ว") and q2["urgent"] is True


def test_the_receive_task_uses_the_stored_delivery(udon):  # noqa: F811
    c = udon
    c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, delivery_date) VALUES"
              "(1, '2026-10-05', '1', 'processed', '2026-10-07')")
    items = {i["key"].split(":")[0]: i for i in TD.build(c, "2026-09", None, None, today=date(2026, 10, 7), now=NOW)["items"]}
    assert items["receive_pending"]["detail"].endswith("(รอบ 1 ส่ง 2026-10-07)")


# ── the HTTP layer ───────────────────────────────────────────────────────────
def test_the_endpoints_calendar_plan_save_late_and_exports(client, monkeypatch):
    import main
    from routers import bom_center as RB
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "t")
    hdr = {"X-Upload-Token": "t"}
    monkeypatch.setattr(F, "now_bkk", lambda: LATE)                    # the server's clock: Monday 15:00, past round 1's cutoff
    monkeypatch.setattr(IS, "now_bkk", lambda: LATE)
    monkeypatch.setattr(RB, "bkk_today", lambda: date(2026, 10, 5))
    client.get("/api/bom-center/hub/queue")                            # creates the bc_* tables
    snap = _rows(main.DB_PATH)
    c = sqlite3.connect(str(main.DB_PATH))
    bid = c.execute("SELECT branch_id FROM branches WHERE name<>'ALL' ORDER BY branch_id LIMIT 1").fetchone()[0]
    c.execute("DELETE FROM bc_order_calendar")
    c.execute("INSERT OR REPLACE INTO bc_ingredient(code, name_th, kind, base_unit, recv_unit, qty_per_recv, supplier, active)"
              " VALUES('X-1', 'ของ ก', 'raw', 'กรัม', 'แพ็ค', 1000, 'ผู้จำหน่าย ก', 1)")
    c.commit()
    c.close()
    try:
        # the calendar: only the delivery weekday is set, the cutoff follows
        r = client.post("/api/bom-center/order-calendar", headers=hdr, json=[{"branch_id": bid, "round": "2", "delivery_weekday": 2}])
        assert r.status_code == 200, r.text
        row = next(x for x in client.get("/api/bom-center/order-calendar").json()["rows"] if x["branch_id"] == bid)
        assert row["r2"]["delivery_weekday"] == 2 and row["r2"]["order_weekday"] == 6 and row["r2"]["lead_days"] == 3
        assert row["r2"]["cutoff_th"] == "วันเสาร์ 13:15"
        bad = client.post("/api/bom-center/order-calendar", headers=hdr, json=[{"branch_id": bid, "round": "2"}])
        assert bad.status_code == 400 and "วันส่ง" in bad.json()["detail"]
        assert client.post("/api/bom-center/order-calendar", headers=hdr,
                           json=[{"branch_id": bid, "round": "2", "delivery_weekday": 9}]).status_code == 400
        c = sqlite3.connect(str(main.DB_PATH))
        c.execute("DELETE FROM bc_order_calendar")                     # back to the defaults for the rest
        c.commit()
        c.close()
        # the plan by delivery
        p = client.get(f"/api/bom-center/order/plan?branch_id={bid}&round=1&delivery=2026-10-07")
        assert p.status_code == 200, p.text
        j = p.json()
        assert j["round"] == "1" and j["delivery"] == "2026-10-07" and j["cycle_date"] == "2026-10-05"
        assert j["cutoff_th"] == "วันจันทร์ 13:15" and j["late"] is True and j["deliveries"]
        assert client.get(f"/api/bom-center/order/plan?branch_id={bid}&round=1&delivery=2026-10-08").status_code == 400
        # no delivery named: the nearest one still open (round 1's is past its cutoff now)
        assert client.get(f"/api/bom-center/order/plan?branch_id={bid}").json()["round"] == "2"
        # a late submit: 400 with a structured detail, then 200 once the late fee is acknowledged
        body = {"round": "1", "delivery": "2026-10-07", "submit": True,
                "lines": [{"code": "X-1", "confirmed_units": 1, "reason_code": "event"}],
                "counts": [{"code": "X-1", "qty": 0, "unit": "กรัม"}]}       # an old client still sends `counts`: ignored
        r = client.post(f"/api/bom-center/order/save?branch_id={bid}", headers=hdr, json=body)
        assert r.status_code == 400 and "ต้องยืนยันใบตัดเบิกของวันนี้" in r.json()["detail"], r.text   # no slip yet
        # the branch confirms today's slip through the API, then orders
        sl = client.post(f"/api/bom-center/issues/save?branch_id={bid}", headers=hdr,
                         json={"date": "2026-10-05", "lines": [{"code": "X-1", "qty_used": 5, "unit": "กรัม"}], "submit": True})
        assert sl.status_code == 200 and sl.json()["status"] == "confirmed" and sl.json()["late"] == 0, sl.text
        r = client.post(f"/api/bom-center/order/save?branch_id={bid}", headers=hdr, json=body)
        assert r.status_code == 400, r.text
        d = r.json()["detail"]
        assert d["code"] == "late" and d["cutoff_th"] == "วันจันทร์ 13:15" and "ค่าปรับล่าช้า" in d["message"]
        assert d["cutoff_at"] == "2026-10-05T13:15:00+07:00"
        n = sqlite3.connect(str(main.DB_PATH))
        assert n.execute("SELECT COUNT(*) FROM bc_order").fetchone()[0] == 0                 # nothing was kept
        assert n.execute("SELECT COUNT(*) FROM bc_stock_count").fetchone()[0] == 0          # no count is written by ordering
        assert n.execute("SELECT COUNT(*) FROM bc_issue_slip WHERE status='confirmed'").fetchone()[0] == 1   # the slip stays
        n.close()
        r = client.post(f"/api/bom-center/order/save?branch_id={bid}", headers=hdr, json=dict(body, ack_late=True))
        assert r.status_code == 200, r.text
        assert r.json()["late"] == 1 and r.json()["status"] == "submitted" and r.json()["slip_today"]["status"] == "confirmed"
        # the queue and the exports show it
        qj = client.get("/api/bom-center/hub/queue?status=submitted").json()
        assert qj["sla"] == {"cutoff_time": "13:15", "process_by": 14}
        assert qj["orders"][0]["late"] is True and qj["orders"][0]["delivery"] == "2026-10-07"
        x = client.get("/api/bom-center/hub/consolidated.xlsx?status=submitted")
        ws = load_workbook(io.BytesIO(x.content), read_only=True)["ต่อสาขา"]
        rows = list(ws.iter_rows(values_only=True))
        head = next(rw for rw in rows if rw and rw[0] == "สาขา")
        assert list(head) == ["สาขา", "วันส่ง", "วันตัดรอบ", "รอบ", "รหัส", "วัตถุดิบ", "จำนวน", "หน่วย", "PO", "ใบสั่งซื้อ", "ล่าช้า", "คีย์บัญชี"]
        line = rows[rows.index(head) + 1]
        assert line[1] == "2026-10-07" and line[2] == "2026-10-05" and line[10] == "ล่าช้า (ค่าปรับ)" and line[-1] == "ยังไม่คีย์"
        x = client.get("/api/bom-center/hub/compliance.xlsx?date_from=2026-10-05&date_to=2026-10-05")
        rows = list(load_workbook(io.BytesIO(x.content), read_only=True).worksheets[0].iter_rows(values_only=True))
        head = next(rw for rw in rows if rw and rw[0] == "วันตัดรอบ")
        assert head[-1] == "ล่าช้า" and any(rw and rw[-1] == "ล่าช้า (ค่าปรับ)" for rw in rows)
        # ordering leaves no stock count behind (the month-end history can still be narrowed to the month counts)
        assert client.get(f"/api/bom-center/stock/counts?branch_id={bid}").json()["counts"] == []
        assert client.get(f"/api/bom-center/stock/counts?branch_id={bid}&kind=month").json()["counts"] == []
    finally:
        _restore(main.DB_PATH, snap)
