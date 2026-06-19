# แผนรวมแอป + ออกแบบ UI/UX ใหม่ (Recon App Consolidation) — คำสั่งงานสำหรับ Sonnet Agent

> **เป้าหมายของเจ้าของงาน:** เลิกมี "เครื่องมือขั้นสูง" (หน้า `classic.html`) แยกต่างหาก —
> รวมทุกอย่างให้เป็น **แอปเดียว** แล้วจัด **UI/UX ใหม่ให้เหมาะสม**
>
> **เอกสารนี้คืออะไร:** แผนงานละเอียดแบบแบ่งเฟส + เกณฑ์ตรวจรับ + **ข้อห้ามเด็ดขาด** +
> **วิธีกู้คืนเมื่อพลาด** เพื่อให้ Sonnet agent ทำงานทีละเฟสได้อย่างปลอดภัย โดย**ไม่ทำของพังหรือตัวเลขบัญชีเพี้ยน**
>
> **กฎเหล็กข้อแรก:** ห้ามลบ `classic.html` จนกว่าจะถึง **เฟส 6** และผ่านการตรวจ parity ครบแล้ว —
> ระหว่างทาง `classic.html` คือ "ตัวเทียบมาตรฐาน (oracle)" สำหรับตรวจว่าหน้าใหม่ให้ตัวเลขตรงกัน

---

## 0. บริบทระบบ (อ่านก่อนเริ่ม)

แอป recon (`recon/`) เป็น FastAPI + SQLite + vanilla HTML/JS เสิร์ฟ 3 หน้า frontend + เอกสาร:

| ไฟล์ | บทบาท | บรรทัด |
|---|---|---|
| `recon/frontend/index.html` | **หน้าใหม่ "Inbox"** (hash-routed) — แอปหลักปัจจุบัน | ~1,870 |
| `recon/frontend/classic.html` | **"เครื่องมือขั้นสูง" หน้าเดิม** (sidebar tabs, Tailwind CDN) — จะถูกลบ | ~3,683 |
| `recon/frontend/setup.html` | **ตั้งค่าระบบ** (manager-gated config) — หน้าแยก | ~417 |
| `recon/frontend/help.html` | คู่มือผู้ใช้ | ~716 |
| `recon/main.py` | FastAPI app + ทุก endpoint + เอกสาร UAT (`_UAT_CASES`) | ใหญ่ |
| `recon/matcher.py` | ตรรกะ matching/recon + helper บัญชี | — |
| `recon/reconcile_v2.py` | reconciliation v2 | — |

**สถาปัตยกรรมสำคัญ:** frontend ทุกหน้าเป็น "view-only ของ backend API" — ตรรกะธุรกิจ/บัญชี/recon อยู่ที่ backend ทั้งหมด
งานนี้ส่วนใหญ่คือ **frontend** (ย้าย/รวม view + ออกแบบใหม่) — **ตามค่าเริ่มต้นห้ามแก้ backend** (ดูข้อห้าม §6)

---

## 1. สถานะปัจจุบัน — ช่องว่าง Feature Parity (classic.html vs index.html)

### A. มีครบทั้งสองหน้าแล้ว (ปลอดภัย ไม่ต้องย้าย)
| ฟีเจอร์ | endpoint | หมายเหตุ |
|---|---|---|
| ภาพรวมทุกสาขา (Inbox) | `/api/v2/overview` | index ใช้ v2 |
| Dashboard รายได้รายวัน | `/api/daily-dashboard` | `#dashboard` |
| สรุปบัญชีรายวัน (daybook) | `/api/daily-accounts` | `#daybook` |
| รายงานเงินผิดช่อง | `/api/reports/wrong-channel` | `#wrong` |
| รายงานข้อผิดพลาด | `/api/reports/branch-errors` | `#errors` |
| หน้าสาขา / กระทบยอด (uploads+discrepancies+reconcile รวมเป็นหน้าเดียว) | `/api/v2/...` | `b/{id}` = `loadBranch` |

