"""Regression tests for the money-matching engine fixes (recon-matcher review,
2026-10). One test (or two) per finding; the numbering follows the review list.

Run:  pytest recon/tests/test_fix_matcher.py
"""
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # repo root → recon.*
sys.path.insert(0, str(Path(__file__).resolve().parent))      # fixture helpers
import recon.main as main                                     # noqa: E402
import recon.core as core                                     # noqa: E402
from recon.seed_payment_map import seed_payment_map           # noqa: E402
from test_recon import (                                      # noqa: E402
    _make_db, DEMO_POS, _add_lineman_net, _seed_point_pos,
)


@pytest.fixture
def client(tmp_path):
    core._DB_PATH = main._DB_PATH = _make_db(tmp_path, DEMO_POS)
    os.environ.pop("OSHINEI_SEED_TOKEN", None)
    with TestClient(main.app) as cl:
        yield cl


# ── helpers ───────────────────────────────────────────────────────────────────

def _pos_day(db, rows, branch_id=4):
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day"
              "(branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT DEFAULT 0)")
    c.executemany("INSERT INTO pos_payment_day VALUES(?,?,?,?,1)",
                  [(branch_id, d, code, amt) for d, code, amt in rows])
    c.commit()
    seed_payment_map(c, "pos_payment_day")                    # map codes new to the fixture
    c.close()


def _set_pos_month(db, rows, month="2026-06"):
    c = sqlite3.connect(db)
    c.execute("DELETE FROM pos_payment_month WHERE branch_id=4 AND period_month=?", (month,))
    c.executemany("INSERT INTO pos_payment_month VALUES(4,?,?,?,0)", [(month, a, b) for a, b in rows])
    c.commit()
    seed_payment_map(c)                                       # map codes new to the fixture
    c.close()


def _bank(db, provider, channel, credit, date, status="unmatched"):
    c = sqlite3.connect(db)
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('bank',4,?,'f',?,0,'confirmed')", (provider, date[:7])).lastrowid
    lid = c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,match_status)"
                    " VALUES(?,4,?,?,?,?)", (up, date, channel, credit, status)).lastrowid
    c.commit(); c.close()
    return lid


def _cc(db, date, acq, gross, net, mid=None):
    c = sqlite3.connect(db)
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('credit_card',4,?,'f',?,0,'confirmed')", (acq, date[:7])).lastrowid
    lid = c.execute("INSERT INTO recon_cc_lines(upload_id,branch_id,settlement_date,acquirer,gross_amount,"
                    "mdr_fee,vat_amount,net_amount,merchant_id) VALUES(?,4,?,?,?,?,0,?,?)",
                    (up, date, acq, gross, gross - net, net, mid)).lastrowid
    c.commit(); c.close()
    return lid


def _grab(db, day, net):
    c = sqlite3.connect(db)
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('grab',4,'GRAB','g',?,0,'confirmed')", (day[:7],)).lastrowid
    c.execute("INSERT INTO recon_grab_lines(upload_id,branch_id,order_date,order_status,food_amount,"
              "commission_amt,vat_amount,promo_deduct) VALUES(?,4,?,'Completed',?,0,0,0)", (up, day, net))
    c.commit(); c.close()


def _point_mid(db, acq="KTCPOINT", mid="662"):
    c = sqlite3.connect(db)
    c.execute("INSERT INTO recon_acquirer_mid(branch_id,acquirer,merchant_id,terminal_id,active)"
              " VALUES(4,?,?,'',1)", (acq, mid))
    c.commit(); c.close()


def _session(conn, month="2026-06"):
    sid = conn.execute("INSERT INTO recon_sessions(branch_id,period_month,status) VALUES(4,?,'draft')",
                       (month,)).lastrowid
    conn.commit()
    return sid


# ── 1. run_auto_match on a COUPON lane waiting for the points summary ─────────

def test_01_run_match_coupon_waiting_doc_no_keyerror(client):
    from recon.matcher import run_auto_match, category_reconciliation
    _seed_point_pos(main._DB_PATH, "แต้มกรุงศรี 1000 ลด 100", 7, 500)
    with main.get_db() as conn:
        rows, _ = category_reconciliation(conn, 4, "2026-06")
        assert next(r for r in rows if r["category"] == "COUPON")["status"] == "waiting_doc"
        sid = _session(conn)
        run_auto_match(conn, sid, 4, "2026-06")          # used to raise KeyError
        d = conn.execute("SELECT disc_type, resolution FROM recon_discrepancies "
                         "WHERE session_id=? AND category='COUPON'", (sid,)).fetchall()
    assert [(x["disc_type"], x["resolution"]) for x in d] == [("unmatched_pos", "pending")]


