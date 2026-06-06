-- Revenue Reconciliation Schema
-- Extends the existing oshinei.db (same SQLite file, new tables only)
-- All tables prefixed with recon_ to avoid conflicts

-- ─── Upload Tracking ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS recon_uploads (
    upload_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type  TEXT NOT NULL CHECK(source_type IN ('bank','credit_card','grab','lineman')),
    branch_id    INTEGER REFERENCES branches(branch_id),
    provider     TEXT,            -- 'KBank','SCB','BBL','KTB','TTB' / 'Visa','Mastercard','AMEX','JCB' / 'Grab' / 'Lineman'
    file_name    TEXT NOT NULL,
    period_month TEXT,            -- 'YYYY-MM'
    row_count    INTEGER,
    status       TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','parsed','confirmed','error')),
    error_msg    TEXT,
    uploaded_at  TEXT NOT NULL DEFAULT (datetime('now')),
    confirmed_at TEXT,
    confirmed_by TEXT
);

-- ─── Bank Statement Lines ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS recon_bank_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id        INTEGER NOT NULL REFERENCES recon_uploads(upload_id) ON DELETE CASCADE,
    branch_id        INTEGER REFERENCES branches(branch_id),
    txn_date         TEXT NOT NULL,    -- 'YYYY-MM-DD'
    value_date       TEXT,             -- 'YYYY-MM-DD' settlement date (may differ)
    description      TEXT,             -- raw description from bank
    channel          TEXT,             -- 'QR','TRANSFER','DEPOSIT','CASH_DEP','FEE','OTHER' (normalised)
    debit            REAL DEFAULT 0,   -- money out
    credit           REAL DEFAULT 0,   -- money in (revenue)
    balance          REAL,
    ref_no           TEXT,             -- bank reference / PromptPay ref
    match_status     TEXT NOT NULL DEFAULT 'unmatched' CHECK(match_status IN ('matched','unmatched','excluded','manual')),
    match_id         INTEGER REFERENCES recon_matches(match_id),
    notes            TEXT
);

CREATE INDEX IF NOT EXISTS idx_rbl_branch_date ON recon_bank_lines(branch_id, txn_date);

-- ─── Credit Card Settlement Lines ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS recon_cc_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id        INTEGER NOT NULL REFERENCES recon_uploads(upload_id) ON DELETE CASCADE,
    branch_id        INTEGER REFERENCES branches(branch_id),
    settlement_date  TEXT NOT NULL,    -- 'YYYY-MM-DD'
    card_type        TEXT,             -- 'VISA','MASTERCARD','AMEX','JCB','UNIONPAY'
    txn_count        INTEGER DEFAULT 0,
    gross_amount     REAL DEFAULT 0,
    refund_amount    REAL DEFAULT 0,
    mdr_fee          REAL DEFAULT 0,   -- Merchant Discount Rate fee
    net_amount       REAL DEFAULT 0,   -- gross - refund - mdr_fee
    terminal_id      TEXT,
    merchant_id      TEXT,
    batch_no         TEXT,
    match_status     TEXT NOT NULL DEFAULT 'unmatched' CHECK(match_status IN ('matched','unmatched','excluded','manual')),
    match_id         INTEGER REFERENCES recon_matches(match_id),
    notes            TEXT
);

CREATE INDEX IF NOT EXISTS idx_rccl_branch_date ON recon_cc_lines(branch_id, settlement_date);

-- ─── Grab Report Lines ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS recon_grab_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id        INTEGER NOT NULL REFERENCES recon_uploads(upload_id) ON DELETE CASCADE,
    branch_id        INTEGER REFERENCES branches(branch_id),
    order_date       TEXT NOT NULL,    -- 'YYYY-MM-DD'
    order_id         TEXT,             -- Grab order ID
    merchant_name    TEXT,             -- outlet name from Grab
    order_type       TEXT,             -- 'GrabFood','GrabMart'
    order_status     TEXT,             -- 'Completed','Cancelled','Refunded'
    food_amount      REAL DEFAULT 0,   -- customer paid (ex delivery)
    commission_rate  REAL,             -- % charged by Grab
    commission_amt   REAL DEFAULT 0,
    promo_deduct     REAL DEFAULT 0,   -- Grab-funded promotions
    net_payout       REAL DEFAULT 0,   -- food_amount - commission - promo
    payout_date      TEXT,             -- 'YYYY-MM-DD' when Grab transfers to bank
    match_status     TEXT NOT NULL DEFAULT 'unmatched' CHECK(match_status IN ('matched','unmatched','excluded','manual')),
    match_id         INTEGER REFERENCES recon_matches(match_id),
    notes            TEXT
);

CREATE INDEX IF NOT EXISTS idx_rgl_branch_date   ON recon_grab_lines(branch_id, order_date);
CREATE INDEX IF NOT EXISTS idx_rgl_payout_date   ON recon_grab_lines(branch_id, payout_date);

