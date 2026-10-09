"""PR-M2 — a value typed on the screen is refused with a reason, never silently kept.

The ingredient master saved the name "-3" with no message and it then showed on every page for every role; the screen now
checks first (js/13 `_bmcIngProblems`) and the server refuses the same values (master.update_rows), so a client that skips
the screen cannot save them either.
"""
import pytest

from bom_center import master as MA
from test_bom_center_s4b import con  # noqa: F401


def _code(con):  # noqa: F811
    return con.execute("SELECT code FROM bc_ingredient ORDER BY code LIMIT 1").fetchone()[0]


@pytest.mark.parametrize("name,why", [("", "ชื่อต้องไม่ว่าง"), ("   ", "ชื่อต้องไม่ว่าง"), ("-3", "ชื่อต้องมีตัวอักษร"), ("12.5", "ชื่อต้องมีตัวอักษร")])
def test_a_wrong_name_is_refused(con, name, why):  # noqa: F811
    code = _code(con)
    before = con.execute("SELECT name_th FROM bc_ingredient WHERE code=?", (code,)).fetchone()[0]
    with pytest.raises(ValueError) as e:
        MA.update_rows(con, [{"code": code, "name_th": name}], "analyst1")
    assert why in str(e.value) and code in str(e.value)
    con.rollback()
    assert con.execute("SELECT name_th FROM bc_ingredient WHERE code=?", (code,)).fetchone()[0] == before


def test_a_real_name_is_trimmed_and_saved(con):  # noqa: F811
    code = _code(con)
    MA.update_rows(con, [{"code": code, "name_th": "  กุ้งขาวแกะเปลือก 31-40  "}], "analyst1")
    assert con.execute("SELECT name_th FROM bc_ingredient WHERE code=?", (code,)).fetchone()[0] == "กุ้งขาวแกะเปลือก 31-40"


def test_numbers_must_be_numbers_not_below_zero(con):  # noqa: F811
    code = _code(con)
    with pytest.raises(ValueError):
        MA.update_rows(con, [{"code": code, "loss_pct": -5}], "analyst1")
    con.rollback()
    with pytest.raises(ValueError):
        MA.update_rows(con, [{"code": code, "qty_per_recv": -1}], "analyst1")
    con.rollback()
