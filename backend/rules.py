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


def avg_ticket_sql(a: str = "st") -> str:
    """Average net revenue per bill over a GROUP — net_revenue_row averaged,
    written as two AVGs so SQLite keeps a single pass."""
    p = f"{a}." if a else ""
    return f"(AVG({p}total_inc_vat)/{VAT_DIVISOR} - AVG(COALESCE({p}service_charge,0)))"


# ─── Discount policy ─────────────────────────────────────────────────────────
# The owner's ceiling (2026-07): discounts should stay within 2.5% of pre-discount
# net sales. Every reading of discount share — the Discounts tab, the management
# report, the discount review — compares against this ONE number and reports the
# baht given beyond it, so "high" means the same thing on every page.
DISCOUNT_POLICY_PCT = 2.5


def discount_policy_pct(con) -> float:
    """The ceiling in force: app_settings['discount_policy_pct'] (Settings →
    ค่าเริ่มต้น), else DISCOUNT_POLICY_PCT. Out-of-range or unreadable → default."""
    try:
        row = con.execute("SELECT value FROM app_settings WHERE key='discount_policy_pct'").fetchone()
        v = float(row[0]) if row and row[0] not in (None, "") else DISCOUNT_POLICY_PCT
    except Exception:
        return DISCOUNT_POLICY_PCT
    return v if 0 <= v <= 100 else DISCOUNT_POLICY_PCT


# Food-cost band for THIS business. The home page used to judge food cost against
# 38% / 45% — à-la-carte norms — which left the tile permanently red for a buffet
# whose honest recipe cost runs 52-68%, and put "ต้นทุนอาหารสูง" at the top of the
# action queue every single day. 55-70 is the band the buffet tab has always used.
FOOD_COST_BAND = (55.0, 70.0)


def food_cost_band(con) -> tuple:
    """(lo, hi) in force: app_settings food_cost_band_lo / _hi (Settings →
    ค่าเริ่มต้น), else FOOD_COST_BAND. Unreadable, out of range or lo >= hi → default."""
    try:
        got = dict(con.execute("SELECT key, value FROM app_settings "
                               "WHERE key IN ('food_cost_band_lo','food_cost_band_hi')").fetchall())
        lo = float(got.get("food_cost_band_lo") or FOOD_COST_BAND[0])
        hi = float(got.get("food_cost_band_hi") or FOOD_COST_BAND[1])
    except Exception:
        return FOOD_COST_BAND
    return (lo, hi) if 0 < lo < hi <= 100 else FOOD_COST_BAND


def discount_excess(discount: float, base: float, policy_pct: float) -> float:
    """Baht given beyond the ceiling: discount − policy% × pre-discount base, never negative."""
    return round(max(0.0, (discount or 0.0) - (policy_pct / 100.0) * (base or 0.0)), 2)


def discount_base_row(a: str = "st") -> str:
    """Per-bill PRE-discount net revenue — what the bill was worth ex-VAT and
    ex-service before the discount came off; the denominator of every discount
    share. Until 2026-09 six copies wrote ((total - service)/1.07 + discount),
    which de-VATs the service charge as well; the charge is already ex-VAT."""
    p = f"{a}." if a else ""
    return f"({net_revenue_row(a)} + {p}discount)"


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


# ─── Buffet menu eras: the price list in force on a given day ────────────────
# The Grand Menu went live on 2026-09-01 and REPLACED the old price list — the
# old prices are not sold any more. The two lists are also quoted on different
# bases, which is why a single tuple cannot serve both:
#
#   until 2026-08-31   350 / 590 / 649 / 689 / 799 / 999   price BEFORE the 5%
#                                                          service charge and 7% VAT
#   from  2026-09-01   459 / 629 / 789 / 999 / 1299        NET price — service
#                                                          charge and VAT already in
#
# Everything that names a buffet price — the management report §11, the price-tier
# report, the menu-impact tier filter, the buffet mix chart, the up-sell simulator —
# reads the list from HERE, keyed on the day it is reporting, so no section can go
# on offering a retired price after 1 Sep 2026.

