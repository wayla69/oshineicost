"""The Grand Menu switch-over (2026-09-01) replaced the whole buffet price
list. Comparing a September period against its auto-selected "previous
window" would, for the first few weeks, reach back into August — a
different menu, not a baseline (and both eras even share the "999" label
for two different products). price_tier_report and _mgt_tier must both
refuse that comparison rather than show a delta that isn't the same dish.
See the conversation that added [[grand_menu_2026_09]]'s BoM-per-tier work.
"""
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import main  # noqa: E402
import conftest as _cf  # noqa: E402

# Old era (until 2026-08-31): tier '799'. New era (from 2026-09-01): tier
# '459' (unique to the new menu) and '999' (used by BOTH eras — the shared
# label that must never blend two different products across A/B).
OLD_ONLY_DAYS = [("2026-07-%02d" % d) for d in range(1, 32)]
OLD_ONLY_DAYS += [("2026-08-%02d" % d) for d in range(1, 32)]
NEW_DAYS = [("2026-09-%02d" % d) for d in range(1, 11)]


def _seed(db: Path):
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
    for day in OLD_ONLY_DAYS:
        con.execute("INSERT INTO menu_fact_total VALUES(1,?,?,?,?,?,?,0)",
                    (day, "799", 5, 10.0, 1800.0, 6320.0))
        con.execute("INSERT INTO menu_fact_total VALUES(1,?,?,?,?,?,?,0)",
                    (day, "999", 2, 4.0, 800.0, 3196.0))
    for day in NEW_DAYS:
        con.execute("INSERT INTO menu_fact_total VALUES(1,?,?,?,?,?,?,0)",
                    (day, "459", 6, 12.0, 458.0, 4908.0))
        # 999's food cost is deliberately over 40% (1600/3596 = 44.5%) so a
        # test can exercise the cross-era "flag the worst tier" action path.
        con.execute("INSERT INTO menu_fact_total VALUES(1,?,?,?,?,?,?,0)",
                    (day, "999", 2, 4.0, 1600.0, 3596.0))
    con.commit()
    con.close()


@pytest.fixture(scope="module")
def era_client(tmp_path_factory):
    db = tmp_path_factory.mktemp("era") / "test.db"
    _cf._build_db(str(db))
    _seed(db)
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


def test_september_vs_auto_previous_august_disables_comparison(era_client):
    """A entirely within the new menu, auto B lands entirely in the old one."""
    j = era_client.get("/api/reports/price-tier",
                       params={"date_from": "2026-09-01", "date_to": "2026-09-10"}).json()
    assert j["ready"] and j.get("comparison_available") is False
    assert j["mode"] == "prev"
    assert j["b_range"][1] < "2026-09-01", "the auto previous window must be the one that reached into August"
    # only tiers active THIS period show up — '799' (old-only, zero pax in A) must not appear
    tier_keys = set(j["tier_keys"])
    assert tier_keys == {"459", "999"}
    for row in j["tiers"]:
        assert row["b"] is None, f"tier {row['tier']}: comparison must be suppressed, not just zeroed"
        assert row["d_pax_pct"] is None and row["d_cogs_head"] is None
    assert j["blend"]["pax_b"] is None and j["blend"]["d_pax_pct"] is None
    assert j["mix_effect"] is None
    # no comparison is possible yet, but the action card is never left empty:
    # tier 999 clears the 40% food-cost bar on THIS period's numbers alone
    # (no "b" data needed), so that real finding replaces the generic
    # "bank this month as a baseline" fallback.
    assert len(j["actions"]) == 1
    assert "999" in j["actions"][0]["title"]
    row999 = next(r for r in j["tiers"] if r["tier"] == "999")
    assert row999["a"]["food_pct"] > 40   # sanity: the fixture really does clear the bar
    assert len(j["insights"]) == 1 and "1 ก.ย." in j["insights"][0] or "2026-09-01" in j["insights"][0]


