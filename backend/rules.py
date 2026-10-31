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
VAT_DIVISOR = 1.0 + VAT_RATE          # 1.07 — inc-VAT ex-VAT divisor


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


# ─── Per-bill joins ──────────────────────────────────────────────────────────
# receipt_no is a PER-BRANCH sequence ("<day>-<SaleNum>-<SaleId>"): the same text
# turns up at two branches (24 pairs in the 31 days to 2026-10-22). Every join of
# a per-bill table to sales_transaction must also match the branch, or a bill takes
# the other branch's cost (2026-10-20-691020021-23842: branch 18's ฿155 bill read
# branch 12's ฿1,430 cost).

def bill_item_branch_sql(con, i: str = "i", t: str = "t") -> str:
    """` AND <branch match>` for a sale_bill_item join, or "" while the table predates
    its branch_id column (a DB synced before 2026-10-26: the app must keep serving it
    until the next sync adds the column). Lines staged before then carry NULL and
    match on receipt_no alone, as before."""
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(sale_bill_item)").fetchall()}
    except Exception:
        return ""
    if "branch_id" not in cols:
        return ""
    return f" AND ({i}.branch_id IS NULL OR {i}.branch_id = {t}.branch_id)"


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


# ONE food-cost target for THIS business (owner 2026-10-25: "food-cost target 58%"). Food cost %
# at or below it is good (ในเป้า), above it is bad (เกินเป้า x.x จุด). It replaced the 55-70 band
# (and the hard-coded ~50% of the management report): every page, report and chart reads this one
# number. Settings → ค่าเริ่มต้น writes app_settings['food_cost_target']; the old band keys
# (food_cost_band_lo/_hi, cost_target_lo/_hi) are not read - unset means the default below.
FOOD_COST_TARGET = 58.0
FOOD_COST_TARGET_MIN, FOOD_COST_TARGET_MAX = 20.0, 90.0


def valid_food_cost_target(v) -> bool:
    """True when v reads as a number inside 20-90 (the range Settings accepts)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return FOOD_COST_TARGET_MIN <= f <= FOOD_COST_TARGET_MAX


def food_cost_target(con) -> float:
    """The target in force: app_settings['food_cost_target'], else FOOD_COST_TARGET.
    Unreadable, blank or outside 20-90 → the default, never an error."""
    try:
        row = con.execute("SELECT value FROM app_settings WHERE key='food_cost_target'").fetchone()
        v = row[0] if row else None
    except Exception:
        return FOOD_COST_TARGET
    return float(v) if v not in (None, "") and valid_food_cost_target(v) else FOOD_COST_TARGET


def food_cost_over_pp(pct, target):
    """Points above the target (> 0 = เกินเป้า), 0.0 when within it, None without a figure."""
    if pct is None:
        return None
    return round(max(0.0, float(pct) - float(target)), 1)


def food_cost_status_th(pct, target) -> str:
    """'ในเป้า' at or below the target, 'เกินเป้า x.x จุด' above it, '—' without a figure."""
    if pct is None:
        return "—"
    return "ในเป้า" if float(pct) <= float(target) else f"เกินเป้า {float(pct) - float(target):.1f} จุด"


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


# ─── Non-food lines (fees, penalties, deposits, coupons) ─────────────────────
# Owner 2026-10-25 (the menu-spike card): a line that is not a dish - the buffet
# leftover penalty "(OT) ค่าปรับบุฟเฟ่ต์ 789 Net (10 กรัม)", room / corkage fees,
# deposits, damage charges, coupon value lines, the service charge - is never a
# menu in a menu list. Easy files all of these under the "(OT)" (other) prefix,
# and older codes (OT0001 ...) carry the same words without it. ONE predicate,
# with its SQL twin derived from the same tuples; packages (is_tier_code) too.
NON_FOOD_DESC_WORDS = ("ค่าปรับ", "ค่าบริการ", "ค่ามัดจำ", "ค่าเสียหาย", "ค่าเปิด", "คูปอง")
NON_FOOD_DESC_PREFIXES = ("(OT)",)
NON_FOOD_CODES = ("SERVICECHARGE", "SERVICE")


def is_non_food_line(code: str, desc: str) -> bool:
    """True for a sales line that is not a dish: a buffet package (is_tier_code), the service charge, or a
    description holding a fee word (NON_FOOD_DESC_WORDS) or starting with an Easy "other" prefix."""
    c, d = code or "", (desc or "").strip()
    if is_tier_code(c) or c.upper() in NON_FOOD_CODES:
        return True
    du = d.upper()
    return any(du.startswith(p) for p in NON_FOOD_DESC_PREFIXES) or any(w in d for w in NON_FOOD_DESC_WORDS)


def non_food_sql(code_expr: str, desc_expr: str) -> str:
    """SQL twin of is_non_food_line for a sku_code and a description expression."""
    d = f"COALESCE({desc_expr},'')"
    words = " OR ".join(f"instr({d}, '{w}') > 0" for w in NON_FOOD_DESC_WORDS)
    pre = " OR ".join(f"UPPER(TRIM({d})) LIKE '{p}%'" for p in NON_FOOD_DESC_PREFIXES)
    codes = "(" + ",".join(f"'{c}'" for c in NON_FOOD_CODES) + ")"
    c = f"COALESCE({code_expr},'')"
    return f"({tier_code_sql(c)} OR UPPER({c}) IN {codes} OR {pre} OR {words})"


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


def dated_cost_sql(row: str, month: str) -> str:
    """The BoM Center cut-over (backend/bom_center/dated.py): from the Grand Menu
    month on, the analyst's imported dish cost (bc_sku_cost_list), else a RELEASED
    version's cost for that SKU and month; NULL otherwise —
    and NULL without even looking for any month before the cut-over, so August
    2026 and earlier keep the old engine's numbers byte for byte."""
    # the latest month on or before the row's (a version stays in force until the next one), so a new
    # month needs no new row to keep its cost. PR-S2: the analyst's imported dish cost (bc_sku_cost_list)
    # beats the released version, per SKU; both follow the same "latest month <=" rule.
    return (f"(CASE WHEN {month} >= '{GRAND_MENU_FROM[:7]}' THEN COALESCE("
            f"(SELECT bcl.unit_cost FROM bc_sku_cost_list bcl WHERE bcl.sku_id = {row}.sku_id"
            f" AND bcl.month <= {month} ORDER BY bcl.month DESC LIMIT 1), "
            f"(SELECT bcm.unit_cost FROM bc_sku_cost_month bcm WHERE bcm.sku_id = {row}.sku_id"
            f" AND bcm.month <= {month} ORDER BY bcm.month DESC LIMIT 1)) "
            f"END)")


