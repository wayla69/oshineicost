# -*- coding: utf-8 -*-
"""Review connectors (Google Business Profile, Facebook Pages), the Takeout /
JSON / XLSX parsers, the upsert on edited reviews and owner replies, the
sources + sync-log endpoints, the fetcher's run loop and the Telegram digest.
Every network call goes through an injected fake http(method, url, headers,
body) → (status, json)."""
import datetime
import io
import json
import sqlite3
import sys
import urllib.parse
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import fetch_feedback as FF  # noqa: E402
import notify as N  # noqa: E402
from feedback_sources import facebook as F  # noqa: E402
from feedback_sources import google_gbp as G  # noqa: E402
from routers.feedback import normalise_row, parse_rows  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_feedback_rows(request):
    """The session DB is shared (the goldens pin the EMPTY summary/sources)."""
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


# ── fixtures: what Google returns ────────────────────────────────────────────
def _rv(rid, stars, comment, created, updated=None, reply=None, name="Somchai"):
    d = {"reviewId": rid, "name": f"accounts/1/locations/9/reviews/{rid}", "reviewer": {"displayName": name},
         "starRating": stars, "createTime": created, "updateTime": updated or created}
    if comment is not None:
        d["comment"] = comment
    if reply:
        d["reviewReply"] = {"comment": reply, "updateTime": updated or created}
    return d


REVIEWS = [
    _rv("r1", "FIVE", "(Translated by Google) Fresh salmon\n\n(Original) แซลมอนสดมาก บริการดี", "2026-08-30T20:02:03.123Z", reply="ขอบคุณค่ะ"),
    _rv("r2", "ONE", "รอนานมาก พนักงานไม่สนใจ", "2026-08-28T05:00:00Z"),
    _rv("r3", "FOUR", None, "2026-08-01T05:00:00Z"),                # star only
    _rv("r4", "TWO", "ทุเรียนไม่สด", "2026-07-15T05:00:00Z"),
]
CFG = {"client_id": "cid", "client_secret": "sec", "refresh_token": "RT", "location_map": {"Oshinei โคราช": "โคราช1"}}


class FakeGoogle:
    """Token, accounts, locations and reviews (page size 2) keyed by URL."""

    def __init__(self, reviews=REVIEWS, locations=None):
        self.reviews, self.calls = reviews, []
        self.locations = locations or [
            {"name": "locations/9", "title": "Oshinei Buffet ขอนแก่น", "metadata": {"placeId": "PID9"}},
            {"name": "locations/10", "title": "Oshinei โคราช", "metadata": {}}]

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url))
        if url == G.TOKEN_URL:
            return 200, {"access_token": "AT", "expires_in": 3599, "refresh_token": "RT-new"}
        if url.startswith(G.ACCOUNTS_API):
            return 200, {"accounts": [{"name": "accounts/1", "accountName": "Oshinei"}]}
        if url.startswith(G.LOCATIONS_API):
            assert headers["Authorization"] == "Bearer AT"
            return 200, {"locations": self.locations}
        if url.startswith(G.REVIEWS_API):
            if "locations/10/" in url:
                return 200, {"totalReviewCount": 0}
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            page = int((q.get("pageToken") or ["0"])[0])
            chunk = self.reviews[page * 2:(page + 1) * 2]
            out = {"reviews": chunk, "totalReviewCount": len(self.reviews)}
            if (page + 1) * 2 < len(self.reviews):
                out["nextPageToken"] = str(page + 1)
            return 200, out
        return 404, {"error": {"message": "unknown " + url}}


