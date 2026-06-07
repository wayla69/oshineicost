#!/usr/bin/env python3
"""Generate Thai-language user manual PDF for Oshinei Cost Dashboard."""

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus.flowables import HRFlowable
import os

# ── Fonts ──────────────────────────────────────────────────────────────────
FONT_DIR = "/tmp"
pdfmetrics.registerFont(TTFont("Sarabun", f"{FONT_DIR}/Sarabun-Regular.ttf"))
pdfmetrics.registerFont(TTFont("SarabunB", f"{FONT_DIR}/Sarabun-Bold.ttf"))

# ── Color palette ──────────────────────────────────────────────────────────
C_PRIMARY   = colors.HexColor("#1e3a5f")   # dark navy
C_ACCENT    = colors.HexColor("#2563eb")   # blue
C_LIGHT     = colors.HexColor("#e8f0fe")   # light blue bg
C_MUTED     = colors.HexColor("#64748b")   # slate
C_BORDER    = colors.HexColor("#cbd5e1")
C_WHITE     = colors.white
C_SUCCESS   = colors.HexColor("#16a34a")
C_WARNING   = colors.HexColor("#d97706")
C_DANGER    = colors.HexColor("#dc2626")
C_HEADER_BG = colors.HexColor("#1e3a5f")
C_ROW_ALT   = colors.HexColor("#f1f5f9")

PAGE_W, PAGE_H = A4

# ── Styles ─────────────────────────────────────────────────────────────────
def S(name, **kw):
    defaults = dict(fontName="Sarabun", fontSize=10, leading=16, textColor=colors.black)
    defaults.update(kw)
    return ParagraphStyle(name, **defaults)

sTitle    = S("Title",   fontName="SarabunB", fontSize=28, leading=36, textColor=C_PRIMARY,  spaceAfter=4)
sSubtitle = S("Subtitle",fontName="Sarabun",  fontSize=14, leading=20, textColor=C_MUTED,    spaceAfter=20)
sH1       = S("H1",      fontName="SarabunB", fontSize=18, leading=24, textColor=C_WHITE,    spaceBefore=6, spaceAfter=4)
sH2       = S("H2",      fontName="SarabunB", fontSize=14, leading=20, textColor=C_PRIMARY,  spaceBefore=10, spaceAfter=4)
sH3       = S("H3",      fontName="SarabunB", fontSize=12, leading=18, textColor=C_ACCENT,   spaceBefore=8,  spaceAfter=3)
sBody     = S("Body",    fontSize=10, leading=17, spaceAfter=5)
sMuted    = S("Muted",   fontSize=9,  leading=14, textColor=C_MUTED, spaceAfter=3)
sBullet   = S("Bullet",  fontSize=10, leading=17, leftIndent=16, spaceAfter=3)
sCode     = S("Code",    fontName="Courier", fontSize=8.5, leading=13, textColor=colors.HexColor("#be123c"), backColor=colors.HexColor("#fff1f2"), leftIndent=8, spaceAfter=4)
sTip      = S("Tip",     fontSize=9.5, leading=15, textColor=colors.HexColor("#166534"), leftIndent=12, rightIndent=12, spaceAfter=4)
sWarn     = S("Warn",    fontSize=9.5, leading=15, textColor=colors.HexColor("#92400e"), leftIndent=12, rightIndent=12, spaceAfter=4)
sCaption  = S("Caption", fontSize=8,  leading=12, textColor=C_MUTED, alignment=1, spaceAfter=6)
sTH       = S("TH",      fontName="SarabunB", fontSize=9.5, leading=14, textColor=C_WHITE)
sTD       = S("TD",      fontSize=9.5, leading=14, textColor=colors.black)
sTDMuted  = S("TDMuted", fontSize=9,  leading=13, textColor=C_MUTED)

# ── Helpers ────────────────────────────────────────────────────────────────
def HR(color=C_BORDER, thickness=0.5):
    return HRFlowable(width="100%", thickness=thickness, color=color, spaceAfter=6, spaceBefore=4)

def section_header(text):
    """Full-width colored section banner."""
    tbl = Table([[Paragraph(text, sH1)]], colWidths=[PAGE_W - 4*cm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), C_HEADER_BG),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING",   (0,0), (-1,-1), 14),
        ("RIGHTPADDING",  (0,0), (-1,-1), 14),
        ("ROUNDEDCORNERS", [4]),
    ]))
    return tbl

def info_box(text, kind="tip"):
    bg  = colors.HexColor("#f0fdf4") if kind == "tip" else colors.HexColor("#fffbeb")
    bdr = C_SUCCESS if kind == "tip" else C_WARNING
    st  = sTip if kind == "tip" else sWarn
    prefix = "💡 " if kind == "tip" else "⚠️  "
    tbl = Table([[Paragraph(prefix + text, st)]], colWidths=[PAGE_W - 4*cm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), bg),
        ("LINEAFTER",  (0,0), (0,-1), 3, bdr),
        ("TOPPADDING",    (0,0), (-1,-1), 7),
        ("BOTTOMPADDING", (0,0), (-1,-1), 7),
        ("LEFTPADDING",   (0,0), (-1,-1), 10),
        ("RIGHTPADDING",  (0,0), (-1,-1), 10),
    ]))
    return tbl

def data_table(headers, rows, col_widths=None):
    th_cells = [Paragraph(h, sTH) for h in headers]
    data = [th_cells]
    for i, row in enumerate(rows):
        bg = C_ROW_ALT if i % 2 == 1 else C_WHITE
        data.append([Paragraph(str(c), sTD) for c in row])
    w = col_widths or [(PAGE_W - 4*cm) / len(headers)] * len(headers)
    tbl = Table(data, colWidths=w, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0), C_ACCENT),
        ("GRID",          (0,0), (-1,-1), 0.4, C_BORDER),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 7),
        ("RIGHTPADDING",  (0,0), (-1,-1), 7),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [C_WHITE, C_ROW_ALT]),
        ("FONTNAME",      (0,1), (-1,-1), "Sarabun"),
        ("FONTSIZE",      (0,1), (-1,-1), 9.5),
    ]))
    return tbl

def bullet(text):
    return Paragraph(f"• {text}", sBullet)

def sub_bullet(text):
    return Paragraph(f"   ◦ {text}", S("SubBullet", fontSize=9.5, leading=15, leftIndent=28, spaceAfter=2))

def sp(pts=6):
    return Spacer(1, pts)

