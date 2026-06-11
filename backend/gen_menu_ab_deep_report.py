# -*- coding: utf-8 -*-
"""Deep-dive printable report: buffet menu cost per head, A vs B.

Scope: 5 master branches (no โคราช2), Jan–May 2026, base Dec 2025.
Reuses main.py's _menucmp_agg so COGS follows the canonical helper
(buffet-tier=0, BoM-first, alias-aware).  Output → docs/.

Usage:  python gen_menu_ab_deep_report.py
"""
import json, html as H, math, sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from main import get_db, _menucmp_agg, _FOOD_CATEGORY_FILTER

BRANCHES = ['โคราช1', 'เชียงใหม่', 'สุรินทร์', 'อุดรธานี', 'อุบลราชธานี']
MONTHS = ['2025-12', '2026-01', '2026-02', '2026-03', '2026-04', '2026-05']
BASE = MONTHS[0]; M26 = MONTHS[1:]
WK_FROM, WK_TO = '2026-02-09', '2026-05-31'   # sales_sku_daily coverage

ph = ','.join(f':b{i}' for i in range(len(BRANCHES)))
# _FOOD_CATEGORY_FILTER now whitelists durian centrally (it's filed under
# Special KKN / Promotion / Oshinei SERVICE but is real, costed buffet food), so
# this report counts it automatically — no report-local override needed.
EW = ["b.name!='ALL'", "COALESCE(sk.active,1)=1", _FOOD_CATEGORY_FILTER,
      "COALESCE(sk.description,'') NOT LIKE '(A)%'", f"b.name IN ({ph})"]
PB = {f'b{i}': n for i, n in enumerate(BRANCHES)}

# ───────────────────────────── extraction ─────────────────────────────
sel_sku = "sk.sku_id sku_id, sk.sku_code sku_code, sk.description description, c.name category"
sel_br_sku = "b.name br, sk.sku_id sku_id, sk.description description, c.name category"

per_month, per_branch_month, covers_bm, rev_bm = {}, {}, {}, {}
with get_db() as con:
    for m in MONTHS:
        per_month[m] = _menucmp_agg(con, sel_sku, "sk.sku_id", EW, PB, m, m, None, None)
        per_branch_month[m] = _menucmp_agg(con, sel_br_sku, "b.name, sk.sku_id", EW, PB, m, m, None, None)
        rows = con.execute(f"""
            SELECT b.name br, SUM(t.customer_count) c, SUM(t.total_inc_vat)/1.07 rev
            FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
            WHERE b.name IN ({ph}) AND t.source='Easy' AND strftime('%Y-%m',t.sold_at)=:m
            GROUP BY b.name""", {**PB, 'm': m}).fetchall()
        covers_bm[m] = {r['br']: r['c'] or 0 for r in rows}
        rev_bm[m]    = {r['br']: r['rev'] or 0 for r in rows}
    # weekly (Mon-start) cogs per branch from sales_sku_daily + weekly covers
    wk_cogs_rows = _menucmp_agg(con, "b.name br, date(d.day,'weekday 0','-6 days') wk",
                                "br, wk", EW, PB, None, None, WK_FROM, WK_TO)
    wk_cov_rows = con.execute(f"""
        SELECT b.name br, date(t.sold_at,'weekday 0','-6 days') wk, SUM(t.customer_count) c
        FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
        WHERE b.name IN ({ph}) AND t.source='Easy'
          AND date(t.sold_at) BETWEEN :f AND :t GROUP BY 1,2""",
        {**PB, 'f': WK_FROM, 't': WK_TO}).fetchall()

cov  = {m: sum(covers_bm[m].values()) for m in MONTHS}
REV  = {m: sum(rev_bm[m].values()) for m in MONTHS}

sku = {m: {r['sku_id']: r for r in per_month[m]} for m in MONTHS}
bsku = {m: {} for m in MONTHS}            # bsku[m][branch][sku_id]
for m in MONTHS:
    for r in per_branch_month[m]:
        bsku[m].setdefault(r['br'], {})[r['sku_id']] = r
meta = {}
for m in MONTHS:
    for sid, r in sku[m].items(): meta.setdefault(sid, r)

wk_cogs = {}                               # wk_cogs[wk][br] = cogs
for r in wk_cogs_rows:
    wk_cogs.setdefault(r['wk'], {})[r['br']] = r['cogs'] or 0
wk_cov = {}
for r in wk_cov_rows:
    wk_cov.setdefault(r['wk'], {})[r['br']] = r['c'] or 0
WEEKS = sorted(w for w in wk_cogs if w >= WK_FROM)
# drop partial first/last weeks (Mon-start weeks fully inside range)
WEEKS = [w for w in WEEKS if w >= '2026-02-09' and w <= '2026-05-25']

# ───────────────────────────── helpers ─────────────────────────────
def name(sid):
    return (meta[sid]['description'] or '').replace('(B) ', '').replace('(B)', '').strip()
def cat(sid):
    return (meta[sid]['category'] or '').replace('Oshinei ', '')
def head(m, sid):
    r = sku[m].get(sid); return (r['cogs'] or 0) / cov[m] if r else 0.0
def qph(m, sid):
    r = sku[m].get(sid); return (r['qty'] or 0) / cov[m] * 100 if r else 0.0
def unit(m, sid):
    r = sku[m].get(sid)
    if not r or not (r['qty'] or 0): return None
    return (r['cogs'] or 0) / r['qty']
def bhead(m, br, sid):
    r = bsku[m].get(br, {}).get(sid)
    c = covers_bm[m].get(br, 0)
    return ((r['cogs'] or 0) / c) if (r and c) else 0.0
def bqph(m, br, sid):
    r = bsku[m].get(br, {}).get(sid)
    c = covers_bm[m].get(br, 0)
    return ((r['qty'] or 0) / c * 100) if (r and c) else 0.0

tot_cogs = {m: sum(r['cogs'] or 0 for r in per_month[m]) for m in MONTHS}
tot_head = {m: tot_cogs[m] / cov[m] for m in MONTHS}
rev_head = {m: REV[m] / cov[m] for m in MONTHS}
ratio    = {m: tot_cogs[m] / REV[m] * 100 for m in MONTHS}
spread   = {m: rev_head[m] - tot_head[m] for m in MONTHS}
br_head  = {m: {br: (sum((r['cogs'] or 0) for r in bsku[m].get(br, {}).values())
                     / covers_bm[m][br] if covers_bm[m].get(br) else 0) for br in BRANCHES}
            for m in MONTHS}
br_rev_head = {m: {br: (rev_bm[m][br] / covers_bm[m][br] if covers_bm[m].get(br) else 0)
                   for br in BRANCHES} for m in MONTHS}

LBL = {'2025-12': 'ธ.ค. 2025', '2026-01': 'ม.ค. 2026', '2026-02': 'ก.พ. 2026',
       '2026-03': 'มี.ค. 2026', '2026-04': 'เม.ย. 2026', '2026-05': 'พ.ค. 2026'}
SH = {'2025-12': 'ธ.ค.25', '2026-01': 'ม.ค.', '2026-02': 'ก.พ.',
      '2026-03': 'มี.ค.', '2026-04': 'เม.ย.', '2026-05': 'พ.ค.'}

f0 = lambda x: f"{x:,.0f}"
f1 = lambda x: f"{x:,.1f}"
f2 = lambda x: f"{x:,.2f}"
esc = H.escape
def dlt(x, dec=2):
    if x is None: return '<span class="mut">—</span>'
    cls = 'up' if x > 0.005 else ('dn' if x < -0.005 else 'mut')
    return f'<span class="{cls}">{"+" if x > 0 else ""}{x:,.{dec}f}</span>'
def dpct(a, b): return None if not b else (a - b) / b * 100

