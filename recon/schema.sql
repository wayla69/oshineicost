-- Revenue Reconciliation Schema
-- Same oshinei.db file as the main dashboard (shared Fly volume).
-- All tables are prefixed recon_ and reference the existing branches table.

-- ─── Upload Tracking ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS recon_uploads (
    upload_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type  TEXT NOT NULL CHECK(source_type IN ('bank','credit_card','grab','lineman')),
    branch_id    INTEGER REFERENCES branches(branch_id),
    provider     TEXT,            -- 'KBank','SCB','BBL','KTB','TTB' / 'Visa','Mastercard','AMEX','JCB' / 'Grab' / 'Lineman'
    file_name    TEXT NOT NULL,
    period_month TEXT,            -- 'YYYY-MM'
    row_count    INTEGER,
    file_hash    TEXT,            -- sha256 of the raw upload → blocks identical re-uploads (double-count guard)
    status       TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','parsed','confirmed','error')),
    error_msg    TEXT,
    uploaded_at  TEXT NOT NULL DEFAULT (datetime('now')),
    confirmed_at TEXT,
    confirmed_by TEXT
);

-- ─── Bank Statement Lines ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS recon_bank_lines (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id    INTEGER NOT NULL REFERENCES recon_uploads(upload_id) ON DELETE CASCADE,
    branch_id    INTEGER REFERENCES branches(branch_id),
    account_no   TEXT,             -- bank account the line belongs to (→ branch via recon_bank_account)
    txn_date     TEXT NOT NULL,    -- 'YYYY-MM-DD'
    value_date   TEXT,             -- settlement date (may differ from txn_date)
    description  TEXT,
    channel      TEXT,             -- 'QR','TRANSFER','CASH_DEP','CC_SETTLE','GRAB_PAYOUT','LINEMAN_PAYOUT','FEE','OTHER'
    debit        REAL DEFAULT 0,
    credit       REAL DEFAULT 0,   -- money in (revenue side)
    balance      REAL,
    ref_no       TEXT,
    match_status TEXT NOT NULL DEFAULT 'unmatched' CHECK(match_status IN ('matched','unmatched','excluded','manual')),
    match_id     INTEGER REFERENCES recon_matches(match_id),
    notes        TEXT
);

CREATE INDEX IF NOT EXISTS idx_rbl_branch_date ON recon_bank_lines(branch_id, txn_date);

