# -*- coding: utf-8 -*-
"""PR-J3 — the reports that set the daily issue slips (ใบตัดเบิก) against the other measures of the same stock.

  reconcile()  month-end:  system stock (from the slips)  vs  the physical count   (R1)
  daily()      every day:  what the branch issued (cost)  vs  the BoM of what it sold (R2)
  anomalies()  the branches whose class-A items run over the BoM for days in a row (PR-B4a, 1.4: or under it - direction='under')
  blank_class_a()  PR-B4a (1.4): the high-value items the sales say were used that day and that the slip leaves blank
  PR-S4        in mode `stock` (close_settings.mode()) there is no recipe: the BoM column of daily() / daily_day() / daily_items() /
               anomalies() / blank_class_a() is replaced by two comparators built from the CONFIRMED slips and the sales - the branch's own
               normal (the same weekday in the 8 weeks before (forecast.HISTORY_WEEKS), scaled by the sales of the day) and the median of the other company branches
               whose demand source is the slips (issued per 100,000 baht of sales over the 8 weeks before). bom_days is not called in that
               mode; mode `full` is untouched.
  log()        PR-J4 (R3): per branch and day, how the slip was handled — on time, late, reopened, reconfirmed —
               with the late purchase orders and the claims beside it; log_monthly() is one month of it
  slip_rows()  PR-J4 (R4): the confirmed slips as priced lines, for the Excel Accounting posts from

Rules (shared with issues.py):
  * "issued" is CONFIRMED slips only — a draft is not an issue;
  * a period / a day is [from, to) on the slip date; the month-end count is taken before trading, so the
    slip of the count date belongs to the period that starts there (stock.system_position uses the same);
  * the BoM of a day = the dishes sold that day × the recipe in force, in base units — computed by
    forecast.usage_history and kept in bcd_demand_day (+ bcd_demand_day_meta), rebuilt only for a day whose
    sales or whose release changed (bom_days). The router calls drop_bom_days() after every write to the
    recipe-side inputs (release / retire / new draft / recipes / prep / code review / central links /
    ingredients / units), exactly where it calls forecast.clear_usage_cache().
"""
import sqlite3
from datetime import date, datetime, timedelta, timezone

from . import forecast as FC
from . import issues as IS
from . import variance as VA
from .clock import BKK, now_bkk

OVER_PCT = 20.0            # a class-A item issued more than this % over its BoM that day is "over"
STREAK_DAYS = 3            # … and that many trading days in a row is a flag
UNDER_PCT = 30.0           # PR-B4a (1.4): a class-A item issued more than this % UNDER its BoM that day is "under" (3 days in a row = a flag)
BLANK_TOP = 8              # PR-B4a (1.4): the confirm dialog names this many blank class-A items, the most valuable first
SLIP_STATUS_TH = {"on_time": "ยืนยันตรงวัน", "late": "ล่าช้า", "draft": "ยังไม่ยืนยัน", "missing": "ไม่มีใบ",
                  "closed": "ไม่มียอดขาย"}
RECON_STATUS_TH = {"ok": "ตรงกัน (ในเกณฑ์)", "over": "นับได้มากกว่าระบบ", "under": "นับได้น้อยกว่าระบบ",
                   "not_counted": "ตรวจนับไม่ครบ"}
SLIP_NOTE_TH = {"on_time": "", "late": "ยืนยันหลังวันที่ของใบ", "draft": "มีร่างแล้ว ยังไม่ยืนยัน",
                "missing": "ยังไม่มีใบตัดเบิก", "closed": "ไม่มียอดขายและไม่มีใบตัดเบิก"}
SIGNAL_TH = {"over": "เบิกเกินเกณฑ์", "streak": "เบิกเกินต่อเนื่อง", "under_streak": "เบิกน้อยกว่าสูตรต่อเนื่อง"}
_EPS = 1e-6

# PR-S4 · mode `stock`: the comparators and their words
NORMAL_WEEKS = FC.HISTORY_WEEKS                    # the same weekday in this many weeks before the day
NORMAL_MIN_DAYS = 2                 # fewer slip days than this among them: no "normal of the branch"
MEDIAN_MIN_BRANCHES = 2             # fewer other branches on the slips than this: no all-branch median
MEDIAN_WINDOW_DAYS = 7 * FC.HISTORY_WEEKS             # the window (days before the day) the issued per PER_SALES baht is measured over
PER_SALES = 100000.0
UNDER_PCT_STOCK = 20.0              # O4: under the comparator by more than this % is "under" (mode full keeps UNDER_PCT)
BASIS_TH = {"normal": "ปกติของสาขา", "median": "ค่ากลาง", "both": "ปกติของสาขาและค่ากลาง"}


def _d(x) -> date:
    return x if isinstance(x, date) and not isinstance(x, datetime) else date.fromisoformat(str(x)[:10])


def _month_bounds(month: str) -> tuple:
    y, m = int(month[:4]), int(month[5:7])
    first = date(y, m, 1)
    nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return first, nxt - timedelta(days=1)


def _round(x, n=2):
    return None if x is None else round(x, n)


# ── the BoM day cache ────────────────────────────────────────────────────────
def _sales_table(con) -> str:
    return "v_sales_sku_daily" if con.execute("SELECT 1 FROM sqlite_master WHERE name='v_sales_sku_daily'").fetchone() \
        else "sales_sku_daily"


def _fingerprint(con, branch_id: int, d0, d1) -> dict:
    """{day: (sales rows, Σ qty)} of the days with sales in [d0, d1] — what a cached day was built from."""
    return {day: (n, q or 0.0) for day, n, q in con.execute(
        f"SELECT day, COUNT(*), SUM(qty) FROM {_sales_table(con)} WHERE branch_id=? AND day BETWEEN ? AND ? GROUP BY day",
        (branch_id, _d(d0).isoformat(), _d(d1).isoformat()))}


def _trading_days(con, branch_id: int, d0, d1) -> list:
    """ISO dates from d0 to d1 (inclusive) on which the branch sold anything."""
    return sorted(_fingerprint(con, branch_id, d0, d1))


def bom_days(con, branch_id: int, d0, d1, month: str, commit: bool = True) -> dict:
    """{day: {ingredient_code: base qty}} for every trading day in [d0, d1] (inclusive) — the BoM of what was sold.

    A day is STALE when it has no meta row, was built under another release than the one in force for `month`,
    or its sales changed since (row count / Σ qty). All stale days are rebuilt with ONE usage_history call over
    the stale span. The cache is written inside the caller's transaction and committed only when the caller had
    none open (`commit=False` leaves even that to the caller); a write that cannot get the lock costs nothing —
    the report is still answered from memory."""
    from .engine import release_in_force
    a, b = _d(d0), _d(d1)
    if b < a:
        return {}
    fp = _fingerprint(con, branch_id, a, b)
    rid = release_in_force(con, month)
    meta = {day: (r, n, q) for day, r, n, q in con.execute(
        "SELECT day, release_id, sales_rows, sales_qty FROM bcd_demand_day_meta WHERE branch_id=? AND day BETWEEN ? AND ?",
        (branch_id, a.isoformat(), b.isoformat()))}
    stale = sorted(day for day, (n, q) in fp.items()
                   if day not in meta or (meta[day][0] or 0) != (rid or 0) or meta[day][1] != n
                   or abs((meta[day][2] or 0.0) - q) > _EPS)
    orphans = [day for day in meta if day not in fp]            # sales removed since: the cached day goes too
    out = {day: {} for day in fp}
    fresh = set(fp) - set(stale)
    if fresh:
        for day, code, q in con.execute(
                "SELECT day, ingredient_code, qty_base FROM bcd_demand_day WHERE branch_id=? AND day BETWEEN ? AND ?",
                (branch_id, a.isoformat(), b.isoformat())):
            if day in fresh:
                out[day][code] = q
    if not stale and not orphans:
        return out
    if stale:
        want = set(stale)
        use, _days, _stats = FC.usage_history(con, branch_id, date.fromisoformat(stale[0]), date.fromisoformat(stale[-1]),
                                              month)
        for code, series in use.items():
            for day, q in series.items():
                if day in want and q:
                    out[day][code] = q
    outside = not con.in_transaction
    try:
        drop = stale + orphans
        con.executemany("DELETE FROM bcd_demand_day WHERE branch_id=? AND day=?", [(branch_id, d) for d in drop])
        con.executemany("DELETE FROM bcd_demand_day_meta WHERE branch_id=? AND day=?", [(branch_id, d) for d in drop])
        con.executemany("INSERT INTO bcd_demand_day(branch_id, day, ingredient_code, qty_base, release_id) VALUES(?,?,?,?,?)",
                        [(branch_id, d, c, q, rid) for d in stale for c, q in out[d].items()])
        built = datetime.now(timezone.utc).isoformat(timespec="seconds")
        con.executemany("INSERT INTO bcd_demand_day_meta(branch_id, day, release_id, sales_rows, sales_qty, built_at)"
                        " VALUES(?,?,?,?,?,?)", [(branch_id, d, rid, fp[d][0], fp[d][1], built) for d in stale])
        if commit and outside:
            con.commit()
    except sqlite3.OperationalError:                           # "database is locked": answer without caching
        if outside:
            con.rollback()
    return out


