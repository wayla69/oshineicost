# -*- coding: utf-8 -*-
"""Google Business Profile — reviews of the chain's own Maps listings.

Two ways in, one row shape (see feedback_sources/__init__.py):
  • the API, nightly (fetch_feedback.py): OAuth refresh token → access token →
    accounts → locations → reviews (newest update first, stopped at `since`);
  • a Google Takeout archive of the "Google Business Profile" product uploaded
    in the tab — location-*/reviews.json (+ data.json for the listing title) —
    the zero-setup path while the API access request is pending.

Reviews the API returns (and Takeout writes) look like:
    {"reviewId": "AbC…", "reviewer": {"displayName": "…"}, "starRating": "FIVE",
     "comment": "(Translated by Google) Great\n\n(Original) อร่อย",
     "createTime": "2026-08-30T11:02:03.123Z", "updateTime": "…",
     "reviewReply": {"comment": "ขอบคุณค่ะ", "updateTime": "…"}}
A star-only review has no `comment`; it is still kept (the rating is the
feedback) with an empty text.
"""
import datetime
import email.utils
import hashlib
import io
import json
import posixpath
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ACCOUNTS_API = "https://mybusinessaccountmanagement.googleapis.com/v1"
LOCATIONS_API = "https://mybusinessbusinessinformation.googleapis.com/v1"
REVIEWS_API = "https://mybusiness.googleapis.com/v4"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPE = "https://www.googleapis.com/auth/business.manage"
SOURCE = "Google"
STARS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}
_TH = datetime.timezone(datetime.timedelta(hours=7))