# ── Google ───────────────────────────────────────────────────────────────────
def test_google_review_to_row_normalises_translation_stars_dates_and_reply():
    row = G.review_to_row(REVIEWS[0], "ขอนแก่น", "Oshinei Buffet ขอนแก่น", "https://x")
    assert row["text"] == "แซลมอนสดมาก บริการดี" and row["rating"] == 5
    assert row["date"] == "2026-08-31"                      # 20:02 UTC = 03:02 the next Thai day
    assert row["external_id"] == "r1" and row["reply"] == "ขอบคุณค่ะ" and row["replied_at"] == "2026-08-31"
    assert row["source"] == "Google" and row["url"] == "https://x" and row["location"] == "Oshinei Buffet ขอนแก่น"
    assert row["author"] and row["author"] != "Somchai"     # hashed, never the name
    star_only = G.review_to_row(REVIEWS[2], "x")
    assert star_only["text"] == "" and star_only["rating"] == 4
    assert G.review_to_row({"reviewId": "e"}, "x") is None
    assert G.thai_date("2026-08-30T11:02:03+0000") == "2026-08-30" and G.thai_date("") == ""
    assert G.original_text("(Translated by Google) Great") == "Great"
    assert "client_id=cid" in G.auth_url("cid", "http://localhost:1/") and "access_type=offline" in G.auth_url("cid", "http://localhost:1/")


def test_google_fetch_rows_pages_maps_locations_and_stops_at_since():
    http = FakeGoogle()
    rows, meta = G.fetch_rows(CFG, http)
    assert [r["external_id"] for r in rows] == ["r1", "r2", "r3", "r4"]
    assert rows[0]["branch"] == "Oshinei Buffet ขอนแก่น"    # no map entry → the title, matched on the server
    assert rows[0]["url"] == "https://search.google.com/local/reviews?placeid=PID9"
    assert meta["fetched"] == 4 and [loc["branch"] for loc in meta["locations"]] == ["Oshinei Buffet ขอนแก่น", "โคราช1"]
    assert sum(1 for m, u in http.calls if "locations/9/reviews" in u) == 2      # two pages
    http2 = FakeGoogle()
    rows, meta = G.fetch_rows(CFG, http2, since="2026-08-20")
    assert [r["external_id"] for r in rows] == ["r1", "r2"]
    assert sum(1 for m, u in http2.calls if "locations/9/reviews" in u) == 2     # page 1 ends inside the window → page 2 read (and skipped)
    http3 = FakeGoogle()
    rows, meta = G.fetch_rows(CFG, http3, since="2026-08-29")
    assert [r["external_id"] for r in rows] == ["r1"]
    assert sum(1 for m, u in http3.calls if "locations/9/reviews" in u) == 1     # page 1 already older than since → stop


def test_google_errors_are_reported_not_swallowed():
    def denied(method, url, headers, body):
        return 403, {"error": {"status": "PERMISSION_DENIED", "message": "The caller does not have permission"}}
    with pytest.raises(RuntimeError, match="PERMISSION_DENIED"):
        G.access_token(CFG, denied)
    with pytest.raises(RuntimeError, match="lacks refresh_token"):
        G.access_token({"client_id": "a", "client_secret": "b"}, denied)
    with pytest.raises(RuntimeError, match="no refresh_token"):
        G.exchange_code("a", "b", "code", "http://localhost:1/", lambda *a: (200, {"access_token": "x"}))


def _takeout_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Takeout/Google Business Profile/account-1/location-9/data.json",
                   json.dumps({"name": "locations/9", "title": "Oshinei Buffet ขอนแก่น", "metadata": {"placeId": "PID9"}}))
        z.writestr("Takeout/Google Business Profile/account-1/location-9/reviews.json", json.dumps({"reviews": REVIEWS[:2]}))
        z.writestr("Takeout/Google Business Profile/account-1/location-10/reviews.json", json.dumps({"reviews": REVIEWS[2:]}))
        z.writestr("Takeout/archive_browser.html", "<html></html>")
    return buf.getvalue()


def test_takeout_zip_parses_locations_and_titles():
    rows, locs = G.parse_takeout_zip(_takeout_zip())
    assert len(rows) == 4 and {loc["title"] for loc in locs} == {"Oshinei Buffet ขอนแก่น", "location-10"}
    assert rows[0]["branch"] == "Oshinei Buffet ขอนแก่น" and rows[0]["url"].endswith("PID9")
    rows2, _ = G.parse_takeout_zip(_takeout_zip(), default_branch="ขอนแก่น")
    assert {r["branch"] for r in rows2} == {"ขอนแก่น"}
    rows3, _ = G.parse_takeout_zip(_takeout_zip(), location_map={"Oshinei Buffet ขอนแก่น": "ขอนแก่น"})
    assert [r["branch"] for r in rows3][:2] == ["ขอนแก่น", "ขอนแก่น"]


