#!/usr/bin/env python3
"""
fraud_analysis.py — robust statistical layer for the Surin/Ubon fraud report.

Adds methods that the original raw-discount t-test lacked:
  1. Revenue confounding check (daily disc ~ daily revenue correlation)
  2. disc%-based per-person comparison (revenue-normalized) via PERMUTATION test
  3. Day-of-week confound diagnosis (off-day DOW skew + weekend disc% premium)
  4. Bonferroni multiple-comparison correction across all tested employees
  5. Exclusive-day analysis (can a single person ever be isolated?)
  6. POS-account alternation analysis (Surin Manager/admin co-occurrence)
  7. Fixed-amount x account cross-tab (is a repeated amount tied to one account?)

Design intent: every per-person "signal" gets a confound verdict so the report can
state honestly what is attributable to a person vs what is an artifact of the work
schedule / sales volume. There is NO POS-account -> employee mapping in the DB, so
direct attribution is impossible; this module quantifies exactly why.
"""
import sqlite3, math, random, datetime
from collections import defaultdict

random.seed(20260620)
W = "substr(sold_at,1,10) BETWEEN ? AND ?"
DOW_EN = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
DOW_TH = ['จ.', 'อ.', 'พ.', 'พฤ.', 'ศ.', 'ส.', 'อา.']


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _pearson(xs, ys):
    n = min(len(xs), len(ys))
    if n < 3:
        return 0.0
    xs, ys = xs[:n], ys[:n]
    mx, my = _mean(xs), _mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return num / den if den else 0.0


def permutation_test(worked, off, iters=20000):
    """Two-sided permutation test on difference of means. Robust, distribution-free."""
    if len(worked) < 3 or len(off) < 3:
        return {"mean_worked": _mean(worked), "mean_off": _mean(off),
                "diff": None, "p": None, "n_worked": len(worked), "n_off": len(off)}
    obs = _mean(worked) - _mean(off)
    pool = worked + off
    nw, no = len(worked), len(off)
    cnt = 0
    for _ in range(iters):
        random.shuffle(pool)
        diff = sum(pool[:nw]) / nw - sum(pool[nw:]) / no
        if abs(diff) >= abs(obs):
            cnt += 1
    return {"mean_worked": _mean(worked), "mean_off": _mean(off),
            "diff": obs, "p": (cnt + 1) / (iters + 1), "n_worked": nw, "n_off": no}


def load_daily(con, branch_id, date_from, date_to):
    """Daily totals over ALL bills (the true branch discount rate per day)."""
    daily = {}
    q = (f"SELECT substr(sold_at,1,10) d, round(sum(discount)) disc, "
         f"round(sum((total_inc_vat-COALESCE(service_charge,0))/1.07+discount)) rev, "
         f"count(*) bills FROM sales_transaction WHERE branch_id=? AND {W} GROUP BY d")
    for r in con.execute(q, (branch_id, date_from, date_to)):
        rev = r["rev"] or 1
        daily[r["d"]] = {"disc": r["disc"] or 0, "rev": rev, "bills": r["bills"],
                         "pct": (r["disc"] or 0) / rev * 100,
                         "dow": datetime.date.fromisoformat(r["d"]).weekday()}
    return daily


def revenue_confound(daily):
    days = sorted(daily)
    discs = [daily[d]["disc"] for d in days]
    revs = [daily[d]["rev"] for d in days]
    return {"r": round(_pearson(discs, revs), 3), "n": len(days)}


def dow_premium(daily):
    """Average disc% by day-of-week; weekend vs weekday premium."""
    by = defaultdict(list)
    for d, v in daily.items():
        by[v["dow"]].append(v["pct"])
    avg = {k: round(_mean(v), 2) for k, v in by.items()}
    wk = _mean([p for k, v in by.items() if k < 5 for p in v])
    we = _mean([p for k, v in by.items() if k >= 5 for p in v])
    return {"by_dow": avg, "weekday": round(wk, 2), "weekend": round(we, 2),
            "premium": round(we / wk, 2) if wk else 0}


