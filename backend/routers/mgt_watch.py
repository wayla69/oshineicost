# -*- coding: utf-8 -*-
"""PR-PW (owner 2026-10-29, phone decisions no. 7-10, 12-14) · ตรวจวันนี้ - the second block of the phone's management summary.

Every item is a finding with the branches to act on and the action, computed over the summary's own month window
(cs..ce, compared with ps..pe) on the scope of the report (branch / group), and every item is guarded: a missing table
or an empty source answers `available: False` with a reason, never an error, and never takes the report down.

  7  portion    raw material issued per cover (confirmed issue slips, bc_issue_*) by high-value ingredient group against the
                chain norm - the branch over-serving salmon
  8  purchases  purchases as % of sales per branch (imported purchase weeks, /api/ingredients/purchases) against the chain
  9  forced     monitored (บังคับสั่ง) ingredients a branch used to order but has not ordered in the last 14 days
  10 newmenu    new dishes reaching their 28th day: plates a day in their latest week vs their first week - keep or drop
  12 voids      cancelled bills as % of bills per branch (bands 0.5 / 1.0 %, rules of ร้านของฉัน) and discounted bills
                printed more than once, per branch
  13 recon      bank-vs-POS sign-off of the month to close (recon_sessions of the recon app, shared DB; deadline the 5th)
  14 tiermix    the premium buffet tier's share of guests per branch against the comparison period - where upselling slipped

Levels: fix (act today) > watch > ok; `none` = nothing to flag. The phone sorts by level and shows the line and the rows.
"""
import datetime as _dt

LEVELS = {"fix": 0, "watch": 1, "ok": 2, "none": 3}
T_PORTION_OVER_PCT = 15          # a branch 15 % or more over the chain's grams per cover
T_PORTION_MIN_DAYS = 7           # ... on at least 7 confirmed slip days
T_PURCHASE_OVER_PP = 5.0         # purchases % of sales 5 points or more over the chain's
T_FORCED_GAP_DAYS = 14           # a usual monitored item not ordered for 14 days
T_FORCED_USUAL_DAYS = 60         # ... "usual" = ordered within the 60 days before that
T_NEWMENU_FROM, T_NEWMENU_TO = 21, 35    # a dish 21-35 days old is at its 28-day decision
T_NEWMENU_WEEK = 7                       # ... its latest 7 days against its first 7 days
T_VOID_WATCH, T_VOID_FIX = 0.5, 1.0      # cancelled bills % of bills (franchise.py bands)
T_TIER_DROP_PP = 5.0             # premium share down 5 points or more
RECON_DEADLINE_DAY = 5


def _scope_ids(con, branch, group) -> dict:
    from rules import scope_filter
    base = ["b.name!='ALL'"]; params: dict = {}
    scope_filter(base, params, branch, group)
    return {r[0]: r[1] for r in con.execute(f"SELECT b.branch_id, b.name FROM branches b WHERE {' AND '.join(base)}", params)}


def _covers(con, ids, s, e) -> dict:
    ph = ",".join("?" * len(ids))
    return {r[0]: r[1] or 0 for r in con.execute(
        f"SELECT branch_id, SUM(customer_count) FROM sales_transaction WHERE source='Easy' AND branch_id IN ({ph}) "
        f"AND date(sold_at) BETWEEN ? AND ? GROUP BY branch_id", list(ids) + [s.isoformat(), e.isoformat()])}


def _bills(con, ids, s, e) -> dict:
    ph = ",".join("?" * len(ids))
    return {r[0]: r[1] or 0 for r in con.execute(
        f"SELECT branch_id, COUNT(*) FROM sales_transaction WHERE source='Easy' AND branch_id IN ({ph}) "
        f"AND date(sold_at) BETWEEN ? AND ? GROUP BY branch_id", list(ids) + [s.isoformat(), e.isoformat()])}


def _unavailable(key, title, why):
    return {"key": key, "title": title, "available": False, "level": "none", "line": why, "rows": []}