### B. **CLASSIC-ONLY — จะหายถ้าลบ classic.html (ต้องย้ายก่อน)**
| ฟีเจอร์ | load fn (classic) | endpoint | ความซับซ้อน |
|---|---|---|---|
| **Coupon series drill** (วิเคราะห์คูปองรายซีรีส์) | `loadCoupon` ~2422 | `/api/coupon-series` | กลาง — index มีแค่ลิงก์ไป classic (`/app/classic.html#coupon`) |
| **Daily Board** (ตารางข้ามสาขาในวันเดียว) | `loadDailyBoard` ~1617 | `/api/daily-board`, `/api/daily-board/dates` | ต่ำ |
| **Trend heatmap** (ปฏิทินสีความคืบหน้ารายวัน) | `loadTrend` ~3147 | `/api/sessions/*/trend` | ต่ำ |
| **Audit log** (ประวัติการแก้ไข) | `loadAudit` ~3219 | `/api/audit` | ต่ำ (อ่านอย่างเดียว) |
| **Bank coverage** (สถานะธนาคารครบ/ขาดต่อ acquirer) | `loadBankCoverage` ~2076 | `/api/sessions/*/bank-coverage` | ต่ำ |
| **Setup/Payment-map admin เต็ม** | `loadSetup` ~3321 | `/api/payment-map`, `/api/bank-accounts`, `/api/acquirer-mids`, `/api/map-suggestions` | สูง — index มีแค่ setup-questions แบบ just-in-time |
| **Payout matching เต็ม** (KTC 2-ทาง ฯลฯ) | `openPayoutMatch` ~2548 | `/api/payout-matching` | กลาง — index มีแค่ `payoutTraceHtml` อ่านอย่างเดียว |
| **Discrepancies admin** (filter/bulk-resolve/assign/drill) **← เจ้าของยืนยัน "ต้องย้าย"** | `loadDiscrepancies` ~2915 | **ไม่มี** | `/api/sessions/{sid}/discrepancies`, `/bulk-resolve`, `/resolve`, `/assign`, `/days` | กลาง — index แทนด้วย day-ack รายวัน (`dayWorklistHtml`) ซึ่ง**ไม่เทียบเท่า** (ไม่มี bulk-resolve/assign/สถานะ accepted-adjusted-rejected) → ย้ายในเฟส 3b |

### C. ผลตรวจเฟส 0 (2026-06-19, ยืนยันแล้ว)
- **Fee/VAT editor: FULL PARITY** ✅ — `loadFees` (index ~1551) เท่ากับ `loadFeeVat` (classic) ทุกอย่าง → **ไม่ต้องย้าย**
- **Manual matching: PARTIAL** — `openMatcher` (index ~1711) ครอบ write path ครบ (POST/DELETE /api/manual) แต่เป็น inline ไม่ใช่ drawer + ไม่โชว์ยอด "consumed" + submit แล้ว reload ทั้งหน้า → **แค่ปรับ UX เล็กน้อย ไม่ต้อง port ใหม่**
- **Discrepancies admin: CLASSIC-ONLY** → ย้ายขึ้น §1B แล้ว (เดิมเข้าใจว่า "ต้องตรวจ" แต่จริง ๆ index ไม่มี)
- ฟีเจอร์ที่ **index มี แต่ classic ไม่มี** (ปลอดภัย): daybook (`#daybook`), wrong-channel (`#wrong`), day-ack รายวัน (`dayWorklistHtml`)

### ลิงก์ที่ index.html ชี้ไป classic (ต้องจัดการในเฟส 6)
1. `index.html` ~1350 (เมนู ⋯): `🧰 เครื่องมือขั้นสูง ↗ → /app/classic.html`
2. `index.html` ~1389 (ช่องคูปองในหน้าสาขา): `→ /app/classic.html#coupon`

