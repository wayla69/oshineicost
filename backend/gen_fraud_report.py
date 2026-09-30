#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a self-contained HTML fraud / internal-control report for Oshinei.

Reads the local Easy-POS warehouse (oshinei.db) directly — no running server
needed — and surfaces the control-risk signals already captured by the POS:

  • Bill cancellations / voids   (pos_cancel_bill_detail)  — by user, branch,
    big-ticket single voids, repeated identical amounts, untyped reasons,
    after-hours clustering.
  • Cash over / short            (pos_cashcount_day)       — net shortage,
    worst days, chronically short branches.
  • Manual price overrides       (pos_price_change)        — who keys price
    drops, biggest reductions, branch concentration.
  • Discount leakage             (sales_transaction)       — untyped-reason
    share, heavy discounts, cashier outliers.

Every figure is a query against the same tables the dashboard uses, so the
report reconciles with the app. Output is a single print-friendly HTML file.

Usage:
    python gen_fraud_report.py [--db oshinei.db] [--from 2026-01-01] [--to 2026-05-31]
"""
import argparse, os, sqlite3
from datetime import datetime
from report_fonts import font_face_css  # KIRA UI, embedded (backend/report_fonts.py)

# ── formatting helpers ──────────────────────────────────────────────────────
def thb(n):       return "฿" + format(int(round(n or 0)), ",")
def thb_m(n):     return "฿{:,.2f}M".format((n or 0) / 1_000_000)
def num(n):       return format(int(round(n or 0)), ",")
def pct(n, d=1):  return "—" if n is None else f"{n:.{d}f}%"
def esc(s):       return (str(s) if s is not None else "—").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

SEV = {"high": ("#ef4444", "สูง"), "med": ("#f59e0b", "กลาง"), "low": ("#10b981", "ต่ำ")}
def pill(sev):
    c, t = SEV[sev]
    return f'<span class="pill" style="background:{c};color:#fff">{t}</span>'

def bar(value, vmax, color, width=150):
    w = 0 if not vmax else max(2, round(abs(value) / vmax * width))
    return f'<div class="bar" style="width:{w}px;background:{color}"></div>'


# ── main ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(os.path.dirname(__file__), "oshinei.db"))
    ap.add_argument("--from", dest="d0", default="2026-01-01")
    ap.add_argument("--to",   dest="d1", default="2026-05-31")
    ap.add_argument("--out",  default=None)
    args = ap.parse_args()
    D0, D1 = args.d0, args.d1

    con = sqlite3.connect(args.db); con.row_factory = sqlite3.Row
    q = con.execute

    brmap = {r["branch_id"]: (r["name"], r["branch_type"]) for r in q("SELECT branch_id,name,branch_type FROM branches")}
    def bname(bid): return brmap.get(bid, (f"#{bid}", ""))[0]
    def btype(bid): return brmap.get(bid, ("", ""))[1] or ""

    # period label, Thai months
    TH_MO = {1:"ม.ค.",2:"ก.พ.",3:"มี.ค.",4:"เม.ย.",5:"พ.ค.",6:"มิ.ย.",7:"ก.ค.",8:"ส.ค.",9:"ก.ย.",10:"ต.ค.",11:"พ.ย.",12:"ธ.ค."}
    m0 = int(D0[5:7]); y1,m1 = int(D1[:4]),int(D1[5:7])
    period = f"{TH_MO[m0]} – {TH_MO[m1]} {y1+543}"
    gen_date = datetime.now().strftime("%d/%m/%Y %H:%M")

    Wsold = "substr(sold_at,1,10) BETWEEN ? AND ?"
    Wsale = "substr(sale_date,1,10) BETWEEN ? AND ?"
    P = (D0, D1)

    # ── gather signals ──────────────────────────────────────────────────────
    total_bills = q(f"SELECT COUNT(*) c FROM sales_transaction WHERE {Wsold}", P).fetchone()["c"]
    total_sales = q(f"SELECT COALESCE(SUM((total_inc_vat-COALESCE(service_charge,0))/1.07),0) s FROM sales_transaction WHERE {Wsold}", P).fetchone()["s"]

    # CANCELLATIONS
    can_tot = q(f"SELECT COUNT(*) n, COALESCE(SUM(amount),0) amt, COALESCE(SUM(discount),0) disc FROM pos_cancel_bill_detail WHERE {Wsale}", P).fetchone()
    can_by_user = q(f"""SELECT COALESCE(cancel_user,'(ไม่ระบุ)') u, COUNT(*) n, ROUND(SUM(amount)) amt
        FROM pos_cancel_bill_detail WHERE {Wsale} GROUP BY u ORDER BY amt DESC""", P).fetchall()
    can_by_branch = q(f"""SELECT branch_id, COUNT(*) n, ROUND(SUM(amount)) amt
        FROM pos_cancel_bill_detail WHERE {Wsale} GROUP BY branch_id ORDER BY amt DESC""", P).fetchall()
    can_big = q(f"""SELECT branch_id, sale_date, receipt_no, cancel_user, ROUND(amount) amt, r.reason_desc
        FROM pos_cancel_bill_detail d LEFT JOIN pos_cancel_reason r ON r.reason_id=d.reason_id
        WHERE {Wsale} ORDER BY amount DESC LIMIT 12""", P).fetchall()
    can_repeat = q(f"""SELECT COALESCE(cancel_user,'(ไม่ระบุ)') u, branch_id, ROUND(amount,2) amt, COUNT(*) n, ROUND(SUM(amount)) tot
        FROM pos_cancel_bill_detail WHERE {Wsale} AND amount>0
        GROUP BY u, branch_id, amount HAVING COUNT(*) >= 25 ORDER BY n DESC LIMIT 12""", P).fetchall()
    q(f"""SELECT branch_id, COUNT(*) n, ROUND(SUM(amount)) amt
        FROM pos_cancel_bill_detail WHERE {Wsale}
        AND CAST(strftime('%H',sale_date) AS INT) >= 22 GROUP BY branch_id ORDER BY amt DESC LIMIT 6""", P).fetchall()
    can_untyped = q(f"""SELECT COUNT(*) n, ROUND(SUM(amount)) amt FROM pos_cancel_bill_detail d
        LEFT JOIN pos_cancel_reason r ON r.reason_id=d.reason_id
        WHERE {Wsale} AND (d.reason_id IS NULL OR r.reason_desc LIKE 'อื่น%')""", P).fetchone()

    # CASH OVER/SHORT
    cash_tot = q("""SELECT COUNT(*) days, ROUND(SUM(over_short)) net,
        ROUND(SUM(CASE WHEN over_short<0 THEN over_short ELSE 0 END)) short,
        ROUND(SUM(CASE WHEN over_short>0 THEN over_short ELSE 0 END)) over_
        FROM pos_cashcount_day WHERE day BETWEEN ? AND ?""", P).fetchone()
    cash_worst = q("""SELECT branch_id, day, ROUND(over_short) os, ROUND(total_sales) ts
        FROM pos_cashcount_day WHERE day BETWEEN ? AND ? ORDER BY over_short ASC LIMIT 12""", P).fetchall()
    cash_branch = q("""SELECT branch_id, COUNT(*) days,
        SUM(CASE WHEN over_short < -500 THEN 1 ELSE 0 END) short_days,
        ROUND(SUM(over_short)) net, ROUND(SUM(CASE WHEN over_short<0 THEN over_short ELSE 0 END)) short
        FROM pos_cashcount_day WHERE day BETWEEN ? AND ?
        GROUP BY branch_id HAVING short < -5000 ORDER BY short ASC""", P).fetchall()

    # PRICE OVERRIDES
    pc_w = "substr(change_date,1,10) BETWEEN ? AND ?"
    pc_tot = q(f"""SELECT COUNT(*) n, SUM(CASE WHEN new_price<default_price THEN 1 ELSE 0 END) drops,
        ROUND(SUM(CASE WHEN new_price<default_price THEN default_price-new_price ELSE 0 END)) drop_baht
        FROM pos_price_change WHERE {pc_w}""", P).fetchone()
    pc_user = q(f"""SELECT user_name u, COUNT(*) n,
        SUM(CASE WHEN new_price<default_price THEN 1 ELSE 0 END) drops
        FROM pos_price_change WHERE {pc_w} GROUP BY u ORDER BY n DESC LIMIT 10""", P).fetchall()
    pc_big = q(f"""SELECT branch_id, change_date, item_desc, default_price, new_price, user_name,
        (default_price-new_price) diff FROM pos_price_change
        WHERE {pc_w} AND new_price<default_price ORDER BY diff DESC LIMIT 10""", P).fetchall()
    pc_branch = q(f"""SELECT branch_id, COUNT(*) n FROM pos_price_change WHERE {pc_w}
        GROUP BY branch_id ORDER BY n DESC LIMIT 6""", P).fetchall()

    # DISCOUNTS
    disc_tot = q(f"""SELECT COUNT(*) bills, ROUND(SUM(discount)) amt FROM sales_transaction
        WHERE {Wsold} AND discount>0""", P).fetchone()
    disc_untyped = q(f"""SELECT COUNT(*) bills, ROUND(SUM(discount)) amt FROM sales_transaction
        WHERE {Wsold} AND discount>0 AND COALESCE(discount_reason_id,0)=0""", P).fetchone()
    # cashier discount outliers
    base = "((total_inc_vat-COALESCE(service_charge,0))/1.07 + discount)"
    crows = q(f"""SELECT cashier ca, branch_id, COUNT(*) bills, ROUND(SUM(discount)) disc,
        ROUND(100.0*SUM(discount)/NULLIF(SUM({base}),0),1) pctd
        FROM sales_transaction WHERE {Wsold} AND discount>0 AND cashier<>''
        GROUP BY cashier, branch_id HAVING SUM(discount) >= 30000""", P).fetchall()
    pcts = sorted([r["pctd"] for r in crows if r["pctd"] is not None])
    med = pcts[len(pcts)//2] if pcts else 0
    thr = max(med*2.0, 5.0)
    cash_outliers = sorted([r for r in crows if (r["pctd"] or 0) >= thr], key=lambda x:-(x["pctd"] or 0))[:10]

    # ── composite branch risk score ─────────────────────────────────────────
    # normalise each driver 0..1 across branches, weight, sum.
    drivers = {}  # bid -> dict
    for r in can_by_branch:  drivers.setdefault(r["branch_id"],{})["cancel"] = r["amt"] or 0
    for r in cash_branch:    drivers.setdefault(r["branch_id"],{})["short"]  = -(r["short"] or 0)
    for r in pc_branch:      drivers.setdefault(r["branch_id"],{})["price"]  = r["n"] or 0
    def norm(key):
        vals = [d.get(key,0) for d in drivers.values()]
        mx = max(vals) if vals else 0
        return {bid: (drivers[bid].get(key,0)/mx if mx else 0) for bid in drivers}
    nC, nS, nP = norm("cancel"), norm("short"), norm("price")
    risk = []
    for bid in drivers:
        score = 100*(0.45*nC[bid] + 0.40*nS[bid] + 0.15*nP[bid])
        risk.append((bid, score, drivers[bid]))
    risk.sort(key=lambda x:-x[1])

    # ── build executive flags (ranked) ──────────────────────────────────────
    flags = []
    if (disc_untyped["amt"] or 0) > 0 and (disc_tot["amt"] or 0) > 0:
        sh = 100.0*disc_untyped["amt"]/disc_tot["amt"]
        flags.append(("high" if sh>=80 else "med",
            f"ส่วนลดไม่ระบุเหตุผล {pct(sh)} ของส่วนลดทั้งหมด",
            f"{num(disc_untyped['bills'])} บิล มูลค่า {thb(disc_untyped['amt'])} ถูกลดโดยไม่เลือกเหตุผล — ควบคุมนโยบายส่วนลดไม่ได้และเปิดช่องให้ลดเองตามอำเภอใจ",
            disc_untyped["amt"]))
    if (cash_tot["net"] or 0) < 0:
        flags.append(("high" if cash_tot["net"]<=-300000 else "med",
            f"เงินสดขาดสุทธิ {thb(cash_tot['net'])}",
            f"จากการนับเงิน {num(cash_tot['days'])} วัน — ยอดขาด {thb(cash_tot['short'])} หักยอดเกิน {thb(cash_tot['over_'])}. เงินขาดต่อเนื่องคือสัญญาณคลาสสิกของการยักย้ายเงินสด",
            -(cash_tot["net"] or 0)))
    if (can_tot["amt"] or 0) > 0:
        rate = 100.0*can_tot["n"]/total_bills if total_bills else 0
        flags.append(("high" if (can_tot["amt"] or 0)>=3_000_000 else "med",
            f"ยกเลิก/วอยด์บิล {thb(can_tot['amt'])} ({num(can_tot['n'])} บิล)",
            f"คิดเป็น {pct(rate,2)} ของจำนวนบิลทั้งหมด — การวอยด์บิลหลังรับเงินเป็นวิธีหลักในการดึงเงินสดออก ตรวจสอบผู้ใช้ที่วอยด์มากผิดปกติ",
            can_tot["amt"]))
    if can_repeat:
        top = can_repeat[0]
        flags.append(("med",
            "พบการวอยด์ยอดเงิน 'เท่ากันเป๊ะ' ซ้ำหลายครั้งโดยผู้ใช้คนเดียว",
            f"เช่น {esc(top['u'])} วอยด์ {thb(top['amt'])} ซ้ำ {top['n']} ครั้ง ({bname(top['branch_id'])}) — รูปแบบซ้ำผิดธรรมชาติของการยกเลิกจริง",
            top["tot"]))
    if (pc_tot["drops"] or 0) > 0:
        flags.append(("med",
            f"แก้ราคาด้วยมือ (ลดราคา) {num(pc_tot['drops'])} ครั้ง",
            f"มูลค่าส่วนต่างที่กดลด {thb(pc_tot['drop_baht'])} — การ override ราคาที่หน้าจอควรจำกัดสิทธิ์และมีเหตุผลกำกับ",
            pc_tot["drop_baht"]))
    flags.sort(key=lambda x:-x[3])

    total_exposure = (can_tot["amt"] or 0) + abs(cash_tot["net"] or 0)

    # ── render ──────────────────────────────────────────────────────────────
    H = []
    A = H.append
    A(f"""<!DOCTYPE html><html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Oshinei Enterprise — รายงานตรวจสอบการทุจริต · {period}</title>
