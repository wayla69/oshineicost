# -*- coding: utf-8 -*-
"""bom_center.schema — the BoM Calculation Center tables (plan §4.3).

Two prefixes, one rule each:

  bc_*   SITE-OWNED. Typed or imported on the running site: ingredients and
         their monthly prices, dishes, recipe releases, order calendars,
         stock counts, orders, the audit trail. "bc" is in
         core._PRESERVED_PREFIXES, so the three daily DB swaps copy these
         tables forward from the live DB. A table named bc_* can never be
         wiped by a refresh — the #209 bug class (ordering tables lost on
         every swap) is closed by the name alone.

  bcd_*  DERIVED. Rebuilt from bc_* and the sales data: the per-release cost
         cache and the daily ingredient demand. Deliberately NOT preserved:
         a swap brings new sales, so these must be recomputed after it, never
         copied over it. (LIKE 'bc\\_%' does not match 'bcd_…'.)

ensure(con) is idempotent and cheap (CREATE … IF NOT EXISTS). The app calls it
at startup; scripts and tests call it on whatever connection they hold.

Release semantics (bc_release):
  kind='baseline'  v0 — today's engine frozen; effective_month = BASELINE_MONTH
                   ("1900-01") so it is the version in force for any month no
                   monthly release covers.
  kind='monthly'   analyst releases, effective_month = 'YYYY-MM'.
  The version in force for month M = the RELEASED release with the greatest
  effective_month <= M (engine.release_in_force).
"""

BASELINE_MONTH = "1900-01"

