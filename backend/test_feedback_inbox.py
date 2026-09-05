# -*- coding: utf-8 -*-
"""The page inbox as feedback: Messenger conversations (and Instagram DMs
through the linked page) aggregated per thread per Thai day, the page's
answer detected, kind "message" — private, never posted to. Fakes only."""
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

from feedback_sources import facebook as F  # noqa: E402
from feedback_sources import instagram as I  # noqa: E402


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


CONVS = [{"id": "t1", "link": "/oshinei/inbox/t1", "updated_time": "2026-09-04T10:00:00+0000",
          "participants": {"data": [{"id": "u1", "name": "A"}, {"id": "PAGE1", "name": "Oshinei"}]}, "message_count": 7},
         {"id": "t2", "link": "/oshinei/inbox/t2", "updated_time": "2026-08-01T10:00:00+0000"}]      # older than since → stop
MSGS_T1 = [     # newest first, as the Graph API returns them
    {"id": "m7", "created_time": "2026-09-04T10:00:00+0000", "from": {"id": "u1"}, "message": "ยังไม่ได้เลยค่ะ รอมา 2 วันแล้ว"},
    {"id": "m6", "created_time": "2026-09-04T09:30:00+0000", "from": {"id": "PAGE1"}, "message": "ตรวจสอบแล้วค่ะ กำลังดำเนินการ"},
    {"id": "m5", "created_time": "2026-09-04T09:00:00+0000", "from": {"id": "u1"}, "message": "ยังไม่ได้รับเงินคืนเลยค่ะ"},
    {"id": "m4", "created_time": "2026-09-03T12:00:00+0000", "from": {"id": "PAGE1"}, "message": "ขออภัยค่ะ ทางร้านจะตรวจสอบให้"},
    {"id": "m3", "created_time": "2026-09-03T11:00:00+0000", "from": {"id": "u1"}, "message": "สาขาขอนแก่นคิดเงินเกิน 200 บาท"},
    {"id": "m2", "created_time": "2026-09-03T10:59:00+0000", "from": {"id": "u1"}, "message": "สวัสดี"},          # too short
    {"id": "m1", "created_time": "2026-09-01T10:00:00+0000", "from": {"id": "u1"}, "message": "จองโต๊ะ 4 คนคืนนี้ได้ไหมคะ"},  # before since
]


def _http(method, url, headers, body):
    if "/PAGE1/conversations?" in url:
        assert "access_token=PT" in url
        return 200, {"data": CONVS}
    if "/t1/messages?" in url:
        return 200, {"data": MSGS_T1}
    if "/t2/messages?" in url:
        raise AssertionError("a thread older than since must not be read")
    if "/ratings" in url:
        return 200, {"data": []}
    if "/17841/media?" in url or "/17841/tags?" in url:
        return 200, {"data": []}
    return 400, {"error": {"type": "OAuthException", "message": "unexpected " + url}}


def test_inbox_aggregates_per_thread_per_day_and_detects_the_answer():
    rows, n = F.fetch_inbox("PAGE1", "PT", _http, since="2026-09-02")
    assert n == 1                                                   # t2 is older than since: paging stops there
    by = {r["external_id"]: r for r in rows}
    assert set(by) == {"msg:t1:2026-09-03", "msg:t1:2026-09-04"}   # m1 (1 Sep) is before since, m2 too short
    d3 = by["msg:t1:2026-09-03"]
    assert d3["text"] == "สาขาขอนแก่นคิดเงินเกิน 200 บาท" and d3["kind"] == "message" and d3["location"] == "Inbox"
    assert d3["reply"] == "ขออภัยค่ะ ทางร้านจะตรวจสอบให้" and d3["replied_at"] == "2026-09-03"   # answered the same day
    assert d3["url"] == "https://www.facebook.com/oshinei/inbox/t1" and d3["author"] and d3["author"] != "u1"
    assert d3["detect_branch"] is True and d3["ref"] == "" and d3["rating"] is None
    d4 = by["msg:t1:2026-09-04"]
    assert d4["text"] == "ยังไม่ได้รับเงินคืนเลยค่ะ | ยังไม่ได้เลยค่ะ รอมา 2 วันแล้ว"
    assert d4["reply"] == "" and d4["replied_at"] == ""           # the customer wrote again after the page's answer
    conv_url = "https://www.facebook.com/PAGE1/conversations?platform=messenger"
    # the platform parameter and the link fallback for Instagram DMs
    seen = []
    def http2(method, url, headers, body):
        seen.append(url)
        if "/conversations?" in url:
            return 200, {"data": [{"id": "ig1", "updated_time": "2026-09-04T10:00:00+0000"}]}
        if "/ig1/messages?" in url:
            return 200, {"data": [{"id": "x1", "created_time": "2026-09-04T01:00:00+0000", "from": {"id": "cust9"}, "message": "ทุเรียนหมดตั้งแต่ทุ่มนึง สาขาระยอง"}]}
        return 400, {}
    rows2, n2 = F.fetch_inbox("PAGE1", "PT", http2, since="", platform="instagram", self_ids=("17841",), source="Instagram", branch="ระยอง")
    assert n2 == 1 and rows2[0]["source"] == "Instagram" and rows2[0]["branch"] == "ระยอง" and rows2[0]["detect_branch"] is False
    assert rows2[0]["url"] == "https://business.facebook.com/latest/inbox/instagram?asset_id=PAGE1"
    assert any("platform=instagram" in u for u in seen) and conv_url.startswith("https://")


