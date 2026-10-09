"""The write gate and its preview exception (BoM Center UX overhaul, PR-3).

Invariants, with OSHINEI_SEED_TOKEN set (hosted deploys):
  * a dry_run=true call on any import needs NO token — and writes nothing: every
    bc_* table reads the same before and after;
  * the real call (dry_run=false, or no flag) still needs the X-Upload-Token header;
  * a refusal says why — X-Denied: token — so the page can tell it from a role
    refusal (X-Denied: role, the auth middleware) and never asks for a token, nor
    wipes a good one, because of a permission problem;
  * POST /api/auth/token-check verifies this browser's token without writing, and
    any logged-in role may call it (the branch login included).
"""
import io
import sqlite3

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

import auth

TOKEN = "s3cret-write-token"


def _xlsx(sheets: dict) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, grid in sheets.items():
        ws = wb.create_sheet(name)
        for r in grid:
            ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def _master_file():
    return _xlsx({"Sheet": [["รหัส", "ชื่อวัตถุดิบ", "หน่วยฐาน", "หน่วยรับ", "ขนาดบรรจุ", "สูญเสีย (%)"],
                            ["ING-GATE", "วัตถุดิบทดสอบด่าน", "กรัม", "ถุง", 500, 10]]})


def _price_file():
    return _xlsx({"Use KR1": [
        [None, "บริษัท โอชิเน"], [None, "รายงานจ่ายสินค้าภายใน"], [None, "วันที่จาก  01/09/2569  ถึง  30/09/2569"],
        [None, "เอกสาร", "วันที่เอกสาร", "แผนก", "หมายเหตุ", "ลำดับ", "รหัสสินค้า", "รายละเอียด", "คลังที่", "จำนวน",
         "หน่วยนับ", "ราคาต่อหน่วย", "มูลค่ารวม"],
        [None, "OU6909001", None, None, None, None, None, None, None, None, None, None, None],
        [None, None, None, None, None, 1, "ING-GATE", "วัตถุดิบทดสอบด่าน", 1, 3, "ถุง", 50, 150]]})


def _bc_state(db_path) -> dict:
    """Every bc_* table, every row, in a comparable form."""
    c = sqlite3.connect(str(db_path))
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bc%'")]
    snap = {t: sorted(repr(r) for r in c.execute(f"SELECT * FROM {t}")) for t in tables}
    c.close()
    return snap


def _branch_id(client) -> int:
    me = client.get("/api/bom-center/stock/me").json()
    return me["branches"][0]["branch_id"]


# Every endpoint that previews with dry_run=true: (path, query, how to build the body).
# A builder returning None means no file. Some previews stop early (a 404 when the
# month has no requisition rows, a 400 for a release that does not exist): the point
# is that the GATE let the call through, so anything but 403 passes.
def _cases():
    from test_bom_center_s2 import _cost_rw_workbook
    from test_bom_center_s11 import _report
    from test_bom_center_p15 import _sheet as _semi_sheet
    return [
        ("ingredients/seed", "", None),
        ("ingredients/import", "", _master_file),
        ("prices/import", "", _price_file),
        ("prices/from-requisition", "&month=2026-09", None),
        ("prices/submissions/999999/import", "", None),     # PR-D: the analyst's preview of the file Sales Support sent
        ("recipes/import-cost-rw", "", _cost_rw_workbook),
        ("recipes/import-flat", "&release_id=999999", _master_file),
        ("prep/import-semi", "", _semi_sheet),
        ("prep/import-flat", "", lambda: None),              # the template, downloaded in the test
        ("central/import", "", _report),
        ("central/deliveries", "", _report),
        ("stock/count-import", "&count_date=2026-10-01", lambda: None),   # the template, downloaded in the test
    ]


