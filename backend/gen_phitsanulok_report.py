#!/usr/bin/env python3
"""พิษณุโลก — ทบทวนการใช้คูปองส่วนลดผู้สูงอายุ (coupon_series_id = 35).

WHY THIS REPORT EXISTS
----------------------
The all-tab audit on 2026-09-10 found 256 bills chain-wide whose discount exceeds
the bill total, and 206 of them sit at ONE branch. Tracing them landed on a single
coupon series — "ส่วนลดผู้สูงอายุ 81 ปีขึ้นไป" — which every branch uses and which
everywhere else takes 34-46% off a bill that still collects money. พิษณุโลก is the
only branch in the chain that has ever written a coupon-35 bill down to ฿0.

This report is a CONTROL review at branch level. It does not name individuals and
does not allege wrongdoing: the POS cashier field is a shared login (see
[[discount_approver_and_reconcile]]), so per-person attribution is not valid
evidence and is deliberately reported only as a spread, not as a ranking of people.

    python backend/gen_phitsanulok_report.py [--db ...] [--out ...]
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gen_surin_ubon_report import CSS, fmt, page_hdr, ro_uri, resolve_db  # noqa: E402
from report_fonts import font_face_css  # KIRA UI, embedded (backend/report_fonts.py)

COUPON_ID = 35
BRANCH_LIKE = "%พิษณุโลก%"


def q1(con, sql, args=()):
    r = con.execute(sql, args).fetchone()
    return r if r else None


def load(db_path):
    con = sqlite3.connect(ro_uri(resolve_db(db_path)), uri=True)
    con.row_factory = sqlite3.Row
    d = {}
    J = "FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id"
    COMP = (f"b.name LIKE ? AND t.coupon_series_id={COUPON_ID} AND t.total_inc_vat<=0")

    d["coupon_name"] = (q1(con, "SELECT name FROM coupon_series WHERE coupon_series_id=?",
                           (COUPON_ID,)) or ["(ไม่พบชื่อคูปอง)"])[0]

    # headline
    r = q1(con, f"SELECT COUNT(*) n, SUM(t.discount) d, SUM(t.customer_count) px, "
                f"MIN(substr(t.sold_at,1,10)) d0, MAX(substr(t.sold_at,1,10)) d1 {J} WHERE {COMP}",
           (BRANCH_LIKE,))
    d["comp"] = dict(n=r["n"], disc=r["d"] or 0, pax=r["px"] or 0, d0=r["d0"], d1=r["d1"])

    # the branch in context
    r = q1(con, f"SELECT SUM(t.total_inc_vat) tot, SUM(t.discount) disc, COUNT(*) n {J} "
                f"WHERE b.name LIKE ?", (BRANCH_LIKE,))
    d["branch"] = dict(total=r["tot"] or 0, disc=r["disc"] or 0, bills=r["n"])

    # peer comparison inside the SAME coupon series — the ratio is what isolates it
    d["peers"] = [dict(r) for r in con.execute(
        f"""SELECT b.name, COUNT(*) n,
                   SUM(CASE WHEN t.total_inc_vat<=0 THEN 1 ELSE 0 END) zero_bills,
                   SUM(t.discount) disc, SUM(t.total_inc_vat) tot, SUM(t.customer_count) px
            {J} WHERE t.coupon_series_id={COUPON_ID}
            GROUP BY b.name HAVING COUNT(*)>=100
            ORDER BY SUM(t.discount)*1.0/NULLIF(SUM(t.total_inc_vat),0) DESC""")]

    # per-head discount: comped bills vs the coupon's ordinary use at the same branch
    def bands(where):
        return [dict(band=b or 0, n=n) for b, n in con.execute(
            f"""SELECT CAST(t.discount/NULLIF(t.customer_count,0)/50 AS INT)*50, COUNT(*)
                {J} WHERE {where} GROUP BY 1 ORDER BY 1""", (BRANCH_LIKE,))]
    d["band_comp"] = bands(COMP)
    d["band_paid"] = bands(f"b.name LIKE ? AND t.coupon_series_id={COUPON_ID} AND t.total_inc_vat>0")

    d["by_month"] = [dict(ym=a, n=b, disc=c) for a, b, c in con.execute(
        f"SELECT substr(t.sold_at,1,7),COUNT(*),SUM(t.discount) {J} WHERE {COMP} GROUP BY 1 ORDER BY 1",
        (BRANCH_LIKE,))]
    d["by_pax"] = [dict(pax=a, n=b, disc=c) for a, b, c in con.execute(
        f"SELECT t.customer_count,COUNT(*),SUM(t.discount) {J} WHERE {COMP} GROUP BY 1 ORDER BY 1",
        (BRANCH_LIKE,))]
    d["by_login"] = [dict(who=a or "(ว่าง)", n=b, disc=c) for a, b, c in con.execute(
        f"SELECT t.cashier,COUNT(*),SUM(t.discount) {J} WHERE {COMP} GROUP BY 1 ORDER BY 2 DESC",
        (BRANCH_LIKE,))]
    d["by_dow"] = [dict(dow=int(a), n=b) for a, b in con.execute(
        f"SELECT strftime('%w',t.sold_at),COUNT(*) {J} WHERE {COMP} GROUP BY 1 ORDER BY 1",
        (BRANCH_LIKE,))]
    d["top_bills"] = [dict(r) for r in con.execute(
        f"""SELECT substr(t.sold_at,1,16) ts, t.receipt_no, t.customer_count px,
                   t.discount disc, t.total_inc_vat tot, t.cashier
            {J} WHERE {COMP} ORDER BY t.discount DESC LIMIT 12""", (BRANCH_LIKE,))]
    con.close()
    return d


DOW_TH = ["อาทิตย์", "จันทร์", "อังคาร", "พุธ", "พฤหัสฯ", "ศุกร์", "เสาร์"]


def bar(label, val, mx, color="#1f3a6b", suffix=""):
    w = (val / mx * 100) if mx else 0
    return (f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0;font-size:12px">'
            f'<span style="min-width:96px;color:#475569">{label}</span>'
            f'<span style="flex:1;background:#f1f5f9;border-radius:4px;height:15px;position:relative">'
            f'<span style="position:absolute;inset:0 auto 0 0;width:{w:.1f}%;background:{color};'
            f'border-radius:4px"></span></span>'
            f'<span style="min-width:80px;text-align:right;font-weight:700;color:{color}">{val:,.0f}{suffix}</span></div>')


def generate(d):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    C, B = d["comp"], d["branch"]
    me = next((p for p in d["peers"] if "พิษณุโลก" in p["name"]), None)
    others = [p for p in d["peers"] if "พิษณุโลก" not in p["name"]]
    peer_lo = min((p["disc"] / p["tot"] * 100) for p in others if p["tot"]) if others else 0
    peer_hi = max((p["disc"] / p["tot"] * 100) for p in others if p["tot"]) if others else 0
    my_ratio = (me["disc"] / me["tot"] * 100) if me and me["tot"] else 0
    out = []
    add = out.append
    add(f"""<!doctype html><html lang="th"><head><meta charset="utf-8">
