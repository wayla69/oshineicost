"""rules.py — the shared business rules, pinned as text and as behaviour.

Text: the helpers must keep producing the exact SQL that the hand-copied
fragments used to, because golden compares endpoint OUTPUT and would not notice
if a helper quietly drifted to a different-but-plausible formula while every
caller still agreed with itself.

Behaviour: run each rule on a tiny in-memory database where the right answer
is known by hand.
"""
import sqlite3

import pytest

import rules


# ─── text ────────────────────────────────────────────────────────────────────

def test_vat_constants():
    assert rules.VAT_RATE == 0.07
    assert rules.VAT_DIVISOR == 1.07
    assert rules.ex_vat(107.0) == pytest.approx(100.0)
    assert rules.ex_vat(None) == 0.0


def test_net_revenue_text_is_the_established_formula():
    assert rules.net_revenue_row("st") == "(st.total_inc_vat/1.07 - COALESCE(st.service_charge,0))"
    assert rules.net_revenue_row("") == "(total_inc_vat/1.07 - COALESCE(service_charge,0))"
    assert rules.net_revenue_sum("t") == "(SUM(t.total_inc_vat)/1.07 - SUM(COALESCE(t.service_charge,0)))"


def test_tier_code_sql_is_derived_from_the_tuples():
    for c in rules.EASY_TIER_CODES:
        assert f"'{c}'" in rules.EASY_TIER_CODES_SQL and f"'{c}'" in rules.ALL_TIER_CODES_SQL
    for c in rules.LEGACY_TIER_CODES:
        assert f"'{c}'" in rules.ALL_TIER_CODES_SQL and f"'{c}'" not in rules.EASY_TIER_CODES_SQL
    assert rules.EASY_TIER_CODES_SQL == "('BUFFET','BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD')"


def test_cogs_expr_zeroes_easy_tier_rows_and_multiplies_qty():
    t = rules.cogs_expr("ssm")
    assert t.startswith("CASE ")
    assert "ssm.source = 'Easy'" in t and rules.EASY_TIER_CODES_SQL in t
    assert "ssm.qty * COALESCE(" in t
    assert "sku_effective_cost" in t and "bom_sku_alias" in t


# ─── behaviour ───────────────────────────────────────────────────────────────

def _db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE skus(sku_id INTEGER PRIMARY KEY, sku_code TEXT);
        CREATE TABLE sku_cost(sku_id INTEGER PRIMARY KEY, unit_cost REAL);
        CREATE TABLE bom_ingredients(sku_id INT, ingredient_cost REAL);
        CREATE TABLE bom_sku_alias(bom_sku_code TEXT, resolved_sku_id INT);
        CREATE TABLE sku_effective_cost(sku_id INTEGER PRIMARY KEY, recipe_cost REAL);
        CREATE TABLE sales(sku_id INT, qty REAL, source TEXT, total_inc_vat REAL, service_charge REAL);
        INSERT INTO skus VALUES (1,'BUFFET799'), (2,'B0001'), (3,'A0001'), (4,'NEW001'), (5,'X0009');
        INSERT INTO sku_cost VALUES (2, 13.4), (3, 0);          -- 0 means "no data", not free
        INSERT INTO bom_ingredients VALUES (3, 20.0), (3, 5.0); -- A0001 recipe = 25
        INSERT INTO bom_sku_alias VALUES ('NEW001', 3);         -- NEW001 borrows A0001's recipe
        INSERT INTO sales VALUES (1, 2, 'Easy', 1598.0, 0),     -- the tier line: revenue, no cost
                                 (2, 3, 'Easy', 0, 0),          -- 3 plates @ 13.40
                                 (3, 1, 'Easy', 214.0, 10.0),   -- 1 plate @ recipe 25
                                 (4, 2, 'Easy', 0, 0),          -- 2 plates via alias @ 25
                                 (5, 4, 'Easy', 0, 0);          -- uncosted → 0, never NULL
    """)
    return con


def test_cogs_expr_resolves_every_step_of_the_chain():
    con = _db()
    got = {r["sku_id"]: r["c"] for r in con.execute(
        f"SELECT sku_id, {rules.cogs_expr('ssm')} AS c FROM sales ssm")}
    assert got == {1: 0, 2: pytest.approx(40.2), 3: 25.0, 4: 50.0, 5: 0}


def test_precomputed_effective_cost_wins_when_present():
    con = _db()
    con.execute("INSERT INTO sku_effective_cost VALUES (2, 99.0)")
    c = con.execute(f"SELECT {rules.cogs_expr('ssm')} FROM sales ssm WHERE sku_id=2").fetchone()[0]
    assert c == pytest.approx(3 * 99.0)


def test_net_revenue_on_a_bill():
    con = _db()
    r = con.execute(f"SELECT {rules.net_revenue_row('ssm')} AS rev, {rules.net_revenue_sum('ssm')} AS tot "
                    f"FROM sales ssm WHERE sku_id=3").fetchone()
    assert r["rev"] == pytest.approx(214.0 / 1.07 - 10.0)
    assert r["tot"] == pytest.approx(214.0 / 1.07 - 10.0)


def test_scope_filter_binds_what_it_appends():
    f, p = ["b.name != 'ALL'"], {}
    rules.scope_filter(f, p, branch="BranchA", group="franchise")
    assert f == ["b.name != 'ALL'", "b.name=:branch", "COALESCE(b.branch_type,'master') = :branch_group"]
    assert p == {"branch": "BranchA", "branch_group": "franchise"}
    f, p = [], {}
    rules.scope_filter(f, p)                       # nothing asked → nothing appended
    assert f == [] and p == {}
    f, p = [], {}
    rules.scope_filter(f, p, branch="X", alias="br")
    assert f == ["br.name=:branch"]
