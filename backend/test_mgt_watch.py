# -*- coding: utf-8 -*-
"""PR-PW (owner 2026-10-29) · ตรวจวันนี้ - the phone's second block (routers/mgt_watch.py), items 7-10, 12-14."""
import datetime as dt
import sqlite3

import conftest as _cf
from bom_center import issues as I
from bom_center import schema as S
from routers import mgt_watch as W

CS, CE = dt.date(2026, 6, 1), dt.date(2026, 6, 3)
PS, PE = dt.date(2026, 5, 1), dt.date(2026, 5, 3)


def _db(tmp_path):
    p = tmp_path / "watch.db"
    _cf._build_db(str(p))
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    S.ensure(c, commit=True)
    for d in I.DDL:
        c.execute(d)
    c.execute("INSERT OR IGNORE INTO bc_ingredient(code, name_th, kind, base_unit) VALUES('02-01-001', 'แซลมอน', 'raw', 'กรัม')")
    c.execute("INSERT OR IGNORE INTO bc_ingredient(code, name_th, kind, base_unit) VALUES('01-01-040', 'ข้าวญี่ปุ่น', 'raw', 'กรัม')")
    c.commit()
    ids = {r["branch_id"]: r["name"] for r in c.execute("SELECT branch_id, name FROM branches WHERE name!='ALL'")}
    return c, ids


def _slip(con, bid, day, g):
    cur = con.execute("INSERT INTO bc_issue_slip(branch_id, slip_date, status) VALUES(?,?,'confirmed')", (bid, day.isoformat()))
    con.execute("INSERT INTO bc_issue_line(slip_id, ingredient_code, qty_used, unit, qty_used_base, qty_waste_base) VALUES(?,?,?,'กรัม',?,0)",
                (cur.lastrowid, "02-01-001", g, g))


def test_portion_flags_the_branch_over_the_chain_grams_per_cover(tmp_path):
    con, ids = _db(tmp_path)
    # the fixture: 4 covers a day per branch on 1-3 June. Salmon issued: branch 1 160 g a day (40 g/head), branch 2 80 g (20 g/head)
    cs, ce = dt.date(2026, 6, 1), dt.date(2026, 6, 3)
    out = W.portion(con, ids, cs, ce)
    assert out["available"] is False                                   # no slips yet
    for i in range(7):                                                 # 7 slip days: the window is widened to hold them all
        d = dt.date(2026, 5, 28) + dt.timedelta(days=i)
        _slip(con, 1, d, 160); _slip(con, 2, d, 80)
        for bid in (1, 2):
            con.execute("INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,food_subtotal,drink_subtotal,other_subtotal,"
                        "discount,service_charge,total_inc_vat,source) VALUES(?,?,?,4,900,100,0,0,0,1070,'Easy')", (bid, f"W-{bid}-{d}", f"{d}T12:00:00"))
    con.commit()
    out = W.portion(con, ids, dt.date(2026, 5, 28), dt.date(2026, 6, 3))
    # covers = 7 x 4 of these bills + the fixture's 3 x 4 (1-3 June) = 40 a branch: branch 1 = 1,120 g / 40 = 28 g, branch 2 = 14 g,
    # the chain 1,680 / 80 = 21 g; branch 1 is +33 % (twice the 15 % band -> fix)
    assert out["available"] and out["level"] == "fix"
    g = out["groups"][0]
    assert g["slug"] == "salmon" and g["chain_per_cover"] == 21.0 and g["unit"] == "กรัม"
    assert out["rows"] == [{"branch": ids[1], "per_cover": 28.0, "vs_chain_pct": 33}]
    assert "แซลมอน" in out["line"] and ids[1] in out["line"] and out["line"].endswith("ตรวจการตักที่สาขา")


