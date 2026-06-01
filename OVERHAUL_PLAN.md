# Oshinei Dashboard — Major Overhaul Plan (2026-06)

Goal: clean data, consistent COGS, simpler navigation, useful drill-downs, and
buffet-focused menu/cost analytics that drive real decisions.

Approach: **phased**, each phase backed up + verified live before the next.
Nothing destructive runs without a backup. You approve this plan, then I execute
phase by phase.

---

## Findings (audit 2026-06-02)

**Data**
- 7 EMPTY categories (A LA CARTE, DISCOUNT, Payment, Other, Unknown, Voucher,
  Oshinei หม้อและน้ำซุป) + ~12 tiny test categories ((YAKI)*, (Renshin)*, (NEW)*).
- "Special <branch>" categories (KKN/LPB/MDH/CBI/Chachoengsao…) — fragmented.
- 482 sku_cost rows vs 291 BoM SKUs → 284 SKUs costed by sku_cost only (no recipe).

**Code**
- main.py 12,333 lines · index.html 16,020 lines · 121 endpoints · 24 tabs.
- **COGS inconsistency**: 34 spots use `_cogs_case` (sku_cost), 14 use BoM.
  Same metric (GP) differs between tabs — the core "ตัวเลขไม่ตรง" complaint.
- 24 overlapping tabs; date/period selector overlaps per tab.

---

## PHASE 1 — Unify COGS (one source of truth)   [highest impact]
Problem: GP differs across tabs because two COGS methods coexist.
Fix: a single `_unified_cogs_expr()` = **BoM recipe cost if the SKU has a recipe,
else fall back to sku_cost.unit_cost** (keeps the 284 recipe-less à-la-carte items
costed). Buffet tier rows still contribute 0 (no double-count).
- Replace all 34 `_cogs_case` call-sites with the unified helper.
- Keep a regression test asserting chain GP is identical across
  /api/profit/summary, /api/gp/by-branch-month, /api/pnl/branch, /api/cost/analysis.
- Risk: low (pure SQL expression swap). Verify: GP equal on all 4 endpoints.

## PHASE 2 — Database deep-clean
- Back up (sqlite backup API) first.
- Delete 7 empty categories + reassign/￼remove ~12 test categories
  ((YAKI)*, (Renshin)*, (NEW)*, UBN BADMINTON) — move real food to proper category,
  drop non-product.
- Normalize "Special <branch>" → keep as real food but tag branch separately, OR
  fold into the matching standard category (confirm per case).
- Re-point any SKU on a deleted category to 'Oshinei other' / correct category.
- VACUUM + ANALYZE. Verify revenue totals unchanged (±0).

## PHASE 3 — Navigation restructure (24 → 7 groups)
Proposed groups:
1. **ภาพรวม / Overview** — KPIs, revenue trend, category movers, heatmap
2. **บุฟเฟ่ต์ / Buffet** — tier mix, per-cover, seat util, daypart, turnover
3. **เมนู & ต้นทุน / Menu & Cost** — menu engineering, most-ordered, BoM cost, coverage
4. **สาขา & P&L / Branches** — GP รายสาขา, P&L deep-dive, benchmark, branch ops
5. **กำไร / Profit** — profit summary, per-bill, forecast
6. **พนักงาน & ส่วนลด / Ops** — staff, discounts
7. **เครื่องมือ / Tools** — settings, cost mgmt, report, manual
- Merge duplicate tabs (e.g. cost/profit/gpbranch overlap → consolidate).
- Risk: medium (UI plumbing). Done incrementally, one group at a time.

## PHASE 4 — Global date/period selector (fix overlap, item #5)
- One sticky period control in the top bar (Month / Quarter / Year / custom range),
  not per-tab. Remove the overlapping per-tab selectors.
- All loaders read the single global state.

## PHASE 5 — Branch drill-down everywhere (item #6)
- One global branch selector (All / specific) in the top bar.
- Every analysis reads it → instant per-branch view without separate tabs.

## PHASE 6 — Buffet menu/cost analytics (item #1, the key business ask)
New view: **"เมนูเสิร์ฟเยอะ vs สัดส่วนต้นทุน"** — for every dish:
qty served, % of total servings, BoM cost, % of total cost, cost-per-serving.
Sort to find HIGH-VOLUME + HIGH-COST dishes = best targets to re-engineer
(cheaper recipe / portion control) for max cost reduction. Per-branch.
Plus: ingredient-level "what raw material to attack first".

## PHASE 7 — Dive-in / zoom that's actually useful (item #4)
- Rework the dive-in modal: real drill (filter to that point, show breakdown,
  per-branch split, trend) instead of just a bigger chart.

## PHASE 8 — Final pass
- Remove dead endpoints, regression tests, VACUUM, full verification, update memory.

---

## Execution rules
- Backup before every DB mutation; verify live (preview :8010) after every phase.
- Revenue totals must stay constant through data cleaning (only junk removed).
- Each phase is shippable on its own; stop/adjust between phases.
