#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collect customer reviews from the platforms into the live dashboard.

    python backend/fetch_feedback.py run                      # every configured source, since its last good run
    python backend/fetch_feedback.py run --source google --since 2026-08-01
    python backend/fetch_feedback.py run --since all --dry-run  # fetch everything, print, post nothing
    python backend/fetch_feedback.py auth google              # one-time consent → refresh token into the config
    python backend/fetch_feedback.py locations google         # the listings the login manages (fill location_map)
    python backend/fetch_feedback.py pages facebook --user-token … [--save]   # pages + Page tokens into the config
    python backend/fetch_feedback.py test-telegram            # one message to the configured chat

Where the rows go: the LIVE app (--api, default https://oshinei-dashboard.fly.dev)
through POST /api/feedback/upload, which analyses and de-duplicates them. The
live DB is the only place reviews exist — they are carried across the daily
DB push (core._PRESERVED_EXACT). --db PATH writes into a local SQLite file
instead (dev / tests) through the same insert code.

Config — backend/.feedback_config.json (gitignored; Backup DB.bat copies it):
  {"google":   {"client_id", "client_secret", "refresh_token",
                "account"?: "accounts/123", "location_map"?: {"<listing title>": "<สาขา>"}},
   "facebook": {"api_version"?: "v21.0",
                "pages": [{"id", "name", "branch", "token", "comments"?: false}]}}
Posting needs the write token (--token → env OSHINEI_WRITE_TOKEN / OSHINEI_SEED_TOKEN
→ backend/.seed_token(s).txt) and, when the site enforces login,
backend/.refresh_login.txt — the same files refresh_fly_db.py uses. New negative
reviews are announced on Telegram when OSHINEI_TG_TOKEN + OSHINEI_TG_CHAT_ID are
set in backend/.env (notify.py).
Exit code: 0 when every configured source ran (or none is configured yet),
1 when one failed — Task Scheduler "Oshinei Feedback Sync" (Fetch Feedback.bat).
"""
import argparse
import datetime
import http.cookiejar
import http.server
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from types import SimpleNamespace

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))
CONFIG_PATH = HERE / ".feedback_config.json"
DEFAULT_API = "https://oshinei-dashboard.fly.dev"


def _load_env(path=HERE / ".env"):
    """backend/.env → os.environ (no external dep; existing values win)."""
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


_load_env()

from feedback_sources import facebook, google_gbp  # noqa: E402
from feedback_sources.google_gbp import urllib_http  # noqa: E402
import notify  # noqa: E402
from refresh_fly_db import _parse_token_file, get_login  # noqa: E402

SOURCES = {"google": (google_gbp.SOURCE, google_gbp.fetch_rows),
           "facebook": (facebook.SOURCE, facebook.fetch_rows)}


# ── config & secrets ─────────────────────────────────────────────────────────
def load_config(path=CONFIG_PATH) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8-sig") or "{}")


def save_config(cfg: dict, path=CONFIG_PATH) -> None:
    Path(path).write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_token(explicit=None) -> str:
    if explicit:
        return explicit.strip()
    for k in ("OSHINEI_WRITE_TOKEN", "OSHINEI_SEED_TOKEN"):
        raw = (os.environ.get(k) or "").strip()
        if raw:
            first = raw.replace("\n", ",").split(",")[0].strip()
            return first.split(":", 1)[1].strip() if ":" in first else first
    for name in (".seed_token.txt", ".seed_tokens.txt"):
        f = HERE / name
        if f.exists():
            tok = _parse_token_file(f)
            if tok:
                return tok
    return ""


def auto_since(sources: dict, label: str, overlap_days: int = 2) -> str:
    """The last good run of `label` minus an overlap (edits and late replies of
    reviews already collected), '' = everything, for a first run."""
    ran = ((sources or {}).get("last_ok") or {}).get(label) or ""
    if len(ran) < 10:
        return ""
    try:
        return (datetime.date.fromisoformat(ran[:10]) - datetime.timedelta(days=overlap_days)).isoformat()
    except ValueError:
        return ""


# ── sinks: where the rows go ─────────────────────────────────────────────────
def _cookie_http():
    """JSON over urllib with a cookie jar (the login session)."""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def http_(method, url, headers=None, body=None):
        headers = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, method=method, data=data, headers=headers)
        try:
            with opener.open(req, timeout=300) as r:
                raw = r.read()
                return r.status, (json.loads(raw.decode("utf-8")) if raw.strip() else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw.decode("utf-8"))
            except Exception:
                return e.code, {"error": raw.decode("utf-8", errors="replace")[:300]}
    return http_


class ApiSink:
    """Posts into the live app; logs in first when the site enforces auth."""

    def __init__(self, api: str, token: str, login=(None, None), http=None):
        self.api, self.token, self.login = api.rstrip("/"), token, login
        self.http = http or _cookie_http()
        self._logged_in = False

    def _call(self, method, path, body=None):
        headers = {"X-Upload-Token": self.token} if self.token else {}
        status, j = self.http(method, self.api + path, headers, body)
        if status == 401 and not self._logged_in:
            self._do_login()
            status, j = self.http(method, self.api + path, headers, body)
        if status >= 400:
            raise RuntimeError(f"{method} {path} → HTTP {status}: {str(j)[:300]}")
        return j or {}

    def _do_login(self):
        user, pw = self.login
        if not (user and pw):
            raise RuntimeError("the site requires login — put user:pass in backend/.refresh_login.txt (or --user/--password)")
        status, j = self.http("POST", self.api + "/api/auth/login", {}, {"username": user, "password": pw})
        if status >= 400:
            raise RuntimeError(f"login failed (HTTP {status}): {str(j)[:200]}")
        self._logged_in = True

    def sources(self) -> dict:
        return self._call("GET", "/api/feedback/sources")

    def upload(self, rows: list, by: str = "") -> dict:
        total = {"rows": 0, "inserted": 0, "updated": 0, "unchanged": 0, "duplicates": 0,
                 "analysed_by": set(), "unmatched": set(), "new_items": []}
        for i in range(0, len(rows), 1000):
            r = self._call("POST", "/api/feedback/upload", {"rows": rows[i:i + 1000], "by": by})
            for k in ("rows", "inserted", "updated", "unchanged", "duplicates"):
                total[k] += int(r.get(k) or 0)
            total["analysed_by"].add(r.get("analysed_by") or "none")
            total["unmatched"].update(r.get("unmatched") or [])
            total["new_items"].extend(r.get("new_items") or [])
        total["analysed_by"] = "/".join(sorted(total["analysed_by"] - {"none"})) or "none"
        total["unmatched"] = sorted(total["unmatched"])
        return total

    def log(self, entry: dict) -> dict:
        return self._call("POST", "/api/feedback/sync-log", entry)


class DbSink:
    """Writes into a local SQLite file through the endpoint's own insert code."""

    def __init__(self, db_path):
        from routers import feedback as fb
        self.fb, self.path = fb, str(db_path)

    def _con(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        self.fb._ensure(con)
        return con

    def sources(self) -> dict:
        con = self._con()
        try:
            last_ok = {}
            for r in con.execute("SELECT source, ran_at FROM feedback_sync_log WHERE status='ok' ORDER BY id DESC"):
                last_ok.setdefault(r["source"], r["ran_at"])
            return {"sources": [], "last_ok": last_ok}
        finally:
            con.close()

    def upload(self, rows: list, by: str = "") -> dict:
        con = self._con()
        try:
            out = self.fb.insert_reviews(con, rows, by)
            con.commit()
            return out
        finally:
            con.close()

    def log(self, entry: dict) -> dict:
        con = self._con()
        try:
            cur = con.execute(
                "INSERT INTO feedback_sync_log(source, since, fetched, inserted, updated, unchanged, status, message, by) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (entry.get("source"), entry.get("since") or "", int(entry.get("fetched") or 0),
                 int(entry.get("inserted") or 0), int(entry.get("updated") or 0), int(entry.get("unchanged") or 0),
                 entry.get("status") or "ok", str(entry.get("message") or "")[:500], entry.get("by") or ""))
            con.commit()
            return {"logged": cur.lastrowid}
        finally:
            con.close()


# ── the nightly run ──────────────────────────────────────────────────────────
def run(args, cfg: dict, sink, http=urllib_http, notifier=None, log=print) -> int:
    if args.source != "all" and not cfg.get(args.source):
        log(f"{args.source}: not configured in backend/.feedback_config.json — see docs/FEEDBACK_SOURCES.html")
        return 1
    names = [args.source] if args.source != "all" else [k for k in SOURCES if cfg.get(k)]
    if not names:
        log("no review source configured yet — see docs/FEEDBACK_SOURCES.html (nothing to do)")
        return 0
    if args.since not in ("auto", "all", ""):
        datetime.date.fromisoformat(args.since)       # ValueError on a bad date
    srcs = sink.sources() if args.since == "auto" else {}
    failures = 0
    for name in names:
        label, fetch = SOURCES[name]
        since = auto_since(srcs, label) if args.since == "auto" else ("" if args.since in ("all", "") else args.since)
        log(f"▶ {label}: since {since or 'the beginning'}")
        t0 = time.time()
        try:
            rows, meta = fetch(cfg[name], http, since, log=log)
        except Exception as e:
            log(f"  ✗ {label}: {e}")
            try:
                sink.log({"source": label, "since": since, "status": "error", "message": str(e)[:400], "by": "fetch_feedback"})
            except Exception as e2:
                log(f"  (could not record the failure: {e2})")
            failures += 1
            continue
        log(f"  fetched {len(rows)} row(s) in {time.time() - t0:.0f}s")
        if args.dry_run:
            for r in rows[:10]:
                log(f"    {r['date']} {r['branch']:<14} {str(r['rating'] if r['rating'] is not None else '-'):>4}  {r['text'][:70]}")
            continue
        try:
            res = sink.upload(rows, by=f"fetch:{name}") if rows else \
                {"inserted": 0, "updated": 0, "unchanged": 0, "unmatched": [], "new_items": [], "analysed_by": "none"}
            listings = meta.get("locations") or meta.get("pages") or []
            msg = f"{len(listings)} listing(s), {time.time() - t0:.0f}s, analysed by {res.get('analysed_by')}"
            if res.get("unmatched"):
                msg += "; no branch for: " + ", ".join(res["unmatched"])
            sink.log({"source": label, "since": since, "fetched": len(rows), "inserted": res["inserted"],
                      "updated": res["updated"], "unchanged": res.get("unchanged", 0), "status": "ok",
                      "message": msg, "by": "fetch_feedback"})
        except Exception as e:
            log(f"  ✗ {label}: posting failed: {e}")
            failures += 1
            continue
        log(f"  ✓ {label}: +{res['inserted']} new, {res['updated']} updated, {res.get('unchanged', 0)} unchanged"
            + (f" — no branch matched: {', '.join(res['unmatched'])}" if res.get("unmatched") else ""))
        neg = [it for it in (res.get("new_items") or [])
               if it.get("sentiment") == "neg" or (it.get("rating") is not None and float(it["rating"]) <= 2)]
        if neg and notifier and not getattr(args, "no_alert", False):
            sent = notifier(notify.negative_digest(neg, label, args.api))
            log(f"  alert: {len(neg)} negative review(s) → telegram {'sent' if sent else 'not configured'}")
    return 1 if failures else 0


# ── one-time setup commands ──────────────────────────────────────────────────
class _CodeCatcher(http.server.BaseHTTPRequestHandler):
    code = None

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _CodeCatcher.code = (q.get("code") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = "เรียบร้อย — กลับไปที่หน้าต่างคำสั่งได้เลย" if _CodeCatcher.code else "ไม่ได้รับ code (ถูกปฏิเสธ?) ลองใหม่"
        self.wfile.write(f"<h3 style='font-family:sans-serif'>{msg}</h3>".encode("utf-8"))

    def log_message(self, *a):
        pass


def wait_for_code(port: int, timeout: int = 300) -> str:
    srv = http.server.HTTPServer(("localhost", port), _CodeCatcher)
    srv.timeout = timeout
    try:
        srv.handle_request()
    finally:
        srv.server_close()
    if not _CodeCatcher.code:
        raise RuntimeError("no authorisation code received (timed out or consent denied)")
    return _CodeCatcher.code


def google_auth(cfg: dict, http, port: int = 8765, manual: bool = False, open_browser: bool = True, log=print) -> dict:
    g = cfg.setdefault("google", {})
    if not (g.get("client_id") and g.get("client_secret")):
        raise SystemExit("fill google.client_id and google.client_secret in backend/.feedback_config.json first "
                         "(docs/FEEDBACK_SOURCES.html §1)")
    redirect = f"http://localhost:{port}/"
    url = google_gbp.auth_url(g["client_id"], redirect)
    log("1. open this URL, sign in with the Google account that manages the listings, allow:\n   " + url)
    if manual:
        code = input("2. paste the code from the address bar (…?code=XXX&…): ").strip()
    else:
        if open_browser:
            webbrowser.open(url)
        log(f"2. waiting for the browser to come back to {redirect} (up to 5 min)…")
        code = wait_for_code(port)
    tok = google_gbp.exchange_code(g["client_id"], g["client_secret"], code, redirect, http)
    g["refresh_token"] = tok["refresh_token"]
    return cfg


def google_locations(cfg: dict, http, log=print) -> list:
    g = cfg.get("google") or {}
    token = google_gbp.access_token(g, http)
    accounts = google_gbp.list_accounts(token, http)
    lmap = g.get("location_map") or {}
    out = []
    for a in accounts:
        for loc in google_gbp.list_locations(token, a["name"], http):
            out.append({"account": a["name"], **loc, "branch": lmap.get(loc["title"]) or lmap.get(loc["name"]) or ""})
    log(f"{len(out)} listing(s) under {len(accounts)} account(s)")
    for o in out:
        log(f"  {o['title']:<40} {o['name']:<22} → {o['branch'] or '(matched by name on the server)'}")
    log('for a listing whose title does not contain the branch name, add to the google section:\n'
        '  "location_map": {"<title>": "<สาขา>"}')
    return out


def facebook_pages(cfg: dict, http, user_token: str, save: bool = False, log=print) -> list:
    pages = facebook.list_pages(user_token, http)
    fb = cfg.setdefault("facebook", {})
    have = {str(p.get("id")): p for p in fb.get("pages") or []}
    for p in pages:
        log(f"  {p['name']:<40} id {p['id']}  page token: {'yes' if p['token'] else 'NO'}")
        if save:
            entry = have.get(p["id"]) or {"id": p["id"], "name": p["name"], "branch": "", "comments": False}
            entry["token"], entry["name"] = p["token"], p["name"]
            have[p["id"]] = entry
    if save:
        fb["pages"] = list(have.values())
        log('saved — now fill "branch" for each page in backend/.feedback_config.json')
    return pages


# ── CLI ──────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Collect customer reviews into the dashboard.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--config", default=str(CONFIG_PATH))

    r = sub.add_parser("run", help="collect and post"); common(r)
    r.add_argument("--source", default="all", choices=["all", *SOURCES])
    r.add_argument("--since", default="auto", help="auto (last good run − 2 days) | YYYY-MM-DD | all")
    r.add_argument("--api", default=DEFAULT_API)
    r.add_argument("--db", default=None, help="write into this local SQLite file instead of the live app")
    r.add_argument("--token", default=None); r.add_argument("--user", default=None); r.add_argument("--password", default=None)
    r.add_argument("--dry-run", action="store_true"); r.add_argument("--no-alert", action="store_true")
    a = sub.add_parser("auth", help="one-time consent"); common(a)
    a.add_argument("source", choices=["google"]); a.add_argument("--manual", action="store_true")
    a.add_argument("--port", type=int, default=8765)
    lo = sub.add_parser("locations", help="listings the login manages"); common(lo)
    lo.add_argument("source", choices=["google"])
    pg = sub.add_parser("pages", help="pages a user token manages"); common(pg)
    pg.add_argument("source", choices=["facebook"]); pg.add_argument("--user-token", required=True)
    pg.add_argument("--save", action="store_true")
    tg = sub.add_parser("test-telegram", help="send one test message")
    tg.add_argument("--text", default="Oshinei feedback sync: ทดสอบการแจ้งเตือน ✓")
    args = ap.parse_args(argv)

    if args.cmd == "test-telegram":
        ok = notify.send_telegram(args.text)
        print("✓ sent" if ok else "✗ not sent — set OSHINEI_TG_TOKEN + OSHINEI_TG_CHAT_ID in backend/.env")
        return 0 if ok else 1
    cfg = load_config(args.config)
    if args.cmd == "auth":
        cfg = google_auth(cfg, urllib_http, args.port, args.manual)
        save_config(cfg, args.config)
        print(f"✓ refresh token saved to {args.config}\n  next: python backend/fetch_feedback.py locations google")
        return 0
    if args.cmd == "locations":
        google_locations(cfg, urllib_http)
        return 0
    if args.cmd == "pages":
        facebook_pages(cfg, urllib_http, args.user_token, args.save)
        if args.save:
            save_config(cfg, args.config)
        return 0
    if args.db:
        sink = DbSink(args.db)
    else:
        login = get_login(SimpleNamespace(user=args.user, password=args.password))
        sink = ApiSink(args.api, resolve_token(args.token), login)
    return run(args, cfg, sink, urllib_http, notifier=notify.send_telegram)


if __name__ == "__main__":
    sys.exit(main())
