# -*- coding: utf-8 -*-
"""scripts/branch_list.py, branch_handout.py, branch_accounts.py (PR-I, 30-branch onboarding):
the list reader, the handout files, and the account plan / creation against a stand-in API
and against the real app. No password or token is ever printed."""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

import auth

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


BL = _load("branch_list")
HO = _load("branch_handout")
AC = _load("branch_accounts")
PW = auth.DEFAULT_PASSWORD


def _csv(tmp_path, text, name="list.csv"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8-sig")
    return p


# ── the list ─────────────────────────────────────────────────────────────────
def test_the_template_round_trips_with_the_five_pilot_rows(tmp_path):
    p = BL.write_template(tmp_path / "branch_list_template.xlsx")
    rows = BL.read_list(p)
    assert [(r["branch"], r["abbr"]) for r in rows] == list(BL.PILOT_ABBRS.items())
    assert [BL.login_of(r) for r in rows] == list(BL.PILOT_USERNAMES.values()) == ["wh_chiangmai", "wh_ubon", "wh_udon", "wh_korat", "wh_surin"]
    assert all(r["recipient"] == "" and r["username"] == "" for r in rows)
    assert list(BL.LEGACY_PILOT_USERNAMES.values()) == ["branch_ubon", "branch_udon", "branch_khonkaen", "branch_rayong"]
    assert "ขอนแก่น" not in BL.PILOT_ABBRS and "ระยอง" not in BL.PILOT_ABBRS      # P5: only the retired logins remember them


def test_csv_list_with_an_abbreviation_a_blank_username_and_a_recipient(tmp_path):
    p = _csv(tmp_path, "สาขา,ตัวย่อ,ชื่อผู้ใช้,LINE/ผู้รับ\nชลบุรี,CBI,,กลุ่มไลน์ชลบุรี\nพัทยา,,branch_pty,\n\n")
    rows = BL.read_list(p)
    assert [r["branch"] for r in rows] == ["ชลบุรี", "พัทยา"]
    assert rows[0]["abbr"] == "cbi" and rows[0]["username"] == "" and rows[0]["recipient"] == "กลุ่มไลน์ชลบุรี"     # lower-cased
    assert BL.login_of(rows[0]) == "wh_cbi" and BL.login_of(rows[1]) == "branch_pty"             # an explicit username wins


@pytest.mark.parametrize("text,msg", [
    ("สาขา,ชื่อผู้ใช้\nชลบุรี,ชลบุรี\n", "ชื่อผู้ใช้"),                       # a Thai login name is refused
    ("สาขา,ชื่อผู้ใช้\nชลบุรี,aa\n", "3–40"),
    ("สาขา,ชื่อผู้ใช้\nชลบุรี,x_one\nชลบุรี,x_two\n", "ซ้ำ"),               # the same branch twice
    ("สาขา,ชื่อผู้ใช้\nชลบุรี,x_one\nพัทยา,x_one\n", "ซ้ำ"),                # the same login for two branches
    ("สาขา,ตัวย่อ\nชลบุรี,cb i\n", "ตัวย่อ"),                                # an abbreviation is a-z 0-9 only
    ("สาขา,ตัวย่อ\nชลบุรี,c\n", "2–12"),
    ("สาขา,ตัวย่อ\nชลบุรี,cbi\nพัทยา,CBI\n", "ซ้ำ"),                         # the same abbreviation twice
])
def test_bad_lists_are_refused_with_a_plain_message(tmp_path, text, msg):
    with pytest.raises(SystemExit) as e:
        BL.read_list(_csv(tmp_path, text))
    assert msg in str(e.value)


def test_a_file_without_the_branch_column_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        BL.read_list(_csv(tmp_path, "a,b\n1,2\n"))


def test_username_for():
    assert BL.username_for("อุดรธานี", 25) == "wh_udon"               # the pilot abbreviation is built in
    assert BL.username_for("ชลบุรี", 1, "CBI") == "wh_cbi"            # wh_<ตัวย่อ>
    assert BL.username_for("ชลบุรี", 1) == "wh_1"                      # no abbreviation: the branch id
    assert BL.username_for("ชลบุรี") is None                          # no id, no guess


# ── the handout ──────────────────────────────────────────────────────────────
def test_handout_from_a_list_writes_messages_and_four_cards_a_page(tmp_path, capsys):
    names = [f"สาขาทดสอบ{i}" for i in range(1, 7)]
    p = _csv(tmp_path, "สาขา,ตัวย่อ,LINE/ผู้รับ\n" + "\n".join(f"{n},t{i},กลุ่ม{i}" for i, n in enumerate(names, 1)))
    out = tmp_path / "out"
    HO.main(["--list", str(p), "--out", str(out)])
    shown = capsys.readouterr().out
    assert PW not in shown                                            # the password is in the files, never on the screen
    for i, n in enumerate(names, 1):
        txt = (out / f"wh_t{i}.txt").read_text(encoding="utf-8")
        assert n in txt and f"ชื่อผู้ใช้: wh_t{i}" in txt and PW in txt
        assert "13:15" in txt and "ตัดเบิก" in txt
    cards = (out / "cards.html").read_text(encoding="utf-8")
    assert cards.count('<section class="card">') == 6 and cards.count('<div class="sheet">') == 2     # 4 + 2
    readme = (out / "README.txt").read_text(encoding="utf-8")
    assert "6 สาขา" in readme and "กลุ่ม3" in readme and "2 หน้า" in readme


def test_handout_without_options_keeps_the_four_pilot_branches(tmp_path):
    HO.main(["--out", str(tmp_path)])
    assert {f.name for f in tmp_path.glob("*.txt") if f.name != "README.txt"} == {f"{u}.txt" for u in BL.PILOT_USERNAMES.values()}
    HO.main(["--out", str(tmp_path / "one"), "--only", "udon"])
    assert [f.name for f in (tmp_path / "one").glob("wh_*.txt")] == ["wh_udon.txt"]


def test_handout_template_option(tmp_path):
    HO.main(["--template", "--out", str(tmp_path)])
    assert (tmp_path / "branch_list_template.xlsx").exists() and not list(tmp_path.glob("*.txt"))


def _db(tmp_path, rows):
    p = tmp_path / "copy.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE branches(branch_id INTEGER PRIMARY KEY, name TEXT, active INTEGER DEFAULT 1)")
    c.executemany("INSERT INTO branches(branch_id, name, active) VALUES(?,?,?)", rows)
    c.commit()
    c.close()
    return p