def effective_unit_cost_sql(row: str = "ssm", month: str = None) -> str:
    """Effective unit cost for a sales row whose alias is `row` (needs {row}.sku_id).
    `month` — an SQL expression for the row's 'YYYY-MM' — turns on the dated
    (released) cost for months on/after the Grand Menu cut-over."""
    dated = f"    {dated_cost_sql(row, month)}, " if month else ""
    return (
        "COALESCE("
        + dated +
        f"    (SELECT sec.recipe_cost FROM sku_effective_cost sec WHERE sec.sku_id = {row}.sku_id), "
        f"    (SELECT NULLIF(sc2.unit_cost, 0) FROM sku_cost sc2 WHERE sc2.sku_id = {row}.sku_id), "
        f"    (SELECT SUM(bi.ingredient_cost) FROM bom_ingredients bi WHERE bi.sku_id = {row}.sku_id), "
        f"    (SELECT SUM(bi2.ingredient_cost) FROM bom_ingredients bi2 "
        f"       JOIN bom_sku_alias a ON a.resolved_sku_id = bi2.sku_id "
        f"       JOIN skus s_al ON s_al.sku_id = {row}.sku_id WHERE a.bom_sku_code = s_al.sku_code), "
        "    0)"
    )


def cogs_expr(ssm: str = "ssm", sk: str = "sk", month: str = None) -> str:
    """THE COGS expression: qty × effective unit cost, with Easy tier rows at 0.

    Self-contained — resolves sku_code, recipe and unit cost through correlated
    subqueries, so it drops into any query that exposes the sales row alias.
    `sk` is accepted for signature compatibility with the older call sites and
    is not needed.

    The row is a monthly roll-up (it has `source`), so it has `period_month`:
    that is the month the dated (released) cost is looked up for — September
    2026 on, a released version's cost; earlier months, the old chain untouched.
    """
    month = month or f"{ssm}.period_month"
    return (
        f"CASE "
        f"  WHEN {ssm}.source = 'Easy' AND "
        f"       {tier_code_sql(f'(SELECT sk_t.sku_code FROM skus sk_t WHERE sk_t.sku_id = {ssm}.sku_id)')} THEN 0 "
        f"  ELSE {ssm}.qty * {effective_unit_cost_sql(ssm, month)} "
        f"END"
    )


# ─── Cost coverage: how much of what was sold carries a cost ─────────────────
# Until 2026-10 the app had three answers: the home page asked the cost engine
# (99.1% of September plates), the P&L counted only lines with a recipe and
# counted buffet packages as gaps (86.3%), and the coverage tab counted any
# sku_cost row, even a ฿0 one, and ignored recipes and aliases. One definition now:
#   costed    = the engine's unit cost is above ฿0 (same number COGS multiplies)
#   in scope  = the line SHOULD carry a cost; these are ฿0 on purpose:
#               buffet packages (the dishes eaten are costed instead), service
#               charge / discount / deposit lines, penalty and room fees, and
#               sauces whose cost is already inside the dishes they come with.
# Plates are the measure for the buffet (its dishes carry ฿0 revenue); revenue
# for à-la-carte, delivery and drinks. Report both from the same predicates.

# Owner ruling 2026-09-17: these sauces are booked inside the dishes they are
# served with, so costing the line too would count them twice.
ZERO_COST_BY_DESIGN = (
    'OTHBFAPP00015', 'SV0007', 'AS-ALL-007',      # สเต็กซอส
    'OTHBFAPP00019', 'SV0012', 'AS-ALL-012',      # ซอส มายองเนส
    'OTHBFSPC00068',                              # ซอสมะเขือเทศ
)
NOT_COSTED_CODES = ('SERVICECHARGE', 'SP', 'SERVICE', 'DISCOUNT', 'DEPOSIT')


