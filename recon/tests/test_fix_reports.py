"""Regression tests for the reporting-layer fixes (2026-10 recon report review).

Each test pins one confirmed bug in recon/routers/reports.py or diagnostics.py:
stored XSS in the wrong-channel print sheet, Excel formula injection, write-off /
override figures taken from client-typed day-ack variances, deleted overrides
vanishing from the audit trail, accepted variances listed as open, the certificate's
signed residual netting, the ambiguous-column diagnostics 500, the wrong-channel
trend baseline, the unbounded /api/audit limit, and the Auditor Pack recomputing the
same branch-month many times.

Run:  py -3.12 -m pytest recon/tests/test_fix_reports.py
"""
import io
import json
import os
import sqlite3
import sys
import zipfile
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import recon.main as main                     # noqa: E402
import recon.core as core                     # noqa: E402
import recon.routers.reports as reports       # noqa: E402
from test_recon import _make_db, _add_bank_line, DEMO_POS   # noqa: E402

PM = "2026-06"


@pytest.fixture
def client(tmp_path):
    core._DB_PATH = main._DB_PATH = _make_db(tmp_path, DEMO_POS)
    os.environ.pop("OSHINEI_SEED_TOKEN", None)
    with TestClient(main.app) as cl:
        yield cl


@pytest.fixture
def qr_cash_client(tmp_path):
    """Branch 4, June: QR short ฿10,000 on the 10th, cash over ฿10,000 on the 11th."""
    core._DB_PATH = main._DB_PATH = _make_db(tmp_path, [("KNNQR", 100000), ("Cash", 20000)])
    os.environ.pop("OSHINEI_SEED_TOKEN", None)
    with TestClient(main.app) as cl:
        db = main._DB_PATH
        pm = {r["payment_code_norm"]: r for r in cl.get("/api/payment-map").json()}
        cl.patch(f"/api/payment-map/{pm['KNNQR']['id']}", json={"category": "QR", "bank": "KBANK"})
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day(branch_id INT, day TEXT, "
                  "payment_code TEXT, amount REAL, txn_count INT DEFAULT 0)")
        c.executemany("INSERT INTO pos_payment_day VALUES(4,?,?,?,0)",
                      [("2026-06-10", "KNNQR", 100000), ("2026-06-10", "Cash", 20000)])
        c.commit(); c.close()
        _add_bank_line(db, "KBANK", "QR", 90000, "2026-06-10")
        _add_bank_line(db, "KBANK", "CASH_DEP", 30000, "2026-06-11")
        yield cl


def _sql(sql, *args):
    c = sqlite3.connect(main._DB_PATH)
    c.row_factory = sqlite3.Row
    cur = c.execute(sql, args)
    rows = [dict(r) for r in cur.fetchall()] if cur.description else []
    last = cur.lastrowid
    c.commit(); c.close()
    return rows if rows else last


def _wrong_channel_match(pos_days, note="test"):
    """A confirmed cross-channel manual match: rung as CASH, money landed as QR."""
    _add_bank_line(main._DB_PATH, "KBANK", "QR", 777)
    lid = _sql("SELECT MAX(id) AS i FROM recon_bank_lines")[0]["i"]
    mid = _sql("INSERT INTO recon_manual_match(branch_id,category,bank,period_month,pos_amount,"
               "ext_amount,variance,pos_days,note,matched_by) VALUES(4,'CASH','NONE',?,777,777,0,?,?,'t')",
               PM, json.dumps(pos_days), note)
    _sql("INSERT INTO recon_manual_match_line(match_id,ext_table,ext_id,amount) "
         "VALUES(?,'recon_bank_lines',?,777)", mid, lid)
    return mid


def _xlsx(resp):
    assert resp.status_code == 200, resp.text[:300]
    return openpyxl.load_workbook(io.BytesIO(resp.content))


def _cells(wb):
    return [c for ws in wb.worksheets for row in ws.iter_rows() for c in row]


# ── 1. stored XSS in the wrong-channel print sheet ───────────────────────────
def test_wrong_channel_print_escapes_pos_days(client):
    _wrong_channel_match([{"day": "2026-06-<img src=x onerror=alert(1)>", "amount": 1}])
    html = client.get("/api/reports/wrong-channel.print", params={"period_month": PM}).text
    assert "<img src=x onerror" not in html
    assert "&lt;img src=x onerror" in html