# ── 7 · portion: issued grams per cover by ingredient group ──────────────────────────────────────────────────────────
def portion(con, ids, cs, ce, th=True) -> dict:
    import ingredient_groups as IG
    t = "สัดส่วนการตัก" if th else "Portioning"
    rows = con.execute("""
        SELECT s.branch_id, i.name_th, i.base_unit, SUM(COALESCE(l.qty_used_base,0)+COALESCE(l.qty_waste_base,0)) q,
               COUNT(DISTINCT s.slip_date) days
        FROM bc_issue_slip s JOIN bc_issue_line l ON l.slip_id=s.slip_id JOIN bc_ingredient i ON i.code=l.ingredient_code
        WHERE s.status='confirmed' AND s.slip_date BETWEEN ? AND ?
        GROUP BY s.branch_id, i.code""", (cs.isoformat(), ce.isoformat())).fetchall()
    if not rows:
        return _unavailable("portion", t, "ยังไม่มีใบตัดเบิกที่ยืนยันในเดือนนี้" if th else "no confirmed issue slips this month")
    acc: dict = {}; days: dict = {}; unit: dict = {}
    for bid, name, bu, q, d in rows:
        if bid not in ids:
            continue
        days[bid] = max(days.get(bid, 0), d or 0)
        for slug in IG.match(name or ""):
            acc.setdefault(slug, {})[bid] = acc.setdefault(slug, {}).get(bid, 0.0) + (q or 0)
            unit.setdefault(slug, bu or "")
    cov = _covers(con, ids, cs, ce)
    out_groups = []
    for slug, per in acc.items():
        ok = {b: q for b, q in per.items() if days.get(b, 0) >= T_PORTION_MIN_DAYS and cov.get(b, 0) > 0}
        if len(ok) < 2:
            continue
        chain = sum(ok.values()) / sum(cov[b] for b in ok)
        if chain <= 0:
            continue
        over = sorted(({"branch": ids[b], "per_cover": round(q / cov[b], 1), "vs_chain_pct": round((q / cov[b] / chain - 1) * 100),
                        "excess": (q / cov[b] - chain) * cov[b]} for b, q in ok.items() if q / cov[b] / chain - 1 >= T_PORTION_OVER_PCT / 100),
                      key=lambda r: -r["excess"])
        out_groups.append({"slug": slug, "label": IG.LABELS.get(slug, slug), "unit": unit.get(slug, ""), "chain_per_cover": round(chain, 1),
                           "branches": len(ok), "over": [{k: v for k, v in r.items() if k != "excess"} for r in over[:3]],
                           "over_count": len(over), "_q": sum(ok.values())})
    if not out_groups:
        return _unavailable("portion", t, "ใบตัดเบิกยังไม่ถึง 7 วันในสองสาขาขึ้นไป" if th else "fewer than 7 slip days at two branches")
    out_groups.sort(key=lambda g: (-len(g["over"]), -g["_q"]))
    g = out_groups[0]
    for x in out_groups:
        x.pop("_q")
    if not g["over"]:
        return {"key": "portion", "title": t, "available": True, "level": "ok", "groups": out_groups, "rows": [],
                "line": (f"ทุกสาขาตัก{g['label']}ใกล้ค่าเครือ ({g['chain_per_cover']} {g['unit']}/หัว)" if th
                         else f"every branch within {T_PORTION_OVER_PCT}% of the chain's {g['label']} per cover")}
    names = " · ".join(f"{r['branch']} {r['per_cover']} ({'+' if r['vs_chain_pct'] > 0 else ''}{r['vs_chain_pct']}%)" for r in g["over"])
    return {"key": "portion", "title": t, "available": True, "level": "fix" if g["over"][0]["vs_chain_pct"] >= 2 * T_PORTION_OVER_PCT else "watch",
            "groups": out_groups, "rows": g["over"],
            "line": (f"{g['label']} ต่อหัว เกินค่าเครือ ({g['chain_per_cover']} {g['unit']}/หัว) {g['over_count']} สาขา: {names} — ตรวจการตักที่สาขา" if th
                     else f"{g['label']} per cover over the chain ({g['chain_per_cover']} {g['unit']}): {names} - check portioning")}