-- ─── Lineman Report Lines ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS recon_lineman_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id        INTEGER NOT NULL REFERENCES recon_uploads(upload_id) ON DELETE CASCADE,
    branch_id        INTEGER REFERENCES branches(branch_id),
    order_date       TEXT NOT NULL,    -- 'YYYY-MM-DD'
    order_id         TEXT,             -- LINE MAN order number
    merchant_name    TEXT,
    order_status     TEXT,             -- 'สำเร็จ','ยกเลิก'
    food_amount      REAL DEFAULT 0,   -- food price (ex delivery fee)
    delivery_fee     REAL DEFAULT 0,   -- customer-paid delivery (may go to Lineman, not restaurant)
    commission_rate  REAL,
    commission_amt   REAL DEFAULT 0,
    vat_on_commission REAL DEFAULT 0,
    promo_deduct     REAL DEFAULT 0,
    net_payout       REAL DEFAULT 0,   -- food_amount - commission - vat_commission - promo
    payout_date      TEXT,             -- 'YYYY-MM-DD'
    match_status     TEXT NOT NULL DEFAULT 'unmatched' CHECK(match_status IN ('matched','unmatched','excluded','manual')),
    match_id         INTEGER REFERENCES recon_matches(match_id),
    notes            TEXT
);

CREATE INDEX IF NOT EXISTS idx_rll_branch_date   ON recon_lineman_lines(branch_id, order_date);
CREATE INDEX IF NOT EXISTS idx_rll_payout_date   ON recon_lineman_lines(branch_id, payout_date);

-- ─── Reconciliation Sessions ──────────────────────────────────────────────────
-- One session = one branch × one month × one close attempt

CREATE TABLE IF NOT EXISTS recon_sessions (
    session_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id    INTEGER NOT NULL REFERENCES branches(branch_id),
    period_month TEXT NOT NULL,        -- 'YYYY-MM'
    status       TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','in_review','approved','closed')),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    created_by   TEXT,
    approved_at  TEXT,
    approved_by  TEXT,
    notes        TEXT,
    UNIQUE(branch_id, period_month)
);

-- ─── Match Records ────────────────────────────────────────────────────────────
-- Links one external line to one POS aggregate (day or month level)

CREATE TABLE IF NOT EXISTS recon_matches (
    match_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       INTEGER NOT NULL REFERENCES recon_sessions(session_id),
    source_type      TEXT NOT NULL CHECK(source_type IN ('bank','credit_card','grab','lineman')),
    -- external side
    external_table   TEXT NOT NULL,    -- 'recon_bank_lines' | 'recon_cc_lines' | etc.
    external_id      INTEGER NOT NULL,
    external_amount  REAL NOT NULL,
    -- POS side (nullable for unmatched external items)
    pos_level        TEXT CHECK(pos_level IN ('transaction','daily','monthly')),
    pos_receipt_no   TEXT,             -- NULL for aggregate matches
    pos_branch_id    INTEGER REFERENCES branches(branch_id),
    pos_date         TEXT,             -- 'YYYY-MM-DD' for daily aggregates
    pos_period_month TEXT,             -- 'YYYY-MM' for monthly aggregates
    pos_amount       REAL,
    -- match metadata
    match_type       TEXT NOT NULL CHECK(match_type IN ('exact','fuzzy_amount','aggregated','manual')),
    confidence       REAL DEFAULT 1.0, -- 0.0–1.0
    variance         REAL GENERATED ALWAYS AS (COALESCE(external_amount,0) - COALESCE(pos_amount,0)) STORED,
    matched_at       TEXT NOT NULL DEFAULT (datetime('now')),
    matched_by       TEXT NOT NULL DEFAULT 'auto'  -- 'auto' or user identifier
);

CREATE INDEX IF NOT EXISTS idx_rm_session      ON recon_matches(session_id);
CREATE INDEX IF NOT EXISTS idx_rm_source       ON recon_matches(source_type, external_id);

-- ─── Discrepancies ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS recon_discrepancies (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       INTEGER NOT NULL REFERENCES recon_sessions(session_id),
    -- what kind of issue
    disc_type        TEXT NOT NULL CHECK(disc_type IN (
                         'unmatched_external',   -- external line has no POS counterpart
                         'unmatched_pos',        -- POS shows revenue with no external evidence
                         'amount_mismatch',      -- matched but amounts differ beyond tolerance
                         'timing_diff'           -- same amount, different settlement date
                     )),
    source_type      TEXT NOT NULL,
    external_id      INTEGER,
    match_id         INTEGER REFERENCES recon_matches(match_id),
    amount_external  REAL,
    amount_pos       REAL,
    variance         REAL,
    -- resolution
    resolution       TEXT NOT NULL DEFAULT 'pending' CHECK(resolution IN ('pending','accepted','adjusted','rejected')),
    resolution_note  TEXT,
    resolved_at      TEXT,
    resolved_by      TEXT
);

CREATE INDEX IF NOT EXISTS idx_rd_session   ON recon_discrepancies(session_id, resolution);

-- ─── Daily POS Aggregate Cache ────────────────────────────────────────────────
-- Pre-computed from sales_transaction + sales_month_summary for fast matching
-- Refreshed whenever recon session is opened

CREATE TABLE IF NOT EXISTS recon_pos_daily (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id    INTEGER NOT NULL REFERENCES branches(branch_id),
    txn_date     TEXT NOT NULL,        -- 'YYYY-MM-DD'
    cash         REAL DEFAULT 0,
    credit_card  REAL DEFAULT 0,
    transfer     REAL DEFAULT 0,
    qr           REAL DEFAULT 0,
    delivery     REAL DEFAULT 0,       -- Grab + Lineman combined if POS tracks separately
    total        REAL DEFAULT 0,
    txn_count    INTEGER DEFAULT 0,
    refreshed_at TEXT DEFAULT (datetime('now')),
    UNIQUE(branch_id, txn_date)
);