def drop_bom_days(con, branch_id: int = None) -> int:
    """Forget the cached BoM days (of one branch, or all) — a recipe-side input changed. The caller commits."""
    where, args = ("", ()) if branch_id is None else (" WHERE branch_id=?", (int(branch_id),))
    n = con.execute("DELETE FROM bcd_demand_day" + where, args).rowcount
    con.execute("DELETE FROM bcd_demand_day_meta" + where, args)
    return max(n, 0)


# ── what was issued ──────────────────────────────────────────────────────────
def issued(con, branch_id: int, d0, d1, by_day: bool = False) -> dict:
    """CONFIRMED slips only, slip dates in [d0, d1): {code: {used, waste}} in base units, or with `by_day`
    {day: {code: {used, waste}}}."""
    out = {}
    for day, code, u, w in con.execute(
            "SELECT s.slip_date, l.ingredient_code, SUM(COALESCE(l.qty_used_base, 0)), SUM(COALESCE(l.qty_waste_base, 0))"
            " FROM bc_issue_line l JOIN bc_issue_slip s USING(slip_id)"
            " WHERE s.branch_id=? AND s.status='confirmed' AND s.slip_date>=? AND s.slip_date<?"
            " GROUP BY s.slip_date, l.ingredient_code", (branch_id, _d(d0).isoformat(), _d(d1).isoformat())):
        cell = {"used": u or 0.0, "waste": w or 0.0}
        if by_day:
            out.setdefault(day, {})[code] = cell
        else:
            cur = out.setdefault(code, {"used": 0.0, "waste": 0.0})
            cur["used"] += cell["used"]
            cur["waste"] += cell["waste"]
    return out


def slip_coverage(con, branch_id: int, d0, d1) -> dict:
    """The trading days in [d0, d1) and how many of them have a confirmed slip."""
    a, b = _d(d0), _d(d1) - timedelta(days=1)
    if b < a:
        return {"trading_days": 0, "confirmed_days": 0, "missing_days": [], "draft_days": []}
    trading = _trading_days(con, branch_id, a, b)
    st = {s["date"]: s for s in IS.status_days(con, branch_id, a, b)}
    return {"trading_days": len(trading),
            "confirmed_days": sum(1 for d in trading if IS.is_confirmed(st[d])),
            "missing_days": [d for d in trading if st[d]["status"] == "missing"],
            "draft_days": [d for d in trading if st[d]["status"] == "draft"]}


def _prices(con, month: str):
    cache = {}

    def p(code):
        if code not in cache:
            from .prices import price_for
            pr = price_for(con, code, month)
            cache[code] = pr[0] if pr else None
        return cache[code]
    return p


def _price_ref(con, month: str):
    """PR-B4a (1.6): price_ref(code) = the price used to RANK items (ABC class): the real price, else the last known price, else the
    median the benchmark branches paid, else 0. Values shown on a screen keep the real price (_prices)."""
    from .prices import PriceBook
    return PriceBook(con, month).ref


def _names(con) -> dict:
    return {c: (n, u) for c, n, u in con.execute("SELECT code, name_th, base_unit FROM bc_ingredient")}


def _branch_name(con, branch_id: int):
    r = con.execute("SELECT name FROM branches WHERE branch_id=?", (branch_id,)).fetchone()
    return r[0] if r else None


# ── R1 · system stock vs the physical count ──────────────────────────────────
def reconcile(con, branch_id: int, end_count_id) -> dict:
    """One period (begin count → end count) of a branch:  system = begin + received + claims − issued (used + waste),
    count = the end count,  diff = count − system.  The class is the item's share of the issued ฿ and sets the
    threshold (variance.THRESHOLD); an item missing from either count is 'not_counted'."""
    from .forecast import _abc
    pair = VA._pair_for_end(con, branch_id, end_count_id)
    if pair is None:
        raise ValueError("ไม่พบงวดนี้ของสาขา")
    b, e = pair
    d0, d1 = date.fromisoformat(b["date"]), date.fromisoformat(e["date"])
    month = VA._price_month(b["date"], e["date"])
    mv = VA._movements(con, branch_id, b, e)
    begin, end, received, claims = mv["begin"], mv["end"], mv["received"], mv["claims"]
    iss = issued(con, branch_id, d0, d1)
    reasons = {c: (r, n, w) for c, r, n, w in con.execute(
        "SELECT ingredient_code, reason_code, note, who FROM bc_variance_reason WHERE end_count_id=?", (e["count_id"],))}
    # batch 4b (3.3): a difference is explained only when the data can say something - the period has receipts and every
    # trading day of it has a confirmed slip - and when it is at least bc_reason_min_baht baht; else it is labelled
    from .close_settings import reason_min_baht
    min_baht = reason_min_baht(con)
    slips = slip_coverage(con, branch_id, d0, d1)
    incomplete_why = "receipts" if mv["n_receipts"] == 0 else ("slips" if slips["missing_days"] or slips["draft_days"] else None)
    end_basis = VA._count_basis(con, e["count_id"])
    price = _prices(con, month)
    names = _names(con)
    pref = _price_ref(con, month)                                # PR-B4a (1.6): an unpriced item is ranked by a fallback price, never shown with one
    klass = _abc({c: (v["used"] + v["waste"]) * pref(c) for c, v in iss.items()
                  if (v["used"] + v["waste"]) > 0 and pref(c)})
    rows, excluded = [], []
    for c in sorted(set(begin) | set(end) | set(received) | set(claims) | set(iss)):
        cell = iss.get(c) or {}
        used, waste = cell.get("used", 0.0), cell.get("waste", 0.0)
        r, cl = received.get(c, 0.0), claims.get(c, 0.0)
        n, u = names.get(c) or (None, None)
        if c not in received and c not in claims and not begin.get(c) and not end.get(c) and not used and not waste:
            excluded.append({"code": c, "name": n})            # counted at zero and nothing moved
            continue
        k = klass.get(c, "C")
        pv = price(c)
        row = {"code": c, "name": n or c, "base_unit": u, "class": k, "begin": begin.get(c), "received": round(r, 3),
               "claims": round(cl, 3), "used": round(used, 3), "waste": round(waste, 3), "price_per_base": pv,
               "system": None, "count": end.get(c), "diff": None, "diff_pct": None, "diff_value": None,
               "movement": None, "movement_gap": None}
        if c in begin:
            row["system"] = round(begin[c] + r + cl - used - waste, 3)
        if c not in begin or c not in end:
            row["status"] = "not_counted"
        else:
            sysq, cnt = begin[c] + r + cl - used - waste, end[c]
            diff = cnt - sysq
            mvt = begin[c] + r + cl - cnt                      # what the two counts say left the shelves
            row.update(diff=round(diff, 3), movement=round(mvt, 3), movement_gap=round(mvt - (used + waste), 3),
                       diff_value=_round(diff * pv) if pv is not None else None)
            pct = 100.0 * diff / sysq if sysq > _EPS else None
            row["diff_pct"] = _round(pct, 1)
            if abs(diff) < _EPS:
                row["status"] = "ok"
            elif pct is not None and abs(pct) <= VA.THRESHOLD[k]:
                row["status"] = "ok"
            else:
                row["status"] = "over" if diff > 0 else "under"
        row["status_th"] = RECON_STATUS_TH[row["status"]]
        rs = reasons.get(c)
        if rs:
            row.update(reason=rs[0], reason_th=VA.REASON_TH.get(rs[0], rs[0]), reason_note=rs[1], reason_by=rs[2])
        flagged_row = row["status"] in ("over", "under")
        row["end_basis"] = end_basis.get(c) or "count"
        if row["end_basis"] == "system":                                    # 3.4: carried from the system stock, not counted
            row.update(system_basis=True, basis_th=VA.SYSTEM_TH)
        if flagged_row and incomplete_why:
            row.update(data_incomplete=True, incomplete_th=VA.INCOMPLETE_TH, incomplete_why=incomplete_why)
        big = (not min_baht) or (row["diff_value"] is not None and abs(row["diff_value"]) >= min_baht)
        row["ask_reason"] = bool(flagged_row and big and not row.get("data_incomplete") and not row.get("system_basis"))
        row["needs_reason"] = row["ask_reason"] and not rs
        rows.append(row)
    order = {"under": 0, "over": 1, "not_counted": 2, "ok": 3}
    rows.sort(key=lambda x: (order[x["status"]], -abs(x["diff_value"] or 0), x["code"]))
    VA.with_count_units(con, rows)                              # PR-M4: the branch page reads the quantities in count units

    def val(key, pred=lambda x: True):
        return round(sum(x[key] * x["price_per_base"] for x in rows
                         if x[key] is not None and x["price_per_base"] is not None and pred(x)), 2)
    counted = lambda x: x["status"] != "not_counted"            # noqa: E731
    conf = con.execute("SELECT receipts_confirmed_by, receipts_confirmed_at, accounting_posted_at, accounting_posted_by"
                       " FROM bc_variance_period WHERE end_count_id=?", (e["count_id"],)).fetchone()
    return {
        "branch": {"branch_id": branch_id, "name": _branch_name(con, branch_id)},
        "period": {"begin_date": b["date"], "end_date": e["date"], "begin_count_id": b["count_id"],
                   "end_count_id": e["count_id"], "month": month, "days": (d1 - d0).days},
        "status": {"receipts_confirmed": bool(conf), "receipts_confirmed_by": conf[0] if conf else None,
                   "receipts_confirmed_at": conf[1] if conf else None,
                   "accounting_posted_at": conf[2] if conf else None, "accounting_posted_by": conf[3] if conf else None},
        "slips": slips, "partial": bool(end_basis), "incomplete_why": incomplete_why, "reason_min_baht": min_baht,
        "rows": rows, "excluded": sorted(excluded, key=lambda x: x["code"]),
        "totals": {"lines": len(rows), "system_value": val("system", counted), "count_value": val("count", counted),
                   "diff_value": val("diff"), "over_value": val("diff", lambda x: x["status"] == "over"),
                   "under_value": val("diff", lambda x: x["status"] == "under"),
                   "flagged": sum(1 for x in rows if x["status"] in ("over", "under")),
                   "needs_reason": sum(1 for x in rows if x["needs_reason"]),
                   "incomplete": sum(1 for x in rows if x.get("data_incomplete")),
                   "system_basis": sum(1 for x in rows if x.get("system_basis")),
                   "not_counted": sum(1 for x in rows if x["status"] == "not_counted"),
                   "unpriced": sum(1 for x in rows if x["price_per_base"] is None)},
        "reasons": [{"code": c, "label": t} for c, t in VA.REASONS],
        "thresholds": VA.THRESHOLD, "status_th": RECON_STATUS_TH,
    }


