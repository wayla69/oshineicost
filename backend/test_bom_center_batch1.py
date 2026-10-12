"""BoM improvement proposal · batch 1 (owner approved 2026-10-12): 1.1 · 2.4 · section 5 and the 10 strings.

The promises:
  * 1.1 the warehouse's order detail carries the unit of every line (`unit_label`, `unit_word`) and the screen writes it - "6 กล่อง
    (กล่องละ 10 แพ็ค)" - instead of a fixed "แพ็ค";
  * 2.4 only TODAY's slip stops an order: the page says so and lists the days behind it as "ใบที่ค้าง N วัน (8–11/10) ทำภายหลังได้";
  * 5.1 the cutoff is written one way ("ส่งใบสั่งซื้อภายใน พฤ. 15/10 เวลา 13:15") with the time left as "อีก 2 วัน 19 ชม.";
  * 5.2 one set of waste-reason words for the slip and the variance page, "กลุ่มมูลค่าสูง/กลาง/ต่ำ" for the value class;
  * 5.3 a message to the floor names the ingredient, never its code, and never says "กฎหน่วย";
  * string 10 a receipt is named by its round and delivery day (never ORDER-8 / CLAIM-1).
"""
import json
import re
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path

import pytest

from bom_center import fmt
from bom_center import forecast as F
from bom_center import issues as IS
from bom_center import orders as OR
from bom_center import stock as ST
from bom_center import variance as VA
from bom_center.clock import BKK
from test_bom_center_issues import CRAB, NOW, used  # noqa: F401
from test_bom_center_s4b import con  # noqa: F401
from test_bom_center_ux_review_w import _sent, av  # noqa: F401
from test_bom_center_unit_avg import CODE  # noqa: F401

REPO = Path(__file__).resolve().parent.parent


def _js(rel):
    return (REPO / rel).read_text(encoding="utf-8")


# ── 1.1 · the unit of an order line ──────────────────────────────────────────────────────────────────────────────
def test_the_hub_order_lines_carry_the_unit_the_screen_writes(av):  # noqa: F811
    av.execute("UPDATE bc_ingredient SET recv_unit='กล่อง (10 แพ็ค)', qty_per_recv=1000 WHERE code=?", (CODE,))
    av.commit()
    oid, _q = _sent(av)
    o = OR.detail(av, oid)                                       # what GET /hub/order/{id} returns (bc_hub_order adds only the branch name and claims)
    ln = next(x for x in o["lines"] if x["code"] == CODE)
    assert ln["unit_label"] == "กล่อง (10 แพ็ค)" and ln["unit_word"] == "กล่อง"
    for k in ("code", "name", "confirmed_units", "suggested_units", "processed_units", "received_units", "order_unit", "unit_label", "unit_word"):
        assert k in ln, k                                         # every field js/15's order detail reads


def test_no_order_line_unit_is_hard_coded_in_the_hub_screen():
    src = _js("js/15-orderhub.js")
    assert "'แพ็ค'" not in src and '"แพ็ค"' not in src and "'หน่วยฐาน'" not in src
    assert "_ohbUnitFull(l)" in src and "_ohbUnitWord(l)" in src
    # the same two helpers write the phone block and the desktop table
    assert src.count("_ohbUnitFull(l)") >= 2


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_unit_helper_writes_the_contents_of_a_label_once():
    src = _js("js/15-orderhub.js")
    fn = re.search(r"function _ohbUnitWord\(l\).*?\n}\n", src, re.S)
    word = re.search(r"function _ohbUnitWord\(l\)[^\n]*\n", src).group(0)
    full = re.search(r"function _ohbUnitFull\(l\) \{.*?\n\}\n", src, re.S).group(0)
    assert fn and word and full
    cases = [{"unit_label": "กล่อง (10 แพ็ค)", "unit_word": "กล่อง"}, {"unit_label": "แพ็ค 10 กก.", "unit_word": "แพ็ค"},
             {"unit_label": "กิโลกรัม", "unit_word": "กิโลกรัม"}, {"unit_label": None, "unit_word": ""}]
    script = word + full + "console.log(JSON.stringify(%s.map(_ohbUnitFull)));" % json.dumps(cases, ensure_ascii=False)
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    assert json.loads(out) == ["กล่อง (กล่องละ 10 แพ็ค)", "แพ็ค (แพ็คละ 10 กก.)", "กิโลกรัม", ""]


