# -*- coding: utf-8 -*-
"""bom_center.release — review and release (plan §4.2 M5, §5.4, Sprint S3).

Workflow (owner decisions §10-2, §10-3):

    draft ──(analyst: 12 checks pass, every block resolved)──► released
        ▲                                                       │
        └── new draft copied from it next month                 ├─(management)─► acknowledged
                                                                └─(either)─────► retired (rollback)

* The ANALYST releases (module bom.release), by the 25th of the month before the
  effective month — later is allowed but warned.
* MANAGEMENT checks AFTER release (module bom.audit): reads the comparison and
  acknowledges, or retires the release, which puts the previous one back in force.
* A check of severity 'block' stops the release until fixed; check 5 (a dish
  moving ±50 %) can instead be ACCEPTED with a reason, recorded with the name.

The comparison ("what changes if this is released") prices every recipe in the
draft against the cost in force today, and turns the difference into money with
the last full month's sales: COGS by branch, and food cost % by GUEST tier from
the per-bill fact table (menu_fact_*: what each tier's guests actually ate) —
the same view the analyst builds by hand in "Cal ต้นทุน Grandmenu".

Releasing does NOT yet feed the reports: until the S4 cut-over the COGS engine
reads sku_effective_cost. costs_in_force() is the function S4 will switch to.
"""
import json
from datetime import date, datetime, timezone

from .master import audit
from .recipes import Coster, cost_recipe, recipe_lines, release_row, release_costs, write_recipe
from .schema import ensure

CHECKS = {
    "C01": ("block", "วัตถุดิบไม่มีราคาเดือนที่ใช้คิด"),
    "C02": ("block", "บรรทัดต้นทุน ฿0 ที่ไม่ได้ติดธง “ตั้งใจเป็น 0”"),
    "C03": ("block", "แปลงหน่วยเป็นหน่วยฐานไม่ได้"),
    "C04": ("block", "ไม่มีรหัสวัตถุดิบ หรือรหัสไม่อยู่ในทะเบียน"),
    "C05": ("block", "ต้นทุนเมนูเปลี่ยน ≥ ±50% จากที่ใช้อยู่ — ต้องยืนยันพร้อมเหตุผล"),
    "C06": ("warn", "ต้นทุนเมนูเปลี่ยน ≥ ±20% จากที่ใช้อยู่"),
    "C07": ("warn", "เมนูบุฟเฟ่ต์ที่ขายเดือนล่าสุดแต่ไม่มีสูตรใน release นี้"),
    "C08": ("warn", "ต้นทุนไม่ตรงยอดในไฟล์ต้นทาง (> ฿0.01)"),
    "C09": ("block", "รหัส POS ไม่มีในระบบ / สูตรว่าง"),
    "C10": ("warn", "วัตถุดิบกึ่งสำเร็จยังไม่มีสูตร (ใช้ราคาซื้อ/ใบเสนอไปก่อน — Phase 1.5)"),
    "C11": ("block", "ต้นทุนดูเหมือนราคาขาย"),
    "C12": ("warn", "สัดส่วนผิดธรรมชาติ (> 1 กก./ลิตร ต่อเสิร์ฟ หรือต้นทุนรวม ฿0)"),
    "UNM": ("warn", "สูตรในไฟล์ที่ยังไม่จับคู่รหัส POS"),
}
ACCEPTABLE = {"C05"}               # a block a person may accept with a reason


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _sales(con) -> str:
    """The service-charge-free sales view the app creates at startup, else the base
    table (a script or a test on a bare DB); the service-charge line has no recipe,
    so either answers the same here."""
    return "v_sales_sku_monthly" if con.execute(
        "SELECT 1 FROM sqlite_master WHERE name='v_sales_sku_monthly'").fetchone() else "sales_sku_monthly"


def last_full_month(con) -> str:
    """The newest month of sales that is over (the current calendar month never is)."""
    today = date.today()
    cur = f"{today.year:04d}-{today.month:02d}"
    r = con.execute("SELECT MAX(period_month) FROM sales_sku_monthly WHERE period_month < ?", (cur,)).fetchone()
    return r[0] if r and r[0] else cur


