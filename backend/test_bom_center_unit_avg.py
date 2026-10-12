"""BoM Center — PR-U2: the average weight per piece learned from real receipts, and "ขนาดบรรจุไม่ตรง" from the branch.

The promises:
  * an item sold by weight whose pieces differ (a salmon: ตัว ≈ 4.5 กก.) is received WITH the number of pieces the branch counted:
    a whole number >= 1 when the received quantity is > 0 (the server is the gate); stored on the order line and the receipt line;
    a manual receipt may carry it too (optional);
  * the real average is Σ weight ÷ Σ pieces over the receipts of the last 90 days (all branches), in the parent unit;
  * it becomes a PROPOSAL only with >= 3 receipts, >= 10 pieces, a difference of >= 3 % and a receipt newer than the last decision;
    the analyst applies it (the ladder changes through set_ladder, the range widens when needed) or keeps the old value with a note;
  * a released month never moves when an average changes later (the factor of a released line is frozen with the release);
  * a branch reports a pack that is not the registered size: the report touches neither the receipt nor the register; it is checked
    as a whole ladder at once; the analyst applies it (all pending reports of the group) or rejects it with a note the branch reads;
  * a file import (BoM_Semi) never replaces a portion recipe;
  * the new routes follow the /units writers' rules; a branch can report only on its own order.
"""
import json
import sqlite3
from datetime import date

import pytest

import auth
from bom_center import hub as HB
from bom_center import master as M
from bom_center import orders as OR
from bom_center import pack_reports as PK
from bom_center import prep as P
from bom_center import todo as TD
from bom_center import units as U
from bom_center import variance as VA
from bom_center.units import Converter
from test_bom_center_receive_claims import _sent  # noqa: F401
from test_bom_center_s4b import con  # noqa: F401

CODE = "13-01-009"              # crab sticks, base กรัม, one pack of 1,000 กรัม in the fixture
TODAY = date(2026, 10, 12)


def _row(unit, qty, parent=None, kind="exact", rmin=None, rmax=None):
    return {"unit": unit, "qty_in_parent": qty, "parent_unit": parent, "conv_kind": kind, "range_min": rmin, "range_max": rmax,
            "label": None}


@pytest.fixture
def av(con):  # noqa: F811
    """The fixture database with an average ladder on CODE: 1 ตัว ≈ 1.5 กก. (range 1–2), ฿0.5 per กรัม."""
    con.execute("UPDATE bc_ingredient SET recv_unit='แพ็ค', qty_per_recv=1000 WHERE code=?", (CODE,))
    con.execute("DELETE FROM bc_ingredient_price WHERE ingredient_code=?", (CODE,))
    con.execute("INSERT INTO bc_ingredient_price(ingredient_code, month, price_per_base, source) VALUES(?,?,?,?)",
                (CODE, "2026-09", 0.5, "purchase_avg"))
    M.set_ladder(con, CODE, [_row("ตัว", 1.5, "กิโลกรัม", "avg", 1, 2)], "t")
    con.commit()
    return con


def _receipts(con, specs, start=1):
    """Manual receipts [(kg, pieces)], one per day from 2026-10-0<start>: the way a branch's counted pieces reach the register."""
    for i, (kg, pcs) in enumerate(specs):
        VA.add_receipt(con, 1, f"2026-10-{start + i:02d}", [{"code": CODE, "qty": kg, "unit": "กิโลกรัม", "pieces": pcs}], "udon")
    con.commit()


# ── 1 · receiving: the branch counts the pieces ───────────────────────────────────────────────────
def test_receive_refuses_an_avg_item_without_a_whole_piece_count(av):
    oid, q = _sent(av)
    for pcs in (None, "", 0, 2.5, -1, "x"):
        with pytest.raises(ValueError) as e:
            OR.receive(av, 1, oid, [{"code": CODE, "received_units": q, "pieces": pcs}], "udon", ack=True)
        assert str(e.value).endswith("กรอกจำนวนตัวที่รับ (จำนวนเต็ม)") and "ปูอัด" in str(e.value), pcs
        av.rollback()
    assert OR.detail(av, oid)["status"] == "processed"                              # nothing was saved


def test_receive_stores_the_pieces_on_the_order_line_and_the_receipt_line(av):
    oid, q = _sent(av)
    out = OR.receive(av, 1, oid, [{"code": CODE, "received_units": q, "pieces": 2}], "udon", receipt_date="2026-10-07", ack=True)
    av.commit()
    assert out["status"] == "received"
    assert av.execute("SELECT received_pieces FROM bc_order_line WHERE order_id=? AND ingredient_code=?", (oid, CODE)).fetchone()[0] == 2
    ln = av.execute("SELECT qty, qty_base, pieces, pieces_unit FROM bc_receipt_line WHERE receipt_id=?", (out["receipt_id"],)).fetchone()
    assert ln == (q, q * 1000.0, 2.0, "ตัว")
    assert OR.detail(av, oid)["lines"][0]["received_pieces"] == 2
    # the branch's receipt history carries it too
    assert VA.receipts(av, 1)[0]["lines"][0]["pieces"] == 2.0