# ── Cover page ─────────────────────────────────────────────────────────────
def cover_page(story):
    story.append(Spacer(1, 3*cm))

    # Logo-like header block
    tbl = Table([[
        Paragraph("🍣  OSHINEI", S("Logo", fontName="SarabunB", fontSize=36, leading=44,
                                    textColor=C_PRIMARY)),
    ]], colWidths=[PAGE_W - 4*cm])
    tbl.setStyle(TableStyle([
        ("BOTTOMPADDING", (0,0), (-1,-1), 0),
    ]))
    story.append(tbl)

    story.append(Paragraph("Cost & Analytics Dashboard", sSubtitle))
    story.append(HR(C_ACCENT, 2))
    story.append(sp(8))

    story.append(Paragraph("คู่มือการใช้งานระบบ", sTitle))
    story.append(Paragraph("User Manual — ฉบับภาษาไทย", sSubtitle))
    story.append(sp(20))

    # Info box on cover
    meta = [
        ["เวอร์ชัน", "2026.06"],
        ["วันที่จัดทำ", "มิถุนายน 2569"],
        ["กลุ่มเป้าหมาย", "ผู้จัดการสาขา / ทีมวิเคราะห์ข้อมูล / เจ้าของกิจการ"],
        ["ภาษา", "ภาษาไทย"],
    ]
    tbl2 = Table(meta, colWidths=[4*cm, PAGE_W - 4*cm - 4*cm - 1*cm])
    tbl2.setStyle(TableStyle([
        ("FONTNAME",      (0,0), (0,-1), "SarabunB"),
        ("FONTNAME",      (1,0), (1,-1), "Sarabun"),
        ("FONTSIZE",      (0,0), (-1,-1), 10),
        ("LEADING",       (0,0), (-1,-1), 18),
        ("TEXTCOLOR",     (0,0), (0,-1), C_MUTED),
        ("TEXTCOLOR",     (1,0), (1,-1), colors.black),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
    ]))
    story.append(tbl2)
    story.append(sp(30))
    story.append(HR(C_BORDER))
    story.append(Paragraph("ใช้งานภายในองค์กรเท่านั้น — ห้ามเผยแพร่ข้อมูลที่เป็นความลับ", sMuted))
    story.append(PageBreak())


# ── TOC ───────────────────────────────────────────────────────────────────
def toc_page(story):
    story.append(section_header("สารบัญ"))
    story.append(sp(10))

    toc = [
        ("1", "ภาพรวมระบบ", "3"),
        ("2", "การเข้าสู่ระบบและการนำทาง", "4"),
        ("3", "หน้าแรก (Dashboard Overview)", "5"),
        ("4", "ภาพรวมยอดขาย (Sales Overview)", "6"),
        ("5", "การวิเคราะห์บุฟเฟ่ต์ (Buffet Analysis)", "9"),
        ("6", "ต้นทุนระดับเครือ (Chain-level Cost)", "12"),
        ("7", "เมนูและอะลาคาร์ท (Menu & À La Carte)", "15"),
        ("8", "การดำเนินงาน (Operations)", "17"),
        ("9", "เครื่องมือและการตั้งค่า (Tools & Settings)", "19"),
        ("10", "การกระทบยอดรายได้ (Revenue Reconciliation)", "22"),
        ("11", "คำถามที่พบบ่อย (FAQ)", "24"),
        ("12", "คำศัพท์และนิยาม", "25"),
    ]

    data = [[
        Paragraph("บทที่", sTH),
        Paragraph("หัวข้อ", sTH),
        Paragraph("หน้า", sTH),
    ]]
    for num, title, page in toc:
        data.append([
            Paragraph(num, sTD),
            Paragraph(title, sTD),
            Paragraph(page, sTD),
        ])

    tbl = Table(data, colWidths=[1.5*cm, PAGE_W - 4*cm - 2.5*cm, 1.5*cm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0), C_ACCENT),
        ("GRID",          (0,0), (-1,-1), 0.4, C_BORDER),
        ("TOPPADDING",    (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [C_WHITE, C_ROW_ALT]),
        ("FONTNAME",      (0,1), (-1,-1), "Sarabun"),
        ("FONTSIZE",      (0,1), (-1,-1), 10),
        ("LEADING",       (0,1), (-1,-1), 17),
        ("ALIGN",         (0,0), (0,-1), "CENTER"),
        ("ALIGN",         (2,0), (2,-1), "CENTER"),
    ]))
    story.append(tbl)
    story.append(PageBreak())


# ── Chapter 1: Overview ────────────────────────────────────────────────────
def ch1_overview(story):
    story.append(section_header("บทที่ 1 — ภาพรวมระบบ"))
    story.append(sp(10))

    story.append(Paragraph("Oshinei Cost Dashboard คืออะไร?", sH2))
    story.append(Paragraph(
        "Oshinei Cost Dashboard เป็นระบบวิเคราะห์ธุรกิจ (Business Intelligence) ที่ออกแบบมาสำหรับ "
        "เครือร้านอาหารโอชิเนย์ โดยเฉพาะ ช่วยให้ผู้บริหารและทีมวิเคราะห์สามารถติดตามยอดขาย "
        "ต้นทุน กำไร และประสิทธิภาพการดำเนินงานของแต่ละสาขาแบบ Real-time ผ่านเว็บเบราว์เซอร์",
        sBody))

    story.append(Paragraph("สิ่งที่ระบบทำได้", sH3))
    items = [
        "วิเคราะห์ยอดขายรายวัน/สัปดาห์/เดือน แยกตามสาขา หมวดหมู่ และเมนู",
        "คำนวณต้นทุนอาหาร (COGS) จากสูตรส่วนผสม (Bill of Materials) และราคาต้นทุนหน่วย",
        "วิเคราะห์กำไรขั้นต้น (Gross Profit) และอัตรากำไร (GP%) ทุกระดับ",
        "ติดตามประสิทธิภาพบุฟเฟ่ต์ — ที่นั่ง อัตราการใช้ รายได้ต่อที่นั่ง (RevPASH)",
        "วิเคราะห์เมนูแบบ Menu Engineering (ความนิยม vs กำไร)",
        "ตรวจสอบการดำเนินงาน — พนักงาน ส่วนลด การไหลเวียนลูกค้ารายชั่วโมง",
        "กระทบยอดรายได้ระหว่าง POS กับธนาคาร/ช่องทางชำระเงิน",
    ]
    for item in items:
        story.append(bullet(item))
    story.append(sp(8))

    story.append(Paragraph("ข้อมูลในระบบ", sH3))
    stats = [
        ["รายการ", "จำนวน"],
        ["สาขาที่ใช้งาน", "26 สาขา"],
        ["เมนู (SKU)", "1,988 รายการ"],
        ["หมวดหมู่", "66 หมวด"],
        ["สูตรอาหาร (BoM)", "1,230 สูตร"],
        ["ข้อมูลยอดขาย", "535,000+ บิล"],
        ["ระยะเวลาข้อมูล", "พ.ค. 2568 – พ.ค. 2569 (13 เดือน)"],
    ]
    story.append(data_table(["รายการ", "จำนวน"], [r[1:] if len(r)>1 else r for r in stats[1:]],
                            col_widths=[8*cm, PAGE_W - 4*cm - 8*cm]))
    story.append(sp(10))

    story.append(Paragraph("เทคโนโลยีที่ใช้", sH3))
    story.append(Paragraph(
        "ระบบพัฒนาด้วย FastAPI (Python) ฝั่ง Backend, SQLite ฐานข้อมูล, "
        "และ Chart.js + Tailwind CSS ฝั่ง Frontend เข้าถึงผ่านเว็บเบราว์เซอร์ ไม่ต้องติดตั้งโปรแกรมเพิ่มเติม",
        sBody))
    story.append(info_box(
        "ระบบทำงานบน Fly.io Cloud (Singapore) เข้าถึงได้ทุกที่ที่มีอินเทอร์เน็ต "
        "รองรับ Chrome, Firefox, Edge และ Safari เวอร์ชันล่าสุด"
    ))
    story.append(PageBreak())


