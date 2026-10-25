"""ONE food-cost target (owner 2026-10-25: "food-cost target 58%").

rules.food_cost_target reads app_settings['food_cost_target'] (Settings → ค่าเริ่มต้น), default 58, valid 20-90.
Food cost % at or below it = ในเป้า, above it = เกินเป้า x.x จุด. It replaced the 55-70 band (food_cost_band),
the cost_target_lo/_hi pair of GP รายสาขา and the monthly pack, the buffet page's 55/70 query defaults and the
management report's hard-coded "~50%". Every endpoint that judged food cost now carries `food_cost_target`.
"""
import sqlite3

import pytest

import core
import rules


def _set(con, value):
    con.execute("DELETE FROM app_settings WHERE key='food_cost_target'")
    if value is not None:
        con.execute("INSERT INTO app_settings(key, value) VALUES('food_cost_target', ?)", (value,))


@pytest.fixture()
def mem():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT)")
    yield con
    con.close()


@pytest.fixture()
def target(client):
    """Set the target through the real settings endpoint; always back to unset afterwards."""
    def put(v):
        r = client.put("/api/settings", json={"food_cost_target": str(v)})
        assert r.status_code == 200, r.text
    yield put
    client.put("/api/settings", json={"food_cost_target": ""})
    con = sqlite3.connect(str(core.DB_PATH))
    con.execute("DELETE FROM app_settings WHERE key='food_cost_target'")
    con.commit(); con.close()
    core.invalidate_cache(rebuild_cost=False)


# ── rules ────────────────────────────────────────────────────────────────────
def test_default_is_58(mem):
    assert rules.FOOD_COST_TARGET == 58.0
    assert rules.food_cost_target(mem) == 58.0


def test_setting_overrides_the_default(mem):
    _set(mem, "60"); assert rules.food_cost_target(mem) == 60.0
    _set(mem, "20"); assert rules.food_cost_target(mem) == 20.0
    _set(mem, "90"); assert rules.food_cost_target(mem) == 90.0
    _set(mem, "57.5"); assert rules.food_cost_target(mem) == 57.5


@pytest.mark.parametrize("bad", ["", "abc", "19.9", "90.1", "0", "-5", "nan"])
def test_invalid_falls_back_to_58(mem, bad):
    _set(mem, bad)
    assert rules.food_cost_target(mem) == 58.0


def test_no_table_falls_back_to_58():
    con = sqlite3.connect(":memory:")
    assert rules.food_cost_target(con) == 58.0


def test_old_band_keys_are_not_carried_over(mem):
    mem.executemany("INSERT INTO app_settings VALUES(?, ?)", [("food_cost_band_lo", "55"), ("food_cost_band_hi", "70"),
                                                             ("cost_target_lo", "55"), ("cost_target_hi", "70")])
    assert rules.food_cost_target(mem) == 58.0
    assert not hasattr(rules, "food_cost_band") and not hasattr(rules, "FOOD_COST_BAND")


def test_status_words():
    assert rules.food_cost_status_th(58.0, 58.0) == "ในเป้า"          # at the target = good
    assert rules.food_cost_status_th(57.9, 58.0) == "ในเป้า"
    assert rules.food_cost_status_th(63.5, 58.0) == "เกินเป้า 5.5 จุด"
    assert rules.food_cost_status_th(None, 58.0) == "—"
    assert rules.food_cost_over_pp(63.5, 58.0) == 5.5 and rules.food_cost_over_pp(50, 58) == 0.0


# ── settings endpoint ────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", ["19", "91", "abc"])
def test_settings_refuses_an_invalid_target(client, bad):
    r = client.put("/api/settings", json={"food_cost_target": bad})
    assert r.status_code == 422
    assert "food_cost_target" not in client.get("/api/settings").json() or \
        client.get("/api/settings").json()["food_cost_target"] != bad


def test_settings_saves_the_target(client, target):
    target(61)
    assert client.get("/api/settings").json()["food_cost_target"] == "61"


