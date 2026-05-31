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

CREATE INDEX IF NOT EXISTS idx_tx_date          ON sales_transaction(sold_at);
CREATE INDEX IF NOT EXISTS idx_tx_branch        ON sales_transaction(branch_id, sold_at);
CREATE INDEX IF NOT EXISTS idx_tx_source_date   ON sales_transaction(source, sold_at);
CREATE INDEX IF NOT EXISTS idx_tx_branch_src    ON sales_transaction(branch_id, source, sold_at);
-- Expression index for strftime('%Y-%m', sold_at) = :month filters used by many endpoints
CREATE INDEX IF NOT EXISTS idx_tx_yearmonth     ON sales_transaction(strftime('%Y-%m', sold_at));
-- Composite for branch + month filter (very common)
CREATE INDEX IF NOT EXISTS idx_tx_branch_ym     ON sales_transaction(branch_id, strftime('%Y-%m', sold_at));
CREATE INDEX IF NOT EXISTS idx_sku_period       ON sales_sku_monthly(period_month);
CREATE INDEX IF NOT EXISTS idx_sku_branch_per   ON sales_sku_monthly(branch_id, period_month);
CREATE INDEX IF NOT EXISTS idx_ssku_sku         ON sales_sku_monthly(sku_id);
CREATE INDEX IF NOT EXISTS idx_sms_period       ON sales_month_summary(period_month);
CREATE INDEX IF NOT EXISTS idx_sku_category     ON skus(category_id);

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