> classic.html เป็นไฟล์ **standalone เต็มตัว** (Tailwind CDN + JS ของตัวเอง ไม่ share อะไรกับ index.html) เรียก backend API ชุดเดียวกันเท่านั้น

---

## 2. สถาปัตยกรรมเป้าหมาย (IA ของแอปเดียว)

ใช้ `index.html` เป็น **shell เดียว** ของทั้งแอป จัดเป็น 4 โซนตามบทบาทผู้ใช้:

```
แอปเดียว (index.html)
├── ① งานประจำวัน (staff)         — Inbox/ภาพรวม → หน้าสาขา(กระทบยอด) → daybook → dashboard
├── ② รายงาน (accounting/manager)  — Reports hub รวมเมนู ⋯ ที่กระจัดกระจาย:
│      monthly-report · outstanding-docs · wrong-channel · errors ·
│      coupon-series · statement-codes · UAT  (เปิดในแอป + ปุ่ม "พิมพ์/PDF")
├── ③ ตั้งค่า/Admin (manager-gated) — รวม setup.html เข้ามาเป็น route #setup:
│      payment-map · bank-accounts · acquirer-MIDs · statement-code-map ·
│      account-map · recon params · + Audit log · Bank coverage
└── ④ วินิจฉัย (diagnostics)        — Trend heatmap · Daily Board
```

**หลักการ navigation:** เพิ่มแถบนำทางเดียวที่สม่ำเสมอ (แนะนำ bottom-nav บนมือถือ / top-tabs บน desktop)
แทนเมนู ⋯ ที่เปิด tab ใหม่กระจัดกระจาย — แต่ **คงปุ่ม "พิมพ์/PDF" ทุกที่** (ฝ่ายบัญชี/ผู้ตรวจสอบต้องใช้กระดาษ)

**การตัดสินใจที่เจ้าของยืนยันแล้ว (2026-06-19) — ล็อกไว้ ห้ามเปลี่ยนเอง:**
- ✅ **Navigation = responsive: bottom-nav บนมือถือ + top-tab บนจอใหญ่** (CSS breakpoint เดียว สลับ)
- ✅ **setup รวมเข้าแอปเป็น route `#setup`** (in-app admin panel, manager-gated) — ไม่คงหน้าแยก
- ⏳ help.html: ยังไม่สรุป — ถามเจ้าของตอนถึงเฟส 5

---

## 3. แผนงานแบบแบ่งเฟส (แต่ละเฟส = ship ได้ + ตรวจได้ + ย้อนกลับได้)

> **ทุกเฟส:** 1 เฟส = 1 branch + 1 commit/PR · รันเทสต์เขียว · อัปเดต doc (help+UAT) · **ไม่ deploy เอง** (เจ้าของ deploy)
> · จบเฟสให้รายงานผล + รออนุมัติก่อนเฟสถัดไป

### เฟส 0 — Safety net + ตรวจ parity (ยังไม่ลบ/ไม่ย้ายอะไร)
- อ่าน `index.html` ส่วน `loadFees`, `openMatcher`, discrepancies inline ให้ครบ → สรุปว่ากลุ่ม C (Fee/VAT, Manual, Discrepancies) **ครบหรือไม่** เทียบ classic
- ทำตาราง parity ฉบับสมบูรณ์ (ทุกฟีเจอร์ classic → มีใน index ที่ไหน/ยังขาด)
- ตั้ง **canary ตัวเลข** สำหรับตรวจถดถอย: อุดรธานี (branch 25) 2026-05-01 → ส่วนลด **฿35,511.48**, เดือน **฿548,989.77**, และ Dr=Cr ทุกวันทุกสาขา
- **ส่งมอบ:** รายงาน parity + รายการฟีเจอร์ที่ต้องย้ายจริง (ยืนยัน/แก้รายการ §1B,§1C) — **ห้ามแก้โค้ดเฟสนี้**

