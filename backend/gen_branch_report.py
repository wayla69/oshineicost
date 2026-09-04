#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Branch performance report — ONE branch, TWO months, benchmarked against the
whole chain in prose and figures (no all-branches table, by owner request).
Five A4 pages: cover + executive summary · performance · cost & margin ·
the branch's own menu cost drivers · findings + recommendations.

    python backend/gen_branch_report.py --branch ขอนแก่น --m1 2026-07 --m2 2026-08
        [--slug KhonKaen] [--gen-dt "4 กันยายน 2569"] [--db backend/oshinei.db]
        [--out docs] [--no-pdf]

→ docs/BRANCH_REPORT_<slug>_<m1>_<m2>.html (+ .pdf via Chrome headless).

Same money basis as the app (backend/rules.py): revenue = Σ subtotal_ex_vat of
sales_sku_monthly; COGS = qty × effective unit cost with buffet tier rows at 0
(cogs_expr); covers = Σ customer_count of sales_month_summary; cost/head =
COGS ÷ covers; food % = COGS ÷ revenue; GP/head = (revenue − COGS) ÷ covers.
Benchmark = every active branch, same month. Rewritten 2026-09-04 — the
2026-06 original was never committed and was lost with its worktree.
"""
import argparse
import datetime as dt
import html
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))
from rules import cogs_expr, is_tier_code  # noqa: E402

THAI_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
               "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


# ── helpers ──────────────────────────────────────────────────────────────────
def thai_month(period: str) -> str:
    y, m = period.split("-")
    return f"{THAI_MONTHS[int(m) - 1]} {int(y) + 543}"


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def b0(n) -> str:
    return f"{(n or 0):,.0f}"


def b1(n) -> str:
    return f"{(n or 0):,.1f}"


def pct(n, d=1) -> str:
    return "—" if n is None else f"{n:.{d}f}%"


def delta_pct(new, old):
    if not old:
        return None
    return (new - old) / old * 100.0


def signed_pct(n, d=1) -> str:
    return "—" if n is None else f"{n:+.{d}f}%"


def signed_baht(n) -> str:
    return "—" if n is None else f"{n:+,.0f}"


def find_db(explicit) -> Path:
    for cand in ([explicit] if explicit else []) + [HERE / "oshinei.db", HERE.parent / "backend" / "oshinei.db"]:
        if cand and Path(cand).exists():
            return Path(cand)
    sys.exit("no database found — pass --db")


# ── data ─────────────────────────────────────────────────────────────────────
def _ids_sql(ids):
    return "(" + ",".join(str(int(i)) for i in ids) + ")" if ids else "(-1)"


def scope_month(con, ids, period) -> dict:
    """Revenue / covers / bills / COGS for a set of branches in one month."""
    ph = _ids_sql(ids)
    rev = con.execute(f"SELECT COALESCE(SUM(subtotal_ex_vat),0) FROM sales_sku_monthly "
                      f"WHERE period_month=? AND branch_id IN {ph}", (period,)).fetchone()[0] or 0.0
    covers, bills = con.execute(
        f"SELECT COALESCE(SUM(customer_count),0), COALESCE(SUM(transaction_count),0) "
        f"FROM sales_month_summary WHERE period_month=? AND branch_id IN {ph}", (period,)).fetchone()
    cogs = con.execute(f"SELECT COALESCE(SUM({cogs_expr('ssm')}),0) FROM sales_sku_monthly ssm "
                       f"WHERE ssm.period_month=? AND ssm.branch_id IN {ph}", (period,)).fetchone()[0] or 0.0
    covers = int(covers or 0)
    return {
        "revenue": float(rev), "covers": covers, "bills": int(bills or 0), "cogs": float(cogs),
        "food_pct": (cogs / rev * 100.0) if rev else None,
        "cost_head": (cogs / covers) if covers else None,
        "gp_head": ((rev - cogs) / covers) if covers else None,
        "spend_head": (rev / covers) if covers else None,
    }


def menu_drivers(con, branch_id, m1, m2, covers1, covers2, top_n=10) -> list:
    """The branch's own menus ranked by COGS in m2, with the month-before figures."""
    rows = {}
    for period, key in ((m1, "m1"), (m2, "m2")):
        for r in con.execute(
                f"SELECT ssm.sku_id, sk.sku_code, sk.description, SUM(ssm.qty) qty, "
                f"       SUM({cogs_expr('ssm')}) cogs, SUM(ssm.subtotal_ex_vat) rev "
                f"FROM sales_sku_monthly ssm JOIN skus sk ON sk.sku_id = ssm.sku_id "
                f"WHERE ssm.branch_id=? AND ssm.period_month=? GROUP BY ssm.sku_id",
                (branch_id, period)):
            if is_tier_code(r["sku_code"] or ""):
                continue
            d = rows.setdefault(r["sku_id"], {"code": r["sku_code"], "name": r["description"] or r["sku_code"],
                                              "m1": {"qty": 0.0, "cogs": 0.0}, "m2": {"qty": 0.0, "cogs": 0.0}})
            d[key] = {"qty": float(r["qty"] or 0), "cogs": float(r["cogs"] or 0)}
    total2 = sum(d["m2"]["cogs"] for d in rows.values()) or 1.0
    out = []
    for d in rows.values():
        c1, c2 = d["m1"]["cogs"], d["m2"]["cogs"]
        out.append({
            "code": d["code"], "name": d["name"],
            "qty1": d["m1"]["qty"], "qty2": d["m2"]["qty"], "cogs1": c1, "cogs2": c2,
            "cost_head1": (c1 / covers1) if covers1 else None,
            "cost_head2": (c2 / covers2) if covers2 else None,
            "share2": c2 / total2 * 100.0,
            "d_cogs_pct": delta_pct(c2, c1),
        })
    out.sort(key=lambda x: -x["cogs2"])
    return out[:top_n]


