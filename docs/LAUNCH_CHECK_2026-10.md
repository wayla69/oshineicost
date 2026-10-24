# Launch check — 2026-10 (RV-M, pre-launch review of the whole app)

The pilot starts on 1 พ.ย. 2569 with 5 branches: เชียงใหม่, อุบลราชธานี, อุดรธานี, โคราช1 and สุรินทร์. This review covers every page for every role, on a phone and on a desktop, plus every function end to end. It was run on 24 ต.ค. 2569 from branch `fix/prelaunch-review`, which is origin/main 6dbed66 (MA #449 and MB #450) plus this PR.

## How it was run

- **Server:** its own server on :8153, running a scratch copy of the deck28 database with auth on. The test accounts exist only in that copy:
  - one account for each of the 10 roles;
  - three branches: เชียงใหม่, อุดรธานี, and หาดใหญ่ (a new branch with no data);
  - the pilot branches อุบลราชธานี, โคราช1 and สุรินทร์;
  - two franchise owners, ระยอง and อุดรธานี.
- **The system** stayed in stock mode, except for the mode-switch flow.
- **Page walk:** Playwright and Chrome. Every page in each role's launcher, plus the phone views home, ค้นหา and ฉัน, at these sizes:
  - desktop 1280×800, light and dark;
  - phone 390×844 and 430×932, light and dark, in the page's default mode and in the other mode.
- **What each screen was measured for:**
  - console and page errors;
  - responses: 403, other 4xx and 5xx, failed requests;
  - horizontal overflow of the document and of the content area;
  - smallest drawn text;
  - touch targets under 44 px (in touch mode);
  - text contrast under 4.5:1 (a gradient is judged at every stop);
  - "กรุณา", "ระบบจะ" and "ฉบับร่าง";
  - recipe wording;
  - prose blocks;
  - card lists without an empty state;
  - clipped text.
- **Flows:** three runners drove the real UI on a phone and a desktop:
  - the branch side;
  - the centre's supply desks;
  - the analyst, management and franchise side.

  Every write was checked on screen and through the API. Every downloaded Excel file was opened in openpyxl and checked against the formula guard (`backend/xl_safe.py`).

## Page walk

The first walk ran on the code before the fixes, the second on this PR.

| Role | Pages | Screens | Overflow | Min px desktop | Min px touch | Min px fit (rest) | Console errors | 403 | Other 4xx/5xx | Small targets before → after | Low contrast before → after |
|---|---|---|---|---|---|---|---|---|---|---|---|
| management | 58 | 590 | 0 | 8.5* | 12 | 5.5 | 0 | 0 | 0 | 35 → 12† | 171 → 5‡ |
| analyst | 54 | 550 | 0 | 8.5* | 12 | 5.5 | 0 | 0 | 0 | 35 → 12† | 156 → 5‡ |
| salessupport | 18 | 190 | 0 | 8.5* | 12 | 5.5 | 0 | 0 | 0 | 12 → 4† | 52 → 5‡ |
| warehouse | 1 | 24 | 0 | 12 | 12 | 6.03 | 0 | 0 | 0 | 0 → 0 | 8 → 2‡ |
| accounting | 20 | 210 | 0 | 8.5* | 12 | 5.5 | 0 | 0 | 0 | 20 → 4† | 78 → 5‡ |
| operation | 21 | 220 | 0 | 8.5* | 12 | 5.5 | 0 | 0 | 0 | 20 → 8† | 63 → 5‡ |
| innovation | 15 | 164 | 2 → 0 | 12 | 12 | 5.5 | 0 | 0 | 0 | 12 → 8† | 46 → 2‡ |
| marketing | 12 | 130 | 0 | 8.5* | 12 | 5.5 | 0 | 0 | 0 | 16 → 12† | 25 → 5‡ |
| branch เชียงใหม่ | 5 | 44 | 0 | 12 | 12 | — | 0 | 0 | 0 | 0 | 4 → 0 |
| branch อุดรธานี | 5 | 44 | 0 | 12 | 12 | — | 0 | 0 | 0 | 0 | 13 → 0 |
| branch หาดใหญ่ (no data) | 5 | 44 | 0 | 12 | 12 | — | 0 | 0 | 0 | 0 | 1 → 0 |
| franchise ระยอง | 1 | 20 | 0 | 12 | 12 | — | 0 | 0 | 0 | 0 | 9 → 0 |
| **Total** | **215** | **2,230** | **0** | | | | **0** | **0** | **0** | | |

Every screen of the final walk also had:
- no "กรุณา", "ระบบจะ" or "ฉบับร่าง";
- no card list without its empty state;
- no recipe page reachable in stock mode. The only recipe words are the mode switch's own labels on ปฏิทินและโหมด.

\* The 8.5 px is the A4 management report (รายงานผู้บริหาร) on a desktop screen. Its print sizes are kept for the printed pack; on a phone they are lifted to 12 px.

† What is left are inline links inside a sentence (the market sources line on สรุปประจำวัน, one link in an empty state of เสียงลูกค้า). WCAG's inline exception covers them. The heat grid of ช่วงเวลาและรอบโต๊ะ has 44 px cells after this PR; its 36 px reading came from the first walk.

‡ The ones left:
- the "お" logo mark on the phone home, 4.27:1 (decorative);
- the management report's foot note in dark mode. It is fixed in this PR (the sheet's own muted ink), after the walk.