# ── Chapter 2: Navigation ──────────────────────────────────────────────────
def ch2_navigation(story):
    story.append(section_header("บทที่ 2 — การเข้าสู่ระบบและการนำทาง"))
    story.append(sp(10))

    story.append(Paragraph("การเข้าสู่ระบบ", sH2))
    story.append(Paragraph(
        "เปิดเว็บเบราว์เซอร์และไปที่ URL ของระบบ ระบบจะแสดงแดชบอร์ดหลักโดยอัตโนมัติ "
        "ไม่มีการล็อกอินสำหรับการอ่านข้อมูล (Read-only) แต่การแก้ไขข้อมูลต้องใช้ Token",
        sBody))
    story.append(info_box(
        "การอัปโหลดข้อมูลหรือแก้ไขต้นทุนต้องใช้ API Token — ติดต่อผู้ดูแลระบบเพื่อขอสิทธิ์"
    , "warn"))
    story.append(sp(8))

    story.append(Paragraph("โครงสร้างการนำทาง (Sidebar Navigation)", sH2))
    story.append(Paragraph(
        "ด้านซ้ายของหน้าจอมี Sidebar แบ่งออกเป็น 6 กลุ่มหลัก:", sBody))

    nav = [
        ["ไอคอน", "เมนูหลัก", "เมนูย่อย (ตัวอย่าง)"],
        ["🏠", "หน้าแรก", "สรุปภาพรวมทั้งเครือ"],
        ["📊", "ภาพรวม", "ยอดขาย, หมวดหมู่, SKU, แนวโน้ม, เปรียบเทียบ, Persona, Benchmark"],
        ["🍱", "บุฟเฟ่ต์", "ภาพรวม, Tier Mix, ที่นั่ง, RevPASH, P&L, BoM, Simulator"],
        ["🌐", "เครือ & ต้นทุน", "GP สาขา, BoM ต้นทุน, Simulator, P&L, ข้อมูลเพิ่มเติม"],
        ["🍣", "เมนู & อะลาคาร์ท", "Menu Engineering, ต้นทุน, กำไร SKU"],
        ["📦", "การดำเนินงาน", "พนักงาน, ส่วนลด, การไหล, ชั่วโมงสูงสุด"],
        ["⚙️", "เครื่องมือ", "จัดการต้นทุน, เป้าหมายสาขา, ต้นทุนคงที่, ข้อมูลสาขา"],
    ]
    story.append(data_table(
        nav[0], nav[1:],
        col_widths=[1.2*cm, 4.5*cm, PAGE_W - 4*cm - 5.7*cm]
    ))
    story.append(sp(10))

    story.append(Paragraph("ตัวกรองข้อมูลทั่วไป", sH2))
    story.append(Paragraph(
        "แทบทุกหน้าจะมีตัวกรองด้านบนให้เลือก:", sBody))
    filters = [
        ("ช่วงเวลา", "เลือกวันเริ่ม-สิ้นสุด หรือเลือกเดือน/ไตรมาสจาก Preset"),
        ("สาขา", "เลือกสาขาเดียวหรือหลายสาขา หรือ 'ทั้งหมด'"),
        ("Granularity", "วัน / สัปดาห์ / เดือน (ใช้ได้ในกราฟแนวโน้ม)"),
        ("หมวดหมู่ / SKU", "กรองเฉพาะหมวดหมู่หรือเมนูที่ต้องการ"),
    ]
    for name, desc in filters:
        story.append(bullet(f"<b>{name}</b> — {desc}"))
    story.append(sp(8))

    story.append(info_box(
        "ข้อมูลจะรีเฟรชอัตโนมัติทุก 5 นาที หรือกดปุ่ม 🔄 เพื่อรีเฟรชทันที"
    ))
    story.append(PageBreak())


# ── Chapter 3: Home ────────────────────────────────────────────────────────
def ch3_home(story):
    story.append(section_header("บทที่ 3 — หน้าแรก (Dashboard Overview)"))
    story.append(sp(10))

    story.append(Paragraph("สิ่งที่เห็นบนหน้าแรก", sH2))
    story.append(Paragraph(
        "หน้าแรกแสดงสรุปภาพรวมของทั้งเครือแบบ Real-time ประกอบด้วย KPI Cards, "
        "กราฟแนวโน้ม และตารางสรุปสาขา", sBody))

    story.append(Paragraph("KPI Cards หลัก", sH3))
    kpis = [
        ["KPI", "ความหมาย", "หมายเหตุ"],
        ["ยอดขายรวม", "รายได้รวมทั้งเครือ (ไม่รวม VAT)", "หน่วย: บาท"],
        ["จำนวนบิล", "จำนวนโต๊ะ/บิลที่เปิดในช่วงเวลา", "นับรวมทุกประเภท"],
        ["จำนวนลูกค้า", "จำนวน Cover (คน) รวมทั้งเครือ", "นับจาก POS"],
        ["GP%", "อัตรากำไรขั้นต้น (Gross Profit %)", "= (ยอดขาย - ต้นทุน) / ยอดขาย"],
        ["ยอดขายเฉลี่ย/คน", "รายได้ต่อลูกค้า 1 คน", "Average Spend Per Cover"],
        ["RevPASH", "รายได้ต่อที่นั่งต่อชั่วโมง", "Revenue Per Available Seat Hour"],
    ]
    story.append(data_table(kpis[0], kpis[1:],
                            col_widths=[4*cm, 8*cm, PAGE_W - 4*cm - 12*cm]))
    story.append(sp(8))

    story.append(Paragraph("การเปรียบเทียบ Period-over-Period", sH3))
    story.append(Paragraph(
        "KPI Card แต่ละใบจะแสดงการเปลี่ยนแปลงเทียบกับช่วงเวลาก่อนหน้า (เช่น เดือนที่แล้ว):", sBody))
    story.append(bullet("สีเขียว (↑) — ดีขึ้น"))
    story.append(bullet("สีแดง (↓) — แย่ลง"))
    story.append(bullet("สีเทา (→) — เท่าเดิม (เปลี่ยนแปลง < 1%)"))
    story.append(sp(8))

    story.append(Paragraph("กราฟแนวโน้มรายเดือน", sH3))
    story.append(Paragraph(
        "กราฟ Line Chart แสดงยอดขายและ GP% รายเดือนของทั้งเครือย้อนหลัง 13 เดือน "
        "สามารถ Hover เพื่อดูตัวเลขละเอียด และคลิกที่ Legend เพื่อซ่อน/แสดงเส้นได้",
        sBody))

    story.append(info_box(
        "ตัวเลข 'ยอดขาย' ในระบบนี้ใช้ subtotal_ex_vat (ยอดขายก่อน VAT) เสมอ — "
        "ไม่นำ VAT 7% มารวมในรายได้"
    ))
    story.append(PageBreak())


