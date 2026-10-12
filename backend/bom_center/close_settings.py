# -*- coding: utf-8 -*-
"""Batch 4b (3.3 and 3.4) - the two settings of the month close, kept in app_settings like the benchmark buffer:

  bc_reason_min_baht   a branch is asked for a variance reason only when the line is outside its class threshold AND the gap is at
                       least this many baht (default 500, 0 to 100,000; 0 = no baht floor, the old behaviour);
  bc_cycle_counts      1 = class-A items are counted every week and the rest at quarter end, the other months close on a partial
                       end count (stock.partial_close); 0 = every month ends with a full count, as before. Default 1.

Both are the centre's to edit (ตั้งค่า); every change is audited. Nothing is read from a request body without a range check.
"""
from .master import audit

KEY_MIN_BAHT = "bc_reason_min_baht"
KEY_CYCLE = "bc_cycle_counts"
MIN_BAHT_DEFAULT = 500.0
MIN_BAHT_MIN, MIN_BAHT_MAX = 0.0, 100000.0
CYCLE_DEFAULT = True


def _get(con, key):
    try:
        r = con.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    except Exception:
        return None
    return r[0] if r and r[0] not in (None, "") else None


def _put(con, key, value) -> None:
    con.execute("CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES(?, ?)", (key, value))


def reason_min_baht(con) -> float:
    raw = _get(con, KEY_MIN_BAHT)
    try:
        v = float(raw) if raw is not None else MIN_BAHT_DEFAULT
    except (TypeError, ValueError):
        return MIN_BAHT_DEFAULT
    return v if MIN_BAHT_MIN <= v <= MIN_BAHT_MAX else MIN_BAHT_DEFAULT


def cycle_counts_on(con) -> bool:
    raw = _get(con, KEY_CYCLE)
    if raw is None:
        return CYCLE_DEFAULT
    return str(raw).strip().lower() in ("1", "true", "on", "yes")


def settings(con) -> dict:
    return {"reason_min_baht": reason_min_baht(con), "reason_min_baht_default": MIN_BAHT_DEFAULT,
            "reason_min_baht_min": MIN_BAHT_MIN, "reason_min_baht_max": MIN_BAHT_MAX,
            "cycle_counts": cycle_counts_on(con), "cycle_counts_default": CYCLE_DEFAULT}


def save(con, who: str, reason_min=None, cycle=None) -> dict:
    """Save either or both (None = leave it). Audited with the old and the new value."""
    if reason_min is not None:
        try:
            v = float(str(reason_min).replace(",", "").strip())
        except (TypeError, ValueError):
            raise ValueError("เกณฑ์ผลต่างเป็นบาทต้องเป็นตัวเลข")
        if not (MIN_BAHT_MIN <= v <= MIN_BAHT_MAX):
            raise ValueError(f"เกณฑ์ผลต่างเป็นบาทต้องอยู่ระหว่าง {MIN_BAHT_MIN:,.0f} ถึง {MIN_BAHT_MAX:,.0f}")
        old = reason_min_baht(con)
        _put(con, KEY_MIN_BAHT, f"{v:g}")
        audit(con, who, "reason_min_baht", "app_settings", KEY_MIN_BAHT, f"{old:g}", f"{v:g}")
    if cycle is not None:
        if not isinstance(cycle, bool):
            raise ValueError("การตรวจนับหมุนเวียนต้องเป็นเปิดหรือปิด")
        old = cycle_counts_on(con)
        _put(con, KEY_CYCLE, "1" if cycle else "0")
        audit(con, who, "cycle_counts", "app_settings", KEY_CYCLE, "1" if old else "0", "1" if cycle else "0")
    return settings(con)
