#!/usr/bin/env python3
"""
รายงานทบทวนส่วนลด — สุรินทร์ & อุบลฯ (Discount Review)
Branch-level discount-control review. Names NO individuals, makes NO fraud verdict.

Reads the live DB READ-ONLY (SELECT only) and writes an HTML report.
Usage: python gen_surin_ubon_report.py [--db oshinei.db] [--out ../docs/SURIN_UBON_DISCOUNT_REVIEW.html]
"""
import sqlite3, sys, os, argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rules import discount_excess, discount_policy_pct  # noqa: E402

DATE_FROM, DATE_TO = "2026-01-01", "2026-05-31"
BRANCH_IDS = {"surin": 24, "ubon": 26}
BRANCH_TH = {"surin": "สุรินทร์", "ubon": "อุบลราชธานี"}

# Reconstructed pre-discount, ex-VAT revenue base (same expression already used in the file).
REV_BASE = "(total_inc_vat-COALESCE(service_charge,0))/1.07+discount"

# Known location of the live production DB (used when the local copy is a stub / lacks tables).
# The working copy left OneDrive on 2026-07-20 (docs/MIGRATE_OFF_ONEDRIVE.md); the DB that
# stayed behind in the archived folder is STALE and must never be read as "live".
LIVE_DB = os.environ.get("OSHINEI_DB") or r"C:\Users\ASUS\Oshinei\backend\oshinei.db"


# Read-only DB URI — never write, never run a server.
def ro_uri(db_path):
    return "file:" + os.path.abspath(db_path).replace("\\", "/") + "?mode=ro"


def resolve_db(db_path):
    """Pick a DB that actually contains the data. In a git worktree the checked-in
    oshinei.db can be a tiny stub; fall back to the live DB path in that case.
    Read-only throughout — never writes."""
    def has_data(p):
        if not (os.path.exists(p) and os.path.getsize(p) > 1_000_000):
            return False
        try:
            c = sqlite3.connect(ro_uri(p), uri=True)
            ok = c.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' "
                "AND name IN ('sales_transaction','coupon_series')").fetchone()[0] == 2
            c.close()
            return ok
        except sqlite3.Error:
            return False
    if has_data(db_path):
        return db_path
    if os.path.abspath(db_path) != os.path.abspath(LIVE_DB) and has_data(LIVE_DB):
        print(f"ℹ️  ใช้ฐานข้อมูลจริง (live DB) แทน: {LIVE_DB}")
        return LIVE_DB
    return db_path

# ── DB (READ-ONLY) ───────────────────────────────────────────────────────────
W = f"substr(sold_at,1,10) BETWEEN '{DATE_FROM}' AND '{DATE_TO}'"


