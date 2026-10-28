# -*- coding: utf-8 -*-
"""Branch handout: one ready-to-send message and one printable card per branch.

The branch logins (วัตถุดิบและใบสั่งซื้อ) each have their own temporary password and must change
it at first sign-in. This script writes what each branch needs to get started - address, username,
starting password, the first steps - so the owner can send it through the channel the branch already
uses (LINE, a printed card).

It sends NOTHING by itself and it never touches the live system. The starting passwords come from
--passwords: temp_passwords.csv, written by branch_accounts.py --yes or printed by
backend/rotate_default_passwords.py --yes (code guard 2, 2026-10-28: the shared default no longer signs in
on the live server). A login missing from that file gets no handout. The output folder is OUTSIDE the repo
for that reason. A branch login saves without the owner write-token (2026-10-08), so no unlock step or code is
part of any handout.

Which branches (the list is the same file branch_accounts.py reads):

    py -3.12 scripts/branch_handout.py --template            # writes branch_list_template.xlsx (the 5 pilot rows filled)
    py -3.12 scripts/branch_handout.py --list D:/onboarding/branches.xlsx        # one handout per row
    py -3.12 scripts/branch_handout.py --list D:/onboarding/accounts_result.csv  # the result of branch_accounts.py --yes
    py -3.12 scripts/branch_handout.py --all --db D:/copy/oshinei.db             # every active branch of a DB copy
    py -3.12 scripts/branch_handout.py                       # no option: the five pilot branches
    py -3.12 scripts/branch_handout.py --list ... --only udn                     # a single branch
    py -3.12 scripts/branch_handout.py --role franchise --list D:/onboarding/owner_accounts_result.csv
                                                                                 # the franchise owners' messages and cards (own_<ตัวย่อ>, page ร้านของฉัน)

    --passwords FILE  temp_passwords.csv (username,role,branch,password) - required
    --out DIR     default ~/Oshinei_branch_handout
    --db FILE     a COPY of the database: resolves branch ids for rows with neither a username nor an abbreviation (wh_<id>)

The login is wh_<ตัวย่อ> (column ตัวย่อ of the list). A row with neither a username nor an abbreviation needs the
branch id (--db), or take it from accounts_result.csv, which branch_accounts.py fills in. Rows that branch_accounts.py marked ไม่สำเร็จ are skipped, and so are rows marked มีอยู่แล้ว
(that login may already have its own password; add --include-existing to write them anyway).
"""
import argparse
import html
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import branch_list as BL  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
URL = "https://oshinei-dashboard.fly.dev"
BRANCHES = [(abbr, name, BL.PREFIX + abbr) for name, abbr in BL.PILOT_ABBRS.items()]     # (key, branch name, username): the five pilot branches
PER_PAGE = 4                       # cards on one A4 page (2 x 2)


def read_passwords(path) -> dict:
    """username -> temporary password, from temp_passwords.csv."""
    import csv
    if not path:
        sys.exit("ต้องระบุ --passwords (temp_passwords.csv) แต่ละบัญชีมีรหัสผ่านชั่วคราวของตนเอง")
    p = Path(path)
    if not p.exists():
        sys.exit(f"ไม่พบไฟล์รหัสผ่าน {p}")
    with open(p, encoding="utf-8-sig", newline="") as f:
        return {r["username"].strip(): r["password"].strip() for r in csv.DictReader(f)
                if (r.get("username") or "").strip() and (r.get("password") or "").strip()}


