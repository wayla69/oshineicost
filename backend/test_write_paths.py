"""Mutation round-trips: every write endpoint actually persists (write → read
back → assert), and the validation guards reject bad input with 4xx, not 500.

test_write_auth.py covers WHO may write (the token gate); this file covers WHAT
the writes do. Runs last alphabetically, so the read-based suites see the
pristine fixture; every test restores the fixture values it touches.
"""
import io

import openpyxl
import pytest


# ── /api/cost + /api/cost/bulk-update ───────────────────────────────────────
def _unit_cost(client, sku_code):
    rows = client.get("/api/cost").json()
    return {r["sku_code"]: r["unit_cost"] for r in rows}.get(sku_code)


def test_cost_upsert_roundtrip(client):
    assert _unit_cost(client, "B0001") == 8.0          # fixture baseline
    try:
        r = client.post("/api/cost", json=[{"sku_code": "B0001", "unit_cost": 8.5}])
        assert r.status_code == 200, r.text[:200]
        assert _unit_cost(client, "B0001") == 8.5
    finally:
        client.post("/api/cost", json=[{"sku_code": "B0001", "unit_cost": 8.0}])
    assert _unit_cost(client, "B0001") == 8.0


def test_cost_rejects_price_as_cost(client):
    """The price-as-cost guard: a 'cost' that is the inc-VAT selling price is a
    data-entry error (price column pasted as cost) and must not be persisted.
    A0001 sells at 100/unit ex-VAT → 107 (ratio 1.07) lands in the 1.03–1.12 band."""
    before = _unit_cost(client, "A0001")
    d = client.post("/api/cost", json=[{"sku_code": "A0001", "unit_cost": 107.0}]).json()
    assert d["updated"] == 0
    assert d["skipped_price_as_cost"] == 1
    assert d["skipped_samples"][0]["sku_code"] == "A0001"
    assert _unit_cost(client, "A0001") == before


def test_cost_bulk_update_roundtrip_and_unknown_sku(client):
    try:
        r = client.post("/api/cost/bulk-update", json=[
            {"sku_code": "B0001", "unit_cost": 8.25},
            {"sku_code": "ZZZ_NO_SUCH", "unit_cost": 1.0},
        ])
        assert r.status_code == 200, r.text[:200]
        d = r.json()
        assert _unit_cost(client, "B0001") == 8.25
        # the unknown SKU must be reported, not silently dropped
        blob = str(d).lower()
        assert "zzz_no_such" in blob or d.get("errors") or d.get("unknown"), d
    finally:
        client.post("/api/cost", json=[{"sku_code": "B0001", "unit_cost": 8.0}])


# ── /api/cost/monthly (per-month overrides) ─────────────────────────────────
def test_cost_monthly_roundtrip_and_validation(client):
    body = [{"sku_code": "B0001", "period_month": "2026-06", "unit_cost": 7.5, "notes": "pytest"}]
    try:
        d = client.post("/api/cost/monthly", json=body).json()
        assert d["written"] == 1 and not d["unknown_skus"] and not d["invalid_months"]
        rows = client.get("/api/cost/monthly", params={"sku": "B0001"}).json()
        rows = rows if isinstance(rows, list) else rows.get("rows", rows.get("items", []))
        mine = [r for r in rows if r["period_month"] == "2026-06"]
        assert mine and mine[0]["unit_cost"] == 7.5
        # bad month + unknown sku are reported, not written
        bad = client.post("/api/cost/monthly", json=[
            {"sku_code": "B0001", "period_month": "2026-13", "unit_cost": 1},
            {"sku_code": "NOPE", "period_month": "2026-06", "unit_cost": 1},
        ]).json()
        assert bad["written"] == 0
        assert bad["invalid_months"] and bad["unknown_skus"]
    finally:
        client.delete("/api/cost/monthly", params={"sku": "B0001", "month": "2026-06"})
    rows = client.get("/api/cost/monthly", params={"sku": "B0001"}).json()
    rows = rows if isinstance(rows, list) else rows.get("rows", rows.get("items", []))
    assert not [r for r in rows if r["period_month"] == "2026-06"]


# ── /api/settings ────────────────────────────────────────────────────────────
def test_settings_roundtrip(client):
    r = client.put("/api/settings", json={"_pytest_key": "hello"})
    assert r.status_code == 200, r.text[:200]
    assert client.get("/api/settings").json().get("_pytest_key") == "hello"
    client.put("/api/settings", json={"_pytest_key": "v2"})    # upsert overwrites
    assert client.get("/api/settings").json().get("_pytest_key") == "v2"


