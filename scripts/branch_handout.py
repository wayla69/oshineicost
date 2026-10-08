# -*- coding: utf-8 -*-
"""Branch handout: one ready-to-send message and one printable card per pilot branch.

The four pilot branch logins (วัตถุดิบและใบสั่งซื้อ) exist with the system's default
password and must change it at first sign-in. This script writes what each branch needs
to get started — address, username, starting password, the five first steps — so the
owner can send it through the channel the branch already uses (LINE, a printed card).

It sends NOTHING by itself and it never touches the live system. The starting password is
read from backend/auth.py at run time (never copied into the repo); the output folder is
OUTSIDE the repo for that reason. The unlock code (Owner Write-Token) is deliberately not
part of any handout — the administrator types it on each branch phone once.

    py -3.12 scripts/branch_handout.py                 # → ~/Oshinei_branch_handout/
    py -3.12 scripts/branch_handout.py --out D:/handout
    py -3.12 scripts/branch_handout.py --only udon     # a single branch
"""
import argparse
import sys as _sys
import html
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "https://oshinei-dashboard.fly.dev"
BRANCHES = [                       # (key, branch name, username) — the accounts created on 2026-10-07
    ("ubon", "อุบลราชธานี", "branch_ubon"),
    ("udon", "อุดรธานี", "branch_udon"),
    ("khonkaen", "ขอนแก่น", "branch_khonkaen"),
    ("rayong", "ระยอง", "branch_rayong"),
]


def default_password() -> str:
    src = (ROOT / "backend" / "auth.py").read_text(encoding="utf-8")
    m = re.search(r'^DEFAULT_PASSWORD\s*=\s*"([^"]+)"', src, re.M)
    if not m:
        sys.exit("DEFAULT_PASSWORD not found in backend/auth.py")
    return m.group(1)


def message(name: str, user: str, pw: str) -> str:
    return f"""เรียน ทีมสาขา{name}

ระบบ "วัตถุดิบและใบสั่งซื้อ" พร้อมให้สาขาใช้งานแล้วครับ ใช้บนโทรศัพท์ของสาขาได้เลย

ที่อยู่ระบบ: {URL}
ชื่อผู้ใช้: {user}
รหัสผ่านเริ่มต้น: {pw}

เริ่มใช้ 5 ขั้นตอน (ประมาณ 5 นาที)
1. เปิดที่อยู่ระบบด้านบนในเบราว์เซอร์ของโทรศัพท์ แล้วเข้าสู่ระบบด้วยชื่อผู้ใช้และรหัสผ่านเริ่มต้น
2. ระบบจะให้ตั้งรหัสผ่านใหม่ทันที ตั้งแล้วจดเก็บไว้เอง หลังจากนี้เครื่องจำการเข้าสู่ระบบไว้ 30 วัน
3. กดเมนูของเบราว์เซอร์ แล้วเลือก "เพิ่มไปยังหน้าจอหลัก" ครั้งต่อไปเปิดได้เหมือนแอป
4. แจ้งผู้ดูแลระบบมาปลดล็อกโทรศัพท์เครื่องนี้ครั้งเดียว (กุญแจข้างชื่อผู้ใช้จะเป็นสีเขียว) ก่อนนั้นสาขาดูได้แต่ส่งผลนับและใบสั่งซื้อไม่ได้
5. เปิดหน้า "หน้างาน" จะเห็นงานของวันนี้ แถบล่างมี 5 ปุ่ม: หน้างาน · ตรวจนับ · สั่งสินค้า · รับสินค้า · ผลต่าง

วันสั่งของของสาขาตามปฏิทินที่ฝ่ายขายตั้งไว้ รอบแรกคือวันศุกร์ที่ 9 ต.ค. 2569 (รอบ 2) ให้นับของก่อนเปิดร้าน แล้วส่งใบสั่งซื้อก่อน 10:00 น.

ข้อควรทราบ
- รหัสผ่านเป็นของสาขา ไม่ส่งต่อทางแชตกลุ่มที่มีคนนอกสาขา
- รหัสปลดล็อกการบันทึกไม่ได้ส่งให้สาขา ผู้ดูแลระบบจะกรอกที่เครื่องให้เอง
- มีข้อสงสัย ติดต่อ Sales Support
"""