def message(name: str, user: str, pw: str) -> str:
    return f"""เรียน ทีมสาขา{name}

ระบบ "วัตถุดิบและใบสั่งซื้อ" พร้อมให้สาขาใช้งานแล้วครับ ใช้บนโทรศัพท์ของสาขาได้เลย

ที่อยู่ระบบ: {URL}
ชื่อผู้ใช้: {user}
รหัสผ่านเริ่มต้น: {pw}

เริ่มใช้ 4 ขั้นตอน (ประมาณ 5 นาที)
1. เปิดที่อยู่ระบบด้านบนในเบราว์เซอร์ของโทรศัพท์ แล้วเข้าสู่ระบบด้วยชื่อผู้ใช้และรหัสผ่านเริ่มต้น
2. ต้องตั้งรหัสผ่านใหม่ทันที ตั้งแล้วจดเก็บไว้เอง หลังจากนี้เครื่องจำการเข้าสู่ระบบไว้ 30 วัน
3. กดเมนูของเบราว์เซอร์ แล้วเลือก "เพิ่มไปยังหน้าจอหลัก" ครั้งต่อไปเปิดได้เหมือนแอป
4. เปิด "หน้าแรก" จะเห็นงานของวันนี้ใน "ต้องทำวันนี้" แถบล่างมี 5 ปุ่ม: หน้าแรก · ตัดเบิก · สั่งของ · รับของ · ฉัน ส่วน "ส่วนต่างการใช้" และ "นับของสิ้นเดือน" เปิดจากปุ่มใหญ่ในหน้าแรก

งานประจำของสาขา
- ทุกวัน "ตัดเบิก": ใส่จำนวน "เบิกใช้" และ "ของเสีย" (ของเสียเลือกสาเหตุ) แล้วแตะ "ส่งใบตัดเบิก" ไม่ต้องนับของก่อนสั่ง
- ใน 14 วันแรกยังไม่มีจำนวนที่ควรสั่ง ให้กรอกยอดสั่งเอง ส่งใบตัดเบิกให้ครบ 14 วัน (ประมาณวันที่ 15 พ.ย.) จำนวนที่ควรสั่งจึงขึ้น และหักของที่ควรเหลือให้แล้ว
- ต้องส่งใบตัดเบิกของวันนี้ก่อนจึงส่งใบสั่งซื้อได้ ใบที่ส่งแล้วแก้ไขเองไม่ได้ หากต้องแก้ไข ให้แจ้ง ประสานงานขาย เปิดแก้ไข
- "สั่งของ": ตรวจจำนวนที่ควรสั่ง (ช่วง 14 วันแรกกรอกช่อง "สั่ง" เอง) แก้ช่อง "สั่ง" เฉพาะรายการที่ไม่เห็นด้วย แล้วแตะ "ส่งใบสั่งซื้อ" ระบบถามอีกครั้งก่อนส่ง รอบส่งของสาขาตามปฏิทินที่ประสานงานขายตั้งไว้ ให้ส่งก่อนเวลาส่งสุดท้าย 13:15 น. สองวันก่อนวันส่ง (ส่งของวันอังคาร ส่งใบก่อนวันเสาร์ 13:15 น.) หากส่งช้ากว่าเวลา ยังส่งได้ แต่มีค่าปรับที่ต้องจ่ายให้คลังภายนอก
- "รับของ": ตอบคำถามเดียว "รับครบตามที่คลังส่งทุกรายการ?" ได้ครบแตะ "รับครบ" ไม่ครบแตะ "มีรายการไม่ครบ" แล้วเลือกเฉพาะรายการนั้น ใส่ "รับจริง" และเลือกสาเหตุ จากนั้นติ๊ก "ตรวจสภาพและจำนวนครบแล้ว" และแตะ "บันทึกรับของ"
- ส่วนที่รับน้อยกว่าที่คลังส่ง ระบบแจ้งของขาดให้ประสานงานขายในครั้งเดียว · ของที่พบว่าชำรุด เสีย หรือส่งผิดภายหลัง แตะ "แจ้งของขาด/เสีย" ที่หน้ารับของ · ส่งรูปถ่ายของที่แจ้งให้ประสานงานขายทางไลน์
- "ส่วนต่าง": ระบบถามเหตุผลเฉพาะรายการของแพง เลือกเหตุผลแล้วแตะ "บันทึก"
- วันที่ 1 พ.ย. 2569 "นับของสิ้นเดือน": นับของครั้งแรกให้ครบทุกรายการก่อนรับของ หลังจากนั้นนับทุกวันที่ 1 ของเดือน นับให้ครบทุกรายการ ใส่ 0 ถ้าไม่มีของ ช่องว่างถือว่ายังไม่ได้นับ ส่งผลนับไม่ได้จนกว่าจะครบ (ปิดเดือนต้องนับ 100%)

ข้อควรทราบ
- รหัสผ่านเป็นของสาขา ไม่ส่งต่อทางแชตกลุ่มที่มีคนนอกสาขา
- ตัดเบิก สั่งของ และรับของ กรอกในแอปหรือใช้ไฟล์ Excel ก็ได้ (ตัดเบิกครั้งละไม่เกิน 7 วัน): ดาวน์โหลดแบบฟอร์ม → กรอก → นำเข้าไฟล์ ส่วนแซลมอนทั้งตัว (ชั่งน้ำหนักและนับตัว) กรอกในแอปเท่านั้น
- สาขาส่งใบตัดเบิก สั่ง และรับของได้ทันทีหลังเข้าสู่ระบบ ไม่ต้องปลดล็อกเครื่องหรือใช้รหัสอื่นเพิ่ม
- การแจ้งเตือนบนมือถือ: แตะ "ฉัน" ที่แถบล่าง แล้วแตะ "เปิดการแจ้งเตือน" ในการ์ด "แจ้งเตือนตอนเช้า" ครั้งเดียวต่อเครื่อง เครื่องเตือนงานของวันเวลา 08:00 และเตือนเวลา 11:00 เมื่อยังไม่ส่งใบสั่งซื้อที่ต้องส่งก่อน 13:15
- คู่มือสาขาอยู่ที่ปุ่ม "คู่มือ 1 หน้า ↗" ที่หัวของหน้าตัดเบิก สั่งของ และรับของ หรือเปิด {URL}/docs/pilot/branch_guide.html หลังเข้าสู่ระบบ
- มีข้อสงสัย ติดต่อ ประสานงานขาย
"""