# ── 8 · purchases as % of sales per branch ──────────────────────────────────────────────────────────────────────────
def purchases(con, ids, cs, ce, branch, group, th=True) -> dict:
    from routers.ingredients import _ing_purchases_data
    t = "ยอดซื้อเทียบยอดขาย" if th else "Purchases vs sales"
    d = _ing_purchases_data(date_from=cs.isoformat(), date_to=ce.isoformat(), branch=branch, group=group)
    brs = [b for b in (d.get("branches") or []) if (b.get("amount") or 0) > 0 and b.get("pct") is not None]
    if not brs:
        return _unavailable("purchases", t, "ยังไม่มีไฟล์ยอดซื้อของเดือนนี้" if th else "no purchase file for this month")
    chain = (d.get("totals") or {}).get("pct")
    if chain is None:
        chain = sum(b["amount"] for b in brs) / max(1.0, sum(b.get("revenue") or 0 for b in brs)) * 100
    over = sorted(({"branch": b["branch"], "pct": round(b["pct"], 1), "vs_chain_pp": round(b["pct"] - chain, 1),
                    "baht": round((b["pct"] - chain) / 100 * (b.get("revenue") or 0)), "weeks_missing": b.get("weeks_missing") or 0}
                   for b in brs if b["pct"] - chain >= T_PURCHASE_OVER_PP and not (b.get("weeks_missing") or 0)), key=lambda r: -r["baht"])
    if not over:
        return {"key": "purchases", "title": t, "available": True, "level": "ok", "chain_pct": round(chain, 1), "rows": [],
                "line": (f"ทุกสาขาซื้อใกล้ค่าเครือ ({chain:.1f}% ของยอดขาย)" if th else f"every branch near the chain's {chain:.1f}% of sales")}
    names = " · ".join(f"{r['branch']} {r['pct']}% (+{r['vs_chain_pp']} {'จุด' if th else 'pts'} ฿{r['baht']:,.0f})" for r in over[:3])
    return {"key": "purchases", "title": t, "available": True, "level": "fix" if over[0]["vs_chain_pp"] >= 2 * T_PURCHASE_OVER_PP else "watch",
            "chain_pct": round(chain, 1), "rows": over[:3], "over_count": len(over),
            "line": (f"ซื้อเกินค่าเครือ ({chain:.1f}% ของยอดขาย) {len(over)} สาขา: {names} — นับสต็อก / ทบทวนการสั่ง" if th
                     else f"{len(over)} branches above the chain's {chain:.1f}%: {names} - stock count / review ordering")}


# ── 9 · monitored ingredients a branch stopped ordering ─────────────────────────────────────────────────────────────
def forced_gaps(con, ids, ce, th=True) -> dict:
    from routers.ingredients import _forced_order
    t = "วัตถุดิบบังคับสั่ง" if th else "Monitored ingredients"
    fo = _forced_order()
    codes = fo.get("codes") or {}
    if not codes:
        return _unavailable("forced", t, "ยังไม่มีรายการบังคับสั่ง" if th else "no forced-order list")
    s_usual = ce - _dt.timedelta(days=T_FORCED_GAP_DAYS + T_FORCED_USUAL_DAYS)
    s_gap = ce - _dt.timedelta(days=T_FORCED_GAP_DAYS - 1)
    rows = con.execute("""
        SELECT o.branch_id, l.ingredient_code, MAX(o.cycle_date) last
        FROM bc_order o JOIN bc_order_line l ON l.order_id=o.order_id
        WHERE o.status IN ('submitted','processed','received') AND COALESCE(l.confirmed_units, l.suggested_units, 0) > 0
          AND o.cycle_date BETWEEN ? AND ?
        GROUP BY o.branch_id, l.ingredient_code""", (s_usual.isoformat(), ce.isoformat())).fetchall()
    if not rows:
        return _unavailable("forced", t, "ยังไม่มีใบสั่งซื้อในช่วง 74 วันล่าสุด" if th else "no orders in the last 74 days")
    gaps: dict = {}
    for bid, code, last in rows:
        if bid not in ids or code not in codes:
            continue
        if last < s_gap.isoformat():
            gaps.setdefault(bid, []).append({"code": code, "name": codes[code].get("name") or code, "last": last})
    out = sorted(({"branch": ids[b], "items": sorted(g, key=lambda x: x["last"]), "count": len(g)} for b, g in gaps.items()), key=lambda r: -r["count"])
    if not out:
        return {"key": "forced", "title": t, "available": True, "level": "ok", "rows": [],
                "line": (f"ทุกสาขาสั่งวัตถุดิบบังคับสั่งครบใน {T_FORCED_GAP_DAYS} วันล่าสุด" if th else f"every branch ordered its monitored items in the last {T_FORCED_GAP_DAYS} days")}
    names = " · ".join(f"{r['branch']} {r['count']} {'รายการ' if th else 'items'} ({', '.join(x['name'] for x in r['items'][:2])}{' …' if r['count'] > 2 else ''})" for r in out[:3])
    return {"key": "forced", "title": t, "available": True, "level": "watch", "rows": out[:3], "over_count": len(out),
            "line": (f"ไม่ได้สั่งมา {T_FORCED_GAP_DAYS} วัน ทั้งที่เคยสั่ง: {names} — ถามสาขาก่อนสุดสัปดาห์" if th
                     else f"not ordered for {T_FORCED_GAP_DAYS} days though usual: {names} - ask before the weekend")}


