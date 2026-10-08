"""The warehouse role (คลัง): the person who updates what is actually sent (owner 2026-10-08).

It replaces Sales Support at the queue's "ดำเนินการแล้ว" step. The role holds ONE module and,
like the branch login, no sales.overview — it reaches only what a rule names."""
import auth

WH = auth.ROLE_MODULES["warehouse"]


def test_the_warehouse_role_exists_with_one_module():
    assert "warehouse" in auth.ROLES and "supply.warehouse" in auth.MODULES
    assert WH == {"supply.warehouse"}
    assert "supply.warehouse" in auth.ROLE_MODULES["management"]            # management holds everything
    for r in auth.ROLES:
        if r not in ("warehouse", "management"):
            assert "supply.warehouse" not in auth.ROLE_MODULES[r], r


def test_the_warehouse_updates_what_is_sent_and_nothing_else():
    assert auth.path_allowed("GET", "/api/bom-center/hub/queue", WH)
    assert auth.path_allowed("GET", "/api/bom-center/hub/order/1", WH)
    assert auth.path_allowed("POST", "/api/bom-center/hub/process/1", WH)
    assert auth.path_allowed("GET", "/api/bom-center/todo", WH)
    for method, path in (("POST", "/api/bom-center/order/save"), ("POST", "/api/bom-center/stock/count"),
                         ("POST", "/api/bom-center/central/import"), ("POST", "/api/bom-center/order-calendar"),
                         ("POST", "/api/bom-center/prices/import"), ("POST", "/api/bom-center/release-act/release"),
                         ("POST", "/api/bom-center/claim-act/decide"), ("POST", "/api/bom-center/claims"),
                         ("GET", "/api/bom-center/claims"), ("GET", "/api/bom-center/ingredients"),
                         ("GET", "/api/sales/monthly"), ("GET", "/api/cost/analysis")):
        assert not auth.path_allowed(method, path, WH), (method, path)


def test_sales_support_no_longer_processes_but_still_reads_and_exports():
    ss = auth.ROLE_MODULES["salessupport"]
    assert not auth.path_allowed("POST", "/api/bom-center/hub/process/1", ss)
    assert auth.path_allowed("GET", "/api/bom-center/hub/queue", ss)
    assert auth.path_allowed("GET", "/api/bom-center/hub/consolidated.xlsx", ss)
    ana = auth.ROLE_MODULES["analyst"]
    assert not auth.path_allowed("POST", "/api/bom-center/hub/process/1", ana)     # "instead of" — not also the analyst


def test_a_saved_matrix_from_before_the_role_still_works(client, app_db):
    """The live matrix predates the role and the module: the role starts at its default and management gains the module."""
    import json
    import sqlite3
    con = sqlite3.connect(app_db)
    con.row_factory = sqlite3.Row
    try:
        old = {r: sorted(auth.ROLE_MODULES[r] - {"supply.warehouse"}) for r in auth.ROLES if r != "warehouse"}
        con.execute("CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT)")
        con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES('role_permissions', ?)", (json.dumps(old),))
        con.commit()
        auth.refresh_perm_cache(con)
        assert auth.modules_for_role("warehouse") == {"supply.warehouse"}
        assert "supply.warehouse" in auth.modules_for_role("management")
        assert "supply.warehouse" not in auth.modules_for_role("salessupport")
    finally:
        con.execute("DELETE FROM app_settings WHERE key='role_permissions'")
        con.commit()
        auth.refresh_perm_cache(con)
        con.close()


def test_the_queue_todo_belongs_to_the_warehouse(client, app_db):
    import core
    from bom_center import todo as td
    from bom_center.schema import ensure
    with core.get_db() as con:
        ensure(con)
        items = {i["key"]: i for i in td.build(con)["items"]}
    q = items["queue_pending"]
    assert q["owner_modules"] == ["supply.warehouse"] and q["owner_th"] == "คลัง"
    assert items["claims_pending"]["owner_modules"] == ["supply.ordering"]