def person_compare(att_emp, daily):
    """Raw-discount lift (old, confounded) AND revenue-normalized disc% lift (new)."""
    raw_w = [daily[d]["disc"] for d in att_emp if att_emp[d] and d in daily]
    raw_o = [daily[d]["disc"] for d in att_emp if not att_emp[d] and d in daily]
    pct_w = [daily[d]["pct"] for d in att_emp if att_emp[d] and d in daily]
    pct_o = [daily[d]["pct"] for d in att_emp if not att_emp[d] and d in daily]
    raw = permutation_test(raw_w, raw_o)
    pct = permutation_test(pct_w, pct_o)
    raw_lift = (raw["mean_worked"] / raw["mean_off"]) if raw["mean_off"] else None
    pct_lift = (pct["mean_worked"] / pct["mean_off"]) if pct["mean_off"] else None
    return {"raw": raw, "pct": pct,
            "raw_lift": round(raw_lift, 2) if raw_lift else None,
            "pct_lift": round(pct_lift, 2) if pct_lift else None}


def offday_dow_skew(att_emp, daily):
    """Is this person's day-off concentrated on particular DOW (a confound source)?"""
    off = defaultdict(int)
    work = defaultdict(int)
    for d, w in att_emp.items():
        k = datetime.date.fromisoformat(d).weekday()
        (work if w else off)[k] += 1
    n_off = sum(off.values()) or 1
    # share of off-days that fall on a weekend
    we_off = (off.get(5, 0) + off.get(6, 0)) / n_off
    # the dominant off-DOW
    dom = max(off.items(), key=lambda x: x[1]) if off else (None, 0)
    return {"off_by_dow": dict(off), "work_by_dow": dict(work),
            "weekend_off_share": round(we_off, 2),
            "dominant_off_dow": dom[0], "dominant_off_n": dom[1]}


def exclusive_days(att, eids, target, daily):
    """Days where ONLY `target` (among tracked `eids`) worked. If 0, no isolation possible."""
    emp = att.get(target, {})
    excl = [d for d in emp if emp[d] and d in daily
            and not any(att.get(o, {}).get(d, False) for o in eids if o != target)]
    avg = _mean([daily[d]["pct"] for d in excl]) if excl else None
    return {"n": len(excl), "avg_pct": round(avg, 2) if avg is not None else None, "days": excl}


def account_alternation(con, branch_id, date_from, date_to, accounts):
    """Per-day presence of each discount account. High solo-rate => alternation/sharing."""
    q = (f"SELECT substr(sold_at,1,10) d, cashier, round(sum(discount)) disc "
         f"FROM sales_transaction WHERE branch_id=? AND {W} AND discount>0 GROUP BY d, cashier")
    day = defaultdict(dict)
    for r in con.execute(q, (branch_id, date_from, date_to)):
        day[r["d"]][r["cashier"]] = r["disc"]
    a, b = accounts[0], accounts[1]
    both = sum(1 for d in day if a in day[d] and b in day[d])
    only_a = sum(1 for d in day if a in day[d] and b not in day[d])
    only_b = sum(1 for d in day if b in day[d] and a not in day[d])
    total = len(day)
    return {"accounts": accounts, "total_disc_days": total, "both": both,
            "only_a": only_a, "only_b": only_b,
            "cooccurrence_rate": round(100 * both / total, 1) if total else 0,
            "solo_rate": round(100 * (only_a + only_b) / total, 1) if total else 0}


def fixed_amount_xtab(con, branch_id, date_from, date_to, top_n=8):
    """For each most-repeated discount amount, how is it split across POS accounts?"""
    amts = [r["amt"] for r in con.execute(
        f"SELECT round(discount) amt, count(*) n FROM sales_transaction "
        f"WHERE branch_id=? AND {W} AND discount>0 GROUP BY amt ORDER BY n DESC LIMIT ?",
        (branch_id, date_from, date_to, top_n))]
    out = []
    for amt in amts:
        accs = [(r["cashier"], r["n"]) for r in con.execute(
            f"SELECT cashier, count(*) n FROM sales_transaction "
            f"WHERE branch_id=? AND {W} AND round(discount)=? GROUP BY cashier ORDER BY n DESC",
            (branch_id, date_from, date_to, amt))]
        tot = sum(n for _, n in accs)
        top_share = round(100 * accs[0][1] / tot, 0) if tot else 0
        out.append({"amt": amt, "total": tot, "accounts": accs, "top_share": top_share})
    return out


