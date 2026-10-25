// ─── 05-pnl-settings.js — slice 5/9 of the former app.js (P2 structure refactor) ───
// Sequential load order matters: index.html loads 01→09; classic scripts
// share one global scope, so this equals the original single file.

// ════════════════════════════════════════════════════════════════
// TAB: P&L DEEP DIVE
// ════════════════════════════════════════════════════════════════
let pnlDeepBranch = '';
let pnlBranchRows = [];
let pnlSortCol = 'gross_profit';
let pnlSortAsc = false;

async function loadPnlTab() {
  const params = new URLSearchParams();
  if (getBranch())    params.set('branch',     getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to',   getMonthTo());
  if (getGroup())     params.set('group',      getGroup());

  const branchParams = new URLSearchParams();
  if (getMonthFrom()) branchParams.set('month_from', getMonthFrom());
  if (getMonthTo())   branchParams.set('month_to',   getMonthTo());
  if (getGroup())     branchParams.set('group',      getGroup());

  const [summary, monthly, branchRows, pc] = await Promise.all([
    api(`${API}/api/profit/summary?${params}`),
    api(`${API}/api/pnl/monthly?${params}`),
    api(`${API}/api/pnl/branches?${branchParams}`),
    api(`${API}/api/period/cost?${gPeriodQuery()}`).catch(() => null),   // PR-PP3: the two comparisons of the top bar's period
  ]);

  renderPnlKpis(summary);
  const PC = pc && pc.cur ? pc : null, v = (o, k) => o ? o[k] : null;
  if (PC) {   // the tiles show the same windows their chips compare (to date through the last closed day)
    periodChip(PC.period, true);
    const set = (id, x) => { const el = document.getElementById(id); if (el) el.textContent = x; };
    set('pnlRev', fmtN(PC.cur.revenue || 0)); set('pnlCogs', fmtN(PC.cur.cogs || 0)); set('pnlGp', fmtN(PC.cur.gp || 0));
    set('pnlMargin', PC.cur.gp_pct != null ? PC.cur.gp_pct.toFixed(1) + '%' : '—');
  }
  [{ id: 'pnlRevC', k: 'revenue', unit: '%', up: true }, { id: 'pnlCogsC', k: 'cogs', unit: '%', up: false },
   { id: 'pnlGpC', k: 'gp', unit: '%', up: true }, { id: 'pnlMarginC', k: 'gp_pct', unit: 'pp', up: true }].forEach((f, i) => {
    const el = document.getElementById(f.id);
    if (el) el.innerHTML = PC ? periodCmpHtml(PC.cur[f.k], v(PC.prev, f.k), v(PC.ly, f.k), { unit: f.unit, upGood: f.up, range: PC.period, q: i === 0 }) : '';
  });
  renderPnlTrend(monthly);

  pnlBranchRows = branchRows;
  renderPnlBranchTable();
  populatePnlDeepBranchSelector(branchRows);

  // Bill distribution: use branch detail when branch is selected, else chain-wide estimate
  if (getBranch()) {
    api(`${API}/api/pnl/branch-detail?${params}`).then(d => {
      renderPnlBillDist(d.bill_stats, true);
    });
  } else {
    api(`${API}/api/profit/per-bill-distribution?${params}`).then(dist => {
      renderPnlBillDistChain(dist);
    });
  }

  // Reload deep dive if a branch was already selected
  if (pnlDeepBranch) loadPnlDeepDive(pnlDeepBranch);
}

function renderPnlKpis(s) {
  const cov = s.cost_coverage_pct || 0;
  const banner = document.getElementById('pnlCoverageBanner');
  const msg    = document.getElementById('pnlCoverageMsg');
  if (cov < 90 && banner) {
    banner.classList.remove('hidden');
    msg.innerHTML = cov === 0
      ? '<b>No SKU costs uploaded yet.</b> GP / Margin show 0. Upload costs via <b>Settings → Costs</b>.'
      : `<b>ต้นทุนครอบคลุม ${cov.toFixed(0)}%</b> ของรายได้ ตัวเลขกำไรนับเฉพาะเมนูที่มีต้นทุน`;
  } else if (banner) banner.classList.add('hidden');

  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  set('pnlRev',     fmtN(s.revenue_ex_vat || 0));
  set('pnlCogs',    fmtN(s.cogs || 0));
  set('pnlGp',      fmtN(s.gross_profit || 0));
  set('pnlMargin',  s.margin_pct != null ? s.margin_pct.toFixed(1) + '%' : '—');
  set('pnlPerBill', s.profit_per_bill != null ? fmtN(s.profit_per_bill) : '—');
  set('pnlBills',   fmtN(s.tx_count || 0));
}

function renderPnlTrend(monthly) {
  mkChart('chartPnlTrend', {
    type: 'bar',
    data: {
      labels: monthly.map(m => fmtPeriod(m.period_month)),
      datasets: [
        { type:'bar',  label:'รายได้ (ไม่รวม VAT)', data: monthly.map(m => m.revenue_ex_vat||0), backgroundColor:'rgba(31,58,107,0.45)', order:3 },
        { type:'bar',  label:'ต้นทุนขาย',     data: monthly.map(m => m.cogs||0),           backgroundColor:'rgba(239,68,68,0.65)', order:3 },
        { type:'line', label:'กำไรขั้นต้น',       data: monthly.map(m => m.gross_profit||0),   borderColor:'#10b981', backgroundColor:'rgba(16,185,129,0.12)', tension:0.3, fill:true, order:1 },
        { type:'line', label:'% กำไรขั้นต้น',     data: monthly.map(m => m.margin_pct),        borderColor:'#f59e0b', borderDash:[4,3], backgroundColor:'transparent', tension:0.25, yAxisID:'y1', order:0, fill:false },
      ],
    },
    options: {
      responsive:true, maintainAspectRatio:false,
      plugins:{
        legend:{ position:'bottom', labels:{ font:{size:10} } },
        datalabels:{ display:false },
        tooltip:{ callbacks:{ label: ctx => ctx.dataset.label === 'Margin %'
          ? 'Margin: ' + (ctx.raw??0).toFixed(1) + '%'
          : ctx.dataset.label + ': '+fmtN(ctx.raw||0) }},
      },
      scales:{
        x:{ ticks:{color:chartTheme().text} },
        y:{ ticks:{color:chartTheme().text, callback:v=>fmt(v)} },
        y1:{ position:'right', grid:{display:false}, ticks:{color:'#f59e0b', callback:v=>v+'%'}, min:0 },
      },
    },
  });
}

function renderPnlBillDistChain(dist) {
  const note = document.getElementById('pnlBillDistNote');
  if (!dist.buckets || dist.buckets.length === 0) {
    if (note) note.textContent = 'ยังไม่มีต้นทุนเมนู จึงยังประมาณ GP ต่อบิลไม่ได้';
    return;
  }
  if (note) note.textContent = `GP ต่อบิลโดยประมาณ · อัตรากำไรที่ใช้ ${dist.margin_pct_used}% · ${fmtN(dist.total_tx)} บิล`;
  mkChart('chartPnlBillDist', {
    type:'bar',
    data:{ labels: dist.buckets.map(b=>b.label), datasets:[{ label:'บิล', data:dist.buckets.map(b=>b.count), backgroundColor:'#7a66b8', borderRadius:3, borderWidth:0 }] },
    options:{ responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false},datalabels:{display:false},tooltip:{callbacks:{label:ctx=>fmtN(ctx.raw)+' บิล'}}},
      scales:{x:{ticks:{color:chartTheme().text}},y:{beginAtZero:true,ticks:{color:chartTheme().text}}} },
  });
}

function renderPnlBillDist(stats, isRevenue) {
  const note = document.getElementById('pnlBillDistNote');
  if (!stats || stats.tx_count === 0) {
    if (note) note.textContent = 'ช่วงนี้ยังไม่มีบิลจาก Easy POS (มีข้อมูลตั้งแต่ พ.ค. 2025)';
    return;
  }
  const buckets = isRevenue ? stats.buckets : stats.profit_buckets;
  const label   = isRevenue ? 'รายได้ต่อบิล ' : 'GP ต่อบิล (ประมาณ) ';
  if (note) note.textContent = `${fmtN(stats.tx_count)} บิล · เฉลี่ย ${fmtN(stats.avg)} · ค่ากลาง ${fmtN(stats.p50)}${stats.margin_pct_used > 0 ? ' · อัตรากำไร '+stats.margin_pct_used+'%' : ''}`;
  mkChart('chartPnlBillDist', {
    type:'bar',
    data:{ labels: buckets.map(b=>b.label+' '), datasets:[{ label, data:buckets.map(b=>b.count), backgroundColor:'#4a70b0', borderRadius:3, borderWidth:0 }] },
    options:{ responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false},datalabels:{display:false},tooltip:{callbacks:{label:ctx=>fmtN(ctx.raw)+' bills'}}},
      scales:{x:{ticks:{color:chartTheme().text,font:{size:9}}},y:{beginAtZero:true,ticks:{color:chartTheme().text}}} },
  });
}

// Branch comparison table
function renderPnlBranchTable() {
  const rows = [...pnlBranchRows].sort((a, b) => {
    const va = a[pnlSortCol] ?? (pnlSortAsc ? Infinity : -Infinity);
    const vb = b[pnlSortCol] ?? (pnlSortAsc ? Infinity : -Infinity);
    return typeof va === 'string'
      ? (pnlSortAsc ? va.localeCompare(vb) : vb.localeCompare(va))
      : (pnlSortAsc ? va - vb : vb - va);
  });
  // Update sort indicator
  ['branch','revenue_ex_vat','cogs','gross_profit','margin_pct','tx_count','avg_ticket','profit_per_bill'].forEach(c => {
    const el = document.getElementById('pnlSort' + c.charAt(0).toUpperCase() + c.slice(1));
    if (el) el.textContent = c === pnlSortCol ? (pnlSortAsc ? '▲' : '▼') : '↕';
  });
  const gpMax = Math.max(...pnlBranchRows.map(r => r.gross_profit || 0), 1);
  document.getElementById('pnlBranchBody').innerHTML = rows.map(r => {
    const gp = r.gross_profit || 0;
    const gpPct = Math.round(gp / gpMax * 100);
    const mColor = (r.margin_pct||0) > 40 ? '#10b981' : (r.margin_pct||0) > 20 ? '#f59e0b' : '#ef4444';
    return `<tr class="border-b border-slate-100 hover:bg-slate-50 cursor-pointer" onclick="clickPnlBranchRow('${jsAttr(r.branch)}')">
      <td class="py-1.5 pr-3 font-medium text-indigo-600">${esc(r.branch)}</td>
      <td class="py-1.5 pr-3 text-right">${fmtN(r.revenue_ex_vat||0)}</td>
      <td class="py-1.5 pr-3 text-right text-red-500">${fmtN(r.cogs||0)}</td>
      <td class="py-1.5 pr-3 text-right">
        <div class="flex items-center justify-end gap-1.5">
          <div class="bg-slate-100 rounded-full h-1.5 w-14 overflow-hidden hidden sm:block">
            <div class="bg-emerald-500 h-full rounded-full" style="width:${gpPct}%"></div>
          </div>
          <span class="text-emerald-600 font-semibold">${fmtN(gp)}</span>
        </div>
      </td>
      <td class="py-1.5 pr-3 text-right font-bold" style="color:${textInk(mColor)}">${r.margin_pct != null ? r.margin_pct.toFixed(1)+'%' : '—'}</td>
      <td class="py-1.5 pr-3 text-right">${fmtN(r.tx_count||0)}</td>
      <td class="py-1.5 pr-3 text-right">${fmtN(r.avg_ticket||0)}</td>
      <td class="py-1.5 text-right font-medium" style="color:var(--accent)">${r.profit_per_bill != null ? fmtN(r.profit_per_bill) : '—'}</td>
    </tr>`;
  }).join('');
}

function sortPnlTable(col) {
  pnlSortAsc = pnlSortCol === col ? !pnlSortAsc : false;
  pnlSortCol = col;
  renderPnlBranchTable();
}

function clickPnlBranchRow(branch) {
  const sel = document.getElementById('pnlDeepBranchSel');
  if (sel) sel.value = branch;
  loadPnlDeepDive(branch);
  // Scroll to deep dive section
  document.getElementById('pnlDeepContent')?.scrollIntoView({ behavior:'smooth', block:'start' });
}

function populatePnlDeepBranchSelector(branches) {
  const sel = document.getElementById('pnlDeepBranchSel');
  if (!sel) return;
  const prev = pnlDeepBranch || sel.value;
  sel.innerHTML = '<option value="">— เลือกสาขา —</option>' +
    branches.map(b => `<option value="${esc(b.branch)}"${b.branch === prev ? ' selected' : ''}>${esc(b.branch)}</option>`).join('');
}

function onPnlDeepBranchChange() {
  const branch = document.getElementById('pnlDeepBranchSel')?.value;
  if (branch) loadPnlDeepDive(branch);
  else {
    pnlDeepBranch = '';
    document.getElementById('pnlDeepContent')?.classList.add('hidden');
    document.getElementById('pnlDeepBranchHint')?.classList.remove('hidden');
  }
}

async function loadPnlDeepDive(branch) {
  pnlDeepBranch = branch;
  const p = new URLSearchParams({ branch });
  if (getMonthFrom()) p.set('month_from', getMonthFrom());
  if (getMonthTo())   p.set('month_to',   getMonthTo());

  const data = await api(`${API}/api/pnl/branch-detail?${p}`);

  document.getElementById('pnlDeepContent')?.classList.remove('hidden');
  document.getElementById('pnlDeepBranchHint')?.classList.add('hidden');

  renderPnlDeepTrend(data.monthly);
  renderPnlDeepBillStats(data.bill_stats);
  renderPnlDeepMonthTable(data.monthly);
  renderPnlDeepIngredients(data.top_ingredients || []);
  renderPnlDeepCategoryMix(data.category_mix || []);
  renderPnlDeepSkus(data.top_skus);

  // Update selector to match
  const sel = document.getElementById('pnlDeepBranchSel');
  if (sel && sel.value !== branch) sel.value = branch;
}

function renderPnlDeepTrend(monthly) {
  mkChart('chartPnlDeepTrend', {
    type:'bar',
    data:{
      labels: monthly.map(m => fmtPeriod(m.period_month)),
      datasets:[
        { type:'bar',  label:'รายได้',      data:monthly.map(m=>m.revenue_ex_vat||0), backgroundColor:'rgba(31,58,107,0.35)', order:2 },
        { type:'bar',  label:'ต้นทุนขาย',         data:monthly.map(m=>m.cogs||0),           backgroundColor:'rgba(239,68,68,0.6)',  order:2 },
        { type:'line', label:'กำไรขั้นต้น', data:monthly.map(m=>m.gross_profit||0),   borderColor:'#10b981', tension:0.3, fill:false, order:1, pointRadius:3 },
        { type:'line', label:'GP/บิล',      data:monthly.map(m=>m.profit_per_bill),   borderColor:'#7a66b8', borderDash:[4,2], tension:0.3, fill:false, order:0, yAxisID:'y1', pointRadius:2 },
      ],
    },
    options:{
      responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{position:'bottom',labels:{font:{size:10}}}, datalabels:{display:false},
        tooltip:{callbacks:{label:ctx=>ctx.dataset.yAxisID==='y1'?ctx.dataset.label+': '+fmtN(ctx.raw||0):ctx.dataset.label+': '+fmtN(ctx.raw||0)}} },
      scales:{
        x:{ ticks:{color:chartTheme().text,font:{size:9}} },
        y:{ ticks:{color:chartTheme().text, callback:v=>fmt(v)} },
        y1:{ position:'right', grid:{display:false}, ticks:{color:'#7a66b8', callback:v=>fmt(v)} },
      },
    },
  });
}

function renderPnlDeepBillStats(stats) {
  const el = document.getElementById('pnlBillStatsTable');
  if (!el) return;
  if (!stats || stats.tx_count === 0) {
    el.innerHTML = '<p class="text-slate-400 py-2">No Easy POS transaction data for this period (Easy POS available from May 2025)</p>';
    return;
  }
  const m = stats.margin_pct_used;
  const gpRow = m > 0 ? `<tr class="border-b border-slate-100">
    <td class="py-1 pr-2 text-slate-500">Est. GP/Bill</td>
    <td class="text-right py-1">${fmtN((stats.p10*m/100).toFixed(0))}</td>
    <td class="text-right py-1">${fmtN((stats.p25*m/100).toFixed(0))}</td>
    <td class="text-right py-1 font-semibold">${fmtN((stats.p50*m/100).toFixed(0))}</td>
    <td class="text-right py-1">${fmtN((stats.p75*m/100).toFixed(0))}</td>
    <td class="text-right py-1">${fmtN((stats.p90*m/100).toFixed(0))}</td>
    <td class="text-right py-1 font-bold text-emerald-600">${fmtN(stats.avg_profit)}</td>
  </tr>` : '';
  el.innerHTML = `<table class="w-full border-collapse">
    <thead><tr class="border-b border-slate-200 text-slate-500">
      <th class="text-left py-1 pr-2"></th>
      <th class="text-right py-1 pr-2">P10</th><th class="text-right py-1 pr-2">P25</th>
      <th class="text-right py-1 pr-2">ค่ากลาง (มัธยฐาน)</th><th class="text-right py-1 pr-2">P75</th>
      <th class="text-right py-1 pr-2">P90</th><th class="text-right py-1">เฉลี่ย</th>
    </tr></thead>
    <tbody>
      <tr class="border-b border-slate-100">
        <td class="py-1 pr-2 text-slate-600 font-medium">Revenue/Bill</td>
        <td class="text-right py-1">${fmtN(stats.p10)}</td>
        <td class="text-right py-1">${fmtN(stats.p25)}</td>
        <td class="text-right py-1 font-semibold">${fmtN(stats.p50)}</td>
        <td class="text-right py-1">${fmtN(stats.p75)}</td>
        <td class="text-right py-1">${fmtN(stats.p90)}</td>
        <td class="text-right py-1 font-bold text-indigo-600">${fmtN(stats.avg)}</td>
      </tr>${gpRow}
    </tbody>
  </table>
  <p class="text-slate-400 mt-1">${fmtN(stats.tx_count)} bills · Range ${fmtN(stats.min)}–${fmtN(stats.max)}${m>0?' · Margin rate: '+m+'%':' · Upload costs for GP estimate'}</p>`;

  // Revenue histogram
  mkChart('chartPnlDeepBillDist', {
    type:'bar',
    data:{ labels:stats.buckets.map(b=>b.label+' '), datasets:[{ label:'บิล', data:stats.buckets.map(b=>b.count), backgroundColor:'#4a70b0', borderRadius:3, borderWidth:0 }] },
    options:{ responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false},datalabels:{display:false},tooltip:{callbacks:{label:ctx=>fmtN(ctx.raw)+' bills'}}},
      scales:{x:{ticks:{color:chartTheme().text,font:{size:9}}},y:{beginAtZero:true,ticks:{color:chartTheme().text}}} },
  });

  // Profit distribution
  const profitNote = document.getElementById('pnlDeepProfitNote');
  if (stats.profit_buckets && stats.profit_buckets.length > 0 && m > 0) {
    if (profitNote) profitNote.textContent = `Avg GP/bill: ${fmtN(stats.avg_profit)} · Margin rate applied: ${m}%`;
    mkChart('chartPnlDeepProfitDist', {
      type:'bar',
      data:{ labels:stats.profit_buckets.map(b=>b.label+' '),
        datasets:[{ label:'บิล', data:stats.profit_buckets.map(b=>b.count),
          backgroundColor: stats.profit_buckets.map((b,i)=>i===0?'rgba(239,68,68,0.7)':'rgba(16,185,129,0.7)'), borderRadius:3, borderWidth:0 }] },
      options:{ responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false},datalabels:{display:false},tooltip:{callbacks:{label:ctx=>fmtN(ctx.raw)+' bills'}}},
        scales:{x:{ticks:{color:chartTheme().text,font:{size:9}}},y:{beginAtZero:true,ticks:{color:chartTheme().text}}} },
    });
  } else if (profitNote) {
    profitNote.textContent = 'Upload SKU costs via Settings → Costs to see GP/bill distribution';
  }
}

function renderPnlDeepMonthTable(monthly) {
  document.getElementById('pnlDeepMonthBody').innerHTML = monthly.map(m => {
    const gp = m.gross_profit || 0;
    const gpColor = gp > 0 ? 'text-emerald-600' : 'text-red-500';
    // PR-PP3: the month in progress compares its GP to date with the same days of last month (mom_basis 'to_date')
    const _tip = m.mom_basis === 'to_date' && m.period ? ` title="${esc(m.period.label_th + ' เทียบ ' + m.period.prev_label_th)}"` : '';
    const momHtml = m.mom_pct != null
      ? `<span class="${m.mom_pct>=0?'text-emerald-500':'text-red-500'} ml-1"${_tip}>(${m.mom_pct>0?'+':''}${m.mom_pct}%${m.mom_basis === 'to_date' ? ' ถึงวันที่' : ''})</span>` : '';
    return `<tr class="border-b border-slate-100 hover:bg-slate-50">
      <td class="py-1 pr-2 font-medium">${fmtPeriod(m.period_month)}</td>
      <td class="py-1 pr-2 text-right">${fmtN(m.revenue_ex_vat||0)}</td>
      <td class="py-1 pr-2 text-right text-red-400">${fmtN(m.cogs||0)}</td>
      <td class="py-1 pr-2 text-right ${gpColor} font-semibold">${fmtN(gp)}${momHtml}</td>
      <td class="py-1 pr-2 text-right">${m.margin_pct!=null?m.margin_pct.toFixed(1)+'%':'—'}</td>
      <td class="py-1 pr-2 text-right">${fmtN(m.tx_count||0)}</td>
      <td class="py-1 pr-2 text-right">${fmtN(m.avg_ticket||0)}</td>
      <td class="py-1 text-right text-violet-600">${m.profit_per_bill!=null?fmtN(m.profit_per_bill):'—'}</td>
    </tr>`;
  }).join('');
}

