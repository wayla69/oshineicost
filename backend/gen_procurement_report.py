#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Procurement report: theoretical ingredient cost vs sales (no real purchase
ledger exists yet — see caveat on page 2), the 48-core-ingredient order plan
by branch, and a watch list of follow-up items.

    python backend/gen_procurement_report.py [--out docs] [--no-pdf]

Pulls live from the app's own endpoints (in-process, no HTTP/auth needed
locally) so the numbers match the dashboard exactly:
  /api/ingredients/purchases            — actual purchase ledger (if any)
  /api/bom-cost/branch-foodcost-mom     — theoretical COGS vs revenue per branch
  /api/ingredients/order-plan           — 48-core-ingredient demand, chain + per branch
  /api/ingredients/order-plan-by-branch.xlsx — the per-branch order sheet (attached as-is)

→ docs/PROCUREMENT_REPORT_<month>.html (+ .pdf via Chrome headless)
  docs/PROCUREMENT_REPORT_<month>_order_plan_by_branch.xlsx (copy of the live export)
"""
import argparse
import datetime as dt
import html
import subprocess
import sys
from pathlib import Path
from report_fonts import font_face_css  # KIRA UI, embedded (backend/report_fonts.py)

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

THAI_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
               "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


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


def signed_pct(n, d=1) -> str:
    if n is None:
        return "—"
    return f"{'+' if n >= 0 else ''}{n:.{d}f} จุด" if abs(n) < 100 else f"{'+' if n >= 0 else ''}{n:.0f} จุด"


# ── pull live data (in-process, no HTTP) ────────────────────────────────────
def pull_data():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)

    def get(path, **params):
        r = client.get(path, params=params)
        r.raise_for_status()
        return r.json()

    purchases = get("/api/ingredients/purchases")
    order_plan = get("/api/ingredients/order-plan", per_branch="true", include="central", days=28)
    forced = get("/api/ingredients/forced-order")

    # last full calendar month for the headline food-cost comparison (the
    # current month is always partial and swings on which weekday it ends on)
    today = dt.date.today()
    cur = today.replace(day=1)
    prev = (cur - dt.timedelta(days=1)).replace(day=1)
    latest_full = prev.strftime("%Y-%m")
    foodcost = get("/api/bom-cost/branch-foodcost-mom", month=latest_full)

    # per-branch item-level order plan — one call per branch, used for the
    # detailed per-branch section (page-per-branch), not just the chain rollup
    branch_names = [b["branch"] for b in order_plan["branches"]]
    order_plan_branch = {}
    for name in branch_names:
        order_plan_branch[name] = get("/api/ingredients/order-plan", branch=name, include="central", days=28)

    return {"purchases": purchases, "order_plan": order_plan, "forced": forced,
            "foodcost": foodcost, "latest_full_month": latest_full,
            "order_plan_branch": order_plan_branch}


# ── CSS (matches gen_branch_report.py house style) ──────────────────────────
CSS = font_face_css() + """
@page{size:A4;margin:0}
*{box-sizing:border-box}
body{margin:0;font-family:'KIRA UI','Noto Sans Thai',sans-serif;color:#1f2937;background:#e5e7eb;font-size:12.5px;line-height:1.55}
.page{width:210mm;min-height:296mm;margin:0 auto 8mm;background:#fff;padding:16mm 16mm 14mm;position:relative;page-break-after:always;break-after:page}
.page:last-child{break-after:auto;page-break-after:auto}
@media print{body{background:#fff}.page{margin:0}}
h1{font-size:26px;margin:0 0 6px;color:#1f3a6b}
h2{font-size:17px;margin:0 0 10px;color:#1f3a6b;border-bottom:2px solid #1f3a6b;padding-bottom:4px}
h3{font-size:13.5px;margin:14px 0 6px;color:#374151}
.muted{color:#6b7280}.small{font-size:11px}
.cover{display:flex;flex-direction:column;justify-content:center;min-height:120mm;border-top:4px solid #1f3a6b;padding-top:10mm}
.kicker{letter-spacing:.14em;text-transform:uppercase;font-size:11px;color:#1f3a6b;font-weight:600}
.cover .big{font-size:32px;font-weight:700;line-height:1.2;margin:8px 0}
.figs{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:10px 0 14px}
.fig{border:1px solid #e5e7eb;border-radius:4px;padding:9px 10px;background:#f9fafb}
.fig .l{font-size:10.5px;color:#6b7280}.fig .v{font-size:18px;font-weight:700;color:#111827;margin-top:2px}.fig .s{font-size:10.5px;color:#374151}
.up{color:#b91c1c}.down{color:#047857}.neutral{color:#374151}
table{width:100%;border-collapse:collapse;font-size:11.5px}
th{background:#f3f4f6;text-align:left;padding:6px 7px;border-bottom:1px solid #d1d5db;font-weight:600}
td{padding:5px 7px;border-bottom:1px solid #eef0f2;vertical-align:top}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.callout{background:#e8eef7;border:1px solid #1f3a6b;padding:8px 12px;border-radius:4px;margin:10px 0}
.warn{background:#fffbeb;border-color:#d97706}
.danger{background:#fef2f2;border-left-color:#b91c1c}
ul.f{padding-left:18px;margin:6px 0}ul.f li{margin:5px 0}
ol.f{padding-left:18px;margin:6px 0}ol.f li{margin:6px 0}
.foot{position:absolute;left:16mm;right:16mm;bottom:8mm;display:flex;justify-content:space-between;font-size:10px;color:#9ca3af;border-top:1px solid #e5e7eb;padding-top:4px}
table.tight{font-size:10.5px}table.tight td{padding:3px 6px}table.tight th{padding:5px 6px}
.badge{display:inline-block;padding:1px 7px;border-radius:4px;font-size:10px;font-weight:600}
.badge.hi{background:#fef2f2;color:#b91c1c}.badge.lo{background:#ecfdf5;color:#047857}
"""


def fig(label, value, sub="", cls="neutral"):
    return f'<div class="fig"><div class="l">{esc(label)}</div><div class="v">{value}</div><div class="s {cls}">{sub}</div></div>'


def page(inner, pno, gen_dt, total=5):
    foot = (f'<div class="foot"><span>Oshinei · รายงานต้นทุนวัตถุดิบ & แผนสั่งซื้อวัตถุดิบหลัก 48 ชนิด</span>'
            f'<span>จัดทำ {esc(gen_dt)} · หน้า {pno}/{total}</span></div>')
    return f'<div class="page">{inner}{foot}</div>'


def build_html(d: dict, gen_dt: str) -> str:
    m = d["latest_full_month"]
    tm = thai_month(m)
    fc = d["foodcost"]["branches"]
    fc_sorted = sorted(fc, key=lambda b: -(b["food_cost_pct"] or 0))
    chain_rev = sum(b["revenue"] for b in fc)
    chain_cogs = sum(b["cogs"] for b in fc)
    chain_costed = sum(b["costed_revenue"] for b in fc)
    chain_pct = round(chain_cogs / chain_costed * 100, 1) if chain_costed else None

    op = d["order_plan"]
    op_branches = sorted(op["branches"], key=lambda b: -b["value_month"])
    op_total_month = sum(b["value_month"] for b in op_branches)
    op_total_week = sum(b["value_week"] for b in op_branches)
    op_rows_sorted = sorted(op["rows"], key=lambda r: -r["value_month"])
    top5_ing = op_rows_sorted[:5]
    not_packed = [r for r in op["rows"] if not r.get("pack_known")]
    coverage = op["meta"]["coverage"]["pct"]

    purch = d["purchases"]
    imported_weeks = purch["meta"]["imported_weeks"]
    total_weeks = imported_weeks + purch["meta"]["missing_weeks"]

    # ── page 1: cover + executive summary ──────────────────────────────────
    p1 = f'''
    <div class="cover"><div class="kicker">Oshinei · Procurement &amp; Ingredient Cost Report</div>
      <div class="big">ต้นทุนวัตถุดิบเทียบยอดขาย &amp; แผนสั่งซื้อวัตถุดิบหลัก 48 ชนิด แยกรายสาขา</div>
      <div style="font-size:15px">ฐานหลัก: {esc(tm)} (เดือนล่าสุดที่ปิดครบเดือน) · แผนสั่งซื้อ: ข้อมูลขาย 28 วันล่าสุด</div>
      <div class="muted" style="margin-top:14px">28 สาขา · จัดทำ {esc(gen_dt)} · แหล่งข้อมูล: ระบบ Oshinei Dashboard (POS + BoM)</div>
    </div>
    <h2>สรุปผู้บริหาร</h2>
    <div class="figs">
      {fig("ยอดขาย (ไม่รวม VAT) " + tm, "฿" + b0(chain_rev), "28 สาขา", "neutral")}
      {fig("ต้นทุนวัตถุดิบตามสูตร (ทฤษฎี)", "฿" + b0(chain_cogs), pct(chain_pct) + " ของยอดขาย", "up" if (chain_pct or 0) > 65 else "neutral")}
      {fig("มูลค่าที่ควรสั่งซื้อ 48 รายการหลัก/เดือน", "฿" + b0(op_total_month), pct(round(op_total_month / chain_cogs * 100, 1) if chain_cogs else None) + " ของต้นทุนวัตถุดิบรวม", "neutral")}
      {fig("ยอดซื้อจริงที่บันทึกในระบบ", "฿0", f"นำเข้าแล้ว {imported_weeks}/{total_weeks} สัปดาห์-สาขา", "up")}
    </div>
    <ul class="f">
      <li>ต้นทุนวัตถุดิบตามสูตร (BoM) ทั้งเครืออยู่ที่ {pct(chain_pct)} ของยอดขายใน{esc(tm)} — สาขาสูงสุด {esc(fc_sorted[0]["branch"])} {pct(fc_sorted[0]["food_cost_pct"])} ต่ำสุด {esc(fc_sorted[-1]["branch"])} {pct(fc_sorted[-1]["food_cost_pct"])} ห่างกัน {pct(round((fc_sorted[0]["food_cost_pct"] or 0) - (fc_sorted[-1]["food_cost_pct"] or 0), 1))}</li>
      <li>วัตถุดิบหลัก 48 รายการ (บังคับสั่งส่วนกลาง) คิดเป็นมูลค่าที่ควรสั่งซื้อรวม ฿{b0(op_total_month)}/เดือน (฿{b0(op_total_week)}/สัปดาห์) ครอบคลุม {pct(coverage)} ของปริมาณอาหารที่ขายจริงทั้งเครือ</li>
      <li>ระบบยังไม่มีการนำเข้า "ยอดซื้อจริง" แม้แต่สัปดาห์เดียว (0 จาก {total_weeks} สัปดาห์-สาขา) — ตัวเลขต้นทุนในรายงานนี้จึงเป็น <b>ค่าทฤษฎีจากสูตรอาหาร</b> ไม่ใช่ยอดซื้อจริงจากใบสั่งซื้อ/ใบเสร็จ (ดูรายละเอียดหน้า 2)</li>
    </ul>
    <div class="callout"><b>ข้อเสนอแนะหลัก:</b> เริ่มนำเข้ายอดซื้อวัตถุดิบรายสัปดาห์ต่อสาขาผ่านฟอร์มที่มีอยู่แล้วในแอป (แท็บวัตถุดิบ → ยอดซื้อ) เพื่อให้เทียบ "ซื้อจริง" กับ "ควรใช้ตามสูตร" ได้ทุกเดือน — ปัจจุบันมีเพียงฝั่งทฤษฎี</div>
    <p class="small muted">ฐานคำนวณเดียวกับแอป: รายได้ = ยอดขายไม่รวม VAT · ต้นทุนวัตถุดิบ = ปริมาณขาย × ต้นทุนต่อหน่วยตามสูตร BoM (ต้นทุนจริงที่อัปโหลด มีสิทธิ์ก่อนสูตร) · ~12% ของจานยังไม่มีต้นทุนในระบบ (นับเป็น 0) ตัวเลข % ต้นทุนจริงจึงมักสูงกว่าที่แสดงเล็กน้อย</p>'''

    # ── page 2: purchases vs sales ──────────────────────────────────────────
    rows_fc = "".join(
        f'<tr><td>{i + 1}</td><td>{esc(b["branch"])}</td>'
        f'<td class="n">฿{b0(b["revenue"])}</td><td class="n">฿{b0(b["cogs"])}</td>'
        f'<td class="n"><b>{pct(b["food_cost_pct"])}</b></td>'
        f'<td class="n {"up" if (b["delta_pp"] or 0) > 0 else "down"}">{signed_pct(b["delta_pp"])}</td>'
        f'<td class="n">{"<span class=\'badge hi\'>สูง</span>" if (b["food_cost_pct"] or 0) >= 65 else ("<span class=\'badge lo\'>ต่ำ</span>" if (b["food_cost_pct"] or 0) <= 55 else "")}</td></tr>'
        for i, b in enumerate(fc_sorted))
    p2 = f'''
    <h2>1. ยอดซื้อ (ต้นทุนวัตถุดิบ) เทียบยอดขาย</h2>
    <div class="callout warn"><b>ข้อจำกัดข้อมูล:</b> ระบบมีช่องทางนำเข้า "ยอดซื้อวัตถุดิบจริงรายสัปดาห์ต่อสาขา" อยู่แล้ว (ตาราง ingredient_purchase_week) แต่ยังไม่เคยมีการนำเข้าข้อมูลเลยแม้แต่สัปดาห์เดียว ({imported_weeks} จาก {total_weeks} สัปดาห์-สาขาในช่วง 12 สัปดาห์ล่าสุด) รายงานหน้านี้จึงใช้ <b>ต้นทุนวัตถุดิบตามสูตรอาหาร (BoM)</b> ซึ่งเป็นสิ่งที่ "ควรใช้" ตามยอดขายจริง แทนยอดซื้อจริงที่ยังไม่มี</div>
    <h3>ต้นทุนวัตถุดิบตามสูตร (ทฤษฎี) เทียบยอดขาย — {esc(tm)}, 28 สาขา เรียงจาก % สูงสุด</h3>
    <table class="tight"><thead><tr><th>#</th><th>สาขา</th><th class="n">ยอดขาย (฿)</th><th class="n">ต้นทุนวัตถุดิบ (฿)</th>
      <th class="n">% ต้นทุน</th><th class="n">เปลี่ยนจาก{esc(thai_month((dt.date(int(m[:4]),int(m[5:7]),1)-dt.timedelta(days=1)).strftime("%Y-%m")))}</th><th></th></tr></thead>
      <tbody>{rows_fc}</tbody>
      <tfoot><tr style="font-weight:700"><td colspan="2">รวมทั้งเครือ (28 สาขา)</td><td class="n">฿{b0(chain_rev)}</td><td class="n">฿{b0(chain_cogs)}</td><td class="n">{pct(chain_pct)}</td><td colspan="2"></td></tr></tfoot>
    </table>
    <p class="small muted">% ต้นทุน = ต้นทุนวัตถุดิบตามสูตร ÷ ยอดขายเฉพาะเมนูที่มีต้นทุนในระบบ (costed revenue) · badge "สูง" = ≥65% (ควรตรวจพอร์ชั่น/ของเสีย) · "ต่ำ" = ≤55% (ตรวจว่าไม่ใช่การบันทึกต้นทุนที่ขาดหาย)</p>'''

    # ── page 3: 48-item order plan by branch ────────────────────────────────
    rows_op = "".join(
        f'<tr><td>{i + 1}</td><td>{esc(b["branch"])}</td><td class="n">{b["ingredients"]}</td>'
        f'<td class="n">฿{b0(b["value_week"])}</td><td class="n"><b>฿{b0(b["value_month"])}</b></td></tr>'
        for i, b in enumerate(op_branches))
    rows_top5 = "".join(
        f'<tr><td>{i + 1}</td><td>{esc(r["code"])}</td><td>{esc(r["name"])}</td>'
        f'<td class="n">{esc(r["order_month_units_disp"])}</td><td class="n">฿{b0(r["value_month"])}</td></tr>'
        for i, r in enumerate(top5_ing))
    p3 = f'''
    <h2>2. แผนสั่งซื้อวัตถุดิบหลัก 48 ชนิด แยกรายสาขา</h2>
    <p class="muted small" style="margin:2px 0 8px">วิธีคำนวณ: ยอดขายจริง 28 วันล่าสุด × สูตรอาหาร (BoM) เฉพาะรายการที่ "บังคับสั่งส่วนกลาง" ({op["meta"]["forced"]["items"]} รายการหลัก จากไฟล์ {esc(op["meta"]["forced"]["file"])} → รวม {op["totals"]["ingredients"]} รหัสวัตถุดิบเมื่อรวมรหัสคลัง/รหัสสูตรของรายการเดียวกัน) เผื่อสต๊อก 10% ปัดปริมาณสั่งขึ้นเป็นจำนวนเต็มทุกกรณี</p>
    <div class="figs" style="margin:6px 0 8px">
      {fig("มูลค่าที่ควรสั่งซื้อรวม/เดือน", "฿" + b0(op_total_month), "28 สาขา", "neutral")}
      {fig("มูลค่าที่ควรสั่งซื้อรวม/สัปดาห์", "฿" + b0(op_total_week), "", "neutral")}
      {fig("ความครอบคลุม", pct(coverage), "ของปริมาณอาหารขายจริงทั้งเครือ", "neutral")}
      {fig("สาขาสูงสุด", esc(op_branches[0]["branch"]), "฿" + b0(op_branches[0]["value_month"]) + "/เดือน", "neutral")}
    </div>
    <table class="tight"><thead><tr><th>#</th><th>สาขา</th><th class="n">จำนวนรายการ</th><th class="n">มูลค่า/สัปดาห์ (฿)</th><th class="n">มูลค่า/เดือน (฿)</th></tr></thead>
      <tbody>{rows_op}</tbody>
      <tfoot><tr style="font-weight:700"><td colspan="2">รวมทั้งเครือ</td><td class="n"></td><td class="n">฿{b0(op_total_week)}</td><td class="n">฿{b0(op_total_month)}</td></tr></tfoot>
    </table>'''

    p3b = f'''
    <h2>2. แผนสั่งซื้อวัตถุดิบหลัก 48 ชนิด แยกรายสาขา (ต่อ)</h2>
    <h3>5 วัตถุดิบมูลค่าสูงสุด (รวมทั้งเครือ ต่อเดือน)</h3>
    <table class="tight"><thead><tr><th>#</th><th>รหัส</th><th>วัตถุดิบ</th><th class="n">ควรสั่ง/เดือน</th><th class="n">มูลค่า/เดือน (฿)</th></tr></thead>
      <tbody>{rows_top5}</tbody></table>
    <p class="small muted">รายละเอียดครบทุกรายการ ทุกสาขา (1 แผ่นต่อสาขา) อยู่ในไฟล์แนบ order_plan_by_branch.xlsx (ออกจากระบบวันเดียวกับรายงานนี้)</p>'''

    # ── section 3: one detail page per branch ───────────────────────────────
    fc_by_branch = {b["branch"]: b for b in fc}
    op_rank = {b["branch"]: i + 1 for i, b in enumerate(op_branches)}
    fc_rank = {b["branch"]: i + 1 for i, b in enumerate(fc_sorted)}
    n_branches = len(op_branches)

    def mover_rows(movers, limit=3):
        return "".join(
            f'<tr><td>{esc(x["description"] or x["sku_code"])}</td>'
            f'<td class="n">฿{b0(x["cogs"])}</td>'
            f'<td class="n {"up" if x["delta"] >= 0 else "down"}">{"+" if x["delta"] >= 0 else ""}฿{b0(x["delta"])}</td></tr>'
            for x in movers[:limit])

    def ing_col_rows(rows):
        return "".join(
            f'<tr><td>{i}</td><td>{esc(r["name"])}</td><td class="n">{esc(r.get("order_month_units_disp","—"))}</td>'
            f'<td class="n">฿{b0(r["value_month"])}</td></tr>'
            for i, r in rows)

    branch_pages = []
    for name in sorted(fc_by_branch.keys()):
        fcb = fc_by_branch[name]
        opb = d["order_plan_branch"].get(name, {})
        opb_rows_all = sorted(opb.get("rows", []), key=lambda r: -r["value_month"])
        opb_totals = opb.get("totals", {})
        cards = (
            fig("ยอดขาย (ไม่รวม VAT)", "฿" + b0(fcb["revenue"]), "", "neutral")
            + fig("ต้นทุนวัตถุดิบตามสูตร", "฿" + b0(fcb["cogs"]), pct(fcb["food_cost_pct"]) + " ของยอดขาย",
                  "up" if (fcb["food_cost_pct"] or 0) >= 65 else ("down" if (fcb["food_cost_pct"] or 0) <= 55 else "neutral"))
            + fig("เปลี่ยนจากเดือนก่อน", signed_pct(fcb["delta_pp"]), pct(fcb["prev_food_cost_pct"]) + " เดือนก่อน",
                  "up" if (fcb["delta_pp"] or 0) > 0 else "down")
            + fig("มูลค่าที่ควรสั่งซื้อ 48 รายการหลัก/เดือน", "฿" + b0(opb_totals.get("value_month", 0)),
                  f'{opb_totals.get("ingredients","—")} รายการ', "neutral")
        )
        section_head = ('<h2 style="margin-bottom:10px">3. รายละเอียดรายสาขา (28 สาขา)</h2>'
                        if len(branch_pages) == 0 else '')
        pg_a = f'''
        {section_head}
        <div style="border-left:6px solid #1f3a6b;padding-left:10mm;margin-bottom:14px">
          <div class="kicker">ส่วนที่ 3 · รายละเอียดรายสาขา</div>
          <div style="font-size:24px;font-weight:700;color:#111827;margin-top:2px">{esc(name)}</div>
          <div class="muted small" style="margin-top:2px">อันดับต้นทุนอาหาร #{fc_rank[name]}/{n_branches} (1=สูงสุด) · อันดับมูลค่าสั่งซื้อ #{op_rank.get(name,"—")}/{n_branches} (1=สูงสุด)</div>
        </div>
        <div class="figs">{cards}</div>
        <div style="display:flex;gap:14px;margin-top:10px">
          <div style="flex:1">
            <h3 style="margin-top:0">เมนูต้นทุนเพิ่มขึ้นมากสุด (MoM)</h3>
            <table class="tight"><thead><tr><th>เมนู</th><th class="n">ต้นทุนเดือนนี้</th><th class="n">เปลี่ยน</th></tr></thead>
              <tbody>{mover_rows(fcb.get("movers_up", []))}</tbody></table>
          </div>
          <div style="flex:1">
            <h3 style="margin-top:0">เมนูต้นทุนลดลงมากสุด (MoM)</h3>
            <table class="tight"><thead><tr><th>เมนู</th><th class="n">ต้นทุนเดือนนี้</th><th class="n">เปลี่ยน</th></tr></thead>
              <tbody>{mover_rows(fcb.get("movers_down", []))}</tbody></table>
          </div>
        </div>
        <p class="small muted" style="margin-top:16px">วัตถุดิบมูลค่าสูงสุดที่ควรสั่งซื้อทั้งหมด ({len(opb_rows_all)} รายการ) อยู่ในหน้าถัดไป</p>'''
        branch_pages.append(pg_a)

        # Full-width, single-column table, chunked into explicit pages — a
        # side-by-side 2-column layout looked tidy but desynced under Chrome's
        # print pagination (long ingredient names wrap to 2-3 lines, so the two
        # columns drift out of step and one column overflows alone onto a
        # near-empty page). One column at full width wraps less AND paginates
        # predictably; chunk size is picked to always fit one A4 page.
        CHUNK = 30
        numbered = list(enumerate(opb_rows_all, start=1))
        chunks = [numbered[i:i + CHUNK] for i in range(0, len(numbered), CHUNK)] or [[]]
        h_ing = '<tr><th>#</th><th>วัตถุดิบ</th><th class="n">ควรสั่ง/เดือน</th><th class="n">มูลค่า/เดือน (฿)</th></tr>'
        for ci, chunk in enumerate(chunks, start=1):
            page_note = f" (หน้า {ci}/{len(chunks)})" if len(chunks) > 1 else ""
            pg_b = f'''
            <div style="border-left:6px solid #1f3a6b;padding-left:10mm;margin-bottom:14px">
              <div class="kicker">ส่วนที่ 3 · รายละเอียดรายสาขา</div>
              <div style="font-size:20px;font-weight:700;color:#111827;margin-top:2px">{esc(name)} — วัตถุดิบทั้งหมดที่ควรสั่งซื้อ ({len(opb_rows_all)} รายการ){page_note}</div>
            </div>
            <table class="tight"><thead>{h_ing}</thead>
              <tbody>{ing_col_rows(chunk) or '<tr><td colspan="4" class="muted">ไม่มีรายการบังคับสั่งที่มียอดขายในช่วงนี้</td></tr>'}</tbody></table>
            <p class="small muted" style="margin-top:10px">รายการที่ไม่ติดบังคับสั่งของสาขานี้ (ไม่รวมในตารางข้างบน) อยู่ในแผ่นงาน "{esc(name)}" ของไฟล์แนบ order_plan_by_branch.xlsx</p>'''
            branch_pages.append(pg_b)

    # ── page 5: watch list ───────────────────────────────────────────────────
    era = op["meta"].get("era") or {}
    watch = [
        "ยังไม่มีการนำเข้ายอดซื้อจริงแม้แต่สัปดาห์เดียว — เริ่มอัปโหลดยอดซื้อรายสัปดาห์ต่อสาขา (แท็บวัตถุดิบ → ยอดซื้อ ในแอป มีแบบฟอร์มอยู่แล้ว) เพื่อให้เดือนหน้าเทียบ \"ซื้อจริง\" กับ \"ควรใช้ตามสูตร\" ได้ครั้งแรก",
        f"สาขาต้นทุนอาหารสูงสุด 5 อันดับ ({', '.join(esc(b['branch']) + ' ' + pct(b['food_cost_pct']) for b in fc_sorted[:5])}) — ควรตรวจพอร์ชั่นเสิร์ฟ/ของเสีย/การเบิกของเมนูบุฟเฟ่ต์ก่อน โดยเฉพาะเมนูทุเรียน/แซลมอนที่ต้นทุนพุ่งขึ้นมากหลังเปลี่ยนเมนูใหม่",
        (f"ช่วงข้อมูลที่ใช้คำนวณแผนสั่งซื้อคาบเกี่ยววันเปลี่ยนเมนู {esc(era.get('menu_era_from',''))} — ตัวเลขปัจจุบันเป็นค่าผสมของเมนูเก่าและเมนูใหม่ ควรใช้เป็นค่าประมาณเบื้องต้นและยืนยันกับสาขาอีกครั้ง จนกว่าจะมีข้อมูลเต็มเดือนหลังเปลี่ยนเมนู"
         if era.get("straddles") else "ช่วงข้อมูลแผนสั่งซื้อไม่คาบเกี่ยววันเปลี่ยนเมนู — ใช้อ้างอิงได้ตามปกติ"),
        f"{len(not_packed)} จาก {op['totals']['ingredients']} รหัสวัตถุดิบหลัก ยังไม่ได้ตั้งขนาดบรรจุ (pack size) ในระบบ — ปริมาณที่แนะนำให้สั่งจึงปัดเป็นกก./ลิตรมาตรฐานแทนที่จะเป็นแพ็ค/เคสจริงที่ซัพพลายเออร์ขาย ให้ฝ่ายจัดซื้อกรอกขนาดบรรจุจริงเพื่อความแม่นยำของใบสั่งซื้อ",
        f"แผนสั่งซื้อครอบคลุม {pct(coverage)} ของปริมาณอาหารขายจริง — {pct(round(100-coverage,1))} ที่เหลือ (ส่วนใหญ่เป็นเครื่องดื่ม/น้ำจิ้ม/เครื่องเคียงบุฟเฟ่ต์) ยังไม่อยู่ในรายการบังคับสั่ง ควรพิจารณาว่ารายการใดควรเพิ่มเข้าไปมอนิเตอร์",
        "สาขาที่ต้นทุนอาหารต่ำผิดปกติ (≤55%) ควรตรวจว่าไม่ใช่เพราะเมนูยังไม่มีต้นทุนในระบบ (นับเป็น 0 จึงทำให้ % ต่ำเทียม) มากกว่าจะสรุปว่าคุมต้นทุนได้ดีจริง",
    ]
    p4 = f'''
    <h2>4. งานที่ควรเห็นเพิ่มเติม (Watch List)</h2>
    <ol class="f">{"".join(f"<li>{x}</li>" for x in watch)}</ol>
    <div class="callout" style="margin-top:22px"><b>ติดตามเดือนหน้า:</b> เริ่มมีข้อมูลยอดซื้อจริงสัปดาห์แรก · ต้นทุนอาหาร % ของ 5 สาขาสูงสุดขยับลงหรือไม่ · แผนสั่งซื้อหลังพ้นช่วงคาบเกี่ยวเมนูใหม่เต็มเดือน</div>
    <p class="small muted" style="margin-top:30px">รายงานนี้สร้างจาก backend/gen_procurement_report.py โดยดึงข้อมูลสดจาก API เดียวกับที่แอปใช้ (/api/ingredients/purchases, /api/bom-cost/branch-foodcost-mom, /api/ingredients/order-plan) ตัวเลขทุกตัวตรงกับหน้าจอแอปในวันจัดทำ</p>'''

    all_pages = [p1, p2, p3, p3b] + branch_pages + [p4]
    total_pages = len(all_pages)
    body = "".join(page(p, i + 1, gen_dt, total=total_pages) for i, p in enumerate(all_pages))
    return (f'<!doctype html><html lang="th"><head><meta charset="utf-8"><title>Procurement Report {esc(m)}</title>'
            f'<style>{CSS}</style></head><body>{body}</body></html>')


def to_pdf(html_path: Path, pdf_path: Path) -> bool:
    if not CHROME.exists():
        return False
    subprocess.run([str(CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
                    f"--print-to-pdf={pdf_path}", "--print-to-pdf-no-header",
                    "--run-all-compositor-stages-before-draw", "--virtual-time-budget=10000",
                    html_path.resolve().as_uri()], check=True, timeout=60)
    return pdf_path.exists()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs")
    ap.add_argument("--no-pdf", action="store_true")
    args = ap.parse_args()

    d = pull_data()
    today = dt.date.today()
    gen_dt = f"{today.day} {THAI_MONTHS[today.month - 1]} {today.year + 543}"

    out_dir = HERE.parent / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    m = d["latest_full_month"]
    html_path = out_dir / f"PROCUREMENT_REPORT_{m}.html"
    html_path.write_text(build_html(d, gen_dt), encoding="utf-8")
    print(f"wrote {html_path}")

    if not args.no_pdf:
        pdf_path = html_path.with_suffix(".pdf")
        if to_pdf(html_path, pdf_path):
            print(f"wrote {pdf_path}")
        else:
            print("PDF skipped (Chrome not found)")

    # copy the live per-branch order-plan workbook alongside the report
    from fastapi.testclient import TestClient
    import main as appmod
    client = TestClient(appmod.app)
    r = client.get("/api/ingredients/order-plan-by-branch.xlsx")
    xlsx_path = out_dir / f"PROCUREMENT_REPORT_{m}_order_plan_by_branch.xlsx"
    xlsx_path.write_bytes(r.content)
    print(f"wrote {xlsx_path}")


if __name__ == "__main__":
    main()
