"""Web Push for the phone (routers/push_api.py): subscriptions, the morning payload,
the sender's bookkeeping, the once-a-day guard, and the served service worker.

The sender is injected (`send=`) so no push service is contacted; VAPID keys are
monkeypatched into the environment (any base64url strings do — the fake sender
never signs anything).
"""
import json


import main
import rules
from routers import push_api as P


SUB = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "BPdh", "auth": "AUTH"}, "ua": "pytest"}
SUB2 = {"endpoint": "https://push.example/def", "keys": {"p256dh": "BPdh2", "auth": "AUTH2"}}


def _keys(monkeypatch, on=True):
    if on:
        monkeypatch.setenv("OSHINEI_VAPID_PRIVATE", "cHJpdmF0ZQ")
        monkeypatch.setenv("OSHINEI_VAPID_PUBLIC", "cHVibGlj")
    else:
        monkeypatch.delenv("OSHINEI_VAPID_PRIVATE", raising=False)
        monkeypatch.delenv("OSHINEI_VAPID_PUBLIC", raising=False)


def _wipe(client):
    with main.get_db() as con:
        P.ensure(con)
        con.execute("DELETE FROM push_subscription")
        con.execute("DELETE FROM usage_meta WHERE key LIKE 'push_%'")
        con.commit()


def test_vapid_public_reflects_configuration(client, monkeypatch):
    _keys(monkeypatch, on=False)
    j = client.get("/api/push/vapid-public").json()
    assert j == {"configured": False, "key": None}
    _keys(monkeypatch, on=True)
    j = client.get("/api/push/vapid-public").json()
    assert j["configured"] is True and j["key"] == "cHVibGlj"


def test_subscribe_unsubscribe_round_trip(client, monkeypatch):
    _keys(monkeypatch); _wipe(client)
    r = client.post("/api/push/subscribe", json=SUB)
    assert r.status_code == 200 and r.json()["devices"] == 1
    # the same endpoint again is an update, not a second device
    assert client.post("/api/push/subscribe", json=SUB).json()["devices"] == 1
    st = client.get("/api/push/status").json()
    assert st["devices"] == 1 and st["configured"] is True and st["send_at"] == "08:30"
    assert "endpoint" not in json.dumps(st)
    r = client.post("/api/push/unsubscribe", json={"endpoint": SUB["endpoint"]})
    assert r.json()["removed"] == 1
    assert client.get("/api/push/status").json()["devices"] == 0


def test_subscribe_refuses_a_plain_http_endpoint(client, monkeypatch):
    _keys(monkeypatch)
    r = client.post("/api/push/subscribe", json={**SUB, "endpoint": "http://push.example/x"})
    assert r.status_code == 400


def test_sender_bookkeeping_sent_gone_failed(client, monkeypatch):
    _keys(monkeypatch); _wipe(client)
    client.post("/api/push/subscribe", json=SUB)
    client.post("/api/push/subscribe", json=SUB2)
    calls = []

    def fake_send(sub_info, data):
        calls.append((sub_info["endpoint"], json.loads(data)))
        return 410 if sub_info["endpoint"].endswith("/def") else 201

    with main.get_db() as con:
        out = P.send_to_all(con, {"title": "t", "body": "b", "url": "/"}, send=fake_send)
        con.commit()
        left = [r[0] for r in con.execute("SELECT endpoint FROM push_subscription ORDER BY endpoint")]
    assert {k: out[k] for k in ("targets", "sent", "gone", "failed")} == {"targets": 2, "sent": 1, "gone": 1, "failed": 0}
    assert sorted(out["statuses"]) == [201, 410]      # the push service's answers travel back to the card
    assert left == [SUB["endpoint"]]                       # 410 → the dead device is dropped
    assert calls and calls[0][1]["title"] == "t"
    # repeated failures retire a device after MAX_FAILS attempts
    with main.get_db() as con:
        for _ in range(P.MAX_FAILS):
            P.send_to_all(con, {"title": "t"}, send=lambda s, d: 500)
        con.commit()
        assert con.execute("SELECT COUNT(*) FROM push_subscription").fetchone()[0] == 0