# ── the cost in force ─────────────────────────────────────────────────────────
def costs_in_force(con, month: str, exclude_release: int = None) -> dict:
    """{sku_code: (unit_cost, where)} as the center would answer for `month`:
    for each SKU, the newest RELEASED monthly release with effective_month <= month
    that holds a recipe for it; otherwise the baseline (v0); otherwise the legacy
    engine table. `exclude_release` leaves one release out (the draft under review)."""
    ensure(con)
    from .dated import CUTOVER_MONTH
    out = {}
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='sku_effective_cost'").fetchone():
        for code, v in con.execute("SELECT s.sku_code, e.recipe_cost FROM sku_effective_cost e JOIN skus s USING(sku_id)"):
            out[code] = (float(v or 0.0), "legacy")
    base = con.execute("SELECT release_id FROM bc_release WHERE kind='baseline' AND status='released'"
                       " ORDER BY release_id DESC LIMIT 1").fetchone()
    if base:
        for code, v in con.execute("SELECT sku_code, unit_cost FROM bcd_cost_cache WHERE release_id=?", (base[0],)):
            out[code] = (float(v or 0.0), "v0")
    if month < CUTOVER_MONTH:                    # before the Grand Menu: the old engine, always
        return out
    rels = con.execute("SELECT release_id, label FROM bc_release WHERE kind='monthly' AND status='released'"
                       " AND effective_month <= ? AND release_id IS NOT ? ORDER BY effective_month, release_id",
                       (month, exclude_release)).fetchall()
    for rid, label in rels:                      # oldest first: a newer release overwrites
        for code, (total, _p) in release_costs(con, rid).items():
            out[code] = (float(total or 0.0), label)
    return out


