# -*- coding: utf-8 -*-
"""2026-10-31 (owner, "ทำเลย"): the franchise owner sees its own branch's บิลยกเลิก / พิมพ์ซ้ำ bill by bill on ร้านของฉัน › การควบคุม.

  * GET /api/franchise/voids(/bill) (routers/franchise.py) = the management drill-down's level 2 (routers/voids.report_voids),
    reached through the franchise rule: the owner's own branch only (another branch_id is 403, no branch is 403), head office may
    name a branch; the classes come through (ไม่ออกใหม่ · ออกใหม่แล้ว · บิลเปล่า); /api/reports/voids itself stays management-only;
  * the page (js/17-franchise.js): the การควบคุม row is a uiRow2 that opens the list under it, the class chips, the fold, the
    reprint lines fetched from the franchise path only (never /api/reports/voids); the manual and the owner guide say so.
"""
import contextlib
import json
import re
from pathlib import Path

import pytest

import auth
from routers import franchise as F
from routers import voids as V
from test_bom_center_batch2 import _fn
from test_franchise_role import _walk, auth_on, world      # noqa: F401  (fixtures used by name)
from test_voids_drilldown import _seed
from test_mgt_watch import _db

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "js" / "17-franchise.js").read_text(encoding="utf-8")


def test_the_franchise_rule_covers_the_new_path_and_the_report_path_stays_management_only():
    for path in ("/api/franchise/voids", "/api/franchise/voids/bill"):
        assert auth.path_allowed("GET", path, auth.ROLE_MODULES["franchise"]), path
        assert auth.path_allowed("GET", path, auth.ROLE_MODULES["management"]), path
        for role in ("branch", "analyst", "accounting"):
            assert not auth.path_allowed("GET", path, auth.ROLE_MODULES[role]), (role, path)
    for role in ("franchise", "branch"):
        assert not auth.path_allowed("GET", "/api/reports/voids", auth.ROLE_MODULES[role]), role
    # no write rule opened: the owner's only writes stay the messages and the push devices
    for m in ("POST", "PUT", "DELETE"):
        assert not auth.path_allowed(m, "/api/franchise/voids", auth.ROLE_MODULES["franchise"]), m


def test_the_endpoint_is_level_2_of_the_owners_branch_with_the_classes(tmp_path, monkeypatch):
    con, ids = _db(tmp_path)
    _seed(con)
    con.execute("INSERT INTO pos_cancel_bill_detail VALUES(1,'2026-06','2026-06-03T20:00:00','C-Z',2,0,'cashier',0)")   # a ฿0 void = บิลเปล่า
    con.commit()
    monkeypatch.setattr(V, "get_db", lambda: contextlib.nullcontext(con))
    monkeypatch.setattr(F, "get_db", lambda: contextlib.nullcontext(con))
    monkeypatch.setattr(F, "_resolve_branch", lambda con_, request, bid: (1, ids[1]))
    r = F.franchise_voids(None, month="2026-06", branch_id=None)
    j = json.loads(r.body) if hasattr(r, "body") else r            # core.ttl_cache hands the route a pre-serialised Response
    assert j["available"] and j["level"] == 2 and j["branch"] == ids[1] and j["window"] == "month"
    by = {b["at"]: b for b in j["bills"]}
    assert by["2026-06-03T20:00:00"]["zero"] is True and by["2026-06-03T20:00:00"]["reissued_as"] is None
    assert by["2026-06-02T12:00:00"]["zero"] is False
    assert j["header"]["voids_open"] == 2 and j["header"]["voids"] == 3          # the ฿0 bill is out of the alert's count
    assert j["reissue_min"] == 30 and "bills_more" in j and "reprints" in j


def test_over_http_the_owner_reads_its_own_branch_only(world):
    b = world["b"]
    r = b.get("/api/franchise/voids", params={"month": "2026-06"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["available"] is False or (j["level"] == 2 and j["branch"] == "BranchB")
    assert b.get("/api/franchise/voids", params={"branch_id": 1}).status_code == 403
    assert b.get("/api/franchise/voids/bill", params={"receipt": "X", "branch_id": 1}).status_code == 403
    assert world["nobranch"].get("/api/franchise/voids").status_code == 403
    assert b.get("/api/reports/voids", params={"month": "2026-06"}).status_code == 403          # the management path stays shut
    for s in _walk(j):
        assert "BranchA" not in s[2]


def test_the_page_opens_the_list_from_the_controls_row_with_the_classes():
    assert "_frcVoidRow(V)" in _fn(JS, "_frcControls") and 'id="frcVoidList" hidden' in _fn(JS, "_frcControls")
    row = _fn(JS, "_frcVoidRow")
    assert "uiRow2(" in row and "onGo: 'frcVoidsOpen()'" in row and 'id="frcCtlVoids"' in row       # the ควรดู row still lands here
    assert "'/api/franchise/voids'" in _fn(JS, "frcVoidsLoad") and "'/api/franchise/voids/bill?'" in _fn(JS, "frcVoidBill")
    assert "/api/reports/voids" not in JS
    html = _fn(JS, "_frcVoidsHtml")
    for t in ("ไม่ออกใหม่", "บิลเปล่า", "ออกใหม่แล้ว", "ทั้งหมด"):
        assert t in JS[JS.index("const _FRC_VCLS"):JS.index("const _FRC_VCLS") + 200], t
    assert "ms-vd-cls is-open" in html and "บิลเปล่า ฿0" in html and "frcVoidBill(" in html
    assert re.search(r"box\.hidden = true", _fn(JS, "frcVoidsOpen"))                                   # a second tap folds it


def test_the_manual_and_the_owner_guide_say_so():
    man = (ROOT / "tabs" / "47-manual.html").read_text(encoding="utf-8")
    art = man[man.index('id="mt-own-month"'):]
    art = art[:art.index("</article>")]
    assert "บิลยกเลิก" in art and "บิลเปล่า" in art and "ดูรายบิล" in art
    assert "บิลยกเลิกรายบิลบนร้านของฉัน" in man
    guide = (ROOT / "docs" / "pilot" / "owner_guide.html").read_text(encoding="utf-8")
    assert "บิลเปล่า" in guide and "ไม่ออกใหม่" in guide
