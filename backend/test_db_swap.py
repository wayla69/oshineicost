"""A3: fail-closed carry-forward — a data refresh must never wipe recon work.

Run:  cd backend && python -m pytest test_db_swap.py -v
"""
import os
os.environ.setdefault("OSHINEI_NO_SWAP", "1")   # no FS side effects on import

import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))
import main  # noqa: E402
import core   # the swap machinery reads core.DB_PATH and calls core._carry_forward_preserved; patching main would miss it


def _mk_live(p):
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE recon_uploads(upload_id INTEGER PRIMARY KEY, file_name TEXT)")
    c.executemany("INSERT INTO recon_uploads(file_name) VALUES(?)", [("a",), ("b",), ("c",)])
    c.execute("CREATE TABLE app_user(id INTEGER PRIMARY KEY, name TEXT)")
    c.execute("INSERT INTO app_user(name) VALUES('admin')")
    # reviews are written on the LIVE site (tab upload, nightly Google/Facebook
    # fetch) and exist nowhere else — the 2026-09-05 fix
    c.execute("CREATE TABLE customer_feedback(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, fingerprint TEXT UNIQUE)")
    c.executemany("INSERT INTO customer_feedback(text, fingerprint) VALUES(?,?)", [("อร่อย", "f1"), ("รอนาน", "f2")])
    c.execute("CREATE INDEX ix_cf_fp ON customer_feedback(fingerprint)")
    c.execute("CREATE TABLE feedback_sync_log(id INTEGER PRIMARY KEY, source TEXT)")
    c.execute("INSERT INTO feedback_sync_log(source) VALUES('Google')")
    c.execute("CREATE TABLE checklist_state(checklist TEXT, item_id TEXT, done INTEGER, PRIMARY KEY(checklist, item_id))")
    c.execute("INSERT INTO checklist_state VALUES('feedback_rollout','s0101',1)")
    c.commit(); c.close()


def _mk_incoming(p):
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE sales_transaction(id INTEGER PRIMARY KEY)")
    c.execute("INSERT INTO sales_transaction(id) VALUES(1)")
    c.commit(); c.close()


def test_carry_forward_preserves_and_verifies(tmp_path):
    """Happy path: recon_*/app_user are carried into the staged DB and verified."""
    live, inc = tmp_path / "live.db", tmp_path / "inc.db"
    _mk_live(live); _mk_incoming(inc)
    safe, reason = main._carry_forward_and_verify(str(live), str(inc))
    assert safe, reason
    cnt = main._preserved_counts(str(inc))
    assert cnt["recon_uploads"] == 3 and cnt["app_user"] == 1
    assert cnt["customer_feedback"] == 2 and cnt["feedback_sync_log"] == 1 and cnt["checklist_state"] == 1
    c = sqlite3.connect(str(inc))
    assert c.execute("SELECT text FROM customer_feedback ORDER BY id").fetchall() == [("อร่อย",), ("รอนาน",)]
    assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='ix_cf_fp'").fetchone()[0] == 1
    # the staged DB's own (sync-owned) tables are untouched
    assert c.execute("SELECT COUNT(*) FROM sales_transaction").fetchone()[0] == 1
    c.close()


def test_fail_closed_on_carry_forward_error(tmp_path):
    """A corrupt live DB → carry-forward errors → NOT safe (live must be kept)."""
    inc = tmp_path / "inc.db"; _mk_incoming(inc)
    bad = tmp_path / "bad.db"; bad.write_bytes(b"this is not a database")
    safe, reason = main._carry_forward_and_verify(str(bad), str(inc))
    assert not safe and "fail" in reason.lower()


def test_fail_closed_on_dropped_rows(tmp_path, monkeypatch):
    """If carry-forward silently no-ops, the verify gate still aborts the swap."""
    live, inc = tmp_path / "live.db", tmp_path / "inc.db"
    _mk_live(live); _mk_incoming(inc)
    monkeypatch.setattr(core, "_carry_forward_preserved", lambda l, i: None)
    safe, reason = main._carry_forward_and_verify(str(live), str(inc))
    assert not safe and "drop" in reason.lower()


