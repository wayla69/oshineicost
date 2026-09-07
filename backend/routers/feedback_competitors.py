# -*- coding: utf-8 -*-
"""🏁 เทียบคู่แข่งรอบสาขา — E6 of the expansion plan. The monthly Google Places
snapshot (feedback_sources/places.py, posted by the fetcher) and where each
branch stands among the restaurants around it: its own rating and count, the
competitors' median and best, its rank, and the move since the last snapshot.
  POST /api/feedback/competitors          {month, rows: [...]} — the fetcher's snapshot (replaces the month)
  GET  /api/feedback/competitors?month=&branch=    the league per branch (+ the branch's list)
  GET  /api/feedback/competitors/status   the latest snapshot month (the fetcher asks before running)
  GET  /api/feedback/competitors/settings          the search radius + keywords in force
  POST /api/feedback/competitors/settings          {radius_m?, keywords?} — Settings-editable, the
                                                     fetcher's api_key stays in .feedback_config.json
Table feedback_competitor — preserved across the DB push by the "feedback" prefix; the radius/keyword
override lives in app_settings (also preserved) under key "feedback_places".
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from feedback_host import get_db, require_write_token, scope_branch
from feedback_sources.places import DEFAULT_KEYWORDS, RADIUS_M
from routers.feedback import _ensure as _ensure_feedback, _thai_now, _thai_today

router = APIRouter()

DDL = """
CREATE TABLE IF NOT EXISTS feedback_competitor(
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  month         TEXT NOT NULL,
  branch_name   TEXT NOT NULL,
  place_id      TEXT,
  name          TEXT,
  is_self       INTEGER DEFAULT 0,
  rating        REAL,
  ratings_total INTEGER,
  price_level   INTEGER,
  distance_m    INTEGER,
  keyword       TEXT,
  address       TEXT,
  captured_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_fc_month_branch ON feedback_competitor(month, branch_name);
"""


def _ensure(con):
    _ensure_feedback(con)
    con.executescript(DDL)


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def get_places_settings(con) -> dict:
    """{"radius_m": int, "keywords": [str,...]} overrides, or {} keys omitted
    when unset — the connector (feedback_sources/places.py) falls back to its
    own RADIUS_M/DEFAULT_KEYWORDS. The fetcher reads this through the sink
    (ApiSink/DbSink.places_settings()) and merges it over .feedback_config.json's
    "places" section, so the api_key stays file-only but radius/keywords are
    Settings-editable without touching that file."""
    con.execute("CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    row = con.execute("SELECT value FROM app_settings WHERE key='feedback_places'").fetchone()
    if not row or not row[0]:
        return {}
    try:
        cur = json.loads(row[0])
        return cur if isinstance(cur, dict) else {}
    except (TypeError, ValueError):
        return {}


def set_places_settings(con, radius_m=None, keywords=None) -> dict:
    cur = get_places_settings(con)
    if radius_m is not None:
        cur["radius_m"] = int(radius_m)
    if keywords is not None:
        cur["keywords"] = [str(k).strip() for k in keywords if str(k).strip()][:12]
    con.execute("CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES('feedback_places', ?)",
               (json.dumps(cur, ensure_ascii=False),))
    return cur


def latest_month(con) -> Optional[str]:
    _ensure(con)
    r = con.execute("SELECT MAX(month) FROM feedback_competitor").fetchone()
    return r[0] if r and r[0] else None


def store_snapshot(con, month: str, rows: list) -> int:
    """Replace the month's snapshot with these rows."""
    _ensure(con)
    con.execute("DELETE FROM feedback_competitor WHERE month=?", (month,))
    now = _thai_now()
    n = 0
    for r in rows:
        if not r.get("branch") or not r.get("name"):
            continue
        con.execute("INSERT INTO feedback_competitor(month, branch_name, place_id, name, is_self, rating, ratings_total, price_level, "
                    "distance_m, keyword, address, captured_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (month, str(r["branch"])[:80], str(r.get("place_id") or "")[:120], str(r["name"])[:160], 1 if r.get("is_self") else 0,
                     float(r["rating"]) if r.get("rating") is not None else None, int(r.get("ratings_total") or 0),
                     int(r["price_level"]) if r.get("price_level") is not None else None, int(r.get("distance_m") or 0),
                     str(r.get("keyword") or "")[:60], str(r.get("address") or "")[:200], now))
        n += 1
    return n


