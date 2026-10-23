# -*- coding: utf-8 -*-
"""PR-U1 (owner 2026-10-22 "เริ่ม U1") - the supply module's blocking and data-flow fixes.

The promises:
  1. an item with no usable base unit is not on the branch's count sheet (it cannot be valued; it is the analyst's task) - a count of
     everything else is sent, an older draft line of such an item never blocks it; a refusal names items, never codes;
  2. an order refused for a missing reason names the items (short name or name), never the codes;
  3. an order line in base units (no pack size) carries `no_pack` and the readable unit (กรัม -> กิโลกรัม), stored quantities unchanged;
  4. ONE branch start date (started.branch_start): rounds before it are "ยังไม่เริ่มใช้ระบบ" (compliance, adoption: neutral, never red),
     a month that ended before it is not asked for (close steps), a day with a slip counts as a day in the slip summary;
  5. the daily variance never judges the day still trading nor a day with no formula value, and its head agrees with its rows;
  6. after a month-end count the plan names the next count and when it is due;
  7. "ยังไม่เสร็จ" is ONE number: the badges' total = the /todo answer's mine_open (the launcher, หน้างาน and the phone print it);
  8. a round whose cutoff is a later day is `upcoming` - "ถัดไป", never a badge.
"""
import json
import re
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path

import pytest

import auth
from bom_center import adoption as AD
from bom_center import hub as HB
from bom_center import issue_reports as IR
from bom_center import issues as IS
from bom_center import nav_badges as NB
from bom_center import orders as OR
from bom_center import started as SB
from bom_center import stock as ST
from bom_center import todo as TD
from bom_center import units as U
from bom_center.clock import BKK
from test_bom_center_s4b import con  # noqa: F401  (five weeks of sales at branch 1, 1 Sep - 5 Oct 2026)

REPO = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
AS_OF = date(2026, 10, 5)
WED = date(2026, 10, 7)
NOW = datetime(2026, 10, 5, 8, 0, tzinfo=BKK)


def _items(sh):
    return {i["code"]: i for z in sh["zones"] for i in z["items"]}


def _all_lines(sh, qty=1):
    return [{"code": c, "qty": qty, "unit": it["count_unit"]} for c, it in _items(sh).items()]


def _slip(c, day, hour=20):
    d = date.fromisoformat(day)
    IS.save(c, 1, day, [{"code": "V-007", "qty_used": 10, "unit": "กรัม"}], "udon", submit=True,
            now=datetime(d.year, d.month, d.day, hour, 0, tzinfo=BKK))


# ── 1 · a count the branch can always send ───────────────────────────────────
def test_an_item_with_no_base_unit_is_not_on_the_count_sheet_and_never_blocks_it(con):  # noqa: F811
    con.execute("INSERT INTO bc_ingredient(code, name_th, kind, base_unit, monitored) VALUES('X-001', 'น้ำจิ้มพิเศษ', 'raw', NULL, 1)")
    con.execute("INSERT INTO bc_ingredient(code, name_th, kind, base_unit, recv_unit, qty_per_recv, monitored)"
                " VALUES('S-011', 'เกลือป่น 1 กก.', 'raw', 'กรัม', 'ถุง 1 กก.', 1000, 1)")
    con.commit()
    sh = ST.sheet(con, 1, AS_OF)
    assert "X-001" not in _items(sh) and "S-011" in _items(sh) and sh["not_countable"] == 1
    # an older draft typed a line for it (before this fix the sheet listed it): the line has no base quantity...
    ST.save(con, 1, [{"code": "X-001", "qty": 3, "unit": ""}], "udon", count_date="2026-10-01")
    assert con.execute("SELECT qty_base FROM bc_stock_count_line WHERE ingredient_code='X-001'").fetchone() == (None,)
    # ...and the count of every countable item is sent all the same - the line is dropped, not a refusal
    out = ST.save(con, 1, _all_lines(sh), "udon", submit=True, count_date="2026-10-01")
    assert out["status"] == "submitted" and out["lines"] == len(_items(sh))
    assert not con.execute("SELECT 1 FROM bc_stock_count_line WHERE ingredient_code='X-001'").fetchone()
    # the analyst's task names it (หน้างาน "ไม่มีหน่วยเล็กสุด N")
    it = {i["key"]: i for i in TD.build(con, "2026-09", None, None, today=date(2026, 10, 7))["items"]}["master_issues"]
    assert it["status"] == "todo" and "ไม่มีหน่วยฐาน 1" in it["detail"]


