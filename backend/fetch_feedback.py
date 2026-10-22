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
    python backend/fetch_feedback.py replies                  # post the replies queued in the tab now (run does it too)

Where the rows go: the LIVE app (--api, default https://oshinei-dashboard.fly.dev)
through POST /api/feedback/upload, which analyses and de-duplicates them. The
live DB is the only place reviews exist — they are carried across the daily
DB push (core._PRESERVED_EXACT). --db PATH writes into a local SQLite file
instead (dev / tests) through the same insert code.

Config — backend/.feedback_config.json (gitignored; Backup DB.bat copies it):
  {"google":   {"client_id", "client_secret", "refresh_token",
                "account"?: "accounts/123", "location_map"?: {"<listing title>": "<สาขา>"}},
   "facebook": {"api_version"?: "v21.0",
                "pages": [{"id", "name", "branch", "token", "comments"?: false, "shares"?, "visitor_posts"?, "tagged"?}]},
   "instagram": {"accounts": [{"ig_user_id", "username", "page_id", "token", "branch", "comments"?, "tags"?}]},  # pages --save fills it
   "tiktok":   {"accounts": [{"client_key", "client_secret", "access_token", "refresh_token", "business_id", "branch"?}]},  # auth tiktok --branch fills one account
   "x":        {"bearer_token", "keywords"?: ["โอชิเน", "oshinei"]},                          # Basic tier (paid)
   "youtube":  {"api_key", "keywords"?: [...], "comments"?: true},
   "web":      {"keywords"?: [...], "feeds"?: [{"url", "name", "source"}]}}                   # Google News built in + Google Alerts RSS
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

from feedback_sources import facebook, google_gbp, instagram, places, rss, tiktok, x, youtube  # noqa: E402
from feedback_sources.google_gbp import _parse_body, urllib_http  # noqa: E402
import notify  # noqa: E402
from refresh_fly_db import _parse_token_file, get_login  # noqa: E402

# config key → (source label in the DB, fetch(cfg, http, since, log) -> (rows, meta))
SOURCES = {"google": (google_gbp.SOURCE, google_gbp.fetch_rows),
           "facebook": (facebook.SOURCE, facebook.fetch_rows),
           "instagram": (instagram.SOURCE, instagram.fetch_rows),
           "tiktok": (tiktok.SOURCE, tiktok.fetch_rows),
           "x": (x.SOURCE, x.fetch_rows),
           "youtube": (youtube.SOURCE, youtube.fetch_rows),
           "web": (rss.SOURCE, rss.fetch_rows)}


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
                return r.status, _parse_body(r.read())      # HTML from a proxy error page must not crash the run
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

    def branches(self) -> list:
        return [b["name"] for b in (self._call("GET", "/api/branches") or []) if b.get("active", True)]

    def competitors_month(self):
        return (self._call("GET", "/api/feedback/competitors/status") or {}).get("latest_month")

    def places_settings(self) -> dict:
        return self._call("GET", "/api/feedback/competitors/settings")

    def competitors_post(self, month: str, rows: list) -> dict:
        return self._call("POST", "/api/feedback/competitors", {"month": month, "rows": rows})

    def digest(self, days: int = 7, branch: str = None) -> dict:
        q = f"/api/feedback/digest?days={int(days)}"
        if branch:
            q += "&branch=" + urllib.parse.quote(branch)
        return self._call("GET", q)

    def daily_report(self, date: str = None, branch: str = None) -> dict:
        params = []
        if date:
            params.append("date=" + urllib.parse.quote(date))
        if branch:
            params.append("branch=" + urllib.parse.quote(branch))
        q = "/api/feedback/daily-report" + (("?" + "&".join(params)) if params else "")
        return self._call("GET", q)

    def sla_alerts(self) -> dict:
        return self._call("GET", "/api/feedback/sla-alerts")

    def sla_alerts_ack(self, ids: list) -> dict:
        return self._call("POST", "/api/feedback/sla-alerts/ack", {"ids": list(ids)})

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

    def reply_queue(self) -> dict:
        return self._call("GET", "/api/feedback/reply-queue?status=pending")

    def reply_result(self, fid: int, status: str, error: str = "") -> dict:
        return self._call("POST", "/api/feedback/reply-result", {"id": fid, "status": status, "error": error})

    def webhook_events(self, platform: str) -> dict:
        return self._call("GET", f"/api/feedback/webhook-events?platform={urllib.parse.quote(platform)}&unprocessed=1")

    def ack_events(self, ids: list, result: str = "ok") -> dict:
        return self._call("POST", "/api/feedback/webhook-events/ack", {"ids": list(ids), "result": result})


class DbSink:
    """Writes into a local SQLite file through the endpoint's own insert code."""

    def __init__(self, db_path):
        from routers import feedback as fb
        self.fb, self.path = fb, str(db_path)

    def _con(self):
        con = sqlite3.connect(self.path, timeout=30)      # the live app writes the same file (WAL): wait for it, do not fail
        con.row_factory = sqlite3.Row
        self.fb._ensure(con)
        return con

    def branches(self) -> list:
        from feedback_host import branch_rows
        con = self._con()
        try:
            return [b["name"] for b in branch_rows(con) if b["active"]]
        finally:
            con.close()

    def competitors_month(self):
        from routers.feedback_competitors import latest_month
        con = self._con()
        try:
            return latest_month(con)
        finally:
            con.close()

    def places_settings(self) -> dict:
        from routers.feedback_competitors import get_places_settings
        con = self._con()
        try:
            return get_places_settings(con)
        finally:
            con.close()

    def competitors_post(self, month: str, rows: list) -> dict:
        from routers.feedback_competitors import store_snapshot
        con = self._con()
        try:
            n = store_snapshot(con, month, rows)
            con.commit()
            return {"month": month, "stored": n}
        finally:
            con.close()

    def digest(self, days: int = 7, branch: str = None) -> dict:
        from routers.feedback_actions import compute_digest
        con = self._con()
        try:
            out = compute_digest(con, days=days, branch=branch)
            con.commit()
            return out
        finally:
            con.close()

    def daily_report(self, date: str = None, branch: str = None) -> dict:
        from feedback_host import thai_today
        from routers.feedback_daily_report import compute_daily_report
        con = self._con()
        try:
            out = compute_daily_report(con, date or thai_today(), branch)
            con.commit()
            return out
        finally:
            con.close()

    def sla_alerts(self) -> dict:
        today = self.fb._thai_today()
        con = self._con()
        try:
            rows = [dict(r) for r in con.execute(
                "SELECT id, branch_name, source, rated_at, rating, text, sentiment, external_id, url, kind, "
                "reply_text, reply_status, contact, severity, severity_note FROM customer_feedback WHERE sla_alerted_at IS NULL")]
            items = [{"id": r["id"], "branch": r["branch_name"], "source": r["source"], "rating": r["rating"],
                     "text": (r["text"] or "")[:200], "url": r.get("url") or "", "age_days": self.fb._age_days(r["rated_at"], today),
                     "severity": r.get("severity") or "low", "severity_note": r.get("severity_note") or ""}
                     for r in rows if self.fb._sla_breached(r, today)]
            return {"items": items, "today": today}
        finally:
            con.close()

    def sla_alerts_ack(self, ids: list) -> dict:
        con = self._con()
        try:
            now = self.fb._thai_now()
            con.executemany("UPDATE customer_feedback SET sla_alerted_at=? WHERE id=?", [(now, i) for i in ids])
            con.commit()
            return {"acked": len(ids)}
        finally:
            con.close()

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

    def reply_queue(self) -> dict:
        con = self._con()
        try:
            return {"items": [dict(r) for r in con.execute(
                "SELECT id, source, external_id, external_ref, branch_name, location, reply_draft, reply_status "
                "FROM customer_feedback WHERE reply_status='pending' ORDER BY reply_requested_at, id")]}
        finally:
            con.close()

    def reply_result(self, fid: int, status: str, error: str = "") -> dict:
        con = self._con()
        try:
            if status == "sent":
                con.execute("UPDATE customer_feedback SET reply_status='sent', reply_error=NULL, reply_text=reply_draft, "
                            "replied_at=date('now','+7 hours') WHERE id=?", (fid,))
            else:
                con.execute("UPDATE customer_feedback SET reply_status='failed', reply_error=? WHERE id=?",
                            ((error or "unknown error")[:300], fid))
            con.commit()
            return {"id": fid, "reply_status": status}
        finally:
            con.close()

    def webhook_events(self, platform: str) -> dict:
        con = self._con()
        try:
            out = []
            for r in con.execute("SELECT id, received_at, platform, object_id, field, payload FROM feedback_webhook_events "
                                 "WHERE platform=? AND processed_at IS NULL ORDER BY id", (platform,)):
                d = dict(r)
                try:
                    d["payload"] = json.loads(d.get("payload") or "{}")
                except ValueError:
                    d["payload"] = {}
                out.append(d)
            return {"events": out}
        finally:
            con.close()

    def ack_events(self, ids: list, result: str = "ok") -> dict:
        con = self._con()
        try:
            con.executemany("UPDATE feedback_webhook_events SET processed_at=datetime('now','+7 hours'), result=? WHERE id=?",
                            [(result[:200], i) for i in ids])
            con.commit()
            return {"acked": len(ids)}
        finally:
            con.close()


# ── queued replies ───────────────────────────────────────────────────────────
def send_replies(cfg: dict, sink, http=urllib_http, log=print) -> dict:
    """Post every reply queued in the tab: Google reviews through the Business
    Profile API, Facebook comments through the Graph API. Each row is reported
    back as sent or failed (with the reason), so a reply never waits silently."""
    items = (sink.reply_queue() or {}).get("items") or []
    if not items:
        return {"sent": 0, "failed": 0}
    g_token, tt_tokens = None, {}                    # tt_tokens: business_id → refreshed {access_token, ...}, one refresh per account
    sent = failed = 0
    for it in items:
        src = (it.get("source") or "").strip().lower()
        try:
            text = (it.get("reply_draft") or "").strip()
            if not text:
                raise RuntimeError("empty reply text")
            if src == "tiktok":
                tt_cfg = cfg.get("tiktok") or {}
                accounts = {str(a.get("business_id")): a for a in tiktok.accounts_of(tt_cfg) if a.get("business_id")}
                business_id, _, rest = (it.get("external_ref") or "").partition("/")
                vid, _, cid = rest.partition("/")
                account = accounts.get(business_id)
                if not account:
                    raise RuntimeError(f"tiktok is not configured on this PC for account {business_id or '(unknown)'} (backend/.feedback_config.json)")
                if business_id not in tt_tokens:
                    tt_tokens[business_id] = tiktok.refresh(account, http)
                tok = tt_tokens[business_id]
                tiktok.reply_to_comment(tok["access_token"], tok.get("business_id") or business_id, vid, cid, text, http)
            elif src == "instagram":
                ig_cfg = cfg.get("instagram") or {}
                accs = {str(a.get("ig_user_id")): a for a in ig_cfg.get("accounts") or []}
                igid, _, cid = (it.get("external_ref") or "").partition("/")
                acc = accs.get(igid)
                if not acc:
                    raise RuntimeError("instagram is not configured on this PC for this account (backend/.feedback_config.json)")
                instagram.reply_to_comment(acc.get("token") or "", cid, text, http, ig_cfg.get("api_version") or facebook.DEFAULT_VERSION)
            elif src == "google":
                if not cfg.get("google"):
                    raise RuntimeError("google is not configured on this PC (backend/.feedback_config.json)")
                if g_token is None:
                    g_token = google_gbp.access_token(cfg["google"], http)
                google_gbp.post_reply(g_token, it.get("external_ref") or "", text, http)
            elif src == "facebook":
                fb = cfg.get("facebook") or {}
                page = facebook.page_for_comment(fb, it.get("external_ref") or "", it.get("location") or "")
                if not page:
                    raise RuntimeError("facebook is not configured on this PC for this page (backend/.feedback_config.json)")
                facebook.reply_to_comment(page.get("token") or "", it.get("external_ref") or "", text, http,
                                          fb.get("api_version") or facebook.DEFAULT_VERSION)
            else:
                raise RuntimeError(f"replies on {src or '?'} cannot be posted through an API")
            sink.reply_result(it["id"], "sent")
            sent += 1
            log(f"  ✓ reply #{it['id']} sent ({src}, {it.get('branch_name') or it.get('location') or ''})")
        except Exception as e:
            try:
                sink.reply_result(it["id"], "failed", str(e)[:300])
            except Exception as e2:
                log(f"  (could not record the failure of reply #{it['id']}: {e2})")
            failed += 1
            log(f"  ✗ reply #{it['id']} failed: {e}")
    return {"sent": sent, "failed": failed}


def _weekday() -> int:            # seam: 0 = Monday, Thai clock
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=7)).weekday()


