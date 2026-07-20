# แผน Lean Overhaul — Oshinei Dashboard
วันที่เสนอ: 2026-07-20 · สถานะ: **Phase 0–5 DEPLOYED LIVE 2026-07-20 (commit eecd402)** — telemetry เริ่มนับแล้ว → กลับมาดู kill list ~2026-08-03 · เหลือ: การลบแท็บ/เส้นจริง, router split, JS แตกละเอียด, GP audit 37 เส้น · ⚠️ โค้ด JS ของ dashboard อยู่ที่ **app.js** แล้ว (ไม่ใช่ index.html)

---

## 1) สภาพปัจจุบัน (ตัวเลขจริงจากการสแกนโค้ด)

| ตัวชี้วัด | ค่าปัจจุบัน |
|---|---|
| backend/main.py | **20,258 บรรทัด** ไฟล์เดียว |
| API endpoints | **193 เส้น** |
| index.html | **27,581 บรรทัด** ไฟล์เดียว |
| จุดเรียก API จาก frontend | 287 จุด (`fetch(`) |
| แท็บ/หน้า | **46 แท็บ** · เมนู sidebar 44 รายการ ใน 8 หมวด |
| Usage telemetry | **ไม่มีเลย** — ไม่มีข้อมูลว่าแท็บไหนถูกเปิดจริง |
| Endpoint ที่ frontend ไม่เรียกเลย | 8 เส้น (ตายจริง ~5 หลังตัดที่ script ใช้) |
| Endpoint ซ้ำกันตรง ๆ | 1 คู่: `/api/analysis/table_turnover` กับ `/api/analysis/table-turnover` |

### จุดทับซ้อนหลัก (overlap clusters)

| กลุ่ม | อาการ |
|---|---|
| **เมนู** | 7 แท็บตอบคำถามเดียวกัน "เมนูไหนทำเงิน/ควรปรับ": วิเคราะห์เมนู, เมนูทำเงิน (engineering), เทียบเมนู A/B, ⚡ปรับเมนู, 💡โอกาสเมนู, menuimpact, menuefficiency |
| **Forecast** | 4 เส้น: `analysis/forecast`, `analysis/forecast_branches`, `analysis/revenue-forecast`, `buffet/forecast` |
| **GP/กำไร** | คำนวณกระจาย 6 ตระกูล: `profit/*` (6), `pnl/*` (5), `gp/by-branch-month`, `sales/margin`, `analysis/gross_profit`, `buffet/gp` |
| **ส่วนลด** | 8+ เส้น `analysis/discount*` + แท็บส่วนลด + วิเคราะห์เชิงลึก + Mgmt Report §6 |
| **Simulator** | 3 ที่: BoM+Simulator (bomcost), 🎮 Simulators (simhub), buffet/upgrade-simulator + breakeven + cost-sensitivity |
| **ต้นทุน/หัว** | 3 ที่: 💸 ต้นทุน/หัว (costhead), 🏅 Top60 (costheadtop60), `sales/cost-per-head` |
| **ภาพรวม** | 5 หน้า: หน้าแรก, ภาพรวม&KPI, ต้องดูวันนี้, แนวโน้ม, ยอดขายรายวัน — "ต้องดูวันนี้" ซ้ำกับ action queue ในหน้าแรก |
| **บุฟเฟ่ต์** | `/api/buffet/*` 24 เส้น หลายเส้นเป็นมุมมองย่อยของกันและกัน |

### Endpoint ตาย (frontend ปัจจุบันไม่เรียก — เช็ค main tree แล้ว)
- `/api/_admin/apply-cost-patches` — ไม่มีใครเรียกเลย → ลบได้
- `/api/ai/commentary`, `/api/buffet/branch-tier-matrix`, `/api/profit/by-sku` — เหลืออ้างอิงแค่ใน backup HTML เก่า (2026-05-28) → orphan จาก UI รุ่นก่อน
- `/api/bom/aliases` (GET/PUT), `/api/skus/business-types` — เหลือแค่ใน test → admin API ที่ UI ไม่มีปุ่มแล้ว
- ⚠️ `/api/_admin/seed-db` — **ห้ามลบ** (refresh_fly_db.py / data-refresh flow ใช้)

---

## 2) หลักคิดของแผน