def load_db(db_path):
    con = sqlite3.connect(ro_uri(db_path), uri=True)
    con.row_factory = sqlite3.Row
    data = {}

    # Neutral chain-wide context figure (shown for reference only, NOT used as a ceiling).
    data["chain_disc_pct"] = con.execute(
        f"SELECT round(100.0*sum(discount)/nullif(sum({REV_BASE}),0),2) FROM sales_transaction WHERE {W}"
    ).fetchone()[0] or 0
    # The owner's policy ceiling (Settings → เพดานส่วนลด, default 2.5%): each branch's
    # ฿ beyond it is the one figure that turns "high" into an amount to recover.
    data["policy_pct"] = discount_policy_pct(con)

    for branch, bid in BRANCH_IDS.items():
        b = {}
        s = con.execute(
            f"SELECT count(*) c, round(sum(discount)) disc FROM sales_transaction "
            f"WHERE branch_id=? AND {W} AND discount>0", (bid,)).fetchone()
        tot = con.execute(
            f"SELECT count(*) c, round(sum({REV_BASE})) rev FROM sales_transaction "
            f"WHERE branch_id=? AND {W}", (bid,)).fetchone()
        b["disc_bills"] = s["c"]
        b["disc_total"] = s["disc"] or 0
        b["total_bills"] = tot["c"]
        b["total_rev"] = tot["rev"] or 0
        b["disc_pct"] = round(100 * b["disc_total"] / b["total_rev"], 2) if b["total_rev"] else 0
        b["excess_baht"] = discount_excess(b["disc_total"], b["total_rev"], data["policy_pct"])

        # ── Discount-channel breakdown: coupon-series vs has-reason vs unexplained ──
        coup = con.execute(
            f"SELECT count(*) c, round(sum(discount)) d FROM sales_transaction "
            f"WHERE branch_id=? AND {W} AND discount>0 AND COALESCE(coupon_series_id,0)>0", (bid,)).fetchone()
        reason = con.execute(
            f"SELECT count(*) c, round(sum(discount)) d FROM sales_transaction "
            f"WHERE branch_id=? AND {W} AND discount>0 "
            f"AND COALESCE(coupon_series_id,0)=0 AND COALESCE(discount_reason_id,0)>0", (bid,)).fetchone()
        unexp = con.execute(
            f"SELECT count(*) c, round(sum(discount)) d FROM sales_transaction "
            f"WHERE branch_id=? AND {W} AND discount>0 "
            f"AND COALESCE(coupon_series_id,0)=0 AND COALESCE(discount_reason_id,0)=0", (bid,)).fetchone()
        b["ch_coupon"] = {"n": coup["c"], "d": coup["d"] or 0}
        b["ch_reason"] = {"n": reason["c"], "d": reason["d"] or 0}
        b["ch_unexp"] = {"n": unexp["c"], "d": unexp["d"] or 0}

        # Top coupon series BY NAME (join lookup table)
        b["top_series"] = [
            (r["id"], r["name"], r["code"], r["n"], r["d"]) for r in con.execute(
                f"SELECT t.coupon_series_id id, cs.name, cs.code, count(*) n, round(sum(t.discount)) d "
                f"FROM sales_transaction t LEFT JOIN coupon_series cs ON cs.coupon_series_id=t.coupon_series_id "
                f"WHERE t.branch_id=? AND {W} AND t.discount>0 AND COALESCE(t.coupon_series_id,0)>0 "
                f"GROUP BY t.coupon_series_id ORDER BY d DESC LIMIT 15", (bid,))]

        # ── Branch-level analytics (no per-person verdict) ──
        b["by_month"] = [(r["mo"], r["n"], r["disc"], r["rev"]) for r in con.execute(
            f"SELECT strftime('%Y-%m',sold_at) mo, count(*) n, round(sum(discount)) disc, "
            f"round(sum({REV_BASE})) rev FROM sales_transaction "
            f"WHERE branch_id=? AND {W} AND discount>0 GROUP BY mo ORDER BY mo", (bid,))]

        # Full monthly revenue base (for % of revenue by month — includes non-discount bills)
        b["month_rev"] = {r["mo"]: r["rev"] for r in con.execute(
            f"SELECT strftime('%Y-%m',sold_at) mo, round(sum({REV_BASE})) rev FROM sales_transaction "
            f"WHERE branch_id=? AND {W} GROUP BY mo", (bid,))}

        b["by_dow"] = [(r[0], r[1], r[2]) for r in con.execute(
            f"SELECT CASE cast(strftime('%w',sold_at) as int) WHEN 0 THEN 'อา.' WHEN 1 THEN 'จ.' "
            f"WHEN 2 THEN 'อ.' WHEN 3 THEN 'พ.' WHEN 4 THEN 'พฤ.' WHEN 5 THEN 'ศ.' WHEN 6 THEN 'ส.' END, "
            f"count(*) n, round(sum(discount)) disc FROM sales_transaction "
            f"WHERE branch_id=? AND {W} AND discount>0 GROUP BY strftime('%w',sold_at) "
            f"ORDER BY strftime('%w',sold_at)", (bid,))]

        b["by_hour"] = [(r[0], r[1], r[2]) for r in con.execute(
            f"SELECT substr(sold_at,12,2) hr, count(*) n, round(sum(discount)) disc "
            f"FROM sales_transaction WHERE branch_id=? AND {W} AND discount>0 GROUP BY hr ORDER BY hr", (bid,))]

        # Discount-amount buckets
        b["buckets"] = [(r[0], r[1], r[2]) for r in con.execute(
            f"SELECT CASE "
            f"WHEN discount<100 THEN '< ฿100' WHEN discount<300 THEN '฿100–299' "
            f"WHEN discount<500 THEN '฿300–499' WHEN discount<1000 THEN '฿500–999' "
            f"WHEN discount<2000 THEN '฿1,000–1,999' ELSE '≥ ฿2,000' END bucket, "
            f"count(*) n, round(sum(discount)) disc FROM sales_transaction "
            f"WHERE branch_id=? AND {W} AND discount>0 "
            f"GROUP BY bucket ORDER BY min(discount)", (bid,))]

        # Largest discount bills (date, time, ฿, % of bill, covers, POS login, channel)
        b["top_bills"] = [
            (r["d"], r["t"], r["disc"], r["pct"], r["cust"], r["cashier"], r["cs_name"], r["reason_id"])
            for r in con.execute(
                f"SELECT substr(sold_at,1,10) d, substr(sold_at,12,5) t, round(discount) disc, "
                f"round(100.0*discount/({REV_BASE})) pct, customer_count cust, cashier, "
                f"cs.name cs_name, discount_reason_id reason_id "
                f"FROM sales_transaction t LEFT JOIN coupon_series cs ON cs.coupon_series_id=t.coupon_series_id "
                f"WHERE branch_id=? AND {W} AND discount>0 ORDER BY discount DESC LIMIT 25", (bid,))]

        # POS logins used (shared accounts) — neutral list, no attribution
        b["logins"] = [r["cashier"] for r in con.execute(
            f"SELECT DISTINCT cashier FROM sales_transaction "
            f"WHERE branch_id=? AND {W} AND discount>0 AND cashier IS NOT NULL ORDER BY cashier", (bid,))]

        data[branch] = b

    con.close()
    return data


# ── HTML helpers (preserved from original) ───────────────────────────────────
def fmt(n, pre="฿"):
    if n is None:
        return "-"
    n = float(n)
    if abs(n) >= 1_000_000:
        return f"{pre}{n/1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"{pre}{n:,.0f}"
    return f"{pre}{n:.0f}"


def badge(lbl, color="#1f3a6b"):
    return (f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;'
            f'font-size:11px;font-weight:700">{lbl}</span>')


