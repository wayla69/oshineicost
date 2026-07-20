"""Usage telemetry (lean-overhaul Phase 0): the switchTab beacon, the summary
endpoint, and the api-hit middleware. Telemetry lives in usage.db BESIDE the
main DB so data-refresh / sync swaps never wipe it — here that lands in the
test fixture's temp dir (main.DB_PATH is rebound by conftest)."""


def test_tab_beacon_roundtrip(client):
    r = client.post("/api/usage/tab", json={"tab": "home"})
    assert r.status_code == 200 and r.json()["ok"] is True
    s = client.get("/api/usage/summary").json()
    hits = {t["name"]: t["hits"] for t in s["tabs"]}
    assert hits.get("home", 0) >= 1


def test_beacon_without_tab_is_noop(client):
    r = client.post("/api/usage/tab", json={})
    assert r.status_code == 200 and r.json()["ok"] is False


def test_api_hits_counted_but_not_usage_itself(client):
    client.get("/api/months")
    s = client.get("/api/usage/summary").json()
    names = [a["name"] for a in s["api"]]
    assert any("/api/months" in n for n in names)
    # /api/usage/* must never count itself — it would drown the real signal
    assert not any("/api/usage" in n for n in names)
