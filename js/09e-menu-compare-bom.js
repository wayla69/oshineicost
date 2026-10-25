// ─── 09e-menu-compare-bom.js — Menu compare A/B · service-charge-as-revenue toggle · BoM cost analysis (overview / per-branch / compare / simulator) · template import/export · adopted-scenario ROI ledger ───
// Slice 5/5 of the former 09-deep-tools.js (split 2026-09-05 by contiguous range, so every
// statement keeps its load order). Shared global scope: index.html loads js/*.js in name order.

// ════════════════════════════════════════════════════════════════
// MENU COMPARE A/B — Top menus across two arbitrary periods + dive-in
// ════════════════════════════════════════════════════════════════
let _mcLast = null, _mcDrillData = null, _mcBomSku = null;
function _mcDefaults() {
  const d = new Date(), ym = (y, m) => `${y}-${String(m).padStart(2, '0')}`;
  const cur = ym(d.getFullYear(), d.getMonth() + 1);
  const pm = d.getMonth() === 0 ? ym(d.getFullYear() - 1, 12) : ym(d.getFullYear(), d.getMonth());
  return { cur, pm };
}
function mcToggleMode(P) {
  const mode = document.getElementById('mc' + P + 'mode').value;
  document.getElementById('mc' + P + 'month').classList.toggle('hidden', mode !== 'month');
  const dt = document.getElementById('mc' + P + 'date');
  dt.classList.toggle('hidden', mode !== 'date'); dt.classList.toggle('flex', mode === 'date');
}
function _mcPeriodParams(P, prefix) {
  const q = {};
  if (document.getElementById('mc' + P + 'mode').value === 'month') {
    const f = document.getElementById('mc' + P + 'mf').value, t = document.getElementById('mc' + P + 'mt').value;
    if (f) q[prefix + '_from'] = f; if (f || t) q[prefix + '_to'] = t || f;
  } else {
    const f = document.getElementById('mc' + P + 'df').value, t = document.getElementById('mc' + P + 'dt').value;
    if (f) q[prefix + '_date_from'] = f; if (t) q[prefix + '_date_to'] = t;
  }
  return q;
}
async function loadMenuCompare() {
  const { cur, pm } = _mcDefaults();
  ['Amf', 'Amt'].forEach(id => { const e = document.getElementById('mc' + id); if (e && !e.value) e.value = pm; });
  ['Bmf', 'Bmt'].forEach(id => { const e = document.getElementById('mc' + id); if (e && !e.value) e.value = cur; });
  const qp = { ..._mcPeriodParams('A', 'a'), ..._mcPeriodParams('B', 'b'), limit: 80 };
  if (getBranch()) qp.branch = getBranch();
  if (getGroup()) qp.group = getGroup();
  const st = document.getElementById('mcStatus'); if (st) st.textContent = 'กำลังโหลด…';
  let d; try { d = await api(`${API}/api/sales/menu-compare?${new URLSearchParams(qp)}`); }
  catch (e) { if (st) st.textContent = 'ผิดพลาด'; return; }
  _mcLast = { items: d.items || [], totals: d.totals || {}, covers_a: d.covers_a || 0,
              covers_b: d.covers_b || 0, a_range: d.a_range, b_range: d.b_range, qp };
  if (st) st.textContent = `A: ${(d.a_range || []).filter(Boolean).join(' – ') || '—'}   ·   B: ${(d.b_range || []).filter(Boolean).join(' – ') || '—'}`
    + (d.to_date ? `   ·   ช่วงที่ยังไม่จบ เทียบ ${d.n_days} วันแรกเท่ากัน` : '');   // PR-PP2 guard
  mcRenderTable();
  mcRenderPareto();
}

