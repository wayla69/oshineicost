# Phone plan — the whole app on a phone (MA, 2026-10-24)

The owner wants the phone to feel like a real mobile app: every page fits the screen, small text is acceptable, pinch-to-zoom works, and each role has a bottom bar built for its daily work. MA builds the shared layer for this. MB (page-by-page card lists) and RV-M (the role-by-role phone review) follow. The tracker row is `STOCK_FIRST_GOLIVE.md` A19.

A phone is the app's own breakpoint: 700 px wide or less (`body.ph-on`, js/09c-mobile.js). The desktop is not affected.

## 1. Two display modes

| | แตะง่าย (touch) | พอดีจอ (fit) |
|---|---|---|
| Layout | The phone layout: stacked cards, 2-column KPI grids, work tables as cards | The page's **desktop layout** in a frame 760–900 px wide, scaled to the screen width with CSS `zoom` (`transform: scale` where `zoom` is unsupported) |
| Text | Never below 12 px | Smallest text **≥ 5.5 px at rest** on a 390 px screen, **≥ 12 px after a double tap** shows a table or chart at 100 % |
| Targets | 44 px | Only the filters, period pickers, sub-tab chips and "?" stay at 44 px (counter-zoomed). The rest is at desktop size inside the frame |
| Zoom | Pinch zoom is the browser's own | The browser's own pinch, plus a **double tap on a table or a chart** to show it at 100 % (double tap again to return) |
| A table row | Its own buttons | A tap opens the **row sheet** (see 3) |