def test_parse_rows_handles_zip_json_and_xlsx():
    rows = parse_rows(data=_takeout_zip(), filename="takeout-2026.zip")
    assert len(rows) == 4 and rows[2]["text"] == "" and rows[2]["rating"] == 4.0
    rows = parse_rows(data=json.dumps({"reviews": REVIEWS[:2]}).encode("utf-8"), filename="reviews.json", default_branch="ขอนแก่น")
    assert [r["external_id"] for r in rows] == ["r1", "r2"] and rows[0]["branch"] == "ขอนแก่น"
    generic = json.dumps([{"วันที่": "2026-08-01", "สาขา": "ระยอง", "รีวิว": "อร่อย", "คะแนน": 5}]).encode("utf-8")
    assert parse_rows(data=generic, filename="x.json") == \
        [normalise_row({"date": "2026-08-01", "branch": "ระยอง", "source": "", "rating": 5, "text": "อร่อย"})]
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active
    ws.append(["date", "branch", "rating", "review"])
    ws.append([datetime.date(2026, 8, 2), "ระยอง", 2, "รอนาน"])
    ws.append([None, None, None, ""])
    b = io.BytesIO(); wb.save(b)
    assert parse_rows(data=b.getvalue(), filename="reviews.xlsx", default_source="Grab") == \
        [normalise_row({"date": "2026-08-02", "branch": "ระยอง", "source": "Grab", "rating": 2, "text": "รอนาน"})]
    assert parse_rows(data=b.getvalue(), filename="")[0]["text"] == "รอนาน"        # sniffed: a PK archive with xl/
    with pytest.raises(Exception):
        parse_rows(data=b"PK\x03\x04garbage", filename="bad.zip")


# ── Facebook ─────────────────────────────────────────────────────────────────
_FB_ITEMS = [
    {"created_time": "2026-08-30T11:02:03+0000", "recommendation_type": "positive", "review_text": "อร่อยมาก",
     "has_review": True, "reviewer": {"name": "A", "id": "1"}, "open_graph_story": {"id": "s1"}},
    {"created_time": "2026-08-29T11:02:03+0000", "recommendation_type": "negative", "has_review": False,
     "reviewer": {"name": "B", "id": "2"}},
    {"created_time": "2026-07-01T11:02:03+0000", "rating": 4, "review_text": "ok", "reviewer": {"name": "C"}},
]
_FB_POSTS = [{"id": "p1", "created_time": "2026-08-30T00:00:00+0000", "permalink_url": "https://fb/p1", "comments": {"data": [
    {"id": "c1", "created_time": "2026-08-30T01:00:00+0000", "message": "ทุเรียนหมดเร็วมาก", "from": {"id": "u1", "name": "D"}},
    {"id": "c2", "created_time": "2026-08-30T02:00:00+0000", "message": "ขออภัยค่ะ", "from": {"id": "PAGE1", "name": "Oshinei"}}]}}]


def _fb_http(method, url, headers, body):
    if "/ratings" in url:
        if "after=" in url:
            return 200, {"data": _FB_ITEMS[2:]}
        return 200, {"data": _FB_ITEMS[:2], "paging": {"next": url + "&after=xyz"}}
    if "/posts" in url:
        return 200, {"data": _FB_POSTS}
    if "/me/accounts" in url:
        return 200, {"data": [{"id": "PAGE1", "name": "Oshinei ขอนแก่น", "access_token": "PT"}]}
    return 400, {"error": {"type": "OAuthException", "message": "bad token"}}


