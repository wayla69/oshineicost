# -*- coding: utf-8 -*-
"""Instagram: comments under the account's posts, posts that tag it, @mentions
through the Meta webhook (signature-checked, stored, resolved by the nightly
run), replies, and the pages command filling the accounts. Fakes only."""
import hashlib
import hmac
import json
import sqlite3
import sys
import urllib.parse
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import fetch_feedback as FF  # noqa: E402
from feedback_sources import facebook as F  # noqa: E402
from feedback_sources import instagram as I  # noqa: E402
from test_feedback_sources import _args, _seed_db  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_feedback_rows(request):
    yield
    if "client" not in request.fixturenames:
        return
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    for t in ("customer_feedback", "feedback_sync_log", "feedback_webhook_events"):
        con.execute(f"CREATE TABLE IF NOT EXISTS {t}(id INTEGER PRIMARY KEY)")
        con.execute(f"DELETE FROM {t}")
    con.commit(); con.close()
    try:
        import main
        main.invalidate_cache()
    except Exception:
        pass


MEDIA = [{"id": "m1", "caption": "ทุเรียนมาแล้ว\nคืนนี้", "timestamp": "2026-09-01T10:00:00+0000",
          "permalink": "https://www.instagram.com/p/AAA/", "media_type": "IMAGE", "comments_count": 3},
         {"id": "m2", "caption": "โปรใหม่", "timestamp": "2026-08-01T10:00:00+0000",
          "permalink": "https://www.instagram.com/p/BBB/", "media_type": "VIDEO", "comments_count": 0}]
COMMENTS = [{"id": "c1", "text": "สาขาขอนแก่นคิวยาวมาก", "timestamp": "2026-09-02T10:00:00+0000", "username": "ploy_eats"},
            {"id": "c2", "text": "ขอบคุณค่ะ", "timestamp": "2026-09-02T11:00:00+0000", "username": "Oshinei.Official"},
            {"id": "c0", "text": "เก่า", "timestamp": "2026-07-01T00:00:00+0000", "username": "x"}]
TAGS = [{"id": "t1", "caption": "มื้อนี้ที่ @oshinei.official ระยอง ฟินมาก", "timestamp": "2026-09-03T10:00:00+0000",
         "permalink": "https://www.instagram.com/p/CCC/", "username": "bee", "media_type": "IMAGE"}]
ACC = {"ig_user_id": "17841", "username": "oshinei.official", "page_id": "PAGE1", "token": "PT", "branch": ""}


def ig_http(method, url, headers, body):
    if "/17841/media?" in url:
        return 200, {"data": MEDIA}
    if "/m1/comments?" in url:
        return 200, {"data": COMMENTS}
    if "/m2/comments?" in url:
        raise AssertionError("no comments call for a post with none")
    if "/17841/tags?" in url:
        return 200, {"data": TAGS}
    if "/17841?" in url:
        fields = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["fields"][0]
        if fields.startswith("mentioned_comment"):
            return 200, {"id": "17841", "mentioned_comment": {"id": "mc1", "text": "@oshinei.official อุดรธานี ของหมดเร็ว",
                                                              "timestamp": "2026-09-04T00:00:00+0000", "username": "nid",
                                                              "media": {"id": "z", "permalink": "https://www.instagram.com/p/ZZZ/"}}}
        if fields.startswith("mentioned_media"):
            return 200, {"id": "17841", "mentioned_media": {"id": "mm1", "caption": "ร้านโปรด @oshinei.official", "timestamp": "2026-09-04T01:00:00+0000",
                                                            "permalink": "https://www.instagram.com/p/YYY/", "username": "bee"}}
    if url.endswith("/c1/replies") and method == "POST":
        assert b"message=" in body and b"access_token=PT" in body
        return 200, {"id": "r1"}
    if "/me/accounts" in url:
        return 200, {"data": [{"id": "PAGE1", "name": "Oshinei", "access_token": "PT",
                               "instagram_business_account": {"id": "17841", "username": "oshinei.official"}},
                              {"id": "PAGE2", "name": "Oshinei ระยอง", "access_token": "PT2"}]}
    if url.endswith("/PAGE1/subscribed_apps") and method == "POST":
        return 200, {"success": True}
    return 400, {"error": {"type": "OAuthException", "message": "unexpected " + url}}