### เฟส 1 — ย้ายฟีเจอร์ classic-only ที่ง่าย (read-only views)
ย้าย **Audit log, Trend heatmap, Bank coverage, Daily Board** เข้า index.html เป็น route ในแอป (เช่น `#audit`, `#trend`, `#bankcov`, `#board`)
- ใช้ endpoint เดิมทั้งหมด (ห้ามแตะ backend)
- จับคู่ดีไซน์กับ index.html (ใช้ CSS tokens ของ index — **ห้ามนำ Tailwind จาก classic เข้ามา**)
- **ตรวจรับ:** เปิดหน้าใหม่ได้ ตัวเลขตรงกับ classic หน้าเดียวกันบนข้อมูลเดียวกัน

### เฟส 2 — ย้าย Coupon series drill
- พอร์ต `loadCoupon`/drill (`/api/coupon-series`) เป็น view/drawer ในแอป
- แทนลิงก์ `/app/classic.html#coupon` (index ~1389) ด้วย view ใหม่
- **ตรวจรับ:** ซีรีส์/ยอด/ MoM ตรงกับ classic; ตัวเลขส่วนลดคูปองสอดคล้องกับ §บัญชี (pos_coupon_day)

### เฟส 3 — Interactive write UIs: Payout matching + Discrepancies admin
**(เป็น write-workflow ทั้งคู่ — ต้องระวัง manager-gating + ตรวจ write path เทียบ classic)**
- **3a Payout matching เต็ม:** พอร์ต `openPayoutMatch` (`/api/payout-matching`) — รวมเคส KTC 2-ทาง/awaiting/matched
  - ตรวจรับ: เทียบ classic ราย payout; LINE MAN ฿500 threshold ยังถูก (ดู [[recon_lineman_payout_threshold]])
- **3b Discrepancies admin** (เจ้าของยืนยัน 2026-06-19 ว่า **ทีมยังใช้ bulk-resolve/assign อยู่ → ต้องย้าย**):
  - พอร์ต `loadDiscrepancies` (classic ~2915): ตารางกรอง (resolution/category/bank) + per-row resolve dropdown + assign + drill-per-day + **bulk-resolve**
  - endpoints: `/api/sessions/{sid}/discrepancies`, `/api/discrepancies/{id}/resolve` (PATCH), `/api/discrepancies/{id}/assign` (PATCH), `/api/discrepancies/{id}/days`, `/api/sessions/{sid}/discrepancies/bulk-resolve` (POST)
  - **อยู่ร่วมกับ** day-ack รายวันของ index (ไม่ทับ ไม่ลบ day-ack) — เป็นมุมมอง admin เพิ่ม
  - ตรวจรับ: filter/bulk-resolve/assign/drill ทำงานเทียบเท่า classic; write ผ่าน manager-gating; เทียบรายการกับ classic

### เฟส 4 — รวม Setup/Payment-map admin เข้าแอป (manager-gated)
- รวม setup.html → route `#setup` ในแอป: payment-map, bank-accounts, acquirer-MIDs, statement-code-map, account-map, params
- **คง manager-token gating ทุก write endpoint** (ดูข้อห้าม §6)
- **ตรวจรับ:** แก้ค่าได้เฉพาะ manager; การ remap มีผลกับข้อมูลเดิม + ไฟล์ใหม่ (เหมือน setup.html เดิม)

### เฟส 5 — Reports hub + UI/UX redesign pass
- รวมเมนู ⋯ เป็น "②รายงาน" ที่สม่ำเสมอ; ทำให้รายงานที่เป็น in-app เปิดในแอป (คงปุ่มพิมพ์/PDF)
- ออกแบบ nav เดียว, design tokens เดียว, mobile-first, role-aware (staff/manager)
- **ตรวจรับ:** ไปทุก view ได้จาก nav เดียว; ไม่มีหน้าจอกำพร้า; พิมพ์/PDF ยังครบ