def svg_line(series, labels, w=620, h=170, ymin=None, ymax=None, fmt=f1):
    """series = [(name, color, [values])] — print-safe inline SVG line chart."""
    allv = [v for _, _, vs in series for v in vs if v is not None]
    lo = ymin if ymin is not None else min(allv); hi = ymax if ymax is not None else max(allv)
    if hi == lo: hi = lo + 1
    pad = (hi - lo) * 0.12; lo -= pad; hi += pad
    L, R, T, B = 46, 10, 12, 26
    px = lambda i: L + i * (w - L - R) / max(1, len(labels) - 1)
    py = lambda v: T + (hi - v) * (h - T - B) / (hi - lo)
    out = [f'<svg viewBox="0 0 {w} {h}" style="width:100%;max-width:{w}px;height:auto" xmlns="http://www.w3.org/2000/svg">']
    for gy in range(5):
        v = lo + (hi - lo) * gy / 4
        out.append(f'<line x1="{L}" y1="{py(v):.1f}" x2="{w-R}" y2="{py(v):.1f}" stroke="#e2e8f0" stroke-width="1"/>')
        out.append(f'<text x="{L-5}" y="{py(v)+3.5:.1f}" text-anchor="end" font-size="9" fill="#64748b">{fmt(v)}</text>')
    for i, lb in enumerate(labels):
        out.append(f'<text x="{px(i):.1f}" y="{h-8}" text-anchor="middle" font-size="9" fill="#64748b">{esc(lb)}</text>')
    for nm, col, vs in series:
        pts = ' '.join(f'{px(i):.1f},{py(v):.1f}' for i, v in enumerate(vs) if v is not None)
        out.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2.2"/>')
        for i, v in enumerate(vs):
            if v is not None:
                out.append(f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="2.6" fill="{col}"/>')
    out.append('</svg>')
    leg = ' '.join(f'<span class="legend"><span class="sw" style="background:{c}"></span>{esc(n)}</span>'
                   for n, c, _ in series if n)
    return ''.join(out) + (f'<div class="small" style="margin-top:2px">{leg}</div>' if leg else '')

BR_COLORS = {'โคราช1': '#4338ca', 'เชียงใหม่': '#dc2626', 'สุรินทร์': '#059669',
             'อุดรธานี': '#d97706', 'อุบลราชธานี': '#0891b2'}

# ───────────────────────────── computed blocks ─────────────────────────────
may_rank = sorted(sku['2026-05'].keys(), key=lambda s: -(sku['2026-05'][s]['cogs'] or 0))
dec_rank = sorted(sku[BASE].keys(), key=lambda s: -(sku[BASE][s]['cogs'] or 0))

def pareto_n(rank, m, frac=0.8):
    acc = 0; tot = tot_cogs[m]
    for i, s in enumerate(rank, 1):
        acc += sku[m][s]['cogs'] or 0
        if acc / tot >= frac: return i
    return len(rank)
n80_may, n80_dec = pareto_n(may_rank, '2026-05'), pareto_n(dec_rank, BASE)
n50_may = pareto_n(may_rank, '2026-05', 0.5)

# month-bridge decomposition (MoM chain)
def bridge(m, pm):
    new = gone = qty_eff = price_eff = 0.0
    for sid in set(sku[m]) | set(sku[pm]):
        a, b = sku[m].get(sid), sku[pm].get(sid)
        if a and not b:   new += head(m, sid)
        elif b and not a: gone -= head(pm, sid)
        else:
            qa, qb = qph(m, sid), qph(pm, sid)
            ua, ub = unit(m, sid) or 0, unit(pm, sid) or 0
            qty_eff   += (qa - qb) * ub / 100
            price_eff += qa * (ua - ub) / 100
    return new, gone, qty_eff, price_eff

# movers vs Dec with cause
def movers_vs(m, base, src=head, limit_abs=0.25):
    out = []
    for sid in set(sku[m]) | set(sku[base]):
        a, b = src(m, sid), src(base, sid)
        if abs(a - b) < limit_abs: continue
        ua, ub = unit(m, sid), unit(base, sid)
        qa, qb = qph(m, sid), qph(base, sid)
        if sid not in sku[base]: cause = 'เมนูใหม่'
        elif sid not in sku[m]:  cause = 'ถอดออก'
        elif ua and ub and abs(ua/ub - 1) > 0.10 and (qb == 0 or abs(qa/qb - 1) < abs(ua/ub - 1)):
            cause = f'ต้นทุน/ชิ้น {f1(ub)}→{f1(ua)} บ.'
        else:
            cause = f'การกิน {f1(qb)}→{f1(qa)} ชิ้น/100หัว'
        out.append((a - b, sid, a, b, cause))
    return out

# deep-dive menus: top 12 by May cogs
DEEP = may_rank[:12]
with get_db() as con:
    bom_map = {}
    for sid in DEEP:
        rows = con.execute("""SELECT ingredient_name, qty_per_serving, unit, price_per_unit, ingredient_cost
                              FROM bom_ingredients WHERE sku_id=? ORDER BY ingredient_cost DESC""", (sid,)).fetchall()
        if not rows:
            rows = con.execute("""SELECT bi.ingredient_name, bi.qty_per_serving, bi.unit, bi.price_per_unit, bi.ingredient_cost
                                  FROM bom_ingredients bi
                                  JOIN bom_sku_alias a ON a.resolved_sku_id=bi.sku_id
                                  JOIN skus s ON s.sku_code=a.bom_sku_code
                                  WHERE s.sku_id=? ORDER BY bi.ingredient_cost DESC""", (sid,)).fetchall()
        bom_map[sid] = [dict(r) for r in rows]

# new menus
new_first = {}
for m in M26:
    for sid in sku[m]:
        if sid not in sku[BASE] and sid not in new_first: new_first[sid] = m
new_impact = {m: sum(head(m, s) for s in sku[m] if s not in sku[BASE]) for m in M26}

# categories ranked by May cost/head
cat_head = {m: {} for m in MONTHS}
for m in MONTHS:
    for sid in sku[m]:
        cat_head[m][cat(sid)] = cat_head[m].get(cat(sid), 0) + head(m, sid)
CATS = sorted(cat_head['2026-05'], key=lambda c: -cat_head['2026-05'][c])
CATS = [c for c in CATS if cat_head['2026-05'].get(c, 0) >= 1.5 or cat_head[BASE].get(c, 0) >= 1.5]

# ───────────────────────────── HTML pieces ─────────────────────────────
def matrix_table(ids, n_label='', show_rank=True, base_m=BASE, totals=True):
    rows = ''
    for i, sid in enumerate(ids, 1):
        hb = head(base_m, sid); is_new = sid not in sku[base_m]
        cells = ''.join(
            f'<td class="num">{f2(head(m,sid)) if sku[m].get(sid) else "—"}'
            f'<div class="sub">{dlt(head(m,sid)-hb)}</div></td>' for m in M26)
        d5 = head('2026-05', sid) - hb; pc = dpct(head('2026-05', sid), hb)
        nm = esc(name(sid)) + (' <span class="newtag">ใหม่</span>' if is_new else '')
        rank_cell = f'<td class="num mut">{i}</td>' if show_rank else ''
        rows += (f'<tr>{rank_cell}'
                 f'<td>{nm}</td><td class="mut">{esc(cat(sid))}</td>'
                 f'<td class="num">{f2(hb) if not is_new else "—"}</td>{cells}'
                 f'<td class="num">{dlt(d5)}</td>'
                 f'<td class="num">{dlt(pc,1) if pc is not None else "<span class=mut>ใหม่</span>"}</td></tr>')
    return rows

def th_months():
    return ''.join(f'<th class="num">{SH[m]}</th>' for m in M26)

# Set-1 matrix top 80
TOPN = 80
top_ids = may_rank[:TOPN]
oth = [s for s in sku['2026-05'] if s not in top_ids]
oth_base = sum(head(BASE, s) for s in sku[BASE] if s not in top_ids)
oth_cells = ''
for m in M26:
    hv = sum(head(m, s) for s in sku[m] if s not in top_ids)
    oth_cells += f'<td class="num">{f2(hv)}<div class="sub">{dlt(hv-oth_base)}</div></td>'
oth_may = sum(head('2026-05', s) for s in oth)
tot_cells = ''.join(f'<td class="num"><b>{f2(tot_head[m])}</b><div class="sub">{dlt(tot_head[m]-tot_head[BASE])}</div></td>' for m in M26)

# Set-2 blocks + bridges
mom_html = ''
chain = list(zip(M26, MONTHS[:-1]))
for m, pm in chain:
    dm = tot_head[m] - tot_head[pm]
    new, gone, qe, pe = bridge(m, pm)
    pairs = []
    for sid in set(sku[m]) | set(sku[pm]):
        dd = head(m, sid) - head(pm, sid)
        if abs(dd) >= 0.2: pairs.append((dd, sid))
    u  = sorted([x for x in pairs if x[0] > 0], key=lambda x: -x[0])[:12]
    dn = sorted([x for x in pairs if x[0] < 0], key=lambda x: x[0])[:12]
    def li(lst, mm=m, pmm=pm):
        s = ''
        for dd, sid in lst:
            tag = ' <span class="newtag">ใหม่</span>' if sid not in sku[BASE] else ''
            s += (f'<tr><td>{esc(name(sid))}{tag}</td><td class="mut">{esc(cat(sid))}</td>'
                  f'<td class="num">{f2(head(pmm,sid))}</td><td class="num">{f2(head(mm,sid))}</td>'
                  f'<td class="num">{dlt(dd)}</td>'
                  f'<td class="num small">{f1(qph(pmm,sid))}→{f1(qph(mm,sid))}</td></tr>')
        return s
    mom_html += f'''
    <div class="avoid-break">
    <h3>{LBL[m]} เทียบ {LBL[pm]} — ต้นทุน/หัว {f2(tot_head[pm])} → {f2(tot_head[m])} บ. ({dlt(dm)} บ., {dlt(dpct(tot_head[m],tot_head[pm]),1)}%)</h3>
    <table class="bridge"><thead><tr><th>องค์ประกอบของการเปลี่ยนแปลง (Bridge)</th><th class="num">บ./หัว</th></tr></thead><tbody>
      <tr><td>ฐาน {LBL[pm]}</td><td class="num"><b>{f2(tot_head[pm])}</b></td></tr>
      <tr><td>± เมนูใหม่ที่เพิ่มเข้ามาในเดือนนี้</td><td class="num">{dlt(new)}</td></tr>
      <tr><td>± เมนูที่หายไป/ถอดออก</td><td class="num">{dlt(gone)}</td></tr>
      <tr><td>± พฤติกรรมการกินของเมนูเดิม (ชิ้น/หัว เปลี่ยน)</td><td class="num">{dlt(qe)}</td></tr>
      <tr><td>± ต้นทุนต่อชิ้นของเมนูเดิม (ราคา/สูตรวัตถุดิบ)</td><td class="num">{dlt(pe)}</td></tr>
      <tr><td><b>รวม {LBL[m]}</b></td><td class="num"><b>{f2(tot_head[m])}</b></td></tr>
    </tbody></table>
    <div class="cols2">
      <table><thead><tr><th>เพิ่มขึ้นแรงสุด 12 อันดับ</th><th>หมวด</th><th class="num">B</th><th class="num">A</th><th class="num">Δ/หัว</th><th class="num">ชิ้น/100หัว</th></tr></thead><tbody>{li(u)}</tbody></table>
      <table><thead><tr><th>ลดลงแรงสุด 12 อันดับ</th><th>หมวด</th><th class="num">B</th><th class="num">A</th><th class="num">Δ/หัว</th><th class="num">ชิ้น/100หัว</th></tr></thead><tbody>{li(dn)}</tbody></table>
    </div></div>'''

# category chapters
cat_html = ''
for c in CATS:
    ids = sorted([s for s in sku['2026-05'] if cat(s) == c] +
                 [s for s in sku[BASE] if cat(s) == c and s not in sku['2026-05']],
                 key=lambda s: -head('2026-05', s))
    ids = list(dict.fromkeys(ids))[:10]
    trend = [cat_head[m].get(c, 0) for m in MONTHS]
    movs = sorted([(head('2026-05', s) - head(BASE, s), s) for s in set(sku['2026-05']) | set(sku[BASE])
                   if cat(s) == c], key=lambda x: -abs(x[0]))[:3]
    mv_txt = ' · '.join(f'{esc(name(s))} {dlt(d)}' for d, s in movs if abs(d) >= 0.15) or 'การเคลื่อนไหวรายเมนูไม่มีนัยสำคัญ'
    d_tot = trend[-1] - trend[0]
    cat_html += f'''
    <div class="avoid-break">
    <h3>หมวด {esc(c)} — {f2(trend[0])} → {f2(trend[-1])} บ./หัว ({dlt(d_tot)})</h3>
    <div class="cols2" style="align-items:center">
      <div>{svg_line([('', '#4338ca', trend)], [SH[m] for m in MONTHS], w=420, h=140)}</div>
      <div class="small"><b>ตัวขับหลักของหมวด:</b><br>{mv_txt}</div>
    </div>
    <table><thead><tr><th>เมนู Top 10 ในหมวด</th><th class="num">ธ.ค.25</th>{th_months()}<th class="num">Δพ.ค.−ธ.ค.</th><th class="num">Δ%</th></tr></thead>
    <tbody>{''.join(f"""<tr><td>{esc(name(s))}{' <span class=newtag>ใหม่</span>' if s not in sku[BASE] else ''}</td>
        <td class="num">{f2(head(BASE,s)) if s in sku[BASE] else '—'}</td>
        {''.join(f'<td class=num>{f2(head(m,s)) if sku[m].get(s) else "—"}</td>' for m in M26)}
        <td class="num">{dlt(head('2026-05',s)-head(BASE,s))}</td>
        <td class="num">{dlt(dpct(head('2026-05',s),head(BASE,s)),1) if s in sku[BASE] and head(BASE,s) else '<span class=mut>ใหม่</span>'}</td></tr>""" for s in ids)}</tbody>
    </table></div>'''

# branch chapters
def br_chapter(br):
    col = BR_COLORS[br]
    trend = [br_head[m][br] for m in MONTHS]
    net   = [tot_head[m] for m in MONTHS]
    rv    = [br_rev_head[m][br] for m in MONTHS]
    gap_may = trend[-1] - net[-1]
    # weekly series
    wk_vals = []
    for w in WEEKS:
        c = wk_cov.get(w, {}).get(br, 0); g = wk_cogs.get(w, {}).get(br, 0)
        wk_vals.append(g / c if c else None)
    valid = [v for v in wk_vals if v]
    mean = sum(valid) / len(valid) if valid else 0
    sd = math.sqrt(sum((v - mean) ** 2 for v in valid) / len(valid)) if valid else 0
    flags = [(WEEKS[i], v) for i, v in enumerate(wk_vals) if v and abs(v - mean) > 2 * sd]
    # branch movers vs Dec
    sids = set(bsku['2026-05'].get(br, {})) | set(bsku[BASE].get(br, {}))
    mv = sorted([(bhead('2026-05', br, s) - bhead(BASE, br, s), s) for s in sids], key=lambda x: -abs(x[0]))
    mv_rows = ''
    for d_, s in mv[:14]:
        tag = ' <span class="newtag">ใหม่</span>' if s not in sku[BASE] else ''
        mv_rows += (f'<tr><td>{esc(name(s))}{tag}</td><td class="mut">{esc(cat(s))}</td>'
                    f'<td class="num">{f2(bhead(BASE,br,s))}</td><td class="num">{f2(bhead("2026-05",br,s))}</td>'
                    f'<td class="num">{dlt(d_)}</td>'
                    f'<td class="num small">{f1(bqph(BASE,br,s))}→{f1(bqph("2026-05",br,s))}</td></tr>')
    # top menus in branch (May)
    top_b = sorted(bsku['2026-05'].get(br, {}), key=lambda s: -(bsku['2026-05'][br][s]['cogs'] or 0))[:15]
    top_rows = ''
    for i, s in enumerate(top_b, 1):
        net_h = head('2026-05', s)
        bh = bhead('2026-05', br, s)
        top_rows += (f'<tr><td class="num mut">{i}</td><td>{esc(name(s))}</td><td class="mut">{esc(cat(s))}</td>'
                     f'<td class="num">{f2(bh)}</td><td class="num">{f2(net_h)}</td>'
                     f'<td class="num">{dlt(bh-net_h)}</td>'
                     f'<td class="num">{f1(bqph("2026-05",br,s))}</td></tr>')
    wk_table = ''
    for i, w in enumerate(WEEKS):
        v = wk_vals[i]
        flag = v is not None and abs(v - mean) > 2 * sd
        wk_table += (f'<td class="num{" flag" if flag else ""}">{f0(v) if v else "—"}</td>')
    flag_txt = (' · '.join(f'สัปดาห์ {w[5:]} = {f0(v)} บ.' for w, v in flags)) if flags else 'ไม่มีสัปดาห์หลุดกรอบ ±2SD'
    return f'''
    <div class="page-break"></div>
    <h2 id="br-{esc(br)}">7.{BRANCHES.index(br)+1}) สาขา{esc(br)} — ต้นทุน/หัว {f1(trend[0])} → {f1(trend[-1])} บ. ({dlt(trend[-1]-trend[0],1)} vs ธ.ค.) · ช่องว่างจากค่าเฉลี่ยเครือ {dlt(gap_may,1)} บ.</h2>
    <div class="kpis" style="grid-template-columns:repeat(4,1fr)">
      <div class="kpi"><div class="l">ลูกค้า พ.ค. 2026</div><div class="v">{f0(covers_bm['2026-05'].get(br,0))}</div><div class="l">หัว/เดือน</div></div>
      <div class="kpi"><div class="l">รายได้/หัว พ.ค.</div><div class="v">{f1(rv[-1])} บ.</div><div class="l">ธ.ค. = {f1(rv[0])} บ. ({dlt(dpct(rv[-1],rv[0]),1)}%)</div></div>
      <div class="kpi"><div class="l">Spread/หัว พ.ค. (รายได้−ต้นทุน)</div><div class="v">{f1(rv[-1]-trend[-1])} บ.</div><div class="l">ธ.ค. = {f1(rv[0]-trend[0])} บ.</div></div>
      <div class="kpi"><div class="l">ความผันผวนรายสัปดาห์ (ก.พ.–พ.ค.)</div><div class="v">±{f1(sd)} บ.</div><div class="l">SD รอบค่าเฉลี่ย {f1(mean)} บ./หัว</div></div>
    </div>
    <div class="cols2" style="align-items:center">
      <div>{svg_line([(f'สาขา{br}', col, trend), ('ค่าเฉลี่ยเครือ 5 สาขา', '#94a3b8', net)], [SH[m] for m in MONTHS], w=460, h=160)}</div>
      <div class="small">
        <b>Control check รายสัปดาห์ (ต้นทุน/หัว, ก.พ.–พ.ค.):</b> {esc(flag_txt)}<br><br>
        ค่าเฉลี่ยสัปดาห์ {f1(mean)} บ. · กรอบควบคุม {f1(mean-2*sd)}–{f1(mean+2*sd)} บ. (±2SD)
      </div>
    </div>
    <table class="small avoid-break"><thead><tr><th>สัปดาห์เริ่ม (จันทร์)</th>{''.join(f'<th class="num">{w[5:]}</th>' for w in WEEKS)}</tr></thead>
    <tbody><tr><td>ต้นทุน/หัว (บ.)</td>{wk_table}</tr></tbody></table>
    <div class="cols2">
      <div><h3>Top 15 เมนูต้นทุนสูงสุดของสาขา (พ.ค. 2026)</h3>
      <table><thead><tr><th>#</th><th>เมนู</th><th>หมวด</th><th class="num">บ./หัว สาขา</th><th class="num">บ./หัว เครือ</th><th class="num">Δ vs เครือ</th><th class="num">ชิ้น/100หัว</th></tr></thead><tbody>{top_rows}</tbody></table></div>
      <div><h3>เมนูที่เปลี่ยนแรงสุดของสาขา (พ.ค. vs ธ.ค.)</h3>
      <table><thead><tr><th>เมนู</th><th>หมวด</th><th class="num">ธ.ค.</th><th class="num">พ.ค.</th><th class="num">Δ/หัว</th><th class="num">ชิ้น/100หัว</th></tr></thead><tbody>{mv_rows}</tbody></table></div>
    </div>'''

branch_html = ''.join(br_chapter(br) for br in BRANCHES)

# deep-dive menu pages
def deep_page(idx, sid):
    nm = name(sid); c = cat(sid)
    hs = [head(m, sid) for m in MONTHS]
    qs = [qph(m, sid) for m in MONTHS]
    us = [unit(m, sid) for m in MONTHS]
    share_may = (sku['2026-05'][sid]['cogs'] or 0) / tot_cogs['2026-05'] * 100
    # branch May heads
    br_cells = ''.join(f'<td class="num">{f2(bhead("2026-05",br,sid))}<div class="sub">{dlt(bhead("2026-05",br,sid)-bhead(BASE,br,sid))}</div></td>' for br in BRANCHES)
    bheads = {br: bhead('2026-05', br, sid) for br in BRANCHES}
    bmax = max(bheads, key=bheads.get); bmin = min(bheads, key=bheads.get)
    bom = bom_map.get(sid) or []
    bom_rows = ''.join(f'<tr><td>{esc(b["ingredient_name"] or "")}</td><td class="num">{f2(b["qty_per_serving"] or 0)}</td>'
                       f'<td>{esc(b["unit"] or "")}</td><td class="num">{f2(b["price_per_unit"] or 0)}</td>'
                       f'<td class="num">{f2(b["ingredient_cost"] or 0)}</td></tr>' for b in bom[:6])
    bom_tot = sum(b['ingredient_cost'] or 0 for b in bom)
    # commentary
    is_new = sid not in sku[BASE]
    d_tot = hs[-1] - hs[0]
    u0 = next((u for u in us if u), 0); u5 = us[-1] or 0
    q0, q5 = qs[0], qs[-1]
    obs = []
    if is_new:
        obs.append(f'เมนูใหม่ (เริ่ม {LBL[new_first.get(sid, "2026-01")]}) — เพิ่มต้นทุนสุทธิ {f2(hs[-1])} บ./หัว ณ พ.ค.')
    else:
        obs.append(f'ต้นทุน/หัวเปลี่ยน {f2(hs[0])} → {f2(hs[-1])} บ. ({dlt(d_tot)})')
    if u0 and u5 and abs(u5/u0 - 1) > 0.05:
        obs.append(f'ต้นทุน/ชิ้นเปลี่ยน {f2(u0)} → {f2(u5)} บ. ({dlt(dpct(u5,u0),1)}%) — สัญญาณด้านจัดซื้อ/สูตร')
    else:
        obs.append('ต้นทุน/ชิ้นแทบไม่เปลี่ยน — การเคลื่อนไหวมาจากพฤติกรรมการกินเป็นหลัก')
    if q0 and abs(q5/q0 - 1) > 0.1:
        obs.append(f'การกิน {f1(q0)} → {f1(q5)} ชิ้น/100หัว ({dlt(dpct(q5,q0),1)}%)')
    obs.append(f'สาขาที่กินแรงสุด: {bmax} ({f2(bheads[bmax])} บ./หัว) เทียบต่ำสุด {bmin} ({f2(bheads[bmin])} บ./หัว) — ช่องว่าง {f2(bheads[bmax]-bheads[bmin])} บ./หัว')
    if not bom:
        obs.append('⚠ ไม่พบ BoM ตรงของ SKU นี้ (ต้นทุนอาจมาจาก alias/unit_cost)')
    month_rows = (f'<tr><td>ชิ้น/100 หัว</td>{"".join(f"<td class=num>{f1(q)}</td>" for q in qs)}</tr>'
                  f'<tr><td>ต้นทุน/ชิ้น (บ.)</td>{"".join(f"<td class=num>{f2(u) if u else chr(8212)}</td>" for u in us)}</tr>'
                  f'<tr><td><b>ต้นทุน/หัว (บ.)</b></td>{"".join(f"<td class=num><b>{f2(h)}</b></td>" for h in hs)}</tr>')
    return f'''
    <div class="avoid-break deep">
    <h3>8.{idx}) {esc(nm)} <span class="mut small">หมวด {esc(c)} · {f1(share_may)}% ของต้นทุนบุฟเฟ่ต์ทั้งหมด (พ.ค.)</span>{' <span class="newtag">ใหม่</span>' if is_new else ''}</h3>
    <div class="cols2" style="align-items:flex-start">
      <div>
        <table><thead><tr><th>ตัวชี้วัด</th>{''.join(f'<th class="num">{SH[m]}</th>' for m in MONTHS)}</tr></thead><tbody>{month_rows}</tbody></table>
        <table><thead><tr><th>บ./หัว พ.ค. รายสาขา (ล่าง = Δ vs ธ.ค.)</th>{''.join(f'<th class="num">{esc(b)}</th>' for b in BRANCHES)}</tr></thead>
        <tbody><tr><td>ต้นทุน/หัว</td>{br_cells}</tr></tbody></table>
      </div>
      <div>
        {svg_line([('ต้นทุน/หัว', '#4338ca', hs)], [SH[m] for m in MONTHS], w=380, h=130, fmt=f1)}
        {f'<table class="small"><thead><tr><th>วัตถุดิบหลัก (BoM/เสิร์ฟ)</th><th class="num">ปริมาณ</th><th>หน่วย</th><th class="num">ราคา/หน่วย</th><th class="num">ต้นทุน</th></tr></thead><tbody>{bom_rows}<tr><td colspan="4"><b>รวมต้นทุน/เสิร์ฟ</b></td><td class="num"><b>{f2(bom_tot)}</b></td></tr></tbody></table>' if bom_rows else ''}
      </div>
    </div>
    <ul class="small">{''.join(f'<li>{o}</li>' for o in obs)}</ul>
    </div>'''

deep_html = ''.join(deep_page(i + 1, s) for i, s in enumerate(DEEP))

# new menus full table
new_rows = ''
zero_bom = []
NEW_LIMIT = 20
# rank by per-head impact first, volume as tiebreaker, so cheap-but-popular
# zero-BoM items (bacon wraps) still make the list
sig_new = sorted(new_first, key=lambda s: (-(max(head(m, s) for m in M26)),
                                           -(max((sku[m][s]['qty'] or 0) for m in M26 if s in sku[m]))))
n_new_shown = 0
for sid in sig_new:
    if n_new_shown >= NEW_LIMIT: break
    last_m = max((m for m in M26 if sid in sku[m]), default=None)
    if not last_m: continue
    q_l = sku[last_m][sid]['qty'] or 0
    q_max = max((sku[m][sid]['qty'] or 0) for m in M26 if sid in sku[m])
    if q_max < 100 and max(head(m, sid) for m in M26) < 0.05: continue
    n_new_shown += 1
    has_bom = (sku[last_m][sid]['cogs'] or 0) > 0
    if not has_bom: zero_bom.append((sid, last_m, q_l))
    cells = ''.join(f'<td class="num">{f2(head(m,sid)) if sku[m].get(sid) else "—"}</td>' for m in M26)
    new_rows += (f'<tr><td>{esc(name(sid))}</td><td class="mut">{esc(cat(sid))}</td>'
                 f'<td class="num">{SH[new_first[sid]]}</td>{cells}'
                 f'<td class="num">{f0(q_l)}</td>'
                 f'<td>{"<span class=ok>มี BoM</span>" if has_bom else "<span class=warn>COGS=0</span>"}</td></tr>')
zero_qty_may = sum(q for s, m, q in zero_bom if m == '2026-05')

# pareto top 60 + cost intensity
p_rows = ''; acc = 0
for i, sid in enumerate(may_rank[:60], 1):
    cg = sku['2026-05'][sid]['cogs'] or 0; acc += cg
    share = cg / tot_cogs['2026-05'] * 100
    p_rows += (f'<tr><td class="num mut">{i}</td><td>{esc(name(sid))}</td><td class="mut">{esc(cat(sid))}</td>'
               f'<td class="num">{f0(cg)}</td><td class="num">{f2(head("2026-05",sid))}</td>'
               f'<td class="num">{f1(share)}%</td><td class="num">{f1(acc/tot_cogs["2026-05"]*100)}%</td>'
               f'<td><div class="bar" style="width:{min(100, share/17.4*100):.0f}%"></div></td></tr>')
tot_qty_may = sum(r['qty'] or 0 for r in per_month['2026-05'])
ci_rows = ''
for i, sid in enumerate(may_rank[:25], 1):
    cg = sku['2026-05'][sid]['cogs'] or 0; q = sku['2026-05'][sid]['qty'] or 0
    cs = cg / tot_cogs['2026-05'] * 100; qs_ = q / tot_qty_may * 100
    idx = cs / qs_ if qs_ else 0
    ci_rows += (f'<tr><td class="num mut">{i}</td><td>{esc(name(sid))}</td>'
                f'<td class="num">{f1(cs)}%</td><td class="num">{f1(qs_)}%</td>'
                f'<td class="num"><b>{f1(idx)}×</b></td>'
                f'<td class="num">{f2(unit("2026-05",sid) or 0)}</td></tr>')

# cannibalization sets
EEL_CODES = {'B0324', 'B0325', 'B0326', 'B0327', 'B0328'}   # new unagi menus — BoM = temporary 8฿/pc estimate (2026-06-10)
eel_sids = [s for s in meta if (meta[s].get('sku_code') or '') in EEL_CODES]
eel_qty_may = sum((sku['2026-05'][s]['qty'] or 0) for s in eel_sids if s in sku['2026-05'])
eel_head_may = sum(head('2026-05', s) for s in eel_sids)

def qrow(label, ids):
    ids = list(dict.fromkeys(ids))   # dedupe — combined find() groups may overlap
    cells = ''
    for m in MONTHS:
        v = sum(qph(m, s) for s in ids)
        cells += f'<td class="num">{f1(v)}</td>'
    h0 = sum(head(BASE, s) for s in ids); h5 = sum(head('2026-05', s) for s in ids)
    return f'<tr><td>{label}</td>{cells}<td class="num">{dlt(h5-h0)}</td></tr>'
def find(part, pool=None):
    return [s for s in (pool or meta) if part in (meta[s]['description'] or '')]
cann_rows = (
    # ── คู่ที่ 1: ยำ — ของใหม่ราคาถูกแทนของเดิม ──
    qrow('🪼 แมงกะพรุน 3 เมนู (ใหม่ เม.ย.)', find('แมงกะพรุน')) +
    qrow('ยำไทยซีฟู้ด (เดิม)', find('ยำไทยซีฟู้ด')) +
    qrow('ยำแซลมอนน้ำปลาร้า (เดิม — โตสวนทาง)', find('ยำแซลมอนน้ำปลาร้า')) +
    # ── คู่ที่ 2: ของทานเล่น/แป้ง — โดนเทเพื่อโปรตีน ──
    qrow('🥟 เบคอนพัน (ใหม่ ก.พ.–มี.ค., COGS=0)', find('เบคอนพัน')) +
    qrow('ขนมครกญี่ปุ่น (เดิม)', find('ขนมครกญี่ปุ่น')) +
    qrow('🍣 โรล/ข้าวห่อสาหร่าย ทั้งหมวด (เดิม)', [s for s in meta if cat(s) == 'ROLL']) +
    qrow('ซูชิ ทั้งหมวด (เดิม)', [s for s in meta if cat(s) == 'SUSHI']) +
    qrow('ของทานเล่น APPETIZER ทั้งหมวด (เดิม)', [s for s in meta if cat(s) == 'APPETIZER']) +
    qrow('ข้าวห่อสาหร่ายไส้สไปซี่แซลมอน (เดิม)', find('สไปซี่แซลมอน')) +
    # ── คู่ที่ 3: โปรตีนพรีเมียม — ใหม่ดึงการกินจากเดิม ──
    qrow('🦪 หอยเชลล์ฮอกไกโด ต้ม+สเต็ก (ใหม่ ก.พ.)', find('หอยเชลล์')) +
    qrow('แซลมอนซาชิมิ (เดิม — ตัวถ่วงดุลหลัก)', find('แซลมอน ซาชิมิ')) +
    qrow('ทูน่าซาชิมิ (เดิม)', find('ทูน่า ซาชิมิ')) +
    qrow('ชุดปลาดิบรวม 4 อย่าง (เดิม)', find('ชุดปลาดิบรวม')) +
    qrow('กุ้งอาเจนติน่าซาชิมิ (เดิม — โตแรง)', find('กุ้งอาเจนติน่า')) +
    qrow('หอยนางรม (เดิม — โต)', find('หอยนางรม')) +
    qrow('กุ้งสด+กุ้งสดดอง (เดิม)', find('กุ้งสด')) +
    # ── คู่ที่ 4: เนื้อ/สเต็ก — ใหม่เข้าสั้น ๆ แล้วถอด ──
    qrow('🥩 เนื้อคารูบิ 3 เมนู (ใหม่ เม.ย. — ถอดแล้ว)', find('คารูบิ')) +
    qrow('สเต็กเนื้อลูกเต๋า (เดิม)', find('สเต็กเนื้อลูกเต๋า')) +
    qrow('เนื้อเสือร้องไห้สไลด์ (เดิม)', find('เสือร้องไห้')) +
    # ── คู่ที่ 5: ซูชิพรีเมียมใหม่ ──
    qrow('🍚 คาโมะ/โอโทโร่ กลุ่มใหม่ (ก.พ.–มี.ค.)', find('คาโมะ') + find('โอโทโร่')) +
    qrow('🍤 ปลาไหลญี่ปุ่นกลุ่มใหม่ (เริ่ม มี.ค. · ประมาณการ 8 บ./ชิ้น)', eel_sids)
)

# appendix: all menus
app_rows = ''
all_rank = sorted(set(meta), key=lambda s: -(max(head(m, s) for m in MONTHS)))
for i, sid in enumerate(all_rank, 1):
    if max(head(m, sid) for m in MONTHS) < 0.01 and max(qph(m, sid) for m in MONTHS) < 0.5: continue
    cells = ''.join(f'<td class="num">{f2(head(m,sid)) if sku[m].get(sid) else "—"}</td>' for m in MONTHS)
    app_rows += (f'<tr><td class="num mut">{i}</td><td>{esc(name(sid))}</td><td class="mut">{esc(cat(sid))}</td>'
                 f'{cells}<td class="num">{dlt(head("2026-05",sid)-head(BASE,sid))}</td></tr>')

# summary tables (set1/set2)
set1_rows = ''.join(
    f'<tr><td><b>{LBL[m]}</b></td><td class="num">{f0(cov[m])}</td>'
    f'<td class="num">{f2(tot_head[m])}</td><td class="num">{f2(tot_head[BASE])}</td>'
    f'<td class="num">{dlt(tot_head[m]-tot_head[BASE])}</td>'
    f'<td class="num">{dlt(dpct(tot_head[m],tot_head[BASE]),1)}%</td>'
    f'<td class="num">{f1(rev_head[m])}</td><td class="num">{f1(spread[m])}</td>'
    f'<td class="num">{f1(ratio[m])}%</td></tr>' for m in M26)
set2_rows = ''.join(
    f'<tr><td><b>{LBL[m]}</b></td><td class="mut">{LBL[pm]}</td>'
    f'<td class="num">{f2(tot_head[pm])}</td><td class="num">{f2(tot_head[m])}</td>'
    f'<td class="num">{dlt(tot_head[m]-tot_head[pm])}</td>'
    f'<td class="num">{dlt(dpct(tot_head[m],tot_head[pm]),1)}%</td></tr>' for m, pm in chain)

# overview charts
ch_cost = svg_line([('ต้นทุน/หัว', '#4338ca', [tot_head[m] for m in MONTHS]),
                    ('รายได้/หัว (ex-VAT)', '#059669', [rev_head[m] for m in MONTHS])],
                   [SH[m] for m in MONTHS])
ch_spread = svg_line([('Spread/หัว (รายได้−ต้นทุนบุฟเฟ่ต์)', '#d97706', [spread[m] for m in MONTHS])],
                     [SH[m] for m in MONTHS], h=150)
ch_br = svg_line([(br, BR_COLORS[br], [br_head[m][br] for m in MONTHS]) for br in BRANCHES],
                 [SH[m] for m in MONTHS], h=190)
ch_ratio = svg_line([('Cost ratio %', '#dc2626', [ratio[m] for m in MONTHS])],
                    [SH[m] for m in MONTHS], h=140, fmt=lambda v: f"{v:.0f}%")

scallop_grp = find('หอยเชลล์')
scallop_head_may = sum(head('2026-05', s) for s in scallop_grp)
salmon_sids = find('แซลมอน ซาชิมิ')
covers5 = cov['2026-05']

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
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:12px 0}
.kpi{border:1px solid var(--line);border-radius:10px;padding:9px 12px;background:var(--bg)}
.kpi .v{font-size:19px;font-weight:700}.kpi .l{font-size:10.5px;color:var(--mut)}
.note{background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;padding:8px 12px;font-size:12px;margin:10px 0}
.danger{background:#fef2f2;border-color:#fca5a5}
.okbox{background:#ecfdf5;border:1px solid #6ee7b7;border-radius:8px;padding:8px 12px;font-size:12px;margin:10px 0}
.newtag{background:#4338ca;color:#fff;border-radius:6px;font-size:9px;padding:1px 5px;vertical-align:middle;white-space:nowrap}
.ok{color:var(--grn);font-weight:600}.warn{color:var(--red);font-weight:700}
.bar{background:var(--acc);height:8px;border-radius:3px;min-width:2px}
.cols2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.avoid-break{break-inside:avoid;page-break-inside:avoid}
.page-break{break-before:page;page-break-before:always}
.cover{border-bottom:3px solid var(--acc);padding-bottom:14px;margin-bottom:12px}
.legend{margin-right:12px;font-size:10.5px;color:#475569;white-space:nowrap}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.toc{columns:2;font-size:12.5px}
.toc a{color:var(--ink);text-decoration:none}
.toc li{margin:3px 0}
ol li,ul li{margin:4px 0}
.printbtn{position:fixed;top:14px;right:14px;background:var(--acc);color:#fff;border:0;border-radius:8px;padding:9px 16px;font-size:13px;cursor:pointer;font-family:inherit;z-index:9}
.bridge td:first-child{padding-left:14px}
@media print{
  .printbtn{display:none}
  body{font-size:10.5px}
  .page{padding:0;max-width:none}
  h2{break-after:avoid}
  table{font-size:9.3px}
  th{font-size:9px}
  @page{size:A4;margin:11mm 9mm}
  thead{display:table-header-group}
  .cols2{gap:8px}
}
"""

# ───────────────────────────── assemble ─────────────────────────────
TOC = """
<ol class="toc">
<li><a href="#s1">1. บทสรุปผู้บริหาร</a></li>
<li><a href="#s2">2. วิธีวิเคราะห์และขอบเขตข้อมูล</a></li>
<li><a href="#s3">3. ภาพรวมเครือ 6 เดือน</a></li>
<li><a href="#s4">4. ชุดที่ 1 — เทียบฐานคงที่ ธ.ค. 2025 (ตารางเมนู Top 80)</a></li>
<li><a href="#s5">5. ชุดที่ 2 — เทียบเดือนก่อนหน้า (MoM) + Bridge รายเดือน</a></li>
<li><a href="#s6">6. วิเคราะห์รายหมวดเมนู</a></li>
<li><a href="#s7">7. วิเคราะห์รายสาขา (5 บท + control chart รายสัปดาห์)</a></li>
<li><a href="#s8">8. เจาะลึกเมนูยุทธศาสตร์ 12 เมนู (BoM + รายสาขา)</a></li>
<li><a href="#s9">9. เมนูใหม่ทั้งหมด + Cannibalization + ความเหมาะสม</a></li>
<li><a href="#s10">10. Pareto + ดัชนีความเข้มข้นต้นทุน</a></li>
<li><a href="#s11">11. แผนปรับลดต้นทุน + Roadmap 90 วัน</a></li>
<li><a href="#s12">12. Playbook บริหารเครือบุฟเฟ่ต์ญี่ปุ่นระดับโลก</a></li>
<li><a href="#s13">13. ความเสี่ยงและคุณภาพข้อมูล</a></li>
<li><a href="#appA">ภาคผนวก ก. ตารางเมนูทั้งหมด</a></li>
<li><a href="#appB">ภาคผนวก ข. นิยามศัพท์</a></li>
</ol>"""

html_out = f"""<!DOCTYPE html>
<html lang="th"><head><meta charset="utf-8">
<title>รายงานเจาะลึกต้นทุนเมนูบุฟเฟ่ต์ต่อหัว A vs B · ม.ค.–พ.ค. 2026 (ฐาน ธ.ค. 2025)</title>
<style>{CSS}</style></head><body>
<button class="printbtn" onclick="window.print()">🖨 พิมพ์ / บันทึก PDF</button>
<div class="page">

<div class="cover">
  <h1>📕 รายงานเจาะลึกต้นทุนเมนูบุฟเฟ่ต์ — ต่อหัว (A vs B)</h1>
  <p><b>สาขา Master 5 สาขา</b>: โคราช1 · เชียงใหม่ · สุรินทร์ · อุดรธานี · อุบลราชธานี (ไม่รวมโคราช2)
  · ช่วงวิเคราะห์ <b>ม.ค.–พ.ค. 2026</b> · ฐาน <b>ธ.ค. 2025</b> (ชุดที่ 1) และ <b>เดือนก่อนหน้า</b> (ชุดที่ 2)</p>
  <p class="small mut">เกณฑ์เดียวกับ "ตารางที่ 2 · เทียบเมนู A vs B — ต่อหัว": เฉพาะเมนูบุฟเฟ่ต์ (B) · ต้นทุน/หัว = COGS (BoM) ÷ ลูกค้าของช่วงนั้น · ไม่รวม à-la-carte (A) · รายได้ ex-VAT · จัดทำ 10 มิ.ย. 2026</p>
  <h3>สารบัญ</h3>{TOC}
</div>

<h2 id="s1">1) บทสรุปผู้บริหาร</h2>
<div class="kpis">
  <div class="kpi"><div class="l">ต้นทุน/หัว ฐาน ธ.ค. 2025</div><div class="v">{f2(tot_head[BASE])} บ.</div><div class="l">ลูกค้า {f0(cov[BASE])} หัว</div></div>
  <div class="kpi"><div class="l">ต้นทุน/หัว พ.ค. 2026</div><div class="v">{f2(tot_head['2026-05'])} บ.</div><div class="l">{dlt(tot_head['2026-05']-tot_head[BASE])} บ. ({dlt(dpct(tot_head['2026-05'],tot_head[BASE]),1)}%) vs ธ.ค.</div></div>
  <div class="kpi"><div class="l">จุดพีค มี.ค. 2026</div><div class="v">{f2(tot_head['2026-03'])} บ.</div><div class="l">{dlt(dpct(tot_head['2026-03'],tot_head[BASE]),1)}% vs ธ.ค. — สูงสุดของช่วง</div></div>
  <div class="kpi"><div class="l">Spread/หัว พ.ค. (รายได้−ต้นทุน)</div><div class="v">{f1(spread['2026-05'])} บ.</div><div class="l">ธ.ค. = {f1(spread[BASE])} บ. (+{f1(dpct(spread['2026-05'],spread[BASE]))}%)</div></div>
</div>
<ul>
  <li><b>ต้นทุน/หัวสูงกว่าฐาน ธ.ค. 2025 ตลอด 5 เดือน</b> — ไต่ขึ้นถึงพีค มี.ค. ({f2(tot_head['2026-03'])} บ., {dlt(tot_head['2026-03']-tot_head[BASE])} บ./หัว) แล้วถูกดึงกลับเหลือ {dlt(tot_head['2026-05']-tot_head[BASE])} บ./หัว ({dlt(dpct(tot_head['2026-05'],tot_head[BASE]),1)}%) ใน พ.ค. — เส้นทางนี้คือผลของ "ใส่ของแพงเข้า แล้วค่อยหาจุดสมดุล"</li>
  <li><b>~85% ของส่วนเพิ่ม ณ จุดพีค ({dlt(tot_head['2026-03']-tot_head[BASE])} บ./หัว) มาจากเมนูใหม่กลุ่มหอยเชลล์ฮอกไกโด</b> (ต้ม +31.2, สเต็ก +6.2 บ./หัว ณ มี.ค.) — เดือน เม.ย.–พ.ค. ถูกบริหารลงมาเหลือรวม ~{f1(scallop_head_may)} บ./หัว · ส่วนที่เหลือคือปลาไหลใหม่ที่เริ่มเข้าม มี.ค. (+2.0 บ./หัว)</li>
  <li><b>แซลมอนซาชิมิ — เมนูต้นทุนอันดับ 1 ({f1((sku['2026-05'][salmon_sids[0]]['cogs'] or 0)/tot_cogs['2026-05']*100) if salmon_sids else '17'}% ของต้นทุนทั้งหมด)</b> ลดจาก 72.72 → 63.47 บ./หัว (−9.25) ใน เม.ย.–พ.ค. จากการกิน/หัวที่ลดลง (ไม่ใช่ราคาปลาลง) — ตัวถ่วงดุลหลักของระบบ</li>
  <li><b>เศรษฐศาสตร์ต่อหัวดีขึ้นสุทธิ:</b> รายได้/หัว 644 → 727 บ. (+12.9%) โตเร็วกว่าต้นทุน → spread/หัว {f1(spread[BASE])} → {f1(spread['2026-05'])} บ. (+{f1(dpct(spread['2026-05'],spread[BASE]))}%) และ cost ratio {f1(ratio[BASE])}% → {f1(ratio['2026-05'])}%</li>
  <li><b>ศักยภาพลดต้นทุนที่ระบุได้เป็นรูปธรรม ≈ −15 บ./หัว (~620,000 บ./เดือน)</b> จาก 5 มาตรการ (ส่วนที่ 11)</li>
  <li>ℹ️ เมนูใหม่กลุ่มปลาไหลญี่ปุ่น (เริ่ม มี.ค. · พ.ค. ขาย {f0(eel_qty_may)} ชิ้น) ใช้<b>ต้นทุนประมาณการชั่วคราว 8 บ./ชิ้น</b> (ใส่ 10 มิ.ย. 2026 — รวมในตัวเลขรายงานแล้ว +{f2(eel_head_may)} บ./หัว) ต้องแทนที่ด้วย BoM จริงก่อนปิดงวด มิ.ย. · กลุ่มเบคอนพันยัง COGS = 0 (ส่วนที่ 13)</li>
</ul>

<h2 id="s2">2) วิธีวิเคราะห์และขอบเขตข้อมูล</h2>
<table class="avoid-break">
<tbody>
<tr><td style="width:200px"><b>ขอบเขตสาขา</b></td><td>สาขา master 5 สาขา (โคราช1, เชียงใหม่, สุรินทร์, อุดรธานี, อุบลราชธานี) — <b>ไม่รวมโคราช2ทุกช่วง รวมถึงฐาน ธ.ค. 2025</b> (โคราช2 หยุดดำเนินการตั้งแต่ ธ.ค. 2025)</td></tr>
<tr><td><b>ขอบเขตเมนู</b></td><td>เฉพาะเมนูบุฟเฟ่ต์ — SKU ที่ขึ้นต้น (B) หรือไม่ใช่ (A) à-la-carte, หมวดอาหารเท่านั้น (ไม่รวมเครื่องดื่ม) เกณฑ์เดียวกับหน้า "เทียบเมนู A vs B" ของแดชบอร์ด</td></tr>
<tr><td><b>ต้นทุน (COGS)</b></td><td>BoM ingredient cost ต่อเสิร์ฟ × จำนวนชิ้นขาย ผ่าน helper มาตรฐานของระบบ (เมนูแพ็กเกจบุฟเฟ่ต์เอง COGS = 0 กันการนับซ้ำ · alias BoM และ unit_cost fallback ตามลำดับ)</td></tr>
<tr><td><b>ต่อหัว</b></td><td>ต้นทุน/หัว = COGS รวมของช่วง ÷ จำนวนลูกค้า (covers) ของช่วงเดียวกัน · ชิ้น/100 หัว = จำนวนชิ้น ÷ ลูกค้า × 100</td></tr>
<tr><td><b>รายได้</b></td><td>total_inc_vat ÷ 1.07 = ex-VAT (เกณฑ์ที่กระทบยอดกับบัญชี) · cost ratio = COGS บุฟเฟ่ต์ ÷ รายได้รวม ex-VAT (ตัวหารรวมเครื่องดื่ม/อื่น ๆ — ใช้ดูทิศทาง ไม่ใช่ food cost % บัญชีเป๊ะ)</td></tr>
<tr><td><b>การแยกสาเหตุ</b></td><td>Δต้นทุน/หัว ของเมนูเดิม = ผลจากการกิน (ชิ้น/หัวเปลี่ยน × ต้นทุน/ชิ้นเดิม) + ผลจากราคา (ชิ้น/หัวใหม่ × Δต้นทุน/ชิ้น) — แสดงเป็นตาราง Bridge ทุกเดือนในส่วนที่ 5</td></tr>
<tr><td><b>ข้อมูลรายสัปดาห์</b></td><td>ต้นทุน/หัวรายสัปดาห์ (จันทร์–อาทิตย์) จากยอดขายรายวัน มีตั้งแต่ 9 ก.พ. 2026 — ใช้ทำ control chart ในบทรายสาขา</td></tr>
<tr><td><b>แหล่งข้อมูล</b></td><td>oshinei.db (sales_sku_monthly · sales_sku_daily · sales_transaction · bom_ingredients) sync จาก Easy POS HQ · ณ 10 มิ.ย. 2026</td></tr>
</tbody></table>

<h2 id="s3">3) ภาพรวมเครือ 6 เดือน</h2>
<table class="avoid-break">
<thead><tr><th>เดือน</th><th class="num">ลูกค้า (หัว)</th><th class="num">รายได้ ex-VAT (บ.)</th><th class="num">รายได้/หัว</th><th class="num">COGS บุฟเฟ่ต์ (บ.)</th><th class="num">ต้นทุน/หัว</th><th class="num">Spread/หัว</th><th class="num">Cost ratio</th><th class="num">เมนู active</th></tr></thead>
<tbody>{''.join(f'<tr><td><b>{LBL[m]}</b></td><td class="num">{f0(cov[m])}</td><td class="num">{f0(REV[m])}</td><td class="num">{f1(rev_head[m])}</td><td class="num">{f0(tot_cogs[m])}</td><td class="num">{f2(tot_head[m])}</td><td class="num">{f1(spread[m])}</td><td class="num">{f1(ratio[m])}%</td><td class="num">{len(sku[m])}</td></tr>' for m in MONTHS)}</tbody>
</table>
<div class="cols2 avoid-break">
  <div><h3>ต้นทุน/หัว vs รายได้/หัว</h3>{ch_cost}</div>
  <div><h3>Spread ต่อหัว (กำไรขั้นต้นเชิงวัตถุดิบ)</h3>{ch_spread}</div>
</div>
<div class="cols2 avoid-break">
  <div><h3>ต้นทุน/หัวรายสาขา</h3>{ch_br}</div>
  <div><h3>Cost ratio (COGS ÷ รายได้ ex-VAT)</h3>{ch_ratio}</div>
</div>
<ul>
  <li><b>ฤดูกาลชัด:</b> ม.ค. และ พ.ค. เป็นเดือน volume สูง (41,000+ หัว) ขณะ มี.ค. ต่ำสุด (29,124 หัว) — เดือน volume ต่ำต้นทุน/หัวมักสูงขึ้นเพราะ fixed prep/waste ต่อหัวหนาขึ้น และสัดส่วนลูกค้า tier สูงเพิ่ม</li>
  <li><b>รายได้/หัวโตต่อเนื่องทุกเดือน</b> (644→727 บ. ไม่มีเดือนถอย) ขณะต้นทุน/หัวพีคแล้วย่อ — แปลว่าการอัพเกรดเมนูแปลงเป็นรายได้ได้จริง ไม่ใช่แค่ต้นทุนบวม</li>
  <li><b>อันดับสาขาคงเส้นคงวา:</b> เชียงใหม่แพงสุดทุกเดือน สุรินทร์ถูกสุดทุกเดือน — ช่องว่าง 70–90 บ./หัว บนเมนูชุดเดียวกันคือโอกาสปฏิบัติการที่ใหญ่ที่สุดรองจากการคุมเมนูพรีเมียม</li>
</ul>

<div class="page-break"></div>
<h2 id="s4">4) ชุดที่ 1 — A = แต่ละเดือนปี 2026 · B = ธ.ค. 2025 (ฐานคงที่)</h2>
<table class="avoid-break">
<thead><tr><th>เดือน A</th><th class="num">ลูกค้า</th><th class="num">ต้นทุน/หัว A</th><th class="num">B (ธ.ค.25)</th><th class="num">Δ บ./หัว</th><th class="num">Δ %</th><th class="num">รายได้/หัว</th><th class="num">Spread/หัว</th><th class="num">Cost ratio</th></tr></thead>
<tbody>{set1_rows}</tbody>
</table>
<h3>ตารางเมนูรายตัว Top {TOPN} (เรียงตามต้นทุน พ.ค.) — ต้นทุน/หัว (บ.) + Δ เทียบ ธ.ค. 2025</h3>
<p class="small mut">ตัวเลขบน = ต้นทุน/หัวของเดือนนั้น · ตัวเลขล่าง = Δ เทียบฐาน ธ.ค. 2025 · แดง = เพิ่ม เขียว = ลด · "ใหม่" = ไม่มีในฐาน</p>
<table>
<thead><tr><th>#</th><th>เมนู (บุฟเฟ่ต์)</th><th>หมวด</th><th class="num">ธ.ค.25 (B)</th>{th_months()}<th class="num">Δ พ.ค.−ธ.ค.</th><th class="num">Δ%</th></tr></thead>
<tbody>
{matrix_table(top_ids)}
<tr><td></td><td><b>เมนูอื่น ๆ ({len(sku['2026-05'])-TOPN} เมนู)</b></td><td></td><td class="num">{f2(oth_base)}</td>{oth_cells}<td class="num">{dlt(oth_may-oth_base)}</td><td class="num">{dlt(dpct(oth_may,oth_base),1)}</td></tr>
</tbody>
<tfoot><tr><td></td><td>รวมทุกเมนู</td><td></td><td class="num">{f2(tot_head[BASE])}</td>{tot_cells}<td class="num">{dlt(tot_head['2026-05']-tot_head[BASE])}</td><td class="num">{dlt(dpct(tot_head['2026-05'],tot_head[BASE]),1)}</td></tr></tfoot>
</table>

<div class="page-break"></div>
<h2 id="s5">5) ชุดที่ 2 — A = แต่ละเดือน · B = เดือนก่อนหน้า (MoM chain) + Bridge</h2>
<table class="avoid-break">
<thead><tr><th>เดือน A</th><th>ฐาน B</th><th class="num">ต้นทุน/หัว B</th><th class="num">A</th><th class="num">Δ บ./หัว</th><th class="num">Δ %</th></tr></thead>
<tbody>{set2_rows}</tbody>
</table>
<p>เส้นทาง 5 เดือน: ม.ค. +2.0% (ฐานปกติ) → ก.พ. +4.4% และ มี.ค. +5.9% (สองเดือนที่เมนูหอยเชลล์เข้าระบบเต็มตัว และปลาไหลใหม่เริ่มเข้าม มี.ค.) → เม.ย. −4.7% (ดึงกลับแรงสุด — ลดทั้งซาชิมิ/หัวและหอยเชลล์ แม้ปลาไหลขยายตัว +2.5 บ./หัว) → พ.ค. {dlt(dpct(tot_head['2026-05'],tot_head['2026-04']),1)}% (ทรงตัว) · ตัวเลขทุกเดือนรวมประมาณการปลาไหล 8 บ./ชิ้นแล้ว</p>
{mom_html}

