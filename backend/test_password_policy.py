"""AUD-1: password policy is enforced on every path that sets a password."""
import os
os.environ.setdefault("OSHINEI_NO_SWAP", "1")
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.resolve()))
import auth  # noqa: E402
import pytest  # noqa: E402


def test_check_password_policy():
    with pytest.raises(ValueError):
        auth.check_password_policy("short")              # < 10 chars
    with pytest.raises(ValueError):
        auth.check_password_policy("")                    # empty
    with pytest.raises(ValueError):
        auth.check_password_policy(auth.DEFAULT_PASSWORD) # the shared seeded default
    auth.check_password_policy("aStrongPass123")          # ok → no raise


def test_policy_enforced_on_create_and_change(tmp_path):
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    auth.init_auth(con)
    with pytest.raises(ValueError):
        auth.create_user(con, "weakling", "abc", "accounting")     # too short → refused
    auth.create_user(con, "strong", "aStrongPass123", "accounting")
    # self-service change to a too-short password is refused; current pw still verifies
    assert auth.change_own_password(con, "strong", "aStrongPass123", "aNewStrong456") is True
    with pytest.raises(ValueError):
        auth.change_own_password(con, "strong", "aNewStrong456", "tiny")
    con.close()