def compute(con, branch_name: str, m1: str, m2: str) -> dict:
    con.row_factory = sqlite3.Row
    b = con.execute("SELECT branch_id, name, branch_type FROM branches WHERE name=?", (branch_name,)).fetchone()
    if not b:
        sys.exit(f"branch not found: {branch_name}")
    chain_ids = [r[0] for r in con.execute("SELECT branch_id FROM branches WHERE COALESCE(active,1)=1")]
    bid = b["branch_id"]
    br = {m: scope_month(con, [bid], m) for m in (m1, m2)}
    ch = {m: scope_month(con, chain_ids, m) for m in (m1, m2)}
    # rank by cost/head among active branches with covers in m2 (1 = most expensive)
    ranked = []
    for oid in chain_ids:
        s = scope_month(con, [oid], m2)
        if s["cost_head"] is not None and s["covers"] > 0:
            ranked.append((oid, s["cost_head"]))
    ranked.sort(key=lambda x: -x[1])
    rank = next((i + 1 for i, (oid, _) in enumerate(ranked) if oid == bid), None)
    drivers = menu_drivers(con, bid, m1, m2, br[m1]["covers"], br[m2]["covers"])
    return {
        "branch": {"id": bid, "name": b["name"], "type": b["branch_type"]},
        "m1": m1, "m2": m2, "branch_months": br, "chain_months": ch,
        "n_branches": len(chain_ids), "rank_cost_head": rank, "n_ranked": len(ranked),
        "share_rev": {m: (br[m]["revenue"] / ch[m]["revenue"] * 100.0 if ch[m]["revenue"] else None) for m in (m1, m2)},
        "drivers": drivers,
    }