function renderPnlDeepIngredients(ings) {
  document.getElementById('pnlDeepIngBody').innerHTML = ings.map((r,i)=>`
    <tr class="border-b border-slate-50">
      <td class="py-1.5 pr-2 text-slate-400">${i+1}</td>
      <td class="py-1.5 pr-2">${esc(r.name)}</td>
      <td class="py-1.5 pr-2 text-right text-red-500">${fmtN(r.cost)}</td>
      <td class="py-1.5 text-right font-semibold">${(r.pct||0).toFixed(1)}%</td>
    </tr>`).join('') || '<tr><td colspan="4" class="py-3 text-center text-slate-400">—</td></tr>';
}

function renderPnlDeepCategoryMix(cats) {
  document.getElementById('pnlDeepCatBody').innerHTML = cats.map(r=>{
    const m = (r.revenue>0) ? (100*(r.revenue-r.cogs)/r.revenue) : null;
    return `<tr class="border-b border-slate-50">
      <td class="py-1.5 pr-2">${esc((r.category||'').replace(/^Oshinei\s+/i,''))}</td>
      <td class="py-1.5 pr-2 text-right text-slate-500">${fmtN(r.revenue)}</td>
      <td class="py-1.5 pr-2 text-right text-red-500">${fmtN(r.cogs)}</td>
      <td class="py-1.5 text-right font-semibold">${(r.cost_pct||0).toFixed(1)}%</td>
    </tr>`;
  }).join('') || '<tr><td colspan="4" class="py-3 text-center text-slate-400">—</td></tr>';
}

function renderPnlDeepSkus(skus) {
  const note = document.getElementById('pnlDeepSkuNote');
  const totalCogs = skus.reduce((a,s)=>a+(s.cogs||0),0);
  const cumTop = skus.reduce((a,s)=>a+(s.cost_pct||0),0);
  if (note) note.textContent =
    `${skus.length} เมนูที่ขับต้นทุนสูงสุด (BoM) · รวม ${cumTop.toFixed(0)}% ของต้นทุนทั้งสาขา · เรียงตามต้นทุน`;
  document.getElementById('pnlDeepSkuBody').innerHTML = skus.map((s, i) => {
    const hasGp = s.gp != null;
    const gpColor = hasGp ? (s.gp >= 0 ? 'text-emerald-600' : 'text-red-500') : 'text-slate-300';
    const rowBg = hasGp && s.gp < 0 ? 'bg-red-50' : '';
    const pct = s.cost_pct || 0;
    // mini bar for cost share
    const bar = `<div style="display:inline-block;width:40px;height:6px;background:#fee2e2;border-radius:3px;vertical-align:middle;margin-right:4px"><div style="width:${Math.min(100,pct*2)}%;height:100%;background:#ef4444;border-radius:3px"></div></div>`;
    return `<tr class="border-b border-slate-100 hover:bg-slate-50 ${rowBg}">
      <td class="py-1.5 pr-2 text-slate-400">${i+1}</td>
      <td class="py-1.5 pr-2 font-mono text-slate-600">${s.sku_code}</td>
      <td class="py-1.5 pr-2 font-medium">${esc(s.description)}</td>
      <td class="py-1.5 pr-2 text-slate-500">${s.category}</td>
      <td class="py-1.5 pr-2 text-right">${fmtN(s.qty||0)}</td>
      <td class="py-1.5 pr-2 text-right">${fmtN(s.revenue_ex_vat||0)}</td>
      <td class="py-1.5 pr-2 text-right text-red-500 font-medium">${fmtN(s.cogs||0)}</td>
      <td class="py-1.5 pr-2 text-right whitespace-nowrap">${bar}<span class="font-semibold">${pct.toFixed(1)}%</span></td>
      <td class="py-1.5 pr-2 text-right font-bold ${gpColor}">${hasGp?fmtN(s.gp):'—'}</td>
      <td class="py-1.5 text-right">${s.margin_pct!=null?s.margin_pct.toFixed(1)+'%':'—'}</td>
    </tr>`;
  }).join('');
}

async function loadCostAnalysis() {
  const params = new URLSearchParams();
  if (getBranch())    params.set('branch',     getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to',   getMonthTo());
  costData = await api(`${API}/api/cost/analysis?${params}`);

  // Populate category filter for รายละเอียดเมนู
  const cats = [...new Set(costData.items.map(r => r.category))].sort();
  const catSel = document.getElementById('costItemCat');
  catSel.innerHTML = '<option value="">ทุกหมวด</option>';
  cats.forEach(c => { const o = document.createElement('option'); o.value=c; o.textContent=c; catSel.appendChild(o); });

  if (activeCostTab === 'overview')    renderCostOverview();
  if (activeCostTab === 'detail')      renderCostDetail();
  if (activeCostTab === 'suggestions') renderSuggestions();
  loadFoodCostTrend();
  renderCphCompare();
}

// Branch cost/head comparison — reuses the enriched cost-per-head endpoint.
// PR-PP3: on the top bar's period (เดือน · ปี), to date through the last closed day when it is not finished (it was the
// selected end month, or the last complete month).
async function renderCphCompare() {
  const G = gPeriodPick();
  const p = new URLSearchParams(); p.set('gran', G.kind === 'year' ? 'year' : 'month');
  if (G.pick) p.set('date', G.pick);
  if (getBranch()) p.set('branch', getBranch());
  if (getGroup()) p.set('group', getGroup());
  let d; try { d = await api(`${API}/api/sales/cost-per-head?${p}`); } catch (e) { return; }
  const branches = (d.branches || []).filter(b => b.cost_per_head != null).sort((a,b)=>b.cost_per_head - a.cost_per_head);
  const avg = (d.total || {}).cost_per_head;
  const R = periodOf(d);
  const mEl = document.getElementById('cphCompareMonth'); if (mEl) mEl.textContent = `${R ? periodSpan(R) : ''}${avg!=null?` · เฉลี่ยเครือ ${fmt(avg)}/หัว`:''}`;
  const ins = document.getElementById('cphCompareInsight');
  if (ins && branches.length >= 2 && avg != null) {
    const hi = branches[0], lo = branches[branches.length-1];
    ins.innerHTML = `<div class="text-xs flex flex-wrap gap-x-6 gap-y-1 p-2 rounded-lg" style="background:var(--bg-subtle)">
      <span>สูงสุด <b>${esc(hi.branch)}</b> ${fmt(hi.cost_per_head)}/หัว (ต้นทุน ${hi.cost_pct}% · รายได้/หัว ${fmt(hi.rev_per_head)})</span>
      <span>ต่ำสุด <b>${esc(lo.branch)}</b> ${fmt(lo.cost_per_head)}/หัว (ต้นทุน ${lo.cost_pct}%)</span>
      <span style="color:var(--text-muted)">ช่วงห่าง ${fmt(hi.cost_per_head - lo.cost_per_head)}/หัว</span></div>`;
  } else if (ins) { ins.innerHTML = ''; }
  if (!branches.length) { if (chartInstances['chartCphCompare']) chartInstances['chartCphCompare'].destroy(); return; }
  mkChart('chartCphCompare', {
    type: 'bar',
    data: {
      labels: branches.map(b => b.branch),
      datasets: [{
        label: 'ต้นทุน/หัว (BoM)',
        data: branches.map(b => b.cost_per_head),
        backgroundColor: branches.map(b => (avg != null && b.cost_per_head > avg) ? '#ef4444' : '#10b981'),
        borderRadius: 4,
      }]
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false }, datalabels: { display: false },
        tooltip: { callbacks: { label: ctx => {
          const b = branches[ctx.dataIndex];
          return ` ต้นทุน/หัว ${fmt(b.cost_per_head)} · ต้นทุน ${b.cost_pct}% · รายได้/หัว ${fmt(b.rev_per_head)} · GP/หัว ${fmt(b.gp_per_head)}`;
        } } }
      },
      scales: {
        x: { ticks: { callback: v => fmt(v) }, title: { display: true, text: avg!=null?`เฉลี่ยเครือ ${fmt(avg)}/หัว`:'' } },
        y: { ticks: { font: { size: 10 }, autoSkip: false } }
      }
    }
  });
}

async function loadFoodCostTrend() {
  const threshold = document.getElementById('foodCostThreshold')?.value || 35;
  const params = new URLSearchParams({ alert_threshold: threshold });
  if (getBranch())    params.set('branch', getBranch());
  if (getGroup())     params.set('group',  getGroup());
  const d = await api(`${API}/api/analysis/food_cost_trend?${params}`).catch(() => null);
  if (!d || !d.months || !d.months.length) return;

  // Alerts
  const alertEl = document.getElementById('foodCostAlerts');
  if (alertEl) {
    alertEl.innerHTML = (d.alerts || []).map(a =>
      `<div class="text-xs px-3 py-1.5 rounded-lg font-medium" style="background:#fef3c7;color:#92400e">${a.message}</div>`
    ).join('') || '';
  }

  // Build comparison chart: revenue vs CoGS vs servings, with cost % line
  // 3 axes: y (currency for rev/cost) · y1 (% for cost ratio) · y2 (servings count)
  const overall = d.overall;
  mkChart('chartFoodCostTrend', {
    data: { labels: d.months, datasets: [
      { type:'bar',  label:'รายได้ (ไม่รวม VAT)',  data: overall.map(o => o.revenue),  backgroundColor:'rgba(31,58,107,0.55)',  borderRadius:3, order:3, yAxisID:'y'  },
      { type:'bar',  label:'ต้นทุนขาย (BoM)',        data: overall.map(o => o.cost),     backgroundColor:'rgba(220,38,38,0.7)',   borderRadius:3, order:3, yAxisID:'y'  },
      { type:'line', label:'จำนวนเสิร์ฟ',       data: overall.map(o => o.servings), borderColor:'#0891b2', backgroundColor:'transparent', borderWidth:2, pointRadius:3, tension:0.3, order:1, yAxisID:'y2' },
      { type:'line', label:'ต้นทุนอาหาร %',       data: overall.map(o => o.cost_pct), borderColor:'#f59e0b', backgroundColor:'rgba(245,158,11,0.10)', borderWidth:2, borderDash:[4,3], pointRadius:3, tension:0.25, fill:true, order:0, yAxisID:'y1' },
      { type:'line', label:`Threshold (${threshold}%)`, data: d.months.map(() => parseFloat(threshold)), borderColor:'#ef4444', borderWidth:1, borderDash:[6,3], pointRadius:0, fill:false, yAxisID:'y1' },
    ] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position:'bottom', labels: { font: { size: 10 }, boxWidth: 14 } },
        datalabels: { display: false },
        tooltip: { mode:'index', intersect:false, callbacks: {
          afterBody: (ctxs) => {
            const idx = ctxs[0]?.dataIndex; if (idx == null) return '';
            const o = overall[idx]; if (!o) return '';
            const rps = o.rev_per_serving, cps = o.cost_per_serving;
            const lines = [];
            if (rps != null) lines.push(`รายได้/เสิร์ฟ: ${fmtN(rps)}`);
            if (cps != null) lines.push(`ต้นทุน/เสิร์ฟ: ${fmtN(cps)}`);
            return lines;
          }
        } },
      },
      scales: {
        x:  { ticks: { font: { size: 9 } } },
        y:  { position:'left',  ticks: { font: { size: 9 }, callback: v => fmt(v) }, title: { display: true, text: 'รายได้ / ต้นทุน (บาท)', font: { size: 9 } } },
        y1: { position:'right', ticks: { font: { size: 9 }, callback: v => v + '%' }, grid: { display: false }, min: 0, suggestedMax: Math.max(60, parseFloat(threshold) + 10) },
        y2: { display: false, beginAtZero: true },
      }
    }
  });

  // Insight strip below the chart. PR-PP3: the top bar's period (to date when it is not finished) against the same days of
  // the period before and of the year before (/api/period/cost); it set the month in progress against a whole month.
  const last = overall[overall.length - 1];
  const insightEl = document.getElementById('foodCostInsight');
  const pc = await api(`${API}/api/period/cost?${gPeriodQuery()}`).catch(() => null);
  if (insightEl && pc && pc.cur) {
    const c = pc.cur, R = pc.period, v = (o, k) => o ? o[k] : null;
    const thr = parseFloat(threshold), hot = (c.food_cost_pct || 0) > thr;
    periodChip(R, true);
    insightEl.innerHTML = `<div class="grid grid-cols-2 md:grid-cols-4 gap-2">` +
      periodKpi({ l: 'รายได้ (ไม่รวม VAT)', v: fmt(c.revenue), c: '#4a70b0', cur: c.revenue, prev: v(pc.prev, 'revenue'), ly: v(pc.ly, 'revenue'), range: R }) +
      periodKpi({ l: 'ต้นทุนขาย (BoM)', v: fmt(c.cogs), c: '#ef4444', cur: c.cogs, prev: v(pc.prev, 'cogs'), ly: v(pc.ly, 'cogs'), upGood: false, range: R, q: false }) +
      periodKpi({ l: 'ต้นทุน/หัว', v: c.cost_per_head != null ? fmt(c.cost_per_head) : '—', c: '#7a66b8', cur: c.cost_per_head, prev: v(pc.prev, 'cost_per_head'), ly: v(pc.ly, 'cost_per_head'), upGood: false, range: R, q: false }) +
      periodKpi({ l: 'ต้นทุนอาหาร %', v: c.food_cost_pct != null ? c.food_cost_pct.toFixed(1) + '%' : '—', c: hot ? '#b91c1c' : '#047857', cur: c.food_cost_pct, prev: v(pc.prev, 'food_cost_pct'), ly: v(pc.ly, 'food_cost_pct'), unit: 'pp', upGood: false, range: R, q: false }) +
      `</div>`;
  } else if (insightEl && last) {
    const prev = overall[overall.length - 2];
    const dPct  = (prev && last.cost_pct != null && prev.cost_pct != null) ? (last.cost_pct - prev.cost_pct) : null;
    const trendIcon = dPct == null ? '→' : dPct > 0 ? '▲' : '▼';
    const trendColor = dPct == null ? '#64748b' : dPct > 0 ? '#ef4444' : '#10b981';
    insightEl.innerHTML = `
      <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
        <div class="rounded-lg border border-indigo-100 bg-indigo-50/50 px-3 py-2">
          <p class="text-[10px] text-indigo-600 font-semibold">รายได้ ${d.months[d.months.length-1]}</p>
          <p class="text-base font-bold text-indigo-700">${fmt(last.revenue)}</p>
        </div>
        <div class="rounded-lg border border-red-100 bg-red-50/50 px-3 py-2">
          <p class="text-[10px] text-red-600 font-semibold">ต้นทุนขาย (BoM)</p>
          <p class="text-base font-bold text-red-700">${fmt(last.cost)}</p>
        </div>
        <div class="rounded-lg border border-cyan-100 bg-cyan-50/50 px-3 py-2">
          <p class="text-[10px] text-cyan-600 font-semibold">จำนวนเสิร์ฟ</p>
          <p class="text-base font-bold text-cyan-700">${fmtN(last.servings)}</p>
          <p class="text-[10px] text-slate-500">รายได้/เสิร์ฟ ${fmtN(last.rev_per_serving || 0)}</p>
        </div>
        <div class="rounded-lg border px-3 py-2 on-light" style="background:${(last.cost_pct||0)>parseFloat(threshold)?'#fef2f2':'#f0fdf4'};border-color:${(last.cost_pct||0)>parseFloat(threshold)?'#fecaca':'#bbf7d0'}">
          <p class="text-[10px] font-semibold" style="color:${textInk((last.cost_pct||0)>parseFloat(threshold)?'#b91c1c':'#047857')}">ต้นทุนอาหาร %</p>
          <p class="text-base font-bold" style="color:${textInk((last.cost_pct||0)>parseFloat(threshold)?'#b91c1c':'#047857')}">${last.cost_pct != null ? last.cost_pct.toFixed(1)+'%' : '—'}</p>
          ${dPct != null ? `<p class="text-[10px]" style="color:${textInk(trendColor)}">${trendIcon} ${Math.abs(dPct).toFixed(1)}pp เทียบเดือนก่อน</p>` : ''}
        </div>
      </div>`;
  }
}


// ── Overview ──────────────────────────────────────────────────

