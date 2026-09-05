# -*- coding: utf-8 -*-
"""The mention connectors (X, YouTube, RSS / Google News / Google Alerts),
TikTok comments + replies, the kind column (review / comment / mention) and
server-side branch detection. Every network call is an injected fake."""
import datetime
import sqlite3
import sys
import urllib.parse
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import fetch_feedback as FF  # noqa: E402
from feedback_sources import rss as R  # noqa: E402
from feedback_sources import tiktok as T  # noqa: E402
from feedback_sources import x as X  # noqa: E402
from feedback_sources import youtube as Y  # noqa: E402
from test_feedback_sources import _args, _seed_db  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_feedback_rows(request):
    yield
    if "client" not in request.fixturenames:
        return
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    for t in ("customer_feedback", "feedback_sync_log"):
        con.execute(f"CREATE TABLE IF NOT EXISTS {t}(id INTEGER PRIMARY KEY)")
        con.execute(f"DELETE FROM {t}")
    con.commit(); con.close()
    try:
        import main
        main.invalidate_cache()
    except Exception:
        pass


# ── RSS / Atom ───────────────────────────────────────────────────────────────
ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Google Alerts - โอชิเน</title>
<entry><id>tag:google.com,2013:googlealerts/feed:123</id>
<title type="html">รีวิว &lt;b&gt;โอชิเน&lt;/b&gt; ขอนแก่น บุฟเฟ่ต์คุ้มมาก</title>
<link href="https://www.google.com/url?rct=j&amp;sa=t&amp;url=https://blog.example.com/oshinei-khonkaen&amp;ct=ga"/>
<published>2026-09-04T03:00:00Z</published>
<content type="html">ไปกิน &lt;b&gt;โอชิเน&lt;/b&gt; สาขาขอนแก่น   แซลมอนสด</content></entry>
<entry><id>x2</id><title>ข่าวเก่า</title><link href="https://news.example.com/a"/><updated>2026-08-01T00:00:00Z</updated><summary>เก่า</summary></entry>
</feed>"""
RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Google News</title>
<item><title>โอชิเน เปิดสาขาใหม่ ระยอง - ข่าวสด</title><link>https://news.google.com/rss/articles/abc</link><guid>abc</guid>
<pubDate>Fri, 04 Sep 2026 10:00:00 GMT</pubDate><description>&lt;a href="x"&gt;โอชิเน&lt;/a&gt; บุฟเฟ่ต์ญี่ปุ่นเปิดสาขาระยอง</description></item>
</channel></rss>"""


def test_rss_parses_atom_and_rss_and_google_alert_links():
    items = R.parse_feed(ATOM)
    assert len(items) == 2 and items[0]["title"] == "รีวิว โอชิเน ขอนแก่น บุฟเฟ่ต์คุ้มมาก"
    assert items[0]["summary"] == "ไปกิน โอชิเน สาขาขอนแก่น แซลมอนสด"
    row = R.item_to_row(items[0], "Web", "Google Alerts")
    assert row["url"] == "https://blog.example.com/oshinei-khonkaen" and row["date"] == "2026-09-04"
    assert row["kind"] == "mention" and row["detect_branch"] is True and row["location"] == "Google Alerts"
    assert row["external_id"].startswith("item:") and row["text"].startswith("รีวิว โอชิเน ขอนแก่น บุฟเฟ่ต์คุ้มมาก — ไปกิน")
    items = R.parse_feed(RSS)
    assert items[0]["date"].startswith("Fri") and R.item_to_row(items[0], "Google News")["date"] == "2026-09-04"
    assert R.parse_feed("not xml") == [] and R.parse_feed("") == [] and R.parse_feed(b"") == []
    feeds = R.feeds_for({"keywords": ["โอชิเน"], "feeds": [{"url": "https://www.google.com/alerts/feeds/1", "name": "Alert"}, "https://ex.com/rss"]})
    assert feeds[0]["source"] == "Google News" and "news.google.com/rss/search?q=" in feeds[0]["url"]
    assert feeds[1]["name"] == "Alert" and feeds[2]["source"] == "Web" and feeds[2]["url"] == "https://ex.com/rss"


