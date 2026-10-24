// ─── 08-guest-buffet.js — slice 8/9 of the former app.js (P2 structure refactor) ───
// Sequential load order matters: index.html loads 01→09; classic scripts
// share one global scope, so this equals the original single file.

// ════════════════════════════════════════════════════════════════
// GUEST & SPH
// ════════════════════════════════════════════════════════════════
function _populateGuestMonthPicker() {
  const sel = document.getElementById('guestMonthPicker');
  if (!sel) return;
  const prev = sel.value;
  const months = [...allMonths].reverse();
  // Rebuild only if option count drifted
  if (sel.options.length !== months.length + 1) {
    sel.innerHTML = '<option value="">ทั้งช่วง (ตามฟิลเตอร์ด้านบน)</option>'
      + months.map(m => `<option value="${m}">${fmtMonth(m)}</option>`).join('');
    if (prev && months.includes(prev)) sel.value = prev;
  }
}

function _guestLocalMonth() {
  const el = document.getElementById('guestMonthPicker');
  return el ? el.value : '';
}

async function loadGuest(force) {
  _populateGuestMonthPicker();
  const localMonth = _guestLocalMonth();
  const _fk = _filterKey() + '|gm=' + localMonth;
  if (!force && _tabLastKey.guest === _fk) return;
  _tabLastKey.guest = _fk;

  const params = new URLSearchParams();
  if (getBranch())    params.set('branch', getBranch());
  if (localMonth) {
    params.set('month_from', localMonth);
    params.set('month_to',   localMonth);
  } else {
    if (getMonthFrom()) params.set('month_from', getMonthFrom());
    if (getMonthTo())   params.set('month_to',   getMonthTo());
  }

  // Range badge
  const badge = document.getElementById('guestRangeBadge');
  if (badge) {
    if (localMonth)            badge.textContent = 'โฟกัส: ' + fmtMonth(localMonth);
    else if (getMonthFrom() || getMonthTo())
                               badge.textContent = 'ช่วง: ' + (getMonthFrom() ? fmtMonth(getMonthFrom()) : '…') + ' – ' + (getMonthTo() ? fmtMonth(getMonthTo()) : 'ล่าสุด');
    else                       badge.textContent = 'ทุกเดือน';
  }

  const [sph, persona] = await Promise.all([
    api(`${API}/api/analysis/sph?${params}`),
    api(`${API}/api/analysis/persona?${params}`).catch(() => ({ overall: {}, party_sizes: [] })),
  ]);

  // KPI row
  const o = sph.overall || {};
  document.getElementById('guest-kpi-row').innerHTML = [
    { label: 'SPH เฉลี่ย', value: `${fmt(o.avg_sph||0)}`, sub: 'spend per head', color: '#4a70b0' },
    { label: 'Median SPH', value: `${fmt(o.median_sph||0)}`, sub: 'P50', color: '#0891b2' },
    { label: 'Avg Party Size', value: (persona.overall?.avg_party_size||0).toFixed(1), sub: 'pax / receipt', color: '#7a66b8' },
    { label: 'ยอดบิลเฉลี่ย', value: `${fmt(persona.overall?.avg_ticket||0)}`, sub: 'ต่อใบเสร็จ', color: 'var(--good-ink)' },
  ].map(k => `<div class="card p-4"><p class="kpi-label">${k.label}</p><p class="kpi-value" style="color:${textInk(k.color)}">${k.value}</p><p class="text-xs mt-1" style="color:var(--text-muted)">${k.sub}</p></div>`).join('');

  // SPH distribution — tier-based 100% stacked bar with percentile ruler
  renderSphDistribution(sph);

  // SPH by branch — sorted high→low, with avg/median/covers in the tooltip
  const byBranch = (sph.by_branch || []).slice().sort((a, b) => (b.avg_sph || 0) - (a.avg_sph || 0));
  const sphNetAvg = byBranch.length
    ? byBranch.reduce((s, b) => s + (b.avg_sph || 0) * (b.tx_count || 0), 0) / (byBranch.reduce((s, b) => s + (b.tx_count || 0), 0) || 1)
    : 0;
  mkChart('chartSphBranch', {
    type: 'bar',
    data: {
      labels: byBranch.map(b => b.branch),
      datasets: [{
        label: 'Avg SPH ',
        data: byBranch.map(b => b.avg_sph),
        backgroundColor: byBranch.map(b => (b.avg_sph || 0) >= sphNetAvg ? '#10b981' : '#f59e0b'),
        borderRadius: 4,
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        datalabels: { display: false },
        tooltip: { callbacks: { label: ctx => {
          const b = byBranch[ctx.dataIndex]; if (!b) return '';
          return [` เฉลี่ย: ${fmt(b.avg_sph)}`, ` มัธยฐาน: ${fmt(b.median_sph)}`,
                  ` บิล: ${fmtN(b.tx_count)}`, ` เทียบเครือ: ${((b.avg_sph/sphNetAvg-1)*100>=0?'+':'')}${((b.avg_sph/sphNetAvg-1)*100).toFixed(1)}%`];
        } } }
      },
      scales: { x: { ticks: { callback: v => fmt(v) } }, y: { ticks: { font: { size: 9 } } } }
    }
  });

  // SPH by daypart
  const dayparts = sph.by_daypart || [];
  mkChart('chartSphDaypart', {
    type: 'bar',
    data: {
      labels: dayparts.map(d => d.daypart),
      datasets: [{
        label: 'Avg SPH ',
        data: dayparts.map(d => d.avg_sph),
        backgroundColor: [PALETTE_6[1], PALETTE_6[2], PALETTE_6[3]],
        borderRadius: 4,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, datalabels: { display: false } },
      scales: { y: { beginAtZero: true, ticks: { callback: v => fmt(v) } } }
    }
  });

  // Party size (from persona)
  const parties = persona.party_sizes || [];
  if (parties.length) {
    mkChart('chartPartySph', {
      type: 'doughnut',
      data: {
        labels: parties.map(p => p.label + ' pax'),
        datasets: [{ data: parties.map(p => p.tx_count), backgroundColor: PALETTE_6 }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'right', labels: { font: { size: 11 } } }, datalabels: { display: false } }
      }
    });
  }

  // SPH trend — with zoom/pan + insight panel
  const trend = sph.trend || [];
  _sphTrendData = { labels: trend.map(t => t.period_month), values: trend.map(t => t.avg_sph) };
  mkChart('chartSphTrend', {
    type: 'line',
    data: {
      labels: _sphTrendData.labels.map(fmtMonth),
      datasets: [{
        label: 'Avg SPH ',
        data: _sphTrendData.values,
        borderColor: PALETTE_6[0],
        backgroundColor: PALETTE_6[0] + '22',
        tension: 0.3, fill: true, pointRadius: 3, pointHoverRadius: 5,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      onClick: (_e, items, chart) => {
        if (!items.length) return;
        const ym = _sphTrendData.labels[items[0].index];
        if (!ym) return;
        const sel = document.getElementById('guestMonthPicker');
        if (sel) { sel.value = ym; loadGuest(true); }
      },
      plugins: {
        legend: { display: false }, datalabels: { display: false },
        zoom: {
          zoom: { wheel: { enabled: true, speed: 0.1 }, pinch: { enabled: true }, drag: { enabled: true, backgroundColor: 'rgba(31,58,107,0.12)' }, mode: 'x', onZoomComplete: ({chart}) => _renderSphInsights(chart) },
          pan:  { enabled: true, mode: 'x', onPanComplete: ({chart}) => _renderSphInsights(chart) },
          limits: { x: { min: 'original', max: 'original' } },
        }
      },
      scales: { y: { beginAtZero: false, ticks: { callback: v => fmt(v) } } }
    }
  });
  // Initial insights (full range)
  const trendChart = chartInstances['chartSphTrend'];
  if (trendChart) {
    trendChart.canvas.ondblclick = () => resetSphTrendZoom();
    _renderSphInsights(trendChart);
  }
  loadBuffetMix();
}

let _sphTrendData = { labels: [], values: [] };

function resetSphTrendZoom() {
  const c = chartInstances['chartSphTrend'];
  if (c) { c.resetZoom(); _renderSphInsights(c); }
}

function _renderSphInsights(chart) {
  const insEl = document.getElementById('sphTrendInsights');
  const obsEl = document.getElementById('sphTrendObs');
  if (!insEl || !obsEl) return;
  const labels = _sphTrendData.labels, values = _sphTrendData.values;
  if (!labels.length) { insEl.innerHTML = ''; obsEl.innerHTML = ''; return; }

  // Determine visible index range from current x-axis
  let lo = 0, hi = labels.length - 1;
  try {
    const xs = chart.scales.x;
    lo = Math.max(0, Math.ceil(xs.min));
    hi = Math.min(labels.length - 1, Math.floor(xs.max));
    if (hi < lo) { lo = 0; hi = labels.length - 1; }
  } catch(e) {}

  const slice = values.slice(lo, hi + 1);
  const slLabels = labels.slice(lo, hi + 1);
  const n = slice.length;
  const avg = n ? slice.reduce((a,b) => a+b, 0) / n : 0;
  const fullAvg = values.reduce((a,b) => a+b, 0) / values.length;
  const minV = n ? Math.min(...slice) : 0;
  const maxV = n ? Math.max(...slice) : 0;
  const minIdx = slice.indexOf(minV), maxIdx = slice.indexOf(maxV);
  const first = slice[0] || 0, last = slice[n-1] || 0;
  const trendPct = first ? ((last - first) / first * 100) : 0;
  const vsFullPct = fullAvg ? ((avg - fullAvg) / fullAvg * 100) : 0;

  // Biggest MoM change inside window
  let biggestDelta = 0, biggestIdx = -1;
  for (let i = 1; i < n; i++) {
    const d = slice[i] - slice[i-1];
    if (Math.abs(d) > Math.abs(biggestDelta)) { biggestDelta = d; biggestIdx = i; }
  }

  const arrow = (v) => v > 0 ? '▲' : v < 0 ? '▼' : '–';
  const clr   = (v) => v > 0 ? '#059669' : v < 0 ? '#dc2626' : '#64748b';
  const card = (label, value, sub, color) =>
    `<div class="rounded-md p-2 border" style="background:${color}10;border-color:${color}33">
       <p class="text-[10px] uppercase tracking-wide font-semibold" style="color:var(--text-muted)">${label}</p>
       <p class="text-base font-bold" style="color:${textInk(color)}">${value}</p>
       <p class="text-[10px]" style="color:var(--text-muted)">${sub}</p>
     </div>`;

  insEl.innerHTML = [
    card('ค่าเฉลี่ยในช่วง', fmt(avg.toFixed(0)),
         `${n} เดือน · ${slLabels[0] ? fmtMonth(slLabels[0]) : ''}${n>1?' – '+fmtMonth(slLabels[n-1]):''}`,
         '#4a70b0'),
    card('เทียบค่าเฉลี่ยทั้งช่วง',
         (vsFullPct >= 0 ? '+' : '') + vsFullPct.toFixed(1) + '%',
         `ค่าเฉลี่ยรวม ${fmt(fullAvg.toFixed(0))}`,
         clr(vsFullPct)),
    card('สูงสุด / ต่ำสุด',
         `${fmt(maxV.toFixed(0))} / ${fmt(minV.toFixed(0))}`,
         `${slLabels[maxIdx] ? fmtMonth(slLabels[maxIdx]) : ''} · ${slLabels[minIdx] ? fmtMonth(slLabels[minIdx]) : ''}`,
         '#0891b2'),
    card('แนวโน้มต้น→ปลาย',
         arrow(trendPct) + ' ' + (trendPct >= 0 ? '+' : '') + trendPct.toFixed(1) + '%',
         `${fmt(first.toFixed(0))} → ${fmt(last.toFixed(0))}`,
         clr(trendPct)),
  ].join('');

  // Observations
  const obs = [];
  if (Math.abs(vsFullPct) >= 5)
    obs.push(`ค่าเฉลี่ยในช่วงนี้${vsFullPct>0?'สูงกว่า':'ต่ำกว่า'}ค่าเฉลี่ยรวม ${Math.abs(vsFullPct).toFixed(1)}% — ${vsFullPct>0?'ลูกค้าใช้จ่ายต่อหัวมากขึ้น':'SPH ลดลงควรตรวจสอบโปรโมชั่น/ส่วนผสมเมนู'}`);
  if (biggestIdx > 0 && first) {
    const pct = (biggestDelta / slice[biggestIdx-1]) * 100;
    if (Math.abs(pct) >= 5)
      obs.push(`เปลี่ยนแปลงเด่น: ${fmtMonth(slLabels[biggestIdx])} ${biggestDelta>0?'ขึ้น':'ลง'} ${Math.abs(pct).toFixed(1)}% (${fmt(slice[biggestIdx-1].toFixed(0))} → ${fmt(slice[biggestIdx].toFixed(0))})`);
  }
  if (n >= 3) {
    // Volatility flag
    const variance = slice.reduce((a,b) => a + (b-avg)*(b-avg), 0) / n;
    const cv = avg ? (Math.sqrt(variance) / avg * 100) : 0;
    if (cv >= 8) obs.push(`ความผันผวนสูง (CV ${cv.toFixed(1)}%) — SPH ไม่นิ่ง อาจมาจาก mix ของ tier หรือกลุ่มลูกค้าเปลี่ยน`);
  }
  if (Math.abs(trendPct) >= 5)
    obs.push(`ทิศทาง: ${trendPct>0?'ขาขึ้น':'ขาลง'} ${Math.abs(trendPct).toFixed(1)}% จากต้นช่วงถึงปลายช่วง`);

  obsEl.innerHTML = obs.length
    ? '<div class="rounded-md p-2.5" style="background:#fefce8;border:1px solid #eab308"><span class="font-semibold">ข้อสังเกต:</span> ' + obs.join(' · ') + '</div>'
    : '<div class="text-[11px]" style="color:var(--text-muted)">SPH ค่อนข้างนิ่งในช่วงนี้ — ไม่มีการเปลี่ยนแปลงที่ชัดเจน</div>';
}

async function loadBuffetMix() {
  const params = new URLSearchParams();
  if (getBranch())    params.set('branch', getBranch());
  const localMonth = _guestLocalMonth();
  if (localMonth) {
    params.set('month_from', localMonth);
    params.set('month_to',   localMonth);
  } else {
    if (getMonthFrom()) params.set('month_from', getMonthFrom());
    if (getMonthTo())   params.set('month_to',   getMonthTo());
  }
  const d = await api(`${API}/api/analysis/buffet_mix?${params}`).catch(() => null);
  if (!d || !d.summary || !d.summary.length) return;

  // Graduated premium palette: entry tier (blue) → premium (rose); à-la-carte violet.
  const TIER_COLORS = { '350': '#60a5fa', '500': '#22d3ee', '590': '#34d399', '799': '#fb7185',
    'เด็ก (Child)': '#f472b6', 'à la carte': '#9a8bc9', 'buffet (unknown tier)': '#4a70b0', 'other': '#cbd5e1' };
  const colors = d.summary.map(s => TIER_COLORS[s.tier] || '#94a3b8');

  const _doughnut = (canvasId, field, unitFmt) => mkChart(canvasId, {
    type: 'doughnut',
    data: { labels: d.summary.map(s => s.tier),
      datasets: [{ data: d.summary.map(s => s[field]), backgroundColor: colors,
        borderColor: '#ffffff', borderWidth: 2, hoverOffset: 8, hoverBorderColor: '#ffffff' }] },
    options: { responsive: true, maintainAspectRatio: false, cutout: '60%', layout: { padding: 6 },
      plugins: {
        legend: { position: 'bottom', labels: { font: { size: 10 }, usePointStyle: true, pointStyle: 'circle', padding: 10 } },
        datalabels: { formatter: v => v >= 4 ? v.toFixed(0) + '%' : '', color: '#fff',
          font: { weight: 'bold', size: 11 }, textStrokeColor: 'rgba(15,23,42,0.35)', textStrokeWidth: 3 },
        tooltip: { callbacks: { label: ctx => { const s = d.summary[ctx.dataIndex];
          return ` ${s.tier}: ${(s[field]||0).toFixed(1)}% · ${unitFmt(field === 'revenue_pct' ? s.revenue : s.qty)}`; } } }
      }
    }
  });
  _doughnut('chartBuffetRevPie', 'revenue_pct', fmt);
  _doughnut('chartBuffetQtyPie', 'qty_pct', fmtN);

  document.getElementById('buffetTierBody').innerHTML = d.summary.map(s => `
    <tr class="border-t border-slate-100">
      <td class="py-1.5 pr-2">
        <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${TIER_COLORS[s.tier]||'#94a3b8'};margin-right:4px"></span>
        ${s.tier}
      </td>
      <td class="py-1.5 pr-2 text-right">${fmt(s.revenue)}</td>
      <td class="py-1.5 text-right font-medium">${s.revenue_pct}%</td>
    </tr>`).join('');

  // Stacked bar trend
  const stackedDatasets = d.tiers.map((t, i) => ({
    label: t,
    data: d.trend.map(row => row[t] || 0),
    backgroundColor: TIER_COLORS[t] || PALETTE_6[i % 6],
    stack: 'tier',
    borderRadius: 2,
  }));
  mkChart('chartBuffetTrend', {
    type: 'bar',
    data: { labels: d.months, datasets: stackedDatasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom', labels: { font: { size: 10 } } }, datalabels: { display: false } },
      scales: { x: { stacked: true }, y: { stacked: true, ticks: { callback: v => fmt(v) } } }
    }
  });
}

// ════════════════════════════════════════════════════════════════
// PHASE 6 — Dedicated Menu Cost Efficiency tab
// ════════════════════════════════════════════════════════════════
let _mce2SortCol = 'cost_pct', _mce2SortDir = -1;
// The "high-volume + high-cost" quadrant boundary (a % of total servings / cost).
// Used by the scatter point colors, the dashed divider lines, the KPI hot-count,
// and the "fix these first" action filter — keep them on ONE value so the chart
// and the action board never disagree about which dishes are red.
const MCE_QUADRANT_PCT = 2;

async function loadMCE() {
  const _fk = _filterKey();
  if (_tabLastKey.menuefficiency === _fk) return;
  _tabLastKey.menuefficiency = _fk;

  const p = new URLSearchParams({ limit: '200' });
  if (getBranch())    p.set('branch',     getBranch());
  if (getMonthFrom()) p.set('month_from', getMonthFrom());
  if (getMonthTo())   p.set('month_to',   getMonthTo());

  const kpiEl  = document.getElementById('mce2Kpis');
  const bodyEl = document.getElementById('mce2Body');
  if (kpiEl)  kpiEl.innerHTML  = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลด…</span>';
  if (bodyEl) bodyEl.innerHTML = '<tr><td colspan="8" class="py-8 text-center" style="color:var(--text-muted)">กำลังโหลด…</td></tr>';

  let d;
  try { d = await api(`${API}/api/buffet/menu-cost-efficiency?${p}`); }
  catch (e) {
    if (kpiEl) kpiEl.innerHTML = `<span class="text-xs text-red-500">โหลดไม่สำเร็จ: ${e.message}</span>`;
    return;
  }

  const scopeEl = document.getElementById('mce2Scope');
  if (scopeEl) scopeEl.textContent = (getBranch() || 'ทุกสาขา') + ' · ' + d.n_dishes + ' เมนู';

  // KPI chips
  const top5cost  = d.items.slice(0, 5).reduce((a, r) => a + r.cost_pct, 0);
  const hotCount  = d.items.filter(r => r.serving_pct >= MCE_QUADRANT_PCT && r.cost_pct >= MCE_QUADRANT_PCT).length;
  const avgCps    = d.items.length ? d.items.reduce((a, r) => a + r.cost_per_serving, 0) / d.items.length : 0;
  if (kpiEl) kpiEl.innerHTML = [
    ['ต้นทุนรวม',           fmt(d.total_cost),             '#ef4444'],
    ['Top 5 เมนู = % ต้นทุน', top5cost.toFixed(0) + '%',   '#f59e0b'],
    ['เมนูเสิร์ฟเยอะ+แพง',  hotCount + ' เมนู',         '#dc2626'],
    ['ต้นทุน/จาน เฉลี่ย',   fmt(avgCps),                   '#7a66b8'],
  ].map(([l, v, c]) => `<span class="px-3 py-1.5 rounded-lg text-xs" style="background:${c}15;color:${textInk(c)}"><b>${v}</b> <span style="color:var(--text-secondary)">${l}</span></span>`).join('');

  // Quadrant scatter
  window._mce2Data = d.items;
  const pts = d.items.map(r => ({
    x: r.serving_pct, y: r.cost_pct,
    r: 5 + Math.min(20, Math.sqrt(Math.max(0, r.cost_per_serving) * 1.5)),
    item: r,
  }));
  mkChart('mce2Scatter', {
    type: 'bubble',
    data: { datasets: [{ data: pts,
      backgroundColor: pts.map(p => p.x >= MCE_QUADRANT_PCT && p.y >= MCE_QUADRANT_PCT ? 'rgba(239,68,68,0.65)' :
                                     p.x >= MCE_QUADRANT_PCT ? 'rgba(16,185,129,0.55)' :
                                     p.y >= MCE_QUADRANT_PCT ? 'rgba(245,158,11,0.60)' :
                                                'rgba(148,163,184,0.30)'),
      borderColor:     pts.map(p => p.x >= MCE_QUADRANT_PCT && p.y >= MCE_QUADRANT_PCT ? '#ef4444' :
                                     p.x >= MCE_QUADRANT_PCT ? '#10b981' : p.y >= MCE_QUADRANT_PCT ? '#f59e0b' : '#94a3b8'),
      borderWidth: 1.5 }] },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, datalabels: { display: false },
        tooltip: { callbacks: { label: ctx => {
          const it = ctx.raw.item;
          return [`${it.description}`,
                  `เสิร์ฟ ${it.serving_pct.toFixed(1)}% · ต้นทุน ${it.cost_pct.toFixed(1)}% · ฿${fmtN(it.cost_per_serving)}/จาน`];
        } } },
      },
      scales: {
        x: { title: { display: true, text: '% จำนวนเสิร์ฟ →', font: { size: 11 } },
             ticks: { callback: v => v + '%' } },
        y: { title: { display: true, text: '% ต้นทุน →', font: { size: 11 } },
             ticks: { callback: v => v + '%' } },
      },
      onClick: (_, elements) => {
        if (!elements.length) return;
        const it = pts[elements[0].index]?.item;
        if (it) gpbSkuDrill(it.sku_code);
      },
    },
  });

  // Add dashed quadrant lines manually via afterDraw plugin
  const chart = chartInstances['mce2Scatter'];
  if (chart && !chart._mce2LinesPlugin) {
    chart._mce2LinesPlugin = true;
    const origDraw = chart.draw.bind(chart);
    chart.draw = function () {
      origDraw();
      const ctx2 = chart.ctx;
      const xa = chart.scales.x, ya = chart.scales.y;
      const x2px = xa.getPixelForValue(MCE_QUADRANT_PCT), y2px = ya.getPixelForValue(MCE_QUADRANT_PCT);
      ctx2.save();
      ctx2.setLineDash([4, 3]);
      ctx2.strokeStyle = 'rgba(239,68,68,0.35)';
      ctx2.lineWidth = 1;
      ctx2.beginPath(); ctx2.moveTo(x2px, ya.top); ctx2.lineTo(x2px, ya.bottom); ctx2.stroke();
      ctx2.beginPath(); ctx2.moveTo(xa.left, y2px); ctx2.lineTo(xa.right, y2px); ctx2.stroke();
      ctx2.setLineDash([]);
      ctx2.restore();
    };
    chart.draw();
  }

  renderMce2Table(_mce2SortCol, _mce2SortDir);
  renderMce2Whatif();

  // Sync sections — data already set above.
  renderMce2Pareto();
  renderMce2CategoryRollup();
  // renderMce2Actions consumes trend + coverage + ingredient data. Run it exactly
  // when those resolve (each guarded so one failure doesn't block the others),
  // not on a fixed 2.5s timer that loses the data on a cold-start (~7s) fetch.
  Promise.all(
    [loadMce2Trend(), loadMce2Coverage(), loadMce2Ingredients()]
      .map(pr => Promise.resolve(pr).catch(() => {}))
  ).then(renderMce2Actions);
}

function renderMce2Table(col, dir) {
  _mce2SortCol = col; _mce2SortDir = dir;
  const data = window._mce2Data;
  if (!data) return;
  const s2 = [...data].sort((a, b) => {
    if (typeof a[col] === 'string') return dir * a[col].localeCompare(b[col]);
    return dir * ((a[col] || 0) - (b[col] || 0));
  });

  // Pre-compute cumulative cost_pct on cost_pct-sorted order for ABC classification
  const byCost = [...data].sort((a, b) => b.cost_pct - a.cost_pct);
  let cum = 0;
  const abcMap = {};
  for (const r of byCost) {
    cum += r.cost_pct;
    abcMap[r.sku_code] = cum <= 70 ? 'A' : cum <= 90 ? 'B' : 'C';
  }

  const bodyEl = document.getElementById('mce2Body');
  if (!bodyEl) return;
  bodyEl.innerHTML = s2.map((r, i) => {
    const hot  = r.serving_pct >= MCE_QUADRANT_PCT && r.cost_pct >= MCE_QUADRANT_PCT;
    const warn = r.cost_pct >= MCE_QUADRANT_PCT && r.serving_pct < MCE_QUADRANT_PCT;
    const rowBg = hot ? 'background:rgba(239,68,68,0.04)' : warn ? 'background:rgba(245,158,11,0.04)' : '';
    const descClean = esc((r.description || '').replace(/^\((?:A|B)\)\s*/, ''));

    // ABC badge
    const abc = abcMap[r.sku_code] || 'C';
    const abcColor = abc === 'A' ? '#ef4444' : abc === 'B' ? '#f59e0b' : '#94a3b8';
    const abcBadge = `<span class="inline-block px-1 rounded text-[9px] font-bold" style="background:${abcColor}20;color:${abc === 'C' ? 'var(--text-secondary)' : textInk(abcColor)}">${abc}</span>`;

    // Source badge
    const src = r.cost_source || 'none';
    const srcLabel = src === 'bom' ? 'BoM' : src === 'sku_cost' ? 'Cost' : '?';
    const srcColor = src === 'bom' ? '#10b981' : src === 'sku_cost' ? '#f59e0b' : '#ef4444';
    const srcBadge = `<span class="inline-block px-1 rounded text-[9px] font-bold" style="background:${srcColor}20;color:${textInk(srcColor)}">${srcLabel}</span>`;

    // FC% = cost / revenue * 100
    const fcPct = r.revenue > 0 ? (r.cost / r.revenue * 100) : null;
    const fcColor = fcPct == null ? 'var(--text-muted)' : fcPct > 60 ? 'var(--bad-ink)' : fcPct > 40 ? '#f59e0b' : '#10b981';
    const fcCell = fcPct != null ? `<span style="color:${textInk(fcColor)}">${fcPct.toFixed(1)}%</span>` : '<span style="color:var(--text-muted)">—</span>';

    return `<tr class="border-b hover:bg-indigo-50/30" style="border-color:var(--border);${rowBg}">
      <td class="py-1.5 pr-2" style="color:var(--text-muted)">${i + 1}</td>
      <td class="py-1.5 pr-1">${abcBadge}</td>
      <td class="py-1.5 pr-2 font-medium">
        ${hot ? '' : warn ? '⚠ ' : ''}${descClean}
        <span class="inline-flex gap-1 ml-1">
          <button class="drill-branch-btn" onclick="event.stopPropagation();gpbSkuDrill('${jsAttr(r.sku_code)}')" title="ดูสูตร BoM">BoM</button>
          <button class="drill-branch-btn" onclick="event.stopPropagation();loadMce2BranchCompare('${jsAttr(r.sku_code)}','${jsAttr(r.description)}')" title="เปรียบเทียบสาขา">สาขา →</button>
        </span>
      </td>
      <td class="py-1.5 pr-2 text-[10px]" style="color:var(--text-muted)">${esc(r.category || '')}</td>
      <td class="py-1.5 pr-1">${srcBadge}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${fmtN(r.servings)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums" style="color:var(--text-muted)">${r.serving_pct.toFixed(1)}%</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${fmt(r.cost)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:${r.cost_pct >= 5 ? 'var(--bad-ink)' : r.cost_pct >= 2 ? 'var(--amber-ink)' : 'inherit'}">${r.cost_pct.toFixed(1)}%</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${fmtN(r.cost_per_serving)}</td>
      <td class="py-1.5 text-right tabular-nums">${fcCell}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="11" class="py-8 text-center" style="color:var(--text-muted)">ไม่มีข้อมูล</td></tr>';
}

function exportMce2Csv() {
  const data = window._mce2Data;
  if (!data) return;
  const hdr = ['sku_code','description','category','servings','serving_pct','cost','cost_pct','cost_per_serving'];
  const rows = data.map(r => hdr.map(k => JSON.stringify(r[k] ?? '')).join(','));
  // downloadCSV prepends the UTF-8 BOM (so Thai descriptions aren't mojibake in
  // Excel) and revokes the object URL — don't hand-roll the Blob/anchor here.
  downloadCSV(hdr.join(',') + '\n' + rows.join('\n'), 'menu_cost_efficiency.csv');
}

async function loadMce2Ingredients() {
  const p = new URLSearchParams({ limit: '60', group_family: 'true' });
  if (getBranch())    p.set('branch',     getBranch());
  if (getMonthFrom()) p.set('month_from', getMonthFrom());
  if (getMonthTo())   p.set('month_to',   getMonthTo());

  let d;
  try { d = await api(`${API}/api/bom/consumption-analysis?${p}`); }
  catch (e) { return; }

  const ing = d.ingredients || d.rows || [];
  if (!ing.length) return;

  const kpiEl = document.getElementById('mce2IngKpis');
  const grand = ing.reduce((a, r) => a + (r.total_cost || 0), 0);
  const top3pct = ing.slice(0, 3).reduce((a, r) => a + (r.pct || 0), 0);
  if (kpiEl) kpiEl.innerHTML = [
    ['ต้นทุนวัตถุดิบรวม', fmt(grand), '#7a66b8'],
    ['Top 3 = % ต้นทุน',  top3pct.toFixed(0) + '%', '#f59e0b'],
    ['จำนวนวัตถุดิบ', String(ing.length), '#4a70b0'],
  ].map(([l, v, c]) => `<span class="px-3 py-1.5 rounded-lg text-xs" style="background:${c}15;color:${textInk(c)}"><b>${v}</b> <span style="color:var(--text-secondary)">${l}</span></span>`).join('');

  const top20 = ing.slice(0, 20);
  mkChart('mce2IngBar', {
    type: 'bar',
    data: {
      labels: top20.map(r => (r.name || r.code || '').substring(0, 22)),
      datasets: [{ label: 'ต้นทุน', data: top20.map(r => Math.round(r.total_cost || 0)),
        backgroundColor: top20.map((_, i) => i < 3 ? 'rgba(122,102,184,0.80)' : 'rgba(122,102,184,0.45)'),
        borderRadius: 3 }],
    },
    options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, datalabels: { display: false },
        tooltip: { callbacks: { label: ctx => fmt(ctx.parsed.x) } } },
      scales: { x: { ticks: { callback: v => fmt(v) } } },
    },
  });

  const bodyEl = document.getElementById('mce2IngBody');
  if (bodyEl) bodyEl.innerHTML = ing.slice(0, 40).map((r, i) => `
    <tr class="border-b" style="border-color:var(--border);background:${i < 3 ? 'rgba(122,102,184,0.05)' : ''}">
      <td class="py-1.5 pr-2" style="color:var(--text-muted)">${i + 1}</td>
      <td class="py-1.5 pr-2 font-medium">${i < 3 ? '' : ''}${esc(r.name || r.code || '')}</td>
      <td class="py-1.5 pr-2 text-[10px]" style="color:var(--text-muted)">${esc(r.type || '')}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${fmt(r.total_cost)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:${textInk(r.pct >= 10 ? '#7a66b8' : 'inherit')}">${(r.pct || 0).toFixed(1)}%</td>
      <td class="py-1.5 text-right tabular-nums" style="color:var(--text-muted)">${r.n_menus || 0}</td>
    </tr>`).join('');
}

// ── Pareto 80/20 chart ────────────────────────────────────────────────────
function renderMce2Pareto() {
  const data = window._mce2Data;
  if (!data || !data.length) return;
  // Sort by cost_pct desc, take top 40 to keep chart readable
  const sorted = [...data].sort((a, b) => b.cost_pct - a.cost_pct).slice(0, 40);
  const labels = sorted.map(r => (r.description || r.sku_code || '').replace(/^\((?:A|B)\)\s*/, '').substring(0, 20));
  const barData = sorted.map(r => r.cost_pct);
  // Cumulative %
  let cum = 0;
  const cumData = barData.map(v => { cum += v; return parseFloat(cum.toFixed(2)); });
  // Find 80% crossing
  const idx80 = cumData.findIndex(v => v >= 80);
  const labelEl = document.getElementById('mce2ParetoLabel');
  if (labelEl && idx80 >= 0) {
    labelEl.textContent = `Top ${idx80 + 1} เมนู = 80% ของต้นทุนทั้งหมด`;
  }

  mkChart('mce2Pareto', {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { type: 'bar', label: '% ต้นทุน', data: barData, yAxisID: 'y',
          backgroundColor: barData.map((_, i) => i <= (idx80 >= 0 ? idx80 : 999) ? 'rgba(245,158,11,0.75)' : 'rgba(148,163,184,0.45)'),
          borderRadius: 2, order: 2 },
        { type: 'line', label: '% ต้นทุนสะสม', data: cumData, yAxisID: 'y2',
          borderColor: '#4a70b0', borderWidth: 2, pointRadius: 0, tension: 0.3, order: 1,
          fill: false },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'top', labels: { font: { size: 10 } } }, datalabels: { display: false },
        tooltip: { callbacks: {
          label: ctx => ctx.dataset.type === 'line'
            ? `สะสม ${ctx.parsed.y.toFixed(1)}%`
            : `ต้นทุน ${ctx.parsed.y.toFixed(2)}%`,
        } },
      },
      scales: {
        x:  { ticks: { font: { size: 9 }, maxRotation: 55, minRotation: 30 } },
        y:  { position: 'left',  title: { display: true, text: '% ต้นทุน', font: { size: 10 } },
              ticks: { callback: v => v + '%' } },
        y2: { position: 'right', title: { display: true, text: '% สะสม', font: { size: 10 } },
              min: 0, max: 100, grid: { drawOnChartArea: false },
              ticks: { callback: v => v + '%' } },
      },
    },
  });
}

// ── What-if calculator ────────────────────────────────────────────────────
function renderMce2Whatif() {
  const data = window._mce2Data;
  if (!data || !data.length) return;
  const n    = parseInt(document.getElementById('mce2WifN')?.value || '5');
  const pct  = parseFloat(document.getElementById('mce2WifPct')?.value || '10') / 100;
  const sorted = [...data].sort((a, b) => b.cost_pct - a.cost_pct);
  const topN   = sorted.slice(0, n);
  const totalCost = data.reduce((s, r) => s + (r.cost || 0), 0);
  const saving = topN.reduce((s, r) => s + (r.cost || 0) * pct, 0);
  const newTotalCost = totalCost - saving;
  const savingPct = totalCost ? saving / totalCost * 100 : 0;

  const resEl = document.getElementById('mce2WhatifResult');
  if (resEl) resEl.innerHTML = [
    [`ประหยัดได้ (ช่วงนี้)`, `฿${fmt(saving)}`, '#10b981'],
    [`% ต้นทุนลดลง`, `${savingPct.toFixed(1)}%`, '#059669'],
    [`ต้นทุนรวมใหม่`, `฿${fmt(newTotalCost)}`, '#4a70b0'],
  ].map(([l, v, c]) => `<div class="rounded-xl p-3 text-center" style="background:${c}12;border:1px solid ${c}30">
    <div class="text-lg font-bold" style="color:${textInk(c)}">${v}</div>
    <div class="text-[11px] mt-0.5" style="color:var(--text-muted)">${l}</div>
  </div>`).join('');

  const tblEl = document.getElementById('mce2WhatifTable');
  if (tblEl) tblEl.innerHTML = `<table class="text-xs w-full">
    <thead><tr class="text-left border-b" style="border-color:var(--border);color:var(--text-muted)">
      <th class="py-1.5 pr-2">#</th><th class="py-1.5 pr-2">เมนู</th>
      <th class="py-1.5 pr-2 text-right">ต้นทุนปัจจุบัน</th>
      <th class="py-1.5 pr-2 text-right">ลด ${(pct*100).toFixed(0)}%</th>
      <th class="py-1.5 text-right font-semibold" style="color:var(--good-ink)">ประหยัดได้</th>
    </tr></thead>
    <tbody>${topN.map((r, i) => {
      const s = (r.cost || 0) * pct;
      return `<tr class="border-b" style="border-color:var(--border)">
        <td class="py-1.5 pr-2" style="color:var(--text-muted)">${i+1}</td>
        <td class="py-1.5 pr-2 font-medium">${esc((r.description||'').replace(/^\((?:A|B)\)\s*/,''))}</td>
        <td class="py-1.5 pr-2 text-right tabular-nums">${fmt(r.cost)} (${r.cost_pct.toFixed(1)}%)</td>
        <td class="py-1.5 pr-2 text-right tabular-nums" style="color:var(--text-muted)">${fmt(r.cost*(1-pct))}</td>
        <td class="py-1.5 text-right tabular-nums font-semibold" style="color:var(--good-ink)">${fmt(s)}</td>
      </tr>`;
    }).join('')}
    <tr class="border-t-2 font-bold" style="border-color:var(--border)">
      <td colspan="4" class="py-1.5 pr-2 text-right">รวมประหยัดได้</td>
      <td class="py-1.5 text-right" style="color:var(--good-ink)">${fmt(saving)}</td>
    </tr></tbody></table>`;
}

// ── Monthly cost trend (top-8 dishes) ────────────────────────────────────
async function loadMce2Trend() {
  const p = new URLSearchParams({ top_n: '8' });
  if (getBranch())    p.set('branch',     getBranch());
  if (getMonthFrom()) p.set('month_from', getMonthFrom());
  if (getMonthTo())   p.set('month_to',   getMonthTo());

  const statusEl = document.getElementById('mce2TrendStatus');
  if (statusEl) statusEl.textContent = 'กำลังโหลด…';

  let d;
  try { d = await api(`${API}/api/buffet/menu-cost-trend?${p}`); }
  catch (e) { if (statusEl) statusEl.textContent = 'โหลดไม่สำเร็จ'; return; }

  window._mce2TrendData = d;

  if (!d.months || d.months.length < 2) {
    if (statusEl) statusEl.textContent = 'ต้องการข้อมูลอย่างน้อย 2 เดือน';
    return;
  }

  const PALETTE = ['#ef4444','#f59e0b','#10b981','#4a70b0','#0ea5e9','#7a66b8','#ec4899','#14b8a6'];
  const datasets = d.dishes.map((dish, i) => {
    const vals = d.months.map(m => {
      const mo = dish.monthly.find(x => x.month === m);
      return mo ? mo.cost_pct : null;
    });
    // Detect trend direction (last 3 vs first 3)
    const nonNull = vals.filter(v => v != null);
    const half = Math.floor(nonNull.length / 2);
    const avgNew = half ? nonNull.slice(-half).reduce((a,b)=>a+b,0)/half : 0;
    const avgOld = half ? nonNull.slice(0,half).reduce((a,b)=>a+b,0)/half : 0;
    const rising = avgNew > avgOld * 1.05;
    const name   = (dish.description || dish.sku_code || '').replace(/^\((?:A|B)\)\s*/, '').substring(0, 20);
    return {
      label: (rising ? '▲ ' : '') + name,
      data: vals, borderColor: PALETTE[i % PALETTE.length],
      backgroundColor: PALETTE[i % PALETTE.length] + '20',
      borderWidth: rising ? 2.5 : 1.5,
      borderDash: rising ? [] : [4, 3],
      pointRadius: 3, tension: 0.3, spanGaps: true,
    };
  });

  mkChart('mce2Trend', {
    type: 'line',
    data: { labels: d.months, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'right', labels: { font: { size: 10 }, boxWidth: 12 } },
        datalabels: { display: false },
        tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y?.toFixed(2) ?? '—'}%` } },
      },
      scales: {
        x: { ticks: { font: { size: 10 } } },
        y: { title: { display: true, text: '% ของต้นทุนรวม', font: { size: 10 } },
             ticks: { callback: v => v + '%' } },
      },
    },
  });

  // Count rising dishes for status label
  const rising = datasets.filter(ds => ds.label.startsWith('▲')).length;
  if (statusEl) statusEl.textContent = rising ? `⚠ ${rising} เมนูมีต้นทุนสูงขึ้น` : '✓ ไม่มีเมนูที่ต้นทุนพุ่งผิดปกติ';
}

