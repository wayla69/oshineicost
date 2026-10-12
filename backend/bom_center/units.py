# -*- coding: utf-8 -*-
"""bom_center.units — the Material Converter (plan §4.2 M2).

Every quantity the center stores is in the ingredient's BASE unit (what the
recipes count in: กรัม / มิลลิลิตร / ชิ้น …). Everything people type or import
arrives in some other unit: the accounting report says กิโลกรัม or แพ็ค,ห่อ, the
kitchen says แท่ง or แผ่น. One function turns any of them into base units, and
says HOW it did so — or why it could not — so an import can show its working.

Resolution order for (code, qty, unit):
  1. the unit IS the base unit (after normalising spellings)        -> × 1
  2. a per-ingredient LADDER unit in bc_unit_rule (PR-U): up to 3 units above the
     base, each stated against the next one down — "1 กล่อง = 10 แพ็ค",
     "1 แพ็ค = 1 กิโลกรัม", "1 แท่ง = 15 กรัม". A step is exact (fixed pack content)
     or an average (pieces that differ in weight: "1 ตัว ≈ 4.5 กิโลกรัม, ช่วง 4–5");
     a conversion that passes an average step is APPROXIMATE and says so. The
     metric step (กิโลกรัม → กรัม) is universal and does not count as a step.
  3. a universal metric step (กิโลกรัม -> กรัม, ลิตร -> มิลลิลิตร)     -> × 1000
  4. the receiving unit (แพ็ค / ถุง / กล่อง … or the master's own
     recv_unit) and the master knows qty_per_recv                     -> × qty_per_recv
  otherwise: not convertible — the caller reports it, never guesses.
"""
import math

# spellings -> one canonical name
_SYN = {
    "กรัม": "กรัม", "g": "กรัม", "gr": "กรัม", "gram": "กรัม", "grams": "กรัม", "ก.": "กรัม", "กรม": "กรัม",
    "กิโลกรัม": "กิโลกรัม", "กก.": "กิโลกรัม", "กก": "กิโลกรัม", "kg": "กิโลกรัม", "kgs": "กิโลกรัม",
    "kilogram": "กิโลกรัม", "กิโล": "กิโลกรัม",
    "มิลลิลิตร": "มิลลิลิตร", "มล.": "มิลลิลิตร", "มล": "มิลลิลิตร", "ml": "มิลลิลิตร", "cc": "มิลลิลิตร",
    "ลิตร": "ลิตร", "l": "ลิตร", "lt": "ลิตร", "ltr": "ลิตร", "litre": "ลิตร", "liter": "ลิตร",
    "ชิ้น": "ชิ้น", "pcs": "ชิ้น", "pc": "ชิ้น", "piece": "ชิ้น", "each": "ชิ้น", "อัน": "ชิ้น",
    "ขีด": "ขีด",
    # spellings seen in the accounting system's issue report (June 2569)
    "kg.": "กิโลกรัม", "k.g.": "กิโลกรัม", "ก.ก.": "กิโลกรัม", "กิโลกรัม.": "กิโลกรัม",
    "lit": "ลิตร", "lit.": "ลิตร", "ลิตร.": "ลิตร", "l.": "ลิตร",
    "gr.": "กรัม", "g.": "กรัม",
    # one purchased pack is written five ways in the same report: one canonical
    # name per pack kind, so ONE unit rule covers "PK", "แพ็ค" and "แพ็ค,ห่อ" alike
    "pk": "แพ็ค", "pk.": "แพ็ค", "pack": "แพ็ค", "pkt": "แพ็ค", "แพ็ก": "แพ็ค", "แพค": "แพ็ค", "ห่อ": "แพ็ค",
    "แพ็ค,ห่อ": "แพ็ค", "แพ็ก,ห่อ": "แพ็ค", "แพ็ค": "แพ็ค",
    "bot": "ขวด", "bot.": "ขวด", "bottle": "ขวด", "ขวด": "ขวด",
    "gal": "แกลลอน", "gal.": "แกลลอน", "gallon": "แกลลอน", "แกลอน": "แกลลอน", "แกลลอน": "แกลลอน",
    "box": "กล่อง", "กล่อง": "กล่อง", "ctn": "ลัง", "carton": "ลัง",
    "ปิ๊บ": "ปี๊บ", "ปีบ": "ปี๊บ", "ปี๊บ": "ปี๊บ",
}
# universal steps between canonical units: (from, to) -> factor
_METRIC = {
    ("กิโลกรัม", "กรัม"): 1000.0, ("กรัม", "กิโลกรัม"): 0.001,
    ("ลิตร", "มิลลิลิตร"): 1000.0, ("มิลลิลิตร", "ลิตร"): 0.001,
    ("ขีด", "กรัม"): 100.0, ("กิโลกรัม", "ขีด"): 10.0,
}
# words that mean "one purchased pack" whatever the pack is. A gallon is a pack
# here, not 3,785 ml: Thai suppliers sell 3.8 L, 4 L and 4.5 L "แกลลอน" alike, so
# the master's qty_per_recv says how big THIS one is.
_PACK_WORDS = {"แพ็ค", "แพ็ก", "แพค", "ห่อ", "แพ็ค,ห่อ", "แพ็ก,ห่อ", "pack", "pk", "pkt", "ถุง", "bag",
               "กล่อง", "box", "ลัง", "carton", "ctn", "ขวด", "bottle", "กระป๋อง", "can", "แกลลอน", "แกลอน",
               "gallon", "gal", "gal.", "ถัง", "tub", "sack", "กระสอบ", "แผง", "tray", "ปิ๊บ", "ปี๊บ", "ปีบ",
               "กระปุก", "jar", "ซอง", "หีบ", "ม้วน", "roll"}


