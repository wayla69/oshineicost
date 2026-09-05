# -*- coding: utf-8 -*-
"""TikTok — comments on the chain's OWN videos, through the TikTok Business
Account API (organic content; a developers.tiktok.com app with the Business
Account product and the scopes user.info.basic, video.list, comment.list,
comment.list.manage — TikTok reviews the app before granting them). Mentions
in other people's videos are not available through any official API.

Config (backend/.feedback_config.json → "tiktok"):
    {"client_key", "client_secret", "access_token", "refresh_token",
     "business_id" (the account's open_id), "branch"?: "", "max_videos"?: 20}
`auth tiktok` in fetch_feedback.py fills the tokens; access tokens last a day,
so every run refreshes first and hands the new tokens back (meta.config_update).

The endpoint paths and field names follow the Business Account API v1.3
reference; they are gathered in the constants below, the one place to adjust
if the approved app is offered a different version. Every response is read
tolerantly: {code: 0, message, data: {...}}.
"""
import hashlib
import json
import urllib.parse

from feedback_sources.google_gbp import thai_date

BUSINESS_API = "https://business-api.tiktok.com/open_api/v1.3"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_PATH = "tt_user/oauth2/token/"
REFRESH_PATH = "tt_user/oauth2/refresh_token/"
VIDEO_LIST_PATH = "business/video/list/"
COMMENT_LIST_PATH = "business/comment/list/"
COMMENT_REPLY_PATH = "business/comment/reply/create/"
SCOPES = "user.info.basic,video.list,comment.list,comment.list.manage"
VIDEO_FIELDS = ["item_id", "create_time", "caption", "share_url", "video_views", "likes", "comments"]
REPLY_MAX = 150
SOURCE = "TikTok"


def _hash(s) -> str:
    s = str(s or "")
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16] if s else ""


def _data(status, j, what) -> dict:
    code = j.get("code") if isinstance(j, dict) else None
    if status >= 400 or code not in (None, 0):
        msg = j.get("message") if isinstance(j, dict) else str(j)[:200]
        raise RuntimeError(f"TikTok {what} failed (HTTP {status}, code {code if code is not None else '?'}): {msg}")
    return (j or {}).get("data") or {} if isinstance(j, dict) else {}


def auth_url(client_key: str, redirect_uri: str, state: str = "") -> str:
    q = {"client_key": client_key, "scope": SCOPES, "response_type": "code", "redirect_uri": redirect_uri}
    if state:
        q["state"] = state
    return AUTH_URL + "?" + urllib.parse.urlencode(q)


def exchange_code(client_key, client_secret, code, redirect_uri, http) -> dict:
    """Authorisation code → {access_token, refresh_token, business_id (open_id)}."""
    status, j = http("POST", f"{BUSINESS_API}/{TOKEN_PATH}", {},
                     {"client_id": client_key, "client_secret": client_secret, "grant_type": "authorization_code",
                      "auth_code": code, "redirect_uri": redirect_uri})
    d = _data(status, j, "code exchange")
    if not d.get("access_token"):
        raise RuntimeError("TikTok returned no access_token")
    return {"access_token": d["access_token"], "refresh_token": d.get("refresh_token") or "",
            "business_id": d.get("open_id") or ""}


def refresh(cfg: dict, http) -> dict:
    """A fresh access token (they last ~24 h). Returns the fields to store back."""
    for k in ("client_key", "client_secret", "refresh_token"):
        if not cfg.get(k):
            raise RuntimeError(f"tiktok config lacks {k} — run: python backend/fetch_feedback.py auth tiktok")
    status, j = http("POST", f"{BUSINESS_API}/{REFRESH_PATH}", {},
                     {"client_id": cfg["client_key"], "client_secret": cfg["client_secret"],
                      "grant_type": "refresh_token", "refresh_token": cfg["refresh_token"]})
    d = _data(status, j, "token refresh")
    out = {"access_token": d.get("access_token") or cfg.get("access_token") or ""}
    if d.get("refresh_token"):
        out["refresh_token"] = d["refresh_token"]
    if d.get("open_id"):
        out["business_id"] = d["open_id"]
    if not out["access_token"]:
        raise RuntimeError("TikTok token refresh returned no access_token")
    return out


def _get(token, path, params, http, what) -> dict:
    status, j = http("GET", f"{BUSINESS_API}/{path}?" + urllib.parse.urlencode(params), {"Access-Token": token}, None)
    return _data(status, j, what)