<style>
{font_face_css()}
:root{{--ink:#0f172a;--mut:#64748b;--line:#e2e8f0;--indigo:#1f3a6b;--em:#10b981;--am:#f59e0b;--rd:#ef4444;--bg:#f8fafc}}
*{{box-sizing:border-box}}
body{{font-family:'KIRA UI','Noto Sans Thai',sans-serif;color:var(--ink);background:var(--bg);margin:0;line-height:1.55;font-size:15px}}
.wrap{{max-width:1080px;margin:0 auto;padding:0 24px 80px}}
.cover{{background:#7f1d1d;color:#fff;padding:54px 48px;border-radius:0 0 24px 24px;margin-bottom:34px}}
.cover .eyebrow{{text-transform:uppercase;letter-spacing:3px;font-size:12px;opacity:.85;font-weight:600}}
.cover h1{{font-size:34px;margin:8px 0 4px;font-weight:800;line-height:1.15}}
.cover .sub{{opacity:.92;font-size:16px}}
.cover .meta{{margin-top:20px;display:flex;gap:28px;flex-wrap:wrap;font-size:13px;opacity:.95}}
.cover .meta b{{display:block;font-size:20px;font-weight:700;opacity:1}}
section{{background:#fff;border:1px solid var(--line);border-radius:4px;padding:30px 32px;margin-bottom:24px;box-shadow:0 1px 3px rgba(15,23,42,.04)}}
h2.sec{{font-size:22px;margin:0 0 4px;font-weight:800}}
h2.sec .num{{color:var(--rd);font-weight:800;margin-right:10px}}
.sec-sub{{color:var(--mut);font-size:13px;margin:0 0 20px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px}}
.kpi{{border:1px solid var(--line);border-radius:4px;padding:16px;background:linear-gradient(180deg,#fff,#fffafa)}}
.kpi .lbl{{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut);font-weight:600}}
.kpi .val{{font-size:25px;font-weight:800;margin:4px 0 2px}}
.kpi .sub{{font-size:12px;color:var(--mut)}}
.flag{{display:flex;gap:14px;align-items:flex-start;border:1px solid var(--line);border:1px solid var(--rd);border-radius:0 10px 10px 0;padding:14px 18px;margin:12px 0;background:#fef2f2}}
.flag.med{{border-left-color:var(--am);background:#fffbeb}}
.flag .ftxt b{{font-weight:700}}
.flag .fnum{{margin-left:auto;font-size:18px;font-weight:800;white-space:nowrap}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}}
th,td{{text-align:right;padding:8px 10px;border-bottom:1px solid var(--line)}}
th:first-child,td:first-child{{text-align:left}}
td.l,th.l{{text-align:left}}
th{{font-size:11px;text-transform:uppercase;letter-spacing:.4px;color:var(--mut);font-weight:700;border-bottom:2px solid var(--line)}}
tbody tr:hover{{background:#fafbff}}
tfoot td{{font-weight:800;border-top:2px solid var(--line);background:#f8fafc}}
.bar{{height:9px;border-radius:4px;display:inline-block;vertical-align:middle}}
.pill{{display:inline-block;padding:1px 9px;border-radius:999px;font-size:11px;font-weight:700}}
.tag{{display:inline-block;padding:1px 7px;border-radius:4px;font-size:10px;font-weight:700;background:#e8eef7;color:#1f3a6b}}
.tag.f{{background:#f1f5f9;color:#64748b}}
.callout{{border:1px solid var(--indigo);background:#e8eef7;border-radius:0 10px 10px 0;padding:14px 18px;margin:14px 0;font-size:14px}}
.callout.warn{{border-color:var(--am);background:#fffbeb}}
.callout.risk{{border-color:var(--rd);background:#fef2f2}}
.rec{{border:1px solid var(--line);border-radius:4px;padding:16px 18px;margin-bottom:12px}}
.rec h4{{margin:0 0 6px;font-size:15px}}
.muted{{color:var(--mut);font-size:12px}}
.toc{{columns:2;font-size:13px;gap:30px}}
.toc a{{color:var(--rd);text-decoration:none;display:block;padding:3px 0}}
footer{{text-align:center;color:var(--mut);font-size:12px;margin-top:30px}}
@media print{{body{{background:#fff;font-size:11.5px;-webkit-print-color-adjust:exact;print-color-adjust:exact}}.cover{{border-radius:0}}section{{break-inside:avoid;box-shadow:none;border-color:#ddd}}.no-print{{display:none}}}}
@media(max-width:740px){{.toc{{columns:1}}}}
</style></head><body>""")

    # Cover
    A(f"""<div class="cover"><div class="wrap" style="padding:0">
  <div class="eyebrow">Invisible Consulting · Internal Control &amp; Forensic Analytics</div>
  <h1>รายงานตรวจสอบการทุจริต<br>(Fraud &amp; Internal-Control Report)</h1>
  <div class="sub">Oshinei Enterprise · ข้อมูล Easy POS · ตรวจหาสัญญาณความเสี่ยงเชิงควบคุมภายใน</div>
  <div class="meta">
    <div><b>{thb_m(total_exposure)}</b>มูลค่าความเสี่ยงรวม (วอยด์+เงินขาด)</div>
    <div><b>{len([f for f in flags if f[0]=='high'])}</b>ธงแดง (ความเสี่ยงสูง)</div>
    <div><b>{period}</b>ช่วงข้อมูล</div>
    <div><b>{gen_date}</b>วันที่ออกรายงาน</div>
  </div>
</div></div><div class="wrap">""")

    # TOC
    A("""<section class="no-print"><h2 class="sec"><span class="num">»</span>สารบัญ</h2>
    <div class="toc">
      <a href="#s1">1 · บทสรุปผู้บริหารและธงแดง</a>
      <a href="#s2">2 · ดัชนีความเสี่ยงหลัก</a>
      <a href="#s3">3 · การยกเลิก / วอยด์บิล</a>
      <a href="#s4">4 · เงินสดขาด-เกิน</a>
      <a href="#s5">5 · การแก้ราคาด้วยมือ</a>
      <a href="#s6">6 · ส่วนลดผิดปกติ</a>
      <a href="#s7">7 · คะแนนความเสี่ยงรายสาขา</a>
      <a href="#s8">8 · ข้อเสนอแนะการควบคุม</a>
      <a href="#s9">9 · ระเบียบวิธีและข้อจำกัดของข้อมูล</a>
    </div></section>""")

    # §1 Executive summary
    A(f"""<section id="s1"><h2 class="sec"><span class="num">1</span>บทสรุปผู้บริหารและธงแดง</h2>
    <p class="sec-sub">จัดอันดับสัญญาณความเสี่ยงเชิงควบคุมตามมูลค่าผลกระทบ — ทั้งหมดมาจากข้อมูลที่ POS บันทึกไว้จริง</p>
    <p>ในช่วง <b>{period}</b> เครือ Oshinei มีบิลทั้งหมด <b>{num(total_bills)}</b> ใบ ยอดขาย (ก่อน VAT) ราว <b>{thb_m(total_sales)}</b>.
    การวิเคราะห์เชิงควบคุมพบ <b>มูลค่าความเสี่ยงรวมราว {thb_m(total_exposure)}</b>
    (ยอดวอยด์บิล {thb(can_tot['amt'])} + เงินสดขาดสุทธิ {thb(abs(cash_tot['net'] or 0))})
    ตัวเลขเหล่านี้ <u>ไม่ใช่การยืนยันการทุจริต</u> แต่เป็นจุดที่ควรตรวจสอบเอกสาร/CCTV และสอบถามผู้รับผิดชอบเป็นลำดับแรก</p>""")
    for sev, title, body, _ in flags:
        A(f"""<div class="flag {'med' if sev=='med' else ''}"><div>{pill(sev)}</div>
        <div class="ftxt"><b>{esc(title)}</b><br><span class="muted" style="font-size:13px">{esc(body)}</span></div></div>""")
    A("</section>")

    # §2 scorecard
    can_rate = 100.0*can_tot["n"]/total_bills if total_bills else 0
    untyped_disc_sh = 100.0*(disc_untyped["amt"] or 0)/(disc_tot["amt"] or 1)
    A(f"""<section id="s2"><h2 class="sec"><span class="num">2</span>ดัชนีความเสี่ยงหลัก</h2>
    <p class="sec-sub">ภาพรวมตัวเลขควบคุมภายในของทั้งช่วง</p>
    <div class="kpis">
      <div class="kpi"><div class="lbl">วอยด์/ยกเลิกบิล</div><div class="val" style="color:var(--rd)">{thb_m(can_tot['amt'])}</div><div class="sub">{num(can_tot['n'])} บิล · {pct(can_rate,2)} ของบิล</div></div>
      <div class="kpi"><div class="lbl">เงินสดขาดสุทธิ</div><div class="val" style="color:var(--rd)">{thb(cash_tot['net'])}</div><div class="sub">นับเงิน {num(cash_tot['days'])} วัน</div></div>
      <div class="kpi"><div class="lbl">ยอดเงินขาดรวม</div><div class="val" style="color:var(--am)">{thb(cash_tot['short'])}</div><div class="sub">เกิน {thb(cash_tot['over_'])}</div></div>
      <div class="kpi"><div class="lbl">แก้ราคา (ลด) ด้วยมือ</div><div class="val" style="color:var(--am)">{num(pc_tot['drops'])}</div><div class="sub">ส่วนต่าง {thb(pc_tot['drop_baht'])}</div></div>
      <div class="kpi"><div class="lbl">ส่วนลดไม่ระบุเหตุผล</div><div class="val" style="color:var(--rd)">{pct(untyped_disc_sh)}</div><div class="sub">{thb_m(disc_untyped['amt'])} จาก {thb_m(disc_tot['amt'])}</div></div>
      <div class="kpi"><div class="lbl">แคชเชียร์ลดเกินเกณฑ์</div><div class="val" style="color:var(--am)">{len(cash_outliers)}</div><div class="sub">เกณฑ์ ≥ {thr:.1f}% ของยอด</div></div>
    </div></section>""")

    # §3 cancellations
    cmax = max((r["amt"] or 0) for r in can_by_user) if can_by_user else 0
    rows = "".join(
        f"<tr><td class='l'>{esc(r['u'])}</td><td>{num(r['n'])}</td><td>{thb(r['amt'])}</td>"
        f"<td>{bar(r['amt'],cmax,'#ef4444')}</td></tr>" for r in can_by_user[:10])
    bmax = max((r["amt"] or 0) for r in can_by_branch) if can_by_branch else 0
    brows = "".join(
        f"<tr><td class='l'>{esc(bname(r['branch_id']))} <span class='tag {'f' if btype(r['branch_id'])=='franchise' else ''}'>{esc(btype(r['branch_id']))}</span></td>"
        f"<td>{num(r['n'])}</td><td>{thb(r['amt'])}</td><td>{bar(r['amt'],bmax,'#f59e0b')}</td></tr>"
        for r in can_by_branch[:10])
    bigrows = "".join(
        f"<tr><td class='l'>{esc(r['sale_date'][:16].replace('T',' '))}</td><td class='l'>{esc(bname(r['branch_id']))}</td>"
        f"<td class='l'>{esc(r['cancel_user'])}</td><td class='l'>{esc(r['reason_desc'] or '(ไม่ระบุ)')}</td><td>{thb(r['amt'])}</td></tr>"
        for r in can_big)
    reprows = "".join(
        f"<tr><td class='l'>{esc(r['u'])}</td><td class='l'>{esc(bname(r['branch_id']))}</td><td>{thb(r['amt'])}</td>"
        f"<td>{num(r['n'])}</td><td>{thb(r['tot'])}</td></tr>" for r in can_repeat)
    A(f"""<section id="s3"><h2 class="sec"><span class="num">3</span>การยกเลิก / วอยด์บิล</h2>
    <p class="sec-sub">วอยด์รวม {thb(can_tot['amt'])} · {num(can_tot['n'])} บิล · เหตุผล "อื่นๆ/ไม่ระบุ" {num(can_untyped['n'])} ครั้ง ({thb(can_untyped['amt'])})</p>
    <div class="callout risk">การวอยด์บิลหลังรับเงินสดเป็นช่องทางหลักของการดึงเงินออก — โฟกัสที่ <b>ผู้ใช้</b>ที่วอยด์มูลค่าสูง และ<b>การวอยด์ยอดเท่ากันซ้ำ ๆ</b></div>
    <h4 style="margin:18px 0 2px">ยอดวอยด์ตามผู้ใช้ (Top 10)</h4>
    <table><thead><tr><th class="l">ผู้ใช้</th><th>จำนวนครั้ง</th><th>มูลค่า</th><th>สัดส่วน</th></tr></thead><tbody>{rows}</tbody></table>
    <h4 style="margin:22px 0 2px">ยอดวอยด์ตามสาขา (Top 10)</h4>
    <table><thead><tr><th class="l">สาขา</th><th>จำนวนครั้ง</th><th>มูลค่า</th><th>สัดส่วน</th></tr></thead><tbody>{brows}</tbody></table>
    <h4 style="margin:22px 0 2px">⚠ วอยด์ยอด "เท่ากันเป๊ะ" ซ้ำโดยผู้ใช้เดียวกัน (≥25 ครั้ง)</h4>
    <p class="muted">ยอดที่ซ้ำเป๊ะหลายสิบครั้งอาจเป็นราคาแพ็กเกจบุฟเฟ่ต์ที่ยกเลิกจริง แต่ก็เป็นรูปแบบที่ควรสุ่มตรวจใบเสร็จจริง</p>
    <table><thead><tr><th class="l">ผู้ใช้</th><th class="l">สาขา</th><th>ยอดต่อครั้ง</th><th>จำนวนครั้ง</th><th>รวม</th></tr></thead><tbody>{reprows or '<tr><td colspan=5 class=l>—</td></tr>'}</tbody></table>
    <h4 style="margin:22px 0 2px">วอยด์มูลค่าสูงสุดรายบิล (Top 12)</h4>
    <table><thead><tr><th class="l">วันเวลา</th><th class="l">สาขา</th><th class="l">ผู้ใช้</th><th class="l">เหตุผล</th><th>มูลค่า</th></tr></thead><tbody>{bigrows}</tbody></table>
    </section>""")

    # §4 cash
    smax = max(abs(r["os"] or 0) for r in cash_worst) if cash_worst else 0
    wrows = "".join(
        f"<tr><td class='l'>{esc(r['day'])}</td><td class='l'>{esc(bname(r['branch_id']))}</td>"
        f"<td style='color:var(--rd)'>{thb(r['os'])}</td><td>{thb(r['ts'])}</td><td>{bar(r['os'],smax,'#ef4444')}</td></tr>"
        for r in cash_worst)
    cbmax = max(abs(r["short"] or 0) for r in cash_branch) if cash_branch else 0
    cbrows = "".join(
        f"<tr><td class='l'>{esc(bname(r['branch_id']))} <span class='tag {'f' if btype(r['branch_id'])=='franchise' else ''}'>{esc(btype(r['branch_id']))}</span></td>"
        f"<td>{num(r['days'])}</td><td>{num(r['short_days'])}</td><td style='color:var(--rd)'>{thb(r['short'])}</td>"
        f"<td>{thb(r['net'])}</td><td>{bar(r['short'],cbmax,'#ef4444')}</td></tr>" for r in cash_branch)
    A(f"""<section id="s4"><h2 class="sec"><span class="num">4</span>เงินสดขาด-เกิน (Cash Over/Short)</h2>
    <p class="sec-sub">ขาดสุทธิ {thb(cash_tot['net'])} จากการนับเงิน {num(cash_tot['days'])} วัน — ยอดขาดรวม {thb(cash_tot['short'])}</p>
    <div class="callout {'risk' if (cash_tot['net'] or 0)<0 else ''}">เงินสดที่ <b>ขาดอย่างต่อเนื่อง</b> (ไม่ใช่สลับขาด-เกินแบบสุ่ม) คือสัญญาณเสี่ยงที่สุด — ดูคอลัมน์ "วันที่ขาด" ของแต่ละสาขา</div>
    <h4 style="margin:18px 0 2px">สาขาที่เงินขาดสะสมมากสุด</h4>
    <table><thead><tr><th class="l">สาขา</th><th>วันนับเงิน</th><th>วันที่ขาด&gt;฿500</th><th>ยอดขาดรวม</th><th>สุทธิ (ขาด-เกิน)</th><th></th></tr></thead><tbody>{cbrows or '<tr><td colspan=6 class=l>—</td></tr>'}</tbody></table>
    <h4 style="margin:22px 0 2px">วันที่เงินขาดหนักสุด (Top 12)</h4>
    <table><thead><tr><th class="l">วันที่</th><th class="l">สาขา</th><th>ขาด/เกิน</th><th>ยอดขายวันนั้น</th><th></th></tr></thead><tbody>{wrows}</tbody></table>
    </section>""")

    # §5 price changes
    pcmax = max((r["n"] or 0) for r in pc_user) if pc_user else 0
    purows = "".join(
        f"<tr><td class='l'>{esc(r['u'])}</td><td>{num(r['n'])}</td><td>{num(r['drops'])}</td>"
        f"<td>{bar(r['n'],pcmax,'#f59e0b')}</td></tr>" for r in pc_user)
    pbigrows = "".join(
        f"<tr><td class='l'>{esc((r['change_date'] or '')[:16].replace('T',' '))}</td><td class='l'>{esc(bname(r['branch_id']))}</td>"
        f"<td class='l'>{esc(r['item_desc'])}</td><td>{thb(r['default_price'])}</td><td style='color:var(--rd)'>{thb(r['new_price'])}</td>"
        f"<td>{esc(r['user_name'])}</td></tr>" for r in pc_big)
    A(f"""<section id="s5"><h2 class="sec"><span class="num">5</span>การแก้ราคาด้วยมือ (Price Override)</h2>
    <p class="sec-sub">แก้ราคา {num(pc_tot['n'])} ครั้ง · เป็นการลดราคา {num(pc_tot['drops'])} ครั้ง (ส่วนต่างรวม {thb(pc_tot['drop_baht'])})</p>
    <div class="callout warn">การกดเปลี่ยนราคาที่หน้าจอควร<b>จำกัดสิทธิ์เฉพาะผู้จัดการ</b>และต้องมีเหตุผลกำกับ หากผู้ใช้คนเดียวกดบ่อยผิดปกติ ควรตรวจสอบ</div>
    <h4 style="margin:18px 0 2px">ผู้ใช้ที่แก้ราคาบ่อยสุด</h4>
    <table><thead><tr><th class="l">ผู้ใช้</th><th>แก้ราคา (ครั้ง)</th><th>ลดราคา (ครั้ง)</th><th></th></tr></thead><tbody>{purows}</tbody></table>
    <h4 style="margin:22px 0 2px">การลดราคาต่อหน่วยมากสุด (Top 10)</h4>
    <table><thead><tr><th class="l">วันเวลา</th><th class="l">สาขา</th><th class="l">รายการ</th><th>ราคาตั้ง</th><th>ราคาใหม่</th><th class="l">ผู้ใช้</th></tr></thead><tbody>{pbigrows}</tbody></table>
    </section>""")

    # §6 discounts
    comax = max((r["pctd"] or 0) for r in cash_outliers) if cash_outliers else 0
    corows = "".join(
        f"<tr><td class='l'>{esc(r['ca'])}</td><td class='l'>{esc(bname(r['branch_id']))}</td><td>{num(r['bills'])}</td>"
        f"<td>{thb(r['disc'])}</td><td style='color:var(--rd)'>{pct(r['pctd'])}</td><td>{bar(r['pctd'],comax,'#ef4444')}</td></tr>"
        for r in cash_outliers)
    A(f"""<section id="s6"><h2 class="sec"><span class="num">6</span>ส่วนลดผิดปกติ (Discount Leakage)</h2>
    <p class="sec-sub">ส่วนลดรวม {thb_m(disc_tot['amt'])} · ไม่ระบุเหตุผล {thb_m(disc_untyped['amt'])} ({pct(untyped_disc_sh)})</p>
    <div class="callout risk">ส่วนลด <b>{pct(untyped_disc_sh)}</b> ถูกให้โดย<b>ไม่เลือกเหตุผล</b> ({num(disc_untyped['bills'])} บิล) — เป็นช่องว่างควบคุมที่ใหญ่ที่สุด ควรบังคับเลือกเหตุผลทุกครั้งและจำกัดสิทธิ์ลดราคา</div>
    <h4 style="margin:18px 0 2px">แคชเชียร์ที่ลดราคาเกินเกณฑ์ (≥ {thr:.1f}% ของยอด, มัธยฐานเครือ {med:.1f}%)</h4>
    <table><thead><tr><th class="l">แคชเชียร์</th><th class="l">สาขา</th><th>บิลที่ลด</th><th>มูลค่าลด</th><th>% ของยอด</th><th></th></tr></thead><tbody>{corows or '<tr><td colspan=6 class=l>—</td></tr>'}</tbody></table>
    </section>""")

    # §7 branch risk score
    rmax = risk[0][1] if risk else 100
    rrows = ""
    for bid, score, d in risk[:15]:
        sev = "high" if score>=66 else ("med" if score>=33 else "low")
        rrows += (f"<tr><td class='l'>{esc(bname(bid))} <span class='tag {'f' if btype(bid)=='franchise' else ''}'>{esc(btype(bid))}</span></td>"
                  f"<td>{pill(sev)}</td><td>{score:.0f}</td>"
                  f"<td>{thb(d.get('cancel',0))}</td><td>{thb(d.get('short',0))}</td><td>{num(d.get('price',0))}</td>"
                  f"<td>{bar(score,rmax,SEV[sev][0])}</td></tr>")
    A(f"""<section id="s7"><h2 class="sec"><span class="num">7</span>คะแนนความเสี่ยงรายสาขา</h2>
    <p class="sec-sub">คะแนนรวม 0–100 ถ่วงน้ำหนัก: วอยด์ 45% · เงินสดขาด 40% · แก้ราคา 15% (normalize เทียบสาขาสูงสุด)</p>
    <table><thead><tr><th class="l">สาขา</th><th>ระดับ</th><th>คะแนน</th><th>วอยด์</th><th>เงินขาด</th><th>แก้ราคา</th><th></th></tr></thead><tbody>{rrows}</tbody></table>
    <p class="muted" style="margin-top:10px">หมายเหตุ: สาขาแฟรนไชส์ (franchise) อาจไม่อยู่ในการบริหารโดยตรง — ใช้ดูเทียบเคียง</p>
    </section>""")

    # §8 recommendations
    A(f"""<section id="s8"><h2 class="sec"><span class="num">8</span>ข้อเสนอแนะการควบคุม</h2>
    <p class="sec-sub">มาตรการลดความเสี่ยงที่ทำได้ทันที เรียงตามผลกระทบ</p>
    <div class="rec"><h4>1 · บังคับเลือก "เหตุผล" ทุกครั้งที่ลดราคา/วอยด์</h4>
      <p class="muted">ปิดช่องว่างที่ใหญ่ที่สุด — ส่วนลดไม่ระบุเหตุผล {pct(untyped_disc_sh)} ({thb_m(disc_untyped['amt'])}) ตั้งค่า POS ให้ reason เป็น required field</p></div>
    <div class="rec"><h4>2 · จำกัดสิทธิ์วอยด์/แก้ราคา เฉพาะผู้จัดการ + รหัสอนุมัติ</h4>
      <p class="muted">วอยด์ {thb(can_tot['amt'])} และแก้ราคา {num(pc_tot['drops'])} ครั้งกระจุกในผู้ใช้ไม่กี่คน — ต้องมี manager override code และเก็บ log</p></div>
    <div class="rec"><h4>3 · นับเงินสดสองชั้น (dual count) ในสาขาที่ขาดสะสม</h4>
      <p class="muted">เงินขาดสุทธิ {thb(cash_tot['net'])} — สาขาที่ขาดต่อเนื่องควรให้ผู้จัดการร่วมนับและเซ็นกำกับทุกกะ</p></div>
    <div class="rec"><h4>4 · สุ่มตรวจใบเสร็จ/CCTV ตามธงแดงในรายงานนี้</h4>
      <p class="muted">โฟกัสที่บิลวอยด์มูลค่าสูง การวอยด์ยอดซ้ำ ๆ และวันที่เงินขาดหนัก — เทียบกับภาพกล้องและสต๊อกจริง</p></div>
    <div class="rec"><h4>5 · ทำรายงานนี้เป็นประจำทุกเดือน + ตั้ง alert</h4>
      <p class="muted">รัน <code>gen_fraud_report.py</code> ทุกสิ้นเดือน หรือผูกกับแท็บ #fraud ในแดชบอร์ดเพื่อเฝ้าระวังต่อเนื่อง</p></div>
    </section>""")

    # §9 methodology
    A(f"""<section id="s9"><h2 class="sec"><span class="num">9</span>ระเบียบวิธีและข้อจำกัดของข้อมูล</h2>
    <ul class="muted" style="font-size:13px;line-height:1.7">
      <li>แหล่งข้อมูล: <code>oshinei.db</code> (คลังข้อมูล Easy POS) ตาราง pos_cancel_bill_detail, pos_cashcount_day, pos_price_change, sales_transaction</li>
      <li>ช่วงเวลา: {esc(D0)} ถึง {esc(D1)} — วอยด์/แก้ราคากรองตามวันที่ทำรายการ, เงินสดกรองตามวันนับเงิน</li>
      <li>"ความเสี่ยงรวม" = ยอดวอยด์ + ยอดเงินขาดสุทธิ (ค่าสัมบูรณ์) — เป็น <b>มูลค่าที่ควรตรวจสอบ ไม่ใช่มูลค่าการสูญเสียที่ยืนยันแล้ว</b></li>
      <li>ตัวเลขทั้งหมดเป็นสัญญาณเชิงสถิติ ต้องตรวจสอบเอกสาร/CCTV/สอบถามผู้รับผิดชอบก่อนสรุปว่าเป็นการทุจริต</li>
      <li>ส่วนลดไม่ระบุเหตุผล = discount_reason_id ว่าง/0; แคชเชียร์เกินเกณฑ์ = ลด ≥ 2× มัธยฐานเครือ (พื้น 5%)</li>
      <li>คะแนนรายสาขาเป็นการเทียบกันเองในเครือ (relative) ไม่ใช่เกณฑ์มาตรฐานอุตสาหกรรม</li>
    </ul>
    <footer>Invisible Consulting — Oshinei Enterprise ERP · รายงานสร้างอัตโนมัติ {gen_date} · เอกสารลับเฉพาะผู้บริหาร</footer>
    </section></div></body></html>""")

    out = args.out or os.path.join(os.path.dirname(__file__), "..", "docs",
                                   f"FRAUD_REPORT_{D0[:7].replace('-','_')}_{D1[:7].replace('-','_')}.html")
    out = os.path.abspath(out)
    with open(out, "w", encoding="utf-8") as f:
        f.write("".join(H))
    print("Wrote", out)
    print(f"  exposure={thb(total_exposure)}  flags={len(flags)} (high={len([f for f in flags if f[0]=='high'])})")


if __name__ == "__main__":
    main()