def test_rss_fetch_rows_dedupes_and_honours_since():
    def http(method, url, headers, body):
        return 200, (ATOM if "alerts" in url else RSS)
    cfg = {"keywords": ["โอชิเน"], "feeds": [{"url": "https://www.google.com/alerts/feeds/1", "name": "Alert"}]}
    rows, meta = R.fetch_rows(cfg, http)
    assert meta["fetched"] == 3 and [f["items"] for f in meta["feeds"]] == [1, 2]
    rows, meta = R.fetch_rows(cfg, http, since="2026-09-01")
    assert meta["fetched"] == 2                                   # the August alert entry is skipped
    with pytest.raises(RuntimeError, match="HTTP 503"):
        R.fetch_rows(cfg, lambda *a: (503, "down"))
    with pytest.raises(RuntimeError, match="neither"):
        R.fetch_rows({"google_news": False}, http)


# ── X ────────────────────────────────────────────────────────────────────────
def test_x_search_pages_and_maps_posts(monkeypatch):
    monkeypatch.setattr(X, "_today", lambda: datetime.date(2026, 9, 5))
    calls = []

    def http(method, url, headers, body):
        calls.append(url)
        assert headers["Authorization"] == "Bearer B"
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if q.get("next_token"):
            return 200, {"data": [{"id": "2", "text": "โอชิเนอุดรธานี ของหมดเร็ว", "author_id": "u2",
                                   "created_at": "2026-09-03T12:00:00.000Z"}], "meta": {"result_count": 1}}
        return 200, {"data": [{"id": "1", "text": "โอชิเน ขอนแก่น อร่อยมาก", "author_id": "u1",
                               "created_at": "2026-09-04T12:00:00.000Z", "public_metrics": {"like_count": 3}}],
                     "includes": {"users": [{"id": "u1", "username": "somchai"}]}, "meta": {"next_token": "n1"}}
    rows, meta = X.fetch_rows({"bearer_token": "B", "keywords": ["โอชิเน", "oshinei"]}, http, since="2026-08-01")
    assert [r["external_id"] for r in rows] == ["tweet:1", "tweet:2"]
    assert rows[0]["url"] == "https://x.com/somchai/status/1" and rows[1]["url"] == "https://x.com/i/web/status/2"
    assert rows[0]["kind"] == "mention" and rows[0]["author"] and rows[0]["author"] != "u1" and rows[0]["date"] == "2026-09-04"
    assert meta["fetched"] == 2 and meta["queries"][0]["pages"] == 2
    q = urllib.parse.parse_qs(urllib.parse.urlparse(calls[0]).query)
    assert q["query"][0] == "(โอชิเน OR oshinei) -is:retweet"
    assert q["start_time"][0] == "2026-08-30T00:00:00Z"            # clamped to the 7-day window of recent search
    assert X.build_query(["oshinei buffet", "โอชิเน"], "th") == '("oshinei buffet" OR โอชิเน) -is:retweet lang:th'
    with pytest.raises(RuntimeError, match="Basic tier"):
        X.fetch_rows({"bearer_token": "B"}, lambda *a: (403, {"title": "Forbidden"}))
    with pytest.raises(RuntimeError, match="bearer_token"):
        X.fetch_rows({}, http)