def needs_cost_sql(code: str = "sk.sku_code") -> str:
    """True when a sold line should carry a cost (see the block comment above)."""
    excluded = "(" + ",".join(f"'{c}'" for c in ZERO_COST_BY_DESIGN) + ")"
    return (f"(NOT {tier_code_sql(code)}"
            f" AND UPPER({code}) NOT IN (" + ",".join(f"'{c}'" for c in NOT_COSTED_CODES) + ")"
            f" AND {code} NOT GLOB 'OT[0-9]*' AND {code} NOT GLOB 'OTHOTOTH*'"
            f" AND {code} NOT IN {excluded})")


def costed_sql(row: str = "ssm", month: str = None) -> str:
    """True when the engine has a unit cost above ฿0 for the sales row `row`.

    The month matters: the analyst's imported dish cost (bc_sku_cost_list) and a released
    version are read only through dated_cost_sql, which needs the row's month. Without it a
    dish costed only by that list counted in COGS but stayed "uncosted" here (PL1 #1). `month`
    defaults to the row's own period_month: every roll-up this is used on carries one."""
    return f"({effective_unit_cost_sql(row, month or f'{row}.period_month')} > 0)"


def coverage_sums_sql(row: str = "ssm", code: str = "sk.sku_code") -> str:
    """Four SELECT columns: plates/revenue in scope, and the part of each that is
    costed. Divide with coverage_from_sums()."""
    need, ok = needs_cost_sql(code), costed_sql(row)
    return (f"SUM(CASE WHEN {need} AND {row}.qty > 0 THEN {row}.qty ELSE 0 END) AS cov_plates, "
            f"SUM(CASE WHEN {need} AND {row}.qty > 0 AND {ok} THEN {row}.qty ELSE 0 END) AS cov_plates_costed, "
            f"SUM(CASE WHEN {need} THEN {row}.subtotal_ex_vat ELSE 0 END) AS cov_rev, "
            f"SUM(CASE WHEN {need} AND {ok} THEN {row}.subtotal_ex_vat ELSE 0 END) AS cov_rev_costed")


def coverage_from_sums(r) -> dict:
    """Turn a row carrying coverage_sums_sql's columns into percentages."""
    def g(k):
        try:
            return float(r[k] or 0)
        except (KeyError, IndexError, TypeError):
            return 0.0
    p, pc, v, vc = g("cov_plates"), g("cov_plates_costed"), g("cov_rev"), g("cov_rev_costed")
    return {
        "plates_pct":       round(pc / p * 100, 1) if p else None,
        "uncosted_plates":  round(p - pc),
        "revenue_pct":      round(vc / v * 100, 1) if v else None,
        "uncosted_revenue": round(v - vc, 2),
    }


# ─── Scope: branch and branch group ──────────────────────────────────────────
# Every report narrows to one branch and/or one group (master | franchise).
# The caller has already `JOIN branches b`; this appends the predicates and
# binds the parameters, so the two never drift apart.

# ---------------------------------------------------------------------------
# The day a "daily" page describes (owner rule 2026-10-02): the latest day that
# has FINISHED, never the day still trading. The live sync runs at 06:00 / 12:00 /
# 16:00, so from noon the newest day in the database is today with a few hours
# of bills — shown as "yesterday" it read as a chain-wide collapse (-99.9%).
# Thai calendar day = UTC+7, the same clock the management report anchors on.
# ---------------------------------------------------------------------------
TH_TODAY_SQL = "date('now','+7 hours')"


def closed_day_clause(col: str = "t.sold_at") -> str:
    """SQL predicate: `col` falls on a day strictly before Thai today."""
    return f"date({col}) < {TH_TODAY_SQL}"


def latest_settled_day(con, where: str = "t.source='Easy' AND b.name!='ALL'", params: Optional[dict] = None,
                       min_ratio: float = 0.4, lookback: int = 10) -> dict:
    """The newest FINISHED day whose bills have actually arrived.

    closed_day_clause keeps the day still trading out of the headline; this also steps
    back over a finished day the POS has not posted yet. The 06:00 sync runs before the
    previous day lands in MSSQL — 2026-10-08 the 7th held 7 bills chain-wide and the
    Daily Brief (desktop and phone) headlined it as a −99.9 % day. A day with fewer
    than `min_ratio` × the mean bills of the up-to-7 days before it is skipped.
    Returns {"day", "skipped_day", "skipped_bills"}; day is None when there is no data.
    `where` uses the aliases t (sales_transaction) and b (branches)."""
    rows = con.execute(
        f"SELECT date(t.sold_at) AS d, COUNT(*) AS n FROM sales_transaction t "
        f"JOIN branches b ON b.branch_id = t.branch_id "
        f"WHERE {where} AND {closed_day_clause('t.sold_at')} "
        f"GROUP BY date(t.sold_at) ORDER BY d DESC LIMIT :_lim",
        {**(params or {}), "_lim": lookback + 7}).fetchall()
    counts = [(r[0], r[1]) for r in rows]   # newest first
    skipped_day, skipped_bills = None, None
    for i, (d, n) in enumerate(counts[:lookback]):
        prior = [c for _, c in counts[i + 1:i + 8]]
        if not prior or n >= min_ratio * (sum(prior) / len(prior)):
            return {"day": d, "skipped_day": skipped_day, "skipped_bills": skipped_bills}
        if skipped_day is None:
            skipped_day, skipped_bills = d, n
    return {"day": counts[0][0] if counts else None, "skipped_day": None, "skipped_bills": None}


