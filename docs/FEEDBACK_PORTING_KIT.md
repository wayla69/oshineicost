# 💬 Customer-voice module — porting kit (E7)

คู่มือย้ายโมดูล "เสียงลูกค้า" ไปใช้ในแอปอื่น (FastAPI + SQLite แบบเดียวกับแดชบอร์ดนี้) —
ทุกจุดที่โมดูลพึ่งพาแอปแม่ถูกรวมไว้ในไฟล์เดียว `backend/feedback_host.py`
(`backend/test_feedback_kit.py` บังคับว่าไฟล์ของโมดูลห้าม import แอปแม่ตรง ๆ)
สถาปัตยกรรมและเหตุผลการออกแบบอยู่ใน `docs/FEEDBACK_ARCHITECTURE.html`; วิธีตั้งค่าแหล่งข้อมูลใน `docs/FEEDBACK_SOURCES.html`

## 1. ไฟล์ที่ต้องคัดลอก (โมดูล)

| กลุ่ม | ไฟล์ | หน้าที่ |
|---|---|---|
| หลัก | `backend/routers/feedback.py` | ตาราง `customer_feedback`, อัปโหลด/นำเข้า, สรุป, รายการ, กล่องงาน (SLA, ✓ ตอบแล้ว, 🚩), คิวตอบรีวิว, webhook Meta |
| | `backend/feedback_nlp.py` | พจนานุกรมไทย (ความรู้สึก หัวข้อ เมนู ความร้ายแรง ชื่อพนักงาน), Claude (batch, ร่างคำตอบ, สรุป, ถามข้อมูล) |
| | `backend/notify.py` | Telegram (แจ้งเตือนรีวิวลบ, สรุปรายสัปดาห์) |
| ช่องทาง | `backend/feedback_sources/*.py` | Google Business Profile, Facebook (+Inbox), Instagram, TikTok, X, YouTube, RSS/Google News, Google Places |
| | `backend/fetch_feedback.py` | ตัวดึงรายวันบนเครื่อง sync: ดึง → โพสต์เข้าแอป (`ApiSink`) หรือลง SQLite (`DbSink`), ส่งคำตอบ, สรุปวันจันทร์, ภาพถ่ายคู่แข่งรายเดือน |
| ส่วนขยาย | `backend/routers/survey.py` | แบบสอบถาม QR ต่อสาขา (หน้า public `/s/{code}`, โปสเตอร์) |
| | `backend/routers/feedback_actions.py` | งานแก้ + พิสูจน์ผล 28 วัน, สรุปสัปดาห์ (`compute_digest`) |
| | `backend/routers/feedback_ai.py` | สรุปเป็นย่อหน้า, ถามข้อมูล |
| | `backend/routers/feedback_business.py` | เสียง × ยอดขาย (ต้องมีตารางบิล — ดูข้อ 3) |
| | `backend/routers/feedback_competitors.py` | ภาพถ่ายคู่แข่งจาก Google Places |
| checklist | `backend/routers/checklist.py`, `backend/checklists/feedback_rollout.json`, `scripts/build_feedback_checklist_pdf.py` | checklist เปิดใช้ (ติ๊กร่วมกัน, ปิดสมบูรณ์เมื่อครบ) + ฉบับพิมพ์ |
| หน้าจอ | `tabs/49-feedback.html`, `js/11-feedback.js`, `tabs/50-checklist.html`, `js/12-checklist.js` | แท็บ (vanilla JS ไม่มี framework) |
| ทดสอบ | `backend/test_feedback*.py`, `backend/test_checklist.py`, `scripts/verify_*.py` | pytest (ไม่ต่อเน็ต ใช้ fake http) + Playwright |

## 2. สัญญากับแอปแม่ — `backend/feedback_host.py` (เขียนใหม่ในแอปปลายทาง)

```python
get_db()                 # context manager → sqlite3 connection ที่ row_factory = sqlite3.Row
require_write_token      # FastAPI dependency กันการเขียน (จะเป็น no-op ก็ได้)
scope_branch(request)    # สาขาที่บัญชีนี้ถูกจำกัด หรือ None
who(request, body)       # ชื่อผู้ทำรายการสำหรับช่อง audit ('local' เมื่อไม่มีล็อกอิน)
branch_rows(con)         # [{branch_id, name, branch_type, active}] — สาขาที่โมดูลรู้จัก
menu_names(con)          # ชื่อเมนูที่ใช้จับการพูดถึง (ว่างได้)
thai_today() / thai_now()# วัน/เวลาธุรกิจ (ที่นี่ UTC+7)
sales_available(con)     # True เมื่อมีตารางบิลที่ E2 อ่าน (sales_transaction, sale_bill_item)
```

โมดูลไม่แตะตารางอื่นของแอปแม่นอกจาก `branches`, `skus` (ผ่านสองฟังก์ชันข้างบน) และตารางบิลของ E2 (ถ้ามี)
ตารางของโมดูลเอง: `customer_feedback`, `feedback_sync_log`, `feedback_webhook_events`, `feedback_survey_link`,
`feedback_action`, `feedback_competitor`, `checklist_state`, `checklist_log` — สร้างเองด้วย `CREATE TABLE IF NOT EXISTS` + ALTER เพิ่มคอลัมน์