def test_a_line_received_at_zero_needs_no_count_and_an_item_without_the_step_ignores_pieces(av):
    oid, q = _sent(av)
    OR.receive(av, 1, oid, [{"code": CODE, "received_units": 0, "reason": "short", "pieces": None}], "udon", ack=True)
    av.commit()
    assert av.execute("SELECT received_pieces FROM bc_order_line WHERE order_id=?", (oid,)).fetchone()[0] is None
    # an item that is not sold by weight with differing pieces: a count is ignored, nothing is stored
    av.execute("INSERT INTO bc_ingredient(code, name_th, kind, base_unit) VALUES('Z-1','ผัก','raw','กรัม')")
    rec = VA.add_receipt(av, 1, "2026-10-08", [{"code": "Z-1", "qty": 500, "unit": "กรัม", "pieces": 3}], "udon")
    assert av.execute("SELECT pieces, pieces_unit FROM bc_receipt_line WHERE receipt_id=?", (rec["receipt_id"],)).fetchone() == (None, None)


def test_the_receive_form_payload_names_the_lines_that_ask_for_pieces(av):
    oid, q = _sent(av)
    d = OR.detail(av, oid, receive=True)
    ln = next(x for x in d["lines"] if x["code"] == CODE)
    assert ln["pieces_unit"] == "ตัว" and ln["pieces_range"] == [1, 2] and ln["pieces_avg"] == 1.5
    assert ln["pieces_parent_unit"] == "กิโลกรัม" and ln["pieces_parent_factor"] == 1000 and ln["base_per_order_unit"] == 1000
    assert ln["pack"]["recv_unit"] == "แพ็ค" and [u["unit"] for u in ln["pack"]["units"]] == ["ตัว", "แพ็ค"]
    assert d["pack_reports"] == []
    assert "pieces_unit" not in OR.detail(av, oid)["lines"][0]                       # the plain detail is unchanged


def test_a_manual_receipt_takes_optional_pieces(av):
    rec = VA.add_receipt(av, 1, "2026-10-08", [{"code": CODE, "qty": 3, "unit": "กิโลกรัม"}], "udon")      # none: fine
    assert av.execute("SELECT pieces FROM bc_receipt_line WHERE receipt_id=?", (rec["receipt_id"],)).fetchone()[0] is None
    rec = VA.add_receipt(av, 1, "2026-10-09", [{"code": CODE, "qty": 3, "unit": "กิโลกรัม", "pieces": 2}], "udon")
    assert av.execute("SELECT pieces, pieces_unit FROM bc_receipt_line WHERE receipt_id=?", (rec["receipt_id"],)).fetchone() == (2.0, "ตัว")
    with pytest.raises(ValueError, match="จำนวนเต็ม"):
        VA.add_receipt(av, 1, "2026-10-10", [{"code": CODE, "qty": 3, "unit": "กิโลกรัม", "pieces": 1.5}], "udon")


# ── 2 · what the receipts say ─────────────────────────────────────────────────────────────────────
def test_observed_avg_is_total_weight_over_total_pieces(av):
    _receipts(av, [(9.2, 2), (13.8, 3), (14.1, 3)])
    o = U.observed_avg(av, CODE, "ตัว", 90, today=TODAY)
    assert o["observed"] == pytest.approx(37.1 / 8) and o["observed"] == pytest.approx(4.6375)
    assert (o["n_receipts"], o["n_pieces"], o["parent"]) == (3, 8, "กิโลกรัม")
    assert o["min_receipt_avg"] == pytest.approx(4.6) and o["max_receipt_avg"] == pytest.approx(14.1 / 3)
    assert o["current"] == 1.5 and o["range"] == [1, 2] and o["diff_pct"] == pytest.approx((37.1 / 8 - 1.5) / 1.5 * 100)
    # a receipt without a count adds nothing; the window drops old receipts
    VA.add_receipt(av, 1, "2026-10-06", [{"code": CODE, "qty": 50, "unit": "กิโลกรัม"}], "udon")
    VA.add_receipt(av, 1, "2026-05-06", [{"code": CODE, "qty": 5, "unit": "กิโลกรัม", "pieces": 1}], "udon")
    assert U.observed_avg(av, CODE, "ตัว", 90, today=TODAY)["n_receipts"] == 3


def test_a_proposal_needs_three_receipts_ten_pieces_and_three_percent(av):
    _receipts(av, [(5.4, 3), (3.6, 2)])
    assert not _row_of(av)["proposal"] and _row_of(av)["state"] == "insufficient"           # 2 receipts
    _receipts(av, [(5.4, 3)], start=3)
    assert not _row_of(av)["proposal"] and _row_of(av)["n_pieces"] == 8                      # 3 receipts, only 8 pieces
    _receipts(av, [(3.6, 2)], start=4)
    r = _row_of(av)
    assert r["proposal"] and r["state"] == "proposal" and r["observed"] == pytest.approx(1.8) and r["diff_pct"] == pytest.approx(20)
    assert r["need_text"] == "ข้อมูลยังไม่พอ (ต้องรับอย่างน้อย 3 ครั้ง และ 10 ตัว)"
    # a difference below 3 % is not worth the analyst's time
    av.execute("DELETE FROM bc_receipt_line")
    av.execute("DELETE FROM bc_receipt")
    _receipts(av, [(3.06, 2), (4.59, 3), (3.06, 2), (4.59, 3)])                              # 15.3 / 10 = 1.53 = +2 %
    r = _row_of(av)
    assert r["n_pieces"] == 10 and r["diff_pct"] == pytest.approx(2.0) and not r["proposal"] and r["state"] == "steady"