# ── R2 · the daily issue vs the BoM ──────────────────────────────────────────
def _pct(issue_v: float, bom_v: float):
    return round(100.0 * (issue_v - bom_v) / bom_v, 1) if bom_v and bom_v > _EPS else None


def _streaks(series: list, threshold: float = OVER_PCT, n: int = STREAK_DAYS, below: bool = False) -> list:
    """`series` = [(day, pct or None)] in trading-day order. Runs of at least `n` consecutive entries above the
    threshold (BELOW it with `below`, PR-B4a 1.4: under-issue); a day with no comparable figure ends a run.
    -> [{days, from, to, max_pct, last_pct}] (`max_pct` is the deepest figure of the run: the largest, or with `below` the lowest)"""
    runs, cur = [], []
    for day, pct in series + [(None, None)]:
        if pct is not None and (pct < threshold if below else pct > threshold):
            cur.append((day, pct))
            continue
        if len(cur) >= n:
            runs.append({"days": len(cur), "from": cur[0][0], "to": cur[-1][0],
                         "max_pct": (min if below else max)(p for _, p in cur), "last_pct": cur[-1][1]})
        cur = []
    return runs


def _month_class(bom: dict, price) -> dict:
    """ABC class of every item by its BoM ฿ over the days given ({day: {code: qty}})."""
    from .forecast import _abc
    tot = {}
    for day in bom.values():
        for c, q in day.items():
            tot[c] = tot.get(c, 0.0) + q * (price(c) or 0.0)
    return _abc({c: v for c, v in tot.items() if v > 0})


def _slip_key(st: dict) -> str:
    return {"confirmed": "on_time", "late": "late", "draft": "draft"}.get(st["status"], "missing")


def _span(con, branch_id: int, d0: date, d1: date, month: str, today: date = None, commit: bool = True) -> dict:
    """Everything the daily report needs for the days d0..d1: the BoM, the confirmed issues, the slip status,
    the classes, the per-day rows and the streak flags. Prices and the release are those of `month`."""
    today = today or now_bkk().date()
    last = min(d1, today)
    price = _prices(con, month)
    names = _names(con)
    empty = {"days": [], "rows": [], "flags": [], "bom": {}, "iss": {}, "st": {}, "klass": {}, "price": price,
             "names": names, "flag_days": {}, "under_days": {}, "comparable": {}}
    if last < d0:
        return empty
    bom = bom_days(con, branch_id, d0, last, month, commit=commit)
    iss = issued(con, branch_id, d0, last + timedelta(days=1), by_day=True)
    st = {s["date"]: s for s in IS.status_days(con, branch_id, d0, last)}
    from .started import branch_start
    start = branch_start(con, branch_id) or "9999"    # PR-U1: a day before the branch started is not listed (nor judged); none yet = none
    days = [d for d in sorted(set(bom) | set(st))
            if ((d in bom and d < today.isoformat()) or st[d]["status"] != "missing")
            and (d >= start or st[d]["status"] != "missing")]
    t_iso = today.isoformat()
    klass = _month_class({d: bom[d] for d in days if d in bom}, _price_ref(con, month))
    a_codes = sorted(c for c, k in klass.items() if k == "A")
    rows, per_code, per_code_u = [], {}, {}                     # per_code: code -> [(day, pct)] · per_code_u: the same for the under-issue streak
    for d in days:
        b, i, s = bom.get(d, {}), iss.get(d, {}), st[d]
        conf = IS.is_confirmed(s)
        # PR-U1: a day is JUDGED (issued against the recipes) only when its slip is confirmed, it has a formula value (its sales are in)
        # and it is over - the day still trading (Bangkok) and a day with no sales yet carry no verdict
        judged = conf and d in bom and d < t_iso
        used_v = sum(v["used"] * (price(c) or 0.0) for c, v in i.items())
        waste_v = sum(v["waste"] * (price(c) or 0.0) for c, v in i.items())
        bom_v = sum(q * (price(c) or 0.0) for c, q in b.items())
        over = []
        for c in a_codes:
            iq = (i.get(c) or {}).get("used", 0.0) + (i.get(c) or {}).get("waste", 0.0)
            pct = _pct(iq, b.get(c, 0.0)) if judged else None
            if d in bom:                                        # a streak runs over TRADING days only
                per_code.setdefault(c, []).append((d, pct))
                per_code_u.setdefault(c, []).append((d, pct))
            if pct is not None and pct > OVER_PCT:
                over.append({"code": c, "name": (names.get(c) or (c,))[0], "var_pct": pct})
        wd = FC._wd(date.fromisoformat(d))
        row = {"date": d, "weekday": wd, "weekday_th": FC.WD_TH[wd], "slip_status": _slip_key(s),
               "slip_status_th": SLIP_STATUS_TH[_slip_key(s)], "slip_id": s["slip_id"], "late": int(s["late"] or 0),
               "confirmed": conf, "trading": d in bom, "bom_value": round(bom_v, 2) if d in bom else None,
               "judged": judged, "today": d == t_iso, "no_formula": d not in bom,
               "no_formula_th": "ยังไม่มีค่าตามสูตร" if d not in bom else None,
               "used_value": round(used_v, 2) if conf else None, "waste_value": round(waste_v, 2) if conf else None,
               "issued_value": round(used_v + waste_v, 2) if conf else None,
               "var_value": round(used_v + waste_v - bom_v, 2) if judged else None,
               "var_pct": _pct(used_v + waste_v, bom_v) if judged else None, "over_items": over}
        rows.append(row)
    flags, flag_days, under_days = [], {}, {}
    total_series = [(r["date"], r["var_pct"]) for r in rows if r["trading"]]
    for run in _streaks(total_series):
        flags.append(dict(run, kind="total", code=None, name="รวมทุกรายการ", direction="over"))
    for c in sorted(per_code):
        for run in _streaks(per_code[c]):
            flags.append(dict(run, kind="item", code=c, name=(names.get(c) or (c,))[0], direction="over"))
    # PR-B4a (1.4): the other direction - a class-A item issued more than UNDER_PCT under its BoM on STREAK_DAYS trading days in a row
    # (the kitchen used it and nobody wrote it on the slip, or wrote less). Items only: the total is already watched by the over flag.
    for c in sorted(per_code_u):
        for run in _streaks(per_code_u[c], threshold=-UNDER_PCT, below=True):
            flags.append(dict(run, kind="item", code=c, name=(names.get(c) or (c,))[0], direction="under"))
    for f in flags:
        into = under_days if f["direction"] == "under" else flag_days
        for r in rows:
            if f["from"] <= r["date"] <= f["to"]:
                into.setdefault(r["date"], []).append(f["code"] or "*")
    for r in rows:
        codes = flag_days.get(r["date"], [])
        r["streak"] = [("รวมทุกรายการ" if c == "*" else (names.get(c) or (c,))[0]) for c in codes]
        r["flagged"] = bool(codes)
        ucodes = under_days.get(r["date"], [])
        r["under_streak"] = [(names.get(c) or (c,))[0] for c in ucodes]
        r["under_flagged"] = bool(ucodes)
    flags.sort(key=lambda f: (-abs(f["max_pct"]), f["code"] or ""))
    return {"days": days, "rows": rows, "flags": flags, "bom": bom, "iss": iss, "st": st, "klass": klass,
            "price": price, "names": names, "flag_days": flag_days, "under_days": under_days}