def owner_message(name: str, user: str, pw: str) -> str:
    """The franchise owner's variant (PR-L, --role franchise): what the page ร้านของฉัน shows and how to sign in."""
    return f"""เรียน เจ้าของสาขา{name}

ระบบ "ร้านของฉัน" พร้อมให้เจ้าของสาขาดูผลการดำเนินงานของสาขา{name}แล้ว ใช้บนโทรศัพท์ได้เลย

ที่อยู่ระบบ: {URL}
ชื่อผู้ใช้: {user}
รหัสผ่านเริ่มต้น: {pw}

เริ่มใช้ 3 ขั้นตอน (ประมาณ 3 นาที)
1. เปิดที่อยู่ระบบด้านบนในเบราว์เซอร์ของโทรศัพท์ แล้วเข้าสู่ระบบด้วยชื่อผู้ใช้และรหัสผ่านเริ่มต้น
2. ต้องตั้งรหัสผ่านใหม่ทันที ตั้งแล้วจดเก็บไว้เอง
3. กดเมนูของเบราว์เซอร์ แล้วเลือก "เพิ่มไปยังหน้าจอหลัก" ครั้งต่อไปเปิดได้เหมือนแอป

หน้า "ร้านของฉัน" แสดงผลของสาขาในเดือนที่เลือก (เลือกดูย้อนหลังรายเดือนได้) เรียงตามการ์ด
- สรุป "ดี / ควรดู / ต้องแก้" ของเดือน อ่านเป็นอันดับแรก
- ยอดขาย: ยอดสะสมเทียบเดือนก่อนและปีก่อน ยอดวันล่าสุด จำนวนบิล ยอดเฉลี่ยต่อบิล ส่วนลด และกราฟยอดขาย 30 วัน
- กำไรขั้นต้นและต้นทุนอาหาร: ตัวเลขเดียวต่อเรื่อง เทียบกับเป้า
- วัตถุดิบ: ส่วนต่างการใช้ในรอบนับของล่าสุด และสัดส่วนของเสีย
- วินัยการทำงานของสาขา: ใบตัดเบิก ใบสั่งซื้อ การรับของ และการแจ้งของขาด/เสีย
- เมนูขายดี 10 อันดับ

ข้อควรทราบ
- บัญชีนี้ดูได้เฉพาะสาขา{name} และดูได้อย่างเดียว ไม่มีข้อมูลบิลรายใบ สูตร ราคาวัตถุดิบ หรือสาขาอื่น และแก้ไขข้อมูลไม่ได้
- รหัสผ่านเป็นของเจ้าของสาขา ไม่ส่งต่อทางแชตกลุ่มที่มีคนอื่น
- ยอดขายไม่รวม VAT ข้อมูลของวันล่าสุดอาจยังไม่ครบ ระบบปรับตัวเลขทุกครั้งที่ได้รับข้อมูลใหม่
- คู่มือเจ้าของสาขา อธิบายความหมายของแต่ละการ์ดและวิธีตัดสินของสรุป: {URL}/docs/pilot/owner_guide.html (เปิดหลังเข้าสู่ระบบ)
- มีข้อสงสัย ติดต่อประสานงานขาย
"""


