# -*- coding: utf-8 -*-
"""2026-10-31 (owner) · บิลยกเลิก / พิมพ์ซ้ำ drill-down (routers/voids.py): every branch -> one branch's bills -> one bill, on the
ตรวจวันนี้ card's own counts and windows (routers/mgt_watch.voids)."""
import contextlib
import datetime as dt
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

import auth
from routers import mgt_watch as W
from routers import voids as V
from test_mgt_watch import CE, CS, WHERE, _db, _detail

ROOT = Path(__file__).resolve().parent.parent


def _seed(con):
    # branch 1: voids on 20 May (before the window), 2 and 3 June (inside), 4 June (the day after the window); branch 2: none,
    # one discounted bill printed 3 times on 2 June
    _detail(con, [(1, "2026-05-20T12:00:00", 100), (1, "2026-06-02T12:00:00", 300), (1, "2026-06-03T19:00:00", 200),
                  (1, "2026-06-04T12:00:00", 900)])
    con.execute("UPDATE pos_cancel_bill_detail SET reason_id=0, discount=40 WHERE sale_date='2026-06-03T19:00:00'")
    con.execute("INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,food_subtotal,drink_subtotal,other_subtotal,discount,"
                "service_charge,total_inc_vat,print_times,cashier,source) VALUES(2,'RP-1','2026-06-02T12:30:00',2,900,100,0,50,0,1070,3,'แคชเชียร์ ก','Easy')")
    con.commit()


def _l1(con, ids, k):
    card = W.voids(con, ids, CS, CE, WHERE, {})["windows"][k]
    s, e = (dt.date.fromisoformat(x) for x in card["range"])
    return card, V._level1(con, ids, s, e, WHERE, {}, W._detail_cover(con), k)


@pytest.mark.parametrize("k", ["day", "week", "half", "month"])
def test_level1_lists_every_branch_sorted_with_the_card_numbers(tmp_path, k):
    con, ids = _db(tmp_path)
    _seed(con)
    card, (source, rows, total) = _l1(con, ids, k)
    assert source == card["source"] == "detail"
    assert sorted(r["branch"] for r in rows) == sorted(ids.values())          # every branch, the one with no void too
    assert [r["voids"] == 0 for r in rows] == sorted(r["voids"] == 0 for r in rows)   # no-void branches at the bottom
    rates = [r["rate_pct"] or 0 for r in rows if r["voids"]]
    assert rates == sorted(rates, reverse=True)
    by = {r["branch"]: r for r in rows}
    for c in card["rows"]:                                                    # one answer: the card's rows are the same numbers
        r = by[c["branch"]]
        assert (r["voids"], r["amount"], r["rate_pct"], r["level"]) == (c["bills"], c["amount"], c["rate_pct"], c["level"])
    for c in card["reprints"]:
        assert (by[c["branch"]]["reprints"], by[c["branch"]]["reprint_baht"]) == (c["bills"], c["baht"])
    assert total["voids"] == sum(r["voids"] for r in rows) and total["branches"] == len(rows)
    assert {r["type"] for r in rows} == {"master", "franchise"}


def test_level1_month_window_equals_the_card_rate(tmp_path):
    con, ids = _db(tmp_path)
    _seed(con)
    card, (_, rows, _) = _l1(con, ids, "month")
    b1 = next(r for r in rows if r["branch"] == ids[1])
    assert b1["voids"] == 2 and b1["amount"] == 500 and b1["rate_pct"] == round(2 / (3 + 2) * 100, 2)   # 4 June is outside
    assert card["rows"][0]["rate_pct"] == b1["rate_pct"]
    b2 = next(r for r in rows if r["branch"] == ids[2])
    assert b2["voids"] == 0 and b2["level"] == "ok" and b2["reprints"] == 1 and b2["reprint_baht"] == 50