def test_handout_all_uses_the_database_copy_and_branch_ids(tmp_path):
    db = _db(tmp_path, [(1, "ชลบุรี", 1), (2, "สุรินทร์", 1), (3, "ปิดแล้ว", 0), (4, "ALL", 1)])
    HO.main(["--all", "--db", str(db), "--out", str(tmp_path / "o")])
    assert sorted(f.name for f in (tmp_path / "o").glob("wh_*.txt")) == ["wh_1.txt", "wh_surin.txt"]
    with pytest.raises(SystemExit):
        HO.main(["--all", "--out", str(tmp_path / "o2")])             # --all needs the copy


def test_a_blank_username_needs_the_branch_id(tmp_path):
    p = _csv(tmp_path, "สาขา,ชื่อผู้ใช้\nชลบุรี,\n")
    with pytest.raises(SystemExit) as e:
        HO.main(["--list", str(p), "--out", str(tmp_path / "o")])
    assert "ชลบุรี" in str(e.value) and "--db" in str(e.value)
    HO.main(["--list", str(p), "--db", str(_db(tmp_path, [(7, "ชลบุรี", 1)])), "--out", str(tmp_path / "o")])
    assert (tmp_path / "o" / "wh_7.txt").exists()


def test_handout_skips_failed_and_existing_rows_of_an_accounts_result(tmp_path, capsys):
    p = _csv(tmp_path, "สาขา,ชื่อผู้ใช้,LINE/ผู้รับ,ผล,หมายเหตุ\nก,branch_a1,,สร้างแล้ว,\nข,branch_b1,,มีอยู่แล้ว,\nค,branch_c1,,ไม่สำเร็จ,x\n")
    HO.main(["--list", str(p), "--out", str(tmp_path / "o")])
    assert [f.name for f in (tmp_path / "o").glob("branch_*.txt")] == ["branch_a1.txt"]
    assert "ข" in capsys.readouterr().out                             # the skip is reported
    HO.main(["--list", str(p), "--include-existing", "--out", str(tmp_path / "o2")])
    assert sorted(f.name for f in (tmp_path / "o2").glob("branch_*.txt")) == ["branch_a1.txt", "branch_b1.txt"]


