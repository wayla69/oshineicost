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

The sheet is generic: any `<table>` works. A page may later register a nicer formatter; that is MB's job.

## 4. Role bottom bars

`_PH_BARS` in `js/09c-mobile.js`. Badges are the open-task counts of `nav_badges` (`data-nb="p:<tab>:<pane>"`). หน้าแรก always carries the person's whole open list (`data-nb="t"`, the count the old งานของฉัน entry had). An entry the login may not open is dropped; a role with three entries shows three, evenly spaced.

| Role | หน้าแรก opens | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| management | launcher (badge: open tasks) | ยอดขาย → dailysales | กำไร → gpbranch | สาขา → overall | ฉัน |
| salessupport | หน้างาน | คิวใบสั่งซื้อ (badge) | ใบตัดเบิกสาขา (badge) | เคลม (badge) | ฉัน |
| warehouse | launcher | คิวใบสั่งซื้อ (badge) | — | — | ฉัน |
| analyst | หน้างาน | ต้นทุนต่อจาน (badge) | ราคารายเดือน (badge) | ไฟล์ราคา (badge) | ฉัน |
| accounting | launcher | ใบสรุปตัดเบิก (badge) | P&L รายสาขา | GP รายสาขา | ฉัน |
| operation | launcher | ใบตัดเบิกสาขา (badge) | ผลต่างตัดเบิกรายวัน (badge) | ส่วนลด | ฉัน |
| marketing | สรุปประจำวัน | ลูกค้าและบุฟเฟ่ต์ | วิเคราะห์เมนู | เสียงลูกค้า | ฉัน |
| innovation | launcher | ทะเบียนวัตถุดิบ | — | — | ฉัน |
| branch / franchise | unchanged | | | | |

Where หน้าแรก opens a page (หน้างาน, สรุปประจำวัน), a second tap on หน้าแรก while that page is open goes to the department launcher.

**ค้นหา** is the magnifier in the header: top right beside the avatar on the home hero and in a page's top bar. The search itself is unchanged. **ฉัน** starts with "หน้าที่เปิดล่าสุด", the last three pages opened on this device, as big tiles.

## 5. Registering a new page

1. Add the page to the registry as before (DEPARTMENTS / SUPPLY_CHIPS / TAB_CLUSTERS).
2. Add its phone default to `PAGE_PHONE`: `'fit'` for analytics or a wide read-only table, `'touch'` for a form, an editor, a branch page or a page worked with a thumb. A page with a list view and an edit view names both (`{ list: 'fit', edit: 'touch' }`) and adds its view detector to `_phPageView` (js/09c-mobile.js).
3. Inside the page, keep to the app's tools: tables are `<table>` with a `thead` (the row sheet reads its header text), and filters are `select` / `input` / `.subtab-btn` (kept at touch size in fit). Use Tailwind `md:` columns for the desktop layout; the frame applies them.
4. For MB later: name the key columns (the 2–3 values a card list shows first) next to the page's renderer, so the touch layout can draw cards instead of a scrolled table.
5. Run `node scripts/check-departments.js` (it fails without a phone default), `bash scripts/check-js.sh`, `backend/test_phone_modes.py`, and the e2e `tests/e2e/phone-modes.spec.js`.

## 6. Launch checklist for phones (5 pilot branches, 1 พ.ย. 2569)

- [ ] Every role signs in on a real phone (Android Chrome and iPhone Safari) and finds its bottom bar as in 4, with badges matching หน้างาน.
- [ ] Each role's daily page opens in its default mode. ⤢ switches the mode and the choice survives a reload.
- [ ] Fit pages: no sideways page scroll; pinch zoom works; double tap on a table shows it at 100 %; a row opens its sheet and the sheet closes on the dim, ← and a swipe down.
- [ ] Touch pages: text ≥ 12 px and targets ≥ 44 px (the branch pages with ตัวอักษรใหญ่ on and off).
- [ ] Light and dark mode on both phones; contrast ≥ 4.5:1 for text.
- [ ] The search magnifier finds a page by its Thai name; ฉัน lists the last three pages.
- [ ] The installed app (home-screen icon) opens on the launcher and keeps the morning notification.
- [ ] No console errors and no 403 on any page of any role (the MA walk: `scratchpad\ma\<role>\`).
