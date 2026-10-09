# -*- coding: utf-8 -*-
"""PR-H - the nightly off-site backup of the owner-data tables: the admin endpoint is
token-only and fail-closed, the file holds exactly the tables a data swap preserves (read
from core, never copied), the JSON agrees with the file, and the PC-side script verifies,
prunes, logs and restore-checks it."""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

_spec = importlib.util.spec_from_file_location("backup_owner_tables", HERE.parent / "scripts" / "backup_owner_tables.py")
S = importlib.util.module_from_spec(_spec)
sys.modules["backup_owner_tables"] = S
_spec.loader.exec_module(S)

TOKEN = "backup-test-token-0123456789"
SQLITE_URL = "/api/_admin/backup/owner-data.sqlite"
JSON_URL = "/api/_admin/backup/owner-data.json"
H = {"X-Seed-Token": TOKEN}


@pytest.fixture()
def tok(monkeypatch):
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "admin:" + TOKEN + ",Patcharee:staff-browser-token")
    monkeypatch.delenv("OSHINEI_AUTH", raising=False)
    return TOKEN


@pytest.fixture()
def seeded(app_db):
    """The live (test) DB as it is. The app holds its own connection on it, so this suite never writes to it;
    the table-selection rules are proven on a private DB in test_export_selects_by_the_swap_predicate."""
    return app_db


def test_export_selects_by_the_swap_predicate(tmp_path):
    import core
    live = tmp_path / "live.db"
    con = sqlite3.connect(str(live))
    con.executescript("""
        CREATE TABLE bc_probe(id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT NOT NULL UNIQUE);
        CREATE INDEX idx_bc_probe_note2 ON bc_probe(note);
        INSERT INTO bc_probe(note) VALUES ('a'),('b'),('c');
        CREATE TABLE recon_probe(x); INSERT INTO recon_probe VALUES (1);
        CREATE TABLE app_user(u TEXT PRIMARY KEY, blobby BLOB); INSERT INTO app_user VALUES ('admin', x'00ff');
        CREATE TABLE bcd_derived(x); INSERT INTO bcd_derived VALUES (1);       -- bcd_* is rebuilt, never copied
        CREATE TABLE skus(x); INSERT INTO skus VALUES (1);
        CREATE TABLE sales_sku_daily(x); INSERT INTO sales_sku_daily VALUES (1);
        CREATE TABLE ingredient_order_unit(ingredient_code TEXT PRIMARY KEY, source TEXT);
        INSERT INTO ingredient_order_unit VALUES ('A','manual'),('B','supplier-sheet');
    """)
    con.commit(); con.close()
    out = tmp_path / "out.sqlite"
    info = core.export_owner_data(str(live), str(out))
    got = {t["name"]: t["rows"] for t in info["tables"]}
    assert got == {"app_user": 1, "bc_probe": 3, "recon_probe": 1, "ingredient_order_unit": 1}   # only the manual pack-size row
    assert info["integrity"] == "ok" and info["bytes"] == out.stat().st_size
    c = sqlite3.connect(str(out))
    try:
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='idx_bc_probe_note2'").fetchone()          # indexes travel
        assert "UNIQUE" in c.execute("SELECT sql FROM sqlite_master WHERE name='bc_probe'").fetchone()[0]     # so does the DDL
        assert c.execute("SELECT blobby FROM app_user").fetchone()[0] == b"\x00\xff"
    finally:
        c.close()
    again = core.export_owner_data(str(live), str(out))                                              # re-running replaces the file
    assert {t["name"]: t["rows"] for t in again["tables"]} == got


