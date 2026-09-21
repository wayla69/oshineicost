# ย้ายโฟลเดอร์ทำงานออกจาก OneDrive → ใช้ GitHub เป็นตัว sync
เขียน: 2026-07-20 · ปรับปรุง: 2026-09-21 · สถานะ: **เสร็จสมบูรณ์ 2026-09-21** — cutover 2026-07-20, โฟลเดอร์เดิมเปลี่ยนชื่อเป็น `_ARCHIVED_Cost Analysis` แล้ว (ข้อ 8)

> ⚠️ **บทเรียนจากของจริง (2026-07-27):** ข้อ 3 ของแผนเดิม **ลืม `backend/.env`** → MSSQL sync
> ล้มเหลวเงียบ ๆ **7 วันติด** (scheduled task ทั้งสองตัว exit 1 ทุกวัน โดยไม่มีใครรู้) กว่าจะจับได้
> รายการไฟล์ด้านล่างแก้ให้ครบแล้ว — **ทำ checklist ข้อ 3 กับข้อ 3b ให้ครบทุกช่องก่อนถือว่าย้ายเสร็จ**

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
3. ปิด server ทุกตัว แล้วคัดลอกไฟล์ที่ git ไม่เก็บ

   **กฎเหล็ก: `git clone` ไม่พาไฟล์ที่อยู่ใน `.gitignore` มาด้วย** — ซึ่งก็คือ *ความลับทั้งหมด*
   ของระบบ ไฟล์พวกนี้จึงมีอยู่ที่เดียวคือเครื่องเดิม ถ้าลืมข้อไหน ระบบจะพังแบบ **เงียบ**
   (task ล้มเหลวทุกวันโดยไม่มีใครเห็น เพราะไม่มีใครเปิดดู log)

   **ความลับ / credentials — ต้องครบทุกไฟล์:**
   - [ ] `backend/.env` — **รหัส MSSQL** (`OSH_SVR/OSH_PORT/OSH_USER/OSH_PWD/OSH_DB`) ← ตัวที่เคยลืม ทำให้ sync ตาย 7 วัน
   - [ ] `backend/.refresh_login.txt` — login ที่ `refresh_fly_db.py` ใช้ยืนยันผลตอน push ขึ้น Fly (ถ้าไม่มี step 5/5 จะ time out)
   - [ ] `backend/.seed_tokens.txt` — token ของ `/api/_admin/seed-db`
   - [ ] `backend/.manager_token.txt`
   - [ ] `recon/.pdf_passwords.txt` (ถ้ามี) — รหัสเปิด PDF statement ของ KTB

   **ข้อมูล:**
   - [ ] `backend/oshinei.db` (หลัง checkpoint — ไม่ copy ตอน -wal มีข้อมูลค้าง)
   - [ ] `backend/usage.db`

3b. **ตรวจว่าครบจริง** (รันที่โฟลเดอร์ใหม่ — ต้องขึ้น OK ทุกบรรทัด):
   ```powershell
   foreach ($f in '.env','.refresh_login.txt','.seed_tokens.txt','.manager_token.txt') {
     $p = "backend\$f"
     if (Test-Path $p) { "OK      $f" } else { "MISSING $f" }
   }
   ```
   แล้วทดสอบว่าต่อ MSSQL ได้จริง (ไม่ใช่แค่ไฟล์มีอยู่):
   ```bash
   cd backend && python -c "import sync_mssql as s; c=s.connect_mssql(); print('MSSQL OK'); c.close()"
   ```
4. **ไฟล์ข้อมูลดิบ** ("Data for Oshinei/", "BoM/", "All recon data/", xlsx ต่าง ๆ): **แนะนำปล่อยไว้ใน OneDrive** เป็นคลังเอกสาร (ได้ cloud backup ฟรี — ตอบโจทย์ AUD-plan เรื่อง off-site backup ด้วย) — เวลา ingest ค่อยชี้ path ไปที่ OneDrive
5. อัปเดต **Windows Task Scheduler**: งาน "Sync Live (Fly)" daily 10:00 + `Refresh Online Data.bat` ให้ชี้ path ใหม่

   **วันรุ่งขึ้นต้องกลับมาเช็คผลรันจริง** — งานพวกนี้ล้มเหลวแบบไม่มีเสียง ไม่มีใครแจ้งเตือน:
   ```powershell
   Get-ScheduledTask | Where-Object { $_.TaskName -like '*Oshinei*' } |
     ForEach-Object { $i = $_ | Get-ScheduledTaskInfo
       [PSCustomObject]@{ Name=$_.TaskName; Last=$i.LastRunTime; Result=$i.LastTaskResult } }
   ```
   อ่านค่า `Result`: **`0` = สำเร็จ** · **`1` = .bat เข้าสาย "SYNC FAILED"** (คือ `sync_mssql.py` ตาย —
   เช็ค `backend/.env` ก่อนอย่างอื่นเสมอ) · `267014` = task ถูกสั่งหยุด
   นี่คือวิธี**เร็วที่สุด**ในการวินิจฉัย sync ที่ไม่ทำงาน — เร็วกว่าไปไล่หาปัญหา network/SQL มาก
   **ตั้งแต่ 2026-09-21 ไม่ต้องเช็คมือแล้ว:** task `Oshinei Task Watchdog` (11:15 / 17:00 / ตอน logon) อ่านค่าพวกนี้ทุกวัน
   แล้วแจ้งที่หน้าแรกของแอป (admin) + Telegram + toast บนเครื่อง — `backend/task_watchdog.py`
   เครื่องใหม่/ย้ายโฟลเดอร์: `powershell -ExecutionPolicy Bypass -File scriptsegister_task_watchdog.ps1`
