# -*- coding: utf-8 -*-
"""Sales Support sends the month's price file; the analyst reviews, imports or rejects it (PR-D).

The promises:
  * the file is accepted by its own bytes (Excel zip or CSV text), never by its name, up to 12 MB;
  * a submission is kept with a dry-run summary; the listing never carries the file, only
    `GET .../file` gives the bytes back, exactly as sent;
  * a decision is final: pending -> imported | rejected, a rejection needs a reason;
  * หน้างาน shows Sales Support the file to send and the analyst the files to review;
  * over HTTP: submitting writes no price; the analyst's preview needs no token and writes nothing; the real
    import writes the prices, marks the file imported and is audited as `price_import`;
  * Sales Support may send and read, never import or reject; accounting (bom.prices) may do both.
"""
import io
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

import auth
from bom_center import price_submissions as PS
from bom_center import todo as TD
from bom_center.fmt import month_th
from test_bom_center_s11 import udon  # noqa: F401  (branch 1 = อุดรธานี, the centre's September report)
from test_bom_center_s4b import con  # noqa: F401  (schema ensured on a scratch DB)

M = "2026-09"
TODAY = date(2026, 10, 7)
TOKEN = {"X-Upload-Token": "owner-token-xyz"}
CSV = ("รหัส,ชื่อสินค้า,จำนวน,หน่วย,มูลค่ารวม,เดือน\n"
       "ING-PSUB,วัตถุดิบทดสอบไฟล์ราคา,1000,กรัม,5000,2098-03\n").encode("utf-8")


def _xlsx_bytes() -> bytes:
    wb = Workbook()
    wb.active.append(["รหัส", "จำนวน"])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def _by(d):
    return {i["key"].split(":")[0]: i for i in d["items"]}


# ── the file ─────────────────────────────────────────────────────────────────
def test_sniff_accepts_excel_and_csv_only_by_content():
    assert PS.sniff_xlsx(_xlsx_bytes()) == PS.XLSX_MIME
    assert PS.sniff_xlsx(CSV) == PS.CSV_MIME
    assert PS.sniff_xlsx(b"\xef\xbb\xbf" + CSV) == PS.CSV_MIME                # with a BOM
    assert PS.sniff_xlsx("รหัส;จำนวน\n1;2".encode("utf-8")) == PS.CSV_MIME
    assert PS.sniff_xlsx(b"") is None
    assert PS.sniff_xlsx(b"just one word\nsecond,line") is None               # the separator must be in line 1
    assert PS.sniff_xlsx(b"\xff\xd8\xff\xe0\x00\x10JFIF,\x00") is None         # an image
    assert PS.sniff_xlsx(b"%PDF-1.4,\x00\x01") is None


def test_create_rejects_the_wrong_kind_and_the_oversize(con):
    with pytest.raises(ValueError, match="Excel"):
        PS.create(con, M, "a.xlsx", b"\xff\xd8\xff\xe0\x00", None, "ss")
    with pytest.raises(ValueError, match="Excel"):
        PS.create(con, M, "a.xlsx", b"", None, "ss")
    with pytest.raises(ValueError, match="ใหญ่เกิน 12 MB"):
        PS.create(con, M, "big.csv", b"a,b\n" + b"x" * PS.MAX_BYTES, None, "ss")
    assert con.execute("SELECT COUNT(*) FROM bc_price_submission").fetchone()[0] == 0


