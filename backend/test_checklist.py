# -*- coding: utf-8 -*-
"""The go-live checklist: the server-owned plan, shared ticks attributed to
the login, task status/notes, the completion rule (every step, or a 400 that
names what is missing), the lock after sign-off and the management reopen."""
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

from routers import checklist as C  # noqa: E402

NAME = "feedback_rollout"


@pytest.fixture(autouse=True)
def _clean(request):
    yield
    if "client" not in request.fixturenames:
        return
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    for t in ("checklist_state", "checklist_log"):
        con.execute(f"CREATE TABLE IF NOT EXISTS {t}(id INTEGER PRIMARY KEY)")
        con.execute(f"DELETE FROM {t}")
    con.commit(); con.close()
    try:
        import main
        main.invalidate_cache()
    except Exception:
        pass


def test_plan_file_is_the_source_and_is_well_formed():
    plan = C.load_plan(NAME)
    steps, tasks = C.step_index(plan), C.task_index(plan)
    assert plan["name"] == NAME and plan["default_start"] and plan["horizon_days"] > 0 and set(plan["statuses"]) == {"todo", "doing", "blocked", "done"}
    assert len(plan["phases"]) == 10 and len(tasks) == 28 and len(steps) == 80
    assert len(set(steps)) == 80 and all(s.startswith("s") for s in steps)      # ids unique
    for ph in plan["phases"]:
        assert ph["days"][0] < ph["days"][1] and ph["owner"] and ph["why"]
        for t in ph["tasks"]:
            for s in t["steps"]:
                assert s["t"] and s.get("how") and s.get("check")                 # every step tells how and what "done" looks like
    with pytest.raises(Exception):
        C.load_plan("../etc")


