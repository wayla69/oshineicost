# -*- coding: utf-8 -*-
"""Phase 2 · Sprint S7 — goods received and usage variance per count period
(docs/BOM_CENTER_PLAN.md §7.1-2/4, §7.2-4).

A PERIOD runs from one submitted count to the next. A count reflects what is on
the shelves BEFORE that day's trading and deliveries, so a period covers the
sales and the deliveries from the begin count's date up to the day before the
end count.

    actual use   = begin + received − end                 (what left the shelves)
    BoM use      = Σ plates sold × recipe (+ trim loss)   (what the recipes say)
    gap          = actual − BoM          > 0 = used more than the recipes allow

Nothing is guessed: an item missing from either count has no actual use, and a
period whose deliveries the branch has not confirmed as complete is shown as
INCOMPLETE (a missing delivery would read as a shortage). Thresholds follow the
plan: class A ±5 %, B ±10 %, C ±20 % (class = share of BoM ฿ in the period).
"""
import json
import re
from datetime import date, datetime, time, timedelta, timezone

from .fmt import day_th as _fmt_day
from .fmt import item as _item
from .fmt import no_unit as _no_unit
from .fmt import reasons as _reasons
from .master import audit
from .master import now as _now
from .units import Converter, unit_word

THRESHOLD = {"A": 5.0, "B": 10.0, "C": 20.0}
REASONS = _reasons("waste", "expired", "staff", "overportion", "transfer", "miscount", "receipt", "other")   # fmt.REASON_LABEL
REASON_TH = dict(REASONS)
FLAG_STATUS = ("over", "under", "negative", "no_bom")   # the statuses that are outside the threshold
ASK_TOP = 20                                            # PR-M4 (owner Q3): a reason is asked for class A and for the top 20 by |฿|


INCOMPLETE_TH = "ข้อมูลไม่ครบ"                           # batch 4b (3.3): a line the branch is not asked about, because the data cannot say
SYSTEM_TH = "ของในระบบ (ไม่ได้นับ)"                  # batch 4b (3.4): an end figure taken from the system stock, not from a count
USED_ZERO_PCT = -99.95            # a gap that rounds to -100.0 % on the screen: the item was used 0 (the counts are the same) against a recipe
_EPS = 1e-9


def _gap_value_for_rank(r: dict):
    """The ฿ a line is ranked by when a reason is asked: its own, else - PR-B4a (1.6), an item with no price - the gap at the fallback
    price (`ref_value`). The ฿ shown on the screen stays `gap_value` (None for an unpriced item)."""
    return r.get("gap_value") if r.get("gap_value") is not None else r.get("ref_value")


def ask_reason_codes(rows: list, top: int = ASK_TOP, min_baht: float = 0.0) -> set:
    """The codes a branch is asked to explain (owner Q3, PR-M4): a flagged line (outside the threshold) that is class A, or
    among the `top` flagged lines by |฿ variance|. Rows need `code`, `status`, `class` and `gap_value` (`ref_value` for an unpriced item).

    Batch 4b (3.3): a line is asked only when it is ALSO at least `min_baht` baht off (the setting bc_reason_min_baht; 0 = no floor;
    an item with no price at all cannot reach a baht floor) and the data can say something: a row marked `data_incomplete` (used 0
    against a recipe, or a period with no receipt at all) or `system_basis` (the end figure was not counted) is labelled, not asked."""
    flagged = [r for r in rows if r["status"] in FLAG_STATUS and not r.get("data_incomplete") and not r.get("system_basis")]

    def big(r):
        if not min_baht or min_baht <= 0:
            return True
        v = _gap_value_for_rank(r)
        return v is not None and abs(v) >= min_baht
    flagged = [r for r in flagged if big(r)]
    biggest = {r["code"] for r in sorted((r for r in flagged if _gap_value_for_rank(r) is not None),
                                         key=lambda r: (-abs(_gap_value_for_rank(r)), r["code"]))[:top]}
    return {r["code"] for r in flagged if r["class"] == "A" or r["code"] in biggest}


def with_count_units(con, rows: list) -> list:
    """Add `count_unit` and `per_count_unit` (base units in one count unit) to every row that has a `code`, so that a
    branch page can show the quantity in the unit the kitchen counts in. Falls back to the base unit (factor 1)."""
    from .stock import count_units
    cu = count_units(con, {r["code"] for r in rows if r.get("code")})
    for r in rows:
        u, f = cu.get(r.get("code"), (None, None))
        if u and f and f > 0:
            r["count_unit"], r["per_count_unit"] = u, float(f)
        else:
            r["count_unit"], r["per_count_unit"] = r.get("base_unit"), 1.0
    return rows


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── goods received ───────────────────────────────────────────────────────────
def _pieces_of(conv, code: str, raw):
    """The whole pieces a branch counted for an item sold by weight (PR-U2): a whole number >= 1, kept only for an item that has
    an average weight step (a salmon, ตัว ≈ 4.5 กก.); None when nothing was counted or the item has no such step."""
    if raw is None or str(raw).strip() == "" or conv.weight_avg_step(code) is None:
        return None
    try:
        p = float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        raise ValueError(f"{_item(conv, code)}: จำนวนต้องเป็นตัวเลขจำนวนเต็ม")
    if p != int(p) or p < 1:
        raise ValueError(f"{_item(conv, code)}: จำนวนที่นับต้องเป็นจำนวนเต็มตั้งแต่ 1 ขึ้นไป")
    return float(int(p))


