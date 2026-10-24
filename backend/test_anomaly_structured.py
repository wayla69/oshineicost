# -*- coding: utf-8 -*-
"""PR-OB (2026-10-24): /api/analysis/anomalies feeds the overall page's insight box with structured fields.

  * every insight keeps its `message` sentence (the home action queue and the monthly report read it) and gains kind / label / value /
    unit / delta / delta_unit / prev / amount / branch / period / period_th / basis_note - the same figures the sentence prints;
  * `cost_series` = the food-cost % of every FULL month (the month still trading is left out) with its change in points;
  * a role with neither cost.analysis nor profit.pnl gets no cost insight and an empty cost_series (as the cost insights today);
  * owner 2026-10-24: the sales tiles compare to-date with the month before AND the same month last year (prev_year, delta_yoy,
    ranges) - the ranges come from the shared pure helper to_date.to_date_ranges(anchor, week | month | year).
"""
import pytest
from fastapi.testclient import TestClient

import auth
from conftest import LATEST_MONTH, PREV_MONTH, EARLY_MONTH

PW = auth.DEFAULT_PASSWORD
KINDS = {"revenue_mom", "branch_top", "branch_drop", "cost_up", "cost_down"}
FIELDS = ("kind", "label", "value", "unit", "delta", "delta_unit", "prev", "amount", "branch", "period", "period_th", "basis_note")


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    yield


def _as(user):
    import main
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"username": user, "password": PW})
    assert r.status_code == 200, r.text
    return c


def _th(ym):
    y, m = ym.split("-")
    return ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."][int(m) - 1] + f" {int(y) + 543}"


def test_every_insight_has_structured_fields_matching_its_message(client):
    # threshold -1: the fixture's flat months fire every rule (revenue, top and bottom branch, cost per month)
    d = client.get("/api/analysis/anomalies", params={"threshold": -1}).json()
    ins = d["insights"]
    assert {i["kind"] for i in ins} >= {"revenue_mom", "branch_top", "branch_drop", "cost_up"}
    for i in ins:
        for f in FIELDS:
            assert f in i, (f, i)
        assert i["kind"] in KINDS
        assert i["message"]                                   # the sentence stays for the other callers
        assert i["period_th"] == _th(i["period"])
        assert i["unit"] in ("฿", "%") and i["delta_unit"] in ("%", "pp")
        if i["kind"] == "revenue_mom":
            assert i["type"] in ("spike", "dip") and i["unit"] == "฿" and i["delta_unit"] == "%"
            assert i["value"] == i["amount"] and i["delta"] == i["mom_pct"]
            assert f"{abs(i['delta']):.1f}%" in i["message"]
            assert f"฿{i['amount']:,.0f}" in i["message"] and f"(ก่อนหน้า ฿{i['prev']:,.0f})" in i["message"]
            assert i["basis_note"] in i["message"]
            assert i["period"] == LATEST_MONTH and i["prev_period"] == PREV_MONTH
        elif i["kind"] in ("branch_top", "branch_drop"):
            assert i["branch"] and i["branch"] in i["message"]
            assert i["unit"] == "%" and i["value"] == i["delta"]
            assert f"{abs(i['delta']):.1f}%" in i["message"] and f"฿{i['amount']:,.0f}" in i["message"]
            assert i["label"] == ("เติบโตเร็วสุด" if i["kind"] == "branch_top" else "ลดมากสุด")
            assert i["basis_note"] in i["message"]
        else:
            assert i["type"] == ("cost_spike" if i["kind"] == "cost_up" else "cost_drop")
            assert i["unit"] == "%" and i["delta_unit"] == "pp" and i["branch"] is None
            assert f"{i['value']:.1f}% ของรายได้" in i["message"] and f"{abs(i['delta']):.1f} จุด" in i["message"]
    # the partial latest month is compared on equal days, and the basis says so
    assert d["meta"]["period"] == LATEST_MONTH and d["meta"]["prev_period"] == PREV_MONTH
    assert d["meta"]["mtd_day"] == 3 and d["meta"]["basis_note"] == "เทียบวันที่ 1–3 เท่ากัน"
    assert d["meta"]["period_th"] == _th(LATEST_MONTH)


