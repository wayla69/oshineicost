# Revenue Reconciliation — Progress

_Branch `claude/revenue-reconciliation-design-ScK5R` · updated 2026-06-06_

Reconciles external money-in (bank, credit-card, Grab, Line Man) against POS
revenue, per master branch per month. Standalone FastAPI app under `recon/`,
sharing `oshinei.db` (all tables `recon_`-prefixed).

## Reconciliation model (owner-confirmed)
- **QR + Bank Transfer** → bank statements (KTB / KBANK), split by bank
- **Credit Card** → settlement statements, split by acquirer (KTB / KBANK / KTC)
- **Grab / Line Man** → their own reports (gross vs net via fee/VAT)
- **Cash** → bank cash-deposit lines
- **Coupon** reconciled; **Entertain/ENT** excluded (internal comps)
- **Fee + VAT** entered manually **per line** (Grab / Line Man / Credit Card)
- Each master branch has its **own KTB + KBANK accounts**
- Differences beyond tolerance (฿100 / 0.5%) → **accounting discrepancy queue**
- Scope: **5 master branches**; Korat2 closed (`active=0`, done)

## Architecture
POS payment codes are messy and branch-specific, with a hidden bank dimension, so
classification is **config-driven, not regex**:
- **`recon_payment_map`** — `payment_code_norm → (category, bank, reconcile)`,
  seeded from `pos_payment_month` (master only) and confirmed by staff in Setup.
- **`recon_bank_account`** / **`recon_acquirer_mid`** — attribute statement/CC lines
  to a branch + bank/acquirer.
- **Engine** (`matcher.py`): `pos_category_totals` buckets POS into
  `{(category,bank)}`; `external_buckets` aggregates bank (QR/TRANSFER/CASH by bank),
  CC (by acquirer, gross + effective net), Grab/Line Man (gross + effective net).
  Per-bucket compare → matched | mismatch | pos_only | ext_only | no_source.
  `recon_category_result` stores the per-(category,bank) outcome (UI cards).

