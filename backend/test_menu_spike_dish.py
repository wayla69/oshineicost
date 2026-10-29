"""DV3 (phone table review 2026-10-25, top-10 #3) · /api/sales/menu-spike by DISH.

- the chain view is one row per dish: the menu name summed over its SKU codes and branches;
- NET extra cost = the branches above their normal minus the branches below it; a branch that sold the dish in the baseline but not once
  in the period counts its whole normal as a decrease; non-food lines and new menus are in no dish;
- สาขาที่ดัน = the top 3 branches by their extra ฿ (pushers, pushers_more);
- the floor (>= ฿1,000 or >= 0.1 % of the food cost; >= 20 plates for a role without cost), the share of the food cost and the cumulative %
  of the positive dishes; totals up / down / net over every dish;
- one branch in scope = view "branch", rows per dish of that branch with its tiers, no pushers;
- new menus grouped per dish: branches, plates a day summed, period cost, floor >= 20 plates a day or >= ฿1,000;
- a role without cost gets no cost key in any of these.
"""
import datetime as dt
import sqlite3
from contextlib import contextmanager

import pytest

from routers import sales

P0, P1 = dt.date(2026, 9, 28), dt.date(2026, 10, 4)          # the period: 7 days, Monday to Sunday


# branch A (1), B (2), C (3); 100 customers a day each; plates a day in the baseline (3 weeks before) -> the period:
#   แซลมอน  (three SKU codes, one name)  A 10 -> 30 (+140 plates x ฿50 = +฿7,000) · B 10 -> 8 (-14 x ฿50 = -฿700)
#                                         C 2 -> none in the period (-14 x ฿50 = -฿700)          net +112 plates, +฿5,600
#   หมึก    A 20 -> 40 (+140 x ฿10 = +฿1,400)
#   เล็ก    A 10 -> 12 (+14 x ฿1 = +฿14)                    under the floor
#   ลดลง    B 20 -> 10 (-70 x ฿20 = -฿1,400)                a decrease: in the totals, not a row
#   ข้าว    C 10 -> 10, no cost                             flat: no row
#   ค่ามัดจำจาน  C 5 a day in the baseline only            a fee line: in no dish, not a decrease
#   เมนูใหม่  first sold 25 Sep at A (5 a day) and B (30 a day): a new dish, 35 plates a day
#   ใหม่น้อย  first sold 30 Sep at A, 2 a day, ฿10              under the new-menu floor
def _db():
    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript("""
      CREATE TABLE branches(branch_id INTEGER PRIMARY KEY, name TEXT, branch_type TEXT);
      CREATE TABLE skus(sku_id INTEGER PRIMARY KEY, sku_code TEXT, description TEXT);
      CREATE TABLE sales_sku_daily(branch_id INT, day TEXT, sku_id INT, qty REAL, subtotal_ex_vat REAL);
      CREATE INDEX ix_ssd ON sales_sku_daily(branch_id, day, sku_id);
      CREATE VIEW v_sales_sku_daily AS SELECT * FROM sales_sku_daily
        WHERE sku_id NOT IN (SELECT sku_id FROM skus WHERE UPPER(sku_code) IN ('SERVICECHARGE','SERVICE'));
      CREATE TABLE sales_transaction(branch_id INT, receipt_no TEXT, sold_at TEXT, customer_count INT, source TEXT,
        total_inc_vat REAL, service_charge REAL);
      CREATE TABLE bc_sku_cost_list(sku_id INT, month TEXT, unit_cost REAL);
      CREATE TABLE bc_sku_cost_month(sku_id INT, month TEXT, unit_cost REAL);
      CREATE TABLE sku_effective_cost(sku_id INT, recipe_cost REAL);
      CREATE TABLE sku_cost(sku_id INT, unit_cost REAL);
      CREATE TABLE bom_ingredients(sku_id INT, ingredient_cost REAL);
      CREATE TABLE bom_sku_alias(bom_sku_code TEXT, resolved_sku_id INT);
      INSERT INTO branches VALUES (1, 'สาขาเอ', 'master'), (2, 'สาขาบี', 'master'), (3, 'สาขาซี', 'franchise');
      INSERT INTO skus VALUES (1, 'S1', '(A) แซลมอน'), (2, 'S2', '(A) แซลมอน'), (3, 'S3', '(A) แซลมอน'), (4, 'Q', '(A) หมึก'),
        (5, 'T', '(A) เล็ก'), (6, 'D', '(A) ลดลง'), (7, 'R', '(A) ข้าว'), (8, 'DEP', 'ค่ามัดจำจาน'), (9, 'N', '(A) เมนูใหม่'),
        (10, 'N2', '(A) ใหม่น้อย');
      INSERT INTO sku_cost VALUES (1, 50), (2, 50), (3, 50), (4, 10), (5, 1), (6, 20), (8, 99), (9, 30), (10, 10);
    """)
    d = dt.date(2026, 8, 17)
    while d <= P1:
        day, p = d.isoformat(), P0 <= d
        for bid in (1, 2, 3):
            con.execute("INSERT INTO sales_transaction VALUES (?, ?, ?, 100, 'Easy', 10700, 0)", (bid, f"R{bid}{day}", day + " 12:00:00"))
        plan = {(1, 1): 30 if p else 10, (2, 2): 8 if p else 10, (3, 3): None if p else 2, (1, 4): 40 if p else 20, (1, 5): 12 if p else 10,
                (2, 6): 10 if p else 20, (3, 7): 10, (3, 8): None if p else 5}
        if d >= dt.date(2026, 9, 25):
            plan[(1, 9)], plan[(2, 9)] = 5, 30
        if d >= dt.date(2026, 9, 30):
            plan[(1, 10)] = 2
        for (bid, sid), q in plan.items():
            if q is not None:
                con.execute("INSERT INTO sales_sku_daily VALUES (?, ?, ?, ?, 0)", (bid, day, sid, q))
        d += dt.timedelta(days=1)
    return con


