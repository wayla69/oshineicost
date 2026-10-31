# -*- coding: utf-8 -*-
"""2026-10-31 (docs/UI_REVIEW_MOBILE_2026-10-31.md items 4 + 6): every HQ department's หน้าแรก answers "วันนี้ต้องทำอะไร" for ITSELF.

The promises:
  * warehouse, salessupport, accounting, analyst, innovation and marketing get `hero` in GET /api/nav/badges (bom_center/dept_hero.py);
    management, operation, branch and franchise do not - management/operation keep the chain sales hero, branch/franchise their own screens;
  * the hero is read from the SAME open list as the badges (nav_badges.mine_open): `open` == the badge total, every page count of the
    hero == the badge count of that page, and the headline number is the count of the role's own open item;
  * nothing open -> n is None and tone "good" (the phone prints "ไม่มีงานค้าง" in green) and the line names the next thing;
  * the phone draws the hero from `_NB.hero` (no new call), keeps the sales hero for report.management and operation, sorts the tiles
    with work first and folds the pages with no status under "หน้าอื่น (n)"; the other departments come below, only when there are any.
"""
import re
import sqlite3
from datetime import date, datetime, time
from pathlib import Path

import pytest

import auth
import core
from bom_center import dept_hero as DH
from bom_center import nav_badges as NB
from bom_center import todo as TD
from bom_center.clock import BKK
from test_bom_center_s11 import udon  # noqa: F401  (branch 1 = อุดรธานี, the centre's September report)
from test_bom_center_s4b import con  # noqa: F401
from test_nav_badges import _login, auth_on  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 7)
HERO_ROLES = ("warehouse", "salessupport", "accounting", "analyst", "innovation")
NO_HERO = ("management", "operation")


def _build(c, role, now=None):
    return NB.build(c, role, auth.modules_for_role(role), None, today=TODAY, now=now)


def test_the_hero_is_the_roles_own_list(udon):
    c = udon
    for role in HERO_ROLES:
        got = _build(c, role)
        h = got["hero"]
        assert h["dept"] == DH.ROLE_DEPT[role]
        assert h["open"] == got["total"], role                                     # one answer: the badges' total
        assert {k: v["n"] for k, v in h["pages"].items()} == got["pages"], role     # each tile count == the badge of the page
        assert not set(h["known"]) & set(h["pages"]), role                         # a green page has nothing open
        if got["total"] == 0:
            assert h["n"] is None and h["tone"] == "good", role
        else:
            assert isinstance(h["n"], int) and h["n"] >= 1 and h["label"] and h["tone"] in ("bad", "watch"), role
        assert h["go"] and h["go"].get("tab"), role
    for role in NO_HERO:
        assert "hero" not in _build(c, role), role
    assert "hero" not in NB.build(c, "branch", auth.modules_for_role("branch"), "อุดรธานี", today=TODAY)


def _order(c, cycle, status="submitted"):
    c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, submitted_at) VALUES(1, ?, '1', ?, ?)",
              (cycle, status, cycle + "T09:00:00+07:00"))
    c.commit()


def test_the_warehouse_counts_what_waits_and_the_clock_to_two_pm(udon):
    c = udon
    c.execute("DELETE FROM bc_order WHERE status='submitted'")
    c.commit()
    zero = _build(c, "warehouse")["hero"]
    assert zero["n"] is None and zero["tone"] == "good" and zero["go"]["pane"] == "queue"
    assert "ตัดรอบถัดไป" in zero["line"]                                          # no work: the next scheduled thing
    _order(c, TODAY.isoformat())
    morning = datetime.combine(TODAY, time(10, 30), tzinfo=BKK)
    got = _build(c, "warehouse", now=morning)
    q = next(i for i in TD.build(c, None, None, None, today=TODAY, viewer_role="warehouse")["items"] if i["key"] == "queue_pending")
    h = got["hero"]
    assert h["n"] == q["count"] == 1 and h["label"] == "ใบที่ต้องยืนยันยอดส่ง" and h["unit"] == "ใบ"
    assert h["line"].startswith("เหลือ 3 ชม. 30 นาที ถึง 14:00") and h["tone"] == "watch"
    late = _build(c, "warehouse", now=datetime.combine(TODAY, time(14, 5), tzinfo=BKK))["hero"]
    assert "เลยเวลา 14:00" in late["line"] and late["tone"] == "bad"


def test_sales_support_names_the_queue_it_follows_but_never_counts_it(udon):
    c = udon
    _order(c, TODAY.isoformat())
    got = _build(c, "salessupport")
    h = got["hero"]
    assert "orderhub:queue" not in got["pages"]                                    # the warehouse's move: never a badge of Sales Support
    assert h["info"].get("orderhub:queue") == 1 and "ใบสั่งซื้อในคิว 1 ใบ" in h["line"]
    c.execute("INSERT INTO bc_round_reschedule(batch_id, branch_id, round, orig_date, new_date, reason_code, status)"
              " VALUES('t', 1, '1', ?, ?, 'transport', 'active')", (TODAY.isoformat(), "2026-10-08"))
    c.commit()
    core.invalidate_cache("bc_")
    assert "รอบส่งที่เลื่อนวันนี้ 1 รอบ" in _build(c, "salessupport")["hero"]["line"]