# ─── The ONE daily baseline: "ปกติ" (owner 2026-10-31, docs/UI_REVIEW_MOBILE_2026-10-31.md no. 12) ─────────────────────
# Every DAILY comparison in the app (phone hero and its เมื่อวาน detail, the branch scorecard, the Daily Brief, the
# management report's daily mode, ร้านของฉัน · สรุปประจำวัน, the morning push) compares a day with ONE normal: the MEDIAN of
# the same weekday over the 8 weeks before it. The owner proposed the 8-week same-weekday average; the median keeps one
# holiday or half-closed day from dragging the normal. A same weekday with no bills (the branch was shut, or its data never
# came) is skipped, not counted as zero. Fewer than 2 such days = no verdict (the figure shows, the pill says "ยังไม่มีฐาน");
# 2..7 = a verdict labelled "ปกติจาก n สัปดาห์". Weekly / monthly / yearly comparisons are a different question and keep
# their own bases (periods.py, the weekday-fair MTD pace, month_projection).
BASELINE_WEEKS = 8                 # same weekdays looked back over
BASELINE_MIN_WEEKS = 2             # fewer usable weeks = no verdict
BELOW_NORMAL_PCT = -8.0            # a day this far under its normal = "ต่ำกว่าปกติ" (the Daily Brief's rule since 2026-09)
ABOVE_NORMAL_PCT = 10.0            # a day this far over its normal = "สูงกว่าปกติ"
BIG_BELOW_NORMAL_PCT = -15.0       # red, not amber


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def baseline_dates(day, weeks: int = BASELINE_WEEKS) -> list:
    """The same weekday on each of the `weeks` weeks before `day` (ISO strings, newest first)."""
    import datetime as _dt
    d = _dt.date.fromisoformat(str(day)[:10])
    return [(d - _dt.timedelta(days=7 * k)).isoformat() for k in range(1, weeks + 1)]


def baseline_label(n_weeks: Optional[int], short: bool = False) -> str:
    """The words for the normal: "เทียบปกติ (ค่ากลาง 8 สัปดาห์)" on the desktop, "เทียบปกติ" on the phone,
    "เทียบปกติจาก n สัปดาห์" while fewer than 8 weeks exist, "ยังไม่มีฐาน" under 2."""
    n = n_weeks or 0
    if n < BASELINE_MIN_WEEKS:
        return "ยังไม่มีฐาน"
    if n < BASELINE_WEEKS:
        return f"เทียบปกติจาก {n} สัปดาห์"
    return "เทียบปกติ" if short else f"เทียบปกติ (ค่ากลาง {BASELINE_WEEKS} สัปดาห์)"


def _pct_vs(cur, base):
    return round((cur - base) / base * 100, 1) if (cur is not None and base) else None


def baseline_from_days(by_day: dict, day: str, weeks: int = BASELINE_WEEKS) -> dict:
    """The baseline of `day` from per-day sums {iso_day: {"rev", "cust", "bills", "disc"}} (one branch, or the chain
    summed per day). Pure Python: the scorecard, the franchise table and the tests call it on rows they already hold.
    → {"day", "revenue", "customers", "bills", "rev_per_head", "discount_pct", "normal": {revenue, customers,
       rev_per_head, discount_pct, n_weeks, dates, weeks}, "vs_normal_pct", "vs_normal_baht", "cust_vs_normal_pct",
       "rph_vs_normal_pct", "has_verdict", "below_normal", "status", "label", "label_short"}
    status: "nobase" (< 2 usable weeks) | "missing" (no bills on the day) | "down" | "up" | "ok"."""
    cur = by_day.get(day) or {}
    used = []
    for d in baseline_dates(day, weeks):
        x = by_day.get(d)
        if x and (x.get("bills") or 0) > 0:
            used.append((d, x))
    rev = float(cur.get("rev") or 0)
    cust = float(cur.get("cust") or 0)
    bills = int(cur.get("bills") or 0)
    disc = float(cur.get("disc") or 0)

    def dshare(r, dd):
        return (dd / (r + dd) * 100) if (r + dd) > 0 else None
    n = len(used)
    m_rev = _median([float(x.get("rev") or 0) for _, x in used])
    m_cust = _median([float(x.get("cust") or 0) for _, x in used])
    m_rph = _median([(float(x.get("rev") or 0) / x["cust"]) if x.get("cust") else None for _, x in used])
    m_disc = _median([dshare(float(x.get("rev") or 0), float(x.get("disc") or 0)) for _, x in used])
    m_bills = _median([float(x.get("bills") or 0) for _, x in used])
    m_tkt = _median([(float(x.get("rev") or 0) / x["bills"]) if x.get("bills") else None for _, x in used])
    ok = n >= BASELINE_MIN_WEEKS and bool(m_rev)
    live = ok and bills > 0
    vs = _pct_vs(rev, m_rev) if live else None
    rph = rev / cust if cust else None
    ds = dshare(rev, disc)
    status = ("nobase" if not ok else "missing" if bills <= 0 else
              "down" if vs is not None and vs <= BELOW_NORMAL_PCT else
              "up" if vs is not None and vs >= ABOVE_NORMAL_PCT else "ok")
    return {
        "day": day, "revenue": round(rev, 2), "customers": cust, "bills": bills,
        "rev_per_head": round(rph, 1) if rph is not None else None,
        "discount_pct": round(ds, 1) if ds is not None else None,
        "normal": {"revenue": round(m_rev, 2) if m_rev is not None else None,
                   "customers": round(m_cust, 1) if m_cust is not None else None,
                   "rev_per_head": round(m_rph, 1) if m_rph is not None else None,
                   "discount_pct": round(m_disc, 1) if m_disc is not None else None,
                   "bills": round(m_bills, 1) if m_bills is not None else None,
                   "avg_ticket": round(m_tkt, 1) if m_tkt is not None else None,
                   "n_weeks": n, "dates": [d for d, _ in used], "weeks": weeks},
        "vs_normal_pct": vs,
        "vs_normal_baht": round(rev - m_rev) if vs is not None else None,
        "cust_vs_normal_pct": _pct_vs(cust, m_cust) if live else None,
        "rph_vs_normal_pct": _pct_vs(rph, m_rph) if (live and rph is not None) else None,
        "has_verdict": ok,
        "below_normal": status == "down",
        "status": status,
        "label": baseline_label(n), "label_short": baseline_label(n, short=True),
    }


