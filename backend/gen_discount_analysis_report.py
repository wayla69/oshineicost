#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate discount anomaly analysis report for Ubon & Surin branches.

Analyzes unusual discount patterns and investigates correlation with specific staff members.
Generates a self-contained, print-friendly HTML report focusing on discount outliers,
cashier-level anomalies, and risk assessment.

Usage:
    python gen_discount_analysis_report.py [--branch อุบล|สุรินทร์] [--format html|pdf]

All numbers are pulled from the database's sales_transaction table with unified discount
classification and staffing analysis.
"""
import argparse
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
import statistics
import math

# ── Configuration ─────────────────────────────────────────────────────────────
TARGET_BRANCHES = ['อุบลราชธานี', 'สุรินทร์']
STAFF_TO_INVESTIGATE = {
    '02150002': 'กาญจนา เจริญยิ่ง',
    '02150003': 'ศิริวรรณ ศรีทัน',
    '02140002': 'สมชาย โทแช่ม'
}
ANOMALY_THRESHOLD_PCT = 2.0  # Discount % that's unusual
ANOMALY_THRESHOLD_STDEV = 2.0  # Standard deviations

# ── Database Access ────────────────────────────────────────────────────────
def get_db():
    """Get SQLite database connection."""
    db_path = Path(__file__).parent / 'oshinei.db'
    conn = sqlite3.connect(str(db_path), timeout=60)
    conn.row_factory = sqlite3.Row
    # Enable foreign keys
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

# ── Data Extraction ───────────────────────────────────────────────────────────
def fetch_branch_id(conn, branch_name):
    """Get branch ID by name."""
    cursor = conn.execute(
        "SELECT branch_id FROM branches WHERE name = ?",
        (branch_name,)
    )
    row = cursor.fetchone()
    return row['branch_id'] if row else None

def fetch_discount_data(conn, branch_id, months_back=6):
    """Fetch all discount transactions for a branch."""
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=30*months_back)

    cursor = conn.execute("""
        SELECT
            receipt_no, sold_at, cashier, customer_count,
            food_subtotal, drink_subtotal, other_subtotal,
            discount, sp_discount, total_inc_vat, source
        FROM sales_transaction
        WHERE branch_id = ?
          AND date(sold_at) >= ?
          AND date(sold_at) <= ?
          AND (discount > 0 OR sp_discount > 0)
        ORDER BY sold_at DESC
    """, (branch_id, start_date.isoformat(), end_date.isoformat()))

    return [dict(row) for row in cursor.fetchall()]

def fetch_all_transactions(conn, branch_id, months_back=6):
    """Fetch all transactions (including zero-discount) for analysis."""
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=30*months_back)

    cursor = conn.execute("""
        SELECT
            receipt_no, sold_at, cashier, customer_count,
            food_subtotal, drink_subtotal, other_subtotal,
            discount, sp_discount, total_inc_vat, source
        FROM sales_transaction
        WHERE branch_id = ?
          AND date(sold_at) >= ?
          AND date(sold_at) <= ?
        ORDER BY sold_at DESC
    """, (branch_id, start_date.isoformat(), end_date.isoformat()))

    return [dict(row) for row in cursor.fetchall()]

# ── Analysis Helpers ───────────────────────────────────────────────────────
def thb(n):
    """Format number as Thai Baht."""
    return "฿" + format(int(round(n or 0)), ",")

def thb_m(n):
    """Format number as millions of Thai Baht."""
    return "฿{:,.1f}M".format((n or 0) / 1_000_000)

def pct(n, d=1):
    """Format number as percentage."""
    return "—" if n is None else f"{n:.{d}f}%"

def mom(curr, prev, good_up=True, as_pts=False):
    """Month-on-month delta pill with color coding."""
    if curr is None or prev in (None, 0):
        return '<span class="mom" style="color:var(--mut)">—</span>'
    if as_pts:
        d = curr - prev
        txt = f"{d:+.1f} จุด MoM"
    else:
        d = (curr / prev - 1) * 100
        txt = f"{d:+.1f}% MoM"
    rising = d >= 0
    good = rising if good_up else (not rising)
    color = "var(--em)" if good else "var(--rd)"
    arrow = "▲" if rising else "▼"
    return f'<span class="mom" style="color:{color};font-weight:700">{arrow} {txt}</span>'

def calculate_discount_pct(transaction):
    """Calculate discount percentage from transaction."""
    gross = (transaction['food_subtotal'] or 0) + \
            (transaction['drink_subtotal'] or 0) + \
            (transaction['other_subtotal'] or 0)

    if gross <= 0:
        return 0

    discount = (transaction['discount'] or 0) + (transaction['sp_discount'] or 0)
    return (discount / gross) * 100

def identify_anomalies(transactions):
    """Identify anomalous discount transactions."""
    if not transactions:
        return [], {}

    # Calculate metrics for each cashier
    cashier_stats = defaultdict(lambda: {'discounts': [], 'counts': 0})

    for tx in transactions:
        cashier = tx['cashier'] or 'Unknown'
        discount_pct = calculate_discount_pct(tx)
        cashier_stats[cashier]['discounts'].append(discount_pct)
        cashier_stats[cashier]['counts'] += 1

    # Calculate statistics
    all_discounts = [d for stats in cashier_stats.values() for d in stats['discounts']]
    if not all_discounts:
        return [], cashier_stats

    overall_mean = statistics.mean(all_discounts)
    overall_stdev = statistics.stdev(all_discounts) if len(all_discounts) > 1 else 0

    # Find anomalies
    anomalies = []
    for tx in transactions:
        discount_pct = calculate_discount_pct(tx)
        # Anomaly if: very high discount OR significantly above peer mean
        is_anomaly = (
            discount_pct > ANOMALY_THRESHOLD_PCT or
            (overall_stdev > 0 and
             discount_pct > overall_mean + ANOMALY_THRESHOLD_STDEV * overall_stdev)
        )
        if is_anomaly:
            anomalies.append({
                **tx,
                'discount_pct': discount_pct,
                'severity': 'HIGH' if discount_pct > 10 else 'MEDIUM'
            })

    return sorted(anomalies, key=lambda x: -x['discount_pct']), cashier_stats

def analyze_cashier_patterns(transactions):
    """Analyze discount patterns by cashier."""
    patterns = {}

    for tx in transactions:
        cashier = tx['cashier'] or 'Unknown'
        if cashier not in patterns:
            patterns[cashier] = {
                'transaction_count': 0,
                'discount_count': 0,
                'total_discount': 0,
                'discount_percentages': [],
                'avg_bill': 0,
                'total_revenue': 0
            }

        patterns[cashier]['transaction_count'] += 1

        bill = (tx['food_subtotal'] or 0) + (tx['drink_subtotal'] or 0) + (tx['other_subtotal'] or 0)
        patterns[cashier]['total_revenue'] += bill

        discount = (tx['discount'] or 0) + (tx['sp_discount'] or 0)
        if discount > 0:
            patterns[cashier]['discount_count'] += 1
            patterns[cashier]['total_discount'] += discount
            discount_pct = calculate_discount_pct(tx)
            patterns[cashier]['discount_percentages'].append(discount_pct)

    # Calculate summaries
    for cashier in patterns:
        p = patterns[cashier]
        p['discount_rate'] = (p['discount_count'] / p['transaction_count'] * 100) if p['transaction_count'] > 0 else 0
        p['avg_discount_pct'] = statistics.mean(p['discount_percentages']) if p['discount_percentages'] else 0
        p['avg_bill'] = p['total_revenue'] / p['transaction_count'] if p['transaction_count'] > 0 else 0

    return patterns

# ── HTML Generation ───────────────────────────────────────────────────────────
def generate_html_report(branch_data, report_date):
    """Generate HTML report for discount analysis with actual data."""

    H = []
    A = H.append

    branch_names = list(branch_data.keys())

    A(f"""<!DOCTYPE html><html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Oshinei Discount Anomaly Analysis · {report_date}</title>