def test_level2_lists_the_window_bills_only_with_reasons_and_reprints(tmp_path):
    con, ids = _db(tmp_path)
    _seed(con)
    cover = W._detail_cover(con)
    source, l2 = V._level2(con, 1, ids[1], CS, CE, WHERE, {}, cover, "month")
    assert source == "detail"
    assert [b["at"] for b in l2["bills"]] == ["2026-06-03T19:00:00", "2026-06-02T12:00:00"]   # newest first; 20 May and 4 June left out
    assert l2["bills"][0]["reason"] == "ไม่ระบุเหตุผล" and l2["bills"][0]["discount"] == 40
    assert l2["bills"][1]["reason"] == "คีย์ข้อมูลผิด" and l2["bills"][1]["user"] == "cashier"
    assert sorted((r["reason"], r["n"]) for r in l2["reasons"]) == [("คีย์ข้อมูลผิด", 1), ("ไม่ระบุเหตุผล", 1)]
    H = l2["header"]
    assert H["voids"] == 2 and H["rate_pct"] == round(2 / 5 * 100, 2)
    assert l2["reprints"] == [] and l2["bills_more"] == 0
    # the reprint definition: a discounted bill printed more than once (branch 2)
    _, l2b = V._level2(con, 2, ids[2], CS, CE, WHERE, {}, cover, "month")
    assert l2b["reprints"] == [{"at": "2026-06-02T12:30:00", "receipt": "RP-1", "no": "RP-1", "total": 1070, "discount": 50, "prints": 3, "cashier": "แคชเชียร์ ก"}]
    assert l2b["bills"] == [] and l2b["header"]["reprints"] == 1


def test_the_endpoint_answers_both_levels_on_the_card_anchor(tmp_path, monkeypatch):
    con, ids = _db(tmp_path)
    _seed(con)
    monkeypatch.setattr(V, "get_db", lambda: contextlib.nullcontext(con))
    fn = V.report_voids.__wrapped__                                       # the uncached body (the cache is the route's business)
    j1 = fn(window="week", month="2026-06", date=None, branch=None, group=None, lang="th")
    assert j1["level"] == 1 and j1["anchor"] == "2026-06-03" and j1["range"] == ["2026-05-28", "2026-06-03"]
    assert j1["windows"] == ["day", "week", "half", "month"]
    j2 = fn(window="week", month="2026-06", date=j1["anchor"], branch=ids[1], group=None, lang="th")
    assert j2["level"] == 2 and j2["branch"] == ids[1] and j2["range"] == j1["range"]
    assert j2["header"]["rate_pct"] == next(r for r in j1["rows"] if r["branch"] == ids[1])["rate_pct"]


def test_the_rule_and_the_branch_pin(monkeypatch):
    assert any(p == "/api/reports/voids" and m == {"report.management"} for (_, p, m) in auth.MODULE_RULES)
    for path in ("/api/reports/voids", "/api/reports/voids/bill"):
        assert auth.path_allowed("GET", path, auth.ROLE_MODULES["management"])
        for role in ("branch", "franchise", "operation", "analyst"):          # report.management only (the stricter rule)
            assert not auth.path_allowed("GET", path, auth.ROLE_MODULES[role]), (role, path)
    monkeypatch.setattr(auth, "scope_branch", lambda request: "BranchA")
    with pytest.raises(HTTPException) as e:
        V._pin(None, "BranchB")
    assert e.value.status_code == 403
    assert V._pin(None, None) == "BranchA" and V._pin(None, "BranchA") == "BranchA"


def test_the_route_is_cached_and_the_bill_has_its_lines(client):
    import core
    q = "/api/reports/voids?window=month&month=2026-06&lang=th"
    a = client.get(q)
    assert a.status_code == 200
    j = a.json()
    assert j["available"] and j["level"] == 1 and {r["branch"] for r in j["rows"]} >= {"BranchA", "BranchB"}   # + any active branch other tests added
    hits = core._cache_stats["hits"]
    b = client.get(q)
    assert core._cache_stats["hits"] == hits + 1 and json.loads(b.content) == j
    d = client.get("/api/reports/voids/bill", params={"branch": "BranchA", "receipt": "2026-06-02-1001-1"}).json()
    assert d["available"] and d["receipt"] == "2026-06-02-1001-1" and d["payments"] == [{"code": "Cash", "amount": 1070}]
    assert client.get("/api/reports/voids/bill", params={"branch": "BranchB", "receipt": "2026-06-02-1001-1"}).json()["available"] is False