def _row_of(con, code=CODE):  # noqa: F811
    return next(r for r in U.avg_proposals(con, 90, today=TODAY) if r["code"] == code)


def test_keeping_the_value_needs_a_note_and_silences_the_proposal_until_a_newer_receipt(av):
    _receipts(av, [(5.4, 3), (3.6, 2), (5.4, 3), (3.6, 2)])
    with pytest.raises(ValueError, match="เหตุผล"):
        U.decide_avg(av, CODE, "ตัว", "kept", "ana", today=TODAY)
    av.rollback()
    out = U.decide_avg(av, CODE, "ตัว", "kept", "ana", note="ปลาล็อตนี้ใหญ่ผิดปกติ", today=TODAY)
    av.commit()
    assert out["message"] == "✓ คงค่าเดิมแล้ว"
    assert Converter(av).weight_avg_step(CODE)["qty"] == 1.5                                   # the ladder is untouched
    d = av.execute("SELECT decision, note, current_avg, observed_avg, n_receipts, n_pieces FROM bc_unit_avg_decision").fetchall()
    assert d == [("kept", "ปลาล็อตนี้ใหญ่ผิดปกติ", 1.5, pytest.approx(1.8), 4, 10)]
    r = _row_of(av)
    assert not r["proposal"] and r["state"] == "decided"
    with pytest.raises(ValueError, match="ไม่มีข้อเสนอ"):
        U.decide_avg(av, CODE, "ตัว", "applied", "ana", today=TODAY)
    av.rollback()
    _receipts(av, [(5.5, 3)], start=6)                                                          # a newer receipt: it can come back
    assert _row_of(av)["proposal"]


def test_applying_updates_the_ladder_and_the_portion_cost_follows(av):
    P.save_portion(av, "CK-801", CODE, 1, "ตัว", 30, "ชิ้น", "k", name="ปูอัดชิ้น", month="2026-09")
    av.commit()
    assert P.PrepCoster(av, "2026-09", P.book(av)).cost("CK-801")["per_base"] == pytest.approx(25.0)   # 1500 g × ฿0.5 ÷ 30
    _receipts(av, [(5.4, 3), (3.6, 2), (5.5, 3), (3.6, 2)])                                     # 18.1 / 10 = 1.81
    out = U.decide_avg(av, CODE, "ตัว", "applied", "ana", today=TODAY)
    av.commit()
    assert out["new"] == 1.81 and not out["widened"] and out["message"].startswith("✓ ใช้ค่าเฉลี่ยใหม่ 1 ตัว = 1.81 กิโลกรัม")
    cv = Converter(av)
    assert cv.factor(CODE, "ตัว")[0] == pytest.approx(1810) and cv.weight_avg_step(CODE)["rmin"] == 1       # the range stays
    assert P.PrepCoster(av, "2026-09", P.book(av)).cost("CK-801")["per_base"] == pytest.approx(1810 * 0.5 / 30)
    assert av.execute("SELECT 1 FROM bc_audit WHERE action='unit_avg_update'").fetchone()
    assert av.execute("SELECT 1 FROM bc_audit WHERE action='unit_ladder' AND row_key=?", (CODE,)).fetchone()
    assert [r["decision"] for r in U.avg_history(av)] == ["applied"]


def test_applying_an_average_outside_the_range_widens_the_range_and_says_so(av):
    _receipts(av, [(7.2, 3), (4.8, 2), (7.2, 3), (4.8, 2)])                                     # 24 / 10 = 2.4, the range is 1–2
    out = U.decide_avg(av, CODE, "ตัว", "applied", "ana", today=TODAY)
    av.commit()
    assert out["widened"] and out["range"] == [1, 2.4] and "ขยายช่วงเป็น 1–2.4 กิโลกรัม" in out["message"]
    st = Converter(av).weight_avg_step(CODE)
    assert (st["qty"], st["rmin"], st["rmax"]) == (2.4, 1, 2.4)


