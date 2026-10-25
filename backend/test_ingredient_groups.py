# -*- coding: utf-8 -*-
"""High Value Product Monitor (owner 2026-10-25): key-ingredient cost per head by branch (backend/ingredient_groups.py,
GET /api/cost/ingredient-groups and /menus).

Fixture relative to the real Thai today: 440 days of bills (10 bills x 2 covers per branch per day), the day-level dish table
only for the last 100 days (the sync's rolling window) and the month table for every month. Per branch per day:
    4 x (B) ซาชิมิแซลมอน ฿10 · 1 x (B) ซาชิมิรวม แซลมอน ทูน่า ฿30 (mixed) · 2 x (B) ปลาเนื้อขาว ซาชิมิ ฿5 (a fish, not beef)
    1 x (B) เนื้อวากิวสไลด์ ฿20 · 2 x (B) ซูชิทูน่า (no cost) · 1 x BUFFETSALMON (a package: never a group, cost 0)
so salmon = ฿70 / 20 covers = ฿3.5 a head, 0.25 plates a head, ฿14 a plate; food cost ฿100 a day."""
import os
os.environ["OSHINEI_NO_SWAP"] = "1"
import datetime as dt
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))
import main  # noqa: E402
import conftest as _cf  # noqa: E402
import periods as P  # noqa: E402
import ingredient_groups as IG  # noqa: E402

TH_TODAY = (dt.datetime.utcnow() + dt.timedelta(hours=7)).date()
YESTERDAY = (TH_TODAY - dt.timedelta(days=1)).isoformat()
FIRST = TH_TODAY - dt.timedelta(days=440)
FLOOR = TH_TODAY - dt.timedelta(days=100)
COVERS = 20
DISHES = [  # sku_id, code, name, unit cost, qty per branch per day
    (10, "B1010", "(B) ซาชิมิแซลมอน (1 ชิ้น)", 10.0, 4),
    (11, "B1011", "(B) ซาชิมิรวม แซลมอน ทูน่า", 30.0, 1),
    (12, "B1012", "(B) ปลาเนื้อขาว ซาชิมิ", 5.0, 2),
    (13, "B1013", "(B) เนื้อวากิวสไลด์", 20.0, 1),
    (14, "B1014", "(B) ซูชิทูน่า", 0.0, 2),
    (15, "BUFFETSALMON", "บุฟเฟ่ต์แซลมอน", 99.0, 1),
]


@pytest.fixture(scope="module")
def ig(tmp_path_factory):
    db = tmp_path_factory.mktemp("hvm") / "test.db"
    _cf._build_db(str(db))
    con = sqlite3.connect(str(db))
    for t in ("sales_transaction", "sales_sku_daily", "sales_sku_monthly", "sales_month_summary", "sale_bill_cogs"):
        con.execute(f"DELETE FROM {t}")
    con.executemany("INSERT INTO skus(sku_id,sku_code,description,category_id,unit,business_type,active) VALUES(?,?,?,3,'plate','buffet',1)",
                    [(s, c, n) for s, c, n, _, _ in DISHES])
    con.executemany("INSERT INTO sku_cost(sku_id,unit_cost) VALUES(?,?)", [(s, u) for s, _, _, u, _ in DISHES])
    month: dict = {}
    d = FIRST
    while d <= TH_TODAY:
        ds = d.isoformat()
        n = 1 if d == TH_TODAY else 10
        for bid in (1, 2):
            con.executemany(
                "INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,food_subtotal,drink_subtotal,"
                "other_subtotal,sp_discount,discount,service_charge,total_inc_vat,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,'Easy')",
                [(bid, f"{ds}-{bid}-{k}", f"{ds}T12:{k:02d}:00", 2, 50.0, 0.0, 0.0, 0.0, 0.0, 0.0, 53.5) for k in range(n)])
            if n != 10:
                continue
            for sid, _, _, _, q in DISHES:
                if d >= FLOOR:
                    con.execute("INSERT INTO sales_sku_daily VALUES(?,?,?,?,?)", (bid, ds, sid, q, 0.0))
                month[(bid, ds[:7], sid)] = month.get((bid, ds[:7], sid), 0) + q
        d += dt.timedelta(days=1)
    for (bid, ym, sid), q in month.items():
        con.execute("INSERT INTO sales_sku_monthly(branch_id,period_month,sku_id,qty,subtotal_ex_vat,tax_total,total_inc_vat,source)"
                    " VALUES(?,?,?,?,0,0,0,'Easy')", (bid, ym, sid, q))
    con.commit(); con.close()
    import core
    prev = core.DB_PATH
    core.DB_PATH = db
    main.DB_PATH = db
    main._tls.con = None
    try: main.invalidate_cache()
    except Exception: pass
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        core.DB_PATH = prev
        main.DB_PATH = prev
        main._tls.con = None
        try: main.invalidate_cache()
        except Exception: pass


