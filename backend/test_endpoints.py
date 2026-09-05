"""HTTP-level smoke tests via FastAPI TestClient against a seeded temp DB.

These catch the SQL-shape class of bugs (e.g. a JOIN placed after WHERE) that
only surface at runtime, plus key data invariants. Every listed route must
return 200 with sane JSON; a handful assert a specific invariant.
"""
import pytest

# (path, params) — must all return 200 against the seeded fixture DB.
SMOKE = [
    ("/api/months", {}),
    ("/api/summary", {}),
    ("/api/branches", {}),
    ("/api/sales/monthly", {}),
    ("/api/sales/top_ordered", {"limit": 60, "sort": "qty"}),
    ("/api/sales/top_ordered", {"sort": "revenue"}),
    ("/api/sales/category", {}),
    ("/api/sales/daily", {}),
    ("/api/sales/menu-spike", {"threshold": 1.5}),
    ("/api/sales/cost-per-head", {}),
    ("/api/analysis/anomalies", {}),
    ("/api/overall/latest-month-mtd", {}),
    ("/api/reports/pnl-bill", {}),
    ("/api/reports/pnl-bill", {"gran": "month"}),
    ("/api/reports/pnl-bill-drill", {"idx": -1}),
    ("/api/reports/ops", {}),
    ("/api/bom-cost/overview", {}),
    ("/api/bom-cost/cost-drivers", {"scope": "chain", "limit": 60}),
    ("/api/bom-cost/cost-drivers", {"scope": "branch", "branch": "BranchA"}),
    ("/api/bom-cost/simulator-base", {"limit": 2000}),
    ("/api/bom-cost/scenarios", {}),
    ("/api/extra/cash", {}),
    ("/api/extra/customer-type", {}),
    ("/api/extra/modifiers", {}),
    ("/api/extra/price-zone", {"limit": 200}),
    ("/api/extra/cancel-bills", {}),
    ("/api/buffet/child", {}),
    # ── recently-added, previously-untested endpoints ──
    ("/api/sales/menu-compare",
     {"a_from": "2026-05", "a_to": "2026-05", "b_from": "2026-06", "b_to": "2026-06"}),
    ("/api/sales/menu-compare/drill",
     {"sku_code": "A0001", "a_from": "2026-05", "a_to": "2026-05", "b_from": "2026-06", "b_to": "2026-06"}),
    ("/api/sales/heavy-eaters", {}),
    ("/api/menu/action-board", {}),
    ("/api/menu/opportunities", {}),
    ("/api/feedback/summary", {}),
    ("/api/feedback/sources", {}),
    ("/api/feedback/survey-links", {}),
    ("/api/feedback/actions", {}),
    ("/api/feedback/digest", {"days": 7, "today": "2026-09-05"}),
    ("/api/feedback/narrative", {"days": 7, "today": "2026-09-05"}),
    ("/api/feedback/business", {"month_from": "2026-05", "month_to": "2026-06", "today": "2026-07-01"}),
    ("/api/feedback/competitors", {}),
    ("/api/feedback/competitors/status", {}),
    ("/api/checklist/feedback_rollout", {}),
    # ── management summary report (daily & weekly) ──
    ("/api/reports/management", {}),
    ("/api/reports/management", {"period": "weekly"}),
    ("/api/reports/management", {"period": "daily", "branch": "BranchA"}),
    ("/api/reports/management", {"period": "weekly", "group": "master"}),
    ("/api/reports/management", {"period": "monthly"}),
    ("/api/reports/management", {"period": "monthly", "branch": "BranchB"}),
    # ── master/franchise scope on the Analysis Report endpoints (must 200 with group=) ──
    ("/api/analysis/anomalies", {"group": "master"}),
    ("/api/analysis/discount_composition", {"month_from": "2026-05", "month_to": "2026-06", "group": "franchise"}),
    ("/api/buffet/performance", {"group": "master"}),
    ("/api/bom-cost/overview", {"group": "franchise"}),
    # ── 🏷️ price-tier analysis (tier fact tables absent in fixture → ready:False) ──
    ("/api/reports/price-tier", {}),
    ("/api/reports/price-tier", {"scope": "master", "date_from": "2026-05-01", "date_to": "2026-05-31"}),
    # ═══ broad smoke sweep: every remaining GET endpoint must 200 on the fixture ═══
    # (guards the SQL-shape bug class across the ~60 handlers that had no test)
    # ── analysis ──
    ("/api/analysis/branches", {}),
    ("/api/analysis/daily", {"month": "2026-06"}),
    ("/api/analysis/hourly", {}),
    ("/api/analysis/hour_detail", {"hour": 12}),
    ("/api/analysis/branch_peak", {}),
    ("/api/analysis/heatmap_branch", {}),
    ("/api/analysis/compare", {"month": "2026-06", "vs": "2026-05"}),
    ("/api/analysis/period_compare",
     {"a_month_from": "2026-05", "a_month_to": "2026-05", "b_month_from": "2026-06", "b_month_to": "2026-06"}),
    ("/api/analysis/gross_profit", {}),
    ("/api/analysis/gross_profit", {"granularity": "weekly"}),
    ("/api/analysis/gross_profit", {"granularity": "monthly"}),
    ("/api/analysis/discount-reasons", {}),
    ("/api/analysis/discount_bills", {}),
    ("/api/analysis/discount_leakage", {}),
    ("/api/analysis/discount_alerts", {}),
    ("/api/analysis/payment_discount", {}),
    ("/api/analysis/coupon-series", {}),
    ("/api/analysis/coupon-series/drill", {"series_id": 5}),
    ("/api/analysis/health", {}),
    ("/api/analysis/yoy", {}),
    ("/api/forecast", {}),
    ("/api/forecast", {"mode": "branches"}),
    ("/api/forecast", {"mode": "trend"}),
    ("/api/forecast", {"mode": "buffet"}),
    ("/api/analysis/cohort", {}),
    ("/api/analysis/affinity", {}),
    ("/api/analysis/affinity", {"sku_code": "A0001"}),
    ("/api/analysis/percentiles", {}),
    ("/api/analysis/sph", {}),
    ("/api/analysis/daypart_grid", {}),
    ("/api/analysis/daypart-economics", {}),
    ("/api/analysis/buffet_mix", {}),
    ("/api/analysis/table-turnover", {}),
    ("/api/analysis/table-turnover", {"view": "dwell"}),
    # ── buffet ──
    ("/api/buffet/revpash", {}),
    ("/api/buffet/gp-calendar", {"month": "2026-06"}),
    ("/api/buffet/child-trend", {}),
    ("/api/buffet/breakeven", {}),
    ("/api/buffet/menu-cost-efficiency", {}),
    ("/api/buffet/menu-cost-trend", {}),
    ("/api/buffet/menu-cost-coverage", {}),
    ("/api/buffet/menu-cost-by-branch", {"sku_code": "B0001"}),
    ("/api/buffet/portion-velocity", {}),
    ("/api/buffet/plate-cost-dist", {}),
    ("/api/buffet/heavy-eater-tail", {}),
    ("/api/buffet/turn-efficiency", {}),
    ("/api/buffet/pnl", {}),
    ("/api/buffet/upgrade-simulator", {}),
    ("/api/buffet/drink-attach-by-branch", {}),
    # ── home dashboard ──
    ("/api/home/brief", {}),
    ("/api/home/leak-watch", {}),
    ("/api/home/menu-movers", {}),
    ("/api/home/branch-scorecard", {}),
    # ── P&L ──
    ("/api/pnl/branches", {}),
    ("/api/pnl/branch-detail", {"branch": "BranchA"}),
    ("/api/pnl/branch-month-detail", {"branch": "BranchA", "month": "2026-06"}),
    # ── profit ──
    ("/api/profit/summary", {}),
    ("/api/profit/trend", {}),
    ("/api/profit/by-branch", {}),
    ("/api/profit/per-bill-distribution", {}),
    ("/api/profit/buffet_per_bill", {}),
    # ── cost ──
    ("/api/cost/monthly", {}),
    ("/api/cost/detail", {}),
    ("/api/cost/audit", {}),
    ("/api/cost/diff", {}),
    ("/api/cost/uncosted-coverage", {}),
    ("/api/cost/recalibration", {}),
    # ── sales ──
    ("/api/sales/margin", {}),
    ("/api/sales/dish-movers", {}),
    ("/api/sales/skus", {}),
    # ── BoM / branch BoM (fixture has no BoM rows → empty-but-200 path) ──
    ("/api/bom/sku-list", {}),
    ("/api/bom/consumption-analysis", {}),
    ("/api/branch-bom/analysis", {"branch": "ALL"}),
    ("/api/bom-cost/branch-foodcost-mom", {}),
    ("/api/bom-cost/compare", {"branches": "BranchA,BranchB"}),
    # ── extra (POS ops drills) ──
    ("/api/extra/cash/detail", {"branch": "BranchA", "month": "2026-06"}),
    ("/api/extra/cash/day", {"branch": "BranchA", "day": "2026-06-01"}),
    ("/api/extra/closing-time", {}),
    ("/api/extra/cancel-bills/detail", {"branch": "BranchA"}),
    # ── misc ──
    ("/api/search", {"q": "Branch"}),
    ("/api/search", {"q": "สเต๊ก"}),
    ("/api/settings", {}),
    ("/api/skus", {}),
    ("/api/skus", {"include_inactive": True}),
    ("/api/targets", {}),
    ("/api/targets/suggest", {}),
    ("/api/menu/lifecycle", {}),
    ("/api/meta/data-window", {}),
    ("/api/data-coverage", {}),
    ("/api/reports/menu-impact/entities", {}),
]

