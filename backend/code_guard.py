"""Code guard (owner 2026-10-27): the app opens to the branches outside head office, so its code
must not be easy to take or copy, and an AI or bot that tries is noticed.

What a browser runs can always be read by that browser, so this is layers, not a lock:
  1. nothing but the login page before a login (main.root / enforce_auth gate /js, /tabs.html);
  2. no API map (/openapi.json, /redoc off) and the code ships minified + mangled without
     comments (scripts/minify-js.js at the Docker build; HTML comments stripped here);
  3. every slice a login downloads carries that login's mark (`mark_for`), so a copy that
     turns up somewhere names the account it came from (GET /api/usage/security?mark=…);
  4. AI agents, crawlers and scripted browsers are refused and recorded: a known bot User-Agent
     (`bot_ua`), the page's own automation check (POST /api/sec/signal: navigator.webdriver,
     headless Chrome), and a session that sweeps far more distinct API paths than a person does
     (`Sweep`). Each kind is recorded in usage.db (survives the daily DB swap) and pushed to the
     admins at most once an hour per login/address.
"""
import hashlib
import hmac
import re
import sqlite3
import threading
import time
from collections import deque

# AI crawlers / agents and plain HTTP tools. A real browser never sends these words.
_BOT_RE = re.compile(
    r"gptbot|chatgpt|oai-searchbot|openai|claudebot|claude-user|claude-searchbot|claude-web|anthropic|"
    r"perplexity|ccbot|bytespider|google-extended|googleother|gemini|cohere|diffbot|amazonbot|"
    r"applebot-extended|meta-externalagent|facebookbot|youbot|mistral|deepseek|petalbot|imagesift|"
    r"timpibot|omgili|scrapy|python-requests|python-urllib|aiohttp|httpx|go-http-client|"
    r"node-fetch|axios|undici|okhttp|java/|libwww|curl/|wget|httrack|headlesschrome|phantomjs|"
    r"puppeteer|playwright|selenium|webdriver", re.I)


def bot_ua(ua: str) -> bool:
    """True for an empty User-Agent or one that names an AI crawler, a scraper library or a
    scripted browser."""
    ua = (ua or "").strip()
    return not ua or bool(_BOT_RE.search(ua))


_COMMENT_RE = re.compile(r"<!--(?!\[if)[\s\S]*?-->")


def strip_html_comments(html: str) -> str:
    """The shell's and the panels' developer notes stay in the repo, not in the browser."""
    return _COMMENT_RE.sub("", html)


def mark_for(username: str, secret: bytes) -> str:
    """The login's code mark: 12 hex of HMAC(secret, username). Stable per login, so a copy can
    be traced back by recomputing it for every account (`who_has`)."""
    return hmac.new(secret, ("cg|" + (username or "")).encode("utf-8"), hashlib.sha256).hexdigest()[:12]


def who_has(mark: str, usernames, secret: bytes):
    mark = (mark or "").strip().lower()
    return next((u for u in usernames if mark_for(u, secret) == mark), None)


def mark_js(body: bytes, mark: str) -> bytes:
    """The slice with the login's mark in front. A statement, not a comment, so a minifier
    or a copy-paste keeps it."""
    return f'var __cg=__cg||"{mark}";'.encode("ascii") + body


# ── sweep detection ─────────────────────────────────────────────────────────
# A person opening every page of the desktop app reaches ~120 distinct API paths in ten
# minutes; a branch login, whose pages are few, ~40. A crawler walking the API does far more.
SWEEP_WINDOW = 600.0
SWEEP_LIMIT = 240
SWEEP_LIMIT_BRANCH = 90


class Sweep:
    """Distinct API paths (query string dropped, numbers folded) per login in a sliding window."""

    def __init__(self, window=SWEEP_WINDOW, max_keys=5000):
        self.window, self.max_keys = window, max_keys
        self._seen = {}
        self._lock = threading.Lock()

    @staticmethod
    def _shape(path: str) -> str:
        return re.sub(r"/\d[\w.-]*", "/#", path)

    def hit(self, who: str, path: str, now=None) -> int:
        now = time.time() if now is None else now
        with self._lock:
            if who not in self._seen and len(self._seen) >= self.max_keys:
                self._seen.pop(next(iter(self._seen)))
            q = self._seen.setdefault(who, deque())
            q.append((now, self._shape(path)))
            while q and now - q[0][0] > self.window:
                q.popleft()
            return len({p for _, p in q})


# ── events ──────────────────────────────────────────────────────────────────
_ALERT_EVERY = 3600.0
_last_alert = {}
_alert_lock = threading.Lock()

KIND_TH = {
    "bot_ua": "โปรแกรมอัตโนมัติหรือ AI พยายามเปิดแอป",
    "automation": "พบเบราว์เซอร์ที่ควบคุมด้วยโปรแกรมอัตโนมัติ",
    "sweep": "บัญชีเรียกข้อมูลจำนวนมากผิดปกติ",
}


def ensure(con) -> None:
    con.execute("CREATE TABLE IF NOT EXISTS sec_event("
                " id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL,"
                " username TEXT DEFAULT '', ip TEXT DEFAULT '', ua TEXT DEFAULT '',"
                " path TEXT DEFAULT '', detail TEXT DEFAULT '')")


def record(db_path, kind: str, username: str = "", ip: str = "", ua: str = "", path: str = "",
           detail: str = "", alert=None) -> bool:
    """Append one event (best effort) and, at most once an hour per (kind, login or address),
    call `alert(kind, username, ip, detail)`. Returns True when the alert fired."""
    try:
        con = sqlite3.connect(str(db_path), timeout=5)
        try:
            ensure(con)
            con.execute("INSERT INTO sec_event(at, kind, username, ip, ua, path, detail)"
                        " VALUES (datetime('now','+7 hours'), ?, ?, ?, ?, ?, ?)",
                        (kind, (username or "")[:40], (ip or "")[:64], (ua or "")[:300],
                         (path or "")[:200], (detail or "")[:300]))
            con.commit()
        finally:
            con.close()
    except Exception:
        pass
    key = (kind, username or ip)
    now = time.time()
    with _alert_lock:
        if now - _last_alert.get(key, 0) < _ALERT_EVERY:
            return False
        if len(_last_alert) > 5000:
            _last_alert.clear()
        _last_alert[key] = now
    if alert:
        try:
            alert(kind, username, ip, detail)
        except Exception:
            pass
    return True


def recent(db_path, limit: int = 200) -> list:
    try:
        con = sqlite3.connect(str(db_path), timeout=5)
        try:
            ensure(con)
            cur = con.execute("SELECT at, kind, username, ip, ua, path, detail FROM sec_event"
                              " ORDER BY id DESC LIMIT ?", (int(limit),))
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            con.close()
    except Exception:
        return []