# ── 2. a reconciled lane must not overwrite manual / excluded decisions ───────

def test_02_run_match_keeps_manual_lines(client):
    from recon.matcher import run_auto_match
    db = main._DB_PATH
    _bank(db, "kbank", "QR", 149000, "2026-06-10")
    b = _bank(db, "kbank", "QR", 1000, "2026-06-11", status="manual")
    with main.get_db() as conn:
        sid = _session(conn)
        res = run_auto_match(conn, sid, 4, "2026-06")
        assert next(r for r in res["categories"] if r["category"] == "QR")["status"] == "matched"
        st = conn.execute("SELECT match_status FROM recon_bank_lines WHERE id=?", (b,)).fetchone()[0]
    assert st == "manual"


# ── 3. resolved discrepancies are not re-created; ready-to-close is closable ──

def test_03_rerun_does_not_recreate_resolved_and_month_closes(client):
    sid = client.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json()["session_id"]
    client.post(f"/api/sessions/{sid}/run-match")
    ds = client.get(f"/api/sessions/{sid}/discrepancies").json()
    assert ds
    client.post(f"/api/sessions/{sid}/discrepancies/bulk-resolve",
                json={"ids": [d["id"] for d in ds], "resolution": "accepted", "resolved_by": "x"})
    # the frontend's ปิดเดือน: run-match (snapshot) then approve
    client.post(f"/api/sessions/{sid}/run-match")
    allds = client.get(f"/api/sessions/{sid}/discrepancies", params={"resolution": "all"}).json()
    assert len(allds) == len(ds)                         # no duplicates piled up
    assert not [d for d in allds if d["resolution"] == "pending"]
    r = client.patch(f"/api/sessions/{sid}/status", data={"status": "approved", "approved_by": "Boss"})
    assert r.status_code == 200, r.text


def test_03_resolved_discrepancy_reopens_when_variance_moves(client):
    """A resolution covers the variance it was made on — a later upload that moves
    the lane's variance beyond tolerance must surface a fresh pending row."""
    db = main._DB_PATH
    sid = client.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json()["session_id"]
    client.post(f"/api/sessions/{sid}/run-match")
    ds = client.get(f"/api/sessions/{sid}/discrepancies").json()
    client.post(f"/api/sessions/{sid}/discrepancies/bulk-resolve",
                json={"ids": [d["id"] for d in ds], "resolution": "accepted", "resolved_by": "x"})
    _bank(db, "kbank", "QR", 60000, "2026-06-10")          # QR lane variance changes
    client.post(f"/api/sessions/{sid}/run-match")
    pend = client.get(f"/api/sessions/{sid}/discrepancies").json()
    assert [(d["category"], d["bank"]) for d in pend] == [("QR", "KBANK")]


def test_03_coupon_no_source_does_not_block_close(tmp_path):
    """COUPON with no issuer points is informational on the branch page (no todo),
    so run-match must not plant a pending blocker for it."""
    core._DB_PATH = main._DB_PATH = _make_db(tmp_path, [("Coupon", 1000)])
    os.environ.pop("OSHINEI_SEED_TOKEN", None)
    with TestClient(main.app) as c:
        r = c.get("/api/v2/reconcile", params={"branch_id": 4, "month": "2026-06"}).json()
        assert r["can_close"] and not r["todo"]
        sid = c.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json()["session_id"]
        c.post(f"/api/sessions/{sid}/run-match")
        r = c.patch(f"/api/sessions/{sid}/status", data={"status": "approved", "approved_by": "Boss"})
        assert r.status_code == 200, r.text


