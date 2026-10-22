"""PR-F4 (owner 2026-10-22 decision 4 "การตอบกลับ") - the franchise owner writes to head office and reads the replies.

Endpoints (all under /api/franchise/messages; auth rule in auth.MODULE_RULES):

  owner (role franchise, ร้านของฉัน)
    GET  /api/franchise/messages                 its own branch's threads, newest first, + the topic list and the unread count
    POST /api/franchise/messages                 a new question {topic, body, context?}
  head office (a role holding HQ_MODULE: management and operation by default, auth.ROLE_MODULES)
    GET  /api/franchise/messages/inbox           every franchise thread [?branch_id=&status=open|closed|all] + per-branch counts
    POST /api/franchise/messages/{id}/status     {status: open|closed} - close or reopen a thread
  both sides
    GET  /api/franchise/messages/{id}            the thread and its messages; marks the reader's side read
    POST /api/franchise/messages/{id}/reply      {body}

Security model
  * Who: the live account (app_user, read per request like main.enforce_auth). A franchise login is an OWNER: its branch is the
    signed session's (auth.scope_branch) and must equal the account's branch; never a body or query field. A franchise account
    without a branch is refused (403 X-Denied: role). Any other role is HEAD OFFICE when it holds HQ_MODULE, else refused.
    An owner opening, answering or naming another branch's thread gets 403 X-Denied: role.
  * Writes are session writes: the auth middleware has already confined the login to the rule for this prefix, and the
    handler re-checks the side, so the write token (X-Upload-Token) adds nothing and is not asked for - the franchise owner
    never needs or sees the unlock code. With sign-in OFF (local development) there is no identity, so writes fall back to
    core.require_write_token (open locally, as every other write) and the owner side must name branch_id.
  * Input: plain text only. NFC, control characters stripped (a new line stays), at most MAX_BODY characters, stored as text.
    The pages draw every user string through esc() / textContent, never innerHTML of raw text. Topics come from TOPICS only;
    the optional context is a whitelisted {level, month, line} (line capped at MAX_CONTEXT). No file uploads.
  * Rate limit: DAILY_LIMIT_OWNER messages per owner login per Thai day (a new thread counts), DAILY_LIMIT_HQ per centre login.
  * Audit: every message keeps author login + display name + role + time; threads keep created_by / closed_by. Messages are
    immutable (no endpoint edits or deletes one, and two triggers refuse an UPDATE or DELETE). Head office may close a
    thread and reopen it; the owner cannot (it starts a new question).
  * What the owner receives: its own threads only; authors as display names or Thai role labels (auth.display_name_of),
    never a login id; no branch list, no other branch, no internal field (author_user, created_by, closed_by stay server side).
  * Push (routers/push_api.py): a new owner message -> kind "franchise_msg" to the devices of push_api.FRANCHISE_MSG_ROLES
    (management); a head-office reply -> kind "franchise_reply" to that branch's franchise logins' devices only. The text says
    who and which topic, never a figure of the chain and never the message itself.

Tables (created idempotently on first use; both carried across the sync's DB swap by name, core._PRESERVED_EXACT):
  franchise_msg_thread   id, branch_id, topic, context (JSON text), status open|closed, created_at, created_by, last_at,
                         unread_hq / unread_owner (messages the other side has not opened yet), closed_at, closed_by
  franchise_msg_message  id, thread_id, side owner|hq, author_user, author_name, author_role, body, created_at
"""
import json
import re
import threading
import unicodedata
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

import auth as _auth
from core import get_db, require_write_token

router = APIRouter()

