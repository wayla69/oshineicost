// ─── 09b-cost-pnl.js — On-screen error capture · cost coverage tab · branch P&L tab and its branch×month drill ───
// Slice 2/5 of the former 09-deep-tools.js (split 2026-09-05 by contiguous range, so every
// statement keeps its load order). Shared global scope: index.html loads js/*.js in name order.

// ── Error capture (visible on-screen for file:// debugging) ────
(function() {
  const _errors = [];
  function showErrBanner(msg) {
    let b = document.getElementById('_errBanner');
    if (!b) {
      b = document.createElement('div');
      b.id = '_errBanner';
      b.style.cssText = 'position:fixed;bottom:12px;left:12px;right:12px;z-index:99999;background:#fef2f2;border:1px solid #fca5a5;border-radius:4px;padding:10px 14px;font-size:12px;color:#991b1b;max-height:140px;overflow:auto;box-shadow:0 4px 12px rgba(0,0,0,.15)';
      document.body.appendChild(b);
    }
    _errors.push(msg);
    b.innerHTML = '<strong>JS errors (close to ignore):</strong><br>' + _errors.map(e => '• ' + e).join('<br>') +
      '<button onclick="this.parentNode.remove()" style="float:right;background:none;border:none;cursor:pointer;font-size:14px">✕</button>';
  }
  // Errors we silently log (cosmetic only — Chart.js datalabel formatters that hit null/undefined values)
  const _IGNORE_RE = /toFixed is not a function|Cannot read propert(y|ies) of (null|undefined).*toFixed/;
  window.addEventListener('error', function(e) {
    const msg = e.message || 'Unknown error';
    if (_IGNORE_RE.test(msg)) { console.warn('[ignored]', msg, e.filename, e.lineno); return; }
    showErrBanner(msg + (e.filename ? ' @ '+e.filename.split('/').pop()+':'+e.lineno : ''));
  });
  window.addEventListener('unhandledrejection', function(e) {
    if (e.reason === STALE) return;                                   // expected — stale nav, already toasted
    const msg = (e.reason && e.reason.message) ? e.reason.message : String(e.reason);
    if (_IGNORE_RE.test(msg) || /abort/i.test(msg)) { console.warn('[ignored promise]', msg); return; }
    showErrBanner('Unhandled promise: ' + msg);
  });
  // Check vendor load failures
  window.addEventListener('load', function() {
    if (window.__vendorErr && __vendorErr.length) {
      showErrBanner('Vendor script(s) failed to load: ' + __vendorErr.join(', '));
    }
  });
})();

// ── Cost Coverage tab (Analysis #3) ────────────────────────────
async function loadCostCoverage() {
  const gen = loadGen;
  const p = new URLSearchParams();
  if (getBranch())    p.set('branch', getBranch());
  if (getMonthFrom()) p.set('month_from', getMonthFrom());
  if (getMonthTo())   p.set('month_to', getMonthTo());
  let d;
  try { d = await getJSON('/api/cost/uncosted-coverage?' + p.toString(), { gen }); }
  catch(e){ if (e === STALE) return; throw e; }
  if (gen !== loadGen) return;
  const ch = d.chain || {};
  document.getElementById('cov-kpis').innerHTML = [
    ['รายได้รวม (ex VAT)', fmt(ch.total_revenue), '#4a70b0'],
    ['มีต้นทุนแล้ว',        fmt(ch.costed_revenue), '#10b981'],
    ['ยังไม่มีต้นทุน',       fmt(ch.uncosted_revenue), '#ef4444'],
    ['% ยังไม่มีต้นทุน',     (ch.pct_uncosted != null ? ch.pct_uncosted.toFixed(1)+'%' : '—'), '#f59e0b'],
  ].map(([l,v,c]) => `<div class="card p-4" style="border-top:3px solid ${c}">
      <div class="text-xs text-slate-500">${esc(l)}</div>
      <div class="text-2xl font-bold" style="color:${textInk(c)}">${v}</div></div>`).join('');
  const tr = d.monthly_trend || [];
  mkChart('cov-trend', {
    type:'line',
    data:{ labels: tr.map(r=>r.period), datasets:[{
      label:'% ไม่มีต้นทุน', data: tr.map(r=>r.pct_uncosted),
      borderColor:'#ef4444', backgroundColor:'rgba(239,68,68,.12)', fill:true }]},
    options:{ responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{display:false}, datalabels:{display:false} },
      scales:{ y:{ beginAtZero:true, ticks:{ callback:v=>v+'%' } } } }
  });
  document.getElementById('cov-branches').innerHTML = (d.branches||[]).map(b=>`
    <tr class="border-b border-slate-50">
      <td class="py-1.5 pr-3">${esc(b.branch)}</td>
      <td class="py-1.5 pr-3 text-right">${fmt(b.total_revenue)}</td>
      <td class="py-1.5 pr-3 text-right" style="color:var(--rose-ink)">${fmt(b.uncosted_revenue)}</td>
      <td class="py-1.5 pr-3 text-right font-semibold">${(b.pct_uncosted??0).toFixed(1)}%</td>
      <td class="py-1.5 text-right">${b.uncosted_skus}</td>
    </tr>`).join('') || '<tr><td colspan="5" class="py-3 text-slate-400 text-center">—</td></tr>';
  // No-cost fixer worklist — editable unit_cost inputs
  renderUncostedFixer(d.top_uncosted || []);
  // Top cost-driver ingredients (grouped families) + peak hours
  loadCoverageExtras();
  loadCostReview();
}