# ── transport ────────────────────────────────────────────────────────────────
def urllib_http(method, url, headers=None, body=None, timeout=60):
    """The one real HTTP function: (status, parsed JSON). `body` may be bytes
    (sent as-is), a dict (JSON) or None. HTTP errors come back as their status
    plus the error body, never as an exception, so callers report them."""
    if isinstance(body, dict):
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", **(headers or {})}
    else:
        data = body
    req = urllib.request.Request(url, method=method, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, _parse_body(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw.decode("utf-8"))
        except Exception:
            return e.code, {"error": raw.decode("utf-8", errors="replace")[:500]}


def _parse_body(raw: bytes):
    """JSON when it is JSON, else the text — RSS/Atom feeds come through the same door."""
    if not raw.strip():
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return raw.decode("utf-8", errors="replace")


def _check(status, body, what):
    if status >= 400:
        err = body.get("error") if isinstance(body, dict) else body
        if isinstance(err, dict):
            err = f"{err.get('status') or err.get('code')}: {err.get('message')}"
        raise RuntimeError(f"Google {what} failed (HTTP {status}): {err}")


# ── helpers shared with the Takeout path ─────────────────────────────────────
def thai_date(iso) -> str:
    """ISO 8601 ('2026-08-30T20:02:03.123Z', '+0000'), RFC 822 (RSS pubDate) or
    unix seconds (TikTok) → the calendar day in Thailand (UTC+7), e.g. '2026-08-31'."""
    if isinstance(iso, (int, float)) and not isinstance(iso, bool):
        return datetime.datetime.fromtimestamp(float(iso), tz=datetime.timezone.utc).astimezone(_TH).strftime("%Y-%m-%d")
    s = iso.strip() if isinstance(iso, str) else ""
    if not s:
        return ""
    if s.isdigit():
        return thai_date(int(s))
    if re.search(r"[+-]\d{4}$", s):            # Facebook writes +0000, not +00:00
        s = s[:-2] + ":" + s[-2:]
    try:
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = email.utils.parsedate_to_datetime(s)
        except (TypeError, ValueError):
            return s[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(_TH).strftime("%Y-%m-%d")


def original_text(comment) -> str:
    """Google prefixes a machine translation to reviews in other languages:
    '(Translated by Google) Great\n\n(Original) อร่อย' → 'อร่อย'."""
    s = (comment or "").strip()
    if "(Original)" in s:
        s = s.split("(Original)", 1)[1].strip()
    elif s.startswith("(Translated by Google)"):
        s = s[len("(Translated by Google)"):].strip()
    return s


def review_to_row(rv: dict, branch: str = "", location: str = "", url: str = ""):
    """One API/Takeout review → one upload row, or None when it carries neither
    text nor rating (nothing to keep)."""
    if not isinstance(rv, dict):
        return None
    text = original_text(rv.get("comment"))
    rating = rv.get("starRating")
    if isinstance(rating, str):
        rating = STARS.get(rating.upper())
    elif not isinstance(rating, (int, float)):
        rating = None
    if not text and rating is None:
        return None
    rid = rv.get("reviewId") or (rv.get("name") or "").rsplit("/", 1)[-1]
    reply = rv.get("reviewReply") or {}
    author = ((rv.get("reviewer") or {}).get("displayName") or "").strip()
    return {
        "date": thai_date(rv.get("createTime") or rv.get("updateTime")),
        "branch": branch or "", "source": SOURCE, "rating": rating, "text": text,
        "external_id": rid or None, "url": url or "", "location": location or "",
        "author": hashlib.sha1(author.encode("utf-8")).hexdigest()[:16] if author else "",
        "reply": (reply.get("comment") or "").strip(), "replied_at": thai_date(reply.get("updateTime")),
        "ref": rv.get("name") or "",          # accounts/…/locations/…/reviews/… — what a reply is PUT to
    }


def post_reply(token: str, ref: str, text: str, http) -> dict:
    """Publish (or replace) the owner's reply to one review. `ref` is the
    review's resource name; the reply is limited to 4,096 characters."""
    if not ref or "/reviews/" not in ref:
        raise RuntimeError("this review carries no resource name to reply to (uploaded from a file?)")
    status, j = http("PUT", f"{REVIEWS_API}/{ref}/reply", {"Authorization": f"Bearer {token}"},
                     {"comment": (text or "").strip()[:4096]})
    _check(status, j, "reply")
    return j or {}


def reviews_url(place_id: str) -> str:
    return f"https://search.google.com/local/reviews?placeid={place_id}" if place_id else ""


# ── OAuth ────────────────────────────────────────────────────────────────────
def auth_url(client_id: str, redirect_uri: str, state: str = "") -> str:
    q = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
         "scope": SCOPE, "access_type": "offline", "prompt": "consent"}
    if state:
        q["state"] = state
    return AUTH_URL + "?" + urllib.parse.urlencode(q)


def exchange_code(client_id, client_secret, code, redirect_uri, http) -> dict:
    """Authorisation code → {access_token, refresh_token, expires_in}."""
    body = urllib.parse.urlencode({"client_id": client_id, "client_secret": client_secret, "code": code,
                                   "redirect_uri": redirect_uri, "grant_type": "authorization_code"}).encode()
    status, j = http("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
    _check(status, j, "code exchange")
    if not j.get("refresh_token"):
        raise RuntimeError("Google returned no refresh_token — revoke the app at myaccount.google.com/permissions and run auth again")
    return j


def access_token(cfg: dict, http) -> str:
    """Refresh-token grant → a fresh access token (valid ~1 h)."""
    for k in ("client_id", "client_secret", "refresh_token"):
        if not cfg.get(k):
            raise RuntimeError(f"google config lacks {k} — run: python backend/fetch_feedback.py auth google")
    body = urllib.parse.urlencode({"client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
                                   "refresh_token": cfg["refresh_token"], "grant_type": "refresh_token"}).encode()
    status, j = http("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
    _check(status, j, "token refresh")
    return j["access_token"]


# ── API reads ────────────────────────────────────────────────────────────────
def _get(token, url, http, what):
    status, j = http("GET", url, {"Authorization": f"Bearer {token}"}, None)
    _check(status, j, what)
    return j or {}


def list_accounts(token, http) -> list:
    """[{name: 'accounts/123', accountName, type}] — every account the login manages."""
    out, page = [], ""
    while True:
        j = _get(token, f"{ACCOUNTS_API}/accounts" + (f"?pageToken={page}" if page else ""), http, "accounts")
        out.extend(j.get("accounts") or [])
        page = j.get("nextPageToken")
        if not page:
            return out


def list_locations(token, account, http) -> list:
    """[{name: 'locations/456', title, place_id}] of one account."""
    out, page = [], ""
    while True:
        q = {"readMask": "name,title,metadata", "pageSize": 100}
        if page:
            q["pageToken"] = page
        j = _get(token, f"{LOCATIONS_API}/{account}/locations?" + urllib.parse.urlencode(q), http, "locations")
        for loc in j.get("locations") or []:
            out.append({"name": loc.get("name") or "", "title": (loc.get("title") or "").strip(),
                        "place_id": (loc.get("metadata") or {}).get("placeId") or ""})
        page = j.get("nextPageToken")
        if not page:
            return out


def iter_reviews(token, account, location, http, since: str = ""):
    """Reviews of one location, newest UPDATE first — stops paging once a page's
    last review was updated before `since` (YYYY-MM-DD, Thai day), so a nightly
    run reads one page per branch. `location` is 'locations/456'."""
    loc_id = location.rsplit("/", 1)[-1]
    acc_id = account.rsplit("/", 1)[-1]
    page = ""
    while True:
        q = {"pageSize": 50}
        if page:
            q["pageToken"] = page
        j = _get(token, f"{REVIEWS_API}/accounts/{acc_id}/locations/{loc_id}/reviews?" + urllib.parse.urlencode(q),
                 http, "reviews")
        reviews = j.get("reviews") or []
        for rv in reviews:
            yield rv
        page = j.get("nextPageToken")
        if not page or not reviews:
            return
        if since and thai_date(reviews[-1].get("updateTime") or reviews[-1].get("createTime")) < since:
            return


def fetch_rows(cfg: dict, http, since: str = "", log=None) -> tuple:
    """Everything a nightly run needs: rows for the upload + meta
    {locations: [{title, name, place_id, branch, reviews}], fetched}.
    Branch per listing: cfg['location_map'] (title or 'locations/…' → branch
    name) else the listing title itself, which the server matches by name."""
    log = log or (lambda *a: None)
    token = access_token(cfg, http)
    lmap = cfg.get("location_map") or {}
    accounts = [cfg["account"]] if cfg.get("account") else [a["name"] for a in list_accounts(token, http)]
    rows, meta = [], {"locations": [], "fetched": 0}
    for acc in accounts:
        for loc in list_locations(token, acc, http):
            branch = lmap.get(loc["title"]) or lmap.get(loc["name"]) or loc["title"]
            n = 0
            for rv in iter_reviews(token, acc, loc["name"], http, since):
                if since and thai_date(rv.get("updateTime") or rv.get("createTime")) < since:
                    continue
                row = review_to_row(rv, branch, loc["title"], reviews_url(loc["place_id"]))
                if row:
                    rows.append(row); n += 1
            meta["locations"].append({**loc, "account": acc, "branch": branch, "reviews": n})
            meta["fetched"] += n
            log(f"  {loc['title']:<40} → {branch:<16} {n:>5} review(s)")
    return rows, meta


# ── Takeout / JSON files ─────────────────────────────────────────────────────
def parse_reviews_json(obj, branch: str = "", location: str = "", url: str = "") -> list:
    """A reviews.json (Takeout), a ListReviews API response, or a bare list."""
    if isinstance(obj, (bytes, str)):
        obj = json.loads(obj)
    reviews = obj.get("reviews") if isinstance(obj, dict) else obj
    if not isinstance(reviews, list):
        return []
    rows = []
    for rv in reviews:
        row = review_to_row(rv, branch, location, url)
        if row:
            rows.append(row)
    return rows


def _location_title(meta: dict) -> str:
    for k in ("title", "locationName", "storeCode", "name"):
        v = meta.get(k)
        if isinstance(v, str) and v.strip() and not v.startswith("locations/"):
            return v.strip()
    return ""


def parse_takeout_zip(data: bytes, default_branch: str = "", location_map: dict = None) -> tuple:
    """A Google Takeout archive (or any zip of reviews*.json files) → (rows,
    locations). Each location folder's data.json names the listing; without it
    the folder name stands in. `location_map` (title → branch) wins over the
    title, `default_branch` covers a single-listing export."""
    lmap = location_map or {}
    rows, locations = [], []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        metas = {}
        for n in names:
            base = posixpath.basename(n).lower()
            if base in ("data.json", "location.json"):
                try:
                    metas[posixpath.dirname(n)] = json.loads(z.read(n).decode("utf-8-sig"))
                except Exception:
                    pass
        for n in names:
            base = posixpath.basename(n).lower()
            if not (base.startswith("review") and base.endswith(".json")):
                continue
            folder = posixpath.dirname(n)
            meta = metas.get(folder) or {}
            title = _location_title(meta) or posixpath.basename(folder) or ""
            place = (meta.get("metadata") or {}).get("placeId") or meta.get("placeId") or ""
            branch = lmap.get(title) or default_branch or title
            try:
                got = parse_reviews_json(z.read(n).decode("utf-8-sig"), branch, title, reviews_url(place))
            except Exception:
                got = []
            rows.extend(got)
            locations.append({"folder": folder, "title": title, "branch": branch, "reviews": len(got)})
    return rows, locations


__all__ = ["urllib_http", "thai_date", "original_text", "review_to_row", "post_reply", "auth_url", "exchange_code",
           "access_token", "list_accounts", "list_locations", "iter_reviews", "fetch_rows",
           "parse_reviews_json", "parse_takeout_zip", "SOURCE", "SCOPE"]