def owner_card(name: str, user: str, pw: str) -> str:
    e = html.escape
    return f"""<section class="card">
  <p class="eyebrow">OSHINEI · ร้านของฉัน (เจ้าของสาขา)</p>
  <h2>สาขา{e(name)}</h2>
  <table>
    <tr><th>ที่อยู่ระบบ</th><td class="url">{e(URL)}</td></tr>
    <tr><th>ชื่อผู้ใช้</th><td class="mono">{e(user)}</td></tr>
    <tr><th>รหัสผ่านเริ่มต้น</th><td class="mono">{e(pw)}</td></tr>
  </table>
  <ol>
    <li>เปิดที่อยู่ระบบบนโทรศัพท์ เข้าสู่ระบบ แล้วตั้งรหัสผ่านใหม่</li>
    <li>เมนูเบราว์เซอร์ → "เพิ่มไปยังหน้าจอหลัก"</li>
    <li>เปิด "ร้านของฉัน" อ่านสรุป ดี / ควรดู / ต้องแก้ แล้วดูยอดขาย กำไรขั้นต้น วัตถุดิบ และวินัยการทำงานของสาขา เลือกดูย้อนหลังรายเดือนได้</li>
  </ol>
  <p class="note">ดูได้เฉพาะสาขาของท่าน และดูได้อย่างเดียว · คู่มือ: {e(URL)}/docs/pilot/owner_guide.html · รหัสผ่านเป็นของเจ้าของสาขา ไม่ส่งต่อ · ติดต่อประสานงานขาย</p>
</section>"""


def card(name: str, user: str, pw: str) -> str:
    e = html.escape
    return f"""<section class="card">
  <p class="eyebrow">OSHINEI · วัตถุดิบและใบสั่งซื้อ</p>
  <h2>สาขา{e(name)}</h2>
  <table>
    <tr><th>ที่อยู่ระบบ</th><td class="url">{e(URL)}</td></tr>
    <tr><th>ชื่อผู้ใช้</th><td class="mono">{e(user)}</td></tr>
    <tr><th>รหัสผ่านเริ่มต้น</th><td class="mono">{e(pw)}</td></tr>
  </table>
  <ol>
    <li>เปิดที่อยู่ระบบบนโทรศัพท์ เข้าสู่ระบบ แล้วตั้งรหัสผ่านใหม่</li>
    <li>เมนูเบราว์เซอร์ → "เพิ่มไปยังหน้าจอหลัก"</li>
    <li>วันที่ 1 พ.ย. 2569 นับของครั้งแรกให้ครบทุกรายการ (ใส่ 0 ถ้าไม่มีของ) แล้วเปิด "หน้าแรก" ทำตามงานของวัน: ตัดเบิก (ส่งใบตัดเบิกทุกวัน) → สั่งของ (ส่งใบสั่งซื้อ) → รับของ (ตอบ "รับครบ" หรือ "มีรายการไม่ครบ")</li>
  </ol>
  <p class="note">ต้องส่งใบตัดเบิกก่อนส่งใบสั่งซื้อ · ส่งก่อน 13:15 น. สองวันก่อนวันส่ง (ส่งช้ามีค่าปรับ) · 14 วันแรกยังไม่มีจำนวนที่ควรสั่ง กรอกยอดสั่งเอง · กรอกในไฟล์ Excel ก็ได้ · ของเสียหายภายหลัง แตะ "แจ้งของขาด/เสีย" และส่งรูปทางไลน์ · ติดต่อ ประสานงานขาย</p>
</section>"""


# A4 portrait, four cards a page (2 x 2, each 105 x 148.5 mm). One .sheet per page, so any number of branches prints cleanly.
PAGE = """<!doctype html><html lang="th"><head><meta charset="utf-8"><title>บัตรเริ่มใช้งานสาขา</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: 'Prompt', 'Noto Sans Thai', Tahoma, sans-serif; margin: 0; color: #0f172a; }}
  .sheet {{ width: 210mm; height: 297mm; display: grid; grid-template-columns: 105mm 105mm; grid-template-rows: 148.5mm 148.5mm; }}
  .sheet + .sheet {{ page-break-before: always; }}
  .card {{ padding: 9mm 8mm; border: 1px dashed #94a3b8; overflow: hidden; page-break-inside: avoid; }}
  .eyebrow {{ font-size: 8.5pt; letter-spacing: 1px; color: #1f3a6b; font-weight: 600; margin: 0; }}
  h2 {{ font-size: 16pt; margin: 3mm 0 4mm; color: #1f3a6b; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 4mm; }}
  th {{ text-align: left; font-weight: 600; width: 32%; padding: 1.5mm 0; font-size: 9.5pt; color: #475569; vertical-align: top; }}
  td {{ padding: 1.5mm 0; font-size: 10pt; }}
  .url {{ word-break: break-all; font-size: 9pt; }}
  .mono {{ font-family: Consolas, 'Courier New', monospace; font-size: 11pt; font-weight: 700; word-break: break-all; }}
  ol {{ font-size: 9.5pt; line-height: 1.55; padding-left: 5mm; margin: 0 0 3mm; }}
  .note {{ font-size: 8.5pt; color: #475569; margin: 0; }}
  @page {{ size: A4 portrait; margin: 0; }}
</style></head><body>
{sheets}
</body></html>
"""