def add_receipt(con, branch_id: int, receipt_date: str, lines: list, who: str, ref: str = None,
                note: str = None, source: str = "branch") -> dict:
    try:
        date.fromisoformat(receipt_date)
    except (TypeError, ValueError):
        raise ValueError("วันที่รับของต้องอยู่ในรูปแบบ YYYY-MM-DD")
    conv = Converter(con)
    clean, bad = {}, []
    for ln in lines or []:
        code = (ln.get("code") or "").strip()
        if not code:
            continue
        try:
            q = float(ln.get("qty"))
        except (TypeError, ValueError):
            raise ValueError(f"{_item(con, code)}: จำนวนต้องเป็นตัวเลข")
        if q <= 0:
            raise ValueError(f"{_item(con, code)}: จำนวนต้องมากกว่า 0")
        unit = (ln.get("unit") or "").strip() or conv.base_unit(code)
        qb, how = conv.to_base(code, q, unit)
        if qb is None:
            bad.append(_no_unit(con, code, unit))
        prev = clean.get(code)
        if prev:                                             # the same item twice in one delivery: add up
            if prev[1] != unit:
                raise ValueError(f"{_item(con, code)}: ระบุซ้ำด้วยหน่วยที่ต่างกัน ({prev[1]} / {unit}) · รวมเป็นบรรทัดเดียว")
            q = prev[0] + q
            qb = None if qb is None or prev[2] is None else prev[2] + qb
        pcs = _pieces_of(conv, code, ln.get("pieces"))
        if prev and (prev[3] or pcs):
            pcs = (prev[3] or 0.0) + (pcs or 0.0)
        clean[code] = (q, unit, qb, pcs)
    if not clean:
        raise ValueError("ยังไม่มีรายการรับของ")
    _refuse_if_posted(con, branch_id, receipt_date)
    if bad:
        raise ValueError("บันทึกไม่ได้: " + "; ".join(bad[:3]) + " · แจ้งฝ่ายวิเคราะห์")
    cur = con.execute("INSERT INTO bc_receipt(branch_id, receipt_date, source, ref, note, entered_by, created_at)"
                      " VALUES(?,?,?,?,?,?,?)", (branch_id, receipt_date, source, ref, note, who, now()))
    rid = cur.lastrowid
    con.executemany("INSERT INTO bc_receipt_line(receipt_id, ingredient_code, qty, unit, qty_base, pieces, pieces_unit)"
                    " VALUES(?,?,?,?,?,?,?)",
                    [(rid, c, q, u, qb, pcs, (conv.weight_avg_step(c) or {}).get("unit") if pcs else None)
                     for c, (q, u, qb, pcs) in clean.items()])
    audit(con, who, "receipt", "bc_receipt", str(rid), None, {"branch_id": branch_id, "date": receipt_date,
                                                             "lines": len(clean)})
    return {"receipt_id": rid, "lines": len(clean)}


def assert_not_posted(con, branch_id: int, day: str, what: str = "ใบรับของ") -> None:
    """A document dated inside a period whose ใบสรุปตัดเบิก Accounting has posted would change numbers it
    already keyed: refuse until Accounting un-posts it. A period is [begin count date, end count date) — the
    month-end count is taken before trading, so the end date itself belongs to the NEXT period. Used for
    receipts (this also locks claim approvals in that period) and for confirming / reopening an issue slip."""
    from .stock import periods as _p
    posted = {r[0] for r in con.execute(
        "SELECT end_count_id FROM bc_variance_period WHERE branch_id=? AND accounting_posted_at IS NOT NULL",
        (branch_id,))}
    if not posted:
        return
    for b, e in _p(con, branch_id):
        if e["count_id"] in posted and b["date"] <= day < e["date"]:
            raise ValueError(f"{what}วันที่ {_fmt_day(day)} อยู่ในงวดที่บัญชีบันทึกใบสรุปตัดเบิกแล้ว "
                             f"({_fmt_day(b['date'])} ถึง {_fmt_day(e['date'])}) จึงแก้ไขไม่ได้ · แจ้งบัญชีให้ยกเลิกการบันทึกก่อน")


def _refuse_if_posted(con, branch_id: int, receipt_date: str) -> None:
    assert_not_posted(con, branch_id, receipt_date, "ใบรับของ")


def _closed_through(con, branch_id: int):
    """The last END count's date: receipts before it belong to a measured period."""
    r = con.execute("SELECT MAX(count_date) FROM bc_stock_count WHERE branch_id=? AND kind='end'"
                    " AND COALESCE(status,'submitted')='submitted'", (branch_id,)).fetchone()
    return r[0] if r else None


CANCEL_REASON_MIN = 3
CANCEL_REASON_MAX = 300


