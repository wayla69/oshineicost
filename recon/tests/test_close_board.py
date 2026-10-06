"""Close Board figures on /api/v2/overview (UX phase 2, 2026-10).

The board (#close) reads per-branch, per-channel figures straight from the
overview — derived from the reconcile_month payload the overview already builds,
so the endpoint stays one reconcile per branch. These pin the definitions that
help.html §3.2 states: open_amount / unexplained_total, missing_docs /
missing_doc, waiting / waiting_total, payout_open_total and the approver.

Run:  pytest recon/tests/test_close_board.py
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))      # fixture helpers
import recon.main as main                                     # noqa: E402
from test_fix_matcher import client, _grab, _bank, _pos_day   # noqa: E402,F401

PM = "2026-06"
NOTE = "Grab หักค่าโปรร้านค้าจากยอดโอน ตรวจกับใบแจ้งแล้ว"


def _ov(client):
    r = client.get(f"/api/v2/overview?month={PM}")
    assert r.status_code == 200, r.text
    return {b["branch_id"]: b for b in r.json()["branches"]}


def _v2(client, bid=4):
    return client.get(f"/api/v2/reconcile?branch_id={bid}&month={PM}").json()


def test_board_fields_present_and_consistent_with_branch_payload(client):
    b = _ov(client)[4]
    for k in ("unexplained_total", "missing_docs", "waiting_total", "payout_open_total",
              "payout_open_count", "payout_issue_count", "prepared_by", "approved_by", "approved_at"):
        assert k in b, k
    full = _v2(client)
    for c in full["channels"]:
        bc = b["channels"][c["key"]]
        # the existing keys are untouched…
        assert (bc["pos"], bc["ext"], bc["variance"], bc["status"]) == \
            (c["pos"], c["ext"], c["variance"], c["status"])
        # …and the residual is the branch payload's own residual
        assert bc["residual"] == c["residual"]
        assert bc["accepted"] is bool(c["accepted"])
    # unexplained = Σ|open_amount| + open payout gaps
    want = sum(abs(c["open_amount"]) for c in b["channels"].values()) + b["payout_open_total"]
    assert abs(b["unexplained_total"] - round(want, 2)) < 0.01
    assert abs(b["waiting_total"] - round(sum(c["waiting"] for c in b["channels"].values()), 2)) < 0.01


def test_diff_channel_counts_until_accepted(client):
    db = main._DB_PATH
    _pos_day(db, [("2026-06-10", "KNNQR", 1000)])
    _bank(db, "kbank", "QR", 400, "2026-06-10")               # QR short 600 on the 10th
    b = _ov(client)[4]
    qr = b["channels"]["QR"]
    full = next(c for c in _v2(client)["channels"] if c["key"] == "QR")
    assert qr["status"] == "diff" and qr["open_amount"] == full["residual"] != 0
    assert b["unexplained_total"] >= abs(qr["open_amount"])
    # an accepted channel is explained: open_amount 0, flagged accepted
    r = client.post("/api/lanes/adj", json={"branch_id": 4, "period_month": PM, "category": "QR",
                                            "bank": "ALL", "note": "ค่าธรรมเนียมธนาคารหักก่อนโอน",
                                            "adj_type": "accept"})
    assert r.status_code == 200, r.text
    qr2 = _ov(client)[4]["channels"]["QR"]
    assert qr2["accepted"] is True and qr2["open_amount"] == 0


def test_missing_documents_listed_with_channel_label(client):
    b = _ov(client)[4]
    keys = {d["key"]: d for d in b["missing_docs"]}
    # DEMO_POS rings Grab (GB) with no Grab report uploaded
    assert "grab" in keys and keys["grab"]["status"] == "missing" and keys["grab"]["provider"] == "GRAB"
    gr = b["channels"].get("GRAB")
    assert gr and gr["status"] == "waiting_doc" and gr["missing_doc"] == "รายงาน Grab"
    assert gr["waiting"] == 0                                   # blocked by a doc, not "in transit"


def test_payout_gap_open_then_accepted(client):
    db = main._DB_PATH
    _grab(db, "2026-06-15", 10000)
    _bank(db, "kbank", "GRAB_PAYOUT", 8000, "2026-06-16")
    b = _ov(client)[4]
    assert b["payout_open_count"] == 1 and b["payout_open_total"] == 2000
    assert b["unexplained_total"] >= 2000
    sid = client.post("/api/sessions", json={"branch_id": 4, "period_month": PM}).json()["session_id"]
    assert sid
    r = client.post("/api/payout-ack", json={"branch_id": 4, "period_month": PM,
                                             "category": "GRAB", "note": NOTE})
    assert r.status_code == 200, r.text
    b2 = _ov(client)[4]
    assert b2["payout_open_count"] == 0 and b2["payout_open_total"] == 0
    assert b2["payout_issue_count"] == 1                       # still listed (accepted)


def test_approver_and_preparer_surface(client):
    con = sqlite3.connect(main._DB_PATH)
    con.execute("INSERT INTO recon_sessions(branch_id,period_month,status,created_by,approved_by,approved_at)"
                " VALUES(5,?,'approved','prep1','Boss','2026-07-20 10:00:00')", (PM,))
    con.commit(); con.close()
    b = _ov(client)[5]
    assert b["status"] == "approved" and b["approved_by"] == "Boss" and b["prepared_by"] == "prep1"
    assert b["approved_at"].startswith("2026-07-20")