// Total ↔ per-head (ต่อหัว) cost view. Per-head = COGS ÷ covers, so a drop in
// total cost that's only caused by fewer customers stops looking like a "win".
let _mcPerHead = false;
function mcSetCostMode(perHead) {
  _mcPerHead = perHead;
  document.getElementById('mcModeTotal')?.classList.toggle('gran-active', !perHead);
  document.getElementById('mcModeHead')?.classList.toggle('gran-active', perHead);
  mcRenderTable();
  // Keep the dive-in in sync: the main toggle also drives the per-branch view.
  _mcDrillPerHead = perHead;
  const dm = document.getElementById('mcDrillModal');
  if (dm && !dm.classList.contains('hidden') && _mcDrillData) mcRenderBranch();
}
// ── Menu-compare table state ────────────────────────────────────────────────
// Table 1 follows the total↔per-head toggle; Table 2 is always per-head (added
// alongside, not replacing). Both share one category filter + one sort order so
// a given row sits at the same position in both — easy side-by-side reading.
const MC_NOISE_QTY = 50;                       // Δ% on volumes below this = noise → dimmed
// Below this min/max ratio, one side is a near-zero residual next to the other (e.g. a
// retired SKU code posting a handful of leftover transactions after a menu-cutover rename)
// — dividing by it produces a nonsensical, direction-flipping delta. Mirrors the backend's
// _CMP_RESIDUAL_RATIO guard in routers/sales.py so the table shows the same "—" it would get
// from the API instead of recomputing the raw (and here, un-guarded) ratio itself.
const MC_RESIDUAL_RATIO = 0.01;
let _mcCatSel = '';                            // category filter ('' = all)
let _mcSort = { key: 'vol', dir: -1 };         // sort key + direction (-1 desc, 1 asc)
function _cmpPctSafe(a, b) {                   // Δ% of a vs b, guarding the residual-ratio blowup
  if (!b) return null;
  if (a && Math.min(Math.abs(a), Math.abs(b)) / Math.max(Math.abs(a), Math.abs(b)) < MC_RESIDUAL_RATIO) return null;
  return (a - b) / b * 100;
}
const _dpRaw  = (a, b) => _cmpPctSafe(a, b);                            // raw Δ% A vs B
function _mcTotals(items) {
  const s = k => items.reduce((a, r) => a + (r[k] || 0), 0);
  return { qty_a: s('qty_a'), qty_b: s('qty_b'), cogs_a: s('cogs_a'), cogs_b: s('cogs_b') };
}
function mcSetCat(v) { _mcCatSel = v; mcRenderTable(); }
function mcSort(key) {
  if (_mcSort.key === key) _mcSort.dir *= -1; else _mcSort = { key, dir: -1 };
  mcRenderTable();
}
function _mcApplySort(items) {
  const k = _mcSort.key, d = _mcSort.dir;
  const val = r => ({ vol: r._vol, qty_a: r.qty_a, qty_b: r.qty_b, qd: r._qd, mix: r._mix_b,
    cogs_a: r.cogs_a, cogs_b: r.cogs_b, cd: r._cd }[k]);
  items.sort((a, b) => { const x = val(a), y = val(b);
    if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
    return (x - y) * d; });
}
function _mcRenderCats(allItems) {
  const sel = document.getElementById('mcCatFilter'); if (!sel) return;
  const cats = [...new Set(allItems.map(r => r.category).filter(Boolean))].sort();
  if (_mcCatSel && !cats.includes(_mcCatSel)) _mcCatSel = '';   // stale filter from a prior period
  const want = `<option value="">ทั้งหมด</option>` + cats.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
  if (sel.dataset.sig !== want) { sel.dataset.sig = want; sel.innerHTML = want; }
  sel.value = _mcCatSel;
}
function _mcRenderInsights(items) {
  const el = document.getElementById('mcInsights'); if (!el) return;
  const nm = r => esc((r.description || '').replace(/^\((?:A|B|T)\)\s*/, '')).slice(0, 26);
  const movers = items.filter(r => !r.only_in_a && !r.only_in_b && !r._noise && r._qd != null);
  const up   = movers.filter(r => r._qd > 0).sort((a, b) => b._qd - a._qd).slice(0, 3);
  const down = movers.filter(r => r._qd < 0).sort((a, b) => a._qd - b._qd).slice(0, 3);
  const neu  = items.filter(r => r.only_in_b).sort((a, b) => b.qty_b - a.qty_b).slice(0, 3);
  const gone = items.filter(r => r.only_in_a).sort((a, b) => b.qty_a - a.qty_a).slice(0, 3);
  const card = (title, color, rows, fmt) =>
    `<div class="card p-3"><div class="text-xs font-bold mb-1.5" style="color:${textInk(color)}">${title}</div>` +
    (rows.length ? rows.map(fmt).join('') : '<div class="text-[11px] text-slate-400">—</div>') + '</div>';
  el.innerHTML =
    card('มาแรง', '#16a34a', up, r => `<div class="text-[11px] truncate" title="${esc(r.description || '')}">${nm(r)} <span style="color:var(--good-ink);font-weight:600">▲${Math.abs(r._qd).toFixed(0)}%</span></div>`) +
    card('ร่วงแรง', '#dc2626', down, r => `<div class="text-[11px] truncate" title="${esc(r.description || '')}">${nm(r)} <span style="color:var(--bad-ink);font-weight:600">▼${Math.abs(r._qd).toFixed(0)}%</span></div>`) +
    card('เมนูใหม่ <span class="font-normal text-slate-400">(มีใน B)</span>', '#4a70b0', neu, r => `<div class="text-[11px] truncate" title="${esc(r.description || '')}">${nm(r)} <span class="text-slate-400">${fmtN(r.qty_b)}</span></div>`) +
    card('หายไป <span class="font-normal text-slate-400">(เหลือใน A)</span>', '#d97706', gone, r => `<div class="text-[11px] truncate" title="${esc(r.description || '')}">${nm(r)} <span class="text-slate-400">${fmtN(r.qty_a)}</span></div>`);
}
function mcRenderTable() {
  const L = _mcLast; if (!L) return;
  const Ca = L.covers_a || 0, Cb = L.covers_b || 0;
  _mcRenderCats(L.items || []);
  let items = (L.items || []).filter(r => !_mcCatSel || r.category === _mcCatSel);
  const T = _mcTotals(items);
  items.forEach(r => {
    r._mix_a = T.qty_a ? r.qty_a / T.qty_a : 0; r._mix_b = T.qty_b ? r.qty_b / T.qty_b : 0;
    r._vol = (r.qty_a || 0) + (r.qty_b || 0);
    r._qd = _dpRaw(r.qty_a, r.qty_b); r._cd = _dpRaw(r.cogs_a, r.cogs_b);
    r._noise = Math.min(r.qty_a || 0, r.qty_b || 0) < MC_NOISE_QTY;  // small base ⇒ Δ% unstable
  });
  _mcApplySort(items);
  _mcRenderInsights(items);
  mcRenderMcInto('mcHead', 'mcBody', 'mcFoot', _mcPerHead, items, T, Ca, Cb);
  mcRenderMcInto('mcHead2', 'mcBody2', 'mcFoot2', true, items, T, Ca, Cb);
  const catNote = _mcCatSel ? ` · กรองหมวด: ${esc(_mcCatSel)}` : '';
  const note = document.getElementById('mcCoversNote');
  if (note) note.textContent = (_mcPerHead
    ? `ลูกค้า A ${fmtN(Ca)} คน · B ${fmtN(Cb)} คน — /หัว = ยอด ÷ ลูกค้า (ตัดผลของยอดขายที่เปลี่ยน)`
    : `แสดง ${fmtN(items.length)} เมนู · สัดส่วน = ส่วนแบ่งใน Top ที่แสดง`) + catNote;
  const note2 = document.getElementById('mcCoversNote2');
  if (note2) note2.textContent = `ลูกค้า A ${fmtN(Ca)} คน · B ${fmtN(Cb)} คน — ทุกคอลัมน์หารด้วยจำนวนลูกค้า (สัดส่วนเป็นอัตราส่วน จึงเท่ากับตารางที่ 1)` + catNote;
}
// ph divides qty/cost by covers for the per-head table; mix-share is a ratio so
// identical in both. Δ% is recomputed from displayed values (per-head deltas
// differ from raw because covers differ between periods).
function mcRenderMcInto(headId, bodyId, footId, ph, items, T, Ca, Cb) {
  const head = document.getElementById(headId), body = document.getElementById(bodyId), foot = document.getElementById(footId);
  if (!head || !body || !foot) return;
  const hq = ph ? ['จำนวน/คน A', 'จำนวน/คน B'] : ['จำนวน A', 'จำนวน B'];
  const hc = ph ? ['ต้นทุน/หัว A', 'ต้นทุน/หัว B'] : ['ต้นทุน A', 'ต้นทุน B'];
  const ind = k => _mcSort.key === k ? (_mcSort.dir < 0 ? ' ▾' : ' ▴') : '';
  const sth = (lbl, k) => `<th class="num" style="cursor:pointer;user-select:none" onclick="mcSort('${k}')" title="คลิกเพื่อเรียง">${lbl}${ind(k)}</th>`;
  head.innerHTML = `<tr><th class="num">#</th><th>SKU</th><th>เมนู</th>
    ${sth(hq[0], 'qty_a')}${sth(hq[1], 'qty_b')}${sth('Δ%<span class="sub">จำนวน</span>', 'qd')}
    ${sth('สัดส่วน<span class="sub">B · Δpp</span>', 'mix')}
    ${sth(hc[0], 'cogs_a')}${sth(hc[1], 'cogs_b')}${sth('Δ%<span class="sub">ต้นทุน</span>', 'cd')}<th></th></tr>`;
  if (!items.length) {
    body.innerHTML = '<tr><td colspan="11" class="py-4 text-center text-slate-400">ไม่มีข้อมูลในช่วง/หมวดที่เลือก</td></tr>';
    foot.innerHTML = ''; return;
  }
  const vD = (x, C) => ph ? (C ? x / C : null) : x;          // divide by covers when per-head
  const nQ = v => v == null ? '—' : (ph ? v.toFixed(2) : fmtN(v));
  const nM = v => v == null ? '—' : (ph ? '฿' + v.toFixed(2) : fmt(v));
  const dp = (a, b) => a != null ? _cmpPctSafe(a, b) : null;
  const badge = r => r.only_in_b
    ? '<span style="font-size:9px;background:var(--indigo-soft);color:var(--indigo);padding:1px 5px;border-radius:var(--r-md);margin-left:5px;font-weight:700">ใหม่</span>'
    : r.only_in_a
      ? '<span style="font-size:9px;background:#fef3c7;color:#a44a08;padding:1px 5px;border-radius:var(--r-md);margin-left:5px;font-weight:700">หาย</span>' : '';
  body.innerHTML = items.map((r, i) => {
    const qa = vD(r.qty_a, Ca), qb = vD(r.qty_b, Cb);
    const ca = vD(r.cogs_a, Ca), cb = vD(r.cogs_b, Cb);
    const dpp = (r._mix_b - r._mix_a) * 100;
    const dimQ = r._noise ? ' style="opacity:.45" title="ปริมาณช่วงใดช่วงหนึ่งน้อย (<' + MC_NOISE_QTY + ' ชิ้น) — % ผันผวนง่าย"' : '';
    return `<tr class="cursor-pointer" onclick="mcOpenDrill('${jsAttr(r.sku_code)}')" title="คลิกดูเชิงลึก">
    <td class="num tabular text-slate-400">${i + 1}</td>
    <td class="font-mono text-slate-400 text-[10px]">${esc(r.sku_code)}</td>
    <td class="max-w-xs truncate" title="${esc(r.description || '')}">${esc((r.description || '').replace(/^\((?:A|B|T)\)\s*/, ''))}${badge(r)}</td>
    <td class="num tabular font-semibold">${nQ(qa)}</td>
    <td class="num tabular font-semibold">${nQ(qb)}</td>
    <td class="num tabular"${dimQ}>${_momSpan(dp(qa, qb), true)}</td>
    <td class="num tabular"><div>${(r._mix_b * 100).toFixed(1)}%</div><div class="text-[10px]" style="color:${dpp >= 0 ? 'var(--good-ink)' : 'var(--bad-ink)'}">${dpp >= 0 ? '+' : ''}${dpp.toFixed(1)}pp</div></td>
    <td class="num tabular text-slate-500">${nM(ca)}</td>
    <td class="num tabular text-slate-500">${nM(cb)}</td>
    <td class="num tabular">${_momSpan(dp(ca, cb), false)}</td>
    <td class="num text-indigo-500 text-[11px] whitespace-nowrap">ดูเชิงลึก ›</td></tr>`;
  }).join('');
  const tqa = vD(T.qty_a, Ca), tqb = vD(T.qty_b, Cb), tca = vD(T.cogs_a, Ca), tcb = vD(T.cogs_b, Cb);
  foot.innerHTML = `<tr><td></td><td></td><td>รวม ${fmtN(items.length)} เมนู</td>
    <td class="num tabular">${nQ(tqa)}</td><td class="num tabular">${nQ(tqb)}</td>
    <td class="num tabular">${_momSpan(dp(tqa, tqb), true)}</td>
    <td class="num tabular">100%</td>
    <td class="num tabular">${nM(tca)}</td><td class="num tabular">${nM(tcb)}</td>
    <td class="num tabular">${_momSpan(dp(tca, tcb), false)}</td><td></td></tr>`;
}
// Cumulative cost-share Pareto: each period sorted by its own COGS desc, plotted
// as cumulative % of that period's total. Concentration is share-based, so it's
// identical whether you read total or per-head cost — hence not tied to the toggle.
let _mcParetoMode = 'concentration';   // 'concentration' (each sorted by own cost) | 'aligned' (shared menu order)
function mcSetParetoMode(mode) {
  _mcParetoMode = mode;
  document.getElementById('mcParetoModeConc')?.classList.toggle('gran-active', mode === 'concentration');
  document.getElementById('mcParetoModeAlign')?.classList.toggle('gran-active', mode === 'aligned');
  mcRenderPareto();
}
function mcRenderPareto() {
  const L = _mcLast, card = document.getElementById('mcParetoCard'); if (!L || !card) return;
  const items = L.items || [];
  if (items.length < 2) { card.style.display = 'none'; return; }
  card.style.display = 'block';
  const totA = items.reduce((s, r) => s + (r.cogs_a || 0), 0) || 1;
  const totB = items.reduce((s, r) => s + (r.cogs_b || 0), 0) || 1;
  const aligned = _mcParetoMode === 'aligned';
  let ca, cb, names;
  if (aligned) {
    // SAME menu order for both periods (by combined cost) → compare item-by-item.
    const ord = [...items].sort((a, b) => ((b.cogs_a || 0) + (b.cogs_b || 0)) - ((a.cogs_a || 0) + (a.cogs_b || 0)));
    let ra = 0, rb = 0; ca = []; cb = []; names = [];
    ord.forEach(r => { ra += (r.cogs_a || 0); rb += (r.cogs_b || 0); ca.push(ra / totA * 100); cb.push(rb / totB * 100);
      names.push((r.description || r.sku_code || '').replace(/^\((?:A|B|T)\)\s*/, '')); });
  } else {
    // concentration: each period sorted by its OWN cost desc.
    const cum = (key, tot) => { const sv = items.map(r => r[key] || 0).filter(v => v > 0).sort((a, b) => b - a);
      let run = 0; return sv.map(v => { run += v; return run / tot * 100; }); };
    ca = cum('cogs_a', totA); cb = cum('cogs_b', totB);
  }
  const n = Math.max(ca.length, cb.length);
  const labels = Array.from({ length: n }, (_, i) => i + 1);
  const rank80 = a => { const i = a.findIndex(v => v >= 80); return i < 0 ? a.length : i + 1; };
  const topN = (a, k) => a.length ? a[Math.min(k, a.length) - 1].toFixed(0) : '—';
  const sum = document.getElementById('mcParetoSummary');
  if (sum) sum.textContent = aligned
    ? `Top 10 เมนู(ชุดเดียวกัน) = A ${topN(ca, 10)}% · B ${topN(cb, 10)}% ของต้นทุน`
    : `80% ของต้นทุน: A = ${rank80(ca)} เมนู · B = ${rank80(cb)} เมนู   |   Top 10 = A ${topN(ca, 10)}% · B ${topN(cb, 10)}%`;
  // PR-MB: the reading of the chart is behind the "?" of the card title; the line under it keeps the legend of the dashed line
  const hint = document.getElementById('mcParetoHint'), q = document.getElementById('mcParetoQ');
  if (hint) hint.textContent = 'เส้นประ = 80%';
  if (q) q.innerHTML = uiQ('mc:pareto', 'Pareto สะสม', aligned
    ? '<p>เรียงเมนูชุดเดียวกันทั้ง A/B (ตามต้นทุนรวม) — อันดับ N = เมนูชุดเดียวกัน ดูว่าเมนูเดิมกินสัดส่วนต้นทุนต่างกันแค่ไหนในแต่ละช่วง · เส้นประ = 80%</p>'
    : '<p>เส้นสะสม % ของต้นทุน เทียบกับจำนวนเมนู (เรียงต้นทุนมาก→น้อยในแต่ละช่วง) · เส้นที่ชันกว่า = ต้นทุนกระจุกในเมนูจำนวนน้อยกว่า · เส้นประ = 80%</p>');
  const aLbl = 'A (' + ((L.a_range || []).filter(Boolean).join('–') || 'A') + ')';
  const bLbl = 'B (' + ((L.b_range || []).filter(Boolean).join('–') || 'B') + ')';
  mkChart('mcParetoChart', {
    type: 'line',
    data: {
      labels, datasets: [
        { label: aLbl, data: ca, borderColor: '#4a70b0', backgroundColor: 'transparent', tension: .2, pointRadius: 0, borderWidth: 2 },
        { label: bLbl, data: cb, borderColor: '#0891b2', backgroundColor: 'transparent', tension: .2, pointRadius: 0, borderWidth: 2 },
        // 80% guideline (hidden from legend + tooltip via filters below)
        { label: '_80', data: labels.map(() => 80), borderColor: 'rgba(148,163,184,0.8)', borderDash: [5, 4], borderWidth: 1, pointRadius: 0, fill: false, tension: 0 },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'top', labels: { boxWidth: 12, font: { size: 11 }, filter: it => it.text !== '_80' } },
        datalabels: { display: false },
        tooltip: {
          filter: it => it.dataset.label !== '_80',
          callbacks: {
            title: items => aligned && names ? `#${items[0].label} ${names[items[0].dataIndex] || ''}` : `${items[0].label} เมนู`,
            label: c => `${c.dataset.label}: ${c.parsed.y.toFixed(1)}% สะสม`,
          }
        },
      },
      scales: {
        x: { title: { display: true, text: aligned ? 'อันดับเมนู (ชุดเดียวกันทั้ง A/B)' : 'จำนวนเมนู (เรียงต้นทุนมาก→น้อย)' }, ticks: { maxTicksLimit: 12 } },
        y: { title: { display: true, text: '% ต้นทุนสะสม' }, min: 0, max: 100, ticks: { callback: v => v + '%' } },
      },
    },
  });
}
async function mcOpenDrill(sku) {
  _mcBomSku = null;
  const it = (_mcLast?.items || []).find(x => x.sku_code === sku) || { description: sku };
  const _mcName = (it.description || sku).replace(/^\((?:A|B|T)\)\s*/, '');   // strip channel prefix like the table
  document.getElementById('mcDrillTitle').textContent = '' + _mcName + ' · ' + sku;
  const _mcm = document.getElementById('mcDrillModal'); _mcm.style.display = 'block'; _mcm.classList.remove('hidden');
  _mcm.scrollIntoView({ behavior: 'smooth', block: 'start' });
  mcDrillTab('branch');
  const bEl = document.getElementById('mcDrillBranch');
  bEl.innerHTML = '<p class="text-sm text-slate-400 py-6 text-center">กำลังโหลด…</p>';
  const q = { sku_code: sku, ..._mcPeriodParams('A', 'a'), ..._mcPeriodParams('B', 'b') };
  if (getGroup()) q.group = getGroup();
  let d; try { d = await api(`${API}/api/sales/menu-compare/drill?${new URLSearchParams(q)}`); }
  catch (e) { bEl.innerHTML = '<p class="text-sm text-red-500 py-6 text-center">โหลดไม่ได้</p>'; return; }
  _mcDrillData = d;
  _mcDrillPerHead = _mcPerHead;   // open the dive-in in the same mode as the main table
  mcRenderBranch();
}