def test_a_refused_count_names_items_not_codes(con):  # noqa: F811
    sh = ST.sheet(con, 1, AS_OF)
    with pytest.raises(ValueError) as e:
        ST.save(con, 1, _all_lines(sh)[:1], "udon", submit=True, count_date="2026-10-01")
    msg = str(e.value)
    assert msg.startswith("ยังไม่ได้นับ") and not re.search(r"\d{2}-\d{2}-\d{3}", msg)


def test_a_count_unit_that_cannot_reach_the_base_unit_is_not_countable(con):  # noqa: F811
    from bom_center.units import Converter
    conv = Converter(con)
    assert ST.countable(conv, "13-01-009", "กรัม", "แพ็ค 1 กก.") is True
    assert ST.countable(conv, "13-01-009", "", "") is False
    assert ST.countable(conv, "13-01-009", "กรัม", "ช้อนโต๊ะ") is False


# ── 2 · the order's missing reason names the item ────────────────────────────
def test_the_missing_reason_names_the_item_never_the_code(con):  # noqa: F811
    p = OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    crab = next(r for r in p["rows"] if r["code"] == "13-01-009")
    _slip(con, "2026-10-05", hour=8)
    with pytest.raises(ValueError) as e:
        OR.save_flow(con, 1, "1", WED, [{"code": "13-01-009", "confirmed_units": crab["order_units"] * 3 + 5}], "udon",
                     submit=True, now=NOW, as_of=AS_OF)
    msg = str(e.value)
    assert "ปูอัด" in msg and "13-01-009" not in msg and msg.startswith("ต้องระบุเหตุผล 1 รายการก่อนส่ง")


# ── 3 · base units read as a bigger unit ─────────────────────────────────────
def test_readable_units():
    assert U.readable(18558, "กรัม") == (18.6, "กิโลกรัม")
    assert U.readable(1142, "มิลลิลิตร") == (1.14, "ลิตร")
    assert U.readable(850, "กรัม") == (850.0, "กรัม")                # under one kilo: as it is
    assert U.readable(12, "ชิ้น") == (12.0, "ชิ้น")
    assert U.readable_unit("กรัม") == ("กิโลกรัม", 1000.0) and U.readable_unit("แผ่น") == ("แผ่น", 1.0)


def test_an_order_line_without_a_pack_size_is_marked_and_kept_in_base_units(con):  # noqa: F811
    con.execute("UPDATE bc_ingredient SET qty_per_recv=NULL, recv_unit=NULL WHERE code='13-01-009'")
    con.commit()
    p = OR.plan(con, 1, "1", WED, as_of=AS_OF, now=NOW)
    crab = next(r for r in p["rows"] if r["code"] == "13-01-009")
    assert crab["order_unit"] == "base" and crab["no_pack"] is True and crab["no_pack_th"] == "ยังไม่มีขนาดแพ็ค"
    assert (crab["show_unit"], crab["show_factor"]) == ("กิโลกรัม", 1000.0)
    _slip(con, "2026-10-05", hour=8)
    out = OR.save_flow(con, 1, "1", WED, [{"code": "13-01-009", "confirmed_units": crab["order_units"]}], "udon",
                       submit=True, now=NOW, as_of=AS_OF)
    con.commit()
    d = OR.detail(con, out["order_id"])
    ln = next(x for x in d["lines"] if x["code"] == "13-01-009")
    assert ln["confirmed_units"] == crab["order_units"] and ln["no_pack"] and ln["show_unit"] == "กิโลกรัม"   # stored in grams
    c = HB.consolidated(con, [out["order_id"]])
    it = c["groups"][0]["items"][0]
    assert it["no_pack"] and it["show_factor"] == 1000.0 and it["total"] == crab["order_units"]
    from routers.bom_center import _xlsx_qty
    assert _xlsx_qty(18558, ln) == (18.558, "กิโลกรัม (ยังไม่มีขนาดแพ็ค)")
    assert _xlsx_qty(3, {"no_pack": False}) == (3, "แพ็ค")