# ── 10 · new dishes at their 28-day decision ─────────────────────────────────────────────────────────────────────────
def new_menus(con, ids, ce, th=True) -> dict:
    from routers.sales import _first_sold_map, _spike_name, _spike_kind
    from rules import non_food_sql
    t = "เมนูใหม่ครบ 28 วัน" if th else "New dishes at day 28"
    ph = ",".join("?" * len(ids))
    s = ce - _dt.timedelta(days=T_NEWMENU_TO)
    # the candidates first (the cached first-sold map, one pass), then the daily rows of THOSE SKUs only - never the whole window
    fsm = _first_sold_map(con, ids)
    first: dict = {}
    for (bid, sid), d in fsm.items():
        if bid in ids:
            first[sid] = min(first.get(sid, d), d)
    cand = {sid: d for sid, d in first.items() if T_NEWMENU_FROM <= (ce - _dt.date.fromisoformat(d[:10])).days <= T_NEWMENU_TO}
    if not cand:
        return {"key": "newmenu", "title": t, "available": True, "level": "none", "rows": [],
                "line": ("ไม่มีเมนูใหม่ที่ถึงกำหนด 28 วันในช่วงนี้" if th else "no new dish at its 28-day mark")}
    meta = {r[0]: (r[1], r[2]) for r in con.execute(f"SELECT sku_id, sku_code, description FROM skus WHERE sku_id IN ({','.join('?' * len(cand))})"
                                                    f" AND NOT {non_food_sql('sku_code', 'description')}", list(cand))}
    if not meta:
        return {"key": "newmenu", "title": t, "available": True, "level": "none", "rows": [],
                "line": ("ไม่มีเมนูใหม่ที่ถึงกำหนด 28 วันในช่วงนี้" if th else "no new dish at its 28-day mark")}
    sph = ",".join("?" * len(meta))
    sold = con.execute(f"""
        SELECT d.sku_id, d.branch_id, d.day, d.qty FROM sales_sku_daily d
        WHERE d.branch_id IN ({ph}) AND d.day BETWEEN ? AND ? AND d.sku_id IN ({sph})""",
        list(ids) + [s.isoformat(), ce.isoformat()] + list(meta)).fetchall()
    if not sold:
        return _unavailable("newmenu", t, "ไม่มีข้อมูลขายรายเมนู" if th else "no daily sku data")
    agg: dict = {}
    for sid, bid, day, q in sold:
        if sid not in cand or sid not in meta:
            continue
        a = agg.setdefault(sid, {"first": 0.0, "last": 0.0, "br": set(), "q": 0.0})
        f0 = _dt.date.fromisoformat(cand[sid][:10]); dd = _dt.date.fromisoformat(day[:10])
        if (dd - f0).days < T_NEWMENU_WEEK:          # its first week vs its latest week: no overlap even at 21 days old
            a["first"] += q or 0
        if (ce - dd).days < T_NEWMENU_WEEK:
            a["last"] += q or 0
        a["br"].add(bid); a["q"] += q or 0
    out = []
    for sid, a in agg.items():
        code, desc = meta[sid]
        age = (ce - _dt.date.fromisoformat(cand[sid][:10])).days
        fpd, lpd = a["first"] / float(T_NEWMENU_WEEK), a["last"] / float(T_NEWMENU_WEEK)
        import re as _re
        name = _re.sub(r"^\((?:A|B|BF|AL|DL|TW|OT)\)\s*", "", _spike_name(desc, code) or "")
        out.append({"menu": name, "kind": _spike_kind(desc), "age_days": age, "first_sold": cand[sid][:10],
                    "branches": len(a["br"]), "per_day": round(lpd, 1), "first_per_day": round(fpd, 1),
                    "trend_pct": round((lpd / fpd - 1) * 100) if fpd > 0 else None, "plates": round(a["q"])})
    out.sort(key=lambda r: (-(r["per_day"] or 0), r["menu"]))
    if not out:
        return {"key": "newmenu", "title": t, "available": True, "level": "none", "rows": [], "line": ("ไม่มีเมนูใหม่ที่ถึงกำหนด 28 วันในช่วงนี้" if th else "no new dish at its 28-day mark")}
    falling = [r for r in out if r["trend_pct"] is not None and r["trend_pct"] <= -30]
    names = " · ".join(f"{r['menu']} {r['per_day']}/{'วัน' if th else 'day'}" + (f" ({'+' if r['trend_pct'] > 0 else ''}{r['trend_pct']}%)" if r["trend_pct"] is not None else "") for r in out[:3])
    return {"key": "newmenu", "title": t, "available": True, "level": "watch" if falling else "ok", "rows": out[:5], "count": len(out),
            "falling": len(falling),
            "line": (f"{len(out)} เมนูถึงกำหนดตัดสินใจ (เก็บ / เลิก): {names}" + (f" — {len(falling)} เมนูขายตกเกิน 30% จากสัปดาห์แรก" if falling else "") if th
                     else f"{len(out)} dishes at the keep / drop decision: {names}" + (f" - {len(falling)} fell over 30% since their first week" if falling else ""))}


