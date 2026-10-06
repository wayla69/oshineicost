# ADR-001 — ฐานของ BoM Calculation Center (Sprint S0)

สถานะ: **ยอมรับ** · วันที่ 2026-10-06 · อ้างอิง `docs/BOM_CENTER_PLAN.md` §4–§5 · โค้ด `backend/bom_center/`

## บริบท

ต้นทุนวัตถุดิบในแอปวันนี้มาจาก `sku_cost` / `bom_ingredients` / `bom_sku_alias` ผ่านลำดับความสำคัญคงที่ใน `core._build_sku_effective_cost` และถูกป้อนด้วยไฟล์ 6 ชั้นใน `cost_sources.apply_all` ไม่มีเวอร์ชัน ไม่มีราคารายเดือน และตารางที่ผู้ใช้กรอกบนเว็บเคยถูก DB swap ลบซ้ำ ๆ (#209) แผนรื้อใหม่ต้องเริ่มจากฐานที่ (ก) ประกาศความเป็นเจ้าของตารางตั้งแต่ชื่อ (ข) มีเวอร์ชัน และ (ค) พิสูจน์ได้ว่าตัวเลขเดิมไม่กระโดดก่อนสลับ engine

## การตัดสินใจ

1. **สอง prefix สองกฎ** — `bc_*` = ของเว็บ (กรอก/นำเข้าบนไซต์: วัตถุดิบ ราคา สูตร release ปฏิทิน นับสต๊อก ออเดอร์ audit) อยู่ใน `core._PRESERVED_PREFIXES` → ทุก swap คัดลอกจาก live ไป incoming · `bcd_*` = ตารางอนุพันธ์ (cost cache, demand รายวัน) **ไม่ preserve** เพราะต้องคำนวณใหม่หลัง swap ที่นำยอดขายใหม่มา (`LIKE 'bc\_%'` ไม่ตรงกับ `bcd_`) ชื่อตารางจึงบอกชะตาของมันเอง และบั๊กคลาส #209 ปิดเชิงโครงสร้าง
2. **Release เป็นแกน** — `bc_release(kind baseline|monthly, effective_month, status draft→review→released→retired)`; เวอร์ชันที่มีผลกับเดือน M = release ที่ `status='released'` และ `effective_month ≤ M` ค่าสูงสุด (`engine.release_in_force`) · baseline ใช้ `effective_month='1900-01'` เป็นค่าตั้งต้นของทุกเดือนที่ไม่มี release รายเดือนครอบ → ประวัติเดือนเก่ายังอ่านตัวเลขเดิมได้เสมอ
3. **สูตรเก็บ "กฎที่ชนะ" เป็นข้อมูล** — `bc_recipe.kind ∈ {zero, flat, recipe, alias}` แทน COALESCE สามตาราง: zero = รหัสแพ็กเกจบุฟเฟ่ต์, flat = ต้นทุนเหมาชนะสูตร (เก็บบรรทัดสูตรไว้ด้วยเพื่อให้ analyst เห็นสิ่งที่ถูกบัง), recipe = Σ บรรทัด, alias = บรรทัดของ SKU ปลายทาง 1 ชั้น (ไม่ใช่ flat ของปลายทาง — ตรงกับพฤติกรรม `bom_sku_alias` เดิม)
4. **Release v0 = engine วันนี้แช่แข็ง** — `snapshot_v0.py` คัดลอกตารางเดิมเข้า v0 โดย `line_cost` เก็บตัวเลขเดิมเป๊ะ แล้ว **ตรวจ parity ต่อ SKU** กับ engine เดิมที่คำนวณใหม่จาก SQL เดียวกัน (`rules.raw_unit_cost_sql`) ต่างเกิน ฿0.005 แม้ SKU เดียว = สคริปต์ปฏิเสธการเขียน · dry-run เป็นค่าเริ่มต้น `--apply` สำรอง `.pre_bc_v0_*` ก่อน
5. **ยังไม่สลับ engine** — S0 สร้างตารางที่ startup (`main._bom_startup` → `bom_center.schema.ensure`) และพิสูจน์ parity เท่านั้น; `sku_effective_cost` ยังเป็นของ engine เดิมจนถึง S4 เมื่อ v1 ผ่านรายงานเทียบและ management รับทราบ
6. **ราคาแยกจากสูตร** (โครงไว้ตั้งแต่ S0) — `bc_recipe_line` ของ release รายเดือนไม่มี `line_cost`; ราคามาจาก `bc_ingredient_price(code, month)` = ราคาซื้อจริงถัวเฉลี่ยจากบัญชี (ข้อตัดสินใจ §10-1) โดย `line_cost` มีค่าเฉพาะ baseline
7. **PC อ่าน ไม่คำนวณ** — `build_menu_fact.py` บน PC จะอ่าน `bcd_cost_cache` ของ release ที่มีผล (ผ่าน fly_state ที่ดึงมาหรือ API) ไม่คำนวณต้นทุนเอง (ต่อยอดกฎ #193) — ลงมือใน S4

## ผลที่ตามมา

- ตาราง `bc_*` ใหม่ทุกตารางได้สิทธิ์ preserve อัตโนมัติ ไม่ต้องแก้ `_PRESERVED_EXACT` ทีละชื่ออีก; ต้องตรวจว่าโผล่ใน `fly_state_*.manifest.txt` หลัง deploy แรก
- ตารางอนุพันธ์ขนาดใหญ่ (`bcd_demand_day`) ไม่ถ่วงเวลา swap และไม่กลายเป็นข้อมูลค้าง
- การเปลี่ยนตัวเลข COGS ทุกครั้งต่อจากนี้มี "ก่อน/หลัง" ที่เป็นเวอร์ชัน เทียบกันได้ และย้อนกลับได้
- ของเดิม (`sku_cost`, `bom_ingredients`, `cost_sources`) ยังทำงานตามเดิมจนถึง cut-over — ห้ามลบใน Phase 1

## ทางเลือกที่ไม่เลือก

- ใส่ `bc_cost_cache` ใน prefix preserve → swap จะคัดลอกตารางที่ต้องคำนวณใหม่อยู่ดี และถ้าเป็น demand รายวันหลายล้านแถว swap จะช้า
- ตั้ง `effective_month` ของ baseline เป็น NULL → ทุก query ต้อง COALESCE; ค่า sentinel `'1900-01'` เปรียบเทียบตรง ๆ ได้
- สลับ engine ใน S0 เลย → ไม่มีรายงานเทียบให้ analyst/management ดูก่อน ขัดกฎ "ตัวเลขเดิมต้องไม่กระโดดเงียบ ๆ"
