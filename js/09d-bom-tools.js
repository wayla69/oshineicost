// ─── 09d-bom-tools.js — BoM composition deep-dive · cost recalibration · monthly pack export dialog · business-type badge · BoM-cost mode switch · simulators hub ───
// Slice 4/5 of the former 09-deep-tools.js (split 2026-09-05 by contiguous range, so every
// statement keeps its load order). Shared global scope: index.html loads js/*.js in name order.

// ════════════════════════════════════════════════════════════════
// Phase 4 — Ingredient-level BoM Composition Deep-dive
// ════════════════════════════════════════════════════════════════
const BOM_TYPE_COLORS = {
  protein:  '#ef4444',
  carb:     '#f59e0b',
  veg:      '#22c55e',
  sauce:    '#a855f7',
  oil:      '#eab308',
  seasoning:'#64748b',
  other:    '#94a3b8',
};
const BOM_TYPE_LABEL_TH = {
  protein:'โปรตีน', carb:'คาร์บ', veg:'ผัก', sauce:'ซอส', oil:'น้ำมัน/ไขมัน', seasoning:'เครื่องปรุง', other:'อื่นๆ',
};
let _bomInited = false;
let _bomLastConsKey = '';

async function initBomDeepDive() {
  if (!_bomInited) {
    _bomInited = true;
    try {
      const res = await fetch(API + '/api/bom/sku-list');
      const data = await res.json();
      const sel = document.getElementById('bomSkuPicker');
      if (sel) {
        if (!data.skus || !data.skus.length) {
          sel.innerHTML = '<option value="">— ไม่มีข้อมูล BoM —</option>';
        } else {
          sel.innerHTML = data.skus.map(s =>
            `<option value="${esc(s.sku_code)}">${esc(s.sku_code)} · ${esc(s.description || '')} (${(s.total_cost||0).toFixed(2)})</option>`
          ).join('');
          sel.value = data.skus[0].sku_code;
          loadBomComposition();
        }
      }
    } catch (e) { console.error('initBomDeepDive sku-list failed:', e); }
  }
  // Always (re)load period consumption when subtab is opened — filters may have changed
  const fk = _filterKey();
  if (fk !== _bomLastConsKey) {
    _bomLastConsKey = fk;
    loadBomConsumption();
  }
}

async function loadBomComposition() {
  const sel = document.getElementById('bomSkuPicker');
  const sku = sel && sel.value;
  if (!sku) return;
  try {
    const res = await fetch(API + '/api/bom/composition?sku=' + encodeURIComponent(sku));
    if (!res.ok) {
      document.getElementById('bomCompHeader').innerHTML = '<span class="text-red-600">ไม่พบข้อมูล BoM สำหรับ ' + sku + '</span>';
      return;
    }
    const d = await res.json();
    renderBomComposition(d);
  } catch (e) {
    console.error('loadBomComposition failed:', e);
  }
}

