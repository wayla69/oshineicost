# -*- coding: utf-8 -*-
"""Task cards for branch staff: ONE printable A4 page per task (batch 3, 4.7 of the improvement proposal, 2026-10-12).

    py -3.14 scripts\\branch_task_cards.py            # writes docs/pilot/cards/card_<task>.html and .pdf, checks each is one page
    py -3.14 scripts\\branch_task_cards.py --html     # the HTML only (no browser)
    py -3.14 scripts\\branch_task_cards.py --png DIR  # also the first page of each PDF as a PNG, for a look

The four cards - ตัดเบิก, สั่งสินค้า, รับสินค้าและแจ้งเคลม, ตรวจนับสิ้นเดือน - say what the branch's screens say after batches 1-2
(same buttons, same words), in formal plain Thai. They hold no login and no password (the cards with those are
scripts/branch_handout.py, which writes outside the repo). Print them, laminate them, or hand the PDF to a branch.

The PDF is made with Chrome headless through Playwright (like scripts/render_sop.py); a card that does not fit one A4 page
fails the run, so the text is shortened rather than the type made smaller.
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "pilot" / "cards"
GUIDE = "/docs/pilot/branch_guide.html"

# id -> card. Steps, warn and note may carry <b>…</b> (the words printed on the screen's buttons).
CARDS = [
    {"id": "issue", "no": "1", "title": "ตัดเบิก", "when": "ทุกวัน · ยืนยันก่อนส่งใบสั่งซื้อของวันนั้น",
     "steps": [
         "แตะ <b>ตัดเบิก</b> ที่แถบล่าง แถบวันแสดง <b>วันนี้</b> และวันที่ยังไม่ทำ (สีส้ม)",
         "แตะโซนทีละโซน: <b>แช่แข็ง</b> · <b>แช่เย็น</b> · <b>ของแห้ง</b> · <b>บาร์น้ำ</b> · <b>อื่น ๆ</b> รายการที่ไม่ได้ใช้ใน 14 วันซ่อนอยู่ แตะ <b>แสดงทั้งหมด</b>",
         "ช่อง <b>เบิกใช้</b>: ใส่จำนวนที่ครัวใช้ในวันนั้นตามหน่วยใต้ช่อง ไม่ได้ใช้ให้เว้นว่าง",
         "วันที่ใช้ใกล้เคียงเมื่อวาน แตะ <b>เบิกเหมือนเมื่อวาน</b> แล้วแก้เฉพาะรายการที่ต่าง",
         "ช่อง <b>ของเสีย</b>: ใส่จำนวนที่ทิ้ง แล้วเลือกสาเหตุ (ถ้าเลือก “อื่น ๆ” ต้องพิมพ์คำอธิบาย)",
         "แตะ <b>ยืนยันใบตัดเบิก</b> คำถามบอกจำนวนรายการที่กรอก รายการที่ว่างถือว่าไม่ได้เบิก แตะ <b>ยืนยัน</b>",
     ],
     "warn": "ยืนยันแล้วแก้เองไม่ได้ ลืมรายการในวันเดียวกัน แตะ <b>เบิกเพิ่ม</b> แล้วกรอกเฉพาะจำนวนที่ต้องบวกเพิ่ม (เป็นจำนวนเต็ม) "
             "ต้องแก้รายการเดิม แจ้งประสานงานขายให้เปิดแก้ไข",
     "note": "วันที่ลืมทำ แตะวันนั้นในแถบวัน ทำย้อนหลังได้ แต่นับเป็น “ยืนยันล่าช้า” · ใบตัดเบิกของวันนี้ต้องยืนยันก่อนจึงส่งใบสั่งซื้อได้"},
    {"id": "order", "no": "2", "title": "สั่งสินค้า", "when": "ทุกรอบสั่ง · ส่งใบสั่งซื้อก่อน 13:15 สองวันก่อนวันส่ง",
     "steps": [
         "แตะ <b>สั่งสินค้า</b> ที่แถบล่าง แถวบนสุดบอกรอบ วันส่ง และเวลาที่ต้องส่งใบสั่งซื้อ พร้อมสถานะ <b>เปิดรับ</b> · <b>เลยเวลา</b> · <b>สั่งแล้ว</b>",
         "ถ้าเห็น “ยืนยันใบตัดเบิกวันนี้แล้วส่งใบสั่งซื้อได้” แตะ <b>ไปที่ตัดเบิก</b> ยืนยันของวันนี้ก่อน แล้วกลับมา",
         "ดูช่อง “แนะนำ” และ “คงเหลือ” ของแต่ละรายการ ช่อง <b>สั่ง</b> ตั้งต้นเท่ายอดแนะนำ เห็นด้วยไม่ต้องแก้ ป้าย “ประมาณจากยอดขาย N วัน” คือวันที่ยังไม่ทำใบตัดเบิก ระบบหักตามยอดขายไว้ก่อน",
         "แก้ช่อง <b>สั่ง</b> เฉพาะรายการที่ไม่เห็นด้วย ต่างจากยอดแนะนำเกิน 20% ให้เลือกเหตุผลใต้รายการ",
         "รู้ว่ารอบนี้ลูกค้าจะต่างจากปกติ แตะ <b>ยอดขายรอบนี้ต่างจากปกติ?</b> ใส่ % และเหตุผล แล้วแตะ <b>ใช้กับยอดแนะนำ</b>",
         "แตะ <b>ส่งใบสั่งซื้อ</b> คำถามยืนยันบอกจำนวนรายการ มูลค่า และเทียบกับรอบก่อน แตะ <b>ส่งใบสั่งซื้อ</b>",
     ],
     "warn": "เลยเวลาตัดรอบ 13:15 ยังส่งได้ แต่มีค่าปรับล่าช้าที่ต้องชำระให้คลังภายนอก · "
             "เวลา 11:00 ถ้ายังไม่ส่ง โทรศัพท์ที่เปิดการแจ้งเตือนไว้จะเตือนว่าเหลืออีกประมาณ 2 ชั่วโมง",
     "note": "ส่งแล้วแตะ <b>เปิดแก้ไข</b> ได้จนกว่าคลังจะยืนยันยอดส่ง · สาขาใหม่ที่ขายไม่ถึง 14 วัน ให้กรอกยอดขายที่คาดของรอบนี้ก่อนส่ง"},
    {"id": "receive", "no": "3", "title": "รับสินค้าและแจ้งเคลม", "when": "ทุกครั้งที่สินค้ามาถึง · ของเสียหายที่พบภายหลังแจ้งเคลม",
     "steps": [
         "แตะ <b>รับสินค้า</b> ที่แถบล่าง หัวข้อ “ใบสั่งซื้อที่คลังจัดส่งแล้ว” มีหลายใบ แตะ <b>รับสินค้า</b> ที่ใบที่ของมาถึง",
         "ตอบคำถามเดียว <b>รับครบตามที่คลังส่งทุกรายการ?</b> ครบแตะ <b>รับครบ</b>",
         "ไม่ครบแตะ <b>มีรายการไม่ครบ</b> พิมพ์ชื่อวัตถุดิบ แตะ <b>เลือก</b> แก้ช่อง <b>รับจริง</b> และเลือกสาเหตุ รายการอื่นนับว่ารับครบ",
         "ปลาทั้งตัว: กรอกจำนวนตัวที่ได้รับเป็นจำนวนเต็มข้างน้ำหนัก ขนาดบรรจุไม่ตรงกับระบบ แตะ <b>ขนาดบรรจุไม่ตรง</b>",
         "ติ๊ก <b>ตรวจสภาพและจำนวนครบแล้ว</b> แล้วแตะ <b>บันทึกรับสินค้า</b> และแตะ <b>บันทึกรับสินค้า</b> ในคำถามยืนยัน",
         "พบของเสียหายภายหลัง: การ์ด <b>แจ้งเคลมสินค้า</b> → <b>แจ้งเคลม</b> ที่ใบนั้น → เลือกรายการ ใส่จำนวนและสาเหตุ → <b>ส่งแจ้งเคลม</b> แล้วส่งรูปถ่ายทางไลน์ถึงประสานงานขาย",
     ],
     "warn": "ระบบไม่รับรูปถ่าย ส่งทางไลน์ถึงประสานงานขายเท่านั้น · รายการที่รับครบโดยไม่แจ้งในวันรับ อาจไม่ได้รับพิจารณา",
     "note": "บันทึกผิดใบ แตะ <b>ยกเลิกใบรับสินค้า</b> ต้องระบุเหตุผลและประสานงานขายเห็นเหตุผลนั้น · ใบที่เกิดจากการอนุมัติเคลม และใบในงวดที่ปิดแล้ว ยกเลิกไม่ได้"},
    {"id": "count", "no": "4", "title": "ตรวจนับสิ้นเดือน (นับครบทุกรายการ)", "when": "วันที่ 1 ของทุกเดือน นับครบทุกรายการก่อนรับสินค้าของวันนั้น · ปิดงวดสิ้นเดือนต้องนับ 100%",
     "steps": [
         "แตะ <b>ตรวจนับสิ้นเดือน</b> ที่หน้าแรก (หรือแตะแถวงานตรวจนับใน <b>ต้องทำวันนี้</b>) หน้าจอบอกว่าครั้งนี้เป็นต้นงวดหรือปลายงวด และนับครบทุกรายการ",
         "แตะโซนแล้วนับทีละโซน เครื่องหมาย ✓ ที่โซนคือนับครบทุกรายการของโซนนั้น (ใส่ 0 ถ้าไม่มีของ ช่องว่างถือว่ายังไม่ได้นับ)",
         "ใส่จำนวนที่นับจริงตามหน่วยในแถว ใช้ปุ่ม − / + หรือพิมพ์ รายการที่มาเป็นกล่องของแพ็คมี 2 ช่อง <b>กล่อง</b> และ <b>แพ็ค</b> ใส่เท่าที่นับได้ ระบบรวมให้",
         "ติ๊ก <b>เฉพาะรายการที่ยังไม่ได้นับ</b> เพื่อดูว่าเหลืออะไร ระบบบันทึกร่างให้เอง ส่งร่างค้างไว้ได้ แต่ยังไม่ใช่การปิดงวด",
         "แตะ <b>ส่งผลตรวจนับ</b> ถ้ายังมีรายการที่ไม่ได้นับ ระบบแจ้ง “ยังไม่ได้นับ N รายการ ปิดงวดต้องตรวจนับครบทุกรายการ” พร้อมชื่อรายการแรก ๆ แตะ <b>กลับไปนับ</b>",
         "นับครบแล้วแตะ <b>ส่งผลตรวจนับ</b> ในคำถามยืนยัน · ไม่มีทางปิดงวดด้วยการนับบางส่วน การนับหมุนเวียนกลุ่มมูลค่าสูง (ถ้าเปิดใช้) เป็นการตรวจเสริมระหว่างเดือน ไม่ใช่การปิดงวด",
     ],
     "warn": "ส่งแล้วแก้ไม่ได้ ถ้าผิดให้นับใหม่ · ครั้งแรกเป็นต้นงวด ครั้งต่อไปเป็นปลายงวดและเป็นต้นงวดของเดือนถัดไปให้เอง · ปิดงวดเดือนแล้วทำใหม่ไม่ได้",
     "note": "หลังส่งผลปลายงวด ที่ปุ่ม <b>ผลต่างการใช้</b> ในหน้าแรก ให้เหตุผลเฉพาะรายการที่ระบบถาม แล้วแตะ <b>ยืนยันรับสินค้าครบ</b>"},
]

CSS = """
  @page { size: A4 portrait; margin: 0; }
  * { box-sizing: border-box; }
  html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  body { margin: 0; color: #0f172a; font: 16pt/1.5 "Leelawadee UI", "Noto Sans Thai", "Sarabun", Tahoma, sans-serif; background: #fff; }
  .page { width: 210mm; height: 296mm; padding: 14mm 16mm 12mm; display: flex; flex-direction: column; overflow: hidden; }
  .eyebrow { margin: 0; font-size: 10pt; letter-spacing: .5px; color: #1f3a6b; font-weight: 700; }
  h1 { margin: 3mm 0 1mm; font-size: 30pt; line-height: 1.2; color: #1f3a6b; display: flex; align-items: center; gap: 5mm; }
  h1 .no { flex: none; width: 15mm; height: 15mm; border-radius: 50%; background: #1f3a6b; color: #fff; font-size: 22pt; display: flex; align-items: center; justify-content: center; }
  .when { margin: 0 0 5mm; padding: 2mm 4mm; border-radius: 3mm; background: #e8edf7; color: #1f3a6b; font-weight: 600; font-size: 13pt; }
  ol { margin: 0; padding: 0; list-style: none; counter-reset: s; }
  li { counter-increment: s; position: relative; padding: 0 0 0 13mm; margin: 0 0 4.2mm; min-height: 9mm; }
  li::before { content: counter(s); position: absolute; left: 0; top: .3mm; width: 9mm; height: 9mm; border-radius: 50%; border: 0.6mm solid #1f3a6b;
               color: #1f3a6b; font-weight: 700; font-size: 13pt; display: flex; align-items: center; justify-content: center; }
  b { color: #1f3a6b; }
  .box { margin: 2mm 0 0; padding: 3mm 4.5mm; border-radius: 3mm; font-size: 13.5pt; line-height: 1.5; }
  .warn { background: #fdf1dc; color: #7c4a00; border: 0.4mm solid #e0a43a; }
  .warn b { color: #7c4a00; }
  .note { background: #f1f5f9; color: #334155; }
  .foot { margin-top: auto; padding-top: 3mm; border-top: 0.3mm solid #cbd5e1; font-size: 10.5pt; color: #475569; display: flex; justify-content: space-between; gap: 6mm; }
"""

PAGE = """<!doctype html>
<html lang="th">
<head>
<meta charset="utf-8">
<title>บัตรงานสาขา · {title}</title>
<style>{css}</style>
</head>
<body>
<div class="page">
  <p class="eyebrow">OSHINEI · วัตถุดิบและใบสั่งซื้อ · บัตรงานของสาขา</p>
  <h1><span class="no">{no}</span>{title}</h1>
  <p class="when">{when}</p>
  <ol>
{steps}
  </ol>
  <p class="box warn">{warn}</p>
  <p class="box note">{note}</p>
  <div class="foot"><span>รับการแจ้งเตือนบนมือถือ: แตะ <b>ฉัน</b> แตะ <b>เปิดการแจ้งเตือน</b> ในการ์ด “แจ้งเตือนตอนเช้า”</span><span>มีข้อสงสัย ติดต่อประสานงานขาย · คู่มือ: {guide}</span></div>
</div>
</body>
</html>
"""


def card_html(c: dict) -> str:
    steps = "\n".join(f"    <li>{s}</li>" for s in c["steps"])
    return PAGE.format(css=CSS, title=c["title"], no=c["no"], when=c["when"], steps=steps, warn=c["warn"], note=c["note"], guide=GUIDE)


def pdf_pages(path: Path) -> int:
    return len(re.findall(rb"/Type\s*/Page[^s]", path.read_bytes()))


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--html", action="store_true", help="write the HTML only (no browser, no PDF)")
    ap.add_argument("--png", help="also write the first page of each PDF as a PNG into this folder")
    a = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    for c in CARDS:
        (OUT / f"card_{c['id']}.html").write_text(card_html(c), encoding="utf-8")
    print(f"wrote {len(CARDS)} html -> {OUT}")
    if a.html:
        return 0
    from playwright.sync_api import sync_playwright
    bad = []
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        for c in CARDS:
            html = OUT / f"card_{c['id']}.html"
            pdf = OUT / f"card_{c['id']}.pdf"
            pg = b.new_page()
            pg.goto("file:///" + html.as_posix(), wait_until="networkidle")
            over = pg.evaluate("(() => { const p = document.querySelector('.page'); return p.scrollHeight - p.clientHeight; })()")
            pg.pdf(path=str(pdf), format="A4", print_background=True, prefer_css_page_size=True)
            n = pdf_pages(pdf)
            print(f"  {pdf.name}: {n} page(s), overflow {over}px")
            if n != 1 or over > 0:
                bad.append(c["id"])
            if a.png:
                Path(a.png).mkdir(parents=True, exist_ok=True)
                pg.set_viewport_size({"width": 794, "height": 1123})
                pg.screenshot(path=str(Path(a.png) / f"card_{c['id']}.png"), full_page=False)
            pg.close()
        b.close()
    if bad:
        print("DOES NOT FIT ONE PAGE:", ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