def test_a_released_month_does_not_move_when_the_average_changes(av):
    from bom_center import snapshot_v0 as V0
    P.save_portion(av, "CK-801", CODE, 1, "ตัว", 30, "ชิ้น", "k", name="ปูอัดชิ้น", month="2026-09")
    av.commit()
    V0.build_v0(av)
    av.execute("INSERT INTO bc_release(label, kind, effective_month, status, scope, price_month) VALUES('t','monthly','2026-10','draft','all','2026-09')")
    rid = av.execute("SELECT MAX(release_id) FROM bc_release").fetchone()[0]
    P.freeze(av, rid)
    assert av.execute("SELECT factor_base FROM bc_rel_prep_line WHERE release_id=? AND prepared_code='CK-801'", (rid,)).fetchone()[0] == 1500
    av.execute("UPDATE bc_release SET status='released' WHERE release_id=?", (rid,))
    av.commit()
    before = P.PrepCoster(av, "2026-09", P.book(av, rid)).cost("CK-801")["per_base"]
    assert before == pytest.approx(25.0)
    _receipts(av, [(5.4, 3), (3.6, 2), (5.5, 3), (3.6, 2)])
    U.decide_avg(av, CODE, "ตัว", "applied", "ana", today=TODAY)
    av.commit()
    live = P.PrepCoster(av, "2026-09", P.book(av)).cost("CK-801")["per_base"]
    frozen = P.PrepCoster(av, "2026-09", P.book(av, rid)).cost("CK-801")["per_base"]
    assert live == pytest.approx(1810 * 0.5 / 30) and frozen == pytest.approx(25.0) == before                # only the live recipe moved
    assert P.explode(P.book(av, rid), Converter(av), "CK-801")[0][CODE] == pytest.approx(50.0)               # 1500 g ÷ 30, frozen
    assert P.explode(P.book(av), Converter(av), "CK-801")[0][CODE] == pytest.approx(1810 / 30)
    # an ordinary exact line is not frozen (nothing to protect), so the table stays small
    av.execute("INSERT INTO bc_ingredient(code, name_th, kind, base_unit) VALUES('CK-802','x','prepared','ชิ้น')")
    P.save(av, "CK-802", 10, "ชิ้น", [{"ingredient_code": CODE, "qty": 100, "unit": "กรัม"}], "k")
    av.execute("INSERT INTO bc_release(label, kind, effective_month, status, scope, price_month) VALUES('t2','monthly','2026-11','draft','all','2026-09')")
    rid2 = av.execute("SELECT MAX(release_id) FROM bc_release").fetchone()[0]
    P.freeze(av, rid2)
    assert av.execute("SELECT factor_base FROM bc_rel_prep_line WHERE release_id=? AND prepared_code='CK-802'", (rid2,)).fetchone()[0] is None
    assert av.execute("SELECT factor_base FROM bc_rel_prep_line WHERE release_id=? AND prepared_code='CK-801'", (rid2,)).fetchone()[0] == 1810


def test_the_todo_shows_the_two_review_queues_only_while_something_waits(av):
    keys = lambda: {i["key"]: i for i in TD.build(av)["items"]}  # noqa: E731
    assert "unit_avg_review" not in keys() and "pack_report_review" not in keys()
    from bom_center.clock import today as bkk_today                                            # the todo reads today's clock: receipts dated today
    av.execute("DELETE FROM bc_receipt_line")
    av.execute("DELETE FROM bc_receipt")
    for kg, pcs in [(5.4, 3), (3.6, 2), (5.4, 3), (3.6, 2)]:
        VA.add_receipt(av, 1, bkk_today().isoformat(), [{"code": CODE, "qty": kg, "unit": "กิโลกรัม", "pieces": pcs}], "udon")
    av.commit()
    it = keys()["unit_avg_review"]
    assert it["status"] == "todo" and it["count"] == 1 and it["owner_modules"] == ["bom.center", "bom.prices"]
    assert it["owner_th"] == "ฝ่ายวิเคราะห์" and it["title"] == "ตรวจค่าเฉลี่ยน้ำหนักต่อชิ้นจากการรับจริง (1 รายการ)"
    assert it["detail"] == "ปูอัด: ใช้ 1.5 กก. · รับจริง ≈ 1.8 กก. (+20.0%)"
    assert it["go"] == {"tab": "bomcenter", "pane": "conv", "anchor": "bmcAvgCard"}
    U.decide_avg(av, CODE, "ตัว", "kept", "ana", note="ตรวจแล้ว")
    av.commit()
    assert "unit_avg_review" not in keys()
    bname = av.execute("SELECT name FROM branches WHERE branch_id=1").fetchone()[0]
    assert all(i["key"] not in ("unit_avg_review", "pack_report_review") for i in TD.build(av, scoped_branch=bname)["items"])



# ── 3 · "ขนาดบรรจุไม่ตรง" ──────────────────────────────────────────────────────────────────────────
@pytest.fixture
def pk(av):
    """CODE with the ladder ตัว (avg) · กล่อง = 10 แพ็ค · แพ็ค = 1,000 กรัม, and one order of branch 1 to report on."""
    M.set_ladder(av, CODE, [_row("ตัว", 1.5, "กิโลกรัม", "avg", 1, 2), _row("กล่อง", 10, "แพ็ค"), _row("แพ็ค", 1000)], "t")
    av.commit()
    oid, q = _sent(av)
    return av, oid


def _snapshot(con):  # noqa: F811
    return (con.execute("SELECT status, received_units FROM bc_order o JOIN bc_order_line l USING(order_id)").fetchall(),
            con.execute("SELECT COUNT(*) FROM bc_receipt").fetchone()[0],
            con.execute("SELECT COUNT(*) FROM bc_receipt_line").fetchone()[0],
            [tuple(r) for r in con.execute("SELECT ingredient_code, unit, factor_to_base, qty_in_parent FROM bc_unit_rule ORDER BY 1, 2")],
            con.execute("SELECT qty_per_recv FROM bc_ingredient WHERE code=?", (CODE,)).fetchone()[0])


