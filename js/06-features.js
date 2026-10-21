// ─── 06-features.js — slice 6/9 of the former app.js (P2 structure refactor) ───
// Sequential load order matters: index.html loads 01→09; classic scripts
// share one global scope, so this equals the original single file.

// ════════════════════════════════════════════════════════════════
// PHASE 7 — Async dive-in enhancers (real breakdown, not just bigger chart)
// ════════════════════════════════════════════════════════════════
const _DIVE_ENHANCERS = {
  chartTrend:       _diveEnhanceTrend,
  chartBranchCompare: _diveEnhanceBranchCompare,
  chartCat:         _diveEnhanceCategory,
  chartBreakdown:   _diveEnhanceCategory,
  chartProfitBranch: _diveEnhanceProfitBranch,
};

async function _diveEnhanceTrend(_cfg) {
  const p = new URLSearchParams();
  if (getBranch())    p.set('branch',     getBranch());
  if (getMonthFrom()) p.set('month_from', getMonthFrom());
  if (getMonthTo())   p.set('month_to',   getMonthTo());

  let gp, cats;
  try {
    [gp, cats] = await Promise.all([
      api(`${API}/api/gp/by-branch-month?${p}`),
      api(`${API}/api/sales/category?${p}`).catch(() => null),
    ]);
  } catch (e) {
    return `<p class="text-xs text-red-400 p-2">โหลดไม่สำเร็จ: ${e.message}</p>`;
  }

  const branches = (gp.rows || []).sort((a, b) => (b.total_revenue || 0) - (a.total_revenue || 0));
  const branchRows = branches.map(b => {
    const gpp = b.total_gp_pct != null ? b.total_gp_pct : (b.total_revenue ? (b.total_revenue - b.total_cogs) / b.total_revenue * 100 : null);
    const gv = gpp != null ? gpp.toFixed(1) : '—';
    const color = gpp == null ? '' : gpp >= 60 ? 'var(--good-ink)' : gpp >= 40 ? '#d97706' : 'var(--bad-ink)';
    return `<tr class="border-b cursor-pointer hover:bg-indigo-50/30" style="border-color:var(--border)"
                onclick="closeDive();drillIntoBranch('${jsAttr(b.branch)}')" title="เจาะลึก ${esc(b.branch)}">
      <td class="py-1.5 pr-2 font-medium">${esc(b.branch)} <span style="opacity:.4">›</span></td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${fmt(b.total_revenue)}</td>
      <td class="py-1.5 text-right tabular-nums font-semibold" style="color:${textInk(color)}">${gv}%</td>
    </tr>`;
  }).join('');

  let catHtml = '';
  if (Array.isArray(cats) && cats.length) {
    const grand = cats.reduce((s, c) => s + (c.revenue || 0), 0) || 1;
    catHtml = _diveSection('หมวดสินค้า Top 5', '',
      cats.slice(0, 5).map(c => _diveMetric(c.category, `${fmt(c.revenue)} (${(c.revenue/grand*100).toFixed(0)}%)`)).join(''));
  }

  return _diveSection('แยกรายสาขา (คลิกเพื่อเจาะลึก)', '', `
    <div style="overflow-x:auto;margin-top:4px">
      <table class="text-xs w-full">
        <thead><tr style="color:var(--text-muted)" class="border-b">
          <th class="py-1 pr-2 text-left">สาขา</th>
          <th class="py-1 pr-2 text-right">รายได้</th>
          <th class="py-1 text-right">GP%</th>
        </tr></thead>
        <tbody>${branchRows}</tbody>
      </table>
    </div>`) + catHtml;
}

async function _diveEnhanceBranchCompare(_cfg) {
  const p = new URLSearchParams();
  if (getMonthFrom()) p.set('month_from', getMonthFrom());
  if (getMonthTo())   p.set('month_to',   getMonthTo());

  let d;
  try { d = await api(`${API}/api/gp/by-branch-month?${p}`); }
  catch (e) { return `<p class="text-xs text-red-400 p-2">โหลดไม่สำเร็จ</p>`; }

  const branches = (d.rows || []).sort((a, b) => (b.total_revenue || 0) - (a.total_revenue || 0));
  const grandRev = branches.reduce((s, b) => s + (b.total_revenue || 0), 0) || 1;
  const rows = branches.map(b => {
    const share = ((b.total_revenue || 0) / grandRev * 100).toFixed(1);
    const gp    = (b.total_revenue || 0) - (b.total_cogs || 0);
    const gpp   = b.total_revenue ? (gp / b.total_revenue * 100).toFixed(1) : '—';
    return `<tr class="border-b cursor-pointer hover:bg-indigo-50/30" style="border-color:var(--border)"
                onclick="closeDive();drillIntoBranch('${jsAttr(b.branch)}')" title="เจาะลึก ${esc(b.branch)}">
      <td class="py-1.5 pr-2 font-medium">${esc(b.branch)}</td>
      <td class="py-1.5 pr-2 text-right">${fmt(b.total_revenue)}</td>
      <td class="py-1.5 pr-2 text-right" style="color:var(--text-muted)">${share}%</td>
      <td class="py-1.5 text-right font-semibold" style="color:${parseFloat(gpp)>=60?'var(--good-ink)':parseFloat(gpp)>=40?'var(--amber-ink)':'var(--bad-ink)'}">${gpp}%</td>
    </tr>`;
  }).join('');

  return _diveSection('GP รายสาขา (คลิกเจาะลึก)', '', `
    <div style="overflow-x:auto;margin-top:4px">
      <table class="text-xs w-full">
        <thead><tr style="color:var(--text-muted)" class="border-b">
          <th class="py-1 pr-2 text-left">สาขา</th>
          <th class="py-1 pr-2 text-right">รายได้</th>
          <th class="py-1 pr-2 text-right">%รวม</th>
          <th class="py-1 text-right">GP%</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`);
}

async function _diveEnhanceCategory(_cfg) {
  const p = new URLSearchParams();
  if (getBranch())    p.set('branch',     getBranch());
  if (getMonthFrom()) p.set('month_from', getMonthFrom());
  if (getMonthTo())   p.set('month_to',   getMonthTo());

  let cats;
  try { cats = await api(`${API}/api/sales/category?${p}`); }
  catch (e) { return `<p class="text-xs text-red-400 p-2">โหลดไม่สำเร็จ</p>`; }

  if (!Array.isArray(cats)) return '';
  const top = cats.slice(0, 10);
  const grand = cats.reduce((s, c) => s + (c.revenue || 0), 0) || 1;
  const rows = top.map((c, i) => {
    const pct = ((c.revenue || 0) / grand * 100).toFixed(1);
    return `<tr class="border-b" style="border-color:var(--border)">
      <td class="py-1.5 pr-2" style="color:var(--text-muted)">${i + 1}</td>
      <td class="py-1.5 pr-2 font-medium">${esc(c.category)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${fmt(c.revenue)}</td>
      <td class="py-1.5 text-right tabular-nums font-semibold">${pct}%</td>
    </tr>`;
  }).join('');

  return _diveSection('Top หมวดสินค้า (ช่วงนี้)', '', `
    <div style="overflow-x:auto;margin-top:4px">
      <table class="text-xs w-full">
        <thead><tr style="color:var(--text-muted)" class="border-b">
          <th class="py-1 pr-2">#</th>
          <th class="py-1 pr-2 text-left">หมวด</th>
          <th class="py-1 pr-2 text-right">รายได้</th>
          <th class="py-1 text-right">%รวม</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`);
}

async function _diveEnhanceProfitBranch(_cfg) {
  return _diveEnhanceBranchCompare(_cfg);
}

// ── DIVE IN — chart zoom + side-by-side analysis ──────────────────────────
function closeDive() {
  const m = document.getElementById('diveModal');
  if (m) m.classList.add('hidden');
  if (chartInstances['zoomChart']) {
    try { chartInstances['zoomChart'].destroy(); } catch (e) {}
    delete chartInstances['zoomChart'];
  }
}

function diveInChart(chartId) {
  // Dedicated rich dive for the Most-Ordered chart (TOP 50 + % + MoM)
  if (chartId === 'chartMostOrdered') { diveMostOrdered(); return; }
  const orig = chartInstances[chartId];
  if (!orig) { console.warn('diveInChart: no chart instance for', chartId); return; }

  const modal = document.getElementById('diveModal');
  const title = document.getElementById('diveTitle');
  const body  = document.getElementById('diveBody');
  if (!modal || !title || !body) { console.error('diveInChart: modal DOM missing'); return; }

  const titleMap = {
    chartTrend: 'Revenue Trend',           chartCat: 'สัดส่วนหมวดสินค้า',
    chartBreakdown: 'แยกรายได้',     chartTopSkus: 'Top SKUs',
    chartMostOrdered: 'สั่งบ่อยที่สุด',      chartMostRev: 'Top Revenue Items',
    chartParty: 'Party Size',              chartFdSplit: 'Food vs Drink',
    chartHours: 'ชั่วโมงที่ขายดี',              chartCmpBar: 'Month over Month',
    chartDaily: 'Daily Revenue',           chartDow: 'Day of Week',
    chartBranchCompare: 'Branch Revenue',  chartDiscountTrend: 'Discount Trend',
    chartHourly: 'Hourly Revenue',         chartDowTime: 'DOW · Time Analysis',
    chartMenuScatter: 'Menu Engineering Scatter',
    chartCostCatMix: 'Cost · สัดส่วนหมวดสินค้า', cmpBranchChart: 'Branches — A vs B',
    mceScatter: 'เมนูเสิร์ฟเยอะ × สัดส่วนต้นทุน',
    'cov-peak': 'ชั่วโมงขายดี', chartProfitBranch: 'กำไรรายสาขา',
    chartHomeForecast: 'Month-end Forecast',
    // วิเคราะห์วัตถุดิบ (Menu Impact) — enhancers registered in js/07-home.js
    miChart: 'วัตถุดิบ: แนวโน้มรายเดือน (ปริมาณ/หัว & % บิลที่สั่ง)',
    ptChartMix: 'Tier ราคา: สัดส่วนคนทานรายเดือน',
    ptChartCogs: 'Tier ราคา: ต้นทุนอาหาร/หัว รายเดือน',
    mgtTierTrendMix: 'Tier ราคาบุฟเฟ่ต์: ลูกค้าเลือกราคาไหน (% ของคนทาน)',
    mgtTierTrendCogs: 'Tier ราคาบุฟเฟ่ต์: ต้นทุนอาหาร/หัว แต่ละราคา',
    // ความสดของข้อมูล — enhancers registered in js/07-home.js
    ctmChartDays: 'สาขาที่มีข้อมูล ต่อวัน (30 วันล่าสุด)',
    ctmChartClose: 'เวลาปิดเฉลี่ยรายสาขา',
  };

  try {
    // 1. Build a clean cloned config for the modal canvas
    const cfg = JSON.parse(JSON.stringify({
      type: orig.config.type,
      data: orig.config.data,
      options: orig.config._config?.options || orig.config.options || {}
    }));
    cfg.data = {
      labels: [...(orig.data.labels || [])],
      datasets: orig.data.datasets.map(ds => ({ ...ds }))
    };
    cfg.options = cfg.options || {};
    cfg.options.responsive = true;
    cfg.options.maintainAspectRatio = false;

    // 2. Build analysis sidebar — static baseline first; async enhancer replaces it
    const hasEnhancer = typeof _DIVE_ENHANCERS !== 'undefined' && !!_DIVE_ENHANCERS[chartId];
    const analysisHtml = hasEnhancer
      ? '<div id="diveEnhancerPane" style="padding:16px 4px"><span class="text-xs" style="color:var(--text-muted)">กำลังโหลดข้อมูลเชิงลึก…</span></div>'
      : ((typeof analyzeChartForDive === 'function')
          ? analyzeChartForDive(chartId, cfg)
          : '<p class="text-sm text-slate-400">No analysis available.</p>');

    // 3. Set ALL content BEFORE showing modal (lesson from the drill-down bug)
    title.textContent = `${titleMap[chartId] || chartId}`;
    body.innerHTML = `
      <div class="dive-layout">
        <div class="dive-chart-wrap rounded-xl p-3" style="background:var(--bg-subtle);position:relative">
          <canvas id="zoomChart"></canvas>
        </div>
        <div class="dive-insight-bar dive-side" id="diveInsightPanel">
          ${analysisHtml}
          <div class="dive-section" id="diveActionsSection">
            <div class="dive-section-title">Actions</div>
            <button onclick="exportChartData('${chartId}')" class="dive-action-btn">ส่งออกข้อมูลกราฟนี้ (CSV)</button>
            <button onclick="if(chartInstances['zoomChart'])chartInstances['zoomChart'].resetZoom&&chartInstances['zoomChart'].resetZoom()" class="dive-action-btn">⟳ ล้างการซูมและเลื่อน</button>
            <button onclick="closeDive()" class="dive-action-btn">✕ ปิด (Esc)</button>
          </div>
        </div>
      </div>`;

    // 4. Now show the modal
    modal.classList.remove('hidden');

    // 5. Render the cloned chart inside the modal canvas
    setTimeout(() => {
      try {
        if (chartInstances['zoomChart']) chartInstances['zoomChart'].destroy();
        const canvas = document.getElementById('zoomChart');
        if (canvas) chartInstances['zoomChart'] = new Chart(canvas, cfg);
      } catch (e) {
        console.error('diveInChart canvas render error:', e);
        body.innerHTML = `<p class="text-sm text-red-500 py-4">⚠ Could not render chart: ${e.message}</p>`;
      }
    }, 50);

    // 6. Fire async enhancer (replaces the loading pane inside the panel)
    if (hasEnhancer) {
      _DIVE_ENHANCERS[chartId](cfg).then(html => {
        const pane = document.getElementById('diveEnhancerPane');
        const actSec = document.getElementById('diveActionsSection');
        if (pane && html) {
          pane.outerHTML = html;
          // Ensure actions section is still present (enhancer html goes before it)
          if (actSec) actSec.style.display = '';
        }
      }).catch(err => {
        const pane = document.getElementById('diveEnhancerPane');
        if (pane) pane.innerHTML = `<p class="text-xs text-red-400 p-2">โหลดไม่สำเร็จ: ${err.message}</p>`;
      });
    }
  } catch (e) {
    console.error('diveInChart error:', e);
    title.textContent = 'Error';
    body.innerHTML = `<p class="text-sm text-red-500 py-4">⚠ Could not open dive-in: ${e.message}</p>`;
    modal.classList.remove('hidden');
  }
}

// Esc closes dive modal
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDive(); });

// ── Dedicated dive for "สั่งบ่อยที่สุด": TOP 50 + % share + MoM vs last month ──
// (keeps the standard dive-layout format; uses qty_share_pct / qty_mom_pct the
//  top_ordered endpoint already returns. Sparkline/YTD is a follow-up that needs
//  a per-SKU monthly series from the backend.)
function _sparkSvg(vals, color) {
  if (!vals || vals.length < 2) return '';
  const w = 60, h = 16, max = Math.max(...vals), min = Math.min(...vals), rng = (max - min) || 1;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1) * w).toFixed(1)},${(h - (v - min) / rng * (h - 2) - 1).toFixed(1)}`).join(' ');
  const lastUp = vals[vals.length - 1] >= vals[0];
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="vertical-align:middle;margin-left:6px;opacity:.9"><polyline points="${pts}" fill="none" stroke="${color || (lastUp ? '#10b981' : '#ef4444')}" stroke-width="1.4" stroke-linejoin="round"/></svg>`;
}
async function diveMostOrdered() {
  const modal = document.getElementById('diveModal');
  const title = document.getElementById('diveTitle');
  const body  = document.getElementById('diveBody');
  if (!modal || !title || !body) return;
  title.textContent = 'สั่งบ่อยที่สุด · TOP 50';
  body.innerHTML = '<p class="text-sm text-slate-400" style="padding:24px">กำลังโหลด TOP 50…</p>';
  modal.classList.remove('hidden');

  let d;
  try { d = await api(`${API}/api/sales/top_ordered${qp({ limit: 50, sort: 'qty' })}`); }
  catch (e) { body.innerHTML = `<p class="text-sm text-red-500" style="padding:24px">โหลดข้อมูลไม่สำเร็จ: ${e.message}</p>`; return; }
  const items = (d && d.items) ? d.items : [];
  if (!items.length) { body.innerHTML = '<p class="text-sm text-slate-400" style="padding:24px">ไม่มีข้อมูล</p>'; return; }

  const clean = s => esc((s || '—').replace(/^\((?:A|B|T)\)\s*/, ''));
  const totalAll = d.total_qty || items.reduce((s, r) => s + (r.qty || 0), 0);
  const sharePct = r => r.qty_share_pct != null ? r.qty_share_pct : (totalAll ? r.qty / totalAll * 100 : 0);
  const nums = items.map(r => r.qty || 0);
  const sum = nums.reduce((a, b) => a + b, 0);
  const avg = sum / nums.length;
  const peak = items[0], low = items[items.length - 1];

  // Outlier (reuse the shared IQR detector)
  const o = _detectOutliers(nums);
  let outlierSection = '';
  if (o.hasOutliers) {
    const names = o.indices.map(i => clean(items[i].description)).slice(0, 3).join(', ');
    outlierSection = _diveSection('การวิเคราะห์ Outlier', '', _diveBullet('',
      `พบ <b>${o.indices.length}</b> ค่าผิดปกติ (${names}${o.indices.length > 3 ? ', …' : ''}) — ทำให้ค่าเฉลี่ยเบี่ยงเบนไป <b>${o.gainPct.toFixed(1)}%</b><br>ถ้าตัด outlier ออก ค่าเฉลี่ยจะเป็น <b>${fmtN(Math.round(o.trimmedAvg))}</b> (จาก <b>${fmtN(Math.round(o.fullAvg))}</b>)`,
      'warn'));
  }

  // MoM movement vs previous month
  const withMom = items.filter(r => r.qty_mom_pct != null);
  const ups = withMom.filter(r => r.qty_mom_pct > 0).sort((a, b) => b.qty_mom_pct - a.qty_mom_pct);
  const downs = withMom.filter(r => r.qty_mom_pct < 0).sort((a, b) => a.qty_mom_pct - b.qty_mom_pct);
  const newCnt = items.length - withMom.length;
  const momRow = (r, up) => `<div class="dive-metric"><span class="lbl">${clean(r.description)}${_sparkSvg(r.spark, up ? '#10b981' : '#ef4444')}</span><span class="val ${up ? 'good' : 'bad'}">${up ? '▲' : '▼'} ${Math.abs(r.qty_mom_pct).toFixed(1)}%</span></div>`;
  const momHtml = withMom.length
    ? `<p class="text-[11px] mb-1" style="color:var(--text-muted)">▲ ${ups.length} ขึ้น · ▼ ${downs.length} ลง${newCnt ? ` · ${newCnt} ใหม่` : ''} · เส้น = YTD</p>`
        + (ups.length ? `<p class="text-[10px] font-semibold mt-1" style="color:var(--good-ink)">มาแรง</p>` + ups.slice(0, 5).map(r => momRow(r, true)).join('') : '')
        + (downs.length ? `<p class="text-[10px] font-semibold mt-1" style="color:var(--bad-ink)">แผ่วลง</p>` + downs.slice(0, 5).map(r => momRow(r, false)).join('') : '')
    : '<p class="text-xs" style="color:var(--text-muted)">ไม่มีข้อมูลเดือนก่อนให้เทียบ</p>';

  const analysisHtml =
    _diveSection('Overview', '',
      _diveMetric('Total', fmtN(sum)) +
      _diveMetric('Average', fmtN(Math.round(avg))) +
      _diveMetric('Peak', `${fmtN(peak.qty)} · ${clean(peak.description)}`, 'good') +
      _diveMetric('Low', `${fmtN(low.qty)} · ${clean(low.description)}`, 'bad') +
      _diveMetric('Points', String(items.length)))
    + outlierSection
    + _diveSection('เทียบเดือนก่อน (MoM)', '', momHtml);

  body.innerHTML = `
    <div class="dive-layout">
      <div class="dive-chart-wrap rounded-xl p-3" style="background:var(--bg-subtle);position:relative">
        <canvas id="zoomChart"></canvas>
      </div>
      <div class="dive-insight-bar dive-side">
        ${analysisHtml}
        <div class="dive-section">
          <div class="dive-section-title">Actions</div>
          <button onclick="exportChartData('chartMostOrdered')" class="dive-action-btn">ส่งออกข้อมูลกราฟนี้ (CSV)</button>
          <button onclick="closeDive()" class="dive-action-btn">✕ ปิด (Esc)</button>
        </div>
      </div>
    </div>`;

  setTimeout(() => {
    if (chartInstances['zoomChart']) chartInstances['zoomChart'].destroy();
    const canvas = document.getElementById('zoomChart');
    if (!canvas) return;
    const maxV = Math.max(...nums, 1);
    chartInstances['zoomChart'] = new Chart(canvas, {
      type: 'bar',
      data: {
        labels: items.map(r => clean(r.description).slice(0, 28)),
        datasets: [{ label: 'จำนวน', data: nums, backgroundColor: nums.map(v => v === maxV ? '#ef4444' : '#10b981'), borderRadius: 4 }]
      },
      options: {
        indexAxis: 'y', responsive: true, maintainAspectRatio: false,
        layout: { padding: { right: 70 } },
        plugins: {
          legend: { display: false },
          datalabels: {
            display: true, anchor: 'end', align: 'end', color: '#475569', font: { size: 9, weight: '600' },
            formatter: (v, ctx) => fmtN(v) + '  ' + sharePct(items[ctx.dataIndex]).toFixed(1) + '%'
          },
          tooltip: {
            callbacks: {
              label: ctx => {
                const r = items[ctx.dataIndex], mom = r.qty_mom_pct;
                return ' ' + fmtN(ctx.raw) + ' pcs · ' + sharePct(r).toFixed(1) + '%' + (mom != null ? ` · MoM ${mom > 0 ? '+' : ''}${mom.toFixed(1)}%` : ' · ใหม่');
              }
            }
          }
        },
        scales: {
          x: { grid: { color: '#f1f5f9' }, ticks: { color: '#94a3b8', font: { size: 10 } } },
          y: { grid: { display: false }, ticks: { color: '#64748b', font: { size: 10 }, autoSkip: false } }
        }
      }
    });
  }, 50);
}

