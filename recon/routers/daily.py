"""The daily board, daily and monthly reports, daily accounts and dashboard.

Moved out of recon/main.py in Phase 7c (2026-09) by the AST: these endpoints and
the helpers only they use, unchanged. URLs are unchanged.
"""
from fastapi import APIRouter
from ..core import RECON_START_DAY
from ..core import _BANK_CH_TO_CAT
from ..core import _DAY_RE
from ..core import _FEE_CH_SPEC
from ..core import _FINANCE_CH_ORDER
from ..core import _MASTER_ONLY
from ..core import _MATCH_CH_TH
from ..core import _PERIOD_RE
from ..core import _POS_DAY_UNAVAILABLE
from ..core import _account_map
from ..core import _daily_finance_data
from ..core import _has_table
from ..core import _m
from ..core import _validate_master_branch
from ..core import get_db
from ..matcher import _BANK_LABEL
from ..matcher import _eff_fee_vat
from ..matcher import coupon_discount_total_daily
from ..matcher import daily_board
from ..matcher import daily_reconciliation
from ..matcher import point_pos_total_daily
from ..matcher import pos_codes_scope
from ..reconcile_v2 import reconcile_month
from datetime import datetime as _dt
from fastapi import HTTPException
from fastapi.responses import Response
import json
import sqlite3

router = APIRouter()


@router.get("/api/daily-board/dates")
def daily_board_dates():
    """Min / max day with POS daily data — drives the date picker bounds + default.

    min_day is floored to RECON_START_DAY so the board never surfaces pre-cutoff
    data (the historical rows stay in the DB for the dashboard)."""
    with get_db() as conn:
        try:
            row = conn.execute(
                "SELECT MIN(day) mn, MAX(day) mx FROM pos_payment_day WHERE day >= ?",
                (RECON_START_DAY,)).fetchone()
            return {"min_day": row["mn"] or RECON_START_DAY, "max_day": row["mx"]}
        except sqlite3.OperationalError:
            return {"min_day": None, "max_day": None}


@router.get("/api/daily-board")
def daily_board_endpoint(day: str):
    if not _DAY_RE.match(day or ""):
        raise HTTPException(400, "day must be in 'YYYY-MM-DD' format.")
    with get_db() as conn:
        if not _has_table(conn, "pos_payment_day"):
            return {"day": day, "available": False, "reason": _POS_DAY_UNAVAILABLE,
                    "categories": [], "branches": [], "totals": {}, "unmapped": []}
        return daily_board(conn, day)


# ── Daily reconciliation report (print-to-PDF evidence) ───────────────────────
_STATUS_TH = {"matched": "ตรงกับเอกสาร", "variance": "มีส่วนต่าง", "pos_only": "รอเอกสาร",
              "ext_only": "เกินจากเอกสาร", "no_source": "ไม่มีแหล่งอ้างอิง", "manual": "กระทบมือ"}


