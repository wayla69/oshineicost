# กระทบยอด Oshinei — UI/UX Overhaul Spec (v3 "Inbox")

Designed 2026-06-13 via a judge-panel of 6 design directions → synthesized.
Top directions (8/10): triage-inbox (backbone), cross-channel-specialist, cashier-persona.
**Engine unchanged** (matcher.py / reconcile_v2.py) — this is a frontend re-presentation of
fields that already exist, + 2 thin backend payload enrichments.

## 1. The big idea
The month is an INBOX, not a spreadsheet. Each branch-month = a short stack of plain-Thai
"เรื่องที่ต้องทำ" cards (one card per REAL problem, one sentence + one button + a suggested
cause). Reconciled channels hide in a green drawer; timing (บัตร T+1/T+2, Grab/LM payout
ข้ามเดือน, เงินสดวันสุดท้ายฝากเดือนหน้า) is structurally barred from becoming a card. Work the
list to zero, then "ส่งให้ผู้จัดการตรวจ". The grid/day-calendar/จับคู่ละเอียด move behind a
"มุมมองนักบัญชี ↗" link (classic.html) for rare manual cases.

## 2. หน้าแรก (replaces the variance grid)
Header (reuse) + hero line "เดือน พฤษภาคม 2569 · เหลือ 23 เรื่อง ก่อนปิดได้ทั้งเครือ" + one slim
progress bar. A green "🔀 เจอเงินที่น่าจะอยู่ผิดช่อง N คู่ (รวม ฿X)" banner when reclass pairs
exist (hidden if zero). Branch LIST (not table) sorted by open count; a branch whose only open
items are wrong-channel pairs must read "เหลือ 1 เรื่อง: ยอดอยู่ผิดช่อง" (needs reclass_count in
the overview payload, else it looks falsely clean). Collapsed "สาขาที่เรียบร้อยแล้ว (N) ▾" at
bottom. Manager chip "รอฉันตรวจ (N)".

## 3. Fixing a wrong-channel payment (the #1 fix) — 2 taps
The engine already finds the pair (reclass_suggestions = ready-to-POST /api/manual). Today it's
a BURIED green card with the opaque label "POS QR ↔ เอกสาร CASH". Promote it to the TOP of the
branch inbox and rewrite as a STORY:

  ⇄ เงินอาจถูกบันทึกผิดช่อง
  วันที่ 25 — เงินสด เกินมา ฿1,705 แต่ เงินโอน ขาดไป ฿1,705 เท่ากันพอดี
  น่าจะเป็นเงินก้อนเดียวกันที่ลูกค้าโอนมา แต่กดเป็นเงินสดในเครื่อง
  [ ในเครื่อง POS: เงินสด +1,705 ]  [ เข้าบัญชีจริง: เงินโอน 1,705 (กรุงไทย·25) ]
  ตรงกันพอดี — มั่นใจสูง
  [ ✅ ใช่ เป็นเงินก้อนเดียวกัน ]  [ 🔍 ขอดูรายการ ]   ไม่ใช่

Tap "ใช่..." → existing applyReclass() → POST /api/manual (auto-note). Both lanes go green,
เหลือ 3→2. Total = 2 taps, no "channel/reclassify/checkbox/bank picker".
- Confidence gate: a `med` cross-bank cash pair (amount+day only = coincidence risk) expands an
  inline confirm (side-by-side + a preselected reason chip "แคชเชียร์กดผิด" → "ยืนยันย้าย").
  High-confidence skips it. Friction only where warranted.
- No auto-pair (split / >1 day apart): no card; the variance card's "🔍 ขอดูรายการ" opens the
  day evidence sheet showing both sides with "...น่าจะคู่กัน" + tick-to-confirm. จับคู่ละเอียด is
  never the front door.

## 4. Killing daily-vs-monthly noise
Rule: a card exists ONLY for a month-level `diff`. reconcile_v2 already separates
pending/waiting_doc/awaiting_payout (NOT errors) from diff. Hide by default: the 1–31 day
calendar (into the evidence sheet), the 7-row channel table (into the green drawer + นักบัญชี),
per-day red for CC/Grab/LM/cash that ties monthly. Show: a collapsed timing drawer
"⏳ รอเงินเข้าตามรอบ — ไม่ต้องทำอะไร (N) ▾" (never red), a ≤฿100 coin-residue rollup card
("ต่างเล็กน้อยรวม ฿142 จาก 3 ช่องทาง — กดรับทราบทั้งหมดได้เลย"), and a green
"✓ เรียบร้อยแล้ว — N ช่องทางตรงหมด ▾" drawer. Result: 23 red cells → ~3 sentences.