# ── 2.4 · the page does not send the branch back to the missed days ──────────────────────────────────────────────
def test_the_order_page_says_only_todays_slip_blocks_the_order():
    src = _js("js/14-branch.js")
    assert "ทำใบของวันที่ขาดก่อน" not in src and "ยังไม่ได้ทำใบตัดเบิก" not in src
    assert "ยืนยันใบตัดเบิกวันนี้แล้วส่งใบสั่งซื้อได้" in src
    assert "ทำภายหลังได้" in src and "brnMissLater()" in src and "brnIssGoToday()" in src      # the dismiss link and the link to the slip
    # …and the server agrees: it asks for today's slip only
    assert "ต้องยืนยันใบตัดเบิกของวันนี้ก่อนส่งใบสั่งซื้อ" in _js("backend/bom_center/orders.py")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_backlog_is_written_as_ranges():
    src = _js("js/14-branch.js")
    dm = re.search(r"function _brnIssDm\(iso\)[^\n]*\n", src).group(0)
    run = re.search(r"function _brnIssRunTxt\(a, b\) \{.*?\n\}\n", src, re.S).group(0)
    runs = re.search(r"function _brnIssRuns\(days\) \{.*?\n\}\n", src, re.S).group(0)
    cases = [["2026-10-08", "2026-10-09", "2026-10-10", "2026-10-11"], ["2026-10-08", "2026-10-09", "2026-10-11"], ["2026-10-11"],
             ["2026-09-30", "2026-10-01", "2026-10-02"], ["2026-10-01", "2026-10-03", "2026-10-05", "2026-10-07", "2026-10-09", "2026-10-11"]]
    script = dm + run + runs + "console.log(JSON.stringify(%s.map(_brnIssRuns)));" % json.dumps(cases)
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    assert json.loads(out) == ["8–11/10", "8–9/10, 11/10", "11/10", "30/9–2/10", "1/10, 3/10, 5/10, 7/10 และอีก 2 วัน"]


# ── 5.1 · one way to write the cutoff and the time left ─────────────────────────────────────────────────────────
def test_the_cutoff_and_the_time_left_have_one_form():
    thu = datetime(2026, 10, 15, 13, 15, tzinfo=BKK)
    assert fmt.cutoff_label(thu) == "พฤ. 15/10 เวลา 13:15" == F.cutoff_th(thu)
    assert fmt.left_th(67.4) == "อีก 2 วัน 19 ชม."
    assert fmt.left_th(48) == "อีก 2 วัน" and fmt.left_th(24.5) == "อีก 1 วัน"
    assert fmt.left_th(23.9) == "อีก 23 ชม." and fmt.left_th(5) == "อีก 5 ชม."          # hours only under 24 hours
    assert fmt.left_th(0.5) == "อีก 30 นาที" and fmt.left_th(-3) == "อีก 0 นาที"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_screen_helpers_agree_with_the_server_ones():
    src = _js("js/01-core.js")
    parts = [re.search(p, src, re.S).group(0) for p in (
        r"const _TH_WD_SHORT = [^\n]*\n", r"function _isoParts\(iso\)[^\n]*\n", r"function fmtDayShort\(iso\)[^\n]*\n",
        r"function fmtCutoff\(iso\)[^\n]*\n", r"function fmtLeft\(hours\) \{.*?\n\}\n")]
    script = "".join(parts) + ("console.log(JSON.stringify([fmtCutoff('2026-10-15T13:15:00+07:00'), fmtLeft(67.4), fmtLeft(48),"
                              " fmtLeft(23.9), fmtLeft(0.5), fmtLeft(5)]));")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    assert json.loads(out) == ["พฤ. 15/10 เวลา 13:15", "อีก 2 วัน 19 ชม.", "อีก 2 วัน", "อีก 23 ชม.", "อีก 30 นาที", "อีก 5 ชม."]


def test_no_screen_still_writes_the_old_cutoff_phrases():
    for rel in ("js/14-branch.js", "js/16-supplyhome.js", "backend/bom_center/todo.py"):
        src = _js(rel)
        assert "สั่งภายใน" not in src and "ส่งภายใน" not in src, rel
        assert not re.search(r"เหลือ \$\{[^}]*\} ชม|เหลือ \{hours\}", src), rel
    assert "ส่งใบสั่งซื้อภายใน" in _js("js/14-branch.js") and "ส่งใบสั่งซื้อภายใน" in _js("js/16-supplyhome.js")


# ── 5.2 · the same words on both pages ──────────────────────────────────────────────────────────────────────────
def test_the_slip_and_the_variance_page_share_their_reason_words():
    iss, var = dict(IS.WASTE_REASONS), dict(VA.REASONS)
    for code in set(iss) & set(var):
        assert iss[code] == var[code] == fmt.REASON_LABEL[code], code
    assert iss["waste"] == "วัตถุดิบเสีย / ทิ้ง" and {"waste", "expired", "other"} <= set(iss) & set(var)
    assert set(iss) | set(var) <= set(fmt.REASON_LABEL)
    assert list(iss) == ["waste", "expired", "spill", "prep_loss", "other"]                   # the stored codes are unchanged
    assert list(var) == ["waste", "expired", "staff", "overportion", "transfer", "miscount", "receipt", "other"]


def test_the_floor_reads_the_value_class_as_a_group_and_the_label_as_must_count():
    assert fmt.CLASS_TH == {"A": "กลุ่มมูลค่าสูง", "B": "กลุ่มมูลค่ากลาง", "C": "กลุ่มมูลค่าต่ำ"}
    hub = _js("js/15-orderhub.js")
    assert "ชั้น ${" not in hub and "ชั้น A" not in hub
    brn = _js("js/14-branch.js")
    assert "'บังคับ'" not in brn and "_bmcChip('ต้องนับ'" in brn
    assert "ไดกิโตะ กรุ๊ป" in brn and re.search(r"ไดกิโตะ(?! กรุ๊ป)", brn) is None
    assert "คงเหลือ <b" not in brn                                                             # always "คงเหลือตามระบบ"


