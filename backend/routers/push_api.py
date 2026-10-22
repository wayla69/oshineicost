# -*- coding: utf-8 -*-
"""Web Push for the phone: the morning brief as a notification, no chat bot needed.

    OSHINEI_VAPID_PRIVATE   base64url raw EC private key (32 bytes)      } generated once with
    OSHINEI_VAPID_PUBLIC    base64url uncompressed public point (65 bytes)} py_vapid, kept as Fly
    OSHINEI_VAPID_SUBJECT   a URL or mailto: the push service may contact } secrets + local backup

Unset → /api/push/vapid-public says configured:false and the phone hides its card;
nothing else changes. Subscriptions live in push_subscription (preserved across the
sync's DB swap by name, core._PRESERVED_EXACT). The morning send runs from a daemon
thread in main.py (the Fly machine never stops: min_machines_running = 1), once a
day after 08:30 Thai time, guarded by usage_meta like the supply reminder.

Weekly (Monday 08:30 onward, once per week): last Monday–Sunday against the week before, chain
total + customers + the best and the weakest branch, sent as soon as the settled day reaches that
Sunday (so a late sync only delays it).

Cash and discounts (same table, pos_cashcount_day): a branch whose cash count was short by
≥ CASH_SHORT_MIN on the last complete day, and a branch whose discount rate that day was ≥ 5%
and ≥ 2× its own 28-day rate. Rare on real data (about one a week each), so each one is a push.

Alerts (2026-10-08): the same thread also checks every ALERT_EVERY_MIN minutes for two
things worth a push on their own — a branch far below its normal on the last settled day (under both its 7-day average and
last week's same weekday),
and sales data that has not arrived by early afternoon (the sync is stuck). Each alert
fires once per distinct condition (usage_meta push_alerts_sent) and carries a deep link
(?go=…) the phone opens on tap. Morning brief and alerts go only to logins that see the
whole chain; a branch-scoped login's devices are skipped.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import auth as _auth
from core import get_db

router = APIRouter()

MORNING_HOUR, MORNING_MINUTE = 8, 30          # Thai clock
MAX_FAILS = 5                                   # a dead endpoint is dropped after this many errors
ALERT_FROM_HOUR = 7                             # drop alerts start once the 06:00 sync has settled
ALERT_EVERY_MIN = 10
STALE_HOUR = 13                                 # after the 12:00 sync: yesterday still missing = stuck
DROP_PCT = -40.0                                # under BOTH the 7-day average and the same weekday last week
DROP_MIN_AVG = 5000.0                           # ex-VAT baht/day — a tiny branch's noise is not an alert
CASH_SHORT_MIN = 2000.0                         # a day's cash count short by at least this (baht)
DISC_RATE_MIN = 5.0                             # a branch-day's discount ≥ 5% of its sales …
DISC_MULT = 2.0                                 # … and ≥ 2× that branch's own 28-day rate …
DISC_MIN_BAHT = 3000.0                          # … and at least this many baht (pos_cashcount_day)

# what a device can switch off, one by one (push_subscription.off holds the muted ids, comma-separated)
KINDS = [("morning", "สรุปเมื่อวานตอน 08:30"), ("weekly", "สรุปรายสัปดาห์ เช้าวันจันทร์"), ("drop", "สาขายอดตกหนัก"), ("stale", "ข้อมูลขายไม่เข้า"),
         ("cash", "เงินสดขาด"), ("disc", "ส่วนลดสูงผิดปกติ"),
         ("supply", "งานวัตถุดิบและใบสั่งซื้อ (ตอนเช้า และเตือนก่อนตัดรอบ)")]
KIND_IDS = [k for k, _ in KINDS]
# batch 3 (2026-10-12): the supply reminders reach the phones of the branch accounts and of the centre roles that own a line.
# A device that belongs to a branch-scoped login, or to a role that cannot read the chain's sales (the warehouse), is offered
# this kind alone and never receives the others (send_to_all chain_only) — they carry the chain's sales figures.
SUPPLY_MORNING = (8, 0)                         # Thai clock: the morning line of each branch and of the centre roles
PRECUTOFF_AT = (11, 0)                          # two hours before the 13:15 cutoff
CUTOFF_AT = (13, 15)                            # the missed-cutoff note, Sales Support only
CUTOFF_UNTIL_HOUR = 20                          # … and not at all after that (a restart at night must not announce it)

TH_MON = ['ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.', 'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.']
TH_DOW = ['จ.', 'อ.', 'พ.', 'พฤ.', 'ศ.', 'ส.', 'อา.']   # Python weekday(): Monday = 0


# ── config ──────────────────────────────────────────────────────────────────
def vapid_config(env=None) -> dict:
    env = os.environ if env is None else env
    priv = (env.get("OSHINEI_VAPID_PRIVATE") or "").strip()
    pub = (env.get("OSHINEI_VAPID_PUBLIC") or "").strip()
    sub = (env.get("OSHINEI_VAPID_SUBJECT") or "https://oshinei-dashboard.fly.dev").strip()
    return {"private": priv, "public": pub, "subject": sub, "configured": bool(priv and pub)}


def _thai_now():
    return datetime.utcnow() + timedelta(hours=7)


# ── storage ─────────────────────────────────────────────────────────────────
def ensure(con) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS push_subscription(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL DEFAULT '',
        endpoint TEXT NOT NULL UNIQUE,
        p256dh TEXT NOT NULL,
        auth TEXT NOT NULL,
        ua TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        last_ok TEXT,
        fails INTEGER NOT NULL DEFAULT 0)""")
    cols = {r[1] for r in con.execute("PRAGMA table_info(push_subscription)").fetchall()}
    if "branch" not in cols:       # the branch a scoped login is confined to ('' = sees the whole chain)
        con.execute("ALTER TABLE push_subscription ADD COLUMN branch TEXT NOT NULL DEFAULT ''")
    if "off" not in cols:          # kinds this device muted ('' = all on), e.g. 'cash,disc'
        con.execute("ALTER TABLE push_subscription ADD COLUMN off TEXT NOT NULL DEFAULT ''")
    con.execute("CREATE TABLE IF NOT EXISTS usage_meta(key TEXT PRIMARY KEY, value TEXT)")


def _meta_get(con, key: str):
    r = con.execute("SELECT value FROM usage_meta WHERE key = ?", (key,)).fetchone()
    return r[0] if r else None