def test_inbox_is_opt_in_on_pages_and_accounts():
    page = {"id": "PAGE1", "name": "Oshinei", "branch": "ขอนแก่น", "token": "PT", "shares": False, "visitor_posts": False, "tagged": False}
    rows, m = F.fetch_page(page, _http, since="2026-09-02")
    assert rows == [] and m["inbox"] == 0 and m["inbox_threads"] == 0
    rows, m = F.fetch_page(dict(page, inbox=True), _http, since="2026-09-02")
    assert m["inbox_threads"] == 1 and m["inbox"] == 2 and all(r["kind"] == "message" and r["branch"] == "ขอนแก่น" for r in rows)
    acc = {"ig_user_id": "17841", "username": "oshinei.official", "page_id": "PAGE1", "token": "PT", "branch": "", "inbox": True}
    calls = []
    def http3(method, url, headers, body):
        calls.append(url)
        return _http(method, url, headers, body)
    rows, m = I.fetch_account(acc, http3, since="2026-09-02")
    assert m["inbox_threads"] == 1 and m["inbox"] == 2 and rows[0]["source"] == "Instagram"
    assert any("/PAGE1/conversations?" in u and "platform=instagram" in u for u in calls)


def test_message_kind_through_the_endpoints(client):
    rows = [{"text": "สาขาขอนแก่นคิดเงินเกิน 200 บาท รอนานมาก", "branch": "BranchA", "source": "Facebook", "external_id": "msg:t1:2026-09-03",
             "date": "2026-09-03", "kind": "message", "url": "https://www.facebook.com/oshinei/inbox/t1", "reply": "ขออภัยค่ะ", "replied_at": "2026-09-03"},
            {"text": "ยังไม่ได้รับเงินคืนเลยค่ะ แย่มาก", "branch": "BranchA", "source": "Facebook", "external_id": "msg:t1:2026-09-04",
             "date": "2026-09-04", "kind": "message", "url": "https://www.facebook.com/oshinei/inbox/t1"}]
    r = client.post("/api/feedback/upload", json={"rows": rows}).json()
    assert r["inserted"] == 2
    items = {i["external_id"]: i for i in client.get("/api/feedback/items", params={"kind": "message"}).json()["items"]}
    assert set(items) == {"msg:t1:2026-09-03", "msg:t1:2026-09-04"}
    assert items["msg:t1:2026-09-03"]["needs_reply"] is False           # the page answered
    assert items["msg:t1:2026-09-04"]["needs_reply"] is True            # negative and unanswered → in the queue
    s = client.get("/api/feedback/summary", params={"month_from": "2026-09", "month_to": "2026-09"}).json()
    assert s["kinds"] == {"message": 2} and s["unreplied"] == 1
    d = client.get(f"/api/feedback/{items['msg:t1:2026-09-04']['id']}/reply-draft").json()
    assert d["channel"] == "manual" and "Inbox" in d["note"] and "ขออภัย" in d["draft"]
    q = client.post(f"/api/feedback/{items['msg:t1:2026-09-04']['id']}/reply", json={"text": "ขออภัยค่ะ"}).json()
    assert q["reply_status"] == "manual"                                 # kept for pasting, never sent by the API
    assert client.get("/api/feedback/summary", params={"kind": "bogus"}).status_code == 400
    src = [x for x in client.get("/api/feedback/sources").json()["sources"] if x["source"] == "Facebook"][0]
    assert src["unreplied"] == 1                                         # a manual draft is not an answer: still waiting until the inbox reply is fetched