// Render the editable "fill missing cost" worklist into #cov-fixer.
function renderUncostedFixer(rows) {
  const tb = document.getElementById('cov-fixer');
  if (!tb) return;
  document.getElementById('cov-fixer-msg').textContent = '';
  tb.innerHTML = rows.map(r => {
    const price = (r.qty > 0) ? (r.revenue / r.qty) : 0;          // ex-VAT avg selling price = ceiling for cost
    const sku   = jsAttr(r.sku_code);
    return `<tr data-sku="${esc(r.sku_code)}" data-price="${price.toFixed(2)}" class="border-b border-slate-50">
      <td class="py-1.5 pr-2 font-mono text-xs text-slate-500">${esc(r.sku_code)}</td>
      <td class="py-1.5 pr-2">${esc((r.description||'').replace(/^\((?:A|B|T)\)\s*/,''))}</td>
      <td class="py-1.5 pr-2 text-xs text-slate-400">${esc(r.category||'')}</td>
      <td class="py-1.5 pr-2 text-right">${fmt(r.revenue)}</td>
      <td class="py-1.5 pr-2 text-right text-slate-400">${fmtN(r.qty)}</td>
      <td class="py-1.5 pr-2 text-right text-slate-500">${price>0?fmtN(price):'—'}</td>
      <td class="py-1.5 pr-2 text-right">
        <input type="number" min="0" step="0.01" placeholder="0.00"
               class="cov-cost-inp w-24 text-right border border-slate-200 rounded px-2 py-1 text-sm"
               onkeydown="if(event.key==='Enter')saveUncostedRow('${sku}')"></td>
      <td class="py-1.5 text-center"><button onclick="saveUncostedRow('${sku}')"
            class="text-xs px-2 py-1 rounded border border-indigo-200 text-indigo-600 hover:bg-indigo-50">${ICON.save}</button></td>
    </tr>`;
  }).join('') || '<tr><td colspan="8" class="py-4 text-center text-emerald-600">ไม่มี SKU ค้างต้นทุนในช่วงนี้</td></tr>';
}

// POST one or more {sku_code, unit_cost} to /api/cost, then surface result + reload.
async function _postCosts(entries, msgEl) {
  try {
    const res = await fetch(`${API}/api/cost`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(entries),
    });
    const out = await res.json();
    if (!res.ok) { msgEl.innerHTML = `<span class="text-red-500">บันทึกไม่สำเร็จ: ${esc(out.detail||res.status)}</span>`; return out; }
    return out;
  } catch(e){ msgEl.innerHTML = `<span class="text-red-500">บันทึกไม่สำเร็จ: ${esc(String(e.message||e))}</span>`; return null; }
}

async function saveUncostedRow(skuCode) {
  const msg = document.getElementById('cov-fixer-msg');
  const row = document.querySelector(`#cov-fixer tr[data-sku="${CSS.escape(skuCode)}"]`);
  if (!row) return;
  const inp = row.querySelector('.cov-cost-inp');
  const val = parseFloat(inp.value);
  if (isNaN(val) || val < 0) { msg.innerHTML = '<span class="text-amber-600">กรอกต้นทุนเป็นตัวเลข ≥ 0 ก่อน</span>'; inp.focus(); return; }
  const out = await _postCosts([{ sku_code: skuCode, unit_cost: val }], msg);
  if (!out) return;
  if (out.skipped_price_as_cost) {
    msg.innerHTML = `<span class="text-amber-600">⚠ ${esc(skuCode)}: ค่าที่ใส่ใกล้เคียง "ราคาขาย×1.07" — น่าจะใส่ราคาขายแทนต้นทุน กรุณาใส่ต้นทุนจริง</span>`;
    inp.classList.add('border-amber-400'); return;
  }
  if (out.updated > 0) {
    row.style.background = '#ecfdf5';
    row.querySelector('button').textContent = '✓';
    msg.innerHTML = `<span class="text-emerald-600">บันทึก ${esc(skuCode)} = ${val} แล้ว · กำลังรีเฟรช…</span>`;
    loadCostCoverage();
  }
}

async function saveAllUncosted() {
  const msg = document.getElementById('cov-fixer-msg');
  const entries = [];
  document.querySelectorAll('#cov-fixer tr[data-sku]').forEach(row => {
    const v = parseFloat(row.querySelector('.cov-cost-inp')?.value);
    if (!isNaN(v) && v >= 0) entries.push({ sku_code: row.dataset.sku, unit_cost: v });
  });
  if (!entries.length) { msg.innerHTML = '<span class="text-amber-600">ยังไม่ได้กรอกต้นทุนช่องไหนเลย</span>'; return; }
  const out = await _postCosts(entries, msg);
  if (!out) return;
  let txt = `บันทึก ${out.updated||0} รายการแล้ว`;
  if (out.skipped_price_as_cost) txt += ` · ข้าม ${out.skipped_price_as_cost} รายการ (ค่าดูเหมือนราคาขาย ไม่ใช่ต้นทุน)`;
  msg.innerHTML = `<span class="${out.skipped_price_as_cost?'text-amber-600':'text-emerald-600'}">${esc(txt)} · กำลังรีเฟรช…</span>`;
  loadCostCoverage();
}