def test_facebook_rows_paging_since_and_pages():
    cfg = {"pages": [{"id": "PAGE1", "name": "Oshinei ขอนแก่น", "branch": "ขอนแก่น", "token": "PT", "comments": True}]}
    rows, meta = F.fetch_rows(cfg, _fb_http)
    assert meta["pages"][0] == {"id": "PAGE1", "name": "Oshinei ขอนแก่น", "branch": "ขอนแก่น", "ratings": 3, "comments": 1}
    by_ext = {r["external_id"]: r for r in rows}
    assert by_ext["rec:s1"]["sentiment_hint"] == "pos" and by_ext["rec:s1"]["rating"] is None
    assert by_ext["rec:s1"]["date"] == "2026-08-30" and by_ext["rec:s1"]["url"] == "https://www.facebook.com/PAGE1/reviews"
    neg = [r for r in rows if r["sentiment_hint"] == "neg"][0]
    assert neg["text"] == "👎 ไม่แนะนำ" and neg["external_id"].startswith("rec:")
    assert by_ext["comment:c1"]["text"] == "ทุเรียนหมดเร็วมาก" and by_ext["comment:c1"]["url"] == "https://fb/p1"
    assert "comment:c2" not in by_ext                       # the page's own reply is not feedback
    rows2, _ = F.fetch_rows(cfg, _fb_http, since="2026-08-01")
    assert len([r for r in rows2 if r["external_id"].startswith("rec:")]) == 2   # stopped before July
    assert F.list_pages("UT", _fb_http) == [{"id": "PAGE1", "name": "Oshinei ขอนแก่น", "token": "PT"}]
    with pytest.raises(RuntimeError, match="OAuthException"):
        F.fetch_page({"id": "P2", "token": "bad"}, lambda *a: (400, {"error": {"type": "OAuthException", "message": "bad token"}}))
    with pytest.raises(RuntimeError, match="no pages"):
        F.fetch_rows({}, _fb_http)
    cfg2 = {}
    FF.facebook_pages(cfg2, _fb_http, "UT", save=True, log=lambda *a: None)
    assert cfg2["facebook"]["pages"] == [{"id": "PAGE1", "name": "Oshinei ขอนแก่น", "branch": "", "comments": False, "token": "PT"}]


# ── endpoints ────────────────────────────────────────────────────────────────
def test_upload_upserts_edited_reviews_and_replies(client):
    rows = [G.review_to_row(rv, "BranchA", "Oshinei BranchA", "https://g") for rv in REVIEWS]
    r = client.post("/api/feedback/upload", json={"rows": rows, "by": "fetch:google"}).json()
    assert r["inserted"] == 4 and r["updated"] == 0 and r["unmatched"] == [] and r["analysed_by"] == "lexicon"
    assert len(r["new_items"]) == 4 and r["new_items"][1]["sentiment"] == "neg" and r["new_items"][0]["url"] == "https://g"
    # the same rows again: nothing analysed, nothing changed
    r = client.post("/api/feedback/upload", json={"rows": rows}).json()
    assert (r["inserted"], r["updated"], r["unchanged"], r["analysed_by"]) == (0, 0, 4, "none")
    # the reviewer edits r2 (text + stars); the branch replies to r4
    edited = dict(REVIEWS[1], comment="แก้ไข: รอนานแต่พนักงานขอโทษ", starRating="THREE")
    replied = dict(REVIEWS[3], reviewReply={"comment": "ขออภัยค่ะ", "updateTime": "2026-08-31T01:00:00Z"})
    r = client.post("/api/feedback/upload", json={"rows": [G.review_to_row(edited, "BranchA"), G.review_to_row(replied, "BranchA")]}).json()
    assert (r["inserted"], r["updated"], r["unchanged"]) == (0, 2, 0)
    items = {i["external_id"]: i for i in client.get("/api/feedback/items", params={"source": "Google"}).json()["items"]}
    assert items["r2"]["text"].startswith("แก้ไข") and items["r2"]["rating"] == 3 and items["r2"]["needs_reply"] is True
    assert items["r4"]["reply_text"] == "ขออภัยค่ะ" and items["r4"]["replied_at"] == "2026-08-31" and items["r4"]["needs_reply"] is False
    assert items["r3"]["text"] == "" and items["r3"]["sentiment"] == "pos"      # star-only review kept, rated by its stars
    assert items["r1"]["branch_name"] == "BranchA" and items["r1"]["location"] == "Oshinei BranchA"
    assert [i["external_id"] for i in client.get("/api/feedback/items", params={"needs_reply": 1}).json()["items"]] == ["r2"]
    s = client.get("/api/feedback/summary", params={"month_from": "2026-07", "month_to": "2026-09"}).json()
    assert s["total"] == 4 and s["unreplied"] == 1 and s["filters"]["source"] is None
    neg = [x for x in s["recent_negative"] if x["id"] == items["r4"]["id"]][0]
    assert neg["replied"] is True and neg["needs_reply"] is False and neg["url"] == "https://g"
    assert client.get("/api/feedback/summary", params={"source": "Grab"}).json()["total"] == 0
    # a listing no branch name matches comes back to the caller (and is still stored under its own name)
    r = client.post("/api/feedback/upload", json={"rows": [
        {"text": "x", "branch": "Oshinei Nowhere", "location": "Oshinei Nowhere", "source": "Google", "external_id": "n1", "date": "2026-08-01"}]}).json()
    assert r["unmatched"] == ["Oshinei Nowhere"] and r["inserted"] == 1
    # a Facebook recommendation without text: the hint decides
    r = client.post("/api/feedback/upload", json={"rows": [
        {"text": "👎 ไม่แนะนำ", "branch": "BranchB", "source": "Facebook", "external_id": "rec:z", "date": "2026-08-02", "sentiment_hint": "neg"}]}).json()
    assert r["inserted"] == 1 and r["new_items"][0]["sentiment"] == "neg"


