"""Auth gate on data-mutating endpoints (require_write_token).

Invariant: when OSHINEI_SEED_TOKEN is set (hosted deploys), every write needs a
X-Upload-Token header (never ?token= - the access log prints the request line); when
unset (local dev), writes are open. Read-only endpoints — including the compute-only POST /api/bom-cost/simulate
— are never gated.
"""
import pytest

TOKEN = "s3cret-write-token"
# A representative write body (price-as-cost guard may skip the row, but the
# point here is the auth decision, not the upsert result).
COST_BODY = [{"sku_code": "A0001", "unit_cost": 9.0}]


def test_write_open_when_token_unset(client, monkeypatch):
    monkeypatch.delenv("OSHINEI_SEED_TOKEN", raising=False)
    r = client.post("/api/cost", json=COST_BODY)
    assert r.status_code != 403, r.text


def test_write_blocked_without_token(client, monkeypatch):
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    r = client.post("/api/cost", json=COST_BODY)
    assert r.status_code == 403, r.text


def test_write_allowed_with_header_token(client, monkeypatch):
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    r = client.post("/api/cost", json=COST_BODY, headers={"X-Upload-Token": TOKEN})
    assert r.status_code != 403, r.text


def test_a_query_string_token_no_longer_counts(client, monkeypatch):
    """Until 2026-09-22 ?token= was accepted as well - and so appeared in Fly's request
    log on every call. A correct token in the URL is now the same as no token."""
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    r = client.post(f"/api/cost?token={TOKEN}", json=COST_BODY)
    assert r.status_code == 403, r.text


def test_write_rejected_with_wrong_token(client, monkeypatch):
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    r = client.post("/api/cost", json=COST_BODY, headers={"X-Upload-Token": "nope"})
    assert r.status_code == 403, r.text


def test_reads_unaffected_by_gate(client, monkeypatch):
    """GET endpoints must never be gated, even with the token set."""
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    r = client.get("/api/branches")
    assert r.status_code == 200, r.text


def test_simulate_is_not_gated(client, monkeypatch):
    """Read-only compute POST must work with no token, even when the gate is on."""
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    body = {"month_from": "2026-06", "month_to": "2026-06", "overrides": []}
    r = client.post("/api/bom-cost/simulate", json=body)
    assert r.status_code != 403, r.text