def peer_benchmark(con, date_from, date_to):
    """disc% by branch_type (master vs franchise) — the FAIR peer comparison.
    Chain-average mixes self-funded franchises (≈0 discount) with company-funded
    master branches, which deflates the benchmark and overstates master anomalies."""
    by_type = {}
    for r in con.execute(
        f"SELECT b.branch_type bt, count(DISTINCT s.branch_id) nb, "
        f"round(100.0*sum(s.discount)/nullif(sum((s.total_inc_vat-COALESCE(s.service_charge,0))/1.07+s.discount),0),2) pct "
        f"FROM sales_transaction s JOIN branches b ON b.branch_id=s.branch_id "
        f"WHERE substr(s.sold_at,1,10) BETWEEN ? AND ? GROUP BY b.branch_type", (date_from, date_to)):
        by_type[r["bt"]] = {"n_branches": r["nb"], "pct": r["pct"]}
    branches = []
    for r in con.execute(
        f"SELECT s.branch_id bid, b.name, b.branch_type bt, "
        f"round(sum(s.discount)) disc, "
        f"round(100.0*sum(s.discount)/nullif(sum((s.total_inc_vat-COALESCE(s.service_charge,0))/1.07+s.discount),0),2) pct "
        f"FROM sales_transaction s JOIN branches b ON b.branch_id=s.branch_id "
        f"WHERE substr(s.sold_at,1,10) BETWEEN ? AND ? GROUP BY s.branch_id ORDER BY pct DESC", (date_from, date_to)):
        branches.append({"branch_id": r["bid"], "name": r["name"], "branch_type": r["bt"],
                         "disc": r["disc"] or 0, "pct": r["pct"] or 0})
    return {"by_type": by_type, "branches": branches}


def cash_signature(con, branch_id, date_from, date_to):
    """The Udon proven-fraud signature: are discounts disproportionately on CASH bills?
    Cash-pocketing requires cash tender — discounts on card/QR/transfer can't be skimmed.
    lift = (cash share of discounted bills) / (cash share of all bills). >>1 = suspicious."""
    q = (f"SELECT "
         f"sum(CASE WHEN s.discount>0 AND p.has_cash=1 THEN 1 ELSE 0 END) disc_cash, "
         f"sum(CASE WHEN s.discount>0 THEN 1 ELSE 0 END) disc_all, "
         f"sum(CASE WHEN p.has_cash=1 THEN 1 ELSE 0 END) cash_all, count(*) tot "
         f"FROM sales_transaction s LEFT JOIN "
         f"(SELECT receipt_no, branch_id, MAX(CASE WHEN payment_code='Cash' THEN 1 ELSE 0 END) has_cash "
         f" FROM sale_payment GROUP BY branch_id, receipt_no) p "
         f"ON p.receipt_no=s.receipt_no AND p.branch_id=s.branch_id "
         f"WHERE s.branch_id=? AND substr(s.sold_at,1,10) BETWEEN ? AND ?")
    r = con.execute(q, (branch_id, date_from, date_to)).fetchone()
    disc_all = r["disc_all"] or 0
    tot = r["tot"] or 1
    cash_share_disc = 100 * (r["disc_cash"] or 0) / disc_all if disc_all else 0
    cash_share_all = 100 * (r["cash_all"] or 0) / tot
    lift = cash_share_disc / cash_share_all if cash_share_all else 0
    # cash split on the most-repeated discount amounts
    fx = []
    for row in con.execute(
        f"SELECT round(s.discount) amt, count(*) n, "
        f"sum(CASE WHEN p.has_cash=1 THEN 1 ELSE 0 END) cash_n FROM sales_transaction s LEFT JOIN "
        f"(SELECT receipt_no, branch_id, MAX(CASE WHEN payment_code='Cash' THEN 1 ELSE 0 END) has_cash "
        f" FROM sale_payment GROUP BY branch_id, receipt_no) p "
        f"ON p.receipt_no=s.receipt_no AND p.branch_id=s.branch_id "
        f"WHERE s.branch_id=? AND substr(s.sold_at,1,10) BETWEEN ? AND ? AND s.discount>0 "
        f"GROUP BY amt ORDER BY n DESC LIMIT 6", (branch_id, date_from, date_to)):
        fx.append({"amt": row["amt"], "n": row["n"],
                   "cash_pct": round(100 * row["cash_n"] / row["n"]) if row["n"] else 0})
    return {"cash_share_disc": round(cash_share_disc, 1), "cash_share_all": round(cash_share_all, 1),
            "lift": round(lift, 2), "disc_bills": disc_all, "fixed_amounts": fx}


