# -*- coding: utf-8 -*-
"""เคลมสินค้า — a branch reports goods that arrived short, damaged or wrong, with a reason per
line, and Sales Support decides (owner 2026-10-08; reshaped 2026-10-09). Photos are no longer
required: the branch sends them to Sales Support by LINE.

A claim has an ORIGIN:
    'receive'  raised inside the receive form: the branch received LESS than the warehouse sent and
               claims the gap (create_from_receive). The order receipt is already booked at the
               received quantity, so the stock is already short — approving writes NO negative
               receipt. A rejection (and the unapproved remainder of a partial approval) adds the
               goods BACK as a positive 'claim' receipt, because the branch is treated as having
               received them.
    'after'    raised later, from the claims card, against what the order received (create):
               approving ADJUSTS the stock — a negative goods-receipt line (source 'claim', dated
               the day the order was received) takes the claimed quantity out of what the branch
               "received", so the usage variance does not count goods that were never usable.
               (NULL origin on older rows reads as 'after'.)

A later claim is made against ONE received order and only for what that order delivered; the sum
of the open and approved 'after' claims of a line can never exceed the received quantity. Photos
that do arrive (older claims, or a client that still sends them) are kept in the database and are
served only to the branch that sent them and to the supply roles.
"""
import json

from .clock import today as bkk_today
from .fmt import item as _item
from .master import audit
from .variance import now

