# -*- coding: utf-8 -*-
"""Top-60 menu COST-PER-HEAD report (ต้นทุนต่อหัว/เดือน) — master branches, one month.

Scope (default พ.ค. 2569 / 2026-05, 5 master branches):
  • Opens with the WHOLE-NETWORK overview (all master branches combined).
  • Then one section per branch, each with its OWN complete Top-60 menu table.
  • Per branch: variance of cost-per-head vs the overview, decomposed into a
    VOLUME effect (พฤติกรรมการกิน/หัว) and a UNIT-COST effect (ต้นทุน/ชิ้น —
    สูตร/ราคาวัตถุดิบ/waste), so each material gap points at WHAT to check.
  • Closes with a cross-branch "material variance" digest + an investigate /
    improve / track playbook.

COGS follows main.py's canonical helper `_menucmp_agg` (buffet-tier rows = 0,
cost-management → BoM → alias → unit_cost fallback), identical to the dashboard's
"เทียบเมนู A vs B — ต่อหัว" view. Cost-per-head = COGS ÷ covers of that scope.
Buffet food only (à-la-carte "(A)" SKUs and non-food categories excluded).

Usage:
    python backend/gen_top60_menu_perhead.py                 # → docs/TOP60_MENU_PERHEAD_2026-05.html
    OSHINEI_DB=/path/oshinei.db python backend/gen_top60_menu_perhead.py --month 2026-05
"""
import argparse, html as H, sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from main import get_db, _menucmp_agg, _FOOD_CATEGORY_FILTER  # noqa: E402

# ───────────────────────────── config ─────────────────────────────
BRANCHES = ['โคราช1', 'เชียงใหม่', 'สุรินทร์', 'อุดรธานี', 'อุบลราชธานี']
DEFAULT_MONTH = '2026-05'
TOPN = 60                 # Top-N menus per scope
MATERIAL_BAHT = 0.30      # |Δ cost/head| ≥ this (฿/หัว) counts as "มีสาระสำคัญ"
MATERIAL_PCT = 25.0       # …or |Δ| ≥ this % of the overview per-head (whichever wider)
DEV_ROWS = 14             # material-variance rows shown per branch

TH_MON = {'01': 'ม.ค.', '02': 'ก.พ.', '03': 'มี.ค.', '04': 'เม.ย.', '05': 'พ.ค.',
          '06': 'มิ.ย.', '07': 'ก.ค.', '08': 'ส.ค.', '09': 'ก.ย.', '10': 'ต.ค.',
          '11': 'พ.ย.', '12': 'ธ.ค.'}


def month_label(m):
    y, mo = m.split('-')
    return f"{TH_MON.get(mo, mo)} {int(y) + 543}"   # Buddhist-era year


# ───────────────────────────── extraction ─────────────────────────────
def build(month):
    ph = ','.join(f':b{i}' for i in range(len(BRANCHES)))
    EW = ["b.name!='ALL'", "COALESCE(sk.active,1)=1", _FOOD_CATEGORY_FILTER,
          "COALESCE(sk.description,'') NOT LIKE '(A)%'", f"b.name IN ({ph})"]
    PB = {f'b{i}': n for i, n in enumerate(BRANCHES)}
    sel_sku = "sk.sku_id sku_id, sk.description description, c.name category"
    sel_br = "b.name br, sk.sku_id sku_id, sk.description description, c.name category"

    with get_db() as con:
        ov_rows = _menucmp_agg(con, sel_sku, "sk.sku_id", EW, PB, month, month, None, None)
        br_rows = _menucmp_agg(con, sel_br, "b.name, sk.sku_id", EW, PB, month, month, None, None)
        cov_rows = con.execute(
            f"""SELECT b.name br, SUM(t.customer_count) c
                FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
                WHERE b.name IN ({ph}) AND t.source='Easy'
                  AND strftime('%Y-%m',t.sold_at)=:m GROUP BY b.name""",
            {**PB, 'm': month}).fetchall()

    covers = {r['br']: (r['c'] or 0) for r in cov_rows}
    COV = sum(covers.values())

    ov = {r['sku_id']: r for r in ov_rows}
    bsku = {}
    for r in br_rows:
        bsku.setdefault(r['br'], {})[r['sku_id']] = r
    meta = {}
    for r in list(ov_rows) + list(br_rows):
        meta.setdefault(r['sku_id'], r)

    return dict(month=month, EW_branches=[b for b in BRANCHES if covers.get(b)],
                covers=covers, COV=COV, ov=ov, bsku=bsku, meta=meta)


