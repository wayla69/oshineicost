# -*- coding: utf-8 -*-
"""วัตถุดิบมูลค่าสูง · จำนวนจาน (owner 2026-10-25, ยอดขายรายวัน · เมนูพุ่งผิดปกติ: "โชว์เป็นจำนวนจานที่ออก ไม่ต้องจำนวนเงิน และให้เลือก
ช่วงเวลาได้"): GET /api/sales/ingredient-groups-plates - the 8 groups of backend/ingredient_groups.py in plates only, on the card's
period (day · week · mtd · range, base_from / base_to), the change in plates split into ลูกค้า · กินต่อหัว (LMDI, two parts).

The fixture is the High Value Product Monitor's (test_ingredient_groups.ig): per branch per day 20 covers, salmon 5 plates (4 + the
mixed set), tuna 3 (the mixed set + 2 uncosted sushi), beef 1; the day table holds the last 100 days."""
import json

import pytest

import ingredient_groups as IG
import periods as P
from test_ingredient_groups import COVERS, YESTERDAY, ig  # noqa: F401  (the module fixture)

URL = "/api/sales/ingredient-groups-plates"


def _keys(o, out=None):
    out = set() if out is None else out
    if isinstance(o, dict):
        for k, v in o.items():
            out.add(k)
            _keys(v, out)
    elif isinstance(o, list):
        for v in o:
            _keys(v, out)
    return out


def test_plates_per_group_and_per_head_on_the_cards_period(ig):  # noqa: F811
    d = ig.get(URL, params={"period": "mtd", "date": YESTERDAY}).json()
    w = P.resolve("month", YESTERDAY)
    assert d["period"]["cur"] == list(w["cur"]) and d["period"]["prev"] == list(w["prev"])
    assert d["base"]["kind"] == "prev" and [g["slug"] for g in d["groups"]] == list(IG.SLUGS)
    n = w["days"]
    for row in [d["chain"]] + d["branches"]:
        k = 2 if row is d["chain"] else 1
        assert row["customers"] == COVERS * n * k
        s = row["cells"]["salmon"]
        assert s["plates"] == pytest.approx(5 * n * k) and s["plates_per_head"] == pytest.approx(0.25)
        assert row["cells"]["tuna"]["plates"] == pytest.approx(3 * n * k)          # the uncosted sushi counts in plates
        assert row["cells"]["beef"]["plates"] == pytest.approx(n * k)               # ปลาเนื้อขาว is not beef
        assert s["prev"]["plates_per_head"] == pytest.approx(0.25)
        assert s["change"] == {"plates": 0.0, "plates_per_head": 0.0}
        assert s["cause"]["total"] == 0 and s["cause"]["main"] is None
    assert {b["branch"] for b in d["branches"]} == {"BranchA", "BranchB"}


def test_no_cost_key_in_the_answer_and_the_cards_gate_not_a_cost_one(ig):  # noqa: F811
    import auth
    for params in ({"period": "day"}, {"period": "week"}, {"period": "mtd", "branch": "BranchA"}):
        d = ig.get(URL, params=params).json()
        bad = sorted(k for k in _keys(d) if "cost" in k.lower() or k in ("food", "revenue", "gp"))
        assert bad == [], bad
        assert "฿" not in json.dumps(d, ensure_ascii=False)
    # every role that sees the menu-spike card reaches it (marketing too); no cost module needed
    for role in ("marketing", "management"):
        mods = auth.modules_for_role(role)
        assert auth.path_allowed("GET", "/api/sales/menu-spike", mods) == auth.path_allowed("GET", URL, mods) is True
    assert not auth.path_allowed("GET", "/api/cost/ingredient-groups", auth.modules_for_role("marketing"))
    assert auth.path_allowed("GET", URL, {"sales.overview", "menu.analysis"})