// Drill-down: click a Top Cost-Driver SKU → its BoM ingredient breakdown.
async function gpbSkuDrill(code) {
  const modal = document.getElementById('diveModal');
  const title = document.getElementById('diveTitle');
  const body  = document.getElementById('diveBody');
  if (!modal || !title || !body) return;
  title.textContent = `ส่วนผสม (BoM) · ${esc(code)}`;
  body.innerHTML = '<p class="text-sm text-slate-400" style="padding:24px">กำลังโหลด…</p>';
  modal.classList.remove('hidden');
  let d;
  try {
    const r = await fetch(`${API}/api/bom/composition?sku=${encodeURIComponent(code)}`);
    if (r.status === 404) {
      body.innerHTML = `<div style="padding:24px"><p class="text-sm text-slate-500">SKU <b>${esc(code)}</b> ไม่มีสูตร BoM (ใช้ต้นทุนแบบ flat จาก <code>sku_cost</code>) — จึงไม่มีรายละเอียดส่วนผสม</p>
        <button onclick="closeDive()" class="dive-action-btn" style="margin-top:12px">✕ ปิด (Esc)</button></div>`;
      return;
    }
    d = await r.json();
  } catch (e) {
    body.innerHTML = `<p class="text-sm text-red-500" style="padding:24px">โหลดไม่สำเร็จ: ${e.message}</p>`;
    return;
  }
  const ings = d.ingredients || [];
  const rowsHtml = ings.map(g => `<tr class="border-t border-slate-100">
    <td class="py-1.5 pr-3">${esc(g.name)}</td>
    <td class="py-1.5 pr-3 text-slate-400 text-[11px]">${esc(g.type)}</td>
    <td class="py-1.5 pr-3 text-right tabular-nums">${fmtN(g.qty)} ${esc(g.unit)}</td>
    <td class="py-1.5 pr-3 text-right tabular-nums font-medium text-orange-700">${fmt(g.cost)}</td>
    <td class="py-1.5 text-right tabular-nums text-slate-500">${g.pct}%</td></tr>`).join('');
  const byType = Object.entries(d.by_type || {}).sort((a, b) => b[1] - a[1]);
  const typeBars = byType.map(([t, p]) => _diveMetric(t, p + '%')).join('') || '<p class="text-xs text-slate-400">—</p>';
  body.innerHTML = `<div class="dive-layout">
    <div class="rounded-xl p-3" style="background:var(--bg-subtle);overflow:auto">
      <p class="text-sm font-semibold mb-1">${esc(d.description || code)}</p>
      <p class="text-xs text-slate-400 mb-2">ต้นทุนรวม/จาน <b class="text-orange-700">${fmt(d.total_cost)}</b> · ${ings.length} วัตถุดิบ</p>
      <table class="text-xs w-full"><thead><tr class="text-left text-slate-500 border-b border-slate-200">
        <th class="py-1.5 pr-3">วัตถุดิบ</th><th class="py-1.5 pr-3">ประเภท</th>
        <th class="py-1.5 pr-3 text-right">ปริมาณ</th><th class="py-1.5 pr-3 text-right">ต้นทุน</th><th class="py-1.5 text-right">%</th>
      </tr></thead><tbody>${rowsHtml}</tbody></table>
    </div>
    <div class="dive-insight-bar dive-side">
      ${_diveSection('สัดส่วนต้นทุนตามประเภท', '', typeBars)}
      <div class="dive-section"><div class="dive-section-title">Actions</div>
        <button onclick="closeDive()" class="dive-action-btn">✕ ปิด (Esc)</button></div>
    </div></div>`;
}

// Dive-in zoom: full bar chart of the Top Cost-Driver SKUs + analysis. Clicking a
// bar drills into that SKU's BoM composition.
function gpbCostDriverDive() {
  const skus = window._gpbCostSkus || [];
  const modal = document.getElementById('diveModal');
  const title = document.getElementById('diveTitle');
  const body  = document.getElementById('diveBody');
  if (!modal || !title || !body) return;
  if (!skus.length) return;
  title.textContent = 'Top Cost-Driver SKUs · กินต้นทุนสูงสุด';
  const top = skus.slice(0, 30);
  const nums = top.map(s => Math.round(s.bom_cost || 0));
  const totalCost = skus.reduce((a, s) => a + (s.bom_cost || 0), 0);
  const top5 = skus.slice(0, 5).reduce((a, s) => a + (s.bom_cost || 0), 0);
  const conc = totalCost ? (top5 / totalCost * 100) : 0;
  const analysis =
    _diveSection('Overview', '',
      _diveMetric('ต้นทุน BoM รวม', fmt(totalCost)) +
      _diveMetric('อันดับ 1', `${esc(top[0].sku_code)} · ${fmt(top[0].bom_cost)}`, 'bad') +
      _diveMetric('Top 5 รวม', `${fmt(top5)} (${conc.toFixed(0)}%)`) +
      _diveMetric('จำนวน SKU', String(skus.length))) +
    _diveSection('คำแนะนำ', '', _diveBullet('',
      `Top 5 SKU กินต้นทุน <b>${conc.toFixed(0)}%</b> ของทั้งหมด — โฟกัสต่อรองราคา/สูตรที่ SKU เหล่านี้ให้ผลมากสุด`, 'info'));
  body.innerHTML = `<div class="dive-layout">
    <div class="dive-chart-wrap rounded-xl p-3" style="background:var(--bg-subtle);position:relative"><canvas id="zoomChart"></canvas></div>
    <div class="dive-insight-bar dive-side">${analysis}
      <div class="dive-section"><div class="dive-section-title">Actions <span class="text-[10px] font-normal text-slate-400">· คลิกแท่งเพื่อดูส่วนผสม</span></div>
        <button onclick="closeDive()" class="dive-action-btn">✕ ปิด (Esc)</button></div>
    </div></div>`;
  modal.classList.remove('hidden');
  setTimeout(() => {
    if (chartInstances['zoomChart']) chartInstances['zoomChart'].destroy();
    const cv = document.getElementById('zoomChart');
    if (!cv) return;
    const mx = Math.max(...nums, 1);
    chartInstances['zoomChart'] = new Chart(cv, {
      type: 'bar',
      data: { labels: top.map(s => s.sku_code),
        datasets: [{ label: 'ต้นทุน BoM', data: nums,
          backgroundColor: nums.map(v => v === mx ? '#ef4444' : '#f97316'), borderRadius: 4 }] },
      options: {
        indexAxis: 'y', responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, datalabels: { display: false },
          tooltip: { callbacks: { label: ctx => ' ' + fmt(ctx.raw) } } },
        scales: { x: { ticks: { callback: v => fmt(v) } }, y: { ticks: { font: { size: 10 }, autoSkip: false } } },
        onClick: (e, els) => { if (els && els.length) gpbSkuDrill(top[els[0].index].sku_code); }
      }
    });
  }, 60);
}

// ── World-class analysis builder (JC-inspired) ────────────────
function _diveSection(title, icon, content) {
  return `<div class="dive-section">
    <div class="dive-section-title">${icon} ${title}</div>
    ${content}
  </div>`;
}
function _diveMetric(label, value, tone) {
  const cls = tone === 'good' ? 'good' : tone === 'bad' ? 'bad' : '';
  return `<div class="dive-metric"><span class="lbl">${label}</span><span class="val ${cls}">${value}</span></div>`;
}
function _diveBullet(icon, html, tone = 'info') {
  return `<div class="dive-bullet ${tone}"><span class="ico">${icon}</span><span class="body">${html}</span></div>`;
}

// Smart analyzer router — picks the right strategy per chart
function analyzeChartForDive(chartId, cfg) {
  const ds = cfg.data?.datasets?.[0];
  if (!ds || !ds.data || !ds.data.length) {
    return _diveSection('Insights', '', '<p class="text-xs text-slate-400">No data to analyze.</p>');
  }
  // Bubble/scatter charts carry no labels and {x,y,r} points — the label-based
  // analyzers (mix/trend) would crash. Route them to the scatter analyzer.
  const noLabels = !(cfg.data?.labels && cfg.data.labels.length);
  if (cfg.type === 'bubble' || cfg.type === 'scatter' || noLabels) return analyzeScatter(cfg, chartId);
  // Route based on chart id keywords
  const id = chartId.toLowerCase();
  if (id.includes('trend') || id === 'cmpbarchart' || id.includes('discount')) return analyzeTrend(cfg, chartId);
  if (id.includes('hour'))                                 return analyzeHourly(cfg);
  if (id.includes('dow'))                                  return analyzeDow(cfg);
  if (id.includes('branchcompare') || id.includes('branchchart')) return analyzeBranches(cfg);
  if (id.includes('cat') || id.includes('breakdown') || cfg.type === 'doughnut' || cfg.type === 'pie')
    return analyzeMix(cfg);
  return analyzeGeneric(cfg);
}

function _basics(cfg) {
  const ds = cfg.data.datasets[0];
  // Guard `null` (typeof null === 'object' → would throw on v.y). Charts with a
  // projection/forecast dataset carry null gaps, so this must tolerate them.
  const nums = ds.data.map(v => (v && typeof v === 'object') ? (v.y ?? v.r ?? 0) : (+v || 0));
  const labels = cfg.data.labels || [];
  const sum = nums.reduce((a,b) => a+b, 0);
  const avg = sum / nums.length;
  const max = Math.max(...nums);
  const min = Math.min(...nums);
  const peakIdx = nums.indexOf(max);
  const lowIdx  = nums.indexOf(min);
  const isCurrency = (ds.label || '').match(/revenue|sales|amount|cost|รายได้|ยอด/i) || cfg.type === 'line';
  const f = v => isCurrency ? fmt(v) : fmtN(v);
  return { ds, nums, labels, sum, avg, max, min, peakIdx, lowIdx, isCurrency, f };
}

// Detect outliers using IQR rule (Tukey's fences). Returns:
//   {hasOutliers, indices, severity, trimmedAvg, trimmedMax, gainPct}
// gainPct = how much the analysis would change if outliers excluded
function _detectOutliers(nums) {
  if (!nums || nums.length < 5) return {hasOutliers: false, indices: [], severity: 0, trimmedAvg: 0, trimmedMax: 0, gainPct: 0};
  const sorted = [...nums].sort((a,b)=>a-b);
  const q = p => {
    const idx = (sorted.length - 1) * p;
    const lo = Math.floor(idx), hi = Math.ceil(idx);
    return lo === hi ? sorted[lo] : sorted[lo] + (idx - lo) * (sorted[hi] - sorted[lo]);
  };
  const q1 = q(0.25), q3 = q(0.75);
  const iqr = q3 - q1;
  const lower = q1 - 1.5 * iqr;
  const upper = q3 + 1.5 * iqr;
  const indices = [];
  nums.forEach((v, i) => { if (v < lower || v > upper) indices.push(i); });
  const trimmed = nums.filter((_, i) => !indices.includes(i));
  const fullAvg = nums.reduce((a,b)=>a+b, 0) / nums.length;
  const trimmedAvg = trimmed.length ? trimmed.reduce((a,b)=>a+b, 0) / trimmed.length : fullAvg;
  const gainPct = fullAvg !== 0 ? Math.abs((trimmedAvg - fullAvg) / fullAvg * 100) : 0;
  return {
    hasOutliers: indices.length > 0 && gainPct >= 5,  // only flag if shifts avg by ≥5%
    indices,
    severity: indices.length / nums.length,
    trimmedAvg,
    trimmedMax: trimmed.length ? Math.max(...trimmed) : 0,
    gainPct,
    fullAvg,
  };
}

// Build an outlier-warning bullet for use inside any analyzeXxx
function _outlierBullet(cfg) {
  const { nums, labels, f } = _basics(cfg);
  const o = _detectOutliers(nums);
  if (!o.hasOutliers) return '';
  const outlierLabels = o.indices.map(i => labels[i]).filter(Boolean).slice(0, 3).join(', ');
  return _diveBullet(
    '',
    `พบ <b>${o.indices.length}</b> ค่าผิดปกติ (${outlierLabels}${o.indices.length>3?', …':''}) — ทำให้ค่าเฉลี่ยเบี่ยงเบนไป <b>${o.gainPct.toFixed(1)}%</b><br>` +
    `ถ้าตัด outlier ออก ค่าเฉลี่ยจะเป็น <b>${f(o.trimmedAvg)}</b> (จาก <b>${f(o.fullAvg)}</b>) — แนะนำให้ <b>วิเคราะห์แบบตัด outlier</b> เพื่อภาพที่สะท้อนความเป็นจริงมากขึ้น`,
    'warn'
  );
}

function analyzeTrend(cfg, chartId) {
  const { nums, labels, sum, avg, max, min, peakIdx, lowIdx, f } = _basics(cfg);
  const n = nums.length;
  // Recent vs prior window (e.g., last 3 vs prior 3)
  const w = Math.max(1, Math.min(3, Math.floor(n / 2)));
  const last = nums.slice(-w).reduce((a,b)=>a+b,0) / w;
  const prior = nums.slice(-2*w, -w).reduce((a,b)=>a+b,0) / Math.max(1, w);
  const trendPct = prior > 0 ? ((last - prior) / prior * 100) : 0;
  const trendTone = trendPct > 3 ? 'good' : trendPct < -3 ? 'bad' : 'info';
  const trendIco  = trendPct > 3 ? '▲' : trendPct < -3 ? '⚠' : '→';

  // % of periods below average (consistency)
  const belowAvg = nums.filter(v => v < avg).length;
  const sd = Math.sqrt(nums.reduce((s,v) => s + (v-avg)**2, 0) / n);
  const cv = avg ? (sd / avg * 100) : 0;
  const volTone = cv > 30 ? 'warn' : cv > 15 ? 'info' : 'good';

  return _diveSection('Overview', '', [
    _diveMetric('Total',          f(sum)),
    _diveMetric('Periods',        n + ' points'),
    _diveMetric('Average',        f(avg)),
    _diveMetric('Peak',           `${f(max)} (${labels[peakIdx] || '?'})`, 'good'),
    _diveMetric('Low',            `${f(min)} (${labels[lowIdx]  || '?'})`, 'bad'),
    _diveMetric('Volatility (CV)', cv.toFixed(1) + '%', volTone === 'good' ? 'good' : volTone === 'warn' ? 'bad' : ''),
  ].join('')) +
  _diveSection('Recent Trend', trendIco,
    _diveBullet(trendIco,
      `Last ${w} periods avg <b>${f(last)}</b> vs prior <b>${f(prior)}</b> — <b>${trendPct >= 0 ? '+' : ''}${trendPct.toFixed(1)}%</b>`,
      trendTone)
  ) +
  _diveSection('Insights & Actions', '', [
    _diveBullet('▼', `<b>${belowAvg}</b> of ${n} periods fell below the average — review promo calendar for those windows.`, belowAvg > n/2 ? 'warn' : 'info'),
    cv > 30
      ? _diveBullet('', `High volatility (CV <b>${cv.toFixed(0)}%</b>) — investigate one-off spikes/dips before forecasting.`, 'warn')
      : _diveBullet('✓', `Stable trajectory (CV <b>${cv.toFixed(0)}%</b>) — safe to use this baseline for targets.`, 'good'),
    trendPct < -5
      ? _diveBullet('', `Recent decline of <b>${Math.abs(trendPct).toFixed(1)}%</b> — push a campaign or revisit pricing.`, 'bad')
      : trendPct > 5
        ? _diveBullet('▲', `Momentum is +<b>${trendPct.toFixed(1)}%</b> — capitalize: increase staffing or stock for the upswing.`, 'good')
        : _diveBullet('', `Trend is flat — drill into branches or categories to find hidden movement.`, 'info'),
    _outlierBullet(cfg),
  ].filter(Boolean).join(''));
}