def _meta_set(con, key: str, value: str) -> None:
    con.execute("INSERT INTO usage_meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value))


def _chain_ok(con, username: str) -> bool:
    """May this login read the chain's sales? The chain-wide kinds (morning, weekly, drop, stale, cash, disc) carry
    revenue figures, so a device of a role without sales.overview (the warehouse, accounting, a branch) never
    receives them. Always true when sign-in is off."""
    if not _auth.auth_enabled() or not username:
        return True
    try:
        u = _auth.get_user(con, username)
    except Exception:  # noqa: BLE001
        return False
    return bool(u and u["active"] and "sales.overview" in _auth.modules_for_role(u["role"]))


SUPPLY_ROLES = ("branch", "warehouse", "salessupport", "operation", "accounting")      # the logins that receive kind "supply"


def _kinds_for(con, user: str, request) -> list:
    """The kinds a login is offered: the chain's kinds when it may read the chain's sales (not a branch-scoped login), and
    "supply" when its role owns supply work (a branch account, the warehouse, Sales Support, Operations, Accounting)."""
    chain = _chain_ok(con, user) and not _auth.scope_branch(request)
    role = ""
    if _auth.auth_enabled() and user:
        try:
            u = _auth.get_user(con, user)
            role = (u["role"] if u else "") or ""
        except Exception:  # noqa: BLE001
            role = ""
    supply = role in SUPPLY_ROLES
    return [kv for kv in KINDS if (kv[0] != "supply" and chain) or (kv[0] == "supply" and supply)]


def _caller(request: Request) -> str:
    """The logged-in user's name ('' when auth is off). 401 when auth is on and nobody is."""
    if not _auth.auth_enabled():
        return ""
    payload = _auth.parse_token(request.cookies.get(_auth.SESSION_COOKIE, ""))
    if not payload:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return str(payload.get("u") or "")


class _Keys(BaseModel):
    p256dh: str
    auth: str


class _SubscribeBody(BaseModel):
    endpoint: str
    keys: _Keys
    ua: Optional[str] = ""


class _EndpointBody(BaseModel):
    endpoint: str


@router.get("/api/push/vapid-public", include_in_schema=False)
def push_vapid_public():
    c = vapid_config()
    return {"configured": c["configured"], "key": c["public"] or None}


@router.post("/api/push/subscribe", include_in_schema=False)
def push_subscribe(body: _SubscribeBody, request: Request):
    user = _caller(request)
    if not body.endpoint.startswith("https://"):
        raise HTTPException(status_code=400, detail="endpoint must be https")
    with get_db() as con:
        ensure(con)
        branch = _auth.scope_branch(request) or ""
        con.execute("""INSERT INTO push_subscription(username, endpoint, p256dh, auth, ua, created_at, branch)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(endpoint) DO UPDATE SET username = excluded.username, p256dh = excluded.p256dh,
                         auth = excluded.auth, ua = excluded.ua, fails = 0, branch = excluded.branch""",
                    (user, body.endpoint, body.keys.p256dh, body.keys.auth, (body.ua or "")[:200],
                     _thai_now().strftime("%Y-%m-%d %H:%M"), branch))
        con.commit()
        n = con.execute("SELECT COUNT(*) FROM push_subscription WHERE username = ?", (user,)).fetchone()[0]
    return {"ok": True, "devices": n}


@router.post("/api/push/unsubscribe", include_in_schema=False)
def push_unsubscribe(body: _EndpointBody, request: Request):
    _caller(request)
    with get_db() as con:
        ensure(con)
        cur = con.execute("DELETE FROM push_subscription WHERE endpoint = ?", (body.endpoint,))
        con.commit()
        return {"ok": True, "removed": cur.rowcount}


@router.get("/api/push/status", include_in_schema=False)
def push_status(request: Request):
    """How the morning notification stands: configured, how many devices, what the
    last send did. Endpoints are never returned (they are capability URLs)."""
    user = _caller(request)
    c = vapid_config()
    with get_db() as con:
        ensure(con)
        total = con.execute("SELECT COUNT(*) FROM push_subscription").fetchone()[0]
        mine = con.execute("SELECT COUNT(*) FROM push_subscription WHERE username = ?", (user,)).fetchone()[0]
        last = _meta_get(con, "push_morning")
        chain = _chain_ok(con, user) and not _auth.scope_branch(request)
    # the last morning record carries the chain's revenue figures and the device total: only a login that reads the chain sees them
    return {"configured": c["configured"], "devices": total if chain else mine, "my_devices": mine,
            "send_at": f"{MORNING_HOUR:02d}:{MORNING_MINUTE:02d}",
            "last": json.loads(last) if (last and chain) else None}


class _PrefsBody(BaseModel):
    endpoint: str
    off: Optional[list] = None      # None = just read; a list = the kinds to mute (replaces the old set)


@router.post("/api/push/prefs", include_in_schema=False)
def push_prefs(body: _PrefsBody, request: Request):
    """Which kinds this device receives. Only the caller's own device can be read or changed
    (matched by endpoint AND username); unknown ids are dropped."""
    user = _caller(request)
    with get_db() as con:
        ensure(con)
        row = con.execute("SELECT off FROM push_subscription WHERE endpoint = ? AND username = ?",
                          (body.endpoint, user)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="เครื่องนี้ยังไม่ได้เปิดรับการแจ้งเตือน")
        if body.off is not None:
            off = [k for k in KIND_IDS if k in {str(x) for x in body.off}]
            con.execute("UPDATE push_subscription SET off = ? WHERE endpoint = ? AND username = ?",
                        (",".join(off), body.endpoint, user))
            con.commit()
        else:
            off = [k for k in (row[0] or "").split(",") if k in KIND_IDS]
        shown = _kinds_for(con, user, request)
    return {"kinds": [{"id": k, "label": lbl} for k, lbl in shown], "off": off}


@router.post("/api/push/test", include_in_schema=False)
def push_test(request: Request):
    """One test notification to the caller's own devices, so the toggle proves itself."""
    user = _caller(request)
    c = vapid_config()
    if not c["configured"]:
        raise HTTPException(status_code=503, detail="ยังไม่ได้ตั้งค่าคีย์แจ้งเตือน (VAPID) บนเซิร์ฟเวอร์")
    with get_db() as con:
        ensure(con)
        chain = _chain_ok(con, user) and not _auth.scope_branch(request)
        body = (f"เครื่องนี้จะได้รับสรุปเมื่อวานทุกเช้า {MORNING_HOUR:02d}:{MORNING_MINUTE:02d}" if chain
                else "เครื่องนี้จะได้รับการแจ้งเตือนงานวัตถุดิบและใบสั่งซื้อ")
        payload = {"title": "Oshinei · ทดสอบการแจ้งเตือน", "body": body, "url": "/", "tag": "oshinei-test"}
        r = send_to_all(con, payload, only_user=user)
        con.commit()
    if not r["targets"]:
        raise HTTPException(status_code=404, detail="เครื่องนี้ยังไม่ได้เปิดรับการแจ้งเตือน")
    return r


# ── sending ──────────────────────────────────────────────────────────────────
def _default_send(sub_info: dict, data: str) -> int:
    """pywebpush, imported lazily so a box without it still serves everything else.
    Returns the push service's HTTP status; raises on transport errors."""
    from pywebpush import webpush, WebPushException   # noqa: WPS433
    c = vapid_config()
    try:
        resp = webpush(subscription_info=sub_info, data=data, vapid_private_key=c["private"],
                       vapid_claims={"sub": c["subject"]}, ttl=6 * 3600)
        return getattr(resp, "status_code", 201)
    except WebPushException as e:
        resp = getattr(e, "response", None)
        if resp is not None and getattr(resp, "status_code", None):
            return int(resp.status_code)
        raise


def send_to_all(con, payload: dict, send=None, only_user: Optional[str] = None,
                chain_only: bool = False, kind: Optional[str] = None, only_users: Optional[list] = None) -> dict:
    """Push `payload` to every stored device (or one user's, or a list of users'). 404/410 = the browser
    dropped the subscription → row deleted; other failures count, MAX_FAILS drops it.
    `chain_only` skips devices of branch-scoped logins and of roles that cannot read the chain's sales (the payload
    is chain-wide); `kind` skips devices that muted that kind (push_prefs)."""
    send = send or _default_send
    q, params = "SELECT id, endpoint, p256dh, auth, fails, off, username FROM push_subscription", ()
    if only_user is not None:
        q, params = q + " WHERE username = ?", (only_user,)
    elif only_users is not None:
        names = [str(u) for u in only_users]
        if not names:
            return {"targets": 0, "sent": 0, "gone": 0, "failed": 0, "statuses": [], "muted": 0}
        q, params = q + f" WHERE username IN ({','.join('?' * len(names))})", tuple(names)
    elif chain_only:
        q = q + " WHERE branch = ''"
    rows = con.execute(q, params).fetchall()
    if chain_only:
        ok = {u: _chain_ok(con, u) for u in {r[6] for r in rows}}
        rows = [r for r in rows if ok.get(r[6], True)]
    data = json.dumps(payload, ensure_ascii=False)
    muted = [r for r in rows if kind and kind in (r[5] or "").split(",")]
    rows = [r for r in rows if r not in muted]
    out = {"targets": len(rows), "sent": 0, "gone": 0, "failed": 0, "statuses": [], "muted": len(muted)}
    now = _thai_now().strftime("%Y-%m-%d %H:%M")
    for r in rows:
        sub_info = {"endpoint": r[1], "keys": {"p256dh": r[2], "auth": r[3]}}
        try:
            status = send(sub_info, data)
        except Exception as e:  # noqa: BLE001 — a transport error is a failure like any other
            print(f"[push] send failed for #{r[0]}: {e}")
            status = 0
        out["statuses"].append(int(status))
        print(f"[push] #{r[0]} {r[1][:40]}… → {status}")
        if 200 <= status < 300:
            out["sent"] += 1
            con.execute("UPDATE push_subscription SET last_ok = ?, fails = 0 WHERE id = ?", (now, r[0]))
        elif status in (404, 410):
            out["gone"] += 1
            con.execute("DELETE FROM push_subscription WHERE id = ?", (r[0],))
        else:
            out["failed"] += 1
            if (r[4] or 0) + 1 >= MAX_FAILS:
                con.execute("DELETE FROM push_subscription WHERE id = ?", (r[0],))
            else:
                con.execute("UPDATE push_subscription SET fails = fails + 1 WHERE id = ?", (r[0],))
    return out


# ── the morning brief as a notification ─────────────────────────────────────
def _fmt_money(v) -> str:
    v = float(v or 0)
    if abs(v) >= 1e6:
        return f"{v / 1e6:.2f}M"
    if abs(v) >= 1e3:
        return f"{v / 1e3:.1f}K"
    return f"{v:,.0f}"


def _th_day(iso: str) -> str:
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
        return f"{TH_DOW[d.weekday()]} {d.day} {TH_MON[d.month - 1]}"
    except Exception:
        return iso or ""


def morning_payload(brief: dict, scorecard: dict) -> Optional[dict]:
    """The phone's เมื่อวาน strip as title + body — same numbers, same rule for
    'below normal' (status down or vs_avg_pct ≤ −8, js/07-home.js). None when the
    brief has no day."""
    if not brief or not brief.get("yesterday"):
        return None
    rev, wow = float(brief.get("revenue_yesterday") or 0), brief.get("revenue_wow")
    chip = ""
    if wow:
        pct = (rev - float(wow)) / float(wow) * 100
        chip = f" {'▲' if pct >= 0 else '▼'}{abs(pct):.1f}%"
    title = f"เมื่อวาน ({_th_day(brief['yesterday'])}) {_fmt_money(rev)}{chip}"
    rows = (scorecard or {}).get("branches") or []
    down = [b for b in rows if b.get("status") == "down" or (b.get("vs_avg_pct") is not None and b["vs_avg_pct"] <= -8)]
    down.sort(key=lambda b: (b.get("vs_avg_pct") if b.get("vs_avg_pct") is not None else 0))
    bits = [f"ลูกค้า {int(brief.get('customer_yesterday') or 0):,}"]
    if down:
        worst = ", ".join(f"{b['branch']} {b['vs_avg_pct']:.0f}%" for b in down[:2] if b.get("vs_avg_pct") is not None)
        bits.append(f"{len(down)} สาขาต่ำกว่าปกติ" + (f": {worst}" if worst else ""))
    else:
        bits.append("ทุกสาขาอยู่ในเกณฑ์ปกติ")
    if brief.get("skipped_day"):
        bits.append(f"ข้อมูล {_th_day(brief['skipped_day'])} ยังมาไม่ครบ")
    return {"title": title, "body": " · ".join(bits), "url": "/?go=summary", "tag": "oshinei-morning"}


def build_morning() -> Optional[dict]:
    """Read the same two endpoints the Daily Brief and the phone strip read."""
    # the SAME module object main.py registered: on Fly the app runs as backend.main, so an
    # absolute `from routers import home_extra` re-executed the module under a second name and
    # @ttl_cache refused the duplicate function names (2026-10-08 08:30: every morning send failed)
    from . import home_extra as _he
    # both are @ttl_cache'd: called in-process the wrapper hands back serialized bytes,
    # so read the plain functions functools.wraps kept under __wrapped__
    brief_fn = getattr(_he.home_brief, "__wrapped__", _he.home_brief)
    sc_fn = getattr(_he.home_branch_scorecard, "__wrapped__", _he.home_branch_scorecard)
    return morning_payload(brief_fn(branch=None), sc_fn(date=None, group=None))


def maybe_send_morning(now=None, send=None, force: bool = False) -> Optional[dict]:
    """Once a day, from MORNING_HOUR:MORNING_MINUTE Thai time: build the brief and push
    it to every device. Best-effort; a day with no devices or no data is recorded too.
    Returns the record it stored, or None when it was not time / already done."""
    now = now or _thai_now()
    if not force and (now.hour, now.minute) < (MORNING_HOUR, MORNING_MINUTE):
        return None
    day = now.strftime("%Y-%m-%d")
    with get_db() as con:
        ensure(con)
        if not force and _meta_get(con, "push_morning_day") == day:
            return None
        _meta_set(con, "push_morning_day", day)      # claim first: two ticks never send twice
        con.commit()
        c = vapid_config()
        rec = {"day": day, "generated_at": now.strftime("%Y-%m-%d %H:%M"), "configured": c["configured"]}
        try:
            payload = build_morning()
        except Exception as e:  # noqa: BLE001
            payload = None
            rec["error"] = f"brief: {e}"
        rec["payload"] = payload
        if payload and c["configured"]:
            rec.update(send_to_all(con, payload, send=send, chain_only=True, kind="morning"))
        else:
            rec.update({"targets": con.execute("SELECT COUNT(*) FROM push_subscription").fetchone()[0],
                        "sent": 0, "gone": 0, "failed": 0})
        _meta_set(con, "push_morning", json.dumps(rec, ensure_ascii=False))
        con.commit()
    print(f"[push] morning {day}: {rec.get('sent')}/{rec.get('targets')} sent" + (f" ({rec['error']})" if rec.get("error") else ""))
    return rec


# ── alerts: worth a push on their own ───────────────────────────────────────
def _plain(v):
    """@ttl_cache'd endpoints hand back serialized bytes when called in-process."""
    if hasattr(v, "body"):                       # a starlette Response around the cached bytes
        v = v.body
    if isinstance(v, (bytes, bytearray, str)):
        return json.loads(v)
    return v


def read_scorecard() -> dict:
    from . import home_extra as _he          # the registered module object (see build_morning)
    return _plain(_he.home_branch_scorecard(date=None, group=None))


# The cloud pull of Easy POS (backend/cloud_sync.py) reports through the same "stale" kind: a person who muted the
# stuck-sync alert mutes this too. Plain words for the result codes it stores in app_settings['cloud_sync_status'].
CLOUD_LABEL = {"ok": "ปกติ", "failed": "ดึงข้อมูลไม่สำเร็จ", "reconcile_failed": "ข้อมูลที่ดึงมาไม่ผ่านการตรวจเทียบ",
               "locked": "ข้ามรอบ เพราะมีงานซิงก์อื่นทำอยู่", "no_space": "พื้นที่เก็บข้อมูลไม่พอ",
               "creds_missing": "ยังไม่ได้ตั้งรหัสเชื่อมต่อ POS"}
CLOUD_ALERT_RESULTS = ("failed", "no_space", "creds_missing")      # reconcile_failed joins them on its second run in a row
CLOUD_ALERT_MAX_AGE_H = 24                                           # an older failure is the stale alert's business


def _cloud_end(cloud: dict):
    """When the last pull ended, as the naive Bangkok time `now` uses (None if unknown)."""
    try:
        end = datetime.fromisoformat(cloud.get("last_end") or "")
    except (TypeError, ValueError):
        return None
    return end.astimezone(timezone(timedelta(hours=7))).replace(tzinfo=None) if end.tzinfo else end


def cloud_sync_hint(cloud: Optional[dict]) -> str:
    """The cloud half of the stuck-sync message, or the old advice when the cloud pull is not in use."""
    if cloud is None:
        return "การซิงก์อาจค้าง ตรวจที่เครื่องซิงก์หรือตัวเฝ้าระวังงาน"
    res = cloud.get("result")
    if not res:
        return "การดึงข้อมูลบนคลาวด์ยังไม่เคยรัน — ตรวจที่ ตั้งค่า > ข้อมูลจาก POS"
    end = _cloud_end(cloud)
    when = f" (รอบ {end.day} {TH_MON[end.month - 1]} {end:%H:%M})" if end else ""
    return f"การดึงข้อมูลบนคลาวด์: {CLOUD_LABEL.get(res, res)}{when} — ตรวจที่ ตั้งค่า > ข้อมูลจาก POS"


def cloud_sync_payloads(cloud: Optional[dict], now) -> list:
    """[(key, payload)] when the cloud pull failed in a way a person must act on: it failed, the volume is full, the
    MSSQL login is missing, or the reconcile gate refused it twice running (once is a normal late-delivery day).
    Pure like alert_payloads. One push per result per day (the key), kind "stale"."""
    if not cloud or cloud.get("running"):
        return []
    res = cloud.get("result")
    if not (res in CLOUD_ALERT_RESULTS or (res == "reconcile_failed" and (cloud.get("streak") or 0) >= 2)):
        return []
    end = _cloud_end(cloud)
    if end is None or now - end > timedelta(hours=CLOUD_ALERT_MAX_AGE_H):
        return []
    return [(f"stale:cloud:{res}:{now.strftime('%Y-%m-%d')}",
             {"title": "ดึงข้อมูลจาก POS ไม่สำเร็จ", "body": cloud_sync_hint(cloud),
              "url": "/?go=summary", "tag": "oshinei-stale"})]


# The cloud backup of oshinei.db (backend/cloud_backup.py) reports through the same "stale" kind (one switch for
# "something the machine does by itself has stopped"). Its status sits in app_settings['cloud_backup_status'].
BACKUP_LABEL = {"ok": "ปกติ", "failed": "สำรองข้อมูลไม่สำเร็จ", "verify_failed": "ทดสอบกู้คืนไม่ผ่าน",
                "locked": "ข้ามรอบ เพราะมีงานซิงก์อื่นทำอยู่", "no_space": "พื้นที่เก็บข้อมูลไม่พอ",
                "creds_missing": "ยังไม่ได้ตั้งรหัสที่เก็บข้อมูลสำรอง"}
BACKUP_ALERT_RESULTS = ("failed", "verify_failed", "no_space", "creds_missing")


def cloud_backup_payloads(backup: Optional[dict], now) -> list:
    """[(key, payload)] when the daily cloud backup failed, failed its restore test, ran out of space or has no
    storage login. Pure like cloud_sync_payloads. One push per result per day (the key), kind "stale"."""
    if not backup or backup.get("running"):
        return []
    res = backup.get("result")
    if res not in BACKUP_ALERT_RESULTS:
        return []
    end = _cloud_end(backup)
    if end is None or now - end > timedelta(hours=CLOUD_ALERT_MAX_AGE_H):
        return []
    when = f" (รอบ {end.day} {TH_MON[end.month - 1]} {end:%H:%M})"
    return [(f"stale:backup:{res}:{now.strftime('%Y-%m-%d')}",
             {"title": "สำรองข้อมูลไม่สำเร็จ",
              "body": f"การสำรองข้อมูลบนคลาวด์: {BACKUP_LABEL.get(res, res)}{when} — ตรวจที่ ตั้งค่า > สำรองข้อมูล",
              "url": "/?go=summary", "tag": "oshinei-stale"})]


def alert_payloads(sc: dict, now, cloud: Optional[dict] = None) -> list:
    """[(key, payload)] for the conditions in the scorecard right now. Pure: no I/O, so the
    rules are testable. A drop is a branch ≥40% under its 7-day average AND its same weekday last week on the last settled
    day (and big enough to matter); data is stale when that settled day is older than
    yesterday once the early-afternoon sync is done. `cloud` is the stored status of the cloud pull
    (None when that is not in use): the stale message then names what it last did."""
    out = []
    day = (sc or {}).get("date")
    if not day:
        return out
    # both baselines: the 7-day average alone flags every Tuesday (it holds the weekend);
    # same-weekday-last-week alone flags a one-off strong week. A branch with no last-week
    # figure is not judged.
    rows = [b for b in (sc.get("branches") or [])
            if b.get("vs_avg_pct") is not None and b["vs_avg_pct"] <= DROP_PCT
            and b.get("vs_wow_pct") is not None and b["vs_wow_pct"] <= DROP_PCT
            and (b.get("avg7") or 0) >= DROP_MIN_AVG]
    rows.sort(key=lambda b: b["vs_avg_pct"])
    if rows:
        bits = [f"{b['branch']} {b['vs_avg_pct']:.0f}% (฿{_fmt_money(b.get('revenue'))} จากปกติ ฿{_fmt_money(b.get('avg7'))})"
                for b in rows[:3]]
        more = f" และอีก {len(rows) - 3} สาขา" if len(rows) > 3 else ""
        title = (f"{rows[0]['branch']} ยอดตกหนัก ({_th_day(day)})" if len(rows) == 1
                 else f"{len(rows)} สาขายอดตกหนัก ({_th_day(day)})")
        url = f"/?go=branch&b={quote(rows[0]['branch'])}" if len(rows) == 1 else "/?go=branches"
        out.append((f"drop:{day}:" + ",".join(sorted(b["branch"] for b in rows)),
                    {"title": title, "body": " · ".join(bits) + more + " — หรือข้อมูลสาขายังไม่เข้า",
                     "url": url, "tag": "oshinei-drop"}))
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    if now.hour >= STALE_HOUR and day < yesterday:
        gap = (datetime.strptime(yesterday, "%Y-%m-%d") - datetime.strptime(day, "%Y-%m-%d")).days
        out.append((f"stale:{today}",
                    {"title": "ข้อมูลขายยังไม่เข้า",
                     "body": f"ข้อมูลครบถึง {_th_day(day)} (ขาด {gap} วัน) — {cloud_sync_hint(cloud)}",
                     "url": "/?go=summary", "tag": "oshinei-stale"}))
    return out


WEEKLY_MIN_PRIOR = 20000.0     # a branch needs this much ex-VAT in the prior week to be ranked


def weekly_data(con, now=None) -> dict:
    """The Monday–Sunday week that just ended (the latest Sunday before Thai today) vs the
    week before, per branch. {} until the settled day has reached that Sunday — a late sync
    never turns this into a report on an older week."""
    from rules import net_revenue_sum, latest_settled_day
    where = "t.source='Easy' AND b.name!='ALL'"
    settled = latest_settled_day(con, where=where)
    day = settled.get("day")
    if not day:
        return {}
    today = (now or _thai_now()).replace(hour=0, minute=0, second=0, microsecond=0)
    end = today - timedelta(days=((today.weekday() + 1) % 7) or 7)     # the Sunday just gone
    if datetime.strptime(day, "%Y-%m-%d") < end:
        return {}
    start, p_start, p_end = end - timedelta(days=6), end - timedelta(days=13), end - timedelta(days=7)
    f = "%Y-%m-%d"
    rows = con.execute(
        f"""SELECT b.name AS branch,
                   CASE WHEN date(t.sold_at) >= :s THEN 'cur' ELSE 'prev' END AS w,
                   ROUND({net_revenue_sum('t')}, 2) AS rev, SUM(t.customer_count) AS cust, COUNT(*) AS bills
            FROM sales_transaction t JOIN branches b ON b.branch_id = t.branch_id
            WHERE {where} AND date(t.sold_at) BETWEEN :ps AND :e
            GROUP BY b.name, w""",
        {"s": start.strftime(f), "ps": p_start.strftime(f), "e": end.strftime(f)}).fetchall()
    br: dict = {}
    for r in rows:
        br.setdefault(r[0], {})[r[1]] = {"rev": r[2] or 0, "cust": r[3] or 0, "bills": r[4] or 0}
    return {"start": start.strftime(f), "end": end.strftime(f), "prev_start": p_start.strftime(f),
            "prev_end": p_end.strftime(f), "branches": br}


def weekly_payload(data: dict) -> Optional[dict]:
    """Pure: title + body for the weekly push. None without two weeks of data."""
    br = (data or {}).get("branches") or {}
    cur = sum(v.get("cur", {}).get("rev", 0) for v in br.values())
    prev = sum(v.get("prev", {}).get("rev", 0) for v in br.values())
    if not cur or not prev:
        return None
    pct = (cur - prev) / prev * 100
    cust = sum(v.get("cur", {}).get("cust", 0) for v in br.values())
    cust_prev = sum(v.get("prev", {}).get("cust", 0) for v in br.values())
    title = (f"สัปดาห์ที่ผ่านมา ({_th_day(data['start'])} – {_th_day(data['end'])}) "
             f"{_fmt_money(cur)} {'▲' if pct >= 0 else '▼'}{abs(pct):.1f}%")
    bits = []
    if cust_prev:
        cp = (cust - cust_prev) / cust_prev * 100
        bits.append(f"ลูกค้า {int(cust):,} ({'▲' if cp >= 0 else '▼'}{abs(cp):.1f}%)")
    else:
        bits.append(f"ลูกค้า {int(cust):,}")
    ranked = []
    for name, v in br.items():
        c, p = v.get("cur", {}).get("rev", 0), v.get("prev", {}).get("rev", 0)
        if p >= WEEKLY_MIN_PRIOR:
            ranked.append((name, (c - p) / p * 100))
    ranked.sort(key=lambda x: x[1])
    if ranked:
        best, worst = ranked[-1], ranked[0]
        if best[1] > 0:
            bits.append(f"ดีสุด {best[0]} +{best[1]:.0f}%")
        if worst[1] < 0:
            bits.append(f"ต่ำสุด {worst[0]} {worst[1]:.0f}%")
        bits.append(f"{sum(1 for _, x in ranked if x < 0)} จาก {len(ranked)} สาขายอดต่ำกว่าสัปดาห์ก่อน")
    return {"title": title, "body": " · ".join(bits), "url": "/?go=branches", "tag": "oshinei-weekly"}


def maybe_send_weekly(now=None, send=None, force: bool = False, data=None) -> Optional[dict]:
    """Monday 08:30 (until Wednesday) once per week: the week that just ended. Waits for the
    settled day to reach that Sunday; checked every ALERT_EVERY_MIN minutes. Returns the
    stored record, or None when it was not time / already sent / data not complete."""
    now = now or _thai_now()
    if not force and (now.weekday() > 2 or (now.weekday() == 0 and (now.hour, now.minute) < (MORNING_HOUR, MORNING_MINUTE))):
        return None
    with get_db() as con:
        ensure(con)
        stamp = now.strftime("%Y-%m-%d %H:%M")
        last = _meta_get(con, "push_weekly_check")
        if not force and last:
            try:
                if now - datetime.strptime(last, "%Y-%m-%d %H:%M") < timedelta(minutes=ALERT_EVERY_MIN):
                    return None
            except ValueError:
                pass
        _meta_set(con, "push_weekly_check", stamp)
        con.commit()
        try:
            d = data if data is not None else weekly_data(con, now)
        except Exception as e:  # noqa: BLE001
            print(f"[push] weekly: {e}")
            return {"checked": stamp, "error": str(e)}
        if not d or not d.get("start"):
            return None
        if not force and _meta_get(con, "push_weekly_week") == d["start"]:
            return None
        payload = weekly_payload(d)
        if not payload:
            return None
        _meta_set(con, "push_weekly_week", d["start"])        # claim first, like the morning guard
        con.commit()
        rec = {"week": d["start"], "generated_at": stamp, "payload": payload}
        c = vapid_config()
        if c["configured"]:
            r = send_to_all(con, payload, send=send, chain_only=True, kind="weekly")
            rec.update({k: r[k] for k in ("targets", "sent", "gone", "failed", "muted")})
        _meta_set(con, "push_weekly", json.dumps(rec, ensure_ascii=False))
        con.commit()
    print(f"[push] weekly {rec['week']}: {rec.get('sent')}/{rec.get('targets')} sent")
    return rec


# ── วัตถุดิบและใบสั่งซื้อ: reminders that reach the people who own them (batch 3, 2026-10-12) ─────────────────
# Kind "supply". Three sends, each claimed first in usage_meta (one per day, so one per user per day):
#   push_supply_day      08:00  the branch's morning line to the devices of that branch's accounts; the warehouse, Sales
#                               Support, Operations and Accounting get the lines they own (bom_center/reminders.py)
#   push_supply_pre_day  11:00  "ส่งใบสั่งซื้อรอบส่ง… ภายในวันนี้ 13:15 (อีก 2 ชม.)" to each branch whose cutoff is today and
#                               whose order is not yet submitted
#   push_supply_cut_day  13:15  the branches that missed the cutoff, to Sales Support only
# A tap opens the page (url /?go=supply&tab=…&pane=…, js/01-core.js supplyDeepLink).
def _devices_by_user(con) -> dict:
    """{username: (role, branch)} for every active login that holds at least one device."""
    try:
        rows = con.execute("SELECT DISTINCT u.username, u.role, COALESCE(u.branch, '') FROM push_subscription s"
                           " JOIN app_user u ON u.username = s.username WHERE u.active = 1").fetchall()
    except Exception:  # noqa: BLE001 — sign-in off, no user table
        return {}
    return {r[0]: (r[1], r[2]) for r in rows}


def _supply_claim(con, key: str, day: str, force: bool) -> bool:
    if not force and _meta_get(con, key) == day:
        return False
    _meta_set(con, key, day)                 # claim first: two ticks never send twice
    con.commit()
    return True


def _supply_rec(con, meta: str, rec: dict) -> dict:
    _meta_set(con, meta, json.dumps(rec, ensure_ascii=False))
    con.commit()
    return rec


def _send_groups(con, groups: list, send, rec: dict) -> None:
    """groups = [(label, usernames, payload)] → one send_to_all per group; the totals go into rec."""
    rec.setdefault("groups", [])
    for label, users, payload in groups:
        r = send_to_all(con, payload, send=send, only_users=users, kind="supply")
        con.commit()
        rec["groups"].append({"label": label, "users": len(users), **{k: r[k] for k in ("targets", "sent", "gone", "failed", "muted")}})
    for k in ("targets", "sent", "gone", "failed", "muted"):
        rec[k] = sum(g[k] for g in rec["groups"])


def maybe_send_supply(now=None, send=None, force: bool = False) -> Optional[dict]:
    """08:00 Thai, once a day: each branch account's line (nothing on a day with nothing due or open) and the centre
    roles' lines. Returns the stored record, or None when it was not time / already done."""
    now = now or _thai_now()
    if not force and (now.hour, now.minute) < SUPPLY_MORNING:
        return None
    day = now.strftime("%Y-%m-%d")
    with get_db() as con:
        ensure(con)
        if not _supply_claim(con, "push_supply_day", day, force):
            return None
        c = vapid_config()
        rec = {"day": day, "generated_at": now.strftime("%Y-%m-%d %H:%M"), "configured": c["configured"], "groups": []}
        try:
            devices = _devices_by_user(con) if c["configured"] else {}
            if devices:
                from bom_center import reminders as RM
                from bom_center.pilot import branch_accounts
                groups = []
                by_branch = {b["name"]: b for b in branch_accounts(con)}
                for name, br in sorted(by_branch.items()):
                    users = sorted(u for u, (role, b) in devices.items() if role == "branch" and b == name)
                    if not users:
                        continue
                    try:
                        payload = RM.branch_morning(con, br, today=now.date(), now=now)
                    except Exception as e:  # noqa: BLE001 — one branch never stops the others
                        rec.setdefault("errors", []).append(f"{name}: {e}")
                        continue
                    if payload:
                        groups.append((name, users, payload))
                if any(role in RM.CENTRE_ROLES for role, _ in devices.values()):
                    centre = RM.centre_morning(con, today=now.date(), now=now)
                    for role, v in sorted(centre.items()):
                        users = sorted(u for u, (r, _) in devices.items() if r == role)
                        if users:
                            groups.append((role, users, RM.centre_payload(role, v)))
                _send_groups(con, groups, send, rec)
        except Exception as e:  # noqa: BLE001
            rec["error"] = str(e)
            print(f"[push] supply: {e}")
        _supply_rec(con, "push_supply", rec)
    print(f"[push] supply {day}: {rec.get('sent', 0)}/{rec.get('targets', 0)} sent" + (f" ({rec['error']})" if rec.get("error") else ""))
    return rec


def maybe_send_precutoff(now=None, send=None, force: bool = False) -> Optional[dict]:
    """11:00 Thai (until the 13:15 cutoff), once a day: the branches whose cutoff is today and whose order is not yet
    submitted are told how long is left. Returns the stored record, or None when it was not time / already done."""
    now = now or _thai_now()
    if not force and not (PRECUTOFF_AT <= (now.hour, now.minute) < CUTOFF_AT):
        return None
    day = now.strftime("%Y-%m-%d")
    with get_db() as con:
        ensure(con)
        if not _supply_claim(con, "push_supply_pre_day", day, force):
            return None
        c = vapid_config()
        rec = {"day": day, "generated_at": now.strftime("%Y-%m-%d %H:%M"), "configured": c["configured"], "groups": []}
        try:
            devices = _devices_by_user(con) if c["configured"] else {}
            if devices:
                from bom_center import reminders as RM
                from bom_center.pilot import branch_accounts
                groups = []
                for br in branch_accounts(con):
                    users = sorted(u for u, (role, b) in devices.items() if role == "branch" and b == br["name"])
                    if not users:
                        continue
                    try:
                        payload = RM.branch_precutoff(con, br, today=now.date(), now=now)
                    except Exception as e:  # noqa: BLE001
                        rec.setdefault("errors", []).append(f"{br['name']}: {e}")
                        continue
                    if payload:
                        groups.append((br["name"], users, payload))
                _send_groups(con, groups, send, rec)
        except Exception as e:  # noqa: BLE001
            rec["error"] = str(e)
            print(f"[push] pre-cutoff: {e}")
        _supply_rec(con, "push_supply_pre", rec)
    print(f"[push] pre-cutoff {day}: {rec.get('sent', 0)}/{rec.get('targets', 0)} sent")
    return rec


def maybe_send_cutoff(now=None, send=None, force: bool = False) -> Optional[dict]:
    """13:15 Thai (until 20:00), once a day: the branches that missed today's cutoff, to Sales Support only."""
    now = now or _thai_now()
    if not force and not (CUTOFF_AT <= (now.hour, now.minute) and now.hour < CUTOFF_UNTIL_HOUR):
        return None
    day = now.strftime("%Y-%m-%d")
    with get_db() as con:
        ensure(con)
        if not _supply_claim(con, "push_supply_cut_day", day, force):
            return None
        c = vapid_config()
        rec = {"day": day, "generated_at": now.strftime("%Y-%m-%d %H:%M"), "configured": c["configured"], "groups": []}
        try:
            devices = _devices_by_user(con) if c["configured"] else {}
            users = sorted(u for u, (role, _) in devices.items() if role == "salessupport")
            if users:
                from bom_center import reminders as RM
                payload = RM.cutoff_payload(RM.cutoff(con, today=now.date(), now=RM._aware(now)))
                if payload:
                    _send_groups(con, [("salessupport", users, payload)], send, rec)
        except Exception as e:  # noqa: BLE001
            rec["error"] = str(e)
            print(f"[push] cutoff: {e}")
        _supply_rec(con, "push_supply_cut", rec)
    print(f"[push] cutoff {day}: {rec.get('sent', 0)}/{rec.get('targets', 0)} sent")
    return rec


def cash_discount_rows(con) -> dict:
    """The last COMPLETE day of pos_cashcount_day (≥ 60% of the busiest recent day's branches
    reported) with each branch's cash over/short, sales, discount and its own 28-day discount
    rate. {} when the table is missing or empty."""
    try:
        days = con.execute(
            "SELECT day, COUNT(*) n FROM pos_cashcount_day GROUP BY day ORDER BY day DESC LIMIT 14").fetchall()
    except Exception:
        return {}
    if not days:
        return {}
    top = max(r[1] for r in days)
    day = next((r[0] for r in days if r[1] >= 0.6 * top), None)
    if not day:
        return {}
    rows = con.execute(
        """WITH base AS (SELECT branch_id, SUM(discount) * 1.0 / NULLIF(SUM(total_sales), 0) r
                         FROM pos_cashcount_day WHERE day >= date(:d, '-28 day') AND day < :d GROUP BY branch_id)
           SELECT b.name, x.over_short, x.total_sales, x.discount, base.r
           FROM pos_cashcount_day x JOIN branches b ON b.branch_id = x.branch_id
           LEFT JOIN base ON base.branch_id = x.branch_id
           WHERE x.day = :d AND b.name != 'ALL'""", {"d": day}).fetchall()
    return {"day": day, "branches": [
        {"branch": r[0], "over_short": r[1] or 0, "sales": r[2] or 0, "discount": r[3] or 0, "base_rate": r[4]}
        for r in rows]}


def cash_discount_payloads(data: dict) -> list:
    """[(key, payload)] — pure, like alert_payloads. A branch's cash count short by
    CASH_SHORT_MIN+, and a branch-day discount that is high in itself AND against its own
    history. Positive overs are ignored: the big ones in the data are entry errors."""
    out = []
    day = (data or {}).get("day")
    rows = (data or {}).get("branches") or []
    if not day:
        return out

    def url(rs):
        return f"/?go=branch&b={quote(rs[0]['branch'])}" if len(rs) == 1 else "/?go=branches"

    short = sorted([b for b in rows if b["over_short"] <= -CASH_SHORT_MIN], key=lambda b: b["over_short"])
    if short:
        bits = [f"{b['branch']} ขาด ฿{_fmt_money(-b['over_short'])}" for b in short[:3]]
        more = f" และอีก {len(short) - 3} สาขา" if len(short) > 3 else ""
        title = (f"{short[0]['branch']} เงินสดขาด ({_th_day(day)})" if len(short) == 1
                 else f"{len(short)} สาขาเงินสดขาด ({_th_day(day)})")
        out.append((f"cash:{day}:" + ",".join(sorted(b["branch"] for b in short)),
                    {"title": title, "body": " · ".join(bits) + more + " — ตรวจการนับเงินสดของวันนั้น",
                     "url": url(short), "tag": "oshinei-cash"}))
    hi = []
    for b in rows:
        s, d, br = b["sales"], b["discount"], b["base_rate"]
        if s and d >= DISC_MIN_BAHT and br is not None and d / s * 100 >= DISC_RATE_MIN and d / s >= DISC_MULT * br:
            hi.append({**b, "rate": d / s * 100, "base_pct": br * 100})
    hi.sort(key=lambda b: -b["discount"])
    if hi:
        bits = [f"{b['branch']} ลด {b['rate']:.1f}% (ปกติ {b['base_pct']:.1f}%) ฿{_fmt_money(b['discount'])}" for b in hi[:3]]
        more = f" และอีก {len(hi) - 3} สาขา" if len(hi) > 3 else ""
        title = (f"{hi[0]['branch']} ส่วนลดสูงผิดปกติ ({_th_day(day)})" if len(hi) == 1
                 else f"{len(hi)} สาขาส่วนลดสูงผิดปกติ ({_th_day(day)})")
        out.append((f"disc:{day}:" + ",".join(sorted(b["branch"] for b in hi)),
                    {"title": title, "body": " · ".join(bits) + more,
                     "url": url(hi) if len(hi) == 1 else "/?go=discounts", "tag": "oshinei-disc"}))
    return out


def maybe_send_alerts(now=None, send=None, force: bool = False, scorecard=None, cashdata=None) -> Optional[dict]:
    """Every ALERT_EVERY_MIN minutes from ALERT_FROM_HOUR: push each alert not yet sent.
    Returns {"checked", "sent": [keys], ...} or None when it was not time."""
    now = now or _thai_now()
    if not force and now.hour < ALERT_FROM_HOUR:
        return None
    with get_db() as con:
        ensure(con)
        last = _meta_get(con, "push_alert_check")
        stamp = now.strftime("%Y-%m-%d %H:%M")
        if not force and last:
            try:
                if now - datetime.strptime(last, "%Y-%m-%d %H:%M") < timedelta(minutes=ALERT_EVERY_MIN):
                    return None
            except ValueError:
                pass
        _meta_set(con, "push_alert_check", stamp)    # claim first, like the morning guard
        con.commit()
        rec = {"checked": stamp, "sent": []}
        cloud = None
        try:                                          # the cloud pull's status, only while that is switched on
            import cloud_sync as _cloud_sync
            if _cloud_sync.enabled():
                cloud = _cloud_sync.status_from_con(con)
        except Exception as e:  # noqa: BLE001
            print(f"[push] alerts cloud status: {e}")
        backup = None
        try:                                          # the cloud backup's status, only while that is switched on
            import cloud_backup as _cloud_backup
            if _cloud_backup.enabled():
                backup = _cloud_backup.status_from_con(con)
        except Exception as e:  # noqa: BLE001
            print(f"[push] alerts backup status: {e}")
        try:
            sc = scorecard if scorecard is not None else read_scorecard()
            todo = alert_payloads(sc, now, cloud=cloud)
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"scorecard: {e}"
            print(f"[push] alerts: {e}")
            todo = []
        try:
            todo += cloud_sync_payloads(cloud, now)
        except Exception as e:  # noqa: BLE001
            print(f"[push] alerts cloud: {e}")
        try:
            todo += cloud_backup_payloads(backup, now)
        except Exception as e:  # noqa: BLE001
            print(f"[push] alerts backup: {e}")
        try:                                          # a failure in one source never hides the other
            todo += cash_discount_payloads(cashdata if cashdata is not None else cash_discount_rows(con))
        except Exception as e:  # noqa: BLE001
            rec["error_cash"] = str(e)
            print(f"[push] alerts cash: {e}")
        if not todo and rec.get("error") and rec.get("error_cash"):
            return rec
        sent_keys = json.loads(_meta_get(con, "push_alerts_sent") or "[]")
        c = vapid_config()
        for key, payload in todo:
            if key in sent_keys:
                continue
            if c["configured"]:
                r = send_to_all(con, payload, send=send, chain_only=True, kind=key.split(":")[0])
                rec.setdefault("results", {})[key] = {k: r[k] for k in ("targets", "sent", "gone", "failed", "muted")}
            sent_keys.append(key)                    # recorded even with no device: no flood when one appears
            rec["sent"].append(key)
            print(f"[push] alert {key}: {payload['title']}")
        _meta_set(con, "push_alerts_sent", json.dumps(sent_keys[-60:], ensure_ascii=False))
        _meta_set(con, "push_alerts", json.dumps(rec, ensure_ascii=False))
        con.commit()
    return rec