1. **วัดก่อนตัด** — ตอนนี้ "อันไหนไม่ได้ใช้" คือการเดา ต้องมี telemetry จริง 2–4 สัปดาห์ + เจ้าของ tick รายการก่อนลบ
2. **ซ่อนก่อนลบ** — soft-kill (เอาออกจากเมนู แต่ redirect ยังทำงาน) 1 เดือน → ค่อย hard delete เมื่อ telemetry ยืนยัน 0 hits
3. **รวมด้วย parameter ไม่ใช่เพิ่มเส้นใหม่** — forecast 4 เส้น → 1 เส้น + `mode=`
4. **ใช้ pattern ที่พิสูจน์แล้วในแอป** — `TAB_CLUSTERS` (cost/profit cluster bar) มีอยู่แล้วใน index.html → ขยายเป็นทุกหมวด แทนการสร้างระบบใหม่
5. ทุกเฟสมี gate: test suite ผ่านครบ → deploy ผ่าน worktree script → verify ด้วย probe บนเว็บจริง
6. งานแก้ไฟล์ใหญ่ทำแบบ atomic write ครั้งเดียว (กัน OneDrive กลืน edit) และงานยาวทำใน worktree

---

## 3) Phase 0 — Instrument & Decide (สัปดาห์ที่ 1 + เก็บข้อมูล 2–4 สัปดาห์)

**งานโค้ด (เล็กมาก, ~ครึ่งวัน):**
- ตาราง `usage_log(ts, user, kind, name)` + middleware นับ endpoint hit (sample ได้ ไม่ต้องทุก request)
- 1 บรรทัดใน `switchTab()` ยิง beacon `POST /api/usage/tab`
- หน้าเล็ก ๆ ใน Settings ดูสรุป hits ต่อแท็บ/ต่อเส้น

**งานตัดสินใจ (เจ้าของ):**
- Checklist 44 เมนู ให้ tick: 🟢 ใช้ประจำ / 🟡 ใช้บางครั้ง / 🔴 ไม่เคยใช้
- เทียบกับ telemetry ตอนจบ → ได้ **keep / merge / kill list** ที่มีข้อมูลรองรับ

**Exit criteria:** รายการ keep/merge/kill เซ็นชื่อโดยเจ้าของ

---

## 4) Phase 1 — Quick wins ✅ ทำแล้ว 2026-07-20

1. ✅ ลบ endpoint ตาย **7 เส้น**: apply-cost-patches, ai/commentary, buffet/branch-tier-matrix, profit/by-sku, bom/aliases (GET+PUT), skus/business-types + แก้ test ที่อ้างถึง → **193 − 7 + 2 (usage) = 188 เส้น**
2. ⏭️ `table_turnover` / `table-turnover` — ตรวจแล้ว **ไม่ใช่ duplicate แท้**: ทั้งคู่ถูก frontend ใช้คนละมุมมอง (timeflow 2 จุด vs monthly view 1 จุด) → ย้ายไปรวมแบบ param ใน Phase 3
3. ✅ ลบ `backend/backup_oshinei_dashboard_html_20260528_151304.html` (680KB backup ซ้ำกับ git history)
4. ✅ 🏅 Top60 เข้า cluster bar 💰 ต้นทุน + ซ่อนจาก sidebar (pattern เดียวกับ costhead/heavyeater/coverage ที่ซ่อนอยู่แล้ว)
5. ✅ ซ่อน "🔔 ต้องดูวันนี้" จาก sidebar — เข้าถึงผ่านปุ่ม "🔔 สัญญาณทั้งระบบ →" ในหน้าแรก (soft-kill: แท็บ+โค้ดยังอยู่ครบ รอ telemetry ยืนยันก่อนลบจริง)

**ผลจริง:** −7 endpoints (เหลือ 188), sidebar −2 รายการ (เหลือ 33 ที่มองเห็น), main.py −201 บรรทัดสุทธิ, test 333+ ตัวผ่านครบ, verify แล้วบน dev server (beacon ยิงจริง, usage panel แสดงผลจริง, endpoint ที่ลบ = 404)

