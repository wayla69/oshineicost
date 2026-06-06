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
1. **Mark Korat2 (id 27) `active=0`** — permanently closed; closes it everywhere via the
   `active=1` filter (no name hardcoding).
2. **VERIFY col-40 mapping** — `ingest_easy.py` maps Easy col 40 → `qr`; may be transfer/mixed.
3. **Confirm parser column maps** — run one real export of each source through `/preview`.
4. **Tune matcher patterns** — check CC/QR/Grab/Lineman regexes via `/api/diagnostics/*`
   against a real month before trusting matches.
5. **No tests** — golden-file parser tests + a matcher test.

## Known issues (logic review 2026-06-06)

🔴 Correctness — produce wrong reconciliation output:
- **Grab/Lineman lines always flagged as discrepancies**, even on a perfect monthly match.
  `match_delivery_lines` makes one aggregate match with `external_id=-1`, but `_create_match`
  only sets `match_status='matched'` when `external_id>0` → every delivery line stays
  `unmatched` → summary shows `matched=0` and all lines become pending discrepancies.
  (matcher.py:229, :323)
- **CC/bank daily matching compares wrong bases.** `recon_pos_daily.total` is the whole day's
  inc-VAT revenue across all tenders; a CC line's `net_amount` is card-only after MDR fee.
  They rarely fall within ฿5 — the monthly cross-check is the meaningful comparison; the daily
  loop inflates discrepancies. (matcher.py:127, :177)
- **`run-match` is not idempotent.** Re-running duplicates the Grab/Lineman aggregate match;
  `recon_discrepancies` has no UNIQUE constraint so `INSERT OR IGNORE` doesn't dedup. (matcher.py:229, :353)

🟠 Production safety (recon is exposed on public Fly port 8443):
- **No auth on mutating endpoints** — upload/confirm/delete/resolve/status are open. Mirror the
  main app's `require_write_token` gate.
- **No file-size cap / content-type check** on uploads.
- **`branch_id` / `period_month` from form are trusted** — no master-branch check, no `YYYY-MM` validation.

🟡 Minor:
- `update_session_status` stamps `approved_at=now()` for any status, incl. draft/in_review. (main.py:263)
- Rows with unparseable dates are dropped silently; `preview.row_count` then understates the file.
- `recon_pos_daily` cash/cc/qr/transfer columns are never populated by `refresh_pos_daily`.

## Next steps
- [ ] Mark Korat2 `active=0` (open item #1).
- [ ] Resolve col-40 (qr vs transfer); re-ingest.
- [ ] Run each source through `/preview`; fix column maps.
- [ ] Tune regexes via diagnostics on a recent month.
- [ ] Add parser + matcher tests.
- [ ] E2E: session → upload 4 sources → run-match → review variance.