def test_a_report_is_saved_without_touching_the_receipt_or_the_register(pk):
    con, oid = pk  # noqa: F811
    before = _snapshot(con)
    r = PK.submit(con, 1, oid, CODE, "กล่อง", 12, "แพ็ค", "กล่องใหม่บรรจุ 12", "udon")
    con.commit()
    assert r["message"] == "✓ แจ้งขนาดบรรจุแล้ว ฝ่ายวิเคราะห์จะตรวจและปรับทะเบียน ถ่ายรูปฉลากส่งทางไลน์ด้วย"
    assert _snapshot(con) == before
    row = con.execute("SELECT branch_id, order_id, ingredient_code, unit, reported_qty, reported_parent, current_qty, current_parent,"
                      " note, status, created_by FROM bc_pack_report").fetchone()
    assert row == (1, oid, CODE, "กล่อง", 12, "แพ็ค", 10, "แพ็ค", "กล่องใหม่บรรจุ 12", "pending", "udon")
    # the receipt can still be saved afterwards, exactly as before
    OR.receive(con, 1, oid, [{"code": CODE, "received_units": 3, "reason": "short", "pieces": 2}], "udon", ack=True)
    con.commit()
    assert OR.detail(con, oid, receive=True)["pack_reports"][0]["status"] == "pending"


def test_a_report_is_pinned_to_the_branchs_own_order_and_checked_as_a_whole_ladder(pk):
    con, oid = pk  # noqa: F811
    with pytest.raises(PermissionError):
        PK.submit(con, 2, oid, CODE, "กล่อง", 12, "แพ็ค", None, "other")                     # another branch's order
    with pytest.raises(ValueError, match="ไม่พบใบสั่งซื้อ"):
        PK.submit(con, 1, 99999, CODE, "กล่อง", 12, "แพ็ค", None, "udon")
    with pytest.raises(ValueError, match="ไม่อยู่ในใบสั่งซื้อนี้"):
        PK.submit(con, 1, oid, "V-007", "กล่อง", 12, "แพ็ค", None, "udon")
    for args, why in [((CODE, "กล่อง", 0, "แพ็ค"), "มากกว่า 0"), ((CODE, "กล่อง", 12, "กล่อง"), "คนละหน่วย"),
                      ((CODE, "ลัง", 12, "แพ็ค"), "ไม่ใช่หน่วยบรรจุ"), ((CODE, "กล่อง", 12, "ถัง"), "ใช้เทียบไม่ได้"),
                      ((CODE, "แพ็ค", 0.1, "กล่อง"), "วนกลับ"),                                  # แพ็ค in กล่อง, กล่อง in แพ็ค: a loop
                      ((CODE, "ตัว", 5, "กิโลกรัม"), "ช่วง")]:                                  # an average of 5 outside its range 1–2
        with pytest.raises(ValueError, match=why):
            PK.submit(con, 1, oid, args[0], args[1], args[2], args[3], None, "udon")
        con.rollback()
    assert con.execute("SELECT COUNT(*) FROM bc_pack_report").fetchone()[0] == 0


def test_a_fourth_unit_is_refused_with_the_plain_message(pk):
    con, oid = pk  # noqa: F811
    con.execute("UPDATE bc_ingredient SET recv_unit='ถุง', qty_per_recv=500 WHERE code=?", (CODE,))     # not a ladder unit; the ladder is full
    with pytest.raises(ValueError, match="ไม่เกิน 3 หน่วย"):
        PK.submit(con, 1, oid, CODE, "ถุง", 400, "กรัม", None, "udon")
    assert [u["unit"] for u in PK.item_info(con, CODE)["units"]][-1] == "ถุง"
    assert "1 ถุง = 500 กรัม" in [u["current"] for u in PK.item_info(con, CODE)["units"]]


def _second_order(con):  # noqa: F811
    oid = con.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(2, '2026-10-06', '1', 'processed')").lastrowid
    con.execute("INSERT INTO bc_order_line(order_id, ingredient_code, confirmed_units, processed_units, order_unit)"
                " VALUES(?,?,3,3,'pack')", (oid, CODE))
    con.commit()
    return oid


def test_applying_a_group_updates_the_ladder_the_pack_size_and_every_pending_report_of_the_group(pk):
    con, oid = pk  # noqa: F811
    oid2 = _second_order(con)
    PK.submit(con, 1, oid, CODE, "แพ็ค", 1.2, "กิโลกรัม", "ฉลากเขียน 1.2 กก.", "udon")
    PK.submit(con, 2, oid2, CODE, "แพ็ค", 1.2, "กิโลกรัม", None, "ubon")
    PK.submit(con, 2, oid2, CODE, "กล่อง", 12, "แพ็ค", None, "ubon")                       # a different group
    con.commit()
    groups = PK.listing(con, "pending")
    g = next(x for x in groups if x["unit"] == "แพ็ค")
    assert (g["n_reports"], g["n_branches"], g["system_text"], g["reported_text"]) == (2, 2, "1 แพ็ค = 1,000 กรัม", "1 แพ็ค = 1.2 กิโลกรัม")
    assert [n["note"] for n in g["notes"]] == ["ฉลากเขียน 1.2 กก."]
    assert PK.pending_count(con) == 2
    out = PK.decide(con, [g["report_ids"][0]], "applied", None, "ana")                      # one id: the whole group is decided
    con.commit()
    assert out["reports"] == 2 and out["qty_per_recv_change"]["to"] == 1200 and out["message"] == "✓ ปรับทะเบียนเป็น 1 แพ็ค = 1.2 กิโลกรัม แล้ว"
    cv = Converter(con)
    assert cv.factor(CODE, "แพ็ค")[0] == pytest.approx(1200) and cv.factor(CODE, "กล่อง")[0] == pytest.approx(12000)   # the box follows
    assert con.execute("SELECT qty_per_recv FROM bc_ingredient WHERE code=?", (CODE,)).fetchone()[0] == 1200
    st = dict(con.execute("SELECT report_id, status FROM bc_pack_report"))
    assert sorted(st.values()) == ["applied", "applied", "pending"]
    assert con.execute("SELECT decided_by FROM bc_pack_report WHERE status='applied'").fetchone()[0] == "ana"
    assert con.execute("SELECT 1 FROM bc_audit WHERE action='unit_pack_report_apply' AND row_key=?", (f"{CODE}|แพ็ค",)).fetchone()
    assert PK.pending_count(con) == 1
    with pytest.raises(ValueError, match="ตัดสินใจไปแล้ว"):
        PK.decide(con, [g["report_ids"][0]], "applied", None, "ana")
    con.rollback()


