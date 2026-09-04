"""B2 (2026-09): every KTB own-account transfer code is suggested for exclusion
at any amount, and one request excludes the whole list — with reasons for the
lines it must leave alone."""
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import recon.main as main  # noqa: E402
import recon.core as core  # noqa: E402
from recon.matcher import suggest_internal_transfers  # noqa: E402
from test_recon import _make_db, DEMO_POS  # noqa: E402

BRANCH = 4


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    core._DB_PATH = main._DB_PATH = _make_db(tmp_path_factory.mktemp("b2"), DEMO_POS)
    with TestClient(main.app) as cl:
        yield cl


def _line(db, credit, description, date="2026-06-10", status="unmatched", channel="TRANSFER"):
    c = sqlite3.connect(db)
    up = c.execute("SELECT upload_id FROM recon_uploads LIMIT 1").fetchone()
    if not up:
        up = (c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                        " VALUES('bank',?,'ktb','f','2026-06',0,'confirmed')", (BRANCH,)).lastrowid,)
    lid = c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,description,match_status)"
                    " VALUES(?,?,?,?,?,?,?)", (up[0], BRANCH, date, channel, credit, description, status)).lastrowid
    c.commit(); c.close()
    return lid


def test_suggester_flags_transfer_codes_at_any_amount_and_says_why(client):
    db = main._DB_PATH
    small_code = _line(db, 5000, "IORSDT โอนเข้า 5,000")           # under the 100k floor — suggested by code
    big_round = _line(db, 200000, "TR sweep")                       # the old rule
    plain = _line(db, 50000, "โอนเข้า 50,000")                     # neither → left alone
    sale = _line(db, 300000, "IORSDT ขาย")                          # code wins even with a sales word
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    got = {s["id"]: s["reason"] for s in suggest_internal_transfers(con, BRANCH, "2026-06")}
    con.close()
    assert got[small_code] == "code" and got[sale] == "code"
    assert got[big_round] == "large_round"
    assert plain not in got
    # the overview carries the same list, reason included
    ov = client.get("/api/v2/overview", params={"month": "2026-06"}).json()
    br = next(b for b in ov["branches"] if b["branch_id"] == BRANCH) if "branches" in ov else None
    if br is not None and "internal_transfer_suggestions" in br:
        assert {s["reason"] for s in br["internal_transfer_suggestions"]} <= {"code", "large_round"}


def test_bulk_ignore_excludes_the_list_and_reports_what_it_could_not(client):
    db = main._DB_PATH
    a = _line(db, 7000, "IORSDT a")
    b = _line(db, 8000, "NBSDT b")
    manual = _line(db, 9000, "IORSDT matched by hand", status="manual")
    r = client.post("/api/bank-lines/bulk-ignore",
                    json={"line_ids": [a, b, manual, 999999, a], "note": "month-end sweep", "by": "staff1"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ignored"] == [a, b] and j["match_status"] == "excluded"
    assert {(s["line_id"], s["reason"][:14]) for s in j["skipped"]} == {
        (manual, "line is manual"), (999999, "bank line not ")}
    con = sqlite3.connect(db)
    assert con.execute("SELECT match_status FROM recon_bank_lines WHERE id IN (?,?)", (a, b)).fetchall() == [("excluded",), ("excluded",)]
    assert con.execute("SELECT match_status FROM recon_bank_lines WHERE id=?", (manual,)).fetchone() == ("manual",)
    # audited per line, with the note
    n_audit = con.execute("SELECT COUNT(*) FROM recon_audit_log WHERE entity='bank_line' AND entity_id IN (?,?) AND new_val='month-end sweep'", (a, b)).fetchone()[0]
    con.close()
    assert n_audit == 2
    # already-ignored lines are skipped, not double-audited; restore works in bulk too
    again = client.post("/api/bank-lines/bulk-ignore", json={"line_ids": [a]}).json()
    assert again["ignored"] == [] and again["skipped"][0]["reason"] == "line is already ignored"
    back = client.post("/api/bank-lines/bulk-ignore", json={"line_ids": [a, b], "ignored": False}).json()
    assert back["restored"] == [a, b] and back["match_status"] == "unmatched"


def test_bulk_ignore_rejects_an_empty_list(client):
    assert client.post("/api/bank-lines/bulk-ignore", json={"line_ids": []}).status_code == 400