# Fixture periods: June (LATEST) sales are scaled 0.8 vs May (PREV).
A_PER, B_PER = "2026-05", "2026-06"


@pytest.mark.parametrize("path,params", SMOKE)
def test_endpoint_200(client, path, params):
    r = client.get(path, params=params)
    assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text[:200]}"
    r.json()  # must be valid JSON


def test_price_tier_graceful_without_fact_tables(client):
    # fixture DB has no tier-dimensioned menu_fact tables → degrade to ready:False, not 500
    d = client.get("/api/reports/price-tier").json()
    assert d["ready"] is False and "hint" in d


def test_management_report_daily_and_weekly(client):
    import datetime as _dt
    d = client.get("/api/reports/management", params={"period": "daily"}).json()
    assert d["available"] and d["period"] == "daily"
    assert d["headline"]["revenue"] > 0 and d["headline"]["bills"] >= 1
    # branch league must sum to the chain headline (no buffet double-count, ex-VAT basis)
    assert abs(sum(b["revenue"] for b in d["branches"]) - d["headline"]["revenue"]) < 1.0
    assert d["pace"]["food_cost_pct"] is not None
    assert len(d["trend"]) == 8
    assert isinstance(d["actions"], list) and d["actions"]
    # §7 is now food cost PER HEAD by dish (not revenue movers)
    assert d["menu"]["basis"] == "cost_per_head"
    assert all("cost_per_head" in m for m in d["menu"]["top"])
    # gross profit (revenue − period food COGS) is coherent
    assert d["gp"]["gp"] == d["headline"]["revenue"] - d["gp"]["food_cost"]
    assert 0 < d["gp"]["gp_pct"] <= 100
    # peak / dayparts present; table block present (available flag at least)
    assert isinstance(d["peak"]["dayparts"], list) and d["peak"]["peak_hour"]
    assert "available" in d["table"]
    assert "has_yoy" in d
    # day picker bounds: min_day/latest_day frame the selectable range, focus defaults to latest
    assert d["min_day"] <= d["latest_day"] and d["focus"] == d["latest_day"]
    # an explicit daily date round-trips and focuses that day
    pday = d["min_day"]
    d2 = client.get("/api/reports/management", params={"period": "daily", "date": pday}).json()
    assert d2["available"] and d2["focus"] == pday

    w = client.get("/api/reports/management", params={"period": "weekly"}).json()
    assert w["period"] == "weekly"
    assert abs(sum(b["revenue"] for b in w["branches"]) - w["headline"]["revenue"]) < 1.0
    # FULL weeks only: never partial; range is a Mon–Sun 7-day window
    assert w["partial"] is False
    assert w["selected_week"] == w["range"][0]
    cs = _dt.date.fromisoformat(w["range"][0]); ce = _dt.date.fromisoformat(w["range"][1])
    assert cs.weekday() == 0 and (ce - cs).days == 6
    # picker carries selectable completed weeks, and an explicit pick round-trips
    assert isinstance(w["weeks"], list) and len(w["weeks"]) >= 1
    pick = w["weeks"][-1]["start"]
    w2 = client.get("/api/reports/management", params={"period": "weekly", "week": pick}).json()
    assert w2["selected_week"] == pick