<div class="page-break"></div>
<h2 id="s6">6) วิเคราะห์รายหมวดเมนู (ต้นทุน/หัว ธ.ค.25 → พ.ค.26)</h2>
<table class="avoid-break">
<thead><tr><th>หมวด</th><th class="num">ธ.ค.25</th>{th_months()}<th class="num">Δ พ.ค.−ธ.ค.</th><th class="num">% ของต้นทุน พ.ค.</th></tr></thead>
<tbody>{''.join(f'<tr><td><b>{esc(c)}</b></td><td class="num">{f2(cat_head[BASE].get(c,0))}</td>{"".join(f"<td class=num>{f2(cat_head[m].get(c,0))}</td>" for m in M26)}<td class="num">{dlt(cat_head["2026-05"].get(c,0)-cat_head[BASE].get(c,0))}</td><td class="num">{f1(cat_head["2026-05"].get(c,0)/tot_head["2026-05"]*100)}%</td></tr>' for c in CATS)}</tbody>
<tfoot><tr><td>รวม</td><td class="num">{f2(tot_head[BASE])}</td>{''.join(f'<td class="num">{f2(tot_head[m])}</td>' for m in M26)}<td class="num">{dlt(tot_head['2026-05']-tot_head[BASE])}</td><td class="num">100%</td></tr></tfoot>
</table>
<p class="small">โครงสร้างการเปลี่ยนแปลงชัดเจน: <b>หมวดโปรตีนพรีเมียม (SASHIMI/STEAK/YUM) รับต้นทุนเพิ่มเกือบทั้งหมด</b> ขณะหมวดแป้ง/ของทานเล่น (APPETIZER/ROLL/SUSHI/SOBA/SALAD) ถูกกินน้อยลงพร้อมกัน −18.4 บ./หัว — พฤติกรรม "trade-up" ของลูกค้าเมื่อมีของพรีเมียมให้เลือก ซึ่งดีต่อความพึงพอใจแต่กดดันต้นทุน</p>
{cat_html}