# ── the accounts plan ────────────────────────────────────────────────────────
BRANCHES = {"ชลบุรี": 1, "ระยอง": 2, "อุดรธานี": 25, "พัทยา": 12, "ขอนแก่น": 30}
USERS = [{"username": "branch_udon", "role": "branch", "active": 1, "branch": "อุดรธานี"},
         {"username": "branch_old", "role": "branch", "active": 1, "branch": "พัทยา"},
         {"username": "management1", "role": "management", "active": 1, "branch": None}]


def _row(b, u=""):
    return {"branch": b, "username": u, "recipient": ""}


def test_plan_decisions():
    plan = {p["branch"]: p for p in AC.plan_rows(
        [_row("ชลบุรี"), _row("อุดรธานี"), _row("พัทยา", "branch_pty"), _row("ไม่มีสาขานี้"), _row("ระยอง", "management1")],
        BRANCHES, USERS)}
    assert plan["ชลบุรี"]["action"] == "create" and plan["ชลบุรี"]["username"] == "wh_1"
    assert plan["อุดรธานี"]["action"] == "exists" and plan["อุดรธานี"]["username"] == "branch_udon"       # no second login without --replace-pilot
    assert plan["พัทยา"]["action"] == "exists" and plan["พัทยา"]["username"] == "branch_old"      # no second login for a branch
    assert plan["ไม่มีสาขานี้"]["action"] == "fail" and "ไม่พบสาขา" in plan["ไม่มีสาขานี้"]["note"]
    assert plan["ระยอง"]["action"] == "fail" and "ถูกใช้" in plan["ระยอง"]["note"]            # the name belongs to another account


class FakeApi:
    def __init__(self, post_status=200):
        self.calls, self.post_status = [], post_status

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/api/branches":
            return 200, [{"id": i, "name": n, "active": True} for n, i in BRANCHES.items()], ""
        if path == "/api/auth/users" and method == "GET":
            return 200, USERS, ""
        if path == "/api/auth/users":
            return self.post_status, ({"ok": True} if self.post_status == 200 else {"detail": "ข้อความจากระบบ"}), ""
        return 404, None, ""


def test_a_dry_run_posts_nothing():
    api = FakeApi()
    plan = AC.run(api, [_row("ชลบุรี"), _row("อุดรธานี")], apply=False)
    assert [p["result"] for p in plan] == [BL.R_PLAN, BL.R_EXISTS]
    assert not [c for c in api.calls if c[0] == "POST"]


def test_apply_creates_a_branch_login_without_sending_a_password():
    api = FakeApi()
    plan = AC.run(api, [_row("ชลบุรี")], apply=True)
    assert plan[0]["result"] == BL.R_CREATED
    posts = [c for c in api.calls if c[0] == "POST"]
    assert posts == [("POST", "/api/auth/users", {"username": "wh_1", "role": "branch", "branch": "ชลบุรี"})]    # no password field


def test_409_counts_as_existing_and_other_errors_as_failed():
    assert AC.run(FakeApi(409), [_row("ชลบุรี")], apply=True)[0]["result"] == BL.R_EXISTS
    p = AC.run(FakeApi(500), [_row("ชลบุรี")], apply=True)[0]
    assert p["result"] == BL.R_FAILED and "ข้อความจากระบบ" in p["note"]