function renderCostOverview() {
  if (!costData) return;
  const s = costData.summary;
  const items = costData.items;
  const hasCosts = s.costed_skus > 0;

  // ── KPI cards ──
  const marginColor = s.overall_margin_pct == null ? '#94a3b8'
    : s.overall_margin_pct >= 60 ? '#10b981'
    : s.overall_margin_pct >= 40 ? '#f59e0b' : '#ef4444';

  document.getElementById('costKpis').innerHTML = [
    { label:'รายได้รวม',  value:fmt(s.total_revenue),      sub: fmtN(s.total_skus)+' items sold',     color:'#4a70b0' },
    { label:'ต้นทุนขายรวม',     value: hasCosts ? fmt(s.total_cogs) : '—',
                               sub: hasCosts ? s.costed_skus+' SKUs costed' : 'no costs entered', color:'#ef4444' },
    { label:'กำไรขั้นต้น',   value: hasCosts ? fmt(s.total_gross_profit) : '—',
                               sub: hasCosts ? 'Revenue (ex-VAT) minus COGS' : 'requires cost data', color:'#10b981' },
    { label:'มาร์จินรวม', value: s.overall_margin_pct != null ? s.overall_margin_pct.toFixed(1)+'%' : '—',
                               sub: hasCosts ? 'weighted gross margin' : 'requires cost data', color: marginColor },
  ].map(k => `
    <div class="card kpi-card p-4">
      <div class="kpi-card-top"><p class="kpi-label">${k.label}</p></div>
      <p class="kpi-value" style="color:${textInk(k.color)}">${k.value}</p>
      <div class="kpi-card-bottom"><p class="kpi-sub">${k.sub}</p></div>
    </div>`).join('');

  // ── No-cost banner ──
  const banner = document.getElementById('costNoBanner');
  if (s.uncosted_skus > 0) {
    document.getElementById('costNoBannerText').textContent =
      `${s.uncosted_skus} of ${s.total_skus} menu items have no cost data — margin analysis is incomplete.`;
    banner.classList.remove('hidden');
  } else {
    banner.classList.add('hidden');
  }

  // ── Scatter chart ──
  // Mode A (no costs): Qty vs Revenue — Revenue on Y, Qty on X, colour by category
  // Mode B (costs):    Qty vs Margin% with quadrant colouring
  const QCOLORS = { star:'#10b981', puzzle:'#4a70b0', plowhorse:'#f59e0b', dog:'#ef4444', uncosted:'#94a3b8' };
  let scatterDatasets;

  if (hasCosts) {
    document.getElementById('scatterTitle').textContent    = 'ตารางวิเคราะห์เมนู';
    document.getElementById('scatterMode').textContent     = 'margin vs popularity';
    document.getElementById('scatterMode').className       = 'pill pill-up';
    document.getElementById('scatterSubtitle').textContent = 'X = orders (popularity)  ·  Y = gross margin %  ·  Colour = quadrant';
    const quadGroups = ['star','puzzle','plowhorse','dog','uncosted'];
    const qlabels    = { star:'Stars', puzzle:'Puzzles', plowhorse:'Plowhorses', dog:'Dogs', uncosted:'Uncosted' };
    scatterDatasets = quadGroups.map(q => ({
      label: qlabels[q],
      data: items.filter(r => r.quadrant === q && r.margin_pct != null)
                 .map(r => ({ x: r.qty, y: r.margin_pct, label: r.description })),
      backgroundColor: QCOLORS[q] + 'BB',
      borderColor:     QCOLORS[q],
      borderWidth: 1, pointRadius: 5, pointHoverRadius: 8,
    }));
  } else {
    document.getElementById('scatterTitle').textContent    = 'Revenue × Popularity';
    document.getElementById('scatterMode').textContent     = 'no cost data';
    document.getElementById('scatterMode').className       = 'pill pill-warn';
    document.getElementById('scatterSubtitle').textContent = 'X = qty ordered  ·  Y = revenue  ·  Colour = category';
    const cats = [...new Set(items.map(r => r.category))];
    scatterDatasets = cats.map((cat, i) => ({
      label: cat,
      data: items.filter(r => r.category === cat)
                 .map(r => ({ x: r.qty, y: r.revenue, label: r.description })),
      backgroundColor: PALETTE[i % PALETTE.length] + 'AA',
      borderColor:     PALETTE[i % PALETTE.length],
      borderWidth: 1, pointRadius: 4, pointHoverRadius: 7,
    }));
  }

  mkChart('chartMenuScatter', {
    type: 'scatter',
    data: { datasets: scatterDatasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { color:'#64748b', font:{size:10}, boxWidth:10, padding:8 } },
        datalabels: { display: false },
        tooltip: { callbacks: { label: ctx => `${ctx.raw.label}: ${hasCosts ? ctx.raw.y?.toFixed(1)+'%' : fmtN(ctx.raw.y)} (${fmtN(ctx.raw.x)} pcs)` } },
      },
      scales: {
        x: { title:{ display:true, text:'Qty Ordered', color:'#94a3b8', font:{size:11} }, grid:{color:'#f1f5f9'}, ticks:{color:'#94a3b8',font:{size:10}} },
        y: { title:{ display:true, text: hasCosts ? 'Margin %' : 'Revenue ', color:'#94a3b8', font:{size:11} },
             grid:{color:'#f1f5f9'}, ticks:{color:'#94a3b8',font:{size:10}, callback: v => hasCosts ? v+'%' : fmt(v)} },
      },
    },
  });

  // ── Category mix donut ──
  const catMap = {};
  items.forEach(r => { catMap[r.category] = (catMap[r.category]||0) + r.revenue; });
  const catEntries = Object.entries(catMap).sort((a,b) => b[1]-a[1]);
  const top8 = catEntries.slice(0, 8);
  const other = catEntries.slice(8).reduce((s,[,v]) => s+v, 0);
  if (other > 0) top8.push(['Other', other]);
  const top8Colors = top8.map(([k]) => colorForCategory(k));
  mkChart('chartCostCatMix', {
    type: 'doughnut',
    data: { labels: top8.map(([k])=>k), datasets:[{ data:top8.map(([,v])=>Math.round(v)), backgroundColor:top8Colors, borderWidth:2, borderColor:chartTheme().bg }] },
    options: { responsive:true, maintainAspectRatio:false, cutout:'65%',
      plugins:{ legend:{display:false}, datalabels:{display:false}, tooltip:{callbacks:{label:ctx=>` ${fmtN(ctx.raw)} (${(ctx.raw/s.total_revenue*100).toFixed(1)}%)`}} } },
  });
  document.getElementById('catMixLegend').innerHTML = top8.map(([k,v],i) =>
    `<div class="flex items-center justify-between text-xs">
      <span class="flex items-center gap-1.5"><span style="background:${top8Colors[i]};width:8px;height:8px;border-radius:2px;display:inline-block"></span><span class="text-slate-600">${k}</span></span>
      <span class="text-slate-400">${(v/s.total_revenue*100).toFixed(1)}%</span>
    </div>`).join('');

  // ── Quadrant panels ──
  if (hasCosts) {
    ['star','puzzle','plowhorse','dog'].forEach(q => {
      const qItems = items.filter(r => r.quadrant === q);
      document.getElementById(q+'Count').textContent = qItems.length;
      document.getElementById(q+'List').innerHTML = qItems.slice(0,5).map(r =>
        `<li class="text-xs text-slate-600 truncate flex justify-between gap-2" title="${esc(r.description)}">
          <span class="truncate">${esc(r.description)}</span>
          <span class="text-slate-400 flex-shrink-0">${r.margin_pct?.toFixed(0)}%</span>
        </li>`).join('') || '<li class="text-xs text-slate-400 italic">None</li>';
    });
  } else {
    // Without costs: show รายได้สูงสุด / Top by Qty / High-Rev Low-Qty / High-Qty Low-Rev
    const byRev = [...items].sort((a,b) => b.revenue-a.revenue);
    const byQty = [...items].sort((a,b) => b.qty-a.qty);
    document.getElementById('starCount').textContent     = 'by revenue';
    document.getElementById('puzzleCount').textContent   = 'by quantity';
    document.getElementById('plowhorseCount').textContent= 'high qty, low rev';
    document.getElementById('dogCount').textContent      = 'low qty, low rev';
    document.getElementById('starList').innerHTML = byRev.slice(0,5).map(r =>
      `<li class="text-xs text-slate-600 truncate flex justify-between gap-2"><span class="truncate">${esc(r.description)}</span><span class="text-slate-400 flex-shrink-0">${fmt(r.revenue)}</span></li>`).join('');
    document.getElementById('puzzleList').innerHTML = byQty.slice(0,5).map(r =>
      `<li class="text-xs text-slate-600 truncate flex justify-between gap-2"><span class="truncate">${esc(r.description)}</span><span class="text-slate-400 flex-shrink-0">${fmtN(r.qty)}</span></li>`).join('');
    // High qty but lower revenue rank (potential underpriced)
    const avgRevPerQty = s.total_revenue / (s.total_qty || 1);
    const underpriced  = [...items].filter(r => r.qty > 0 && r.revenue/r.qty < avgRevPerQty * 0.7)
                                   .sort((a,b) => b.qty-a.qty).slice(0,5);
    document.getElementById('plowhorseList').innerHTML = underpriced.map(r =>
      `<li class="text-xs text-slate-600 truncate flex justify-between gap-2"><span class="truncate">${esc(r.description)}</span><span class="text-slate-400 flex-shrink-0">${fmtN(r.qty)} pcs</span></li>`).join('')
      || '<li class="text-xs text-slate-400 italic">None found</li>';
    const longTail = items.filter(r => r.cum_revenue_pct > 95).slice(0,5);
    document.getElementById('dogList').innerHTML = longTail.map(r =>
      `<li class="text-xs text-slate-600 truncate flex justify-between gap-2"><span class="truncate">${esc(r.description)}</span><span class="text-slate-400 flex-shrink-0">${r.revenue_mix_pct?.toFixed(2)}%</span></li>`).join('')
      || '<li class="text-xs text-slate-400 italic">None</li>';
  }

  // ── Pareto ──
  let p80idx = items.findIndex(r => r.cum_revenue_pct >= 80);
  let p50idx = items.findIndex(r => r.cum_revenue_pct >= 50);
  if (p80idx < 0) p80idx = items.length - 1;
  if (p50idx < 0) p50idx = 0;
  const pct80 = (((p80idx+1)/items.length)*100).toFixed(0);
  const pct50 = (((p50idx+1)/items.length)*100).toFixed(0);
  // Cost concentration analysis (cumulative cost vs cumulative revenue)
  const costedItems = items.filter(r => r.cogs != null && r.cogs > 0)
    .sort((a, b) => (b.cogs || 0) - (a.cogs || 0));
  let cumCost = 0;
  const totalCost = costedItems.reduce((s, r) => s + (r.cogs || 0), 0);
  costedItems.forEach(r => { cumCost += r.cogs; r._cum_cost_pct = totalCost ? (cumCost / totalCost * 100) : 0; });
  let c80idx = costedItems.findIndex(r => r._cum_cost_pct >= 80);
  if (c80idx < 0) c80idx = costedItems.length - 1;
  const cPct80 = costedItems.length ? (((c80idx+1)/costedItems.length)*100).toFixed(0) : '—';

  document.getElementById('paretoContent').innerHTML = `
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
      <div class="bg-indigo-50 rounded-xl p-4 text-center">
        <p class="text-2xl font-bold text-indigo-700">${pct50}%</p>
        <p class="text-xs text-slate-500 mt-1">of SKUs = 50% of revenue</p>
      </div>
      <div class="bg-indigo-50 rounded-xl p-4 text-center">
        <p class="text-2xl font-bold text-indigo-700">${pct80}%</p>
        <p class="text-xs text-slate-500 mt-1">of SKUs = 80% of revenue</p>
      </div>
      <div class="bg-red-50 rounded-xl p-4 text-center">
        <p class="text-2xl font-bold text-red-700">${cPct80 !== '—' ? cPct80+'%' : '—'}</p>
        <p class="text-xs text-slate-500 mt-1">of costed SKUs = 80% of total cost</p>
      </div>
      <div class="bg-slate-50 rounded-xl p-4 text-center">
        <p class="text-2xl font-bold text-slate-700">${items.length - p80idx - 1}</p>
        <p class="text-xs text-slate-500 mt-1">items in the long tail (last 20% rev)</p>
      </div>
    </div>
    <!-- Revenue & Cost concentration line chart -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-5 mt-4">
      <div>
        <p class="text-xs text-slate-500 font-medium mb-2">Revenue Concentration Curve</p>
        <div style="height:220px"><canvas id="chartParetoRev"></canvas></div>
      </div>
      <div>
        <p class="text-xs text-slate-500 font-medium mb-2">Cost Concentration Curve ${costedItems.length === 0 ? '<span class="text-amber-600 ml-2">(upload SKU costs to view)</span>' : ''}</p>
        <div style="height:220px"><canvas id="chartParetoCost"></canvas></div>
      </div>
    </div>
    <p class="text-xs text-slate-400 mt-4">A steeper curve means a few SKUs drive most of the metric. Use this to identify which items deserve cost-control attention first.</p>
    <div class="flex items-center gap-3 flex-wrap mt-3">
      <span class="text-xs font-medium text-slate-500">Top 80%-revenue SKUs:</span>
      ${items.slice(0, Math.min(p80idx+1, 8)).map((r,i) => `
        <div class="flex items-center gap-1.5 bg-indigo-50 rounded-lg px-2.5 py-1.5">
          <span class="text-xs font-semibold text-indigo-800">#${i+1}</span>
          <span class="text-xs text-slate-700 max-w-32 truncate">${esc(r.description)}</span>
          <span class="text-xs text-indigo-600 font-bold">${r.revenue_mix_pct?.toFixed(1)}%</span>
        </div>`).join('')}
      ${p80idx >= 8 ? `<span class="text-xs text-slate-400">+${p80idx-7} more…</span>` : ''}
    </div>`;

  // Render Pareto charts (after innerHTML so canvas exists)
  setTimeout(() => {
    // Revenue concentration
    const topN = Math.min(40, items.length);
    const revPoints = items.slice(0, topN);
    mkChart('chartParetoRev', {
      type: 'bar',
      data: {
        labels: revPoints.map((r, i) => '#' + (i+1)),
        datasets: [
          { type:'bar',  label:'รายได้ %', data: revPoints.map(r => r.revenue_mix_pct || 0), backgroundColor:'rgba(31,58,107,0.7)', order:2 },
          { type:'line', label:'% สะสม', data: revPoints.map(r => r.cum_revenue_pct || 0), borderColor:'#ef4444', backgroundColor:'transparent', borderWidth:2, pointRadius:0, tension:0.25, yAxisID:'y1', order:1 },
        ],
      },
      options: { responsive:true, maintainAspectRatio:false,
        plugins:{legend:{display:false}, datalabels:{display:false},
          tooltip:{callbacks:{title:(c)=>revPoints[c[0].dataIndex]?.description||'', label:ctx=>ctx.dataset.label+': '+(ctx.raw||0).toFixed(1)+'%'}}},
        scales:{ x:{ticks:{font:{size:9},color:'#94a3b8'}}, y:{ticks:{color:'#94a3b8',callback:v=>v+'%'}}, y1:{position:'right',max:100,min:0,grid:{display:false},ticks:{color:'#ef4444',callback:v=>v+'%'}}}},
    });

    if (costedItems.length) {
      const topC = Math.min(40, costedItems.length);
      const costPoints = costedItems.slice(0, topC);
      mkChart('chartParetoCost', {
        type: 'bar',
        data: {
          labels: costPoints.map((r, i) => '#' + (i+1)),
          datasets: [
            { type:'bar',  label:'Cost ', data: costPoints.map(r => r.cogs || 0), backgroundColor:'rgba(239,68,68,0.7)', order:2 },
            { type:'line', label:'% สะสม', data: costPoints.map(r => r._cum_cost_pct || 0), borderColor:'#f59e0b', backgroundColor:'transparent', borderWidth:2, pointRadius:0, tension:0.25, yAxisID:'y1', order:1 },
          ],
        },
        options: { responsive:true, maintainAspectRatio:false,
          plugins:{legend:{display:false}, datalabels:{display:false},
            tooltip:{callbacks:{title:(c)=>costPoints[c[0].dataIndex]?.description||'', label:ctx=>ctx.dataset.label+': '+(ctx.dataset.label==='Cost '?fmtN(ctx.raw||0):(ctx.raw||0).toFixed(1)+'%')}}},
          scales:{ x:{ticks:{font:{size:9},color:'#94a3b8'}}, y:{ticks:{color:'#94a3b8',callback:v=>fmtN(v)}}, y1:{position:'right',max:100,min:0,grid:{display:false},ticks:{color:'#f59e0b',callback:v=>v+'%'}}}},
      });
    } else {
      const canvas = document.getElementById('chartParetoCost');
      if (canvas && canvas.getContext) {
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#94a3b8'; ctx.font = '13px sans-serif'; ctx.textAlign = 'center';
        ctx.fillText('Upload SKU costs to see cost concentration', canvas.width/2, canvas.height/2);
      }
    }
  }, 50);

  renderCostAnalysisBar();
  renderAbcAnalysis();
}


let _abcMode = 'cost'; // 'revenue' | 'cost' — default to cost per user request
function setAbcMode(m) { _abcMode = m; renderAbcAnalysis(); }

function renderAbcAnalysis() {
  const el = document.getElementById('abcContent');
  if (!el || !costData) return;
  const items = costData.items;
  if (!items.length) { el.innerHTML = '<p class="text-sm text-slate-400">ไม่มีข้อมูล</p>'; return; }

  const byCost = _abcMode === 'cost';
  // Recompute cumulative pct based on chosen metric (BoM cost OR revenue)
  const sorted = [...items]
    .filter(r => byCost ? (r.cogs != null && r.cogs > 0) : (r.revenue > 0))
    .sort((a, b) => byCost ? (b.cogs - a.cogs) : (b.revenue - a.revenue));

  if (!sorted.length) {
    el.innerHTML = `<p class="text-sm text-amber-600">ยังไม่มีข้อมูล BoM cost — กลับไปใช้โหมดรายได้แทน หรืออัพโหลดต้นทุนใน Settings → Costs</p>`;
    return;
  }

  const total = sorted.reduce((s, r) => s + (byCost ? r.cogs : r.revenue), 0);
  let cum = 0;
  const classified = sorted.map(r => {
    const v = byCost ? r.cogs : r.revenue;
    cum += v;
    const cumPct = total ? (cum / total * 100) : 0;
    return { ...r, _val: v, _cumPct: cumPct,
             abc: cumPct <= 70 ? 'A' : cumPct <= 90 ? 'B' : 'C' };
  });

  const groupA = classified.filter(r => r.abc === 'A');
  const groupB = classified.filter(r => r.abc === 'B');
  const groupC = classified.filter(r => r.abc === 'C');
  const valA = groupA.reduce((s, r) => s + r._val, 0);
  const valB = groupB.reduce((s, r) => s + r._val, 0);
  const valC = groupC.reduce((s, r) => s + r._val, 0);
  const tot  = total || 1;
  const metricLabel = byCost ? 'ต้นทุน BoM' : 'รายได้';
  const valFmt = byCost ? fmt : fmt;

  // Each group's share of TOTAL BoM cost (independent of the ranking metric) —
  // so even in revenue mode you can see how much ingredient cost each class drives.
  const allCogs = (items.reduce((s, r) => s + (r.cogs || 0), 0)) || 1;
  const cogsOf  = grp => grp.reduce((s, r) => s + (r.cogs || 0), 0);
  const TOPN = 15;   // show up to 15 items per class (was 3)

  const card = (cls, color, border, items2, val, pct) => {
    const cogsPct = cogsOf(items2) / allCogs * 100;
    return `
    <div class="card p-4" style="border-top:3px solid ${border}">
      <div class="flex items-center gap-2 mb-2">
        <span class="text-lg font-black" style="color:${textInk(border)}">Class ${cls}</span>
        <span class="pill text-xs ml-auto on-light" style="background:${color};color:${textInk(border)}">${items2.length} SKUs · ${((items2.length/classified.length)*100).toFixed(0)}%</span>
      </div>
      <p class="text-xl font-bold" style="color:${textInk(border)}">${valFmt(val)}</p>
      <p class="text-xs text-slate-400 mt-0.5">${pct.toFixed(1)}% ของ${metricLabel}</p>
      <p class="text-[11px] font-semibold mt-0.5" style="color:${textInk(border)}">${cogsPct.toFixed(1)}% ของต้นทุน BoM รวม</p>
      <div class="mt-2 space-y-0.5 overflow-auto" style="max-height:240px">
        ${items2.slice(0,TOPN).map((r,i)=>`<div class="flex items-baseline justify-between gap-2 text-xs">
          <span class="text-slate-600 truncate" title="${esc(r.description)}">${i+1}. ${esc(r.description)}</span>
          <span class="text-slate-400 flex-shrink-0 tabular-nums">${fmt(byCost ? (r.cogs||0) : (r.revenue||0))}</span>
        </div>`).join('')}
        ${items2.length > TOPN ? `<div class="text-xs text-slate-400 pt-0.5">+${items2.length-TOPN} รายการ</div>` : ''}
      </div>
    </div>`;
  };

  const pA = (valA/tot*100).toFixed(1), pB = (valB/tot*100).toFixed(1), pC = (valC/tot*100).toFixed(1);

  // Mode toggle + insight (PR-MB: the reading of the classes is behind the "?" of the ABC line)
  const insight = uiQ('abc:insight-' + (byCost ? 'cost' : 'revenue'), 'ABC Analysis', byCost
    ? `Class A คือกลุ่ม SKU ที่ใช้ <b>ต้นทุนวัตถุดิบ 70% แรก</b> — ต้องโฟกัสด้าน supply chain (เจรจาราคา · safety stock · cycle count บ่อย)
       <br>Class C เป็น long-tail ของต้นทุน — อาจจะคุ้มน้อย ทบทวนว่าเป็น strategic items หรือไม่`
    : `Class A คือกลุ่ม SKU ที่ทำ <b>รายได้ 70% แรก</b> — ต้องโฟกัสด้าน sales (positioning · staff training · stockout prevention)
       <br>Class C เป็น long-tail — พิจารณาลดจำนวนเพื่อเพิ่ม kitchen execution speed`);

  el.innerHTML = `
    <div class="flex items-center gap-3 mb-4 flex-wrap">
      <span class="text-xs font-semibold text-slate-500 uppercase tracking-wide">วิเคราะห์ตาม</span>
      <div class="flex rounded-lg overflow-hidden border border-slate-200 text-xs font-medium">
        <button onclick="setAbcMode('cost')"    class="px-3 py-1.5 transition-colors ${byCost ? 'bg-rose-600 text-white' : 'bg-white text-slate-600 hover:bg-rose-50'}">ต้นทุน BoM</button>
        <button onclick="setAbcMode('revenue')" class="px-3 py-1.5 border-l border-slate-200 transition-colors ${byCost ? 'bg-white text-slate-600 hover:bg-rose-50' : 'bg-rose-600 text-white'}">รายได้</button>
      </div>
      <span class="text-xs text-slate-400">${sorted.length} SKUs ที่มีข้อมูล${byCost ? ' BoM' : 'รายได้'}</span>
    </div>
    <div class="grid grid-cols-3 gap-4 mb-5">
      ${card('A','#d1fae5','#10b981',groupA,valA,valA/tot*100)}
      ${card('B','#fef3c7','#f59e0b',groupB,valB,valB/tot*100)}
      ${card('C','#fee2e2','#ef4444',groupC,valC,valC/tot*100)}
    </div>
    <div class="mb-4">
      <div class="flex items-center gap-2 mb-1.5">
        <span class="text-xs font-semibold text-slate-500">สัดส่วน${metricLabel}</span>
        <span class="text-xs text-slate-400">${classified.length} SKUs</span>
      </div>
      <div class="flex rounded-full overflow-hidden h-5" style="width:100%">
        <div style="width:${pA}%;background:#10b981;color:${inkOn('#10b981')}" class="flex items-center justify-center text-xs font-bold" title="Class A: ${pA}%">${parseFloat(pA)>8?pA+'%':''}</div>
        <div style="width:${pB}%;background:#f59e0b;color:${inkOn('#f59e0b')}" class="flex items-center justify-center text-xs font-bold" title="Class B: ${pB}%">${parseFloat(pB)>8?pB+'%':''}</div>
        <div style="width:${pC}%;background:#ef4444;color:${inkOn('#ef4444')}" class="flex items-center justify-center text-xs font-bold" title="Class C: ${pC}%">${parseFloat(pC)>8?pC+'%':''}</div>
      </div>
      <div class="flex gap-4 mt-1.5">
        <span class="text-xs text-slate-500">A: ${groupA.length} SKUs = ${pA}%</span>
        <span class="text-xs text-slate-500">B: ${groupB.length} SKUs = ${pB}%</span>
        <span class="text-xs text-slate-500">C: ${groupC.length} SKUs = ${pC}%</span>
      </div>
    </div>
    <p class="text-xs text-slate-500 leading-relaxed"><b>ABC Analysis ตาม${metricLabel}</b> — A = 70% แรก · B = 70-90% · C = 90-100% ${insight}</p>`;
}


function renderCostAnalysisBar() {
  const el = document.getElementById('costAnalysisBar');
  if (!el || !costData) return;
  const items = costData.items;
  const s     = costData.summary;

  // Top 5 by revenue
  const top5 = items.slice(0, 5);
  const p20  = items.findIndex(r => r.cum_revenue_pct >= 80);
  const hasCosts = s.costed_skus > 0;

  const stat = (label, value, color = '#64748b') =>
    `<div>
      <p class="text-xs text-slate-400 mb-0.5">${label}</p>
      <p class="text-sm font-bold" style="color:${textInk(color)}">${value}</p>
    </div>`;

  const topList = `
    <div>
      <p class="text-xs text-slate-400 mb-1">Top Items</p>
      ${top5.map((r, i) => `
        <div class="flex items-baseline justify-between gap-1 mb-0.5">
          <span class="text-xs text-slate-600 truncate" style="max-width:90px" title="${esc(r.description)}">${i+1}. ${esc(r.description.slice(0,16))}</span>
          <span class="text-xs font-semibold text-indigo-600 flex-shrink-0">${r.revenue_mix_pct?.toFixed(1)}%</span>
        </div>`).join('')}
    </div>`;

  el.innerHTML = [
    stat('Active SKUs',    fmtN(s.total_skus), '#4a70b0'),
    hasCosts ? stat('Avg Margin',    (s.overall_margin_pct?.toFixed(1)||'—')+'%', s.overall_margin_pct>=60?'#10b981':s.overall_margin_pct>=40?'#f59e0b':'#ef4444') : stat('Costed SKUs', '0 / '+s.total_skus, '#f59e0b'),
    p20 >= 0 ? stat('80% Rev in',    (p20+1)+' SKUs', 'var(--text-primary)') : '',
    '<hr class="border-slate-100">',
    topList,
  ].join('');
}


