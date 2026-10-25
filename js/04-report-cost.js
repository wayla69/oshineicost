// ─── 04-report-cost.js — slice 4/9 of the former app.js (P2 structure refactor) ───
// Sequential load order matters: index.html loads 01→09; classic scripts
// share one global scope, so this equals the original single file.

// ════════════════════════════════════════════════════════════════
// TAB 4 — ANALYSIS REPORT
// ════════════════════════════════════════════════════════════════
let _rptLastMonth = '';
// §8 Cost Contribution — latest month for a branch (or all), with real MoM Δ vs prior month
async function renderReportCost(branch) {
  branch = branch || '';
  const month = _rptLastMonth;
  const body = document.getElementById('rptCostContribBody');
  const foot = document.getElementById('rptCostContribFoot');
  const note = document.getElementById('rptCostContribNote');
  const kpis = document.getElementById('rptCostContribKpis');
  if (!body) return;
  if (!month) { body.innerHTML = '<tr><td colspan="9" class="py-4 text-center text-slate-400 text-xs">ไม่มีเดือนข้อมูล</td></tr>'; if(foot)foot.innerHTML=''; return; }
  const [y,m] = month.split('-').map(Number);
  const prevM = m === 1 ? `${y-1}-12` : `${y}-${String(m-1).padStart(2,'0')}`;
  if (note) note.textContent = `เดือน ${fmtMonth(month)} · ${branch||'ทุกสาขา'} · MoM Δ เทียบ ${fmtMonth(prevM)} · % = สัดส่วนต้นทุนรวม · สะสม% = ขนาดรวม Top 60`;
  body.innerHTML = '<tr><td colspan="9" class="py-6 text-center text-slate-400 text-xs">กำลังโหลด…</td></tr>';
  if (foot) foot.innerHTML = '';
  // The shared cost ranking (/api/sales/top_ordered?sort=cogs) — the same list as
  // §4 above, the Menu tab and the Top-60 cost/head page; its total is the COGS of
  // the P&L. Until 2026-10-01 this section read /api/cost/analysis, which drops
  // buffet dishes, so §8 ranked à-la-carte only and disagreed with §4 on one page.
  const qpf = mm => { const p = new URLSearchParams(); if (branch) p.set('branch', branch); p.set('month', mm); p.set('sort', 'cogs'); p.set('limit', '100'); return p.toString(); };
  let cur, prev;
  try {
    [cur, prev] = await Promise.all([
      api(`${API}/api/sales/top_ordered?${qpf(month)}`),
      api(`${API}/api/sales/top_ordered?${qpf(prevM)}`).catch(() => ({ items: [] })),
    ]);
  } catch (e) { body.innerHTML = '<tr><td colspan="9" class="py-6 text-center text-red-500 text-xs">โหลดไม่ได้</td></tr>'; return; }
  const costItems = (cur.items || []).filter(r => r.cogs != null && r.cogs > 0);
  const priorMap  = new Map((prev.items || []).map(r => [r.sku_code, r]));
  const top60 = costItems.slice(0, 60);
  const totalCogsAll = cur.total_cogs || 0;
  const totalCogsTop = top60.reduce((s,r)=>s+(r.cogs||0),0);
  const totalQtyTop  = top60.reduce((s,r)=>s+(r.qty||0),0);

  kpis.innerHTML = [
    { label:'ต้นทุนขายรวม (เดือนนี้)', value:fmtN(Math.round(totalCogsAll)), color:'#dc2626' },
    { label:`Top 60 = ${totalCogsAll ? (totalCogsTop/totalCogsAll*100).toFixed(1) : '—'}% ของต้นทุน`, value:fmtN(Math.round(totalCogsTop)), color:'#f59e0b' },
    { label:'ลูกค้า (หัว)', value:fmtN(cur.total_covers||0), color:'#4a70b0' },
    { label:'ต้นทุน/หัว', value:cur.total_covers ? fmtN(Math.round(totalCogsAll/cur.total_covers)) : '—', color:'#64748b' },
  ].map(k=>`<div class="rounded-lg p-3 text-center" style="background:${k.color}0f;border:1px solid ${k.color}33">
    <p class="text-[10px] uppercase tracking-wide font-semibold" style="color:var(--text-muted)">${k.label}</p>
    <p class="text-lg font-bold" style="color:${textInk(k.color)}">${k.value}</p></div>`).join('');

  let cum = 0, totQtyDelta = 0, totCogsDelta = 0, anyPrior = false;
  body.innerHTML = top60.length ? top60.map((r,i) => {
    const prior = priorMap.get(r.sku_code);
    const qtyDelta  = prior ? (r.qty||0)-(prior.qty||0) : null;
    const cogsDelta = prior ? (r.cogs||0)-(prior.cogs||0) : null;
    if (prior) { anyPrior = true; totQtyDelta += qtyDelta; totCogsDelta += cogsDelta; }
    const shareAll = totalCogsAll ? ((r.cogs||0)/totalCogsAll*100) : 0;
    cum += shareAll;
    const dCell = (v, round) => v==null ? '<span class="text-slate-300">—</span>'
      : `<span style="color:${v>0?'var(--bad-ink)':v<0?'var(--good-ink)':'var(--text-muted)'}">${v>0?'+':''}${fmtN(round?Math.round(v):v)}</span>`;
    return `<tr class="border-t border-slate-100 ${i<5?'bg-red-50/30':''}">
      <td class="py-1.5 pr-3 text-slate-400">${i+1}</td>
      <td class="py-1.5 pr-3 font-medium text-slate-800">${esc(r.description||r.sku_code)}</td>
      <td class="py-1.5 pr-3 text-slate-400">${r.category||'—'}</td>
      <td class="py-1.5 pr-3 text-right">${fmtN(r.qty||0)}</td>
      <td class="py-1.5 pr-3 text-right font-semibold text-red-600">${fmtN(Math.round(r.cogs||0))}</td>
      <td class="py-1.5 pr-3 text-right text-slate-500">${shareAll.toFixed(1)}%</td>
      <td class="py-1.5 pr-3 text-right text-slate-400">${cum.toFixed(1)}%</td>
      <td class="py-1.5 pr-3 text-right">${dCell(qtyDelta,false)}</td>
      <td class="py-1.5 text-right">${dCell(cogsDelta,true)}</td>
    </tr>`;
  }).join('') : '<tr><td colspan="9" class="py-4 text-center text-slate-400 text-xs">ไม่มีข้อมูลต้นทุนสำหรับสาขา/เดือนนี้ — อัปโหลดที่ Settings → Cost Management</td></tr>';

  if (foot && top60.length) {
    const dTot = (v, round) => !anyPrior ? '<span class="text-slate-300">—</span>'
      : `<span style="color:${v>0?'var(--bad-ink)':v<0?'var(--good-ink)':'var(--text-muted)'}">${v>0?'+':''}${fmtN(round?Math.round(v):v)}</span>`;
    foot.innerHTML = `<tr class="border-t-2 border-slate-300 bg-slate-50">
      <td class="py-2 pr-3"></td>
      <td class="py-2 pr-3" colspan="2">รวม Top 60 เมนู</td>
      <td class="py-2 pr-3 text-right">${fmtN(totalQtyTop)}</td>
      <td class="py-2 pr-3 text-right text-red-700">${fmtN(Math.round(totalCogsTop))}</td>
      <td class="py-2 pr-3 text-right">${totalCogsAll?(totalCogsTop/totalCogsAll*100).toFixed(1):'—'}%</td>
      <td class="py-2 pr-3"></td>
      <td class="py-2 pr-3 text-right">${dTot(totQtyDelta,false)}</td>
      <td class="py-2 text-right">${dTot(totCogsDelta,true)}</td>
    </tr>`;
  }
}

// §9 Buffet price-tier block — reuses /api/reports/price-tier for the report's
// headline month (A = that month, B = auto previous same-length window). Hidden
// with a note when the tier fact tables don't cover the month.
async function renderReportTier(month) {
  const block = document.getElementById('rptTierBlock');
  const note = document.getElementById('rptTierNote');
  if (!block) return;
  if (!month) { block.innerHTML = '<p class="text-xs text-slate-400">ไม่มีเดือนข้อมูล</p>'; return; }
  block.innerHTML = '<p class="text-xs text-slate-400 py-2">กำลังโหลด…</p>';
  const [y, m] = month.split('-').map(Number);
  const lastDay = new Date(y, m, 0).getDate();
  const p = new URLSearchParams({ date_from: `${month}-01`, date_to: `${month}-${String(lastDay).padStart(2, '0')}`,
                                  scope: reportScope || 'all' });
  let d;
  try { d = await api(`${API}/api/reports/price-tier?${p}`); }
  catch (e) { block.innerHTML = '<p class="text-xs text-slate-400">โหลดข้อมูล Tier ไม่สำเร็จ</p>'; return; }
  if (!d.ready || d.error || !(d.tiers || []).length) {
    block.innerHTML = `<p class="text-xs text-slate-400">${esc(d.error || d.hint || 'ไม่มีข้อมูล Tier สำหรับเดือนนี้')}</p>`;
    return;
  }
  // PR-MB: the line keeps the period and the scope; what a Tier is sits behind the "?" of the heading (hlp-report-mb3)
  if (note) note.innerHTML = d.comparison_available === false
    ? `เดือน ${esc(fmtMonth(month))} · ${esc(d.scope_label || '')} · ไม่มีเดือนก่อนหน้าสำหรับเปรียบเทียบ ${uiQ('report:tier-nocmp', 'ไม่มีเดือนเปรียบเทียบ', '<p>ยังไม่มีเดือนก่อนหน้าที่ใช้เมนูเดียวกันสำหรับเปรียบเทียบ (เมนูเปลี่ยน 1 ก.ย. 2569) · Tier = ราคาบุฟเฟ่ต์ผู้ใหญ่บนบิล (ไม่รวม VAT)</p>')}`
    : `เดือน ${esc(fmtMonth(month))} (A) เทียบ ${esc(d.b_range[0])} → ${esc(d.b_range[1])} (B) · ${esc(d.scope_label || '')}`;
  const sgn = v => v == null ? '—' : `<span style="color:${v > 0 ? 'var(--bad-ink)' : v < 0 ? 'var(--good-ink)' : 'var(--text-muted)'}">${v > 0 ? '+' : ''}${v.toLocaleString('en-US', { maximumFractionDigits: 1 })}</span>`;
  const paxD = v => v == null ? '—' : `<span style="color:${v >= 0 ? 'var(--good-ink)' : 'var(--bad-ink)'}">${v >= 0 ? '▲' : '▼'} ${Math.abs(v).toFixed(1)}%</span>`;
  const rows = d.tiers.map(x => `<tr class="border-t border-slate-100">
    <td class="py-2 pr-4 font-semibold">${esc(x.label)}</td>
    <td class="py-2 pr-4 text-right">${fmtN(x.a.pax)}</td>
    <td class="py-2 pr-4 text-right">${paxD(x.d_pax_pct)}</td>
    <td class="py-2 pr-4 text-right">${x.a.share ?? '—'}%<span class="text-[10px] text-slate-400"> (${x.d_share_pp != null ? (x.d_share_pp > 0 ? '+' : '') + x.d_share_pp : '—'}pp)</span></td>
    <td class="py-2 pr-4 text-right">${x.a.rev_head != null ? '฿' + fmtN(x.a.rev_head) : '—'}</td>
    <td class="py-2 pr-4 text-right">${x.a.cogs_head != null ? '฿' + fmtN(x.a.cogs_head) : '—'}<span class="text-[10px]"> (${sgn(x.d_cogs_head)})</span></td>
    <td class="py-2 pr-4 text-right">${x.bom_avg_cost != null ? '฿' + fmtN(x.bom_avg_cost) : '—'}${x.bom_dish_count ? `<span class="text-[10px] text-slate-400"> (${x.bom_dish_count} จาน)</span>` : ''}</td>
    <td class="py-2 pr-4 text-right font-semibold">${x.a.gp_head != null ? '฿' + fmtN(x.a.gp_head) : '—'}</td>
    <td class="py-2 text-right">${x.a.food_pct ?? '—'}%</td></tr>`).join('');
  const mix = (d.mix_effect != null && Math.abs(d.mix_effect) >= 1)
    ? `<p class="text-xs mt-2 font-medium" style="color:${d.mix_effect < 0 ? 'var(--bad-ink)' : 'var(--good-ink)'}">${d.mix_effect < 0 ? 'ลูกค้าย้ายไป tier ถูกลง — ผลจาก mix กดรายได้/หัวลง ฿' + Math.abs(d.mix_effect).toFixed(2) : 'ลูกค้าย้ายขึ้น tier แพงขึ้น — ผลจาก mix เพิ่มรายได้/หัว ฿' + d.mix_effect.toFixed(2)}</p>` : '';
  const bullets = (d.insights || []).slice(0, 4).map(t => `<li>${esc(t)}</li>`).join('');
  block.innerHTML = `<table class="text-sm text-slate-700 w-full">
      <thead><tr class="text-left text-xs text-slate-500 uppercase tracking-wide border-b border-slate-200">
        <th class="py-2 pr-4">ระดับราคา</th><th class="py-2 pr-4 text-right">คนทาน (A)</th>
        <th class="py-2 pr-4 text-right">Δ คนทาน</th><th class="py-2 pr-4 text-right">สัดส่วน (Δpp)</th>
        <th class="py-2 pr-4 text-right">รายได้/หัว</th><th class="py-2 pr-4 text-right">ต้นทุน/หัว (Δ)</th>
        <th class="py-2 pr-4 text-right">BoM เฉลี่ย/จาน ${uiQ('report:tier-bom-avg', 'BoM เฉลี่ย/จาน', '<p>"BoM เฉลี่ย/จาน" = ต้นทุนสูตรอาหารเฉลี่ยของทุกจานที่ tier นั้นกินได้ (สะสม — รวมจานของ tier ที่ถูกกว่า) ไม่ใช่ยอดขายจริง มีเฉพาะเมนูใหม่ตั้งแต่ 1 ก.ย. 2569 · ดูเชิงลึก (Top-30 เมนูต้นทุนตาม tier, เทรนด์รายเดือน, ข้อเสนอแนะ) ได้ที่แท็บ วิเคราะห์วัตถุดิบ → วิเคราะห์ Tier ราคาขาย</p>')}</th>
        <th class="py-2 pr-4 text-right">GP/หัว</th><th class="py-2 text-right">ต้นทุนอาหาร %</th>
      </tr></thead><tbody>${rows}</tbody></table>
    ${mix}${bullets ? `<ul class="text-xs text-slate-600 mt-2 space-y-1" style="list-style:none;padding:0">${bullets}</ul>` : ''}`;
}

// Run async thunks with bounded concurrency, preserving input order. Used by the
// Analysis Report so it doesn't fire all ~16 queries at once (that put too many
// heavy queries on the small host simultaneously and OOM-killed it). Rejects if
// any thunk rejects — same failure semantics as Promise.all.
async function _poolAll(thunks, limit = 4) {
  const results = new Array(thunks.length);
  let next = 0;
  async function worker() {
    while (next < thunks.length) {
      const i = next++;
      results[i] = await thunks[i]();
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, thunks.length) }, worker));
  return results;
}

// Print / Export PDF. The Analysis Report fills its sections from ~15 async
// fetches (~2 s); calling window.print() immediately captured a blank report.
// When the report tab is active we force a full (re)load and await render before
// opening the print dialog, so the printed/saved PDF always has data.
//
// Two guards keep this robust on the small (512 MB) host:
//   • _printing re-entrancy lock — when the host is slow the dialog doesn't open
//     instantly, so users click repeatedly; without this each click re-fired
//     the whole ~17-query report load and the stacked batches thrashed/OOM'd the
//     host (a request storm seen in the logs). The lock collapses repeat clicks.
//   • Promise.race timeout — fetches have no per-request timeout, so one hung
//     request would block window.print() forever ("nothing happens"). After the
//     deadline we print whatever has rendered instead of hanging.
let _printing = false;
async function printReport() {
  if (_printing) return;                 // ignore repeat clicks while preparing
  _printing = true;
  try {
    if (typeof activeTab !== 'undefined' && activeTab === 'report') {
      showLoadingBar();
      try {
        _tabLastKey.report = null;
        await Promise.race([
          loadReport(),
          new Promise((_, rej) => setTimeout(() => rej(new Error('preload timeout')), 15000)),
        ]);
      }
      catch (e) { console.error('print preload failed/timed out — printing current view', e); }
      finally { hideLoadingBar(); }
      await new Promise(r => setTimeout(r, 80));   // let layout settle before snapshot
    }
    window.print();
  } finally { _printing = false; }
}

// Analysis Report scope: '' = overview (all branches), 'master' / 'franchise' =
// that branch group only. Report-local (independent of the global top-bar group
// filter) so the user can flip all/master/franchise from inside the report.
let reportScope = '';
function _rptScopeLabel(sc) {
  return sc === 'master' ? 'เฉพาะสาขา Master'
       : sc === 'franchise' ? 'เฉพาะแฟรนไชส์'
       : 'ภาพรวมทุกกลุ่ม (Master + Franchise)';
}
// Force `group` on a query string to the current report scope (overriding any
// global group), or strip it entirely for the overview.
function _rptScopeQp(qs) {
  const u = new URLSearchParams((qs || '').replace(/^\?/, ''));
  if (reportScope) u.set('group', reportScope); else u.delete('group');
  const s = u.toString();
  return s ? '?' + s : '';
}
function _rptScopeHighlight() {
  document.querySelectorAll('#rptScopeSel button').forEach(b => {
    const on = (b.dataset.scope || '') === reportScope;
    b.style.background = on ? 'rgba(255,255,255,0.95)' : 'transparent';
    b.style.color      = on ? '#1f3a6b' : '#fff';
  });
}
function setReportScope(sc) {
  reportScope = sc || '';
  _rptScopeHighlight();
  _tabLastKey.report = null;   // bust the per-tab cache so the report reloads
  loadReport();
}

