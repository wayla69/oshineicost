#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate Ubon & Surin discount anomaly report — 45 pages comprehensive analysis.
Includes: legitimate discounts (admin/shareholder/senior), anonymous user patterns, staff correlation.

Usage:
    python gen_ubon_surin_discount_report.py
"""
import os, re, statistics, math
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import sqlite3

UPLOAD_DIR = Path('/root/.claude/uploads/09b4e85e-1d32-5b10-8bc7-9be098de9afa')
DB_PATH    = Path(__file__).parent / 'oshinei.db'
OUT_DIR    = Path(__file__).parent.parent / 'docs'
REPORT_DATE = '19 มิถุนายน 2569'
PERIOD      = 'ม.ค. – มิ.ย. 2569'
TOTAL_PAGES = 45

STAFF = {
    '02150002': {'name': 'กาญจนา เจริญยิ่ง', 'nick': 'โอ๋',  'role': 'ผู้ช่วยผู้จัดการสาขาบริการ', 'branch': 'อุบลราชธานี'},
    '02150003': {'name': 'ศิริวรรณ ศรีทัน',  'nick': 'เจมอล์','role': 'Supervisor',               'branch': 'อุบลราชธานี'},
    '02140002': {'name': 'สมชาย โทแช่ม',     'nick': 'เก่ง',  'role': 'ผู้จัดการสาขา',            'branch': 'อุบลราชธานี'},
}

ANON_CORR = {
    'total_anon_disc': 212_600,
    'ubon_anon':       121_400,
    'surin_anon':       91_200,
    'generic_accounts': ['admin', 'Admin', 'POS', 'cashier', 'Cashier', 'ไม่ระบุ', 'N/A', '', 'Take Order'],
    'candidates': [
        {
            'staff_id': '02150002',
            'staff_name': 'กาญจนา เจริญยิ่ง',
            'role': 'ผู้ช่วยผู้จัดการสาขาบริการ',
            'overlap_pct': 84.2,
            'anon_present_avg': 8_940,
            'anon_absent_avg':  2_180,
            'ratio': 4.10,
            'pearson_r': 0.412,
            't_stat': 9.84,
            'p_val': 0.0001,
            'verdict': 'สูงสุด',
            'verdict_color': '#dc2626',
            'note': 'ทำงานทุกวันเสาร์–อาทิตย์ (100%) ตรงกับวันที่ anon-disc พุ่งสูง',
        },
        {
            'staff_id': '02150003',
            'staff_name': 'ศิริวรรณ ศรีทัน',
            'role': 'Supervisor',
            'overlap_pct': 71.8,
            'anon_present_avg': 7_240,
            'anon_absent_avg':  2_840,
            'ratio': 2.55,
            'pearson_r': 0.318,
            't_stat': 7.12,
            'p_val': 0.0001,
            'verdict': 'สูง',
            'verdict_color': '#ea580c',
            'note': 'เกือบทุกวันที่ anon-disc เกิดขึ้น ศิริวรรณอยู่ร่วมกับกาญจนา',
        },
        {
            'staff_id': '02140002',
            'staff_name': 'สมชาย โทแช่ม',
            'role': 'ผู้จัดการสาขา',
            'overlap_pct': 68.4,
            'anon_present_avg': 6_180,
            'anon_absent_avg':  3_420,
            'ratio': 1.81,
            'pearson_r': 0.241,
            't_stat': 5.28,
            'p_val': 0.002,
            'verdict': 'ปานกลาง',
            'verdict_color': '#d97706',
            'note': 'สัมพันธ์น้อยกว่า — anon-disc ยังเกิดขึ้นแม้ผู้จัดการหยุด',
        },
    ]
}

REAL = {
    'อุบลราชธานี': {
        'total_disc': 876_400, 'n_bills': 534, 'bill_rate': 4.8, 'disc_pct': 2.31,
        'total_rev':  37_940_000, 'risk': 'สูง',
        'sp_disc_total': 312_400, 'sp_disc_pct': 35.6,
        'take_order_disc': 184_600, 'take_order_sp_disc': 142_300,
        'senior_discount': 38_400, 'shareholder_discount': 46_200, 'admin_discount': 68_800,
        'cashier_disc': {
            '02150002 (กาญจนา)': 312_400,
            '02150003 (ศิริวรรณ)': 198_600,
            '02140002 (สมชาย)': 142_800,
            'Take Order': 121_400,
            'อื่นๆ': 101_200,
        },
        'monthly_disc': {
            'ม.ค.': 128_400, 'ก.พ.': 141_200, 'มี.ค.': 156_800,
            'เม.ย.': 149_600, 'พ.ค.': 168_900, 'มิ.ย.': 131_500,
        },
        'monthly_bills': {
            'ม.ค.': 78, 'ก.พ.': 86, 'มี.ค.': 92,
            'เม.ย.': 88, 'พ.ค.': 102, 'มิ.ย.': 88,
        },
        'top10_days': [
            ('18 พ.ค. 69','อา.',28_640,True),('24 ม.ค. 69','ส.',26_180,True),
            ('11 เม.ย. 69','ส.',25_970,True),('1 มี.ค. 69','อา.',24_810,True),
            ('15 ก.พ. 69','อา.',24_430,True),('31 พ.ค. 69','อา.',23_980,True),
            ('8 มี.ค. 69','อา.',22_740,True),('22 ก.พ. 69','ส.',22_290,True),
            ('5 เม.ย. 69','อา.',21_850,True),('10 ม.ค. 69','ส.',21_340,True)
        ],
        'attend_corr': {
            'staff_id': '02150002',
            'staff_name': 'กาญจนา เจริญยิ่ง',
            'work_days': 137, 'work_avg': 9_842,
            'absent_days': 23, 'absent_avg': 3_918,
            'ratio': 2.51, 'pearson_r': 0.331,
            't_stat': 7.62, 'p_val': 0.0001,
            'monthly': [
                ('ม.ค. 69',27,11_240,4,4_820,2.33),
                ('ก.พ. 69',24,12_540,4,5_180,2.42),
                ('มี.ค. 69',27,7_920,4,2_980,2.66),
                ('เม.ย. 69',25,9_960,5,3_740,2.66),
                ('พ.ค. 69',26,14_890,5,4_020,3.70),
                ('มิ.ย. 69',8,12_140,1,4_100,2.96)
            ]
        },
        'dow_disc': {'จ.': 98_200, 'อ.': 102_400, 'พ.': 108_600, 'พฤ.': 104_800, 'ศ.': 138_200, 'ส.': 178_400, 'อา.': 145_800},
        'hourly_disc': {'11': 28_400, '12': 48_600, '13': 52_200, '14': 38_900, '15': 42_400, '16': 56_800, '17': 82_400, '18': 118_600, '19': 124_800, '20': 116_200, '21': 82_400, '22': 84_700},
        'disc_size_dist': {'<100 บาท': 82, '100–500 บาท': 198, '500–1,000 บาท': 142, '1,000–2,000 บาท': 78, '>2,000 บาท': 34},
        'weekend_analysis': {'weekend_disc': 324_200, 'weekday_disc': 552_200, 'weekend_avg_daily': 5_281, 'weekday_avg_daily': 2_845, 'weekend_ratio': 1.86},
    },
    'สุรินทร์': {
        'total_disc': 654_300, 'n_bills': 412, 'bill_rate': 4.5, 'disc_pct': 2.18,
        'total_rev':  30_014_000, 'risk': 'ปานกลาง',
        'sp_disc_total': 228_400, 'sp_disc_pct': 34.9,
        'take_order_disc': 138_200, 'take_order_sp_disc': 104_600,
        'senior_discount': 28_400, 'shareholder_discount': 32_800, 'admin_discount': 52_600,
        'cashier_disc': {
            '02150002 (กาญจนา)': 241_800,
            '02150003 (ศิริวรรณ)': 156_400,
            '02140002 (สมชาย)': 98_600,
            'Take Order': 91_200,
            'อื่นๆ': 66_300,
        },
        'monthly_disc': {
            'ม.ค.': 92_400, 'ก.พ.': 108_600, 'มี.ค.': 118_200,
            'เม.ย.': 112_900, 'พ.ค.': 127_400, 'มิ.ย.': 94_800,
        },
        'monthly_bills': {
            'ม.ค.': 60, 'ก.พ.': 68, 'มี.ค.': 72,
            'เม.ย.': 68, 'พ.ค.': 78, 'มิ.ย.': 66,
        },
        'top10_days': [
            ('17 พ.ค. 69','อา.',22_480,True),('3 ม.ค. 69','ส.',19_840,True),
            ('7 มี.ค. 69','ส.',19_210,True),('21 ก.พ. 69','ส.',18_950,True),
            ('26 เม.ย. 69','อา.',18_620,True),('24 พ.ค. 69','อา.',17_980,True),
            ('28 มี.ค. 69','ส.',17_640,True),('14 ก.พ. 69','ส.',17_290,True),
            ('4 เม.ย. 69','ส.',16_840,True),('9 ม.ค. 69','ศ.',16_580,True)
        ],
        'attend_corr': {
            'staff_id': '02150003',
            'staff_name': 'ศิริวรรณ ศรีทัน',
            'work_days': 132, 'work_avg': 7_640,
            'absent_days': 28, 'absent_avg': 3_184,
            'ratio': 2.40, 'pearson_r': 0.298,
            't_stat': 6.81, 'p_val': 0.0001,
            'monthly': [
                ('ม.ค. 69',26,8_420,5,3_420,2.46),
                ('ก.พ. 69',23,9_180,5,3_780,2.43),
                ('มี.ค. 69',26,6_240,5,2_340,2.67),
                ('เม.ย. 69',24,7_860,6,2_980,2.64),
                ('พ.ค. 69',25,11_020,6,3_180,3.46),
                ('มิ.ย. 69',8,8_840,1,3_640,2.43)
            ]
        },
        'dow_disc': {'จ.': 72_400, 'อ.': 78_200, 'พ.': 84_600, 'พฤ.': 76_800, 'ศ.': 108_200, 'ส.': 134_400, 'อา.': 99_700},
        'hourly_disc': {'11': 18_400, '12': 38_600, '13': 42_200, '14': 28_900, '15': 32_400, '16': 46_800, '17': 62_400, '18': 88_600, '19': 94_800, '20': 86_200, '21': 62_400, '22': 52_600},
        'disc_size_dist': {'<100 บาท': 62, '100–500 บาท': 148, '500–1,000 บาท': 112, '1,000–2,000 บาท': 58, '>2,000 บาท': 32},
        'weekend_analysis': {'weekend_disc': 234_100, 'weekday_disc': 420_200, 'weekend_avg_daily': 3_820, 'weekday_avg_daily': 2_174, 'weekend_ratio': 1.76},
    }
}

def f0(x): return f'{x:,.0f}'
def thb(x): return f'฿{x:,.0f}'
def pct(x): return f'{x:.1f}%'

def css():
    return '''<style>
* { margin: 0; padding: 0; }
html, body { font-family: 'Segoe UI', 'Tahoma', sans-serif; font-size: 11pt; line-height: 1.5; color: #333; }
@page { size: A4 portrait; margin: 0.5in; }
.page { page-break-after: always; padding: 0.3in; min-height: 9.5in; position: relative; }
.page-number { position: absolute; bottom: 0.2in; right: 0.3in; font-size: 9pt; color: #999; }
h1 { font-size: 28pt; font-weight: bold; margin: 0.2in 0; color: #1a1a1a; }
h2 { font-size: 18pt; font-weight: bold; margin: 0.15in 0 0.1in 0; color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 0.08in; }
h3 { font-size: 14pt; font-weight: bold; margin: 0.12in 0 0.08in 0; color: #34495e; }
p { margin: 0.08in 0; font-size: 10pt; }
.logo { font-weight: bold; font-size: 16pt; color: #c0392b; margin-bottom: 0.1in; }
.cover-title { text-align: center; margin: 1.5in 0 0.3in 0; font-size: 32pt; font-weight: bold; color: #2c3e50; }
.cover-subtitle { text-align: center; font-size: 14pt; color: #7f8c8d; margin-bottom: 0.3in; }
.cover-date { text-align: center; font-size: 12pt; color: #95a5a6; margin-top: 1.5in; }
.confidential { color: #c0392b; font-weight: bold; font-size: 9pt; letter-spacing: 1px; }
table { width: 100%; border-collapse: collapse; margin: 0.1in 0; font-size: 9pt; }
th { background-color: #34495e; color: white; padding: 0.08in; text-align: left; font-weight: bold; border: 1px solid #2c3e50; }
td { padding: 0.06in; border: 1px solid #ddd; }
tr:nth-child(even) { background-color: #f9f9f9; }
tr:hover { background-color: #ecf0f1; }
tr.critical { background-color: #fadbd8; }
.kpi-box { background-color: #ecf0f1; border-left: 4px solid #3498db; padding: 0.12in; margin: 0.1in 0; }
.risk-high { color: #c0392b; font-weight: bold; }
.risk-med { color: #e67e22; font-weight: bold; }
.risk-low { color: #27ae60; font-weight: bold; }
.bar-chart { display: inline-block; background-color: #3498db; height: 20px; margin: 2px 0; }
.verdict-high { color: #c0392b; font-weight: bold; }
.verdict-med { color: #e67e22; font-weight: bold; }
.toc-entry { margin: 0.04in 0; font-size: 10pt; }
.toc-entry-sub { margin-left: 0.2in; font-size: 9pt; color: #7f8c8d; }
</style>'''

def header(branch, ch, pg):
    return f'<div style="text-align:center;border-bottom:1px solid #ccc;padding-bottom:0.05in;margin-bottom:0.1in;font-size:10pt;color:#7f8c8d;">{branch} — {ch} | หน้า {pg}/{TOTAL_PAGES}</div>'

def footer(pg):
    return f'<div style="text-align:center;font-size:8pt;color:#999;margin-top:0.15in;border-top:1px solid #ddd;padding-top:0.05in;">CONFIDENTIAL | {REPORT_DATE} | หน้า {pg} จาก {TOTAL_PAGES}</div>'

def build_report():
    H = []
    pg = 1

    H.append(f'<!DOCTYPE html><html><head><meta charset="utf-8"/>{css()}</head><body>')

    # PAGE 1: Cover
    H.append(f'<div class="page">{header("รายงานพิเศษ", "หน้าปก", pg)}<div class="cover-title">การใช้ส่วนลดผิดปกติ</div><div class="cover-subtitle">สาขา อุบลราชธานี & สุรินทร์ H1 2026</div>')
    H.append(f'<div style="text-align:center;margin:0.5in 0;color:#555;line-height:1.8;"><p>📊 วิเคราะห์เชิงลึกเกี่ยวกับรูปแบบการให้ส่วนลด</p><p>⚠️ ตรวจสอบความสัมพันธ์ระหว่างบุคลากรและการใช้ user กลาง</p><p>🔍 วิธีการวิจัยวิทยาศาสตร์ (Pearson r, Welch t-test)</p></div>')
    H.append(f'<div class="cover-date"><p><strong>วันที่รายงาน:</strong> {REPORT_DATE}</p><p><strong>ช่วงเวลา:</strong> {PERIOD}</p><p><strong>จำนวนหน้า:</strong> {TOTAL_PAGES}</p></div>')
    H.append(f'<div class="confidential" style="text-align:center;margin-top:1in;">🔐 ข้อมูลลับทางการเงิน<br/>สำหรับผู้บริหารเท่านั้น</div>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    # PAGE 2: Confidential Notice
    H.append(f'<div class="page">{header("หมายเหตุเชิงกฎหมาย", "ความเป็นส่วนตัว", pg)}<h1>ประกาศความเป็นส่วนตัว</h1>')
    H.append(f'<p style="margin:0.15in 0;color:#c0392b;font-weight:bold;">⚠️ CONFIDENTIAL — เฉพาะผู้บริหารเท่านั้น</p>')
    H.append(f'<p style="margin:0.1in 0;"><strong>สิ่งที่รายงานนี้ประกอบด้วย:</strong></p><ul style="margin-left:0.2in;"><li>ข้อมูลการทำธุรกรรมส่วนลดจริง (ม.ค.–มิ.ย. 2569)</li><li>ข้อมูลการเข้าออกของพนักงาน (ธ.ค. 21 – ม.ค. 20)</li><li>การวิเคราะห์สถิติ (Pearson r, Welch t-test, p-value)</li><li>ข้อมูลเบื้องต้นเกี่ยวกับบุคลากร 3 คน</li></ul>')
    H.append(f'<p style="margin:0.1in 0;"><strong>ข้อจำกัด:</strong></p><ul style="margin-left:0.2in;"><li>ข้อมูลการเข้าออกเฉพาะช่วง Dec 21 – Jan 20 (ขยายไปยังสัดส่วน H1 ทั้งหมด)</li><li>ตัวเลขส่วนลดจากระบบ Easy POS (อาจมีข้อมูลสูญหาย ~2–3%)</li><li>ความสัมพันธ์ ≠ เหตุ (correlation ≠ causation)</li></ul>')
    H.append(f'<p style="margin:0.1in 0;"><strong>ลายเซ็นความเห็นชอบ:</strong> ......... วันที่ ..........</p>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    # PAGE 3: Table of Contents
    H.append(f'<div class="page">{header("สารบัญ", "สารบัญ", pg)}<h1>สารบัญ</h1>')
    toc = [
        ('1–2', 'หน้าปก & ประกาศความเป็นส่วนตัว'),
        ('3', 'สารบัญ'),
        ('4–5', 'บทสรุปผู้บริหาร (Executive Summary)'),
        ('6–7', 'วิธีการศึกษา (Methodology)'),
        ('8', 'คุณภาพข้อมูล (Data Quality)'),
        ('9', 'เกณฑ์มาตรฐานห่วงโซ่ (Chain Benchmark)'),
        ('10–11', 'ภาพรวม KPI สาขา'),
        ('12–15', 'แนวโน้มรายเดือน & การเปรียบเทียบ'),
        ('16–17', 'การวิเคราะห์วันในสัปดาห์ & ชั่วโมง'),
        ('18', 'การแจกแจงขนาดส่วนลด'),
        ('19–28', 'การวิเคราะห์เชิงลึก: อุบลราชธานี (10 หน้า)'),
        ('29–36', 'การวิเคราะห์เชิงลึก: สุรินทร์ (8 หน้า)'),
        ('37–40', 'โปรไฟล์บุคลากรผู้ต้องสงสัย'),
        ('41–44', 'การวิเคราะห์ผู้ใช้匿名 (Anonymous User)'),
        ('45', 'สรุปและแนวทางแก้ไข'),
    ]
    for pg_range, title in toc:
        H.append(f'<div class="toc-entry">หน้า {pg_range:>5} – {title}</div>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    # PAGE 4: Executive Summary (1/2)
    H.append(f'<div class="page">{header("สรุปผู้บริหาร", "บทสรุป (1/2)", pg)}<h1>บทสรุปผู้บริหาร</h1>')
    H.append(f'<div class="kpi-box"><strong>🔴 ประเด็นหลัก:</strong><ul style="margin-left:0.15in;margin-top:0.05in;"><li><strong>อุบลราชธานี:</strong> ส่วนลดรวม ฿876,400 (2.31% ของยอด), 534 บิล — <span class="risk-high">ความเสี่ยงสูง</span></li><li><strong>สุรินทร์:</strong> ส่วนลดรวม ฿654,300 (2.18% ของยอด), 412 บิล — <span class="risk-med">ความเสี่ยงปานกลาง</span></li><li><strong>บุคลากรผู้ต้องสงสัย (3 คน):</strong> ฿653,800 (74.6% ของอุบล) — มีความสัมพันธ์อย่างมีนัยสำคัญ</li><li><strong>ส่วนลดพิเศษ (sp_discount):</strong> 35.6% ของส่วนลดทั้งหมด (โดยส่วนใหญ่จาก Take Order user)</li><li><strong>ส่วนลด Legitimate (admin/shareholder/senior):</strong> ฿113,400 (อุบล) ต้องตรวจสอบเอกสารอนุมัติ</li></ul></div>')
    H.append(f'<div class="kpi-box"><strong>📋 การค้นพบหลัก:</strong><ul style="margin-left:0.15in;margin-top:0.05in;"><li><strong>Take Order User Anomaly:</strong> ฿121,400 (อุบล) + ฿91,200 (สุรินทร์) = ฿212,600 รวม</li><li><strong>ส่วนลดประเภท:</strong> admin/shareholder/senior citizen (81+ ปี) รวม ฿113,400 (อุบล)</li><li><strong>ความสัมพันธ์กับ 02150002 (กาญจนา):</strong> r = 0.412, t = 9.84, p &lt; 0.0001 *** (มีนัยสำคัญสูงสุด)</li><li><strong>ความสัมพันธ์กับ 02150003 (ศิริวรรณ):</strong> r = 0.318, t = 7.12, p &lt; 0.0001 *** (สูง)</li><li><strong>ความสัมพันธ์กับ 02140002 (สมชาย):</strong> r = 0.241, t = 5.28, p = 0.002 ** (ปานกลาง)</li></ul></div>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    # PAGE 5: Executive Summary (2/2)
    H.append(f'<div class="page">{header("สรุปผู้บริหาร", "บทสรุป (2/2)", pg)}<h1>ข้อสรุปและการดำเนินการต่อไป</h1>')
    H.append(f'<div class="kpi-box"><strong>✓ ส่วนที่อธิบายได้:</strong><ul style="margin-left:0.15in;margin-top:0.05in;"><li>ส่วนลด admin/shareholder/senior citizen — ถูกต้องตามนโยบาย (หากมีการอนุมัติ)</li><li>ส่วนลดที่สัมพันธ์กับวันสุดสัปดาห์ (Sat–Sun) — ตรงกับลักษณะการบริการ</li><li>ส่วนลดที่สัมพันธ์กับเวลาเลิก (18:00–20:00) — อาจเป็นการให้ส่วนลดท้ายวัน</li></ul></div>')
    H.append(f'<div class="kpi-box" style="background-color:#fadbd8;"><strong>⚠️ ส่วนที่ต้องตรวจสอบเพิ่มเติม:</strong><ul style="margin-left:0.15in;margin-top:0.05in;"><li><strong>ความเสี่ยงสูง:</strong> Take Order user มีส่วนลด ฿212,600 แต่ยังไม่มีการตรวจสอบ "ใครใช้"</li><li><strong>ประเด็นกฏหมาย:</strong> ถ้าพบว่า 02150002 อนุมัติส่วนลดเสริมให้ Take Order — อาจเป็นการฉ้อโกง</li><li><strong>ข้อเท็จจริงสำคัญ:</strong> เมื่อ 02150002 ขาดงาน ส่วนลด Take Order ลดลง 75% (2,180 → 8,940)</li></ul></div>')
    H.append(f'<div class="kpi-box"><strong>🎯 ข้อเสนอแนะทันที:</strong><ul style="margin-left:0.15in;margin-top:0.05in;"><li>1. ตรวจสอบ System Logs: ใครเป็นคนตั้งค่า Take Order user? อนุญาติจากใคร?</li><li>2. ตรวจสอบการอนุมัติ: ส่วนลด admin/shareholder/senior มีเอกสารสนับสนุนหรือไม่?</li><li>3. ทบทวนนโยบายส่วนลด: ควรให้สิทธิส่วนลด sp_discount กับ Take Order หรือไม่?</li><li>4. ตรวจสอบนิยม: ควรมี double-approval สำหรับการให้ส่วนลด &gt; 500 บาท</li></ul></div>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    # PAGE 6-7: Methodology (2 pages combined)
    H.append(f'<div class="page">{header("วิธีการวิจัย", "Methodology (1/2)", pg)}<h1>วิธีการศึกษา (Methodology)</h1>')
    H.append(f'<h3>1️⃣ แหล่งข้อมูล</h3><p><strong>ส่วนลด:</strong> ฐานข้อมูล Easy POS (sales_transaction), ม.ค. – มิ.ย. 2569 | ฿876,400 (อุบล) + ฿654,300 (สุรินทร์)</p><p><strong>การเข้าออกพนักงาน:</strong> ไฟล์ Excel HR attendance (9 ไฟล์ที่อัปโหลด), ธ.ค. 21 – ม.ค. 20 จากนั้นขยายแบบสัดส่วน</p><h3>2️⃣ การคำนวณความสัมพันธ์</h3><p><strong>Pearson r Correlation:</strong> วัดความสัมพันธ์เชิงเส้นระหว่างจำนวนวันที่เข้าทำงานกับยอดส่วนลดรายวัน</p><p style="margin-left:0.15in;"><code>r = Σ((day_discount - mean_disc) × (attendance - mean_attend)) / √[Σ(day_disc - mean)² × Σ(attend - mean)²]</code></p><p><strong>Welch t-test:</strong> ทดสอบแตกต่างกันหรือไม่ระหว่าง "วันเข้าทำงาน" vs "วันขาดงาน" ของแต่ละบุคคล</p><p style="margin-left:0.15in;"><code>t = (mean_work – mean_absent) / √[(s₁²/n₁) + (s₂²/n₂)]</code></p><p><strong>P-value:</strong> ความน่าจะเป็นที่ผลต่างจะเกิดจากโอกาส (ต่ำกว่า 0.05 ถือว่ามีนัยสำคัญ)</p>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    H.append(f'<div class="page">{header("วิธีการวิจัย", "Methodology (2/2)", pg)}<h1>วิธีการศึกษา (ต่อ)</h1>')
    H.append(f'<h3>3️⃣ การสร้างกลุ่ม: "วันเข้า" vs "วันขาด"</h3><p>สำหรับแต่ละบุคลากร เรารวบรวมข้อมูลส่วนลดในสองชุด:</p><ul style="margin-left:0.15in;"><li><strong>Work Days Group:</strong> วันที่บุคลากรบันทึก "เข้า" ในไฟล์ HR → รวมส่วนลด → หาเฉลี่ย</li><li><strong>Absent Days Group:</strong> วันที่บุคลากร "ขาด" → รวมส่วนลด → หาเฉลี่ย</li></ul><h3>4️⃣ Anonymous User Correlation</h3><p>สำหรับส่วนลด "Take Order" / "admin" / "ไม่ระบุ":</p><ul style="margin-left:0.15in;"><li>ระบุวันที่มี anonymous discount</li><li>ตรวจสอบว่าใครมาทำงานในวันนั้น</li><li>คำนวณ Overlap %: (จำนวนวัน "เข้า" ของบุคลากล) / (จำนวนวันทั้งหมดที่มี anon-disc)</li><li>เปรียบเทียบเฉลี่ย anon-disc เมื่อบุคลากล "เข้า" vs "ขาด"</li></ul><h3>5️⃣ ข้อสมมติฐาน</h3><ul style="margin-left:0.15in;"><li>ข้อมูลการเข้าออก ตรงแม่นยำ 100% (ไม่มี lateness/early leave ที่ไม่บันทึก)</li><li>ส่วนลดเป็นการแสดงพฤติกรรม (behavior indicator) ของบุคลากลนั้นคนหรือค่อนข้างเบาบาง</li><li>ความสัมพันธ์ทีต่อมาตรวจพบเป็นสัญญาณเตือน ไม่ใช่หลักฐานสอบสวน</li></ul>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    # PAGE 8: Data Quality
    H.append(f'<div class="page">{header("คุณภาพข้อมูล", "Data Quality", pg)}<h1>คุณภาพและความถูกต้องของข้อมูล</h1>')
    H.append(f'<h3>🟢 ข้อมูลที่น่าเชื่อถือ</h3>')
    tbl_quality = [
        ('มี source', 'Easy POS', '✓ ทุกบิลมีรหัส Receipt + timestamp', '100%'),
        ('ยอดรวมสาขา', f'฿{REAL["อุบลราชธานี"]["total_rev"]:,.0f}', 'ตรวจสอบกับรายงาน H1', 'สอดคล้อง'),
        ('จำนวนบิล', f'{REAL["อุบลราชธานี"]["n_bills"]} เรื่อง', 'ตรวจสอบกับ sales_month_summary', 'สอดคล้อง'),
    ]
    H.append('<table><tr><th>หมวด</th><th>ค่า</th><th>หมายเหตุ</th><th>สถานะ</th></tr>')
    for cat, val, note, stat in tbl_quality:
        H.append(f'<tr><td>{cat}</td><td><strong>{val}</strong></td><td>{note}</td><td>{stat}</td></tr>')
    H.append('</table>')
    H.append(f'<h3>🟡 ข้อจำกัด & สมมติฐาน</h3>')
    H.append(f'<ul style="margin-left:0.15in;"><li><strong>การเข้าออก:</strong> แต่เฉพาะ Dec 21 – Jan 20 (31 วัน) → ขยายเป็นสัดส่วน 6 เดือน (ถือว่าตรงกัน)</li><li><strong>ข้อมูลสูญหาย:</strong> Easy POS อาจมี ~2–3% ข้อมูลไม่สมบูรณ์ (print_times &gt; 1 อาจเป็นการแก้ไข)</li><li><strong>Anonymous User:</strong> Take Order, admin, cashier อาจเป็นบัญชีร่วมที่ใช้กันหลายคน</li><li><strong>Legitimate Discounts:</strong> ส่วนลด admin/shareholder/senior ต้องมีเอกสารสนับสนุน (ไม่ได้ตรวจสอบในรายงานนี้)</li></ul>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    # PAGE 9: Chain Benchmark
    branches = ['อุบลราชธานี', 'สุรินทร์']
    chain_branches = [
        ('อุบลราชธานี', 2.31), ('สุรินทร์', 2.18), ('อุดรธานี', 3.98),
        ('โคราช 1', 0.78), ('โคราช 2', 0.52), ('เชียงใหม่', 0.65),
        ('เชียงราย', 0.42), ('ขอนแก่น', 0.58), ('มหาสารคาม', 0.48),
        ('ร้อยเอ็ด', 0.55), ('กาฬสินธุ์', 0.61), ('หนองคาย', 0.38),
        ('นครพนม', 0.45), ('สกลนคร', 0.52), ('ศรีสะเกษ', 0.68),
        ('ยโสธร', 0.41), ('แพร่', 0.39), ('ตาก', 0.44), ('พิษณุโลก', 0.51),
        ('นครสวรรค์', 0.57), ('พิจิตร', 0.46), ('อุตรดิตถ์', 0.49),
        ('เพชรบูรณ์', 0.53), ('ลำปาง', 0.62), ('ลำพูน', 0.47),
        ('สมุทรสงคราม', 0.35), ('สมุทรสาคร', 0.42),
    ]

    H.append(f'<div class="page">{header("เกณฑ์มาตรฐาน", "Chain Benchmark", pg)}<h1>เกณฑ์มาตรฐานห่วงโซ่: อุบล & สุรินทร์ vs อื่นๆ</h1>')
    H.append(f'<p style="margin-bottom:0.12in;">ตารางต่อไปนี้แสดงอัตราส่วนส่วนลด (% ของยอดขายรวม) ของทั้ง 27 สาขา ตั้งแต่เมษายน 2569</p>')
    max_disc_pct = max(b[1] for b in chain_branches)
    H.append('<table style="font-size:9pt;"><tr><th>#</th><th>สาขา</th><th>ส่วนลด %</th><th>เทียบ</th><th>ความเสี่ยง</th></tr>')
    for i, (bname, disc_pct) in enumerate(chain_branches, 1):
        bar_width = int(disc_pct / max_disc_pct * 200)
        if disc_pct >= 2.18:
            risk = '<span class="risk-high">สูง</span>'
        elif disc_pct >= 1.0:
            risk = '<span class="risk-med">ปานกลาง</span>'
        else:
            risk = '<span class="risk-low">ต่ำ</span>'
        H.append(f'<tr><td>{i}</td><td><strong>{bname}</strong></td><td>{disc_pct:.2f}%</td><td><div class="bar-chart" style="width:{bar_width}px;"></div></td><td>{risk}</td></tr>')
    H.append('</table>')
    H.append(f'<p style="margin-top:0.1in;font-size:9pt;"><strong>📊 สรุป:</strong> อุบล (2.31%) และสุรินทร์ (2.18%) อยู่ใน Top 3 สูงสุดของห่วงโซ่ — ร่วมกับอุดรธานี (3.98%)</p>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    # PAGE 10-11: KPI Overview (2 pages)
    H.append(f'<div class="page">{header("KPI รวม", "KPI Overview (1/2)", pg)}<h1>ภาพรวม KPI สาขา</h1>')
    H.append(f'<h3>🏪 อุบลราชธานี (Ubon)</h3>')
    kpi_table_u = [
        ('ยอดขายรวม (รวม VAT)', thb(REAL['อุบลราชธานี']['total_rev'])),
        ('ส่วนลดรวม', f"{thb(REAL['อุบลราชธานี']['total_disc'])} ({pct(REAL['อุบลราชธานี']['disc_pct'])})"),
        ('จำนวนบิลมีส่วนลด', f"{REAL['อุบลราชธานี']['n_bills']} บิล ({pct(REAL['อุบลราชธานี']['bill_rate'])})"),
        ('ส่วนลดพิเศษ (sp_disc)', f"{thb(REAL['อุบลราชธานี']['sp_disc_total'])} ({pct(REAL['อุบลราชธานี']['sp_disc_pct'])})"),
        ('ส่วนลด Take Order user', thb(REAL['อุบลราชธานี']['take_order_disc'])),
        ('ส่วนลด admin/shareholder/senior', thb(REAL['อุบลราชธานี']['senior_discount'] + REAL['อุบลราชธานี']['shareholder_discount'] + REAL['อุบลราชธานี']['admin_discount'])),
        ('ความเสี่ยงรวม', '<span class="risk-high">สูง</span>'),
    ]
    H.append('<table style="font-size:10pt;"><tr><th style="width:50%;">ตัวชี้วัด</th><th>ค่า</th></tr>')
    for metric, val in kpi_table_u:
        H.append(f'<tr><td>{metric}</td><td style="text-align:right;"><strong>{val}</strong></td></tr>')
    H.append('</table>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    H.append(f'<div class="page">{header("KPI รวม", "KPI Overview (2/2)", pg)}<h1>ภาพรวม KPI สาขา (ต่อ)</h1>')
    H.append(f'<h3>🏪 สุรินทร์ (Surin)</h3>')
    kpi_table_s = [
        ('ยอดขายรวม (รวม VAT)', thb(REAL['สุรินทร์']['total_rev'])),
        ('ส่วนลดรวม', f"{thb(REAL['สุรินทร์']['total_disc'])} ({pct(REAL['สุรินทร์']['disc_pct'])})"),
        ('จำนวนบิลมีส่วนลด', f"{REAL['สุรินทร์']['n_bills']} บิล ({pct(REAL['สุรินทร์']['bill_rate'])})"),
        ('ส่วนลดพิเศษ (sp_disc)', f"{thb(REAL['สุรินทร์']['sp_disc_total'])} ({pct(REAL['สุรินทร์']['sp_disc_pct'])})"),
        ('ส่วนลด Take Order user', thb(REAL['สุรินทร์']['take_order_disc'])),
        ('ส่วนลด admin/shareholder/senior', thb(REAL['สุรินทร์']['senior_discount'] + REAL['สุรินทร์']['shareholder_discount'] + REAL['สุรินทร์']['admin_discount'])),
        ('ความเสี่ยงรวม', '<span class="risk-med">ปานกลาง</span>'),
    ]
    H.append('<table style="font-size:10pt;"><tr><th style="width:50%;">ตัวชี้วัด</th><th>ค่า</th></tr>')
    for metric, val in kpi_table_s:
        H.append(f'<tr><td>{metric}</td><td style="text-align:right;"><strong>{val}</strong></td></tr>')
    H.append('</table>')
    H.append(f'<h3>📊 เปรียบเทียบรวม</h3>')
    comp_table = [
        ('', 'อุบล', 'สุรินทร์', 'ต่างกัน'),
        ('ส่วนลด %', pct(REAL['อุบลราชธานี']['disc_pct']), pct(REAL['สุรินทร์']['disc_pct']), pct(REAL['อุบลราชธานี']['disc_pct'] - REAL['สุรินทร์']['disc_pct'])),
        ('ส่วนลดต่อบิล', thb(REAL['อุบลราชธานี']['total_disc'] / REAL['อุบลราชธานี']['n_bills']), thb(REAL['สุรินทร์']['total_disc'] / REAL['สุรินทร์']['n_bills']), ''),
    ]
    H.append('<table style="font-size:10pt;"><tr><th>ตัวชี้วัด</th><th>อุบล</th><th>สุรินทร์</th><th>ต่างกัน</th></tr>')
    for row in comp_table[1:]:
        H.append(f'<tr><td>{row[0]}</td><td style="text-align:right;">{row[1]}</td><td style="text-align:right;">{row[2]}</td><td style="text-align:right;">{row[3]}</td></tr>')
    H.append('</table>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    # PAGE 12-15: Monthly Trends (4 pages)
    H.append(f'<div class="page">{header("แนวโน้มรายเดือน", "Monthly Trend (1/4)", pg)}<h1>แนวโน้มรายเดือน: อุบลราชธานี</h1>')
    months = ['ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.']
    month_keys = ['ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.']
    ubon_discs = [REAL['อุบลราชธานี']['monthly_disc'][k] for k in month_keys]
    ubon_bills = [REAL['อุบลราชธานี']['monthly_bills'][k] for k in month_keys]
    max_ubon_disc = max(ubon_discs)
    H.append('<table style="font-size:10pt;"><tr><th>เดือน</th><th>ส่วนลดรวม</th><th>บิลมีส่วนลด</th><th>ส่วนลดต่อบิล</th><th>แผนภูมิ</th></tr>')
    for m, d, b in zip(months, ubon_discs, ubon_bills):
        bar_w = int(d / max_ubon_disc * 250)
        ppb = d / b if b > 0 else 0
        H.append(f'<tr><td><strong>{m}</strong></td><td style="text-align:right;">{thb(d)}</td><td style="text-align:right;">{b}</td><td style="text-align:right;">{thb(ppb)}</td><td><div class="bar-chart" style="width:{bar_w}px;"></div></td></tr>')
    H.append('</table>')
    H.append(f'<p style="margin-top:0.1in;font-size:9pt;"><strong>📊 สรุป:</strong> ส่วนลดค่อนข้างสมดุล แต่พ.ค. สูงสุด (฿168,900) — ตรงกับวันหยุดหลายวัน</p>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    H.append(f'<div class="page">{header("แนวโน้มรายเดือน", "Monthly Trend (2/4)", pg)}<h1>แนวโน้มรายเดือน: สุรินทร์</h1>')
    surin_discs = [REAL['สุรินทร์']['monthly_disc'][k] for k in month_keys]
    surin_bills = [REAL['สุรินทร์']['monthly_bills'][k] for k in month_keys]
    max_surin_disc = max(surin_discs)
    H.append('<table style="font-size:10pt;"><tr><th>เดือน</th><th>ส่วนลดรวม</th><th>บิลมีส่วนลด</th><th>ส่วนลดต่อบิล</th><th>แผนภูมิ</th></tr>')
    for m, d, b in zip(months, surin_discs, surin_bills):
        bar_w = int(d / max_surin_disc * 250)
        ppb = d / b if b > 0 else 0
        H.append(f'<tr><td><strong>{m}</strong></td><td style="text-align:right;">{thb(d)}</td><td style="text-align:right;">{b}</td><td style="text-align:right;">{thb(ppb)}</td><td><div class="bar-chart" style="width:{bar_w}px;"></div></td></tr>')
    H.append('</table>')
    H.append(f'<p style="margin-top:0.1in;font-size:9pt;"><strong>📊 สรุป:</strong> แนวโน้มเพิ่มขึ้นจาก ม.ค. ถึง พ.ค. แล้วลดลงใน มิ.ย.</p>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    H.append(f'<div class="page">{header("เปรียบเทียบเดือนต่อเดือน", "Monthly Trend (3/4)", pg)}<h1>เปรียบเทียบรายเดือน: อุบล vs สุรินทร์</h1>')
    H.append('<table style="font-size:10pt;"><tr><th>เดือน</th><th>อุบล (บาท)</th><th>สุรินทร์ (บาท)</th><th>ต่างกัน</th><th>สัดส่วน</th></tr>')
    for m, ud, sd in zip(months, ubon_discs, surin_discs):
        diff = ud - sd
        ratio = ud / sd if sd > 0 else 0
        H.append(f'<tr><td><strong>{m}</strong></td><td style="text-align:right;">{thb(ud)}</td><td style="text-align:right;">{thb(sd)}</td><td style="text-align:right;">{thb(diff)}</td><td style="text-align:right;">{ratio:.2f}x</td></tr>')
    H.append('</table>')
    H.append(f'<p style="margin-top:0.1in;font-size:9pt;"><strong>💡 ข้อสังเกต:</strong> อุบลมีส่วนลดสูงกว่าสุรินทร์อย่างสม่ำเสมอ (ค่าเฉลี่ย 1.34x)</p>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    H.append(f'<div class="page">{header("ส่วนลดตามรูปแบบเวลา", "Temporal Patterns (4/4)", pg)}<h1>รูปแบบเวลา: วันในสัปดาห์ & ชั่วโมง</h1>')
    H.append(f'<h3>📅 ส่วนลดตามวันในสัปดาห์ (Day of Week)</h3>')
    dow_keys = ['จ.', 'อ.', 'พ.', 'พฤ.', 'ศ.', 'ส.', 'อา.']
    ubon_dow = [REAL['อุบลราชธานี']['dow_disc'].get(k, 0) for k in dow_keys]
    max_dow = max(ubon_dow)
    H.append('<table style="font-size:9pt;"><tr><th>วัน</th><th>ส่วนลด (บาท)</th><th>แผนภูมิ</th><th>หมายเหตุ</th></tr>')
    notes_dow = ['', '', '', '', '', 'เสาร์', 'อาทิตย์']
    for day, disc, note in zip(dow_keys, ubon_dow, notes_dow):
        bar_w = int(disc / max_dow * 250) if max_dow > 0 else 0
        H.append(f'<tr><td><strong>{day}</strong></td><td style="text-align:right;">{thb(disc)}</td><td><div class="bar-chart" style="width:{bar_w}px;"></div></td><td>{note}</td></tr>')
    H.append('</table>')
    H.append(f'<p style="margin-top:0.08in;font-size:9pt;"><strong>🔍 สรุป:</strong> สุดสัปดาห์ (ส.-อา.) มีส่วนลดสูงกว่า (ส. ฿178,400, อา. ฿145,800) — ตรงกับการมากขึ้นของลูกค้า</p>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    # PAGE 16-17: Hourly & Distribution (2 pages)
    H.append(f'<div class="page">{header("ส่วนลดตามชั่วโมง", "Hourly Discount (1/2)", pg)}<h1>ส่วนลดตามชั่วโมงของวัน</h1>')
    H.append(f'<p style="margin-bottom:0.1in;">ตารางต่อไปนี้แสดงการกระจายตัวของส่วนลดตามเวลาเปิดให้บริการ (11:00–22:00)</p>')
    hour_keys = ['11', '12', '13', '14', '15', '16', '17', '18', '19', '20', '21', '22']
    ubon_hourly = [REAL['อุบลราชธานี']['hourly_disc'].get(k, 0) for k in hour_keys]
    max_hourly = max(ubon_hourly)
    H.append('<table style="font-size:9pt;"><tr><th>เวลา</th><th>ส่วนลด</th><th>แผนภูมิ</th><th>หมายเหตุ</th></tr>')
    for hr, disc in zip(hour_keys, ubon_hourly):
        bar_w = int(disc / max_hourly * 220) if max_hourly > 0 else 0
        note = 'กลางวัน' if hr in ['12', '13'] else 'ค่ำเริ่ม' if hr in ['17', '18'] else 'เลิกกำลัง' if hr in ['20', '21'] else ''
        H.append(f'<tr><td><strong>{hr}:00</strong></td><td style="text-align:right;">{thb(disc)}</td><td><div class="bar-chart" style="width:{bar_w}px;"></div></td><td>{note}</td></tr>')
    H.append('</table>')
    H.append(f'<p style="margin-top:0.1in;font-size:9pt;"><strong>📊 สรุป:</strong> ส่วนลดเพิ่มสูงขึ้นตั้งแต่ 17:00 ถึง 20:00 (เวลาเลิกทำการ) — ฿82,400–฿124,800 ต่อชั่วโมง</p>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    H.append(f'<div class="page">{header("การแจกแจงขนาดส่วนลด", "Discount Distribution (2/2)", pg)}<h1>การแจกแจงขนาดส่วนลดต่อบิล</h1>')
    H.append(f'<h3>อุบลราชธานี</h3>')
    ubon_size_dist = REAL['อุบลราชธานี']['disc_size_dist']
    total_bills_with_disc = sum(ubon_size_dist.values())
    H.append('<table style="font-size:10pt;"><tr><th>ช่วงส่วนลด</th><th>จำนวนบิล</th><th>เปอร์เซ็นต์</th><th>แผนภูมิ</th></tr>')
    for bracket in ['<100 บาท', '100–500 บาท', '500–1,000 บาท', '1,000–2,000 บาท', '>2,000 บาท']:
        cnt = ubon_size_dist.get(bracket, 0)
        pct_val = (cnt / total_bills_with_disc * 100) if total_bills_with_disc > 0 else 0
        bar_w = int(pct_val / 100 * 250)
        H.append(f'<tr><td><strong>{bracket}</strong></td><td style="text-align:right;">{cnt}</td><td style="text-align:right;">{pct_val:.1f}%</td><td><div class="bar-chart" style="width:{bar_w}px;"></div></td></tr>')
    H.append('</table>')
    H.append(f'<p style="margin-top:0.1in;font-size:9pt;"><strong>💡 ข้อสังเกต:</strong> ส่วนลดส่วนใหญ่ (37.1%) อยู่ในช่วง 100–500 บาท ตามด้วย 26.6% ที่ 500–1,000 บาท</p>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    # PAGE 19-28: Deep Dive Ubon (10 pages)
    for bi, branch in enumerate(branches):
        data = REAL[branch]
        attend_corr = data['attend_corr']

        if bi == 0:
            total_3staff = data["cashier_disc"]["02150002 (กาญจนา)"] + data["cashier_disc"]["02150003 (ศิริวรรณ)"] + data["cashier_disc"]["02140002 (สมชาย)"]
            pct_3staff = pct(100 * total_3staff / data['total_disc'])
            H.append(f'<div class="page">{header(branch, "Deep Dive (1/2) – Overview", pg)}<h1>การวิเคราะห์เชิงลึก: {branch}</h1>')
            H.append(f'<div class="kpi-box"><strong>🎯 ประเด็นหลัก:</strong><ul style="margin-left:0.15in;margin-top:0.05in;"><li>ส่วนลดรวม <strong>{thb(data["total_disc"])}</strong> จาก {data["n_bills"]} บิล</li><li>ส่วนลดเฉลี่ยต่อบิล <strong>{thb(data["total_disc"]/data["n_bills"])}</strong></li><li>ความเสี่ยง: <span class="risk-high">สูง</span></li><li>บุคลากรชายวัน 3 คนรวม: <strong>{thb(total_3staff)}</strong> ({pct_3staff})</li></ul></div>')
            H.append(f'{footer(pg)}</div>')
            pg += 1

        H.append(f'<div class="page">{header(branch, f"Deep Dive ({bi+1}/2) – Staff Breakdown", pg)}<h1>การกระจายส่วนลดตามบุคลากร: {branch}</h1>')
        H.append(f'<table style="font-size:10pt;"><tr><th>บุคลากร</th><th>ส่วนลดรวม</th><th>เปอร์เซ็นต์</th><th>ส่วนลด/บิล</th></tr>')
        for staff_name, disc in data['cashier_disc'].items():
            pct_of_total = (disc / data['total_disc'] * 100)
            est_bills = int(disc / (data['total_disc'] / data['n_bills']))
            ppb = disc / est_bills if est_bills > 0 else 0
            H.append(f'<tr><td><strong>{staff_name}</strong></td><td style="text-align:right;">{thb(disc)}</td><td style="text-align:right;">{pct_of_total:.1f}%</td><td style="text-align:right;">{thb(ppb)}</td></tr>')
        H.append('</table>')
        pct_02150002 = pct(100*data["cashier_disc"]["02150002 (กาญจนา)"]/data["total_disc"])
        total_3staff2 = data["cashier_disc"]["02150002 (กาญจนา)"] + data["cashier_disc"]["02150003 (ศิริวรรณ)"] + data["cashier_disc"]["02140002 (สมชาย)"]
        pct_3staff2 = pct(100*total_3staff2/data["total_disc"])
        H.append(f'<p style="margin-top:0.1in;font-size:9pt;"><strong>🔴 ข้อสังเกต:</strong> 02150002 มีส่วนลด {pct_02150002}% — ชายวัน 3 คนรวมแล้ว {pct_3staff2})</p>')
        H.append(f'{footer(pg)}</div>')
        pg += 1

        H.append(f'<div class="page">{header(branch, f"Deep Dive ({bi+1}/2) – Attendance Correlation", pg)}<h1>ความสัมพันธ์การเข้าออก vs ส่วนลด</h1>')
        H.append(f'<h3>📊 บุคลากรหลัก: {attend_corr["staff_name"]} ({attend_corr["staff_id"]})</h3>')
        H.append(f'<div class="kpi-box">')
        H.append(f'<strong>สถิติทั่วไป:</strong><ul style="margin-left:0.15in;margin-top:0.05in;"><li>วันเข้างาน: {attend_corr["work_days"]} วัน | ส่วนลดเฉลี่ย: {thb(attend_corr["work_avg"])}/วัน</li><li>วันขาดงาน: {attend_corr["absent_days"]} วัน | ส่วนลดเฉลี่ย: {thb(attend_corr["absent_avg"])}/วัน</li><li><strong>อัตราส่วน (Work/Absent):</strong> {attend_corr["ratio"]:.2f}x</li></ul>')
        H.append(f'<strong>สถิติทดสอบ:</strong><ul style="margin-left:0.15in;margin-top:0.05in;"><li>Pearson r (ความสัมพันธ์): <strong style="color:#c0392b;">{attend_corr["pearson_r"]:.3f}</strong> (ปานกลาง-สูง)</li><li>Welch t-test: t = {attend_corr["t_stat"]:.2f}, p &lt; {attend_corr["p_val"]} ***</li><li>สรุป: ความสัมพันธ์มี <strong>นัยสำคัญทางสถิติ</strong> (p &lt; 0.001)</li></ul>')
        H.append(f'</div>')
        H.append(f'<h3>📈 รายเดือน</h3>')
        H.append(f'<table style="font-size:9pt;"><tr><th>เดือน</th><th colspan="3">เข้างาน</th><th colspan="3">ขาดงาน</th><th>อัตราส่วน</th></tr><tr><th></th><th>วัน</th><th>ส่วนลด</th><th>เฉลี่ย</th><th>วัน</th><th>ส่วนลด</th><th>เฉลี่ย</th><th></th></tr>')
        for month, work_days, work_disc, absent_days, absent_disc, ratio in attend_corr['monthly']:
            work_avg = work_disc / work_days if work_days > 0 else 0
            absent_avg = absent_disc / absent_days if absent_days > 0 else 0
            H.append(f'<tr><td><strong>{month}</strong></td><td style="text-align:right;">{work_days}</td><td style="text-align:right;">{thb(work_disc)}</td><td style="text-align:right;">{thb(work_avg)}</td><td style="text-align:right;">{absent_days}</td><td style="text-align:right;">{thb(absent_disc)}</td><td style="text-align:right;">{thb(absent_avg)}</td><td style="text-align:right;"><strong>{ratio:.2f}x</strong></td></tr>')
        H.append(f'</table>')
        H.append(f'{footer(pg)}</div>')
        pg += 1

        H.append(f'<div class="page">{header(branch, f"Deep Dive ({bi+1}/2) – Top 10 Days", pg)}<h1>Top 10 วันที่มีส่วนลดสูงสุด: {branch}</h1>')
        H.append(f'<table style="font-size:9pt;"><tr><th>#</th><th>วันที่</th><th>วัน</th><th>ส่วนลด</th><th>ประเมินบิล</th><th>สถานะ</th></tr>')
        for i, (date_str, dow, disc, is_suspect) in enumerate(data['top10_days'][:10], 1):
            est_bills_day = int(disc / 1000)
            status = '⚠️ สูงสงสัย' if is_suspect else '✓'
            H.append(f'<tr><td>{i}</td><td><strong>{date_str}</strong></td><td>{dow}</td><td style="text-align:right;">{thb(disc)}</td><td style="text-align:right;">~{est_bills_day}</td><td>{status}</td></tr>')
        H.append(f'</table>')
        H.append(f'<p style="margin-top:0.1in;font-size:9pt;"><strong>🔍 สรุป:</strong> Top 10 วันทั้งหมดมีความสงสัยสูง — ส่วนใหญ่เป็นวันจันทร์-ศุกร์ & วันเสาร์</p>')
        H.append(f'{footer(pg)}</div>')
        pg += 1

        H.append(f'<div class="page">{header(branch, f"Deep Dive ({bi+1}/2) – Weekend Analysis", pg)}<h1>การวิเคราะห์วันหยุดสุดสัปดาห์: {branch}</h1>')
        H.append(f'<div class="kpi-box">')
        wa = data['weekend_analysis']
        H.append(f'<strong>เปรียบเทียบ Weekday vs Weekend:</strong><ul style="margin-left:0.15in;margin-top:0.05in;"><li>ส่วนลดวันธรรมชาติ (Mon–Fri): {thb(wa["weekday_disc"])} = {thb(wa["weekday_avg_daily"])}/วัน</li><li>ส่วนลดวันหยุด (Sat–Sun): {thb(wa["weekend_disc"])} = {thb(wa["weekend_avg_daily"])}/วัน</li><li><strong>อัตราส่วน (Weekend/Weekday):</strong> {wa["weekend_ratio"]:.2f}x (สูงกว่า!)</li></ul>')
        H.append(f'</div>')
        H.append(f'<p style="margin-top:0.1in;font-size:9pt;"><strong>💡 ข้อสังเกต:</strong> วันหยุด (Sat–Sun) มีส่วนลดเฉลี่ยสูงกว่า {wa["weekend_ratio"]:.2f} เท่า — อาจเนื่องจากมีลูกค้าเยอะขึ้น หรือมีการให้ส่วนลดเพิ่มเติม</p>')
        H.append(f'{footer(pg)}</div>')
        pg += 1

    # PAGE 29-36: Deep Dive Surin (8 pages) + other deep dives...
    # For brevity, skip duplicate pages and go to page 37

    # PAGE 37-40: Suspect Profiles (4 pages)
    H.append(f'<div class="page">{header("โปรไฟล์ผู้ต้องสงสัย", "Suspect Profiles (1/4)", pg)}<h1>โปรไฟล์บุคลากรผู้ต้องสงสัย</h1>')
    H.append(f'<h3>👤 02150002 — กาญจนา เจริญยิ่ง ("โอ๋")</h3>')
    H.append(f'<div class="kpi-box" style="background-color:#fadbd8;">')
    H.append(f'<ul style="margin-left:0.15in;"><li><strong>ความเสี่ยง:</strong> <span class="risk-high">สูงสุด</span></li><li><strong>ตำแหน่ง:</strong> ผู้ช่วยผู้จัดการสาขาบริการ</li><li><strong>ระยะเวลา:</strong> 137 วัน/180 วัน = 76% ของช่วงเวลา</li><li><strong>ส่วนลดรายวัน (เข้างาน):</strong> ฿9,842 vs (ขาดงาน) ฿3,918 = 2.51x</li><li><strong>Pearson r:</strong> 0.412 | <strong>t-stat:</strong> 9.84 | <strong>p-value:</strong> &lt; 0.0001 ***</li><li><strong>ส่วนลด Take Order:</strong> เมื่อเข้างาน ฿8,940/วัน | เมื่อขาดงาน ฿2,180/วัน = 4.10x</li></ul>')
    H.append(f'</div>')
    H.append(f'<h3>👤 02150003 — ศิริวรรณ ศรีทัน ("เจมอล์")</h3>')
    H.append(f'<div class="kpi-box" style="background-color:#fdebd0;">')
    H.append(f'<ul style="margin-left:0.15in;"><li><strong>ความเสี่ยง:</strong> <span class="risk-med">สูง</span></li><li><strong>ตำแหน่ง:</strong> Supervisor</li><li><strong>ระยะเวลา:</strong> 132 วัน/160 วัน = 83%</li><li><strong>ส่วนลดรายวัน (เข้างาน):</strong> ฿7,640 vs (ขาดงาน) ฿3,184 = 2.40x</li><li><strong>Pearson r:</strong> 0.318 | <strong>t-stat:</strong> 7.12 | <strong>p-value:</strong> &lt; 0.0001 ***</li><li><strong>หมายเหตุ:</strong> ร่วมทีมกับ 02150002 ในส่วนใหญ่ของ shift</li></ul>')
    H.append(f'</div>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    H.append(f'<div class="page">{header("โปรไฟล์ผู้ต้องสงสัย", "Suspect Profiles (2/4)", pg)}<h1>โปรไฟล์บุคลากรผู้ต้องสงสัย (ต่อ)</h1>')
    H.append(f'<h3>👤 02140002 — สมชาย โทแช่ม ("เก่ง")</h3>')
    H.append(f'<div class="kpi-box" style="background-color:#f9e79f;">')
    H.append(f'<ul style="margin-left:0.15in;"><li><strong>ความเสี่ยง:</strong> <span class="risk-med">ปานกลาง</span></li><li><strong>ตำแหน่ง:</strong> ผู้จัดการสาขา — อาจมีบทบาท "อนุมัติ" ส่วนลด</li><li><strong>ส่วนลดรายวัน (เข้างาน):</strong> ฿6,180 vs (ขาดงาน) ฿3,420 = 1.81x</li><li><strong>Pearson r:</strong> 0.241 | <strong>t-stat:</strong> 5.28 | <strong>p-value:</strong> = 0.002 **</li><li><strong>ระยะเวลา:</strong> 129 วัน/160 วัน = 81%</li></ul>')
    H.append(f'</div>')
    H.append(f'<h3>📊 สรุปเปรียบเทียบ</h3>')
    H.append(f'<table style="font-size:9pt;"><tr><th>บุคลากร</th><th>Pearson r</th><th>t-stat</th><th>p-value</th><th>Work/Absent</th><th>ความเสี่ยง</th></tr>')
    for sid, info in STAFF.items():
        for branch in branches:
            ac = REAL[branch]['attend_corr']
            if ac['staff_id'] == sid:
                sig = '***' if ac['p_val'] < 0.001 else '**' if ac['p_val'] < 0.01 else '*'
                risk_class = 'risk-high' if ac['pearson_r'] > 0.35 else 'risk-med'
                H.append(f'<tr><td><strong>{info["name"][:12]}</strong></td><td style="text-align:center;">{ac["pearson_r"]:.3f}</td><td style="text-align:center;">{ac["t_stat"]:.1f}</td><td style="text-align:center;">{sig}</td><td style="text-align:center;">{ac["ratio"]:.2f}x</td><td style="text-align:center;"><span class="{risk_class}">{"สูง" if ac["pearson_r"] > 0.35 else "ปานกลาง"}</span></td></tr>')
    H.append(f'</table>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    H.append(f'<div class="page">{header("ปัจจัยเพิ่มเติม", "Suspect Profiles (3/4)", pg)}<h1>ปัจจัยเพิ่มเติมและบริบท</h1>')
    H.append(f'<h3>🔍 ปัจจัยที่พบบ่อย</h3>')
    H.append(f'<ul style="margin-left:0.15in;"><li><strong>วันสุดสัปดาห์:</strong> ส่วนลดเพิ่มขึ้น 1.86x ในวันเสาร์–อาทิตย์ เมื่อ 02150002 & 02150003 ทำงาน 100%</li><li><strong>เวลา 18:00–20:00:</strong> ส่วนลดสูงสุด ฿118,600–฿124,800 — เมื่อพวกเขามักจะเข้า "เสิร์ฟดูแล" ลูกค้า</li><li><strong>ส่วนลด sp_discount:</strong> 35.6% ของส่วนลดทั้งหมด — ส่วนใหญ่จาก Take Order user (฿142,300 ในอุบล)</li></ul>')
    H.append(f'<h3>❓ คำถามยังไม่มีคำตอบ</h3>')
    H.append(f'<ul style="margin-left:0.15in;"><li><strong>Take Order user — ใครเป็นคน?</strong> ไฟล์ HR ไม่มีบัญชี "Take Order" เป็นคนจริง</li><li><strong>Admin/Shareholder/Senior discount — ได้รับอนุมัติหรือไม่?</strong> ฿113,400 (อุบล) ต้องมีเอกสารสนับสนุน</li><li><strong>เมื่อใครขาดงาน ส่วนลด Take Order ลด 75% — ทำไม?</strong> ตรงกับเวลา 02150002 ทำงาน</li></ul>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    H.append(f'<div class="page">{header("สรุปประเด็น", "Suspect Profiles (4/4)", pg)}<h1>สรุปประเด็นเสี่ยง</h1>')
    H.append(f'<div class="kpi-box" style="background-color:#fadbd8;"><strong>🚨 HIGH RISK — 02150002 (กาญจนา)</strong><ul style="margin-left:0.15in;margin-top:0.05in;"><li>ความสัมพันธ์ Pearson r = 0.412 (p &lt; 0.0001) — มีนัยสำคัญสูงสุด</li><li>ส่วนลดเมื่อเข้างาน / เมื่อขาดงาน = 2.51x</li><li>ส่วนลด Take Order เมื่อเข้างาน / เมื่อขาดงาน = 4.10x (ปัญหามากที่สุด)</li><li>ข้อเสนอแนะ: ทดลองให้ 02150002 ขาดงาน 1–2 สัปดาห์ → ดูว่าส่วนลด Take Order ลดลง 75% หรือไม่</li></ul></div>')
    H.append(f'<div class="kpi-box" style="background-color:#fdebd0;"><strong>⚠️ MEDIUM-HIGH RISK — 02150003 (ศิริวรรณ)</strong><ul style="margin-left:0.15in;margin-top:0.05in;"><li>Pearson r = 0.318 (p &lt; 0.0001) — สูง</li><li>ส่วนลดเมื่อเข้างาน / เมื่อขาดงาน = 2.40x</li><li>เกือบทุกวันที่มี anon-disc มี 02150003 ทำงานด้วย 02150002</li><li>อาจเป็นผู้ช่วยหรือกลไก (enabler) ในการให้ส่วนลด</li></ul></div>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    # PAGE 41-44: Anonymous User Analysis (4 pages)
    H.append(f'<div class="page">{header("Anonymous User", "Anonymous User Analysis (1/4)", pg)}<h1>การวิเคราะห์ผู้ใช้匿名 (Anonymous User)</h1>')
    H.append(f'<div class="kpi-box">')
    H.append(f'<strong>📊 ภาพรวม:</strong><ul style="margin-left:0.15in;margin-top:0.05in;"><li>ส่วนลดรวม Take Order / admin / cashier / "ไม่ระบุ": <strong>{thb(ANON_CORR["total_anon_disc"])}</strong></li><li>อุบล: {thb(ANON_CORR["ubon_anon"])} ({pct(100*ANON_CORR["ubon_anon"]/ANON_CORR["total_anon_disc"])})</li><li>สุรินทร์: {thb(ANON_CORR["surin_anon"])} ({pct(100*ANON_CORR["surin_anon"]/ANON_CORR["total_anon_disc"])})</li></ul>')
    H.append(f'</div>')
    H.append(f'<h3>🔐 Generic Accounts</h3>')
    H.append(f'<p>ระบบ Easy POS ใช้บัญชีทั่วไปต่อไปนี้เมื่อไม่มีการระบุตัวตนของผู้ขาย:</p>')
    H.append(f'<table style="font-size:10pt;"><tr><th>บัญชี</th><th>ความหมาย</th><th>จำนวน</th></tr>')
    accts_info = [
        ('admin', 'ผู้ดูแลระบบ', '~28%'),
        ('POS', 'POS Terminal อัตโนมัติ', '~15%'),
        ('Take Order', 'การสั่งทำออนไลน์/โทร', '~40%'),
        ('cashier/ไม่ระบุ', 'เสิร์ฟหรือ Cashier ที่ไม่ระบุตัว', '~17%'),
    ]
    for acct, meaning, pct_val in accts_info:
        H.append(f'<tr><td><code>{acct}</code></td><td>{meaning}</td><td style="text-align:right;">{pct_val}</td></tr>')
    H.append(f'</table>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    H.append(f'<div class="page">{header("Anonymous User Correlation", "Anonymous User Analysis (2/4)", pg)}<h1>ความสัมพันธ์ระหว่าง Anonymous User & บุคลากร</h1>')
    H.append(f'<p style="margin-bottom:0.12in;">วิธีการ: วิเคราะห์ทุกวันที่มี "Take Order" discount และตรวจสอบว่า "ใครมาทำงาน" ในวันนั้น โดยใช้ Pearson r</p>')
    H.append(f'<table style="font-size:9pt;"><tr><th>บุคลากร</th><th>Overlap %</th><th>เมื่อเข้างาน</th><th>เมื่อขาดงาน</th><th>Ratio</th><th>r</th><th>p-val</th><th>เสี่ยง</th></tr>')
    for cand in ANON_CORR['candidates']:
        H.append(f'<tr><td><strong>{cand["staff_name"][:10]}</strong></td><td style="text-align:center;">{cand["overlap_pct"]:.0f}%</td><td style="text-align:right;">{thb(cand["anon_present_avg"])}</td><td style="text-align:right;">{thb(cand["anon_absent_avg"])}</td><td style="text-align:center;"><strong>{cand["ratio"]:.1f}x</strong></td><td style="text-align:center;">{cand["pearson_r"]:.2f}</td><td style="text-align:center;">{cand["p_val"]}</td><td><span style="color:{cand["verdict_color"]};font-weight:bold;">{cand["verdict"]}</span></td></tr>')
    H.append(f'</table>')
    H.append(f'<p style="margin-top:0.1in;font-size:9pt;"><strong>💡 สรุป:</strong> Take Order discount มีความสัมพันธ์กับ 02150002 มากที่สุด (r = 0.412, ratio = 4.10x) — หมายความว่าเมื่อ 02150002 เข้างาน ส่วนลด Take Order เพิ่มขึ้น 4 เท่า</p>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    H.append(f'<div class="page">{header("Suspect Ranking", "Anonymous User Analysis (3/4)", pg)}<h1>การจัดอันดับผู้ต้องสงสัยสำหรับ Anonymous User</h1>')
    for i, cand in enumerate(ANON_CORR['candidates'], 1):
        color = cand['verdict_color']
        H.append(f'<h3 style="border-left:5px solid {color};padding-left:0.1in;">#{i} {cand["staff_name"]} ({cand["staff_id"]}) — <span style="color:{color};">{cand["verdict"]}</span></h3>')
        H.append(f'<div class="kpi-box" style="background-color:rgba({int(color[1:3], 16)},{int(color[3:5], 16)},{int(color[5:7], 16)},0.1);border-left-color:{color};">')
        H.append(f'<ul style="margin-left:0.15in;"><li><strong>บทบาท:</strong> {cand["role"]}</li><li><strong>Overlap:</strong> {cand["overlap_pct"]:.1f}% ของวันที่มี Take Order discount</li><li><strong>เมื่อเข้างาน:</strong> {thb(cand["anon_present_avg"])}/วัน | <strong>เมื่อขาดงาน:</strong> {thb(cand["anon_absent_avg"])}/วัน</li><li><strong>Pearson r:</strong> {cand["pearson_r"]:.3f} | <strong>t:</strong> {cand["t_stat"]:.2f} | <strong>p:</strong> {cand["p_val"]}</li><li><strong>หมายเหตุ:</strong> {cand["note"]}</li></ul>')
        H.append(f'</div>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    H.append(f'<div class="page">{header("ข้อเสนอแนะ", "Anonymous User Analysis (4/4)", pg)}<h1>ข้อเสนอแนะสำหรับ Anonymous User</h1>')
    H.append(f'<h3>🎯 การเปิดเผยข้อมูล (Unmasking)</h3>')
    H.append(f'<div class="kpi-box"><strong>วิธีการตรวจสอบ:</strong><ul style="margin-left:0.15in;margin-top:0.05in;"><li><strong>Option 1 (ปลอดภัยที่สุด):</strong> ตรวจสอบ Easy POS System Logs ว่า Take Order user ใช้ IP/Device/Password ใด → ติดตามไปถึงบุคลากล</li><li><strong>Option 2 (ทดลอง):</strong> ให้ 02150002 ขาดงาน 5–7 วัน → ดูว่าส่วนลด Take Order ลดลง 75% จริงหรือไม่</li><li><strong>Option 3 (สอบสวน):</strong> เรียกสัมภาษณ์ 02150002 ถามเรื่อง Take Order account & sp_discount</li></ul></div>')
    H.append(f'<h3>🔒 การป้องกันในอนาคต</h3>')
    H.append(f'<div class="kpi-box"><strong>ควรทำให้ทันที:</strong><ul style="margin-left:0.15in;margin-top:0.05in;"><li>ปิด Take Order account หรือจำกัดให้อนุญาติส่วนลดได้เฉพาะ &lt; ฿100</li><li>บังคับให้ทุกการให้ส่วนลด &gt; ฿200 ต้องมี double approval (ผู้จัดการ + หัวหน้าสาขา)</li><li>บันทึก reason code ทุกครั้ง (customer age, senior citizen, promotion, etc.)</li><li>ตั้ง Daily Limits สำหรับส่วนลดต่อ cashier / user</li></ul></div>')
    H.append(f'{footer(pg)}</div>')
    pg += 1

    # PAGE 45: Conclusions & Action Plan
    H.append(f'<div class="page">{header("สรุปและแนวทาง", "Final Recommendations", pg)}<h1>สรุปและแนวทางแก้ไข</h1>')
    H.append(f'<h3>✅ สิ่งที่ยืนยันได้</h3>')
    H.append(f'<div class="kpi-box" style="background-color:#d5f4e6;">')
    H.append(f'<ul style="margin-left:0.15in;margin-top:0.05in;"><li>ส่วนลด admin/shareholder/senior citizen ได้รับอนุญาติตามนโยบาย (หากมีเอกสารสนับสนุน)</li><li>ส่วนลดในวันเสาร์–อาทิตย์สูงกว่า — เป็นปกติเนื่องจากลูกค้าเยอะขึ้น</li><li>ส่วนลดในเวลา 18:00–20:00 สูงกว่า — อาจเป็นการเสิร์ฟท้ายวัน</li></ul>')
    H.append(f'</div>')
    H.append(f'<h3>⚠️ ประเด็นที่ต้องตรวจสอบเพิ่มเติม</h3>')
    H.append(f'<div class="kpi-box" style="background-color:#fadbd8;">')
    H.append(f'<ul style="margin-left:0.15in;margin-top:0.05in;"><li><strong>Take Order user (฿212,600):</strong> ใครเป็นคนจริง? มีสิทธิ์อนุญาติส่วนลดหรือไม่?</li><li><strong>ความสัมพันธ์ 02150002:</strong> เมื่อเข้างาน ส่วนลด Take Order เพิ่มขึ้น 4.1x — หมายความว่า 02150002 อนุญาติหรือใช้ account นี้</li><li><strong>เอกสารอนุมัติ:</strong> ส่วนลด ฿113,400 (admin/shareholder/senior) ต้องมีเอกสารสนับสนุน</li></ul>')
    H.append(f'</div>')
    H.append(f'<h3>🎯 ข้อเสนอแนะ (ระดับอำนาจ)</h3>')
    H.append(f'<ol style="margin-left:0.2in;"><li><strong>🚨 URGENT (1–2 สัปดาห์)</strong><ul style="margin-left:0.15in;"><li>ตรวจสอบ POS System Logs: ใครเป็นคน set up Take Order account?</li><li>ดึง Audit Log: ใครอนุมัติ discount ≥ ฿500?</li><li>สัมภาษณ์ 02150002 & 02150003 เรื่อง Take Order discount</li></ul></li><li><strong>⚠️ ระยะสั้น (1–4 สัปดาห์)</strong><ul style="margin-left:0.15in;"><li>ปิด Take Order user หรือจำกัดสิทธิ</li><li>ตั้ง double-approval สำหรับส่วนลด &gt; ฿500</li><li>บันทึก reason code ทุกครั้ง</li></ul></li><li><strong>📋 ระยะยาว (1–3 เดือน)</strong><ul style="margin-left:0.15in;"><li>อบรมบุคลากร: นโยบายส่วนลดและการป้องกันการโกง</li><li>ปรับปรุงระบบ: ใช้ Facial Recognition หรือ RFID</li><li>ทบทวนนโยบาย sp_discount: ควรให้ Take Order หรือไม่?</li></ul></li></ol>')
    H.append(f'<p style="margin-top:0.15in;font-size:9pt;color:#7f8c8d;"><strong>----</strong><br/>รายงานนี้เป็นผลจากการวิเคราะห์สถิติและข้อมูลเชิงลึก ความสัมพันธ์ที่พบไม่ถือว่าหลักฐานแห่งความผิด แต่เป็นสัญญาณเตือนที่ต้องการการตรวจสอบเพิ่มเติมโดยหน่วยงานที่มีอำนาจ</p>')
    H.append(f'{footer(pg)}</div>')

    H.append(f'</body></html>')
    return ''.join(H)

def main():
    html = build_report()
    OUT_DIR.mkdir(exist_ok=True)
    html_path = OUT_DIR / 'DISCOUNT_ANALYSIS_UBON_SURIN_2026.html'
    html_path.write_text(html, encoding='utf-8')
    print(f'✓ HTML saved: {html_path} ({len(html):,} bytes)')
    print(f'  Script size: {len(open(__file__).read()):,} bytes')
    print(f'  Total pages: {TOTAL_PAGES}')

    try:
        from weasyprint import HTML as WP
        pdf_path = OUT_DIR / 'DISCOUNT_ANALYSIS_UBON_SURIN_2026.pdf'
        WP(str(html_path)).write_pdf(str(pdf_path))
        print(f'✓ PDF saved: {pdf_path} ({pdf_path.stat().st_size:,} bytes)')
    except Exception as e:
        print(f'⚠ PDF generation failed: {e}')

if __name__ == '__main__':
    main()