def test_rejecting_needs_a_note_which_the_branch_reads_on_its_receive_history(pk):
    con, oid = pk  # noqa: F811
    PK.submit(con, 1, oid, CODE, "กล่อง", 12, "แพ็ค", None, "udon")
    con.commit()
    rid = con.execute("SELECT report_id FROM bc_pack_report").fetchone()[0]
    before = _snapshot(con)
    with pytest.raises(ValueError, match="ต้องระบุเหตุผล"):
        PK.decide(con, [rid], "rejected", "  ", "ana")
    con.rollback()
    with pytest.raises(ValueError, match="ตัดสินใจไม่ถูกต้อง"):
        PK.decide(con, [rid], "maybe", "x", "ana")
    con.rollback()
    PK.decide(con, [rid], "rejected", "ตรวจกับผู้จำหน่ายแล้ว กล่องละ 10 แพ็ค", "ana")
    con.commit()
    assert _snapshot(con) == before                                                          # the register is untouched
    OR.receive(con, 1, oid, [{"code": CODE, "received_units": 3, "reason": "short", "pieces": 2}], "udon", ack=True)
    con.commit()
    r = next(x for x in VA.receipts(con, 1) if x["ref"] == f"ORDER-{oid}")
    pr = r["pack_reports"][0]
    assert (pr["status"], pr["decision_note"], pr["text"]) == ("rejected", "ตรวจกับผู้จำหน่ายแล้ว กล่องละ 10 แพ็ค", "1 กล่อง = 12 แพ็ค")
    assert OR.detail(con, oid, receive=True)["pack_reports"][0]["status_th"] == "ไม่ใช้"
    assert PK.for_orders(con, 2) == {}                                                       # another branch sees nothing


def test_the_todo_counts_the_decisions_not_the_rows(pk):
    con, oid = pk  # noqa: F811
    oid2 = _second_order(con)
    PK.submit(con, 1, oid, CODE, "กล่อง", 12, "แพ็ค", None, "udon")
    PK.submit(con, 2, oid2, CODE, "กล่อง", 12, "แพ็ค", None, "ubon")                       # the same size from a second branch
    PK.submit(con, 2, oid2, CODE, "แพ็ค", 1.2, "กิโลกรัม", None, "ubon")
    con.commit()
    it = {i["key"]: i for i in TD.build(con)["items"]}["pack_report_review"]
    assert it["count"] == 2 and it["title"] == "ตรวจขนาดบรรจุที่สาขาแจ้ง (2 เรื่อง)" and it["owner_th"] == "ฝ่ายวิเคราะห์"
    assert it["owner_modules"] == ["bom.center", "bom.prices"] and it["go"]["anchor"] == "bmcPackCard"
    assert "ในระบบ 1 กล่อง = 10 แพ็ค · สาขาแจ้ง 1 กล่อง = 12 แพ็ค" in it["detail"]
    assert all(i["key"] != "pack_report_review" for i in TD.build(con, scoped_branch="BranchA")["items"])  # the branch's หน้างาน shows nothing new


# ── 4 · a file never replaces a portion recipe ─────────────────────────────────────────────────────
def test_the_bom_semi_import_leaves_a_portion_recipe_alone(av):
    P.save_portion(av, "CK-801", CODE, 1, "ตัว", 30, "ชิ้น", "k", name="ปูอัดชิ้น", month="2026-09")
    av.commit()
    block = {"row": 3, "src_code": "CK-001", "name": "ปูอัดชิ้น", "kitchen": "", "yield_text": "1 สูตร = 100 ชิ้น", "yield_qty": 100,
             "yield_unit": "ชิ้น", "net_cost": None, "lines": [{"seq": 1, "name": "ปูอัด", "qty": 100, "src_price": None}]}
    plan = P.plan_import(av, {"sheet": "BoM_Semi", "blocks": [block], "cpu": {}})
    w = next(x for x in plan["writes"] if x["prepared_code"] == "CK-801")
    assert w["portion_skip"] and w["note_th"] == "สูตรแบ่งชิ้น ไม่ถูกแทนที่โดยไฟล์นำเข้า แก้ไขที่การ์ดสูตรแบ่งชิ้น" and not w["replaces"]
    out = P.apply_import(av, plan, "k")
    av.commit()
    assert out["saved"] == 0 and out["skipped"] == [{"code": "CK-801", "name": "ปูอัดชิ้น", "note": w["note_th"]}]
    b = P.book(av)["CK-801"]
    assert b["mode"] == "portion" and b["yield_qty"] == 30 and b["lines"][0]["no_loss"] == 1 and b["lines"][0]["unit"] == "ตัว"


