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


# ── owner 2026-10-25 · W vs W: the section's own week picker ("w1 vs w7", Monday–Sunday weeks) ─────────────────────────────────
def test_weeks_are_monday_to_sunday_with_iso_numbers_across_the_year_boundary():
    import datetime as dt
    ws = P.iso_weeks("2025-12-15", "2026-01-07")
    assert [w["key"] for w in ws] == ["2026-W02", "2026-W01", "2025-W52", "2025-W51"]
    for w in ws:
        a, b = dt.date.fromisoformat(w["from"]), dt.date.fromisoformat(w["to"])
        assert a.weekday() == 0 and (b - a).days == 6
    now, w1, w52 = ws[0], ws[1], ws[2]
    assert now["partial"] and now["closed_to"] == "2026-01-07" and now["days"] == 3
    assert now["label"] == "W2 · 5–7 ม.ค. (ถึงวันที่ปิด)"
    assert w1["from"] == "2025-12-29" and w1["week"] == 1 and w1["year"] == 2026 and not w1["partial"]
    assert w1["label"] == "W1 · 29 ธ.ค.–4 ม.ค."                         # W1 starts in the old calendar year
    assert w52["label"] == "W52 · 22–28 ธ.ค. 68"                          # another ISO year: its year is named
    # a 53-week year (2026: 1 Jan is a Thursday); a week whose Monday is before the first day is not offered
    assert P.iso_weeks("2026-12-28", "2027-01-03")[0]["key"] == "2026-W53"
    assert [w["from"] for w in P.iso_weeks("2026-10-14", "2026-10-25")] == ["2026-10-19"]
    assert P.iso_weeks(None, "2026-10-25") == [] and P.iso_weeks("2026-10-26", "2026-10-25") == []


def test_the_answer_lists_the_weeks_the_day_data_holds(ig):  # noqa: F811
    from test_ingredient_groups import FLOOR
    d = ig.get(URL, params={"period": "week", "date": YESTERDAY}).json()
    assert d["latest_day"] == YESTERDAY
    ws = d["weeks"]
    assert ws and ws[0]["closed_to"] == YESTERDAY and ws[0]["from"] <= YESTERDAY <= ws[0]["to"]
    assert all(w["from"] >= FLOOR.isoformat() for w in ws) and len(ws) >= 13
    assert [w["from"] for w in ws] == sorted((w["from"] for w in ws), reverse=True)


def test_a_week_in_progress_compares_the_same_weekdays_of_the_other_week(ig):  # noqa: F811
    import datetime as dt
    ws = ig.get(URL, params={"period": "week"}).json()["weeks"]
    a, b = ws[0], ws[6]                                                   # "w1 vs w7"
    n = a["days"]
    bt = (dt.date.fromisoformat(b["from"]) + dt.timedelta(days=n - 1)).isoformat()
    d = ig.get(URL, params={"period": "range", "date_from": a["from"], "date_to": a["closed_to"],
                            "base_from": b["from"], "base_to": bt}).json()
    assert d["period"]["cur"] == [a["from"], a["closed_to"]] and d["base"]["from"] == b["from"] and d["base"]["to"] == bt
    ch = d["chain"]
    assert ch["customers"] == ch["prev_customers"] == COVERS * n * 2       # the same weekdays: the same days on both sides
    assert ch["cells"]["salmon"]["prev"]["plates"] == pytest.approx(5 * n * 2)
    assert ch["cells"]["salmon"]["change"] == {"plates": 0.0, "plates_per_head": 0.0}


def test_the_base_may_be_after_the_chosen_week_but_never_overlap_it(ig):  # noqa: F811
    import datetime as dt
    ws = ig.get(URL, params={"period": "week"}).json()["weeks"]
    early, late = ws[6], ws[1]                                            # the base is the LATER week
    r = ig.get(URL, params={"period": "range", "date_from": early["from"], "date_to": early["to"],
                            "base_from": late["from"], "base_to": late["to"]})
    assert r.status_code == 200
    d = r.json()
    assert d["base"]["kind"] == "range" and d["base"]["from"] == late["from"] and d["period"]["cur"] == [early["from"], early["to"]]
    assert d["chain"]["prev_customers"] == COVERS * 7 * 2
    # an overlap is refused in formal Thai, from either side
    mid = (dt.date.fromisoformat(early["from"]) + dt.timedelta(days=3)).isoformat()
    for bf, bt in ((mid, late["to"]), (ws[7]["from"], mid), (early["from"], early["to"])):
        r = ig.get(URL, params={"period": "range", "date_from": early["from"], "date_to": early["to"], "base_from": bf, "base_to": bt})
        assert r.status_code == 400 and r.json()["detail"] == "ช่วงที่ใช้เทียบต้องไม่ซ้อนทับกับช่วงที่เลือก"
    # a base past the last closed day is still refused
    r = ig.get(URL, params={"period": "range", "date_from": early["from"], "date_to": early["to"],
                            "base_from": YESTERDAY, "base_to": (dt.date.fromisoformat(YESTERDAY) + dt.timedelta(days=1)).isoformat()})
    assert r.status_code == 400 and "วันล่าสุดที่มีข้อมูลครบ" in r.json()["detail"]


def test_the_top_bars_period_gives_the_cost_views_windows(ig):  # noqa: F811
    """DV4: the จำนวนจาน view of High Value Product Monitor follows the top bar - kind (+ pick / date_from / date_to) resolves the same
    windows as /api/cost/ingredient-groups (cur and prev), the branch scope included; a bad kind is refused in formal Thai."""
    for kind in ("week", "month", "year"):
        c = ig.get("/api/cost/ingredient-groups", params={"kind": kind, "pick": YESTERDAY}).json()
        p = ig.get(URL, params={"kind": kind, "pick": YESTERDAY}).json()
        assert p["period"]["cur"] == c["period"]["cur"] and p["period"]["prev"] == c["period"]["prev"], kind
        assert p["base"]["kind"] == "prev"
        assert p["chain"]["customers"] == c["chain"]["customers"]
        for s in IG.SLUGS:
            assert p["chain"]["cells"][s]["plates"] == pytest.approx(c["chain"]["cells"][s]["plates"]), (kind, s)
    w = P.resolve("week", YESTERDAY)
    r = ig.get(URL, params={"kind": "range", "date_from": w["cur"][0], "date_to": w["cur"][1], "branch": "BranchA"}).json()
    assert r["period"]["cur"] == list(w["cur"]) and [b["branch"] for b in r["branches"]] == ["BranchA"]
    assert r["chain"]["customers"] == r["branches"][0]["customers"]
    # the top bar's kind wins over the old period parameter
    k = ig.get(URL, params={"kind": "week", "pick": YESTERDAY, "period": "day"}).json()
    assert k["period"]["cur"] == list(w["cur"])
    bad = ig.get(URL, params={"kind": "decade"})
    assert bad.status_code == 400 and bad.json()["detail"] == "ช่วงเวลาไม่ถูกต้อง"