// ── รายละเอียดเมนู ──────────────────────────────────────────────

function filterCostDetail() { renderCostDetail(); }

function sortCostDetail(key) {
  if (costSortKey === key) costSortAsc = !costSortAsc;
  else { costSortKey = key; costSortAsc = false; }
  renderCostDetail();
}

function renderCostDetail() {
  if (!costData) return;
  const q  = (document.getElementById('costItemSearch')?.value || '').toLowerCase();
  const cat = document.getElementById('costItemCat')?.value || '';
  const qd  = document.getElementById('costItemQuadrant')?.value || '';

  let rows = costData.items.filter(r =>
    (!q   || r.description.toLowerCase().includes(q) || r.sku_code.toLowerCase().includes(q)) &&
    (!cat || r.category === cat) &&
    (!qd  || r.quadrant === qd)
  );

  rows = [...rows].sort((a, b) => {
    const av = a[costSortKey] ?? -Infinity;
    const bv = b[costSortKey] ?? -Infinity;
    return costSortAsc ? av - bv : bv - av;
  });

  const _leak = costData.summary?.buffet_leak_excluded || 0;
  document.getElementById('costItemCount').textContent =
    `${rows.length.toLocaleString()} items` +
    (_leak ? ` · ตัดเมนูบุฟเฟ่ต์ (ราคา/หน่วย≈0) ${_leak.toLocaleString()} รายการ` : '');

  const QLABELS = { star:'★', puzzle:'', plowhorse:'', dog:'', uncosted:'○' };
  const QPILLS  = { star:'pill-up', puzzle:'pill-neu', plowhorse:'pill-warn', dog:'pill-down', uncosted:'' };

  document.getElementById('costItemBody').innerHTML = rows.map((r, i) => {
    const priced = _hasMenuPrice(r);              // buffet (B) has no per-dish price → margin undefined
    const mpv = priced ? r.margin_pct : null;
    const mc = mpv == null ? '#94a3b8'
      : mpv >= 60 ? '#10b981'
      : mpv >= 40 ? '#f59e0b' : '#ef4444';
    const mpShow = !priced
      ? '<span class="text-[10px] font-normal" style="color:var(--amber-ink)" title="บุฟเฟต์ — รวมในแพ็กเกจ ไม่มีราคาขายรายจาน">บุฟเฟต์</span>'
      : (mpv != null ? mpv.toFixed(1)+'%' : '<span class="text-slate-300 font-normal">—</span>');
    return `<tr class="border-t border-slate-100">
      <td class="py-2 pr-2 text-slate-400 text-xs">${i+1}</td>
      <td class="py-2 pr-3 font-mono text-indigo-600 text-xs">${r.sku_code}</td>
      <td class="py-2 pr-3 text-slate-700 max-w-xs truncate font-medium" title="${esc(r.description)}">${esc(r.description)}</td>
      <td class="py-2 pr-3 text-xs text-slate-500">${r.category}</td>
      <td class="py-2 pr-3 text-right">${fmtN(r.qty)}</td>
      <td class="py-2 pr-3 text-right text-slate-400 text-xs">${r.qty_mix_pct?.toFixed(1)}%</td>
      <td class="py-2 pr-3 text-right font-semibold">${fmtN(r.revenue)}</td>
      <td class="py-2 pr-3 text-right text-xs">
        <div class="flex items-center justify-end gap-1">
          <div class="h-1.5 rounded-full bg-indigo-500" style="width:${Math.min(r.revenue_mix_pct*3,60)}px"></div>
          <span class="text-slate-500">${r.revenue_mix_pct?.toFixed(1)}%</span>
        </div>
      </td>
      <td class="py-2 pr-3 text-right text-slate-500">${r.unit_cost != null ? fmtN(r.unit_cost) : '<span class="text-slate-300">—</span>'}</td>
      <td class="py-2 pr-3 text-right font-bold" style="color:${textInk(mc)}">${mpShow}</td>
      <td class="py-2 text-center">
        ${priced
          ? `<span class="pill ${QPILLS[r.quadrant]||''} text-xs">${QLABELS[r.quadrant]||''} ${r.quadrant !== 'uncosted' ? r.quadrant : '—'}</span>`
          : '<span class="pill text-xs" style="background:#fef3c7;color:#92400e">บุฟเฟต์</span>'}
      </td>
    </tr>`;
  }).join('');
}


// ── Suggestions ───────────────────────────────────────────────

function renderSuggestions() {
  if (!costData) return;
  const s     = costData.summary;
  const items = costData.items;
  const hasCosts = s.costed_skus > 0;

  const cards = [];

  // ── 1. Health summary ──
  const healthColor = !hasCosts ? '#f59e0b'
    : s.overall_margin_pct >= 60 ? '#10b981'
    : s.overall_margin_pct >= 40 ? '#f59e0b' : '#ef4444';
  const healthLabel = !hasCosts ? 'ต้องอัพเดทต้นทุน'
    : s.overall_margin_pct >= 60 ? 'กำไรแข็งแรง'
    : s.overall_margin_pct >= 40 ? 'มีโอกาสปรับปรุง' : 'น่าเป็นห่วง';
  cards.push(`
    <div class="card p-5" style="border:1px solid ${healthColor}">
      <div class="flex items-center gap-3 mb-3">
        <span class="text-xl">${!hasCosts ? '' : s.overall_margin_pct >= 60 ? '✓' : s.overall_margin_pct >= 40 ? '⚠' : '⚠'}</span>
        <div>
          <h3 class="font-bold text-slate-800">สุขภาพเมนู: ${healthLabel}</h3>
          <p class="text-xs text-slate-400">กำไรขั้นต้นรวม · ${s.total_skus} SKUs · ${s.costed_skus} ตัวที่มีต้นทุน</p>
        </div>
        ${hasCosts ? `<span class="ml-auto pill ${s.overall_margin_pct>=60?'pill-up':s.overall_margin_pct>=40?'pill-warn':'pill-down'} text-base">${s.overall_margin_pct?.toFixed(1)}%</span>` : ''}
      </div>
      <ul class="space-y-1.5 text-sm text-slate-600">
        <li>• เมนูมีทั้งหมด <strong>${s.total_skus} รายการ</strong> ยอดขายรวม <strong>${fmtN(Math.round(s.total_revenue))}</strong>${getMonth()?` ในเดือน ${fmtMonth(getMonth())}`:''}</li>
        ${hasCosts
          ? `<li>• ต้นทุนรวม: <strong>${fmtN(Math.round(s.total_cogs))}</strong> · กำไรขั้นต้น: <strong>${fmtN(Math.round(s.total_gross_profit))}</strong></li>
             <li>• อัตรากำไรขั้นต้น <strong>${s.overall_margin_pct?.toFixed(1)}%</strong> — ค่ามาตรฐานร้านอาหารไทยที่แข็งแรง 60–70%</li>`
          : `<li>• ยังขาดข้อมูลต้นทุน <strong>${s.uncosted_skus} รายการ</strong> — อัพเดทใน Settings เพื่อปลดล็อกการวิเคราะห์เต็มรูปแบบ</li>`}
      </ul>
    </div>`);

  // ── 2. Pareto + buffet concentration ──
  const p20idx = items.findIndex(r => r.cum_revenue_pct >= 80);
  const topPct  = p20idx >= 0 ? (((p20idx+1)/items.length)*100).toFixed(0) : '?';
  const top5Rev = items.slice(0,5).reduce((sum,r)=>sum+r.revenue,0);
  const top5Pct = s.total_revenue ? (top5Rev/s.total_revenue*100).toFixed(1) : '?';
  const buffetItems = items.filter(r => /BUFFET/i.test(r.sku_code || r.category || ''));
  const buffetRev   = buffetItems.reduce((sum,r)=>sum+r.revenue,0);
  const buffetCost  = buffetItems.reduce((sum,r)=>sum+(r.cogs||0),0);
  const buffetPct   = s.total_revenue ? (buffetRev/s.total_revenue*100).toFixed(1) : '0';
  const buffetCostPct = s.total_cogs ? (buffetCost/s.total_cogs*100).toFixed(1) : '0';
  const buffetMargin  = buffetRev > 0 && buffetCost > 0 ? ((buffetRev - buffetCost) / buffetRev * 100).toFixed(1) : null;
  cards.push(`
    <div class="card p-5">
      <div class="flex items-center gap-2 mb-3"><span class="text-xl"></span><h3 class="font-bold text-slate-800">กระจุกตัวรายได้ + บุฟเฟ่ต์ ${uiQ('insights:buffet-share', 'สัดส่วนบุฟเฟ่ต์', '<p>หาก buffet > 70% ของยอดขาย → ธุรกิจพึ่งพา volume สูง · เน้นควบคุม BoM และ portion control · พิจารณาเพิ่มเมนู à la carte กำไรสูงเพื่อกระจายความเสี่ยง</p>')}</h3></div>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
        <div class="rounded-lg p-3 border border-indigo-100" style="background:var(--accent-soft)">
          <p class="text-xs text-indigo-600 font-semibold mb-1">Pareto 80/20</p>
          <p class="text-sm text-slate-700">SKU เพียง <b>${topPct}%</b> ทำรายได้ 80% — ${Number(topPct) <= 20 ? 'กระจุกตัวสมเหตุสมผล' : 'กระจายเกินไป (ปกติ ≤20%)'}</p>
          <p class="text-xs text-slate-500 mt-1">Top 5 รวม <b>${top5Pct}%</b> ของยอดทั้งหมด</p>
        </div>
        <div class="rounded-lg p-3 border" style="background:#fff7ed;border-color:#fed7aa">
          <p class="text-xs text-orange-600 font-semibold mb-1">บุฟเฟ่ต์</p>
          <p class="text-sm text-slate-700">บุฟเฟ่ต์คิดเป็น <b>${buffetPct}%</b> ของยอดขาย แต่ <b>${buffetCostPct}%</b> ของต้นทุนรวม</p>
          ${buffetMargin ? `<p class="text-xs text-slate-500 mt-1">อัตรากำไรขั้นต้นของบุฟเฟ่ต์ <b>${buffetMargin}%</b></p>` : ''}
        </div>
      </div>
    </div>`);

  // ── 3 & 4 — quadrant recommendations (only if costs exist) ──
  if (hasCosts) {
    const stars      = items.filter(r => r.quadrant === 'star');
    const plowhorses = items.filter(r => r.quadrant === 'plowhorse');
    const puzzles    = items.filter(r => r.quadrant === 'puzzle');
    const dogs       = items.filter(r => r.quadrant === 'dog');

    // High-cost SKUs (cost > 50% of price) — actionable for renegotiation.
    // Exclude buffet (B) items: they have NO à-la-carte price (revenue ≈ 0), so
    // cogs/revenue explodes to meaningless % (e.g. -29,709,896%). Per-dish margin
    // is only defined for à-la-carte items. Also cap the ratio at a sane 3× as a
    // backstop against price-as-cost data errors. See _hasMenuPrice().
    const highCostItems = items.filter(r => r.unit_cost != null && _hasMenuPrice(r)
                                  && r.cogs/r.revenue_ex_vat > 0.5 && r.cogs/r.revenue_ex_vat <= 3)
                               .sort((a,b)=>b.cogs-a.cogs).slice(0,8);
    if (highCostItems.length) {
      const topCostBurden = highCostItems.slice(0,3).reduce((s,r)=>s+r.cogs,0);
      cards.push(`
        <div class="card p-5" style="border:1px solid #dc2626;background:#fef2f2">
          <div class="flex items-center gap-2 mb-3"><span class="text-xl"></span><h3 class="font-bold text-red-800">SKU ต้นทุนสูง — เป้าหมายลด BoM (${highCostItems.length} รายการ)</h3></div>
          <p class="text-sm text-slate-700 mb-3">รายการที่มีต้นทุน <b>เกิน 50%</b> ของยอดขาย ex-VAT แสดงว่า margin ต่ำ พร้อมถูกปรับ:</p>
          <div class="space-y-1 text-xs mb-3">
            ${highCostItems.map(r => `
              <div class="flex items-center justify-between bg-white rounded px-2 py-1 border border-red-100">
                <span class="truncate flex-1">${esc(r.description)}</span>
                <span class="text-slate-500 mx-2">ต้นทุน ${fmt(r.cogs)} (${(r.cogs/r.revenue_ex_vat*100).toFixed(0)}%)</span>
                <span class="font-bold text-red-600">${r.margin_pct?.toFixed(0)}%</span>
              </div>
            `).join('')}
          </div>
          <p class="text-xs text-slate-600 bg-white rounded p-2 border border-red-200">
            <b>แนวทาง:</b> Top 3 รายการสูงสุดมีต้นทุนรวม <b>${fmt(topCostBurden)}</b> — ลดได้ 10% = ประหยัด <b>${fmt(topCostBurden * 0.1)}</b>/เดือน
            <br>• <b>เจรจาซัพพลายเออร์</b> สำหรับวัตถุดิบที่ใช้บ่อย (ปริมาณมากต่อรองได้มากกว่า)
            <br>• <b>ลด portion size 5-10%</b> ในรายการที่ลูกค้ามักทานเหลือ (ดูจาก feedback)
            <br>• <b>ปรับสูตรหลัก</b> สลับวัตถุดิบราคาแพงด้วยทางเลือกราคาดีคุณภาพใกล้เคียง
          </p>
        </div>`);
    }

    if (stars.length) {
      const starsRev = stars.reduce((sum,r)=>sum+r.revenue,0);
      const starsPct = s.total_revenue ? (starsRev/s.total_revenue*100).toFixed(1) : 0;
      cards.push(`
        <div class="card p-5" style="border:1px solid #10b981">
          <div class="flex items-center gap-2 mb-3"><span class="text-xl">★</span><h3 class="font-bold text-emerald-800">ดาวเด่น Stars (${stars.length} รายการ · ${starsPct}% ของรายได้)</h3></div>
          <p class="text-sm text-slate-600 mb-3">เป็นทั้ง <b>ขายดี + กำไรสูง</b> — เป็นเครื่องจักรทำเงินของร้าน ต้องดูแลพิเศษ</p>
          <ul class="space-y-1 text-sm text-slate-600 mb-3">
            <li>• <b>การันตี supply chain</b> — สต็อกขาดสาขาเสียหายมากที่สุด ตั้ง safety stock 1.5 เท่า</li>
            <li>• <b>วางใน menu position ที่ดี</b> เช่น มุมขวาบน (golden corner) หรือมีรูปประกอบ</li>
            <li>• <b>ทดลองขึ้นราคา 5–10%</b> ลูกค้าที่รักเมนูนี้ ไม่ค่อย sensitive ต่อราคา</li>
            <li>• ใช้เป็น <b>menu engineering anchor</b> — เปรียบเทียบราคากับเมนูอื่นเพื่อทำให้ดูคุ้ม</li>
          </ul>
          <div class="flex flex-wrap gap-1">${stars.slice(0,10).map(r=>`<span class="pill pill-up">${esc(r.description.slice(0,20))} · ${r.margin_pct?.toFixed(0)}%</span>`).join('')}</div>
        </div>`);
    }

    if (plowhorses.length) {
      const avgMar = plowhorses.reduce((sum,r)=>sum+r.margin_pct,0)/plowhorses.length;
      const phRev = plowhorses.reduce((sum,r)=>sum+r.revenue,0);
      // Estimate the gain from a 5% price increase (assume 90% retention)
      const priceUpGain = phRev * 0.05 * 0.9;
      cards.push(`
        <div class="card p-5" style="border:1px solid #f59e0b">
          <div class="flex items-center gap-2 mb-3"><span class="text-xl"></span><h3 class="font-bold text-amber-800">ม้างาน Plowhorses (${plowhorses.length} รายการ · กำไรเฉลี่ย ${avgMar.toFixed(1)}%)</h3></div>
          <p class="text-sm text-slate-600 mb-3">ขายดีแต่กำไรต่ำ — ลูกค้ารัก แต่เราเสียโอกาส re-engineer เพื่อทำกำไรเพิ่ม</p>
          <ul class="space-y-1 text-sm text-slate-600 mb-3">
            <li>• <b>ตรวจต้นทุนวัตถุดิบรายตัว</b> — ประหยัด 1 บาท × volume สูง = ผลกระทบใหญ่</li>
            <li>• <b>ขึ้นราคา 5–10%</b> ทดลอง → คาดประหยัดต้นทุน + เพิ่มรายได้ <b>~${fmt(priceUpGain)}</b>/เดือน (สมมุติ retention 90%)</li>
            <li>• <b>ปรับสูตร/portion</b> เช่น ลดเนื้อจาก 200g เป็น 180g → ประหยัดต้นทุน 10% โดยไม่กระทบ perception</li>
            <li>• <b>Bundle กับ add-on margin สูง</b> (เครื่องดื่ม side dish) — ลูกค้าจ่ายเพิ่มเล็กน้อย margin รวมพุ่ง</li>
            <li>• ระวัง <b>plowhorse → dog spiral</b> ถ้าวัตถุดิบขึ้นราคา 20% แต่เราไม่ขึ้นตาม</li>
          </ul>
          <div class="flex flex-wrap gap-1">${plowhorses.slice(0,10).map(r=>`<span class="pill pill-warn">${esc(r.description.slice(0,20))} · ${r.margin_pct?.toFixed(0)}%</span>`).join('')}</div>
        </div>`);
    }

    if (puzzles.length) {
      const avgRev = puzzles.reduce((sum,r)=>sum+r.revenue_mix_pct,0)/puzzles.length;
      cards.push(`
        <div class="card p-5" style="border:1px solid var(--accent)">
          <div class="flex items-center gap-2 mb-3"><span class="text-xl"></span><h3 class="font-bold text-indigo-800">ปริศนา Puzzles (${puzzles.length} รายการ · revenue mix เฉลี่ย ${avgRev.toFixed(2)}%)</h3></div>
          <p class="text-sm text-slate-600 mb-3">กำไรดีแต่ขายน้อย — เพชรในตม ที่ลูกค้ายังไม่เห็น เป้าหมายคือเพิ่มยอดขาย</p>
          <ul class="space-y-1 text-sm text-slate-600 mb-3">
            <li>• <b>Training พนักงาน upselling</b> — front-of-house แนะนำได้ ยอดขายขยับเร็ว 30-50%</li>
            <li>• <b>"Chef's Special" หรือ "เมนูแนะนำ"</b> หมุนเวียนเด่นในเมนู ทำให้ดูพิเศษ</li>
            <li>• <b>Bundle กับ Plowhorse</b> เป็น combo set — ดึง volume จาก Plowhorse มาช่วย</li>
            <li>• <b>Reposition ในเมนู</b> ใส่รูปประกอบ + label "ขายดี" หรือ "ลูกค้าโปรด"</li>
            <li>• <b>โปรโมชั่นทดลอง</b> ลดราคา 10% ในสัปดาห์แรก เพื่อให้ลูกค้ารู้จัก</li>
          </ul>
          <div class="flex flex-wrap gap-1">${puzzles.slice(0,10).map(r=>`<span class="pill pill-neu">${esc(r.description.slice(0,20))} · ${r.margin_pct?.toFixed(0)}%</span>`).join('')}</div>
        </div>`);
    }

    if (dogs.length) {
      const dogsRev = dogs.reduce((sum,r)=>sum+r.revenue,0);
      const dogsPct = s.total_revenue ? (dogsRev/s.total_revenue*100).toFixed(1) : 0;
      cards.push(`
        <div class="card p-5" style="border:1px solid #ef4444">
          <div class="flex items-center gap-2 mb-3"><span class="text-xl"></span><h3 class="font-bold text-red-700">ตัวถ่วง Dogs (${dogs.length} รายการ · ${dogsPct}% ของรายได้) ${uiQ('insights:dogs-rule', 'ขนาดเมนู', '<p><b>Industry rule of thumb:</b> เมนู 50–80 รายการทำงานดีกว่าเมนูใหญ่ ทั้งใน kitchen execution + customer decision speed</p>')}</h3></div>
          <p class="text-sm text-slate-600 mb-3">ขายน้อย <em>และ</em> กำไรต่ำ — เปลืองพื้นที่เมนู เปลือง training พนักงาน เปลืองสต็อก</p>
          <ul class="space-y-1 text-sm text-slate-600 mb-3">
            <li>• <b>ตัดออกจากเมนูเลย</b> — สั้นกระชับดีกว่า ลูกค้าตัดสินใจเร็วขึ้น</li>
            <li>• ถ้าเป็น <b>strategic items</b> (vegan/allergen-free) → ปรับสูตรลดต้นทุน</li>
            <li>• ถ้ามีกลุ่มลูกค้าเฉพาะที่สั่ง → ประเมินผลกระทบรายได้ก่อนตัด</li>
            <li>• สาขาที่มีปัญหาเรื่อง kitchen capacity ควรเริ่มจากตัด Dogs ก่อน</li>
          </ul>
          <div class="flex flex-wrap gap-1">${dogs.slice(0,10).map(r=>`<span class="pill pill-down">${esc(r.description.slice(0,20))} · ${r.margin_pct?.toFixed(0)}%</span>`).join('')}</div>
        </div>`);
    }
  } else {
    // Without costs — volume-based insights
    const avgRevPerQty = s.total_revenue / (s.total_qty || 1);
    const potentiallyUnderpriced = items.filter(r => r.qty > 0 && r.revenue/r.qty < avgRevPerQty * 0.65).sort((a,b)=>b.qty-a.qty);
    if (potentiallyUnderpriced.length) {
      cards.push(`
        <div class="card p-5" style="border:1px solid #f59e0b">
          <div class="flex items-center gap-2 mb-3"><span class="text-xl"></span><h3 class="font-bold text-amber-800">Potential Pricing Opportunity (${potentiallyUnderpriced.length} items)</h3></div>
          <p class="text-sm text-slate-600 mb-3">These items are ordered frequently but generate below-average revenue per portion — they may be underpriced relative to their popularity.</p>
          <div class="flex flex-wrap gap-2 mb-3">${potentiallyUnderpriced.slice(0,8).map(r=>`<span class="pill pill-warn">${esc(r.description.slice(0,22))} · ${fmtN(Math.round(r.revenue/r.qty))}/pc</span>`).join('')}</div>
          <p class="text-xs text-slate-400">Enter unit costs in Settings to confirm whether these items also have low margins.</p>
        </div>`);
    }

    // Long tail suggestion
    const longTailCount = items.filter(r => r.cum_revenue_pct > 90).length;
    if (longTailCount > 20) {
      cards.push(`
        <div class="card p-5" style="border:1px solid #94a3b8">
          <div class="flex items-center gap-2 mb-3"><span class="text-xl"></span><h3 class="font-bold text-slate-700">โอกาสลดจำนวนเมนู</h3></div>
          <p class="text-sm text-slate-600 mb-2"><strong>${longTailCount} items</strong> (${(longTailCount/items.length*100).toFixed(0)}% of your menu) together contribute only 10% of revenue. A focused menu improves kitchen speed, reduces waste, and sharpens your brand.</p>
          <ul class="space-y-1 text-sm text-slate-600">
            <li>• Industry research shows menus with 50–80 items outperform larger menus on execution quality</li>
            <li>• You currently have <strong>${items.length} active SKUs</strong></li>
            <li>• Enter costs to identify which of these are also low-margin before deciding on removal</li>
          </ul>
        </div>`);
    }
  }

  // ── Buffet-specific deep dive (always shown if buffet category exists) ──
  if (buffetItems.length && hasCosts) {
    const sorted = [...buffetItems].sort((a,b) => (b.revenue || 0) - (a.revenue || 0));
    const topTier = sorted[0];
    const buffetGP = buffetItems.reduce((sum,r)=>sum+(r.gross_profit||0),0);
    const buffetGPPct = buffetRev > 0 ? (buffetGP/buffetRev*100).toFixed(1) : '0';
    // Find pricing imbalance: highest tier doesn't have highest margin?
    const highest_price = sorted.reduce((mx, r) => (r.revenue/r.qty > mx.revenue/mx.qty) ? r : mx, sorted[0]);
    const highest_margin = sorted.reduce((mx, r) => (r.margin_pct || 0) > (mx.margin_pct || 0) ? r : mx, sorted[0]);
    cards.push(`
      <div class="card p-5" style="border:1px solid #ec4899;background:#fdf2f8">
        <div class="flex items-center gap-2 mb-3"><span class="text-xl"></span><h3 class="font-bold text-pink-800">เจาะลึกบุฟเฟ่ต์ — วิเคราะห์ธุรกิจหลัก</h3></div>
        <p class="text-sm text-slate-700 mb-3">บุฟเฟ่ต์เป็น <b>${buffetPct}%</b> ของยอดขาย แต่ใช้ <b>${buffetCostPct}%</b> ของต้นทุนรวม — อัตรากำไรขั้นต้น <b>${buffetGPPct}%</b></p>
        <div class="space-y-2 text-sm">
          <div class="bg-white rounded-lg p-3 border border-pink-100">
            <p class="font-semibold text-pink-700 mb-1">Tier ทำเงินสูงสุด</p>
            <p class="text-slate-700">${topTier.description} — ยอดขาย <b>${fmt(topTier.revenue)}</b> (${topTier.qty.toLocaleString()} pax) · margin <b>${topTier.margin_pct?.toFixed(1)}%</b></p>
          </div>
          ${highest_price.sku_code !== highest_margin.sku_code ? `
          <div class="bg-amber-50 rounded-lg p-3 border border-amber-200">
            <p class="font-semibold text-amber-800 mb-1">⚠ Pricing imbalance</p>
            <p class="text-xs text-slate-700">Tier ราคาสูงสุด (<b>${highest_price.description}</b>) ทำกำไร <b>${highest_price.margin_pct?.toFixed(1)}%</b><br>
            แต่ Tier กำไรสูงสุดจริงคือ <b>${highest_margin.description}</b> ที่ <b>${highest_margin.margin_pct?.toFixed(1)}%</b><br>
            → พิจารณาขึ้นราคา Tier บน หรือ ลดต้นทุนวัตถุดิบ premium</p>
          </div>` : ''}
          <div class="bg-rose-50 rounded-lg p-3 border border-rose-200">
            <p class="font-semibold text-rose-800 mb-1">Action items บุฟเฟ่ต์ ${uiQ('insights:buffet-actions', 'Action items บุฟเฟ่ต์', `<ul><li>• <b>BoM Tracking</b> — บันทึกต้นทุนวัตถุดิบบุฟเฟ่ต์รายเดือน (เนื้อแซลมอน เนื้อวากิว ของหวาน) → ตอบโจทย์ว่า margin ตกหรือไม่</li><li>• <b>Portion Control</b> — บุฟเฟ่ต์ที่ "all-you-can-eat" ต้องมีระบบ <b>ค่าปรับเหลือ</b> (มี SKU OT0012/0013/0016 อยู่แล้ว) เก็บอย่างเข้มงวด</li><li>• <b>Daypart Pricing</b> — ลด tier ราคาตอน lunch (฿459) เพื่อดึงคน เพิ่ม tier ราคาตอน dinner (฿789/฿999) เพื่อเพิ่ม margin</li><li>• <b>Premium Add-on</b> — ขาย add-on margin สูง เช่น แซลมอนพรีเมียม เครื่องดื่ม alcohol → ลูกค้าบุฟเฟ่ต์ตัดสินใจง่ายเพราะอารมณ์ "เปย์มาแล้ว"</li><li>• <b>Buffet Mix Optimization</b> — Tier ฿629 เป็น sweet spot ของเมนูใหม่ — ปรับ menu mix ในนี้เพื่อ <b>ลด BoM 5% = +${fmt(buffetCost * 0.05)}/เดือน</b></li></ul>`)}</p>
          </div>
        </div>
      </div>`);
  }

  // ── Final: missing cost data CTA ──
  if (s.uncosted_skus > 0) {
    const coverageRev = costData.items.filter(r=>r.unit_cost!=null).reduce((a,r)=>a+r.revenue,0);
    const coveragePct = s.total_revenue ? (coverageRev/s.total_revenue*100).toFixed(1) : 0;
    cards.push(`
      <div class="card p-5" style="border:1px solid #06b6d4">
        <div class="flex items-center gap-2 mb-3"><span class="text-xl"></span><h3 class="font-bold text-cyan-800">เติมข้อมูลต้นทุนให้ครบ</h3></div>
        <p class="text-sm text-slate-600 mb-3">ยังขาดต้นทุน <strong>${s.uncosted_skus} SKUs</strong> · ครอบคลุมรายได้ปัจจุบัน <strong>${coveragePct}%</strong> · เติมให้ครบเพื่อปลดล็อก menu engineering เต็มรูปแบบ</p>
        <button onclick="switchTab('settings');switchSettingsTab('costs')"
          class="px-4 py-2 bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-medium rounded-lg transition-colors">
          เปิดหน้าจัดการต้นทุน →
        </button>
      </div>`);
  }

  document.getElementById('suggestionsContent').innerHTML = cards.join('');
}