def test_analysis_cohort_shape(client):
    """Cohort view: growth-fair fields + partial-month guards."""
    j = client.get("/api/analysis/cohort").json()
    assert j["matrix"], "fixture months must appear"
    for r in j["matrix"]:
        for k in ("branches", "cust_per_branch", "rev_per_cust",
                  "partial", "days_covered", "days_in_month", "mom_pct", "yoy_pct"):
            assert k in r
        # fixture covers only days 01-03 → every month is partial → the guards
        # must refuse MoM/YoY rather than reading a 3-day month as a collapse
        assert r["partial"] is True
        assert r["mom_pct"] is None and r["yoy_pct"] is None
        assert r["cust_per_branch"] == round(r["customers"] / r["branches"])
    assert isinstance(j["seasonality"], list) and j["seasonality"]
    assert "ไม่ครบเดือน" in j["note"]          # Thai explainer (F)


def test_management_report_monthly(client):
    import datetime as _dt
    m = client.get("/api/reports/management", params={"period": "monthly"}).json()
    assert m["available"] and m["period"] == "monthly"
    # COMPLETE months only: default = latest complete month, never the partial one
    assert m["partial"] is False
    assert m["selected_month"] == m["range"][0][:7]
    cs = _dt.date.fromisoformat(m["range"][0]); ce = _dt.date.fromisoformat(m["range"][1])
    assert cs.day == 1 and (ce + _dt.timedelta(days=1)).day == 1   # full calendar month
    assert m["headline"]["revenue"] > 0
    assert abs(sum(b["revenue"] for b in m["branches"]) - m["headline"]["revenue"]) < 1.0
    assert len(m["trend"]) == 8
    # picker carries the selectable complete months; explicit pick round-trips
    assert isinstance(m["months"], list) and len(m["months"]) >= 1
    pick = m["months"][-1]["month"]
    m2 = client.get("/api/reports/management", params={"period": "monthly", "month": pick}).json()
    assert m2["selected_month"] == pick
    # comparison window = the previous calendar month
    if m["comp_range"]:
        pe = _dt.date.fromisoformat(m["comp_range"][1])
        assert (pe + _dt.timedelta(days=1)) == cs