def card(name: str, user: str, pw: str) -> str:
    e = html.escape
    return f"""<section class="card">
  <p class="eyebrow">OSHINEI · วัตถุดิบและใบสั่งซื้อ</p>
  <h2>สาขา{e(name)}</h2>
  <table>
    <tr><th>ที่อยู่ระบบ</th><td>{e(URL)}</td></tr>
    <tr><th>ชื่อผู้ใช้</th><td class="mono">{e(user)}</td></tr>
    <tr><th>รหัสผ่านเริ่มต้น</th><td class="mono">{e(pw)}</td></tr>
  </table>
  <ol>
    <li>เปิดที่อยู่ระบบบนโทรศัพท์ เข้าสู่ระบบ แล้วตั้งรหัสผ่านใหม่</li>
    <li>เมนูเบราว์เซอร์ → "เพิ่มไปยังหน้าจอหลัก"</li>
    <li>ให้ผู้ดูแลระบบปลดล็อกโทรศัพท์ครั้งเดียว (กุญแจเป็นสีเขียว)</li>
    <li>เปิด "หน้างาน" ทำตามงานของวัน: นับของ → สั่งสินค้า → รับสินค้า</li>
  </ol>
  <p class="note">นับก่อนเปิดร้าน ส่งใบสั่งซื้อก่อน 10:00 น. · รอบแรก วันศุกร์ที่ 9 ต.ค. 2569 · ติดต่อ Sales Support</p>
</section>"""


PAGE = """<!doctype html><html lang="th"><head><meta charset="utf-8"><title>บัตรเริ่มใช้งานสาขานำร่อง</title>
<style>
  body {{ font-family: 'Prompt', 'Noto Sans Thai', Tahoma, sans-serif; margin: 0; color: #0f172a; }}
  .sheet {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0; }}
  .card {{ box-sizing: border-box; padding: 14mm 12mm; border: 1px dashed #94a3b8; height: 148mm; page-break-inside: avoid; }}
  .eyebrow {{ font-size: 10pt; letter-spacing: 1px; color: #1f3a6b; font-weight: 600; margin: 0; }}
  h2 {{ font-size: 20pt; margin: 4mm 0 6mm; color: #1f3a6b; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 5mm; }}
  th {{ text-align: left; font-weight: 600; width: 34%; padding: 2mm 0; font-size: 11pt; color: #475569; }}
  td {{ padding: 2mm 0; font-size: 12pt; }}
  .mono {{ font-family: Consolas, 'Courier New', monospace; font-size: 13pt; font-weight: 700; }}
  ol {{ font-size: 11pt; line-height: 1.6; padding-left: 5mm; margin: 0 0 4mm; }}
  .note {{ font-size: 10pt; color: #475569; margin: 0; }}
  @page {{ size: A4 landscape; margin: 0; }}
</style></head><body><div class="sheet">
{cards}
</div></body></html>
"""


def main() -> None:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(Path.home() / "Oshinei_branch_handout"))
    ap.add_argument("--only", choices=[k for k, _, _ in BRANCHES])
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pw = default_password()
    todo = [b for b in BRANCHES if not a.only or b[0] == a.only]
    for key, name, user in todo:
        (out / f"{user}.txt").write_text(message(name, user, pw), encoding="utf-8")
    (out / "cards.html").write_text(PAGE.format(cards="\n".join(card(n, u, pw) for _, n, u in todo)), encoding="utf-8")
    (out / "README.txt").write_text(
        f"สร้างเมื่อ {date.today().isoformat()} · {len(todo)} สาขา\n"
        "- <ชื่อผู้ใช้>.txt = ข้อความพร้อมส่ง (LINE/อีเมล) ส่งให้เฉพาะสาขานั้น\n"
        "- cards.html = บัตรพิมพ์ A4 แนวนอน 4 ใบ เปิดในเบราว์เซอร์แล้วสั่งพิมพ์ ตัดตามเส้นประ\n"
        "- โฟลเดอร์นี้มีรหัสผ่านเริ่มต้น ลบหลังแจกเสร็จ ห้ามอัปโหลดขึ้น GitHub\n"
        "- รหัสปลดล็อกการบันทึกไม่อยู่ในไฟล์เหล่านี้ ผู้ดูแลระบบกรอกที่เครื่องสาขาเอง\n", encoding="utf-8")
    print(f"wrote {len(todo)} handouts → {out}")
    for _, name, user in todo:
        print(f"  {name}: {user}.txt")
    print("  cards.html (print), README.txt")


if __name__ == "__main__":
    main()