def test_the_kitchen_without_own_work_says_so_and_names_the_uncosted_menus():
    d = {"items": [{"key": "dish_cost_month", "state": "todo", "count": 12, "owner_th": "ฝ่ายวิเคราะห์", "owner_modules": ["bom.center"],
                    "go": {"tab": "bomcenter", "pane": "dishcost"}}], "close": {"steps": []}, "month": "2026-09"}
    h = DH.build(None, d, "innovation", auth.modules_for_role("innovation"), [], now=datetime(2026, 10, 7, 9, tzinfo=BKK))
    assert h["n"] is None and h["tone"] == "good" and "เมนูยังไม่มีต้นทุนต่อจาน 12 เมนู" in h["line"]
    assert h["info"] == {"bomcenter:dishcost": 12} and h["pages"] == {} and h["go"]["pane"] == "dishcost"
    # the analyst, whose move it is, gets it as the headline
    it = d["items"][0]
    a = DH.build(None, d, "analyst", auth.modules_for_role("analyst"), [it], now=datetime(2026, 10, 7, 9, tzinfo=BKK))
    assert (a["label"], a["n"], a["unit"]) == ("เมนูยังไม่มีต้นทุนต่อจาน", 12, "เมนู") and a["pages"] == {"bomcenter:dishcost": {"n": 1, "tone": "watch"}}


def test_marketing_reads_the_feedback_queue_by_the_pages_own_rule(tmp_path):
    from routers import feedback as FB
    c = sqlite3.connect(tmp_path / "fb.db")
    FB._ensure(c)
    today = FB._thai_today()
    rows = [("google", "g1", 2, "neg", today, None), ("google", "g2", 1, "neg", "2020-01-01", None),
            ("google", "g3", 5, "pos", today, None), ("google", "g4", 2, "neg", today, "ตอบแล้ว"), (None, None, 1, "neg", today, None)]
    for i, (src, ext, rating, sent, at, reply) in enumerate(rows):
        c.execute("INSERT INTO customer_feedback(source, external_id, rating, sentiment, rated_at, text, reply_text, kind, fingerprint)"
                  " VALUES(?,?,?,?,?,?,?,'review',?)", (src, ext, rating, sent, at, "x", reply, f"f{i}"))
    c.commit()
    n, late = FB.reply_counts(c)
    assert (n, late) == (2, 1)                         # g1 + g2 wait; g2 is past the reply time; answered / positive / no id never count
    h = DH.marketing(n, late, FB.SLA_DAYS)
    assert (h["label"], h["n"], h["tone"]) == ("เสียงลูกค้ารอตอบ", 2, "bad") and h["pages"] == {"feedback": {"n": 2, "tone": "bad"}}
    zero = DH.marketing(0, 0, FB.SLA_DAYS)
    assert zero["n"] is None and zero["tone"] == "good" and zero["known"] == ["feedback"]


def test_over_http_only_the_departments_get_a_hero(client, auth_on):  # noqa: F811
    core.invalidate_cache("bc_")
    for role in HERO_ROLES + ("marketing",):
        d = _login(f"{role}1").get("/api/nav/badges").json()
        assert d["hero"]["dept"] == DH.ROLE_DEPT[role] and d["hero"]["open"] == (d["total"] if role != "marketing" else d["hero"]["open"]), role
    for role in NO_HERO:
        assert "hero" not in _login(f"{role}1").get("/api/nav/badges").json(), role
    assert "hero" not in client.get("/api/nav/badges").json()                      # auth off = the manager's view


# ── the phone (static pins) ─────────────────────────────────────────────────────────────────────────────────────────
MOB = (ROOT / "js" / "09c-mobile.js").read_text(encoding="utf-8")
LAUNCH = (ROOT / "js" / "18-launcher.js").read_text(encoding="utf-8")


def _fn(src, name):
    i = src.index(f"function {name}(")
    j = src.index("\n}\n", i)
    return src[i:j + 2]


def test_the_phone_keeps_the_sales_hero_for_management_and_operation_only():
    key = _fn(MOB, "_phDeptHeroKey")
    assert "hasModule('report.management')" in key
    roles = re.search(r"const _PH_DEPT_HERO = \{([^}]*)\}", MOB).group(1)
    assert set(re.findall(r"(\w+):", roles)) == set(DH.ROLE_DEPT)                 # the two lists stay in step
    assert "operation" not in roles and "management" not in roles and "branch" not in roles and "franchise" not in roles
    home = _fn(MOB, "_phCentreHome")
    assert home.index("_phDeptHeroKey()") < home.index("phYest")                 # a department home never builds the sales hero
    assert "!_phDeptHeroKey()" in _fn(MOB, "phRenderHome")                       # ... nor fetches the brief for it


def test_the_phone_reads_the_hero_from_the_badges_answer_and_folds_pages_without_status():
    assert "_NB.hero = (j.hero" in LAUNCH and "phDeptHeroApply()" in LAUNCH
    dt = _fn(MOB, "_phDtHtml")
    assert "หน้าอื่น (" in dt and "details class=\"ph-more\"" in dt
    assert "rank(a) - rank(b)" in dt                                             # work first
    assert "api(" not in _fn(MOB, "_phDhHtml") + dt + _fn(MOB, "phDeptHeroApply")   # no call of its own
    assert "ไม่มีงานค้าง" in _fn(MOB, "_phDhHtml")
    home = _fn(MOB, "_phDeptHome")
    assert "deptVisible().filter(r => !r.home)" in home and "others.length ?" in home   # other departments only when there are any