function analyzeHourly(cfg) {
  const { nums, labels, sum, avg, max, min, peakIdx, f } = _basics(cfg);
  // Daypart classification
  const morning = labels.map((l,i)=>i).filter(i=>parseInt(labels[i])>=6 && parseInt(labels[i])<=10).reduce((s,i)=>s+nums[i],0);
  const lunch   = labels.map((l,i)=>i).filter(i=>parseInt(labels[i])>=11 && parseInt(labels[i])<=14).reduce((s,i)=>s+nums[i],0);
  const dinner  = labels.map((l,i)=>i).filter(i=>parseInt(labels[i])>=17 && parseInt(labels[i])<=21).reduce((s,i)=>s+nums[i],0);
  const late    = labels.map((l,i)=>i).filter(i=>{const h=parseInt(labels[i]); return h>=22 || h<=2;}).reduce((s,i)=>s+nums[i],0);
  const segments = [
    {n:'Morning', v:morning}, {n:'Lunch', v:lunch},
    {n:'Dinner', v:dinner}, {n:'Late Night', v:late},
  ].sort((a,b)=>b.v-a.v);
  const peakSeg = segments[0];
  const deadHours = nums.map((v,i)=>({h:labels[i], v})).filter(x=>x.v < avg*0.3).length;

  return _diveSection('Hourly Overview', '', [
    _diveMetric('Total revenue', f(sum)),
    _diveMetric('Hourly average', f(avg)),
    _diveMetric('Peak hour',      `${labels[peakIdx]} (${f(max)})`, 'good'),
    _diveMetric('Dead hours (<30% avg)', deadHours, deadHours > 6 ? 'bad' : ''),
  ].join('')) +
  _diveSection('Dayparts Ranked', '', segments.map((s,i) =>
    `<div class="dive-metric"><span class="lbl">#${i+1} ${s.n}</span><span class="val ${i===0?'good':''}">${f(s.v)} · ${sum?((s.v/sum*100).toFixed(0)):0}%</span></div>`
  ).join('')) +
  _diveSection('Insights & Actions', '', [
    _diveBullet('', `<b>${peakSeg.n}</b> is your strongest daypart — protect it with full staffing & no maintenance.`, 'good'),
    deadHours > 6
      ? _diveBullet('', `<b>${deadHours}</b> hours run at <30% of average — consider reduced hours or happy-hour promos to boost.`, 'warn')
      : _diveBullet('✓', `Day spreads load well — keep current opening hours.`, 'good'),
    _diveBullet('', `Try to lift the weakest daypart (<b>${segments[3].n}</b>) — even a 10% gain there = <b>${f(segments[3].v*0.1)}</b>/period.`, 'info'),
  ].join(''));
}

function analyzeDow(cfg) {
  const { nums, labels, sum, avg, max, peakIdx, f } = _basics(cfg);
  const weekend = labels.map((l,i)=>({l,i})).filter(x=>/sat|sun|fri/i.test(x.l)).reduce((s,x)=>s+nums[x.i],0);
  const weekday = sum - weekend;
  const ratio = weekday ? (weekend / weekday) : 0;
  return _diveSection('Day-of-Week', '', [
    _diveMetric('Total', f(sum)),
    _diveMetric('Daily average', f(avg)),
    _diveMetric('Best day', `${labels[peakIdx]} (${f(max)})`, 'good'),
    _diveMetric('Weekend / Weekday', ratio.toFixed(2) + '×', ratio > 1.2 ? 'good' : ratio < 0.7 ? 'bad' : ''),
  ].join('')) +
  _diveSection('Insights', '', [
    ratio > 1.5
      ? _diveBullet('', `Weekends pull <b>${ratio.toFixed(1)}×</b> weekday volume — staff up Fri-Sun & limit weekend events.`, 'good')
      : ratio < 0.8
        ? _diveBullet('', `Weekday-heavy (${ratio.toFixed(2)}×) — explore weekend brunch / dinner sets to grow Sat-Sun.`, 'warn')
        : _diveBullet('', `Week is well-balanced — no urgent rebalancing needed.`, 'info'),
    _diveBullet('▼', `<b>${labels[nums.indexOf(Math.min(...nums))]}</b> is the slowest day — perfect candidate for a recurring weekly promo.`, 'info'),
  ].join(''));
}

function analyzeBranches(cfg) {
  const { nums, labels, sum, avg, max, f } = _basics(cfg);
  const ranked = labels.map((l,i)=>({b:l, v:nums[i]})).sort((a,b)=>b.v-a.v);
  const top3 = ranked.slice(0, 3);
  const bot3 = ranked.slice(-3).reverse();
  const cumul = [...ranked].reduce((acc, x) => { acc.push((acc[acc.length-1] || 0) + x.v); return acc; }, []);
  const cntFor80 = cumul.findIndex(c => c >= sum * 0.8) + 1;
  const gap = bot3[0].v ? (top3[0].v / bot3[0].v).toFixed(1) : '∞';

  return _diveSection('Branch Performance', '', [
    _diveMetric('Total revenue', f(sum)),
    _diveMetric('Branches',      labels.length),
    _diveMetric('Average',       f(avg)),
    _diveMetric('Top branch',    `${top3[0].b} (${f(top3[0].v)})`, 'good'),
    _diveMetric('Pareto 80%',    `${cntFor80} of ${labels.length} branches`),
    _diveMetric('Top vs Bottom', gap + '×', Number(gap) > 5 ? 'bad' : ''),
  ].join('')) +
  _diveSection('Top 3', '',
    top3.map((x,i) => `<div class="dive-metric"><span class="lbl">${['1.','2.','3.'][i]} ${x.b}</span><span class="val good">${f(x.v)}</span></div>`).join('')) +
  _diveSection('⚠ Bottom 3', '⚠',
    bot3.map(x => `<div class="dive-metric"><span class="lbl">${x.b}</span><span class="val bad">${f(x.v)}</span></div>`).join('')) +
  _diveSection('Insights & Actions', '', [
    cntFor80 <= labels.length * 0.4
      ? _diveBullet('', `Heavy concentration — <b>${cntFor80}/${labels.length}</b> branches drive 80% of revenue. Bet on them, but de-risk by lifting the tail.`, 'warn')
      : _diveBullet('', `Revenue is well-distributed across <b>${cntFor80}/${labels.length}</b> branches — healthy diversification.`, 'good'),
    parseFloat(gap) > 5
      ? _diveBullet('', `Top branch is <b>${gap}×</b> the bottom — investigate the gap (location, menu mix, manager training).`, 'bad')
      : _diveBullet('✓', `Performance gap is reasonable (<b>${gap}×</b>) — consistent operations.`, 'good'),
    _diveBullet('▲', `Lifting <b>${bot3[0].b}</b> by 20% would add <b>${f(bot3[0].v * 0.2)}</b> to system revenue.`, 'info'),
    _outlierBullet(cfg),
  ].filter(Boolean).join(''));
}

function analyzeMix(cfg) {
  const { nums, labels, sum, max, peakIdx, f } = _basics(cfg);
  const ranked = labels.map((l,i)=>({l, v:nums[i], pct: sum?(nums[i]/sum*100):0})).sort((a,b)=>b.v-a.v);
  const top3 = ranked.slice(0, 3);
  const top3Pct = top3.reduce((s,x)=>s+x.pct, 0);
  const top3Val = top3.reduce((s,x)=>s+x.v, 0);
  const MEDALS = ['1.','2.','3.'];
  return _diveSection('สรุปภาพรวม', '', [
    _diveMetric('ยอดรวม',      f(sum)),
    _diveMetric('จำนวนกลุ่ม',  labels.length),
    _diveMetric('กลุ่มสูงสุด', `${ranked[0].l} (${ranked[0].pct.toFixed(1)}%)`, 'good'),
    _diveMetric('% Top 3',     top3Pct.toFixed(1) + '%', top3Pct > 75 ? 'bad' : ''),
  ].join('')) +
  _diveSection('Top 3 รายละเอียด', '',
    top3.map((x, i) => {
      const rest = (i + 1 < top3.length) ? top3[i+1].v : 0;
      const gap  = rest ? (x.v / rest) : null;
      const gapStr = (gap && gap !== Infinity) ? ` · ${gap.toFixed(2)}× กว่าอันดับถัดไป` : '';
      return `<div class="dive-metric">
        <span class="lbl">${MEDALS[i]} <b>${x.l}</b></span>
        <span class="val good">${f(x.v)} · ${x.pct.toFixed(1)}%${gapStr}</span>
      </div>`;
    }).join('') +
    `<div class="dive-metric" style="border-top:1px dashed #cbd5e1;margin-top:6px;padding-top:6px">
      <span class="lbl">รวม Top 3</span>
      <span class="val">${f(top3Val)} · ${top3Pct.toFixed(1)}% ของทั้งหมด</span>
    </div>`
  ) +
  _diveSection('รายละเอียดทุกกลุ่ม', '',
    ranked.slice(0, 10).map((x, i) => `<div class="dive-metric"><span class="lbl">${i+1}. ${x.l}</span><span class="val">${f(x.v)} · ${x.pct.toFixed(1)}%</span></div>`).join('')) +
  _diveSection('การวิเคราะห์', '', [
    top3Pct > 75
      ? _diveBullet('⚠', `Top 3 รวม <b>${top3Pct.toFixed(0)}%</b> ของยอดทั้งหมด — สัดส่วนกระจุก ควรกระจายความเสี่ยงหรือยอมรับการพึ่งพา`, 'warn')
      : _diveBullet('✓', `สัดส่วนสมดุล (Top 3 = <b>${top3Pct.toFixed(0)}%</b>) — ไม่พึ่งพากลุ่มใดกลุ่มเดียวมากเกินไป`, 'good'),
    top3[0].pct > 50
      ? _diveBullet('', `<b>${top3[0].l}</b> ครองตลาดเดี่ยวที่ <b>${top3[0].pct.toFixed(0)}%</b> — เป็น core business ที่ต้องดูแลเป็นพิเศษ`, 'info')
      : '',
    ranked[ranked.length-1].pct < 2
      ? _diveBullet('▼', `กลุ่มท้าย <b>${ranked[ranked.length-1].l}</b> มีเพียง <b>${ranked[ranked.length-1].pct.toFixed(1)}%</b> — พิจารณาเลิกขายหรือควบรวม`, 'info')
      : _diveBullet('', `ทุกกลุ่มมีส่วนแบ่งสมเหตุสมผล — ไม่มี long-tail ที่ต้องตัดทิ้ง`, 'good'),
    _outlierBullet(cfg),
  ].filter(Boolean).join(''));
}

function analyzeScatter(cfg, chartId) {
  // Works for bubble/scatter where each point may carry an `item` payload.
  const ds = cfg.data?.datasets?.[0];
  const pts = (ds?.data || []).map(p => p && typeof p === 'object' ? p : {x:p,y:p});
  if (!pts.length) return _diveSection('Insights','','<p class="text-xs text-slate-400">ไม่มีข้อมูล</p>');
  // Menu cost-efficiency scatter: item has servings/cost. Find top reduce targets.
  const withItem = pts.filter(p => p.item);
  if (withItem.length) {
    const byCost = [...withItem].sort((a,b)=>(b.item.cost_pct||0)-(a.item.cost_pct||0));
    const hot = withItem.filter(p => (p.item.serving_pct||0) >= 2 && (p.item.cost_pct||0) >= 2)
                        .sort((a,b)=>(b.item.cost_pct||0)-(a.item.cost_pct||0));
    const top5cost = byCost.slice(0,5).reduce((s,p)=>s+(p.item.cost_pct||0),0);
    return _diveSection('สรุป', '', [
      _diveMetric('จำนวนเมนู', withItem.length),
      _diveMetric('Top 5 = % ต้นทุน', top5cost.toFixed(0)+'%', top5cost>40?'bad':''),
      _diveMetric('เมนูเป้าลดต้นทุน (เสิร์ฟเยอะ+แพง)', hot.length, hot.length?'bad':'good'),
    ].join('')) +
    _diveSection('เป้าหมายลดต้นทุนอันดับต้น', '',
      (hot.length?hot:byCost).slice(0,8).map((p,i)=>{
        const it=p.item;
        return `<div class="dive-metric"><span class="lbl">${i+1}. ${esc((it.description||'').replace(/^\((?:A|B)\)\s*/,''))}</span>`+
               `<span class="val bad">ต้นทุน ${(it.cost_pct||0).toFixed(1)}% · เสิร์ฟ ${(it.serving_pct||0).toFixed(1)}% · ${fmtN(it.cost_per_serving||0)}/จาน</span></div>`;
      }).join('')) +
    _diveSection('การวิเคราะห์', '', [
      hot.length
        ? _diveBullet('', `<b>${hot.length}</b> เมนูที่ <b>เสิร์ฟเยอะ + ต้นทุนสูง</b> — ปรับสูตร/ลด portion เมนูเหล่านี้ก่อน จะลดต้นทุนรวมได้มากสุด นำโดย <b>${esc((hot[0].item.description||'').replace(/^\((?:A|B)\)\s*/,''))}</b>`, 'warn')
        : _diveBullet('✓', 'ไม่มีเมนูที่ทั้งเสิร์ฟเยอะและแพงผิดปกติ — ต้นทุนกระจายดี', 'good'),
      _diveBullet('', 'จุดมุมขวาบน = เป้าหมายหลัก · มุมขวาล่าง = เสิร์ฟเยอะแต่ถูก (โปรโมทได้)', 'info'),
    ].join(''));
  }
  // Generic scatter fallback (x/y stats)
  const xs=pts.map(p=>+p.x||0), ys=pts.map(p=>+p.y||0);
  const favg=a=>a.reduce((s,v)=>s+v,0)/a.length;
  return _diveSection('Overview','',[
    _diveMetric('จุดข้อมูล', pts.length),
    _diveMetric('เฉลี่ย X', fmtN(favg(xs))),
    _diveMetric('เฉลี่ย Y', fmtN(favg(ys))),
  ].join(''));
}

function analyzeGeneric(cfg) {
  const { nums, labels, sum, avg, max, min, peakIdx, lowIdx, f } = _basics(cfg);
  const outlierHtml = _outlierBullet(cfg);
  return _diveSection('Overview', '', [
    _diveMetric('Total', f(sum)),
    _diveMetric('Average', f(avg)),
    _diveMetric('Peak', `${f(max)}${labels[peakIdx] ? ' · ' + labels[peakIdx] : ''}`, 'good'),
    _diveMetric('Low',  `${f(min)}${labels[lowIdx] ? ' · ' + labels[lowIdx] : ''}`, 'bad'),
    _diveMetric('Points', nums.length),
  ].join('')) +
  (outlierHtml ? _diveSection('การวิเคราะห์ Outlier', '', outlierHtml) : '');
}

function exportChartData(chartId) {
  const c = chartInstances[chartId];
  if (!c) return;
  const labels = c.data.labels || [];
  const rows = [['label', ...c.data.datasets.map(d => d.label || 'value')].join(',')];
  for (let i = 0; i < labels.length; i++) {
    rows.push([`"${labels[i]}"`, ...c.data.datasets.map(d => d.data[i] ?? '')].join(','));
  }
  downloadCSV(rows.join('\n'), `${chartId}.csv`);
}


// ════════════════════════════════════════════════════════════════
// FEATURE 6: HEALTH SCORE
// ════════════════════════════════════════════════════════════════
async function loadHealthScores() {
  const tbody = document.getElementById('healthBody');
  if (!tbody) return;
  const params = new URLSearchParams();
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to',   getMonthTo());
  if (getGroup())     params.set('group',      getGroup());
  const [health, pcts] = await Promise.all([
    api(`${API}/api/analysis/health?${params}`),
    api(`${API}/api/analysis/percentiles?${params}`).catch(()=>[]),
  ]);
  const pctMap = Object.fromEntries(pcts.map(p => [p.branch, p]));
  const colorFor = s => s >= 75 ? '#10b981' : s >= 55 ? '#4a70b0' : s >= 40 ? '#f59e0b' : '#ef4444';
  const pctChip = (v) => {
    if (v == null) return '<span class="text-slate-300">—</span>';
    const c = v >= 75 ? '#10b981' : v >= 50 ? '#4a70b0' : v >= 25 ? '#f59e0b' : '#ef4444';
    return `<span style="display:inline-block;min-width:36px;padding:2px 6px;border-radius:999px;background:${c}22;color:${textInk(c)};font-weight:700">${v}<span style="opacity:0.7">th</span></span>`;
  };
  tbody.innerHTML = health.map((b, i) => {
    const p = pctMap[b.branch] || {};
    return `
    <tr class="border-t border-slate-100">
      <td class="py-2 pr-3"><span class="text-slate-400 mr-1">#${i+1}</span><span class="font-semibold">${esc(b.branch)}</span></td>
      <td class="py-2 pr-3 text-center">
        <div class="health-gauge mx-auto" style="background:${colorFor(b.score)}">${b.score}</div>
      </td>
      <td class="py-2 pr-3 text-right">${fmt(b.total_revenue)}</td>
      <td class="py-2 pr-3 text-right">${fmt(b.last_month_revenue)}</td>
      <td class="py-2 pr-3 text-right">${badge(b.growth_pct)}</td>
      <td class="py-2 pr-3 text-center">${pctChip(p.p_revenue)}</td>
      <td class="py-2 pr-3 text-center">${pctChip(p.p_avg_ticket)}</td>
      <td class="py-2 pr-3 text-right text-xs">${b.revenue_score}</td>
      <td class="py-2 pr-3 text-right text-xs">${b.growth_score}</td>
      <td class="py-2 pr-3 text-right text-xs">${b.consistency_score}</td>
      <td class="py-2 text-right text-xs">${b.momentum_score}</td>
    </tr>`;
  }).join('');
}

// ════════════════════════════════════════════════════════════════
// FEATURE 7: PRESETS / SAVED FILTERS
// ════════════════════════════════════════════════════════════════
function loadPresetRange(range) {
  if (!allMonths.length) return;
  const today = new Date();
  const ym = d => d.toISOString().slice(0,7);
  const setMR = (from, to) => {
    setGranularity('monthly');
    document.getElementById('selMonthFrom').value = from || '';
    document.getElementById('selMonthTo').value   = to   || allMonths[allMonths.length-1];
    onFilterChange();
  };
  const last = allMonths[allMonths.length-1];
  const monthsAgo = n => allMonths[Math.max(0, allMonths.length-1-n)];
  switch (range) {
    case 'this_month': setMR(last, last); break;
    case 'last_month': { const m = monthsAgo(1); setMR(m, m); break; }
    case 'last_3m': setMR(monthsAgo(2), last); break;
    case 'last_6m': setMR(monthsAgo(5), last); break;
    case 'ytd':     { const y = last.slice(0,4); setMR(`${y}-01`, last); break; }
    case 'all':     setMR('', ''); break;
  }
}

// Custom DAY-range picker (full calendar) in the Quick-load bar.
// Picks exact start/end dates. Day-precise pages (transaction-grain) honour the
// exact dates; month-aggregate pages fall back to the enclosing month range.
function _lastDayOfMonth(ym) {            // "YYYY-MM" → "YYYY-MM-DD" (last day)
  const [y, m] = ym.split('-').map(Number);
  return `${ym}-${String(new Date(y, m, 0).getDate()).padStart(2, '0')}`;
}
function toggleCustomRange() {
  const box = document.getElementById('customRangeBox');
  const open = box.classList.contains('hidden');
  if (open) {
    const f = document.getElementById('customFrom'), t = document.getElementById('customTo');
    // Data window bounds for the calendar
    const dataMin = (allMonths[0] || '2025-01') + '-01';
    const today = new Date().toISOString().slice(0, 10);
    f.min = dataMin; f.max = today; t.min = dataMin; t.max = today;
    // Prefill from the active range: day pickers if in day mode, else the month range
    const dF = document.getElementById('selDateFrom')?.value;
    const dT = document.getElementById('selDateTo')?.value;
    if (dF || dT) { f.value = dF || ''; t.value = dT || ''; }
    else {
      const mf = getMonthFrom(), mt = getMonthTo();
      f.value = mf ? mf + '-01' : dataMin;
      t.value = mt ? _lastDayOfMonth(mt) : today;
    }
    box.classList.remove('hidden'); box.classList.add('inline-flex');
    document.getElementById('customRangeChip').classList.add('active');
  } else {
    box.classList.add('hidden'); box.classList.remove('inline-flex');
    document.getElementById('customRangeChip').classList.remove('active');
  }
}

