"""💬 เสียงลูกค้า — the lexicon engine on Thai review text, the upload/summary/
items/delete endpoints, dedupe, and the RBAC module."""
import io
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import feedback_nlp as N  # noqa: E402
from routers.feedback import parse_rows  # noqa: E402

MENUS = ["(B) แซลมอน ซาชิมิ (1 ชิ้น)", "(B) ทุเรียน (1 เม็ด)", "(A) สเต๊ก", "(B) แซลมอนดอง (3 ชิ้น)", "BUFFET"]


@pytest.fixture(autouse=True)
def _clean_feedback_rows(request):
    """The session DB is shared with every other module (the golden pins the
    EMPTY summary): leave no review behind."""
    yield
    if "client" not in request.fixturenames:
        return
    import sqlite3
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    con.execute("CREATE TABLE IF NOT EXISTS customer_feedback(id INTEGER PRIMARY KEY, text TEXT)")
    con.execute("DELETE FROM customer_feedback")
    con.commit(); con.close()
    try:
        import main
        main.invalidate_cache()
    except Exception:
        pass


# ── lexicon ──────────────────────────────────────────────────────────────────
def test_clean_menu_name_strips_prefix_and_portion():
    assert N.clean_menu_name("(B) แซลมอน ซาชิมิ (1 ชิ้น)") == "แซลมอน ซาชิมิ"
    assert N.clean_menu_name("(A) สเต๊ก") == "สเต๊ก"
    assert N.menu_index(MENUS)[0] == "แซลมอน ซาชิมิ"       # longest first


def test_positive_review_with_menu_mention():
    r = N.analyse_one("แซลมอน ซาชิมิสดมาก บริการดี จะกลับมาอีก", None, N.menu_index(MENUS))
    assert r["sentiment"] == "pos" and r["score"] > 0.15
    assert "ความสด" in r["themes"] and "บริการ" in r["themes"]
    assert r["menus"] == ["แซลมอน ซาชิมิ"]


def test_negation_flips_a_positive_word():
    r = N.analyse_one("ทุเรียนไม่สด รอนานมาก พนักงานไม่สนใจ", None, N.menu_index(MENUS))
    assert r["sentiment"] == "neg"
    assert {"ความสด", "รอนาน", "บริการ"} <= set(r["themes"])
    assert r["menus"] == ["ทุเรียน"]


def test_rating_decides_when_the_text_is_silent():
    assert N.analyse_one("มาวันเสาร์", 5)["sentiment"] == "pos"
    assert N.analyse_one("มาวันเสาร์", 1)["sentiment"] == "neg"
    assert N.analyse_one("มาวันเสาร์", None)["sentiment"] == "neu"


def test_analyse_falls_back_to_the_lexicon_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res, engine = N.analyse([{"text": "อร่อยมาก", "rating": None}, {"text": "แพงและไม่คุ้ม", "rating": 2}], MENUS)
    assert engine == "lexicon" and [r["sentiment"] for r in res] == ["pos", "neg"]