def test_forced_gaps_name_the_usual_item_a_branch_stopped_ordering(tmp_path, monkeypatch):
    con, ids = _db(tmp_path)
    from routers import ingredients as ING
    monkeypatch.setattr(ING, "_forced_order", lambda: {"file": "x", "items": [], "codes": {"02-01-001": {"code": "02-01-001", "name": "แซลมอน"}}})
    ce = dt.date(2026, 6, 3)

    def order(bid, day, code, units=2):
        cur = con.execute("INSERT INTO bc_order(branch_id, cycle_date, status) VALUES(?,?,'submitted')", (bid, day.isoformat()))
        con.execute("INSERT INTO bc_order_line(order_id, ingredient_code, confirmed_units) VALUES(?,?,?)", (cur.lastrowid, code, units))
    assert W.forced_gaps(con, ids, ce)["available"] is False          # no orders at all
    order(1, ce - dt.timedelta(days=40), "02-01-001")                  # branch 1 used to order salmon, not in the last 14 days
    order(1, ce - dt.timedelta(days=3), "01-01-040")                   # ... it still orders rice (not monitored)
    order(2, ce - dt.timedelta(days=5), "02-01-001")                   # branch 2 ordered salmon this week
    con.commit()
    out = W.forced_gaps(con, ids, ce)
    assert out["level"] == "watch" and out["rows"] == [{"branch": ids[1], "count": 1, "items": [{"code": "02-01-001", "name": "แซลมอน", "last": (ce - dt.timedelta(days=40)).isoformat()}]}]
    assert "แซลมอน" in out["line"] and ids[2] not in out["line"]


def test_voids_band_and_reprints_per_branch(tmp_path):
    con, ids = _db(tmp_path)
    # the fixture: branch 1 has 5 cancelled bills in June (pos_cancel_bill) against 3 real bills -> far over 1 %
    con.execute("INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,food_subtotal,drink_subtotal,other_subtotal,discount,"
                "service_charge,total_inc_vat,print_times,source) VALUES(2,'RP-1','2026-06-02T12:00:00',2,900,100,0,50,0,1070,3,'Easy')")
    con.commit()
    out = W.voids(con, ids, CS, CE, "st.source='Easy' AND b.name!='ALL'", {})
    assert out["level"] == "fix"
    assert out["rows"][0]["branch"] == ids[1] and out["rows"][0]["bills"] == 5 and out["rows"][0]["rate_pct"] == round(5 / 8 * 100, 2)
    assert out["reprints"] == [{"branch": ids[2], "bills": 1, "baht": 50}]
    assert "ยกเลิกบิล" in out["line"] and "พิมพ์ซ้ำ" in out["line"] and out["line"].endswith("ตรวจที่สาขา")


def test_recon_counts_signed_off_open_and_not_started_for_the_month_to_close(tmp_path):
    con, ids = _db(tmp_path)
    out = W.recon(con, ids, CS, CE)
    assert out["available"] is False                                   # no recon tables in the fixture
    con.executescript("""
      CREATE TABLE recon_sessions(session_id INTEGER PRIMARY KEY, branch_id INT, period_month TEXT, status TEXT);
      CREATE TABLE recon_category_result(session_id INT, category TEXT, variance REAL, status TEXT);
      INSERT INTO recon_sessions VALUES(1, 1, '2026-05', 'approved');
      INSERT INTO recon_sessions VALUES(2, 2, '2026-05', 'in_review');
      INSERT INTO recon_category_result VALUES(2, 'CASH', -1500, 'mismatch');
      INSERT INTO recon_category_result VALUES(2, 'QR', 200, 'mismatch');
      INSERT INTO recon_category_result VALUES(2, 'CREDIT', 0, 'matched');
    """)
    out = W.recon(con, ids, CS, CE)                                     # the month to close = May; 3 June is before the 5th
    assert out["month"] == "2026-05" and out["level"] == "watch" and out["done"] == 1 and out["total"] == 2
    assert out["rows"] == [{"branch": ids[2], "status": "in_review", "variance": 1700}]
    assert "ปิดแล้ว 1/2" in out["line"] and "กำหนดวันที่ 5" in out["line"]
    out2 = W.recon(con, ids, CS, dt.date(2026, 6, 9))                  # past the 5th: act today
    assert out2["level"] == "fix" and out2["past_due"] is True