# ── 4 · the branch start date ────────────────────────────────────────────────
def test_one_branch_start_date(con):  # noqa: F811
    assert SB.branch_start(con, 1) is None
    con.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(1, '2026-10-02', '1', 'suggested')")
    assert SB.branch_start(con, 1) is None                          # a draft is not a start
    _slip(con, "2026-10-05")
    assert SB.branch_start(con, 1) == "2026-10-05"
    con.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(1, '2026-10-03', '2', 'submitted')")
    assert SB.branch_start(con, 1) == "2026-10-03" and SB.started_by(con, 1, date(2026, 10, 3)) and not SB.started_by(con, 1, "2026-10-02")
    assert IR._started(con, 1) == "2026-10-03"                       # the slip log reads the same date


def test_rounds_before_the_start_are_not_judged(con):  # noqa: F811
    _slip(con, "2026-10-05")
    c = HB.compliance(con, "2026-09-22", "2026-10-06", branch_id=1)
    by = {(r["cycle_date"], r["round"]): r for r in c["rows"]}
    assert by[("2026-09-28", "1")]["status"] == "not_started" and by[("2026-09-28", "1")]["status_th"] == "ยังไม่เริ่มใช้ระบบ"
    assert by[("2026-10-05", "1")]["status"] in ("not_ordered", "upcoming")      # from the start on, a round is judged


def test_the_adoption_grid_is_neutral_before_the_start(con):  # noqa: F811
    con.execute("CREATE TABLE IF NOT EXISTS app_user(username TEXT PRIMARY KEY, role TEXT, branch TEXT, active INTEGER DEFAULT 1)")
    con.execute("INSERT INTO app_user(username, role, branch, active) SELECT 'br1', 'branch', name, 1 FROM branches WHERE branch_id=1")
    con.commit()
    g = AD.grid(con, today=date(2026, 10, 6))
    r = next(x for x in g["rows"] if x["branch_id"] == 1)
    assert r["status"] == "not_started" and r["status_th"] == "ยังไม่เริ่มใช้ระบบ" and g["counts"]["red"] == 0
    assert "ยังไม่เริ่มใช้ระบบ 1" in AD.summary_line(g)
    # the branch starts on the 5th: the rounds of 30/9-4/10 are not held against it
    _slip(con, "2026-10-05")
    con.commit()
    r = next(x for x in AD.grid(con, today=date(2026, 10, 6))["rows"] if x["branch_id"] == 1)
    assert r["status"] != "not_started" and r["orders"]["due"] <= 1


def test_a_month_that_ended_before_the_start_is_not_asked_for(con):  # noqa: F811
    _slip(con, "2026-10-05")                                          # the branch started in October: a slip and an order
    con.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(1, '2026-10-05', '1', 'submitted')")
    d = TD.build(con, "2026-09", None, None, today=date(2026, 10, 7))
    st = {s["key"]: s for s in d["close"]["steps"]}
    for k in ("deliveries", "begin_count", "end_count", "receipts_confirm", "issue_posted"):
        rows = st[k]["branches"]
        assert rows and all(b["status"] == "na" and b["not_started"] for b in rows), k
        assert st[k]["status"] == "na" and st[k]["count"] is None, k
    assert "เริ่มใช้ระบบวันที่ 5/10/2026" in st["begin_count"]["branches"][0]["detail"]
    # its own month: a branch that started mid-month begins with its first count of the month
    st = {s["key"]: s for s in TD.build(con, "2026-10", None, None, today=date(2026, 10, 7))["close"]["steps"]}
    b = st["begin_count"]["branches"][0]
    assert b["status"] == "todo" and "เริ่มใช้ระบบวันที่ 5/10/2026" in b["detail"]
    ST.save(con, 1, [{"code": "13-01-009", "qty": 1, "unit": "แพ็ค 1 กก."}], "u", submit=True, count_date="2026-10-06",
            require_all=False)
    st = {s["key"]: s for s in TD.build(con, "2026-10", None, None, today=date(2026, 10, 7))["close"]["steps"]}
    assert st["begin_count"]["branches"][0]["status"] == "done"