**The text-floor rule (lead's decision, 2026-10-24).** The brief asked for a 10.5 px floor after scaling. That cannot hold: a desktop layout with 12 px text in a 1,100 px frame on a 390 px screen draws at 4.3 px. The rule that replaced it, following the owner's "ตัวเล็กและมีซูมได้":

- **Frame width.** The page's natural content width, i.e. the narrowest width at which nothing overflows, with charts left out. It is clamped to 760–900 px, with 820 px when the width cannot be read.
- **Cap.** The frame is never wider than `available width × 12 / 5.5` (833 px on a 390 px phone).
- **Text inside the frame.** At least 12 px. 12 px text therefore draws at ≥ 5.5 px at rest (about 5.7–6.3 px on a 390 px screen) and at 12 px or more at 100 %.
- **Charts.** In fit, charts are the desktop charts, with no font under 12 px.

**Always at touch size.** These sit outside the scaled frame:

- the page header (title on one line, a short "ถึง 19 ต.ค." chip, ⤢ mode toggle, the tools, the search magnifier, the avatar)
- the chip rows (one horizontal row on the supply pages, with no repeated title card)
- the bottom bar

The **sticky table header and first column** stay pinned:

- **A table that fits its box** keeps the page scroll, and its header pins under the top bar.
- **A wider table** scrolls inside its own box, with the header and the first column pinned. There is never a horizontal page scroll outside the frame.

**The toggle** is the ⤢ button of the phone top bar. Its label is the current mode: พอดีจอ or แตะง่าย. The choice is remembered per page on the device (`localStorage` key `oshinei_phmode`, wrapped in try/catch).

The branch login never gets fit. Its pages are the touch layout, and its "ตัวอักษรใหญ่" pill (× 1.15) is a touch-mode option.

## 2. Defaults per page

The registry holds the default: `PAGE_PHONE` in `js/01-core.js`, keyed like `PAGE_KEYWORDS` (`"tab"` or `"tab|pane"`). A page with two views names one mode per view. `scripts/check-departments.js` fails when a page of a department or of หน้ารายงานทั้งหมด has no entry.

| Page type | Default | Pages |
|---|---|---|
| Analytics, reports, wide read-only tables | **fit** | mgtreport, report, overall, dailysales, trend, compare, more, action, checklist, gpbranch, pnlbranch, pnlbill, profit, benchmark, pnl, cost, bomcost, costhead, coverage, menuimpact, menuefficiency, branchbom, simhub, heavyeater, buffetperf, childbuffet, guest, custtype, timeflow, feedback, menu, menuactionboard, menuopportunities, menucompare, opsreport, ops, discounts, cashctl, cancelbill, staff, modifiers, pricezone, closetime, fmsg, ordering, ingpurchase, ordersetup\|pack, orderhub\|queue, claims, comp, issues, issuevar, persales, adoption, month, bomcenter\|fc, price, issue, rcheck, overview |
| Branch pages, หน้างาน, ร้านของฉัน, launcher, ฉัน, ค้นหา | **touch** | branchstock\|issue/order/recv/var/count, supplyhome, franchise, launcher, allpages, home (สรุปประจำวัน), manual |
| Editors and import forms | **touch** | settings, bomcenter\|pricefile, rec, prep, rel, conv, cal, events, zones, codereq, orderhub\|central |
| Two views | table/list **fit**, import/edit **touch** | bomcenter\|dishcost (the table / a file read and waiting to be saved), bomcenter\|ing (the list / a field changed or in use) |
| Not a page | **touch** | the ผู้บริหาร phone summary (6 tabs) and its branch sheet stay their own phone views |

Recipe pages hidden in stock mode still have a default, for full mode.

## 3. The row sheet

In fit, a tap on a table row opens the existing sheet (`#msSheet`, class `ph-rowsheet`) as a bottom sheet over a dimmed page. It shows:

- the row's status pill first
- every column as label → value: the header text from `thead`, colspans counted; a cell holding only a selection tick is skipped; fields show their values
- the row's own actions last: the buttons and links in the row, plus "เปิดรายละเอียด" when the row itself is clickable

A tap on the dim, the ← button or a swipe down closes the sheet. A button, link or field inside a row still does its own job. A double tap zooms the table instead of opening the sheet: the single tap waits 350 ms.

The sheet is generic: any `<table>` works. In touch, the card lists of 7 open the same sheet from a card's chevron.

## 4. Role bottom bars

`_PH_BARS` in `js/09c-mobile.js`. Badges are the open-task counts of `nav_badges` (`data-nb="p:<tab>:<pane>"`). หน้าแรก always carries the person's whole open list (`data-nb="t"`, the count the old งานของฉัน entry had). An entry the login may not open is dropped; a role with three entries shows three, evenly spaced.

Phone B (owner 2026-10-30): a management login has ONE bottom bar (`#phNav`) on every screen - the summary's own seven-tab `#msNav` is not shown in the phone shell. Its ยอดขาย / สาขา / เมนู open the summary's detail screens (`ms:` targets; a login without `report.management` opens the pages named in `_PH_BARS`). กำไร (ต้นทุนอาหาร) and ส่วนลด are cards of หน้าแรก. Every centre view has ONE header (`#phTop`: title or back arrow, the month and branch chips over `#msMonthSel` / `#msBranch`, ฉัน); a full-app page keeps its top bar in the same colours. A card opens its detail with `history.pushState`; the back arrow and the phone's back button return to the cards at the same scroll. "เปิดแดชบอร์ดเต็ม (เดสก์ท็อป)" is a row of ฉัน.

| Role | หน้าแรก opens | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| management | launcher + overview cards (badge: open tasks) | ยอดขาย → summary detail `sales` | สาขา → detail `branches` | เมนู → detail `menu` | ฉัน |
| salessupport | หน้างาน | คิวใบสั่งซื้อ (badge) | ใบตัดเบิกสาขา (badge) | เคลม (badge) | ฉัน |
| warehouse | launcher | คิวใบสั่งซื้อ (badge) | — | — | ฉัน |
| analyst | หน้างาน | ต้นทุนต่อจาน (badge) | ราคารายเดือน (badge) | ไฟล์ราคา (badge) | ฉัน |
| accounting | launcher | ใบสรุปตัดเบิก (badge) | P&L รายสาขา | GP รายสาขา | ฉัน |
| operation | launcher | ใบตัดเบิกสาขา (badge) | ผลต่างตัดเบิกรายวัน (badge) | ส่วนลด | ฉัน |
| marketing | launcher (department home) | ลูกค้าและบุฟเฟ่ต์ | วิเคราะห์เมนู | เสียงลูกค้า | ฉัน |
| innovation | launcher | ทะเบียนวัตถุดิบ | — | — | ฉัน |
| branch / franchise | unchanged | | | | |

Where หน้าแรก opens a page (หน้างาน), a second tap on หน้าแรก while that page is open goes to the department launcher.

**ค้นหา** is the magnifier in the header: top right beside the avatar on the home hero and in a page's top bar. The search itself is unchanged. **ฉัน** starts with "หน้าที่เปิดล่าสุด", the last three pages opened on this device, as big tiles.

## 5. Registering a new page

1. Add the page to the registry as before (DEPARTMENTS / SUPPLY_CHIPS / TAB_CLUSTERS).
2. Add its phone default to `PAGE_PHONE`: `'fit'` for analytics or a wide read-only table, `'touch'` for a form, an editor, a branch page or a page worked with a thumb. A page with a list view and an edit view names both (`{ list: 'fit', edit: 'touch' }`) and adds its view detector to `_phPageView` (js/09c-mobile.js).
3. Inside the page, keep to the app's tools: tables are `<table>` with a `thead` (the row sheet reads its header text), and filters are `select` / `input` / `.subtab-btn` (kept at touch size in fit). Use Tailwind `md:` columns for the desktop layout; the frame applies them.
4. A table people read every day on a phone gets a card list: add it to `PAGE_CARDS` (js/01-core.js) with its name column and its three key columns (see 7).
5. Run `node scripts/check-departments.js` (it fails without a phone default), `bash scripts/check-js.sh`, `backend/test_phone_modes.py`, `backend/test_phone_content.py`, and the e2e `tests/e2e/phone-modes.spec.js` and `tests/e2e/phone-cards.spec.js`.

## 6. Launch checklist for phones (5 pilot branches, 1 พ.ย. 2569)

- [ ] Every role signs in on a real phone (Android Chrome and iPhone Safari) and finds its bottom bar as in 4, with badges matching หน้างาน.
- [ ] Each role's daily page opens in its default mode. ⤢ switches the mode and the choice survives a reload.
- [ ] Fit pages: no sideways page scroll; pinch zoom works; double tap on a table shows it at 100 %; a row opens its sheet and the sheet closes on the dim, ← and a swipe down.
- [ ] Touch pages: text ≥ 12 px and targets ≥ 44 px (the branch pages with ตัวอักษรใหญ่ on and off).
- [ ] Touch card lists (7.2): each card shows its pill, name and three figures; the sort and filter chips work; a card opens its sheet; an empty list says what to do.
- [ ] Light and dark mode on both phones; contrast ≥ 4.5:1 for text.
- [ ] The search magnifier finds a page by its Thai name; ฉัน lists the last three pages.
- [ ] The installed app (home-screen icon) opens on the launcher and keeps the morning notification.
- [ ] No console errors and no 403 on any page of any role (the MA walk: `scratchpad\ma\<role>\`).

## 7. Card lists, help behind "?", headers and charts (MB, 2026-10-24)

### 7.1 The card-list rule

In แตะง่าย (touch), the tables people read every day are drawn as **one card per row**. In พอดีจอ (fit) and on a desktop, the table stays a table.

- **The renderer** is `phCardList(spec)` in `js/09c-mobile.js` ("CARD LISTS"), and it is the only one. `_phCardSpec` reads the table as the page drew it: its header texts, its body rows and the columns that `PAGE_CARDS` names.
- **A card** is **two fixed lines** (since 2026-10-31, §10):
  - line 1: the name (one line, ellipsis) and ONE key figure - the figure the list is sorted by, else the first one it has (bold, right);
  - line 2: the status chip (at most 6 characters, `uiChipShort`; colour = meaning: the row's own chip gives its tone, plain text takes one from
    `tones`), one grey line (code, branch, date as d/m, then the other figures as "label value") and the sparkline as a thin strip;
  - a chevron. A tap opens the MA row sheet with every column, plus the row's own buttons.
- **The chips on top:**
  - **sort chips:** the name and the three key figures. A second tap flips the order. With no chip pressed, the list keeps the table's own order.
  - **filter chips:** the pill's values, with counts. They show when there are 2–10 values.
  - **finished rows** are hidden by default behind the shared state filter (`uiStateFilter`, ยังไม่เสร็จ / เสร็จแล้ว / ทั้งหมด). A page that already filters its own list (the supply queues) keeps its filter above the cards.
- **A long list** shows 50 cards, then "แสดงอีก".
- **An empty list** shows one line and the action to take ("เลือกช่วงอื่น" goes to the page's own period picker). It never shows a blank area, or zeros that look like data.
- **The table stays in the page,** hidden while its cards show. The sheet reads its rows, and the card's tick and actions click the row's own controls. The cards follow every redraw of the table, sub-tab and filter changes included.
- **Exceptions:**
  - A writer's editable table (`fields: 'table'`: ทะเบียนวัตถุดิบ for the analyst, the cost fixer of ความครอบคลุมต้นทุน) stays a table, because cards would hide its fields.
  - On the centre phone shell, the queue and the adoption list draw their table for the shared cards (`_ohbSharedCards`). The branch login keeps its own cards.
- **Row-number columns** (`#`, ลำดับ, อันดับ) are hidden on a phone in both modes. The sheet leaves them out. In fit, the sticky first column moves to the next column.

To register a table, add it to `PAGE_CARDS` (js/01-core.js, keyed like `PAGE_PHONE`):

```
{ sel, title, sub, subL, keyCols: [3 headers], labels, pill, tones, done, rows, spark | sparkCell, fields, all, empty }
```

`backend/test_phone_content.py` fails when a page of the list below has no entry, or when a key column is not a header the page prints.

### 7.2 Key columns per page

| Page | Key | Table | Name · grey line | Key figures | Pill | Finished = hidden |
|---|---|---|---|---|---|---|
| วิเคราะห์เมนู | menu | `#orderedTable` · `#costHeadTable` · the menu-engineering table | เมนู · SKU / หมวด | จำนวนขาย, % ของยอดรวม, เปลี่ยน % · ต้นทุน/หัว, % ของต้นทุน, เปลี่ยน % · ยอดขาย, กำไร, % กำไร | ABC · กลุ่ม | — |
| ปรับเมนู | menuactionboard | — (own cards) | | | | |
| โอกาสเมนู | menuopportunities | `#oppGaps` · `#oppWhitespace` · `#oppRevive` | เมนู · สาขา, หมวด | สาขานี้, ทั้งเครือ, โอกาสเพิ่ม (ชิ้น) · เมนู, รายได้, รายได้/เมนู | คำแนะนำ | — |
| กำไร SKU | profit | `#profitBandTable` | ช่วง · บิล, ลูกค้า | รายได้, กำไร, อัตรากำไร | — | — |
| เทียบสาขา | benchmark | `#benchmarkTable` | สาขา | รายได้, ยอดบิลเฉลี่ย, คะแนน | — | — |
| GP รายสาขา | gpbranch | `#gpbTable` (branch rows) | สาขา · ต้นทุนอาหาร % | รายได้, GP, GP % | สถานะ (ในเป้า / ใกล้ขอบ / นอกเป้า) | — |
| P&L รายสาขา | pnlbranch | `#pnlbTable` | สาขา · GP%, OP% | รายได้, กำไรขั้นต้น, กำไรดำเนินงาน | — | — |
| ยอดขายรายวัน | dailysales | the branch table (`#dsBody`) | สาขา · เทียบเดือนก่อน, เทียบปีก่อน | ยอดขาย, ลูกค้า, ยอด/หัว | — | — |
| ยอดขายรายวัน · เมนูพุ่งผิดปกติ | dailysales | `#spikeTable` | เมนู · สาขา | วันนี้ (จาน) / ช่วงนี้ (จาน), เฉลี่ย 21 วัน / ปกติ (จาน), สูงกว่า | — | — |
| ส่วนลด | discounts | cashiers (`#discCashierBody`) · reasons | แคชเชียร์ · สาขา (the branch row is a group card) | ส่วนลดรวม, % ยอดขาย, อัตรา | — | — |
| พนักงาน | staff | `#staffTable` | แคชเชียร์ · สาขา | รายได้, ยอดบิลเฉลี่ย, % ส่วนลด | — | — |
| บิลยกเลิก | cancelbill | branches · reasons | สาขา · เหตุผล | บิลยกเลิก, มูลค่า · บิล, %, มูลค่า | — | — |
| เงินสดขาด/เกิน | cashctl | branches (`#ccBody`) · worst days (`#ccWorst`) | สาขา · วันที่ | ขาด/เกิน, วันที่ขาด, % ของยอด · ขาด/เกิน, ยอดขาย, ลูกค้า | — | — |
| ต้นทุนต่อหัว | costhead | `#chBody` | สาขา · vs เฉลี่ย | ต้นทุน/หัว, ต้นทุน%, GP/หัว | — | — |
| ความครอบคลุมต้นทุน | coverage | branches · the review list · the fixer (a table for a writer) | สาขา · SKU ค้าง / เมนู | รายได้, ไม่มีต้นทุน, % ไม่มี · ต้นทุน/ที่, จานเดือนนี้, ต้นทุนรวม/เดือน | สถานะ (review) | ยืนยันแล้ว |
| เป้าลดต้นทุน | menuefficiency | `#mce2Body` | เมนู · หมวด, แหล่ง | ต้นทุนรวม, %ต้นทุน, ฿/จาน | ABC | — |
| คิวใบสั่งซื้อ | orderhub\|queue | one table per delivery day | ใบสั่งซื้อ · ตัดรอบ | มูลค่า, รายการ (+ the key-in tick) | สถานะ chips | the page's own filter |
| เคลมสินค้า | orderhub\|claims | cancelled receipts (the claim list is already cards) | สาขา · ใบรับของ, เหตุผล | รายการ | — | the page's own filter |
| ใบตัดเบิกสาขา | orderhub\|issues | the per-branch summary (the slip log is already rows) | สาขา · ส่งช้า, ยังไม่ส่ง | ตรงวัน %, มีใบ %, ไม่มีใบ | — | the page's own filter |
| ผลต่างตัดเบิกรายวัน | orderhub\|issuevar | the daily table of a branch | วันที่ | รวมเบิก ฿, ปกติ ฿, เทียบ | ใบตัดเบิก | — |
| ตรวจสอบการสั่ง | orderhub\|comp | `#ohbComp` | สาขา · ตัดรอบ | สาขายืนยัน ฿, ส่งจริง ฿, รับจริง ฿ | สถานะ | the page's own filter |
| การใช้งานทุกสาขา | orderhub\|adoption | `#ohbAdList` | สาขา · ใช้งานล่าสุด | ตัดเบิกตรงวัน, สั่งตรงเวลา, บันทึกรับของ | สถานะ (เขียว / เหลือง / แดง) | the page's own filter |
| ใช้วัตถุดิบต่อยอดขาย | orderhub\|persales | branches · the items of a branch | สาขา / วัตถุดิบ · หน่วย | ยอดขาย ฿, วัตถุดิบต่อ ฿100,000, เทียบค่ากลาง · ต่อ ฿100,000, เปลี่ยน, มูลค่า ฿ | the row's chips | — |
| ยอดแนะนำรายรอบ | bomcenter\|fc | `#bmcFcTable` | วัตถุดิบ · รหัส | แนะนำสั่ง, มูลค่า ฿, ใช้/วัน (+ its trend line) | กลุ่ม (A/B/C) | — |
| ราคารายเดือน | bomcenter\|price | `#bmcPriceBody` | วัตถุดิบ · รหัส, หน่วยฐาน | ราคา/หน่วยฐาน, อายุ (เดือน), สาขาที่ซื้อ | แหล่ง | — |
| ต้นทุนต่อจาน | bomcenter\|dishcost | `#bmcDcList` | เมนู · รหัส POS | ต้นทุนต่อจาน, เปลี่ยน, จานที่ขาย | ที่มา | the page's own filter |
| ทะเบียนวัตถุดิบ | bomcenter\|ing | `#bmcIngBody` (read-only logins; a writer keeps the table) | วัตถุดิบ · รหัส, หน่วยฐาน | ราคา/หน่วยฐาน, ราคา/หน่วยรับ, ใช้ในเมนู | สถานะ | — |
| ใบสรุปตัดเบิก | bomcenter\|issue | `#bmcIssList` | สาขา · งวด | มูลค่าตัดเบิก, ไม่มีราคา | สถานะ | the page's own filter |
| เสียงลูกค้า | feedback | all items (`#fbItems`) · branches (`#fbBranches`); the reply queue is already cards | สาขา · วันที่, ช่องทาง, ข้อความ | ดาว, ตอบกลับ · เสียง, ดาว, ลบ % | ความรู้สึก | — |
| ข้อความจากเจ้าของสาขา | fmsg | — (own cards) | | | | |
| High Value Product Monitor | hvmonitor | `#hvmTable` (one group) · `#hvmMatrix` (ทั้งหมด) | สาขา | ต้นทุน/หัว, จาน/หัว, ต้นทุน/จาน · แซลมอน, ทูน่า, ต้นทุนอาหาร/หัว | — | — |
| วิเคราะห์ระดับราคา | pricetier | `#ptTierTbl` (one row per adult buffet price) · `#ptTop30` (dishes by cost per buffet head) | ระดับราคา · เมนู | ต้นทุน/หัวบุฟเฟ่ต์ (Δ), GP/หัว, ต้นทุนขาย % · ต้นทุน/หัวบุฟเฟ่ต์, ต้นทุน/หน่วย | — | — |

**Left as they are, with the reason:**

- **ปรับเมนู:** four bucket boards of menu cards. There is no table.
- **ข้อความจากเจ้าของสาขา:** one row per conversation, with the shared state filter. There is no table.
- **The claim list of เคลมสินค้า, the slip log of ใบตัดเบิกสาขา and the reply queue of เสียงลูกค้า:** each is already one card or row per item, with its own filter.
- **ทะเบียนวัตถุดิบ for a writer, and the cost fixer of ความครอบคลุมต้นทุน:** editable tables. A card would hide the fields.
- **Wide reference tables** (heatmaps, the P&L statement, the management report): these are read in fit.

### 7.3 Help, headers, charts

- **Prose → "?":** every page keeps at most three explanatory blocks outside a "?" (`backend/test_less_text.py` now covers every fragment and slice). Two files are the explanation itself and are exempt:
  - the user manual;
  - the printed management report, whose notes are the footnotes of the A4 pack.
- **Headers:**
  - the page title stays on one line, and "ข้อมูลถึง …" is the short chip of the top bar (MA);
  - row numbers are hidden;
  - the dates the app prints on a card are d/m. Native date inputs stay native.
- **Charts in touch:**
  - one-series period bars are B1;
  - a category axis with more than six labels names its first, middle and last label;
  - labels follow the VZ2 collision rule.
- **Charts in fit:** drawn for the size they are shown at, i.e. canvas pixels = drawn width × device pixel ratio. That holds at rest and again after a double tap to 100 %, so they are never a stretched bitmap.

## 8. One daily baseline (owner 2026-10-31, UI review no. 12)

Every DAILY comparison compares the day with **ปกติ** = the median of the same weekday over the 8 weeks before
(`rules.daily_baseline` / `baseline_from_days`; a weekday without bills is skipped; under 2 weeks = no verdict, pill
"ยังไม่มีฐาน"; 2..7 weeks = "ปกติจาก n สัปดาห์"; full = "เทียบปกติ (ค่ากลาง 8 สัปดาห์)" on the desktop, "เทียบปกติ" on
the phone). "ต่ำกว่าปกติ" = 8 % or more under it (`rules.BELOW_NORMAL_PCT`), counted once from the scorecard
(`below_normal`, `below_count`). On it: the phone hero and the เมื่อวาน detail, `/api/home/brief`
(`revenue_vs_normal_pct`), `/api/home/branch-scorecard` (branches + `chain`), the Daily Brief tiles and table, the
management report's daily headline and league (`headline.basis = "normal"`; the menu, daypart, payment and GP-bridge
sections keep the same weekday last week, `prev_label`), ร้านของฉัน · สรุปประจำวัน and its daily table, the morning push
and the drop alert. Week / month / year comparisons keep the period pattern (เทียบช่วงก่อน · เทียบปีก่อน), and the
period pages' **วัน** view (ยอดขายรายวัน) keeps it too.

## 9. A department's own หน้าแรก (owner 2026-10-31, UI review items 4 + 6)

The chain sales hero (เมื่อวาน vs ปกติ) stays only for a login with `report.management` and for operation. Every other HQ department -
warehouse, salessupport, accounting, analyst, innovation, marketing - gets a **department hero** instead (`_phDeptHome`, js/09c-mobile.js):
one headline number, one line, one button that opens the page of the work. Branch and franchise homes are unchanged.

* **Data:** `hero` of `GET /api/nav/badges` (`backend/bom_center/dept_hero.py`), built from the same open list as the red badges and
  หน้างาน (`nav_badges.mine_open`) - no new endpoint, no call of the shell's own (`_NB.hero`, redrawn by `phDeptHeroApply` when the badges
  refresh). Marketing (no หน้างาน) reads the เสียงลูกค้า queue by the page's own rule (`feedback.reply_counts`).