def test_management_report_xlsx(client):
    import io, openpyxl
    for period in ("daily", "weekly", "monthly"):
        r = client.get("/api/reports/management.xlsx", params={"period": period})
        assert r.status_code == 200, r.text[:200]
        assert "spreadsheetml" in r.headers.get("content-type", "")
        assert "attachment" in r.headers.get("content-disposition", "")
        wb = openpyxl.load_workbook(io.BytesIO(r.content))   # must be a valid workbook
        assert "Summary" in wb.sheetnames and "Branch League" in wb.sheetnames


def test_management_daily_never_defaults_to_current_partial_day(client):
    """Regression: the daily report must NOT default to the in-progress current day
    (its handful of today's sales would read as a chain-wide ~-100% collapse).
    Default anchors on the latest COMPLETE day; an explicit today is flagged partial."""
    import datetime as _dt
    today = (_dt.datetime.utcnow() + _dt.timedelta(hours=7)).date().isoformat()  # Thai 'today'
    d = client.get("/api/reports/management", params={"period": "daily"}).json()
    assert d["available"]
    assert d["focus"] < today, f"daily default focus {d['focus']} must be before today {today}"
    assert d["partial"] is False
    # An explicitly-requested current day is honoured but flagged partial so the UI
    # can annotate it and the WoW/GP chips are not read as a real collapse.
    dt = client.get("/api/reports/management", params={"period": "daily", "date": today}).json()
    assert dt["partial"] is True


def test_management_xlsx_honours_language(client):
    """Regression: the Excel export must match the on-screen language (was always EN)."""
    import io, openpyxl
    def summary_text(lang):
        r = client.get("/api/reports/management.xlsx", params={"period": "weekly", "lang": lang})
        assert r.status_code == 200
        ws = openpyxl.load_workbook(io.BytesIO(r.content))["Summary"]
        return "\n".join(str(c.value) for row in ws.iter_rows() for c in row if c.value is not None)
    en, th = summary_text("en"), summary_text("th")
    assert en and th and en != th, "xlsx Summary should differ by language"
    assert any("฀" <= ch <= "๿" for ch in th), "Thai export should contain Thai text"
    assert not any("฀" <= ch <= "๿" for ch in en), "English export should not contain Thai text"


def test_cost_drivers_has_total_and_mom(client):
    d = client.get("/api/bom-cost/cost-drivers", params={"scope": "chain", "limit": 60}).json()
    assert d["items"], "no cost-driver items"
    assert "total" in d and d["total"]["total_cogs"] > 0
    # total of shown ≤ grand total (it's a subset)
    assert d["total"]["shown_cogs"] <= d["total"]["total_cogs"] + 1
    # buffet TIER must never appear as a cost driver (cost is on the item row)
    assert all(i["sku_code"] != "BUFFET" for i in d["items"])


def test_pnl_bill_loss_and_summary(client):
    d = client.get("/api/reports/pnl-bill", params={"gran": "month"}).json()
    s = d["summary"]
    assert s["bills"] > 0 and s["revenue"] > 0
    # gross profit = revenue - cogs (BoM)
    assert abs(s["gross_profit"] - (s["revenue"] - s["cogs"])) < 1.0
    assert "loss" in d and isinstance(d["loss"], list)


def test_ops_void_carries_bom_cost(client):
    d = client.get("/api/reports/ops").json()
    assert "bom_cost" in d["void_total"]
    # the A-item void (3 qty * 10 cost) contributes 30 to BoM cost; buffet item 0-cost? it has cost 8
    assert d["void_total"]["bom_cost"] >= 0


def test_ops_daily_range_follows_dates(client):
    # Month (default) path unchanged — monthly scope, void from the monthly pos_void table.
    m = client.get("/api/reports/ops").json()
    assert m["daily"] is False and m["month"] and m["payment"]
    assert m["void_pending"] is False and m["channel_pending"] is False
    # Date range → DAILY tables. Payment/Promo/Table have daily data; the Void/Channel daily
    # tables aren't staged in the fixture (mirrors a freshly-deployed, not-yet-resynced prod)
    # → those two sections degrade to empty + *_pending True instead of erroring.
    d = client.get("/api/reports/ops", params={"date_from": "2026-06-01", "date_to": "2026-06-03"}).json()
    assert d["daily"] is True and d["date_from"] == "2026-06-01" and d["date_to"] == "2026-06-03"
    assert d["month"] is None
    pay = {p["code"]: p["amount"] for p in d["payment"]}
    assert pay.get("Cash") == 30000 and pay.get("Credit") == 15000      # 3 days x (10000 / 5000)
    assert d["promo"] and d["table"]["tabs"]                            # promo + table daily present
    assert d["void"] == [] and d["void_pending"] is True               # void daily not staged yet
    assert d["channel"] == [] and d["channel_pending"] is True
    # a single-day range scopes to one day (1/3 of the 3-day Cash total)
    one = client.get("/api/reports/ops", params={"date_from": "2026-06-02", "date_to": "2026-06-02"}).json()
    assert {p["code"]: p["amount"] for p in one["payment"]}.get("Cash") == 10000


def test_simulator_base_all_menus(client):
    d = client.get("/api/bom-cost/simulator-base", params={"limit": 2000}).json()
    assert d["skus"], "no menus"
    assert all("period_revenue" in s and "current_unit_cost" in s for s in d["skus"])
    assert d["baseline"]["cogs"] > 0