# ── the owner's defaults, one place ──────────────────────────────────────────
HQ_MODULE = "franchise.inbox"          # who reads and answers the franchise threads (auth.ROLE_MODULES: management + operation)
MAX_BODY = 1000                        # characters of one message
MAX_CONTEXT = 300                      # characters of the verdict line a question refers to
DAILY_LIMIT_OWNER = 20                 # messages per owner login per Thai day (a new thread counts)
DAILY_LIMIT_HQ = 200                   # messages per centre login per Thai day
TOPICS = [("sales", "ยอดขาย"), ("cost", "ต้นทุนและวัตถุดิบ"), ("orders", "ใบสั่งซื้อและเคลม"), ("controls", "การควบคุม"),
          ("other", "อื่น ๆ")]
TOPIC_TH = dict(TOPICS)
LEVEL_TH = {"good": "ดี", "watch": "ควรดู", "fix": "ต้องแก้"}
LIST_LIMIT = 200
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩﻿]")

_PUSH_SEND = None      # tests: a send(sub_info, data) -> status callable; the push then runs in the request thread


def _now() -> datetime:
    return datetime.utcnow() + timedelta(hours=7)          # Thai clock, as push_api._thai_now


def _stamp(dt: Optional[datetime] = None) -> str:
    return (dt or _now()).strftime("%Y-%m-%d %H:%M:%S")


