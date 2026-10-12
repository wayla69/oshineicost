# -*- coding: utf-8 -*-
"""bom_center.prices — monthly ingredient prices (plan §4.2 M4, decision §10-1).

The price a recipe is costed with is the ACTUAL weighted-average purchase price
from accounting:  Σ value ÷ Σ quantity (in base units)  per ingredient per month.

Inputs, either one:
  * the accounting system's monthly "รายงานจ่ายสินค้าภายใน" workbook (one sheet per
    store, "Use KR1" …) — the same file /api/requisition/upload reads; its unit
    price is the accounting system's cost, its rows carry code, qty, unit, value;
    or the requisition_line rows already uploaded to the site;
  * any purchase-line export reshaped to the center's template
    (รหัส · ปริมาณ · หน่วย · มูลค่า | ราคาต่อหน่วย · สาขา · เดือน).

Flow: lines -> aggregate() (convert every quantity to base units with the
Material Converter, sum per code and per code×branch, list every line it could
not use and why) -> preview() (vs the price in force before, ±10 % warn, ±25 %
flag) -> apply() writes the month and CARRIES FORWARD the last known price for
every active ingredient nobody bought that month (source='carry_forward', with
its age in months), never over a price a person typed (source='manual').

price_for(code, month) is THE lookup every other part of the center uses.
"""
import json
from collections import defaultdict

from .master import audit, now
from .units import Converter, norm

WARN_PCT, FLAG_PCT = 10.0, 25.0
PACK_RATIO = 2.5          # a pack conversion this far from the last known price is refused


def month_diff(a: str, b: str) -> int:
    """Whole months from a to b ('YYYY-MM')."""
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])


def price_for(con, code: str, month: str):
    """(price_per_base, source, priced_month, age_months) in force for `month`:
    that month's row, else the latest earlier one. An item with NO price up to
    `month` falls back to the earliest LATER supplier quote (list_ref) — the DATAP
    sheet is dated 23-07-2569, so without this an item never bought in June had no
    June price at all; a later actual purchase never reaches back (age < 0 marks it).
    None when never priced."""
    r = con.execute("SELECT price_per_base, source, month, price_age_months FROM bc_ingredient_price"
                    " WHERE ingredient_code=? AND month<=? ORDER BY month DESC LIMIT 1", (code, month)).fetchone()
    if not r:
        r = con.execute("SELECT price_per_base, source, month, price_age_months FROM bc_ingredient_price"
                        " WHERE ingredient_code=? AND month>? AND source='list_ref' ORDER BY month LIMIT 1",
                        (code, month)).fetchone()
        if not r:
            return None
        return float(r[0]), r[1], r[2], month_diff(r[2], month)
    age = (r[3] or 0) + month_diff(r[2], month)
    return float(r[0]), r[1], r[2], age


def nice_factor(x: float) -> float:
    """A suggested pack size, snapped to a round number when within 1 % of one
    (1,005.7 g -> 1,000 · 20.1 -> 20 · 498.3 -> 500); otherwise 3 significant figures."""
    if not x or x <= 0:
        return x
    import math
    mag = 10 ** (math.floor(math.log10(x)) - 1)          # 2 significant figures
    for step in (mag * 10, mag * 5):                     # 1 or 1.5 significant figures
        r = round(x / step) * step
        if r > 0 and abs(r - x) / x <= 0.01:
            return float(f"{r:.6g}")
    return float(f"{x:.3g}")


def reference_price(con, code: str, month: str):
    """A price to sanity-check against: the one in force for `month`, else the
    nearest LATER one (an import of an older month than the supplier sheet)."""
    p = price_for(con, code, month)
    if p:
        return p
    r = con.execute("SELECT price_per_base, source, month FROM bc_ingredient_price WHERE ingredient_code=?"
                    " AND month>? ORDER BY month LIMIT 1", (code, month)).fetchone()
    return (float(r[0]), r[1], r[2], 0) if r else None


def resolve_code(con, code: str, known: set, aliases: dict) -> str:
    """A code from an outside system -> the master code (direct, or via alias)."""
    c = str(code or "").strip()
    if c in known:
        return c
    return aliases.get(c, c)