def _totals(rows: list) -> dict:
    """PR-U1: the baht compare the JUDGED days only (confirmed, with a formula value, over); the head counts the days that are over and
    needed a slip - a day with sales or a day with a slip - so "ยืนยันแล้ว N/M วัน" agrees with the rows (the day still trading is not in it)."""
    conf = [r for r in rows if r.get("judged", r["confirmed"])]
    bom_v = sum(r["bom_value"] or 0.0 for r in conf)
    iss_v = sum(r["issued_value"] for r in conf)
    trading = [r for r in rows if not r.get("today") and (r["trading"] or r["slip_status"] != "missing")]
    return {"days": len(rows), "trading_days": len(trading), "confirmed_days": sum(1 for r in trading if r["confirmed"]),
            "late_days": sum(1 for r in trading if r["slip_status"] == "late"),
            "draft_days": sum(1 for r in trading if r["slip_status"] == "draft"),
            "missing_days": sum(1 for r in trading if r["slip_status"] == "missing"),
            "used_value": round(sum(r["used_value"] or 0.0 for r in conf), 2),
            "waste_value": round(sum(r["waste_value"] or 0.0 for r in conf), 2), "issued_value": round(iss_v, 2),
            "bom_value": round(bom_v, 2), "var_value": round(iss_v - bom_v, 2), "var_pct": _pct(iss_v, bom_v),
            "flagged_days": sum(1 for r in rows if r["flagged"]), "over_days": sum(1 for r in rows if r["over_items"]),
            "under_flagged_days": sum(1 for r in rows if r.get("under_flagged"))}


# ── PR-S4 · mode `stock`: the daily issue against the branch's own normal and the all-branch median ──────────
def stock_on(con) -> bool:
    """True in mode `stock` (the default): no recipe, so the daily report compares with the comparators below."""
    from .close_settings import stock_mode
    return stock_mode(con)


def _hist(con, ids: list, lo: date, hi: date) -> dict:
    """{branch_id: {days: confirmed slip days, iss: {day: {code: used + waste, base units}}, sales: {day: ex-VAT baht}}} for the
    CONFIRMED slips between lo and hi (inclusive). A confirmed slip without lines is still a slip day."""
    from . import per_sales as PS
    out = {int(b): {"days": set(), "iss": {}, "sales": {}} for b in ids}
    if not out:
        return out
    ph = ",".join("?" * len(out))
    for b, day, code, q in con.execute(
            "SELECT s.branch_id, s.slip_date, l.ingredient_code, SUM(COALESCE(l.qty_used_base, 0) + COALESCE(l.qty_waste_base, 0))"
            " FROM bc_issue_slip s LEFT JOIN bc_issue_line l USING(slip_id)"
            f" WHERE s.status='confirmed' AND s.branch_id IN ({ph}) AND s.slip_date BETWEEN ? AND ?"
            " GROUP BY s.branch_id, s.slip_date, l.ingredient_code", (*out, lo.isoformat(), hi.isoformat())):
        h = out[b]
        h["days"].add(day)
        if code is not None and (q or 0) > 0:
            h["iss"].setdefault(day, {})[code] = q
    for b, day, v in con.execute(
            f"SELECT branch_id, day, SUM(COALESCE(subtotal_ex_vat, 0)) FROM {PS.sales_table(con)}"
            f" WHERE branch_id IN ({ph}) AND day BETWEEN ? AND ? GROUP BY branch_id, day", (*out, lo.isoformat(), hi.isoformat())):
        out[b]["sales"][day] = float(v or 0.0)
    return out


def _company_hist(con, lo: date, hi: date, cache: dict) -> dict:
    """_hist of every company branch (forecast.company_branch_ids), kept in `cache` for the duration of one report call."""
    key = (lo.isoformat(), hi.isoformat())
    if key not in cache:
        cache[key] = _hist(con, FC.company_branch_ids(con), lo, hi)
    return cache[key]


def normal_of(h: dict, day: date):
    """The branch's own normal for `day`: {code: base qty} = the mean issued quantity of the item on the same weekday in the NORMAL_WEEKS
    weeks before (only days with a confirmed slip; a day without the item counts as 0) x the sales of `day` / the mean sales of those
    days. Fewer than NORMAL_MIN_DAYS such days -> None; no sales of `day` (or none on the reference days) -> the mean as it is."""
    ref = [r for r in ((day - timedelta(days=7 * k)).isoformat() for k in range(1, NORMAL_WEEKS + 1)) if r in h["days"]]
    if len(ref) < NORMAL_MIN_DAYS:
        return None
    tot = {}
    for r in ref:
        for c, q in (h["iss"].get(r) or {}).items():
            tot[c] = tot.get(c, 0.0) + q
    scale = 1.0
    sd = h["sales"].get(day.isoformat(), 0.0)
    mean_sales = sum(h["sales"].get(r, 0.0) for r in ref) / len(ref)
    if sd > 0 and mean_sales > 0:
        scale = sd / mean_sales
    return {c: v / len(ref) * scale for c, v in tot.items() if v > 0}


def median_of(others: list, day: date, sales_day):
    """The all-branch median for `day`: {code: base qty} = the median, over the OTHER company branches whose demand source is the slips
    (at least forecast.SLIP_MIN_DAYS confirmed days in the MEDIAN_WINDOW_DAYS before), of (issued per PER_SALES baht of the sales of those
    slip days) x the sales of `day` / PER_SALES. `others` = [hist of a branch]. Fewer than MEDIAN_MIN_BRANCHES such branches, or no sales
    of `day` -> None."""
    import statistics
    if not sales_day or sales_day <= 0:
        return None
    window = [(day - timedelta(days=k)).isoformat() for k in range(1, MEDIAN_WINDOW_DAYS + 1)]
    rates = []
    for h in others:
        sd = [x for x in window if x in h["days"]]
        if len(sd) < FC.SLIP_MIN_DAYS:
            continue
        tot_sales = sum(h["sales"].get(x, 0.0) for x in sd)
        if tot_sales <= 0:
            continue
        agg = {}
        for x in sd:
            for c, q in (h["iss"].get(x) or {}).items():
                agg[c] = agg.get(c, 0.0) + q
        rates.append({c: v / tot_sales * PER_SALES for c, v in agg.items()})
    if len(rates) < MEDIAN_MIN_BRANCHES:
        return None
    codes = set().union(*rates)
    out = {}
    for c in codes:
        m = statistics.median(r.get(c, 0.0) for r in rates)
        if m > 0:
            out[c] = m * sales_day / PER_SALES
    return out


