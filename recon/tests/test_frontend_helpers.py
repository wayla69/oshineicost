"""Pure helpers of the recon frontend, run in Node (UX phase 3).

22-docs.js turns reconcile_v2 documents[].days_covered into the coverage strip's
pill ('ครบ' / 'ขาด 27–30' / 'ยังไม่มี'); 26-signoff.js builds the sign-off
checklist from the branch payload. The slices are plain browser scripts, so the
harness loads them into a vm context with a stubbed DOM. Skipped when Node is not
installed (CI installs it; the assertions are platform-neutral).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "frontend" / "js"
SLICES = ["00-core.js", "15-closeboard.js", "20-branch.js", "22-docs.js", "26-signoff.js"]

HARNESS = r"""
const vm = require('vm'), fs = require('fs'), path = require('path');
const noop = () => {};
const el = () => ({addEventListener: noop, classList: {add: noop, remove: noop, toggle: noop}, style: {}});
const ctx = {console, Intl, Date, Math, JSON, Set, Map, Array, Object, String, Number, isNaN,
  window: {addEventListener: noop}, localStorage: {getItem: () => null, setItem: noop},
  sessionStorage: {getItem: () => null, setItem: noop},
  document: {addEventListener: noop, getElementById: () => null, querySelectorAll: () => [],
             createElement: el, body: el(), head: el(), documentElement: el()},
  location: {hash: ''}, matchMedia: () => ({matches: false, addEventListener: noop})};
