# -*- coding: utf-8 -*-
"""Buffet-ONLY menu-engineering / cost-reduction analysis for the cost report.
Replicates the app's unified COGS model (sku_effective_cost.recipe_cost,
buffet tier codes zeroed) over the Jan-May 2026 window. Buffet 100%:
- buffet revenue = tier package revenue (BUFFET350/500/590/799/CHILD rows)
- buffet COGS    = cost of items CONSUMED during buffet (qty>0, ~no separate revenue)
- excludes a-la-carte (drinks, service charge, delivery, take-away).
Dumps JSON to stdout.
"""
import sqlite3, json

DB = "backend/oshinei.db"
WIN = ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05")
TIER = ('BUFFET','BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD')
WINQ = ",".join("?" for _ in WIN)
TIERQ = ",".join("?" for _ in TIER)
# categories that are NOT buffet food (a-la-carte / non-food) -> excluded from buffet view
NON_BUFFET_CATS = {
    "Oshinei SOFT DRINK","Oshinei ALCOHOL","Oshinei SERVICE","Service","Service Charge",
    "Oshinei DELIVERY","Special DELIVERY","Take Away","Special SOFT DRINK",
    "Non-product (penalty/voucher)","Oshinei DRINKING",
}

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

out = {"window": list(WIN)}

# ── 0. Headline: buffet package revenue (tier), blended revenue, total non-tier COGS ──
row = cur.execute(f"""
  SELECT
    ROUND(SUM(CASE WHEN sk.sku_code IN ({TIERQ}) THEN ssm.subtotal_ex_vat ELSE 0 END)) tier_rev,
    ROUND(SUM(ssm.subtotal_ex_vat)) total_rev,
    ROUND(SUM(ssm.qty * sec.recipe_cost
              * (CASE WHEN sk.sku_code IN ({TIERQ}) THEN 0 ELSE 1 END))) total_cogs
  FROM sales_sku_monthly ssm
  JOIN skus sk ON sk.sku_id = ssm.sku_id
  LEFT JOIN sku_effective_cost sec ON sec.sku_id = ssm.sku_id
  WHERE ssm.source='Easy' AND ssm.period_month IN ({WINQ})
""", (*TIER, *TIER, *WIN)).fetchone()
out["headline"] = dict(row)

row = cur.execute(f"""
  SELECT ROUND(SUM(customer_count)) covers, COUNT(*) bills
  FROM sales_transaction
  WHERE source='Easy' AND strftime('%Y-%m', sold_at) IN ({WINQ})
""", WIN).fetchone()
out["covers"] = dict(row)

# ── 1. Item-level (non-tier, qty>0) ─────────────────────────────────────────
rows = cur.execute(f"""
  SELECT sk.sku_code, sk.description, c.name AS category,
         ROUND(SUM(ssm.qty)) qty,
         MAX(sec.recipe_cost) unit_cost,
         ROUND(SUM(ssm.qty * sec.recipe_cost)) total_cost,
         ROUND(SUM(ssm.subtotal_ex_vat)) rev,
         COUNT(DISTINCT ssm.branch_id) branches
  FROM sales_sku_monthly ssm
  JOIN skus sk ON sk.sku_id = ssm.sku_id
  LEFT JOIN categories c ON c.category_id = sk.category_id
  LEFT JOIN sku_effective_cost sec ON sec.sku_id = ssm.sku_id
  WHERE ssm.source='Easy' AND ssm.period_month IN ({WINQ})
    AND sk.sku_code NOT IN ({TIERQ})
  GROUP BY ssm.sku_id
  HAVING qty > 0
""", (*WIN, *TIER)).fetchall()
items = [dict(r) for r in rows]
for it in items:
    it["unit_cost"] = round(it["unit_cost"] or 0, 2)
    it["total_cost"] = it["total_cost"] or 0
    it["rev"] = it["rev"] or 0
    # buffet-consumed = served but ~no separate revenue, AND not a non-buffet category
    it["is_buffet"] = (it["rev"] <= (it["qty"] * 0.5)) and ((it["category"] or "") not in NON_BUFFET_CATS)

# BUFFET universe only
buf_items = [it for it in items if it["is_buffet"]]
buffet_cogs = sum(it["total_cost"] for it in buf_items)
out["buffet_cogs"] = round(buffet_cogs)
out["buffet_rev"] = out["headline"]["tier_rev"]
out["buffet_food_cost_pct"] = round(100 * buffet_cogs / out["headline"]["tier_rev"], 1)
out["blended_food_cost_pct"] = round(100 * out["headline"]["total_cogs"] / out["headline"]["total_rev"], 1)

tot_cost = buffet_cogs or 1
for it in buf_items:
    it["cost_share_pct"] = round(100 * it["total_cost"] / tot_cost, 2)