## 5. Plain-language glossary (steal into the label maps + card copy)
| Current term | New plain-Thai |
|---|---|
| ext_only | มีเงินเข้าบัญชี แต่ไม่มียอดขายในระบบ |
| settlement / สรุปยอดบัตร | ยอดที่ค่ายบัตรสรุปว่าจะโอนให้ |
| two-stage | เงินเดินทาง 2 จังหวะ: รูด → เข้าบัญชี |
| pending / awaiting | กำลังโอน — ปกติ ไม่ใช่ปัญหา |
| waiting_doc | ยังไม่ได้อัปโหลดเอกสาร |
| diff / ±1,250 | เงินเข้าจริงน้อยกว่า POS ฿1,250 / เกินมา ฿1,250 (ทิศทางเป็นคำ ไม่ใช่เครื่องหมาย) |
| no_source | ช่องนี้ไม่ต้องตรวจ (ตรงกับผู้ออกคูปอง) |
| setup / unmapped | รหัสรับเงินใหม่ — ยังไม่รู้ว่าเข้าธนาคารไหน |
| reclassify / จับคู่ | ย้ายให้ถูกช่อง / เป็นเงินก้อนเดียวกัน |
| จับคู่ละเอียด | จับคู่เอง (เคสที่ระบบเดาไม่ออก) — ซ่อนใน นักบัญชี |
| ส่งตรวจ | ส่งให้ผู้จัดการตรวจ |
| internal transfer | เงินเข้าที่ไม่ใช่ยอดขาย (โอนกันเอง) |
| lane/channel | ช่อง/ช่องทาง (เลิกใช้ "lane") |

## 6. Core screens
1. หน้าแรก: กล่องงานทั้งเครือ (§2) → tap branch.
2. กล่องงานของสาขา: prioritized stack (1 wrong-channel → 2 missing docs → 3 setup → 4 internal-
   transfer → 5 real variance → 6 coin rollup), then green/timing/docs drawers, footer
   "ส่งให้ผู้จัดการตรวจ" (disabled until todo=0) + quiet "มุมมองนักบัญชี ↗".
3. การ์ดขาดเอกสาร: "อัปโหลดแล้วถึงวันที่ 19 — ขาด 20–31" + names the exact file (hint) →
   เลือกไฟล์ → preview → ยืนยัน.
4. การ์ดต่างจริง + รับว่าต่าง bottom sheet of canned reasons (replaces the bare prompt()).
5. หลักฐาน (evidence sheet, on-demand): the existing day-drill, entered narrowly per issue.

## 7. What to cut (nothing deleted — moved to classic.html / นักบัญชี)
The landing variance grid, the "รวมทุกสาขา" footer row, the always-on day calendar, the always-
visible 7-row channel table, the per-channel "จับคู่ละเอียด" buttons as front-line tools, raw
status chips, signed ± numbers, prompt() dialogs, header link clutter (→ "⋯ เพิ่มเติม" menu).

## 8. Build plan (vanilla HTML/JS + FastAPI, smallest-highest-impact first; ~4.5 days)
- **Phase 0 (~0.5d, zero risk):** rewrite label maps + card copy to plain Thai (§5); add Thai
  channel labels to reclass_suggestions payload (use _CH_LABEL) so cards never show "CASH·KBANK".
  Kills the jargon before any layout change.
- **Phase 1 (~1.5d):** promote the wrong-channel card to the TOP of loadBranch as the §3 hero
  (reuse applyReclass), reorder branch blocks into the priority stack, add the med-pair inline
  confirm + the canned-reason bottom sheet. → the #1 error becomes a 2-tap card.
- **Phase 2 (~1d):** collapse channel table + day grid into green/timing drawers (route
  pending/awaiting there, never red); add the ≤฿100 coin rollup; add "มุมมองนักบัญชี ↗".
  → 23 red cells → ~3 cards.
- **Phase 3 (~1d):** replace loadOverview with the branch-list inbox; add reclass_count to
  /api/v2/overview so falsely-clean branches surface + the chain banner shows "N คู่ (รวม ฿X)".
- **Phase 4 (~0.5d, optional):** manager chip, "⋯ เพิ่มเติม" menu, doc coverage_to wording,
  evidence-sheet "น่าจะคู่กัน" nudge.

Files: recon/frontend/index.html (all), recon/reconcile_v2.py (Phase 0 reclass labels ~L404;
Phase 3 reclass_count in reconcile_overview ~L442), recon/frontend/help.html (update after
Phase 3), recon/frontend/classic.html (unchanged — the rollback target).
