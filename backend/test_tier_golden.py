"""The buffet price-tier code paths, pinned on a fixture that HAS the tier fact
tables (Phase 8b, 2026-09).

The shared fixture deliberately lacks menu_fact_* (test_price_tier_graceful_
without_fact_tables pins the degrade path), so until now §11 of the Management
report (_mgt_tier: tier rows, the promo breakdown, the cost drivers), the
workbook's "Buffet Price Tiers" sheet and /api/reports/price-tier (232 lines)
ran in no test at all. This module copies the shared dataset, adds a small
tier-dimensioned fact universe for every day 2026-04-01 → 2026-06-03 (so the
daily, weekly and monthly comparison windows all have a previous period) and
pins what those paths produce.

  regenerate:  GOLDEN_UPDATE=1 python -m pytest backend/test_tier_golden.py
"""
import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import main  # noqa: E402
import conftest as _cf  # noqa: E402
from test_golden import _canon  # noqa: E402
from test_mgt_xlsx_golden import _dump as _dump_xlsx  # noqa: E402

GOLDEN = HERE / "golden" / "tier"
UPDATE = os.environ.get("GOLDEN_UPDATE") == "1"

# per tier: (bills, pax) a day by branch and month; cogs/rev per head are constants.
# Tier codes must be ones _PT_ORDER knows (789, 590): an unknown code is never listed.
APR = {1: {"789": (13, 26.0), "590": (8, 14.0), "promo": (3, 3.0)},
       2: {"789": (7, 13.0), "590": (4, 7.0), "promo": (1, 1.5)}}
MAY = {1: {"789": (12, 24.0), "590": (8, 14.0), "promo": (3, 3.0)},      # a few guests moved down
       2: {"789": (6, 12.0), "590": (4, 7.0), "promo": (1, 1.5)}}
JUN = {1: {"789": (10, 20.0), "590": (9, 16.0), "promo": (2, 2.0)},      # and more of them in June
       2: {"789": (5, 10.0), "590": (4, 8.0), "promo": (1, 1.0)}}
BY_MONTH = {"04": APR, "05": MAY, "06": JUN}
COGS_HEAD = {"789": 210.0, "590": 180.0, "promo": 150.0}
REV_HEAD = {"789": 789.0, "590": 590.0, "promo": 600.0}
# ingredient groups / menu skus inside a paid tier: (qty per head, cogs per head)
ENTS = [("g:salmon", 2.0, 90.0), ("g:beef", 1.0, 60.0), ("s:2", 1.5, 12.0), ("s:3", 0.2, 2.0)]
# the promo bucket: pz:<kind>:<net unit price>
PROMO = {(1, "04"): [("pz:disc:710", 2, 2.0), ("pz:free:0", 1, 1.0)],
         (2, "04"): [("pz:disc:539", 1, 1.5)],
         (1, "05"): [("pz:disc:710", 2, 2.0), ("pz:free:0", 1, 1.0)],
         (1, "06"): [("pz:disc:710", 1, 1.0), ("pz:mixed:789", 1, 1.0)],
         (2, "05"): [("pz:disc:539", 1, 1.5)],
         (2, "06"): [("pz:disc:710", 1, 1.0)]}


