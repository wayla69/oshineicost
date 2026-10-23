# -*- coding: utf-8 -*-
"""PL3 (RV3 F14-F19): the branch pages never present "nothing to compare" as data, and a new branch starts on its first day.

  * ส่วนต่างการใช้: a percentage over 999 reads ">999%" (a comparison against almost nothing is not a size); no comparator = "—", never the green
    "เบิกตามปกติ"; the ✓ of "nothing found" is the chip's own, said once, and only when something was compared; a comparator with no basis says
    "ยังไม่มีข้อมูลพอ" instead of ฿0;
  * a branch login reads the branch words in the search ("ปิดเดือน", not the office's "ปิดงวด");
  * สั่งของ: the fallback message is said once (the card below it already says it) and its "x/y วัน" is behind the "?";
  * a branch with no activity yet starts TODAY (started.effective_start): no past day is "ยังไม่ทำ", and a round whose cutoff is before that day is not
    "เลยเวลาส่ง · มีค่าปรับ"; a branch that has started keeps its late rounds.
"""
import json
import re
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

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


def _node(code, expr, tmp_path):
    js = tmp_path / "t.js"
    js.write_text(code + f"\nconsole.log(JSON.stringify({expr}));\n", encoding="utf-8")
    return json.loads(subprocess.run([NODE, str(js)], capture_output=True, text=True, encoding="utf-8", check=True).stdout)


# ── the gap words ───────────────────────────────────────────────────────────────────────────────────────────────────────
@needs_node
def test_a_percentage_over_999_is_capped_and_no_comparison_is_a_dash(tmp_path):
    src = _src("js/13-bom-center.js")
    a = src.index("const _BMC_GAP_WORDS")
    code = ("function esc(s){return String(s)}\n" + src[a:src.index("function _bmcPrice")].replace("function stkGapHtml", "function stkGapHtml")
            + "\nfunction _bmcNum(v,d=2){ if (v===null||v===undefined||v===''||isNaN(v)) return '—'; return Number(v).toLocaleString('en-US',{minimumFractionDigits:0,maximumFractionDigits:d}); }")
    out = _node(code, "[bmcGapWords(5000, 17669, 'issue_m', true), bmcGapWords(-5000, -99.9, 'issue_n'), stkGapHtml(null, null, 'issue_n'), "
                      "stkGapHtml(0, 0, 'issue_n'), bmcGapWords(250, 12.5, 'issue_n')]", tmp_path)
    assert out[0] == "มากกว่า ฿5,000 (>999%)"
    assert "999" not in out[1] and out[1].startswith("เบิกน้อยกว่าปกติ")
    assert "—" in out[2] and "เบิกตามปกติ" not in out[2]            # nothing compared: a dash, not "all good"
    assert "ใกล้เคียงปกติ" in out[3] or "เบิกตามปกติ" in out[3]      # a real zero gap is still said
    assert out[4].startswith("เบิกมากกว่าปกติ ฿250 (13%)") or "12.5" in out[4] or "13%" in out[4]


def test_the_no_basis_states_are_in_the_source():
    f = _src("js/15-orderhub.js")
    assert "ยังไม่มีข้อมูลพอให้เทียบกับปกติ" in f and "'ไม่พบการเบิกเกินหรือต่ำกว่าปกติต่อเนื่อง'" in f   # no ✓ in the text: the chip adds its own
    assert "✓ ไม่พบการเบิกเกินหรือต่ำกว่าปกติต่อเนื่อง" not in f and "✓ ไม่พบการเบิกเกินหรือน้อยกว่าสูตรต่อเนื่อง" not in f
    b = _src("js/14-branch.js")
    assert b.count("ยังไม่มีข้อมูลพอ") >= 2                          # the branch card head says it for the normal and for the all-branch median
    assert "t.normal_value > 0" in b and "t.median_value > 0" in b


def test_a_branch_reads_the_branch_words_in_the_search():
    s = _src("js/18-launcher.js")
    assert "function _brWord(" in s and "'branch'" in s and "ปิดงวด" in s and "ปิดเดือน" in s
    assert "label = _brWord(label)" in s