* **Per role:** คลัง = ใบที่ต้องยืนยันยอดส่ง + time left to 14:00 (or ยืนยันครบ / วันนี้ไม่มีรอบ + the next cutoff) → คิวใบสั่งซื้อ ·
  ประสานงานขาย = ใบตัดเบิกยังไม่ส่ง / เคลมรอพิจารณา / ใบสั่งซื้อยังไม่คีย์บัญชี, line = ใบสั่งซื้อในคิว + รอบส่งที่เลื่อนวันนี้ ·
  บัญชี = ใบสรุปตัดเบิกพร้อมบันทึก n สาขา, line = the close step + รอสาขา ·
  ฝ่ายวิเคราะห์ = เมนูยังไม่มีต้นทุนต่อจาน / ไฟล์ราคารอตรวจ / ขนาดบรรจุรอตรวจ / … · ครัว = its recipe tasks (full mode), line = the menus without
  a dish cost → ต้นทุนต่อจาน · การตลาด = เสียงลูกค้ารอตอบ, line = past the reply time → เสียงลูกค้า.
  Open work that is none of the named figures shows as "งานของฉัน n งาน". Nothing open → "ไม่มีงานค้าง" (green) + the next thing.
* **Tiles:** the department's pages, work first; ONE chip each - red = overdue, amber = waiting (own task count = the page badge, or a
  figure waiting on another department), green ✓ = the department's duties on that page are done. Pages with no status fold under
  "หน้าอื่น (n)". The other departments the role may open (แผนกอื่น) come last, and not at all for a role with only its own.