# ── Chapter 4: Sales Overview ─────────────────────────────────────────────
def ch4_sales(story):
    story.append(section_header("บทที่ 4 — ภาพรวมยอดขาย (Sales Overview)"))
    story.append(sp(10))

    story.append(Paragraph("4.1 สรุปยอดขาย (Sales Summary)", sH2))
    story.append(Paragraph(
        "แสดงยอดขายรวม, จำนวนบิล, จำนวนลูกค้า แยกตามสาขาและช่วงเวลา พร้อม Trend Chart "
        "และ Heat Map วันในสัปดาห์ × ช่วงเวลา", sBody))

    story.append(Paragraph("วิธีใช้งาน", sH3))
    story.append(bullet("เลือกช่วงวันที่จาก Date Picker หรือ Preset (เช่น 'เดือนนี้', 'ไตรมาสนี้')"))
    story.append(bullet("เลือกสาขาหรือดู 'ทั้งหมด' เพื่อรวมทุกสาขา"))
    story.append(bullet("สลับมุมมอง Bar/Line Chart ด้วยปุ่ม Toggle ด้านบนกราฟ"))
    story.append(bullet("Export ข้อมูลเป็น CSV ด้วยปุ่ม 📥 Download"))
    story.append(sp(8))

    story.append(Paragraph("4.2 วิเคราะห์หมวดหมู่ (Category Analysis)", sH2))
    story.append(Paragraph(
        "Breakdown ยอดขายและจำนวนออเดอร์แยกตามหมวดหมู่เมนู เช่น บุฟเฟ่ต์, เครื่องดื่ม, "
        "ของหวาน, อะลาคาร์ท ฯลฯ พร้อม Pie Chart และ Waterfall Chart", sBody))

    story.append(Paragraph("4.3 ประสิทธิภาพ SKU (SKU Performance)", sH2))
    story.append(Paragraph(
        "ตารางเมนูทุกรายการพร้อมยอดขาย จำนวนออเดอร์ ราคาเฉลี่ย และ GP% "
        "สามารถเรียงตามคอลัมน์ได้ และกรอง Top N รายการ", sBody))

    story.append(Paragraph("4.4 การวิเคราะห์แนวโน้ม (Trend Analysis)", sH2))
    story.append(Paragraph(
        "กราฟ Multi-line แสดงแนวโน้มของหลายเมตริกพร้อมกัน เช่น ยอดขาย + จำนวนลูกค้า + GP% "
        "เลือก Granularity ได้ (วัน/สัปดาห์/เดือน)", sBody))

    story.append(Paragraph("4.5 การเปรียบเทียบ (Comparison)", sH2))
    story.append(Paragraph(
        "เปรียบเทียบสองช่วงเวลา (Period A vs Period B) แบบ Side-by-side "
        "ช่วยวิเคราะห์ผลของโปรโมชั่น การเปลี่ยนราคา หรือการเปิดสาขาใหม่", sBody))

    story.append(Paragraph("4.6 Customer Personas", sH2))
    story.append(Paragraph(
        "จัดกลุ่มลูกค้าตามพฤติกรรมการสั่งอาหาร ความถี่ และการใช้จ่าย "
        "ช่วยวางแผนโปรโมชั่นและปรับเมนูให้ตรงกลุ่มเป้าหมาย", sBody))

    story.append(Paragraph("4.7 Revenue per Seated Hour (SPH)", sH2))
    story.append(Paragraph(
        "วิเคราะห์รายได้ต่อชั่วโมงที่มีลูกค้านั่ง แยกตามช่วงเวลาในวัน (Daypart) "
        "ช่วยระบุ Peak Hours และโอกาสเพิ่มรายได้ในช่วง Off-peak", sBody))

    story.append(Paragraph("4.8 Branch Benchmarking", sH2))
    story.append(Paragraph(
        "เปรียบเทียบประสิทธิภาพสาขาทั้งหมดแบบ Radar Chart และตาราง Ranking "
        "ดัชนีที่วัด: ยอดขาย, GP%, จำนวนลูกค้า, RevPASH, การหมุนเวียนโต๊ะ", sBody))

    story.append(info_box(
        "Benchmark ใช้ค่าเฉลี่ยของเครือเป็น Baseline — สาขาที่อยู่เหนือเส้น = outperform, "
        "ต่ำกว่า = ต้องให้ความสนใจ"
    ))
    story.append(PageBreak())


# ── Chapter 5: Buffet ──────────────────────────────────────────────────────
def ch5_buffet(story):
    story.append(section_header("บทที่ 5 — การวิเคราะห์บุฟเฟ่ต์ (Buffet Analysis)"))
    story.append(sp(10))

    story.append(Paragraph("5.1 ภาพรวมบุฟเฟ่ต์ (Buffet Overview)", sH2))
    story.append(Paragraph(
        "สรุปตัวเลขสำคัญของธุรกิจบุฟเฟ่ต์ ได้แก่ รายได้บุฟเฟ่ต์รวม, จำนวน Cover, "
        "รายได้เฉลี่ยต่อ Cover, และ GP% ของบุฟเฟ่ต์", sBody))

    story.append(Paragraph("5.2 Tier Mix", sH2))
    story.append(Paragraph(
        "วิเคราะห์สัดส่วนลูกค้าแต่ละ Tier บุฟเฟ่ต์:", sBody))
    tiers = [
        ["Tier", "กลุ่มลูกค้า", "ราคาโดยทั่วไป"],
        ["เด็ก (Child)", "ลูกค้าอายุต่ำกว่าเกณฑ์", "ราคาพิเศษ"],
        ["ผู้ใหญ่ (Adult)", "ลูกค้าทั่วไป", "ราคามาตรฐาน"],
        ["พรีเมี่ยม (Premium)", "ลูกค้าเลือก Tier สูง", "ราคาสูงสุด"],
    ]
    story.append(data_table(tiers[0], tiers[1:],
                            col_widths=[4*cm, 7*cm, PAGE_W - 4*cm - 11*cm]))
    story.append(sp(6))
    story.append(Paragraph(
        "กราฟ Stacked Bar แสดงสัดส่วน Tier Mix ของแต่ละสาขา "
        "ช่วยเปรียบเทียบว่าสาขาไหนมีลูกค้า Premium สูงกว่าค่าเฉลี่ย", sBody))

    story.append(Paragraph("5.3 การใช้งานที่นั่ง (Seat Utilization)", sH2))
    story.append(Paragraph(
        "อัตราการใช้งานที่นั่ง = จำนวน Cover ที่เข้าจริง / (จำนวนที่นั่ง × ชั่วโมงเปิด) × 100% "
        "แสดงแบบ Heat Map รายชั่วโมง × วันในสัปดาห์", sBody))

    story.append(Paragraph("5.4 RevPASH (Revenue Per Available Seat Hour)", sH2))
    story.append(Paragraph(
        "ตัวชี้วัดหลักของธุรกิจบุฟเฟ่ต์ คำนวณจาก:", sBody))
    story.append(Paragraph(
        "RevPASH = ยอดขายบุฟเฟ่ต์ ÷ (จำนวนที่นั่ง × ชั่วโมงที่เปิด)",
        sCode))
    story.append(Paragraph(
        "เป้าหมาย RevPASH ตั้งได้ในหน้า 'เป้าหมายสาขา' และระบบจะแสดงสัญญาณ "
        "สีเขียว/เหลือง/แดง ตามระดับการบรรลุเป้า", sBody))

    story.append(Paragraph("5.5 Buffet P&L (กำไร-ขาดทุนบุฟเฟ่ต์)", sH2))
    story.append(Paragraph(
        "งบกำไรขาดทุนระดับบิล แสดง Revenue, ต้นทุนอาหาร (COGS จาก BoM), "
        "ต้นทุนคงที่ (ค่าเช่า ค่าแรง สาธารณูปโภค) และ Net Profit", sBody))

    story.append(Paragraph("5.6 BoM Deep-dive (สูตรอาหารเชิงลึก)", sH2))
    story.append(Paragraph(
        "วิเคราะห์ต้นทุนระดับส่วนผสม (Ingredient) ของเมนูบุฟเฟ่ต์ "
        "แสดงสัดส่วนต้นทุนแต่ละประเภท: โปรตีน คาร์โบไฮเดรต ผัก ซอส น้ำมัน เครื่องปรุง", sBody))

    story.append(Paragraph("5.7 Cost Sensitivity Simulator", sH2))
    story.append(Paragraph(
        "จำลอง 'What-if' — ถ้าราคาวัตถุดิบขึ้น X% GP% จะเปลี่ยนเท่าไร? "
        "ปรับ Slider แล้วดูผลทันที สามารถบันทึก Scenario เพื่อเปรียบเทียบได้", sBody))

    story.append(info_box(
        "Simulator ไม่ได้บันทึกลงฐานข้อมูล — เป็นเพียงการจำลองเพื่อการวางแผนเท่านั้น"
    ))
    story.append(PageBreak())


