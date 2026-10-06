# -*- coding: utf-8 -*-
"""bom_center.units — the Material Converter (plan §4.2 M2).

Every quantity the center stores is in the ingredient's BASE unit (what the
recipes count in: กรัม / มิลลิลิตร / ชิ้น …). Everything people type or import
arrives in some other unit: the accounting report says กิโลกรัม or แพ็ค,ห่อ, the
kitchen says แท่ง or แผ่น. One function turns any of them into base units, and
says HOW it did so — or why it could not — so an import can show its working.

Resolution order for (code, qty, unit):
  1. the unit IS the base unit (after normalising spellings)        -> × 1
  2. a per-ingredient rule in bc_unit_rule (แท่ง = 15 กรัม)          -> × factor
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


class Converter:
    """Loaded once per request from bc_ingredient + bc_unit_rule."""

    def __init__(self, con):
        self.last_kind = "exact"          # "pack" after a generic pack-word conversion
        self.master = {}
        for code, base, recv, per in con.execute(
                "SELECT code, base_unit, recv_unit, qty_per_recv FROM bc_ingredient"):
            self.master[code] = (norm(base), str(recv or "").strip(), float(per) if per else None)
        self.rules = {}
        for code, unit, f in con.execute("SELECT ingredient_code, unit, factor_to_base FROM bc_unit_rule"):
            self.rules[(code, norm(unit))] = float(f)

    def base_unit(self, code: str) -> str:
        m = self.master.get(code)
        return m[0] if m else ""

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
            return self.rules[(code, u)], f"กฎของวัตถุดิบ ({u})"
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
        return None, f"แปลง {u} เป็น {base} ไม่ได้ กรุณาเพิ่มกฎหน่วย"

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