def test_ticks_tasks_meta_and_the_completion_rule(client):
    assert client.get(f"/api/checklist/{NAME}/plan").json()["phases"][0]["id"] == "p0"
    s0 = client.get(f"/api/checklist/{NAME}").json()
    assert s0["progress"]["total"] == 80 and s0["progress"]["done"] == 0 and s0["locked"] is False and s0["complete"] is None
    assert s0["meta"]["start"] == "2026-09-07" and s0["log"] == []
    # the sign-off is refused while anything is open, and says what
    r = client.post(f"/api/checklist/{NAME}/complete", json={"by": "owner"})
    assert r.status_code == 400 and r.json()["detail"]["progress"]["done"] == 0 and len(r.json()["detail"]["missing"]) == 50
    assert r.json()["detail"]["missing"][0]["id"] == "s0101" and "ยังไม่ครบ" in r.json()["detail"]["message"]
    # a tick (auth off → the client's name is recorded)
    r = client.post(f"/api/checklist/{NAME}/step/s0101", json={"done": True, "by": "เก่ง"}).json()
    assert r["done"] is True and r["by"] == "เก่ง" and r["progress"]["done"] == 1
    assert client.post(f"/api/checklist/{NAME}/step/nope", json={"done": True}).status_code == 404
    # task status + note, kept independently
    assert client.post(f"/api/checklist/{NAME}/task/t01", json={"status": "doing", "by": "เก่ง"}).json()["status"] == "doing"
    assert client.post(f"/api/checklist/{NAME}/task/t01", json={"note": "คุณเอ ดูแลระบบ", "by": "เก่ง"}).json()["status"] == "doing"
    assert client.post(f"/api/checklist/{NAME}/task/t01", json={"status": "bogus"}).status_code == 400
    assert client.post(f"/api/checklist/{NAME}/meta", json={"start": "2026-09-14", "by": "เก่ง"}).json()["start"] == "2026-09-14"
    assert client.post(f"/api/checklist/{NAME}/meta", json={"start": "soon"}).status_code == 400
    s1 = client.get(f"/api/checklist/{NAME}").json()
    assert s1["steps"]["s0101"]["done"] is True and s1["steps"]["s0101"]["by"] == "เก่ง"
    assert s1["tasks"]["t01"] == {**s1["tasks"]["t01"], "status": "doing", "note": "คุณเอ ดูแลระบบ"} and s1["meta"]["start"] == "2026-09-14"
    assert [l["action"] for l in s1["log"]][:4] == ["start", "note", "status:doing", "tick"]
    # untick keeps the record
    client.post(f"/api/checklist/{NAME}/step/s0101", json={"done": False, "by": "เก่ง"})
    assert client.get(f"/api/checklist/{NAME}").json()["progress"]["done"] == 0
    # tick everything → complete succeeds, locks, and further edits are refused until reopened
    plan = client.get(f"/api/checklist/{NAME}/plan").json()
    for ph in plan["phases"]:
        for t in ph["tasks"]:
            for s in t["steps"]:
                assert client.post(f"/api/checklist/{NAME}/step/{s['id']}", json={"done": True, "by": "ทีม"}).status_code == 200
    assert client.get(f"/api/checklist/{NAME}").json()["progress"] == {"done": 80, "total": 80, "pct": 100, "missing": []}
    r = client.post(f"/api/checklist/{NAME}/complete", json={"by": "owner"}).json()
    assert r["complete"]["done"] is True and r["complete"]["by"] == "owner" and r["already"] is False
    s2 = client.get(f"/api/checklist/{NAME}").json()
    assert s2["locked"] is True and s2["complete"]["by"] == "owner" and s2["log"][0]["action"] == "complete" and s2["log"][0]["detail"] == "80/80"
    assert client.post(f"/api/checklist/{NAME}/step/s0101", json={"done": False}).status_code == 409
    assert client.post(f"/api/checklist/{NAME}/task/t01", json={"status": "todo"}).status_code == 409
    assert client.post(f"/api/checklist/{NAME}/meta", json={"start": "2026-10-01"}).status_code == 409
    assert client.post(f"/api/checklist/{NAME}/complete", json={}).json()["already"] is True
    r = client.post(f"/api/checklist/{NAME}/reopen", json={"by": "owner", "reason": "ลืมสาขาใหม่"}).json()
    assert r["reopened"] is True
    s3 = client.get(f"/api/checklist/{NAME}").json()
    assert s3["locked"] is False and s3["complete"] is None and s3["log"][0]["action"] == "reopen" and s3["log"][0]["detail"] == "ลืมสาขาใหม่"
    assert client.post(f"/api/checklist/{NAME}/step/s0101", json={"done": False}).status_code == 200
    assert client.post(f"/api/checklist/{NAME}/reopen", json={}).json() == {"reopened": False, "locked": False}


def test_login_names_the_ticker_and_gates_the_reopen(client, app_db, monkeypatch):
    import test_auth as TA
    monkeypatch.setenv("OSHINEI_AUTH", "1"); monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret")
    import auth as A
    monkeypatch.setattr(A, "_perm_cache", None, raising=False)
    c = TA._fresh(app_db); TA._login(c, "accounting1")
    assert c.get(f"/api/checklist/{NAME}").status_code == 403                  # no customer.feedback module
    m = TA._fresh(app_db); TA._login(m, "marketing1")
    r = m.post(f"/api/checklist/{NAME}/step/s0102", json={"done": True, "by": "spoofed"}).json()
    assert r["by"] == "marketing1"                                            # the session names the ticker, never the body
    plan = m.get(f"/api/checklist/{NAME}/plan").json()
    for ph in plan["phases"]:
        for t in ph["tasks"]:
            for s in t["steps"]:
                m.post(f"/api/checklist/{NAME}/step/{s['id']}", json={"done": True})
    assert m.post(f"/api/checklist/{NAME}/complete", json={}).json()["complete"]["by"] == "marketing1"
    assert m.post(f"/api/checklist/{NAME}/reopen", json={}).status_code == 403     # marketing cannot reopen
    boss = TA._fresh(app_db); TA._login(boss, "management1")
    assert boss.post(f"/api/checklist/{NAME}/reopen", json={"reason": "x"}).json()["reopened"] is True
    assert boss.get(f"/api/checklist/{NAME}").json()["log"][0]["by"] == "management1"
