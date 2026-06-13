"""
Reconcile v2 — one composed structure per (branch, month), POS-channel-first.

This layer does NOT match anything itself: it composes the proven engine pieces
in matcher.py (category_reconciliation, daily_reconciliation, cash matcher,
credit 3-way, coverage) into the single shape the v2 UI renders, with the
status vocabulary collapsed to what staff actually act on:

  ok          — POS and money-in agree (within tolerance)
  diff        — both sides present, beyond tolerance → investigate
  waiting_doc — POS is there, the document isn't (missing upload / late days)
  no_source   — nothing to check against (coupon — reconciled with the issuer)
  setup       — the POS code's bank is unknown; answer once, inline

Everything is computed live. Nothing here writes; the only persisted snapshot
is taken by run-match at month close (ส่งตรวจ → ปิดเดือน on recon_sessions).
"""
from __future__ import annotations

import calendar
import sqlite3
from datetime import date

from .matcher import (
    category_reconciliation,
    coupon_reconciliation,
    credit_three_way,
    daily_reconciliation,
    map_bank_suggestions,
    payout_matching,
    point_pos_total,
    point_settlement_total,
    suggest_internal_transfers,
    suggest_reclassifications,
    _coverage,
    _lane_cov_key,
    _REAL_BANKS,
    _BANK_LABEL,
)

# Channel order + Thai labels (the UI renders these verbatim)
CHANNELS = [
    ("QR",       "QR พร้อมเพย์"),
    ("CASH",     "เงินสด"),
    ("TRANSFER", "เงินโอน"),
    ("CREDIT",   "บัตรเครดิต"),
    ("GRAB",     "Grab"),
    ("LINEMAN",  "Line Man"),
    ("COUPON",   "คูปอง"),
]
_CH_LABEL = dict(CHANNELS)

# Day-lane statuses (from daily_reconciliation) that mean "done"
_DAY_DONE = {"matched", "manual"}
# ... and the ones that mean "a problem to investigate" (pending = late doc, not a problem)
_DAY_ISSUE = {"variance", "missing", "ext_only"}