def _expected_tables(db) -> set:
    """What the swap protects, computed from core's own predicate (+ the merged-row tables that have hand-written rows)."""
    import core
    con = sqlite3.connect(str(db))
    try:
        names = {r[0] for r in con.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND {core._preserved_pred('name')}")}
        for t, (_key, where) in core._MERGED_ROWS.items():
            try:
                if con.execute(f'SELECT COUNT(*) FROM "{t}" WHERE {where}').fetchone()[0]:
                    names.add(t)
            except sqlite3.Error:
                pass
        return names
    finally:
        con.close()


# ── the endpoint ─────────────────────────────────────────────────────────────
def test_refused_without_the_admin_token(client, monkeypatch, tok):
    for url in (SQLITE_URL, JSON_URL):
        assert client.get(url).status_code == 403
        assert client.get(url, headers={"X-Seed-Token": "wrong"}).status_code == 403
        assert client.get(url, params={"token": TOKEN}).status_code == 403            # never the URL
        assert client.get(url, headers={"X-Seed-Token": "staff-browser-token"}).status_code == 403   # a per-person token is not the owner's
        assert client.get(url, headers=H).status_code == 200
        assert client.get(url, headers={"X-Upload-Token": TOKEN}).status_code == 200
    # fail closed: with no token configured at all the export is NOT open (unlike the dev-mode write gate)
    monkeypatch.delenv("OSHINEI_SEED_TOKEN", raising=False)
    assert client.get(SQLITE_URL).status_code == 403
    assert client.get(JSON_URL, headers=H).status_code == 403


def test_a_login_session_never_opens_it_but_the_token_passes_the_login_gate(client, monkeypatch, tok):
    import auth
    mods = set(auth.MODULES)
    assert not auth.path_allowed("GET", SQLITE_URL, mods)             # not even a role holding every module
    assert not auth.path_allowed("GET", JSON_URL, mods)
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret")
    assert client.get("/api/feedback/summary").status_code == 401     # the login gate is on ...
    assert client.get(SQLITE_URL).status_code == 403                  # ... a bare request is refused by the token gate
    assert client.get(SQLITE_URL, headers=H).status_code == 200       # ... and the token alone is enough (headless job)


# ── the file ─────────────────────────────────────────────────────────────────
def test_file_holds_exactly_the_preserved_tables(client, seeded, tok, tmp_path):
    r = client.get(SQLITE_URL, headers=H)
    assert r.status_code == 200
    assert "attachment; filename=oshinei_owner_data_" in r.headers["content-disposition"]
    assert r.headers["content-disposition"].endswith(".sqlite")
    assert r.headers["cache-control"] == "no-store"
    f = tmp_path / "out.sqlite"
    f.write_bytes(r.content)
    assert r.content[:15] == b"SQLite format 3"

    con = sqlite3.connect(str(f))
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        tables = {x[0] for x in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\'")}
        counts = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        idx = {x[0] for x in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        con.close()
    assert tables == _expected_tables(seeded) and "app_user" in tables
    assert any(t.startswith("bc_") for t in tables)
    assert "skus" not in tables and "sales_sku_daily" not in tables and "sales_transaction" not in tables
    assert idx                                                       # indexes travel with their tables
    assert json.loads(r.headers["x-backup-counts"]) == counts         # the header describes THIS file

    # the live DB agrees row for row
    live = sqlite3.connect(str(seeded))
    try:
        for t, n in counts.items():
            assert live.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] == n
    finally:
        live.close()

    # and the monitoring JSON reports the same tables and rows
    j = client.get(JSON_URL, headers=H).json()
    assert {t["name"]: t["rows"] for t in j["tables"]} == counts
    assert j["rows"] == sum(counts.values()) and j["bytes"] > 0 and len(j["generated_at"]) == 19


# ── the PC-side script ───────────────────────────────────────────────────────
@pytest.fixture()
def saved(client, seeded, tok, tmp_path):
    r = client.get(SQLITE_URL, headers=H)
    f = tmp_path / "oshinei_owner_data_20261009_0230.sqlite"
    f.write_bytes(r.content)
    return f, json.loads(r.headers["x-backup-counts"])


def test_verify_function_accepts_the_download_and_rejects_bad_files(saved, tmp_path):
    f, declared = saved
    res = S.verify_file(f, declared=declared, live=declared)
    assert res["ok"] and res["tables"] == len(declared) and res["rows"] == sum(declared.values()) and not res["problems"]

    wrong = dict(declared, app_user=declared["app_user"] + 99)
    assert not S.verify_file(f, declared=wrong)["ok"]                                       # server declared another count
    assert not S.verify_file(f, declared={k: v for k, v in declared.items() if k != "app_user"})["ok"]
    assert not S.verify_file(f, live=dict(declared, app_user=declared["app_user"] + 500))["ok"]               # file clearly lost rows vs live
    assert S.verify_file(f, live=dict(declared, app_user=declared["app_user"] + 1))["ok"]                     # normal live drift only warns
    assert not S.verify_file(f, live=dict(declared, extra_table=3))["ok"]                     # a live table the file lacks

    junk = tmp_path / "junk.sqlite"
    junk.write_bytes(b"this is not sqlite" * 50)
    assert not S.verify_file(junk)["ok"]
    bad = tmp_path / "trunc.sqlite"
    bad.write_bytes(f.read_bytes()[:3000])
    assert not S.verify_file(bad)["ok"]


def test_restore_check_reads_every_table_from_a_copy(saved, tmp_path):
    f, declared = saved
    before = f.read_bytes()
    res = S.restore_check(f)
    assert res["ok"] and res["tables"] == declared
    assert f.read_bytes() == before                                   # the original is untouched
    assert S.main(["--restore-check", str(f)]) == 0
    assert not S.restore_check(tmp_path / "missing.sqlite")["ok"]
    assert S.main(["--restore-check", str(tmp_path / "missing.sqlite")]) == 1


def test_prune_keeps_the_newest(tmp_path):
    names = [f"oshinei_owner_data_202610{d:02d}_0230.sqlite" for d in range(1, 9)]
    for n in names:
        (tmp_path / n).write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("keep me")
    removed = S.prune(tmp_path, 5)
    assert sorted(removed) == names[:3]
    assert sorted(p.name for p in tmp_path.glob("oshinei_owner_data_*")) == names[3:]
    assert (tmp_path / "notes.txt").exists()
    assert S.prune(tmp_path, 0) == []


def test_token_comes_from_env_then_env_file_and_never_reaches_the_log(tmp_path, monkeypatch):
    for k in ("OSHINEI_SEED_TOKEN", "OSHINEI_WRITE_TOKEN", "MY_TOK"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "backend").mkdir()
    assert S.resolve_token(repo=tmp_path) == ""
    (tmp_path / "backend" / ".seed_tokens.txt").write_text("# tokens\nowner:filetoken\n")
    assert S.resolve_token(repo=tmp_path) == "filetoken"
    (tmp_path / "backend" / ".env").write_text("OSH_SVR=x\nOSHINEI_SEED_TOKEN=\"dotenvtoken\"\n")
    assert S.resolve_token(repo=tmp_path) == "dotenvtoken"
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "a:envtoken,b:other")
    assert S.resolve_token(repo=tmp_path) == "envtoken"
    monkeypatch.setenv("MY_TOK", "customenv")
    assert S.resolve_token("MY_TOK", repo=tmp_path) == "customenv"


def _wire(monkeypatch, client):
    """Route the script's two network calls through the TestClient (no sockets)."""
    def fake_download(base, token, dest, timeout=600):
        r = client.get(SQLITE_URL, headers={"X-Seed-Token": token})
        assert r.status_code == 200
        Path(dest).write_bytes(r.content)
        return {"counts": json.loads(r.headers["x-backup-counts"]),
                "filename": r.headers["content-disposition"].split("filename=")[1]}

    def fake_json(base, token, timeout=300):
        r = client.get(JSON_URL, headers={"X-Seed-Token": token})
        assert r.status_code == 200
        return r.json()
    monkeypatch.setattr(S, "download", fake_download)
    monkeypatch.setattr(S, "fetch_json", fake_json)


def test_run_writes_verifies_logs_and_prunes(client, seeded, tok, tmp_path, monkeypatch, capsys):
    _wire(monkeypatch, client)
    out = tmp_path / "bk"
    out.mkdir()
    for d in range(1, 6):                                                     # 5 old files, keep 3 incl. the new one
        (out / f"oshinei_owner_data_202609{d:02d}_0230.sqlite").write_bytes(b"old")
    rc = S.main(["--base", "http://x", "--out", str(out), "--keep", "3"])
    assert rc == 0
    files = sorted(out.glob("oshinei_owner_data_*.sqlite"))
    assert len(files) == 3 and files[-1].stat().st_size > 4096 and not (out / "owner_data.part").exists()
    assert S.restore_check(files[-1])["ok"]
    line = (out / "backup.log").read_text(encoding="utf-8").strip().splitlines()[-1]
    assert " | OK | " in line and "bytes=" in line and "tables=" in line and "rows=" in line and TOKEN not in line
    assert TOKEN not in capsys.readouterr().out


def test_run_fails_loudly_on_a_bad_download_and_keeps_old_files(client, seeded, tok, tmp_path, monkeypatch):
    out = tmp_path / "bk"
    out.mkdir()
    old = out / "oshinei_owner_data_20260101_0230.sqlite"
    old.write_bytes(b"old")

    def broken(base, token, dest, timeout=600):
        Path(dest).write_bytes(b"<html>gateway error</html>")
        return {"counts": {"app_user": 1}, "filename": "oshinei_owner_data_20261009_0230.sqlite"}
    monkeypatch.setattr(S, "download", broken)
    monkeypatch.setattr(S, "fetch_json", lambda *a, **k: {"tables": []})
    assert S.main(["--base", "http://x", "--out", str(out), "--keep", "1"]) == 1
    assert old.exists() and not list(out.glob("oshinei_owner_data_2026100*"))   # nothing new accepted, nothing pruned
    assert " | FAIL | " in (out / "backup.log").read_text(encoding="utf-8")

    def refused(base, token, dest, timeout=600):
        import urllib.error
        raise urllib.error.HTTPError("http://x", 403, "Forbidden", {}, None)
    monkeypatch.setattr(S, "download", refused)
    assert S.main(["--base", "http://x", "--out", str(out)]) == 1
    assert "token was refused" in (out / "backup.log").read_text(encoding="utf-8")


def test_dry_run_only_calls_the_json_endpoint(client, seeded, tok, tmp_path, monkeypatch):
    _wire(monkeypatch, client)
    monkeypatch.setattr(S, "download", lambda *a, **k: pytest.fail("a dry run must not download"))
    out = tmp_path / "bk"
    assert S.main(["--base", "http://x", "--out", str(out), "--dry-run"]) == 0
    assert " | DRY | " in (out / "backup.log").read_text(encoding="utf-8")
    assert not list(out.glob("*.sqlite"))


def test_no_token_exits_2(tmp_path, monkeypatch):
    for k in ("OSHINEI_SEED_TOKEN", "OSHINEI_WRITE_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(S, "REPO", tmp_path)
    monkeypatch.setattr(S, "resolve_token", lambda *a, **k: "")
    assert S.main(["--out", str(tmp_path / "bk")]) == 2
