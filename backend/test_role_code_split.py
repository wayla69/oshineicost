# -*- coding: utf-8 -*-
"""Per-role code (code guard, owner 2026-10-27 "do the per-role code split too"): the branch's own logins
(branch, franchise) get the lazy slices and panels of their own pages only (main.ROLE_LAZY_SLICES /
ROLE_PANELS); head office's analysis, cost, P&L and report code never reaches them."""
import itertools
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth
import core

ROOT = Path(__file__).resolve().parent.parent
PW = "Role-Split-2026!"
_GEN = itertools.count(300_000)
HDR = {"Fly-Client-IP": "203.0.113.8",
       "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Mobile Safari/537.36"}


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    yield


@pytest.fixture
def iso_db(client, app_db, tmp_path, monkeypatch):
    dst = tmp_path / "iso.db"
    src = sqlite3.connect(str(app_db)); out = sqlite3.connect(str(dst))
    src.backup(out); src.close(); out.close()
    monkeypatch.setattr(core, "DB_PATH", dst)
    monkeypatch.setattr(core, "_db_gen", core._db_gen + next(_GEN))
    core.invalidate_cache(rebuild_cost=False)
    yield dst
    core.invalidate_cache(rebuild_cost=False)


def _client(db, username, role, branch=None):
    import main
    c = sqlite3.connect(str(db)); c.row_factory = sqlite3.Row
    try:
        auth.create_user(c, username, PW, role, branch=branch)
        c.execute("UPDATE app_user SET must_change=0 WHERE username=?", (username,))
        c.commit()
    finally:
        c.close()
    tc = TestClient(main.app, headers=HDR)
    assert tc.post("/api/auth/login", json={"username": username, "password": PW}).status_code == 200
    return tc


def _branch_name(db):
    c = sqlite3.connect(str(db))
    try:
        return c.execute("SELECT name FROM branches ORDER BY branch_id LIMIT 1").fetchone()[0]
    finally:
        c.close()


@pytest.mark.parametrize("role", ["branch", "franchise"])
def test_a_branch_login_gets_its_own_pages_code_only(iso_db, auth_on, role):
    import main
    c = _client(iso_db, f"rs_{role}", role, _branch_name(iso_db))
    keep = main.ROLE_LAZY_SLICES[role]
    shell = c.get("/").text
    assert set(re.findall(r'data-lazy="/js/([0-9a-z-]+\.js)\?v=', shell)) == keep
    for name in main._lazy_slice_names():
        assert c.get(f"/js/{name}").status_code == (200 if name in keep else 404), name
    assert c.get("/js/01-core.js").status_code == 200                  # the eager slices: login, boot, phone summary
    assert c.get("/app.js").status_code == 404
    tabs = c.get("/tabs.html")
    panels = set(re.findall(r'<div id="(tab-[a-z0-9]+)"', tabs.text))
    assert panels and "tab-mgtreport" not in panels and "tab-pnl" not in panels and "tab-cost" not in panels
    assert f'data-tabs-v="{tabs.headers["X-Tabs-Version"]}"' in shell     # the shell asks for its own bundle


def test_head_office_keeps_every_slice_and_panel(iso_db, auth_on):
    import main
    c = _client(iso_db, "rs_mgr", "management")
    shell = c.get("/").text
    assert set(re.findall(r'data-lazy="/js/([0-9a-z-]+\.js)\?v=', shell)) == main._lazy_slice_names()
    assert c.get("/js/02-mgt-report.js").status_code == 200
    assert len(re.findall(r'<div id="tab-', c.get("/tabs.html").text)) == len(list((ROOT / "tabs").glob("*.html")))


def test_the_lists_name_real_files():
    import main
    lazy = main._lazy_slice_names()
    for role, names in main.ROLE_LAZY_SLICES.items():
        assert names <= lazy, (role, names - lazy)
    files = {p.name for p in (ROOT / "tabs").glob("*.html")}
    for role, names in main.ROLE_PANELS.items():
        assert names <= files, (role, names - files)


# ── a kept slice must not call into a dropped one ────────────────────────────
_DECL = re.compile(r"^(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)|^(?:const|let|var)\s+([A-Za-z_$][\w$]*)", re.M)
_TOK = re.compile(r"[A-Za-z_$][\w$]*")
# names a kept slice mentions on a path the role never takes (checked in the browser for both roles, 2026-10-27)
_ALLOWED = {
    # งานของฉัน on the launcher: a role without a supply module returns before the หน้างาน filter is read
    "franchise": {"_shmOpen", "shmFilterGet", "shmFilterSet", "shmState", "shmOpenKeys", "_shmMine", "shmIsOpenTask"},
    "branch": set(),
}


@pytest.mark.parametrize("role", ["branch", "franchise"])
def test_kept_slices_do_not_call_into_dropped_ones(role):
    import main
    lazy = main._lazy_slice_names()
    src = {p.name: p.read_text(encoding="utf-8") for p in (ROOT / "js").glob("*.js")}
    defs = {}
    for name, text in src.items():
        for m in _DECL.finditer(text):
            defs.setdefault(m.group(1) or m.group(2), set()).add(name)
    dropped = lazy - main.ROLE_LAZY_SLICES[role]
    only_dropped = {n for n, fs in defs.items() if fs <= dropped and len(n) > 3}
    bad = []
    for name in sorted(main.ROLE_LAZY_SLICES[role]):
        for i, line in enumerate(src[name].splitlines(), 1):
            if line.strip().startswith("//"):
                continue
            for tok in set(_TOK.findall(line)) & only_dropped - _ALLOWED[role]:
                if f"typeof {tok}" not in line:
                    bad.append(f"{name}:{i} {tok}")
    assert not bad, bad