function applyCustomRange() {
  const from = document.getElementById('customFrom').value;   // YYYY-MM-DD
  const to   = document.getElementById('customTo').value;
  const msg  = document.getElementById('customRangeMsg');
  if (!from || !to) { msg.textContent = 'เลือกวันเริ่มและวันสิ้นสุด'; return; }
  if (from > to)    { msg.textContent = 'วันเริ่มต้องไม่เกินวันสิ้นสุด'; return; }
  msg.textContent = '';
  // Day-precise custom range → daily granularity + global date pickers.
  setGranularity('daily');
  document.getElementById('selDateFrom').value = from;
  document.getElementById('selDateTo').value   = to;
  // Derive enclosing month range so month-aggregate pages still filter correctly.
  document.getElementById('selMonthFrom').value = from.slice(0, 7);
  document.getElementById('selMonthTo').value   = to.slice(0, 7);
  onFilterChange();
}

async function saveCurrentAsPreset() {
  const name = await uiPrompt('Name this preset:', 'My preset');
  if (!name) return;
  const preset = {
    name,
    branch: getBranch(),
    month_from: getMonthFrom(),
    month_to:   getMonthTo(),
    granularity: currentGranularity,
  };
  const presets = JSON.parse(localStorage.getItem('oshinei_presets') || '[]');
  presets.push(preset);
  localStorage.setItem('oshinei_presets', JSON.stringify(presets));
  renderUserPresets();
}

function applyUserPreset(idx) {
  const presets = JSON.parse(localStorage.getItem('oshinei_presets') || '[]');
  const p = presets[idx];
  if (!p) return;
  if (p.branch !== undefined) document.getElementById('selBranch').value = p.branch;
  setGranularity(p.granularity || 'monthly');
  if (p.month_from !== undefined) document.getElementById('selMonthFrom').value = p.month_from;
  if (p.month_to   !== undefined) document.getElementById('selMonthTo').value   = p.month_to;
  onFilterChange();
}

function deleteUserPreset(idx, ev) {
  ev.stopPropagation();
  const presets = JSON.parse(localStorage.getItem('oshinei_presets') || '[]');
  presets.splice(idx, 1);
  localStorage.setItem('oshinei_presets', JSON.stringify(presets));
  renderUserPresets();
}

function renderUserPresets() {
  const wrap = document.getElementById('userPresets');
  const presets = JSON.parse(localStorage.getItem('oshinei_presets') || '[]');
  if (!presets.length) { wrap.innerHTML = '<span class="text-xs text-slate-400 italic">none yet</span>'; return; }
  wrap.innerHTML = presets.map((p, i) => `
    <span class="preset-chip" onclick="applyUserPreset(${i})">
      ★ ${p.name}
      <span class="x" onclick="deleteUserPreset(${i}, event)" title="Delete">✕</span>
    </span>`).join('');
}

function togglePresetMenu() {
  const bar = document.getElementById('presetBar');
  bar.classList.toggle('hidden');
  document.getElementById('presetBtn')?.setAttribute('aria-expanded', bar.classList.contains('hidden') ? 'false' : 'true');
}

function toggleMoreFilters() {
  const menu = document.getElementById('moreFiltersMenu');
  if (!menu) return;
  menu.classList.toggle('hidden');
  document.getElementById('moreFiltersBtn')?.setAttribute('aria-expanded', menu.classList.contains('hidden') ? 'false' : 'true');
}
// close the More-filters popover when clicking outside it
document.addEventListener('click', (e) => {
  const menu = document.getElementById('moreFiltersMenu');
  const btn  = document.getElementById('moreFiltersBtn');
  if (menu && !menu.classList.contains('hidden') &&
      !menu.contains(e.target) && btn && !btn.contains(e.target)) {
    menu.classList.add('hidden');
    btn.setAttribute('aria-expanded', 'false');
  }
});

// ════════════════════════════════════════════════════════════════
// FEATURE 8: GLOBAL SEARCH
// ════════════════════════════════════════════════════════════════
let searchTimer = null;
function onGlobalSearchInput(e) {
  const q = e.target.value.trim();
  clearTimeout(searchTimer);
  if (!q) { hideSearchResults(); return; }
  searchTimer = setTimeout(async () => {
    try {
      const data = await api(`${API}/api/search?q=${encodeURIComponent(q)}`);
      renderSearchResults(data, q);
    } catch(err) { hideSearchResults(); }
  }, 200);
}

function renderSearchResults(data, q) {
  const el = document.getElementById('searchResults');
  if (!data.branches.length && !data.skus.length) {
    el.innerHTML = `<div class="px-3 py-3 text-xs text-slate-400">No results for "${q}"</div>`;
    el.classList.remove('hidden');
    return;
  }
  let html = '';
  if (data.branches.length) {
    html += '<div class="search-group-title">สาขา</div>';
    html += data.branches.map(b => `
      <div class="search-item" onclick="selectSearchBranch('${jsAttr(b.name)}')">
        <span class="badge">BRANCH</span>
        <span class="font-medium">${esc(b.name)}</span>
      </div>`).join('');
  }
  if (data.skus.length) {
    html += '<div class="search-group-title">SKUs</div>';
    html += data.skus.map(s => `
      <div class="search-item" onclick="selectSearchSku('${jsAttr(s.sku_code)}')">
        <span class="badge badge-sku">SKU</span>
        <span class="font-mono text-xs text-indigo-600">${esc(s.sku_code)}</span>
        <span class="text-slate-600 truncate">${esc(s.description || '')}</span>
        <span class="text-xs text-slate-400 ml-auto">${esc(s.category || '')}</span>
      </div>`).join('');
  }
  el.innerHTML = html;
  el.classList.remove('hidden');
}

function hideSearchResults() {
  document.getElementById('searchResults').classList.add('hidden');
}

function selectSearchBranch(name) {
  document.getElementById('selBranch').value = name;
  document.getElementById('globalSearch').value = '';
  hideSearchResults();
  onFilterChange();
}

function selectSearchSku(skuCode) {
  document.getElementById('globalSearch').value = '';
  hideSearchResults();
  switchTab('settings');
  switchSettingsTab('costs');
  setTimeout(() => {
    const inp = document.getElementById('costSearch');
    if (inp) { inp.value = skuCode; debounceLoadCosts(); }
  }, 200);
}

// ════════════════════════════════════════════════════════════════
// FEATURE 9: CLOSED-HOURS MANAGER
// ════════════════════════════════════════════════════════════════
let _hoursState = {};   // {branch_id: {dow: {is_closed, hour_from, hour_to, hourly_open: bool[24]}}}

async function loadClosedHours() {
  const sel = document.getElementById('hoursBranchSel');
  if (!sel.options.length) {
    const branches = await api(`${API}/api/branches`);
    sel.innerHTML = branches.map(b => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
  }
  const data = await api(`${API}/api/closed_hours`);
  _hoursState = {};
  data.forEach(r => {
    const map = _hoursState[r.branch_id] = _hoursState[r.branch_id] || {};
    if (r.dow != null) {
      map[r.dow] = { is_closed: !!r.is_closed, hour_from: r.hour_from, hour_to: r.hour_to };
    }
  });
  renderClosedHoursGrid();
}

function renderClosedHoursGrid() {
  const bid = parseInt(document.getElementById('hoursBranchSel').value, 10);
  const wrap = document.getElementById('hoursGrid');
  const dows = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  const branchData = _hoursState[bid] || {};

  // header row
  let html = `<div class="hours-grid mb-1">
    <div></div>
    ${Array.from({length:24}, (_,h) => `<div class="hours-hour">${h%3===0?h:''}</div>`).join('')}
  </div>`;
  // body rows
  html += dows.map((d, dow) => {
    const dd = branchData[dow] || { is_closed:false, hour_from:0, hour_to:23 };
    return `<div class="hours-grid mb-1">
      <div class="hours-label">${d}</div>
      ${Array.from({length:24}, (_,h) => {
        const closed = dd.is_closed || h < dd.hour_from || h > dd.hour_to;
        return `<div class="hours-cell ${closed?'closed':''}"
          onclick="toggleHourCell(${dow},${h})"
          title="${d} ${h}:00 — ${closed?'closed':'open'}"></div>`;
      }).join('')}
    </div>`;
  }).join('');
  wrap.innerHTML = html;
}

function toggleHourCell(dow, hour) {
  const bid = parseInt(document.getElementById('hoursBranchSel').value, 10);
  const branchData = _hoursState[bid] = _hoursState[bid] || {};
  const dd = branchData[dow] = branchData[dow] || { is_closed:false, hour_from:0, hour_to:23 };
  if (!dd.hourly_open) {
    dd.hourly_open = Array.from({length:24}, (_,h) => !dd.is_closed && h >= dd.hour_from && h <= dd.hour_to);
  }
  dd.hourly_open[hour] = !dd.hourly_open[hour];
  // derive hour_from/hour_to (smallest open .. largest open); closed if none open
  const openHrs = dd.hourly_open.map((v,h) => v ? h : null).filter(x => x !== null);
  if (!openHrs.length) { dd.is_closed = true; dd.hour_from = 0; dd.hour_to = 23; }
  else { dd.is_closed = false; dd.hour_from = openHrs[0]; dd.hour_to = openHrs[openHrs.length-1]; }
  renderClosedHoursGrid();
}

async function saveClosedHours() {
  const status = document.getElementById('hoursSaveStatus');
  const entries = [];
  Object.entries(_hoursState).forEach(([bid, byDow]) => {
    Object.entries(byDow).forEach(([dow, dd]) => {
      entries.push({
        branch_id: parseInt(bid,10), dow: parseInt(dow,10),
        hour_from: dd.hour_from || 0, hour_to: dd.hour_to || 23,
        is_closed: !!dd.is_closed,
      });
    });
  });
  status.textContent = 'Saving…'; status.className = 'text-sm text-slate-400';
  try {
    await fetch(`${API}/api/closed_hours`, {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(entries),
    });
    status.textContent = '✓ Saved'; status.className = 'text-sm text-emerald-600';
    setTimeout(() => status.textContent = '', 2500);
  } catch(e) {
    status.textContent = '✗ ' + e.message; status.className = 'text-sm text-red-600';
  }
}

// ════════════════════════════════════════════════════════════════
// FEATURE 10: DARK MODE
// ════════════════════════════════════════════════════════════════
// A saved choice (oshinei_dark) wins everywhere. A PHONE with no saved choice follows
// the device (prefers-color-scheme) and keeps following it until the person taps a
// toggle — the phone summary has its own in its header (#msDarkBtn, 2026-10-08). A
// desktop with no choice stays light, as it always has.
function _systemDark() { try { return window.matchMedia('(prefers-color-scheme: dark)').matches; } catch (e) { return false; } }
function _darkChoice() { try { return localStorage.getItem('oshinei_dark'); } catch (e) { return null; } }
function _onPhone() { try { return window.matchMedia('(max-width:700px)').matches; } catch (e) { return false; } }
function applyDarkMode(isDark, rerender) {
  document.body.classList.toggle('dark', !!isDark);
  document.getElementById('darkBtn')?.setAttribute('aria-pressed', isDark ? 'true' : 'false');
  setDarkBtnIcon(!!isDark);
  if (!rerender) return;
  // Force-rebuild charts so palette/axes recompute via chartTheme()
  Object.keys(chartInstances).forEach(id => { try { chartInstances[id].destroy(); } catch(e){} delete chartInstances[id]; });
  loadActiveTab();
}
function initDarkMode() {
  const choice = _darkChoice();
  applyDarkMode(choice === null ? (_onPhone() && _systemDark()) : choice === '1', false);
  try {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
      if (_darkChoice() === null && _onPhone()) applyDarkMode(e.matches, true);
    });
  } catch (e) {}
}

function toggleDarkMode() {
  const isDark = !document.body.classList.contains('dark');
  try { localStorage.setItem('oshinei_dark', isDark ? '1' : '0'); } catch (e) {}
  applyDarkMode(isDark, true);
}

function setDarkBtnIcon(isDark) {
  const btn = document.getElementById('darkBtn');
  if (btn) btn.innerHTML = isDark ? ICON.sun : ICON.moon;
  const mb = document.getElementById('msDarkBtn');
  if (mb) {
    mb.innerHTML = isDark ? ICON.sun : ICON.moon;
    mb.setAttribute('aria-pressed', isDark ? 'true' : 'false');
    mb.title = isDark ? 'สลับเป็นโหมดสว่าง' : 'สลับเป็นโหมดมืด';
  }
  if (typeof phDarkSync === 'function') phDarkSync();   // the phone shell's ฉัน row (js/09c-mobile.js)
}


// ════════════════════════════════════════════════════════════════
// COHORT
// ════════════════════════════════════════════════════════════════
async function loadCohort() {
  const params = new URLSearchParams();
  if (getBranch()) params.set('branch', getBranch());
  if (getGroup())  params.set('group',  getGroup());
  const d = await api(`${API}/api/analysis/cohort?${params}`);
  document.getElementById('cohortNote').textContent = d.note || '';
  const M = d.matrix || [];
  const TH_M = ['ม.ค.','ก.พ.','มี.ค.','เม.ย.','พ.ค.','มิ.ย.','ก.ค.','ส.ค.','ก.ย.','ต.ค.','พ.ย.','ธ.ค.'];
  const mLbl = ym => `${TH_M[+ym.slice(5,7)-1]} ${ym.slice(2,4)}`;
  const chip = (p, inv) => (p == null)
    ? '<span class="text-slate-300">—</span>'
    : `<span style="font-weight:700;color:${(inv ? p <= 0 : p >= 0) ? 'var(--good-ink)' : 'var(--bad-ink)'}">${p >= 0 ? '▲' : '▼'} ${Math.abs(p).toFixed(1)}%</span>`;

  // ── verdict line: latest FULL month vs prev + vs peak (partial months excluded) ──
  const full = M.filter(x => !x.partial);
  const last = full[full.length - 1] || M[M.length - 1];
  const peak = full.reduce((a, b) => (b.customers > ((a && a.customers) || 0) ? b : a), null);
  const vEl = document.getElementById('cohortVerdict');
  if (vEl && last) {
    const mom = last.mom_pct;
    const col = mom == null ? '#64748b' : mom >= 1 ? '#059669' : mom <= -5 ? '#dc2626' : '#d97706';
    const vsPeak = (peak && peak.cohort !== last.cohort && peak.customers)
      ? ` · ต่ำกว่าพีค ${mLbl(peak.cohort)} (${fmtN(peak.customers)} คน) ${chip(-((peak.customers - last.customers) / peak.customers * 100))}` : '';
    const partTag = last.partial ? ` <span class="text-[10px] px-1.5 py-0.5 rounded" style="background:#fef3c7;color:#a44a08">ไม่ครบเดือน (${last.days_covered}/${last.days_in_month} วัน)</span>` : '';
    vEl.innerHTML = `<p class="text-[13px] mb-3 pb-2 border-b border-slate-100 flex items-start gap-2 flex-wrap">
        <span style="color:${textInk(col)};line-height:1.5">●</span>
        <span class="text-slate-600">เดือนเต็มล่าสุด ${mLbl(last.cohort)}: <b>${fmtN(last.customers)}</b> คน
        ${mom != null ? `· MoM ${chip(mom)}` : ''}${last.yoy_pct != null ? ` · YoY ${chip(last.yoy_pct)}` : ''}
        · <b>${fmtN(last.cust_per_branch)}</b> คน/สาขา (${last.branches} สาขา) · ยอด/หัว <b>฿${fmtN(last.rev_per_cust)}</b>${vsPeak}${partTag}</span></p>`;
  }

  // ── combo chart: customer bars (partial = ซีด + ลาย, latest full = เข้ม) + MA3 ──
  const vals = M.map(x => x.customers || 0);
  const lastFullIdx = M.length ? M.map(x => !x.partial).lastIndexOf(true) : -1;
  mkChart('cohortChart', {
    type: 'bar',
    data: { labels: M.map(x => mLbl(x.cohort)), datasets: [
      { type: 'bar', label: 'ลูกค้า', data: vals,
        backgroundColor: M.map((x, i) => x.partial ? '#e2e8f0' : i === lastFullIdx ? '#1e40af' : '#93c5fd'),
        borderRadius: 5, categoryPercentage: 0.72, barPercentage: 0.85, order: 2,
        datalabels: { display: false } },
      { type: 'line', label: 'แนวโน้ม (MA3)', data: movingAvg(vals, 3),
        borderColor: '#f59e0b', backgroundColor: 'transparent', borderWidth: 2,
        tension: 0.35, fill: false, order: 1, pointRadius: 0, datalabels: { display: false } },
    ] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#0f172a', padding: 10, cornerRadius: 8, displayColors: false,
          filter: it => it.dataset.type !== 'line',
          callbacks: {
            label: c => {
              const x = M[c.dataIndex];
              const out = [`ลูกค้า ${fmtN(x.customers)} คน · ${x.branches} สาขา`,
                           `ลูกค้า/สาขา ${fmtN(x.cust_per_branch)} · ยอด/หัว ฿${fmtN(x.rev_per_cust)}`];
              if (x.partial) out.push(`⚠ ไม่ครบเดือน (${x.days_covered}/${x.days_in_month} วัน)`);
              return out;
            },
          },
        },
        datalabels: { display: false },
      },
      scales: {
        y: { beginAtZero: true, grace: '12%', border: { display: false },
             grid: { color: 'rgba(148,163,184,0.13)', drawTicks: false },
             ticks: { color: '#94a3b8', font: { size: 10 },
                      callback: v => v >= 1e3 ? (v / 1e3).toFixed(0) + 'k' : v } },
        x: { border: { display: false }, grid: { display: false },
             ticks: { color: ctx => ctx.index === lastFullIdx ? '#1e40af' : '#64748b',
                      font: ctx => ({ size: 10, weight: ctx.index === lastFullIdx ? '700' : '500' }),
                      maxRotation: 0, autoSkip: true } },
      },
    },
  });

  // ── table: bars on ลูกค้า, heat on ลูกค้า/สาขา, MoM/YoY chips, partial muted ──
  const maxCust = Math.max(1, ...vals);
  const cpbFull = full.map(x => x.cust_per_branch || 0);
  const cpbMax = Math.max(1, ...cpbFull), cpbMin = Math.min(...(cpbFull.length ? cpbFull : [0]));
  const heat = v => {
    if (v == null || !cpbFull.length || cpbMax === cpbMin) return '';
    const t = (v - cpbMin) / (cpbMax - cpbMin);          // 0 = worst, 1 = best
    return `background:rgba(${t >= 0.5 ? '5,150,105' : '220,38,38'},${(0.06 + 0.24 * Math.abs(t - 0.5) * 2).toFixed(2)})`;
  };
  document.getElementById('cohortBody').innerHTML = M.map((r, i) => `
    <tr class="border-t border-slate-100 ${i === lastFullIdx ? 'font-semibold' : ''}" ${r.partial ? 'style="opacity:.65"' : ''}>
      <td class="py-2 pr-3 font-semibold whitespace-nowrap">${fmtPeriod(r.cohort)}${r.partial ? ` <span class="text-[9px] px-1 py-0.5 rounded font-normal" style="background:#fef3c7;color:#a44a08" title="มีข้อมูล ${r.days_covered} จาก ${r.days_in_month} วัน — ไม่ถูกนำไปเทียบ MoM/YoY">ไม่ครบเดือน</span>` : ''}</td>
      <td class="py-2 pr-3 text-right tabular-nums">${fmtN(r.tx)}</td>
      <td class="py-2 pr-3 text-right tabular-nums whitespace-nowrap">${fmtN(r.customers)}
        <span style="display:inline-block;height:5px;border-radius:3px;background:var(--accent);vertical-align:middle;margin-left:5px;width:${Math.max(3, Math.round((r.customers || 0) / maxCust * 44))}px"></span></td>
      <td class="py-2 pr-3 text-right tabular-nums"><span class="rounded px-1" style="${r.partial ? '' : heat(r.cust_per_branch)}">${fmtN(r.cust_per_branch)}</span> <span class="text-slate-400 text-[10px]">(${r.branches})</span></td>
      <td class="py-2 pr-3 text-right tabular-nums">฿${fmtN(r.rev_per_cust)}</td>
      <td class="py-2 pr-3 text-right tabular-nums">${fmt(r.revenue)}</td>
      <td class="py-2 pr-3 text-right">${chip(r.mom_pct)}</td>
      <td class="py-2 text-right">${chip(r.yoy_pct)}</td>
    </tr>`).join('');

  // ── seasonality: month-of-year grouped bars, ลูกค้า/สาขา basis (growth-fair) ──
  const sEl = document.getElementById('cohortSeason');
  if (sEl) {
    const SS = d.seasonality || [];
    const years = [...new Set(SS.flatMap(s => Object.keys(s.years)))].sort();
    if (!SS.length || years.length < 1) { sEl.innerHTML = ''; }
    else {
      const YC = { 0: '#c9d5e8', 1: '#4a70b0', 2: '#0f766e' };   // oldest → newest
      const mx = Math.max(1, ...SS.flatMap(s => Object.values(s.years).map(v => v.cpb || 0)));
      const bars = SS.map(s => {
        const grp = years.map((y, yi) => {
          const v = s.years[y];
          if (!v) return '<div style="flex:1"></div>';
          const h = Math.max(3, Math.round((v.cpb || 0) / mx * 78));
          return `<div style="flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center" title="${TH_M[s.m-1]} ${y}: ${fmtN(v.cpb)} คน/สาขา (รวม ${fmtN(v.cust)} คน)${v.partial ? ' · ไม่ครบเดือน' : ''}">
              <div style="width:82%;height:${h}px;border-radius:3px 3px 0 0;background:${YC[yi] || '#94a3b8'};${v.partial ? 'opacity:.4' : ''}"></div></div>`;
        }).join('');
        return `<div class="flex-1 flex flex-col items-center">
            <div class="flex items-end w-full" style="height:86px;gap:1px">${grp}</div>
            <span class="text-[9px] text-slate-500 mt-1">${TH_M[s.m-1]}</span></div>`;
      }).join('');
      const legend = years.map((y, yi) => `<span class="flex items-center gap-1"><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${YC[yi] || '#94a3b8'}"></span>${y}</span>`).join(' ');
      sEl.innerHTML = `<div class="pt-3 border-t border-slate-100">
          <div class="flex items-center justify-between flex-wrap gap-2 mb-1">
            <p class="text-xs font-semibold text-slate-500">ฤดูกาล — ลูกค้า/สาขา ตามเดือนของปี <span class="font-normal text-slate-400">(เทียบปีต่อปีแบบตัดผลขยายสาขา · แท่งซีด = เดือนไม่ครบ)</span></p>
            <div class="flex items-center gap-3 text-[10px] text-slate-500">${legend}</div>
          </div>
          <div class="flex" style="gap:6px">${bars}</div></div>`;
    }
  }
}

