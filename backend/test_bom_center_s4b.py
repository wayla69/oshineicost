"""BoM Center — Sprint S4b: Forecast Engine v1 and the order calendar.

The promises:
  * the order calendar (sales team, owner §10-7) decides the cover period: a regular
    delivery lasts until the next regular delivery, fresh salmon a week, pre-ordered;
  * the daily forecast is the weekday-fair baseline × trend × season, from history
    that never crosses the Grand Menu changeover;
  * the suggestion is forecast + safety stock rounded UP to whole packs — and says
    plainly that on-hand stock is not deducted yet;
  * salmon is ordered only in its own round; a released recipe beats the legacy one;
  * a perfectly regular pattern backtests to ~0 % error.
"""
import sqlite3
from datetime import date, timedelta

import pytest

import auth
from bom_center import forecast as F
from bom_center import recipes as R
from bom_center import release as RL
from bom_center import snapshot_v0 as V0
from test_bom_center_s2 import _db

PATTERN = {0: 30, 1: 10, 2: 10, 3: 10, 4: 12, 5: 20, 6: 40}      # plates by SQLite %w (Sun=0)


@pytest.fixture
def con(tmp_path):
    c = _db(tmp_path)
    c.execute("UPDATE bc_ingredient SET recv_unit='แพ็ค 1 กก.', qty_per_recv=1000, moq=1 WHERE code='13-01-009'")
    c.execute("UPDATE bc_ingredient SET recv_unit='แพ็ค 1 กก.', qty_per_recv=1000, loss_pct=0 WHERE code='02-01-001'")
    c.executemany("INSERT INTO bom_ingredients(sku_id, seq, ingredient_code, ingredient_name, qty_per_serving, unit,"
                  " price_per_unit, ingredient_cost, ingredient_type) VALUES(?,?,?,?,?,?,?,?,?)", [
                      (10, 1, "13-01-009", "ปูอัด", 45, "กรัม", 0.15, 6.75, "protein"),     # crab sashimi: 45 g
                      (11, 1, "02-01-001", "แซลมอน", 20, "กรัม", 0.37, 7.4, "protein")])    # a salmon dish: 20 g
    d0 = date(2026, 9, 1)
    rows = []
    for i in range(35):                                     # five full weeks of the Grand Menu era
        d = d0 + timedelta(days=i)
        q = PATTERN[(d.weekday() + 1) % 7]
        rows += [(1, d.isoformat(), 10, q, 0.0), (1, d.isoformat(), 11, q, 0.0)]
    rows += [(1, "2026-08-20", 10, 999, 0.0)]                # the old menu: must never count
    c.executemany("INSERT INTO sales_sku_daily VALUES(?,?,?,?,?)", rows)
    c.commit()
    yield c
    c.close()


AS_OF = date(2026, 10, 5)       # the last day of the five weeks (Sep 1 + 34)


def test_cover_periods_from_the_calendar(con):
    o, dlv, cf, ct = F.cover_period(con, 1, "1", date(2026, 10, 6))         # a Tuesday
    assert (o, dlv, cf, ct) == (date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 7), date(2026, 10, 9))
    o, dlv, cf, ct = F.cover_period(con, 1, "2", date(2026, 10, 6))         # rolls forward to Friday
    assert o == date(2026, 10, 9) and dlv == date(2026, 10, 10) and ct == date(2026, 10, 13)
    o, dlv, cf, ct = F.cover_period(con, 1, "salmon", date(2026, 10, 6))    # Monday, a week ahead
    assert o == date(2026, 10, 12) and dlv == date(2026, 10, 19) and (ct - cf).days == 6
    con.execute("INSERT INTO bc_order_calendar(branch_id, round, order_weekday, lead_days) VALUES(1, '1', 3, 2)")
    o, dlv, cf, ct = F.cover_period(con, 1, "1", date(2026, 10, 6))         # Wednesday, 2 days to arrive
    assert o == date(2026, 10, 7) and dlv == date(2026, 10, 9) and ct == date(2026, 10, 9)


def test_weekday_fair_forecast_safety_and_packs(con):
    out = F.suggest(con, 1, "1", date(2026, 10, 6), season="off", as_of=AS_OF)
    assert out["on_hand_included"] is False
    assert out["history_from"] == "2026-09-01"                              # 6 weeks back, clipped at the cut-over
    crab = {r["code"]: r for r in out["rows"]}["13-01-009"]
    # cover Wed 7, Thu 8, Fri 9 -> 10 + 12 + 20 plates × 45 g, flat trend
    assert crab["forecast"] == pytest.approx(42 * 45, rel=1e-6)
    assert crab["trend"] == 1.0 and crab["safety"] == pytest.approx(0, abs=1e-6)   # no noise around the pattern
    assert crab["suggested_units"] == 2.0                                   # 1,890 g -> two 1 kg packs (round UP)
    assert "02-01-001" not in {r["code"] for r in out["rows"]}               # salmon waits for its own round
    sal = F.suggest(con, 1, "salmon", date(2026, 10, 6), season="off", as_of=AS_OF)
    assert [r["code"] for r in sal["rows"]] == ["02-01-001"]
    assert sal["rows"][0]["forecast"] == pytest.approx(sum(PATTERN.values()) * 20, rel=1e-6)   # a full week


