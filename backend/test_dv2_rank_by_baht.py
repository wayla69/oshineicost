# -*- coding: utf-8 -*-
"""DV2 (phone table review 2026-10-25): tables rank by baht.

  * ราคารายเดือน: /api/bom-center/prices carries the price the month before, the change in %, the quantity bought and the baht effect
    (impact_baht = (price - prev_price) x qty, the month's purchases or else the month before's); the other callers of month_table are
    unchanged (no new keys unless asked);
  * บิลยกเลิก รายสาขา: branches by value, not by count;
  * สรุปประจำวัน: every branch carries vs_normal_baht (the day minus its normal: the median of the same weekday over 8 weeks)
    and the list ranks by it, most below first; the top
    bar's branch narrows it.
"""
import sqlite3

import pytest

import conftest as _cf
from bom_center import prices as P
from bom_center import schema as S


@pytest.fixture
def con(tmp_path):
    p = tmp_path / "dv2.db"
    _cf._build_db(str(p))
    c = sqlite3.connect(str(p))
    S.ensure(c, commit=True)
    c.executemany("INSERT INTO bc_ingredient(code, name_th, kind, base_unit) VALUES(?,?,?,?)",
                  [("02-01-001", "แซลมอน", "raw", "กรัม"), ("01-01-040", "ข้าวญี่ปุ่น", "raw", "กรัม"),
                   ("13-01-009", "ปูอัด", "raw", "กรัม")])
    c.executemany("INSERT INTO bc_ingredient_price(ingredient_code, month, price_per_base, qty_bought, value_bought, source)"
                  " VALUES(?,?,?,?,?,'purchase_avg')",
                  [("02-01-001", "2026-09", 0.40, 10000, 4000), ("02-01-001", "2026-10", 0.45, 12000, 5400),   # +12.5 % on 12 kg
                   ("01-01-040", "2026-09", 0.05, 50000, 2500), ("01-01-040", "2026-10", 0.05, 0, 0),         # no change, no Oct qty
                   ("13-01-009", "2026-09", 0.20, 3000, 600)])                                                # carried, no Oct row
    c.commit()
    yield c
    c.close()


def test_price_table_change_and_baht_effect(con):
    rows = {r["code"]: r for r in P.month_table(con, "2026-10", with_change=True)}
    s = rows["02-01-001"]
    assert s["prev_price"] == pytest.approx(0.40) and s["change_pct"] == pytest.approx(12.5)
    assert s["qty"] == pytest.approx(12000) and s["qty_month"] == "2026-10"
    assert s["impact_baht"] == pytest.approx((0.45 - 0.40) * 12000)
    r = rows["01-01-040"]                                   # no purchase in October: the quantity of September, no change -> 0 baht
    assert r["qty_month"] == "2026-09" and r["change_pct"] == 0 and r["impact_baht"] == 0
    c = rows["13-01-009"]                                   # a carried price: the same price both months
    assert c["prev_price"] == pytest.approx(c["price_per_base"]) and c["change_pct"] == 0


def test_price_table_other_callers_unchanged(con):
    for r in P.month_table(con, "2026-10"):
        assert not {"prev_price", "change_pct", "qty", "impact_baht"} & set(r)


def test_prev_month_wraps_the_year():
    assert P.prev_month("2026-01") == "2025-12" and P.prev_month("2026-10") == "2026-09"


def test_cancel_bills_branches_by_value(client):
    d = client.get("/api/extra/cancel-bills").json()
    amounts = [b["amount"] or 0 for b in d["branches"]]
    assert amounts == sorted(amounts, reverse=True)


def test_scorecard_ranks_by_baht_below_normal_and_follows_branch(client):
    d = client.get("/api/home/branch-scorecard").json()
    rows = d["branches"]
    assert rows
    for r in rows:
        assert "vs_normal_baht" in r
        if r["vs_normal_baht"] is not None:
            assert r["vs_normal_baht"] == pytest.approx(r["revenue"] - r["normal"], abs=1.0)
    vals = [r["vs_normal_baht"] for r in rows if r["vs_normal_baht"] is not None]
    assert vals == sorted(vals)                              # most below its normal first
    one = rows[0]["branch"]
    d1 = client.get("/api/home/branch-scorecard", params={"branch": one}).json()
    assert [r["branch"] for r in d1["branches"]] == [one]
