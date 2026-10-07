# เปิดใช้ล็อกอินของระบบกระทบยอด (recon) — ขั้นตอนสำหรับเจ้าของ

> Owner decision 2026-10-06: recon มีบัญชีผู้ใช้ สิทธิ์ และ session **ของตัวเอง** แยกจากแดชบอร์ดต้นทุน
> โค้ดอยู่ใน `recon/auth.py`, `recon/routers/auth.py`, `recon/frontend/js/05-login.js`, `85-users.js`
> คู่มือผู้ใช้: `recon/frontend/help.html` §9.7 · UAT: TC-59 – TC-63

**ปลอดภัยตอน deploy:** ถ้ายังไม่ได้ตั้ง `RECON_LOGIN=1` ระบบทำงาน **เหมือนเดิมทุกอย่าง**
(คุกกี้แดชบอร์ด + `RECON_ENFORCE_AUTH` + ปุ่ม รหัส / write token + รหัสผู้จัดการ) — deploy โค้ดนี้แล้วไม่มีใครถูกล็อกออก

## ตัวแปรที่เกี่ยวข้อง (Fly secrets)

| ตัวแปร | ใช้ทำอะไร | หมายเหตุ |
|---|---|---|
| `RECON_SESSION_SECRET` | กุญแจเซ็นคุกกี้ `recon_session` (HMAC-SHA256) | ยาว ≥ 16 ตัว (แนะนำ 48+ แบบสุ่ม) · ถ้า `RECON_LOGIN=1` แต่ไม่มีค่านี้ → ทุก `/api` ตอบ **503** (ปิดตาย ไม่เปิดโล่ง) · เปลี่ยนค่า = ทุกคนต้องเข้าสู่ระบบใหม่ |
| `RECON_ADMIN_USER` / `RECON_ADMIN_PASSWORD` | สร้างผู้จัดการคนแรก ตอน start ถ้าตาราง `recon_users` **ว่าง** | รหัส ≥ 8 ตัว · ต้องเปลี่ยนรหัสตอนเข้าครั้งแรก · มีผู้ใช้แล้วค่านี้ถูกเมิน |
| `RECON_LOGIN` | `1` = บังคับเข้าสู่ระบบ + สิทธิ์ | ไม่ตั้ง = แบบเดิม · ลบออก = ย้อนกลับแบบเดิมทันที |

**กฎโปรเจกต์: ตั้ง secret บน Fly ครั้งใด ต้องสำรองค่าลงเครื่องก่อน/พร้อมกันเสมอ** (Fly อ่านค่ากลับไม่ได้)
→ เก็บใน `backend/.recon_login_secrets.txt` (อยู่ใน `.gitignore` แล้ว — ห้าม commit) บรรทัดละ `ชื่อ=ค่า`

## ขั้นตอนเปิดใช้

1. **รวมโค้ดและ deploy ตามปกติ** (ยังไม่ตั้ง `RECON_LOGIN`) — `bash scripts/deploy-clean.sh <ref>` ตามกฎ deploy
   ตรวจ: `curl -s https://oshinei-dashboard.fly.dev:8443/api/me` → `"login_enabled":false` และหน้าเว็บเหมือนเดิม

2. **สร้างกุญแจและรหัสผู้จัดการคนแรก** บนเครื่อง
   ```bash
   py -3.12 -c "import secrets; print(secrets.token_urlsafe(48))"      # → RECON_SESSION_SECRET
   ```
   เลือกชื่อผู้ใช้ผู้จัดการคนแรก (ภาษาอังกฤษ เช่น `owner`) + รหัสชั่วคราว ≥ 8 ตัว

3. **สำรองค่าก่อน** ลง `backend/.recon_login_secrets.txt`
   ```
   RECON_SESSION_SECRET=...
   RECON_ADMIN_USER=owner
   RECON_ADMIN_PASSWORD=...
   ```

4. **ตั้ง secrets บน Fly** (ยังไม่ใส่ `RECON_LOGIN`) — คำสั่งนี้ restart เครื่อง ใช้เวลาไม่กี่วินาที
   ```bash
   flyctl secrets set -a oshinei-dashboard RECON_SESSION_SECRET='...' RECON_ADMIN_USER='owner' RECON_ADMIN_PASSWORD='...'
   ```
   ตรวจ: `/api/me` → `"login_enabled":false,"login_ready":true`

