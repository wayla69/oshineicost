# -*- coding: utf-8 -*-
"""One discount policy ceiling (rules.discount_policy_pct, default 2.5% of
pre-discount sales; app_settings['discount_policy_pct'] overrides it) and the
baht given beyond it, reported the same way by the Discounts tab endpoint and
the management report headline. Also: the Excel ingest's net_sales rule now
matches the live backfill (service charge subtracted)."""
import re
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import rules  # noqa: E402


def _set_policy(client, value):
    if value is None:
        import core
        con = sqlite3.connect(str(core.DB_PATH))
        con.execute("DELETE FROM app_settings WHERE key='discount_policy_pct'")
        con.commit(); con.close()
        core.invalidate_cache("analysis_discounts", "report_management")
    else:
        assert client.put("/api/settings", json={"discount_policy_pct": str(value)}).status_code == 200


def test_policy_helpers():
    con = sqlite3.connect(":memory:")
    assert rules.discount_policy_pct(con) == 2.5                       # no table at all → default
    con.execute("CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT)")
    assert rules.discount_policy_pct(con) == 2.5
    con.execute("INSERT INTO app_settings VALUES ('discount_policy_pct', '1.75')")
    assert rules.discount_policy_pct(con) == 1.75
    con.execute("UPDATE app_settings SET value='abc'")
    assert rules.discount_policy_pct(con) == 2.5                       # unreadable → default
    con.execute("UPDATE app_settings SET value='250'")
    assert rules.discount_policy_pct(con) == 2.5                       # out of range → default
    assert rules.discount_excess(300, 10000, 2.5) == 50.0
    assert rules.discount_excess(200, 10000, 2.5) == 0.0               # never negative
    assert rules.discount_excess(None, None, 2.5) == 0.0


def test_discounts_endpoint_reports_excess_over_the_ceiling(client):
    _set_policy(client, None)
    try:
        d = client.get("/api/analysis/discounts").json()
        assert d["policy_pct"] == 2.5 and d["overall"]["policy_pct"] == 2.5
        rows = d["by_branch"]
        assert rows and {"gross_base", "excess_baht", "over_policy"} <= set(rows[0])
        for r in rows:                                                    # the ฿ beyond 2.5% of the pre-discount base
            assert r["excess_baht"] == rules.discount_excess(r["total_discount"], r["gross_base"], 2.5)
            assert r["over_policy"] == (r["disc_pct_of_sales"] > 2.5)
            assert r["gross_base"] > r["total_discount"] > 0
        assert d["overall"]["excess_baht"] == pytest.approx(sum(r["excess_baht"] for r in rows), abs=0.01)
        assert d["overall"]["branches_over_policy"] == sum(1 for r in rows if r["over_policy"])
        # a tighter ceiling from Settings: every branch is over it, and the cache dropped on save
        _set_policy(client, 1.0)
        d1 = client.get("/api/analysis/discounts").json()
        assert d1["policy_pct"] == 1.0
        for r in d1["by_branch"]:
            assert r["over_policy"] and r["excess_baht"] == pytest.approx(r["total_discount"] - 0.01 * r["gross_base"], abs=0.01)
        assert d1["overall"]["excess_baht"] > d["overall"]["excess_baht"]
        assert d1["overall"]["branches_over_policy"] == len(d1["by_branch"])
    finally:
        _set_policy(client, None)


def test_management_headline_uses_the_same_base_and_ceiling(client):
    _set_policy(client, None)
    try:
        h = client.get("/api/reports/management", params={"period": "monthly"}).json()["headline"]
        assert h["discount_policy_pct"] == 2.5
        base = h["revenue"] + h["discount"]
        assert h["discount_pct"] == round(h["discount"] / base * 100, 1)          # pre-discount base, like the tab
        assert h["discount_excess"] == rules.discount_excess(h["discount"], base, 2.5)
        _set_policy(client, 0.5)
        h2 = client.get("/api/reports/management", params={"period": "monthly"}).json()["headline"]
        assert h2["discount_policy_pct"] == 0.5 and h2["discount_excess"] > h["discount_excess"]
    finally:
        _set_policy(client, None)


def test_excel_ingest_net_sales_subtracts_service_charge():
    """The formula in ingest_easy.load_es_typeb (read from source, so a rewrite
    of the ingest cannot silently drop the service-charge term again)."""
    src = (HERE / "ingest_easy.py").read_text(encoding="utf-8")
    m = re.search(r"net_sales = sum\((.*)\)\n", src)
    assert m and "- (t[11] or 0)" in m.group(1), m.group(0) if m else "formula not found"
    src2 = (HERE / "reingest_raw_2026.py").read_text(encoding="utf-8")
    assert '- (t["svc"] or 0)' in src2
    # and it evaluates like rules.net_revenue: ex-VAT total minus the service charge
    t = (None,) * 11 + (50.0, 1070.0, 1000.0)          # t[11]=service 50, t[12]=inc 1070, t[13]=ex 1000
    assert sum((x[13] if x[13] else x[12] / 1.07) - (x[11] or 0) for x in [t]) == 950.0