def sheets_html(cards: list) -> str:
    pages = [cards[i:i + PER_PAGE] for i in range(0, len(cards), PER_PAGE)]
    return "\n".join('<div class="sheet">\n' + "\n".join(p) + "\n</div>" for p in pages)


def db_branches(db: str) -> list:
    """[(branch_id, name)] of the active branches of a database COPY, opened read-only."""
    if not Path(db).exists():
        sys.exit(f"ไม่พบไฟล์ฐานข้อมูล: {db}")
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(branches)")}
        where = " AND COALESCE(active, 1) = 1" if "active" in cols else ""
        return [(r[0], r[1]) for r in con.execute(f"SELECT branch_id, name FROM branches WHERE name <> 'ALL'{where} ORDER BY name")]
    finally:
        con.close()


def build_entries(a) -> list:
    """-> [{branch, username, recipient}] for the chosen source; exits with a plain Thai message when a login name cannot be known."""
    role = getattr(a, "role", "branch")
    ids = {n: b for b, n in db_branches(a.db)} if a.db else {}
    if role == "franchise" and not (a.all or a.list):
        sys.exit("--role franchise ต้องระบุ --list (ไฟล์รายชื่อสาขาแฟรนไชส์ หรือ owner_accounts_result.csv) หรือ --all กับ --db")
    if a.all:
        if not a.db:
            sys.exit("--all ต้องระบุ --db (ไฟล์ฐานข้อมูลสำเนา) เพื่ออ่านรายชื่อสาขา")
        rows = [{"branch": n, "username": "", "recipient": "", "result": ""} for n in ids]
    elif a.list:
        rows = BL.read_list(a.list)
    else:
        rows = [{"branch": n, "username": u, "recipient": "", "result": ""} for _, n, u in BRANCHES]
    out, skipped, missing = [], [], []
    for r in rows:
        if r.get("result") == BL.R_FAILED:
            skipped.append(f"{r['branch']} (สร้างบัญชีไม่สำเร็จ)")
            continue
        if r.get("result") == BL.R_EXISTS and not a.include_existing:
            skipped.append(f"{r['branch']} (มีบัญชีอยู่แล้ว อาจเปลี่ยนรหัสผ่านไปแล้ว ใช้ --include-existing หากต้องการสร้างข้อความ)")
            continue
        user = BL.login_of(r, role) if role == "franchise" else r["username"]
        user = user or BL.username_for(r["branch"], ids.get(r["branch"]), r.get("abbr", ""), role)
        if not user:
            missing.append(r["branch"])
            continue
        out.append({"branch": r["branch"], "username": user, "recipient": r.get("recipient", "")})
    if missing:
        sys.exit("ไม่ทราบชื่อผู้ใช้ของสาขา: " + ", ".join(missing) +
                 "\nกรอกคอลัมน์ ตัวย่อ ในไฟล์รายชื่อ หรือระบุ --db (ไฟล์ฐานข้อมูลสำเนา) เพื่อใช้ wh_<รหัสสาขา> หรือใช้ accounts_result.csv ที่ branch_accounts.py สร้างให้")
    if a.only:
        key = a.only.strip().lower()
        out = [e for e in out if key in (e["username"].lower(), e["branch"].lower(),
                                         e["username"].lower().replace("wh_", "").replace("own_", "").replace("branch_", ""))]
        if not out:
            sys.exit(f"ไม่พบสาขาที่ตรงกับ --only {a.only}")
    for s in skipped:
        print(f"  ข้าม: {s}")
    return out