<div class="page-break"></div>
<h2 id="s7">7) วิเคราะห์รายสาขา</h2>
<table class="avoid-break">
<thead><tr><th>สาขา</th>{''.join(f'<th class="num">{SH[m]}</th>' for m in MONTHS)}<th class="num">Δ พ.ค.−ธ.ค.</th><th class="num">vs เครือ (พ.ค.)</th></tr></thead>
<tbody>{''.join(f'<tr><td><b>{esc(br)}</b></td>{"".join(f"<td class=num>{f1(br_head[m][br])}</td>" for m in MONTHS)}<td class="num">{dlt(br_head["2026-05"][br]-br_head[BASE][br],1)}</td><td class="num">{dlt(br_head["2026-05"][br]-tot_head["2026-05"],1)}</td></tr>' for br in BRANCHES)}</tbody>
<tfoot><tr><td>เฉลี่ยเครือ</td>{''.join(f'<td class="num">{f1(tot_head[m])}</td>' for m in MONTHS)}<td class="num">{dlt(tot_head['2026-05']-tot_head[BASE],1)}</td><td class="num">—</td></tr></tfoot>
</table>
{branch_html}

<div class="page-break"></div>
<h2 id="s8">8) เจาะลึกเมนูยุทธศาสตร์ — Top 12 ต้นทุนสูงสุด (รวม {f1(sum((sku['2026-05'][s]['cogs'] or 0) for s in DEEP)/tot_cogs['2026-05']*100)}% ของต้นทุนทั้งหมด)</h2>
{deep_html}

