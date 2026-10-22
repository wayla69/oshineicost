"""BoM Center — PR-W: seven fixes from the owner's review of PR-U / PR-U2 / PR-V.

The promises (the server side of them; the screens are checked in the browser):
  * ONE helper `units.unit_word(label)` turns a receiving-unit label that carries its contents ("กล่อง (10 แพ็ค)", "แพ็ค 10 กก.")
    into its unit word ("กล่อง", "แพ็ค") — and every place that MATCHES a receiving unit against unit names uses it (the Converter's
    receiving-unit step, the qty_per_recv sync of set_ladder and of the unit import, the pack-size report). The stored label is
    never rewritten;
  * the order, receive and claim payloads carry the unit word of every line, so a screen never prints a fixed "แพ็ค";
  * a line received by weight (a whole salmon) takes the WEIGHT weighed as its quantity: up to 3 % below the weight sent is no
    shortage (no reason, no claim); more needs a reason and becomes a claim; more than sent is accepted; the pieces are counted
    against the weight weighed, so the learned average is the branch's own;
  * the "ขนาดบรรจุไม่ตรง" form starts at the next unit down of the item's ladder (กล่อง -> แพ็ค), never the bare base unit.
"""
import pytest

from bom_center import claims as CL
from bom_center import master as M
from bom_center import orders as OR
from bom_center import pack_reports as PK
from bom_center import unit_sheet as US
from bom_center import units as U
from bom_center import variance as VA
from bom_center.units import Converter, unit_word
from test_bom_center_s4b import con  # noqa: F401
from test_bom_center_unit_avg import CODE, _row, _sent, av  # noqa: F401


# ── fix 2 · the unit word ───────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("label,word", [
    ("กล่อง (10 แพ็ค)", "กล่อง"), ("แพ็ค 10 กก.", "แพ็ค"), ("แพ็ค 4.4 กก.", "แพ็ค"), ("กล่อง (20 กิโลกรัม)", "กล่อง"),
    ("กิโลกรัม", "กิโลกรัม"), ("กระสอบ", "กระสอบ"), ("กก.", "กิโลกรัม"), ("แพ็ค,ห่อ", "แพ็ค"), ("PK", "แพ็ค"),
    ("  ถุง (10 กิโลกรัม) ", "ถุง"), ("", ""), (None, "")])
def test_unit_word_is_the_first_word_before_a_space_a_bracket_or_a_digit(label, word):
    assert unit_word(label) == word


def _shrimp(con):  # noqa: F811
    """CODE as the shrimp 03-01-179 is in the register: a box of 10 packs, written "กล่อง (10 แพ็ค)", 10,000 กรัม a box, and the
    ladder knows only the pack (1 แพ็ค = 1,000 กรัม)."""
    con.execute("UPDATE bc_ingredient SET recv_unit='กล่อง (10 แพ็ค)', qty_per_recv=10000 WHERE code=?", (CODE,))
    M.set_ladder(con, CODE, [_row("แพ็ค", 1000)], "t")
    con.commit()


def test_the_converter_matches_the_receiving_unit_by_its_word_and_by_its_label(av):  # noqa: F811
    M.set_ladder(av, CODE, [], "t")
    av.execute("UPDATE bc_ingredient SET recv_unit='กล่อง (10 แพ็ค)', qty_per_recv=10000 WHERE code=?", (CODE,))
    cv = Converter(av)
    assert cv.to_base(CODE, 2, "กล่อง")[0] == 20000                               # the word
    assert cv.to_base(CODE, 2, "กล่อง (10 แพ็ค)")[0] == 20000                      # the label as the receipts have stored it
    assert cv.factor(CODE, "ลัง")[0] == 10000 and cv.last_kind == "pack"           # a generic pack word, as before
    av.execute("UPDATE bc_ingredient SET recv_unit='เข่ง', qty_per_recv=7000 WHERE code=?", (CODE,))
    assert Converter(av).to_base(CODE, 2, "เข่ง")[0] == 14000                      # a word the pack list does not know
    av.execute("UPDATE bc_ingredient SET recv_unit='เข่ง (7 กก.)', qty_per_recv=7000 WHERE code=?", (CODE,))
    assert Converter(av).to_base(CODE, 2, "เข่ง")[0] == 14000                      # … written with its contents