# ── checks ───────────────────────────────────────────────────────────────────
def run_checks(con, release_id: int) -> dict:
    """Recompute every check for a draft; keep earlier acceptances of the same finding."""
    ensure(con)
    rel = release_row(con, release_id)
    kept = {(c, r): (by, at, msg) for c, r, by, at, msg in con.execute(
        "SELECT check_code, ref, resolved_by, resolved_at, message FROM bc_release_check"
        " WHERE release_id=? AND resolved_by IS NOT NULL", (release_id,))}
    con.execute("DELETE FROM bc_release_check WHERE release_id=? AND check_code NOT IN ('unmatched')", (release_id,))
    findings = []

    def add(code, ref, msg):
        findings.append((code, ref, msg))

    known_ing = {r[0] for r in con.execute("SELECT code FROM bc_ingredient")}
    kinds = dict(con.execute("SELECT code, kind FROM bc_ingredient"))
    has_prep = {r[0] for r in con.execute("SELECT prepared_code FROM bc_prep_recipe")}
    pos = {r[0] for r in con.execute("SELECT sku_code FROM skus")}
    names = dict(con.execute("SELECT sku_code, description FROM skus"))
    month_now = rel["effective_month"]
    before = costs_in_force(con, month_now, exclude_release=release_id)
    coster = Coster(con, rel["price_month"] or rel["effective_month"])
    sales_month = last_full_month(con)
    avg_price = {c: (rev / q if q else None) for c, rev, q in con.execute(
        "SELECT s.sku_code, SUM(m.subtotal_ex_vat), SUM(m.qty) FROM " + _sales(con) + " m JOIN skus s USING(sku_id)"
        " WHERE m.period_month=? GROUP BY s.sku_code", (sales_month,))}
    recipes = [r[0] for r in con.execute("SELECT sku_code FROM bc_recipe WHERE release_id=?", (release_id,))]
    for sku in recipes:
        if sku not in pos:
            add("C09", sku, f"{sku} ไม่มีใน POS")
            continue
        c = cost_recipe(con, release_id, sku, coster)
        if not c["lines"] and c["kind"] == "recipe":
            add("C09", sku, f"{names.get(sku) or sku}: สูตรว่าง")
            continue
        label = names.get(sku) or sku
        for ln in c["lines"]:
            ref = f"{sku}#{ln['seq']}"
            p = ln.get("problem") or ""
            code = ln.get("ingredient_code")
            if not code or code not in known_ing:
                add("C04", ref, f"{label} บรรทัด {ln['seq']}: {ln.get('ingredient_name') or ''} — ไม่มีรหัสหรือรหัสไม่อยู่ในทะเบียน")
            elif p.startswith("ไม่มีราคา"):
                add("C01", ref, f"{label} บรรทัด {ln['seq']}: {code} ไม่มีราคาเดือน {coster.month}")
            elif p:
                add("C03", ref, f"{label} บรรทัด {ln['seq']}: {code} — {p}")
            elif not ln.get("zero") and (ln.get("cost") or 0) <= 0:
                add("C02", ref, f"{label} บรรทัด {ln['seq']}: {code} ต้นทุน ฿0 แต่ไม่ได้ติดธงตั้งใจเป็น 0")
            if code and kinds.get(code) == "prepared" and code not in has_prep and not ln.get("zero"):
                add("C10", code, f"{code} {ln.get('ingredient_name') or ''} (กึ่งสำเร็จ) ยังไม่มีสูตรย่อย")
            qb = ln.get("qty_base")
            if qb and ln.get("base_unit") in ("กรัม", "มิลลิลิตร") and qb > 1000:
                add("C12", ref, f"{label} บรรทัด {ln['seq']}: {qb:,.0f} {ln['base_unit']} ต่อเสิร์ฟ")
        total = c["total"] or 0.0
        if total <= 0 and not c["problems"]:
            add("C12", sku, f"{label}: ต้นทุนรวม ฿0")
        old = before.get(sku, (None, None))[0]
        if old and total and not c["problems"]:
            pct = 100.0 * (total - old) / old
            if abs(pct) >= 50:
                add("C05", sku, f"{label}: ฿{old:,.2f} → ฿{total:,.2f} ({pct:+.0f}%)")
            elif abs(pct) >= 20:
                add("C06", sku, f"{label}: ฿{old:,.2f} → ฿{total:,.2f} ({pct:+.0f}%)")
        if c.get("source_cost") is not None and abs(total - c["source_cost"]) > 0.01 and not c["problems"]:
            add("C08", sku, f"{label}: ระบบ ฿{total:,.2f} / ไฟล์ ฿{c['source_cost']:,.2f}")
        ap = avg_price.get(sku)
        if ap and ap > 0 and total >= 0.85 * ap:
            add("C11", sku, f"{label}: ต้นทุน ฿{total:,.2f} ≈ ราคาขายเฉลี่ย ฿{ap:,.2f}")
    # C07: buffet dishes sold last month with no recipe here (scope = buffet)
    if rel["scope"] in ("buffet", "all"):
        have = set(recipes)
        for code, desc, q in con.execute(
                "SELECT s.sku_code, s.description, SUM(m.qty) FROM " + _sales(con) + " m JOIN skus s USING(sku_id)"
                " WHERE m.period_month=? AND s.sku_code LIKE 'OTHBF%' GROUP BY s.sku_code HAVING SUM(m.qty) > 0",
                (sales_month,)):
            if code not in have:
                add("C07", code, f"{desc or code}: ขาย {q:,.0f} จานใน {sales_month} — ใช้ต้นทุนจากเวอร์ชันก่อน")
    seen = set()
    for code, ref, msg in findings:
        if (code, ref) in seen:
            continue
        seen.add((code, ref))
        sev = CHECKS[code][0]
        by, at, old_msg = kept.get((code, ref), (None, None, None))
        if by and code in ACCEPTABLE:
            reason = (old_msg or "").split(" ‖ ยอมรับ: ", 1)
            msg = msg + (" ‖ ยอมรับ: " + reason[1] if len(reason) > 1 else "")
        else:
            by = at = None
        con.execute("INSERT OR REPLACE INTO bc_release_check(release_id, check_code, ref, severity, message,"
                    " resolved_by, resolved_at) VALUES(?,?,?,?,?,?,?)", (release_id, code, ref, sev, msg, by, at))
    return checks_summary(con, release_id)