## Status
| Area | State |
|------|-------|
| Phase A — payment map + bank/acquirer registries + CRUD + seed | ✅ Done |
| Phase B — category/bank reconciliation engine | ✅ Done |
| Phase C — manual fee/VAT per line | ✅ Done |
| Phase D — frontend (Reconcile cards, Setup, fee/VAT editor, uploads, token) | ✅ Done |
| Phase E — pytest suite (23 tests) | ✅ Done |
| Master-only scope + write-auth gate + upload guards | ✅ Done |
| Idempotent run-match; ENTERTAIN excluded; unmapped codes surfaced | ✅ Done |
| **Bank-statement check (bank-first coverage) + CREDIT 3-document match** — new "เช็คยอดธนาคาร" tab + `GET /api/sessions/{id}/bank-coverage`. `bank_statement_coverage()` walks every statement credit line and tags it matched / unmatched / excluded / informational (cash via `cash_deposit_matching`, revenue lanes via `_line_status`/lane status, payout legs via net-day grouping) with headline + per-lane rollups and an unmatched list → "จับคู่ใน POS" opens the existing manual matcher scoped to that lane. `credit_three_way()` does the per-acquirer 3-doc chain: POS gross ↔ settlement gross (`pos_leg`), settlement net ↔ bank credit (`bank_leg`) with MDR fee+VAT auto-pulled (`_eff_fee_vat`) to bridge gross→net; bank leg matched per acquirer over CC_SETTLE+TRANSFER by amount+lag (`_match_net_days_to_credits`). No schema change, no new mutating endpoints (reuses `/api/manual`, `/api/bank-lines/{id}/ignore`). +2 pytest cases (69 pass). Verified live UDN May: 61/168 lines matched, ฿3.5M internal transfer reported apart, CREDIT cards flag "ยังไม่ได้อัปโหลดสรุปยอดบัตร" when the settlement summary is missing. | ✅ Done 2026-06-11 (branch claude/recon-bank-coverage-3way) |
| Guided **Mapping Flow** (Setup) — per-payment-type stepper: type → bank chips → reconciling document, progress bar, deep links from every unmapped-code banner (`openMappingFlow`); needs_attention now includes PROMPTPAY in bank-required cats | ✅ Done 2026-06-10 |
| **Cross-branch board uses the cash matcher (consistent everywhere)** — `daily_board` now runs `cash_deposit_matching` per branch (same channel-agnostic T+1/lump engine as the Daily view) instead of reading raw CASH_DEP next-day; cash-claimed lines (e.g. SDCH) are excluded from the QR/transfer columns. Verified all 5 branches reconcile cash 95–99% (สุรินทร์ has no cash POS code — genuine). Also fixed the board date-picker (typing/selecting a date now works — input is the source of truth; prev/next keep it in sync). | ✅ Done 2026-06-11 (branch claude/board-cash-consistency) |
| **Cash matching v3 — channel-agnostic by amount** — banks post the next-day cash lump under inconsistent codes (UDN KTB posts it as **"SDCH"**, parsed as QR — 31/31 days = POS cash D vs SDCH D+1, owner-confirmed). `_cash_dep_credits` now also considers QR/TRANSFER credits in lanes NO mapped POS tender claims (so real KNNQR-on-KBANK is never stolen) and matches by amount+T+1; consumed lines are re-bucketed to CASH everywhere (daily/monthly/stats/upload drill) so they stop showing as QR ext_only. POS cash collapses bank→NONE (a deposit has no bank dimension). UDN May: cash went from 0 matched → **30/31 days matched (฿2.21M)**, QR·KTB ext_only 2.26M→0; banner red→amber "1 วันรอเอกสาร" (05-31 banks 1 มิ.ย.). | ✅ Done 2026-06-11 (branch claude/cash-flexible-matching) |
| **Cash deposit matching v2** — `cash_deposit_matching()` groups 1–4 consecutive POS-cash days → CASH_DEP credit (lag 0–3, incl. next-month rollover for day-31 cash); weekend lump deposits now match; daily lanes carry deposit trace (date/lag/combined); cash coverage falls back to bank coverage when statements have ZERO deposit lines → days read **missing** not eternal pending; `cash_summary` in /daily + red banner "ไม่พบการนำฝากเงินสด" (UDN May: POS cash ฿2.33M, deposits 0 — REAL finding, all 11 statements checked); cross-branch daily_board CASH column off-by-one fixed (now reads day+1 deposits) | ✅ Done 2026-06-10 |
| **Upload usability** — empty statements (KBank CA "No Data") confirm cleanly with note "ไม่มีรายการในเอกสาร" + "ว่าง" badge (no more scary 0-row/422); confirm-time wrong-branch guard (doc's account#/MID vs chosen branch → 409); `PATCH /api/uploads/{id}/branch` + "ย้ายสาขา" button moves an upload + its lines (fix for live upload 23: UBN KTB statement sitting under UDN branch 25) | ✅ Done 2026-06-10 |
| **Document match status** — `/api/uploads` carries match_done/match_total per upload (bank=credit lines, delivery=Completed); "จับคู่แล้ว" chip in Uploads table → per-line modal via `/api/uploads/{id}/lines`; run-match now also marks grouped cash-deposit lines matched | ✅ Done 2026-06-10 |
| **Uploads page redesign (one panel, auto-refresh)** — consolidated match-status + map-suggestions onto the Uploads tab; per-document rows show a live match progress bar (matched/total + unmatched count) computed **read-only by lane** (`upload_match_stats`) so numbers are current without pressing Run Match; reconciliation-health headline (% + missing-document list); inline green "เอกสารบอกธนาคารได้" suggestion card (moved out of Setup) with Apply that re-renders in place; per-line drill derives live status (matched/unmatched/excluded/informational) consistent with the panel; non-revenue lines (debits/FEE/payout legs) → informational, kept out of the denominator | ✅ Done 2026-06-10 (branch claude/recon-internal-transfers) |
| **Internal-transfer ignore flag** — `PATCH /api/bank-lines/{id}/ignore` sets bank-line `match_status='excluded'` (reuses the CHECK-allowed status; no migration): excluded everywhere (monthly + daily lanes, daily board, cash credits, payout candidates, map suggestions), survives Run Match, reversible, audited with note; "ตัดออก / เลิกตัดออก" buttons in the drill drawer + upload-lines modal; verified live-mirror: 05-29 ฿2M+฿1.5M intercompany sweeps drop TRANSFER·KBANK from 3.5M to 1.4k | ✅ Done 2026-06-10 (branch claude/recon-internal-transfers) |
| **Map-from-documents suggestions** — `map_bank_suggestions()` matches each needs-map POS code's daily series against statement-lane residuals (QR/TRANSFER per bank, CREDIT per acquirer with T+1/T+2 lag) → `/api/map-suggestions`; Setup shows a green "เอกสารบอกธนาคารได้" card with one-click Apply (audit note "from documents: h/n days matched"). Verified on real UDN May data: **KNNQR → QR·KBANK, 24/31 days satang-exact** — answers the long-open "which bank does KNNQR settle into" | ✅ Done 2026-06-10 |

## Reconcile v2 — channel-first redesign (2026-06-11, in progress)
Owner decisions: main view = POS channels; daily board folded into a monthly
overview (variance per cell, drillable); two-tier close (staff ส่งตรวจ →
manager ปิดเดือน). New composition layer `reconcile_v2.py` (NO new matching
logic — composes category_reconciliation + daily_reconciliation + cash matcher +
credit_three_way + _coverage) with the status vocabulary collapsed to
ok / diff / waiting_doc / no_source / setup. Always computed live — **no Run
Match button**; the persisted snapshot happens once, inside ปิดเดือน
(run-match + approve). Progress is money-weighted (1 − Σ|variance|/Σmax(pos,ext)),
not day-counted. Endpoints: `GET /api/v2/overview?month=` (all master branches),
`GET /api/v2/reconcile?branch_id=&month=` (documents checklist derived from the
branch's POS channels, channel cards with per-day status, non_revenue =
excluded internal transfers reported apart, setup_questions with one-click
map-suggestion apply, todo list, can_close), `GET /api/v2/lane` (line drill, no
session needed). New UI `frontend/v2.html` served at **/app/v2.html** —
runs PARALLEL to the old /app (nothing removed yet): overview table → branch
page → channel rows → day calendar → bank lines with "ตัดออก ไม่ใช่ยอดขาย";
reuses existing mutating endpoints only (sessions, payment-map PATCH,
bank-lines ignore). Verified live on real data: UDN May progress 75.2%,
todo 6 (สรุปยอดบัตร×2, Grab, Line Man, QR diff, transfer residue); ฿3.5M
internal transfer shows in the non-revenue strip. +2 pytest (72 pass).
Remaining for v2: manual-match action in the day drill, upload from within v2,
close-month lock surfacing in UI, then retire the old tabs.

**v2 P1 — ใช้ได้จริง (2026-06-11):** (1) **Upload in-page** — missing doc chips
are buttons → file picker → `/preview` (row count, provider, wrong-branch guard
shown inline) → confirm; doc checklist carries `source`/`provider`/`channel`.
(2) **รับทราบผลต่าง (accept)** — the close-blocker fix: channel-level
`recon_lane_adj` row `adj_type='accept'` (bank='ALL', zero amounts, note =
mandatory reason; CHECK widened via `_migrate_lane_adj_accept`). Clears the
channel's todo + its doc todo, numbers stay honest (variance unchanged);
`run_auto_match` writes that lane's discrepancy **born 'accepted'** with the
reason so the approval gate passes — a month can now close over a ฿396 missing
Line Man report. Reversible (เลิกรับทราบ = DELETE adj). (3) **Manual match in
day drill** — checkbox bank lines → "จับคู่กับยอด POS วันนี้" (POSTs
`/api/manual`); `reclass_suggestions` (wrong-channel, same bank/amount/±1d)
rendered as green one-click cards. (4) **Close lock + Excel** — approved month:
all mutating buttons hidden, 🔒 banner, approved_by/at shown, ⬇ Excel button
(`/export.xlsx`); ปิดเดือน now prompts for the manager name (sent as
approved_by; status endpoint is Form-encoded, not JSON). E2E on live-copy DB:
UDN May todo 6→4 after accepting GRAB+LINEMAN, variance intact (-396), doc
chips carry upload metadata. 73 pytest pass.

**v2 M1 — merge plan step 1: full document manager in v2 (2026-06-11):**
Merge direction decided with owner: v2 = main path, old app's mature tools get
absorbed as deep layers (M1 docs ✅ → M2 full manual matcher + mapping flow →
M3 manager review screen → M4 switch /app to v2, retire duplicate tabs).
M1 ships the old Uploads page's strengths inside v2's เอกสาร card:
"จัดการไฟล์ ▾" expands a table of this branch+month's uploads with live
match-progress bars (`match_done/match_total` from /api/uploads), per-line
drill (รายบรรทัด → /api/uploads/{id}/lines with the same live status chips as
the old panel), ย้ายสาขา (PATCH /branch, master-only target list), and ลบ
(DELETE + re-upload; dedup guard server-side). Mutations hidden when the month
is closed. Frontend-only — all endpoints already existed. Verified live UDN
May: 3 files, KB_SA 31/65 bar, drill 72 lines = 30 matched / 8 unmatched / 34
informational.

**v2 M2+M3 — full matcher, self-serve mapping, manager review (2026-06-11):**
(M2a) **จับคู่ละเอียด** per channel ("🔧" in the channel expand; bank chips when
the channel spans several): two-pane lane matcher fed by GET /api/manual —
left = POS days with remaining + partial-amount inputs, right = document lines
with checkboxes (auto-`matched` lines shown dimmed and unselectable so they
can't be double-claimed; `manual` ones hidden), live both-side sums with
variance color, note → POST /api/manual; existing matches listed with ยกเลิก
(DELETE). Covers the lump-over-several-days / partial-allocation cases the
simple day-drill match can't. (M2b) **ตั้งค่าเอง** on every setup-question
card: category+bank selects → PATCH when the code already has a map row,
POST to create when it doesn't — no more jumping to /app#setup for a code the
suggestions can't answer. (M3) **สรุปสำหรับผู้จัดการ** — when the session is
in_review, a review table appears above the buttons: every channel's
POS/เข้าจริง/ต่าง + status, the staff's accept reasons with who wrote them,
and the internal-transfer total, so the manager reads everything before
entering the close code. JS parse-checked (node), 76 pytest pass.

**v2 M4 — switch the entry point (2026-06-11):** `git mv` swapped the files —
v2 is now `frontend/index.html` (served at **/app/** by the StaticFiles
html=True mount, no route change) and the old app is `frontend/classic.html`
(**/app/classic.html**), kept as the escape hatch for ~1 month. `frontend/v2.html`
is now a tiny meta+JS redirect to /app/ so old bookmarks / the old Start Recon.bat
URL still land right. Cross-links updated: v2's "เครื่องมือขั้นสูง ↗" → classic;
classic's top banner now grey "นี่คือหน้าเดิม (สำรอง) → ไปหน้าหลักแบบใหม่".
Coupon (the one tab v2 lacks) stays in classic and is reachable from v2's COUPON
channel via "🎟️ รายละเอียดคูปอง ↗" → /app/classic.html#coupon; classic's init()
now reads location.hash and opens that tab directly. Start Recon.bat opens /app/.
Verified live: /app/=v2, /app/classic.html=old, /app/v2.html→redirect,
#coupon deep-link opens the coupon tab. Duplicate tabs (overview/board/daily/
monthly/bankcov) intentionally NOT deleted yet — that's the post-month cleanup
once the team has closed one real month on v2 and confirms nothing sends them
back to classic. 76 pytest pass.

**v2 P2+P3 — trust & polish (2026-06-11):** (P2.5) **POS-number provenance** —
every channel carries `codes` (POS payment codes behind the total, via
`_pos_codes_by_channel`); expanding a channel shows "ที่มา POS: KNNQR 3.6M…"
chips. (P2.6) **Fee/VAT in v2** — CREDIT expand renders the 3-document chain
cards (POS รูด → สรุปยอดบัตร − fee+VAT → เข้าบัญชี, leg chips) +
"✎ ค่าธรรมเนียม & VAT" table for CREDIT/GRAB/LINEMAN with inline inputs
(PATCH `/api/lines/{src}/{id}/fee-vat`, auto-save, live net recompute).
(P2.7) **Manager gate** — `OSHINEI_MANAGER_TOKEN` env (open when unset, same
pattern as the write gate): PATCH status→approved/closed requires
`X-Manager-Token`; staff transitions stay open; v2 prompts for the code only
when the server demands it (remembers per browser session). (P3.8)
**Month-boundary rollover** — `prev_month_consumed_ids()`: deposits on the
1st–3rd that the PREVIOUS month's cash matcher claims (T+1 of month-end cash)
are now excluded from this month's buckets/daily/coverage (coverage shows them
informational·CASH) — kills the systematic "statement เยอะกว่า" at every month
start; pytest-proven with a May-31-cash → June-1-SDCH case. (P3.9) Mobile CSS
(stacked channel grid, smaller day cells ≤640px). (P3.10) Old /app now shows a
banner linking to v2 — old tabs intentionally NOT removed until the team
finishes one full month on v2. 76 pytest pass. Verified live: UDN May QR
provenance = KNNQR 3,623,111; CREDIT split กรุงไทย/กสิกรไทย; manager gate
403→(409 discrepancy gate) on real session; overview 5 branches 0.06s.