def test_create_list_and_decide(con):
    out = PS.create(con, M, "ราคา.csv", CSV, " หมายเหตุ ", "salessupport1", {"lines": 1, "priced": 1})
    sid = out["sub_id"]
    rows = PS.listing(con)
    assert len(rows) == 1 and rows[0]["sub_id"] == sid and rows[0]["status"] == "pending"
    assert "data" not in rows[0] and rows[0]["note"] == "หมายเหตุ" and rows[0]["preview"] == {"lines": 1, "priced": 1}
    assert rows[0]["status_th"] == "รอฝ่ายวิเคราะห์ตรวจ" and rows[0]["size"] == len(CSV) and rows[0]["mime"] == PS.CSV_MIME
    assert PS.blob(con, sid) == ("ราคา.csv", PS.CSV_MIME, CSV)                  # the exact bytes
    assert PS.counts(con) == {"pending": 1, "imported": 0, "rejected": 0}
    assert PS.listing(con, status="imported") == [] and len(PS.listing(con, month=M)) == 1 and PS.listing(con, month="2020-01") == []
    with pytest.raises(ValueError, match="ไม่พบ"):
        PS.blob(con, 999999)
    # a rejection needs a reason, and nothing is decided by the failed attempt
    with pytest.raises(ValueError, match="เหตุผล"):
        PS.decide(con, sid, "rejected", "analyst1", "  ")
    assert PS.head(con, sid)["status"] == "pending"
    PS.decide(con, sid, "rejected", "analyst1", "คอลัมน์ไม่ครบ")
    h = PS.head(con, sid)
    assert h["status"] == "rejected" and h["decision_note"] == "คอลัมน์ไม่ครบ" and h["decided_by"] == "analyst1"
    assert h["status_th"] == "ไม่รับ (ส่งกลับ)"
    for st in ("imported", "rejected"):                                        # a decision is final
        with pytest.raises(ValueError, match="พิจารณาแล้ว"):
            PS.decide(con, sid, st, "analyst1", "x")
    s2 = PS.create(con, M, "b.csv", CSV, None, "ss")["sub_id"]
    PS.decide(con, s2, "imported", "analyst1", None, {"written": 1})
    assert PS.head(con, s2)["status"] == "imported" and PS.counts(con) == {"pending": 0, "imported": 1, "rejected": 1}
    assert con.execute("SELECT COUNT(*) FROM bc_audit WHERE action='price_submit'").fetchone()[0] == 2
    assert PS.listing(con, limit=1)[0]["sub_id"] == s2                         # newest first


# ── หน้างาน ───────────────────────────────────────────────────────────────────
def test_todo_items_for_sales_support_and_the_analyst(udon):
    c = udon
    it = _by(TD.build(c, M, None, None, today=TODAY))
    ps = it["price_submit"]
    assert ps["status"] == "todo" and ps["title"] == "ส่งไฟล์ราคาเดือน" + month_th(M) + " ให้ฝ่ายวิเคราะห์"
    assert ps["owner_modules"] == ["supply.ordering"] and "ประสานงานขาย" in ps["owner_th"]
    assert ps["go"]["pane"] == "pricefile" and ps["go"]["anchor"] == "bmcPriceSubmitCard"   # PR-M2: her own page, ไฟล์ราคา
    assert "price_review" not in it
    assert it["price_month"]["owner_th"] == "ฝ่ายวิเคราะห์"
    assert it["price_month"]["detail"] == "นำเข้าจากไฟล์ที่ประสานงานขายส่งมา หรือนำเข้าเอง"
    sid = PS.create(c, M, "a.csv", CSV, None, "salessupport1")["sub_id"]
    it = _by(TD.build(c, M, None, None, today=TODAY))
    assert it["price_submit"]["status"] == "done" and it["price_submit"]["detail"].startswith("ส่งแล้ว 1 ไฟล์ · ล่าสุด")
    assert "salessupport1" in it["price_submit"]["detail"]
    rv = it["price_review"]
    assert rv["status"] == "todo" and rv["count"] == 1 and rv["title"].endswith("(1 ไฟล์)")
    assert rv["owner_modules"] == ["bom.center"] and rv["owner_th"] == "ฝ่ายวิเคราะห์" and rv["go"]["anchor"] == "bmcPriceSubList"
    assert rv["seq"] == 98 and it["price_submit"]["seq"] == 95
    PS.decide(c, sid, "imported", "analyst1")
    assert "price_review" not in _by(TD.build(c, M, None, None, today=TODAY))
    # prices already imported for the month without any file sent: nothing to send
    c.execute("DELETE FROM bc_price_submission")
    c.execute("INSERT INTO bc_ingredient_price(ingredient_code, month, price_per_base, source)"
              " VALUES('13-01-009', ?, 0.2, 'purchase_avg')", (M,))
    assert _by(TD.build(c, M, None, None, today=TODAY))["price_submit"]["status"] == "na"


