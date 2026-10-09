# -*- coding: utf-8 -*-
"""PR-J1 — ใบตัดเบิก: the branch's daily issue slip and the system stock as the order basis.

The promises:
  * a slip is one day, one branch: used (เบิกใช้) and wasted (ของเสีย, with a reason) per item, saved
    as a draft and CONFIRMED once; a slip for a day that is over is accepted but marked late, on the
    FIRST confirm only; none for a future day, none older than 60 days, none before the latest
    month-end count;
  * a confirmed slip is locked; Sales Support reopens it with a reason, the branch edits and confirms
    again, and every create / confirm / reopen / reconfirm is on the record;
  * the system stock = the latest month-end count + goods received − CONFIRMED slips (the count date
    included on both sides), counted items only; a draft slip counts for nothing;
  * the order suggestion is net of that stock, an order can be submitted only with today's slip
    confirmed (an earlier gap only warns), and ordering writes no stock count;
  * the usage history behind a plan is computed once per process (cached), and the plan is computed
    BEFORE the order's write transaction begins (the lock found by the 30-branch load test);
  * หน้างาน, the reminders and the pilot scorecard follow the slips.
"""
import sqlite3
from datetime import date, datetime, timedelta

import pytest

import auth
from bom_center import forecast as F
from bom_center import issues as IS
from bom_center import orders as OR
from bom_center import pilot as PL
from bom_center import reminders as RM
from bom_center import stock as ST
from bom_center import todo as TD
from bom_center import variance as VA
from bom_center.clock import BKK
from test_bom_center_export import _restore, _rows
from test_bom_center_s11 import udon  # noqa: F401  (branch 1 = อุดรธานี)
from test_bom_center_s4b import con  # noqa: F401  (sales to 5 Oct 2026 at branch 1; crab stick 1 kg packs)

CRAB = "13-01-009"
PACK = "แพ็ค 1 กก."
AS_OF = date(2026, 10, 5)
WED = date(2026, 10, 7)


def at(d, h=21, m=0, month=10):
    return datetime(2026, month, d, h, m, tzinfo=BKK)


NOW = at(5, 8)                                       # Monday 08:00


def used(code=CRAB, qty=1000, unit="กรัม", **kw):
    return {"code": code, "qty_used": qty, "unit": unit, **kw}


def slip(con, day, lines, submit=True, now=None):
    d = date.fromisoformat(day)
    return IS.save(con, 1, day, lines, "udon", submit=submit, now=now or at(d.day, 21, month=d.month))


def build_stock(con):
    """10 packs counted 1 Oct, 5 received 2 Oct; slips: 1 Oct used 1000 g, 2 Oct used 600 g, 3 Oct wasted 700 g,
    4 Oct a DRAFT using 5 packs. System stock on 5 Oct = 10000 + 5000 - 1000 - 600 - 700 = 12700 g."""
    cid = ST.save(con, 1, [{"code": CRAB, "qty": 10, "unit": PACK}], "udon", submit=True, count_date="2026-10-01")["count_id"]
    VA.add_receipt(con, 1, "2026-10-02", [{"code": CRAB, "qty": 5, "unit": PACK}], "udon")
    slip(con, "2026-10-01", [used(qty=1000)])
    slip(con, "2026-10-02", [used(qty=600)])
    slip(con, "2026-10-03", [{"code": CRAB, "qty_waste": 700, "unit": "กรัม", "waste_reason": "spill"}])
    slip(con, "2026-10-04", [used(qty=5, unit=PACK)], submit=False)
    con.commit()
    return cid


# ── 1 · the sheet ────────────────────────────────────────────────────────────
def test_the_sheet_shows_the_system_stock_before_the_day_the_average_and_the_reasons(con):  # noqa: F811
    cid = build_stock(con)
    sh = IS.sheet(con, 1, "2026-10-04", now=NOW)
    crab = next(i for z in sh["zones"] for i in z["items"] if i["code"] == CRAB)
    assert crab["count_unit"] == PACK and crab["per_count_unit"] == 1000
    assert crab["system_qty"] == pytest.approx(12.7)                       # 13400 - 1000 - 600 - 700 … before 4 Oct's own issue
    assert crab["avg_used_7d"] == pytest.approx((1000 + 600 + 0) / 3 / 1000)   # three confirmed days, in packs
    assert crab["used"] == 5 and crab["waste"] == 0                         # the draft's own line, back in the count unit
    assert sh["date"] == "2026-10-04" and sh["status"]["status"] == "draft" and sh["status_th"] == "ร่าง"
    assert sh["editable"] is True and sh["backdated"] is True               # today (NOW) is 5 Oct
    assert sh["basis"] == {"count_id": cid, "date": "2026-10-01"} and sh["missing_days"] == []
    assert [r["code"] for r in sh["reasons"]] == ["waste", "expired", "spill", "prep_loss", "other"]
    assert sh["reasons"][0]["label"] == "ของเสีย / เน่าเสีย"
    done = IS.sheet(con, 1, "2026-10-03", now=NOW)                          # a confirmed day reads back converted, locked
    c3 = next(i for z in done["zones"] for i in z["items"] if i["code"] == CRAB)
    assert done["editable"] is False and done["status"]["status"] == "confirmed"
    assert c3["waste"] == pytest.approx(0.7) and c3["waste_reason"] == "spill" and c3["used"] == 0
    empty = IS.sheet(con, 1, "2026-10-05", now=NOW)
    assert empty["status"]["status"] == "missing" and empty["status_th"] == "ยังไม่บันทึก" and empty["backdated"] is False
    assert [m for m in empty["missing_days"]] == ["2026-10-04"]             # the draft day is still open
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        IS.sheet(con, 1, "5/10/2026")


