# Cost-Data Audit — `sku_cost` is selling price, not cost

_2026-06-01 · read-only investigation · triggered by the negative à-la-carte margins the COGS fix (A2) surfaced_

## Finding (high confidence)

The negative à-la-carte margins are **not a code bug** — they are a **data-entry error in `sku_cost.unit_cost`**. For ~90% of costed SKUs, the stored "cost" is actually the **VAT-inclusive selling price**.

Evidence — across **567** costed non-tier SKUs (Easy, qty>0, rev>0):

| `unit_cost / (ex-VAT price)` ratio | # SKUs | Interpretation |
|---|---:|---|
| **1.05 – 1.09** (median **1.070**) | **513** | cost == inc-VAT **selling price** (price × 1.07) |
| 0.95 – 1.05 | 3 | cost == ex-VAT price |
| > 1.09 | 29 | cost above price (also wrong) |
| 0.60 – 0.95 | 2 | implausibly high but maybe real |
| **< 0.60** | **20** | looks like a **genuine** cost |

The median ratio is **exactly 1.070** — i.e. `unit_cost = price × 1.07`. Examples: BV0021 cost 48.1 vs price 45.0; DEL0002 cost 788.4 vs price 736.7; A0032 (sashimi) cost 573.9 vs price 535.4. Only ~20 SKUs (<4%) have a plausible real cost.

**Consequence:** any food-cost / GP / margin computed from `sku_cost` reads ≈ 100–107% by construction. The `/api/cost/analysis`, `/api/profit/*`, and Menu-Engineering tabs (which use `sku_cost`) are therefore showing placeholder economics.

## What is NOT affected

The flagship **BoM Cost Analysis** tab and the chain food-cost figure (**10.7%**) come from `bom_ingredients` (real recipe costs), **not** `sku_cost` — so that path is trustworthy. The problem is isolated to the older `sku_cost`-based endpoints.

## Exposure & recoverability

- The 513 price-as-cost SKUs carry **65% of the 148 M THB** non-tier à-la-carte revenue.
- **128** of them already have a **real BoM recipe cost** in `bom_ingredients` → can be substituted directly.
- **385** have no recipe → need a cost source (real cost import, a food-cost-% assumption, or flag as uncosted).
- `bom_sku_alias` has 94 B-code→A-SKU maps that may extend BoM coverage further.

## Recommended remediation (not yet done — changes reported financials)

1. **Back up first** (and now that the repo is in git, commit a checkpoint).
2. **For the 128 with BoM cost:** overwrite `sku_cost.unit_cost` with the BoM-derived per-serving cost. Route every write through `cost_change_log` (the audit table already exists).
3. **For the 385 without:** decide a policy —
   - import real purchase/recipe costs (best), or
   - apply a category food-cost-% assumption (e.g. food ~32%, beverage ~25% of ex-VAT price) as an explicit placeholder, clearly labelled, or
   - set them **uncosted** (NULL) so the UI shows "uncosted" instead of a fake 107% — this is the most honest interim state (the `has_cost` flag added in A2 already supports it).
4. Re-verify: à-la-carte `overall_margin` should turn positive and reconcile with the BoM tab.

A migration script can do steps 2–3 deterministically and log every change. **This is a financial-data change, so it needs an explicit go-ahead and a backup** — flagged here, not executed.

## UPDATE 2026-06-01 — remediation APPLIED (Mode A)

Ran `backend/fix_sku_cost_2026.py --apply` (threshold ratio ≥ 1.0):
- **544 SKUs** remediated · DB backed up to `oshinei.db.backup_pre_costfix_20260601_004011` · `VACUUM`+`ANALYZE` run.
- **128** set to real **BoM recipe cost**; **416** set **uncosted** (row deleted; `unit_cost` is `NOT NULL` so uncosted = no row).
- **544** rows written to `cost_change_log` (source `fix_sku_cost_2026-06-01`) — fully reversible/auditable.

**Result:** `/api/cost/analysis` à-la-carte `overall_margin` went **−5.9% → +75.0%** (≈25% food cost, plausible), `costed_skus` 767→**484** (now honest), `uncosted_skus`→**506**. Sample costed items show healthy margins (A0032 77.9%, A0108 89.3%).

**Remaining work (data, ongoing):** progressively cost the 506 uncosted SKUs — beverages especially (BV*: high volume, were uncosted because no BoM recipe). Load real supplier/recipe costs or extend BoM coverage; the `has_cost` flag makes the gap visible in the UI.

## Verification queries used
- ratio distribution over `sku_cost.unit_cost ÷ (Σ subtotal_ex_vat ÷ Σ qty)` per SKU
- BoM coverage via `SUM(bom_ingredients.ingredient_cost)` per `sku_id`
All read-only; no data mutated.