def test_03_day_acked_channel_closes(tmp_path):
    """A channel the branch page calls clear because its differing days were
    acknowledged (reconcile BY DATE) must not re-block approval at run-match."""
    core._DB_PATH = main._DB_PATH = _make_db(tmp_path, [("KNNQR", 150000)])
    os.environ.pop("OSHINEI_SEED_TOKEN", None)
    with TestClient(main.app) as c:
        db = main._DB_PATH
        _pos_day(db, [("2026-06-10", "KNNQR", 50000), ("2026-06-11", "KNNQR", 50000),
                      ("2026-06-12", "KNNQR", 50000)])
        _bank(db, "kbank", "QR", 40000, "2026-06-10")
        _bank(db, "kbank", "QR", 50000, "2026-06-11")
        _bank(db, "kbank", "QR", 50000, "2026-06-12")
        assert c.post("/api/day-ack", json={
            "branch_id": 4, "period_month": "2026-06", "category": "QR", "day": "2026-06-10",
            "variance": -10000, "note": "ธนาคารตัดยอดผิดวัน ตรวจกับสลิปแล้ว"}).status_code == 200
        r = c.get("/api/v2/reconcile", params={"branch_id": 4, "month": "2026-06"}).json()
        assert r["can_close"], r["todo"]
        sid = c.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json()["session_id"]
        c.post(f"/api/sessions/{sid}/run-match")
        ds = c.get(f"/api/sessions/{sid}/discrepancies", params={"resolution": "all"}).json()
        assert [(d["category"], d["resolution"]) for d in ds] == [("QR", "accepted")]
        assert "ธนาคารตัดยอดผิดวัน" in ds[0]["resolution_note"]
        r = c.patch(f"/api/sessions/{sid}/status", data={"status": "approved", "approved_by": "Boss"})
        assert r.status_code == 200, r.text


# ── 4. daily board with a manual match ────────────────────────────────────────

def test_04_daily_board_with_manual_match(client):
    db = main._DB_PATH
    _pos_day(db, [("2026-06-10", "KNNQR", 1000)])
    lid = _bank(db, "kbank", "QR", 400, "2026-06-10")
    body = {"branch_id": 4, "period_month": "2026-06", "category": "QR", "bank": "KBANK",
            "pos_days": [{"day": "2026-06-10", "amount": 400}],
            "ext": [{"table": "recon_bank_lines", "id": lid, "amount": 400}], "note": "ติ๊กจับคู่มือ"}
    assert client.post("/api/manual", json=body).status_code == 200
    r = client.get("/api/daily-board", params={"day": "2026-06-10"})
    assert r.status_code == 200, r.text
    cell = next(b for b in r.json()["branches"] if b["branch_id"] == 4)["cells"]["QR"]
    assert cell["manual"] == 400
    assert client.get("/api/daily-report", params={"day": "2026-06-10"}).status_code == 200


# ── 5. cash: a deposit last month already claimed is not claimed again ────────

def test_05_cash_month_boundary_not_double_claimed(client):
    from recon.matcher import cash_deposit_matching
    db = main._DB_PATH
    _pos_day(db, [("2026-06-30", "Cash", 10000), ("2026-07-01", "Cash", 10040)])
    x = _bank(db, "ktb", "CASH_DEP", 10000, "2026-07-01")
    y = _bank(db, "ktb", "CASH_DEP", 10040, "2026-07-02")
    with main.get_db() as conn:
        june = cash_deposit_matching(conn, 4, "2026-06")
        july = cash_deposit_matching(conn, 4, "2026-07")
    assert june["consumed_ids"] == {x}
    assert july["consumed_ids"] == {y}                   # used to re-claim x (within ฿50)
    assert july["groups"][0]["days"] == [{"day": "2026-07-01", "pos": 10040}]


def test_05_cash_skips_credit_last_month_payout_claimed(client):
    from recon.matcher import cash_deposit_matching
    db = main._DB_PATH
    _grab(db, "2026-06-30", 5000)
    t = _bank(db, "kbank", "TRANSFER", 5000, "2026-07-01")   # June's Grab payout
    _pos_day(db, [("2026-07-01", "Cash", 5000)], )
    with main.get_db() as conn:
        july = cash_deposit_matching(conn, 4, "2026-07")
    assert t not in july["consumed_ids"]


# ── 6. payout_reconciliation counts this month's carried-out payout ───────────