def list_videos(token, business_id, http, max_videos: int = 20) -> list:
    out, cursor = [], None
    while len(out) < max_videos:
        params = {"business_id": business_id, "fields": json.dumps(VIDEO_FIELDS), "max_count": 20}
        if cursor:
            params["cursor"] = cursor
        d = _get(token, VIDEO_LIST_PATH, params, http, "video list")
        vids = d.get("videos") or d.get("list") or []
        out.extend(vids)
        if not d.get("has_more") or not vids:
            break
        cursor = d.get("cursor")
    return out[:max_videos]


def iter_comments(token, business_id, video_id, http, since: str = "", max_count: int = 30):
    """Public comments of one video, newest first, stopped once a page ends before `since`."""
    cursor = 0
    while True:
        d = _get(token, COMMENT_LIST_PATH,
                 {"business_id": business_id, "video_id": video_id, "max_count": max_count, "cursor": cursor,
                  "status": "PUBLIC", "sort_field": "create_time", "sort_order": "DESC"}, http, "comment list")
        comments = d.get("comments") or []
        for c in comments:
            yield c
        if not d.get("has_more") or not comments:
            return
        if since and thai_date(comments[-1].get("create_time")) < since:
            return
        cursor = d.get("cursor") or (cursor + len(comments))


def comment_to_row(c: dict, video: dict, branch: str = ""):
    """One comment → one row (None for the account's own comments or empty ones)."""
    text = (c.get("text") or c.get("comment") or "").strip()
    if not text or c.get("owner"):
        return None
    cid = str(c.get("comment_id") or c.get("id") or "")
    vid = str(video.get("item_id") or video.get("id") or c.get("video_id") or "")
    if not cid:
        return None
    return {"date": thai_date(c.get("create_time")), "branch": branch or "", "source": SOURCE, "rating": None, "text": text,
            "external_id": f"comment:{cid}", "url": video.get("share_url") or f"https://www.tiktok.com/video/{vid}",
            "author": _hash(c.get("username") or c.get("user_id")), "reply": "", "replied_at": "",
            "location": (video.get("caption") or "")[:80], "kind": "comment", "detect_branch": not branch,
            "ref": f"{vid}/{cid}"}          # what a reply is posted under


def fetch_rows(cfg: dict, http, since: str = "", log=None) -> tuple:
    """Refresh the token, list the account's videos, read their comments →
    (rows, {videos: [...], fetched, config_update: {tokens to store}})."""
    log = log or (lambda *a: None)
    tok = refresh(cfg, http)
    token = tok["access_token"]
    business_id = tok.get("business_id") or cfg.get("business_id") or ""
    if not business_id:
        raise RuntimeError("tiktok config lacks business_id (the account's open_id from auth tiktok)")
    rows, meta = [], {"videos": [], "fetched": 0, "config_update": tok}
    for v in list_videos(token, business_id, http, int(cfg.get("max_videos") or 20)):
        vid = str(v.get("item_id") or v.get("id") or "")
        n = 0
        for c in iter_comments(token, business_id, vid, http, since):
            if since and thai_date(c.get("create_time")) < since:
                continue
            row = comment_to_row(c, v, cfg.get("branch") or "")
            if row:
                rows.append(row)
                n += 1
        meta["videos"].append({"id": vid, "caption": (v.get("caption") or "")[:60], "comments": n})
        meta["fetched"] += n
        log(f"  video {vid:<20} {(v.get('caption') or '')[:30]:<30} {n:>4} comment(s)")
    return rows, meta


def reply_to_comment(token: str, business_id: str, video_id: str, comment_id: str, text: str, http) -> dict:
    """The account's reply under a comment (comment.list.manage). TikTok caps a
    comment at 150 characters."""
    if not (video_id and comment_id):
        raise RuntimeError("no video/comment id to reply under")
    text = (text or "").strip()
    if len(text) > REPLY_MAX:
        raise RuntimeError(f"TikTok replies are limited to {REPLY_MAX} characters (this one is {len(text)})")
    status, j = http("POST", f"{BUSINESS_API}/{COMMENT_REPLY_PATH}", {"Access-Token": token},
                     {"business_id": business_id, "video_id": video_id, "comment_id": comment_id, "text": text})
    return _data(status, j, "comment reply")


__all__ = ["auth_url", "exchange_code", "refresh", "list_videos", "iter_comments", "comment_to_row", "fetch_rows",
           "reply_to_comment", "SOURCE", "SCOPES", "REPLY_MAX"]