def disc_pct_buckets(con, branch_id, date_from, date_to):
    """Discount-% magnitude buckets per POS account. High-% discounts (≥20%, ~50% half-price,
    100% comp) are high-authority types (exec/shareholder/elderly/child) — a low-authority
    account (e.g. Take Order) applying many of these is an authorization-inversion red flag."""
    q = (f"SELECT cashier, "
         f"sum(CASE WHEN pct<20 THEN 1 ELSE 0 END) lt20, "
         f"sum(CASE WHEN pct>=20 AND pct<50 THEN 1 ELSE 0 END) b2050, "
         f"sum(CASE WHEN pct>=50 THEN 1 ELSE 0 END) ge50, "
         f"count(*) n, round(max(pct)) mx FROM "
         f"(SELECT cashier, 100.0*discount/((total_inc_vat-COALESCE(service_charge,0))/1.07+discount) pct "
         f" FROM sales_transaction WHERE branch_id=? AND substr(sold_at,1,10) BETWEEN ? AND ? AND discount>0) "
         f"GROUP BY cashier ORDER BY n DESC")
    out = []
    for r in con.execute(q, (branch_id, date_from, date_to)):
        out.append({"cashier": r["cashier"], "n": r["n"], "lt20": r["lt20"],
                    "b2050": r["b2050"], "ge50": r["ge50"], "max": r["mx"],
                    "high_n": r["b2050"] + r["ge50"]})
    return out


# coupon series that represent high-authority / eligibility-based comps (must be vouched)
SPECIAL_COUPONS = {6, 30, 10, 11, 29, 40, 8, 35, 76, 27, 33, 34, 75, 12, 14}


def discount_attribution(con, branch_id, date_from, date_to):
    """FACT CHECK: split discounted bills into reason-coded / coupon-coded / unattributed.
    Overturns the 'no reason' assumption by reading discount_reason_id + coupon_series_id."""
    W = "substr(sold_at,1,10) BETWEEN ? AND ?"
    r = con.execute(
        f"SELECT count(*) n, "
        f"sum(CASE WHEN COALESCE(discount_reason_id,0)>0 THEN 1 ELSE 0 END) reason_n, "
        f"sum(CASE WHEN COALESCE(coupon_series_id,0)>0 THEN 1 ELSE 0 END) coupon_n, "
        f"sum(CASE WHEN COALESCE(discount_reason_id,0)=0 AND COALESCE(coupon_series_id,0)=0 THEN 1 ELSE 0 END) neither_n, "
        f"round(sum(CASE WHEN COALESCE(discount_reason_id,0)=0 AND COALESCE(coupon_series_id,0)=0 THEN discount ELSE 0 END)) neither_amt "
        f"FROM sales_transaction WHERE branch_id=? AND {W} AND discount>0",
        (branch_id, date_from, date_to)).fetchone()
    # unattributed comps by cashier (the Surin signature)
    neither_by_cashier = [(x["cashier"], x["n"], round(x["amt"]), round(x["avg"])) for x in con.execute(
        f"SELECT cashier, count(*) n, sum(discount) amt, avg(discount) avg FROM sales_transaction "
        f"WHERE branch_id=? AND {W} AND discount>0 AND COALESCE(discount_reason_id,0)=0 AND COALESCE(coupon_series_id,0)=0 "
        f"GROUP BY cashier ORDER BY n DESC", (branch_id, date_from, date_to))]
    return {"disc_bills": r["n"], "reason_n": r["reason_n"], "coupon_n": r["coupon_n"],
            "neither_n": r["neither_n"], "neither_amt": r["neither_amt"] or 0,
            "neither_by_cashier": neither_by_cashier}