function renderBomComposition(d) {
  document.getElementById('bomCompHeader').innerHTML =
    `<span class="font-semibold text-slate-700">${esc(d.sku_code)}</span> · ${esc(d.description || '')} · ` +
    `<span class="font-semibold text-orange-700">ต้นทุนรวม ${(d.total_cost||0).toFixed(2)}</span> · ${d.ingredients.length} วัตถุดิบ`;

  // Bar chart (descending cost)
  const sorted = [...d.ingredients].sort((a,b) => b.cost - a.cost);
  const barCanvas = document.getElementById('bomCompBar');
  if (chartInstances.bomCompBar) { try { chartInstances.bomCompBar.destroy(); } catch(e){} }
  chartInstances.bomCompBar = new Chart(barCanvas, {
    type: 'bar',
    data: {
      labels: sorted.map(x => (x.name || x.code).slice(0, 24)),
      datasets: [{
        label: 'ต้นทุน ()',
        data: sorted.map(x => x.cost),
        backgroundColor: sorted.map(x => BOM_TYPE_COLORS[x.type] || BOM_TYPE_COLORS.other),
        borderRadius: 4,
      }],
    },
    options: {
      indexAxis: 'y',
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        datalabels: { display: false },
        tooltip: { callbacks: { label: ctx => ''  + ctx.parsed.x.toFixed(2) + ' (' + (sorted[ctx.dataIndex].pct||0) + '%)' } },
      },
      scales: {
        x: { ticks: { color:'#94a3b8', font:{size:10}, callback: v => ''+v.toFixed(1) }, grid:{color:'#f1f5f9'} },
        y: { ticks: { color:'#334155', font:{size:10} }, grid:{display:false} },
      },
    },
  });

  // Donut by type
  const tEntries = Object.entries(d.by_type || {});
  const donut = document.getElementById('bomCompDonut');
  if (chartInstances.bomCompDonut) { try { chartInstances.bomCompDonut.destroy(); } catch(e){} }
  chartInstances.bomCompDonut = new Chart(donut, {
    type: 'doughnut',
    data: {
      labels: tEntries.map(([k]) => BOM_TYPE_LABEL_TH[k] || k),
      datasets: [{
        data: tEntries.map(([_,v]) => v),
        backgroundColor: tEntries.map(([k]) => BOM_TYPE_COLORS[k] || BOM_TYPE_COLORS.other),
        borderWidth: 2, borderColor: '#fff',
      }],
    },
    options: {
      responsive:true, maintainAspectRatio:false, cutout:'55%',
      plugins: {
        legend: { position:'right', labels:{color:'#334155', font:{size:11}, boxWidth:12} },
        datalabels: { color:'#fff', font:{weight:'bold', size:10}, formatter: v => (Number(v)||0).toFixed(1)+'%' },
        tooltip: { callbacks: { label: ctx => ctx.label + ': ' + (Number(ctx.parsed)||0).toFixed(1) + '%' } },
      },
    },
  });

  // Table
  const tbody = document.getElementById('bomCompTable');
  tbody.innerHTML = sorted.map((x,i) => `
    <tr class="border-b border-slate-100 hover:bg-orange-50/40">
      <td class="py-1.5 pr-2 text-slate-400">${i+1}</td>
      <td class="py-1.5 pr-2 font-mono text-[11px] text-slate-500">${x.code || '—'}</td>
      <td class="py-1.5 pr-2 text-slate-700">${x.name || '—'}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${(x.qty||0).toFixed(2)}</td>
      <td class="py-1.5 pr-2 text-slate-500">${x.unit || ''}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-semibold">${(x.cost||0).toFixed(2)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${(x.pct||0).toFixed(1)}%</td>
      <td class="py-1.5 pr-2"><span class="px-1.5 py-0.5 rounded text-[10px] text-white" style="background:${BOM_TYPE_COLORS[x.type]||BOM_TYPE_COLORS.other}">${BOM_TYPE_LABEL_TH[x.type]||x.type}</span></td>
    </tr>
  `).join('');
}

async function loadBomConsumption() {
  const params = new URLSearchParams();
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to',   getMonthTo());
  if (getBranch())    params.set('branch',     getBranch());
  params.set('limit', '20');
  if (document.getElementById('bomConsGroupFamily')?.checked) params.set('group_family', 'true');
  try {
    const res = await fetch(API + '/api/bom/consumption-analysis?' + params.toString());
    const d = await res.json();
    renderBomConsumption(d);
  } catch (e) {
    console.error('loadBomConsumption failed:', e);
  }
}