def daily_series(con, where: str, params: Optional[dict], dates: list, alias: str = "t") -> dict:
    """Per-branch per-day sums for the given ISO dates: {branch: {day: {"rev", "cust", "bills", "disc"}}}.
    `where` uses the aliases `alias` (sales_transaction, default t) and b (branches); revenue = net_revenue_sum."""
    dates = sorted(set(str(d)[:10] for d in dates if d))
    if not dates:
        return {}
    ph = ", ".join(f":_bd{i}" for i in range(len(dates)))
    q = {**(params or {}), **{f"_bd{i}": d for i, d in enumerate(dates)}}
    a = alias
    rows = con.execute(
        f"SELECT b.name AS branch, date({a}.sold_at) AS d, {net_revenue_sum(a)} AS rev, "
        f"COALESCE(SUM({a}.customer_count),0) AS cust, COUNT(*) AS bills, COALESCE(SUM({a}.discount),0) AS disc "
        f"FROM sales_transaction {a} JOIN branches b ON b.branch_id={a}.branch_id "
        f"WHERE {where} AND date({a}.sold_at) IN ({ph}) GROUP BY b.name, date({a}.sold_at)", q).fetchall()
    per: dict = {}
    for r in rows:
        per.setdefault(r[0], {})[r[1]] = {"rev": r[2] or 0, "cust": r[3] or 0, "bills": r[4] or 0, "disc": r[5] or 0}
    return per


def chain_days(per: dict) -> dict:
    """daily_series summed across branches per day (the chain-wide variant: sum per day, then the median)."""
    out: dict = {}
    for dm in per.values():
        for d, x in dm.items():
            c = out.setdefault(d, {"rev": 0.0, "cust": 0.0, "bills": 0, "disc": 0.0})
            for k in c:
                c[k] += x[k]
    return out


def chain_baseline(con, per: dict, day: str, weeks: int = BASELINE_WEEKS, keep: Optional[str] = None) -> dict:
    """The chain-wide baseline from daily_series rows: the branches trading now (one shut over the day and the 7 before -
    closed_branches, the scorecard's own rows - does not hold up the normal with the weeks it traded in), summed per
    day, then the median across days. `keep` = the branch the page is filtered to (never left out)."""
    import datetime as _dt
    wk0 = (_dt.date.fromisoformat(day) - _dt.timedelta(days=7)).isoformat()
    shut = closed_branches(con, [(wk0, day)], keep=keep)
    return baseline_from_days(chain_days({nm: dm for nm, dm in per.items() if nm not in shut}), day, weeks)


def daily_baseline(con, where: str = "t.source='Easy' AND b.name!='ALL'", params: Optional[dict] = None,
                   day: Optional[str] = None, weeks: int = BASELINE_WEEKS, by_branch: bool = False,
                   alias: str = "t", keep: Optional[str] = None):
    """THE daily baseline ("ปกติ") of `day` - the median of the same weekday over the `weeks` weeks before it (see the
    block above). `day` defaults to the last FINISHED day whose bills arrived (latest_settled_day on the same scope).
    `where` uses the aliases `alias` (sales_transaction, default t) and b (branches); revenue = net_revenue_sum (ex-VAT,
    less service charge).
    Chain-wide (by_branch=False): the scope summed per day, then the median across days → baseline_from_days' dict.
    by_branch=True → {branch_name: baseline_from_days' dict}, each branch on its own days. None when there is no day."""
    if not day:
        w = where if alias == "t" else where.replace(f"{alias}.", "t.")
        day = latest_settled_day(con, where=w, params=params)["day"]
        if not day:
            return None
    day = str(day)[:10]
    per = daily_series(con, where, params, [day] + baseline_dates(day, weeks), alias=alias)
    if by_branch:
        return {nm: baseline_from_days(dm, day, weeks) for nm, dm in per.items()}
    return chain_baseline(con, per, day, weeks, keep=keep)