def coupon_breakdown(con, branch_id, date_from, date_to, names, top_n=10):
    """Per coupon-series usage from the bill-level coupon_series_id (with covers + dominant account)."""
    W = "substr(sold_at,1,10) BETWEEN ? AND ?"
    out = []
    for x in con.execute(
        f"SELECT coupon_series_id sid, count(*) bills, round(sum(discount)) amt, "
        f"sum(customer_count) covers FROM sales_transaction "
        f"WHERE branch_id=? AND {W} AND discount>0 AND COALESCE(coupon_series_id,0)>0 "
        f"GROUP BY sid ORDER BY bills DESC LIMIT ?", (branch_id, date_from, date_to, top_n)):
        tc = con.execute(
            f"SELECT cashier, count(*) n FROM sales_transaction WHERE branch_id=? AND {W} "
            f"AND discount>0 AND coupon_series_id=? GROUP BY cashier ORDER BY n DESC LIMIT 1",
            (branch_id, date_from, date_to, x["sid"])).fetchone()
        out.append({"sid": x["sid"], "name": names.get(x["sid"], f"#{x['sid']}"),
                    "bills": x["bills"], "amt": x["amt"] or 0, "covers": x["covers"] or 0,
                    "top_cashier": tc["cashier"] if tc else "-", "top_n": tc["n"] if tc else 0,
                    "special": x["sid"] in SPECIAL_COUPONS})
    return out


def bonferroni(results, alpha=0.05):
    """results: list of dicts each with key 'p_pct'. Returns corrected threshold + verdicts."""
    m = sum(1 for r in results if r.get("p_pct") is not None)
    thr = alpha / m if m else alpha
    for r in results:
        p = r.get("p_pct")
        r["bonferroni_pass"] = (p is not None and p < thr)
    return {"n_tests": m, "threshold": thr}


