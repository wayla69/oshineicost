# -*- coding: utf-8 -*-
"""E5 — AI depth: severity and staff names on every voice (lexicon, and the
Claude batch when a key is set), the auto-pin of severe cases, the weekly
narrative (rules / Claude) and "ask the data" (search / Claude)."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import feedback_nlp as N  # noqa: E402
import notify  # noqa: E402
from routers import feedback_actions as A  # noqa: E402


def _wipe(con, table):
    """Empty a table if it exists — never create a stub that would shadow the real DDL."""
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        con.execute(f"DELETE FROM {table}")


@pytest.fixture(autouse=True)
def _clean(request):
    yield
    if "client" not in request.fixturenames:
        return
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    _wipe(con, "customer_feedback")
    _wipe(con, "feedback_action")
    con.commit(); con.close()
    try:
        import main
        main.invalidate_cache()
    except Exception:
        pass


class _Resp:
    """What urlopen hands back: a context manager with read()."""
    def __init__(self, text):
        self._b = json.dumps({"content": [{"type": "text", "text": text}]}).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_claude(monkeypatch, reply):
    calls = []

    def fake(req, timeout=30):
        calls.append(json.loads(req.data.decode("utf-8")))
        return _Resp(reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False))
    monkeypatch.setattr(N, "_urlopen", fake)
    return calls


def _voices(client, rows):
    body = [{"branch": b, "date": d, "source": "Google", "rating": r, "text": t, "external_id": f"ai:{i}:{b}"}
            for i, (b, d, r, t) in enumerate(rows)]
    r = client.post("/api/feedback/upload", json={"rows": body, "by": "test"})
    assert r.status_code == 200, r.text
    return r.json()


def test_severity_and_staff_lexicon():
    assert N.severity_of("กินแล้วท้องเสียทั้งบ้าน", "neg", 1) == ("high", "ท้องเสีย")
    assert N.severity_of("เจอแมลงสาบในซุป", "neg", 2)[0] == "high"
    assert N.severity_of("คิดเงินเกินมา 200 บาท", "neg", 3) == ("high", "คิดเงินเกิน")
    assert N.severity_of("รอนานมาก พนักงานไม่สนใจ", "neg", 1) == ("med", "")
    assert N.severity_of("รอนิดหน่อย", "neg", 4) == ("low", "")
    assert N.severity_of("แซลมอนสดมาก", "pos", 5) == ("low", "")
    a = N.analyse_one("อร่อยดีแต่กินแล้วอาเจียนทั้งคืน", 4, [])
    assert a["sentiment"] == "neg" and a["severity"] == "high" and a["severity_note"] == "อาเจียน"
    assert N.staff_names("พนักงานชื่อเก่งบริการดีมาก น้องมิ้นท์น่ารัก") == ["เก่ง", "มิ้นท์"]
    assert N.staff_names("คุณ Joy ดูแลดี") == ["Joy"]
    assert N.staff_names("น้องเสิร์ฟชื่อบอลตะคอกใส่ลูกค้า") == ["บอล"]
    assert N.staff_names("พนักงานหน้าบึ้ง ไม่สนใจ น้องๆ บริการดี ขอบคุณคุณลูกค้า") == []
    assert N.analyse_one("พี่แจ๋วเติมซาชิมิเร็วมาก", 5, [])["staff"] == ["แจ๋ว"]


def test_claude_batch_carries_severity_and_staff(monkeypatch):
    calls = _fake_claude(monkeypatch, [{"sentiment": "neg", "score": -0.9, "themes": ["ความสด"], "menus": [],
                                         "severity": "high", "severity_note": "อาหารเป็นพิษ", "staff": ["เก่ง"]},
                                        {"sentiment": "pos", "score": 0.8, "themes": ["บริการ"], "menus": [], "severity": "nope", "staff": "x"}])
    out = N.analyse_with_claude([{"text": "a", "rating": 1}, {"text": "b", "rating": 5}], [], "key")
    assert out[0]["severity"] == "high" and out[0]["severity_note"] == "อาหารเป็นพิษ" and out[0]["staff"] == ["เก่ง"]
    assert out[1]["severity"] == "low" and out[1]["staff"] == []
    assert "severity" in calls[0]["messages"][0]["content"] and calls[0]["model"] == N.CLAUDE_MODEL


def test_severe_voice_pins_itself_and_leads_the_queue(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(A, "_today", lambda: "2026-09-05")
    _voices(client, [("BranchA", "2026-09-03", 1, "รอนานมาก พนักงานไม่สนใจ"),
                     ("BranchA", "2026-09-04", 3, "อร่อยดีแต่กินแล้วท้องเสียทั้งบ้าน พนักงานชื่อเก่งช่วยดีมาก"),
                     ("BranchB", "2026-09-04", 5, "น้องมิ้นท์บริการดีมาก แซลมอนสด")])
    items = client.get("/api/feedback/items", params={"sort": "priority"}).json()["items"]
    top = items[0]
    assert "ท้องเสีย" in top["text"] and top["severity"] == "high" and top["flag"] == 1
    assert top["flag_note"].startswith("อัตโนมัติ: ร้ายแรง") and "ท้องเสีย" in top["flag_note"] and top["staff"] == ["เก่ง"]
    s = client.get("/api/feedback/summary").json()
    assert s["severe"] == 1 and s["flagged"] == 1
    names = {x["name"]: x for x in s["staff"]}
    assert names["เก่ง"]["n"] == 1 and names["มิ้นท์"]["pos"] == 1 and names["มิ้นท์"]["branch"] == "BranchB"
    q = client.get("/api/feedback/items", params={"needs_reply": "1", "sort": "priority"}).json()["items"]
    assert q[0]["severity"] == "high"
    d = client.get("/api/feedback/digest", params={"days": 7, "today": "2026-09-05"}).json()
    assert d["severe"] == 1 and any(x["name"] == "เก่ง" for x in d["staff"])
    assert "🔥 ร้ายแรง <b>1</b>" in notify.weekly_digest(d)
    alert = notify.negative_digest([{"branch": "BranchA", "rating": 3, "text": "ท้องเสีย", "severity": "high", "severity_note": "ท้องเสีย"},
                                    {"branch": "BranchB", "rating": 1, "text": "รอนาน"}], "Google")
    assert alert.index("🔥") < alert.index("รอนาน") and "ร้ายแรง — ท้องเสีย" in alert


def test_narrative_rules_and_claude(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(A, "_today", lambda: "2026-09-05")
    n0 = client.get("/api/feedback/narrative", params={"days": 7, "today": "2026-09-05"}).json()
    assert n0["engine"] == "template" and "ยังไม่มีเสียงลูกค้า" in n0["overview"] and n0["actions"]
    _voices(client, [("BranchA", "2026-09-01", 1, "รอนานมาก พนักงานไม่สนใจ"), ("BranchA", "2026-09-02", 2, "รอคิวนานมาก"),
                     ("BranchA", "2026-09-03", 1, "ของหมดเร็ว"), ("BranchB", "2026-09-04", 5, "บริการดี แซลมอนสดมาก"),
                     ("BranchB", "2026-09-04", 5, "อร่อย"), ("BranchB", "2026-09-05", 4, "ดี"), ("BranchA", "2026-08-27", 5, "ดี")])
    n = client.get("/api/feedback/narrative", params={"days": 7, "today": "2026-09-05"}).json()
    assert n["engine"] == "template" and n["range"] == ["2026-08-30", "2026-09-05"]
    assert "6 เสียง" in n["overview"] and "เชิงลบ 50%" in n["overview"]
    assert "รอนาน" in n["fix"] and "BranchA" in n["fix"] and "BranchB" in n["praise"]
    assert 1 <= len(n["actions"]) <= 3 and any("รอนาน" in a and "BranchA" in a for a in n["actions"])
    # Claude writes it when the key is set; a broken reply falls back to the rules
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    calls = _fake_claude(monkeypatch, {"overview": "สัปดาห์นี้มี 6 เสียง", "fix": "รอนานที่ BranchA", "praise": "BranchB ได้คำชม",
                                       "actions": ["เปิดงานแก้รอนานที่ BranchA", "ตอบทุกเสียงใน 1 วัน", "ชมทีม BranchB", "extra"]})
    n2 = client.get("/api/feedback/narrative", params={"days": 7, "today": "2026-09-05"}).json()
    assert n2["engine"] == "claude" and n2["overview"] == "สัปดาห์นี้มี 6 เสียง" and len(n2["actions"]) == 3
    assert "6" in calls[0]["messages"][0]["content"]
    _fake_claude(monkeypatch, "not json at all")
    assert client.get("/api/feedback/narrative", params={"days": 7, "today": "2026-09-05"}).json()["engine"] == "template"


def test_ask_search_and_claude(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(A, "_today", lambda: "2026-09-05")
    _voices(client, [("BranchA", "2026-09-01", 1, "แซลมอนไม่สด คาว"), ("BranchB", "2026-09-02", 5, "แซลมอนสดมาก อร่อย"),
                     ("BranchA", "2026-09-03", 2, "รอนานมาก"), ("BranchB", "2026-09-04", 4, "น้องมิ้นท์บริการดี")])
    assert client.post("/api/feedback/ask", json={"question": ""}).status_code == 400
    a = client.post("/api/feedback/ask", json={"question": "ลูกค้าบ่นเรื่องแซลมอนสาขาไหนบ้าง", "days": 30}).json()
    assert a["engine"] == "search" and a["matches"] == 2 and a["negative"] == 1 and "แซลมอน" in a["terms"]
    assert "พบ 2 เสียง" in a["answer"] and {c["branch"] for c in a["cites"]} == {"BranchA", "BranchB"}
    a2 = client.post("/api/feedback/ask", json={"question": "ทุเรียน", "days": 30}).json()
    assert a2["matches"] == 0 and "ไม่พบ" in a2["answer"]
    a3 = client.post("/api/feedback/ask", json={"question": "แซลมอน", "days": 30, "branch": "BranchA"}).json()
    assert a3["matches"] == 1 and a3["branch"] == "BranchA"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    ids = [c["id"] for c in a["cites"]]
    calls = _fake_claude(monkeypatch, {"answer": "มี 2 เสียงพูดถึงแซลมอน BranchA บ่นว่าไม่สด", "cite_ids": [ids[0], 999999]})
    a4 = client.post("/api/feedback/ask", json={"question": "แซลมอน", "days": 30}).json()
    assert a4["engine"] == "claude" and a4["answer"].startswith("มี 2 เสียง") and [c["id"] for c in a4["cites"]] == [ids[0]]
    assert "Voices:" in calls[0]["messages"][0]["content"]