def test_instagram_comments_and_tags():
    rows, meta = I.fetch_rows({"accounts": [ACC]}, ig_http, since="2026-08-15")
    by = {r["external_id"]: r for r in rows}
    assert set(by) == {"comment:c1", "tagged:t1"}            # the account's own reply and the July comment skipped; m2 has none
    c = by["comment:c1"]
    assert c["kind"] == "comment" and c["ref"] == "17841/c1" and c["url"] == "https://www.instagram.com/p/AAA/"
    assert c["location"] == "ทุเรียนมาแล้ว คืนนี้" and c["detect_branch"] is True and c["author"] and c["author"] != "ploy_eats"
    t = by["tagged:t1"]
    assert t["kind"] == "mention" and t["ref"] == "" and t["location"] == "แท็กบัญชี" and t["date"] == "2026-09-03"
    assert meta["accounts"][0] == {"ig_user_id": "17841", "username": "oshinei.official", "branch": "", "media": 2, "comments": 1, "tagged": 1,
                                   "inbox_threads": 0, "inbox": 0}
    assert I.reply_to_comment("PT", "c1", "ขออภัยค่ะ", ig_http) == {"id": "r1"}
    with pytest.raises(RuntimeError, match="no accounts"):
        I.fetch_rows({}, ig_http)
    with pytest.raises(RuntimeError, match="OAuthException"):
        I.fetch_rows({"accounts": [{"ig_user_id": "999", "token": "bad"}]}, ig_http)
    rows2, meta2 = I.fetch_rows({"accounts": [dict(ACC, comments=False, tags=False)]}, ig_http)
    assert rows2 == [] and meta2["accounts"][0]["media"] == 0


def test_resolve_mentions_from_webhook_events():
    events = [{"id": 1, "object_id": "17841", "payload": json.dumps({"comment_id": "mc1"})},
              {"id": 2, "object_id": "17841", "payload": {"media_id": "mm1"}},
              {"id": 3, "object_id": "17841", "payload": {}},
              {"id": 4, "object_id": "0000", "payload": {"media_id": "q"}}]
    rows, done, failed = I.resolve_mentions({"accounts": [ACC]}, events, ig_http)
    assert [r["external_id"] for r in rows] == ["mentionc:mc1", "mention:mm1"]
    assert rows[0]["url"] == "https://www.instagram.com/p/ZZZ/" and rows[0]["kind"] == "mention" and rows[0]["text"].startswith("@oshinei.official อุดรธานี")
    assert rows[1]["location"] == "@mention ในโพสต์" and rows[1]["date"] == "2026-09-04"
    assert done == [1, 2, 3] and failed == [(4, "no account for this webhook object")]
    rows, done, failed = I.resolve_mentions({"accounts": [ACC]}, [{"id": 9, "object_id": "17841", "payload": {"comment_id": "bad"}}],
                                            lambda *a: (400, {"error": {"type": "GraphMethodException", "message": "gone"}}))
    assert rows == [] and done == [] and failed[0][0] == 9 and "GraphMethodException" in failed[0][1]