<div class="page-break"></div>
<h2 id="s9">9) เมนูใหม่ — ผลกระทบ, Cannibalization และความเหมาะสม</h2>
<table class="avoid-break">
<thead><tr><th>ผลกระทบรวมของเมนูใหม่ (บ./หัว เทียบฐาน ธ.ค.25)</th>{th_months()}</tr></thead>
<tbody><tr><td>ต้นทุน/หัวของเมนูที่ไม่มีในฐาน</td>{''.join(f'<td class="num">{f2(new_impact[m])}</td>' for m in M26)}</tr></tbody>
</table>
<h3>รายการเมนูใหม่ที่มีนัยสำคัญ Top 20 (ต้นทุน/หัวรายเดือน · เรียงตาม impact)</h3>
<table>
<thead><tr><th>เมนู</th><th>หมวด</th><th class="num">เริ่ม</th>{th_months()}<th class="num">ชิ้น (ล่าสุด)</th><th>สถานะ</th></tr></thead>
<tbody>{new_rows}</tbody>
</table>
<div class="note avoid-break"><b>ℹ️ ประมาณการชั่วคราว:</b> กลุ่มปลาไหลญี่ปุ่น 5 เมนู (B0324–B0328, ขาย {f0(eel_qty_may)} ชิ้นใน พ.ค.) ใส่ต้นทุนประมาณการ <b>8 บ./ชิ้น</b> เมื่อ 10 มิ.ย. 2026 (แท็ก ESTIMATE ในระบบ) — คิดเป็น +{f2(eel_head_may)} บ./หัว ที่รวมในรายงานฉบับนี้แล้ว · ความไวของสมมติฐาน: ทุก ±1 บ./ชิ้น ≈ ±0.51 บ./หัว ดังนั้นถ้า BoM จริงเป็น 12 บ./ชิ้น ต้นทุน พ.ค. จะเพิ่มอีก ~2 บ./หัว · <b>กลุ่มเบคอนพัน (~2,000–3,000 ชิ้น/เดือน) ยัง COGS = 0</b> — ใส่ BoM จริงทั้งสองกลุ่มก่อนปิดงวด มิ.ย.</div>
<h3>Cannibalization — เมนูใหม่ไปกดการกินเมนูเดิมตัวไหน (ชิ้น/100 หัว · 21 กลุ่ม จัดเป็น 5 คู่เปรียบเทียบ)</h3>
<table class="avoid-break">
<thead><tr><th>กลุ่มเมนู</th>{''.join(f'<th class="num">{SH[m]}</th>' for m in MONTHS)}<th class="num">Δ บ./หัว (พ.ค.−ธ.ค.)</th></tr></thead>
<tbody>{cann_rows}</tbody>
</table>
<ul class="small">
  <li><b>แมงกะพรุน ↔ ยำไทยซีฟู้ด:</b> แมงกะพรุน 3 เมนูเข้าม เม.ย. → การกินยำไทยซีฟู้ดร่วงต่อเนื่อง — สุทธิหมวดยำยังแพงขึ้น แต่การสลับนี้ช่วยให้ลูกค้ารู้สึก "มีของใหม่" ด้วยต้นทุนวัตถุดิบต่ำกว่ากุ้ง/หมึก</li>
  <li><b>หอยเชลล์ ↔ แซลมอนซาชิมิ:</b> การกินแซลมอน/หัวลดลงชัดหลังหอยเชลล์เข้า (เม.ย.–พ.ค.) — cannibalization ที่ "ดี" เพราะย้ายการกินจากเมนู 63 บ./หัว ไปเมนูที่ถูกควบคุมรอบเสิร์ฟได้</li>
  <li><b>โรล/ข้าวห่อสาหร่าย + ขนมครก + ซูชิ:</b> ร่วงพร้อมกันทั้งกลุ่ม (รวม −15 บ./หัว) โดยไม่มีเมนูใหม่แทนในหมวด — ลูกค้าเทโควต้ากระเพาะให้โปรตีนพรีเมียม นี่คือต้นทุนแฝงของการใส่ของแพง: ของถูกที่เคย "ถ่วงค่าเฉลี่ย" ถูกกินน้อยลง</li>
  <li><b>ปลาไหลใหม่ {f1(sum(qph('2026-05', s) for s in eel_sids))} ชิ้น/100หัว ตั้งแต่เดือนแรก</b> — ด้วยประมาณการ 8 บ./ชิ้น คิดเป็น +{f2(eel_head_may)} บ./หัว และเป็น mover อันดับต้นของ พ.ค. ทันที · เบคอนพัน (~10 ชิ้น/100หัว) ยัง COGS = 0 — ใส่ BoM แล้วจะโผล่ตามมาอีกระลอกใน มิ.ย.</li>