### Phase 0 ✅ ทำแล้ว 2026-07-20 (รายละเอียด implement)
- `usage.db` แยกไฟล์จาก DB หลัก (กัน sync/refresh swap ทับ) ที่ `DB_PATH.parent` · ตาราง `usage_daily(day,kind,name,hits,last_user)` UPSERT รายวัน · วัน = เวลาไทย (+7h)
- middleware นับทุก `/api/*` ที่ status < 400 (ไม่นับ `/api/usage/*` เอง) · beacon `POST /api/usage/tab` จาก `switchTab()` ผ่าน `navigator.sendBeacon`
- ดูผลที่ **Settings → 📈 การใช้งานจริง**: แท็บที่ถูกเปิด / แท็บที่ยังไม่ถูกเปิดเลย / Top 20 API
- test ใหม่ `backend/test_usage_telemetry.py` (3 ตัว)

---

## 5) Phase 2 — UI Consolidation ✅ ทำแล้ว 2026-07-20 (แบบ soft — ไม่ลบอะไร)

**ผลจริง: sidebar 45 → 12 รายการใน 3 หมวด (📊 วิเคราะห์ · 📄 รายงาน · ⚙️ เครื่องมือ)**
- 7 clusters: 📊 ภาพรวม(5) · 🍱 บุฟเฟ่ต์(5) · 🍣 เมนู(4) · ⚡ ปรับเมนู(3) · 💰 ต้นทุน(8) · 💵 กำไร&P&L(5) · 🛒 ปฏิบัติการ(9) — entry ของ cluster = tabs[0], สมาชิกที่เหลือซ่อนจาก sidebar แต่ DOM อยู่ครบ เข้าผ่าน cluster bar บนหน้า
- ทุกแท็บเดิมทำงานผ่าน `switchTab('ชื่อเดิม')` ครบ — bookmark/นิสัยผู้ใช้ไม่พัง ไม่มีการลบแท็บใด (การลบจริงรอ telemetry ยืนยัน)
- **RBAC safety net** ใน applyPermissions: ถ้า role เปิด entry ของ cluster ไม่ได้แต่เปิดสมาชิกอื่นได้ → โชว์ nav ของสมาชิกตัวแรกที่มีสิทธิ์แทน (เช่น accounting ไม่มี ops.report แต่มี ops.discounts)
- คู่มือ (📖) เพิ่มย่อหน้าอัปเดต 2026-07-20 อธิบาย nav ใหม่
- หมายเหตุ: แท็บ `pnl` (P&L Deep Dive) ไม่มี nav item มาแต่เดิม (unreachable อยู่แล้ว) — คงสถานะเดิม รอ checklist ตัดสินว่าจะฟื้นหรือลบ

### แผนเดิม (เก็บไว้อ้างอิง — ส่วน "ลบจริง" ยังรอ kill list)

โครงเมนูใหม่ 7 หมวด — ทุกแท็บเดิมที่ยุบ ใช้กลไก **cluster bar / subtab ที่มีอยู่แล้ว** (buffetperf มี subtab, cost+profit มี cluster bar แล้ว):

| เมนูใหม่ | ยุบมาจาก (แท็บเดิม) |
|---|---|
| 🏠 หน้าแรก | home + action |
| 📊 ยอดขาย & แนวโน้ม | overall + trend + dailysales + compare (subtabs) |
| 🍱 บุฟเฟ่ต์ & ลูกค้า | buffetperf + childbuffet + heavyeater + custtype + timeflow |
| 🍣 เมนู (Menu Studio) | engineering + menu + menucompare + menuimpact + menuefficiency + profit(SKU) |
| ⚡ ปรับเมนู | menuactionboard + menuopportunities + เป้าลดต้นทุน |
| 💰 ต้นทุน | cost + bomcost + simhub + branchbom + coverage + costhead(+top60) |
| 💵 สาขา & กำไร | gpbranch + pnlbranch + pnlbill + benchmark |
| 🛡️ Operations | opsreport + staff + discounts + ops + cashctl + cancelbill + modifiers + pricezone + closetime |
| 📄 รายงาน | report (Monthly) + mgtreport (Management) |
| ⚙️ ตั้งค่า + 📖 คู่มือ | settings + manual |