**Prose:** the prose measure flags every long Thai leaf. What it found is page content: data insight bullets, menu names, the server's findings text, chart captions and select lists. As in MB, it is not explanation text. The one real English sentence (ต้นทุน: "Go to … to enter unit costs …") is fixed.

## Flow matrix

Totals: 248 recorded checks (flow × role × device):
- branch: 18;
- centre: 39;
- office, management and franchise: 187;
- mode switch: 4.

Every check that failed was re-run after its fix and now passes. What is left are the P3 rows listed under Open P3. In the tables below, ✓ means pass on that device after the fixes of this PR, and — means not applicable.

### Branch (rvm_branch_ud / branch1 อุดรธานี, rvm_branch_cm เชียงใหม่, rvm_branch_hy หาดใหญ่)

| Flow | Phone | Desktop |
|---|---|---|
| ตัดเบิก: key in, waste with reason, autosave, บันทึกไว้ก่อน, ส่ง with its dialog, day strip; late day sent as ส่งช้า; เบิกเหมือนเมื่อวาน; เบิกเพิ่ม; Excel slip (download, fill, import, preview, send) | ✓ | ✓ |
| สั่งของ: plan, fill, reason gate, สั่งตามจำนวนที่ควรสั่งทั้งหมด, send before the cutoff, send after the cutoff with ack_late (the server refuses without it: 400 late), Excel order | ✓ (fixed: P1-1) | ✓ (fixed: P1-1) |
| รับของ: รับครบ, receive with a shortage → claim, เคลมภายหลัง with the claim list, Excel receive | ✓ | ✓ |
| นับของสิ้นเดือน in the app: zones, ✓ per zone, a submit with items missing is refused with กลับไปนับ, first count stored | ✓ | ✓ |
| นับของ by Excel | — (an office function, see ประสานงานขาย) | — |
| ส่วนต่างการใช้: tabs, reason on one line, one reason for ticked lines, แจ้งว่าบันทึกรับของครบ (month close), Excel with the formula guard | ✓ (fixed: P1-2, P2-1, P2-2) | ✓ (fixed: P1-2, P2-1, P2-2) |
| Shell: bottom bar and badges, home tiles, ต้องทำวันนี้, ค้นหา, ฉัน, ตัวอักษรใหญ่, toasts, sign-out | ✓ (fixed: P2-3, P2-4) | ✓ |
| New branch with no data (หาดใหญ่): every page, home, หน้างาน | ✓ (P3-1 open) | ✓ |

### Centre supply desks

| Flow | Role | Phone | Desktop |
|---|---|---|---|
| คิวใบสั่งซื้อ: queue, order read-only, สรุปรวม, Excel; fit row sheet; touch cards | salessupport1 | ✓ | ✓ |
| ใบตัดเบิกสาขา: reopen a sent slip (reason required); the branch edits and sends again; 3 Excel files; lock on a posted period | salessupport1, rvm_branch_sr, rvm_branch_ub | ✓ | ✓ |
| เคลมสินค้า: approve and reject (reason required); effect shown; the branch sees the decision | salessupport1 | ✓ | ✓ |
| คลัง: process an order (ส่งจริง, a short line with its reason, fill all), PO; the branch then receives it | warehouse1 | ✓ | ✓ |
| สรุปรวม Excel and คีย์บัญชีแล้ว | salessupport1 | — | ✓ |
| Month-end count by Excel: template, one item blank → refused with a way back, complete file, dry run, import, the same date again refused; the branch sees the count | salessupport1 | ✓ (fixed: P3 "ต่อไป") | ✓ |
| ใบสรุปตัดเบิก: chips, branch-month drawer, 3 Excel files, reconciliation (count vs system), post (the dialog states the lock), un-post | accounting1 | ✓ | ✓ |
| ใบตัดเบิกสาขา and ผลต่างตัดเบิกรายวัน, read only, Excel | operation1 | ✓ | ✓ |
| ตรวจสอบการสั่ง, ใช้วัตถุดิบต่อยอดขาย (filters, Excel) | management1 | ✓ | ✓ |
| การใช้งานทุกสาขา → a row opens that branch's slips | management1 | ✓ (fixed: P2-5) | ✓ (fixed: P2-5) |
| Phone shell: role bottom bar (PHONE_PLAN §4), badges equal `/api/nav/badges`, หน้าแรก, ค้นหา, ฉัน, row sheet | salessupport1, warehouse1, accounting1, operation1, management1 | ✓ (P3-2 open) | — |