# ── /api/targets ─────────────────────────────────────────────────────────────
def test_targets_roundtrip_and_suggest(client):
    r = client.put("/api/targets", json=[
        {"branch": "BranchA", "period_month": "2026-06",
         "revenue_target": 99000, "customer_target": 500},
    ])
    assert r.status_code == 200, r.text[:200]
    got = client.get("/api/targets", params={"month": "2026-06"}).json()
    rows = got if isinstance(got, list) else got.get("rows", got.get("branches", []))
    mine = {r["branch"]: r for r in rows}
    assert mine["BranchA"]["revenue_target"] == 99000
    assert mine["BranchA"]["customer_target"] == 500
    # an unknown branch is not silently created
    r2 = client.put("/api/targets", json=[
        {"branch": "NoSuchBranch", "period_month": "2026-06", "revenue_target": 1}])
    assert r2.status_code in (200, 400)
    names = {b["name"] for b in client.get("/api/branches").json()}
    assert "NoSuchBranch" not in names


# ── /api/closed_hours ────────────────────────────────────────────────────────
def test_closed_hours_roundtrip_and_validation(client):
    r = client.put("/api/closed_hours", json=[
        {"branch_id": 1, "dow": 1, "hour_from": 0, "hour_to": 9, "is_closed": True}])
    assert r.status_code == 200, r.text[:200]
    rows = client.get("/api/closed_hours").json()
    rows = rows if isinstance(rows, list) else rows.get("rows", rows.get("entries", []))
    assert any(e["branch_id"] == 1 and e["dow"] == 1 for e in rows)
    # out-of-range dow must 400 (fix D12), never a silent write or a 500
    bad = client.put("/api/closed_hours", json=[
        {"branch_id": 1, "dow": 7, "hour_from": 0, "hour_to": 9}])
    assert bad.status_code == 400


# ── branches: rename / active / type ────────────────────────────────────────
def _branch(client, bid):
    rows = client.get("/api/branches", params={"include_inactive": True}).json()
    return {b["id"]: b for b in rows}.get(bid)


def test_branch_rename_clash_and_roundtrip(client):
    # renaming BranchB onto BranchA's name must be rejected (409/400), not merged
    clash = client.put("/api/branches/2", json={"name": "BranchA"})
    assert clash.status_code in (400, 409), clash.text[:200]
    try:
        r = client.put("/api/branches/2", json={"name": "BranchB_rn"})
        assert r.status_code == 200, r.text[:200]
        assert _branch(client, 2)["name"] == "BranchB_rn"
    finally:
        client.put("/api/branches/2", json={"name": "BranchB"})
    assert _branch(client, 2)["name"] == "BranchB"


def test_branch_active_toggle_roundtrip(client):
    try:
        assert client.put("/api/branches/2/active", json={"active": False}).status_code == 200
        b = _branch(client, 2)
        assert b is None or not b.get("active", 1), "deactivated branch still active"
    finally:
        client.put("/api/branches/2/active", json={"active": True})


def test_branch_type_roundtrip_and_validation(client):
    assert client.put("/api/branches/2/type", json={"branch_type": "junk"}).status_code == 400
    try:
        assert client.put("/api/branches/2/type", json={"branch_type": "master"}).status_code == 200
        assert _branch(client, 2)["branch_type"] == "master"
    finally:
        client.put("/api/branches/2/type", json={"branch_type": "franchise"})
    assert _branch(client, 2)["branch_type"] == "franchise"


# ── SKUs: active / business-type ─────────────────────────────────────────────
def _sku(client, code):
    return {s["sku_code"]: s for s in
            client.get("/api/skus", params={"include_inactive": True}).json()}.get(code)


def test_sku_active_toggle_roundtrip(client):
    try:
        assert client.put("/api/skus/A0001/active", json={"active": False}).status_code == 200
        assert not _sku(client, "A0001")["active"]
    finally:
        client.put("/api/skus/A0001/active", json={"active": True})
    assert _sku(client, "A0001")["active"]


def test_sku_business_type_roundtrip_and_validation(client):
    bad = client.put("/api/skus/A0001/business-type", json={"business_type": "junk"})
    assert bad.status_code == 400

    def counts():
        return client.get("/api/skus/business-types").json()["counts"]

    base = counts()
    try:
        r = client.put("/api/skus/A0001/business-type", json={"business_type": "beverage"})
        assert r.status_code == 200, r.text[:200]
        after = counts()
        assert after.get("beverage", 0) == base.get("beverage", 0) + 1
        assert after.get("alacarte", 0) == base.get("alacarte", 0) - 1
    finally:
        client.put("/api/skus/A0001/business-type", json={"business_type": "alacarte"})
    assert counts() == base