def _daily_report_html(conn, day: str, branch_id: int | None) -> str:
    board = daily_board(conn, day)
    cmeta = board.get("column_meta", {})
    rows = board["branches"]
    if branch_id:
        rows = [r for r in rows if r["branch_id"] == branch_id]
    bf = " AND branch_id=?" if branch_id else ""
    p = [day, branch_id] if branch_id else [day]

    def grouped(sql, params):
        out = {}
        for r in conn.execute(sql, params):
            out.setdefault(r["branch_id"], []).append(r)
        return out
    cc   = grouped(f"SELECT * FROM recon_cc_lines WHERE settlement_date=?{bf}", p)
    grab = grouped(f"SELECT * FROM recon_grab_lines WHERE order_date=? AND order_status='Completed'{bf}", p)
    lnmn = grouped(f"SELECT * FROM recon_lineman_lines WHERE order_date=? AND order_status='Completed'{bf}", p)
    bank = grouped(f"SELECT * FROM recon_bank_lines WHERE txn_date=? AND credit>0{bf}", p)
    # manual matches for the month, keep those that touch this day
    mm = {}
    mp = [day[:7], branch_id] if branch_id else [day[:7]]
    for r in conn.execute(f"SELECT * FROM recon_manual_match WHERE period_month=?{bf}", mp):
        try:
            days = json.loads(r["pos_days"] or "[]")
        except Exception:
            days = []
        amt = sum(float(x.get("amount") or 0) for x in days if x.get("day") == day)
        if amt:
            mm.setdefault(r["branch_id"], []).append((r, amt))

    css = """<style>
      @page{size:A4;margin:14mm}
      *{box-sizing:border-box} body{font-family:'Sarabun','TH Sarabun New',system-ui,sans-serif;color:#1e293b;font-size:12px;margin:0;padding:24px;background:#f8fafc}
      .sheet{background:#fff;max-width:900px;margin:0 auto 18px;padding:26px 30px;border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
      h1{font-size:19px;margin:0 0 2px} h2{font-size:15px;margin:22px 0 8px;border-left:4px solid #ea580c;padding-left:8px}
      h3{font-size:13px;margin:14px 0 6px;color:#475569}
      .muted{color:#64748b;font-size:11px} .right{text-align:right} .mono{font-variant-numeric:tabular-nums;font-family:ui-monospace,monospace}
      table{width:100%;border-collapse:collapse;margin:6px 0 10px;font-size:11.5px}
      th,td{padding:5px 7px;border-bottom:1px solid #eef2f7;text-align:left}
      th{color:#64748b;font-size:10px;text-transform:uppercase;border-bottom:1.5px solid #e2e8f0}
      tfoot td{border-top:1.5px solid #cbd5e1;font-weight:700}
      .kpis{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}
      .kpi{flex:1;min-width:120px;border:1px solid #e5e7eb;border-radius:8px;padding:8px 10px}
      .kpi .l{font-size:10px;color:#64748b} .kpi .v{font-size:17px;font-weight:700}
      .pos{color:#059669} .neg{color:#dc2626} .badge{padding:1px 7px;border-radius:99px;font-size:10px;font-weight:600}
      .b-ok{background:#dcfce7;color:#166534}.b-warn{background:#fef9c3;color:#854d0e}.b-err{background:#fee2e2;color:#b91c1c}.b-mut{background:#f1f5f9;color:#475569}
      .toolbar{max-width:900px;margin:0 auto 14px;text-align:right}
      .btn{background:#ea580c;color:#fff;border:0;border-radius:8px;padding:9px 18px;font-size:13px;cursor:pointer}
      .note{background:#fff7ed;border-left:3px solid #f59e0b;padding:6px 10px;border-radius:4px;margin:4px 0}
      .sign{display:flex;gap:40px;margin-top:34px} .sign div{flex:1;border-top:1px solid #94a3b8;padding-top:5px;text-align:center;font-size:11px;color:#475569}
      @media print{body{background:#fff;padding:0}.toolbar{display:none}.sheet{box-shadow:none;margin:0;max-width:none;border-radius:0;page-break-after:always}}
    </style>"""

    def status_badge(s):
        cls = {"matched": "b-ok", "manual": "b-ok", "variance": "b-err", "ext_only": "b-warn",
               "pos_only": "b-warn", "no_source": "b-mut"}.get(s, "b-mut")
        return f'<span class="badge {cls}">{_STATUS_TH.get(s, s)}</span>'

    scope = "ทุกสาขา" if not branch_id else (rows[0]["branch_name"] if rows else "—")
    parts = ["<!doctype html><html lang='th'><head><meta charset='utf-8'>",
             "<link href='https://fonts.googleapis.com/css2?family=Sarabun:wght@400;600;700&display=swap' rel='stylesheet'>",
             css, "</head><body>",
             "<div class='toolbar'><button class='btn' onclick='window.print()'>🖨️ พิมพ์ / บันทึกเป็น PDF</button></div>"]

    if not rows:
        parts.append(f"<div class='sheet'><h1>รายงานกระทบยอดรายวัน</h1><p class='muted'>{day} · {scope}</p>"
                     "<p>ไม่มีข้อมูลการขายสำหรับวันนี้</p></div>")
        parts.append("</body></html>")
        return "".join(parts)

    for b in rows:
        bid = b["branch_id"]
        parts.append("<div class='sheet'>")
        parts.append(f"<h1>รายงานกระทบยอดรายวัน — หลักฐานการกระทบยอด</h1>"
                     f"<p class='muted'>สาขา <b>{b['branch_name']}</b> · วันที่ <b>{day}</b> · "
                     f"ออกรายงานเมื่อ {_dt.now().strftime('%Y-%m-%d %H:%M')}</p>")
        var = b["variance"]; vc = "pos" if abs(var) < 1 else "neg"
        parts.append("<div class='kpis'>"
                     f"<div class='kpi'><div class='l'>ยอด POS (ที่กระทบได้)</div><div class='v mono'>{_m(b['pos_total'])}</div></div>"
                     f"<div class='kpi'><div class='l'>ยอดจากเอกสาร</div><div class='v mono'>{_m(b['ext_total'])}</div></div>"
                     f"<div class='kpi'><div class='l'>ส่วนต่าง</div><div class='v mono {vc}'>{_m(var)}</div></div>"
                     f"<div class='kpi'><div class='l'>รายการมีปัญหา</div><div class='v'>{b['issues']}</div></div>"
                     "</div>")

        # 1) Category reconciliation (POS vs documents)
        parts.append("<h2>① กระทบยอดตามช่องทาง (POS เทียบเอกสาร)</h2>"
                     "<table><thead><tr><th>ช่องทาง</th><th class='right'>POS</th><th class='right'>เอกสาร</th>"
                     "<th class='right'>กระทบมือ</th><th class='right'>ส่วนต่าง</th><th>สถานะ</th></tr></thead><tbody>")
        for ck, cell in b["cells"].items():
            lbl = (cmeta.get(ck) or {}).get("label", ck)
            v = cell["variance"]
            parts.append(f"<tr><td>{lbl}</td><td class='right mono'>{_m(cell['pos'])}</td>"
                         f"<td class='right mono'>{_m(cell['ext'])}</td><td class='right mono'>{_m(cell.get('manual'))}</td>"
                         f"<td class='right mono {'pos' if abs(v)<1 else 'neg'}'>{_m(v)}</td><td>{status_badge(cell['status'])}</td></tr>")
        parts.append(f"<tr><td colspan='6' class='muted' style='border:0'></td></tr></tbody><tfoot><tr><td>รวม</td>"
                     f"<td class='right mono'>{_m(b['pos_total'])}</td><td class='right mono'>{_m(b['ext_total'])}</td>"
                     f"<td></td><td class='right mono {vc}'>{_m(var)}</td><td></td></tr></tfoot></table>")

        # 2) Fees & VAT — line level
        cc_l, gr_l, ln_l = cc.get(bid, []), grab.get(bid, []), lnmn.get(bid, [])
        tot = {"gross": 0.0, "fee": 0.0, "vat": 0.0, "promo": 0.0, "refund": 0.0, "net": 0.0}
        feerows = []
        for r in cc_l:
            fee, vat = _eff_fee_vat(r, "mdr_fee", "vat_amount")
            gross = float(r["gross_amount"] or 0); refund = float(r["refund_amount"] or 0)
            net = gross - refund - fee - vat
            tot["gross"] += gross; tot["fee"] += fee; tot["vat"] += vat; tot["refund"] += refund; tot["net"] += net
            feerows.append(("CREDIT " + (r["acquirer"] or ""), gross, refund, fee, vat, 0.0, net,
                            "MDR" + (" (แก้มือ)" if r["fee_manual"] is not None else "")))
        for r in gr_l:
            fee, vat = _eff_fee_vat(r, "commission_amt", "vat_amount")
            gross = float(r["food_amount"] or 0); promo = float(r["promo_deduct"] or 0)
            net = gross - fee - vat - promo
            tot["gross"] += gross; tot["fee"] += fee; tot["vat"] += vat; tot["promo"] += promo; tot["net"] += net
            feerows.append(("GRAB", gross, 0.0, fee, vat, promo, net, "ค่าคอมมิชชั่น"))
        for r in ln_l:
            fee, vat = _eff_fee_vat(r, "commission_amt", "vat_on_commission")
            gross = float(r["food_amount"] or 0); promo = float(r["promo_deduct"] or 0)
            net = gross - fee - vat - promo
            tot["gross"] += gross; tot["fee"] += fee; tot["vat"] += vat; tot["promo"] += promo; tot["net"] += net
            feerows.append(("LINEMAN", gross, 0.0, fee, vat, promo, net, "ค่าคอมมิชชั่น + VAT"))
        if feerows:
            parts.append("<h2>② ค่าธรรมเนียม &amp; VAT (รายบรรทัดจากเอกสาร)</h2>"
                         "<table><thead><tr><th>ช่องทาง</th><th class='right'>ยอดเต็ม</th><th class='right'>คืนเงิน</th>"
                         "<th class='right'>ค่าธรรมเนียม</th><th class='right'>VAT</th><th class='right'>โปรโมชัน</th>"
                         "<th class='right'>สุทธิ</th><th>หมายเหตุ</th></tr></thead><tbody>")
            for nm, g, rf, fe, va, pr, nt, note in feerows:
                parts.append(f"<tr><td>{nm}</td><td class='right mono'>{_m(g)}</td><td class='right mono'>{_m(rf)}</td>"
                             f"<td class='right mono neg'>{_m(fe)}</td><td class='right mono neg'>{_m(va)}</td>"
                             f"<td class='right mono neg'>{_m(pr)}</td><td class='right mono'>{_m(nt)}</td><td class='muted'>{note}</td></tr>")
            parts.append(f"</tbody><tfoot><tr><td>รวม</td><td class='right mono'>{_m(tot['gross'])}</td>"
                         f"<td class='right mono'>{_m(tot['refund'])}</td><td class='right mono neg'>{_m(tot['fee'])}</td>"
                         f"<td class='right mono neg'>{_m(tot['vat'])}</td><td class='right mono neg'>{_m(tot['promo'])}</td>"
                         f"<td class='right mono'>{_m(tot['net'])}</td><td></td></tr></tfoot></table>"
                         f"<p class='muted'>สุทธิ = ยอดเต็ม − คืนเงิน − ค่าธรรมเนียม − VAT − โปรโมชัน · "
                         f"ค่าธรรมเนียมรวม {_m(tot['fee'])} · VAT รวม {_m(tot['vat'])}</p>")

        # 3) Manual matches — wrong-category reclassification with reason
        ms = mm.get(bid, [])
        if ms:
            parts.append("<h2>③ การกระทบมือ (สาขาบันทึกผิดประเภท / จับคู่เอง)</h2>")
            parts.append("<table><thead><tr><th>ช่องทาง</th><th>ธนาคาร</th><th class='right'>ยอดที่กระทบ (วันนี้)</th>"
                         "<th>เหตุผล / หมายเหตุ</th><th>โดย</th></tr></thead><tbody>")
            for r, amt in ms:
                parts.append(f"<tr><td>{r['category']}</td><td>{r['bank'] or '—'}</td>"
                             f"<td class='right mono'>{_m(amt)}</td><td>{(r['note'] or '—')}</td>"
                             f"<td class='muted'>{(r['matched_by'] or '—')}<br>{(r['matched_at'] or '')[:16]}</td></tr>")
            parts.append("</tbody></table>")

        # 4) Document evidence — the actual lines used
        parts.append("<h2>④ เอกสารที่ใช้อ้างอิง (หลักฐาน)</h2>")
        bk = bank.get(bid, [])
        if bk:
            parts.append("<h3>รายการเดินบัญชีธนาคาร</h3><table><thead><tr><th>ช่องทาง</th><th>บัญชี</th>"
                         "<th class='right'>เงินเข้า</th></tr></thead><tbody>")
            for r in bk:
                parts.append(f"<tr><td>{r['channel']}</td><td class='mono'>{r['account_no'] or '—'}</td>"
                             f"<td class='right mono'>{_m(r['credit'])}</td></tr>")
            parts.append("</tbody></table>")
        if cc_l:
            parts.append("<h3>สรุปยอดบัตรเครดิต (settlement)</h3><table><thead><tr><th>ผู้รับบัตร</th>"
                         "<th>MID/Terminal</th><th class='right'>ยอดเต็ม</th><th class='right'>สุทธิ</th></tr></thead><tbody>")
            for r in cc_l:
                parts.append(f"<tr><td>{r['acquirer'] or '—'}</td><td class='mono'>{(r['merchant_id'] or '—')}/{(r['terminal_id'] or '—')}</td>"
                             f"<td class='right mono'>{_m(r['gross_amount'])}</td><td class='right mono'>{_m(r['net_amount'])}</td></tr>")
            parts.append("</tbody></table>")
        if gr_l or ln_l:
            parts.append("<h3>รายงาน Grab / Line Man (ออเดอร์สำเร็จ)</h3><table><thead><tr><th>ผู้ให้บริการ</th>"
                         "<th class='right'>จำนวนออเดอร์</th><th class='right'>ยอดอาหาร</th></tr></thead><tbody>")
            if gr_l: parts.append(f"<tr><td>Grab</td><td class='right'>{len(gr_l)}</td><td class='right mono'>{_m(sum(float(r['food_amount'] or 0) for r in gr_l))}</td></tr>")
            if ln_l: parts.append(f"<tr><td>Line Man</td><td class='right'>{len(ln_l)}</td><td class='right mono'>{_m(sum(float(r['food_amount'] or 0) for r in ln_l))}</td></tr>")
            parts.append("</tbody></table>")
        if not (bk or cc_l or gr_l or ln_l):
            parts.append("<p class='note'>ยังไม่มีเอกสารอัปโหลดสำหรับวันนี้ — ยอดที่แสดงเป็นฝั่ง POS เท่านั้น</p>")

        parts.append("<div class='sign'><div>ผู้จัดทำ (กระทบยอด)</div><div>ผู้ตรวจสอบ</div><div>ผู้อนุมัติ</div></div>")
        parts.append("</div>")  # sheet

    parts.append("</body></html>")
    return "".join(parts)


@router.get("/api/daily-report")
def daily_report(day: str, branch_id: int | None = None):
    """Print-to-PDF daily reconciliation evidence: POS vs documents, manual
    matches (wrong-category reclass + reason), all fees, and VAT — per the
    selected day. Omit branch_id for an all-branches pack (one page each)."""
    if not _DAY_RE.match(day or ""):
        raise HTTPException(400, "day must be in 'YYYY-MM-DD' format.")
    with get_db() as conn:
        if not _has_table(conn, "pos_payment_day"):
            html = (f"<!doctype html><html lang=\"th\"><meta charset=\"utf-8\">"
                    f"<body style=\"font-family:sans-serif;padding:2rem\">"
                    f"<h2>รายงานกระทบยอดรายวัน {day}</h2><p>{_POS_DAY_UNAVAILABLE}</p></body></html>")
            return Response(content=html, media_type="text/html; charset=utf-8")
        html = _daily_report_html(conn, day, branch_id)
    return Response(content=html, media_type="text/html; charset=utf-8")