# ───────────────────────────── analytics helpers ─────────────────────────────
def name(meta, sid):
    return (meta[sid]['description'] or '').replace('(B) ', '').replace('(B)', '').strip()


def cat(meta, sid):
    return (meta[sid]['category'] or '').replace('Oshinei ', '').strip()


def topn(rowmap, n=TOPN):
    """sku rows sorted by COGS desc → top n (positive-cost only)."""
    rows = [r for r in rowmap.values() if (r['cogs'] or 0) > 0]
    return sorted(rows, key=lambda r: -(r['cogs'] or 0))[:n]


def decompose(br_row, ov_row, cov_b, COV):
    """Δ(cost/head) = volume effect + unit-cost effect. Returns (dh, vol, price, driver)."""
    bq, bc = (br_row['qty'] or 0), (br_row['cogs'] or 0)
    oq, oc = (ov_row['qty'] or 0), (ov_row['cogs'] or 0)
    bh = bc / cov_b if cov_b else 0.0
    oh = oc / COV if COV else 0.0
    dh = bh - oh
    qph_b = bq / cov_b if cov_b else 0.0
    qph_o = oq / COV if COV else 0.0
    unit_b = bc / bq if bq else 0.0
    unit_o = oc / oq if oq else 0.0
    vol = (qph_b - qph_o) * unit_o          # eat-rate effect @ overview unit cost
    price = qph_b * (unit_b - unit_o)       # unit-cost effect @ branch eat-rate
    driver = 'ปริมาณ (กิน/หัว)' if abs(vol) >= abs(price) else 'ต้นทุน/ชิ้น'
    return dh, vol, price, driver, bh, oh


def is_material(dh, oh):
    return abs(dh) >= MATERIAL_BAHT or (oh and abs(dh) / oh * 100 >= MATERIAL_PCT and abs(dh) >= 0.10)


# ───────────────────────────── formatters ─────────────────────────────
f0 = lambda x: f"{x:,.0f}"
f1 = lambda x: f"{x:,.1f}"
f2 = lambda x: f"{x:,.2f}"
esc = H.escape


def dlt(x, dec=2):
    if x is None:
        return '<span class="mut">—</span>'
    cls = 'up' if x > 0.005 else ('dn' if x < -0.005 else 'mut')
    return f'<span class="{cls}">{"+" if x > 0 else ""}{x:,.{dec}f}</span>'


CSS = """
:root{--ink:#1e293b;--mut:#64748b;--line:#e2e8f0;--acc:#4338ca;--red:#dc2626;--grn:#059669;--bg:#f8fafc}
*{box-sizing:border-box}
body{font-family:'Segoe UI','Leelawadee UI','Noto Sans Thai',Tahoma,sans-serif;color:var(--ink);margin:0;background:#fff;font-size:13px;line-height:1.55}
.page{max-width:1080px;margin:0 auto;padding:28px 36px}
h1{font-size:25px;margin:0 0 4px;color:#0f172a}
h2{font-size:17.5px;margin:30px 0 8px;color:var(--acc);border-bottom:2px solid var(--acc);padding-bottom:4px}
h3{font-size:14px;margin:18px 0 6px;color:#0f172a}
p{margin:6px 0}
.mut{color:var(--mut)} .small{font-size:11.5px} .sub{font-size:10px;line-height:1.2}
.up{color:var(--red);font-weight:600}.dn{color:var(--grn);font-weight:600}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:11.5px}
th{background:var(--bg);text-align:left;padding:5px 7px;border:1px solid var(--line);font-size:11px;color:#334155}
td{padding:4px 7px;border:1px solid var(--line);vertical-align:top}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr:nth-child(even) td{background:#fcfdff}
tfoot td{background:#eef2ff;font-weight:700}
td.flag{background:#fee2e2 !important;font-weight:700;color:#b91c1c}
td.flagdn{background:#dcfce7 !important;font-weight:700;color:#15803d}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:12px 0}
.kpi{border:1px solid var(--line);border-radius:10px;padding:9px 12px;background:var(--bg)}
.kpi .v{font-size:19px;font-weight:700}.kpi .l{font-size:10.5px;color:var(--mut)}
.note{background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;padding:8px 12px;font-size:12px;margin:10px 0}
.danger{background:#fef2f2;border-color:#fca5a5}
.okbox{background:#ecfdf5;border:1px solid #6ee7b7;border-radius:8px;padding:8px 12px;font-size:12px;margin:10px 0}
.ok{color:var(--grn);font-weight:600}.warn{color:var(--red);font-weight:700}
.avoid-break{break-inside:avoid;page-break-inside:avoid}
.page-break{break-before:page;page-break-before:always}
.cover{border-bottom:3px solid var(--acc);padding-bottom:14px;margin-bottom:12px}
.toc{columns:2;font-size:12.5px}
.toc a{color:var(--ink);text-decoration:none}
.toc li{margin:3px 0}
ol li,ul li{margin:4px 0}
.printbtn{position:fixed;top:14px;right:14px;background:var(--acc);color:#fff;border:0;border-radius:8px;padding:9px 16px;font-size:13px;cursor:pointer;font-family:inherit;z-index:9}
@media print{
  .printbtn{display:none}
  body{font-size:10.5px}
  .page{padding:0;max-width:none}
  h2{break-after:avoid}
  table{font-size:9.1px}
  th{font-size:8.8px}
  @page{size:A4;margin:11mm 9mm}
  thead{display:table-header-group}
}
"""