def _branch_chats(cfg: dict) -> dict:
    """{branch name: telegram chat id} — each branch's own group, on top of the
    chain's chat (`telegram.branches` in .feedback_config.json)."""
    return dict(((cfg or {}).get("telegram") or {}).get("branches") or {})


def _branch_alerts(neg: list, cfg: dict, label: str, api: str, notifier_to, log) -> int:
    """The negative voices of a branch also go to that branch's own chat."""
    chats = _branch_chats(cfg)
    if not chats or not notifier_to:
        return 0
    sent = 0
    for bname, chat in chats.items():
        mine = [it for it in neg if (it.get("branch") or "") == bname]
        if mine and notifier_to(notify.negative_digest(mine, label, api), chat):
            sent += 1
            log(f"  alert → {bname}: {len(mine)} negative review(s) to the branch chat")
    return sent


def _digest_step(args, sink, notifier, log, force: bool = False, cfg: dict = None, notifier_to=None):
    """Every Monday (or on --digest) the week's voices go to the group chat:
    counts against the week before, what waits, and whether the fixes held.
    Branches with their own chat get their own digest."""
    if getattr(args, "dry_run", False) or not notifier:
        return False
    if not (force or getattr(args, "digest", False) or _weekday() == 0):
        return False
    try:
        d = sink.digest(7)
        sent = notifier(notify.weekly_digest(d, getattr(args, "api", "") or ""))
        log(f"  digest: {d.get('voices', 0)} voice(s) in {d.get('range')} → telegram {'sent' if sent else 'not configured'}")
        if notifier_to:
            for bname, chat in _branch_chats(cfg).items():
                try:
                    db = sink.digest(7, bname)
                    if db.get("voices") or (db.get("actions") or {}).get("open"):
                        notifier_to(notify.weekly_digest(db, getattr(args, "api", "") or ""), chat)
                        log(f"  digest → {bname}: {db.get('voices', 0)} voice(s)")
                except Exception as e:      # one branch's chat must not stop the others
                    log(f"  ✗ digest → {bname}: {e}")
        return bool(sent)
    except Exception as e:
        log(f"  ✗ digest: {e}")
        return False