def main(argv=None) -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(Path.home() / "Oshinei_branch_handout"))
    ap.add_argument("--list", help="รายชื่อสาขา (.xlsx หรือ .csv) คอลัมน์ สาขา · ตัวย่อ · ชื่อผู้ใช้ · LINE/ผู้รับ")
    ap.add_argument("--all", action="store_true", help="ทุกสาขาที่เปิดใช้งานในฐานข้อมูลสำเนาที่ระบุด้วย --db")
    ap.add_argument("--db", help="ไฟล์ฐานข้อมูลสำเนา (อ่านอย่างเดียว) ใช้หา wh_<รหัสสาขา> เมื่อไม่มีตัวย่อ")
    ap.add_argument("--only", help="เฉพาะสาขาเดียว (ชื่อผู้ใช้ ชื่อสาขา หรือตัวย่อ เช่น udn)")
    ap.add_argument("--include-existing", action="store_true", help="สร้างข้อความให้บัญชีที่มีอยู่แล้วด้วย")
    ap.add_argument("--role", choices=("branch", "franchise"), default="branch",
                    help="branch = ข้อความและบัตรของทีมสาขา (ค่าเริ่มต้น) · franchise = ข้อความและบัตรของเจ้าของสาขา (own_<ตัวย่อ>, หน้า ร้านของฉัน)")
    ap.add_argument("--template", action="store_true", help="เขียนไฟล์ตัวอย่าง branch_list_template.xlsx (สาขานำร่อง 5 แถว) แล้วจบ")
    ap.add_argument("--passwords", help="temp_passwords.csv (username,role,branch,password) จาก branch_accounts.py หรือ rotate_default_passwords.py")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.template:
        p = BL.write_template(out / "branch_list_template.xlsx")
        print(f"wrote {p}")
        return
    if a.list and a.all:
        sys.exit("เลือกอย่างใดอย่างหนึ่ง: --list หรือ --all")
    todo = build_entries(a)
    pws = read_passwords(a.passwords)
    for e in [e for e in todo if e["username"] not in pws]:
        print(f"  ข้าม: {e['branch']} ({e['username']} ไม่มีรหัสผ่านชั่วคราวในไฟล์ {a.passwords})")
    todo = [e for e in todo if e["username"] in pws]
    if not todo:
        sys.exit("ไม่มีสาขาให้สร้างข้อความ")
    owner = a.role == "franchise"
    make_msg, make_card = (owner_message, owner_card) if owner else (message, card)
    for e in todo:
        (out / f"{e['username']}.txt").write_text(make_msg(e["branch"], e["username"], pws[e["username"]]), encoding="utf-8")
    (out / "cards.html").write_text(PAGE.format(sheets=sheets_html([make_card(e["branch"], e["username"], pws[e["username"]]) for e in todo])), encoding="utf-8")
    pages = -(-len(todo) // PER_PAGE)
    lines = [f"สร้างเมื่อ {date.today().isoformat()} · {len(todo)} สาขา",
             "- <ชื่อผู้ใช้>.txt = ข้อความพร้อมส่ง (LINE/อีเมล) ส่งให้เฉพาะสาขานั้น",
             f"- cards.html = บัตรพิมพ์ A4 แนวตั้ง หน้าละ {PER_PAGE} ใบ ({pages} หน้า) เปิดในเบราว์เซอร์แล้วสั่งพิมพ์ ตัดตามเส้นประ",
             "- โฟลเดอร์นี้มีรหัสผ่านชั่วคราวของแต่ละบัญชี ส่งให้เฉพาะสาขานั้น ลบหลังแจกเสร็จ ห้ามอัปโหลดขึ้น GitHub",
             ("- บัญชีเจ้าของสาขาดูผลของสาขาตนเองได้อย่างเดียว ไม่มีการบันทึกข้อมูล จึงไม่ต้องปลดล็อกเครื่อง" if owner else
              "- สาขาไม่ต้องปลดล็อกเครื่อง: บัญชีสาขาบันทึกได้ทันที (ตั้งแต่ 8 ต.ค. 2569)"),
             "", "ไฟล์ · สาขา · ส่งถึง"]
    lines += [f"{e['username']}.txt · {e['branch']} · {e['recipient'] or '-'}" for e in todo]
    (out / "README.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(todo)} handouts → {out}")
    for e in todo:
        print(f"  {e['branch']}: {e['username']}.txt" + (f"  → {e['recipient']}" if e["recipient"] else ""))
    print(f"  cards.html (print, {pages} page{'s' if pages != 1 else ''}), README.txt")


if __name__ == "__main__":
    main()