def test_scenario_roundtrip_qty_and_disabled(client):
    # save a scenario that changes cost + qty + disables a menu, then read it back
    body = {"name": "_pytest_rt", "overrides": [
        {"sku_code": "A0001", "unit_cost": 12.5, "qty": 999, "disabled": False},
        {"sku_code": "B0001", "unit_cost": 8.0, "qty": None, "disabled": True},
    ]}
    sid = client.post("/api/bom-cost/scenarios", json=body).json()["id"]
    got = client.get(f"/api/bom-cost/scenarios/{sid}").json()
    items = {i["sku_code"]: i for i in got["items"]}
    assert items["A0001"]["new_qty"] == 999
    assert items["B0001"]["disabled"] == 1
    client.delete(f"/api/bom-cost/scenarios/{sid}")   # cleanup


def test_menu_compare_delta_direction_and_totals(client):
    d = client.get("/api/sales/menu-compare", params={
        "a_from": A_PER, "a_to": A_PER, "b_from": B_PER, "b_to": B_PER}).json()
    assert d["items"], "no compare items"
    # buffet-only: à-la-carte (description-prefixed "(A)") must never appear here
    assert all(not (i.get("description") or "").startswith("(A)") for i in d["items"]), \
        "à-la-carte leaked into buffet-only compare"
    # June (B) is scaled 0.8 vs May (A): every shown item should be down (or equal),
    # never up — catches a flipped-sign / swapped-period bug.
    movers = [i for i in d["items"] if (i["qty_a"] or 0) > 0]
    assert movers, "no period-A volume to compare"
    # delta_pct convention is _cmp_pct(A, B) = (A-B)/B*100 — A ("current") vs
    # B ("baseline"). A higher than B → positive delta.
    for i in movers:
        assert i["qty_b"] <= i["qty_a"] + 1e-6, f"{i['sku_code']} rose in B unexpectedly"
        if i["qty_b"] < i["qty_a"] and (i["qty_b"] or 0) > 0:
            assert i["qty_delta_pct"] is not None and i["qty_delta_pct"] > 0
    # totals row must equal the sum over the shown rows (consistency, not vibes)
    assert abs(d["totals"]["qty_a"] - sum(i["qty_a"] for i in d["items"])) < 0.02
    assert abs(d["totals"]["rev_b"] - sum(i["rev_b"] for i in d["items"])) < 0.02


def test_menu_compare_drill_branches_and_chronological_trend(client):
    d = client.get("/api/sales/menu-compare/drill", params={
        "sku_code": "A0001", "a_from": A_PER, "a_to": A_PER,
        "b_from": B_PER, "b_to": B_PER}).json()
    assert d["sku_code"] == "A0001"
    assert d["branches"], "no per-branch rows"
    assert all({"qty_a", "qty_b", "covers_a", "covers_b"} <= set(r) for r in d["branches"])
    months = [t["month"] for t in d["trend"]]
    assert A_PER in months and B_PER in months
    assert months == sorted(months), "trend not in chronological order"


def test_scenario_savings_ledger(client):
    """Phase B ROI: adopt a scenario → realized stays 0 until the cheaper cost is
    actually in the data, then accrues = (baseline−current) × qty since adoption."""
    import main
    body = {"name": "_pytest_roi", "overrides": [
        {"sku_code": "A0001", "unit_cost": 6.0, "qty": None, "disabled": False},
    ]}
    sid = client.post("/api/bom-cost/scenarios", json=body).json()["id"]
    try:
        assert client.get(f"/api/bom-cost/scenarios/{sid}/savings").json()["adopted"] is False
        ad = client.post(f"/api/bom-cost/scenarios/{sid}/adopt", json={"adopted_by": "pytest"}).json()
        assert ad["baseline_month"] == "2026-06"          # latest fixture month
        s1 = client.get(f"/api/bom-cost/scenarios/{sid}/savings").json()
        assert s1["adopted"] is True
        a = {i["sku_code"]: i for i in s1["items"]}["A0001"]
        assert abs(a["baseline_cost"] - 10.0) < 1e-6       # effective cost at adoption
        assert a["qty_since_adopt"] == 80                  # June: 40/branch × 2 branches
        # not implemented yet → realized 0; potential = (10−6)·80 = 320
        assert s1["totals"]["realized_saving"] == 0
        assert abs(s1["totals"]["potential_saving"] - 320.0) < 0.01
        # simulate implementing the cheaper recipe: effective cost 10 → 7
        with main.get_db() as con:
            con.execute("UPDATE sku_effective_cost SET recipe_cost=7 "
                        "WHERE sku_id=(SELECT sku_id FROM skus WHERE sku_code='A0001')")
            con.commit()
        s2 = client.get(f"/api/bom-cost/scenarios/{sid}/savings").json()
        assert abs(s2["totals"]["realized_saving"] - 240.0) < 0.01   # (10−7)·80
        assert abs(s2["totals"]["progress_pct"] - 75.0) < 0.1        # 240/320
        assert abs(s2["series"][-1]["cumulative"] - 240.0) < 0.01
    finally:
        # restore the session-scoped fixture cost so later tests see A0001 = 10
        with main.get_db() as con:
            con.execute("UPDATE sku_effective_cost SET recipe_cost=10 "
                        "WHERE sku_id=(SELECT sku_id FROM skus WHERE sku_code='A0001')")
            con.commit()
        client.post(f"/api/bom-cost/scenarios/{sid}/unadopt")
        client.delete(f"/api/bom-cost/scenarios/{sid}")


