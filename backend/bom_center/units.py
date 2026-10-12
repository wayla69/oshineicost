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
     recv_unit — by the label as written or by its unit word, see unit_word: "กล่อง (10 แพ็ค)" is a กล่อง)
     and the master knows qty_per_recv                                -> × qty_per_recv
  otherwise: not convertible — the caller reports it, never guesses.
"""
import math
import re

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


_WORD_STOP = re.compile(r"[^\s()\[\]{}（）0-9๐-๙]+")


def unit_word(label) -> str:
    """The unit WORD of a receiving-unit label: the first word before a space, a bracket or a digit, spelled the canonical way.

    The register keeps labels that carry their contents — "กล่อง (10 แพ็ค)", "แพ็ค 10 กก.", "กล่อง (20 กิโลกรัม)" — beside plain
    ones ("กิโลกรัม", "กระสอบ"). Matching a unit NAME against the receiving unit, and writing the unit after a quantity, both
    use this word: "กล่อง (10 แพ็ค)" -> "กล่อง", "แพ็ค 10 กก." -> "แพ็ค", "กิโลกรัม" -> "กิโลกรัม", "" -> "". The stored label is
    never rewritten."""
    t = str(label or "").strip()
    if not t:
        return ""
    m = _WORD_STOP.search(t)
    return norm(m.group(0)) if m else ""


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
        self.names = {}                   # code -> name_th: what a message to the floor calls the item (fmt.item)
        cols = {r[1] for r in con.execute("PRAGMA table_info(bc_ingredient)")}
        name_sql = "name_th" if "name_th" in cols else "NULL"
        if "short_name" in cols:                      # 4.1: a message to the floor says the short name when the register has one
            name_sql = f"COALESCE(NULLIF(TRIM(short_name), ''), {name_sql})"
        for code, base, recv, per, nm in con.execute(
                "SELECT code, base_unit, recv_unit, qty_per_recv, " + name_sql + " FROM bc_ingredient"):
            self.master[code] = (norm(base), str(recv or "").strip(), float(per) if per else None)
            self.names[code] = nm or ""
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

    def weight_avg_step(self, code: str):
        """The item's lowest AVERAGE step that is stated in the item's own weight / volume (PR-U2) — 'ตัว ≈ 4.5 กิโลกรัม':
        its parent is the base unit or a metric unit of it. Goods received by weight whose pieces differ in weight have one;
        the branch counts the pieces at receiving and the real average is learned from them.
        -> {unit, parent (name), parent_factor (base units in 1 parent), qty (average, in parent), rmin, rmax, factor_to_base,
        level} or None."""
        base = self.base_unit(code)
        best = None
        for r in self.ladder(code):
            if r["kind"] != KIND_AVG or r["factor_to_base"] is None:
                continue
            row = (self.ladders.get(code) or {}).get(r["unit"]) or {}
            parent = row.get("parent")
            if parent and parent != base and (parent, base) not in _METRIC:
                continue                                  # stated against another ladder unit: not a weight per piece
            pf = 1.0 if (not parent or parent == base) else _METRIC[(parent, base)]
            best = {"unit": r["unit"], "parent": parent or base, "parent_factor": pf, "qty": r["qty_in_parent"],
                    "rmin": r["range_min"], "rmax": r["range_max"], "factor_to_base": r["factor_to_base"], "level": r["level"]}
            break                                         # ladder() is ordered lowest level first
        return best

    def sealed(self, code: str) -> list:
        """PR-B2 (3.1): the SEALED units of an item, smallest first — the packs a person holds in the hand: [{unit, label, factor,
        source}] (`factor` = base units in one; `label` is how the unit is written on the screen: a receiving unit that carries its
        contents, "แพ็ค 1 กก.", keeps it). They are the base unit when it is itself a pack word (an item stocked in แพ็ค), every
        EXACT ladder unit that is a pack word (แพ็ค, ขวด, ถุง, แกลลอน …) and the master's receiving unit when that is one (กล่อง
        (10 แพ็ค) -> กล่อง). A piece that differs in weight (ตัว ≈ 4.5 กก.), a loose weight (กิโลกรัม) and a piece word (ชิ้น) are not sealed."""
        m = self.master.get(code)
        if m is None:
            return []
        base, recv, per = m
        out = []
        if base and _is_pack_word(base):
            out.append({"unit": base, "label": base, "factor": 1.0, "source": "base"})
        for r in self.ladder(code):
            f = r["factor_to_base"]
            if r["kind"] == KIND_AVG or not f or f <= 0 or r["unit"] == base or not _is_pack_word(r["unit"]):
                continue
            out.append({"unit": r["unit"], "label": r["unit"], "factor": float(f), "source": "ladder"})
        w = unit_word(recv)
        if per and recv and w and _is_pack_word(w):
            same = next((o for o in out if o["unit"] == w and abs(o["factor"] - float(per)) < 1e-9), None)
            if same:
                same["label"] = recv
            else:
                out.append({"unit": w, "label": recv, "factor": float(per), "source": "recv"})
        out.sort(key=lambda o: o["factor"])               # a stable sort: a tie keeps the ladder's own order
        return out

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
        recv_n = norm(recv)                          # the label as written ("กล่อง (10 แพ็ค)") …
        recv_w = unit_word(recv)                     # … and the unit word it starts with ("กล่อง")
        if per and (u == recv_n or (recv_w and u == recv_w) or _is_pack_word(u)):
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


# ── PR-B4a (1.7): a BIG pack is not rounded up for a small remainder ─────────────────────────────────────────
ROUND_DOWN_FRACTION = 0.20      # the remainder under this share of ONE pack may be left unordered (owner decision 1.7, 2026-10-12)
BIG_PACK_KG = 5.0               # a pack of at least this weight is a big pack …
BIG_PACK_INNER = 10             # … and so is one that holds at least this many sealed inner units (กล่อง (10 แพ็ค))


def pack_size(conv: "Converter", code: str, per) -> dict:
    """{kg, inner} of ONE receiving pack of `per` base units: its weight in kilograms when the base unit is a mass (None otherwise)
    and the number of the next smaller sealed unit it holds (the 10 แพ็ค of a box; None when there is none)."""
    out = {"kg": None, "inner": None}
    if not per or per <= 0:
        return out
    base = conv.base_unit(code)
    f = 1.0 if base == "กิโลกรัม" else _METRIC.get(("กิโลกรัม", base))
    if f:
        out["kg"] = float(per) / f
    smaller = [u for u in conv.sealed(code) if u["factor"] < per * (1 - 1e-9)]
    if smaller:
        out["inner"] = float(per) / smaller[-1]["factor"]
    return out


def is_big_pack(conv: "Converter", code: str, per) -> bool:
    """Apply the round-down rule to this pack? ≥ BIG_PACK_KG kilograms, or ≥ BIG_PACK_INNER sealed inner units."""
    s = pack_size(conv, code, per)
    return bool((s["kg"] or 0) >= BIG_PACK_KG or (s["inner"] or 0) >= BIG_PACK_INNER - 1e-9)


def to_order_units_ex(qty_base: float, qty_per_order: float, moq: float = 1.0, step: float = 1.0, safety: float = 0.0,
                      big: bool = False) -> tuple:
    """(units, rounded_down) - to_order_units, except that a BIG pack is rounded DOWN when the remainder is under ROUND_DOWN_FRACTION
    of one pack and the part left unordered is covered by the safety stock (`safety`, base units): the stock at the end of the cover
    period, forecast use already taken out, then stays at or above 0. `qty_base` is forecast + safety − stock, so the shortfall that
    rounding down leaves is exactly the remainder and it is taken out of the safety stock only; "stays ≥ safety stock" read
    literally could never hold, the need already containing the whole of it. A pack below the big-pack size, a remainder of
    ROUND_DOWN_FRACTION or more, a remainder the safety stock does not cover, and a minimum order above the rounded-down quantity
    all keep the rounding up of to_order_units."""
    up = to_order_units(qty_base, qty_per_order, moq, step)
    if not big or up <= 0 or not qty_per_order or qty_per_order <= 0:
        return up, False
    step = step if step and step > 0 else 1.0
    floor_u = math.floor(qty_base / qty_per_order / step + 1e-9) * step
    rem = qty_base - floor_u * qty_per_order
    if rem <= 1e-9 or rem >= ROUND_DOWN_FRACTION * qty_per_order - 1e-9 or rem > (safety or 0.0) + 1e-9:
        return up, False
    if floor_u > 0 and floor_u < (moq or 0.0):
        return up, False
    return float(floor_u), True


# ── the average weight per piece, learned from real receipts (PR-U2) ─────────────────────────────────────────
AVG_MIN_RECEIPTS, AVG_MIN_PIECES, AVG_MIN_DIFF_PCT = 3, 10, 3.0     # L5: when the observed average becomes a proposal
AVG_WINDOW_DAYS = 90


def _window(days: int, today=None) -> tuple:
    from datetime import timedelta
    from .clock import today as bkk_today
    end = today or bkk_today()
    return (end - timedelta(days=max(int(days), 1))).isoformat(), end.isoformat()


def observed_avg(con, code: str, unit: str = None, days: int = AVG_WINDOW_DAYS, today=None, conv: "Converter" = None) -> dict:
    """The real average weight of ONE piece of an item, from the receipts of every branch in the last `days` days (L4):
    Σ received weight ÷ Σ pieces counted, in the parent unit of the average step (กิโลกรัม for a salmon).

    -> {code, unit, parent, observed, n_receipts, n_pieces, min_receipt_avg, max_receipt_avg, current, range, diff_pct, since,
        until, newest_receipt_id}; `observed` is None when no receipt of the window carries a piece count.
    Only receipt lines that carry a piece count in the step's unit are read; a branch that did not count adds nothing."""
    conv = conv or Converter(con)
    step = conv.weight_avg_step(code)
    out = {"code": code, "unit": unit or (step or {}).get("unit"), "parent": (step or {}).get("parent"), "observed": None,
           "n_receipts": 0, "n_pieces": 0.0, "min_receipt_avg": None, "max_receipt_avg": None,
           "current": (step or {}).get("qty"), "range": [step["rmin"], step["rmax"]] if step and step["rmin"] is not None else None,
           "diff_pct": None, "newest_receipt_id": None}
    out["since"], out["until"] = _window(days, today)
    if step is None or (unit and norm(unit) != step["unit"]):
        return out
    pf = step["parent_factor"]
    per, tot_b, tot_p, newest = [], 0.0, 0.0, 0
    for rid, qb, pcs in con.execute(
            "SELECT l.receipt_id, l.qty_base, l.pieces FROM bc_receipt_line l JOIN bc_receipt r USING(receipt_id)"
            " WHERE l.ingredient_code=? AND l.pieces>0 AND l.qty_base>0 AND l.pieces_unit=?"
            " AND r.receipt_date>=? AND r.receipt_date<=?", (code, step["unit"], out["since"], out["until"])):
        per.append(qb / pcs / pf)
        tot_b, tot_p, newest = tot_b + qb, tot_p + pcs, max(newest, rid)
    if per:
        out.update(observed=tot_b / tot_p / pf, n_receipts=len(per), n_pieces=tot_p, min_receipt_avg=min(per),
                   max_receipt_avg=max(per), newest_receipt_id=newest)
        if step["qty"]:
            out["diff_pct"] = (out["observed"] - step["qty"]) / step["qty"] * 100.0
    return out