def test_the_shrimp_pack_report_one_box_is_twelve_packs_is_accepted(av):  # noqa: F811
    _shrimp(av)
    oid, _q = _sent(av)
    info = PK.item_info(av, CODE)
    box = next(u for u in info["units"] if u["unit"] == "กล่อง")                     # the receiving unit is offered by its word
    assert info["recv_unit"] == "กล่อง" and info["recv_label"] == "กล่อง (10 แพ็ค)"
    assert box["current"] == "1 กล่อง = 10,000 กรัม" and box["label"] == "กล่อง (10 แพ็ค)" and box["default_parent"] == "แพ็ค"
    r = PK.submit(av, 1, oid, CODE, "กล่อง", 12, "แพ็ค", "กล่องใหม่บรรจุ 12", "udon")
    av.commit()
    assert av.execute("SELECT unit, reported_qty, reported_parent, current_qty, current_parent FROM bc_pack_report WHERE report_id=?",
                      (r["report_id"],)).fetchone() == ("กล่อง", 12, "แพ็ค", 10000, "กรัม")
    # the analyst applies it: the box joins the ladder and the pack size of the receiving unit follows (12 × 1,000)
    out = PK.decide(av, [r["report_id"]], "applied", None, "ana")
    av.commit()
    assert out["qty_per_recv_change"]["to"] == 12000
    assert av.execute("SELECT qty_per_recv FROM bc_ingredient WHERE code=?", (CODE,)).fetchone()[0] == 12000
    assert av.execute("SELECT recv_unit FROM bc_ingredient WHERE code=?", (CODE,)).fetchone()[0] == "กล่อง (10 แพ็ค)"      # the label is not rewritten
    assert Converter(av).to_base(CODE, 1, "กล่อง")[0] == 12000


def test_set_ladder_syncs_qty_per_recv_when_the_receiving_unit_label_carries_its_contents(av):  # noqa: F811
    _shrimp(av)
    res = M.set_ladder(av, CODE, [_row("กล่อง", 12, "แพ็ค"), _row("แพ็ค", 1000)], "t")
    assert res["qty_per_recv_change"] == {"recv_unit": "กล่อง (10 แพ็ค)", "from": 10000, "to": 12000, "approx": False}
    assert av.execute("SELECT qty_per_recv FROM bc_ingredient WHERE code=?", (CODE,)).fetchone()[0] == 12000
    # "แพ็ค 10 กก." -> the word แพ็ค is the ladder unit
    av.execute("UPDATE bc_ingredient SET recv_unit='แพ็ค 10 กก.', qty_per_recv=10000 WHERE code=?", (CODE,))
    res = M.set_ladder(av, CODE, [_row("แพ็ค", 9500)], "t")
    assert res["qty_per_recv_change"]["to"] == 9500
    assert av.execute("SELECT qty_per_recv FROM bc_ingredient WHERE code=?", (CODE,)).fetchone()[0] == 9500


def test_the_unit_import_syncs_qty_per_recv_through_the_same_path(av):  # noqa: F811
    _shrimp(av)
    parsed = {"ladders": {CODE: {"rows": [_row("กล่อง", 12, "แพ็ค"), _row("แพ็ค", 1000)], "row_nos": [3, 4], "base_in_file": set()}},
              "portions": [], "problems": []}
    out = US.apply(av, parsed, "t")
    assert out["problems"] == 0 and out["ladders"][0]["qty_per_recv_change"]["to"] == 12000
    assert av.execute("SELECT qty_per_recv FROM bc_ingredient WHERE code=?", (CODE,)).fetchone()[0] == 12000


def test_the_pack_report_starts_at_the_next_unit_down(av):  # noqa: F811
    M.set_ladder(av, CODE, [_row("กล่อง", 10, "แพ็ค"), _row("แพ็ค", 1000), _row("ตัว", 1.5, "กิโลกรัม", "avg", 1, 2)], "t")
    lad = Converter(av).ladder(CODE)
    assert PK.default_parent("กรัม", lad, "กล่อง") == "แพ็ค"          # a box of packs
    assert PK.default_parent("กรัม", lad, "แพ็ค") == "กิโลกรัม"       # a pack of grams: kilograms, never the bare gram
    assert PK.default_parent("กรัม", lad, "ตัว") == "กิโลกรัม"
    assert PK.default_parent("กรัม", lad, "ลัง") == "กล่อง"           # a unit that is not in the ladder: the top step
    assert PK.default_parent("กรัม", [], "แพ็ค") == "กิโลกรัม"        # no ladder at all
    assert PK.default_parent("มิลลิลิตร", [], "ขวด") == "ลิตร"
    assert PK.default_parent("ชิ้น", [], "แพ็ค") == "ชิ้น"            # nothing easier than the base
    units = {u["unit"]: u["default_parent"] for u in PK.item_info(av, CODE)["units"]}
    assert units == {"แพ็ค": "กิโลกรัม", "กล่อง": "แพ็ค", "ตัว": "กิโลกรัม"}
    assert all(u["default_parent"] in PK.item_info(av, CODE)["parents"] for u in PK.item_info(av, CODE)["units"])


