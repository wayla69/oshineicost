"""Provisioning a login on its own random temporary password, forced to change at first
login (auth.provision_user + backend/provision_user.py). Code guard 2: never the shared default."""
import os
os.environ.setdefault("OSHINEI_NO_SWAP", "1")
import sqlite3  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.resolve()))
import auth  # noqa: E402
import provision_user as cli  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    auth.init_auth(c)
    yield c
    c.close()


def test_provision_creates_user_on_its_own_temp_password_that_must_change(con):
    assert auth.provision_user(con, "marketing1", "marketing") == "created"
    pw = auth.last_temp_password()
    row = auth.get_user(con, "marketing1")
    assert row["role"] == "marketing"
    assert row["active"] == 1
    assert row["must_change"] == 1                       # forced change at first login
    assert pw and pw != auth.DEFAULT_PASSWORD and len(pw) >= auth.MIN_PASSWORD_LEN
    assert not auth.verify_password(auth.DEFAULT_PASSWORD, row["pw_hash"], row["pw_salt"])
    assert auth.authenticate(con, "marketing1", pw) is not None
    auth.provision_user(con, "marketing2", "marketing")
    assert auth.last_temp_password() != pw               # each account its own


def test_provision_is_idempotent_and_reset_rearms(con):
    auth.provision_user(con, "marketing1", "marketing")
    # the user picks their own password → must_change cleared
    assert auth.change_own_password(con, "marketing1", auth.last_temp_password(),
                                    "aStrongPass123") is True
    assert auth.get_user(con, "marketing1")["must_change"] == 0

    # re-running must NOT clobber it
    assert auth.provision_user(con, "marketing1", "marketing") == "exists"
    assert auth.last_temp_password() is None
    assert auth.get_user(con, "marketing1")["must_change"] == 0
    assert auth.authenticate(con, "marketing1", "aStrongPass123") is not None

    # ...unless explicitly reset
    assert auth.provision_user(con, "marketing1", "marketing", reset_existing=True) == "reset"
    row = auth.get_user(con, "marketing1")
    assert row["must_change"] == 1
    assert auth.authenticate(con, "marketing1", auth.last_temp_password()) is not None
    assert auth.authenticate(con, "marketing1", "aStrongPass123") is None


def test_explicit_password_still_obeys_the_policy(con):
    with pytest.raises(ValueError):
        auth.provision_user(con, "marketing2", "marketing", password="tiny")
    auth.provision_user(con, "marketing2", "marketing", password="aStrongPass123")
    assert auth.get_user(con, "marketing2")["must_change"] == 1   # temp password too


def test_provision_rejects_unknown_role_and_branch(con):
    with pytest.raises(ValueError):
        auth.provision_user(con, "nobody", "wizard")
    with pytest.raises(ValueError):
        auth.provision_user(con, "nobody", "marketing", branch="ไม่มีสาขานี้")
    assert auth.get_user(con, "nobody") is None


def test_cli_creates_then_reports_existing(tmp_path, capsys):
    db = tmp_path / "oshinei.db"
    sqlite3.connect(str(db)).close()                     # empty DB; init_auth builds the table

    assert cli.main(["marketing1", "--role", "marketing", "--db", str(db)]) == 0
    assert "created marketing1" in capsys.readouterr().out

    assert cli.main(["marketing1", "--role", "marketing", "--db", str(db)]) == 0
    assert "already exists" in capsys.readouterr().out

    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    row = auth.get_user(c, "marketing1")
    c.close()
    assert row["role"] == "marketing" and row["must_change"] == 1


def test_cli_missing_db_is_an_error(tmp_path, capsys):
    assert cli.main(["marketing1", "--role", "marketing",
                     "--db", str(tmp_path / "nope.db")]) == 2