### เฟส 6 — ลบ classic.html + เก็บกวาด
- ลบลิงก์ทั้ง 2 จุดใน index.html → ลบไฟล์ `classic.html`
- ค้นทั้งรีโปว่าไม่มี ref `classic.html` หลงเหลือ (รวม main.py route, help.html)
- **ตรวจรับสุดท้าย:** ทุกฟีเจอร์ classic-only เข้าถึงได้ในแอปใหม่; canary ตัวเลขยังตรง; เทสต์เขียว; help+UAT อัปเดต

---

## 4. หลักการออกแบบ UI/UX

1. **Navigation เดียว role-aware** — staff เห็นงานประจำวัน; manager เห็นตั้งค่า/admin เพิ่ม (ใช้ RBAC ที่มี ดู [[auth_rbac]])
2. **Design tokens เดียว** — ใช้ CSS variables ของ index.html (`--brand`, `--line`, `--shadow` ฯลฯ); **ห้ามผสม Tailwind CDN ของ classic** (จะได้ระบบสีซ้อน + พึ่ง CDN ออนไลน์)
3. **Mobile-first** — พนักงานสาขาใช้มือถือ; การ์ด/ตารางต้อง responsive; แตะง่าย (touch target ≥ 40px)
4. **Progressive disclosure** — Inbox/งานวันนี้มาก่อน; ของขั้นสูง (audit/trend/setup) อยู่หลังโซน ②③④
5. **คงการพิมพ์/PDF** — endpoint print เดิมต้องอยู่ครบ (บัญชี/ผู้ตรวจสอบใช้กระดาษ)
6. **ภาษาไทย + อ่านง่าย** — คอนทราสต์พอ, ฟอนต์ไทยชัด, ตัวเลข tabular-nums (มีอยู่แล้ว)
7. **ไม่ทำลายสำนวนที่พิสูจน์แล้ว** — re-use pattern ที่มี (status chips, การ์ดสาขา, period filter) อย่ารื้อทิ้งทั้งหมด

---

## 5. การตรวจสอบ/เกณฑ์ตรวจรับ (ทุกเฟสต้องผ่านก่อน "เสร็จ")