def _doc_checklist(conn, branch_id, period_month, monthly_rows, cov):
    """Required documents derived from the POS channels this branch actually has;
    status from what's been uploaded (coverage = max line date per source)."""
    pos_banks = {r["bank"] for r in monthly_rows
                 if r["category"] in ("QR", "TRANSFER") and r["pos_amount"] > 0
                 and r["bank"] in _REAL_BANKS}
    has_cash = any(r["category"] == "CASH" and r["pos_amount"] > 0 for r in monthly_rows)
    if has_cash:                       # cash deposits only show on a bank statement
        stmt_banks = {b for (src, b) in cov if src == "bank"} & _REAL_BANKS
        pos_banks |= stmt_banks or _REAL_BANKS
    cc_acqs = {r["bank"] for r in monthly_rows
               if r["category"] == "CREDIT" and r["pos_amount"] > 0}
    try:    # pos_payment_day is sync-built; absent until the first MSSQL sync
        last_pos_day = conn.execute(
            "SELECT MAX(day) FROM pos_payment_day WHERE branch_id=? AND strftime('%Y-%m', day)=?",
            (branch_id, period_month)).fetchone()[0]
    except sqlite3.OperationalError:
        last_pos_day = None

    docs = []

    # C8: compare coverage against POS's latest day; when POS isn't synced
    # (last_pos_day absent) fall back to the calendar end of month (capped at
    # today for an in-progress month) so a half-uploaded statement reads
    # 'partial' instead of silently 'uploaded'.
    y, mo = int(period_month[:4]), int(period_month[5:7])
    eom = f"{period_month}-{calendar.monthrange(y, mo)[1]:02d}"
    cmp_day = last_pos_day or min(eom, date.today().isoformat())

    def add(key, label, cov_key):
        mx = cov.get(cov_key)
        if not mx:
            status, detail = "missing", "ยังไม่ได้อัปโหลด"
        elif mx < cmp_day:
            status, detail = "partial", f"มีถึงวันที่ {mx[-2:]}"
        else:
            status, detail = "uploaded", ""
        docs.append({"key": key, "label": label, "status": status,
                     "detail": detail, "coverage_to": mx})       # C8

    for b in sorted(pos_banks):
        add(f"stmt_{b}", f"สเตทเมนต์ {b}", ("bank", b))
    for a in sorted(cc_acqs):
        add(f"cc_{a}", f"สรุปยอดบัตร {a}", ("cc", a))
    # Multi-bank transfer codes (ANY) can land in either bank → need both statements
    if any(r["category"] == "TRANSFER" and r["bank"] == "ANY" and r["pos_amount"] > 0
           for r in monthly_rows):
        for b in sorted(_REAL_BANKS - pos_banks):
            add(f"stmt_{b}", f"สเตทเมนต์ {b}", ("bank", b))
            pos_banks.add(b)
    for r in monthly_rows:
        if r["category"] == "GRAB" and r["pos_amount"] > 0:
            add("grab", "รายงาน Grab", ("grab", None))
        if r["category"] == "LINEMAN" and r["pos_amount"] > 0:
            add("lineman", "รายงาน Line Man", ("lineman", None))
    # Loyalty-point summaries (Coupon channel): required when POS shows the
    # issuer's point coupons; "uploaded" = the point settlement rows are present.
    for issuer, pacq in (("KTC", "KTCPOINT"), ("BAY", "BAYPOINT")):
        if point_pos_total(conn, branch_id, period_month, issuer) > 0:
            have = point_settlement_total(conn, branch_id, period_month, pacq) > 0
            docs.append({"key": f"points_{issuer}", "label": f"สรุปคะแนน {issuer}",
                         "status": "uploaded" if have else "missing",
                         "detail": "" if have else "ยังไม่ได้อัปโหลด"})
    # Upload metadata so the v2 UI can open the right uploader from the chip:
    # source_type + provider hint, and which channel an "accept" would clear
    # (a whole missing statement is never acceptable — nothing reconciles without it)
    # C2: an inline hint naming the file to upload, so staff don't grab a fee
    # invoice (which parses to nothing) instead of the settlement/sales report.
    for d in docs:
        k = d["key"]
        if k.startswith("stmt_"):
            d.update(source="bank", provider=k[5:], channel=None,
                     hint="สเตทเมนต์ธนาคาร (ไฟล์ Excel/CSV ดีที่สุด; PDF รองรับ KBANK/KTB)")
        elif k.startswith("cc_"):
            d.update(source="credit_card", provider=k[3:], channel="CREDIT",
                     hint="สรุป settlement บัตร — KTC ใช้ไฟล์ *-RTxInv-* (ไม่ใช่ TIV_MDR); "
                          "Krungsri ใช้ RDB003")
        elif k.startswith("points_"):
            # KTC/BAY point summaries upload through the credit_card parser
            # (they parse as KTCPOINT/BAYPOINT settlement rows)
            d.update(source="credit_card", provider=k[7:] + "POINT", channel="COUPON",
                     hint="ไฟล์สรุปคะแนนจากผู้ออกบัตร (KTC / BAY)")
        elif k == "grab":
            d.update(source="grab", provider="GRAB", channel="GRAB",
                     hint="รายงานยอดขาย Grab (ไฟล์ขึ้นต้น 3-CZ... ไม่ใช่ IM)")
        else:
            d.update(source="lineman", provider="LINEMAN", channel="LINEMAN",
                     hint="รายงานยอดขาย Line Man (REPORT_*.csv/pdf ไม่ใช่ Balance-Report)")
    return docs