// ════════════════════════════════════════════════════════════════
// TAB 7 — SETTINGS
// ════════════════════════════════════════════════════════════════

// ── Monthly Targets ───────────────────────────────────────────

async function loadTargets() {
  const month = document.getElementById('targetMonth').value;
  if (!month) return;

  const [targets, actuals] = await Promise.all([
    api(`${API}/api/targets?month=${encodeURIComponent(month)}`),
    api(`${API}/api/analysis/branches?month=${encodeURIComponent(month)}`),
  ]);

  const actualMap = {};
  actuals.forEach(a => { actualMap[a.branch] = a; });

  document.getElementById('targetBody').innerHTML = targets.map(t => {
    const act = actualMap[t.branch] || {};
    const revPct = t.revenue_target > 0 && act.revenue
      ? (act.revenue / t.revenue_target * 100) : null;
    const custPct = t.customer_target > 0 && act.customer_count
      ? (act.customer_count / t.customer_target * 100) : null;
    const pillClass = revPct == null ? '' : revPct >= 100 ? 'pill-up' : revPct >= 80 ? 'pill-warn' : 'pill-down';
    const pillText  = revPct == null ? '—' : revPct.toFixed(0) + '%';
    // the owner's rule (2026-09-05): blank = automatic = this branch's actual of the same month last year;
    // a typed figure wins; 0 goes back to automatic
    const manual = t.source === 'manual';
    const src = manual ? '<span class="pill pill-warn">ตั้งเอง</span>'
      : t.source === 'last_year' ? `<span class="pill pill-up">อัตโนมัติ</span> <span class="text-slate-500">= ยอดจริง ${fmtMonth(t.basis_month)}</span>`
      : `<span class="pill">ไม่มีข้อมูล ${fmtMonth(t.basis_month)}</span>`;
    return `<tr class="border-t border-slate-100">
      <td class="py-2.5 pr-4 font-medium text-slate-700">${t.branch}</td>
      <td class="py-2.5 pr-4 text-xs whitespace-nowrap">${src}</td>
      <td class="py-2.5 pr-4 text-right">
        <input type="number" min="0" step="10000"
          class="target-rev-input w-36 border border-slate-200 rounded-lg px-2 py-1 text-sm text-right focus:outline-none focus:ring-2 focus:ring-indigo-400"
          data-branch="${t.branch}" value="${manual ? t.revenue_target : ''}" placeholder="${t.source === 'last_year' ? fmtN(t.revenue_target) : '—'}" title="เว้นว่าง = ใช้ยอดจริงของ ${fmtMonth(t.basis_month)} (${fmtN(t.basis_revenue || 0)})">
      </td>
      <td class="py-2.5 pr-4 text-right">
        <input type="number" min="0" step="100"
          class="target-cust-input w-28 border border-slate-200 rounded-lg px-2 py-1 text-sm text-right focus:outline-none focus:ring-2 focus:ring-indigo-400"
          data-branch="${t.branch}" value="${manual ? t.customer_target : ''}" placeholder="${t.source === 'last_year' ? fmtN(t.customer_target) : '—'}">
      </td>
      <td class="py-2.5 pr-4 text-right text-slate-600">${act.revenue != null ? fmtN(act.revenue) : '—'}</td>
      <td class="py-2.5 pr-4 text-right text-slate-600">${act.customer_count != null ? fmtN(act.customer_count) : '—'}</td>
      <td class="py-2.5 text-right">
        <span class="pill ${pillClass}">${pillText}</span>
      </td>
    </tr>`;
  }).join('');
}

async function saveTargets() {
  const month  = document.getElementById('targetMonth').value;
  const status = document.getElementById('targetSaveStatus');
  if (!month) return;

  const entries = [];
  document.querySelectorAll('.target-rev-input').forEach(inp => {
    const branch = inp.dataset.branch;
    const custInp = document.querySelector(`.target-cust-input[data-branch="${branch}"]`);
    entries.push({
      branch,
      period_month: month,
      revenue_target:  parseFloat(inp.value) || 0,
      customer_target: parseInt(custInp?.value) || 0,
    });
  });

  try {
    status.textContent = 'Saving…'; status.className = 'text-sm text-slate-400';
    const res = await fetch(`${API}/api/targets`, {
      method: 'PUT',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(entries),
    });
    const data = await res.json();
    status.textContent = `✓ Saved ${data.saved} branches`;
    status.className = 'text-sm text-emerald-600';
    setTimeout(() => { status.textContent = ''; }, 3000);
  } catch(e) {
    status.textContent = '✗ ' + e.message;
    status.className = 'text-sm text-red-600';
  }
}

async function suggestTargets() {
  const month = document.getElementById('targetMonth').value;
  const status = document.getElementById('targetSaveStatus');
  if (!month) return;
  status.textContent = 'กำลังคำนวณ…'; status.className = 'text-sm text-slate-400';
  let d; try { d = await api(`${API}/api/targets/suggest?month=${encodeURIComponent(month)}`); }
  catch (e) { status.textContent = '✗ ' + e.message; status.className = 'text-sm text-red-600'; return; }
  if (!d.suggestions || !d.suggestions.length) {
    status.textContent = 'ไม่มีข้อมูลย้อนหลังพอ'; status.className = 'text-sm text-amber-600'; return;
  }
  const map = {}; d.suggestions.forEach(s => { map[s.branch] = s; });
  // fill the editable inputs — owner reviews, then presses Save Targets
  document.querySelectorAll('.target-rev-input').forEach(inp => {
    const s = map[inp.dataset.branch]; if (s) inp.value = s.revenue_target || '';
  });
  document.querySelectorAll('.target-cust-input').forEach(inp => {
    const s = map[inp.dataset.branch]; if (s) inp.value = s.customer_target || '';
  });
  status.innerHTML = `✓ เติมจากเฉลี่ย ${d.lookback} เดือน (${(d.trailing_months||[]).map(fmtMonth).join(', ')}) ×${d.uplift} — ตรวจแล้วกด <b>บันทึกเป้าหมาย</b>`;
  status.className = 'text-sm text-violet-700';
}

function populateTargetMonths() {
  const sel = document.getElementById('targetMonth');
  sel.innerHTML = '';
  [...allMonths].reverse().forEach(m => {
    const opt = document.createElement('option');
    opt.value = m; opt.textContent = fmtMonth(m);
    sel.appendChild(opt);
  });
}


// ── Cost Management ───────────────────────────────────────────

function debounceLoadCosts() {
  clearTimeout(costSearchTimer);
  costSearchTimer = setTimeout(() => { costPageOffset = 0; loadCosts(); }, 320);
}

async function loadCosts() {
  const q        = document.getElementById('costSearch').value.trim();
  const category = document.getElementById('costCatFilter').value;
  const priced   = document.getElementById('costPricedFilter')?.value || '';
  const minRev   = document.getElementById('costMinRev')?.value || '';
  const sortBy   = document.getElementById('costSortBy')?.value || '';
  const bizType  = document.getElementById('costBizTypeFilter')?.value || '';
  const params   = new URLSearchParams({ limit: COST_PAGE_SIZE, offset: costPageOffset });
  if (q)        params.set('q', q);
  if (category) params.set('category', category);
  if (priced)   params.set('priced', priced);
  if (minRev)   params.set('min_rev', minRev);
  if (sortBy)   params.set('sort', sortBy);
  if (bizType)  params.set('business_type', bizType);

  const data = await api(`${API}/api/cost/detail?${params}`);
  document.getElementById('costTotal').textContent = `${data.total.toLocaleString()} SKUs`;

  document.getElementById('costDetailBody').innerHTML = data.items.map(r => `
    <tr class="border-t border-slate-100" id="costrow-${r.sku_id}">
      <td class="py-2 pr-3 font-mono text-indigo-600 text-xs">${r.sku_code}</td>
      <td class="py-2 pr-3 text-slate-700 max-w-xs truncate" title="${esc(r.description)}">${esc(r.description || '—')}</td>
      <td class="py-2 pr-3">${renderBizTypeCell(r.sku_code, r.business_type || 'buffet')}</td>
      <td class="py-2 pr-3 text-slate-500 text-xs">${esc(r.category)}</td>
      <td class="py-2 pr-3 text-right">
        <div class="flex items-center justify-end gap-1.5">
          <input type="number" min="0" step="1"
            class="w-24 border border-slate-200 rounded-lg px-2 py-1 text-xs text-right focus:outline-none focus:ring-2 focus:ring-indigo-400"
            id="cost-inp-${r.sku_id}" value="${r.unit_cost != null ? r.unit_cost : ''}"
            placeholder="${r.unit_cost == null && r.effective_cost > 0 ? fmtN(r.effective_cost) : ''}"
            oninput="debounceCostSave(${r.sku_id}, '${jsAttr(r.sku_code)}')"
            onblur="saveCostRow(${r.sku_id}, '${jsAttr(r.sku_code)}')">
          <span id="cost-stat-${r.sku_id}" class="text-sm w-5 text-center font-bold" title="บันทึกอัตโนมัติ · auto-saves"></span>
        </div>
        ${r.unit_cost == null && r.effective_cost > 0
          ? `<div class="text-[10px] text-emerald-600 mt-0.5" title="ต้นทุนจากสูตร BoM — ใช้คำนวณ COGS อยู่แล้ว; พิมพ์ทับได้ถ้าต้องการแก้">BoM ฿${fmtN(r.effective_cost)}</div>`
          : ''}
      </td>
      <td class="py-2 pr-3 text-right text-slate-500">${r.total_revenue != null ? fmtN(r.total_revenue) : '—'}</td>
      <td class="py-2 text-right text-slate-500">${r.total_qty != null ? fmtN(r.total_qty) : '—'}</td>
    </tr>
  `).join('');

  // Pager
  const pages = Math.ceil(data.total / COST_PAGE_SIZE);
  const cur   = Math.floor(costPageOffset / COST_PAGE_SIZE);
  let pagerHtml = '';
  if (pages > 1) {
    pagerHtml += `<button onclick="setCostPage(${cur-1})" ${cur===0?'disabled':''} class="px-3 py-1 text-sm border border-slate-200 rounded-lg disabled:opacity-40">←</button>`;
    pagerHtml += `<span class="text-sm text-slate-500">${cur+1} / ${pages}</span>`;
    pagerHtml += `<button onclick="setCostPage(${cur+1})" ${cur>=pages-1?'disabled':''} class="px-3 py-1 text-sm border border-slate-200 rounded-lg disabled:opacity-40">→</button>`;
  }
  document.getElementById('costPager').innerHTML = pagerHtml;
}

function setCostPage(page) {
  costPageOffset = page * COST_PAGE_SIZE;
  loadCosts();
}

// ── Menu filter presets (Settings > Cost Management) ──────────
function getMenuFilterState() {
  return {
    q:        document.getElementById('costSearch').value.trim(),
    category: document.getElementById('costCatFilter').value,
    priced:   document.getElementById('costPricedFilter')?.value || '',
    min_rev:  document.getElementById('costMinRev')?.value || '',
    sort:     document.getElementById('costSortBy')?.value || '',
    biz_type: document.getElementById('costBizTypeFilter')?.value || '',
  };
}
function setMenuFilterState(s) {
  document.getElementById('costSearch').value = s.q || '';
  document.getElementById('costCatFilter').value = s.category || '';
  if (document.getElementById('costPricedFilter')) document.getElementById('costPricedFilter').value = s.priced || '';
  if (document.getElementById('costMinRev'))       document.getElementById('costMinRev').value = s.min_rev || '';
  if (document.getElementById('costSortBy'))       document.getElementById('costSortBy').value = s.sort || '';
  if (document.getElementById('costBizTypeFilter')) document.getElementById('costBizTypeFilter').value = s.biz_type || '';
}

async function saveMenuPreset() {
  const name = await uiPrompt('Name this menu filter:', 'My filter', { write: false });
  if (!name) return;
  const presets = JSON.parse(localStorage.getItem('oshinei_menu_presets') || '[]');
  presets.push({ name, state: getMenuFilterState() });
  localStorage.setItem('oshinei_menu_presets', JSON.stringify(presets));
  renderMenuPresetSelect();
}

function loadMenuPreset(idx) {
  if (idx === '') return;
  const presets = JSON.parse(localStorage.getItem('oshinei_menu_presets') || '[]');
  const p = presets[parseInt(idx, 10)];
  if (!p) return;
  setMenuFilterState(p.state);
  costPageOffset = 0;
  loadCosts();
}

function resetMenuFilters() {
  setMenuFilterState({});
  document.getElementById('costPresetSel').value = '';
  costPageOffset = 0;
  loadCosts();
}

function renderMenuPresetSelect() {
  const sel = document.getElementById('costPresetSel');
  if (!sel) return;
  const presets = JSON.parse(localStorage.getItem('oshinei_menu_presets') || '[]');
  sel.innerHTML = '<option value="">— เรียกตัวกรองที่บันทึกไว้ —</option>' +
    presets.map((p, i) => `<option value="${i}">★ ${p.name}</option>`).join('') +
    (presets.length ? '<option disabled>──────</option><option value="__delete">Delete a preset…</option>' : '');
  sel.onchange = async (e) => {
    if (e.target.value === '__delete') {
      const names = presets.map((p, i) => `${i+1}. ${p.name}`).join('\n');
      const idx = await uiPrompt('Delete which preset? Enter number:\n\n' + names, '', { write: false });
      const n = parseInt(idx, 10);
      if (!isNaN(n) && n >= 1 && n <= presets.length) {
        presets.splice(n - 1, 1);
        localStorage.setItem('oshinei_menu_presets', JSON.stringify(presets));
      }
      renderMenuPresetSelect();
      e.target.value = '';
    } else {
      loadMenuPreset(e.target.value);
    }
  };
}