1. **เทสต์เขียว:** `cd recon && python -m pytest tests/test_recon.py -q` (ปัจจุบัน 130 passed, 9 skipped) — **ห้ามแก้เทสต์ให้ผ่าน** ถ้าพัง ให้แก้สาเหตุ
2. **Canary บัญชี (ตรวจถดถอยตัวเลข):** บน **สำเนา DB เท่านั้น** (ดูข้อห้าม) — อุดร 2026-05-01 ส่วนลด = ฿35,511.48; เดือน = ฿548,989.77; identity `gross = net + discount + fee + vat` ทุกวันทุกสาขา; Dr=Cr ในร่างบัญชี · **หมายเหตุ Windows:** `cp` ใน git-bash เขียน `/tmp` (WSL) ที่ Python (Windows) อ่านไม่เห็น — ใช้ PowerShell `Copy-Item` ไป `AppData\Local\Temp\` แทน · รัน python จาก worktree root + `sys.path.insert(0,os.getcwd())` ให้ `recon` ชี้แพ็กเกจ worktree (ไม่ใช่ main dir ที่ไม่มี fix)
3. **เทียบกับ classic (oracle):** หน้า/ตัวเลขที่ย้ายมา ต้องตรงกับ classic บนข้อมูลเดียวกัน (จึงห้ามลบ classic ก่อนเฟส 6)
4. **Doc-sync:** เปลี่ยนฟีเจอร์/โครงสร้าง → อัปเดต `help.html` + UAT (ทั้ง `_UAT_CASES` ใน main.py และ `UAT_SIGNOFF_*.md`) ในล็อกสเต็ป (ดู [[recon_doc_sync_rule]])
5. **ไม่มี regression การเข้าถึง:** ทุก view เดิมยังไปถึงได้

---

## 6. 🚫 ข้อห้ามเด็ดขาด (อ่านทุกเฟส — ละเมิด = หยุดและถามเจ้าของ)

### 6.1 Backend / ตรรกะธุรกิจ
- **ห้ามแก้ backend API contract / ตรรกะ recon / ตรรกะบัญชี** เว้นแต่เจ้าของสั่งชัด — งานนี้เป็น **frontend consolidation**
- **ห้ามแตะ** `_daily_finance_data`, `_monthly_finance_html`, `_day_finance_html`, `coupon_discount_total_daily`, `point_pos_total_daily` — เพิ่งแก้บั๊กส่วนลดคูปอง (เด็ก/ผู้สูงอายุ จาก `pos_coupon_day`, gross-up รายได้, net คงเดิม, ตัดซีรีส์แต้มกันนับซ้ำ) ต้องคงพฤติกรรมนี้เป๊ะ (ดู [[gp_formula_rule]])
- **ส่วนลดบนบิลห้ามรั่วเข้าการกระทบยอด POS↔ธนาคาร** — variance ต้องอิง tender เท่านั้น (ช่องคูปอง = 360 ไม่ใช่ยอด gross-up)
- **ห้าม double-count ส่วนลด** — bill-coupon path กับ coupon-tender path เป็นคนละก้อน บวกกันไม่ซ้ำ
- VAT ไม่ใช่รายได้ (ex-VAT basis) — ห้ามแก้ (ดู [[vat_and_analytics_rules]])
- ห้ามทำ headline เป็นยอดสะสม (ดู [[no-cumulative-totals-rule]])
- **ห้ามแก้ schema/รัน migration** เป็นส่วนหนึ่งของงาน UI

### 6.2 ข้อมูล / ฐานข้อมูล
- **อัปโหลด recon มีจริงเฉพาะบน Fly** (local 11 vs Fly 277) — **ห้าม push local→Fly DB เด็ดขาด**
- **ตรวจ/รันสคริปต์กับ "สำเนา DB" เท่านั้น** — `cp` ไฟล์ออกมาก่อน ห้ามชี้ที่ DB จริง/ต้นฉบับ (ดู [[recon_requirement_overhaul]])
- ห้ามแตะฟีเจอร์รหัสผ่าน KTB PDF (`RECON_PDF_PASSWORDS` / `unlock_pdf` / `.pdf_passwords.txt`) (ดู [[ktb_pdf_passwords]])

### 6.3 Auth / สิทธิ์
- **คง manager-token gating** ทุก write endpoint (setup/config/remap) — ห้ามเปิด write หลุด (ดู [[write_auth_gate]], [[auth_rbac]])
- คงการซ่อนเมนูตามบทบาท

### 6.4 Deploy / Git (OneDrive worktree)
- **Deploy เฉพาะ `bash scripts/deploy-clean.sh` จาก worktree** — ห้าม `flyctl` จาก main dir (ดู [[deploy_workflow]])
- ต้อง **commit + push origin/main ก่อน** แล้วค่อย deploy; deploy โค้ด ≠ refresh ข้อมูล
- **ก่อน commit:** `git worktree lock .` กัน OneDrive prune ทำ worktree พัง; ซ่อมในที่ ห้าม remove/re-add (ดู [[git_worktree_corruption_gotcha]])
- **agent ไม่ deploy เอง** — รายงานให้เจ้าของ deploy

### 6.5 Frontend
- **ห้ามนำ Tailwind CDN ของ classic เข้ามาใน index.html** — ใช้ design tokens ของ index เท่านั้น (กัน CDN-dependency + ระบบสีซ้อน)
- **ห้ามลบ endpoint/ปุ่มพิมพ์-PDF** ใด ๆ (บัญชี/audit ต้องใช้)
- **คง franchise-branch exclusion** (master-only) ในทุก view ทุกสาขา (ดู [[cashier_and_targets_data_quality]])
- **ห้ามลบ classic.html ก่อนเฟส 6** และก่อน parity sign-off

---

## 7. 🛠 วิธีกู้คืนเมื่อพลาด (Error Recovery)

| อาการ | สาเหตุที่น่าจะเป็น | วิธีแก้ |
|---|---|---|
| หน้าใหม่โชว์ตัวเลข**ผิด** | logic frontend ผิด / เรียก endpoint ผิด | เทียบกับ **classic หน้าเดียวกัน** บนข้อมูลเดียวกัน (classic = oracle, ยังไม่ลบ) แก้ frontend ให้ตรง |
| **variance/กระทบยอดเปลี่ยน**หลังงาน UI | เผลอแตะ backend | **revert ทันที** — UI ต้อง read-only ต่อ backend |
| **เทสต์แดง** | แก้ผิดจุด | `pytest tests/test_recon.py -q` ดูเคสที่แดง → แก้ **สาเหตุ** ห้ามแก้เทสต์ให้ผ่าน |
| **canary ตัวเลขเพี้ยน** (ส่วนลดอุดร ≠ 35,511.48) | กระทบ path บัญชี | revert การเปลี่ยนที่ทำให้เพี้ยน; ยืนยันว่าไม่ได้แตะ §6.1 |
| **deploy แล้ว live พัง** | โค้ดเฟสล่าสุดมีปัญหา | redeploy ref ดีก่อนหน้า: `bash scripts/deploy-clean.sh <prev-good-sha>` (สคริปต์รับ ref ได้) |
| **worktree พัง** ("not a git repository") | OneDrive lock + git prune | ซ่อม HEAD/commondir/gitdir + `git reset` + `git worktree lock` (ดู [[git_worktree_corruption_gotcha]]) — ซ่อมในที่ |
| **PR ถูก squash-merge แล้ว ทำต่อ conflict** | branch ชน main | cherry-pick delta ลง origin/main สดเป็น branch ใหม่ (ห้าม force-push) |
| **ลิงก์ตาย/หน้าขาว** หลังลบ classic | ลบเร็วไป/ลืม ref | grep ทั้งรีโปหา `classic.html`; ถ้ายังมี dependency → กลับไปเฟส 1–4 ให้ครบก่อน |

**กฎกู้คืนรวม:** ทุกเฟสเป็น commit/PR แยก → ถดถอย = `git revert <commit>` ของเฟสนั้น
classic.html คงไว้เป็น fallback จนจบ; ลบลิงก์เฉพาะเฟส 6

---

## 8. วิธีสั่งงาน Sonnet agent (work packaging)

- **1 เฟส = 1 agent task** — แนบ: เป้าหมายเฟส + งานย่อย + เกณฑ์ตรวจรับ + **ลิงก์เอกสารนี้ (โดยเฉพาะ §6 ข้อห้าม)**
- แต่ละ task ให้ agent: อ่านแผนนี้ → อ่านโค้ดที่เกี่ยว → แก้ (frontend เท่านั้นโดยค่าเริ่มต้น) → รันเทสต์ → อัปเดต doc → **ไม่ deploy** → รายงานผล + canary
- จบเฟส → เจ้าของตรวจ/อนุมัติ → ค่อยสั่งเฟสถัดไป
- เฟสที่ต้องตัดสินใจดีไซน์ (4–5) → ให้ agent **ถามเจ้าของก่อน** (nav model, in-app setup, help drawer) อย่าเดา

---

## 9. งานค้างที่เกี่ยวข้อง (ต้องรู้ก่อนเริ่ม)

- มี commit `fe77723` (แก้ส่วนลดคูปองในร่างบัญชี) **ค้างอยู่บน branch ยังไม่ deploy** — เจ้าของยังไม่อนุมัติ deploy
- `origin/main` อยู่ที่ `72d45ca` (daily-filter + KTB unlock — deploy แล้ว)
- ก่อนเริ่มงาน consolidation ควรเคลียร์สถานะ deploy ของ `fe77723` ให้ชัดก่อน (เจ้าของตัดสิน)