async function loadCoverageExtras() {
  const ip = new URLSearchParams({ group_family:'true', limit:'15' });
  if (getMonthFrom()) ip.set('month_from', getMonthFrom());
  if (getMonthTo())   ip.set('month_to', getMonthTo());
  if (getBranch())    ip.set('branch', getBranch());
  api(`${API}/api/bom/consumption-analysis?${ip}`).then(d=>{
    const ings = d.ingredients||[];
    document.getElementById('cov-ingredients').innerHTML = ings.map((r,i)=>`
      <tr class="border-b border-slate-50">
        <td class="py-1.5 pr-3 text-slate-400">${i+1}</td>
        <td class="py-1.5 pr-3">${esc(r.name)}</td>
        <td class="py-1.5 pr-3 text-right text-red-500 font-medium">${fmtN(r.total_cost)}</td>
        <td class="py-1.5 text-right font-semibold">${(r.pct||0).toFixed(1)}%</td>
      </tr>`).join('') || '<tr><td colspan="4" class="py-3 text-center text-slate-400">—</td></tr>';
  }).catch(()=>{});

  const hp = new URLSearchParams();
  if (getBranch())    hp.set('branch', getBranch());
  if (getMonthFrom()) hp.set('month_from', getMonthFrom());
  if (getMonthTo())   hp.set('month_to', getMonthTo());
  api(`${API}/api/analysis/hourly_open?${hp}`).then(d=>{
    const all = (d.hourly||d||[]).filter(h=>h.hour!=null);
    // ตัดเวลาร้านปิดออก: ใช้ closed_hours config ถ้ามี ไม่งั้น fallback ตัดชั่วโมงที่รายได้ = 0
    const hasClosed = all.some(h=>h.is_open===false);
    const hrs = all.filter(h => hasClosed ? h.is_open!==false : (h.avg_daily_revenue ?? h.revenue ?? 0) > 0);
    const rev = hrs.map(h=>h.avg_daily_revenue ?? h.revenue ?? 0);
    const mx = Math.max(...rev, 1);
    mkChart('cov-peak', { type:'bar',
      data:{ labels: hrs.map(h=>h.hour+':00'),
        datasets:[{ data: rev, backgroundColor: hrs.map((h,i)=> rev[i]===mx?'#ef4444':colorForHour(h.hour)), borderRadius:3 }] },
      options:{ responsive:true, maintainAspectRatio:false,
        plugins:{ legend:{display:false}, datalabels:{display:false},
          tooltip:{callbacks:{label:ctx=>fmtN(ctx.raw)+' avg/hr'}} },
        scales:{ x:{ticks:{font:{size:9}}}, y:{ticks:{callback:v=>fmt(v)}} } } });
  }).catch(()=>{});
}

// Cost review — the kitchen confirms what the files only proposed.
// Every row is a dish costed by an estimate or by a recipe whose GRAMS were
// modelled on a sibling dish. Confirming stores an intent server-side that
// survives the daily DB swap (see cost_sources.apply_overrides).
let _covReview = [];
const _CR_BADGE = {
  'estimate':    ['ประมาณการ', '#b45309', '#fef3c7'],
  'recipe-file': ['สูตรเสนอ',  '#1d4ed8', '#dbeafe'],
  'confirmed':   ['ยืนยันแล้ว', '#047857', '#d1fae5'],
};
async function loadCostReview() {
  const body = document.getElementById('cov-review');
  if (!body) return;
  let d;
  try { d = await api(`${API}/api/cost/review`); }
  catch (e) { body.innerHTML = '<tr><td colspan="6" class="py-3 text-center text-slate-400">โหลดไม่ได้</td></tr>'; return; }
  _covReview = d.items || [];
  const s = d.summary || {};
  const sum = document.getElementById('cov-review-sum');
  if (sum) sum.innerHTML = `รอตรวจ <b style="color:#b45309">${s.pending || 0}</b> เมนู (฿${fmtN(s.pending_cogs_month || 0)}/เดือน) · ยืนยันแล้ว <b style="color:var(--good-ink)">${s.confirmed || 0}</b> · เดือน ${esc(d.month || '-')}`;
  body.innerHTML = _covReview.map((r, i) => {
    const [lab, fg, bg] = _CR_BADGE[r.source] || [r.source, '#475569', '#e2e8f0'];
    return `<tr class="border-b border-slate-50 cursor-pointer hover:bg-slate-50" onclick="toggleCostReview(${i})">
      <td class="py-1.5 pr-2">${esc(r.description)}<div class="text-[10px] text-slate-400">${esc(r.sku_code)}</div></td>
      <td class="py-1.5 pr-2"><span class="text-[10px] font-semibold px-1.5 py-0.5 rounded" style="color:${fg};background:${bg}">${lab}</span></td>
      <td class="py-1.5 pr-2 text-right font-semibold tabular-nums">฿${(r.cost || 0).toFixed(2)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${fmtN(r.plates || 0)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">฿${fmtN(r.cogs_month || 0)}</td>
      <td class="py-1.5 text-center text-slate-400">▾</td></tr>
      <tr id="cov-rv-${i}" style="display:none"><td colspan="6" class="pb-3"></td></tr>`;
  }).join('') || '<tr><td colspan="6" class="py-3 text-center text-slate-400">ไม่มีเมนูที่รอตรวจ</td></tr>';
}

function toggleCostReview(i) {
  const row = document.getElementById('cov-rv-' + i);
  if (!row) return;
  if (row.style.display !== 'none') { row.style.display = 'none'; return; }
  row.style.display = '';
  _renderCostReviewEditor(i);
}

