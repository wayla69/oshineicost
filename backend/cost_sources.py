# -*- coding: utf-8 -*-
"""cost_sources.py — every file-borne cost source, applied in ONE place.

The COGS engine (core._build_sku_effective_cost) is a pure function of four
tables: skus / sku_cost / bom_ingredients / bom_sku_alias. What feeds those
tables, besides the owner's uploads, is a set of files that ship with the code
— and until 2026-09-18 they were applied only by the app at startup, against
the LIVE database. The menu_fact_* tables are built by the sync on another
machine against a STAGING database the app never touches, so every new source
needed a hand-written "overlay" in build_menu_fact.py to be seen there (#186
estimates, #188 recipes, #189 aliases) — three copies of the precedence rules
waiting to drift.

Now both sides call apply_all(con): the app at startup, the fact builder before
it reads a single cost. Same code, same order, same tables — and the staged
database arrives on Fly already carrying the costs the app would have written.

Order is precedence, weakest last, and it is deliberate:
  1. Grand Menu master sheet   -> sku_cost          (owner's numbers)
  2. recipes file              -> bom_ingredients   (before aliases: a donor
                                                     may live in this file)
  3. aliases                   -> bom_sku_alias     (one hop to a donor recipe)
  4. hand-verified patches     -> sku_cost          (a patch exists because a
                                                     bulk source was wrong)
  5. estimates                 -> sku_cost          (fills ฿0 only, never
                                                     overwrites anything)
then the derived sku_effective_cost table is rebuilt from the result.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


@contextmanager
def _use(con):
    """The loaders were written around `with get_db() as con:`; they now receive
    the connection instead, and the caller owns its lifetime."""
    yield con


def _is_price_as_cost(unit_cost, ex_vat_price):
    # core pulls in FastAPI; import it only when a row actually needs judging
    from core import is_price_as_cost
    return is_price_as_cost(unit_cost, ex_vat_price)


PROVENANCE_DDL = (
    "CREATE TABLE IF NOT EXISTS cost_provenance("
    "sku_id INTEGER PRIMARY KEY, source TEXT NOT NULL, basis TEXT, value REAL,"
    " note TEXT, applied_at TEXT)")


def _tag(con, sku_id, source, basis, value, note, now):
    """Record that WE put this number there, and what it was.

    Without this a loader cannot tell its own earlier estimate from a cost the
    owner typed in: "skip anything already costed" then freezes the first value
    it ever wrote, and every later edit to the file is silently ignored on any
    database that has seen the old one (caught 2026-09-18, the day apply_all
    started writing into the sync's base DB). The rule this enables: a cost is
    still ours only while it equals what we last wrote — the moment anyone
    changes it, it is theirs, and we never touch it again.
    It is also the provenance the UI shows: confirmed / recipe / estimate."""
    con.execute(
        "INSERT OR REPLACE INTO cost_provenance(sku_id, source, basis, value, note, applied_at)"
        " VALUES(?, ?, ?, ?, ?, ?)", (sku_id, source, basis, value, note, now))


def _ours(con, sku_id, source, current):
    """Is the cost on this SKU the one THIS source last wrote, untouched since?"""
    r = con.execute("SELECT source, value FROM cost_provenance WHERE sku_id=?", (sku_id,)).fetchone()
    return bool(r and r[0] == source and current is not None
                and abs((r[1] or 0) - (current or 0)) < 0.005)


GRAND_MENU_COST_FILE = HERE / "data" / "grand_menu_costs_2026-09.csv"


def load_grand_menu_costs(con, path=None):
    """Upsert the Grand Menu master costs into sku_cost, and each buffet dish's
    price tier into sku_tier (à la carte rows carry no tier). Returns a summary dict."""
    import csv as _csv
    path = Path(path) if path else GRAND_MENU_COST_FILE
    out = {"applied": 0, "unknown_sku": 0, "rejected_price_as_cost": 0, "bad_value": 0}
    if not path.exists():
        print(f"[grand-menu] cost file not found: {path} — skipped")
        return out
    try:
        rows = []
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for rec in _csv.DictReader(fh):
                code = (rec.get("sku_code") or "").strip()
                if not code:
                    continue
                try:
                    cost = float(rec.get("cost_per_serve") or 0)
                except (TypeError, ValueError):
                    out["bad_value"] += 1
                    continue
                if cost <= 0:
                    out["bad_value"] += 1
                    continue
                # à la carte rows carry the literal text "None" in this column
                # (Master Item Grand Menu.xlsx), not a blank — see grand-menu-2026-09.
                tier = (rec.get("tier") or "").strip()
                if tier.lower() == "none":
                    tier = ""
                rows.append((code, cost, tier))

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        with _use(con) as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS sku_tier("
                "sku_id INTEGER PRIMARY KEY REFERENCES skus(sku_id), tier TEXT NOT NULL)"
            )
            ids = {r[1]: r[0] for r in con.execute(
                "SELECT sku_id, sku_code FROM skus WHERE sku_code IN (%s)"
                % ",".join("?" * len(rows)), [c for c, _, _ in rows])} if rows else {}
            # A selling price pasted into a cost column has reached this project
            # three separate times. Where the SKU has actually sold we can tell
            # the difference, so we check — and refuse the row rather than book
            # a dish at its own menu price.
            price = {}
            if ids:
                for r in con.execute(
                    "SELECT sku_id, SUM(subtotal_ex_vat) rev, SUM(qty) qty "
                    "FROM sales_sku_monthly WHERE sku_id IN (%s) GROUP BY 1"
                    % ",".join("?" * len(ids)), list(ids.values())
                ):
                    # A handful of promo sales must not veto the owner's number: on
                    # 2026-09-04 three discounted hand rolls made ฿53 look like a
                    # price. Judge only once the dish has really sold.
                    if (r[2] or 0) >= 10 and (r[1] or 0) > 0:
                        price[r[0]] = r[1] / r[2]

            for code, cost, tier in rows:
                sku_id = ids.get(code)
                if sku_id is None:
                    out["unknown_sku"] += 1
                    continue
                if _is_price_as_cost(cost, price.get(sku_id)):
                    print(f"[grand-menu] {code}: {cost} looks like the selling price — rejected")
                    out["rejected_price_as_cost"] += 1
                    continue
                con.execute(
                    "INSERT OR REPLACE INTO sku_cost(sku_id, unit_cost, effective_from, updated_at)"
                    " VALUES(?, ?, ?, ?)",
                    (sku_id, cost, "2026-09-01", now),
                )
                if tier:
                    con.execute(
                        "INSERT OR REPLACE INTO sku_tier(sku_id, tier) VALUES(?, ?)",
                        (sku_id, tier),
                    )
                out["applied"] += 1
            con.commit()
        print(f"[grand-menu] master costs: {out}")
    except Exception as e:
        print(f"[grand-menu] load failed: {e}")
    return out


# Some Grand Menu codes are a renamed dish that already has a recipe under its old
# code. They are costed by pointing at that donor rather than by re-entering a
# number, so the dish inherits the recipe itself — which is what lets the
# ingredient order plan see it at all, something a flat cost cannot do.
#
# bom_sku_alias resolves in ONE hop: _build_sku_effective_cost sums the donor's
# bom_ingredients rows directly. It does NOT chain and it does NOT read the
# donor's sku_cost, so pointing at a donor that is itself aliased, or at one
# priced by a flat cost, silently yields ฿0 — the failure this loader exists to
# make impossible. Two of these needed the chain resolved by hand (the soup
# stocks point past THBFSOU000xx to B0097/B0098) and one donor has no recipe at
# all, so its cost is COPIED instead. The copy is refreshed on every boot, so it
# tracks the donor rather than freezing a number into the database.
GRAND_MENU_ALIAS_FILE = HERE / "data" / "grand_menu_aliases_2026-09.csv"


def load_grand_menu_aliases(con, path=None):
    """Point Grand Menu codes at the donor that carries their recipe."""
    import csv as _csv
    path = Path(path) if path else GRAND_MENU_ALIAS_FILE
    out = {"aliased": 0, "cost_copied": 0, "unknown_sku": 0,
           "unknown_donor": 0, "donor_has_nothing": 0}
    if not path.exists():
        print(f"[grand-menu] alias file not found: {path} — skipped")
        return out
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            pairs = [((r.get("sku_code") or "").strip(),
                      (r.get("donor_sku_code") or "").strip())
                     for r in _csv.DictReader(fh)]
        pairs = [p for p in pairs if p[0] and p[1]]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        with _use(con) as con:
            for code, donor in pairs:
                own = con.execute("SELECT sku_id FROM skus WHERE sku_code = ?", (code,)).fetchone()
                if own is None:
                    out["unknown_sku"] += 1
                    continue
                d = con.execute("SELECT sku_id FROM skus WHERE sku_code = ?", (donor,)).fetchone()
                if d is None:
                    print(f"[grand-menu] alias {code}: donor {donor} not found — skipped")
                    out["unknown_donor"] += 1
                    continue
                donor_id = d[0]
                bom = con.execute(
                    "SELECT SUM(ingredient_cost) FROM bom_ingredients WHERE sku_id = ?",
                    (donor_id,)).fetchone()[0]
                if bom:
                    con.execute(
                        "INSERT OR REPLACE INTO bom_sku_alias"
                        "(bom_sku_code, resolved_sku_id, match_method, confidence, notes)"
                        " VALUES(?, ?, 'grand-menu-2026-09', 1.0, ?)",
                        (code, donor_id, f"Grand Menu 2026-09: same dish as {donor}"))
                    out["aliased"] += 1
                    continue
                # No recipe to inherit. Fall back to the donor's effective cost so
                # the dish is at least costed — an alias here would book it at ฿0.
                eff = con.execute(
                    "SELECT recipe_cost FROM sku_effective_cost WHERE sku_id = ?",
                    (donor_id,)).fetchone()
                eff = (eff[0] if eff else 0) or 0
                if eff > 0:
                    con.execute(
                        "INSERT OR REPLACE INTO sku_cost(sku_id, unit_cost, effective_from, updated_at)"
                        " VALUES(?, ?, ?, ?)", (own[0], eff, "2026-09-01", now))
                    out["cost_copied"] += 1
                else:
                    print(f"[grand-menu] alias {code}: donor {donor} has neither recipe nor cost")
                    out["donor_has_nothing"] += 1
            con.commit()
        print(f"[grand-menu] aliases: {out}")
    except Exception as e:
        print(f"[grand-menu] alias load failed: {e}")
    return out


# Manual cost corrections — applied at every startup after BOM seed.
# Use when the BOM workbook has a verified-wrong ingredient cost and cannot
# be corrected immediately. Each entry removes the SKU's BOM rows (so the
# COALESCE falls through to sku_cost) and upserts the confirmed unit cost.
COST_PATCHES: list[tuple[str, float]] = [
    ("B0040",    13.40),  # หอยเชลล์ต้มฮอกไกโด (1 ชิ้น)  — BOM workbook had ~38.2 (wrong)
    ("B0322",    13.40),  # หอยเชลล์ฮอกไกโดซาชิมิ (1 ตัว) — same ingredient, same correction
    ("1150-084", 13.40),  # HOKKAIDO HOTATE SASHIMI (1PCS) — same ingredient, same correction
    # Durian: the requisition audit priced it at ฿68/serve, but the correction was
    # applied to a database and then lost — sync copies the cost tables out of the
    # LOCAL base DB into .incoming, which is hot-swapped over the live one, so a DB-only
    # fix survives exactly until the next swap. It was found back at ฿20/฿30/฿40 on
    # 2026-09-02, understating COGS by ฿36.7M across the history. Listed here so it
    # re-asserts itself on every startup no matter which DB is swapped in.
    # The BoM is NOT deleted: EST-DURIAN already totals ฿68, so the guard above leaves
    # the recipe alone and the ingredient order plan keeps seeing durian.
    ("KKN018",        68.00),  # (B) ทุเรียน (1 พู)
    ("KKN019",        68.00),  # (B) ทุเรียน (1 เม็ด)
    ("DUR-999",       68.00),  # ทุเรียน (B)(Buffet)
    ("PM0010",        68.00),  # (B) ทุเรียนยำน้ำปลาร้า
    ("PM0011",        68.00),  # (B) เทมปุระทุเรียนมัทชะ (5 ชิ้น)
    ("PM0012",        68.00),  # (B) ทุเรียนไม่อั้น
    ("OTHBFPMO00013", 68.00),  # (BF) ทุเรียน — Grand Menu 2026-09 code for the same dish
    # เนื้อวากิวสไลด์ย่างใบโฮบะ. UBN002 was the only SKU in this dish family carrying
    # any cost at all, and it carried ฿409.25 — 93.9% of its own ฿435.94 ex-VAT
    # selling price. That is a price sitting in a cost column, not a cost. It slipped
    # past is_price_as_cost because that guard only spots the 1.03-1.12 band, i.e. an
    # INC-VAT price pasted in; a number just under the ex-VAT price is equally
    # impossible for food and equally invisible to it.
    # The Grand Menu code was inheriting that same figure through the alias file, so
    # the alias row is gone and both codes are pinned here at the owner-confirmed ฿16.
    ("UBN002",        16.00),  # (A) เนื้อวากิวสไลด์ย่างใบโฮบะ — retired code, 32 units ever
    ("OTHBFSPC00045", 16.00),  # (BF) เนื้อวากิวสไลด์ย่างใบโฮบะ — Grand Menu 2026-09
]


def apply_cost_patches(con):
    """Delete BOM rows for patched SKUs and upsert the verified sku_cost."""
    if not COST_PATCHES:
        return
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        with _use(con) as con:
            for sku_code, unit_cost in COST_PATCHES:
                row = con.execute(
                    "SELECT sku_id FROM skus WHERE sku_code = ?", (sku_code,)
                ).fetchone()
                if row is None:
                    print(f"[cost-patch] SKU not found: {sku_code} — skipped")
                    continue
                sku_id = row[0]
                # Only drop the BOM when it DISAGREES with the verified cost.
                # The patch exists because these SKUs once carried a BOM saying
                # ~฿38.2 against a true ฿13.40 — not because a recipe is unwanted.
                # A recipe that already totals the verified cost is harmless to
                # COGS (sku_cost still wins in _build_sku_effective_cost) and is
                # what lets the ingredient order plan see the dish at all, so
                # deleting it would keep หอยเชลล์ฮอกไกโด permanently unorderable.
                bom_total = con.execute(
                    "SELECT SUM(ingredient_cost) FROM bom_ingredients WHERE sku_id = ?",
                    (sku_id,)
                ).fetchone()[0]
                deleted = 0
                if bom_total is not None and abs(float(bom_total) - float(unit_cost)) > 0.01:
                    deleted = con.execute(
                        "DELETE FROM bom_ingredients WHERE sku_id = ?", (sku_id,)
                    ).rowcount
                con.execute(
                    """INSERT OR REPLACE INTO sku_cost(sku_id, unit_cost, effective_from, updated_at)
                       VALUES(?, ?, ?, ?)""",
                    (sku_id, unit_cost, now[:10], now),
                )
                print(f"[cost-patch] {sku_code}: bom rows removed={deleted}, sku_cost={unit_cost}")
            con.commit()
    except Exception as e:
        print(f"[cost-patch] failed: {e}")


RECIPE_FILE = HERE / "data" / "recipes_2026-09.csv"


def load_recipe_file(con, path=None):
    """Seed bom_ingredients for dishes that have no recipe anywhere.

    The BoM is normally ingested from the owner's Excel workbooks, which live in
    a gitignored folder — nothing that reaches the image or Fly. So a dish the
    workbooks never covered (the Grand Menu lines that shipped at ฿0) can only
    get a recipe through a file that ships WITH the code, which is this one.

    Each row is a real ingredient code at a real price: either the price a
    sibling dish already books for that ingredient, or the purchase price from
    the requisition workbooks. The GRAMS are the proposal — modelled on the
    nearest sibling dish (ยำกุ้งขาว takes ยำไทยปูอัด's 45 g of protein, and so
    on) — and are what the owner should check first. See docs/RECIPES_2026-09.md.

    Skipped for any SKU that already has a recipe OR its own sku_cost, so it can
    never overwrite the owner's numbers; loaded BEFORE _load_estimated_costs so a
    recipe outranks a flat estimate. Delete a dish's rows here once the real
    recipe lands in the workbooks and nothing changes but the source.
    """
    import csv as _csv
    path = Path(path) if path else RECIPE_FILE
    out = {"skus": 0, "updated": 0, "unchanged": 0, "rows": 0, "already_costed": 0,
           "unknown_sku": 0, "bad_row": 0}
    if not path.exists():
        print(f"[recipe] file not found: {path} — skipped")
        return out
    try:
        by_sku = {}
        for rec in _csv.DictReader(open(path, encoding="utf-8-sig", newline="")):
            code = (rec.get("sku_code") or "").strip()
            try:
                qty = float(rec.get("qty_per_serving") or 0)
                price = float(rec.get("price_per_unit") or 0)
                seq = int(rec.get("seq") or 0)
            except (TypeError, ValueError):
                out["bad_row"] += 1
                continue
            if not code or qty <= 0 or price <= 0 or seq <= 0:
                out["bad_row"] += 1
                continue
            by_sku.setdefault(code, []).append((seq, rec, qty, price))

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        with _use(con) as con:
            con.execute(PROVENANCE_DDL)
            for code, rows in by_sku.items():
                r = con.execute("SELECT sku_id FROM skus WHERE sku_code=?", (code,)).fetchone()
                if r is None:
                    out["unknown_sku"] += 1
                    continue
                sku_id = r[0]
                # A recipe is OURS while every one of its rows still carries this
                # file's name — the workbook ingestors DELETE a SKU's rows before
                # writing, and the in-app editor re-stamps source_file, so the first
                # real recipe to arrive takes the dish away from us for good.
                n_all, n_ours = con.execute(
                    "SELECT COUNT(*), SUM(CASE WHEN source_file = ? THEN 1 ELSE 0 END)"
                    " FROM bom_ingredients WHERE sku_id=?", (path.name, sku_id)).fetchone()
                mine = bool(n_all) and n_all == (n_ours or 0)
                have_cost = con.execute("SELECT unit_cost FROM sku_cost WHERE sku_id=?",
                                        (sku_id,)).fetchone()
                if (n_all and not mine) or (have_cost and (have_cost[0] or 0) > 0):
                    con.execute("DELETE FROM cost_provenance WHERE sku_id=? AND source='recipe-file'",
                                (sku_id,))
                    out["already_costed"] += 1
                    continue
                want = [(seq, (rec.get("ingredient_code") or "").strip(), round(qty, 6), round(price, 6))
                        for seq, rec, qty, price in sorted(rows, key=lambda r: r[0])]
                if mine:
                    got = [(s_, c_, round(q_ or 0, 6), round(p_ or 0, 6)) for s_, c_, q_, p_ in con.execute(
                        "SELECT seq, ingredient_code, qty_per_serving, price_per_unit"
                        " FROM bom_ingredients WHERE sku_id=? ORDER BY seq", (sku_id,))]
                    if got == want:
                        _tag(con, sku_id, "recipe-file", "proposed-grams",
                             round(sum(q * p for _, _, q, p in want), 4),
                             "ราคาวัตถุดิบจริง · ปริมาณเป็นข้อเสนอ รอครัวยืนยัน", now)
                        out["unchanged"] += 1
                        continue
                    con.execute("DELETE FROM bom_ingredients WHERE sku_id=?", (sku_id,))
                    out["updated"] += 1
                else:
                    out["skus"] += 1
                total = 0.0
                for seq, rec, qty, price in sorted(rows, key=lambda r: r[0]):
                    con.execute(
                        "INSERT OR REPLACE INTO bom_ingredients(sku_id, seq, ingredient_code,"
                        " ingredient_name, qty_per_serving, unit, price_per_unit, ingredient_cost,"
                        " ingredient_type, source_file) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (sku_id, seq, (rec.get("ingredient_code") or "").strip(),
                         (rec.get("ingredient_name") or "").strip(), qty,
                         (rec.get("unit") or "").strip(), price, round(qty * price, 4),
                         (rec.get("ingredient_type") or "").strip() or None, path.name))
                    out["rows"] += 1
                    total += qty * price
                _tag(con, sku_id, "recipe-file", "proposed-grams", round(total, 4),
                     "ราคาวัตถุดิบจริง · ปริมาณเป็นข้อเสนอ รอครัวยืนยัน", now)
            con.commit()
        print(f"[recipe] seeded recipes: {out}")
    except Exception as e:
        print(f"[recipe] load failed: {e}")
    return out


ESTIMATED_COST_FILE = HERE / "data" / "estimated_costs_2026-09.csv"


def load_estimated_costs(con, path=None):
    """Fill dishes that carry NO cost at all with an estimate, lowest priority.

    Drinks, dipping sauces and ice cream book ฿0 today because nobody ever wrote
    them a recipe — and a ฿0 plate silently flatters every food-cost figure it
    lands in (12% of plates served in Sep 1-15). These numbers come from the
    owner's requisition workbooks (พ.ค./มิ.ย. 69, 5 master branches): the pool of
    ฿ actually drawn for an item, minus whatever a recipe already books, divided
    by the covers that ate it, then by that line's requests per cover — so
    booking them reproduces the real spend rate rather than a guess. A few are
    priced straight off the purchase unit price (wasabi per gram), and the ice
    creams take the cost the system already carries for their own twins.

    Deliberately the WEAKEST source in the chain: a SKU with any real cost — its
    own sku_cost, a Grand Menu master row, a patch, or a recipe — is left alone,
    so this can never overwrite a costed dish. It is a floor under ฿0, not an
    opinion about anything that is already costed. Replace an entry with a real
    recipe and the estimate stops applying on the next start.
    """
    import csv as _csv
    path = Path(path) if path else ESTIMATED_COST_FILE
    out = {"applied": 0, "updated": 0, "unchanged": 0, "already_costed": 0,
           "unknown_sku": 0, "bad_value": 0}
    if not path.exists():
        print(f"[est-cost] file not found: {path} — skipped")
        return out
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        with _use(con) as con:
            con.execute(PROVENANCE_DDL)
            for rec in _csv.DictReader(open(path, encoding="utf-8-sig", newline="")):
                code = (rec.get("sku_code") or "").strip()
                try:
                    cost = float(rec.get("unit_cost") or 0)
                except (TypeError, ValueError):
                    cost = 0
                if not code or cost <= 0:
                    out["bad_value"] += 1
                    continue
                row = con.execute("SELECT sku_id FROM skus WHERE sku_code=?", (code,)).fetchone()
                if row is None:
                    out["unknown_sku"] += 1
                    continue
                sku_id = row[0]
                have = con.execute("SELECT unit_cost FROM sku_cost WHERE sku_id=?", (sku_id,)).fetchone()
                have = (have[0] or 0) if have else 0
                bom = con.execute("SELECT SUM(ingredient_cost) FROM bom_ingredients WHERE sku_id=?",
                                  (sku_id,)).fetchone()[0]
                mine = have > 0 and _ours(con, sku_id, "estimate", have)
                if have > 0 and not mine and abs(have - cost) < 0.005 and not con.execute(
                        "SELECT 1 FROM cost_provenance WHERE sku_id=?", (sku_id,)).fetchone():
                    # Written before provenance existed (2026-09-17): untagged, and
                    # exactly the number this file carries. Adopt it, or it could
                    # never be corrected again.
                    mine = True
                if (have > 0 and not mine) or (bom or 0) > 0:
                    # someone else's number (or a recipe) — not ours to touch; and if
                    # it USED to be ours, it no longer is
                    con.execute("DELETE FROM cost_provenance WHERE sku_id=? AND source='estimate'",
                                (sku_id,))
                    out["already_costed"] += 1
                    continue
                if mine and abs(have - cost) < 0.005:
                    _tag(con, sku_id, "estimate", (rec.get("basis") or "").strip(), cost,
                         (rec.get("note") or "").strip(), now)
                    out["unchanged"] += 1
                    continue
                con.execute(
                    "INSERT OR REPLACE INTO sku_cost(sku_id, unit_cost, effective_from, updated_at)"
                    " VALUES(?, ?, ?, ?)", (sku_id, cost, "2026-09-01", now))
                _tag(con, sku_id, "estimate", (rec.get("basis") or "").strip(), cost,
                     (rec.get("note") or "").strip(), now)
                out["updated" if mine else "applied"] += 1
            con.commit()
        print(f"[est-cost] estimated costs: {out}")
    except Exception as e:
        print(f"[est-cost] load failed: {e}")
    return out


def apply_all(con, rebuild=True):
    """Apply every file-borne cost source to `con`, in precedence order, then
    rebuild sku_effective_cost. Returns {source: summary}. Idempotent."""
    from core import _build_sku_effective_cost
    out = {"grand_menu": load_grand_menu_costs(con),
           "recipes": load_recipe_file(con)}
    # the alias loader's fallback reads sku_effective_cost, so it must see the
    # two sources above before it runs
    _build_sku_effective_cost(con, force=True)
    out["aliases"] = load_grand_menu_aliases(con)
    apply_cost_patches(con)
    out["estimates"] = load_estimated_costs(con)
    if rebuild:
        _build_sku_effective_cost(con, force=True)
    return out