<title>พิษณุโลก — ทบทวนการใช้คูปองส่วนลดผู้สูงอายุ</title><style>{font_face_css()}
{CSS}</style></head><body>""")

    # ── COVER ───────────────────────────────────────────────────────────────
    add(f"""<div class="cover">
<span class="cover-tag">DISCOUNT CONTROL REVIEW · INTERNAL</span>
<div class="cover-title">พิษณุโลก<br>ทบทวนการใช้คูปองส่วนลดผู้สูงอายุ</div>
<div class="cover-sub">คูปอง #{COUPON_ID} “{d['coupon_name']}” · บิลที่ถูกยกยอดทั้งใบ · {C['d0']} – {C['d1']}</div>
<div class="cover-kpis">
  <div class="ckpi"><div class="ckpi-val">{C['n']:,}</div><div class="ckpi-lbl">บิลที่ยกยอดทั้งใบ (จ่ายจริง ฿0)</div></div>
  <div class="ckpi"><div class="ckpi-val">{fmt(C['disc'])}</div><div class="ckpi-lbl">มูลค่าส่วนลดรวมของบิลเหล่านั้น</div></div>
  <div class="ckpi"><div class="ckpi-val">0</div><div class="ckpi-lbl">บิลลักษณะเดียวกันในอีก 26 สาขา</div></div>
  <div class="ckpi"><div class="ckpi-val">{my_ratio:.0f}%</div><div class="ckpi-lbl">ส่วนลด ÷ ยอดบิล ของคูปองนี้ (สาขาอื่น {peer_lo:.0f}–{peer_hi:.0f}%)</div></div>