function _renderCostReviewEditor(i) {
  const r = _covReview[i], cell = document.querySelector(`#cov-rv-${i} td`);
  if (!r || !cell) return;
  const inp = 'border border-slate-200 rounded px-1.5 py-0.5 text-xs text-right tabular-nums';
  const lines = (r.lines || []).map((ln, k) => `
    <tr data-k="${k}">
      <td class="py-1 pr-2 text-xs">${ln._new
        ? `<input data-f="name" class="border border-slate-200 rounded px-1.5 py-0.5 text-xs" style="width:100%" placeholder="ชื่อวัตถุดิบ" value="${esc(ln.name || '')}">`
        : `${esc(ln.name || '')}<span class="text-[10px] text-slate-400"> ${esc(ln.code || '')}</span>`}</td>
      <td class="py-1 pr-2 text-right"><input class="${inp}" style="width:70px" type="number" step="any" min="0" value="${ln.qty ?? ''}" oninput="_crRecalc(${i})" data-f="qty"> <span class="text-[10px] text-slate-400">${esc(ln.unit || '')}</span></td>
      <td class="py-1 pr-2 text-right"><input class="${inp}" style="width:82px" type="number" step="any" min="0" value="${ln.price ?? ''}" oninput="_crRecalc(${i})" data-f="price"></td>
      <td class="py-1 pr-2 text-right text-xs tabular-nums" data-f="cost">฿${((ln.qty || 0) * (ln.price || 0)).toFixed(2)}</td>
      <td class="py-1 text-center"><button class="text-xs text-rose-500" title="ลบบรรทัดนี้" onclick="event.stopPropagation();_crDelLine(${i},${k})">✕</button></td>
    </tr>`).join('');
  const hasRecipe = (r.lines || []).length > 0;
  cell.innerHTML = `<div class="rounded-lg p-3" style="background:#f8fafc;border:1px solid #e2e8f0" onclick="event.stopPropagation()">
    ${r.note ? `<p class="text-[11px] text-slate-500 mb-2">ที่มา: ${esc(r.note)}</p>` : ''}
    ${hasRecipe ? `<table class="w-full mb-2"><thead><tr class="text-left text-[10px] text-slate-400 border-b">
        <th class="py-1 pr-2">วัตถุดิบ</th><th class="py-1 pr-2 text-right">ปริมาณ/ที่</th><th class="py-1 pr-2 text-right">ราคา/หน่วย (฿)</th><th class="py-1 pr-2 text-right">ต้นทุน</th><th></th></tr></thead>
        <tbody id="cov-rv-lines-${i}">${lines}</tbody></table>
        <button class="text-[11px] text-indigo-600 mb-2" onclick="_crAddLine(${i})">+ เพิ่มวัตถุดิบ</button>`
      : `<div class="flex items-center gap-2 mb-2 text-xs">ต้นทุนต่อที่ (฿)
          <input id="cov-rv-flat-${i}" class="${inp}" style="width:90px" type="number" step="any" min="0" value="${r.cost || ''}"></div>`}
    <div class="flex items-center flex-wrap gap-2">
      <input id="cov-rv-note-${i}" class="border border-slate-200 rounded px-2 py-1 text-xs" style="flex:1;min-width:160px" placeholder="หมายเหตุ (เช่น ชั่งจริงที่สาขา…)">
      ${hasRecipe ? `<span class="text-xs">รวม <b id="cov-rv-total-${i}" class="tabular-nums">฿${(r.cost || 0).toFixed(2)}</b></span>` : ''}
      <button class="text-xs px-3 py-1.5 rounded-lg font-semibold" style="background:#059669;color:#fff" onclick="confirmCostReview(${i})">ยืนยัน</button>
      ${r.source === 'confirmed' ? `<button class="text-xs px-3 py-1.5 rounded-lg border border-slate-300" onclick="withdrawCostReview(${i})">ถอนการยืนยัน</button>` : ''}
    </div></div>`;
  if (hasRecipe) _crRecalc(i);
}

// read the editor back into plain line objects (what the API takes)
function _crLines(i) {
  return [...document.querySelectorAll(`#cov-rv-lines-${i} tr`)].map(tr => {
    const base = (_covReview[i].lines || [])[+tr.dataset.k] || {};
    const num = f => parseFloat(tr.querySelector(`input[data-f="${f}"]`)?.value) || 0;
    const nameInp = tr.querySelector('input[data-f="name"]');
    return { code: base.code || '', name: nameInp ? nameInp.value.trim() : (base.name || ''),
             qty: num('qty'), unit: base.unit || 'กรัม', price: num('price'),
             type: base.type || null, _new: !!base._new, _tr: tr };
  });
}
function _crRecalc(i) {
  let tot = 0;
  _crLines(i).forEach(ln => {
    const c = ln.qty * ln.price; tot += c;
    const el = ln._tr.querySelector('td[data-f="cost"]'); if (el) el.textContent = '฿' + c.toFixed(2);
  });
  const t = document.getElementById('cov-rv-total-' + i); if (t) t.textContent = '฿' + tot.toFixed(2);
}
function _crDelLine(i, k) {
  _covReview[i].lines = _crLines(i).filter(ln => +ln._tr.dataset.k !== k).map(({ _tr, ...ln }) => ln);
  _renderCostReviewEditor(i);
}
function _crAddLine(i) {
  _covReview[i].lines = _crLines(i).map(({ _tr, ...ln }) => ln);
  _covReview[i].lines.push({ code: '', name: '', qty: 0, unit: 'กรัม', price: 0, type: null, _new: true });
  _renderCostReviewEditor(i);
}