# ── who may call what ────────────────────────────────────────────────────────
def test_the_permission_matrix():
    sales, analyst = auth.modules_for_role("salessupport"), auth.modules_for_role("analyst")
    kitchen = auth.modules_for_role("innovation")
    pa = auth.path_allowed
    assert pa("POST", "/api/bom-center/prices/submit", sales)
    assert pa("GET", "/api/bom-center/prices/submissions", sales) and pa("GET", "/api/bom-center/prices/submissions/3/file", sales)
    for p in ("/api/bom-center/prices/submissions/3/import", "/api/bom-center/prices/submissions/3/reject",
              "/api/bom-center/prices/import", "/api/bom-center/prices/manual"):
        assert not pa("POST", p, sales), p
    for p in ("/api/bom-center/prices/submit", "/api/bom-center/prices/submissions/3/import",
              "/api/bom-center/prices/submissions/3/reject"):
        assert pa("POST", p, analyst), p
        assert pa("POST", p, {"bom.prices"}), p                                # accounting keeps the price
    assert not pa("POST", "/api/bom-center/prices/submit", kitchen)


# ── over HTTP ────────────────────────────────────────────────────────────────
@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "owner-token-xyz")
    yield


def _login(user):
    import main
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": user, "password": auth.DEFAULT_PASSWORD}).status_code == 200
    return c


def _clean():
    import core
    with core.get_db() as c:
        c.execute("DELETE FROM bc_price_submission")
        c.execute("DELETE FROM bc_ingredient_price WHERE month='2098-03'")
        c.execute("DELETE FROM bc_ingredient_price_branch WHERE month='2098-03'")
        c.execute("DELETE FROM bc_ingredient WHERE code='ING-PSUB'")
        c.commit()


def _db_count(app_db, sql):
    c = sqlite3.connect(str(app_db))
    try:
        return c.execute(sql).fetchone()[0]
    finally:
        c.close()


def test_http_submit_review_import_and_reject(client, app_db, auth_on):
    import core
    _clean()
    try:
        with core.get_db() as c:
            from bom_center.schema import ensure
            ensure(c)
            c.execute("INSERT INTO bc_ingredient(code, name_th, kind, base_unit, active) VALUES('ING-PSUB', 'ทดสอบ', 'raw', 'กรัม', 1)")
            c.commit()
        _http_flow(app_db)
    finally:
        _clean()


