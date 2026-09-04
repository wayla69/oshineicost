#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a world-class HTML business analysis report for Oshinei Enterprise.

Reads live figures from the running dashboard API (default http://127.0.0.1:8010),
computes derived metrics + opportunity sizing, and writes a self-contained,
print-friendly HTML report to docs/.

Usage:
    python gen_analysis_report.py [--api http://127.0.0.1:8010] [--date 2026-06-02]

All numbers are pulled from the same unified-COGS endpoints the dashboard uses,
so the report reconciles exactly with the app (no hand-typed figures).
"""
import argparse, json, os, urllib.request

# ── data access ─────────────────────────────────────────────────────────────
def fetch(api, path):
    with urllib.request.urlopen(api + path, timeout=60) as r:
        return json.load(r)

# ── formatting helpers ──────────────────────────────────────────────────────
def thb(n):
    return "฿" + format(int(round(n or 0)), ",")

def thb_m(n):
    return "฿{:,.1f}M".format((n or 0) / 1_000_000)

def pct(n, d=1):
    return "—" if n is None else f"{n:.{d}f}%"

def mom(curr, prev, good_up=True, as_pts=False):
    """Month-on-month delta pill. as_pts → percentage-point change (for ratios
    like GP%/food%); otherwise a % growth. Colour reflects good vs bad direction."""
    if curr is None or prev in (None, 0):
        return '<span class="mom" style="color:var(--mut)">—</span>'
    if as_pts:
        d = curr - prev
        txt = f"{d:+.1f} จุด MoM"
    else:
        d = (curr / prev - 1) * 100
        txt = f"{d:+.1f}% MoM"
    rising = d >= 0
    good = rising if good_up else (not rising)
    color = "var(--em)" if good else "var(--rd)"
    arrow = "▲" if rising else "▼"
    return f'<span class="mom" style="color:{color};font-weight:700">{arrow} {txt}</span>'

def bar(value, vmax, color, label="", width=160):
    w = 0 if not vmax else max(1, round(value / vmax * width))
    return (f'<div class="bar-wrap"><div class="bar" style="width:{w}px;background:{color}"></div>'
            f'<span class="bar-lbl">{label}</span></div>')

def svg_line(series, color, fill, height=230, width=960, fmt=thb_m, y_suffix="",
             y_is_pct=False, target=None):
    """series = list of (label, value). Returns an SVG line+area chart."""
    vals = [v for _, v in series]
    if not vals:
        return ""
    lo = min(vals); hi = max(vals)
    if y_is_pct:
        lo = min(lo, (target - 5) if target else lo); hi = max(hi, (target + 5) if target else hi)
    pad = (hi - lo) * 0.12 or 1
    lo -= pad; hi += pad
    L, R, T, B = 56, 16, 18, 40
    iw, ih = width - L - R, height - T - B
    def x(i): return L + (iw * i / max(1, len(series) - 1))
    def y(v): return T + ih - (ih * (v - lo) / (hi - lo or 1))
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, (_, v) in enumerate(series))
    area = f"{L},{T+ih} " + pts + f" {x(len(series)-1):.1f},{T+ih}"
    # gridlines + y labels (4)
    grid = ""
    for g in range(5):
        gv = lo + (hi - lo) * g / 4
        gy = y(gv)
        lbl = (f"{gv:.0f}{y_suffix}" if y_is_pct else fmt(gv))
        grid += (f'<line x1="{L}" y1="{gy:.1f}" x2="{width-R}" y2="{gy:.1f}" class="grid"/>'
                 f'<text x="{L-8}" y="{gy+4:.1f}" class="ytick">{lbl}</text>')
    tline = ""
    if target is not None:
        ty = y(target)
        tline = (f'<line x1="{L}" y1="{ty:.1f}" x2="{width-R}" y2="{ty:.1f}" class="target"/>'
                 f'<text x="{width-R}" y="{ty-5:.1f}" class="tlbl">เป้า {target:.0f}{y_suffix}</text>')
    xlabels = ""
    step = max(1, len(series) // 13)
    for i, (lab, _) in enumerate(series):
        if i % step == 0 or i == len(series) - 1:
            xlabels += f'<text x="{x(i):.1f}" y="{height-12}" class="xtick">{lab[2:] if len(lab)>5 else lab}</text>'
    dots = "".join(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="3" fill="{color}"/>'
                   for i, (_, v) in enumerate(series))
    return (f'<svg viewBox="0 0 {width} {height}" class="chart" preserveAspectRatio="xMidYMid meet">'
            f'{grid}{tline}<polygon points="{area}" fill="{fill}"/>'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.5"/>'
            f'{dots}{xlabels}</svg>')

# ── main ────────────────────────────────────────────────────────────────────
def build(api, gen_date, group, scope_label, out_name):
    """Build one analysis-report HTML for a branch scope.

    group ∈ {None, 'master', 'franchise'}: None = whole chain (overview);
    otherwise only that branch group. Every API call is scoped identically so the
    three reports (overview / master / franchise) reconcile with the dashboard's
    own master/franchise filter."""
    gq = f"?group={group}" if group else ""
    profit   = fetch(api, "/api/profit/summary" + gq)
    gpb      = fetch(api, "/api/gp/by-branch-month" + gq)
    buf      = fetch(api, "/api/buffet/performance" + gq)["kpis"]
    buf_ft   = fetch(api, "/api/buffet/performance" + gq)["food_cost_trend"]
    me       = fetch(api, "/api/cost/analysis" + gq)
    bom      = fetch(api, "/api/bom-cost/overview" + gq)
    cats     = fetch(api, "/api/sales/category" + gq)

    grand = gpb["grand_total"]
    branches = sorted(gpb["rows"], key=lambda r: -(r.get("total_revenue") or 0))
    mt = profit["monthly_trend"]
    period = f"{mt[0]['period_month']} – {mt[-1]['period_month']}"
    n_months = len(mt)

    # derived — full-period structural figures (used only by the menu §6 and
    # ingredient §7 sections, which are mix-of-period by nature and labelled so)
    buffet_rev = next((c["revenue"] for c in cats if c.get("category") == "BUFFET"), 0)
    buffet_share = buffet_rev / grand["revenue"] * 100
    TARGET_GP = 40.0
    ings = bom.get("top_ingredients", [])[:12]
    salmon_cost = sum(i["total_cost"] for i in ings if "แซลมอน" in (i["ingredient_name"] or ""))
    salmon_pct = sum(i["pct_of_chain_cogs"] for i in ings if "แซลมอน" in (i["ingredient_name"] or ""))
    salmon_save = salmon_cost * 0.05

    items = me["items"]
    from collections import Counter
    qc = Counter(i.get("quadrant") for i in items)
    stars = sorted([i for i in items if i.get("quadrant") == "star"],
                   key=lambda i: -(i.get("revenue") or 0))[:8]
    s = me["summary"]

    # ── MONTH-ON-MONTH basis (house rule: analyse per month, never cumulative) ──
    latest_month = mt[-1]["period_month"]
    prev_month   = mt[-2]["period_month"] if n_months >= 2 else None
    last_mt = mt[-1]                       # {revenue, cogs, gross_profit, margin_pct,…}
    prev_mt = mt[-2] if n_months >= 2 else None

    def _branch_month_stats(b, month):
        """Revenue/GP for one branch in one YYYY-MM (None if no sales that month)."""
        if not month:
            return None
        rev = cogs = 0.0; seen = False
        for m in b["monthly"]:
            if m["month"] == month:
                seen = True
                rev  += m.get("revenue") or 0
                cogs += m.get("cogs") or 0
        if not seen or rev <= 0:
            return None
        gp = rev - cogs
        return {"revenue": rev, "cogs": cogs, "gp": gp,
                "gp_pct": gp / rev * 100, "food_cost_pct": cogs / rev * 100}

    for b in branches:
        b["_cur"]  = _branch_month_stats(b, latest_month)
        b["_prev"] = _branch_month_stats(b, prev_month)

    active = [b for b in branches if b["_cur"]]          # branches with sales this month
    active.sort(key=lambda b: -b["_cur"]["revenue"])
    gp_gap = (max(b["_cur"]["gp_pct"] for b in active)
              - min(b["_cur"]["gp_pct"] for b in active)) if active else 0
    # Uplift if every sub-target branch reached TARGET_GP — computed on the latest
    # month, annualised ×12 for the "/ปี" headline (no cumulative period figures).
    equal_uplift_mo = sum(b["_cur"]["revenue"] * (TARGET_GP - b["_cur"]["gp_pct"]) / 100
                          for b in active if b["_cur"]["gp_pct"] < TARGET_GP)
    equal_uplift = equal_uplift_mo * 12
    best  = sorted(active, key=lambda b: -b["_cur"]["gp_pct"])[:3]
    worst = sorted(active, key=lambda b: b["_cur"]["gp_pct"])[:3]
    max_rev = max((b["_cur"]["revenue"] for b in active), default=1)

    # ── HTML ────────────────────────────────────────────────────────────────
    H = []
    A = H.append
    A(f"""<!DOCTYPE html><html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Oshinei Enterprise — Business Analysis Report · {period}</title>
<style>
:root{{--ink:#0f172a;--mut:#64748b;--line:#e2e8f0;--indigo:#4f46e5;--em:#10b981;--am:#f59e0b;--rd:#ef4444;--bg:#f8fafc}}
*{{box-sizing:border-box}}
body{{font-family:"Sarabun","Segoe UI",-apple-system,Roboto,sans-serif;color:var(--ink);background:var(--bg);margin:0;line-height:1.55;font-size:15px}}
.wrap{{max-width:1080px;margin:0 auto;padding:0 24px 80px}}
.cover{{background:linear-gradient(135deg,#312e81 0%,#4f46e5 55%,#7c3aed 100%);color:#fff;padding:54px 48px;border-radius:0 0 24px 24px;margin-bottom:34px}}
.cover .eyebrow{{text-transform:uppercase;letter-spacing:3px;font-size:12px;opacity:.85;font-weight:600}}
.cover h1{{font-size:36px;margin:8px 0 4px;font-weight:800;line-height:1.15}}
.cover .sub{{opacity:.9;font-size:16px}}
.cover .meta{{margin-top:20px;display:flex;gap:28px;flex-wrap:wrap;font-size:13px;opacity:.92}}
.cover .meta b{{display:block;font-size:20px;font-weight:700;opacity:1}}
section{{background:#fff;border:1px solid var(--line);border-radius:16px;padding:30px 32px;margin-bottom:24px;box-shadow:0 1px 3px rgba(15,23,42,.04)}}
h2.sec{{font-size:22px;margin:0 0 4px;font-weight:800}}
h2.sec .num{{color:var(--indigo);font-weight:800;margin-right:10px}}
.sec-sub{{color:var(--mut);font-size:13px;margin:0 0 20px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px}}
.kpi{{border:1px solid var(--line);border-radius:12px;padding:16px;background:linear-gradient(180deg,#fff,#fcfcff)}}
.kpi .lbl{{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut);font-weight:600}}
.kpi .val{{font-size:26px;font-weight:800;margin:4px 0 2px}}
.kpi .sub{{font-size:12px;color:var(--mut)}}
.callout{{border-left:4px solid var(--indigo);background:#eef2ff;border-radius:0 10px 10px 0;padding:14px 18px;margin:14px 0;font-size:14px}}
.callout.warn{{border-color:var(--am);background:#fffbeb}}
.callout.good{{border-color:var(--em);background:#ecfdf5}}
.callout.risk{{border-color:var(--rd);background:#fef2f2}}
.callout b{{font-weight:700}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}}
th,td{{text-align:right;padding:8px 10px;border-bottom:1px solid var(--line)}}
th:first-child,td:first-child{{text-align:left}}
th{{font-size:11px;text-transform:uppercase;letter-spacing:.4px;color:var(--mut);font-weight:700;border-bottom:2px solid var(--line)}}
tbody tr:hover{{background:#fafbff}}
tfoot td{{font-weight:800;border-top:2px solid var(--line);background:#f8fafc}}
.bar-wrap{{display:flex;align-items:center;gap:8px;justify-content:flex-end}}
.bar{{height:9px;border-radius:5px}}
.bar-lbl{{font-variant-numeric:tabular-nums;min-width:54px;text-align:right}}
.pill{{display:inline-block;padding:1px 9px;border-radius:999px;font-size:11px;font-weight:700}}
.chart{{width:100%;height:auto;display:block;margin:6px 0}}
.chart .grid{{stroke:#eef2f6;stroke-width:1}}
.chart .target{{stroke:var(--em);stroke-width:1.4;stroke-dasharray:4 4}}
.chart .ytick{{fill:#94a3b8;font-size:11px;text-anchor:end}}
.chart .tlbl{{fill:var(--em);font-size:11px;text-anchor:end;font-weight:700}}
.chart .xtick{{fill:#94a3b8;font-size:10px;text-anchor:middle}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:22px}}
.rec{{border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:14px;position:relative}}
.rec .tag{{position:absolute;top:-10px;left:16px;font-size:11px;font-weight:800;padding:2px 10px;border-radius:999px;color:#fff}}
.rec h4{{margin:6px 0 6px;font-size:16px}}
.rec .impact{{font-size:13px;color:var(--em);font-weight:700}}
.rec ol{{margin:8px 0 0;padding-left:20px;font-size:13px;color:#334155}}
.muted{{color:var(--mut);font-size:12px}}
.toc{{columns:2;font-size:13px;gap:30px}}
.toc a{{color:var(--indigo);text-decoration:none;display:block;padding:3px 0}}
footer{{text-align:center;color:var(--mut);font-size:12px;margin-top:30px}}
@media print{{body{{background:#fff;font-size:11.5px}}.cover{{border-radius:0}}section{{break-inside:avoid;box-shadow:none;border-color:#ddd}}.no-print{{display:none}}}}
@media(max-width:740px){{.grid2{{grid-template-columns:1fr}}.toc{{columns:1}}}}
</style></head><body>""")

    # Cover
    A(f"""<div class="cover"><div class="wrap" style="padding:0">
  <div class="eyebrow">Invisible Consulting · Enterprise ERP Analytics · {scope_label}</div>
  <h1>Oshinei Enterprise<br>รายงานวิเคราะห์ธุรกิจ</h1>
  <div class="sub">เครือบุฟเฟ่ต์ปลาแซลมอน · ขอบเขต: <b>{scope_label}</b> · {len(branches)} สาขา · ข้อมูล Easy POS · วิเคราะห์แบบเดือนต่อเดือน (MoM)</div>
  <div class="meta">
    <div><b>{thb_m(last_mt['revenue'])}</b>รายได้เดือนล่าสุด · {latest_month}<br>{mom(last_mt['revenue'], prev_mt['revenue'] if prev_mt else None)}</div>
    <div><b>{pct(last_mt['margin_pct'])}</b>GP เดือนล่าสุด<br>{mom(last_mt['margin_pct'], prev_mt['margin_pct'] if prev_mt else None, as_pts=True)}</div>
    <div><b>{len(active)}</b>สาขาที่มียอดขายเดือนนี้</div>
    <div><b>{period}</b>ช่วงข้อมูล ({n_months} เดือน)</div>
    <div><b>{gen_date}</b>วันที่ออกรายงาน</div>
  </div>
</div></div><div class="wrap">""")

    # TOC
    A("""<section class="no-print"><h2 class="sec"><span class="num">»</span>สารบัญ</h2>
    <div class="toc">
      <a href="#s1">1 · บทสรุปผู้บริหาร (Executive Summary)</a>
      <a href="#s2">2 · ดัชนีชี้วัดหลัก (Scorecard)</a>
      <a href="#s3">3 · รายได้และการเติบโต</a>
      <a href="#s4">4 · ผลการดำเนินงานรายสาขา</a>
      <a href="#s5">5 · เศรษฐศาสตร์บุฟเฟ่ต์</a>
      <a href="#s6">6 · วิเคราะห์เมนูและมาร์จิ้น</a>
      <a href="#s7">7 · ความเสี่ยงด้านวัตถุดิบ</a>
      <a href="#s8">8 · ข้อเสนอเชิงกลยุทธ์</a>
      <a href="#s9">9 · คุณภาพข้อมูลและระเบียบวิธี</a>
    </div></section>""")

    # §1 Executive summary  (month-on-month)
    last3_avg = sum(m['revenue'] for m in mt[-3:]) / min(3, n_months)
    peak = max(mt, key=lambda m: m['revenue'])
    last_fc = (last_mt['cogs'] / last_mt['revenue'] * 100) if last_mt['revenue'] else None
    prev_fc = (prev_mt['cogs'] / prev_mt['revenue'] * 100) if (prev_mt and prev_mt['revenue']) else None
    bf_vals = [x for x in buf_ft if x['cost_pct'] is not None]
    bf_cur  = bf_vals[-1]['cost_pct'] if bf_vals else None
    bf_prev = bf_vals[-2]['cost_pct'] if len(bf_vals) >= 2 else None
    A(f"""<section id="s1"><h2 class="sec"><span class="num">1</span>บทสรุปผู้บริหาร</h2>
    <p class="sec-sub">วิเคราะห์แบบเดือนต่อเดือน (MoM) — โฟกัสเดือนล่าสุดและการเปลี่ยนแปลงเทียบเดือนก่อน</p>
    <p>เดือนล่าสุด <b>{latest_month}</b> เครือ Oshinei ทำรายได้ <b>{thb_m(last_mt['revenue'])}</b> (ก่อน VAT)
    {mom(last_mt['revenue'], prev_mt['revenue'] if prev_mt else None)} · กำไรขั้นต้น <b>{thb_m(last_mt['gross_profit'])}</b>
    ที่มาร์จิ้น <b>{pct(last_mt['margin_pct'])}</b> {mom(last_mt['margin_pct'], prev_mt['margin_pct'] if prev_mt else None, as_pts=True)}
    (food cost {pct(last_fc)} {mom(last_fc, prev_fc, good_up=False, as_pts=True)}) จาก {len(active)} สาขาที่เปิดขายในเดือนนี้ ·
    ค่าเฉลี่ย 3 เดือนล่าสุด ~<b>{thb_m(last3_avg)}</b>/เดือน</p>
    <div class="callout good">📈 <b>โมเมนตัมรายเดือน</b> — รายได้ {thb_m(prev_mt['revenue']) if prev_mt else '—'} → {thb_m(last_mt['revenue'])} ({mom(last_mt['revenue'], prev_mt['revenue'] if prev_mt else None)}); จุดสูงสุดอยู่เดือน <b>{peak['period_month']}</b> ที่ {thb_m(peak['revenue'])}</div>
    <div class="callout">🍱 <b>บุฟเฟ่ต์คือหัวใจ ({pct(buffet_share)} ของรายได้)</b> — food cost บุฟเฟ่ต์เดือนล่าสุด {pct(bf_cur)} {mom(bf_cur, bf_prev, good_up=False, as_pts=True)} (เทรนด์รายเดือนในข้อ 5)</div>
    <div class="callout risk">🐟 <b>ความเสี่ยงกระจุกตัว: ปลาแซลมอน = {pct(salmon_pct)} ของต้นทุนวัตถุดิบ</b> ({thb_m(salmon_cost)}, รวมทั้งช่วง) — วัตถุดิบเดียวกำหนดกำไรของทั้งธุรกิจ ราคาผันผวน = ความเสี่ยงสูงสุด</div>
    <div class="callout warn">🏪 <b>ช่องว่างระหว่างสาขาเดือน {latest_month} กว้าง {gp_gap:.1f} จุด</b> (GP {min(b['_cur']['gp_pct'] for b in active):.1f}% – {max(b['_cur']['gp_pct'] for b in active):.1f}%) — ยกสาขาล่างขึ้นสู่ค่ากลาง {TARGET_GP:.0f}% = โอกาสกำไรเพิ่ม ~{thb_m(equal_uplift_mo)}/เดือน (~{thb_m(equal_uplift)}/ปี)</div>
    </section>""")

    # §2 scorecard  (latest month + MoM; period-only metrics shown as /เดือน average)
    cov = profit.get("cost_coverage_pct")
    rcpt_mo  = profit['tx_count'] / n_months
    covers_mo = buf['total_pax'] / n_months
    A(f"""<section id="s2"><h2 class="sec"><span class="num">2</span>ดัชนีชี้วัดหลัก — เดือนล่าสุด ({latest_month})</h2>
    <p class="sec-sub">เทียบเดือนก่อน (MoM) · COGS สูตรเดียว (BoM → sku_cost) · รายได้ใช้ subtotal_ex_vat · ตัวเลขที่ไม่มีอนุกรมรายเดือนแสดงเป็นค่าเฉลี่ย/เดือน</p>
    <div class="kpis">
      <div class="kpi"><div class="lbl">รายได้ (ex-VAT)</div><div class="val">{thb_m(last_mt['revenue'])}</div><div class="sub">{mom(last_mt['revenue'], prev_mt['revenue'] if prev_mt else None)}</div></div>
      <div class="kpi"><div class="lbl">กำไรขั้นต้น</div><div class="val" style="color:var(--em)">{thb_m(last_mt['gross_profit'])}</div><div class="sub">{pct(last_mt['margin_pct'])} · {mom(last_mt['margin_pct'], prev_mt['margin_pct'] if prev_mt else None, as_pts=True)}</div></div>
      <div class="kpi"><div class="lbl">Food Cost</div><div class="val" style="color:var(--am)">{pct(last_fc)}</div><div class="sub">{mom(last_fc, prev_fc, good_up=False, as_pts=True)} · เป้า 55–70%</div></div>
      <div class="kpi"><div class="lbl">ใบเสร็จ/เดือน (เฉลี่ย)</div><div class="val">{format(int(round(rcpt_mo)),',')}</div><div class="sub">{thb(profit['avg_ticket'])}/บิล</div></div>
      <div class="kpi"><div class="lbl">ลูกค้า/เดือน (เฉลี่ย)</div><div class="val">{format(int(round(covers_mo)),',')}</div><div class="sub">{buf['avg_pax_per_bill']}/บิล</div></div>
      <div class="kpi"><div class="lbl">กำไร/บิล</div><div class="val" style="color:var(--em)">{thb(profit['profit_per_bill'])}</div><div class="sub">เฉลี่ยทั้งช่วง</div></div>
      <div class="kpi"><div class="lbl">สาขาเปิดขายเดือนนี้</div><div class="val">{len(active)}</div><div class="sub">จากทั้งหมด {len(branches)}</div></div>
      <div class="kpi"><div class="lbl">ความครอบคลุมต้นทุน</div><div class="val">{pct(cov)}</div><div class="sub">ของจำนวนเสิร์ฟ</div></div>
    </div></section>""")

    # §3 revenue trend
    rev_series = [(m['period_month'], m['revenue']) for m in mt]
    gp_series  = [(m['period_month'], m['margin_pct']) for m in mt if m['margin_pct'] is not None]
    A(f"""<section id="s3"><h2 class="sec"><span class="num">3</span>รายได้และกำไรรายเดือน (MoM)</h2>
    <p class="sec-sub">รายได้รายเดือน (ex-VAT) · {period}</p>
    {svg_line(rev_series, "#4f46e5", "rgba(79,70,229,.10)")}
    <p>เดือนล่าสุด <b>{latest_month}</b> รายได้ <b>{thb_m(last_mt['revenue'])}</b>
    {mom(last_mt['revenue'], prev_mt['revenue'] if prev_mt else None)} เทียบเดือนก่อน ({thb_m(prev_mt['revenue']) if prev_mt else '—'});
    จุดสูงสุดอยู่เดือน <b>{peak['period_month']}</b> ที่ {thb_m(peak['revenue'])} · ค่าเฉลี่ย 3 เดือนล่าสุด ~{thb_m(last3_avg)}/เดือน</p>
    <p class="sec-sub" style="margin:14px 0 2px">มาร์จิ้นกำไรขั้นต้นรายเดือน (%)</p>
    {svg_line(gp_series, "#10b981", "rgba(16,185,129,.10)", y_is_pct=True, y_suffix="%")}
    <p>มาร์จิ้นเดือนล่าสุด <b>{pct(last_mt['margin_pct'])}</b>
    {mom(last_mt['margin_pct'], prev_mt['margin_pct'] if prev_mt else None, as_pts=True)} เทียบเดือนก่อน ({pct(prev_mt['margin_pct']) if prev_mt else '—'})</p>
    </section>""")

    # §4 branches — latest month with MoM revenue change (no cumulative totals)
    rows_html = ""
    for b in active:
        cur = b["_cur"]; prev = b["_prev"]
        fc = cur["food_cost_pct"]
        fccol = "var(--em)" if 40 <= fc <= 62 else ("var(--rd)" if fc > 67 else "var(--am)")
        rows_html += (f"<tr><td>{b['branch']}</td>"
                      f"<td>{bar(cur['revenue'],max_rev,'#4f46e5',thb_m(cur['revenue']))}</td>"
                      f"<td>{thb_m(cur['gp'])}</td>"
                      f"<td><b>{cur['gp_pct']:.1f}%</b></td>"
                      f"<td style='color:{fccol};font-weight:700'>{fc:.1f}%</td>"
                      f"<td>{mom(cur['revenue'], prev['revenue'] if prev else None)}</td></tr>")
    A(f"""<section id="s4"><h2 class="sec"><span class="num">4</span>ผลการดำเนินงานรายสาขา — เดือนล่าสุด ({latest_month})</h2>
    <p class="sec-sub">เรียงตามรายได้เดือนล่าสุด · {len(active)} สาขาที่เปิดขายเดือนนี้ · MoM = เทียบ {prev_month or '—'} · COGS แบบ BoM</p>
    <div class="grid2" style="margin-bottom:16px">
      <div class="callout good">🏆 <b>GP สูงสุด (เดือนนี้):</b> {', '.join(f"{x['branch']} ({x['_cur']['gp_pct']:.1f}%)" for x in best)}</div>
      <div class="callout warn">⚠️ <b>GP ต่ำสุด (เดือนนี้):</b> {', '.join(f"{x['branch']} ({x['_cur']['gp_pct']:.1f}%)" for x in worst)}</div>
    </div>
    <table><thead><tr><th>สาขา</th><th>รายได้ (เดือน)</th><th>กำไรขั้นต้น</th><th>GP%</th><th>Food%</th><th>รายได้ MoM</th></tr></thead>
    <tbody>{rows_html}</tbody>
    <tfoot><tr><td>รวมเดือน {latest_month} ({len(active)} สาขา)</td><td style="text-align:right">{thb_m(last_mt['revenue'])}</td><td>{thb_m(last_mt['gross_profit'])}</td><td>{last_mt['margin_pct']:.1f}%</td><td>{pct(last_fc)}</td><td>{mom(last_mt['revenue'], prev_mt['revenue'] if prev_mt else None)}</td></tr></tfoot>
    </table>
    <div class="callout">💡 <b>โอกาสจากการยกระดับสาขา (ฐานเดือนล่าสุด):</b> หากทุกสาขาที่ GP ต่ำกว่า {TARGET_GP:.0f}% ขยับขึ้นถึง {TARGET_GP:.0f}%
    จะเพิ่มกำไรขั้นต้น ~<b>{thb_m(equal_uplift_mo)}/เดือน</b> (~{thb_m(equal_uplift)}/ปี) โดยไม่ต้องเพิ่มยอดขาย — ถ่ายทอด best practice จากสาขาเก่งเดือนนี้ ({', '.join(x['branch'] for x in best[:2])}) สู่สาขาอ่อน</div>
    </section>""")

    # §5 buffet
    bf_series = [(x['period'], x['cost_pct']) for x in buf_ft if x['cost_pct'] is not None]
    A(f"""<section id="s5"><h2 class="sec"><span class="num">5</span>เศรษฐศาสตร์บุฟเฟ่ต์</h2>
    <p class="sec-sub">หัวใจของธุรกิจ — {pct(buffet_share)} ของรายได้ทั้งเครือ</p>
    <div class="kpis" style="margin-bottom:18px">
      <div class="kpi"><div class="lbl">ลูกค้า/เดือน (เฉลี่ย)</div><div class="val">{format(int(round(buf['total_pax']/n_months)),',')}</div><div class="sub">{buf['avg_pax_per_bill']} คน/บิล</div></div>
      <div class="kpi"><div class="lbl">ผู้ใหญ่ / เด็ก</div><div class="val">{100-buf['child_pct']:.0f}/{buf['child_pct']:.0f}</div><div class="sub">เด็ก {buf['child_pct']:.1f}%</div></div>
      <div class="kpi"><div class="lbl">Food Cost บุฟเฟ่ต์ (เดือนล่าสุด)</div><div class="val" style="color:var(--am)">{pct(bf_cur)}</div><div class="sub">{mom(bf_cur, bf_prev, good_up=False, as_pts=True)}</div></div>
      <div class="kpi"><div class="lbl">เครื่องดื่ม (attach)</div><div class="val">~80%</div><div class="sub">ของบิลสั่งดื่ม · {pct(buf['beverage_attach_pct'])} ของรายได้</div></div>
    </div>
    <p class="sec-sub" style="margin-bottom:2px">แนวโน้ม Food Cost บุฟเฟ่ต์รายเดือน (เป้า 55–70%)</p>
    {svg_line(bf_series, "#ec4899", "rgba(236,72,153,.10)", y_is_pct=True, y_suffix="%", target=65)}
    <div class="callout good">✅ ต้นทุนอาหารบุฟเฟ่ต์รายเดือนปรับลดจาก <b>{pct(bf_series[0][1])} ({bf_series[0][0]}) → {pct(bf_cur)} ({bf_vals[-1]['period']})</b> · เดือนล่าสุด {mom(bf_cur, bf_prev, good_up=False, as_pts=True)} — สะท้อนการคุม portion / เจรจาซัพพลายเออร์ที่ได้ผล</div>
    <div class="callout warn">🥤 <b>เครื่องดื่มเป็นโอกาสที่ยังไม่ถูกใช้เต็มที่</b> — ~80% ของบิลสั่งเครื่องดื่ม แต่คิดเป็นเพียง {pct(buf['beverage_attach_pct'])} ของรายได้ การดันเครื่องดื่มมาร์จิ้นสูง (เครื่องดื่มพิเศษ/แอลกอฮอล์) เพิ่มกำไรได้โดยไม่กระทบต้นทุนอาหาร</div>
    </section>""")

    # §6 menu engineering
    stars_html = "".join(f"<tr><td>{i.get('description') or i.get('sku_code')}</td>"
                         f"<td>{format(int(i.get('qty') or 0),',')}</td>"
                         f"<td>{thb(i.get('revenue') or 0)}</td>"
                         f"<td><b>{(i.get('margin_pct') or 0):.0f}%</b></td></tr>" for i in stars)
    A(f"""<section id="s6"><h2 class="sec"><span class="num">6</span>วิเคราะห์เมนูและมาร์จิ้น</h2>
    <p class="sec-sub">à-la-carte {s['total_skus']} เมนู · จัดกลุ่มได้ {qc['star']+qc['plowhorse']+qc['puzzle']+qc['dog']} เมนูที่มีข้อมูลต้นทุน
    · <b>ข้อมูลรวมทั้งช่วง {period}</b> (mix สะสม — ไม่ใช่รายเดือน)</p>
    <div class="kpis" style="margin-bottom:18px">
      <div class="kpi"><div class="lbl">⭐ ดาวเด่น</div><div class="val" style="color:var(--em)">{qc['star']}</div><div class="sub">ขายดี+กำไรดี</div></div>
      <div class="kpi"><div class="lbl">🐴 ม้างาน</div><div class="val" style="color:var(--am)">{qc['plowhorse']}</div><div class="sub">ขายดี กำไรบาง</div></div>
      <div class="kpi"><div class="lbl">🔮 เพชรในตม</div><div class="val" style="color:var(--indigo)">{qc['puzzle']}</div><div class="sub">กำไรดี ขายน้อย</div></div>
      <div class="kpi"><div class="lbl">🐕 ตัวถ่วง</div><div class="val" style="color:var(--rd)">{qc['dog']}</div><div class="sub">พิจารณาถอด</div></div>
    </div>
    <p class="sec-sub" style="margin-bottom:2px">เมนูดาวเด่น (à-la-carte) — รายได้สูงสุด</p>
    <table><thead><tr><th>เมนู</th><th>จำนวนขาย</th><th>รายได้</th><th>มาร์จิ้น</th></tr></thead><tbody>{stars_html}</tbody></table>
    <div class="callout">🍣 เมนู à-la-carte ที่ทำกำไรล้วนเป็น<b>กลุ่มแซลมอน/ซีฟู้ด</b> — สอดคล้องกับจุดขายของแบรนด์ ควรชู เมนูเหล่านี้ให้เด่นและขยายเป็นเซ็ต/โปรโมชัน</div>
    </section>""")

    # §7 ingredient risk
    imax = max(i["total_cost"] for i in ings)
    ing_html = "".join(f"<tr><td>{i['ingredient_name']}</td>"
                       f"<td><span class='pill' style='background:#eef2ff;color:#4f46e5'>{i['ingredient_type']}</span></td>"
                       f"<td>{bar(i['total_cost'],imax,'#ef4444' if 'แซลมอน' in (i['ingredient_name'] or '') else '#94a3b8',thb_m(i['total_cost']))}</td>"
                       f"<td><b>{i['pct_of_chain_cogs']:.1f}%</b></td></tr>" for i in ings)
    A(f"""<section id="s7"><h2 class="sec"><span class="num">7</span>ความเสี่ยงด้านวัตถุดิบ (Supply Concentration)</h2>
    <p class="sec-sub">วัตถุดิบ 12 อันดับแรกตามสัดส่วนต้นทุน · <b>ข้อมูลรวมทั้งช่วง {period}</b> (mix สะสม — ไม่ใช่รายเดือน)</p>
    <table><thead><tr><th>วัตถุดิบ</th><th>ประเภท</th><th>ต้นทุนรวม</th><th>% ของ COGS</th></tr></thead><tbody>{ing_html}</tbody></table>
    <div class="callout risk">🐟 <b>ปลาแซลมอนคิดเป็น {pct(salmon_pct)} ของต้นทุนวัตถุดิบทั้งเครือ</b> ({thb_m(salmon_cost)} ตลอดช่วง {period})
    — วัตถุดิบเดียวที่กำหนดความอยู่รอดของกำไร หากราคาแซลมอนขึ้น 10% กำไรขั้นต้นหายไป ~{thb_m(salmon_cost*0.10)} ต่อช่วงข้อมูลทันที</div>
    <div class="callout good">💰 <b>โอกาส:</b> ลดต้นทุนแซลมอนเพียง 5% (เจรจาสัญญาระยะยาว / dual-source / คุม yield การขูดเนื้อ) = กำไรเพิ่ม ~<b>{thb_m(salmon_save)} ต่อช่วงข้อมูล</b></div>
    </section>""")

    # §8 recommendations
    def rec(color, tag, title, impact, steps):
        li = "".join(f"<li>{x}</li>" for x in steps)
        return (f'<div class="rec"><span class="tag" style="background:{color}">{tag}</span>'
                f'<h4>{title}</h4><div class="impact">💵 {impact}</div><ol>{li}</ol></div>')
    A(f"""<section id="s8"><h2 class="sec"><span class="num">8</span>ข้อเสนอเชิงกลยุทธ์</h2>
    <p class="sec-sub">เรียงตามผลกระทบต่อกำไร · ตัวเลขโอกาสคำนวณจากข้อมูลจริง</p>
    {rec('#ef4444','ลำดับ 1 · เร่งด่วน','บริหารความเสี่ยงปลาแซลมอน (40% ของต้นทุน)',
         f'ป้องกันความเสี่ยง {thb_m(salmon_cost)} · โอกาสเพิ่มกำไร ~{thb_m(salmon_save)}/ปี',
         ['ล็อกสัญญาราคาระยะยาวกับซัพพลายเออร์หลัก + หาแหล่งสำรอง (dual-source)',
          'คุม yield การขูด/ตัดเนื้อแซลมอนต่อจาน (ใช้ BoM Cost Simulator จำลองผล)',
          'ทดสอบเมนูโปรตีนทางเลือก (ทูน่า/หอยเชลล์) เพื่อกระจายความเสี่ยงในเมนูบุฟเฟ่ต์'])}
    {rec('#f59e0b','ลำดับ 2 · ผลกระทบสูง','ยกระดับสาขาที่กำไรต่ำสู่ค่ากลางเครือ',
         f'กำไรเพิ่ม ~{thb_m(equal_uplift_mo)}/เดือน (~{thb_m(equal_uplift)}/ปี) โดยไม่ต้องเพิ่มยอดขาย',
         [f'โฟกัส 3 สาขาล่างเดือน {latest_month}: {", ".join(x["branch"] for x in worst)} (GP {worst[-1]["_cur"]["gp_pct"]:.0f}–{worst[0]["_cur"]["gp_pct"]:.0f}%)',
          f'ถอด best practice จากสาขาเก่งเดือนนี้ ({", ".join(x["branch"] for x in best[:2])}) — portion control, การสั่งซื้อ, waste',
          'ใช้ P&L รายสาขา + BoM เปรียบเทียบ หา เมนู/วัตถุดิบที่ทำให้ food cost สูงผิดปกติ']) }
    {rec('#10b981','ลำดับ 3 · กำไรเพิ่ม','ดันยอดเครื่องดื่ม (profit center)',
         f'เครื่องดื่ม attach ~80% แต่เพียง {pct(buf["beverage_attach_pct"])} ของรายได้ — มี upside สูง',
         ['ฝึกพนักงานเสนอเครื่องดื่มมาร์จิ้นสูง (พิเศษ/แอลกอฮอล์) ทุกโต๊ะ',
          'ออกเซ็ตเครื่องดื่ม + โปรโมชันคู่บุฟเฟ่ต์',
          'เติมต้นทุนเครื่องดื่มในระบบเพื่อวัดกำไรจริง (ดูข้อ 9)'])}
    {rec('#4f46e5','ลำดับ 4 · เสริมความแม่นยำ','ปิดช่องว่างข้อมูลต้นทุน',
         f'ความครอบคลุมปัจจุบัน {pct(cov)} → เป้า 90%+',
         ['เติมต้นทุนเครื่องดื่ม (เกือบทั้งหมดยังไม่มี cost → มาร์จิ้นโชว์ 100% ปลอม)',
          'แก้ข้อมูล à-la-carte ที่ unit_cost ≈ ราคาขาย (ทำให้บางเมนูมาร์จิ้นติดลบ)',
          'ตั้งรอบทบทวนต้นทุนรายไตรมาสผ่าน Settings → Cost Recalibration'])}
    </section>""")

    # §9 data quality
    A(f"""<section id="s9"><h2 class="sec"><span class="num">9</span>คุณภาพข้อมูลและระเบียบวิธี</h2>
    <p class="sec-sub">ความโปร่งใสของข้อมูล — เพื่อให้ตัวเลขทุกตัวตรวจสอบย้อนกลับได้</p>
    <ul style="font-size:14px;color:#334155">
      <li><b>แหล่งข้อมูล:</b> Easy POS (easyrestaurant.asia) · {len(branches)} สาขา · {period} ({n_months} เดือน)</li>
      <li><b>ฐานการวิเคราะห์:</b> เดือนต่อเดือน (MoM) — ตัวเลขหลักคือ<b>เดือนล่าสุด {latest_month}</b> พร้อมการเปลี่ยนแปลงเทียบเดือนก่อน ({prev_month or '—'}) · <u>ไม่ใช้</u>ยอดสะสมทั้งช่วงเป็นตัวชี้วัด · ข้อ 6–7 (เมนู/วัตถุดิบ) เป็น mix สะสมทั้งช่วงโดยธรรมชาติ จึงระบุกำกับไว้</li>
      <li><b>รายได้</b> ใช้ <code>subtotal_ex_vat</code> (ก่อน VAT 7%) เสมอ — VAT ไม่นับเป็นรายได้</li>
      <li><b>COGS</b> สูตรเดียวทั้งระบบ: BoM recipe ก่อน → ถ้าไม่มีใช้ sku_cost → buffet tier นับ 0 (กัน double-count). ทุกหน้า/รายงาน GP ตรงกันที่ {pct(grand['gp_pct'])}</li>
      <li><b>ความครอบคลุมต้นทุน {pct(cov)}</b> ของจำนวนเสิร์ฟ — ส่วนที่เหลือยังไม่มีรีซิปี/ต้นทุน</li>
    </ul>
    <div class="callout warn"><b>ข้อควรระวัง (อ่านก่อนตีความ):</b><br>
      • <b>เครื่องดื่มยังไม่มีต้นทุนเกือบทั้งหมด</b> → มาร์จิ้นเครื่องดื่มในวิเคราะห์เมนูขึ้น ~100% (ปลอม) ควรเติมก่อนนำไปตัดสินใจ<br>
      • à-la-carte บางรายการ <code>unit_cost ≈ ราคาขาย</code> → มาร์จิ้นอาจติดลบ/ผิดปกติ เป็นปัญหาคุณภาพข้อมูล ไม่ใช่ผลประกอบการจริง<br>
      • Food cost บุฟเฟ่ต์เดือนล่าสุด {pct(bf_cur)} ({latest_month}) คำนวณจากต้นทุนเมนูบุฟเฟ่ต์ (B0xxx) ÷ รายได้บุฟเฟ่ต์ · ลูกค้า (pax) ใช้ customer_count จริง</li>
    </div>
    <p class="muted">รายงานนี้สร้างอัตโนมัติจาก API ของระบบ (สูตรเดียวกับแดชบอร์ด) — ตัวเลขจึงตรงกับหน้าจอทุกประการ
    · สร้างใหม่ได้ด้วย <code>python backend/gen_analysis_report.py</code></p>
    </section>""")

    A(f"""<footer>Oshinei Enterprise ERP · Business Analysis Report · ออกรายงาน {gen_date}
    · Invisible Consulting<br>FastAPI · SQLite · unified-COGS engine</footer>
    </div></body></html>""")

    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, out_name)
    with open(out, "w", encoding="utf-8") as f:
        f.write("".join(H))
    print("Wrote", out, f"({os.path.getsize(out)//1024} KiB)")


# scope → (group, cover label, output filename)
_SCOPES = {
    "all":       (None,        "ภาพรวมทุกกลุ่ม (Master + Franchise)", "BUSINESS_ANALYSIS_2026-06.html"),
    "master":    ("master",    "เฉพาะสาขา Master",                     "BUSINESS_ANALYSIS_2026-06_master.html"),
    "franchise": ("franchise", "เฉพาะแฟรนไชส์ (Franchise)",            "BUSINESS_ANALYSIS_2026-06_franchise.html"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8010")
    ap.add_argument("--date", default=None)
    ap.add_argument("--scope", default="all", choices=["all", "master", "franchise", "everything"],
                    help="'everything' = overview + master + franchise (default builds overview only "
                         "unless 'everything').")
    args = ap.parse_args()
    api = args.api.rstrip("/")
    gen_date = args.date or "2026-06-02"

    scopes = ["all", "master", "franchise"] if args.scope == "everything" else [args.scope]
    for sc in scopes:
        group, label, out_name = _SCOPES[sc]
        build(api, gen_date, group, label, out_name)


if __name__ == "__main__":
    main()