def month_projection(con, where: str, params: Optional[dict], month_first, days_elapsed: int, days_total: int,
                     mtd: float) -> dict:
    """THE month-end projection of a month in progress (DV1, phone table review 2026-10-25: the overview page projected
    flat - to date / days x days in month - while the management summary weighted by weekday, 126 % vs 124 % of target).
    Weekday-fair: to date / (the previous month's average revenue of each weekday summed over the days elapsed) x (the
    same over the whole month); a weekday the previous month lacks uses its mean. Flat when the previous month has no
    sales. `where` uses the aliases st (sales_transaction) and b (branches); revenue = net_revenue_sum.
    Returns {"projected", "method" ('dow' | 'flat'), "dow_avg", "dow_fallback", "baseline_elapsed"}."""
    import datetime as _dt
    prev_month = (month_first - _dt.timedelta(days=1)).strftime("%Y-%m")
    rows = con.execute(f"""
        SELECT CAST(strftime('%w', st.sold_at) AS INT) wd, {net_revenue_sum('st')} rev, COUNT(DISTINCT date(st.sold_at)) days
        FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
        WHERE {where} AND strftime('%Y-%m', st.sold_at)=:_pm_month
        GROUP BY wd""", {**(params or {}), "_pm_month": prev_month}).fetchall()
    dow_avg = {r[0]: (r[1] or 0) / r[2] for r in rows if r[2]}
    fb = sum(dow_avg.values()) / len(dow_avg) if dow_avg else 0

    def wsum(n):
        return sum(dow_avg.get(int((month_first + _dt.timedelta(days=i)).strftime("%w")), fb) for i in range(n))
    out = {"projected": None, "method": "flat", "dow_avg": dow_avg, "dow_fallback": fb, "baseline_elapsed": None}
    if not (mtd and days_elapsed):
        return out
    out["projected"] = round(mtd / days_elapsed * days_total, 2)
    if dow_avg and days_elapsed < days_total:
        w = wsum(days_elapsed)
        out["baseline_elapsed"] = round(w, 2)
        if w:
            out.update(projected=round(mtd / w * wsum(days_total), 2), method="dow")
    return out


def scope_filter(filters: list, params: dict, branch: Optional[str] = None,
                 group: Optional[str] = None, alias: str = "b") -> None:
    if branch:
        filters.append(f"{alias}.name=:branch")
        params["branch"] = branch
    if group:
        filters.append(f"COALESCE({alias}.branch_type,'master') = :branch_group")
        params["branch_group"] = group


# ─── Closed branches: who a CURRENT-PERIOD table leaves out ──────────────────
# Owner 2026-10-26 ("Hide closed branches"): a branch that has closed (โคราช2, no
# bills since 2025-12) kept showing as a row of 0 sales and "-100%" in every
# branch table of the period on screen. THE rule, stated once:
#   a branch is left out of a current-period table when it has no sale in the
#   period on screen AND none in the PREVIOUS period (the year-ago period does not
#   count: a shop closed since last year is not a row of 0 and -100 %). A branch
#   marked closed (branches.active = 0) that DID sell in either stays a row
#   (owner decision 2026-10-26: the rows add up to the chain total, e.g. โคราช2
#   in Dec 2025); the mark alone counts only where there is no sales window
#   (targets) or nobody sold in the windows at all.
# The callers filter the rows after the totals are summed. The year-ago chain total
# may then hold a hidden branch's sales: hidden_amount() gives the baht for the
# table's footnote "รวมสาขาที่ปิดแล้ว ฿…" (no row). History and trend
# views (monthly series, YoY history, exports) do not call this. A branch picker
# lists the branches that are not closed (picker_closed_branches: marked closed,
# or no bill in the last 90 days) and puts the closed ones behind its
# "สาขาที่ปิดแล้ว" fold so their history stays reachable.
PICKER_IDLE_DAYS = 90       # a picker drops a branch with history but no bill in the last 90 days of the data


def _win_bounds(w):
    """(from_day, to_day) as 'YYYY-MM-DD'; a month 'YYYY-MM' (or a pair of them) is its whole month(s)."""
    a, b = (w, w) if isinstance(w, str) else (w[0], w[1])
    a, b = str(a)[:10], str(b)[:10]
    if len(a) == 7:
        a += "-01"
    if len(b) == 7:
        b += "-31"                       # string compare on ISO days: -31 covers every month end
    return a, b


def _columns(con, table: str) -> set:
    """The columns of `table` (empty when it does not exist): a slim database (a report generator's, a unit test's)
    may hold no `active` column or no month table, and the rule then reads what is there."""
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}


