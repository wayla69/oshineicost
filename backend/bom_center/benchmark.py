# -*- coding: utf-8 -*-
"""PR-X — the benchmark ("ค่ากลาง") from the company-owned branches.

The owner: the quantity that should really be ordered follows the branches the company runs itself (อุบลราชธานี, อุดรธานี,
โคราช1, สุรินทร์); the franchise branches may also buy outside. So every per-฿100,000 figure can be read against ONE yardstick:

  * the branches that count are the setting `bc_benchmark_branches` (JSON list of branch ids; absent = the four names above,
    the active ones that exist);
  * the benchmark value of a figure is the MEDIAN of those branches that have the figure (at least MIN_BRANCHES, else none) —
    a median, not a mean: one odd branch does not move it;
  * `bc_stock_buffer_pct` (default 15, 0-50) is the tolerance used while the begin / end counts of the branches are not all
    there: purchases are then read as use within ± this band. A branch-month that HAS a submitted begin AND end count is read
    with the stock change instead (use = bought + begin - end) and judged by the normal variance thresholds (variance.THRESHOLD).

Nothing here changes data: it is the yardstick, never an order. Calibrating the order suggestion with it is a later decision.
"""
import json
import re
import statistics
from datetime import date, timedelta

from .master import audit

KEY_BRANCHES = "bc_benchmark_branches"
KEY_BUFFER = "bc_stock_buffer_pct"
DEFAULT_NAMES = ("อุบลราชธานี", "อุดรธานี", "โคราช1", "สุรินทร์")
BUFFER_DEFAULT = 15.0
BUFFER_MIN, BUFFER_MAX = 0.0, 50.0
MIN_BRANCHES = 2                      # a median needs at least this many branches that have the figure
LABEL = "ค่ากลางสาขาของบริษัท"
BUFFER_NOTE = "ช่วงยอมรับสำหรับสต็อกต้นงวดและปลายงวด ใช้ชั่วคราวจนกว่าจะมีผลตรวจนับปลายงวดครบทุกสาขา"
PERIOD_DAYS = (25, 40)                # a pair of consecutive counts this far apart is a month's period


def _key(name) -> str:
    return re.sub(r"\s+", "", str(name or "")).lower()


def _setting(con, key):
    try:
        r = con.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    except Exception:
        return None
    return r[0] if r and r[0] not in (None, "") else None


def _put(con, key, value) -> None:
    con.execute("CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES(?, ?)", (key, value))