# ── findings (rule-based Thai prose) ─────────────────────────────────────────
def findings(d: dict) -> dict:
    m1, m2 = d["m1"], d["m2"]
    b1_, b2_ = d["branch_months"][m1], d["branch_months"][m2]
    c1_, c2_ = d["chain_months"][m1], d["chain_months"][m2]
    name = d["branch"]["name"]
    find, reco = [], []

    if b2_["food_pct"] is not None and c2_["food_pct"] is not None:
        gap = b2_["food_pct"] - c2_["food_pct"]
        move_b = (b2_["food_pct"] - b1_["food_pct"]) if b1_["food_pct"] is not None else None
        move_c = (c2_["food_pct"] - c1_["food_pct"]) if c1_["food_pct"] is not None else None
        find.append(f"ต้นทุนอาหาร {name} เดือน{thai_month(m2)} อยู่ที่ {pct(b2_['food_pct'])} ของรายได้ "
                    f"(ทั้งเครือ {pct(c2_['food_pct'])} — {'สูงกว่า' if gap >= 0 else 'ต่ำกว่า'}เครือ {abs(gap):.1f} จุด)"
                    + (f" และ{'เพิ่มขึ้น' if move_b >= 0 else 'ลดลง'} {abs(move_b):.1f} จุดจากเดือนก่อน"
                       f" ขณะที่เครือ{'เพิ่ม' if (move_c or 0) >= 0 else 'ลด'} {abs(move_c or 0):.1f} จุด" if move_b is not None else ""))
        if gap > 2:
            reco.append("ทบทวนสูตร/ปริมาณต่อจานของเมนูต้นทุนสูงให้ตรงมาตรฐานเครือ — ส่วนต่างต้นทุนอาหารเกิน 2 จุดคือเงินที่หายไปทุกเดือน")
    if b2_["cost_head"] is not None and c2_["cost_head"] is not None:
        gap = b2_["cost_head"] - c2_["cost_head"]
        rank_txt = f" (อันดับ {d['rank_cost_head']} จาก {d['n_ranked']} สาขา เรียงจากแพงสุด)" if d["rank_cost_head"] else ""
        find.append(f"ต้นทุน/หัว ฿{b1(b2_['cost_head'])} เทียบเครือ ฿{b1(c2_['cost_head'])} "
                    f"({'+' if gap >= 0 else '−'}฿{abs(gap):,.0f}/หัว){rank_txt}"
                    + (f" · จาก ฿{b1(b1_['cost_head'])} เดือนก่อน ({signed_pct(delta_pct(b2_['cost_head'], b1_['cost_head']))})"
                       if b1_["cost_head"] else ""))
    if b2_["gp_head"] is not None and b1_["gp_head"] is not None:
        dg = delta_pct(b2_["gp_head"], b1_["gp_head"])
        find.append(f"กำไรขั้นต้น/หัว ฿{b1(b1_['gp_head'])} → ฿{b1(b2_['gp_head'])} ({signed_pct(dg)})"
                    + (f" — เครือ ฿{b1(c2_['gp_head'])}" if c2_["gp_head"] is not None else ""))
        if dg is not None and dg < -3:
            reco.append("กำไร/หัวลดลงเกิน 3% ในเดือนเดียว — ตรวจราคาขายเฉลี่ยต่อหัวและส่วนลดควบคู่กับต้นทุน ไม่ใช่ต้นทุนอย่างเดียว")
    if b1_["covers"] and c1_["covers"]:
        gb, gc = delta_pct(b2_["covers"], b1_["covers"]), delta_pct(c2_["covers"], c1_["covers"])
        sh1, sh2 = d["share_rev"][m1], d["share_rev"][m2]
        find.append(f"ลูกค้า {b0(b1_['covers'])} → {b0(b2_['covers'])} หัว ({signed_pct(gb)}) เทียบเครือ {signed_pct(gc)}"
                    + (f" · ส่วนแบ่งรายได้เครือ {pct(sh1)} → {pct(sh2)}" if sh1 is not None and sh2 is not None else ""))
        if gb is not None and gc is not None and gb < gc - 3:
            reco.append("จำนวนลูกค้าโตช้ากว่าเครือเกิน 3 จุด — ดูโปรโมชันท้องถิ่น/ช่องทางเดลิเวอรีของสาขา ก่อนสรุปว่าเป็นเรื่องต้นทุน")
    top = [x for x in d["drivers"] if x["cogs2"] > 0][:3]
    if top:
        find.append("เมนูที่กินต้นทุนสูงสุดของสาขา: " + " · ".join(
            f"{x['name']} ฿{b0(x['cogs2'])} ({pct(x['share2'])} ของต้นทุน" + (f", {signed_pct(x['d_cogs_pct'])} MoM" if x["d_cogs_pct"] is not None else "") + ")"
            for x in top))
        risers = [x for x in d["drivers"] if x["d_cogs_pct"] is not None and x["d_cogs_pct"] > 15 and x["cogs2"] > 0]
        if risers:
            reco.append("ต้นทุนของ " + ", ".join(x["name"] for x in risers[:3])
                        + " โตเกิน 15% ในเดือนเดียว — เช็คปริมาณเสิร์ฟ/ของเสีย/การเบิกของเมนูเหล่านี้ก่อน")
    if not reco:
        reco.append("ตัวเลขอยู่ในกรอบของเครือ — รักษามาตรฐานสูตรและติดตามเมนูต้นทุนสูงสุด 3 อันดับทุกเดือน")
    return {"findings": find, "recommendations": reco}