def test_06_payout_reconciliation_symmetric_carry(client):
    from recon.matcher import payout_reconciliation
    db = main._DB_PATH
    _grab(db, "2026-05-31", 1000); _bank(db, "kbank", "GRAB_PAYOUT", 1000, "2026-06-01")
    _grab(db, "2026-06-15", 1000); _bank(db, "kbank", "GRAB_PAYOUT", 1000, "2026-06-16")
    _grab(db, "2026-06-30", 1000); _bank(db, "kbank", "GRAB_PAYOUT", 1000, "2026-07-01")
    with main.get_db() as conn:
        g = {r["category"]: r for r in payout_reconciliation(conn, 4, "2026-06")}["GRAB"]
    assert g["report_net"] == 2000 and g["bank_payout"] == 2000
    assert g["status"] == "matched"


# ── 7. card: month-end settlement credited next month ─────────────────────────

def test_07_credit_month_end_settlement_matched_once(client):
    from recon.matcher import credit_three_way
    db = main._DB_PATH
    _set_pos_month(db, [("Credit กสิกรไทย", 20000)])
    _cc(db, "2026-06-15", "KBANK", 12000, 12000)
    _bank(db, "kbank", "CC_SETTLE", 12000, "2026-06-16")
    _cc(db, "2026-06-30", "KBANK", 8000, 8000)
    nxt = _bank(db, "kbank", "CC_SETTLE", 8000, "2026-07-01")     # T+1 → next month
    _cc(db, "2026-07-01", "KBANK", 8000, 8000)                    # July's own (not yet paid)
    with main.get_db() as conn:
        june = next(x for x in credit_three_way(conn, 4, "2026-06")["rows"] if x["acquirer"] == "KBANK")
        july_c3 = credit_three_way(conn, 4, "2026-07")
    assert june["bank_credit"] == 20000 and june["bank_leg"] == "matched"
    july = next(x for x in july_c3["rows"] if x["acquirer"] == "KBANK")
    assert nxt not in july_c3["bank_line_ids"]          # June's money is not July's
    assert july["bank_leg"] == "awaiting"


# ── 8. KTC Point rides inside the KTC deposit (bank leg) ──────────────────────

def test_08_ktc_point_in_bank_leg_not_pos_leg(client):
    from recon.matcher import credit_three_way
    db = main._DB_PATH
    _point_mid(db)
    _cc(db, "2026-06-03", "KTC", 5000, 5000, "661")
    _cc(db, "2026-06-03", "KTC", 600, 600, "662")
    _bank(db, "ktb", "CC_SETTLE", 5600, "2026-06-04")
    with main.get_db() as conn:
        r = next(x for x in credit_three_way(conn, 4, "2026-06")["rows"] if x["acquirer"] == "KTC")
    assert r["settle_gross"] == 5000                     # POS leg: card sales only
    assert r["bank_credit"] == 5600 and r["bank_leg"] == "matched"


# ── 9. daybook: point settlements are not CREDIT ──────────────────────────────

def test_09_daybook_point_rows_not_credit(client):
    from recon.core import _daily_finance_data
    db = main._DB_PATH
    _point_mid(db)
    _cc(db, "2026-06-15", "KTC", 300, 300, "662")
    _cc(db, "2026-06-15", "KTC", 2000, 2000, "661")
    with main.get_db() as conn:
        f = _daily_finance_data(conn, 4, "2026-06")
    d = next(x for x in f["days"] if x["day"] == "2026-06-15")
    cr = next(r for r in d["channels"] if r["ch"] == "CREDIT")
    assert cr["gross"] == 2000


# ── 10. one bank credit, one payout stream ────────────────────────────────────

def test_10_one_credit_one_payout_stream(client):
    from recon.matcher import payout_matching
    db = main._DB_PATH
    _grab(db, "2026-06-10", 1000)
    _add_lineman_net(db, "2026-06-10", 1000)
    _bank(db, "kbank", "TRANSFER", 1000, "2026-06-11")
    with main.get_db() as conn:
        pm = payout_matching(conn, 4, "2026-06")
    claims = [p["credit_id"] for cat in ("GRAB", "LINEMAN", "CREDIT", "BAYPOINT")
              for p in pm[cat]["payouts"] if p["status"] == "matched"]
    assert len(claims) == len(set(claims)) == 1