def _sla_alert_step(args, sink, notifier, log, cfg: dict = None, notifier_to=None):
    """Cases that just crossed the SLA get an immediate ping — same spirit as
    the new-negatives alert in run(), but keyed off age rather than "fetched
    this run" (a case can sit unanswered for days before it actually breaches,
    long after the run that fetched it). Each case pings once (ack marks
    sla_alerted_at). Its own --no-sla-alert flag, separate from --no-alert:
    a run can want the new-negatives alert without the (potentially noisier,
    backlog-clearing) SLA alert, or vice versa."""
    if getattr(args, "dry_run", False) or not notifier or getattr(args, "no_sla_alert", False):
        return False
    try:
        items = (sink.sla_alerts() or {}).get("items") or []
        if not items:
            return False
        sent = notifier(notify.sla_alert_digest(items, getattr(args, "api", "") or ""))
        log(f"  sla alert: {len(items)} case(s) → telegram {'sent' if sent else 'not configured'}")
        if not sent:
            return False          # unconfigured: leave un-acked so the next run tries again
        if notifier_to:
            for bname, chat in _branch_chats(cfg).items():
                mine = [it for it in items if (it.get("branch") or "") == bname]
                if mine and notifier_to(notify.sla_alert_digest(mine, getattr(args, "api", "") or ""), chat):
                    log(f"  sla alert → {bname}: {len(mine)} case(s)")
        sink.sla_alerts_ack([it["id"] for it in items])
        return True
    except Exception as e:
        log(f"  ✗ sla alert: {e}")
        return False