def _sig(secret: str, raw: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()


def test_meta_webhook_verify_store_and_ack(client, monkeypatch):
    payload = {"object": "instagram", "entry": [{"id": "17841", "time": 1, "changes": [
        {"field": "mentions", "value": {"comment_id": "mc1"}}, {"field": "mentions", "value": {"media_id": "mm1"}}]}]}
    raw = json.dumps(payload).encode("utf-8")
    monkeypatch.delenv("META_APP_SECRET", raising=False)
    assert client.post("/api/feedback/webhook/meta", content=raw, headers={"X-Hub-Signature-256": _sig("s", raw)}).status_code == 503
    monkeypatch.setenv("META_APP_SECRET", "s3cret")
    monkeypatch.setenv("META_WEBHOOK_VERIFY_TOKEN", "vt")
    ok = client.get("/api/feedback/webhook/meta", params={"hub.mode": "subscribe", "hub.challenge": "12345", "hub.verify_token": "vt"})
    assert ok.status_code == 200 and ok.text == "12345"
    assert client.get("/api/feedback/webhook/meta", params={"hub.mode": "subscribe", "hub.challenge": "1", "hub.verify_token": "no"}).status_code == 403
    assert client.post("/api/feedback/webhook/meta", content=raw, headers={"X-Hub-Signature-256": _sig("wrong", raw)}).status_code == 403
    assert client.post("/api/feedback/webhook/meta", content=raw).status_code == 403
    r = client.post("/api/feedback/webhook/meta", content=raw, headers={"X-Hub-Signature-256": _sig("s3cret", raw)})
    assert r.status_code == 200 and r.json() == {"stored": 2}
    ev = client.get("/api/feedback/webhook-events", params={"platform": "instagram"}).json()["events"]
    assert len(ev) == 2 and ev[0]["object_id"] == "17841" and ev[0]["field"] == "mentions" and ev[0]["payload"] == {"comment_id": "mc1"}
    assert client.post("/api/feedback/webhook-events/ack", json={"ids": [ev[0]["id"]], "result": "ok"}).json() == {"acked": 1}
    assert client.post("/api/feedback/webhook-events/ack", json={"ids": []}).status_code == 400
    assert len(client.get("/api/feedback/webhook-events", params={"platform": "instagram"}).json()["events"]) == 1
    assert len(client.get("/api/feedback/webhook-events", params={"platform": "instagram", "unprocessed": 0}).json()["events"]) == 2
    # the receiver stays reachable when the site enforces login; the reading side does not
    monkeypatch.setenv("OSHINEI_AUTH", "1"); monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret")
    assert client.get("/api/feedback/webhook/meta", params={"hub.mode": "subscribe", "hub.challenge": "777", "hub.verify_token": "vt"}).text == "777"
    assert client.get("/api/feedback/webhook-events").status_code == 401


def test_fetch_run_resolves_webhook_mentions_and_replies(tmp_path):
    db = tmp_path / "local.db"; _seed_db(db)
    sink = FF.DbSink(db)
    sink._con().close()                                        # creates the feedback tables
    con = sqlite3.connect(str(db)); con.row_factory = sqlite3.Row
    con.execute("INSERT INTO feedback_webhook_events(platform, object_id, field, payload) VALUES ('instagram','17841','mentions',?)",
                (json.dumps({"comment_id": "mc1"}),))
    con.commit()
    cfg, lines = {"instagram": {"accounts": [ACC]}}, []
    assert FF.run(_args(since="all"), cfg, sink, http=ig_http, log=lines.append) == 0
    ext = {r[0] for r in con.execute("SELECT external_id FROM customer_feedback")}
    assert ext == {"comment:c0", "comment:c1", "mentionc:mc1", "tagged:t1"}     # since=all → the July comment too
    assert con.execute("SELECT result FROM feedback_webhook_events").fetchone()[0] == "ok"
    assert any("webhook: 1 mention event(s)" in ln for ln in lines)
    row = con.execute("SELECT id, branch_name FROM customer_feedback WHERE external_id='comment:c1'").fetchone()
    assert row["branch_name"] == "ขอนแก่น"                                       # detected from the comment text
    con.execute("UPDATE customer_feedback SET reply_status='pending', reply_draft='ขออภัยค่ะ' WHERE id=?", (row["id"],)); con.commit()
    assert FF.send_replies(cfg, sink, ig_http, log=lambda *a: None) == {"sent": 1, "failed": 0}
    assert con.execute("SELECT reply_status FROM customer_feedback WHERE id=?", (row["id"],)).fetchone()[0] == "sent"
    # a second run finds no unprocessed events and nothing new
    assert FF.run(_args(since="all"), cfg, sink, http=ig_http, log=lines.append) == 0
    assert con.execute("SELECT COUNT(*) FROM customer_feedback").fetchone()[0] == 4
    con.close()


def test_pages_command_fills_instagram_accounts_and_subscribes():
    cfg = {"facebook": {"pages": [{"id": "PAGE1", "name": "old", "branch": "ขอนแก่น", "token": "stale", "comments": True}]}}
    FF.facebook_pages(cfg, ig_http, "UT", save=True, log=lambda *a: None)
    assert cfg["instagram"]["accounts"] == [{"ig_user_id": "17841", "username": "oshinei.official", "page_id": "PAGE1",
                                             "branch": "ขอนแก่น", "comments": True, "tags": True, "token": "PT"}]
    pages = {p["id"]: p for p in cfg["facebook"]["pages"]}
    assert pages["PAGE1"]["token"] == "PT" and pages["PAGE1"]["branch"] == "ขอนแก่น" and pages["PAGE2"]["name"] == "Oshinei ระยอง"
    assert F.list_pages("UT", ig_http)[1]["instagram"] is None
    lines = []
    assert FF.subscribe_webhooks(cfg, ig_http, log=lines.append) == 1                # PAGE2 has no handler → reported, not fatal
    assert any("✓ Oshinei: subscribed" in ln for ln in lines) and any("✗ Oshinei ระยอง" in ln for ln in lines)


def test_instagram_reply_channel_through_the_endpoints(client):
    client.post("/api/feedback/upload", json={"rows": [
        {"text": "คิวยาว", "source": "Instagram", "external_id": "comment:c1", "ref": "17841/c1", "date": "2026-09-03", "kind": "comment", "branch": "BranchA"},
        {"text": "แท็ก", "source": "Instagram", "external_id": "tagged:t1", "date": "2026-09-03", "kind": "mention"}]})
    items = {i["external_id"]: i for i in client.get("/api/feedback/items", params={"source": "Instagram"}).json()["items"]}
    d = client.get(f"/api/feedback/{items['comment:c1']['id']}/reply-draft").json()
    assert d["channel"] == "instagram" and d["max_len"] == 2200 and "Instagram" in d["note"]
    assert client.post(f"/api/feedback/{items['comment:c1']['id']}/reply", json={"text": "ขออภัยค่ะ"}).json()["reply_status"] == "pending"
    assert client.get("/api/feedback/reply-queue").json()["items"][0]["external_ref"] == "17841/c1"
    assert client.get(f"/api/feedback/{items['tagged:t1']['id']}/reply-draft").json()["channel"] == "manual"
