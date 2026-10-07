"""UX phase 3 (track A): per-day document coverage (the DOCS pane's coverage strips),
the drop zone's bulk-upload result fields, and the sign-off figures the branch
payload carries (pending discrepancies). Step-up re-authentication on approve /
close / reopen is tested with the login in test_recon_login.py.

Platform-neutral: a temp DB per test, no network.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import recon.main as main  # noqa: E402
from recon.reconcile_v2 import _days_covered  # noqa: E402
from test_recon import client  # noqa: E402,F401  (fixture)

PM = "2026-06"


def _upload(db, provider, dates, source="bank", branch_id=4):
    """One confirmed upload with a line on each of `dates`."""
    c = sqlite3.connect(db)
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES(?,?,?,'f',?,?,'confirmed')", (source, branch_id, provider, PM, len(dates))).lastrowid
    for d in dates:
        if source == "bank":
            c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit)"
                      " VALUES(?,?,?,'QR',100)", (up, branch_id, d))
        elif source == "credit_card":
            c.execute("INSERT INTO recon_cc_lines(upload_id,branch_id,settlement_date,card_type,gross_amount)"
                      " VALUES(?,?,?,'VISA',100)", (up, branch_id, d))
        else:
            c.execute("INSERT INTO recon_grab_lines(upload_id,branch_id,order_date,order_status,food_amount)"
                      " VALUES(?,?,?,'Completed',100)", (up, branch_id, d))
    c.commit(); c.close()


def _cov(provider="KBANK", src="bank"):
    c = sqlite3.connect(main._DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        return sorted(_days_covered(c, 4, PM).get((src, provider), ()))
    finally:
        c.close()


def _doc(cl, key):
    r = cl.get(f"/api/v2/reconcile?branch_id=4&month={PM}").json()
    return next(d for d in r["documents"] if d["key"] == key)


# ── days_covered ──────────────────────────────────────────────────────────────
def test_statement_for_1_to_16_covers_every_day_between(client):
    """Lines on 1, 2, 5 and 16 only (no money moved on the other days): the
    statement still covers 1..16 — coverage is the document's span, not its lines."""
    db = main._DB_PATH
    _upload(db, "KBANK", ["2026-06-01", "2026-06-02", "2026-06-05", "2026-06-16"])
    assert _cov() == list(range(1, 17))
    d = _doc(client, "stmt_KBANK")
    assert d["days_covered"] == list(range(1, 17))
    assert d["days_due"] == 30 and d["status"] == "partial" and d["coverage_to"] == "2026-06-16"


def test_two_uploads_add_up_and_leave_the_gap_visible(client):
    db = main._DB_PATH
    _upload(db, "KBANK", ["2026-06-01", "2026-06-10"])
    _upload(db, "kbank", ["2026-06-20", "2026-06-30"])            # provider spelled another way
    _upload(db, "KBANK", ["2026-06-05", "2026-06-08"])            # inside the first: no change
    assert _cov() == list(range(1, 11)) + list(range(20, 31))
    d = _doc(client, "stmt_KBANK")
    assert d["status"] == "uploaded"                              # max date = 30 …
    assert 15 not in d["days_covered"]                            # … yet the strip shows 11–19 missing


def test_a_statement_spilling_across_months_covers_to_the_edge(client):
    db = main._DB_PATH
    _upload(db, "KBANK", ["2026-05-28", "2026-06-03"])            # began in May
    _upload(db, "KBANK", ["2026-06-27", "2026-07-02"])            # nothing moved on 28–30 June
    assert _cov() == [1, 2, 3, 27, 28, 29, 30]


def test_banks_and_sources_are_kept_apart(client):
    db = main._DB_PATH
    _upload(db, "KBANK", ["2026-06-01", "2026-06-03"])
    _upload(db, "KTB", ["2026-06-10", "2026-06-12"])
    _upload(db, "KTC", ["2026-06-04", "2026-06-06"], source="credit_card")
    _upload(db, "Grab", ["2026-06-07", "2026-06-08"], source="grab")
    _upload(db, "KBANK", ["2026-06-20"], branch_id=5)             # another branch: not ours
    assert _cov("KBANK") == [1, 2, 3]
    assert _cov("KTB") == [10, 11, 12]
    assert _cov("KTC", "cc") == [4, 5, 6]
    assert _cov(None, "grab") == [7, 8]
    d = _doc(client, "cc_KTB")                                    # Credit กรุงไทย → acquirer KTB
    assert d["days_covered"] == []                                # KTC lines are not KTB's