def add_tier_facts(db: Path):
    con = sqlite3.connect(str(db))
    con.executescript("""
      CREATE TABLE menu_fact_total(branch_id INT, day TEXT, tier TEXT, bills INT, pax REAL, cogs REAL, rev REAL, hc REAL,
                                   PRIMARY KEY(branch_id, day, tier));
      CREATE TABLE menu_fact_day(branch_id INT, day TEXT, tier TEXT, ent TEXT, bills INT, pax REAL, qty REAL,
                                 ent_cogs REAL, ent_hc REAL, cogs REAL, rev REAL, hc REAL,
                                 PRIMARY KEY(branch_id, day, tier, ent));
      CREATE TABLE menu_fact_meta(ent TEXT PRIMARY KEY, label TEXT, kind TEXT, unit_cost REAL, members TEXT);
      CREATE TABLE menu_fact_state(k TEXT PRIMARY KEY, v TEXT);
    """)
    # The shared dataset trades on six days only, so every comparison window the
    # report uses (last week, last month) would be empty and has_prev False. This
    # copy trades every day from 1 April: one bill a day per branch, like the
    # shared rows, so the tier section's previous-period columns are exercised.
    for t in ("sales_transaction", "sales_sku_daily", "sale_bill_cogs", "sale_payment"):
        con.execute(f"DELETE FROM {t}")
    d = dt.date(2026, 4, 1)
    while d <= dt.date(2026, 6, 3):
        day = d.isoformat()
        month = day[5:7]
        for bid in (1, 2):
            rcpt = f"{day}-{bid}001-{bid}"
            con.execute("INSERT INTO sales_sku_daily VALUES(?,?,?,?,?)", (bid, day, 2, 50, 0.0))
            con.execute("INSERT INTO sales_sku_daily VALUES(?,?,?,?,?)", (bid, day, 3, 5, 500.0))
            con.execute("INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,"
                        "food_subtotal,drink_subtotal,other_subtotal,discount,service_charge,total_inc_vat,source)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?, 'Easy')",
                        (bid, rcpt, f"{day}T12:00:00", 4, 900, 100, 0, 20, 50, 1070.0))
            con.execute("INSERT INTO sale_bill_cogs VALUES(?,?,?,?)", (rcpt, bid, day, 520.0))
            con.execute("INSERT INTO sale_payment VALUES(?,?,?,?)", (bid, rcpt, "Cash", 1070.0))
            for tier, (bills, pax) in BY_MONTH[month][bid].items():
                cogs, rev = pax * COGS_HEAD[tier], pax * REV_HEAD[tier]
                con.execute("INSERT INTO menu_fact_total VALUES(?,?,?,?,?,?,?,0)",
                            (bid, day, tier, bills, pax, cogs, rev))
                if tier == "promo":
                    for ent, b2, p2 in PROMO[(bid, month)]:
                        con.execute("INSERT INTO menu_fact_day VALUES(?,?,?,?,?,?,0,0,0,?,?,0)",
                                    (bid, day, tier, ent, b2, p2, p2 * COGS_HEAD[tier], p2 * REV_HEAD[tier]))
                else:
                    for ent, q_head, c_head in ENTS:
                        con.execute("INSERT INTO menu_fact_day VALUES(?,?,?,?,?,?,?,?,0,?,?,0)",
                                    (bid, day, tier, ent, bills, pax, pax * q_head, pax * c_head, cogs, rev))
        d += dt.timedelta(days=1)
    con.executemany("INSERT INTO menu_fact_meta VALUES(?,?,?,?,?)", [
        ("g:salmon", "แซลมอน", "group", 0.0, "B0001,B0002"),
        ("g:beef", "เนื้อ", "group", 0.0, "B0003"),
        ("s:2", "(B) แซลมอน", "sku", 8.0, "B0001"),
        ("s:3", "(A) สเต๊ก", "sku", 10.0, "A0001"),
    ])
    con.executemany("INSERT INTO menu_fact_state VALUES(?,?)",
                    [("last_build", "2026-06-04 03:00:00"), ("hc_cost", "30")])
    con.commit()
    con.close()


@pytest.fixture(scope="module")
def tier_client(tmp_path_factory):
    db = tmp_path_factory.mktemp("tier") / "test.db"
    _cf._build_db(str(db))
    add_tier_facts(db)
    import core
    prev = core.DB_PATH
    core.DB_PATH = db          # get_db() reads core.DB_PATH; main.DB_PATH is a copy
    main.DB_PATH = db
    main._tls.con = None
    try: main.invalidate_cache()
    except Exception: pass
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        # global state: point every later module back at the shared dataset and
        # drop anything this dataset left in the ttl cache
        core.DB_PATH = prev
        main.DB_PATH = prev
        main._tls.con = None
        try: main.invalidate_cache()
        except Exception: pass