# ── Chapter 6: Chain Cost ──────────────────────────────────────────────────
def ch6_cost(story):
    story.append(section_header("บทที่ 6 — ต้นทุนระดับเครือ (Chain-level Cost)"))
    story.append(sp(10))

    story.append(Paragraph("6.1 GP by Branch/Month (กำไรขั้นต้นแยกสาขา)", sH2))
    story.append(Paragraph(
        "ตารางและกราฟ GP% ของทุกสาขาแบบรายเดือน Heatmap แสดงสีเขียว = GP% สูง, "
        "แดง = GP% ต่ำ ช่วยระบุสาขาที่มีปัญหาด้านต้นทุนอย่างรวดเร็ว", sBody))

    story.append(Paragraph("6.2 BoM Analysis by Branch", sH2))
    story.append(Paragraph(
        "เจาะลึก Bill of Materials (สูตรอาหาร) แยกตามสาขา ดูว่าสาขาไหนมี Food Cost สูงกว่าค่าเฉลี่ย "
        "และส่วนผสมไหนที่ดึงต้นทุนขึ้น", sBody))

    story.append(Paragraph("6.3 BoM Cost + Simulator (เครื่องมือหลัก)", sH2))
    story.append(Paragraph(
        "หน้านี้เป็น Flagship ของระบบ ประกอบด้วย 3 ส่วนหลัก:", sBody))
    story.append(bullet("Cost Drivers — ส่วนผสมที่มีผลต่อต้นทุนมากที่สุด"))
    story.append(bullet("Branch Comparison — เปรียบเทียบ BoM Cost ระหว่างสาขา"))
    story.append(bullet("What-if Scenarios — จำลองการเปลี่ยนแปลงราคาวัตถุดิบ"))

    story.append(sp(6))
    story.append(Paragraph("วิธีใช้ Simulator", sH3))
    steps = [
        "เลือกเมนูที่ต้องการวิเคราะห์จาก Dropdown",
        "ปรับ Slider ราคาส่วนผสมแต่ละชนิด",
        "ดูการเปลี่ยนแปลง GP% ทันทีในกราฟด้านขวา",
        "กด 'บันทึก Scenario' เพื่อเก็บผลและเปรียบเทียบ",
        "ใช้ 'Reset' เพื่อกลับค่าเดิม",
    ]
    for i, step in enumerate(steps, 1):
        story.append(bullet(f"ขั้นตอนที่ {i}: {step}"))

    story.append(sp(8))

    story.append(Paragraph("6.4 Cost Coverage Audit", sH2))
    story.append(Paragraph(
        "ตรวจสอบความครอบคลุมของข้อมูลต้นทุน — SKU ไหนมีราคาต้นทุนครบถ้วน, "
        "ไหนยังขาดข้อมูล (Missing Cost) แสดงเป็น % Coverage แยกสาขา", sBody))
    story.append(info_box(
        "SKU ที่ Missing Cost จะถูกคำนวณ GP% ไม่ได้ — ควรกรอกข้อมูลใน 'จัดการต้นทุน' ก่อน"
    , "warn"))

    story.append(Paragraph("6.5 Branch P&L (งบกำไรขาดทุนสาขา)", sH2))
    story.append(Paragraph(
        "งบ P&L ระดับสาขา ประกอบด้วย:", sBody))
    pl_items = [
        ("Revenue", "ยอดขายรวม (ก่อน VAT)"),
        ("COGS", "ต้นทุนอาหารและเครื่องดื่ม (จาก BoM หรือ unit_cost)"),
        ("Gross Profit", "= Revenue - COGS"),
        ("Fixed Costs", "ค่าเช่า ค่าแรง สาธารณูปโภค (ตั้งใน Branch Fixed Costs)"),
        ("Operating Profit", "= Gross Profit - Fixed Costs"),
    ]
    for item, desc in pl_items:
        story.append(bullet(f"<b>{item}</b>: {desc}"))

    story.append(Paragraph("6.6 Additional Insights", sH2))
    insights = [
        ["ข้อมูลเพิ่มเติม", "คำอธิบาย"],
        ["Daypart Economics", "วิเคราะห์รายได้และต้นทุนแยกตาม Breakfast/Lunch/Dinner/Night"],
        ["Menu Mix Analysis", "สัดส่วนการสั่งเมนูแต่ละประเภทและผลต่อ GP%"],
        ["Table Turnover", "อัตราการหมุนเวียนโต๊ะ = จำนวนบิล / จำนวนโต๊ะ / วัน"],
        ["Revenue Forecast", "พยากรณ์ยอดขาย 3 เดือนข้างหน้าจาก Historical Trend"],
    ]
    story.append(data_table(insights[0], insights[1:],
                            col_widths=[5*cm, PAGE_W - 4*cm - 5*cm]))
    story.append(PageBreak())


# ── Chapter 7: Menu ────────────────────────────────────────────────────────
def ch7_menu(story):
    story.append(section_header("บทที่ 7 — เมนูและอะลาคาร์ท (Menu & À La Carte)"))
    story.append(sp(10))

    story.append(Paragraph("7.1 Menu Engineering Matrix", sH2))
    story.append(Paragraph(
        "วิธีวิเคราะห์เมนูแบบ Boston Consulting Group (BCG) Matrix แบ่งเมนูออกเป็น 4 กลุ่ม "
        "ตามแกน Popularity (จำนวนออเดอร์) และ Contribution (กำไรต่อรายการ):", sBody))

    matrix = [
        ["กลุ่ม", "Popularity", "Contribution", "กลยุทธ์แนะนำ"],
        ["⭐ STAR", "สูง", "สูง", "รักษาคุณภาพ, โปรโมต"],
        ["🐄 PLOW HORSE", "สูง", "ต่ำ", "ลดต้นทุน หรือขึ้นราคา"],
        ["❓ PUZZLE", "ต่ำ", "สูง", "เพิ่มการโปรโมต"],
        ["🐕 DOG", "ต่ำ", "ต่ำ", "พิจารณาตัดเมนู"],
    ]
    story.append(data_table(matrix[0], matrix[1:],
                            col_widths=[3.5*cm, 2.5*cm, 3*cm, PAGE_W - 4*cm - 9*cm]))
    story.append(sp(8))
    story.append(Paragraph(
        "Matrix แสดงเป็น Scatter Plot แต่ละจุดคือ 1 เมนู hover เพื่อดูชื่อและตัวเลข "
        "กรองตามหมวดหมู่หรือสาขาได้", sBody))

    story.append(Paragraph("7.2 Menu Analysis (การวิเคราะห์เมนู)", sH2))
    story.append(Paragraph(
        "ตารางรวมข้อมูลทุกมิติของแต่ละเมนู: ยอดขาย, จำนวนออเดอร์, "
        "ราคาเฉลี่ย, ต้นทุน, GP% และการเปลี่ยนแปลงเดือน-ต่อ-เดือน", sBody))

    story.append(Paragraph("7.3 Cost Analysis (วิเคราะห์ต้นทุนเมนู)", sH2))
    story.append(Paragraph(
        "วิเคราะห์ต้นทุนของแต่ละเมนูเทียบกับราคาขาย แสดง Waterfall Chart "
        "แยกต้นทุนตามประเภทส่วนผสม (โปรตีน, คาร์บ, ผัก, ซอส, น้ำมัน, เครื่องปรุง)", sBody))

    story.append(Paragraph("7.4 Profit by SKU/Branch", sH2))
    story.append(Paragraph(
        "กำไรและ GP% ของแต่ละ SKU แยกตามสาขา ช่วยระบุว่าเมนูเดียวกัน "
        "สาขาไหน 'ทำกำไรได้ดีกว่า' เนื่องจากต้นทุนวัตถุดิบท้องถิ่น", sBody))

    story.append(Paragraph("7.5 Lifecycle Tracking", sH2))
    story.append(Paragraph(
        "ติดตามวงจรชีวิตของเมนูตั้งแต่เปิดตัว → เติบโต → คงที่ → ถดถอย "
        "ช่วยวางแผนว่าเมนูไหนควรรีเฟรช หรือลบออก", sBody))

    story.append(info_box(
        "เมนูที่มี BoM (สูตรอาหาร) จะคำนวณต้นทุนได้แม่นยำกว่า — "
        "หากเมนูไม่มี BoM จะใช้ unit_cost แทน (อาจมี deviation)"
    , "warn"))
    story.append(PageBreak())


