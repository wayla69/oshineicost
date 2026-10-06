# -*- coding: utf-8 -*-
"""bom_center.engine — unit cost per SKU for a release. Pure SQLite, no FastAPI.

The precedence is the one core._build_sku_effective_cost applies to the legacy
tables, written down once as data (bc_recipe.kind) instead of as a COALESCE
over three tables:

  zero    buffet package codes (BUFFET*, B01 …): 0 — the dishes eaten are costed
  flat    a flat unit cost (sku_cost non-zero) wins over any recipe lines
  recipe  SUM over bc_recipe_line of (line_cost, else qty × price_per_unit)
  alias   one hop to another SKU's LINES (the target's flat cost is not used —
          exactly the legacy bom_sku_alias path, so v0 reproduces it)
  (none)  no row: 0, genuinely uncosted

For a BASELINE release the line_cost column carries the exact legacy figure.
Monthly releases (S1+) leave line_cost NULL and are priced at read time from
bc_ingredient_price for the release's effective month — that lookup lands with
M4; until then qty × price_per_unit is the fallback.
"""
from datetime import datetime, timezone

from .schema import ensure


def release_in_force(con, month: str):
    """release_id of the RELEASED release that governs `month` ('YYYY-MM'):
    the greatest effective_month <= month, newest release on a tie. None when
    nothing is released (the caller then stays on the legacy engine)."""
    ensure(con)
    row = con.execute(
        "SELECT release_id FROM bc_release WHERE status='released' AND effective_month <= ? "
        "ORDER BY effective_month DESC, release_id DESC LIMIT 1", (month,)).fetchone()
    return row[0] if row else None


def _line_sums(con, release_id: int) -> dict:
    return {
        r[0]: float(r[1] or 0.0)
        for r in con.execute(
            "SELECT sku_code, SUM(COALESCE(line_cost, qty * price_per_unit, 0)) "
            "FROM bc_recipe_line WHERE release_id=? GROUP BY sku_code", (release_id,))
    }


def unit_costs(con, release_id: int) -> dict:
    """{sku_code: (unit_cost, source)} for every SKU in `skus`. SKUs the release
    does not mention cost 0 with source None."""
    ensure(con)
    sums = _line_sums(con, release_id)
    recipes = {
        r[0]: (r[1], r[2], r[3], r[4])            # kind, flat_cost, alias_sku_code, source
        for r in con.execute(
            "SELECT sku_code, kind, flat_cost, alias_sku_code, source FROM bc_recipe WHERE release_id=?",
            (release_id,))
    }
    out = {}
    for (code,) in con.execute("SELECT sku_code FROM skus"):
        rec = recipes.get(code)
        if rec is None:
            out[code] = (0.0, None)
            continue
        kind, flat, alias_to, source = rec
        if kind == "zero":
            cost = 0.0
        elif kind == "flat":
            cost = float(flat or 0.0)
        elif kind == "recipe":
            cost = sums.get(code, 0.0)
        elif kind == "alias":
            cost = sums.get(alias_to or "", 0.0)
        else:                                      # unknown kind: never NULL, never crash
            cost = 0.0
        out[code] = (round(cost, 6), source or kind)
    return out


def rebuild_cost_cache(con, release_id: int) -> int:
    """(Re)build bcd_cost_cache for one release from bc_recipe/_line. Compute-
    then-replace, like the legacy builder: a failure never leaves a half table."""
    costs = unit_costs(con, release_id)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    rows = [(release_id, code, cost, src, now) for code, (cost, src) in costs.items()]
    con.execute("DELETE FROM bcd_cost_cache WHERE release_id=?", (release_id,))
    con.executemany(
        "INSERT INTO bcd_cost_cache(release_id, sku_code, unit_cost, source, built_at) VALUES(?,?,?,?,?)",
        rows)
    return len(rows)