out["item_total_cost"] = round(buffet_cogs)
out["top_cost_drivers"] = sorted(buf_items, key=lambda x: -x["total_cost"])[:60]

# ── 2. Quadrants (buffet items, qty>=200) ───────────────────────────────────
buf = [it for it in buf_items if it["qty"] >= 200]
def median(xs):
    xs = sorted(xs); n = len(xs)
    return xs[n//2] if n % 2 else (xs[n//2-1] + xs[n//2]) / 2
med_qty = median([it["qty"] for it in buf]) if buf else 0
med_cost = median([it["unit_cost"] for it in buf if it["unit_cost"] > 0]) if buf else 0
out["buffet_medians"] = {"qty": med_qty, "unit_cost": round(med_cost, 2)}
for it in buf:
    hi_pop = it["qty"] >= med_qty
    hi_cost = it["unit_cost"] >= med_cost
    it["quadrant"] = ("STAR_keep" if hi_pop and not hi_cost else
                      "TRANSFORM" if hi_pop and hi_cost else
                      "PUZZLE" if not hi_pop and hi_cost else "DOG")
out["buffet_items"] = sorted(buf, key=lambda x: -x["total_cost"])
out["high_pop_low_cost"] = sorted([it for it in buf if it["quadrant"]=="STAR_keep"], key=lambda x:-x["qty"])[:40]
out["high_pop_high_cost"] = sorted([it for it in buf if it["quadrant"]=="TRANSFORM"], key=lambda x:-x["total_cost"])[:40]

# ── 3. Category breakdown (buffet food only) ────────────────────────────────
cat = {}
for it in buf_items:
    k = it["category"] or "?"
    d = cat.setdefault(k, {"category": k, "qty": 0, "total_cost": 0, "n": 0})
    d["qty"] += it["qty"]; d["total_cost"] += it["total_cost"]; d["n"] += 1
cats = sorted(cat.values(), key=lambda x: -x["total_cost"])
for d in cats:
    d["cost_share_pct"] = round(100 * d["total_cost"] / tot_cost, 2)
out["categories"] = cats

# ── 4. Per-branch BUFFET food cost = branch buffet COGS / branch tier revenue ──
# branch tier revenue
trev = {r["branch_id"]: r["v"] for r in cur.execute(f"""
  SELECT ssm.branch_id, ROUND(SUM(ssm.subtotal_ex_vat)) v
  FROM sales_sku_monthly ssm JOIN skus sk ON sk.sku_id=ssm.sku_id
  WHERE ssm.source='Easy' AND ssm.period_month IN ({WINQ}) AND sk.sku_code IN ({TIERQ})
  GROUP BY ssm.branch_id""", (*WIN, *TIER)).fetchall()}
# branch non-tier COGS (buffet consumption)
bcogs = {r["branch_id"]: r["v"] for r in cur.execute(f"""
  SELECT ssm.branch_id,
    ROUND(SUM(ssm.qty * sec.recipe_cost)) v
  FROM sales_sku_monthly ssm JOIN skus sk ON sk.sku_id=ssm.sku_id
  LEFT JOIN sku_effective_cost sec ON sec.sku_id=ssm.sku_id
  WHERE ssm.source='Easy' AND ssm.period_month IN ({WINQ}) AND sk.sku_code NOT IN ({TIERQ})
  GROUP BY ssm.branch_id""", (*WIN, *TIER)).fetchall()}
bmeta = {r["branch_id"]: (r["name"], r["branch_type"]) for r in cur.execute("SELECT branch_id,name,branch_type FROM branches").fetchall()}
br = []
for bid, rv in trev.items():
    if not rv: continue
    cg = bcogs.get(bid, 0) or 0
    nm, bt = bmeta.get(bid, ("?", "?"))
    br.append({"branch": nm, "branch_type": bt, "rev": rv, "cogs": cg,
               "food_cost_pct": round(100*cg/rv, 1)})
out["branches"] = sorted(br, key=lambda x: -x["food_cost_pct"])

# convenience: salmon / shrimp / durian family share of buffet COGS
def fam(kw):
    t = sum(it["total_cost"] for it in buf_items if any(k in it["description"] for k in kw))
    q = sum(it["qty"] for it in buf_items if any(k in it["description"] for k in kw))
    return {"cost": round(t), "qty": round(q), "share": round(100*t/tot_cost, 1)}
out["families"] = {
    "salmon": fam(["แซลมอน", "แซลมอล"]),
    "shrimp": fam(["กุ้ง"]),
    "durian": fam(["ทุเรียน"]),
    "tuna": fam(["ทูน่า"]),
}

print(json.dumps(out, ensure_ascii=False))