def _yesterday() -> str:
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=7) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")


def _daily_report_step(args, sink, notifier, log, force: bool = False, cfg: dict = None, notifier_to=None):
    """Every run (unless --no-daily-report), yesterday's 📰 report — brand-new
    unclaimed problems and any chronic pattern — goes to the group chat, so
    marketing sees them without opening the app and clicking "สร้างรายงาน".
    Complements _digest_step (the week-over-week numbers, Mondays only)."""
    if getattr(args, "dry_run", False) or not notifier:
        return False
    if not force and getattr(args, "no_daily_report", False):
        return False
    date = getattr(args, "date", None) or _yesterday()
    try:
        d = sink.daily_report(date)
        sent = notifier(notify.daily_report_digest(d, getattr(args, "api", "") or ""))
        log(f"  daily report: {d.get('total', 0)} voice(s) on {date} → telegram {'sent' if sent else 'not configured'}")
        if notifier_to:
            for bname, chat in _branch_chats(cfg).items():
                try:
                    db = sink.daily_report(date, bname)
                    if db.get("total") or db.get("new_issues") or db.get("chronic_branches"):
                        notifier_to(notify.daily_report_digest(db, getattr(args, "api", "") or ""), chat)
                        log(f"  daily report → {bname}: {db.get('total', 0)} voice(s)")
                except Exception as e:      # one branch's chat must not stop the others
                    log(f"  ✗ daily report → {bname}: {e}")
        return bool(sent)
    except Exception as e:
        log(f"  ✗ daily report: {e}")
        return False


