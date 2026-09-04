"""The ingredient order plan — every /api/ingredients endpoint.

This module was the only endpoint family with no test at all (14 of the 23
untested endpoints in the 2026-09 audit), and it is the one that turns sales
into purchase quantities and warning letters. The cases below pin behaviour a
branch would notice: demand scales with the branch filter, the buffer is applied
on top of demand, pack sizes round UP to whole packs, a 'local' ingredient
leaves the plan and the letter, and an issued cycle is a frozen snapshot that
actuals and compliance read back exactly.

Runs against the shared fixture DB (conftest): two branches, sku 2 (buffet
plate, 50/day/branch) and sku 3 (à-la-carte, 5/day/branch), 1..3 of two months.
BoM lines are seeded here, since no other test needs them.
"""
import math
import sqlite3

import pytest

import conftest as _cf
import main

END = f"{_cf.LATEST_MONTH}-03"           # last fixture day
PLAN = {"date_to": END, "days": 28}

# one recipe per fixture dish: salmon plate = salmon + rice, steak = beef
BOM = [
    # sku_id, seq, code,       name,        qty/serve, unit,  price, cost, type
    (2, 1, "ING-SAL",  "แซลมอน",     0.12, "กก.", 500.0, 60.0, "protein"),
    (2, 2, "ING-RICE", "ข้าวญี่ปุ่น", 0.05, "กก.",  40.0,  2.0, "carb"),
    (3, 1, "ING-BEEF", "เนื้อ",       0.20, "กก.", 600.0, 120.0, "protein"),
]


@pytest.fixture(scope="module", autouse=True)
def _seed_bom(client):
    con = sqlite3.connect(str(main.DB_PATH))
    con.executemany(
        "INSERT OR REPLACE INTO bom_ingredients(sku_id, seq, ingredient_code, ingredient_name,"
        " qty_per_serving, unit, price_per_unit, ingredient_cost, ingredient_type, source_file)"
        " VALUES(?,?,?,?,?,?,?,?,?,'test')", BOM)
    for t in ("ingredient_sourcing", "ingredient_order_unit", "ingredient_order_issue",
              "ingredient_order_issue_line", "ingredient_order_actual", "ingredient_demand_calendar"):
        try:
            con.execute(f"DELETE FROM {t}")
        except sqlite3.OperationalError:
            pass                                    # created lazily by the endpoint
    con.commit(); con.close()
    main.invalidate_cache()
    yield