def _run(cost=True, branch=None):
    bf, pp = (" AND b.name=:branch", {"branch": branch}) if branch else ("", {})
    return sales._menu_spike_period(_db(), "range", None, P0.isoformat(), P1.isoformat(), bf, pp, 300, 1.5, with_cost=cost)


def _dish(r, menu):
    return next((x for x in r["dishes"] if x["menu"] == menu), None)


def test_one_row_per_dish_with_the_net_extra_cost_and_the_branches_that_push_it():
    r = _run()
    assert r["view"] == "chain"
    assert [x["menu"] for x in r["dishes"]] == ["แซลมอน", "หมึก", "เล็ก"]       # by net ฿; ลดลง and ข้าว are not above their normal
    s = _dish(r, "แซลมอน")
    assert (s["today"], s["expected"], s["excess"]) == (266, 154, 112)          # 210 + 56 + 0 plates vs 70 + 70 + 14
    assert s["net_cost"] == 5600.0                                               # +7,000 - 700 - 700: a branch below its normal subtracts
    assert s["branches"] == 2                                                    # สาขาซี sold none in the period
    assert s["pushers"] == [{"branch": "สาขาเอ", "excess": 140, "extra_cost": 7000.0}] and s["pushers_more"] == 0
    assert s["x"] == round(266 / 154, 2) and s["uncosted"] is False
    # the พุ่ง chip is the dish's own x (>= 1.5 and >= 5 plates), not one branch's
    assert [x["spike"] for x in r["dishes"]] == [True, True, False] and s["spike_branches"] == 1
    # the branch x dish rows of the ranked list are unchanged: แซลมอน at สาขาเอ only (the others are below their normal)
    assert [(x["menu"], x["branch"]) for x in r["spikes"] if x["menu"] == "แซลมอน"] == [("แซลมอน", "สาขาเอ")]