6. Deploy worktree: ลบ `~/oshinei_deploy_clean` เดิม (มันชี้ .git เก่า) — `deploy-clean.sh` สร้างใหม่เองจาก repo ใหม่
7. ย้าย Claude Code memory: copy `~/.claude/projects/<key เดิม>/memory/` → `<key ใหม่>` (path เปลี่ยน = project key เปลี่ยน)
8. เปลี่ยนชื่อโฟลเดอร์เดิมใน OneDrive เป็น `_ARCHIVED_Cost Analysis` **อย่าเพิ่งลบ** (กันเผลอเข้าไปแก้ + เผื่อไฟล์ตกหล่น เก็บไว้ 1–2 สัปดาห์)
   - **ห้ามลบจนกว่าข้อ 3b จะผ่านครบทุกช่อง** — โฟลเดอร์เดิมคือ *แหล่งเดียว* ของไฟล์ความลับ
     ถ้าลบไปก่อนแล้วพบว่าลืม `.env` จะกู้ไม่ได้เลย (ไม่ได้อยู่บน git ตามเจตนา)
   - หลังยืนยันครบแล้ว ควรสำรอง credentials ไว้อีกที่ (password manager / ที่เก็บ secret) —
     ตอนนี้มันอยู่บนดิสก์ลูกเดียวและไม่มี backup
   - **ทำแล้ว 2026-09-21.** ที่เปลี่ยนชื่อไม่ได้อยู่ 2 เดือน ("being used by another process") ไม่ใช่ OneDrive —
     คือ **Claude session เก่าที่เคยเปิดในโฟลเดอร์นั้น**: แอปเปิด session ล่าสุดกลับขึ้นมาเองตอนเริ่มโปรแกรม
     process `claude.exe` (+ MCP ลูก ๆ + terminal) จึงมี current directory อยู่ในโฟลเดอร์และล็อกไว้ แม้ session จะนิ่งอยู่
     command line ของ process ไม่มี path ให้เห็น ต้องอ่าน cwd ของ process ตรง ๆ ถึงจะเจอ
     ปิด process ตัวนั้นแล้วเปลี่ยนชื่อได้ทันที ข้อมูลดิบ (`All recon data/`, `Data for Oshinei/`, `BoM/` ต้นฉบับ,
     `Reconciliation Data Udon/`) ยังอยู่ในโฟลเดอร์ archive ตามข้อ 4 — `recon_paths.py` รู้จักทั้งสองชื่อ
   - session เก่าที่เปิดไว้ในโฟลเดอร์เดิมจะ resume ที่เดิมไม่ได้อีก (ตั้งใจ) — งานใหม่เปิดที่ `C:\Users\ASUS\Oshinei` เท่านั้น
9. เครื่องอื่น (ถ้ามี): ทำแบบเดียวกัน — clone จาก GitHub + copy DB ของเครื่องตัวเอง จากนั้น **sync โค้ดผ่าน git pull/push เท่านั้น**

## ข้อแลกเปลี่ยนที่ต้องรู้
- โค้ด: ปลอดภัยขึ้นมาก (git เป็น source of truth เดียว ไม่มีตัวกลางมาเขียนทับ)
- ข้อมูล: DB ในเครื่องจะไม่มี cloud backup อัตโนมัติอีก → production จริงอยู่บน Fly volume + ไฟล์ดิบยังอยู่ OneDrive (ข้อ 4) + ควรตั้ง backup ประจำตาม AUDIT_READINESS_PLAN (AUD-item เรื่อง off-site backup + restore test)
- กฎใหม่หลังย้าย: **ห้าม** เอาโฟลเดอร์ repo กลับเข้า OneDrive/Google Drive/Dropbox อีก