# ── storage ──────────────────────────────────────────────────────────────────
def ensure(con) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS franchise_msg_thread(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        branch_id INTEGER NOT NULL,
        topic TEXT NOT NULL,
        context TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL DEFAULT '',
        last_at TEXT NOT NULL,
        unread_hq INTEGER NOT NULL DEFAULT 0,
        unread_owner INTEGER NOT NULL DEFAULT 0,
        closed_at TEXT,
        closed_by TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS franchise_msg_message(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id INTEGER NOT NULL REFERENCES franchise_msg_thread(id),
        side TEXT NOT NULL CHECK (side IN ('owner', 'hq')),
        author_user TEXT NOT NULL DEFAULT '',
        author_name TEXT NOT NULL DEFAULT '',
        author_role TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL,
        created_at TEXT NOT NULL)""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_fmsg_thread_branch ON franchise_msg_thread(branch_id, last_at)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_fmsg_message_thread ON franchise_msg_message(thread_id, id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_fmsg_message_author ON franchise_msg_message(author_user, created_at)")
    # a message is a record: nothing edits or removes one (the swap's carry-forward DROPs and re-creates the table, which fires neither)
    con.execute("""CREATE TRIGGER IF NOT EXISTS trg_fmsg_message_no_update BEFORE UPDATE ON franchise_msg_message
                   BEGIN SELECT RAISE(ABORT, 'franchise messages are immutable'); END""")
    con.execute("""CREATE TRIGGER IF NOT EXISTS trg_fmsg_message_no_delete BEFORE DELETE ON franchise_msg_message
                   BEGIN SELECT RAISE(ABORT, 'franchise messages are immutable'); END""")


# ── input ────────────────────────────────────────────────────────────────────
def clean_text(s, cap: int, field: str = "ข้อความ", required: bool = True) -> str:
    """Plain text: NFC, CRLF -> LF, tabs -> a space, control and direction-override characters removed, at most two blank
    lines in a row, trimmed. Over `cap` characters is refused (never cut: the writer must see what was not sent)."""
    t = unicodedata.normalize("NFC", str(s or ""))
    t = t.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    t = _CTRL.sub("", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if required and not t:
        raise HTTPException(400, f"กรุณากรอก{field}")
    if len(t) > cap:
        raise HTTPException(400, f"{field}ยาวเกิน {cap:,} ตัวอักษร (ขณะนี้ {len(t):,} ตัวอักษร)")
    return t


def clean_context(ctx) -> str:
    """The optional link of a new question to a line of ร้านของฉัน: {level, month, line}, every field checked. -> JSON text or ''."""
    if not ctx:
        return ""
    if not isinstance(ctx, dict):
        raise HTTPException(400, "รูปแบบเรื่องที่อ้างถึงไม่ถูกต้อง")
    level = str(ctx.get("level") or "")
    month = str(ctx.get("month") or "")
    if level and level not in LEVEL_TH:
        raise HTTPException(400, "รูปแบบเรื่องที่อ้างถึงไม่ถูกต้อง")
    if month and not MONTH_RE.match(month):
        raise HTTPException(400, "รูปแบบเดือนที่อ้างถึงไม่ถูกต้อง")
    line = clean_text(ctx.get("line"), MAX_CONTEXT, "เรื่องที่อ้างถึง", required=False).replace("\n", " ")
    if not (level or month or line):
        return ""
    return json.dumps({"level": level, "month": month, "line": line}, ensure_ascii=False)


# ── who is asking ────────────────────────────────────────────────────────────
def _deny(msg: str):
    raise HTTPException(403, detail=msg, headers={"X-Denied": "role"})


def caller(request: Request, branch_id: Optional[int] = None) -> dict:
    """-> {side 'owner'|'hq', user, role, name, branch_id, branch}. Sign-in off: owner when branch_id is given, else head office."""
    if not _auth.auth_enabled():
        if branch_id is not None:
            with get_db() as con:
                r = con.execute("SELECT branch_id, name FROM branches WHERE branch_id=? AND name <> 'ALL'", (int(branch_id),)).fetchone()
            if not r:
                raise HTTPException(404, "ไม่พบสาขา")
            return {"side": "owner", "user": "", "role": "franchise", "name": f"เจ้าของสาขา{r[1]}", "branch_id": int(r[0]), "branch": r[1]}
        return {"side": "hq", "user": "", "role": "management", "name": _auth.ROLE_TH["management"], "branch_id": None, "branch": None}
    payload = _auth.parse_token(request.cookies.get(_auth.SESSION_COOKIE, "")) or {}
    user = str(payload.get("u") or "")
    with get_db() as con:
        row = _auth.get_user(con, user) if user else None
        if not row or not row["active"]:
            raise HTTPException(401, "Authentication required.")
        role = row["role"]
        name = _auth.display_name_of(row)
        if role == "franchise":
            scoped = _auth.scope_branch(request)
            live = (row["branch"] or "") if "branch" in row.keys() else ""
            if not scoped or not live:
                _deny("บัญชีนี้ยังไม่ได้ผูกกับสาขา กรุณาแจ้งผู้ดูแลระบบ")
            if scoped != live:
                _deny("สาขาของบัญชีนี้เปลี่ยนแล้ว กรุณาเข้าสู่ระบบใหม่")
            r = con.execute("SELECT branch_id, name FROM branches WHERE name=?", (scoped,)).fetchone()
            if not r:
                _deny("บัญชีนี้ผูกกับสาขาที่ไม่พบในระบบ กรุณาแจ้งผู้ดูแลระบบ")
            if branch_id is not None and int(branch_id) != int(r[0]):
                _deny("บัญชีเจ้าของสาขาดูได้เฉพาะสาขาของตนเอง")
            return {"side": "owner", "user": user, "role": role, "name": name, "branch_id": int(r[0]), "branch": r[1]}
        if role not in _auth.PINNED_ROLES and HQ_MODULE in _auth.modules_for_role(role):
            return {"side": "hq", "user": user, "role": role, "name": name, "branch_id": None, "branch": None}
    _deny("บัญชีนี้ไม่มีสิทธิ์ใช้ข้อความกับเจ้าของสาขา")


def _owner(request: Request, branch_id: Optional[int] = None) -> dict:
    who = caller(request, branch_id)
    if who["side"] != "owner":
        if not _auth.auth_enabled():
            raise HTTPException(400, "กรุณาเลือกสาขา")
        _deny("หน้านี้สำหรับเจ้าของสาขา")
    return who


def _hq(request: Request) -> dict:
    who = caller(request)
    if who["side"] != "hq":
        _deny("หน้านี้สำหรับสำนักงานใหญ่")
    return who


def _write_gate(request: Request) -> None:
    """Sign-in on: the session is the gate (see the module docstring). Sign-in off: the ordinary write token."""
    if not _auth.auth_enabled():
        require_write_token(request, request.headers.get("X-Upload-Token"))


def _rate_limit(con, who: dict) -> None:
    if not _auth.auth_enabled():
        return
    cap = DAILY_LIMIT_OWNER if who["side"] == "owner" else DAILY_LIMIT_HQ
    since = _now().strftime("%Y-%m-%d 00:00:00")
    n = con.execute("SELECT COUNT(*) FROM franchise_msg_message WHERE author_user=? AND created_at >= ?", (who["user"], since)).fetchone()[0]
    if n >= cap:
        raise HTTPException(429, f"ส่งข้อความครบ {cap} ข้อความของวันนี้แล้ว กรุณาส่งใหม่ในวันถัดไป")


# ── shaping the answers ──────────────────────────────────────────────────────
def _ctx(text: str) -> Optional[dict]:
    if not text:
        return None
    try:
        c = json.loads(text)
    except ValueError:
        return None
    c["level_th"] = LEVEL_TH.get(c.get("level") or "", "")
    return c


def _thread_out(r, side: str, branch_names: Optional[dict] = None, last: Optional[dict] = None) -> dict:
    """One thread for the reader of `side`. The owner never gets the branch list or another side's internal fields."""
    out = {"id": r["id"], "topic": r["topic"], "topic_th": TOPIC_TH.get(r["topic"], r["topic"]), "status": r["status"],
           "created_at": r["created_at"], "last_at": r["last_at"], "context": _ctx(r["context"]),
           "unread": int(r["unread_owner"] if side == "owner" else r["unread_hq"]),
           "closed_at": r["closed_at"]}
    if side == "hq":
        out["branch_id"] = r["branch_id"]
        out["branch"] = (branch_names or {}).get(r["branch_id"], "")
        out["waiting_owner"] = int(r["unread_owner"])          # replies the owner has not opened yet
    if last:
        out["last"] = last
    return out


def _msg_out(m) -> dict:
    return {"id": m["id"], "side": m["side"], "author": m["author_name"] or _auth.ROLE_TH.get(m["author_role"], ""),
            "body": m["body"], "created_at": m["created_at"]}


def _last_lines(con, ids) -> dict:
    """{thread id: {side, author, body (first 120 characters), created_at}} of the newest message of each thread."""
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    rows = con.execute(f"""SELECT m.* FROM franchise_msg_message m JOIN (SELECT thread_id, MAX(id) mid FROM franchise_msg_message
                           WHERE thread_id IN ({ph}) GROUP BY thread_id) x ON x.mid = m.id""", tuple(ids)).fetchall()
    out = {}
    for m in rows:
        d = _msg_out(m)
        d["body"] = d["body"][:120]
        out[m["thread_id"]] = d
    return out


def _thread_row(con, tid: int, who: dict):
    r = con.execute("SELECT * FROM franchise_msg_thread WHERE id=?", (int(tid),)).fetchone()
    if not r:
        raise HTTPException(404, "ไม่พบหัวข้อนี้")
    if who["side"] == "owner" and int(r["branch_id"]) != int(who["branch_id"]):
        _deny("บัญชีเจ้าของสาขาดูได้เฉพาะข้อความของสาขาตนเอง")
    return r


def _branch_names(con) -> dict:
    return {int(r[0]): r[1] for r in con.execute("SELECT branch_id, name FROM branches").fetchall()}


def unread_count(con, who: dict) -> int:
    """Threads with a message the reader's side has not opened (the badge on หน้าแรก / the inbox tile)."""
    ensure(con)
    if who["side"] == "owner":
        return con.execute("SELECT COUNT(*) FROM franchise_msg_thread WHERE branch_id=? AND unread_owner > 0", (who["branch_id"],)).fetchone()[0]
    return con.execute("SELECT COUNT(*) FROM franchise_msg_thread WHERE unread_hq > 0").fetchone()[0]


def badge_counts(con, role: str, modules, branch: Optional[str]) -> dict:
    """{page key: n} for /api/nav/badges: 'franchise' = unread replies of a franchise login, 'fmsg' = unread owner threads of HQ."""
    try:
        if role == "franchise":
            if not branch:
                return {}
            r = con.execute("SELECT branch_id FROM branches WHERE name=?", (branch,)).fetchone()
            if not r:
                return {}
            n = unread_count(con, {"side": "owner", "branch_id": int(r[0])})
            return {"franchise": n} if n else {}
        if role in _auth.PINNED_ROLES or HQ_MODULE not in set(modules or ()):
            return {}
        n = unread_count(con, {"side": "hq"})
        return {"fmsg": n} if n else {}
    except Exception as e:      # pragma: no cover - a badge never breaks the menu
        print(f"[franchise_msgs] badge: {e}")
        return {}


# ── push ─────────────────────────────────────────────────────────────────────
def hq_push_users(con) -> list:
    """The logins whose devices hear of a new owner message: active accounts of push_api.FRANCHISE_MSG_ROLES holding a device."""
    from . import push_api as _P
    return sorted(u for u, (role, _b) in _P._devices_by_user(con).items() if role in _P.FRANCHISE_MSG_ROLES)


def owner_push_users(con, branch: str) -> list:
    """The franchise logins of `branch` holding a device: a reply is sent to them and to nobody else."""
    from . import push_api as _P
    return sorted(u for u, (role, b) in _P._devices_by_user(con).items() if role == _P.FRANCHISE_ROLE and b == branch)


def push_new_message(branch: str, tid: int, topic: str) -> dict:
    return {"title": "Oshinei · ข้อความจากเจ้าของสาขา", "body": f"สาขา{branch} · {TOPIC_TH.get(topic, topic)}",
            "url": f"/?go=fmsg&t={int(tid)}", "tag": f"oshinei-fmsg-{int(tid)}"}


def push_reply(tid: int, topic: str) -> dict:
    return {"title": "Oshinei · สำนักงานใหญ่ตอบข้อความ", "body": f"หัวข้อ {TOPIC_TH.get(topic, topic)} · เปิดอ่านได้ที่ร้านของฉัน",
            "url": f"/?go=franchise&t={int(tid)}", "tag": f"oshinei-freply-{int(tid)}"}


def _push(kind: str, users_fn, payload: dict) -> None:
    """Send in the background (a push service may be slow); never fails the request."""
    from . import push_api as _P

    def run(send=None):
        try:
            with get_db() as con:
                _P.ensure(con)
                users = users_fn(con)
                if users:
                    _P.send_to_all(con, payload, send=send, only_users=users, kind=kind)
                    con.commit()
        except Exception as e:  # noqa: BLE001
            print(f"[franchise_msgs] push {kind}: {e}")
    if _PUSH_SEND is not None:
        run(_PUSH_SEND)
        return
    if not _P.vapid_config()["configured"]:
        return
    threading.Thread(target=run, daemon=True, name=f"fmsg-push-{kind}").start()


# ── endpoints ────────────────────────────────────────────────────────────────
class _NewBody(BaseModel):
    topic: str
    body: str
    context: Optional[dict] = None


class _ReplyBody(BaseModel):
    body: str


class _StatusBody(BaseModel):
    status: str


def _insert_message(con, tid: int, who: dict, body: str, at: str) -> int:
    cur = con.execute("""INSERT INTO franchise_msg_message(thread_id, side, author_user, author_name, author_role, body, created_at)
                         VALUES (?, ?, ?, ?, ?, ?, ?)""", (tid, who["side"], who["user"], who["name"], who["role"], body, at))
    return int(cur.lastrowid)


@router.get("/api/franchise/messages")
def fmsg_owner_list(request: Request, branch_id: Optional[int] = Query(None)):
    who = _owner(request, branch_id)
    with get_db() as con:
        ensure(con)
        rows = con.execute("""SELECT * FROM franchise_msg_thread WHERE branch_id=?
                              ORDER BY (status='open') DESC, last_at DESC, id DESC LIMIT ?""", (who["branch_id"], LIST_LIMIT)).fetchall()
        last = _last_lines(con, [r["id"] for r in rows])
        threads = [_thread_out(r, "owner", last=last.get(r["id"])) for r in rows]
    return {"topics": [{"id": k, "label": v} for k, v in TOPICS], "max_body": MAX_BODY, "threads": threads,
            "unread": sum(1 for t in threads if t["unread"])}


@router.post("/api/franchise/messages")
def fmsg_owner_new(body: _NewBody, request: Request, branch_id: Optional[int] = Query(None)):
    _write_gate(request)
    who = _owner(request, branch_id)
    if body.topic not in TOPIC_TH:
        raise HTTPException(400, "กรุณาเลือกหัวข้อ")
    text = clean_text(body.body, MAX_BODY)
    ctx = clean_context(body.context)
    at = _stamp()
    with get_db() as con:
        ensure(con)
        _rate_limit(con, who)
        cur = con.execute("""INSERT INTO franchise_msg_thread(branch_id, topic, context, status, created_at, created_by, last_at,
                                                             unread_hq, unread_owner)
                             VALUES (?, ?, ?, 'open', ?, ?, ?, 1, 0)""", (who["branch_id"], body.topic, ctx, at, who["user"], at))
        tid = int(cur.lastrowid)
        _insert_message(con, tid, who, text, at)
        con.commit()
        r = con.execute("SELECT * FROM franchise_msg_thread WHERE id=?", (tid,)).fetchone()
        out = _thread_out(r, "owner")
    _push("franchise_msg", hq_push_users, push_new_message(who["branch"], tid, body.topic))
    return {"ok": True, "thread": out}


@router.get("/api/franchise/messages/inbox")
def fmsg_inbox(request: Request, branch_id: Optional[int] = Query(None), status: str = Query("open", pattern="^(open|closed|all)$")):
    _hq(request)
    with get_db() as con:
        ensure(con)
        names = _branch_names(con)
        q, params = "SELECT * FROM franchise_msg_thread", []
        cond = []
        if branch_id is not None:
            cond.append("branch_id = ?")
            params.append(int(branch_id))
        if status != "all":
            cond.append("status = ?")
            params.append(status)
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY (unread_hq > 0) DESC, last_at DESC, id DESC LIMIT ?"
        params.append(LIST_LIMIT)
        rows = con.execute(q, tuple(params)).fetchall()
        last = _last_lines(con, [r["id"] for r in rows])
        threads = [_thread_out(r, "hq", names, last.get(r["id"])) for r in rows]
        per = con.execute("""SELECT branch_id, SUM(status='open') open_n, SUM(status='closed') closed_n, SUM(unread_hq > 0) unread_n
                             FROM franchise_msg_thread GROUP BY branch_id""").fetchall()
        counts = con.execute("""SELECT SUM(status='open'), SUM(status='closed'), COUNT(*), SUM(unread_hq > 0)
                                FROM franchise_msg_thread""" + (" WHERE branch_id = ?" if branch_id is not None else ""),
                             (int(branch_id),) if branch_id is not None else ()).fetchone()
    branches = sorted(({"branch_id": int(p["branch_id"]), "branch": names.get(int(p["branch_id"]), ""), "open": int(p["open_n"] or 0),
                        "closed": int(p["closed_n"] or 0), "unread": int(p["unread_n"] or 0)} for p in per), key=lambda b: b["branch"])
    return {"threads": threads, "branches": branches, "status": status,
            "counts": {"open": int(counts[0] or 0), "closed": int(counts[1] or 0), "all": int(counts[2] or 0)},
            "unread": int(counts[3] or 0), "topics": [{"id": k, "label": v} for k, v in TOPICS], "max_body": MAX_BODY}


@router.get("/api/franchise/messages/{tid}")
def fmsg_thread(tid: int, request: Request, branch_id: Optional[int] = Query(None)):
    who = caller(request, branch_id)
    with get_db() as con:
        ensure(con)
        r = _thread_row(con, tid, who)
        col = "unread_owner" if who["side"] == "owner" else "unread_hq"
        if r[col]:
            con.execute(f"UPDATE franchise_msg_thread SET {col} = 0 WHERE id = ?", (int(tid),))
            con.commit()
            r = con.execute("SELECT * FROM franchise_msg_thread WHERE id=?", (int(tid),)).fetchone()
        msgs = con.execute("SELECT * FROM franchise_msg_message WHERE thread_id=? ORDER BY id", (int(tid),)).fetchall()
        names = _branch_names(con) if who["side"] == "hq" else None
        out = _thread_out(r, who["side"], names)
        if who["side"] == "hq" and r["closed_by"]:
            out["closed_by"] = _auth.display_name_for(con, r["closed_by"]) or ""
    out["messages"] = [_msg_out(m) for m in msgs]
    out["max_body"] = MAX_BODY
    return out


@router.post("/api/franchise/messages/{tid}/reply")
def fmsg_reply(tid: int, body: _ReplyBody, request: Request, branch_id: Optional[int] = Query(None)):
    _write_gate(request)
    who = caller(request, branch_id)
    text = clean_text(body.body, MAX_BODY)
    at = _stamp()
    with get_db() as con:
        ensure(con)
        r = _thread_row(con, tid, who)
        if r["status"] != "open":
            raise HTTPException(409, "หัวข้อนี้ปิดแล้ว กรุณาส่งคำถามใหม่" if who["side"] == "owner" else "หัวข้อนี้ปิดแล้ว กรุณาเปิดหัวข้ออีกครั้งก่อนตอบ")
        _rate_limit(con, who)
        _insert_message(con, int(tid), who, text, at)
        if who["side"] == "owner":
            con.execute("UPDATE franchise_msg_thread SET last_at=?, unread_hq=unread_hq+1, unread_owner=0 WHERE id=?", (at, int(tid)))
        else:
            con.execute("UPDATE franchise_msg_thread SET last_at=?, unread_owner=unread_owner+1, unread_hq=0 WHERE id=?", (at, int(tid)))
        con.commit()
        branch = con.execute("SELECT name FROM branches WHERE branch_id=?", (r["branch_id"],)).fetchone()
        branch = branch[0] if branch else ""
    if who["side"] == "owner":
        _push("franchise_msg", hq_push_users, push_new_message(branch, int(tid), r["topic"]))
    else:
        _push("franchise_reply", lambda con: owner_push_users(con, branch), push_reply(int(tid), r["topic"]))
    return fmsg_thread(tid, request, branch_id)


@router.post("/api/franchise/messages/{tid}/status")
def fmsg_status(tid: int, body: _StatusBody, request: Request):
    _write_gate(request)
    who = _hq(request)
    if body.status not in ("open", "closed"):
        raise HTTPException(400, "สถานะไม่ถูกต้อง")
    at = _stamp()
    with get_db() as con:
        ensure(con)
        r = _thread_row(con, tid, who)
        if r["status"] != body.status:
            if body.status == "closed":
                con.execute("UPDATE franchise_msg_thread SET status='closed', closed_at=?, closed_by=? WHERE id=?", (at, who["user"], int(tid)))
            else:
                con.execute("UPDATE franchise_msg_thread SET status='open', closed_at=NULL, closed_by=NULL WHERE id=?", (int(tid),))
            con.commit()
    return fmsg_thread(tid, request)
