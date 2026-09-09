# -*- coding: utf-8 -*-
"""Instagram — a Business/Creator account linked to one of the Facebook pages
(same Meta app, same Page token): comments under the account's own posts
(+ replies), posts by others that TAG the account, and — through the webhook
the dashboard exposes — posts and comments that @mention it.

Config (backend/.feedback_config.json → "instagram"):
    {"accounts": [{"ig_user_id": "17841…", "username": "oshinei.official", "page_id": "123",
                   "token": "<Page token>", "branch": "", "comments": true, "tags": true, "max_media": 30,
                   "inbox": false}]}                  # inbox = Instagram DMs (instagram_manage_messages)
`pages facebook --user-token … --save` fills the accounts from each page's
instagram_business_account. Permissions: instagram_basic,
instagram_manage_comments (+ pages_show_list, pages_read_engagement).

Mentions have no pull endpoint: Meta pushes them (webhook field `mentions`)
to POST /api/feedback/webhook/meta, the dashboard keeps the ids, and the
nightly run resolves them here (mentioned_media / mentioned_comment) — so
mentions exist from the day the webhook is switched on, with no history.
"""
import hashlib
import json
import urllib.parse

from feedback_sources.facebook import DEFAULT_VERSION, GRAPH, _check, _paged, fetch_inbox
from feedback_sources.google_gbp import thai_date

SOURCE = "Instagram"
REPLY_MAX = 2200


def _hash(s) -> str:
    s = str(s or "")
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16] if s else ""


def _caption(m: dict) -> str:
    return (m.get("caption") or "").strip().replace("\n", " ")


def comment_to_row(c: dict, media: dict, ig_user_id: str, branch: str = "", own_username: str = ""):
    """A comment under one of the account's posts (the account's own replies skipped)."""
    text, cid = (c.get("text") or "").strip(), str(c.get("id") or "")
    uname = c.get("username") or (c.get("from") or {}).get("username") or ""
    if not text or not cid or (own_username and uname.lower() == own_username.lower()):
        return None
    return {"date": thai_date(c.get("timestamp")), "branch": branch or "", "source": SOURCE, "rating": None, "text": text,
            "external_id": f"comment:{cid}", "url": media.get("permalink") or "",
            "author": _hash(uname or (c.get("from") or {}).get("id")), "reply": "", "replied_at": "",
            "location": _caption(media)[:80], "kind": "comment", "detect_branch": not branch,
            "ref": f"{ig_user_id}/{cid}", "sentiment_hint": None}          # account/comment — what a reply is posted under


def tagged_to_row(m: dict, branch: str = ""):
    """A post by someone else that tags the account (/{ig-user-id}/tags)."""
    mid = str(m.get("id") or "")
    if not mid:
        return None
    text = _caption(m) or f"แท็กบัญชีในโพสต์ ({m.get('media_type') or 'IMAGE'})"
    return {"date": thai_date(m.get("timestamp")), "branch": branch or "", "source": SOURCE, "rating": None, "text": text,
            "external_id": f"tagged:{mid}", "url": m.get("permalink") or "", "author": _hash(m.get("username")),
            "reply": "", "replied_at": "", "location": "แท็กบัญชี", "kind": "mention", "detect_branch": not branch,
            "ref": "", "sentiment_hint": None}


def mention_media_to_row(m: dict, branch: str = ""):
    mid = str(m.get("id") or "")
    if not mid:
        return None
    text = _caption(m) or f"@mention ในโพสต์ ({m.get('media_type') or 'IMAGE'})"
    return {"date": thai_date(m.get("timestamp")), "branch": branch or "", "source": SOURCE, "rating": None, "text": text,
            "external_id": f"mention:{mid}", "url": m.get("permalink") or "", "author": _hash(m.get("username")),
            "reply": "", "replied_at": "", "location": "@mention ในโพสต์", "kind": "mention", "detect_branch": not branch,
            "ref": "", "sentiment_hint": None}


def mention_comment_to_row(c: dict, branch: str = ""):
    cid, text = str(c.get("id") or ""), (c.get("text") or "").strip()
    if not cid or not text:
        return None
    return {"date": thai_date(c.get("timestamp")), "branch": branch or "", "source": SOURCE, "rating": None, "text": text,
            "external_id": f"mentionc:{cid}", "url": (c.get("media") or {}).get("permalink") or "",
            "author": _hash(c.get("username")), "reply": "", "replied_at": "", "location": "@mention ในคอมเมนต์",
            "kind": "mention", "detect_branch": not branch, "ref": "", "sentiment_hint": None}