# ── 12 · cancelled bills and reprinted bills per branch ─────────────────────────────────────────────────────────────
# 2026-10-31 (owner): the cancelled bills and the bill denominator share ONE window. The count comes from
# pos_cancel_bill_detail (one row per voided bill, the sync keeps the 20,000 most recent ~ 11 months) by date(sale_date);
# only a window older than that table falls back to the monthly pos_cancel_bill (whole month, "source": "month").
# Three windows are answered at once - เมื่อวาน (the last finished day, rules.latest_settled_day, the hero's anchor),
# 7 วัน (the 7 finished days ending there) and เดือนนี้ (cs..ce) - the phone's chips switch between them client-side; the
# top-level fields stay the month window. The bands are per-window rates; the day window needs T_VOID_MIN_BILLS bills.
T_VOID_MIN_BILLS = 20            # small-sample guard of the เมื่อวาน window
_DETAIL_KEEP = 20000             # sync_mssql keeps this many voided bills in pos_cancel_bill_detail
_MON_TH = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
_MON_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def range_label(s, e, th=True) -> str:
    """'1–30 ต.ค.' / '31 ต.ค.' / '28 ก.ย. – 4 ต.ค.' (no year: the card's month is on the page)."""
    M = _MON_TH if th else _MON_EN
    if s == e:
        return f"{e.day} {M[e.month - 1]}"
    if (s.year, s.month) == (e.year, e.month):
        return f"{s.day}–{e.day} {M[e.month - 1]}"
    return f"{s.day} {M[s.month - 1]} – {e.day} {M[e.month - 1]}"


def _detail_cover(con):
    """(first day held, whole history?) of pos_cancel_bill_detail, or None when the table is missing or empty."""
    try:
        lo, n = con.execute("SELECT MIN(date(sale_date)), COUNT(*) FROM pos_cancel_bill_detail").fetchone()
    except Exception:
        return None
    return (lo, n < _DETAIL_KEEP) if lo else None