def test_summary_table_and_result_file(tmp_path, capsys):
    plan = AC.run(FakeApi(), [_row("ชลบุรี"), _row("ไม่มี")], apply=True)
    AC.print_table(plan)
    shown = capsys.readouterr().out
    assert "สร้างแล้ว 1" in shown and "ไม่สำเร็จ 1" in shown and PW not in shown
    out = BL.write_result(tmp_path / "accounts_result.csv", plan)
    assert [r["result"] for r in BL.read_list(out)] == [BL.R_CREATED, BL.R_FAILED]       # the handout can read it back


def test_a_rerun_keeps_the_created_mark_so_the_handout_still_covers_them(tmp_path):
    dest = tmp_path / "accounts_result.csv"
    BL.write_result(dest, [{"branch": "ชลบุรี", "username": "wh_1", "result": BL.R_CREATED},
                           {"branch": "ระยอง", "username": "wh_rayong", "result": BL.R_EXISTS}])
    plan = [{"branch": "ชลบุรี", "username": "wh_1", "result": BL.R_EXISTS, "note": ""},
            {"branch": "ระยอง", "username": "wh_rayong", "result": BL.R_EXISTS, "note": ""},
            {"branch": "พัทยา", "username": "wh_12", "result": BL.R_EXISTS, "note": ""}]
    AC.keep_created(plan, dest)
    assert [p["result"] for p in plan] == [BL.R_CREATED, BL.R_EXISTS, BL.R_EXISTS]


def test_yes_and_dry_run_together_are_refused(tmp_path):
    p = BL.write_template(tmp_path / "l.xlsx")
    assert AC.main(["--list", str(p), "--yes", "--dry-run"]) == 2
    assert AC.main(["--list", str(p), "--replace-pilot"]) == 2          # --replace-pilot is only ever combined with --yes


# ── PR-K: the four pilot logins are recreated as wh_* and the old branch_* ones switched off ──────────
class RecordingApi(FakeApi):
    """FakeApi that remembers who exists, so a second run sees what the first one created."""

    def __init__(self, users):
        super().__init__()
        self.users = [dict(u) for u in users]

    def request(self, method, path, body=None):
        if path == "/api/auth/users" and method == "GET":
            self.calls.append((method, path, body))
            return 200, self.users, ""
        if path == "/api/auth/users" and method == "POST":
            self.calls.append((method, path, body))
            self.users.append({"username": body["username"], "role": body["role"], "branch": body["branch"], "active": 1})
            return 200, {"ok": True}, ""
        if path.startswith("/api/auth/users/") and method == "PUT":
            self.calls.append((method, path, body))
            next(u for u in self.users if u["username"] == path.rsplit("/", 1)[1]).update(active=int(body["active"]))
            return 200, {"ok": True}, ""
        return super().request(method, path, body)


def test_replace_pilot_creates_wh_first_and_retires_the_old_login_on_the_next_run():
    users = [{"username": "branch_udon", "role": "branch", "active": 1, "branch": "อุดรธานี"},
             {"username": "branch_khonkaen", "role": "branch", "active": 1, "branch": "ขอนแก่น"}]
    rows = [{"branch": "อุดรธานี", "abbr": "udon", "username": "", "recipient": ""},
            {"branch": "ขอนแก่น", "abbr": "khonkaen", "username": "", "recipient": ""},
            {"branch": "ชลบุรี", "abbr": "cbi", "username": "", "recipient": ""}]
    api = RecordingApi(users)
    plan = AC.run(api, rows, apply=False, replace_pilot=True)                     # the plan changes nothing
    assert [p["result"] for p in plan] == [BL.R_PLAN] * 3 and plan[0]["retire"] == ["branch_udon"]
    assert not [c for c in api.calls if c[0] in ("POST", "PUT")]
    plan = AC.run(api, rows, apply=True, replace_pilot=True)                      # run 1: wh_* created, nothing retired yet
    assert [p["result"] for p in plan] == [BL.R_CREATED] * 3
    assert [p["username"] for p in plan] == ["wh_udon", "wh_khonkaen", "wh_cbi"]
    assert not [c for c in api.calls if c[0] == "PUT"]
    assert all(u["active"] for u in api.users)
    plan = AC.run(api, rows, apply=True, replace_pilot=True)                      # run 2 (after the new handout): old logins off
    assert sorted(c[1] for c in api.calls if c[0] == "PUT") == ["/api/auth/users/branch_khonkaen", "/api/auth/users/branch_udon"]
    assert all(c[2] == {"active": False} for c in api.calls if c[0] == "PUT")      # switched off, never deleted
    assert {u["username"]: u["active"] for u in api.users}["branch_udon"] == 0
    assert {u["username"]: u["active"] for u in api.users}["wh_udon"] == 1
    n = len([c for c in api.calls if c[0] == "PUT"])
    AC.run(api, rows, apply=True, replace_pilot=True)                             # run 3: nothing left to retire
    assert len([c for c in api.calls if c[0] == "PUT"]) == n