def test_the_phone_opens_the_drill_from_the_card_row():
    mob = (ROOT / "js" / "09c-mobile.js").read_text(encoding="utf-8")
    idx = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "voids: { v: 'voids'" in mob and "onGo: it.key === 'voids' ? 'msOpenVoids()'" in mob
    assert "_MS_VIEWS = ['summary', 'sales', 'branches', 'menu', 'profit', 'discounts', 'voids']" in mob
    assert "history.pushState({ phd: 'voids', vb: name }" in mob and "/api/reports/voids/bill?" in mob
    assert 'id="msv-voids"' in idx and 'id="msVoids"' in idx


# ── 2026-10-31 (owner): a void followed by a new bill is a correction - the alert counts the voids NOT re-issued ──────────────
def test_the_reissue_rule():
    import rules
    v = {"amount": 1000, "table": "", "customers": 0}
    assert rules.void_reissue_match(v, {"amount": 1150, "table": "", "customers": 0})           # within ±20 %
    assert not rules.void_reissue_match(v, {"amount": 1300, "table": "", "customers": 0})
    assert rules.void_reissue_match({**v, "table": "12"}, {"amount": 50, "table": "12"})        # same table wins over the amount
    assert not rules.void_reissue_match({**v, "table": "12"}, {"amount": 1000, "table": "7"})
    assert rules.void_reissue_match({**v, "customers": 4}, {"amount": 9, "customers": 4})      # same customers when both carry one
    assert not rules.void_reissue_match({**v, "customers": 4}, {"amount": 1000, "customers": 3})
    assert rules.VOID_REISSUE_MIN == 30


def _reissue_db(tmp_path):
    con, ids = _db(tmp_path)
    con.execute("CREATE TABLE pos_cancel_bill_detail(branch_id INT, period_month TEXT, sale_date TEXT, receipt_no TEXT, reason_id INT, "
                "amount REAL, cancel_user TEXT, discount REAL, customers INT, table_no TEXT)")
    rows = [(1, "2026-05-20T12:00:00", "OLD", 100, 0, None),
            (1, "2026-06-02T11:40:00", "V1", 2000, 0, "12"),    # T5 (table 12, 20 min later) re-issues it
            (1, "2026-06-02T11:29:00", "V2", 2000, 0, "9"),     # T6 (table 9) comes 31 min later: not re-issued
            (1, "2026-06-03T12:00:00", "V3", 1100, 0, None),    # no table: the fixture's 1,070 bill at 12:00 is within 20 %
            (1, "2026-06-03T18:00:00", "V4", 500, 0, None)]     # nothing after it: not re-issued
    con.executemany("INSERT INTO pos_cancel_bill_detail VALUES(?,?,?,?,2,?,'cashier',0,?,?)",
                    [(b, d[:7], d, r, a, c, t) for b, d, r, a, c, t in rows])
    for rc, at, tab in (("T5", "2026-06-02T12:00:00", "12"), ("T6", "2026-06-02T12:00:00", "9")):
        con.execute("INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,table_no,customer_count,food_subtotal,drink_subtotal,"
                    "other_subtotal,discount,service_charge,total_inc_vat,source) VALUES(1,?,?,?,2,500,0,0,0,0,535,'Easy')", (rc, at, tab))
    con.commit()
    return con, ids


def test_classify_marks_the_reissued_voids(tmp_path):
    import rules
    con, ids = _reissue_db(tmp_path)
    cls = rules.classify_voids(con, [1, 2], CS, CE)
    by = {r[1]: cls[r[0]] for r in con.execute("SELECT rowid, receipt_no FROM pos_cancel_bill_detail WHERE sale_date >= '2026-06'")}
    assert by["V1"] == "T5" and by["V2"] is None and by["V4"] is None
    assert by["V3"] == "2026-06-03-1001-1"                          # the fixture's bill (receipt "<day>-<bid>001-<bid>")