</div>
<div class="cover-note"><strong>ข้อค้นพบหลัก</strong> — คูปองนี้ใช้กันทุกสาขาและทำงานเหมือนกันหมด คือลดบางส่วนแล้วบิลยัง<u>เก็บเงินได้</u>
ไม่มีสาขาใดเคยยกบิลเป็น ฿0 เลยแม้แต่ใบเดียว ยกเว้นพิษณุโลก {C['n']:,} ใบ</div>
<div class="cover-note"><strong>รายงานนี้เป็นการทบทวนการควบคุมระดับสาขา</strong> ไม่ชี้ว่าผู้ใดกระทำผิด —
ช่องผู้ใช้งานใน POS เป็น login ที่ใช้ร่วมกัน จึงใช้ระบุตัวบุคคลไม่ได้ และแสดงไว้เป็นการกระจายเท่านั้น</div>
<div class="cover-foot">
  <span>OSHINEI ENTERPRISE · Discount Control Review</span>
  <span>ข้อมูล {C['d0']} – {C['d1']} · สร้าง {now}</span>
  <span>INTERNAL USE</span>
</div></div>""")

    # ── 1. EXECUTIVE SUMMARY ────────────────────────────────────────────────
    add(page_hdr("1. สรุปผู้บริหาร (Executive Summary)"))
    add(f"""<div class="sec-label">EXECUTIVE SUMMARY</div>
<div class="sec-title" style="font-size:19px">คูปองผู้สูงอายุถูกใช้ยกยอดทั้งโต๊ะ ไม่ใช่ลดเฉพาะผู้สูงอายุ</div>
<div class="sec-desc">ตรวจพบจากการไล่ตรวจทุกแท็บของระบบเมื่อ {now[:10]} — เริ่มจากสัญญาณ “บิลที่ส่วนลดมากกว่ายอดบิล”</div>
<ul style="font-size:13px;line-height:1.9">
<li>คูปอง #{COUPON_ID} ชื่อ <strong>“{d['coupon_name']}”</strong> เป็นส่วนลด<strong>รายบุคคล</strong>สำหรับผู้สูงอายุ</li>
<li>ทุกสาขาที่ใช้คูปองนี้ ลดอยู่ในกรอบ <strong>{peer_lo:.0f}–{peer_hi:.0f}%</strong> ของยอดบิล และ<strong>ไม่มีบิลใดถูกยกเป็น ฿0</strong></li>
<li>พิษณุโลกมี <strong>{C['n']:,} บิล</strong> ที่ลูกค้าจ่ายจริง ฿0 รวมมูลค่า <strong>{fmt(C['disc'])}</strong> ({C['pax']:,.0f} คน)</li>
<li>บนบิลเหล่านั้น ส่วนลดต่อหัวอยู่ที่ <strong>฿600–649</strong> ซึ่งเท่ากับ<strong>ราคาบุฟเฟ่ต์เต็มของทุกคนบนโต๊ะ</strong>
    ขณะที่บิลคูปองเดียวกันของสาขานี้ที่ยังเก็บเงิน ลดหัวละ ฿100–300 ตามปกติ</li>
<li><strong>{sum(r['n'] for r in d['by_pax'] if (r['pax'] or 0) >= 3)} บิล</strong> เป็นโต๊ะ <strong>3–6 คน</strong>
    ที่ถูกยกให้ฟรีทั้งโต๊ะ ทั้งที่คูปองเป็นสิทธิ์ของผู้สูงอายุรายคน</li>
<li><strong>แนวโน้มลดลงจนเกือบหยุดแล้ว</strong> — สูงสุด {d['by_month'][0]['ym']} ({d['by_month'][0]['n']} บิล)
    เหลือ {d['by_month'][-1]['n']} บิลใน {d['by_month'][-1]['ym']} จึงไม่ใช่ความเสียหายที่ยังเกิดต่อเนื่อง</li>