def test_sources_and_sync_log_endpoints(client):
    assert client.get("/api/feedback/sources").json() == {"sources": [], "last_ok": {}, "runs": []}
    client.post("/api/feedback/upload", json={"rows": [G.review_to_row(REVIEWS[1], "BranchA")]})
    ok = client.post("/api/feedback/sync-log", json={"source": "Google", "since": "2026-08-01", "fetched": 1, "inserted": 1,
                                                     "status": "ok", "message": "2 listing(s)", "by": "fetch_feedback"})
    assert ok.status_code == 200 and ok.json()["logged"] > 0
    assert client.post("/api/feedback/sync-log", json={"source": "Facebook", "status": "error", "message": "bad token"}).status_code == 200
    assert client.post("/api/feedback/sync-log", json={}).status_code == 400
    d = client.get("/api/feedback/sources").json()
    g = [s for s in d["sources"] if s["source"] == "Google"][0]
    assert g["n"] == 1 and g["fetched"] == 1 and g["unreplied"] == 1 and g["latest"] == "2026-08-28"
    assert g["last_run"]["status"] == "ok" and g["last_run"]["inserted"] == 1 and g["last_run"]["since"] == "2026-08-01"
    fb = [s for s in d["sources"] if s["source"] == "Facebook"][0]
    assert fb["n"] == 0 and fb["last_run"]["status"] == "error" and fb["last_run"]["message"] == "bad token"
    assert list(d["last_ok"]) == ["Google"] and len(d["runs"]) == 2


# ── the fetcher ──────────────────────────────────────────────────────────────
def _seed_db(path):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE branches(branch_id INTEGER PRIMARY KEY, name TEXT)")
    con.executemany("INSERT INTO branches VALUES(?,?)", [(1, "ขอนแก่น"), (2, "โคราช1")])
    con.execute("CREATE TABLE skus(sku_code TEXT, description TEXT)")
    con.executemany("INSERT INTO skus VALUES(?,?)", [("A1", "(B) แซลมอน ซาชิมิ (1 ชิ้น)"), ("A2", "(B) ทุเรียน (1 เม็ด)")])
    con.commit(); con.close()


