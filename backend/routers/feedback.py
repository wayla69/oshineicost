# -*- coding: utf-8 -*-
"""💬 เสียงลูกค้า — customer feedback (pillar 3 of the menu programme, 2026-09).

Reviews exported from Google / Facebook / Grab / LINE MAN (or pasted) land in
`customer_feedback`, each analysed for sentiment, themes and the menus it
mentions (feedback_nlp: Claude when ANTHROPIC_API_KEY is set, the Thai lexicon
otherwise — the row records which). The summary turns them into what the menu
tabs cannot see from POS data: what guests say about the dishes and the branch.

  POST   /api/feedback/upload           file (CSV/TSV/TXT) or pasted text or JSON rows
  GET    /api/feedback/summary          counts, sentiment, themes, menus, branches, recent negatives
  GET    /api/feedback/items            the rows behind a summary figure
  DELETE /api/feedback/{id}
RBAC module customer.feedback (auth.py); writes need the write token like every upload.
"""
import csv
import datetime
import hashlib
import io
import json
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile

from core import get_db, require_write_token
from feedback_nlp import analyse, clean_menu_name

router = APIRouter()

DDL = """
CREATE TABLE IF NOT EXISTS customer_feedback(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  branch_id   INTEGER,
  branch_name TEXT,
  source      TEXT,
  rated_at    TEXT,
  rating      REAL,
  text        TEXT NOT NULL,
  sentiment   TEXT,
  score       REAL,
  themes      TEXT,
  menus       TEXT,
  analysed_by TEXT,
  uploaded_at TEXT DEFAULT (datetime('now','+7 hours')),
  uploaded_by TEXT,
  fingerprint TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_cf_branch_date ON customer_feedback(branch_id, rated_at);
"""

# header names the CSV/TSV import understands (lower-cased, Thai or English)
_COLS = {
    "date": ("date", "วันที่", "rated_at", "time", "datetime", "created"),
    "branch": ("branch", "สาขา", "store", "location", "ร้าน"),
    "source": ("source", "ช่องทาง", "แหล่ง", "platform", "channel"),
    "rating": ("rating", "คะแนน", "ดาว", "stars", "score"),
    "text": ("text", "review", "ข้อความ", "รีวิว", "comment", "ความคิดเห็น", "content", "body"),
}


def _ensure(con):
    con.executescript(DDL)


def _col_map(header):
    out = {}
    for i, h in enumerate(header):
        hl = (h or "").strip().lower()
        for key, names in _COLS.items():
            if key not in out and any(hl == n or hl.startswith(n) for n in names):
                out[key] = i
    return out


def parse_rows(text: str = "", data: bytes = b"", filename: str = "",
               default_branch: str = "", default_source: str = "", default_date: str = "") -> list:
    """Turn pasted lines or a CSV/TSV/TXT file into rows {date, branch, source, rating, text}."""
    rows = []
    if data:
        body = data.decode("utf-8-sig", errors="replace")
        delim = "\t" if body.count("\t") > body.count(",") else ","
        reader = list(csv.reader(io.StringIO(body), delimiter=delim))
        cmap = _col_map(reader[0]) if reader else {}
        if "text" in cmap:
            for r in reader[1:]:
                if not r or len(r) <= cmap["text"]:
                    continue
                t = (r[cmap["text"]] or "").strip()
                if not t:
                    continue
                g = lambda k: (r[cmap[k]].strip() if k in cmap and len(r) > cmap[k] else "")  # noqa: E731
                rows.append({"date": g("date") or default_date, "branch": g("branch") or default_branch,
                             "source": g("source") or default_source, "rating": g("rating") or None, "text": t})
        else:   # no recognisable header → every non-empty line is one review
            text = (text or "") + "\n" + body
    for line in (text or "").splitlines():
        t = line.strip()
        if t and not t.startswith("#"):
            rows.append({"date": default_date, "branch": default_branch, "source": default_source,
                         "rating": None, "text": t})
    for r in rows:
        try:
            r["rating"] = float(str(r["rating"]).replace("★", "").strip()) if r.get("rating") not in (None, "") else None
        except ValueError:
            r["rating"] = None
        d = (r.get("date") or "").strip()
        r["date"] = d[:10] if len(d) >= 10 else d
    return rows


def _branches(con) -> dict:
    return {row["name"]: row["branch_id"] for row in con.execute("SELECT branch_id, name FROM branches")}