def test_10_tagged_payout_preferred_over_transfer(client):
    from recon.matcher import payout_matching
    db = main._DB_PATH
    _grab(db, "2026-06-10", 1000)
    _grab(db, "2026-06-11", 1000)
    t = _bank(db, "kbank", "TRANSFER", 1000, "2026-06-11")       # a customer transfer
    g = _bank(db, "kbank", "GRAB_PAYOUT", 2000, "2026-06-12")
    with main.get_db() as conn:
        gr = payout_matching(conn, 4, "2026-06")["GRAB"]
    got = [p["credit_id"] for p in gr["payouts"] if p["status"] == "matched"]
    assert got == [g] and t not in got


def test_10_cash_deposit_not_a_payout(client):
    from recon.matcher import payout_matching, cash_deposit_matching
    db = main._DB_PATH
    _pos_day(db, [("2026-06-10", "Cash", 1000)])
    _grab(db, "2026-06-10", 1000)
    t = _bank(db, "kbank", "TRANSFER", 1000, "2026-06-11")       # cash banked as รับโอน
    with main.get_db() as conn:
        assert t in cash_deposit_matching(conn, 4, "2026-06")["consumed_ids"]
        gr = payout_matching(conn, 4, "2026-06")["GRAB"]
    assert t not in [p["credit_id"] for p in gr["payouts"]]


# ── 11. LINE MAN: the tagged payout, not a customer transfer ──────────────────

def test_11_lineman_skips_small_customer_transfer(client):
    from recon.matcher import payout_matching
    db = main._DB_PATH
    _add_lineman_net(db, "2026-06-10", 260)
    _add_lineman_net(db, "2026-06-11", 270)
    t = _bank(db, "kbank", "TRANSFER", 350, "2026-06-12")
    p = _bank(db, "kbank", "LINEMAN_PAYOUT", 530, "2026-06-12")
    with main.get_db() as conn:
        lm = payout_matching(conn, 4, "2026-06")["LINEMAN"]
    got = [(x["credit_id"], [d["day"] for d in x["days"]]) for x in lm["payouts"] if x["status"] == "matched"]
    assert got == [(p, ["2026-06-10", "2026-06-11"])]
    assert t not in [x["credit_id"] for x in lm["payouts"]]


# ── 12. a card settlement posted as รับโอนเงิน is not also a TRANSFER ──────────

def test_12_transfer_lane_drops_card_settlement(client):
    from recon.matcher import credit_three_way, category_reconciliation
    db = main._DB_PATH
    _set_pos_month(db, [("Credit กสิกรไทย", 8000), ("โอน Kbank", 3000)])
    _cc(db, "2026-06-10", "KBANK", 8000, 8000)
    _bank(db, "kbank", "TRANSFER", 3000, "2026-06-10")          # real customer transfer
    t2 = _bank(db, "kbank", "TRANSFER", 8000, "2026-06-11")     # card settlement as รับโอนเงิน
    with main.get_db() as conn:
        c3 = credit_three_way(conn, 4, "2026-06")
        rows, _ = category_reconciliation(conn, 4, "2026-06")
    assert t2 in c3["bank_line_ids"]
    tr = next(r for r in rows if r["category"] == "TRANSFER" and r["bank"] == "KBANK")
    assert tr["ext_amount"] == 3000 and tr["status"] == "matched"


def test_12_daily_transfer_lane_drops_payout_credit(client):
    from recon.matcher import daily_reconciliation
    db = main._DB_PATH
    _pos_day(db, [("2026-06-10", "โอน Kbank", 3000), ("2026-06-11", "โอน Kbank", 0.0)])
    _grab(db, "2026-06-10", 5000)
    _bank(db, "kbank", "TRANSFER", 3000, "2026-06-10")
    _bank(db, "kbank", "TRANSFER", 5000, "2026-06-11")          # Grab payout as รับโอนเงิน
    with main.get_db() as conn:
        d = daily_reconciliation(conn, 4, "2026-06")
    lanes = [(x["day"], l["ext"]) for x in d["days"] for l in x["lanes"]
             if l["category"] == "TRANSFER"]
    assert ("2026-06-11", 5000) not in lanes


# ── 13. daily views split point MIDs out of CREDIT ────────────────────────────