def _void_window(con, ids, s, e, where, params, th, cover, month_ok, min_bills=0) -> dict:
    """One window's verdict: per-branch cancelled bills % of bills (numerator and denominator over s..e) + reprints."""
    ph = ",".join("?" * len(ids))
    lo = cover[0] if cover else None
    # the oldest day the detail table holds may be cut part-way (it keeps the newest N bills): it covers s only from the next day,
    # unless the table holds the whole history
    covered = bool(cover) and (lo < s.isoformat() or (cover[1] and lo <= s.isoformat()))
    if covered:
        source = "detail"
        canc = {r[0]: (r[1] or 0, r[2] or 0) for r in con.execute(
            f"SELECT branch_id, COUNT(*), SUM(amount) FROM pos_cancel_bill_detail WHERE branch_id IN ({ph}) "
            f"AND date(sale_date) BETWEEN ? AND ? GROUP BY branch_id", list(ids) + [s.isoformat(), e.isoformat()])}
    elif month_ok:
        source = "month"
        canc = {r[0]: (r[1] or 0, r[2] or 0) for r in con.execute(
            f"SELECT branch_id, SUM(bills), SUM(amount) FROM pos_cancel_bill WHERE period_month=? AND branch_id IN ({ph}) GROUP BY branch_id",
            [s.strftime("%Y-%m")] + list(ids))}
    else:
        source, canc = "none", {}
    bills = _bills(con, ids, s, e)
    rows, few = [], 0
    for bid, (cb, amt) in canc.items():
        tot = bills.get(bid, 0) + cb
        if tot <= 0 or cb <= 0:
            continue
        if tot < min_bills:                         # a handful of bills says nothing - no verdict
            few += 1
            continue
        rate = cb / tot * 100
        rows.append({"branch": ids[bid], "bills": cb, "amount": round(amt), "rate_pct": round(rate, 2),
                     "level": "fix" if rate >= T_VOID_FIX else "watch" if rate >= T_VOID_WATCH else "ok"})
    rep = [{"branch": r[0], "bills": r[1], "baht": r[2] or 0} for r in con.execute(f"""
        SELECT b.name, COUNT(*), ROUND(COALESCE(SUM(st.discount),0))
        FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
        WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e AND st.discount>0 AND COALESCE(st.print_times,0)>1
        GROUP BY b.name ORDER BY COUNT(*) DESC""", {**params, "s": s.isoformat(), "e": e.isoformat()})]
    n_bills = sum(bills.values()) + sum(cb for cb, _ in canc.values())
    out = {"range": [s.isoformat(), e.isoformat()], "label": range_label(s, e, th), "source": source, "bills": n_bills}
    if min_bills and 0 < n_bills < min_bills and source != "none":
        return {**out, "available": True, "level": "none", "rows": [], "reprints": rep[:3], "few": True,
                "line": (f"บิลน้อย ({n_bills:,} บิล) — ยังสรุปไม่ได้" if th else f"too few bills ({n_bills:,}) - no verdict")}
    if not rows and not rep:
        if n_bills and source == "detail":          # the detail table covers the window: none voided is a real zero
            return {**out, "available": True, "level": "ok", "rows": [], "reprints": [],
                    "line": "ไม่มีบิลยกเลิกและไม่มีบิลพิมพ์ซ้ำ" if th else "no voids and no reprints"}
        why = ("ยังไม่มีข้อมูลบิลยกเลิกรายวันของช่วงนี้" if th else "no daily cancel data for this window") if source == "none" \
            else ("ยังไม่มีข้อมูลบิลยกเลิกของช่วงนี้" if th else "no cancel data in this window")
        return {**out, "available": False, "level": "none", "rows": [], "reprints": [], "line": why}
    bad = sorted((r for r in rows if r["level"] != "ok"), key=lambda r: (LEVELS[r["level"]], -r["rate_pct"]))
    level = bad[0]["level"] if bad else ("watch" if rep else "ok")
    parts = []
    if bad:
        parts.append(("ยกเลิกบิล: " if th else "voids: ") + " · ".join(f"{r['branch']} {r['rate_pct']}% ({r['bills']} {'บิล' if th else 'bills'} ฿{r['amount']:,.0f})" for r in bad[:3]))
    if rep:
        parts.append(("พิมพ์ซ้ำ: " if th else "reprints: ") + " · ".join(f"{r['branch']} {r['bills']} {'บิล' if th else 'bills'}" for r in rep[:3]))
    line = " | ".join(parts) if parts else ("ทุกสาขาต่ำกว่า 0.5% และไม่มีบิลพิมพ์ซ้ำ" if th else "every branch under 0.5% and no reprints")
    if bad or rep:
        line += (" — ตรวจที่สาขา" if th else " - audit at the branch")
    if few:
        line += (f" (บิลน้อย {few} สาขา ยังไม่สรุป)" if th else f" ({few} branches with too few bills, no verdict)")
    return {**out, "available": True, "level": level, "rows": bad[:3], "reprints": rep[:3], "line": line}


def voids(con, ids, cs, ce, where, params, th=True) -> dict:
    t = "บิลยกเลิก / พิมพ์ซ้ำ" if th else "Voids / reprints"
    cover = _detail_cover(con)
    month = _void_window(con, ids, cs, ce, where, params, th, cover, month_ok=True)
    windows = {"month": month}
    try:
        from rules import latest_settled_day
        d = latest_settled_day(con, where=where.replace("st.", "t."), params=params)["day"]
    except Exception:                               # pragma: no cover - the month still answers
        d = None
    if d:
        day = min(_dt.date.fromisoformat(d[:10]), ce)
        windows["day"] = _void_window(con, ids, day, day, where, params, th, cover, month_ok=False, min_bills=T_VOID_MIN_BILLS)
        windows["week"] = _void_window(con, ids, day - _dt.timedelta(days=6), day, where, params, th, cover, month_ok=False)
    common = {"bands": {"watch": T_VOID_WATCH, "fix": T_VOID_FIX}, "range": month["range"], "label": month["label"],
              "source": month["source"], "min_bills_day": T_VOID_MIN_BILLS, "windows": windows}
    if not month["available"]:
        return {**_unavailable("voids", t, "ยังไม่มีข้อมูลบิลยกเลิกของเดือนนี้" if th else "no cancel data this month"), **common}
    return {"key": "voids", "title": t, "available": True, "level": month["level"], "rows": month["rows"], "reprints": month["reprints"],
            "line": month["line"], "bills": month["bills"], **common}