def checks_summary(con, release_id: int) -> dict:
    rows = [dict(zip(("check_code", "ref", "severity", "message", "resolved_by", "resolved_at"), r)) for r in con.execute(
        "SELECT check_code, ref, severity, message, resolved_by, resolved_at FROM bc_release_check"
        " WHERE release_id=? ORDER BY severity='warn', check_code, ref", (release_id,))]
    for r in rows:
        if r["check_code"] == "unmatched":
            r["check_code"], r["severity"] = "UNM", "warn"
            try:
                m = json.loads(r["message"])
                r["message"] = f"฿{m.get('tier')} · {m.get('th') or m.get('en')} — ยังไม่จับคู่รหัส POS"
            except Exception:
                pass
        r["title"] = CHECKS.get(r["check_code"], ("", r["check_code"]))[1]
        r["acceptable"] = r["check_code"] in ACCEPTABLE
    open_blocks = [r for r in rows if r["severity"] == "block" and not r["resolved_by"]]
    by_code = {}
    for r in rows:
        k = by_code.setdefault(r["check_code"], {"code": r["check_code"], "title": r["title"], "severity": r["severity"],
                                                  "count": 0, "open": 0})
        k["count"] += 1
        k["open"] += 0 if r["resolved_by"] else 1
    return {"release_id": release_id, "rows": rows, "by_code": sorted(by_code.values(), key=lambda k: k["code"]),
            "open_blocks": len(open_blocks), "warnings": sum(1 for r in rows if r["severity"] == "warn"),
            "can_release": not open_blocks}


def accept(con, release_id: int, code: str, ref: str, reason: str, who: str):
    if code not in ACCEPTABLE:
        raise ValueError("ข้อนี้ต้องแก้ ยอมรับไม่ได้")
    if not (reason or "").strip():
        raise ValueError("ต้องระบุเหตุผล")
    r = con.execute("SELECT message FROM bc_release_check WHERE release_id=? AND check_code=? AND ref=?",
                    (release_id, code, ref)).fetchone()
    if not r:
        raise ValueError("ไม่พบรายการตรวจนี้ — กดตรวจใหม่ก่อน")
    msg = r[0].split(" ‖ ยอมรับ: ")[0] + " ‖ ยอมรับ: " + reason.strip()
    con.execute("UPDATE bc_release_check SET resolved_by=?, resolved_at=?, message=? WHERE release_id=? AND"
                " check_code=? AND ref=?", (who, now(), msg, release_id, code, ref))
    audit(con, who, "accept_check", "bc_release_check", f"{release_id}|{code}|{ref}", None, reason)


# ── comparison ───────────────────────────────────────────────────────────────
def compare(con, release_id: int, month: str = None) -> dict:
    """What changes if this release is put in force: per dish, per branch, per guest tier."""
    ensure(con)
    rel = release_row(con, release_id)
    month = month or last_full_month(con)
    new = release_costs(con, release_id)
    before = costs_in_force(con, rel["effective_month"], exclude_release=release_id)
    sku_id = dict(con.execute("SELECT sku_code, sku_id FROM skus"))
    names = dict(con.execute("SELECT sku_code, description FROM skus"))
    delta = {}
    dishes = []
    for code, (total, problems) in new.items():
        old = before.get(code, (None, None))
        d = (total or 0.0) - (old[0] or 0.0)
        delta[code] = d
        dishes.append({"sku_code": code, "name": names.get(code), "old": old[0], "old_from": old[1], "new": total,
                       "problems": problems, "delta": round(d, 4),
                       "pct": round(100.0 * d / old[0], 1) if old[0] else None})
    ids = {sku_id[c]: c for c in delta if c in sku_id}
    # per branch, last full month
    names_b = dict(con.execute("SELECT branch_id, name FROM branches"))
    by_branch = {}
    qty_by_sku = {}
    for bid, sid, q in con.execute("SELECT branch_id, sku_id, SUM(qty) FROM " + _sales(con) + " WHERE period_month=?"
                                   " GROUP BY branch_id, sku_id", (month,)):
        code = ids.get(sid)
        if not code:
            continue
        b = by_branch.setdefault(bid, {"branch_id": bid, "branch": names_b.get(bid, str(bid)), "plates": 0.0, "delta": 0.0})
        b["plates"] += q or 0
        b["delta"] += (q or 0) * delta[code]
        qty_by_sku[code] = qty_by_sku.get(code, 0.0) + (q or 0)
    for d in dishes:
        d["plates"] = round(qty_by_sku.get(d["sku_code"], 0.0))
        d["impact"] = round(d["plates"] * d["delta"], 2)
    dishes.sort(key=lambda d: -abs(d["impact"]))
    branches = sorted(({**b, "delta": round(b["delta"], 2), "plates": round(b["plates"])} for b in by_branch.values()),
                      key=lambda b: -abs(b["delta"]))
    # per guest tier from the per-bill fact table
    tiers = []
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='menu_fact_day'").fetchone():
        first, last = f"{month}-01", f"{month}-31"
        tot = {t: (rev or 0.0, cogs or 0.0, pax or 0) for t, rev, cogs, pax in con.execute(
            "SELECT tier, SUM(rev), SUM(cogs), SUM(pax) FROM menu_fact_total WHERE day BETWEEN ? AND ? GROUP BY tier",
            (first, last))}
        dt = {}
        for t, ent, q in con.execute("SELECT tier, ent, SUM(qty) FROM menu_fact_day WHERE day BETWEEN ? AND ?"
                                     " AND ent LIKE 's:%' GROUP BY tier, ent", (first, last)):
            try:
                code = ids.get(int(ent[2:]))
            except ValueError:
                continue
            if code:
                dt[t] = dt.get(t, 0.0) + (q or 0) * delta[code]
        for t, (rev, cogs, pax) in sorted(tot.items(), key=lambda kv: (not kv[0].isdigit(), int(kv[0]) if kv[0].isdigit() else 0)):
            if not rev:
                continue
            new_cogs = cogs + dt.get(t, 0.0)
            tiers.append({"tier": t, "pax": pax, "rev": round(rev, 2), "cogs_old": round(cogs, 2),
                          "cogs_new": round(new_cogs, 2), "food_pct_old": round(100 * cogs / rev, 1),
                          "food_pct_new": round(100 * new_cogs / rev, 1),
                          "head_old": round(cogs / pax, 2) if pax else None,
                          "head_new": round(new_cogs / pax, 2) if pax else None})
    return {"release": rel, "month": month, "dishes": dishes, "branches": branches, "tiers": tiers,
            "total_delta": round(sum(b["delta"] for b in branches), 2),
            "changed": sum(1 for d in dishes if abs(d["delta"]) > 0.005)}


