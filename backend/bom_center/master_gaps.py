# -*- coding: utf-8 -*-
"""PR-B4a (1.5) - the master data the order suggestion relies on and that is still blank.

The suggestion reads three fields of the ingredient register and the register has them nearly empty (2026-10-12 review: shelf life
501 of 501 blank, minimum order 327 blank, loss 0 on 452): without a shelf life the order cannot be capped at it, without a minimum
order a pack is not rounded the way the supplier sells it, and a loss of 0 on meat or vegetables under-states what has to be bought.
This module only COUNTS and RANKS them for the analyst's หน้างาน card - it never fills a value in (no shelf life is invented):

    fresh_no_shelf   fresh / chilled items (the chilled storage zone) with no shelf_days
    no_moq           bought items with no minimum order (moq)
    loss_zero        meat, seafood and vegetables whose loss % is 0 or blank

"Bought items" are the register's raw and consumable items; an item the kitchen makes (kind 'prepared') is not ordered, so it has no
minimum order and no shelf life to cap an order with. Each list is ranked by the ฿ the item is bought for (`spend`): the branch
purchases of the price files (bc_ingredient_price_branch, the latest 3 months) or, when larger, the central orders sent in the last
90 days at the price in force - an item nobody has bought is ranked last, by name.
"""
from datetime import date, timedelta

from .clock import today as bkk_today

TOP = 6                                    # items named on the card for each gap
SPEND_DAYS = 90                            # the central orders read for the ranking
SPEND_MONTHS = 3                           # … and the price-file months

PROTEIN_WORDS = ("เนื้อ", "หมู", "ไก่", "ปลา", "กุ้ง", "แซลมอน", "หมึก", "ปู", "หอย", "เบค่อน", "ไส้กรอก", "ปีก", "สันคอ", "สามชั้น",
                 "ซี่โครง", "ไข่", "salmon", "beef", "pork", "chicken", "shrimp", "squid")
PRODUCE_WORDS = ("ผัก", "กะหล่ำ", "ต้นหอม", "หอม", "กระเทียม", "พริก", "มะนาว", "ขิง", "เห็ด", "แครอท", "แตง", "ข้าวโพด", "ผักกาด",
                 "cabbage", "lettuce", "mushroom", "onion", "carrot")
PROTEIN_CATEGORY = ("protein", "ของสด", "เนื้อสัตว์")
PRODUCE_CATEGORY = ("ผักสด", "veg", "ผัก")
PROTEIN_PREFIX = ("03-", "13-01", "13-02", "13-03")        # accounting families: meat / seafood / processed meat (stock._guess)
PRODUCE_PREFIX = ("V-", "VY", "FV")


def is_protein_or_produce(code: str, name: str, family: str, category: str) -> str:
    """'protein' | 'produce' | '' - a grouping from the accounting code, the register category and the name, for the card only."""
    c, n, fam, cat = code or "", (name or "").lower(), family or "", (category or "").strip().lower()
    if cat in PRODUCE_CATEGORY or fam in ("V", "VY") or c.startswith(PRODUCE_PREFIX) or any(w in n for w in PRODUCE_WORDS):
        return "produce"
    if cat in PROTEIN_CATEGORY or c.startswith(PROTEIN_PREFIX) or any(w in n for w in PROTEIN_WORDS):
        return "protein"
    return ""