# status → (Thai label, css class, bucket)  bucket: ok | issue | wait | none
_MATCH_ST = {
    "matched":       ("✓ ตรง",            "m-ok",   "ok"),
    "manual":        ("✓ จับคู่มือ",       "m-ok",   "ok"),
    "variance":      ("✗ ต่าง",           "m-err",  "issue"),
    "missing":       ("✗ ขาดเอกสาร",      "m-err",  "issue"),
    "ext_only":      ("เกินเอกสาร",        "m-warn", "issue"),
    "pending":       ("รอเอกสารตามรอบ",    "m-wait", "wait"),
    "unmapped_bank": ("ยังไม่ตั้งธนาคาร",  "m-info", "issue"),
    "no_source":     ("ไม่มีแหล่งอ้างอิง",  "m-mut",  "none"),
}


def _daily_finance_html(conn, branch_id: int, period_month: str) -> str:
    """Per-day accounting breakdown rendered as the table inside the monthly report.
    Same numbers as the daybook cards — see _daily_finance_data. The in-app section
    (📅 สรุปบัญชีรายวัน รายใบ) renders one card per day from the same data."""
    import html as _html
    def esc(s): return _html.escape(str(s or ""))
    def m(v):  return f"{float(v or 0):,.2f}"
    data = _daily_finance_data(conn, branch_id, period_month)
    if not data["days"]:
        return ""

    P = ["<h2 style='border-left-color:#0f6e56'>📅 สรุปบัญชีรายวัน (ยอดเต็ม / ส่วนลดแยกบรรทัด)</h2>",
         "<table><thead><tr><th class='l'>วันที่</th><th class='l'>รหัสบัญชี</th><th class='l'>ชื่อบัญชี</th>"
         "<th class='l'>ช่องทาง</th><th>ยอดเต็ม</th><th>ส่วนลด</th><th>ค่าธรรมเนียม</th>"
         "<th>VAT</th><th>สุทธิ</th></tr></thead><tbody>"]
    for day in data["days"]:
        for i, a in enumerate(day["channels"]):
            daycell = f"<span class='day'>{day['dom']}</span>" if i == 0 else ""
            P.append(
                f"<tr class='{'daystart' if i == 0 else ''}'><td class='l'>{daycell}</td>"
                f"<td class='l mono'>{esc(a['code']) or '—'}</td><td class='l'>{esc(a['name']) or '—'}</td>"
                f"<td class='l'>{esc(a['ch_th'])}</td>"
                f"<td class='mono'>{m(a['gross'])}</td><td class='mono'>{m(a['discount'])}</td>"
                f"<td class='mono'>{m(a['fee'])}</td><td class='mono'>{m(a['vat'])}</td>"
                f"<td class='mono'>{m(a['net'])}</td></tr>")
    t = data["totals"]
    P.append(
        f"</tbody><tfoot><tr><td class='l' colspan='4'>รวมทั้งเดือน</td>"
        f"<td class='mono'>{m(t['gross'])}</td><td class='mono'>{m(t['discount'])}</td><td class='mono'>{m(t['fee'])}</td>"
        f"<td class='mono'>{m(t['vat'])}</td><td class='mono'>{m(t['net'])}</td></tr></tfoot></table>"
        "<p class='legend'>ยอดเต็ม = ยอดขายก่อนหักส่วนลด/ค่าธรรมเนียม · ส่วนลด = โปรโมชัน/คืนเงิน (บันทึกแยกบรรทัด) · "
        "สุทธิ = ยอดเต็ม − ส่วนลด − ค่าธรรมเนียม − VAT · รหัส/ชื่อบัญชีเติมภายหลัง</p>")
    return "".join(P)


def _monthly_finance_html(conn, branch_id: int, period_month: str) -> str:
    """Monthly receipts by channel (gross → discount → fee → VAT → net) + account
    code/name + a draft journal, so the books tie to what came in. Amounts are GROSS;
    discount is recorded separately. Fee/VAT use the effective (override-aware) figures."""
    import html as _html
    def esc(s): return _html.escape(str(s or ""))
    def m(v):  return f"{float(v or 0):,.2f}"
    acct = _account_map(conn)

    rows = []   # (channel, gross, discount, fee, vat, net)
    for ch, tbl, dcol, gcol, fcol, vcol, discol, extra in _FEE_CH_SPEC:
        try:
            lines = conn.execute(
                f"SELECT * FROM {tbl} WHERE branch_id=? AND substr({dcol},1,7)=?{extra}",
                (branch_id, period_month)).fetchall()
        except sqlite3.OperationalError:
            continue
        g = disc = f = v = 0.0
        for r in lines:
            g += float(r[gcol] or 0)
            fee, vat = _eff_fee_vat(r, fcol, vcol)
            f += fee; v += vat; disc += float(r[discol] or 0)
        if g > 0.005:
            rows.append((ch, g, disc, f, v, g - disc - f - v))

    # no-fee channels (cash / QR / transfer): money received = POS, no fee/VAT.
    # COUPON splits: KTC/BAY points = real money (kept in COUPON); other discount
    # coupons (ผู้สูงอายุ/เด็ก/อื่นๆ) book the full price as revenue + a discount
    # deduction (net 0) so REVENUE stays gross and the journal's discount line carries them.
    try:
        rm = reconcile_month(conn, branch_id, period_month)
        pts_total = round(sum(point_pos_total_daily(conn, branch_id, period_month).values()), 2)
        for c in rm.get("channels", []):
            key = c["key"]; pos = float(c.get("pos", 0))
            if key in ("CASH", "QR", "TRANSFER") and pos > 0.005:
                rows.append((key, pos, 0.0, 0.0, 0.0, pos))
            elif key == "COUPON" and pos > 0.005:
                pts = min(max(pts_total, 0.0), pos); disc = round(pos - pts, 2)
                if pts > 0.005:
                    rows.append(("COUPON", round(pts, 2), 0.0, 0.0, 0.0, round(pts, 2)))
                if disc > 0.005:
                    rows.append(("DISCOUNT", disc, disc, 0.0, 0.0, 0.0))
    except Exception:
        pass

    # Bill-level coupon discounts (ส่วนลดเด็ก/ผู้สูงอายุ/วันเกิด …) from pos_coupon_day —
    # not a payment tender, so otherwise missing here. Gross up revenue + book the discount
    # (net 0), merged into the single ส่วนลด row so month and per-day journals agree.
    coupon_disc = round(sum(coupon_discount_total_daily(conn, branch_id, period_month).values()), 2)
    if coupon_disc > 0.005:
        for i, r in enumerate(rows):
            if r[0] == "DISCOUNT":
                rows[i] = ("DISCOUNT", r[1] + coupon_disc, r[2] + coupon_disc, 0.0, 0.0, 0.0)
                break
        else:
            rows.append(("DISCOUNT", coupon_disc, coupon_disc, 0.0, 0.0, 0.0))

    if not rows:
        return ""

    rows.sort(key=lambda r: _FINANCE_CH_ORDER.get(r[0], 9))
    G = sum(r[1] for r in rows); D = sum(r[2] for r in rows)
    F = sum(r[3] for r in rows); V = sum(r[4] for r in rows); N = sum(r[5] for r in rows)
    out_vat = round(G * 7 / 107, 2)            # output VAT estimate if prices are VAT-inclusive

    def acc_cells(slot):
        code, name = acct.get(slot, ("", ""))
        return f"<td class='l mono'>{esc(code) or '—'}</td><td class='l'>{esc(name) or '—'}</td>"

    P = ["<div style='margin-top:14px;border-top:2px solid #0f6e56;padding-top:6px'>"
         "<h2 style='border-left-color:#0f6e56'>💰 สรุปการรับเงินทั้งเดือน · ส่วนลด · ค่าธรรมเนียม · VAT</h2>",
         "<table><thead><tr><th class='l'>รหัสบัญชี</th><th class='l'>ชื่อบัญชี</th><th class='l'>ช่องทาง</th>"
         "<th>ยอดรับเต็ม</th><th>ส่วนลด</th><th>ค่าธรรมเนียม</th>"
         "<th>VAT (ค่าธรรมเนียม)</th><th>สุทธิเข้าบัญชี</th></tr></thead><tbody>"]
    for ch, g, disc, f, v, n in rows:
        P.append(f"<tr>{acc_cells(ch)}<td class='l'>{esc(_MATCH_CH_TH.get(ch, ch))}</td>"
                 f"<td class='mono'>{m(g)}</td><td class='mono'>{m(disc)}</td><td class='mono'>{m(f)}</td>"
                 f"<td class='mono'>{m(v)}</td><td class='mono'>{m(n)}</td></tr>")
    P.append(f"</tbody><tfoot><tr><td class='l' colspan='3'>รวม</td><td class='mono'>{m(G)}</td>"
             f"<td class='mono'>{m(D)}</td><td class='mono'>{m(F)}</td><td class='mono'>{m(V)}</td>"
             f"<td class='mono'>{m(N)}</td></tr></tfoot></table>")

    # draft journal entry (money-received basis) — gross revenue, discount booked apart
    P.append("<h2 style='border-left-color:#0f6e56'>📒 ร่างการบันทึกบัญชี (รับชำระเงิน)</h2>")
    P.append("<table><thead><tr><th class='l'>รหัสบัญชี</th><th class='l'>บัญชี</th>"
             "<th>เดบิต (Dr)</th><th>เครดิต (Cr)</th></tr></thead><tbody>")
    je = [("BANK", "เงินสด / ธนาคาร (รับสุทธิ)", N, 0), ("DISCOUNT", "ส่วนลด / โปรโมชัน", D, 0),
          ("FEE", "ค่าธรรมเนียมรับชำระเงิน", F, 0), ("VAT", "ภาษีซื้อ – VAT ค่าธรรมเนียม", V, 0),
          ("REVENUE", "รายได้จากการขาย (ยอดรับเต็ม)", 0, G)]
    for slot, acc, dr, cr in je:
        code, _name = acct.get(slot, ("", ""))
        P.append(f"<tr><td class='l mono'>{esc(code) or '—'}</td><td class='l'>{esc(acc)}</td>"
                 f"<td class='mono'>{m(dr) if dr else ''}</td><td class='mono'>{m(cr) if cr else ''}</td></tr>")
    P.append(f"</tbody><tfoot><tr><td class='l' colspan='2'>รวม</td><td class='mono'>{m(N + D + F + V)}</td>"
             f"<td class='mono'>{m(G)}</td></tr></tfoot></table>")
    P.append("<div class='legend'>หมายเหตุ: VAT ในตารางคือ <b>ภาษีซื้อ</b>จากค่าธรรมเนียมรับชำระ (บัตร/เดลิเวอรี) · "
             f"หากราคาขายรวม VAT แล้ว <b>ภาษีขาย (output) โดยประมาณ = ฿{m(out_vat)}</b> (7/107 ของยอดรับเต็ม) "
             "ซึ่งฝ่ายบัญชีแยกออกจากรายได้ตามเกณฑ์ ex-VAT · ตัวเลขนี้เป็นร่างตามข้อมูลกระทบยอด ฝ่ายบัญชีปรับตามผังบัญชีจริง</div>")
    P.append("</div>")
    return "".join(P)