def _this_month() -> str:
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=7)).strftime("%Y-%m")


def _places_step(args, cfg, sink, http, log, force: bool = False) -> bool:
    """Once a month (the first run that finds no snapshot for this month, or
    --places / the `places` command) the competitors around every branch are
    captured from Google Places into feedback_competitor."""
    pc = dict((cfg or {}).get("places") or {})
    if not pc.get("api_key") or getattr(args, "dry_run", False):
        return False
    try:                                        # radius_m/keywords set from the app (Settings) override the file —
        override = sink.places_settings() or {}  # chain-wide keys replace outright; "branches" merges per-branch so a
        for k, v in override.items():             # file-set find_self() query override for that branch survives
            if k != "branches" and v:
                pc[k] = v
        for b, ov in (override.get("branches") or {}).items():
            pc.setdefault("branches", {}).setdefault(b, {}).update({k: v for k, v in ov.items() if v})
    except Exception as e:
        log(f"  places: could not read the Settings override, using .feedback_config.json as-is ({e})")
    month = _this_month()
    if not (force or getattr(args, "places", False)):
        try:
            if sink.competitors_month() == month:
                return False
        except Exception as e:
            log(f"  ✗ places: cannot read the snapshot status: {e}")
            return False
    try:
        branches = [b for b in sink.branches() if b and b != "ALL"]
        only = pc.get("only") or []
        if only:
            branches = [b for b in branches if b in only]
        rows, meta = places.snapshot(pc, http, branches, log)
        res = sink.competitors_post(month, rows)
        log(f"  ✓ places: {res.get('stored', 0)} row(s) for {month} across {len(meta['branches'])} branch(es)")
        sink.log({"source": places.SOURCE, "since": month, "fetched": meta["fetched"], "inserted": res.get("stored", 0),
                  "status": "ok", "message": f"{len(branches)} branch(es), snapshot {month}", "by": "fetch_feedback"})
        return True
    except Exception as e:
        log(f"  ✗ places: {e}")
        try:
            sink.log({"source": places.SOURCE, "since": month, "status": "error", "message": str(e)[:300], "by": "fetch_feedback"})
        except Exception:
            pass
        return False


def _replies_step(args, cfg, sink, http, log):
    if getattr(args, "dry_run", False):
        return
    try:
        res = send_replies(cfg, sink, http, log)
    except Exception as e:
        log(f"  ✗ reply queue: {e}")
        return
    if res["sent"] or res["failed"]:
        log(f"  replies: {res['sent']} sent, {res['failed']} failed")