def test_wrong_channel_non_string_day_does_not_500(client):
    _wrong_channel_match([{"day": 20260610, "amount": 1}])
    assert client.get("/api/reports/wrong-channel.print", params={"period_month": PM}).status_code == 200
    _xlsx(client.get("/api/reports/wrong-channel.xlsx", params={"period_month": PM}))


# ── 2. Excel formula injection ───────────────────────────────────────────────
_EVIL = '=HYPERLINK("http://evil.example","x")'


def _assert_neutralised(wb, text):
    hits = [c for c in _cells(wb) if c.value == text]
    assert hits, f"{text!r} not exported"
    for c in hits:
        assert c.data_type == "s", (c.coordinate, c.data_type)


def test_xlsx_formula_injection_neutralised(client):
    _sql("INSERT INTO recon_lane_adj(branch_id,period_month,category,bank,note,adj_type,created_by) "
         "VALUES(4,?,'GRAB','ALL',?,'accept','t')", PM, _EVIL)
    _sql("INSERT INTO recon_channel_move(branch_id,period_month,from_category,from_bank,to_category,"
         "to_bank,amount,note,created_by) VALUES(4,?,'CASH','NONE','QR','KBANK',500,'@SUM(A1)','t')", PM)
    _sql("INSERT INTO recon_day_ack(branch_id,period_month,category,day,variance,note,created_by) "
         "VALUES(4,?,'QR','2026-06-10',-250,'+cmd|calc','t')", PM)
    _assert_neutralised(_xlsx(client.get("/api/reports/override-register.xlsx",
                                         params={"period_month": PM})), _EVIL)
    _assert_neutralised(_xlsx(client.get("/api/reports/branch-errors.xlsx")), _EVIL)
    wc = _xlsx(client.get("/api/reports/wrong-channel.xlsx", params={"period_month": PM}))
    _assert_neutralised(wc, "@SUM(A1)")
    av = _xlsx(client.get("/api/reports/accepted-variance.xlsx", params={"period_month": PM}))
    _assert_neutralised(av, "+cmd|calc")
    # real numbers stay numeric — negative amounts must not be turned into text
    nums = [c for c in _cells(av) if isinstance(c.value, (int, float)) and c.value < 0]
    assert nums and all(c.data_type == "n" for c in nums)


# ── 3. write-off / override register value acks at the LIVE variance ────────
def test_day_ack_reported_at_live_variance_not_typed(qr_cash_client):
    cl = qr_cash_client
    # a client-typed variance that has nothing to do with the real day gap (−10,000)
    _sql("INSERT INTO recon_day_ack(branch_id,period_month,category,day,variance,note,created_by) "
         "VALUES(4,?,'QR','2026-06-10',-999999,'typed by hand on the client','t')", PM)
    av = cl.get("/api/reports/accepted-variance", params={"period_month": PM}).json()
    it = next(i for i in av["items"] if i["source"] == "day")
    assert round(it["amount"]) == -10000
    assert round(it["acked_amount"]) == -999999          # the typed figure stays visible
    assert round(av["grand_total_abs"]) == 10000
    reg = cl.get("/api/reports/override-register", params={"period_month": PM}).json()
    row = next(r for r in reg["rows"] if r["kind_key"] == "day_ack")
    assert round(row["amount"]) == -10000
    assert "999,999" in row["detail"]                    # acked figure shown when it differs


def test_override_register_values_channel_accept_at_residual(qr_cash_client):
    cl = qr_cash_client
    _sql("INSERT INTO recon_lane_adj(branch_id,period_month,category,bank,note,adj_type,created_by) "
         "VALUES(4,?,'QR','ALL','accepted after checking slips','accept','t')", PM)
    reg = cl.get("/api/reports/override-register", params={"period_month": PM}).json()
    row = next(r for r in reg["rows"] if r["kind_key"] == "lane_adj")
    assert round(row["amount"]) == -10000                # not adj_pos+adj_ext = 0
    assert round(reg["total_abs_amount"]) == 10000