def test_a_day_with_a_slip_is_a_day_in_the_slip_summary(con):  # noqa: F811
    _slip(con, "2026-10-06")                                          # no sales that day yet (the POS comes a day late)
    s = IR.log(con, date(2026, 10, 6), date(2026, 10, 6), 1, today=date(2026, 10, 7))["summary"][0]
    assert (s["trading_days"], s["on_time"]) == (1, 1) and s["coverage_pct"] == 100.0


# ── 5 · the daily variance ───────────────────────────────────────────────────
def test_the_daily_variance_never_judges_today_nor_a_day_with_no_formula(con):  # noqa: F811
    for d in ("2026-10-04", "2026-10-05", "2026-10-06"):
        _slip(con, d)
    out = IR.daily(con, 1, "2026-10", today=date(2026, 10, 6))
    rows = {r["date"]: r for r in out["rows"]}
    assert rows["2026-10-04"]["judged"] and rows["2026-10-04"]["var_value"] is not None
    assert not rows["2026-10-05"]["judged"] or rows["2026-10-05"]["var_value"] is not None
    t6 = rows["2026-10-06"]                                            # today, and no sales yet: confirmed, never judged
    assert t6["confirmed"] and t6["today"] and t6["no_formula"] and t6["no_formula_th"] == "ยังไม่มีค่าตามสูตร"
    assert t6["var_value"] is None and t6["var_pct"] is None and t6["bom_value"] is None
    t = out["totals"]
    over = [r for r in out["rows"] if not r["today"] and (r["trading"] or r["slip_status"] != "missing")]
    assert t["trading_days"] == len(over) and t["confirmed_days"] == sum(1 for r in over if r["confirmed"])
    assert t["issued_value"] == pytest.approx(sum(r["issued_value"] for r in out["rows"] if r["judged"]), abs=0.01)
    assert all(it["var_value"] is None and it["signal"] is None for it in IR.daily_day(con, 1, "2026-10-06", today=date(2026, 10, 6))["rows"])
    # the branch started on the 4th: the days before it are not listed
    assert min(rows) == "2026-10-04"


# ── 6 · after the count ──────────────────────────────────────────────────────
def test_the_plan_names_the_next_count_and_when_it_is_due(con):  # noqa: F811
    assert [ST.next_full_date(x).isoformat() for x in ("2026-10-01", "2026-10-22", "2026-10-31", "2026-12-15")] == \
        ["2026-11-01", "2026-11-01", "2026-12-01", "2027-01-01"]
    p = ST.count_plan(con, 1, date(2026, 10, 2))
    assert p["first"] and p["next_full_due"] is True and p["last_full"] is None
    ST.save(con, 1, [{"code": "13-01-009", "qty": 1, "unit": "แพ็ค 1 กก."}], "u", submit=True, count_date="2026-10-01", require_all=False)
    p = ST.count_plan(con, 1, date(2026, 10, 2))
    assert p["last_full"] == {"kind": "begin", "date": "2026-10-01", "kind_th": "นับครั้งแรก"}
    assert p["next_full_date"] == "2026-11-01" and p["next_full_due"] is False
    assert ST.count_plan(con, 1, date(2026, 10, 31))["next_full_due"] is True        # due from the last day of the month