def test_analyse_survives_a_failing_claude_call(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no network")
    monkeypatch.setattr(N, "analyse_with_claude", boom)
    res, engine = N.analyse([{"text": "อร่อย", "rating": None}], MENUS, api_key="k")
    assert engine == "lexicon" and res[0]["sentiment"] == "pos"


# ── parsing ──────────────────────────────────────────────────────────────────
def test_parse_rows_reads_a_csv_with_thai_headers_and_pasted_lines():
    csv_bytes = "วันที่,สาขา,ช่องทาง,คะแนน,รีวิว\n2026-08-03,ขอนแก่น,Google,5,อร่อยมาก\n2026-08-04,,Grab,2,รอนาน\n".encode("utf-8-sig")
    rows = parse_rows(text="บรรทัดที่วางเอง\n# comment\n", data=csv_bytes, default_branch="ชลบุรี", default_source="หน้าร้าน", default_date="2026-08-10")
    assert [r["text"] for r in rows] == ["อร่อยมาก", "รอนาน", "บรรทัดที่วางเอง"]
    assert rows[0]["branch"] == "ขอนแก่น" and rows[0]["rating"] == 5.0 and rows[0]["date"] == "2026-08-03"
    assert rows[1]["branch"] == "ชลบุรี"          # default fills the blank
    core = ("date", "branch", "source", "rating", "text")
    assert {k: rows[2][k] for k in core} == {"date": "2026-08-10", "branch": "ชลบุรี", "source": "หน้าร้าน", "rating": None, "text": "บรรทัดที่วางเอง"}
    assert rows[2]["external_id"] is None and rows[2]["sentiment_hint"] is None   # the connector-only fields stay empty


# ── endpoints ────────────────────────────────────────────────────────────────
def test_upload_summary_items_delete_roundtrip(client):
    r = client.post("/api/feedback/upload", json={"by": "tester", "rows": [
        {"date": "2026-06-02", "branch": "BranchA", "source": "Google", "rating": 5, "text": "(B) แซลมอน สดมาก บริการดี"},
        {"date": "2026-06-03", "branch": "BranchA", "source": "Grab", "rating": 1, "text": "รอนานมาก สเต๊กเย็นชืด"},
        {"date": "2026-06-03", "branch": "BranchB", "source": "Facebook", "rating": None, "text": "ราคาแพงไปหน่อยแต่อร่อย"},
    ]})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["inserted"] == 3 and j["duplicates"] == 0 and j["analysed_by"] == "lexicon"
    # a re-upload of the same rows is a no-op
    again = client.post("/api/feedback/upload", json={"rows": [{"date": "2026-06-02", "branch": "BranchA", "source": "Google", "rating": 5, "text": "(B) แซลมอน สดมาก บริการดี"}]}).json()
    assert again["inserted"] == 0 and again["duplicates"] == 1

    s = client.get("/api/feedback/summary", params={"month_from": "2026-06", "month_to": "2026-06"}).json()
    assert s["total"] == 3 and s["sentiment"]["pos"] >= 1 and s["sentiment"]["neg"] >= 1
    assert s["avg_rating"] == 3.0
    assert {b["branch"] for b in s["branches"]} == {"BranchA", "BranchB"}
    assert any(t["theme"] == "รอนาน" for t in s["themes"])
    assert any(m["menu"] == "แซลมอน" for m in s["menus"])
    assert s["recent_negative"] and "รอนาน" in s["recent_negative"][0]["text"]
    only_a = client.get("/api/feedback/summary", params={"branch": "BranchA"}).json()
    assert only_a["total"] == 2

    items = client.get("/api/feedback/items", params={"sentiment": "neg"}).json()["items"]
    assert len(items) == 1 and items[0]["branch_name"] == "BranchA"
    fid = items[0]["id"]
    assert client.delete(f"/api/feedback/{fid}").json() == {"deleted": fid}
    assert client.delete(f"/api/feedback/{fid}").status_code == 404
    assert client.get("/api/feedback/summary").json()["total"] == 2


def test_upload_multipart_text_and_file(client):
    csv_bytes = "date,branch,rating,text\n2026-07-01,BranchB,4,ทุเรียนอร่อย\n".encode("utf-8")
    r = client.post("/api/feedback/upload", data={"text": "บริการช้ามาก", "branch": "BranchA", "source": "หน้าร้าน", "date": "2026-07-02"},
                    files={"file": ("reviews.csv", io.BytesIO(csv_bytes), "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["inserted"] == 2
    items = client.get("/api/feedback/items", params={"month_from": "2026-07", "month_to": "2026-07"}).json()["items"]
    by_text = {i["text"]: i for i in items}
    assert by_text["ทุเรียนอร่อย"]["branch_name"] == "BranchB" and by_text["ทุเรียนอร่อย"]["rating"] == 4
    assert by_text["บริการช้ามาก"]["branch_name"] == "BranchA" and by_text["บริการช้ามาก"]["sentiment"] == "neg"


def test_undated_review_is_stamped_today(client):
    import datetime as dt
    today = (dt.datetime.utcnow() + dt.timedelta(hours=7)).strftime("%Y-%m-%d")
    r = client.post("/api/feedback/upload", json={"rows": [{"text": "วันนี้อร่อยมาก"}]}).json()
    assert r["inserted"] == 1
    items = client.get("/api/feedback/items", params={"month_from": today[:7], "month_to": today[:7]}).json()["items"]
    assert any(i["text"] == "วันนี้อร่อยมาก" and i["rated_at"] == today for i in items)


def test_upload_without_text_is_400(client):
    assert client.post("/api/feedback/upload", json={"rows": []}).status_code == 400


def test_rbac_module_gates_the_feedback_endpoints(client, app_db, monkeypatch):
    import test_auth as TA
    monkeypatch.setenv("OSHINEI_AUTH", "1"); monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret")
    import auth as A
    monkeypatch.setattr(A, "_perm_cache", None, raising=False)
    c = TA._fresh(app_db); TA._login(c, "accounting1")
    assert c.get("/api/feedback/summary").status_code == 403       # accounting has no customer.feedback
    c = TA._fresh(app_db); TA._login(c, "marketing1")
    assert c.get("/api/feedback/summary").status_code == 200       # marketing does
    assert "customer.feedback" in A.MODULES and "customer.feedback" in A.ROLE_MODULES["marketing"]