5. **เข้าสู่ระบบครั้งแรก (ยังไม่บังคับ)** — เปิด `https://oshinei-dashboard.fly.dev:8443/app/#setup` → ส่วน **⑥ ผู้ใช้งานระบบ** → **เข้าสู่ระบบ**
   ด้วย `owner` + รหัสชั่วคราว → ระบบให้ตั้งรหัสผ่านใหม่ของตัวเอง (จดไว้ในที่ปลอดภัย)

6. **สร้างบัญชีทุกคน** ใน ⑥ ผู้ใช้งานระบบ → **เพิ่มผู้ใช้**
   - พนักงานบัญชี → สิทธิ์ **บัญชี** · ผู้บริหาร/ผู้สอบบัญชี → **ดูอย่างเดียว**
   - **ควรมีผู้จัดการอย่างน้อย 2 คน** — แบ่งแยกหน้าที่ (SoD): คนที่สร้างงวดอนุมัติงวดนั้นเองไม่ได้ และกันกรณีผู้จัดการคนเดียวลืมรหัส
   - แจ้งรหัสชั่วคราวให้แต่ละคนทางช่องทางส่วนตัว (ทุกคนต้องตั้งรหัสเองตอนเข้าครั้งแรก)

7. **เปิดบังคับ** (สำรองค่า `RECON_LOGIN=1` ลงไฟล์ในข้อ 3 ด้วย)
   ```bash
   flyctl secrets set -a oshinei-dashboard RECON_LOGIN=1
   ```

8. **ตรวจหลังเปิด**
   ```bash
   curl -s https://oshinei-dashboard.fly.dev:8443/api/me              # "login_enabled":true,"login_ready":true
   curl -s -o /dev/null -w "%{http_code}\n" https://oshinei-dashboard.fly.dev:8443/api/branches   # 401
   curl -s -o /dev/null -w "%{http_code}\n" https://oshinei-dashboard.fly.dev:8443/health          # 200
   ```
   แล้วเปิด `/app/` → ขึ้นหน้าเข้าสู่ระบบ · เข้าเป็นผู้จัดการ / บัญชี / ดูอย่างเดียว อย่างละครั้ง (UAT TC-59 – TC-63)
   และลองสคริปต์ bulk upload 1 ไฟล์ (ข้อถัดไป)

9. **สคริปต์ bulk upload** (`bulk_recon_upload.py`) — ยังใช้ write token (`--token` / `OSHINEI_WRITE_TOKEN`) ได้เหมือนเดิม
   เมื่อ `RECON_LOGIN=1` write token อย่างเดียวพอ: ใช้ `--no-login` ได้ (ไม่ต้องล็อกอินแดชบอร์ด) · write token ใช้อนุมัติ/ปิดเดือน/จัดการผู้ใช้ไม่ได้

10. **หลังทุกคนใช้งานได้ (แนะนำ)**
    - **เปลี่ยน `OSHINEI_SEED_TOKEN`** เป็นค่าใหม่ที่มีแค่สคริปต์ bulk upload ถือ — ค่าเดิมอยู่ในเบราว์เซอร์ของพนักงาน (ปุ่ม รหัส) และใครถือ write token ยังเขียนข้อมูลได้โดยไม่ต้องเข้าสู่ระบบ — ขั้นตอนเต็ม + สคริปต์ที่สำรองค่า/ตั้งบน Fly/ทดสอบให้เอง: **`docs/TOKEN_ROTATION.md`** (`scripts/rotate_seed_token.py`)
      **ระวัง:** แดชบอร์ดต้นทุนใช้ `OSHINEI_SEED_TOKEN` ตัวเดียวกัน — ตรวจว่าการอัปโหลด/งานอัตโนมัติฝั่งแดชบอร์ดที่ใช้ token เดิมได้ค่าใหม่ด้วย
    - ลบ `RECON_ADMIN_PASSWORD` ออกจาก Fly: `flyctl secrets unset -a oshinei-dashboard RECON_ADMIN_PASSWORD RECON_ADMIN_USER`
      (ค่านี้ใช้เฉพาะตอนตารางผู้ใช้ว่าง — ถ้าวันหนึ่งกู้ฐานข้อมูลที่ไม่มีผู้ใช้ ค่าเก่าที่ค้างไว้จะสร้างบัญชีผู้จัดการด้วยรหัสที่หลายคนอาจเคยเห็น)
    - `OSHINEI_MANAGER_TOKEN` ไม่ถูกใช้ขณะ `RECON_LOGIN=1` (ระบบดูจากสิทธิ์ผู้จัดการ) — เก็บไว้สำหรับกรณีย้อนกลับ