### Office, management and franchise

| Flow | Role | Phone | Desktop |
|---|---|---|---|
| ต้นทุนต่อจาน: template and export, dry run (nothing saved), real import (saved), a 0-baht row asks first, a 0-baht edit asks a second time | analyst1 | ✓ (fixed: P3 "นำเข้า chip") | ✓ |
| ไฟล์ราคา submitted → ราคารายเดือน: preview (nothing saved), import, reject with a reason; the submitter sees the result | salessupport1 → analyst1 | ✓ | ✓ |
| ยอดแนะนำรายรอบ renders; ทะเบียนวัตถุดิบ edit, save, put back | analyst1 | ✓ (fixed: P2-6) | ✓ (fixed: P2-6) |
| Mode switch stock → full → stock through the page (the recipe page opens only in full); the switch is read-only or refused (403) for other roles | management1; analyst1, salessupport1, marketing1 | ✓ | ✓ |
| `scripts/branch_accounts.py` dry run (branch and `--role franchise`): plan printed, nothing created (48 users before and after), an unknown branch flagged | script | — | ✓ |
| Push and toasts: push cards hide cleanly with no push service; every write shows a result toast | all | ✓ | ✓ |
| Launcher, every tile, 3 Thai searches each, bottom bar, ฉัน (last 3 pages) | management1 (68 tiles), marketing1, innovation1 | ✓ (fixed: P2-7) | ✓ |
| ร้านของฉัน: opens directly, month switch, download; a new message → head office reads, replies, closes → the owner sees the reply and the badge clears | rvm_franchise_ry, rvm_franchise_ud, management1 | ✓ (fixed: P2-8) | ✓ (fixed: P2-8) |
| Management summary (6 tabs and branch sheet); รายงานผู้บริหาร daily, weekly and monthly, print/PDF, Excel | management1 | ✓ | ✓ |
| Excel exports (dish cost, prices, ingredient register with `=1+1` typed, ร้านของฉัน, management report, branch forms, slips, PO): formula guard | various | ✓ | ✓ |
| CSV exports (ส่วนลด and the 7 other `downloadCSV` callers): formula guard | management1 | ✓ (fixed: P2-9) | ✓ (fixed: P2-9) |

## Data and ops

- **`/api/admin/cloud-sync|backup|jobs/status`:** all three answer 200 for management (disabled, no credentials on the copy, the next runs listed) and 403 for the analyst, a branch and a franchise owner.
- **`bom_center.schema.ensure`:** idempotent.
  - On a fresh copy of the database: 237 objects before and after the first run, no change on the second.
  - On an empty database: 67 objects, no change on the second run.
- **Golden tests:** 148 pass, alone and in the 4-process split (see the fix below).
- **Backups:**
  - The cloud backup copies the whole database file (SQLite backup API).
  - The owner-data backup and the data swap keep every `bc_*` table by prefix, plus `franchise_msg_*` and `push_subscription` by name.
  - The derived `bcd_*` tables are rebuilt by design.

## Fixed in this PR