def _args(**kw):
    base = dict(source="all", since="auto", dry_run=False, no_alert=False, api="https://app")
    base.update(kw)
    return SimpleNamespace(**base)


def test_fetch_run_end_to_end_with_a_local_db_and_the_alert(tmp_path):
    db = tmp_path / "local.db"; _seed_db(db)
    sink, sent, log = FF.DbSink(db), [], []
    cfg = {"google": CFG}
    assert FF.run(_args(), cfg, sink, http=FakeGoogle(), notifier=lambda text: sent.append(text) or True, log=log.append) == 0
    con = sqlite3.connect(str(db)); con.row_factory = sqlite3.Row
    rows = {r["external_id"]: dict(r) for r in con.execute("SELECT * FROM customer_feedback")}
    assert set(rows) == {"r1", "r2", "r3", "r4"}
    assert rows["r1"]["branch_name"] == "ขอนแก่น" and rows["r1"]["branch_id"] == 1     # title contains the branch name
    assert rows["r1"]["reply_text"] == "ขอบคุณค่ะ" and rows["r4"]["menus"] == '["ทุเรียน"]' and rows["r1"]["uploaded_by"] == "fetch:google"
    runs = con.execute("SELECT * FROM feedback_sync_log").fetchall()
    assert len(runs) == 1 and runs[0]["status"] == "ok" and runs[0]["inserted"] == 4 and runs[0]["since"] == ""
    assert "2 listing(s)" in runs[0]["message"]
    assert len(sent) == 1 and "รีวิวเชิงลบใหม่ 2 รายการ" in sent[0] and "รอนานมาก" in sent[0] and "ทุเรียนไม่สด" in sent[0]
    # the next night: since = last good run − 2 days → nothing new, no alert, still logged
    assert FF.run(_args(), cfg, sink, http=FakeGoogle(), notifier=lambda t: sent.append(t) or True, log=log.append) == 0
    assert len(sent) == 1 and con.execute("SELECT COUNT(*) FROM feedback_sync_log WHERE status='ok'").fetchone()[0] == 2
    assert any("since 2026-" in line for line in log)
    # a failing source is logged, exits 1; an unconfigured one says so
    def broken(method, url, headers, body):
        return 500, {"error": {"message": "boom"}}
    assert FF.run(_args(source="google", since="all", no_alert=True), cfg, sink, http=broken, log=log.append) == 1
    last = con.execute("SELECT status, message FROM feedback_sync_log ORDER BY id DESC LIMIT 1").fetchone()
    assert last[0] == "error" and "boom" in last[1]
    assert FF.run(_args(), {}, sink, log=log.append) == 0                    # nothing configured = nothing to do
    assert FF.run(_args(source="facebook"), cfg, sink, log=log.append) == 1  # asked for a source that is not configured
    # dry run: fetch and print, write nothing
    n0 = con.execute("SELECT COUNT(*) FROM feedback_sync_log").fetchone()[0]
    assert FF.run(_args(since="all", dry_run=True), cfg, sink, http=FakeGoogle(), log=log.append) == 0
    assert con.execute("SELECT COUNT(*) FROM feedback_sync_log").fetchone()[0] == n0
    con.close()


def test_api_sink_posts_through_the_endpoints(client):
    def http(method, url, headers, body):
        path = url.replace("https://app", "")
        fn = getattr(client, method.lower())
        resp = fn(path, json=body, headers=headers) if body is not None else fn(path, headers=headers)
        return resp.status_code, (resp.json() if resp.content else {})
    sink = FF.ApiSink("https://app", "tok", http=http)
    assert sink.sources()["sources"] == []
    out = sink.upload([G.review_to_row(rv, "BranchA") for rv in REVIEWS], by="fetch:google")
    assert out["inserted"] == 4 and out["analysed_by"] == "lexicon" and len(out["new_items"]) == 4 and out["unmatched"] == []
    assert sink.upload([], by="x")["inserted"] == 0
    sink.log({"source": "Google", "status": "ok", "inserted": 4})
    assert sink.sources()["last_ok"]["Google"]
    with pytest.raises(RuntimeError, match="HTTP 400"):
        sink.log({})