def test_tier_mix_flags_the_branch_whose_premium_share_fell(tmp_path):
    con, ids = _db(tmp_path)
    con.execute("CREATE TABLE IF NOT EXISTS menu_fact_total(day TEXT, branch_id INT, tier TEXT, bills INT, pax REAL, cogs REAL, rev REAL)")
    rows = []
    for d in ("2026-06-01", "2026-06-02"):                              # now: branch 1 premium 30 %, branch 2 60 %
        rows += [(d, 1, "BUFFET799", 10, 70, 0, 70 * 799), (d, 1, "BUFFET999", 10, 30, 0, 30 * 999),
                 (d, 2, "BUFFET799", 10, 40, 0, 40 * 799), (d, 2, "BUFFET999", 10, 60, 0, 60 * 999)]
    for d in ("2026-05-01", "2026-05-02"):                              # before: both 50 %
        rows += [(d, 1, "BUFFET799", 10, 50, 0, 50 * 799), (d, 1, "BUFFET999", 10, 50, 0, 50 * 999),
                 (d, 2, "BUFFET799", 10, 50, 0, 50 * 799), (d, 2, "BUFFET999", 10, 50, 0, 50 * 999)]
    con.executemany("INSERT INTO menu_fact_total VALUES(?,?,?,?,?,?,?)", rows)
    con.commit()
    out = W.tier_mix(con, ids, CS, CE, PS, PE, True)
    assert out["tier"] == "BUFFET999" and out["level"] == "watch"
    assert out["rows"] == [{"branch": ids[1], "share_pct": 30.0, "share_prev_pct": 50.0, "delta_pp": -20.0, "pax_lost": 40}]
    assert "50%→30%" in out["line"] and out["line"].endswith("ดันการเสนอขายที่สาขา")
    assert W.tier_mix(con, ids, CS, CE, None, None, False)["available"] is False


def test_build_answers_every_item_and_sorts_by_level(tmp_path):
    con, ids = _db(tmp_path)
    out = W.build(con, cs=CS, ce=CE, ps=PS, pe=PE, has_prev=True, where="st.source='Easy' AND b.name!='ALL'", params={}, branch=None, group=None)
    keys = [it["key"] for it in out["items"]]
    assert sorted(keys) == ["forced", "newmenu", "portion", "purchases", "recon", "tiermix", "voids"]
    levels = [W.LEVELS[it["level"]] for it in out["items"]]
    assert levels == sorted(levels) and out["fix"] >= 1                 # the fixture's cancelled bills put voids first
    assert all(it["line"] for it in out["items"])


# ── 2026-10-31 (owner): one window for the cancelled bills and the bills; three windows on the card ─────────────────────────────────
WHERE = "st.source='Easy' AND b.name!='ALL'"


def _detail(con, rows):
    con.execute("CREATE TABLE IF NOT EXISTS pos_cancel_bill_detail(branch_id INT, period_month TEXT, sale_date TEXT, receipt_no TEXT, "
                "reason_id INT, amount REAL, cancel_user TEXT, discount REAL)")
    con.executemany("INSERT INTO pos_cancel_bill_detail VALUES(?,?,?,?,2,?,'cashier',0)",
                    [(bid, d[:7], d, f"C-{i}", amt) for i, (bid, d, amt) in enumerate(rows)])
    con.commit()


def test_voids_count_the_cancelled_bills_over_the_bills_window_only(tmp_path):
    con, ids = _db(tmp_path)
    # the detail table holds history from May (covers 1 June); branch 1: two voids on 2 June inside 1-3 June, one on 4 June = the day
    # after ce (the old monthly count took the whole month, 5 bills in pos_cancel_bill, and overstated the rate)
    _detail(con, [(1, "2026-05-20T12:00:00", 100), (1, "2026-06-02T12:00:00", 300), (1, "2026-06-02T19:00:00", 200),
                  (1, "2026-06-04T12:00:00", 900)])
    out = W.voids(con, ids, CS, CE, WHERE, {})
    assert out["source"] == "detail" and out["range"] == [CS.isoformat(), CE.isoformat()] and out["label"] == "1–3 มิ.ย."
    r = out["rows"][0]
    assert r["branch"] == ids[1] and r["bills"] == 2 and r["amount"] == 500          # 4 June is not counted
    assert r["rate_pct"] == round(2 / (3 + 2) * 100, 2)                               # 3 real bills on 1-3 June + the 2 voided
    # a window that starts on or before the detail table's first day falls back to the monthly table and says so
    old = W.voids(con, ids, dt.date(2026, 5, 1), dt.date(2026, 5, 3), WHERE, {})
    assert old["source"] == "month"