def norm(unit) -> str:
    """Canonical spelling of a unit ('' for none)."""
    u = str(unit or "").strip()
    key = u.lower().replace(" ", "")
    if key in _SYN:
        return _SYN[key]
    if u in _SYN:
        return _SYN[u]
    k2 = key.rstrip(".")                        # "KG." / "ลิตร." -> the plain spelling
    return _SYN.get(k2, u)


def _is_pack_word(u: str) -> bool:
    k = u.lower().replace(" ", "")
    return k in _PACK_WORDS or any(k.startswith(w) for w in ("แพ็ค", "แพ็ก", "pack"))


MAX_STEPS = 3                       # units above the base unit in one ladder (PR-U)
KIND_EXACT, KIND_AVG = "exact", "avg"
KIND_TH = {KIND_EXACT: "แน่นอน", KIND_AVG: "เฉลี่ย"}
_KIND_FROM_TH = {"แน่นอน": KIND_EXACT, "เฉลี่ย": KIND_AVG, "exact": KIND_EXACT, "avg": KIND_AVG, "average": KIND_AVG}


def kind_of(v) -> str:
    """'แน่นอน' / 'เฉลี่ย' / 'exact' / 'avg' -> 'exact' | 'avg'; empty -> 'exact'; anything else -> ''."""
    t = str(v or "").strip()
    if not t:
        return KIND_EXACT
    return _KIND_FROM_TH.get(t, _KIND_FROM_TH.get(t.lower(), ""))