DDL = (
    # ── M1 ingredient master ────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS bc_ingredient(
         code TEXT PRIMARY KEY,
         name_th TEXT, name_en TEXT,
         family TEXT,                               -- ACC / HK / CK / V / S / F / D / DK / other
         kind TEXT NOT NULL DEFAULT 'raw'
              CHECK(kind IN ('raw','prepared','consumable','zero_by_design')),
         category TEXT, supplier TEXT,
         base_unit TEXT,                            -- what recipes count in: กรัม / มิลลิลิตร / ชิ้น
         recv_unit TEXT, qty_per_recv REAL,         -- one purchased pack = qty_per_recv base units
         loss_pct REAL NOT NULL DEFAULT 0,          -- trim loss; yield = 1 - loss_pct/100
         moq REAL, lead_days INTEGER, shelf_days INTEGER,
         monitored INTEGER NOT NULL DEFAULT 0,      -- on the warehouse "forced order" list
         sourcing_default TEXT NOT NULL DEFAULT 'central' CHECK(sourcing_default IN ('central','local')),
         active INTEGER NOT NULL DEFAULT 1,
         note TEXT, updated_at TEXT, updated_by TEXT)""",
    """CREATE TABLE IF NOT EXISTS bc_ingredient_alias(
         alias_code TEXT PRIMARY KEY,
         ingredient_code TEXT NOT NULL,
         system TEXT,                               -- warehouse / acc / old-recipe
         note TEXT)""",
    """CREATE TABLE IF NOT EXISTS bc_unit_rule(
         ingredient_code TEXT NOT NULL, unit TEXT NOT NULL,
         factor_to_base REAL NOT NULL,              -- 1 <unit> = factor_to_base base units
         label TEXT,
         PRIMARY KEY (ingredient_code, unit))""",
    # ── M4 monthly prices (actual weighted-average purchase price, plan §10-1) ─
    """CREATE TABLE IF NOT EXISTS bc_ingredient_price(
         ingredient_code TEXT NOT NULL, month TEXT NOT NULL,       -- 'YYYY-MM'
         price_per_base REAL NOT NULL,
         qty_bought REAL, value_bought REAL,                       -- avg = value / qty
         source TEXT NOT NULL DEFAULT 'purchase_avg'
                CHECK(source IN ('purchase_avg','carry_forward','manual','list_ref','snapshot')),
         price_age_months INTEGER NOT NULL DEFAULT 0,
         imported_at TEXT, approved_by TEXT, note TEXT,
         PRIMARY KEY (ingredient_code, month))""",
    "CREATE INDEX IF NOT EXISTS ix_bc_price_month ON bc_ingredient_price(month)",
    """CREATE TABLE IF NOT EXISTS bc_ingredient_price_branch(
         ingredient_code TEXT NOT NULL, month TEXT NOT NULL, branch_id INTEGER NOT NULL,
         price_per_base REAL NOT NULL, qty_bought REAL, value_bought REAL,
         PRIMARY KEY (ingredient_code, month, branch_id))""",
    # ── M2 prepared (semi-finished) items — structure only until Phase 1.5 ──
    """CREATE TABLE IF NOT EXISTS bc_prep_recipe(
         prepared_code TEXT PRIMARY KEY,
         batch_yield_qty REAL NOT NULL, batch_unit TEXT NOT NULL,  -- "1 สูตร = 15,225 ml"
         note TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS bc_prep_line(
         prepared_code TEXT NOT NULL, seq INTEGER NOT NULL,
         ingredient_code TEXT NOT NULL, qty REAL NOT NULL, unit TEXT, note TEXT,
         PRIMARY KEY (prepared_code, seq))""",
    # ── M3 dishes and their POS codes ───────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS bc_dish(
         dish_id INTEGER PRIMARY KEY AUTOINCREMENT,
         name_th TEXT NOT NULL, name_en TEXT,
         category TEXT, channel_default TEXT,
         min_tier INTEGER,                          -- 459/629/789/999/1299; NULL = à la carte only
         status TEXT NOT NULL DEFAULT 'active', note TEXT)""",
    """CREATE TABLE IF NOT EXISTS bc_dish_sku(
         sku_code TEXT PRIMARY KEY,
         dish_id INTEGER NOT NULL REFERENCES bc_dish(dish_id),
         channel TEXT NOT NULL CHECK(channel IN ('BF','AL','TW','DL','OT')),
         serve_qty REAL, serve_unit TEXT)           -- serve_unit e.g. "6 ชิ้น ต่อ 1 เสิร์ฟ"
    """,
    # ── M5 releases ─────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS bc_release(
         release_id INTEGER PRIMARY KEY AUTOINCREMENT,
         label TEXT NOT NULL UNIQUE,
         kind TEXT NOT NULL CHECK(kind IN ('baseline','monthly')),
         effective_month TEXT NOT NULL,             -- 'YYYY-MM'; baseline = BASELINE_MONTH
         status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','review','released','retired')),
         scope TEXT NOT NULL DEFAULT 'all' CHECK(scope IN ('buffet','alacarte','all')),
         created_by TEXT, created_at TEXT,
         reviewed_by TEXT, reviewed_at TEXT,
         released_by TEXT, released_at TEXT,
         acknowledged_by TEXT, acknowledged_at TEXT, -- management's "ตรวจ/รับทราบ" after release
         parent_release_id INTEGER, notes TEXT)""",
    """CREATE TABLE IF NOT EXISTS bc_recipe(
         release_id INTEGER NOT NULL REFERENCES bc_release(release_id),
         sku_code TEXT NOT NULL,
         kind TEXT NOT NULL CHECK(kind IN ('recipe','flat','alias','zero')),
         flat_cost REAL,                            -- kind='flat'
         alias_sku_code TEXT,                       -- kind='alias': take that SKU's lines
         source TEXT,                               -- provenance: confirmed / recipe-file / estimate / sku_cost / …
         sop_ok INTEGER, note TEXT,
         PRIMARY KEY (release_id, sku_code))""",
    """CREATE TABLE IF NOT EXISTS bc_recipe_line(
         release_id INTEGER NOT NULL, sku_code TEXT NOT NULL, seq INTEGER NOT NULL,
         ingredient_code TEXT, ingredient_name TEXT,
         qty REAL, unit TEXT, prep_note TEXT, ingredient_type TEXT,
         zero_by_design INTEGER NOT NULL DEFAULT 0,
         price_per_unit REAL,                       -- baseline snapshot only; monthly releases price at read
         line_cost REAL,                            -- baseline snapshot only (exact reproduction)
         PRIMARY KEY (release_id, sku_code, seq))""",
    "CREATE INDEX IF NOT EXISTS ix_bc_recipe_line_ing ON bc_recipe_line(release_id, ingredient_code)",
    """CREATE TABLE IF NOT EXISTS bc_release_check(
         release_id INTEGER NOT NULL, check_code TEXT NOT NULL, ref TEXT NOT NULL,
         severity TEXT NOT NULL CHECK(severity IN ('block','warn')),
         message TEXT, resolved_by TEXT, resolved_at TEXT,
         PRIMARY KEY (release_id, check_code, ref))""",
    # ── M7 order calendar / forecast runs ───────────────────────────────────
    """CREATE TABLE IF NOT EXISTS bc_order_calendar(
         branch_id INTEGER NOT NULL,
         round TEXT NOT NULL CHECK(round IN ('1','2','salmon')),
         order_weekday INTEGER NOT NULL,            -- 0=Sunday … 6=Saturday (SQLite %w)
         delivery_weekday INTEGER, lead_days INTEGER,
         filled_by TEXT, updated_at TEXT,
         PRIMARY KEY (branch_id, round))""",
    """CREATE TABLE IF NOT EXISTS bc_forecast_run(
         run_id INTEGER PRIMARY KEY AUTOINCREMENT,
         branch_id INTEGER NOT NULL, cycle_date TEXT NOT NULL, round TEXT,
         cover_from TEXT, cover_to TEXT,
         method TEXT, params TEXT, release_id INTEGER,
         created_at TEXT, created_by TEXT)""",
    """CREATE TABLE IF NOT EXISTS bc_forecast_line(
         run_id INTEGER NOT NULL REFERENCES bc_forecast_run(run_id),
         ingredient_code TEXT NOT NULL,
         forecast_base REAL, safety_base REAL, on_hand_base REAL, on_order_base REAL,
         suggested_units REAL, order_unit TEXT, qty_per_order REAL, price_per_base REAL,
         PRIMARY KEY (run_id, ingredient_code))""",
    # ── M8 branch stock & orders (Phase 2) ──────────────────────────────────
    """CREATE TABLE IF NOT EXISTS bc_stock_count(
         count_id INTEGER PRIMARY KEY AUTOINCREMENT,
         branch_id INTEGER NOT NULL, count_date TEXT NOT NULL,
         kind TEXT NOT NULL CHECK(kind IN ('begin','end','spot')),
         counted_by TEXT, note TEXT, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS bc_stock_count_line(
         count_id INTEGER NOT NULL REFERENCES bc_stock_count(count_id),
         ingredient_code TEXT NOT NULL,
         qty_kitchen REAL, unit TEXT, qty_base REAL, photo_ref TEXT,
         PRIMARY KEY (count_id, ingredient_code))""",
    """CREATE TABLE IF NOT EXISTS bc_order(
         order_id INTEGER PRIMARY KEY AUTOINCREMENT,
         branch_id INTEGER NOT NULL, cycle_date TEXT NOT NULL, round TEXT,
         run_id INTEGER,
         status TEXT NOT NULL DEFAULT 'suggested'
                CHECK(status IN ('suggested','confirmed','submitted','processed','received','cancelled')),
         confirmed_by TEXT, confirmed_at TEXT, submitted_at TEXT,
         processed_by TEXT, processed_at TEXT, po_ref TEXT,   -- Sales Support (plan §7.5)
         received_at TEXT, note TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_bc_order_branch ON bc_order(branch_id, cycle_date)",
    """CREATE TABLE IF NOT EXISTS bc_order_line(
         order_id INTEGER NOT NULL REFERENCES bc_order(order_id),
         ingredient_code TEXT NOT NULL,
         suggested_units REAL, confirmed_units REAL, processed_units REAL, received_units REAL,
         order_unit TEXT, reason_code TEXT, note TEXT,
         PRIMARY KEY (order_id, ingredient_code))""",
    # ── audit trail (who changed what) ──────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS bc_audit(
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         at TEXT NOT NULL, who TEXT, action TEXT NOT NULL,
         table_name TEXT, row_key TEXT, old_value TEXT, new_value TEXT)""",
    # ── derived (bcd_*): rebuilt, never carried forward ─────────────────────
    """CREATE TABLE IF NOT EXISTS bcd_cost_cache(
         release_id INTEGER NOT NULL, sku_code TEXT NOT NULL,
         unit_cost REAL NOT NULL DEFAULT 0,
         source TEXT, breakdown TEXT, built_at TEXT,
         PRIMARY KEY (release_id, sku_code))""",
    "CREATE INDEX IF NOT EXISTS ix_bcd_cost_cache_sku ON bcd_cost_cache(sku_code)",
    """CREATE TABLE IF NOT EXISTS bcd_demand_day(
         branch_id INTEGER NOT NULL, day TEXT NOT NULL, ingredient_code TEXT NOT NULL,
         qty_base REAL NOT NULL, release_id INTEGER,
         PRIMARY KEY (branch_id, day, ingredient_code))""",
    "CREATE INDEX IF NOT EXISTS ix_bcd_demand_day_day ON bcd_demand_day(day)",
)

# Columns added after a table first shipped: ensure() adds whichever are missing,
# so a live DB created by an earlier sprint is brought up to date at startup.
MIGRATIONS = (
    ("bc_release", "price_month", "TEXT"),        # the month of prices a monthly release is costed at
    ("bc_recipe", "source_cost", "REAL"),         # the cost the source sheet stated (tie-out)
    ("bc_recipe", "source_ref", "TEXT"),          # where the recipe came from (sheet / block)
    ("bc_recipe_line", "loss_pct", "REAL"),       # line-level trim loss; NULL = the ingredient's own
    ("bc_ingredient", "order_round", "TEXT"),     # 'salmon' = the weekly pre-order round; NULL = regular
    ("bc_prep_recipe", "source_cost", "REAL"),    # Phase 1.5: the batch cost the kitchen sheet stated
    ("bc_prep_recipe", "source_ref", "TEXT"),     # where the sub-recipe came from (BoM_Semi row / flat file)
    ("bc_prep_recipe", "updated_by", "TEXT"),
    # Phase 2 · S6 — branch stock counts
    ("bc_ingredient", "storage_zone", "TEXT"),    # frozen / chilled / dry / bar / other — where a branch keeps it
    ("bc_ingredient", "count_unit", "TEXT"),      # the unit a branch counts it in (NULL = its pack, else base)
    ("bc_stock_count", "status", "TEXT"),         # draft / submitted (NULL = submitted)
    ("bc_stock_count", "submitted_at", "TEXT"),
    ("bc_stock_count", "updated_at", "TEXT"),
    ("bc_stock_count_line", "note", "TEXT"),
    # Phase 2 · S8 — the branch order
    ("bc_order", "stock_count_id", "INTEGER"),   # the count the suggestion deducted (NULL = ไม่หักสต๊อก)
    ("bc_order", "value", "REAL"),               # ฿ of the confirmed quantities at the month's price
    # Phase 2 · S9 — Sales Support's reason when what is sent differs from what the branch confirmed
    ("bc_order_line", "ss_reason", "TEXT"),
    ("bc_order_line", "ss_note", "TEXT"),
    # PR-19 — the branch's own expectation for the cover period (buffer), one per order
    ("bc_order", "buffer_pct", "REAL"),          # +20 = the branch expects 20 % more sales than the forecast
    ("bc_order", "buffer_reason", "TEXT"),       # event / promotion / booking / holiday / quiet / other
    ("bc_order", "buffer_note", "TEXT"),
)
EXTRA_DDL = (
    # a person's answer to "which POS code is this recipe block?" — kept across imports
    """CREATE TABLE IF NOT EXISTS bc_dish_name_map(
         tier TEXT NOT NULL, name_key TEXT NOT NULL,
         sku_code TEXT NOT NULL, mapped_by TEXT, mapped_at TEXT,
         PRIMARY KEY (tier, name_key))""",
    # Phase 1.5 — the kitchen's answer to "which prepared code is this BoM_Semi row?" ('-' = not ours)
    """CREATE TABLE IF NOT EXISTS bc_prep_name_map(
         name_key TEXT PRIMARY KEY, prepared_code TEXT NOT NULL, mapped_by TEXT, mapped_at TEXT)""",
    # sub-recipes frozen into a release when it is released: history never moves when the kitchen edits
    """CREATE TABLE IF NOT EXISTS bc_rel_prep_recipe(
         release_id INTEGER NOT NULL, prepared_code TEXT NOT NULL,
         batch_yield_qty REAL NOT NULL, batch_unit TEXT NOT NULL,
         PRIMARY KEY (release_id, prepared_code))""",
    # Phase 2 · S7 — goods received at a branch (the branch types it now; S8 order receiving writes here too)
    """CREATE TABLE IF NOT EXISTS bc_receipt(
         receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
         branch_id INTEGER NOT NULL, receipt_date TEXT NOT NULL,
         source TEXT NOT NULL DEFAULT 'branch',   -- branch / order / import
         ref TEXT, note TEXT, entered_by TEXT, created_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_bc_receipt_branch ON bc_receipt(branch_id, receipt_date)",
    """CREATE TABLE IF NOT EXISTS bc_receipt_line(
         receipt_id INTEGER NOT NULL, ingredient_code TEXT NOT NULL,
         qty REAL NOT NULL, unit TEXT, qty_base REAL,
         PRIMARY KEY (receipt_id, ingredient_code))""",
    # the branch's reason for a gap in one period (keyed by the period's END count)
    """CREATE TABLE IF NOT EXISTS bc_variance_reason(
         end_count_id INTEGER NOT NULL, ingredient_code TEXT NOT NULL, branch_id INTEGER NOT NULL,
         reason_code TEXT NOT NULL, note TEXT, who TEXT, at TEXT,
         PRIMARY KEY (end_count_id, ingredient_code))""",
    # "every delivery of this period is recorded" — until then the period is incomplete, never guessed
    """CREATE TABLE IF NOT EXISTS bc_variance_period(
         end_count_id INTEGER PRIMARY KEY, branch_id INTEGER NOT NULL,
         receipts_confirmed_by TEXT, receipts_confirmed_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS bc_rel_prep_line(
         release_id INTEGER NOT NULL, prepared_code TEXT NOT NULL, seq INTEGER NOT NULL,
         ingredient_code TEXT NOT NULL, qty REAL NOT NULL, unit TEXT, note TEXT,
         PRIMARY KEY (release_id, prepared_code, seq))""",
)

OWNED_TABLES = (
    "bc_ingredient", "bc_ingredient_alias", "bc_unit_rule",
    "bc_ingredient_price", "bc_ingredient_price_branch",
    "bc_prep_recipe", "bc_prep_line",
    "bc_dish", "bc_dish_sku",
    "bc_release", "bc_recipe", "bc_recipe_line", "bc_release_check",
    "bc_order_calendar", "bc_forecast_run", "bc_forecast_line",
    "bc_stock_count", "bc_stock_count_line", "bc_order", "bc_order_line",
    "bc_audit", "bc_dish_name_map",
    "bc_sku_cost_month",          # the cut-over: released cost per SKU per month (dated.py)
    "bc_code_review",             # analyst's answer per legacy recipe code: same item / use another code (codes.py)
    "bc_prep_name_map", "bc_rel_prep_recipe", "bc_rel_prep_line",   # Phase 1.5 (prep.py)
    "bc_receipt", "bc_receipt_line", "bc_variance_reason", "bc_variance_period",   # Phase 2 S7 (variance.py)
    "bc_central_item",            # Phase 2 S11: what a branch orders from the centre (central.py)
    "bc_code_link",               # S11: a recipe's older code -> the code the centre sends (central.py)
)
DERIVED_TABLES = ("bcd_cost_cache", "bcd_demand_day")
TABLES = OWNED_TABLES + DERIVED_TABLES


def ensure(con, commit: bool = False) -> None:
    """Create every bom-center table and index that is missing. Idempotent.

    Does not commit unless asked: callers that build a release inside one
    transaction (snapshot_v0's dry-run rolls everything back) must not have
    their half-written work committed under them by a CREATE TABLE."""
    from .dated import DDL as DATED_DDL
    from .codes import DDL as CODES_DDL
    from .central import DDL as CENTRAL_DDL, LINK_DDL
    for ddl in DDL + EXTRA_DDL + (DATED_DDL, CODES_DDL, CENTRAL_DDL, LINK_DDL):
        con.execute(ddl)
    for table, col, typ in MIGRATIONS:
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if col not in have:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    if commit:
        con.commit()


def existing(con) -> set:
    """Names of the bom-center tables present on this connection."""
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND "
        "(name LIKE 'bc\\_%' ESCAPE '\\' OR name LIKE 'bcd\\_%' ESCAPE '\\')").fetchall()
    return {r[0] for r in rows}