## 10. Phone review set 2 (owner 2026-10-31, UI review items 3, 6, 7, 8, 11)

Five rules, each with ONE shared piece in `js/01-core.js` (or `js/09c-mobile.js`) that the pages go through instead of their own markup.
Pinned by `backend/test_mobile_review_set2.py`.

1. **A list row is two fixed lines (item 3).** Line 1 = the name (one line, ellipsis) + the ONE key number (bold, right, tabular);
   line 2 = a status chip of at most 6 characters that never wraps + one short fact (ellipsis) + an optional thin bar; everything else is
   behind a tap (the row expands, or opens the branch sheet / the row sheet). Shared: `uiRow2(o)` + `uiChip` / `uiChipShort` (the app's long
   status words have a short form: ไม่มีในตาราง (ใช้ต้นทุนเดิม) → ใช้เดิม, คลังจัดส่งแล้ว → ส่งแล้ว …; the full words stay in the title);
   `msRow2` is now `uiRow2`. Lists on it: ต้องทำ / ตรวจวันนี้, เมนูที่ดันต้นทุน (`msCostRisersHtml`: cause chip ราคาขึ้น / ตักเพิ่ม / ทั้งสอง),
   สาขา (`msRenderBranches`: the first 10, then ดูทั้งหมด), ขายดี (`msTopMenuHtml`), and every `PAGE_CARDS` table (`phCardList`: ต้นทุนต่อจาน,
   คิวใบสั่งซื้อ, the DV2 rank tables, …).