# ── 13 · bank-vs-POS sign-off of the month to close ─────────────────────────────────────────────────────────────────
def recon(con, ids, cs, ce, th=True) -> dict:
    from calendar import month_abbr
    t = "กระทบยอดเงินเข้า" if th else "Bank vs POS"
    mon = (cs - _dt.timedelta(days=1)).strftime("%Y-%m")
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='recon_sessions'").fetchone():
        return _unavailable("recon", t, "ระบบกระทบยอดยังไม่เชื่อม" if th else "recon tables absent")
    ses = {r[0]: (r[1], r[2]) for r in con.execute("SELECT branch_id, status, session_id FROM recon_sessions WHERE period_month=?", (mon,))}
    var = {}
    if ses:
        ph = ",".join("?" * len(ses))
        var = {r[0]: r[1] or 0 for r in con.execute(
            f"SELECT session_id, SUM(ABS(variance)) FROM recon_category_result WHERE session_id IN ({ph}) AND status!='matched' GROUP BY session_id",
            [v[1] for v in ses.values()])}
    done, open_, none = [], [], []
    for bid, name in ids.items():
        st = ses.get(bid)
        if not st:
            none.append(name)
        elif st[0] in ("approved", "closed"):
            done.append(name)
        else:
            open_.append({"branch": name, "status": st[0], "variance": round(var.get(st[1], 0))})
    open_.sort(key=lambda r: -r["variance"])
    th_mon = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."][int(mon[5:7]) - 1]
    mlabel = th_mon if th else month_abbr[int(mon[5:7])]
    past_due = ce.day > RECON_DEADLINE_DAY and ce.strftime("%Y-%m") > mon
    level = "ok" if not open_ and not none else ("fix" if past_due else "watch")
    if level == "ok":
        line = (f"เดือน {mlabel}: ปิดครบ {len(done)}/{len(ids)} สาขา" if th else f"{mlabel}: all {len(done)} branches signed off")
    else:
        parts = [f"ปิดแล้ว {len(done)}/{len(ids)}" if th else f"signed off {len(done)}/{len(ids)}"]
        if open_:
            parts.append((f"ยังไม่ตรง {len(open_)} (฿{sum(r['variance'] for r in open_):,.0f}): " if th else f"open {len(open_)} (฿{sum(r['variance'] for r in open_):,.0f}): ")
                         + " · ".join(f"{r['branch']} ฿{r['variance']:,.0f}" for r in open_[:3]))
        if none:
            parts.append((f"ยังไม่เริ่ม {len(none)}" if th else f"not started {len(none)}") + (f": {' · '.join(none[:3])}" if len(none) <= 3 else ""))
        line = (f"เดือน {mlabel}: " if th else f"{mlabel}: ") + " · ".join(parts) + (f" — กำหนดวันที่ {RECON_DEADLINE_DAY}" if th else f" - due the {RECON_DEADLINE_DAY}th")
    return {"key": "recon", "title": t, "available": True, "level": level, "month": mon, "done": len(done), "total": len(ids),
            "rows": open_[:3], "not_started": none, "past_due": past_due, "line": line}