# ── HTML ─────────────────────────────────────────────────────────────────────
CSS = """
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Thai:wght@400;500;600;700&display=swap');
@page{size:A4;margin:0}
*{box-sizing:border-box}
body{margin:0;font-family:'IBM Plex Sans Thai',Sarabun,sans-serif;color:#1f2937;background:#e5e7eb;font-size:12.5px;line-height:1.55}
.page{width:210mm;min-height:296mm;margin:0 auto 8mm;background:#fff;padding:16mm 16mm 14mm;position:relative;page-break-after:always;break-after:page}
.page:last-child{break-after:auto;page-break-after:auto}
@media print{body{background:#fff}.page{margin:0}}
h1{font-size:26px;margin:0 0 6px;color:#0f766e}
h2{font-size:17px;margin:0 0 10px;color:#0f766e;border-bottom:2px solid #0f766e;padding-bottom:4px}
h3{font-size:13.5px;margin:14px 0 6px;color:#374151}
.muted{color:#6b7280}.small{font-size:11px}
.cover{display:flex;flex-direction:column;justify-content:center;min-height:120mm;border-left:6px solid #0f766e;padding-left:14mm}
.cover .kicker{letter-spacing:.14em;text-transform:uppercase;font-size:11px;color:#0f766e;font-weight:600}
.cover .big{font-size:34px;font-weight:700;line-height:1.15;margin:8px 0}
.figs{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:10px 0 14px}
.fig{border:1px solid #e5e7eb;border-radius:8px;padding:9px 10px;background:#f9fafb}
.fig .l{font-size:10.5px;color:#6b7280}.fig .v{font-size:19px;font-weight:700;color:#111827;margin-top:2px}.fig .s{font-size:10.5px;color:#374151}
.up{color:#b91c1c}.down{color:#047857}.neutral{color:#374151}
table{width:100%;border-collapse:collapse;font-size:11.5px}
th{background:#f3f4f6;text-align:left;padding:6px 7px;border-bottom:1px solid #d1d5db;font-weight:600}
td{padding:5px 7px;border-bottom:1px solid #eef0f2;vertical-align:top}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.callout{background:#ecfdf5;border-left:4px solid #0f766e;padding:8px 12px;border-radius:0 8px 8px 0;margin:10px 0}
.warn{background:#fffbeb;border-left-color:#d97706}
ul.f{padding-left:18px;margin:6px 0}ul.f li{margin:5px 0}
.foot{position:absolute;left:16mm;right:16mm;bottom:8mm;display:flex;justify-content:space-between;font-size:10px;color:#9ca3af;border-top:1px solid #e5e7eb;padding-top:4px}
table.tight{font-size:11px}table.tight td{padding:4px 6px}
.bar{height:10px;background:#e5e7eb;border-radius:5px;overflow:hidden}.bar i{display:block;height:100%;background:#0f766e}
"""


def fig(label, value, sub="", cls="neutral"):
    return f'<div class="fig"><div class="l">{esc(label)}</div><div class="v">{value}</div><div class="s {cls}">{sub}</div></div>'


def _trend(new, old, better="down"):
    """CSS class for a change: `better='down'` means a decrease is good (costs)."""
    d = delta_pct(new, old)
    if d is None or abs(d) < 0.05:
        return "neutral"
    good = (d < 0) if better == "down" else (d > 0)
    return "down" if good else "up"


def page(inner, pno, name, m1, m2, gen_dt):
    foot = (f'<div class="foot"><span>รายงานผลการดำเนินงานสาขา {esc(name)} · {esc(thai_month(m1))} เทียบ {esc(thai_month(m2))}</span>'
            f'<span>จัดทำ {esc(gen_dt)} · หน้า {pno}/5</span></div>')
    return f'<div class="page">{inner}{foot}</div>'