def test_the_order_page_says_its_fallback_once_with_the_days_behind_a_question_mark():
    b = _src("js/14-branch.js")
    sl = _fn(b, "_brnSrcLine")
    assert "p.ref_mode && p.ref_mode.active) return ''" in sl.replace("\n", " ") or "ref_mode.active" in sl
    assert "uiQ('brn:srcline'" in sl
    order = _fn(b, "brnRenderOrder")
    assert 'id="brnOrdWhy"' not in order and "ส่งใบสั่งซื้อไม่ได้ · ต้องส่งใบตัดเบิกวันนี้ก่อน" not in order      # the card above the list says it
    assert 'aria-describedby="brnOrdGate"' in order
    ref = _fn(b, "_brnRenderRefCard")
    assert ref.count("uiQ('brn:ref'") == 1 and "text-[11px] mt-1\" style=\"color:var(--text-muted)\">${own}" not in ref   # the x/y days are inside the "?" only


# ── a new branch starts on its first day ────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def newbranch(app_db):
    import sqlite3
    from bom_center.schema import ensure
    con = sqlite3.connect(str(app_db))
    con.row_factory = sqlite3.Row
    ensure(con)
    con.execute("INSERT OR IGNORE INTO branches(branch_id, name) VALUES(98, 'สาขาใหม่PL3')")
    con.execute("INSERT OR IGNORE INTO branches(branch_id, name) VALUES(97, 'สาขาเก่าPL3')")
    con.execute("DELETE FROM bc_issue_slip WHERE branch_id IN (97, 98)")
    con.execute("DELETE FROM bc_order WHERE branch_id IN (97, 98)")
    con.execute("INSERT INTO bc_issue_slip(branch_id, slip_date, status, created_by, created_at, updated_at, confirmed_by, confirmed_at)"
                " VALUES(97, '2026-09-01', 'confirmed', 't', '2026-09-01', '2026-09-01', 't', '2026-09-01')")
    con.commit()
    yield con
    con.execute("DELETE FROM bc_issue_slip WHERE branch_id IN (97, 98)")
    con.commit()
    con.close()


def test_effective_start_is_the_first_activity_or_today(newbranch):
    from bom_center.started import branch_start, effective_start
    today = date(2026, 10, 23)
    assert branch_start(newbranch, 98) is None and effective_start(newbranch, 98, today) == "2026-10-23"
    assert branch_start(newbranch, 97) == "2026-09-01" and effective_start(newbranch, 97, today) == "2026-09-01"
    assert effective_start(newbranch, 98, "2026-10-23") == "2026-10-23"


def test_a_new_branch_has_no_round_that_is_already_late_and_an_old_one_keeps_it(newbranch):
    from bom_center.forecast import next_deliveries
    from bom_center import todo as TD
    today = date(2026, 10, 23)
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone(timedelta(hours=7)))
    late_exists = any(i["late"] for i in next_deliveries(newbranch, 98, today, horizon_days=7, now=now))
    assert late_exists                                           # the calendar does have a round whose cutoff has gone (22/10 for 24/10)
    for bid, name, expect_late in ((98, "สาขาใหม่PL3", False), (97, "สาขาเก่าPL3", True)):
        d = TD.build(newbranch, "2026-10", None, name, today=today, viewer_role="branch", now=now)
        rounds = [i for i in d["items"] if i["key"].startswith("order_due_")]
        assert rounds, name
        assert any(i["late"] for i in rounds) is expect_late, (name, [(i["key"], i["late"], i["cutoff_date"]) for i in rounds])
        if not expect_late:
            assert all(i["cutoff_date"] >= "2026-10-23" for i in rounds)
            assert not [i for i in d["items"] if i["key"].startswith("issue_missing_")]      # no past day to make up


def test_the_slip_day_strip_of_a_new_branch_has_no_past_missing_days():
    src = _src("backend/routers/bom_center.py")
    body = src.split("def bc_issue_status(")[1].split("@router")[0]
    assert "effective_start" in body and "date_from or x[\"date\"] >= start" in body
