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
| Guided **Mapping Flow** (Setup) — per-payment-type stepper: type → bank chips → reconciling document, progress bar, deep links from every unmapped-code banner (`openMappingFlow`); needs_attention now includes PROMPTPAY in bank-required cats | ✅ Done 2026-06-10 |
| **Cross-branch board uses the cash matcher (consistent everywhere)** — `daily_board` now runs `cash_deposit_matching` per branch (same channel-agnostic T+1/lump engine as the Daily view) instead of reading raw CASH_DEP next-day; cash-claimed lines (e.g. SDCH) are excluded from the QR/transfer columns. Verified all 5 branches reconcile cash 95–99% (สุรินทร์ has no cash POS code — genuine). Also fixed the board date-picker (typing/selecting a date now works — input is the source of truth; prev/next keep it in sync). | ✅ Done 2026-06-11 (branch claude/board-cash-consistency) |
| **Cash matching v3 — channel-agnostic by amount** — banks post the next-day cash lump under inconsistent codes (UDN KTB posts it as **"SDCH"**, parsed as QR — 31/31 days = POS cash D vs SDCH D+1, owner-confirmed). `_cash_dep_credits` now also considers QR/TRANSFER credits in lanes NO mapped POS tender claims (so real KNNQR-on-KBANK is never stolen) and matches by amount+T+1; consumed lines are re-bucketed to CASH everywhere (daily/monthly/stats/upload drill) so they stop showing as QR ext_only. POS cash collapses bank→NONE (a deposit has no bank dimension). UDN May: cash went from 0 matched → **30/31 days matched (฿2.21M)**, QR·KTB ext_only 2.26M→0; banner red→amber "1 วันรอเอกสาร" (05-31 banks 1 มิ.ย.). | ✅ Done 2026-06-11 (branch claude/cash-flexible-matching) |
| **Cash deposit matching v2** — `cash_deposit_matching()` groups 1–4 consecutive POS-cash days → CASH_DEP credit (lag 0–3, incl. next-month rollover for day-31 cash); weekend lump deposits now match; daily lanes carry deposit trace (date/lag/combined); cash coverage falls back to bank coverage when statements have ZERO deposit lines → days read **missing** not eternal pending; `cash_summary` in /daily + red banner "ไม่พบการนำฝากเงินสด" (UDN May: POS cash ฿2.33M, deposits 0 — REAL finding, all 11 statements checked); cross-branch daily_board CASH column off-by-one fixed (now reads day+1 deposits) | ✅ Done 2026-06-10 |
| **Upload usability** — empty statements (KBank CA "No Data") confirm cleanly with note "ไม่มีรายการในเอกสาร" + "ว่าง" badge (no more scary 0-row/422); confirm-time wrong-branch guard (doc's account#/MID vs chosen branch → 409); `PATCH /api/uploads/{id}/branch` + "ย้ายสาขา" button moves an upload + its lines (fix for live upload 23: UBN KTB statement sitting under UDN branch 25) | ✅ Done 2026-06-10 |
| **Document match status** — `/api/uploads` carries match_done/match_total per upload (bank=credit lines, delivery=Completed); "จับคู่แล้ว" chip in Uploads table → per-line modal via `/api/uploads/{id}/lines`; run-match now also marks grouped cash-deposit lines matched | ✅ Done 2026-06-10 |
| **Uploads page redesign (one panel, auto-refresh)** — consolidated match-status + map-suggestions onto the Uploads tab; per-document rows show a live match progress bar (matched/total + unmatched count) computed **read-only by lane** (`upload_match_stats`) so numbers are current without pressing Run Match; reconciliation-health headline (% + missing-document list); inline green "เอกสารบอกธนาคารได้" suggestion card (moved out of Setup) with Apply that re-renders in place; per-line drill derives live status (matched/unmatched/excluded/informational) consistent with the panel; non-revenue lines (debits/FEE/payout legs) → informational, kept out of the denominator | ✅ Done 2026-06-10 (branch claude/recon-internal-transfers) |
| **Internal-transfer ignore flag** — `PATCH /api/bank-lines/{id}/ignore` sets bank-line `match_status='excluded'` (reuses the CHECK-allowed status; no migration): excluded everywhere (monthly + daily lanes, daily board, cash credits, payout candidates, map suggestions), survives Run Match, reversible, audited with note; "ตัดออก / เลิกตัดออก" buttons in the drill drawer + upload-lines modal; verified live-mirror: 05-29 ฿2M+฿1.5M intercompany sweeps drop TRANSFER·KBANK from 3.5M to 1.4k | ✅ Done 2026-06-10 (branch claude/recon-internal-transfers) |
| **Map-from-documents suggestions** — `map_bank_suggestions()` matches each needs-map POS code's daily series against statement-lane residuals (QR/TRANSFER per bank, CREDIT per acquirer with T+1/T+2 lag) → `/api/map-suggestions`; Setup shows a green "เอกสารบอกธนาคารได้" card with one-click Apply (audit note "from documents: h/n days matched"). Verified on real UDN May data: **KNNQR → QR·KBANK, 24/31 days satang-exact** — answers the long-open "which bank does KNNQR settle into" | ✅ Done 2026-06-10 |

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