def test_no_false_abort_on_fresh_install(tmp_path):
    """Live with zero recon rows must NOT block the swap (fresh deploy)."""
    live, inc = tmp_path / "live.db", tmp_path / "inc.db"
    c = sqlite3.connect(str(live)); c.execute("CREATE TABLE recon_uploads(id INTEGER PRIMARY KEY)"); c.commit(); c.close()
    _mk_incoming(inc)
    safe, reason = main._carry_forward_and_verify(str(live), str(inc))
    assert safe, reason


# ── every handle on .incoming must close before the function returns ─────────
# A probe connection leaked by the swap validation (query raising mid-block)
# kept oshinei.db.incoming open on Windows: the discard-unlink below it failed
# silently, and every nightly sync died at its own INCOMING.unlink() with
# WinError 32 (2026-08-08..10, three days). The poller re-runs the same block
# every 60 s, so a leak there re-arms forever. These tests spy on every
# sqlite3.connect() the swap paths make and demand each one is closed.
import pytest  # noqa: E402


def _spy_connections(monkeypatch):
    opened, real = [], sqlite3.connect

    def spy(*a, **k):
        con = real(*a, **k)
        opened.append(con)
        return con

    monkeypatch.setattr(core.sqlite3, "connect", spy)
    return opened


def _assert_all_closed(opened):
    for con in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            con.execute("SELECT 1")   # "Cannot operate on a closed database"


def test_startup_swap_discards_malformed_incoming_and_closes_probe(tmp_path, monkeypatch):
    live = tmp_path / "oshinei.db"; _mk_live(live)
    inc = Path(str(live) + ".incoming")
    inc.write_bytes(b"this is not a database")    # probe raises mid-validation
    monkeypatch.setattr(core, "DB_PATH", live)
    opened = _spy_connections(monkeypatch)
    main._maybe_swap_incoming_db()
    _assert_all_closed(opened)
    # the discard-unlink only works on Windows because the probe closed first
    assert not inc.exists()


def test_startup_swap_applies_good_incoming_and_closes_probe(tmp_path, monkeypatch):
    live = tmp_path / "oshinei.db"; _mk_live(live)
    inc = Path(str(live) + ".incoming"); _mk_incoming(inc)
    monkeypatch.setattr(core, "DB_PATH", live)
    opened = _spy_connections(monkeypatch)
    main._maybe_swap_incoming_db()
    _assert_all_closed(opened)
    assert not inc.exists()                       # swapped in (needs all handles closed)
    con = sqlite3.connect(str(live))
    assert con.execute("SELECT COUNT(*) FROM sales_transaction").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM recon_uploads").fetchone()[0] == 3  # carried
    con.close()


def test_hot_swap_discards_malformed_incoming_and_closes_probe(tmp_path, monkeypatch):
    live = tmp_path / "oshinei.db"; _mk_live(live)
    inc = Path(str(live) + ".incoming")
    inc.write_bytes(b"this is not a database")
    monkeypatch.setattr(core, "DB_PATH", live)
    opened = _spy_connections(monkeypatch)
    assert main._apply_incoming_live() is False
    _assert_all_closed(opened)
    assert not inc.exists()


def test_hot_swap_applies_good_incoming_and_closes_probe(tmp_path, monkeypatch):
    live = tmp_path / "oshinei.db"; _mk_live(live)
    inc = Path(str(live) + ".incoming"); _mk_incoming(inc)
    monkeypatch.setattr(core, "DB_PATH", live)
    gen_before = core._db_gen          # the swap bumps core's counter; main re-exports a snapshot
    opened = _spy_connections(monkeypatch)
    assert main._apply_incoming_live() is True
    _assert_all_closed(opened)
    assert core._db_gen == gen_before + 1         # threads reconnect lazily
    con = sqlite3.connect(str(live))
    assert con.execute("SELECT COUNT(*) FROM sales_transaction").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM recon_uploads").fetchone()[0] == 3  # carried
    con.close()