function renderBomConsumption(d) {
  // KPI chips
  const kpis = document.getElementById('bomConsKpis');
  kpis.innerHTML = `
    <div class="px-3 py-1.5 bg-cyan-50 rounded-lg border border-cyan-100 text-xs">
      <span class="text-slate-500">ต้นทุน BoM รวม:</span>
      <span class="font-semibold text-cyan-800 ml-1">${fmt(d.grand_total_cost || 0)}</span>
    </div>
    <div class="px-3 py-1.5 bg-slate-50 rounded-lg border border-slate-200 text-xs">
      <span class="text-slate-500">วัตถุดิบที่ใช้:</span>
      <span class="font-semibold text-slate-800 ml-1">${d.n_ingredients_total || 0} รายการ</span>
    </div>
    <div class="px-3 py-1.5 bg-slate-50 rounded-lg border border-slate-200 text-xs">
      <span class="text-slate-500">ช่วงเวลา:</span>
      <span class="font-semibold text-slate-800 ml-1">${d.month_from || '—'} → ${d.month_to || '—'}</span>
    </div>
  `;

  // Top-20 bar
  const top = (d.ingredients || []).slice(0, 20);
  const canvas = document.getElementById('bomConsTopBar');
  if (chartInstances.bomConsTopBar) { try { chartInstances.bomConsTopBar.destroy(); } catch(e){} }
  chartInstances.bomConsTopBar = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: top.map(x => (x.name || x.code).slice(0, 28)),
      datasets: [{
        label: 'ต้นทุนรวม ()',
        data: top.map(x => x.total_cost),
        backgroundColor: top.map(x => BOM_TYPE_COLORS[x.type] || BOM_TYPE_COLORS.other),
        borderRadius: 4,
      }],
    },
    options: {
      indexAxis: 'y',
      responsive:true, maintainAspectRatio:false,
      plugins: {
        legend: { display: false },
        datalabels: { display: false },
        tooltip: { callbacks: {
          label: ctx => {
            const r = top[ctx.dataIndex];
            return [''  + fmt(r.total_cost) + ' (' + r.pct + '%)',
                    'qty: ' + fmt(r.total_qty) + ' ' + (r.unit||''),
                    'in ' + r.n_menus + ' menus'];
          } } },
      },
      scales: {
        x: { ticks: { color:'#94a3b8', font:{size:10}, callback: v => ''+fmt(v) }, grid:{color:'#f1f5f9'} },
        y: { ticks: { color:'#334155', font:{size:10} }, grid:{display:false} },
      },
    },
  });

  // Type donut
  const tEntries = Object.entries(d.by_type || {});
  const donut = document.getElementById('bomConsTypeDonut');
  if (chartInstances.bomConsTypeDonut) { try { chartInstances.bomConsTypeDonut.destroy(); } catch(e){} }
  chartInstances.bomConsTypeDonut = new Chart(donut, {
    type: 'doughnut',
    data: {
      labels: tEntries.map(([k]) => BOM_TYPE_LABEL_TH[k] || k),
      datasets: [{
        data: tEntries.map(([_,v]) => v),
        backgroundColor: tEntries.map(([k]) => BOM_TYPE_COLORS[k] || BOM_TYPE_COLORS.other),
        borderWidth: 2, borderColor:'#fff',
      }],
    },
    options: {
      responsive:true, maintainAspectRatio:false, cutout:'55%',
      plugins: {
        legend: { position:'right', labels:{color:'#334155', font:{size:11}, boxWidth:12} },
        datalabels: { color:'#fff', font:{weight:'bold', size:10}, formatter: v => (Number(v)||0).toFixed(1)+'%' },
      },
    },
  });

  // Benchmark badges
  const bench = (d.benchmarks && d.benchmarks.ayce_buffet) || {};
  const status = d.benchmark_status || {};
  const benchLines = [
    `<p class="text-[11px] font-semibold text-slate-600">เทียบ benchmark: Top AYCE buffet (Japan/KR)</p>`,
  ];
  ['protein','carb','veg','sauce','oil','seasoning','other'].forEach(t => {
    const r = bench[t];
    const s = status[t];
    if (!r) return;
    const actual = s ? s.pct : 0;
    let color = 'bg-slate-200 text-slate-700';
    let icon = '—';
    if (s && s.status === 'in')    { color = 'bg-emerald-100 text-emerald-700 border border-emerald-200'; icon = '✓'; }
    else if (s && s.status === 'over')  { color = 'bg-amber-100 text-amber-700 border border-amber-200'; icon = '↑'; }
    else if (s && s.status === 'under') { color = 'bg-amber-100 text-amber-700 border border-amber-200'; icon = '↓'; }
    benchLines.push(`
      <div class="flex items-center justify-between text-[11px] px-2 py-1 rounded ${color}">
        <span class="font-medium">${icon} ${BOM_TYPE_LABEL_TH[t]||t}</span>
        <span class="tabular-nums">${actual.toFixed(1)}% <span class="text-slate-500">(target ${r[0]}–${r[1]}%)</span></span>
      </div>
    `);
  });
  benchLines.push(`<p class="text-[10px] text-slate-400 mt-1 italic">Fine-dining Japanese: protein 50–60%, carb 5–10%, veg 15–20%</p>`);
  document.getElementById('bomConsBench').innerHTML = benchLines.join('');
}