# ── Chapter 8: Operations ──────────────────────────────────────────────────
def ch8_operations(story):
    story.append(section_header("บทที่ 8 — การดำเนินงาน (Operations)"))
    story.append(sp(10))

    story.append(Paragraph("8.1 พนักงาน/แคชเชียร์ (Staff Performance)", sH2))
    story.append(Paragraph(
        "วิเคราะห์ประสิทธิภาพแคชเชียร์แต่ละคน ได้แก่ จำนวนบิลที่รับผิดชอบ "
        "มูลค่ายอดขายเฉลี่ย เวลาต่อบิล และการให้ส่วนลด", sBody))

    story.append(Paragraph("8.2 การวิเคราะห์ส่วนลด (Discount Analysis)", sH2))
    story.append(Paragraph(
        "สรุปมูลค่าส่วนลดทั้งหมด แยกตามประเภทส่วนลด (เหตุผล) สาขา และพนักงาน "
        "ช่วยตรวจสอบว่ามีการให้ส่วนลดเกินนโยบายหรือไม่", sBody))

    disc_types = [
        ["ประเภทส่วนลด", "คำอธิบาย"],
        ["Staff Meal", "อาหารพนักงาน ลดราคาตามนโยบาย"],
        ["Complimentary", "อาหารให้ฟรีเพื่อแก้ไขปัญหา"],
        ["Promotion", "ส่วนลดจากแคมเปญการตลาด"],
        ["Management Discount", "ส่วนลดโดยผู้จัดการสาขา"],
    ]
    story.append(data_table(disc_types[0], disc_types[1:],
                            col_widths=[5*cm, PAGE_W - 4*cm - 5*cm]))
    story.append(sp(8))

    story.append(Paragraph("8.3 การไหลเวียนลูกค้ารายชั่วโมง (Hourly Flow)", sH2))
    story.append(Paragraph(
        "กราฟ Bar Chart แสดงจำนวนลูกค้าในแต่ละชั่วโมงของวัน 0:00–23:00 "
        "ช่วยระบุ Peak Hour และ Off-peak เพื่อวางแผนพนักงานและวัตถุดิบ", sBody))
    story.append(info_box(
        "ใช้ Hourly Flow ร่วมกับ Daypart Economics เพื่อตัดสินใจ เช่น "
        "เปิด/ปิดบริการ Lunch Set หรือปรับ Staffing ในช่วง Off-peak"
    ))

    story.append(Paragraph("8.4 Day-of-Week Analysis", sH2))
    story.append(Paragraph(
        "เปรียบเทียบยอดขายและจำนวนลูกค้าแยกตามวันในสัปดาห์ (จันทร์–อาทิตย์) "
        "แสดง Heatmap สี และค่าเฉลี่ย", sBody))

    story.append(Paragraph("8.5 สาขา Peak Hours", sH2))
    story.append(Paragraph(
        "ระบุชั่วโมง Top 3 ที่มียอดขายสูงสุดของแต่ละสาขา ช่วยวางแผน "
        "การเตรียมวัตถุดิบและจัดตารางงานพนักงาน", sBody))

    story.append(Paragraph("8.6 Operational Health Dashboard", sH2))
    story.append(Paragraph(
        "สรุป KPI ด้านการดำเนินงาน 5 ตัวชี้วัดในหน้าเดียว:", sBody))
    health_items = [
        "อัตราส่วนลด (Discount Rate) vs เป้าหมาย",
        "การใช้งานที่นั่ง (Seat Utilization %)",
        "เวลาเฉลี่ยต่อโต๊ะ (Average Dwell Time)",
        "Table Turnover Rate",
        "Void/Cancel Rate",
    ]
    for item in health_items:
        story.append(bullet(item))
    story.append(PageBreak())