# ── actions ──────────────────────────────────────────────────────────────────
def check_effective_month(month: str):
    """Owner 2026-10-06: the center applies from the Grand Menu month on, never before."""
    from .dated import CUTOVER_MONTH
    if month < CUTOVER_MONTH:
        raise ValueError(f"เวอร์ชันต้องมีผลตั้งแต่ {CUTOVER_MONTH} (เริ่มเมนูใหม่) — เดือนก่อนหน้าใช้ต้นทุนระบบเดิมเสมอ")


def do_release(con, release_id: int, who: str, backdate_reason: str = None, today: date = None) -> dict:
    """Release. A version effective for a month that has already started (the very
    first one, effective 2026-09, released in October) RE-COSTS sales already made,
    so it needs an explicit backdate reason, recorded in the audit trail."""
    from . import dated
    rel = release_row(con, release_id)
    if not rel or rel["kind"] != "monthly":
        raise ValueError("ไม่พบ release")
    if rel["status"] not in ("draft", "review"):
        raise ValueError(f"release นี้สถานะ {rel['status']} — ปล่อยซ้ำไม่ได้")
    check_effective_month(rel["effective_month"])
    today = today or date.today()
    eff = rel["effective_month"]
    cur = f"{today.year:04d}-{today.month:02d}"
    backdated = eff <= cur
    if backdated and not (backdate_reason or "").strip():
        raise ValueError(f"เวอร์ชันนี้มีผลเดือน {eff} ซึ่งเริ่มไปแล้ว — การปล่อยจะคิดต้นทุนยอดขายที่เกิดไปแล้วใหม่ "
                         "ต้องระบุเหตุผลการย้อนหลัง")
    summary = run_checks(con, release_id)
    if not summary["can_release"]:
        raise ValueError(f"ยังมีข้อที่ต้องแก้ {summary['open_blocks']} รายการ — ดูที่รายการตรวจ")
    late = None
    y, m = int(eff[:4]), int(eff[5:7])
    deadline = date(y - (m == 1), 12 if m == 1 else m - 1, 25)
    if backdated:
        late = f"ย้อนหลัง: มีผล {eff} — คิดต้นทุนยอดขายตั้งแต่ {eff} ใหม่ ({backdate_reason.strip()})"
    elif today > deadline:
        late = f"ปล่อยหลังกำหนด (ควรปล่อยภายใน {deadline.isoformat()} สำหรับเดือนที่มีผล {eff})"
    # an earlier release of the same month steps aside
    con.execute("UPDATE bc_release SET status='retired', notes=COALESCE(notes,'') || ? WHERE kind='monthly' AND"
                " status='released' AND effective_month=? AND release_id<>?",
                (f" · แทนที่โดย release {release_id} ({now()})", eff, release_id))
    con.execute("UPDATE bc_release SET status='released', released_by=?, released_at=?, acknowledged_by=NULL,"
                " acknowledged_at=NULL WHERE release_id=?", (who, now(), release_id))
    from .engine import rebuild_cost_cache
    rebuild_cost_cache(con, release_id)
    d = dated.rebuild(con, today)
    audit(con, who, "release", "bc_release", str(release_id), rel["status"],
          json.dumps({"late": late, "backdated": backdated, "dated_rows": d["rows"]}, ensure_ascii=False))
    return {"released": True, "late": late, "backdated": backdated, "warnings": summary["warnings"],
            "dated_rows": d["rows"]}