def _day_finance_html(conn, branch_id: int, day: str) -> str:
    """Receipts summary + draft journal entry (Dr/Cr) for ONE calendar day, so the day's
    accounting can be posted straight into the daily books. Same gross → discount → fee →
    VAT → net split (incl. the KTC/BAY coupon-points vs discount-coupon handling) as the
    monthly journal — see _monthly_finance_html — but scoped to a single day via
    _daily_finance_data so screen, monthly report and paper all tie to the baht."""
    import html as _html
    def esc(s): return _html.escape(str(s or ""))
    def m(v):  return f"{float(v or 0):,.2f}"
    acct = _account_map(conn)
    data = _daily_finance_data(conn, branch_id, day[:7])
    dd = next((x for x in data["days"] if x["day"] == day), None)
    if not dd or not dd["channels"]:
        return ("<h2 style='border-left-color:#0f6e56'>📒 ร่างการบันทึกบัญชี (รับชำระเงิน) — "
                f"วันที่ {esc(day)}</h2><div class='empty'>ไม่มีรายการรับเงินในวันนี้</div>")
    chans = dd["channels"]; t = dd["totals"]
    G, D, F, V, N = t["gross"], t["discount"], t["fee"], t["vat"], t["net"]
    out_vat = round(G * 7 / 107, 2)

    def acc_cells(slot):
        code, name = acct.get(slot, ("", ""))
        return f"<td class='l mono'>{esc(code) or '—'}</td><td class='l'>{esc(name) or '—'}</td>"

    P = ["<div style='margin-top:14px;border-top:2px solid #0f6e56;padding-top:6px'>"
         f"<h2 style='border-left-color:#0f6e56'>💰 สรุปการรับเงินวันที่ {esc(day)} · ส่วนลด · ค่าธรรมเนียม · VAT</h2>",
         "<table><thead><tr><th class='l'>รหัสบัญชี</th><th class='l'>ชื่อบัญชี</th><th class='l'>ช่องทาง</th>"
         "<th>ยอดรับเต็ม</th><th>ส่วนลด</th><th>ค่าธรรมเนียม</th>"
         "<th>VAT (ค่าธรรมเนียม)</th><th>สุทธิเข้าบัญชี</th></tr></thead><tbody>"]
    for a in chans:
        P.append(f"<tr>{acc_cells(a['ch'])}<td class='l'>{esc(a['ch_th'])}</td>"
                 f"<td class='mono'>{m(a['gross'])}</td><td class='mono'>{m(a['discount'])}</td><td class='mono'>{m(a['fee'])}</td>"
                 f"<td class='mono'>{m(a['vat'])}</td><td class='mono'>{m(a['net'])}</td></tr>")
    P.append(f"</tbody><tfoot><tr><td class='l' colspan='3'>รวม</td><td class='mono'>{m(G)}</td>"
             f"<td class='mono'>{m(D)}</td><td class='mono'>{m(F)}</td><td class='mono'>{m(V)}</td>"
             f"<td class='mono'>{m(N)}</td></tr></tfoot></table>")

    # draft journal entry for THIS day (money-received basis) — gross revenue, discount apart
    P.append(f"<h2 style='border-left-color:#0f6e56'>📒 ร่างการบันทึกบัญชี (รับชำระเงิน) — วันที่ {esc(day)}</h2>")
    P.append("<table><thead><tr><th class='l'>รหัสบัญชี</th><th class='l'>บัญชี</th>"
             "<th>เดบิต (Dr)</th><th>เครดิต (Cr)</th></tr></thead><tbody>")
    je = [("BANK", "เงินสด / ธนาคาร (รับสุทธิ)", N, 0), ("DISCOUNT", "ส่วนลด / โปรโมชัน", D, 0),
          ("FEE", "ค่าธรรมเนียมรับชำระเงิน", F, 0), ("VAT", "ภาษีซื้อ – VAT ค่าธรรมเนียม", V, 0),
          ("REVENUE", "รายได้จากการขาย (ยอดรับเต็ม)", 0, G)]
    for slot, acc, dr, cr in je:
        code, _name = acct.get(slot, ("", ""))
        P.append(f"<tr><td class='l mono'>{esc(code) or '—'}</td><td class='l'>{esc(acc)}</td>"
                 f"<td class='mono'>{m(dr) if dr else ''}</td><td class='mono'>{m(cr) if cr else ''}</td></tr>")
    P.append(f"</tbody><tfoot><tr><td class='l' colspan='2'>รวม</td><td class='mono'>{m(N + D + F + V)}</td>"
             f"<td class='mono'>{m(G)}</td></tr></tfoot></table>")
    P.append("<div class='legend'>หมายเหตุ: VAT ในตารางคือ <b>ภาษีซื้อ</b>จากค่าธรรมเนียมรับชำระ (บัตร/เดลิเวอรี) · "
             f"หากราคาขายรวม VAT แล้ว <b>ภาษีขาย (output) โดยประมาณ = ฿{m(out_vat)}</b> (7/107 ของยอดรับเต็ม) "
             "ซึ่งฝ่ายบัญชีแยกออกจากรายได้ตามเกณฑ์ ex-VAT · ตัวเลขนี้เป็นร่างตามข้อมูลกระทบยอด ฝ่ายบัญชีปรับตามผังบัญชีจริง</div>")
    P.append("</div>")
    return "".join(P)