def test_the_floor_the_share_and_the_cumulative_percent():
    r = _run()
    fc = r["food_cost_total"]
    assert fc == 210 * 50 + 280 * 10 + 84 * 1 + 56 * 50 + 70 * 20 + 245 * 30 + 10 * 10      # the period's COGS of the scope, new menus too
    up = 5600 + 1400 + 14
    s, q, t = (_dish(r, m) for m in ("แซลมอน", "หมึก", "เล็ก"))
    assert s["cost_share"] == round(5600 / fc * 100, 2) and q["cost_share"] == round(1400 / fc * 100, 2)
    assert (s["cum_pct"], q["cum_pct"], t["cum_pct"]) == (round(5600 / up * 100, 1), round(7000 / up * 100, 1), 100.0)
    assert (s["material"], q["material"], t["material"]) == (True, True, False)   # ฿14 = 0.08 % of the food cost, under ฿1,000
    assert (r["dish_count"], r["dish_material_count"]) == (3, 2)
    assert r["dish_floor"] == {"baht": 1000, "share_pct": 0.1}


def test_the_totals_are_up_minus_down_over_every_dish():
    tot = _run()["dish_totals"]
    assert tot["up_cost"] == 7014.0 and tot["down_cost"] == -1400.0 and tot["net_cost"] == 5614.0
    assert tot["up_excess"] == 112 + 140 + 14 and tot["down_excess"] == -70.0
    assert tot["net_excess"] == 196.0


def test_fee_lines_and_new_menus_are_in_no_dish():
    r = _run()
    names = {x["menu"] for x in r["dishes"]}
    assert not names & {"ค่ามัดจำจาน", "เมนูใหม่", "ใหม่น้อย"}


def test_new_menus_are_grouped_per_dish_with_their_floor():
    r = _run()
    assert r["new_count"] == 3                                                  # per branch: เมนูใหม่ x 2, ใหม่น้อย x 1
    assert [x["menu"] for x in r["new_dishes"]] == ["เมนูใหม่", "ใหม่น้อย"]
    n = r["new_dishes"][0]
    assert (n["branches"], n["qty"], n["per_day"], n["period_cost"]) == (2, 245, 35.0, 245 * 30.0)
    assert [b["branch"] for b in n["top"]] == ["สาขาบี", "สาขาเอ"] and n["more"] == 0
    assert n["material"] is True and n["returning"] == 0
    m = r["new_dishes"][1]
    assert (m["qty"], m["period_cost"], m["material"]) == (10, 100.0, False)    # 2 a day, ฿100: under 20 a day and ฿1,000
    assert (r["new_dish_count"], r["new_dish_material_count"]) == (2, 1)
    assert r["new_dish_floor"] == {"per_day": 20, "baht": 1000}


def test_one_branch_is_the_branch_view_rows_per_dish_of_that_branch():
    r = _run(branch="สาขาเอ")
    assert r["view"] == "branch"
    assert [x["menu"] for x in r["dishes"]] == ["แซลมอน", "หมึก", "เล็ก"]
    s = _dish(r, "แซลมอน")
    assert s["net_cost"] == 7000.0 and s["excess"] == 140 and "pushers" not in s
    assert "tiers" in s and s["basis"] == "all" and s["rate"] == 30.0           # the branch's ranked row: its per-head figures
    b = _run(branch="สาขาบี")
    assert [x["menu"] for x in b["dishes"]] == []                               # both of its dishes are below their normal
    assert b["dish_totals"]["net_cost"] == -700.0 - 1400.0


def test_a_role_without_cost_ranks_dishes_by_net_plates_and_gets_no_cost_key():
    r = _run(cost=False)
    assert [x["menu"] for x in r["dishes"]] == ["หมึก", "แซลมอน", "เล็ก"]
    assert [x["material"] for x in r["dishes"]] == [True, True, False]          # 20 net plates
    assert r["dishes"][0]["cum_pct"] == round(140 / 266 * 100, 1)
    assert r["dish_floor"] == {"plates": 20} and r["new_dish_floor"] == {"per_day": 20}
    cost_keys = {"net_cost", "cost_share", "net_pp", "uncosted", "extra_cost", "period_cost", "up_cost", "down_cost", "net_share"}
    for x in r["dishes"] + r["new_dishes"] + [r["dish_totals"]] + [p for x in r["dishes"] for p in x.get("pushers", [])]:
        assert not (cost_keys & set(x)), x
    assert [p["branch"] for p in _dish(r, "แซลมอน")["pushers"]] == ["สาขาเอ"]