def delete_receipt(con, branch_id: int, receipt_id: int, who: str, reason: str) -> bool:
    """Cancel a receipt (batch 3, 3.6: the button is "ยกเลิกใบรับของ"). The receipt row is really deleted, as before — the
    stock figures read bc_receipt — but a reason is REQUIRED and the whole receipt (header and every line) is first written to
    bc_receipt_cancel and to the audit log, so what was cancelled, by whom, when and why is never lost. Sales Support reads the
    last 7 days (cancelled_receipts)."""
    why = " ".join(str(reason or "").split())
    if len(why) < CANCEL_REASON_MIN:
        raise ValueError("ต้องระบุเหตุผลในการยกเลิกใบรับของ")
    why = why[:CANCEL_REASON_MAX]
    r = con.execute("SELECT receipt_date, source, ref, note, entered_by, created_at FROM bc_receipt WHERE receipt_id=? AND branch_id=?",
                    (receipt_id, branch_id)).fetchone()
    if not r:
        raise ValueError("ไม่พบใบรับของนี้")
    if r[1] == "claim":
        raise ValueError("รายการนี้เกิดจากการอนุมัติเรื่องที่แจ้ง ลบไม่ได้ หากต้องแก้ไขให้แจ้งประสานงานขาย")
    closed = _closed_through(con, branch_id)
    if closed and r[0] < closed:
        raise ValueError(f"ใบรับของวันที่ {_fmt_day(r[0])} อยู่ในเดือนที่ปิดแล้ว ({_fmt_day(closed)}) จึงลบไม่ได้ · แจ้งฝ่ายวิเคราะห์")
    cur = con.execute("SELECT * FROM bc_receipt_line WHERE receipt_id=? ORDER BY ingredient_code", (receipt_id,))
    cols = [d[0] for d in cur.description]
    lines = [dict(zip(cols, row)) for row in cur.fetchall()]
    snap = {"receipt_id": receipt_id, "branch_id": branch_id, "receipt_date": r[0], "source": r[1], "ref": r[2], "note": r[3],
            "entered_by": r[4], "created_at": r[5], "lines": lines}
    blob = json.dumps(snap, ensure_ascii=False, default=str)
    con.execute("INSERT INTO bc_receipt_cancel(receipt_id, branch_id, receipt_date, source, ref, entered_by, created_at, line_count,"
                " cancel_reason, cancelled_by, cancelled_at, snapshot) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (receipt_id, branch_id, r[0], r[1], r[2], r[4], r[5], len(lines), why, who, _now(), blob))
    audit(con, who, "receipt_cancel", "bc_receipt", str(receipt_id), blob, why)      # the record first, then the delete
    con.execute("DELETE FROM bc_receipt_line WHERE receipt_id=?", (receipt_id,))
    con.execute("DELETE FROM bc_receipt WHERE receipt_id=?", (receipt_id,))
    return True


def cancelled_receipts(con, days: int = 7, branch_id: int = None, today=None) -> list:
    """The receipts a branch cancelled in the last `days` days (newest first), with the reason, who and when, for Sales
    Support's claims / slips pane: [{cancel_id, branch_id, branch, receipt_date, ref_th, lines, reason, cancelled_by, cancelled_at}]."""
    from .clock import BKK, today as _today
    t = today or _today()
    first = t - timedelta(days=max(int(days), 1) - 1)                        # midnight Bangkok of the first day, as the UTC stamp stored
    since = datetime.combine(first, time.min, BKK).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    q = ("SELECT cancel_id, branch_id, receipt_id, receipt_date, source, ref, line_count, cancel_reason, cancelled_by, cancelled_at"
         " FROM bc_receipt_cancel WHERE cancelled_at >= ?" + (" AND branch_id=?" if branch_id else "")
         + " ORDER BY cancel_id DESC")
    names = dict(con.execute("SELECT branch_id, name FROM branches"))
    out = []
    for cid, bid, rid, rdate, src, ref, n, why, by, at in con.execute(q, (since,) + ((int(branch_id),) if branch_id else ())).fetchall():
        out.append({"cancel_id": cid, "branch_id": bid, "branch": names.get(bid), "receipt_id": rid, "receipt_date": rdate,
                    "source": src, "ref_th": _ref_th(con, bid, ref, src), "lines": n, "reason": why, "cancelled_by": by,
                    "cancelled_at": _bkk_stamp(at)})
    return out


def _bkk_stamp(ts) -> str:
    """A stored UTC stamp as the branch's clock: 'YYYY-MM-DD HH:MM' (Bangkok)."""
    from .clock import BKK
    try:
        t = datetime.fromisoformat(str(ts))
    except ValueError:
        return str(ts or "")
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(BKK).strftime("%Y-%m-%d %H:%M")


def _ref_th(con, branch_id: int, ref, source=None):
    """How a receipt is named on the branch's screen (string 10): "รับตามใบสั่งซื้อ รอบ 2 (ส่ง 10/10)" for ORDER-8, "ปรับตามเคลม รอบ 2
    (ส่ง 10/10)" for CLAIM-1, "รายงานไดกิโตะ กรุ๊ป เดือนกันยายน 2026" for CENTRAL-2026-09; None for a reference the person typed (shown as written)."""
    from .fmt import month_th
    from .forecast import ROUND_TH
    from .hub import delivery_of
    r = str(ref or "")

    def of_order(oid):
        o = con.execute("SELECT round, cycle_date, delivery_date FROM bc_order WHERE order_id=?", (oid,)).fetchone()
        if not o:
            return None
        d = date.fromisoformat(delivery_of(con, branch_id, o[0], o[1], o[2]))
        return f"{ROUND_TH.get(o[0], o[0])} (ส่ง {d.day}/{d.month})"
    m = re.fullmatch(r"ORDER-(\d+)", r)
    if m:
        t = of_order(int(m.group(1)))
        return f"รับตามใบสั่งซื้อ {t}" if t else "รับตามใบสั่งซื้อ"
    m = re.fullmatch(r"CLAIM-(\d+)", r)
    if m:
        row = con.execute("SELECT order_id FROM bc_claim WHERE claim_id=?", (int(m.group(1)),)).fetchone()
        t = of_order(row[0]) if row else None
        return f"ปรับตามที่แจ้งของขาด/เสีย {t}" if t else "ปรับตามที่แจ้งของขาด/เสีย"
    m = re.fullmatch(r"CENTRAL-(\d{4}-\d{2})", r)
    if m:
        return f"รายงานไดกิโตะ กรุ๊ป เดือน{month_th(m.group(1))}"
    return None


def receipts(con, branch_id: int, limit: int = 30) -> list:
    names = dict(con.execute("SELECT code, name_th FROM bc_ingredient"))
    out = []
    for rid, d, src, ref, by in con.execute(
            "SELECT receipt_id, receipt_date, source, ref, entered_by FROM bc_receipt WHERE branch_id=?"
            " ORDER BY receipt_date DESC, receipt_id DESC LIMIT ?", (branch_id, limit)).fetchall():
        lines = [{"code": c, "name": names.get(c), "qty": q, "unit": u, "unit_word": unit_word(u), "qty_base": qb, "pieces": pc, "pieces_unit": pu}
                 for c, q, u, qb, pc, pu in con.execute(
                     "SELECT ingredient_code, qty, unit, qty_base, pieces, pieces_unit FROM bc_receipt_line WHERE receipt_id=?"
                     " ORDER BY ingredient_code", (rid,))]
        out.append({"receipt_id": rid, "receipt_date": d, "source": src, "ref": ref, "entered_by": by, "lines": lines})
    closed = _closed_through(con, branch_id)
    from .pack_reports import for_orders          # PR-U2: what the analyst answered to "ขนาดแพ็คไม่ตรง", read on the receive history
    reports = for_orders(con, branch_id)
    for r in out:
        r["locked"] = bool(closed and r["receipt_date"] < closed)
        m = re.fullmatch(r"ORDER-(\d+)", r["ref"] or "")
        r["pack_reports"] = reports.get(int(m.group(1)), []) if m else []
        r["ref_th"] = _ref_th(con, branch_id, r["ref"], r["source"])
    return out


# ── variance ─────────────────────────────────────────────────────────────────
def _count_lines(con, count_id: int) -> dict:
    return {c: qb for c, qb in con.execute(
        "SELECT ingredient_code, qty_base FROM bc_stock_count_line WHERE count_id=?", (count_id,)) if qb is not None}


def _count_basis(con, count_id: int) -> dict:
    """{code: 'system'|'cycle'} of the lines of a PARTIAL end count (stock.partial_close) that were not counted that day: 'system' =
    the system stock carried to the date, 'cycle' = the latest weekly count of a class-A item carried to the date. A full count has none."""
    try:
        return {c: b for c, b in con.execute(
            "SELECT ingredient_code, basis FROM bc_stock_count_line WHERE count_id=? AND basis IS NOT NULL", (count_id,))}
    except Exception:                                   # a database that has not been migrated yet has no such column
        return {}


def _price_month(begin_date: str, end_date: str) -> str:
    """The month a period is priced in: the month of the day BEFORE its end count (end - 1 day)."""
    last_day = date.fromisoformat(end_date) - timedelta(days=1)
    return f"{last_day.year:04d}-{last_day.month:02d}"


def _movements(con, branch_id: int, b: dict, e: dict) -> dict:
    """Everything that moved stock in [begin date, end date): both counts, deliveries and claim adjustments.

    'claims' = receipts booked by a claim decision (source 'claim'; negative when goods were short,
    positive when a receive-time claim was rejected); 'received' = every other receipt."""
    received, claims = {}, {}
    for c, is_claim, qb in con.execute(
            "SELECT l.ingredient_code, r.source='claim', SUM(l.qty_base) FROM bc_receipt r"
            " JOIN bc_receipt_line l USING(receipt_id)"
            " WHERE r.branch_id=? AND r.receipt_date>=? AND r.receipt_date<?"
            " GROUP BY l.ingredient_code, r.source='claim'", (branch_id, b["date"], e["date"])):
        (claims if is_claim else received)[c] = qb or 0.0
    n_receipts = con.execute("SELECT COUNT(*) FROM bc_receipt WHERE branch_id=? AND receipt_date>=? AND receipt_date<?",
                             (branch_id, b["date"], e["date"])).fetchone()[0]
    return {"begin": _count_lines(con, b["count_id"]), "end": _count_lines(con, e["count_id"]),
            "received": received, "claims": claims, "n_receipts": n_receipts}


def periods(con, branch_id: int) -> list:
    """Every measured period of a branch, newest first, with its headline numbers."""
    from .stock import periods as _p
    out = []
    for b, e in reversed(_p(con, branch_id)):
        v = period(con, branch_id, e["count_id"], summary_only=True)
        out.append(v)
    return out


def period(con, branch_id: int, end_count_id: int = None, summary_only: bool = False) -> dict:
    """Actual vs BoM use for one period (default: the latest)."""
    from .forecast import _abc, usage_history
    from .prices import price_for
    from .stock import periods as _p
    ps = _p(con, branch_id)
    if not ps:
        raise ValueError("ยังเทียบไม่ได้ · ต้องมีผลนับครั้งแรกและนับสิ้นเดือน")
    pair = ps[-1] if end_count_id is None else next((p for p in ps if p[1]["count_id"] == int(end_count_id)), None)
    if pair is None:
        raise ValueError("ไม่พบงวดนี้")
    b, e = pair
    d0, d1 = date.fromisoformat(b["date"]), date.fromisoformat(e["date"])
    last_day = d1 - timedelta(days=1)
    month = _price_month(b["date"], e["date"])
    mv = _movements(con, branch_id, b, e)
    begin, end, n_receipts = mv["begin"], mv["end"], mv["n_receipts"]
    rec = {c: mv["received"].get(c, 0.0) + mv["claims"].get(c, 0.0)
           for c in set(mv["received"]) | set(mv["claims"])}
    theo, days = {}, []
    if last_day >= d0:
        use, days, _ = usage_history(con, branch_id, d0, last_day, month)
        theo = {c: sum(s.values()) for c, s in use.items()}
    conf = con.execute("SELECT receipts_confirmed_by, receipts_confirmed_at FROM bc_variance_period WHERE end_count_id=?",
                       (e["count_id"],)).fetchone()
    reasons = {c: (r, n, w, a) for c, r, n, w, a in con.execute(
        "SELECT ingredient_code, reason_code, note, who, at FROM bc_variance_reason WHERE end_count_id=?", (e["count_id"],))}
    from .close_settings import reason_min_baht
    min_baht = reason_min_baht(con)
    end_basis, begin_basis = _count_basis(con, e["count_id"]), _count_basis(con, b["count_id"])
    price = {}
    from .prices import PriceBook
    pbook = PriceBook(con, month)                       # PR-B4a (1.6): ranks an unpriced item by a fallback price, shows none

    def p(c):
        if c not in price:
            pr = price_for(con, c, month)
            price[c] = pr[0] if pr else None
        return price[c]
    klass = _abc({c: q * pbook.ref(c) for c, q in theo.items()})
    names = {c: (n, u) for c, n, u in con.execute("SELECT code, name_th, base_unit FROM bc_ingredient")}
    rows = []
    for c in sorted(set(begin) | set(end) | set(theo) | set(rec)):
        k = klass.get(c, "C")
        t = theo.get(c, 0.0)
        r = rec.get(c, 0.0)
        row = {"code": c, "name": (names.get(c) or (c,))[0], "base_unit": (names.get(c) or (None, None))[1],
               "class": k, "begin": begin.get(c), "received": r, "end": end.get(c), "bom": round(t, 3),
               "price_per_base": p(c)}
        if c not in begin or c not in end:
            row.update(status="not_counted", actual=None, gap=None, gap_pct=None, gap_value=None)
        else:
            act = begin[c] + r - end[c]
            gap = act - t
            pct = (100.0 * gap / t) if t > 0 else None
            pv = p(c)
            row.update(actual=round(act, 3), gap=round(gap, 3), gap_pct=round(pct, 1) if pct is not None else None,
                       gap_value=round(gap * pv, 2) if pv is not None else None)
            if pv is None:                                  # PR-B4a (1.6): "ไม่มีราคา (ใช้ราคา …)" - the gap at the fallback, for the ranking only
                row.update(price_note=pbook.chip(c, row["base_unit"]),
                           ref_value=round(gap * pbook.ref(c), 2) if pbook.ref(c) else None)
            if act < 0:
                row["status"] = "negative"                   # more on the shelf than began + arrived: a missing receipt or a miscount
            elif t <= 0:
                row["status"] = "no_bom" if act > 0 else "ok"
            elif abs(pct) <= THRESHOLD[k]:
                row["status"] = "ok"
            else:
                row["status"] = "over" if gap > 0 else "under"
        row["end_basis"] = end_basis.get(c) or "count"
        row["begin_basis"] = begin_basis.get(c) or "count"
        if row["end_basis"] == "system":                        # 3.4: not counted - the gap is the slips against the recipes
            row.update(system_basis=True, basis_th=SYSTEM_TH)
        if row["status"] in FLAG_STATUS and n_receipts == 0:            # 3.3: no delivery is recorded in the whole period - every gap would read as a shortage
            row.update(data_incomplete=True, incomplete_th=INCOMPLETE_TH, incomplete_why="receipts")
        elif row["status"] == "under" and row.get("gap_pct") is not None and row["gap_pct"] <= USED_ZERO_PCT and row["bom"] > 0:
            row.update(data_incomplete=True, incomplete_th=INCOMPLETE_TH, incomplete_why="used_zero")      # -100 %: used (next to) nothing against a recipe
        rs = reasons.get(c)
        if rs:
            row.update(reason=rs[0], reason_th=REASON_TH.get(rs[0], rs[0]), reason_note=rs[1], reason_by=rs[2])
        rows.append(row)
    flagged = [r for r in rows if r["status"] in FLAG_STATUS]
    # PR-M4 (owner Q3): only a high-value line is asked for a reason — class A, or among the top ASK_TOP by |฿ variance|.
    # The other flagged lines are listed without a form; `needs_reason` follows (a line asked and not yet explained).
    ask = ask_reason_codes(rows, min_baht=min_baht)
    for r in rows:
        r["ask_reason"] = r["code"] in ask
        r["needs_reason"] = r["ask_reason"] and "reason" not in r
    counted = [r for r in rows if r["status"] != "not_counted" and r.get("actual") is not None and r["price_per_base"] is not None]
    summary = {
        "branch_id": branch_id, "begin_count_id": b["count_id"], "end_count_id": e["count_id"],
        "begin_date": b["date"], "end_date": e["date"], "days": (d1 - d0).days, "trading_days": len(days),
        "price_month": month, "receipts": n_receipts,
        "receipts_confirmed": bool(conf), "receipts_confirmed_by": conf[0] if conf else None,
        "complete": bool(conf),
        "gap_value": round(sum(r["gap_value"] or 0 for r in rows if r["gap_value"] is not None and r["status"] != "not_counted"), 2),
        "over_value": round(sum(r["gap_value"] for r in flagged if (r["gap_value"] or 0) > 0), 2),
        "bom_value": round(sum(r["bom"] * (r["price_per_base"] or 0) for r in rows), 2),
        "flagged": len(flagged), "needs_reason": sum(1 for r in rows if r["needs_reason"]),
        "not_counted": sum(1 for r in rows if r["status"] == "not_counted" and r["bom"] > 0),
        # PR-M4: the head of the branch page — what was used, what the recipes say for the same lines, and the gap
        "actual_value": round(sum(r["actual"] * r["price_per_base"] for r in counted), 2),
        "bom_value_counted": round(sum(r["bom"] * r["price_per_base"] for r in counted), 2),
        "ask_reason": sum(1 for r in rows if r["ask_reason"]),
        "reason_min_baht": min_baht,
        "data_incomplete": sum(1 for r in flagged if r.get("data_incomplete")),
        "system_basis": sum(1 for r in rows if r.get("system_basis")),
        "partial": bool(end_basis),
    }
    summary["totals"] = {"ask_reason": summary["ask_reason"], "ask_reason_open": summary["needs_reason"],
                         "flagged": summary["flagged"], "other_flagged": summary["flagged"] - summary["ask_reason"],
                         "incomplete": summary["data_incomplete"], "system_basis": summary["system_basis"]}
    if summary["bom_value"]:
        summary["gap_pct"] = round(100.0 * summary["gap_value"] / summary["bom_value"], 1)
    if summary_only:
        return summary
    order = {"negative": 0, "over": 1, "under": 2, "no_bom": 3, "not_counted": 4, "ok": 5}
    rows.sort(key=lambda r: (order[r["status"]], -abs(r["gap_value"] or 0), r["code"]))
    with_count_units(con, rows)
    return dict(summary, rows=rows, reasons=[{"code": k, "label": v} for k, v in REASONS],
                thresholds=THRESHOLD)


def set_reason(con, branch_id: int, end_count_id: int, code: str, reason: str, note: str, who: str):
    if reason not in REASON_TH:
        raise ValueError("เลือกเหตุผลจากรายการ")
    if reason == "other" and not (note or "").strip():
        raise ValueError("เหตุผล “อื่น ๆ” ต้องระบุคำอธิบาย")
    if not con.execute("SELECT 1 FROM bc_stock_count WHERE count_id=? AND branch_id=?", (end_count_id, branch_id)).fetchone():
        raise ValueError("ไม่พบงวดนี้ของสาขา")
    con.execute("INSERT INTO bc_variance_reason(end_count_id, ingredient_code, branch_id, reason_code, note, who, at)"
                " VALUES(?,?,?,?,?,?,?) ON CONFLICT(end_count_id, ingredient_code) DO UPDATE SET"
                " reason_code=excluded.reason_code, note=excluded.note, who=excluded.who, at=excluded.at",
                (end_count_id, code, branch_id, reason, (note or "").strip() or None, who, now()))
    audit(con, who, "variance_reason", "bc_variance_reason", f"{end_count_id}|{code}", None, reason)


def confirm_receipts(con, branch_id: int, end_count_id: int, who: str, undo: bool = False):
    if not con.execute("SELECT 1 FROM bc_stock_count WHERE count_id=? AND branch_id=?", (end_count_id, branch_id)).fetchone():
        raise ValueError("ไม่พบงวดนี้ของสาขา")
    if undo:
        posted = con.execute("SELECT 1 FROM bc_variance_period WHERE end_count_id=? AND accounting_posted_at IS NOT NULL",
                             (end_count_id,)).fetchone()
        if posted:
            raise ValueError("บัญชีบันทึกใบสรุปตัดเบิกของงวดนี้เข้าระบบบัญชีแล้ว ยกเลิกการยืนยันไม่ได้ "
                             "· แจ้งบัญชีให้ยกเลิกการบันทึกก่อน")
        con.execute("DELETE FROM bc_variance_period WHERE end_count_id=?", (end_count_id,))
    else:                                                    # an UPSERT: re-confirming keeps the accounting mark
        con.execute("INSERT INTO bc_variance_period(end_count_id, branch_id, receipts_confirmed_by,"
                    " receipts_confirmed_at) VALUES(?,?,?,?) ON CONFLICT(end_count_id) DO UPDATE SET"
                    " receipts_confirmed_by=excluded.receipts_confirmed_by,"
                    " receipts_confirmed_at=excluded.receipts_confirmed_at", (end_count_id, branch_id, who, now()))
    audit(con, who, "receipts_confirm" if not undo else "receipts_unconfirm", "bc_variance_period", str(end_count_id))


def overview(con) -> list:
    """The latest period of every branch that has one — the centre's list to check."""
    out = []
    for bid, name in con.execute("SELECT DISTINCT c.branch_id, b.name FROM bc_stock_count c JOIN branches b"
                                 " USING(branch_id) ORDER BY b.name").fetchall():
        try:
            s = period(con, bid, summary_only=True)
        except ValueError:
            s = {"branch_id": bid, "end_date": None}
        s["branch"] = name
        last = con.execute("SELECT MAX(count_date) FROM bc_stock_count WHERE branch_id=? AND"
                           " COALESCE(status,'submitted')='submitted'", (bid,)).fetchone()[0]
        s["last_count"] = last
        out.append(s)
    return out


# ── ใบสรุปตัดเบิก: the period's actual usage, for Accounting to post ─────────────
PRICE_SOURCE_TH = {"purchase_avg": "ซื้อจริง", "carry_forward": "ยกมา", "manual": "กำหนดเอง",
                   "list_ref": "ใบเสนอราคา", "snapshot": "สูตรเดิม"}


def _pair_for_end(con, branch_id: int, end_count_id):
    """The (begin, end) pair of this branch whose END count is `end_count_id`, else None."""
    from .stock import periods as _p
    try:
        eid = int(end_count_id)
    except (TypeError, ValueError):
        return None
    return next((p for p in _p(con, branch_id) if p[1]["count_id"] == eid), None)


def issue_summary(con, branch_id: int, end_count_id: int) -> dict:
    """The ใบสรุปตัดเบิก of one period: per item  used = begin + received + claims − end,
    valued at the purchase price of the month the period ends in."""
    from .prices import price_for
    pair = _pair_for_end(con, branch_id, end_count_id)
    if pair is None:
        raise ValueError("ไม่พบงวดนี้ของสาขา")
    b, e = pair
    d0, d1 = date.fromisoformat(b["date"]), date.fromisoformat(e["date"])
    month = _price_month(b["date"], e["date"])
    mv = _movements(con, branch_id, b, e)
    begin, end, received, claims = mv["begin"], mv["end"], mv["received"], mv["claims"]
    reasons = {c for (c,) in con.execute("SELECT ingredient_code FROM bc_variance_reason WHERE end_count_id=?",
                                         (e["count_id"],))}
    info = {c: (n, u, f) for c, n, u, f in con.execute(
        "SELECT code, name_th, base_unit, family FROM bc_ingredient")}
    end_basis = _count_basis(con, e["count_id"])                 # batch 4b (3.4): a partial end count says where each figure came from
    lines, excluded = [], []
    for c in sorted(set(begin) | set(end) | set(received) | set(claims)):
        r, cl = received.get(c, 0.0), claims.get(c, 0.0)
        n, u, fam = info.get(c) or (None, None, None)
        if c not in received and c not in claims and not begin.get(c) and not end.get(c):
            excluded.append({"code": c, "name": n})          # counted at zero and nothing arrived: nothing moved
            continue
        pr = price_for(con, c, month)
        unit_price = pr[0] if pr else None
        src = f"{PRICE_SOURCE_TH.get(pr[1], pr[1])} ({pr[2]})" if pr else None
        ln = {"code": c, "name": n, "base_unit": u, "family": fam,
              "begin": begin.get(c), "received": round(r, 3), "claims": round(cl, 3), "end": end.get(c),
              "unit_price": unit_price, "price_source": src, "has_reason": c in reasons,
              "used": None, "value": None, "status": "ok", "note": None,
              "end_basis": end_basis.get(c) or "count",
              "basis_th": SYSTEM_TH if end_basis.get(c) == "system" else ("นับเสริมรายสัปดาห์ล่าสุด" if end_basis.get(c) == "cycle" else "นับจริง")}
        if c not in begin or c not in end:
            miss = [t for t, d in (("ครั้งแรก", begin), ("สิ้นเดือน", end)) if c not in d]
            ln.update(status="not_counted", note="ไม่พบในผลนับ" + "และ".join(miss))
        else:
            used = begin[c] + r + cl - end[c]
            ln["used"] = round(used, 3)
            ln["value"] = round(used * unit_price, 2) if unit_price is not None else None
            if used < -1e-9:
                ln["status"] = "negative"
        lines.append(ln)
    lines.sort(key=lambda x: (x["family"] or "~", x["code"]))
    excluded.sort(key=lambda x: x["code"])

    def val(key):
        return round(sum(x[key] * x["unit_price"] for x in lines if x["unit_price"] is not None), 2)
    conf = con.execute("SELECT receipts_confirmed_by, receipts_confirmed_at, accounting_posted_at, accounting_posted_by"
                       " FROM bc_variance_period WHERE end_count_id=?", (e["count_id"],)).fetchone()
    try:
        reasons_done = period(con, branch_id, e["count_id"], summary_only=True)["needs_reason"] == 0
    except ValueError:
        reasons_done = False
    name = (con.execute("SELECT name FROM branches WHERE branch_id=?", (branch_id,)).fetchone() or [None])[0]
    from .issue_reports import slip_coverage             # a warning for Accounting, not a lock
    return {
        "branch": {"branch_id": branch_id, "name": name},
        "slips": slip_coverage(con, branch_id, d0, d1),
        "period": {"begin_date": b["date"], "end_date": e["date"], "begin_count_id": b["count_id"],
                   "end_count_id": e["count_id"], "month": month, "days": (d1 - d0).days, "partial": bool(end_basis),
                   "partial_th": (f"ปิดเดือนด้วยการนับเสริมรายสัปดาห์ (รายการเดิม ก่อนมีมติให้นับครบทุกรายการ) · กลุ่มมูลค่าสูง {sum(1 for v in end_basis.values() if v == 'cycle')} รายการใช้ผลนับเสริมรายสัปดาห์ล่าสุด · "
                                  f"อีก {sum(1 for v in end_basis.values() if v == 'system')} รายการเป็น{SYSTEM_TH} ใช้จริงจึงเท่ากับใบตัดเบิกที่ยืนยันแล้ว "
                                  "ส่วนต่างจากการนับจริงจะปรากฏเมื่อปิดเดือนถัดไปด้วยการนับของครบทุกรายการ") if end_basis else None},
        "status": {"receipts_confirmed": bool(conf), "receipts_confirmed_by": conf[0] if conf else None,
                   "receipts_confirmed_at": conf[1] if conf else None, "reasons_done": reasons_done,
                   "accounting_posted_at": conf[2] if conf else None,
                   "accounting_posted_by": conf[3] if conf else None, "receipts": mv["n_receipts"]},
        "lines": lines, "excluded": excluded,
        "totals": {"lines": len(lines), "received_value": val("received"), "claims_value": val("claims"),
                   "used_value": round(sum(x["value"] or 0.0 for x in lines), 2),
                   "unpriced": sum(1 for x in lines if x["unit_price"] is None),
                   "system_basis": sum(1 for x in lines if x["end_basis"] == "system"),
                   "not_counted": sum(1 for x in lines if x["status"] == "not_counted")},
    }


def branch_periods(con, branch_id: int) -> list:
    """THE period rule (QA 2026-10-13, H1): [(begin_count, end_count, month)] oldest first. A period is two consecutive submitted month counts
    and is priced in the month of the day BEFORE its end count - so an end count dated the last day of a month and one dated the 1st of the
    next both close that month. The ใบสรุปตัดเบิก periods and the หน้างาน close steps both read this one function."""
    from .stock import periods as _p
    return [(b, e, _price_month(b["date"], e["date"])) for b, e in _p(con, branch_id)]


def month_period(con, branch_id: int, month: str):
    """(begin_count, end_count) of the period priced in `month` (the newest if several), or None."""
    hit = [(b, e) for b, e, m in branch_periods(con, branch_id) if m == month]
    return hit[-1] if hit else None


def issue_periods(con, branch_id: int = None) -> list:
    """Every branch's begin/end periods (newest first) with their confirmation and posting state — no prices."""
    conf = {r[0]: r[1:] for r in con.execute(
        "SELECT end_count_id, receipts_confirmed_by, receipts_confirmed_at, accounting_posted_at, accounting_posted_by"
        " FROM bc_variance_period")}
    q = "SELECT DISTINCT c.branch_id, b.name FROM bc_stock_count c JOIN branches b USING(branch_id)"
    args = ()
    if branch_id is not None:
        q += " WHERE c.branch_id=?"
        args = (branch_id,)
    out = []
    for bid, name in con.execute(q + " ORDER BY b.name", args).fetchall():
        ps = []
        for b, e, month in reversed(branch_periods(con, bid)):
            cf = conf.get(e["count_id"])
            ps.append({"begin_count_id": b["count_id"], "end_count_id": e["count_id"], "begin_date": b["date"],
                       "end_date": e["date"], "month": month,
                       "receipts_confirmed": bool(cf), "receipts_confirmed_by": cf[0] if cf else None,
                       "accounting_posted_at": cf[2] if cf else None, "accounting_posted_by": cf[3] if cf else None})
        out.append({"branch_id": bid, "name": name, "periods": ps})
    return out


def set_posted(con, end_count_id: int, posted: bool, who: str, branch_id: int = None) -> dict:
    """Accounting: the ใบสรุปตัดเบิก of this period is (or is no longer) posted in the accounting system."""
    row = con.execute("SELECT branch_id FROM bc_stock_count WHERE count_id=?", (end_count_id,)).fetchone()
    if (not row or (branch_id is not None and int(row[0]) != int(branch_id))
            or _pair_for_end(con, row[0], end_count_id) is None):
        raise ValueError("ไม่พบงวดนี้ของสาขา")
    conf = con.execute("SELECT 1 FROM bc_variance_period WHERE end_count_id=?", (end_count_id,)).fetchone()
    if posted and not conf:
        raise ValueError("สาขาต้องแจ้งว่าบันทึกรับของครบของเดือนนี้ก่อนจึงบันทึกเข้าระบบบัญชีได้")
    at = now() if posted else None
    if conf:
        con.execute("UPDATE bc_variance_period SET accounting_posted_at=?, accounting_posted_by=? WHERE end_count_id=?",
                    (at, who if posted else None, end_count_id))
    audit(con, who, "issue_posted" if posted else "issue_unposted", "bc_variance_period", str(end_count_id))
    return {"end_count_id": int(end_count_id), "posted": bool(posted), "accounting_posted_at": at,
            "accounting_posted_by": who if posted else None}