| # | Sev | Where | What was wrong | Fix |
|---|---|---|---|---|
| P1-1 | P1 | สั่งของ · Excel | A blank สั่ง box kept the suggestion out of sight. A file with 4 lines typed sent 175 lines (฿285,647), while the preview and the dialog said "4 รายการ จากไฟล์". | The template's สั่ง box starts at the suggestion, as the app's box does, and a blank box is 0. The preview chip and the dialog say "N รายการ · ฿X" of what the order will hold. `branch_forms.py`, `js/14-branch.js` |
| P1-2 | P1 | ส่วนต่าง · stock mode | The month close step "แจ้งว่าบันทึกรับของครบ" lived only in the รายเดือน tab, which stock mode does not offer, so the close could not be finished. | The สิ้นเดือน tab has the step and its undo for its period. |
| P2-1 | P2 | ส่วนต่าง · สิ้นเดือน | There were no tick boxes to give one reason to several lines. | Tick boxes plus one reason for the ticked lines. |
| P2-2 | P2 | ส่วนต่าง · สิ้นเดือน (and ใบสรุปตัดเบิก) | The list was cut at 80 rows with no note, while the head counted 86 lines still waiting for a reason. | Every line that needs a reason is listed; a cut list says "แสดง N จาก M". |
| P2-3 | P2 | Branch phone | The branch home had no way to open ค้นหา. | A magnifier in the branch home header. |
| P2-4 | P2 | ตัดเบิก, สั่งของ, นับของ | Every autosave popped a toast and wiped the "saved at" line. The rule still matched the old word บันทึกร่าง. | An autosave (บันทึกไว้แล้ว / บันทึกไว้ก่อน) is the inline note again; a pressed save still toasts. |
| P2-5 | P2 | การใช้งานทุกสาขา → ใบตัดเบิกสาขา | A row opened the slips page on ทุกสาขา. | The branch the link asked for is chosen once. |
| P2-6 | P2 | ทะเบียนวัตถุดิบ | A save showed no toast, and a refusal showed only a grey line. The "*Summary is a counter" rule came before the result routing. | Only a plain counter stays inline. |
| P2-7 | P2 | ฉัน and the lock chip | They said อ่านอย่างเดียว while a valid code was stored and saves went through. | A stored, unchecked code is checked once. |
| P2-8 | P2 | ร้านของฉัน · ติดต่อสำนักงานใหญ่ | A closed thread with an unread reply was hidden: "ใหม่ 1" over an empty list. | It stays in the default list until read. |
| P2-9 | P2 | Every CSV export | A POS reason `=1+1` was written raw. | `downloadCSV` applies the `xl_safe` rule (a leading apostrophe; numbers and a lone "-" kept). |
| P2-10 | P2 | ตัวจำลอง (simhub) | In แตะง่าย the content was 10 px wider than a 390 px phone (90 px at 430 dark). The sub-tab row kept `flex: 1` (basis 0) with nowrap labels. This was known since MA. | On a phone the sub-tab buttons take their own width and wrap. Its title card was a fixed light pastel in dark mode; it now uses the theme token, and its help is in Thai. |
| P2-11 | P2 | 12 pages in พอดีจอ (P&L, coverage, discounts, costhead, staff, benchmark, pnlbranch, profit, pricezone …) and the franchise table | The sticky first head cell was navy on the band (2:1; the franchise table's first head cell was 1.2:1). | The band colour plus white ink, only where the header is the band; flat headers keep their own fill. |
| P2-12 | P2 | Dark mode, every active chip | The period, granularity, status filter, launcher task tab and ดาวน์โหลด buttons were 4.34:1. | Gradient stops at 5.2:1 and up. The dark "money" and "feedback" group inks went from 4.49:1 to 5.0:1. |
| P2-13 | P2 | Dark mode tables and notes | <ul><li>Header second lines (.sub) were muted grey on the band (3.2:1).</li><li>A tinted outlier row's sticky name cell was navy on dark (1.6:1).</li><li>The กำไร SKU coverage note was amber-800 on a dark card (2.8:1).</li><li>Pink titles were 2.9:1.</li><li>On เป้าลดต้นทุน, two header cells (หมวด, ประเภท) read 1.03:1.</li><li>The benchmark sort caret was invisible.</li></ul> | Each follows its fill's ink. |
| P2-14 | P2 | แตะง่าย targets | Clickable table rows and cells (the สรุปประจำวัน branch rows, the heat-map branch names, the sortable P&L heads), the clickable div rows of ส่วนลด and P&L ต่อบิล, the file pickers, and lone links were under 44 px. | 44 px in touch mode; the fit frame keeps the desktop sizes. |
| P2-15 | P2 | เป้าลดต้นทุน | FC% read 859,642.7% for a buffet item with a few baht of its own revenue. | Above 999 % the cell shows "—". |
| P3 | P3 | Wording | <ul><li>English on ต้นทุน, P&L (headers, GP per bill notes), ช่วงเวลา (day names, Peak Day …) and สรุปประจำวัน (market context).</li><li>"อิงสูตร BoM" in the management report's foot in stock mode.</li></ul> | Thai, and the stock wording. |
| P3 | P3 | Month-end count by Excel (ประสานงานขาย) | After an import the form silently moved to the next branch still waiting for a count. | It says "ต่อไป: สาขา… วันที่ …" beside the fields. |
| P3 | P3 | ต้นทุนต่อจาน | After a real import the table reopened on ไม่มีในตาราง and hid the rows just saved. | It opens on นำเข้า. |
| Known | — | `test_golden[feedback_business…]` | It failed alone or in a split run, and passed only after `test_feedback_business.py` in the same process. **Not flaky:** the golden was captured after that module had created `sale_bill_item` in the session database. Alone, the endpoint saw no bill tables and answered `"branches": []`, `"days": 0`. | The fixture creates the table that the sync always creates (`backend/conftest.py`). Evidence: on origin/main, `pytest test_golden.py -k feedback_business` fails; with this fix `pytest test_golden.py` gives 148 passed, and the 4-process split is green. |
| Known | — | Browser tests | `test_departments` ×2 and `test_phone_shell::…branch_and_franchise…` failed on main. | Updated to the current app: the branch words (สั่งของ, รับของ, ส่วนต่างการใช้, นับของสิ้นเดือน), and PR-F1's franchise login, which opens ร้านของฉัน with no launcher. No assertion was weakened. |

## Open P3

| # | Where | Note |
|---|---|---|
| P3-1 | New branch, day one | A round whose cutoff passed earlier on the branch's first day is shown as today's late duty ("เลยเวลาส่ง · มีค่าปรับ"). This is the pinned design: `test_bom_center_u1`, and the 13:15 push to ประสานงานขาย names the branches that missed it. Rounds before the first day are already exempt (PL3). Exempting the first day too needs an owner decision. |
| P3-2 | Warehouse phone · ฉัน | "หน้าที่เปิดล่าสุด" can list the start-up page (หน้างาน) under a leftover title after the first tap. The recent page is recorded before the requested page shows (`js/09c-mobile.js` phOpenPage / phFitApply). |
| P3-3 | นับของ (branch) | The zone chip ✓ and the progress line update when the zone changes, not while typing. |
| P3-4 | ฉัน | ตัวอักษรใหญ่ is only on the branch page header, not in ฉัน; on a desktop the button shows but the size stays. |
| P3-5 | Phone toasts | Up to 3 toasts can stack over an open form; a stale warning can stay after a success. |
| P3-6 | Wording | <ul><li>The variance reason "เสิร์ฟเกินปริมาณตามสูตร" in stock mode (also in the SOP).</li><li>"แนะนำ N" on order rows.</li><li>"เคลม/ปรับ" and "งวด" in the เทียบของ rows and Excel.</li><li>The branch search placeholder suggests "ต้นทุน อุดรธานี".</li><li>Remaining English labels on old analytics pages (Baseline, ROI, SKUs, quartile, weekday-fair).</li><li>The P&L warning chips are sentences.</li></ul> |
| P3-7 | Badges | The ตัดเบิก badge counts open tasks, while the strip counts missing days (1 versus "ค้าง 2 วัน"). |
| P3-8 | Slip log | An inverted date range says "ช่วงวันที่ต้องไม่เกิน 92 วัน". |
| P3-9 | Order Excel | Clearing a suggested line counts as ordering less than suggested, so it needs a reason, as in the app. A branch that deletes many rows meets many reason prompts. |
| P3-10 | รายงานผู้บริหาร | 8.5 px print sizes on a desktop screen; the logo mark is 4.27:1. |
| P3-11 | ตัวจำลอง | A saved scenario (`cost_scenario`) is not in the data swap's preserved tables, so it is lost on the next swap. |

## Checks

- `py -3.12 -m pytest -o addopts=""`, 4 processes, 0 failed:
  - `669 passed, 2 skipped`
  - `594 passed, 4 skipped`
  - `830 passed, 8 skipped`
  - `853 passed, 9 skipped`
- **A second order dependency, fixed:**
  - Adding a test module shifted the 4-process split, and `test_slip_forecast::test_the_plan_endpoint_answers_source_and_fallback` then failed after `test_bom_center_calendar.py`.
  - Cause: the calendar test changed branch 1's round 1 for the rest of the session. Bisected to that one module.
  - Fix: the test now restores the branch's calendar rows.
- **Browser tests (py -3.14):** 80 passed, across `test_prelaunch_review.py`, `test_departments.py`, `test_phone_shell.py`, `test_phone_modes.py`, `test_nav_badges.py` and the two pinned-login files.
- `bash scripts/check-js.sh`: clean.
- `node scripts/check-departments.js`: clean.
- pyflakes (as CI): 0.
- Playwright e2e, the whole suite on the fixture: 22 passed.

## Go / no-go

**GO for 1 พ.ย. 2569** with the 5 pilot branches. Every function of the pilot works end to end, on a phone and on a desktop, for every role. Both P1s found by this review (the order Excel and the stock-mode month close) are fixed and re-tested. No console error, no failed request and no horizontal overflow is left on any page. What is open is P3 polish.