# ───────────────────────────── table renderers ─────────────────────────────
def overview_table(D):
    ov, COV, meta = D['ov'], D['COV'], D['meta']
    rows = topn(ov)
    tot_cogs = sum(r['cogs'] or 0 for r in ov.values())
    cum = 0.0
    body = []
    for i, r in enumerate(rows, 1):
        sid = r['sku_id']; cg = r['cogs'] or 0; q = r['qty'] or 0
        share = cg / tot_cogs * 100 if tot_cogs else 0
        cum += share
        body.append(
            f"<tr><td class='num'>{i}</td><td>{esc(name(meta, sid))}</td>"
            f"<td>{esc(cat(meta, sid))}</td><td class='num'>{f0(q)}</td>"
            f"<td class='num'>{f1(q / COV * 100 if COV else 0)}</td>"
            f"<td class='num'>{f0(cg)}</td><td class='num'><b>{f2(cg / COV if COV else 0)}</b></td>"
            f"<td class='num'>{f1(share)}%</td><td class='num'>{f1(cum)}%</td></tr>")
    shown = sum(r['cogs'] or 0 for r in rows)
    foot = (f"<tfoot><tr><td colspan='5'>รวม Top {len(rows)} (จาก {len([r for r in ov.values() if (r['cogs'] or 0) > 0])} เมนู)</td>"
            f"<td class='num'>{f0(shown)}</td><td class='num'>{f2(shown / COV if COV else 0)}</td>"
            f"<td class='num'>{f1(shown / tot_cogs * 100 if tot_cogs else 0)}%</td><td></td></tr></tfoot>")
    return (
        "<table class='avoid-break'><thead><tr><th>#</th><th>เมนู</th><th>หมวด</th>"
        "<th class='num'>ชิ้นขาย</th><th class='num'>ชิ้น/100หัว</th><th class='num'>ต้นทุนรวม (บ.)</th>"
        "<th class='num'>ต้นทุน/หัว (บ.)</th><th class='num'>% ของ COGS</th><th class='num'>สะสม %</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody>{foot}</table>")


def branch_table(D, br):
    ov, COV, meta = D['ov'], D['COV'], D['meta']
    bm = D['bsku'].get(br, {}); cov_b = D['covers'].get(br, 0)
    rows = topn(bm)
    tot_cogs_b = sum(r['cogs'] or 0 for r in bm.values())
    body = []
    for i, r in enumerate(rows, 1):
        sid = r['sku_id']; cg = r['cogs'] or 0; q = r['qty'] or 0
        ov_row = ov.get(sid, {'qty': 0, 'cogs': 0})
        dh, vol, price, driver, bh, oh = decompose(r, ov_row, cov_b, COV)
        share = cg / tot_cogs_b * 100 if tot_cogs_b else 0
        flag = ''
        if is_material(dh, oh):
            flag = 'flag' if dh > 0 else 'flagdn'
        body.append(
            f"<tr><td class='num'>{i}</td><td>{esc(name(meta, sid))}</td>"
            f"<td>{esc(cat(meta, sid))}</td><td class='num'>{f0(q)}</td>"
            f"<td class='num'>{f1(q / cov_b * 100 if cov_b else 0)}</td>"
            f"<td class='num'>{f0(cg)}</td><td class='num'><b>{f2(bh)}</b></td>"
            f"<td class='num'>{f2(oh)}</td><td class='num {flag}'>{dlt(dh)}</td>"
            f"<td>{driver if flag else '<span class=mut>—</span>'}</td>"
            f"<td class='num'>{f1(share)}%</td></tr>")
    shown = sum(r['cogs'] or 0 for r in rows)
    foot = (f"<tfoot><tr><td colspan='5'>รวม Top {len(rows)}</td>"
            f"<td class='num'>{f0(shown)}</td><td class='num'>{f2(shown / cov_b if cov_b else 0)}</td>"
            f"<td colspan='4'></td></tr></tfoot>")
    return (
        "<table class='avoid-break'><thead><tr><th>#</th><th>เมนู</th><th>หมวด</th>"
        "<th class='num'>ชิ้นขาย</th><th class='num'>ชิ้น/100หัว</th><th class='num'>ต้นทุนรวม (บ.)</th>"
        "<th class='num'>ต้นทุน/หัว สาขา</th><th class='num'>ต้นทุน/หัว ภาพรวม</th>"
        "<th class='num'>Δ/หัว</th><th>สาเหตุหลัก</th><th class='num'>%COGS</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody>{foot}</table>")