def _pos_codes_by_channel(conn, branch_id, period_month):
    """Which POS payment codes make up each channel's total — so staff can see
    where a number comes from (e.g. QR = KNNQR 3.1M + K-SHOP 0.5M) instead of
    having to trust an opaque sum."""
    from .seed_payment_map import normalize
    out: dict = {}
    try:
        pmap = {r["payment_code_norm"]: r for r in conn.execute(
            "SELECT payment_code_norm, category, bank, reconcile FROM recon_payment_map")}
        rows = conn.execute(
            "SELECT payment_code, amount FROM pos_payment_month "
            "WHERE branch_id=? AND period_month=?", (branch_id, period_month)).fetchall()
    except sqlite3.OperationalError:
        return out
    agg: dict = {}
    for r in rows:
        m = pmap.get(normalize(r["payment_code"]))
        if m is None or not m["reconcile"]:
            continue
        k = (m["category"], normalize(r["payment_code"]), m["bank"])
        agg[k] = agg.get(k, 0.0) + float(r["amount"] or 0)
    for (cat, code, bank), amt in agg.items():
        out.setdefault(cat, []).append({"code": code, "bank": bank, "amount": round(amt, 2)})
    for cat in out:
        out[cat].sort(key=lambda x: -x["amount"])
    return out


def _accepted_channels(conn, branch_id, period_month):
    """Staff-acknowledged variances: lane_adj rows with adj_type='accept'
    (one per channel, bank='ALL', zero amounts — a pure marker with a reason).
    They clear the channel's todo so the month can actually close."""
    try:
        return {r["category"]: {"id": r["id"], "note": r["note"],
                                "by": r["created_by"], "at": r["created_at"]}
                for r in conn.execute(
                    "SELECT * FROM recon_lane_adj WHERE branch_id=? AND period_month=? "
                    "AND adj_type='accept'", (branch_id, period_month))}
    except sqlite3.OperationalError:
        return {}


def _non_revenue(conn, branch_id, period_month):
    """Operator-flagged internal transfers (excluded bank credits) this month —
    reported apart so 'statement > POS' never hides inside the revenue lanes."""
    lines = [dict(r) for r in conn.execute("""
        SELECT l.id, l.txn_date AS date, l.description AS ref, l.credit AS amount
        FROM recon_bank_lines l
        WHERE l.branch_id=? AND l.credit>0 AND l.match_status='excluded'
          AND strftime('%Y-%m', l.txn_date)=?
        ORDER BY l.txn_date
    """, (branch_id, period_month))]
    return {"count": len(lines),
            "amount": round(sum(l["amount"] for l in lines), 2),
            "lines": lines}


