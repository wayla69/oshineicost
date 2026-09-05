# -*- coding: utf-8 -*-
"""📊 เสียงลูกค้า × ข้อมูลธุรกิจ — E2 of the expansion plan. The one thing a
generic review tool cannot do: read the voices against the till.
  · "รอนาน" (and บริการ, ของหมด) against how busy the branch was that day — a
    complaint on a day 20 % busier than the branch's usual is a staffing /
    prep problem at peak; on a quiet day it is a standard-of-work problem.
  · the dishes guests name against what those dishes sold and cost (sale_bill_item):
    a dish that is complained about AND sells in the top fifth is the one to fix
    first; a praised top seller is the one to protect.
  · every branch's voices against its revenue and guest trend, its discount
    share (a service problem and a leak often travel together) and its spend
    per head (what "ราคา/ความคุ้ม" complaints are measured against).
  GET /api/feedback/business?month_from=&month_to=[&branch=][&today=]
Branch-scoped logins are confined to their branch. Results are cached for
CACHE_SECONDS per window because the bill-item join is the heaviest read in
the module.
"""
import datetime
import json
import time
from typing import Optional

from fastapi import APIRouter, Query, Request

from feedback_host import branch_rows, get_db, sales_available, scope_branch
from feedback_nlp import clean_menu_name
from routers.feedback import _ROW_COLS, _ensure, _thai_today

router = APIRouter()

BUSY_FACTOR = 1.2                 # a day with ≥ 120 % of the branch's median bills is "busy"
LOAD_THEMES = ("รอนาน", "บริการ", "ปริมาณ/ของหมด")
TOP_SELLER_PCT = 20               # a dish in the top fifth by quantity is a top seller
MIN_QTY = 20                      # below this a dish's cost per unit is noise
CACHE_SECONDS = 900
_CACHE: dict = {}


def _d(s):
    return datetime.date.fromisoformat(s[:10])


