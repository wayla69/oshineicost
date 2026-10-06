# -*- coding: utf-8 -*-
"""bom_center — the BoM Calculation Center (docs/BOM_CENTER_PLAN.md).

One place that answers "what does this dish cost, from which recipe, at which
month's prices" for every other part of the app. Sprint S0 lays the foundation:

  schema       the bc_* (site-owned, preserved) and bcd_* (derived) tables
  engine       unit cost per SKU for a release — the precedence rules in code
  snapshot_v0  Release v0: today's engine frozen into bc_*, with a parity check

Nothing here is wired into COGS yet: the live engine still reads
sku_effective_cost. The cut-over (S4) switches that table's source to the
release in force once parity has been proven — see docs/ADR-001.
"""
PREFIX_OWNED = "bc"      # bc_*  typed/imported on the site -> core._PRESERVED_PREFIXES
PREFIX_DERIVED = "bcd"   # bcd_* rebuilt from bc_* + sales   -> never carried forward