def _traded_ids(con, windows) -> set:
    """branch_ids with a sale in any of the windows: a bill (sales_transaction) on a day inside it, or a
    month row (sales_sku_monthly) for a month it touches - the months before the bill table starts."""
    out = set()
    has_tx = "sold_at" in _columns(con, "sales_transaction")
    has_month = "period_month" in _columns(con, "sales_sku_monthly")
    for w in windows:
        if not w or (not isinstance(w, str) and not (w[0] and w[1])):
            continue
        a, b = _win_bounds(w)
        if has_tx:
            out |= {r[0] for r in con.execute(
                "SELECT b.branch_id FROM branches b WHERE EXISTS (SELECT 1 FROM sales_transaction t "
                "WHERE t.branch_id=b.branch_id AND t.sold_at >= :a AND t.sold_at < date(:b,'+1 day'))",
                {"a": a, "b": b})}
        if has_month:
            out |= {r[0] for r in con.execute(
                "SELECT DISTINCT branch_id FROM sales_sku_monthly WHERE period_month BETWEEN :a AND :b",
                {"a": a[:7], "b": b[:7]})}
    return out


def closed_branches(con, windows=(), *, keep: Optional[str] = None, ids: bool = False) -> set:
    """THE closed-branch rule for a current-period table: the names (ids=True: the branch_ids) of the branches
    to leave out. `windows` = the period on screen and the PREVIOUS period (callers never pass the year-ago one): (from, to) day pairs
    ('YYYY-MM-DD') or months ('YYYY-MM'; month_window() gives all time as one); an empty / None window is skipped.
    A branch is left out when it traded in none of the windows - marked closed or not: a closed branch with a sale
    in one of them stays a row. With no window, or when no branch at all traded in the windows (a period before
    the data), only the closed mark (branches.active = 0) counts - a table is never emptied by this rule.
    `keep` = the branch the page is filtered to (its branch= / the picker's closed-branch fold): never left out."""
    act = "COALESCE(active,1)" if "active" in _columns(con, "branches") else "1"
    rows = con.execute(f"SELECT branch_id, name, {act} AS active FROM branches WHERE name != 'ALL'").fetchall()
    traded = _traded_ids(con, [w for w in (windows or ()) if w])
    out = set()
    for bid, name, active in rows:
        if name == keep:
            continue
        if (bid not in traded) if traded else (not active):
            out.add(bid if ids else name)
    return out


def month_window(month: Optional[str] = None, month_from: Optional[str] = None, month_to: Optional[str] = None):
    """The month filter of a month-grain page as one closed_branches window ('YYYY-MM' pair); no filter = all time."""
    if month:
        return (month[:7], month[:7])
    return ((month_from or "0000-01")[:7], (month_to or "9999-12")[:7])


def hidden_amount(by_branch: dict, closed) -> Optional[dict]:
    """The footnote of a table whose เทียบปีก่อน chain total holds the sales of branches it hides: by_branch = {name: baht of
    the year-ago period}; -> {"amount", "branches"} for the hidden ones that sold, None when there are none."""
    hit = {n: float(v or 0) for n, v in (by_branch or {}).items() if n in (closed or ()) and (v or 0) > 0}
    if not hit:
        return None
    return {"amount": round(sum(hit.values()), 2), "branches": sorted(hit)}


def drop_closed(rows, closed, key="branch"):
    """The rows of a branch table without the closed branches. `key` = the row's branch field (a name or an id,
    matching what `closed` holds) or a function row -> that value. Rows of other shapes pass unchanged."""
    if not closed:
        return list(rows)
    get = key if callable(key) else (lambda r: r.get(key) if isinstance(r, dict) else r[key])
    return [r for r in rows if get(r) not in closed]


def picker_closed_branches(con) -> set:
    """The branches a branch PICKER leaves out (behind its "สาขาที่ปิดแล้ว" fold): marked closed, or with sales in
    the past but none in the last PICKER_IDLE_DAYS days of the data (anchored on the chain's newest bill, not on
    today, so an old copy of the database keeps its list). A branch with no sales at all yet (a new one being set
    up) stays in the picker."""
    flagged = closed_branches(con)
    if "sold_at" not in _columns(con, "sales_transaction"):
        return flagged
    last = con.execute("SELECT MAX(sold_at) FROM sales_transaction").fetchone()[0]
    if not last:
        return flagged
    a = con.execute("SELECT date(:d, :off)", {"d": last[:10], "off": f"-{PICKER_IDLE_DAYS - 1} days"}).fetchone()[0]
    idle = closed_branches(con, [(a, last[:10])])
    month_rows = ("OR EXISTS (SELECT 1 FROM sales_sku_monthly m WHERE m.branch_id=b.branch_id)"
                  if "branch_id" in _columns(con, "sales_sku_monthly") else "")
    ever = {r[0] for r in con.execute(
        "SELECT b.name FROM branches b WHERE EXISTS (SELECT 1 FROM sales_transaction t WHERE t.branch_id=b.branch_id) "
        + month_rows)}
    return flagged | (idle & ever)


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