let _mcDrillPerHead = false;
function mcSetBranchMode(perHead) { _mcDrillPerHead = perHead; mcRenderBranch(); }
function mcRenderBranch() {
  const el = document.getElementById('mcDrillBranch'); if (!el || !_mcDrillData) return;
  const br = _mcDrillData.branches || [], ph = _mcDrillPerHead;
  const toggle = `<div class="flex items-center gap-2 mb-3 flex-wrap"><span class="text-xs text-slate-500">มุมมอง:</span>
    <div style="display:inline-flex;background:var(--bg-subtle);border:1px solid var(--border);border-radius:var(--r-md);padding:3px;gap:2px">
      <button class="gran-btn ${ph?'':'gran-active'}" onclick="mcSetBranchMode(false)">รวม</button>
      <button class="gran-btn ${ph?'gran-active':''}" onclick="mcSetBranchMode(true)">ต่อคน</button>
    </div>${ph?'<span class="text-[11px] text-slate-400">จำนวน/คน = จำนวนเมนู ÷ ลูกค้าของสาขา · ต้นทุน/คน = COGS ÷ ลูกค้า</span>':''}</div>`;
  if (!br.length) { el.innerHTML = toggle + '<p class="text-sm text-slate-400 py-6 text-center">ไม่มีข้อมูลรายสาขา</p>'; return; }
  const nq = v => v == null ? '—' : (ph ? v.toFixed(2) : fmtN(v));
  const nc = v => v == null ? '—' : (ph ? v.toFixed(2) : fmt(v));
  const rows = br.map(b => {
    let qa, qb, ca, cb;
    if (ph) {
      qa = b.covers_a ? b.qty_a / b.covers_a : null; qb = b.covers_b ? b.qty_b / b.covers_b : null;
      ca = b.covers_a ? b.cogs_a / b.covers_a : null; cb = b.covers_b ? b.cogs_b / b.covers_b : null;
    } else { qa = b.qty_a; qb = b.qty_b; ca = b.cogs_a; cb = b.cogs_b; }
    const qd = (qa != null && qb) ? (qa - qb) / qb * 100 : null;
    const cd = (ca != null && cb) ? (ca - cb) / cb * 100 : null;
    return `<tr><td>${esc(b.branch)}</td>
      <td class="num tabular">${nq(qa)}</td><td class="num tabular">${nq(qb)}</td>
      <td class="num tabular">${_momSpan(qd, true)}</td>
      <td class="num tabular text-slate-500">${nc(ca)}</td><td class="num tabular text-slate-500">${nc(cb)}</td>
      <td class="num tabular">${_momSpan(cd, false)}</td></tr>`;
  }).join('');
  const L = ph ? ['จำนวน/คน A', 'จำนวน/คน B', 'ต้นทุน/คน A', 'ต้นทุน/คน B'] : ['จำนวน A', 'จำนวน B', 'ต้นทุน A', 'ต้นทุน B'];
  el.innerHTML = toggle + `<table class="tbl-top60 w-full"><thead><tr>
    <th>สาขา</th><th class="num">${L[0]}</th><th class="num">${L[1]}</th><th class="num">Δ%</th>
    <th class="num">${L[2]}</th><th class="num">${L[3]}</th><th class="num">Δ%</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}
function mcDrillTab(t) {
  ['branch', 'trend', 'bom'].forEach(x => {
    const el = document.getElementById('mcDrill' + x.charAt(0).toUpperCase() + x.slice(1));
    if (el) el.classList.toggle('hidden', x !== t);
  });
  document.querySelectorAll('#mcDrillTabs .gran-btn').forEach((b, i) => b.classList.toggle('gran-active', ['branch', 'trend', 'bom'][i] === t));
  if (t === 'trend') mcRenderTrend();
  if (t === 'bom') mcLoadBom();
}
function mcRenderTrend() {
  const tr = _mcDrillData?.trend || [];
  if (!tr.length || typeof mkChart !== 'function') return;
  mkChart('mcDrillChart', { type: 'line', data: { labels: tr.map(x => x.month), datasets: [
    { label: 'จำนวนขาย', data: tr.map(x => x.qty), borderColor: '#4a70b0', backgroundColor: 'transparent', tension: 0.3, pointRadius: 2, yAxisID: 'y' },
    { label: 'ต้นทุนขาย', data: tr.map(x => x.cogs), borderColor: '#ef4444', backgroundColor: 'transparent', tension: 0.3, pointRadius: 2, yAxisID: 'y1' },
  ] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { font: { size: 10 } } }, datalabels: { display: false } },
    scales: { y: { position: 'left', ticks: { callback: v => fmtN(v) } }, y1: { position: 'right', grid: { display: false }, ticks: { callback: v => fmt(v) } } } } });
}
async function mcLoadBom() {
  const sku = _mcDrillData?.sku_code, el = document.getElementById('mcDrillBom');
  if (!sku || !el || _mcBomSku === sku) return;
  el.innerHTML = '<p class="text-sm text-slate-400 py-6 text-center">กำลังโหลด BoM…</p>';
  let d; try { d = await api(`${API}/api/bom-cost/recipe/${encodeURIComponent(sku)}`); }
  catch (e) { el.innerHTML = '<p class="text-sm text-red-500 py-6 text-center">โหลด BoM ไม่ได้</p>'; return; }
  _mcBomSku = sku;
  const ings = d.ingredients || [];
  if (!d.has_recipe || !ings.length) { el.innerHTML = '<p class="text-sm text-amber-600 py-6 text-center">เมนูนี้ไม่มีสูตร BoM (เช่น เครื่องดื่ม/บุฟเฟต์ tier)</p>'; return; }
  el.innerHTML = `<table class="tbl-top60 w-full"><thead><tr><th>วัตถุดิบ</th><th class="num">ปริมาณ/เสิร์ฟ</th><th class="num">ราคา/หน่วย</th><th class="num">ต้นทุน</th></tr></thead>
    <tbody>${ings.map(g => `<tr><td>${esc(g.ingredient_name || g.ingredient_code || '—')}</td>
      <td class="num tabular">${g.qty_per_serving != null ? fmtN(g.qty_per_serving) : ''} ${esc(g.unit || '')}</td>
      <td class="num tabular text-slate-500">${g.price_per_unit != null ? fmt(g.price_per_unit) : '—'}</td>
      <td class="num tabular">${fmt(g.ingredient_cost || 0)}</td></tr>`).join('')}</tbody>
    <tfoot><tr><td colspan="3">รวมต้นทุน/เสิร์ฟ</td><td class="num tabular">${fmt(d.total_cost || 0)}</td></tr></tfoot></table>`;
}
function mcCloseDrill() { const m = document.getElementById('mcDrillModal'); m.style.display = 'none'; m.classList.add('hidden'); }

// ─── Global "count service charge as revenue" toggle (persisted, all analysis pages) ───
function inclService() { return localStorage.getItem('inclService') === '1'; }
function setInclService(on) {
  localStorage.setItem('inclService', on ? '1' : '0');
  // keep every visible toggle checkbox in sync
  document.querySelectorAll('input[id$="InclServiceChk"]').forEach(c => { c.checked = on; });
  // reload only the analysis view that is actually on screen (tabs hide via display:none)
  const visible = id => { const el = document.getElementById(id); return el && el.offsetParent !== null; };
  if (typeof loadBomCostTab === 'function' && visible('tab-bomcost')) loadBomCostTab();
  if (typeof loadBranchBom === 'function' && visible('tab-branchbom')) loadBranchBom(true);
}
// reflect persisted state on any checkbox when the page/tab renders
function _syncInclServiceChecks() {
  const on = inclService();
  document.querySelectorAll('input[id$="InclServiceChk"]').forEach(c => { c.checked = on; });
}

function bcFmtN(n) {
  if (n == null || isNaN(n)) return '—';
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
}
function bcFmtPct(n, digits=1) {
  if (n == null || isNaN(n)) return '—';
  return Number(n).toFixed(digits) + '%';
}
function bcFoodPctColor(p) {
  if (p == null) return 'text-slate-400';
  if (p < 55)  return 'text-blue-600';
  if (p <= 70) return 'text-emerald-600';
  if (p <= 85) return 'text-amber-600';
  return 'text-red-600';
}

async function loadBomCostTab() {
  // PR-PP3: the top bar is the period (one control per page): its months fill the hidden bcMonthFrom / bcMonthTo, and its
  // kind the detail (สัปดาห์ -> week, a short กำหนดเอง -> day, else month)
  (function () {
    const r = gPeriodResolve(), bf = document.getElementById('bcMonthFrom'), bt = document.getElementById('bcMonthTo');
    const g = /** @type {HTMLSelectElement|null} */ (document.getElementById('bcGranularity'));
    if (r) { if (bf) bf.value = r.cur[0].slice(0, 7); if (bt) bt.value = r.cur[1].slice(0, 7); }
    const k = getPeriodKind();
    if (g) g.value = k === 'week' ? 'week' : (k === 'range' && r && r.days <= 62) ? 'day' : 'month';
  })();
  const el = document.getElementById('bcStatus');
  if (el) el.textContent = 'กำลังโหลด...';
  try {
    if (bcMode === 'overview')       await bcLoadOverview();
    else if (bcMode === 'branch')    await bcLoadBranch();
    else if (bcMode === 'compare')   await bcLoadCompare();
    else if (bcMode === 'simulator') await bcLoadSimulator();
    if (el) el.textContent = '';
  } catch(e) {
    console.error(e);
    if (el) el.textContent = 'ผิดพลาด: ' + e.message;
  }
}

// Shared Top-N cost-driver table — used by overview / branch / compare so they
// look identical. Total row at the bottom + MoM (MTD-aware). Cost ↑ = red (bad).
function bcCostDriverTable(cd, scopeLabel, extraNote) {
  const items = cd.items || []; const tt = cd.total || {};
  const momNote = cd.mom_basis === 'mtd' ? `เทียบ 1–${cd.mtd_cutoff_day} วันเท่ากัน (MTD)` : 'เทียบเดือนก่อน';
  const momCell = (v, prev) => v == null ? '<span class="text-slate-300">—</span>'
    : `<span title="ช่วงก่อน ${prev != null ? bcFmtN(prev) : '—'}" style="color:${v <= 0 ? 'var(--good-ink)' : 'var(--bad-ink)'};font-weight:600">${v > 0 ? '▲' : '▼'} ${Math.abs(v).toFixed(1)}%</span>`;
  const pctLabel = cd.scope === 'branch' ? '% สาขา' : '% chain';
  const rows = items.map((r, i) => {
    const m = (typeof BIZ_TYPE_META !== 'undefined' ? (BIZ_TYPE_META[r.business_type] || BIZ_TYPE_META.other) : {icon:'',label:r.business_type,bg:'bg-slate-100',text:'text-slate-600'});
    return `<tr class="border-t border-slate-100">
      <td class="py-1.5 pr-2 text-slate-400 tabular-nums">${i + 1}</td>
      <td class="py-1.5 pr-2 font-mono text-indigo-600">${esc(r.sku_code)}</td>
      <td class="py-1.5 pr-2 truncate max-w-xs" title="${esc(r.description)}">${esc(r.description || '')}</td>
      <td class="py-1.5 pr-2"><span class="${m.bg} ${m.text} text-[10px] font-medium px-2 py-0.5 rounded-full">${m.icon} ${m.label}</span></td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${bcFmtN(r.qty)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${bcFmtN(r.unit_cost)}</td>
      <td class="py-1.5 pr-2 text-right font-semibold tabular-nums">${bcFmtN(r.total_cogs)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${bcFmtPct(r.pct_of_total, 1)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${momCell(r.mom_pct, r.prev_cogs)}</td>
      <td class="py-1.5 text-center"><button onclick="bcShowRecipe('${esc(r.sku_code)}')" class="text-indigo-600 hover:underline text-[10px]">${ICON.search}</button></td>
    </tr>`;
  }).join('');
  const totalRow = `<tr class="border-t-2 border-slate-300 font-semibold" style="background:var(--bg-subtle)">
      <td colspan="6" class="py-2 pr-2 text-right">รวม Top ${items.length}</td>
      <td class="py-2 pr-2 text-right tabular-nums">${bcFmtN(tt.shown_cogs)}</td>
      <td class="py-2 pr-2 text-right tabular-nums">${bcFmtPct(tt.shown_pct, 1)}</td>
      <td class="py-2 pr-2 text-right tabular-nums">${momCell(tt.mom_pct, tt.prev_cogs)}</td><td></td></tr>
    <tr style="background:var(--bg-subtle)"><td colspan="6" class="py-1.5 pr-2 text-right text-slate-500">รวมทั้งหมด (ทุก SKU)</td>
      <td class="py-1.5 pr-2 text-right font-semibold tabular-nums">${bcFmtN(tt.total_cogs)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums text-slate-400">100%</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${momCell(tt.mom_pct, tt.prev_cogs)}</td><td></td></tr>`;
  return `<div class="card p-4 mb-5">
      <div class="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 class="font-semibold">Top ${items.length} Cost-Driver SKUs ${scopeLabel || ''}</h3>
        <span class="text-xs text-slate-500">MoM ${momNote} · ต้นทุน↑=แดง (ผิดปกติ)${extraNote ? ' · ' + extraNote : ''}</span>
      </div>
      <div class="overflow-x-auto" style="max-height:520px;overflow-y:auto">
      <table class="text-xs w-full">
        <thead class="sticky top-0 bg-white"><tr class="text-left text-slate-500 border-b border-slate-200">
          <th class="py-2 pr-2">#</th><th class="py-2 pr-2">SKU</th><th class="py-2 pr-2">รายละเอียด</th>
          <th class="py-2 pr-2">ประเภท</th><th class="py-2 pr-2 text-right">จำนวน</th>
          <th class="py-2 pr-2 text-right">ต้นทุนต่อหน่วย</th><th class="py-2 pr-2 text-right">ต้นทุนขายรวม</th>
          <th class="py-2 pr-2 text-right">${pctLabel}</th><th class="py-2 pr-2 text-right">เทียบเดือนก่อน</th><th class="py-2 text-center">สูตร</th>
        </tr></thead>
        <tbody>${rows}${totalRow}</tbody>
      </table>
      </div>
    </div>`;
}

// ─── MODE 1: Overview ────────────────────────────────────────────────────────
async function bcLoadOverview() {
  _syncInclServiceChecks();
  const { mf, mt, gr } = bcPeriod();
  const svc = inclService();
  const qp = new URLSearchParams({ granularity: gr });
  if (mf) qp.set('month_from', mf);
  if (mt) qp.set('month_to',   mt);
  if (svc) qp.set('incl_service', 'true');
  // PR-PP3: the branch boxes are the bar's last month (to date while it is in progress) against the same days of the month
  // before and of the month a year back
  const momP = new URLSearchParams(); if (svc) momP.set('incl_service', 'true'); if (mt) momP.set('month', mt);
  const momQp = momP.toString() ? '?' + momP.toString() : '';
  const cdQp = new URLSearchParams({ scope: 'chain', limit: 60 });
  if (mf) cdQp.set('month_from', mf); if (mt) cdQp.set('month_to', mt);
  const [data, mom, cd, pc] = await Promise.all([
    api(`${API}/api/bom-cost/overview?${qp}`),
    api(`${API}/api/bom-cost/branch-foodcost-mom${momQp}`).catch(() => ({branches:[]})),
    api(`${API}/api/bom-cost/cost-drivers?${cdQp}`).catch(() => null),
    api(`${API}/api/period/cost?${gPeriodQuery()}`).catch(() => null),
  ]);
  bcOverviewData = data;
  bcFoodMomData  = mom;
  const t = Object.assign({}, data.totals || {});
  const PC = (pc && pc.cur && !svc) ? pc : null, pv = (o, k) => o ? o[k] : null;
  const chip = (k, unit, upGood, i) => PC ? `<div class="pkpi-cmp">${periodCmpHtml(PC.cur[k], pv(PC.prev, k), pv(PC.ly, k), { unit, upGood, range: PC.period, q: i === 0 })}</div>` : '';
  if (PC) {   // the tiles show the windows their chips compare (to date through the last closed day)
    periodChip(PC.period, true);
    const q = document.getElementById('bcPeriodQ'); if (q) q.innerHTML = periodQ(PC.period, 'pp');
    Object.assign(t, { revenue: PC.cur.revenue, cogs: PC.cur.cogs, gp: PC.cur.gp, gp_pct: PC.cur.gp_pct, food_cost_pct: PC.cur.food_cost_pct });
  }
  const pane = document.getElementById('bc-pane-overview');
  const fc = t.food_cost_pct;
  const con = data.concentration || {};
  const svcOn = !!t.incl_service;
  const revSub = svcOn ? `ex-VAT + service ${bcFmtN(t.service_charge)}` : 'ex-VAT';
  const kpis = `
    <div class="grid grid-cols-2 md:grid-cols-5 gap-3 mb-5">
      ${bcKpiCard('Total Revenue',  bcFmtN(t.revenue),       revSub,                   '#0284c7', chip('revenue', '%', true, 0))}
      ${bcKpiCard('Total COGS',     bcFmtN(t.cogs),          'BoM/Unit Cost',          '#dc2626', chip('cogs', '%', false))}
      ${bcKpiCard('Total GP',       bcFmtN(t.gp),            `${bcFmtPct(t.gp_pct)}`,  t.gp>=0?'#10b981':'#ef4444', chip('gp', '%', true))}
      ${bcKpiCard('GP %',           bcFmtPct(t.gp_pct),      '',                        '#10b981', chip('gp_pct', 'pp', true))}
      ${bcKpiCard('ต้นทุนอาหาร %',    bcFmtPct(PC ? PC.cur.food_cost_pct : t.food_cost_pct), svcOn?'(เป้า 55–70% · รวม service)':'(เป้า 55–70%)',
                                    (fc!=null && fc>70)?'#dc2626':(fc!=null && fc>=55)?'#10b981':'#f59e0b', chip('food_cost_pct', 'pp', false))}
    </div>`;

  // Top 60 cost-driver table (shared renderer · total row · MTD MoM)
  const concNote = `Concentration · Top 5: ${bcFmtPct(con.top_5_pct,1)} · Top 10: ${bcFmtPct(con.top_10_pct,1)} · Top 20: ${bcFmtPct(con.top_20_pct,1)} · Top 50: ${bcFmtPct(con.top_50_pct,1)}`;
  const topTbl = cd ? bcCostDriverTable(cd, '(chain-wide)', concNote)
    : '<div class="card p-4 mb-5 text-sm text-slate-400">ไม่มีข้อมูล cost-driver</div>';

  // Heatmap (HTML table coloring)
  const hm = data.heatmap || [];
  const branches = [...new Set(hm.map(r => r.branch))].sort();
  const months   = [...new Set(hm.map(r => r.month))].sort();
  const cellMap = {};
  hm.forEach(r => { cellMap[`${r.branch}|${r.month}`] = r; });
  const cellColor = (fcv) => {
    if (fcv == null) return '#f1f5f9';
    if (fcv < 55)  return '#dbeafe';
    if (fcv <= 70) return '#bbf7d0';
    if (fcv <= 85) return '#fde68a';
    if (fcv <= 100) return '#fdba74';
    return '#fca5a5';
  };
  const heatmapHtml = `
    <div class="card p-4 mb-5">
      <h3 class="font-semibold mb-3">แผนภาพความร้อน · สาขา × เดือน · ต้นทุนอาหาร %</h3>
      <div class="overflow-x-auto">
      <table class="text-[10px]">
        <thead><tr><th class="px-2 py-1 text-left text-slate-500">สาขา</th>
          ${months.map(m => `<th class="px-1 py-1 text-slate-500" style="writing-mode:vertical-rl;transform:rotate(180deg)">${m}</th>`).join('')}
        </tr></thead>
        <tbody>
          ${branches.map(b => `<tr>
            <td class="px-2 py-1 text-slate-700 font-medium whitespace-nowrap">${b}</td>
            ${months.map(m => {
              const c = cellMap[`${b}|${m}`];
              const fcv = c?.food_cost_pct;
              return `<td class="text-center" style="background:${cellColor(fcv)};min-width:40px"
                title="${b} · ${m}\\nRev: ${bcFmtN(c?.revenue)}\\nCOGS: ${bcFmtN(c?.cogs)}\\nGP: ${bcFmtN(c?.gp)}\\nFood%: ${bcFmtPct(fcv)}">${fcv!=null?fcv.toFixed(0):''}</td>`;
            }).join('')}
          </tr>`).join('')}
        </tbody>
      </table>
      </div>
      <p class="text-[10px] text-slate-400 mt-2">เขียว 55–70% (เป้า) · เหลือง/ส้ม > เป้า · แดง > 100% (ขาดทุน)</p>
    </div>`;

  // Branch breakdown — colored KPI boxes (latest month + MoM, click to drill down)
  const brHtml = bcRenderFoodCostBoxes(mom);

  pane.innerHTML = kpis + topTbl + heatmapHtml + brHtml;
}

// Hex color for the Food-Cost zone (matches bcFoodPctColor zones)
function bcFoodPctHex(p) {
  if (p == null)  return '#94a3b8';
  if (p < 55)     return '#2563eb';   // below target (too low)
  if (p <= 70)    return '#059669';   // on target
  if (p <= 85)    return '#d97706';   // above target
  return '#dc2626';                   // far above target
}

// Renders the per-branch Food-Cost KPI grid + a shared drill-down panel.
function bcRenderFoodCostBoxes(mom) {
  bcFoodDrillOpen = null;   // panel is rebuilt empty — keep toggle state in sync
  const brs = (mom && mom.branches) || [];
  const monLbl = mom && mom.month ? mom.month : '';
  const prevLbl = mom && mom.prev_month ? mom.prev_month : '';
  if (!brs.length) {
    return `<div class="card p-4 mb-5"><h3 class="font-semibold mb-1">แยกสาขา · ต้นทุนอาหาร %</h3>
      <p class="text-xs text-slate-400">ไม่มีข้อมูล</p></div>`;
  }
  const boxes = brs.map((r, i) => {
    const fcv  = r.food_cost_pct;
    const hex  = bcFoodPctHex(fcv);
    const dp   = r.delta_pp;
    let momHtml = '<span class="text-[10px] text-slate-400">— ไม่มีเดือนก่อน</span>';
    if (mom && mom.period) {   // PR-PP3: เทียบช่วงก่อน + เทียบปีก่อน in จุด on the same days (a cost: down = good)
      momHtml = `<span onclick="event.stopPropagation()">${periodCmpHtml(fcv, r.prev_food_cost_pct, r.ly_food_cost_pct, { unit: 'pp', upGood: false, range: mom.period, q: i === 0 })}</span>`;
    } else if (dp != null) {
      // ต้นทุนอาหาร: lower = better → a drop is good (green), a rise is bad (red)
      const good = dp < 0;
      const col  = dp === 0 ? '#64748b' : (good ? 'var(--good-ink)' : 'var(--bad-ink)');
      const arr  = dp === 0 ? '→' : (dp > 0 ? '▲' : '▼');
      momHtml = `<span class="text-[11px] font-semibold" style="color:${textInk(col)}">${arr} ${dp>0?'+':''}${dp.toFixed(1)}pp</span>
             <span class="text-[10px] text-slate-400"> vs ${prevLbl}</span>`;
    }
    return `<button type="button" onclick="bcDrillBranch(${i})"
        class="card p-3 text-left hover:shadow-md transition" data-bcbox="${i}"
        style="border:1px solid ${hex};cursor:pointer">
      <div class="text-xs font-medium text-slate-600 truncate" title="${esc(r.branch)}">${esc(r.branch)}</div>
      <div class="flex items-baseline gap-1 mt-0.5">
        <span class="text-2xl font-bold" style="color:${textInk(hex)}">${bcFmtPct(fcv)}</span>
      </div>
      <div class="mt-0.5">${momHtml}</div>
      <div class="text-[10px] text-slate-400 mt-1">COGS ${bcFmtN(r.cogs)} · ${mom && mom.period ? esc(mom.period.label_th) : monLbl}</div>
    </button>`;
  }).join('');

  return `<div class="card p-4 mb-5">
    <div class="flex items-center justify-between mb-1 flex-wrap gap-2">
      <h3 class="font-semibold">แยกสาขา · ต้นทุนอาหาร % <span class="text-xs font-normal text-slate-400">(เป้า 55–70%)</span></h3>
      <span class="text-xs text-slate-500">${monLbl}${prevLbl?` เทียบ ${prevLbl}`:''} · คลิกสาขาเพื่อดูรายละเอียด</span>
    </div>
    <p class="text-[10px] text-slate-400 mb-3">
      <span style="color:#2563eb">◼</span> ต่ำกว่าเป้า ·
      <span style="color:#059669">◼</span> ตามเป้า 55–70% ·
      <span style="color:#d97706">◼</span> สูงกว่าเป้า ·
      <span style="color:#dc2626">◼</span> สูงมาก · ▼ ลด = ดี · ▲ เพิ่ม = ต้องระวัง
    </p>
    <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">${boxes}</div>
    <div id="bcFoodDrill" class="mt-4"></div>
  </div>`;
}

// Drill-down: show one branch's MoM detail + top menu cost movers.
let bcFoodDrillOpen = null;
function bcDrillBranch(idx) {
  const host = document.getElementById('bcFoodDrill');
  const r = bcFoodMomData && bcFoodMomData.branches && bcFoodMomData.branches[idx];
  if (!host || !r) return;
  // toggle: clicking the open one again closes it
  if (bcFoodDrillOpen === idx) { host.innerHTML = ''; bcFoodDrillOpen = null; _bcHighlightBox(null); return; }
  bcFoodDrillOpen = idx;
  _bcHighlightBox(idx);

  const monLbl = bcFoodMomData.month || '';
  const prevLbl = bcFoodMomData.prev_month || '';
  const dpTxt = r.delta_pp == null ? '—'
    : `${r.delta_pp>0?'+':''}${r.delta_pp.toFixed(1)}pp`;
  const dpCol = r.delta_pp == null ? '#64748b' : (r.delta_pp < 0 ? 'var(--good-ink)' : (r.delta_pp>0?'var(--bad-ink)':'#64748b'));
  const cogsDeltaCol = r.cogs_delta > 0 ? 'var(--bad-ink)' : (r.cogs_delta < 0 ? 'var(--good-ink)' : '#64748b');

  const moverRow = (m) => {
    const up = m.delta > 0;
    const col = up ? 'var(--bad-ink)' : 'var(--good-ink)';
    const badge = m.is_new  ? '<span class="text-[9px] px-1 rounded bg-blue-100 text-blue-700 ml-1">ใหม่</span>'
              : (m.is_gone  ? '<span class="text-[9px] px-1 rounded bg-slate-200 text-slate-600 ml-1">หยุดขาย</span>' : '');
    return `<div class="flex items-center justify-between gap-2 py-1 border-b border-slate-100 text-xs">
      <div class="truncate min-w-0">
        <span class="font-mono text-indigo-600">${esc(m.sku_code||'')}</span>
        <span class="text-slate-600">${esc(m.description||'')}</span>${badge}
      </div>
      <div class="text-right whitespace-nowrap" style="flex:none">
        <span class="font-semibold" style="color:${textInk(col)}">${up?'+':''}${bcFmtN(m.delta)}</span>
        <span class="text-[10px] text-slate-400 ml-1">${bcFmtN(m.prev_cogs)}→${bcFmtN(m.cogs)}</span>
      </div>
    </div>`;
  };
  const ups   = (r.movers_up   || []);
  const downs = (r.movers_down || []);
  const empty = '<p class="text-[11px] text-slate-400 py-2">— ไม่มี</p>';

  host.innerHTML = `
    <div class="rounded-lg border border-slate-200 p-4" style="background:#f8fafc">
      <div class="flex items-center justify-between flex-wrap gap-2 mb-3">
        <h4 class="font-semibold">${esc(r.branch)} · ${monLbl} เทียบ ${prevLbl||'—'}</h4>
        <button onclick="bcDrillBranch(${idx})" class="text-xs text-slate-400 hover:text-slate-600">✕ ปิด</button>
      </div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4 text-center">
        <div class="bg-white rounded p-2 border border-slate-100">
          <div class="text-[10px] text-slate-400">ต้นทุนอาหาร %</div>
          <div class="text-lg font-bold" style="color:${textInk(bcFoodPctHex(r.food_cost_pct))}">${bcFmtPct(r.food_cost_pct)}</div>
          <div class="text-[10px] text-slate-400">ก่อน ${bcFmtPct(r.prev_food_cost_pct)}</div>
        </div>
        <div class="bg-white rounded p-2 border border-slate-100">
          <div class="text-[10px] text-slate-400">เทียบเดือนก่อน</div>
          <div class="text-lg font-bold" style="color:${textInk(dpCol)}">${dpTxt}</div>
          <div class="text-[10px] text-slate-400">เป้า 55–70%</div>
        </div>
        <div class="bg-white rounded p-2 border border-slate-100">
          <div class="text-[10px] text-slate-400">ต้นทุนขาย</div>
          <div class="text-base font-bold">${bcFmtN(r.cogs)}</div>
          <div class="text-[10px]" style="color:${textInk(cogsDeltaCol)}">${r.cogs_delta>0?'+':''}${bcFmtN(r.cogs_delta)} MoM</div>
        </div>
        <div class="bg-white rounded p-2 border border-slate-100">
          <div class="text-[10px] text-slate-400">รายได้ฐานคิดต้นทุน (costed)</div>
          <div class="text-base font-bold">${bcFmtN(r.costed_revenue)}</div>
          <div class="text-[10px] text-slate-400">ทั้งหมด ex-VAT ${bcFmtN(r.revenue)}</div>
        </div>
      </div>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <h5 class="text-xs font-semibold mb-1" style="color:var(--bad-ink)">▲ เมนูที่ต้นทุนเพิ่มมากสุด (Top ${ups.length})</h5>
          ${ups.length ? ups.map(moverRow).join('') : empty}
        </div>
        <div>
          <h5 class="text-xs font-semibold mb-1" style="color:var(--good-ink)">▼ เมนูที่ต้นทุนลดมากสุด (Top ${downs.length})</h5>
          ${downs.length ? downs.map(moverRow).join('') : empty}
        </div>
      </div>
      <p class="text-[10px] text-slate-400 mt-3">ตัวเลข = ส่วนต่าง COGS ระหว่าง ${prevLbl||'เดือนก่อน'} → ${monLbl} (บาท) · เรียงตามขนาดการเปลี่ยนแปลง</p>
    </div>`;
  host.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function _bcHighlightBox(idx) {
  document.querySelectorAll('[data-bcbox]').forEach(b => {
    b.style.outline = (idx != null && +b.dataset.bcbox === idx) ? '2px solid var(--accent)' : 'none';
  });
}

function bcKpiCard(label, val, sub, color, cmpHtml) {
  return `<div class="card p-3" style="border:1px solid ${color}">
    <div class="text-[10px] text-slate-500 font-medium uppercase">${label}</div>
    <div class="text-xl font-bold mt-0.5" style="color:${textInk(color)}">${val}</div>
    <div class="text-[10px] text-slate-400 mt-0.5">${sub||''}</div>${cmpHtml || ''}
  </div>`;
}

// ─── MODE 2: Per-branch ──────────────────────────────────────────────────────
async function bcLoadBranch() {
  const pane = document.getElementById('bc-pane-branch');
  const { mf, mt, gr } = bcPeriod();
  // Branch selector
  const brs = await api(`${API}/api/branches`);
  const brList = Array.isArray(brs) ? brs : (brs.items || []);
  const sel = pane.querySelector('#bcBranchSel');
  const currBranch = sel ? sel.value : (brList[0]?.name || '');
  const branchOptions = brList.map(b => `<option value="${esc(b.name)}" ${b.name===currBranch?'selected':''}>${esc(b.name)}</option>`).join('');
  if (!sel) {
    pane.innerHTML = `
      <div class="card p-3 mb-4 flex flex-wrap items-center gap-3 text-xs">
        <label>สาขา <select id="bcBranchSel" onchange="bcLoadBranch()" class="border border-slate-300 rounded-lg px-2 py-1 min-w-[200px]">
          <option value="ALL">ALL (รวมเครือ)</option>
          ${branchOptions}
        </select></label>
      </div>
      <div id="bcBranchBody"></div>`;
  }
  const useBranch = pane.querySelector('#bcBranchSel').value || 'ALL';
  const qp = new URLSearchParams({ branch: useBranch, granularity: gr, top_n: 50 });
  if (mf) qp.set('month_from', mf);
  if (mt) qp.set('month_to',   mt);
  const cdQp = new URLSearchParams({ scope: 'branch', branch: useBranch, limit: 60 });
  if (mf) cdQp.set('month_from', mf); if (mt) cdQp.set('month_to', mt);
  const [data, cd] = await Promise.all([
    api(`${API}/api/branch-bom/analysis?${qp}`),
    (useBranch && useBranch !== 'ALL')
      ? api(`${API}/api/bom-cost/cost-drivers?${cdQp}`).catch(() => null)
      : api(`${API}/api/bom-cost/cost-drivers?scope=chain&limit=60${mf?'&month_from='+mf:''}${mt?'&month_to='+mt:''}`).catch(() => null),
  ]);
  const body = pane.querySelector('#bcBranchBody');
  const t = data.totals || {};
  body.innerHTML = `
    <div class="grid grid-cols-2 md:grid-cols-5 gap-3 mb-5">
      ${bcKpiCard('Revenue',      bcFmtN(t.revenue),       'ex-VAT', '#0284c7')}
      ${bcKpiCard('COGS',         bcFmtN(t.cogs),          '',        '#dc2626')}
      ${bcKpiCard('GP',           bcFmtN(t.gp),            bcFmtPct(t.gp_pct), t.gp>=0?'#10b981':'#ef4444')}
      ${bcKpiCard('ต้นทุนอาหาร %',  bcFmtPct(t.food_cost_pct), '(เป้า 55–70%)', '#f59e0b')}
      ${bcKpiCard('Menus',        bcFmtN(t.menus_with_cost), `/ ${bcFmtN(t.menus_total)} ทั้งหมด`, '#4a70b0')}
    </div>
    ${cd ? bcCostDriverTable(cd, '· ' + esc(useBranch)) : '<div class="card p-4 mb-5 text-sm text-slate-400">ไม่มีข้อมูล cost-driver</div>'}
    <div class="card p-4 mb-5">
      <h3 class="font-semibold mb-3">25 อันดับวัตถุดิบ</h3>
      <div class="overflow-x-auto">
        <table class="text-xs w-full">
          <thead><tr class="text-left text-slate-500 border-b">
            <th class="py-1 pr-2">รหัส</th><th class="py-1 pr-2">ชื่อ</th>
            <th class="py-1 pr-2">ประเภท</th>
            <th class="py-1 pr-2 text-right">จำนวน</th><th class="py-1 pr-2 text-right">ต้นทุน</th>
            <th class="py-1 pr-2 text-right">% ของสาขา</th>
          </tr></thead>
          <tbody>
            ${(data.top_ingredients_by_cost||[]).map(r => `<tr class="border-t border-slate-100">
              <td class="py-1 pr-2 font-mono">${r.ingredient_code||'—'}</td>
              <td class="py-1 pr-2">${r.ingredient_name}</td>
              <td class="py-1 pr-2">${r.ingredient_type}</td>
              <td class="py-1 pr-2 text-right">${bcFmtN(r.total_qty)}</td>
              <td class="py-1 pr-2 text-right font-semibold">${bcFmtN(r.total_cost)}</td>
              <td class="py-1 pr-2 text-right">${bcFmtPct(r.pct_of_branch_cogs)}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>
    </div>
    <div class="card p-4 mb-5">
      <h3 class="font-semibold mb-3">ต้นทุนตามประเภทวัตถุดิบ</h3>
      <div class="flex flex-wrap gap-2">
        ${Object.entries(data.cost_by_ingredient_type||{}).sort((a,b)=>b[1]-a[1]).map(([k,v]) =>
          `<span class="px-3 py-1 bg-slate-100 rounded-full text-xs"><b>${k}</b>: ${bcFmtN(v)}</span>`).join('')}
      </div>
      <p class="text-[10px] text-slate-400 mt-2">Coverage: ${t.menus_with_bom_recipe||0} of ${t.menus_in_period||0} menus มี BoM recipe</p>
    </div>`;
}

async function bcShowRecipe(skuCode) {
  const data = await api(`${API}/api/bom-cost/recipe/${encodeURIComponent(skuCode)}`);
  const html = `
    <div onclick="this.remove()" class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div onclick="event.stopPropagation()" class="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[80vh] overflow-auto p-5">
        <div class="flex items-center justify-between mb-3">
          <div><div class="font-mono text-indigo-600 text-sm">${data.sku_code}</div>
               <div class="font-semibold">${data.description||''}</div></div>
          <button onclick="this.closest('.fixed').remove()" class="text-slate-400 hover:text-slate-700 text-2xl">✕</button>
        </div>
        ${data.has_recipe ? `
          <div class="text-xs text-slate-500 mb-3">Total recipe cost / serving: <b class="text-base text-slate-900">${bcFmtN(data.total_cost)}</b></div>
          <table class="text-xs w-full">
            <thead><tr class="text-left text-slate-500 border-b"><th class="py-1">วัตถุดิบ</th><th class="py-1">ประเภท</th><th class="py-1 text-right">จำนวน/เสิร์ฟ</th><th class="py-1">หน่วย</th><th class="py-1 text-right">ราคา</th><th class="py-1 text-right">ต้นทุน</th></tr></thead>
            <tbody>${(data.ingredients||[]).map(r => `<tr class="border-t border-slate-100">
              <td class="py-1">${r.ingredient_name}</td>
              <td class="py-1 text-slate-500">${r.ingredient_type||''}</td>
              <td class="py-1 text-right">${r.qty_per_serving!=null?r.qty_per_serving:''}</td>
              <td class="py-1">${r.unit||''}</td>
              <td class="py-1 text-right">${r.price_per_unit!=null?bcFmtN(r.price_per_unit):''}</td>
              <td class="py-1 text-right font-semibold">${bcFmtN(r.ingredient_cost)}</td>
            </tr>`).join('')}</tbody>
          </table>` : `<p class="text-amber-700 bg-amber-50 p-3 rounded">ไม่พบ BoM recipe สำหรับ SKU นี้</p>`}
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
}

// ─── MODE 3: Compare branches ────────────────────────────────────────────────
async function bcLoadCompare() {
  const pane = document.getElementById('bc-pane-compare');
  if (!pane.querySelector('#bcCmpSel')) {
    const brs = await api(`${API}/api/branches`);
    const brList = Array.isArray(brs) ? brs : (brs.items || []);
    pane.innerHTML = `
      <div class="card p-3 mb-4">
        <label class="text-xs"><b>เลือกสาขา (2–5):</b></label>
        <select id="bcCmpSel" multiple size="6" class="border border-slate-300 rounded-lg px-2 py-1 text-xs w-full mt-2" onchange="bcCmpSelectionChanged()">
          ${brList.map(b => `<option value="${esc(b.name)}">${esc(b.name)}</option>`).join('')}
        </select>
        <button onclick="bcLoadCompareRun()" class="mt-2 px-3 py-1.5 bg-indigo-600 text-white rounded-lg text-xs font-medium">เปรียบเทียบ</button>
      </div>
      <div id="bcCmpBody" class="text-sm text-slate-500">เลือก 2–5 สาขาแล้วกด "เปรียบเทียบ"</div>`;
  }
}
function bcCmpSelectionChanged() {
  const sel = document.getElementById('bcCmpSel');
  bcCompareSelection = Array.from(sel.selectedOptions).map(o => o.value);
}
async function bcLoadCompareRun() {
  const sel = document.getElementById('bcCmpSel');
  bcCompareSelection = Array.from(sel.selectedOptions).map(o => o.value);
  if (bcCompareSelection.length < 2 || bcCompareSelection.length > 5) {
    uiNotice('โปรดเลือก 2–5 สาขา'); return;
  }
  const { mf, mt } = bcPeriod();
  const qp = new URLSearchParams({ branches: bcCompareSelection.join(',') });
  if (mf) qp.set('month_from', mf);
  if (mt) qp.set('month_to', mt);
  const data = await api(`${API}/api/bom-cost/compare?${qp}`);
  const body = document.getElementById('bcCmpBody');
  const res = data.results || [];
  const variance = data.variance || {};
  const outlierBranches = (metric) => {
    const out = (variance[metric]?.outliers || []).map(o => o.branch);
    return new Set(out);
  };
  const ofFc = outlierBranches('food_cost_pct');
  const ofGp = outlierBranches('gp_pct');
  const ofCogs = outlierBranches('cogs');
  const ofRev = outlierBranches('revenue');
  const flag = (set, br) => set.has(br) ? 'bg-amber-50' : '';

  body.innerHTML = `
    <div class="card p-4 mb-4 overflow-x-auto">
      <table class="text-xs w-full">
        <thead><tr class="border-b"><th class="text-left py-2 pr-3">ตัวชี้วัด</th>
          ${res.map(r => `<th class="text-right py-2 pr-3">${esc(r.branch)}</th>`).join('')}
        </tr></thead>
        <tbody>
          <tr class="border-t"><td class="py-1 pr-3 text-slate-500">รายได้</td>
            ${res.map(r => `<td class="py-1 pr-3 text-right ${flag(ofRev, r.branch)}">${bcFmtN(r.revenue)}</td>`).join('')}</tr>
          <tr class="border-t"><td class="py-1 pr-3 text-slate-500">ต้นทุนขาย</td>
            ${res.map(r => `<td class="py-1 pr-3 text-right ${flag(ofCogs, r.branch)}">${bcFmtN(r.cogs)}</td>`).join('')}</tr>
          <tr class="border-t"><td class="py-1 pr-3 text-slate-500">GP</td>
            ${res.map(r => `<td class="py-1 pr-3 text-right">${bcFmtN(r.gp)}</td>`).join('')}</tr>
          <tr class="border-t"><td class="py-1 pr-3 text-slate-500">GP %</td>
            ${res.map(r => `<td class="py-1 pr-3 text-right ${flag(ofGp, r.branch)}">${bcFmtPct(r.gp_pct)}</td>`).join('')}</tr>
          <tr class="border-t"><td class="py-1 pr-3 text-slate-500">ต้นทุนอาหาร %</td>
            ${res.map(r => `<td class="py-1 pr-3 text-right ${flag(ofFc, r.branch)} ${bcFoodPctColor(r.food_cost_pct)}">${bcFmtPct(r.food_cost_pct)}</td>`).join('')}</tr>
          <tr class="border-t"><td class="py-1 pr-3 text-slate-500">Uncosted SKUs</td>
            ${res.map(r => `<td class="py-1 pr-3 text-right">${bcFmtN(r.uncosted_skus)}</td>`).join('')}</tr>
          ${[0,1,2].map(i => `
            <tr class="border-t"><td class="py-1 pr-3 text-slate-500">Top menu #${i+1}</td>
              ${res.map(r => {
                const m = r.top_menus[i];
                return `<td class="py-1 pr-3 text-right text-[10px]">${m?`<span class="font-mono text-indigo-600">${m.sku_code}</span> · ${bcFmtN(m.total_cogs)} (${bcFmtPct(m.pct_of_branch_cogs,1)})`:'—'}</td>`;
              }).join('')}
            </tr>`).join('')}
        </tbody>
      </table>
      <p class="text-[10px] text-slate-400 mt-2">เซลล์สีเหลือง = ค่าเบี่ยงเบนจากค่าเฉลี่ยกลุ่ม ≥ 10%</p>
    </div>
    <div class="card p-4 mb-4">
      <h3 class="font-semibold mb-3">ต้นทุนตามประเภทวัตถุดิบ (รายสาขา)</h3>
      <div class="overflow-x-auto">
        <table class="text-xs w-full">
          <thead><tr class="border-b"><th class="text-left py-1 pr-3">ประเภท</th>
            ${res.map(r => `<th class="text-right py-1 pr-3">${esc(r.branch)}</th>`).join('')}
          </tr></thead>
          <tbody>
            ${(()=>{
              const types = new Set();
              res.forEach(r => Object.keys(r.cost_by_ingredient_type||{}).forEach(t => types.add(t)));
              return [...types].sort().map(t => `<tr class="border-t">
                <td class="py-1 pr-3 font-medium">${t}</td>
                ${res.map(r => `<td class="py-1 pr-3 text-right">${bcFmtN(r.cost_by_ingredient_type?.[t]||0)}</td>`).join('')}
              </tr>`).join('');
            })()}
          </tbody>
        </table>
      </div>
    </div>`;

  // Per-branch shared cost-driver tables (Top 60 + total + MoM) — same as
  // overview/branch modes, so all three look identical.
  const cds = await Promise.all(bcCompareSelection.map(br => {
    const q = new URLSearchParams({ scope: 'branch', branch: br, limit: 60 });
    if (mf) q.set('month_from', mf); if (mt) q.set('month_to', mt);
    return api(`${API}/api/bom-cost/cost-drivers?${q}`).catch(() => null);
  }));
  let cdHtml = '<h3 class="font-semibold mb-2 mt-2">Top 60 SKU ที่ใช้ต้นทุนสูงสุด · แยกรายสาขา</h3>';
  bcCompareSelection.forEach((br, i) => { if (cds[i]) cdHtml += bcCostDriverTable(cds[i], '· ' + esc(br)); });
  body.insertAdjacentHTML('beforeend', cdHtml);
}

// ─── MODE 4: Simulator ───────────────────────────────────────────────────────
async function bcLoadSimulator() {
  const pane = document.getElementById('bc-pane-simulator');
  const { mf, mt } = bcPeriod();
  const qp = new URLSearchParams({ limit: 2000 });   // ALL menus that have BoM
  if (mf) qp.set('month_from', mf);
  if (mt) qp.set('month_to', mt);
  const data = await api(`${API}/api/bom-cost/simulator-base?${qp}`);
  bcSimBase = data;
  // scenarios list
  const scens = await api(`${API}/api/bom-cost/scenarios`);
  const scenList = scens.scenarios || [];
  pane.innerHTML = `
    <div class="flex flex-wrap items-center gap-3 mb-4">
      <label class="text-xs">สถานการณ์:
        <select id="bcScenSel" onchange="bcLoadScenario(this.value)" class="border border-slate-300 rounded-lg px-2 py-1 text-xs min-w-[180px]">
          <option value="">— Baseline —</option>
          ${scenList.map(s => `<option value="${s.id}">${esc(s.name)} (${s.n_items})</option>`).join('')}
        </select>
      </label>
      <button onclick="bcSaveScenario()" class="px-3 py-1.5 text-xs bg-emerald-600 text-white rounded-lg font-medium">บันทึกค่าปัจจุบัน</button>
      <button onclick="bcDeleteScenario()" class="px-3 py-1.5 text-xs bg-rose-100 text-rose-700 rounded-lg font-medium">ลบ</button>
      <button onclick="bcAdoptScenario()" class="px-3 py-1.5 text-xs bg-violet-600 text-white rounded-lg font-medium" title="บันทึกว่าใช้จริง แล้วติดตามเงินที่ประหยัดได้สะสม">นำไปใช้และติดตามผล</button>
      <button onclick="bcShowRoi()" class="px-3 py-1.5 text-xs bg-violet-100 text-violet-700 rounded-lg font-medium" title="ดูผลประหยัดสะสมของ scenario ที่ adopt">ROI</button>
      <button onclick="bcResetOverrides()" class="px-3 py-1.5 text-xs bg-slate-100 text-slate-700 rounded-lg">↺ ล้างค่า</button>
      <span class="border-l border-slate-200 h-5 mx-1"></span>
      <button onclick="bcExportTemplate()" class="px-3 py-1.5 text-xs bg-sky-600 text-white rounded-lg font-medium" title="ดาวน์โหลด CSV เปิดใน Excel แก้ตัวเลขแล้ว upload กลับ">ส่งออกแบบฟอร์ม</button>
      <button onclick="document.getElementById('bcTplFile').click()" class="px-3 py-1.5 text-xs bg-indigo-600 text-white rounded-lg font-medium">อัปโหลดแบบฟอร์ม</button>
      <input type="file" id="bcTplFile" accept=".csv,text/csv" style="display:none" onchange="bcImportTemplate(this)">
      <span class="text-xs text-slate-400 ml-auto" id="bcSimMeta">SKUs ในตาราง: ${data.skus.length}</span>
    </div>
    <div id="bcRoiPanel" class="card p-4 mb-4 hidden" style="border:1px solid var(--accent)"></div>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div class="card p-4">
        <h3 class="font-semibold mb-3">แก้ไข — ปรับต้นทุน · จำนวนเสิร์ฟ · ปิดเมนู</h3>
        <div class="flex flex-wrap gap-2 mb-3 text-xs">
          <input id="bcSimSearch" placeholder="ค้นหารหัสหรือชื่อเมนู" oninput="bcRenderSimEditor()" class="border border-slate-300 rounded-lg px-2 py-1 flex-1 min-w-[120px]">
          <select id="bcSimBizFilter" onchange="bcRenderSimEditor()" class="border border-slate-300 rounded-lg px-2 py-1">
            <option value="">ทั้งหมด</option>
            ${BIZ_TYPES.map(t => `<option value="${t}">${BIZ_TYPE_META[t].icon} ${t}</option>`).join('')}
          </select>
        </div>
        <div class="flex flex-wrap gap-2 mb-3 text-xs">
          <button onclick="bcApplyPct(-5)" class="px-2 py-1 bg-amber-100 text-amber-700 rounded">−5% ทั้งหมด</button>
          <button onclick="bcApplyPct(-10)" class="px-2 py-1 bg-amber-100 text-amber-700 rounded">−10% ทั้งหมด</button>
          <button onclick="bcApplyPct(10)" class="px-2 py-1 bg-rose-100 text-rose-700 rounded">+10% ทั้งหมด</button>
          <button onclick="bcApplyTargetFood()" class="px-2 py-1 bg-indigo-100 text-indigo-700 rounded">ตั้งเป้าต้นทุนอาหาร % =
            <input id="bcTargetFood" type="number" value="65" class="w-12 border border-indigo-300 rounded px-1" onclick="event.stopPropagation()"></button>
        </div>
        <div id="bcSimEditor" style="max-height:520px;overflow-y:auto"></div>
      </div>
      <div class="card p-4">
        <h3 class="font-semibold mb-3">ผลต่อ KPI ทันที</h3>
        <div id="bcSimKpis"></div>
      </div>
    </div>`;
  bcRenderSimEditor();
  bcRecomputeKpis();
}

function bcRenderSimEditor() {
  if (!bcSimBase) return;
  const q   = (document.getElementById('bcSimSearch')?.value || '').toLowerCase();
  const bt  = document.getElementById('bcSimBizFilter')?.value || '';
  const rows = (bcSimBase.skus||[]).filter(r => {
    if (bt && r.business_type !== bt) return false;
    if (q && !(`${r.sku_code} ${r.description}`.toLowerCase().includes(q))) return false;
    return true;
  });
  const el = document.getElementById('bcSimEditor');
  el.innerHTML = `<table class="text-xs w-full">
    <thead class="sticky top-0 bg-white"><tr class="border-b text-slate-500">
      <th class="text-left py-1 pr-2">SKU</th><th class="text-left py-1 pr-2">รายละเอียด</th>
      <th class="text-right py-1 pr-2">ต้นทุน/หน่วย<br><span class="font-normal text-[9px]">เดิม → ใหม่</span></th>
      <th class="text-right py-1 pr-2">จำนวนเสิร์ฟ<br><span class="font-normal text-[9px]">เดิม → ใหม่</span></th>
      <th class="text-center py-1">ปิด</th></tr></thead>
    <tbody>
      ${rows.map(r => {
        const ov = bcSimOverrides[r.sku_code];
        const qv = bcSimQty[r.sku_code];
        const off = !!bcSimDisabled[r.sku_code];
        return `<tr class="border-t border-slate-100 ${off?'opacity-40':''}">
          <td class="py-1 pr-2 font-mono text-indigo-600">${r.sku_code}</td>
          <td class="py-1 pr-2 truncate max-w-[160px]" title="${esc(r.description)}">${esc(r.description)}</td>
          <td class="py-1 pr-2 text-right whitespace-nowrap">
            <span class="text-slate-400">${bcFmtN(r.current_unit_cost)}</span>
            <input type="number" min="0" step="0.01" value="${ov!=null?ov:r.current_unit_cost}"
              data-sku="${r.sku_code}" data-base="${r.current_unit_cost}" oninput="bcOnOverrideEdit(this)" ${off?'disabled':''}
              class="w-16 border border-slate-200 rounded px-1 py-0.5 text-right ${ov!=null?'bg-amber-50 border-amber-300':''}">
          </td>
          <td class="py-1 pr-2 text-right whitespace-nowrap">
            <span class="text-slate-400">${bcFmtN(r.qty)}</span>
            <input type="number" min="0" step="1" value="${qv!=null?qv:Math.round(r.qty)}"
              data-sku="${r.sku_code}" data-baseqty="${r.qty}" oninput="bcOnQtyEdit(this)" ${off?'disabled':''}
              class="w-20 border border-slate-200 rounded px-1 py-0.5 text-right ${qv!=null?'bg-sky-50 border-sky-300':''}">
          </td>
          <td class="py-1 text-center">
            <input type="checkbox" ${off?'checked':''} data-sku="${r.sku_code}" onchange="bcOnDisableToggle(this)" title="ปิดการขายเมนูนี้ (discontinue)">
          </td>
        </tr>`;
      }).join('')}
    </tbody></table>`;
}
function bcOnQtyEdit(inp) {
  const sku = inp.dataset.sku, base = parseFloat(inp.dataset.baseqty), v = parseFloat(inp.value);
  if (!isFinite(v) || Math.abs(v - base) < 0.5) { delete bcSimQty[sku]; inp.classList.remove('bg-sky-50','border-sky-300'); }
  else { bcSimQty[sku] = v; inp.classList.add('bg-sky-50','border-sky-300'); }
  bcRecomputeKpis();
}
function bcOnDisableToggle(chk) {
  const sku = chk.dataset.sku;
  if (chk.checked) bcSimDisabled[sku] = true; else delete bcSimDisabled[sku];
  bcRenderSimEditor(); bcRecomputeKpis();
}
function bcOnOverrideEdit(inp) {
  const sku = inp.dataset.sku;
  const base = parseFloat(inp.dataset.base);
  const v = parseFloat(inp.value);
  if (!isFinite(v) || Math.abs(v - base) < 1e-9) {
    delete bcSimOverrides[sku];
    inp.classList.remove('bg-amber-50','border-amber-300');
  } else {
    bcSimOverrides[sku] = v;
    inp.classList.add('bg-amber-50','border-amber-300');
  }
  bcRecomputeKpis();
}
function bcRecomputeKpis() {
  if (!bcSimBase) return;
  const b = bcSimBase.baseline;
  let cogsDelta = 0, revDelta = 0;
  (bcSimBase.skus||[]).forEach(r => {
    const baseCogs = r.period_cogs || 0;
    const baseRev  = r.period_revenue || 0;          // à la carte; buffet dishes = 0
    let newCogs, newRev;
    if (bcSimDisabled[r.sku_code]) {                 // menu closed → no cost, no revenue
      newCogs = 0; newRev = 0;
    } else {
      const unit = bcSimOverrides[r.sku_code] != null ? bcSimOverrides[r.sku_code] : r.current_unit_cost;
      const qty  = bcSimQty[r.sku_code] != null ? bcSimQty[r.sku_code] : r.qty;
      newCogs = qty * unit;
      newRev  = r.qty > 0 ? baseRev * (qty / r.qty) : baseRev;   // revenue scales with servings
    }
    cogsDelta += (newCogs - baseCogs);
    revDelta  += (newRev  - baseRev);
  });
  const scenCogs   = b.cogs + cogsDelta;
  const scenCosted = b.costed_revenue + revDelta;
  const scenRev    = b.revenue + revDelta;
  const scenGp     = scenCosted - scenCogs;
  const scenGpPct  = scenCosted > 0 ? (scenGp / scenCosted * 100) : null;
  const scenFood   = scenCosted > 0 ? (scenCogs / scenCosted * 100) : null;
  const dR = scenRev - b.revenue, dC = scenCogs - b.cogs, dG = scenGp - b.gp;
  const nOv = Object.keys(bcSimOverrides).length, nQ = Object.keys(bcSimQty).length, nD = Object.keys(bcSimDisabled).length;
  document.getElementById('bcSimKpis').innerHTML = `
    <table class="text-sm w-full">
      <thead><tr class="text-xs text-slate-500 border-b"><th class="text-left py-1">ตัวชี้วัด</th><th class="text-right py-1">ฐานเดิม</th><th class="text-right py-1">สถานการณ์จำลอง</th><th class="text-right py-1">Δ</th></tr></thead>
      <tbody>
        <tr class="border-t"><td class="py-1">รายได้</td><td class="text-right">${bcFmtN(b.revenue)}</td><td class="text-right font-semibold">${bcFmtN(scenRev)}</td><td class="text-right ${dR>=0?'text-emerald-600':'text-red-600'}">${Math.abs(dR)<1?'—':(dR>0?'+':'')+bcFmtN(dR)}</td></tr>
        <tr class="border-t"><td class="py-1">ต้นทุนขาย</td><td class="text-right">${bcFmtN(b.cogs)}</td><td class="text-right font-semibold">${bcFmtN(scenCogs)}</td><td class="text-right ${dC>0?'text-red-600':'text-emerald-600'}">${Math.abs(dC)<1?'—':(dC>0?'+':'')+bcFmtN(dC)}</td></tr>
        <tr class="border-t"><td class="py-1">GP</td><td class="text-right">${bcFmtN(b.gp)}</td><td class="text-right font-semibold">${bcFmtN(scenGp)}</td><td class="text-right ${dG>=0?'text-emerald-600':'text-red-600'}">${Math.abs(dG)<1?'—':(dG>=0?'+':'')+bcFmtN(dG)}</td></tr>
        <tr class="border-t"><td class="py-1">GP %</td><td class="text-right">${bcFmtPct(b.gp_pct)}</td><td class="text-right font-semibold">${bcFmtPct(scenGpPct)}</td><td class="text-right">${scenGpPct!=null&&b.gp_pct!=null?((scenGpPct-b.gp_pct>=0?'+':'')+(scenGpPct-b.gp_pct).toFixed(2)+'pp'):''}</td></tr>
        <tr class="border-t"><td class="py-1">ต้นทุนอาหาร %</td><td class="text-right ${bcFoodPctColor(b.food_cost_pct)}">${bcFmtPct(b.food_cost_pct)}</td><td class="text-right font-semibold ${bcFoodPctColor(scenFood)}">${bcFmtPct(scenFood)}</td><td class="text-right">${scenFood!=null&&b.food_cost_pct!=null?((scenFood-b.food_cost_pct>=0?'+':'')+(scenFood-b.food_cost_pct).toFixed(2)+'pp'):''}</td></tr>
      </tbody>
    </table>
    <p class="text-[10px] text-slate-400 mt-3">ปรับใช้: ต้นทุน ${nOv} · จำนวนเสิร์ฟ ${nQ} · ปิดเมนู ${nD} · preview client-side · COGS=BoM เท่านั้น ${uiQ('bcsim:note', 'รายได้ในสถานการณ์จำลอง', '<p>หมายเหตุ: เมนูบุฟเฟ่ต์รายได้/จาน = 0 (ปิดเมนู = ประหยัดต้นทุน รายได้ค่าบุฟเฟ่ต์คงเดิม) · à la carte รายได้ปรับตามจำนวนเสิร์ฟ</p>')}</p>`;
}
function bcApplyPct(p) {
  (bcSimBase?.skus||[]).forEach(r => {
    if (r.current_unit_cost > 0) bcSimOverrides[r.sku_code] = +(r.current_unit_cost * (1 + p/100)).toFixed(4);
  });
  bcRenderSimEditor(); bcRecomputeKpis();
}
function bcApplyTargetFood() {
  const target = parseFloat(document.getElementById('bcTargetFood').value);
  if (!isFinite(target) || target <= 0) return;
  const b = bcSimBase.baseline;
  if (!(b.food_cost_pct > 0)) return;
  const scale = target / b.food_cost_pct;
  (bcSimBase?.skus||[]).forEach(r => {
    if (r.current_unit_cost > 0) bcSimOverrides[r.sku_code] = +(r.current_unit_cost * scale).toFixed(4);
  });
  bcRenderSimEditor(); bcRecomputeKpis();
}
function bcResetOverrides() {
  bcSimOverrides = {}; bcSimQty = {}; bcSimDisabled = {};   // คืนค่าทั้งหมด (ต้นทุน + จำนวน + ปิดเมนู)
  bcRenderSimEditor(); bcRecomputeKpis();
}
// ── Template export/import (edit 337 menus in Excel instead of inline) ──
function bcExportTemplate() {
  if (!bcSimBase) return;
  const hdr = ['sku_code','description','current_unit_cost','current_qty','new_unit_cost','new_qty','close(1=ปิดเมนู)'];
  const rows = [hdr.join(',')];
  (bcSimBase.skus||[]).forEach(r => {
    const newCost = bcSimOverrides[r.sku_code] != null ? bcSimOverrides[r.sku_code] : r.current_unit_cost;
    const newQty  = bcSimQty[r.sku_code] != null ? bcSimQty[r.sku_code] : Math.round(r.qty);
    const close   = bcSimDisabled[r.sku_code] ? 1 : 0;
    const desc = '"' + String(r.description||'').replace(/"/g,'""') + '"';
    rows.push([r.sku_code, desc, r.current_unit_cost, Math.round(r.qty), newCost, newQty, close].join(','));
  });
  downloadCSV(rows.join('\n'), `simulator_template_${new Date().toISOString().slice(0,10)}.csv`);
}
function _parseCSV(text) {
  // minimal RFC-4180 parser (handles quoted fields, commas, "" escapes, CRLF)
  const rows = []; let row = [], cur = '', q = false;
  text = text.replace(/^﻿/, '');
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i+1] === '"') { cur += '"'; i++; } else q = false; }
      else cur += c;
    } else if (c === '"') q = true;
    else if (c === ',') { row.push(cur); cur = ''; }
    else if (c === '\n') { row.push(cur); rows.push(row); row = []; cur = ''; }
    else if (c === '\r') { /* skip */ }
    else cur += c;
  }
  if (cur.length || row.length) { row.push(cur); rows.push(row); }
  return rows;
}
function bcImportTemplate(input) {
  const file = input.files && input.files[0];
  if (!file || !bcSimBase) return;
  const reader = new FileReader();
  reader.onload = e => {
    try {
      const rows = _parseCSV(String(e.target.result));
      if (rows.length < 2) { uiNotice('ไฟล์ว่างหรือรูปแบบไม่ถูกต้อง'); return; }
      const hdr = rows[0].map(h => h.trim().toLowerCase());
      const ix = n => hdr.findIndex(h => h.startsWith(n));
      const iSku = ix('sku_code'), iCost = ix('new_unit_cost'), iQty = ix('new_qty'), iClose = ix('close');
      if (iSku < 0 || iCost < 0) { uiNotice('ไม่พบคอลัมน์รหัสเมนูหรือต้นทุนใหม่ ให้ใช้ไฟล์แม่แบบที่ส่งออกจากหน้านี้'); return; }
      const cur = {}; (bcSimBase.skus||[]).forEach(r => { cur[r.sku_code] = r; });
      let nCost = 0, nQty = 0, nClose = 0, unknown = 0;
      bcSimOverrides = {}; bcSimQty = {}; bcSimDisabled = {};   // template replaces current scenario
      for (let i = 1; i < rows.length; i++) {
        const r = rows[i]; if (!r || !r[iSku]) continue;
        const sku = String(r[iSku]).trim(); const base = cur[sku];
        if (!base) { unknown++; continue; }
        const nc = parseFloat(r[iCost]);
        if (isFinite(nc) && Math.abs(nc - base.current_unit_cost) > 1e-9) { bcSimOverrides[sku] = nc; nCost++; }
        if (iQty >= 0) { const nq = parseFloat(r[iQty]); if (isFinite(nq) && Math.abs(nq - base.qty) >= 0.5) { bcSimQty[sku] = nq; nQty++; } }
        if (iClose >= 0) { const cl = String(r[iClose]).trim(); if (cl === '1' || cl.toLowerCase() === 'true' || cl === 'ปิด') { bcSimDisabled[sku] = true; nClose++; } }
      }
      bcRenderSimEditor(); bcRecomputeKpis();
      uiNotice(`อัปโหลดสำเร็จ ✓\nปรับต้นทุน ${nCost} · จำนวนเสิร์ฟ ${nQty} · ปิดเมนู ${nClose}` + (unknown ? `\n(ข้าม ${unknown} แถวที่ไม่รู้จัก SKU)` : ''));
    } catch (err) { uiNotice('อ่านไฟล์ไม่สำเร็จ: ' + err.message); }
    finally { input.value = ''; }
  };
  reader.readAsText(file, 'utf-8');
}
async function bcSaveScenario() {
  const name = await uiPrompt('ชื่อ Scenario:', 'My scenario');
  if (!name) return;
  const desc = await uiPrompt('คำอธิบาย (optional):', '');
  // union of all changed SKUs (cost / qty / disabled). unit_cost is always sent
  // (current cost when only qty/disable changed) because the column is NOT NULL.
  const curCost = {}; (bcSimBase?.skus || []).forEach(r => { curCost[r.sku_code] = r.current_unit_cost; });
  const codes = new Set([...Object.keys(bcSimOverrides), ...Object.keys(bcSimQty), ...Object.keys(bcSimDisabled)]);
  const overrides = [...codes].map(sku_code => ({
    sku_code,
    unit_cost: bcSimOverrides[sku_code] != null ? bcSimOverrides[sku_code] : (curCost[sku_code] || 0),
    qty: bcSimQty[sku_code] != null ? bcSimQty[sku_code] : null,
    disabled: !!bcSimDisabled[sku_code],
  }));
  if (!overrides.length) { uiNotice('ยังไม่มีการปรับ (ต้นทุน/จำนวน/ปิดเมนู)'); return; }
  const r = await api(`${API}/api/bom-cost/scenarios`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name, description: desc, overrides})
  });
  uiNotice(`บันทึก Scenario #${r.id} สำเร็จ (${r.items_inserted} items)`);
  bcLoadSimulator();
}
async function bcLoadScenario(id) {
  if (!id) { bcSimOverrides = {}; bcSimQty = {}; bcSimDisabled = {}; bcRenderSimEditor(); bcRecomputeKpis(); return; }
  const r = await api(`${API}/api/bom-cost/scenarios/${id}`);
  bcSimOverrides = {}; bcSimQty = {}; bcSimDisabled = {};
  const curCost = {}; (bcSimBase?.skus || []).forEach(r => { curCost[r.sku_code] = r.current_unit_cost; });
  (r.items||[]).forEach(it => {
    // only treat unit_cost as an override if it actually differs from current
    if (it.unit_cost != null && (curCost[it.sku_code] == null || Math.abs(it.unit_cost - curCost[it.sku_code]) > 1e-9))
      bcSimOverrides[it.sku_code] = it.unit_cost;
    if (it.new_qty != null) bcSimQty[it.sku_code] = it.new_qty;
    if (it.disabled) bcSimDisabled[it.sku_code] = true;
  });
  bcRenderSimEditor(); bcRecomputeKpis();
}
async function bcDeleteScenario() {
  const id = document.getElementById('bcScenSel')?.value;
  if (!id) { uiNotice('เลือก scenario ก่อน'); return; }
  if (!await uiConfirm('ลบ scenario นี้?')) return;
  await fetch(`${API}/api/bom-cost/scenarios/${id}`, {method:'DELETE'});
  bcLoadSimulator();
}