def branch_variance(D, br):
    """Top material deviations for a branch (union of branch & overview Top-N)."""
    ov, COV, meta = D['ov'], D['COV'], D['meta']
    bm = D['bsku'].get(br, {}); cov_b = D['covers'].get(br, 0)
    sids = {r['sku_id'] for r in topn(bm)} | {r['sku_id'] for r in topn(ov)}
    devs = []
    for sid in sids:
        br_row = bm.get(sid, {'qty': 0, 'cogs': 0})
        ov_row = ov.get(sid, {'qty': 0, 'cogs': 0})
        dh, vol, price, driver, bh, oh = decompose(br_row, ov_row, cov_b, COV)
        if is_material(dh, oh):
            devs.append((sid, dh, vol, price, driver, bh, oh))
    devs.sort(key=lambda t: -abs(t[1]))
    devs = devs[:DEV_ROWS]
    if not devs:
        return "<p class='mut'>ไม่มีเมนูที่ส่วนต่างต้นทุน/หัวถึงเกณฑ์มีสาระสำคัญ</p>"
    body = []
    for sid, dh, vol, price, driver, bh, oh in devs:
        cls = 'flag' if dh > 0 else 'flagdn'
        tag = ('🔺 สูงกว่าภาพรวม' if dh > 0 else '🟢 ต่ำกว่าภาพรวม')
        body.append(
            f"<tr><td>{esc(name(meta, sid))}</td><td>{esc(cat(meta, sid))}</td>"
            f"<td class='num'>{f2(bh)}</td><td class='num'>{f2(oh)}</td>"
            f"<td class='num {cls}'>{dlt(dh)}</td><td class='num'>{dlt(vol)}</td>"
            f"<td class='num'>{dlt(price)}</td><td><b>{driver}</b></td><td>{tag}</td></tr>")
    return (
        "<table class='avoid-break'><thead><tr><th>เมนู</th><th>หมวด</th>"
        "<th class='num'>ต้นทุน/หัว สาขา</th><th class='num'>ภาพรวม</th><th class='num'>Δ/หัว</th>"
        "<th class='num'>ผลจากปริมาณ</th><th class='num'>ผลจากต้นทุน/ชิ้น</th>"
        "<th>สาเหตุหลัก</th><th>ทิศทาง</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>")