# ── 14 · the premium tier's share of guests per branch ───────────────────────────────────────────────────────────────
def tier_mix(con, ids, cs, ce, ps, pe, has_prev, th=True) -> dict:
    from rules import GRAND_MENU_FROM, tier_label
    t = "กลุ่มราคาบุฟเฟ่ต์" if th else "Buffet tier mix"
    if not has_prev or not ps:
        return _unavailable("tiermix", t, "ไม่มีช่วงเทียบ" if th else "no comparison period")
    if ps.isoformat() < GRAND_MENU_FROM <= cs.isoformat():
        return _unavailable("tiermix", t, "ช่วงเทียบอยู่ก่อนเมนูใหม่ 1 ก.ย." if th else "comparison period precedes the Grand Menu")
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='menu_fact_total'").fetchone():
        return _unavailable("tiermix", t, "ยังไม่มีข้อมูลกลุ่มราคา" if th else "no tier data")
    ph = ",".join("?" * len(ids))

    def tsum(s, e):
        out: dict = {}
        for bid, tier, px, rv in con.execute(f"""SELECT branch_id, tier, SUM(pax), SUM(rev) FROM menu_fact_total
                                                WHERE day BETWEEN ? AND ? AND branch_id IN ({ph}) GROUP BY branch_id, tier""",
                                             [s.isoformat(), e.isoformat()] + list(ids)):
            out.setdefault(bid, {})[str(tier or "promo")] = (px or 0.0, rv or 0.0)
        return out
    C, P = tsum(cs, ce), tsum(ps, pe)
    if not C or not P:
        return _unavailable("tiermix", t, "ยังไม่มีข้อมูลกลุ่มราคาของสองช่วง" if th else "tier data missing for one period")
    chain: dict = {}
    for per in C.values():
        for tier, (px, rv) in per.items():
            c = chain.setdefault(tier, [0.0, 0.0]); c[0] += px; c[1] += rv
    real = {k: v for k, v in chain.items() if k not in ("promo", "child") and v[0] > 0}
    if not real:
        return _unavailable("tiermix", t, "ไม่มีกลุ่มราคาหลัก" if th else "no priced tier")
    prem = max(real, key=lambda k: real[k][1] / real[k][0])
    rows = []
    for bid, per in C.items():
        pc = sum(v[0] for v in per.values()); pp = sum(v[0] for v in P.get(bid, {}).values())
        if pc < 50 or pp < 50:
            continue
        sc = per.get(prem, (0, 0))[0] / pc * 100; sp = P.get(bid, {}).get(prem, (0, 0))[0] / pp * 100
        rows.append({"branch": ids[bid], "share_pct": round(sc, 1), "share_prev_pct": round(sp, 1), "delta_pp": round(sc - sp, 1),
                     "pax_lost": round((sp - sc) / 100 * pc)})
    drop = sorted((r for r in rows if r["delta_pp"] <= -T_TIER_DROP_PP), key=lambda r: -r["pax_lost"])
    lbl = tier_label(prem, th)
    if not drop:
        return {"key": "tiermix", "title": t, "available": True, "level": "ok", "tier": prem, "tier_label": lbl, "rows": [],
                "line": (f"สัดส่วน {lbl} ไม่ลดเกิน {T_TIER_DROP_PP:g} จุดที่สาขาใด" if th else f"{lbl} share down {T_TIER_DROP_PP:g} points at no branch")}
    names = " · ".join(f"{r['branch']} {r['share_prev_pct']:.0f}%→{r['share_pct']:.0f}% (−{abs(r['delta_pp']):.0f} {'จุด' if th else 'pts'})" for r in drop[:3])
    return {"key": "tiermix", "title": t, "available": True, "level": "watch", "tier": prem, "tier_label": lbl, "rows": drop[:3], "over_count": len(drop),
            "line": (f"สัดส่วนลูกค้า {lbl} ลด {len(drop)} สาขา: {names} — ดันการเสนอขายที่สาขา" if th
                     else f"{lbl} share of guests down at {len(drop)} branches: {names} - push upselling")}


def build(con, *, cs, ce, ps, pe, has_prev, where, params, branch, group, th=True) -> dict:
    """The whole block; one failed item is reported as unavailable with its reason, the rest still answer."""
    ids = _scope_ids(con, branch, group)
    items = []

    def run(fn, key, title, *a, **k):
        try:
            items.append(fn(*a, **k))
        except Exception as e:                      # pragma: no cover - degraded, not broken
            print(f"[mgt-watch] {key} skipped: {e}")
            items.append(_unavailable(key, title, ("ยังอ่านไม่ได้" if th else "unavailable") + f" ({type(e).__name__})"))
    run(portion, "portion", "สัดส่วนการตัก" if th else "Portioning", con, ids, cs, ce, th)
    run(purchases, "purchases", "ยอดซื้อเทียบยอดขาย" if th else "Purchases vs sales", con, ids, cs, ce, branch, group, th)
    run(forced_gaps, "forced", "วัตถุดิบบังคับสั่ง" if th else "Monitored ingredients", con, ids, ce, th)
    run(new_menus, "newmenu", "เมนูใหม่ครบ 28 วัน" if th else "New dishes at day 28", con, ids, ce, th)
    run(voids, "voids", "บิลยกเลิก / พิมพ์ซ้ำ" if th else "Voids / reprints", con, ids, cs, ce, where, params, th)
    run(recon, "recon", "กระทบยอดเงินเข้า" if th else "Bank vs POS", con, ids, cs, ce, th)
    run(tier_mix, "tiermix", "กลุ่มราคาบุฟเฟ่ต์" if th else "Buffet tier mix", con, ids, cs, ce, ps, pe, has_prev, th)
    items.sort(key=lambda it: (LEVELS.get(it.get("level"), 9), not it.get("available")))
    return {"items": items, "fix": sum(1 for it in items if it.get("level") == "fix"), "watch": sum(1 for it in items if it.get("level") == "watch"),
            "range": [cs.isoformat(), ce.isoformat()]}