def _month_class_issued(iss_q: dict, price) -> dict:
    """ABC class of every item by its ISSUED ฿ over the days given ({day: {code: qty}}) - _month_class with the slips instead of the BoM."""
    return _month_class(iss_q, price)


def _scores(*pcts, below=False):
    v = [p for p in pcts if p is not None]
    return None if not v else (min(v) if below else max(v))


def _basis(trig: set):
    return None if not trig else ("both" if len(trig) > 1 else next(iter(trig)))


def signal_th_stock(signal, basis) -> str:
    if not signal or not basis:
        return None
    b = BASIS_TH[basis]
    return {"over": f"เบิกเกิน{b}", "streak": f"เบิกเกิน{b}ต่อเนื่อง", "under_streak": f"เบิกต่ำกว่า{b}ต่อเนื่อง"}[signal]


def _span_stock(con, branch_id: int, d0: date, d1: date, month: str, today: date = None, commit: bool = True,
                cache: dict = None) -> dict:
    """_span for mode `stock`: the same shape, with `normal` / `median` ({day: {code: qty} or None}) where the BoM was. The BoM day cache
    is not touched. `cache` (optional) shares the other branches' slips between the calls of one report (anomalies)."""
    today = today or now_bkk().date()
    last = min(d1, today)
    price = _prices(con, month)
    names = _names(con)
    empty = {"days": [], "rows": [], "flags": [], "normal": {}, "median": {}, "iss": {}, "st": {}, "klass": {}, "price": price,
             "names": names, "flag_days": {}, "under_days": {}, "stock": True}
    if last < d0:
        return empty
    cache = {} if cache is None else cache
    lo = d0 - timedelta(days=max(7 * NORMAL_WEEKS, MEDIAN_WINDOW_DAYS))
    company = _company_hist(con, lo, last, cache)
    own = company.get(branch_id) or _hist(con, [branch_id], lo, last)[branch_id]
    others = [h for b, h in company.items() if b != branch_id]
    trading = set(_trading_days(con, branch_id, d0, last))
    iss = issued(con, branch_id, d0, last + timedelta(days=1), by_day=True)
    st = {s["date"]: s for s in IS.status_days(con, branch_id, d0, last)}
    from .started import branch_start
    start = branch_start(con, branch_id) or "9999"
    days = [d for d in sorted(trading | set(st))
            if ((d in trading and d < today.isoformat()) or st[d]["status"] != "missing")
            and (d >= start or st[d]["status"] != "missing")]
    t_iso = today.isoformat()
    normal, median = {}, {}
    for d in days:
        dd = date.fromisoformat(d)
        normal[d] = normal_of(own, dd)
        median[d] = median_of(others, dd, own["sales"].get(d))
    iss_q = {d: {c: v["used"] + v["waste"] for c, v in iss.get(d, {}).items()} for d in days}
    klass = _month_class_issued(iss_q, _price_ref(con, month))
    a_codes = sorted(c for c, k in klass.items() if k == "A")
    rows, per_over, per_under, trig_o, trig_u = [], {}, {}, {}, {}      # per_*: code -> [(day, score)] · trig_*: (code or "*", day) -> {basis}
    for d in days:
        i, s = iss.get(d, {}), st[d]
        conf = IS.is_confirmed(s)
        nrm, med = normal.get(d), median.get(d)
        have = nrm is not None or med is not None
        judged = conf and d in trading and d < t_iso and have
        used_v = sum(v["used"] * (price(c) or 0.0) for c, v in i.items())
        waste_v = sum(v["waste"] * (price(c) or 0.0) for c, v in i.items())
        iss_v = used_v + waste_v
        nrm_v = round(sum(q * (price(c) or 0.0) for c, q in nrm.items()), 2) if nrm is not None else None
        med_v = round(sum(q * (price(c) or 0.0) for c, q in med.items()), 2) if med is not None else None
        pn = _pct(iss_v, nrm_v) if judged and nrm_v is not None else None
        pm = _pct(iss_v, med_v) if judged and med_v is not None else None
        over = []
        for c in a_codes:
            iq = (i.get(c) or {}).get("used", 0.0) + (i.get(c) or {}).get("waste", 0.0)
            cn = _pct(iq, nrm.get(c, 0.0)) if judged and nrm is not None else None
            cm = _pct(iq, med.get(c, 0.0)) if judged and med is not None else None
            if d in trading:
                per_over.setdefault(c, []).append((d, _scores(cn, cm)))
                per_under.setdefault(c, []).append((d, _scores(cn, cm, below=True)))
                trig_o[(c, d)] = {k for k, p in (("normal", cn), ("median", cm)) if p is not None and p > OVER_PCT}
                trig_u[(c, d)] = {k for k, p in (("normal", cn), ("median", cm)) if p is not None and p < -UNDER_PCT_STOCK}
            if trig_o.get((c, d)):
                bs = _basis(trig_o[(c, d)])
                over.append({"code": c, "name": (names.get(c) or (c,))[0], "var_pct": _scores(cn, cm), "basis": bs,
                             "basis_th": BASIS_TH[bs]})
        if d in trading:
            trig_o[("*", d)] = {k for k, p in (("normal", pn), ("median", pm)) if p is not None and p > OVER_PCT}
        wd = FC._wd(date.fromisoformat(d))
        if d not in trading:
            no_th = "ยังไม่มียอดขายของวันนั้น"
        else:
            no_th = None if have else "ยังไม่มีค่าปกติให้เทียบ"
        rows.append({"date": d, "weekday": wd, "weekday_th": FC.WD_TH[wd], "slip_status": _slip_key(s),
                     "slip_status_th": SLIP_STATUS_TH[_slip_key(s)], "slip_id": s["slip_id"], "late": int(s["late"] or 0),
                     "confirmed": conf, "trading": d in trading, "normal_value": nrm_v if d in trading else None,
                     "median_value": med_v if d in trading else None, "judged": judged, "today": d == t_iso,
                     "no_formula": d not in trading or not have, "no_formula_th": no_th,
                     "used_value": round(used_v, 2) if conf else None, "waste_value": round(waste_v, 2) if conf else None,
                     "issued_value": round(iss_v, 2) if conf else None,
                     "var_value": round(iss_v - nrm_v, 2) if judged and nrm_v is not None else None, "var_pct": pn,
                     "median_var_value": round(iss_v - med_v, 2) if judged and med_v is not None else None, "median_var_pct": pm,
                     "over_items": over})
    flags, flag_days, under_days = [], {}, {}

    def run_flag(run, kind, code, name, direction, trig):
        days_in = [d for d in days if run["from"] <= d <= run["to"]]
        u = set().union(*(trig.get((code or "*", d), set()) for d in days_in)) if days_in else set()
        bs = _basis(u) or "normal"
        flags.append(dict(run, kind=kind, code=code, name=name, direction=direction, basis=bs, basis_th=BASIS_TH[bs]))

    total_over = [(r["date"], _scores(r["var_pct"], r["median_var_pct"])) for r in rows if r["trading"]]
    for run in _streaks(total_over):
        run_flag(run, "total", None, "รวมทุกรายการ", "over", trig_o)
    for c in sorted(per_over):
        for run in _streaks(per_over[c]):
            run_flag(run, "item", c, (names.get(c) or (c,))[0], "over", trig_o)
    for c in sorted(per_under):
        for run in _streaks(per_under[c], threshold=-UNDER_PCT_STOCK, below=True):
            run_flag(run, "item", c, (names.get(c) or (c,))[0], "under", trig_u)
    for f in flags:
        into = under_days if f["direction"] == "under" else flag_days
        for r in rows:
            if f["from"] <= r["date"] <= f["to"]:
                into.setdefault(r["date"], []).append(f["code"] or "*")
    for r in rows:
        codes = flag_days.get(r["date"], [])
        r["streak"] = [("รวมทุกรายการ" if c == "*" else (names.get(c) or (c,))[0]) for c in codes]
        r["flagged"] = bool(codes)
        ucodes = under_days.get(r["date"], [])
        r["under_streak"] = [(names.get(c) or (c,))[0] for c in ucodes]
        r["under_flagged"] = bool(ucodes)
    flags.sort(key=lambda f: (-abs(f["max_pct"]), f["code"] or ""))
    return {"days": days, "rows": rows, "flags": flags, "normal": normal, "median": median, "iss": iss, "st": st, "klass": klass,
            "price": price, "names": names, "flag_days": flag_days, "under_days": under_days, "stock": True,
            "trig_o": trig_o, "trig_u": trig_u}