def test_groups_and_plates_identical_to_the_cost_view(ig):  # noqa: F811
    for kind, period in (("month", "mtd"), ("week", "week")):
        c = ig.get("/api/cost/ingredient-groups", params={"kind": kind, "pick": YESTERDAY}).json()
        p = ig.get(URL, params={"period": period, "date": YESTERDAY}).json()
        assert p["period"]["cur"] == c["period"]["cur"]
        assert p["groups"] == c["groups"]
        rows_c = {r.get("branch", "chain"): r for r in [c["chain"]] + c["branches"]}
        rows_p = {r.get("branch", "chain"): r for r in [p["chain"]] + p["branches"]}
        assert set(rows_c) == set(rows_p)
        for name, rc in rows_c.items():
            rp = rows_p[name]
            assert rp["customers"] == rc["customers"]
            for s in IG.SLUGS:
                assert rp["cells"][s]["plates"] == pytest.approx(rc["cells"][s]["plates"]), (name, s)
                assert rp["cells"][s]["plates_per_head"] == pytest.approx(rc["cells"][s]["plates_per_head"]), (name, s)


def test_a_chosen_base_and_its_checks(ig):  # noqa: F811
    y = P._d(YESTERDAY)
    import datetime as dt
    f, t = (y - dt.timedelta(days=6)).isoformat(), YESTERDAY
    bf, bt = (y - dt.timedelta(days=20)).isoformat(), (y - dt.timedelta(days=7)).isoformat()
    d = ig.get(URL, params={"period": "range", "date_from": f, "date_to": t, "base_from": bf, "base_to": bt}).json()
    assert d["base"] == {"kind": "range", "from": bf, "to": bt, "label_th": P.label_th(bf, bt)}
    assert d["period"]["prev"] == [bf, bt] and d["period"]["cur"] == [f, t]
    ch = d["chain"]
    assert ch["prev_customers"] == COVERS * 14 * 2 and ch["customers"] == COVERS * 7 * 2
    s = ch["cells"]["salmon"]
    # twice as long a base, the same per head: every plate of the change is ลูกค้า
    assert s["cause"]["total"] == pytest.approx(-5 * 7 * 2) and s["cause"]["main"] == "customers"
    assert s["cause"]["plates_per_head"] == pytest.approx(0, abs=0.05)
    assert ig.get(URL, params={"period": "range", "date_from": f, "date_to": t, "base_from": bf}).status_code == 400
    assert ig.get(URL, params={"period": "range", "date_from": f, "date_to": t, "base_from": f, "base_to": t}).status_code == 400
    assert ig.get(URL, params={"period": "decade"}).status_code == 400
    assert ig.get(URL, params={"period": "range"}).status_code == 400


def test_branch_scope(ig):  # noqa: F811
    d = ig.get(URL, params={"period": "week", "branch": "BranchB"}).json()
    assert [b["branch"] for b in d["branches"]] == ["BranchB"]
    assert d["chain"]["customers"] == d["branches"][0]["customers"]


# ── why the plates moved: ลูกค้า · กินต่อหัว (LMDI, two parts, in plates) ─────────────────────────────────────────────────────
def test_doubled_customers_same_per_head_is_all_customers():
    s = IG.split_plates(400, 200, 200, 100)
    assert s["total"] == 200 and s["customers"] == pytest.approx(200) and s["plates_per_head"] == pytest.approx(0)
    assert s["main"] == "customers" and s["total_pct"] == 100.0


def test_more_plates_per_head_is_eating():
    s = IG.split_plates(300, 100, 200, 100)
    assert s["plates_per_head"] == pytest.approx(100) and s["customers"] == pytest.approx(0) and s["main"] == "plates_per_head"


@pytest.mark.parametrize("p1,n1,p0,n0", [(1213, 133, 1245, 131), (126, 90, 132, 120), (43500, 5000, 48600, 5400), (9475, 918, 9927, 989)])
def test_the_two_parts_add_up_to_the_change(p1, n1, p0, n0):
    s = IG.split_plates(p1, n1, p0, n0)
    assert abs(s["customers"] + s["plates_per_head"] - s["total"]) <= 0.1
    assert s["main"] == max(("customers", "plates_per_head"), key=lambda k: abs(s[k]))


def test_no_base_gives_no_parts_and_a_reason():
    s = IG.split_plates(100, 50, 0, 0)
    assert s["customers"] is None and s["main"] is None and s["reason"] and s["total"] == 100 and s["total_pct"] is None