// Reload BoM consumption when global filters change
const _origLoadActiveTab = typeof loadActiveTab === 'function' ? loadActiveTab : null;
if (_origLoadActiveTab) {
  window.loadActiveTab = function() {
    _bomLastConsKey = '';   // invalidate so next switch into bomdeep reloads
    return _origLoadActiveTab.apply(this, arguments);
  };
}

// ════════════════════════════════════════════════════════════════════════════
// Phase 11 — Cost Recalibration Tool
// ════════════════════════════════════════════════════════════════════════════
let _recalibData = null;

async function loadRecalibration() {
  const target = parseFloat(document.getElementById('recalibTarget').value || '65');
  const limit  = parseInt(document.getElementById('recalibLimit').value || '50', 10);
  const mf = getMonthFrom(), mt = getMonthTo();
  const params = new URLSearchParams({ target_pct: target, limit });
  if (mf) params.set('month_from', mf);
  if (mt) params.set('month_to',   mt);
  const body = document.getElementById('recalibBody');
  body.innerHTML = '<tr><td colspan="9" class="py-6 text-center text-slate-400">Loading…</td></tr>';
  try {
    const data = await api(`${API}/api/cost/recalibration?${params}`);
    _recalibData = data;
    _renderRecalibSummary();
    _renderRecalibTable();
  } catch (e) {
    body.innerHTML = `<tr><td colspan="9" class="py-6 text-center text-red-500">โหลดล้มเหลว: ${e.message}</td></tr>`;
  }
}

function _renderRecalibSummary() {
  const d = _recalibData;
  if (!d) return;
  const cur = d.current_food_cost_pct, tgt = d.target_food_cost_pct;
  const inRange = cur >= 55 && cur <= 70;
  const curColor = inRange ? '#10b981' : cur > 70 ? '#ef4444' : '#f59e0b';
  document.getElementById('recalibSummary').innerHTML = `
    <div class="bg-white rounded-lg p-2.5 border border-amber-200 text-center">
      <p class="text-[10px] text-slate-500">รายได้ (period)</p>
      <p class="text-sm font-bold text-slate-800">${(d.current_revenue/1e6).toFixed(1)}M</p>
    </div>
    <div class="bg-white rounded-lg p-2.5 border border-amber-200 text-center">
      <p class="text-[10px] text-slate-500">COGS ปัจจุบัน</p>
      <p class="text-sm font-bold text-rose-600">${(d.current_cogs/1e6).toFixed(1)}M</p>
    </div>
    <div class="bg-white rounded-lg p-2.5 border border-amber-200 text-center">
      <p class="text-[10px] text-slate-500">Food cost %</p>
      <p class="text-base font-bold" style="color:${textInk(curColor)}">${cur.toFixed(1)}%</p>
    </div>
    <div class="bg-white rounded-lg p-2.5 border border-amber-200 text-center">
      <p class="text-[10px] text-slate-500">Target COGS</p>
      <p class="text-sm font-bold text-emerald-600">${(d.target_cogs/1e6).toFixed(1)}M @ ${tgt}%</p>
    </div>
    <div class="bg-white rounded-lg p-2.5 border border-amber-200 text-center">
      <p class="text-[10px] text-slate-500">ต้องลด COGS</p>
      <p class="text-sm font-bold text-amber-700">${(d.cogs_reduction_needed/1e6).toFixed(0)}M (×${d.scale_factor.toFixed(3)})</p>
    </div>
  `;
}

