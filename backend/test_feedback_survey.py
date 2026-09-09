# -*- coding: utf-8 -*-
"""📱 แบบสอบถาม QR — the chain's own feedback channel: a branch link, the public
form, one answer landing in customer_feedback as kind "survey" (aspects → themes,
a contact → the work queue), the guards (honeypot, time on page, rate limit,
inactive code), and the login exemption of the public endpoint only."""
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

from routers import survey as S  # noqa: E402


def _wipe(con, table):
    """Empty a table if it exists — never create a stub that would shadow the real DDL."""
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        con.execute(f"DELETE FROM {table}")


@pytest.fixture(autouse=True)
def _clean(request):
    """The session DB is shared with every other module: leave no answer or link behind."""
    S._HITS.clear()
    yield
    S._HITS.clear()
    if "client" not in request.fixturenames:
        return
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    _wipe(con, "customer_feedback")
    _wipe(con, "feedback_survey_link")
    con.commit(); con.close()
    try:
        import main
        main.invalidate_cache()
    except Exception:
        pass


def _answer(client, code, **kw):
    body = {"rating": 2, "aspects": ["รอนาน"], "text": "รอนานมาก พนักงานไม่สนใจ", "contact": "081-234-5678",
            "website": "", "t0": (S._now() - 30) * 1000}
    body.update(kw)
    return client.post(f"/api/feedback/survey/{code}", json=body)