# ── 7 · one "ยังไม่เสร็จ" ────────────────────────────────────────────────────
def test_the_badges_total_is_the_open_task_list(con):  # noqa: F811
    for role in ("analyst", "salessupport", "accounting", "warehouse", "management", "branch"):
        mods = auth.modules_for_role(role)
        scoped = con.execute("SELECT name FROM branches WHERE branch_id=1").fetchone()[0] if role == "branch" else None
        d = TD.build(con, None, None, scoped, today=date(2026, 10, 7), viewer_role=role)
        mine = NB.mine_open(d, role, mods)
        b = NB.build(con, role, mods, scoped, today=date(2026, 10, 7))
        assert b["total"] == len(mine) == sum(b["pages"].values()), role
        assert all(it["state"] == "todo" and not it.get("upcoming") for it in mine), role
        if role in NB.CLOSE_ROLES:                                      # the close steps count for the roles that own them
            assert {s["key"] for s in mine if s.get("group") == "close"} == \
                {s["key"] for s in d["close"]["steps"] if s["state"] == "todo" and NB.is_mine(s, role, mods)}


def test_the_slip_days_left_behind_are_one_task(con):  # noqa: F811
    ST.save(con, 1, [{"code": "13-01-009", "qty": 1, "unit": "แพ็ค 1 กก."}], "u", submit=True, count_date="2026-10-01", require_all=False)
    name = con.execute("SELECT name FROM branches WHERE branch_id=1").fetchone()[0]
    d = TD.build(con, None, None, name, today=date(2026, 10, 7), viewer_role="branch")
    gaps = [i for i in d["items"] if i["key"].startswith("issue_missing_")]
    assert len(gaps) >= 2
    mine = NB.mine_open(d, "branch", auth.modules_for_role("branch"))
    kept = [i for i in mine if i["key"].startswith("issue_missing_")]
    assert len(kept) == 1 and kept[0]["slip_date"] == min(g["slip_date"] for g in gaps)


def test_the_todo_endpoint_carries_the_open_keys(client):
    j = client.get("/api/bom-center/todo").json()
    assert set(j["mine_open"]) == {"count", "keys"} and j["mine_open"]["count"] == len(j["mine_open"]["keys"])