@pytest.mark.parametrize("path,query,build", _cases(), ids=lambda v: v if isinstance(v, str) else "")
def test_a_preview_needs_no_token_and_writes_nothing(client, monkeypatch, path, query, build):
    import main
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    if path == "prep/import-flat":
        data = client.get("/api/bom-center/prep/template.xlsx").content
    elif path == "stock/count-import":
        bid = _branch_id(client)
        query += f"&branch_id={bid}"
        data = client.get(f"/api/bom-center/stock/count-template.xlsx?branch_id={bid}").content
    else:
        data = build() if build else None
    files = {"file": ("f.xlsx", data)} if data else None
    before = _bc_state(main.DB_PATH)
    r = client.post(f"/api/bom-center/{path}?dry_run=true{query}", files=files)
    assert r.status_code != 403, (path, r.text)
    assert _bc_state(main.DB_PATH) == before, f"{path}: a preview changed a bc_* table"
    # the real call is still gated, and says so
    r2 = client.post(f"/api/bom-center/{path}?dry_run=false{query}", files={"file": ("f.xlsx", data)} if data else None)
    assert r2.status_code == 403, (path, r2.text)
    assert r2.headers.get("X-Denied") == "token"
    assert _bc_state(main.DB_PATH) == before


def test_no_dry_run_flag_means_a_real_write(client, monkeypatch):
    """Fail closed: an import called without the flag is gated like any write."""
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    r = client.post("/api/bom-center/ingredients/seed")
    assert r.status_code == 403 and r.headers.get("X-Denied") == "token"
    r = client.post("/api/bom-center/ingredients/seed?dry_run=maybe")
    assert r.status_code in (403, 422)


def test_the_header_token_still_opens_a_real_call(client, monkeypatch):
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    # a real call that cannot write here (no requisition rows for the month) → 404, not 403
    r = client.post("/api/bom-center/prices/from-requisition?dry_run=false&month=2099-01",
                    headers={"X-Upload-Token": TOKEN})
    assert r.status_code == 404, r.text


def test_plain_writes_are_unchanged(client, monkeypatch):
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    r = client.post("/api/bom-center/units", json=[{"code": "X", "unit": "แท่ง", "factor_to_base": 15}])
    assert r.status_code == 403 and r.headers.get("X-Denied") == "token"


# ── token-check ──────────────────────────────────────────────────────────────
def test_token_check_reports_an_open_gate(client, monkeypatch):
    monkeypatch.delenv("OSHINEI_SEED_TOKEN", raising=False)
    r = client.post("/api/auth/token-check")
    assert r.status_code == 200 and r.json() == {"ok": True, "gated": False}


def test_token_check_verifies_without_writing(client, monkeypatch):
    import main
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    before = _bc_state(main.DB_PATH)
    r = client.post("/api/auth/token-check")
    assert r.status_code == 403 and r.headers.get("X-Denied") == "token"
    r = client.post("/api/auth/token-check", headers={"X-Upload-Token": "nope"})
    assert r.status_code == 403 and r.headers.get("X-Denied") == "token"
    r = client.post("/api/auth/token-check", headers={"X-Upload-Token": TOKEN})
    assert r.status_code == 200 and r.json() == {"ok": True, "gated": True}
    assert _bc_state(main.DB_PATH) == before


# ── role refusals carry their own marker ─────────────────────────────────────
@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    yield


def _login(c, username, password=auth.DEFAULT_PASSWORD):
    return c.post("/api/auth/login", json={"username": username, "password": password})


def test_a_role_refusal_is_marked_role_not_token(client, auth_on, monkeypatch):
    import main
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    c = TestClient(main.app)
    assert _login(c, "branch1").status_code == 200
    # the branch role may not edit unit rules: the middleware refuses before the gate
    r = c.post("/api/bom-center/units", json=[{"code": "X", "unit": "แท่ง", "factor_to_base": 15}],
               headers={"X-Upload-Token": TOKEN})
    assert r.status_code == 403, r.text
    assert r.headers.get("X-Denied") == "role" and r.json().get("code") == "role"
    # what the role may read still reads, and token-check is reachable by this role
    assert c.get("/api/bom-center/stock/me").status_code == 200
    r = c.post("/api/auth/token-check", headers={"X-Upload-Token": TOKEN})
    assert r.status_code == 200 and r.json()["gated"] is True