</ul>
<h3>ความเหมาะสมของการใส่เมนูใหม่ — ประเมินรายกลุ่ม</h3>
<table class="avoid-break">
<thead><tr><th>กลุ่มเมนูใหม่</th><th>เข้าเมื่อ</th><th class="num">ต้นทุนเพิ่ม บ./หัว</th><th>ผลต่อรายได้/ภาพลักษณ์</th><th>คำตัดสิน</th></tr></thead>
<tbody>
<tr><td><b>หอยเชลล์ฮอกไกโด (ต้ม+สเต็ก)</b></td><td>ก.พ.</td><td class="num">+33.8 (พ.ค.)</td><td>รายได้/หัวเพิ่ม +54 บ. ช่วง มี.ค.→พ.ค. · cost ratio กลับลดลง {f1(ratio['2026-03'])}→{f1(ratio['2026-05'])}% · เป็นแม่เหล็กการตลาดชัดเจน</td><td><b class="ok">คงไว้ + คุมรอบเสิร์ฟ</b> — ถ้าปล่อยอิสระต้นทุนวิ่งถึง +37 บ./หัว (มี.ค.)</td></tr>
<tr><td><b>แมงกะพรุน 3 เมนู</b></td><td>เม.ย.</td><td class="num">+9.0 (พ.ค.)</td><td>เพิ่มความหลากหลายหมวดยำด้วยวัตถุดิบถูก ทดแทนยำซีฟู้ดเดิม</td><td><b class="ok">ตัวอย่างที่ดี</b> ของเมนูใหม่ต้นทุนคุมได้</td></tr>
<tr><td><b>คาโมะ/โอโทโร่/คารูบิ</b></td><td>ก.พ.–เม.ย.</td><td class="num">+1–2</td><td>เพิ่มความรู้สึกพรีเมียมด้วย impact ต่ำ บางตัวถูกถอดออกหลัง 1–2 เดือน (คารูบิหายไปหลัง เม.ย.)</td><td><b class="ok">ใส่ได้</b> · ใช้เป็นเมนูหมุนเวียน</td></tr>
<tr><td><b>ปลาไหลญี่ปุ่น 5 เมนู</b></td><td>มี.ค.</td><td class="num">+{f2(eel_head_may)} (พ.ค. · ประมาณการ 8 บ./ชิ้น)</td><td>อุนางิเป็นวัตถุดิบแพงและเป็นจุดขายญี่ปุ่นแท้ — ขยายเร็วมาก มี.ค. 7,339 → พ.ค. {f0(eel_qty_may)} ชิ้น ({f1(sum(qph('2026-05', s) for s in eel_sids))} ชิ้น/100หัว)</td><td><b class="warn">รอ BoM จริงยืนยัน</b> — เปิดขายโดยไม่มี BoM (ละเมิด stage-gate) รีวิวด้วยต้นทุนจริง มิ.ย.</td></tr>
<tr><td><b>เบคอนพัน 6–7 เมนู</b></td><td>ก.พ.</td><td class="num warn">ไม่ทราบ (COGS=0)</td><td>กินดี ต้นทุนน่าจะต่ำ (เบคอน+ผัก) — น่าจะเป็น Star ของจริง</td><td><b class="warn">ใส่ BoM เพื่อยืนยัน</b> ก่อนเลื่อนสถานะ</td></tr>
</tbody></table>