def test_cost_series_full_months_only(client):
    d = client.get("/api/analysis/anomalies").json()
    s = d["cost_series"]
    months = [r["month"] for r in s]
    assert months == sorted(months) and months, s
    assert LATEST_MONTH not in months                        # still trading in the fixture (data to day 3)
    assert months == [EARLY_MONTH, PREV_MONTH]
    assert s[0]["delta"] is None and all(isinstance(r["delta"], (int, float)) for r in s[1:])
    for r in s:
        assert set(r) == {"month", "month_th", "pct", "delta"} and r["month_th"] == _th(r["month"])
        assert 0 < r["pct"] < 100
    for a, b in zip(s, s[1:]):
        assert abs(b["delta"] - (b["pct"] - a["pct"])) <= 0.11


def test_cost_fields_withheld_for_a_role_without_cost(client, app_db, auth_on):
    for user in ("marketing1", "operation1"):
        j = _as(user).get("/api/analysis/anomalies", params={"threshold": -1}).json()
        assert j["cost_series"] == [], user
        assert not any(i.get("kind") in ("cost_up", "cost_down") or i.get("type") in ("cost_spike", "cost_drop") for i in j["insights"]), user
        assert any(i.get("kind") == "revenue_mom" for i in j["insights"]), user   # the sales side stays
    mg = _as("management1").get("/api/analysis/anomalies", params={"threshold": -1}).json()
    assert mg["cost_series"] and any(i.get("kind") == "cost_up" for i in mg["insights"])


def test_to_date_ranges_month_week_year():
    from to_date import to_date_ranges
    r = to_date_ranges("2026-10-23", "month")
    assert r["current"] == ("2026-10-01", "2026-10-23") and r["previous"] == ("2026-09-01", "2026-09-23")
    assert r["last_year"] == ("2025-10-01", "2025-10-23") and r["day"] == 23 and r["complete"] is False
    r = to_date_ranges("2026-10-31", "month")                      # a finished month: whole months
    assert r["complete"] and r["previous"] == ("2026-09-01", "2026-09-30") and r["last_year"] == ("2025-10-01", "2025-10-31")
    r = to_date_ranges("2026-03-30", "month")                      # the previous month is shorter: clamped to its end
    assert r["previous"] == ("2026-02-01", "2026-02-28")
    r = to_date_ranges("2026-01-15", "month")
    assert r["previous"] == ("2025-12-01", "2025-12-15")
    r = to_date_ranges("2026-10-22", "week")                       # a Thursday: Mon..Thu, the week before, 52 weeks before
    assert r["current"] == ("2026-10-19", "2026-10-22") and r["previous"] == ("2026-10-12", "2026-10-15")
    assert r["last_year"] == ("2025-10-20", "2025-10-23") and r["day"] == 4 and not r["complete"]
    r = to_date_ranges("2028-02-29", "year")
    assert r["current"] == ("2028-01-01", "2028-02-29") and r["last_year"] == ("2027-01-01", "2027-02-28") == r["previous"]
    with pytest.raises(ValueError):
        to_date_ranges("2026-10-01", "day")


def test_tiles_carry_last_year_fields_and_ranges(client):
    d = client.get("/api/analysis/anomalies", params={"threshold": -1}).json()
    tiles = [i for i in d["insights"] if i["kind"] in ("revenue_mom", "branch_top", "branch_drop")]
    assert tiles
    for i in tiles:
        for f in ("prev_year", "delta_yoy", "prev_year_period", "prev_year_period_th", "ranges"):
            assert f in i, (f, i)
        assert i["prev_year_period"] == "2025-06"                 # the fixture has no 2025: no figure, no change
        assert i["prev_year"] is None and i["delta_yoy"] is None
        assert i["ranges"] == {"current": ["2026-06-01", "2026-06-03"], "previous": ["2026-05-01", "2026-05-03"],
                               "last_year": ["2025-06-01", "2025-06-03"]}
    assert d["meta"]["ranges"]["current"] == ["2026-06-01", "2026-06-03"] and d["meta"]["prev_year_period_th"] == "มิ.ย. 2568"
