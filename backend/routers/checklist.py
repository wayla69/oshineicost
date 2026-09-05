# -*- coding: utf-8 -*-
"""✅ Go-live checklists — a plan the server owns (backend/checklists/<name>.json:
phases → tasks → steps, each step with how / why / done-when / if-stuck) and a
shared state that everyone holding the module ticks from the dashboard. Who and
when come from the login session, never from the client. Completion is a
server-side rule: POST …/complete succeeds only when every step of the plan is
done; the checklist is then locked (a signed-off record) until a manager reopens it.

  GET  /api/checklist/{name}/plan            the plan (+ default start, horizon, status labels)
  GET  /api/checklist/{name}                 state: steps, tasks, meta, complete, locked, progress, log tail
  POST /api/checklist/{name}/step/{id}       {done: true|false}
  POST /api/checklist/{name}/task/{id}       {status?, note?}
  POST /api/checklist/{name}/meta            {start: "YYYY-MM-DD"}
  POST /api/checklist/{name}/complete        every step done → complete{by, at}; else 400 with the missing ids
  POST /api/checklist/{name}/reopen          management only when login is enforced
RBAC: the feedback rollout rides customer.feedback (auth.MODULE_RULES). Tables
checklist_state / checklist_log survive the daily DB push (core._PRESERVED_PREFIXES).
"""
import datetime
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

import auth as _auth
from core import get_db

router = APIRouter()
PLANS_DIR = Path(__file__).resolve().parent.parent / "checklists"

DDL = """
CREATE TABLE IF NOT EXISTS checklist_state(
  checklist TEXT NOT NULL,
  item_id   TEXT NOT NULL,
  kind      TEXT NOT NULL,            -- step | task | meta
  done      INTEGER DEFAULT 0,
  status    TEXT,
  note      TEXT,
  value     TEXT,
  by        TEXT,
  at        TEXT,
  PRIMARY KEY (checklist, item_id)
);
CREATE TABLE IF NOT EXISTS checklist_log(
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  checklist TEXT NOT NULL,
  item_id   TEXT,
  action    TEXT,
  detail    TEXT,
  by        TEXT,
  at        TEXT DEFAULT (datetime('now','+7 hours'))
);
CREATE INDEX IF NOT EXISTS ix_cl_log ON checklist_log(checklist, id);
"""
_plans: dict = {}


def _ensure(con):
    con.executescript(DDL)


def _now() -> str:
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")


def load_plan(name: str) -> dict:
    """The plan file, re-read when it changes on disk."""
    if not re.fullmatch(r"[a-z0-9_]{1,40}", name or ""):
        raise HTTPException(404, "no such checklist")
    p = PLANS_DIR / f"{name}.json"
    if not p.exists():
        raise HTTPException(404, "no such checklist")
    mtime = p.stat().st_mtime
    cached = _plans.get(name)
    if cached and cached[0] == mtime:
        return cached[1]
    plan = json.loads(p.read_text(encoding="utf-8"))
    _plans[name] = (mtime, plan)
    return plan


def step_index(plan: dict) -> dict:
    """step id → title, in plan order."""
    out = {}
    for ph in plan.get("phases") or []:
        for t in ph.get("tasks") or []:
            for s in t.get("steps") or []:
                out[s["id"]] = s.get("t") or s["id"]
    return out


def task_index(plan: dict) -> dict:
    return {t["id"]: t.get("name") or t["id"] for ph in plan.get("phases") or [] for t in ph.get("tasks") or []}


def _session(request: Request):
    if not _auth.auth_enabled():
        return None
    return _auth.parse_token(request.cookies.get(_auth.SESSION_COOKIE, "")) or None


def _who(request: Request, body: dict) -> str:
    """The login name when the site enforces login; the client's `by` (or
    'local') on an open dev instance."""
    payload = _session(request)
    if payload and payload.get("u"):
        return str(payload["u"])[:60]
    return str((body or {}).get("by") or "local")[:60]


def _role(request: Request, con):
    payload = _session(request)
    if not payload:
        return None
    row = _auth.get_user(con, str(payload.get("u") or ""))
    return row["role"] if row else None