# ── 2 · draft → confirm, and late ────────────────────────────────────────────
def test_a_draft_is_confirmed_once_and_a_slip_for_a_past_day_is_late(con):  # noqa: F811
    d = IS.save(con, 1, "2026-10-05", [used()], "udon", now=NOW)
    assert d["status"] == "draft" and d["lines"] == 1 and d["late"] == 0 and d["slip"]["status"] == "draft"
    assert not IS.is_confirmed(d["slip"])
    ok = IS.save(con, 1, "2026-10-05", [], "udon", submit=True, note="ครบ", now=at(5, 20))
    assert ok["status"] == "confirmed" and ok["late"] == 0 and ok["confirm_count"] == 1
    st = IS.status_of(con, 1, "2026-10-05")
    assert st["status"] == "confirmed" and st["confirmed_by"] == "udon" and st["lines"] == 1 and IS.is_confirmed(st)
    assert st["confirmed_at"] == "2026-10-05T13:00:00+00:00"                # stored in UTC, like an order's submitted_at
    # yesterday's slip done today: confirmed, but marked late
    back = IS.save(con, 1, "2026-10-03", [used()], "udon", submit=True, now=NOW)
    assert back["late"] == 1 and back["slip"]["status"] == "late" and IS.is_confirmed(back["slip"])
    assert IS.STATUS_TH["late"] == "ยืนยันล่าช้า"
    # every day of a range gets a verdict, a day without a slip is "missing"
    days = IS.status_days(con, 1, "2026-10-03", "2026-10-06")
    assert [(x["date"], x["status"]) for x in days] == [("2026-10-03", "late"), ("2026-10-04", "missing"),
                                                       ("2026-10-05", "confirmed"), ("2026-10-06", "missing")]
    assert IS.missing_days(con, 1, "2026-10-03", "2026-10-06") == ["2026-10-04", "2026-10-06"]
    assert IS.missing_days(con, 1, "2026-10-06", "2026-10-03") == []
    # the saved line round-trips in its own unit and the base unit
    d3 = IS.detail(con, back["slip_id"])
    assert d3["lines"][0]["qty_used_base"] == 1000 and d3["status_th"] == "ยืนยันล่าช้า" and d3["date"] == "2026-10-03"


# ── 3 · validations ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("day, lines, submit, msg", [
    ("5/10/2026", [], False, "วันที่ใบตัดเบิกต้องอยู่ในรูปแบบ YYYY-MM-DD"),
    ("2026-10-06", [], False, "ใบตัดเบิกต้องไม่เป็นวันล่วงหน้า"),
    ("2026-08-01", [], False, "บันทึกใบตัดเบิกย้อนหลังได้ไม่เกิน 60 วัน"),
    ("2026-10-05", [used(qty="abc")], False, f"{CRAB}: จำนวนต้องเป็นตัวเลข"),
    ("2026-10-05", [used(qty=-1)], False, f"{CRAB}: จำนวนต้องไม่ติดลบ"),
    ("2026-10-05", [{"code": CRAB, "qty_waste": 1, "unit": "กรัม"}], False, f"{CRAB}: กรุณาเลือกสาเหตุของของเสีย"),
    ("2026-10-05", [{"code": CRAB, "qty_waste": 1, "unit": "กรัม", "waste_reason": "zzz"}], False, f"{CRAB}: กรุณาเลือกสาเหตุของของเสีย"),
    ("2026-10-05", [{"code": CRAB, "qty_waste": 1, "unit": "กรัม", "waste_reason": "other"}], False, f"{CRAB}: สาเหตุ “อื่น ๆ” ต้องระบุคำอธิบาย"),
    ("2026-10-05", [], True, "ยังไม่มีรายการในใบตัดเบิก"),
    ("2026-10-05", [used(qty=1, unit="ช้อนโต๊ะ")], True, "แปลงหน่วยไม่ได้"),
])
def test_validations(con, day, lines, submit, msg):  # noqa: F811
    with pytest.raises(ValueError, match=msg):
        IS.save(con, 1, day, lines, "udon", submit=submit, now=NOW)
    con.rollback()
    assert con.execute("SELECT COUNT(*) FROM bc_issue_line").fetchone()[0] == 0


