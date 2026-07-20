# ย้ายโฟลเดอร์ทำงานออกจาก OneDrive → ใช้ GitHub เป็นตัว sync
เขียน: 2026-07-20 · สถานะ: **รอเจ้าของสั่ง cutover**

## ข้อเท็จจริงก่อน: โค้ด "อยู่บน GitHub แล้ว"
- Repo: `github.com/wayla69/oshineicost` — ทุก commit ถูก push ขึ้นไปตลอด (ล่าสุด `eecd402` deploy วันนี้)
- สิ่งที่เป็นปัญหาไม่ใช่ "ยังไม่ขึ้น GitHub" แต่คือ **โฟลเดอร์ทำงาน (working copy + .git) อยู่ใน OneDrive** ทำให้:
  - ไฟล์ถูก sync ทับกันเมื่อหลาย session/เครื่องแก้พร้อมกัน (เกิดจริงวันนี้กับ main.py 2 รอบ)
  - `.git` โดน OneDrive แตะ → worktree corruption (เคยพัง 18 commits เมื่อ 2026-07-10), HEAD/ref ขยับเอง
  - DB ~1GB ถูก re-upload ทุกครั้งที่เขียน
- **การแก้ = ย้าย working copy ไปนอก OneDrive** แล้วให้ GitHub เป็นช่องทาง sync ระหว่างเครื่อง/…ตามที่เป็นอยู่แล้วในทางปฏิบัติ

## แผน cutover (ครั้งเดียว ~30 นาที)

**เงื่อนไขก่อนเริ่ม (สำคัญที่สุด):** ปิด Claude session อื่นทุกตัวที่ทำงานในโฟลเดอร์นี้ + push งานค้างของทุกเครื่องขึ้น origin ให้หมด

1. ตรวจ `git status` สะอาด + `git push` ครบ
2. `git clone https://github.com/wayla69/oshineicost.git C:\Users\ASUS\Oshinei`  (นอก OneDrive)
3. ปิด server ทุกตัว แล้วคัดลอกไฟล์ที่ git ไม่เก็บ:
   - `backend/oshinei.db` (หลัง checkpoint — ไม่ copy ตอน -wal มีข้อมูลค้าง)
   - `backend/usage.db`, `backend/.seed_tokens.txt`, `backend/.manager_token.txt`, `.pdf_passwords.txt` (ถ้ามี)
4. **ไฟล์ข้อมูลดิบ** ("Data for Oshinei/", "BoM/", "All recon data/", xlsx ต่าง ๆ): **แนะนำปล่อยไว้ใน OneDrive** เป็นคลังเอกสาร (ได้ cloud backup ฟรี — ตอบโจทย์ AUD-plan เรื่อง off-site backup ด้วย) — เวลา ingest ค่อยชี้ path ไปที่ OneDrive
5. อัปเดต **Windows Task Scheduler**: งาน "Sync Live (Fly)" daily 10:00 + `Refresh Online Data.bat` ให้ชี้ path ใหม่
6. Deploy worktree: ลบ `~/oshinei_deploy_clean` เดิม (มันชี้ .git เก่า) — `deploy-clean.sh` สร้างใหม่เองจาก repo ใหม่
7. ย้าย Claude Code memory: copy `~/.claude/projects/<key เดิม>/memory/` → `<key ใหม่>` (path เปลี่ยน = project key เปลี่ยน)
8. เปลี่ยนชื่อโฟลเดอร์เดิมใน OneDrive เป็น `_ARCHIVED_Cost Analysis` **อย่าเพิ่งลบ** (กันเผลอเข้าไปแก้ + เผื่อไฟล์ตกหล่น เก็บไว้ 1–2 สัปดาห์)
9. เครื่องอื่น (ถ้ามี): ทำแบบเดียวกัน — clone จาก GitHub + copy DB ของเครื่องตัวเอง จากนั้น **sync โค้ดผ่าน git pull/push เท่านั้น**

## ข้อแลกเปลี่ยนที่ต้องรู้
- โค้ด: ปลอดภัยขึ้นมาก (git เป็น source of truth เดียว ไม่มีตัวกลางมาเขียนทับ)
- ข้อมูล: DB ในเครื่องจะไม่มี cloud backup อัตโนมัติอีก → production จริงอยู่บน Fly volume + ไฟล์ดิบยังอยู่ OneDrive (ข้อ 4) + ควรตั้ง backup ประจำตาม AUDIT_READINESS_PLAN (AUD-item เรื่อง off-site backup + restore test)
- กฎใหม่หลังย้าย: **ห้าม** เอาโฟลเดอร์ repo กลับเข้า OneDrive/Google Drive/Dropbox อีก