def test_link_form_answer_queue_and_guards(client, monkeypatch):
    # a branch gets its code once; the second call only updates the url
    r = client.post("/api/feedback/survey-links", json={"branch_id": 1, "by": "owner"}).json()
    code = r["code"]
    assert len(code) == 8 and r["branch"] == "BranchA" and r["active"] and r["url"].endswith(f"/s/{code}")
    r2 = client.post("/api/feedback/survey-links", json={"branch_id": 1, "google_review_url": "https://g.page/r/abc/review"}).json()
    assert r2["code"] == code and r2["google_review_url"] == "https://g.page/r/abc/review"
    assert client.post("/api/feedback/survey-links", json={"branch_id": 999}).status_code == 404
    links = client.get("/api/feedback/survey-links").json()
    assert [x["branch"] for x in links["links"]] == ["BranchA", "BranchB"] and links["links"][1]["code"] is None
    bulk = client.post("/api/feedback/survey-links", json={"all": True, "by": "owner"}).json()
    assert bulk["created"] == 1 and bulk["links"][0]["code"] == code and len(bulk["links"][1]["code"]) == 8
    assert client.post("/api/feedback/survey-links", json={"all": True}).json()["created"] == 0
    assert "รอนาน" in links["aspects"] and links["contact_days"] == S.CONTACT_DAYS

    # the guest's page: no login, names the branch, counts the scan
    page = client.get(f"/s/{code}")
    assert page.status_code == 200 and "BranchA" in page.text and "วันนี้เป็นอย่างไรบ้าง" in page.text
    assert "รอนาน / คิว" in page.text and str(S.CONTACT_DAYS) in page.text
    assert client.get("/s/nope").status_code == 404
    poster = client.get(f"/s/{code}/print")
    assert poster.status_code == 200 and f"/s/{code}" in poster.text
    # The QR is drawn here, not by a script in whatever browser the poster is printed from — a blank
    # square on a card already taped to a counter is only noticed a month of missing answers later.
    assert "<svg" in poster.text and "qrcode.min.js" not in poster.text
    # …and it carries the survey URL, not merely some QR: the same bytes segno would draw for that URL
    # have to appear in the page, so a wrong `_base_url` or a stale code fails here and not in the field.
    import segno
    want = segno.make(f"http://testserver/s/{code}", error="m").svg_inline(
        scale=1, border=0, svgclass=None, lineclass=None, omitsize=True)
    assert want[want.index("<path"):] in poster.text

    # a negative answer with a contact → kind survey, the tapped aspect is a theme, it waits in the queue
    assert _answer(client, code).json() == {"ok": True, "review_url": None, "callback": True}
    items = client.get("/api/feedback/items", params={"kind": "survey"}).json()["items"]
    assert len(items) == 1
    it = items[0]
    assert it["branch_name"] == "BranchA" and it["source"] == S.SOURCE and it["rating"] == 2 and it["sentiment"] == "neg"
    assert "รอนาน" in it["themes"] and it["needs_reply"] is True and it["contact"] == "081-234-5678"
    queue = client.get("/api/feedback/items", params={"needs_reply": "1", "sort": "priority"}).json()["items"]
    assert [q["id"] for q in queue] == [it["id"]]
    summary = client.get("/api/feedback/summary").json()
    assert summary["kinds"] == {"survey": 1} and summary["unreplied"] == 1
    draft = client.get(f"/api/feedback/{it['id']}/reply-draft").json()
    assert draft["channel"] == "manual" and "081-234-5678" in draft["note"] and "ตอบแล้ว" in draft["note"]
    assert client.get("/api/feedback/survey-links").json()["links"][0]["scans"] == 1
    assert client.get("/api/feedback/survey-links").json()["links"][0]["responses"] == 1

    # a happy answer: no contact → nothing to answer; the Google review link comes back
    r = _answer(client, code, rating=5, aspects=["ความสด"], text="แซลมอนสดมาก", contact="").json()
    assert r == {"ok": True, "review_url": "https://g.page/r/abc/review", "callback": False}
    items = client.get("/api/feedback/items", params={"kind": "survey", "sort": "newest"}).json()["items"]
    assert len(items) == 2 and {i["sentiment"] for i in items} == {"neg", "pos"}
    assert sum(1 for i in items if i["needs_reply"]) == 1
    # a star-only answer is still an answer
    assert _answer(client, code, rating=3, aspects=[], text="", contact="").json()["ok"] is True
    assert len(client.get("/api/feedback/items", params={"kind": "survey"}).json()["items"]) == 3

    # guards: honeypot and a too-quick submit are accepted but not stored; bad rating; inactive code
    assert _answer(client, code, website="http://spam").json()["ok"] is True
    assert _answer(client, code, t0=S._now() * 1000).json()["ok"] is True
    assert len(client.get("/api/feedback/items", params={"kind": "survey"}).json()["items"]) == 3
    assert client.post(f"/api/feedback/survey/{code}", json={"rating": 9}).status_code == 400
    assert client.post(f"/api/feedback/survey/{code}", json={"rating": 4, "aspects": ["<script>"], "t0": 0}).status_code == 200
    assert client.post("/api/feedback/survey/zzzzzzzz", json={"rating": 4}).status_code == 404
    client.post("/api/feedback/survey-links", json={"branch_id": 1, "active": False})
    assert client.get(f"/s/{code}").status_code == 404 and _answer(client, code).status_code == 404
    client.post("/api/feedback/survey-links", json={"branch_id": 1, "active": True})

    # rate limit per address
    monkeypatch.setattr(S, "RATE_PER_IP", 2)
    S._HITS.clear()
    assert _answer(client, code, rating=4, contact="").status_code == 200
    assert _answer(client, code, rating=4, contact="").status_code == 200
    assert _answer(client, code, rating=4, contact="").status_code == 429

    # the public answer endpoint stays reachable when the site enforces login; the manager's side does not
    monkeypatch.setenv("OSHINEI_AUTH", "1"); monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret")
    S._HITS.clear()
    assert _answer(client, code, rating=4, contact="").status_code == 200
    assert client.get(f"/s/{code}").status_code == 200
    assert client.get("/api/feedback/survey-links").status_code == 401


def test_contact_is_blanked_after_the_retention_window():
    con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
    con.executescript("CREATE TABLE customer_feedback(id INTEGER PRIMARY KEY, rated_at TEXT, contact TEXT);")
    con.executemany("INSERT INTO customer_feedback(rated_at, contact) VALUES (?,?)",
                    [("2026-05-01", "081"), ("2026-08-20", "082"), ("2026-09-01", None)])
    assert S.purge_contacts(con, today="2026-09-05", days=90) == 1
    assert [r["contact"] for r in con.execute("SELECT contact FROM customer_feedback ORDER BY id")] == [None, "082", None]


def test_codes_avoid_look_alike_characters():
    for _ in range(50):
        c = S._new_code()
        assert len(c) == 8 and not set(c) & set("01ol")