GRAND_MENU_FROM = "2026-09-01"          # Grand Menu 2026-09 [[grand_menu_2026_09]]
PRICE_TIERS_LEGACY = (350, 590, 649, 689, 799, 999)   # base price (pre SC + VAT)
PRICE_TIERS_GRAND = (459, 629, 789, 999, 1299)        # NET price (SC + VAT inside)
SERVICE_CHARGE_MULT = 1.05              # 5% service charge, when the bill carries one
TIER_TOL = 3.0                          # ฿ tolerance around a legacy (base) price
TIER_TOL_NET = 4.0                      # ฿ tolerance on a net price (±3 grossed up)
# Non-price buckets every tier list carries, in display order after the prices.
TIER_EXTRA = ("promo", "child")
TIER_LABEL_TH = {"promo": "โปรฯ/ราคาอื่น", "child": "เด็กล้วน/อื่นๆ"}
TIER_LABEL_EN = {"promo": "Promo/other price", "child": "Child-only/other"}


def _as_day(day) -> str:
    """A day as an ISO string. Accepts date/datetime, 'YYYY-MM-DD' or 'YYYY-MM'."""
    s = day.isoformat() if hasattr(day, "isoformat") else str(day or "")
    return f"{s}-01" if len(s) == 7 else s


def is_grand_menu(day) -> bool:
    """True when `day` (date, 'YYYY-MM-DD' or month 'YYYY-MM') is on the Grand Menu."""
    return _as_day(day) >= GRAND_MENU_FROM


def price_tiers(day) -> tuple:
    """The price list actually on sale on `day` — never both."""
    return PRICE_TIERS_GRAND if is_grand_menu(day) else PRICE_TIERS_LEGACY


def price_tiers_between(d_from=None, d_to=None) -> tuple:
    """The prices sold anywhere in [d_from, d_to], richest first.

    A window that ends before 1 Sep 2026 gets the legacy list only; one that
    starts on or after it gets the Grand Menu only; a window straddling the
    changeover legitimately carries both, because both were sold in it.
    """
    lo, hi = _as_day(d_from) if d_from else "", _as_day(d_to) if d_to else "9999-12-31"
    tiers: set = set()
    if lo < GRAND_MENU_FROM:
        tiers |= set(PRICE_TIERS_LEGACY)
    if hi >= GRAND_MENU_FROM:
        tiers |= set(PRICE_TIERS_GRAND)
    return tuple(sorted(tiers, reverse=True))


def tier_codes_between(d_from=None, d_to=None, extra: bool = True) -> tuple:
    """price_tiers_between as the string tier codes the fact tables store,
    followed by the 'promo' / 'child' buckets."""
    codes = tuple(str(p) for p in price_tiers_between(d_from, d_to))
    return codes + TIER_EXTRA if extra else codes


def tier_label(t, th: bool = True) -> str:
    """'789' → '฿789'; 'promo'/'child' → the bucket's name."""
    lbl = (TIER_LABEL_TH if th else TIER_LABEL_EN).get(t)
    if lbl:
        return lbl
    return f"฿{int(t):,}" if str(t).isdigit() else str(t)


def net_to_base(net: float, service_charge: bool = True) -> float:
    """A Grand Menu net price back to the base the POS stores: ฿789 → ฿702.27."""
    return (net or 0.0) / VAT_DIVISOR / (SERVICE_CHARGE_MULT if service_charge else 1.0)


def base_to_net(base: float, service_charge: bool = True) -> float:
    """The inverse — a stored base price up to what the Grand Menu quotes."""
    return (base or 0.0) * VAT_DIVISOR * (SERVICE_CHARGE_MULT if service_charge else 1.0)


def tier_of_price(price: float, day, tol: Optional[float] = None) -> str:
    """Bucket a per-head price into the tier on sale that day, or 'promo'.

    `price` is on the basis that day's menu quotes: the stored base price before
    2026-09-01, the net price on and after it. Nothing within tolerance = 'promo',
    which is the whole point — a ฿590 head in September is a discount off ฿629,
    not a ฿590 tier, because ฿590 is not sold any more.
    """
    grand = is_grand_menu(day)
    tol = tol if tol is not None else (TIER_TOL_NET if grand else TIER_TOL)
    tiers = PRICE_TIERS_GRAND if grand else PRICE_TIERS_LEGACY
    best = min(tiers, key=lambda t: abs((price or 0.0) - t))
    return str(best) if abs((price or 0.0) - best) <= tol else "promo"


def upsell_pair(day) -> tuple:
    """(from_tier, to_tier) for the up-sell what-if: the mainstream tier and the
    one above it on the menu in force — ('590','799') before the Grand Menu,
    ('629','789') from 1 Sep 2026. Stated once so the simulator cannot keep
    modelling a shift between two prices that no longer exist."""
    return ("629", "789") if is_grand_menu(day) else ("590", "799")
