"""Payout gaps on the branch page (owner 2026-10-06).

Provider NET ↔ bank payout mismatches used to live only in #discrepancies, so a
branch page that said "พร้อมปิดเดือน" could still 409 at approval. They are now in
the v2 payload (payout_issues + a 'payout' todo) and acknowledged in place via
/api/payout-ack, which run-match respects.

Run:  pytest recon/tests/test_payout_on_branch.py
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))      # fixture helpers
import recon.main as main                                     # noqa: E402
from test_fix_matcher import client, _grab, _bank             # noqa: E402,F401

NOTE = "Grab หักค่าโปรร้านค้าจากยอดโอน ตรวจกับใบแจ้งแล้ว"


def _gap(db):
    """Grab NET ฿10,000 on 15 Jun, only ฿8,000 paid on the 16th → −2,000 mismatch."""
    _grab(db, "2026-06-15", 10000)
    _bank(db, "kbank", "GRAB_PAYOUT", 8000, "2026-06-16")


def _v2(client):
    r = client.get("/api/v2/reconcile?branch_id=4&month=2026-06")
    assert r.status_code == 200, r.text
    return r.json()


def _session(client):
    return client.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json()["session_id"]


def test_mismatch_is_listed_and_blocks_close(client):
    _gap(main._DB_PATH)
    d = _v2(client)
    g = [p for p in d["payout_issues"] if p["category"] == "GRAB"]
    assert g and g[0]["variance"] == -2000 and g[0]["accepted"] is None
    assert any(t["type"] == "payout" and t["channel"] == "GRAB" for t in d["todo"])
    assert d["can_close"] is False


def test_ack_clears_todo_and_survives_run_match(client):
    _gap(main._DB_PATH)
    sid = _session(client)
    body = {"branch_id": 4, "period_month": "2026-06", "category": "GRAB", "note": "short"}
    assert client.post("/api/payout-ack", json=body).status_code == 400
    r = client.post("/api/payout-ack", json={**body, "note": NOTE})
    assert r.status_code == 200, r.text
    d = _v2(client)
    g = [p for p in d["payout_issues"] if p["category"] == "GRAB"][0]
    assert g["accepted"] and NOTE in g["accepted"]["note"]
    assert not any(t["type"] == "payout" for t in d["todo"])
    # run-match must not plant a pending twin (that is what used to 409 the close)
    assert client.post(f"/api/sessions/{sid}/run-match").status_code == 200
    with main.get_db() as conn:
        pend = conn.execute("SELECT COUNT(*) FROM recon_discrepancies WHERE session_id=? "
                            "AND bank='PAYOUT' AND resolution='pending'", (sid,)).fetchone()[0]
        audit = conn.execute("SELECT COUNT(*) FROM recon_audit_log WHERE entity='discrepancy'").fetchone()[0]
    assert pend == 0 and audit >= 1


def test_ack_reappears_when_the_gap_moves(client):
    db = main._DB_PATH
    _gap(db)
    _session(client)
    client.post("/api/payout-ack", json={"branch_id": 4, "period_month": "2026-06",
                                         "category": "GRAB", "note": NOTE})
    _grab(db, "2026-06-20", 3000)                    # a new report day widens the gap
    d = _v2(client)
    g = [p for p in d["payout_issues"] if p["category"] == "GRAB"][0]
    assert g["accepted"] is None
    assert any(t["type"] == "payout" for t in d["todo"])


def test_unack_and_guards(client):
    db = main._DB_PATH
    _gap(db)
    sid = _session(client)
    # nothing to acknowledge on a provider without a gap
    r = client.post("/api/payout-ack", json={"branch_id": 4, "period_month": "2026-06",
                                             "category": "LINEMAN", "note": NOTE})
    assert r.status_code == 409
    ack = client.post("/api/payout-ack", json={"branch_id": 4, "period_month": "2026-06",
                                               "category": "GRAB", "note": NOTE}).json()
    assert client.delete(f"/api/payout-ack/{ack['id']}").status_code == 200
    assert client.delete(f"/api/payout-ack/{ack['id']}").status_code == 404
    assert any(t["type"] == "payout" for t in _v2(client)["todo"])
    # a closed month can't be acknowledged into
    c = sqlite3.connect(db)
    c.execute("UPDATE recon_sessions SET status='approved' WHERE session_id=?", (sid,))
    c.commit(); c.close()
    r = client.post("/api/payout-ack", json={"branch_id": 4, "period_month": "2026-06",
                                             "category": "GRAB", "note": NOTE})
    assert r.status_code == 423