**กติกา migration:**
- `switchTab('ชื่อเก่า')` → redirect ไปแท็บใหม่ + เปิด subtab ที่ถูกต้อง (bookmark/นิสัยผู้ใช้ไม่พัง)
- แท็บใน kill list: ซ่อนจากเมนู 1 เดือน (เข้าถึงได้ทาง "More") → ลบจริงเมื่อ 0 hits
- อัปเดต RBAC module mapping + PAGE_TITLES + help/manual + UAT ให้ lockstep
- แท็บที่มี picker ของตัวเองต้องต่อ `_syncExtra*` / บัสต์ `_tabLastKey` ตามกติกา global filter

**ผลลัพธ์:** เมนู 44 → ~16 · หาของเจอใน 1 คลิก · onboarding ผู้ใช้ใหม่ง่ายขึ้นมาก

---

## 6) Phase 3 — Backend Consolidation ✅ บางส่วน 2026-07-20 · **193 → 184 เส้น**

1. ✅ **Forecast 4 เส้น → 1**: `GET /api/forecast?mode=month|branches|trend|buffet` — dispatcher เรียก 4 ฟังก์ชันเดิม (คง @ttl_cache แยกชื่อ ไม่ชนแคช) · frontend สลับครบ 7 จุด · เส้นเก่าลบเลย (ไม่มี script/external caller — ตรวจแล้ว) → 404
2. ✅ **table_turnover 2 เส้น → 1**: `GET /api/analysis/table-turnover?view=branches|dwell` (default=branches คงพฤติกรรมเดิมของ path) · timeflow ใช้ view=dwell
3. ⏳ **GP/COGS audit**: สแกนอัตโนมัติพบ **37 เส้น** ที่คำนวณ GP/COGS โดยไม่ผ่าน `_cogs_case()` (รายชื่อจาก script — หลายเส้นใช้ `sku_effective_cost` ที่ถูกต้องอยู่แล้ว จึงเป็น candidate ไม่ใช่ confirmed bug) → ต้อง review ทีละเส้น ห้ามแก้แบบเหมา (บทเรียน buffet double-count)
4. ⏳ ลบเส้น orphan หลังลบแท็บจริง — รอ kill list จาก telemetry
5. ⏳ **แตก main.py เป็น modules** (ทำท้ายสุด หลังจำนวนเส้นนิ่งแล้ว):
   ```
   backend/
     app.py            # FastAPI factory + middleware + INDEX serve
     core/             # db, cache (ttl_cache), auth glue, _cogs_case, month_summary
     routers/
       sales.py  buffet.py  menu.py  cost.py  pnl.py
       reports.py  admin.py  recon.py
   ```
   - ทำแบบ **mechanical move** (ไม่แก้ logic พร้อมกัน) — `test_endpoints.py` + invariant tests ต้องผ่านครบก่อน/หลัง
   - ทำใน worktree, commit ถี่, deploy ครั้งเดียวตอนจบเฟส

---

## 7) Phase 4 — Frontend Split ✅ ทำแล้ว 2026-07-20

- **index.html 27,8xx → 6,523 บรรทัด** (เป้า <10k ✓) — JS ทั้งก้อน (21,105 บรรทัด) ย้ายไป **`app.js`** ที่ root แบบ verbatim (ไม่แตะ logic เลย ลำดับรันเหมือนเดิม 100%)
- เสิร์ฟผ่าน route ใหม่ `GET /app.js` พร้อม **`Cache-Control: no-cache`** + ETag → browser revalidate ทุกครั้ง (304 เมื่อไม่เปลี่ยน) — deploy แล้วไม่มีทางติด JS ค้าง cache (ต่างจาก /vendor ที่พึ่ง heuristic caching ได้เพราะ lib ไม่เคยเปลี่ยน)
- **Dockerfile เพิ่ม `COPY app.js`** (สำคัญ — Dockerfile copy แบบระบุไฟล์ ไม่ใส่ = deploy แล้วจอขาว)
- script <head> เล็ก (theme bootstrap ~22 บรรทัด) คงไว้ inline ตามเดิม
- ข้อจำกัดที่รู้: เปิด index.html แบบ `file://` จะโหลด /app.js ไม่ได้ (ใช้ผ่าน backend เท่านั้น — ตรงกับ workflow ปัจจุบันอยู่แล้ว)
- test ใหม่ `backend/test_static_split.py` (2 ตัว: shell อ้าง /app.js + เสิร์ฟด้วย no-cache)
- การแตกละเอียดเป็น ~8 ไฟล์ต่อหมวด: **เลื่อนไปหลัง kill list** — แตกตอนนี้แล้วต้องรื้ออีกรอบตอนลบแท็บจริง