# ── BoM aliases ──────────────────────────────────────────────────────────────
def test_bom_alias_upsert_roundtrip(client):
    r = client.put("/api/bom/aliases/PYTEST01", json={"resolved_sku_code": "A0001",
                                                      "notes": "from pytest"})
    assert r.status_code == 200, r.text[:200]
    rows = client.get("/api/bom/aliases").json()
    rows = rows if isinstance(rows, list) else rows.get("rows", rows.get("aliases", []))
    mine = [a for a in rows if a["bom_sku_code"] == "PYTEST01"]
    assert mine and mine[0]["resolved_sku_code"] == "A0001"
    assert mine[0]["match_method"] == "manual"
    # unknown resolved SKU must be rejected
    bad = client.put("/api/bom/aliases/PYTEST02", json={"resolved_sku_code": "NOPE"})
    assert bad.status_code in (400, 404)


# ── per-branch fixed / labor costs ───────────────────────────────────────────
@pytest.mark.parametrize("path", ["/api/branches/fixed-costs", "/api/branches/labor-costs"])
def test_branch_cost_settings_roundtrip(client, path):
    r = client.post(path, json={"costs": {"BranchA": 50000, "BranchB": 40000}})
    assert r.status_code == 200, r.text[:200]
    assert r.json()["saved"] == 2
    got = client.get(path).json()
    costs = got.get("costs", got)
    assert costs["BranchA"] == 50000 and costs["BranchB"] == 40000
    # unknown branch keys are rejected (guards against typo'd branch names)
    bad = client.post(path, json={"costs": {"Nope": 1}})
    assert bad.status_code in (400, 422)


# ── /api/upload (multipart ingest of cleansed-raw Easy exports) ──────────────
def _es1_workbook(rows):
    """Cleansed flat raw ES1 layout: header + [_, branch, code, desc, qty,
    net_ex, tax1, tax2, tax3, gross_inc] (see reingest_raw_2026.parse_es1)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["No", "Branch", "Code", "Description", "Qty",
               "Net", "Tax1", "Tax2", "Tax3", "Gross"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def test_upload_es1_roundtrip(client):
    """Full pipeline: multipart upload → classify → parse → period-scoped load.
    Uses 2026-03 (a month the fixture doesn't seed) so nothing else is touched."""
    buf = _es1_workbook([
        [1, "BranchA", "T9001", "(T) Pytest Dish", 10, 1000, 70, 0, 0, 1070],
        [2, "BranchA", "T9002", "(T) Pytest Dish 2", 5, 500, 35, 0, 0, 535],
    ])
    r = client.post("/api/upload",
                    files={"file": ("ES1_Mar 26_raw.xlsx", buf,
                                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d["period"] == "2026-03"
    assert d["file_type"] == "monthly_sku"
    assert d["rows_ingested"] == 2
    # the ingested month is now visible to the API
    assert "2026-03" in client.get("/api/months").json()
    sold = client.get("/api/sales/skus", params={"month": "2026-03"}).json()
    codes = {s["sku_code"] for s in (sold if isinstance(sold, list) else sold.get("items", []))}
    assert {"T9001", "T9002"} <= codes


def test_upload_rejects_unknown_format_and_new_branch(client):
    # not a cleansed-raw export → clean 400, not silent garbage
    buf = _es1_workbook([[1, "BranchA", "X1", "d", 1, 1, 0, 0, 0, 1]])
    r = client.post("/api/upload", files={"file": ("random.xlsx", buf, "application/octet-stream")})
    assert r.status_code == 400, r.text[:200]
    # unknown branch names must be refused (the duplicate-branch trap)
    buf = _es1_workbook([[1, "BrandNewBranch", "X1", "d", 1, 1, 0, 0, 0, 1]])
    r = client.post("/api/upload", files={"file": ("ES1_Mar 26_raw.xlsx", buf, "application/octet-stream")})
    assert r.status_code == 400, r.text[:200]
    assert "BrandNewBranch" in r.json()["detail"]


def test_upload_token_gate(client, monkeypatch):
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "tok")
    buf = _es1_workbook([[1, "BranchA", "X1", "d", 1, 1, 0, 0, 0, 1]])
    r = client.post("/api/upload", files={"file": ("ES1_Mar 26_raw.xlsx", buf, "application/octet-stream")})
    assert r.status_code == 403