def test_13_daily_views_exclude_point_rows(client):
    from recon.matcher import daily_reconciliation, lane_lines, lane_ext_lines, daily_board
    db = main._DB_PATH
    _point_mid(db)
    _pos_day(db, [("2026-06-15", "Credit กรุงไทย", 5000)])
    _cc(db, "2026-06-15", "KTB", 5000, 5000, "661")
    _cc(db, "2026-06-15", "KTB", 300, 300, "662")
    with main.get_db() as conn:
        d = daily_reconciliation(conn, 4, "2026-06")
        ll = lane_lines(conn, 4, "2026-06", "CREDIT", "KTB")
        le = lane_ext_lines(conn, 4, "2026-06", "CREDIT", "KTB")
        board = daily_board(conn, "2026-06-15")
    day = next(x for x in d["days"] if x["day"] == "2026-06-15")
    cr = [(l["bank"], l["ext"], l["status"]) for l in day["lanes"] if l["category"] == "CREDIT"]
    assert cr == [("KTB", 5000, "matched")]
    assert [x["amount"] for x in ll] == [5000] and [x["amount"] for x in le] == [5000]
    assert not [c for c in board["categories"] if "POINT" in c]
    cell = next(b for b in board["branches"] if b["branch_id"] == 4)["cells"]["CREDIT:KTB"]
    assert cell["ext"] == 5000 and cell["status"] == "matched"


def test_13_upload_stats_point_rows_follow_coupon_lane(client):
    from recon.matcher import upload_match_stats
    db = main._DB_PATH
    _point_mid(db)
    _seed_point_pos(db, "แลกคะแนน KTC 699 คะแนนลด 80 บาท", 1, 300)
    lid = _cc(db, "2026-06-15", "KTC", 300, 300, "662")
    with main.get_db() as conn:
        uid = conn.execute("SELECT upload_id FROM recon_cc_lines WHERE id=?", (lid,)).fetchone()[0]
        st = upload_match_stats(conn, 4, "2026-06")[uid]
    assert st == {"total": 1, "matched": 1, "excluded": 0}


# ── 14. a staff channel move lands before the TRANSFER·ANY spill ──────────────

def test_14_channel_move_before_transfer_any(client):
    from recon.matcher import category_reconciliation
    db = main._DB_PATH
    _set_pos_month(db, [("Bank Transfer", 2000), ("Cash", 5000)])
    _bank(db, "kbank", "TRANSFER", 2000, "2026-06-10")
    _bank(db, "kbank", "TRANSFER", 5000, "2026-06-11")          # customer transfer rung as Cash
    c = sqlite3.connect(db)
    c.execute("INSERT INTO recon_channel_move(branch_id,period_month,from_category,from_bank,"
              "to_category,to_bank,amount) VALUES(4,'2026-06','CASH','NONE','TRANSFER','KBANK',5000)")
    c.commit(); c.close()
    with main.get_db() as conn:
        rows, _ = category_reconciliation(conn, 4, "2026-06")
    tr = {r["bank"]: (r["pos_amount"], r["ext_amount"], r["status"])
          for r in rows if r["category"] == "TRANSFER"}
    assert tr == {"ANY": (2000, 2000, "matched"), "KBANK": (5000, 5000, "matched")}


# ── 15. issuer points: a summary with no POS points is a diff, not ok ─────────

def test_15_coupon_points_ext_only_is_a_diff(client):
    from recon.matcher import category_reconciliation
    db = main._DB_PATH
    _point_mid(db)
    _cc(db, "2026-06-03", "KTC", 160, 160, "662")                # KTC point summary, POS has none
    with main.get_db() as conn:
        rows, _ = category_reconciliation(conn, 4, "2026-06")
    assert next(r for r in rows if r["category"] == "COUPON")["status"] == "mismatch"
    r = client.get("/api/v2/reconcile", params={"branch_id": 4, "month": "2026-06"}).json()
    assert next(c for c in r["channels"] if c["key"] == "COUPON")["status"] == "diff"


# ── 16. daily board honours last month's T+1 cash claim ───────────────────────

def test_16_daily_board_prev_month_cash_claim(client):
    from recon.matcher import daily_board
    db = main._DB_PATH
    _pos_day(db, [("2026-06-30", "Cash", 10000), ("2026-07-01", "KNNQR", 700)])
    _bank(db, "ktb", "QR", 10000, "2026-07-01")                  # June 30 cash (KTB 'SDCH')
    _bank(db, "kbank", "QR", 700, "2026-07-01")
    with main.get_db() as conn:
        board = daily_board(conn, "2026-07-01")
    cell = next(b for b in board["branches"] if b["branch_id"] == 4)["cells"]["QR"]
    assert cell["ext"] == 700 and cell["status"] == "matched"


