"""PR-SPHT · /api/analysis/sph `tiers`: customers by Grand Menu price tier (owner 2026-10-25).

- the window is clipped to on/after 1 Sep 2026 (GRAND_MENU_FROM) and says so (`clipped`);
- a period that ends before 1 Sep 2026 answers tiers = null with a reason;
- rows in the spend bar's order (฿459 → ฿1,299, then โปรฯ, เด็ก, ไม่ใช่บุฟเฟ่ต์); shares sum to 100 %;
- revenue on the card's basis: menu_fact_total.rev / SERVICE_CHARGE_MULT (owner option A), compared with net_to_base(price);
- ไม่ใช่บุฟเฟ่ต์ = every bill/customer of the card minus the buffet tiers, floored at 0;
- facts that lag the sales -> covered days only, `data_to` + `lagging`;
- branch and group (master | franchise) filters.
"""
import inspect
import sqlite3
from contextlib import contextmanager

import pytest

import rules
from routers import analysis as A

SC = rules.SERVICE_CHARGE_MULT
VAT = rules.VAT_DIVISOR


def _db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
      CREATE TABLE branches(branch_id INTEGER PRIMARY KEY, name TEXT, branch_type TEXT);
      CREATE TABLE sales_transaction(id INTEGER PRIMARY KEY, branch_id INT, sold_at TEXT, customer_count INT,
                                     total_inc_vat REAL, service_charge REAL, source TEXT);
      CREATE TABLE menu_fact_total(branch_id INT, day TEXT, tier TEXT, bills INT, pax REAL, cogs REAL, rev REAL, hc REAL,
                                   PRIMARY KEY(branch_id, day, tier));
      INSERT INTO branches VALUES (1, 'สาขาเอ', 'master'), (2, 'สาขาบี', 'franchise'), (9, 'ALL', 'master');
    """)

    def bills(bid, day, n, pax, net_ex):
        """n bills of `pax` customers, each net_ex ex VAT ex service charge (service charge 5 % on top)."""
        for _ in range(n):
            sc = net_ex * (SC - 1)
            con.execute("INSERT INTO sales_transaction(branch_id, sold_at, customer_count, total_inc_vat, service_charge, source)"
                        " VALUES (?,?,?,?,?, 'Easy')", (bid, f"{day} 19:00:00", pax, (net_ex + sc) * VAT, sc))

    def fact(bid, day, tier, nb, pax, rev_per_head_base):
        # menu_fact_total.rev = Sale.Total / 1.07 -> still carries the 5 % service charge
        con.execute("INSERT INTO menu_fact_total VALUES (?,?,?,?,?,0,?,0)",
                    (bid, day, tier, nb, pax, rev_per_head_base * pax * SC))

    # August (old menu): must never show up
    bills(1, "2026-08-20", 10, 2, 600.0)
    fact(1, "2026-08-20", "590", 10, 20, 600.0)
    # September, branch 1 (master): 789 x 10 bills / 30 pax at ฿720/head, 999 x 5 / 10 at ฿889.19, promo 2 / 4, + 4 a-la-carte bills
    bills(1, "2026-09-05", 10, 3, 720.0 * 3)
    bills(1, "2026-09-05", 5, 2, 889.19 * 2)
    bills(1, "2026-09-05", 2, 2, 500.0 * 2)
    bills(1, "2026-09-05", 4, 2, 300.0 * 2)
    fact(1, "2026-09-05", "789", 10, 30, 720.0)
    fact(1, "2026-09-05", "999", 5, 10, 889.19)
    fact(1, "2026-09-05", "promo", 2, 4, 500.0)
    # October, branch 2 (franchise): 459 x 4 / 8 ; sales also on 20 Oct but the facts stop on 10 Oct
    bills(2, "2026-10-10", 4, 2, 420.0 * 2)
    bills(2, "2026-10-10", 1, 1, 200.0)
    fact(2, "2026-10-10", "459", 4, 8, 420.0)
    bills(2, "2026-10-20", 6, 2, 420.0 * 2)
    return con


def _rows(t):
    return {r["tier"]: r for r in t["rows"]}


def test_a_period_before_1_sep_2026_has_no_tiers():
    t, why = A._sph_tiers(_db(), month_from="2026-07", month_to="2026-08")
    assert t is None and why == "before_grand_menu"


def test_the_window_is_clipped_to_1_sep_and_the_old_menu_never_appears():
    t, why = A._sph_tiers(_db(), month_from="2026-08", month_to="2026-09")
    assert why is None
    assert t["from"] == rules.GRAND_MENU_FROM and t["clipped"] is True
    assert t["to"] == "2026-09-05" and t["data_to"] == "2026-09-05" and t["lagging"] is False
    tiers = [r["tier"] for r in t["rows"]]
    assert tiers == ["459", "629", "789", "999", "1299", "promo", "child", "alacarte"]   # spend bar order, left -> right
    assert "590" not in tiers
    assert sum(r["pax"] for r in t["rows"]) == 30 + 10 + 4 + 8                             # August's 20 heads are not counted
    # a September-only period is not clipped
    t2, _ = A._sph_tiers(_db(), month_from="2026-09", month_to="2026-09")
    assert t2["clipped"] is False


def test_shares_sum_to_100_and_the_alacarte_row_is_the_rest():
    t, _ = A._sph_tiers(_db(), month_from="2026-09", month_to="2026-09")
    r = _rows(t)
    assert sum(x["pax_share"] for x in t["rows"]) == pytest.approx(100, abs=0.05)
    assert sum(x["bill_share"] for x in t["rows"]) == pytest.approx(100, abs=0.05)
    assert r["alacarte"]["label"] == "ไม่ใช่บุฟเฟ่ต์"
    assert (r["alacarte"]["bills"], r["alacarte"]["pax"]) == (4, 8)
    assert r["alacarte"]["rev_per_head"] == pytest.approx(300.0, abs=0.01)
    assert r["789"]["pax_share"] == pytest.approx(30 / 52 * 100, abs=0.01)
    assert r["789"]["label"] == "฿789" and r["promo"]["label"] == rules.tier_label("promo")


def test_revenue_is_on_the_card_basis_and_compared_with_the_base_price():
    t, _ = A._sph_tiers(_db(), month_from="2026-09", month_to="2026-09")
    r = _rows(t)
    assert r["789"]["rev_per_head"] == pytest.approx(720.0, abs=0.01)                      # rev / 1.05 / pax
    assert r["789"]["base_price"] == pytest.approx(rules.net_to_base(789), abs=0.01)
    assert r["789"]["over_price"] == pytest.approx(720.0 - rules.net_to_base(789), abs=0.02)
    assert r["789"]["sph_vs_price"] == pytest.approx(720.0 / rules.net_to_base(789), abs=0.001)
    assert r["999"]["over_price"] == pytest.approx(0, abs=0.02)
    assert r["promo"]["price"] is None and r["promo"]["sph_vs_price"] is None


def test_alacarte_is_floored_at_zero():
    con = _db()
    con.execute("INSERT INTO menu_fact_total VALUES (1, '2026-09-05', '1299', 50, 200, 0, 1e6, 0)")
    t, _ = A._sph_tiers(con, month_from="2026-09", month_to="2026-09")
    a = _rows(t)["alacarte"]
    assert a["bills"] == 0 and a["pax"] == 0 and a["revenue"] == 0
    assert sum(x["pax_share"] for x in t["rows"]) == pytest.approx(100, abs=0.05)


def test_facts_that_lag_the_sales_use_the_covered_days():
    t, _ = A._sph_tiers(_db(), month_from="2026-10", month_to="2026-10")
    assert t["data_to"] == "2026-10-10" and t["to"] == "2026-10-20" and t["lagging"] is True
    r = _rows(t)
    assert r["459"]["pax"] == 8
    assert (r["alacarte"]["bills"], r["alacarte"]["pax"]) == (1, 1)                       # the 20 Oct bills are not counted


def test_no_fact_rows_in_the_window():
    con = _db()
    con.execute("DELETE FROM menu_fact_total WHERE day >= '2026-10-01'")
    t, why = A._sph_tiers(con, month_from="2026-10", month_to="2026-10")
    assert t is None and why == "no_facts"


def test_branch_and_group_filters():
    con = _db()
    t, _ = A._sph_tiers(con, month_from="2026-09", month_to="2026-10", branch="สาขาเอ")
    assert _rows(t)["459"]["pax"] == 0 and _rows(t)["789"]["pax"] == 30
    t, _ = A._sph_tiers(con, month_from="2026-09", month_to="2026-10", group="franchise")
    assert _rows(t)["789"]["pax"] == 0 and _rows(t)["459"]["pax"] == 8
    t, _ = A._sph_tiers(con, month_from="2026-09", month_to="2026-10", group="master")
    assert _rows(t)["459"]["pax"] == 0 and _rows(t)["789"]["pax"] == 30
    t, why = A._sph_tiers(con, month_from="2026-09", month_to="2026-10", branch="สาขาเอ", group="franchise")
    assert t is None and why == "no_data"


def test_the_endpoint_carries_tiers_and_honours_group(monkeypatch):
    con = _db()

    @contextmanager
    def fake_db():
        yield con
    monkeypatch.setattr(A, "get_db", fake_db)
    fn = inspect.unwrap(A.analysis_sph)
    res = fn(month=None, month_from="2026-09", month_to="2026-10", branch=None, group="franchise")
    assert res["tiers"]["rows"] and res["tiers_reason"] is None
    assert res["overall"]["total_tx"] == 4 + 1 + 6                       # the spend bands follow the group too
    assert {b["branch"] for b in res["by_branch"]} == {"สาขาบี"}
    old = fn(month="2026-08", month_from=None, month_to=None, branch=None, group=None)
    assert old["tiers"] is None and old["tiers_reason"] == "before_grand_menu"
    assert old["distribution"]                                          # existing keys unchanged
