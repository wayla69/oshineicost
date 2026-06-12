# Revenue Reconciliation — Pilot Guide (May/June test → July go-live)

A step-by-step checklist for the accounting team to validate the app on **May and
June 2026** data before relying on it in July. Work **one branch + one month at a
time** and get a human to agree the result before moving on.

**App:** https://oshinei-dashboard.fly.dev:8443/app/
**Scope:** 5 master branches — โคราช1, เชียงใหม่, สุรินทร์, อุดรธานี, อุบลราชธานี.
**Write token:** click 🔑 (top-right) and paste the token once (needed to upload, map, reconcile).

---

## The flow
1. **POS data is already synced** (daily, 1 May → 5 June). ✔ done — nothing to do.
2. **You upload the documents** — bank, credit-card, Grab, Line Man statements.
3. **You map** the POS payment codes (Setup) so the app knows each code's bank.
4. **You reconcile** — the Daily board shows each day matched / off / awaiting.

> Refresh POS data anytime with **Refresh Online Data.bat** — your reconciliation
> work (uploads, mappings, sessions) is preserved across refreshes.

---

## Step 1 — Setup (do this once, ~15 min)
Open **Setup** tab.

- [ ] **Payment-code map** — every code with an amber **⚠ / "needs a bank"** must be
      given a real bank. Priority by volume (shown per code):
  - `KNNQR` (฿80M) — which bank account does this QR settle into? Set **KTB** or **KBANK**.
  - `Bank Transfer`, `Credit`, `CC`, `QR Code on EDC` — set the right bank/acquirer.
  - `QR PromptPay` → set the bank account it lands in (not "PROMPTPAY").
  - Changes **auto-save** (✓). The amber list at the top must reach **zero**.
- [ ] **Bank accounts** — add each branch's **KTB** and **KBANK** account numbers.
- [ ] **Acquirer MIDs** — add each branch's card merchant IDs (KTB / KBANK / KTC).

_If unsure about a code's bank, ask whoever owns the bank accounts — getting this
right is what makes the numbers tie out._

## Step 2 — Upload documents (per branch, per month)
Open **Uploads** → pick the branch (top bar) → **+ Upload** for each source:

- [ ] **Bank statement** — one upload per bank (KTB, KBANK). Set Provider = KTB / KBANK.
- [ ] **Credit-card settlement** — per acquirer (KTB / KBANK / KTC).
- [ ] **Grab** report · **Line Man** report.

For each file: click **Preview** first. **Check the preview looks right** (dates,
amounts, row count, "skipped rows" should be ~0). If the preview is wrong or empty,
**stop and report it** (see bottom) — the parser likely needs a tweak for that file
format. Only **Confirm** once the preview is correct.

## Step 3 — Reconcile (per branch, per month)
- [ ] Top bar: pick the **branch** and the **month** (May, then June).
- [ ] The branch opens on the **Daily** board. Click **▶ Run Match** (Monthly tab) to
      record results, or just read the live board.
- [ ] Use the **date picker** to focus a single day.

## Step 4 — Read the verdicts
Each day shows POS vs the statements per lane (Cash, QR·KTB, QR·KBANK, Credit·…,
Grab, Line Man). Lane status:

| status | meaning | action |
|---|---|---|
| ✓ **matched** | POS = statement (within ฿50/1%) | nothing |
| ≠ **variance** | both present, amounts differ | investigate the gap |
| ✗ **missing** | statement covers that day but the line isn't there | a real gap — chase it |
| … **pending** | no statement covers that day yet | upload the late document |
| ? **unmapped_bank** | the code's bank isn't set | fix it in Setup |
| + ext_only / · no_source | extra in statement / coupon | review |

The **coverage strip** shows the last day each document reaches; the **Discrepancies**
tab is the follow-up queue (assign + accept/adjust/reject). From **Overview**, click a
branch's **Issues** badge to jump straight to its follow-up list.

## Step 5 — Sign-off (the real test)
For **one branch + one month**, a human must confirm:
- [ ] Every lane is **matched** or has an **explained** variance/missing.
- [ ] The explanations make sense (fees, timing, a genuine error caught).
- [ ] Then repeat for the **other branches** and for the **second month**.

✅ If May **and** June tie out across all 5 branches → trust it for **July go-live**.
❌ If not → the gaps it exposed are the fix list. Don't go live until they're closed.

---

## What's new since the initial pilot spec (June 2026 overhaul)

- **Advanced Setup page** (`/app/setup.html`) — edit tolerance, lag, merge-days per lane; payment-code map for all 12 POS codes in one table. Method-config mutations require the manager token.
- **Two-stage visibility** — Credit card, Grab, and Line Man cards each show ขั้น 1 (summary↔POS gross) and ขั้น 2 (net↔bank) separately so you can see exactly which leg is off.
- **Coupon channel** — KTC / BAY point coupons now reconcile automatically (POS ↔ issuer settlement ↔ bank); status shows matched / waiting_doc / diff instead of "no source" when point data exists.
- **Cash 3-way** — per-day slip column with ±฿10 tolerance; type the slip amount directly in the row, attach a photo with 📷.
- **Internal-transfer exclusion** — large round bank credits (฿100k+) are flagged as likely intercompany sweeps; one click excludes them from the TRANSFER lane.
- **TRANSFER/ANY** — "Bank Transfer" codes that can land in either KTB or KBANK are matched against both statements; both bank chips appear in the channel card.

## Known limitations (expected, not bugs)
- **Timing:** credit-card settles T+1/T+2 and delivery payouts lag, so a sale's day
  and the statement's day can differ by 1–2 days — judge with the missing/pending split.
- **Fees/VAT:** for Grab / Line Man / Credit Card, enter fee + VAT per line (Daily →
  Fee & VAT panel) so net ties to what hit the bank.
- **Coupon progress** — coupon points are discounts, not bank money-in (reimbursement rides CREDIT/TRANSFER lanes), so they are excluded from the progress gauge in all statuses to avoid double-counting.
- POS data syncs daily (1–2 day lag); later days fill in on the next sync.

## Report an issue
When something looks wrong, note: **branch, month, source, what you expected vs what
the app showed**, and (for upload problems) the **file**. Parser/format issues and any
lane that won't reconcile are the most useful to send back — they're quick to fix and
are exactly what the pilot is for.