# ── 4. deleted overrides stay in the append-only audit trail ────────────────
def test_audit_trail_keeps_deleted_override(client):
    aid = _sql("INSERT INTO recon_lane_adj(branch_id,period_month,category,bank,note,adj_type,created_by) "
               "VALUES(4,?,'GRAB','ALL','accepted for the audit test','accept','t')", PM)
    _sql("INSERT INTO recon_audit_log(ts,entity,entity_id,action,field,old_val,new_val,by) "
         "VALUES('2026-06-20T10:00:00','lane_adj',?,'create','accept',NULL,'GRAB·ALL','alice')", aid)
    _sql("DELETE FROM recon_lane_adj WHERE id=?", aid)
    for f, v in (("branch_id", "4"), ("period_month", PM)):
        _sql("INSERT INTO recon_audit_log(ts,entity,entity_id,action,field,old_val,new_val,by) "
             "VALUES('2026-06-21T10:00:00','lane_adj',?,'delete',?,?,NULL,'bob')", aid, f, v)
    tr = client.get("/api/reports/audit-trail", params={"period_month": PM}).json()
    mine = [r for r in tr["rows"] if r["entity"] == "lane_adj"]
    assert {r["action"] for r in mine} == {"create", "delete"}
    assert all(r["branch_id"] == 4 for r in mine)
    tr4 = client.get("/api/reports/audit-trail", params={"period_month": PM, "branch_id": 4}).json()
    assert any(r["action"] == "delete" and r["entity"] == "lane_adj" for r in tr4["rows"])
    other = client.get("/api/reports/audit-trail", params={"period_month": "2026-05"}).json()
    assert not any(r["entity"] == "lane_adj" for r in other["rows"])


def test_audit_trail_deleted_scope_from_dict_diff(client):
    # _audit() str()s values, so a dict diff lands as a Python repr (or JSON)
    for eid, raw in ((555001, str({"branch_id": 4, "period_month": PM, "day": "2026-06-10"})),
                     (555002, json.dumps({"branch_id": 5, "day": "2026-06-11"}))):
        _sql("INSERT INTO recon_audit_log(ts,entity,entity_id,action,field,old_val,new_val,by) "
             "VALUES('2026-07-02T10:00:00','day_ack',?,'delete','row',?,NULL,'bob')", eid, raw)
    tr = client.get("/api/reports/audit-trail", params={"period_month": PM}).json()
    got = {r["branch_id"] for r in tr["rows"] if r["entity"] == "day_ack"}
    assert got == {4, 5}
    assert not any(r["entity"] == "day_ack" for r in
                   client.get("/api/reports/audit-trail", params={"period_month": "2026-07"}).json()["rows"])


def test_audit_trail_unknown_branch_bucket(client):
    # a deleted channel move whose audit rows carry no branch/period at all
    _sql("INSERT INTO recon_audit_log(ts,entity,entity_id,action,field,old_val,new_val,by) "
         "VALUES('2026-06-15T09:00:00','channel_move',987654,'delete',NULL,NULL,NULL,'bob')")
    tr = client.get("/api/reports/audit-trail", params={"period_month": PM}).json()
    row = next(r for r in tr["rows"] if r["entity"] == "channel_move")
    assert row["branch_id"] is None and row["branch_name"]
    tr4 = client.get("/api/reports/audit-trail", params={"period_month": PM, "branch_id": 4}).json()
    assert not any(r["entity"] == "channel_move" for r in tr4["rows"])
    assert client.get("/api/reports/audit-trail.print", params={"period_month": PM}).status_code == 200
    _xlsx(client.get("/api/reports/audit-trail.xlsx", params={"period_month": PM}))


# ── 5. accepted variances are not listed as open in the monthly register ────
def test_error_register_accepted_channel_not_open(client):
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['KNNQR']['id']}", json={"category": "QR", "bank": "KBANK"})
    _add_bank_line(db, "KBANK", "QR", 90000)
    for cat in ("QR", "GRAB"):
        _sql("INSERT INTO recon_lane_adj(branch_id,period_month,category,bank,note,adj_type,created_by) "
             "VALUES(4,?,?,'ALL','accepted after review','accept','t')", PM, cat)
    rep = client.get("/api/reports/wrong-channel", params={"period_month": PM}).json()
    mine = [e for e in rep["errors"] if e["branch_id"] == 4]
    qr = [e for e in mine if e["channel"] == "QR" and e["type"] in ("variance", "ext_only")]
    assert qr and all(e["status"] == "acknowledged" for e in qr)
    assert not any(e["type"] == "missing_doc" and e["channel"] in ("QR", "GRAB") for e in mine)
    html = client.get("/api/reports/wrong-channel.print", params={"period_month": PM}).text
    assert "รับทราบทั้งช่องทาง" in html