def _audit_detail_html(conn, branch_id: int, period_month: str) -> str:
    """Audit-grade evidence for ONE branch+month so an auditor can trace every baht:
    POS composition (codes), credit settlement by acquirer (gross/fee/VAT/net), every
    bank credit line (date/description/ref), Grab/LM payout, and manual matches."""
    import html as _html
    def esc(s): return _html.escape(str(s or ""))
    def m(v):  return f"{float(v or 0):,.2f}"
    P = []

    # 1) POS composition — every payment code, its channel + acquirer/bank
    try:
        codes = pos_codes_scope(conn, branch_id, period_month=period_month)
    except sqlite3.OperationalError:
        codes = []
    if codes:
        P.append("<h2>① ที่มาของยอด POS (รายรหัสรับเงิน)</h2>")
        P.append("<table><thead><tr><th class='l'>รหัสรับเงิน</th><th class='l'>ช่องทาง</th>"
                 "<th class='l'>ธนาคาร/ผู้รับบัตร</th><th>จำนวนรายการ</th><th>ยอด (บาท)</th></tr></thead><tbody>")
        tot = 0.0
        for c in sorted(codes, key=lambda x: (x["category"] or "zz", -x["amount"])):
            tot += c["amount"]
            ch = _MATCH_CH_TH.get(c["category"], c["category"] or "—")
            bk = _BANK_LABEL.get(c["bank"], c["bank"] or "—")
            P.append(f"<tr><td class='l mono'>{esc(c['code'])}</td><td class='l'>{esc(ch)}</td>"
                     f"<td class='l'>{esc(bk)}</td><td class='mono'>{c['txn_count']:,}</td>"
                     f"<td class='mono'>{m(c['amount'])}</td></tr>")
        P.append(f"</tbody><tfoot><tr><td colspan='4' class='l'>รวม POS</td>"
                 f"<td class='mono'>{m(tot)}</td></tr></tfoot></table>")

    # 2) Credit settlement by acquirer (the document that backs the CREDIT lane)
    cc = conn.execute(
        "SELECT acquirer, COUNT(*) n, SUM(COALESCE(gross_amount,0)) g, SUM(COALESCE(mdr_fee,0)) f, "
        "SUM(COALESCE(vat_amount,0)) v, SUM(COALESCE(net_amount,0)) net "
        "FROM recon_cc_lines WHERE branch_id=? AND substr(settlement_date,1,7)=? GROUP BY acquirer",
        (branch_id, period_month)).fetchall()
    if cc:
        P.append("<h2>② สรุปยอดบัตรเครดิต ตามผู้รับบัตร (acquirer)</h2>")
        P.append("<table><thead><tr><th class='l'>ผู้รับบัตร</th><th>จำนวนแบตช์</th><th>ยอดเต็ม</th>"
                 "<th>ค่าธรรมเนียม</th><th>VAT</th><th>สุทธิ (เข้าบัญชี)</th></tr></thead><tbody>")
        for r in cc:
            P.append(f"<tr><td class='l'>{esc(_BANK_LABEL.get(r['acquirer'], r['acquirer'] or '—'))}</td>"
                     f"<td class='mono'>{r['n']:,}</td><td class='mono'>{m(r['g'])}</td>"
                     f"<td class='mono'>{m(r['f'])}</td><td class='mono'>{m(r['v'])}</td>"
                     f"<td class='mono'>{m(r['net'])}</td></tr>")
        P.append("</tbody></table>")

    # 3) Every bank credit line — the actual money received (grouped by channel)
    bl = conn.execute(
        "SELECT txn_date, description, channel, ref_no, account_no, COALESCE(credit,0) credit "
        "FROM recon_bank_lines WHERE branch_id=? AND substr(txn_date,1,7)=? AND credit>0 "
        "ORDER BY channel, txn_date", (branch_id, period_month)).fetchall()
    if bl:
        P.append("<h2>③ รายการเงินเข้าบัญชีธนาคาร (ทุกบรรทัด)</h2>")
        P.append("<table><thead><tr><th class='l'>วันที่</th><th class='l'>ช่องทาง</th>"
                 "<th class='l'>รายละเอียด</th><th class='l'>อ้างอิง</th><th class='l'>บัญชี</th>"
                 "<th>เงินเข้า (บาท)</th></tr></thead><tbody>")
        tot = 0.0
        for r in bl:
            tot += float(r["credit"])
            cat = _BANK_CH_TO_CAT.get((r["channel"] or "").upper())
            ch = _MATCH_CH_TH.get(cat, r["channel"] or "—")
            P.append(f"<tr><td class='l mono'>{esc(r['txn_date'])}</td><td class='l'>{esc(ch)}</td>"
                     f"<td class='l'>{esc((r['description'] or '')[:48])}</td>"
                     f"<td class='l mono'>{esc(r['ref_no'] or '—')}</td>"
                     f"<td class='l mono'>{esc(r['account_no'] or '—')}</td>"
                     f"<td class='mono'>{m(r['credit'])}</td></tr>")
        P.append(f"</tbody><tfoot><tr><td colspan='5' class='l'>รวมเงินเข้า</td>"
                 f"<td class='mono'>{m(tot)}</td></tr></tfoot></table>")

    # 4) Grab / Line Man payout summary (gross → fee+VAT → net). Note the VAT column
    # differs: grab='vat_amount', lineman='vat_on_commission'.
    for tbl, label, vatcol in (("recon_grab_lines", "Grab", "vat_amount"),
                               ("recon_lineman_lines", "Line Man", "vat_on_commission")):
        try:
            gr = conn.execute(
                f"SELECT COUNT(*) n, SUM(COALESCE(food_amount,0)) g, SUM(COALESCE(commission_amt,0)) f, "
                f"SUM(COALESCE({vatcol},0)) v, SUM(COALESCE(net_payout,0)) net "
                f"FROM {tbl} WHERE branch_id=? AND substr(order_date,1,7)=? AND order_status='Completed'",
                (branch_id, period_month)).fetchone()
        except sqlite3.OperationalError:
            gr = None
        if gr and gr["n"]:
            P.append(f"<h2>④ {esc(label)} — สรุปออเดอร์สำเร็จ</h2>")
            P.append("<table><thead><tr><th>จำนวนออเดอร์</th><th>ยอดอาหาร</th><th>ค่าคอมมิชชั่น</th>"
                     "<th>VAT</th><th>สุทธิ (payout)</th></tr></thead><tbody>")
            P.append(f"<tr><td class='mono'>{gr['n']:,}</td><td class='mono'>{m(gr['g'])}</td>"
                     f"<td class='mono'>{m(gr['f'])}</td><td class='mono'>{m(gr['v'])}</td>"
                     f"<td class='mono'>{m(gr['net'])}</td></tr></tbody></table>")

    # 5) Manual matches — every hand correction, with reason + who
    mm = conn.execute(
        "SELECT category, bank, COALESCE(pos_amount,0) p, COALESCE(ext_amount,0) e, pos_days, note, matched_by "
        "FROM recon_manual_match WHERE branch_id=? AND period_month=? ORDER BY matched_at",
        (branch_id, period_month)).fetchall()
    if mm:
        P.append("<h2>⑤ การจับคู่ด้วยมือ (พร้อมเหตุผล)</h2>")
        P.append("<table><thead><tr><th class='l'>ช่องทาง</th><th class='l'>ธนาคาร</th><th class='l'>วันที่</th>"
                 "<th>POS</th><th>เอกสาร</th><th class='l'>เหตุผล</th><th class='l'>โดย</th></tr></thead><tbody>")
        for r in mm:
            try:
                days = ", ".join(x.get("day", "")[8:] for x in json.loads(r["pos_days"] or "[]"))
            except Exception:
                days = ""
            P.append(f"<tr><td class='l'>{esc(_MATCH_CH_TH.get(r['category'], r['category']))}</td>"
                     f"<td class='l'>{esc(r['bank'] or '—')}</td><td class='l mono'>{esc(days)}</td>"
                     f"<td class='mono'>{m(r['p'])}</td><td class='mono'>{m(r['e'])}</td>"
                     f"<td class='l'>{esc(r['note'] or '—')}</td><td class='l'>{esc(r['matched_by'] or '—')}</td></tr>")
        P.append("</tbody></table>")

    if not P:
        return "<div class='empty'>ไม่มีข้อมูลหลักฐานสำหรับเดือนนี้</div>"
    return ("<div style='margin-top:14px;border-top:2px solid #ea580c;padding-top:6px'>"
            "<h2 style='border:0;padding:0;color:#9a3412'>📑 หลักฐานประกอบ (สำหรับผู้ตรวจสอบ) — "
            "ตรวจทานได้ทุกบรรทัด</h2>" + "".join(P) + "</div>")