def bar_row(label, val, max_val, color="#1f3a6b", val_fmt=None):
    pct = min(100, 100 * val / max_val) if max_val > 0 else 0
    vstr = val_fmt if val_fmt else fmt(val)
    return (f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">'
            f'<div style="width:82px;font-size:12px;text-align:right;flex-shrink:0">{label}</div>'
            f'<div style="flex:1;background:#e5e7eb;border-radius:3px;height:18px;overflow:hidden">'
            f'<div style="width:{pct:.0f}%;background:{color};height:100%;border-radius:3px"></div></div>'
            f'<div style="width:66px;font-size:12px;font-weight:600">{vstr}</div></div>')


MONTH_TH = {"2026-01": "มกราคม", "2026-02": "กุมภาพันธ์", "2026-03": "มีนาคม",
            "2026-04": "เมษายน", "2026-05": "พฤษภาคม"}
MONTH_SHORT = {"2026-01": "ม.ค.", "2026-02": "ก.พ.", "2026-03": "มี.ค.",
               "2026-04": "เม.ย.", "2026-05": "พ.ค."}

# ── CSS (preserved, re-toned to a neutral teal review palette) ───────────────
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Sarabun',sans-serif;font-size:13px;color:#1e293b;background:#e2e8f0;print-color-adjust:exact;-webkit-print-color-adjust:exact}
:root{--teal:#1f3a6b;--rd:#dc2626;--am:#d97706;--em:#16a34a}
.cover{width:100%;min-height:100vh;background:#134e4a;display:flex;flex-direction:column;justify-content:center;align-items:flex-start;padding:70px 80px;color:#fff;page-break-after:always}
.cover-tag{background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.3);padding:5px 14px;border-radius:4px;font-size:11px;letter-spacing:2px;text-transform:uppercase;margin-bottom:28px;display:inline-block}
.cover-title{font-size:46px;font-weight:800;line-height:1.12;margin-bottom:14px}
.cover-sub{font-size:19px;font-weight:300;color:rgba(255,255,255,.85);margin-bottom:40px}
.cover-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:20px;margin-bottom:36px;width:100%}
.ckpi{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);border-radius:4px;padding:20px}
.ckpi-val{font-size:28px;font-weight:800;color:#fbbf24}
.ckpi-lbl{font-size:12px;color:rgba(255,255,255,.75);margin-top:4px}
.cover-note{display:flex;align-items:center;gap:8px;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.25);padding:8px 14px;border-radius:4px;font-size:13px;margin-top:8px}
.cover-foot{font-size:11px;color:rgba(255,255,255,.6);border-top:1px solid rgba(255,255,255,.15);padding-top:20px;width:100%;display:flex;justify-content:space-between;margin-top:36px}
.page{width:210mm;min-height:297mm;margin:0 auto 24px;background:#fff;box-shadow:0 4px 24px rgba(0,0,0,.1);page-break-after:always}
.page-last{page-break-after:auto}
.sec-hdr{background:var(--teal);color:#fff;padding:12px 28px;display:flex;justify-content:space-between;align-items:center}
.sec-hdr-title{font-size:15px;font-weight:700}
.sec-hdr-brand{font-size:10px;opacity:.75;letter-spacing:1px}
.sec-body{padding:24px 28px}
.sec-label{font-size:10px;font-weight:700;color:var(--teal);letter-spacing:2px;text-transform:uppercase;margin-bottom:3px}
.sec-title{font-size:20px;font-weight:800;margin-bottom:6px}
.sec-desc{font-size:12px;color:#64748b;margin-bottom:18px}
.kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}
.kpi3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:18px}
.kpi{background:#f8fafc;border:1px solid #e2e8f0;border-radius:4px;padding:14px}
.kpi-val{font-size:24px;font-weight:800;color:var(--teal)}
.kpi-val.red{color:var(--rd)}
.kpi-val.am{color:var(--am)}
.kpi-val.em{color:var(--em)}
.kpi-lbl{font-size:11px;color:#64748b;margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:16px}
th{background:#f1f5f9;color:#374151;font-weight:700;padding:8px 10px;text-align:left;border-bottom:2px solid #e2e8f0;font-size:11px}
td{padding:7px 10px;border-bottom:1px solid #f1f5f9;vertical-align:middle}
.num{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.center{text-align:center}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:16px}
.alert{border-radius:4px;padding:12px 16px;margin-bottom:14px;border-left:4px solid;font-size:12px;line-height:1.7}
.alert-red{background:#fef2f2;border-color:#dc2626;color:#991b1b}
.alert-am{background:#fffbeb;border-color:#d97706;color:#92400e}
.alert-em{background:#f0fdf4;border-color:#16a34a;color:#14532d}
.alert-blue{background:#eff6ff;border-color:#3b82f6;color:#1e40af}
.alert-teal{background:#f0fdfa;border-color:#1f3a6b;color:#115e59}
.toc-item{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px dashed #e2e8f0;font-size:12px}
@media print{body{background:#fff}.page{box-shadow:none;margin:0}.cover{page-break-after:always}}
@page{margin:0;size:A4}
"""


def page_hdr(title):
    return (f'<div class="page"><div class="sec-hdr"><span class="sec-hdr-title">{title}</span>'
            f'<span class="sec-hdr-brand">OSHINEI · DISCOUNT REVIEW 2026</span></div><div class="sec-body">')


# ── REPORT BUILDER ───────────────────────────────────────────────────────────
def generate(db_data):
    sd = db_data["surin"]
    ud = db_data["ubon"]
    chain = db_data["chain_disc_pct"]
    policy = db_data["policy_pct"]
    now = datetime.now().strftime("%d %b %Y")
    months = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05"]

    parts = []

    def add(h):
        parts.append(h)

    # ── HEAD ────────────────────────────────────────────────────────────────
    add(f"""<!DOCTYPE html><html lang="th"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>รายงานทบทวนส่วนลด — สุรินทร์ &amp; อุบลฯ (Discount Review)</title>
<link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@300;400;600;700;800&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>""")

    # ── COVER ────────────────────────────────────────────────────────────────
    add(f"""<div class="cover">
<span class="cover-tag">DISCOUNT REVIEW · INTERNAL · ทบทวนการควบคุมส่วนลด</span>
<div class="cover-title">รายงานทบทวนส่วนลด<br>สุรินทร์ &amp; อุบลราชธานี</div>
<div class="cover-sub">ทบทวนที่มาของส่วนลดและการควบคุมกระบวนการ · ม.ค.–พ.ค. 2569 (ระดับสาขา)</div>
<div class="cover-kpis">
  <div class="ckpi"><div class="ckpi-val">{fmt(sd['disc_total'])}</div><div class="ckpi-lbl">ส่วนลดสุรินทร์ (5 เดือน) · {sd['disc_pct']}% ของรายได้</div></div>
  <div class="ckpi"><div class="ckpi-val">{fmt(sd['ch_unexp']['d'])}</div><div class="ckpi-lbl">สุรินทร์: ส่วนลดที่ยังไม่มีที่มา ({sd['ch_unexp']['n']:,} บิล)</div></div>
  <div class="ckpi"><div class="ckpi-val">{fmt(ud['disc_total'])}</div><div class="ckpi-lbl">ส่วนลดอุบลฯ (5 เดือน) · {ud['disc_pct']}% ของรายได้</div></div>
  <div class="ckpi"><div class="ckpi-val">100%</div><div class="ckpi-lbl">อุบลฯ: ส่วนลดที่ผูกกับคูปองโปรฯ ที่นิยามไว้</div></div>
</div>
<div class="cover-note">อุบลฯ: ส่วนลดทั้งหมดเป็นคูปองโปรโมชันที่นิยามไว้ในระบบ — เป็นประเด็นเชิงนโยบายมาร์จิน/โปรโมชัน ไม่ใช่การทุจริต</div>
<div class="cover-note">สุรินทร์: ส่วนลด {fmt(sd['ch_unexp']['d'])} ({sd['ch_unexp']['n']:,} บิล) ไม่มีเหตุผลและไม่ผูกคูปอง — เป็นช่องว่างของ<strong>การควบคุมการอนุมัติ</strong>ที่ควรทบทวน</div>
<div class="cover-foot">
  <span>OSHINEI ENTERPRISE · Discount Control Review</span>
  <span>ข้อมูล {DATE_FROM} – {DATE_TO} · สร้าง {now}</span>
  <span>INTERNAL USE</span>
</div></div>""")

    # ── TOC PAGE ──────────────────────────────────────────────────────────────
    add(page_hdr("สารบัญ (Table of Contents)"))
    add('<div class="sec-desc">รายงานนี้ทบทวนส่วนลดในระดับสาขา ไม่ระบุตัวบุคคล และไม่มีการชี้ว่าผู้ใดกระทำผิด</div>')
    toc_items = [
        ("1", "สรุปผู้บริหาร (Executive Summary)", "2"),
        ("2", "ที่มาของส่วนลด — จำแนกช่องทาง (Discount Channels)", "3"),
        ("3", "สุรินทร์ — โปรโมชันตามชื่อคูปอง & ส่วนลดที่ยังไม่มีที่มา", "4"),
        ("4", "สุรินทร์ — ส่วนลดตามเดือน / วัน / ชั่วโมง / ช่วงมูลค่า", "5"),
        ("5", "สุรินทร์ — บิลส่วนลดสูงสุด", "6"),
        ("6", "อุบลฯ — โปรโมชันตามชื่อคูปอง (100% ผูกคูปอง)", "7"),
        ("7", "อุบลฯ — ส่วนลดตามเดือน / วัน / ชั่วโมง / ช่วงมูลค่า", "8"),
        ("8", "อุบลฯ — บิลส่วนลดสูงสุด", "9"),
        ("9", "ข้อเสนอแนะด้านการควบคุม (Control Recommendations)", "10"),
    ]
    toc_html = '<div style="background:#f8fafc;border-radius:4px;padding:16px">'
    for num, title, pg in toc_items:
        toc_html += (f'<div class="toc-item"><span style="font-weight:700;display:flex;gap:8px">'
                     f'<span style="color:var(--teal);font-weight:700;min-width:30px">{num}</span>'
                     f'<span>{title}</span></span><span style="color:#94a3b8">{pg}</span></div>')
    toc_html += '</div>'
    add(toc_html)
    add('</div></div>')

    # ── EXEC SUMMARY ─────────────────────────────────────────────────────────
    add(page_hdr("สรุปผู้บริหาร (Executive Summary)"))
    add(f"""
<div class="alert alert-teal"><strong>ภาพรวม:</strong> รายงานนี้ทบทวน<em>ที่มา</em>ของส่วนลดในสาขาสุรินทร์และอุบลฯ (ม.ค.–พ.ค. 2569)
โดยจำแนกทุกบิลออกเป็น 3 ช่องทาง: (1) คูปองโปรโมชันที่นิยามไว้ในระบบ (2) มีรหัสเหตุผลกำกับ และ (3) ยังไม่มีที่มา (ไม่มีทั้งคูปองและเหตุผล)
การวิเคราะห์อยู่ในระดับสาขาเท่านั้น ไม่พาดพิงตัวบุคคล</div>
<div class="kpi-grid">
  <div class="kpi"><div class="kpi-val">{sd['disc_pct']}%</div><div class="kpi-lbl">% ส่วนลด สุรินทร์ (บริบทเชน {chain}% · เพดานนโยบาย {policy}%)</div></div>
  <div class="kpi"><div class="kpi-val">{ud['disc_pct']}%</div><div class="kpi-lbl">% ส่วนลด อุบลฯ (บริบทเชน {chain}% · เพดานนโยบาย {policy}%)</div></div>
  <div class="kpi"><div class="kpi-val am">{fmt(sd['ch_unexp']['d'])}</div><div class="kpi-lbl">สุรินทร์: ส่วนลดที่ยังไม่มีที่มา ({sd['ch_unexp']['n']:,} บิล)</div></div>
  <div class="kpi"><div class="kpi-val em">100%</div><div class="kpi-lbl">อุบลฯ: ผูกคูปองโปรฯ ({ud['ch_coupon']['n']:,} บิล)</div></div>
</div>
<div class="alert alert-teal"><strong>เทียบเพดานนโยบาย {policy}% ของยอดขายก่อนลด:</strong> สุรินทร์ให้ส่วนลดเกินเพดาน <strong>{fmt(sd['excess_baht'])}</strong> · อุบลฯ <strong>{fmt(ud['excess_baht'])}</strong>
(ช่วง ม.ค.–พ.ค. 2569 — ตัวเลขนี้คือส่วนที่เกินนโยบาย ไม่ใช่ส่วนลดทั้งหมด; เพดานตั้งได้ที่ Settings ของแดชบอร์ด)</div>
<div class="two-col">
<div>
<strong style="color:#d97706;font-size:14px">สาขาสุรินทร์ — พบช่องว่างการควบคุม (ไม่ใช่ข้อกล่าวหา)</strong>
<ul style="margin-top:8px;padding-left:18px;line-height:2.0;font-size:12px">
<li>ส่วนลดรวม <strong>{fmt(sd['disc_total'])}</strong> จาก {sd['disc_bills']:,} บิล</li>
<li>ผูกคูปองโปรฯ ที่นิยามไว้: {sd['ch_coupon']['n']:,} บิล ({fmt(sd['ch_coupon']['d'])})</li>
<li>มีรหัสเหตุผลกำกับ: {sd['ch_reason']['n']:,} บิล ({fmt(sd['ch_reason']['d'])})</li>
<li><strong>ยังไม่มีที่มา: {sd['ch_unexp']['n']:,} บิล ({fmt(sd['ch_unexp']['d'])})</strong> — ไม่มีทั้งคูปองและเหตุผล</li>
<li>ข้อเสนอ: ทบทวน<strong>ขั้นตอนอนุมัติส่วนลด</strong> (ใครอนุมัติ, บังคับระบุรหัสเหตุผล) — เป็นความเสี่ยงเชิงกระบวนการ</li>
</ul>
</div>
<div>
<strong style="color:#1f3a6b;font-size:14px">สาขาอุบลราชธานี — เป็นเรื่องนโยบายโปรโมชัน ไม่ใช่การทุจริต</strong>
<ul style="margin-top:8px;padding-left:18px;line-height:2.0;font-size:12px">
<li>ส่วนลดรวม <strong>{fmt(ud['disc_total'])}</strong> จาก {ud['disc_bills']:,} บิล</li>
<li><strong>ทั้งหมด (100%)</strong> ผูกกับคูปองโปรโมชันที่นิยามไว้ในระบบ</li>
<li>ตัวอย่างคูปอง: {"; ".join(f"{n}" for _, n, _, _, _ in ud['top_series'][:3] if n)}</li>
<li>ไม่มีบิลที่ "ยังไม่มีที่มา" เลย (0 บิล)</li>
<li>ประเด็นที่ควรพิจารณาคือ <strong>มาร์จิน/ความคุ้มค่าของโปรโมชัน</strong> ไม่ใช่การควบคุมภายใน</li>
</ul>
</div></div>
<div class="alert alert-blue"><strong>หมายเหตุด้านบัญชี POS login:</strong> ค่า cashier ในระบบ Easy POS ของทั้งสองสาขาเป็น
<strong>บัญชีเข้าใช้แบบใช้ร่วมกัน</strong> (เช่น Manager, admin, Take Order) จึงไม่สามารถใช้ระบุตัวบุคคลได้
รายงานนี้จึงไม่นำมาสรุปความรับผิดของบุคคลใด</div>""")
    add('</div></div>')

    # ── DISCOUNT CHANNELS (both branches) ────────────────────────────────────
    add(page_hdr("ที่มาของส่วนลด — จำแนกช่องทาง (Discount Channels)"))
    add('<div class="sec-label">DISCOUNT CHANNELS</div>')
    add('<div class="sec-title" style="font-size:17px">ส่วนลดแยกตามที่มา: คูปองโปรฯ · มีรหัสเหตุผล · ยังไม่มีที่มา</div>')
    add('<div class="sec-desc">ทุกบิลถูกจำแนกจากคอลัมน์ coupon_series_id และ discount_reason_id ในฐานข้อมูล POS โดยตรง</div>')

    def channel_block(name, b):
        tot = b["disc_total"] or 1
        rows = [
            ("คูปองโปรโมชันที่นิยามไว้", b["ch_coupon"], "#16a34a", "ถูกต้อง — เป็นโปรฯ ที่ตั้งค่าในระบบ"),
            ("มีรหัสเหตุผลกำกับ", b["ch_reason"], "#1f3a6b", "มีการระบุเหตุผลไว้"),
            ("ยังไม่มีที่มา (ไม่มีคูปอง/เหตุผล)", b["ch_unexp"], "#d97706", "ควรทบทวนการควบคุมการอนุมัติ"),
        ]
        h = (f'<strong>{name}</strong> — ส่วนลดรวม {fmt(b["disc_total"])} ({b["disc_bills"]:,} บิล)'
             f'<table style="margin-top:8px"><thead><tr><th>ช่องทาง</th><th class="num">บิล</th>'
             f'<th class="num">ส่วนลด</th><th class="num">% ของส่วนลด</th><th>หมายเหตุ</th></tr></thead><tbody>')
        for lbl, ch, col, note in rows:
            pct = round(100 * ch["d"] / tot, 1)
            h += (f'<tr><td style="color:{col};font-weight:700">{lbl}</td>'
                  f'<td class="num">{ch["n"]:,}</td>'
                  f'<td class="num" style="color:{col};font-weight:700">{fmt(ch["d"])}</td>'
                  f'<td class="num">{pct}%</td>'
                  f'<td style="font-size:11px;color:#64748b">{note}</td></tr>')
        h += '</tbody></table>'
        return h

    add('<div style="margin-bottom:8px">')
    add(channel_block("สุรินทร์", sd))
    add('</div><div>')
    add(channel_block("อุบลราชธานี", ud))
    add('</div>')
    add(f"""<div class="alert alert-teal">
<strong>อ่านผล:</strong> ที่อุบลฯ ส่วนลด <strong>100% ผูกกับคูปองโปรโมชัน</strong>ที่นิยามไว้ในระบบ (เช่น ส่วนลดตามอายุ/ส่วนสูง, ส่วนลดวันเกิด, ส่วนลดผู้บริหาร/ผู้ถือหุ้น)
จึงตรวจสอบย้อนกลับได้ครบ — ประเด็นคือความเหมาะสมของ<strong>นโยบายโปรโมชัน/มาร์จิน</strong>
ส่วนที่สุรินทร์ มีส่วนลด <strong>{fmt(sd['ch_unexp']['d'])} ({sd['ch_unexp']['n']:,} บิล)</strong> ที่ไม่มีทั้งคูปองและรหัสเหตุผล
จึงตรวจสอบย้อนกลับไม่ได้ — เป็น<strong>ช่องว่างของการควบคุม</strong>ที่ควรอุดด้วยการบังคับระบุเหตุผลและกำหนดผู้อนุมัติ</div>""")
    add('</div></div>')

    # ── PER-BRANCH SECTIONS ──────────────────────────────────────────────────
    def series_page(bkey):
        b = db_data[bkey]
        bn = BRANCH_TH[bkey]
        add(page_hdr(f"{bn} — โปรโมชันตามชื่อคูปอง & ส่วนลดที่ยังไม่มีที่มา"))
        add('<div class="sec-label">COUPON SERIES BY NAME</div>')
        add(f'<div class="sec-title" style="font-size:17px">{bn} — คูปองโปรโมชันที่ใช้จริง (เรียงตามมูลค่า)</div>')
        coup_tot = b["ch_coupon"]["d"] or 1
        add('<table><thead><tr><th>รหัส</th><th>ชื่อคูปอง / โปรโมชัน</th><th class="num">บิล</th>'
            '<th class="num">ส่วนลดรวม</th><th class="num">% ของคูปอง</th></tr></thead><tbody>')
        for sid, sname, scode, n, d in b["top_series"]:
            label = sname or (f"(code {scode})" if scode else f"(series #{sid})")
            pct = round(100 * (d or 0) / coup_tot, 1)
            add(f'<tr><td style="font-family:monospace;color:#64748b">#{sid}</td>'
                f'<td>{label}</td><td class="num">{n:,}</td>'
                f'<td class="num" style="color:#1f3a6b;font-weight:700">{fmt(d)}</td>'
                f'<td class="num">{pct}%</td></tr>')
        add('</tbody></table>')
        # Unexplained callout
        if b["ch_unexp"]["n"] > 0:
            add(f"""<div class="alert alert-am">
<strong>ส่วนลดที่ยังไม่มีที่มา:</strong> {b['ch_unexp']['n']:,} บิล รวม {fmt(b['ch_unexp']['d'])}
({round(100*b['ch_unexp']['d']/(b['disc_total'] or 1),1)}% ของส่วนลดสาขา) ไม่มีทั้งคูปองและรหัสเหตุผลในระบบ
จึงไม่สามารถระบุได้ว่าเป็นโปรโมชันประเภทใด — แนะนำให้บังคับระบุรหัสเหตุผลก่อนกดส่วนลด และกำหนดผู้มีสิทธิ์อนุมัติให้ชัดเจน</div>""")
        else:
            add("""<div class="alert alert-em">
<strong>ไม่มีส่วนลดที่ยังไม่มีที่มา:</strong> ทุกบิลผูกกับคูปองโปรโมชันที่นิยามไว้ — ตรวจสอบย้อนกลับได้ครบถ้วน
ประเด็นที่ควรพิจารณาคือความเหมาะสมของนโยบายโปรโมชันและผลต่อมาร์จิน</div>""")
        add('</div></div>')

    def analytics_page(bkey):
        b = db_data[bkey]
        bn = BRANCH_TH[bkey]
        add(page_hdr(f"{bn} — ส่วนลดตามเดือน / วัน / ชั่วโมง / ช่วงมูลค่า"))
        # Monthly table with % of revenue
        add('<strong>ส่วนลดรายเดือน &amp; % ของรายได้ (ex-VAT ก่อนหักส่วนลด)</strong>')
        add('<table style="margin-top:8px"><thead><tr><th>เดือน</th><th class="num">บิลส่วนลด</th>'
            '<th class="num">ส่วนลดรวม</th><th class="num">รายได้ (ฐาน)</th><th class="num">% ส่วนลด</th></tr></thead><tbody>')
        m_disc = {r[0]: (r[1], r[2]) for r in b["by_month"]}
        for mo in months:
            n, d = m_disc.get(mo, (0, 0))
            rev = b["month_rev"].get(mo, 0) or 0
            pct = round(100 * (d or 0) / rev, 2) if rev else 0
            add(f'<tr><td>{MONTH_TH.get(mo, mo)}</td><td class="num">{n:,}</td>'
                f'<td class="num" style="font-weight:700">{fmt(d)}</td>'
                f'<td class="num">{fmt(rev)}</td><td class="num">{pct}%</td></tr>')
        add('</tbody></table>')
        # DOW + Hour bars
        max_dow = max((r[2] for r in b["by_dow"]), default=1)
        max_hr = max((r[2] for r in b["by_hour"]), default=1)
        add('<div class="two-col"><div><strong>ส่วนลดตามวันในสัปดาห์</strong><div style="height:8px"></div>')
        for dow, n, disc in b["by_dow"]:
            add(bar_row(dow, disc, max_dow, "#1f3a6b"))
        add('</div><div><strong>ส่วนลดตามช่วงเวลา (ชั่วโมง)</strong><div style="height:8px"></div>')
        for hr, n, disc in b["by_hour"]:
            add(bar_row(f"{hr}:00", disc, max_hr, "#0e7490"))
        add('</div></div>')
        # Amount buckets
        add('<strong>ส่วนลดตามช่วงมูลค่าต่อบิล</strong>')
        add('<table style="margin-top:8px"><thead><tr><th>ช่วงมูลค่า</th><th class="num">บิล</th>'
            '<th class="num">ส่วนลดรวม</th><th class="num">% ของส่วนลด</th></tr></thead><tbody>')
        btot = b["disc_total"] or 1
        for bucket, n, disc in b["buckets"]:
            add(f'<tr><td>{bucket}</td><td class="num">{n:,}</td>'
                f'<td class="num" style="font-weight:700">{fmt(disc)}</td>'
                f'<td class="num">{round(100*(disc or 0)/btot,1)}%</td></tr>')
        add('</tbody></table>')
        add('</div></div>')

    def top_bills_page(bkey):
        b = db_data[bkey]
        bn = BRANCH_TH[bkey]
        add(page_hdr(f"{bn} — บิลส่วนลดสูงสุด (Top 25)"))
        add('<div class="sec-desc">คอลัมน์ "POS login" เป็นบัญชีใช้ร่วมกัน แสดงเพื่อบริบทเท่านั้น ไม่ใช้ระบุตัวบุคคล · '
            'คอลัมน์ "ที่มา" มาจากคูปอง/รหัสเหตุผลในระบบ</div>')
        add('<table><thead><tr><th>วันที่</th><th>เวลา</th><th class="num">ส่วนลด</th>'
            '<th class="num">% ของบิล</th><th class="num">คน</th><th>POS login (บัญชีร่วม)</th>'
            '<th>ที่มา</th></tr></thead><tbody>')
        for d, t, disc, pct, cust, cashier, cs_name, reason_id in b["top_bills"]:
            if cs_name:
                origin = f'<span style="color:#16a34a">คูปอง: {cs_name}</span>'
            elif reason_id:
                origin = f'<span style="color:#1f3a6b">รหัสเหตุผล #{reason_id}</span>'
            else:
                origin = '<span style="color:#d97706">ยังไม่มีที่มา</span>'
            add(f'<tr><td style="font-size:11px">{d}</td><td style="font-size:11px">{t}</td>'
                f'<td class="num" style="font-weight:700">{fmt(disc)}</td>'
                f'<td class="num">{pct}%</td><td class="num">{cust or "-"}</td>'
                f'<td style="font-size:11px;color:#64748b"><code>{cashier or "-"}</code></td>'
                f'<td style="font-size:11px">{origin}</td></tr>')
        add('</tbody></table>')
        add('</div></div>')

    # Surin
    series_page("surin")
    analytics_page("surin")
    top_bills_page("surin")
    # Ubon
    series_page("ubon")
    analytics_page("ubon")
    top_bills_page("ubon")

    # ── CONTROL RECOMMENDATIONS ──────────────────────────────────────────────
    add(page_hdr("ข้อเสนอแนะด้านการควบคุม (Control Recommendations)"))
    add('<div class="sec-label">CONTROL RECOMMENDATIONS</div>')
    add('<div class="sec-title" style="font-size:17px">ข้อเสนอแนะเชิงกระบวนการ (ไม่พาดพิงบุคคล)</div>')
    add("""<div class="alert alert-teal">
ข้อเสนอต่อไปนี้มุ่งที่<strong>กระบวนการควบคุมส่วนลด</strong> เพื่อให้ทุกส่วนลดตรวจสอบย้อนกลับได้
ไม่ได้ตั้งอยู่บนสมมติฐานว่ามีผู้ใดกระทำผิด</div>""")
    add("""<table><thead><tr><th>#</th><th>ข้อเสนอ</th><th>เป้าหมาย</th><th class="center">ระดับ</th></tr></thead><tbody>
<tr><td>1</td><td><strong>บังคับระบุรหัสเหตุผลก่อนกดส่วนลด</strong> ในทุกสาขา (โดยเฉพาะสุรินทร์)</td>
<td>ปิดช่องว่างส่วนลด "ยังไม่มีที่มา"</td><td class="center">""" + badge("สูง", "#d97706") + """</td></tr>
<tr><td>2</td><td><strong>กำหนดสิทธิ์อนุมัติส่วนลด</strong> ให้ชัดเจน — ส่วนลดเกินเกณฑ์ที่ตั้งไว้ต้องผ่านหัวหน้า</td>
<td>ควบคุมการอนุมัติส่วนลดมูลค่าสูง</td><td class="center">""" + badge("สูง", "#d97706") + """</td></tr>
<tr><td>3</td><td><strong>ทบทวนบัญชี POS login ที่ใช้ร่วมกัน</strong> (Manager/admin/Take Order) — พิจารณาบัญชีรายบุคคล</td>
<td>ให้มี audit trail ที่ระบุผู้ทำรายการได้</td><td class="center">""" + badge("กลาง", "#1f3a6b") + """</td></tr>
<tr><td>4</td><td><strong>ทบทวนนโยบายโปรโมชัน/มาร์จิน</strong> ของคูปองส่วนลดที่มีมูลค่าสูง (เช่น ส่วนลดตามอายุ/ส่วนสูง, วันเกิด)</td>
<td>ประเมินความคุ้มค่าของโปรโมชัน (โดยเฉพาะอุบลฯ)</td><td class="center">""" + badge("กลาง", "#1f3a6b") + """</td></tr>
<tr><td>5</td><td><strong>รายงานส่วนลดรายเดือนต่อผู้บริหาร</strong> พร้อมสัดส่วนช่องทาง (คูปอง/เหตุผล/ยังไม่มีที่มา)</td>
<td>เฝ้าระวังแนวโน้มต่อเนื่อง</td><td class="center">""" + badge("กลาง", "#1f3a6b") + """</td></tr>
</tbody></table>""")
    add(f"""<div class="alert alert-am" style="margin-top:6px">
<strong>ประเด็นสำคัญที่ต้องติดตาม — สุรินทร์:</strong> ส่วนลด {fmt(sd['ch_unexp']['d'])} จาก {sd['ch_unexp']['n']:,} บิล
ที่ไม่มีทั้งคูปองและรหัสเหตุผล ควรได้รับการทบทวนกระบวนการอนุมัติเป็นลำดับแรก
โดยเน้นที่ "ใครมีสิทธิ์อนุมัติ" และ "บังคับระบุเหตุผล" มากกว่าการชี้ตัวบุคคล</div>""")
    add("""<div class="alert alert-em">
<strong>สรุปสำหรับอุบลฯ:</strong> ส่วนลดทั้งหมดผูกกับคูปองโปรโมชันที่นิยามไว้และตรวจสอบได้ครบ
จึงเป็นคำถามเชิงนโยบายมาร์จิน/โปรโมชัน ไม่ใช่ประเด็นการทุจริตหรือการควบคุมภายใน</div>""")
    add('</div></div>')

    # ── CLOSE ────────────────────────────────────────────────────────────────
    add(f'<div style="text-align:center;padding:20px;font-size:11px;color:#94a3b8">'
        f'OSHINEI ENTERPRISE · Discount Control Review · {now} · INTERNAL USE</div>')
    add('</body></html>')
    return "".join(parts)


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(os.path.dirname(__file__), "oshinei.db"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "docs",
                                                  "SURIN_UBON_DISCOUNT_REVIEW.html"))
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print(" ดึงข้อมูล DB (read-only)...")
    db_data = load_db(resolve_db(args.db))
    for bkey in ("surin", "ubon"):
        b = db_data[bkey]
        print(f"  {BRANCH_TH[bkey]}: {b['disc_bills']:,} บิลส่วนลด · รวม {b['disc_total']:,.0f}฿ "
              f"| คูปอง {b['ch_coupon']['n']:,}/{b['ch_coupon']['d']:,.0f}฿ "
              f"· เหตุผล {b['ch_reason']['n']:,}/{b['ch_reason']['d']:,.0f}฿ "
              f"· ยังไม่มีที่มา {b['ch_unexp']['n']:,}/{b['ch_unexp']['d']:,.0f}฿")

    print("สร้างรายงาน HTML...")
    html = generate(db_data)
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"สร้างสำเร็จ: {out} ({os.path.getsize(out):,} bytes)")
