"""The forced-order list (บังคับสั่ง) — owner's warehouse sheet, 2026-09-21.

"ปริมาณที่ควรสั่ง รายวัตถุดิบ ให้ใช้ตามนี้ครับ ที่บังคับสั่ง เบื้องต้นจะได้มอนิเตอร์ถูก"

A listed ingredient defaults to สั่งส่วนกลาง (monitored), anything else to สาขาซื้อเอง; a tick
made in the app still wins; and — found the same day — what is typed in the ordering tabs
must survive the daily DB swap, which it never did.
"""
import sqlite3

import pytest

import conftest as _cf
import main
import routers.ingredients as ing

END = f"{_cf.LATEST_MONTH}-03"
PLAN = {"date_to": END, "days": 28}
BOM = [
    (2, 1, "ING-SAL", "แซลมอน", 0.12, "กก.", 500.0, 60.0, "protein"),       # recipes call salmon ING-SAL...
    (2, 2, "ING-RICE", "ข้าวญี่ปุ่น", 0.05, "กก.", 40.0, 2.0, "carb"),
    (3, 1, "ING-BEEF", "เนื้อ", 0.20, "กก.", 600.0, 120.0, "protein"),
]
SHEET = (
    "code,name,unit,group,monthly_need,stock_months,remark,sheet_no,recipe_codes\n"
    "WH-SALMON,ปลาแซลมอนแช่แข็ง Salmon Frozen,KGx1,import,3028.33,0.41,ซื้อตปท.,7,ING-SAL\n"   # ...the warehouse WH-SALMON
    "ING-BEEF,เนื้อวัวสันใน Beef,KGx1,top40,5495.17,0.28,,2,\n"
    "WH-SOY,ซอสถั่วเหลือง 18L,Boxx1,top40,290.40,0.37,,38,\n"                                    # no recipe uses it
)


@pytest.fixture(scope="module", autouse=True)
def _seed(client):
    """Recipes for the fixture dishes - and put the shared DB back exactly as found: this
    module sorts BEFORE test_golden, whose snapshots must not see these rows."""
    con = sqlite3.connect(str(main.DB_PATH))
    keys = [(b[0], b[1]) for b in BOM]
    before = [r for k in keys for r in con.execute("SELECT * FROM bom_ingredients WHERE sku_id=? AND seq=?", k)]
    con.executemany(
        "INSERT OR REPLACE INTO bom_ingredients(sku_id, seq, ingredient_code, ingredient_name,"
        " qty_per_serving, unit, price_per_unit, ingredient_cost, ingredient_type, source_file)"
        " VALUES(?,?,?,?,?,?,?,?,?,'test')", BOM)
    con.commit(); con.close()
    main.invalidate_cache()
    yield
    con = sqlite3.connect(str(main.DB_PATH))
    con.executemany("DELETE FROM bom_ingredients WHERE sku_id=? AND seq=?", keys)
    if before:
        con.executemany(f"INSERT INTO bom_ingredients VALUES ({','.join('?' * len(before[0]))})", before)
    try:
        con.execute("DELETE FROM ingredient_sourcing")
    except sqlite3.OperationalError:
        pass
    con.commit(); con.close()
    main.invalidate_cache()


@pytest.fixture
def sheet(tmp_path, monkeypatch):
    f = tmp_path / "forced_order_2026-09.csv"
    f.write_text(SHEET, encoding="utf-8")
    monkeypatch.setenv("OSHINEI_FORCED_ORDER_FILE", str(f))
    _clear_sourcing(); main.invalidate_cache()
    yield f
    _clear_sourcing(); main.invalidate_cache()


def _clear_sourcing():
    con = sqlite3.connect(str(main.DB_PATH))
    try:
        con.execute("DELETE FROM ingredient_sourcing")
        con.commit()
    except sqlite3.OperationalError:
        pass
    con.close()


