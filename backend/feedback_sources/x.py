# -*- coding: utf-8 -*-
"""X (Twitter) — public posts that mention the brand, through the official
API v2 recent search. Needs the **Basic tier or above** (developer.x.com; the
Free tier is write-only, so search answers 402/403 there) — a bearer token in
the config is all the setup.

Config (backend/.feedback_config.json → "x"):
    {"bearer_token": "AAAA…", "keywords": ["โอชิเน", "oshinei"],
     "query": "(optional, replaces the keywords)", "lang": "th"}
Rows are kind "mention": no rating, the branch detected from the text on the
server. Recent search covers the last 7 days, so a nightly run never misses a
post; replies are not sent through the API (posting needs user-context OAuth).
"""
import datetime
import hashlib
import urllib.parse

from feedback_sources.google_gbp import thai_date

API = "https://api.x.com/2"
SOURCE = "X"
DEFAULT_KEYWORDS = ["โอชิเน", "oshinei"]


def _today():
    return datetime.date.today()


def build_query(keywords, lang: str = "") -> str:
    terms = [f'"{k.strip()}"' if " " in k.strip() else k.strip() for k in keywords if k and k.strip()]
    q = "(" + " OR ".join(terms) + ") -is:retweet"
    if lang:
        q += f" lang:{lang}"
    return q


def _err(status, j) -> str:
    if status in (402, 403):
        return f"X API refused (HTTP {status}) — search needs the Basic tier (paid); the Free tier is write-only"
    if isinstance(j, dict):
        errs = j.get("errors") or []
        detail = j.get("detail") or j.get("title") or (errs[0].get("message") if errs and isinstance(errs[0], dict) else "")
        return f"X API failed (HTTP {status}): {detail or j}"
    return f"X API failed (HTTP {status}): {str(j)[:200]}"


def tweet_to_row(t: dict, users: dict):
    text = (t.get("text") or "").strip()
    tid = str(t.get("id") or "")
    if not text or not tid:
        return None
    uid = str(t.get("author_id") or "")
    uname = ((users or {}).get(uid) or {}).get("username") or ""
    return {"date": thai_date(t.get("created_at")), "branch": "", "source": SOURCE, "rating": None, "text": text,
            "external_id": f"tweet:{tid}", "url": f"https://x.com/{uname or 'i/web'}/status/{tid}",
            "author": hashlib.sha1(uid.encode("utf-8")).hexdigest()[:16] if uid else "", "reply": "", "replied_at": "",
            "location": "", "kind": "mention", "detect_branch": True}


def fetch_rows(cfg: dict, http, since: str = "", log=None) -> tuple:
    """Recent search, paged → (rows, {queries: [q], fetched})."""
    log = log or (lambda *a: None)
    bearer = (cfg.get("bearer_token") or "").strip()
    if not bearer:
        raise RuntimeError("x config lacks bearer_token — an X API Basic-tier app (developer.x.com)")
    q = cfg.get("query") or build_query(cfg.get("keywords") or DEFAULT_KEYWORDS, cfg.get("lang") or "")
    params = {"query": q, "max_results": 100, "tweet.fields": "created_at,lang,public_metrics,author_id",
              "expansions": "author_id", "user.fields": "username"}
    if since:
        # recent search reaches back 7 days at most; an older start_time is a 400
        floor = _today() - datetime.timedelta(days=6)
        start = max(datetime.date.fromisoformat(since), floor)
        params["start_time"] = f"{start.isoformat()}T00:00:00Z"
    rows, token, pages = [], None, 0
    while pages < 10:
        p = dict(params)
        if token:
            p["next_token"] = token
        status, j = http("GET", f"{API}/tweets/search/recent?" + urllib.parse.urlencode(p),
                         {"Authorization": f"Bearer {bearer}"}, None)
        if status >= 400 or not isinstance(j, dict):
            raise RuntimeError(_err(status, j))
        users = {str(u.get("id")): u for u in ((j.get("includes") or {}).get("users") or [])}
        for t in j.get("data") or []:
            row = tweet_to_row(t, users)
            if row:
                rows.append(row)
        token = (j.get("meta") or {}).get("next_token")
        pages += 1
        if not token:
            break
    log(f"  {q[:50]:<50} {len(rows):>4} post(s), {pages} page(s)")
    return rows, {"queries": [{"query": q, "posts": len(rows), "pages": pages}], "fetched": len(rows)}


__all__ = ["build_query", "tweet_to_row", "fetch_rows", "SOURCE"]