## Verification
- `pytest recon/tests/test_recon.py` — 23 pass (classify, seed, engine statuses +
  idempotency, fee/VAT net, parser skipped-count, master-only/period/token gates).
- Backend E2E via TestClient: Grab upload through the parser → fee/VAT edit →
  run-match → category cards + accounting discrepancies.
- Live ASGI server confirmed serving `/app` + correct API (overview reconcilable
  totals with ENTERTAIN excluded; `needs_attention` flags unmapped codes).

## Remaining (need real files / decisions)
- **Confirm UNKNOWN-bank codes** in Setup: `KNNQR` (฿76M — biggest tender),
  `CC`, `Credit`, `Bank Transfer`, `QR Code on EDC`. KNNQR's settling bank is the
  top priority.
- **KTC** — separate settlement statement, or settles via KTB/KBANK?
- **Coupon** external source (voucher provider) — currently `no_source` → flagged.
- Run one real bank / CC / Grab / Line Man export per master branch through
  `/preview`; confirm parser column maps; populate bank-account + acquirer-MID registries.
- **Deploy** recon as the 2nd Fly process (two-process `fly.toml` already on branch;
  not yet deployed). Auth activates automatically (`OSHINEI_SEED_TOKEN` already set).

## Daily reconciliation (2026-06-06)
Staff reconcile per day. `pos_payment_day` (new in `sync_mssql.py`, char(10) daily
analog of `pos_payment_month`) is the daily per-tender POS source — **run the sync to
populate it**. `GET /api/sessions/{id}/daily` returns a per-(day, category, bank) board
plus per-day **pending** tracking for late documents:
- **matched | variance** (both sides present), **missing** (statement covers the day but
  the line is absent = real gap), **pending** (no statement covers that day yet = late doc),
  **ext_only**, **no_source** (coupon), **unmapped_bank** (POS code's bank is UNKNOWN/PROMPTPAY
  — map it in Setup).