def test_noise_gives_safety_stock_and_class_drives_z(con):
    con.execute("UPDATE sales_sku_daily SET qty = qty + 6 WHERE day IN ('2026-09-09', '2026-09-23') AND sku_id=10")
    out = F.suggest(con, 1, "1", date(2026, 10, 6), season="off", as_of=AS_OF)
    crab = {r["code"]: r for r in out["rows"]}["13-01-009"]
    assert crab["safety"] > 0 and crab["sigma"] > 0
    assert crab["safety"] == pytest.approx(F.Z[crab["class"]] * crab["sigma"] * 3 ** 0.5, rel=1e-3)


def test_released_recipe_beats_legacy(con):
    con.execute("INSERT INTO sku_cost(sku_id, unit_cost) VALUES(10, 5.0)")
    V0.build_v0(con)
    rid = R.get_or_create_draft(con, "fc", "2026-10", "2026-07", "a")
    R.save_lines(con, rid, "OTHBFSSM00020", [{"ingredient_code": "13-01-009", "qty": 60, "unit": "กรัม"}], "a")
    for r in RL.run_checks(con, rid)["rows"]:
        if r["check_code"] == "C05":
            RL.accept(con, rid, "C05", r["ref"], "ok", "a")
    RL.do_release(con, rid, "a", backdate_reason="test", today=date(2026, 10, 6))
    out = F.suggest(con, 1, "1", date(2026, 10, 6), season="off", as_of=AS_OF)
    crab = {r["code"]: r for r in out["rows"]}["13-01-009"]
    assert crab["forecast"] == pytest.approx(42 * 60, rel=1e-6)            # 60 g from the release, not 45 g
    assert out["stats"]["release_skus"] == 1


def test_backtest_on_a_regular_pattern_is_near_zero(con):
    b = F.backtest(con, 1, weeks=2, history_weeks=2, as_of=AS_OF)
    assert b["available"] and b["mape"] is not None and b["mape"] < 0.5


def test_missed_lines_are_reported_for_dishes_sold(con):
    con.execute("INSERT INTO bom_ingredients(sku_id, seq, ingredient_code, ingredient_name, qty_per_serving, unit,"
                " price_per_unit, ingredient_cost, ingredient_type) VALUES(10, 2, '13-01-009', 'ปูอัด', 1, 'ลิตร', 0, 0, 'x')")
    out = F.suggest(con, 1, "1", date(2026, 10, 6), season="off", as_of=AS_OF)
    assert out["stats"]["missed_lines"] == 1 and out["stats"]["missed"][0]["plates"] > 0


def test_calendar_rules_and_permissions(client):
    import main
    r = client.post("/api/bom-center/order-calendar", json=[{"branch_id": 1, "round": "1", "order_weekday": 4}])
    assert r.status_code == 400                                             # round 1 is Tue/Wed only
    r = client.post("/api/bom-center/order-calendar", json=[{"branch_id": 1, "round": "2", "order_weekday": 6,
                                                             "lead_days": 1}])
    assert r.status_code == 200
    cal = client.get("/api/bom-center/order-calendar").json()
    assert next(x for x in cal["rows"] if x["branch_id"] == 1)["r2"]["order_weekday"] == 6
    out = client.get("/api/bom-center/forecast?branch_id=1&round=1&season=off").json()
    assert out["round"] == "1" and "rows" in out
    saved = client.post("/api/bom-center/forecast/run", json={"branch_id": 1, "round": "1", "season": "off"}).json()
    assert saved["ok"]
    assert any(x["run_id"] == saved["run_id"] for x in client.get("/api/bom-center/forecast/runs").json()["rows"])
    assert client.get("/api/bom-center/forecast/backtest?branch_id=1").status_code == 200
    ss = auth.ROLE_MODULES["salessupport"]
    assert auth.path_allowed("POST", "/api/bom-center/order-calendar", ss)
    assert auth.path_allowed("POST", "/api/bom-center/forecast/run", ss)
    assert not auth.path_allowed("POST", "/api/bom-center/release-act/release", ss)
    assert not auth.path_allowed("POST", "/api/bom-center/ingredients", ss)
    c = sqlite3.connect(str(main.DB_PATH))
    c.execute("DELETE FROM bc_order_calendar")
    c.execute("DELETE FROM bc_forecast_line")
    c.execute("DELETE FROM bc_forecast_run")
    c.execute("DELETE FROM bc_audit WHERE action='order_calendar'")
    c.commit()
    c.close()