def all_branches(con) -> list:
    """[{branch_id, name, branch_type, active}] of every real branch, by name."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(branches)")}
    typ = "branch_type" if "branch_type" in cols else "NULL"
    act = "COALESCE(active, 1)" if "active" in cols else "1"
    return [{"branch_id": b, "name": n, "branch_type": t, "active": bool(a)} for b, n, t, a in con.execute(
        f"SELECT branch_id, name, {typ}, {act} FROM branches WHERE name <> 'ALL' ORDER BY name")]


def default_ids(con) -> list:
    """The four named company branches that exist (active ones), in the order of DEFAULT_NAMES."""
    by_name = {_key(b["name"]): b["branch_id"] for b in all_branches(con) if b["active"]}
    return [by_name[_key(n)] for n in DEFAULT_NAMES if _key(n) in by_name]


def branch_ids(con) -> list:
    """The branches whose figures make the benchmark: the stored list (those that still exist), else the default four."""
    raw = _setting(con, KEY_BRANCHES)
    if raw is not None:
        try:
            ids = [int(x) for x in json.loads(raw)]
        except (TypeError, ValueError):
            ids = None
        if ids is not None:
            have = {b["branch_id"] for b in all_branches(con)}
            return [i for i in dict.fromkeys(ids) if i in have]
    return default_ids(con)


def buffer_pct(con) -> float:
    raw = _setting(con, KEY_BUFFER)
    try:
        v = float(raw) if raw is not None else BUFFER_DEFAULT
    except (TypeError, ValueError):
        return BUFFER_DEFAULT
    return v if BUFFER_MIN <= v <= BUFFER_MAX else BUFFER_DEFAULT


def settings(con) -> dict:
    """What the settings card shows: every branch (with its type) and whether it counts, plus the buffer."""
    ids = set(branch_ids(con))
    dflt = default_ids(con)
    return {"branches": [dict(b, selected=b["branch_id"] in ids) for b in all_branches(con)],
            "selected": sorted(ids), "default": dflt, "is_default": _setting(con, KEY_BRANCHES) is None,
            "min_branches": MIN_BRANCHES, "buffer_pct": buffer_pct(con), "buffer_default": BUFFER_DEFAULT,
            "buffer_min": BUFFER_MIN, "buffer_max": BUFFER_MAX, "buffer_note": BUFFER_NOTE, "label": LABEL,
            "names_default": list(DEFAULT_NAMES)}


def set_benchmark(con, who: str, ids=None, buffer=None) -> dict:
    """Save the branch set (ids None = leave it; an empty `reset` list is not allowed) and / or the buffer %. Audited."""
    if ids is not None:
        try:
            new = [int(x) for x in dict.fromkeys(ids)]
        except (TypeError, ValueError):
            raise ValueError("รหัสสาขาไม่ถูกต้อง")
        have = {b["branch_id"] for b in all_branches(con)}
        if any(i not in have for i in new):
            raise ValueError("ไม่พบสาขาที่เลือก")
        if len(new) < MIN_BRANCHES:
            raise ValueError(f"เลือกสาขาที่ใช้เป็นค่ากลางอย่างน้อย {MIN_BRANCHES} สาขา")
        old = branch_ids(con)
        _put(con, KEY_BRANCHES, json.dumps(sorted(new)))
        audit(con, who, "benchmark_branches", "app_settings", KEY_BRANCHES, json.dumps(old), json.dumps(sorted(new)))
    if buffer is not None:
        try:
            v = float(str(buffer).replace(",", "").strip())
        except (TypeError, ValueError):
            raise ValueError("ช่วงยอมรับต้องเป็นตัวเลข")
        if not (BUFFER_MIN <= v <= BUFFER_MAX):
            raise ValueError(f"ช่วงยอมรับต้องอยู่ระหว่าง {BUFFER_MIN:g}% ถึง {BUFFER_MAX:g}%")
        old = buffer_pct(con)
        _put(con, KEY_BUFFER, f"{v:g}")
        audit(con, who, "benchmark_buffer", "app_settings", KEY_BUFFER, f"{old:g}", f"{v:g}")
    _clear_caches()
    return settings(con)


def _clear_caches() -> None:
    try:
        from . import per_sales, recipe_check
        per_sales.clear_cache()
        recipe_check.clear_cache()
    except Exception:
        pass


# ── the median ──────────────────────────────────────────────────────────────────
def median_of(values):
    """The median of the figures, None when fewer than MIN_BRANCHES have one (None entries are not figures)."""
    v = [float(x) for x in values if x is not None]
    return statistics.median(v) if len(v) >= MIN_BRANCHES else None


def median_of_any(values):
    """The plain median of whatever figures exist (None when there are none) - for a hint, not for a yardstick."""
    v = [float(x) for x in values if x is not None]
    return statistics.median(v) if v else None


def vs_pct(value, bench):
    """How far `value` is from the benchmark, in % of the benchmark; None when either is missing or the benchmark is 0."""
    if value is None or not bench:
        return None
    return round((value - bench) / bench * 100.0, 1)


# ── purchases read with the stock change ────────────────────────────────────────
def month_period(con, branch_id: int, month: str):
    """The submitted begin + end count that bracket `month` (the pair of consecutive counts whose price month is `month` and
    that lie 25-40 days apart) -> {begin, end} with count_id and date, else None."""
    from .stock import periods
    from .variance import _price_month
    for b, e in reversed(periods(con, branch_id)):
        try:
            span = (date.fromisoformat(e["date"]) - date.fromisoformat(b["date"])).days
        except ValueError:
            continue
        if _price_month(b["date"], e["date"]) == month and PERIOD_DAYS[0] <= span <= PERIOD_DAYS[1]:
            return {"begin": b, "end": e}
    return None


def adjusted_use(con, branch_id: int, month: str, bought: dict) -> tuple:
    """(use, adjusted, period): `use` = {code: base qty} read as consumption. An item that is counted at BOTH ends of the
    branch's month is bought + begin - end (never below 0: a negative result is a miscount or a missing receipt and the plain
    purchase is kept); every other item is the purchase itself. `adjusted` = the codes read with the stock change."""
    per = month_period(con, branch_id, month)
    if not per:
        return dict(bought), set(), None
    from .variance import _count_lines
    begin, end = _count_lines(con, per["begin"]["count_id"]), _count_lines(con, per["end"]["count_id"])
    use, adjusted = {}, set()
    for code, q in bought.items():
        if code in begin and code in end and q + begin[code] - end[code] >= 0:
            use[code] = q + begin[code] - end[code]
            adjusted.add(code)
        else:
            use[code] = q
    for code in set(begin) & set(end):                 # counted, nothing bought: what left the shelf was used
        if code not in use and begin[code] - end[code] > 0:
            use[code] = begin[code] - end[code]
            adjusted.add(code)
    return use, adjusted, per


def purchase_per_100k(con, branch_id: int, d0, d1, sales_day: dict, price_of) -> dict:
    """What a branch BUYS FROM THE CENTRE per ฿100,000 of sales over [d0, d1] (฿ at price_of(code, month)) - the figure that can
    show a branch also buying elsewhere (recipe usage cannot). `sales_day` = {iso day: ฿ sales ex VAT} of the branch.
      1. the price file's per-branch rows (bc_ingredient_price_branch) of the months the window touches, each month pro-rated by
         (days with sales in the window ÷ days in the month); only months that have rows count, and the sales are those months' days;
      2. else the central orders (bc_order, status processed / received, processed units in base units, delivered inside the window),
         against the sales of the whole window.
    -> {source: 'price_file' | 'orders', value, sales, days, per_100k_value} or None when the branch has no purchase data."""
    a, b = str(d0)[:10], str(d1)[:10]
    days = {d: v for d, v in sales_day.items() if a <= d <= b and v > 0}
    if not days:
        return None
    by_month = {}
    for d in days:
        by_month.setdefault(d[:7], []).append(d)
    value = sales = 0.0
    ndays = 0
    for m, ds in by_month.items():
        rows = con.execute("SELECT ingredient_code, qty_bought FROM bc_ingredient_price_branch WHERE branch_id=? AND month=?"
                           " AND COALESCE(qty_bought, 0) > 0", (branch_id, m)).fetchall()
        if not rows:
            continue
        m0, m1 = month_days(m)
        frac = min(1.0, len(ds) / ((m1 - m0).days + 1))
        value += sum(q * (price_of(c, m) or 0.0) for c, q in rows) * frac
        sales += sum(days[d] for d in ds)
        ndays += len(ds)
    if value > 0 and sales > 0:
        return {"source": "price_file", "value": round(value, 2), "sales": round(sales, 2), "days": ndays,
                "per_100k_value": round(value / sales * 1e5, 2)}
    try:                                                    # the central orders delivered inside the window
        from .hub import delivery_of
        from .units import Converter
        conv = Converter(con)
        info = {c: (p, r) for c, p, r in con.execute("SELECT code, qty_per_recv, recv_unit FROM bc_ingredient")}
        lo = (date.fromisoformat(a) - timedelta(days=21)).isoformat()
        value = 0.0
        for rnd, cyc, dd, code, unit, q in con.execute(
                "SELECT o.round, o.cycle_date, o.delivery_date, l.ingredient_code, l.order_unit, l.processed_units FROM bc_order o"
                " JOIN bc_order_line l USING(order_id) WHERE o.branch_id=? AND o.status IN ('processed','received')"
                " AND o.cycle_date BETWEEN ? AND ? AND COALESCE(l.processed_units, 0) > 0", (branch_id, lo, b)):
            dlv = delivery_of(con, branch_id, rnd, cyc, dd)
            if not (a <= dlv <= b):
                continue
            per, recv = info.get(code, (None, None))
            if unit == "base":
                qb = float(q)
            elif per:
                qb = float(q) * float(per)
            else:
                f = conv.factor(code, recv)[0] if recv else None
                if not f:
                    continue
                qb = float(q) * f
            value += qb * (price_of(code, dlv[:7]) or 0.0)
    except Exception:
        return None
    tot = sum(days.values())
    if value > 0 and tot > 0:
        return {"source": "orders", "value": round(value, 2), "sales": round(tot, 2), "days": len(days),
                "per_100k_value": round(value / tot * 1e5, 2)}
    return None


def tolerance(klass: str, adjusted: bool, buf: float) -> float:
    """The ± band a figure is judged by: the normal variance threshold of its ABC class when the branch's counts are in,
    else the temporary buffer."""
    if adjusted:
        from .variance import THRESHOLD
        return THRESHOLD.get(klass or "C", THRESHOLD["C"])
    return buf


def month_days(month: str) -> tuple:
    y, m = int(month[:4]), int(month[5:7])
    d0 = date(y, m, 1)
    d1 = (date(y + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)
    return d0, d1