def test_api_sink_logs_in_when_the_site_requires_it():
    state = {"logged": False, "calls": []}

    def http(method, url, headers, body):
        state["calls"].append(url)
        if url.endswith("/api/auth/login"):
            state["logged"] = body == {"username": "u", "password": "p"}
            return (200, {"username": "u"}) if state["logged"] else (401, {})
        if not state["logged"]:
            return 401, {"detail": "Authentication required."}
        assert headers.get("X-Upload-Token") == "tok"
        return 200, {"sources": [], "last_ok": {}}
    sink = FF.ApiSink("https://app", "tok", login=("u", "p"), http=http)
    assert sink.sources() == {"sources": [], "last_ok": {}}
    assert state["calls"] == ["https://app/api/feedback/sources", "https://app/api/auth/login", "https://app/api/feedback/sources"]
    with pytest.raises(RuntimeError, match="requires login"):
        FF.ApiSink("https://app", "tok", http=lambda *a: (401, {})).sources()


def test_google_auth_exchanges_the_code_and_lists_locations(monkeypatch):
    http = FakeGoogle()
    monkeypatch.setattr(FF, "wait_for_code", lambda port, timeout=300: "CODE123")
    cfg = FF.google_auth({"google": {"client_id": "cid", "client_secret": "sec"}}, http, open_browser=False, log=lambda *a: None)
    assert cfg["google"]["refresh_token"] == "RT-new"
    assert any(m == "POST" and u == G.TOKEN_URL for m, u in http.calls)
    with pytest.raises(SystemExit):
        FF.google_auth({}, http, log=lambda *a: None)
    lines = []
    locs = FF.google_locations({"google": CFG}, FakeGoogle(), log=lines.append)
    assert [loc["branch"] for loc in locs] == ["", "โคราช1"] and "2 listing(s)" in lines[0]


def test_auto_since_config_and_token_resolution(tmp_path, monkeypatch):
    assert FF.auto_since({"last_ok": {"Google": "2026-09-03 09:00:00"}}, "Google") == "2026-09-01"
    assert FF.auto_since({"last_ok": {}}, "Google") == "" and FF.auto_since({}, "Google") == ""
    p = tmp_path / "cfg.json"
    assert FF.load_config(p) == {}
    FF.save_config({"google": {"client_id": "x", "location_map": {"Oshinei โคราช": "โคราช1"}}}, p)
    assert FF.load_config(p)["google"]["location_map"]["Oshinei โคราช"] == "โคราช1"
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "owner:abc123,other:def")
    monkeypatch.delenv("OSHINEI_WRITE_TOKEN", raising=False)
    assert FF.resolve_token() == "abc123" and FF.resolve_token(" explicit ") == "explicit"


# ── alerts ───────────────────────────────────────────────────────────────────
def test_telegram_digest_and_send():
    items = [{"branch": "ขอนแก่น", "rating": 1, "text": "รอนานมาก <b>", "url": "https://g/1"},
             {"branch": "ระยอง", "rating": None, "text": "ไม่สด"}]
    text = N.negative_digest(items, "Google", "https://app/")
    assert "รีวิวเชิงลบใหม่ 2 รายการ" in text and "&lt;b&gt;" in text and "1★" in text and "https://g/1" in text
    assert text.endswith("https://app/")
    calls = []

    def http(method, url, headers, body):
        calls.append((url, body)); return 200, {"ok": True}
    assert N.send_telegram("hi", http=http, env={}) is False                     # unconfigured → silent no-op
    cfg = {"OSHINEI_TG_TOKEN": "T", "OSHINEI_TG_CHAT_ID": "C"}
    assert N.send_telegram("hi", http=http, env=cfg) is True
    assert calls[0][0].endswith("/botT/sendMessage") and calls[0][1]["chat_id"] == "C" and calls[0][1]["parse_mode"] == "HTML"
    assert N.send_telegram("hi", http=lambda *a: (403, {"ok": False}), env=cfg) is False
