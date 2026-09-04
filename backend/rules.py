"""The business rules that every report shares, stated once.

Until 2026-09 these lived as hand-copied SQL fragments: the net-revenue formula
in 96 places, the effective-cost chain in 9, the branch/group filter in 140.
Changing a rule meant editing every copy and missing some — the service-charge
split of 2026-09-02 touched 100+ lines and double-subtracted on 17 of them.

This module has no FastAPI or database imports on purpose: the live sync
(sync_mssql.py) and the standalone report generators run outside the app and
must be able to say "revenue" and "cost" the same way the app does.

Every helper returns SQL TEXT that is byte-identical to the copies it replaced,
which is what lets backend/test_golden.py prove the replacement changed nothing.
"""
from typing import Optional

# ─── VAT ─────────────────────────────────────────────────────────────────────
# Thai VAT. total_inc_vat is what the guest paid; revenue is stated ex-VAT.
VAT_RATE = 0.07
VAT_DIVISOR = 1.0 + VAT_RATE          # 1.07 — inc-VAT ➜ ex-VAT divisor


def ex_vat(amount_inc_vat: float) -> float:
    """Python-side twin of the SQL division, for the few places that total in Python."""
    return (amount_inc_vat or 0.0) / VAT_DIVISOR


# ─── Net revenue ─────────────────────────────────────────────────────────────
# Revenue = inc-VAT total ÷ 1.07, minus the service charge, which sits INSIDE the
# ex-VAT subtotal on Easy bills and is not the restaurant's revenue (it is
# distributed to staff). Both forms below are the same rule at two grains.

def net_revenue_row(a: str = "st") -> str:
    """Per-bill net revenue. `a` is the sales_transaction alias ("" when the
    query has none and the columns stand bare)."""
    p = f"{a}." if a else ""
    return f"({p}total_inc_vat/{VAT_DIVISOR} - COALESCE({p}service_charge,0))"


def net_revenue_sum(a: str = "st") -> str:
    """Net revenue over a GROUP. Same rule as net_revenue_row, summed."""
    return f"(SUM({a}.total_inc_vat)/{VAT_DIVISOR} - SUM(COALESCE({a}.service_charge,0)))"


# ─── Buffet tier codes ───────────────────────────────────────────────────────
# A buffet bill carries the tier as its own line (BUFFET799…) AND the dishes the
# guest ate as B0xxx lines. Revenue is on the tier line; consumption (and so
# cost) is on the dish lines. Costing the tier line as well would double-count —
# 825M of phantom COGS the first time it happened — so every cost expression
# zeroes these codes. The SQL forms are DERIVED from the tuples: hand-maintaining
# two lists that must agree is exactly the drift this module exists to end.
EASY_TIER_CODES = ('BUFFET', 'BUFFET350', 'BUFFET500', 'BUFFET590', 'BUFFET799', 'BUFFETCHILD')
LEGACY_TIER_CODES = ('B01', 'B02', 'B03', 'B07', 'B11')     # tier-level codes with no item breakdown
ALL_TIER_CODES = EASY_TIER_CODES + LEGACY_TIER_CODES
EASY_TIER_CODES_SQL = "(" + ",".join(f"'{c}'" for c in EASY_TIER_CODES) + ")"
ALL_TIER_CODES_SQL = "(" + ",".join(f"'{c}'" for c in ALL_TIER_CODES) + ")"


def is_tier_code(code: str) -> bool:
    """Package predicate: the known codes PLUS the BUFFET* prefix. Easy emits
    package variants the tuple never listed (BUFFETCHILDFREE) that are still
    packages, never costed line items. Exact match keeps real B0xxx dishes."""
    c = code or ""
    return c in ALL_TIER_CODES or c.startswith("BUFFET")


def tier_code_sql(expr: str) -> str:
    """SQL twin of is_tier_code for a sku_code expression. Until 2026-09 the app
    used the IN-list and the sync used the prefix — two definitions of the same
    rule, which agreed only because no variant carried a cost yet."""
    return f"({expr} IN {ALL_TIER_CODES_SQL} OR {expr} LIKE 'BUFFET%')"