@needs_node
def test_the_screens_keep_only_the_servers_open_tasks():
    core = (REPO / "js/01-core.js").read_text(encoding="utf-8")
    pre = "\n".join(re.search(r"(function %s\([\s\S]*?\n})" % n, core).group(1) for n in ("shmOpenKeys", "shmIsOpenTask"))
    out = subprocess.run([NODE, "-e", pre + "\nconst k = shmOpenKeys({ mine_open: { count: 1, keys: ['a'] } });"
                          "console.log(JSON.stringify([shmIsOpenTask({ key: 'a' }, k), shmIsOpenTask({ key: 'b' }, k),"
                          " shmIsOpenTask({ key: 'a', upcoming: true }, k), shmIsOpenTask({ key: 'b' }, shmOpenKeys({}))]))"],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    assert json.loads(out) == [True, False, False, True]
    for rel in ("js/18-launcher.js", "js/16-supplyhome.js", "js/09c-mobile.js"):
        src = (REPO / rel).read_text(encoding="utf-8")
        assert "shmOpenKeys(" in src and "shmIsOpenTask(" in src, rel


# ── 8 · a later cutoff is not a task of today ────────────────────────────────
def test_a_round_whose_cutoff_is_a_later_day_is_upcoming_and_never_a_badge(con):  # noqa: F811
    name = con.execute("SELECT name FROM branches WHERE branch_id=1").fetchone()[0]
    mods = auth.modules_for_role("branch")
    mon = date(2026, 10, 5)                                          # round 1's cutoff day (Wednesday's delivery)
    d = TD.build(con, None, None, name, today=mon, viewer_role="branch")
    due = {i["key"].split(":")[0]: i for i in d["items"] if i["key"].startswith("order_due_")}
    assert due["order_due_1"]["due_today"] and not due["order_due_1"]["upcoming"]
    later = [i for i in due.values() if i["cutoff_date"] > mon.isoformat()]
    assert later and all(i["upcoming"] and i["next_th"].startswith("ถัดไป: ") and "ส่งก่อน" in i["next_th"] for i in later)
    pages = NB.build(con, "branch", mods, name, today=mon)["pages"]
    assert pages.get("branchstock:order") == 1                         # today's round only
    keys = {i["key"] for i in NB.mine_open(d, "branch", mods)}
    assert all(i["key"] not in keys for i in later)
    # a cutoff that has passed and was not sent is still today's (late) duty
    d = TD.build(con, None, None, name, today=mon, now=datetime(2026, 10, 5, 15, 0, tzinfo=BKK), viewer_role="branch")
    o1 = next(i for i in d["items"] if i["key"].startswith("order_due_1"))
    assert o1["late"] and not o1["upcoming"]
    assert NB.build(con, "branch", mods, name, today=mon, now=datetime(2026, 10, 5, 15, 0, tzinfo=BKK))["pages"].get("branchstock:order") == 1


# ── the screens ──────────────────────────────────────────────────────────────
def _src(rel):
    return (REPO / rel).read_text(encoding="utf-8")


def test_the_branch_screens():
    brn, bmc = _src("js/14-branch.js"), _src("js/13-bom-center.js")
    # 1: a refusal of a branch save stays in the status line; the empty ladder line is gone
    assert "/^brn/.test(id || '')" in bmc and "function _brnLadderTxt(" in brn and "1 ${esc(it.count_unit)} = ${_bmcNum(it.per_count_unit)}" not in brn
    # 2: the reason is checked before the confirm dialog
    fn = brn[brn.index("async function brnOrderSave"):brn.index("async function brnOrderAct")]
    assert fn.index("_brnOrdMissingReasons(p)") < fn.index("uiConfirm(") and "_brnOrdFocusRow(" in fn
    # 3: base-unit lines in the bigger unit, with the chip
    assert "bmcNoPackChip(r)" in brn and "brnOrdInput(" in brn and "function bmcShowQty(" in bmc
    # 6: the finished count is a done state with a start button when due
    assert "function _brnCountDone(" in brn and "เริ่มนับของสิ้นเดือน" in brn and 'id="brnDone"' in _src("tabs/53-branchstock.html")


@needs_node
def test_the_status_line_keeps_a_branch_refusal():
    pre = ("const _BMC_STATUS_FIELD = " + re.search(r"const _BMC_STATUS_FIELD = (.*?);\n", _src("js/13-bom-center.js")).group(1) + ";\n"
           + re.search(r"(function _bmcStatusKind\([\s\S]*?\n})", _src("js/13-bom-center.js")).group(1))
    out = subprocess.run([NODE, "-e", pre + "\nconsole.log(JSON.stringify([_bmcStatusKind('brnStatus', 'ไม่สำเร็จ: ยังไม่ได้นับ 17 รายการ', 'var(--bad-ink)'),"
                          " _bmcStatusKind('bmcX', 'ไม่สำเร็จ: เครือข่ายขัดข้อง', 'var(--bad-ink)')]))"],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    assert json.loads(out) == [{"toast": "error", "inline": "field"}, {"toast": "error", "inline": "clear"}]


@needs_node
def test_bmc_show_qty():
    src = _src("js/13-bom-center.js")
    pre = ("function _bmcNum(v, d = 2) { return Number(v).toLocaleString('en-US', { maximumFractionDigits: d }); }\n"
           + re.search(r"(function bmcShowQty\([\s\S]*?\n})", src).group(1))
    out = subprocess.run([NODE, "-e", pre + "\nconst r = { no_pack: true, show_unit: 'กิโลกรัม', show_factor: 1000 };"
                          "console.log(JSON.stringify([bmcShowQty(18558, r), bmcShowQty(500, r), bmcShowQty(3, { no_pack: false })]))"],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    a, b, c = json.loads(out)
    assert a == {"q": 18.6, "unit": "กิโลกรัม", "f": 1000} and b["f"] == 1 and c["f"] == 1


def test_the_next_count_across_the_year_end():
    """A count dated the last day of December begins January: the next one is the 1st of February."""
    assert ST.next_full_date(date(2026, 12, 31)) == date(2027, 2, 1) and ST.next_full_date(date(2026, 12, 30)) == date(2027, 1, 1)


import pytest as _pytest_s3  # noqa: E402
pytestmark = _pytest_s3.mark.usefixtures("recipe_mode")   # PR-S3: this module builds its forecast, plans and count sheets from recipes (mode full)
