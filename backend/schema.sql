-- Oshinei Sales & Cost Analysis Dashboard
-- SQLite DDL

CREATE TABLE IF NOT EXISTS branches (
  branch_id   INTEGER PRIMARY KEY,
  name        TEXT UNIQUE NOT NULL,
  branch_type TEXT    DEFAULT 'master',
  active      INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS categories (
  category_id INTEGER PRIMARY KEY,
  name        TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS skus (
  sku_id      INTEGER PRIMARY KEY,
  sku_code    TEXT UNIQUE NOT NULL,
  description TEXT,
  category_id INTEGER REFERENCES categories(category_id),
  unit        TEXT
);

CREATE TABLE IF NOT EXISTS sku_cost (
  sku_id         INTEGER PRIMARY KEY REFERENCES skus(sku_id),
  unit_cost      REAL NOT NULL,
  effective_from TEXT,
  updated_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sales_sku_monthly (
  branch_id        INTEGER NOT NULL REFERENCES branches(branch_id),
  period_month     TEXT    NOT NULL,   -- 'YYYY-MM'
  sku_id           INTEGER NOT NULL REFERENCES skus(sku_id),
  qty              REAL,
  subtotal_ex_vat  REAL,
  tax_total        REAL,
  total_inc_vat    REAL,
  source           TEXT NOT NULL,       -- 'Easy' | 'CM POS' | 'CM'
  PRIMARY KEY (branch_id, period_month, sku_id, source)
);

CREATE TABLE IF NOT EXISTS sales_transaction (
  branch_id        INTEGER NOT NULL REFERENCES branches(branch_id),
  receipt_no       TEXT    NOT NULL,
  sold_at          TEXT    NOT NULL,    -- ISO datetime
  cashier          TEXT,
  table_no         TEXT,
  customer_count   INTEGER,
  food_subtotal    REAL,
  drink_subtotal   REAL,
  other_subtotal   REAL,
  tip              REAL,
  sp_discount      REAL,
  discount         REAL,
  service_charge   REAL,
  total_inc_vat    REAL,
  source           TEXT NOT NULL,
  print_times      INTEGER DEFAULT 0,   -- Easy POS Sale.PrintTimes: times receipt was printed
  PRIMARY KEY (branch_id, receipt_no, source)
);

CREATE TABLE IF NOT EXISTS sales_month_summary (
  branch_id         INTEGER NOT NULL REFERENCES branches(branch_id),
  period_month      TEXT    NOT NULL,
  gross_sales       REAL,
  refunds           REAL,
  discounts         REAL,
  voided            REAL,
  net_sales         REAL,
  cash              REAL,
  credit_card       REAL,
  transfer          REAL,
  qr                REAL,
  coupon            REAL,
  customer_count    INTEGER,
  transaction_count INTEGER,
  source            TEXT NOT NULL,
  PRIMARY KEY (branch_id, period_month, source)
);

-- ── sales_transaction indexes ──────────────────────────────────────────────
-- NOTE: this is the single source of truth for ALL performance indexes. The
-- covering indexes below were previously created ad-hoc in the live DB only,
-- so a full re-ingest silently lost them; they now live here. Three former
-- single/short indexes (idx_tx_yearmonth, idx_sku_branch_per, idx_ssku_sku)
-- were dropped as exact prefix-duplicates of the covering indexes — the query
-- planner uses the covering index for those access paths just as well.
CREATE INDEX IF NOT EXISTS idx_tx_date          ON sales_transaction(sold_at);
CREATE INDEX IF NOT EXISTS idx_tx_branch        ON sales_transaction(branch_id, sold_at);
CREATE INDEX IF NOT EXISTS idx_tx_source_date   ON sales_transaction(source, sold_at);
CREATE INDEX IF NOT EXISTS idx_tx_branch_src    ON sales_transaction(branch_id, source, sold_at);
CREATE INDEX IF NOT EXISTS idx_tx_source_branch ON sales_transaction(source, branch_id);
-- Composite for branch + month filter (very common)
CREATE INDEX IF NOT EXISTS idx_tx_branch_ym     ON sales_transaction(branch_id, strftime('%Y-%m', sold_at));
-- Covering indexes: leading expr/col + (source, branch_id, total_inc_vat, customer_count)
-- so the common revenue/cover aggregations are answered index-only.
CREATE INDEX IF NOT EXISTS idx_tx_ym_covering   ON sales_transaction(strftime('%Y-%m', sold_at), source, branch_id, total_inc_vat, customer_count);
CREATE INDEX IF NOT EXISTS idx_tx_day_covering  ON sales_transaction(date(sold_at), source, branch_id, total_inc_vat, customer_count);
CREATE INDEX IF NOT EXISTS idx_tx_src_day       ON sales_transaction(source, date(sold_at), branch_id, total_inc_vat, customer_count);
CREATE INDEX IF NOT EXISTS idx_tx_src_week      ON sales_transaction(source, strftime('%Y-W%W', sold_at), branch_id, total_inc_vat, customer_count);
CREATE INDEX IF NOT EXISTS idx_tx_src_dow       ON sales_transaction(source, strftime('%w', sold_at), branch_id, total_inc_vat, customer_count);
CREATE INDEX IF NOT EXISTS idx_tx_src_hour      ON sales_transaction(source, strftime('%H', sold_at), branch_id, total_inc_vat, customer_count);
-- Covering indexes for the Analysis-Report endpoints (discounts / persona / dow / hourly).
-- Also created idempotently at startup (_perf_startup) so existing DBs pick them up.
CREATE INDEX IF NOT EXISTS idx_tx_disc_cov      ON sales_transaction(branch_id, strftime('%Y-%m', sold_at), cashier, discount, food_subtotal, drink_subtotal, other_subtotal);
CREATE INDEX IF NOT EXISTS idx_tx_persona_cov   ON sales_transaction(branch_id, strftime('%Y-%m', sold_at), customer_count, total_inc_vat, food_subtotal, drink_subtotal, other_subtotal);
CREATE INDEX IF NOT EXISTS idx_tx_ym_dow_cov    ON sales_transaction(strftime('%Y-%m', sold_at), strftime('%w', sold_at), sold_at, total_inc_vat, customer_count);

-- Per-SKU effective COGS lookup (backs _unified_cogs_expr; populated by _build_sku_effective_cost).
CREATE TABLE IF NOT EXISTS sku_effective_cost(sku_id INTEGER PRIMARY KEY, recipe_cost REAL NOT NULL DEFAULT 0);

-- ── sales_sku_monthly indexes ──────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_sku_period       ON sales_sku_monthly(period_month);
CREATE INDEX IF NOT EXISTS idx_ssm_sku_period      ON sales_sku_monthly(sku_id, period_month, qty, subtotal_ex_vat, total_inc_vat);
CREATE INDEX IF NOT EXISTS idx_ssm_sku_covering    ON sales_sku_monthly(sku_id, branch_id, period_month, qty, total_inc_vat, subtotal_ex_vat);
CREATE INDEX IF NOT EXISTS idx_ssm_branch_covering ON sales_sku_monthly(branch_id, period_month, sku_id, qty, subtotal_ex_vat, total_inc_vat);

CREATE INDEX IF NOT EXISTS idx_sms_period       ON sales_month_summary(period_month);
CREATE INDEX IF NOT EXISTS idx_sku_category     ON skus(category_id);

-- Refresh query-planner statistics. Re-run ANALYZE after any bulk re-ingest.
ANALYZE;

-- Monthly revenue / customer targets per branch
CREATE TABLE IF NOT EXISTS branch_targets (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  branch_id      INTEGER NOT NULL REFERENCES branches(branch_id),
  period_month   TEXT    NOT NULL,  -- YYYY-MM
  revenue_target REAL    DEFAULT 0,
  customer_target INTEGER DEFAULT 0,
  UNIQUE(branch_id, period_month)
);

-- Key-value store for app preferences
CREATE TABLE IF NOT EXISTS app_settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT ''
);

-- Ingredient-level Bill of Materials (Phase 4)
CREATE TABLE IF NOT EXISTS bom_ingredients (
  sku_id           INTEGER NOT NULL REFERENCES skus(sku_id),
  seq              INTEGER NOT NULL,
  ingredient_code  TEXT,
  ingredient_name  TEXT NOT NULL,
  qty_per_serving  REAL,
  unit             TEXT,
  price_per_unit   REAL,
  ingredient_cost  REAL NOT NULL,         -- ต้นทุนสูตร in baht
  ingredient_type  TEXT,                  -- 'protein'|'carb'|'veg'|'sauce'|'oil'|'seasoning'|'other'
  source_file      TEXT,
  PRIMARY KEY (sku_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_bom_sku  ON bom_ingredients(sku_id);
CREATE INDEX IF NOT EXISTS idx_bom_code ON bom_ingredients(ingredient_code);

-- Monthly per-SKU unit_cost overrides (Phase 8)
-- Falls back to sku_cost.unit_cost when no monthly row exists for the period.
CREATE TABLE IF NOT EXISTS sku_cost_monthly (
  sku_id        INTEGER NOT NULL REFERENCES skus(sku_id),
  period_month  TEXT    NOT NULL,        -- 'YYYY-MM'
  unit_cost     REAL    NOT NULL,
  notes         TEXT,
  updated_at    TEXT    DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (sku_id, period_month)
);
CREATE INDEX IF NOT EXISTS idx_skucm_period ON sku_cost_monthly(period_month);
CREATE INDEX IF NOT EXISTS idx_skucm_sku    ON sku_cost_monthly(sku_id);