def _plan(client, **kw):
    r = client.get("/api/ingredients/order-plan", params={**PLAN, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _modes(plan):
    return {r["code"]: r["sourcing"] for r in plan["rows"]}


def test_without_a_list_everything_is_monitored_as_before(client):
    main.invalidate_cache()
    p = _plan(client)
    assert set(_modes(p).values()) == {"central"} and p["meta"]["forced"] == {"file": "", "items": 0}
    assert not any(r["forced"] for r in p["rows"])


def test_the_list_decides_what_is_monitored(client, sheet):
    p = _plan(client)
    # WH-SOY: on the sheet, used by no recipe — still on the plan, as a zero row (owner 2026-10-02)
    assert _modes(p) == {"ING-SAL": "central", "ING-BEEF": "central", "ING-RICE": "local", "WH-SOY": "central"}
    assert p["meta"]["forced"] == {"file": "forced_order_2026-09.csv", "items": 3}
    rows = {r["code"]: r for r in p["rows"]}
    assert rows["ING-BEEF"]["forced"] and rows["ING-BEEF"]["forced_group"] == "top40" and rows["ING-BEEF"]["forced_as"] == ""
    assert rows["ING-BEEF"]["forced_name"] == "เนื้อวัวสันใน Beef" and rows["ING-BEEF"]["no_usage"] is False
    # salmon is listed under the WAREHOUSE's code; the recipe code inherits it and says so
    assert rows["ING-SAL"]["forced"] and rows["ING-SAL"]["forced_group"] == "import" and rows["ING-SAL"]["forced_as"] == "WH-SALMON"
    assert rows["ING-SAL"]["forced_name"] == "ปลาแซลมอนแช่แข็ง Salmon Frozen"
    assert not rows["ING-RICE"]["forced"]
    soy = rows["WH-SOY"]
    assert soy["no_usage"] is True and soy["forced"] and soy["name"] == "ซอสถั่วเหลือง 18L" and soy["unit"] == "Boxx1"
    assert soy["order_week"] == 0 and soy["value_month"] == 0 and soy["n_menus"] == 0
    assert p["rows"][-1]["code"] == "WH-SOY"          # zero rows sort to the bottom
    # the letter / an issued cycle read include=central: every sheet line is there, rice is not
    assert set(_modes(_plan(client, include="central"))) == {"ING-SAL", "ING-BEEF", "WH-SOY"}
    assert set(_modes(_plan(client, include="local"))) == {"ING-RICE"}


def test_every_sheet_line_is_on_every_branch_and_a_search_still_narrows(client, sheet):
    for br in ("BranchA", "BranchB"):
        codes = {r["code"]: r for r in _plan(client, branch=br, include="central")["rows"]}
        assert "WH-SOY" in codes and codes["WH-SOY"]["no_usage"]
    # the chain view's per-branch roll-up does not count a sheet line nobody used
    p = _plan(client, include="central")
    assert all(b["ingredients"] <= 2 for b in p["branches"])
    assert [r["code"] for r in _plan(client, q="ซอส")["rows"]] == ["WH-SOY"]
    assert "WH-SOY" not in _modes(_plan(client, type="protein"))


def test_a_tick_made_in_the_app_still_wins_both_ways(client, sheet, monkeypatch):
    monkeypatch.delenv("OSHINEI_SEED_TOKEN", raising=False)
    r = client.post("/api/ingredients/sourcing", json=[
        {"ingredient_code": "ING-RICE", "branch": "", "mode": "central"},      # not listed, owner wants it watched
        {"ingredient_code": "ING-BEEF", "branch": "BranchA", "mode": "local"}])  # listed, but this branch has a butcher
    assert r.status_code == 200, r.text
    main.invalidate_cache()
    assert _modes(_plan(client))["ING-RICE"] == "central"
    assert _modes(_plan(client, branch="BranchA"))["ING-BEEF"] == "local"
    assert _modes(_plan(client, branch="BranchB"))["ING-BEEF"] == "central"
    # removing the override hands the row back to the LIST, not to the old "everything central"
    client.post("/api/ingredients/sourcing", json=[{"ingredient_code": "ING-RICE", "branch": "", "mode": "local"}])
    main.invalidate_cache()
    assert _modes(_plan(client))["ING-RICE"] == "local"


def test_the_list_as_the_app_understands_it(client, sheet):
    d = client.get("/api/ingredients/forced-order").json()
    assert d["file"] == "forced_order_2026-09.csv"
    assert d["summary"] == {"items": 3, "monitorable": 2, "via_alias": 1, "not_in_recipes": 1, "import": 1}
    by = {i["code"]: i for i in d["items"]}
    assert by["WH-SALMON"]["in_recipes"] and by["WH-SALMON"]["via_alias"] and by["WH-SALMON"]["matched_codes"] == ["ING-SAL"]
    assert by["ING-BEEF"]["in_recipes"] and not by["ING-BEEF"]["via_alias"]
    assert not by["WH-SOY"]["in_recipes"] and by["WH-SOY"]["monthly_need"] == 290.4      # the kitchen's worklist


def test_a_warehouse_code_is_never_shadowed_by_another_lines_alias(tmp_path, monkeypatch):
    f = tmp_path / "forced_order_x.csv"
    f.write_text("code,name,group,recipe_codes\nA,first,top40,B\nB,second,import,\n", encoding="utf-8")
    monkeypatch.setenv("OSHINEI_FORCED_ORDER_FILE", str(f))
    codes = ing._forced_order()["codes"]
    assert codes["B"]["name"] == "second" and codes["A"]["name"] == "first"


def test_the_shipped_sheet_is_well_formed(monkeypatch):
    monkeypatch.delenv("OSHINEI_FORCED_ORDER_FILE", raising=False)
    fo = ing._forced_order()
    assert fo["file"].startswith("forced_order_") and len(fo["items"]) == 48
    assert len({i["code"] for i in fo["items"]}) == 48
    groups = [i["group"] for i in fo["items"]]
    assert set(groups) == {"import", "top40"} and groups.count("import") == 13
    assert all(i["monthly_need"] and i["monthly_need"] > 0 for i in fo["items"])
    unagi = fo["codes"]["13-01-184"]                       # 146,208 packs on hand = 11.96 months: WHY it is forced
    assert unagi["group"] == "import" and unagi["stock_months"] == 11.96
    # owner's answers 2026-09-22: the frozen-salmon lines are NOT the recipes' แซลมอน (02-01-001), and
    # ปลาเงิน maps to HK-019 only; both frozen-salmon lines stay listed but no recipe uses them
    assert "02-01-001" not in fo["codes"] and "CK-019" not in fo["codes"]
    assert fo["codes"]["HK-019"]["code"] == "03-01-018" and fo["codes"]["NEW CODE-021"]["code"] == "13-01-451"
    assert "ไม่อยู่ในสูตร" in fo["codes"]["11-01-325"]["remark"]      # soy sauce is a table condiment, not a gap


def test_what_is_typed_in_the_ordering_tabs_survives_the_daily_swap(tmp_path):
    """Found 2026-09-21: none of these tables was preserved, so every sync erased the owner's
    ticks, the issued cycles, the actuals, the purchases and the festival calendar."""
    live, inc = tmp_path / "live.db", tmp_path / "inc.db"
    c = sqlite3.connect(str(live))
    c.execute(ing._ING_SOURCING_DDL)
    c.execute("INSERT INTO ingredient_sourcing(ingredient_code, branch, mode) VALUES('ING-RICE','', 'central')")
    for t in ("ingredient_order_issue", "ingredient_order_issue_line", "ingredient_order_actual",
              "ingredient_purchase_week", "demand_calendar"):
        c.execute(f"CREATE TABLE {t}(id INTEGER PRIMARY KEY, v TEXT)")
        c.execute(f"INSERT INTO {t}(v) VALUES('typed on the live site')")
    c.execute("CREATE TABLE ingredient_order_unit(ingredient_code TEXT PRIMARY KEY, order_unit TEXT)")   # seeded on the PC
    c.execute("INSERT INTO ingredient_order_unit VALUES('ING-SAL', 'edited on live')")
    c.commit(); c.close()
    c = sqlite3.connect(str(inc))
    c.execute("CREATE TABLE sales_transaction(id INTEGER PRIMARY KEY)")
    c.execute("CREATE TABLE ingredient_order_unit(ingredient_code TEXT PRIMARY KEY, order_unit TEXT)")
    c.execute("INSERT INTO ingredient_order_unit VALUES('ING-SAL', 'seeded on the PC')")
    c.commit(); c.close()

    safe, reason = main._carry_forward_and_verify(str(live), str(inc))
    assert safe, reason
    c = sqlite3.connect(str(inc))
    assert c.execute("SELECT ingredient_code, mode FROM ingredient_sourcing").fetchall() == [("ING-RICE", "central")]
    for t in ("ingredient_order_issue", "ingredient_order_issue_line", "ingredient_order_actual",
              "ingredient_purchase_week", "demand_calendar"):
        assert c.execute(f"SELECT v FROM {t}").fetchall() == [("typed on the live site",)], t
    # pack sizes travel WITH the base DB on purpose - the PC's seed wins
    assert c.execute("SELECT order_unit FROM ingredient_order_unit").fetchall() == [("seeded on the PC",)]
    c.close()