# ─── Voided bills: re-issued (a correction) or not (owner 2026-10-31) ─────────
# "ถ้าออกแทนบิลเก่า ดูเหมือนจะไม่ค่อยปกติ ... เตือนเอาเฉพาะยอดที่ voided แล้วไม่มีการทำใหม่": a void followed by a replacement
# bill is a correction, not a leak. THE rule, used by the ตรวจวันนี้ card (routers/mgt_watch), the drill-down (routers/voids),
# the management report's §7.1 and the franchise owner's finding: a voided bill at time t at branch b is RE-ISSUED when a live
# bill (sales_transaction, source Easy) exists at b with sold_at in [t, t + VOID_REISSUE_MIN min] that matches it:
#   - the same table, when both sides carry a table number (pos_cancel_bill_detail.table_no is optional - the POS export gives
#     none today, and sales_transaction.table_no is blank from the Easy sync);
#   - else the same number of customers, when both sides carry one (pos_cancel_bill_detail.customers = Sale.NumOfCust, added by
#     the sync 2026-10-31; optional, a DB synced before has no column);
#   - else an amount within ± VOID_REISSUE_AMOUNT_TOL of the voided amount (a ฿0 void therefore matches only a ฿0 bill).
# Each live bill re-issues at most one void of the same branch and day (voids taken in time order, the earliest matching bill),
# so a void's class never depends on the window it is read in (a window is whole days). The matched receipt is `reissued_as`.
VOID_REISSUE_MIN = 30
VOID_REISSUE_AMOUNT_TOL = 0.20
# Owner 2026-10-31 ("ตัด"): a void of ฿0 is an empty bill, not money that went missing - it can never match a new bill, so it
# is its own class: left out of the alert count (voids_open) and of the re-issued count, shown in the details as บิลเปล่า.
VOID_ZERO = "zero"


def _ts(x):
    import datetime as _d
    try:
        return _d.datetime.fromisoformat(str(x or "").replace("T", " ")[:19])
    except ValueError:
        return None


def receipt_label(receipt) -> str:
    """The bill number a cashier reads: the Easy sync keys a sold bill "<YYYY-MM-DD>-<SaleNum>-<SaleId>" (receipt_no is not
    unique on its own); this gives the SaleNum. Any other form is returned as it is."""
    import re
    m = re.match(r"^\d{4}-\d{2}-\d{2}-(.+)-\d+$", str(receipt or ""))
    return m.group(1) if m else str(receipt or "")


def void_reissue_match(void: dict, bill: dict) -> bool:
    """Does the live `bill` re-issue the voided `void`? Both {amount, table?, customers?}; the time window is the caller's."""
    vt, bt = str(void.get("table") or "").strip(), str(bill.get("table") or "").strip()
    if vt and bt:
        return vt == bt
    vc, bc = int(void.get("customers") or 0), int(bill.get("customers") or 0)
    if vc > 0 and bc > 0:
        return vc == bc
    va, ba = float(void.get("amount") or 0), float(bill.get("amount") or 0)
    return abs(ba - va) <= abs(va) * VOID_REISSUE_AMOUNT_TOL


def classify_voids(con, branch_ids, s, e) -> dict:
    """{pos_cancel_bill_detail rowid: receipt_no of the bill that re-issued it, or None} for every voided bill of the branches
    in branch_ids with date(sale_date) in s..e (dates). Empty when the detail table is missing."""
    import bisect
    import datetime as _d
    ids = [b for b in branch_ids if b is not None]
    cols = _columns(con, "pos_cancel_bill_detail")
    if not ids or not cols:
        return {}
    tcol = "table_no" if "table_no" in cols else "NULL"
    ccol = "customers" if "customers" in cols else "NULL"
    ph = ",".join("?" * len(ids))
    vrows = con.execute(f"SELECT rowid, branch_id, sale_date, amount, {tcol}, {ccol} FROM pos_cancel_bill_detail "
                        f"WHERE branch_id IN ({ph}) AND date(sale_date) BETWEEN ? AND ? ORDER BY sale_date",
                        ids + [s.isoformat(), e.isoformat()]).fetchall()
    out = {r[0]: None for r in vrows}
    if not vrows:
        return out
    vb = sorted({r[1] for r in vrows})
    ph2 = ",".join("?" * len(vb))
    bills: dict = {}
    for r in con.execute(f"SELECT branch_id, sold_at, receipt_no, total_inc_vat, table_no, customer_count FROM sales_transaction "
                         f"WHERE source='Easy' AND branch_id IN ({ph2}) AND sold_at >= ? AND sold_at < ? ORDER BY sold_at",
                         vb + [s.isoformat(), (e + _d.timedelta(days=2)).isoformat()]):
        t = _ts(r[1])
        if t:
            bills.setdefault(r[0], []).append({"at": t, "receipt": r[2], "amount": r[3] or 0, "table": r[4], "customers": r[5]})
    keys = {b: [x["at"] for x in lst] for b, lst in bills.items()}
    win, used = _d.timedelta(minutes=VOID_REISSUE_MIN), set()
    for rid, bid, at, amt, tab, cust in vrows:
        if abs(float(amt or 0)) < 0.005:
            out[rid] = VOID_ZERO
            continue
        day = str(at)[:10]
        t, lst = _ts(at), bills.get(bid)
        if not t or not lst:
            continue
        v = {"amount": amt or 0, "table": tab, "customers": cust}
        i = bisect.bisect_left(keys[bid], t)
        while i < len(lst) and lst[i]["at"] <= t + win:
            if (bid, day, i) not in used and void_reissue_match(v, lst[i]):
                used.add((bid, day, i))
                out[rid] = lst[i]["receipt"]
                break
            i += 1
    return out