## ย้อนกลับ (rollback)

```bash
flyctl secrets unset -a oshinei-dashboard RECON_LOGIN
```
เครื่อง restart แล้วระบบกลับเป็นแบบเดิมทันที (คุกกี้แดชบอร์ด + `RECON_ENFORCE_AUTH` ถ้ายังตั้งอยู่ + ปุ่ม รหัส + รหัสผู้จัดการ)
บัญชีผู้ใช้ที่สร้างไว้ยังอยู่ครบ — ตั้ง `RECON_LOGIN=1` ใหม่เมื่อพร้อม ไม่ต้องสร้างบัญชีซ้ำ
(ถ้าเปลี่ยน `OSHINEI_SEED_TOKEN` ไปแล้ว ต้องแจ้งค่าใหม่ให้พนักงานใส่ปุ่ม รหัส ระหว่างย้อนกลับ)

## กู้บัญชีเมื่อเข้าไม่ได้

| อาการ | ทางแก้ |
|---|---|
| พนักงานลืมรหัส / ถูกล็อก 15 นาที | ผู้จัดการ → ⑥ ผู้ใช้งานระบบ → **ตั้งรหัสใหม่** (ปลดล็อกทันที) |
| ผู้จัดการทุกคนเข้าไม่ได้ | `flyctl ssh console -a oshinei-dashboard` แล้ว `cd /app && python -m recon.create_user <ชื่อ> --reset` (หรือสร้างผู้จัดการใหม่: `python -m recon.create_user <ชื่อใหม่> --role manager --name "ชื่อ"`) — ระบบถามรหัสผ่านด้วย getpass ไม่แสดงบนจอ |
| ด่วนมาก / ssh ไม่ได้ | ย้อนกลับ: `flyctl secrets unset -a oshinei-dashboard RECON_LOGIN` แล้วค่อยกู้บัญชี |
| ทุก `/api` ตอบ 503 "ระบบเข้าสู่ระบบยังไม่พร้อม" | `RECON_SESSION_SECRET` หายหรือสั้นเกิน — ตั้งใหม่จากไฟล์สำรอง (ทุกคนต้องเข้าสู่ระบบใหม่) |

## เรื่องที่ควรรู้

- **ข้อมูลผู้ใช้อยู่ในตาราง `recon_users`** ของ `/data/oshinei.db` — ขึ้นต้น `recon_` จึงถูกพาไปด้วยทุกครั้งที่ sync ข้อมูลยอดขายสลับไฟล์ฐานข้อมูล
  (`backend/core.py` `_PRESERVED_PREFIXES` คัดลอกทุกตาราง `recon_*` แล้วตรวจจำนวนแถว — ถ้าหายจะไม่สลับไฟล์) · ทดสอบไว้ใน `recon/tests/test_recon_login.py`
  และอยู่ใน backup รายคืนของทั้งไฟล์
- รหัสผ่านเก็บเป็น scrypt + salt รายคน (ไม่มีใครอ่านรหัสจริงได้ รวมถึงผู้ดูแล) · คุกกี้อายุ 12 ชม. (HttpOnly, SameSite=Lax, Secure บน https)
- สิทธิ์และสถานะบัญชีอ่านจากฐานข้อมูลทุกคำขอ: เปลี่ยนสิทธิ์/ปิดบัญชีมีผลทันที · ตั้งรหัสใหม่/เปลี่ยนรหัส = ออกจากระบบทุกเครื่อง
- ประวัติ: การเข้า/ออก/เข้าไม่สำเร็จ = `recon_audit_log` entity `recon_auth` · การจัดการผู้ใช้ = `recon_user` (ไม่ปนกับ `auth` ของแดชบอร์ด)
- ผู้ทำรายการ (audit "by", ผู้จัดทำ/ผู้อนุมัติ) = **ชื่อผู้ใช้** ของระบบกระทบยอด (ไม่ใช่ชื่อที่แสดง ซึ่งแก้ได้) — SoD เทียบชื่อผู้ใช้จริง
- คุกกี้แดชบอร์ดไม่มีผลกับ recon เมื่อ `RECON_LOGIN=1` (คนละระบบ คนละรายชื่อ)