def _monthly_matching_html(conn, period_month: str, branch_id: int | None,
                           detail: bool = False, day_sel: str | None = None) -> str:
    import html as _html
    def esc(s): return _html.escape(str(s or ""))
    def m(v):  return f"{float(v or 0):,.0f}"

    if branch_id:
        brs = conn.execute("SELECT branch_id, name FROM branches WHERE branch_id=?",
                           (branch_id,)).fetchall()
    else:
        # all-branches pack = the 5 master (active) branches only, like every other view
        brs = conn.execute(
            f"SELECT branch_id, name FROM branches WHERE {_MASTER_ONLY} ORDER BY name").fetchall()
    # master list drives the on-page branch picker (always all 5, regardless of filter)
    master = conn.execute(
        f"SELECT branch_id, name FROM branches WHERE {_MASTER_ONLY} ORDER BY name").fetchall()

    css = """<style>
      @page{size:A4;margin:13mm}
      *{box-sizing:border-box} body{font-family:'Sarabun','TH Sarabun New',system-ui,sans-serif;color:#1e293b;font-size:11.5px;margin:0;padding:22px;background:#f8fafc}
      .sheet{background:#fff;max-width:1000px;margin:0 auto 18px;padding:24px 28px;border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
      h1{font-size:18px;margin:0 0 2px} .sub{color:#64748b;font-size:12px;margin-bottom:12px}
      .kpis{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0 14px}
      .kpi{border:1px solid #e5e7eb;border-radius:8px;padding:7px 12px;min-width:96px}
      .kpi .l{font-size:10px;color:#64748b} .kpi .v{font-size:16px;font-weight:700}
      .kpi.ok .v{color:#166534} .kpi.issue .v{color:#b91c1c} .kpi.wait .v{color:#92610e}
      table{width:100%;border-collapse:collapse;font-size:11px;margin-top:4px}
      th,td{padding:4px 8px;border-bottom:1px solid #eef2f7;text-align:right}
      th{color:#64748b;font-size:9.5px;text-transform:uppercase;border-bottom:1.5px solid #e2e8f0;background:#f8fafc;position:sticky;top:0}
      td:first-child,th:first-child,td.l,th.l{text-align:left}
      .mono{font-variant-numeric:tabular-nums}
      tr.daystart td{border-top:2px solid #cbd5e1}
      tr.issue{background:#fef2f2} tr.wait{background:#fffbeb}
      .day{font-weight:700;color:#334155}
      .badge{padding:1px 7px;border-radius:99px;font-size:9.5px;font-weight:700;white-space:nowrap}
      .m-ok{background:#dcfce7;color:#166534}.m-err{background:#fee2e2;color:#b91c1c}
      .m-warn{background:#ffedd5;color:#9a3412}.m-wait{background:#fef9c3;color:#854d0e}
      .m-info{background:#dbeafe;color:#1e40af}.m-mut{background:#f1f5f9;color:#475569}
      .neg{color:#b91c1c} .legend{font-size:10px;color:#64748b;margin:2px 0 10px}
      h2{font-size:13px;margin:18px 0 6px;border-left:4px solid #ea580c;padding-left:8px}
      .sign{display:flex;gap:40px;margin-top:26px}.sign div{flex:1;border-top:1px solid #94a3b8;padding-top:5px;text-align:center;font-size:11px;color:#475569}
      .toolbar{max-width:1000px;margin:0 auto 12px;text-align:right}
      .btn{background:#ea580c;color:#fff;border:0;border-radius:8px;padding:9px 18px;font-size:13px;cursor:pointer}
      .ok-all{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:9px 13px;color:#166534;font-size:12px;margin:8px 0}
      .empty{color:#64748b;font-size:12px;padding:10px 0}
      @media print{body{background:#fff;padding:0}.toolbar{display:none}.sheet{box-shadow:none;border-radius:0;max-width:none;margin:0;page-break-after:always}th{position:static}}
    </style>"""

    def th_date(d):
        try:
            return f"{int(d[8:])}"
        except Exception:
            return d

    # query-string fragments preserved when a control changes (so detail/day/branch stick)
    _detail_q = "&detail=1" if detail else ""
    _day_q = f"&day={day_sel}" if day_sel else ""
    _br_q = f"&branch_id={branch_id}" if branch_id else ""

    # on-page branch picker (hidden when printing) — choose 1 of the 5 or all
    bopts = (f"<option value=''{'' if branch_id else ' selected'}>ทุกสาขา (5 สาขาหลัก)</option>"
             + "".join(f"<option value='{b['branch_id']}'"
                       f"{' selected' if branch_id == b['branch_id'] else ''}>{esc(b['name'])}</option>"
                       for b in master))
    picker = (f"<select onchange=\"location.href='?period_month={period_month}'"
              f"+(this.value?'&branch_id='+this.value:'')+'{_detail_q}{_day_q}'\" "
              "style='padding:8px 10px;border:1px solid #cbd5e1;border-radius:8px;"
              "font:inherit;margin-right:8px'>" + bopts + "</select>")
    # date filter — pick ONE day to post that day's accounting; clear to view the whole month
    daypick = (f"<input type='date' value='{esc(day_sel or '')}' "
               f"min='{period_month}-01' max='{period_month}-31' title='เลือกวันเดียว' "
               f"onchange=\"location.href='?period_month={period_month}{_br_q}{_detail_q}'"
               "+(this.value?'&day='+this.value:'')\" "
               "style='padding:7px 10px;border:1px solid #cbd5e1;border-radius:8px;font:inherit;margin-right:8px'>")
    clear = (f"<a href='?period_month={period_month}{_br_q}{_detail_q}' "
             "style='margin-right:10px;font-size:12px;color:#0f6e56'>↺ ทั้งเดือน</a>"
             if day_sel else "")

    parts = ["<!doctype html><html lang='th'><head><meta charset='utf-8'>",
             "<link href='https://fonts.googleapis.com/css2?family=Sarabun:wght@400;600;700&display=swap' rel='stylesheet'>",
             "<title>รายงานการจับคู่รายวัน</title>", css, "</head><body>",
             "<div class='toolbar'>" + picker + daypick + clear
             + "<button class='btn' onclick='window.print()'>🖨️ พิมพ์ / บันทึก PDF</button></div>"]

    for b in brs:
        bid, bname = b["branch_id"], b["name"]
        try:
            d = daily_reconciliation(conn, bid, period_month)
        except Exception:
            d = {"pos_daily_available": False, "days": []}
        parts.append("<div class='sheet'>")
        parts.append(f"<h1>รายงานการจับคู่รายวัน — สาขา{esc(bname)}</h1>")
        if day_sel:
            parts.append(f"<div class='sub'>เฉพาะวันที่ <b>{esc(day_sel)}</b> · "
                         "สำหรับนำการบันทึกบัญชีของวันนี้เข้าระบบบัญชีรายวัน</div>")
        else:
            parts.append(f"<div class='sub'>เดือน {esc(period_month)} · ตรวจสอบว่าทุกช่องทางในแต่ละวันจับคู่กับเอกสารถูกต้อง</div>")
        if not d.get("pos_daily_available"):
            parts.append("<div class='empty'>ยังไม่มีข้อมูล POS รายวันสำหรับเดือนนี้ — ต้องซิงก์ข้อมูลก่อน</div></div>")
            continue

        # active lanes only (POS or document present)
        rows, n_ok, n_issue, n_wait = [], 0, 0, 0
        tot_pos = tot_ext = tot_absvar = 0.0
        for day in d["days"]:
            if day_sel and day["day"] != day_sel:   # date filter — one day only
                continue
            day_lanes = [l for l in day["lanes"]
                         if abs(l["pos"]) > 0.005 or abs(l["ext"]) > 0.005]
            for i, l in enumerate(day_lanes):
                lab, cls, bucket = _MATCH_ST.get(l["status"], (l["status"], "m-mut", "none"))
                if bucket == "ok": n_ok += 1
                elif bucket == "issue": n_issue += 1
                elif bucket == "wait": n_wait += 1
                tot_pos += l["pos"]; tot_ext += l["ext"]; tot_absvar += abs(l["variance"])
                trcls = (" daystart" if i == 0 else "") + (f" {bucket}" if bucket in ("issue", "wait") else "")
                daycell = f"<span class='day'>{th_date(day['day'])}</span>" if i == 0 else ""
                bank = "" if l["bank"] in ("NONE", "ANY") else esc(l["bank"])
                rows.append(
                    f"<tr class='{trcls.strip()}'>"
                    f"<td class='l'>{daycell}</td>"
                    f"<td class='l'>{esc(_MATCH_CH_TH.get(l['category'], l['category']))}</td>"
                    f"<td class='l'>{bank}</td>"
                    f"<td class='mono'>{m(l['pos'])}</td>"
                    f"<td class='mono'>{m(l['ext'])}</td>"
                    f"<td class='mono{' neg' if abs(l['variance'])>0.5 else ''}'>{m(l['variance'])}</td>"
                    f"<td class='l'><span class='badge {cls}'>{esc(lab)}</span></td></tr>")
        total_lanes = n_ok + n_issue + n_wait

        parts.append("<div class='kpis'>")
        parts.append(f"<div class='kpi ok'><div class='l'>จับคู่ตรง</div><div class='v'>{n_ok}/{total_lanes}</div></div>")
        parts.append(f"<div class='kpi issue'><div class='l'>ต้องตรวจ (ต่าง/ขาด)</div><div class='v'>{n_issue}</div></div>")
        parts.append(f"<div class='kpi wait'><div class='l'>รอเอกสารตามรอบ</div><div class='v'>{n_wait}</div></div>")
        parts.append(f"<div class='kpi'><div class='l'>POS รวม</div><div class='v mono'>{m(tot_pos)}</div></div>")
        parts.append(f"<div class='kpi'><div class='l'>เอกสาร/เงินเข้ารวม</div><div class='v mono'>{m(tot_ext)}</div></div>")
        parts.append(f"<div class='kpi'><div class='l'>ผลต่างสะสม (abs)</div><div class='v mono'>{m(tot_absvar)}</div></div>")
        parts.append("</div>")

        if n_issue == 0 and total_lanes:
            parts.append("<div class='ok-all'>✓ ทุกช่องทางที่มียอดในเดือนนี้จับคู่กับเอกสารครบ ไม่มีรายการที่ต้องตรวจ"
                         + (f" (มี {n_wait} รายการรอเอกสารเข้าตามรอบ — ปกติ)" if n_wait else "") + "</div>")
        parts.append("<div class='legend'>สถานะ: <b>✓ ตรง/จับคู่มือ</b> = ยอด POS ตรงกับเอกสาร · "
                     "<b>✗ ต่าง/ขาดเอกสาร</b> = ต้องตรวจ · <b>เกินเอกสาร</b> = มีเงินเข้าเกินยอด POS · "
                     "<b>รอเอกสารตามรอบ</b> = ยังไม่ถึงรอบเงินเข้า (ไม่ใช่ปัญหา) · ผลต่าง = เอกสาร − POS</div>")

        if not rows and not d["days"]:
            parts.append("<div class='empty'>ไม่มีรายการในเดือนนี้</div>")
        parts.append("<table><thead><tr><th class='l'>วันที่</th><th class='l'>ช่องทาง</th>"
                     "<th class='l'>ธนาคาร</th><th>ยอด POS</th><th>เอกสาร/เงินเข้า</th>"
                     "<th>ผลต่าง</th><th class='l'>สถานะการจับคู่</th></tr></thead><tbody>")
        parts.extend(rows)
        parts.append("</tbody></table>")

        # hand-confirmed matches (reclass corrections) — the human overrides, with reason
        mm = conn.execute(
            "SELECT category, bank, pos_amount, ext_amount, note, matched_by, matched_at "
            "FROM recon_manual_match WHERE branch_id=? AND period_month=? ORDER BY matched_at",
            (bid, period_month)).fetchall()
        if mm and not detail:        # in detail mode the audit pack (⑤) lists these in full
            parts.append("<h2>การจับคู่ด้วยมือ (เจ้าหน้าที่ยืนยันเอง) — มีเหตุผลกำกับ</h2>")
            parts.append("<table><thead><tr><th class='l'>ช่องทาง</th><th class='l'>ธนาคาร</th>"
                         "<th>ยอด POS</th><th>เอกสาร</th><th class='l'>เหตุผล/หมายเหตุ</th>"
                         "<th class='l'>โดย</th></tr></thead><tbody>")
            for r in mm:
                parts.append(
                    f"<tr><td class='l'>{esc(_MATCH_CH_TH.get(r['category'], r['category']))}</td>"
                    f"<td class='l'>{esc(r['bank'] or '—')}</td>"
                    f"<td class='mono'>{m(r['pos_amount'])}</td><td class='mono'>{m(r['ext_amount'])}</td>"
                    f"<td class='l'>{esc(r['note'] or '—')}</td>"
                    f"<td class='l'>{esc(r['matched_by'] or '—')}</td></tr>")
            parts.append("</tbody></table>")

        if day_sel:
            # date-filtered: just that one day's receipts + its own draft journal (Dr/Cr)
            parts.append(_day_finance_html(conn, bid, day_sel))
        else:
            # per-day accounting breakdown (the daily summary: gross + discount split)
            parts.append(_daily_finance_html(conn, bid, period_month))
            # monthly receipts roll-up + fees/VAT + draft journal entry (accounting tie-out)
            parts.append(_monthly_finance_html(conn, bid, period_month))

        if detail and not day_sel:   # audit-grade line-level evidence for this branch (month-wide)
            parts.append(_audit_detail_html(conn, bid, period_month))

        parts.append("<div class='sign'><div>ผู้จัดทำ (กระทบยอด)</div>"
                     "<div>ผู้ตรวจสอบ</div><div>ผู้อนุมัติ</div></div>")
        parts.append("</div>")

    parts.append("</body></html>")
    return "".join(parts)