# ── Chapter 9: Tools ───────────────────────────────────────────────────────
def ch9_tools(story):
    story.append(section_header("บทที่ 9 — เครื่องมือและการตั้งค่า (Tools & Settings)"))
    story.append(sp(10))

    story.append(Paragraph("9.1 จัดการต้นทุน (Cost Management)", sH2))

    story.append(Paragraph("9.1.1 ปรับสอบเทียบต้นทุน (Cost Recalibration)", sH3))
    story.append(Paragraph(
        "เครื่องมือปรับต้นทุนเพื่อให้ GP% บรรลุเป้าหมาย "
        "ป้อน Target Food Cost % แล้วระบบจะแนะนำว่าต้องปรับราคาวัตถุดิบไหนเท่าไร", sBody))

    story.append(Paragraph("9.1.2 อัปโหลดต้นทุนรายเดือน (Monthly Cost Upload)", sH3))
    story.append(Paragraph(
        "อัปโหลดราคาต้นทุนวัตถุดิบรายเดือนเป็นไฟล์ CSV "
        "ระบบจะใช้ราคานี้ Override ราคาปัจจุบันในเดือนที่กำหนด", sBody))

    story.append(Paragraph("รูปแบบไฟล์ CSV ที่รองรับ:", sH3))
    story.append(Paragraph("ingredient_code, month (YYYY-MM), unit_cost", sCode))
    story.append(Paragraph("ตัวอย่าง: ING-001, 2026-06, 85.50", sCode))

    steps_upload = [
        "คลิกปุ่ม 'อัปโหลดต้นทุน' ด้านบนขวา",
        "เลือกไฟล์ CSV จากเครื่อง",
        "ตรวจสอบ Preview ข้อมูลที่จะอัปโหลด",
        "คลิก 'ยืนยันอัปโหลด' — ระบบจะแจ้งผลสรุป (สำเร็จ/ข้อผิดพลาด)",
    ]
    for i, step in enumerate(steps_upload, 1):
        story.append(bullet(f"{i}. {step}"))

    story.append(sp(6))
    story.append(Paragraph("9.1.3 ตรวจสอบ BoM (BoM Ingredient Audit)", sH3))
    story.append(Paragraph(
        "รายงานความสมบูรณ์ของสูตรอาหาร (BoM) แสดง SKU ที่ยังไม่มีสูตร "
        "หรือมีส่วนผสมที่ขาดราคาต้นทุน", sBody))

    story.append(Paragraph("9.1.4 นำเข้าต้นทุนจำนวนมาก (CSV Bulk Import)", sH3))
    story.append(Paragraph(
        "อัปโหลดราคา unit_cost ของ SKU หลายรายการพร้อมกันผ่านไฟล์ CSV "
        "เหมาะสำหรับการอัปเดตต้นทุนครั้งใหญ่หลังราคาวัตถุดิบเปลี่ยน", sBody))

    story.append(Paragraph("9.2 เป้าหมายสาขา (Branch Targets)", sH2))
    story.append(Paragraph(
        "ตั้งเป้าหมายรายเดือนสำหรับแต่ละสาขา: ยอดขาย, จำนวนลูกค้า, GP%, RevPASH "
        "ระบบจะเปรียบเทียบผลจริงกับเป้าและแสดงสัญญาณสี", sBody))

    story.append(Paragraph("9.3 ต้นทุนคงที่ (Branch Fixed Costs)", sH2))
    story.append(Paragraph(
        "บันทึกต้นทุนคงที่รายเดือนของแต่ละสาขา ใช้ในการคำนวณ Operating Profit:", sBody))
    fixed_costs = [
        "ค่าเช่า (Rent)",
        "ค่าแรงพนักงาน (Labor Cost)",
        "สาธารณูปโภค — ไฟฟ้า น้ำ แก๊ส (Utilities)",
        "ค่าใช้จ่ายอื่นๆ (Miscellaneous)",
    ]
    for fc in fixed_costs:
        story.append(bullet(fc))

    story.append(Paragraph("9.4 ข้อมูลหลักสาขา (Branch Master Data)", sH2))
    story.append(Paragraph(
        "แก้ไขข้อมูลพื้นฐานสาขา เช่น ชื่อ, จำนวนที่นั่ง, เขต/จังหวัด, "
        "ผู้จัดการสาขา และสถานะ (เปิด/ปิด)", sBody))

    story.append(Paragraph("9.5 ชั่วโมงทำการ (Operating Hours)", sH2))
    story.append(Paragraph(
        "ตั้งชั่วโมงเปิด-ปิดของแต่ละสาขา แยกตามวัน ใช้ในการคำนวณ RevPASH และ Seat Utilization "
        "สามารถตั้ง 'วันหยุดพิเศษ' หรือ 'ปิดชั่วคราว' ได้", sBody))

    story.append(info_box(
        "ถ้าชั่วโมงทำการไม่ถูกต้อง จะทำให้ RevPASH และ Utilization% คำนวณผิดพลาด — "
        "ควรตรวจสอบและอัปเดตทุกครั้งที่มีการเปลี่ยนแปลงตารางสาขา"
    , "warn"))
    story.append(PageBreak())


# ── Chapter 10: Reconciliation ────────────────────────────────────────────
def ch10_recon(story):
    story.append(section_header("บทที่ 10 — การกระทบยอดรายได้ (Revenue Reconciliation)"))
    story.append(sp(10))

    story.append(Paragraph("ภาพรวม", sH2))
    story.append(Paragraph(
        "ระบบ Revenue Reconciliation ทำงานแยกจาก Dashboard หลัก (เข้าถึงได้จากพอร์ต 8443) "
        "ช่วยตรวจสอบว่ารายได้จาก POS ตรงกับเงินที่เข้าบัญชีธนาคาร/Gateway หรือไม่", sBody))

    story.append(Paragraph("แหล่งข้อมูลที่กระทบ", sH2))
    sources = [
        ["แหล่งข้อมูล", "รูปแบบไฟล์", "คำอธิบาย"],
        ["POS System", "Database", "ข้อมูลบิลจากระบบเครื่องคิดเงิน"],
        ["Bank Statement", "CSV / Excel", "รายการฝากถอนจากธนาคาร"],
        ["Credit Card Settlement", "CSV", "รายการชำระบัตรเครดิต"],
        ["Grab Food", "CSV / Report", "รายได้จาก Grab Food Delivery"],
        ["LINE MAN", "CSV / Report", "รายได้จาก LINE MAN Delivery"],
    ]
    story.append(data_table(sources[0], sources[1:],
                            col_widths=[4*cm, 3.5*cm, PAGE_W - 4*cm - 7.5*cm]))
    story.append(sp(8))

    story.append(Paragraph("ขั้นตอนการกระทบยอด", sH2))
    recon_steps = [
        ("อัปโหลดไฟล์", "อัปโหลดไฟล์ Bank Statement หรือ Gateway Report ในหน้า 'อัปโหลด'"),
        ("Mapping", "ระบบจับคู่รายการอัตโนมัติตาม วันที่, จำนวนเงิน, เลขที่อ้างอิง"),
        ("ตรวจสอบ Variance", "รายการที่ไม่ตรงกันจะแสดงสีแดง พร้อมแจ้ง Variance"),
        ("อนุมัติ", "ผู้จัดการตรวจสอบและอนุมัติหรือตั้ง Flag สำหรับรายการผิดปกติ"),
        ("รายงาน", "Export สรุปการกระทบยอดเป็น PDF หรือ CSV"),
    ]
    for title, desc in recon_steps:
        story.append(KeepTogether([
            Paragraph(f"→ {title}", S("StepTitle", fontName="SarabunB", fontSize=10,
                                       leading=16, textColor=C_ACCENT, spaceAfter=2)),
            Paragraph(f"   {desc}", sBody),
        ]))

    story.append(Paragraph("ประเภทความผิดพลาดที่พบบ่อย", sH2))
    errors = [
        ["ประเภท", "สาเหตุ", "วิธีแก้ไข"],
        ["Missing Payment", "บิล POS ไม่มีรายการตรงกันใน Bank", "ตรวจสอบวันที่ / ช่องทางชำระ"],
        ["Amount Mismatch", "จำนวนเงินต่างกัน (เช่น ค่าธรรมเนียม)", "ตรวจ Fee Structure"],
        ["Duplicate Entry", "รายการซ้ำใน Bank Statement", "แก้ไขไฟล์ก่อนอัปโหลด"],
        ["Timing Difference", "T+1 Settlement Delay", "ใช้ Tolerance Window 1-2 วัน"],
    ]
    story.append(data_table(errors[0], errors[1:],
                            col_widths=[3.5*cm, 5.5*cm, PAGE_W - 4*cm - 9*cm]))
    story.append(PageBreak())