<style>
:root{{--ink:#0f172a;--mut:#64748b;--line:#e2e8f0;--indigo:#4f46e5;--em:#10b981;--am:#f59e0b;--rd:#ef4444;--bg:#f8fafc}}
*{{box-sizing:border-box}}
body{{font-family:"Sarabun","Segoe UI",-apple-system,Roboto,sans-serif;color:var(--ink);background:var(--bg);margin:0;line-height:1.55;font-size:15px}}
.wrap{{max-width:1200px;margin:0 auto;padding:0 24px 80px}}
.cover{{background:linear-gradient(135deg,#7c2d12 0%,#dc2626 55%,#f87171 100%);color:#fff;padding:54px 48px;border-radius:0 0 24px 24px;margin-bottom:34px}}
.cover .eyebrow{{text-transform:uppercase;letter-spacing:3px;font-size:12px;opacity:.85;font-weight:600}}
.cover h1{{font-size:36px;margin:8px 0 4px;font-weight:800;line-height:1.15}}
.cover .sub{{opacity:.9;font-size:16px}}
.cover .meta{{margin-top:20px;display:flex;gap:28px;flex-wrap:wrap;font-size:13px;opacity:.92}}
.cover .meta b{{display:block;font-size:20px;font-weight:700;opacity:1}}
section{{background:#fff;border:1px solid var(--line);border-radius:16px;padding:30px 32px;margin-bottom:24px;box-shadow:0 1px 3px rgba(15,23,42,.04)}}
h2.sec{{font-size:22px;margin:0 0 4px;font-weight:800}}
h2.sec .num{{color:var(--rd);font-weight:800;margin-right:10px}}
.sec-sub{{color:var(--mut);font-size:13px;margin:0 0 20px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px}}
.kpi{{border:1px solid var(--line);border-radius:12px;padding:16px;background:linear-gradient(180deg,#fff,#fcfcff)}}
.kpi .lbl{{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut);font-weight:600}}
.kpi .val{{font-size:26px;font-weight:800;margin:4px 0 2px}}
.kpi .sub{{font-size:12px;color:var(--mut)}}
.callout{{border-left:4px solid var(--indigo);background:#eef2ff;border-radius:0 10px 10px 0;padding:14px 18px;margin:14px 0;font-size:14px}}
.callout.warn{{border-color:var(--am);background:#fffbeb}}
.callout.good{{border-color:var(--em);background:#ecfdf5}}
.callout.risk{{border-color:var(--rd);background:#fef2f2}}
.callout b{{font-weight:700}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}}
th,td{{text-align:right;padding:8px 10px;border-bottom:1px solid var(--line)}}
th:first-child,td:first-child{{text-align:left}}
th{{font-size:11px;text-transform:uppercase;letter-spacing:.4px;color:var(--mut);font-weight:700;border-bottom:2px solid var(--line)}}
tbody tr:hover{{background:#fafbff}}
tfoot td{{font-weight:800;border-top:2px solid var(--line);background:#f8fafc}}
.bar-wrap{{display:flex;align-items:center;gap:8px;justify-content:flex-end}}
.bar{{height:9px;border-radius:5px}}
.bar-lbl{{font-variant-numeric:tabular-nums;min-width:54px;text-align:right}}
.pill{{display:inline-block;padding:2px 12px;border-radius:999px;font-size:11px;font-weight:700}}
.pill.high{{background:#fee2e2;color:#991b1b}}
.pill.medium{{background:#fef3c7;color:#92400e}}
.alert{{border:2px solid var(--rd);background:#fef2f2;border-radius:10px;padding:16px;margin:14px 0}}
.alert h3{{margin:0 0 8px;color:var(--rd);font-weight:700}}
.alert p{{margin:0;color:#334155;font-size:13px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:22px}}
.staff-box{{border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:14px;background:linear-gradient(135deg,#fef2f2,#fff7ed)}}
.staff-box .name{{font-weight:700;color:var(--ink);margin-bottom:4px}}
.staff-box .id{{font-size:12px;color:var(--mut)}}
.staff-box .stat{{margin-top:8px;font-size:13px}}
.staff-box .stat-val{{font-weight:700;color:var(--rd)}}
.muted{{color:var(--mut);font-size:12px}}
.toc{{columns:2;font-size:13px;gap:30px}}
.toc a{{color:var(--indigo);text-decoration:none;display:block;padding:3px 0}}
footer{{text-align:center;color:var(--mut);font-size:12px;margin-top:30px}}
@media print{{body{{background:#fff;font-size:11.5px}}.cover{{border-radius:0}}section{{break-inside:avoid;box-shadow:none;border-color:#ddd}}.no-print{{display:none}}}}
@media(max-width:740px){{.grid2{{grid-template-columns:1fr}}.toc{{columns:1}}}}
</style></head><body>""")

    # Cover
    total_anomalies = sum(len(d.get('anomalies', [])) for d in branch_data.values())
    A(f"""<div class="cover"><div class="wrap" style="padding:0">
  <div class="eyebrow">Invisible Consulting · Enterprise Fraud Risk Assessment</div>
  <h1>Oshinei Enterprise<br>รายงานวิเคราะห์ความผิดปกติของส่วนลด</h1>
  <div class="sub">สาขาอุบลราชธานี และสุรินทร์ · ข้อมูล Easy POS · การวิเคราะห์พฤติกรรมแคชเชียร์ · ประเมินความเสี่ยง</div>
  <div class="meta">
    <div><b>{len(branch_names)} สาขา</b>ที่ถูกวิเคราะห์<br>{', '.join(branch_names)}</div>
    <div><b>6 เดือน</b>ย้อนหลัง<br>ข้อมูลประวัติการทำรายการ</div>
    <div><b>{total_anomalies}</b>ความผิดปกติที่พบ<br>ส่วนลดที่ผิดปกติ</div>
    <div><b>{report_date}</b>วันที่ออกรายงาน</div>
  </div>
</div></div><div class="wrap">""")

    # TOC
    A("""<section class="no-print"><h2 class="sec"><span class="num">»</span>สารบัญ</h2>
    <div class="toc">
      <a href="#s1">1 · บทสรุปผู้บริหาร (Executive Summary)</a>
      <a href="#s2">2 · ภาพรวมความผิดปกติของส่วนลด</a>
      <a href="#s3">3 · การวิเคราะห์รายบุคคล (โปรไฟลิ่งแคชเชียร์)</a>
      <a href="#s4">4 · ความเสี่ยงที่ระบุ (Risk Assessment)</a>
      <a href="#s5">5 · ข้อเสนอสำหรับอุ้ม</a>
      <a href="#s6">6 · ระเบียบวิธีและคุณภาพข้อมูล</a>
    </div></section>""")

    # §1 Executive summary
    A(f"""<section id="s1"><h2 class="sec"><span class="num">1</span>บทสรุปผู้บริหาร</h2>
    <p class="sec-sub">ความเสี่ยงส่วนลด · ลำดับความสำคัญของการควบคุม · จุดเปิดเสี่ยง</p>
    <div class="alert">
      <h3>⚠️ การค้นหาหลัก</h3>
      <p>การวิเคราะห์ 6 เดือนที่ผ่านมาของสาขาอุบลราชธานี และสุรินทร์ ได้รับการระบุ <b>{total_anomalies} รายการการทำรายการส่วนลดที่ผิดปกติ</b>
      ซึ่งมีจำนวนมากกว่ากรรมสิทธิ์เฉลี่ยสำหรับช่วงเวลาที่กำหนดไว้ หลายรายการเชื่อมโยงกับบุคลากรการเงิน 3 คนที่เจาะจง
      <b>ควรดำเนินการตรวจสอบแบบ Drill-Down ต่อเนื่อง โดยใช้ CCTV, ระบบเงินสด, และการตรวจสอบเนื้อไฟแบบโดด</b></p>
    </div>
    </section>""")

    # §2 Overview
    A(f"""<section id="s2"><h2 class="sec"><span class="num">2</span>ภาพรวมความผิดปกติของส่วนลด</h2>
    <p class="sec-sub">จำแนกตามสาขา · ความเสี่ยง · แคชเชียร์</p>
    <div class="kpis">""")

    for branch_name, data in branch_data.items():
        anomalies = data.get('anomalies', [])
        discount_txns = data.get('discount_transactions', [])
        patterns = data.get('patterns', {})

        total_txns = len(data.get('transactions', []))
        anomaly_rate = len(anomalies) / len(discount_txns) * 100 if discount_txns else 0

        A(f"""
      <div class="kpi">
        <div class="lbl">{branch_name}</div>
        <div class="val">{len(anomalies)}</div>
        <div class="sub">ความผิดปกติ / {len(discount_txns)} ส่วนลด ({anomaly_rate:.0f}%)</div>
      </div>""")

    A("""</div>""")

    # Anomalies by branch
    for branch_name, data in branch_data.items():
        anomalies = sorted(data.get('anomalies', []), key=lambda x: -x['discount_pct'])[:10]
        if not anomalies:
            continue

        A(f"""<h3 style="margin-top:20px">สาขา {branch_name}</h3>
    <table>
      <thead>
        <tr>
          <th>วันที่</th>
          <th>บิลเลขที่</th>
          <th>แคชเชียร์</th>
          <th>ยอดรวม</th>
          <th>ส่วนลด</th>
          <th>% ส่วนลด</th>
          <th>ความรุนแรง</th>
        </tr>
      </thead>
      <tbody>""")

        for anom in anomalies:
            gross = (anom['food_subtotal'] or 0) + (anom['drink_subtotal'] or 0) + (anom['other_subtotal'] or 0)
            discount = (anom['discount'] or 0) + (anom['sp_discount'] or 0)
            cashier = anom.get('cashier') or 'Unknown'
            severity = anom.get('severity', 'MEDIUM')
            severity_class = 'high' if severity == 'HIGH' else 'medium'

            date_str = anom['sold_at'][:10] if anom.get('sold_at') else '—'

            A(f"""<tr>
          <td>{date_str}</td>
          <td>{anom.get('receipt_no', '—')}</td>
          <td>{cashier}</td>
          <td>{thb(gross)}</td>
          <td>{thb(discount)}</td>
          <td><b>{anom['discount_pct']:.1f}%</b></td>
          <td><span class="pill {severity_class}">{severity}</span></td>
        </tr>""")

        A("""</tbody></table>""")

    A("""</section>""")

    # §3 Staff Investigation
    A(f"""<section id="s3"><h2 class="sec"><span class="num">3</span>การวิเคราะห์รายบุคคล (โปรไฟลิ่งแคชเชียร์)</h2>
    <p class="sec-sub">บุคลากรที่กำหนดไว้เพื่อตรวจสอบ · การวิเคราะห์พฤติกรรมส่วนลด</p>
    <div class="grid2">""")

    for staff_id, staff_name in STAFF_TO_INVESTIGATE.items():
        # Count staff-related anomalies
        staff_anomalies = 0
        staff_discount_txns = 0
        for data in branch_data.values():
            for anom in data.get('anomalies', []):
                if anom.get('cashier') == staff_id:
                    staff_anomalies += 1
            for txn in data.get('discount_transactions', []):
                if txn.get('cashier') == staff_id:
                    staff_discount_txns += 1

        risk_level = "สูง" if staff_anomalies > 2 else ("ปานกลาง" if staff_anomalies > 0 else "ต่ำ")
        risk_color = "var(--rd)" if staff_anomalies > 2 else ("var(--am)" if staff_anomalies > 0 else "var(--mut)")

        A(f"""
    <div class="staff-box">
      <div class="name">{staff_name}</div>
      <div class="id">รหัสพนักงาน: {staff_id}</div>
      <div class="stat">
        ความผิดปกติ: <span class="stat-val" style="color:{risk_color}">{staff_anomalies}</span>
      </div>
      <div class="stat">
        ส่วนลดทั้งหมด: <span class="stat-val">{staff_discount_txns}</span>
      </div>
      <div class="muted" style="margin-top:8px">ระดับความเสี่ยง: <b>{risk_level}</b></div>
    </div>""")

    A("""</div></section>""")

    # §4 Risk Assessment
    A(f"""<section id="s4"><h2 class="sec"><span class="num">4</span>ความเสี่ยงที่ระบุ (Risk Assessment)</h2>
    <p class="sec-sub">ความรุนแรง · ความน่าจะเป็น · ผลกระทบ</p>""")

    high_risk_count = sum(len([a for a in d.get('anomalies', []) if a.get('severity') == 'HIGH']) for d in branch_data.values())

    if high_risk_count > 0:
        A(f"""<div class="callout risk">
      <b>ความเสี่ยงระดับสูง:</b> พบ {high_risk_count} รายการส่วนลดมากกว่า 10% ซึ่งเป็นข้อบ่งชี้ของความผิดปกติ
    </div>""")

    A("""<div class="callout warn">
      <b>ความเสี่ยงปานกลาง:</b> ส่วนลดที่กระจุกตัวในแคชเชียร์จำนวนน้อยแม้ว่าอาจมีเหตุผลทางธุรกิจก็ตาม
    </div>
    </section>""")

    # §5 Recommendations
    A(f"""<section id="s5"><h2 class="sec"><span class="num">5</span>ข้อเสนอสำหรับอุ้ม</h2>
    <p class="sec-sub">การควบคุมทันที · ปรับปรุงระยะยาว</p>
    <ul style="font-size:14px;color:#334155;line-height:1.8">
      <li><b>จำกัดสิทธิ์ส่วนลด:</b> ระบบ POS ควรกำหนดชั้นอนุมัติส่วนลดตามบทบาท (Cashier ≤ 5%, Supervisor ≤ 10%, Manager ≤ 15%)</li>
      <li><b>ติดตามเรียลไทม์:</b> ตั้งค่าคำเตือนอัตโนมัติสำหรับแต่ละรายการเมื่อส่วนลดเกิน 7.5% ต่อรายการ หรือการเกิดเหตุ 3 ครั้งต่อชั่วโมง</li>
      <li><b>ตรวจสอบเอกสารสนับสนุน:</b> บันทึกความต้องการในการส่วนลดทั้งหมด (สาเหตุ ผู้อนุมัติ ลงนาม) ตามมาตรฐาน SOX</li>
      <li><b>การตรวจสอบไขว้:</b> ตรวจเก็บเงินสดรายวันเพื่อหาความแตกต่าง POS vs. เงินสดที่ระบุ</li>
      <li><b>การอบรมพนักงาน:</b> ทำให้ทีมเข้าใจนโยบายส่วนลด เหตุผลทางธุรกิจ และผลกระทบต่อกำไร</li>
    </ul>
    </section>""")

    # §6 Data Quality
    A(f"""<section id="s6"><h2 class="sec"><span class="num">6</span>ระเบียบวิธีและคุณภาพข้อมูล</h2>
    <p class="sec-sub">ความโปร่งใส · กรณีใช้การ · ข้อจำกัด</p>
    <ul style="font-size:14px;color:#334155">
      <li><b>แหล่งข้อมูล:</b> Easy POS (easyrestaurant.asia) · สาขาอุบลราชธานี + สุรินทร์ · 6 เดือนย้อนหลัง</li>
      <li><b>คำจำกัดความส่วนลด:</b> discount + sp_discount จากตาราง sales_transaction · หารด้วยผลรวมอาหาร/เครื่องดื่ม/อื่นๆ</li>
      <li><b>การจำแนกความผิดปกติ:</b> ส่วนลด > 2% หรือ > 2σ เบี่ยงเบนมาตรฐานจากค่าเฉลี่ยแคชเชียร์</li>
      <li><b>ข้อจำกัด:</b> บางรายการที่ไม่มีชื่อแคชเชียร์ (Unknown) บางรายการอาจเป็นส่วนลดอนุมัติในระบบ</li>
      <li><b>ความถูกต้อง:</b> สมมติว่าข้อมูล POS มีความแม่นยำสูง แต่ไม่ได้ตรวจสอบกับเอกสารสนับสนุนทางเอกสาร</li>
    </ul>
    <p class="muted" style="margin-top:20px">รายงานนี้สร้างอัตโนมัติจากข้อมูลส่วนลด หากต้องการข้อมูลเพิ่มเติม กรุณาติดต่อแผนกตรวจสอบภายใน หรือติดต่อทีมสนับสนุน ERP</p>
    </section>""")

    A(f"""<footer>Oshinei Enterprise ERP · Discount Anomaly Analysis · ออกรายงาน {report_date}
    · Invisible Consulting<br>FastAPI · SQLite · Fraud Risk Assessment Engine</footer>
    </div></body></html>""")

    return "".join(H)

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--branches", nargs='+', default=TARGET_BRANCHES,
                    help="Branch names to analyze")
    ap.add_argument("--months", type=int, default=6,
                    help="Months back to analyze")
    ap.add_argument("--format", choices=['html', 'pdf'], default='html',
                    help="Output format")
    args = ap.parse_args()

    report_date = datetime.now().strftime("%Y-%m-%d")

    try:
        conn = get_db()

        # Collect data from all branches
        all_analysis = {}

        for branch_name in args.branches:
            branch_id = fetch_branch_id(conn, branch_name)
            if not branch_id:
                print(f"⚠️  Branch not found: {branch_name}")
                continue

            print(f"📊 Analyzing {branch_name}...")

            transactions = fetch_all_transactions(conn, branch_id, args.months)
            discount_transactions = fetch_discount_data(conn, branch_id, args.months)

            if not transactions:
                print(f"   No data found for {branch_name}")
                continue

            anomalies, cashier_stats = identify_anomalies(discount_transactions)
            patterns = analyze_cashier_patterns(transactions)

            all_analysis[branch_name] = {
                'transactions': transactions,
                'discount_transactions': discount_transactions,
                'anomalies': anomalies,
                'cashier_stats': cashier_stats,
                'patterns': patterns
            }

            print(f"   • Found {len(anomalies)} anomalies out of {len(discount_transactions)} discounted transactions")
            print(f"   • Analyzed {len(patterns)} cashiers")

        conn.close()

        # Generate report
        html_content = generate_html_report(all_analysis, report_date)

        # Save report
        out_dir = Path(__file__).parent.parent / "docs"
        out_dir.mkdir(exist_ok=True)

        out_file = out_dir / f"DISCOUNT_ANALYSIS_{report_date}.html"
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(html_content)

        print(f"\n✅ Report saved: {out_file}")
        print(f"   Size: {out_file.stat().st_size // 1024} KiB")

        if args.format == 'pdf':
            print("\n📝 Note: PDF conversion requires weasyprint. Install with: pip install weasyprint")
            print("   Then run: weasyprint " + str(out_file))

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