// ── Per-branch efficiency for one dish ───────────────────────────────────
async function loadMce2BranchCompare(skuCode, description) {
  const panel = document.getElementById('mce2BranchPanel');
  const titleEl = document.getElementById('mce2BranchTitle');
  const tblEl   = document.getElementById('mce2BranchTable');
  if (!panel) return;

  titleEl.textContent = `เปรียบเทียบสาขา · ${description || skuCode}`;
  tblEl.innerHTML = '<p class="text-xs py-4 text-center" style="color:var(--text-muted)">กำลังโหลด…</p>';
  panel.classList.remove('hidden');

  const p = new URLSearchParams({ sku_code: skuCode });
  if (getMonthFrom()) p.set('month_from', getMonthFrom());
  if (getMonthTo())   p.set('month_to',   getMonthTo());

  let d;
  try { d = await api(`${API}/api/buffet/menu-cost-by-branch?${p}`); }
  catch (e) { tblEl.innerHTML = `<p class="text-xs text-red-500 p-2">${e.message}</p>`; return; }

  const branches = d.branches || [];
  if (!branches.length) { tblEl.innerHTML = '<p class="text-xs py-4 text-center" style="color:var(--text-muted)">ไม่มีข้อมูล</p>'; return; }

  const avgCps = branches.reduce((s, b) => s + b.cost_per_serving, 0) / branches.length;
  const bestBr = branches.reduce((best, b) => b.cost_per_serving < best.cost_per_serving ? b : best, branches[0]);

  mkChart('mce2BranchBar', {
    type: 'bar',
    data: {
      labels: branches.map(b => b.branch),
      datasets: [{
        label: 'ต้นทุน/จาน (฿)', data: branches.map(b => b.cost_per_serving),
        backgroundColor: branches.map(b =>
          b.branch === bestBr.branch ? 'rgba(16,185,129,0.75)' :
          b.cost_per_serving > avgCps * 1.15 ? 'rgba(239,68,68,0.65)' : 'rgba(31,58,107,0.55)'),
        borderRadius: 3,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, datalabels: { display: false },
        tooltip: { callbacks: { label: ctx => `฿${fmtN(ctx.parsed.y)}/จาน` } } },
      scales: { y: { ticks: { callback: v => '฿' + fmtN(v) } } },
    },
  });

  tblEl.innerHTML = `<table class="text-xs w-full">
    <thead><tr class="text-left border-b" style="border-color:var(--border);color:var(--text-muted)">
      <th class="py-1.5 pr-2">สาขา</th>
      <th class="py-1.5 pr-2 text-right">เสิร์ฟ</th>
      <th class="py-1.5 pr-2 text-right">ต้นทุน/จาน</th>
      <th class="py-1.5 pr-2 text-right">% ต้นทุน</th>
      <th class="py-1.5 text-right">FC%</th>
    </tr></thead>
    <tbody>${branches.map(b => {
      const isBest = b.branch === bestBr.branch;
      const isHigh = b.cost_per_serving > avgCps * 1.15;
      return `<tr class="border-b" style="border-color:var(--border);background:${isBest?'rgba(16,185,129,0.05)':isHigh?'rgba(239,68,68,0.04)':''}">
        <td class="py-1.5 pr-2 font-medium">${isBest?'✓ ':isHigh?'⚠ ':''}${esc(b.branch)}</td>
        <td class="py-1.5 pr-2 text-right tabular-nums">${fmtN(b.servings)}</td>
        <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:${isHigh?'var(--bad-ink)':isBest?'var(--good-ink)':'inherit'}">฿${fmtN(b.cost_per_serving)}</td>
        <td class="py-1.5 pr-2 text-right tabular-nums">${b.cost_pct_of_total.toFixed(1)}%</td>
        <td class="py-1.5 text-right tabular-nums" style="color:var(--text-muted)">${b.food_cost_rate != null ? b.food_cost_rate.toFixed(1)+'%' : '—'}</td>
      </tr>`;
    }).join('')}</tbody></table>
    <p class="text-[10.5px] mt-2" style="color:var(--text-muted)">Best practice · ⚠ สูงกว่าเฉลี่ย 15%+ · ค่าเฉลี่ยระบบ ฿${fmtN(Math.round(avgCps))}/จาน</p>`;

  // Scroll to panel
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ── Phase 6 Extra: Category Cost Rollup ──────────────────────────────────
function renderMce2CategoryRollup() {
  const data = window._mce2Data;
  const bodyEl = document.getElementById('mce2CatBody');
  if (!data || !data.length) {
    if (bodyEl) bodyEl.innerHTML = '<tr><td colspan="7" class="py-4 text-center" style="color:var(--text-muted)">ไม่มีข้อมูล</td></tr>';
    return;
  }

  // Aggregate by category
  const catMap = {};
  for (const r of data) {
    const cat = r.category || 'อื่นๆ';
    if (!catMap[cat]) catMap[cat] = { cost: 0, revenue: 0, servings: 0, count: 0 };
    catMap[cat].cost     += r.cost     || 0;
    catMap[cat].revenue  += r.revenue  || 0;
    catMap[cat].servings += r.servings || 0;
    catMap[cat].count    += 1;
  }
  const totCost = Object.values(catMap).reduce((s, v) => s + v.cost, 0) || 1;
  const cats = Object.entries(catMap)
    .map(([name, v]) => ({
      name,
      cost:     v.cost,
      revenue:  v.revenue,
      servings: v.servings,
      count:    v.count,
      cost_pct: v.cost / totCost * 100,
      fc_rate:  v.revenue > 0 ? v.cost / v.revenue * 100 : null,
      avg_cps:  v.servings > 0 ? v.cost / v.servings : 0,
    }))
    .sort((a, b) => b.cost - a.cost);

  const top12 = cats.slice(0, 12);

  // Bar chart — horizontal, top-12 categories
  const maxCost = top12[0]?.cost || 1;
  mkChart('mce2CatBar', {
    type: 'bar',
    data: {
      labels: top12.map(c => c.name.substring(0, 20)),
      datasets: [{
        label: 'ต้นทุน', data: top12.map(c => Math.round(c.cost)),
        backgroundColor: top12.map(c => {
          const intensity = c.cost / maxCost;
          const r = Math.round(14  + intensity * (239 - 14));
          const g = Math.round(165 + intensity * (68  - 165));
          const b = Math.round(233 + intensity * (68  - 233));
          return `rgba(${r},${g},${b},0.75)`;
        }),
        borderRadius: 3,
      }],
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, datalabels: { display: false },
        tooltip: { callbacks: { label: ctx => fmt(ctx.parsed.x) } } },
      scales: { x: { ticks: { callback: v => fmt(v) } } },
    },
  });

  // Table
  if (bodyEl) bodyEl.innerHTML = cats.map((c, i) => {
    const fcColor = c.fc_rate == null ? 'var(--text-muted)' : c.fc_rate > 60 ? 'var(--bad-ink)' : c.fc_rate > 40 ? '#f59e0b' : '#10b981';
    return `<tr class="border-b" style="border-color:var(--border);background:${i < 3 ? 'rgba(14,165,233,0.04)' : ''}">
      <td class="py-1.5 pr-2" style="color:var(--text-muted)">${i + 1}</td>
      <td class="py-1.5 pr-2 font-medium">${i < 3 ? '★ ' : ''}${esc(c.name)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums" style="color:var(--text-muted)">${c.count}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${fmt(c.cost)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:${c.cost_pct >= 20 ? 'var(--bad-ink)' : c.cost_pct >= 10 ? 'var(--amber-ink)' : 'inherit'}">${c.cost_pct.toFixed(1)}%</td>
      <td class="py-1.5 pr-2 text-right tabular-nums" style="color:${textInk(fcColor)}">${c.fc_rate != null ? c.fc_rate.toFixed(1) + '%' : '—'}</td>
      <td class="py-1.5 text-right tabular-nums" style="color:var(--text-muted)">${fmtN(Math.round(c.avg_cps))}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="7" class="py-4 text-center" style="color:var(--text-muted)">ไม่มีข้อมูล</td></tr>';
}

// ── Phase 6 Extra: Recipe Coverage ───────────────────────────────────────
async function loadMce2Coverage() {
  const p = new URLSearchParams();
  if (getBranch())    p.set('branch',     getBranch());
  if (getMonthFrom()) p.set('month_from', getMonthFrom());
  if (getMonthTo())   p.set('month_to',   getMonthTo());

  const covBodyEl = document.getElementById('mce2CovBody');
  const covKpiEl  = document.getElementById('mce2CovKpis');
  if (covBodyEl) covBodyEl.innerHTML = '<tr><td colspan="5" class="py-4 text-center" style="color:var(--text-muted)">กำลังโหลด…</td></tr>';

  let d;
  try { d = await api(`${API}/api/buffet/menu-cost-coverage?${p}`); }
  catch (e) {
    if (covBodyEl) covBodyEl.innerHTML = `<tr><td colspan="5" class="py-4 text-center text-red-500">โหลดไม่สำเร็จ: ${esc(e.message)}</td></tr>`;
    return;
  }

  window._mce2CovData = d;

  const sm = d.summary || {};
  const bomCount  = sm.bom?.count      || 0;
  const scCount   = sm.sku_cost?.count || 0;
  const noneCount = sm.none?.count     || 0;
  const total     = bomCount + scCount + noneCount || 1;
  const noneCost  = sm.none?.cost      || 0;

  // KPI chips
  if (covKpiEl) covKpiEl.innerHTML = [
    ['เมนูทั้งหมด',        String(total),                            '#4a70b0'],
    ['มีสูตร BoM',         `${bomCount} (${(bomCount/total*100).toFixed(0)}%)`, '#10b981'],
    ['Cost เท่านั้น',      `${scCount} เมนู`,                       '#f59e0b'],
    ['ไม่มีข้อมูลต้นทุน', `${noneCount} เมนู · ฿${fmt(noneCost)}`, '#ef4444'],
  ].map(([l, v, c]) => `<span class="px-3 py-1.5 rounded-lg text-xs" style="background:${c}15;color:${textInk(c)}"><b>${v}</b> <span style="color:var(--text-secondary)">${l}</span></span>`).join('');

  // Donut chart
  mkChart('mce2CovDonut', {
    type: 'doughnut',
    data: {
      labels: ['BoM (สมบูรณ์)', 'Cost เท่านั้น', 'ไม่มีข้อมูล'],
      datasets: [{
        data: [bomCount, scCount, noneCount],
        backgroundColor: ['rgba(16,185,129,0.75)', 'rgba(245,158,11,0.75)', 'rgba(239,68,68,0.75)'],
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { font: { size: 10 }, boxWidth: 10 } },
        datalabels: {
          display: ctx => ctx.dataset.data[ctx.dataIndex] > 0,
          formatter: (v, ctx) => v + '\n' + (v / total * 100).toFixed(0) + '%',
          color: '#fff', font: { size: 10, weight: 'bold' },
        },
      },
    },
  });

  // Table: high_cost_no_bom (sku_cost bucket) + high_revenue_uncosted (none bucket)
  const rows = [];
  for (const r of (d.high_cost_no_bom || [])) rows.push({ ...r, _badge: 'amber' });
  for (const r of (d.high_revenue_uncosted || [])) rows.push({ ...r, _badge: 'red' });

  if (covBodyEl) covBodyEl.innerHTML = rows.map(r => {
    const isAmber = r._badge === 'amber';
    const badgeColor = isAmber ? '#f59e0b' : '#ef4444';
    const badgeLabel = isAmber ? '⚠ Cost' : 'ไม่มี';
    return `<tr class="border-b" style="border-color:var(--border);background:${isAmber ? 'rgba(245,158,11,0.03)' : 'rgba(239,68,68,0.04)'}">
      <td class="py-1.5 pr-2"><span class="inline-block px-1 rounded text-[9px] font-bold" style="background:${badgeColor}20;color:${textInk(badgeColor)}">${badgeLabel}</span></td>
      <td class="py-1.5 pr-2 font-medium">${esc((r.description || '').replace(/^\((?:A|B)\)\s*/, ''))}</td>
      <td class="py-1.5 pr-2 text-[10px]" style="color:var(--text-muted)">${esc(r.category || '')}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${fmt(r.cost)}</td>
      <td class="py-1.5 text-right tabular-nums" style="color:var(--text-muted)">${fmt(r.revenue)}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="5" class="py-4 text-center" style="color:var(--text-muted)">ข้อมูลครบทุกเมนู ✓</td></tr>';
}

// ── Phase 6 Extra: Priority Actions ──────────────────────────────────────
function renderMce2Actions() {
  const actEl = document.getElementById('mce2Actions');
  if (!actEl) return;

  const mceData  = window._mce2Data  || [];
  const trendData = window._mce2TrendData;
  const covData  = window._mce2CovData;

  const actions = [];

  // Action 1: Top-3 red quadrant dishes (high-volume + high-cost)
  const redDishes = mceData
    .filter(r => r.serving_pct >= MCE_QUADRANT_PCT && r.cost_pct >= MCE_QUADRANT_PCT)
    .sort((a, b) => b.cost_pct - a.cost_pct)
    .slice(0, 3);
  if (redDishes.length) {
    const names = redDishes.map(r => esc((r.description || '').replace(/^\((?:A|B)\)\s*/, '').substring(0, 18))).join(', ');
    const impact = redDishes.reduce((s, r) => s + (r.cost || 0), 0);
    actions.push({
      priority: 1, color: '#ef4444',
      icon: '',
      title: `ลดต้นทุน ${redDishes.length} เมนูนี้ก่อน (เสิร์ฟเยอะ + ต้นทุนสูง)`,
      detail: names,
      impact: `ผลกระทบ: ฿${fmt(impact)} ต้นทุน`,
    });
  }

  // Action 2: High-cost dishes without BoM recipe
  if (covData) {
    const noRecipe = (covData.high_cost_no_bom || []).slice(0, 3);
    if (noRecipe.length) {
      const n     = (covData.summary?.sku_cost?.count || 0) + (covData.summary?.none?.count || 0);
      const names = noRecipe.map(r => esc((r.description || '').replace(/^\((?:A|B)\)\s*/, '').substring(0, 18))).join(', ');
      actions.push({
        priority: 2, color: '#f59e0b',
        icon: '',
        title: `เพิ่มสูตร BoM ให้ ${n} เมนูที่ไม่มีสูตร (ปรับต้นทุนได้ทันที)`,
        detail: `เริ่มจาก: ${names}`,
        impact: `ต้นทุนที่ยังควบคุมไม่ได้: ฿${fmt((covData.summary?.sku_cost?.cost || 0) + (covData.summary?.none?.cost || 0))}`,
      });
    }
  }

  // Action 3: Top ingredient to negotiate
  const ingBody = document.getElementById('mce2IngBody');
  if (ingBody) {
    const firstRow = ingBody.querySelector('tr:first-child td:nth-child(2)');
    if (firstRow && firstRow.textContent.trim()) {
      const ingName = firstRow.textContent.replace(/^\s*/, '').trim().substring(0, 30);
      actions.push({
        priority: 3, color: '#7a66b8',
        icon: '',
        title: `ต่อรองราคาวัตถุดิบนี้ก่อน`,
        detail: esc(ingName),
        impact: 'วัตถุดิบที่ใช้สูงสุดในทุกสูตร',
      });
    }
  }

  // Action 4: Branch with anomaly (static suggestion if branch panel has data)
  actions.push({
    priority: 4, color: '#0ea5e9',
    icon: '',
    title: `ตรวจสอบสาขาที่ต้นทุน/จานสูงผิดปกติ`,
    detail: 'คลิก "สาขา →" ในตารางด้านบนเพื่อเปรียบเทียบ',
    impact: 'สาขาที่แพงกว่าค่าเฉลี่ย 15%+ = โอกาสประหยัด',
  });

  // Action 5: Rising dishes from trend
  if (trendData && trendData.dishes) {
    const risingDishes = trendData.dishes.filter(dish => {
      const vals = (trendData.months || []).map(m => {
        const mo = dish.monthly?.find(x => x.month === m);
        return mo ? mo.cost_pct : null;
      }).filter(v => v != null);
      const half = Math.floor(vals.length / 2);
      if (!half) return false;
      const avgNew = vals.slice(-half).reduce((a, b) => a + b, 0) / half;
      const avgOld = vals.slice(0, half).reduce((a, b) => a + b, 0) / half;
      return avgNew > avgOld * 1.05;
    });
    if (risingDishes.length) {
      const names = risingDishes.slice(0, 2)
        .map(d => esc((d.description || '').replace(/^\((?:A|B)\)\s*/, '').substring(0, 18))).join(', ');
      actions.push({
        priority: 5, color: '#ec4899',
        icon: '▲',
        title: `เฝ้าระวัง ${risingDishes.length} เมนูที่ต้นทุนพุ่งขึ้น`,
        detail: names,
        impact: 'ต้นทุนสูงขึ้น >5% เทียบกับช่วงก่อนหน้า',
      });
    }
  }

  if (!actions.length) {
    actEl.innerHTML = '<p class="text-xs" style="color:var(--text-muted)">โหลดข้อมูลไม่ครบ — รอสักครู่แล้วลองใหม่</p>';
    return;
  }

  actEl.innerHTML = actions.map(a => `
    <div class="flex items-start gap-3 p-3 rounded-lg" style="background:${a.color}08;border:1px solid ${a.color}25">
      <span class="text-lg leading-none mt-0.5">${a.icon}</span>
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 flex-wrap">
          <span class="px-1.5 py-0.5 rounded text-[10px] font-bold" style="background:${a.color};color:${inkOn(a.color)}">P${a.priority}</span>
          <span class="text-sm font-semibold" style="color:var(--text-primary)">${a.title}</span>
        </div>
        ${a.detail ? `<p class="text-[11px] mt-1" style="color:var(--text-secondary)">${a.detail}</p>` : ''}
        ${a.impact ? `<p class="text-[11px] mt-0.5 font-medium" style="color:${textInk(a.color)}">${a.impact}</p>` : ''}
      </div>
    </div>`).join('');
}

// ════════════════════════════════════════════════════════════════
// BRANCH BENCHMARK
// ── Menu Cost Efficiency (Phase 6): servings × cost share → reduction targets ──
async function loadMenuCostEfficiency() {
  // PL3 (RV3 F1): the card is cost data - a role without the cost module neither asks for it nor sees an empty card
  const card = document.getElementById('mceCard');
  if (card) card.style.display = hasModule('cost.analysis') ? '' : 'none';
  if (!hasModule('cost.analysis')) return;
  const p = new URLSearchParams({ limit:'80' });
  if (getBranch())    p.set('branch', getBranch());
  if (getMonthFrom()) p.set('month_from', getMonthFrom());
  if (getMonthTo())   p.set('month_to', getMonthTo());
  let d;
  try { d = await api(`${API}/api/buffet/menu-cost-efficiency?${p}`); }
  catch(e){ return; }
  if (!d || !Array.isArray(d.items)) { if (card) card.style.display = 'none'; return; }   // a refused or empty answer: no card, no error
  document.getElementById('mceScope').textContent = (getBranch()||'ทุกสาขา') + ' · ' + d.n_dishes + ' เมนู';
  // KPI chips
  const top5cost = d.items.slice(0,5).reduce((a,r)=>a+r.cost_pct,0);
  document.getElementById('mceKpis').innerHTML = [
    ['ต้นทุนรวม', ''+fmt(d.total_cost), '#ef4444'],
    ['จำนวนเสิร์ฟรวม', fmtN(d.total_servings), '#4a70b0'],
    ['Top 5 เมนู = % ต้นทุน', top5cost.toFixed(0)+'%', '#f59e0b'],
  ].map(([l,v,c])=>`<span class="px-3 py-1.5 rounded-lg text-xs" style="background:${c}15;color:${textInk(c)}"><b>${v}</b> <span class="text-slate-500">${l}</span></span>`).join('');
  // Scatter: x=serving%, y=cost%, r=cost/serving. Top-right = reduce target.
  const pts = d.items.map(r=>({ x:r.serving_pct, y:r.cost_pct,
    r: 4 + Math.min(20, Math.sqrt(r.cost_per_serving)), item:r }));
  mkChart('mceScatter', { type:'bubble',
    data:{ datasets:[{ data:pts,
      backgroundColor: pts.map(p=> (p.x>=2 && p.y>=2) ? 'rgba(239,68,68,0.65)' : 'rgba(31,58,107,0.45)'),
      borderColor: pts.map(p=> (p.x>=2 && p.y>=2) ? '#ef4444' : '#4a70b0'), borderWidth:1 }] },
    options:{ responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{display:false}, datalabels:{display:false},
        tooltip:{callbacks:{label:ctx=>{const it=ctx.raw.item; return [it.description, `เสิร์ฟ ${it.serving_pct}% · ต้นทุน ${it.cost_pct}% · ${it.cost_per_serving}/จาน`];}}} },
      scales:{ x:{title:{display:true,text:'% จำนวนเสิร์ฟ →'},ticks:{callback:v=>v+'%'}},
               y:{title:{display:true,text:'% ต้นทุน →'},ticks:{callback:v=>v+'%'}} } } });
  // Table top 20
  document.getElementById('mceBody').innerHTML = d.items.slice(0,20).map((r,i)=>{
    const hot = (r.serving_pct>=2 && r.cost_pct>=2);
    return `<tr class="border-b border-slate-50 ${hot?'bg-rose-50':''}">
      <td class="py-1.5 pr-2 text-slate-400">${i+1}</td>
      <td class="py-1.5 pr-2">${hot?'':''}${esc((r.description||'').replace(/^\((?:A|B)\)\s*/,''))}</td>
      <td class="py-1.5 pr-2 text-right">${fmtN(r.servings)}</td>
      <td class="py-1.5 pr-2 text-right text-slate-500">${r.serving_pct.toFixed(1)}%</td>
      <td class="py-1.5 pr-2 text-right text-red-500 font-semibold">${r.cost_pct.toFixed(1)}%</td>
      <td class="py-1.5 text-right">${fmtN(r.cost_per_serving)}</td>
    </tr>`;
  }).join('');
}

// ════════════════════════════════════════════════════════════════
// ════════════════════════════════════════════════════════════════
// BUFFET PERFORMANCE — core-business dashboard for AYCE buffet operations
// ════════════════════════════════════════════════════════════════
// Resize a chart canvas's container so all rows fit (for horizontal per-branch bars).
function _setChartHeight(canvasId, rowCount, perRow = 22, min = 220) {
  const cv = document.getElementById(canvasId);
  if (!cv || !cv.parentElement) return;
  const h = Math.max(min, (rowCount || 0) * perRow + 48);
  cv.parentElement.style.height = h + 'px';
}

async function loadBuffetPerformance() {
  const _fk = _filterKey(); if (_tabLastKey.buffetperf === _fk) return; _tabLastKey.buffetperf = _fk;
  const params = new URLSearchParams();
  if (getBranch())    params.set('branch', getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to', getMonthTo());

  loadMenuCostEfficiency();
  const d = await api(`${API}/api/buffet/performance?${params}`).catch(() => null);
  if (!d || !d.kpis) {
    document.getElementById('bperfKpiRow').innerHTML = '<p class="text-slate-400 col-span-6 text-center py-8">ไม่มีข้อมูล</p>';
    return;
  }

  // ── KPI hero row ─────────────────────────────────────────────────────
  // Phase 12: combined whole-restaurant cards take priority. Food-cost % now
  // uses the combined (buffet + à la carte) figure from kpis.combined_*.
  const k = d.kpis;
  const cFoodPct = (k.combined_food_cost_pct != null) ? k.combined_food_cost_pct : k.food_cost_pct;
  const cGpPct   = (k.combined_gp_pct        != null) ? k.combined_gp_pct        : null;
  const cRev     = k.combined_revenue_ex_vat || 0;
  const cCogs    = k.combined_cogs           || 0;
  const cGp      = k.combined_gross_profit   || 0;
  const fcInRange = cFoodPct >= k.food_cost_target_lo && cFoodPct <= k.food_cost_target_hi;
  const fcColor   = fcInRange ? '#10b981' : cFoodPct > k.food_cost_target_hi ? '#ef4444' : '#f59e0b';
  const gpColor   = (cGpPct == null) ? '#64748b' : cGpPct >= 30 ? '#10b981' : cGpPct >= 15 ? '#f59e0b' : '#ef4444';
  const premColor = k.premium_tier_pct >= 30 ? '#10b981' : k.premium_tier_pct >= 15 ? '#f59e0b' : '#ef4444';
  const wasteColor = k.over_consumption_pct < 2 ? '#10b981' : k.over_consumption_pct < 5 ? '#f59e0b' : '#ef4444';
  const bevColor = k.beverage_attach_pct >= 15 ? '#10b981' : k.beverage_attach_pct >= 8 ? '#f59e0b' : '#ef4444';
  const _fmtBaht = v => ''  + (Number(v)||0).toLocaleString('th-TH', { maximumFractionDigits: 0 });

  const kpiCard = (label, value, sub, color) =>
    `<div class="rounded-lg p-3 border" style="background:${color}10;border-color:${color}33">
      <p class="text-[10px] text-slate-500 uppercase tracking-wide font-semibold">${label}</p>
      <p class="text-lg font-bold" style="color:${textInk(color)}">${value}</p>
      <p class="text-[10px] text-slate-500">${sub}</p>
    </div>`;
  document.getElementById('bperfKpiRow').innerHTML = [
    kpiCard('รายได้รวม',         _fmtBaht(cRev),                            `รวมทุก SKU (บุฟเฟ่ต์ + อลาคาท)`,                '#4a70b0'),
    kpiCard('ต้นทุน (COGS)',      _fmtBaht(cCogs),                           `ทั้งร้าน · ใช้สูตร BoM`,                       '#0891b2'),
    kpiCard('ต้นทุนอาหาร รวม',  cFoodPct.toFixed(1) + '%',                 `เป้า ${k.food_cost_target_lo}-${k.food_cost_target_hi}% · ภาพรวม`, fcColor),
    kpiCard('GP รวม',         (cGpPct != null ? cGpPct.toFixed(1) : '–') + '%', `${_fmtBaht(cGp)} · ทั้งร้าน`,                    gpColor),
    kpiCard('Pax / บิล',     (Number(k.avg_pax_per_bill)||0).toFixed(2),  `${(Number(k.total_pax)||0).toLocaleString()} pax · เด็ก ${(Number(k.child_pct)||0).toFixed(1)}%`, '#7a66b8'),
    kpiCard('บิลที่สั่งดื่ม',  (Number(k.beverage_bill_attach_pct)||0).toFixed(0) + '%',  `เครื่องดื่ม ${(Number(k.beverage_attach_pct)||0).toFixed(1)}% ของรายได้`, (k.beverage_bill_attach_pct>=60?'#10b981':k.beverage_bill_attach_pct>=40?'#f59e0b':'#ef4444')),
  ].join('');

  // ── Tier mix trend (100% stacked bar) ────────────────────────────────
  const tt = d.tier_trend;
  const TIER_COLORS = {
    BUFFET350: '#eab308', BUFFET500: '#10b981', BUFFET590: '#4a70b0',
    BUFFET799: '#f97316', BUFFETCHILD: '#7a66b8',
  };
  const TIER_LABELS = {
    BUFFET350: 'Buffet 350', BUFFET500: 'Buffet 500', BUFFET590: 'Buffet 590',
    BUFFET799: 'Buffet 799', BUFFETCHILD: 'Child',
  };
  const tierKeys = ['BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD'];
  mkChart('bperfTierTrend', {
    type: 'bar',
    data: {
      labels: tt.map(r => r.period),
      datasets: tierKeys.map(t => ({
        label: TIER_LABELS[t],
        data: tt.map(r => r.total_pax ? (r[t] / r.total_pax * 100) : 0),
        backgroundColor: TIER_COLORS[t],
        borderRadius: 2, borderWidth: 0,
      })),
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { font: { size: 10 }, boxWidth: 10 } },
        datalabels: { display: false },
        tooltip: { mode: 'index', intersect: false,
          callbacks: { label: ctx => {
            const tot = tt[ctx.dataIndex].total_pax;
            const pax = tt[ctx.dataIndex][tierKeys[ctx.datasetIndex]] || 0;
            return ` ${ctx.dataset.label}: ${pax.toLocaleString()} pax (${(Number(ctx.raw)||0).toFixed(1)}%)`;
          } } },
      },
      scales: {
        x: { stacked: true, ticks: { font: { size: 9 } } },
        y: { stacked: true, max: 100, ticks: { callback: v => v + '%', font: { size: 9 } } },
      },
    },
  });

  // ── Tier Mix donut: ALL tiers (350/500/590/799/Child) — Phase 18f
  // Replaced old "590+ vs Standard 350/500" gauge per user request 2026-05-28:
  // show every tier separately, not collapsed into Premium-vs-Standard.
  const tpsForDonut = d.tier_pct_split || {};
  const donutMeta = [
    { code:'BUFFET350',   label:'350',   color:'#94a3b8' },
    { code:'BUFFET500',   label:'500',   color:'#60a5fa' },
    { code:'BUFFET590',   label:'590',   color:'#4a70b0' },
    { code:'BUFFET799',   label:'799',   color:'#db2777' },
    { code:'BUFFETCHILD', label:'Child', color:'#fbbf24' },
  ];
  const donutVals = donutMeta.map(t => Number(tpsForDonut[t.code]) || 0);
  mkChart('bperfPremiumGauge', {
    type: 'doughnut',
    data: {
      labels: donutMeta.map(t => `${t.label}`),
      datasets: [{
        data: donutVals,
        backgroundColor: donutMeta.map(t => t.color),
        borderWidth: 2,
        borderColor: '#fff',
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      cutout: '60%',
      plugins: {
        legend: { display: true, position: 'bottom', labels: { font: { size: 10 }, boxWidth: 10 } },
        datalabels: {
          display: ctx => (Number(ctx.dataset.data[ctx.dataIndex]) || 0) >= 5,
          color: '#fff',
          font: { weight: 'bold', size: 11 },
          formatter: v => (Number(v)||0).toFixed(1) + '%',
        },
        tooltip: { callbacks: { label: ctx => ` ${ctx.label}: ${(Number(ctx.raw)||0).toFixed(1)}%` } },
      }
    },
    plugins: [{
      id: 'tierMixCenterText',
      afterDraw(chart) {
        const { ctx, chartArea: { left, right, top, bottom } } = chart;
        const cx = (left + right) / 2;
        const cy = (top + bottom) / 2;
        const dominant = donutMeta.reduce((best, t, i) =>
          donutVals[i] > best.v ? { label: t.label, v: donutVals[i], color: t.color } : best,
          { label: '', v: 0, color: '#64748b' });
        ctx.save();
        ctx.textAlign = 'center';
        ctx.fillStyle = dominant.color;
        ctx.font = 'bold 20px sans-serif';
        ctx.fillText(`${dominant.label}`, cx, cy - 4);
        ctx.fillStyle = '#64748b';
        ctx.font = '11px sans-serif';
        ctx.fillText(`top tier ${dominant.v.toFixed(1)}%`, cx, cy + 14);
        ctx.restore();
      }
    }]
  });

  // ── Per-tier % split (350/500/590/799/CHILD) ────────────────────────
  const tps = d.tier_pct_split || {};
  const tierMeta = [
    { code:'BUFFET350',   label:'350',   color:'#94a3b8', desc:'Standard' },
    { code:'BUFFET500',   label:'500',   color:'#60a5fa', desc:'Mid' },
    { code:'BUFFET590',   label:'590',   color:'#4a70b0', desc:'Premium' },
    { code:'BUFFET799',   label:'799',   color:'#db2777', desc:'Top' },
    { code:'BUFFETCHILD', label:'Child', color:'#fbbf24', desc:'เด็ก' },
  ];
  const splitHost = document.getElementById('bperfTierSplitCards');
  if (splitHost) {
    splitHost.innerHTML = tierMeta.map(t => {
      const pct = tps[t.code] || 0;
      return `<div class="rounded-lg border border-slate-200 p-3 text-center" style="border-top:3px solid ${t.color}">
        <div class="text-[10px] text-slate-500">${t.desc}</div>
        <div class="text-base font-bold" style="color:${textInk(t.color)}">${t.label}</div>
        <div class="text-xl font-bold mt-1">${pct.toFixed(1)}%</div>
      </div>`;
    }).join('');
  }
  mkChart('bperfTierSplitChart', {
    type: 'bar',
    data: {
      labels: tierMeta.map(t => `${t.label}${t.code==='BUFFETCHILD'?'':' บาท'}`),
      datasets: [{
        label: '% ลูกค้า',
        data: tierMeta.map(t => tps[t.code] || 0),
        backgroundColor: tierMeta.map(t => t.color),
        borderWidth: 0,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false, indexAxis: 'y',
      plugins: {
        legend: { display: false },
        datalabels: { anchor:'end', align:'right', color:'#1e293b', font:{ size:10, weight:'bold' }, formatter: v => (Number(v)||0).toFixed(1)+'%' },
        tooltip: { callbacks: { label: ctx => ` ${(Number(ctx.parsed.x)||0).toFixed(1)}%` } },
      },
      scales: { x: { beginAtZero: true, max: Math.max(60, ...tierMeta.map(t=>tps[t.code]||0))*1.1, ticks:{ callback:v=>v+'%', font:{size:9} } }, y: { ticks:{ font:{size:10} } } }
    }
  });

  // ── Food cost vs target band ────────────────────────────────────────
  const fc = d.food_cost_trend;
  mkChart('bperfFoodCost', {
    data: {
      labels: fc.map(r => r.period),
      datasets: [
        { type:'line', label:'ต้นทุนอาหาร % จริง', data: fc.map(r => r.cost_pct), borderColor:'#ec4899', backgroundColor:'rgba(236,72,153,0.15)', tension:0.3, fill:true, pointRadius:3, borderWidth:2.5 },
        { type:'line', label:`เป้าล่าง ${k.food_cost_target_lo}%`, data: fc.map(() => k.food_cost_target_lo), borderColor:'#10b981', borderDash:[3,3], borderWidth:1.5, pointRadius:0 },
        { type:'line', label:`เป้าบน ${k.food_cost_target_hi}%`,  data: fc.map(() => k.food_cost_target_hi), borderColor:'#ef4444', borderDash:[3,3], borderWidth:1.5, pointRadius:0 },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position:'bottom', labels: { font: { size: 10 }, boxWidth: 14 } },
        datalabels: { display: false },
      },
      scales: { y: { beginAtZero: true, ticks: { callback: v => v + '%', font: { size: 9 } } }, x: { ticks: { font: { size: 9 } } } }
    }
  });

  // ── Seat utilization (per-branch horizontal bar — ALL branches) ─────
  const su = d.seat_util;
  _setChartHeight('bperfSeatUtil', su.length);
  mkChart('bperfSeatUtil', {
    type: 'bar',
    data: {
      labels: su.map(r => r.branch),
      datasets: [{
        label: 'อัตราใช้งาน %',
        data: su.map(r => r.utilization_pct),
        backgroundColor: su.map(r => r.utilization_pct >= 80 ? '#10b981' : r.utilization_pct >= 50 ? '#f59e0b' : '#ef4444'),
        borderRadius: 3,
      }]
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        datalabels: { display: true, anchor: 'end', align: 'right', color: '#1e293b', font: { size: 9, weight: '600' }, formatter: v => (Number(v)||0).toFixed(0) + '%' },
        tooltip: { callbacks: { label: ctx => {
          const r = su[ctx.dataIndex];
          return [` Utilization: ${r.utilization_pct}%`, ` Avg pax/วัน: ${r.avg_pax_per_day}`, ` Max pax/วัน: ${r.max_pax_per_day}`];
        } } },
      },
      scales: { x: { beginAtZero: true, ticks: { callback: v => v + '%', font: { size: 9 } } }, y: { ticks: { font: { size: 9 } } } }
    }
  });

  // ── Turnover (bills per table — ALL branches) ───────────────────────
  const tv = d.turnover;
  _setChartHeight('bperfTurnover', tv.length);
  mkChart('bperfTurnover', {
    type: 'bar',
    data: {
      labels: tv.map(r => r.branch),
      datasets: [{
        label: 'Bills/Table',
        data: tv.map(r => r.bills_per_table),
        backgroundColor: tv.map(r => r.bills_per_table >= 2.5 ? '#10b981' : r.bills_per_table >= 1.5 ? '#f59e0b' : '#94a3b8'),
        borderRadius: 3,
      }]
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        datalabels: { display: true, anchor: 'end', align: 'right', color: '#1e293b', font: { size: 9, weight: '600' }, formatter: v => (Number(v)||0).toFixed(1) },
        tooltip: { callbacks: { label: ctx => {
          const r = tv[ctx.dataIndex];
          return [` Bills/Table/day: ${r.bills_per_table}`, ` Bills/day: ${r.bills_per_day}`, ` Tables active: ${r.tables_per_day}`];
        } } },
      },
      scales: { x: { beginAtZero: true, ticks: { font: { size: 9 } } }, y: { ticks: { font: { size: 9 } } } }
    }
  });

  // ── Beverage attach ────────────────────────────────────────────────
  const bv = d.beverage;
  mkChart('bperfBevAttach', {
    data: {
      labels: bv.map(r => r.period),
      datasets: [
        { type:'bar',  label:'% เครื่องดื่ม/ยอดรวม', data: bv.map(r => r.attach_pct),       backgroundColor:'rgba(6,182,212,0.55)', borderRadius:3, yAxisID:'y' },
        { type:'line', label:'% บิลที่มีเครื่องดื่ม', data: bv.map(r => r.bills_attach_pct), borderColor:'#0891b2', backgroundColor:'transparent', tension:0.3, pointRadius:3, borderWidth:2, yAxisID:'y' },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom', labels: { font: { size: 10 } } }, datalabels: { display: false } },
      scales: { y: { beginAtZero: true, ticks: { callback: v => v + '%', font: { size: 9 } } }, x: { ticks: { font: { size: 9 } } } }
    }
  });

  // ── Daypart tier mix (lunch vs dinner) ─────────────────────────────
  const dp = d.daypart_tier;
  mkChart('bperfDaypartTier', {
    type: 'bar',
    data: {
      labels: dp.map(r => r.daypart === 'lunch' ? 'Lunch' : r.daypart === 'dinner' ? 'Dinner' : 'อื่นๆ'),
      datasets: tierKeys.map(t => ({
        label: TIER_LABELS[t],
        data: dp.map(r => r[t] || 0),
        backgroundColor: TIER_COLORS[t],
        borderRadius: 2, borderWidth: 0,
      })),
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom', labels: { font: { size: 10 }, boxWidth: 10 } }, datalabels: { display: false },
        tooltip: { callbacks: { label: ctx => ` ${ctx.dataset.label}: ${(Number(ctx.raw)||0).toFixed(1)}%` } } },
      scales: { x: { stacked: true, max: 100, ticks: { callback: v => v + '%', font: { size: 9 } } }, y: { stacked: true, ticks: { font: { size: 9 } } } }
    }
  });

  // ── Branch cohort curve ─────────────────────────────────────────────
  const co = d.cohort;
  mkChart('bperfCohort', {
    data: {
      labels: co.map(r => `M+${r.months_since_open}`),
      datasets: [
        { type:'line', label:'รายได้เฉลี่ย/สาขา', data: co.map(r => r.avg_revenue), borderColor:'#4a70b0', backgroundColor:'rgba(31,58,107,0.10)', tension:0.3, fill:true, pointRadius:3, yAxisID:'y' },
        { type:'line', label:'ลูกค้าเฉลี่ย/สาขา',   data: co.map(r => r.avg_pax),     borderColor:'#10b981', backgroundColor:'transparent', tension:0.3, pointRadius:3, borderWidth:2, borderDash:[5,3], yAxisID:'y1' },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { font: { size: 10 } } },
        datalabels: { display: false },
        tooltip: { mode: 'index', callbacks: { afterBody: ctxs => {
          const idx = ctxs[0]?.dataIndex; if (idx == null) return '';
          const r = co[idx]; return r ? `จากการรวมข้อมูล ${r.branch_count} สาขา` : '';
        } } },
      },
      scales: {
        x: { ticks: { font: { size: 9 } }, title: { display: true, text: 'เดือนหลังเปิดสาขา', font: { size: 9 } } },
        y:  { position: 'left',  ticks: { callback: v => fmt(v), font: { size: 9 } }, beginAtZero: true },
        y1: { position: 'right', ticks: { callback: v => fmtN(v), font: { size: 9 } }, grid: { display: false }, beginAtZero: true },
      }
    }
  });

  // ── Pricing recommendation card ─────────────────────────────────────
  const pr = d.pricing_rec;
  // From the Grand Menu on, the tier SKU codes below carry prices that are no
  // longer sold, so the backend blanks the baht projection. Show the mix and say
  // why rather than rendering "+฿0" as if it were a real number.
  const prBaht = pr && !pr.unavailable;
  const tierMixDelta = tierKeys.map(t => ({
    tier: TIER_LABELS[t],
    color: TIER_COLORS[t],
    current: pr.current_mix[t] || 0,
    target:  pr.target_mix[t]  || 0,
    delta:   (pr.target_mix[t] || 0) - (pr.current_mix[t] || 0),
  }));
  document.getElementById('bperfPricingRec').innerHTML = `
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div>
        <p class="text-xs font-semibold text-slate-600 mb-2">เปรียบเทียบ tier mix</p>
        <table class="text-xs w-full">
          <thead><tr class="border-b border-slate-200 text-slate-500"><th class="text-left py-1.5">ระดับราคา</th><th class="text-right py-1.5">ปัจจุบัน</th><th class="text-right py-1.5">เป้า</th><th class="text-right py-1.5">เปลี่ยน</th></tr></thead>
          <tbody>${tierMixDelta.map(r => `
            <tr class="border-b border-slate-100">
              <td class="py-1.5"><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${r.color};margin-right:6px"></span>${r.tier}</td>
              <td class="text-right py-1.5">${r.current.toFixed(1)}%</td>
              <td class="text-right py-1.5">${r.target.toFixed(1)}%</td>
              <td class="text-right py-1.5 ${r.delta > 0 ? 'text-emerald-600 font-bold' : r.delta < 0 ? 'text-red-600' : 'text-slate-400'}">${r.delta >= 0 ? '+' : ''}${r.delta.toFixed(1)}pp</td>
            </tr>`).join('')}</tbody>
        </table>
      </div>
      <div>
        <p class="text-xs font-semibold text-slate-600 mb-2">ผลกระทบทางการเงิน (ถ้าทำได้ตามเป้า)</p>
        ${prBaht ? `<div class="grid grid-cols-2 gap-3 mb-3">
          <div class="bg-emerald-50 rounded-lg p-3 border border-emerald-100">
            <p class="text-[10px] text-emerald-700 font-semibold">รายได้เพิ่ม</p>
            <p class="text-xl font-bold text-emerald-700">+${fmt(pr.revenue_gain)}</p>
            <p class="text-[10px] text-slate-500">จาก ${fmt(pr.current_revenue)} → ${fmt(pr.target_revenue)}</p>
          </div>
          <div class="bg-indigo-50 rounded-lg p-3 border border-indigo-100">
            <p class="text-[10px] text-indigo-700 font-semibold">กำไรเพิ่ม</p>
            <p class="text-xl font-bold text-indigo-700">+${fmt(pr.margin_gain)}</p>
            <p class="text-[10px] text-slate-500">Margin ${pr.current_margin_pct}% → ${pr.target_margin_pct}%</p>
          </div>
        </div>` : `<div class="rounded-lg p-3 mb-3 bg-amber-50 border border-amber-200">
          <p class="text-xs text-amber-800">ยังประเมินเป็นตัวเงินไม่ได้ในช่วงนี้ — ตั้งแต่ <b>1 ก.ย. 2026</b> ใช้ราคาเมนูใหม่ (Grand Menu) ราคาเก่า 350/500/590/799 <b>ไม่มีขายแล้ว</b> จึงไม่นำมาคิดรายได้</p>
        </div>`}
        <p class="text-xs text-slate-500 bg-slate-50 rounded p-2 border border-slate-200">
          <b>สมมุติฐาน:</b> ${pr.assumption}<br>
          <b>กลยุทธ์ที่ได้ผล:</b> Up-sell ที่หน้าจอ POS · เมนูแนะนำ "ขายดี" บน tier ฿${pr.upsell_from || '590'} · staff training · combo set ที่รวม tier ฿${pr.upsell_from || '590'}+ กับ premium add-on เพื่อดันขึ้น ฿${pr.upsell_to || '799'}</p>
      </div>
    </div>`;

  // ── Auto-generated action items ─────────────────────────────────────
  const actions = [];
  if (k.food_cost_pct > k.food_cost_target_hi) {
    actions.push({ icon:'⚠', tone:'red', html:`<b>ต้นทุนอาหาร ${k.food_cost_pct.toFixed(1)}% เกินเป้าสูงสุด ${k.food_cost_target_hi}%</b> — ตรวจสอบ BoM, เจรจาซัพพลายเออร์, ปรับ portion control` });
  } else if (k.food_cost_pct < k.food_cost_target_lo) {
    actions.push({ icon:'✓', tone:'green', html:`<b>ต้นทุนอาหาร ${k.food_cost_pct.toFixed(1)}% ต่ำกว่าเป้า</b> ${uiQ('guest:food-low', 'ต้นทุนอาหารต่ำกว่าเป้า', '<p>อาจหมายความว่า portion น้อยเกินไป (ลูกค้ารู้สึกไม่คุ้ม) หรือคุณลด BoM ได้สำเร็จ</p>')}` });
  }
  if (k.beverage_attach_pct < 10) {
    actions.push({ icon:'', tone:'amber', html:`<b>เครื่องดื่ม ${(Number(k.beverage_attach_pct)||0).toFixed(1)}% ของรายได้</b> · ${(Number(k.beverage_bill_attach_pct)||0).toFixed(0)}% ของบิลสั่งดื่ม — ดันเครื่องดื่มมาร์จิ้นสูง (พิเศษ/แอลกอฮอล์) ${uiQ('guest:bev', 'เครื่องดื่ม', '<p>แม้บิลส่วนใหญ่สั่งดื่ม แต่สัดส่วนรายได้ยังต่ำ ดันเครื่องดื่มมาร์จิ้นสูง (พิเศษ/แอลกอฮอล์) เพิ่มกำไรได้โดยไม่กระทบต้นทุนอาหาร</p>')}` });
  }
  // Top vs bottom utilization
  if (d.seat_util.length >= 2) {
    const top = d.seat_util[0], bot = d.seat_util[d.seat_util.length - 1];
    if (top.utilization_pct > bot.utilization_pct * 2) {
      actions.push({ icon:'', tone:'info', html:`<b>${top.branch}</b> มี utilization ${top.utilization_pct}% (สูงกว่า ${bot.branch} ที่ ${bot.utilization_pct}% เกือบ ${(top.utilization_pct/bot.utilization_pct).toFixed(1)} เท่า) — ดูเป็น case study เพื่อปรับสาขาที่ต่ำ` });
    }
  }
  // Render
  const toneClass = { red:'bg-red-50 border-red-200 text-red-800', amber:'bg-amber-50 border-amber-200 text-amber-800', green:'bg-emerald-50 border-emerald-200 text-emerald-800', info:'bg-cyan-50 border-cyan-200 text-cyan-800', indigo:'bg-indigo-50 border-indigo-200 text-indigo-800' };
  document.getElementById('bperfActions').innerHTML = actions.length
    ? actions.map(a => `<div class="rounded-lg p-3 border ${toneClass[a.tone]} text-xs"><span class="mr-2">${a.icon}</span>${a.html}</div>`).join('')
    : '<p class="text-slate-400 text-sm">ไม่มี action items — ตัวเลขอยู่ในเกณฑ์ดีทั้งหมด</p>';

  // Deep-dive panels load lazily when scrolled into view
  _observeBuffetPanels();
}

// Track elements already handed to an observer so re-calls don't double-observe
const _bperfObserved = new Set();
let   _bperfObserver = null;

function _observeBuffetPanels() {
  const panels = [
    { id: 'tierGpTable',           fn: loadTierGp },
    { id: 'chartPartySize',        fn: loadCoverEconomics },
    { id: 'chartRevpashHourly',    fn: loadRevPASH },
    { id: 'gpCalendarGrid',        fn: loadGpCalendar },         // placeholder <p> gives height
    { id: 'chartChildTrend',       fn: loadChildTrend },
    { id: 'chartDrinkAttach',      fn: loadDrinkAttach },
    { id: 'chartBreakeven',        fn: loadBreakeven },          // was beKpis (empty div) → canvas has height
    { id: 'chartForecastPax',      fn: loadForecast },
    { id: 'chartGpBomMain',        fn: loadBuffetGP },           // was gpBomKpis (empty div) → canvas has height
    { id: 'pnlBody',               fn: loadBomPnl },
    { id: 'pvBody',                fn: () => loadPortionVelocity() },
    { id: 'chartPlateDist',        fn: loadPlateCostDist },
    { id: 'simSliders',            fn: loadCostSensitivity },
    { id: 'lcBody',                fn: () => loadSkuLifecycle() },
  ];
  // Create observer once; subsequent calls just add newly-unobserved elements
  if (!_bperfObserver) {
    _bperfObserver = new IntersectionObserver((entries, observer) => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        const panel = panels.find(p => p.id === entry.target.id);
        if (panel) {
          _bperfObserved.delete(entry.target.id); // allow re-observe after filter change
          panel.fn();
          observer.unobserve(entry.target);
        }
      });
    }, { rootMargin: '200px' });
  }
  panels.forEach(p => {
    if (_bperfObserved.has(p.id)) return;  // already being watched
    const el = document.getElementById(p.id);
    if (el) { _bperfObserver.observe(el); _bperfObserved.add(p.id); }
  });
}

