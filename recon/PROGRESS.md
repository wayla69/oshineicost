# Revenue Reconciliation — Progress

_Branch `claude/revenue-reconciliation-design-ScK5R` · updated 2026-06-06_

Reconciles external money-in (bank, credit-card, Grab, Lineman) against POS
revenue, per branch per month. Standalone FastAPI app under `recon/`, sharing
`oshinei.db` (all tables `recon_`-prefixed).

## Scope — master branches only
Reconciliation covers **master (company-owned) branches only**; franchises are out
of scope. Enforced by `COALESCE(branch_type,'master')='master' AND active=1` on the
recon `/api/branches` and `/api/overview` endpoints.

5 master branches in scope:

| id | branch |
|----|--------|
| 26 | อุบลราชธานี (Ubon) |
| 25 | อุดรธานี (Udon) |
| 24 | สุรินทร์ (Surin) |
| 7  | เชียงใหม่ (Chiang Mai) |
| 4  | โคราช1 (Korat1) |

**โคราช2 / Korat2 (id 27) is permanently closed** — excluded. It is still tagged
`master` + `active=1` in the live DB; mark it `active=0` so the `AND active=1`
filter drops it here and everywhere else in the app. Until then it would show as
an empty master row.

_Verified against the live app `/api/branches?branch_type=master` on 2026-06-06._

## Status
| Area | State |
|------|-------|
| Schema (9 tables, auto-created on startup) | ✅ Done |
| API (uploads, sessions, run-match, summary, discrepancies, overview, diagnostics) | ✅ Done |
| Master-only filter on `/api/branches` + `/api/overview` | ✅ Done |
| Matching engine (CC/bank daily ±฿5/±3d; Grab/Lineman monthly aggregate) | ⚠️ Built, untuned |
| Parsers (KBank/SCB/BBL/KTB/TTB, CC, Grab, Lineman) | ⚠️ Built, unverified vs real files |
| Frontend dashboard | ✅ Done |
| Fly deployment (dashboard 443 + recon 8443, shared `/data` volume) | ✅ Wired |
| Easy POS payment-column ingest (cols 34/36/39/40) | ⚠️ Partial — col-40 unverified |
| Tests | ❌ None |

## Assumptions
- POS revenue source of truth = `pos_payment_month` / `pos_customer_type_month`
  (from `sync_mssql.py`); falls back to `sales_month_summary` when absent.
- Payment-type classification relies on Easy `PaymentCode` / `CustomerTypeName`
  keyword patterns — branch-config dependent, unverified.
- All amounts reconciled ex-VAT (POS `net` ÷ 1.07).

## Open items
1. ✅ **Korat2 (id 27) `active=0`** — done 2026-06-06 on the live Fly DB; dropped from the
   master set (now 5) via the `active=1` filter.
2. **VERIFY col-40 mapping** — `ingest_easy.py` maps Easy col 40 → `qr`; may be transfer/mixed.
3. **Confirm parser column maps** — run one real export of each source through `/preview`.
4. **Tune matcher patterns** — check CC/QR/Grab/Lineman regexes via `/api/diagnostics/*`
   against a real month before trusting matches.
5. **No tests** — golden-file parser tests + a matcher test.

## Known issues (logic review 2026-06-06)

✅ Correctness — FIXED 2026-06-06 (matcher.py + main.py summary):
- **Grab/Lineman/CC now settle at the monthly level** via `_settle_monthly`: within tolerance →
  all lines marked `matched`; otherwise one `amount_mismatch` discrepancy. No more per-line
  discrepancy noise / `matched=0` on a clean month. Cancelled delivery orders → `excluded`.
- **Daily CC matching removed** (wrong base: whole-day inc-VAT vs card-only-after-MDR). Bank
  still uses daily fuzzy matching, which is appropriate for deposits.
- **`run-match` is now idempotent** — `_reset_session` clears prior auto matches/pending
  discrepancies and resets line status (preserving `manual`) before each run.
- _Remaining caveat:_ delivery `net_payout` (post-commission) vs POS delivery revenue
  (gross ex-VAT) diverge by ~commission — needs basis tuning once a real report is inspected.

✅ Production safety — FIXED 2026-06-06 (main.py):
- **Write auth** — `require_write_token` (mirrors the dashboard: `OSHINEI_SEED_TOKEN` +
  `X-Upload-Token`/`?token=`, open locally) now gates confirm-upload, delete-upload,
  create-session, run-match, status, and resolve. Read/preview stay open.
- **Upload guards** — `_read_upload` enforces `.xlsx/.xls` extension, rejects empty files,
  and caps size at 15 MB.
- **Input validation** — `period_month` must be `YYYY-MM`; `branch_id` must be an active
  master branch (else 400) on preview/confirm/create-session.

✅ Minor — FIXED 2026-06-06:
- `update_session_status` only stamps `approved_at`/`approved_by` when status is `approved`.
- Parsers now return a `skipped` count (data rows dropped for an unparseable date); surfaced
  as `skipped_rows` in the preview/confirm responses so silent data loss is visible.
- Dead per-tender columns removed from `recon_pos_daily` (sales_transaction has no per-tender
  breakdown; per-tender reconciliation is monthly via `pos_payment_month`).

## Next steps
- [x] Mark Korat2 `active=0` (done 2026-06-06).
- [ ] Resolve col-40 (qr vs transfer); re-ingest.
- [ ] Run each source through `/preview`; fix column maps.
- [ ] Tune regexes via diagnostics on a recent month.
- [ ] Add parser + matcher tests.
- [ ] E2E: session → upload 4 sources → run-match → review variance.