// Per-row debounce timers for auto-save
const _costSaveTimers = {};
function debounceCostSave(skuId, skuCode) {
  const stat = document.getElementById(`cost-stat-${skuId}`);
  if (stat) { stat.textContent = '…'; stat.style.color = 'var(--text-muted)'; }
  clearTimeout(_costSaveTimers[skuId]);
  _costSaveTimers[skuId] = setTimeout(() => saveCostRow(skuId, skuCode), 800);
}
async function saveCostRow(skuId, skuCode) {
  const inp  = document.getElementById(`cost-inp-${skuId}`);
  const stat = document.getElementById(`cost-stat-${skuId}`);
  if (!inp) return;
  clearTimeout(_costSaveTimers[skuId]);   // blur/debounce shouldn't double-fire
  const raw = inp.value.trim();
  if (raw === '') { inp.style.borderColor = ''; if (stat) stat.textContent = ''; return; }
  const value = parseFloat(raw);
  if (isNaN(value) || value < 0) {
    inp.style.borderColor = '#ef4444';
    if (stat) { stat.textContent = '✗'; stat.style.color = 'var(--bad-ink)'; }
    return;
  }
  inp.style.borderColor = '';
  if (stat) { stat.textContent = '…'; stat.style.color = 'var(--text-muted)'; }
  try {
    const res  = await fetch(`${API}/api/cost`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify([{ sku_code: skuCode, unit_cost: value }]),
    });
    const data = await res.json();
    if (data.skipped_price_as_cost) {   // guard rejected it as price-as-cost
      inp.style.borderColor = '#f59e0b';
      if (stat) { stat.textContent = '⚠'; stat.style.color = 'var(--amber-ink)';
        stat.setAttribute('title', 'ค่านี้ใกล้เคียงราคาขาย (×1.07) — ดูเหมือนราคา ไม่ใช่ต้นทุน · looks like price, not cost'); }
      return;
    }
    if (stat) { stat.textContent = '✓'; stat.style.color = 'var(--good-ink)'; stat.title = 'บันทึกแล้ว · saved'; }
  } catch(e) {
    inp.style.borderColor = '#ef4444';
    if (stat) { stat.textContent = '✗'; stat.style.color = 'var(--bad-ink)'; }
  }
}

async function initCostCategories() {
  if (!hasModule('cost.analysis')) return;       // GET /api/cost needs cost.analysis (QA L4a: Sales Support and operation do not hold it) — skip the 403
  const data = await api(`${API}/api/cost`);
  const cats = [...new Set(data.map(r => r.category))].sort();
  const sel  = document.getElementById('costCatFilter');
  cats.forEach(c => {
    const opt = document.createElement('option');
    opt.value = c; opt.textContent = c;
    sel.appendChild(opt);
  });
}


// ── Branch Config ─────────────────────────────────────────────

async function loadBranchConfig() {
  const data = await api(`${API}/api/branches?include_inactive=true`);
  document.getElementById('branchConfigBody').innerHTML = data.map(b => `
    <tr class="border-t border-slate-100">
      <td class="py-2.5 pr-4 text-slate-400 text-xs">#${b.id}</td>
      <td class="py-2.5 pr-4">
        <input type="text" value="${esc(b.name)}"
          id="branch-name-${b.id}"
          class="border border-slate-200 rounded-lg px-3 py-1.5 text-sm w-48 focus:outline-none focus:ring-2 focus:ring-indigo-400">
      </td>
      <td class="py-2.5 pr-4">
        <select id="branch-type-${b.id}"
          class="border border-slate-200 rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400">
          <option value="master"    ${b.branch_type === 'master'    ? 'selected' : ''}>สาขาหลัก</option>
          <option value="franchise" ${b.branch_type === 'franchise' ? 'selected' : ''}>แฟรนไชส์</option>
        </select>
      </td>
      <td class="py-2.5 text-right">
        <button onclick="saveBranch(${b.id})"
          class="px-3 py-1.5 text-xs bg-indigo-100 hover:bg-indigo-200 text-indigo-700 font-medium rounded-lg transition-colors">
          บันทึก
        </button>
        <span id="branch-status-${b.id}" class="text-xs ml-2"></span>
      </td>
    </tr>
  `).join('');
}

async function saveBranch(branchId) {
  const inp    = document.getElementById(`branch-name-${branchId}`);
  const typeSel = document.getElementById(`branch-type-${branchId}`);
  const status = document.getElementById(`branch-status-${branchId}`);
  const name   = inp.value.trim();
  const branch_type = typeSel?.value || 'master';
  if (!name) return;
  try {
    const res  = await fetch(`${API}/api/branches/${branchId}`, {
      method: 'PUT',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ name }),
    });
    if (!res.ok) { const d = await res.json(); throw new Error(d.detail); }
    await fetch(`${API}/api/branches/${branchId}/type`, {
      method: 'PUT',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ branch_type }),
    });
    status.textContent = '✓'; status.className = 'text-xs ml-2 text-emerald-600';
    setTimeout(() => { status.textContent = ''; }, 2000);
    // refresh topbar branch list in case grouping changed
    if (typeof onGroupChange === 'function') onGroupChange();
  } catch(e) {
    status.textContent = '✗ ' + e.message; status.className = 'text-xs ml-2 text-red-500';
  }
}


// ── Deactivate ───────────────────────────────────────────────

let _deactivateSkus = [];  // cached full list

async function loadDeactivate() {
  const [branches, skus] = await Promise.all([
    api(`${API}/api/branches?include_inactive=true`),
    api(`${API}/api/skus?include_inactive=true`),
  ]);

  // Branches
  const bl = document.getElementById('deactivateBranchList');
  if (!branches.length) {
    bl.innerHTML = '<p class="text-xs text-slate-400">No branches found.</p>';
  } else {
    bl.innerHTML = branches.map(b => `
      <div class="flex items-center justify-between py-2 border-b border-slate-100 last:border-0">
        <span class="text-sm text-slate-700">${esc(b.name)}
          <span class="ml-1 text-xs text-slate-400">#${b.id}</span>
        </span>
        <label class="relative inline-flex items-center cursor-pointer gap-2">
          <input type="checkbox" ${b.active ? 'checked' : ''}
            onchange="toggleBranchActive(${b.id}, this.checked)"
            class="sr-only peer">
          <div class="w-10 h-5 bg-slate-200 peer-checked:bg-indigo-600 rounded-full transition-colors
                      after:content-[''] after:absolute after:top-0.5 after:left-0.5
                      after:bg-white after:rounded-full after:h-4 after:w-4
                      after:transition-all peer-checked:after:translate-x-5"></div>
          <span class="text-xs text-slate-500 w-14">${b.active ? 'Active' : 'Inactive'}</span>
        </label>
      </div>
    `).join('');
  }

  // SKUs
  _deactivateSkus = skus;
  renderSkuDeactivateList();
}

function renderSkuDeactivateList() {
  const q = (document.getElementById('deactivateSkuSearch')?.value || '').toLowerCase();
  const filtered = q
    ? _deactivateSkus.filter(s =>
        s.sku_code.toLowerCase().includes(q) ||
        (s.description || '').toLowerCase().includes(q) ||
        (s.category || '').toLowerCase().includes(q)
      )
    : _deactivateSkus;

  const empty = document.getElementById('deactivateSkuEmpty');
  const list  = document.getElementById('deactivateSkuList');

  if (!filtered.length) {
    list.innerHTML = '';
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');

  list.innerHTML = filtered.map(s => `
    <div class="flex items-center justify-between py-1.5 border-b border-slate-100 last:border-0">
      <div class="min-w-0 mr-3">
        <span class="text-sm font-medium text-slate-700">${s.sku_code}</span>
        <span class="text-xs text-slate-400 ml-2">${esc(s.description || '')}</span>
        ${s.category ? `<span class="ml-1 text-xs text-indigo-400">${s.category}</span>` : ''}
      </div>
      <label class="relative inline-flex items-center cursor-pointer gap-2 flex-shrink-0">
        <input type="checkbox" ${s.active ? 'checked' : ''}
          onchange="toggleSkuActive('${s.sku_code}', this.checked, this)"
          class="sr-only peer">
        <div class="w-10 h-5 bg-slate-200 peer-checked:bg-indigo-600 rounded-full transition-colors
                    after:content-[''] after:absolute after:top-0.5 after:left-0.5
                    after:bg-white after:rounded-full after:h-4 after:w-4
                    after:transition-all peer-checked:after:translate-x-5"></div>
        <span class="text-xs text-slate-500 w-14">${s.active ? 'Active' : 'Inactive'}</span>
      </label>
    </div>
  `).join('');
}

async function toggleBranchActive(branchId, active) {
  try {
    await fetch(`${API}/api/branches/${branchId}/active`, {
      method: 'PUT',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ active }),
    });
    // Update cached list
    const b = _deactivateSkus; // branches are separate — reload
    showToast(active ? '✓ Branch activated' : '⊘ Branch deactivated', active ? 'good' : 'warn', 2500);
    // Refresh branch dropdown throughout app
    if (typeof loadBranches === 'function') loadBranches();
    // Re-render label in place — reload the section
    const branches = await api(`${API}/api/branches?include_inactive=true`);
    const bl = document.getElementById('deactivateBranchList');
    bl.innerHTML = branches.map(b => `
      <div class="flex items-center justify-between py-2 border-b border-slate-100 last:border-0">
        <span class="text-sm text-slate-700">${esc(b.name)}
          <span class="ml-1 text-xs text-slate-400">#${b.id}</span>
        </span>
        <label class="relative inline-flex items-center cursor-pointer gap-2">
          <input type="checkbox" ${b.active ? 'checked' : ''}
            onchange="toggleBranchActive(${b.id}, this.checked)"
            class="sr-only peer">
          <div class="w-10 h-5 bg-slate-200 peer-checked:bg-indigo-600 rounded-full transition-colors
                      after:content-[''] after:absolute after:top-0.5 after:left-0.5
                      after:bg-white after:rounded-full after:h-4 after:w-4
                      after:transition-all peer-checked:after:translate-x-5"></div>
          <span class="text-xs text-slate-500 w-14">${b.active ? 'Active' : 'Inactive'}</span>
        </label>
      </div>
    `).join('');
  } catch(e) {
    showToast('Failed to update branch: ' + e.message, 'bad', 4000);
  }
}

async function toggleSkuActive(skuCode, active, checkbox) {
  try {
    await fetch(`${API}/api/skus/${encodeURIComponent(skuCode)}/active`, {
      method: 'PUT',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ active }),
    });
    // Update cache
    const sk = _deactivateSkus.find(s => s.sku_code === skuCode);
    if (sk) sk.active = active;
    // Update label next to the toggle
    const label = checkbox?.closest('label')?.querySelector('span.text-slate-500');
    if (label) label.textContent = active ? 'Active' : 'Inactive';
    showToast(active ? `✓ ${skuCode} activated` : `⊘ ${skuCode} deactivated`, active ? 'good' : 'warn', 2000);
  } catch(e) {
    showToast('Failed to update SKU: ' + e.message, 'bad', 4000);
    if (checkbox) checkbox.checked = !active; // revert
  }
}

// ── Preferences ──────────────────────────────────────────────

async function loadPreferences() {
  const [settings, branches] = await Promise.all([
    api(`${API}/api/settings`),
    api(`${API}/api/branches`),
  ]);

  // Populate branch dropdown
  const branchSel = document.getElementById('prefDefaultBranch');
  branchSel.innerHTML = '<option value="">ทุกสาขา</option>';
  branches.forEach(b => {
    const opt = document.createElement('option');
    opt.value = b.name; opt.textContent = b.name;
    branchSel.appendChild(opt);
  });

  branchSel.value = settings.default_branch || '';
  document.getElementById('prefDefaultMonth').value  = settings.default_month  || '';
  document.getElementById('prefFiscalStart').value   = settings.fiscal_year_start || '1';
  document.getElementById('prefDiscountPolicy').value = settings.discount_policy_pct || '2.5';
  document.getElementById('prefFoodBandLo').value = settings.food_cost_band_lo || '55';
  document.getElementById('prefFoodBandHi').value = settings.food_cost_band_hi || '70';
}

async function savePreferences() {
  const status = document.getElementById('prefSaveStatus');
  const body = {
    default_branch:      document.getElementById('prefDefaultBranch').value,
    default_month:       document.getElementById('prefDefaultMonth').value,
    fiscal_year_start:   document.getElementById('prefFiscalStart').value,
    discount_policy_pct: document.getElementById('prefDiscountPolicy').value || '2.5',
    food_cost_band_lo:   document.getElementById('prefFoodBandLo').value || '55',
    food_cost_band_hi:   document.getElementById('prefFoodBandHi').value || '70',
  };
  try {
    status.textContent = 'Saving…'; status.className = 'text-sm text-slate-400';
    await fetch(`${API}/api/settings`, {
      method: 'PUT',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    status.textContent = '✓ Saved'; status.className = 'text-sm text-emerald-600';
    setTimeout(() => { status.textContent = ''; }, 2500);
    // settings feed other tabs (the discount ceiling → Discounts, Management Report);
    // drop every tab's "same filter, skip the fetch" key so the next open refetches
    Object.keys(_tabLastKey).forEach(k => { delete _tabLastKey[k]; });
  } catch(e) {
    status.textContent = '✗ ' + e.message; status.className = 'text-sm text-red-600';
  }
}


// ════════════════════════════════════════════════════════════════
// FEATURE 1: PERIOD COMPARISON (A vs B)
// ════════════════════════════════════════════════════════════════
function initCompare() {
  // populate month selects
  const fillMonthSel = (id, defaultIdx) => {
    const sel = document.getElementById(id);
    sel.innerHTML = '';
    allMonths.forEach(m => {
      const o = document.createElement('option');
      o.value = m; o.textContent = fmtMonth(m); sel.appendChild(o);
    });
    if (allMonths.length && defaultIdx != null) {
      const idx = defaultIdx < 0 ? allMonths.length + defaultIdx : defaultIdx;
      sel.value = allMonths[Math.max(0, Math.min(allMonths.length-1, idx))];
    }
  };
  // default: A = last month, B = 2nd-to-last, C = 3rd-to-last, D = 4th-to-last
  fillMonthSel('cmpA_mfrom', -1); fillMonthSel('cmpA_mto', -1);
  fillMonthSel('cmpB_mfrom', -2); fillMonthSel('cmpB_mto', -2);
  fillMonthSel('cmpC_mfrom', -3); fillMonthSel('cmpC_mto', -3);
  fillMonthSel('cmpD_mfrom', -4); fillMonthSel('cmpD_mto', -4);
  updateCompareUI();
}

// Period colours: A=indigo, B=amber, C=emerald, D=rose
const CMP_COLORS = {
  A: { hex:'#4a70b0', badge:'text-indigo-700', bg:'#e8eef7' },
  B: { hex:'#f59e0b', badge:'text-amber-700',  bg:'#fef3c7' },
  C: { hex:'#10b981', badge:'text-emerald-700',bg:'#ecfdf5' },
  D: { hex:'#f43f5e', badge:'text-rose-700',   bg:'#fff1f2' },
};

function updateCompareUI() {
  ['A','B','C','D'].forEach(side => {
    const typeEl = document.getElementById(`cmp${side}_type`);
    if (!typeEl) return;
    const type = typeEl.value;
    document.getElementById(`cmp${side}_month`).classList.toggle('hidden', type !== 'month');
    document.getElementById(`cmp${side}_date`).classList.toggle('hidden', type !== 'date');
    // C & D: toggle enabled state
    if (side === 'C' || side === 'D') {
      const enabled = document.getElementById(`cmp${side}_enabled`).checked;
      document.getElementById(`cmp${side}_card`).style.opacity = enabled ? '1' : '0.45';
      ['type','mfrom','mto','dfrom','dto'].forEach(k => {
        const el = document.getElementById(`cmp${side}_${k}`);
        if (el) el.disabled = !enabled;
      });
    }
  });
}

async function runComparison() {
  const params = new URLSearchParams();
  ['A','B','C','D'].forEach(side => {
    if ((side === 'C' || side === 'D') && !document.getElementById(`cmp${side}_enabled`).checked) return;
    const prefix = side.toLowerCase() + '_';
    const type = document.getElementById(`cmp${side}_type`).value;
    if (type === 'month') {
      const mf = document.getElementById(`cmp${side}_mfrom`).value;
      const mt = document.getElementById(`cmp${side}_mto`).value;
      if (mf) params.set(prefix + 'month_from', mf);
      if (mt) params.set(prefix + 'month_to',   mt);
    } else {
      const df = document.getElementById(`cmp${side}_dfrom`).value;
      const dt = document.getElementById(`cmp${side}_dto`).value;
      if (df) params.set(prefix + 'date_from', df);
      if (dt) params.set(prefix + 'date_to',   dt);
    }
  });
  if (getBranch()) params.set('branch', getBranch());
  document.getElementById('cmpStatus').textContent = 'กำลังโหลด…';
  try {
    const data = await api(`${API}/api/analysis/period_compare?${params}`);
    // PR-PP2: a period that runs past the last closed day is to date, and the others are cut to the same first n days
    document.getElementById('cmpStatus').textContent = data && data.to_date
      ? `ช่วงที่ยังไม่จบ: ทุกช่วงเทียบ ${data.n_days} วันแรกเท่ากัน (ข้อมูลถึง ${fmtDateTh(data.anchor)})` : '';
    Object.entries((data && data.windows) || {}).forEach(([k, w]) => { if (data[k]) data[k].label = periodLabelTh(w[0], w[1]); });
    renderComparison(data);
  } catch(e) {
    document.getElementById('cmpStatus').textContent = '✗ ' + e.message;
  }
}

function renderComparison(data) {
  document.getElementById('cmpResults').classList.remove('hidden');

  // Active periods only (c/d are null when not requested)
  const allPeriods = ['a','b','c','d'];
  const periods = allPeriods
    .filter(k => data[k] != null)
    .map(k => ({
      key:   k,
      label: data[k].label || ('Period ' + k.toUpperCase()),
      color: CMP_COLORS[k.toUpperCase()].hex,
      bg:    CMP_COLORS[k.toUpperCase()].bg,
    }));

  const shortLabel = p => {
    // e.g. "2026-05 → 2026-05" → "2026-05"  or  "2026-05 → 2026-07" → "May–Jul"
    const parts = p.label.split('→').map(s => s.trim());
    return parts[0] === parts[1] ? parts[0] : parts.join('→');
  };

  // ── KPI comparison table ──
  const metrics = [
    { key:'revenue',        label:'Revenue ',    fmt: v => fmt(v) },
    { key:'tx_count',       label:'บิล',   fmt: v => fmtN(v) },
    { key:'customer_count', label:'ลูกค้า',      fmt: v => fmtN(v) },
    { key:'avg_ticket',     label:'ยอดเฉลี่ยต่อบิล', fmt: v => fmtN(v) },
    { key:'discount',       label:'Discounts ',  fmt: v => fmt(v) },
  ];

  // Header: Metric | A | B | C? | D? | Δ A-B | Δ A-C? | Δ A-D?
  const headValCols = periods.map(p =>
    `<th class="py-2 px-3 text-right font-bold" style="color:${textInk(p.color)}">${shortLabel(p)}</th>`
  ).join('');
  const headDeltaCols = periods.slice(1).map(p =>
    `<th class="py-2 px-2 text-right text-slate-400">Δ vs ${p.key.toUpperCase()}</th>`
  ).join('');
  document.getElementById('cmpKpiHead').innerHTML =
    `<tr class="text-xs border-b-2 border-slate-200" style="background:var(--bg-subtle)">
       <th class="py-2 px-3 text-left text-slate-500">ตัวชี้วัด</th>
       ${headValCols}${headDeltaCols}
     </tr>`;

  const aData = data['a'] || {};
  document.getElementById('cmpKpiBody').innerHTML = metrics.map(m => {
    const valCols = periods.map(p => {
      const v = data[p.key]?.[m.key] || 0;
      return `<td class="py-2 px-3 text-right font-semibold">${m.fmt(v)}</td>`;
    }).join('');
    const aVal = aData[m.key] || 0;
    const deltaCols = periods.slice(1).map(p => {
      const bv = data[p.key]?.[m.key] || 0;
      const d  = bv ? ((aVal - bv) / bv * 100) : null;
      return `<td class="py-2 px-2 text-right">${badge(d)}</td>`;
    }).join('');
    return `<tr class="border-t border-slate-100 hover:bg-slate-50 text-xs">
      <td class="py-2 px-3 font-medium text-slate-500">${m.label}</td>
      ${valCols}${deltaCols}
    </tr>`;
  }).join('');

  // ── Branch grouped-bar chart ──
  const topBranches = data.branch_changes.slice(0, 10);
  const branchLabels = topBranches.map(c => c.branch);
  mkChart('cmpBranchChart', {
    type: 'bar',
    data: {
      labels: branchLabels,
      datasets: periods.map(p => ({
        label: shortLabel(p),
        data:  topBranches.map(c => c[`revenue_${p.key}`] || 0),
        backgroundColor: p.color + 'cc',
        borderColor:     p.color,
        borderWidth: 1,
        borderRadius: 4,
      })),
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position:'bottom' }, datalabels: { display:false } },
      scales: {
        x: { ticks: { font:{ size:9 } } },
        y: { ticks: { callback: v => fmt(v) } },
      },
    },
  });

  // ── SKU table ──
  // Merge SKU lists across periods
  const skuMap = {};
  periods.forEach(p => {
    (data[p.key]?.top_skus || []).forEach(s => {
      if (!skuMap[s.sku_code]) skuMap[s.sku_code] = { sku_code: s.sku_code };
      skuMap[s.sku_code][p.key + '_rev'] = s.revenue;
    });
  });
  const allSkus = Object.values(skuMap)
    .sort((x, y) => (y.a_rev || 0) - (x.a_rev || 0))
    .slice(0, 15);

  const skuHeadCols = periods.map(p =>
    `<th class="py-2 px-2 text-right font-semibold" style="color:${textInk(p.color)}">${shortLabel(p)}</th>`
  ).join('');
  document.getElementById('cmpSkuHead').innerHTML =
    `<tr class="text-left text-xs border-b border-slate-200" style="background:var(--bg-subtle)">
       <th class="py-2 pr-3 text-slate-500">SKU</th>
       ${skuHeadCols}
       <th class="py-2 px-2 text-right text-slate-400">Δ A-B</th>
     </tr>`;

  document.getElementById('cmpSkuBody').innerHTML = allSkus.map(s => {
    const valCols = periods.map(p =>
      `<td class="py-2 px-2 text-right">${s[p.key+'_rev'] ? fmt(s[p.key+'_rev']) : '<span class="text-slate-300">—</span>'}</td>`
    ).join('');
    const aRev = s.a_rev || 0, bRev = s.b_rev || 0;
    const d = bRev ? ((aRev - bRev) / bRev * 100) : null;
    return `<tr class="border-t border-slate-100 hover:bg-slate-50 text-xs">
      <td class="py-2 pr-3 font-mono text-indigo-600 text-[10px]">${s.sku_code}</td>
      ${valCols}
      <td class="py-2 px-2 text-right">${badge(d)}</td>
    </tr>`;
  }).join('');

  // ── Branch rank-shift table ──
  const rankHeadCols = periods.map(p =>
    `<th class="py-2 px-2 text-center font-semibold" style="color:${textInk(p.color)}">#${p.key.toUpperCase()}</th>
     <th class="py-2 px-2 text-right font-semibold" style="color:${textInk(p.color)}">${shortLabel(p)}</th>`
  ).join('');
  const hasShift = periods.length >= 2;

  document.getElementById('cmpRankShift').innerHTML = `
    <table class="text-xs w-full" id="cmpRankShiftTable">
      <thead>
        <tr class="text-xs border-b-2 border-slate-200" style="background:var(--bg-subtle)">
          <th class="py-2 px-3 text-left text-slate-500">สาขา</th>
          ${rankHeadCols}
          ${hasShift ? '<th class="py-2 px-2 text-center text-slate-400">ย้าย A→B</th><th class="py-2 px-2 text-right text-slate-400">Δ A-B</th>' : ''}
        </tr>
      </thead>
      <tbody>
        ${data.branch_changes.map(c => {
          const revCols = periods.map(p => {
            const rank = c[`rank_${p.key}`];
            const rev  = c[`revenue_${p.key}`] || 0;
            return `<td class="py-2 px-2 text-center">${rank ?? '—'}</td>
                    <td class="py-2 px-2 text-right">${fmt(rev)}</td>`;
          }).join('');
          const shift = c.rank_shift ?? 0;
          const arrow = shift > 0
            ? `<span class="text-emerald-600">▲${shift}</span>`
            : shift < 0
              ? `<span class="text-red-500">▼${Math.abs(shift)}</span>`
              : `<span class="text-slate-400">—</span>`;
          const shiftCols = hasShift
            ? `<td class="py-2 px-2 text-center">${arrow}</td>
               <td class="py-2 px-2 text-right">${badge(c.delta_b_pct)}</td>`
            : '';
          return `<tr class="border-t border-slate-100 hover:bg-slate-50">
            <td class="py-2 px-3 font-semibold">${esc(c.branch)}</td>
            ${revCols}${shiftCols}
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;
}