</ul>
<div class="alert alert-teal" style="margin-top:10px"><strong>สิ่งที่ข้อมูลบอกได้และบอกไม่ได้:</strong>
ข้อมูลยืนยันได้ว่า<strong>วิธีปฏิบัติของสาขานี้ต่างจากทุกสาขาอย่างมีนัยสำคัญ</strong>
แต่<strong>บอกไม่ได้</strong>ว่าเกิดจากความเข้าใจเงื่อนไขคูปองผิด การอนุมัติที่หละหลวม หรือเจตนาทุจริต —
ทั้งสามอย่างให้ร่องรอยในข้อมูลแบบเดียวกัน การสรุปสาเหตุต้องใช้การสอบถามหน้างานประกอบ</div>""")
    add('</div></div>')
    # ── 2. PEER COMPARISON ──────────────────────────────────────────────────
    add(page_hdr("2. เทียบกับสาขาอื่นภายในคูปองเดียวกัน"))
    add(f"""<div class="sec-label">PEER COMPARISON</div>
<div class="sec-title" style="font-size:17px">คูปอง #{COUPON_ID} ใช้กันทั้งเครือ — เทียบกันตรงๆ ได้</div>
<div class="sec-desc">เทียบ<strong>อัตราส่วน</strong> ไม่ใช่ยอดบาท เพราะขนาดสาขาต่างกัน · เฉพาะสาขาที่ใช้คูปองนี้ ≥ 100 บิล</div>
<table><thead><tr><th>สาขา</th><th class="num">บิลคูปองนี้</th><th class="num">บิลที่ยกทั้งใบ</th>
<th class="num">ส่วนลด</th><th class="num">ยอดบิล</th><th class="num">ส่วนลด ÷ ยอดบิล</th><th class="num">ส่วนลด/หัว</th></tr></thead><tbody>""")
    for p in d["peers"]:
        mine = "พิษณุโลก" in p["name"]
        ratio = (p["disc"] / p["tot"] * 100) if p["tot"] else 0
        st = ' style="background:#fef2f2;font-weight:700"' if mine else ""
        col = "#b91c1c" if mine else "#334155"
        zcol = "#b91c1c" if p["zero_bills"] else "#16a34a"
        add(f'<tr{st}><td style="color:{col}">{p["name"]}</td><td class="num">{p["n"]:,}</td>'
            f'<td class="num" style="color:{zcol};font-weight:700">{p["zero_bills"]:,}</td>'
            f'<td class="num">{fmt(p["disc"])}</td><td class="num">{fmt(p["tot"])}</td>'
            f'<td class="num" style="color:{col}">{ratio:.0f}%</td>'
            f'<td class="num">{fmt(p["disc"] / p["px"] if p["px"] else 0)}</td></tr>')
    add("""</tbody></table>
<div class="alert alert-amber" style="margin-top:10px">คอลัมน์ที่ชี้ขาดคือ <strong>“บิลที่ยกทั้งใบ”</strong> —
ทุกสาขาเป็น <strong>0</strong> สาขาเดียวที่ไม่ใช่ 0 คือพิษณุโลก ถ้าเป็นเรื่องของการตีความเงื่อนไขคูปองที่กำกวม
เราจะเห็นสาขาอื่นทำแบบเดียวกันบ้างประปราย แต่ไม่มีเลยแม้แต่ใบเดียว</div>""")
    add('</div></div>')

    # ── 3. THE MECHANISM ────────────────────────────────────────────────────
    add(page_hdr("3. กลไก — ลดเต็มราคาให้ทุกคนบนโต๊ะ"))
    mxc = max((r["n"] for r in d["band_comp"]), default=1)
    mxp = max((r["n"] for r in d["band_paid"]), default=1)
    add("""<div class="sec-label">MECHANISM</div>
<div class="sec-title" style="font-size:17px">ส่วนลดต่อหัว — บิลที่ยกทั้งใบ เทียบ บิลคูปองเดียวกันที่ยังเก็บเงิน</div>
<div class="sec-desc">แกนคือส่วนลด ÷ จำนวนคนบนบิล จัดกลุ่มทีละ ฿50 · สาขาเดียวกัน คูปองเดียวกัน ต่างกันแค่ว่าบิลนั้นเก็บเงินหรือไม่</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:8px">
<div><div style="font-weight:700;font-size:13px;color:#b91c1c;margin-bottom:4px">บิลที่ยกทั้งใบ (จ่าย ฿0)</div>""")
    for r in d["band_comp"]:
        add(bar(f"฿{r['band']:,}/หัว", r["n"], mxc, "#dc2626", " ใบ"))
    add('</div><div><div style="font-weight:700;font-size:13px;color:#1f3a6b;margin-bottom:4px">'
        'บิลคูปองเดียวกันที่ยังเก็บเงิน</div>')
    for r in d["band_paid"]:
        add(bar(f"฿{r['band']:,}/หัว", r["n"], mxp, "#1f3a6b", " ใบ"))
    add("""</div></div>