def reconcile_month(conn: sqlite3.Connection, branch_id: int, period_month: str) -> dict:
    """The whole month for one branch, channel-first: month → channel → days."""
    monthly_rows, unmapped = category_reconciliation(conn, branch_id, period_month)
    daily = daily_reconciliation(conn, branch_id, period_month)
    cov = _coverage(conn, branch_id, period_month)
    c3 = credit_three_way(conn, branch_id, period_month)
    accepted = _accepted_channels(conn, branch_id, period_month)
    pos_codes = _pos_codes_by_channel(conn, branch_id, period_month)
    payouts = payout_matching(conn, branch_id, period_month)     # GRAB/LM stage-2 trace
    issuer_points = coupon_reconciliation(conn, branch_id, period_month)

    # Collapse day-lanes by channel (sum over banks within a day)
    ch_days: dict = {}     # channel -> {day: {pos, ext, statuses:set, banks:set}}
    for d in daily["days"]:
        for lane in d["lanes"]:
            ch = ch_days.setdefault(lane["category"], {})
            cd = ch.setdefault(d["day"], {"pos": 0.0, "ext": 0.0, "statuses": set(), "banks": set()})
            cd["pos"] += lane["pos"]
            cd["ext"] += lane["ext"]
            cd["statuses"].add(lane["status"])
            cd["banks"].add(lane["bank"])

    # Monthly rollup per channel (sum over banks)
    ch_month: dict = {}
    for r in monthly_rows:
        m = ch_month.setdefault(r["category"], {"pos": 0.0, "ext": 0.0, "net": 0.0, "banks": []})
        m["pos"] += r["pos_amount"]
        m["ext"] += r["ext_amount"]
        m["net"] += r["ext_net"]
        m["banks"].append({"bank": r["bank"], "pos": r["pos_amount"],
                           "ext": r["ext_amount"], "variance": r["variance"],
                           "status": r["status"]})

    channels = []
    for key, label in CHANNELS:
        m = ch_month.get(key)
        days_map = ch_days.get(key, {})
        if not m and not days_map:
            continue                                  # channel absent at this branch
        pos_amt = round(m["pos"], 2) if m else 0.0
        ext_amt = round(m["ext"], 2) if m else 0.0

        # Per-day card: one status per day (worst lane wins)
        days = []
        n_done = n_pending = n_issue = n_setup = 0
        for day in sorted(days_map):
            cd = days_map[day]
            sts = cd["statuses"]
            if "unmapped_bank" in sts:
                dst = "setup"; n_setup += 1
            elif sts & _DAY_ISSUE:
                dst = "diff"; n_issue += 1
            elif "pending" in sts:
                dst = "waiting"; n_pending += 1
            elif sts & _DAY_DONE:
                dst = "ok"; n_done += 1
            else:                                     # no_source only (coupon)
                dst = "no_source"
            days.append({"day": day, "pos": round(cd["pos"], 2),
                         "ext": round(cd["ext"], 2),
                         "variance": round(cd["ext"] - cd["pos"], 2), "status": dst,
                         "banks": sorted(cd["banks"])})

        # Channel verdict + a one-line Thai explanation staff can act on
        doc_missing = []
        if key == "CREDIT":
            doc_missing = [r["acquirer"] for r in c3["rows"]
                           if r["pos_gross"] > 0 and r["settle_gross"] == 0]
        elif key in ("GRAB", "LINEMAN") and pos_amt > 0 and not cov.get(_lane_cov_key(key, None)):
            doc_missing = [key]

        if key == "COUPON":
            # Issuer-points method (owner's table): KTC/BAY point coupons check
            # against the issuer's point summary. Other coupon series still have
            # no external source → channel stays no_source when no points exist.
            if not issuer_points:
                status, detail = "no_source", "ตรวจกับผู้ออกคูปอง"
            elif any(r["status"] == "mismatch" for r in issuer_points):
                status, detail = "diff", "ยอดแต้มไม่ตรงกับใบสรุปคะแนน"
            elif any(r["status"] == "waiting_doc" for r in issuer_points):
                miss = [r["issuer"] for r in issuer_points if r["status"] == "waiting_doc"]
                status, detail = "waiting_doc", "อัปโหลดสรุปคะแนน " + ", ".join(miss)
            elif any(r["status"] == "awaiting_payout" for r in issuer_points):
                status, detail = "waiting_doc", "รอเงินแต้มเข้าบัญชี"
            else:
                ok_iss = "/".join(r["issuer"] for r in issuer_points)
                status, detail = "ok", f"แต้ม {ok_iss} ตรงตามใบสรุป"
        elif n_setup:
            codes = ", ".join(u["code"] for u in
                              [{"code": k} for k in sorted(set(
                                  b for d in days_map.values() for b in d["banks"]
                                  if b not in _REAL_BANKS | {"NONE", "KTC"}))])
            status, detail = "setup", f"ระบุธนาคาร ({codes})" if codes else "ระบุธนาคารของรหัสชำระ"
        elif doc_missing:
            status = "waiting_doc"
            detail = ("ขาดสรุปยอดบัตร " + ", ".join(doc_missing)) if key == "CREDIT" \
                     else f"ขาดรายงาน {label}"
        elif n_issue:
            status = "diff"
            detail = ("เงินเข้าโดยไม่มียอดขาย POS" if pos_amt == 0
                      else f"ต่างกัน {n_issue} วัน")
        elif n_pending:
            status, detail = "waiting_doc", f"รอเอกสาร {n_pending} วัน"
        else:
            status, detail = "ok", ""
        if status == "ok" and n_done:
            detail = f"{n_done}/{len(days)} วัน"

        ch = {"key": key, "label": label, "pos": pos_amt, "ext": ext_amt,
              "variance": round(ext_amt - pos_amt, 2), "status": status,
              "detail": detail, "days": days,
              "days_done": n_done, "days_total": len(days),
              "banks": m["banks"] if m else [],
              "codes": pos_codes.get(key, []),
              "accepted": accepted.get(key)}
        if key == "CREDIT":
            ch["three_way"] = c3["rows"]
        if key in ("GRAB", "LINEMAN"):
            # Stage 2 of the owner's two-stage method: provider NET ↔ bank credit,
            # with combined-day groups + lag — rendered like CREDIT's three_way.
            ch["payout_trace"] = payouts.get(key)
        if key == "COUPON":
            ch["issuer_points"] = issuer_points
            if issuer_points:
                # the channel's headline variance = the points variance (the
                # non-point coupon remainder has no external source to differ from)
                ch["variance"] = round(sum(r["variance"] for r in issuer_points), 2)
        if key == "CASH":
            # 3-way rows: POS cash ↔ typed deposit slip ↔ bank deposit group
            ch["cash_three_way"] = daily["cash_summary"].get("three_way", [])
        if key == "TRANSFER":
            # C4: per-branch treatment without a config table. A branch that rings
            # a POS transfer code (pos_amt>0, e.g. CNX) should MATCH transfers; a
            # branch with no transfer code books them elsewhere, so its bank
            # transfers are EXCLUDE-mode (auto/interbank, or customer transfer
            # rung as cash). Lets the UI guide "จับคู่" vs "ตัดออก" automatically.
            ch["transfer_mode"] = "match" if pos_amt > 0 else "exclude"
        channels.append(ch)

    # Progress is money-weighted, not day-counted: 27 days of ฿1k transfer noise
    # must not drag the gauge as hard as ฿3.6M of matched QR. unexplained =
    # |channel variance| summed over checkable channels; base = larger side.
    # COUPON stays out of the money base in all statuses: points are discounts,
    # not bank money-in (the reimbursement rides the CREDIT/TRANSFER lanes), so
    # counting it would wrongly drag the gauge once the points doc arrives.
    unexplained = sum(abs(ch["variance"]) for ch in channels
                      if ch["status"] != "no_source" and ch["key"] != "COUPON")
    base = sum(max(ch["pos"], ch["ext"]) for ch in channels
               if ch["status"] != "no_source" and ch["key"] != "COUPON")
    progress = round(max(0.0, 100.0 * (1 - unexplained / base)), 1) if base else 100.0

    docs = _doc_checklist(conn, branch_id, period_month, monthly_rows, cov)

    # Inline setup questions: unmapped codes + document-derived suggestions
    try:
        sugg = {s["code"]: s for s in
                map_bank_suggestions(conn, branch_id, period_month)}
    except Exception:
        sugg = {}
    setup_q = [{"code": u["code"], "amount": u["amount"],
                "suggestion": ({"category": sugg[u["code"]]["category"],
                                "bank": sugg[u["code"]]["bank"],
                                "hits": sugg[u["code"]]["hits"],
                                "map_id": sugg[u["code"]]["map_id"]}
                               if u["code"] in sugg else None)}
               for u in daily["unmapped"]]

    # To-do list = exactly what blocks the month from closing. A channel-level
    # "accept" (acknowledged variance with a reason) clears its items — that's
    # the escape hatch that lets a month close over a ฿396 missing report.
    todo = []
    for doc in docs:
        if doc["status"] == "missing" and not (doc["channel"] and doc["channel"] in accepted):
            todo.append({"type": "upload", "label": f"อัปโหลด{doc['label']}",
                         "doc": doc["key"], "channel": doc["channel"]})
    for q in setup_q:
        todo.append({"type": "setup", "label": f"ระบุธนาคารของ {q['code']}", "code": q["code"]})
    for ch in channels:
        if ch["accepted"]:
            continue
        if ch["status"] == "diff":
            todo.append({"type": "diff", "channel": ch["key"],
                         "label": f"ตรวจผลต่าง{ch['label']} {ch['variance']:+,.0f} บาท"})
        elif ch["status"] == "waiting_doc" and not any(
                t["type"] == "upload" for t in todo):
            todo.append({"type": "waiting", "channel": ch["key"],
                         "label": f"{ch['label']}: {ch['detail']}"})

    session = conn.execute(
        "SELECT session_id, status, approved_at, approved_by "
        "FROM recon_sessions WHERE branch_id=? AND period_month=?",
        (branch_id, period_month)).fetchone()
    branch = conn.execute(
        "SELECT name FROM branches WHERE branch_id=?", (branch_id,)).fetchone()

    # Wrong-channel suggestions (same bank, equal amount, ±1 day) — each is a
    # ready-to-POST /api/manual payload the UI applies in one click
    try:
        reclass = suggest_reclassifications(conn, branch_id, period_month)
    except sqlite3.OperationalError:
        reclass = []
    # Phase 0: enrich each suggestion with plain-Thai channel + bank labels so the
    # UI card reads "เงินสด → เงินโอน (กรุงไทย)" instead of the raw "CASH·KBANK".
    for s in reclass:
        s["pos_cat_th"]  = _CH_LABEL.get(s.get("pos_cat"),  s.get("pos_cat"))
        s["ext_cat_th"]  = _CH_LABEL.get(s.get("ext_cat"),  s.get("ext_cat"))
        s["pos_bank_th"] = _BANK_LABEL.get(s.get("pos_bank"), s.get("pos_bank"))
        s["ext_bank_th"] = _BANK_LABEL.get(s.get("ext_bank"), s.get("ext_bank"))

    # Large round non-sales credits that look like own-account sweeps — surfaced so
    # staff can drop them from the lanes in one click (PATCH /api/bank-lines/id/ignore)
    try:
        internal_xfers = suggest_internal_transfers(conn, branch_id, period_month)
    except sqlite3.OperationalError:
        internal_xfers = []

    return {
        "branch_id": branch_id,
        "branch_name": branch["name"] if branch else str(branch_id),
        "period_month": period_month,
        "session": dict(session) if session else None,
        "documents": docs,
        "channels": channels,
        "non_revenue": _non_revenue(conn, branch_id, period_month),
        "setup_questions": setup_q,
        "reclass_suggestions": reclass,
        "internal_transfer_suggestions": internal_xfers,
        "todo": todo,
        "progress_pct": progress,
        "can_close": not todo,
        "pos_daily_available": daily["pos_daily_available"],
    }


def reconcile_overview(conn: sqlite3.Connection, period_month: str) -> dict:
    """All master branches for one month — the board: per-branch progress +
    per-channel variance, every cell drillable into reconcile_month."""
    branches = conn.execute(
        "SELECT branch_id, name FROM branches "
        "WHERE branch_type='master' AND active=1 ORDER BY name").fetchall()
    rows = []
    for b in branches:
        r = reconcile_month(conn, b["branch_id"], period_month)
        rows.append({
            "branch_id": b["branch_id"],
            "branch_name": b["name"],
            "progress_pct": r["progress_pct"],
            "todo_count": len(r["todo"]),
            "status": (r["session"] or {}).get("status", "draft"),
            "pos_total": round(sum(c["pos"] for c in r["channels"]), 2),
            "ext_total": round(sum(c["ext"] for c in r["channels"]), 2),
            "non_revenue": r["non_revenue"]["amount"],
            "channels": {c["key"]: {"pos": c["pos"], "ext": c["ext"],
                                    "variance": c["variance"], "status": c["status"]}
                         for c in r["channels"]},
        })
    return {"period_month": period_month, "channel_order": [k for k, _ in CHANNELS],
            "channel_labels": _CH_LABEL, "branches": rows}