@router.get("/api/monthly-report")
def monthly_matching_report(period_month: str, branch_id: int | None = None,
                            detail: int = 0, day: str | None = None):
    """Print-to-PDF monthly matching audit: every day × channel with POS vs document,
    variance, and match status — confirm the whole month reconciled. Omit branch_id
    for an all-branches pack (one page each). detail=1 appends audit-grade line-level
    evidence (POS codes, credit-by-acquirer, every bank line, payouts, manual matches).
    day=YYYY-MM-DD (within period_month) scopes the report to one calendar day and shows
    that day's own draft journal entry (Dr/Cr) so it can be posted to the daily books."""
    if not _PERIOD_RE.match(period_month or ""):
        raise HTTPException(400, "period_month must be in 'YYYY-MM' format.")
    if day is not None:
        if not _DAY_RE.match(day) or day[:7] != period_month:
            raise HTTPException(400, "day must be 'YYYY-MM-DD' within period_month.")
    if branch_id:
        with get_db() as conn:
            _validate_master_branch(conn, branch_id)
    with get_db() as conn:
        html = _monthly_matching_html(conn, period_month, branch_id,
                                      detail=bool(detail), day_sel=day)
    return Response(content=html, media_type="text/html; charset=utf-8")


@router.get("/api/daily-accounts")
def daily_accounts(period_month: str, branch_id: int | None = None):
    """สรุปบัญชีรายวัน (รายใบ) for ONE branch: per-day accounting where each day lists its
    channels at GROSS with discount/fee/VAT split out + account code/name, plus a day
    total. Returns the master-branch list so the UI can switch branches; omitting
    branch_id defaults to the first master branch."""
    if not _PERIOD_RE.match(period_month or ""):
        raise HTTPException(400, "period_month must be in 'YYYY-MM' format.")
    with get_db() as conn:
        masters = conn.execute(
            f"SELECT branch_id, name FROM branches WHERE {_MASTER_ONLY} ORDER BY name").fetchall()
        branches = [{"branch_id": b["branch_id"], "name": b["name"]} for b in masters]
        if not branches:
            return {"period_month": period_month, "branches": [], "branch_id": None,
                    "branch_name": None, "days": [], "totals": {}}
        if branch_id is None:
            branch_id = branches[0]["branch_id"]
        else:
            _validate_master_branch(conn, branch_id)
        bname = next((b["name"] for b in branches if b["branch_id"] == branch_id), None)
        data = _daily_finance_data(conn, branch_id, period_month)
        acct = _account_map(conn)
        # account codes/names for the per-day draft journal (Dr/Cr) the UI renders per card
        je_accounts = {s: list(acct.get(s, ("", "")))
                       for s in ("BANK", "DISCOUNT", "FEE", "VAT", "REVENUE")}
    return {"period_month": period_month, "branches": branches,
            "branch_id": branch_id, "branch_name": bname,
            "days": data["days"], "totals": data["totals"], "je_accounts": je_accounts}


def _daily_dashboard_data(conn, day: str) -> dict:
    """Revenue by channel for ONE calendar day across ALL master branches: each branch's
    channels (gross/discount/fee/VAT/net) for that day + per-branch totals, plus grand
    totals across branches. Reuses _daily_finance_data so the KTC/BAY coupon split + the
    ส่วนลด(คูปอง) deduction line are identical to the daybook and monthly report — then
    extracts the single matching day. Also attaches the POS↔money-in view per channel
    (pos_channels): what the register rang up (POS) vs what landed in the bank/settlement
    (ext) + the variance — the reconciliation lens, from the same daily_reconciliation."""
    period_month = day[:7]
    masters = conn.execute(
        f"SELECT branch_id, name FROM branches WHERE {_MASTER_ONLY} ORDER BY name").fetchall()
    zero = {"gross": 0.0, "discount": 0.0, "fee": 0.0, "vat": 0.0, "net": 0.0}
    pzero = {"pos": 0.0, "money": 0.0}
    branches, grand, pgrand = [], dict(zero), dict(pzero)
    for b in masters:
        bid = b["branch_id"]
        try:
            daily = daily_reconciliation(conn, bid, period_month)
        except Exception:
            daily = {"days": []}
        data = _daily_finance_data(conn, bid, period_month, daily=daily)   # one daily compute
        dd = next((x for x in data["days"] if x["day"] == day), None)
        channels = dd["channels"] if dd else []
        totals = dd["totals"] if dd else dict(zero)
        # POS ↔ money-in per channel for the day (sum lanes over banks)
        day_lanes = next((x["lanes"] for x in daily.get("days", []) if x["day"] == day), [])
        pos_agg: dict = {}
        for l in day_lanes:
            e = pos_agg.setdefault(l["category"], {"pos": 0.0, "money": 0.0})
            e["pos"] += float(l.get("pos") or 0); e["money"] += float(l.get("ext") or 0)
        pos_channels, ptot = [], dict(pzero)
        for ch in sorted(pos_agg, key=lambda c: _FINANCE_CH_ORDER.get(c, 9)):
            p, mny = round(pos_agg[ch]["pos"], 2), round(pos_agg[ch]["money"], 2)
            if abs(p) <= 0.005 and abs(mny) <= 0.005:
                continue
            pos_channels.append({"ch": ch, "ch_th": _MATCH_CH_TH.get(ch, ch),
                                 "pos": p, "money": mny, "variance": round(mny - p, 2)})
            ptot["pos"] += p; ptot["money"] += mny
        ptot = {"pos": round(ptot["pos"], 2), "money": round(ptot["money"], 2),
                "variance": round(ptot["money"] - ptot["pos"], 2)}
        branches.append({"branch_id": bid, "name": b["name"],
                         "channels": channels, "totals": totals,
                         "pos_channels": pos_channels, "pos_totals": ptot})
        for k in grand:
            grand[k] += float(totals.get(k, 0) or 0)
        pgrand["pos"] += ptot["pos"]; pgrand["money"] += ptot["money"]
    return {"day": day, "branches": branches,
            "grand_totals": {k: round(v, 2) for k, v in grand.items()},
            "pos_grand": {"pos": round(pgrand["pos"], 2), "money": round(pgrand["money"], 2),
                          "variance": round(pgrand["money"] - pgrand["pos"], 2)}}