# ── every endpoint returns the single target ─────────────────────────────────
ENDPOINTS = [
    ("/api/gp/by-branch-month", lambda d: d),
    ("/api/home/brief", lambda d: d["cost_health"]),
    ("/api/buffet/performance", lambda d: d["kpis"]),
    ("/api/analysis/food_cost_trend", lambda d: d),
    ("/api/bom-cost/branch-foodcost-mom", lambda d: d),
    ("/api/reports/management?lang=th", lambda d: d),
]
OLD_KEYS = {"target_lo", "target_hi", "band_lo", "band_hi", "food_cost_target_lo", "food_cost_target_hi"}


@pytest.mark.parametrize("path,pick", ENDPOINTS, ids=[p for p, _ in ENDPOINTS])
def test_endpoint_carries_the_one_target(client, target, path, pick):
    d = pick(client.get(path).json())
    assert d["food_cost_target"] == 58.0
    assert not (OLD_KEYS & set(d))
    target(62)
    d = pick(client.get(path).json())          # the settings write drops every cached response
    assert d["food_cost_target"] == 62.0


def test_food_cost_trend_alerts_on_the_target(client, target):
    d = client.get("/api/analysis/food_cost_trend").json()
    assert d["alert_threshold"] == 58.0
    target(20)                                  # the fixture books ~30% food cost: above a 20% target
    d = client.get("/api/analysis/food_cost_trend").json()
    assert d["alert_threshold"] == 20.0
    assert d["alerts"] and all("เกินเป้า" in a["message"] and "เป้า 20%" in a["message"] for a in d["alerts"])


def test_recalibration_defaults_to_the_target(client, target):
    assert client.get("/api/cost/recalibration").json()["target_food_cost_pct"] == 58.0
    target(50)
    assert client.get("/api/cost/recalibration").json()["target_food_cost_pct"] == 50.0
    assert client.get("/api/cost/recalibration?target_pct=40").json()["target_food_cost_pct"] == 40.0   # typed on the page wins


def test_buffet_performance_no_longer_takes_band_params(client):
    d = client.get("/api/buffet/performance?food_cost_target_lo=10&food_cost_target_hi=20").json()["kpis"]
    assert d["food_cost_target"] == 58.0


# ── the executive to-do uses the target ──────────────────────────────────────
def _fc_actions(client, lang):
    d = client.get(f"/api/reports/management?lang={lang}").json()
    return d, [a["text"] for a in d["actions"] if ("ต้นทุนอาหาร" if lang == "th" else "Food cost") in a["text"]]


def test_management_todo_uses_the_target(client, target):
    d, th = _fc_actions(client, "th")
    fc = d["pace"]["food_cost_pct"]
    assert fc is not None and fc > 58                     # the fixture's BoM month runs far above any target
    assert th == [f"ต้นทุนอาหาร {fc:.1f}% — เป้า 58% · เกินเป้า {fc - 58:.1f} จุด"]
    assert not any("~50" in a["text"] for a in d["actions"])
    _, en = _fc_actions(client, "en")
    assert en == [f"Food cost {fc:.1f}% — {fc - 58:.1f} pts above the 58% target."]
    target(90)                                            # still above 90? then the line moves with the target
    d, th = _fc_actions(client, "th")
    if d["pace"]["food_cost_pct"] > 90:
        assert th and "เป้า 90%" in th[0]
    else:
        assert th == []                                   # at or below the target: no to-do


# ── GP รายสาขา: the monthly pack status flips at the target ────────────────────
def test_monthly_pack_status_flips_at_the_target(client, target):
    from routers.export import _mpack_collect
    from conftest import LATEST_MONTH
    p = _mpack_collect(LATEST_MONTH)
    assert p["food_cost_target"] == 58.0 and "target_lo" not in p
    b = next(x for x in p["branches"] if x["food_cost_pct"] is not None)
    fc = b["food_cost_pct"]
    target(min(90.0, max(20.0, round(fc, 1))))           # exactly at the target = ในเป้า
    p = _mpack_collect(LATEST_MONTH)
    b = next(x for x in p["branches"] if x["branch"] == b["branch"])
    if 20.0 <= fc <= 90.0:
        assert b["status"] == "in" and b["status_th"] == "ในเป้า"
    target(20)
    p = _mpack_collect(LATEST_MONTH)
    b = next(x for x in p["branches"] if x["branch"] == b["branch"])
    if fc > 20:
        assert b["status"] == "out" and b["status_th"] == f"เกินเป้า {fc - 20:.1f} จุด"