def league(con, month: Optional[str] = None, branch: Optional[str] = None) -> dict:
    _ensure(con)
    month = month or latest_month(con)
    if not month:
        return {"month": None, "prev_month": None, "branches": [], "available": False}
    prev = con.execute("SELECT MAX(month) FROM feedback_competitor WHERE month < ?", (month,)).fetchone()[0]
    w, p = ["month = ?"], [month]
    if branch:
        w.append("branch_name = ?"); p.append(branch)
    rows = [dict(r) for r in con.execute(f"SELECT * FROM feedback_competitor WHERE {' AND '.join(w)} ORDER BY branch_name, is_self DESC, ratings_total DESC", p)]
    prev_rows = {}
    if prev:
        for r in con.execute("SELECT branch_name, is_self, rating, ratings_total, name FROM feedback_competitor WHERE month = ?", (prev,)):
            prev_rows.setdefault(r["branch_name"], []).append(dict(r))
    by_b = {}
    for r in rows:
        by_b.setdefault(r["branch_name"], []).append(r)
    out = []
    for bname, rs in by_b.items():
        me = next((r for r in rs if r["is_self"]), None)
        comps = [r for r in rs if not r["is_self"]]
        rated = [c for c in comps if c["rating"] is not None and (c["ratings_total"] or 0) >= 20]
        med = _median([c["rating"] for c in rated])
        best = max(rated, key=lambda c: (c["rating"], c["ratings_total"])) if rated else None
        rank = None
        if me and me["rating"] is not None and rated:
            rank = 1 + sum(1 for c in rated if (c["rating"], c["ratings_total"]) > (me["rating"], me["ratings_total"] or 0))
        pm = next((r for r in prev_rows.get(bname, []) if r["is_self"]), None)
        p_rated = [r["rating"] for r in prev_rows.get(bname, []) if not r["is_self"] and r["rating"] is not None and (r["ratings_total"] or 0) >= 20]
        out.append({"branch": bname, "self": {"name": me["name"], "rating": me["rating"], "ratings_total": me["ratings_total"],
                                              "price_level": me["price_level"]} if me else None,
                    "competitors": len(comps), "rated_competitors": len(rated), "median_rating": med,
                    "best": {"name": best["name"], "rating": best["rating"], "ratings_total": best["ratings_total"], "distance_m": best["distance_m"]} if best else None,
                    "rank": rank, "of": len(rated) + (1 if me else 0),
                    "gap": round(me["rating"] - med, 2) if (me and me["rating"] is not None and med is not None) else None,
                    "prev": {"rating": pm["rating"], "ratings_total": pm["ratings_total"], "median_rating": _median(p_rated)} if pm else None,
                    "list": [{"name": c["name"], "rating": c["rating"], "ratings_total": c["ratings_total"], "price_level": c["price_level"],
                              "distance_m": c["distance_m"], "keyword": c["keyword"]} for c in comps[:15]] if branch else []})
    out.sort(key=lambda b: ((b["gap"] if b["gap"] is not None else 9), b["branch"]))
    return {"month": month, "prev_month": prev, "branches": out, "available": True}


@router.get("/api/feedback/competitors/status")
def competitors_status():
    with get_db() as con:
        return {"latest_month": latest_month(con)}


@router.get("/api/feedback/competitors")
def competitors_get(request: Request, month: Optional[str] = Query(None), branch: Optional[str] = Query(None)):
    scope = scope_branch(request)
    if scope:
        branch = scope
    with get_db() as con:
        return league(con, (month or "")[:7] or None, branch)


@router.get("/api/feedback/competitors/settings")
def competitors_settings_get():
    with get_db() as con:
        cur = get_places_settings(con)
    return {"radius_m": cur.get("radius_m", RADIUS_M), "keywords": cur.get("keywords") or list(DEFAULT_KEYWORDS),
            "radius_m_default": RADIUS_M, "keywords_default": list(DEFAULT_KEYWORDS)}


@router.post("/api/feedback/competitors/settings", dependencies=[Depends(require_write_token)])
async def competitors_settings_post(request: Request):
    body = await request.json()
    radius_m, keywords = body.get("radius_m"), body.get("keywords")
    if radius_m is not None and not (500 <= int(radius_m) <= 10000):
        raise HTTPException(400, "radius_m must be 500–10000")
    if keywords is not None and (not isinstance(keywords, list) or not keywords):
        raise HTTPException(400, "keywords must be a non-empty list")
    with get_db() as con:
        cur = set_places_settings(con, radius_m, keywords)
        con.commit()
    return {"radius_m": cur.get("radius_m", RADIUS_M), "keywords": cur.get("keywords") or list(DEFAULT_KEYWORDS)}


@router.post("/api/feedback/competitors", dependencies=[Depends(require_write_token)])
async def competitors_post(request: Request):
    body = await request.json()
    month = str(body.get("month") or _thai_today()[:7])[:7]
    rows = body.get("rows") or []
    if not isinstance(rows, list):
        raise HTTPException(400, "rows")
    with get_db() as con:
        n = store_snapshot(con, month, rows)
        con.commit()
    return {"month": month, "stored": n}


__all__ = ["router", "league", "store_snapshot", "latest_month", "get_places_settings", "set_places_settings"]