# ───────────────────────────── report assembly ─────────────────────────────
def render(D):
    month, COV, ov = D['month'], D['COV'], D['ov']
    BR = D['EW_branches']
    lbl = month_label(month)
    tot_cogs = sum(r['cogs'] or 0 for r in ov.values())
    ov_head = tot_cogs / COV if COV else 0

    # per-branch headline
    bsum = []
    for br in BR:
        bm = D['bsku'].get(br, {}); cov_b = D['covers'].get(br, 0)
        cg = sum(r['cogs'] or 0 for r in bm.values())
        bh = cg / cov_b if cov_b else 0
        bsum.append((br, cov_b, cg, bh, bh - ov_head))
    bsum.sort(key=lambda t: -t[3])

    # KPI cards
    kpis = (
        f"<div class='kpis'>"
        f"<div class='kpi'><div class='l'>ลูกค้า (covers) — รวมทุกสาขา</div><div class='v'>{f0(COV)}</div><div class='l'>หัว · {lbl}</div></div>"
        f"<div class='kpi'><div class='l'>COGS บุฟเฟ่ต์รวม</div><div class='v'>{f0(tot_cogs)}</div><div class='l'>บาท</div></div>"
        f"<div class='kpi'><div class='l'>ต้นทุน/หัว — ภาพรวม</div><div class='v'>{f2(ov_head)} บ.</div><div class='l'>เกณฑ์เทียบของทุกสาขา</div></div>"
        f"<div class='kpi'><div class='l'>เมนูบุฟเฟ่ต์ที่มีต้นทุน</div><div class='v'>{len([r for r in ov.values() if (r['cogs'] or 0) > 0])}</div><div class='l'>SKU active</div></div>"
        f"</div>")

    # branch compare table
    brc = []
    for br, cov_b, cg, bh, dh in bsum:
        cls = 'flag' if dh > 0 else 'flagdn'
        brc.append(
            f"<tr><td><b>{esc(br)}</b></td><td class='num'>{f0(cov_b)}</td>"
            f"<td class='num'>{f0(cg)}</td><td class='num'><b>{f2(bh)}</b></td>"
            f"<td class='num {cls}'>{dlt(dh)}</td>"
            f"<td class='num'>{dlt((dh / ov_head * 100) if ov_head else 0, 1)}%</td></tr>")
    brc_tbl = (
        "<table class='avoid-break'><thead><tr><th>สาขา</th><th class='num'>ลูกค้า (หัว)</th>"
        "<th class='num'>COGS รวม (บ.)</th><th class='num'>ต้นทุน/หัว</th>"
        "<th class='num'>Δ vs ภาพรวม</th><th class='num'>Δ%</th></tr></thead>"
        f"<tbody>{''.join(brc)}</tbody>"
        f"<tfoot><tr><td>ภาพรวม (รวมทุกสาขา)</td><td class='num'>{f0(COV)}</td>"
        f"<td class='num'>{f0(tot_cogs)}</td><td class='num'>{f2(ov_head)}</td>"
        f"<td class='num'>—</td><td class='num'>—</td></tr></tfoot></table>")

    # cross-branch material digest: collect biggest abs deviations across all branches
    digest = []
    for br in BR:
        bm = D['bsku'].get(br, {}); cov_b = D['covers'].get(br, 0)
        sids = {r['sku_id'] for r in topn(bm)} | {r['sku_id'] for r in topn(ov)}
        for sid in sids:
            br_row = bm.get(sid, {'qty': 0, 'cogs': 0}); ov_row = ov.get(sid, {'qty': 0, 'cogs': 0})
            dh, vol, price, driver, bh, oh = decompose(br_row, ov_row, cov_b, COV)
            if is_material(dh, oh):
                digest.append((br, sid, dh, driver, bh, oh))
    digest.sort(key=lambda t: -abs(t[2]))
    dg = []
    for br, sid, dh, driver, bh, oh in digest[:24]:
        cls = 'flag' if dh > 0 else 'flagdn'
        dg.append(
            f"<tr><td><b>{esc(br)}</b></td><td>{esc(name(D['meta'], sid))}</td>"
            f"<td class='num'>{f2(bh)}</td><td class='num'>{f2(oh)}</td>"
            f"<td class='num {cls}'>{dlt(dh)}</td><td>{driver}</td></tr>")
    digest_tbl = (
        "<table class='avoid-break'><thead><tr><th>สาขา</th><th>เมนู</th>"
        "<th class='num'>ต้นทุน/หัว สาขา</th><th class='num'>ภาพรวม</th>"
        "<th class='num'>Δ/หัว</th><th>สาเหตุหลัก</th></tr></thead>"
        f"<tbody>{''.join(dg)}</tbody></table>"
        if dg else "<p class='mut'>ไม่พบส่วนต่างที่มีสาระสำคัญ</p>")

    # per-branch sections
    sections = []
    for i, br in enumerate(BR, 1):
        bm = D['bsku'].get(br, {}); cov_b = D['covers'].get(br, 0)
        cg = sum(r['cogs'] or 0 for r in bm.values()); bh = cg / cov_b if cov_b else 0
        dh = bh - ov_head
        verdict = ('สูงกว่าภาพรวม — เน้นคุมพรีเมียม/พอร์ชัน' if dh > 0.2 else
                   'ต่ำกว่าภาพรวม — เป็นต้นแบบ/ถอดบทเรียน' if dh < -0.2 else 'ใกล้เคียงภาพรวม')
        sections.append(f"""
<div class="page-break"></div>
<h2 id="b{i}">สาขา {esc(br)} — Top {TOPN} เมนู (ต้นทุน/หัว, {lbl})</h2>
<div class="kpis">
  <div class="kpi"><div class="l">ลูกค้า (covers)</div><div class="v">{f0(cov_b)}</div><div class="l">หัว</div></div>
  <div class="kpi"><div class="l">COGS บุฟเฟ่ต์รวม</div><div class="v">{f0(cg)}</div><div class="l">บาท</div></div>
  <div class="kpi"><div class="l">ต้นทุน/หัว สาขานี้</div><div class="v">{f2(bh)} บ.</div><div class="l">{dlt(dh)} บ. vs ภาพรวม ({f2(ov_head)})</div></div>
  <div class="kpi"><div class="l">สถานะ</div><div class="v" style="font-size:14px">{verdict}</div><div class="l">{dlt((dh / ov_head * 100) if ov_head else 0, 1)}% vs ภาพรวม</div></div>
</div>
<h3>ก) ส่วนต่างที่มีสาระสำคัญเทียบภาพรวม (|Δ| ≥ {f2(MATERIAL_BAHT)} บ./หัว หรือ ≥ {f0(MATERIAL_PCT)}%)</h3>
<p class="small mut">Δ/หัว แยกเป็น <b>ผลจากปริมาณ</b> (ลูกค้าสาขานี้กินเมนูนี้มาก/น้อยกว่าค่าเฉลี่ย × ต้นทุน/ชิ้นภาพรวม) และ
<b>ผลจากต้นทุน/ชิ้น</b> (ต้นทุน/ชิ้นของสาขาต่างจากภาพรวม — สูตร/ราคาวัตถุดิบ/การตัดสต๊อก/waste) × ปริมาณที่กิน</p>
{branch_variance(D, br)}
<h3>ข) ตาราง Top {TOPN} เมนูของสาขา {esc(br)} (เรียงตามต้นทุนรวม)</h3>
{branch_table(D, br)}
""")

    TOC = ("<ol class='toc'>"
           "<li><a href='#s1'>1. บทสรุปผู้บริหาร + วิธีวิเคราะห์</a></li>"
           "<li><a href='#s2'>2. ภาพรวมทุกสาขา (รวม master) — Top 60</a></li>"
           "<li><a href='#s3'>3. เปรียบเทียบต้นทุน/หัว รายสาขา</a></li>"
           + ''.join(f"<li><a href='#b{i}'>{3 + i}. สาขา {esc(br)} — Top 60</a></li>" for i, br in enumerate(BR, 1))
           + f"<li><a href='#sV'>{4 + len(BR)}. ส่วนต่างที่มีสาระสำคัญ (รวมทุกสาขา)</a></li>"
           + f"<li><a href='#sP'>{5 + len(BR)}. แนวทางตรวจสอบ · พัฒนา · ติดตามแก้ไข</a></li>"
           "</ol>")

    return f"""<!DOCTYPE html><html lang="th"><head><meta charset="utf-8">
<title>รายงาน Top 60 เมนู — ต้นทุนต่อหัว/เดือน · สาขา master · {lbl}</title>
<style>{CSS}</style></head><body>
<button class="printbtn" onclick="window.print()">🖨 พิมพ์ / บันทึก PDF</button>
<div class="page">

<div class="cover">
  <h1>🍱 รายงาน Top {TOPN} เมนู — ต้นทุนต่อหัว/เดือน</h1>
  <p><b>สาขา master {len(BR)} สาขา</b>: {esc(' · '.join(BR))} · เดือน <b>{lbl}</b> ({month})</p>
  <p class="small mut">เริ่มจากภาพรวมรวมทุกสาขา แล้วแยกทีละสาขา (แต่ละสาขามี Top {TOPN} ครบ) ·
  ต้นทุน/หัว = COGS บุฟเฟ่ต์ ÷ ลูกค้า (covers) ของสโคปนั้น · เกณฑ์ COGS เดียวกับหน้า "เทียบเมนู A vs B" ของแดชบอร์ด ·
  เฉพาะเมนูบุฟเฟ่ต์ (ไม่รวม à-la-carte/เครื่องดื่ม) · จัดทำ {month_label('2026-06')} จาก oshinei.db</p>
  <h3>สารบัญ</h3>{TOC}
</div>

<h2 id="s1">1) บทสรุปผู้บริหาร + วิธีวิเคราะห์</h2>
{kpis}
<ul>
  <li><b>ต้นทุน/หัวภาพรวมของเครือ = {f2(ov_head)} บ./หัว</b> ({lbl}) บนฐานลูกค้า {f0(COV)} หัว และ COGS บุฟเฟ่ต์ {f0(tot_cogs)} บ. — ใช้เป็น "เส้นฐาน" เทียบทุกสาขา</li>
  <li><b>สาขาแพงสุด:</b> {esc(bsum[0][0])} ({f2(bsum[0][3])} บ./หัว, {dlt(bsum[0][4])} vs ภาพรวม) · <b>ถูกสุด:</b> {esc(bsum[-1][0])} ({f2(bsum[-1][3])} บ./หัว, {dlt(bsum[-1][4])}) — ช่องว่าง {f2(bsum[0][3] - bsum[-1][3])} บ./หัว บนเมนูชุดเดียวกัน คือโอกาสปฏิบัติการ</li>
  <li>แต่ละสาขาแสดง <b>Top {TOPN} เมนูครบ</b> พร้อมคอลัมน์ "ต้นทุน/หัว สาขา" เทียบ "ต้นทุน/หัว ภาพรวม" และ Δ — แถวที่ <span class="flag" style="padding:1px 4px">แดง</span> = สูงกว่าภาพรวมอย่างมีสาระสำคัญ, <span class="flagdn" style="padding:1px 4px">เขียว</span> = ต่ำกว่า</li>
</ul>
<table class="avoid-break"><tbody>
<tr><td style="width:200px"><b>ขอบเขตสาขา</b></td><td>สาขา master {len(BR)} สาขาที่มีลูกค้าในเดือนนี้ (เรียงตามต้นทุน/หัว)</td></tr>
<tr><td><b>ขอบเขตเมนู</b></td><td>เฉพาะเมนูบุฟเฟ่ต์ (หมวดอาหาร, ไม่ขึ้นต้น "(A)") — ไม่รวมเครื่องดื่ม/บริการ/เดลิเวอรี เกณฑ์เดียวกับหน้า "เทียบเมนู A vs B"</td></tr>
<tr><td><b>ต้นทุน (COGS)</b></td><td>ผ่าน helper มาตรฐาน <code>_menucmp_agg</code>: cost-management → BoM → alias → unit_cost · เมนูแพ็กเกจบุฟเฟ่ต์เอง = 0 (กันนับซ้ำ)</td></tr>
<tr><td><b>ต้นทุน/หัว</b></td><td>COGS ของเมนู ÷ ลูกค้า (covers) ของสโคป — ภาพรวมหารด้วยลูกค้ารวมทุกสาขา, รายสาขาหารด้วยลูกค้าของสาขานั้น (normalize ขนาดสาขา)</td></tr>
<tr><td><b>แยกสาเหตุ Δ/หัว</b></td><td>= ผลจากปริมาณ (Δ ชิ้น/หัว × ต้นทุน/ชิ้นภาพรวม) + ผลจากต้นทุน/ชิ้น (Δ ต้นทุน/ชิ้น × ชิ้น/หัวสาขา) → ชี้ว่าต้อง "คุมการกิน" หรือ "คุมต้นทุนวัตถุดิบ/สูตร"</td></tr>
<tr><td><b>เกณฑ์มีสาระสำคัญ</b></td><td>|Δ ต้นทุน/หัว| ≥ {f2(MATERIAL_BAHT)} บ. หรือ ≥ {f0(MATERIAL_PCT)}% ของค่าภาพรวม</td></tr>
</tbody></table>

<div class="page-break"></div>
<h2 id="s2">2) ภาพรวมทุกสาขา (รวม master) — Top {TOPN} เมนู</h2>
<p>ต้นทุน/หัวภาพรวม <b>{f2(ov_head)} บ.</b> · ลูกค้า {f0(COV)} หัว · COGS รวม {f0(tot_cogs)} บ. คอลัมน์ "สะสม %" ใช้อ่าน Pareto (เมนูไม่กี่ตัวแรกกินสัดส่วนต้นทุนส่วนใหญ่)</p>
{overview_table(D)}

<h2 id="s3">3) เปรียบเทียบต้นทุน/หัว รายสาขา</h2>
<p>เรียงจากแพง→ถูก เทียบเส้นฐานภาพรวม {f2(ov_head)} บ./หัว</p>
{brc_tbl}
{''.join(sections)}

<div class="page-break"></div>
<h2 id="sV">{4 + len(BR)}) ส่วนต่างที่มีสาระสำคัญ — รวมทุกสาขา</h2>
<p>เมนู×สาขา ที่ต้นทุน/หัวเบี่ยงจากภาพรวมมากที่สุด (เรียงตาม |Δ/หัว|) — รายการเฝ้าระวังลำดับแรก</p>
{digest_tbl}

<h2 id="sP">{5 + len(BR)}) แนวทางตรวจสอบ · พัฒนา · ติดตามแก้ไข</h2>
<h3>ก) ตรวจสอบ (Investigate) — ทำทันทีกับเมนูธงแดง</h3>
<ul>
  <li><b>ถ้าสาเหตุหลัก = "ต้นทุน/ชิ้น":</b> ต้นทุน/ชิ้นของสาขาสูงกว่าภาพรวม → ตรวจ (1) สูตร/พอร์ชันจริงหน้างาน vs BoM มาตรฐาน, (2) ราคารับซื้อวัตถุดิบ (ผู้ส่ง/ล็อต), (3) yield การตัดแต่ง/ของเสีย, (4) การคีย์ POS ผิด SKU/น้ำหนัก</li>
  <li><b>ถ้าสาเหตุหลัก = "ปริมาณ (กิน/หัว)":</b> ลูกค้ากินเมนูนี้/หัวมากผิดปกติ → ตรวจ (1) การเติม/รีฟิลไม่จำกัด, (2) การจัดวางบุฟเฟ่ต์ไลน์ที่กระตุ้นการตัก, (3) การพกกลับ/ทิ้ง, (4) สัดส่วน tier ลูกค้า</li>
  <li><b>ตรวจคุณภาพข้อมูล:</b> เมนูต้นทุน/หัว = 0 หรือสูงผิดปกติ มัก = BoM ขาด/ราคาประมาณการ — ยืนยันก่อนสรุป (เทียบกับ COST_DATA_AUDIT)</li>
</ul>
<h3>ข) พัฒนา (Improve) — ยกระดับให้เข้าหาหรือดีกว่าภาพรวม</h3>
<ul>
  <li><b>ถอดบทเรียนจากสาขาถูกสุด</b> ({esc(bsum[-1][0])}): นำมาตรฐานพอร์ชัน/การจัดไลน์/การคุมรีฟิลของสาขานี้ไปใช้กับสาขาที่แพงกว่า</li>
  <li><b>Re-engineer เมนูต้นทุนสูง Top 10</b> (จากตารางภาพรวม): ปรับพอร์ชัน/หน้าตา/ตำแหน่งในไลน์ หรือสลับวัตถุดิบเทียบเท่าที่ถูกกว่าโดยไม่ลดคุณค่ารับรู้</li>
  <li><b>เจรจาจัดซื้อรวมศูนย์</b> สำหรับวัตถุดิบที่ผลต่าง "ต้นทุน/ชิ้น" ข้ามสาขากว้าง — ดึงทุกสาขาเข้าหาราคาที่ดีที่สุด</li>
  <li><b>ตั้งเป้าต้นทุน/หัวรายสาขา</b> = ภาพรวม + ค่าเผื่อที่ยอมรับได้ (เช่น +{f2(MATERIAL_BAHT)} บ.) เป็น KPI ครัว</li>
</ul>
<h3>ค) ติดตามแก้ไข (Track) — ปิดลูป</h3>
<ul>
  <li><b>รันรายงานนี้ทุกเดือน</b> (เปลี่ยน <code>--month</code>) แล้วเทียบ Δ/หัวรายสาขาเดือนต่อเดือน — ธงแดงตัวเดิมที่ยังไม่ดีขึ้น = ยังไม่ถูกแก้</li>
  <li><b>มอบหมายเจ้าภาพต่อเมนูธงแดง</b> (ผจก.สาขา/หัวหน้าครัว) + กำหนดวันตรวจซ้ำ และบันทึกผลในรายงานเดือนถัดไป</li>
  <li><b>Control chart รายสัปดาห์</b> ต้นทุน/หัวรวมรายสาขา (จาก sales_sku_daily) เพื่อจับการเบี่ยงก่อนสิ้นเดือน</li>
  <li><b>ตรวจรับ BoM/ราคาที่เป็นค่าประมาณการ</b> ให้เป็นค่าจริงก่อนปิดงวด เพื่อให้ต้นทุน/หัวสะท้อนของจริง</li>
</ul>
<p class="small mut">— จบรายงาน · สร้างด้วย backend/gen_top60_menu_perhead.py —</p>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--month', default=DEFAULT_MONTH, help="YYYY-MM (default 2026-05)")
    ap.add_argument('--out', default=None, help="output HTML path")
    args = ap.parse_args()

    D = build(args.month)
    if not D['EW_branches']:
        sys.exit(f"[error] ไม่พบข้อมูลลูกค้า/ยอดขายของสาขา master ในเดือน {args.month} "
                 f"— ตรวจว่า OSHINEI_DB ชี้ไปที่ oshinei.db ที่มีข้อมูลเดือนนั้น")
    out = Path(args.out) if args.out else (HERE.parent / "docs" / f"TOP60_MENU_PERHEAD_{args.month}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(D), encoding='utf-8')
    print(f"✓ wrote {out}  ({out.stat().st_size:,} bytes) · "
          f"{len(D['EW_branches'])} branches · {D['COV']:,} covers")


if __name__ == '__main__':
    main()
