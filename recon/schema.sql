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
    -- 'ANY' = the code can land in more than one bank (e.g. generic 'Bank Transfer'
    -- → KTB or KBANK); the matcher checks both statements' residual lines.
    bank              TEXT NOT NULL DEFAULT 'NONE' CHECK(bank IN
                        ('KTB','KBANK','KTC','BAY','SCB','BBL','TTB','PROMPTPAY','ANY','NONE','UNKNOWN')),
    reconcile         INTEGER NOT NULL DEFAULT 1,   -- 0 = informational only (e.g. ENTERTAIN)
    notes             TEXT,
    updated_at        TEXT DEFAULT (datetime('now')),
    updated_by        TEXT
);

-- ─── Config: per-lane reconciliation method (the owner's "ตารางจับคู่" params) ──
-- One row per (category × bank) lane; bank='*' = every bank in the category.
-- Encodes the owner's advanced mapping table: which documents reconcile the lane
-- (doc1 = summary/slip, doc2 = bank statement), the method, and the parameters
-- the matcher reads (tolerance override, settlement lag, multi-day merge).
-- NULL parameter = engine default (today's hardcoded constants).
CREATE TABLE IF NOT EXISTS recon_method_config (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    category         TEXT NOT NULL,             -- CASH|QR|TRANSFER|CREDIT|GRAB|LINEMAN|COUPON
    bank             TEXT NOT NULL DEFAULT '*', -- specific bank/acquirer or '*' fallback
    doc1             TEXT,                      -- 'deposit_slip','cc_summary','grab_report','lineman_report','points_summary'
    doc2             TEXT,                      -- 'bank_stmt_KTB','bank_stmt_KBANK','bank_stmt_*'
    method           TEXT NOT NULL DEFAULT 'direct'
                       CHECK(method IN ('direct','two_stage','cash_3way','itemized','issuer_points')),
    tol_abs          REAL,                      -- monthly tolerance override (NULL = TOL_ABS)
    tol_abs_day      REAL,                      -- daily tolerance override (NULL = TOL_ABS_DAY)
    lag_days         INTEGER,                   -- max settlement lag in days (NULL = engine default)
    merge_days       INTEGER,                   -- max days merged into ONE bank credit (NULL = default)
    fee_vat_expected INTEGER NOT NULL DEFAULT 0,-- 1 = doc carries MDR/commission fee + VAT (gross≠bank)
    notes            TEXT,
    updated_at       TEXT DEFAULT (datetime('now')),
    updated_by       TEXT,
    UNIQUE(category, bank)
);

-- ─── CASH stage-1 evidence: typed deposit slips (สลิปนำฝาก) ────────────────────
-- Staff type the slip amount per POS sale day (cash from day D deposited D+1
-- before 13:00). Enables the explicit 3-way: POS cash ↔ slip ↔ bank statement.
CREATE TABLE IF NOT EXISTS recon_deposit_slip (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id     INTEGER NOT NULL REFERENCES branches(branch_id),
    sale_day      TEXT NOT NULL,        -- POS day D the cash belongs to (YYYY-MM-DD)
    deposit_day   TEXT,                 -- day staff deposited (normally D+1)
    amount        REAL NOT NULL,
    attachment_id INTEGER,              -- optional slip photo (recon_attachment.id)
    note          TEXT,
    created_by    TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(branch_id, sale_day)
);
CREATE INDEX IF NOT EXISTS idx_rds_scope ON recon_deposit_slip(branch_id, sale_day);

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

-- AUD-6: append-only enforcement. The audit log is the evidentiary backbone for
-- an auditable reconciliation, so no code path or direct DB edit may rewrite or
-- delete an entry — only INSERT is allowed. (Re-created idempotently at startup;
-- a DB swap drops them with the table and schema.sql restores them.)
CREATE TRIGGER IF NOT EXISTS recon_audit_log_no_update
    BEFORE UPDATE ON recon_audit_log
    BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS recon_audit_log_no_delete
    BEFORE DELETE ON recon_audit_log
    BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;

-- ─── Immutable close snapshot (AUD-5) ─────────────────────────────────────────
-- At sign-off (approve/close) the working-paper export is frozen to a file on the
-- volume + a hashed row here, so "what management signed" survives later edits.
CREATE TABLE IF NOT EXISTS recon_close_snapshot (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   INTEGER NOT NULL REFERENCES recon_sessions(session_id),
    branch_id    INTEGER,
    period_month TEXT,
    status       TEXT,                 -- the transition that froze it: approved | closed
    file_name    TEXT,
    file_path    TEXT,                 -- relative path under SNAPSHOT_DIR on the volume
    sha256       TEXT,                 -- of the frozen .xlsx bytes
    size_bytes   INTEGER,
    created_by   TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    source_manifest TEXT             -- JSON [{file,sha256,type,provider}] of the source docs frozen with this snapshot
);
CREATE INDEX IF NOT EXISTS idx_rcs_session ON recon_close_snapshot(session_id);

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

-- ─── Manual cross-channel MOVE (booked-in-wrong-channel correction) ───────────
-- Staff/manager declares that ฿amount was booked under the WRONG channel and
-- actually belongs in another (e.g. a QR sale rung up as CASH). The move shifts the
-- amount on the POS side OUT of (from_category,from_bank) and INTO (to_category,
-- to_bank), correcting BOTH lanes at once (กระทบสองยอดพร้อมกัน). Applied at
-- read-time in category_reconciliation/daily_reconciliation; fully reversible by
-- deleting the row. No UNIQUE constraint — many moves coexist; identity is the id.
CREATE TABLE IF NOT EXISTS recon_channel_move (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id     INTEGER NOT NULL REFERENCES branches(branch_id),
    period_month  TEXT    NOT NULL,
    from_category TEXT    NOT NULL,
    from_bank     TEXT    NOT NULL DEFAULT 'NONE',
    to_category   TEXT    NOT NULL,
    to_bank       TEXT    NOT NULL DEFAULT 'NONE',
    amount        REAL    NOT NULL,
    note          TEXT,
    created_by    TEXT,
    created_at    TEXT    DEFAULT (datetime('now')),
    day           TEXT                -- sale day of the mis-entry; NULL = legacy (lands on the lane's last POS day)
);
CREATE INDEX IF NOT EXISTS idx_rcm_scope ON recon_channel_move(branch_id, period_month);

-- ─── Per-day acknowledged variances (reconcile BY DATE) ───────────────────────
-- Staff reconcile a channel day-by-day: each differing day is either fixed/matched
-- or acknowledged here with a reason. One row per (branch × period × category × day).
-- variance = the day's POS↔money-in gap at the moment it was acknowledged (snapshot
-- for the audit trail / Excel close pack). A channel's todo clears when every
-- differing day is acknowledged (or the whole channel is accepted via recon_lane_adj).
CREATE TABLE IF NOT EXISTS recon_day_ack (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id    INTEGER NOT NULL REFERENCES branches(branch_id),
    period_month TEXT    NOT NULL,
    category     TEXT    NOT NULL,
    day          TEXT    NOT NULL,            -- 'YYYY-MM-DD'
    variance     REAL    NOT NULL DEFAULT 0,  -- acknowledged day variance (money-in − POS)
    note         TEXT,
    created_by   TEXT,
    created_at   TEXT    DEFAULT (datetime('now')),
    UNIQUE(branch_id, period_month, category, day)
);
CREATE INDEX IF NOT EXISTS idx_rda_scope ON recon_day_ack(branch_id, period_month);

-- ─── Chart-of-accounts mapping (slot → account code + name) ───────────────────
-- Lets the reports carry an account code + name per channel / journal slot. Filled
-- in later by the owner (the columns render blank until then). 'slot' is a channel
-- key (CASH/QR/TRANSFER/CREDIT/GRAB/LINEMAN/COUPON) or a journal slot
-- (DISCOUNT/FEE/VAT/BANK/REVENUE). Amounts are always recorded GROSS with the
-- discount shown separately, so each can post to its own account.
CREATE TABLE IF NOT EXISTS recon_account_map (
    slot         TEXT PRIMARY KEY,
    account_code TEXT,
    account_name TEXT,
    updated_at   TEXT DEFAULT (datetime('now')),
    updated_by   TEXT
);

-- ─── Statement-code overrides (manager-editable remap of bank abbreviations) ──
-- Lets a manager fix a mis-classified statement code (e.g. the SDCH=cash case, or a
-- non-revenue IORSDT → OTHER) from the app WITHOUT a code change + redeploy. Takes
-- precedence over parsers.bank.STATEMENT_CODE_MAP; applied at upload time and re-run
-- over existing recon_bank_lines when saved. code is stored uppercased.
CREATE TABLE IF NOT EXISTS recon_statement_code_map (
    code        TEXT PRIMARY KEY,        -- statement abbreviation / keyword (UPPER)
    channel     TEXT NOT NULL,           -- QR/TRANSFER/CASH_DEP/CC_SETTLE/GRAB_PAYOUT/LINEMAN_PAYOUT/FEE/OTHER
    note        TEXT,
    updated_by  TEXT,
    updated_at  TEXT DEFAULT (datetime('now'))
);

-- ─── Recon login accounts (recon/auth.py — SEPARATE from the dashboard's app_user) ──
-- Owner decision 2026-10-06: recon has its own users, roles and session. Enforcement
-- is off until RECON_LOGIN=1 (see recon/LOGIN_ROLLOUT.md). pw_hash = scrypt with a
-- per-user salt (never the password). pw_version is stamped into every session
-- cookie: a reset / password change / deactivation bumps it, which kills the old
-- sessions. The recon_ prefix is what makes the dashboard's DB swap carry this table
-- forward (backend/core.py _PRESERVED_PREFIXES) — never rename it outside recon_.
CREATE TABLE IF NOT EXISTS recon_users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    display_name   TEXT    NOT NULL DEFAULT '',
    role           TEXT    NOT NULL CHECK(role IN ('manager','accountant','viewer')),
    pw_hash        TEXT    NOT NULL,
    pw_version     INTEGER NOT NULL DEFAULT 1,
    active         INTEGER NOT NULL DEFAULT 1,
    must_change_pw INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    created_by     TEXT,
    last_login_at  TEXT,
    failed_count   INTEGER NOT NULL DEFAULT 0,
    locked_until   TEXT
);