def test_test_endpoint_targets_only_the_caller(client, monkeypatch):
    _keys(monkeypatch); _wipe(client)
    sent = []
    monkeypatch.setattr(P, "_default_send", lambda s, d: (sent.append(json.loads(d)), 201)[1])
    assert client.post("/api/push/test").status_code == 404      # no device yet
    client.post("/api/push/subscribe", json=SUB)
    r = client.post("/api/push/test")
    assert r.status_code == 200 and r.json()["sent"] == 1
    assert "ทดสอบ" in sent[0]["title"]
    _keys(monkeypatch, on=False)
    assert client.post("/api/push/test").status_code == 503     # keys missing → says so


def test_morning_payload_reads_like_the_strip():
    brief = {"yesterday": "2026-10-06", "revenue_yesterday": 2047485.64, "revenue_wow": 2071106.01,
             "customer_yesterday": 2989, "skipped_day": "2026-10-07", "skipped_bills": 7}
    sc = {"branches": [{"branch": "อยุธยา", "vs_avg_pct": -58.3, "status": "down"},
                       {"branch": "นครสวรรค์", "vs_avg_pct": -57.3, "status": "down"},
                       {"branch": "ชลบุรี", "vs_avg_pct": 3.0, "status": "ok"},
                       {"branch": "ระยอง", "vs_avg_pct": -9.0, "status": "ok"}]}
    p = P.morning_payload(brief, sc)
    assert p["title"] == "เมื่อวาน (อ. 6 ต.ค.) 2.05M ▼1.1%"
    assert p["body"] == "ลูกค้า 2,989 · 3 สาขาต่ำกว่าปกติ: อยุธยา -58%, นครสวรรค์ -57% · ข้อมูล พ. 7 ต.ค. ยังมาไม่ครบ"
    assert p["url"] == "/" and p["tag"] == "oshinei-morning"
    assert P.morning_payload({"yesterday": None}, sc) is None
    quiet = P.morning_payload({"yesterday": "2026-10-06", "revenue_yesterday": 1000, "customer_yesterday": 3}, {"branches": []})
    assert quiet["body"] == "ลูกค้า 3 · ทุกสาขาอยู่ในเกณฑ์ปกติ" and quiet["title"] == "เมื่อวาน (อ. 6 ต.ค.) 1.0K"


def test_build_morning_uses_the_live_brief(client):
    p = P.build_morning()
    brief = client.get("/api/home/brief").json()
    assert (p is None) == (not brief.get("yesterday"))
    if p:
        assert p["title"].startswith("เมื่อวาน (") and "ลูกค้า" in p["body"]


def test_morning_sends_once_a_day_after_0830(client, monkeypatch):
    _keys(monkeypatch); _wipe(client)
    client.post("/api/push/subscribe", json=SUB)
    import datetime as dt
    sent = []
    fake = lambda s, d: (sent.append(d), 201)[1]
    early = dt.datetime(2026, 10, 8, 8, 29)
    assert P.maybe_send_morning(now=early, send=fake) is None          # not yet
    rec = P.maybe_send_morning(now=dt.datetime(2026, 10, 8, 8, 30), send=fake)
    assert rec and rec["day"] == "2026-10-08" and rec["configured"] is True
    assert rec["targets"] == 1 and rec["sent"] == (1 if rec["payload"] else 0)
    assert P.maybe_send_morning(now=dt.datetime(2026, 10, 8, 9, 0), send=fake) is None   # done for today
    nxt = P.maybe_send_morning(now=dt.datetime(2026, 10, 9, 8, 45), send=fake)
    assert nxt and nxt["day"] == "2026-10-09"
    st = client.get("/api/push/status").json()
    assert st["last"]["day"] == "2026-10-09"


def test_subscriptions_survive_the_db_swap():
    assert "push_subscription" in rules.__dict__.get("_PRESERVED_EXACT", ()) or True   # rules has no list; core does
    import core
    assert "push_subscription" in core._PRESERVED_EXACT


def test_service_worker_is_served_with_the_build_id(client):
    r = client.get("/sw.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert "__BUILD__" not in r.text and "oshinei-shell-" in r.text
    assert r.headers["cache-control"] == "no-cache"
    assert "/api/push/subscribe" in r.text and "notificationclick" in r.text


def test_dockerfile_ships_the_worker():
    """2026-10-08: the first deploy of the worker 404'd on Fly — the image copies
    paths one by one (Dockerfile) and sw.js at the repo root was not among them."""
    from pathlib import Path
    docker = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY sw.js" in docker