def test_without_replace_pilot_the_old_logins_are_left_alone():
    users = [{"username": "branch_udon", "role": "branch", "active": 1, "branch": "อุดรธานี"}]
    api = RecordingApi(users)
    plan = AC.run(api, [{"branch": "อุดรธานี", "abbr": "udon", "username": "", "recipient": ""}], apply=True)
    assert plan[0]["result"] == BL.R_EXISTS and plan[0]["username"] == "branch_udon"
    assert not [c for c in api.calls if c[0] in ("POST", "PUT")]


# ── against the real app (logins off in the test client, so sign-in is skipped) ────────────────
class _ClientApi:
    def __init__(self, client):
        self.c = client

    def request(self, method, path, body=None):
        r = self.c.request(method, path, json=body)
        try:
            js = r.json()
        except ValueError:
            js = None
        return r.status_code, js, r.text


def test_creating_logins_against_the_app(client, app_db):
    import main
    names = ["ทดสอบสาขาใหม่ก", "ทดสอบสาขาใหม่ข"]                           # two new branches: no login can exist for them yet
    rows = [_row(names[0], "pi_script_a"), _row(names[1], "pi_script_b")]
    c = sqlite3.connect(str(main.DB_PATH))
    c.executemany("INSERT INTO branches(branch_id, name, active) VALUES(?,?,1)", [(9101, names[0]), (9102, names[1])])
    c.commit()
    try:
        plan = AC.run(_ClientApi(client), rows, apply=True)
        assert [p["result"] for p in plan] == [BL.R_CREATED, BL.R_CREATED]
        again = AC.run(_ClientApi(client), rows, apply=True)               # a second run changes nothing
        assert [p["result"] for p in again] == [BL.R_EXISTS, BL.R_EXISTS]
        got = c.execute("SELECT role, branch, must_change FROM app_user WHERE username='pi_script_a'").fetchone()
        assert got == ("branch", names[0], 1)                              # a branch login that must change the default password
        assert auth.verify_password(PW, *c.execute("SELECT pw_hash, pw_salt FROM app_user WHERE username='pi_script_a'").fetchone())
    finally:
        c.execute("DELETE FROM app_user WHERE username LIKE 'pi_script_%'")
        c.execute("DELETE FROM branches WHERE branch_id IN (9101, 9102)")
        c.commit()
        c.close()


# ── PR-L: the franchise owners (own_<ตัวย่อ>, role franchise) from the same list ───────────────
def test_owner_logins_are_own_prefix_and_ignore_a_branch_username_exception():
    assert BL.username_for("ชลบุรี", 1, "CBI", "franchise") == "own_cbi" and BL.username_for("ชลบุรี", 1, "", "franchise") == "own_1"
    assert BL.username_for("อุดรธานี", 25, "", "franchise") == "own_udon"
    assert BL.username_for("ชลบุรี", 1, "CBI") == "wh_cbi"                                  # the branch default is untouched
    row = {"branch": "พัทยา", "abbr": "pty", "username": "wh_special"}
    assert BL.login_of(row) == "wh_special" and BL.login_of(row, "franchise") == "own_pty"    # the column is the branch exception
    assert BL.login_of({"branch": "พัทยา", "abbr": "", "username": "own_pattaya"}, "franchise") == "own_pattaya"
    assert BL.login_of({"branch": "พัทยา"}, "franchise") == ""