def _window(month_from: Optional[str], month_to: Optional[str], today: str):
    """[from, to] dates for the tab's month range (default: this month and the two before)."""
    t = _d(today)
    if month_to:
        y, m = int(month_to[:4]), int(month_to[5:7])
        last = (datetime.date(y + (m // 12), (m % 12) + 1, 1) - datetime.timedelta(days=1))
        to = min(last, t)
    else:
        to = t
    if month_from:
        frm = datetime.date(int(month_from[:4]), int(month_from[5:7]), 1)
    else:
        frm = datetime.date(to.year, to.month, 1) - datetime.timedelta(days=62)
        frm = datetime.date(frm.year, frm.month, 1)
    if frm > to:
        frm = datetime.date(to.year, to.month, 1)
    return frm.isoformat(), to.isoformat()


def _prev_window(d_from: str, d_to: str):
    span = (_d(d_to) - _d(d_from)).days + 1
    pe = _d(d_from) - datetime.timedelta(days=1)
    return (pe - datetime.timedelta(days=span - 1)).isoformat(), pe.isoformat()


def _daily(con, d_from: str, d_to: str, bids=None) -> dict:
    """{(branch_id, day): {bills, pax, rev, disc}} from the bill table."""
    w, p = ["sold_at >= ?", "sold_at < ?"], [d_from, (_d(d_to) + datetime.timedelta(days=1)).isoformat()]
    if bids:
        w.append(f"branch_id IN ({','.join('?' * len(bids))})"); p.extend(bids)
    out = {}
    for r in con.execute(f"SELECT branch_id, substr(sold_at,1,10) AS day, COUNT(*) AS bills, "
                         f"COALESCE(SUM(customer_count),0) AS pax, COALESCE(SUM(total_inc_vat),0) AS rev, COALESCE(SUM(discount),0) AS disc "
                         f"FROM sales_transaction WHERE {' AND '.join(w)} GROUP BY 1, 2", p):
        out[(r["branch_id"], r["day"])] = {"bills": r["bills"], "pax": r["pax"] or 0, "rev": r["rev"] or 0.0, "disc": r["disc"] or 0.0}
    return out


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0


def _sales_by_dish(con, d_from: str, d_to: str, bids=None) -> dict:
    """{clean dish name: {qty, rev, cost, kinds}} over the window (sale_bill_item × bills × skus)."""
    if not sales_available(con):
        return {}
    w, p = ["t.sold_at >= ?", "t.sold_at < ?"], [d_from, (_d(d_to) + datetime.timedelta(days=1)).isoformat()]
    if bids:
        w.append(f"t.branch_id IN ({','.join('?' * len(bids))})"); p.extend(bids)
    out = {}
    for r in con.execute(f"SELECT s.description AS d, SUM(i.qty) AS qty, COALESCE(SUM(i.rev),0) AS rev, COALESCE(SUM(i.bom_cost),0) AS cost "
                         f"FROM sale_bill_item i JOIN sales_transaction t ON t.receipt_no = i.receipt_no "
                         f"JOIN skus s ON s.sku_id = i.sku_id WHERE {' AND '.join(w)} GROUP BY s.description", p):
        name = clean_menu_name(r["d"] or "")
        if not name:
            continue
        o = out.setdefault(name, {"qty": 0.0, "rev": 0.0, "cost": 0.0, "kinds": set()})
        o["qty"] += float(r["qty"] or 0); o["rev"] += float(r["rev"] or 0); o["cost"] += float(r["cost"] or 0)
        o["kinds"].add("B" if (r["d"] or "").startswith("(B)") else ("A" if (r["d"] or "").startswith("(A)") else "-"))
    return out


def compute_business(con, month_from=None, month_to=None, branch=None, today=None) -> dict:
    _ensure(con)
    today = today or _thai_today()
    d_from, d_to = _window(month_from, month_to, today)
    p_from, p_to = _prev_window(d_from, d_to)
    key = (d_from, d_to, branch or "")
    hit = _CACHE.get(key)
    if hit and hit[0] > time.time() - CACHE_SECONDS:
        return hit[1]
    names = {b["branch_id"]: b["name"] for b in branch_rows(con)}
    ids = {v: k for k, v in names.items()}
    bids = [ids[branch]] if branch and branch in ids else None
    if branch and branch not in ids:
        bids = [-1]
    w, p = ["rated_at >= ?", "rated_at <= ?"], [d_from, d_to]
    if branch:
        w.append("branch_name = ?"); p.append(branch)
    voices = [dict(r) for r in con.execute(f"SELECT {_ROW_COLS} FROM customer_feedback WHERE {' AND '.join(w)}", p)]
    for v in voices:
        v["themes"] = json.loads(v["themes"] or "[]"); v["menus"] = json.loads(v["menus"] or "[]")
    if sales_available(con):
        daily, daily_prev = _daily(con, d_from, d_to, bids), _daily(con, p_from, p_to, bids)
    else:                                       # a host without bill tables: the voices alone
        daily, daily_prev = {}, {}

    # ── load: complaints against how busy the day was ──────────────────────
    per_branch_bills = {}
    for (bid, day), d in daily.items():
        per_branch_bills.setdefault(bid, []).append(d["bills"])
    medians = {bid: _median(v) for bid, v in per_branch_bills.items()}
    busy = {k: (d["bills"] >= BUSY_FACTOR * medians.get(k[0], 0) and medians.get(k[0], 0) > 0) for k, d in daily.items()}

    def _verdict(n, busy_n):
        if n < 3:
            return "ยังน้อยเกินตัดสิน"
        share = busy_n / n
        if share >= 0.6:
            return "เกิดตอนแน่น → กำลังคน/เตรียมของช่วงพีค"
        if share <= 0.3:
            return "เกิดตอนไม่แน่น → มาตรฐานงาน/ขั้นตอน"
        return "ปนกัน"

    theme_days, load_branches = [], {}
    for th in LOAD_THEMES:
        n = busy_n = matched = 0
        for v in voices:
            if th not in v["themes"]:
                continue
            n += 1
            bid = ids.get(v["branch_name"] or "")
            k = (bid, (v["rated_at"] or "")[:10])
            if k in daily:
                matched += 1
                if busy.get(k):
                    busy_n += 1
                if th == "รอนาน":
                    lb = load_branches.setdefault(v["branch_name"], {"branch": v["branch_name"], "n": 0, "busy": 0})
                    lb["n"] += 1; lb["busy"] += 1 if busy.get(k) else 0
        theme_days.append({"theme": th, "n": n, "matched": matched, "on_busy_days": busy_n,
                           "busy_share": round(100 * busy_n / matched) if matched else None, "verdict": _verdict(matched, busy_n)})
    for lb in load_branches.values():
        lb["busy_share"] = round(100 * lb["busy"] / lb["n"]) if lb["n"] else None
        lb["median_bills"] = medians.get(ids.get(lb["branch"]), 0)
        lb["verdict"] = _verdict(lb["n"], lb["busy"])
    busy_days_total = sum(1 for x in busy.values() if x)

    # ── menus: what guests name against what it sold and cost ──────────────
    mentions = {}
    for v in voices:
        for m in v["menus"]:
            o = mentions.setdefault(m, {"menu": m, "n": 0, "neg": 0, "pos": 0})
            o["n"] += 1; o["neg"] += v["sentiment"] == "neg"; o["pos"] += v["sentiment"] == "pos"
    sales = _sales_by_dish(con, d_from, d_to, bids)
    ranked = sorted(sales.items(), key=lambda kv: -kv[1]["qty"])
    rank_of = {name: i + 1 for i, (name, _) in enumerate(ranked)}
    n_sold = len(ranked)
    unit_costs = sorted(o["cost"] / o["qty"] for o in sales.values() if o["qty"] >= MIN_QTY and o["cost"] > 0)
    p80 = unit_costs[int(len(unit_costs) * 0.8)] if unit_costs else None
    menus = []
    for m, o in mentions.items():
        s = sales.get(m)
        rank = rank_of.get(m)
        rank_pct = round(100 * rank / n_sold) if rank and n_sold else None
        cpu = round(s["cost"] / s["qty"], 2) if s and s["qty"] else None
        signals = []
        if o["neg"] >= 2 and rank_pct is not None and rank_pct <= TOP_SELLER_PCT:
            signals.append("ขายดีแต่ถูกบ่น — แก้ก่อน")
        if o["pos"] >= 2 and rank_pct is not None and rank_pct <= TOP_SELLER_PCT:
            signals.append("ขายดีและได้คำชม — รักษาไว้")
        if cpu is not None and p80 is not None and cpu >= p80 and o["neg"] >= 1:
            signals.append("ต้นทุน/หน่วยสูง (top 20%) และมีเสียงลบ — ทบทวนสเปก")
        if o["neg"] >= 2 and s is None:
            signals.append("ไม่พบยอดขายของชื่อนี้ — ชื่อเมนูในระบบอาจต่างกัน")
        menus.append({"menu": m, "mentions": o["n"], "neg": o["neg"], "pos": o["pos"],
                      "qty": round(s["qty"]) if s else None, "rev": round(s["rev"]) if s else None,
                      "cost_per_unit": cpu, "rank": rank, "rank_pct": rank_pct,
                      "kind": "".join(sorted(s["kinds"])) if s else "", "signals": signals})
    menus.sort(key=lambda x: (-x["neg"], -x["mentions"]))

    # ── branches: voices against revenue, guests, discounts, spend per head ──
    def _sum(dd, bid=None):
        agg = {"bills": 0, "pax": 0, "rev": 0.0, "disc": 0.0}
        for (b, _day), d in dd.items():
            if bid is None or b == bid:
                for k in agg:
                    agg[k] += d[k]
        return agg
    chain_cur = _sum(daily)
    chain_disc = round(100 * chain_cur["disc"] / (chain_cur["rev"] + chain_cur["disc"]), 2) if (chain_cur["rev"] + chain_cur["disc"]) else None
    by_branch = {}
    for v in voices:
        b = by_branch.setdefault(v["branch_name"] or "", {"n": 0, "neg": 0, "ratings": [], "price_neg": 0, "wait_neg": 0})
        b["n"] += 1; b["neg"] += v["sentiment"] == "neg"
        if v["rating"] is not None:
            b["ratings"].append(float(v["rating"]))
        if v["sentiment"] == "neg" and "ราคา/ความคุ้ม" in v["themes"]:
            b["price_neg"] += 1
        if v["sentiment"] == "neg" and "รอนาน" in v["themes"]:
            b["wait_neg"] += 1
    heads = []
    for bid, name in names.items():
        if bids and bid not in bids:
            continue
        cur, prev = _sum(daily, bid), _sum(daily_prev, bid)
        if not cur["bills"] and name not in by_branch:
            continue
        heads.append((name, cur["rev"] / cur["pax"] if cur["pax"] else None))
    med_head = _median([h for _, h in heads if h])
    # the discount yardstick is the median branch, not the chain total (one big branch would set its own bar)
    disc_shares = {}
    for bid, name in names.items():
        if bids and bid not in bids:
            continue
        c = _sum(daily, bid)
        if c["rev"] + c["disc"]:
            disc_shares[name] = 100 * c["disc"] / (c["rev"] + c["disc"])
    branches = []
    for bid, name in names.items():
        if bids and bid not in bids:
            continue
        cur, prev = _sum(daily, bid), _sum(daily_prev, bid)
        vb = by_branch.get(name)
        if not cur["bills"] and not vb:
            continue
        neg_pct = round(100 * vb["neg"] / vb["n"], 1) if vb and vb["n"] else None
        disc_share = round(100 * cur["disc"] / (cur["rev"] + cur["disc"]), 2) if (cur["rev"] + cur["disc"]) else None
        head = round(cur["rev"] / cur["pax"], 0) if cur["pax"] else None
        rev_delta = round(100 * (cur["rev"] - prev["rev"]) / prev["rev"], 1) if prev["rev"] else None
        pax_delta = round(100 * (cur["pax"] - prev["pax"]) / prev["pax"], 1) if prev["pax"] else None
        signals = []
        if neg_pct is not None and vb["n"] >= 3 and neg_pct >= 30 and rev_delta is not None and rev_delta <= -5:
            signals.append("เสียงลบสูงและยอดขายลด — ลูกค้ากำลังหาย")
        others = _median([v for k, v in disc_shares.items() if k != name]) if len(disc_shares) > 1 else None
        if disc_share is not None and others is not None and disc_share >= 1.5 * others and disc_share >= 3:
            signals.append("ส่วนลดสูงกว่าเครือ 1.5 เท่า — ดูคู่กับเรดาร์จุดรั่ว")
        if vb and vb["price_neg"] >= 2 and head and med_head and head > med_head:
            signals.append("บ่นเรื่องราคา และยอด/หัวสูงกว่าค่ากลาง — ทบทวนความคุ้มของ tier")
        if vb and vb["wait_neg"] >= 3 and pax_delta is not None and pax_delta >= 10:
            signals.append("รอนานเพิ่มพร้อมลูกค้าเพิ่ม — กำลังคนไม่ทันยอด")
        branches.append({"branch": name, "voices": vb["n"] if vb else 0, "neg_pct": neg_pct,
                         "avg_rating": round(sum(vb["ratings"]) / len(vb["ratings"]), 2) if vb and vb["ratings"] else None,
                         "rev": round(cur["rev"]), "rev_delta_pct": rev_delta, "pax": cur["pax"], "pax_delta_pct": pax_delta,
                         "disc_share": disc_share, "spend_head": head, "signals": signals})
    branches.sort(key=lambda b: (-(len(b["signals"])), -(b["neg_pct"] or 0), -b["rev"]))

    # ── the reading ─────────────────────────────────────────────────────────
    notes = []
    tw = next((t for t in theme_days if t["theme"] == "รอนาน"), None)
    if tw and tw["matched"] >= 3:
        notes.append(f"\"รอนาน\" {tw['matched']} เสียง เกิดในวันที่แน่นกว่าปกติ {tw['busy_share']}% — {tw['verdict']}")
    hot = [m for m in menus if any("ขายดีแต่ถูกบ่น" in s for s in m["signals"])]
    if hot:
        notes.append("เมนูขายดีที่ถูกบ่น: " + ", ".join(f"{m['menu']} (ลบ {m['neg']}, อันดับขาย {m['rank']})" for m in hot[:3]) + " — ส่งเข้า ⚡ปรับเมนู")
    flagged = [b for b in branches if b["signals"]]
    if flagged:
        notes.append("สาขาที่มีสัญญาณซ้อน: " + " · ".join(f"{b['branch']} ({b['signals'][0]})" for b in flagged[:3]))
    if not notes:
        notes.append("ยังไม่มีสัญญาณที่ซ้อนกันระหว่างเสียงลูกค้ากับข้อมูลขาย — ต้องการเสียงราว 30 เสียง/สาขา/เดือน จึงจะอ่านได้ชัด")
    out = {"range": [d_from, d_to], "prev_range": [p_from, p_to], "branch": branch, "voices": len(voices),
           "load": {"busy_factor": BUSY_FACTOR, "busy_days": busy_days_total, "days": len(daily), "themes": theme_days,
                    "branches": sorted(load_branches.values(), key=lambda x: -x["n"])},
           "menus": menus[:30], "dishes_sold": n_sold, "cost_p80": round(p80, 2) if p80 else None,
           "branches": branches, "chain": {"rev": round(chain_cur["rev"]), "pax": chain_cur["pax"], "disc_share": chain_disc,
                                           "disc_share_median": round(_median(list(disc_shares.values())), 2) if disc_shares else None,
                                           "spend_head_median": round(med_head) if med_head else None},
           "notes": notes}
    _CACHE[key] = (time.time(), out)
    return out


@router.get("/api/feedback/business")
def feedback_business(request: Request, month_from: Optional[str] = Query(None), month_to: Optional[str] = Query(None),
                      branch: Optional[str] = Query(None), today: Optional[str] = Query(None), fresh: int = Query(0)):
    scope = scope_branch(request)
    if scope:
        branch = scope
    if fresh:
        _CACHE.clear()
    with get_db() as con:
        return compute_business(con, month_from, month_to, branch, today=(today or "")[:10] or None)