def build_html(d: dict, gen_dt: str, db_path) -> str:
    m1, m2 = d["m1"], d["m2"]
    name = d["branch"]["name"]
    b1_, b2_ = d["branch_months"][m1], d["branch_months"][m2]
    c1_, c2_ = d["chain_months"][m1], d["chain_months"][m2]
    fr = findings(d)
    tm1, tm2 = thai_month(m1), thai_month(m2)

    # page 1 — cover + executive summary
    p1 = f'''
    <div class="cover"><div class="kicker">Oshinei · Branch Performance Report</div>
      <div class="big">สาขา{esc(name)}</div>
      <div style="font-size:16px">{esc(tm1)} เทียบ {esc(tm2)} · เทียบมาตรฐานทั้งเครือ ({d["n_branches"]} สาขา)</div>
      <div class="muted" style="margin-top:14px">ประเภทสาขา: {esc(d["branch"]["type"] or "-")} · จัดทำ {esc(gen_dt)} · แหล่งข้อมูล: ระบบ Oshinei Dashboard (POS + BoM)</div>
    </div>
    <h2>สรุปผู้บริหาร</h2>
    <div class="figs">
      {fig("รายได้ (ไม่รวม VAT)", "฿" + b0(b2_["revenue"]), signed_pct(delta_pct(b2_["revenue"], b1_["revenue"])) + " MoM", _trend(b2_["revenue"], b1_["revenue"], "up"))}
      {fig("ลูกค้า (หัว)", b0(b2_["covers"]), signed_pct(delta_pct(b2_["covers"], b1_["covers"])) + " MoM", _trend(b2_["covers"], b1_["covers"], "up"))}
      {fig("ต้นทุนอาหาร %", pct(b2_["food_pct"]), "เครือ " + pct(c2_["food_pct"]), "up" if (b2_["food_pct"] or 0) > (c2_["food_pct"] or 0) else "down")}
      {fig("กำไรขั้นต้น/หัว", "฿" + b1(b2_["gp_head"]), "เครือ ฿" + b1(c2_["gp_head"]), "down" if (b2_["gp_head"] or 0) >= (c2_["gp_head"] or 0) else "up")}
    </div>
    <ul class="f">{"".join(f"<li>{esc(x)}</li>" for x in fr["findings"])}</ul>
    <div class="callout"><b>ข้อเสนอแนะหลัก:</b> {esc(fr["recommendations"][0])}</div>
    <p class="small muted">ฐานคำนวณเดียวกับแอป: รายได้ = ยอดขายไม่รวม VAT · ต้นทุน = ปริมาณขาย × ต้นทุนต่อหน่วย (ราคาแพ็กเกจบุฟเฟ่ต์ไม่นับเป็นต้นทุน) · ต้นทุน/หัว = ต้นทุน ÷ จำนวนลูกค้า · กำไรขั้นต้น/หัว = (รายได้ − ต้นทุน) ÷ จำนวนลูกค้า</p>'''

    # page 2 — performance
    def row(label, f, fmt, better):
        vb1, vb2, vc1, vc2 = f(b1_), f(b2_), f(c1_), f(c2_)
        return (f'<tr><td>{esc(label)}</td><td class="n">{fmt(vb1)}</td><td class="n"><b>{fmt(vb2)}</b></td>'
                f'<td class="n {_trend(vb2 or 0, vb1 or 0, better)}">{signed_pct(delta_pct(vb2 or 0, vb1 or 0))}</td>'
                f'<td class="n">{fmt(vc1)}</td><td class="n">{fmt(vc2)}</td>'
                f'<td class="n">{signed_pct(delta_pct(vc2 or 0, vc1 or 0))}</td></tr>')
    share1, share2 = d["share_rev"][m1], d["share_rev"][m2]
    p2 = f'''
    <h2>1. ผลการดำเนินงาน</h2>
    <table><thead><tr><th>ตัวชี้วัด</th><th class="n">{esc(tm1)}</th><th class="n">{esc(tm2)}</th><th class="n">เปลี่ยน</th>
      <th class="n">เครือ {esc(tm1)}</th><th class="n">เครือ {esc(tm2)}</th><th class="n">เครือเปลี่ยน</th></tr></thead><tbody>
      {row("รายได้ (฿)", lambda s: s["revenue"], lambda v: "฿" + b0(v), "up")}
      {row("ลูกค้า (หัว)", lambda s: s["covers"], b0, "up")}
      {row("บิล", lambda s: s["bills"], b0, "up")}
      {row("รายได้/หัว (฿)", lambda s: s["spend_head"], lambda v: "฿" + b1(v), "up")}
    </tbody></table>
    <h3>ส่วนแบ่งรายได้ในเครือ</h3>
    <div class="bar"><i style="width:{min(100, (share2 or 0) * 4):.1f}%"></i></div>
    <p>{esc(name)} มีส่วนแบ่ง {pct(share2)} ของรายได้ทั้งเครือใน{esc(tm2)} (จาก {pct(share1)} ใน{esc(tm1)}) — แถบแสดงสัดส่วนโดยเต็มแถบ = 25%</p>
    <div class="callout{'' if (delta_pct(b2_["covers"], b1_["covers"]) or 0) >= (delta_pct(c2_["covers"], c1_["covers"]) or 0) else ' warn'}">
      จำนวนลูกค้าของสาขา {signed_pct(delta_pct(b2_["covers"], b1_["covers"]))} เทียบเครือ {signed_pct(delta_pct(c2_["covers"], c1_["covers"]))} ใน{esc(tm2)}
    </div>
    <p class="small muted">รายได้และจำนวนลูกค้าจากตารางสรุปรายเดือนของ POS (Easy) · เครือ = ทุกสาขาที่เปิดดำเนินการ</p>'''

    # page 3 — cost & margin
    p3 = f'''
    <h2>2. ต้นทุนและมาร์จิน</h2>
    <div class="figs">
      {fig("ต้นทุนอาหาร (฿)", "฿" + b0(b2_["cogs"]), signed_pct(delta_pct(b2_["cogs"], b1_["cogs"])) + " MoM", _trend(b2_["cogs"], b1_["cogs"]))}
      {fig("ต้นทุนอาหาร %", pct(b2_["food_pct"]), f"{esc(tm1)}: " + pct(b1_["food_pct"]), "up" if (b2_["food_pct"] or 0) > (b1_["food_pct"] or 0) else "down")}
      {fig("ต้นทุน/หัว", "฿" + b1(b2_["cost_head"]), "เครือ ฿" + b1(c2_["cost_head"]), "up" if (b2_["cost_head"] or 0) > (c2_["cost_head"] or 0) else "down")}
      {fig("อันดับต้นทุน/หัว", f"#{d['rank_cost_head'] or '-'}/{d['n_ranked']}", "1 = แพงที่สุดในเครือ")}
    </div>
    <table><thead><tr><th>ตัวชี้วัด</th><th class="n">{esc(tm1)}</th><th class="n">{esc(tm2)}</th><th class="n">เปลี่ยน</th>
      <th class="n">เครือ {esc(tm1)}</th><th class="n">เครือ {esc(tm2)}</th><th class="n">เครือเปลี่ยน</th></tr></thead><tbody>
      {row("ต้นทุนอาหาร (฿)", lambda s: s["cogs"], lambda v: "฿" + b0(v), "down")}
      {row("ต้นทุนอาหาร % ของรายได้", lambda s: s["food_pct"], pct, "down")}
      {row("ต้นทุน/หัว (฿)", lambda s: s["cost_head"], lambda v: "฿" + b1(v), "down")}
      {row("กำไรขั้นต้น/หัว (฿)", lambda s: s["gp_head"], lambda v: "฿" + b1(v), "up")}
    </tbody></table>
    <p class="small muted">ต้นทุนวัตถุดิบตามสูตร BoM / ตารางต้นทุนของระบบ (ต้นทุนจริงที่อัปโหลด มีสิทธิ์ก่อนสูตร) · แพ็กเกจบุฟเฟ่ต์นับต้นทุนจากจานที่เสิร์ฟ ไม่ใช่ราคาแพ็กเกจ · ค่าแรง/ค่าเช่าไม่รวม</p>'''

    # page 4 — the branch's own menu cost drivers
    drv = d["drivers"]
    rows = "".join(
        f'<tr><td>{i + 1}</td><td>{esc(x["name"])} <span class="small muted">{esc(x["code"])}</span></td>'
        f'<td class="n">{b0(x["qty2"])}</td><td class="n">฿{b0(x["cogs2"])}</td><td class="n">{pct(x["share2"])}</td>'
        f'<td class="n">฿{b1(x["cost_head2"])}</td><td class="n">฿{b1(x["cost_head1"])}</td>'
        f'<td class="n {"up" if (x["d_cogs_pct"] or 0) > 0 else "down"}">{signed_pct(x["d_cogs_pct"])}</td></tr>'
        for i, x in enumerate(drv))
    p4 = f'''
    <h2>3. เมนูที่กินต้นทุนสูงสุดของสาขา ({esc(tm2)})</h2>
    <p class="muted">10 อันดับแรกตามต้นทุนวัตถุดิบรวมในเดือน · ต้นทุน/หัว = ต้นทุนของเมนูนั้น ÷ ลูกค้าทั้งสาขา (บอกว่าเมนูนี้กินต้นทุนกี่บาทต่อลูกค้าหนึ่งคน)</p>
    <table class="tight"><thead><tr><th>#</th><th>เมนู</th><th class="n">จำนวน</th><th class="n">ต้นทุน (฿)</th><th class="n">% ต้นทุนสาขา</th>
      <th class="n">ต้นทุน/หัว {esc(tm2)}</th><th class="n">ต้นทุน/หัว {esc(tm1)}</th><th class="n">ต้นทุน MoM</th></tr></thead>
      <tbody>{rows or '<tr><td colspan="8" class="muted">ไม่มีข้อมูลต้นทุนรายเมนูของเดือนนี้</td></tr>'}</tbody></table>
    <p class="small muted">เมนูที่ไม่มีต้นทุนในระบบ (ต้นทุน 0) จะไม่ปรากฏในตาราง — ดูรายการค้างได้ที่แท็บ ครอบคลุมต้นทุน ในแอป</p>'''

    # page 5 — findings + recommendations
    p5 = f'''
    <h2>4. ข้อค้นพบ</h2>
    <ul class="f">{"".join(f"<li>{esc(x)}</li>" for x in fr["findings"])}</ul>
    <h2 style="margin-top:18px">5. ข้อเสนอแนะ</h2>
    <ol class="f">{"".join(f"<li>{esc(x)}</li>" for x in fr["recommendations"])}</ol>
    <div class="callout" style="margin-top:22px"><b>ติดตามเดือนหน้า:</b> ต้นทุนอาหาร % ให้ขยับเข้าหาเครือ · ต้นทุน/หัวของ 3 เมนูแรกในหน้า 3 · จำนวนลูกค้าเทียบเครือ</div>
    <p class="small muted" style="margin-top:30px">รายงานนี้สร้างจากฐานข้อมูล {esc(Path(str(db_path)).name)} ด้วย backend/gen_branch_report.py · ตัวเลขทุกตัวตรงกับหน้าจอแอปในวันจัดทำ</p>'''

    body = "".join(page(p, i + 1, name, m1, m2, gen_dt) for i, p in enumerate((p1, p2, p3, p4, p5)))
    return (f'<!doctype html><html lang="th"><head><meta charset="utf-8"><title>Branch Report {esc(name)} {esc(m1)} vs {esc(m2)}</title>'
            f'<style>{CSS}</style></head><body>{body}</body></html>')