function _renderRecalibTable() {
  const d = _recalibData;
  if (!d || !d.skus.length) {
    document.getElementById('recalibBody').innerHTML = '<tr><td colspan="9" class="py-6 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
    return;
  }
  const fmtN = n => Number(n).toLocaleString('th-TH');
  const rows = d.skus.map(s => {
    const flagBadge = s.flag
      ? `<span class="px-1.5 py-0.5 rounded text-[10px] bg-rose-100 text-rose-700">${s.flag}</span>`
      : '';
    return `
      <tr class="border-b border-slate-100 hover:bg-amber-50">
        <td class="py-1.5 px-2 font-mono text-[11px] text-slate-700">${s.sku_code}</td>
        <td class="py-1.5 px-2 text-slate-700" title="${esc(s.description)}">${esc(s.description.length>40?s.description.slice(0,40)+'…':s.description)}</td>
        <td class="py-1.5 px-2 text-right text-slate-600">${fmtN(s.qty)}</td>
        <td class="py-1.5 px-2 text-right text-slate-500">${s.avg_sell_per_unit.toFixed(2)}</td>
        <td class="py-1.5 px-2 text-right font-semibold text-rose-600">${s.current_unit_cost.toFixed(2)}</td>
        <td class="py-1.5 px-2 text-right" style="background:#fef3c7">
          <input type="number" min="0" step="0.01"
            data-sku="${s.sku_code}"
            data-orig="${s.current_unit_cost}"
            data-suggested="${s.suggested_unit_cost}"
            placeholder="${s.suggested_unit_cost}"
            class="recalib-input w-20 text-right border border-amber-300 rounded px-1.5 py-0.5 font-semibold text-emerald-700">
        </td>
        <td class="py-1.5 px-2 text-right text-slate-500">${(s.total_cogs/1e6).toFixed(1)}M</td>
        <td class="py-1.5 px-2 text-right text-slate-500">${s.pct_of_total_cogs.toFixed(2)}%</td>
        <td class="py-1.5 px-2">${flagBadge}</td>
      </tr>`;
  }).join('');
  document.getElementById('recalibBody').innerHTML = rows;
}

function recalibApplyAllSuggested() {
  document.querySelectorAll('.recalib-input').forEach(inp => {
    inp.value = inp.dataset.suggested;
  });
  document.getElementById('recalibSaveStatus').textContent = 'พร้อม save → กด';
  document.getElementById('recalibSaveStatus').className = 'text-xs text-amber-700 font-semibold';
}

function recalibResetEdits() {
  document.querySelectorAll('.recalib-input').forEach(inp => { inp.value = ''; });
  document.getElementById('recalibSaveStatus').textContent = '';
}

async function recalibSave() {
  const updates = [];
  document.querySelectorAll('.recalib-input').forEach(inp => {
    const v = inp.value.trim();
    if (v === '') return;
    const num = parseFloat(v);
    if (isFinite(num) && num >= 0 && num.toFixed(2) !== parseFloat(inp.dataset.orig).toFixed(2)) {
      updates.push({ sku_code: inp.dataset.sku, unit_cost: num });
    }
  });
  const status = document.getElementById('recalibSaveStatus');
  if (!updates.length) {
    status.textContent = 'ไม่มีการแก้ไข';
    status.className = 'text-xs text-slate-500';
    return;
  }
  if (!confirm(`บันทึก ${updates.length} SKU? unit_cost เดิมจะถูกแทนที่`)) return;
  status.textContent = 'saving…';
  status.className = 'text-xs text-amber-700';
  try {
    const res = await api(`${API}/api/cost/bulk-update`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    });
    status.textContent = `บันทึก ${res.written} SKU` + (res.unknown_skus?.length ? ` · unknown: ${res.unknown_skus.join(',')}` : '');
    status.className = 'text-xs text-emerald-700 font-semibold';
    setTimeout(loadRecalibration, 700);   // refresh
  } catch (e) {
    status.textContent = `✗ ${e.message}`;
    status.className = 'text-xs text-red-600';
  }
}