def analyze(db_path, employees, branch_ids, date_from, date_to, load_attendance):
    """Top-level: returns the full robust-analysis bundle for the report."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    att = load_attendance()
    coupon_names = {r["coupon_series_id"]: r["name"] for r in con.execute("SELECT coupon_series_id,name FROM coupon_series")}

    branches = {}
    for br, bid in branch_ids.items():
        daily = load_daily(con, bid, date_from, date_to)
        eids = [e[0] for e in employees if e[2] == br]
        accounts = ("Manager", "admin") if br == "surin" else ("Take Order", "Cashier")
        branches[br] = {
            "daily": daily,
            "revenue_confound": revenue_confound(daily),
            "dow_premium": dow_premium(daily),
            "alternation": account_alternation(con, bid, date_from, date_to, accounts),
            "fixed_xtab": fixed_amount_xtab(con, bid, date_from, date_to),
            "cash_signature": cash_signature(con, bid, date_from, date_to),
            "disc_pct_buckets": disc_pct_buckets(con, bid, date_from, date_to),
            "attribution": discount_attribution(con, bid, date_from, date_to),
            "coupons": coupon_breakdown(con, bid, date_from, date_to, coupon_names),
            "eids": eids,
        }

    # peer benchmark (master vs franchise) + the Udon proven-fraud reference case
    peer = peer_benchmark(con, date_from, date_to)
    UDON_ID = 25
    udon = {
        "branch_id": UDON_ID,
        "cash_signature": cash_signature(con, UDON_ID, date_from, date_to),
        "fixed_xtab": fixed_amount_xtab(con, UDON_ID, date_from, date_to),
        "attribution": discount_attribution(con, UDON_ID, date_from, date_to),
        "coupons": coupon_breakdown(con, UDON_ID, date_from, date_to, coupon_names),
        "pct": next((b["pct"] for b in peer["branches"] if b["branch_id"] == UDON_ID), None),
        "disc": next((b["disc"] for b in peer["branches"] if b["branch_id"] == UDON_ID), None),
    }

    people = []
    for eid, role, br, full, nick, fn in employees:
        daily = branches[br]["daily"]
        emp = att.get(eid, {})
        cmp = person_compare(emp, daily)
        skew = offday_dow_skew(emp, daily)
        excl = exclusive_days(att, branches[br]["eids"], eid, daily)
        people.append({
            "eid": eid, "role": role, "branch": br, "fullname": full, "nick": nick,
            "raw_lift": cmp["raw_lift"], "p_raw": cmp["raw"]["p"],
            "pct_lift": cmp["pct_lift"], "p_pct": cmp["pct"]["p"],
            "mean_pct_worked": round(cmp["pct"]["mean_worked"], 2),
            "mean_pct_off": round(cmp["pct"]["mean_off"], 2),
            "mean_raw_worked": round(cmp["raw"]["mean_worked"]),
            "mean_raw_off": round(cmp["raw"]["mean_off"]),
            "n_worked": cmp["pct"]["n_worked"], "n_off": cmp["pct"]["n_off"],
            "skew": skew, "exclusive": excl,
        })

    bonf = bonferroni(people)

    # confound verdict per person
    for p in people:
        dp = branches[p["branch"]]["dow_premium"]["premium"]
        reasons = []
        if p["exclusive"]["n"] == 0:
            reasons.append("ไม่มีวันทำงานเดี่ยว (แยกตัวคนทำไม่ได้)")
        if p["skew"]["weekend_off_share"] >= 0.4 and dp > 1.2:
            reasons.append(f"วันหยุดเอียงไปวันหยุดสุดสัปดาห์ ({int(p['skew']['weekend_off_share']*100)}%) ซึ่ง disc% สูงอยู่แล้ว → inverse artifact")
        if p["skew"]["dominant_off_dow"] in (1, 2) and p["skew"]["dominant_off_n"] >= 6 and dp > 1.2:
            reasons.append(f"หยุด{DOW_TH[p['skew']['dominant_off_dow']]}เป็นหลัก (วัน disc% ต่ำ) → worked disc% สูงเทียม")
        if p["p_pct"] is None:
            verdict = "ข้อมูลไม่พอ"
        elif not p["bonferroni_pass"]:
            verdict = "ไม่มีนัยสำคัญหลังแก้ Bonferroni"
        elif reasons:
            verdict = "นัยสำคัญแต่อธิบายได้ด้วยตารางกะ (confounded)"
        else:
            verdict = "นัยสำคัญ — ควรสืบเพิ่ม (ยังไม่ใช่หลักฐาน)"
        p["confound_reasons"] = reasons
        p["verdict"] = verdict

    con.close()
    return {"branches": branches, "people": people, "bonferroni": bonf,
            "peer": peer, "udon": udon,
            "date_from": date_from, "date_to": date_to}


if __name__ == "__main__":
    import sys, os, gen_surin_ubon_report as g
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    res = analyze(os.path.join(os.path.dirname(__file__), "oshinei.db"),
                  g.EMPLOYEES, g.BRANCH_IDS, g.DATE_FROM, g.DATE_TO, g.load_attendance)
    for br, b in res["branches"].items():
        print(f"\n=== {br.upper()} ===")
        print(f"  revenue confound r={b['revenue_confound']['r']}")
        print(f"  DOW premium weekend/weekday={b['dow_premium']['premium']}x "
              f"(we={b['dow_premium']['weekend']}% wk={b['dow_premium']['weekday']}%)")
        a = b["alternation"]
        print(f"  alternation {a['accounts']}: cooccur={a['cooccurrence_rate']}% solo={a['solo_rate']}%")
    print(f"\nBonferroni: {res['bonferroni']['n_tests']} tests, "
          f"threshold p<{res['bonferroni']['threshold']:.4f}")
    print(f"\n{'nick':<8}{'role':<16}{'raw_lift':>9}{'p_raw':>8}{'pct_lift':>9}{'p_pct':>8}{'excl':>5}  verdict")
    for p in sorted(res["people"], key=lambda x: (x["branch"], -(x["pct_lift"] or 0))):
        print(f"{p['nick']:<8}{p['role']:<16}{str(p['raw_lift']):>9}{p['p_raw']:>8.4f}"
              f"{str(p['pct_lift']):>9}{p['p_pct']:>8.4f}{p['exclusive']['n']:>5}  {p['verdict']}")