def _pin(name: str, got: str):
    f = GOLDEN / name
    if UPDATE or not f.exists():
        GOLDEN.mkdir(parents=True, exist_ok=True)
        f.write_text(got, encoding="utf-8")
        if not UPDATE:
            pytest.skip(f"golden file created: {f.name}")
        return
    want = f.read_text(encoding="utf-8")
    if got != want:
        gl, wl = got.splitlines(), want.splitlines()
        for i, (a, b) in enumerate(zip(gl, wl)):
            if a != b:
                pytest.fail(f"{f.name} differs at line {i + 1}:\n  golden: {b[:160]}\n  now:    {a[:160]}")
        pytest.fail(f"{f.name}: length differs ({len(wl)} -> {len(gl)} lines)")


def _json(obj) -> str:
    return json.dumps(_canon(obj), ensure_ascii=False, sort_keys=True, indent=1) + "\n"


MGT_CASES = [{"period": "daily"}, {"period": "weekly"}, {"period": "monthly"},
             {"period": "monthly", "lang": "th"}, {"period": "monthly", "branch": "BranchA"}]


@pytest.mark.parametrize("params", MGT_CASES, ids=["_".join(f"{k}-{v}" for k, v in sorted(p.items())) for p in MGT_CASES])
def test_management_tier_section_is_pinned(tier_client, params):
    j = tier_client.get("/api/reports/management", params=params).json()
    assert j.get("available") and j.get("tier"), "the tier section must be present on this fixture"
    _pin("mgt_tier__" + "_".join(f"{k}-{v}" for k, v in sorted(params.items())) + ".json", _json(j["tier"]))


def test_management_tier_section_reads_right(tier_client):
    t = tier_client.get("/api/reports/management", params={"period": "monthly"}).json()["tier"]
    labels = {r["tier"]: r["label"] for r in t["rows"]}
    assert labels == {"789": "฿789", "590": "฿590", "promo": "Promo/other price"}
    by = {r["tier"]: r for r in t["rows"]}
    # the monthly report is the latest COMPLETE month: May (31 days) against April (30)
    assert by["789"]["pax"] == 31 * 36 and by["789"]["pax_prev"] == 30 * 39
    assert by["789"]["cogs_head"] == 210.0 and by["789"]["food_pct"] == 26.6
    assert t["pax"] == round(31 * 61.5) and t["pax_prev"] == 30 * 64.5
    # guests moved down a tier → the mix effect is negative and both paid tiers cover ≥90%
    assert t["mix_effect"] is not None and t["mix_effect"] < 0
    # the promo bucket names its discounts against the nearest standard price
    disc = t["promo"]["discounts"][0]
    assert (disc["unit"], disc["of_tier"], disc["pct_of_tier"], disc["off_pct"]) == (710, 789, 90.0, 10.0)
    assert set(t["promo"]["kinds"]) == {"disc", "free"}
    # cost drivers: the salmon group is the biggest cost per head on both tiers
    drv = t["drivers"]
    assert drv["tier_order"] == ["590", "789"]
    assert drv["by_tier"]["789"]["groups"][0]["label"] == "แซลมอน"
    assert drv["by_tier"]["789"]["groups"][0]["cogs_head"] == 90.0
    assert drv["by_tier"]["789"]["ref_tier"] == "590"
    assert drv["default_tier"] == "590"            # the worse food-cost % of the two paid tiers


def test_management_workbook_tier_sheet_is_pinned(tier_client):
    r = tier_client.get("/api/reports/management.xlsx", params={"period": "monthly"})
    assert r.status_code == 200
    dump = _dump_xlsx(r)
    assert "## Buffet Price Tiers" in dump or "Price Tiers" in dump
    _pin("mgt_xlsx__period-monthly.txt", dump)


PT_CASES = [{}, {"scope": "master", "date_from": "2026-05-01", "date_to": "2026-05-31"},
            {"date_from": "2026-06-01", "date_to": "2026-06-03"}]


@pytest.mark.parametrize("params", PT_CASES, ids=["default", "master_may", "june"])
def test_price_tier_report_is_pinned(tier_client, params):
    j = tier_client.get("/api/reports/price-tier", params=params).json()
    assert j.get("ready") is not False, j.get("hint")
    _pin("price_tier__" + ("_".join(f"{k}-{v}" for k, v in sorted(params.items())) or "default") + ".json", _json(j))