# ── YouTube ──────────────────────────────────────────────────────────────────
def test_youtube_videos_and_comments():
    def http(method, url, headers, body):
        if "/search?" in url:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            assert q["key"][0] == "K" and q["publishedAfter"][0] == "2026-08-01T00:00:00Z"
            return 200, {"items": [
                {"id": {"videoId": "v1"}, "snippet": {"publishedAt": "2026-09-02T10:00:00Z", "title": "รีวิวโอชิเน ระยอง",
                                                      "description": "บุฟเฟ่ต์ญี่ปุ่น  คุ้มไหม", "channelId": "c1", "channelTitle": "กินไปเรื่อย"}},
                {"id": {"videoId": "v1"}, "snippet": {}},                 # duplicate
                {"id": {"channelId": "zzz"}}]}                             # not a video
        if "/commentThreads?" in url:
            if "videoId=v1" in url:
                return 200, {"items": [{"snippet": {"topLevelComment": {"id": "c9", "snippet": {
                    "textOriginal": "สาขาอุดรธานีรอนานมาก", "publishedAt": "2026-09-03T00:00:00Z", "authorChannelId": {"value": "a1"}}}}}]}
            return 403, {"error": {"errors": [{"reason": "commentsDisabled"}], "message": "disabled"}}
        return 404, {}
    rows, meta = Y.fetch_rows({"api_key": "K", "keywords": ["โอชิเน"], "comments": True}, http, since="2026-08-01")
    assert [r["external_id"] for r in rows] == ["video:v1", "comment:c9"]
    assert rows[0]["text"] == "รีวิวโอชิเน ระยอง\nบุฟเฟ่ต์ญี่ปุ่น คุ้มไหม" and rows[0]["location"] == "กินไปเรื่อย"
    assert rows[0]["kind"] == "mention" and rows[0]["url"] == "https://www.youtube.com/watch?v=v1" and rows[0]["date"] == "2026-09-02"
    assert rows[1]["kind"] == "comment" and rows[1]["url"] == "https://www.youtube.com/watch?v=v1&lc=c9"
    assert rows[1]["author"] and rows[1]["author"] != "a1"
    assert meta["queries"] == [{"keyword": "โอชิเน", "videos": 1, "comments": 1, "pages": 1}] and meta["fetched"] == 2
    assert Y.list_comments("K", "v2", http) == []                  # comments disabled → none, not an error
    with pytest.raises(RuntimeError, match="quotaExceeded"):
        Y.fetch_rows({"api_key": "K"}, lambda *a: (403, {"error": {"errors": [{"reason": "quotaExceeded"}], "message": "quota"}}))
    with pytest.raises(RuntimeError, match="api_key"):
        Y.fetch_rows({}, http)


# ── TikTok ───────────────────────────────────────────────────────────────────
class FakeTikTok:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, body))
        if url.endswith(T.REFRESH_PATH):
            return 200, {"code": 0, "data": {"access_token": "AT2", "refresh_token": "RT2", "open_id": "biz1", "expires_in": 86400}}
        if url.endswith(T.TOKEN_PATH):
            assert body["auth_code"] == "CODE"
            return 200, {"code": 0, "data": {"access_token": "AT1", "refresh_token": "RT1", "open_id": "biz1"}}
        assert headers.get("Access-Token") == "AT2"
        if T.VIDEO_LIST_PATH in url:
            return 200, {"code": 0, "data": {"videos": [{"item_id": "vid1", "caption": "แซลมอนไม่อั้น",
                                                         "share_url": "https://www.tiktok.com/@oshinei/video/vid1",
                                                         "create_time": 1787500000}], "has_more": False}}
        if T.COMMENT_LIST_PATH in url:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            if q["cursor"][0] == "0":
                # unix seconds: 1788536000 = 2026-09-04 (Thai day), the owner's own reply just before it
                return 200, {"code": 0, "data": {"comments": [
                    {"comment_id": "c1", "video_id": "vid1", "text": "สาขาขอนแก่นคิวยาวมาก", "create_time": 1788536000, "username": "user1", "owner": False},
                    {"comment_id": "c2", "text": "ขอบคุณค่ะ", "create_time": 1788526000, "owner": True}],
                    "cursor": 2, "has_more": True}}
            return 200, {"code": 0, "data": {"comments": [{"comment_id": "c3", "text": "เก่ามาก", "create_time": 1690000000, "owner": False}],
                                             "has_more": False}}
        if T.COMMENT_REPLY_PATH in url:
            return 200, {"code": 0, "data": {"comment_id": "reply1"}}
        return 200, {"code": 40001, "message": "unknown " + url}