# ── 5.3 · the ingredient's name, not its code ───────────────────────────────────────────────────────────────────
def _name(con, code):  # noqa: F811
    return con.execute("SELECT name_th FROM bc_ingredient WHERE code=?", (code,)).fetchone()[0]


def test_a_message_to_the_branch_names_the_ingredient(con):  # noqa: F811
    nm = _name(con, CRAB)
    assert nm and nm != CRAB
    with pytest.raises(ValueError) as e:
        IS.save(con, 1, "2026-10-05", [used(qty="abc")], "udon", now=NOW)
    assert str(e.value) == f"{nm}: จำนวนต้องเป็นตัวเลข" and CRAB not in str(e.value)
    con.rollback()
    with pytest.raises(ValueError) as e:
        IS.save(con, 1, "2026-10-05", [{"code": CRAB, "qty_waste": 1, "unit": "กรัม"}], "udon", now=NOW)
    assert str(e.value).startswith(nm + ":") and CRAB not in str(e.value)
    con.rollback()
    with pytest.raises(ValueError) as e:
        ST.save(con, 1, [{"code": CRAB, "qty": "x"}], "udon", count_date="2026-10-01")
    assert str(e.value).startswith(nm + ":")
    con.rollback()
    with pytest.raises(ValueError) as e:
        VA.add_receipt(con, 1, "2026-10-02", [{"code": CRAB, "qty": 0}], "udon")
    assert str(e.value) == f"{nm}: จำนวนต้องมากกว่า 0"
    con.rollback()


def test_a_unit_that_is_not_set_up_is_said_in_plain_words(con):  # noqa: F811
    nm = _name(con, CRAB)
    with pytest.raises(ValueError) as e:
        IS.save(con, 1, "2026-10-05", [used(qty=1, unit="ช้อนโต๊ะ")], "udon", submit=True, now=NOW)
    msg = str(e.value)
    assert msg == f"บันทึกไม่ได้: {nm} ยังไม่ได้ตั้งหน่วย (ช้อนโต๊ะ) · แจ้งฝ่ายวิเคราะห์"
    assert "กฎหน่วย" not in msg and CRAB not in msg
    con.rollback()
    with pytest.raises(ValueError) as e:
        VA.add_receipt(con, 1, "2026-10-02", [{"code": CRAB, "qty": 1, "unit": "ช้อนโต๊ะ"}], "udon")
    assert str(e.value) == f"บันทึกไม่ได้: {nm} ยังไม่ได้ตั้งหน่วย (ช้อนโต๊ะ) · แจ้งฝ่ายวิเคราะห์"
    con.rollback()


# ── string 10 · a receipt is named by its round and delivery day ───────────────────────────────────────────────
def test_a_receipt_is_named_by_its_round_and_delivery_day(av):  # noqa: F811
    oid, q = _sent(av)
    OR.receive(av, 1, oid, [{"code": CODE, "received_units": q, "pieces": 2}], "udon", ack=True)
    av.commit()
    r = next(x for x in VA.receipts(av, 1) if x["ref"] == f"ORDER-{oid}")
    d = date.fromisoformat(av.execute("SELECT COALESCE(delivery_date, cycle_date) FROM bc_order WHERE order_id=?", (oid,)).fetchone()[0])
    rnd = av.execute("SELECT round FROM bc_order WHERE order_id=?", (oid,)).fetchone()[0]
    assert r["ref_th"] == f"รับตามใบสั่งซื้อ {F.ROUND_TH[rnd]} (ส่ง {d.day}/{d.month})"
    assert "ORDER-" not in r["ref_th"]
    # a reference the person typed is left as written; the central report and a claim are named too
    assert VA._ref_th(av, 1, "INV-77") is None
    assert VA._ref_th(av, 1, "CENTRAL-2026-09") == "รายงานไดกิโตะ กรุ๊ป เดือนกันยายน 2026"
    assert VA._ref_th(av, 1, "CLAIM-999") == "ปรับตามเคลม"


def test_the_receipt_cancel_button_is_worded_and_the_order_label_replaces_the_ref():
    brn = _js("js/14-branch.js")
    assert ">ยกเลิกใบรับสินค้า</button>" in brn and "ลบใบรับ" not in brn
    assert "x.ref_th" in brn


def test_the_slip_confirmation_says_what_blank_rows_mean():
    brn = _js("js/14-branch.js")
    assert "ยังไม่ได้กรอก ${Math.max(nAll - nAny.length, 0)} รายการ ถือว่าไม่ได้เบิก คงเหลือตามระบบจึงไม่ลดลง" in brn
    assert "(ถือว่าไม่ได้เบิก)" not in brn
    strip = _js("js/16-supplyhome.js")
    assert "รอยืนยัน · " not in strip and "ค้างย้อนหลัง" not in strip
    assert "label: 'ตัดเบิกวันนี้'" in strip and "ค้าง ${gaps} วัน" in strip