def test_an_uncosted_dish_above_its_normal_comes_last_and_is_never_material():
    con = _db()
    con.execute("DELETE FROM sku_cost WHERE sku_id = 4")                      # หมึก loses its cost
    r = sales._menu_spike_period(con, "range", None, P0.isoformat(), P1.isoformat(), "", {}, 300, 1.5, with_cost=True)
    q = r["dishes"][-1]
    assert q["menu"] == "หมึก" and q["uncosted"] is True and q["net_cost"] == 0 and q["material"] is False and q["cum_pct"] is None


@pytest.fixture
def client_db(monkeypatch):
    con = _db()

    @contextmanager
    def fake():
        yield con
    monkeypatch.setattr(sales, "get_db", fake)
    return con


def test_the_endpoint_follows_the_branch_param(client_db):
    a = dict(date=None, group=None, limit=300, threshold=1.5, period="range", date_from=P0.isoformat(), date_to=P1.isoformat())
    chain = sales.menu_spike(branch=None, **a)
    one = sales.menu_spike(branch="สาขาเอ", **a)
    assert (chain["view"], one["view"]) == ("chain", "branch")
    assert _dish(chain, "แซลมอน")["net_cost"] == 5600.0 and _dish(one, "แซลมอน")["net_cost"] == 7000.0


def test_through_the_app_the_signed_in_role_decides_the_cost_keys(client, app_db, monkeypatch):
    """Auth on: marketing (no cost.analysis / profit.pnl) gets the dish rows by plates, no cost key; management gets ฿."""
    from test_menu_spike_period import _as
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    con = _db()

    @contextmanager
    def fake():
        yield con
    monkeypatch.setattr(sales, "get_db", fake)
    q = {"period": "range", "date_from": P0.isoformat(), "date_to": P1.isoformat()}
    mk = _as("marketing1").get("/api/sales/menu-spike", params=q).json()
    assert mk["cost_view"] is False and [x["menu"] for x in mk["dishes"]] == ["หมึก", "แซลมอน", "เล็ก"]
    assert not any(k in x for x in mk["dishes"] + mk["new_dishes"] for k in ("net_cost", "period_cost", "cost_share"))
    mg = _as("management1").get("/api/sales/menu-spike", params={**q, "branch": "สาขาเอ"}).json()
    assert mg["cost_view"] is True and mg["view"] == "branch" and _dish(mg, "แซลมอน")["net_cost"] == 7000.0


def test_the_fixture_answer_carries_the_dish_keys(client):
    r = client.get("/api/sales/menu-spike", params={"period": "week"})
    assert r.status_code == 200
    j = r.json()
    for k in ("view", "dishes", "dish_count", "dish_material_count", "dish_totals", "dish_floor", "new_dishes", "new_dish_count"):
        assert k in j, k
    assert j["view"] == "chain"
    nets = [x["net_cost"] for x in j["dishes"] if not x.get("uncosted")]
    assert nets == sorted(nets, reverse=True) and all(n > 0 for n in nets)


# ── PR-SK (owner 2026-10-29) · spellings fold, a chain-wide thin normal, the price side of a paid dish, the ingredient roll-up ──
class _Sc:
    """A stand-in for _SpikeCost: unit cost per SKU, the period's revenue per (branch, SKU), a ฿10,000 food cost."""
    def __init__(self, unit, rev=None):
        self.unit, self.rev, self.food_cost, self.revenue, self.pkg = unit, rev or {}, 10000.0, 100000.0, set()

    def unit_of(self, bid, sid):
        return self.unit.get(sid, 0.0)

    def share(self, baht):
        return round(baht / self.food_cost * 100, 2)

    def pp(self, baht):
        return round(baht / self.revenue * 100, 2)


def test_two_spellings_of_one_dish_fold_into_the_row_named_by_the_best_seller():
    ents = [("alc", "(TW) แซลมอน ซาชิมิ 1,000 กรัม", 1, "สาขาเอ", 1, 13, 10), ("alc", "(TW) แซลมอน ซาชิมิ 1000 กรัม", 2, "สาขาบี", 2, 3, 1)]
    rows, _ = sales._spike_dishes(ents, _Sc({1: 500.0, 2: 500.0}), {}, False)
    assert [r["menu"] for r in rows] == ["(TW) แซลมอน ซาชิมิ 1,000 กรัม"]
    r = rows[0]
    assert (r["today"], r["expected"], r["excess"], r["net_cost"]) == (16, 11, 5, 2500.0)
    assert r["merged"] == ["(TW) แซลมอน ซาชิมิ 1000 กรัม"] and r["branches"] == 2
    assert sales._spike_norm("ซูชิ หมึก  ·  ") == sales._spike_norm("ซูชิหมึก")
    assert sales._spike_norm("(B) แซลมอน") != sales._spike_norm("(A) แซลมอน")      # the kind is in the key too


