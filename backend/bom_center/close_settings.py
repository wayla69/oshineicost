# -*- coding: utf-8 -*-
"""Batch 4b (3.3 and 3.4) - the two settings of the month close, kept in app_settings like the benchmark buffer:

  bc_reason_min_baht   a branch is asked for a variance reason only when the line is outside its class threshold AND the gap is at
                       least this many baht (default 500, 0 to 100,000; 0 = no baht floor, the old behaviour);
  bc_cycle_counts      1 = the class-A items may be counted every week as a mid-month check ("ตรวจนับหมุนเวียน (ไม่ใช่การปิดงวด)"): it
                       feeds the system stock and NEVER closes a period. Default 0. Owner 2026-10-13: every month-end close is a
                       full count of every item, whatever this switch says.

Both are the centre's to edit (ตั้งค่า); every change is audited. Nothing is read from a request body without a range check.

PR-S1 (owner 2026-10-22, docs/STOCK_FIRST_PLAN.md): a third switch, the system mode, sits beside them:

  bc_mode              stock = stock management only - every recipe page, recipe task and recipe reminder is hidden (default when the key
                       is absent); full = the whole app with the recipes. Nothing is deleted either way. Management alone changes it.
"""
from .master import audit

KEY_MIN_BAHT = "bc_reason_min_baht"
KEY_CYCLE = "bc_cycle_counts"
MIN_BAHT_DEFAULT = 500.0
MIN_BAHT_MIN, MIN_BAHT_MAX = 0.0, 100000.0
CYCLE_DEFAULT = False
KEY_MODE = "bc_mode"
MODE_STOCK, MODE_FULL = "stock", "full"
MODES = (MODE_STOCK, MODE_FULL)
MODE_DEFAULT = MODE_STOCK
MODE_LABEL_TH = {MODE_STOCK: "สต๊อกก่อน (ปิดส่วนสูตร)", MODE_FULL: "เต็มระบบ (รวมสูตร)"}


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


def mode(con) -> str:
    """The system mode: `stock` (the default, also for an unreadable value) or `full`."""
    raw = _get(con, KEY_MODE)
    v = str(raw).strip().lower() if raw is not None else MODE_DEFAULT
    return v if v in MODES else MODE_DEFAULT


def mode_info(con) -> dict:
    m = mode(con)
    return {"mode": m, "label_th": MODE_LABEL_TH[m]}


def stock_mode(con) -> bool:
    return mode(con) == MODE_STOCK


def set_mode(con, who: str, value) -> dict:
    """Save the mode (`stock` or `full`), audited with the old and the new value."""
    v = value.strip().lower() if isinstance(value, str) else None
    if v not in MODES:
        raise ValueError("โหมดต้องเป็น stock หรือ full")
    old = mode(con)
    _put(con, KEY_MODE, v)
    audit(con, who, "bc_mode", "app_settings", KEY_MODE, old, v)
    return mode_info(con)
