"""Reconciliation engine — category/bank driven.

POS side: pos_payment_month, classified into (category, bank) buckets via the
editable recon_payment_map (seed_payment_map.py). Reconcile=0 codes (e.g.
ENTERTAIN comps) are skipped.

External side, per the owner's workflow:
  CASH               → bank statement CASH_DEP lines (either bank)
  QR(bank)           → bank statement QR lines for that bank
  TRANSFER(bank)     → bank statement TRANSFER lines for that bank
  CREDIT(acquirer)   → credit-card settlement gross for that acquirer
  GRAB / LINEMAN     → their report's gross (Completed orders)
  COUPON             → no external source yet → flagged for accounting

Fee + VAT are entered manually per line (Grab/Line Man/CC); the effective net
(gross − fee − VAT [− promo/refund]) is reported alongside.

Anything outside tolerance becomes a discrepancy for accounting staff.

Since 2026-09-05 a package: one module per lane (see each module's docstring), every
name re-exported here so callers and tests keep importing from recon.matcher."""

from ._shared import (  # noqa: F401
    TOL_ABS,
    TOL_PCT,
    TOL_ABS_DAY,
    TOL_PCT_DAY,
    DEF_CASH_MERGE,
    DEF_CASH_LAG,
    DEF_PAYOUT_MERGE,
    DEF_PAYOUT_LAG,
    LINEMAN_PAYOUT_THRESHOLD,
    DEF_LINEMAN_MERGE,
    DEF_LINEMAN_LAG,
    _CHANNEL_CATEGORY,
    _CATEGORY_SOURCE,
    method_config,
    _cfg,
    _within_tol,
    _provider_bank,
    _provider_acquirer,
    pos_category_totals,
    _eff_fee_vat,
    _pd,
    _point_mids,
    _cc_acquirer,
    _within_tol_day,
    pos_category_totals_daily,
    _manual_consumed,
    _lane_cov_key,
    _REAL_BANKS,
    _SPLIT_BY_BANK,
    _BANK_LABEL,
    _col_key,
    _col_label,
    _d,
)

from .cash import (  # noqa: F401
    prev_month_consumed_ids,
    deposit_slips,
    cash_three_way,
    _cash_pos_days,
    _claimed_lanes,
    _cash_dep_credits,
    cash_deposit_matching,
)

from .buckets import (  # noqa: F401
    external_buckets,
    _apply_transfer_any,
    channel_move_deltas,
    _external_buckets_daily,
)

from .points import (  # noqa: F401
    _POINT_SERIES_KW,
    point_pos_total,
    point_pos_total_daily,
    coupon_discount_total_daily,
    _point_settlement_gross,
    point_settlement_total,
)

from .payout import (  # noqa: F401
    _PAYOUT_CHANNEL,
    _report_net_by_category,
    _bank_payout_totals,
    _prev_payout_carried_amounts,
    payout_reconciliation,
    _PAYOUT_CANDIDATE_CHANNELS,
    _payout_net_by_day,
    _payout_candidate_credits,
    _prev_payout_consumed_ids,
    payout_matching,
    _PAYOUT_CHANNELS,
    _PAYOUT_CHAN_CAT,
)

from .coupon import (  # noqa: F401
    coupon_reconciliation,
)

from .credit import (  # noqa: F401
    _ACQ_BANK,
    _match_net_days_to_credits,
    _credit_bank_candidates,
    _carried_bank_line_ids,
    credit_three_way,
)

from .category import (  # noqa: F401
    category_reconciliation,
)

from .lines import (  # noqa: F401
    lane_lines,
    lane_pos_days,
    pos_lane_lines,
    lane_ext_lines,
    _pick_ext_lines,
    pos_codes_scope,
)

from .coverage import (  # noqa: F401
    _coverage,
    _line_status,
    _lane_status_map,
    upload_match_stats,
    bank_statement_coverage,
)

from .suggest import (  # noqa: F401
    suggest_reclassifications,
    _INTXFER_FLOOR,
    _INTXFER_ROUND,
    _INTXFER_SALES_KW,
    _INTXFER_CODES,
    suggest_internal_transfers,
    map_bank_suggestions,
)

from .board import (  # noqa: F401
    daily_reconciliation,
    _BOARD_CAT_ORDER,
    daily_board,
)

from .auto import (  # noqa: F401
    _LINE_TABLES,
    _reset_session,
    run_auto_match,
)

__all__ = [
    'TOL_ABS',
    'TOL_PCT',
    'TOL_ABS_DAY',
    'TOL_PCT_DAY',
    'DEF_CASH_MERGE',
    'DEF_CASH_LAG',
    'DEF_PAYOUT_MERGE',
    'DEF_PAYOUT_LAG',
    'LINEMAN_PAYOUT_THRESHOLD',
    'DEF_LINEMAN_MERGE',
    'DEF_LINEMAN_LAG',
    '_CHANNEL_CATEGORY',
    '_CATEGORY_SOURCE',
    'method_config',
    '_cfg',
    '_within_tol',
    '_provider_bank',
    '_provider_acquirer',
    'pos_category_totals',
    '_eff_fee_vat',
    '_pd',
    '_point_mids',
    '_cc_acquirer',
    '_within_tol_day',
    'pos_category_totals_daily',
    '_manual_consumed',
    '_lane_cov_key',
    '_REAL_BANKS',
    '_SPLIT_BY_BANK',
    '_BANK_LABEL',
    '_col_key',
    '_col_label',
    '_d',
    'prev_month_consumed_ids',
    'deposit_slips',
    'cash_three_way',
    '_cash_pos_days',
    '_claimed_lanes',
    '_cash_dep_credits',
    'cash_deposit_matching',
    'external_buckets',
    '_apply_transfer_any',
    'channel_move_deltas',
    '_external_buckets_daily',
    '_POINT_SERIES_KW',
    'point_pos_total',
    'point_pos_total_daily',
    'coupon_discount_total_daily',
    '_point_settlement_gross',
    'point_settlement_total',
    '_PAYOUT_CHANNEL',
    '_report_net_by_category',
    '_bank_payout_totals',
    '_prev_payout_carried_amounts',
    'payout_reconciliation',
    '_PAYOUT_CANDIDATE_CHANNELS',
    '_payout_net_by_day',
    '_payout_candidate_credits',
    '_prev_payout_consumed_ids',
    'payout_matching',
    '_PAYOUT_CHANNELS',
    '_PAYOUT_CHAN_CAT',
    'coupon_reconciliation',
    '_ACQ_BANK',
    '_match_net_days_to_credits',
    '_credit_bank_candidates',
    '_carried_bank_line_ids',
    'credit_three_way',
    'category_reconciliation',
    'lane_lines',
    'lane_pos_days',
    'pos_lane_lines',
    'lane_ext_lines',
    '_pick_ext_lines',
    'pos_codes_scope',
    '_coverage',
    '_line_status',
    '_lane_status_map',
    'upload_match_stats',
    'bank_statement_coverage',
    'suggest_reclassifications',
    '_INTXFER_FLOOR',
    '_INTXFER_ROUND',
    '_INTXFER_SALES_KW',
    '_INTXFER_CODES',
    'suggest_internal_transfers',
    'map_bank_suggestions',
    'daily_reconciliation',
    '_BOARD_CAT_ORDER',
    'daily_board',
    '_LINE_TABLES',
    '_reset_session',
    'run_auto_match',
]