// ── GP per Cover by Tier ─────────────────────────────────────────────────
async function loadTierGp() {
  const params = new URLSearchParams();
  if (getBranch())    params.set('branch', getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to', getMonthTo());

  const data = await api(`${API}/api/buffet/tier-mix?${params}`).catch(() => null);
  if (!data || !data.rows || !data.rows.length) {
    document.getElementById('tierGpBody').innerHTML = '<tr><td colspan="7" class="py-4 text-center text-slate-400">ไม่มีข้อมูล ให้ใส่ต้นทุนต่อหน่วยในตั้งค่า (จัดการต้นทุน)</td></tr>';
    return;
  }

  const TIER_LABEL = { BUFFET350:'Buffet 350', BUFFET500:'Buffet 500', BUFFET590:'Buffet 590',
                       BUFFET799:'Buffet 799', BUFFETCHILD:'Child', B01:'Buffet 350++',
                       B02:'Buffet 590++', B07:'Buffet 500++', B11:'Buffet 799++' };

  // Aggregate rows across all period_months by sku_code
  const aggMap = {};
  for (const r of data.rows) {
    if (!aggMap[r.sku_code]) {
      aggMap[r.sku_code] = { sku_code: r.sku_code, description: r.description,
        qty: 0, revenue: 0, revenue_ex_vat: 0, cogs: 0, unit_cost: r.unit_cost, has_cost: r.unit_cost != null };
    }
    const a = aggMap[r.sku_code];
    a.qty           += r.qty || 0;
    a.revenue       += r.revenue || 0;
    a.revenue_ex_vat += r.revenue_ex_vat || 0;
    if (r.cogs != null) a.cogs += r.cogs; else a.has_cost = false;
    if (r.unit_cost != null) a.unit_cost = r.unit_cost;
  }

  const tiers = Object.values(aggMap)
    .sort((a, b) => b.revenue - a.revenue)
    .map(a => {
      const rev_per_cover  = a.qty ? a.revenue_ex_vat / a.qty : null;
      const cogs_per_cover = (a.has_cost && a.qty) ? a.cogs / a.qty : null;
      const gp             = a.has_cost ? a.revenue_ex_vat - a.cogs : null;
      const gp_per_cover   = (gp != null && a.qty) ? gp / a.qty : null;
      const margin_pct     = (gp != null && a.revenue_ex_vat) ? gp / a.revenue_ex_vat * 100 : null;
      return { ...a, rev_per_cover, cogs_per_cover, gp, gp_per_cover, margin_pct };
    });

  const bodyHtml = tiers.map(t => {
    const label = TIER_LABEL[t.sku_code] || t.description || t.sku_code;
    const gpColor  = t.gp_per_cover  == null ? '#94a3b8' : t.gp_per_cover  > 0 ? '#10b981' : '#ef4444';
    const pctColor = t.margin_pct    == null ? '#94a3b8' : t.margin_pct    > 40 ? '#10b981' : t.margin_pct > 20 ? '#f59e0b' : '#ef4444';
    return `<tr class="border-b border-slate-100 hover:bg-slate-50">
      <td class="py-2 pr-4 font-medium">${label}</td>
      <td class="py-2 pr-4 text-right tabular-nums">${fmtN(t.qty)}</td>
      <td class="py-2 pr-4 text-right tabular-nums">${t.rev_per_cover  != null ? fmt(t.rev_per_cover)  : '—'}</td>
      <td class="py-2 pr-4 text-right tabular-nums">${t.unit_cost      != null ? fmt(t.unit_cost)      : '<span class="text-amber-500">ยังไม่ใส่</span>'}</td>
      <td class="py-2 pr-4 text-right tabular-nums">${t.cogs_per_cover != null ? fmt(t.cogs_per_cover) : '—'}</td>
      <td class="py-2 pr-4 text-right font-bold tabular-nums" style="color:${textInk(gpColor)}">${t.gp_per_cover != null ? fmt(t.gp_per_cover) : '—'}</td>
      <td class="py-2 text-right font-semibold tabular-nums" style="color:${textInk(pctColor)}">${t.margin_pct != null ? t.margin_pct.toFixed(1)+'%' : '—'}</td>
    </tr>`;
  }).join('');
  document.getElementById('tierGpBody').innerHTML = bodyHtml || '<tr><td colspan="7" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';

  // Bar chart: COGS/cover + GP/cover stacked, sorted by descending qty
  const costedTiers = tiers.filter(t => t.gp_per_cover != null);
  if (costedTiers.length) {
    mkChart('chartTierGp', {
      type: 'bar',
      data: {
        labels: costedTiers.map(t => TIER_LABEL[t.sku_code] || t.sku_code),
        datasets: [
          { label: 'COGS/cover', data: costedTiers.map(t => +(t.cogs_per_cover||0).toFixed(0)), backgroundColor: 'rgba(245,158,11,0.75)', borderRadius: 4 },
          { label: 'GP/cover',   data: costedTiers.map(t => +(t.gp_per_cover  ||0).toFixed(0)), backgroundColor: 'rgba(16,185,129,0.85)',  borderRadius: 4 },
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { labels: { font: { size: 11 } } }, datalabels: { display: false },
          tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${fmt(ctx.raw)}` } }
        },
        scales: {
          x: { stacked: true, ticks: { font: { size: 11 } } },
          y: { stacked: true, ticks: { callback: v => fmt(v), font: { size: 10 } } }
        }
      }
    });
  }
}

// ── Cover Economics ──────────────────────────────────────────────────────
async function loadCoverEconomics() {
  const params = new URLSearchParams();
  if (getBranch())    params.set('branch', getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to', getMonthTo());

  const data = await api(`${API}/api/buffet/cover-economics?${params}`).catch(() => null);
  if (!data) return;

  // KPI chips
  const s = data.summary || {};
  const childPct = s.total_covers && s.child_cover_estimate
    ? (s.child_cover_estimate / s.total_covers * 100).toFixed(1) : null;
  document.getElementById('coverEconKpis').innerHTML = [
    { label:'Avg Party', value: s.avg_party_size?.toFixed(1) || '—' },
    { label:'Rev/Cover', value: s.avg_revenue_per_cover ? fmt(s.avg_revenue_per_cover) : '—' },
    { label:'เด็ก %',   value: childPct ? childPct + '%' : '—' },
  ].map(k => `<div class="rounded p-2 bg-slate-50"><div class="text-slate-400 text-[10px] uppercase">${k.label}</div><div class="text-base font-bold text-slate-700">${k.value}</div></div>`).join('');

  // Party size bar chart
  const ps = (data.party_size_dist || []).filter(r => r.party_size <= 10);
  if (ps.length) {
    mkChart('chartPartySize', {
      type: 'bar',
      data: {
        labels: ps.map(r => r.party_size + ' คน'),
        datasets: [
          { label: 'จำนวนบิล', data: ps.map(r => r.tx_count), backgroundColor: 'rgba(31,58,107,0.7)', borderRadius: 4, yAxisID: 'yTx' },
          { label: 'รายได้/หัว ()', type: 'line', data: ps.map(r => r.avg_revenue_per_cover), borderColor: '#10b981', backgroundColor: 'transparent', borderWidth: 2, pointRadius: 4, yAxisID: 'yRpc', tension: 0.3 },
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { labels: { font: { size: 10 } } }, datalabels: { display: false },
          tooltip: { callbacks: { label: ctx => ctx.dataset.yAxisID === 'yRpc' ? `Rev/Cover: ${fmt(ctx.raw)}` : `บิล: ${fmtN(ctx.raw)}` } }
        },
        scales: {
          x:    { ticks: { font: { size: 10 } } },
          yTx:  { position: 'left',  ticks: { font: { size: 10 }, callback: v => fmtN(v) } },
          yRpc: { position: 'right', grid: { display: false }, ticks: { font: { size: 10 }, callback: v => fmt(v) } }
        }
      }
    });
  }

  // Hourly cover revenue
  const hr = (data.hourly_covers || []).filter(r => r.total_covers > 0);
  if (hr.length) {
    mkChart('chartHourlyCover', {
      type: 'bar',
      data: {
        labels: hr.map(r => r.hour + ':00'),
        datasets: [
          { label: 'ลูกค้า (หัว)', data: hr.map(r => r.total_covers), backgroundColor: 'rgba(14,165,233,0.65)', borderRadius: 3, yAxisID: 'yCov' },
          { label: 'Rev/Cover', type: 'line', data: hr.map(r => r.revenue && r.total_covers ? r.revenue / r.total_covers : 0),
            borderColor: '#f97316', backgroundColor: 'transparent', borderWidth: 2, pointRadius: 3, yAxisID: 'yRpc', tension: 0.3 },
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { labels: { font: { size: 10 } } }, datalabels: { display: false } },
        scales: {
          x:    { ticks: { font: { size: 10 } } },
          yCov: { position: 'left',  ticks: { font: { size: 10 } } },
          yRpc: { position: 'right', grid: { display: false }, ticks: { font: { size: 10 }, callback: v => fmt(v) } }
        }
      }
    });
  }
}

// ── RevPASH ──────────────────────────────────────────────────────────────
async function loadRevPASH() {
  const seats  = parseInt(document.getElementById('revpashSeats')?.value) || 120;
  const params = new URLSearchParams({ seats });
  if (getBranch())    params.set('branch', getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to', getMonthTo());

  const data = await api(`${API}/api/buffet/revpash?${params}`).catch(() => null);
  if (!data) return;

  // KPI strip
  const peak = data.peak_hour;
  const dead = data.dead_hour;
  document.getElementById('revpashKpis').innerHTML = [
    { label: 'Peak Hour',   value: peak != null ? peak + ':00' : '—',  sub: peak != null ? fmt((data.hourly.find(h=>h.hour===peak)||{}).revpash||0) + '/seat/hr' : '', color: '#4a70b0' },
    { label: 'Avg RevPASH', value: data.hourly ? fmt(data.hourly.reduce((s,h)=>s+(h.revpash||0),0)/Math.max(data.hourly.filter(h=>h.revpash>0).length,1)) : '—', sub: 'เฉลี่ยชั่วโมงที่มีลูกค้า', color: 'var(--good-ink)' },
    { label: 'Dead Zone',   value: dead != null ? dead + ':00' : '—',  sub: 'ชั่วโมงที่ RevPASH ต่ำสุด', color: '#f59e0b' },
    { label: 'ที่นั่งที่ใช้', value: fmtN(seats), sub: 'กรอกแก้ได้ด้านบน', color: '#64748b' },
  ].map(k => `<div class="rounded-lg p-3 bg-slate-50"><p class="text-[10px] text-slate-500 uppercase">${k.label}</p><p class="text-lg font-bold" style="color:${textInk(k.color)}">${k.value}</p><p class="text-[10px] text-slate-400">${k.sub}</p></div>`).join('');

  // Hourly RevPASH
  const hrly = data.hourly || [];
  mkChart('chartRevpashHourly', {
    type: 'bar',
    data: {
      labels: hrly.map(r => r.hour + ':00'),
      datasets: [{
        label: 'RevPASH (/ที่นั่ง/ชม.)',
        data: hrly.map(r => r.revpash || 0),
        backgroundColor: hrly.map(r => r.hour === peak ? 'rgba(31,58,107,0.9)' : 'rgba(31,58,107,0.45)'),
        borderRadius: 3,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, datalabels: { display: false } },
      scales: {
        x: { ticks: { font: { size: 9 } } },
        y: { ticks: { font: { size: 9 }, callback: v => fmt(v) }, beginAtZero: true }
      }
    }
  });

  // DoW RevPASH
  const DOW_TH = ['อา','จ','อ','พ','พฤ','ศ','ส'];
  const dows = data.dow || [];
  mkChart('chartRevpashDow', {
    type: 'bar',
    data: {
      labels: dows.map(r => DOW_TH[r.dow] || r.dow),
      datasets: [{
        label: 'Avg RevPASH',
        data: dows.map(r => r.avg_revpash || 0),
        backgroundColor: dows.map((r,i) => ['#f97316','#4a70b0','#0891b2','#10b981','#7a66b8','#ec4899','#f59e0b'][i % 7] + 'cc'),
        borderRadius: 4,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, datalabels: { display: false } },
      scales: {
        x: { ticks: { font: { size: 11 } } },
        y: { ticks: { font: { size: 9 }, callback: v => fmt(v) }, beginAtZero: true }
      }
    }
  });
}

// ── Tier Upgrade Simulator ───────────────────────────────────────────────
async function loadUpgradeSimulator() {
  const shiftPct = parseFloat(document.getElementById('simShiftPct')?.value) || 10;
  const params   = new URLSearchParams({ shift_pct: shiftPct });
  if (getBranch())    params.set('branch', getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to', getMonthTo());

  const data = await api(`${API}/api/buffet/upgrade-simulator?${params}`).catch(() => null);
  // The pair being simulated follows the menu in force over the window
  // (฿590→฿799 up to Aug 2026, ฿629→฿789 from the Grand Menu on 1 Sep 2026),
  // so every label below reads it off the response instead of hard-coding it.
  const upFrom = (data && data.from_tier) || '590', upTo = (data && data.to_tier) || '799';
  if (!data || !data.current) {
    document.getElementById('simKpis').innerHTML = `<div class="col-span-2 text-slate-400 text-sm py-4">ไม่มีข้อมูล — ต้องใส่ unit_cost ของ tier ${upFrom} และ ${upTo} ก่อน</div>`;
    return;
  }
  const simNoteEl = document.getElementById('simScenarioNote');
  if (simNoteEl) simNoteEl.textContent =
    `จำลอง: ย้ายลูกค้า ${shiftPct}% จาก tier ฿${upFrom} ขึ้นไป ฿${upTo}` +
    (data.menu_era === 'grand' ? ' (Grand Menu ตั้งแต่ 1 ก.ย. 2026 — ราคาเก่าไม่มีขายแล้ว)' : '') +
    (data.note ? ` · ${data.note}` : '');

  const cur = data.current, sim = data.simulated;
  const deltaGp  = data.delta_gp  || 0;
  const deltaPct = data.delta_pct || 0;

  document.getElementById('simKpis').innerHTML = [
    { label: 'GP ปัจจุบัน',    value: fmt(cur.total_gp),  color: '#475569' },
    { label: 'GP หลังจำลอง', value: fmt(sim.total_gp),  color: '#10b981' },
    { label: 'เพิ่มขึ้น',       value: fmt(deltaGp),       color: deltaGp >= 0 ? '#10b981' : '#ef4444' },
    { label: '% เพิ่ม',         value: (deltaPct >= 0 ? '+' : '') + deltaPct.toFixed(1) + '%', color: deltaPct >= 0 ? '#10b981' : '#ef4444' },
  ].map(k => `<div class="rounded-lg p-3 bg-slate-50"><p class="text-[10px] text-slate-500 uppercase">${k.label}</p><p class="text-xl font-bold" style="color:${textInk(k.color)}">${k.value}</p></div>`).join('');

  // Tier table
  const TIER_LABEL = { BUFFET350:'350', BUFFET500:'500', BUFFET590:'590', BUFFET799:'799', BUFFETCHILD:'Child' };
  const tierRows = (cur.tiers || []).map(ct => {
    const st = (sim.tiers || []).find(t => t.name === ct.name) || ct;
    return `<tr class="border-b border-slate-100 text-xs">
      <td class="py-2 pr-3 font-medium">${TIER_LABEL[ct.name] || ct.name}</td>
      <td class="py-2 pr-3 text-right tabular-nums">${fmtN(ct.qty)}</td>
      <td class="py-2 pr-3 text-right tabular-nums ${st.qty !== ct.qty ? 'text-indigo-600 font-semibold' : ''}">${fmtN(st.qty)}</td>
      <td class="py-2 pr-3 text-right tabular-nums">${ct.gp_per_cover != null ? fmt(ct.gp_per_cover) : '—'}</td>
      <td class="py-2 text-right tabular-nums ${st.gp > ct.gp ? 'text-emerald-600 font-semibold' : ''}">${st.gp != null ? fmt(st.gp) : '—'}</td>
    </tr>`;
  }).join('');
  document.getElementById('simTierTable').innerHTML = `
    <table class="text-xs w-full">
      <thead><tr class="text-slate-500 text-left border-b border-slate-200">
        <th class="py-2 pr-3">ระดับราคา</th>
        <th class="py-2 pr-3 text-right">ลูกค้าปัจจุบัน</th>
        <th class="py-2 pr-3 text-right">ลูกค้าหลังจำลอง</th>
        <th class="py-2 pr-3 text-right">GP/หัว</th>
        <th class="py-2 text-right">GP หลังจำลอง</th>
      </tr></thead>
      <tbody>${tierRows}</tbody>
    </table>`;

  // Compare bar chart
  const allTiers = (cur.tiers || []).map(t => TIER_LABEL[t.name] || t.name);
  mkChart('chartSimCompare', {
    type: 'bar',
    data: {
      labels: allTiers,
      datasets: [
        { label: 'รายได้ปัจจุบัน',  data: (cur.tiers||[]).map(t=>t.revenue||0), backgroundColor:'rgba(100,116,139,0.6)', borderRadius:3 },
        { label: 'GP ปัจจุบัน',       data: (cur.tiers||[]).map(t=>t.gp||0),      backgroundColor:'rgba(16,185,129,0.6)',  borderRadius:3 },
        { label: 'GP หลังจำลอง',       data: (sim.tiers||[]).map(t=>t.gp||0),      backgroundColor:'rgba(31,58,107,0.75)',  borderRadius:3, borderDash:[4,2] },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { labels: { font: { size: 10 } } }, datalabels: { display: false },
        tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${fmt(ctx.raw)}` } }
      },
      scales: {
        x: { ticks: { font: { size: 11 } } },
        y: { ticks: { font: { size: 10 }, callback: v => fmt(v) }, beginAtZero: true }
      }
    }
  });
}