def test_a_chain_normal_under_five_plates_is_thin_and_a_paid_dish_carries_its_margin():
    ents = [("alc", "(DL) แซลมอน 1 กก.", 1, "สาขาเอ", 1, 4, 0), ("buffet", "ซาชิมิ", 1, "สาขาเอ", 2, 120, 100)]
    sc = _Sc({1: 565.0, 2: 50.0}, rev={(1, 1): 4 * 900.0})
    rows, _ = sales._spike_dishes(ents, sc, {}, False)
    dl, bf = (next(r for r in rows if r["menu"] == m) for m in ("(DL) แซลมอน 1 กก.", "ซาชิมิ"))
    assert dl["thin_base"] is True and bf["thin_base"] is False
    assert (dl["price"], dl["extra_revenue"], dl["extra_gp"]) == (900.0, 3600.0, 3600.0 - 4 * 565.0)
    assert "price" not in bf and "extra_gp" not in bf                         # a buffet dish has no price of its own
    # a role without cost: the same rows, no price side
    rows2, _ = sales._spike_dishes(ents, None, {}, False)
    assert all("extra_gp" not in r and "net_cost" not in r for r in rows2)


def test_the_list_rolls_up_by_high_value_ingredient():
    rows = [{"menu": "ซาชิมิแซลมอน", "net_cost": 5000.0, "excess": 100, "uncosted": False},
            {"menu": "ซาชิมิหอยเชลล์ ฮอกไกโด", "net_cost": 2900.0, "excess": 50, "uncosted": False},
            {"menu": "แซลมอนดองซีอิ๊ว", "net_cost": 1000.0, "excess": 20, "uncosted": False},
            {"menu": "ข้าวผัด", "net_cost": 100.0, "excess": 5, "uncosted": False},
            {"menu": "แซลมอนไม่มีต้นทุน", "net_cost": 0.0, "excess": 9, "uncosted": True}]
    g = sales._spike_groups(rows, True)
    assert [(x["slug"], x["net_cost"], x["dishes"]) for x in g] == [("salmon", 6000.0, 2), ("scallop", 2900.0, 1)]
    assert g[0]["label"] == "แซลมอน" and g[0]["share_pct"] == round(6000 / 9000 * 100, 1)
    p = sales._spike_groups(rows, False)                                       # a role without cost: net extra plates
    assert [(x["slug"], x["excess"]) for x in p] == [("salmon", 129), ("scallop", 50)]
    assert sales._spike_groups([], True) == []


def test_the_endpoint_answer_carries_the_groups_and_the_kind_split():
    r = _run()
    assert r["dish_groups"] == [{"slug": "salmon", "label": "แซลมอน", "net_cost": 5600.0, "dishes": 1, "share_pct": round(5600 / 7014 * 100, 1)}]
    assert r["dish_kind_count"] == {"buffet": 0, "alc": 3}
    assert all(x["thin_base"] is False for x in r["dishes"])


# ── PR-SK2 (owner 2026-10-29, "Do 4-7") · กระจุก / ทั้งเครือ, พุ่งต่อเนื่อง N สัปดาห์, ต้นทุน/จาน ──
def test_the_streak_counts_the_up_weeks_in_a_row_from_the_latest():
    # the latest week 30 vs the 3 before it (10, 10, 10) = 3x; two weeks (30, 10) vs (10, 10, 10): 10 is not up -> streak 1
    assert sales._spike_streak([30, 10, 10, 10, 10, 10, 10, 10]) == 1
    # three rising weeks 40, 30, 20 vs the (10, 10, 10) before them, all >= 1.2x; four would take 10 vs 10 -> 3
    assert sales._spike_streak([40, 30, 20, 10, 10, 10, 10, 10]) == 3
    assert sales._spike_streak([4, 1, 1, 1, 1, 1, 1, 1]) == 0            # under 5 plates never counts
    assert sales._spike_streak([30, 0, 0, 0, 0, 0, 0, 0]) == 0           # no base behind it
    assert sales._spike_streak([30] * 8) == 0                            # flat = no streak
    assert sales._spike_streak([20] * 5 + [10] * 3) == 5                 # a plateau: five 20s vs the (10, 10, 10) before them
    assert sales._spike_streak([30, 30, 30, 30, 30, 30, 10, 10, 10]) == 5   # capped at _SPIKE_STREAK_WEEKS