// ════════════════════════════════════════════════════════════════
// AFFINITY
// ════════════════════════════════════════════════════════════════
let _affTimer = null;
function debounceAffinity() {
  clearTimeout(_affTimer);
  _affTimer = setTimeout(loadAffinity, 350);
}
async function loadAffinity() {
  const anchor = document.getElementById('affinityAnchor').value.trim();
  const body = document.getElementById('affinityBody');
  const status = document.getElementById('affinityStatus');
  if (!anchor) { body.innerHTML = '<p class="text-sm text-slate-400">Enter an anchor SKU to find what often sells alongside it.</p>'; status.textContent=''; return; }
  status.textContent = 'Searching…';
  const params = new URLSearchParams({ sku_code: anchor, limit: 20 });
  if (getBranch())    params.set('branch', getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to',   getMonthTo());
  const d = await api(`${API}/api/analysis/affinity?${params}`);
  status.textContent = `${d.baskets || 0} baskets found containing ${anchor}`;
  if (!d.items || !d.items.length) {
    body.innerHTML = '<p class="text-sm text-slate-400">No co-occurring items found. Try a different SKU.</p>';
    return;
  }
  body.innerHTML = `
    <table class="text-xs w-full" id="affinityTable">
      <thead><tr class="text-left text-slate-500 border-b border-slate-200">
        <th class="py-2 pr-3">SKU</th>
        <th class="py-2 pr-3">รายละเอียด</th>
        <th class="py-2 pr-3 text-right">บิล</th>
        <th class="py-2 pr-3 text-right">รายได้ร่วม</th>
        <th class="py-2 pr-3 text-right">จำนวนร่วม</th>
        <th class="py-2 text-right">ค่า Lift</th>
      </tr></thead>
      <tbody>${d.items.map(r => `
        <tr class="border-t border-slate-100">
          <td class="py-2 pr-3 font-mono text-indigo-600">${r.sku_code}</td>
          <td class="py-2 pr-3 max-w-xs truncate" title="${esc(r.description)}">${esc(r.description)}</td>
          <td class="py-2 pr-3 text-right">${r.co_count}</td>
          <td class="py-2 pr-3 text-right">${fmt(r.co_revenue)}</td>
          <td class="py-2 pr-3 text-right">${fmtN(r.co_qty)}</td>
          <td class="py-2 text-right"><span class="pill pill-up">${r.lift}%</span></td>
        </tr>`).join('')}
      </tbody>
    </table>`;
}

// ════════════════════════════════════════════════════════════════
// WHAT-IF SIMULATOR
// ════════════════════════════════════════════════════════════════
let _wiBase = null;
async function initWhatIf() {
  const d = await api(`${API}/api/summary${qp()}`);
  const marg = await api(`${API}/api/sales/margin${qp()}`).catch(()=>({total:{margin_pct:30}}));
  _wiBase = {
    revenue: d.revenue || 0,
    tx:      d.tx_count || 0,
    cust:    d.customer_count || 0,
    avg:     d.avg_ticket || 0,
    margin:  marg.total?.margin_pct || 30,
  };
  document.getElementById('whatifBase').innerHTML = `
    <div class="font-semibold text-slate-600 mb-1">Baseline (current filters)</div>
    Revenue ${fmt(_wiBase.revenue)} · Tx ${fmtN(_wiBase.tx)} · Cust ${fmtN(_wiBase.cust)} · Avg ${fmtN(_wiBase.avg)} · Margin ${_wiBase.margin}%
  `;
  document.getElementById('wiTraffic').value = 0;
  document.getElementById('wiTicket').value  = 0;
  document.getElementById('wiCogs').value    = 0;
  renderWhatIf();
}
function renderWhatIf() {
  if (!_wiBase) return;
  const traffic = +document.getElementById('wiTraffic').value;
  const ticket  = +document.getElementById('wiTicket').value;
  const cogs    = +document.getElementById('wiCogs').value;
  document.getElementById('wiTrafficLbl').textContent = (traffic>=0?'+':'')+traffic+'%';
  document.getElementById('wiTicketLbl').textContent  = (ticket>=0?'+':'')+ticket+'%';
  document.getElementById('wiCogsLbl').textContent    = (cogs>=0?'+':'')+cogs+'%';
  const newTx   = _wiBase.tx  * (1 + traffic/100);
  const newAvg  = _wiBase.avg * (1 + ticket/100);
  const newRev  = newTx * newAvg;
  const newMarg = Math.max(0, Math.min(100, _wiBase.margin - cogs));
  const newProfit = newRev * newMarg / 100;
  const baseProfit = _wiBase.revenue * _wiBase.margin / 100;
  const drev = newRev - _wiBase.revenue, dprof = newProfit - baseProfit;
  const fmtDelta = (d, isPct=false) => {
    const c = d > 0 ? '#10b981' : d < 0 ? '#ef4444' : '#64748b';
    const sign = d > 0 ? '+' : '';
    return `<span style="color:${textInk(c)};font-weight:700">${sign}${isPct?d.toFixed(1)+'%':fmt(d)}</span>`;
  };
  document.getElementById('whatifResult').innerHTML = `
    <div class="card p-3"><p class="kpi-label">Projected Revenue</p><p class="kpi-value text-indigo-600">${fmt(newRev)}</p><p class="text-xs mt-1">vs base · ${fmtDelta(drev)}</p></div>
    <div class="card p-3"><p class="kpi-label">Projected Profit</p><p class="kpi-value" style="color:var(--good-ink)">${fmt(newProfit)}</p><p class="text-xs mt-1">vs base · ${fmtDelta(dprof)}</p></div>
    <div class="card p-3"><p class="kpi-label">อัตรากำไร %</p><p class="kpi-value" style="color:var(--teal-ink)">${newMarg.toFixed(1)}%</p><p class="text-xs mt-1">${fmtDelta(newMarg - _wiBase.margin, true)} vs base</p></div>
    <div class="card p-3"><p class="kpi-label">Tx · Avg Ticket</p><p class="text-sm font-semibold">${fmtN(newTx)} × ${fmtN(newAvg)}</p><p class="text-xs text-slate-500 mt-1">${(newRev/_wiBase.revenue*100).toFixed(1)}% of base</p></div>
  `;
  mkChart('chartWhatIf', {
    type: 'bar',
    data: { labels: ['Revenue','Profit'], datasets: [
      { label: 'Base',     data: [_wiBase.revenue, baseProfit], backgroundColor: SEMANTIC.neutral },
      { label: 'What-if',  data: [newRev, newProfit], backgroundColor: SEMANTIC.revenue },
    ]},
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend:{position:'bottom',labels:{font:{size:10}}}, datalabels:{display:false} },
      scales: { y: { ticks: { callback: v => fmt(v) } } } },
  });
}

// ════════════════════════════════════════════════════════════════
// AI COMMENTARY
// ════════════════════════════════════════════════════════════════
function renderAIBlankState() {
  document.getElementById('aiCommentary').innerHTML = '<p class="text-slate-400 italic">Click "Generate commentary" to produce a plain-English summary of the current filters.</p>';
}

// ════════════════════════════════════════════════════════════════
// TOAST notifications
// ════════════════════════════════════════════════════════════════
function showToast(msg, type = 'info', duration = 4500) {
  const colors = { info:'#4a70b0', good:'#10b981', warn:'#f59e0b', bad:'#ef4444' };
  const icons  = { info:'ℹ️', good:'✓', warn:'⚠', bad:'✗' };
  const t = document.createElement('div');
  t.style.cssText = [
    'position:fixed','bottom:24px','right:24px','z-index:9999',
    'padding:12px 16px 12px 14px','border-radius:var(--r-md)',
    `background:${colors[type]||colors.info}`,
    'color:#fff','font-size:13px','font-weight:500',
    'max-width:380px','line-height:1.4',
    'box-shadow:0 8px 28px rgba(0,0,0,0.22)',
    'display:flex','align-items:flex-start','gap:8px',
    'animation:slideDown 0.22s ease',
    'cursor:pointer',
  ].join(';');
  t.innerHTML = `<span style="flex-shrink:0;font-size:15px">${icons[type]||icons.info}</span><span>${msg}</span>`;
  t.onclick = () => t.remove();
  document.body.appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity 0.3s'; setTimeout(() => t.remove(), 320); }, duration);
}

// ════════════════════════════════════════════════════════════════
// COST SPIKE alerts on upload
// ════════════════════════════════════════════════════════════════
async function checkCostSpikes() {
  try {
    const d = await api(`${API}/api/cost/diff?threshold_pct=10`);
    if (d.recent_updates?.length) {
      const spikes = d.recent_updates.slice(0, 4);
      const lines = spikes.map(r => {
        const pct = r.pct_change != null ? (r.pct_change > 0 ? '+' : '') + r.pct_change.toFixed(0) + '%' : '?';
        return `${r.sku_code} ${pct}`;
      }).join(' · ');
      showToast(`Cost changes &gt;10% detected: ${lines}`, 'warn', 9000);
    }
  } catch(e) {}
}

// ════════════════════════════════════════════════════════════════
// UNIT TOGGLE ( total vs per-tx vs per-customer)
// ════════════════════════════════════════════════════════════════
function getUnitMode() { return document.getElementById('selUnit')?.value || 'total'; }
function onUnitChange() {
  localStorage.setItem('oshinei_unit', getUnitMode());
  loadActiveTab();
}
function applyStoredUnit() {
  const stored = localStorage.getItem('oshinei_unit');
  if (stored && document.getElementById('selUnit')) document.getElementById('selUnit').value = stored;
}
// Scale a revenue value by the selected unit mode
function scaleRevenue(value, txCount, custCount) {
  const mode = getUnitMode();
  if (mode === 'per_tx'   && txCount   > 0) return value / txCount;
  if (mode === 'per_cust' && custCount > 0) return value / custCount;
  return value;
}
function unitLabel() {
  const mode = getUnitMode();
  if (mode === 'per_tx')   return '/ บิล';
  if (mode === 'per_cust') return '/ ลูกค้า';
  return '';
}

// ════════════════════════════════════════════════════════════════
// KEYBOARD SHORTCUTS
// ════════════════════════════════════════════════════════════════
const SHORTCUTS = [
  { k: '/',  desc: 'Focus search' },
  { k: '?',  desc: 'Show this help' },
  { k: 'd',  desc: 'สลับโหมดมืด' },
  { k: 'p',  desc: 'Print / Save as PDF' },
  { k: 'f',  desc: 'Toggle filter presets bar' },
  { k: 'Esc',desc: 'Close modal / drill-down' },
  { k: '1', desc: 'Daily Brief (Home)' },
  { k: '2', desc: 'Overview' },
  { k: '3', desc: 'Trend' },
  { k: '4', desc: 'Compare A/B' },
  { k: '5', desc: 'Guest & SPH' },
  { k: '6', desc: 'วิเคราะห์เมนู' },
  { k: '7', desc: 'Staff Scorecard' },
  { k: '8', desc: 'Discounts & Leakage' },
  { k: '9', desc: 'Time & Flow' },
  { k: '0', desc: 'Settings' },
];
const TAB_NUMS = {
  '1':'home', '2':'overall', '3':'trend', '4':'compare',
  '5':'guest', '6':'engineering', '7':'staff', '8':'discounts',
  '9':'timeflow', '0':'settings'
};
function showShortcutsHelp() {}
function setupShortcuts() {
  document.addEventListener('keydown', (e) => {
    if (!_desktopBooted && _phoneFirst()) return;   // the summary has no tabs to switch, and the slices are not here yet
    const tag = (e.target.tagName||'').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === '?')  { e.preventDefault(); showShortcutsHelp(); }
    // PR-D4: "/" opens the page search (js/18-launcher.js; Ctrl+K does too); the branch / SKU box of the top bar is reached by a click
    else if (e.key === '/') { e.preventDefault(); if (typeof pgSearchOpen === 'function' && _launcherLanding() && !phOn()) pgSearchOpen(); else document.getElementById('globalSearch')?.focus(); }
    else if (e.key === 'd') { toggleDarkMode(); }
    else if (e.key === 'p') { printReport(); }
    else if (e.key === 'f') { togglePresetMenu(); }
    else if (TAB_NUMS[e.key]) { switchTab(TAB_NUMS[e.key]); }
  });
}

// ══════════════════════════════════════════════════════════════════════════
// GP by Branch tab — BoM formula (Easy)
// ══════════════════════════════════════════════════════════════════════════
let _gpbData = null;
let _gpbSortKey = 'total_revenue';
let _gpbSortDir = 'desc';
let _gpbExpanded = new Set();
window.gpbGranularity = window.gpbGranularity || 'month';

function setGpbGranularity(g) {
  if (!['day','week','month'].includes(g)) g = 'month';
  window.gpbGranularity = g;
  ['day','week','month'].forEach(k => {
    const b = document.getElementById('gpbGran-' + k);
    if (b) b.classList.toggle('active', k === g);
  });
  loadGpBranch();
}

function _gpbStatusBucket(foodPct, lo, hi) {
  if (foodPct == null) return { label:'—', cls:'text-slate-400', emoji:'·', color:'#94a3b8' };
  const band = (hi - lo); // typically 10
  if (foodPct >= lo && foodPct <= hi) return { label:'ในเป้า', cls:'text-emerald-600', emoji:'✓', color:'#10b981' };
  if (foodPct >= lo - band*0.4 && foodPct <= hi + band*0.4) return { label:'ใกล้ขอบ', cls:'text-amber-600', emoji:'⚠', color:'#f59e0b' };
  return { label:'นอกเป้า', cls:'text-rose-600', emoji:'●', color:'#ef4444' };
}

function _gpbInitFilters() {
  // Populate month dropdowns from window.allMonths (preferred) or use global current values
  const fromSel = document.getElementById('gpbMonthFrom');
  const toSel   = document.getElementById('gpbMonthTo');
  if (!fromSel || !toSel) return;
  if (fromSel.options.length === 0) {
    const months = Array.isArray(window.allMonths) ? window.allMonths.slice() : [];
    const opts = ['<option value="">— ทั้งหมด —</option>'].concat(
      months.map(m => `<option value="${m}">${m}</option>`)
    ).join('');
    fromSel.innerHTML = opts;
    toSel.innerHTML   = opts;
    // default to global filter values if any
    const gf = (typeof getMonthFrom === 'function') ? getMonthFrom() : '';
    const gt = (typeof getMonthTo   === 'function') ? getMonthTo()   : '';
    if (gf) fromSel.value = gf;
    if (gt) toSel.value   = gt;
  }
}

async function loadGpBranch() {
  _gpbInitFilters();
  // Follow the GLOBAL month range when it changes (a manual local pick wins until
  // the global range changes again).
  (function () {
    const gf = getMonthFrom(), gt = getMonthTo(), key = gf + '|' + gt;
    if (key !== _extraLastGlobal['gpbranch_r']) {
      const fS = document.getElementById('gpbMonthFrom'), tS = document.getElementById('gpbMonthTo');
      if (fS && [...fS.options].some(o => o.value === gf)) fS.value = gf;
      if (tS && [...tS.options].some(o => o.value === gt)) tS.value = gt;
      _extraLastGlobal['gpbranch_r'] = key;
    }
  })();
  const status = document.getElementById('gpbStatus');
  if (status) status.textContent = 'กำลังโหลด…';
  const mf = document.getElementById('gpbMonthFrom')?.value || '';
  const mt = document.getElementById('gpbMonthTo')?.value   || '';
  const params = new URLSearchParams();
  if (mf) params.set('month_from', mf);
  if (mt) params.set('month_to',   mt);
  const gran = window.gpbGranularity || 'month';
  if (gran !== 'month') params.set('granularity', gran);
  const qs = params.toString();
  try {
    const data = await api(`${API}/api/gp/by-branch-month${qs?'?'+qs:''}`);
    _gpbData = data;
    document.getElementById('gpbTargetText').textContent = `${data.target_lo}–${data.target_hi}%`;
    _gpbRenderSummary(data);
    _gpbRenderNarrative(data);
    _gpbRenderBoxes();
    _gpbRenderTable();
    _gpbRenderBarChart();
    _gpbRenderHeatmap();
    if (status) status.textContent = `อัปเดต ${new Date().toLocaleTimeString('th-TH')}`;
  } catch (err) {
    console.error('loadGpBranch failed', err);
    if (status) status.textContent = 'โหลดข้อมูลไม่สำเร็จ';
  }
}