- `coverage` = last day each source's document reaches; `pending_summary` rolls up pending
  lane-days. Daily tolerance ฿50 / 1%. UI: **Daily** tab (default on opening a session).

Reviewed by an adversarial workflow (19 findings); fixed: cash coverage now filters to
`CASH_DEP` only; UNKNOWN/PROMPTPAY lanes → `unmapped_bank` (not silently pending); negative
(refund) POS no longer mis-flagged `ext_only`; daily response surfaces unmapped codes.

### Known daily limitations (deferred)
- **Date semantics differ by source** (bank txn_date / CC settlement_date / delivery order_date
  vs POS SaleDate) — settlement lag is inherent; the missing-vs-pending split absorbs it, but a
  same-day-vs-next-day post can shift a lane by one day (staff judgment).
- **Bank-side refunds (debits)** are out of scope — engine reconciles money-in (`credit>0`);
  CC refunds are netted via `refund_amount`.
- **Cash is a global lane** (POS cash has no bank); coverage is per-bank for QR/Transfer/CC.
- **Per-account coverage** not modelled (assumes one account per bank per branch).

## Notes
- `pos_customer_type_month` (Grab/Line Man) only exists from 2026-01; delivery POS
  uses the `GB`/`LM` payment codes instead (back to 2025-05, all masters).
- `sales_month_summary` payment columns are 0 (col-34/40 ingest not re-run) — the
  engine relies on `pos_payment_month`, so this is moot.