def fmt_num(x) -> str:
    """1000 -> '1,000' · 4.5 -> '4.5' · 0.0666667 -> '0.066667' (no scientific notation, no trailing zeros)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    t = format(round(v, 6), ",f")
    return t.rstrip("0").rstrip(".") if "." in t else t


def metric_units(base: str) -> set:
    """The universal units of a base unit (กิโลกรัม and ขีด for กรัม, ลิตร for มิลลิลิตร …)."""
    return {a for (a, b) in _METRIC if b == base}


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def climb(base: str, rows: dict, unit: str, _depth: int = 1, _seen: tuple = ()):
    """Resolve one ladder unit down to the base unit.

    `rows` = {unit: {parent, qty, kind, rmin, rmax, factor}} — the whole ladder of ONE item, units already
    normalised, parent None = the base unit. -> (factor, approx, parts, reason). `parts` names the steps from
    `unit` downwards: [{unit, qty, parent, kind, rmin, rmax}], with the metric step last when the chain ends on one.
    A unit that cannot be resolved returns (None, False, [], reason)."""
    r = rows.get(unit)
    if r is None:
        return None, False, [], f"ไม่พบหน่วย {unit} ในบันไดหน่วย"
    if unit in _seen:
        return None, False, [], f"{unit}: อ้างอิงวนกลับมาที่ตัวเอง"
    if _depth > MAX_STEPS + 1:
        return None, False, [], f"{unit}: แปลงเป็นหน่วยฐานได้ไม่เกิน {MAX_STEPS} ขั้น"
    if r.get("qty") is None:                              # a rule written before PR-U: one step straight to the base
        f = float(r["factor"])
        return f, False, [{"unit": unit, "qty": f, "parent": base, "kind": KIND_EXACT, "rmin": None, "rmax": None}], None
    parent = r.get("parent") or base
    part = {"unit": unit, "qty": float(r["qty"]), "parent": parent, "kind": r.get("kind") or KIND_EXACT,
            "rmin": r.get("rmin"), "rmax": r.get("rmax")}
    approx = part["kind"] == KIND_AVG
    if parent == base:
        pf, pa, pparts = 1.0, False, []
    elif parent in rows and parent != unit:
        pf, pa, pparts, why = climb(base, rows, parent, _depth + 1, _seen + (unit,))
        if pf is None:
            return None, False, [], why
    elif (parent, base) in _METRIC:
        pf, pa = _METRIC[(parent, base)], False
        pparts = [{"unit": parent, "qty": pf, "parent": base, "kind": KIND_EXACT, "rmin": None, "rmax": None, "metric": True}]
    else:
        return None, False, [], f"{unit}: หน่วยที่อ้างอิง “{parent}” ไม่อยู่ในบันไดหน่วยของรายการนี้"
    return part["qty"] * pf, approx or pa, [part] + pparts, None


def chain_text(parts: list) -> str:
    """'1 กล่อง = 10 แพ็ค · 1 แพ็ค = 1,000 กรัม' — with '≈ ' in front and '(เฉลี่ย ช่วง 4–5 กิโลกรัม)' behind when an
    average step is part of the chain."""
    if not parts:
        return ""
    t = " · ".join(f"1 {p['unit']} = {fmt_num(p['qty'])} {p['parent']}" for p in parts)
    avg = [p for p in parts if p["kind"] == KIND_AVG]
    if not avg:
        return t
    notes = [f"ช่วง {fmt_num(p['rmin'])}–{fmt_num(p['rmax'])} {p['parent']}" for p in avg
             if p.get("rmin") is not None and p.get("rmax") is not None]
    return f"≈ {t} (เฉลี่ย" + (" " + " · ".join(notes) if notes else "") + ")"


def check_ladder(base_unit: str, rows: list) -> list:
    """Problems of one item's WHOLE ladder (pure; an empty list = acceptable). rows = [{unit, parent_unit, qty_in_parent,
    conv_kind, range_min, range_max}] — the stored column names."""
    base = norm(base_unit)
    if not base:
        return ["วัตถุดิบนี้ยังไม่ระบุหน่วยฐาน · ระบุหน่วยฐานในทะเบียนก่อน"]
    out, metric = [], metric_units(base)
    seen, nr = set(), {}
    if len(rows) > MAX_STEPS:
        out.append(f"ตั้งหน่วยได้ไม่เกิน {MAX_STEPS} หน่วยต่อวัตถุดิบ")
    for i, r in enumerate(rows, start=1):
        u = norm(r.get("unit"))
        if not u:
            out.append(f"แถวที่ {i}: ต้องระบุหน่วย")
            continue
        if u == base or u in metric:
            out.append(f"{u}: เป็นหน่วยฐานหรือหน่วยมาตรฐานของ {base} อยู่แล้ว จึงกำหนดการแปลงซ้ำไม่ได้")
            continue
        if u in seen:
            out.append(f"{u}: ชื่อหน่วยซ้ำกัน")
            continue
        seen.add(u)
        q = _num(r.get("qty_in_parent"))
        parent = norm(r.get("parent_unit")) or None
        if parent == base:
            parent = None
        kind = kind_of(r.get("conv_kind"))
        rmin, rmax = _num(r.get("range_min")), _num(r.get("range_max"))
        if q is None or q <= 0:
            out.append(f"{u}: จำนวนในช่อง “1 หน่วยนี้ =” ต้องมากกว่า 0")
        if not kind:
            out.append(f"{u}: ชนิดการแปลงต้องเป็น แน่นอน หรือ เฉลี่ย")
            kind = KIND_EXACT
        if kind == KIND_EXACT and (rmin is not None or rmax is not None):
            out.append(f"{u}: การแปลงแบบแน่นอนไม่มีช่วงต่ำสุด–สูงสุด · เปลี่ยนเป็นเฉลี่ย หรือลบช่วงออก")
        if kind == KIND_AVG and (rmin is not None or rmax is not None):
            if rmin is None or rmax is None:
                out.append(f"{u}: ช่วงต้องใส่ทั้งค่าต่ำสุดและค่าสูงสุด")
            elif rmin <= 0 or rmax <= 0:
                out.append(f"{u}: ค่าต่ำสุดและค่าสูงสุดต้องมากกว่า 0")
            elif rmin > rmax:
                out.append(f"{u}: ค่าต่ำสุดต้องไม่มากกว่าค่าสูงสุด")
            elif q is not None and q > 0 and not (rmin - 1e-9 <= q <= rmax + 1e-9):
                out.append(f"{u}: ค่าเฉลี่ย {fmt_num(q)} ต้องอยู่ในช่วง {fmt_num(rmin)}–{fmt_num(rmax)}")
        nr[u] = parent
    for u, parent in nr.items():
        if parent is not None and parent not in metric and parent not in nr:
            out.append(f"{u}: หน่วยที่อ้างอิง “{parent}” ไม่อยู่ในบันไดหน่วยของรายการนี้ (ใช้ {base} หรือหน่วยอื่นในรายการ)")
    cyc = set()
    for u in nr:
        cur, path = u, []
        while cur in nr and cur not in path:
            path.append(cur)
            cur = nr[cur]
        if cur in path:
            cyc.add(u)
            out.append(f"{u}: อ้างอิงวนกลับมาที่ตัวเอง")
    for u in nr:
        if u in cyc:
            continue
        depth, cur = 0, u
        while cur in nr:
            depth += 1
            cur = nr[cur]
        if depth > MAX_STEPS:
            out.append(f"{u}: แปลงเป็นหน่วยฐานได้ไม่เกิน {MAX_STEPS} ขั้น")
    return out


class Converter:
    """Loaded once per request from bc_ingredient + bc_unit_rule."""

    def __init__(self, con):
        self.last_kind = "exact"          # "pack" after a generic pack-word conversion
        self.last_approx = False          # True after a conversion that passed an average step (PR-U)
        self.master = {}
        for code, base, recv, per in con.execute(
                "SELECT code, base_unit, recv_unit, qty_per_recv FROM bc_ingredient"):
            self.master[code] = (norm(base), str(recv or "").strip(), float(per) if per else None)
        self.rules = {}                   # (code, unit) -> factor_to_base — kept for the readers that look a rule up
        self.ladders = {}                 # code -> {unit: {parent, qty, kind, rmin, rmax, factor}}
        cols = {r[1] for r in con.execute("PRAGMA table_info(bc_unit_rule)")}
        if "qty_in_parent" in cols:       # a database brought up to date by schema.ensure(); an older one has only the factor
            q = ("SELECT ingredient_code, unit, factor_to_base, parent_unit, qty_in_parent, conv_kind, range_min, range_max"
                 " FROM bc_unit_rule")
        else:
            q = "SELECT ingredient_code, unit, factor_to_base, NULL, NULL, NULL, NULL, NULL FROM bc_unit_rule"
        for code, unit, f, parent, qty, kind, rmin, rmax in con.execute(q):
            u = norm(unit)
            self.rules[(code, u)] = float(f)
            self.ladders.setdefault(code, {})[u] = {
                "factor": float(f), "parent": norm(parent) or None, "qty": None if qty is None else float(qty),
                "kind": kind_of(kind) or KIND_EXACT, "rmin": rmin, "rmax": rmax}
        self._memo = {}

    def base_unit(self, code: str) -> str:
        m = self.master.get(code)
        return m[0] if m else ""

    def _ladder_unit(self, code: str, u: str):
        """-> (factor, approx, text) of a ladder unit, (None, False, reason) when the chain cannot be resolved."""
        key = (code, u)
        if key not in self._memo:
            base = self.base_unit(code)
            f, approx, parts, why = climb(base, self.ladders.get(code) or {}, u)
            self._memo[key] = (f, approx, chain_text(parts)) if f is not None else (None, False, why)
        return self._memo[key]

    def ladder(self, code: str) -> list:
        """The item's ladder, lowest level first: [{unit, parent_unit (the base unit's name when none), qty_in_parent,
        kind, range_min, range_max, factor_to_base, level, approx, how}]."""
        base = self.base_unit(code)
        out = []
        for u, r in (self.ladders.get(code) or {}).items():
            f, approx, parts, why = climb(base, self.ladders[code], u)
            depth = sum(1 for p in parts if not p.get("metric"))
            out.append({"unit": u, "parent_unit": r["parent"] or base, "qty_in_parent": r["qty"] if r["qty"] is not None else r["factor"],
                        "kind": r["kind"], "range_min": r["rmin"], "range_max": r["rmax"], "factor_to_base": f,
                        "level": depth or 1, "approx": bool(approx), "how": chain_text(parts) if f is not None else why})
        out.sort(key=lambda x: (x["level"], x["unit"]))
        return out

    def approx_units(self, code: str) -> set:
        """The ladder units of an item whose conversion passes an average step."""
        return {u for u in (self.ladders.get(code) or {}) if self._ladder_unit(code, u)[1]}

    def approx_unit(self, code: str):
        """(unit, base units in 1 unit) of the item's lowest average ladder unit — what a branch order row shows as
        '≈ 2.1 ตัว' beside the weight it orders — or None when the item has no average step."""
        for r in self.ladder(code):
            if r["approx"] and r["factor_to_base"]:
                return r["unit"], r["factor_to_base"]
        return None

    def span(self, code: str, unit):
        """(lightest, heaviest) base units in ONE `unit` when the conversion passes an average step that carries a range
        (1 ตัว of 4–5 กก. -> (4000, 5000)); None when the unit is exact or the average has no range."""
        u = norm(unit)
        if (code, u) not in self.rules:
            return None
        base = self.base_unit(code)
        f, approx, parts, _ = climb(base, self.ladders.get(code) or {}, u)
        if f is None or not approx:
            return None
        lo = hi = f
        found = False
        for p in parts:
            if p["kind"] == KIND_AVG and p.get("rmin") is not None and p.get("rmax") is not None and p["qty"]:
                lo, hi, found = lo * p["rmin"] / p["qty"], hi * p["rmax"] / p["qty"], True
        return (lo, hi) if found else None

    def from_base(self, code: str, qty_base, unit) -> tuple:
        """-> (qty in `unit` or None, how): the way back from base units, e.g. 4,500 กรัม -> 1 ตัว."""
        f, how = self.factor(code, unit)
        if f is None:
            return None, how
        try:
            return float(qty_base) / f, how
        except (TypeError, ValueError):
            return None, "ปริมาณไม่ใช่ตัวเลข"

    def factor(self, code: str, unit) -> tuple:
        """-> (factor_to_base or None, how). `how` names the rule used, or why not.
        A composite unit from the accounting system ("ชิ้น,ฟอง", "แพ็ค,ห่อ") is tried
        whole first, then part by part."""
        f, how = self._factor(code, unit)
        if f is None and "," in str(unit or "") and self.master.get(code):
            for part in str(unit).split(","):
                g, h = self._factor(code, part)
                if g is not None:
                    return g, h
        return f, how

    def _factor(self, code: str, unit) -> tuple:
        self.last_kind = "exact"
        self.last_approx = False
        m = self.master.get(code)
        if m is None:
            return None, "ไม่มีรหัสนี้ในทะเบียนวัตถุดิบ"
        base, recv, per = m
        u = norm(unit)
        if not u:
            return None, "ไม่มีหน่วย"
        if base and u == base:
            return 1.0, "หน่วยฐาน"
        if (code, u) in self.rules:
            f, approx, text = self._ladder_unit(code, u)
            self.last_approx = approx
            return f, text
        if base and (u, base) in _METRIC:
            return _METRIC[(u, base)], f"{u} → {base}"
        recv_n = norm(recv)
        if per and (u == recv_n or _is_pack_word(u)):
            self.last_kind = "pack"           # a generic pack word: the caller may sanity-check it
            return per, f"1 {u} = {per:g} {base}"
        if not base:
            return None, "ทะเบียนยังไม่ระบุหน่วยฐาน"
        if per is None and _is_pack_word(u):
            return None, f"ยังไม่ระบุว่า 1 {u} เท่ากับกี่ {base}"
        return None, f"แปลง {u} เป็น {base} ไม่ได้ · เพิ่มกฎหน่วย"

    def to_base(self, code: str, qty, unit) -> tuple:
        """-> (qty_in_base or None, how)."""
        f, how = self.factor(code, unit)
        if f is None:
            return None, how
        try:
            return float(qty) * f, how
        except (TypeError, ValueError):
            return None, "ปริมาณไม่ใช่ตัวเลข"


def to_order_units(qty_base: float, qty_per_order: float, moq: float = 1.0, step: float = 1.0) -> float:
    """Whole purchasing units for a base-unit need, rounded UP (short is worse
    than carry-over), at least `moq` when anything is needed, in multiples of `step`."""
    if not qty_base or qty_base <= 0 or not qty_per_order or qty_per_order <= 0:
        return 0.0
    step = step if step and step > 0 else 1.0
    units = math.ceil(qty_base / qty_per_order / step - 1e-9) * step
    return max(units, moq or 0.0)
