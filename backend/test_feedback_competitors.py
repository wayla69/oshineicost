# -*- coding: utf-8 -*-
"""E6 — the competitors around each branch from Google Places: the connector
(own listing, keyword searches within the radius, dedupe, distance), the
monthly snapshot endpoints and league, the fetcher's once-a-month step."""
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import fetch_feedback as FF  # noqa: E402
from feedback_sources import places as P  # noqa: E402
from test_feedback_sources import _args, _seed_db  # noqa: E402

KK = (16.4322, 102.8236)      # ขอนแก่น


def _wipe(con, table):
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        con.execute(f"DELETE FROM {table}")


@pytest.fixture(autouse=True)
def _clean(request):
    yield
    if "client" not in request.fixturenames:
        return
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    _wipe(con, "feedback_competitor")
    con.commit(); con.close()


def _pl(pid, name, rating, n, lat, lng, price=None):
    return {"id": pid, "displayName": {"text": name}, "rating": rating, "userRatingCount": n,
            "location": {"latitude": lat, "longitude": lng}, "priceLevel": price, "formattedAddress": "ถนน…"}


def fake_http(method, url, headers, body):
    """Text Search: the query decides the answer; a location bias → the neighbourhood."""
    assert method == "POST" and url == P.API and headers["X-Goog-Api-Key"] == "KEY" and "places.rating" in headers["X-Goog-FieldMask"]
    q = body["textQuery"]
    if q.startswith("โอชิเน"):
        if "โคราช" in q:
            return 200, {"places": []}
        return 200, {"places": [_pl("self1", "โอชิเน บุฟเฟ่ต์ ขอนแก่น", 4.3, 812, KK[0], KK[1], "PRICE_LEVEL_MODERATE"),
                                _pl("other", "ร้านอื่น", 4.9, 5, KK[0], KK[1])]}
    assert "locationBias" in body and body["locationBias"]["circle"]["radius"] == 3000.0
    if q == "ชาบู":
        return 200, {"places": [_pl("c1", "ชาบูอิ่มดี", 4.6, 1400, KK[0] + 0.005, KK[1], "PRICE_LEVEL_MODERATE"),
                                _pl("c2", "ชาบูไกล", 4.8, 900, KK[0] + 0.1, KK[1])]}          # 11 km → outside the radius
    if q == "บุฟเฟ่ต์ญี่ปุ่น":
        return 200, {"places": [_pl("c1", "ชาบูอิ่มดี", 4.6, 1400, KK[0] + 0.005, KK[1]),        # duplicate
                                _pl("c3", "ซูชิบุฟเฟ่ต์", 4.1, 60, KK[0], KK[1] + 0.01),
                                _pl("c4", "Oshinei Khon Kaen 2", 4.0, 10, KK[0], KK[1]),          # our own brand
                                _pl("c5", "ร้านใหม่ยังไม่มีรีวิว", None, 0, KK[0], KK[1] + 0.002)]}
    return 200, {"places": []}


def test_connector_snapshot_dedupes_and_measures():
    cfg = {"api_key": "KEY", "keywords": ["ชาบู", "บุฟเฟ่ต์ญี่ปุ่น"], "radius_m": 3000}
    lines = []
    rows, meta = P.snapshot(cfg, fake_http, ["ขอนแก่น", "โคราช1"], lines.append)
    assert meta["fetched"] == 4 and meta["branches"][1]["error"].startswith("own listing not found")
    me = rows[0]
    assert me["is_self"] == 1 and me["name"] == "โอชิเน บุฟเฟ่ต์ ขอนแก่น" and me["rating"] == 4.3 and me["price_level"] == 2 and me["distance_m"] == 0
    comps = [r for r in rows if not r["is_self"]]
    assert [c["place_id"] for c in comps] == ["c1", "c3", "c5"]          # far one dropped, duplicate once, own brand excluded
    assert 500 < comps[0]["distance_m"] < 600 and comps[0]["keyword"] == "ชาบู"
    assert P.is_own("Oshinei Khon Kaen") and not P.is_own("ชาบูอิ่มดี")
    assert round(P.haversine_m(KK[0], KK[1], KK[0] + 0.01, KK[1])) in range(1100, 1115)
    with pytest.raises(RuntimeError):
        P.snapshot({}, fake_http, ["ขอนแก่น"])


