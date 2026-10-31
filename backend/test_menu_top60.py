"""60 เมนูที่สั่งเยอะ (owner 2026-10-31, phone เมนู detail) · /api/sales/top_ordered?windows=1.

- three windows at once: week = the 7 days ending on the anchor vs the 7 before, half = the 15 vs the 15 before, month = days 1..n of
  the anchor's month vs days 1..n of the month before (a finished month: whole vs whole);
- top 60 dishes by plates of the window; one row per dish (SKU spellings fold), (A)/(B) stripped;
- per100 = plates / customers x 100: a buffet dish on the buffet customers (menu_fact_total), every other dish on all customers;
- a dish without a plate in the window before: prev_qty 0, no prev_per100, no delta_pct (the phone shows ใหม่);
- non-food lines (deposits, fees, packages) are in no row and no total; the branch filter is honoured;
- the web answer is cached (the hits grow on the second call).
"""
import datetime as dt
import json
import sqlite3
from contextlib import contextmanager

import pytest

import core
from routers import sales

ANCHOR = dt.date(2026, 9, 20)
FILLERS = 70


def _db():
    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript("""
      CREATE TABLE branches(branch_id INTEGER PRIMARY KEY, name TEXT, branch_type TEXT, active INT DEFAULT 1);
      CREATE TABLE skus(sku_id INTEGER PRIMARY KEY, sku_code TEXT, description TEXT);
      CREATE TABLE sales_sku_daily(branch_id INT, day TEXT, sku_id INT, qty REAL, subtotal_ex_vat REAL);
      CREATE INDEX ix_ssd ON sales_sku_daily(branch_id, day, sku_id);
      CREATE TABLE sales_transaction(branch_id INT, receipt_no TEXT, sold_at TEXT, customer_count INT, source TEXT,
        total_inc_vat REAL, service_charge REAL);
      CREATE TABLE menu_fact_total(branch_id INT, day TEXT, tier TEXT, pax REAL);
      INSERT INTO branches(branch_id, name, branch_type) VALUES (1, 'สาขาเอ', 'master'), (2, 'สาขาบี', 'franchise');
      INSERT INTO skus VALUES (1, 'S1', '(B) แซลมอน'), (2, 'S2', '(B) แซลมอน '), (3, 'R', '(A) ข้าวผัด'),
        (4, 'DEP', 'ค่ามัดจำจาน'), (5, 'M', '(A) ราเมน');
    """)
    for i in range(FILLERS):
        con.execute("INSERT INTO skus VALUES (?, ?, ?)", (100 + i, f"F{i}", f"(A) เมนูเสริม {i:02d}"))
    d = dt.date(2026, 8, 1)
    while d <= ANCHOR:
        day = d.isoformat()
        for bid in (1, 2):
            con.execute("INSERT INTO sales_transaction VALUES (?, ?, ?, 100, 'Easy', 10700, 0)", (bid, f"R{bid}{day}", day + " 12:00:00"))
            con.execute("INSERT INTO menu_fact_total VALUES (?, ?, '499', 60)", (bid, day))
        recent = d >= dt.date(2026, 9, 14)
        rows = [(1, 1, 10), (2, 2, 5), (1, 4, 50), (1, 5, 3 if recent else 2)]
        if recent:
            rows.append((1, 3, 4))                       # ข้าวผัด: first sold on 14 Sep
            rows += [(2, 100 + i, 1) for i in range(FILLERS)]
        for bid, sid, q in rows:
            con.execute("INSERT INTO sales_sku_daily VALUES (?, ?, ?, ?, 0)", (bid, day, sid, q))
        d += dt.timedelta(days=1)
    return con


def _run(branch=None, month=None, anchor=ANCHOR):
    bf, pp = (" AND b.name=:branch", {"branch": branch}) if branch else ("", {})
    return sales._top_ordered_windows(_db(), month, bf, pp, anchor=anchor.isoformat() if anchor else None)


def _item(w, menu):
    return next((x for x in w["items"] if x["menu"] == menu), None)


def test_three_windows_with_their_ranges():
    r = _run()
    assert r["anchor"] == "2026-09-20"
    W = r["windows"]
    assert (W["week"]["range"], W["week"]["prev_range"]) == (["2026-09-14", "2026-09-20"], ["2026-09-07", "2026-09-13"])
    assert (W["half"]["range"], W["half"]["prev_range"]) == (["2026-09-06", "2026-09-20"], ["2026-08-22", "2026-09-05"])
    assert (W["month"]["range"], W["month"]["prev_range"]) == (["2026-09-01", "2026-09-20"], ["2026-08-01", "2026-08-20"])
    assert W["week"]["label"] == "14–20 ก.ย." and W["week"]["prev_label"] == "7–13 ก.ย."
    assert (W["week"]["days"], W["half"]["days"], W["month"]["days"]) == (7, 15, 20)