# ── the nightly run ──────────────────────────────────────────────────────────
def run(args, cfg: dict, sink, http=urllib_http, notifier=None, log=print, notifier_to=None) -> int:
    if args.source != "all" and not cfg.get(args.source):
        log(f"{args.source}: not configured in backend/.feedback_config.json — see docs/FEEDBACK_SOURCES.html")
        return 1
    names = [args.source] if args.source != "all" else [k for k in SOURCES if cfg.get(k)]
    if not names:
        log("no review source configured yet — see docs/FEEDBACK_SOURCES.html (nothing to do)")
        _replies_step(args, cfg, sink, http, log)   # queued replies fail with a reason instead of waiting forever
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
        if name == "instagram" and not args.dry_run:
            # @mentions arrive through the dashboard's Meta webhook; resolve what it kept since the last run
            try:
                events = (sink.webhook_events("instagram") or {}).get("events") or []
                if events:
                    m_rows, done, failed = instagram.resolve_mentions(cfg[name], events, http, log)
                    rows.extend(m_rows)
                    meta["mentions"] = len(m_rows)
                    if done:
                        sink.ack_events(done, "ok")
                    for eid, why in failed:
                        sink.ack_events([eid], f"failed: {why}")
                    log(f"  webhook: {len(events)} mention event(s) → {len(m_rows)} row(s), {len(failed)} failed")
            except Exception as e:
                log(f"  webhook events: {e}")
        log(f"  fetched {len(rows)} row(s) in {time.time() - t0:.0f}s")
        if meta.get("config_update") and getattr(args, "config", None):
            cfg[name].update(meta["config_update"])          # rotated tokens (TikTok) go back into the file
            save_config(cfg, args.config)
        if args.dry_run:
            for r in rows[:10]:
                log(f"    {r['date']} {r['branch']:<14} {str(r['rating'] if r['rating'] is not None else '-'):>4}  {r['text'][:70]}")
            continue
        try:
            res = sink.upload(rows, by=f"fetch:{name}") if rows else \
                {"inserted": 0, "updated": 0, "unchanged": 0, "unmatched": [], "new_items": [], "analysed_by": "none"}
            units = next((v for v in meta.values() if isinstance(v, list)), [])   # locations / pages / videos / queries / feeds
            msg = f"{len(units)} listing(s), {time.time() - t0:.0f}s, analysed by {res.get('analysed_by')}"
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
            _branch_alerts(neg, cfg, label, args.api, notifier_to, log)
    _replies_step(args, cfg, sink, http, log)
    _places_step(args, cfg, sink, http, log)
    _sla_alert_step(args, sink, notifier, log, cfg=cfg, notifier_to=notifier_to)
    _digest_step(args, sink, notifier, log, cfg=cfg, notifier_to=notifier_to)
    _daily_report_step(args, sink, notifier, log, cfg=cfg, notifier_to=notifier_to)
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


def tiktok_auth(cfg: dict, http, redirect_uri: str = None, log=print, code: str = None, branch: str = None) -> dict:
    """TikTok's consent must land on a registered https URL, so the dashboard's
    own /api/feedback/oauth-callback page shows the code and the owner pastes
    it here (no localhost listener). One developer app (client_key/secret) can
    authorize many accounts, so with --branch this adds/replaces that branch's
    account in tiktok.accounts instead of overwriting the whole section —
    every branch with its own TikTok account runs this once, same app."""
    t = cfg.setdefault("tiktok", {})
    accounts = t.setdefault("accounts", [])
    existing = next((a for a in accounts if (a.get("branch") or None) == branch), None) if branch else None
    # client_key/secret: this account's own (re-auth), else the branch-less
    # legacy fields, else whatever the first configured account already has
    client_key = (existing or {}).get("client_key") or t.get("client_key") or next((a.get("client_key") for a in accounts if a.get("client_key")), None)
    client_secret = (existing or {}).get("client_secret") or t.get("client_secret") or next((a.get("client_secret") for a in accounts if a.get("client_secret")), None)
    if not (client_key and client_secret):
        raise SystemExit("fill tiktok.client_key and tiktok.client_secret in backend/.feedback_config.json first "
                         "(docs/FEEDBACK_SOURCES.html §10) — one TikTok developer app, reused for every branch's account")
    redirect = redirect_uri or t.get("redirect_uri") or f"{DEFAULT_API}/api/feedback/oauth-callback"
    url = tiktok.auth_url(client_key, redirect)
    log(f"1. open this URL, log in to {'the ' + branch + ' branch' if branch else 'the brand'}'s TikTok account, allow:\n   " + url)
    log(f"2. the browser lands on {redirect} (register exactly this URL in the TikTok app) and shows a code")
    code = (code or input("3. paste the code: ")).strip()
    if not code:
        raise SystemExit("no code")
    tok = tiktok.exchange_code(client_key, client_secret, code, redirect, http)
    account = {**(existing or {}), "client_key": client_key, "client_secret": client_secret, **tok}
    if branch:
        account["branch"] = branch
    if existing:
        accounts[accounts.index(existing)] = account
    else:
        accounts.append(account)
    t["redirect_uri"] = redirect
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
    """The pages (and their linked Instagram accounts) a user token manages;
    --save writes both into the config, keeping branches already filled in."""
    pages = facebook.list_pages(user_token, http)
    fb = cfg.setdefault("facebook", {})
    have = {str(p.get("id")): p for p in fb.get("pages") or []}
    ig_cfg = cfg.setdefault("instagram", {}) if save else {}
    have_ig = {str(a.get("ig_user_id")): a for a in ig_cfg.get("accounts") or []} if save else {}
    for p in pages:
        ig = p.get("instagram")
        log(f"  {p['name']:<40} id {p['id']}  page token: {'yes' if p['token'] else 'NO'}"
            + (f"  IG @{ig['username']} ({ig['id']})" if ig else "  (no Instagram account linked)"))
        if save:
            entry = have.get(p["id"]) or {"id": p["id"], "name": p["name"], "branch": "", "comments": False}
            entry["token"], entry["name"] = p["token"], p["name"]
            have[p["id"]] = entry
            if ig:
                acc = have_ig.get(ig["id"]) or {"ig_user_id": ig["id"], "username": ig["username"], "page_id": p["id"],
                                                 "branch": entry.get("branch") or "", "comments": True, "tags": True}
                acc["token"], acc["username"], acc["page_id"] = p["token"], ig["username"] or acc.get("username", ""), p["id"]
                have_ig[ig["id"]] = acc
    if save:
        fb["pages"] = list(have.values())
        if have_ig:
            ig_cfg["accounts"] = list(have_ig.values())
        elif not ig_cfg:
            cfg.pop("instagram", None)
        log('saved — now fill "branch" for each page / account in backend/.feedback_config.json')
    return pages