// ════════════════════════════════════════════════════════════════
// BUFFET DEEP-DIVE: 1-5, 7
// ════════════════════════════════════════════════════════════════

// ── 1. GP Calendar ───────────────────────────────────────────────────────

async function loadGpCalendar() {
  const branch = getBranch();
  const sel    = document.getElementById('calMonth');

  // Populate month selector from available months if empty
  if (!sel.options.length) {
    const months = await api(`${API}/api/months`).catch(()=>[]);
    months.forEach(m => { const o = document.createElement('option'); o.value = m; o.textContent = fmtMonth(m); sel.appendChild(o); });
    if (sel.options.length) sel.value = sel.options[sel.options.length-1].value;
  }
  const month = sel.value;
  if (!month) return;

  const params = new URLSearchParams({ month });
  if (branch) params.set('branch', branch);
  const d = await api(`${API}/api/buffet/gp-calendar?${params}`).catch(()=>null);
  if (!d || !d.days) { document.getElementById('gpCalendarGrid').innerHTML = '<p class="text-slate-400 text-sm py-4">ไม่มีข้อมูล</p>'; return; }

  const [y, m] = month.split('-').map(Number);
  const firstDow = new Date(y, m-1, 1).getDay(); // 0=Sun
  const daysInMonth = new Date(y, m, 0).getDate();
  const dayMap = {};
  d.days.forEach(r => { dayMap[r.date] = r; });

  // Heatmap intensity is driven by daily GROSS PROFIT (฿). Margin% is ~flat
  // across days because COGS is a monthly figure pro-rated onto days by revenue,
  // so it cannot vary day-to-day — GP (฿) is the signal that actually moves.
  const gpVals = d.days.map(r => r.gp).filter(v => v != null);
  const maxGp  = gpVals.length ? Math.max(...gpVals) : 0;
  const _cellColor = gp => {
    if (gp == null || maxGp <= 0) return '#e2e8f0';
    const t = Math.max(0, gp) / maxGp;
    if (t >= 0.85) return '#065f46';
    if (t >= 0.65) return '#059669';
    if (t >= 0.45) return '#34d399';
    if (t >= 0.25) return '#a7f3d0';
    return '#d1fae5';
  };
  const _cellText = gp => (gp != null && maxGp > 0 && (Math.max(0,gp)/maxGp) >= 0.45) ? '#ffffff' : '#064e3b';

  const DOW_TH = ['อา','จ','อ','พ','พฤ','ศ','ส'];
  let html = `<div style="display:grid;grid-template-columns:repeat(7,1fr);gap:4px;min-width:420px">`;
  DOW_TH.forEach(h => { html += `<div class="text-center text-[10px] font-semibold text-slate-400 py-1">${h}</div>`; });
  for (let i=0; i<firstDow; i++) html += '<div></div>';
  for (let day=1; day<=daysInMonth; day++) {
    const dateStr = `${month}-${String(day).padStart(2,'0')}`;
    const r = dayMap[dateStr];
    const gp   = r?.gp ?? null;
    const pct  = r?.margin_pct ?? null;
    const bg   = _cellColor(gp);
    const tc   = _cellText(gp);
    const cogsPct = (r && r.revenue_ex && r.cogs != null) ? (100*r.cogs/r.revenue_ex) : null;
    const tip  = r
      ? `${dateStr}`
        + `\nRevenue (ex VAT): ${fmt(r.revenue_ex)}`
        + `\nCOGS: ${r.cogs != null ? fmt(r.cogs) : '—'}${cogsPct != null ? '  ('+cogsPct.toFixed(1)+'% ของยอดขาย)' : ''}`
        + `\nGross Profit: ${gp != null ? fmt(gp) : '—'}${pct != null ? '  ('+pct.toFixed(1)+'%)' : ''}`
        + `\nPax: ${fmtN(r.customer_count)} · บิล: ${fmtN(r.tx_count)}`
      : '';
    html += `<div title="${tip}" style="background:${bg};color:${tc};border-radius:var(--r-md);padding:6px 4px;text-align:center;cursor:default">
      <div style="font-size:11px;font-weight:700">${day}</div>
      <div style="font-size:9px">${gp != null ? fmt(gp) : (r ? fmt(r.revenue_ex||0) : '')}</div>
    </div>`;
  }
  html += '</div>';
  document.getElementById('gpCalendarGrid').innerHTML = html;

  // KPI strip — best/worst day measured by GP (฿), plus the headline margin
  const days = d.days;
  const withGp = days.filter(r => r.gp != null);
  const bestDay  = withGp.reduce((a,b) => (b.gp > (a?.gp??-Infinity) ? b : a), null);
  const worstDay = withGp.reduce((a,b) => (b.gp < (a?.gp?? Infinity) ? b : a), null);
  const totalRev = days.reduce((s,r)=>s+(r.revenue_inc||0),0);
  const totalGp  = withGp.reduce((s,r)=>s+(r.gp||0),0);
  document.getElementById('gpCalKpis').innerHTML = [
    { label:'รายได้รวม (รวม VAT)', value: fmt(totalRev),                                                       color:'#4a70b0' },
    { label:'GP รวม / อัตรากำไร',       value: `${fmt(totalGp)} · ${d.monthly_margin_pct != null ? d.monthly_margin_pct.toFixed(1)+'%' : '—'}`, color: (d.monthly_margin_pct||0) >= 40 ? 'var(--good-ink)' : '#f59e0b' },
    { label:'วัน GP สูงสุด',         value: bestDay  ? bestDay.date.slice(8)+' ('+fmt(bestDay.gp)+')'  : '—',     color:'var(--good-ink)' },
    { label:'วัน GP ต่ำสุด',         value: worstDay ? worstDay.date.slice(8)+' ('+fmt(worstDay.gp)+')': '—',     color:'#ef4444' },
  ].map(k=>`<div class="rounded p-3 bg-slate-50"><p class="text-[10px] text-slate-500 uppercase">${k.label}</p><p class="text-base font-bold" style="color:${textInk(k.color)}">${k.value}</p></div>`).join('');
}