async function loadReport() {
  // Include scope in the cache key so switching all/master/franchise reloads.
  const _fk = _filterKey() + '|scope=' + reportScope;
  if (_tabLastKey.report === _fk) return; _tabLastKey.report = _fk;
  _rptScopeHighlight();
  const branch = getBranch();
  const month  = getMonth();
  const monthFrom = getMonthFrom();
  const monthTo   = getMonthTo();

  // Header
  document.getElementById('rptPeriod').textContent = monthFrom ? (`${fmtMonth(monthFrom)} → ${fmtMonth(monthTo || monthFrom)}`) : (month ? fmtMonth(month) : 'All Months');
  document.getElementById('rptBranch').textContent =
    (branch ? 'Branch: ' + branch : 'All Branches') + ' · ' + _rptScopeLabel(reportScope);
  document.getElementById('rptDate').textContent   = new Date().toLocaleDateString('th-TH');

  // Sticky section nav — clickable chips that scroll to each §
  const topics = [
    'สรุปผู้บริหาร','รายได้และการเติบโต','อันดับสาขา',
    'ต้นทุน/หัว เมนู','พฤติกรรมลูกค้า','เวลาและการดำเนินงาน',
    'รายได้ตามวันและเวลา','ต้นทุนเมนูสูง','ราคาและส่วนลด','พยากรณ์และข้อเสนอแนะ',
  ];
  document.getElementById('rptNavInner').innerHTML = topics.map((t,i) =>
    `<a href="#rpt${i+1}" onclick="event.preventDefault();document.getElementById('rpt${i+1}')?.scrollIntoView({behavior:'smooth'})"
        class="px-2.5 py-1 rounded-lg hover:bg-indigo-50 text-slate-500 hover:text-indigo-700 transition-colors cursor-pointer">
        <span class="text-slate-300 font-mono">${i+1}</span> ${t}</a>`).join('');

  // Every report query is forced to the current scope (all / master / franchise)
  // via _rptScopeQp, independent of the global top-bar group filter.
  const qpStr = _rptScopeQp(qp());
  // Fetch all in parallel. PR-PP3: the cost card no longer sets the range's cost against ONE prior month (it reads
  // /api/period/cost on the headline month below); the prior-month cost fetch is gone. The prior month stays for §9's
  // discount composition.
  const _curTo   = monthTo   || monthFrom || '';
  let priorQpStr = qpStr;
  if (_curTo) {
    const [py, pm] = _curTo.split('-').map(Number);
    const pp = pm === 1 ? `${py-1}-12` : `${py}-${String(pm-1).padStart(2,'0')}`;
    const priorP = new URLSearchParams();
    if (branch) priorP.set('branch', branch);
    priorP.set('month_from', pp); priorP.set('month_to', pp);
    priorQpStr = _rptScopeQp('?' + priorP.toString());
  }
  const [
    summary, trend, skus, cats, persona,
    branches, yoy, costAnalysis,
    timeHourly, timeDow, discounts, anomalies, forecast, forecastBranches,
    costHead,
    // Bounded concurrency (≤4): firing all 16 at once put ~6 heavy queries on
    // the small host simultaneously and OOM-killed it. _poolAll keeps the order
    // and rejects if a non-caught fetch fails (same as Promise.all did).
  ] = await _poolAll([
    () => api(`${API}/api/summary${qpStr}`),
    () => api(`${API}/api/sales/monthly${qpStr}`),
    () => api(`${API}/api/sales/skus${_rptScopeQp(qp({limit:15}))}`),
    () => api(`${API}/api/sales/category${qpStr}`),
    () => api(`${API}/api/analysis/persona${qpStr}`),
    () => api(`${API}/api/analysis/branches${qpStr}`).catch(()=>[]),
    () => api(`${API}/api/analysis/yoy${qpStr}`).catch(()=>[]),
    () => api(`${API}/api/cost/analysis${qpStr}`).catch(()=>({items:[],summary:{}})),
    () => api(`${API}/api/analysis/hourly_open${qpStr}`).catch(()=>[]),
    () => api(`${API}/api/analysis/dow${qpStr}`).catch(()=>[]),
    () => api(`${API}/api/analysis/discounts${qpStr}`).catch(()=>[]),
    () => api(`${API}/api/analysis/anomalies${qpStr}`).catch(()=>[]),
    () => api(`${API}/api/forecast${_rptScopeQp(branch?'?branch='+encodeURIComponent(branch):'')}`).catch(()=>({available:false})),
    () => api(`${API}/api/forecast?mode=branches${reportScope?'&group='+reportScope:''}`).catch(()=>({available:false})),
    () => api(`${API}/api/sales/top_ordered${_rptScopeQp(qp({limit:60, sort:'cogs'}))}`).catch(()=>null),
  ], 4);

  // Full monthly history (branch/group only, no month-range) so MoM/YTD framing
  // works even when the user narrows the filter to a single month — otherwise
  // `trend` has 1 row and every MoM chip collapses to "—".
  const _fullP = new URLSearchParams();
  if (branch) _fullP.set('branch', branch);
  const _fullQp = _rptScopeQp(_fullP.toString() ? '?' + _fullP.toString() : '');
  const trendFull = await api(`${API}/api/sales/monthly${_fullQp}`).catch(()=>trend);
  // Per-branch monthly revenue (scoped, full history) → powers §3 ranking
  // movement, per-branch MoM, and YTD sparklines.
  const branchMonthlyRaw = await api(`${API}/api/gp/by-branch-month${_rptScopeQp('')}`).catch(()=>({rows:[]}));
  // Data window → the report must NOT headline a PARTIAL trailing month (e.g. data
  // through Jun-03) vs a full prior month (a misleading ~-90% MoM). Lead with the
  // last COMPLETE month; surface the partial month separately as MTD.
  const dataWin = await api(`${API}/api/meta/data-window`).catch(()=>({}));
  const completeMonth = dataWin.last_complete_month || null;   // e.g. 2026-05
  const partialMonth  = dataWin.partial_month || null;         // e.g. 2026-06 (3 days)

  // ── Monthly-framing helpers — lead with the latest COMPLETE month + MoM/YoY/YTD.
  const _rowFor = mm => (Array.isArray(trend) && trend.find(r => r.period_month === mm))
                     || (Array.isArray(trendFull) && trendFull.find(r => r.period_month === mm)) || null;
  const _prevMonthStr = mm => { if (!mm) return null; let [y,m]=mm.split('-').map(Number); m-=1; if(m<1){m=12;y-=1;} return `${y}-${String(m).padStart(2,'0')}`; };
  // Headline month — never lead with the in-progress (partial) month by default: a
  // partial month vs a full prior month reads as a false ~-100% MoM across every
  // section. Mirrors the Management Report's "anchor on the latest COMPLETE period"
  // guard. Rules:
  //   • user pinned ONLY the current month (from==to==partial) → honour it, flagged (MTD)
  //   • an explicit month that isn't the partial one          → use it
  //   • otherwise (no filter, or a range ending on the partial month, e.g. the
  //     default YTD range) → fall back to the last COMPLETE month
  const _filterHeadMonth = monthTo || monthFrom || null;
  const _explicitPartialOnly = partialMonth && monthFrom === partialMonth && monthTo === partialMonth;
  const _headMonth = _explicitPartialOnly ? partialMonth
    : (_filterHeadMonth && _filterHeadMonth !== partialMonth) ? _filterHeadMonth
    : (completeMonth || _filterHeadMonth || null);
  let mLast = _headMonth
    ? (_rowFor(_headMonth) || {period_month: _headMonth})
    : (trend[trend.length-1] || {});
  let mPrev = _rowFor(_prevMonthStr(mLast.period_month));
  if (!mPrev && trend.length >= 2 && !completeMonth) mPrev = trend[trend.length-2];
  // Partial (MTD) month, for a separate note.
  let mPartial = partialMonth ? _rowFor(partialMonth) : null;
  const _isMtdHeadline = mLast.period_month && mLast.period_month === partialMonth;
  // PR-PP2 · the one period pattern: the month in progress is to date (1 .. the last closed day) and compared with days
  // 1..n of last month and of the same month a year back; the year is to date the same way (/api/period/sales).
  const _rp = (kind, pick) => { const u = new URLSearchParams({ kind, pick }); if (branch) u.set('branch', branch);
    return api(`${API}/api/period/sales${_rptScopeQp('?' + u.toString())}`).catch(() => null); };
  const _ytdYear = (mLast.period_month || partialMonth || '').slice(0, 4);
  const [_rpMtd, _rpYtd] = await Promise.all([partialMonth ? _rp('month', partialMonth + '-01') : null, _ytdYear ? _rp('year', _ytdYear + '-01-01') : null]);
  const _ov = (row, v, pm) => v ? Object.assign({}, row || { period_month: pm }, { revenue: v.revenue, revenue_ex_vat: v.revenue, tx_count: v.bills, customer_count: v.customers }) : null;
  if (_rpMtd && _rpMtd.cur) {
    mPartial = _ov(mPartial, _rpMtd.cur, partialMonth);
    if (_isMtdHeadline) { mLast = _ov(mLast, _rpMtd.cur, partialMonth); mPrev = _ov(mPrev, _rpMtd.prev, _prevMonthStr(partialMonth)); }
  }
  const mLastLabel = mLast.period_month
    ? fmtMonth(mLast.period_month) + (_isMtdHeadline ? ' (MTD)' : '')
    : '—';
  const pctDelta = (cur, prev) => (prev && prev > 0) ? ((cur - prev) / prev * 100) : null;
  const momChip = p => p == null ? '<span class="text-slate-300">—</span>'
    : `<span style="color:${p>=0?'var(--good-ink)':'var(--bad-ink)'}">${p>=0?'▲':'▼'} ${Math.abs(p).toFixed(1)}%</span>`;
  const yoyForMonth = m => { const y = yoy.find(x => x.period_month === m); return (y && y.yoy_pct != null) ? y.yoy_pct : null; };
  const _cs0 = costAnalysis.summary || {};
  // PR-PP3 · the cost card on the one period pattern: the headline month (to date when it is the month in progress) against
  // the same days of the month before and of the month a year back, GP % in จุด (/api/period/cost)
  let _rc = null;
  if (mLast.period_month && typeof canSeeCost === 'function' && canSeeCost()) {
    const u = new URLSearchParams({ kind: 'month', pick: mLast.period_month + '-01' }); if (branch) u.set('branch', branch);
    _rc = await api(`${API}/api/period/cost${_rptScopeQp('?' + u.toString())}`).catch(() => null);
  }
  const _rcC = (_rc && _rc.cur) ? _rc.cur : null;
  const lastAov = mLast.tx_count ? Math.round(mLast.revenue / mLast.tx_count) : 0;
  const prevAov = (mPrev && mPrev.tx_count) ? Math.round(mPrev.revenue / mPrev.tx_count) : 0;

  // ── §1 Executive Summary — latest month vs previous month (+ YoY), not a period sum
  const exKpis = [
    { label:`รายได้ · ${mLastLabel}`, value:fmt(mLast.revenue||0), mom:pctDelta(mLast.revenue, mPrev?.revenue), yoy:yoyForMonth(mLast.period_month) },
    { label:`บิล · ${mLastLabel}`, value:fmtN(mLast.tx_count||0), mom:pctDelta(mLast.tx_count, mPrev?.tx_count) },
    { label:`ลูกค้า · ${mLastLabel}`, value:fmtN(mLast.customer_count||0), mom:pctDelta(mLast.customer_count, mPrev?.customer_count) },
    { label:`ยอดบิลเฉลี่ย · ${mLastLabel}`, value:fmtN(lastAov), mom:pctDelta(lastAov, prevAov) },
  ];
  const mPrevLabel = (mPrev && mPrev.period_month) ? fmtMonth(mPrev.period_month) : '—';
  document.getElementById('rptKpis').innerHTML = exKpis.map(k =>
    `<div class="p-4 bg-slate-50 rounded-lg">
      <p class="text-xs text-slate-500 uppercase tracking-wide mb-1">${k.label}</p>
      <p class="text-2xl font-bold text-indigo-700">${k.value}</p>
      <p class="text-[11px] mt-1 text-slate-500">MoM ${momChip(k.mom)} <span class="text-slate-400">vs ${mPrevLabel}</span></p>
    </div>`).join('');

  // ── KPI hero band — latest month at-a-glance (+ period margin)
  const heroCards = [
    { label:`รายได้ · ${mLastLabel}`, value:fmt(mLast.revenue||0), accent:'#4a70b0', mom:pctDelta(mLast.revenue, mPrev?.revenue) },
    _rcC ? { label:`Gross Margin · ${mLastLabel}`, value:_rcC.gp_pct!=null?_rcC.gp_pct.toFixed(1)+'%':'—', accent:'#059669',
             cmp: periodCmpHtml(_rcC.gp_pct, _rc.prev && _rc.prev.gp_pct, _rc.ly && _rc.ly.gp_pct, { unit: 'pp', range: _rc.period }) }
         : { label:'Gross Margin', value:_cs0.overall_margin_pct!=null?_cs0.overall_margin_pct.toFixed(1)+'%':'—', accent:'#059669' },
    { label:`บิล · ${mLastLabel}`, value:fmtN(mLast.tx_count||0), accent:'#0891b2', mom:pctDelta(mLast.tx_count, mPrev?.tx_count) },
    { label:`ลูกค้า · ${mLastLabel}`, value:fmtN(mLast.customer_count||0), accent:'#7a66b8', mom:pctDelta(mLast.customer_count, mPrev?.customer_count) },
    { label:`ยอดบิลเฉลี่ย · ${mLastLabel}`, value:fmtN(lastAov), accent:'#d97706', mom:pctDelta(lastAov, prevAov) },
  ];
  document.getElementById('rptHero').innerHTML = heroCards.map(h =>
    `<div class="card p-4" style="border-top:3px solid ${h.accent}">
      <p class="text-[11px] text-slate-500 uppercase tracking-wide mb-1">${h.label}</p>
      <p class="text-2xl font-bold" style="color:${textInk(h.accent)}">${h.value}</p>
      ${h.mom!=null?`<p class="text-[11px] mt-0.5">MoM ${momChip(h.mom)}</p>`:''}
      ${h.cmp ? `<div class="pkpi-cmp">${h.cmp}</div>` : ''}
    </div>`).join('');

  // Alerts
  const alerts = [];
  const periodTotalRev = trend.reduce((a,r)=>a+(r.revenue||0),0);
  alerts.push(`<b>${_isMtdHeadline ? 'เดือนที่ยังไม่จบ' : 'เดือนล่าสุดที่จบแล้ว'}:</b> ${mLastLabel}${(partialMonth && mPartial && !_isMtdHeadline) ? ` · <b>${fmtMonth(partialMonth)}</b> ยังไม่จบ (ข้อมูลถึงวันที่ ${dataWin.max_day}) เทียบช่วงวันเท่ากันใน §2` : ''} · ยอดรวมทั้งช่วง ${trend.length} เดือน = ${fmt(periodTotalRev)}`);
  const latestYoy = yoy[yoy.length-1];
  if (latestYoy && latestYoy.yoy_pct != null) {
    // Prefer the like-for-like figure when the raw one is structurally unfair
    // (branch roll-out / partial month) — see the guard in /api/analysis/yoy.
    alerts.push(
      (!latestYoy.comparable && latestYoy.same_store_pct != null)
        ? `<b>YoY ${latestYoy.period_month} (เทียบสาขาเดิม):</b> ${latestYoy.same_store_pct>=0?'+':''}${latestYoy.same_store_pct.toFixed(1)}% vs ${latestYoy.ly_period} · <span style="color:var(--amber-ink)">ตัวเลขดิบ ${latestYoy.yoy_pct>=0?'+':''}${latestYoy.yoy_pct.toFixed(1)}% รวมผลขยายสาขา (${latestYoy.ly_branches}→${latestYoy.cur_branches} สาขา)</span>`
        : `<b>YoY ${latestYoy.period_month}:</b> ${latestYoy.yoy_pct>=0?'+':''}${latestYoy.yoy_pct.toFixed(1)}% vs ${latestYoy.ly_period}`);
  }
  const cs = costAnalysis.summary || {};
  if (_rcC && _rcC.gp_pct != null) {
    const _d = _rc.change && _rc.change.prev ? _rc.change.prev.gp_pct : null;
    alerts.push(`<b>Gross margin ${mLastLabel}:</b> ${_rcC.gp_pct.toFixed(1)}%${_d != null ? ` (${_d >= 0 ? '+' : ''}${_d.toFixed(1)} จุด เทียบ ${esc(_rc.period.prev_label_th)})` : ''}`);
  } else if (cs.overall_margin_pct != null) {
    alerts.push(`<b>Gross margin:</b> ${cs.overall_margin_pct.toFixed(1)}% (on ${cs.costed_skus||0}/${cs.total_skus||0} costed SKUs)`);
  } else {
    alerts.push(`⚠ <b>No SKU costs uploaded.</b> Profit & margin analysis are unavailable.`);
  }
  if (anomalies.length) alerts.push(`⚠ <b>${anomalies.length} anomalies</b> detected in the last 90 days (see §10).`);
  document.getElementById('rptExecAlerts').innerHTML = alerts.map(a=>`<div class="px-3 py-2 bg-slate-50 rounded">${a}</div>`).join('');

  // ── §2 Revenue Performance & Growth — monthly + YTD movement (not a big sum)
  const trendWithYoy = trend.map(r => ({ ...r, yoy: yoy.find(y => y.period_month === r.period_month) }));
  const totalRevTrend = trend.reduce((a,r)=>a+(r.revenue||0),0);
  // YTD: current-year months up to the latest, vs the same months last year
  const curYear = (mLast.period_month || '').slice(0,4);
  const ytdMonths = trend.filter(r => (r.period_month||'').slice(0,4) === curYear);
  const ytdRev = ytdMonths.reduce((a,r)=>a+(r.revenue||0),0);
  // Only compare YTD vs last year when EVERY ytd month has a prior-year counterpart
  // with real revenue — otherwise a partial/empty base (data started mid-2025)
  // produces a meaningless 5-digit %.
  let priorYtd = 0, matched = 0;
  ytdMonths.forEach(r => {
    const [y,m] = (r.period_month||'').split('-');
    const lyRow = trend.find(x => x.period_month === `${(+y)-1}-${m}`);
    if (lyRow && (lyRow.revenue||0) > 0) { priorYtd += lyRow.revenue; matched++; }
  });
  const ytdComparable = ytdMonths.length > 0 && matched === ytdMonths.length && priorYtd > 0;
  let ytdGrowth = ytdComparable ? ((ytdRev - priorYtd)/priorYtd*100) : null;
  // PR-PP2: the year to date through the last closed day against 1 Jan .. the same date last year (never a part month
  // against a whole one); no year-ago data from 1 Jan -> no %.
  let ytdShown = ytdRev;
  if (_rpYtd && _rpYtd.cur) { ytdShown = _rpYtd.cur.revenue; ytdGrowth = _rpYtd.ly ? periodPct(_rpYtd.cur.revenue, _rpYtd.ly.revenue) : null; }
  // Sanity: an extreme swing means the base year was still ramping up — don't mislead.
  if (ytdGrowth != null && Math.abs(ytdGrowth) > 300) ytdGrowth = null;
  const bestMonth = trend.length ? trend.reduce((mx,r)=>(r.revenue||0)>(mx.revenue||0)?r:mx, trend[0]) : null;
  const avgMonth = trend.length ? totalRevTrend/trend.length : 0;
  const momLast = pctDelta(mLast.revenue, mPrev?.revenue);
  const yoyLast = yoyForMonth(mLast.period_month);
  document.getElementById('rptRevHeadline').innerHTML = `
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-1">
      ${(partialMonth && mPartial) ? `
      <div class="p-3 rounded-lg" style="background:var(--bg-subtle);border:1px solid var(--border)">
        <p class="text-xs" style="color:var(--text-muted)">${esc(_rpMtd && _rpMtd.period ? _rpMtd.period.label_th : fmtMonth(partialMonth))} <span style="color:var(--text-secondary)">(ถึงวันที่ปิดยอดล่าสุด)</span></p>
        <p class="text-lg font-bold" style="color:var(--text-primary)">${fmt(mPartial.revenue||0)}</p>
        ${_rpMtd && _rpMtd.cur ? periodCmpHtml(_rpMtd.cur.revenue, _rpMtd.prev && _rpMtd.prev.revenue, _rpMtd.ly && _rpMtd.ly.revenue, { range: _rpMtd.period }) : ''}
      </div>` : `
      <div class="p-3 rounded-lg bg-slate-50">
        <p class="text-xs text-slate-500">เดือนล่าสุด (${mLastLabel})</p>
        <p class="text-lg font-bold text-indigo-700">${fmt(mLast.revenue||0)}</p>
        <p class="text-[11px]">MoM ${momChip(momLast)} <span class="text-slate-400">vs ${mPrevLabel}</span></p>
      </div>`}
      <div class="p-3 rounded-lg bg-slate-50">
        <p class="text-xs text-slate-500">YTD ${curYear} (${ytdMonths.length} เดือน)</p>
        <p class="text-lg font-bold text-emerald-700">${fmt(ytdShown)}</p>
        <p class="text-[11px]">${ytdGrowth!=null?`vs YTD ${(+curYear)-1} ${momChip(ytdGrowth)}`:'<span class="text-slate-400">ไม่มีปีก่อนเทียบ</span>'}</p>
      </div>
      <div class="p-3 rounded-lg bg-slate-50">
        <p class="text-xs text-slate-500">เฉลี่ย/เดือน</p>
        <p class="text-lg font-bold text-slate-700">${fmt(avgMonth)}</p>
        <p class="text-[11px] text-slate-400">${trend.length} เดือนในช่วง</p>
      </div>
      <div class="p-3 rounded-lg bg-slate-50">
        <p class="text-xs text-slate-500">เดือนสูงสุด</p>
        <p class="text-lg font-bold text-amber-700">${bestMonth?fmtMonth(bestMonth.period_month):'—'}</p>
        <p class="text-[11px] text-slate-400">${bestMonth?fmt(bestMonth.revenue||0):''}</p>
      </div>
    </div>`;
  const _miniMoM = (cur, prev) => {
    if (!(prev > 0) || cur == null) return '';
    const p = (cur - prev) / prev * 100;
    return `<span class="block text-[10px]" style="color:${p>=0?'var(--good-ink)':'var(--bad-ink)'}">${p>=0?'▲':'▼'} ${Math.abs(p).toFixed(1)}%</span>`;
  };
  document.getElementById('rptTrendBody').innerHTML = trend.map((r,i) => {
    const prev = trend[i-1];
    const isPartial = r.period_month === partialMonth;   // suppress its misleading MoM
    const mom = (v, pv) => isPartial ? '' : _miniMoM(v, pv);
    const avgTicket = r.tx_count ? Math.round(r.revenue / r.tx_count) : 0;
    const prevAT = (prev && prev.tx_count) ? Math.round(prev.revenue / prev.tx_count) : 0;
    return `<tr class="border-t border-slate-100" style="${isPartial?'background:#fffbeb':''}">
      <td class="py-2 pr-6">${fmtMonth(r.period_month)}${isPartial?` <span class="text-[10px] text-amber-600">· บางส่วน ${dataWin.max_day||''} วัน</span>`:''}</td>
      <td class="py-2 pr-6 text-right font-semibold">${fmtN(r.revenue)}${mom(r.revenue, prev?.revenue)}</td>
      <td class="py-2 pr-6 text-right">${r.tx_count?fmtN(r.tx_count):'—'}${mom(r.tx_count, prev?.tx_count)}</td>
      <td class="py-2 pr-6 text-right">${r.customer_count?fmtN(r.customer_count):'—'}${mom(r.customer_count, prev?.customer_count)}</td>
      <td class="py-2 text-right">${avgTicket?fmtN(avgTicket):'—'}${isPartial?'':_miniMoM(avgTicket, prevAT)}</td>
    </tr>`;
  }).join('');

  // ── §3 Branch Performance Ranking — latest month, MoM, rank movement, YTD spark
  const _bm = {};   // branch -> { month: revenue }
  (branchMonthlyRaw.rows || []).forEach(r => {
    const mm = _bm[r.branch] || (_bm[r.branch] = {});
    (r.monthly || []).forEach(m => { mm[m.month] = (mm[m.month] || 0) + (m.revenue || 0); });
  });
  const _allMonthsB = Array.from(new Set(Object.values(_bm).flatMap(o => Object.keys(o)))).sort();
  // Rank by the last COMPLETE month (not the partial trailing one) so MoM is a
  // like-for-like full-month vs full-month comparison, not 3-days-vs-full-month.
  const _lastMo = (completeMonth && _allMonthsB.includes(completeMonth))
    ? completeMonth : (_allMonthsB[_allMonthsB.length - 1] || null);
  const _lastIdx = _allMonthsB.indexOf(_lastMo);
  const _prevMo = _lastIdx > 0 ? _allMonthsB[_lastIdx - 1] : null;
  const revOf = (br, mo) => (mo && _bm[br]) ? (_bm[br][mo] || 0) : 0;
  const rankByMonth = mo => {
    const map = {};
    Object.keys(_bm).map(br => ({ br, rev: revOf(br, mo) })).filter(x => x.rev > 0)
      .sort((a, b) => b.rev - a.rev).forEach((x, i) => map[x.br] = i + 1);
    return map;
  };
  const _curRank  = rankByMonth(_lastMo);
  const _prevRank = _prevMo ? rankByMonth(_prevMo) : {};
  const _spark = (vals, w = 84, h = 22) => {
    const v = (vals || []).filter(x => x != null);
    if (v.length < 2) return '<span class="text-slate-300 text-[10px]">—</span>';
    const mn = Math.min(...v), mx = Math.max(...v), rng = (mx - mn) || 1;
    const pts = v.map((x, i) => `${(i / (v.length - 1) * w).toFixed(1)},${(h - ((x - mn) / rng) * h).toFixed(1)}`).join(' ');
    const up = v[v.length - 1] >= v[0];
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="vertical-align:middle"><polyline points="${pts}" fill="none" stroke="${up?'#10b981':'#ef4444'}" stroke-width="1.5" stroke-linejoin="round"/></svg>`;
  };
  const _curYear = (_lastMo || '').slice(0, 4);
  const _ytdMonths = _allMonthsB.filter(m => m.slice(0, 4) === _curYear);
  const _curTotalRev = Object.keys(_bm).reduce((a, br) => a + revOf(br, _lastMo), 0) || 1;

  const branchesByLast = Object.keys(_bm).map(br => {
    const last = revOf(br, _lastMo), prev = revOf(br, _prevMo);
    const meta = branches.find(x => x.branch === br) || {};
    return {
      branch: br, last, prev,
      // A branch whose prior month is <5% of this month barely operated last
      // month (newly opened) — its MoM% would be a meaningless 5-digit number.
      mom: (prev > 0 && prev >= last * 0.05) ? (last - prev) / prev * 100 : null,
      ramp: (prev > 0 && prev < last * 0.05),
      dRank: (_curRank[br] && _prevRank[br]) ? (_prevRank[br] - _curRank[br]) : null,
      isNew: (_curRank[br] && !_prevRank[br]),
      share: last / _curTotalRev * 100,
      avg_ticket: meta.avg_ticket || 0,
      ytd: _ytdMonths.map(m => revOf(br, m)),
    };
  }).filter(b => b.last > 0).sort((a, b) => b.last - a.last);

  const _momChipS = p => p == null ? '<span class="text-slate-300">—</span>'
    : `<span style="color:${p>=0?'var(--good-ink)':'var(--bad-ink)'}">${p>=0?'▲':'▼'} ${Math.abs(p).toFixed(1)}%</span>`;
  const _rankMove = b => {
    if (b.isNew) return '<span style="color:var(--cyan-ink)"></span>';
    if (b.dRank == null || b.dRank === 0) return '<span class="text-slate-400">—</span>';
    return b.dRank > 0
      ? `<span style="color:var(--good-ink)">▲ ${b.dRank}</span>`
      : `<span style="color:var(--bad-ink)">▼ ${Math.abs(b.dRank)}</span>`;
  };
  const rankSub = document.getElementById('rptBranchRankSub');
  if (rankSub && _lastMo) rankSub.innerHTML = `จัดอันดับตามรายได้เดือน <b>${fmtMonth(_lastMo)}</b>${_prevMo?` · MoM/ขยับ เทียบ ${fmtMonth(_prevMo)}`:''} · YTD = แนวโน้มรายได้ปี ${_curYear}`;

  document.getElementById('rptBranchBody').innerHTML = branchesByLast.length
    ? branchesByLast.map((b, i) => {
        const medal = i===0?'1.':i===1?'2.':i===2?'3.':(i+1);
        return `<tr class="border-t border-slate-100">
          <td class="py-2 pr-4">${medal}</td>
          <td class="py-2 pr-4 font-medium">${esc(b.branch)}</td>
          <td class="py-2 pr-4 text-right font-semibold">${fmtN(b.last)}</td>
          <td class="py-2 pr-4 text-right">${b.ramp?'<span style="color:var(--cyan-ink)">พุ่ง</span>':_momChipS(b.mom)}</td>
          <td class="py-2 pr-4 text-center">${_rankMove(b)}</td>
          <td class="py-2 pr-4 text-right">${b.share.toFixed(1)}%</td>
          <td class="py-2 pr-4 text-right">${fmtN(b.avg_ticket||0)}</td>
          <td class="py-2 text-center">${_spark(b.ytd)}</td>
        </tr>`;
      }).join('')
    : [...branches].sort((a,b)=>(b.revenue||0)-(a.revenue||0)).map((b,i)=>`<tr class="border-t border-slate-100">
        <td class="py-2 pr-4">${i+1}</td><td class="py-2 pr-4 font-medium">${esc(b.branch)}</td>
        <td class="py-2 pr-4 text-right">${fmtN(b.revenue||0)}</td><td class="py-2 pr-4 text-right">—</td>
        <td class="py-2 pr-4 text-center">—</td><td class="py-2 pr-4 text-right">—</td>
        <td class="py-2 pr-4 text-right">${fmtN(b.avg_ticket||0)}</td><td class="py-2 text-center">—</td></tr>`).join('');

  // ── §3b Branch profiling — how each branch deviates from the network median
  (function renderBranchProfile() {
    const el = document.getElementById('rptBranchProfile');
    if (!el) return;
    const rows = (branches || []).filter(b => (b.tx_count||0) > 0).map(b => ({
      branch: b.branch,
      aov: b.avg_ticket || 0,
      disc: b.discount_rate || 0,
      cpb: b.tx_count ? (b.customer_count||0)/b.tx_count : 0,   // customers per bill
      rev: b.revenue || 0,
    }));
    if (rows.length < 3) { el.innerHTML = '<p class="text-xs text-slate-400">ข้อมูลไม่พอสำหรับเทียบรายสาขา</p>'; return; }
    const median = arr => { const s=[...arr].sort((x,y)=>x-y); const n=s.length; return n%2?s[(n-1)/2]:(s[n/2-1]+s[n/2])/2; };
    const med = { aov:median(rows.map(r=>r.aov)), disc:median(rows.map(r=>r.disc)), cpb:median(rows.map(r=>r.cpb)), rev:median(rows.map(r=>r.rev)) };
    const dev = (v,m) => m ? (v-m)/m*100 : 0;
    const CAP = 60; // % deviation that maxes out the bar track
    const devColor = (d, goodHigh) => {
      if (Math.abs(d) < 20) return '#cbd5e1';
      const good = goodHigh==null ? null : ((d>0) === goodHigh);
      return good==null ? '#64748b' : (good ? '#059669' : '#dc2626');
    };
    // diverging mini-bar centered on the network median, ±CAP% maps to the half-track
    const bar = (d, goodHigh) => {
      const flagged = Math.abs(d) >= 20;
      const color = devColor(d, goodHigh);
      const w = (Math.min(Math.abs(d), CAP) / CAP * 50).toFixed(1);
      const side = d > 0
        ? `left:50%;width:${w}%;border-radius:0 3px 3px 0`
        : `right:50%;width:${w}%;border-radius:3px 0 0 3px`;
      const label = flagged ? `${d>0?'+':'-'}${Math.abs(d).toFixed(0)}%` : '—';
      return `<div class="flex items-center gap-2 justify-end" title="${d>=0?'+':''}${d.toFixed(1)}% vs median">
          <span class="text-[11px] tabular-nums w-10 text-right" style="color:${textInk(color)}">${label}</span>
          <div class="relative w-14 h-2.5 bg-slate-100 rounded shrink-0">
            <div class="absolute top-0 left-1/2 w-px h-full bg-slate-300"></div>
            <div class="absolute top-0 h-full" style="${side};background:${color}"></div>
          </div>
        </div>`;
    };
    const highlight = (label, d, goodHigh) => {
      if (Math.abs(d) < 20) return null;
      const color = devColor(d, goodHigh);
      return `<span class="px-1.5 py-0.5 rounded text-[10px] whitespace-nowrap" style="background:${color}14;color:${textInk(color)}">${label} ${d>0?'▲':'▼'}${Math.abs(d).toFixed(0)}%</span>`;
    };
    const scored = rows.map(r => ({
      ...r, dAov: dev(r.aov,med.aov), dDisc: dev(r.disc,med.disc), dCpb: dev(r.cpb,med.cpb), dRev: dev(r.rev,med.rev),
    })).sort((a,b) => b.rev - a.rev);
    el.innerHTML = `<div class="overflow-x-auto">
      <table class="text-xs w-full">
        <thead><tr class="text-left text-slate-400 border-b border-slate-100">
          <th class="py-1.5 pr-3">สาขา</th>
          <th class="py-1.5 pr-3 text-right">ยอดเฉลี่ยต่อบิล</th>
          <th class="py-1.5 pr-3 text-right">ส่วนลด</th>
          <th class="py-1.5 pr-3 text-right">คน/บิล</th>
          <th class="py-1.5 pr-3 text-right">รายได้</th>
          <th class="py-1.5 pl-2">จุดเด่น</th>
        </tr></thead>
        <tbody>${scored.map(r => {
          const chips = [
            highlight('AOV', r.dAov, true),
            highlight('ส่วนลด', r.dDisc, false),
            highlight('คน/บิล', r.dCpb, null),
            highlight('รายได้', r.dRev, true),
          ].filter(Boolean);
          return `<tr class="border-t border-slate-50 hover:bg-slate-50">
            <td class="py-1.5 pr-3 font-medium text-slate-700 whitespace-nowrap">${esc(r.branch)}</td>
            <td class="py-1.5 pr-3">${bar(r.dAov, true)}</td>
            <td class="py-1.5 pr-3">${bar(r.dDisc, false)}</td>
            <td class="py-1.5 pr-3">${bar(r.dCpb, null)}</td>
            <td class="py-1.5 pr-3">${bar(r.dRev, true)}</td>
            <td class="py-1.5 pl-2"><div class="flex flex-wrap gap-1">${chips.length?chips.join(''):'<span class="text-[11px] text-slate-300">ใกล้เคียงค่ากลาง</span>'}</div></td>
          </tr>`;
        }).join('')}</tbody>
      </table></div>
      <p class="text-[11px] text-slate-400 mt-2">แถบ = ระยะห่างจากค่ากลางของเครือ ${uiQ('report:branch-median-bars', 'อ่านแถบเทียบค่ากลาง', '<p>แถบ = ระยะห่างจากค่ากลาง (median) ของเครือ ณ ค่ากลาง (เส้นแบ่งกลาง) · ●เขียว = ทิศทางดี · ●แดง = ทิศทางแย่ · เทา = ต่างกันแต่ไม่มีทิศทางดี/แย่ชัดเจน</p>')}</p>`;
  })();

  // ── §4 Top 60 เมนู — เทียบต้นทุนต่อหัว (reuses the Menu tab's renderer so the
  // numbers are identical; fed by /api/sales/top_ordered?sort=cogs with covers).
  _renderTop60CostHead(costHead, { body:'rpt4Body', foot:'rpt4Foot', momNote:'rpt4MomNote', note:'rpt4Note' });
  // costAnalysis food items (with menu-engineering quadrant) still power the §10
  // Action Items below — keep them available.
  const items = costAnalysis.items || [];

  // ── §5 Customer Behavior
  const o = persona.overall;
  document.getElementById('rptPersona').innerHTML = [
    { label:'Avg Party Size', value: (o.avg_party_size||0).toFixed(1)+' pax' },
    { label:'Avg Spend / Head', value: fmtN(o.avg_per_head||0) },
    { label:'ยอดบิลเฉลี่ย', value: fmtN(o.avg_ticket||0) },
    { label:'บิล', value: fmtN(o.tx_count||0) },
  ].map(k =>
    `<div class="text-center p-3 bg-slate-50 rounded-lg">
      <p class="text-xs text-slate-500 uppercase tracking-wide mb-1">${k.label}</p>
      <p class="text-xl font-bold text-slate-700">${k.value}</p>
    </div>`).join('');
  const fd = persona.food_drink_split || {};
  const fdTotal = (fd.food||0)+(fd.drink||0)+(fd.other||0);
  if (fdTotal > 0) {
    const fdPct = k => ((fd[k]||0)/fdTotal*100).toFixed(1);
    document.getElementById('rptPersonaNarrative').textContent = `Mix: ${fdPct('food')}% food, ${fdPct('drink')}% drink, ${fdPct('other')}% other.`;
  }

  // ── §5 rich — party-size distribution, spend/head consistency, sales structure
  (function renderPersonaRich() {
    const el = document.getElementById('rptPersonaRich'); if (!el) return;
    const ps = (persona.party_sizes || []).filter(p => (p.tx_count||0) > 0);
    if (!ps.length) { el.innerHTML = ''; return; }
    const totTx = ps.reduce((a,p)=>a+(p.tx_count||0),0) || (o.tx_count||1);
    const maxTx = Math.max(...ps.map(p=>p.tx_count||0), 1);
    const couples = ps.find(p=>p.size===2);
    const couplesPct = couples ? (couples.tx_count/totTx*100) : null;
    const phVals = ps.map(p=>p.avg_per_head||0).filter(Boolean);
    const phMin = phVals.length?Math.min(...phVals):0, phMax = phVals.length?Math.max(...phVals):0;
    const phSpread = phMax>0 ? ((phMax-phMin)/phMax*100) : 0;
    const bh = (persona.busy_hours||[]).filter(h=>(h.tx_count||0)>0);
    const peakH = bh.length ? bh.reduce((mx,h)=>h.tx_count>(mx?.tx_count||0)?h:mx,null) : null;
    const fdAbs = persona.food_drink_split || {};
    const fdTot = (fdAbs.food_abs||0)+(fdAbs.drink_abs||0)+(fdAbs.other_abs||0);
    const fdSeg = (lbl,val,color)=>{ const p = fdTot? val/fdTot*100:0; return p<0.5?'':`<div style="width:${p}%;background:${color};color:${inkOn(color)}" class="h-full flex items-center justify-center text-[10px] font-semibold" title="${lbl} ${p.toFixed(1)}%">${p>=8?p.toFixed(0)+'%':''}</div>`; };
    const PS_COLORS = ['#4a70b0','#0ea5e9','#10b981','#f59e0b','#ef4444','#a855f7','#14b8a6','#f97316'];
    const psSorted = [...ps].sort((a,b)=>(a.size||0)-(b.size||0));
    const rows = psSorted.map((p,i)=>{
      const sharePct = p.tx_count/totTx*100, w = p.tx_count/maxTx*100;
      const color = PS_COLORS[i % PS_COLORS.length];
      const headDelta = o.avg_per_head ? ((p.avg_per_head||0)-o.avg_per_head)/o.avg_per_head*100 : null;
      const headChip = headDelta==null ? '<span class="text-slate-300">—</span>'
        : `<span style="color:${headDelta>=0?'var(--good-ink)':'var(--bad-ink)'}">${headDelta>=0?'▲':'▼'}${Math.abs(headDelta).toFixed(0)}%</span>`;
      return `<tr class="border-t border-slate-50 hover:bg-slate-50">
        <td class="py-1.5 pr-3 font-medium text-slate-700 whitespace-nowrap"><span class="inline-block w-2 h-2 rounded-full mr-1.5 align-middle" style="background:${color}"></span>${p.label} คน</td>
        <td class="py-1.5 pr-3" style="width:32%"><div class="bg-slate-100 rounded-full h-3"><div style="width:${w.toFixed(0)}%;background:${color};height:100%;border-radius:9999px"></div></div></td>
        <td class="py-1.5 pr-3 text-right tabular-nums">${fmtN(p.tx_count)}</td>
        <td class="py-1.5 pr-3 text-right tabular-nums font-semibold">${sharePct.toFixed(1)}%</td>
        <td class="py-1.5 pr-3 text-right tabular-nums text-slate-500">${fmtN(p.avg_bill||0)}</td>
        <td class="py-1.5 pr-3 text-right tabular-nums text-slate-500">${fmtN(p.avg_per_head||0)}</td>
        <td class="py-1.5 text-right tabular-nums text-[11px]">${headChip}</td>
      </tr>`;
    }).join('');
    el.innerHTML = `
      <div class="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
        ${[
          ['คู่/2 คน = กลุ่มหลัก', couplesPct!=null?couplesPct.toFixed(0)+'%':'—', 'ของบิลทั้งหมดเป็นโต๊ะ 2 คน', '#4a70b0'],
          ['ยอด/หัวเกือบคงที่', phVals.length?`${fmtN(phMin)}–${fmtN(phMax)}`:'—', `ต่างแค่ ${phSpread.toFixed(0)}% ทุกขนาดกลุ่ม — บุฟเฟ่ต์คุมต่อหัวได้แม่น`, '#059669'],
          ['ชั่วโมงพีค', peakH?`${peakH.hour}:00 น.`:'—', peakH?`${fmtN(peakH.tx_count)} บิล/ชม.`:'', '#d97706'],
        ].map(([l,v,s,c])=>`<div class="p-3 rounded-lg border" style="border-color:${c}33;background:${c}0a">
          <p class="text-[11px] text-slate-500">${l}</p><p class="text-2xl font-extrabold" style="color:${textInk(c)}">${v}</p>
          <p class="text-[11px] text-slate-400 mt-0.5">${s}</p></div>`).join('')}
      </div>
      <p class="text-xs font-semibold text-slate-600 mb-1">การกระจายขนาดกลุ่มลูกค้า — สัดส่วนบิล · ยอด/บิล · ยอด/หัว</p>
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">
        <div class="lg:col-span-2 overflow-x-auto">
          <table class="text-xs w-full"><thead><tr class="text-left text-slate-400">
            <th class="pb-1 pr-3">กลุ่ม</th><th class="pb-1 pr-3">สัดส่วนบิล</th><th class="pb-1 pr-3 text-right">บิล</th>
            <th class="pb-1 pr-3 text-right">% บิล</th><th class="pb-1 pr-3 text-right">ยอด/บิล</th><th class="pb-1 pr-3 text-right">ยอด/หัว</th>
            <th class="pb-1 text-right">vs เฉลี่ย/หัว</th>
          </tr></thead><tbody>${rows}</tbody></table>
        </div>
        <div>
          <p class="text-[11px] text-slate-400 mb-1 text-center">สัดส่วนบิลตามขนาดกลุ่ม</p>
          <div style="position:relative;height:170px"><canvas id="rptPartyDonut"></canvas></div>
        </div>
      </div>
      <p class="text-xs font-semibold text-slate-600 mb-1">โครงสร้างยอดขาย</p>
      <div class="rounded-lg overflow-hidden border border-slate-200 flex" style="height:30px">
        ${fdSeg('บุฟเฟ่ต์ & อื่นๆ', fdAbs.other_abs||0, '#4a70b0')}
        ${fdSeg('Food à la carte', fdAbs.food_abs||0, '#10b981')}
        ${fdSeg('Drink', fdAbs.drink_abs||0, '#f59e0b')}
      </div>
      <p class="text-[11px] text-slate-400 mt-1">บุฟเฟ่ต์&อื่นๆ ${fdTot?((fdAbs.other_abs||0)/fdTot*100).toFixed(1):'—'}% · Food ${fdTot?((fdAbs.food_abs||0)/fdTot*100).toFixed(1):'—'}% · Drink ${fdTot?((fdAbs.drink_abs||0)/fdTot*100).toFixed(1):'—'}%</p>`;
    if (typeof Chart !== 'undefined') {
      const donutCanvas = document.getElementById('rptPartyDonut');
      if (chartInstances.rptPartyDonut) { try { chartInstances.rptPartyDonut.destroy(); } catch(e){} }
      if (donutCanvas) {
        chartInstances.rptPartyDonut = new Chart(donutCanvas, {
          type: 'doughnut',
          data: {
            labels: psSorted.map(p => p.label + ' คน'),
            datasets: [{
              data: psSorted.map(p => p.tx_count||0),
              backgroundColor: psSorted.map((_,i) => PS_COLORS[i % PS_COLORS.length]),
              borderWidth: 2, borderColor: '#fff',
            }],
          },
          options: {
            responsive: true, maintainAspectRatio: false, cutout: '55%',
            plugins: {
              legend: { position: 'bottom', labels: { color:'#334155', font:{size:10}, boxWidth:10, padding:8 } },
              datalabels: { color:'#fff', font:{weight:'bold', size:10}, formatter: (v,ctx) => { const tot = ctx.chart.data.datasets[0].data.reduce((a,b)=>a+b,0); return tot ? (v/tot*100).toFixed(0)+'%' : ''; } },
              tooltip: { callbacks: { label: ctx => `${ctx.label}: ${fmtN(ctx.parsed)} บิล` } },
            },
          },
        });
        // report data loads async after switchTab's own resize sweep already ran,
        // so this chart can be born inside a not-yet-laid-out panel — nudge once.
        setTimeout(() => { try { chartInstances.rptPartyDonut.resize(); } catch(e){} }, 60);
      }
    }
  })();

  // ── §6 Time & Operations
  const openHours = timeHourly.filter(h => h.is_open !== false);
  const totalHourRev = openHours.reduce((a,h)=>a+(h.revenue||0),0);
  const peak = openHours.reduce((mx,h)=>h.revenue>(mx?.revenue||0)?h:mx,null);
  const quiet = openHours.filter(h=>h.tx_count>0).reduce((mn,h)=>h.revenue<(mn?.revenue||Infinity)?h:mn,null);
  document.getElementById('rptTimeKpis').innerHTML = [
    { label:'ชั่วโมงเปิดทำการ', value: openHours.length+'/24', sub:'configured' },
    { label:'Peak Hour', value: peak?`${peak.hour}:00`:'—', sub: peak?`${fmtN(peak.revenue)}`:'' },
    { label:'Quietest Hour', value: quiet?`${quiet.hour}:00`:'—', sub: quiet?`${fmtN(quiet.revenue)}`:'' },
    { label:'Tx per Open Hr', value: openHours.length?fmtN(Math.round(openHours.reduce((a,h)=>a+h.tx_count,0)/openHours.length)):'—', sub:'avg' },
  ].map(k =>
    `<div class="text-center p-3 bg-slate-50 rounded-lg">
      <p class="text-xs text-slate-500 uppercase tracking-wide mb-1">${k.label}</p>
      <p class="text-xl font-bold text-indigo-700">${k.value}</p>
      <p class="text-xs text-slate-400">${k.sub}</p>
    </div>`).join('');
  if (!peak) {
    document.getElementById('rptTimeNarrative').innerHTML = 'No transaction data in selected period.';
  } else {
    // Weekday vs weekend (avg/day)
    const _avgOf = ds => { const a = ds.map(d=>(timeDow||[]).find(r=>r.dow===d)).filter(Boolean); return a.length ? a.reduce((s,r)=>s+(r.avg_revenue||0),0)/a.length : 0; };
    const wkdayAvg = _avgOf([1,2,3,4,5]), wkendAvg = _avgOf([6,0]);
    const wkLift = wkdayAvg > 0 ? (wkendAvg/wkdayAvg-1)*100 : null;
    // Top-3 hour concentration
    const hSorted = [...openHours].sort((a,b)=>(b.revenue||0)-(a.revenue||0));
    const top3Share = totalHourRev ? hSorted.slice(0,3).reduce((a,h)=>a+(h.revenue||0),0)/totalHourRev*100 : 0;
    // Dayparts (within open range)
    const dpDefs = [
      { label:'เช้า–เที่ยง', lo:6,  hi:14 },
      { label:'บ่าย',        lo:14, hi:17 },
      { label:'เย็น',        lo:17, hi:20 },
      { label:'ค่ำ',         lo:20, hi:24 },
    ];
    const dpRows = dpDefs.map(dp => {
      const hs = openHours.filter(h => h.hour >= dp.lo && h.hour < dp.hi);
      const rev = hs.reduce((a,h)=>a+(h.revenue||0),0);
      const tx  = hs.reduce((a,h)=>a+(h.tx_count||0),0);
      return { ...dp, rev, tx, share: totalHourRev ? rev/totalHourRev*100 : 0 };
    }).filter(d => d.rev > 0).sort((a,b)=>b.rev-a.rev);
    document.getElementById('rptTimeNarrative').innerHTML = `
      <p class="mb-3">ชั่วโมงพีค <b>${peak.hour}:00</b> ทำ <b>${(peak.revenue/totalHourRev*100).toFixed(1)}%</b> ของรายได้ในวัน · <b>3 ชั่วโมงแรก</b>รวมกัน <b>${top3Share.toFixed(1)}%</b> (ความกระจุกตัว)</p>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <p class="text-xs font-semibold text-slate-600 mb-2">วันธรรมดา vs สุดสัปดาห์ (เฉลี่ย/วัน)</p>
          <div class="flex gap-3">
            <div class="flex-1 p-3 rounded-lg bg-slate-50 text-center"><p class="text-xs text-slate-500">จ–ศ</p><p class="text-lg font-bold text-slate-700">${fmtN(wkdayAvg)}</p></div>
            <div class="flex-1 p-3 rounded-lg bg-cyan-50 text-center"><p class="text-xs text-slate-500">ส–อา</p><p class="text-lg font-bold text-cyan-700">${fmtN(wkendAvg)}</p><p class="text-[11px]">${wkLift!=null?momChip(wkLift)+' vs วันธรรมดา':''}</p></div>
          </div>
        </div>
        <div>
          <p class="text-xs font-semibold text-slate-600 mb-2">สัดส่วนรายได้ตามช่วงเวลา (Daypart)</p>
          <table class="text-xs w-full">
            <tbody>${dpRows.map(d=>`<tr class="border-t border-slate-50">
              <td class="py-1 font-medium">${d.label}</td>
              <td class="py-1"><div class="bg-slate-100 rounded-full h-2"><div style="width:${d.share.toFixed(0)}%;background:var(--accent);height:100%;border-radius:9999px"></div></div></td>
              <td class="py-1 text-right text-slate-500 w-12">${d.share.toFixed(0)}%</td>
              <td class="py-1 text-right text-slate-400 w-16">${fmtN(d.tx)} บิล</td>
            </tr>`).join('')}</tbody>
          </table>
        </div>
      </div>
      <p class="text-[11px] text-slate-400 mt-2">ตั้งค่าชั่วโมงเปิดทำการที่ Settings → ชั่วโมงเปิดทำการ เพื่อความแม่นยำ</p>`;
  }

  // ── §7 รายได้ตามวันและเวลา (share% = avg/day ÷ Σ avg/day — consistent units)
  const openHrsRpt = (timeHourly || []).filter(h => h.is_open !== false);
  const hrAvg = h => h.avg_daily_revenue ?? h.revenue ?? 0;
  const totalHrAvg = openHrsRpt.reduce((a,h)=>a+hrAvg(h),0) || 1;
  const peakRpt = openHrsRpt.length ? openHrsRpt.reduce((mx,h)=>hrAvg(h)>hrAvg(mx)?h:mx, openHrsRpt[0]) : null;
  const DOW_LABELS_RPT = ['อา','จ','อ','พ','พฤ','ศ','ส'];
  const dowOrder = [1,2,3,4,5,6,0];
  const dowSorted = dowOrder.map(d => (timeDow||[]).find(r=>r.dow===d) || {dow:d,revenue:0,avg_revenue:0,day_count:0});
  const peakDow = dowSorted.length ? dowSorted.reduce((mx,d)=>(d.avg_revenue||0)>(mx.avg_revenue||0)?d:mx, dowSorted[0]) : null;
  const dowTotal = dowSorted.reduce((a,d)=>a+(d.avg_revenue||0),0) || 1;
  const hrsByRev = [...openHrsRpt].sort((a,b)=>hrAvg(b)-hrAvg(a));
  let _hrCum = 0;
  document.getElementById('rptDayTimeBlock').innerHTML = `
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
      ${peakRpt ? `<div class="p-3 rounded-lg bg-indigo-50 text-center"><p class="text-xs text-slate-500">Peak Hour</p><p class="text-2xl font-bold text-indigo-700">${peakRpt.hour}:00</p><p class="text-xs text-slate-400">${fmtN(hrAvg(peakRpt))}/วัน · ${(hrAvg(peakRpt)/totalHrAvg*100).toFixed(1)}% ของวัน</p></div>` : ''}
      ${peakDow ? `<div class="p-3 rounded-lg bg-cyan-50 text-center"><p class="text-xs text-slate-500">Peak Day</p><p class="text-2xl font-bold text-cyan-700">${DOW_LABELS_RPT[peakDow.dow]||'—'}</p><p class="text-xs text-slate-400">${fmtN(peakDow.avg_revenue||0)}/วัน · ${((peakDow.avg_revenue||0)/dowTotal*100).toFixed(1)}% ของสัปดาห์</p></div>` : ''}
      <div class="p-3 rounded-lg bg-emerald-50 text-center"><p class="text-xs text-slate-500">Open Hours</p><p class="text-2xl font-bold text-emerald-700">${openHrsRpt.length}</p><p class="text-xs text-slate-400">ชั่วโมงต่อวัน</p></div>
      <div class="p-3 rounded-lg bg-amber-50 text-center"><p class="text-xs text-slate-500">Revenue/Open Hr</p><p class="text-xl font-bold text-amber-700">${openHrsRpt.length?fmtN(Math.round(totalHrAvg/openHrsRpt.length)):'—'}</p><p class="text-xs text-slate-400">avg/day across hours</p></div>
    </div>
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div>
        <p class="text-xs font-semibold text-slate-600 mb-2">Revenue by Hour (avg/day · เรียงมาก→น้อย)</p>
        <table class="text-xs w-full">
          <thead><tr class="text-slate-400 border-b border-slate-100"><th class="py-1 text-left">ชั่วโมง</th><th class="py-1 text-right">เฉลี่ย/วัน</th><th class="py-1 text-right">สัดส่วน</th><th class="py-1 text-right">สะสม</th></tr></thead>
          <tbody>${hrsByRev.map(h=>{ const sh = hrAvg(h)/totalHrAvg*100; _hrCum += sh; return `
            <tr class="border-t border-slate-50">
              <td class="py-1 font-mono">${h.hour}:00</td>
              <td class="py-1 text-right font-medium">${fmtN(hrAvg(h))}</td>
              <td class="py-1 text-right text-slate-500">${sh.toFixed(1)}%</td>
              <td class="py-1 text-right text-slate-400">${_hrCum.toFixed(1)}%</td>
            </tr>`; }).join('')}</tbody>
        </table>
      </div>
      <div>
        <p class="text-xs font-semibold text-slate-600 mb-2">Revenue by Day of Week (avg/day)</p>
        <div style="position:relative;height:140px" class="mb-3"><canvas id="rptDowChart"></canvas></div>
        <table class="text-xs w-full">
          <thead><tr class="text-slate-400 border-b border-slate-100"><th class="py-1 text-left">วัน</th><th class="py-1 text-right">เฉลี่ย/วัน</th><th class="py-1 text-right">สัดส่วน</th><th class="py-1 text-right">vs เฉลี่ยสัปดาห์</th></tr></thead>
          <tbody>${[...dowSorted].sort((a,b)=>(b.avg_revenue||0)-(a.avg_revenue||0)).map(d=>{
            const avgOfWk = dowTotal/7;
            const dd = avgOfWk ? ((d.avg_revenue||0)-avgOfWk)/avgOfWk*100 : 0;
            return `
            <tr class="border-t border-slate-50 ${d.dow===peakDow?.dow?'bg-cyan-50':''}">
              <td class="py-1 font-medium">${d.dow===peakDow?.dow?'':''}${DOW_LABELS_RPT[d.dow]||'—'}</td>
              <td class="py-1 text-right font-medium">${fmtN(d.avg_revenue||0)}</td>
              <td class="py-1 text-right text-slate-500">${((d.avg_revenue||0)/dowTotal*100).toFixed(1)}%</td>
              <td class="py-1 text-right" style="color:${dd>=0?'var(--good-ink)':'var(--bad-ink)'}">${dd>=0?'▲':'▼'}${Math.abs(dd).toFixed(0)}%</td>
            </tr>`; }).join('')}</tbody>
        </table>
      </div>
    </div>`;
  if (typeof Chart !== 'undefined') {
    const dowCanvas = document.getElementById('rptDowChart');
    if (chartInstances.rptDowChart) { try { chartInstances.rptDowChart.destroy(); } catch(e){} }
    if (dowCanvas) {
      chartInstances.rptDowChart = new Chart(dowCanvas, {
        type: 'bar',
        data: {
          labels: dowSorted.map(d => DOW_LABELS_RPT[d.dow] || '—'),
          datasets: [{
            data: dowSorted.map(d => d.avg_revenue || 0),
            backgroundColor: dowSorted.map(d => d.dow === peakDow?.dow ? '#0891b2' : '#c9d5e8'),
            borderRadius: 4,
          }],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            datalabels: { display: false },
            tooltip: { callbacks: { label: ctx => fmtN(ctx.parsed.y) + '/วัน' } },
          },
          scales: {
            x: { ticks: { color:'#64748b', font:{size:11} }, grid: { display:false } },
            y: { ticks: { color:'#94a3b8', font:{size:10}, callback: v => fmtN(v) }, grid: { color:'#f1f5f9' } },
          },
        },
      });
      setTimeout(() => { try { chartInstances.rptDowChart.resize(); } catch(e){} }, 60);
    }
  }

  // ── §8 Cost Contribution — latest month, per-branch selectable, real MoM Δ
  _rptLastMonth = mLast.period_month || getMonthTo() || getMonthFrom() || '';
  // populate branch selector once (preserve current selection)
  const csel = document.getElementById('rptCostBranch');
  if (csel) {
    const want = getBranch() || '';
    const opts = ['<option value="">ทุกสาขา</option>']
      .concat([...branches].sort((a,b)=>(b.revenue||0)-(a.revenue||0)).map(b=>`<option value="${esc(b.branch)}">${esc(b.branch)}</option>`));
    csel.innerHTML = opts.join('');
    csel.value = want;
  }
  await renderReportCost(getBranch() || '');
  renderReportTier(_rptLastMonth);   // §9 tier block — async, non-blocking

  // ── §9 Pricing & Discounts
  const discOverall = (discounts && discounts.overall) ? discounts.overall : {};
  const discByBranch = (discounts && Array.isArray(discounts.by_branch)) ? discounts.by_branch : [];
  const totalDisc = discOverall.total_discount || 0;
  document.getElementById('rptDiscountSummary').innerHTML = totalDisc > 0
    ? `Discounts: <b>${fmtN(totalDisc)}</b> across <b>${fmtN(discOverall.discount_tx||0)}</b> of <b>${fmtN(discOverall.total_tx||0)}</b> transactions (avg ${fmtN(discOverall.avg_discount||0)} each).`
    : 'No discount data.';
  const discSorted = [...discByBranch].sort((a,b)=>(b.rate||0)-(a.rate||0));
  document.getElementById('rptDiscountBody').innerHTML = discSorted.slice(0,15).map(d =>
    `<tr class="border-t border-slate-100">
      <td class="py-2 pr-4">${esc(d.branch)}</td>
      <td class="py-2 pr-4 text-right">${fmtN(d.discount_tx||0)} / ${fmtN(d.total_tx||0)}</td>
      <td class="py-2 pr-4 text-right text-red-500">${fmtN(d.total_discount||0)}</td>
      <td class="py-2 text-right">${(d.rate||0).toFixed(1)}%</td>
    </tr>`).join('');

  // Per-branch discount composition (nature) + MoM (current period vs prior month)
  try {
    const hasPrior = priorQpStr !== qpStr;   // distinct prior month only when a month/range is set
    const [compCur, compPrev] = await Promise.all([
      api(`${API}/api/analysis/discount_composition${qpStr}`).catch(()=>({branches:[]})),
      hasPrior ? api(`${API}/api/analysis/discount_composition${priorQpStr}`).catch(()=>({branches:[]})) : Promise.resolve({branches:[]}),
    ]);
    const BK = [
      { k:'free',       label:'ฟรี/100%+', color:'#7f1d1d' },
      { k:'ge50',       label:'50%+',       color:'#dc2626' },
      { k:'p20_50',     label:'20–50%',     color:'#f59e0b' },
      { k:'p10_20',     label:'10–20%',     color:'#facc15' },
      { k:'lt10',       label:'<10%',       color:'#84cc16' },
      { k:'zero_gross', label:'ไม่มียอดบิล', color:'#6b7280' },
    ];
    const prevMap = {}; (compPrev.branches||[]).forEach(b=>prevMap[b.branch]=b);
    const rows = (compCur.branches||[]).slice(0,15);
    const el = document.getElementById('rptDiscountComposition');
    if (!rows.length) { el.innerHTML = '<p class="text-slate-400 text-xs py-2">ไม่มีข้อมูลส่วนลด</p>'; }
    else el.innerHTML = `<table class="w-full text-xs">
      <thead><tr class="text-left text-slate-500 border-b border-slate-200">
        <th class="py-2 pr-3">สาขา</th><th class="py-2 pr-3 text-right">ส่วนลดรวม</th>
        <th class="py-2 pr-3 text-right">vs เดือนก่อน</th>
        <th class="py-2 pr-3">องค์ประกอบ (ตาม % ของบิล)</th>
        <th class="py-2 pr-3">เด่น</th></tr></thead>
      <tbody>${rows.map(b => {
        const tot = b.total_discount || 0;
        const prev = prevMap[b.branch];
        const mom = (hasPrior && prev && prev.total_discount>0) ? ((tot-prev.total_discount)/prev.total_discount*100) : null;
        const seg = BK.map(bk => { const v = (b.buckets?.[bk.k]||0); const w = tot? v/tot*100 : 0; return w>0?`<div title="${bk.label}: ${fmtN(v)} (${w.toFixed(0)}%)" style="width:${w}%;background:${bk.color}"></div>`:''; }).join('');
        const dom = BK.find(bk=>bk.k===b.dominant);
        return `<tr class="border-b border-slate-50">
          <td class="py-1.5 pr-3 font-medium">${esc(b.branch)}</td>
          <td class="py-1.5 pr-3 text-right text-red-500">${fmtN(tot)}</td>
          <td class="py-1.5 pr-3 text-right">${mom==null?'<span class="text-slate-300">—</span>':momChip(mom)}</td>
          <td class="py-1.5 pr-3"><div class="flex h-3 rounded overflow-hidden" style="min-width:120px">${seg}</div></td>
          <td class="py-1.5 pr-3">${dom?`<span style="color:${textInk(dom.color)};font-weight:600">${dom.label}</span>`:'—'}</td>
        </tr>`;
      }).join('')}</tbody></table>
      <div class="flex flex-wrap gap-3 mt-2 text-[11px] text-slate-500">${BK.map(bk=>`<span class="flex items-center gap-1"><span style="width:10px;height:10px;background:${bk.color};border-radius:2px;display:inline-block"></span>${bk.label}</span>`).join('')}</div>`;
  } catch(e) { console.error('discount composition failed', e); }

  // ── §10 Next-month forecast — projects the month AFTER the latest complete
  //    month so it can be tracked: at month-end, drop actuals next to it.
  (function renderNextMonth() {
    const el = document.getElementById('rptNextMonth'); if (!el) return;
    const series = (trendFull || []).map(r => r.revenue || 0);
    if (series.length < 3 || !_lastMo) { el.innerHTML = ''; return; }
    const ols = ys => {
      const n = ys.length, xs = ys.map((_, i) => i);
      const mx = xs.reduce((a,b)=>a+b,0)/n, my = ys.reduce((a,b)=>a+b,0)/n;
      let num=0, den=0; for (let i=0;i<n;i++){ num+=(xs[i]-mx)*(ys[i]-my); den+=(xs[i]-mx)**2; }
      const slope = den?num/den:0, inter = my-slope*mx;
      const sd = Math.sqrt(ys.reduce((a,y,i)=>a+(y-(inter+slope*i))**2,0)/n);
      return { sd, pred: inter + slope*n };
    };
    const f = ols(series);
    const pred = Math.max(0, f.pred);
    const lo = Math.max(0, pred - 1.28*f.sd), hi = pred + 1.28*f.sd;  // ~80% band
    const [yy, mm] = _lastMo.split('-').map(Number);
    const nextMo = mm === 12 ? `${yy+1}-01` : `${yy}-${String(mm+1).padStart(2,'0')}`;
    const lastRev = series[series.length-1];
    const momProj = lastRev > 0 ? (pred-lastRev)/lastRev*100 : null;
    const branchProj = Object.keys(_bm).map(br => {
      const ys = _allMonthsB.map(m => revOf(br, m)).filter(v => v > 0);
      const p = ys.length >= 3 ? Math.max(0, ols(ys).pred) : (ys.length ? ys[ys.length-1] : 0);
      return { branch: br, last: revOf(br, _lastMo), proj: p };
    }).filter(b => b.proj > 0).sort((a,b)=>b.proj-a.proj);
    const sumProj = branchProj.reduce((a,b)=>a+b.proj,0);
    const chip = p => p==null ? '' : `<span style="color:${p>=0?'var(--good-ink)':'var(--bad-ink)'}">${p>=0?'▲':'▼'} ${Math.abs(p).toFixed(1)}%</span>`;
    el.innerHTML = `
      <div class="rounded-xl border-2 p-4 mb-3" style="border-color:var(--accent);background:var(--accent-soft)">
        <div class="flex items-center justify-between flex-wrap gap-2 mb-2">
          <h3 class="text-sm font-bold text-indigo-900">พยากรณ์เดือนถัดไป — ${fmtMonth(nextMo)}</h3>
          <span class="text-[11px] text-slate-500">linear trend จาก ${series.length} เดือน · สิ้นเดือนนำ actual มาเทียบช่องนี้เพื่อติดตาม</span>
        </div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div class="p-3 rounded-lg bg-white border border-slate-100"><p class="text-[11px] text-slate-500">คาดการณ์รายได้</p><p class="text-2xl font-extrabold text-indigo-700">${fmt(pred)}</p><p class="text-[11px] text-slate-400">${fmtN(Math.round(pred))} บาท</p></div>
          <div class="p-3 rounded-lg bg-white border border-slate-100"><p class="text-[11px] text-slate-500">ช่วงเชื่อมั่น ~80%</p><p class="text-sm font-bold text-slate-700">${fmt(lo)} – ${fmt(hi)}</p><p class="text-[11px] text-slate-400">low – high</p></div>
          <div class="p-3 rounded-lg bg-white border border-slate-100"><p class="text-[11px] text-slate-500">vs ${fmtMonth(_lastMo)} (ล่าสุด)</p><p class="text-lg font-bold">${chip(momProj)}</p><p class="text-[11px] text-slate-400">${fmt(lastRev)} → ${fmt(pred)}</p></div>
          <div class="p-3 rounded-lg bg-white border border-slate-100"><p class="text-[11px] text-slate-500">รวมจากรายสาขา</p><p class="text-lg font-bold text-slate-700">${fmt(sumProj)}</p><p class="text-[11px] text-slate-400">${branchProj.length} สาขา</p></div>
        </div>
      </div>
      <details class="mb-1"><summary class="text-xs font-semibold text-slate-600 cursor-pointer">พยากรณ์รายสาขา — ${fmtMonth(nextMo)} (กดเพื่อดู)</summary>
        <div class="overflow-x-auto mt-2"><table class="w-full text-xs">
          <thead><tr class="text-left text-slate-400 border-b border-slate-200"><th class="py-1 pr-3">สาขา</th><th class="py-1 pr-3 text-right">${fmtMonth(_lastMo)} (ล่าสุด)</th><th class="py-1 pr-3 text-right">คาด ${fmtMonth(nextMo)}</th><th class="py-1 text-right">Δ</th></tr></thead>
          <tbody>${branchProj.map(b=>{const d=b.last>0?(b.proj-b.last)/b.last*100:null;return `<tr class="border-b border-slate-50"><td class="py-1 pr-3 font-medium">${esc(b.branch)}</td><td class="py-1 pr-3 text-right text-slate-500">${fmtN(b.last)}</td><td class="py-1 pr-3 text-right font-semibold">${fmtN(Math.round(b.proj))}</td><td class="py-1 text-right">${chip(d)}</td></tr>`;}).join('')}</tbody>
        </table></div>
      </details>`;
  })();

  // ── §10 Forecasts & Action Items
  const fcEl = document.getElementById('rptForecast');
  if (forecast && forecast.available) {
    fcEl.innerHTML = `คาดการณ์รายได้ <b>${fmtMonth(forecast.period)}</b> (ทุกสาขา): <b>${fmtN(forecast.projected)}</b> บาท
      · ช่วงความเชื่อมั่น ${fmtN(forecast.lo)} – ${fmtN(forecast.hi)}
      · อ้างอิงข้อมูล ${forecast.days_elapsed}/${forecast.days_total} วัน (MTD ${fmtN(forecast.mtd)} บาท · เฉลี่ย ${fmtN(forecast.avg_daily)}/วัน).`;
  } else { fcEl.textContent = 'Forecast not available for the selected scope.'; }

  // ── Forecast scorecard (projected / MoM / YoY / target) + scenario
  const scEl   = document.getElementById('rptForecastScorecard');
  const scenEl = document.getElementById('rptScenario');
  if (forecast && forecast.available) {
    const proj   = forecast.projected;
    const momPct = (forecastBranches && forecastBranches.total) ? forecastBranches.total.mom_pct : null;
    const yoyPct = (latestYoy && latestYoy.yoy_pct != null) ? latestYoy.yoy_pct : null;
    const prior  = (forecastBranches && forecastBranches.total) ? forecastBranches.total.prior_actual : null;
    // Target attainment (respects branch filter; empty-state when no target set)
    let targetTotal = 0;
    try {
      const tg = await api(`${API}/api/targets?month=${forecast.period}`).catch(() => []);
      const bsel = getBranch();
      targetTotal = (Array.isArray(tg) ? tg : [])
        .filter(t => !bsel || t.branch === bsel)
        .reduce((a, t) => a + (t.revenue_target || 0), 0);
    } catch (e) {}
    const attainPct = targetTotal > 0 ? (proj / targetTotal * 100) : null;
    const pctChip = p => p == null ? '<span class="text-slate-300">—</span>'
      : `<span style="color:${p>=0?'var(--good-ink)':'var(--bad-ink)'}">${p>=0?'▲':'▼'} ${Math.abs(p).toFixed(1)}%</span>`;
    const card = (label, valHtml, sub, color, onclick) => `<div class="p-3 rounded-lg bg-slate-50 ${onclick?'cursor-pointer hover:bg-slate-100':''}" ${onclick?`onclick="${onclick}"`:''}>
      <p class="text-xs text-slate-500 mb-0.5">${label}</p>
      <p class="text-lg font-bold" style="color:${textInk(color||'#1e293b')}">${valHtml}</p>
      <p class="text-[11px] text-slate-400">${sub}</p></div>`;
    scEl.innerHTML =
      card('คาดสิ้นเดือน', fmtN(proj), 'คาดจากยอดขายต่อวัน', '#4a70b0') +
      card('vs เดือนก่อน (MoM)', pctChip(momPct), 'projected vs last month', '#1e293b') +
      card('vs ปีก่อน (YoY)', yoyPct==null?'—':(yoyPct>=0?'+':'')+yoyPct.toFixed(1)+'%',
           latestYoy?`${latestYoy.period_month} vs ${latestYoy.ly_period}`:'ไม่มีข้อมูล',
           yoyPct==null?'#94a3b8':yoyPct>=0?'var(--good-ink)':'var(--bad-ink)') +
      (attainPct!=null
        ? card('เทียบเป้า', attainPct.toFixed(0)+'%', `เป้า ${fmtN(targetTotal)}`, attainPct>=100?'var(--good-ink)':attainPct>=90?'#d97706':'var(--bad-ink)')
        : card('เทียบเป้า', 'ตั้งเป้า →', 'ยังไม่ได้ตั้งเป้าเดือนนี้', '#4a70b0', "switchTab('settings')"));
    // Scenario best / base / worst from the confidence band
    const vsPrior = v => prior ? `${v>=prior?'+':''}${((v-prior)/prior*100).toFixed(1)}% vs เดือนก่อน` : '';
    const scLo = forecast.lo, scHi = forecast.hi, scBase = forecast.projected;
    const scRange = (scHi - scLo) || 1;
    const scBasePct = Math.min(100, Math.max(0, (scBase - scLo) / scRange * 100));
    const scPriorPct = (prior != null && prior >= scLo && prior <= scHi) ? (prior - scLo) / scRange * 100 : null;
    scenEl.innerHTML = `<h3 class="text-sm font-bold text-slate-600 mb-2">สถานการณ์จำลอง</h3>
      <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
        ${[
          ['ดีสุด (Best)',  forecast.hi,        '#059669', 'วันหยุด/โปรช่วยดันยอด · +1SD'],
          ['ฐาน (Base)',    forecast.projected, '#4a70b0', 'คงยอดขายต่อวันเท่าปัจจุบัน'],
          ['แย่สุด (Worst)', forecast.lo,        '#dc2626', 'สาขาขาช้า/สภาพอากาศ · −1SD'],
        ].map(([t,v,c,assume]) => `<div class="p-4 rounded-xl border" style="border-color:${c}33;background:${c}0a">
          <p class="text-xs font-semibold mb-1" style="color:${textInk(c)}">${t}</p>
          <p class="text-xl font-bold text-slate-800">${fmtN(v)}</p>
          <p class="text-[11px] text-slate-500 mt-0.5">${vsPrior(v)}</p>
          <p class="text-[11px] text-slate-400 mt-1">${assume}</p></div>`).join('')}
      </div>
      <div class="mt-4 px-1">
        <p class="text-[11px] text-slate-400 mb-2">ตำแหน่งของคาดการณ์ฐาน (Base) ภายในช่วงความเชื่อมั่น</p>
        <div class="relative h-2.5 rounded-full" style="background:#dc262633">
          ${scPriorPct != null ? `<div class="absolute top-1/2 -translate-y-1/2 w-0.5 h-5 bg-slate-500" style="left:${scPriorPct.toFixed(1)}%" title="เดือนก่อน ${fmtN(prior)}"></div>` : ''}
          <div class="absolute top-1/2 -translate-y-1/2 w-3.5 h-3.5 rounded-full border-2 border-white shadow" style="left:calc(${scBasePct.toFixed(1)}% - 7px);background:var(--accent)" title="ฐาน ${fmtN(scBase)}"></div>
        </div>
        <div class="flex justify-between text-[10px] text-slate-400 mt-1">
          <span>แย่สุด ${fmtN(scLo)}</span>
          ${scPriorPct != null ? `<span>| เดือนก่อน ${fmtN(prior)}</span>` : ''}
          <span>ดีสุด ${fmtN(scHi)}</span>
        </div>
      </div>`;
  } else { scEl.innerHTML = ''; scenEl.innerHTML = ''; }

  // Per-branch month-end projection table
  const fbWrap = document.getElementById('rptForecastBranchWrap');
  const momCell = pct => pct == null
    ? '<span class="text-slate-300">—</span>'
    : `<span style="color:${pct>=0?'var(--good-ink)':'var(--bad-ink)'}">${pct>=0?'▲':'▼'} ${Math.abs(pct).toFixed(1)}%</span>`;
  if (forecastBranches && forecastBranches.available && (forecastBranches.branches||[]).length) {
    // A branch whose prior month is <5% of its projection barely operated last
    // month (newly opened) — its MoM% would be a meaningless 5-digit number.
    const momBranchCell = b => (b.prior_actual != null && b.prior_actual < b.projected * 0.05)
      ? '<span style="color:var(--cyan-ink)">ใหม่</span>'
      : momCell(b.mom_pct);
    document.getElementById('rptForecastBranchBody').innerHTML = forecastBranches.branches.map(b =>
      `<tr class="border-b border-slate-100">
        <td class="py-1.5 pr-4 font-medium text-slate-800">${esc(b.branch)}</td>
        <td class="py-1.5 pr-4 text-right text-slate-500">${fmtN(b.mtd)}</td>
        <td class="py-1.5 pr-4 text-right font-semibold">${fmtN(b.projected)}</td>
        <td class="py-1.5 pr-4 text-right text-slate-500">${b.prior_actual!=null?fmtN(b.prior_actual):'—'}</td>
        <td class="py-1.5 text-right">${momBranchCell(b)}</td>
      </tr>`).join('');
    const t = forecastBranches.total || {};
    document.getElementById('rptForecastBranchFoot').innerHTML =
      `<tr class="border-t-2 border-slate-300 font-bold text-slate-800">
        <td class="py-2 pr-4">รวมทุกสาขา</td>
        <td class="py-2 pr-4 text-right">${fmtN(t.mtd||0)}</td>
        <td class="py-2 pr-4 text-right">${fmtN(t.projected||0)}</td>
        <td class="py-2 pr-4 text-right">${t.prior_actual!=null?fmtN(t.prior_actual):'—'}</td>
        <td class="py-2 text-right">${momCell(t.mom_pct)}</td>
      </tr>`;
    // Inactive-branch note (explains the 26-vs-27 gap honestly)
    const noteEl = document.getElementById('rptInactiveNote');
    const inact = forecastBranches.inactive_branches || [];
    if (noteEl) {
      noteEl.innerHTML = `แสดง <b>${forecastBranches.active_branches}</b> สาขาที่มียอดในเดือน ${fmtMonth(forecastBranches.period)}`
        + (forecastBranches.total_branches ? ` จากทั้งหมด <b>${forecastBranches.total_branches}</b> สาขา` : '')
        + (inact.length ? ` · ⚠ ไม่มียอด: ${inact.map(b=>`${esc(b.branch)} (ล่าสุด ${b.last_sale||'—'})`).join(', ')}` : '');
    }
    fbWrap.classList.remove('hidden');
  } else { fbWrap.classList.add('hidden'); }

  document.getElementById('rptAnomalies').innerHTML = anomalies.length
    ? anomalies.slice(0,8).map(a => `<li>${a.kind || a.type || 'Anomaly'} on <b>${a.date || a.period || ''}</b> at <b>${a.branch || 'multiple branches'}</b>${a.note?' — '+a.note:''}${a.z_score?' (z='+a.z_score.toFixed(1)+')':''}</li>`).join('')
    : '<li class="italic text-slate-400">No anomalies detected.</li>';

  // ── Action items — prioritized, specific, ฿-quantified, owner-tagged
  const actions = [];
  const pushA = (pri, owner, text, impact) => actions.push({ pri, owner, text, impact });
  const topItems = (q, n=3) => items.filter(r => r.quadrant === q).sort((a,b)=>(b.revenue||0)-(a.revenue||0)).slice(0,n);
  const topNames = (q, n=3) => topItems(q, n).map(r => esc(r.description || r.sku_code));
  const sumRev   = q => items.filter(r => r.quadrant === q).reduce((a,r)=>a+(r.revenue||0),0);

  const plowItems = items.filter(r => r.quadrant === 'plowhorse');
  if (plowItems.length) pushA('high', 'ราคา/ต้นทุน',
    `<b>${plowItems.length} Plowhorse SKU</b> (ขายดีแต่มาร์จิ้นต่ำ) เช่น ${topNames('plowhorse').join(', ')} — ขึ้นราคาเล็กน้อยหรือลดต้นทุนต่อจาน ให้ผลกับกำไรเร็วสุดเพราะปริมาณสูง.`,
    `ฐานรายได้กระทบ ~${fmtN(sumRev('plowhorse'))} บาท`);

  const dogItems = items.filter(r => r.quadrant === 'dog');
  if (dogItems.length) pushA('med', 'เมนู',
    `<b>${dogItems.length} Dog SKU</b> (ขายน้อย-มาร์จิ้นต่ำ) เช่น ${topNames('dog').join(', ')} — พิจารณาถอด/ปรับราคา/รวมเมนู.`,
    `รายได้รวมเพียง ${fmtN(sumRev('dog'))} บาท`);

  const puzzleItems = items.filter(r => r.quadrant === 'puzzle');
  if (puzzleItems.length) pushA('med', 'เมนู/การตลาด',
    `<b>${puzzleItems.length} Puzzle SKU</b> (มาร์จิ้นสูงแต่ขายน้อย) เช่น ${topNames('puzzle').join(', ')} — ดันยอดผ่านโปรโมท/จัดวางเมนู/ให้พนักงานเชียร์.`,
    `อัปไซด์กำไร ถ้าดันยอดได้`);

  if (cs.uncosted_skus > 0) pushA('high', 'ต้นทุน/ข้อมูล',
    `<b>${cs.uncosted_skus} SKU ยังไม่มีต้นทุน</b> (จาก ${cs.total_skus||0}) — กำไร/มาร์จิ้นยังไม่ครบ. อัปโหลดที่ Settings → Cost Management.`,
    `บล็อกความแม่นยำของ §4, §8`);

  const discList = (discounts && Array.isArray(discounts.by_branch)) ? [...discounts.by_branch].sort((a,b)=>(b.rate||0)-(a.rate||0)) : [];
  const worstDisc = discList[0];
  if (worstDisc && (worstDisc.rate||0) > 10) pushA('high', 'สาขา/การเงิน',
    `ส่วนลดสูงสุดที่สาขา <b>${esc(worstDisc.branch)}</b> ${(worstDisc.rate||0).toFixed(1)}% — ตรวจการกดส่วนลด (ดูบิลที่ §9).`,
    `ส่วนลด ${fmtN(worstDisc.total_discount||0)} บาท`);
  else if ((discOverall.discount_tx || 0) / (discOverall.total_tx || 1) > 0.10) pushA('med', 'การเงิน',
    `อัตราการให้ส่วนลดรวมเกิน 10% — ตรวจสอบประสิทธิภาพโปรโมชั่น.`,
    `ส่วนลดรวม ${fmtN(discOverall.total_discount||0)} บาท`);

  // Only raise the YoY-decline action on a figure that is actually comparable —
  // a raw drop caused by a partial current month is not a business problem.
  const _yoyAct = (latestYoy && !latestYoy.comparable && latestYoy.same_store_pct != null)
    ? latestYoy.same_store_pct : (latestYoy ? latestYoy.yoy_pct : null);
  if (_yoyAct != null && _yoyAct < 0) pushA('high', 'ผู้บริหาร',
    `รายได้ลดลง YoY <b>${_yoyAct.toFixed(1)}%</b> (${latestYoy.period_month} เทียบ ${latestYoy.ly_period}${latestYoy.comparable ? '' : ' · เทียบสาขาเดิม'}) — เจาะหาสาขา/เมนูที่ฉุดยอด.`,
    `แนวโน้มรายได้`);

  if (forecastBranches && forecastBranches.available) {
    const dropping = forecastBranches.branches.filter(b => b.mom_pct != null && b.mom_pct < -10);
    if (dropping.length) pushA('high', 'สาขา',
      `<b>${dropping.length} สาขา</b> คาดรายได้สิ้นเดือนต่ำกว่าเดือนก่อน >10%: ${dropping.slice(0,4).map(b=>esc(b.branch)+' ('+b.mom_pct.toFixed(0)+'%)').join(', ')}.`,
      `รวม ~${fmtN(dropping.reduce((a,b)=>a+((b.prior_actual||0)-(b.projected||0)),0))} บาทที่อาจหาย`);
  }

  if (cs.overall_margin_pct != null && cs.overall_margin_pct < 60) pushA('med', 'ต้นทุน',
    `Gross margin รวม ${cs.overall_margin_pct.toFixed(1)}% — ต่ำกว่าเกณฑ์ร้านอาหารทั่วไป (~65–70%). ทบทวนต้นทุนวัตถุดิบ/สูตร.`,
    `ทุก 1% margin = ${fmtN((totalRevTrend||0)*0.01)} บาท/ช่วง`);

  const PRI = { high:{o:0,b:'●',label:'เร่งด่วน',bg:'#fef2f2'}, med:{o:1,b:'●',label:'ควรทำ',bg:'#fffbeb'}, info:{o:2,b:'●',label:'ข้อมูล',bg:'#f0fdf4'} };
  actions.sort((a,b)=>PRI[a.pri].o - PRI[b.pri].o);
  document.getElementById('rptActions').innerHTML = actions.length
    ? actions.map(a => `<li class="flex gap-2 items-start rounded-lg px-3 py-2" style="background:${PRI[a.pri].bg}">
        <span class="mt-0.5">${PRI[a.pri].b}</span>
        <div class="flex-1">
          <div>${a.text}</div>
          <div class="flex items-center gap-2 mt-1 text-[11px]">
            <span class="px-1.5 py-0.5 rounded font-medium" style="background:var(--accent-soft);color:var(--accent)">${esc(a.owner)}</span>
            ${a.impact?`<span class="text-slate-500">${a.impact}</span>`:''}
          </div>
        </div></li>`).join('')
    : '<li class="italic text-slate-400">Operations look healthy — no urgent flags.</li>';
}


// ════════════════════════════════════════════════════════════════
// COST UPLOAD MODAL
// ════════════════════════════════════════════════════════════════
function openCostModal() {
  document.getElementById('costModal').classList.remove('hidden');
  document.getElementById('costModal').classList.add('flex');
  api(`${API}/api/cost`).then(data => {
    costModalSkuMap = {};
    data.forEach(s => { costModalSkuMap[s.sku_code] = s; });
  });
}
function closeCostModal() {
  document.getElementById('costModal').classList.add('hidden');
  document.getElementById('costModal').classList.remove('flex');
  document.getElementById('costPreview').classList.add('hidden');
  document.getElementById('previewBody').innerHTML = '';
  document.getElementById('saveBtn').disabled = true;
  document.getElementById('saveStatus').textContent = '';
  document.getElementById('costFile').value = '';
  parsedCostRows = [];
}
function onDrop(e) {
  e.preventDefault();
  document.getElementById('dropZone').classList.remove('border-indigo-400','bg-indigo-50');
  const file = e.dataTransfer.files[0];
  if (file) parseCsvFile(file);
}
function onCostFileChange(e) { if (e.target.files[0]) parseCsvFile(e.target.files[0]); }
function parseCsvFile(file) {
  const reader = new FileReader();
  reader.onload = e => renderCostPreview(e.target.result);
  reader.readAsText(file);
}
function renderCostPreview(text) {
  const lines = text.split(/\r?\n/).map(l=>l.trim()).filter(Boolean);
  if (!lines.length) return;
  const hasHeader = lines[0].toLowerCase().includes('sku') || lines[0].toLowerCase().includes('code');
  const dataLines = hasHeader ? lines.slice(1) : lines;
  parsedCostRows = [];
  const unknowns = [];
  const rows = dataLines.map(line => {
    const parts = line.includes('\t') ? line.split('\t') : line.split(',');
    const sku_code = (parts[0]||'').trim();
    const costRaw = (parts[1]||'').trim().replace(/[,\s]/g,'');
    const unit_cost = parseFloat(costRaw);
    if (!sku_code) return null;
    const known = costModalSkuMap[sku_code];
    const valid = !isNaN(unit_cost) && unit_cost >= 0;
    if (valid) parsedCostRows.push({sku_code, unit_cost});
    if (!known) unknowns.push(sku_code);
    return {sku_code, description: known?.description||'—', unit_cost: valid?unit_cost:null, known:!!known, valid};
  }).filter(Boolean);
  const body = document.getElementById('previewBody');
  body.innerHTML = '';
  rows.forEach(r => {
    const sc = !r.known ? 'text-amber-600' : r.valid ? 'text-emerald-600' : 'text-red-600';
    const st = !r.known ? 'Unknown' : r.valid ? 'OK' : 'Invalid';
    body.innerHTML += `<tr class="border-t border-slate-100">
      <td class="py-1.5 px-3 font-mono text-indigo-600 text-xs">${r.sku_code}</td>
      <td class="py-1.5 px-3 text-slate-500 truncate max-w-xs text-xs">${esc(r.description)}</td>
      <td class="py-1.5 px-3 text-right text-xs">${r.unit_cost!=null?fmtN(r.unit_cost):'—'}</td>
      <td class="py-1.5 px-3 text-xs ${sc}">${st}</td>
    </tr>`;
  });
  document.getElementById('previewMeta').textContent = `${rows.length} rows · ${parsedCostRows.length} valid · ${rows.length-parsedCostRows.length} skipped`;
  document.getElementById('previewWarn').textContent = unknowns.length ? `${unknowns.length} unknown SKUs` : '';
  document.getElementById('costPreview').classList.remove('hidden');
  document.getElementById('saveBtn').disabled = parsedCostRows.length === 0;
}
async function saveCosts() {
  const btn = document.getElementById('saveBtn');
  const status = document.getElementById('saveStatus');
  btn.disabled = true;
  status.textContent = 'Saving…'; status.className = 'text-sm text-slate-400';
  try {
    const res = await fetch(`${API}/api/cost`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(parsedCostRows) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Save failed');
    if (data.skipped_price_as_cost) {
      status.textContent = `✓ ${data.updated} saved · ⚠ ${data.skipped_price_as_cost} skipped (looked like price, not cost)`;
      status.className = 'text-sm text-amber-600';
      checkCostSpikes();
      return;  // keep modal open so the user can correct the skipped rows
    }
    status.textContent = `✓ ${data.updated} SKUs updated`; status.className = 'text-sm text-emerald-600';
    checkCostSpikes();
    setTimeout(closeCostModal, 1200);
  } catch(err) {
    status.textContent = '✗ ' + err.message; status.className = 'text-sm text-red-600';
    btn.disabled = false;
  }
}
async function downloadCostTemplate() {
  const btn = event?.target?.closest('button');
  const origText = btn?.innerHTML;
  if (btn) { btn.disabled = true; btn.innerHTML = 'Preparing…'; }
  try {
    const data = await api(`${API}/api/cost/template`);
    const headers = [
      'sku_code',
      'unit_cost',          // ← fill this in (THB per portion)
      'description',
      'category',
      'ref_total_revenue',  // reference only — do not edit
      'ref_qty_sold',       // reference only — do not edit
      'ref_last_sold',      // reference only — do not edit
    ];
    const rows = [headers];
    data.forEach(s => rows.push([
      s.sku_code,
      s.unit_cost ?? '',    // pre-fill if already set
      s.description ?? '',
      s.category ?? '',
      s.total_revenue ?? 0,
      s.total_qty ?? 0,
      s.last_sold ?? '',
    ]));
    const csv = rows.map(r =>
      r.map(v => `"${String(v).replace(/"/g,'""')}"`).join(',')
    ).join('\r\n');
    const blob = new Blob(['﻿' + csv, ''], { type: 'text/csv;charset=utf-8' });
    const a = Object.assign(document.createElement('a'), {
      href: URL.createObjectURL(blob),
      download: `oshinei_cost_template_${new Date().toISOString().slice(0,10)}.csv`,
    });
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  } catch(e) {
    uiNotice('Download failed: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = origText; }
  }
}

