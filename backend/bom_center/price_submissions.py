# -*- coding: utf-8 -*-
"""bom_center.price_submissions — the monthly price file Sales Support sends to the analyst (PR-D).

Sales Support (ประสานงานขาย) uploads the month's purchase file; the system checks that it can be
read and keeps the bytes next to a dry-run summary. The analyst (ฝ่ายวิเคราะห์) then reviews it and
either imports it (the same weighted-average import as an upload by the analyst) or rejects it with a
reason Sales Support can read. Nothing here writes prices: the import runs in the router through the
same `_import` as every other price import.

The stored bytes leave the table only through `blob()`; `listing()` never returns `data`.
"""
import json

from .master import audit
from .variance import now

MAX_BYTES = 12 * 1024 * 1024
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_MIME = "text/csv"
STATUS_TH = {"pending": "รอฝ่ายวิเคราะห์ตรวจ", "imported": "นำเข้าแล้ว", "rejected": "ไม่รับ (ส่งกลับ)"}

DDL = (
    """CREATE TABLE IF NOT EXISTS bc_price_submission(
         sub_id INTEGER PRIMARY KEY AUTOINCREMENT,
         month TEXT NOT NULL, filename TEXT, mime TEXT NOT NULL, size INTEGER NOT NULL, data BLOB NOT NULL,
         note TEXT, submitted_by TEXT, submitted_at TEXT,
         status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','imported','rejected')),
         decided_by TEXT, decided_at TEXT, decision_note TEXT,
         preview_json TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_bc_price_sub ON bc_price_submission(month, status)",
)

_COLS = ("sub_id", "month", "filename", "mime", "size", "note", "submitted_by", "submitted_at", "status",
         "decided_by", "decided_at", "decision_note", "preview_json")


def sniff_xlsx(data: bytes):
    """The MIME type from the file's own first bytes (never from the name or the header), or None:
    a zip container is read as Excel, text whose first line has a separator is read as CSV."""
    if not data:
        return None
    if data[:4] == b"PK\x03\x04":
        return XLSX_MIME
    head = data[:65536]
    if b"\x00" in head:
        return None
    text = head.decode("utf-8", errors="ignore").lstrip("\ufeff")
    first = text.split("\n", 1)[0]
    if any(sep in first for sep in (",", ";", "\t")):
        return CSV_MIME
    return None


def _row(r) -> dict:
    d = dict(zip(_COLS, r))
    pj = d.pop("preview_json", None)
    try:
        d["preview"] = json.loads(pj) if pj else None
    except ValueError:
        d["preview"] = None
    d["status_th"] = STATUS_TH.get(d["status"], d["status"])
    return d


def create(con, month: str, filename: str, data: bytes, note: str, who: str, preview: dict = None) -> dict:
    """Keep the file for the analyst. `preview` is the dry-run summary taken at submit time."""
    if len(data or b"") > MAX_BYTES:
        raise ValueError("ไฟล์ใหญ่เกิน 12 MB")
    mime = sniff_xlsx(data or b"")
    if not mime:
        raise ValueError("ไฟล์ต้องเป็น Excel (.xlsx) หรือ CSV")
    fn = (filename or "").strip()[:200] or "ไฟล์ราคา"
    cur = con.execute(
        "INSERT INTO bc_price_submission(month, filename, mime, size, data, note, submitted_by, submitted_at, status,"
        " preview_json) VALUES(?,?,?,?,?,?,?,?,'pending',?)",
        (month, fn, mime, len(data), data, (note or "").strip() or None, who, now(),
         json.dumps(preview, ensure_ascii=False, default=str) if preview is not None else None))
    sid = cur.lastrowid
    audit(con, who, "price_submit", "bc_price_submission", str(sid), None,
          {"month": month, "filename": fn, "size": len(data)})
    return {"sub_id": sid, "month": month, "filename": fn, "size": len(data), "status": "pending"}


def listing(con, status: str = None, month: str = None, limit: int = 50) -> list:
    """Newest first; never carries the file bytes."""
    where, args = [], []
    if status:
        where.append("status=?")
        args.append(status)
    if month:
        where.append("month=?")
        args.append(month)
    sql = f"SELECT {', '.join(_COLS)} FROM bc_price_submission"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY sub_id DESC LIMIT ?"
    args.append(max(1, min(int(limit or 50), 200)))
    return [_row(r) for r in con.execute(sql, args).fetchall()]


def head(con, sub_id: int):
    r = con.execute(f"SELECT {', '.join(_COLS)} FROM bc_price_submission WHERE sub_id=?", (sub_id,)).fetchone()
    return _row(r) if r else None


def blob(con, sub_id: int):
    """-> (filename, mime, bytes). The only way the stored file leaves the table."""
    r = con.execute("SELECT filename, mime, data FROM bc_price_submission WHERE sub_id=?", (sub_id,)).fetchone()
    if not r:
        raise ValueError("ไม่พบไฟล์ราคาที่ส่งมา")
    return r[0] or "ไฟล์ราคา", r[1], bytes(r[2])


def decide(con, sub_id: int, status: str, who: str, note: str = None, applied=None) -> dict:
    """pending -> imported | rejected. A rejection needs a reason; a decided file is final."""
    if status not in ("imported", "rejected"):
        raise ValueError("สถานะไม่ถูกต้อง")
    h = head(con, sub_id)
    if not h:
        raise ValueError("ไม่พบไฟล์ราคาที่ส่งมา")
    if h["status"] != "pending":
        raise ValueError(f"ไฟล์นี้ตัดสินแล้ว ({STATUS_TH.get(h['status'], h['status'])})")
    note = (note or "").strip() or None
    if status == "rejected" and not note:
        raise ValueError("กรุณาระบุเหตุผลที่ไม่รับไฟล์นี้")
    con.execute("UPDATE bc_price_submission SET status=?, decided_by=?, decided_at=?, decision_note=? WHERE sub_id=?",
                (status, who, now(), note, sub_id))
    audit(con, who, "price_submit_" + ("import" if status == "imported" else "reject"), "bc_price_submission",
          str(sub_id), "pending", {"status": status, "note": note, "applied": applied})
    return {"sub_id": sub_id, "status": status, "month": h["month"]}


def counts(con) -> dict:
    out = {"pending": 0, "imported": 0, "rejected": 0}
    for st, n in con.execute("SELECT status, COUNT(*) FROM bc_price_submission GROUP BY status"):
        out[st] = n
    return out