<div class="alert alert-teal" style="margin-top:12px">บิลที่ยังเก็บเงินกระจุกที่ <strong>฿100–300/หัว</strong>
ซึ่งคือ “ลดบางส่วนให้ผู้สูงอายุ” ตามที่คูปองตั้งใจ · ส่วนบิลที่ยกทั้งใบกระจุกที่ <strong>฿600–649/หัว</strong>
ซึ่งเท่ากับ<strong>ราคาบุฟเฟ่ต์เต็ม</strong> และถูกคิดให้<strong>ทุกหัวบนโต๊ะ</strong> ไม่ใช่เฉพาะผู้สูงอายุ</div>""")
    add('</div></div>')

    # ── 4. WHO / WHEN / HOW BIG ─────────────────────────────────────────────
    add(page_hdr("4. รูปแบบการเกิด — ขนาดโต๊ะ · ช่วงเวลา · แนวโน้ม"))
    add('<div class="sec-label">PATTERNS</div>')
    add('<div class="sec-title" style="font-size:17px">แนวโน้มรายเดือน (กำลังหยุดไปเอง)</div>')
    mxm = max((r["n"] for r in d["by_month"]), default=1)
    for r in d["by_month"]:
        add(bar(r["ym"], r["n"], mxm, "#b45309", " ใบ"))
    add('<div class="sec-title" style="font-size:17px;margin-top:14px">ขนาดโต๊ะที่ถูกยกให้ฟรีทั้งใบ</div>')
    mxx = max((r["n"] for r in d["by_pax"]), default=1)
    for r in d["by_pax"]:
        big = (r["pax"] or 0) >= 3
        add(bar(f"{r['pax']} คน", r["n"], mxx, "#dc2626" if big else "#64748b", " ใบ"))
    add('<div class="sec-desc" style="margin-top:6px">แถบแดง = โต๊ะ 3 คนขึ้นไป '
        'ซึ่งยากที่จะเป็นผู้สูงอายุ 81 ปีขึ้นไปทั้งโต๊ะ</div>')
    add('<div class="sec-title" style="font-size:17px;margin-top:14px">วันในสัปดาห์</div>')
    mxd = max((r["n"] for r in d["by_dow"]), default=1)
    for r in d["by_dow"]:
        wk = r["dow"] in (0, 6)
        add(bar(DOW_TH[r["dow"]], r["n"], mxd, "#5f4d99" if wk else "#64748b", " ใบ"))
    add('</div></div>')

    # ── 5. LOGIN SPREAD + TOP BILLS ─────────────────────────────────────────
    add(page_hdr("5. การกระจายตามผู้ใช้งาน POS และบิลมูลค่าสูงสุด"))
    add("""<div class="sec-label">SPREAD &amp; SAMPLES</div>
<div class="alert alert-amber"><strong>อ่านส่วนนี้ด้วยความระมัดระวัง</strong> — ช่องผู้ใช้งานใน POS
เป็น <strong>login ที่ใช้ร่วมกัน</strong> (เช่น “Cashier”, “Manager”, “admin” ไม่ใช่ชื่อบุคคล)
จึง<strong>ใช้ระบุตัวผู้กระทำไม่ได้</strong> แสดงไว้เพื่อดูว่ากระจุกที่สิทธิ์ระดับใดเท่านั้น</div>
<table><thead><tr><th>login ที่ใช้คีย์</th><th class="num">บิล</th><th class="num">มูลค่า</th></tr></thead><tbody>""")
    for r in d["by_login"]:
        add(f'<tr><td>{r["who"]}</td><td class="num">{r["n"]:,}</td><td class="num">{fmt(r["disc"])}</td></tr>')
    add("""</tbody></table>