def _totals_stock(rows: list) -> dict:
    """_totals for mode `stock`: the baht compare the JUDGED days; each comparator is summed over the days that have it."""
    conf = [r for r in rows if r.get("judged")]
    trading = [r for r in rows if not r.get("today") and (r["trading"] or r["slip_status"] != "missing")]
    nr = [r for r in conf if r["normal_value"] is not None]
    md = [r for r in conf if r["median_value"] is not None]
    n_iss, n_val = sum(r["issued_value"] for r in nr), sum(r["normal_value"] for r in nr)
    m_iss, m_val = sum(r["issued_value"] for r in md), sum(r["median_value"] for r in md)
    return {"days": len(rows), "trading_days": len(trading), "confirmed_days": sum(1 for r in trading if r["confirmed"]),
            "late_days": sum(1 for r in trading if r["slip_status"] == "late"),
            "draft_days": sum(1 for r in trading if r["slip_status"] == "draft"),
            "missing_days": sum(1 for r in trading if r["slip_status"] == "missing"),
            "used_value": round(sum(r["used_value"] or 0.0 for r in conf), 2),
            "waste_value": round(sum(r["waste_value"] or 0.0 for r in conf), 2),
            "issued_value": round(sum(r["issued_value"] for r in conf), 2),
            "normal_value": round(n_val, 2), "var_value": round(n_iss - n_val, 2), "var_pct": _pct(n_iss, n_val),
            "median_value": round(m_val, 2), "median_var_value": round(m_iss - m_val, 2), "median_var_pct": _pct(m_iss, m_val),
            "flagged_days": sum(1 for r in rows if r["flagged"]), "over_days": sum(1 for r in rows if r["over_items"]),
            "under_flagged_days": sum(1 for r in rows if r.get("under_flagged"))}


def _day_items_stock(s: dict, day: str) -> list:
    """_day_items for mode `stock`: issued against the normal of the branch and the all-branch median."""
    nrm, med = s["normal"].get(day), s["median"].get(day)
    i, st = s["iss"].get(day, {}), s["st"].get(day)
    conf = bool(st) and IS.is_confirmed(st)
    row = next((r for r in s["rows"] if r["date"] == day), None)
    judged = conf and bool(row and row.get("judged"))
    p, names, klass = s["price"], s["names"], s["klass"]
    flagged, under_flagged = set(s["flag_days"].get(day, [])), set(s["under_days"].get(day, []))
    out = []
    for c in sorted(set(nrm or ()) | set(med or ()) | set(i)):
        cell = i.get(c) or {}
        used, waste = cell.get("used", 0.0), cell.get("waste", 0.0)
        iq = used + waste
        nq = (nrm or {}).get(c, 0.0) if nrm is not None else None
        mq = (med or {}).get(c, 0.0) if med is not None else None
        pv = p(c)
        n, u = names.get(c) or (c, None)
        k = klass.get(c, "C")
        pn = _pct(iq, nq) if judged and nq is not None else None
        pm = _pct(iq, mq) if judged and mq is not None else None
        over_t = {b for b, x in (("normal", pn), ("median", pm)) if x is not None and x > OVER_PCT}
        under_t = {b for b, x in (("normal", pn), ("median", pm)) if x is not None and x < -UNDER_PCT_STOCK}
        signal = basis = None
        if judged and c in flagged:
            signal, basis = "streak", _basis(s["trig_o"].get((c, day)) or over_t)
        elif judged and c in under_flagged:
            signal, basis = "under_streak", _basis(s["trig_u"].get((c, day)) or under_t)
        elif judged and k == "A" and over_t:
            signal, basis = "over", _basis(over_t)
        out.append({"date": day, "code": c, "name": n, "base_unit": u, "class": k,
                    "used": round(used, 3) if conf else None, "waste": round(waste, 3) if conf else None,
                    "issued": round(iq, 3) if conf else None,
                    "normal": round(nq, 3) if nq is not None else None, "median": round(mq, 3) if mq is not None else None,
                    "var_qty": round(iq - nq, 3) if judged and nq is not None else None, "var_pct": pn, "median_pct": pm,
                    "price_per_base": pv,
                    "var_value": _round((iq - nq) * pv) if judged and nq is not None and pv is not None else None,
                    "signal": signal, "basis": basis, "signal_th": signal_th_stock(signal, basis)})
    rank = {"streak": 0, "under_streak": 0, "over": 1, None: 2}
    out.sort(key=lambda r: (rank[r["signal"]], -abs(r["var_value"] or 0), r["code"]))
    return out


def daily(con, branch_id: int, month: str, today: date = None, commit: bool = True) -> dict:
    """R2: per trading day of `month`  issued (used + waste, ฿)  vs  the BoM of what was sold (฿); the slip
    status; the class-A items issued more than OVER_PCT over the BoM; and the flags (a class-A item — or the
    total — more than OVER_PCT over on STREAK_DAYS consecutive trading days). A day whose slip is not
    confirmed has no comparison (nothing was issued yet), and breaks a streak. Today is listed only once it
    has a slip."""
    first, last = _month_bounds(month)
    if stock_on(con):                                           # PR-S4: no recipe - the branch's normal and the all-branch median
        s = _span_stock(con, branch_id, first, last, month, today, commit)
        return {"branch": {"branch_id": branch_id, "name": _branch_name(con, branch_id)}, "month": month, "stock": True,
                "price_month": month, "rows": s["rows"], "flags": s["flags"], "totals": _totals_stock(s["rows"]),
                "thresholds": {"over_pct": OVER_PCT, "streak_days": STREAK_DAYS, "under_pct": UNDER_PCT_STOCK},
                "slip_status_th": SLIP_STATUS_TH}
    s = _span(con, branch_id, first, last, month, today, commit)
    return {"branch": {"branch_id": branch_id, "name": _branch_name(con, branch_id)}, "month": month,
            "price_month": month, "rows": s["rows"], "flags": s["flags"], "totals": _totals(s["rows"]),
            "thresholds": {"over_pct": OVER_PCT, "streak_days": STREAK_DAYS, "under_pct": UNDER_PCT},
            "slip_status_th": SLIP_STATUS_TH}


def _day_items(s: dict, day: str) -> list:
    """The per-item comparison of one day inside a span (see _span)."""
    b, i, st = s["bom"].get(day, {}), s["iss"].get(day, {}), s["st"].get(day)
    conf = bool(st) and IS.is_confirmed(st)
    row = next((r for r in s["rows"] if r["date"] == day), None)
    judged = conf and bool(row and row.get("judged"))         # PR-U1: no verdict for today or for a day with no formula value
    p, names, klass = s["price"], s["names"], s["klass"]
    flagged = set(s["flag_days"].get(day, []))
    under_flagged = set(s["under_days"].get(day, []))
    out = []
    for c in sorted(set(b) | set(i)):
        cell = i.get(c) or {}
        used, waste = cell.get("used", 0.0), cell.get("waste", 0.0)
        bq = b.get(c, 0.0)
        pv = p(c)
        n, u = names.get(c) or (c, None)
        k = klass.get(c, "C")
        pct = _pct(used + waste, bq) if judged else None
        signal = None
        if judged and c in flagged:
            signal = "streak"
        elif judged and c in under_flagged:
            signal = "under_streak"
        elif judged and k == "A" and pct is not None and pct > OVER_PCT:
            signal = "over"
        out.append({"date": day, "code": c, "name": n, "base_unit": u, "class": k,
                    "used": round(used, 3) if conf else None, "waste": round(waste, 3) if conf else None,
                    "issued": round(used + waste, 3) if conf else None, "bom": round(bq, 3),
                    "var_qty": round(used + waste - bq, 3) if judged else None, "var_pct": pct, "price_per_base": pv,
                    "var_value": _round((used + waste - bq) * pv) if judged and pv is not None else None,
                    "signal": signal, "signal_th": SIGNAL_TH.get(signal)})
    rank = {"streak": 0, "under_streak": 0, "over": 1, None: 2}
    out.sort(key=lambda r: (rank[r["signal"]], -abs(r["var_value"] or 0), r["code"]))
    return out