// ── 2. Branch × Tier Matrix ──────────────────────────────────────────────

// ── 3. Child/Adult Cover Trend ───────────────────────────────────────────
async function loadChildTrend() {
  const params = new URLSearchParams();
  if (getBranch())    params.set('branch', getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to',   getMonthTo());
  const rows = await api(`${API}/api/buffet/child-trend?${params}`).catch(()=>[]);
  if (!rows.length) return;

  mkChart('chartChildTrend', {
    type: 'bar',
    data: {
      labels: rows.map(r=>r.period_month),
      datasets: [
        { label:'ผู้ใหญ่', data:rows.map(r=>r.adult_pax||0), backgroundColor:'rgba(31,58,107,0.7)', borderRadius:3, stack:'pax' },
        { label:'เด็ก',   data:rows.map(r=>r.child_pax||0), backgroundColor:'rgba(236,72,153,0.7)', borderRadius:3, stack:'pax' },
        { label:'GP %',   type:'line', data:rows.map(r=>r.gp_pct), borderColor:'#10b981', backgroundColor:'transparent',
          borderWidth:2, pointRadius:3, tension:0.3, yAxisID:'yGp',
          segment:{ borderDash: ctx => ctx.p0.parsed.y==null||ctx.p1.parsed.y==null ? [4,4] : undefined } },
      ]
    },
    options: {
      responsive:true, maintainAspectRatio:false,
      plugins:{
        legend:{labels:{font:{size:10}}}, datalabels:{display:false},
        tooltip:{callbacks:{label:ctx=> ctx.dataset.yAxisID==='yGp' ? `GP%: ${(ctx.raw||0).toFixed(1)}%` : `${ctx.dataset.label}: ${fmtN(ctx.raw)}`}}
      },
      scales:{
        x:   {stacked:true, ticks:{font:{size:9}}},
        y:   {stacked:true, position:'left',  ticks:{font:{size:9},callback:v=>fmtN(v)}},
        yGp: {position:'right', grid:{display:false}, ticks:{font:{size:9},callback:v=>v+'%'}, min:0, max:100},
      }
    }
  });
}

// ── 4. Drink Attach Rate per Branch ─────────────────────────────────────
async function loadDrinkAttach() {
  const params = new URLSearchParams();
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to',   getMonthTo());
  const rows = await api(`${API}/api/buffet/drink-attach-by-branch?${params}`).catch(()=>[]);
  if (!rows.length) return;

  mkChart('chartDrinkAttach', {
    type:'bar',
    data:{
      labels: rows.map(r=>r.branch),
      datasets:[
        { label:'อัตราสั่งร่วม %', data:rows.map(r=>r.attach_rate_pct||0),
          backgroundColor: rows.map(r=>(r.attach_rate_pct>=40?'rgba(16,185,129,0.8)':'rgba(245,158,11,0.7)')),
          borderRadius:4, yAxisID:'yPct' },
        { label:'รายได้เครื่องดื่ม %', type:'line', data:rows.map(r=>r.drink_revenue_pct||0),
          borderColor:'#4a70b0', backgroundColor:'transparent', borderWidth:2, pointRadius:4, yAxisID:'yPct' },
      ]
    },
    options:{
      responsive:true, maintainAspectRatio:false,
      plugins:{
        legend:{labels:{font:{size:10}}}, datalabels:{display:false},
        annotation:{ annotations:{ target:{ type:'line', yMin:40, yMax:40, borderColor:'#10b981', borderWidth:1.5, borderDash:[4,4],
          label:{content:'เป้า 40%', display:true, position:'end', font:{size:10}, color:'#10b981'} }}}
      },
      scales:{
        x: {ticks:{font:{size:9}}},
        yPct:{position:'left', ticks:{font:{size:10},callback:v=>v+'%'}, beginAtZero:true, max:100},
      }
    }
  });

  document.getElementById('drinkAttachBody').innerHTML = rows.map(r=>{
    const c = r.attach_rate_pct>=40 ? 'var(--good-ink)' : r.attach_rate_pct>=25 ? '#f59e0b' : '#ef4444';
    return `<tr class="border-b border-slate-100 hover:bg-slate-50">
      <td class="py-2 pr-3 font-medium text-xs">${esc(r.branch)}</td>
      <td class="py-2 pr-3 text-right text-xs font-bold tabular-nums" style="color:${textInk(c)}">${(r.attach_rate_pct||0).toFixed(1)}%</td>
      <td class="py-2 pr-3 text-right text-xs tabular-nums">${(r.drink_revenue_pct||0).toFixed(1)}%</td>
      <td class="py-2 text-right text-xs tabular-nums">${fmt(r.avg_drink_per_bill||0)}</td>
    </tr>`;
  }).join('');
}

// ── 5. Breakeven Calculator ──────────────────────────────────────────────
async function loadBreakeven() {
  const fixedCost = parseFloat(document.getElementById('beFixedCost')?.value) || 0;
  const params = new URLSearchParams({ monthly_fixed_cost: fixedCost });
  if (getBranch())    params.set('branch', getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to',   getMonthTo());
  const d = await api(`${API}/api/buffet/breakeven?${params}`).catch(()=>null);
  if (!d) return;

  const beUtil = d.breakeven_utilization_pct;
  const utilColor = beUtil==null ? '#94a3b8' : beUtil<70?'#10b981':beUtil<90?'#f59e0b':'#ef4444';
  document.getElementById('beKpis').innerHTML = [
    { label:'GP/หัว (เฉลี่ย)',          value: d.avg_gp_per_cover  != null ? fmt(d.avg_gp_per_cover)  : 'ไม่มีต้นทุน', color:'#4a70b0' },
    { label:'ต้องขาย/เดือน',           value: d.required_covers_per_month != null ? fmtN(Math.ceil(d.required_covers_per_month)) + ' cover' : '—', color:'#f59e0b' },
    { label:'ต้องขาย/วัน',             value: d.required_covers_per_day   != null ? fmtN(Math.ceil(d.required_covers_per_day))   + ' cover' : '—', color:'#f97316' },
    { label:'Breakeven Utilization',   value: beUtil != null ? beUtil.toFixed(1)+'%' : '—', color: utilColor },
  ].map(k=>`<div class="rounded-lg p-3 bg-slate-50"><p class="text-[10px] text-slate-500 uppercase">${k.label}</p><p class="text-lg font-bold" style="color:${textInk(k.color)}">${k.value}</p></div>`).join('');

  // Chart: actual vs required covers (scenario analysis)
  const actual = d.actual_avg_covers_per_day || 0;
  const required = d.required_covers_per_day || 0;
  const scenarios = [0.5,0.75,1.0,1.25,1.5].map(m => ({
    label: (m*100).toFixed(0)+'% of req',
    covers: +(required * m).toFixed(0),
    gp: d.avg_gp_per_cover != null ? +(required * m * d.avg_gp_per_cover * 30).toFixed(0) : 0,
  }));
  mkChart('chartBreakeven', {
    type:'bar',
    data:{
      labels: scenarios.map(s=>s.label),
      datasets:[
        { label:'GP รายเดือน ()', data:scenarios.map(s=>s.gp), backgroundColor: scenarios.map(s=>
            s.covers >= required ? 'rgba(16,185,129,0.75)' : 'rgba(239,68,68,0.65)'), borderRadius:4, yAxisID:'yGp' },
      ]
    },
    options:{
      responsive:true, maintainAspectRatio:false,
      plugins:{
        datalabels:{display:false}, legend:{display:false},
        annotation:{ annotations:{
          fixed:{ type:'line', yMin:fixedCost, yMax:fixedCost, borderColor:'#f59e0b', borderWidth:2, borderDash:[5,4],
            label:{content:'Fixed Cost '+fmtN(fixedCost), display:true, position:'end', font:{size:10}, color:'#92400e', backgroundColor:'#fef3c7'} },
          actual:{ type:'line', xMin:actual/required-0.5, xMax:actual/required-0.5, borderColor:'#4a70b0', borderWidth:2, borderDash:[4,3],
            label:{content:'Actual avg', display:true, position:'start', font:{size:9}, color:'#4a70b0'} }
        }}
      },
      scales:{
        x:  {ticks:{font:{size:10}}},
        yGp:{ticks:{font:{size:10},callback:v=>fmt(v)}, beginAtZero:true},
      }
    }
  });
}

// ── 7. Forecast ──────────────────────────────────────────────────────────
async function loadForecast() {
  const periods = parseInt(document.getElementById('forecastPeriods')?.value) || 3;
  const params  = new URLSearchParams({ periods });
  if (getBranch())    params.set('branch', getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to',   getMonthTo());
  const d = await api(`${API}/api/forecast?mode=buffet&${params}`).catch(()=>null);
  if (!d || d.error) {
    document.getElementById('forecastKpis').innerHTML = `<div class="col-span-4 text-slate-400 text-sm py-2">${d?.error || 'ไม่มีข้อมูลเพียงพอ (ต้องการ ≥3 เดือน)'}</div>`;
    return;
  }

  const trendColor = d.trend==='up' ? '#10b981' : d.trend==='down' ? '#ef4444' : '#94a3b8';
  const confColor  = d.forecast?.[0]?.confidence==='high'?'var(--good-ink)':d.forecast?.[0]?.confidence==='med'?'#f59e0b':'#ef4444';
  document.getElementById('forecastKpis').innerHTML = [
    { label:'แนวโน้ม',           value: d.trend==='up'?'ขึ้น':d.trend==='down'?'ลง':'คงที่', color:trendColor },
    { label:'R²',              value: d.r_squared!=null ? d.r_squared.toFixed(3) : '—',              color: d.r_squared>0.7?'var(--good-ink)':d.r_squared>0.4?'#f59e0b':'#ef4444' },
    { label:'เติบโต/เดือน %', value: d.monthly_growth_rate!=null ? (d.monthly_growth_rate>0?'+':'')+d.monthly_growth_rate.toFixed(1)+'%' : '—', color:trendColor },
    { label:'Confidence',      value: d.forecast?.[0]?.confidence?.toUpperCase() || '—',             color:confColor },
  ].map(k=>`<div class="rounded p-3 bg-slate-50"><p class="text-[10px] text-slate-500 uppercase">${k.label}</p><p class="text-base font-bold" style="color:${textInk(k.color)}">${k.value}</p></div>`).join('');

  const hist = d.historical || [];
  const fore = d.forecast   || [];
  const confAlpha = { high:'cc', med:'88', low:'44' };

  ['Pax','Rev'].forEach(key => {
    const field = key==='Pax' ? 'pax' : 'revenue';
    const labels = [...hist.map(r=>r.period), ...fore.map(r=>r.period)];
    const histData = hist.map(r=>r[field]||0);
    const foreData = fore.map(r=>r[field]||0);
    const foreColors = fore.map(r => '#4a70b0'+(confAlpha[r.confidence]||'88'));
    mkChart('chartForecast'+key, {
      type:'line',
      data:{
        labels,
        datasets:[
          { label:'Historical', data:[...histData,...fore.map(()=>null)], borderColor:'#4a70b0', backgroundColor:'rgba(31,58,107,0.08)',
            borderWidth:2, pointRadius:3, fill:true, tension:0.3, spanGaps:false },
          { label:'Forecast',   data:[...hist.map(()=>null), histData[histData.length-1], ...foreData],
            borderColor:'#10b981', backgroundColor:'rgba(16,185,129,0.08)',
            borderWidth:2, borderDash:[6,4], pointRadius:4,
            pointBackgroundColor:['#4a70b0',...foreColors], fill:true, tension:0.3, spanGaps:true },
        ]
      },
      options:{
        responsive:true, maintainAspectRatio:false,
        plugins:{
          legend:{labels:{font:{size:10}}}, datalabels:{display:false},
          tooltip:{callbacks:{label:ctx=>ctx.dataset.label+': '+(key==='Rev'?fmt(ctx.raw):fmtN(ctx.raw))}}
        },
        scales:{
          x:{ticks:{font:{size:9}}},
          y:{ticks:{font:{size:9},callback:v=>key==='Rev'?fmt(v):fmtN(v)}, beginAtZero:false},
        }
      }
    });
  });
}

// ── Buffet GP from BoM ───────────────────────────────────────────────────
let _bgpGran = 'month';
function setBgpGran(g) {
  _bgpGran = g;
  ['day','week','month'].forEach(x => {
    const btn = document.getElementById('gpGran-'+x);
    if (!btn) return;
    if (x === g) { btn.className = btn.className.replace('bg-white text-slate-600 hover:bg-emerald-50','bg-emerald-600 text-white'); }
    else         { btn.className = btn.className.replace('bg-emerald-600 text-white','bg-white text-slate-600 hover:bg-emerald-50'); }
  });
  _lastGpKey = null;  // force reload
  loadBuffetGP();
}

let _lastGpKey = null;
// Heavy-Eater Margin Tail — REAL per-cover margin (paid − actually ate).
let _hetKey = null;
async function loadHeavyEaterTail() {
  const _fk = (getBranch() || '') + '|' + (getGroup() || '');
  if (_hetKey === _fk) return; _hetKey = _fk;
  const p = new URLSearchParams();
  if (getBranch()) p.set('branch', getBranch());
  if (getGroup()) p.set('group', getGroup());
  let d; try { d = await api(`${API}/api/buffet/heavy-eater-tail?${p}`); } catch (e) { return; }
  const s = d.summary || {};
  const wEl = document.getElementById('hetWindow');
  if (wEl) wEl.textContent = d.window ? `ช่วงข้อมูล ${d.window[0]} → ${d.window[1]}` : '';
  document.getElementById('hetKpis').innerHTML = [
    _extKpi('หัวที่ขาดทุน', (s.loss_cover_pct != null ? s.loss_cover_pct : 0) + '%', fmtN(s.loss_covers || 0) + ' หัว', s.loss_cover_pct > 2 ? '#ef4444' : '#f59e0b'),
    _extKpi('มูลค่าที่เสียไป', fmt(s.loss_amount || 0), 'ต้นทุน > เงินที่จ่าย', '#ef4444'),
    _extKpi('กำไร/หัว เฉลี่ย', fmt(s.gp_per_head || 0), 'ทั้งช่วง', '#10b981'),
    _extKpi('บิลขาดทุน', fmtN(s.loss_bills || 0), (s.loss_bill_pct || 0) + '% ของบิล', '#7a66b8'),
  ].join('');
  // Auto insight: worst party-size band (the heavy-eater signal)
  const parties = (d.by_party_size || []).filter(r => r.covers > 50).sort((a, b) => b.loss_cover_pct - a.loss_cover_pct);
  const ins = document.getElementById('hetInsight');
  if (ins && parties.length) {
    const w = parties[0], best = parties[parties.length - 1];
    ins.innerHTML = `<div class="card p-3 text-xs" style="background:#fffbeb;border:1px solid #f59e0b">
      <b>โต๊ะ ${esc(w.k)} คน</b> ขาดทุนสูงสุด <b style="color:var(--bad-ink)">${w.loss_cover_pct}%</b> ของหัว (กำไร/หัว ${fmt(w.avg_margin_head)}) — เทียบกับโต๊ะ ${esc(best.k)} คนเพียง ${best.loss_cover_pct}%.
      ${uiQ('het:party', 'ขนาดโต๊ะกับการขาดทุน', '<p>ยิ่งคนน้อย ความเสี่ยง "คนกินเยอะคนเดียว" ยิ่งสูง → จุดที่ควรคุม portion/ราคา tier</p>')}</div>`;
  } else if (ins) { ins.innerHTML = ''; }
  // distribution bars
  const dist = d.distribution || [];
  const maxC = Math.max(1, ...dist.map(x => x.covers || 0));
  const dcol = b => b.bkt <= 1 ? '#ef4444' : b.bkt === 2 ? '#f59e0b' : '#10b981';
  document.getElementById('hetDist').innerHTML = dist.map(b => `
    <div class="flex items-center gap-2 mb-1.5">
      <span class="text-[10.5px] w-32 shrink-0" style="color:var(--text-secondary)">${esc(b.label)}</span>
      <div class="flex-1 rounded-full h-3.5" style="background:var(--bg-subtle)"><div class="h-3.5 rounded-full" style="width:${(b.covers || 0) / maxC * 100}%;background:${dcol(b)}"></div></div>
      <span class="text-[10.5px] tabular-nums w-12 text-right" style="color:var(--text-muted)">${fmtN(b.covers)}</span>
    </div>`).join('') || '<p class="text-xs text-slate-400">ไม่มีข้อมูล</p>';
  document.getElementById('hetParty').innerHTML = (d.by_party_size || []).map(r => `<tr class="border-t border-slate-100">
    <td class="py-1 pr-2 font-medium">${esc(r.k)}</td>
    <td class="py-1 pr-2 text-right tabular-nums font-semibold" style="color:${r.loss_cover_pct > 5 ? 'var(--bad-ink)' : r.loss_cover_pct > 2 ? 'var(--amber-ink)' : 'var(--good-ink)'}">${r.loss_cover_pct}%</td>
    <td class="py-1 text-right tabular-nums">${fmt(r.avg_margin_head)}</td></tr>`).join('') || '<tr><td colspan="3" class="py-3 text-center text-slate-400">—</td></tr>';
  document.getElementById('hetBranch').innerHTML = (d.by_branch || []).slice(0, 8).map(r => `<tr class="border-t border-slate-100">
    <td class="py-1 pr-2 font-medium">${esc(r.branch)}</td>
    <td class="py-1 pr-2 text-right tabular-nums font-semibold" style="color:${r.loss_cover_pct > 3 ? 'var(--bad-ink)' : 'var(--amber-ink)'}">${r.loss_cover_pct}%</td>
    <td class="py-1 text-right tabular-nums" style="color:var(--bad-ink)">${fmt(r.loss_amount)}</td></tr>`).join('') || '<tr><td colspan="3" class="py-3 text-center text-slate-400">—</td></tr>';
}

// Turn-Time & Table Throughput Efficiency
let _turnKey = null;
async function loadTurnEfficiency() {
  const _fk = (getBranch() || '') + '|' + (getGroup() || '');
  if (_turnKey === _fk) return; _turnKey = _fk;
  const p = new URLSearchParams();
  if (getBranch()) p.set('branch', getBranch());
  if (getGroup()) p.set('group', getGroup());
  let d; try { d = await api(`${API}/api/buffet/turn-efficiency?${p}`); } catch (e) { return; }
  const ch = d.chain || {}; const rows = d.branches || [];
  const cw = document.getElementById('turnChain');
  if (cw) cw.textContent = ch.turn_min ? `เฉลี่ยเครือ: นั่ง ${ch.turn_min} นาที · กำไร/หัว ${fmt(ch.gp_cover)} · GP/นาที-โต๊ะ ${ch.gp_per_table_min}` : '';
  const ineff = rows.filter(r => r.inefficient);
  const ins = document.getElementById('turnInsight');
  if (ins && ineff.length) {
    const names = ineff.slice(0, 4).map(r => esc(r.branch)).join(', ');
    ins.innerHTML = `<div class="card p-3 text-xs" style="background:#fef2f2;border:1px solid #ef4444">● <b>${ineff.length} สาขา</b> นั่งนานกว่าเฉลี่ย <b>และ</b> กำไร/หัวต่ำกว่าเฉลี่ย (${names}${ineff.length > 4 ? ', …' : ''}) — โต๊ะถูกครองนานเพื่อกำไรที่น้อยกว่า เป็นเป้าหมายคุม dwell/portion อันดับแรก</div>`;
  } else if (ins) { ins.innerHTML = ''; }
  document.getElementById('turnBody').innerHTML = rows.map(r => `<tr class="border-t border-slate-100 hover:bg-amber-50/40" style="${r.inefficient ? 'background:rgba(239,68,68,0.05)' : ''}">
    <td class="py-1.5 pr-2 font-medium">${r.inefficient ? '● ' : ''}${esc(r.branch)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums" style="color:${(ch.turn_min && r.turn_min > ch.turn_min) ? 'var(--bad-ink)' : 'var(--text-secondary)'}">${r.turn_min != null ? r.turn_min : '—'}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${r.cov_per_tab != null ? r.cov_per_tab : '—'}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums" style="color:${(ch.gp_cover && r.gp_cover < ch.gp_cover) ? 'var(--bad-ink)' : 'var(--good-ink)'}">${r.gp_cover != null ? fmt(r.gp_cover) : '—'}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${r.gp_per_table_occasion != null ? fmt(r.gp_per_table_occasion) : '—'}</td>
    <td class="py-1.5 text-right tabular-nums font-bold" style="color:${r.gp_per_table_min == null ? 'var(--text-muted)' : r.gp_per_table_min >= (ch.gp_per_table_min || 0) ? 'var(--good-ink)' : 'var(--amber-ink)'}">${r.gp_per_table_min != null ? r.gp_per_table_min : '—'}</td>
  </tr>`).join('') || '<tr><td colspan="6" class="py-4 text-center text-slate-400">ไม่มีข้อมูลการหมุนโต๊ะ</td></tr>';
}

async function loadBuffetGP() {
  loadHeavyEaterTail();
  loadTurnEfficiency();
  const _fk = _filterKey() + '|' + _bgpGran;
  if (_lastGpKey === _fk) return;
  _lastGpKey = _fk;

  const params = new URLSearchParams({ granularity: _bgpGran });
  if (getBranch())    params.set('branch', getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to', getMonthTo());

  const d = await api(`${API}/api/buffet/gp?${params}`).catch(() => null);
  if (!d || !d.period_total) {
    document.getElementById('gpBomKpis').innerHTML = '<p class="col-span-6 text-slate-400 text-center py-6">ไม่มีข้อมูล ให้ใส่ต้นทุนต่อหน่วยในตั้งค่า (จัดการต้นทุน)</p>';
    return;
  }

  const pt = d.period_total;
  const noC = pt.bom_cost === 0;

  // ── KPI strip ───────────────────────────────────────────────────────
  const gpColor = !noC ? (pt.margin_pct >= 55 ? '#10b981' : pt.margin_pct >= 45 ? '#f59e0b' : '#ef4444') : '#94a3b8';
  const kpi = (lbl, val, sub, clr) =>
    `<div class="rounded-lg p-3 border" style="background:${clr}10;border-color:${clr}33">
      <p class="text-[10px] text-slate-500 uppercase tracking-wide font-semibold">${lbl}</p>
      <p class="text-lg font-bold" style="color:${textInk(clr)}">${val}</p>
      <p class="text-[10px] text-slate-500">${sub}</p>
    </div>`;
  document.getElementById('gpBomKpis').innerHTML = [
    kpi('Revenue (period)',    fmt(pt.revenue),                       'ex-VAT · ช่วงที่เลือก',          '#4a70b0'),
    kpi('BoM Cost',            noC ? 'ไม่มีต้นทุน' : fmt(pt.bom_cost), 'ต้นทุนตามรายการกดสั่ง',           '#f97316'),
    kpi('Gross Profit',        noC ? '—' : fmt(pt.gp),               'Revenue − BoM Cost',              gpColor),
    kpi('GP Margin %',         noC ? '—' : pt.margin_pct.toFixed(1)+'%', 'เป้า 55-65%',                gpColor),
    kpi('Covers (period)',     fmtN(pt.covers),                       'จาก transaction',                 '#0891b2'),
    kpi('Cost/Cover',          noC ? '—' : (pt.cost_per_cover ? fmt(pt.cost_per_cover) : '—'), 'BoM ÷ covers', '#f59e0b'),
  ].join('');

  // ── Main chart ───────────────────────────────────────────────────────
  const series = d.series || [];
  const labels = series.map(s => s.bucket);
  mkChart('chartGpBomMain', {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: 'รายได้',   data: series.map(s => s.revenue),  backgroundColor: 'rgba(31,58,107,0.65)',  borderRadius: 3, order: 2 },
        { label: 'ต้นทุน BoM',  data: series.map(s => s.bom_cost), backgroundColor: 'rgba(249,115,22,0.65)', borderRadius: 3, order: 2 },
        { label: 'กำไรขั้นต้น', data: series.map(s => s.gp), type: 'line',
          borderColor: '#10b981', backgroundColor: 'rgba(16,185,129,0.08)',
          borderWidth: 2.5, pointRadius: 3, fill: true, tension: 0.3, yAxisID: 'yGp', order: 1 },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { labels: { font: { size: 10 } } },
        datalabels: { display: false },
        tooltip: { callbacks: {
          label: ctx => ctx.dataset.label + ': ' + fmt(ctx.raw),
          afterBody: (items) => {
            const s = series[items[0].dataIndex];
            if (!s || !s.revenue) return '';
            const out = ['', '— สัดส่วนของยอดขาย —'];
            if (s.bom_cost != null) out.push('COGS: ' + (s.bom_cost / s.revenue * 100).toFixed(1) + '%');
            if (s.gp != null)       out.push('Gross Profit: ' + (s.gp / s.revenue * 100).toFixed(1) + '%');
            return out;
          }
        } },
      },
      scales: {
        x:   { ticks: { font: { size: 9 }, maxRotation: 45 } },
        y:   { ticks: { font: { size: 9 }, callback: v => fmt(v) }, beginAtZero: true, title: { display: true, text: 'Revenue / Cost ()', font: { size: 9 } } },
        yGp: { ticks: { font: { size: 9 }, callback: v => fmt(v) }, position: 'right', beginAtZero: true,
               grid: { drawOnChartArea: false }, title: { display: true, text: 'GP ()', font: { size: 9 } } },
      }
    }
  });

  // ── Branch table ─────────────────────────────────────────────────────
  const branches = d.by_branch || [];
  if (!branches.length) {
    document.getElementById('gpBomBranchBody').innerHTML = '<tr><td colspan="8" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
  } else {
    document.getElementById('gpBomBranchBody').innerHTML = branches.map(b => {
      const mClr = b.margin_pct == null ? '#94a3b8' : b.margin_pct >= 55 ? '#10b981' : b.margin_pct >= 45 ? '#f59e0b' : '#ef4444';
      const vsPP = b.vs_network_pp;
      const vsHtml = vsPP == null ? '<td class="py-2 text-right text-slate-300">—</td>'
        : `<td class="py-2 text-right font-semibold" style="color:${vsPP >= 0 ? 'var(--good-ink)' : 'var(--bad-ink)'}">${vsPP >= 0 ? '+' : ''}${vsPP.toFixed(1)}pp</td>`;
      return `<tr class="border-t border-slate-100 hover:bg-slate-50">
        <td class="py-2 pr-4 font-medium">${esc(b.branch)}</td>
        <td class="py-2 pr-4 text-right tabular-nums">${fmtN(b.covers)}</td>
        <td class="py-2 pr-4 text-right tabular-nums">${fmt(b.revenue)}</td>
        <td class="py-2 pr-4 text-right tabular-nums">${b.bom_cost ? fmt(b.bom_cost) : '—'}</td>
        <td class="py-2 pr-4 text-right tabular-nums font-semibold" style="color:${textInk(mClr)}">${b.gp != null ? fmt(b.gp) : '—'}</td>
        <td class="py-2 pr-4 text-right tabular-nums">${b.gp_per_cover != null ? fmt(b.gp_per_cover) : '—'}</td>
        <td class="py-2 pr-4 text-right tabular-nums font-semibold" style="color:${textInk(mClr)}">${b.margin_pct != null ? b.margin_pct.toFixed(1)+'%' : '—'}</td>
        ${vsHtml}
      </tr>`;
    }).join('');
  }

  // ── Top cost SKUs table ───────────────────────────────────────────────
  const skus = d.top_cost_skus || [];
  window._gpbCostSkus = skus;   // for the dive-in / drill-down
  if (!skus.length) {
    document.getElementById('gpBomSkuBody').innerHTML = '<tr><td colspan="5" class="py-4 text-center text-slate-400">ไม่มีข้อมูลต้นทุน SKU</td></tr>';
  } else {
    const maxCost = skus[0].bom_cost || 1;
    document.getElementById('gpBomSkuBody').innerHTML = skus.map((s, i) => {
      const barW = Math.round(s.bom_cost / maxCost * 100);
      return `<tr class="border-t border-slate-100 hover:bg-orange-50/50 cursor-pointer" onclick="gpbSkuDrill('${s.sku_code}')" title="คลิกดูส่วนผสม BoM ของ ${s.sku_code}">
        <td class="py-1.5 pr-3 font-mono text-slate-700">${s.sku_code}</td>
        <td class="py-1.5 pr-3 text-slate-500 max-w-[80px] truncate" title="${s.category}">${s.category}</td>
        <td class="py-1.5 pr-3 text-right tabular-nums">${fmtN(Math.round(s.qty))}</td>
        <td class="py-1.5 pr-3 text-right tabular-nums font-medium text-orange-700">${fmt(s.bom_cost)}</td>
        <td class="py-1.5 text-right">
          <div class="flex items-center justify-end gap-1.5">
            <div class="w-14 h-1.5 rounded-full bg-slate-100 overflow-hidden">
              <div style="width:${barW}%;height:100%;background:#f97316"></div>
            </div>
            <span class="tabular-nums text-orange-600 font-semibold w-8 text-right">${s.cost_pct.toFixed(1)}%</span>
          </div>
        </td>
      </tr>`;
    }).join('');
  }

  // ── Waterfall chart ───────────────────────────────────────────────────
  const wf = d.waterfall || {};
  const rev = wf.revenue || 0;
  // Waterfall segments: [Revenue, −BufCost, −FoodCost, −BevCost, =GP]
  const wfLabels = ['Revenue', 'Buffet Tier\n(BoM Cost)', 'Food Items\n(BoM Cost)', 'Beverages\n(BoM Cost)', 'Gross\nProfit'];
  const wfData   = [rev, -(wf.bom_buffet||0), -(wf.bom_food||0), -(wf.bom_bev||0), wf.gp||0];
  const wfColors = ['#4a70b0', '#f97316', '#ef4444', '#f59e0b', '#10b981'];
  // Floating bar: [start, end] pairs
  const wfFloat = [];
  let running = rev;
  wfFloat.push([0, rev]);         // Revenue bar from 0 to rev
  running = rev;
  running -= (wf.bom_buffet||0);
  wfFloat.push([running, running + (wf.bom_buffet||0)]);
  running -= (wf.bom_food||0);
  wfFloat.push([running, running + (wf.bom_food||0)]);
  running -= (wf.bom_bev||0);
  wfFloat.push([running, running + (wf.bom_bev||0)]);
  wfFloat.push([0, wf.gp||0]);   // GP bar from 0 to gp

  mkChart('chartGpWaterfall', {
    type: 'bar',
    data: {
      labels: wfLabels,
      datasets: [{
        label: 'Amount',
        data: wfFloat,
        backgroundColor: wfColors.map(c => c + 'cc'),
        borderColor: wfColors,
        borderWidth: 1.5,
        borderRadius: 4,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        datalabels: {
          display: true,
          anchor: 'end', align: 'end',
          font: { size: 9, weight: 'bold' },
          color: '#475569',
          formatter: (val) => Array.isArray(val) ? fmt(Math.abs(val[1] - val[0])) : fmt(val),
        },
        tooltip: {
          callbacks: {
            label: ctx => {
              const v = ctx.raw;
              if (Array.isArray(v)) return 'Amount: ' + fmt(Math.abs(v[1] - v[0]));
              return 'Amount: ' + fmt(v);
            }
          }
        }
      },
      scales: {
        x: { ticks: { font: { size: 9 } } },
        y: { ticks: { font: { size: 9 }, callback: v => fmt(v) }, beginAtZero: true },
      }
    }
  });
}

// ── Export GP Excel ──────────────────────────────────────────────────────
function exportGpExcel(e) {
  e.preventDefault();
  // Full-page navigation can't catch a 403 — gate it client-side so a role
  // without cost/profit access gets a message, not a raw JSON error page.
  if (!hasModule('cost.analysis') && !hasModule('profit.pnl')) {
    if (typeof notifyError === 'function') notifyError('บัญชีของคุณไม่มีสิทธิ์ส่งออกข้อมูลกำไร (GP)');
    return;
  }
  const params = new URLSearchParams({ granularity: _bgpGran });
  if (getBranch())    params.set('branch', getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to', getMonthTo());
  window.location.href = `${API}/api/buffet/gp/export?${params}`;
}

// ── Net P&L per branch ───────────────────────────────────────────────────
let _lastPnlKey = null;
async function loadBomPnl() {
  const _fk = _filterKey();
  if (_lastPnlKey === _fk) return;
  _lastPnlKey = _fk;

  const params = new URLSearchParams();
  if (getBranch())    params.set('branch', getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to', getMonthTo());

  const d = await api(`${API}/api/buffet/pnl?${params}`).catch(() => null);
  if (!d) { document.getElementById('pnlBody').innerHTML = '<tr><td colspan="7" class="py-4 text-center text-red-400">โหลดไม่ได้</td></tr>'; return; }

  const rows = d.branches || [];
  // Compute totals from branches
  const totRev = rows.reduce((s,r) => s+(r.revenue||0), 0);
  const totGp  = rows.reduce((s,r) => s+(r.gross_profit||0), 0);
  const totFc  = rows.reduce((s,r) => s+(r.fixed_cost_period||0), 0);
  const totNp  = rows.reduce((s,r) => s+(r.net_profit||0), 0);
  const npColor = c => c == null ? '#94a3b8' : c >= 0 ? '#10b981' : '#ef4444';
  document.getElementById('pnlKpis').innerHTML = [
    { label: 'รายได้รวม',      value: fmt(totRev), color: '#4a70b0' },
    { label: 'กำไรขั้นต้นรวม', value: fmt(totGp),  color: '#10b981' },
    { label: 'ค่าใช้จ่ายคงที่รวม',  value: fmt(totFc),  color: '#f97316' },
    { label: 'กำไรสุทธิรวม',   value: fmt(totNp),  color: npColor(totNp) },
  ].map(k => `<div class="rounded-lg p-3 border" style="background:${k.color}10;border-color:${k.color}33">
    <p class="text-[10px] text-slate-500 uppercase tracking-wide font-semibold">${k.label}</p>
    <p class="text-base font-bold" style="color:${textInk(k.color)}">${k.value}</p>
  </div>`).join('');

  if (!rows.length) {
    document.getElementById('pnlBody').innerHTML = '<tr><td colspan="7" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
    return;
  }
  document.getElementById('pnlBody').innerHTML = rows.map(r => {
    const npc  = npColor(r.net_profit);
    const mClr = r.gp_margin_pct == null ? '#94a3b8' : r.gp_margin_pct >= 55 ? '#10b981' : r.gp_margin_pct >= 45 ? '#f59e0b' : '#ef4444';
    const nmClr = r.np_margin_pct == null ? '#94a3b8' : r.np_margin_pct >= 20 ? '#10b981' : r.np_margin_pct >= 10 ? '#f59e0b' : '#ef4444';
    return `<tr class="border-t border-slate-100 hover:bg-slate-50">
      <td class="py-2 pr-4 font-medium">${esc(r.branch)}</td>
      <td class="py-2 pr-4 text-right tabular-nums">${fmt(r.revenue||0)}</td>
      <td class="py-2 pr-4 text-right tabular-nums text-orange-600">${r.bom_cost ? fmt(r.bom_cost) : '—'}</td>
      <td class="py-2 pr-4 text-right tabular-nums font-semibold" style="color:${textInk(mClr)}">${r.gross_profit != null ? fmt(r.gross_profit) : '—'}${r.gp_margin_pct != null ? ` <span class="text-[10px]">(${r.gp_margin_pct.toFixed(1)}%)</span>` : ''}</td>
      <td class="py-2 pr-4 text-right tabular-nums text-slate-500">${r.fixed_cost_period ? fmt(r.fixed_cost_period) : '<span class="text-slate-300">ไม่ได้ตั้ง</span>'}</td>
      <td class="py-2 pr-4 text-right tabular-nums font-bold" style="color:${textInk(npc)}">${r.net_profit != null ? fmt(r.net_profit) : '—'}</td>
      <td class="py-2 text-right tabular-nums font-semibold" style="color:${textInk(nmClr)}">${r.np_margin_pct != null ? r.np_margin_pct.toFixed(1)+'%' : '—'}</td>
    </tr>`;
  }).join('');
}

// ── Portion Velocity ──────────────────────────────────────────────────────
let _pvData = [];
let _pvFilter = 'all';
let _lastPvKey = null;
function setPvFilter(f) {
  _pvFilter = f;
  const base = 'px-3 py-1.5 rounded-lg font-medium';
  const on   = base + ' bg-violet-600 text-white';
  const off  = base + ' bg-white border border-slate-200 text-slate-600 hover:bg-violet-50';
  ['all','up','down'].forEach(x => {
    const b = document.getElementById('pvFilter-'+x);
    if (b) b.className = x === f ? on : off;
  });
  _renderPv();
}
function _renderPv() {
  const rows = _pvFilter === 'all' ? _pvData
             : _pvFilter === 'up'  ? _pvData.filter(r => (r.trend_pct||0) > 0)
             :                       _pvData.filter(r => (r.trend_pct||0) < 0);
  if (!rows.length) {
    document.getElementById('pvBody').innerHTML = '<tr><td colspan="6" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
    return;
  }
  document.getElementById('pvBody').innerHTML = rows.map(r => {
    const up = (r.trend_pct||0) >= 0;
    const clr = up ? '#10b981' : '#ef4444';
    return `<tr class="border-t border-slate-100 hover:bg-slate-50">
      <td class="py-1.5 pr-3 font-mono text-slate-700">${r.sku_code}</td>
      <td class="py-1.5 pr-3 text-slate-600 max-w-[120px] truncate" title="${esc(r.description||'')}">${esc(r.description||'—')}</td>
      <td class="py-1.5 pr-3 text-slate-400">${r.category||'—'}</td>
      <td class="py-1.5 pr-3 text-right tabular-nums">${r.avg_ratio != null ? r.avg_ratio.toFixed(3) : '—'}</td>
      <td class="py-1.5 pr-3 text-right">${up ? '▲' : '▼'}</td>
      <td class="py-1.5 text-right tabular-nums font-semibold" style="color:${textInk(clr)}">${r.trend_pct != null ? (r.trend_pct >= 0 ? '+' : '') + r.trend_pct.toFixed(1)+'%' : '—'}</td>
    </tr>`;
  }).join('');
}
async function loadPortionVelocity() {
  const _fk = _filterKey();
  if (_lastPvKey === _fk) return;
  _lastPvKey = _fk;
  const params = new URLSearchParams();
  if (getBranch())    params.set('branch', getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to', getMonthTo());
  const d = await api(`${API}/api/buffet/portion-velocity?${params}`).catch(() => null);
  if (!d || !d.skus) { document.getElementById('pvBody').innerHTML = '<tr><td colspan="6" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>'; return; }
  _pvData = d.skus;
  _renderPv();
}

// ── Plate Cost Distribution ───────────────────────────────────────────────
let _lastPlateKey = null;
async function loadPlateCostDist() {
  const _fk = _filterKey();
  if (_lastPlateKey === _fk) return;
  _lastPlateKey = _fk;
  const params = new URLSearchParams();
  if (getBranch())    params.set('branch', getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to', getMonthTo());
  const d = await api(`${API}/api/buffet/plate-cost-dist?${params}`).catch(() => null);
  if (!d || !d.distribution) { return; }

  const hist = d.distribution || [];
  mkChart('chartPlateDist', {
    type: 'bar',
    data: {
      labels: hist.map(h => h.label || `${h.lo}–${h.hi||'+'}`),
      datasets: [{
        label: 'จำนวนบิล',
        data: hist.map(h => h.count),
        backgroundColor: 'rgba(236,72,153,0.55)',
        borderColor: '#ec4899',
        borderWidth: 1,
        borderRadius: 3,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, datalabels: { display: false } },
      scales: { x: { ticks: { font: { size: 8 }, maxRotation: 45 } }, y: { beginAtZero: true, ticks: { font: { size: 9 } } } }
    }
  });

  const p = d.percentiles || {};
  document.getElementById('plateDistKpis').innerHTML = [
    ['Median cost/bill',  fmt(p.p50||0),  '#ec4899'],
    ['P25 (low bills)',   fmt(p.p25||0),  '#a855f7'],
    ['P75 (high bills)',  fmt(p.p75||0),  '#f97316'],
    ['P90 (outliers)',    fmt(p.p90||0),  '#ef4444'],
    ['Mean cost/bill',    fmt(d.mean||0), '#0891b2'],
    ['Count (bills)',     fmtN(d.count||0), '#94a3b8'],
  ].map(([lbl, val, clr]) => `<div class="flex justify-between items-center py-2 border-b border-slate-100">
    <span class="text-xs text-slate-500">${lbl}</span>
    <span class="text-sm font-bold" style="color:${textInk(clr)}">${val}</span>
  </div>`).join('');
}

// ── Cost Sensitivity Simulator ────────────────────────────────────────────
let _simBase = null;
let _simAdjust = {};
let _lastSimKey = null;
async function loadCostSensitivity() {
  const _fk = _filterKey();
  if (_lastSimKey === _fk) return;
  _lastSimKey = _fk;
  const params = new URLSearchParams();
  if (getBranch())    params.set('branch', getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to', getMonthTo());
  const d = await api(`${API}/api/buffet/cost-sensitivity?${params}`).catch(() => null);
  if (!d || !d.skus) { document.getElementById('simSliders').innerHTML = '<p class="text-xs text-slate-400">ไม่มีข้อมูล</p>'; return; }
  _simBase = d;
  _simAdjust = {};
  d.skus.forEach(s => _simAdjust[s.sku_code] = 0);
  _renderSimSliders();
  _updateSimResult();
}
function _renderSimSliders() {
  if (!_simBase) return;
  document.getElementById('simSliders').innerHTML = (_simBase.skus||[]).map(s => `
    <div class="mb-3">
      <div class="flex justify-between text-xs mb-1">
        <span class="font-medium text-slate-700">${s.sku_code} <span class="text-slate-400 font-normal">${(s.description||'').slice(0,30)}</span></span>
        <span class="text-slate-500">${(s.unit_cost||0).toFixed(2)}/unit · BoM ${fmt(s.total_bom_cost||0)}</span>
      </div>
      <div class="flex items-center gap-2">
        <input type="range" min="-50" max="50" step="5" value="${_simAdjust[s.sku_code]||0}"
          oninput="this.nextElementSibling.textContent=(this.value>=0?'+':'')+this.value+'%';_simAdjust['${s.sku_code}']=+this.value;_updateSimResult()"
          class="flex-1 h-2 accent-amber-500">
        <span class="text-xs font-semibold w-12 text-center text-amber-600">${_simAdjust[s.sku_code]>=0?'+':''}${_simAdjust[s.sku_code]}%</span>
      </div>
    </div>`).join('');
}
function _updateSimResult() {
  if (!_simBase) return;
  const baseCost = _simBase.total_bom || 0;
  const baseRev  = _simBase.total_revenue || 0;
  let adjCost = baseCost;
  (_simBase.skus || []).forEach(s => {
    const adj = (_simAdjust[s.sku_code] || 0) / 100;
    adjCost += (s.total_bom_cost || 0) * adj;
  });
  const adjGp  = baseRev - adjCost;
  const adjMgn = baseRev > 0 ? adjGp / baseRev * 100 : 0;
  const baseGp  = baseRev - baseCost;
  const baseMgn = baseRev > 0 ? baseGp / baseRev * 100 : 0;
  const delta = adjMgn - baseMgn;
  const clr = delta >= 0 ? '#10b981' : '#ef4444';
  document.getElementById('simResult').innerHTML = [
    ['Base BoM Cost',  fmt(baseCost),  '#f97316'],
    ['Adjusted Cost',  fmt(adjCost),   '#f97316'],
    ['Base GP',        fmt(baseGp),    '#10b981'],
    ['Simulated GP',   fmt(adjGp),     '#10b981'],
    ['Base Margin',    baseMgn.toFixed(1)+'%', '#4a70b0'],
    ['Sim Margin',     adjMgn.toFixed(1)+'%',  clr],
    ['Δ Margin',       (delta >= 0 ? '+' : '') + delta.toFixed(1)+'pp', clr],
  ].map(([lbl, val, c]) => `<div class="flex justify-between py-1.5 border-b border-slate-100">
    <span class="text-xs text-slate-500">${lbl}</span>
    <span class="text-sm font-bold" style="color:${textInk(c)}">${val}</span>
  </div>`).join('');
}

// ── SKU Lifecycle ─────────────────────────────────────────────────────────
let _lcData = [];
let _lcFilter = 'all';
let _lastLcKey = null;
const _lcColors = { new:'#3b82f6', growing:'#10b981', stable:'#64748b', declining:'#f59e0b', dead:'#ef4444' };
const _lcEmoji  = { new:'', growing:'▲', stable:'→', declining:'▼', dead:'' };
function setLcFilter(f) {
  _lcFilter = f;
  const base = 'px-3 py-1.5 rounded-lg font-medium';
  const on   = base + ' bg-emerald-600 text-white';
  const off  = base + ' bg-white border border-slate-200 text-slate-600 hover:bg-emerald-50';
  ['all','new','growing','stable','declining','dead'].forEach(x => {
    const b = document.getElementById('lcFilter-'+x);
    if (b) b.className = x === f ? on : off;
  });
  _renderLc();
}
function _renderLc() {
  const rows = _lcFilter === 'all' ? _lcData : _lcData.filter(r => r.stage === _lcFilter);
  if (!rows.length) {
    document.getElementById('lcBody').innerHTML = '<tr><td colspan="6" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
    return;
  }
  document.getElementById('lcBody').innerHTML = rows.map(r => {
    const clr = _lcColors[r.stage] || '#64748b';
    const emoji = _lcEmoji[r.stage] || '';
    return `<tr class="border-t border-slate-100 hover:bg-slate-50">
      <td class="py-1.5 pr-3 font-mono text-slate-700">${r.sku_code}</td>
      <td class="py-1.5 pr-3 text-slate-600 max-w-[120px] truncate" title="${esc(r.description||'')}">${esc(r.description||'—')}</td>
      <td class="py-1.5 pr-3 text-slate-400">${r.category||'—'}</td>
      <td class="py-1.5 pr-3 text-right tabular-nums">${r.avg_monthly != null ? fmtN(Math.round(r.avg_monthly)) : '—'}</td>
      <td class="py-1.5 pr-3 text-right tabular-nums" style="color:${textInk(clr)}">${r.slope_pct != null ? (r.slope_pct >= 0 ? '+' : '') + r.slope_pct.toFixed(1)+'%' : '—'}</td>
      <td class="py-1.5 text-right"><span class="px-2 py-0.5 rounded-full text-[10px] font-semibold" style="background:${clr}18;color:${textInk(clr)}">${emoji} ${r.stage}</span></td>
    </tr>`;
  }).join('');
}
async function loadSkuLifecycle(force) {
  const _fk = _filterKey();
  if (!force && _lastLcKey === _fk) return;
  _lastLcKey = _fk;
  const params = new URLSearchParams();
  if (getBranch())    params.set('branch', getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to', getMonthTo());
  const d = await api(`${API}/api/menu/lifecycle?${params}`).catch(() => null);
  if (!d || !d.skus) { document.getElementById('lcBody').innerHTML = '<tr><td colspan="6" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>'; return; }
  _lcData = d.skus;
  const counts = d.summary || {};
  document.getElementById('lcKpis').innerHTML = Object.entries(counts).map(([st, cnt]) => {
    const clr = _lcColors[st] || '#64748b';
    return `<span class="px-3 py-1.5 rounded-full text-xs font-semibold" style="background:${clr}18;color:${textInk(clr)}">${_lcEmoji[st]||''} ${st}: ${cnt}</span>`;
  }).join('');
  _renderLc();
}

// ── BoM Cost Audit ────────────────────────────────────────────────────────
let _bomAuditLoaded = false;
async function loadBomAudit(force) {
  if (!force && _bomAuditLoaded) return;
  _bomAuditLoaded = true;
  document.getElementById('bomAuditBody').innerHTML = '<tr><td colspan="7" class="py-4 text-center text-slate-400">กำลังโหลด…</td></tr>';
  const d = await api(`${API}/api/cost/audit`).catch(() => null);
  if (!d) {
    document.getElementById('bomAuditBody').innerHTML = '<tr><td colspan="7" class="py-4 text-center text-red-400">โหลดไม่ได้</td></tr>';
    return;
  }
  const allItems = d.all || d.flagged || [];
  const kColors = { error:'#ef4444', warning:'#f59e0b', high_z:'#7a66b8', tier:'#0891b2', ok:'#10b981' };
  // KPI chips from summary
  const s = d.summary || {};
  const okCount = (s.total_with_cost||0) - (s.error_count||0) - (s.warning_count||0) - (s.high_z_count||0) - (s.tier_count||0);
  document.getElementById('bomAuditKpis').innerHTML = [
    ['error',   s.error_count,   'Error'],
    ['warning', s.warning_count, 'Warning'],
    ['high_z',  s.high_z_count,  'High Z'],
    ['tier',    s.tier_count,    'Tier/Buffet'],
    ['ok',      okCount,         'OK'],
  ].filter(([,v]) => v > 0).map(([k, v, lbl]) =>
    `<span class="px-3 py-1.5 rounded-full text-xs font-semibold" style="background:${kColors[k]}18;color:${textInk(kColors[k])}">${lbl}: ${v}</span>`
  ).join('');
  if (!allItems.length) {
    document.getElementById('bomAuditBody').innerHTML = '<tr><td colspan="7" class="py-6 text-center text-slate-400">ดี — ไม่พบปัญหา unit_cost</td></tr>';
    return;
  }
  const flagLabel = { error:'Error', warning:'Warning', high_z:'High Z', tier:'Tier', ok:'OK' };
  document.getElementById('bomAuditBody').innerHTML = allItems.map(r => {
    const clr = kColors[r.flag] || '#64748b';
    const sp = r.implied_selling_price;
    const costPct = sp > 0 ? (r.unit_cost / sp * 100).toFixed(1)+'%' : '—';
    return `<tr class="border-t border-slate-100 hover:bg-slate-50">
      <td class="py-1.5 pr-3 font-mono">${r.sku_code}</td>
      <td class="py-1.5 pr-3 max-w-[120px] truncate text-slate-600" title="${esc(r.description||'')}">${esc(r.description||'—')}</td>
      <td class="py-1.5 pr-3 text-slate-400">${r.category||'—'}</td>
      <td class="py-1.5 pr-3 text-right tabular-nums font-medium">${r.unit_cost != null ? ''+r.unit_cost.toFixed(2) : '—'}</td>
      <td class="py-1.5 pr-3 text-right tabular-nums text-slate-400">${sp != null ? ''+sp.toFixed(2) : '—'}</td>
      <td class="py-1.5 pr-3 text-right tabular-nums" style="color:${textInk(clr)};font-weight:600">${costPct}</td>
      <td class="py-1.5 text-right"><span class="px-2 py-0.5 rounded-full text-[10px] font-semibold" style="background:${clr}18;color:${textInk(clr)}">${flagLabel[r.flag]||r.flag}</span></td>
    </tr>`;
  }).join('');
}

// ── Fixed Costs (Settings) ───────────────────────────────────────────────
async function loadFixedCosts() {
  const [fcRes, lcRes, brRes] = await Promise.all([
    api(`${API}/api/branches/fixed-costs`).catch(() => ({})),
    api(`${API}/api/branches/labor-costs`).catch(() => ({})),
    api(`${API}/api/branches`).catch(() => []),
  ]);
  const fc = (fcRes && typeof fcRes === 'object' && !Array.isArray(fcRes)) ? fcRes : {};
  const lc = (lcRes && typeof lcRes === 'object' && !Array.isArray(lcRes)) ? lcRes : {};
  const branches = (Array.isArray(brRes) ? brRes : (brRes.branches || [])).filter(b => b.active && b.name !== 'ALL');
  if (!branches.length) {
    document.getElementById('fixedCostRows').innerHTML = '<p class="text-xs text-slate-400">ยังไม่มีสาขา</p>';
    return;
  }
  const inp = (id, v) => `<input type="number" min="0" step="1000" id="${id}" value="${v != null ? v : ''}" placeholder="0"
      class="w-full border border-slate-300 rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400">`;
  document.getElementById('fixedCostRows').innerHTML =
    `<div class="flex items-center gap-3 mb-1 text-[11px] font-semibold" style="color:var(--text-muted)">
      <span class="w-36 flex-shrink-0">สาขา</span>
      <span class="flex-1">ค่าแรง/เดือน (labor)</span>
      <span class="flex-1">ค่าใช้จ่ายคงที่/เดือน (เช่า/น้ำไฟ)</span>
    </div>` +
    branches.map(b => {
      const k = b.name.replace(/\s/g, '_');
      return `<div class="flex items-center gap-3 mb-1.5">
        <label class="text-sm font-medium text-slate-700 w-36 flex-shrink-0">${esc(b.name)}</label>
        <div class="flex-1">${inp('lc_' + k, lc[b.name])}</div>
        <div class="flex-1">${inp('fc_' + k, fc[b.name])}</div>
      </div>`;
    }).join('');
}
async function saveFixedCosts() {
  const brRes = await api(`${API}/api/branches`).catch(() => []);
  const branches = (Array.isArray(brRes) ? brRes : (brRes.branches || [])).filter(b => b.active && b.name !== 'ALL');
  const fcPayload = {}, lcPayload = {};
  branches.forEach(b => {
    const k = b.name.replace(/\s/g, '_');
    const fe = document.getElementById('fc_' + k); const le = document.getElementById('lc_' + k);
    if (fe) { const v = parseFloat(fe.value); if (!isNaN(v)) fcPayload[b.name] = v; }
    if (le) { const v = parseFloat(le.value); if (!isNaN(v)) lcPayload[b.name] = v; }
  });
  const st = document.getElementById('fixedCostStatus');
  st.textContent = 'กำลังบันทึก…'; st.style.color = 'var(--text-muted)';
  const [r1, r2] = await Promise.all([
    api(`${API}/api/branches/fixed-costs`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ costs: fcPayload }) }).catch(() => null),
    api(`${API}/api/branches/labor-costs`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ costs: lcPayload }) }).catch(() => null),
  ]);
  if (r1 && r1.saved != null && r2 && r2.saved != null) { st.textContent = 'บันทึกแล้ว (ค่าแรง + คงที่)'; st.style.color = 'var(--good-ink)'; }
  else { st.textContent = 'บันทึกไม่ได้'; st.style.color = 'var(--bad-ink)'; }
  setTimeout(() => { st.textContent = ''; }, 3000);
  _lastPnlKey = null; _primeKey = null;   // force P&L + Prime-cost reload
}

// Food-cost benchmark: rank branches vs the chain peer median + the target band.
async function renderBenchFoodCost(params) {
  const body = document.getElementById('benchFoodCostBody'); if (!body) return;
  body.innerHTML = '<p class="text-sm text-slate-400 py-4 text-center">กำลังโหลด…</p>';
  let d; try { d = await api(`${API}/api/gp/by-branch-month?${params}`); }
  catch (e) { body.innerHTML = '<p class="text-sm text-red-400 py-4 text-center">โหลดไม่สำเร็จ</p>'; return; }
  const rows = (d.rows || []).filter(b => b.total_food_cost_pct != null && (b.total_revenue || 0) > 0);
  if (!rows.length) { body.innerHTML = '<p class="text-sm text-slate-400 py-4 text-center">ไม่มีข้อมูลในช่วงนี้</p>'; return; }
  const lo = d.target_lo ?? 55, hi = d.target_hi ?? 70;
  const fcs = rows.map(b => b.total_food_cost_pct).sort((a, b) => a - b);
  const pctile = q => { const i = (fcs.length - 1) * q, a = Math.floor(i), b = Math.ceil(i); return fcs[a] + (fcs[b] - fcs[a]) * (i - a); };
  const median = pctile(0.5), q1 = pctile(0.25), q3 = pctile(0.75);
  const sorted = [...rows].sort((a, b) => a.total_food_cost_pct - b.total_food_cost_pct);  // best (low) first
  const maxFc = Math.max(...fcs, hi) * 1.08;
  const color = fc => fc <= q1 ? '#059669' : fc >= q3 ? '#dc2626' : '#f59e0b';
  const sumEl = document.getElementById('benchFcSummary');
  if (sumEl) sumEl.textContent = `ค่ากลางเครือ ${median.toFixed(1)}% · เป้า ${lo}-${hi}% · ดีสุด ${sorted[0].branch} ${sorted[0].total_food_cost_pct.toFixed(1)}% · แย่สุด ${sorted[sorted.length-1].branch} ${sorted[sorted.length-1].total_food_cost_pct.toFixed(1)}%`;
  const medPct = median / maxFc * 100, loPct = lo / maxFc * 100, hiPct = hi / maxFc * 100;
  body.innerHTML = sorted.map(b => {
    const fc = b.total_food_cost_pct, w = fc / maxFc * 100, c = color(fc), vs = fc - median;
    return `<div class="flex items-center gap-2 mb-1.5 text-xs">
      <div style="width:130px" class="truncate font-medium" title="${esc(b.branch)}">${esc(b.branch)}</div>
      <div style="flex:1;position:relative;height:18px;background:var(--bg-subtle);border-radius:var(--r-md);overflow:hidden">
        <div style="position:absolute;left:${loPct}%;width:${Math.max(0,hiPct-loPct)}%;height:100%;background:rgba(16,185,129,0.12)"></div>
        <div style="height:100%;width:${w}%;background:${c};opacity:.85"></div>
        <div style="position:absolute;left:${medPct}%;top:0;bottom:0;border-left:2px dashed #475569"></div>
      </div>
      <div style="width:52px;color:${textInk(c)}" class="text-right tabular-nums font-semibold">${fc.toFixed(1)}%</div>
      <div style="width:60px" class="text-right tabular-nums ${vs<=0?'text-emerald-600':'text-rose-500'}" title="เทียบค่ากลางเครือ">${vs>0?'+':''}${vs.toFixed(1)}</div>
    </div>`;
  }).join('') + `<div class="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-[10px] text-slate-400">
      <span>quartile บน (ดีกว่าเพื่อน)</span><span>กลาง</span><span>quartile ล่าง (ต้องแก้ก่อน)</span>
      <span>┊ ค่ากลางเครือ ${median.toFixed(1)}%</span><span>▮ แถบเขียวจาง = ช่วงเป้า ${lo}-${hi}%</span>
      <span>คอลัมน์ขวา = เทียบค่ากลาง (− ดีกว่า)</span></div>`;
}

async function loadBenchmark() {
  const _fk = _filterKey(); if (_tabLastKey.benchmark === _fk) return; _tabLastKey.benchmark = _fk;
  const params = new URLSearchParams();
  if (getGroup())     params.set('group', getGroup());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to', getMonthTo());

  const [branches, health] = await Promise.all([
    api(`${API}/api/analysis/branches?${params}`).catch(() => []),
    api(`${API}/api/analysis/health?${params}`).catch(() => []),
  ]);

  const healthMap = {};
  (health || []).forEach(h => healthMap[h.branch] = h);

  // KPI row
  const totalRev = branches.reduce((s, b) => s + (b.revenue || 0), 0);
  const avgTick = branches.length ? branches.reduce((s, b) => s + (b.avg_ticket || 0), 0) / branches.length : 0;
  const avgDisc = branches.length ? branches.reduce((s, b) => s + (b.discount_rate || 0), 0) / branches.length : 0;
  document.getElementById('benchmark-kpi-row').innerHTML = [
    { label: 'รายได้รวมเครือข่าย', value: fmt(totalRev), sub: `จาก ${branches.length} สาขา`, color: '#4a70b0' },
    { label: 'สาขาที่ทำได้สูงสุด', value: branches[0]?.branch || '—', sub: fmt(branches[0]?.revenue || 0), color: 'var(--good-ink)' },
    { label: 'ยอดบิลเฉลี่ยเครือข่าย', value: fmt(avgTick), sub: 'ต่อใบเสร็จ', color: '#0891b2' },
    { label: '% ส่วนลดเครือข่าย', value: `${avgDisc.toFixed(1)}%`, sub: 'ของยอดบิล', color: '#f59e0b' },
  ].map(k => `<div class="card p-4"><p class="kpi-label">${k.label}</p><p class="kpi-value" style="color:${textInk(k.color)}">${k.value}</p><p class="text-xs mt-1" style="color:var(--text-muted)">${k.sub}</p></div>`).join('');

  renderBenchFoodCost(params);

  // ── Branch KPI table: latest month vs previous month (MoM), sortable ──
  const curMonth = getMonthTo() || await api(`${API}/api/months`)
    .then(ms => Array.isArray(ms) && ms.length ? ms[ms.length - 1] : '').catch(() => '');
  const prevMonth = curMonth ? monthAdd(curMonth, -1) : '';
  const grp = getGroup() ? `&group=${encodeURIComponent(getGroup())}` : '';
  const snap = m => m
    ? api(`${API}/api/analysis/branches?month_from=${m}&month_to=${m}${grp}`).catch(() => [])
    : Promise.resolve([]);
  const [curB, prevB] = await Promise.all([snap(curMonth), snap(prevMonth)]);
  const prevMap = {}; (prevB || []).forEach(b => prevMap[b.branch] = b);
  const baseRows = (curB && curB.length) ? curB : branches;   // fallback to range if no month resolved
  _benchRows = baseRows.map(b => {
    const h = healthMap[b.branch] || {};
    const p = prevMap[b.branch];
    return {
      branch: b.branch,
      revenue: b.revenue,         prev_revenue: p ? p.revenue : null,
      tx_count: b.tx_count,       prev_tx_count: p ? p.tx_count : null,
      avg_ticket: b.avg_ticket,   prev_avg_ticket: p ? p.avg_ticket : null,
      discount_rate: b.discount_rate, prev_discount_rate: p ? p.discount_rate : null,
      cust_per_tx: b.tx_count > 0 ? b.customer_count / b.tx_count : null,
      prev_cust_per_tx: (p && p.tx_count > 0) ? p.customer_count / p.tx_count : null,
      revenue_score: h.revenue_score ?? null, growth_score: h.growth_score ?? null,
      consistency_score: h.consistency_score ?? null, momentum_score: h.momentum_score ?? null,
      score: h.score ?? null,
      breakdown: (h && h.score != null) ? `รายได้ ${h.revenue_score?.toFixed(0)}×40% + เติบโต ${h.growth_score?.toFixed(0)}×30% + สม่ำเสมอ ${h.consistency_score?.toFixed(0)}×20% + โมเมนตัม ${h.momentum_score?.toFixed(0)}×10% = ${h.score.toFixed(1)}` : '',
    };
  });
  const lbl = document.getElementById('benchCompareLabel');
  if (lbl) lbl.textContent = curMonth
    ? `เทียบ ${curMonth} กับเดือนก่อน ${prevMonth || '—'} · คลิกหัวคอลัมน์เพื่อเรียงลำดับ · ตัวเลขเล็กใต้ค่า = MoM`
    : 'คลิกหัวคอลัมน์เพื่อเรียงลำดับ · ตัวเลขเล็กใต้ค่า = MoM';
  renderBenchTable();

  // Revenue bar chart
  mkChart('chartBenchRevenue', {
    type: 'bar',
    data: {
      labels: branches.map(b => b.branch),
      datasets: [{
        label: 'รายได้',
        data: branches.map(b => b.revenue),
        backgroundColor: PALETTE_6,
        borderRadius: 6,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, datalabels: { display: false } },
      scales: { y: { ticks: { callback: v => fmt(v) } } }
    }
  });

  // Health bar chart
  const healthSorted = [...(health || [])].sort((a, b) => b.score - a.score);
  mkChart('chartBenchHealth', {
    type: 'bar',
    data: {
      labels: healthSorted.map(h => h.branch),
      datasets: [{
        label: 'Health Score',
        data: healthSorted.map(h => h.score),
        backgroundColor: healthSorted.map(h => h.score >= 70 ? '#10b981' : h.score >= 50 ? '#f59e0b' : '#ef4444'),
        borderRadius: 6,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, datalabels: { display: false } },
      scales: { y: { min: 0, max: 100, ticks: { callback: v => v + '%' } } }
    }
  });
}

// ── Branch KPI table: sortable + MoM (vs previous month) ──
let _benchRows = [];
let _benchSort = { key: 'revenue', dir: -1 };
function monthAdd(ym, delta) {
  const [y, m] = ym.split('-').map(Number);
  const d = new Date(y, (m - 1) + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}
function _benchMom(cur, prev, opt) {
  opt = opt || {};
  if (cur == null || prev == null || prev === 0) return '';
  const pp = !!opt.pp, hb = opt.hb !== false;
  const d = pp ? (cur - prev) : (cur - prev) / prev * 100;
  if (!isFinite(d)) return '';
  const good = hb ? d >= 0 : d <= 0;
  const col = Math.abs(d) < 0.05 ? '#94a3b8' : (good ? '#10b981' : '#ef4444');
  const ar = d > 0 ? '▲' : (d < 0 ? '▼' : '▬');
  return `<div class="text-[10px] tabular-nums" style="color:${textInk(col)}">${ar} ${Math.abs(d).toFixed(1)}${pp ? 'pp' : '%'}</div>`;
}
function _benchScoreCell(v, accent) {
  if (v == null) return '<td class="py-2 pr-3 text-right text-slate-300">—</td>';
  const pct = Math.max(0, Math.min(100, v));
  return `<td class="py-2 pr-3 text-right"><div class="inline-flex items-center justify-end gap-1.5">
    <div class="w-10 h-1.5 rounded-full bg-slate-100 overflow-hidden"><div style="width:${pct}%;height:100%;background:${accent}"></div></div>
    <span class="text-[11px] tabular-nums" style="color:${textInk(accent)};font-weight:600">${v.toFixed(0)}</span></div></td>`;
}
function sortBench(key) {
  if (_benchSort.key === key) _benchSort.dir *= -1;
  else _benchSort = { key, dir: key === 'branch' ? 1 : -1 };
  renderBenchTable();
}
function renderBenchTable() {
  const { key, dir } = _benchSort;
  const rows = [..._benchRows].sort((a, b) => {
    const x = a[key], y = b[key];
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    return (typeof x === 'string' ? x.localeCompare(y) : x - y) * dir;
  });
  document.querySelectorAll('.bench-caret').forEach(s => {
    s.textContent = (s.dataset.k === key) ? (dir < 0 ? '▾' : '▴') : '';
  });
  const body = document.getElementById('benchmarkBody');
  if (!body) return;
  body.innerHTML = rows.map(r => {
    const scoreColor = r.score != null ? (r.score >= 70 ? 'var(--good-ink)' : r.score >= 50 ? '#f59e0b' : '#ef4444') : '#94a3b8';
    const cpt = r.cust_per_tx != null ? r.cust_per_tx.toFixed(1) : '—';
    const cell = (val, mom) => `<td class="py-2 pr-3 text-right tabular-nums"><div>${val}</div>${mom}</td>`;
    const discCls = (r.discount_rate || 0) > 15 ? 'text-red-500 font-medium' : '';
    return `<tr class="border-t border-slate-100 hover:bg-slate-50">
      <td class="py-2 pr-3 font-medium">${esc(r.branch)}</td>
      ${cell(fmt(r.revenue), _benchMom(r.revenue, r.prev_revenue, { hb: true }))}
      ${cell(fmtN(r.tx_count), _benchMom(r.tx_count, r.prev_tx_count, { hb: true }))}
      ${cell(fmt(r.avg_ticket), _benchMom(r.avg_ticket, r.prev_avg_ticket, { hb: true }))}
      ${cell(`<span class="${discCls}">${(r.discount_rate || 0).toFixed(1)}%</span>`, _benchMom(r.discount_rate, r.prev_discount_rate, { hb: false, pp: true }))}
      ${cell(cpt, _benchMom(r.cust_per_tx, r.prev_cust_per_tx, { hb: true }))}
      ${_benchScoreCell(r.revenue_score, '#4a70b0')}
      ${_benchScoreCell(r.growth_score, '#10b981')}
      ${_benchScoreCell(r.consistency_score, '#06b6d4')}
      ${_benchScoreCell(r.momentum_score, '#f59e0b')}
      <td class="py-2 text-right" title="${r.breakdown || ''}"><span style="color:${textInk(scoreColor)};font-weight:700;font-size:13px">${r.score != null ? r.score.toFixed(0) : '—'}</span></td>
    </tr>`;
  }).join('');
}