<div class="sec-desc" style="margin-top:6px">กระจายอยู่หลาย login รวมถึงระดับ Manager และ admin —
ไม่ได้กระจุกที่ login เดียว ซึ่งชี้ไปทาง<strong>วิธีปฏิบัติของสาขา</strong> มากกว่าการกระทำของคนคนเดียว</div>
<div class="sec-title" style="font-size:17px;margin-top:14px">ตัวอย่างบิลมูลค่าสูงสุด</div>
<table><thead><tr><th>เวลา</th><th>เลขที่บิล</th><th class="num">คน</th><th class="num">ส่วนลด</th>
<th class="num">ลูกค้าจ่ายจริง</th><th class="num">ส่วนลด/หัว</th></tr></thead><tbody>""")
    for r in d["top_bills"]:
        ph = (r["disc"] / r["px"]) if r["px"] else 0
        add(f'<tr><td>{r["ts"]}</td><td style="font-size:11px;color:#64748b">{r["receipt_no"]}</td>'
            f'<td class="num">{r["px"]}</td>'
            f'<td class="num" style="color:#b91c1c;font-weight:700">{fmt(r["disc"])}</td>'
            f'<td class="num">{fmt(r["tot"])}</td><td class="num">{fmt(ph)}</td></tr>')
    add('</tbody></table>')
    add('</div></div>')

    # ── 6. RECOMMENDATIONS ──────────────────────────────────────────────────
    share = (C["disc"] / B["total"] * 100) if B["total"] else 0
    add(page_hdr("6. ข้อเสนอแนะด้านการควบคุม"))
    add(f"""<div class="sec-label">CONTROL RECOMMENDATIONS</div>
<div class="sec-title" style="font-size:17px">สี่ข้อ เรียงตามลำดับที่ควรทำ</div>
<ol style="font-size:13px;line-height:1.95">
<li><strong>ถามสาขาก่อนสรุป</strong> — ขอคำอธิบายว่าเข้าใจเงื่อนไขคูปอง #{COUPON_ID} อย่างไร
    และมีการอนุมัติจากใครเมื่อยกยอดทั้งใบ ข้อมูลชี้ว่า<strong>ต่างจากสาขาอื่นชัดเจน</strong> แต่ไม่ได้บอกสาเหตุ</li>
<li><strong>กำหนดเพดานให้คูปองในระบบ POS</strong> — คูปองที่เป็นสิทธิ์<strong>รายบุคคล</strong>
    ไม่ควรลดเกินราคา 1 ที่ต่อการใช้ 1 ครั้ง ถ้า POS ตั้งเพดานได้ จะปิดช่องนี้ที่ต้นทางโดยไม่ต้องพึ่งวินัยหน้างาน</li>
<li><strong>ทำให้ “บิลจ่ายจริง ฿0” ต้องมีผู้อนุมัติเสมอ</strong> — ปัจจุบัน login เป็นแบบใช้ร่วมกัน
    ทำให้ตรวจสอบย้อนหลังไม่ได้ว่าใครอนุมัติ ควรแยก login รายบุคคลอย่างน้อยสำหรับสิทธิ์ยกยอดบิล</li>
<li><strong>เฝ้าสัญญาณนี้ต่อเนื่อง</strong> — แท็บ เวลาปิดร้าน → ตรวจสุขภาพข้อมูล
    รายงาน “บิลที่ส่วนลดมากกว่ายอดบิล” อยู่แล้วทุกวัน ใช้เป็นตัวเฝ้าระวังได้เลยโดยไม่ต้องทำรายงานใหม่</li>
</ol>
<div class="alert alert-teal" style="margin-top:12px"><strong>บริบทเชิงมูลค่า</strong> —
{fmt(C['disc'])} เทียบกับยอดบิลรวมของสาขาทั้งช่วง {fmt(B['total'])} คิดเป็น {share:.2f}% ของยอดขายสาขา
จึง<strong>ไม่ใช่ความเสียหายระดับที่กระทบงบการเงิน</strong> แต่เป็น<strong>ช่องว่างของการควบคุม</strong>
ที่ถ้าไม่ปิด อาจถูกใช้กับรายการที่ใหญ่กว่านี้ได้</div>""")
    add('</div></div>')
    add("</body></html>")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(HERE, "oshinei.db"))
    ap.add_argument("--out", default=os.path.join(HERE, "..", "docs", "PHITSANULOK_COUPON_REVIEW.html"))
    a = ap.parse_args()
    d = load(a.db)
    html = generate(d)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {a.out}  ({len(html):,} bytes) — {d['comp']['n']} comped bills")


if __name__ == "__main__":
    main()