def _plan(client, **kw):
    r = client.get("/api/ingredients/order-plan", params={**PLAN, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _row(plan, code):
    m = [r for r in plan["rows"] if r["code"] == code]
    assert m, f"{code} missing from plan: {[r['code'] for r in plan['rows']]}"
    return m[0]


# ─── order plan ──────────────────────────────────────────────────────────────

def test_plan_is_sales_times_recipe():
    """50 salmon plates/day/branch × 0.12 kg = 6 kg/day per branch."""
    p = _plan(client=pytest.client, branch="BranchA")
    assert p["available"] is True
    sal = _row(p, "ING-SAL")
    assert sal["unit"] == "กก."
    assert sal["per_day"] == pytest.approx(6.0, rel=0.02)
    # the buffer sits ON TOP of demand — a week is never less than 7 × per_day
    assert sal["order_week"] >= sal["per_day"] * 7 * 1.09


def test_window_demand_is_the_same_however_it_is_sliced():
    """A branch is averaged over the days it actually traded (so a branch that
    opened mid-window is not undercounted); the chain view is averaged over the
    calendar window. Per-day figures therefore differ when the window is only
    partly traded, but the demand they describe must be one and the same:
    chain per_day × window days == Σ branch per_day × that branch's trading days."""
    pa = _plan(pytest.client, branch="BranchA"); pb = _plan(pytest.client, branch="BranchB")
    pc = _plan(pytest.client)
    a = _row(pa, "ING-SAL")["per_day"] * pa["meta"]["trading_days"]
    b = _row(pb, "ING-SAL")["per_day"] * pb["meta"]["trading_days"]
    chain = _row(pc, "ING-SAL")["per_day"] * pc["meta"]["days"]
    assert pa["meta"]["trading_days"] == 3 and pc["meta"]["days"] == 28
    assert chain == pytest.approx(a + b, rel=0.02)


def test_plan_sorted_by_monthly_value_desc():
    p = _plan(pytest.client)
    vals = [r["value_month"] for r in p["rows"]]
    assert vals == sorted(vals, reverse=True)
    assert p["totals"]["ingredients"] == len(p["rows"]) == 3


def test_type_filter_and_search_narrow_the_rows():
    assert {r["code"] for r in _plan(pytest.client, type="protein")["rows"]} == {"ING-SAL", "ING-BEEF"}
    assert {r["code"] for r in _plan(pytest.client, q="ข้าว")["rows"]} == {"ING-RICE"}


def test_plan_xlsx_and_letter_render(client):
    x = client.get("/api/ingredients/order-plan.xlsx", params=PLAN)
    assert x.status_code == 200 and x.content[:2] == b"PK", "not a workbook"
    h = client.get("/api/ingredients/order-letter.print", params={**PLAN, "branch": "BranchA"})
    assert h.status_code == 200
    assert "BranchA" in h.text and "แซลมอน" in h.text


# ─── pack sizes ──────────────────────────────────────────────────────────────

def test_pack_size_rounds_the_order_up_to_whole_packs(client):
    u = client.get("/api/ingredients/order-units").json()
    sal = next(r for r in u["rows"] if r["code"] == "ING-SAL")
    assert sal["pack_known"] is False, "fixture starts on the fallback unit"

    r = client.post("/api/ingredients/order-units", json=[{
        "ingredient_code": "ING-SAL", "base_unit": "กก.", "order_unit": "ลัง",
        "qty_per_order": 10, "price_per_order": 4800}])
    assert r.status_code == 200 and r.json()["saved"] == 1

    sal = next(r for r in client.get("/api/ingredients/order-units").json()["rows"] if r["code"] == "ING-SAL")
    assert sal["pack_known"] is True and sal["order_unit"] == "ลัง"

    row = _row(_plan(client, branch="BranchA"), "ING-SAL")
    # ceil(base / pack) — never a fraction of a pack, never fewer than demand
    assert row["order_week_units"] == math.ceil(row["order_week"] / 10)
    assert row["order_week_units"] * 10 >= row["order_week"]


def test_zero_pack_size_is_refused(client):
    r = client.post("/api/ingredients/order-units", json=[{
        "ingredient_code": "ING-RICE", "base_unit": "กก.", "order_unit": "ถุง", "qty_per_order": 0}])
    assert r.status_code == 400


# ─── sourcing: central vs branch-bought ──────────────────────────────────────

def test_local_ingredient_leaves_the_plan_and_the_letter(client):
    assert client.get("/api/ingredients/sourcing").json()["rows"] == []

    r = client.post("/api/ingredients/sourcing",
                    json=[{"ingredient_code": "ING-RICE", "branch": "", "mode": "local"}])
    assert r.status_code == 200

    codes = lambda **kw: {x["code"] for x in _plan(client, **kw)["rows"]}
    assert codes(include="central") == {"ING-SAL", "ING-BEEF"}
    assert codes(include="local") == {"ING-RICE"}
    assert _row(_plan(client), "ING-RICE")["sourcing"] == "local"
    # the letter keeps a local item OUT of the order table; it is named only in
    # the note beneath it, so the branch knows it is theirs to buy
    html = client.get("/api/ingredients/order-letter.print", params={**PLAN, "branch": "BranchA"}).text
    # the letter opens with a header table (branch, period); the order table is
    # the one that lists the ingredients, so find it by content, not position
    tables = html.split("</table>")
    order_table = next(t for t in tables if "แซลมอน" in t)
    assert "ข้าวญี่ปุ่น" not in order_table
    assert "ข้าวญี่ปุ่น" in html

    # a branch override, then 'default' DELETES it rather than pinning central
    client.post("/api/ingredients/sourcing",
                json=[{"ingredient_code": "ING-RICE", "branch": "BranchB", "mode": "central"}])
    assert _row(_plan(client, branch="BranchB"), "ING-RICE")["sourcing"] == "central"
    assert _row(_plan(client, branch="BranchA"), "ING-RICE")["sourcing"] == "local"
    client.post("/api/ingredients/sourcing",
                json=[{"ingredient_code": "ING-RICE", "branch": "BranchB", "mode": "default"}])
    assert _row(_plan(client, branch="BranchB"), "ING-RICE")["sourcing"] == "local"
    assert client.get("/api/ingredients/sourcing").json()["summary"]["branch_overrides"] == 0

    r = client.post("/api/ingredients/sourcing",
                    json=[{"ingredient_code": "ING-RICE", "mode": "sometimes"}])
    assert r.status_code == 400


# ─── issue → actual → compliance ─────────────────────────────────────────────

def test_issued_cycle_is_a_frozen_snapshot_that_actuals_read_back(client):
    r = client.post("/api/ingredients/order-plan/issue",
                    json={"branch": "BranchA", "horizon": "week", **PLAN})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True and len(j["issued"]) == 1
    iid = j["issued"][0]["issue_id"]
    assert j["issued"][0]["branch"] == "BranchA" and j["issued"][0]["lines"] >= 2

    cyc = client.get("/api/ingredients/order-cycles", params={"branch": "BranchA"}).json()["rows"]
    assert any(c["issue_id"] == iid for c in cyc)

    comp = client.get("/api/ingredients/order-compliance", params={"issue_id": iid}).json()
    sal = next(x for x in comp["rows"] if x["ingredient_code"] == "ING-SAL")
    assert sal["act_units"] == 0 and sal["rec_units"] > 0

    # order exactly the recommended PACKS. Packs round demand up, so the base
    # quantity received sits at or just above 100% of the base recommended —
    # never below, and never by more than one pack.
    r = client.post("/api/ingredients/order-actual", json={
        "issue_id": iid, "rows": [{"ingredient_code": "ING-SAL", "qty_units": sal["rec_units"]}]})
    assert r.status_code == 200 and r.json()["saved"] == 1
    comp = client.get("/api/ingredients/order-compliance", params={"issue_id": iid}).json()
    sal = next(x for x in comp["rows"] if x["ingredient_code"] == "ING-SAL")
    assert sal["act_units"] == sal["rec_units"]
    one_pack_pct = 100.0 * sal["qty_per_order"] / sal["rec_base"]
    assert 100.0 <= sal["pct_of_rec"] < 100.0 + one_pack_pct

    # the letter reads the same frozen figures
    h = client.get("/api/ingredients/compliance-letter.print", params={"issue_id": iid})
    assert h.status_code == 200 and "BranchA" in h.text

    # accuracy has a closed cycle to evaluate now — must not crash, must be shaped
    acc = client.get("/api/ingredients/order-accuracy", params={"branch": "BranchA"}).json()
    assert isinstance(acc, dict) and ("rows" in acc or acc.get("available") is False)


# ─── festival calendar ───────────────────────────────────────────────────────

def test_demand_calendar_round_trip(client):
    assert client.get("/api/ingredients/demand-calendar").json()["rows"] == []
    r = client.post("/api/ingredients/demand-calendar", json=[{
        "name": "สงกรานต์", "date_from": "2026-04-13", "date_to": "2026-04-15", "factor": 1.3}])
    assert r.status_code == 200 and r.json()["saved"] == 1
    rows = client.get("/api/ingredients/demand-calendar").json()["rows"]
    assert len(rows) == 1 and rows[0]["name"] == "สงกรานต์"
    eid = rows[0]["event_id"]
    assert client.delete(f"/api/ingredients/demand-calendar/{eid}").json()["ok"] is True
    assert client.get("/api/ingredients/demand-calendar").json()["rows"] == []


def test_season_factor_answers_without_last_years_data(client):
    r = client.get("/api/ingredients/season-factor",
                   params={"start": f"{_cf.LATEST_MONTH}-01", "end": END})
    assert r.status_code == 200
    j = r.json()
    assert isinstance(j, dict) and "factor" in j


@pytest.fixture(scope="module", autouse=True)
def _expose_client(client):
    """A few cases build several plans; a module-level handle keeps them short."""
    pytest.client = client
    yield
    del pytest.client