def test_a_slip_cannot_pre_date_the_month_end_count_and_a_confirmed_one_is_locked(con):  # noqa: F811
    ST.save(con, 1, [{"code": CRAB, "qty": 1, "unit": PACK}], "udon", submit=True, count_date="2026-10-02")
    con.commit()
    with pytest.raises(ValueError, match=r"บันทึกใบตัดเบิกย้อนหลังได้ไม่เกินวันตรวจนับสิ้นเดือนล่าสุด \(2026-10-02\)"):
        IS.save(con, 1, "2026-10-01", [used()], "udon", now=NOW)
    con.rollback()
    assert IS.save(con, 1, "2026-10-02", [used()], "udon", submit=True, now=NOW)["status"] == "confirmed"   # the count date itself is allowed
    with pytest.raises(ValueError, match="ใบตัดเบิกวันที่ 2026-10-02 ยืนยันแล้ว หากต้องแก้ไข กรุณาแจ้งประสานงานขายให้เปิดแก้ไข"):
        IS.save(con, 1, "2026-10-02", [used(qty=1)], "udon", now=NOW)
    # both quantities zero removes a line; a waste line without a reason is refused only when it has waste
    d = IS.save(con, 1, "2026-10-04", [used(), used("V-007", 5)], "udon", now=NOW)
    assert d["lines"] == 2
    assert IS.save(con, 1, "2026-10-04", [used(qty=0)], "udon", now=NOW)["lines"] == 1
    assert IS.save(con, 1, "2026-10-04", [used("V-007", 0, note="x")], "udon", now=NOW)["lines"] == 0


# ── 4 · lock → reopen → reconfirm, and the log ───────────────────────────────
def test_reopen_reconfirm_and_the_log(con):  # noqa: F811
    s = IS.save(con, 1, "2026-10-03", [used()], "udon", submit=True, now=NOW)         # late (5 Oct)
    sid = s["slip_id"]
    for who_msg, args in (("ไม่พบใบตัดเบิก", (9999, "ss", "x")),
                          ("กรุณาระบุเหตุผลในการเปิดแก้ไขใบตัดเบิก", (sid, "ss", "  "))):
        with pytest.raises(ValueError, match=who_msg):
            IS.reopen(con, *args, now=NOW)
    r = IS.reopen(con, sid, "ss", "สาขาคีย์ปริมาณผิด", now=at(6, 9))
    assert r == {"slip_id": sid, "branch_id": 1, "date": "2026-10-03", "status": "draft"}
    with pytest.raises(ValueError, match="เปิดแก้ไขได้เฉพาะใบตัดเบิกที่ยืนยันแล้ว"):
        IS.reopen(con, sid, "ss", "อีกครั้ง", now=NOW)
    row = IS.detail(con, sid)
    assert row["status"] == "draft" and row["late"] == 1 and row["confirm_count"] == 1       # `late` and the count stay
    assert row["reopened_by"] == "ss" and row["reopen_note"] == "สาขาคีย์ปริมาณผิด"
    IS.save(con, 1, "2026-10-03", [used(qty=1500)], "udon", now=at(6, 10))                    # the first save after a reopen is logged …
    IS.save(con, 1, "2026-10-03", [used(qty=1600)], "udon", now=at(6, 10, 5))                 # … a second one is not
    again = IS.save(con, 1, "2026-10-03", [], "udon", submit=True, now=at(6, 11))
    assert again["status"] == "confirmed" and again["late"] == 1 and again["confirm_count"] == 2
    d = IS.detail(con, sid)
    assert [x["action"] for x in d["log"]] == ["create", "confirm", "reopen", "save", "reconfirm"]
    assert [x["late"] for x in d["log"]] == [0, 1, 1, 1, 1]
    assert d["log"][2]["note"] == "สาขาคีย์ปริมาณผิด" and d["log"][2]["who"] == "ss"
    assert d["lines"][0]["qty_used"] == 1600
    acts = [a for (a,) in con.execute("SELECT action FROM bc_audit WHERE table_name='bc_issue_slip' ORDER BY id")]
    assert acts == ["issue_confirm", "issue_reopen", "issue_reconfirm"]
    # a slip confirmed on its own day stays on time through a reopen and a later reconfirm
    s2 = IS.save(con, 1, "2026-10-05", [used()], "udon", submit=True, now=at(5, 20))
    IS.reopen(con, s2["slip_id"], "ss", "แก้ไข", now=at(7, 9))
    assert IS.save(con, 1, "2026-10-05", [], "udon", submit=True, now=at(7, 10))["late"] == 0
    # listing: by what each slip shows
    lst = IS.listing(con, 1, "2026-10-01", "2026-10-06")
    assert [(x["date"], x["status"]) for x in lst] == [("2026-10-05", "confirmed"), ("2026-10-03", "late")]
    assert [x["date"] for x in IS.listing(con, None, "2026-10-01", "2026-10-06", "late")] == ["2026-10-03"]
    assert IS.listing(con, 1, "2026-10-01", "2026-10-06", "draft") == []
    with pytest.raises(ValueError):
        IS.listing(con, 1, status="zzz")