## 8) Phase 5 — Data & Perf ✅ ทำแล้ว 2026-07-20

- **PR#14 Phase 2 (deferred) ปิดแล้ว:** รัน `phase2_db_clean.py` — dry-run พบว่า junk categories ถูกล้างไปแล้วจาก re-ingest รอบก่อน เหลือ **orphan SKUs 128 ตัว → repoint เข้า 'Oshinei other'** สำเร็จ, revenue invariant ตรวจผ่าน (฿1,294.499619M เท่าเดิมเป๊ะ), backup อัตโนมัติก่อนแก้ (`oshinei.db.safebak_phase2_20260720_130016`)
- **VACUUM + ANALYZE + PRAGMA optimize** เสร็จ — VACUUM คืนพื้นที่แค่ ~7MB (986→978.9MB) เพราะ freelist = 0.0% อยู่แล้ว ไม่ bloat; ANALYZE เขียน sqlite_stat1 ช่วย query planner
- **Index audit (สังเกต ไม่แก้):** sales_transaction มี **16 indexes** (อาจ over-indexed — ภาระตอน sync เขียน), sale_payment 2 (มี idx_sp_receipt ✓), sale_bill_item 1, sales_sku_month 0 — ยังไม่เพิ่ม/ลดจนกว่าจะมี slow query จริงชี้เป้า
- **📢 คำแนะนำให้เจ้าของตัดสินใจ (ไม่ได้ลบให้):** backend/ มีไฟล์ backup DB เก่า **30 ไฟล์ รวม 7.07 GB** (พ.ค.–มิ.ย. + อันใหม่วันนี้) — OneDrive ต้อง sync ทั้งหมดนี้ด้วย แนะนำเก็บอันล่าสุด 1–2 อันแล้วย้ายที่เหลือออกนอก OneDrive/ลบ

---

## 9) ความเสี่ยงและการกันพลาด

| ความเสี่ยง | การกัน |
|---|---|
| ลบของที่เจ้าของแอบใช้ | Phase 0 telemetry + checklist + soft-kill 1 เดือนก่อนลบจริง |
| Refactor ทำ logic เพี้ยน | mechanical move เท่านั้น + test suite เป็น gate ทุกเฟส |
| OneDrive กลืน edit | atomic write ครั้งเดียวต่อไฟล์ / งานใหญ่ทำใน worktree |
| Deploy ผิดวิธี | ใช้ `scripts/deploy-clean.sh` เท่านั้น + verify ด้วย probe |
| RBAC/คู่มือ/UAT หลุด sync | รวมอยู่ใน definition-of-done ของ Phase 2 |
| ผู้ใช้งง UI เปลี่ยน | redirect ทุกชื่อเก่า + ประกาศใน help + ทำทีละหมวด |

## 10) ตัวชี้วัดความสำเร็จ

- เมนู sidebar 44 → **≤16** · แท็บ 46 → **≤20 panel**
- Endpoints 193 → **≤130** และ**ทุกเส้นที่เหลือมี hit จริงใน 30 วัน**
- โค้ดรวมลดลง ≥25% (จาก ~48k บรรทัด 2 ไฟล์หลัก)
- ฟีเจอร์ที่เจ้าของใช้ประจำ: **ครบ 100% ไม่หาย**

## 11) Timeline โดยรวม (~6 สัปดาห์ แบบไม่รีบ)

| สัปดาห์ | งาน |
|---|---|
| 1 | Phase 0 (วาง telemetry) + Phase 1 (quick wins) |
| 2–4 | เก็บ telemetry · ระหว่างรอ: เตรียม Phase 2 ใน worktree + เจ้าของ tick checklist |
| 4–5 | Phase 2 (ยุบเมนูทีละหมวด, deploy ทีละก้อน) |
| 5–6 | Phase 3 (ยุบ endpoint + แตก routers) |
| 6+ | Phase 4–5 (split frontend, VACUUM) — ทำเมื่อทุกอย่างนิ่ง |