// Kept for backward compat (modal ↓ Template button)

// ════════════════════════════════════════════════════════════════
// PHASE 8 — MONTHLY COST OVERRIDES
// ════════════════════════════════════════════════════════════════
let mcRows = [];        // current loaded rows
let mcEdits = {};       // sku_code -> {unit_cost, notes}
let mcHistChartRef = null;

function _mcCurrentMonth() {
  const el = document.getElementById('mcMonth');
  let m = (el?.value || '').trim();
  if (!m) {
    const d = new Date();
    m = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;
    if (el) el.value = m;
  }
  return m;
}

async function loadMonthlyCost() {
  const month = _mcCurrentMonth();
  const status = document.getElementById('mcostStatus');
  status.textContent = 'กำลังโหลด…';
  try {
    const data = await api(`${API}/api/cost/monthly?month=${month}`);
    mcRows = Array.isArray(data) ? data : [];
    mcEdits = {};
    document.getElementById('mcSaveBtn').disabled = true;
    document.getElementById('mcSaveStatus').textContent = '';
    renderMonthlyCostTable();
    status.textContent = `${mcRows.length} SKUs · เดือน ${month}`;
  } catch(e) {
    status.textContent = '✗ โหลดไม่สำเร็จ: ' + e.message;
  }
}

function renderMonthlyCostTable() {
  const body = document.getElementById('mcostBody');
  if (!mcRows.length) {
    body.innerHTML = '<tr><td colspan="8" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
    return;
  }
  const q = (document.getElementById('mcSearch')?.value || '').trim().toLowerCase();
  const srcFilter = document.getElementById('mcSourceFilter')?.value || '';
  const month = _mcCurrentMonth();
  const fmt = v => (v==null || v==='') ? '' : (typeof fmtN === 'function' ? fmtN(v) : Number(v).toLocaleString('th-TH',{maximumFractionDigits:2}));
  const sourceBadge = {
    monthly:   '<span class="px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700">monthly</span>',
    inherited: '<span class="px-1.5 py-0.5 rounded bg-amber-100 text-amber-700">inherited</span>',
    default:   '<span class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-600">default</span>',
    none:      '<span class="px-1.5 py-0.5 rounded bg-red-100 text-red-700">none</span>',
  };
  const filtered = mcRows.filter(r => {
    if (srcFilter && r.source !== srcFilter) return false;
    if (q) {
      const hay = ((r.sku_code||'') + ' ' + (r.description||'')).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  body.innerHTML = filtered.map(r => {
    const edit = mcEdits[r.sku_code];
    const currentMonthly = edit !== undefined
      ? (edit.unit_cost === '' ? '' : edit.unit_cost)
      : (r.source === 'monthly' ? r.unit_cost : '');
    const currentNotes = edit !== undefined ? (edit.notes || '') : (r.notes || '');
    return `<tr class="border-t border-slate-100 hover:bg-slate-50">
      <td class="py-1.5 pr-3 font-mono text-indigo-600 text-xs">
        <a href="#" onclick="openMcHistory('${r.sku_code.replace(/'/g,"\\'")}'); return false;" class="hover:underline">${r.sku_code}</a>
      </td>
      <td class="py-1.5 pr-3 text-slate-600 truncate max-w-xs">${esc(r.description || '')}</td>
      <td class="py-1.5 pr-3 text-right text-slate-500">${fmt(r.default_cost)}</td>
      <td class="py-1.5 pr-3 text-right">
        <input type="number" step="0.01" min="0" value="${currentMonthly}"
               oninput="onMcCellEdit('${r.sku_code.replace(/'/g,"\\'")}', 'unit_cost', this.value)"
               class="w-24 text-right border border-slate-300 rounded px-1.5 py-0.5 text-xs">
      </td>
      <td class="py-1.5 pr-3 text-right font-semibold ${r.source==='none'?'text-red-600':'text-slate-700'}">${fmt(r.unit_cost)}</td>
      <td class="py-1.5 pr-3">${sourceBadge[r.source] || r.source}</td>
      <td class="py-1.5 pr-3">
        <input type="text" value="${(currentNotes||'').replace(/"/g,'&quot;')}"
               oninput="onMcCellEdit('${r.sku_code.replace(/'/g,"\\'")}', 'notes', this.value)"
               class="w-32 border border-slate-200 rounded px-1.5 py-0.5 text-xs" placeholder="—">
      </td>
      <td class="py-1.5 pr-3 text-right">
        ${r.source==='monthly' ? `<button onclick="deleteMcRow('${r.sku_code.replace(/'/g,"\\'")}')" class="text-red-500 hover:text-red-700 text-xs" title="ลบ override เดือนนี้">${ICON.trash}</button>` : ''}
      </td>
    </tr>`;
  }).join('');
}

function onMcCellEdit(sku, field, value) {
  if (!mcEdits[sku]) mcEdits[sku] = { unit_cost: '', notes: '' };
  mcEdits[sku][field] = value;
  document.getElementById('mcSaveBtn').disabled = false;
}

async function saveMonthlyCostEdits() {
  const month = _mcCurrentMonth();
  const payload = [];
  for (const [sku_code, e] of Object.entries(mcEdits)) {
    const raw = String(e.unit_cost || '').trim();
    if (raw === '') continue;        // empty input = no override to save
    const uc = parseFloat(raw);
    if (isNaN(uc) || uc < 0) continue;
    payload.push({ sku_code, period_month: month, unit_cost: uc, notes: e.notes || null });
  }
  const status = document.getElementById('mcSaveStatus');
  if (!payload.length) { status.textContent = 'ไม่มีข้อมูลที่จะบันทึก'; return; }
  status.textContent = 'กำลังบันทึก…';
  try {
    const res = await fetch(`${API}/api/cost/monthly`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'บันทึกไม่สำเร็จ');
    let msg = `✓ บันทึก ${data.written} รายการ`;
    if (data.unknown_skus?.length) msg += ` · unknown: ${data.unknown_skus.length}`;
    if (data.invalid_months?.length) msg += ` · invalid_months: ${data.invalid_months.length}`;
    status.textContent = msg;
    await loadMonthlyCost();
  } catch(e) {
    status.textContent = '✗ ' + e.message;
  }
}

async function deleteMcRow(sku) {
  if (!await uiConfirm(`ลบ monthly override ของ ${sku} เดือน ${_mcCurrentMonth()} ?`)) return;
  const month = _mcCurrentMonth();
  try {
    const res = await fetch(`${API}/api/cost/monthly?sku=${encodeURIComponent(sku)}&month=${month}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('delete failed');
    await loadMonthlyCost();
  } catch(e) { uiNotice('ลบไม่สำเร็จ: ' + e.message); }
}

async function downloadMonthlyTemplate() {
  const month = _mcCurrentMonth();
  try {
    const res = await fetch(`${API}/api/cost/monthly/template?month=${month}`);
    if (!res.ok) throw new Error('template fetch failed');
    const blob = await res.blob();
    const a = Object.assign(document.createElement('a'), {
      href: URL.createObjectURL(blob),
      download: `cost_monthly_${month}.csv`,
    });
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  } catch(e) { uiNotice('ดาวน์โหลดไม่สำเร็จ: ' + e.message); }
}

function onMonthlyCsvSelected(ev) {
  const file = ev.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async () => {
    const text = reader.result;
    const month = _mcCurrentMonth();
    const lines = text.split(/\r?\n/).filter(l => l.trim());
    if (!lines.length) return;
    // Detect header
    const first = lines[0].toLowerCase();
    const hasHeader = first.includes('sku') || first.includes('code');
    const headerCells = hasHeader ? lines[0].split(',').map(s => s.trim().toLowerCase().replace(/"/g,'')) : null;
    let idxSku = 0, idxCost = -1, idxNotes = -1, idxMonth = -1;
    if (headerCells) {
      idxSku   = headerCells.findIndex(h => h.includes('sku'));
      idxMonth = headerCells.findIndex(h => h.includes('period') || h === 'month');
      idxCost  = headerCells.findIndex(h => h.includes('monthly_unit_cost'));
      if (idxCost === -1) idxCost = headerCells.findIndex(h => h.includes('unit_cost'));
      idxNotes = headerCells.findIndex(h => h.includes('note'));
    } else {
      idxCost = 1;
    }
    const payload = [];
    const skip = [];
    for (const line of (hasHeader ? lines.slice(1) : lines)) {
      // simple CSV split (no embedded commas/quotes expected in our template)
      const parts = line.split(',').map(s => s.trim().replace(/^"|"$/g,''));
      const sku_code = parts[idxSku] || '';
      const costRaw  = idxCost >= 0 ? (parts[idxCost] || '') : '';
      const notes    = idxNotes >= 0 ? (parts[idxNotes] || '') : '';
      const pm       = (idxMonth >= 0 && parts[idxMonth]) ? parts[idxMonth] : month;
      if (!sku_code || costRaw === '') { skip.push(sku_code); continue; }
      const uc = parseFloat(costRaw);
      if (isNaN(uc) || uc < 0) { skip.push(sku_code); continue; }
      payload.push({ sku_code, period_month: pm, unit_cost: uc, notes: notes || null });
    }
    if (!payload.length) { uiNotice(`ไม่มีแถวที่ใช้ได้ (skipped ${skip.length})`); ev.target.value=''; return; }
    if (!await uiConfirm(`อัปโหลด ${payload.length} แถว (skip ${skip.length}) เดือน ${month} ?`)) { ev.target.value=''; return; }
    try {
      const res = await fetch(`${API}/api/cost/monthly`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'upload failed');
      uiNotice(`✓ บันทึก ${data.written}\n• unknown SKUs: ${data.unknown_skus?.length||0}\n• invalid months: ${data.invalid_months?.length||0}`);
      await loadMonthlyCost();
    } catch(err) {
      uiNotice('อัปโหลดไม่สำเร็จ: ' + err.message);
    } finally {
      ev.target.value = '';
    }
  };
  reader.readAsText(file);
}

// Upload a sales-data file (cleansed-raw Easy export or legacy .txt) to /api/upload.
// The backend auto-detects format and routes to the shared upload_data engine.
async function onSalesDataSelected(ev) {
  const file = ev.target.files && ev.target.files[0];
  if (!file) return;
  if (!await uiConfirm(`อัพโหลดไฟล์ยอดขาย "${file.name}" เข้าฐานข้อมูล?\n(จะแทนที่ข้อมูลของเดือนนั้น — curated cost ไม่ถูกแตะ)`)) {
    ev.target.value = ''; return;
  }
  const fd = new FormData();
  fd.append('file', file);
  // Hosted deploys require an upload token (X-Upload-Token). Local dev needs none.
  const doUpload = (tok) => {
    const headers = tok ? { 'X-Upload-Token': tok } : {};
    return fetch(`${API}/api/upload`, { method: 'POST', body: fd, headers });
  };
  try {
    let res = await doUpload(localStorage.getItem('oshinei_upload_token') || '');
    if (res.status === 403) {
      const tok = await window.askWriteToken('การอัปโหลดข้อมูลต้องใช้ Owner Write-Token\nกรอกครั้งเดียว เบราว์เซอร์นี้จะจำไว้');
      if (!tok) { ev.target.value = ''; return; }
      localStorage.setItem('oshinei_upload_token', tok.trim());
      res = await doUpload(tok.trim());
    }
    const data = await res.json();
    if (!res.ok) {
      if (res.status === 403 && res.headers.get('X-Denied') !== 'role') localStorage.removeItem('oshinei_upload_token');
      throw new Error(data.detail || 'upload failed');
    }
    await uiNotice(`✓ อัพโหลดสำเร็จ\n• ไฟล์: ${data.file}\n• ชนิด: ${data.file_type}\n• เดือน: ${data.period}\n• สาขา: ${data.branch}\n• แถว: ${data.rows_ingested}`);
    location.reload();
  } catch (err) {
    uiNotice('อัพโหลดไม่สำเร็จ: ' + err.message);
  } finally {
    ev.target.value = '';
  }
}

async function openMcHistory(sku) {
  const modal = document.getElementById('mcHistoryModal');
  modal.classList.remove('hidden'); modal.classList.add('flex');
  document.getElementById('mcHistTitle').textContent = `ประวัติต้นทุน — ${sku}`;
  document.getElementById('mcHistBody').innerHTML = '<tr><td colspan="4" class="py-3 text-center text-slate-400">กำลังโหลด…</td></tr>';
  try {
    const data = await api(`${API}/api/cost/monthly?sku=${encodeURIComponent(sku)}`);
    const monthly = (data || []).filter(r => r.source === 'monthly').sort((a,b)=>a.period_month.localeCompare(b.period_month));
    const def = (data || []).find(r => r.source === 'default');
    const tbody = document.getElementById('mcHistBody');
    const rows = [...monthly];
    if (def) rows.push({ period_month: '— default —', unit_cost: def.unit_cost, source: 'default', notes: '' });
    tbody.innerHTML = rows.length ? rows.map(r => `
      <tr class="border-t border-slate-100">
        <td class="py-1.5 pr-3">${r.period_month || ''}</td>
        <td class="py-1.5 pr-3 text-right">${r.unit_cost==null?'—':(typeof fmtN==='function'?fmtN(r.unit_cost):r.unit_cost)}</td>
        <td class="py-1.5 pr-3 text-xs text-slate-500">${r.source}</td>
        <td class="py-1.5 pr-3 text-xs text-slate-500">${r.notes||''}</td>
      </tr>`).join('') : '<tr><td colspan="4" class="py-3 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';

    // Render simple line chart (Chart.js available globally)
    const canvas = document.getElementById('mcHistChart');
    if (canvas && typeof Chart !== 'undefined') {
      if (mcHistChartRef) { try { mcHistChartRef.destroy(); } catch(_){} }
      const labels = monthly.map(r => r.period_month);
      const values = monthly.map(r => r.unit_cost);
      mcHistChartRef = new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
          labels,
          datasets: [{
            label: 'unit_cost',
            data: values,
            borderColor: '#4a70b0',
            backgroundColor: 'rgba(31,58,107,.1)',
            tension: 0.25,
            fill: true,
          }, def ? {
            label: 'default',
            data: labels.map(() => def.unit_cost),
            borderColor: '#94a3b8',
            borderDash: [5,4],
            pointRadius: 0,
            fill: false,
          } : null].filter(Boolean),
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: true } } },
      });
    }
  } catch(e) {
    document.getElementById('mcHistBody').innerHTML = `<tr><td colspan="4" class="py-3 text-center text-red-500">${e.message}</td></tr>`;
  }
}