def subscribe_webhooks(cfg: dict, http, log=print) -> int:
    """Subscribe the app to every configured page's webhooks (once per page)."""
    n = 0
    for p in (cfg.get("facebook") or {}).get("pages") or []:
        try:
            facebook.subscribe_page(str(p.get("id")), p.get("token") or "", http,
                                    (cfg.get("facebook") or {}).get("api_version") or facebook.DEFAULT_VERSION)
            log(f"  ✓ {p.get('name') or p.get('id')}: subscribed")
            n += 1
        except Exception as e:
            log(f"  ✗ {p.get('name') or p.get('id')}: {e}")
    return n


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
    r.add_argument("--no-sla-alert", action="store_true", help="skip the immediate SLA-breach alert after this run")
    r.add_argument("--digest", action="store_true", help="send the weekly digest after this run (else Mondays only)")
    r.add_argument("--no-daily-report", action="store_true", help="skip the daily report push after this run (else every run, for yesterday)")
    r.add_argument("--places", action="store_true", help="capture the competitor snapshot now (else once a month)")
    a = sub.add_parser("auth", help="one-time consent"); common(a)
    a.add_argument("source", choices=["google", "tiktok"]); a.add_argument("--manual", action="store_true")
    a.add_argument("--port", type=int, default=8765)
    a.add_argument("--redirect", default=None, help="tiktok: the registered https redirect URL (default: the live app's callback page)")
    a.add_argument("--branch", default=None, help="tiktok: which branch's account this authorizes (adds/replaces it in tiktok.accounts; omit for a single branch-less account)")
    lo = sub.add_parser("locations", help="listings the login manages"); common(lo)
    lo.add_argument("source", choices=["google"])
    pg = sub.add_parser("pages", help="pages a user token manages"); common(pg)
    pg.add_argument("source", choices=["facebook"]); pg.add_argument("--user-token", required=True)
    pg.add_argument("--save", action="store_true")
    tg = sub.add_parser("test-telegram", help="send one test message")
    tg.add_argument("--text", default="Oshinei feedback sync: ทดสอบการแจ้งเตือน ✓")
    sw = sub.add_parser("subscribe", help="subscribe the Meta app to the configured pages' webhooks"); common(sw)
    sw.add_argument("source", choices=["facebook"])
    dg = sub.add_parser("digest", help="send the weekly voice digest to Telegram now"); common(dg)
    dg.add_argument("--api", default=DEFAULT_API); dg.add_argument("--db", default=None)
    dg.add_argument("--token", default=None); dg.add_argument("--user", default=None); dg.add_argument("--password", default=None)
    dg.add_argument("--days", type=int, default=7)
    dr = sub.add_parser("daily-report", help="send one day's (or period's) 📰 report to Telegram now"); common(dr)
    dr.add_argument("--api", default=DEFAULT_API); dr.add_argument("--db", default=None)
    dr.add_argument("--token", default=None); dr.add_argument("--user", default=None); dr.add_argument("--password", default=None)
    dr.add_argument("--date", default=None, help="YYYY-MM-DD (default: yesterday, Thai clock)")
    dr.add_argument("--branch", default=None)
    sa = sub.add_parser("sla-alert", help="send the SLA-breach alert to Telegram now (and ack the pushed cases)"); common(sa)
    sa.add_argument("--api", default=DEFAULT_API); sa.add_argument("--db", default=None)
    sa.add_argument("--token", default=None); sa.add_argument("--user", default=None); sa.add_argument("--password", default=None)
    pl = sub.add_parser("places", help="capture the competitors around every branch now (Google Places)"); common(pl)
    pl.add_argument("--api", default=DEFAULT_API); pl.add_argument("--db", default=None)
    pl.add_argument("--token", default=None); pl.add_argument("--user", default=None); pl.add_argument("--password", default=None)
    rp = sub.add_parser("replies", help="post the replies queued in the tab now"); common(rp)
    rp.add_argument("--api", default=DEFAULT_API); rp.add_argument("--db", default=None)
    rp.add_argument("--token", default=None); rp.add_argument("--user", default=None); rp.add_argument("--password", default=None)
    args = ap.parse_args(argv)

    if args.cmd == "test-telegram":
        ok = notify.send_telegram(args.text)
        print("✓ sent" if ok else "✗ not sent — set OSHINEI_TG_TOKEN + OSHINEI_TG_CHAT_ID in backend/.env")
        return 0 if ok else 1
    cfg = load_config(args.config)
    if args.cmd == "auth":
        if args.source == "tiktok":
            cfg = tiktok_auth(cfg, urllib_http, args.redirect, branch=args.branch)
            save_config(cfg, args.config)
            who = f" for {args.branch}" if args.branch else ""
            print(f"✓ TikTok account{who} saved to {args.config}\n  next: python backend/fetch_feedback.py run --source tiktok --since all --dry-run"
                 + ("\n  another branch: python backend/fetch_feedback.py auth tiktok --branch <ชื่อสาขาถัดไป>" if not args.branch else ""))
            return 0
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
    if args.cmd == "subscribe":
        n = subscribe_webhooks(cfg, urllib_http)
        print(f"{n} page(s) subscribed — the dashboard receives Meta webhooks at /api/feedback/webhook/meta "
              "(META_APP_SECRET + META_WEBHOOK_VERIFY_TOKEN must be set on Fly, docs/FEEDBACK_SOURCES.html §3.2)")
        return 0 if n else 1
    if args.db:
        sink = DbSink(args.db)
    else:
        login = get_login(SimpleNamespace(user=args.user, password=args.password))
        sink = ApiSink(args.api, resolve_token(args.token), login)
    if args.cmd == "digest":
        d = sink.digest(args.days)
        text = notify.weekly_digest(d, args.api)
        ok = notify.send_telegram(text)
        print(text)
        print("telegram:", "sent" if ok else "not configured (OSHINEI_TG_TOKEN / OSHINEI_TG_CHAT_ID)")
        for bname, chat in _branch_chats(cfg).items():
            db = sink.digest(args.days, bname)
            okb = notify.send_telegram(notify.weekly_digest(db, args.api), chat_id=chat)
            print(f"  → {bname}: {'sent' if okb else 'not sent'}")
        return 0
    if args.cmd == "daily-report":
        date = args.date or _yesterday()
        d = sink.daily_report(date, args.branch)
        text = notify.daily_report_digest(d, args.api)
        ok = notify.send_telegram(text)
        print(text)
        print("telegram:", "sent" if ok else "not configured (OSHINEI_TG_TOKEN / OSHINEI_TG_CHAT_ID)")
        return 0
    if args.cmd == "sla-alert":
        items = (sink.sla_alerts() or {}).get("items") or []
        if not items:
            print("no case has just crossed the SLA")
            return 0
        text = notify.sla_alert_digest(items, args.api)
        ok = notify.send_telegram(text)
        print(text)
        print("telegram:", "sent" if ok else "not configured (OSHINEI_TG_TOKEN / OSHINEI_TG_CHAT_ID)")
        if ok:
            sink.sla_alerts_ack([it["id"] for it in items])
        return 0
    if args.cmd == "places":
        ok = _places_step(args, cfg, sink, urllib_http, print, force=True)
        return 0 if ok else 1
    if args.cmd == "replies":
        res = send_replies(cfg, sink, urllib_http)
        print(f"replies: {res['sent']} sent, {res['failed']} failed")
        return 0
    return run(args, cfg, sink, urllib_http, notifier=notify.send_telegram,
               notifier_to=lambda text, chat: notify.send_telegram(text, chat_id=chat))


if __name__ == "__main__":
    sys.exit(main())