## 3. การต่อสายในแอปแม่ (นอกไฟล์โมดูล)

1. **ลงทะเบียน router** ใน `main.py`: `feedback`, `checklist`, `survey`, `feedback_actions`, `feedback_ai`, `feedback_business`, `feedback_competitors`
2. **ยกเว้นล็อกอิน** เฉพาะสองเส้นทางสาธารณะ: `/api/feedback/webhook/` (Meta ตรวจลายเซ็นเอง) และ `/api/feedback/survey/` (รหัสสาขาใน URL + rate limit + honeypot) — ใส่ trailing slash เพื่อไม่ให้ `/api/feedback/webhook-events` และ `/api/feedback/survey-links` หลุด
3. **สิทธิ์** (ถ้ามี RBAC): กฎเดียว `("*", "/api/feedback", {"customer.feedback"})` + `("*", "/api/checklist", {"customer.feedback"})`; บัญชีเฉพาะสาขา = ใส่ชื่อสาขาใน token แล้วให้ `scope_branch()` อ่านคืน
4. **ตารางที่ต้องรอดจากการแทนที่ฐานข้อมูล** (ถ้าแอปแม่มีการ push DB ทับรายวันแบบนี้): `customer_feedback` + ทุกตารางที่ขึ้นต้น `feedback_` และ `checklist_` — ที่นี่คือ `core._PRESERVED_EXACT / _PRESERVED_PREFIXES`
5. **ความลับ**: `ANTHROPIC_API_KEY` (ทางเลือก — ไม่มีก็ใช้พจนานุกรม/กฎ/ค้นหาคำ), `META_APP_SECRET` + `META_WEBHOOK_VERIFY_TOKEN` (webhook), `OSHINEI_TG_TOKEN` + `OSHINEI_TG_CHAT_ID` (Telegram บนเครื่องดึง), `.feedback_config.json` (token ของแต่ละแพลตฟอร์ม, Places key, กลุ่ม Telegram รายสาขา) — เก็บนอก repo และสำรอง
6. **หน้าจอ**: แท็บใช้ตัวช่วยของแอปแม่ที่ต้องมีชื่อเดียวกัน: `api(url, opts)`, `getBranch()`, `getMonthFrom()`, `getMonthTo()`, `_filterKey()`, `_tabLastKey`, `switchTab(name)`, `mkChart(id, config)` (Chart.js), `notifyError(msg)`, `notifySuccess(msg)`, `window.AUTH {enabled, modules[], branch}`, CSS token `--border --bg-input --text-muted --text-secondary --text-primary --bg-card` และคลาส `card`; หน้าแรกมือถือเรียก `msLoadVoice()` (ทางเลือก)
7. **Management Report §12** (ทางเลือก): เรียก `routers.feedback_actions.compute_digest(con, start, end, prev_start, prev_end, branch, group)` แล้วใส่เป็น `feedback` ในผลลัพธ์ + ส่วนแสดงผล/ชีตในรายงานของแอปแม่
8. **งานตามเวลา** บนเครื่องที่ต่อเน็ตได้: `fetch_feedback.py run` วันละครั้ง (เก็บ log, ASCII-only .bat ถ้าใช้ตัว tee ของที่นี่)

## 4. ตรวจว่าต่อครบ

```bash
python -m pytest backend/test_feedback_kit.py backend/test_feedback*.py backend/test_checklist.py
```

`test_feedback_kit.py` ล้มถ้า (ก) `feedback_host` ขาดชื่อใดในสัญญา (ข) ไฟล์โมดูล import แอปแม่ตรง ๆ (ค) ตารางของโมดูลไม่อยู่ในรายการที่รอดจากการแทนที่ DB

## 5. สิ่งที่ยังผูกกับที่นี่ (รู้ไว้ก่อนย้าย)

- ภาษาไทยในพจนานุกรม/แม่แบบ/ข้อความหน้าจอ — เปลี่ยน `BRAND` และคำใน `feedback_nlp.py` ตามแบรนด์ปลายทาง
- ชื่อสาขาเป็นกุญแจ (ไม่ใช่ id) ในหลายตาราง เพื่อให้รีวิวจับคู่กับชื่อลิสติ้ง/เพจได้ — ถ้าเปลี่ยนชื่อสาขาให้ UPDATE `branch_name` ในตารางของโมดูลด้วย
- E2 (เสียง × ยอดขาย) อ่านคอลัมน์ `sales_transaction(branch_id, sold_at, customer_count, total_inc_vat, discount)` และ `sale_bill_item(receipt_no, sku_id, qty, rev, bom_cost)` — ถ้าโครงสร้างบิลต่างกัน แก้ `_daily()` และ `_sales_by_dish()` ใน `feedback_business.py` เท่านั้น
- แผน multi-tenant (หลายแบรนด์ในฐานเดียว) ยังไม่ทำ: โมดูลถือว่าหนึ่งฐานข้อมูล = หนึ่งแบรนด์