# ── 5 · the system stock ─────────────────────────────────────────────────────
def test_system_position_is_count_plus_receipts_minus_confirmed_slips(con):  # noqa: F811
    cid = build_stock(con)
    sp = ST.system_position(con, 1, AS_OF)
    assert sp["basis"] == {"count_id": cid, "date": "2026-10-01"}
    assert sp["qty"][CRAB] == 12700                                       # 10000 + 5000 - 1000 - 600 - 700; the draft is ignored
    assert sp["through"] == "2026-10-05" and sp["missing_days"] == ["2026-10-04", "2026-10-05"]
    # the count date's own receipts and slip are in; a later issue day is not
    assert ST.system_position(con, 1, date(2026, 10, 2), issues_through=date(2026, 10, 1))["qty"][CRAB] == 14000
    assert ST.system_position(con, 1, date(2026, 10, 2))["qty"][CRAB] == 13400
    # an item that was not counted has no figure; before the count there is no basis at all
    VA.add_receipt(con, 1, "2026-10-02", [{"code": "02-01-001", "qty": 1, "unit": "กรัม"}], "udon")
    assert "02-01-001" not in ST.system_position(con, 1, AS_OF)["qty"]
    assert ST.system_position(con, 1, date(2026, 9, 30)) == {"basis": None, "qty": {}, "missing_days": [], "through": "2026-09-30"}
    # a slip reopened is a draft again and stops counting
    IS.reopen(con, IS.status_of(con, 1, "2026-10-02")["slip_id"], "ss", "แก้ไข", now=NOW)
    assert ST.system_position(con, 1, AS_OF)["qty"][CRAB] == 13300


# ── 6 · the plan ─────────────────────────────────────────────────────────────
def test_the_plan_deducts_the_system_stock_after_todays_slip(con):  # noqa: F811
    ST.save(con, 1, [{"code": CRAB, "qty": 3, "unit": PACK}], "udon", submit=True, count_date="2026-10-05")
    con.commit()
    p = OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    r = next(x for x in p["rows"] if x["code"] == CRAB)
    assert r["on_hand"] == 3000 and r["system_qty"] == 3 and r["system_unit"] == PACK
    assert "count" not in p and not ({"counted", "count_qty"} & set(r))
    assert p["stock"]["slip_today"]["status"] == "missing" and p["stock"]["missing_days"] == []
    slip(con, "2026-10-05", [used(qty=1000)], now=NOW)
    con.commit()
    p2 = OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    r2 = next(x for x in p2["rows"] if x["code"] == CRAB)
    assert r2["on_hand"] == 2000 and r2["system_qty"] == 2 and p2["stock"]["slip_today"]["status"] == "confirmed"
    assert r2["need"] >= r["need"]                                         # less on the shelf, never less to order
    # the stock date is the order day (now), not the last day of sales: tomorrow's plan also takes tomorrow's issue
    nxt = OR.plan(con, 1, "1", WED, as_of=AS_OF, now=at(6, 8))
    assert nxt["stock"]["through"] == "2026-10-06" and nxt["stock"]["missing_days"] == []   # (5 Oct is confirmed)


# ── 7 · submit needs today's slip ────────────────────────────────────────────
def test_submit_needs_todays_confirmed_slip_no_count_is_written_and_a_gap_only_warns(con):  # noqa: F811
    ST.save(con, 1, [{"code": CRAB, "qty": 3, "unit": PACK}], "udon", submit=True, count_date="2026-10-02")
    con.commit()
    line = [{"code": "V-007", "confirmed_units": 2, "reason_code": "event"}]
    n_counts = con.execute("SELECT COUNT(*) FROM bc_stock_count").fetchone()[0]
    OR.save_flow(con, 1, "1", WED, line, "udon", now=NOW, as_of=AS_OF)
    con.commit()
    p = OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    assert p["stock"]["missing_days"] == ["2026-10-02", "2026-10-03", "2026-10-04"]      # earlier days: a warning
    crab = next(r for r in p["rows"] if r["code"] == CRAB)["order_units"]
    line = line + [{"code": CRAB, "confirmed_units": crab}]
    with pytest.raises(ValueError, match="ต้องยืนยันใบตัดเบิกของวันนี้ก่อนส่งใบสั่งซื้อ"):
        OR.save_flow(con, 1, "1", WED, line, "udon", submit=True, now=NOW, as_of=AS_OF)
    con.rollback()
    slip(con, "2026-10-05", [used("V-007", 10)], now=NOW)
    con.commit()
    ok = OR.save_flow(con, 1, "1", WED, line, "udon", submit=True, now=NOW, as_of=AS_OF)
    assert ok["status"] == "submitted" and ok["slip_today"]["status"] == "confirmed"
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM bc_stock_count").fetchone()[0] == n_counts     # ordering wrote no count
    acts = [r[0] for r in con.execute("SELECT new_value FROM bc_audit WHERE action='order_submit'")]
    assert len(acts) == 1 and f"'slip_id': {IS.status_of(con, 1, '2026-10-05')['slip_id']}" in acts[0]