# ── the groups ───────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name,want", [
    ("(B) ซาชิมิแซลมอน (1 ชิ้น ต่อ 1 เสิร์ฟ)", ["salmon"]),
    ("(TW) ชุดแซลม่อนย่าง", ["salmon"]),
    ("(B) ซาชิมิรวม แซลมอน ทูน่า", ["salmon", "tuna"]),
    ("(BF)ซูชิโอโทโร่สับอูนิและทัฟเฟิล", ["tuna"]),
    ("(B) ซาชิมิกุ้งแดงอาร์เจนติน่า (1 ชิ้น)", ["amaebi"]),
    ("(B) หอยเชลลล์ซาชิมิ", ["scallop"]),
    ("(B) แซลมอน อูนากิ ซอสมันปูโรล", ["salmon", "eel"]),
    ("(B) หอยนางรมสด เสิร์ฟพร้อมน้ำจิ้ม", ["oyster"]),
    ("(B) ลิ้นวัวสไลด์ (5 ชิ้น)", ["beef"]),
    ("(B) เนื้อวากิวทาทากิ", ["beef"]),
    ("(B) ปลาเนื้อขาว ซาชิมิ (1 ชิ้น)", []),            # a white fish
    ("ข้าวผัดเนื้อปู", []),
    ("ซุปเนื้อปลา", []),
    ("(B) ซาชิมิปลาฮามาจิ", ["hamachi"]),
])
def test_a_dish_belongs_to_the_groups_its_name_matches(name, want):
    assert IG.match(name, "OTHBFSSM00001") == want


def test_a_package_code_never_counts():
    assert IG.match("บุฟเฟ่ต์แซลมอน", "BUFFETSALMON") == []
    assert IG.match("บุฟเฟ่ต์แซลมอน", "BUFFET799") == []
    assert IG.match("แซลมอน", "B0035") == ["salmon"]          # a B0xxx dish is a dish
    assert [g[0] for g in IG.GROUPS] == ["salmon", "tuna", "hamachi", "amaebi", "scallop", "eel", "oyster", "beef"]


# ── the figures ──────────────────────────────────────────────────────────────────────────────────────────────────────
def test_cost_per_head_plates_per_head_cost_per_plate_and_share(ig):
    d = ig.get("/api/cost/ingredient-groups", params={"kind": "month"}).json()
    w = P.resolve("month", YESTERDAY)
    assert d["period"]["cur"] == list(w["cur"]) and d["period"]["prev"] == list(w["prev"])
    assert [g["slug"] for g in d["groups"]] == list(IG.SLUGS) and d["groups"][3]["label"] == "กุ้งหวาน"
    n = w["days"]
    for row in [d["chain"]] + d["branches"]:
        k = 2 if row is d["chain"] else 1
        assert row["customers"] == COVERS * n * k
        s = row["cells"]["salmon"]
        assert s["cost"] == pytest.approx(70 * n * k) and s["cost_per_head"] == pytest.approx(3.5)
        assert s["plates_per_head"] == pytest.approx(0.25) and s["cost_per_plate"] == pytest.approx(14.0)
        assert s["share_of_food_cost"] == pytest.approx(70.0) and s["uncosted_plates"] == 0
        assert row["food_cost_per_head"] == pytest.approx(5.0)
        # the mixed dish counts in tuna as well, at its full cost; the uncosted sushi counts in plates, not in cost
        t = row["cells"]["tuna"]
        assert t["cost"] == pytest.approx(30 * n * k) and t["plates"] == pytest.approx(3 * n * k)
        assert t["uncosted_plates"] == pytest.approx(2 * n * k) and t["cost_per_plate"] == pytest.approx(30.0)
        assert t["plates_per_head"] == pytest.approx(0.15)
        b = row["cells"]["beef"]                                    # ปลาเนื้อขาว is not beef
        assert b["plates"] == pytest.approx(n * k) and b["cost_per_head"] == pytest.approx(1.0)
        assert row["cells"]["oyster"]["cost_per_head"] == 0 and row["cells"]["oyster"]["cost_per_plate"] is None
    assert {b["branch"] for b in d["branches"]} == {"BranchA", "BranchB"}


def test_prev_to_date_is_the_same_days_and_unchanged(ig):
    for kind in ("week", "month"):
        d = ig.get("/api/cost/ingredient-groups", params={"kind": kind}).json()
        w = P.resolve(kind, YESTERDAY)
        assert d["period"]["prev"] == list(w["prev"]) and d["period"]["cur"][1] == YESTERDAY
        assert w["prev"][0] >= FLOOR.isoformat()                  # inside the day table: compared
        s = d["chain"]["cells"]["salmon"]
        assert s["prev"]["cost_per_head"] == pytest.approx(3.5)
        assert s["change"]["prev"] == {"cost_per_head": 0.0, "plates_per_head": 0.0, "cost_per_plate": 0.0}
        c = s["cause"]["prev"]
        assert c["total"] == 0 and c["main"] is None and c["customers"] == 0


def test_before_the_day_table_there_is_a_reason_not_a_figure(ig):
    d = ig.get("/api/cost/ingredient-groups", params={"kind": "week"}).json()
    s = d["chain"]["cells"]["salmon"]
    assert s["ly"] is None and s["change"]["ly"]["cost_per_head"] is None
    assert d["period"]["reasons"]["ly"].startswith("ต้นทุนรายวันมีตั้งแต่")
    assert s["cause"]["ly"]["total"] is None and s["cause"]["ly"]["reason"]