# ── 17. coverage reuses the payout matcher (LINE MAN threshold) ───────────────

def test_17_coverage_lineman_threshold_payout_matched(client):
    from recon.matcher import bank_statement_coverage
    db = main._DB_PATH
    _add_lineman_net(db, "2026-06-05", 40)
    _add_lineman_net(db, "2026-06-12", 30)
    _add_lineman_net(db, "2026-06-20", 460)
    p = _bank(db, "kbank", "LINEMAN_PAYOUT", 530, "2026-06-21")
    with main.get_db() as conn:
        cov = bank_statement_coverage(conn, 4, "2026-06")
    line = next(x for x in cov["lines"] if x["id"] == p)
    assert (line["category"], line["status"]) == ("LINEMAN", "matched")


# ── 18. the certificate knows who prepared the month ──────────────────────────

def test_18_reconcile_month_session_has_preparer(client):
    from recon.reconcile_v2 import reconcile_month
    with main.get_db() as conn:
        conn.execute("INSERT INTO recon_sessions(branch_id,period_month,status,created_by)"
                     " VALUES(4,'2026-06','draft','Nok')")
        conn.commit()
        r = reconcile_month(conn, 4, "2026-06")
    assert r["session"]["created_by"] == "Nok"


# ── 19. a day-ack only clears the variance it acknowledged ────────────────────

def _qr_day_ack_setup(client):
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['KNNQR']['id']}", json={"category": "QR", "bank": "KBANK"})
    client.patch(f"/api/payment-map/{pm['K-SHOP']['id']}", json={"category": "QR", "bank": "KBANK"})
    _pos_day(db, [("2026-06-10", "KNNQR", 50000), ("2026-06-11", "KNNQR", 50000),
                  ("2026-06-12", "K-SHOP", 50000)])
    short = _bank(db, "kbank", "QR", 40000, "2026-06-10")         # 10,000 short on the 10th
    _bank(db, "kbank", "QR", 50000, "2026-06-11")
    _bank(db, "kbank", "QR", 50000, "2026-06-12")
    assert client.post("/api/day-ack", json={
        "branch_id": 4, "period_month": "2026-06", "category": "QR", "day": "2026-06-10",
        "variance": -10000, "note": "ลูกค้าจ่าย QR แต่ยอดเข้าน้อยกว่า ตรวจสลิปแล้ว"}).status_code == 200
    return db, short


def _qr(client):
    r = client.get("/api/v2/reconcile", params={"branch_id": 4, "month": "2026-06"}).json()
    return r, next(c for c in r["channels"] if c["key"] == "QR")


def test_19_day_ack_holds_while_variance_unchanged(client):
    _qr_day_ack_setup(client)
    r, qr = _qr(client)
    assert qr["acked_days"] == 1 and qr["status"] != "diff"
    assert next(d for d in qr["days"] if d["day"] == "2026-06-10").get("ack")


def test_19_day_ack_stale_after_variance_moves(client):
    db, short = _qr_day_ack_setup(client)
    c = sqlite3.connect(db)
    c.execute("DELETE FROM recon_bank_lines WHERE id=?", (short,))   # day now −50,000
    c.commit(); c.close()
    r, qr = _qr(client)
    d10 = next(d for d in qr["days"] if d["day"] == "2026-06-10")
    assert not d10.get("ack") and d10.get("ack_stale")
    assert d10["detail"] == "ยอดเปลี่ยนหลังรับทราบ"
    assert qr["status"] == "diff" and "ยอดเปลี่ยนหลังรับทราบ" in qr["detail"]
    assert any(t["type"] == "diff" and t["channel"] == "QR" for t in r["todo"])
    # re-ack at the new figure clears it again
    assert client.post("/api/day-ack", json={
        "branch_id": 4, "period_month": "2026-06", "category": "QR", "day": "2026-06-10",
        "variance": d10["variance"], "note": "ธนาคารยังไม่โอนยอดวันนี้ ติดตามแล้ว"}).status_code == 200
    r, qr = _qr(client)
    assert next(d for d in qr["days"] if d["day"] == "2026-06-10").get("ack")
