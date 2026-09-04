"""refresh_fly_db.py — the push-to-Fly script's restart step.

The nightly push ended with "could not list machines; restart manually" for
every scheduled run: flyctl cannot read its own ~/.fly/config.yml under Task
Scheduler ("no access token available"), so the staged DB waited for the app's
next restart to be swapped in. fly_env() hands flyctl the token explicitly.
"""
import sys
from pathlib import Path

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import refresh_fly_db as r  # noqa: E402


def test_fly_env_reads_the_token_from_the_config_when_the_env_has_none(tmp_path):
    cfg = tmp_path / "config.yml"
    cfg.write_text('access_token: "fo1_abc123"\nmetrics_token: xyz\n', encoding="utf-8")
    env = r.fly_env(config=cfg, environ={"PATH": "/x"})
    assert env["FLY_API_TOKEN"] == "fo1_abc123"
    assert env["PATH"] == "/x"                      # everything else passes through


def test_fly_env_keeps_a_token_the_caller_already_has(tmp_path):
    cfg = tmp_path / "config.yml"
    cfg.write_text("access_token: fo1_from_file\n", encoding="utf-8")
    env = r.fly_env(config=cfg, environ={"FLY_API_TOKEN": "fo1_from_env"})
    assert env["FLY_API_TOKEN"] == "fo1_from_env"


def test_fly_env_without_a_config_file_changes_nothing(tmp_path):
    env = r.fly_env(config=tmp_path / "missing.yml", environ={"A": "1"})
    assert env == {"A": "1"}


def test_fly_env_ignores_a_blank_token_line(tmp_path):
    cfg = tmp_path / "config.yml"
    cfg.write_text("access_token:\nother: 1\n", encoding="utf-8")
    assert "FLY_API_TOKEN" not in r.fly_env(config=cfg, environ={})
