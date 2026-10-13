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
    # PR-J3: what the cache above was built from (release + a fingerprint of that day's sales), so a day is
    # rebuilt only when the release in force or the sales of that day changed (issue_reports.bom_days)
    """CREATE TABLE IF NOT EXISTS bcd_demand_day_meta(
         branch_id INTEGER NOT NULL, day TEXT NOT NULL, release_id INTEGER,
         sales_rows INTEGER, sales_qty REAL, built_at TEXT,
         PRIMARY KEY (branch_id, day))""",
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
    # PR-A — the delivery calendar and the 13:15 cutoff (cycle_date = the cutoff date)
    ("bc_order", "delivery_date", "TEXT"),       # the delivery day this order is for
    ("bc_order", "cutoff_at", "TEXT"),           # ISO time the order had to be submitted by (Bangkok)
    ("bc_order", "late", "INTEGER DEFAULT 0"),   # 1 = submitted after the cutoff (late fee at the outside warehouse)
    # PR-B — claims raised inside the receive form, and the branch's acknowledgement of its check
    ("bc_claim", "origin", "TEXT"),              # 'receive' = raised while receiving; 'after'/NULL = raised later
    ("bc_order", "ack_at", "TEXT"),              # the branch confirmed it checked condition and quantity
    ("bc_order", "ack_by", "TEXT"),
    # PR-C — the kitchen submits a draft for the analyst's review
    ("bc_release", "submitted_by", "TEXT"),
    ("bc_release", "submitted_at", "TEXT"),
    # PR-E — Sales Support marks the orders it has keyed into the external accounting system
    ("bc_order", "accounting_keyed_at", "TEXT"),
    ("bc_order", "accounting_keyed_by", "TEXT"),
    # PR-F — Accounting marks the ใบสรุปตัดเบิก of a period as posted into the accounting system
    ("bc_variance_period", "accounting_posted_at", "TEXT"),
    ("bc_variance_period", "accounting_posted_by", "TEXT"),
    # PR-U — the unit ladder (up to 3 units above the base unit, each stated against the one below it) …
    ("bc_unit_rule", "parent_unit", "TEXT"),      # the unit this one is stated in; NULL = the item's base unit
    ("bc_unit_rule", "qty_in_parent", "REAL"),    # 1 <unit> = qty_in_parent <parent_unit> (the average when conv_kind='avg')
    ("bc_unit_rule", "conv_kind", "TEXT"),        # 'exact' | 'avg' (pieces that differ in weight); NULL is read as 'exact'
    ("bc_unit_rule", "range_min", "REAL"),        # avg only: the lightest / heaviest piece, in parent_unit
    ("bc_unit_rule", "range_max", "REAL"),
    ("bc_unit_rule", "updated_at", "TEXT"),
    ("bc_unit_rule", "updated_by", "TEXT"),
    # … and the portion recipe: one whole piece (a salmon) cut into N pieces
    ("bc_prep_recipe", "mode", "TEXT"),           # 'portion' = สูตรแบ่งชิ้น; NULL is read as 'batch'
    ("bc_prep_line", "no_loss", "INTEGER DEFAULT 0"),   # 1 = the yield already counts only usable pieces: no trim loss on top
    ("bc_rel_prep_recipe", "mode", "TEXT"),
    ("bc_rel_prep_line", "no_loss", "INTEGER DEFAULT 0"),
    # PR-U2 — the number of whole pieces the branch counted when it received goods sold by weight (a salmon), so the real
    # average weight per piece can be learned from receipts …
    ("bc_order_line", "received_pieces", "REAL"),
    ("bc_receipt_line", "pieces", "REAL"),
    ("bc_receipt_line", "pieces_unit", "TEXT"),
    # … and a released month stays frozen when an average changes later: the factor (base units in ONE unit of the line) a
    # released sub-recipe line was costed with is kept with the release (L6)
    ("bc_rel_prep_line", "factor_base", "REAL"),
    # PR-V — the sales (฿, ex VAT) the branch typed for the cover period: the buffer in baht (the % is derived from it and
    # still drives the suggestion), or, for a new branch with a reference branch, the figure the suggestion is built on
    ("bc_order", "buffer_sales", "REAL"),
    # PR-Y — a code from another system (accounting, warehouse, supplier) is an ALIAS of the register item: the recipes keep the
    # register code. An alias is retired (active=0), never deleted; `is_current` marks the code to USE when writing to that system
    ("bc_ingredient_alias", "valid_from", "TEXT"),
    ("bc_ingredient_alias", "active", "INTEGER DEFAULT 1"),
    ("bc_ingredient_alias", "is_current", "INTEGER DEFAULT 0"),
    ("bc_ingredient_alias", "created_by", "TEXT"),
    ("bc_ingredient_alias", "created_at", "TEXT"),
    # PR-B2 (4.1) — a short display name for the branch screens (the register name is the supplier's spec and runs to 2-3 lines)
    ("bc_ingredient", "short_name", "TEXT"),
    # batch 4b (3.4) - a PARTIAL end count (stock.partial_close, withdrawn 2026-10-13: old rows only) is an ordinary 'end' count whose lines say where each figure came
    # from: basis 'system' = the system stock carried to the date (not counted), 'cycle' = the latest weekly count carried to it,
    # NULL = counted that day. partial = 1 on the count header. Both only added, so no existing row or CHECK is touched.
    ("bc_stock_count", "partial", "INTEGER"),
    ("bc_stock_count_line", "basis", "TEXT"),
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
    # batch 3 (3.6) — a receipt the branch cancelled: the whole receipt (snapshot = header + every line as JSON), the reason, who and
    # when. Append-only: written before the receipt is deleted, never updated or deleted (variance.delete_receipt)
    """CREATE TABLE IF NOT EXISTS bc_receipt_cancel(
         cancel_id INTEGER PRIMARY KEY AUTOINCREMENT,
         receipt_id INTEGER NOT NULL, branch_id INTEGER NOT NULL, receipt_date TEXT, source TEXT, ref TEXT,
         entered_by TEXT, created_at TEXT, line_count INTEGER,
         cancel_reason TEXT NOT NULL, cancelled_by TEXT, cancelled_at TEXT NOT NULL, snapshot TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_bc_receipt_cancel ON bc_receipt_cancel(cancelled_at)",
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
    # PR-U2 — the analyst's decision on an average weight per piece learned from real receipts (applied | kept);
    # last_receipt_id = the newest receipt the decision looked at: a new proposal needs a newer one
    """CREATE TABLE IF NOT EXISTS bc_unit_avg_decision(
         decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
         ingredient_code TEXT NOT NULL, unit TEXT NOT NULL,
         current_avg REAL, observed_avg REAL, n_receipts INTEGER, n_pieces REAL,
         window_from TEXT, window_to TEXT,
         decision TEXT NOT NULL CHECK(decision IN ('applied','kept')),
         note TEXT, decided_by TEXT, decided_at TEXT, last_receipt_id INTEGER)""",
    "CREATE INDEX IF NOT EXISTS ix_bc_unit_avg_decision ON bc_unit_avg_decision(ingredient_code, unit)",
    # PR-U2 — a branch reports that a pack is not the size the register says; the analyst applies or rejects it
    """CREATE TABLE IF NOT EXISTS bc_pack_report(
         report_id INTEGER PRIMARY KEY AUTOINCREMENT,
         branch_id INTEGER NOT NULL, order_id INTEGER, ingredient_code TEXT NOT NULL, unit TEXT NOT NULL,
         reported_qty REAL NOT NULL, reported_parent TEXT,
         current_qty REAL, current_parent TEXT, note TEXT,
         status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','applied','rejected')),
         created_by TEXT, created_at TEXT, decided_by TEXT, decided_at TEXT, decision_note TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_bc_pack_report ON bc_pack_report(status, ingredient_code)",
    # PR-Y — Sales Support reports that an item has a new code in another system; the analyst applies it (it becomes an alias)
    # or rejects it with a reason (aliases.py)
    # batch 4b (3.4) - the weekly count of a branch's class-A items. A separate pair of tables, not a new kind in bc_stock_count: that
    # table's CHECK(kind IN ('begin','end','spot')) cannot be widened in place in SQLite, and rebuilding the table that every period,
    # reason and posted-period key points at would put the counts at risk. Month-end readers (periods, variance, reconcile, the
    # accounting summary) read only bc_stock_count, so a cycle count never changes a period by itself.
    """CREATE TABLE IF NOT EXISTS bc_cycle_count(
         cycle_id INTEGER PRIMARY KEY AUTOINCREMENT,
         branch_id INTEGER NOT NULL, count_date TEXT NOT NULL,
         status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','submitted')),
         counted_by TEXT, note TEXT, created_at TEXT, updated_at TEXT, submitted_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_bc_cycle_count ON bc_cycle_count(branch_id, count_date)",
    """CREATE TABLE IF NOT EXISTS bc_cycle_count_line(
         cycle_id INTEGER NOT NULL REFERENCES bc_cycle_count(cycle_id),
         ingredient_code TEXT NOT NULL,
         qty_kitchen REAL, unit TEXT, qty_base REAL, note TEXT,
         PRIMARY KEY (cycle_id, ingredient_code))""",
    # batch 4b (2.6) - "I have read this task": an append-only record. A feedback task (feedback.py) is open while its rule still holds
    # and nobody acknowledged it for that month; it closes by an acknowledgement or when the rule stops holding. Rows are never changed.
    """CREATE TABLE IF NOT EXISTS bc_feedback_ack(
         ack_id INTEGER PRIMARY KEY AUTOINCREMENT,
         rule TEXT NOT NULL, subject TEXT NOT NULL, month TEXT NOT NULL,
         acked_by TEXT, acked_at TEXT NOT NULL, note TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_bc_feedback_ack ON bc_feedback_ack(rule, subject, month)",
    """CREATE TABLE IF NOT EXISTS bc_code_request(
         request_id INTEGER PRIMARY KEY AUTOINCREMENT,
         old_code TEXT, new_code TEXT NOT NULL,
         system TEXT NOT NULL DEFAULT 'acc' CHECK(system IN ('acc','warehouse','supplier','other')),
         name TEXT, ingredient_code TEXT NOT NULL, valid_from TEXT, note TEXT,
         status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','applied','rejected')),
         requested_by TEXT, requested_at TEXT, decided_by TEXT, decided_at TEXT, decision_note TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_bc_code_request ON bc_code_request(status, new_code)",
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
    "bc_receipt_cancel",          # batch 3: a cancelled receipt, kept with its reason (variance.py)
    "bc_central_item",            # Phase 2 S11: what a branch orders from the centre (central.py)
    "bc_code_link",               # S11: a recipe's older code -> the code the centre sends (central.py)
    "bc_claim", "bc_claim_line", "bc_claim_photo",   # เคลมสินค้า: branch claim + photos, decided by Sales Support (claims.py)
    "bc_price_submission",        # the monthly price file Sales Support sends to the analyst (price_submissions.py)
    "bc_issue_slip", "bc_issue_line", "bc_issue_log",   # ใบตัดเบิก: the branch's daily issue slip + its log (issues.py)
    "bc_unit_avg_decision",       # PR-U2: the analyst's answer to "use the average weight learned from receipts?" (units.py)
    "bc_pack_report",             # PR-U2: a branch reports a pack that is not the registered size (pack_reports.py)
    "bc_code_request",            # PR-Y: a new code of an existing item, asked by Sales Support, decided by the analyst (aliases.py)
    "bc_cycle_count", "bc_cycle_count_line",   # batch 4b (3.4): the weekly count of the class-A items (stock.py)
    "bc_feedback_ack",            # batch 4b (2.6): a task made from the variance reasons was read (feedback.py)
)
DERIVED_TABLES = ("bcd_cost_cache", "bcd_demand_day", "bcd_demand_day_meta")
TABLES = OWNED_TABLES + DERIVED_TABLES


def ensure(con, commit: bool = False) -> None:
    """Create every bom-center table and index that is missing. Idempotent.

    Does not commit unless asked: callers that build a release inside one
    transaction (snapshot_v0's dry-run rolls everything back) must not have
    their half-written work committed under them by a CREATE TABLE."""
    from .dated import DDL as DATED_DDL
    from .codes import DDL as CODES_DDL
    from .central import DDL as CENTRAL_DDL, LINK_DDL
    from .claims import DDL as CLAIMS_DDL
    from .price_submissions import DDL as PRICE_SUB_DDL
    from .issues import DDL as ISSUES_DDL
    for ddl in DDL + EXTRA_DDL + (DATED_DDL, CODES_DDL, CENTRAL_DDL, LINK_DDL) + CLAIMS_DDL + PRICE_SUB_DDL + ISSUES_DDL:
        con.execute(ddl)
    for table, col, typ in MIGRATIONS:
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if col not in have:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    # PR-U: every rule written before the ladder is a level-1 exact step on the base unit (idempotent; reads first so a
    # plain GET never opens a write transaction when there is nothing to fill)
    if con.execute("SELECT 1 FROM bc_unit_rule WHERE qty_in_parent IS NULL LIMIT 1").fetchone():
        con.execute("UPDATE bc_unit_rule SET qty_in_parent=factor_to_base WHERE qty_in_parent IS NULL")
    from .issues import widen_log      # PR-M7: bc_issue_log.action gained 'append' (rebuilt once on a DB that has the old CHECK)
    widen_log(con)
    from .orders import backfill        # PR-A: delivery weekday / delivery date of rows saved before it (idempotent)
    backfill(con)
    if commit:
        con.commit()


def existing(con) -> set:
    """Names of the bom-center tables present on this connection."""
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND "
        "(name LIKE 'bc\\_%' ESCAPE '\\' OR name LIKE 'bcd\\_%' ESCAPE '\\')").fetchall()
    return {r[0] for r in rows}