ctx.window.matchMedia = ctx.matchMedia;
vm.createContext(ctx);
const dir = process.argv[1];
// a slice's top-level const/let stay in the script scope: one combined script shares them
const src = JSON.parse(process.argv[2]).map(f => fs.readFileSync(path.join(dir, f), 'utf8')).join('\n;\n');
vm.runInContext(src + `
;globalThis.__out = (function(){
  const R = {};
  const r1_16 = Array.from({length: 16}, (_, i) => i + 1);
  R.text = [covText([], 30), covText(null, 30), covText(r1_16, 16), covText(r1_16, 30),
            covText([1,2,3,5,6,30], 30), covText([1, 3, 5, 7, 9], 10), covText([2], 2)];
  R.ranges = covRanges([9, 1, 2, 3, 5, 10, 2]);
  R.missing = covMissing([1, 2, 4], 5);
  R.through = [covThrough([1, 2, 3, 5]), covThrough([2, 3]), covThrough([])];
  R.cls = [covCls(1, new Set([1]), 10), covCls(2, new Set([1]), 10), covCls(11, new Set([1]), 10)];
  R.bkk = bkkYmd(new Date(Date.UTC(2026, 5, 30, 18, 30)));          // 01:30 on 1 July in Bangkok
  R.ts = [tsBkk('2026-06-30 18:30:00'), tsBkk('2026-06-15T02:05:09'), tsBkk('')];
  const doc = (d, st) => ({key: 'stmt_KBANK', label: 'สเตทเมนต์ KBANK', status: st || 'uploaded',
                           days_covered: d, days_due: 30});
  R.pill = [covPill(doc(Array.from({length: 30}, (_, i) => i + 1)), '2026-06'),
            covPill(doc(Array.from({length: 26}, (_, i) => i + 1), 'partial'), '2026-06'),
            covPill({label: 'สรุปคะแนน KTC', status: 'missing', detail: 'ยังไม่ได้อัปโหลด', days_covered: null}, '2026-06')];
  R.closeBlock = [closeBlock({todo: [], session: {pending_discrepancies: 0}}),
                  closeBlock({todo: [{}, {}], session: {pending_discrepancies: 1}})];
  const payload = {
    documents: [doc(Array.from({length: 26}, (_, i) => i + 1), 'partial'),
                {key: 'points_KTC', label: 'สรุปคะแนน KTC', status: 'uploaded', days_covered: null}],
    channels: [
      {key: 'QR', label: 'QR พร้อมเพย์', status: 'ok', days: [
        {day: '2026-06-12', variance: -100, ack: {note: 'ค่าธรรมเนียมธนาคาร', by: 'somsri'}}]},
      {key: 'CASH', label: 'เงินสด', status: 'diff', residual: -350, variance: -350, days: [],
       accepted: {note: 'เงินทอนขาด ตรวจกับสลิปแล้ว', by: 'somsri'}},
      {key: 'GRAB', label: 'Grab', status: 'waiting_doc', detail: 'ขาดรายงาน Grab', days: []}],
    payout_issues: [{label: 'Grab', variance: -40, accepted: {note: 'ค่าธรรมเนียมแพลตฟอร์ม', by: 'somsri'}}],
    todo: [{label: 'อัปโหลดรายงาน Grab'}], session: {pending_discrepancies: 0}};
  const reg = {rows: [{kind_key: 'manual_match'}, {kind_key: 'channel_move'}, {kind_key: 'lane_adj'},
                      {kind_key: 'lane_adj'}, {kind_key: 'day_ack'}], missing_reason: 0};
  R.checks = signoffChecks(payload, reg, '2026-06').map(c => ({key: c.key, ok: c.ok, detail: c.detail, n: c.items.length}));
  R.checksNoReg = signoffChecks(payload, null, '2026-06').find(c => c.key === 'adj').ok;
  return R;
})();`, ctx);
process.stdout.write(JSON.stringify(ctx.__out));
"""


def _run():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    out = subprocess.run([node, "-e", HARNESS, str(JS), json.dumps(SLICES)],
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def R():
    return _run()


def test_coverage_pill_text(R):
    assert R["text"] == ["ยังไม่มี", "ยังไม่มี", "ครบ", "ขาด 17–30", "ขาด 4, 7–29", "ขาด 2, 4, 6 …", "ขาด 1"]


def test_coverage_ranges_missing_and_run(R):
    assert R["ranges"] == [[1, 3], [5, 5], [9, 10]]
    assert R["missing"] == [3, 5]
    assert R["through"] == [3, 0, 0]
    assert R["cls"] == ["on", "miss", "fut"]


def test_bangkok_dates_and_stamps(R):
    assert R["bkk"] == "2026-07-01"                      # Bangkok, not the machine's zone
    assert R["ts"] == ["2026-07-01 01:30", "2026-06-15 09:05", ""]


def test_doc_pills_and_close_block(R):
    assert R["pill"] == [["c-ok", "ครบ"], ["c-warn", "ขาด 27–30"], ["c-err", "ยังไม่ได้อัปโหลด"]]
    assert R["closeBlock"] == ["", "ยังเหลือ 2 รายการ · มีข้อแตกต่างค้าง 1"]


def test_signoff_checklist(R):
    c = {x["key"]: x for x in R["checks"]}
    assert list(c) == ["docs", "ch", "acc", "adj", "todo", "disc"]
    assert not c["docs"]["ok"] and c["docs"]["detail"] == "อัปโหลดแล้ว 1/2 · ครอบคลุมต่อเนื่องถึงวันที่ 26"
    assert c["docs"]["n"] == 1                           # "สเตทเมนต์ KBANK: ขาด 27–30"
    assert not c["ch"]["ok"] and c["ch"]["detail"] == "2/3 ช่องทาง" and c["ch"]["n"] == 1
    assert c["acc"]["ok"] and c["acc"]["detail"] == "3 รายการ · รวม ฿490" and c["acc"]["n"] == 3
    # 2 lane_adj rows − 1 whole-channel accept (already listed above) = 1 lane adjustment
    assert c["adj"]["ok"] and c["adj"]["detail"] == "จับคู่ด้วยมือ 1 · ย้ายช่อง 1 · ปรับยอดช่อง 1"
    assert not c["todo"]["ok"] and c["disc"]["ok"]
    assert R["checksNoReg"] is False                     # register not loaded → flagged, not hidden