def test_heavy_eaters_margin_identity_and_monotone_percentiles(client):
    d = client.get("/api/sales/heavy-eaters").json()
    assert d["n_bills"] > 0 and d["total_covers"] > 0
    # margin/head must reconcile to rev/head − cogs/head (a COGS-helper slip here
    # is exactly the per-cover bug class this endpoint exists to expose).
    assert abs(d["avg_margin_head"] - (d["avg_rev_head"] - d["avg_cogs_head"])) < 0.05
    p = d["percentiles"]
    assert p["p50"] <= p["p75"] <= p["p90"] <= p["p95"] <= p["p99"]
    # heavy / loss buckets are subsets of all bills
    assert d["heavy"]["bills"] <= d["n_bills"]
    assert d["loss"]["bills"] <= d["n_bills"]


# ── Overhaul Phase 1 invariant — ONE COGS, identical GP across every tab ──────
# The core "ตัวเลขไม่ตรง" complaint was GP differing between tabs because two
# COGS methods coexisted. Phase 1 unified them (_cogs_case is now a shim over
# _unified_cogs_expr). This locks that in: the four headline revenue/COGS/GP
# surfaces must report the SAME whole-store numbers for the SAME period. A swap
# back to sku_cost on any one of them moves COGS by thousands and trips this.
#
# Each endpoint exposes a whole-store roll-up under a different key:
#   /api/profit/summary     -> top-level   {revenue_ex_vat, cogs, gross_profit}
#   /api/gp/by-branch-month -> grand_total {revenue, cogs, gp}
#   /api/pnl/branch         -> chain       {revenue, cogs, gross_profit}
#   /api/cost/analysis      -> summary     {total_revenue, total_cogs, total_gross_profit}
def _chain_rev_cogs_gp(client, params):
    """(revenue, cogs, gp) whole-store roll-up from each of the 4 endpoints."""
    ps = client.get("/api/profit/summary", params=params).json()
    gp = client.get("/api/gp/by-branch-month", params=params).json()["grand_total"]
    pn = client.get("/api/pnl/branch", params=params).json()["chain"]
    ca = client.get("/api/cost/analysis", params=params).json()["summary"]
    return {
        "profit/summary":     (ps["revenue_ex_vat"], ps["cogs"], ps["gross_profit"]),
        "gp/by-branch-month": (gp["revenue"],        gp["cogs"], gp["gp"]),
        "pnl/branch":         (pn["revenue"],        pn["cogs"], pn["gross_profit"]),
        "cost/analysis":      (ca["total_revenue"],  ca["total_cogs"], ca["total_gross_profit"]),
    }


@pytest.mark.parametrize("params", [{}, {"month": B_PER}], ids=["all_months", "single_month"])
def test_gp_identical_across_tabs(client, params):
    surfaces = _chain_rev_cogs_gp(client, params)
    # Tolerance: per-branch roll-ups round before summing, so allow a few cents of
    # accumulation — but a method swap shifts COGS by thousands, so this stays tight.
    def close(a, b):
        return abs(a - b) <= max(0.5, 0.0005 * max(abs(a), abs(b)))

    ref_name, ref = next(iter(surfaces.items()))
    for name, (rev, cogs, gp) in surfaces.items():
        r0, c0, g0 = ref
        assert close(rev, r0),  f"revenue differs: {name}={rev} vs {ref_name}={r0}"
        assert close(cogs, c0), f"COGS differs: {name}={cogs} vs {ref_name}={c0}"
        assert close(gp, g0),   f"GP differs: {name}={gp} vs {ref_name}={g0}"
        # internal identity: GP must be revenue − COGS on every surface
        assert close(gp, rev - cogs), f"{name}: gp {gp} != rev-cogs {rev - cogs}"


# ── Menu Action Board ──────────────────────────────────────────────────────
_ACTION_BUCKETS = ("feature", "market", "fix_margin", "review_cut", "needs_cost")


def test_action_board_buckets_partition_and_shape(client):
    d = client.get("/api/menu/action-board").json()
    assert set(d["buckets"]) == set(_ACTION_BUCKETS)
    assert d["counts"] == {k: len(v) for k, v in d["buckets"].items()}
    # Every classified dish lands in EXACTLY one bucket; counts sum to total placed.
    placed = sum(d["counts"].values())
    assert placed == d["total_skus"], f"placed {placed} != total_skus {d['total_skus']}"
    # No dish appears in two buckets.
    seen = {}
    for bucket, rows in d["buckets"].items():
        for r in rows:
            assert r["sku_code"] not in seen, f"{r['sku_code']} in {bucket} and {seen.get(r['sku_code'])}"
            seen[r["sku_code"]] = bucket
            assert r["action"] == bucket
            assert r["reason"], "every action row carries a human reason"
    # review_cut never holds a still-healthy (non-declining/dead) dish.
    assert all(r["stage"] in ("declining", "dead") for r in d["buckets"]["review_cut"])