def daily_day(con, branch_id: int, day, today: date = None, commit: bool = True) -> dict:
    """R2 drill-down: the items of one day, issued vs BoM, with the waste reasons of the slip."""
    d = _d(day)
    month = f"{d.year:04d}-{d.month:02d}"
    first, last = _month_bounds(month)
    stock = stock_on(con)
    s = (_span_stock if stock else _span)(con, branch_id, first, last, month, today, commit)
    iso = d.isoformat()
    st = s["st"].get(iso) or IS.status_of(con, branch_id, iso)
    items = (_day_items_stock if stock else _day_items)(s, iso)
    why = {}
    if st.get("slip_id"):
        why = {c: (r, n) for c, r, n in con.execute(
            "SELECT ingredient_code, waste_reason, note FROM bc_issue_line WHERE slip_id=?", (st["slip_id"],))}
    for it in items:
        r = why.get(it["code"])
        it["waste_reason"] = r[0] if r else None
        it["waste_reason_th"] = IS.WASTE_REASON_TH.get(r[0]) if r and r[0] else None
        it["note"] = r[1] if r else None
    VA.with_count_units(con, items)                             # PR-M4: count units for the branch page
    row = next((r for r in s["rows"] if r["date"] == iso), None)
    key = _slip_key(st)
    out = {"branch": {"branch_id": branch_id, "name": _branch_name(con, branch_id)}, "date": iso, "month": month,
           "weekday_th": FC.WD_TH[FC._wd(d)], "slip": dict(st, key=key, status_th=SLIP_STATUS_TH[key]),
           "day": row, "rows": items, "flags": [f for f in s["flags"] if f["from"] <= iso <= f["to"]]}
    if stock:
        out["stock"] = True
    return out


def daily_items(con, branch_id: int, month: str, today: date = None, commit: bool = True) -> list:
    """Every day's per-item rows of a month (the second sheet of daily.xlsx)."""
    first, last = _month_bounds(month)
    if stock_on(con):
        s = _span_stock(con, branch_id, first, last, month, today, commit)
        return [it for d in s["days"] for it in _day_items_stock(s, d)]
    s = _span(con, branch_id, first, last, month, today, commit)
    return [it for d in s["days"] for it in _day_items(s, d)]


def anomalies(con, as_of, lookback: int = 7, today: date = None, commit: bool = True, direction: str = "over") -> list:
    """The branches whose confirmed issues ran more than OVER_PCT over the BoM on STREAK_DAYS consecutive
    trading days (class-A items, or the total) inside the `lookback` days ending at `as_of`.
    PR-B4a (1.4): `direction='under'` lists the branches whose class-A items ran more than UNDER_PCT UNDER the BoM instead."""
    end = _d(as_of)
    start = end - timedelta(days=int(lookback) - 1)
    month = f"{end.year:04d}-{end.month:02d}"
    out = []
    ids = [r[0] for r in con.execute(
        "SELECT DISTINCT branch_id FROM bc_issue_slip WHERE status='confirmed' AND slip_date BETWEEN ? AND ?"
        " ORDER BY branch_id", (start.isoformat(), end.isoformat()))]
    stock = stock_on(con)
    shared = {}                                                  # PR-S4: the other branches' slips are read once for all branches
    for bid in ids:
        if stock:
            s = _span_stock(con, bid, start, end, month, today or end + timedelta(days=1), commit, cache=shared)
        else:
            s = _span(con, bid, start, end, month, today or end + timedelta(days=1), commit)
        flags =[f for f in s["flags"] if f["direction"] == direction]
        if flags:
            out.append({"branch_id": bid, "branch": _branch_name(con, bid), "flags": flags,
                        "max_pct": (min if direction == "under" else max)(f["max_pct"] for f in flags),
                        "items": [f["name"] for f in flags]})
    out.sort(key=lambda x: (-abs(x["max_pct"]), x["branch_id"]))
    return out


def blank_class_a(con, branch_id: int, day, filled, today: date = None, top: int = BLANK_TOP, commit: bool = True) -> dict:
    """PR-B4a (1.4): the class-A items the sales of `day` say were used (BoM > 0) that have no used / waste quantity on the slip.

    `filled` = the codes the slip has a quantity for (the confirm dialog sends what is on the screen). Class A is the class of the
    month so far (the daily report's own: the items that make 80 % of the BoM ฿). The list is cut to the `top` most valuable (BoM
    ฿); `total` is how many there are. Nothing is written except the BoM-day cache bom_days keeps anyway.
    -> {date, items: [{code, name, bom, base_unit, value}], total, shown}"""
    d = _d(day)
    month = f"{d.year:04d}-{d.month:02d}"
    first, _last = _month_bounds(month)
    out = {"date": d.isoformat(), "items": [], "total": 0, "shown": 0}
    if d < first:
        return out
    if stock_on(con):                                            # PR-S4: the item is "expected" when the branch's normal for the day has it
        lo = d - timedelta(days=7 * NORMAL_WEEKS)
        own = _hist(con, [branch_id], lo, d)[branch_id]
        nrm = normal_of(own, d)
        if not nrm:
            return out
        month_iss = issued(con, branch_id, first, d, by_day=True)           # confirmed slips of the month before `d`
        klass = _month_class_issued({day: {c: v["used"] + v["waste"] for c, v in items.items()}
                                     for day, items in month_iss.items()}, _price_ref(con, month))
        price, names, have = _prices(con, month), _names(con), set(filled or ())
        rows = []
        for c, q in nrm.items():
            if q > _EPS and klass.get(c) == "A" and c not in have:
                n, u = names.get(c) or (c, None)
                rows.append({"code": c, "name": n, "normal": round(q, 3), "base_unit": u, "value": _round(q * (price(c) or 0.0))})
        rows.sort(key=lambda r: (-(r["value"] or 0), r["code"]))
        out.update(items=rows[:max(int(top), 0)], total=len(rows), shown=min(len(rows), max(int(top), 0)), stock=True)
        return out
    bom = bom_days(con, branch_id, first, d, month, commit=commit)
    today_bom = bom.get(d.isoformat()) or {}
    if not today_bom:
        return out
    price = _prices(con, month)
    klass = _month_class(bom, _price_ref(con, month))
    names = _names(con)
    have = set(filled or ())
    rows = []
    for c, q in today_bom.items():
        if q and q > _EPS and klass.get(c) == "A" and c not in have:
            n, u = names.get(c) or (c, None)
            rows.append({"code": c, "name": n, "bom": round(q, 3), "base_unit": u, "value": _round(q * (price(c) or 0.0))})
    rows.sort(key=lambda r: (-(r["value"] or 0), r["code"]))
    out.update(items=rows[:max(int(top), 0)], total=len(rows), shown=min(len(rows), max(int(top), 0)))
    return out