# ─── Effective unit cost and COGS ────────────────────────────────────────────
# One SKU, one cost, resolved in this order:
#   1. sku_effective_cost — precomputed at startup and on every cost/BoM edit
#      (one rowid lookup instead of three correlated subqueries per sales row)
#   2. sku_cost.unit_cost, when non-zero — the owner-maintained cost table
#   3. the SKU's own BoM recipe (sum of ingredient costs)
#   4. a recipe reached through bom_sku_alias (a renamed dish keeps its recipe)
#   5. 0 — genuinely uncosted; never NULL, so callers can multiply safely
# Steps 2-4 are kept as the fallback so the expression stays correct when the
# precomputed table is absent (tests, offline scripts, a SKU added since the
# last build). COALESCE short-circuits, so they cost nothing once 1 hits.

def raw_unit_cost_sql(s: str = "s") -> str:
    """The SOURCE chain for a row of `skus` aliased `s` (needs sku_id and
    sku_code): owner cost table, own recipe, a recipe reached by alias, 0.
    This is what sku_effective_cost is BUILT from, so it must never read
    that table itself."""
    return (
        "COALESCE("
        f"(SELECT NULLIF(sc2.unit_cost, 0) FROM sku_cost sc2 WHERE sc2.sku_id = {s}.sku_id), "
        f"(SELECT SUM(bi.ingredient_cost) FROM bom_ingredients bi WHERE bi.sku_id = {s}.sku_id), "
        f"(SELECT SUM(bi2.ingredient_cost) FROM bom_ingredients bi2 "
        f"   JOIN bom_sku_alias a ON a.resolved_sku_id = bi2.sku_id WHERE a.bom_sku_code = {s}.sku_code), "
        "0)"
    )


def effective_unit_cost_sql(row: str = "ssm") -> str:
    """Effective unit cost for a sales row whose alias is `row` (needs {row}.sku_id)."""
    return (
        "COALESCE("
        f"    (SELECT sec.recipe_cost FROM sku_effective_cost sec WHERE sec.sku_id = {row}.sku_id), "
        f"    (SELECT NULLIF(sc2.unit_cost, 0) FROM sku_cost sc2 WHERE sc2.sku_id = {row}.sku_id), "
        f"    (SELECT SUM(bi.ingredient_cost) FROM bom_ingredients bi WHERE bi.sku_id = {row}.sku_id), "
        f"    (SELECT SUM(bi2.ingredient_cost) FROM bom_ingredients bi2 "
        f"       JOIN bom_sku_alias a ON a.resolved_sku_id = bi2.sku_id "
        f"       JOIN skus s_al ON s_al.sku_id = {row}.sku_id WHERE a.bom_sku_code = s_al.sku_code), "
        "    0)"
    )


def cogs_expr(ssm: str = "ssm", sk: str = "sk") -> str:
    """THE COGS expression: qty × effective unit cost, with Easy tier rows at 0.

    Self-contained — resolves sku_code, recipe and unit cost through correlated
    subqueries, so it drops into any query that exposes the sales row alias.
    `sk` is accepted for signature compatibility with the older call sites and
    is not needed.
    """
    return (
        f"CASE "
        f"  WHEN {ssm}.source = 'Easy' AND "
        f"       {tier_code_sql(f'(SELECT sk_t.sku_code FROM skus sk_t WHERE sk_t.sku_id = {ssm}.sku_id)')} THEN 0 "
        f"  ELSE {ssm}.qty * {effective_unit_cost_sql(ssm)} "
        f"END"
    )


# ─── Scope: branch and branch group ──────────────────────────────────────────
# Every report narrows to one branch and/or one group (master | franchise).
# The caller has already `JOIN branches b`; this appends the predicates and
# binds the parameters, so the two never drift apart.

def scope_filter(filters: list, params: dict, branch: Optional[str] = None,
                 group: Optional[str] = None, alias: str = "b") -> None:
    if branch:
        filters.append(f"{alias}.name=:branch")
        params["branch"] = branch
    if group:
        filters.append(f"COALESCE({alias}.branch_type,'master') = :branch_group")
        params["branch_group"] = group