# ── 5 · permissions ───────────────────────────────────────────────────────────────────────────────
def test_new_routes_follow_the_units_writers_and_a_branch_may_only_report():
    ana, acc, br = auth.ROLE_MODULES["analyst"], auth.ROLE_MODULES["accounting"], auth.ROLE_MODULES["branch"]
    kit, ss, wh = auth.ROLE_MODULES["innovation"], auth.ROLE_MODULES["salessupport"], auth.ROLE_MODULES["warehouse"]
    for path in ("/api/bom-center/units/avg-decision", "/api/bom-center/units/pack-report/decide"):
        assert auth.path_allowed("POST", path, ana) and auth.path_allowed("POST", path, {"bom.prices"}), path
        for other in (acc, br, kit, ss, wh):
            assert not auth.path_allowed("POST", path, other), path
    assert auth.path_allowed("POST", "/api/bom-center/units/pack-report", br)                    # the branch reports …
    assert auth.path_allowed("POST", "/api/bom-center/units/pack-report", ana)
    assert not auth.path_allowed("POST", "/api/bom-center/units/pack-report", acc)
    assert not auth.path_allowed("POST", "/api/bom-center/units/pack-report", wh)
    assert not auth.path_allowed("POST", "/api/bom-center/units/pack-report/decide", br)         # … but never decides
    assert not auth.path_allowed("POST", "/api/bom-center/units", br)                            # and the old rule is unchanged
    for path in ("/api/bom-center/units/avg-proposals", "/api/bom-center/units/pack-reports"):
        assert auth.path_allowed("GET", path, ana) and not auth.path_allowed("GET", path, br), path


# ── 6 · over HTTP ──────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "owner-token-xyz")
    yield


def _login(user, pw=auth.DEFAULT_PASSWORD):
    import main
    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": user, "password": pw}).status_code == 200
    return c


def _branch_user(name, branch):
    mg = _login("management1")
    r = mg.post("/api/auth/users", headers={"X-Upload-Token": "owner-token-xyz"},
                json={"username": name, "password": "Temp@12345", "role": "branch", "branch": branch})
    assert r.status_code in (200, 409), r.text
    return _login(name, "Temp@12345")


def _seed_processed_order(branch_id):
    import core
    from bom_center.schema import ensure
    with core.get_db() as c:
        ensure(c)
        c.execute("INSERT OR IGNORE INTO bc_ingredient(code, name_th, base_unit, recv_unit, qty_per_recv) VALUES(?,?,?,?,?)",
                  ("T-AV1", "ปลาทดสอบ", "กรัม", "แพ็ค", 1000))
        M.set_ladder(c, "T-AV1", [_row("ตัว", 1.5, "กิโลกรัม", "avg", 1, 2), _row("กล่อง", 10, "แพ็ค"), _row("แพ็ค", 1000)], "t")
        oid = c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, delivery_date) VALUES(?,?,?,?,?)",
                        (branch_id, "2026-10-06", "1", "processed", "2026-10-07")).lastrowid
        c.execute("INSERT INTO bc_order_line(order_id, ingredient_code, confirmed_units, processed_units, order_unit)"
                  " VALUES(?,?,?,?,?)", (oid, "T-AV1", 6, 6, "pack"))
        c.commit()
    return oid


def _cleanup_http(*oids):
    import core
    with core.get_db() as c:
        for oid in oids:
            for rid, in c.execute("SELECT receipt_id FROM bc_receipt WHERE ref=?", (f"ORDER-{oid}",)).fetchall():
                c.execute("DELETE FROM bc_receipt_line WHERE receipt_id=?", (rid,))
                c.execute("DELETE FROM bc_receipt WHERE receipt_id=?", (rid,))
            c.execute("DELETE FROM bc_pack_report WHERE order_id=?", (oid,))
            c.execute("DELETE FROM bc_order_line WHERE order_id=?", (oid,))
            c.execute("DELETE FROM bc_order WHERE order_id=?", (oid,))
        c.execute("DELETE FROM bc_unit_rule WHERE ingredient_code='T-AV1'")
        c.execute("DELETE FROM bc_unit_avg_decision WHERE ingredient_code='T-AV1'")
        c.execute("DELETE FROM bc_ingredient WHERE code='T-AV1'")
        c.execute("DELETE FROM bc_audit WHERE row_key LIKE 'T-AV1%'")
        c.commit()


