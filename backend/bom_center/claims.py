# -*- coding: utf-8 -*-
"""เคลมสินค้า — a branch reports goods that arrived damaged or wrong, with photos, and Sales
Support decides (owner 2026-10-08).

    branch: after the receipt of an order → แจ้งเคลม (lines + a reason each + 1–6 photos)
    Sales Support: sees the photos → อนุมัติรับเคลม (per line, up to what was claimed) or ไม่อนุมัติ
    approved → the stock is ADJUSTED: a negative goods-receipt line (source 'claim', dated the
               day the order was received) takes the claimed quantity out of what the branch
               "received", so the usage variance does not count goods that were never usable.

A claim is made against ONE received order and only for what that order delivered; the sum of
the open and approved claims of a line can never exceed the received quantity. The photos are
kept in the database (a re-compressed JPEG is a few hundred KB) and are served only to the
branch that sent them and to the supply roles.
"""
import json

from .clock import today as bkk_today
from .master import audit
from .variance import now

REASONS = [
    ("damaged", "สินค้าชำรุด แตก หรือเสียหาย"),
    ("spoiled", "สินค้าเสีย เน่า หรือคุณภาพไม่ได้มาตรฐาน"),
    ("expired", "สินค้าหมดอายุหรืออายุสั้นเกินไป"),
    ("wrong_item", "ส่งผิดรายการ"),
    ("packaging", "บรรจุภัณฑ์ฉีกขาดหรือรั่ว"),
    ("temperature", "อุณหภูมิไม่เหมาะสม (ละลายหรือไม่เย็นพอ)"),
    ("other", "อื่น ๆ (ระบุในหมายเหตุ)"),
]
REASON_TH = dict(REASONS)
STATUS_TH = {"submitted": "รอประสานงานขายพิจารณา", "approved": "อนุมัติรับเคลมแล้ว",
             "rejected": "ไม่อนุมัติ", "cancelled": "สาขายกเลิก"}
OPEN = ("submitted", "approved")          # the statuses that use up a line's claimable quantity
MAX_PHOTOS = 6
MAX_PHOTO_BYTES = 3 * 1024 * 1024