<div class="page-break"></div>
<h2 id="s10">10) Pareto และดัชนีความเข้มข้นต้นทุน (พ.ค. 2026)</h2>
<div class="kpis" style="grid-template-columns:repeat(4,1fr)">
  <div class="kpi"><div class="l">เมนูกิน 50% ของต้นทุน</div><div class="v">{n50_may} เมนู</div><div class="l">จาก {len(sku['2026-05'])} เมนู</div></div>
  <div class="kpi"><div class="l">เมนูกิน 80% ของต้นทุน</div><div class="v">{n80_may} เมนู</div><div class="l">ธ.ค.25 = {n80_dec} เมนู — กระจุกตัวขึ้นเล็กน้อย</div></div>
  <div class="kpi"><div class="l">Top 2 เมนู</div><div class="v">25.1%</div><div class="l">แซลมอนซาชิมิ + หอยเชลล์ต้ม</div></div>
  <div class="kpi"><div class="l">Top 12 เมนู (ส่วนที่ 8)</div><div class="v">{f1(sum((sku['2026-05'][s]['cogs'] or 0) for s in DEEP)/tot_cogs['2026-05']*100)}%</div><div class="l">ของต้นทุนทั้งหมด</div></div>
</div>
<h3>Top 60 เมนูตามต้นทุนรวม พ.ค. 2026 (สะสม %)</h3>
<table>
<thead><tr><th>#</th><th>เมนู</th><th>หมวด</th><th class="num">ต้นทุนรวม (บ.)</th><th class="num">บ./หัว</th><th class="num">%</th><th class="num">สะสม</th><th style="width:110px">สัดส่วน</th></tr></thead>
<tbody>{p_rows}</tbody>
</table>
<h3>ดัชนีความเข้มข้นต้นทุน — % ต้นทุน ÷ % จำนวนชิ้น (Top 25)</h3>
<p class="small mut">ดัชนี > 3× = เมนู "กินงบ" — ทุกชิ้นที่ลูกค้าหยิบกินต้นทุนแรงกว่าค่าเฉลี่ยมาก ควรเป็นเป้าหมายการคุม portion/รอบเสิร์ฟก่อน · ดัชนีต่ำ = เมนู filler ที่ช่วยถ่วงค่าเฉลี่ย</p>
<table>
<thead><tr><th>#</th><th>เมนู</th><th class="num">% ต้นทุน</th><th class="num">% ชิ้น</th><th class="num">ดัชนี</th><th class="num">ต้นทุน/ชิ้น (บ.)</th></tr></thead>
<tbody>{ci_rows}</tbody>
</table>

<div class="page-break"></div>
<h2 id="s11">11) แผนปรับลดต้นทุนอย่างเป็นรูปธรรม + Roadmap 90 วัน</h2>
<table>
<thead><tr><th>#</th><th>มาตรการ</th><th>วิธีปฏิบัติ</th><th class="num">เป้า บ./หัว</th><th class="num">มูลค่า/เดือน*</th><th>เจ้าของ</th></tr></thead>
<tbody>
<tr><td>1</td><td><b>คุมสเปคแซลมอนซาชิมิ</b><br><span class="small mut">63.47 บ./หัว · 17.4% ของต้นทุน</span></td><td>กำหนดน้ำหนักชิ้น 12–13 g + ชั่งสุ่ม 5 ชิ้น/กะ บันทึกผล · ถาดเล็กลงเติมถี่ขึ้นช่วง peak · off-cut เข้าเมนูยำ/โรลทั้งหมด</td><td class="num">−3.2 (−5%)</td><td class="num">~130,000</td><td>ครัวกลาง + ผจก.สาขา</td></tr>
<tr><td>2</td><td><b>หอยเชลล์ฮอกไกโด → เสิร์ฟแบบควบคุม</b><br><span class="small mut">33.8 บ./หัว (กลุ่มต้ม+สเต็ก)</span></td><td>เปลี่ยนจากวางไลน์อิสระเป็นเสิร์ฟรอบเวลา (2–3 รอบ/วัน) หรือจานแรกต่อโต๊ะ+สั่งเพิ่มได้ · วัด waste ก่อน/หลัง 2 สัปดาห์</td><td class="num">−8.0</td><td class="num">~328,000</td><td>ฝ่ายปฏิบัติการ</td></tr>
<tr><td>3</td><td><b>เจรจารวมซื้อกุ้งสด+กุ้งดอง</b><br><span class="small mut">24.5 บ./หัว รวม 2 เมนู</span></td><td>สัญญาราคารายไตรมาสรวม 5 สาขา · ล็อกไซส์ตัว/กก. · supplier สำรอง 1 ราย</td><td class="num">−1.2 (−5%)</td><td class="num">~50,000</td><td>จัดซื้อกลาง</td></tr>
<tr><td>4</td><td><b>Cross-utilization แซลมอน</b><br><span class="small mut">กลุ่มยำแซลมอน 18.6 บ./หัว</span></td><td>บังคับสูตรยำใช้ trim จากไลน์ซาชิมิ ≥ 60% · รายงาน yield ปลาต่อตัวรายสัปดาห์</td><td class="num">−2.8 (−15%)</td><td class="num">~115,000</td><td>ครัวกลาง</td></tr>
<tr><td>5</td><td><b>ปิดช่องว่างสาขา (เชียงใหม่/อุดรฯ)</b><br><span class="small mut">สูงกว่าเครือ +31.5 / +12.6 บ./หัว</span></td><td>Waste audit รายสัปดาห์เทียบ checklist สุรินทร์ (benchmark) · ตรวจ portion 10 เมนู Top · ทบทวนการเติมไลน์ช่วงท้ายวัน</td><td class="num">−6–8 (เฉพาะ 2 สาขา)</td><td class="num">~180,000</td><td>ผอ.เขต</td></tr>
<tr><td>6</td><td><b>แทนประมาณการปลาไหล (8 บ./ชิ้น) ด้วย BoM จริง + ใส่ BoM เบคอนพัน 7 เมนู</b></td><td>ฝ่ายข้อมูล+ครัวกลางทำ BoM จริงภายใน 2 สัปดาห์ · เพิ่มกฎ "ห้ามเปิดขายถ้าไม่มี BoM" ใน SOP เมนูใหม่ · ความไว: ปลาไหล ±1 บ./ชิ้น ≈ ±0.5 บ./หัว</td><td class="num">ความถูกต้อง</td><td class="num">—</td><td>ฝ่ายข้อมูล</td></tr>
</tbody>
<tfoot><tr><td></td><td>รวมศักยภาพ (1–5)</td><td></td><td class="num">≈ −15 บ./หัว</td><td class="num">~620,000 บ./เดือน ≈ 7.4 ลบ./ปี</td><td></td></tr></tfoot>
</table>
<p class="small mut">* ฐานลูกค้า พ.ค. 2026 = {f0(covers5)} หัว/เดือน (5 สาขา) · −15 บ./หัว ดึงต้นทุนกลับสู่ ~{f0(tot_head['2026-05']-15)} บ. โดยไม่ต้องถอดเมนูพรีเมียมออก</p>
<h3>Sensitivity — ถ้าทำได้บางส่วน</h3>
<table class="avoid-break">
<thead><tr><th>สถานการณ์</th><th class="num">Δ บ./หัว</th><th class="num">มูลค่า/เดือน</th><th class="num">ต้นทุน/หัวใหม่</th><th class="num">Cost ratio ใหม่**</th></tr></thead>
<tbody>
<tr><td>ทำได้ 50% ของแผน</td><td class="num">−7.5</td><td class="num">~310,000</td><td class="num">{f1(tot_head['2026-05']-7.5)}</td><td class="num">{f1((tot_cogs['2026-05']-7.5*covers5)/REV['2026-05']*100)}%</td></tr>
<tr><td>ทำได้เต็มแผน (ข้อ 1–5)</td><td class="num">−15</td><td class="num">~620,000</td><td class="num">{f1(tot_head['2026-05']-15)}</td><td class="num">{f1((tot_cogs['2026-05']-15*covers5)/REV['2026-05']*100)}%</td></tr>
<tr><td>เต็มแผน แต่ BoM ปลาไหลจริงแพงกว่าประมาณการ (12 บ./ชิ้น → ต้นทุนเพิ่มอีก ~2 บ./หัว)</td><td class="num">−13 สุทธิ</td><td class="num">~530,000</td><td class="num">{f1(tot_head['2026-05']-13)}</td><td class="num">{f1((tot_cogs['2026-05']-13*covers5)/REV['2026-05']*100)}%</td></tr>
</tbody></table>
<p class="small mut">** สมมติรายได้ พ.ค. คงที่</p>
<h3>Roadmap 90 วัน</h3>
<table class="avoid-break">
<thead><tr><th>ช่วง</th><th>งาน</th><th>ตัวชี้วัดความสำเร็จ</th></tr></thead>
<tbody>
<tr><td><b>วันที่ 1–14</b></td><td>แทนประมาณการปลาไหลด้วย BoM จริง + ใส่ BoM เบคอนพัน (ข้อ 6) · เริ่มชั่งสุ่มแซลมอน (ข้อ 1) · กำหนดรูปแบบเสิร์ฟหอยเชลล์ใหม่ (ข้อ 2) นำร่อง 1 สาขา (แนะนำโคราช1)</td><td>BoM จริงครบ 100% · baseline waste หอยเชลล์</td></tr>
<tr><td><b>วันที่ 15–45</b></td><td>ขยายรูปแบบเสิร์ฟหอยเชลล์ครบ 5 สาขา · waste audit เชียงใหม่/อุดรฯ รอบแรก (ข้อ 5) · เปิดเจรจาสัญญากุ้ง (ข้อ 3)</td><td>ต้นทุน/หัว มิ.ย. ≤ 365 บ. · ช่องว่างเชียงใหม่ vs เครือ < +25 บ.</td></tr>
<tr><td><b>วันที่ 46–90</b></td><td>สูตรยำใช้ trim บังคับ (ข้อ 4) · ติดตั้ง weekly control chart อัตโนมัติ + menu engineering review ไตรมาส (ส่วนที่ 12) · รีวิวเมนูปลาไหลด้วยต้นทุนจริง</td><td>ต้นทุน/หัว ก.ค.–ส.ค. 352–356 บ. · cost ratio ≤ 49%</td></tr>
</tbody></table>