def test_the_card_counts_only_the_voids_not_reissued(tmp_path):
    con, ids = _reissue_db(tmp_path)
    m = W.voids(con, ids, CS, CE, WHERE, {})["windows"]["month"]
    r = next(x for x in m["rows"] if x["branch"] == ids[1])
    assert (r["bills"], r["voids_open"], r["voids_reissued"]) == (4, 2, 2)              # every void stays in `bills`
    tot = 3 + 2 + 4                                                                       # 3 fixture bills + T5/T6 + the 4 voids
    assert r["rate_pct"] == round(4 / tot * 100, 2) and r["rate_open_pct"] == round(2 / tot * 100, 2)
    assert r["level"] == W.void_level(2 / tot * 100) and r["amount_open"] == 2500
    assert "ยกเลิกแล้วไม่ออกใหม่" in m["line"] and "รวมยกเลิกทั้งหมด 4" in m["line"]
    # level 1 = the card, level 2 = level 1, the report's §7.1 = level 1
    cover = W._detail_cover(con)
    _, rows, _ = V._level1(con, ids, CS, CE, WHERE, {}, cover, "month")
    l1 = next(x for x in rows if x["branch"] == ids[1])
    assert (l1["voids"], l1["voids_open"], l1["rate_open_pct"], l1["level"]) == (r["bills"], r["voids_open"], r["rate_open_pct"], r["level"])
    _, l2 = V._level2(con, 1, ids[1], CS, CE, WHERE, {}, cover, "month")
    assert l2["header"]["voids_open"] == l1["voids_open"] and sum(1 for b in l2["bills"] if not b["reissued_as"]) == l1["voids_open"]
    assert {b["receipt"]: b["reissued_as"] for b in l2["bills"]}["V1"] == "T5"
    sec = V.report_section(con, CS, CE, WHERE, {}, whole_month=False)
    assert sec["branches"]
    for b in sec["branches"]:
        x = next(y for y in rows if y["branch"] == b["branch"])
        assert (b["voids"], b["voids_open"], b["amount"], b["rate_open_pct"], b["reprints"]) == \
               (x["voids"], x["voids_open"], x["amount"], x["rate_open_pct"], x["reprints"])


def test_the_report_section_caps_the_lists_and_names_the_rest(tmp_path):
    con, ids = _db(tmp_path)
    _detail(con, [(1, f"2026-06-02T{10 + i // 60:02d}:{i % 60:02d}:00", 999 + i) for i in range(25)] + [(1, "2026-05-20T12:00:00", 1)])
    sec = V.report_section(con, CS, CE, WHERE, {}, cap=20)
    b = sec["branches"][0]
    assert len(b["bills"]) == 20 and b["bills_more"] == 5 and sec["rest"]["branches"] == 1     # branch 2 has neither
    full = V.report_section(con, CS, CE, WHERE, {}, cap=None)
    assert len(full["branches"][0]["bills"]) == 25 and full["branches"][0]["bills_more"] == 0


def test_the_franchise_finding_uses_the_open_count(tmp_path):
    from routers import franchise as F
    con, ids = _reissue_db(tmp_path)
    v = F.void_open(con, ids[1], "2026-06")
    assert v["voids_open"] == 2 and v["voids_reissued"] == 2
    flags = F._control_flags({"pct": None}, {"bills": 4, "baht": 5600, "rate_pct": 40.0, **v}, {"days": []})
    item = next(f for f in flags["items"] if f["key"] == "voids")
    assert "ไม่ออกใหม่ 2 ใบ" in item["text"] and "รวมยกเลิกทั้งหมด 4 ใบ" in item["text"]
    assert F.void_open(con, ids[1], "2026-04") is None                 # the per-bill table does not cover April