def test_snapshot_endpoints_and_league(client, monkeypatch):
    assert client.get("/api/feedback/competitors").json() == {"month": None, "prev_month": None, "branches": [], "available": False}
    assert client.get("/api/feedback/competitors/status").json() == {"latest_month": None}
    rows_july = [{"branch": "BranchA", "place_id": "s", "name": "โอชิเน A", "is_self": 1, "rating": 4.1, "ratings_total": 500},
                 {"branch": "BranchA", "place_id": "c1", "name": "ชาบูอิ่มดี", "rating": 4.6, "ratings_total": 1400, "distance_m": 550, "price_level": 2},
                 {"branch": "BranchA", "place_id": "c3", "name": "ซูชิบุฟเฟ่ต์", "rating": 4.0, "ratings_total": 60, "distance_m": 900},
                 {"branch": "BranchA", "place_id": "c5", "name": "ร้านใหม่", "rating": 5.0, "ratings_total": 3, "distance_m": 200},   # < 20 ratings: not rated
                 {"branch": "BranchB", "place_id": "sb", "name": "โอชิเน B", "is_self": 1, "rating": 4.5, "ratings_total": 200}]
    assert client.post("/api/feedback/competitors", json={"month": "2026-07", "rows": rows_july}).json() == {"month": "2026-07", "stored": 5}
    rows_aug = [dict(r, rating=(4.4 if r.get("is_self") and r["branch"] == "BranchA" else r["rating"]),
                     ratings_total=(560 if r.get("is_self") and r["branch"] == "BranchA" else r["ratings_total"])) for r in rows_july]
    assert client.post("/api/feedback/competitors", json={"month": "2026-08", "rows": rows_aug}).json()["stored"] == 5
    assert client.post("/api/feedback/competitors", json={"month": "2026-08", "rows": rows_aug}).json()["stored"] == 5   # replaces, no duplicates
    assert client.get("/api/feedback/competitors/status").json() == {"latest_month": "2026-08"}
    j = client.get("/api/feedback/competitors").json()
    assert j["month"] == "2026-08" and j["prev_month"] == "2026-07" and [b["branch"] for b in j["branches"]] == ["BranchA", "BranchB"]
    a = j["branches"][0]
    assert a["self"]["rating"] == 4.4 and a["competitors"] == 3 and a["rated_competitors"] == 2 and a["median_rating"] == 4.6
    assert a["best"]["name"] == "ชาบูอิ่มดี" and a["rank"] == 2 and a["of"] == 3 and a["gap"] == -0.2
    assert a["prev"] == {"rating": 4.1, "ratings_total": 500, "median_rating": 4.6} and a["list"] == []
    b = j["branches"][1]
    assert b["competitors"] == 0 and b["rank"] is None and b["gap"] is None
    one = client.get("/api/feedback/competitors", params={"branch": "BranchA", "month": "2026-07"}).json()
    assert one["month"] == "2026-07" and one["prev_month"] is None and len(one["branches"][0]["list"]) == 3
    assert one["branches"][0]["list"][0]["name"] == "ชาบูอิ่มดี" and one["branches"][0]["prev"] is None
    assert client.post("/api/feedback/competitors", json={"month": "2026-09", "rows": "x"}).status_code == 400


def test_fetcher_captures_once_a_month(tmp_path, monkeypatch):
    db = tmp_path / "local.db"; _seed_db(db)
    sink = FF.DbSink(db)
    cfg = {"places": {"api_key": "KEY", "keywords": ["ชาบู"], "only": ["ขอนแก่น"]}}
    lines = []
    monkeypatch.setattr(FF, "_this_month", lambda: "2026-09")
    assert FF._places_step(_args(), {}, sink, fake_http, lines.append) is False            # no key → nothing
    assert FF._places_step(_args(), cfg, sink, fake_http, lines.append) is True             # first run of the month
    assert sink.competitors_month() == "2026-09"
    assert FF._places_step(_args(), cfg, sink, fake_http, lines.append) is False            # already captured
    assert FF._places_step(_args(places=True), cfg, sink, fake_http, lines.append) is True  # forced
    assert FF._places_step(_args(dry_run=True), cfg, sink, fake_http, lines.append) is False
    con = sqlite3.connect(str(db)); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT branch_name, is_self, name FROM feedback_competitor ORDER BY is_self DESC, name").fetchall()
    assert [(r["branch_name"], r["is_self"]) for r in rows] == [("ขอนแก่น", 1), ("ขอนแก่น", 0)]
    assert con.execute("SELECT source, status FROM feedback_sync_log ORDER BY id DESC LIMIT 1").fetchone()[0] == P.SOURCE
    con.close()
    assert any("places:" in ln for ln in lines) and sink.branches() == ["ขอนแก่น", "โคราช1"]