# ── PDF ──────────────────────────────────────────────────────────────────────
def to_pdf(html_path: Path, pdf_path: Path) -> bool:
    chrome = CHROME if CHROME.exists() else (shutil.which("chrome") or shutil.which("google-chrome"))
    if not chrome:
        print("Chrome not found — HTML only")
        return False
    cmd = [str(chrome), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
           "--virtual-time-budget=12000", f"--print-to-pdf={pdf_path.resolve()}", html_path.resolve().as_uri()]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if not pdf_path.exists():
        print("PDF failed:", (r.stderr or "")[-300:])
        return False
    try:
        import pikepdf
        with pikepdf.open(pdf_path) as pdf:
            n = len(pdf.pages)
        print(f"PDF: {pdf_path.name} — {n} page(s)" + ("" if n == 5 else "  ⚠ expected 5"))
    except Exception:
        print(f"PDF: {pdf_path.name}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Branch performance report: one branch, two months, vs the chain")
    ap.add_argument("--branch", required=True)
    ap.add_argument("--m1", required=True); ap.add_argument("--m2", required=True)
    ap.add_argument("--slug", default="")
    ap.add_argument("--gen-dt", default="")
    ap.add_argument("--db", default=None)
    ap.add_argument("--out", default=str(HERE.parent / "docs"))
    ap.add_argument("--no-pdf", action="store_true")
    a = ap.parse_args()
    db = find_db(a.db)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    d = compute(con, a.branch, a.m1, a.m2)
    con.close()
    today = dt.date.today()
    gen_dt = a.gen_dt or f"{today.day} {THAI_MONTHS[today.month - 1]} {today.year + 543}"
    slug = a.slug or "".join(ch for ch in a.branch if ch.isalnum()) or f"branch{d['branch']['id']}"
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    html_path = out / f"BRANCH_REPORT_{slug}_{a.m1}_{a.m2}.html"
    html_path.write_text(build_html(d, gen_dt, db), encoding="utf-8")
    print("HTML:", html_path)
    for line in findings(d)["findings"]:
        print("  •", line)
    if not a.no_pdf:
        to_pdf(html_path, html_path.with_suffix(".pdf"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
