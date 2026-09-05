# -*- coding: utf-8 -*-
"""Facebook Pages — recommendations (what replaced star reviews in 2018) and,
per page when enabled, comments on the page's own posts.

Config (backend/.feedback_config.json → "facebook"):
    {"api_version": "v21.0",
     "pages": [{"id": "1234567890", "name": "Oshinei ขอนแก่น", "branch": "ขอนแก่น",
                "token": "EAAB… (Page access token)", "comments": false}]}
The token needs pages_show_list + pages_read_user_content (+ pages_read_engagement
for comments); a Page token derived from a long-lived user token does not
expire. `pages facebook --user-token …` in fetch_feedback.py lists the pages a
user token manages, with their Page tokens, and can write them into the config.

A recommendation looks like:
    {"created_time": "2026-08-30T11:02:03+0000", "recommendation_type": "positive",
     "review_text": "อร่อยมาก", "has_review": true, "reviewer": {"name": "…", "id": "…"},
     "open_graph_story": {"id": "…"}}            # older entries carry "rating": 1–5
Positive/negative with no text still counts (sentiment_hint carries the verdict).
"""
import hashlib
import urllib.parse

from feedback_sources.google_gbp import thai_date

GRAPH = "https://graph.facebook.com"
DEFAULT_VERSION = "v21.0"
SOURCE = "Facebook"


def _check(status, body, what):
    if status >= 400:
        err = body.get("error") if isinstance(body, dict) else body
        if isinstance(err, dict):
            err = f"{err.get('type') or err.get('code')}: {err.get('message')}"
        raise RuntimeError(f"Facebook {what} failed (HTTP {status}): {err}")


def _paged(url, http, what, max_pages=100):
    """Follow Graph API cursor paging (`paging.next`) and yield every item."""
    pages = 0
    while url and pages < max_pages:
        status, j = http("GET", url, {}, None)
        _check(status, j, what)
        for item in (j or {}).get("data") or []:
            yield item
        url = ((j or {}).get("paging") or {}).get("next")
        pages += 1


def _hash(s: str) -> str:
    return hashlib.sha1((s or "").encode("utf-8")).hexdigest()[:16]


def rating_to_row(item: dict, page_id: str, branch: str = "", page_name: str = ""):
    """One recommendation/rating → one upload row (None when empty)."""
    if not isinstance(item, dict):
        return None
    text = (item.get("review_text") or "").strip()
    rating = item.get("rating")
    rec = (item.get("recommendation_type") or "").lower()
    if rating is None and not text and rec not in ("positive", "negative"):
        return None
    reviewer = item.get("reviewer") or {}
    story = (item.get("open_graph_story") or {}).get("id")
    ext = story or hashlib.sha1(
        f"{page_id}|{item.get('created_time')}|{reviewer.get('id') or reviewer.get('name')}|{text}".encode("utf-8")
    ).hexdigest()[:24]
    hint = "pos" if rec == "positive" else ("neg" if rec == "negative" else None)
    if rating is None and not text:
        text = "👍 แนะนำ" if hint == "pos" else "👎 ไม่แนะนำ"
    return {"date": thai_date(item.get("created_time")), "branch": branch or "", "source": SOURCE,
            "rating": float(rating) if rating is not None else None, "text": text,
            "external_id": f"rec:{ext}", "url": f"https://www.facebook.com/{page_id}/reviews",
            "author": _hash(reviewer.get("name") or reviewer.get("id") or ""), "reply": "", "replied_at": "",
            "location": page_name or "", "sentiment_hint": hint}


def comment_to_row(c: dict, post: dict, page_id: str, branch: str = "", page_name: str = ""):
    """A visitor's comment on the page's own post → one row; the page's own
    comments (its replies) are skipped."""
    text = (c.get("message") or "").strip()
    if not text or not c.get("id"):
        return None
    if str((c.get("from") or {}).get("id") or "") == str(page_id):
        return None
    return {"date": thai_date(c.get("created_time")), "branch": branch or "", "source": SOURCE, "rating": None,
            "text": text, "external_id": f"comment:{c['id']}",
            "url": c.get("permalink_url") or post.get("permalink_url") or "",
            "author": _hash((c.get("from") or {}).get("name") or (c.get("from") or {}).get("id") or ""),
            "reply": "", "replied_at": "", "location": page_name or "", "sentiment_hint": None,
            "ref": str(c["id"])}              # the comment id a reply is posted under