# ── fix 1 · the payloads carry the unit word ─────────────────────────────────────────────────────────
def test_the_order_receive_claim_and_receipt_payloads_carry_the_unit_word(av):  # noqa: F811
    av.execute("UPDATE bc_ingredient SET recv_unit='กล่อง (10 แพ็ค)', qty_per_recv=1000 WHERE code=?", (CODE,))
    av.commit()
    oid, q = _sent(av)
    ln = next(x for x in OR.detail(av, oid)["lines"] if x["code"] == CODE)
    assert ln["unit_word"] == "กล่อง" and ln["unit_label"] == "กล่อง (10 แพ็ค)"        # the plain detail too (the order screen's extra rows)
    out = OR.receive(av, 1, oid, [{"code": CODE, "received_units": q - 1, "reason": "short", "pieces": 2}], "udon", ack=True)
    av.commit()
    lines = CL.claimable(av, 1, oid)["lines"]
    assert lines[0]["unit_word"] == "กล่อง" and lines[0]["unit_label"] == "กล่อง (10 แพ็ค)"
    cd = CL.detail(av, out["claim_id"])["lines"][0]
    assert cd["unit"] == "กล่อง" and cd["unit_label"] == "กล่อง (10 แพ็ค)"
    assert VA.receipts(av, 1)[0]["lines"][0]["unit_word"] == "กล่อง" and VA.receipts(av, 1)[0]["lines"][0]["unit"] == "กล่อง (10 แพ็ค)"
    # a base-unit line says the base unit; a pack line of an item with no receiving unit still says แพ็ค (as before)
    assert OR.unit_of_line(None, "กรัม", "base") == ("กรัม", "กรัม") and OR.unit_of_line(None, "กรัม", "pack") == (None, "แพ็ค")
    assert OR.unit_of_line("กิโลกรัม", "กรัม", "pack") == ("กิโลกรัม", "กิโลกรัม")


# ── fix 3 · the weight weighed ───────────────────────────────────────────────────────────────────────
def _line(av, oid):  # noqa: F811
    return next(x for x in OR.detail(av, oid, receive=True)["lines"] if x["code"] == CODE)


def test_the_receive_form_payload_carries_the_weight_sent_and_the_tolerance(av):  # noqa: F811
    oid, q = _sent(av)
    d = OR.detail(av, oid, receive=True)
    assert OR.WEIGHT_TOLERANCE_PCT == 3 and d["weight_tolerance_pct"] == 3
    assert next(x for x in d["lines"] if x["code"] == CODE)["sent_weight"] == pytest.approx(q)            # q packs of 1 kg


def test_a_weight_within_three_percent_below_the_weight_sent_is_no_shortage(av):  # noqa: F811
    oid, q = _sent(av)
    w = round(q * 0.98, 4)
    out = OR.receive(av, 1, oid, [{"code": CODE, "weight": w, "pieces": 2}], "udon", ack=True)             # no reason, no note
    av.commit()
    assert out["claim_id"] is None and out["claim_lines"] == 0 and out["over"] == []
    assert out["within_tolerance"][0]["code"] == CODE and out["within_tolerance"][0]["short_pct"] == pytest.approx(2.0)
    assert av.execute("SELECT received_units, received_pieces FROM bc_order_line WHERE order_id=? AND ingredient_code=?",
                      (oid, CODE)).fetchone() == (pytest.approx(w), 2)                                     # booked at the weight weighed
    assert av.execute("SELECT COUNT(*) FROM bc_claim WHERE order_id=?", (oid,)).fetchone()[0] == 0
    ln = av.execute("SELECT qty, qty_base, pieces FROM bc_receipt_line WHERE receipt_id=?", (out["receipt_id"],)).fetchone()
    assert ln == (pytest.approx(w), pytest.approx(w * 1000), 2)


def test_exactly_three_percent_is_still_within(av):  # noqa: F811
    oid, q = _sent(av)
    out = OR.receive(av, 1, oid, [{"code": CODE, "weight": q * 0.97, "pieces": 2}], "udon", ack=True)
    assert out["claim_id"] is None and len(out["within_tolerance"]) == 1


def test_a_weight_more_than_three_percent_below_is_a_short_line_that_needs_a_reason_and_becomes_a_claim(av):  # noqa: F811
    oid, q = _sent(av)
    w = round(q * 0.96, 4)
    with pytest.raises(ValueError, match="เลือกสาเหตุของส่วนต่าง"):
        OR.receive(av, 1, oid, [{"code": CODE, "weight": w, "pieces": 2}], "udon", ack=True)
    av.rollback()
    assert OR.detail(av, oid)["status"] == "processed"                                                       # nothing was saved
    out = OR.receive(av, 1, oid, [{"code": CODE, "weight": w, "pieces": 2, "reason": "short"}], "udon", ack=True)
    av.commit()
    assert out["claim_id"] and out["claim_lines"] == 1 and out["within_tolerance"] == []
    cl = CL.detail(av, out["claim_id"])["lines"][0]
    assert cl["claim_units"] == pytest.approx(q - w) and cl["received_units"] == pytest.approx(w)