REASONS = [
    ("short", "ส่งไม่ครบ / จำนวนขาด"),
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
ORIGIN_TH = {"receive": "แจ้งขณะรับสินค้า", "after": "แจ้งภายหลัง"}
OPEN = ("submitted", "approved")          # the statuses that use up a line's claimable quantity


def _origin(v) -> str:
    return v or "after"
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
    """What can still be claimed LATER on a received order: per line, received minus what open/approved
    'after' claims took. A claim raised inside the receive form (origin 'receive') claims the gap
    between what was sent and what was received — goods never booked as received — so it is not
    counted against the received quantity."""
    o = _order(con, branch_id, order_id)
    if o["status"] != "received":
        raise ValueError("แจ้งเคลมได้เมื่อบันทึกรับสินค้าของใบสั่งซื้อนี้แล้วเท่านั้น")
    names = dict(con.execute("SELECT code, name_th FROM bc_ingredient"))
    taken = {}
    for c, cu, au, st in con.execute(
            "SELECT l.ingredient_code, l.claim_units, l.approved_units, c.status FROM bc_claim_line l JOIN bc_claim c"
            " USING(claim_id) WHERE c.order_id=? AND c.status IN ('submitted','approved')"
            " AND COALESCE(c.origin,'after')='after'", (order_id,)):
        taken[c] = taken.get(c, 0.0) + float(au if st == "approved" and au is not None else cu)
    lines = []
    from .orders import unit_of_line
    meta = {c: (r, b) for c, r, b in con.execute("SELECT code, recv_unit, base_unit FROM bc_ingredient")}
    for c, u, recv, pu, cf in con.execute(
            "SELECT ingredient_code, order_unit, received_units, processed_units, confirmed_units FROM bc_order_line"
            " WHERE order_id=? AND COALESCE(received_units,0)>0 ORDER BY ingredient_code", (order_id,)):
        left = round(float(recv) - taken.get(c, 0.0), 6)
        sent = pu if pu is not None else cf
        lbl, word = unit_of_line(*(meta.get(c) or (None, None)), u)      # PR-W: the unit word, never a hard-coded "แพ็ค"
        lines.append({"code": c, "name": names.get(c), "order_unit": u, "unit_word": word, "unit_label": lbl, "received_units": float(recv),
                      "sent_units": float(sent or 0), "claimed_units": round(taken.get(c, 0.0), 6),
                      "remaining_units": max(left, 0.0)})
    return {"order_id": order_id, "cycle_date": o["cycle_date"], "round": o["round"], "lines": lines}


def _clean_lines(lines: list, limit: dict, over_msg, con=None) -> list:
    """Validate claim lines → [(code, qty, reason, note)]. `limit` = {code: most that may be claimed};
    a code outside it, or a quantity above it, raises with `over_msg(code, cap, qty)`."""
    clean = []
    for ln in lines or []:
        code = (ln.get("code") or "").strip()
        if not code:
            continue
        try:
            q = float(ln.get("claim_units"))
        except (TypeError, ValueError):
            raise ValueError(f"{_item(con, code)}: จำนวนที่เคลมต้องเป็นตัวเลข")
        if q <= 0:
            continue
        if code not in limit:
            raise ValueError(f"{_item(con, code)} ไม่ได้อยู่ในรายการที่รับของใบสั่งซื้อนี้")
        if q > limit[code] + 1e-9:
            raise ValueError(over_msg(code, limit[code], q))
        rs = (ln.get("reason") or "").strip()
        if rs not in REASON_TH:
            raise ValueError(f"{_item(con, code)}: เลือกสาเหตุของการเคลม")
        nt = (ln.get("note") or "").strip() or None
        if rs == "other" and not nt:
            raise ValueError(f"{_item(con, code)}: สาเหตุ “อื่น ๆ” ต้องระบุคำอธิบาย")
        clean.append((code, q, rs, nt))
    if not clean:
        raise ValueError("ระบุรายการที่จะเคลมอย่างน้อย 1 รายการ")
    if len({c for c, *_ in clean}) != len(clean):
        raise ValueError("มีรายการซ้ำ · รวมเป็นบรรทัดเดียว")
    return clean


def _insert(con, branch_id: int, order_id: int, origin: str, clean: list, photos: list, note: str, who: str) -> int:
    """Write the claim, its lines and any (already validated) photos; returns claim_id."""
    cur = con.execute("INSERT INTO bc_claim(branch_id, order_id, status, note, created_by, created_at, origin)"
                      " VALUES(?,?,?,?,?,?,?)",
                      (branch_id, order_id, "submitted", (note or "").strip() or None, who, now(), origin))
    cid = cur.lastrowid
    con.executemany("INSERT INTO bc_claim_line(claim_id, ingredient_code, claim_units, reason_code, note) VALUES(?,?,?,?,?)",
                    [(cid, c, q, rs, nt) for c, q, rs, nt in clean])
    con.executemany("INSERT INTO bc_claim_photo(claim_id, mime, size, data, created_at) VALUES(?,?,?,?,?)",
                    [(cid, m, len(d), d, now()) for m, d in photos])
    audit(con, who, "claim_create", "bc_claim", str(cid), None, {"branch_id": branch_id, "order_id": order_id,
                                                                 "lines": len(clean), "photos": len(photos),
                                                                 "origin": origin})
    return cid


def create(con, branch_id: int, order_id: int, lines: list, photos: list, note: str, who: str) -> dict:
    """A LATER claim (origin 'after') against what the order received. Photos are optional (they go to
    Sales Support by LINE); any that arrive are checked by their own bytes. Returns {claim_id}."""
    can = {ln["code"]: ln for ln in claimable(con, branch_id, order_id)["lines"]}

    def over(code, cap, q):
        return (f"{code}: เคลมได้ไม่เกิน {cap:g} (รับแล้ว {can[code]['received_units']:g}"
                + (f" · เคลมไปแล้ว {can[code]['claimed_units']:g}" if can[code]["claimed_units"] else "") + ")")

    clean = _clean_lines(lines, {c: ln["remaining_units"] for c, ln in can.items()}, over, con)
    photos = photos or []
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
    cid = _insert(con, branch_id, order_id, "after", clean, checked, note, who)
    return {"claim_id": cid, "lines": len(clean), "photos": len(checked), "origin": "after"}


def create_from_receive(con, branch_id: int, order_id: int, lines: list, note: str, who: str) -> dict:
    """The claim raised by orders.receive for the gap between what the warehouse sent and what the branch
    received. lines = [{code, claim_units, reason, note, sent_units, received_units}]; each claim_units
    is capped at sent − received. No photos (they go by LINE)."""
    cap = {}
    for ln in lines or []:
        c = (ln.get("code") or "").strip()
        if c:
            cap[c] = round(float(ln.get("sent_units") or 0) - float(ln.get("received_units") or 0), 6)

    def over(code, limit, q):
        return f"{code}: เคลมขณะรับสินค้าได้ไม่เกินส่วนต่างระหว่างที่คลังส่งกับที่รับจริง ({limit:g})"

    clean = _clean_lines(lines, cap, over, con)
    cid = _insert(con, branch_id, order_id, "receive", clean, [], note, who)
    return {"claim_id": cid, "lines": len(clean), "photos": 0, "origin": "receive"}


def _head(con, claim_id):
    r = con.execute(
        "SELECT c.claim_id, c.branch_id, b.name, c.order_id, c.status, c.note, c.created_by, c.created_at, c.decided_by,"
        " c.decided_at, c.decision_note, c.adjust_receipt_id, o.cycle_date, o.round, c.origin FROM bc_claim c"
        " LEFT JOIN branches b USING(branch_id) LEFT JOIN bc_order o USING(order_id) WHERE c.claim_id=?", (claim_id,)).fetchone()
    if not r:
        return None
    d = dict(zip(("claim_id", "branch_id", "branch", "order_id", "status", "note", "created_by", "created_at", "decided_by",
                  "decided_at", "decision_note", "adjust_receipt_id", "cycle_date", "round", "origin"), r))
    d["status_th"] = STATUS_TH.get(d["status"], d["status"])
    d["origin"] = _origin(d["origin"])
    d["origin_th"] = ORIGIN_TH[d["origin"]]
    # 4.2 (centre): two claims on one order are told apart by when they were raised, never by a number - `nth` is this claim's place
    # among the order's claims (oldest = 1, any status) and `of_order` how many the order has; the screens write "ครั้งที่ 2" from 2 up
    d["nth"], d["of_order"] = con.execute(
        "SELECT (SELECT COUNT(*) FROM bc_claim WHERE order_id=? AND claim_id<=?), (SELECT COUNT(*) FROM bc_claim WHERE order_id=?)",
        (d["order_id"], claim_id, d["order_id"])).fetchone()
    return d


def _delivery(con, h: dict):
    """PR-M4: the delivery day of the claim's order — a claim is read as "round · delivery day · N lines", not by its id."""
    from .hub import delivery_of
    if not h.get("cycle_date"):
        return None
    dd = con.execute("SELECT delivery_date FROM bc_order WHERE order_id=?", (h["order_id"],)).fetchone()
    return delivery_of(con, h["branch_id"], h["round"], h["cycle_date"], dd[0] if dd else None)


def detail(con, claim_id: int) -> dict:
    d = _head(con, claim_id)
    if not d:
        raise ValueError("ไม่พบรายการเคลม")
    d["delivery"] = _delivery(con, d)
    names = dict(con.execute("SELECT code, name_th FROM bc_ingredient"))
    units = {c: u for c, u in con.execute("SELECT ingredient_code, order_unit FROM bc_order_line WHERE order_id=?", (d["order_id"],))}
    recv = {c: r for c, r in con.execute("SELECT code, recv_unit FROM bc_ingredient")}
    base = {c: b for c, b in con.execute("SELECT code, base_unit FROM bc_ingredient")}
    sent, got = {}, {}
    for c, pu, cf, rc in con.execute("SELECT ingredient_code, processed_units, confirmed_units, received_units"
                                     " FROM bc_order_line WHERE order_id=?", (d["order_id"],)):
        sent[c] = float((pu if pu is not None else cf) or 0)
        got[c] = float(rc or 0)
    from .orders import unit_of_line
    d["lines"] = [{"code": c, "name": names.get(c), "claim_units": q, "approved_units": au, "reason": rs,
                   "reason_th": REASON_TH.get(rs, rs), "note": nt,
                   "sent_units": sent.get(c), "received_units": got.get(c),
                   # PR-W: `unit` is the unit WORD ("กล่อง"), `unit_label` the register's whole label ("กล่อง (10 แพ็ค)")
                   "unit": unit_of_line(recv.get(c), base.get(c), units.get(c) or "base")[1],
                   "unit_label": unit_of_line(recv.get(c), base.get(c), units.get(c) or "base")[0]}
                  for c, q, rs, nt, au in con.execute(
                      "SELECT ingredient_code, claim_units, reason_code, note, approved_units FROM bc_claim_line"
                      " WHERE claim_id=? ORDER BY ingredient_code", (claim_id,))]
    d["stock_adjusted"] = d["origin"] == "after"            # only a later claim, once approved, takes stock out
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
        h["delivery"] = _delivery(con, h)
        h["lines"] = con.execute("SELECT COUNT(*) FROM bc_claim_line WHERE claim_id=?", (cid,)).fetchone()[0]
        h["photos"] = con.execute("SELECT COUNT(*) FROM bc_claim_photo WHERE claim_id=?", (cid,)).fetchone()[0]
        out.append(h)
    return out


def for_order(con, order_id: int) -> list:
    """Every non-cancelled claim of one order (any origin) with its lines, for the order's detail."""
    out = []
    for (cid,) in con.execute("SELECT claim_id FROM bc_claim WHERE order_id=? AND status<>'cancelled'"
                              " ORDER BY claim_id", (order_id,)).fetchall():
        d = detail(con, cid)
        out.append({"claim_id": cid, "status": d["status"], "status_th": d["status_th"], "origin": d["origin"],
                    "origin_th": d["origin_th"], "lines": d["lines"], "created_at": d["created_at"],
                    "nth": d["nth"], "of_order": d["of_order"]})
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

    origin 'after': approving books the stock adjustment — a negative goods receipt dated the day the
    order was received. origin 'receive': the order receipt is already at the received quantity, so
    approving writes NO negative receipt; whatever is NOT approved (all of it on a rejection) is added
    back as a positive 'claim' receipt, because the branch is treated as having received those goods."""
    from .variance import add_receipt
    h = _head(con, claim_id)
    if not h:
        raise ValueError("ไม่พบรายการเคลม")
    if h["status"] != "submitted":
        raise ValueError(f"เคลมนี้พิจารณาแล้ว ({h['status_th']})")
    note = (note or "").strip() or None
    origin = h["origin"]
    units = {c: u for c, u in con.execute("SELECT ingredient_code, order_unit FROM bc_order_line WHERE order_id=?", (h["order_id"],))}
    recv = {c: r for c, r in con.execute("SELECT code, recv_unit FROM bc_ingredient")}
    base = {c: b for c, b in con.execute("SELECT code, base_unit FROM bc_ingredient")}
    from .fmt import round_day_th                         # 4.2: a note names the order by its round and delivery day, never a number
    from .hub import delivery_of
    ob = con.execute("SELECT round, cycle_date, delivery_date FROM bc_order WHERE order_id=?", (h["order_id"],)).fetchone()
    lbl = round_day_th(ob[0], delivery_of(con, h["branch_id"], ob[0], ob[1], ob[2])) if ob else "ใบสั่งซื้อ"

    def _book(sign, qtys, note_txt):
        """One 'claim' receipt dated the order's receipt, so the same variance period sees it."""
        rec = con.execute("SELECT receipt_date FROM bc_receipt WHERE branch_id=? AND ref=? ORDER BY receipt_id LIMIT 1",
                          (h["branch_id"], f"ORDER-{h['order_id']}")).fetchone()
        rdate = rec[0] if rec else (con.execute("SELECT SUBSTR(received_at,1,10) FROM bc_order WHERE order_id=?",
                                                (h["order_id"],)).fetchone() or [None])[0] or bkk_today().isoformat()
        rl = [{"code": c, "qty": q, "unit": (recv.get(c) if units.get(c) and units.get(c) != "base" else base.get(c))}
              for c, q in qtys]
        r = add_receipt(con, h["branch_id"], rdate, rl, who, ref=f"CLAIM-{claim_id}", note=note_txt, source="claim")
        if sign < 0:
            con.execute("UPDATE bc_receipt_line SET qty=-qty, qty_base=-qty_base WHERE receipt_id=?", (r["receipt_id"],))
        return r["receipt_id"], rdate

    if decision == "reject":
        if not note:
            raise ValueError("ระบุเหตุผลที่ไม่อนุมัติ")
        rid = rdate = None
        restored = 0
        if origin == "receive":
            back = [(c, float(cu)) for c, cu in con.execute(
                "SELECT ingredient_code, claim_units FROM bc_claim_line WHERE claim_id=? AND claim_units>0", (claim_id,))]
            if back:
                rid, rdate = _book(+1, back, f"สินค้าตามเคลม {lbl} ที่ไม่อนุมัติ ถือว่าสาขาได้รับแล้ว")
                restored = len(back)
        con.execute("UPDATE bc_claim SET status='rejected', decided_by=?, decided_at=?, decision_note=?,"
                    " adjust_receipt_id=? WHERE claim_id=?", (who, now(), note, rid, claim_id))
        audit(con, who, "claim_reject", "bc_claim", str(claim_id), "submitted",
              {"note": note, "origin": origin, "restored": restored, "receipt_id": rid})
        return {"claim_id": claim_id, "status": "rejected", "origin": origin, "adjusted": 0, "restored": restored,
                "receipt_id": rid, "receipt_date": rdate}
    if decision != "approve":
        raise ValueError("คำขอไม่ถูกต้อง")
    given = {(ln.get("code") or "").strip(): ln.get("approved_units") for ln in lines or []}
    rows = con.execute("SELECT ingredient_code, claim_units FROM bc_claim_line WHERE claim_id=?", (claim_id,)).fetchall()
    approved = []
    for c, cu in rows:
        v = given.get(c)
        try:
            a = float(cu if v is None or v == "" else v)
        except (TypeError, ValueError):
            raise ValueError(f"{_item(con, c)}: จำนวนที่อนุมัติต้องเป็นตัวเลข")
        if a < 0 or a > float(cu) + 1e-9:
            raise ValueError(f"{_item(con, c)}: อนุมัติได้ 0 ถึง {float(cu):g} (จำนวนที่สาขาเคลม)")
        approved.append((c, a))
    if not any(a > 0 for _, a in approved):
        raise ValueError("ระบุจำนวนที่อนุมัติอย่างน้อย 1 รายการ หรือเลือก “ไม่อนุมัติ”")
    for c, a in approved:
        con.execute("UPDATE bc_claim_line SET approved_units=? WHERE claim_id=? AND ingredient_code=?", (a, claim_id, c))
    rid = rdate = None
    adjusted = restored = 0
    if origin == "after":
        # the adjustment: dated the receipt of the order, so the same variance period loses these goods
        plus = [(c, a) for c, a in approved if a > 0]
        rid, rdate = _book(-1, plus, f"ปรับสินค้าตามเคลม {lbl}")
        adjusted = len(plus)
    else:
        # already received at the lower quantity: nothing comes out; the part not approved goes back in
        cl = dict(rows)
        back = [(c, round(float(cl[c]) - a, 6)) for c, a in approved if float(cl[c]) - a > 1e-9]
        if back:
            rid, rdate = _book(+1, back, f"สินค้าตามเคลม {lbl} ส่วนที่ไม่อนุมัติ ถือว่าสาขาได้รับแล้ว")
            restored = len(back)
    con.execute("UPDATE bc_claim SET status='approved', decided_by=?, decided_at=?, decision_note=?, adjust_receipt_id=?"
                " WHERE claim_id=?", (who, now(), note, rid, claim_id))
    audit(con, who, "claim_approve", "bc_claim", str(claim_id), "submitted",
          json.dumps({"receipt_id": rid, "lines": len([1 for _, a in approved if a > 0]),
                      "receipt_date": rdate, "origin": origin, "adjusted": adjusted, "restored": restored},
                     ensure_ascii=False))
    return {"claim_id": claim_id, "status": "approved", "origin": origin, "adjusted": adjusted, "restored": restored,
            "receipt_id": rid, "receipt_date": rdate}


def counts(con) -> dict:
    return {s: n for s, n in con.execute("SELECT status, COUNT(*) FROM bc_claim GROUP BY status")}