def _match_branch(name: str, table: dict):
    n = (name or "").strip()
    if not n:
        return None, ""
    if n in table:
        return table[n], n
    for bn, bid in table.items():
        if bn in n or n in bn:
            return bid, bn
    return None, n


def _menu_names(con) -> list:
    return [r[0] for r in con.execute("SELECT description FROM skus WHERE description IS NOT NULL")]


def _fingerprint(r) -> str:
    return hashlib.sha1(f"{r.get('branch') or ''}|{r.get('date') or ''}|{(r.get('text') or '').strip()}".encode("utf-8")).hexdigest()


@router.post("/api/feedback/upload", dependencies=[Depends(require_write_token)])
async def feedback_upload(request: Request, file: Optional[UploadFile] = File(None),
                          text: Optional[str] = Form(None), branch: str = Form(""), source: str = Form(""),
                          date: str = Form(""), by: str = Form("")):
    """Accepts a multipart form (file and/or pasted `text`, with defaults) or a
    JSON body {rows:[{date, branch, source, rating, text}], by}."""
    rows = []
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        body = await request.json()
        by = body.get("by") or by
        for r in body.get("rows") or []:
            if isinstance(r, dict) and (r.get("text") or "").strip():
                rows.append({"date": (r.get("date") or "")[:10], "branch": r.get("branch") or "",
                             "source": r.get("source") or "", "rating": r.get("rating"), "text": r["text"].strip()})
    else:
        data = await file.read() if file is not None else b""
        rows = parse_rows(text or "", data, file.filename if file is not None else "", branch, source, date)
    if not rows:
        raise HTTPException(400, "ไม่พบข้อความรีวิว — วางข้อความบรรทัดละ 1 รีวิว หรือแนบไฟล์ CSV ที่มีคอลัมน์ text/รีวิว")
    if len(rows) > 5000:
        raise HTTPException(400, "ครั้งละไม่เกิน 5,000 รีวิว")
    # a review pasted without a date is a review received today (Thai calendar day);
    # the summary is filtered by month, so an undated row would never show anywhere
    today = (datetime.datetime.utcnow() + datetime.timedelta(hours=7)).strftime("%Y-%m-%d")
    for r in rows:
        if not (r.get("date") or "").strip():
            r["date"] = today
    with get_db() as con:
        _ensure(con)
        table = _branches(con)
        results, engine = analyse(rows, _menu_names(con))
        inserted = 0
        for r, a in zip(rows, results):
            bid, bname = _match_branch(r.get("branch") or "", table)
            cur = con.execute(
                "INSERT OR IGNORE INTO customer_feedback(branch_id, branch_name, source, rated_at, rating, text, "
                "sentiment, score, themes, menus, analysed_by, uploaded_by, fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (bid, bname, r.get("source") or "", r.get("date") or "", r.get("rating"), r["text"],
                 a["sentiment"], a["score"], json.dumps(a["themes"], ensure_ascii=False),
                 json.dumps(a["menus"], ensure_ascii=False), engine, by or "", _fingerprint(r)))
            inserted += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        con.commit()
    return {"rows": len(rows), "inserted": inserted, "duplicates": len(rows) - inserted, "analysed_by": engine}


def _where(branch, month_from, month_to, sentiment=None, theme=None, menu=None):
    w, p = ["1=1"], []
    if branch:
        w.append("branch_name = ?"); p.append(branch)
    if month_from:
        w.append("substr(rated_at,1,7) >= ?"); p.append(month_from[:7])
    if month_to:
        w.append("substr(rated_at,1,7) <= ?"); p.append(month_to[:7])
    if sentiment:
        w.append("sentiment = ?"); p.append(sentiment)
    if theme:
        w.append("themes LIKE ?"); p.append(f'%"{theme}"%')
    if menu:
        w.append("menus LIKE ?"); p.append(f'%"{menu}"%')
    return " AND ".join(w), p