// ── Phase B: adopt a scenario + show its realized-savings (ROI) ledger ─────────
async function bcAdoptScenario() {
  const id = document.getElementById('bcScenSel')?.value;
  if (!id) { uiNotice('เลือก scenario ที่บันทึกไว้ก่อน (หรือกด Save current)'); return; }
  if (!await uiConfirm('บันทึกว่า "ใช้จริง" (adopt)?\nจะบันทึกต้นทุนปัจจุบันเป็นฐาน แล้วเริ่มนับเงินที่ประหยัดได้จริงตั้งแต่เดือนล่าสุดเป็นต้นไป')) return;
  const r = await fetch(`${API}/api/bom-cost/scenarios/${id}/adopt`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({})
  });
  if (!r.ok) { uiNotice('Adopt ไม่สำเร็จ'); return; }
  bcShowRoi();
}
async function bcUnadopt(id) {
  if (!await uiConfirm('ยกเลิกการ adopt? (ล้าง baseline และผลประหยัดสะสม)')) return;
  await fetch(`${API}/api/bom-cost/scenarios/${id}/unadopt`, { method: 'POST' });
  bcShowRoi();
}
async function bcShowRoi() {
  const id = document.getElementById('bcScenSel')?.value;
  const panel = document.getElementById('bcRoiPanel'); if (!panel) return;
  if (!id) { uiNotice('เลือก scenario ก่อน'); return; }
  panel.classList.remove('hidden');
  panel.innerHTML = '<p class="text-sm text-slate-400 py-4 text-center">กำลังโหลด…</p>';
  let d; try { d = await api(`${API}/api/bom-cost/scenarios/${id}/savings`); }
  catch (e) { panel.innerHTML = '<p class="text-sm text-red-500 py-4 text-center">โหลดไม่สำเร็จ</p>'; return; }
  bcRenderRoi(d, id);
}
function bcRenderRoi(d, id) {
  const panel = document.getElementById('bcRoiPanel'); if (!panel) return;
  const close = `<button onclick="document.getElementById('bcRoiPanel').classList.add('hidden')" class="topbar-icon-btn" aria-label="ปิด">✕</button>`;
  if (!d.adopted) {
    panel.innerHTML = `<div class="flex items-center justify-between"><h3 class="text-sm font-bold" style="color:var(--accent)">ผลประหยัดจริง</h3>${close}</div>
      <p class="text-sm text-slate-500 mt-2">scenario นี้ยังไม่ได้ adopt — กด <b>Adopt &amp; Track ROI</b> เพื่อเริ่มจับ baseline แล้วติดตามเงินที่ประหยัดได้จริง</p>`;
    return;
  }
  const t = d.totals || {}, sc = d.scenario || {};
  const prog = t.progress_pct == null ? 0 : Math.max(0, Math.min(100, t.progress_pct));
  const rows = (d.items || []).map(i => {
    const pp = i.unit_progress_pct;
    return `<tr class="border-b" style="border-color:var(--border)">
      <td class="py-1.5 pr-2 font-mono text-[10px] text-slate-400">${esc(i.sku_code)}</td>
      <td class="py-1.5 pr-2 max-w-xs truncate" title="${esc(i.description||'')}">${esc((i.description||'').replace(/^\((?:A|B|T)\)\s*/,''))}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${fmt(i.baseline_cost)}→${fmt(i.current_cost)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums text-slate-400">${fmt(i.target_cost)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${fmtN(i.qty_since_adopt)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:${i.realized_saving>0?'var(--good-ink)':'var(--text-muted)'}">${fmt(i.realized_saving)}</td>
      <td class="py-1.5 text-right tabular-nums text-slate-400">${pp==null?'—':pp.toFixed(0)+'%'}</td></tr>`;
  }).join('');
  panel.innerHTML = `
    <div class="flex items-center justify-between flex-wrap gap-2">
      <h3 class="text-sm font-bold" style="color:var(--accent)">ผลประหยัดจริง (ROI) — ${esc(sc.name||'')} ${uiQ('bcroi:real', 'ผลประหยัดจริง', '<p>ประหยัด "จริง" = (ต้นทุน/หน่วยตอน adopt − ต้นทุน/หน่วยปัจจุบัน) × จำนวนที่ขายตั้งแต่ adopt — จะขึ้นเมื่อต้นทุนจริงลดลงตามที่วางแผน</p>')}</h3>
      <div class="flex items-center gap-2">
        <span class="text-[11px] text-slate-400">adopt ${esc((sc.adopted_at||'').slice(0,10))} · baseline ${esc(sc.baseline_month||'')} · ${t.months_tracked||0} เดือน</span>
        <button onclick="bcUnadopt('${id}')" class="text-[11px] border border-slate-200 rounded px-2 py-0.5" style="color:var(--text-muted)">ยกเลิกการนำไปใช้</button>
        ${close}
      </div>
    </div>
    <div class="flex flex-wrap items-end gap-6 mt-3 mb-2">
      <div><div class="text-[11px] text-slate-400">ประหยัดได้จริงสะสม</div>
        <div class="text-2xl font-bold" style="color:var(--good-ink)">${fmt(t.realized_saving)}</div></div>
      <div><div class="text-[11px] text-slate-400">เป้าหมายเต็ม (ถ้าทำได้ครบ)</div>
        <div class="text-lg font-semibold text-slate-500">${fmt(t.potential_saving)}</div></div>
      <div class="flex-1 min-w-[160px]">
        <div class="text-[11px] text-slate-400 mb-1">ความคืบหน้า ${t.progress_pct==null?'—':t.progress_pct.toFixed(0)+'%'}</div>
        <div style="height:10px;background:var(--bg-subtle);border-radius:var(--r-md);overflow:hidden">
          <div style="height:100%;width:${prog}%;background:#7a66b8"></div></div>
      </div>
    </div>
    <div class="overflow-auto" style="max-height:300px">
      <table class="text-xs w-full"><thead><tr style="color:var(--text-muted)" class="border-b">
        <th class="py-1 pr-2 text-left">SKU</th><th class="py-1 pr-2 text-left">เมนู</th>
        <th class="py-1 pr-2 text-right">ฐานเดิม→ปัจจุบัน</th><th class="py-1 pr-2 text-right">เป้า</th>
        <th class="py-1 pr-2 text-right">ขาย (หลังนำไปใช้)</th><th class="py-1 pr-2 text-right">ประหยัดจริง</th><th class="py-1 text-right">คืบหน้า</th>
      </tr></thead><tbody>${rows || '<tr><td colspan="7" class="py-3 text-center text-slate-400">ไม่มีรายการ</td></tr>'}</tbody></table>
    </div>`;
}
