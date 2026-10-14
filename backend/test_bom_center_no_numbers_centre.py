# -*- coding: utf-8 -*-
"""No record numbers on the warehouse / Sales Support screens either (owner 2026-10-14: "change the warehouse and claim numbers to
round and date too"; batch 2 item 4.2 did the branch screens).

The promises:
  * an order is named "ขอนแก่น · รอบ 2 (ส่ง 17/10)" (salmon: "แซลมอนสด (ส่ง 19/10)") on the warehouse queue, the order detail, the
    confirm question and the accounting-keyed message - never "ใบสั่งซื้อ #9" or "#9";
  * a claim is "เคลมของ ขอนแก่น · รอบ 2 (ส่ง 17/10) · แจ้ง 12/10 14:05" on the claims list, the detail and the approve / reject question
    (the Bangkok clock of when it was raised), with "· ครั้งที่ 2" when its order has 2 or more claims - the API gives `nth` and `of_order`;
  * the numbers stay where they key into another system: the API fields, the audit log, the Excel files (consolidated.xlsx, the issue
    export's "เลขที่ใบ") and the PO reference the warehouse types.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from bom_center import claims as CL
from bom_center import hub as HB
from test_bom_center_receive_claims import CODE, _receive, _sent  # noqa: F401
from test_bom_center_s4b import con  # noqa: F401

REPO = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _src(rel):
    return (REPO / rel).read_text(encoding="utf-8")


def _fn(src, name):
    m = re.search(r"(?:async )?function %s\(" % re.escape(name), src)
    assert m, name
    i = src.index("{", src.index(")", m.end()))
    depth, j = 0, i
    while j < len(src):
        c = src[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
        j += 1
    raise AssertionError("unbalanced " + name)


def _const(src, name):
    m = re.search(r"^(?:const|let) %s = .*?;\s*$" % re.escape(name), src, re.M | re.S)
    assert m, name
    return m.group(0)


def _node(prelude, expr):
    out = subprocess.run([NODE, "-e", prelude + f"\nconsole.log(JSON.stringify({expr}));"], capture_output=True, text=True,
                         encoding="utf-8", check=True).stdout
    return json.loads(out)


CORE = _src("js/01-core.js")
BMC = _src("js/13-bom-center.js")
BRN = _src("js/14-branch.js")
OHB = _src("js/15-orderhub.js")
SHM = _src("js/16-supplyhome.js")
LABELS = (_fn(CORE, "_isoParts") + _fn(CORE, "_bkkClock") + _const(BMC, "_BMC_ROUND_TH") + _fn(BMC, "_bmcDm") + _fn(BMC, "_bmcRoundDay")
          + _fn(BMC, "_bmcOrderTitle") + _fn(BMC, "_bmcClaimWhen") + _fn(BMC, "_bmcClaimTitle"))


# ── the words on the screen ──────────────────────────────────────────────────────────────────────────────────────
@needs_node
def test_an_order_is_named_by_branch_round_and_delivery_day():
    assert _node(LABELS, "[_bmcRoundDay({round:'2', delivery:'2026-10-17'}), _bmcRoundDay({round:'salmon', delivery:'2026-10-19'}),"
                         " _bmcRoundDay({round:'1'}), _bmcOrderTitle({branch:'ขอนแก่น', round:'2', delivery:'2026-10-17'}),"
                         " _bmcOrderTitle({round:'salmon', cycle_date:'2026-10-16', delivery:'2026-10-19'})]") \
        == ["รอบ 2 (ส่ง 17/10)", "แซลมอนสด (ส่ง 19/10)", "รอบ 1", "ขอนแก่น · รอบ 2 (ส่ง 17/10)", "แซลมอนสด (ส่ง 19/10)"]


@needs_node
def test_a_claim_is_named_by_branch_round_delivery_and_the_bangkok_time_it_was_raised():
    got = _node(LABELS, """[
      _bmcClaimTitle({branch:'ขอนแก่น', round:'2', delivery:'2026-10-17', created_at:'2026-10-12T07:05:00+00:00', nth:1, of_order:1}),
      _bmcClaimTitle({branch:'อุดร', round:'salmon', delivery:'2026-10-19', created_at:'2026-10-12T07:05:00', nth:1, of_order:1}),
      _bmcClaimTitle({branch:'ขอนแก่น', round:'2', delivery:'2026-10-17', created_at:'2026-10-12T07:05:00+00:00', nth:1, of_order:2}),
      _bmcClaimTitle({branch:'ขอนแก่น', round:'2', delivery:'2026-10-17', created_at:'2026-10-12T18:30:00+00:00', nth:2, of_order:2}),
      _bmcClaimTitle({branch:'ขอนแก่น', round:'2', delivery:'2026-10-17'}),
      _bmcClaimWhen({created_at:'2026-10-12T07:05:00+00:00', nth:2, of_order:3}),
      _bmcClaimWhen({created_at:'2026-10-12 14:05', nth:1, of_order:1})]""")
    assert got == [
        "เคลมของ ขอนแก่น · รอบ 2 (ส่ง 17/10) · แจ้ง 12/10 14:05",
        "เคลมของ อุดร · แซลมอนสด (ส่ง 19/10) · แจ้ง 12/10 14:05",                       # a stamp without an offset is UTC, as the tables hold it
        "เคลมของ ขอนแก่น · รอบ 2 (ส่ง 17/10) · แจ้ง 12/10 14:05 · ครั้งที่ 1",           # the order has two claims: both say which
        "เคลมของ ขอนแก่น · รอบ 2 (ส่ง 17/10) · แจ้ง 13/10 01:30 · ครั้งที่ 2",           # 18:30 UTC is 01:30 the next day in Bangkok
        "เคลมของ ขอนแก่น · รอบ 2 (ส่ง 17/10)",                                          # no stamp: no "แจ้ง"
        "แจ้ง 12/10 14:05 · ครั้งที่ 2",
        "แจ้ง 12/10 14:05",                                                            # a space-separated stamp is the branch's own clock
    ]


# ── the screens use the words ────────────────────────────────────────────────────────────────────────────────────
def test_the_centre_screens_write_no_order_or_claim_number():
    files = ["js/01-core.js", "js/13-bom-center.js", "js/14-branch.js", "js/15-orderhub.js", "js/16-supplyhome.js"] \
        + sorted(str(p.relative_to(REPO)).replace("\\", "/") for p in (REPO / "tabs").glob("5[2-5]-*.html"))
    for rel in files:
        src = _src(rel)
        assert not re.search(r"ORDER-|CLAIM-|CENTRAL-", src), rel
        assert not re.search(r"#\$\{|#\{|ใบสั่งซื้อ #|เคลม #|'#' ?\+ ?[A-Za-z]|เลขที่ ?\$\{|เลขที่ ' ?\+", src), rel
        assert "เลขที่ใบเพื่อ" not in src, rel
    # the warehouse / Sales Support screens name an order and a claim with the shared helpers
    assert OHB.count("_bmcOrderTitle(") >= 4 and OHB.count("_bmcClaimTitle(") >= 4 and "_bmcClaimWhen(" in OHB
    assert "_bmcClaimWhen(" in BRN and "_brnRoundDay" not in BRN + OHB + BMC                  # one helper, in 13-bom-center.js
    # the confirm questions and the queue / detail headings
    for needle in ("อนุมัติ${_bmcClaimTitle(c)}", "ไม่อนุมัติ${_bmcClaimTitle(c)}", "ใบสั่งซื้อ ${_bmcOrderTitle(o)}"):
        assert needle in OHB, needle


def test_no_sentence_of_the_centre_backend_carries_a_record_number():
    for rel in sorted(str(p.relative_to(REPO)).replace("\\", "/") for p in (REPO / "backend" / "bom_center").glob("*.py")) \
            + ["backend/routers/bom_center.py"]:
        for n, line in enumerate(_src(rel).splitlines(), 1):
            code = line.split("  #")[0] if '"' in line else line
            if re.match(r"\s*#", line) or '"""' in line:
                continue
            # a sentence that writes "#" before an interpolated id ("{sku}#{seq}" is a recipe line's own key, not an order or a claim)
            assert not re.search(r"""(?<![\w}])#\{""", code), (rel, n, line)