def _state(con, name: str) -> tuple:
    steps, tasks, meta = {}, {}, {}
    for r in con.execute("SELECT item_id, kind, done, status, note, value, by, at FROM checklist_state WHERE checklist=?", (name,)):
        d = dict(r)
        if d["kind"] == "step":
            steps[d["item_id"]] = {"done": bool(d["done"]), "by": d["by"], "at": d["at"]}
        elif d["kind"] == "task":
            tasks[d["item_id"]] = {"status": d["status"] or "todo", "note": d["note"] or "", "by": d["by"], "at": d["at"]}
        else:
            meta[d["item_id"]] = {"value": d["value"], "done": bool(d["done"]), "by": d["by"], "at": d["at"]}
    return steps, tasks, meta


def _progress(plan: dict, steps: dict) -> dict:
    ids = list(step_index(plan))
    missing = [i for i in ids if not (steps.get(i) or {}).get("done")]
    done = len(ids) - len(missing)
    return {"done": done, "total": len(ids), "pct": round(done / len(ids) * 100) if ids else 0, "missing": missing}


def _locked(meta: dict) -> bool:
    return bool((meta.get("complete") or {}).get("done"))


def _log(con, name: str, item_id, action: str, detail: str, by: str):
    con.execute("INSERT INTO checklist_log(checklist, item_id, action, detail, by, at) VALUES (?,?,?,?,?,?)",
                (name, item_id, action, (detail or "")[:200], by, _now()))
    con.execute("DELETE FROM checklist_log WHERE checklist=? AND id NOT IN "
                "(SELECT id FROM checklist_log WHERE checklist=? ORDER BY id DESC LIMIT 2000)", (name, name))