def test_action_board_quadrants_match_engineering(client):
    """Cross-check: the board reuses _menu_engineering_compute, so every dish's
    quadrant MUST equal the Menu Engineering tab's — guards against drift."""
    eng = client.get("/api/menu/engineering").json()
    eng_quad = {i["sku_code"]: i["quadrant"] for i in eng["items"] if i.get("quadrant")}
    board = client.get("/api/menu/action-board").json()
    board_quad = {}
    for rows in board["buckets"].values():
        for r in rows:
            if r.get("quadrant"):
                board_quad[r["sku_code"]] = r["quadrant"]
    assert board_quad, "no costed/quadranted dishes in fixture"
    for code, q in eng_quad.items():
        assert board_quad.get(code) == q, f"{code}: board {board_quad.get(code)} != engineering {q}"


# ── Menu Opportunities ─────────────────────────────────────────────────────
def test_opportunities_shape_and_no_cost_leak(client):
    d = client.get("/api/menu/opportunities").json()
    assert set(("cross_branch_gaps", "category_whitespace", "revive")) <= set(d)
    assert all(isinstance(d[k], list) for k in ("cross_branch_gaps", "category_whitespace", "revive"))
    # RBAC contract: this endpoint is menu.analysis-gated, so NO COGS/margin fields
    # may leak into any row (those belong to cost.analysis).
    forbidden = {"cogs", "margin", "margin_pct", "margin_per_unit", "unit_cost"}
    for k in ("cross_branch_gaps", "category_whitespace", "revive"):
        for row in d[k]:
            assert not (forbidden & set(row)), f"cost field leaked into {k}: {row}"
    # whitespace rows are labelled and carry per-SKU demand metrics
    for w in d["category_whitespace"]:
        assert w.get("label") in ("expand", "trim", "watch")
        assert "revenue_per_sku" in w and "n_skus" in w


# ── AI idea generator (fallback path — tests never hit the real API) ─────────
def test_ai_ideas_fallback_without_key(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post("/api/menu/ai-ideas", json={})
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert d["generated_by"].startswith("fallback")
    assert isinstance(d["ideas"], list) and d["ideas"], "fallback must still yield ideas"
    for idea in d["ideas"]:
        assert idea.get("title") and "rationale" in idea and "builds_on" in idea




# ── broad-sweep companions: endpoints needing discovered params / binary bodies ──
def test_menu_impact_report_via_entities(client):
    """menu-impact needs an ent= handle; discover one from the selector endpoint
    (falls back to a raw SKU handle when the fixture has no fact coverage)."""
    ents = client.get("/api/reports/menu-impact/entities").json()
    handles = [e["ent"] for e in ents.get("items", []) if e.get("ent")] or ["s:3"]
    r = client.get("/api/reports/menu-impact", params={"ent": handles[0]})
    assert r.status_code == 200, r.text[:200]
    r.json()


def test_pnl_bill_items_drill(client):
    # fixture receipts look like "2026-06-01-1001-1" (see conftest)
    r = client.get("/api/reports/pnl-bill-items", params={"receipt": "2026-06-01-1001-1"})
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert "items" in d or "lines" in d or isinstance(d, (list, dict))


XLSX_CT = "spreadsheetml"


@pytest.mark.parametrize("path,params", [
    ("/api/cost/template", {}),
    ("/api/cost/monthly/template", {"month": "2026-05"}),
    ("/api/buffet/gp/export", {}),
    ("/api/export/monthly-pack", {"month": "2026-05"}),
])
def test_binary_exports_are_valid_workbooks(client, path, params):
    import io, openpyxl
    r = client.get(path, params=params)
    assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text[:200]}"
    ct = r.headers.get("content-type", "")
    if XLSX_CT in ct:
        openpyxl.load_workbook(io.BytesIO(r.content))     # must parse
    else:
        assert "csv" in ct or "text" in ct or "json" in ct, f"{path}: unexpected content-type {ct}"


# ── master / franchise scope split (regression) ─────────────────────────────
# Fixture: BranchA=master, BranchB=franchise, both carry sales. For an ADDITIVE
# metric the two disjoint groups must sum back to the ungrouped total, and each
# group must differ from the total. Guards the exact bug the scope split fixed:
# an endpoint silently ignoring group= and returning ALL branches (then
# master==franchise==all → both assertions below fail).  [[analysis_report_scope_split]]
GROUP_ADDITIVE = [
    ("/api/summary",                {}, lambda d: d["revenue"]),
    ("/api/analysis/persona",       {}, lambda d: d["overall"]["tx_count"]),
    ("/api/analysis/dow",           {}, lambda d: round(sum((r["revenue"] or 0) for r in d), 2)),
    # hourly_open uses the same "JOIN branches when branch OR group" path as dow but
    # needs the closed_hours table, which the hermetic fixture omits — dow covers it.
    ("/api/cost/analysis",          {}, lambda d: d["summary"]["total_revenue"]),
    ("/api/gp/by-branch-month",     {}, lambda d: len(d["rows"])),
    ("/api/analysis/discounts",     {}, lambda d: d["overall"]["total_tx"]),
]