function closeMcHistory() {
  const modal = document.getElementById('mcHistoryModal');
  modal.classList.add('hidden'); modal.classList.remove('flex');
  if (mcHistChartRef) { try { mcHistChartRef.destroy(); } catch(_){} mcHistChartRef = null; }
}

// ════════════════════════════════════════════════════════════════
// TAB 6 — COST ANALYSIS
// ════════════════════════════════════════════════════════════════

let costData = null;            // raw API response
let activeCostTab = 'overview';
let costSortKey = 'revenue';
let costSortAsc = false;

function switchCostTab(name) {
  activeCostTab = name;
  document.querySelectorAll('#tab-cost .subtab-btn').forEach((b, i) => {
    b.classList.toggle('active', ['overview','detail','suggestions'][i] === name);
  });
  ['overview','detail','suggestions'].forEach(s =>
    document.getElementById('costtab-' + s).classList.toggle('hidden', s !== name)
  );
  if (!costData) return;
  if (name === 'overview')    renderCostOverview();
  if (name === 'detail')      renderCostDetail();
  if (name === 'suggestions') renderSuggestions();
}

// ════════════════════════════════════════════════════════════════
// PROFIT ANALYSIS — joins sales × sku_cost ⇒ GP, margin, per-bill
// ════════════════════════════════════════════════════════════════
let _buffetPerBillGran = 'monthly';
function setBuffetPerBillGran(g) {
  _buffetPerBillGran = g;
  ['daily','weekly','monthly','quarterly'].forEach(v => {
    const btn = document.getElementById('bpb-' + v);
    if (!btn) return;
    btn.className = (v === g)
      ? 'px-3 py-1.5 bg-pink-600 text-white border-l border-slate-200'
      : 'px-3 py-1.5 bg-white text-slate-600 hover:bg-pink-50 border-l border-slate-200';
  });
  loadBuffetPerBill();
}

