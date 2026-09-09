# -*- coding: utf-8 -*-
"""YouTube — videos that mention the brand (title / description) and, when
enabled, the top comments under them, through the YouTube Data API v3 with a
plain API key (free quota: 10,000 units a day; a search costs 100, a comment
page 1, so a nightly run with a few keywords uses well under 1,000).

Config (backend/.feedback_config.json → "youtube"):
    {"api_key": "AIza…", "keywords": ["โอชิเน", "oshinei buffet"],
     "comments": true, "max_results": 25, "max_comments": 50}
Videos are kind "mention" (the channel title as location), comments kind
"comment"; the branch is detected from the text on the server. Replies are
not sent (they would need the brand channel's OAuth).
"""
import hashlib
import re
import urllib.parse

from feedback_sources.google_gbp import thai_date

API = "https://www.googleapis.com/youtube/v3"
SOURCE = "YouTube"
DEFAULT_KEYWORDS = ["โอชิเน"]


def _hash(s: str) -> str:
    return hashlib.sha1((s or "").encode("utf-8")).hexdigest()[:16] if s else ""


def _clean(s) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _err(status, j) -> str:
    e = j.get("error") if isinstance(j, dict) else None
    if isinstance(e, dict):
        reason = ((e.get("errors") or [{}])[0]).get("reason") or e.get("status") or ""
        return f"YouTube API failed (HTTP {status}, {reason}): {e.get('message')}"
    return f"YouTube API failed (HTTP {status}): {str(j)[:200]}"


def video_to_row(vid: str, sn: dict):
    title, desc = _clean(sn.get("title")), _clean(sn.get("description"))[:500]
    text = title + (f"\n{desc}" if desc else "")
    if not text or not vid:
        return None
    return {"date": thai_date(sn.get("publishedAt") or sn.get("publishTime")), "branch": "", "source": SOURCE,
            "rating": None, "text": text, "external_id": f"video:{vid}", "url": f"https://www.youtube.com/watch?v={vid}",
            "author": _hash(sn.get("channelId") or ""), "reply": "", "replied_at": "",
            "location": _clean(sn.get("channelTitle"))[:80], "kind": "mention", "detect_branch": True}


def list_comments(api_key: str, vid: str, http, max_comments: int = 50) -> list:
    """Top-level comments of one video as rows; a video with comments disabled
    (HTTP 403 commentsDisabled) simply yields none."""
    params = {"part": "snippet", "videoId": vid, "maxResults": max(1, min(int(max_comments), 100)),
              "order": "relevance", "textFormat": "plainText", "key": api_key}
    status, j = http("GET", f"{API}/commentThreads?" + urllib.parse.urlencode(params), {}, None)
    if status >= 400 or not isinstance(j, dict):
        return []
    out = []
    for th in j.get("items") or []:
        top = ((th.get("snippet") or {}).get("topLevelComment") or {})
        s, cid = top.get("snippet") or {}, top.get("id")
        text = _clean(s.get("textOriginal") or s.get("textDisplay"))
        if not text or not cid:
            continue
        ach = s.get("authorChannelId")
        ach = ach.get("value") if isinstance(ach, dict) else str(ach or "")
        out.append({"date": thai_date(s.get("publishedAt")), "branch": "", "source": SOURCE, "rating": None, "text": text,
                    "external_id": f"comment:{cid}", "url": f"https://www.youtube.com/watch?v={vid}&lc={cid}",
                    "author": _hash(ach), "reply": "", "replied_at": "", "location": "", "kind": "comment",
                    "detect_branch": True})
    return out


def fetch_rows(cfg: dict, http, since: str = "", log=None) -> tuple:
    """search.list per keyword (+ commentThreads per video) → (rows, {queries, fetched})."""
    log = log or (lambda *a: None)
    key = (cfg.get("api_key") or "").strip()
    if not key:
        raise RuntimeError("youtube config lacks api_key — Google Cloud → APIs → YouTube Data API v3 → credentials → API key")
    keywords = cfg.get("keywords") or DEFAULT_KEYWORDS
    rows, meta, seen = [], {"queries": [], "fetched": 0}, set()
    for kw in keywords:
        params = {"part": "snippet", "q": kw, "type": "video", "order": "date",
                  "maxResults": max(1, min(int(cfg.get("max_results") or 25), 50)),
                  "regionCode": "TH", "relevanceLanguage": "th", "key": key}
        if since:
            params["publishedAfter"] = f"{since}T00:00:00Z"
        n_v = n_c = pages = 0
        token = None
        while pages < max(1, int(cfg.get("max_pages") or 2)):        # 100 quota units per page
            p = dict(params)
            if token:
                p["pageToken"] = token
            status, j = http("GET", f"{API}/search?" + urllib.parse.urlencode(p), {}, None)
            if status >= 400 or not isinstance(j, dict):
                raise RuntimeError(_err(status, j))
            pages += 1
            for it in j.get("items") or []:
                vid = (it.get("id") or {}).get("videoId")
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                row = video_to_row(vid, it.get("snippet") or {})
                if row:
                    rows.append(row)
                    n_v += 1
                if cfg.get("comments"):
                    got = list_comments(key, vid, http, int(cfg.get("max_comments") or 50))
                    rows.extend(got)
                    n_c += len(got)
            token = j.get("nextPageToken")
            if not token:
                break
        meta["queries"].append({"keyword": kw, "videos": n_v, "comments": n_c, "pages": pages})
        log(f"  {kw[:40]:<40} {n_v:>4} video(s) {n_c:>4} comment(s)")
    meta["fetched"] = len(rows)
    return rows, meta


__all__ = ["video_to_row", "list_comments", "fetch_rows", "SOURCE"]