def _last_decision(con, code: str, unit: str):
    r = con.execute("SELECT decision, note, decided_by, decided_at, last_receipt_id FROM bc_unit_avg_decision"
                    " WHERE ingredient_code=? AND unit=? ORDER BY decision_id DESC LIMIT 1", (code, unit)).fetchone()
    return dict(zip(("decision", "note", "decided_by", "decided_at", "last_receipt_id"), r)) if r else None


def _judge(obs: dict, last: dict) -> dict:
    """Does the observed average become a proposal (L5)? -> {enough, newer, proposal, state}."""
    enough = obs["n_receipts"] >= AVG_MIN_RECEIPTS and obs["n_pieces"] >= AVG_MIN_PIECES
    newer = last is None or (obs["newest_receipt_id"] or 0) > (last.get("last_receipt_id") or 0)
    big = obs["diff_pct"] is not None and abs(obs["diff_pct"]) >= AVG_MIN_DIFF_PCT
    proposal = bool(enough and big and newer)
    state = "proposal" if proposal else ("insufficient" if not enough else ("decided" if big and not newer else "steady"))
    return {"enough": enough, "newer": newer, "proposal": proposal, "state": state}


def avg_proposals(con, days: int = AVG_WINDOW_DAYS, today=None) -> list:
    """Every item with an average weight step, with what the receipts of the last `days` days say (observed_avg) and whether it
    is a proposal for the analyst (L5: ≥ 3 receipts, ≥ 10 pieces, a difference of ≥ 3 %, and a receipt newer than the last decision).
    Proposals first (largest difference first), then the rest by name."""
    conv = Converter(con)
    names = {c: (n, a) for c, n, a in con.execute("SELECT code, name_th, COALESCE(active, 1) FROM bc_ingredient")}
    out = []
    for code in sorted(conv.ladders):
        if code not in names or not names[code][1]:
            continue
        step = conv.weight_avg_step(code)
        if step is None:
            continue
        obs = observed_avg(con, code, step["unit"], days, today, conv)
        last = _last_decision(con, code, step["unit"])
        j = _judge(obs, last)
        out.append({**obs, **j, "name": names[code][0], "parent_factor": step["parent_factor"], "last_decision": last,
                    "need_text": f"ข้อมูลยังไม่พอ (ต้องรับอย่างน้อย {AVG_MIN_RECEIPTS} ครั้ง และ {AVG_MIN_PIECES} {step['unit']})"})
    out.sort(key=lambda r: (not r["proposal"], -abs(r["diff_pct"] or 0.0) if r["proposal"] else 0.0, r["name"] or ""))
    return out