# ── Chapter 11: FAQ ────────────────────────────────────────────────────────
def ch11_faq(story):
    story.append(section_header("บทที่ 11 — คำถามที่พบบ่อย (FAQ)"))
    story.append(sp(10))

    faqs = [
        (
            "ทำไมยอดขายในระบบไม่ตรงกับรายงาน POS?",
            "ระบบใช้ subtotal_ex_vat (ราคาก่อน VAT) ขณะที่รายงาน POS บางฉบับรวม VAT 7% "
            "ให้หาร ยอด POS ด้วย 1.07 เพื่อเปรียบเทียบ"
        ),
        (
            "GP% ที่แสดงคำนวณอย่างไร?",
            "GP% = (ยอดขาย - ต้นทุน COGS) / ยอดขาย × 100 โดย COGS ใช้ BoM ถ้ามีสูตร "
            "หรือ unit_cost ถ้าไม่มีสูตร"
        ),
        (
            "RevPASH คืออะไร ใช้ทำอะไร?",
            "Revenue Per Available Seat Hour = ยอดขาย / (ที่นั่ง × ชั่วโมงเปิด) "
            "ใช้วัดประสิทธิภาพการใช้พื้นที่ — ยิ่งสูงยิ่งดี"
        ),
        (
            "ข้อมูลรีเฟรชบ่อยแค่ไหน?",
            "API Cache รีเฟรชทุก 5 นาทีอัตโนมัติ กด 🔄 เพื่อรีเฟรชทันที"
        ),
        (
            "ทำไมบางสาขาไม่มีข้อมูล BoM?",
            "BoM ขึ้นอยู่กับว่า Recipe ถูกนำเข้าระบบสำหรับสาขานั้นหรือยัง "
            "ติดต่อทีม Data เพื่อนำเข้า Recipe"
        ),
        (
            "จะ Export ข้อมูลได้อย่างไร?",
            "ทุกหน้าที่มีตารางจะมีปุ่ม 📥 Download (CSV) หรือ 🖨️ Print "
            "สำหรับกราฟสามารถ Right-click > Save Image"
        ),
        (
            "Fixed Cost ไม่ได้ตั้งค่า P&L จะคำนวณอย่างไร?",
            "ถ้าไม่มีข้อมูล Fixed Cost ระบบจะแสดง Gross Profit เท่านั้น "
            "Operating Profit จะไม่ปรากฎจนกว่าจะกรอกข้อมูล Fixed Cost"
        ),
        (
            "ใช้บน Mobile ได้ไหม?",
            "รองรับการใช้งานบน Tablet (iPad) ขึ้นไป สำหรับ Mobile Phone ขนาดเล็ก "
            "บางหน้าอาจแสดงผลไม่สมบูรณ์ แนะนำให้ใช้ Desktop หรือ Laptop"
        ),
    ]

    for q, a in faqs:
        story.append(KeepTogether([
            Paragraph(f"Q: {q}", S("FAQ_Q", fontName="SarabunB", fontSize=10,
                                    leading=17, textColor=C_PRIMARY, spaceBefore=8, spaceAfter=3)),
            Paragraph(f"A: {a}", S("FAQ_A", fontSize=10, leading=17,
                                    leftIndent=12, textColor=colors.black, spaceAfter=6)),
            HR(C_BORDER),
        ]))
    story.append(PageBreak())


# ── Chapter 12: Glossary ───────────────────────────────────────────────────
def ch12_glossary(story):
    story.append(section_header("บทที่ 12 — คำศัพท์และนิยาม"))
    story.append(sp(10))

    terms = [
        ["คำศัพท์", "ย่อ/ภาษาอังกฤษ", "นิยาม"],
        ["กำไรขั้นต้น", "Gross Profit (GP)", "ยอดขาย หักต้นทุนอาหาร/เครื่องดื่ม"],
        ["อัตรากำไรขั้นต้น", "GP%", "GP ÷ ยอดขาย × 100"],
        ["ต้นทุนสินค้าขาย", "COGS", "ต้นทุนรวมของอาหารและเครื่องดื่มที่ขายได้"],
        ["สูตรส่วนผสม", "Bill of Materials (BoM)", "รายการส่วนผสมและปริมาณต่อ 1 หน่วยเมนู"],
        ["เมนูหน่วย", "SKU (Stock Keeping Unit)", "รายการเมนูหรือสินค้าแต่ละรายการ"],
        ["รายได้ต่อที่นั่งต่อชั่วโมง", "RevPASH", "Revenue Per Available Seat Hour"],
        ["การใช้งานที่นั่ง", "Seat Utilization", "Cover จริง ÷ Capacity × 100%"],
        ["ลูกค้า 1 คน", "Cover", "ลูกค้า 1 คนที่รับบริการ ใช้ในการคำนวณ RevPASH"],
        ["ช่วงเวลาในวัน", "Daypart", "กลุ่มชั่วโมง เช่น เช้า/กลางวัน/เย็น/ดึก"],
        ["การหมุนเวียนโต๊ะ", "Table Turnover", "จำนวนครั้งที่โต๊ะถูกใช้ต่อวัน"],
        ["ยอดขายก่อน VAT", "Subtotal ex-VAT", "ยอดขายที่ไม่รวมภาษีมูลค่าเพิ่ม 7%"],
        ["ราคาต้นทุนหน่วย", "Unit Cost", "ราคาต้นทุนต่อหน่วยของ SKU (fallback)"],
        ["เป้าหมายสาขา", "Branch Target", "ตัวเลขเป้าหมายรายเดือนที่ตั้งไว้ต่อสาขา"],
        ["ต้นทุนคงที่", "Fixed Cost", "ต้นทุนที่ไม่ขึ้นกับปริมาณขาย เช่น ค่าเช่า ค่าแรง"],
        ["กำไรจากการดำเนินงาน", "Operating Profit", "Gross Profit หักต้นทุนคงที่"],
        ["วิศวกรรมเมนู", "Menu Engineering", "การจัดกลุ่มเมนูตาม Popularity × Contribution"],
        ["การกระทบยอด", "Reconciliation", "การตรวจสอบให้ยอดเงิน 2 แหล่งตรงกัน"],
        ["ค่าต่างจากการกระทบ", "Variance", "ส่วนต่างระหว่างยอด POS กับยอด Bank"],
    ]
    story.append(data_table(terms[0], terms[1:],
                            col_widths=[4.5*cm, 4.5*cm, PAGE_W - 4*cm - 9*cm]))
    story.append(sp(12))
    story.append(HR(C_ACCENT, 1.5))
    story.append(sp(6))
    story.append(Paragraph(
        "เอกสารนี้จัดทำโดยทีมพัฒนาระบบ Oshinei Cost Dashboard | เวอร์ชัน 2026.06",
        sMuted))
    story.append(Paragraph(
        "สงวนสิทธิ์ในทรัพย์สินทางปัญญา — ใช้งานภายในองค์กรเท่านั้น",
        sMuted))


# ── Page template ──────────────────────────────────────────────────────────
def on_page(canvas_obj, doc):
    canvas_obj.saveState()
    w, h = A4
    # Footer line
    canvas_obj.setStrokeColor(C_BORDER)
    canvas_obj.setLineWidth(0.5)
    canvas_obj.line(2*cm, 1.8*cm, w - 2*cm, 1.8*cm)
    # Footer text left
    canvas_obj.setFont("Sarabun", 7.5)
    canvas_obj.setFillColor(C_MUTED)
    canvas_obj.drawString(2*cm, 1.2*cm, "Oshinei Cost Dashboard — คู่มือการใช้งาน")
    # Page number
    canvas_obj.drawRightString(w - 2*cm, 1.2*cm, f"หน้า {doc.page}")
    canvas_obj.restoreState()


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    out_path = "/home/user/oshineicost/docs/Oshinei_User_Manual_TH.pdf"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2.5*cm,
        title="Oshinei Cost Dashboard — คู่มือการใช้งาน",
        author="Oshinei Tech Team",
        subject="User Manual TH v2026.06",
    )

    story = []
    cover_page(story)
    toc_page(story)
    ch1_overview(story)
    ch2_navigation(story)
    ch3_home(story)
    ch4_sales(story)
    ch5_buffet(story)
    ch6_cost(story)
    ch7_menu(story)
    ch8_operations(story)
    ch9_tools(story)
    ch10_recon(story)
    ch11_faq(story)
    ch12_glossary(story)

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    print(f"PDF saved → {out_path}")
    import os as _os
    size = _os.path.getsize(out_path) / 1024
    print(f"File size: {size:.1f} KB")


if __name__ == "__main__":
    main()