def test_voids_answer_three_windows_and_the_month_is_the_top_level(tmp_path):
    con, ids = _db(tmp_path)
    _detail(con, [(1, "2026-05-20T12:00:00", 100), (1, "2026-06-02T12:00:00", 300), (1, "2026-06-03T12:00:00", 200)])
    out = W.voids(con, ids, CS, CE, WHERE, {})
    win = out["windows"]
    assert set(win) == {"day", "week", "half", "month"}
    m = win["month"]
    for k in ("level", "rows", "reprints", "line", "range", "label", "source"):
        assert out[k] == m[k], k
    assert win["day"]["range"] == ["2026-06-03", "2026-06-03"]                        # the last finished day with bills (the fixture ends 3 June)
    assert win["week"]["range"] == ["2026-05-28", "2026-06-03"] and win["week"]["label"] == "28 พ.ค. – 3 มิ.ย."
    assert win["half"]["range"] == ["2026-05-20", "2026-06-03"]                        # 15 วัน (owner 2026-10-31)
    for k in ("rows", "reprints", "line", "level", "range", "label", "source"):
        assert k in win["day"] and k in win["week"], k


def test_the_day_window_gives_no_verdict_on_a_handful_of_bills(tmp_path):
    con, ids = _db(tmp_path)
    _detail(con, [(1, "2026-05-20T12:00:00", 100), (1, "2026-06-03T12:00:00", 300)])
    day = W.voids(con, ids, CS, CE, WHERE, {})["windows"]["day"]
    assert day["level"] == "none" and day["rows"] == [] and day.get("few") is True and day["line"].startswith("บิลน้อย")
    # 30 more bills on 3 June at branch 1: the same one void is now a rate with a verdict (1 / 32 = 3.1 % -> fix)
    for i in range(30):
        con.execute("INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,food_subtotal,drink_subtotal,other_subtotal,"
                    "discount,service_charge,total_inc_vat,source) VALUES(1,?,'2026-06-03T13:00:00',2,900,100,0,0,0,1070,'Easy')", (f"D-{i}",))
    con.commit()
    day = W.voids(con, ids, CS, CE, WHERE, {})["windows"]["day"]
    assert day["level"] == "fix" and day["rows"][0]["bills"] == 1 and day["rows"][0]["rate_pct"] == round(1 / 32 * 100, 2)


def test_the_range_label_says_the_dates():
    assert W.range_label(dt.date(2026, 10, 1), dt.date(2026, 10, 30)) == "1–30 ต.ค."
    assert W.range_label(dt.date(2026, 10, 30), dt.date(2026, 10, 30)) == "30 ต.ค."
    assert W.range_label(dt.date(2026, 9, 28), dt.date(2026, 10, 4)) == "28 ก.ย. – 4 ต.ค."
    assert W.range_label(dt.date(2026, 10, 1), dt.date(2026, 10, 30), th=False) == "1–30 Oct"


def test_the_phone_card_switches_the_voids_window_with_chips():
    from pathlib import Path
    from test_bom_center_batch2 import _fn
    mob = (Path(__file__).resolve().parent.parent / "js" / "09c-mobile.js").read_text(encoding="utf-8")
    assert "[['day', 'เมื่อวาน'], ['week', '7 วัน'], ['half', '15 วัน'], ['month', 'เดือนนี้']]" in mob
    assert "sessionStorage.setItem('msVoidWin', k)" in _fn(mob, "msSetVoidWin") and "msRenderWatch(_msRpt)" in _fn(mob, "msSetVoidWin")
    assert "sessionStorage.getItem('msVoidWin')" in _fn(mob, "_msVoidKey")
    rw = _fn(mob, "msRenderWatch")
    assert "W.items.map(_msVoidItem)" in rw and "_msVoidChips(it)" in rw and "it.label" in rw and "ยกเลิกนับทั้งเดือน" in rw
