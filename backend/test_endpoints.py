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
]

# Fixture periods: June (LATEST) sales are scaled 0.8 vs May (PREV).
A_PER, B_PER = "2026-05", "2026-06"


@pytest.mark.parametrize("path,params", SMOKE)
def test_endpoint_200(client, path, params):
    r = client.get(path, params=params)
    assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text[:200]}"
    r.json()  # must be valid JSON


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