def test_more_weight_than_was_sent_is_accepted_as_over_delivery(av):  # noqa: F811
    oid, q = _sent(av)
    out = OR.receive(av, 1, oid, [{"code": CODE, "weight": q + 1.5, "pieces": 3}], "udon", ack=True)
    assert out["claim_id"] is None and out["within_tolerance"] == []
    assert out["over"] == [{"code": CODE, "sent_units": q, "received_units": pytest.approx(q + 1.5), "over_units": pytest.approx(1.5)}]


def test_the_pieces_are_counted_against_the_weight_weighed(av):  # noqa: F811
    oid, q = _sent(av)
    w = round(q * 0.98, 4)
    OR.receive(av, 1, oid, [{"code": CODE, "weight": w, "pieces": 2}], "udon", receipt_date="2026-10-07", ack=True)
    av.commit()
    o = U.observed_avg(av, CODE, "ตัว", 90, today=__import__("datetime").date(2026, 10, 12))
    assert o["n_receipts"] == 1 and o["n_pieces"] == 2 and o["observed"] == pytest.approx(w / 2)        # the branch's own weight, not the weight sent


def test_a_weight_weighed_in_another_unit_than_the_order_unit_is_converted(av):  # noqa: F811
    # a box of 10 kg (qty_per_recv 10,000 กรัม): 4.5 boxes sent -> 45 kg; 44 kg weighed = 4.4 boxes, 2.2 % below -> within
    av.execute("UPDATE bc_ingredient SET recv_unit='กล่อง (10 กิโลกรัม)', qty_per_recv=10000 WHERE code=?", (CODE,))
    av.commit()
    oid, q = _sent(av)
    ln = _line(av, oid)
    assert ln["base_per_order_unit"] == 10000 and ln["sent_weight"] == pytest.approx(q * 10)
    w = round(q * 10 * 0.98, 4)
    out = OR.receive(av, 1, oid, [{"code": CODE, "weight": w, "pieces": 4}], "udon", ack=True)
    assert out["claim_id"] is None
    assert av.execute("SELECT received_units FROM bc_order_line WHERE order_id=? AND ingredient_code=?", (oid, CODE)).fetchone()[0] \
        == pytest.approx(w / 10)


def test_the_weight_is_refused_when_it_is_not_a_number_or_the_item_is_not_received_by_weight(av):  # noqa: F811
    oid, q = _sent(av)
    for bad, why in (("abc", "ต้องเป็นตัวเลข"), (-1, "ไม่ติดลบ")):
        with pytest.raises(ValueError, match=why):
            OR.receive(av, 1, oid, [{"code": CODE, "weight": bad, "pieces": 2}], "udon", ack=True)
        av.rollback()
    with pytest.raises(ValueError, match="ต้องระบุจำนวนที่รับ"):                                         # neither a quantity nor a weight
        OR.receive(av, 1, oid, [{"code": CODE, "pieces": 2}], "udon", ack=True)
    av.rollback()
    M.set_ladder(av, CODE, [], "t")                                                                       # no average step: a weight has no meaning
    av.commit()
    with pytest.raises(ValueError, match="ไม่ต้องกรอกน้ำหนักที่ชั่งได้"):
        OR.receive(av, 1, oid, [{"code": CODE, "weight": q, "received_units": q}], "udon", ack=True)


def test_a_line_without_a_weight_keeps_the_old_strict_rule(av):  # noqa: F811
    oid, q = _sent(av)
    with pytest.raises(ValueError, match="เลือกสาเหตุของส่วนต่าง"):                                        # 2 % short by quantity alone is still a shortage
        OR.receive(av, 1, oid, [{"code": CODE, "received_units": round(q * 0.98, 4), "pieces": 2}], "udon", ack=True)


def test_the_receive_route_accepts_a_weight_without_a_quantity():
    from routers.bom_center import ReceiveLine
    ln = ReceiveLine(code="02-01-001", weight=8.8, pieces=2)
    assert ln.received_units is None and ln.dict()["weight"] == 8.8
    assert ReceiveLine(code="X", received_units=3).dict()["weight"] is None


import pytest as _pytest_s3  # noqa: E402
pytestmark = _pytest_s3.mark.usefixtures("recipe_mode")   # PR-S3: this module builds its forecast, plans and count sheets from recipes (mode full)