def test_http_flow_receive_pieces_pack_report_and_the_decisions(client, app_db, auth_on):
    oid_a, oid_b = _seed_processed_order(1), _seed_processed_order(2)
    try:
        a = _branch_user("brn_av_a", "BranchA")
        b = _branch_user("brn_av_b", "BranchB")
        ana = _login("analyst1")
        tok = {"X-Upload-Token": "owner-token-xyz"}
        # the receive form's payload tells which lines ask for pieces and what can be reported
        d = a.get(f"/api/bom-center/order/{oid_a}").json()
        ln = d["lines"][0]
        assert ln["pieces_unit"] == "ตัว" and ln["pieces_range"] == [1, 2] and ln["pack"]["units"][0]["unit"] == "ตัว"
        assert b.get(f"/api/bom-center/order/{oid_a}").status_code == 403
        # no count: refused with the plain message; with a count: saved
        body = {"lines": [{"code": "T-AV1", "received_units": 6}], "ack": True}
        no = a.post(f"/api/bom-center/order/{oid_a}/receive", json=body)
        assert no.status_code == 400 and "กรอกจำนวนตัวที่รับ (จำนวนเต็ม)" in no.json()["detail"]
        # a pack report first: it does not block the receive that follows
        rep = {"order_id": oid_a, "code": "T-AV1", "unit": "กล่อง", "qty": 12, "parent": "แพ็ค", "note": "ฉลากเขียน 12"}
        r = a.post("/api/bom-center/units/pack-report", json=rep)
        assert r.status_code == 200 and r.json()["report_id"], r.text
        assert b.post("/api/bom-center/units/pack-report", json=rep).status_code == 403            # not B's order
        assert a.post("/api/bom-center/units/pack-report", json={**rep, "qty": 0}).status_code == 400
        ok = a.post(f"/api/bom-center/order/{oid_a}/receive", json={**body, "lines": [{"code": "T-AV1", "received_units": 6, "pieces": 4}]})
        assert ok.status_code == 200, ok.text
        recs = a.get("/api/bom-center/stock/receipts").json()["receipts"]
        mine = next(x for x in recs if x["ref"] == f"ORDER-{oid_a}")
        assert mine["lines"][0]["pieces"] == 4 and mine["pack_reports"][0]["status"] == "pending"
        # a branch can neither read nor decide the reports
        assert a.get("/api/bom-center/units/pack-reports").status_code == 403
        assert a.post("/api/bom-center/units/pack-report/decide", json={"report_ids": [r.json()["report_id"]], "decision": "applied"}).status_code == 403
        assert a.get("/api/bom-center/units/avg-proposals").status_code == 403
        # the analyst reads and decides (the owner's token, like every central write)
        lst = ana.get("/api/bom-center/units/pack-reports").json()
        assert lst["pending"] == 1 and lst["groups"][0]["reported_text"] == "1 กล่อง = 12 แพ็ค" and lst["groups"][0]["system_text"] == "1 กล่อง = 10 แพ็ค"
        dec = {"report_ids": lst["groups"][0]["report_ids"], "decision": "applied"}
        assert ana.post("/api/bom-center/units/pack-report/decide", json=dec).headers.get("X-Denied") == "token"
        done = ana.post("/api/bom-center/units/pack-report/decide", json=dec, headers=tok)
        assert done.status_code == 200 and done.json()["reports"] == 1, done.text
        lad = ana.get("/api/bom-center/units/ladder?code=T-AV1").json()["ladder"]
        assert next(x for x in lad if x["unit"] == "กล่อง")["qty_in_parent"] == 12
        # the average: too little data to decide
        pr = ana.get("/api/bom-center/units/avg-proposals").json()
        row = next(x for x in pr["rows"] if x["code"] == "T-AV1")
        assert pr["need"] == {"receipts": 3, "pieces": 10, "diff_pct": 3.0} and row["n_receipts"] == 1 and row["n_pieces"] == 4
        assert row["state"] == "insufficient" and not row["proposal"]
        no_data = ana.post("/api/bom-center/units/avg-decision", headers=tok, json={"code": "T-AV1", "unit": "ตัว", "decision": "applied"})
        assert no_data.status_code == 400 and "ข้อมูลยังไม่พอ" in no_data.json()["detail"]
        assert ana.post("/api/bom-center/units/avg-decision", json={"code": "T-AV1", "unit": "ตัว", "decision": "kept", "note": "x"}).headers.get("X-Denied") == "token"
    finally:
        _cleanup_http(oid_a, oid_b)


def test_the_new_write_handlers_clear_the_usage_cache_and_the_receive_handler_does_not():
    import inspect
    from routers import bom_center as RB
    for fn in (RB.bc_units_avg_decision, RB.bc_units_pack_report_decide):
        src = inspect.getsource(fn)
        assert "clear_usage_cache()" in src and "_drop_bom_days()" in src, fn.__name__
    src = inspect.getsource(RB.bc_units_pack_report)                                           # a report changes no recipe-side input
    assert "clear_usage_cache" not in src and "_drop_bom_days" not in src
    assert "_drop_bom_days" not in inspect.getsource(RB.bc_order_receive)


def test_the_new_tables_are_owned_and_the_migrations_are_idempotent(tmp_path):
    from bom_center import schema as S
    assert "bc_unit_avg_decision" in S.OWNED_TABLES and "bc_pack_report" in S.OWNED_TABLES
    p = tmp_path / "m.db"
    c = sqlite3.connect(str(p))
    S.ensure(c, commit=True)
    S.ensure(c, commit=True)
    cols = lambda t: {r[1] for r in c.execute(f"PRAGMA table_info({t})")}  # noqa: E731
    assert "received_pieces" in cols("bc_order_line") and {"pieces", "pieces_unit"} <= cols("bc_receipt_line")
    assert "factor_base" in cols("bc_rel_prep_line")
    assert {"decision_id", "last_receipt_id", "decision"} <= cols("bc_unit_avg_decision")
    assert {"report_id", "reported_qty", "decision_note", "status"} <= cols("bc_pack_report")
    c.close()
    json.dumps(sorted(S.OWNED_TABLES))