# ── 6. certificate residual is not netted across channels ───────────────────
def test_certificate_residual_is_absolute_and_skips_accepted(qr_cash_client):
    cl = qr_cash_client
    cert = cl.get("/api/reports/recon-certificate", params={"branch_id": 4, "period_month": PM}).json()
    assert round(cert["residual_total"]) == 20000        # |−10,000| + |+10,000|, not 0
    assert round(cert["residual_net"]) == 0
    html = cl.get("/api/reports/recon-certificate.print",
                  params={"branch_id": 4, "period_month": PM}).text
    assert "฿20,000</b>" in html
    _sql("INSERT INTO recon_lane_adj(branch_id,period_month,category,bank,note,adj_type,created_by) "
         "VALUES(4,?,'QR','ALL','accepted after checking slips','accept','t')", PM)
    cert = cl.get("/api/reports/recon-certificate", params={"branch_id": 4, "period_month": PM}).json()
    assert round(cert["residual_total"]) == 10000        # accepted QR no longer counts


# ── 7. diagnostics excel-payment-cols with a branch filter ──────────────────
def test_excel_payment_cols_branch_filter(client):
    _sql("CREATE TABLE sales_month_summary(branch_id INT, period_month TEXT, source TEXT, cash REAL, "
         "credit_card REAL, transfer REAL, qr REAL, coupon REAL, net_sales REAL)")
    _sql("INSERT INTO sales_month_summary VALUES(4,?,'Easy',1,2,3,4,5,15)", PM)
    _sql("INSERT INTO sales_month_summary VALUES(5,?,'Easy',9,9,9,9,9,45)", PM)
    r = client.get("/api/diagnostics/excel-payment-cols", params={"period_month": PM, "branch_id": 4})
    assert r.status_code == 200, r.text[:300]
    assert [x["branch"] for x in r.json()["rows"]] == ["Korat1"]


# ── 8. wrong-channel trend compares with the previous CALENDAR month ────────
def test_wrong_channel_trend_uses_previous_calendar_month(client):
    for pm_ in ("2026-04", "2026-04", "2026-06"):
        _sql("INSERT INTO recon_channel_move(branch_id,period_month,from_category,from_bank,to_category,"
             "to_bank,amount,note,created_by) VALUES(4,?,'CASH','NONE','QR','KBANK',100,'n','t')", pm_)
    rep = client.get("/api/reports/wrong-channel", params={"period_month": PM}).json()
    s = next(x for x in rep["summary"] if x["branch_id"] == 4)
    assert s["direction"] == "up"          # May had 0, June has 1 — worse, not "better than April"


# ── 9. /api/audit limit is clamped ──────────────────────────────────────────
def test_audit_limit_clamped(client):
    for i in range(3):
        _sql("INSERT INTO recon_audit_log(ts,entity,entity_id,action,by) "
             "VALUES('2026-06-01T00:00:0' || ?,'lane_adj',?,'create','t')", str(i), 1000 + i)
    assert len(client.get("/api/audit", params={"limit": -1}).json()) == 1
    assert len(client.get("/api/audit", params={"limit": 0}).json()) == 1
    assert len(client.get("/api/audit", params={"limit": 2}).json()) == 2


# ── 10. Auditor Pack reuses one reconcile per branch-month ──────────────────
def test_auditor_pack_reuses_reconcile_per_branch_month(client, monkeypatch):
    calls = []
    real = reports.reconcile_month

    def counting(conn, bid, pm_):
        calls.append((bid, pm_))
        return real(conn, bid, pm_)
    monkeypatch.setattr(reports, "reconcile_month", counting)
    _sql("INSERT INTO recon_lane_adj(branch_id,period_month,category,bank,note,adj_type,created_by) "
         "VALUES(4,?,'GRAB','ALL','accepted for the pack test','accept','t')", PM)
    r = client.get("/api/reports/auditor-pack.zip", params={"period_month": PM})
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert len(names) == 7
    readme = zipfile.ZipFile(io.BytesIO(r.content)).read("00_README.txt").decode("utf-8")
    assert "สร้างไม่สำเร็จ" not in readme
    assert calls and len(calls) == len(set(calls)), calls
    # outside the pack nothing is memoised: two report calls recompute
    calls.clear()
    client.get("/api/reports/recon-certificate", params={"branch_id": 4, "period_month": PM})
    client.get("/api/reports/recon-certificate", params={"branch_id": 4, "period_month": PM})
    assert calls.count((4, PM)) >= 2