// ════════════════════════════════════════════════════════════════
// MONTHLY REVIEW PACK — Export dialog (Excel / PDF)
// ════════════════════════════════════════════════════════════════
function openMonthlyPackDialog() {
  // Default month = current global filter, else today's month
  let defMonth = '';
  const gMt = document.getElementById('globalMonthTo') || document.getElementById('bbomMonthTo') || document.getElementById('gpbMonthTo');
  if (gMt && gMt.value) defMonth = gMt.value;
  if (!defMonth) {
    const now = new Date();
    defMonth = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}`;
  }
  let dlg = document.getElementById('monthlyPackDlg');
  if (dlg) dlg.remove();
  dlg = document.createElement('div');
  dlg.id = 'monthlyPackDlg';
  dlg.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,0.45);z-index:9999;display:flex;align-items:center;justify-content:center';
  dlg.innerHTML = `
    <div style="background:var(--bg-card,#fff);color:var(--text-primary,#0f172a);border-radius:var(--r-md);padding:24px;min-width:380px;max-width:92vw;box-shadow:0 20px 60px rgba(15,23,42,0.3)">
      <h3 style="font-size:18px;font-weight:700;margin:0 0 4px;color:#1F4E79">Export Monthly Review Pack</h3>
      <p style="font-size:12px;color:var(--text-muted);margin:0 0 16px">รายงานสรุปรายเดือน — ภาพรวม, P&L รายสาขา, Top SKU, วัตถุดิบ, alerts, action items</p>
      <label style="display:block;font-size:12px;color:var(--text-muted);margin-bottom:4px">เดือน (YYYY-MM)</label>
      <input id="mpackMonth" type="month" value="${defMonth}"
             style="width:100%;padding:8px 10px;border:1px solid var(--border,#cbd5e1);border-radius:var(--r-md);font-size:13px;background:var(--bg-input,#fff);color:inherit;margin-bottom:14px">
      <label style="display:block;font-size:12px;color:var(--text-muted);margin-bottom:6px">รูปแบบไฟล์</label>
      <div style="display:flex;gap:10px;margin-bottom:18px">
        <label style="flex:1;display:flex;align-items:center;gap:6px;padding:10px;border:1px solid var(--border,#cbd5e1);border-radius:var(--r-md);cursor:pointer">
          <input type="radio" name="mpackFmt" value="xlsx" checked> <span style="font-size:13px">Excel (.xlsx)</span>
        </label>
        <label style="flex:1;display:flex;align-items:center;gap:6px;padding:10px;border:1px solid var(--border,#cbd5e1);border-radius:var(--r-md);cursor:pointer">
          <input type="radio" name="mpackFmt" value="pdf"> <span style="font-size:13px">PDF</span>
        </label>
      </div>
      <div style="display:flex;justify-content:flex-end;gap:8px">
        <button onclick="document.getElementById('monthlyPackDlg').remove()"
                style="padding:8px 16px;border:1px solid var(--border,#cbd5e1);background:transparent;color:inherit;border-radius:var(--r-md);font-size:13px;cursor:pointer">ยกเลิก</button>
        <button onclick="downloadMonthlyPack()"
                style="padding:8px 18px;border:none;background:#1F4E79;color:white;border-radius:var(--r-md);font-size:13px;font-weight:600;cursor:pointer">ดาวน์โหลด</button>
      </div>
    </div>`;
  document.body.appendChild(dlg);
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.remove(); });
}

function downloadMonthlyPack() {
  const month = document.getElementById('mpackMonth').value;
  if (!month || month.length !== 7) {
    alert('กรุณาเลือกเดือน (YYYY-MM)');
    return;
  }
  const fmt = document.querySelector('input[name="mpackFmt"]:checked')?.value || 'xlsx';
  const url = `${API}/api/export/monthly-pack?month=${encodeURIComponent(month)}&format=${fmt}`;
  // Trigger a download via a temporary anchor — works cross-origin if server sets CD header.
  const a = document.createElement('a');
  a.href = url;
  a.download = `oshinei_monthly_pack_${month}.${fmt}`;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => a.remove(), 1000);
  document.getElementById('monthlyPackDlg')?.remove();
}

// ═════════════════════════════════════════════════════════════════════════════
// business_type badge / inline editor (Phase 17c)
// ═════════════════════════════════════════════════════════════════════════════
const BIZ_TYPE_META = {
  buffet:      { icon:'', bg:'bg-emerald-100', text:'text-emerald-700',  label:'buffet' },
  buffet_tier: { icon:'', bg:'bg-fuchsia-100', text:'text-fuchsia-700',  label:'buffet_tier' },
  alacarte:    { icon:'', bg:'bg-sky-100',     text:'text-sky-700',      label:'alacarte' },
  beverage:    { icon:'', bg:'bg-amber-100',   text:'text-amber-700',    label:'beverage' },
  takeaway:    { icon:'', bg:'bg-slate-200',   text:'text-slate-700',    label:'takeaway' },
  fee:         { icon:'', bg:'bg-zinc-200',    text:'text-zinc-700',     label:'fee' },
  other:       { icon:'○', bg:'bg-neutral-100', text:'text-neutral-600',  label:'other' },
};
const BIZ_TYPES = Object.keys(BIZ_TYPE_META);

function renderBizTypeCell(skuCode, bt) {
  const m = BIZ_TYPE_META[bt] || BIZ_TYPE_META.other;
  return `<select onchange="updateBizType('${skuCode}', this.value, this)"
            class="text-xs ${m.bg} ${m.text} font-medium border-0 rounded-full px-2 py-0.5 cursor-pointer focus:outline-none focus:ring-2 focus:ring-indigo-400">
    ${BIZ_TYPES.map(t => `<option value="${t}" ${t===bt?'selected':''}>${BIZ_TYPE_META[t].icon} ${BIZ_TYPE_META[t].label}</option>`).join('')}
  </select>`;
}

async function updateBizType(skuCode, newType, el) {
  try {
    const r = await fetch(`${API}/api/skus/${encodeURIComponent(skuCode)}/business-type`, {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({business_type:newType})
    });
    if (!r.ok) throw new Error(await r.text());
    const m = BIZ_TYPE_META[newType] || BIZ_TYPE_META.other;
    if (el) { el.className = `text-xs ${m.bg} ${m.text} font-medium border-0 rounded-full px-2 py-0.5 cursor-pointer focus:outline-none focus:ring-2 focus:ring-indigo-400`; }
  } catch(e) {
    alert('Failed to update: ' + e.message);
  }
}

// ═════════════════════════════════════════════════════════════════════════════
// BoM Cost Analysis — 4-mode tab (Phase 17d)
// ═════════════════════════════════════════════════════════════════════════════
let bcMode = 'overview';
let bcOverviewData = null;
let bcFoodMomData  = null;
let bcSimBase = null;
let bcSimOverrides = {};   // {sku_code: unit_cost}
let bcSimQty = {};         // {sku_code: new serving qty}
let bcSimDisabled = {};    // {sku_code: true}  → menu closed/discontinued
let bcCompareSelection = [];

function bcSetMode(mode) {
  bcMode = mode;
  // 'simulator' moved to the unified Simulators hub — no longer a mode here
  ['overview','branch','compare'].forEach(m => {
    document.getElementById('bc-mode-' + m)?.classList.toggle('active', m === mode);
    document.getElementById('bc-pane-' + m)?.classList.toggle('hidden', m !== mode);
  });
  loadBomCostTab();
}

function bcPeriod() {
  const mf = document.getElementById('bcMonthFrom')?.value || '';
  const mt = document.getElementById('bcMonthTo')?.value   || '';
  const gr = document.getElementById('bcGranularity')?.value || 'month';
  return { mf, mt, gr };
}

// ════════════════════════════════════════════════════════════════
// UNIFIED SIMULATORS HUB
// Combines BoM Cost, Cost Sensitivity, Tier Upgrade, and What-If into
// one tab. The three LIVE simulators are "adopted" — their existing DOM
// node is physically moved into a hub host so all their original JS keeps
// working untouched (no duplicate IDs). The previously-orphaned Tier
// Upgrade simulator (markup had been deleted; loader never called) is
// rebuilt fresh here. All four are driven by the shared top-bar filter.
// ════════════════════════════════════════════════════════════════
let _simhubMode = 'bomcost';
let _simhubTierBuilt = false;
function simhubAdopt(hostId, nodeId) {
  const host = document.getElementById(hostId);
  const node = document.getElementById(nodeId);
  if (host && node && node.parentNode !== host) host.appendChild(node);
  if (node) node.classList.remove('hidden');
  return node;
}
function simhubBuildTier() {
  if (_simhubTierBuilt) return;
  const host = document.getElementById('sh-host-tier');
  if (!host) return;
  _simhubTierBuilt = true;
  host.innerHTML = `
    <div class="card p-5">
      <div class="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div>
          <h2 class="text-base font-bold" style="color:var(--accent)">Tier Upgrade Simulator</h2>
          <p class="text-xs text-slate-400 mt-0.5">จำลองการดันลูกค้าขึ้น tier สูงกว่าแล้วดู GP ที่เพิ่ม — คู่ tier ใช้ตามเมนูที่ขายจริงในช่วงที่เลือก (ถึง ส.ค. 2026 = ฿590 → ฿799 · ตั้งแต่ 1 ก.ย. 2026 = ฿629 → ฿789)</p>
          <p id="simScenarioNote" class="text-[11px] text-indigo-500 mt-0.5"></p>
        </div>
        <label class="text-xs flex items-center gap-2 whitespace-nowrap">ดัน % ลูกค้าขึ้น tier:
          <input id="simShiftPct" type="number" min="0" max="100" value="10" step="5"
            onchange="loadUpgradeSimulator()" class="w-20 border border-slate-300 rounded-lg px-2 py-1">
          <button onclick="loadUpgradeSimulator()" class="px-3 py-1 bg-indigo-600 text-white rounded-lg font-medium">จำลอง</button>
        </label>
      </div>
      <div id="simKpis" class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4"></div>
      <div id="simTierTable" class="overflow-x-auto mb-4"></div>
      <div style="height:260px"><canvas id="chartSimCompare"></canvas></div>
    </div>`;
}
function simhubLoadMode(mode) {
  try {
    if (mode === 'bomcost') {
      simhubAdopt('sh-host-bomcost', 'bc-pane-simulator');
      // drive the BoM simulator off the shared top-bar month filter
      const bf = document.getElementById('bcMonthFrom'), bt = document.getElementById('bcMonthTo');
      if (bf) bf.value = getMonthFrom() || '';
      if (bt) bt.value = getMonthTo() || '';
      if (typeof bcLoadSimulator === 'function') bcLoadSimulator();
    } else if (mode === 'sensitivity') {
      simhubAdopt('sh-host-sensitivity', 'sensCard');
      _lastSimKey = null;   // force a fresh render in the hub context
      if (typeof loadCostSensitivity === 'function') loadCostSensitivity();
    } else if (mode === 'tier') {
      simhubBuildTier();
      if (typeof loadUpgradeSimulator === 'function') loadUpgradeSimulator();
    } else if (mode === 'whatif') {
      simhubAdopt('sh-host-whatif', 'opsub-whatif');
      if (typeof initWhatIf === 'function') initWhatIf();
    }
  } catch (e) { console.error('simhub mode load failed:', e); }
}
function simhubSetMode(mode) {
  _simhubMode = mode;
  ['bomcost','sensitivity','tier','whatif'].forEach(m => {
    document.getElementById('sh-mode-' + m)?.classList.toggle('active', m === mode);
    document.getElementById('sh-host-' + m)?.classList.toggle('hidden', m !== mode);
  });
  simhubLoadMode(mode);
}
function loadSimhub() { simhubSetMode(_simhubMode); }