DDL = (
    """CREATE TABLE IF NOT EXISTS bc_claim(
         claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
         branch_id INTEGER NOT NULL, order_id INTEGER NOT NULL,
         status TEXT NOT NULL DEFAULT 'submitted',          -- submitted / approved / rejected / cancelled
         note TEXT, created_by TEXT, created_at TEXT,
         decided_by TEXT, decided_at TEXT, decision_note TEXT,
         adjust_receipt_id INTEGER)""",
    "CREATE INDEX IF NOT EXISTS ix_bc_claim_branch ON bc_claim(branch_id, status)",
    """CREATE TABLE IF NOT EXISTS bc_claim_line(
         claim_id INTEGER NOT NULL, ingredient_code TEXT NOT NULL,
         claim_units REAL NOT NULL, reason_code TEXT NOT NULL, note TEXT,
         approved_units REAL,
         PRIMARY KEY (claim_id, ingredient_code))""",
    """CREATE TABLE IF NOT EXISTS bc_claim_photo(
         photo_id INTEGER PRIMARY KEY AUTOINCREMENT,
         claim_id INTEGER NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL, data BLOB NOT NULL,
         created_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_bc_claim_photo ON bc_claim_photo(claim_id)",
)


def sniff_image(data: bytes):
    """The MIME type from the file's own first bytes (never from the name or the header), or None."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _order(con, branch_id, order_id):
    r = con.execute("SELECT order_id, branch_id, cycle_date, round, status, received_at FROM bc_order WHERE order_id=?",
                    (order_id,)).fetchone()
    if not r or r[1] != branch_id:
        raise ValueError("ไม่พบใบสั่งซื้อของสาขานี้")
    return {"order_id": r[0], "branch_id": r[1], "cycle_date": r[2], "round": r[3], "status": r[4], "received_at": r[5]}


def claimable(con, branch_id: int, order_id: int) -> dict:
    """What can still be claimed on a received order: per line, received minus what open/approved claims took."""
    o = _order(con, branch_id, order_id)
    if o["status"] != "received":
        raise ValueError("แจ้งเคลมได้เมื่อบันทึกรับสินค้าของใบสั่งซื้อนี้แล้วเท่านั้น")
    names = dict(con.execute("SELECT code, name_th FROM bc_ingredient"))
    taken = {}
    for c, cu, au, st in con.execute(
            "SELECT l.ingredient_code, l.claim_units, l.approved_units, c.status FROM bc_claim_line l JOIN bc_claim c"
            " USING(claim_id) WHERE c.order_id=? AND c.status IN ('submitted','approved')", (order_id,)):
        taken[c] = taken.get(c, 0.0) + float(au if st == "approved" and au is not None else cu)
    lines = []
    for c, u, recv in con.execute("SELECT ingredient_code, order_unit, received_units FROM bc_order_line"
                                  " WHERE order_id=? AND COALESCE(received_units,0)>0 ORDER BY ingredient_code", (order_id,)):
        left = round(float(recv) - taken.get(c, 0.0), 6)
        lines.append({"code": c, "name": names.get(c), "order_unit": u, "received_units": float(recv),
                      "claimed_units": round(taken.get(c, 0.0), 6), "remaining_units": max(left, 0.0)})
    return {"order_id": order_id, "cycle_date": o["cycle_date"], "round": o["round"], "lines": lines}


def create(con, branch_id: int, order_id: int, lines: list, photos: list, note: str, who: str) -> dict:
    """photos = [(bytes)]; every one is checked by its own bytes. Returns {claim_id}."""
    can = {ln["code"]: ln for ln in claimable(con, branch_id, order_id)["lines"]}
    clean = []
    for ln in lines or []:
        code = (ln.get("code") or "").strip()
        if not code:
            continue
        try:
            q = float(ln.get("claim_units"))
        except (TypeError, ValueError):
            raise ValueError(f"{code}: จำนวนที่เคลมต้องเป็นตัวเลข")
        if q <= 0:
            continue
        if code not in can:
            raise ValueError(f"{code} ไม่ได้อยู่ในรายการที่รับของใบสั่งซื้อนี้")
        if q > can[code]["remaining_units"] + 1e-9:
            raise ValueError(f"{code}: เคลมได้ไม่เกิน {can[code]['remaining_units']:g} (รับแล้ว {can[code]['received_units']:g}"
                             + (f" · เคลมไปแล้ว {can[code]['claimed_units']:g}" if can[code]["claimed_units"] else "") + ")")
        rs = (ln.get("reason") or "").strip()
        if rs not in REASON_TH:
            raise ValueError(f"{code}: กรุณาเลือกสาเหตุของการเคลม")
        nt = (ln.get("note") or "").strip() or None
        if rs == "other" and not nt:
            raise ValueError(f"{code}: สาเหตุ “อื่น ๆ” ต้องระบุคำอธิบาย")
        clean.append((code, q, rs, nt))
    if not clean:
        raise ValueError("กรุณาระบุรายการที่จะเคลมอย่างน้อย 1 รายการ")
    if len({c for c, *_ in clean}) != len(clean):
        raise ValueError("มีรายการซ้ำ กรุณารวมเป็นบรรทัดเดียว")
    if not photos:
        raise ValueError("กรุณาแนบรูปถ่ายสินค้าอย่างน้อย 1 รูป")
    if len(photos) > MAX_PHOTOS:
        raise ValueError(f"แนบรูปได้ไม่เกิน {MAX_PHOTOS} รูป")
    checked = []
    for i, data in enumerate(photos, 1):
        if len(data) > MAX_PHOTO_BYTES:
            raise ValueError(f"รูปที่ {i} ใหญ่เกิน {MAX_PHOTO_BYTES // (1024 * 1024)} MB")
        mime = sniff_image(data)
        if not mime:
            raise ValueError(f"รูปที่ {i} ไม่ใช่ไฟล์รูปภาพ (รองรับ JPG PNG WebP)")
        checked.append((mime, data))
    cur = con.execute("INSERT INTO bc_claim(branch_id, order_id, status, note, created_by, created_at) VALUES(?,?,?,?,?,?)",
                      (branch_id, order_id, "submitted", (note or "").strip() or None, who, now()))
    cid = cur.lastrowid
    con.executemany("INSERT INTO bc_claim_line(claim_id, ingredient_code, claim_units, reason_code, note) VALUES(?,?,?,?,?)",
                    [(cid, c, q, rs, nt) for c, q, rs, nt in clean])
    con.executemany("INSERT INTO bc_claim_photo(claim_id, mime, size, data, created_at) VALUES(?,?,?,?,?)",
                    [(cid, m, len(d), d, now()) for m, d in checked])
    audit(con, who, "claim_create", "bc_claim", str(cid), None, {"branch_id": branch_id, "order_id": order_id,
                                                                 "lines": len(clean), "photos": len(checked)})
    return {"claim_id": cid, "lines": len(clean), "photos": len(checked)}


def _head(con, claim_id):
    r = con.execute(
        "SELECT c.claim_id, c.branch_id, b.name, c.order_id, c.status, c.note, c.created_by, c.created_at, c.decided_by,"
        " c.decided_at, c.decision_note, c.adjust_receipt_id, o.cycle_date, o.round FROM bc_claim c"
        " LEFT JOIN branches b USING(branch_id) LEFT JOIN bc_order o USING(order_id) WHERE c.claim_id=?", (claim_id,)).fetchone()
    if not r:
        return None
    d = dict(zip(("claim_id", "branch_id", "branch", "order_id", "status", "note", "created_by", "created_at", "decided_by",
                  "decided_at", "decision_note", "adjust_receipt_id", "cycle_date", "round"), r))
    d["status_th"] = STATUS_TH.get(d["status"], d["status"])
    return d


def detail(con, claim_id: int) -> dict:
    d = _head(con, claim_id)
    if not d:
        raise ValueError("ไม่พบรายการเคลม")
    names = dict(con.execute("SELECT code, name_th FROM bc_ingredient"))
    units = {c: u for c, u in con.execute("SELECT ingredient_code, order_unit FROM bc_order_line WHERE order_id=?", (d["order_id"],))}
    recv = {c: r for c, r in con.execute("SELECT code, recv_unit FROM bc_ingredient")}
    base = {c: b for c, b in con.execute("SELECT code, base_unit FROM bc_ingredient")}
    d["lines"] = [{"code": c, "name": names.get(c), "claim_units": q, "approved_units": au, "reason": rs,
                   "reason_th": REASON_TH.get(rs, rs), "note": nt,
                   "unit": (recv.get(c) if units.get(c) and units.get(c) != "base" else base.get(c))}
                  for c, q, rs, nt, au in con.execute(
                      "SELECT ingredient_code, claim_units, reason_code, note, approved_units FROM bc_claim_line"
                      " WHERE claim_id=? ORDER BY ingredient_code", (claim_id,))]
    d["photos"] = [{"photo_id": p, "mime": m, "size": sz} for p, m, sz in con.execute(
        "SELECT photo_id, mime, size FROM bc_claim_photo WHERE claim_id=? ORDER BY photo_id", (claim_id,))]
    return d


def listing(con, branch_id=None, status=None, limit: int = 50) -> list:
    sql = ("SELECT c.claim_id FROM bc_claim c WHERE 1=1")
    args = []
    if branch_id:
        sql += " AND c.branch_id=?"; args.append(branch_id)
    if status and status != "all":
        sql += " AND c.status=?"; args.append(status)
    rows = [r[0] for r in con.execute(sql + " ORDER BY (c.status='submitted') DESC, c.claim_id DESC LIMIT ?", args + [limit])]
    out = []
    for cid in rows:
        h = _head(con, cid)
        h["lines"] = con.execute("SELECT COUNT(*) FROM bc_claim_line WHERE claim_id=?", (cid,)).fetchone()[0]
        h["photos"] = con.execute("SELECT COUNT(*) FROM bc_claim_photo WHERE claim_id=?", (cid,)).fetchone()[0]
        out.append(h)
    return out


def photo(con, claim_id: int, photo_id: int):
    r = con.execute("SELECT p.mime, p.data, c.branch_id FROM bc_claim_photo p JOIN bc_claim c USING(claim_id)"
                    " WHERE p.photo_id=? AND p.claim_id=?", (photo_id, claim_id)).fetchone()
    if not r:
        raise ValueError("ไม่พบรูป")
    return r[0], bytes(r[1]), r[2]


def cancel(con, claim_id: int, who: str) -> dict:
    h = _head(con, claim_id)
    if not h:
        raise ValueError("ไม่พบรายการเคลม")
    if h["status"] != "submitted":
        raise ValueError("ยกเลิกได้เฉพาะเคลมที่ยังรอพิจารณา")
    con.execute("UPDATE bc_claim SET status='cancelled', decided_by=?, decided_at=? WHERE claim_id=?", (who, now(), claim_id))
    audit(con, who, "claim_cancel", "bc_claim", str(claim_id), "submitted", "cancelled")
    return {"claim_id": claim_id, "status": "cancelled"}


def decide(con, claim_id: int, decision: str, lines: list, note: str, who: str) -> dict:
    """Sales Support: approve (per line up to the claimed quantity; default = all of it) or reject.
    Approving books the stock adjustment — a negative goods receipt dated the day the order was received."""
    from .variance import add_receipt
    h = _head(con, claim_id)
    if not h:
        raise ValueError("ไม่พบรายการเคลม")
    if h["status"] != "submitted":
        raise ValueError(f"เคลมนี้ตัดสินแล้ว ({h['status_th']})")
    note = (note or "").strip() or None
    if decision == "reject":
        if not note:
            raise ValueError("กรุณาระบุเหตุผลที่ไม่อนุมัติ เพื่อให้สาขาทราบ")
        con.execute("UPDATE bc_claim SET status='rejected', decided_by=?, decided_at=?, decision_note=? WHERE claim_id=?",
                    (who, now(), note, claim_id))
        audit(con, who, "claim_reject", "bc_claim", str(claim_id), "submitted", {"note": note})
        return {"claim_id": claim_id, "status": "rejected", "adjusted": 0}
    if decision != "approve":
        raise ValueError("การตัดสินต้องเป็น approve หรือ reject")
    given = {(ln.get("code") or "").strip(): ln.get("approved_units") for ln in lines or []}
    rows = con.execute("SELECT ingredient_code, claim_units FROM bc_claim_line WHERE claim_id=?", (claim_id,)).fetchall()
    approved = []
    for c, cu in rows:
        v = given.get(c)
        try:
            a = float(cu if v is None or v == "" else v)
        except (TypeError, ValueError):
            raise ValueError(f"{c}: จำนวนที่อนุมัติต้องเป็นตัวเลข")
        if a < 0 or a > float(cu) + 1e-9:
            raise ValueError(f"{c}: อนุมัติได้ 0 ถึง {float(cu):g} (จำนวนที่สาขาเคลม)")
        approved.append((c, a))
    if not any(a > 0 for _, a in approved):
        raise ValueError("กรุณาระบุจำนวนที่อนุมัติอย่างน้อย 1 รายการ หรือเลือก “ไม่อนุมัติ”")
    for c, a in approved:
        con.execute("UPDATE bc_claim_line SET approved_units=? WHERE claim_id=? AND ingredient_code=?", (a, claim_id, c))
    # the adjustment: dated the receipt of the order, so the same variance period loses these goods
    rec = con.execute("SELECT receipt_date FROM bc_receipt WHERE branch_id=? AND ref=? ORDER BY receipt_id LIMIT 1",
                      (h["branch_id"], f"ORDER-{h['order_id']}")).fetchone()
    rdate = rec[0] if rec else (con.execute("SELECT SUBSTR(received_at,1,10) FROM bc_order WHERE order_id=?",
                                            (h["order_id"],)).fetchone() or [None])[0] or bkk_today().isoformat()
    units = {c: u for c, u in con.execute("SELECT ingredient_code, order_unit FROM bc_order_line WHERE order_id=?", (h["order_id"],))}
    recv = {c: r for c, r in con.execute("SELECT code, recv_unit FROM bc_ingredient")}
    base = {c: b for c, b in con.execute("SELECT code, base_unit FROM bc_ingredient")}
    plus = [{"code": c, "qty": a, "unit": (recv.get(c) if units.get(c) and units.get(c) != "base" else base.get(c))}
            for c, a in approved if a > 0]
    r = add_receipt(con, h["branch_id"], rdate, plus, who, ref=f"CLAIM-{claim_id}",
                    note=f"ปรับสินค้าตามเคลม #{claim_id}", source="claim")
    con.execute("UPDATE bc_receipt_line SET qty=-qty, qty_base=-qty_base WHERE receipt_id=?", (r["receipt_id"],))
    con.execute("UPDATE bc_claim SET status='approved', decided_by=?, decided_at=?, decision_note=?, adjust_receipt_id=?"
                " WHERE claim_id=?", (who, now(), note, r["receipt_id"], claim_id))
    audit(con, who, "claim_approve", "bc_claim", str(claim_id), "submitted",
          json.dumps({"receipt_id": r["receipt_id"], "lines": len(plus), "receipt_date": rdate}, ensure_ascii=False))
    return {"claim_id": claim_id, "status": "approved", "adjusted": len(plus), "receipt_id": r["receipt_id"], "receipt_date": rdate}


def counts(con) -> dict:
    return {s: n for s, n in con.execute("SELECT status, COUNT(*) FROM bc_claim GROUP BY status")}