def test_top_60_by_plates_one_row_per_dish():
    w = _run()["windows"]["week"]
    assert len(w["items"]) == 60 and w["dishes"] == 3 + FILLERS
    qs = [x["qty"] for x in w["items"]]
    assert qs == sorted(qs, reverse=True)
    assert [x["menu"] for x in w["items"][:3]] == ["แซลมอน", "ข้าวผัด", "ราเมน"]
    s = w["items"][0]
    assert (s["qty"], s["kind"], s["basis"]) == (105, "buffet", "buffet")          # two SKU spellings, two branches: (10 + 5) x 7


def test_per_head_on_the_buffet_or_all_customers():
    w = _run()["windows"]["week"]
    assert (w["customers"], w["prev_customers"], w["buffet_customers"]) == (1400, 1400, 840)
    s, m = _item(w, "แซลมอน"), _item(w, "ราเมน")
    assert (s["per100"], s["prev_per100"], s["delta_pct"]) == (12.5, 12.5, 0.0)        # 105 / 840 buffet customers
    assert (m["basis"], m["qty"], m["prev_qty"], m["per100"], m["prev_per100"], m["delta_pct"]) == ("all", 21, 14, 1.5, 1.0, 50.0)
    assert (w["total_qty"], w["prev_total_qty"], w["per100"], w["prev_per100"]) == (644, 119, 46.0, 8.5)   # 105 + 28 + 21 + 70 fillers x 7
    assert w["delta_pct"] == 441.2
    mo = _run()["windows"]["month"]
    assert _item(mo, "ราเมน")["qty"] == 13 * 2 + 7 * 3 and _item(mo, "ราเมน")["prev_qty"] == 40


def test_a_dish_new_in_the_window_has_no_change():
    for k in ("week", "half", "month"):
        n = _item(_run()["windows"][k], "ข้าวผัด")
        assert n["prev_qty"] == 0 and n["prev_per100"] is None and n["delta_pct"] is None
        assert n["qty"] == 28 and n["per100"] == round(28 / _run()["windows"][k]["customers"] * 100, 2)


def test_non_food_lines_are_out():
    r = _run()
    for w in r["windows"].values():
        assert not any("มัดจำ" in x["menu"] for x in w["items"])
    assert r["windows"]["week"]["total_qty"] == 644                                  # 50 deposits a day are not plates


def test_branch_filter():
    w = _run(branch="สาขาเอ")["windows"]["week"]
    assert w["customers"] == 700 and w["buffet_customers"] == 420
    assert _item(w, "แซลมอน")["qty"] == 70 and _item(w, "แซลมอน")["per100"] == round(70 / 420 * 100, 2)
    assert not any(x["menu"].startswith("เมนูเสริม") for x in w["items"])            # the fillers sell at สาขาบี only


def test_a_finished_month_compares_whole_months_and_caps_the_anchor():
    r = _run(month="2026-08")
    W = r["windows"]
    assert r["anchor"] == "2026-08-31"
    assert (W["month"]["range"], W["month"]["prev_range"]) == (["2026-08-01", "2026-08-31"], ["2026-07-01", "2026-07-31"])
    assert W["week"]["range"] == ["2026-08-25", "2026-08-31"]
    assert W["month"]["prev_customers"] == 0 and W["month"]["prev_per100"] is None


@pytest.fixture
def patched_db(monkeypatch):
    con = _db()

    @contextmanager
    def fake():
        yield con
    monkeypatch.setattr(sales, "get_db", fake)
    core.invalidate_cache("_top_ordered_windows_cached")
    yield con
    core.invalidate_cache("_top_ordered_windows_cached")


def test_the_route_answers_the_windows_and_is_cached(patched_db):
    kw = dict(month=None, month_from=None, month_to=None, branch=None, group=None, sort="qty", limit=15)
    a = sales.top_ordered_route(windows=1, **kw)
    hits = core._cache_stats["hits"]
    b = sales.top_ordered_route(windows=1, **kw)
    assert core._cache_stats["hits"] == hits + 1 and a.body == b.body
    d = json.loads(a.body)
    assert d["anchor"] == "2026-09-20"                                               # the last finished day with its bills in
    assert set(d["windows"]) == {"week", "half", "month"} and len(d["windows"]["week"]["items"]) == 60
    g = json.loads(sales.top_ordered_route(windows=1, **{**kw, "group": "master"}).body)
    assert g["windows"]["week"]["customers"] == 700                                  # group= scopes like branch=