function _gpbRenderSummary(data) {
  const g = data.grand_total || {};
  const bucket = _gpbStatusBucket(g.food_cost_pct, data.target_lo, data.target_hi);
  const card = (label, value, sub, color) => `
    <div class="card p-4" style="border-top:3px solid ${color || '#10b981'}">
      <div class="text-xs text-slate-500 mb-1">${label}</div>
      <div class="text-xl font-bold" style="color:${textInk(color || 'var(--text-primary)')}">${value}</div>
      ${sub ? `<div class="text-xs text-slate-400 mt-1">${sub}</div>` : ''}
    </div>`;
  const fmtB = n => (n == null) ? '—' : Number(n).toLocaleString('th-TH', {maximumFractionDigits:0}) + ' ';
  const fmtPct = n => (n == null) ? '—' : n.toFixed(1) + '%';
  document.getElementById('gpbSummary').innerHTML = [
    card('รายได้รวม (ex VAT)', fmtB(g.revenue), `${data.rows.length} สาขา`, '#0ea5e9'),
    card('BoM Cost รวม',       fmtB(g.cogs),    'ตามสูตร BoM ใหม่',           '#f59e0b'),
    card('GP รวม',             fmtB(g.gp),      (g.gp >= 0 ? 'กำไร' : 'ขาดทุน'), g.gp >= 0 ? '#10b981' : '#ef4444'),
    card('GP %',               fmtPct(g.gp_pct), `เป้า ${data.target_lo}-${data.target_hi}%`, '#7a66b8'),
    card('ต้นทุนอาหาร %', `${bucket.emoji} ${fmtPct(g.food_cost_pct)}`, bucket.label, bucket.color),
  ].join('');
}

// Plain-Thai narrative summarising the GP picture across branches (page read).
function _gpbRenderNarrative(data) {
  const el = document.getElementById('gpbNarrative'); if (!el) return;
  const g = data.grand_total || {};
  const lo = data.target_lo, hi = data.target_hi;
  const rows = (data.rows || []).map(r => {
    const rev = r.total_revenue || 0, cogs = r.total_cogs || 0;
    return { branch: r.branch, rev, gpp: rev ? (rev - cogs) / rev * 100 : null, fc: rev ? cogs / rev * 100 : null };
  }).filter(r => r.rev > 0 && r.gpp != null);
  if (!rows.length) { el.innerHTML = '<p class="text-xs text-slate-400 italic">ไม่มีข้อมูลพอจะสรุป</p>'; return; }
  const byGp = [...rows].sort((a, b) => b.gpp - a.gpp);
  const best = byGp[0], worst = byGp[byGp.length - 1];
  const overFc = rows.filter(r => r.fc > hi);             // food cost over target = margin risk
  const inBand = rows.filter(r => r.fc >= lo && r.fc <= hi);
  const gap = best.gpp - worst.gpp;
  const fcCol = (g.food_cost_pct > hi) ? 'var(--bad-ink)' : (g.food_cost_pct < lo) ? '#0ea5e9' : 'var(--good-ink)';
  const fmtB = n => Number(n || 0).toLocaleString('th-TH', { maximumFractionDigits: 0 });
  const lines = [
    `ภาพรวมช่วงนี้: รายได้ <b>฿${fmtB(g.revenue)}</b> · ต้นทุน BoM <b>฿${fmtB(g.cogs)}</b> · GP <b style="color:${(g.gp || 0) >= 0 ? 'var(--good-ink)' : 'var(--bad-ink)'}">฿${fmtB(g.gp)} (${(g.gp_pct || 0).toFixed(1)}%)</b> · ต้นทุนอาหาร <b style="color:${textInk(fcCol)}">${(g.food_cost_pct || 0).toFixed(1)}%</b> (เป้า ${lo}–${hi}%)`,
    `GP สูงสุด <b>${esc(best.branch)}</b> ${best.gpp.toFixed(1)}% · ต่ำสุด <b>${esc(worst.branch)}</b> ${worst.gpp.toFixed(1)}% — ห่างกัน <b>${gap.toFixed(1)} จุด</b>`,
    overFc.length
      ? `⚠ <b style="color:var(--bad-ink)">${overFc.length} สาขา</b> ต้นทุนอาหาร เกินเป้า (${overFc.sort((a,b)=>b.fc-a.fc).slice(0, 3).map(r => esc(r.branch)).join(', ')}${overFc.length > 3 ? ' …' : ''}) — กดดันมาร์จิน ควรตรวจสูตร/ปริมาณเสิร์ฟ/ของเสีย`
      : `ทุกสาขา ต้นทุนอาหาร อยู่ในกรอบเป้า — มาร์จินสุขภาพดี`,
    `${inBand.length}/${rows.length} สาขาอยู่ในกรอบเป้า — ${gap > 15 ? 'ความเหลื่อมล้ำระหว่างสาขาสูง ควรถอดบทเรียนสาขาเด่นไปใช้กับสาขาท้าย' : 'การกระจาย GP ค่อนข้างสม่ำเสมอ'}`,
  ];
  el.innerHTML = `<div class="text-xs space-y-1.5" style="color:var(--text-primary);line-height:1.5">${lines.map(t => `<div>• ${t}</div>`).join('')}</div>`;
}

function _gpbRenderBoxes() {
  if (!_gpbData) return;
  const el = document.getElementById('gpbBoxes');
  if (!el) return;
  const lo = _gpbData.target_lo, hi = _gpbData.target_hi;
  const boxes = (_gpbData.rows || []).map(r => {
    // Roll up monthly (Easy+CM) → GP% per month, then take latest vs previous.
    const byMonth = {};
    (r.monthly || []).forEach(m => {
      if (!byMonth[m.month]) byMonth[m.month] = { revenue: 0, cogs: 0 };
      byMonth[m.month].revenue += (m.revenue || 0);
      byMonth[m.month].cogs    += (m.cogs    || 0);
    });
    const months = Object.keys(byMonth).filter(m => byMonth[m].revenue > 0).sort();
    const gpPctOf = m => { const c = byMonth[m]; return (c && c.revenue > 0) ? (c.revenue - c.cogs) / c.revenue * 100 : null; };
    const fcPctOf = m => { const c = byMonth[m]; return (c && c.revenue > 0) ? c.cogs / c.revenue * 100 : null; };
    const lastM = months[months.length - 1] || null;
    const prevM = months.length >= 2 ? months[months.length - 2] : null;
    const cur  = lastM ? gpPctOf(lastM) : (r.total_gp_pct ?? null);
    const prev = prevM ? gpPctOf(prevM) : null;
    return {
      branch: r.branch,
      cur, prev,
      delta: (cur != null && prev != null) ? (cur - prev) : null,
      lastM, prevM,
      fc: lastM ? fcPctOf(lastM) : r.total_food_cost_pct,
    };
  }).filter(b => b.cur != null)
    .sort((a, b) => (b.cur ?? -999) - (a.cur ?? -999));

  if (!boxes.length) { el.innerHTML = '<p class="text-xs text-slate-400">ไม่มีข้อมูล</p>'; return; }
  // Colour the box by GP MAGNITUDE (higher GP = better) — the food-cost-vs-target
  // band lives in the table/heatmap; here we want a clean progression read.
  el.innerHTML = boxes.map(b => {
    const gpColor = b.cur >= 50 ? '#059669' : b.cur >= 40 ? '#0ea5e9' : b.cur >= 30 ? '#d97706' : '#dc2626';
    const dColor = b.delta == null ? '#94a3b8' : b.delta >= 0 ? 'var(--good-ink)' : 'var(--bad-ink)';
    const dTxt = b.delta == null
      ? '<span class="text-slate-400">ไม่มีเดือนก่อนเทียบ</span>'
      : `${b.delta >= 0 ? '▲' : '▼'} ${Math.abs(b.delta).toFixed(1)} pp <span class="text-slate-400">vs ${b.prevM}</span>`;
    return `<div class="rounded-xl border p-3 gpb-box-drill" onclick="drillIntoBranch('${jsAttr(b.branch)}')" title="คลิกเพื่อดูเฉพาะ ${esc(b.branch)}" style="border-color:${gpColor}33;background:${gpColor}0a">
      <p class="text-sm font-semibold text-slate-700 truncate" title="${esc(b.branch)}">${esc(b.branch)}</p>
      <p class="text-2xl font-extrabold mt-1 leading-none" style="color:${textInk(gpColor)}">${b.cur.toFixed(1)}%</p>
      <p class="text-[11px] text-slate-400 mt-0.5">GP% · ${b.lastM || ''}</p>
      <p class="text-xs font-semibold mt-1" style="color:${textInk(dColor)}">${dTxt}</p>
      <p class="text-[11px] text-slate-400 mt-0.5">ต้นทุนอาหาร ${b.fc != null ? b.fc.toFixed(1) + '%' : '—'}</p>
    </div>`;
  }).join('');
}

function _gpbSort(key) {
  if (_gpbSortKey === key) {
    _gpbSortDir = (_gpbSortDir === 'desc') ? 'asc' : 'desc';
  } else {
    _gpbSortKey = key;
    _gpbSortDir = (key === 'branch') ? 'asc' : 'desc';
  }
  _gpbRenderTable();
}

function _gpbRenderTable() {
  if (!_gpbData) return;
  const rows = _gpbData.rows.slice();
  const dir = (_gpbSortDir === 'desc') ? -1 : 1;
  rows.sort((a, b) => {
    const av = a[_gpbSortKey];
    const bv = b[_gpbSortKey];
    if (av == null) return 1;
    if (bv == null) return -1;
    if (typeof av === 'string') return dir * av.localeCompare(bv, 'th');
    return dir * (av - bv);
  });
  const fmtB = n => (n == null) ? '—' : Number(n).toLocaleString('th-TH', {maximumFractionDigits:0});
  const fmtPct = n => (n == null) ? '—' : n.toFixed(1) + '%';
  const lo = _gpbData.target_lo, hi = _gpbData.target_hi;

  const html = rows.map(r => {
    const bucket = _gpbStatusBucket(r.total_food_cost_pct, lo, hi);
    const expanded = _gpbExpanded.has(r.branch_id);
    const arrow = expanded ? '▾' : '▸';
    const main = `
      <tr class="gpb-row" style="border-bottom:1px solid var(--border);cursor:pointer" onclick="_gpbToggleRow(${r.branch_id})">
        <td class="px-3 py-2 font-medium">${arrow} ${esc(r.branch)}<button class="drill-branch-btn" onclick="event.stopPropagation();drillIntoBranch('${jsAttr(r.branch)}')" title="ดูเฉพาะสาขานี้">เจาะลึก →</button></td>
        <td class="px-3 py-2 text-right">${fmtB(r.total_revenue)}</td>
        <td class="px-3 py-2 text-right">${fmtB(r.total_cogs)}</td>
        <td class="px-3 py-2 text-right" style="color:${(r.total_gp || 0) >= 0 ? 'var(--good-ink)' : 'var(--bad-ink)'};font-weight:600">${fmtB(r.total_gp)}</td>
        <td class="px-3 py-2 text-right" style="font-weight:600">${fmtPct(r.total_gp_pct)}</td>
        <td class="px-3 py-2 text-right">${fmtPct(r.total_food_cost_pct)}</td>
        <td class="px-3 py-2 text-center"><span class="${bucket.cls}" style="font-weight:600">${bucket.emoji} ${bucket.label}</span></td>
      </tr>`;
    if (!expanded) return main;
    const gran = (_gpbData.granularity || 'month');
    const useTrend = gran !== 'month' && Array.isArray(r.trend);
    const buckets = useTrend
      ? r.trend.slice().sort((a, b) => String(a.bucket).localeCompare(String(b.bucket)))
      : (r.monthly || []).slice().sort((a, b) => a.month.localeCompare(b.month));
    const sub = buckets.map(m => {
      const bk = _gpbStatusBucket(m.food_cost_pct, lo, hi);
      const label = useTrend ? m.bucket : `${m.month} · ${m.source || ''}`;
      return `
        <tr style="background:var(--bg-subtle);border-bottom:1px solid var(--border)">
          <td class="px-3 py-1 pl-8 text-xs text-slate-500">${label}</td>
          <td class="px-3 py-1 text-right text-xs">${fmtB(m.revenue)}</td>
          <td class="px-3 py-1 text-right text-xs">${fmtB(m.cogs)}</td>
          <td class="px-3 py-1 text-right text-xs" style="color:${(m.gp || 0) >= 0 ? 'var(--good-ink)' : 'var(--bad-ink)'}">${fmtB(m.gp)}</td>
          <td class="px-3 py-1 text-right text-xs">${fmtPct(m.gp_pct)}</td>
          <td class="px-3 py-1 text-right text-xs">${fmtPct(m.food_cost_pct)}</td>
          <td class="px-3 py-1 text-center text-xs"><span class="${bk.cls}">${bk.emoji}</span></td>
        </tr>`;
    }).join('');
    return main + sub;
  }).join('');
  document.getElementById('gpbTableBody').innerHTML = html;
}

function _gpbToggleRow(branchId) {
  if (_gpbExpanded.has(branchId)) _gpbExpanded.delete(branchId);
  else _gpbExpanded.add(branchId);
  _gpbRenderTable();
}

function _gpbRenderBarChart() {
  if (!_gpbData) return;
  const canvas = document.getElementById('gpbBarChart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (chartInstances['gpbBar']) { chartInstances['gpbBar'].destroy(); }
  const rows = _gpbData.rows.slice().sort((a, b) => (b.total_gp_pct || -999) - (a.total_gp_pct || -999));
  const labels = rows.map(r => r.branch);
  const values = rows.map(r => r.total_gp_pct == null ? 0 : r.total_gp_pct);
  const lo = _gpbData.target_lo, hi = _gpbData.target_hi;
  // GP-side band: if food cost target is 45-55%, then GP target band is 45-55% too
  const colors = rows.map(r => _gpbStatusBucket(r.total_food_cost_pct, lo, hi).color);

  chartInstances['gpbBar'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'GP %',
        data: values,
        backgroundColor: colors,
        borderColor: colors,
        borderWidth: 1,
      }],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const r = rows[ctx.dataIndex];
              return [
                `GP%: ${(r.total_gp_pct ?? 0).toFixed(1)}%`,
                `ต้นทุนอาหาร: ${(r.total_food_cost_pct ?? 0).toFixed(1)}%`,
                `รายได้: ${Number(r.total_revenue||0).toLocaleString('th-TH')} `,
                `GP: ${Number(r.total_gp||0).toLocaleString('th-TH')} `,
              ];
            },
          },
        },
        annotation: {
          annotations: {
            band: {
              type: 'box',
              xMin: lo, xMax: hi,
              backgroundColor: 'rgba(16,185,129,0.10)',
              borderColor: 'rgba(16,185,129,0.40)',
              borderWidth: 1,
            },
          },
        },
      },
      scales: {
        x: { title: { display: true, text: 'GP %' }, grid: { color: 'rgba(148,163,184,0.15)' } },
        y: { ticks: { font: { size: 11 } } },
      },
    },
  });
}

function _gpbRenderHeatmap() {
  if (!_gpbData) return;
  const wrap = document.getElementById('gpbHeatmap');
  if (!wrap) return;
  // Collect all months across all branches
  const monthSet = new Set();
  _gpbData.rows.forEach(r => (r.monthly || []).forEach(m => monthSet.add(m.month)));
  const months = Array.from(monthSet).sort();
  if (months.length === 0) { wrap.innerHTML = '<p class="text-xs text-slate-400">ไม่มีข้อมูล</p>'; return; }

  const lo = _gpbData.target_lo, hi = _gpbData.target_hi;
  // Color scale by food cost % vs target
  const cellColor = (fcPct) => {
    if (fcPct == null) return '#f1f5f9';
    return _gpbStatusBucket(fcPct, lo, hi).color;
  };

  const fmtB = n => Number(n||0).toLocaleString('th-TH', {maximumFractionDigits:0});

  // Sort branches by total revenue desc
  const rows = _gpbData.rows.slice().sort((a, b) => (b.total_revenue||0) - (a.total_revenue||0));

  let html = '<table style="border-collapse:collapse;font-size:11px"><thead><tr>';
  html += '<th style="padding:4px 8px;text-align:left;position:sticky;left:0;background:var(--bg-subtle);z-index:2">สาขา</th>';
  months.forEach(m => { html += `<th style="padding:4px 6px;text-align:center;background:var(--bg-subtle);white-space:nowrap">${m}</th>`; });
  html += '<th style="padding:4px 8px;background:var(--bg-subtle)">รวม</th>';
  html += '</tr></thead><tbody>';

  rows.forEach(r => {
    const byMonth = {};
    (r.monthly || []).forEach(m => {
      // If both Easy and CM exist for same month, aggregate them
      if (!byMonth[m.month]) byMonth[m.month] = { revenue:0, cogs:0 };
      byMonth[m.month].revenue += (m.revenue || 0);
      byMonth[m.month].cogs    += (m.cogs    || 0);
    });
    html += `<tr><td style="padding:4px 8px;font-weight:600;position:sticky;left:0;background:var(--bg-card);z-index:1;white-space:nowrap">${esc(r.branch)}</td>`;
    months.forEach(m => {
      const cell = byMonth[m];
      if (!cell || cell.revenue === 0) {
        html += `<td style="padding:4px 6px;text-align:center;background:#f8fafc;color:#5f6c80">—</td>`;
      } else {
        const gp = cell.revenue - cell.cogs;
        const gpPct = cell.revenue > 0 ? (gp / cell.revenue * 100) : null;
        const fcPct = cell.revenue > 0 ? (cell.cogs / cell.revenue * 100) : null;
        const bg = cellColor(fcPct);
        const tip = `${r.branch} · ${m}\nรายได้: ${fmtB(cell.revenue)} \nBoM: ${fmtB(cell.cogs)} \nGP: ${fmtB(gp)}  (${gpPct == null ? '—' : gpPct.toFixed(1)}%)`;
        html += `<td title="${tip}" style="padding:4px 6px;text-align:center;background:${bg};color:${inkOn(bg)};font-weight:600;min-width:48px">${gpPct == null ? '—' : gpPct.toFixed(0) + '%'}</td>`;
      }
    });
    const totBucket = _gpbStatusBucket(r.total_food_cost_pct, lo, hi);
    html += `<td style="padding:4px 8px;text-align:center;background:${totBucket.color};color:${inkOn(totBucket.color)};font-weight:700">${r.total_gp_pct == null ? '—' : r.total_gp_pct.toFixed(0)+'%'}</td>`;
    html += '</tr>';
  });
  html += '</tbody></table>';
  wrap.innerHTML = html;
}