@router.get("/api/feedback/summary")
def feedback_summary(branch: Optional[str] = Query(None), month_from: Optional[str] = Query(None),
                     month_to: Optional[str] = Query(None)):
    with get_db() as con:
        _ensure(con)
        where, params = _where(branch, month_from, month_to)
        rows = [dict(r) for r in con.execute(
            f"SELECT id, branch_name, source, rated_at, rating, text, sentiment, score, themes, menus, analysed_by "
            f"FROM customer_feedback WHERE {where} ORDER BY rated_at DESC, id DESC", params)]
    total = len(rows)
    sent = {"pos": 0, "neg": 0, "neu": 0}
    themes, menus, branches, by_month, sources, engines = {}, {}, {}, {}, {}, {}
    ratings = []
    for r in rows:
        s = r["sentiment"] or "neu"
        sent[s] = sent.get(s, 0) + 1
        if r["rating"] is not None:
            ratings.append(float(r["rating"]))
        for t in json.loads(r["themes"] or "[]"):
            d = themes.setdefault(t, {"theme": t, "n": 0, "neg": 0}); d["n"] += 1; d["neg"] += (s == "neg")
        for m in json.loads(r["menus"] or "[]"):
            d = menus.setdefault(m, {"menu": m, "n": 0, "pos": 0, "neg": 0}); d["n"] += 1; d["pos"] += (s == "pos"); d["neg"] += (s == "neg")
        bn = r["branch_name"] or "(ไม่ระบุสาขา)"
        d = branches.setdefault(bn, {"branch": bn, "n": 0, "neg": 0, "ratings": []}); d["n"] += 1; d["neg"] += (s == "neg")
        if r["rating"] is not None:
            d["ratings"].append(float(r["rating"]))
        ym = (r["rated_at"] or "")[:7] or "ไม่ระบุ"
        d = by_month.setdefault(ym, {"month": ym, "n": 0, "pos": 0, "neg": 0}); d["n"] += 1; d["pos"] += (s == "pos"); d["neg"] += (s == "neg")
        sources[r["source"] or "(ไม่ระบุ)"] = sources.get(r["source"] or "(ไม่ระบุ)", 0) + 1
        engines[r["analysed_by"] or "?"] = engines.get(r["analysed_by"] or "?", 0) + 1
    for d in branches.values():
        d["avg_rating"] = round(sum(d["ratings"]) / len(d["ratings"]), 2) if d["ratings"] else None
        d["neg_pct"] = round(d["neg"] / d["n"] * 100, 1) if d["n"] else 0.0
        del d["ratings"]
    for d in themes.values():
        d["neg_pct"] = round(d["neg"] / d["n"] * 100, 1) if d["n"] else 0.0
    recent_neg = [{"id": r["id"], "date": r["rated_at"], "branch": r["branch_name"], "source": r["source"],
                   "rating": r["rating"], "text": r["text"][:300], "themes": json.loads(r["themes"] or "[]"),
                   "menus": json.loads(r["menus"] or "[]")}
                  for r in rows if r["sentiment"] == "neg"][:12]
    return {
        "total": total, "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "sentiment": sent, "neg_pct": round(sent["neg"] / total * 100, 1) if total else 0.0,
        "themes": sorted(themes.values(), key=lambda d: -d["n"]),
        "menus": sorted(menus.values(), key=lambda d: -d["n"])[:30],
        "branches": sorted(branches.values(), key=lambda d: -d["n"]),
        "by_month": sorted(by_month.values(), key=lambda d: d["month"]),
        "sources": sources, "analysed_by": engines, "recent_negative": recent_neg,
        "filters": {"branch": branch, "month_from": month_from, "month_to": month_to},
    }


@router.get("/api/feedback/items")
def feedback_items(branch: Optional[str] = Query(None), month_from: Optional[str] = Query(None),
                   month_to: Optional[str] = Query(None), sentiment: Optional[str] = Query(None),
                   theme: Optional[str] = Query(None), menu: Optional[str] = Query(None),
                   limit: int = Query(200, ge=1, le=2000)):
    with get_db() as con:
        _ensure(con)
        where, params = _where(branch, month_from, month_to, sentiment, theme, menu)
        rows = con.execute(
            f"SELECT id, branch_name, source, rated_at, rating, text, sentiment, score, themes, menus, analysed_by, uploaded_at "
            f"FROM customer_feedback WHERE {where} ORDER BY rated_at DESC, id DESC LIMIT ?", params + [limit]).fetchall()
    return {"items": [{**dict(r), "themes": json.loads(r["themes"] or "[]"), "menus": json.loads(r["menus"] or "[]")} for r in rows]}


@router.delete("/api/feedback/{fid}", dependencies=[Depends(require_write_token)])
def feedback_delete(fid: int):
    with get_db() as con:
        _ensure(con)
        n = con.execute("DELETE FROM customer_feedback WHERE id=?", (fid,)).rowcount
        con.commit()
    if not n:
        raise HTTPException(404, "not found")
    return {"deleted": fid}


__all__ = ["router", "parse_rows", "clean_menu_name"]