# ── 8 · the usage-history cache ──────────────────────────────────────────────
def test_the_usage_history_is_computed_once_per_process_until_the_cache_generation_moves(con, monkeypatch):  # noqa: F811
    import core
    calls = []
    real = F._usage_history_uncached
    monkeypatch.setattr(F, "_usage_history_uncached", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    monkeypatch.setattr(F, "USAGE_CACHE_TTL", 600)
    F.clear_usage_cache()
    a = OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    b = OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    assert len(calls) == 1 and a["rows"] == b["rows"]                    # two plans, one walk over the sales
    OR.plan(con, 1, "2", date(2026, 10, 10), as_of=AS_OF, now=NOW)       # same window, other round: still the cached walk
    assert len(calls) == 1
    use1, days1, stats1 = F.usage_history(con, 1, date(2026, 9, 1), AS_OF, "2026-10")
    days1.append("x")
    stats1.pop("release_skus", None)                                     # callers pop from what they get …
    _u, days2, stats2 = F.usage_history(con, 1, date(2026, 9, 1), AS_OF, "2026-10")
    assert "x" not in days2 and "release_skus" in stats2                 # … without spoiling the cached copy
    n = len(calls)
    # a PREFIXED invalidate_cache (what every order / slip / claim save calls at the cutoff hour) leaves it alone …
    core.invalidate_cache("bc_")
    OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    assert len(calls) == n
    # … a FULL flush (sales / cost / bom data changed) and a data swap start afresh
    monkeypatch.setattr(core, "_build_sku_effective_cost", lambda *a, **k: None)     # (not the point here)
    core.invalidate_cache()
    OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    assert len(calls) == n + 1
    OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    assert len(calls) == n + 1
    monkeypatch.setattr(core, "_db_gen", core._db_gen + 1)
    OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    assert len(calls) == n + 2
    # a recipe-side write clears it explicitly (the router calls this after release / recipes / prep / links / units …)
    OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    assert len(calls) == n + 2
    F.clear_usage_cache()
    OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    assert len(calls) == n + 3
    monkeypatch.setattr(F, "USAGE_CACHE_TTL", 0)                         # switched off (the tests' default): every call recomputes
    OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    assert len(calls) == n + 5


def test_the_router_clears_the_usage_cache_after_every_recipe_side_write():
    import inspect
    from routers import bom_center as RB
    for fn in (RB.bc_release_do, RB.bc_release_retire, RB.bc_release_new_draft, RB.bc_release_submit_review,
               RB.bc_release_send_back, RB.bc_recipe_save, RB.bc_recipes_map, RB.bc_recipes_import_flat,
               RB.bc_recipes_import_cost_rw, RB.bc_code_review_decide, RB.bc_units_save, RB.bc_ingredients_save,
               RB.bc_ingredients_seed, RB.bc_ingredients_import):
        assert "clear_usage_cache()" in inspect.getsource(fn), fn.__name__
    for fn in (RB.bc_order_save, RB.bc_issue_save, RB.bc_order_receive):                # the rush: never a flush of the usage cache
        assert "clear_usage_cache" not in inspect.getsource(fn), fn.__name__


def test_plans_share_a_few_cpu_slots(con, monkeypatch):  # noqa: F811
    import threading
    assert isinstance(F._plan_slots, type(threading.BoundedSemaphore()))
    assert F.PLAN_CONCURRENCY >= 1 and F._plan_slots._value == F.PLAN_CONCURRENCY
    taken = []

    class Spy:
        def __enter__(self):
            taken.append("in")

        def __exit__(self, *a):
            taken.append("out")
    monkeypatch.setattr(F, "_plan_slots", Spy())
    OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    assert taken == ["in", "out"]                                        # only the suggest() computation is inside the slot


def test_the_cache_is_on_by_default_and_off_in_the_tests():
    import os
    import subprocess
    import sys
    assert F.USAGE_CACHE_TTL == 0                                        # conftest's autouse fixture
    here = os.path.dirname(os.path.abspath(__file__))
    code = "import sys; sys.path.insert(0, '.'); from bom_center import forecast; print(forecast.USAGE_CACHE_TTL)"
    env = {k: v for k, v in os.environ.items() if k != "OSHINEI_USAGE_CACHE_TTL"}
    assert subprocess.run([sys.executable, "-c", code], cwd=here, env=env, capture_output=True,
                          text=True).stdout.strip() == "600.0"           # on by default: ten minutes
    assert subprocess.run([sys.executable, "-c", code], cwd=here, env=dict(env, OSHINEI_USAGE_CACHE_TTL="30"),
                          capture_output=True, text=True).stdout.strip() == "30.0"


# ── 9 · the write transaction starts inside save() ───────────────────────────
def test_the_write_transaction_starts_only_inside_save_after_the_plan(con, monkeypatch):  # noqa: F811
    con.commit()
    seen, order = {}, []
    real_plan, real_save = OR.plan, OR.save
    monkeypatch.setattr(OR, "plan", lambda *a, **k: (order.append("plan"), real_plan(*a, **k))[1])

    def spy(c, *a, **k):
        order.append("save")
        seen["in_tx"] = c.in_transaction
        return real_save(c, *a, **k)
    monkeypatch.setattr(OR, "save", spy)
    OR.save_flow(con, 1, "1", WED, [], "udon", now=NOW, as_of=AS_OF)
    assert order == ["plan", "save"] and seen["in_tx"] is False             # nothing written (or locked) before save()
    assert con.in_transaction                                              # save() itself did write
    con.rollback()


# ── 10 · หน้างาน and the reminders ───────────────────────────────────────────
def _issue_items(d):
    return {i["key"].split(":")[0]: i for i in d["items"]}


def test_todo_and_reminders_follow_the_slip(udon):  # noqa: F811
    c = udon
    build = lambda branch_id=None: TD.build(c, "2026-09", branch_id, None, today=date(2026, 10, 5), now=NOW)  # noqa: E731
    # a branch that has never counted nor made a slip has not started: no chase for yesterday
    it = _issue_items(build(branch_id=1))
    assert it["issue_today"]["started"] is False and not [k for k in it if k.startswith("issue_missing_")]
    assert "ใบตัดเบิกเมื่อวาน" not in RM.branch_line(build(branch_id=1)["items"])["text"]
    ST.save(c, 1, [{"code": CRAB, "qty": 3, "unit": PACK}], "udon", submit=True, count_date="2026-10-02")
    c.commit()
    d = build(branch_id=1)
    it = _issue_items(d)
    t = it["issue_today"]
    assert t["title"] == "ยืนยันใบตัดเบิกประจำวันที่ 5/10" and t["seq"] == 305 and t["status"] == "todo"
    assert t["detail"] == "ยังไม่บันทึก · ต้องยืนยันก่อนส่งใบสั่งซื้อของวันนี้"
    assert (t["slip_status"], t["yesterday"], t["started"]) == ("missing", "missing", True)
    assert t["owner_modules"] == ["branch.stock"] and t["go"] == {"tab": "branchstock", "pane": "issue",
                                                                  "extra": {"date": "2026-10-05"}, "branch": 1}
    gaps = sorted(i["slip_date"] for i in d["items"] if i["key"].startswith("issue_missing_"))
    assert gaps == ["2026-10-02", "2026-10-03", "2026-10-04"]
    g = next(i for i in d["items"] if i["key"].startswith("issue_missing_2026-10-04"))
    assert g["seq"] == 306 and g["urgent"] is True and g["status"] == "todo" and g["go"]["extra"] == {"date": "2026-10-04"}
    assert it["order_due_1"]["detail"].endswith(" · ต้องยืนยันใบตัดเบิกวันนี้ก่อน")
    line = RM.branch_line(d["items"])
    assert "ใบตัดเบิกเมื่อวาน: ยังไม่ยืนยัน (ล่าช้า)" in line["text"] and line["pending"] is True
    # a draft, then a confirmed slip today; yesterday's confirmed too
    IS.save(c, 1, "2026-10-05", [used()], "udon", now=NOW)
    assert _issue_items(build(branch_id=1))["issue_today"]["detail"] == "มีร่างแล้ว ยังไม่ยืนยัน · ต้องยืนยันก่อนส่งใบสั่งซื้อของวันนี้"
    IS.save(c, 1, "2026-10-05", [], "udon", submit=True, now=NOW)
    IS.save(c, 1, "2026-10-04", [used()], "udon", submit=True, now=NOW)
    d = build(branch_id=1)
    it = _issue_items(d)
    assert it["issue_today"]["status"] == "done" and it["issue_today"]["detail"] == "ยืนยันแล้ว เวลา 08:00 โดย udon"
    assert it["issue_today"]["yesterday"] == "late" and "issue_missing_2026-10-04" not in it
    assert not it["order_due_1"]["detail"].endswith("ต้องยืนยันใบตัดเบิกวันนี้ก่อน")
    assert "ใบตัดเบิกเมื่อวาน: ยืนยันแล้ว" in RM.branch_line(d["items"])["text"]
    # the centre follows up the branches that did not confirm yesterday's slip
    IS.reopen(c, IS.status_of(c, 1, "2026-10-04")["slip_id"], "ss", "แก้ไข", now=NOW)
    cen = _issue_items(build())["issue_missing"]
    assert cen["seq"] == 228 and cen["title"] == "ติดตามสาขาที่ยังไม่ยืนยันใบตัดเบิกของเมื่อวาน" and cen["status"] == "todo"
    assert cen["owner_modules"] == ["supply.ordering", "ops.report"] and cen["owner_th"] == "ประสานงานขาย (Sales Support)"
    assert cen["count"] == 1 and "อุดรธานี" in cen["detail"] and cen["urgent"] is True
    morning = RM.morning(c, today=date(2026, 10, 5))
    assert any("ใบตัดเบิกเมื่อวาน: ยังไม่ยืนยัน (ล่าช้า)" in b["text"] for b in morning["branches"]) \
        and "ใบตัดเบิกเมื่อวาน: ยังไม่ยืนยัน (ล่าช้า)" in morning["text"]


# ── 11 · the scorecard ───────────────────────────────────────────────────────
def test_scorecard_metrics_come_from_the_slips(con):  # noqa: F811
    for day, late in (("2026-09-24", 0), ("2026-09-25", 1), ("2026-09-26", 1), ("2026-09-28", 0), ("2026-09-29", 0),
                      ("2026-10-01", 0), ("2026-10-02", 0), ("2026-10-03", 0), ("2026-10-04", 0), ("2026-10-05", 0)):
        d = date.fromisoformat(day)
        IS.save(con, 1, day, [used()], "udon", submit=True, now=datetime(d.year, d.month, d.day, 21, tzinfo=BKK) + timedelta(days=late))
    con.commit()
    s = PL.scorecard(con, 1, weeks=3, as_of=AS_OF)
    assert s["from"] == "2026-09-24" and s["slips"] == {"days": 12, "confirmed": 8, "late": 2, "draft": 0, "missing": 2}
    assert s["metrics"]["issue_coverage"] == 83.3 and s["metrics"]["issue_on_time"] == 80.0
    assert s["passes"]["issue_coverage"] is False and s["passes"]["issue_on_time"] is False
    assert "count_minutes" not in s["metrics"] and "count_minutes" not in s["passes"] and "count_minutes" not in s["targets"]
    # the day still open (as_of, nothing confirmed yet) is not held against the branch
    assert PL.scorecard(con, 1, weeks=3, as_of=date(2026, 10, 6))["slips"]["days"] == 12
    assert PL.scorecard(con, 2, weeks=2, as_of=AS_OF)["verdict"] == "not_started"


# ── 12 · permissions and the HTTP flow ───────────────────────────────────────
def test_who_may_read_write_and_reopen():
    R = auth.ROLE_MODULES
    can = lambda m, p, role: auth.path_allowed(m, p, R[role])  # noqa: E731
    for path in ("/api/bom-center/issues", "/api/bom-center/issues/sheet", "/api/bom-center/issues/status", "/api/bom-center/issues/12"):
        for role in ("branch", "salessupport", "analyst", "operation", "accounting"):
            assert can("GET", path, role), (role, path)
        assert not can("GET", path, "warehouse") and not can("GET", path, "marketing")
    assert can("POST", "/api/bom-center/issues/save", "branch") and can("POST", "/api/bom-center/issues/save", "salessupport")
    assert can("POST", "/api/bom-center/issues/save", "analyst")
    assert not can("POST", "/api/bom-center/issues/save", "operation") and not can("POST", "/api/bom-center/issues/save", "accounting")
    assert can("POST", "/api/bom-center/issues/reopen", "salessupport") and can("POST", "/api/bom-center/issues/reopen", "analyst")
    assert not can("POST", "/api/bom-center/issues/reopen", "branch")             # a branch cannot unlock its own slip
    assert not can("POST", "/api/bom-center/issues/reopen", "operation") and not can("POST", "/api/bom-center/issues/reopen", "accounting")
    assert not can("POST", "/api/bom-center/issues/reopen", "warehouse")


def test_the_http_flow_sheet_save_status_list_detail_reopen(client, monkeypatch):
    import main
    from routers import bom_center as RB
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "t")
    hdr = {"X-Upload-Token": "t"}
    monkeypatch.setattr(IS, "now_bkk", lambda: at(5, 20))
    monkeypatch.setattr(F, "now_bkk", lambda: at(5, 8))
    monkeypatch.setattr(RB, "bkk_today", lambda: date(2026, 10, 5))
    client.get("/api/bom-center/hub/queue")                                  # creates the bc_* tables
    snap = _rows(main.DB_PATH)
    c = sqlite3.connect(str(main.DB_PATH))
    bid = c.execute("SELECT branch_id FROM branches WHERE name<>'ALL' ORDER BY branch_id LIMIT 1").fetchone()[0]
    c.execute("DELETE FROM bc_order_calendar")
    c.execute("INSERT OR REPLACE INTO bc_ingredient(code, name_th, kind, base_unit, recv_unit, qty_per_recv, supplier, active)"
              " VALUES('X-1', 'ของ ก', 'raw', 'กรัม', 'แพ็ค', 1000, 'ผู้จำหน่าย ก', 1)")
    c.commit()
    c.close()
    try:
        sh = client.get(f"/api/bom-center/issues/sheet?branch_id={bid}&date=2026-10-05")
        assert sh.status_code == 200, sh.text
        j = sh.json()
        assert j["status"]["status"] == "missing" and j["editable"] is True and len(j["reasons"]) == 5
        assert client.get(f"/api/bom-center/issues/sheet?branch_id={bid}&date=5-10").status_code == 400
        assert client.get("/api/bom-center/issues/sheet").status_code == 400            # the centre must name a branch
        body = {"date": "2026-10-05", "lines": [{"code": "X-1", "qty_used": 5, "unit": "กรัม"}], "submit": False}
        assert client.post(f"/api/bom-center/issues/save?branch_id={bid}", json=body).status_code in (401, 403)   # the token gate
        r = client.post(f"/api/bom-center/issues/save?branch_id={bid}", headers=hdr, json=body)
        assert r.status_code == 200 and r.json()["status"] == "draft" and r.json()["lines"] == 1, r.text
        st = client.get(f"/api/bom-center/issues/status?branch_id={bid}&date_from=2026-10-03&date_to=2026-10-05").json()
        assert [(x["date"], x["status"]) for x in st["days"]] == [("2026-10-03", "missing"), ("2026-10-04", "missing"),
                                                                 ("2026-10-05", "draft")]
        assert client.get(f"/api/bom-center/issues/status?branch_id={bid}&date_from=2026-01-01&date_to=2026-10-05").status_code == 400
        # the order flow sees the unconfirmed slip, and its write transaction starts inside save()
        seen = []
        real_save = OR.save
        monkeypatch.setattr(OR, "save", lambda cn, *a, **k: (seen.append(cn.in_transaction), real_save(cn, *a, **k))[1])
        ob = {"round": "1", "delivery": "2026-10-07", "lines": [{"code": "X-1", "confirmed_units": 1, "reason_code": "event"}]}
        o = client.post(f"/api/bom-center/order/save?branch_id={bid}", headers=hdr, json=ob)
        assert o.status_code == 200 and o.json()["slip_today"]["status"] == "draft", o.text
        o = client.post(f"/api/bom-center/order/save?branch_id={bid}", headers=hdr, json=dict(ob, submit=True))
        assert o.status_code == 400 and "ต้องยืนยันใบตัดเบิกของวันนี้ก่อนส่งใบสั่งซื้อ" in o.json()["detail"]
        assert seen == [False, False]
        r = client.post(f"/api/bom-center/issues/save?branch_id={bid}", headers=hdr, json=dict(body, submit=True))
        assert r.status_code == 200 and r.json()["status"] == "confirmed" and r.json()["late"] == 0
        again = client.post(f"/api/bom-center/issues/save?branch_id={bid}", headers=hdr, json=body)
        assert again.status_code == 400 and "ยืนยันแล้ว" in again.json()["detail"]
        lst = client.get(f"/api/bom-center/issues?branch_id={bid}&date_from=2026-10-01&date_to=2026-10-05").json()
        assert [(x["date"], x["status"], x["lines"]) for x in lst["slips"]] == [("2026-10-05", "confirmed", 1)]
        assert {s["code"] for s in lst["statuses"]} == {"confirmed", "late", "draft", "missing"}
        assert client.get(f"/api/bom-center/issues?branch_id={bid}&status=draft").json()["slips"] == []
        assert client.get("/api/bom-center/issues?status=zzz").status_code == 400
        assert len(client.get("/api/bom-center/issues?date_from=2026-10-01&date_to=2026-10-05").json()["slips"]) == 1   # every branch
        sid = lst["slips"][0]["slip_id"]
        d = client.get(f"/api/bom-center/issues/{sid}")
        assert d.status_code == 200 and d.json()["lines"][0]["code"] == "X-1" and [x["action"] for x in d.json()["log"]] == ["create", "confirm"]
        assert client.get("/api/bom-center/issues/99999").status_code == 404
        # the order can now be sent
        o = client.post(f"/api/bom-center/order/save?branch_id={bid}", headers=hdr, json=dict(ob, submit=True))
        assert o.status_code == 200 and o.json()["status"] == "submitted", o.text
        assert seen == [False, False, False]
        # Sales Support reopens with a reason; the branch edits and confirms again
        no_note = client.post("/api/bom-center/issues/reopen", headers=hdr, json={"slip_id": sid, "note": " "})
        assert no_note.status_code == 400 and "เหตุผล" in no_note.json()["detail"]
        ro = client.post("/api/bom-center/issues/reopen", headers=hdr, json={"slip_id": sid, "note": "คีย์ปริมาณผิด"})
        assert ro.status_code == 200 and ro.json()["status"] == "draft", ro.text
        r = client.post(f"/api/bom-center/issues/save?branch_id={bid}", headers=hdr,
                        json={"date": "2026-10-05", "lines": [{"code": "X-1", "qty_used": 6, "unit": "กรัม"}], "submit": True})
        assert r.status_code == 200 and r.json()["confirm_count"] == 2
        d = client.get(f"/api/bom-center/issues/{sid}").json()
        assert [x["action"] for x in d["log"]] == ["create", "confirm", "reopen", "save", "reconfirm"]
    finally:
        _restore(main.DB_PATH, snap)