def aggregate(con, lines: list, add_unknown: bool = False, who: str = "-", month: str = None) -> dict:
    """lines: dicts with code, qty, unit, value (or unit_price), branch_id?, name?.
    add_unknown registers codes the master does not know yet (master.add_from_purchase)
    — inside the caller's transaction, so a dry run rolls them back with the rest.
    -> {'codes': {code: {...}}, 'branches': {(code, bid): {...}}, 'issues': [...], 'added': [...]}"""
    known = {r[0] for r in con.execute("SELECT code FROM bc_ingredient")}
    from .aliases import active_map          # PR-Y: a retired alias no longer resolves
    aliases = active_map(con)
    added = []
    if add_unknown:
        from .master import add_from_purchase
        for ln in lines:
            raw = str(ln.get("code") or "").strip()
            if not raw or raw in ("#N/A", "None"):
                continue
            code = resolve_code(con, raw, known, aliases)
            if code not in known:
                base = add_from_purchase(con, code, ln.get("name"), ln.get("unit"), who)
                known.add(code)
                added.append({"code": code, "name": ln.get("name"), "base_unit": base, "bought_as": ln.get("unit")})
    conv = Converter(con)
    known = set(conv.master)
    codes, branches, issues = {}, {}, []
    for ln in lines:
        raw = str(ln.get("code") or "").strip()
        if not raw:
            continue
        code = resolve_code(con, raw, known, aliases)
        try:
            qty = float(ln.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        value = ln.get("value")
        try:
            value = float(value) if value not in (None, "") else float(ln.get("unit_price") or 0) * qty
        except (TypeError, ValueError):
            value = 0.0
        if qty <= 0 or value <= 0:
            issues.append({"code": raw, "name": ln.get("name"), "unit": ln.get("unit"), "qty": qty,
                           "value": value, "why": "ปริมาณหรือมูลค่าเป็น 0"})
            continue
        if code not in known:
            issues.append({"code": raw, "name": ln.get("name"), "unit": ln.get("unit"), "qty": qty,
                           "value": round(value, 2), "why": "ไม่มีในทะเบียนวัตถุดิบ"})
            continue
        qb, how = conv.to_base(code, qty, ln.get("unit"))
        if qb is None or qb <= 0:
            issues.append({"code": code, "name": ln.get("name"), "unit": ln.get("unit"), "qty": qty,
                           "value": round(value, 2), "why": how})
            continue
        # A generic pack word ("PK", "แพ็ค,ห่อ") was turned into the master's pack size.
        # Accounting often means a smaller pack (shrimp: a 1 kg PK, the master's pack
        # is the 10 kg box) — the price per gram would then be 10x wrong and nothing
        # downstream could tell. Check it against the last known price and refuse it,
        # with the factor that WOULD fit, when it is off by more than PACK_RATIO.
        if conv.last_kind == "pack" and month:
            ref = reference_price(con, code, month)
            if ref and ref[0] > 0:
                ratio = (value / qb) / ref[0]
                if ratio > PACK_RATIO or ratio < 1 / PACK_RATIO:
                    fits = (value / qty) / ref[0]
                    issues.append({"code": code, "name": ln.get("name"), "unit": ln.get("unit"), "qty": qty,
                                   "value": round(value, 2), "suggest_factor": round(fits, 3),
                                   "why": f"{how} ทำให้ราคาเป็น {ratio:.2f} เท่าของราคาล่าสุด 1 {ln.get('unit')}"
                                          f" ควรเท่ากับประมาณ {fits:,.0f} {conv.base_unit(code)} · ตั้งกฎหน่วย"})
                    continue
        a = codes.setdefault(code, {"qty_base": 0.0, "value": 0.0, "lines": 0, "units": set(), "hows": set()})
        a["qty_base"] += qb
        a["value"] += value
        a["lines"] += 1
        a["units"].add(norm(ln.get("unit")))
        a["hows"].add(how)
        bid = ln.get("branch_id")
        if bid is not None:
            b = branches.setdefault((code, int(bid)), {"qty_base": 0.0, "value": 0.0})
            b["qty_base"] += qb
            b["value"] += value
    for a in codes.values():
        a["avg"] = a["value"] / a["qty_base"]
        a["units"] = sorted(a["units"])
        a["hows"] = sorted(a["hows"])
    for b in branches.values():
        b["avg"] = b["value"] / b["qty_base"]
    return {"codes": codes, "branches": branches, "issues": issues, "added": added}


def preview(con, month: str, agg: dict) -> dict:
    """What apply() would write, against the price in force the month before."""
    prev_month = f"{int(month[:4]) - (1 if month[5:7] == '01' else 0):04d}-" \
                 f"{(12 if month[5:7] == '01' else int(month[5:7]) - 1):02d}"
    names = dict(con.execute("SELECT code, name_th FROM bc_ingredient"))
    base = dict(con.execute("SELECT code, base_unit FROM bc_ingredient"))
    manual = {r[0] for r in con.execute(
        "SELECT ingredient_code FROM bc_ingredient_price WHERE month=? AND source='manual'", (month,))}
    rows = []
    for code, a in agg["codes"].items():
        # what this import replaces: the month's own actual (a re-import), else the
        # month before, else the nearest reference (the first import ever)
        own = con.execute("SELECT price_per_base, source, month, price_age_months FROM bc_ingredient_price"
                          " WHERE ingredient_code=? AND month=? AND source IN ('purchase_avg','manual')",
                          (code, month)).fetchone()
        prev = (float(own[0]), own[1], own[2], own[3]) if own else \
            (price_for(con, code, prev_month) or reference_price(con, code, month))
        pct = None
        if prev and prev[0] > 0:
            pct = round(100.0 * (a["avg"] - prev[0]) / prev[0], 1)
        flag = "flag" if pct is not None and abs(pct) >= FLAG_PCT else \
               "warn" if pct is not None and abs(pct) >= WARN_PCT else ("new" if prev is None else "ok")
        rows.append({"code": code, "name": names.get(code), "base_unit": base.get(code),
                     "qty_base": round(a["qty_base"], 3), "value": round(a["value"], 2),
                     "price_per_base": round(a["avg"], 6), "lines": a["lines"], "units": a["units"],
                     "converted_by": a["hows"],
                     "prev_price": round(prev[0], 6) if prev else None,
                     "prev_source": prev[1] if prev else None, "prev_month": prev[2] if prev else None,
                     "change_pct": pct, "flag": flag, "manual_kept": code in manual})
    rows.sort(key=lambda r: -r["value"])
    carried = [c for (c,) in con.execute(
        "SELECT code FROM bc_ingredient WHERE active=1") if c not in agg["codes"]]
    will_carry = sum(1 for c in carried if price_for(con, c, month))
    never = [c for c in carried if not price_for(con, c, month)]
    counts = {k: sum(1 for r in rows if r["flag"] == k) for k in ("ok", "warn", "flag", "new")}
    unknown = sorted({i["code"] for i in agg["issues"] if i["why"] == "ไม่มีในทะเบียนวัตถุดิบ"})
    # PR-Y: each unknown code with the register items it could be a new code of — the person chooses per code, nothing is added
    # on its own (no sheet line is lost: it stays an issue until the code is aliased or registered)
    from .aliases import UNKNOWN_WHY, unknown_in, with_suggestions
    unk_lines = [i for i in agg["issues"] if i["why"] == UNKNOWN_WHY]
    unknown_items = with_suggestions(con, unknown_in(con, unk_lines))
    for u in unknown_items:
        u["value"] = round(sum(i.get("value") or 0 for i in unk_lines if i["code"] == u["code"]), 2)
    unknown_items.sort(key=lambda u: -u["value"])
    # the pack guard's suggestions, one per (code, unit): median factor, how
    # consistent the lines were, and the money they carry — so the analyst can set
    # them in one pass and re-import, instead of one prompt per line
    sug = defaultdict(list)
    for i in agg["issues"]:
        if i.get("suggest_factor"):
            sug[(i["code"], norm(i["unit"]))].append((i["suggest_factor"], i.get("value") or 0, i.get("name")))
    suggested = []
    for (code, unit), lst in sug.items():
        fs = sorted(f for f, _, _ in lst)
        med = fs[len(fs) // 2]
        suggested.append({"code": code, "name": lst[0][2], "unit": unit, "base_unit": base.get(code),
                          "factor": nice_factor(med), "raw_median": round(med, 3), "lines": len(lst),
                          "value": round(sum(v for _, v, _ in lst), 2),
                          "consistent": fs[0] > 0 and fs[-1] / fs[0] <= 1.05})
    suggested.sort(key=lambda r: -r["value"])
    return {"month": month, "rows": rows, "issues": agg["issues"], "counts": counts,
            "added_codes": agg.get("added", []), "unknown_codes": unknown, "unknown_items": unknown_items[:200],
            "suggested_rules": suggested,
            "unknown_value": round(sum(i.get("value") or 0 for i in agg["issues"]
                                       if i["why"] == "ไม่มีในทะเบียนวัตถุดิบ"), 2),
            "priced": len(rows), "carry_forward": will_carry, "never_priced": len(never),
            "never_priced_codes": never[:200],
            "value_total": round(sum(r["value"] for r in rows), 2),
            "value_unused": round(sum(i.get("value") or 0 for i in agg["issues"]), 2)}


def carry_forward(con, month: str, who: str) -> int:
    """Give every active ingredient with no row this month its latest earlier price."""
    n = 0
    # an earlier run's carried rows are recomputed: the price behind them may have changed
    con.execute("DELETE FROM bc_ingredient_price WHERE month=? AND source='carry_forward'", (month,))
    have = {r[0] for r in con.execute("SELECT ingredient_code FROM bc_ingredient_price WHERE month=?", (month,))}
    for (code,) in con.execute("SELECT code FROM bc_ingredient WHERE active=1").fetchall():
        if code in have:
            continue
        p = price_for(con, code, month)
        if not p:
            continue
        con.execute("INSERT INTO bc_ingredient_price(ingredient_code, month, price_per_base, source,"
                    " price_age_months, imported_at, approved_by, note) VALUES(?,?,?,'carry_forward',?,?,?,?)",
                    (code, month, p[0], p[3], now(), who, f"ยกมาจาก {p[2]} ({p[1]})"))
        n += 1
    return n


def apply(con, month: str, agg: dict, who: str, source_note: str = "") -> dict:
    """Write the month's actual averages (keeping manual rows), the per-branch
    averages, then carry forward. Caller commits."""
    written = kept = 0
    for code, a in agg["codes"].items():
        cur = con.execute("SELECT source FROM bc_ingredient_price WHERE ingredient_code=? AND month=?",
                          (code, month)).fetchone()
        if cur and cur[0] == "manual":
            kept += 1
            continue
        con.execute("INSERT OR REPLACE INTO bc_ingredient_price(ingredient_code, month, price_per_base, qty_bought,"
                    " value_bought, source, price_age_months, imported_at, approved_by, note)"
                    " VALUES(?,?,?,?,?,'purchase_avg',0,?,?,?)",
                    (code, month, round(a["avg"], 6), round(a["qty_base"], 4), round(a["value"], 2), now(), who,
                     source_note or None))
        written += 1
    con.execute("DELETE FROM bc_ingredient_price_branch WHERE month=?", (month,))
    con.executemany("INSERT INTO bc_ingredient_price_branch(ingredient_code, month, branch_id, price_per_base,"
                    " qty_bought, value_bought) VALUES(?,?,?,?,?,?)",
                    [(c, month, bid, round(b["avg"], 6), round(b["qty_base"], 4), round(b["value"], 2))
                     for (c, bid), b in agg["branches"].items()])
    # a carried-forward row from an earlier run must give way to the new actuals
    carried = carry_forward(con, month, who)
    audit(con, who, "price_import", "bc_ingredient_price", month, None,
          json.dumps({"written": written, "kept_manual": kept, "carried": carried,
                      "issues": len(agg["issues"]), "note": source_note}, ensure_ascii=False))
    return {"written": written, "kept_manual": kept, "carried_forward": carried,
            "branch_rows": len(agg["branches"])}


def set_manual(con, code: str, month: str, price_per_base: float, note: str, who: str):
    if price_per_base is None:
        old = con.execute("SELECT price_per_base FROM bc_ingredient_price WHERE ingredient_code=? AND month=?"
                          " AND source='manual'", (code, month)).fetchone()
        con.execute("DELETE FROM bc_ingredient_price WHERE ingredient_code=? AND month=? AND source='manual'",
                    (code, month))
        audit(con, who, "manual_price_delete", "bc_ingredient_price", f"{code}|{month}", old[0] if old else None)
        return
    if price_per_base <= 0:
        raise ValueError("ราคาต้องมากกว่า 0")
    if not (note or "").strip():
        raise ValueError("ต้องระบุเหตุผลเมื่อแก้ไขราคาด้วยตนเอง")
    old = con.execute("SELECT price_per_base, source FROM bc_ingredient_price WHERE ingredient_code=? AND month=?",
                      (code, month)).fetchone()
    con.execute("INSERT OR REPLACE INTO bc_ingredient_price(ingredient_code, month, price_per_base, source,"
                " price_age_months, imported_at, approved_by, note) VALUES(?,?,?,'manual',0,?,?,?)",
                (code, month, float(price_per_base), now(), who, note.strip()))
    audit(con, who, "manual_price", "bc_ingredient_price", f"{code}|{month}", old and list(old), price_per_base)


def month_table(con, month: str) -> list:
    """Every master ingredient with the price in force for `month`."""
    out = []
    branch_n = defaultdict(int)
    for code, n in con.execute("SELECT ingredient_code, COUNT(*) FROM bc_ingredient_price_branch WHERE month=?"
                               " GROUP BY ingredient_code", (month,)):
        branch_n[code] = n
    for code, name, base, kind, monitored, active in con.execute(
            "SELECT code, name_th, base_unit, kind, monitored, active FROM bc_ingredient ORDER BY code"):
        p = price_for(con, code, month)
        out.append({"code": code, "name": name, "base_unit": base, "kind": kind, "monitored": bool(monitored),
                    "active": bool(active),
                    "price_per_base": round(p[0], 6) if p else None, "source": p[1] if p else None,
                    "priced_month": p[2] if p else None, "age_months": p[3] if p else None,
                    "branches": branch_n.get(code, 0)})
    return out


def history(con, code: str, limit: int = 24) -> list:
    rows = con.execute("SELECT month, price_per_base, source, qty_bought, value_bought, price_age_months, note,"
                       " approved_by FROM bc_ingredient_price WHERE ingredient_code=? ORDER BY month DESC LIMIT ?",
                       (code, limit)).fetchall()
    out = [dict(zip(("month", "price_per_base", "source", "qty_bought", "value_bought", "age_months",
                     "note", "by"), r)) for r in rows]
    for r in out:
        r["branches"] = [dict(zip(("branch_id", "price_per_base", "qty_bought", "value_bought"), b)) for b in
                         con.execute("SELECT branch_id, price_per_base, qty_bought, value_bought FROM"
                                     " bc_ingredient_price_branch WHERE ingredient_code=? AND month=?"
                                     " ORDER BY branch_id", (code, r["month"]))]
    return out


# ── PR-B4a (1.6): a price for an item that has none - for RANKING and SAFETY only, never written back ───────────
class PriceBook:
    """What an ingredient is worth for the ABC class, the safety stock and the choice of variance reasons when it has no price:

        1. the price in force (price_for) - used as it is, no mark;
        1b. else the price of the centre's order list (central.central_map): the order screen already values the row at it, so the
           item is priced - without this step 19 of 88 rows of อุดรธานี round 1 were ranked as free (class C) while the screen showed
           their ฿ - no mark;
        2. else the LAST KNOWN price of the item, of whatever month (the nearest later month when none is earlier - the price file
           of a later month is still the best evidence of what the item costs);
        3. else the MEDIAN price the benchmark branches paid (bc_ingredient_price_branch, the setting `bc_benchmark_branches`),
           the branch's latest month up to `month`, else its latest month at all;
        4. else None (the item stays valueless).

    `ref(code)` is the figure to rank with (0 when there is none); `info(code)` is None for an item that has its own price, else
    {price, kind 'last' | 'bench' | None, month, label} - the words of the chip "ไม่มีราคา (ใช้ราคา …)". The fallback is read at the moment
    of the question and written NOWHERE: the price table and every ฿ shown on a screen keep the real price (or no price)."""

    LABEL_LAST = "ล่าสุด"
    LABEL_BENCH = "ค่ากลางสาขาบริษัท"

    def __init__(self, con, month: str, cmap: dict = None):
        self.con, self.month = con, month
        self._memo = {}
        self._bench = None
        if cmap is None:
            try:
                from .central import central_map
                cmap = central_map(con)
            except Exception:
                cmap = {}
        self._cmap = cmap or {}

    def _bench_ids(self) -> list:
        if self._bench is None:
            try:
                from .benchmark import branch_ids
                self._bench = list(branch_ids(self.con))
            except Exception:
                self._bench = []
        return self._bench

    def _last_known(self, code):
        r = self.con.execute("SELECT price_per_base, month FROM bc_ingredient_price WHERE ingredient_code=?"
                             " AND price_per_base > 0 ORDER BY (month<=?) DESC, month DESC LIMIT 1",
                             (code, self.month)).fetchone()
        if not r:
            return None
        if r[1] > self.month:                          # none earlier: the NEAREST later month
            r = self.con.execute("SELECT price_per_base, month FROM bc_ingredient_price WHERE ingredient_code=?"
                                 " AND price_per_base > 0 AND month>? ORDER BY month LIMIT 1", (code, self.month)).fetchone()
        return (float(r[0]), r[1]) if r else None

    def _bench_median(self, code):
        ids = self._bench_ids()
        if not ids:
            return None
        import statistics
        got = []
        for b in ids:
            r = self.con.execute("SELECT price_per_base FROM bc_ingredient_price_branch WHERE ingredient_code=? AND branch_id=?"
                                 " AND price_per_base > 0 ORDER BY (month<=?) DESC, month DESC LIMIT 1",
                                 (code, b, self.month)).fetchone()
            if r:
                got.append(float(r[0]))
        return statistics.median(got) if got else None

    def _own(self, code: str):
        """The item's own price per base unit: the price in force, else the centre's list price; None when it has neither."""
        p = price_for(self.con, code, self.month)
        if p:
            return p[0]
        f, pr = self._cmap.get(code, (None, None))
        return pr / f if f and pr else None

    def info(self, code: str):
        if code not in self._memo:
            if self._own(code) is not None:
                self._memo[code] = None
            else:
                last = self._last_known(code)
                if last:
                    self._memo[code] = {"price": last[0], "kind": "last", "month": last[1], "label": self.LABEL_LAST}
                else:
                    med = self._bench_median(code)
                    self._memo[code] = ({"price": med, "kind": "bench", "month": None, "label": self.LABEL_BENCH} if med
                                        else {"price": None, "kind": None, "month": None, "label": None})
        return self._memo[code]

    def ref(self, code: str) -> float:
        """The price to rank / weigh the item with: its own, else the fallback, else 0."""
        i = self.info(code)
        if i is None:
            return self._own(code) or 0.0
        return i["price"] or 0.0

    def chip(self, code: str, unit: str = None):
        """The words for an item without a price: 'ไม่มีราคา (ใช้ราคาล่าสุด 2026-10 ฿12.5 ต่อกรัม)', or just 'ไม่มีราคา' when no fallback
        exists; None when the item has its own price."""
        i = self.info(code)
        if i is None:
            return None
        if not i["price"]:
            return "ไม่มีราคา"
        v = float(i["price"])
        shown = f"{v:,.0f}" if v >= 1000 else f"{v:,.2f}".rstrip("0").rstrip(".")
        when = f" {i['month']}" if i["month"] else ""
        per = f" ต่อ{unit}" if unit else ""
        return f"ไม่มีราคา (ใช้ราคา{i['label']}{when} ฿{shown}{per})"