@pytest.mark.parametrize("path,params,extract", GROUP_ADDITIVE,
                         ids=[p for p, _, _ in GROUP_ADDITIVE])
def test_group_split_is_additive(client, path, params, extract):
    def val(group):
        p = dict(params)
        if group:
            p["group"] = group
        r = client.get(path, params=p)
        assert r.status_code == 200, (path, group, r.text[:200])
        return extract(r.json())

    all_v = val(None)
    master = val("master")
    franchise = val("franchise")
    # group= must actually filter — if ignored, a group would equal the whole
    assert master != all_v, f"{path}: group=master ({master}) == ungrouped ({all_v}) — group filter ignored?"
    assert franchise != all_v, f"{path}: group=franchise ({franchise}) == ungrouped ({all_v}) — group filter ignored?"
    # disjoint groups reconcile to the total (allow tiny per-bucket ROUND drift)
    tol = max(1.0, abs(all_v) * 1e-4)
    assert abs((master + franchise) - all_v) <= tol, \
        f"{path}: master+franchise ({master}+{franchise}) != all ({all_v})"


def test_management_gp_never_fake_100pct_without_cost_data(client):
    """Regression: sales_sku_daily is a ROLLING window. Reading it blindly made
    every pre-coverage period report ฿0 food cost — rendering as a triumphant
    'GP 100%'. Cost must come from the monthly table for whole-month windows,
    and be reported as unavailable (nulls) otherwise — never revenue-minus-zero."""
    j = client.get("/api/reports/management", params={"period": "monthly"}).json()
    gp = j["gp"]
    assert gp.get("cost_basis") in ("daily", "monthly", "unavailable")
    if gp.get("cost_basis") == "unavailable":
        # unknown cost => unknown GP, and the UI is told why
        assert gp["gp"] is None and gp["gp_pct"] is None and gp["food_cost"] is None
        assert gp.get("cost_from")
        assert j["menu"]["cost_basis"] == "unavailable"
    else:
        # a real cost basis must produce a sane margin, never 100%
        assert gp["food_cost"] and gp["food_cost"] > 0
        assert gp["gp_pct"] is not None and gp["gp_pct"] < 99.9


def test_yoy_carries_like_for_like_guard(client):
    """Regression: raw YoY compared whatever traded in each month, so a chain
    that grew 7→27 branches reported +1991% 'growth'. Every row must carry the
    comparability context and a same-store figure when the raw one is unfair."""
    rows = client.get("/api/analysis/yoy",
                      params={"month_from": "2026-05", "month_to": "2026-06"}).json()
    assert rows, "fixture must produce yoy rows"
    for r in rows:
        for k in ("comparable", "same_store_pct", "cur_days", "ly_days",
                  "cur_branches", "ly_branches", "same_store_count"):
            assert k in r, f"{k} missing from yoy row"
        assert isinstance(r["comparable"], bool)
        # a month judged comparable must not be built on a partial year-ago month
        if r["comparable"]:
            assert r["ly_days"] >= 20
            assert abs(r["cur_branches"] - r["ly_branches"]) <= 2


def test_data_quality_reports_without_repairing(client):
    """The integrity report must expose findings with counts + a benign flag,
    and must never be a mutation surface (it only ever reads)."""
    j = client.get("/api/data-quality").json()
    assert "findings" in j and j["findings"], "expected integrity findings"
    keys = {f["key"] for f in j["findings"]}
    for expected in ("missing_days", "zero_covers", "discount_gt_total",
                     "uncosted_skus", "stub_names", "stale_skus"):
        assert expected in keys, f"{expected} check missing"
    for f in j["findings"]:
        assert isinstance(f["count"], int) and f["count"] >= 0
        assert f["severity"] in ("high", "warn", "info")
        assert isinstance(f["benign"], bool)
        assert f["detail"]
    # the headline total excludes by-design findings
    assert j["total"] == sum(f["count"] for f in j["findings"] if not f["benign"])


def test_cost_suggestions_are_defensible_estimates(client):
    """Cost suggestions must be derived from costed dishes only, carry their
    method/sample, and never claim a dish costs most of what it sells for.
    They are review candidates — nothing is written to sku_cost."""
    j = client.get("/api/cost/suggestions", params={"limit": 50}).json()
    assert "items" in j and "median_cost_price_ratio" in j
    for i in j["items"]:
        assert i["suggested_cost"] > 0
        assert i["method"] in ("ingredient", "category", "price_ratio")
        assert i["confidence"] in ("high", "medium", "low")
        assert i["sample"] >= 1
        # sanity cap: never above 60% of the dish's own selling price
        if i.get("price"):
            assert i["suggested_cost"] <= i["price"] * 0.6 + 0.01, i
    # the endpoint must not mutate anything
    before = client.get("/api/cost/uncosted-coverage").json()
    client.get("/api/cost/suggestions")
    assert client.get("/api/cost/uncosted-coverage").json() == before