async function loadBuffetPerBill() {
  const p = new URLSearchParams();
  if (getBranch())    p.set('branch',     getBranch());
  if (getMonthFrom()) p.set('month_from', getMonthFrom());
  if (getMonthTo())   p.set('month_to',   getMonthTo());
  p.set('granularity', _buffetPerBillGran);
  const d = await api(`${API}/api/profit/buffet_per_bill?${p}`).catch(() => null);
  if (!d || !d.rows) return;

  const ov = d.overall || {};
  const kpi = (lbl, val, sub, color) =>
    `<div class="rounded-lg p-3 border" style="background:${color}10;border-color:${color}33">
      <p class="text-[10px] text-slate-500 uppercase tracking-wide">${lbl}</p>
      <p class="text-lg font-bold" style="color:${textInk(color)}">${val}</p>
      <p class="text-[10px] text-slate-500">${sub}</p>
    </div>`;
  document.getElementById('bpbKpiStrip').innerHTML = [
    kpi('บิลทั้งหมด',          fmtN(ov.bills),               `${fmtN(ov.pax)} pax`,        '#ec4899'),
    kpi('รายได้ ex-VAT',        fmt(ov.revenue_ex),           `เฉลี่ย/บิล ${fmt(ov.avg_per_bill_rev)}`,    '#4a70b0'),
    kpi('BoM (ต้นทุน)',         fmt(ov.cost),                 `เฉลี่ย/บิล ${fmt(ov.avg_per_bill_cost)}`,   '#dc2626'),
    kpi('กำไรขั้นต้น',          fmt(ov.profit),               `เฉลี่ย/บิล ${fmt(ov.avg_per_bill_profit)}`, '#10b981'),
    kpi('% Margin',             `${(ov.margin_pct||0).toFixed(1)}%`, `BoM ${(100-(ov.margin_pct||0)).toFixed(1)}% ของรายได้`, ov.margin_pct >= 40 ? '#10b981' : ov.margin_pct >= 25 ? '#f59e0b' : '#ef4444'),
  ].join('');

  const rows = d.rows;

  // Line+bar chart: avg revenue/bill, avg cost/bill, avg profit/bill per period
  mkChart('chartBuffetPerBill', {
    type: 'bar',
    data: {
      labels: rows.map(r => r.period),
      datasets: [
        { type:'bar',  label:'เฉลี่ย/บิล: รายได้', data: rows.map(r => r.avg_per_bill_rev),    backgroundColor:'rgba(31,58,107,0.55)', borderRadius:3, yAxisID:'y' },
        { type:'bar',  label:'เฉลี่ย/บิล: BoM',     data: rows.map(r => r.avg_per_bill_cost),   backgroundColor:'rgba(220,38,38,0.65)', borderRadius:3, yAxisID:'y' },
        { type:'line', label:'เฉลี่ย/บิล: กำไร',    data: rows.map(r => r.avg_per_bill_profit), borderColor:'#10b981', backgroundColor:'rgba(16,185,129,0.15)', tension:0.3, fill:true, yAxisID:'y' },
        { type:'line', label:'อัตรากำไร %',            data: rows.map(r => r.margin_pct),          borderColor:'#f59e0b', borderDash:[4,3], backgroundColor:'transparent', tension:0.25, yAxisID:'y1', pointRadius:3 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'bottom', labels: { font: { size: 10 } } },
        datalabels: { display: false },
        tooltip: { mode: 'index', intersect: false },
      },
      scales: {
        x: { ticks: { font: { size: 9 } } },
        y:  { position:'left',  ticks: { font: { size: 9 }, callback: v => fmt(v) } },
        y1: { position:'right', ticks: { font: { size: 9 }, callback: v => v.toFixed(0) + '%' }, grid: { display: false }, min: 0, max: 100 },
      },
    },
  });

  // Cost mix doughnut — concentration of BoM cost by per-head spend bracket
  const tierMix = ov.tier_cost_mix || {};
  const TIER_COLORS = { '≤450': '#eab308', '450-600': '#10b981', '600-720': '#4a70b0', '>720': '#f97316' };
  const tierKeys = Object.keys(tierMix);
  mkChart('chartBuffetCostMix', {
    type: 'doughnut',
    data: {
      labels: tierKeys,
      datasets: [{
        data: tierKeys.map(k => tierMix[k]),
        backgroundColor: tierKeys.map(k => TIER_COLORS[k] || '#94a3b8'),
        borderWidth: 2, borderColor: '#fff',
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      cutout: '60%',
      plugins: {
        legend: { display: true, position: 'bottom', labels: { font: { size: 10 }, boxWidth: 10 } },
        datalabels: { display: false },
        tooltip: { callbacks: {
          label: ctx => {
            const tot = tierKeys.reduce((s,k) => s + (tierMix[k]||0), 0);
            const pct = tot ? (ctx.raw / tot * 100).toFixed(1) : 0;
            return ` ${ctx.label}: ${fmt(ctx.raw)} (${pct}%)`;
          }
        } },
      },
    },
  });

  // Detail table
  const colorMargin = v => v >= 40 ? 'text-emerald-600 font-semibold' : v >= 25 ? 'text-amber-600' : 'text-red-600 font-semibold';
  document.getElementById('bpbTableBody').innerHTML = rows.map(r => `
    <tr class="border-t border-slate-100 hover:bg-pink-50/40">
      <td class="py-1.5 pr-2 font-medium">${r.period}</td>
      <td class="py-1.5 pr-2 text-right">${fmtN(r.bills)}</td>
      <td class="py-1.5 pr-2 text-right">${fmtN(r.pax)}</td>
      <td class="py-1.5 pr-2 text-right">${fmt(r.revenue_ex)}</td>
      <td class="py-1.5 pr-2 text-right">${fmt(r.cost)}</td>
      <td class="py-1.5 pr-2 text-right font-semibold ${r.profit >= 0 ? 'text-emerald-700' : 'text-red-700'}">${fmt(r.profit)}</td>
      <td class="py-1.5 pr-2 text-right ${colorMargin(r.margin_pct)}">${r.margin_pct.toFixed(1)}%</td>
      <td class="py-1.5 pr-2 text-right">${fmt(r.avg_per_bill_rev)}</td>
      <td class="py-1.5 pr-2 text-right">${fmt(r.avg_per_bill_cost)}</td>
      <td class="py-1.5 pr-2 text-right font-semibold">${fmt(r.avg_per_bill_profit)}</td>
      <td class="py-1.5 text-right text-[11px] text-slate-500">${r.dominant_tier || '—'}</td>
    </tr>
  `).join('');

  // Insight summary
  const insightEl = document.getElementById('bpbInsight');
  if (rows.length >= 2 && insightEl) {
    const last  = rows[rows.length - 1];
    const prev  = rows[rows.length - 2];
    const dM    = last.margin_pct - prev.margin_pct;
    const dRev  = last.avg_per_bill_rev - prev.avg_per_bill_rev;
    const arrow = dM >= 0 ? '▲' : '▼';
    const trend = dM >= 0 ? 'text-emerald-600' : 'text-red-600';
    insightEl.innerHTML = `<b>เทียบ ${last.period} กับ ${prev.period}:</b>
      Margin <span class="${trend}">${arrow} ${Math.abs(dM).toFixed(1)} percentage points</span>
      · รายได้เฉลี่ย/บิล ${dRev >= 0 ? '▲' : '▼'} ${fmt(Math.abs(dRev))}
      · ช่วงใช้จ่าย/หัวที่พบบ่อยสุด <b>${last.dominant_tier || '—'}</b> บาท`;
  } else if (insightEl) {
    insightEl.innerHTML = '';
  }
}

async function loadProfitAnalysis() {
  const params = new URLSearchParams();
  if (getBranch())    params.set('branch',     getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to',   getMonthTo());

  // Kick off buffet per-bill in parallel; no need to await it
  loadBuffetPerBill();

  // Fetch endpoints in parallel (Top/Bottom-SKU removed 2026-06-01)
  const [summary, trend, byBranch, dist] = await Promise.all([
    api(`${API}/api/profit/summary?${params}`),
    api(`${API}/api/profit/trend?${params}`),
    api(`${API}/api/profit/by-branch?${params}`),
    api(`${API}/api/profit/per-bill-distribution?${params}`),
  ]);

  // KPI cards
  document.getElementById('pftRev').textContent     = fmtN(summary.revenue_ex_vat || 0);
  document.getElementById('pftCogs').textContent    = fmtN(summary.cogs || 0);
  document.getElementById('pftGp').textContent      = fmtN(summary.gross_profit || 0);
  document.getElementById('pftMargin').textContent  = summary.margin_pct != null ? summary.margin_pct.toFixed(1) + '%' : '—';
  document.getElementById('pftPerBill').textContent = fmtN(summary.profit_per_bill || 0);

  // Cost-coverage banner
  const cov = summary.cost_coverage_pct || 0;
  const banner = document.getElementById('profitCoverageBanner');
  const msg    = document.getElementById('profitCoverageMsg');
  if (cov < 90) {
    banner.classList.remove('hidden');
    if (cov === 0) {
      msg.innerHTML = '<b>No SKU costs uploaded yet.</b> Profit metrics show 0 until you upload cost data via <b>Settings → Costs</b>. Upload a CSV with sku_code, unit_cost columns to activate this dashboard.';
    } else {
      msg.innerHTML = `<b>ต้นทุนครอบคลุม ${cov.toFixed(0)}%</b> ของรายได้ · เพิ่มต้นทุนได้ที่ <b>ตั้งค่า → ต้นทุน</b> ${uiQ('profit:coverage', 'ต้นทุนครอบคลุม', '<p>บางเมนูยังไม่มีต้นทุน ตัวเลขกำไรนับเฉพาะเมนูที่มีต้นทุน</p>')}`;
    }
  } else {
    banner.classList.add('hidden');
  }

  // Trend chart: Revenue, COGS, GP, Margin %
  mkChart('chartProfitTrend', {
    type: 'bar',
    data: {
      labels: trend.map(t => fmtPeriod(t.period_month)),
      datasets: [
        { type:'bar',  label:'ต้นทุนขาย',         data: trend.map(t=>t.cogs),                  backgroundColor:'rgba(239,68,68,0.7)',  stack:'stack1', order:2 },
        { type:'bar',  label:'กำไรขั้นต้น', data: trend.map(t=>t.gross_profit),          backgroundColor:'rgba(16,185,129,0.7)', stack:'stack1', order:2 },
        { type:'line', label:'อัตรากำไร %',     data: trend.map(t=>t.margin_pct),            yAxisID:'y1', borderColor:'#06b6d4', backgroundColor:'#06b6d4', tension:0.25, order:1, fill:false },
      ],
    },
    options: {
      responsive:true, maintainAspectRatio:false,
      plugins: { legend:{position:'bottom'}, datalabels:{display:false},
        tooltip:{callbacks:{label:ctx=>ctx.dataset.label+': '+(ctx.dataset.label==='Margin %'?(ctx.raw??0).toFixed(1)+'%':fmtN(ctx.raw||0))}} },
      scales: {
        x: { stacked:true, ticks:{color:chartTheme().text} },
        y: { stacked:true, ticks:{color:chartTheme().text, callback:v=>fmtN(v)} },
        y1:{ position:'right', grid:{display:false}, ticks:{color:'#06b6d4', callback:v=>v+'%'}, max:100, min:0 },
      },
    },
  });

  // Per-bill distribution
  mkChart('chartProfitDist', {
    type: 'bar',
    data: {
      labels: dist.buckets.map(b=>b.label),
      datasets: [{ label:'บิล', data: dist.buckets.map(b=>b.count), backgroundColor:'#7a66b8' }],
    },
    options: {
      responsive:true, maintainAspectRatio:false,
      plugins: { legend:{display:false}, datalabels:{display:false},
        tooltip:{callbacks:{label:ctx=>fmtN(ctx.raw)+' bills'}} },
      scales: { x:{ticks:{color:chartTheme().text}}, y:{beginAtZero:true,ticks:{color:chartTheme().text}} },
    },
  });
  document.getElementById('pftDistNote').textContent = dist.note
    ? `⚠ ${dist.note}`
    : `Range: ${fmtN(dist.min_profit)} → ${fmtN(dist.max_profit)} · Avg: ${fmtN(dist.avg_profit)} · Margin used: ${dist.margin_pct_used}% · ${fmtN(dist.total_tx)} bills`;

  // By-branch bar chart
  mkChart('chartProfitBranch', {
    type: 'bar',
    data: {
      labels: byBranch.map(b=>b.branch),
      datasets: [
        { label:'รายได้ (ไม่รวม VAT)', data: byBranch.map(b=>b.revenue_ex_vat||0), backgroundColor:'rgba(31,58,107,0.6)' },
        { label:'ต้นทุนขาย',             data: byBranch.map(b=>b.cogs||0),           backgroundColor:'rgba(239,68,68,0.6)' },
        { label:'กำไรขั้นต้น',     data: byBranch.map(b=>b.gross_profit||0),   backgroundColor:'rgba(16,185,129,0.6)' },
      ],
    },
    options: {
      responsive:true, maintainAspectRatio:false,
      plugins: { legend:{position:'bottom'}, datalabels:{display:false},
        tooltip:{callbacks:{label:ctx=>ctx.dataset.label+': '+fmtN(ctx.raw||0)}} },
      scales: { x:{ticks:{color:chartTheme().text}}, y:{ticks:{color:chartTheme().text, callback:v=>fmtN(v)}} },
    },
  });

  // Top/Bottom 15 SKU tables removed 2026-06-01 (per-SKU profit unreliable for buffet).
}