def test_window_straddling_the_switchover_is_cut_to_the_live_menu(era_client):
    """The DEFAULT view: the pickers open on the whole coverage, which reaches
    back over the changeover. Blending the two price lists put retired tiers in
    the table, gave the Top-N a column per dead tier and let the action card
    read the old menu as a finding about the live one — so A is cut at the
    changeover and only the live menu is analysed."""
    j = era_client.get("/api/reports/price-tier",
                       params={"date_from": "2026-07-01", "date_to": "2026-09-10"}).json()
    assert j["ready"]
    assert j["era_clamped"] is True
    assert j["a_range"] == ["2026-09-01", "2026-09-10"]
    # the retired price list is gone: from the tier table, the Top-N columns…
    assert set(j["tier_keys"]) == {"459", "999"}
    assert "799" not in {r["tier"] for r in j["tiers"]}
    for row in j["top30"]:
        assert "799" not in row["tiers"]
    # …and from the monthly trend behind the mix / cost charts
    assert all(r["ym"] >= "2026-09" for r in j["trend_month"])
    # B now lands entirely in the old era, so the comparison stays suppressed
    # until October has September to compare against.
    assert j["comparison_available"] is False


def test_both_periods_pre_switchover_still_compare(era_client):
    """Old era vs older-still old era — no menu change in between, comparison stays on."""
    j = era_client.get("/api/reports/price-tier",
                       params={"date_from": "2026-08-15", "date_to": "2026-08-21"}).json()
    assert j["ready"] and j.get("comparison_available") is True
    assert j["b_range"][1] < "2026-08-15"
    row = next(r for r in j["tiers"] if r["tier"] == "799")
    assert row["b"] is not None
    assert j["blend"]["pax_b"] is not None


def test_both_periods_post_switchover_compare_once_history_exists(era_client):
    """Once both windows sit on the new-menu side, comparison resumes automatically —
    the exact 'October vs September' case, reached here with two windows inside
    the 10 days of September fixture data."""
    j = era_client.get("/api/reports/price-tier",
                       params={"date_from": "2026-09-06", "date_to": "2026-09-10"}).json()
    assert j["ready"] and j.get("comparison_available") is True
    assert j["b_range"][0] >= "2026-09-01"
    row = next(r for r in j["tiers"] if r["tier"] == "459")
    assert row["b"] is not None


# ─── same guard, Management Report §11 (_mgt_tier) ──────────────────────────

def _mgt_db():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE branches(branch_id INTEGER PRIMARY KEY, branch_type TEXT)")
    con.execute("INSERT INTO branches VALUES(1, 'master')")
    con.execute("""CREATE TABLE menu_fact_total(branch_id INT, day TEXT, tier TEXT,
                   bills INT, pax REAL, cogs REAL, rev REAL)""")
    for day in OLD_ONLY_DAYS[-10:]:                       # a slice of August
        con.execute("INSERT INTO menu_fact_total VALUES(1,?,?,?,?,?,?)",
                    (day, "799", 5, 10.0, 1800.0, 6320.0))
    for day in NEW_DAYS:                                  # 2026-09-01..10
        con.execute("INSERT INTO menu_fact_total VALUES(1,?,?,?,?,?,?)",
                    (day, "459", 6, 12.0, 458.0, 4908.0))
    return con


def _pct(c, p):
    return round((c - p) / p * 100, 1) if p else None


def test_mgt_tier_drops_comparison_across_the_switchover():
    import datetime as dt
    from routers.reports import _mgt_tier
    con = _mgt_db()
    out = _mgt_tier(con, cs=dt.date(2026, 9, 1), ce=dt.date(2026, 9, 10),
                    ps=dt.date(2026, 8, 22), pe=dt.date(2026, 8, 31),
                    has_prev=True, pct=_pct, sp=(), sw="1=1", th=True)["tier_out"]
    assert out is not None
    row = next(r for r in out["rows"] if r["tier"] == "459")
    assert row["pax_prev"] is None and row["d_pax_pct"] is None
    assert out["mix_effect"] is None
    assert out["pax_prev"] is None


def test_mgt_tier_keeps_comparison_within_one_era():
    import datetime as dt
    from routers.reports import _mgt_tier
    con = _mgt_db()
    out = _mgt_tier(con, cs=dt.date(2026, 8, 25), ce=dt.date(2026, 8, 31),
                    ps=dt.date(2026, 8, 15), pe=dt.date(2026, 8, 21),
                    has_prev=True, pct=_pct, sp=(), sw="1=1", th=True)["tier_out"]
    assert out is not None
    row = next(r for r in out["rows"] if r["tier"] == "799")
    assert row["pax_prev"] is not None