async function confirmCostReview(i) {
  const r = _covReview[i];
  const note = document.getElementById('cov-rv-note-' + i)?.value || '';
  let body;
  if ((r.lines || []).length) {
    const lines = _crLines(i).map(({ _tr, _new, ...ln }) => ln).filter(ln => ln.qty > 0 && ln.price > 0 && ln.name);
    if (!lines.length) { showToast('ต้องมีวัตถุดิบอย่างน้อย 1 บรรทัดที่มีปริมาณและราคา', 'error'); return; }
    body = { sku_code: r.sku_code, kind: 'recipe', lines, note };
  } else {
    const c = parseFloat(document.getElementById('cov-rv-flat-' + i)?.value);
    if (!(c > 0)) { showToast('กรอกต้นทุนต่อที่ให้มากกว่า 0', 'error'); return; }
    body = { sku_code: r.sku_code, kind: 'cost', unit_cost: c, note };
  }
  try {
    const res = await fetch(`${API}/api/cost/confirm`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(j.detail || ('HTTP ' + res.status));
    showToast(`ยืนยัน ${r.description} แล้ว — ฿${(j.cost || 0).toFixed(2)}/ที่`, 'success');
    await loadCostReview();
  } catch (e) { showToast('ยืนยันไม่สำเร็จ: ' + e.message, 'error'); }
}

async function withdrawCostReview(i) {
  const r = _covReview[i];
  try {
    const res = await fetch(`${API}/api/cost/confirm/${encodeURIComponent(r.sku_code)}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    showToast(`ถอนการยืนยัน ${r.description} แล้ว — กลับไปใช้ค่าที่ระบบเสนอ`, 'info');
    await loadCostReview();
  } catch (e) { showToast('ถอนไม่สำเร็จ: ' + e.message, 'error'); }
}

// Prime Cost = Food% + Labor% per branch (the biggest controllable costs).
let _primeKey = null;
async function loadPrimeCost() {
  const msel = document.getElementById('pnlMonthSel');
  const month = (msel && msel.value) || getMonthTo() || getMonthFrom() || '';
  const _fk = month + '|' + (getGroup() || '');
  if (_primeKey === _fk) return; _primeKey = _fk;
  const p = new URLSearchParams();
  if (month) p.set('month', month);
  if (getGroup()) p.set('group', getGroup());
  let d; try { d = await api(`${API}/api/finance/prime-cost?${p}`); } catch (e) { return; }
  const ch = d.chain || {}; const band = d.band || { good: 70, high: 78 };
  const rows = d.branches || [];
  const pcCol = pp => pp == null ? '#94a3b8' : pp > band.high ? '#ef4444' : pp > band.good ? '#f59e0b' : '#10b981';
  const cw = document.getElementById('primeChain');
  if (cw) cw.textContent = `เดือน ${d.month || '—'} · เฉลี่ยเครือ Food ${ch.food_pct != null ? ch.food_pct + '%' : '—'}${ch.labor_pct != null ? ' + Labor ' + ch.labor_pct + '% = Prime ' + ch.prime_pct + '%' : ''}`;
  const bn = document.getElementById('primeBanner');
  if (bn) {
    if (!ch.branches_with_labor) {
      bn.innerHTML = `<div class="card p-3 text-xs" style="background:#fffbeb;border:1px solid #f59e0b">ยังไม่ได้ใส่ <b>ค่าแรง</b> — Food% แสดงจริงแล้ว แต่ <b>Prime%/Labor%</b> จะว่างจนกว่าจะกรอก · ไปที่ <button onclick="switchTab('settings');switchSettingsTab('fixedcosts')" class="underline font-semibold" style="color:#92400e">Settings → ค่าแรง + ค่าใช้จ่ายคงที่</button> (กรอกครั้งเดียว ~27 ช่อง)</div>`;
    } else if (ch.branches_with_labor < ch.branch_count) {
      bn.innerHTML = `<div class="text-xs" style="color:var(--text-muted)">ใส่ค่าแรงแล้ว ${ch.branches_with_labor}/${ch.branch_count} สาขา — ที่เหลือแสดงเฉพาะ Food%</div>`;
    } else { bn.innerHTML = ''; }
  }
  document.getElementById('primeBody').innerHTML = rows.map(r => `<tr class="border-t border-slate-100 hover:bg-indigo-50/40">
    <td class="py-1.5 pr-2 font-medium">${esc(r.branch)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${fmt(r.rev)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums" style="color:${r.food_pct > 55 ? 'var(--bad-ink)' : r.food_pct > 50 ? '#f59e0b' : 'var(--good-ink)'}">${r.food_pct != null ? r.food_pct + '%' : '—'}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${r.labor_pct != null ? r.labor_pct + '%' : '<span class="text-slate-300">—</span>'}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums font-bold" style="color:${textInk(pcCol(r.prime_pct))}">${r.prime_pct != null ? r.prime_pct + '%' : '<span class="text-slate-300">—</span>'}</td>
    <td class="py-1.5 text-right tabular-nums" style="color:${r.operating_pct == null ? '#94a3b8' : r.operating_pct < 0 ? 'var(--bad-ink)' : 'var(--good-ink)'}">${r.operating_pct != null ? r.operating_pct + '%' : '—'}</td>
  </tr>`).join('') || '<tr><td colspan="6" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
}

// ── Branch P&L tab (Analysis #1, BoM-based COGS) ───────────────
function _pnlFillMonths() {
  // populate the month dropdown once (from the global selMonthTo options)
  const msel = document.getElementById('pnlMonthSel');
  if (msel && msel.options.length <= 1) {
    const src = document.getElementById('selMonthTo');
    if (src) [...src.options].filter(o=>o.value).forEach(o=>{
      const opt=document.createElement('option'); opt.value=o.value; opt.textContent=o.textContent; msel.appendChild(opt);
    });
  }
}
async function loadPnlBranch() {
  loadPrimeCost();
  const gen = loadGen;
  _pnlFillMonths();
  const msel = document.getElementById('pnlMonthSel');
  const pnlMonth = msel ? msel.value : '';
  const p = new URLSearchParams();
  if (getBranch())    p.set('branch', getBranch());
  if (pnlMonth) { p.set('month_from', pnlMonth); p.set('month_to', pnlMonth); }
  else {
    if (getMonthFrom()) p.set('month_from', getMonthFrom());
    if (getMonthTo())   p.set('month_to', getMonthTo());
  }
  _pnlActiveMonth = pnlMonth || getMonthTo() || getMonthFrom() || '';
  let d;
  try { d = await getJSON('/api/pnl/branch?' + p.toString(), { gen }); }
  catch(e){ if (e === STALE) return; throw e; }
  if (gen !== loadGen) return;
  const c = d.chain || {};
  document.getElementById('pnl-kpis').innerHTML = [
    ['รายได้ (ex VAT)',      fmt(c.revenue),          '#4a70b0'],
    ['ต้นทุน BoM',           fmt(c.cogs),             '#f59e0b'],
    ['กำไรขั้นต้น',          fmt(c.gross_profit) + (c.gp_margin_pct!=null?` · ${c.gp_margin_pct}%`:''), '#10b981'],
    ['กำไรดำเนินงาน',        fmt(c.operating_profit) + (c.op_margin_pct!=null?` · ${c.op_margin_pct}%`:''), (c.operating_profit>=0?'#0891b2':'#ef4444')],
  ].map(([l,v,col]) => `<div class="card p-4" style="border-top:3px solid ${col}">
      <div class="text-xs text-slate-500">${esc(l)}</div>
      <div class="text-xl font-bold" style="color:${textInk(col)}">${v}</div></div>`).join('');
  const note = document.getElementById('pnl-note');
  if (!c.fixed_costs_set || (c.fixed_cost_period||0) === 0) {
    note.classList.remove('hidden');
    note.textContent = '⚠ ยังไม่ได้ตั้งค่าใช้จ่ายคงที่ (rent/labor/utilities) → กำไรดำเนินงาน = กำไรขั้นต้น. ตั้งค่าได้ที่ ตั้งค่า → ค่าใช้จ่ายคงที่รายสาขา';
  } else { note.classList.add('hidden'); }
  _pnlBranchRows = {}; (d.branches||[]).forEach(b => { _pnlBranchRows[b.branch] = b; });
  document.getElementById('pnl-branches').innerHTML = (d.branches||[]).map(b => {
    const opCol = (b.operating_profit>=0) ? '#0891b2' : '#e11d48';
    return `<tr class="border-b border-slate-50 hover:bg-indigo-50">
      <td class="py-1.5 pr-3 font-medium"><a onclick="openPnlDrill('${jsAttr(b.branch)}')" class="text-indigo-600 hover:underline cursor-pointer">${esc(b.branch)}</a></td>
      <td class="py-1.5 pr-3 text-right">${fmt(b.revenue)}</td>
      <td class="py-1.5 pr-3 text-right" style="color:#b45309">${fmt(b.cogs)}</td>
      <td class="py-1.5 pr-3 text-right" style="color:var(--good-ink)">${fmt(b.gross_profit)}</td>
      <td class="py-1.5 pr-3 text-right">${b.gp_margin_pct!=null?b.gp_margin_pct+'%':'—'}</td>
      <td class="py-1.5 pr-3 text-right text-slate-500">${b.fixed_cost_period?fmt(b.fixed_cost_period):'—'}</td>
      <td class="py-1.5 pr-3 text-right font-semibold" style="color:${textInk(opCol)}">${fmt(b.operating_profit)}</td>
      <td class="py-1.5 pr-3 text-right" style="color:${textInk(opCol)}">${b.op_margin_pct!=null?b.op_margin_pct+'%':'—'}</td>
      <td class="py-1.5 pr-3 text-right text-slate-500">${fmtN(b.covers)}</td>
      <td class="py-1.5 text-right text-slate-500">${b.breakeven_covers?fmtN(b.breakeven_covers):'—'}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="10" class="py-3 text-slate-400 text-center">—</td></tr>';
}

// ── P&L branch×month drill-down (click a branch name) ──────────────────────
let _pnlActiveMonth = '';
let _pnlBranchRows = {};

// Full P&L waterfall (Revenue → COGS → GP → Fixed → Operating Profit) from a branch row
function _pnlWaterfallHtml(b, scopeLabel) {
  if (!b) return '';
  const rev = b.revenue || 0;
  const pct = v => rev ? (v / rev * 100) : 0;
  const fc  = b.fixed_cost_period || 0;
  const row = (label, val, color, opts={}) => {
    const w = Math.max(0, Math.min(100, Math.abs(pct(val))));
    const sign = opts.minus ? '−' : (opts.eq ? '=' : '');
    return `<div class="flex items-center gap-2 py-1 ${opts.bold?'font-bold':''}">
      <span class="w-28 text-xs ${opts.bold?'text-slate-800':'text-slate-500'}">${sign} ${label}</span>
      <div class="flex-1 bg-slate-100 rounded-full h-3"><div style="width:${w}%;background:${color};height:100%;border-radius:9999px"></div></div>
      <span class="w-28 text-right text-sm" style="color:${textInk(color)}">${fmtN(val)}</span>
      <span class="w-12 text-right text-xs text-slate-400">${pct(val).toFixed(0)}%</span>
    </div>`;
  };
  const opCol = (b.operating_profit||0) >= 0 ? '#0891b2' : '#e11d48';
  const beCovers = b.breakeven_covers ? `จุดคุ้มทุน ~${fmtN(b.breakeven_covers)} ที่นั่ง/ลูกค้า (จริง ${fmtN(b.covers||0)})` : '';
  return `<div class="card p-4 mb-4">
    <h4 class="text-sm font-bold text-slate-700 mb-2">งบกำไรขาดทุน (P&L Waterfall) <span class="font-normal text-xs text-slate-400">· ${esc(scopeLabel||'')}</span></h4>
    ${row('รายได้ (ex-VAT)', rev, '#4a70b0', {bold:true})}
    ${row('ต้นทุน BoM', b.cogs||0, '#f59e0b', {minus:true})}
    ${row('กำไรขั้นต้น', b.gross_profit||0, '#10b981', {eq:true, bold:true})}
    ${row('ค่าใช้จ่ายคงที่', fc, '#a855f7', {minus:true})}
    ${row('กำไรดำเนินงาน', b.operating_profit||0, opCol, {eq:true, bold:true})}
    <p class="text-[11px] text-slate-400 mt-2">${beCovers}${b.fixed_cost_period? '' : ' · ยังไม่ได้ตั้งค่าใช้จ่ายคงที่ → OP = GP'}</p>
  </div>`;
}

function closePnlDrill() { const m = document.getElementById('pnlDrillModal'); if (m) { m.style.display = 'none'; m.classList.add('hidden'); } }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closePnlDrill(); });

async function openPnlDrill(branch) {
  const modal = document.getElementById('pnlDrillModal');
  const body  = document.getElementById('pnlDrillBody');
  const title = document.getElementById('pnlDrillTitle');
  // pick the month: tab dropdown → range bounds → else fall back to the latest
  // month available in the dropdown (avoids a dead-end in All-Time / "ทุกเดือน" mode)
  let month = _pnlActiveMonth || getMonthTo() || getMonthFrom();
  if (!month) {
    const msel = document.getElementById('pnlMonthSel');
    if (msel) month = [...msel.options].map(o => o.value).find(v => v) || '';
  }
  if (!month) { alert('ยังไม่มีข้อมูลเดือนให้แสดง — โหลดข้อมูลก่อน'); return; }
  title.textContent = `${branch} · ${typeof fmtPeriod==='function'?fmtPeriod(month):month}`;
  body.innerHTML = '<p class="text-sm text-slate-400 py-8 text-center">กำลังโหลด…</p>';
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  let d;
  try {
    const r = await fetch(`${API}/api/pnl/branch-month-detail?branch=${encodeURIComponent(branch)}&month=${month}&limit=40`);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    d = await r.json();
  } catch(e){ body.innerHTML = '<p class="text-sm text-red-500 py-6 text-center">โหลดไม่ได้: ' + esc(String(e.message||e)) + '</p>'; return; }
  const s = d.summary || {};
  const revDelta = s.rev_delta_pct;
  const deltaChip = revDelta==null ? '' :
    `<span style="color:${revDelta>=0?'var(--good-ink)':'var(--bad-ink)'}">${revDelta>=0?'▲':'▼'} ${Math.abs(revDelta)}% vs ${fmtPeriod(d.prev_month)}</span>`;
  const kpi = (l,v,c)=>`<div class="card p-3" style="border-top:3px solid ${c}"><div class="text-[11px] text-slate-500">${l}</div><div class="text-lg font-bold" style="color:${textInk(c)}">${v}</div></div>`;
  const costRows = (d.cost_breakdown||[]).slice(0,40).map((r,i)=>`
    <tr class="border-b border-slate-50">
      <td class="py-1 pr-2 text-slate-400">${i+1}</td>
      <td class="py-1 pr-2">${esc((r.description||'').replace(/^\((?:A|B)\)\s*/,''))}</td>
      <td class="py-1 pr-2 text-right">${fmtN(r.servings)}</td>
      <td class="py-1 pr-2 text-right text-red-500">${fmtN(r.cogs)}</td>
      <td class="py-1 text-right font-semibold">${r.cost_pct}%</td>
    </tr>`).join('');
  const ingRows = (d.top_ingredients||[]).map((r,i)=>`
    <tr class="border-b border-slate-50"><td class="py-1 pr-2 text-slate-400">${i+1}</td>
    <td class="py-1 pr-2">${esc(r.name)}</td><td class="py-1 pr-2 text-right text-red-500">${fmtN(r.cost)}</td>
    <td class="py-1 text-right font-semibold">${r.pct}%</td></tr>`).join('');
  const moverRow = (m,up)=>{
    const pct = m.is_new?'ใหม่':(m.delta_pct==null?'—':(m.delta_pct>0?'+':'')+m.delta_pct+'%');
    return `<div class="flex justify-between gap-2 py-0.5">
      <span class="truncate">${esc((m.description||'').replace(/^\((?:A|B)\)\s*/,''))}</span>
      <span class="flex-shrink-0 font-semibold" style="color:${up?'var(--good-ink)':'var(--bad-ink)'}">${up?'▲':'▼'}${fmtN(Math.abs(m.delta))} (${pct})</span>
    </div>`;
  };
  body.innerHTML = `
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
      ${kpi('รายได้ '+deltaChip, fmtN(s.revenue), '#4a70b0')}
      ${kpi('ต้นทุน BoM', fmtN(s.cogs), '#f59e0b')}
      ${kpi('กำไรขั้นต้น', fmtN(s.gross_profit)+' · '+(s.gp_margin_pct??'—')+'%', '#10b981')}
      ${kpi('Food Cost %', (s.food_cost_pct??'—')+'%', '#ef4444')}
    </div>
    ${_pnlWaterfallHtml(_pnlBranchRows[branch], (function(){ const ps=document.getElementById('pnlMonthSel')?.value; return ps?fmtPeriod(ps):(getMonthFrom()?`${fmtMonth(getMonthFrom())}–${fmtMonth(getMonthTo()||getMonthFrom())}`:'ทุกเดือนรวม'); })())}
    <div class="card p-4 mb-4">
      <h4 class="text-sm font-bold text-slate-700 mb-2">แนวโน้มกำไรรายเดือน (ทุกเดือนของสาขานี้)</h4>
      <div style="height:200px"><canvas id="pnlDrillTrend"></canvas></div>
    </div>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
      <div>
        <h4 class="text-sm font-semibold text-slate-700 mb-2">ต้นทุน BoM ประกอบด้วยเมนู (Top 40 · % ของต้นทุนเดือนนี้)</h4>
        <div style="max-height:340px;overflow:auto"><table class="w-full text-xs">
          <thead class="sticky top-0 bg-white"><tr class="text-left text-slate-400 border-b">
            <th class="py-1 pr-2">#</th><th class="py-1 pr-2">เมนู</th><th class="py-1 pr-2 text-right">เสิร์ฟ</th>
            <th class="py-1 pr-2 text-right">ต้นทุน</th><th class="py-1 text-right">%</th></tr></thead>
          <tbody>${costRows||'<tr><td colspan=5 class="py-3 text-center text-slate-400">—</td></tr>'}</tbody>
        </table></div>
      </div>
      <div>
        <h4 class="text-sm font-semibold text-slate-700 mb-2">วัตถุดิบหลักที่ขับต้นทุน</h4>
        <div style="max-height:150px;overflow:auto;margin-bottom:14px"><table class="w-full text-xs">
          <tbody>${ingRows||'<tr><td class="py-3 text-center text-slate-400">—</td></tr>'}</tbody></table></div>
        <h4 class="text-sm font-semibold text-emerald-700 mb-2">เมนูคนนิยมมากขึ้น (vs ${fmtPeriod(d.prev_month)})</h4>
        <div class="text-xs mb-3">${(d.rising_dishes||[]).slice(0,8).map(m=>moverRow(m,true)).join('')||'<span class="text-slate-400">—</span>'}</div>
        <h4 class="text-sm font-semibold text-red-600 mb-2">เมนูคนนิยมน้อยลง</h4>
        <div class="text-xs">${(d.falling_dishes||[]).slice(0,6).map(m=>moverRow(m,false)).join('')||'<span class="text-slate-400">—</span>'}</div>
      </div>
    </div>
    <p class="text-[11px] text-slate-400 mt-4">รวม ${s.dish_count||0} เมนูในเดือนนี้ · ต้นทุนใช้สูตร BoM เดียวกับทุกหน้า</p>`;

  // Monthly profit trend for this branch (all months)
  try {
    const mt = await api(`${API}/api/pnl/monthly?branch=${encodeURIComponent(branch)}`).catch(() => []);
    const arr = (Array.isArray(mt) ? mt : []).sort((a,b)=>(a.period_month||'').localeCompare(b.period_month||''));
    if (arr.length) mkChart('pnlDrillTrend', {
      type: 'bar',
      data: {
        labels: arr.map(r => fmtMonth(r.period_month)),
        datasets: [
          { label: 'รายได้', data: arr.map(r=>r.revenue||0), backgroundColor: '#4a70b033', borderColor:'#4a70b0', borderWidth:1, order:3 },
          { label: 'กำไรขั้นต้น', data: arr.map(r=>r.gross_profit||0), backgroundColor: '#10b98155', borderColor:'#10b981', borderWidth:1, order:2 },
          { label: 'GP %', data: arr.map(r=>r.gp_margin_pct||0), type:'line', borderColor:'#f59e0b', backgroundColor:'transparent', tension:0.3, yAxisID:'pct', order:1 },
        ]
      },
      options: {
        responsive:true, maintainAspectRatio:false,
        plugins:{ legend:{ position:'bottom', labels:{font:{size:10}} }, datalabels:{display:false} },
        scales:{ y:{ ticks:{ callback:v=>fmt(v) } }, pct:{ position:'right', grid:{drawOnChartArea:false}, ticks:{ callback:v=>v+'%' } } }
      }
    });
  } catch(e) {}
}

// ── More Analytics tab (#4 daypart, #5 mix-shift, #7 turnover, #9 forecast) ──
