# -*- coding: utf-8 -*-
"""📄 ใบแจ้งปริมาณสั่งวัตถุดิบ — the three sections the letter must carry, and
the one thing it must never do.

§1 ปริมาณที่แนะนำสั่ง · §2 ข้อมูลอ้างอิงการออกเมนู · §3 หน้าที่ตามสัญญาแฟรนไชส์.

§3 is a contractual reference sent to a business partner, so two rules hold:
it appears ONLY on a franchisee's copy (a company-owned branch has no franchise
agreement to be reminded of), and the clause NUMBER is never invented — it comes
from the caller or prints blank to be filled in by hand.
"""
import sqlite3

import pytest

import conftest as _cf
import main

# BranchA = master (company-owned) · BranchB = franchise, per the shared fixture.
BOM = [
    (2, 1, "ING-SAL",  "แซลมอน",      0.12, "กก.", 500.0, 60.0, "protein"),
    (2, 2, "ING-RICE", "ข้าวญี่ปุ่น", 0.05, "กก.",  40.0,  2.0, "carb"),
    (3, 1, "ING-BEEF", "เนื้อ",        0.20, "กก.", 600.0, 120.0, "protein"),
]
PLAN = {"date_to": f"{_cf.LATEST_MONTH}-03", "days": 28}


@pytest.fixture(scope="module", autouse=True)
def _seed_bom(client):
    con = sqlite3.connect(str(main.DB_PATH))
    con.executemany(
        "INSERT OR REPLACE INTO bom_ingredients(sku_id, seq, ingredient_code, ingredient_name,"
        " qty_per_serving, unit, price_per_unit, ingredient_cost, ingredient_type, source_file)"
        " VALUES(?,?,?,?,?,?,?,?,?,'test')", BOM)
    con.commit()
    con.close()
    try:
        main.invalidate_cache()
    except Exception:
        pass
    yield


def _letter(client, **extra):
    r = client.get("/api/ingredients/order-letter.print", params={**PLAN, **extra})
    assert r.status_code == 200, r.text[:200]
    return r.text


def test_letter_carries_the_three_sections(client):
    html = _letter(client, branch="BranchB")
    assert "๑. ปริมาณวัตถุดิบที่แนะนำให้สั่ง" in html
    assert "๒. ข้อมูลอ้างอิงและวิธีคำนวณ" in html
    assert "๓. หน้าที่ของสาขาตามสัญญาแฟรนไชส์" in html
    # §2 must state the window, the basis and the recipe coverage it used
    assert PLAN["date_to"] in html
    assert "สูตรมาตรฐาน (BoM)" in html
    assert "ความครอบคลุมของสูตร" in html


def test_franchise_duties_only_on_a_franchisees_copy(client):
    """A company-owned branch has no franchise agreement — printing a
    contractual reminder on its copy would be simply wrong."""
    fr = _letter(client, branch="BranchB")
    ma = _letter(client, branch="BranchA")
    assert "หน้าที่ของสาขาตามสัญญาแฟรนไชส์" in fr
    assert "สัญญาแฟรนไชส์" not in ma


def test_clause_number_is_never_invented(client):
    """Without contract_ref the letter prints a blank to fill in by hand. A
    fabricated clause citation in a notice to a franchisee is worse than none."""
    blank = _letter(client, branch="BranchB")
    assert "ข้อ ................" in blank
    given = _letter(client, branch="BranchB", contract_ref="ข้อ 7.2 และ 9.1")
    assert "ตามสัญญาแฟรนไชส์ ข้อ 7.2 และ 9.1" in given
    assert "ข้อ ................" not in given


def test_reply_deadline_only_appears_when_asked_for(client):
    assert "ยืนยันปริมาณที่จะสั่งกลับภายในวันที่" not in _letter(client, branch="BranchB")
    with_due = _letter(client, branch="BranchB", reply_by="2026-06-10")
    assert "ยืนยันปริมาณที่จะสั่งกลับภายในวันที่ 2026-06-10" in with_due


def test_reference_window_states_which_menu_it_used(client):
    """§2's menu line is the point of the section: order quantities derived from a
    window that spans the changeover are a blend of two recipe books, and the
    letter has to say which case it is rather than let the branch order against
    a number whose provenance it cannot see.

    The shared fixture trades only through 2026-06, i.e. entirely before the
    changeover, so that is the branch exercised here."""
    html = _letter(client, branch="BranchB")
    assert "เมนูที่อ้างอิง" in html
    assert "อ้างอิงเมนูที่ใช้อยู่ในช่วงวันที่ระบุข้างต้น" in html
    assert "คาบเกี่ยววันเปลี่ยนเมนู" not in html


@pytest.mark.parametrize("start,end,expect", [
    ("2026-09-01", "2026-09-30", "Grand Menu"),              # wholly on the new menu
    ("2026-09-05", "2026-09-30", "Grand Menu"),
    ("2026-08-20", "2026-09-10", "คาบเกี่ยววันเปลี่ยนเมนู"),   # straddles it → provisional
    ("2026-08-31", "2026-09-01", "คาบเกี่ยววันเปลี่ยนเมนู"),   # the tightest straddle there is
    ("2026-07-01", "2026-08-31", "อ้างอิงเมนูที่ใช้อยู่"),       # wholly on the old one
])
def test_menu_era_line_covers_all_three_cases(start, end, expect):
    """The shared fixture stops in June, so the three-way choice is exercised on
    the function the letter renders rather than through a window it cannot make."""
    from routers.ingredients import _ing_letter_menu_era_line
    assert expect in _ing_letter_menu_era_line(start, end)