@router.get("/api/daily-dashboard")
def daily_dashboard(day: str):
    """Dashboard: รายได้ตามช่องทางของทุกสาขาในวันเดียว (เลือกวันจากปฏิทิน). Returns each
    master branch's per-channel breakdown for `day` + per-branch totals + grand totals
    across branches. Inherits the coupon split (KTC/BAY points kept as เงินรับจริง; other
    discount coupons surfaced as a ส่วนลด deduction line) from _daily_finance_data."""
    try:
        _dt.strptime(day or "", "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "day must be in 'YYYY-MM-DD' format.")
    with get_db() as conn:
        return _daily_dashboard_data(conn, day)


def _daily_accounts_print_html(conn, period_month: str, branch_id: int | None) -> str:
    """Printable per-day card report. Each day is a card (ใบ) with its channels at GROSS
    + discount/fee/VAT split + day total; one branch per sheet (all masters if no
    branch_id). Mirrors _daily_finance_data so screen and paper agree to the baht."""
    import html as _html
    def esc(s): return _html.escape(str(s or ""))
    def m(v):  return f"{float(v or 0):,.2f}"
    def neg(v):  # discount/fee/VAT print as a deduction; blank when zero
        return f"−{m(v)}" if abs(float(v or 0)) > 0.005 else "—"
    acct = _account_map(conn)

    def journal_block(t):  # per-day draft journal (Dr/Cr) for posting to the daily books
        def code(slot):
            c = acct.get(slot, ("", ""))[0]
            return f" <span class='code'>{esc(c)}</span>" if c else ""
        rows = [f"<tr><td>เงินสด/ธนาคาร (สุทธิ){code('BANK')}</td><td class='mono'>{m(t['net'])}</td><td class='mono'>—</td></tr>"]
        if abs(float(t.get("discount") or 0)) > 0.005:
            rows.append(f"<tr><td>ส่วนลด/โปรโมชัน{code('DISCOUNT')}</td><td class='mono'>{m(t['discount'])}</td><td class='mono'>—</td></tr>")
        if abs(float(t.get("fee") or 0)) > 0.005:
            rows.append(f"<tr><td>ค่าธรรมเนียมรับชำระเงิน{code('FEE')}</td><td class='mono'>{m(t['fee'])}</td><td class='mono'>—</td></tr>")
        if abs(float(t.get("vat") or 0)) > 0.005:
            rows.append(f"<tr><td>ภาษีซื้อ–VAT ค่าธรรมเนียม{code('VAT')}</td><td class='mono'>{m(t['vat'])}</td><td class='mono'>—</td></tr>")
        rows.append(f"<tr><td>รายได้จากการขาย (เต็ม){code('REVENUE')}</td><td class='mono'>—</td><td class='mono'>{m(t['gross'])}</td></tr>")
        dtot = float(t["net"]) + float(t["discount"]) + float(t["fee"]) + float(t["vat"])
        return ("<table class='je'><thead><tr><th>ร่างบันทึกบัญชี (Dr/Cr)</th><th>เดบิต</th><th>เครดิต</th></tr></thead><tbody>"
                + "".join(rows)
                + f"</tbody><tfoot><tr><td>รวม</td><td class='mono'>{m(dtot)}</td><td class='mono'>{m(t['gross'])}</td></tr></tfoot></table>")

    if branch_id:
        brs = conn.execute("SELECT branch_id, name FROM branches WHERE branch_id=?",
                           (branch_id,)).fetchall()
    else:
        brs = conn.execute(
            f"SELECT branch_id, name FROM branches WHERE {_MASTER_ONLY} ORDER BY name").fetchall()
    master = conn.execute(
        f"SELECT branch_id, name FROM branches WHERE {_MASTER_ONLY} ORDER BY name").fetchall()

    css = """<style>
      @page{size:A4;margin:13mm}
      *{box-sizing:border-box} body{font-family:'Sarabun','TH Sarabun New',system-ui,sans-serif;color:#1e293b;font-size:11.5px;margin:0;padding:22px;background:#f8fafc}
      .sheet{background:#fff;max-width:900px;margin:0 auto 18px;padding:24px 28px;border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
      h1{font-size:18px;margin:0 0 2px} .sub{color:#64748b;font-size:12px;margin-bottom:14px}
      .legend{font-size:10px;color:#64748b;margin:2px 0 14px}
      .cards{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
      .dc{border:1px solid #e2e8f0;border-radius:9px;overflow:hidden;break-inside:avoid}
      .dc-h{display:flex;justify-content:space-between;align-items:center;padding:7px 12px;background:#ecfdf5;border-bottom:1px solid #d1fae5}
      .dc-date{font-weight:700;color:#0a523f;font-size:12.5px}
      .dc-net{font-weight:700;font-size:12.5px}
      table{width:100%;border-collapse:collapse;font-size:10.5px}
      th,td{padding:3px 12px;border-bottom:1px solid #f1f5f9;text-align:right}
      th{color:#64748b;font-size:9px;text-transform:uppercase;background:#fff}
      td:first-child,th:first-child{text-align:left}
      .mono{font-variant-numeric:tabular-nums}
      .disc{color:#9a3412} .code{color:#94a3b8;font-size:9px}
      tfoot td{border-top:1.4px solid #cbd5e1;border-bottom:none;font-weight:700;background:#f8fafc}
      table.je{border-top:1px solid #d1fae5} table.je th{background:#ecfdf5;color:#0a523f}
      .mtot{margin-top:14px;border:1.5px solid #0f6e56;border-radius:9px;overflow:hidden}
      .mtot .dc-h{background:#0f6e56;border-bottom:none} .mtot .dc-date,.mtot .dc-net{color:#fff}
      .empty{color:#64748b;font-size:12px;padding:10px 0}
      .toolbar{max-width:900px;margin:0 auto 12px;text-align:right}
      .btn{background:#0f6e56;color:#fff;border:0;border-radius:8px;padding:9px 18px;font-size:13px;cursor:pointer}
      @media print{body{background:#fff;padding:0}.toolbar{display:none}.sheet{box-shadow:none;border-radius:0;max-width:none;margin:0;page-break-after:always}}
    </style>"""

    bopts = (f"<option value=''{'' if branch_id else ' selected'}>ทุกสาขา ({len(master)} สาขา)</option>"
             + "".join(f"<option value='{b['branch_id']}'"
                       f"{' selected' if branch_id == b['branch_id'] else ''}>{esc(b['name'])}</option>"
                       for b in master))
    picker = (f"<select onchange=\"location.href='?period_month={period_month}'"
              f"+(this.value?'&branch_id='+this.value:'')\" "
              "style='padding:8px 10px;border:1px solid #cbd5e1;border-radius:8px;"
              "font:inherit;margin-right:8px'>" + bopts + "</select>")

    parts = ["<!doctype html><html lang='th'><head><meta charset='utf-8'>",
             "<link href='https://fonts.googleapis.com/css2?family=Sarabun:wght@400;600;700&display=swap' rel='stylesheet'>",
             "<title>สรุปบัญชีรายวัน (รายใบ)</title>", css, "</head><body>",
             "<div class='toolbar'>" + picker
             + "<button class='btn' onclick='window.print()'>🖨️ พิมพ์ / บันทึก PDF</button></div>"]

    def card_table(channels, foot, foot_label):
        rows = ["<table><thead><tr><th>ช่องทาง</th><th>ยอดเต็ม</th><th>ส่วนลด</th>"
                "<th>ค่าธรรมเนียม</th><th>VAT</th><th>สุทธิ</th></tr></thead><tbody>"]
        for a in channels:
            code = f" <span class='code'>{esc(a['code'])}</span>" if a.get("code") else ""
            rows.append(
                f"<tr><td>{esc(a['ch_th'])}{code}</td><td class='mono'>{m(a['gross'])}</td>"
                f"<td class='mono disc'>{neg(a['discount'])}</td><td class='mono'>{neg(a['fee'])}</td>"
                f"<td class='mono'>{neg(a['vat'])}</td><td class='mono'>{m(a['net'])}</td></tr>")
        rows.append(
            f"</tbody><tfoot><tr><td>{foot_label}</td><td class='mono'>{m(foot['gross'])}</td>"
            f"<td class='mono disc'>{neg(foot['discount'])}</td><td class='mono'>{neg(foot['fee'])}</td>"
            f"<td class='mono'>{neg(foot['vat'])}</td><td class='mono'>{m(foot['net'])}</td></tr></tfoot></table>")
        return "".join(rows)

    for b in brs:
        data = _daily_finance_data(conn, b["branch_id"], period_month)
        parts.append("<div class='sheet'>")
        parts.append(f"<h1>สรุปบัญชีรายวัน — สาขา{esc(b['name'])}</h1>")
        parts.append(f"<div class='sub'>เดือน {esc(period_month)} · แต่ละวัน = 1 ใบบันทึกบัญชี "
                     "(บันทึกยอดเต็ม แล้วแยกบรรทัดส่วนลด)</div>")
        if not data["days"]:
            parts.append("<div class='empty'>ไม่มีรายการบัญชีในเดือนนี้</div></div>")
            continue
        parts.append("<div class='cards'>")
        for day in data["days"]:
            parts.append("<div class='dc'><div class='dc-h'>"
                         f"<span class='dc-date'>วันที่ {day['dom']}</span>"
                         f"<span class='dc-net'>สุทธิ ฿{m(day['totals']['net'])}</span></div>"
                         + card_table(day["channels"], day["totals"], "รวมวันนี้")
                         + journal_block(day["totals"]) + "</div>")
        parts.append("</div>")
        parts.append("<div class='mtot'><div class='dc-h'>"
                     "<span class='dc-date'>รวมทั้งเดือน</span>"
                     f"<span class='dc-net'>สุทธิ ฿{m(data['totals']['net'])}</span></div>"
                     + card_table([], data["totals"], "รวมทั้งเดือน") + "</div>")
        parts.append("<div class='legend'>ยอดเต็ม = ยอดขายก่อนหักส่วนลด/ค่าธรรมเนียม · "
                     "ส่วนลด/ค่าธรรมเนียม/VAT แสดงเป็นยอดหัก (−) แยกบรรทัด · "
                     "สุทธิ = ยอดเต็ม − ส่วนลด − ค่าธรรมเนียม − VAT · รหัสบัญชีตั้งที่หน้าตั้งค่าระบบ</div>")
        parts.append("</div>")  # sheet
    parts.append("</body></html>")
    return "".join(parts)


@router.get("/api/daily-accounts.print")
def daily_accounts_print(period_month: str, branch_id: int | None = None):
    """Print-to-PDF of สรุปบัญชีรายวัน (รายใบ): one card per day. Omit branch_id for an
    all-branches pack (one sheet each)."""
    if not _PERIOD_RE.match(period_month or ""):
        raise HTTPException(400, "period_month must be in 'YYYY-MM' format.")
    if branch_id:
        with get_db() as conn:
            _validate_master_branch(conn, branch_id)
    with get_db() as conn:
        html = _daily_accounts_print_html(conn, period_month, branch_id)
    return Response(content=html, media_type="text/html; charset=utf-8")