def acknowledge(con, release_id: int, who: str, note: str = ""):
    rel = release_row(con, release_id)
    if not rel or rel["status"] != "released":
        raise ValueError("รับทราบได้เฉพาะ release ที่ปล่อยแล้ว")
    if who not in ("-", "", None) and who == rel["released_by"]:
        raise ValueError("ผู้รับทราบต้องไม่ใช่คนเดียวกับผู้ปล่อย")
    con.execute("UPDATE bc_release SET acknowledged_by=?, acknowledged_at=?, notes=COALESCE(notes,'') || ?"
                " WHERE release_id=?", (who, now(), (" · รับทราบ: " + note.strip()) if note and note.strip() else "",
                                        release_id))
    audit(con, who, "acknowledge", "bc_release", str(release_id), None, note)


def retire(con, release_id: int, who: str, reason: str):
    rel = release_row(con, release_id)
    if not rel or rel["status"] != "released" or rel["kind"] != "monthly":
        raise ValueError("ย้อนกลับได้เฉพาะ release รายเดือนที่ปล่อยแล้ว")
    if not (reason or "").strip():
        raise ValueError("ต้องระบุเหตุผล")
    con.execute("UPDATE bc_release SET status='retired', notes=COALESCE(notes,'') || ? WHERE release_id=?",
                (f" · ย้อนกลับโดย {who}: {reason.strip()}", release_id))
    con.execute("DELETE FROM bcd_cost_cache WHERE release_id=?", (release_id,))
    from . import dated
    dated.rebuild(con)
    audit(con, who, "retire", "bc_release", str(release_id), "released", reason)


def new_draft_from(con, src_id: int, label: str, effective_month: str, price_month: str, who: str) -> int:
    """Next month's draft: every recipe of `src_id` copied, priced at a new month."""
    src = release_row(con, src_id)
    if not src or src["kind"] != "monthly":
        raise ValueError("คัดลอกได้จาก release รายเดือนเท่านั้น")
    check_effective_month(effective_month)
    if con.execute("SELECT 1 FROM bc_release WHERE label=?", (label,)).fetchone():
        raise ValueError(f"มีชื่อ {label} แล้ว")
    con.execute("INSERT INTO bc_release(label, kind, effective_month, status, scope, created_by, created_at,"
                " parent_release_id, price_month) VALUES(?, 'monthly', ?, 'draft', ?, ?, ?, ?, ?)",
                (label, effective_month, src["scope"], who, now(), src_id, price_month))
    rid = con.execute("SELECT release_id FROM bc_release WHERE label=?", (label,)).fetchone()[0]
    for code, source, scost, sref in con.execute("SELECT sku_code, source, source_cost, source_ref FROM bc_recipe"
                                                 " WHERE release_id=?", (src_id,)).fetchall():
        write_recipe(con, rid, code, recipe_lines(con, src_id, code), source=source, source_cost=scost,
                     source_ref=sref, who=who, register_codes=False)
    audit(con, who, "new_draft", "bc_release", str(rid), src_id, effective_month)
    return rid