def fetch_account(acc: dict, http, since: str = "", api_version: str = DEFAULT_VERSION) -> tuple:
    """Comments under the account's latest posts (acc['max_media'], default 30)
    and the posts that tag it → (rows, counts)."""
    ig, token = str(acc.get("ig_user_id") or ""), acc.get("token") or ""
    if not ig or not token:
        raise RuntimeError("instagram account config needs ig_user_id and token")
    branch, own, v = acc.get("branch") or "", acc.get("username") or "", api_version
    rows = []
    m = {"ig_user_id": ig, "username": own, "branch": branch, "media": 0, "comments": 0, "tagged": 0,
         "inbox_threads": 0, "inbox": 0}
    if acc.get("comments", True):
        max_media = int(acc.get("max_media") or 30)
        q = {"fields": "id,caption,timestamp,permalink,media_type,comments_count", "limit": min(50, max_media), "access_token": token}
        for media in _paged(f"{GRAPH}/{v}/{ig}/media?" + urllib.parse.urlencode(q), http, "media", max_pages=10):
            if m["media"] >= max_media:
                break
            m["media"] += 1
            if "comments_count" in media and not int(media.get("comments_count") or 0):
                continue                                          # nothing under it: no call
            cq = {"fields": "id,text,timestamp,username,from", "limit": 50, "access_token": token}
            for c in _paged(f"{GRAPH}/{v}/{media['id']}/comments?" + urllib.parse.urlencode(cq), http, "comments", max_pages=4):
                if since and thai_date(c.get("timestamp")) < since:
                    continue
                row = comment_to_row(c, media, ig, branch, own)
                if row:
                    rows.append(row); m["comments"] += 1
    if acc.get("tags", True):
        q = {"fields": "id,caption,timestamp,permalink,username,media_type", "limit": 50, "access_token": token}
        for media in _paged(f"{GRAPH}/{v}/{ig}/tags?" + urllib.parse.urlencode(q), http, "tags", max_pages=10):
            if since and thai_date(media.get("timestamp")) < since:
                continue
            row = tagged_to_row(media, branch)
            if row:
                rows.append(row); m["tagged"] += 1
    if acc.get("inbox") and acc.get("page_id"):
        # Instagram DMs ride the linked page's conversations edge (instagram_manage_messages)
        got, n = fetch_inbox(str(acc["page_id"]), token, http, since, v, "instagram", (ig, acc["page_id"]), SOURCE,
                             branch, int(acc.get("inbox_min_chars") or 8))
        rows.extend(got)
        m["inbox_threads"], m["inbox"] = n, len(got)
    return rows, m


def fetch_rows(cfg: dict, http, since: str = "", log=None) -> tuple:
    """Every configured account → (rows, {accounts: [...], fetched})."""
    log = log or (lambda *a: None)
    accounts = cfg.get("accounts") or []
    if not accounts:
        raise RuntimeError("instagram config has no accounts — run: python backend/fetch_feedback.py pages facebook --user-token … --save")
    v = cfg.get("api_version") or DEFAULT_VERSION
    rows, meta = [], {"accounts": [], "fetched": 0}
    for acc in accounts:
        got, m = fetch_account(acc, http, since, v)
        rows.extend(got)
        meta["accounts"].append(m)
        meta["fetched"] += len(got)
        log(f"  @{(m['username'] or m['ig_user_id']):<30} → {m['branch']:<16} {m['media']:>3} post(s) {m['comments']:>4} comment(s) {m['tagged']:>3} tag(s)"
            + (f" {m['inbox']:>3} DM day(s) from {m['inbox_threads']} thread(s)" if m.get("inbox_threads") else ""))
    return rows, meta


def resolve_mentions(cfg: dict, events: list, http, log=None) -> tuple:
    """Webhook events ({id, object_id = the ig user, payload {media_id} or
    {comment_id}}) → rows. Returns (rows, done_ids, [(id, reason)] failed)."""
    accounts = {str(a.get("ig_user_id")): a for a in cfg.get("accounts") or []}
    v = cfg.get("api_version") or DEFAULT_VERSION
    rows, done, failed = [], [], []
    for ev in events:
        acc = accounts.get(str(ev.get("object_id") or ""))
        if not acc:
            failed.append((ev.get("id"), "no account for this webhook object"))
            continue
        val = ev.get("payload") or {}
        if isinstance(val, str):
            try:
                val = json.loads(val or "{}")
            except ValueError:
                val = {}
        ig, token, branch = str(acc.get("ig_user_id")), acc.get("token") or "", acc.get("branch") or ""
        try:
            row = None
            if val.get("comment_id"):
                f = (f"mentioned_comment.comment_id({val['comment_id']})"
                     "{id,text,timestamp,username,media{id,permalink,caption}}")
                status, j = http("GET", f"{GRAPH}/{v}/{ig}?" + urllib.parse.urlencode({"fields": f, "access_token": token}), {}, None)
                _check(status, j, "mentioned comment")
                row = mention_comment_to_row((j or {}).get("mentioned_comment") or {}, branch)
            elif val.get("media_id"):
                f = f"mentioned_media.media_id({val['media_id']}){{id,caption,timestamp,permalink,username,media_type}}"
                status, j = http("GET", f"{GRAPH}/{v}/{ig}?" + urllib.parse.urlencode({"fields": f, "access_token": token}), {}, None)
                _check(status, j, "mentioned media")
                row = mention_media_to_row((j or {}).get("mentioned_media") or {}, branch)
            if row:
                rows.append(row)
            done.append(ev.get("id"))
        except Exception as e:
            failed.append((ev.get("id"), str(e)[:200]))
    return rows, done, failed


def reply_to_comment(token: str, comment_id: str, text: str, http, api_version: str = DEFAULT_VERSION) -> dict:
    """The account's reply under a comment on its own post (instagram_manage_comments)."""
    if not comment_id:
        raise RuntimeError("no comment id to reply under")
    body = urllib.parse.urlencode({"message": (text or "").strip()[:REPLY_MAX], "access_token": token}).encode("utf-8")
    status, j = http("POST", f"{GRAPH}/{api_version}/{comment_id}/replies",
                     {"Content-Type": "application/x-www-form-urlencoded"}, body)
    _check(status, j, "comment reply")
    return j or {}


__all__ = ["comment_to_row", "tagged_to_row", "mention_media_to_row", "mention_comment_to_row", "fetch_account",
           "fetch_rows", "resolve_mentions", "reply_to_comment", "SOURCE", "REPLY_MAX"]