// Wire init additions
const _origInit = init;
// @ts-ignore — deliberate: this slice wraps the app's init() to add its startup steps
init = async function() {
  await _origInit();
  applyStoredUnit();
  setupShortcuts();
};

// ════════════════════════════════════════════════════════════════
// PHASE 12 — BRANCH BoM DEEP DIVE
// ════════════════════════════════════════════════════════════════
let _bbomData = null;
let _bbomSortKey = 'total_cogs';
let _bbomSortDir = 'desc';
let _bbomFiltersReady = false;
window.bbomGranularity = window.bbomGranularity || 'month';

function setBbomGranularity(g) {
  if (!['day','week','month'].includes(g)) g = 'month';
  window.bbomGranularity = g;
  ['day','week','month'].forEach(k => {
    const b = document.getElementById('bbomGran-' + k);
    if (b) b.classList.toggle('active', k === g);
  });
  loadBranchBom();
}
const _BBOM_TYPE_COLORS = {
  protein:'#ef4444', carb:'#f59e0b', veg:'#10b981',
  sauce:'#7a66b8', oil:'#f97316', seasoning:'#0891b2', other:'#64748b'
};
const _BBOM_TYPE_LABELS = {
  protein:'โปรตีน', carb:'คาร์โบไฮเดรต', veg:'ผัก',
  sauce:'ซอส', oil:'น้ำมัน', seasoning:'เครื่องปรุง', other:'อื่นๆ'
};

async function _bbomInitFilters() {
  if (_bbomFiltersReady) return;
  const brSel = document.getElementById('bbomBranch');
  const mfSel = document.getElementById('bbomMonthFrom');
  const mtSel = document.getElementById('bbomMonthTo');
  try {
    const [branches, months] = await Promise.all([
      api(`${API}/api/branches`),
      api(`${API}/api/months`),
    ]);
    brSel.innerHTML = (branches || []).filter(b => b.active !== false)
      .map(b => `<option value="${esc(b.name)}">${esc(b.name)}</option>`).join('');
    // Default to the current top-bar branch if set, else first
    const curBr = (typeof getBranch === 'function') ? getBranch() : '';
    if (curBr && (branches || []).some(b => b.name === curBr)) brSel.value = curBr;
    // Months — empty option means "all"
    const monthOpts = '<option value="">— ทั้งหมด —</option>' +
      (months || []).map(m => `<option value="${m}">${m}</option>`).join('');
    mfSel.innerHTML = monthOpts;
    mtSel.innerHTML = monthOpts;
    _bbomFiltersReady = true;
  } catch (e) {
    console.error('bbom filter init failed', e);
  }
}

// Phase 16: chain-wide mode toggle
let _bbomViewMode = 'branch';   // 'branch' | 'chain'
function setBbomViewMode(mode) {
  if (mode !== 'branch' && mode !== 'chain') return;
  _bbomViewMode = mode;
  document.querySelectorAll('#bbomViewModeBar [data-bbom-mode]').forEach(b =>
    b.classList.toggle('active', b.dataset.bbomMode === mode));
  // Hide branch selector + label when chain mode
  const brSel = document.getElementById('bbomBranch');
  const brLabel = document.getElementById('bbomBranchLabel');
  if (brSel)   brSel.style.display   = (mode === 'chain') ? 'none' : '';
  if (brLabel) brLabel.style.display = (mode === 'chain') ? 'none' : '';
  loadBranchBom(true);
}

// Requisitions vs recipes (routers/requisition.py). Two honest numbers that will
// never be equal: what the stores DREW and what the recipes say they should have
// used. The gap between them is the largest controllable cost in the business.
let _reqInit = false;
async function loadRequisitionGap() {
  const body = document.getElementById('reqBody');
  if (!body) return;
  const mf = document.getElementById('reqMonthFrom'), mt = document.getElementById('reqMonthTo');
  const qs = new URLSearchParams();
  if (_reqInit && mf.value) qs.set('month', mf.value);
  if (_reqInit && mt.value) qs.set('month_to', mt.value);
  let d;
  try { d = await api(`${API}/api/requisition/vs-recipe?${qs}`); }
  catch (e) { body.innerHTML = '<p class="text-xs text-slate-400">โหลดไม่ได้</p>'; return; }
  if (!d.ready) {
    body.innerHTML = `<p class="text-xs" style="color:var(--orange-ink)">${esc(d.why || 'ยังไม่มีข้อมูล')}</p>`;
    return;
  }
  if (!_reqInit) {                       // first load: fill the pickers from what exists
    const opts = d.months.map(m => `<option value="${m}">${m}</option>`).join('');
    mf.innerHTML = opts; mt.innerHTML = opts;
    mf.value = d.month_from; mt.value = d.month_to;
    _reqInit = true;
  }
  const b = v => v == null ? '-' : '฿' + fmtN(v);
  const b2 = v => v == null ? '-' : '฿' + Number(v).toFixed(2);
  const pc = v => v == null ? '-' : v.toFixed(1) + '%';
  const ratioCol = r => r == null ? '#64748b' : r >= 1.4 ? 'var(--bad-ink)' : r >= 1.25 ? '#ea580c' : 'var(--good-ink)';
  const row = (r, bold) => `<tr class="border-t border-slate-100${bold ? ' font-semibold' : ''}" ${bold ? 'style="background:#fff7ed"' : ''}>
      <td class="py-1.5 pr-2">${esc(r.branch)}${r.basis === 'used'
        ? ' <span class="text-[10px] px-1 rounded" style="color:#047857;background:#d1fae5" title="มีมูลค่าสต๊อกต้นงวดและปลายงวดครบ — คิดเป็น ต้นงวด + เบิก − ปลายงวด">ใช้จริง</span>'
        : (r.branch_id != null ? ' <span class="text-[10px] px-1 rounded" style="color:#9a3412;background:#ffedd5" title="ยังไม่มีมูลค่าสต๊อก — ตัวเลขนี้คือของที่เบิกออก เป็นค่าสูงสุด">เบิก</span>' : '')}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${fmtN(r.covers || 0)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${b2(r.actual_head)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${b2(r.recipe_head)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-bold" style="color:${textInk(ratioCol(r.ratio))}">${r.ratio == null ? '-' : r.ratio.toFixed(2) + '×'}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${pc(r.food_pct_actual)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${pc(r.food_pct_recipe)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums" style="color:var(--bad-ink)">${b(r.gap_value)}</td>
      <td class="py-1.5 text-right">${r.branch_id == null ? '' : `<button class="text-[11px] text-indigo-600 underline" onclick="enterStockCount(${r.branch_id},'${esc(r.branch)}')">กรอกสต๊อก</button>`}</td></tr>`;
  const ing = (d.ingredients || []).slice(0, 15).map((x, i) => `<tr class="border-t border-slate-100">
      <td class="py-1 pr-2 text-slate-400">${i + 1}</td>
      <td class="py-1 pr-2">${esc(x.name || '')} <span class="text-[10px] text-slate-400">${esc(x.code)}</span>${x.in_recipes ? '' : ' <span class="text-[10px] px-1 rounded" style="color:#b91c1c;background:#fee2e2" title="ไม่มีสูตรอาหารใดอ้างถึงวัตถุดิบนี้เลย — เงินก้อนนี้ไม่เคยเข้าต้นทุนตามสูตร">ไม่อยู่ในสูตรใด</span>'}</td>
      <td class="py-1 pr-2 text-right tabular-nums">${b(x.drawn)}</td>
      <td class="py-1 pr-2 text-right tabular-nums">${b(x.booked)}</td>
      <td class="py-1 text-right tabular-nums font-semibold" style="color:var(--bad-ink)">${b(x.gap)}</td></tr>`).join('');
  body.innerHTML = `
    <div style="overflow-x:auto"><table class="w-full text-sm"><thead><tr class="text-left text-slate-400 text-xs border-b">
      <th class="py-1.5 pr-2">สาขา</th><th class="py-1.5 pr-2 text-right">คนทาน</th>
      <th class="py-1.5 pr-2 text-right">เบิก/ใช้จริง ต่อหัว</th><th class="py-1.5 pr-2 text-right">ตามสูตร ต่อหัว</th>
      <th class="py-1.5 pr-2 text-right">เท่า</th><th class="py-1.5 pr-2 text-right">ต้นทุนอาหาร % (จริง)</th>
      <th class="py-1.5 pr-2 text-right">ต้นทุนอาหาร % (สูตร)</th><th class="py-1.5 pr-2 text-right">ส่วนต่าง ฿</th><th></th>
    </tr></thead><tbody>${d.branches.map(r => row(r, false)).join('')}${row(d.total, true)}</tbody></table></div>
    <p class="text-[11px] mt-2 p-2 rounded" style="color:#9a3412;background:#fff7ed">⚠ ${esc(d.caveat)}</p>
    <div class="text-xs font-semibold text-slate-600 mt-4 mb-1">วัตถุดิบที่ส่วนต่างสูงสุด (เบิก − ที่สูตรคิดไว้) · วัตถุดิบที่ไม่อยู่ในสูตรใดเลยรวม ${b(d.not_in_any_recipe)}</div>
    <div style="max-height:340px;overflow:auto"><table class="w-full text-xs"><thead><tr class="text-left text-slate-400 border-b">
      <th class="py-1 pr-2">#</th><th class="py-1 pr-2">วัตถุดิบ</th><th class="py-1 pr-2 text-right">เบิก</th>
      <th class="py-1 pr-2 text-right">สูตรคิดไว้</th><th class="py-1 text-right">ส่วนต่าง</th></tr></thead><tbody>${ing}</tbody></table></div>`;
}

async function uploadRequisition(input) {
  const f = input.files && input.files[0];
  if (!f) return;
  const fd = new FormData(); fd.append('file', f);
  try {
    const res = await fetch(`${API}/api/requisition/upload`, { method: 'POST', body: fd });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((j.detail && (j.detail.detail || j.detail)) || ('HTTP ' + res.status));
    const s = (j.sheets || []).map(x => `${x.sheet.replace('Use ', '')} ${x.month}`).join(', ');
    showToast(`อัปโหลดแล้ว: ${s}${(j.skipped || []).length ? ` · ข้าม ${j.skipped.length} ชีต` : ''}`, 'success', 7000);
    _reqInit = false;
    await loadRequisitionGap();
  } catch (e) { showToast('อัปโหลดไม่สำเร็จ: ' + e.message, 'error', 8000); }
  input.value = '';
}

async function enterStockCount(branchId, branchName) {
  const m = document.getElementById('reqMonthTo')?.value;
  const v = await uiPrompt(`มูลค่าสต๊อกวัตถุดิบคงเหลือสิ้นเดือน ${m} ของ ${branchName} (บาท)\n\nต้องมีทั้งเดือนนี้และเดือนก่อนหน้า ระบบจึงจะคิดเป็น "ใช้จริง"`);
  if (v == null || v.trim() === '') return;
  const n = parseFloat(v.replace(/,/g, ''));
  if (!(n >= 0)) { showToast('กรอกเป็นตัวเลข', 'error'); return; }
  try {
    const res = await fetch(`${API}/api/requisition/stock-count`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ period_month: m, branch_id: branchId, closing_value: n }) });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    showToast(`บันทึกสต๊อกสิ้นเดือน ${m} ของ ${branchName} แล้ว`, 'success');
    await loadRequisitionGap();
  } catch (e) { showToast('บันทึกไม่สำเร็จ: ' + e.message, 'error'); }
}

async function loadBranchBom(force) {
  loadRequisitionGap();            // independent of the BoM filters below — own month pickers
  await _bbomInitFilters();
  // Phase 16: when chain mode, send branch=ALL (backend aggregates across all active branches)
  const branch = (_bbomViewMode === 'chain') ? 'ALL' : document.getElementById('bbomBranch').value;
  const mf     = document.getElementById('bbomMonthFrom').value;
  const mt     = document.getElementById('bbomMonthTo').value;
  if (!branch) {
    document.getElementById('bbomSummary').innerHTML =
      '<p class="text-slate-400 col-span-5 text-center py-8">เลือกสาขาก่อน</p>';
    return;
  }
  const status = document.getElementById('bbomStatus');
  status.textContent = 'กำลังโหลด…';
  const params = new URLSearchParams({ branch, top_n: 60 });
  if (mf) params.set('month_from', mf);
  if (mt) params.set('month_to',   mt);
  const gran = window.bbomGranularity || 'month';
  if (gran !== 'month') params.set('granularity', gran);
  try {
    const d = await fetch(`${API}/api/branch-bom/analysis?${params}`).then(r => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    });
    _bbomData = d;
    _bbomRenderSummary(d);
    _bbomRenderConcentration(d);
    _bbomRenderTrend(d);
    _bbomRenderMenuTable();
    _bbomRenderIngredients(d);
    _bbomRenderTypeChart(d);
    _bbomRenderCoverage(d);
    // Chain mode → also show per-branch summary table
    const brSummary = document.getElementById('bbomBranchSummary');
    if (_bbomViewMode === 'chain') {
      brSummary.style.display = '';
      _bbomLoadBranchSummary(mf, mt);
    } else {
      brSummary.style.display = 'none';
    }
    status.textContent = `อัพเดทเมื่อ ${new Date().toLocaleTimeString('th-TH')}`;
  } catch (err) {
    console.error('loadBranchBom failed', err);
    status.textContent = '⚠ โหลดข้อมูลไม่สำเร็จ';
    document.getElementById('bbomSummary').innerHTML =
      `<p class="text-red-500 col-span-5 text-center py-8">เกิดข้อผิดพลาด: ${err.message}</p>`;
  }
}

// ── Per-branch summary table (chain mode) ──────────────────────────────────
let _bbomBranchRows = [];
let _bbsumSortKey = 'total_gp';
let _bbsumSortDir = 'desc';
let _bbomGrand = null;
async function _bbomLoadBranchSummary(mf, mt) {
  const p = new URLSearchParams();
  if (mf) p.set('month_from', mf);
  if (mt) p.set('month_to', mt);
  try {
    const d = await api(`${API}/api/gp/by-branch-month?${p}`);
    _bbomBranchRows = d.rows || [];
    _bbomGrand = d.grand_total || null;
    _bbomRenderBranchSummary();
  } catch(e) { /* leave hidden */ }
}
function _bbomSortBranches(key) {
  if (_bbsumSortKey === key) _bbsumSortDir = _bbsumSortDir === 'desc' ? 'asc' : 'desc';
  else { _bbsumSortKey = key; _bbsumSortDir = key === 'branch' ? 'asc' : 'desc'; }
  _bbomRenderBranchSummary();
}
function _bbomRenderBranchSummary() {
  const rows = [..._bbomBranchRows].sort((a,b)=>{
    const k=_bbsumSortKey; let av=a[k], bv=b[k];
    if (typeof av === 'string') return _bbsumSortDir==='asc' ? String(av).localeCompare(bv) : String(bv).localeCompare(av);
    av=av||0; bv=bv||0; return _bbsumSortDir==='asc' ? av-bv : bv-av;
  });
  const fcColor = p => p==null?'#94a3b8' : (p<=70 && p>=40)?'#10b981' : p>70?'#ef4444':'#f59e0b';
  document.getElementById('bbomBranchSummaryBody').innerHTML = rows.map(b=>`
    <tr class="border-b border-slate-50 hover:bg-fuchsia-50">
      <td class="py-1.5 pr-3 font-medium">${esc(b.branch)}</td>
      <td class="py-1.5 pr-3 text-right">${fmtN(b.total_revenue)}</td>
      <td class="py-1.5 pr-3 text-right" style="color:var(--amber-ink)">${fmtN(b.total_cogs)}</td>
      <td class="py-1.5 pr-3 text-right" style="color:var(--good-ink)">${fmtN(b.total_gp)}</td>
      <td class="py-1.5 pr-3 text-right font-semibold">${b.total_gp_pct!=null?b.total_gp_pct.toFixed(1)+'%':'—'}</td>
      <td class="py-1.5 text-right font-semibold" style="color:${textInk(fcColor(b.total_food_cost_pct))}">${b.total_food_cost_pct!=null?b.total_food_cost_pct.toFixed(1)+'%':'—'}</td>
    </tr>`).join('') || '<tr><td colspan="6" class="py-3 text-center text-slate-400">—</td></tr>';
  const g = _bbomGrand;
  document.getElementById('bbomBranchSummaryFoot').innerHTML = g ? `
    <tr><td class="py-2 pr-3">รวมทุกสาขา (${rows.length})</td>
      <td class="py-2 pr-3 text-right">${fmtN(g.revenue)}</td>
      <td class="py-2 pr-3 text-right" style="color:var(--amber-ink)">${fmtN(g.cogs)}</td>
      <td class="py-2 pr-3 text-right" style="color:var(--good-ink)">${fmtN(g.gp)}</td>
      <td class="py-2 pr-3 text-right">${g.gp_pct}%</td>
      <td class="py-2 text-right" style="color:${textInk(fcColor(g.food_cost_pct))}">${g.food_cost_pct}%</td>
    </tr>` : '';
}

function _bbomFmtB(n) {
  return ''  + Number(n||0).toLocaleString('th-TH', { maximumFractionDigits: 0 });
}
function _bbomFmtPct(n) {
  return (n == null) ? '—' : (Number(n)||0).toFixed(1) + '%';
}

function _bbomRenderSummary(d) {
  const t = d.totals || {};
  const lo = 55, hi = 70;
  const fcInRange = t.food_cost_pct != null && t.food_cost_pct >= lo && t.food_cost_pct <= hi;
  const fcColor   = (t.food_cost_pct == null) ? '#64748b' :
                    fcInRange ? '#10b981' :
                    t.food_cost_pct > hi ? '#ef4444' : '#f59e0b';
  const gpColor   = (t.gp_pct == null) ? '#64748b' :
                    t.gp_pct >= 30 ? '#10b981' :
                    t.gp_pct >= 15 ? '#f59e0b' : '#ef4444';
  const card = (label, value, sub, color) =>
    `<div class="rounded-lg p-3 border" style="background:${color}10;border-color:${color}33">
       <p class="text-[10px] text-slate-500 uppercase tracking-wide font-semibold">${label}</p>
       <p class="text-lg font-bold" style="color:${textInk(color)}">${value}</p>
       <p class="text-[10px] text-slate-500">${sub}</p>
     </div>`;
  document.getElementById('bbomSummary').innerHTML = [
    card('รายได้',            _bbomFmtB(t.revenue),    `${d.period.months || 0} เดือน · ${esc(d.branch)}`, '#4a70b0'),
    card('ต้นทุนรวม',          _bbomFmtB(t.cogs),       `${(t.menus_with_cost||0).toLocaleString('th-TH')} เมนูมีต้นทุน`, '#0891b2'),
    card('Gross Profit',      _bbomFmtB(t.gp),         `GP ${_bbomFmtPct(t.gp_pct)}`, gpColor),
    card('ต้นทุนอาหาร %',       _bbomFmtPct(t.food_cost_pct), `เป้า ${lo}-${hi}%`, fcColor),
    card('เมนูที่มีสูตร BoM', `${(t.menus_with_bom_recipe||0).toLocaleString('th-TH')}`, `จากทั้งหมด ${(t.menus_in_period||0).toLocaleString('th-TH')} เมนู`, '#d946ef'),
  ].join('');
}