def test_a_finished_month_reads_the_month_table(ig):
    last = TH_TODAY.replace(day=1) - dt.timedelta(days=1)
    d = ig.get("/api/cost/ingredient-groups", params={"kind": "month", "pick": last.replace(day=10).isoformat()}).json()
    assert d["period"]["complete"] is True
    assert d["chain"]["cells"]["salmon"]["cost"] == pytest.approx(70 * last.day * 2)
    assert d["chain"]["cells"]["salmon"]["ly"]["cost_per_head"] == pytest.approx(3.5)   # a whole month a year back: the month table


def test_branch_scope(ig):
    d = ig.get("/api/cost/ingredient-groups", params={"kind": "month", "branch": "BranchA"}).json()
    assert [b["branch"] for b in d["branches"]] == ["BranchA"]
    assert d["chain"]["customers"] == d["branches"][0]["customers"]


def test_menus_of_a_group_flag_the_mixed_dish(ig):
    d = ig.get("/api/cost/ingredient-groups/menus", params={"kind": "month", "branch": "BranchA", "slug": "tuna"}).json()
    by = {m["sku_code"]: m for m in d["menus"]}
    assert set(by) == {"B1011", "B1014"}
    assert by["B1011"]["mixed"] is True and by["B1011"]["groups"] == ["salmon", "tuna"]
    assert by["B1014"]["cost_per_plate"] is None and by["B1014"]["uncosted_plates"] == by["B1014"]["plates"] > 0
    allb = ig.get("/api/cost/ingredient-groups/menus", params={"kind": "month", "slug": "tuna"}).json()
    assert {m["sku_code"]: m["plates"] for m in allb["menus"]}["B1011"] == pytest.approx(2 * by["B1011"]["plates"])
    assert ig.get("/api/cost/ingredient-groups/menus", params={"slug": "nope"}).status_code == 400
    assert ig.get("/api/cost/ingredient-groups", params={"kind": "decade"}).status_code == 400


def test_cost_roles_only():
    import auth
    for path in ("/api/cost/ingredient-groups", "/api/cost/ingredient-groups/menus"):
        assert not auth.path_allowed("GET", path, auth.modules_for_role("marketing"))
        assert not auth.path_allowed("GET", path, auth.modules_for_role("operation"))
        assert auth.path_allowed("GET", path, {"sales.overview", "profit.pnl"})
        assert auth.path_allowed("GET", path, {"sales.overview", "cost.analysis"})
    core = (HERE.parent / "js" / "01-core.js").read_text(encoding="utf-8")
    assert "hvmonitor:['cost.analysis','profit.pnl']" in core
    assert 'data-needs="/api/cost/ingredient-groups"' in (HERE.parent / "tabs" / "60-hvmonitor.html").read_text(encoding="utf-8")


# ── why the cost moved: ลูกค้า · กินต่อหัว · ต้นทุนต่อจาน (LMDI, baht) ──────────────────────────────────────────────────
def _cell(cust, pph, cpp):
    q = cust * pph
    return {"cost": q * cpp, "_cust": cust, "_costed": q}


def test_doubled_customers_is_all_customers():
    s = IG.split(_cell(200, 2, 10), _cell(100, 2, 10))
    assert s["total"] == pytest.approx(2000) and s["customers"] == pytest.approx(2000)
    assert s["plates_per_head"] == pytest.approx(0) and s["cost_per_plate"] == pytest.approx(0) and s["main"] == "customers"


def test_more_plates_per_head_is_eating():
    s = IG.split(_cell(100, 3, 10), _cell(100, 2, 10))
    assert s["plates_per_head"] == pytest.approx(1000) and s["customers"] == pytest.approx(0) and s["main"] == "plates_per_head"


def test_a_dearer_plate_is_the_cost_per_plate():
    s = IG.split(_cell(100, 2, 12), _cell(100, 2, 10))
    assert s["cost_per_plate"] == pytest.approx(400) and s["main"] == "cost_per_plate" and s["total_pct"] == 20.0


@pytest.mark.parametrize("cur,base", [((133, 9.1, 14.6), (131, 9.5, 13.8)), ((90, 1.4, 31), (120, 1.1, 27)), ((5000, 8.7, 16.3), (5400, 9.0, 14.5))])
def test_the_parts_add_up_to_the_change(cur, base):
    s = IG.split(_cell(*cur), _cell(*base))
    assert abs(s["customers"] + s["plates_per_head"] + s["cost_per_plate"] - s["total"]) < 1


def test_no_base_gives_no_parts_and_a_reason():
    s = IG.split(_cell(100, 2, 10), None)
    assert s["total"] is None and s["main"] is None and s["reason"]
    z = IG.split(_cell(100, 2, 10), {"cost": 0, "_cust": 100, "_costed": 0})
    assert z["customers"] is None and z["total"] == 2000 and z["reason"] and z["total_pct"] is None