def _put(con, name: str, item_id: str, kind: str, by: str, **fields):
    cols = {"done": 0, "status": None, "note": None, "value": None}
    cols.update({k: v for k, v in fields.items() if k in cols})
    con.execute("INSERT OR REPLACE INTO checklist_state(checklist, item_id, kind, done, status, note, value, by, at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (name, item_id, kind, int(bool(cols["done"])), cols["status"], cols["note"], cols["value"], by, _now()))


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
    except Exception:
        return {}
    return b if isinstance(b, dict) else {}


def _reject_if_locked(meta: dict):
    if _locked(meta):
        c = meta["complete"]
        raise HTTPException(409, f"checklist นี้ปิดสมบูรณ์แล้วโดย {c.get('by') or '-'} เมื่อ {c.get('at') or '-'} — กด \"เปิดแก้ไขอีกครั้ง\" ก่อน")


@router.get("/api/checklist/{name}/plan")
def checklist_plan(name: str):
    return load_plan(name)


@router.get("/api/checklist/{name}")
def checklist_state(name: str):
    plan = load_plan(name)
    with get_db() as con:
        _ensure(con)
        steps, tasks, meta = _state(con, name)
        log = [dict(r) for r in con.execute(
            "SELECT item_id, action, detail, by, at FROM checklist_log WHERE checklist=? ORDER BY id DESC LIMIT 30", (name,))]
    complete = meta.get("complete") if _locked(meta) else None
    return {"name": name, "steps": steps, "tasks": tasks,
            "meta": {"start": (meta.get("start") or {}).get("value") or plan.get("default_start")},
            "complete": complete, "locked": bool(complete), "progress": _progress(plan, steps), "log": log}


@router.post("/api/checklist/{name}/step/{item_id}")
async def checklist_step(name: str, item_id: str, request: Request):
    plan = load_plan(name)
    titles = step_index(plan)
    if item_id not in titles:
        raise HTTPException(404, "no such step")
    body = await _body(request)
    done = bool(body.get("done"))
    with get_db() as con:
        _ensure(con)
        _, _, meta = _state(con, name)
        _reject_if_locked(meta)
        by = _who(request, body)
        _put(con, name, item_id, "step", by, done=done)
        _log(con, name, item_id, "tick" if done else "untick", titles[item_id], by)
        steps, _, _ = _state(con, name)
        con.commit()
    return {"item_id": item_id, "done": done, "by": by, "at": _now(), "progress": _progress(plan, steps)}


@router.post("/api/checklist/{name}/task/{item_id}")
async def checklist_task(name: str, item_id: str, request: Request):
    plan = load_plan(name)
    names = task_index(plan)
    if item_id not in names:
        raise HTTPException(404, "no such task")
    body = await _body(request)
    statuses = plan.get("statuses") or {}
    status = body.get("status")
    if status is not None and status not in statuses:
        raise HTTPException(400, f"status must be one of {', '.join(statuses)}")
    note = body.get("note")
    if note is not None:
        note = str(note)[:2000]
    with get_db() as con:
        _ensure(con)
        _, tasks, meta = _state(con, name)
        _reject_if_locked(meta)
        cur = tasks.get(item_id) or {"status": "todo", "note": ""}
        by = _who(request, body)
        new_status = status if status is not None else cur["status"]
        new_note = note if note is not None else cur["note"]
        _put(con, name, item_id, "task", by, status=new_status, note=new_note, done=(new_status == "done"))
        if status is not None and status != cur["status"]:
            _log(con, name, item_id, "status:" + status, names[item_id], by)
        elif note is not None and note != cur["note"]:
            _log(con, name, item_id, "note", names[item_id], by)
        con.commit()
    return {"item_id": item_id, "status": new_status, "note": new_note, "by": by, "at": _now()}


@router.post("/api/checklist/{name}/meta")
async def checklist_meta(name: str, request: Request):
    load_plan(name)
    body = await _body(request)
    start = str(body.get("start") or "").strip()
    try:
        datetime.date.fromisoformat(start)
    except ValueError:
        raise HTTPException(400, "start must be YYYY-MM-DD")
    with get_db() as con:
        _ensure(con)
        _, _, meta = _state(con, name)
        _reject_if_locked(meta)
        by = _who(request, body)
        _put(con, name, "start", "meta", by, value=start)
        _log(con, name, "start", "start", start, by)
        con.commit()
    return {"start": start, "by": by}


@router.post("/api/checklist/{name}/complete")
async def checklist_complete(name: str, request: Request):
    """The sign-off: refused, with the list of what is still open, unless every
    step of the plan is ticked. Success locks the checklist."""
    plan = load_plan(name)
    body = await _body(request)
    with get_db() as con:
        _ensure(con)
        steps, _, meta = _state(con, name)
        if _locked(meta):
            return {"complete": meta["complete"], "already": True}
        prog = _progress(plan, steps)
        if prog["missing"]:
            titles = step_index(plan)
            raise HTTPException(400, detail={
                "message": f"ยังไม่ครบ: เหลืออีก {len(prog['missing'])} จาก {prog['total']} ขั้นตอน — ต้องติ๊กให้ครบก่อนจึงปิดสมบูรณ์ได้",
                "missing": [{"id": i, "title": titles[i]} for i in prog["missing"][:50]],
                "progress": {k: prog[k] for k in ("done", "total", "pct")}})
        by = _who(request, body)
        at = _now()
        _put(con, name, "complete", "meta", by, done=True, value=at)
        _log(con, name, "complete", "complete", f"{prog['done']}/{prog['total']}", by)
        con.commit()
    return {"complete": {"done": True, "by": by, "at": at, "value": at}, "already": False}


@router.post("/api/checklist/{name}/reopen")
async def checklist_reopen(name: str, request: Request):
    load_plan(name)
    body = await _body(request)
    with get_db() as con:
        _ensure(con)
        role = _role(request, con)
        if _auth.auth_enabled() and role != "management":
            raise HTTPException(403, "เปิดแก้ไขอีกครั้งได้เฉพาะผู้บริหาร (management)")
        _, _, meta = _state(con, name)
        if not _locked(meta):
            return {"reopened": False, "locked": False}
        by = _who(request, body)
        _put(con, name, "complete", "meta", by, done=False, value=None)
        _log(con, name, "complete", "reopen", str(body.get("reason") or "")[:200], by)
        con.commit()
    return {"reopened": True, "locked": False, "by": by}


__all__ = ["router", "load_plan", "step_index", "task_index"]