function _bbomRenderConcentration(d) {
  const c = d.concentration || {};
  const wrap = document.getElementById('bbomConcentration');
  if (c.top_5_menus_pct_of_cogs == null && c.top_10_menus_pct_of_cogs == null) {
    wrap.style.display = 'none';
    return;
  }
  wrap.style.display = '';
  wrap.querySelector('p').innerHTML =
    `<b>Top 5 เมนู</b> คิดเป็น <b>${_bbomFmtPct(c.top_5_menus_pct_of_cogs)}</b> ของต้นทุนสาขา · ` +
    `<b>Top 10</b> = <b>${_bbomFmtPct(c.top_10_menus_pct_of_cogs)}</b> · ` +
    `<b>20 อันดับ</b> = <b>${_bbomFmtPct(c.top_20_menus_pct_of_cogs)}</b>`;
}

function _bbomRenderTrend(d) {
  // Prefer the granularity-aware `trend` array (always present in new responses);
  // fall back to monthly_trend for older callers.
  const gran = d.granularity || 'month';
  const useTrend = Array.isArray(d.trend) && d.trend.length;
  const t = useTrend
    ? d.trend
    : (d.monthly_trend || []).map(r => ({
        bucket: r.month, revenue: r.revenue, cogs: r.cogs, gp: r.gp,
        gp_pct: r.gp_pct, food_cost_pct: r.food_cost_pct,
      }));
  const labels = t.map(r => r.bucket);
  if (chartInstances['bbomTrend']) { chartInstances['bbomTrend'].destroy(); }
  const ctx = document.getElementById('bbomTrendChart').getContext('2d');
  // Day = line (too many points for bars); week / month = bars.
  const chartType = gran === 'day' ? 'line' : 'bar';
  const pointRadius = gran === 'day' ? 1.5 : 3;
  chartInstances['bbomTrend'] = new Chart(ctx, {
    type: chartType,
    data: {
      labels,
      datasets: [
        { label:'รายได้', data:t.map(r => r.revenue), borderColor:'#4a70b0', backgroundColor: chartType==='line' ? '#4a70b020' : '#4a70b0cc', tension:.3, borderWidth:2, fill:false, pointRadius, borderRadius: 3 },
        { label:'ต้นทุนขาย',  data:t.map(r => r.cogs),    borderColor:'#ef4444', backgroundColor: chartType==='line' ? '#ef444420' : '#ef4444cc', tension:.3, borderWidth:2, fill:false, pointRadius, borderRadius: 3 },
        { label:'GP',    data:t.map(r => r.gp),      borderColor:'#10b981', backgroundColor: chartType==='line' ? '#10b98120' : '#10b981cc', tension:.3, borderWidth:2, fill:false, pointRadius, borderRadius: 3 },
      ],
    },
    options: {
      responsive:true, maintainAspectRatio:false,
      plugins: {
        legend:{ position:'bottom', labels:{ font:{ size:11 }, boxWidth:12 } },
        datalabels:{ display:false },
        title:{ display: gran !== 'month', text: gran === 'day' ? 'รายวัน (COGS pro-rated จาก tx revenue)' : gran === 'week' ? 'รายสัปดาห์ ISO' : '', font:{ size:11 }, color:'#64748b' },
        tooltip:{ mode:'index', intersect:false,
          callbacks:{ label: ctx => ` ${ctx.dataset.label}: ${_bbomFmtB(ctx.parsed.y)}` } },
      },
      scales: {
        x:{ ticks:{ font:{ size: gran === 'day' ? 9 : 10 }, maxRotation: gran === 'day' ? 60 : 0, autoSkip: true, maxTicksLimit: gran === 'day' ? 31 : 26 } },
        y:{ ticks:{ callback: v => _bbomFmtB(v), font:{ size:10 } } },
      },
    },
  });
}

function _bbomSort(key) {
  if (_bbomSortKey === key) _bbomSortDir = (_bbomSortDir === 'desc') ? 'asc' : 'desc';
  else { _bbomSortKey = key; _bbomSortDir = (key === 'sku_code') ? 'asc' : 'desc'; }
  _bbomRenderMenuTable();
}

function _bbomRenderMenuTable() {
  if (!_bbomData) return;
  const rows = (_bbomData.top_menus_by_cost || []).slice();
  const dir = (_bbomSortDir === 'desc') ? -1 : 1;
  rows.sort((a, b) => {
    const av = a[_bbomSortKey], bv = b[_bbomSortKey];
    if (typeof av === 'string') return av.localeCompare(bv) * dir;
    return ((av || 0) - (bv || 0)) * dir;
  });
  const tbody = document.getElementById('bbomMenuTableBody');
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="text-center py-6 text-slate-400">ไม่มีข้อมูล</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(r => {
    // Sparkline svg
    const monthly = r.monthly_cogs || [];
    const vals = monthly.map(x => x.cogs || 0);
    const mx = Math.max(...vals, 1);
    const W = 90, H = 20;
    const step = vals.length > 1 ? W / (vals.length - 1) : 0;
    const points = vals.map((v, i) => `${(i * step).toFixed(1)},${(H - (v / mx) * H).toFixed(1)}`).join(' ');
    const spark = vals.length > 0
      ? `<svg width="${W}" height="${H}" style="display:inline-block;vertical-align:middle">
           <polyline fill="none" stroke="#d946ef" stroke-width="1.5" points="${points}"/>
         </svg>`
      : '<span class="text-slate-300">—</span>';
    const bomBadge = r.has_bom_recipe
      ? '<span title="มีสูตร BoM" style="color:var(--fuchsia-ink)"></span>'
      : '<span title="ยังไม่มีสูตร BoM" style="color:var(--text-muted)">○</span>';
    return `<tr style="border-bottom:1px solid var(--border)">
      <td class="px-3 py-1.5 font-mono text-xs">${r.sku_code || ''}</td>
      <td class="px-3 py-1.5 text-xs">${esc(r.description || '')}</td>
      <td class="px-3 py-1.5 text-right text-xs">${(r.qty||0).toLocaleString('th-TH', {maximumFractionDigits:0})}</td>
      <td class="px-3 py-1.5 text-right text-xs">${(r.unit_cost||0).toFixed(2)}</td>
      <td class="px-3 py-1.5 text-right text-xs font-semibold">${_bbomFmtB(r.total_cogs)}</td>
      <td class="px-3 py-1.5 text-right text-xs" style="color:var(--fuchsia-ink);font-weight:600">${(r.pct_of_branch_cogs||0).toFixed(2)}%</td>
      <td class="px-3 py-1.5 text-center">${spark}</td>
      <td class="px-3 py-1.5 text-center">${bomBadge}</td>
    </tr>`;
  }).join('');

  // ── Totals row (sum of the displayed Top-N menus) ───────────────────
  const tQty  = rows.reduce((s, r) => s + (r.qty || 0), 0);
  const tCogs = rows.reduce((s, r) => s + (r.total_cogs || 0), 0);
  const tPct  = rows.reduce((s, r) => s + (r.pct_of_branch_cogs || 0), 0);
  const wAvg  = tQty > 0 ? tCogs / tQty : 0;   // weighted avg cost/unit
  tbody.innerHTML += `<tr style="border-top:2px solid var(--border);background:var(--bg-subtle);font-weight:700">
    <td class="px-3 py-2 text-xs" colspan="2">รวม Top ${rows.length}</td>
    <td class="px-3 py-2 text-right text-xs">${tQty.toLocaleString('th-TH', {maximumFractionDigits:0})}</td>
    <td class="px-3 py-2 text-right text-xs" style="color:var(--text-muted)">${wAvg.toFixed(2)}</td>
    <td class="px-3 py-2 text-right text-xs">${_bbomFmtB(tCogs)}</td>
    <td class="px-3 py-2 text-right text-xs" style="color:var(--fuchsia-ink)">${tPct.toFixed(2)}%</td>
    <td class="px-3 py-2"></td>
    <td class="px-3 py-2"></td>
  </tr>`;
}

function _bbomRenderIngredients(d) {
  const ings = d.top_ingredients_by_cost || [];
  // Drop any leftover chart instance from older bar-chart version
  if (chartInstances['bbomIng']) { chartInstances['bbomIng'].destroy(); delete chartInstances['bbomIng']; }
  const host = document.getElementById('bbomIngList');
  if (!host) return;
  if (!ings.length) {
    host.innerHTML = '<p class="text-center py-8 text-slate-400 text-sm">ไม่มีข้อมูลวัตถุดิบ</p>';
    return;
  }
  const maxCost = Math.max(...ings.map(i => i.total_cost || 0), 1);
  host.innerHTML = ings.map((i, idx) => {
    const color   = _BBOM_TYPE_COLORS[i.ingredient_type] || _BBOM_TYPE_COLORS.other;
    const typeLbl = _BBOM_TYPE_LABELS[i.ingredient_type] || i.ingredient_type || 'อื่นๆ';
    const barW    = Math.max(2, (i.total_cost || 0) / maxCost * 100);
    const qtyTxt  = (i.total_qty || 0).toLocaleString('th-TH', {maximumFractionDigits:1}) + (i.unit ? ' ' + esc(i.unit) : '');
    const menus   = (i.top_menus && i.top_menus.length) ? ' · เช่น ' + esc(i.top_menus.join(', ')) : '';
    return `<div style="padding:7px 0;border-bottom:1px solid var(--border)">
      <div class="flex items-center justify-between" style="gap:8px">
        <div class="flex items-center" style="gap:8px;min-width:0">
          <span style="flex:none;width:22px;text-align:right;color:var(--text-muted);font-size:11px;font-weight:600">${idx+1}</span>
          <span style="flex:none;width:9px;height:9px;border-radius:2px;background:${color}"></span>
          <span class="text-xs font-medium truncate" title="${esc(i.ingredient_name||'')}">${esc(i.ingredient_name||'')}</span>
          ${i.ingredient_code ? `<span class="text-xs font-mono text-slate-400" style="flex:none">${esc(i.ingredient_code)}</span>` : ''}
          <span style="flex:none;font-size:10px;padding:1px 6px;border-radius:var(--r-md);background:${color}22;color:${textInk(color)};font-weight:600">${esc(typeLbl)}</span>
        </div>
        <div class="text-right" style="flex:none">
          <span class="text-xs font-semibold">${_bbomFmtB(i.total_cost)}</span>
          <span class="text-xs" style="color:var(--fuchsia-ink);font-weight:600;margin-left:4px">${(i.pct_of_branch_cogs||0).toFixed(2)}%</span>
        </div>
      </div>
      <div class="flex items-center" style="gap:8px;margin-top:4px;padding-left:30px">
        <div style="flex:1;height:7px;border-radius:var(--r-md);background:var(--bg-subtle);overflow:hidden">
          <div style="width:${barW.toFixed(1)}%;height:100%;background:${color};border-radius:var(--r-md)"></div>
        </div>
        <span class="text-slate-400" style="flex:none;font-size:10px">${qtyTxt} · ใช้ใน ${i.n_menus_using||0} เมนู${menus}</span>
      </div>
    </div>`;
  }).join('');
}

function _bbomRenderTypeChart(d) {
  const t = d.cost_by_ingredient_type || {};
  if (chartInstances['bbomType']) { chartInstances['bbomType'].destroy(); }
  const ctx = document.getElementById('bbomTypeChart').getContext('2d');
  const keys = Object.keys(t);
  if (!keys.length) {
    document.getElementById('bbomTypeLegend').innerHTML =
      '<p class="text-slate-400">ไม่มีข้อมูลวัตถุดิบ (สาขานี้ยังไม่มีสูตร BoM ครอบคลุม)</p>';
    return;
  }
  const data = keys.map(k => t[k]);
  const colors = keys.map(k => _BBOM_TYPE_COLORS[k] || _BBOM_TYPE_COLORS.other);
  const total = data.reduce((a, b) => a + b, 0);
  chartInstances['bbomType'] = new Chart(ctx, {
    type:'doughnut',
    data:{ labels:keys.map(k => _BBOM_TYPE_LABELS[k] || k),
           datasets:[{ data, backgroundColor:colors, borderWidth:2, borderColor:'#fff' }] },
    options:{
      responsive:true, maintainAspectRatio:false,
      cutout:'55%',
      plugins:{
        legend:{ display:false },
        datalabels:{ display:false },
        tooltip:{
          callbacks:{
            label: ctx => ` ${ctx.label}: ${_bbomFmtB(ctx.parsed)} (${total > 0 ? (ctx.parsed / total * 100).toFixed(1) : 0}%)`,
          },
        },
      },
    },
  });
  // Legend below
  document.getElementById('bbomTypeLegend').innerHTML = keys.map(k => {
    const v = t[k];
    const pct = total > 0 ? (v / total * 100) : 0;
    const c = _BBOM_TYPE_COLORS[k] || _BBOM_TYPE_COLORS.other;
    return `<div class="flex items-center justify-between">
      <span><span style="display:inline-block;width:10px;height:10px;background:${c};border-radius:2px;margin-right:6px"></span>${_BBOM_TYPE_LABELS[k] || k}</span>
      <span class="text-slate-500">${_bbomFmtB(v)} <span class="text-slate-400">(${pct.toFixed(1)}%)</span></span>
    </div>`;
  }).join('');
}

function _bbomRenderCoverage(d) {
  const t = d.totals || {};
  const wbr = t.menus_with_bom_recipe || 0;
  const tot = t.menus_in_period || 0;
  const cov = tot > 0 ? (wbr / tot * 100) : 0;
  const ingCovered = (d.top_ingredients_by_cost || []).reduce((s, i) => s + (i.total_cost || 0), 0);
  const cogs = t.cogs || 0;
  const ingCoveragePct = cogs > 0 ? (ingCovered / cogs * 100) : 0;
  document.getElementById('bbomCoverage').innerHTML =
    `BoM coverage: ${wbr.toLocaleString('th-TH')} / ${tot.toLocaleString('th-TH')} เมนูมีสูตร (${cov.toFixed(1)}%) · ` +
    `วัตถุดิบที่ระบุได้คิดเป็น ${ingCoveragePct.toFixed(1)}% ของต้นทุนสาขา`;
}

// ── Wide tables (UX review item 2, 2026-10-20) ─────────────────────────────────
// A table with 8 or more columns keeps its name column on screen while it scrolls sideways, shows
// its key figure in bold, and - where a table is listed below - folds its supporting columns behind
// "แสดงอีก N คอลัมน์" (each viewer's choice is remembered). Column numbers count from 0 in the
// header row; the table is left alone when a body row does not have one cell per header cell.
const _WIDE_TABLES = {
  pnlbTable:       { key: [6] },                    // P&L รายสาขา · กำไรดำเนินงาน
  benchmarkTable:  { key: [10], fold: [6, 7, 8, 9] },  // เทียบสาขา · คะแนน; its 4 weighted parts fold
  orderedTable:    { key: [5], fold: [7, 9], stick: [3] },   // เมนู · จำนวนขาย; the two running % fold; the menu name stays
  costItemTable:   { key: [9], fold: [5, 7] },        // ต้นทุน · อัตรากำไร; the two mix % fold
  profitBandTable: { key: [5], fold: [8] },           // กำไร · กำไร; BoM per bill folds
  staffTable:      { key: [9], fold: [7] },           // พนักงาน · % ส่วนลด
  pbLossTable:     { key: [5], fold: [8] },           // P&L ต่อบิล · ขาดทุน
};
function _wideFoldKey(id) { return 'oshinei_wide_open:' + id; }
function _wideIsOpen(id) { try { return localStorage.getItem(_wideFoldKey(id)) === '1'; } catch (e) { return false; } }
function wideToggle(id) {
  try { localStorage.setItem(_wideFoldKey(id), _wideIsOpen(id) ? '0' : '1'); } catch (e) { /* the toggle still works this visit */ }
  const t = document.getElementById(id); if (t) { t.dataset.wideSig = ''; _wideApply(t); }
}
function _wideApply(t) {
  const head = t.tHead && t.tHead.rows[t.tHead.rows.length - 1];
  if (!head || head.cells.length < 8) return;
  const n = head.cells.length, body = t.tBodies[0] ? [...t.tBodies[0].rows] : [];
  const sig = n + ':' + body.length + ':' + (body[0] ? body[0].cells.length : 0) + ':' + _wideIsOpen(t.id);
  const fresh = !!(body[0] && body[0].cells[0] && !body[0].cells[0].classList.contains('tbl-stick'));   // rows rebuilt (a sort, a reload)
  if (t.dataset.wideSig === sig && !fresh) return;       // nothing changed since the last pass
  t.dataset.wideSig = sig;
  t.classList.add('tbl-wide');
  const cfg = _WIDE_TABLES[t.id] || {};
  const regular = body.every(r => r.cells.length === n || r.cells.length === 1);   // 1 = an empty / loading row
  const rows = [...(t.tHead ? t.tHead.rows : [])].filter(r => r.cells.length === n).concat(body.filter(r => r.cells.length === n));
  // the name column: the first, or the second when the first is only a rank number
  const h0 = (head.cells[0].textContent || '').trim();
  const stick = cfg.stick || (/^(#|อันดับ|ลำดับ)?$/.test(h0) && n > 1 ? [0, 1] : [0]);
  let left = 0;
  stick.forEach(c => {
    rows.forEach(r => { const cell = r.cells[c]; if (cell) { cell.classList.add('tbl-stick'); cell.style.left = left + 'px'; } });
    left += head.cells[c].getBoundingClientRect().width || 0;
  });
  if (!regular) return;
  (cfg.key || []).forEach(c => rows.forEach(r => r.cells[c] && r.cells[c].classList.add('tbl-key')));
  const fold = cfg.fold || [];
  if (!fold.length) return;
  const open = _wideIsOpen(t.id);
  rows.forEach(r => fold.forEach(c => { if (r.cells[c]) r.cells[c].style.display = open ? '' : 'none'; }));
  let bar = document.querySelector('.tbl-fold-bar[data-for="' + t.id + '"]');
  const host = t.closest('.overflow-x-auto, [style*="overflow"]') || t;
  if (!bar) {
    bar = document.createElement('div');
    bar.className = 'tbl-fold-bar'; bar.dataset.for = t.id;
    host.parentElement.insertBefore(bar, host);
  }
  const firstText = el => { const n = [...el.childNodes].find(x => x.nodeType === 3 && x.textContent.trim()); return (n ? n.textContent : el.textContent || '').replace(/[▲▼↕]/g, '').trim(); };
  const names = fold.map(c => firstText(head.cells[c])).filter(Boolean);
  bar.innerHTML = `<button type="button" class="tbl-fold-btn" onclick="wideToggle('${t.id}')" aria-expanded="${open}">${open ? 'ซ่อนคอลัมน์ประกอบ' : `แสดงอีก ${fold.length} คอลัมน์`}</button>`
    + `<span class="tbl-fold-names">${esc(names.join(' · '))}</span>`;
}
function wideTablesScan(root) {
  (root || document).querySelectorAll('[id^="tab-"] table:not(.wt):not(.heatmap-table)').forEach(t => {
    if (t.offsetParent === null || t.closest('#mgtRptBody, #tab-supplyhome, #tab-bomcenter, #tab-orderhub, #tab-branchstock')) return;
    try { _wideApply(t); } catch (e) { /* a table it cannot read is left as it was */ }
  });
}
// re-run when a page renders or re-renders its rows (debounced; only the visible tab is scanned)
let _wideT = null;
new MutationObserver(() => {
  clearTimeout(_wideT);
  _wideT = setTimeout(() => { const tab = typeof activeTab !== 'undefined' && document.getElementById('tab-' + activeTab); if (tab) wideTablesScan(tab); }, 250);
}).observe(document.body, { childList: true, subtree: true });