def test_tiktok_comments_reply_and_auth():
    http = FakeTikTok()
    cfg = {"client_key": "ck", "client_secret": "cs", "refresh_token": "RT1", "business_id": "biz1", "max_videos": 5}
    rows, meta = T.fetch_rows(cfg, http, since="2026-01-01")
    assert [r["external_id"] for r in rows] == ["comment:c1"]      # the account's own reply and the 2023 comment are skipped
    assert rows[0]["kind"] == "comment" and rows[0]["ref"] == "vid1/c1" and rows[0]["detect_branch"] is True
    assert rows[0]["location"] == "แซลมอนไม่อั้น" and rows[0]["url"].endswith("/video/vid1") and rows[0]["author"] != "user1"
    assert meta["config_update"] == {"access_token": "AT2", "refresh_token": "RT2", "business_id": "biz1"}
    assert meta["videos"][0]["comments"] == 1
    assert T.reply_to_comment("AT2", "biz1", "vid1", "c1", "ขออภัยค่ะ", http) == {"comment_id": "reply1"}
    with pytest.raises(RuntimeError, match="150"):
        T.reply_to_comment("AT2", "biz1", "vid1", "c1", "x" * 151, http)
    with pytest.raises(RuntimeError, match="code 40001"):
        T._data(200, {"code": 40001, "message": "bad"}, "x")
    with pytest.raises(RuntimeError, match="lacks refresh_token"):
        T.refresh({"client_key": "a", "client_secret": "b"}, http)
    assert T.exchange_code("ck", "cs", "CODE", "https://app/cb", http) == {"access_token": "AT1", "refresh_token": "RT1", "business_id": "biz1"}
    url = T.auth_url("ck", "https://app/cb")
    assert "client_key=ck" in url and "comment.list.manage" in url
    cfg2 = FF.tiktok_auth({"tiktok": {"client_key": "ck", "client_secret": "cs"}}, http, code="CODE", log=lambda *a: None)
    assert cfg2["tiktok"]["access_token"] == "AT1" and cfg2["tiktok"]["redirect_uri"].endswith("/api/feedback/oauth-callback")
    with pytest.raises(SystemExit):
        FF.tiktok_auth({}, http, code="CODE", log=lambda *a: None)


def test_fetch_run_saves_tiktok_tokens_and_sends_tiktok_replies(tmp_path):
    db = tmp_path / "local.db"; _seed_db(db)
    sink = FF.DbSink(db)
    cfgp = tmp_path / "cfg.json"
    FF.save_config({"tiktok": {"client_key": "ck", "client_secret": "cs", "refresh_token": "RT1", "business_id": "biz1"}}, cfgp)
    cfg, http = FF.load_config(cfgp), FakeTikTok()
    assert FF.run(_args(config=str(cfgp), since="all"), cfg, sink, http=http, log=lambda *a: None) == 0
    assert FF.load_config(cfgp)["tiktok"]["refresh_token"] == "RT2"          # the rotated token was written back
    con = sqlite3.connect(str(db)); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM customer_feedback").fetchone()
    assert row["source"] == "TikTok" and row["kind"] == "comment" and row["external_ref"] == "vid1/c1"
    assert row["branch_name"] == "ขอนแก่น" and row["branch_id"] == 1            # detected from the comment text
    con.execute("UPDATE customer_feedback SET reply_status='pending', reply_draft='ขออภัยค่ะ' WHERE id=?", (row["id"],)); con.commit()
    assert FF.send_replies(cfg, sink, http, log=lambda *a: None) == {"sent": 1, "failed": 0}
    assert con.execute("SELECT reply_status FROM customer_feedback").fetchone()[0] == "sent"
    assert any(m == "POST" and T.COMMENT_REPLY_PATH in u and b["comment_id"] == "c1" and b["video_id"] == "vid1"
               for m, u, b in http.calls)
    con.close()