def avg_history(con, limit: int = 30) -> list:
    names = dict(con.execute("SELECT code, name_th FROM bc_ingredient"))
    cols = ("decision_id", "ingredient_code", "unit", "current_avg", "observed_avg", "n_receipts", "n_pieces", "window_from",
            "window_to", "decision", "note", "decided_by", "decided_at")
    out = [dict(zip(cols, r)) for r in con.execute(
        f"SELECT {', '.join(cols)} FROM bc_unit_avg_decision ORDER BY decision_id DESC LIMIT ?", (limit,))]
    for r in out:
        r["name"] = names.get(r["ingredient_code"])
    return out


def decide_avg(con, code: str, unit: str, decision: str, who: str, note: str = None, days: int = AVG_WINDOW_DAYS,
               today=None) -> dict:
    """The analyst's answer to a proposal (L5). 'applied' makes the observed average (rounded to 2 decimals, in the parent
    unit) the average of the step — through master.set_ladder, so every reader follows — and widens the range when the new
    value falls outside it; 'kept' leaves the ladder as it is and needs a note. Both are recorded. Caller commits."""
    from .master import audit, set_ladder, _ladder_rows
    if decision not in ("applied", "kept"):
        raise ValueError("ตัดสินใจไม่ถูกต้อง · ใช้ค่านี้ หรือ คงค่าเดิม")
    code, u = (code or "").strip(), norm(unit)
    note = (note or "").strip() or None
    if decision == "kept" and not note:
        raise ValueError("คงค่าเดิมต้องระบุเหตุผล")
    conv = Converter(con)
    step = conv.weight_avg_step(code)
    if step is None or step["unit"] != u:
        raise ValueError(f"{code}: ไม่มีหน่วยเฉลี่ย “{unit}” ที่เรียนรู้จากการรับสินค้าได้")
    obs = observed_avg(con, code, u, days, today, conv)
    j = _judge(obs, _last_decision(con, code, u))
    if not j["enough"]:
        raise ValueError(f"ข้อมูลยังไม่พอ (ต้องรับอย่างน้อย {AVG_MIN_RECEIPTS} ครั้ง และ {AVG_MIN_PIECES} {u})")
    if not j["proposal"]:
        raise ValueError("ไม่มีข้อเสนอให้ตัดสินใจ · ค่าที่ใช้ใกล้เคียงกับที่รับจริง หรือตัดสินใจไปแล้วและยังไม่มีการรับใหม่")
    new, widened, rng = None, False, None
    if decision == "applied":
        new = round(float(obs["observed"]), 2)
        if new <= 0:
            raise ValueError("ค่าเฉลี่ยที่รับจริงต้องมากกว่า 0")
        rows = _ladder_rows(con, code)
        for r in rows:
            if norm(r["unit"]) != u:
                continue
            r["qty_in_parent"] = new
            lo, hi = r.get("range_min"), r.get("range_max")
            if lo is not None and hi is not None:
                nlo, nhi = min(lo, new), max(hi, new)
                widened = (nlo, nhi) != (lo, hi)
                r["range_min"], r["range_max"] = nlo, nhi
                rng = [nlo, nhi]
        set_ladder(con, code, rows, who)
        audit(con, who, "unit_avg_update", "bc_unit_rule", f"{code}|{u}", obs["current"],
              {"new": new, "observed": obs["observed"], "n_receipts": obs["n_receipts"], "n_pieces": obs["n_pieces"],
               "range": rng, "widened": widened})
    else:
        audit(con, who, "unit_avg_kept", "bc_unit_rule", f"{code}|{u}", obs["current"], {"observed": obs["observed"], "note": note})
    from datetime import datetime, timezone
    con.execute("INSERT INTO bc_unit_avg_decision(ingredient_code, unit, current_avg, observed_avg, n_receipts, n_pieces,"
                " window_from, window_to, decision, note, decided_by, decided_at, last_receipt_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (code, u, obs["current"], obs["observed"], obs["n_receipts"], obs["n_pieces"], obs["since"], obs["until"],
                 decision, note, who, datetime.now(timezone.utc).isoformat(timespec="seconds"), obs["newest_receipt_id"]))
    msg = (f"✓ ใช้ค่าเฉลี่ยใหม่ 1 {u} = {fmt_num(new)} {step['parent']}"
           + (f" และขยายช่วงเป็น {fmt_num(rng[0])}–{fmt_num(rng[1])} {step['parent']}" if widened else "")) \
        if decision == "applied" else "✓ คงค่าเดิมแล้ว"
    return {"decision": decision, "code": code, "unit": u, "current": obs["current"], "observed": obs["observed"], "new": new,
            "widened": widened, "range": rng, "message": msg}