-- ─── Credit Card Settlement Lines ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS recon_cc_lines (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id       INTEGER NOT NULL REFERENCES recon_uploads(upload_id) ON DELETE CASCADE,
    branch_id       INTEGER REFERENCES branches(branch_id),
    settlement_date TEXT NOT NULL,    -- 'YYYY-MM-DD'
    card_type       TEXT,             -- 'VISA','MASTERCARD','AMEX','JCB','UNIONPAY'
    txn_count       INTEGER DEFAULT 0,
    gross_amount    REAL DEFAULT 0,
    refund_amount   REAL DEFAULT 0,
    mdr_fee         REAL DEFAULT 0,   -- Merchant Discount Rate fee (ex-VAT)
    vat_amount      REAL DEFAULT 0,   -- VAT on the fee, parsed from the statement
    net_amount      REAL DEFAULT 0,   -- gross - refund - mdr_fee - vat_amount
    acquirer        TEXT,             -- 'KTB','KBANK','KTC' (→ POS credit-by-acquirer)
    fee_manual      REAL,             -- staff-entered fee (overrides mdr_fee when set)
    vat_manual      REAL,             -- staff-entered VAT (overrides vat_amount when set)
    fee_vat_by      TEXT,
    fee_vat_at      TEXT,
    terminal_id     TEXT,
    merchant_id     TEXT,
    batch_no        TEXT,
    match_status    TEXT NOT NULL DEFAULT 'unmatched' CHECK(match_status IN ('matched','unmatched','excluded','manual')),
    match_id        INTEGER REFERENCES recon_matches(match_id),
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_rccl_branch_date ON recon_cc_lines(branch_id, settlement_date);

-- ─── Grab Report Lines ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS recon_grab_lines (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id       INTEGER NOT NULL REFERENCES recon_uploads(upload_id) ON DELETE CASCADE,
    branch_id       INTEGER REFERENCES branches(branch_id),
    order_date      TEXT NOT NULL,    -- 'YYYY-MM-DD'
    order_id        TEXT,
    merchant_name   TEXT,             -- outlet name from Grab (use to map to branch)
    order_type      TEXT,             -- 'GrabFood','GrabMart'
    order_status    TEXT,             -- 'Completed','Cancelled','Refunded'
    food_amount     REAL DEFAULT 0,
    commission_rate REAL,
    commission_amt  REAL DEFAULT 0,   -- commission ex-VAT
    vat_amount      REAL DEFAULT 0,   -- VAT 7% on the commission (Grab bundles it; we split it out)
    promo_deduct    REAL DEFAULT 0,
    net_payout      REAL DEFAULT 0,   -- food_amount - commission - vat - promo
    fee_manual      REAL,             -- staff-entered fee (overrides commission_amt when set)
    vat_manual      REAL,             -- staff-entered VAT (overrides vat_amount when set)
    fee_vat_by      TEXT,
    fee_vat_at      TEXT,
    payout_date     TEXT,             -- 'YYYY-MM-DD' when Grab transfers to bank
    match_status    TEXT NOT NULL DEFAULT 'unmatched' CHECK(match_status IN ('matched','unmatched','excluded','manual')),
    match_id        INTEGER REFERENCES recon_matches(match_id),
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_rgl_branch_date  ON recon_grab_lines(branch_id, order_date);
CREATE INDEX IF NOT EXISTS idx_rgl_payout_date  ON recon_grab_lines(branch_id, payout_date);

-- ─── Lineman Report Lines ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS recon_lineman_lines (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id         INTEGER NOT NULL REFERENCES recon_uploads(upload_id) ON DELETE CASCADE,
    branch_id         INTEGER REFERENCES branches(branch_id),
    order_date        TEXT NOT NULL,    -- 'YYYY-MM-DD'
    order_id          TEXT,
    merchant_name     TEXT,
    order_status      TEXT,             -- 'Completed','Cancelled'
    food_amount       REAL DEFAULT 0,
    delivery_fee      REAL DEFAULT 0,
    commission_rate   REAL,
    commission_amt    REAL DEFAULT 0,
    vat_on_commission REAL DEFAULT 0,
    promo_deduct      REAL DEFAULT 0,
    net_payout        REAL DEFAULT 0,
    fee_manual        REAL,           -- staff-entered fee (overrides commission_amt when set)
    vat_manual        REAL,           -- staff-entered VAT on commission
    fee_vat_by        TEXT,
    fee_vat_at        TEXT,
    payout_date       TEXT,
    match_status      TEXT NOT NULL DEFAULT 'unmatched' CHECK(match_status IN ('matched','unmatched','excluded','manual')),
    match_id          INTEGER REFERENCES recon_matches(match_id),
    notes             TEXT
);

CREATE INDEX IF NOT EXISTS idx_rll_branch_date  ON recon_lineman_lines(branch_id, order_date);
CREATE INDEX IF NOT EXISTS idx_rll_payout_date  ON recon_lineman_lines(branch_id, payout_date);

-- ─── Reconciliation Sessions ──────────────────────────────────────────────────

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

CREATE TABLE IF NOT EXISTS recon_matches (
    match_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES recon_sessions(session_id),
    source_type     TEXT NOT NULL CHECK(source_type IN ('bank','credit_card','grab','lineman')),
    external_table  TEXT NOT NULL,
    external_id     INTEGER NOT NULL,
    external_amount REAL NOT NULL,
    pos_level       TEXT CHECK(pos_level IN ('transaction','daily','monthly')),
    pos_receipt_no  TEXT,
    pos_branch_id   INTEGER REFERENCES branches(branch_id),
    pos_date        TEXT,
    pos_period_month TEXT,
    pos_amount      REAL,
    match_type      TEXT NOT NULL CHECK(match_type IN ('exact','fuzzy_amount','aggregated','manual')),
    confidence      REAL DEFAULT 1.0,
    variance        REAL GENERATED ALWAYS AS (COALESCE(external_amount,0) - COALESCE(pos_amount,0)) STORED,
    matched_at      TEXT NOT NULL DEFAULT (datetime('now')),
    matched_by      TEXT NOT NULL DEFAULT 'auto'
);

CREATE INDEX IF NOT EXISTS idx_rm_session ON recon_matches(session_id);
CREATE INDEX IF NOT EXISTS idx_rm_source  ON recon_matches(source_type, external_id);

-- ─── Discrepancies ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS recon_discrepancies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES recon_sessions(session_id),
    disc_type       TEXT NOT NULL CHECK(disc_type IN (
                        'unmatched_external',
                        'unmatched_pos',
                        'amount_mismatch',
                        'timing_diff'
                    )),
    source_type     TEXT NOT NULL,
    category        TEXT,             -- CASH|QR|TRANSFER|CREDIT|GRAB|LINEMAN|COUPON
    bank            TEXT,             -- KTB|KBANK|KTC|PROMPTPAY|NONE for the affected bucket
    external_id     INTEGER,
    match_id        INTEGER REFERENCES recon_matches(match_id),
    amount_external REAL,
    amount_pos      REAL,
    variance        REAL,
    resolution      TEXT NOT NULL DEFAULT 'pending' CHECK(resolution IN ('pending','accepted','adjusted','rejected')),
    resolution_note TEXT,
    assigned_to     TEXT,             -- accounting staff this is assigned to
    resolved_at     TEXT,
    resolved_by     TEXT
);

CREATE INDEX IF NOT EXISTS idx_rd_session ON recon_discrepancies(session_id, resolution);

-- ─── Daily POS Cache ──────────────────────────────────────────────────────────
-- Built from sales_transaction by the matcher on each run-match call.
-- Avoids rescanning millions of rows for every matching operation.

-- sales_transaction has no per-tender breakdown, so daily totals are whole-day
-- only. Per-tender reconciliation is done at the monthly level (pos_payment_month)
-- in the matcher, not here.
CREATE TABLE IF NOT EXISTS recon_pos_daily (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id    INTEGER NOT NULL REFERENCES branches(branch_id),
    txn_date     TEXT NOT NULL,
    total        REAL DEFAULT 0,
    txn_count    INTEGER DEFAULT 0,
    refreshed_at TEXT DEFAULT (datetime('now')),
    UNIQUE(branch_id, txn_date)
);

-- ─── Config: POS payment-code → canonical category + bank ─────────────────────
-- Replaces regex classification. Seeded from pos_payment_month (seed_payment_map.py),
-- then confirmed/edited by staff in the Setup UI. payment_code_norm is the raw Easy
-- code trimmed + internal-space-collapsed so 'Cash'/'CASH' and ' KNNQR'/'KNNQR' unify.
CREATE TABLE IF NOT EXISTS recon_payment_map (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_code_norm TEXT NOT NULL UNIQUE,
    category          TEXT NOT NULL CHECK(category IN
                        ('CASH','QR','TRANSFER','CREDIT','GRAB','LINEMAN','COUPON','ENTERTAIN','OTHER')),
    bank              TEXT NOT NULL DEFAULT 'NONE' CHECK(bank IN
                        ('KTB','KBANK','KTC','BAY','SCB','BBL','PROMPTPAY','NONE','UNKNOWN')),
    reconcile         INTEGER NOT NULL DEFAULT 1,   -- 0 = informational only (e.g. ENTERTAIN)
    notes             TEXT,
    updated_at        TEXT DEFAULT (datetime('now')),
    updated_by        TEXT
);

-- ─── Config: per-branch bank accounts (attribute bank lines → branch) ─────────
CREATE TABLE IF NOT EXISTS recon_bank_account (
    account_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id    INTEGER NOT NULL REFERENCES branches(branch_id),
    bank         TEXT NOT NULL CHECK(bank IN ('KTB','KBANK')),
    account_no   TEXT NOT NULL,
    account_name TEXT,
    active       INTEGER NOT NULL DEFAULT 1,
    UNIQUE(bank, account_no)
);

-- ─── Config: per-branch credit-card acquirer MIDs (attribute CC lines → branch) ─
CREATE TABLE IF NOT EXISTS recon_acquirer_mid (
    mid_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id   INTEGER NOT NULL REFERENCES branches(branch_id),
    acquirer    TEXT NOT NULL CHECK(acquirer IN ('KTB','KBANK','KTC','BAY','SCB','BBL','KTCPOINT','BAYPOINT')),
    merchant_id TEXT,
    terminal_id TEXT,
    active      INTEGER NOT NULL DEFAULT 1
);

-- ─── Per-category reconciliation result (one row per session × category × bank) ─
-- Written by the matcher on each run-match. This is what the UI "category cards"
-- read: POS tender total vs the matching external statement total.
CREATE TABLE IF NOT EXISTS recon_category_result (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES recon_sessions(session_id),
    category    TEXT NOT NULL,    -- CASH|QR|TRANSFER|CREDIT|GRAB|LINEMAN|COUPON
    bank        TEXT NOT NULL,    -- KTB|KBANK|KTC|PROMPTPAY|NONE|UNKNOWN
    pos_amount  REAL DEFAULT 0,   -- POS tender (inc-VAT)
    ext_amount  REAL DEFAULT 0,   -- external statement gross
    ext_net     REAL DEFAULT 0,   -- external net after effective fee/VAT (delivery/CC)
    variance    REAL DEFAULT 0,   -- ext_amount - pos_amount
    status      TEXT NOT NULL,    -- matched|mismatch|pos_only|ext_only|no_source
    computed_at TEXT DEFAULT (datetime('now')),
    UNIQUE(session_id, category, bank)
);

CREATE INDEX IF NOT EXISTS idx_rcr_session ON recon_category_result(session_id);

-- ─── Audit Log ────────────────────────────────────────────────────────────────
-- Full field-level change log for every reconciliation mutation.
-- One row per changed field; create/delete events get a single row with field=NULL.
CREATE TABLE IF NOT EXISTS recon_audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL DEFAULT (datetime('now')),
    entity    TEXT NOT NULL,   -- 'session' | 'discrepancy' | 'payment_map' | 'line' | 'upload'
    entity_id INTEGER NOT NULL,
    action    TEXT NOT NULL,   -- 'create' | 'update' | 'delete'
    field     TEXT,            -- NULL for create/delete (no field-level diff)
    old_val   TEXT,
    new_val   TEXT,
    by        TEXT NOT NULL DEFAULT 'staff'
);
CREATE INDEX IF NOT EXISTS idx_audit_ts     ON recon_audit_log(ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON recon_audit_log(entity, entity_id, ts DESC);

-- ─── Manual reconciliation (tick-to-match, reversible) ──────────────────────────
-- Staff tick one-or-more POS day amounts + one-or-more statement lines that
-- together reconcile (e.g. a lump-sum bank credit covering several days), then
-- Match. Fully reversible: Unmatch deletes the record and frees the lines.
CREATE TABLE IF NOT EXISTS recon_manual_match (
    match_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id    INTEGER NOT NULL REFERENCES branches(branch_id),
    category     TEXT NOT NULL,
    bank         TEXT NOT NULL,
    period_month TEXT NOT NULL,        -- 'YYYY-MM'
    pos_amount   REAL DEFAULT 0,       -- sum of ticked POS day amounts
    ext_amount   REAL DEFAULT 0,       -- sum of ticked statement lines
    variance     REAL DEFAULT 0,       -- ext - pos
    pos_days     TEXT,                 -- JSON [{"day":"YYYY-MM-DD","amount":n}]
    note         TEXT,
    matched_by   TEXT,
    matched_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_rmm_scope
    ON recon_manual_match(branch_id, period_month, category, bank);

-- Statement lines that belong to a manual match (so it can be reversed).
CREATE TABLE IF NOT EXISTS recon_manual_match_line (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id  INTEGER NOT NULL REFERENCES recon_manual_match(match_id) ON DELETE CASCADE,
    ext_table TEXT NOT NULL,           -- recon_bank_lines | recon_cc_lines | recon_grab_lines | recon_lineman_lines
    ext_id    INTEGER NOT NULL,
    amount    REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_rmml_match ON recon_manual_match_line(match_id);

-- Generic evidence attachments (photos / PDFs) staff add as reconciliation proof,
-- scoped to a branch + day (+ optional channel/category, e.g. COUPON).
-- Files are stored on the Fly volume at ATTACH_DIR/{file_path}; only metadata here.
CREATE TABLE IF NOT EXISTS recon_attachment (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id   INTEGER NOT NULL,
    day         TEXT NOT NULL,            -- YYYY-MM-DD
    category    TEXT,                     -- optional channel (CASH/QR/CREDIT/COUPON…)
    file_name   TEXT NOT NULL,
    mime        TEXT,
    size_bytes  INTEGER,
    file_path   TEXT,                     -- relative path under ATTACH_DIR
    note        TEXT,
    uploaded_by TEXT,
    uploaded_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_recon_attachment ON recon_attachment(branch_id, day);

-- ─── Lane adjustments (off-POS revenue / manual deposit entries) ──────────────
-- One row per (branch × period × category × bank).
-- adj_pos  = manual POS-side addition  (e.g. off-POS QR scanned on KTB PromptPay)
-- adj_ext  = manual EXT-side addition  (e.g. cash deposit not reflected in bank statement)
-- adj_type 'accept' = v2 acknowledged variance: zero-amount marker (bank='ALL')
--   whose note is the staff reason; clears the channel's todo so the month can close.
-- Applied at summary-read time; no re-run-match needed.
CREATE TABLE IF NOT EXISTS recon_lane_adj (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id    INTEGER NOT NULL REFERENCES branches(branch_id),
    period_month TEXT    NOT NULL,
    category     TEXT    NOT NULL,
    bank         TEXT    NOT NULL DEFAULT 'NONE',
    adj_pos      REAL    NOT NULL DEFAULT 0,
    adj_ext      REAL    NOT NULL DEFAULT 0,
    note         TEXT,
    adj_type     TEXT    NOT NULL DEFAULT 'other'
                     CHECK(adj_type IN ('off_pos','deposit_note','other','accept')),
    created_by   TEXT,
    created_at   TEXT    DEFAULT (datetime('now')),
    UNIQUE(branch_id, period_month, category, bank)
);
CREATE INDEX IF NOT EXISTS idx_rla_scope ON recon_lane_adj(branch_id, period_month);