def test_owner_plan_counts_only_franchise_logins_and_creates_one_per_branch():
    users = USERS + [{"username": "own_old", "role": "franchise", "active": 1, "branch": "ระยอง"}]
    rows = [{"branch": "ชลบุรี", "abbr": "cbi", "username": "wh_cbi", "recipient": ""},         # the list's wh_* is not an owner login
            {"branch": "อุดรธานี", "abbr": "udon", "username": "", "recipient": ""},              # has a branch login, not an owner login
            {"branch": "ระยอง", "abbr": "rayong", "username": "", "recipient": ""},              # already has an owner login
            {"branch": "พัทยา", "abbr": "", "username": "", "recipient": ""}]                    # no abbreviation: own_<id>
    plan = {p["branch"]: p for p in AC.plan_rows(rows, BRANCHES, users, role="franchise")}
    assert plan["ชลบุรี"]["action"] == "create" and plan["ชลบุรี"]["username"] == "own_cbi"
    assert plan["อุดรธานี"]["action"] == "create" and plan["อุดรธานี"]["username"] == "own_udon"
    assert plan["ระยอง"]["action"] == "exists" and plan["ระยอง"]["username"] == "own_old"      # no second owner login
    assert plan["พัทยา"]["action"] == "create" and plan["พัทยา"]["username"] == "own_12"
    assert all(not p["retire"] for p in plan.values())                                        # nothing is ever retired for owners


def test_owner_accounts_post_the_franchise_role_and_never_a_password():
    api = FakeApi()
    plan = AC.run(api, [{"branch": "ชลบุรี", "abbr": "cbi", "username": "", "recipient": ""}], apply=True, role="franchise")
    assert plan[0]["result"] == BL.R_CREATED
    assert [c for c in api.calls if c[0] == "POST"] == [("POST", "/api/auth/users", {"username": "own_cbi", "role": "franchise", "branch": "ชลบุรี"})]


def test_owner_run_refuses_replace_pilot(tmp_path):
    p = _csv(tmp_path, "สาขา,ตัวย่อ\nชลบุรี,cbi\n")
    assert AC.main(["--list", str(p), "--role", "franchise", "--replace-pilot", "--yes"]) == 2


def test_owner_handout_is_the_owner_variant(tmp_path, capsys):
    p = _csv(tmp_path, "สาขา,ตัวย่อ,ชื่อผู้ใช้,LINE/ผู้รับ,ผล\nชลบุรี,cbi,wh_cbi,เจ้าของชลบุรี,\nพัทยา,pty,own_pattaya,,สร้างแล้ว\n")
    out = tmp_path / "o"
    HO.main(["--role", "franchise", "--list", str(p), "--out", str(out)])
    assert sorted(f.name for f in out.glob("*.txt") if f.name != "README.txt") == ["own_cbi.txt", "own_pattaya.txt"]
    txt = (out / "own_cbi.txt").read_text(encoding="utf-8")
    assert "ร้านของฉัน" in txt and "ชื่อผู้ใช้: own_cbi" in txt and PW in txt and "ชลบุรี" in txt
    assert "ดูได้อย่างเดียว" in txt and "13:15" not in txt                                      # not the branch team's text
    assert "OSHINEI · ร้านของฉัน" in (out / "cards.html").read_text(encoding="utf-8")
    assert PW not in capsys.readouterr().out
    with pytest.raises(SystemExit):
        HO.main(["--role", "franchise", "--out", str(tmp_path / "o2")])                         # an owner handout needs a list
    HO.main(["--role", "franchise", "--list", str(p), "--only", "pattaya", "--out", str(tmp_path / "o3")])
    assert [f.name for f in (tmp_path / "o3").glob("own_*.txt")] == ["own_pattaya.txt"]