def page_for_comment(cfg: dict, comment_id: str, location: str = ""):
    """The configured page a comment belongs to: comment ids are
    '<page_id>_<post>_<comment>' (the page id leads); failing that, the page
    whose name is the row's `location` (the page name the fetcher recorded)."""
    pages = (cfg or {}).get("pages") or []
    pid = str(comment_id or "").split("_", 1)[0]
    for page in pages:
        if str(page.get("id")) == pid:
            return page
    if location:
        for page in pages:
            if (page.get("name") or "").strip() == location.strip():
                return page
    return None


def reply_to_comment(page_token: str, comment_id: str, text: str, http, api_version: str = DEFAULT_VERSION) -> dict:
    """Publish the page's reply under a visitor's comment (pages_manage_engagement).
    Recommendations cannot be answered through the API — those stay manual."""
    if not comment_id:
        raise RuntimeError("no comment id to reply under")
    body = urllib.parse.urlencode({"message": (text or "").strip()[:8000], "access_token": page_token}).encode("utf-8")
    status, j = http("POST", f"{GRAPH}/{api_version}/{comment_id}/comments",
                     {"Content-Type": "application/x-www-form-urlencoded"}, body)
    _check(status, j, "comment reply")
    return j or {}


def fetch_page(page: dict, http, since: str = "", api_version: str = DEFAULT_VERSION) -> tuple:
    """Rows of one page: recommendations (newest first, stopped at `since`) and,
    when page['comments'] is true, comments on posts published since `since`."""
    pid, token = str(page.get("id") or ""), page.get("token") or ""
    if not pid or not token:
        raise RuntimeError("facebook page config needs id and token")
    branch, name = page.get("branch") or "", page.get("name") or ""
    rows, n_rec, n_com = [], 0, 0
    q = {"fields": "created_time,rating,recommendation_type,review_text,has_review,reviewer,open_graph_story",
         "limit": 100, "access_token": token}
    for item in _paged(f"{GRAPH}/{api_version}/{pid}/ratings?" + urllib.parse.urlencode(q), http, "ratings"):
        if since and thai_date(item.get("created_time")) < since:
            break
        row = rating_to_row(item, pid, branch, name)
        if row:
            rows.append(row); n_rec += 1
    if page.get("comments"):
        q = {"fields": "id,created_time,message,permalink_url,comments.limit(100){id,created_time,message,from,permalink_url}",
             "limit": 25, "access_token": token}
        if since:
            q["since"] = since
        for post in _paged(f"{GRAPH}/{api_version}/{pid}/posts?" + urllib.parse.urlencode(q), http, "posts", max_pages=20):
            for c in ((post.get("comments") or {}).get("data") or []):
                row = comment_to_row(c, post, pid, branch, name)
                if row:
                    rows.append(row); n_com += 1
    return rows, {"id": pid, "name": name, "branch": branch, "ratings": n_rec, "comments": n_com}


def fetch_rows(cfg: dict, http, since: str = "", log=None) -> tuple:
    """Every configured page → (rows, {pages: [...], fetched})."""
    log = log or (lambda *a: None)
    pages = cfg.get("pages") or []
    if not pages:
        raise RuntimeError("facebook config has no pages — run: python backend/fetch_feedback.py pages facebook --user-token …")
    version = cfg.get("api_version") or DEFAULT_VERSION
    rows, meta = [], {"pages": [], "fetched": 0}
    for page in pages:
        got, m = fetch_page(page, http, since, version)
        rows.extend(got)
        meta["pages"].append(m)
        meta["fetched"] += len(got)
        log(f"  {m['name'] or m['id']:<40} → {m['branch']:<16} {m['ratings']:>4} recommendation(s) {m['comments']:>4} comment(s)")
    return rows, meta


def extend_user_token(app_id: str, app_secret: str, short_token: str, http, api_version: str = DEFAULT_VERSION) -> str:
    """Short-lived user token (Graph API Explorer) → long-lived (~60 days)."""
    q = {"grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret, "fb_exchange_token": short_token}
    status, j = http("GET", f"{GRAPH}/{api_version}/oauth/access_token?" + urllib.parse.urlencode(q), {}, None)
    _check(status, j, "token exchange")
    return j["access_token"]


def list_pages(user_token: str, http, api_version: str = DEFAULT_VERSION) -> list:
    """[{id, name, access_token}] — the pages the user administers, each with
    its Page token (never expires when the user token was long-lived)."""
    q = {"fields": "id,name,access_token", "limit": 100, "access_token": user_token}
    return [{"id": str(p.get("id") or ""), "name": p.get("name") or "", "token": p.get("access_token") or ""}
            for p in _paged(f"{GRAPH}/{api_version}/me/accounts?" + urllib.parse.urlencode(q), http, "pages")]


__all__ = ["rating_to_row", "comment_to_row", "fetch_page", "fetch_rows", "extend_user_token", "list_pages",
           "page_for_comment", "reply_to_comment", "SOURCE"]
