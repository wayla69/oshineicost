# -*- coding: utf-8 -*-
"""Branch targets — the owner's rule (2026-09-05): a branch's target for a month
is its OWN actual of the same month one year earlier — net revenue (ex-VAT,
ex-service charge, rules.net_revenue_sum) and covers — rolling forward every
year on its own: September 2026 is measured against September 2025, September
2027 against September 2026, and so on. Nothing to maintain.

A figure typed into Settings → Monthly Targets still wins for that branch and
month (source 'manual'); a branch with no sales in last year's month (opened
since, or no data yet) has no target (source 'none') and the pages keep their
"vs normal" baselines. Every reader of branch_targets goes through here so the
home brief, the management report and the Settings editor agree.
"""
from typing import Optional

from datetime import date as _date, timedelta as _timedelta

from rules import net_revenue_sum, scope_filter

BASIS_LATE_DAYS = 7      # a branch whose first sale of last year's month is > 7 days after the chain's: a part month, no basis


def prev_year_month(month: str) -> str:
    """'2026-09' → '2025-09'."""
    return f"{int(month[:4]) - 1}{month[4:]}"


def targets_for_month(con, month: str, branch: Optional[str] = None, group: Optional[str] = None) -> list:
    """One row per branch in scope: branch_id, branch, period_month, revenue_target,
    customer_target, source ('manual' | 'last_year' | 'none'), basis_month and last
    year's actuals (basis_revenue, basis_customers) whatever the source."""
    basis = prev_year_month(month)
    clauses = ["b.name != 'ALL'"]
    params: dict = {"month": month, "basis": basis}
    scope_filter(clauses, params, branch, group)
    rows = con.execute(f"""
        SELECT b.branch_id, b.name AS branch,
               COALESCE(bt.revenue_target, 0)  AS manual_revenue,
               COALESCE(bt.customer_target, 0) AS manual_customers,
               COALESCE(ly.rev, 0)             AS basis_revenue,
               COALESCE(ly.cust, 0)            AS basis_customers,
               ly.first_day                    AS basis_first_day
        FROM branches b
        LEFT JOIN branch_targets bt ON bt.branch_id = b.branch_id AND bt.period_month = :month
        LEFT JOIN (SELECT st.branch_id, ROUND({net_revenue_sum('st')}, 2) AS rev, SUM(st.customer_count) AS cust,
                          MIN(date(st.sold_at)) AS first_day
                     FROM sales_transaction st
                    WHERE st.source = 'Easy' AND strftime('%Y-%m', st.sold_at) = :basis
                    GROUP BY st.branch_id) ly ON ly.branch_id = b.branch_id
        WHERE {' AND '.join(clauses)}
        ORDER BY b.name""", params).fetchall()
    # DV1 (phone table review 2026-10-25, §5 no. 5): last year's month is a basis only when the branch traded through it - a
    # branch that opened at its end (นครพนม 31 Oct 2025: one day, ฿39K) gave a target of one day and 3792 % "of target". Its
    # first sale that month must fall within BASIS_LATE_DAYS of the chain's first sale that month, else no automatic target
    chain_first = con.execute("SELECT MIN(date(sold_at)) FROM sales_transaction WHERE source = 'Easy' "
                              "AND strftime('%Y-%m', sold_at) = ?", (basis,)).fetchone()[0]
    late_from = ((_date.fromisoformat(chain_first) + _timedelta(days=BASIS_LATE_DAYS)).isoformat()
                 if chain_first else None)
    out = []
    for r in rows:
        manual_rev = float(r["manual_revenue"] or 0)
        basis_rev = float(r["basis_revenue"] or 0)
        basis_cust = int(r["basis_customers"] or 0)
        partial = bool(late_from and r["basis_first_day"] and r["basis_first_day"] > late_from)
        if manual_rev > 0:
            source, rev, cust = "manual", manual_rev, int(r["manual_customers"] or 0)
        elif basis_rev > 0 and not partial:
            source, rev, cust = "last_year", float(round(basis_rev)), basis_cust
        else:
            source, rev, cust = "none", 0.0, 0
        out.append({"branch_id": r["branch_id"], "branch": r["branch"], "period_month": month,
                    "revenue_target": rev, "customer_target": cust, "source": source,
                    "basis_month": basis, "basis_revenue": float(round(basis_rev)), "basis_customers": basis_cust,
                    "basis_partial": partial})
    return out


def target_revenue_total(con, month: str, branch: Optional[str] = None, group: Optional[str] = None) -> float:
    """The scope's revenue target for the month (sum of the branch targets)."""
    return round(sum(r["revenue_target"] for r in targets_for_month(con, month, branch, group)), 2)


def attainment(con, month: str, rev_by_branch: dict, factor: float = 1.0,
               branch: Optional[str] = None, group: Optional[str] = None) -> dict:
    """DV1 (phone table review 2026-10-25) · THE "% of target" of a scope, like for like: the branches that both have a
    target for the month AND sold in it - their sales x factor (the month-end projection factor, 1 for a closed month)
    over their targets. The chain's "124 % of target" counted the sales of ten branches without a target (new since last
    year) against the targets of the others, plus the target of a branch that no longer sells (โคราช2), so it disagreed
    with every branch's own %. rev_by_branch = {branch name: sales to date}. Returns {"target", "projected", "pct",
    "branches", "no_target_branches", "no_target_revenue"} (projected / no_target_revenue scaled by factor)."""
    rows = {r["branch"]: r for r in targets_for_month(con, month, branch, group)
            if r["source"] != "none" and r["revenue_target"] > 0}
    tgt = proj = other = 0.0
    n = n_other = 0
    for nm, rev in rev_by_branch.items():
        rev = float(rev or 0)
        if nm in rows and rev > 0:
            tgt += rows[nm]["revenue_target"]
            proj += rev * factor
            n += 1
        elif rev > 0:
            other += rev * factor
            n_other += 1
    return {"target": round(tgt, 2) if n else None, "projected": round(proj, 2) if n else None,
            "pct": round(proj / tgt * 100, 1) if tgt else None, "branches": n,
            "no_target_branches": n_other, "no_target_revenue": round(other, 2)}