# ── the words from the server ────────────────────────────────────────────────────────────────────────────────────
def test_the_accounting_message_names_the_order_not_its_number(con):  # noqa: F811
    con.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, value, delivery_date)"
                " VALUES(1, '2026-10-12', '2', 'submitted', 0, '2026-10-17')")
    con.commit()
    oid = con.execute("SELECT MAX(order_id) FROM bc_order").fetchone()[0]
    assert HB.order_label_th(con, oid).endswith(" · รอบ 2 (ส่ง 17/10)") and "#" not in HB.order_label_th(con, oid)
    with pytest.raises(ValueError) as e:
        HB.set_keyed(con, [oid], True, "ss")
    msg = str(e.value)
    assert "รอบ 2 (ส่ง 17/10)" in msg and "#" not in msg and "คลังยังไม่ยืนยันยอดส่ง" in msg
    con.rollback()
    assert HB.order_label_th(con, 99999) == "ใบสั่งซื้อ"


def test_two_claims_of_one_order_are_numbered_by_their_order_in_time(con):  # noqa: F811
    oid, q = _sent(con)
    out = _receive(con, oid, q - 2)                                  # the first claim, raised while receiving
    first = out["claim_id"]
    later = CL.create(con, 1, oid, [{"code": CODE, "claim_units": 1, "reason": "damaged"}], [], None, "udon")
    con.commit()
    second = later["claim_id"]
    a, b = CL.detail(con, first), CL.detail(con, second)
    assert (a["nth"], a["of_order"], b["nth"], b["of_order"]) == (1, 2, 2, 2)
    assert a["created_at"] and b["created_at"] and a["round"] == b["round"] and a["delivery"] == b["delivery"]
    assert [(c["nth"], c["of_order"], bool(c["created_at"])) for c in CL.for_order(con, oid)] == [(1, 2, True), (2, 2, True)]
    rows = {c["claim_id"]: c for c in CL.listing(con, status="all")}
    assert (rows[first]["nth"], rows[second]["nth"]) == (1, 2)
    # a claim cancelled by the branch still holds its place, so the other one is not renamed "ครั้งที่ 1"
    CL.cancel(con, first, "udon")
    assert CL.detail(con, second)["nth"] == 2 and CL.detail(con, second)["of_order"] == 2
    con.rollback()


def test_a_single_claim_says_nothing_about_its_turn(con):  # noqa: F811
    oid, q = _sent(con)
    out = _receive(con, oid, q - 2)
    d = CL.detail(con, out["claim_id"])
    assert (d["nth"], d["of_order"]) == (1, 1)


def test_a_replaced_version_is_named_by_its_label_in_the_release_note():
    src = _src("backend/bom_center/release.py")
    assert "เวอร์ชันเลขที่" not in src and "แทนที่โดยเวอร์ชัน {lbl(rel['label'])}" in src