def test_no_upload_means_an_empty_strip(client):
    d = _doc(client, "stmt_KBANK")
    assert d["status"] == "missing" and d["days_covered"] == [] and d["days_due"] == 30


# ── sign-off figures in the branch payload ────────────────────────────────────
def test_session_carries_the_pending_discrepancy_count(client):
    r = client.get(f"/api/v2/reconcile?branch_id=4&month={PM}").json()
    assert r["session"] is None
    sid = client.post("/api/sessions", json={"branch_id": 4, "period_month": PM}).json()["session_id"]
    c = sqlite3.connect(main._DB_PATH)
    c.executemany("INSERT INTO recon_discrepancies(session_id,disc_type,source_type,category,bank,resolution)"
                  " VALUES(?,'amount_mismatch','bank','QR','KBANK',?)",
                  [(sid, "pending"), (sid, "pending"), (sid, "accepted")])
    c.commit(); c.close()
    ses = client.get(f"/api/v2/reconcile?branch_id=4&month={PM}").json()["session"]
    assert ses["session_id"] == sid and ses["pending_discrepancies"] == 2
    assert ses["sent_for_review_at"] is None
    assert client.patch(f"/api/sessions/{sid}/status", data={"status": "in_review"}).status_code == 200
    ses = client.get(f"/api/v2/reconcile?branch_id=4&month={PM}").json()["session"]
    assert ses["sent_for_review_at"] and ses["sent_for_review_by"] == "staff"


# ── drop zone: what the bulk result tells about each file ─────────────────────
def _csv(lines):
    return ("Kasikorn Bank KBANK Statement\nDate,Description,Withdrawal,Deposit,Balance\n"
            + "".join(x + "\n" for x in lines)).encode()


def test_bulk_result_names_rows_of_other_months_duplicates_and_refusals(client):
    june = _csv(["29/06/2026,QR receive,,1000.00,1000.00",
                 "30/06/2026,QR receive,,500.00,1500.00",
                 "01/07/2026,QR receive,,200.00,1700.00"])        # a T+1 line in July
    may = _csv(["02/05/2026,QR receive,,300.00,300.00"])
    files = [("files", ("KBANK_statement_06.csv", june, "text/csv")),
             ("files", ("KBANK_statement_06_copy.csv", june, "text/csv")),   # same bytes
             ("files", ("KBANK_statement_05.csv", may, "text/csv"))]
    r = client.post("/api/uploads/bulk", files=files,
                    data={"branch_id": "4", "period_month": PM, "source_type": "auto"})
    assert r.status_code == 200, r.text
    j = r.json()
    ok, dup, other = j["files"]
    assert ok["status"] == "ok" and ok["rows_saved"] == 3 and ok["other_month_rows"] == 1
    assert ok["row_months"] == ["2026-06", "2026-07"] and ok["duplicate_lines"] == 0
    assert dup["status"] == "skipped" and "อัปโหลดไปแล้ว" in dup["reason"]
    assert other["status"] == "error" and "2026-05" in other["reason"]
    s = j["summary"]
    assert (s["saved"], s["skipped"], s["errored"], s["rows"], s["other_month_rows"]) == (1, 1, 1, 3, 1)
    # the coverage strip picks the upload up: 29, 30 (the July line runs to the month's edge)
    assert _cov() == [29, 30]
    # a re-downloaded statement (new bytes, same movements + one new day): overlap skipped by line
    again = _csv(["30/06/2026,QR receive,,500.00,1500.00",
                  "01/07/2026,QR receive,,200.00,1700.00",
                  "02/07/2026,QR receive,,100.00,1800.00"])
    j2 = client.post("/api/uploads/bulk", files=[("files", ("KBANK_statement_07.csv", again, "text/csv"))],
                     data={"branch_id": "4", "period_month": PM, "source_type": "auto"}).json()
    assert j2["files"][0]["status"] == "ok" and j2["files"][0]["duplicate_lines"] == 2
    assert j2["summary"]["duplicate_lines"] == 2