function exportComparison() {
  exportTable('cmpRankShiftTable', 'period_comparison.csv');
}

// ════════════════════════════════════════════════════════════════
// FEATURE 2: ANOMALY / INSIGHT BANNER
// ════════════════════════════════════════════════════════════════
async function loadAnomalyBanner() {
  const banner = document.getElementById('anomalyBanner');
  if (!banner) return;
  const params = new URLSearchParams();
  if (getBranch())    params.set('branch',     getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to',   getMonthTo());
  try {
    const data = await api(`${API}/api/analysis/anomalies?${params}`);
    if (!data.insights || data.insights.length === 0) {
      banner.classList.add('hidden');
      return;
    }
    renderInsightBox(banner, data);
  } catch(e) { banner.classList.add('hidden'); }
}

// PR-OB (owner 2026-10-24 "ข้อสังเกตุทำให้ดูง่าย อ่านชัดได้ไหมครับ"): the box was one solid pill per sentence (16 of them, one for every
// month the food cost moved). Now: a header (period chip, "?" for the basis, ✕), three KPI tiles (revenue, fastest-growing branch, biggest
// drop) and one food-cost block (the latest full month big, a small bar chart of the monthly %, every month behind ดูทุกเดือน). Colour is
// meaning on the figure and the arrow only (good / bad ink); the text keeps the theme inks. It reads the structured fields of
// /api/analysis/anomalies (kind, delta, prev, amount, ...) and its cost_series; a tile or a block with no data is not drawn.
const _insSign = (v, dp) => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(dp == null ? 1 : dp);
const _insTone = (d, upGood) => (d == null || d === 0) ? 'flat' : ((d > 0) === upGood ? 'good' : 'bad');
const _insArrow = d => d > 0 ? '▲' : d < 0 ? '▼' : '';
// "มิ.ย. 2568" -> "มิ.ย. 68" (an axis tick)
const _insTick = s => String(s || '').replace(/\s(\d{2})(\d{2})$/, ' $2');
function _insDrill(kind, branch) {
  if (!tabAllowed('dailysales')) return;
  if (branch) { const sel = document.getElementById('selBranch'); if (sel) sel.value = branch; }
  switchTab('dailysales');
}
// one to-date range ["2026-10-01", "2026-10-19"] -> "1–19 ต.ค. 2569" (Buddhist year, as the data chip of the top bar)
function _insRange(r) {
  if (!Array.isArray(r) || r.length < 2) return '';
  const p = x => String(x).split('-').map(Number);
  const [y1, m1, d1] = p(r[0]), [y2, m2, d2] = p(r[1]);
  const mon = (m, y) => `${_TH_MON_SHORT[m - 1]} ${y + 543}`;
  return (y1 === y2 && m1 === m2) ? `${d1}–${d2} ${mon(m2, y2)}` : `${d1} ${mon(m1, y1)} – ${d2} ${mon(m2, y2)}`;
}
// owner 2026-10-24: every sales tile compares to-date with the period before AND the same period last year
function _insCmp(lbl, d) {
  const v = d != null ? `<span class="ins-delta ins-${_insTone(d, true)}">${_insArrow(d)} ${_insSign(d)}%</span>` : '<span class="ins-delta ins-flat">—</span>';
  return `<span class="ins-cmp"><span class="ins-cmp-l">${lbl}</span>${v}</span>`;
}
function _insTile(i) {
  const isRev = i.kind === 'revenue_mom';
  const big = isRev ? `฿${fmtN(i.amount)}` : esc(i.branch || '');
  const base = `ก่อนหน้า ฿${fmtN(i.prev)}${i.prev_year ? ` · ปีก่อน ฿${fmtN(i.prev_year)}` : ''}`;
  const go = tabAllowed('dailysales');
  const rg = i.ranges || {};
  const tip = rg.current ? `${_insRange(rg.current)} เทียบ ${_insRange(rg.previous)} และ ${_insRange(rg.last_year)}` : '';
  const inner = `<span class="ins-lbl">${esc(i.label || '')}</span>
    <span class="ins-big${isRev ? '' : ' ins-name'}">${big}</span>${isRev ? '' : `<span class="ins-amt">฿${fmtN(i.amount)}</span>`}
    ${_insCmp('เทียบเดือนก่อน', i.delta)}${_insCmp('เทียบปีก่อน', i.delta_yoy)}
    <span class="ins-sub">${base}</span>${go ? '<span class="ins-go" aria-hidden="true">›</span>' : ''}`;
  return go
    ? `<button type="button" class="ins-tile" data-ins-kind="${esc(i.kind)}" data-ins-branch="${esc(i.branch || '')}" title="${esc(tip ? tip + ' · ' : '')}ดูยอดขายรายวัน${i.branch ? ' · ' + esc(i.branch) : ''}">${inner}</button>`
    : `<div class="ins-tile"${tip ? ` title="${esc(tip)}"` : ''}>${inner}</div>`;
}
// the monthly food-cost % as bars from zero: red = up from the month before, green = down (the cost reads "up = worse"); value labels on
// the latest month and the highest / lowest only, placed by the glance layer's rule (glanceLabelSpot: latest first; a label with no room is dropped)
function _insCostChart(series, width) {
  const n = series.length, H = 96;
  const max = Math.max(...series.map(r => r.pct)) || 1;
  const top = max * 1.18;
  const bw = width / n;
  const iLast = n - 1;
  let iHi = 0, iLo = 0;
  series.forEach((r, k) => { if (r.pct > series[iHi].pct) iHi = k; if (r.pct < series[iLo].pct) iLo = k; });
  const placed = [], labels = [];
  [iLast, iHi, iLo].forEach(k => {
    if (labels.some(l => l.k === k)) return;
    const txt = series[k].pct.toFixed(1) + '%';
    const w = txt.length * 7 + 4, y = H - series[k].pct / top * H - 4;
    const s = typeof glanceLabelSpot === 'function' ? glanceLabelSpot(placed, { x: (k + 0.5) * bw, y, w, align: 'center' }, { left: 0, right: width }) : { x: (k + 0.5) * bw, cx: (k + 0.5) * bw };
    if (!s) return;
    placed.push({ x: s.cx, y, w });
    labels.push({ k, txt, x: s.x, y });
  });
  const ticks = n > 6 ? [0, Math.floor(iLast / 2), iLast] : series.map((_, k) => k);
  const bars = series.map((r, k) => {
    const tone = _insTone(r.delta, false);
    const t = `${r.month_th} · ${r.pct.toFixed(1)}%${r.delta != null ? ` · ${_insArrow(r.delta)} ${_insSign(r.delta)} จุด` : ''}`;
    return `<div class="ins-col" title="${esc(t)}"><div class="ins-bar ins-bar-${tone}${k === iLast ? ' ins-bar-now' : ''}" style="height:${(r.pct / top * 100).toFixed(1)}%"></div></div>`;
  }).join('');
  const lab = labels.map(l => `<span class="ins-val${l.k === iLast ? ' ins-val-now' : ''}" style="left:${l.x.toFixed(1)}px;top:${Math.max(0, l.y - 14).toFixed(1)}px">${l.txt}</span>`).join('');
  const tk = ticks.map(k => `<span class="ins-tick${k === iLast ? ' ins-tick-now' : ''}" style="left:${((k + 0.5) * bw).toFixed(1)}px">${esc(_insTick(series[k].month_th))}</span>`).join('');
  const hi = series[iHi], lo = series[iLo];
  const aria = `ต้นทุนอาหารรายเดือน ${series[0].month_th} ถึง ${series[iLast].month_th}: ล่าสุด ${series[iLast].pct.toFixed(1)}% · สูงสุด ${hi.month_th} ${hi.pct.toFixed(1)}% · ต่ำสุด ${lo.month_th} ${lo.pct.toFixed(1)}%`;
  return `<div class="ins-chart" role="img" aria-label="${esc(aria)}"><div class="ins-plot" style="height:${H}px">${bars}${lab}</div><div class="ins-ticks">${tk}</div></div>`;
}
function _insAllTable(series) {
  const rows = series.slice().reverse();
  const half = Math.ceil(rows.length / 2);
  const tbl = part => `<table class="ins-tbl"><thead><tr><th>เดือน</th><th>% ของรายได้</th><th>เปลี่ยน</th></tr></thead><tbody>${part.map(r => {
    const tone = _insTone(r.delta, false);
    return `<tr><td>${esc(r.month_th)}</td><td class="ins-num">${r.pct.toFixed(1)}%</td><td class="ins-num">${r.delta != null ? `<span class="ins-${tone}">${_insArrow(r.delta)} ${_insSign(r.delta)} จุด</span>` : '—'}</td></tr>`;
  }).join('')}</tbody></table>`;
  return tbl(rows.slice(0, half)) + (rows.length > half ? tbl(rows.slice(half)) : '');
}
function renderInsightBox(box, data) {
  const meta = data.meta || {};
  const ins = data.insights || [];
  const tiles = ['revenue_mom', 'branch_top', 'branch_drop'].map(k => ins.find(i => i.kind === k)).filter(Boolean);
  const series = (data.cost_series || []).filter(r => r && r.pct != null);
  if (!tiles.length && !series.length) { box.classList.add('hidden'); return; }
  const costWord = stkText('ต้นทุน BoM ลด').replace(/\s*ลด$/, '');   // the stock-mode wording: ต้นทุนอาหาร (full mode keeps ต้นทุน BoM)
  const basis = (meta.mtd_day ? `วันที่ 1–${meta.mtd_day} · ` : '') + 'เทียบเดือนก่อนและปีก่อน';
  const chip = meta.period_th ? `<span class="ins-chip">${esc(meta.period_th)} · ${esc(basis)}</span>` : '';
  const help = uiQ('overall:insights', 'ข้อสังเกต',
    `<p><b>รายได้และสาขา</b>: ${meta.ranges ? `${esc(_insRange(meta.ranges.current))} เทียบ ${esc(_insRange(meta.ranges.previous))} (เดือนก่อน) และ ${esc(_insRange(meta.ranges.last_year))} (ปีก่อน)` : `${esc(meta.period_th || 'เดือนล่าสุด')} เทียบเดือนก่อนและปีก่อน`}${meta.mtd_day ? ' · เดือนที่ยังไม่จบเทียบจำนวนวันเท่ากัน' : ''} · รายได้ไม่รวม VAT · ไม่มีข้อมูลปีก่อน แสดง —</p>
     <p>แสดงเมื่อเปลี่ยนตั้งแต่ 1.5% ขึ้นไป · เติบโตเร็วสุด / ลดมากสุด = สาขาที่ยอดเปลี่ยนมากที่สุดเทียบเดือนก่อนของสาขานั้น · แตะการ์ดเพื่อดูยอดขายรายวัน</p>
     ${series.length ? `<p><b>${esc(costWord)} % ของรายได้</b>: ${esc(costWord)}ของจานที่ขาย ÷ รายได้ของจานเดียวกัน · ตัวเลขใหญ่ = เดือนเดียวกับการ์ดด้านบน (เดือนที่ยังไม่จบนับถึงวันที่ปิดยอดล่าสุด) เทียบจำนวนวันเท่ากันของเดือนก่อนและปีก่อน เป็นจุด · ไม่มีต้นทุนของช่วงฐาน แสดง —${data.cost_now && data.cost_now.reasons && Object.values(data.cost_now.reasons).length ? ' (' + esc(Object.values(data.cost_now.reasons).join(' · ')) + ')' : ''}</p><p>กราฟนับเฉพาะเดือนที่จบแล้ว · แท่งสีแดง = สูงขึ้นจากเดือนก่อน · สีเขียว = ลดลง · ตัวเลขบนแท่ง = เดือนล่าสุด สูงสุด และต่ำสุด · ดูทุกเดือนได้ที่ "ดูทุกเดือน"</p>` : ''}`);
  const head = `<div class="ins-head"><span class="ins-title">ข้อสังเกต</span>${chip}<span class="ins-sp"></span>${help}
    <button type="button" class="ins-x" aria-label="ปิดข้อสังเกต" onclick="this.closest('#anomalyBanner').classList.add('hidden')">✕</button></div>`;
  const kpis = tiles.length ? `<div class="ins-kpis">${tiles.map(_insTile).join('')}</div>` : '';
  let cost = '';
  if (series.length) {
    const last = series[series.length - 1], prev = series.length > 1 ? series[series.length - 2] : null;
    const tone = _insTone(last.delta, false);
    // PR-PP3: the headline is the month of the tiles above (to date when not finished) with เทียบเดือนก่อน + เทียบปีก่อน in จุด
    // (cost_now, the one period pattern); without it, the latest closed month against the one before, as before
    const cn = data.cost_now && data.cost_now.pct != null ? data.cost_now : null;
    const cmpPp = (lbl, d) => `<span class="ins-cmp"><span class="ins-cmp-l">${lbl}</span>${d != null ? `<span class="ins-delta ins-${_insTone(d, false)}">${_insArrow(d)} ${_insSign(d)} จุด</span>` : '<span class="ins-delta ins-flat">—</span>'}</span>`;
    const ppOf = (a, b) => (a != null && b != null) ? Math.round((a - b) * 10) / 10 : null;
    const cnTip = cn && cn.period ? `${cn.period.label_th} เทียบ ${cn.period.prev_label_th} และ ${cn.period.ly_label_th}${Object.values(cn.reasons || {}).length ? ' · ' + Object.values(cn.reasons).join(' · ') : ''}` : '';
    cost = `<div class="ins-cost">
      <div class="ins-cost-l"${cnTip ? ` title="${esc(cnTip)}"` : ''}>
        <span class="ins-lbl">${esc(costWord)} % ของรายได้</span>
        <span class="ins-big">${(cn ? cn.pct : last.pct).toFixed(1)}%</span>
        ${cn ? cmpPp('เทียบเดือนก่อน', ppOf(cn.pct, cn.prev)) + cmpPp('เทียบปีก่อน', ppOf(cn.pct, cn.ly))
             : (last.delta != null ? `<span class="ins-delta ins-${tone}">${_insArrow(last.delta)} ${_insSign(last.delta)} จุด</span>` : '')}
        <span class="ins-sub">${cn && cn.period ? esc(cn.period.label_th) : `${esc(last.month_th)}${prev ? ` · เทียบ ${esc(prev.month_th)} (${prev.pct.toFixed(1)}%)` : ''}`}</span>
      </div>
      <div class="ins-cost-r" data-ins-chart="1"></div>
      <div class="ins-all-wrap">
        <button type="button" class="ins-more" aria-expanded="false">ดูทุกเดือน (${series.length})</button>
        <div class="ins-all hidden">${_insAllTable(series)}</div>
      </div>
    </div>`;
  }
  box.className = 'ins-box';
  box.innerHTML = head + kpis + cost;
  // the chart is laid out at the width it gets (labels are placed in pixels); last 16 months
  const cw = box.querySelector('[data-ins-chart]');
  if (cw) {
    const draw = () => { cw.innerHTML = _insCostChart(series.slice(-16), Math.max(160, cw.clientWidth || 480)); };
    draw();
    if (window.ResizeObserver && !box._insRO) {
      let wLast = cw.clientWidth;
      box._insRO = new ResizeObserver(() => { const c = box.querySelector('[data-ins-chart]'); if (c && c.clientWidth && Math.abs(c.clientWidth - wLast) > 8) { wLast = c.clientWidth; c.dispatchEvent(new Event('ins-redraw')); } });
      box._insRO.observe(box);
    }
    cw.addEventListener('ins-redraw', draw);
  }
  box.querySelectorAll('.ins-tile[data-ins-kind]').forEach(b => b.addEventListener('click', () => _insDrill(b.dataset.insKind, b.dataset.insBranch)));
  const more = box.querySelector('.ins-more');
  if (more) more.addEventListener('click', () => {
    const all = box.querySelector('.ins-all'), open = all.classList.toggle('hidden') === false;
    more.setAttribute('aria-expanded', String(open));
    more.textContent = open ? 'ซ่อน' : `ดูทุกเดือน (${series.length})`;
  });
}

// Action Center — consolidate anomalies + branch-pace (MTD-fair) into one
// prioritized "watch today" panel with drill-through. Reuses existing endpoints.
async function loadHeavyEaters(force) {
  const fromEl = document.getElementById('heFrom'), toEl = document.getElementById('heTo'),
        mcEl = document.getElementById('heMinCov');
  const p = new URLSearchParams();
  if (fromEl?.value) p.set('date_from', fromEl.value);
  if (toEl?.value)   p.set('date_to', toEl.value);
  if (mcEl?.value)   p.set('min_covers', mcEl.value);
  if (getBranch())   p.set('branch', getBranch());
  if (getGroup())    p.set('group', getGroup());
  const status = document.getElementById('heStatus');
  if (status) status.textContent = 'กำลังโหลด…';
  let d;
  try { d = await api(`${API}/api/sales/heavy-eaters?${p}`); }
  catch (e) { if (status) status.textContent = 'โหลดไม่สำเร็จ'; return; }
  if (fromEl && !fromEl.value && d.window?.lo) fromEl.value = d.window.lo;
  if (toEl && !toEl.value && d.window?.hi) toEl.value = d.window.hi;
  if (status) status.textContent = `${fmtN(d.n_bills || 0)} บิล · ${fmtN(d.total_covers || 0)} หัว · ข้อมูลมี ${d.window?.lo || '—'} ถึง ${d.window?.hi || '—'}`;

  const pc = d.percentiles || {};
  document.getElementById('heKpis').innerHTML = [
    { l: 'รายได้/หัว (เฉลี่ย)', v: fmt(d.avg_rev_head || 0), s: 'ex-VAT ต่อลูกค้า', c: '#0891b2' },
    { l: 'ต้นทุน(BoM)/หัว', v: fmt(d.avg_cogs_head || 0), s: `มัธยฐาน ${fmt(pc.p50 || 0)} · p90 ${fmt(pc.p90 || 0)}`, c: '#ef4444' },
    { l: 'มาร์จิน/หัว (เฉลี่ย)', v: fmt(d.avg_margin_head || 0), s: 'รายได้ − ต้นทุน ต่อหัว', c: '#10b981' },
    { l: 'เกณฑ์นักกินหนัก', v: fmt(d.heavy_threshold || 0), s: 'ต้นทุน/หัว ≥ p90', c: '#f59e0b' },
  ].map(k => `<div class="card p-4" style="border-top:3px solid ${k.c}"><p class="text-[11px] uppercase tracking-wide" style="color:var(--text-muted)">${k.l}</p><p class="text-2xl font-bold" style="color:${textInk(k.c)}">${k.v}</p><p class="text-[11px]" style="color:var(--text-muted)">${k.s}</p></div>`).join('');

  const H = d.heavy || {}, L = d.loss || {};
  document.getElementById('heCallouts').innerHTML = `
    <div class="card p-4" style="background:#fffbeb;border:1px solid #f59e0b">
      <p class="text-sm font-bold" style="color:var(--amber-ink)">นักกินหนัก (ต้นทุน/หัว หาง 10% บน)</p>
      <p class="text-2xl font-bold mt-1" style="color:var(--amber-ink)">${fmtN(H.bills || 0)} บิล <span class="text-sm font-normal text-slate-500">(${H.pct_bills || 0}% ของบิล)</span></p>
      <p class="text-xs text-slate-600 mt-1">กินต้นทุนรวม <b>${H.pct_cogs || 0}%</b> · ต้นทุน/หัว ${fmt(H.avg_cogs_head || 0)} · มาร์จิน/หัว เหลือเพียง <b style="color:${(H.avg_margin_head || 0) < 0 ? 'var(--bad-ink)' : 'var(--amber-ink)'}">${fmt(H.avg_margin_head || 0)}</b></p>
    </div>
    <div class="card p-4" style="background:#fef2f2;border:1px solid #ef4444">
      <p class="text-sm font-bold" style="color:var(--bad-ink)">บิลขาดทุนต่อหัว (มาร์จิน/หัว < 0)</p>
      <p class="text-2xl font-bold mt-1" style="color:var(--bad-ink)">${fmtN(L.bills || 0)} บิล <span class="text-sm font-normal text-slate-500">(${L.pct_bills || 0}% ของบิล)</span></p>
      <p class="text-xs text-slate-600 mt-1">ขาดทุนเฉลี่ย/หัว <b style="color:var(--bad-ink)">${fmt(L.avg_margin_head || 0)}</b> · รวมต้นทุน ${fmt(L.cogs || 0)} (${L.pct_cogs || 0}% ของต้นทุน)</p>
    </div>`;

  const hist = d.histogram || []; const maxc = Math.max(1, ...hist.map(h => h.count || 0));
  const thr = d.heavy_threshold || 0;
  document.getElementById('heHist').innerHTML = hist.map(h => {
    const w = Math.round(100 * (h.count || 0) / maxc);
    const lbl = h.hi == null ? `${fmtN(h.lo)}+` : `${fmtN(h.lo)}–${fmtN(h.hi)}`;
    const isHeavy = thr > 0 && h.lo >= thr;
    const col = isHeavy ? '#f59e0b' : '#38bdf8';
    return `<div class="flex items-center gap-2 mb-1">
      <span class="text-[10px] text-slate-400 text-right" style="width:104px">${lbl}</span>
      <div class="flex-1" style="background:var(--bg-subtle);border-radius:var(--r-md);overflow:hidden">
        <div style="width:${w}%;min-width:2px;height:16px;background:${col}"></div>
      </div>
      <span class="text-[10px] tabular-nums text-slate-500" style="width:54px">${fmtN(h.count || 0)}</span>
    </div>`;
  }).join('');

  document.getElementById('heBranchBody').innerHTML = (d.by_branch || []).map(r => {
    const hpCol = r.heavy_pct >= 15 ? '#ef4444' : r.heavy_pct >= 12 ? '#f59e0b' : '#64748b';
    const mCol = (r.margin_head || 0) < 0 ? '#ef4444' : '#10b981';
    return `<tr class="border-t border-slate-100">
      <td class="py-1.5 pr-2 font-medium">${esc(r.branch)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${fmtN(r.bills)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${fmtN(r.heavy_bills)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-bold" style="color:${textInk(hpCol)}">${r.heavy_pct}%</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${fmt(r.rev_head)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:var(--bad-ink)">${fmt(r.cogs_head)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-bold" style="color:${textInk(mCol)}">${fmt(r.margin_head)}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="7" class="text-center text-slate-400 py-4">ไม่มีข้อมูล</td></tr>';

  document.getElementById('heExBody').innerHTML = (d.examples || []).map(r => {
    const mCol = (r.margin_head || 0) < 0 ? '#ef4444' : '#10b981';
    return `<tr class="border-t border-slate-100">
      <td class="py-1.5 pr-2 text-slate-500">${esc(r.day)}</td>
      <td class="py-1.5 pr-2 font-medium">${esc(r.branch)}</td>
      <td class="py-1.5 pr-2 text-slate-400 text-[11px]">${esc(r.receipt)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${fmtN(r.covers)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${fmt(r.rev_head)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:var(--bad-ink)">${fmt(r.cogs_head)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-bold" style="color:${textInk(mCol)}">${fmt(r.margin_head)}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="7" class="text-center text-slate-400 py-4">ไม่มีข้อมูล</td></tr>';
}

async function loadActionCenter() {
  const body = document.getElementById('acBody'); if (!body) return;
  body.innerHTML = '<p class="text-sm text-slate-400 py-8 text-center">กำลังโหลด…</p>';
  const p = new URLSearchParams();
  if (getBranch()) p.set('branch', getBranch());
  if (getMonthFrom()) p.set('month_from', getMonthFrom());
  if (getMonthTo()) p.set('month_to', getMonthTo());
  const dp = new URLSearchParams({ gran: 'month' });
  if (getBranch()) dp.set('branch', getBranch());
  if (getGroup()) dp.set('group', getGroup());
  let an = { insights: [] }, dy = { branches: [] }, al = { flags: [] };
  try {
    [an, dy, al] = await Promise.all([
      api(`${API}/api/analysis/anomalies?${p}`),
      api(`${API}/api/sales/daily?${dp}`),
      api(`${API}/api/analysis/discount_alerts?${p}`).catch(() => ({ flags: [] })),
    ]);
  } catch (e) {}
  const items = [];
  // discount-risk flags (high severity) → action items, click → Discounts tab Alert Center
  (al.flags || []).filter(f => f.severity === 'high').slice(0, 6).forEach(f => items.push({
    sev: 'bad', msg: '⚑ ' + f.text, drill: 'discounts',
  }));
  (an.insights || []).forEach(ins => items.push({
    sev: ins.severity === 'good' ? 'good' : 'bad',
    msg: ins.message,
    drill: (ins.metric || '').includes('cost') ? 'bomcost' : 'dailysales',
  }));
  const isMtd = !!dy.mtd;
  const mtdLbl = dy.mom_basis === 'dow' ? 'MTD เทียบฐานวันในสัปดาห์เดือนก่อน' : 'MTD เทียบเดือนก่อน';
  const br = (dy.branches || []).filter(b => b.mom_pct != null);
  br.slice().sort((a, b) => a.mom_pct - b.mom_pct).slice(0, 3).forEach(b => {
    if (b.mom_pct <= -10) items.push({ sev: 'bad', msg: `▼ ${b.branch} ยอดตก ${Math.abs(b.mom_pct).toFixed(0)}%${isMtd ? ` (${mtdLbl})` : ''} — รายได้ ${fmt(b.revenue)}`, drill: 'dailysales' });
  });
  br.slice().sort((a, b) => b.mom_pct - a.mom_pct).slice(0, 2).forEach(b => {
    if (b.mom_pct >= 10) items.push({ sev: 'good', msg: `▲ ${b.branch} ยอดโต ${b.mom_pct.toFixed(0)}%${isMtd ? ' (MTD)' : ''}`, drill: 'dailysales' });
  });
  const bad = items.filter(i => i.sev === 'bad'), good = items.filter(i => i.sev === 'good');
  const card = (i, color) => `<div class="card p-3 mb-2 flex items-center justify-between gap-3 cursor-pointer hover:shadow-md transition-shadow" style="border:1px solid ${color}" onclick="switchTab('${i.drill}')">
    <span class="text-sm" style="color:var(--text-primary)">${esc(i.msg)}</span>
    <span class="text-[11px] text-indigo-500 whitespace-nowrap">ดู ›</span></div>`;
  body.innerHTML =
    `<div class="mb-5"><h3 class="text-sm font-bold mb-2" style="color:var(--bad-ink)">ต้องดู / แก้ (${bad.length})</h3>` +
    (bad.length ? bad.map(i => card(i, '#ef4444')).join('') : '<p class="text-xs text-slate-400">ไม่มีสัญญาณเตือน</p>') + '</div>' +
    `<div><h3 class="text-sm font-bold mb-2" style="color:var(--good-ink)">ไปได้ดี (${good.length})</h3>` +
    (good.length ? good.map(i => card(i, '#10b981')).join('') : '<p class="text-xs text-slate-400">—</p>') + '</div>';
  const st = document.getElementById('acStatus'); if (st) st.textContent = `รวม ${items.length} สัญญาณ · อัปเดตตามตัวกรองด้านบน`;
}

// ════════════════════════════════════════════════════════════════
// FEATURE 3: CSV EXPORT
// ════════════════════════════════════════════════════════════════
function exportTable(tableId, filename = 'export.csv') {
  const table = document.getElementById(tableId);
  if (!table) { uiNotice('Nothing to export'); return; }
  const rows = [];
  table.querySelectorAll('tr').forEach(tr => {
    const cells = [];
    tr.querySelectorAll('th, td').forEach(td => {
      let txt = td.innerText.replace(/\n/g, ' ').replace(/"/g, '""').trim();
      cells.push(`"${txt}"`);
    });
    rows.push(cells.join(','));
  });
  downloadCSV(rows.join('\n'), filename);
}


// RV-M: the formula guard of backend/xl_safe.py for every CSV the page writes (8 callers build their own text): a field that starts with
// = + - @ tab or CR - a POS reason "=HYPERLINK(...)", a typed note - opens in Excel as a formula. It gets a leading apostrophe; a number
// ("-12.5", "+3") and a lone "-" stay as they are. The text is split on its own CSV quoting (a quoted field may hold commas and newlines).
function csvGuard(text) {
  const out = [];
  let i = 0, row = [], f = '', q = false, quoted = false;
  const put = () => {
    const danger = f.length && '=+-@\t\r'.includes(f[0]) && f !== '-' && !/^[-+]?\d[\d,]*(\.\d+)?%?$/.test(f);
    const v = danger ? "'" + f : f;
    row.push(quoted || danger || /[",\n\r]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v);
    f = ''; quoted = false;
  };
  const s = String(text);
  for (; i < s.length; i++) {
    const c = s[i];
    if (q) {
      if (c === '"' && s[i + 1] === '"') { f += '"'; i++; }
      else if (c === '"') q = false;
      else f += c;
    } else if (c === '"') { q = true; quoted = true; }
    else if (c === ',') put();
    else if (c === '\n') { put(); out.push(row.join(',')); row = []; }
    else if (c === '\r' && s[i + 1] === '\n') { /* the \n ends the row */ }
    else f += c;
  }
  if (f.length || row.length || quoted) { put(); out.push(row.join(',')); }
  return out.join('\n');
}
function downloadCSV(text, filename) {
  const blob = new Blob(['﻿' + csvGuard(text)], { type: 'text/csv;charset=utf-8;' });
  const url  = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ════════════════════════════════════════════════════════════════
// FEATURE 4: BRANCH × PERIOD HEATMAP
// ════════════════════════════════════════════════════════════════
// Branch × period heatmap. Two readings of the same cells:
//  'value'  - the revenue figure in each cell, coloured by the cell vs THAT branch's own
//             average of complete periods (row-normalised: red = a weak period for that
//             branch, green = a strong one), so a small and a large branch compare;
//  'vsnorm' - the same colour with the % vs that average in the cell.
// One red → neutral → green scale (heatRG, js/01-core.js; owner 2026-10-25), the
// vs ก่อนหน้า column too.
// Rows run by the latest complete period; the period still in progress is
// marked MTD and kept out of every average and comparison.
let _heatMode = 'value', _heatData = null;
function setHeatMode(m) { _heatMode = m; _renderBranchHeatmap(); }
async function loadBranchHeatmap() {
  const wrap = document.getElementById('heatmapWrap');
  if (!wrap) return;
  const granularity = document.getElementById('heatmapGran').value;
  const params = new URLSearchParams({ granularity });
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to',   getMonthTo());
  _heatData = await api(`${API}/api/analysis/heatmap_branch?${params}`);
  _renderBranchHeatmap();
}
function _renderBranchHeatmap() {
  const wrap = document.getElementById('heatmapWrap');
  const data = _heatData || [];
  if (!wrap) return;
  if (!data.length) { wrap.innerHTML = '<p class="text-sm text-slate-400">No data.</p>'; return; }

  const periods = [...new Set(data.map(r => r.period))].sort();
  const map = {};
  data.forEach(r => { (map[r.branch] = map[r.branch] || {})[r.period] = r; });
  const rev = (br, p) => (map[br] && map[br][p] && map[br][p].revenue) || 0;
  const pi = glancePeriods(periods).partial;                 // the period still in progress, or -1
  const done = periods.filter((p, i) => i !== pi);
  const L = done[done.length - 1], Pv = done[done.length - 2];
  const branches = [...new Set(data.map(r => r.branch))]
    .sort((a, b) => (rev(b, L) - rev(a, L)) || a.localeCompare(b, 'th'));
  // A branch's opening month is a part-month (อยุธยา opened on 51K): it is kept
  // out of the branch's normal and not scored. A normal needs >= 3 full periods.
  const opening = {};
  branches.forEach(br => {
    const live = done.filter(p => rev(br, p) > 0);
    if (live.length < 2) return;
    const rest = live.slice(1).map(p => rev(br, p)).sort((a, b) => a - b), med = rest[Math.floor(rest.length / 2)];
    if (rev(br, live[0]) < 0.3 * med) opening[br] = live[0];
  });
  const avgOf = br => { const v = done.filter(p => p !== opening[br]).map(p => rev(br, p)).filter(x => x > 0); return v.length >= 3 ? v.reduce((a, b) => a + b, 0) / v.length : 0; };
  const avg = Object.fromEntries(branches.map(b => [b, avgOf(b)]));
  const fmtPer = p => fmtPeriod(p);
  const vs = (br, p) => { const a = avg[br], v = rev(br, p); return a > 0 && v > 0 && p !== opening[br] ? (v / a - 1) * 100 : null; };
  const pctTxt = x => `${x > 0 ? '+' : ''}${x.toFixed(0)}%`;

  // One sentence on the latest complete period, in the active reading.
  let sentence = '';
  if (L) {
    if (_heatMode === 'vsnorm') {
      const rows = branches.map(b => [b, vs(b, L)]).filter(r => r[1] != null);
      const below = rows.filter(r => r[1] < -5).length, above = rows.filter(r => r[1] > 5).length;
      const worst = rows.slice().sort((a, b) => a[1] - b[1])[0], best = rows.slice().sort((a, b) => b[1] - a[1])[0];
      sentence = rows.length ? `<b style="color:var(--text-primary)">${esc(fmtPer(L))}</b> · ต่ำกว่าค่าปกติ ${below} สาขา · สูงกว่า ${above} สาขา`
        + (worst && worst[1] < -5 ? ` · แย่สุด <b style="color:var(--text-primary)">${esc(worst[0])} ${pctTxt(worst[1])}</b>` : '')
        + (best && best[1] > 5 ? ` · ดีสุด <b style="color:var(--text-primary)">${esc(best[0])} ${pctTxt(best[1])}</b>` : '') : '';
    } else {
      const s2 = glanceRangeSentence(branches, branches.map(b => rev(b, L) || null), { fmt, below: true });
      sentence = s2 ? `<b style="color:var(--text-primary)">${esc(fmtPer(L))}</b> · ${s2}` : '';
    }
  }
  // owner 2026-10-25: both readings colour on ONE red → neutral → green scale (heatRG): the cell vs that branch's own normal, so a
  // small and a large branch compare; ยอดขาย keeps the baht figure in the cell, เทียบค่าปกติของสาขา shows the %.
  const legend = heatRGLegend();
  const seg = (m, label) => `<button onclick="setHeatMode('${m}')" class="text-xs px-2.5 py-1" style="border:1px solid ${_heatMode === m ? 'var(--accent)' : 'var(--border)'};background:${_heatMode === m ? 'var(--accent)' : 'transparent'};color:${_heatMode === m ? 'var(--on-accent)' : 'var(--text-secondary)'};border-radius:3px">${label}</button>`;

  const cell = (br, p, i) => {
    const r = map[br] && map[br][p], v = rev(br, p), inProg = i === pi;
    const x0 = inProg ? null : vs(br, p);
    const tip = `${br} · ${fmtPer(p)}${inProg ? ' (ยังไม่ครบ)' : ''} — ${fmtN(v)} · ${r?.tx_count || 0} tx` + (avg[br] ? ` · ค่าปกติ ${fmt(avg[br])}` : '')
      + (x0 != null ? ` · ${pctTxt(x0)} จากค่าปกติ` : '');
    if (!v) return `<td><div class="heatmap-cell" style="background:transparent;color:var(--text-muted);font-weight:400" title="${esc(tip)}">–</div></td>`;
    if (inProg) return `<td><div class="heatmap-cell" style="background:transparent;border:1px dashed var(--border-control,#7d8aa0);color:var(--text-secondary);font-weight:500" title="${esc(tip)}">${fmt(v)}</div></td>`;
    if (p === opening[br]) return `<td><div class="heatmap-cell" style="background:transparent;color:var(--text-muted);font-weight:500" title="${esc(tip)} · เดือนเปิดสาขา (ไม่ครบเดือน)">เปิด</div></td>`;
    const x = x0;
    const bg = heatRG(x);
    const txt = _heatMode === 'vsnorm' ? (x == null ? '–' : pctTxt(x)) : fmt(v);
    // no normal yet (under 3 full periods): the figure on a plain cell, never a colour it has not earned
    if (!bg) return `<td><div class="heatmap-cell" style="background:transparent;border:1px solid var(--border);color:var(--text-secondary);font-weight:500" title="${esc(tip)}">${txt}</div></td>`;
    return `<td><div class="heatmap-cell hrg" data-rg="${heatRGStep(x)}" style="background:${bg};color:${inkOn(bg)}" title="${esc(tip)}">${txt}</div></td>`;
  };
  const momChip = br => {
    const a = rev(br, L), b = rev(br, Pv);
    if (!a || !b) return '<td></td>';
    const d = (a / b - 1) * 100, bg = heatRG(d);
    return `<td class="hrg-mom-td"><div class="heatmap-cell hrg hrg-mom" data-rg="${heatRGStep(d)}" style="background:${bg};color:${inkOn(bg)}" title="${esc(`${br} · ${fmtPer(L)} เทียบ ${fmtPer(Pv)}: ${fmtN(a)} เทียบ ${fmtN(b)}`)}">${d >= 0 ? '▲' : '▼'} ${Math.abs(d).toFixed(1)}%</div></td>`;
  };

  wrap.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px">
      <div style="display:inline-flex;gap:4px">${seg('value', 'ยอดขาย')}${seg('vsnorm', 'เทียบค่าปกติของสาขา')}</div>
      ${legend}
    </div>
    ${sentence ? `<p style="font-size:12px;line-height:1.45;color:var(--text-secondary);margin:0 0 8px">${sentence}</p>` : ''}
    <table id="heatmapTable" class="heatmap-table">
      <thead><tr>
        <th></th>
        ${periods.map((p, i) => `<th>${fmtPer(p)}${i === pi ? '<br><span style="font-weight:600">MTD</span>' : ''}</th>`).join('')}
        ${L && Pv ? `<th style="padding-left:8px">${esc(fmtPer(L))}<br>vs ก่อนหน้า</th>` : ''}
      </tr></thead>
      <tbody>
        ${branches.map(br => `<tr>
          <td class="heatmap-branch-label branch-drillable" onclick="drillIntoBranch('${jsAttr(br)}')" title="คลิกเพื่อดูเฉพาะ ${jsAttr(br)}">${esc(br)}</td>
          ${periods.map((p, i) => cell(br, p, i)).join('')}
          ${L && Pv ? momChip(br) : ''}
        </tr>`).join('')}
      </tbody>
    </table>
    <p style="font-size:10.5px;color:var(--text-muted);margin-top:6px">เรียงตามยอด${L ? ' ' + esc(fmtPer(L)) : ''} · ${_heatMode === 'vsnorm' ? 'ค่าปกติ = ค่าเฉลี่ยของสาขานั้นเองในช่วงที่เลือก (ไม่รวมช่วงที่ยังไม่ครบ)' : 'สี = เทียบค่าเฉลี่ยของสาขานั้นเองในช่วงที่เลือก (แดง ต่ำกว่า · เขียว สูงกว่า)'}${pi >= 0 ? ' · MTD = ยังไม่ครบ ไม่นำมาเทียบ' : ''} · – = ไม่มียอด${Object.keys(opening).length ? ' · เปิด = เดือนที่เปิดสาขา (ไม่ครบเดือน)' : ''}</p>`;
}

function closeDrill() {}  // legacy no-op (old cached callers)