def test_a_dish_carries_its_concentration_its_streak_and_its_cost_per_plate():
    ents = [("buffet", "ซาชิมิแซลมอน", 1, "สาขาเอ", 1, 100, 40), ("buffet", "ซาชิมิแซลมอน", 2, "สาขาบี", 2, 50, 40),
            ("buffet", "ซาชิมิแซลมอน", 3, "สาขาซี", 3, 30, 40),
            ("buffet", "ข้าวผัด", 1, "สาขาเอ", 4, 60, 40), ("buffet", "ข้าวผัด", 2, "สาขาบี", 5, 60, 40)]
    weekly = {(1, 1): [60, 30, 10, 10, 10, 10, 10, 10], (2, 2): [30, 10, 10, 10, 10, 10, 10, 10], (3, 3): [10] * 8,
              (1, 4): [40, 40, 40, 40, 40, 40, 40, 40]}
    rows, _ = sales._spike_dishes(ents, _Sc({1: 50.0, 2: 50.0, 3: 40.0, 4: 10.0, 5: 10.0}), {}, False, weekly=weekly)
    sal, rice = (next(r for r in rows if r["menu"] == m) for m in ("ซาชิมิแซลมอน", "ข้าวผัด"))
    # กระจุก: branch A holds 3,000 of the 3,500 ฿ of increases (the decrease of C is not in the base)
    assert sal["concentration"] == {"branch": "สาขาเอ", "share_pct": round(3000 / 3500 * 100, 1), "up_branches": 2, "spread": "focused"}
    assert rice["concentration"]["spread"] == "wide" and rice["concentration"]["share_pct"] == 50.0
    # the streak sums the branches' weeks: 100, 50, 30, 30, ... -> 100 vs 36.7 = up; 50 vs 30 = 1.67 up; 30 vs 30 = no -> 2
    assert sal["streak_weeks"] == 2 and rice["streak_weeks"] == 0
    # ต้นทุน/จาน = plate-weighted: (100x50 + 50x50 + 30x40) / 180
    assert sal["unit_cost"] == round((5000 + 2500 + 1200) / 180, 2) and rice["unit_cost"] == 10.0
    # one branch only: the pushers and the concentration are chain-view keys; the streak stays
    rows1, _ = sales._spike_dishes(ents[:1], _Sc({1: 50.0}), {}, True, weekly=weekly)
    assert "concentration" not in rows1[0] and rows1[0]["streak_weeks"] == 2
    # a role without cost: plates decide the concentration, no unit cost
    rows2, _ = sales._spike_dishes(ents, None, {}, False, weekly=weekly)
    s2 = next(r for r in rows2 if r["menu"] == "ซาชิมิแซลมอน")
    assert s2["concentration"]["share_pct"] == round(60 / 70 * 100, 1) and "unit_cost" not in s2
    # without the weekly figures (the table could not be read): no streak key at all
    rows3, _ = sales._spike_dishes(ents, None, {}, False)
    assert all("streak_weeks" not in r for r in rows3)


def test_the_endpoint_answer_carries_the_streak_rule_and_the_rows_their_streak():
    r = _run()
    assert r["dish_focus_pct"] == 50 and r["dish_streak"] == {"weeks": 5, "x": 1.2, "min_plates": 5}
    assert all(isinstance(x["streak_weeks"], int) and "unit_cost" in x for x in r["dishes"] if not x["uncosted"])
    assert all("concentration" in x for x in r["dishes"] if x["excess"] > 0 and x["pushers"])