2. **A department page's tiles carry their status, work first (item 6).** ผู้บริหาร, ปฏิบัติการ, operation's own home grid and the
   แผนก grid of the owner: tile = icon + name + ONE value or chip coloured by meaning - ยอดขายรายวัน = the chain's yesterday against its
   normal (the hero's own pill rule), สรุปประจำวัน = สาขาต่ำกว่าปกติ, ส่วนลด = yesterday's chain discount against the 2.5 % ceiling, ต้นทุน =
   food cost to date against the 58 % target, GP รายสาขา = 100 − food cost, a page with open work = its badge count. Pages with nothing to
   show fold under "หน้าอื่น (n)"; a department tile carries how many of its pages need a look (or ✓). Values come only from
   `GET /api/home/brief` + `GET /api/home/branch-scorecard`, the two answers the sales hero already loads (`phTileDataLoad`, both
   `ttl_cache` on the server) and the nav badges - no new endpoint. One code path: `_phTileState` / `_phDtHtml` (#528) + `_phSalesVals`.
3. **One empty state per page; skeletons while loading (item 7).** A page whose data is entirely empty shows ONE card (`uiEmpty`: icon, one
   line of why, one button to the fix or to the manual's task, `uiManualGo`); an empty sub-section is hidden. Applied to เสียงลูกค้า (every
   data section hidden), ใช้วัตถุดิบต่อยอดขาย (no 28 rows of zeros), ทะเบียนวัตถุดิบ, คิวใบสั่งซื้อ (no empty filter strip), ใบตัดเบิกสาขา,
   the branch ตัดเบิก (no zone bar, no send bar, no empty history) and รับของ (one card that opens บันทึกของที่รับเอง). A table that renders
   `uiEmpty` in its empty row shows the same card on the phone (`_phCardSpec` keeps it). While loading: `uiSkel(n)` grey rows, and every
   remaining "กำลังโหลด…" line on a phone is drawn as a grey bar (`phTextFloor` marks it `.ui-skel-line`; the words stay for a screen reader).
4. **Filters are one row (item 8).** On a phone a filter row is ONE row of chips (`.ui-fbar`, scrolled sideways; ช่วงเวลา · สาขา · สถานะ each
   opening its native picker) and its downloads / print / file actions sit under a "⋯" at the row's end (`.ui-more`, inline on a desktop).
   Static on ใบตัดเบิกสาขา (`#ohbIsMore`; the tab's month joins the row) and ต้นทุนต่อจาน (`#bmcDcMore`); every other card filter row with a
   labelled select / date is compacted on open (`uiCompactFilters`, the same elements moved - ids and handlers stay). หน้างาน: เดือน · สาขา
   in one row, the page name and the date are the header's; the state filter (`uiStateFilter`) is one row of 36 px tabs; the all-branches
   line sits under งานของฉัน.
5. **Charts on a phone are at most 220 px; tiny data is a number (item 11).** `_phoneChartTune` caps the plot box at `PH_CHART_MAX` = 220 px
   (it used to grow charts to 320 px). `_phChartSwap` (in `mkChart`) draws no chart where one would not read: fewer than 3 points → one big
   number + one word (เพิ่มขึ้น / ลดลง / เท่าเดิม, or ในเป้า / เกินเป้า against a series named เป้า); doughnut / pie, scatter / bubble and a bar
   chart of more than 10 categories → the 5 largest as bars (by baht where the points carry it) + "ดูทั้งหมด (N)". `config.phKeep` keeps a
   chart; `config.phRank` / `config.phGoodUp` name the ranking or the direction. One chart per question: สรุปประจำวัน keeps the month's pace
   and folds ชั่วโมงพีค + เทรนด์ 7 วัน (`uiPhoneFold`). Chart colours stay hex; the glance plugin and `B1_PAL` are unchanged.

## 11. ร้านของฉัน แบบ B - the franchise owner's cards (owner 2026-10-31, "เริ่มเลย")

The management phone's card-first shell (#524 / #529 / #531), scaled to ONE branch, for role `franchise` only. One code path for the
phone and the desktop (2 card columns on a phone, 3 at > 700 px); nothing of another role's screens changes. Pinned by
`backend/test_franchise_b.py`.

1. **One call, one findings list.** `GET /api/franchise/overview?month&kind&pick` returns the page's three payloads (summary + insight +
   เรื่องที่ควรดู, run side by side) and `findings` (`routers/franchise.py franchise_findings`, pure, no query): discount over the ceiling,
   voids over 0.5 / 1 %, discounted reprints (the report's `leak.reprinted`), cash short, food cost over the ONE target, a high-value group
   ≥ 10 % per customer over เฉลี่ยทุกสาขา, days ≤ `BELOW_NORMAL_PCT` against their normal, reviews unanswered / negative. Each carries
   `go: {d, el}`. The read is covered by the existing `GET /api/franchise` rule (`franchise.view`); no write rule was added. สรุปประจำวัน
   keeps `/api/franchise/daily`; วัตถุดิบจากส่วนกลาง still loads when it is first on screen.
2. **Cards → detail in place.** `_frcCardsHtml` (pure) draws `.ph-ov-card`s - เมื่อวาน (wide: revenue, the เทียบปกติ pill, customers /
   bills, one cause line) · เดือนนี้ · เป้า (% + ต้องขายวันละ) · ต้นทุนอาหาร (vs 58 %) · ควรดู (red bar when any ต้องแก้) · การควบคุม
   (discount · voids · reprints) · เมนู (top dish) · เสียงลูกค้า (รอตอบ) · ติดต่อสำนักงานใหญ่ (unread); a card with nothing to show is left
   out. Every section of the old long page is a `.frc-sec` whose `data-d` names the detail(s) it sits on; `#frcDetail.d-<name>` shows them
   (CSS in index.html). `frcOpen` pushes `{frd}`, the head's ‹ / the phone's back (popstate) return to the cards where they were left;
   หน้าแรก on the bottom bar does the same (`frcHome`, called from `phOpenFranchise`).
3. **Rows that go.** ควรดู rows are `uiRow2` + `onGo` → `frcGo(d, el)` opens the detail and `uiFlash`es the line (ids `frcCtlDisc /
   frcCtlVoids / frcCtlReprint / frcCtlCash`, `frcGp`, `frcCostly-<slug>`, `frcD-<date>`, `frcVoice`); ▾ reads the sentence. Top menus,
   ของแพง, เมนูที่ลูกค้าสั่งเพิ่มขึ้น and the money lists of การควบคุม are `uiRow2`; empty sub-sections are hidden, an empty month is one
   `uiEmpty`, loading is `uiSkel`. Second-question charts fold (แนวโน้ม 12 เดือน + 6 สัปดาห์; สัดส่วนการขาย + ช่วงเวลาขายดี).
4. **ฉัน.** `#frcMe` (รับแจ้งเตือนทุกเช้า + ดาวน์โหลดและพิมพ์) moves into the phone's ฉัน view before ออกจากระบบ (under the cards on a
   desktop); the generic morning-brief card is hidden there for this role. พิมพ์ prints every section on A4 (no cards).
5. **Guard rails kept.** 17-franchise.js calls only eager helpers (01-core / 09c-mobile) or 18-launcher (`test_role_code_split.py`), never
   the management phone's detail machinery or `_msRpt`. The owner's write paths (messages to head office, the push device endpoints) are
   unchanged. The day card keeps no day-YoY; VAT is never revenue.