# ── kinds + branch detection through the endpoints ───────────────────────────
def test_kinds_filter_and_branch_detection(client):
    rows = [
        {"text": "โอชิเน BranchA อร่อยมาก", "source": "X", "external_id": "tweet:1", "date": "2026-09-01", "kind": "mention", "detect_branch": True},
        {"text": "BranchB รอนานมาก", "source": "YouTube", "external_id": "comment:1", "date": "2026-09-02", "kind": "comment", "detect_branch": True},
        {"text": "ไม่มีชื่อสาขา", "source": "Web", "external_id": "item:1", "date": "2026-09-02", "kind": "mention", "detect_branch": True},
        {"text": "แซลมอนสด", "branch": "BranchA", "source": "Google", "external_id": "r9", "rating": 5, "date": "2026-09-03"},
    ]
    r = client.post("/api/feedback/upload", json={"rows": rows}).json()
    assert r["inserted"] == 4 and r["unmatched"] == []
    items = {i["external_id"]: i for i in client.get("/api/feedback/items").json()["items"]}
    assert items["tweet:1"]["branch_name"] == "BranchA" and items["comment:1"]["branch_name"] == "BranchB"
    assert items["item:1"]["branch_name"] == "" and items["r9"]["kind"] == "review" and items["tweet:1"]["kind"] == "mention"
    s = client.get("/api/feedback/summary", params={"month_from": "2026-09", "month_to": "2026-09"}).json()
    assert s["total"] == 4 and s["kinds"] == {"review": 1, "comment": 1, "mention": 2}
    s_m = client.get("/api/feedback/summary", params={"kind": "mention"}).json()
    assert s_m["total"] == 2 and s_m["kinds"] == {"review": 1, "comment": 1, "mention": 2}   # chip counts ignore the kind filter
    assert s_m["filters"]["kind"] == "mention"
    assert client.get("/api/feedback/summary", params={"kind": "bogus"}).status_code == 400
    assert [i["external_id"] for i in client.get("/api/feedback/items", params={"kind": "comment"}).json()["items"]] == ["comment:1"]
    # a negative mention is read, not answered; a negative comment is in the reply list
    assert [i["external_id"] for i in client.get("/api/feedback/items", params={"needs_reply": 1}).json()["items"]] == ["comment:1"]
    g = [x for x in client.get("/api/feedback/sources").json()["sources"] if x["source"] == "YouTube"][0]
    assert g["unreplied"] == 1
    # a mention cannot be queued for an API reply; a TikTok comment can, within 150 characters
    d = client.get(f"/api/feedback/{items['tweet:1']['id']}/reply-draft").json()
    assert d["channel"] == "manual" and d["max_len"] == 4000 and d["kind"] == "mention" and "พูดถึง" in d["note"]
    client.post("/api/feedback/upload", json={"rows": [
        {"text": "คิวยาว", "source": "TikTok", "external_id": "comment:t1", "ref": "vid1/t1", "date": "2026-09-03", "kind": "comment", "branch": "BranchA"}]})
    tk = client.get("/api/feedback/items", params={"source": "TikTok"}).json()["items"][0]["id"]
    d = client.get(f"/api/feedback/{tk}/reply-draft").json()
    assert d["channel"] == "tiktok" and d["max_len"] == 150 and "150" in d["note"]
    assert client.post(f"/api/feedback/{tk}/reply", json={"text": "x" * 151}).status_code == 400
    assert client.post(f"/api/feedback/{tk}/reply", json={"text": "ขออภัยค่ะ"}).json()["reply_status"] == "pending"
    assert client.get("/api/feedback/reply-queue").json()["items"][0]["external_ref"] == "vid1/t1"
    # the OAuth callback page shows the code for copy-paste
    page = client.get("/api/feedback/oauth-callback", params={"code": "ABC123", "state": "s"})
    assert page.status_code == 200 and "ABC123" in page.text
    assert "ไม่ได้รับ code" in client.get("/api/feedback/oauth-callback", params={"error": "access_denied"}).text