<div class="page-break"></div>
<h2 id="s12">12) Playbook บริหารเครือบุฟเฟ่ต์อาหารญี่ปุ่นระดับโลก</h2>
<h3>12.1 แข่งที่ spread ต่อหัว ไม่ใช่ยอดขายรวม</h3>
<p>เครือบุฟเฟ่ต์ชั้นนำของญี่ปุ่น (สไตล์เดียวกับกลุ่มยากินิคุ/ชาบูรายใหญ่) วัดสุขภาพธุรกิจที่ <b>spread = รายได้/หัว − ต้นทุนวัตถุดิบ/หัว</b> และบริหารทั้งสองขาพร้อมกัน — ของเรา ธ.ค. {f1(rev_head[BASE])}−{f2(tot_head[BASE])} = <b>{f1(spread[BASE])} บ./หัว</b> → พ.ค. {f1(rev_head['2026-05'])}−{f2(tot_head['2026-05'])} = <b>{f1(spread['2026-05'])} บ./หัว (+{f1(dpct(spread['2026-05'],spread[BASE]))}%)</b> ใน 5 เดือน ถือว่าแข็งแรงมาก บทเรียนคือ<b>การเพิ่มต้นทุนไม่ใช่ปัญหาเสมอไป ถ้าซื้อรายได้/หัวได้มากกว่า</b> — ปัญหาคือการเพิ่มแบบไม่มีเพดาน (มี.ค.) จึงควรประกาศ <b>เพดาน cost ratio ต่อเดือนที่ 52%</b> และเพดานเมนูใหม่ "+10 บ./หัว ต่อการเปิดตัว 1 ชุด" เป็นกติกาถาวร</p>
<h3>12.2 แยกสัญญาณ "จัดซื้อ" ออกจาก "หน้าร้าน" ทุกเดือน</h3>
<p class="small">จากตาราง Bridge (ส่วนที่ 5): การเปลี่ยนแปลงเกือบทั้งหมดของช่วงนี้มาจาก<b>พฤติกรรมการกิน + เมนูใหม่</b> ไม่ใช่ราคาวัตถุดิบ (ผลด้านราคา/ชิ้นรวมแล้วเล็กมากทุกเดือน) — แปลว่าคันโยกอยู่ที่<b>การออกแบบไลน์บุฟเฟ่ต์</b> (ตำแหน่ง ขนาดถาด รอบเติม การจัดชุด) มากกว่าโต๊ะเจรจา supplier · เดือนไหนที่ Bridge ฝั่ง "ต้นทุน/ชิ้น" เริ่มหนา นั่นคือสัญญาณให้จัดซื้อขยับ</p>
<h3>12.3 ระบบที่ควรติดตั้งถาวร (สรุปจากบทวิเคราะห์ทั้งฉบับ)</h3>
<ol>
  <li><b>Weekly cost-per-head control chart รายสาขา (±2SD)</b> — ใช้ได้ทันที (ต้นแบบในส่วนที่ 7) สาขาหลุดกรอบ 2 สัปดาห์ติด → trigger waste audit อัตโนมัติ</li>
  <li><b>Menu engineering matrix รายไตรมาส</b> — Popularity (ชิ้น/100หัว) × Cost intensity (ส่วนที่ 10): Plowhorse (นิยม+แพง: ซาชิมิ, หอยเชลล์, กุ้ง) คุม portion · Star (นิยม+ถูก: เบคอนพัน — รอ BoM ยืนยัน) ดันให้เด่นเพื่อถ่วงค่าเฉลี่ย · Dog (ไม่นิยม+แพง) ถอดออก — {len(sku['2026-05'])} เมนูแต่ {n80_may} ตัวกิน 80% ของงบ ที่เหลือบริหารแบบเบา</li>
  <li><b>Premium gating</b> — ของ >5 บ./หัว เสิร์ฟแบบควบคุม (รอบเวลา/จานแรก+สั่งเพิ่ม/ทำสดต่อออเดอร์) ซึ่งลูกค้ารับรู้เป็น "สดกว่า" ไม่ใช่ "ถูกหวง" — เคสหอยเชลล์ เม.ย.–พ.ค. พิสูจน์แล้วว่าลดได้ ~3 บ./หัวโดย mix รวมไม่เสีย</li>
  <li><b>New-menu stage-gate</b> — (1) BoM ครบก่อนขาย (2) ประมาณการ +บ./หัว และเพดาน (3) ระบุเมนูที่คาดว่าจะถูก cannibalize (4) รีวิว 30 วันด้วยข้อมูลจริง — เคสปลาไหลคือบทเรียนข้อ (1) ที่แพงที่สุดของช่วงนี้</li>
  <li><b>Supplier risk ledger</b> — วัตถุดิบ >10 บ./หัว (แซลมอน หอยเชลล์ กุ้ง ปลาไหล) ต้องมีราคาอ้างอิงตลาด + supplier สำรอง ทบทวนรายเดือน — แซลมอนตัวเดียวคือ 17% ของต้นทุน ความเสี่ยงราคา/โรคปลา/ค่าเงินเยน-โครนกระทบ P&L ตรง</li>
  <li><b>แยกต้นทุน/หัวตาม tier ราคา</b> — รายงานนี้เป็นค่าเฉลี่ยรวมทุก tier ขั้นถัดไปคือเชื่อมตาราง buffet tier เพื่อหา tier ที่ margin บางและออกแบบเมนูในแพ็กเกจใหม่ (เช่น ล็อกซาชิมิพรีเมียมไว้ tier บนเท่านั้น)</li>
  <li><b>วางแผนตามฤดูกาล</b> — มี.ค. volume ต่ำสุด+ต้นทุน/หัวสูงสุดไม่ใช่เรื่องบังเอิญ: เดือน volume ต่ำควรลดความถี่เมนูพรีเมียมหมุนเวียนลง และอัดโปรเครื่องดื่ม/ของหวานที่ margin สูงแทน</li>
</ol>

<h2 id="s13">13) ความเสี่ยงและคุณภาพข้อมูล</h2>
<table class="avoid-break">
<thead><tr><th>ประเด็น</th><th>ผลกระทบ</th><th>การจัดการ</th></tr></thead>
<tbody>
<tr><td><b>ปลาไหล 5 เมนูใช้ประมาณการ 8 บ./ชิ้น</b> (ใส่ 10 มิ.ย. 2026, แท็ก ESTIMATE · {f0(eel_qty_may)} ชิ้น/พ.ค. = +{f2(eel_head_may)} บ./หัว)</td><td>ตัวเลข พ.ค. แม่นเท่าสมมติฐาน: ±1 บ./ชิ้น ≈ ±0.5 บ./หัว</td><td>แทนด้วย BoM จริงภายใน 2 สัปดาห์ + ห้ามเปิดขายเมนูไม่มี BoM (SOP ใหม่)</td></tr>
<tr><td><b>เบคอนพัน 7 เมนู COGS = 0</b> (~2,000–3,000 ชิ้น/เดือน ตั้งแต่ ก.พ.)</td><td>ต้นทุน ก.พ.–พ.ค. ต่ำเกินจริงเล็กน้อย (วัตถุดิบถูก คาด <1.5 บ./หัว)</td><td>ใส่ BoM พร้อมรอบปลาไหล</td></tr>
<tr><td><b>สเต็กหอยเชลล์/หอยเชลล์ต้มมีร่องรอยขายเล็กน้อยใน ธ.ค.</b> (0.02–0.08 บ./หัว)</td><td>ฐาน "เมนูใหม่" ไม่บริสุทธิ์ 100% (น่าจะเป็นการทดลองขาย/คีย์ทดสอบ)</td><td>ไม่กระทบสาระ — มูลค่า ธ.ค. ≈ 0</td></tr>
<tr><td><b>ต้นทุน/ชิ้นจาก BoM เป็นราคามาตรฐาน</b> ไม่ใช่ราคาซื้อจริงรายเดือน</td><td>"ผลด้านราคา" ใน Bridge ต่ำกว่าจริงถ้าราคาตลาดขยับแต่ BoM ไม่อัปเดต</td><td>ทบทวนราคา BoM วัตถุดิบ Top 20 ทุกไตรมาส</td></tr>
<tr><td><b>Cost ratio ใช้รายได้รวมเป็นตัวหาร</b> (รวมเครื่องดื่ม/อื่น ๆ)</td><td>ต่ำกว่า food cost % ตามบัญชีเล็กน้อย</td><td>ใช้เปรียบเทียบแนวโน้มภายในเท่านั้น</td></tr>
<tr><td><b>ข้อมูลรายวัน (weekly chart) เริ่ม 9 ก.พ. 2026</b></td><td>control chart ไม่ครอบคลุม ธ.ค.–ม.ค.</td><td>ครบถ้วนตั้งแต่ มิ.ย. เป็นต้นไป</td></tr>
<tr><td><b>โคราช2 ถูกตัดออกทุกช่วง</b> ตามขอบเขตรายงาน</td><td>ตัวเลขเครือนี้ไม่ตรงกับรายงานที่รวมโคราช2</td><td>ระบุขอบเขตชัดในทุกหน้า</td></tr>
</tbody></table>

<div class="page-break"></div>
<h2 id="appA">ภาคผนวก ก. — เมนูบุฟเฟ่ต์ทั้งหมด (ต้นทุน/หัว บ. รายเดือน, เรียงตามต้นทุนสูงสุด)</h2>
<table>
<thead><tr><th>#</th><th>เมนู</th><th>หมวด</th><th class="num">ธ.ค.25</th>{th_months()}<th class="num">Δ พ.ค.−ธ.ค.</th></tr></thead>
<tbody>{app_rows}</tbody>
</table>

<h2 id="appB">ภาคผนวก ข. — นิยามศัพท์</h2>
<table class="avoid-break"><tbody>
<tr><td style="width:220px"><b>ต้นทุน/หัว (cost per head)</b></td><td>COGS (BoM) รวมของช่วง ÷ จำนวนลูกค้า (covers) ของช่วงเดียวกัน — ตัวเลขหลักของทั้งรายงาน</td></tr>
<tr><td><b>ชิ้น/100 หัว</b></td><td>จำนวนชิ้นขาย ÷ ลูกค้า × 100 — วัด "ความนิยม" ที่ปรับฐานลูกค้าแล้ว เทียบข้ามเดือน/สาขาได้ตรง</td></tr>
<tr><td><b>ต้นทุน/ชิ้น</b></td><td>COGS ÷ จำนวนชิ้น — สะท้อนราคา/สูตรวัตถุดิบ (จาก BoM มาตรฐาน)</td></tr>
<tr><td><b>Spread/หัว</b></td><td>รายได้ ex-VAT/หัว − ต้นทุนบุฟเฟ่ต์/หัว — กำไรขั้นต้นเชิงวัตถุดิบต่อลูกค้า 1 คน</td></tr>
<tr><td><b>Cost ratio</b></td><td>COGS เมนูบุฟเฟ่ต์ ÷ รายได้รวม ex-VAT ของ 5 สาขา</td></tr>
<tr><td><b>Bridge</b></td><td>การแยก Δต้นทุน/หัว เป็น 4 ก้อน: เมนูใหม่ · เมนูที่ถอด · พฤติกรรมการกินของเมนูเดิม · ต้นทุน/ชิ้นของเมนูเดิม</td></tr>
<tr><td><b>Cannibalization</b></td><td>เมนูใหม่ดึงการกินจากเมนูเดิม — วัดจาก ชิ้น/100หัว ของเมนูเดิมที่ลดลงหลังเมนูใหม่เข้า</td></tr>
<tr><td><b>ดัชนีความเข้มข้นต้นทุน</b></td><td>% ส่วนแบ่งต้นทุน ÷ % ส่วนแบ่งจำนวนชิ้น — สูง = เมนูกินงบแรงต่อชิ้นที่ถูกหยิบ</td></tr>
<tr><td><b>±2SD control band</b></td><td>กรอบค่าเฉลี่ย ± 2 ส่วนเบี่ยงเบนมาตรฐานของต้นทุน/หัวรายสัปดาห์ — หลุดกรอบ = ผิดปกติเชิงสถิติ ควรหาสาเหตุ</td></tr>
</tbody></table>
<p class="small mut">จัดทำโดยระบบ Oshinei Dashboard · สคริปต์ backend/gen_menu_ab_deep_report.py · ข้อมูล ณ 10 มิ.ย. 2026</p>

</div></body></html>
"""

out = HERE.parent / 'docs' / 'MENU_COST_AB_DEEP_2026-01-05.html'
out.write_text(html_out, encoding='utf-8')
print('wrote', out)
print('chars:', len(html_out), '| appendix rows:', app_rows.count('<tr>'),
      '| new rows:', new_rows.count('<tr>'), '| weeks:', len(WEEKS))