def _http_flow(app_db):
    ss, an = _login("salessupport1"), _login("analyst1")
    price_rows = "SELECT COUNT(*) FROM bc_ingredient_price WHERE month='2098-03'"
    files = {"file": ("ราคา.csv", CSV)}
    # sending is a write: the owner token is asked for; a wrong kind or month is refused before anything is kept
    assert ss.post("/api/bom-center/prices/submit", files=files, data={"month": "2098-03"}).headers.get("X-Denied") == "token"
    r = ss.post("/api/bom-center/prices/submit", files={"file": ("x.xlsx", b"\xff\xd8\xff\xe0\x00JFIF")},
                data={"month": "2098-03"}, headers=TOKEN)
    assert r.status_code == 400 and "Excel" in r.json()["detail"]
    r = ss.post("/api/bom-center/prices/submit", files=files, data={"month": "98-3"}, headers=TOKEN)
    assert r.status_code == 400 and "เดือนไม่ถูกต้อง" in r.json()["detail"]
    assert _db_count(app_db, "SELECT COUNT(*) FROM bc_price_submission") == 0
    r = ss.post("/api/bom-center/prices/submit", files=files, data={"month": "2098-03", "note": "ไฟล์เดือนมีนาคม"}, headers=TOKEN)
    assert r.status_code == 200, r.text
    sid = r.json()["sub_id"]
    s = r.json()["summary"]
    assert s["lines"] == 1 and s["priced"] == 1 and s["value_total"] == 5000 and s["issues"] == 0
    assert _db_count(app_db, price_rows) == 0                                   # submitting prices nothing
    # Sales Support reads its list; the bytes only come from /file
    lst = ss.get("/api/bom-center/prices/submissions").json()
    row = next(x for x in lst["submissions"] if x["sub_id"] == sid)
    assert "data" not in row and row["status"] == "pending" and row["note"] == "ไฟล์เดือนมีนาคม" and row["submitted_by"] == "salessupport1"
    assert row["preview"]["priced"] == 1 and lst["counts"]["pending"] == 1
    f = ss.get(f"/api/bom-center/prices/submissions/{sid}/file")
    assert f.status_code == 200 and f.content == CSV and "attachment" in f.headers["content-disposition"]
    assert ss.get("/api/bom-center/prices/submissions/999999/file").status_code == 404
    assert ss.get("/api/bom-center/prices/submissions?status=weird").status_code == 400
    # Sales Support can neither import nor reject, nor use the analyst's own import
    for p, kw in ((f"/api/bom-center/prices/submissions/{sid}/import?dry_run=true", {}),
                  (f"/api/bom-center/prices/submissions/{sid}/reject", {"json": {"note": "x"}, "headers": TOKEN}),
                  ("/api/bom-center/prices/import?dry_run=true", {"files": files})):
        d = ss.post(p, **kw)
        assert d.status_code == 403 and d.headers.get("X-Denied") == "role", p
    # the analyst's preview needs no token and writes nothing
    pv = an.post(f"/api/bom-center/prices/submissions/{sid}/import?dry_run=true")
    assert pv.status_code == 200, pv.text
    assert pv.json()["dry_run"] is True and pv.json()["priced"] == 1 and pv.json()["month"] == "2098-03"
    assert _db_count(app_db, price_rows) == 0
    assert an.get(f"/api/bom-center/prices/submissions?status=pending").json()["submissions"][0]["status"] == "pending"
    # the real import is gated, and a rejection needs a reason
    d = an.post(f"/api/bom-center/prices/submissions/{sid}/import?dry_run=false")
    assert d.status_code == 403 and d.headers.get("X-Denied") == "token"
    d = an.post(f"/api/bom-center/prices/submissions/{sid}/reject", json={"note": " "}, headers=TOKEN)
    assert d.status_code == 400 and "เหตุผล" in d.json()["detail"]
    assert _db_count(app_db, price_rows) == 0
    audits = _db_count(app_db, "SELECT COUNT(*) FROM bc_audit WHERE action='price_import' AND row_key='2098-03'")
    d = an.post(f"/api/bom-center/prices/submissions/{sid}/import?dry_run=false", headers=TOKEN)
    assert d.status_code == 200, d.text
    assert d.json()["submission"] == {"sub_id": sid, "status": "imported"}
    assert _db_count(app_db, price_rows) == 1
    assert _db_count(app_db, "SELECT COUNT(*) FROM bc_audit WHERE action='price_import' AND row_key='2098-03'") == audits + 1
    h = next(x for x in ss.get("/api/bom-center/prices/submissions").json()["submissions"] if x["sub_id"] == sid)
    assert h["status"] == "imported" and h["decided_by"] == "analyst1" and h["status_th"] == "นำเข้าแล้ว"
    # decided: neither a second import nor a rejection is possible
    for p, kw in ((f"/api/bom-center/prices/submissions/{sid}/import?dry_run=true", {}),
                  (f"/api/bom-center/prices/submissions/{sid}/import?dry_run=false", {"headers": TOKEN}),
                  (f"/api/bom-center/prices/submissions/{sid}/reject", {"json": {"note": "x"}, "headers": TOKEN})):
        d = an.post(p, **kw)
        assert d.status_code == 400 and "พิจารณาแล้ว" in d.json()["detail"], p
    # a second file is rejected with a reason Sales Support can read
    r2 = ss.post("/api/bom-center/prices/submit", files=files, data={"month": "2098-03"}, headers=TOKEN)
    sid2 = r2.json()["sub_id"]
    d = an.post(f"/api/bom-center/prices/submissions/{sid2}/reject", json={"note": "ซ้ำกับไฟล์ที่นำเข้าแล้ว"}, headers=TOKEN)
    assert d.status_code == 200, d.text
    h2 = next(x for x in ss.get("/api/bom-center/prices/submissions?status=rejected").json()["submissions"] if x["sub_id"] == sid2)
    assert h2["status"] == "rejected" and h2["decision_note"] == "ซ้ำกับไฟล์ที่นำเข้าแล้ว"
    assert _db_count(app_db, price_rows) == 1
