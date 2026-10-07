"""scripts/rotate_seed_token.py — the pieces that decide what goes to Fly and the backup."""
import datetime as dt
import importlib.util
import subprocess
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "rotate_seed_token", Path(__file__).resolve().parents[1] / "scripts" / "rotate_seed_token.py")
rot = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rot)

OLD = "# comment\nadmin:aaa\n\nPatcharee:bbb\nArnan:ccc\n"


def test_parse_backup_keeps_order_and_skips_comments():
    assert rot.parse_backup(OLD) == [("admin", "aaa"), ("Patcharee", "bbb"), ("Arnan", "ccc")]


def test_plan_puts_the_pc_token_first_and_makes_fresh_tokens():
    new = rot.plan(rot.parse_backup(OLD), ["Patcharee", "admin"])
    assert [lab for lab, _ in new] == ["admin", "Patcharee"]
    assert all(len(t) == 64 and t not in ("aaa", "bbb") for _, t in new)


@pytest.mark.parametrize("keep", [["Patcharee"], ["admin", "bad label"], ["admin", "x", "X"]])
def test_plan_refuses_unsafe_lists(keep):
    with pytest.raises(SystemExit):
        rot.plan([], keep)


def test_fly_value_parses_like_the_live_gate(monkeypatch):
    new = rot.plan([], ["admin", "AR"])
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", rot.fly_value(new))
    import core
    assert core._accepted_tokens() == {tok: lab for lab, tok in new}


@pytest.mark.parametrize("hhmm,blocked", [("05:49", False), ("05:50", True), ("06:59", True), ("07:00", False),
                                          ("11:55", True), ("13:00", False), ("16:30", True), ("22:00", False)])
def test_sync_window(hhmm, blocked):
    h, m = map(int, hhmm.split(":"))
    assert rot.in_sync_window(dt.datetime(2026, 10, 7, h, m)) is blocked


def test_failed_fly_set_restores_the_old_backup(tmp_path, monkeypatch):
    b = tmp_path / ".seed_tokens.txt"
    b.write_text(OLD, encoding="utf-8")
    monkeypatch.setattr(rot, "in_sync_window", lambda now: False)
    monkeypatch.setattr(rot.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "boom"))
    with pytest.raises(SystemExit):
        rot.main(["--keep", "admin", "--apply", "--backup", str(b)])
    assert b.read_text(encoding="utf-8") == OLD


def test_apply_writes_backup_then_verifies(tmp_path, monkeypatch, capsys):
    b = tmp_path / ".seed_tokens.txt"
    b.write_text(OLD, encoding="utf-8")
    sent = {}
    monkeypatch.setattr(rot, "in_sync_window", lambda now: False)
    monkeypatch.setattr(rot, "_wait_up", lambda seconds=120: True)

    def fake_run(cmd, **k):
        sent["value"] = cmd[-1].split("=", 1)[1]
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(rot.subprocess, "run", fake_run)
    # exact match against the list sent to Fly — a substring test flaked when an old token
    # like "aaa" happened to occur inside a new random hex token (CI, 2026-10-07)
    monkeypatch.setattr(rot, "probe",
                        lambda tok: 422 if tok in {t.split(":", 1)[1] for t in sent["value"].split(",")} else 401)
    assert rot.main(["--keep", "admin", "AR", "--apply", "--backup", str(b)]) == 0
    new = rot.parse_backup(b.read_text(encoding="utf-8"))
    assert [lab for lab, _ in new] == ["admin", "AR"] and sent["value"] == rot.fly_value(new)
    assert list(tmp_path.glob(".seed_tokens.revoked-*.txt"))
    assert "rotation complete" in capsys.readouterr().out