# ── R3 · the lateness log ────────────────────────────────────────────────────
def _bkk(ts):
    """A stored UTC ISO timestamp as Bangkok time -> (date, 'YYYY-MM-DD HH:MM', 'HH:MM'); (None, None, None) if empty."""
    if not ts:
        return None, None, None
    try:
        t = datetime.fromisoformat(str(ts))
    except ValueError:
        return None, None, None
    t = (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(BKK)
    return t.date(), t.strftime("%Y-%m-%d %H:%M"), t.strftime("%H:%M")


def _started(con, branch_id: int):
    """The first day the branch is held to the slip: the one branch start date (started.branch_start, PR-U1)."""
    from .started import branch_start
    return branch_start(con, branch_id)


def log(con, d0, d1, branch_id: int = None, today: date = None) -> dict:
    """R3: for every branch and every day from d0 to d1 (clipped at today) on which it traded or has a slip —
    the slip's state (on_time / late / draft / missing, or closed = no sales and no slip but something else
    happened), when and by whom it was confirmed, how often it was confirmed again or reopened (with the
    reasons), the purchase orders of that cutoff day sent late, and the claims raised. Days before the branch
    started (its first month-end count or slip) are not held against it, and today without a slip is left
    open. `summary` is one line per branch:  on_time_pct = on_time ÷ confirmed slips (the pilot's
    'confirmed within the day'),  coverage_pct = confirmed slips ÷ trading days (the pilot's coverage)."""
    a, b = _d(d0), _d(d1)
    today = today or now_bkk().date()
    last = min(b, today)
    if branch_id is not None:
        ids = [int(branch_id)]
    else:
        ids = sorted({r[0] for r in con.execute("SELECT branch_id FROM bc_stock_count UNION SELECT branch_id FROM bc_order"
                                                 " UNION SELECT branch_id FROM bc_issue_slip")})
    names = dict(con.execute("SELECT branch_id, name FROM branches"))
    lo, hi = a.isoformat(), b.isoformat()
    reopens = {}
    q = ("SELECT branch_id, slip_date, at, who, note FROM bc_issue_log WHERE action='reopen' AND slip_date BETWEEN ? AND ?"
         + (" AND branch_id=?" if branch_id is not None else "") + " ORDER BY log_id")
    for bid, day, at, who, note in con.execute(q, (lo, hi) + ((int(branch_id),) if branch_id is not None else ())):
        reopens.setdefault((bid, day), []).append({"at": _bkk(at)[1], "who": who, "note": note})
    late_orders = {}
    for bid, day, n in con.execute(
            "SELECT branch_id, cycle_date, COUNT(*) FROM bc_order WHERE COALESCE(late,0)=1 AND status<>'cancelled'"
            " AND cycle_date BETWEEN ? AND ? GROUP BY branch_id, cycle_date", (lo, hi)):
        late_orders[(bid, day)] = n
    claims = {}
    for bid, ts in con.execute("SELECT branch_id, created_at FROM bc_claim WHERE status<>'cancelled'"):
        day = _bkk(ts)[0]
        if day and a <= day <= b:
            claims[(bid, day.isoformat())] = claims.get((bid, day.isoformat()), 0) + 1
    rows, summary = [], []
    for bid in ids:
        start = _started(con, bid)
        trading = set(_trading_days(con, bid, a, b)) if last >= a else set()
        st = {s["date"]: s for s in IS.status_days(con, bid, a, last)} if last >= a else {}
        confirms = {day: cc for day, cc in con.execute(
            "SELECT slip_date, confirm_count FROM bc_issue_slip WHERE branch_id=? AND slip_date BETWEEN ? AND ?",
            (bid, lo, hi))}
        cnt = {"trading_days": 0, "on_time": 0, "late": 0, "draft": 0, "missing": 0, "reopens": 0, "reconfirms": 0,
               "late_orders": 0, "claims": 0}
        had = False
        days = set(st) | {k[1] for k in late_orders if k[0] == bid} | {k[1] for k in claims if k[0] == bid}
        for day in sorted(days):
            s = st.get(day) or IS.status_of(con, bid, day)
            have_slip = s["status"] != "missing"
            is_trading = day in trading
            ro = reopens.get((bid, day), [])
            lo_n, cl_n = late_orders.get((bid, day), 0), claims.get((bid, day), 0)
            if start and day < start and not have_slip:
                continue                                       # before the branch began: not held against it
            if not have_slip and not is_trading and not (ro or lo_n or cl_n):
                continue
            if not have_slip and is_trading and day >= today.isoformat():
                continue                                       # today is still open
            key = _slip_key(s) if (have_slip or is_trading) else "closed"
            ct = confirms.get(day, 0) if have_slip else 0
            conf_at, conf_hm = _bkk(s["confirmed_at"])[1:] if IS.is_confirmed(s) else (None, None)
            row = {"date": day, "weekday_th": FC.WD_TH[FC._wd(date.fromisoformat(day))], "branch_id": bid,
                   "branch": names.get(bid), "trading": is_trading, "status": key, "status_th": SLIP_STATUS_TH[key],
                   "slip_id": s["slip_id"], "lines": s["lines"], "late": int(s["late"] or 0), "confirmed_at": conf_at,
                   "confirmed_time": conf_hm, "confirmed_by": s["confirmed_by"] if conf_at else None,
                   "reconfirms": max(int(ct or 0) - 1, 0), "reopens": len(ro), "reopen_notes": ro,
                   "late_orders": lo_n, "claims": cl_n, "status_note": SLIP_NOTE_TH[key],
                   "note": "; ".join(f"{x['at']} {x['who'] or '-'}: {x['note']}" for x in ro) or None}
            rows.append(row)
            had = True
            cnt["reopens"] += row["reopens"]
            cnt["reconfirms"] += row["reconfirms"]
            cnt["late_orders"] += lo_n
            cnt["claims"] += cl_n
            # PR-U1: a day with a slip is a day the branch worked even when its sales are not in yet (the POS arrives a day late), so the
            # summary's วันขาย / ยืนยันตรงวัน agree with the day rows above it
            if (is_trading or have_slip) and key in ("on_time", "late", "draft", "missing"):
                cnt["trading_days"] += 1
                cnt[key] += 1
        if had or branch_id is not None:
            ok = cnt["on_time"] + cnt["late"]
            summary.append({"branch_id": bid, "branch": names.get(bid), **cnt,
                            "on_time_pct": round(100.0 * cnt["on_time"] / ok, 1) if ok else None,
                            "coverage_pct": round(100.0 * ok / cnt["trading_days"], 1) if cnt["trading_days"] else None})
    rows.sort(key=lambda r: (r["date"], r["branch"] or ""), reverse=True)
    summary.sort(key=lambda r: r["branch"] or "")
    tot = {k: sum(s[k] for s in summary) for k in ("trading_days", "on_time", "late", "draft", "missing", "reopens",
                                                   "reconfirms", "late_orders", "claims")}
    return {"from": lo, "to": hi, "branch_id": branch_id, "rows": rows, "summary": summary, "totals": tot,
            "status_th": SLIP_STATUS_TH}


def log_monthly(con, month: str, branch_id: int = None, today: date = None) -> dict:
    first, last = _month_bounds(month)
    out = log(con, first, last, branch_id, today)
    out["month"] = month
    return out


# ── R4 · the confirmed slips as lines, for Accounting's program ──────────────
def slip_rows(con, month: str = None, branch_id: int = None, slip_id: int = None, d0=None, d1=None) -> list:
    """One row per line of every CONFIRMED slip — of a month, of a date range, of one branch, or of one slip —
    in base units, priced at the purchase price of the slip's own month. Sorted by date, branch, code."""
    from .prices import price_for
    sql = ("SELECT s.slip_id, s.slip_date, s.branch_id, b.name, s.confirmed_at, s.confirmed_by, s.late, l.ingredient_code,"
           " l.qty_used_base, l.qty_waste_base, l.waste_reason, l.note FROM bc_issue_line l"
           " JOIN bc_issue_slip s USING(slip_id) LEFT JOIN branches b ON b.branch_id=s.branch_id"
           " WHERE s.status='confirmed'")
    args = []
    if slip_id is not None:
        sql += " AND s.slip_id=?"
        args.append(int(slip_id))
    else:
        if month:
            d0, d1 = _month_bounds(month)
        if d0 is not None:
            sql += " AND s.slip_date>=?"
            args.append(_d(d0).isoformat())
        if d1 is not None:
            sql += " AND s.slip_date<=?"
            args.append(_d(d1).isoformat())
    if branch_id is not None:
        sql += " AND s.branch_id=?"
        args.append(int(branch_id))
    names = _names(con)
    cache, out = {}, []
    for sid, day, bid, bname, conf, by, late, code, ub, wb, rs, nt in con.execute(
            sql + " ORDER BY s.slip_date, b.name, s.slip_id, l.ingredient_code", args):
        m = day[:7]
        if (code, m) not in cache:
            pr = price_for(con, code, m)
            cache[(code, m)] = pr[0] if pr else None
        p = cache[(code, m)]
        used, waste = ub or 0.0, wb or 0.0
        n, u = names.get(code) or (code, None)
        out.append({"date": day, "slip_id": sid, "branch_id": bid, "branch": bname, "code": code, "name": n, "base_unit": u,
                    "qty_used": round(used, 3), "qty_waste": round(waste, 3), "waste_reason": rs,
                    "waste_reason_th": IS.WASTE_REASON_TH.get(rs) if rs else None, "note": nt, "unit_price": p,
                    "used_value": _round(used * p) if p is not None else None,
                    "waste_value": _round(waste * p) if p is not None else None,
                    "confirmed_at": _bkk(conf)[1], "confirmed_by": by, "late": int(late or 0), "month": m})
    return out