def spend_by_code(con, today: date = None) -> dict:
    """{code: ฿ bought} - see the module doc. The price file's branch rows (latest SPEND_MONTHS months) against the central orders of
    the last SPEND_DAYS days (processed units × units in a pack × the price in force); the larger of the two."""
    today = today or bkk_today()
    out = {}
    try:
        months = [m for (m,) in con.execute("SELECT DISTINCT month FROM bc_ingredient_price_branch ORDER BY month DESC LIMIT ?",
                                            (SPEND_MONTHS,))]
        if months:
            q = ",".join("?" * len(months))
            for c, v in con.execute("SELECT ingredient_code, SUM(COALESCE(value_bought, qty_bought * price_per_base, 0))"
                                    f" FROM bc_ingredient_price_branch WHERE month IN ({q}) GROUP BY ingredient_code", months):
                out[c] = float(v or 0.0)
    except Exception:
        out = {}
    try:
        from .prices import price_for
        per = {c: p for c, p in con.execute("SELECT code, qty_per_recv FROM bc_ingredient")}
        since = (today - timedelta(days=SPEND_DAYS)).isoformat()
        month = f"{today.year:04d}-{today.month:02d}"
        orders = {}
        for code, unit, u in con.execute(
                "SELECT l.ingredient_code, l.order_unit, COALESCE(l.processed_units, l.confirmed_units, 0) FROM bc_order o"
                " JOIN bc_order_line l USING(order_id) WHERE o.status IN ('processed','received') AND o.cycle_date>=?", (since,)):
            if not u:
                continue
            f = 1.0 if unit == "base" else (per.get(code) or 0.0)
            p = price_for(con, code, month)
            if f and p:
                orders[code] = orders.get(code, 0.0) + float(u) * f * p[0]
        for c, v in orders.items():
            out[c] = max(out.get(c, 0.0), v)
    except Exception:
        pass
    return out


def _zone(code, name, family, kind, zone) -> str:
    if zone:
        return zone
    from .stock import guess_zone
    return guess_zone(code, name, family, kind)


def row_gaps(r: dict) -> list:
    """The gap keys one register row has ('fresh_no_shelf', 'no_moq', 'loss_zero'); [] for a made item (kind 'prepared') or an inactive one.
    `r` carries code, name_th, family, kind, category, storage_zone, shelf_days, moq, loss_pct (the register list's row)."""
    if r.get("kind") not in ("raw", "consumable") or not (r.get("active") is None or r.get("active")):
        return []
    out = []
    if not (r.get("shelf_days") or 0) > 0 and _zone(r.get("code"), r.get("name_th"), r.get("family"), r.get("kind"), r.get("storage_zone")) == "chilled":
        out.append("fresh_no_shelf")
    if not (r.get("moq") or 0) > 0:
        out.append("no_moq")
    if not (r.get("loss_pct") or 0) > 0 and is_protein_or_produce(r.get("code"), r.get("name_th"), r.get("family"), r.get("category")):
        out.append("loss_zero")
    return out


def gaps(con, today: date = None, top: int = TOP) -> dict:
    """-> {counts: {fresh_no_shelf, no_moq, loss_zero, total}, lists: {key: {count, items: [{code, name, spend}]}}, active}.
    Only active items; see the module doc for the three definitions."""
    spend = spend_by_code(con, today)
    rows = con.execute(
        "SELECT code, name_th, COALESCE(NULLIF(TRIM(short_name), ''), name_th), family, kind, category, storage_zone, shelf_days, moq,"
        " loss_pct FROM bc_ingredient WHERE COALESCE(active, 1)=1 AND kind IN ('raw', 'consumable')").fetchall()
    lists = {"fresh_no_shelf": [], "no_moq": [], "loss_zero": []}
    for code, name, short, family, kind, category, zone, shelf, moq, loss in rows:
        entry = {"code": code, "name": short or name or code, "spend": round(spend.get(code, 0.0), 2)}
        if (shelf is None or shelf <= 0) and _zone(code, name, family, kind, zone) == "chilled":
            lists["fresh_no_shelf"].append(entry)
        if moq is None or moq <= 0:
            lists["no_moq"].append(entry)
        if (loss is None or loss <= 0) and is_protein_or_produce(code, name, family, category):
            lists["loss_zero"].append(entry)
    out = {"counts": {}, "lists": {}}
    for k, items in lists.items():
        items.sort(key=lambda e: (-e["spend"], e["name"] or "", e["code"]))
        out["counts"][k] = len(items)
        out["lists"][k] = {"count": len(items), "items": items[:top]}
    out["counts"]["total"] = sum(out["counts"][k] for k in lists)
    out["counts"]["items"] = len({e["code"] for items in lists.values() for e in items})        # an item with two gaps is one item
    out["active"] = len(rows)
    return out
